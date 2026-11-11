export type ModelModality = "nlp" | "vision" | "image-generation";
export type ModelAssetType = "base" | "adapter" | "merged";
export type ModelAssetStatus = "available" | "pending" | "unavailable" | "archived";
export type ModelReadinessState = "ready" | "pending" | "blocked";

export type ModelReadiness = {
  state: ModelReadinessState;
  reason: string;
};

export type ModelLineage = {
  baseModelId: string | null;
  baseModelName: string | null;
  taskId: string | null;
  dataset: string | null;
  method: string | null;
  variant: string | null;
};

export type ModelStorage = {
  state: "not-uploaded" | "uploading" | "available" | "failed";
  bytes: number | null;
  manifest: string | null;
  uri: string | null;
  error: string | null;
};

export type ModelAssetView = {
  id: string;
  modality: ModelModality;
  assetType: ModelAssetType;
  name: string;
  repoId: string | null;
  revision: string | null;
  localPath: string | null;
  source: "modelscope-cache" | "youyun-catalog" | "manual" | "training-output";
  status: ModelAssetStatus;
  archived: boolean;
  readiness: {
    localTraining: ModelReadiness;
    remoteTraining: ModelReadiness;
    inference: ModelReadiness;
  };
  lineage: ModelLineage | null;
  storage: ModelStorage;
  capabilities: string[];
  updatedAt: string | null;
  raw: unknown;
};

const ready = (reason: string): ModelReadiness => ({ state: "ready", reason });
const pending = (reason: string): ModelReadiness => ({ state: "pending", reason });
const blocked = (reason: string): ModelReadiness => ({ state: "blocked", reason });

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function bool(value: unknown): boolean {
  return value === true || value === 1 || value === "true";
}

function sourceFor(payload: Record<string, unknown>, catalog: boolean): ModelAssetView["source"] {
  if (catalog || payload.youyunCatalog === true) return "youyun-catalog";
  const source = text(payload.source)?.toLowerCase() ?? "";
  if (source.includes("modelscope") || source.includes("cache") || source.includes("仓库")) return "modelscope-cache";
  if (source.includes("training") || source.includes("训练") || payload.taskId) return "training-output";
  return "manual";
}

function storageFor(payload: Record<string, unknown>): ModelStorage {
  const storage = (payload.sharedStorage && typeof payload.sharedStorage === "object"
    ? payload.sharedStorage
    : {}) as Record<string, unknown>;
  const rawStatus = text(storage.status) ?? "not_uploaded";
  const state: ModelStorage["state"] = rawStatus === "available"
    ? "available"
    : rawStatus === "uploading" || rawStatus === "pending_upload"
      ? "uploading"
      : rawStatus === "failed" || rawStatus === "unavailable"
        ? "failed"
        : "not-uploaded";
  const bytes = typeof storage.sizeBytes === "number" ? storage.sizeBytes : null;
  return {
    state,
    bytes,
    manifest: text(storage.manifestMd5) ?? text(storage.manifest),
    uri: text(storage.uri),
    error: text(storage.error),
  };
}

function nlpReadiness(payload: Record<string, unknown>, assetType: ModelAssetType, archived: boolean): ModelAssetView["readiness"] {
  if (archived) {
    const reason = "模型已归档";
    return { localTraining: blocked(reason), remoteTraining: blocked(reason), inference: blocked(reason) };
  }
  const available = text(payload.status) === "available";
  const reason = available ? "模型路径已通过后端检查" : "模型路径尚未通过后端检查";
  const local = available ? ready(reason) : blocked(reason);
  const remote = assetType === "base" ? (available ? ready("基座模型可用于远程训练") : pending("需要准备远程可访问的基座模型")) : (available ? ready("训练产物可用于远程部署") : blocked(reason));
  const inference = available ? ready("模型文件可用于推理") : blocked(reason);
  return { localTraining: local, remoteTraining: remote, inference };
}

function visionReadiness(payload: Record<string, unknown>, archived: boolean): ModelAssetView["readiness"] {
  const supported = payload.supported === true;
  const probe = text(payload.probeStatus) ?? "";
  const path = text(payload.path);
  const remote = bool(payload.availableOnYouyun);
  const capabilities = Array.isArray(payload.capabilities) ? payload.capabilities.filter((item): item is string => typeof item === "string") : [];
  const visual = capabilities.includes("vision_language");
  const train = capabilities.includes("lora_training") || capabilities.includes("qlora_training");
  const serve = capabilities.includes("transformers_serving");
  if (archived) {
    const reason = "模型已归档";
    return { localTraining: blocked(reason), remoteTraining: blocked(reason), inference: blocked(reason) };
  }
  const localReady = supported && probe === "passed" && Boolean(path);
  const probeBlocked = ["missing", "failed", "unsupported"].includes(probe);
  const localTraining = !visual || !train ? blocked("模型缺少视觉语言或 LoRA/QLoRA 训练能力") : localReady ? ready("本地目录和训练能力已通过检查") : probeBlocked ? blocked(text(payload.probeMessage) ?? "本地模型探针未通过") : pending("需要配置服务器可访问的本地模型目录");
  const remoteTraining = !visual || !train ? blocked("模型缺少视觉语言或 LoRA/QLoRA 训练能力") : probeBlocked ? blocked(text(payload.probeMessage) ?? "模型探针未通过") : remote ? ready("Repo ID 已配置到优云可用模型目录") : pending("Repo ID 尚未配置到优云可用模型目录");
  const inference = !visual || !serve ? blocked("模型缺少视觉语言或推理服务能力") : localReady ? ready("本地目录和推理能力已通过检查") : probeBlocked ? blocked(text(payload.probeMessage) ?? "本地模型探针未通过") : pending("需要配置服务器可访问的本地模型目录");
  return { localTraining, remoteTraining, inference };
}

function imageReadiness(payload: Record<string, unknown>, archived: boolean): ModelAssetView["readiness"] {
  if (archived) {
    const reason = "模型已归档";
    return { localTraining: blocked(reason), remoteTraining: blocked(reason), inference: blocked(reason) };
  }
  const readyForYouyun = bool(payload.readyForYouyun);
  const modes = Array.isArray(payload.supportedTrainingModes) ? payload.supportedTrainingModes : [];
  const train = modes.length > 0;
  const reason = text(payload.reason) ?? "文生图模型服务端校验未完成";
  return {
    localTraining: train ? pending("本地 Diffusers 训练能力需在运行时确认") : blocked("模型未声明 LoRA/QLoRA 训练能力"),
    remoteTraining: readyForYouyun ? ready("文生图服务端校验通过") : pending(reason),
    inference: readyForYouyun ? ready("Diffusers 推理能力已通过服务端校验") : pending(reason),
  };
}

export function normalizeModelAsset(payload: unknown, modality: ModelModality): ModelAssetView {
  const value = (payload && typeof payload === "object" ? payload : {}) as Record<string, unknown>;
  const catalog = value.youyunCatalog === true;
  const kind = text(value.kind) ?? (text(value.group) === "trained" ? "trained" : "base");
  const assetType: ModelAssetType = kind === "trained" || value.adapterVariant ? "adapter" : kind === "merged" ? "merged" : "base";
  const archived = bool(value.archived);
  const capabilities = Array.isArray(value.capabilities)
    ? value.capabilities.filter((item): item is string => typeof item === "string")
    : [];
  const readiness = modality === "vision"
    ? visionReadiness(value, archived)
    : modality === "image-generation"
      ? imageReadiness(value, archived)
      : nlpReadiness(value, assetType, archived);
  const statusText = text(value.status);
  const status: ModelAssetStatus = archived
    ? "archived"
    : statusText === "available" || bool(value.readyForYouyun)
      ? "available"
      : statusText === "pending" || catalog
        ? "pending"
        : "unavailable";
  const lineage = assetType === "adapter" || value.taskId
    ? {
        baseModelId: text(value.baseModelId),
        baseModelName: text(value.baseModelName),
        taskId: text(value.taskId),
        dataset: text(value.dataset),
        method: text(value.method),
        variant: text(value.adapterVariant),
      }
    : null;
  return {
    id: text(value.id) ?? `${modality}-unknown`,
    modality,
    assetType,
    name: text(value.name) ?? text(value.label) ?? "未命名模型",
    repoId: text(value.repo) ?? text(value.repoId),
    revision: text(value.revision),
    localPath: text(value.path),
    source: sourceFor(value, catalog),
    status,
    archived,
    readiness,
    lineage,
    storage: storageFor(value),
    capabilities,
    updatedAt: text(value.updatedAt),
    raw: payload,
  };
}

export function modelAssetStatusLabel(status: ModelAssetStatus): string {
  return ({ available: "可用", pending: "待处理", unavailable: "不可用", archived: "已归档" })[status];
}
