export type VisionManagedModel = {
  id: string;
  name: string;
  repoId: string;
  revision: string;
  path: string;
  capabilities: string[];
  supported: boolean;
  probeStatus: string;
  probeMessage: string;
  availableOnYouyun: boolean;
  remotePath?: string;
  sizeGb?: number;
  recommendedVramGb?: number;
  template?: string;
  imageId?: string;
  imageName?: string;
  imageStatus?: string;
  source?: string;
  youyunCatalog?: boolean;
  archived?: boolean;
  createdAt?: string;
  updatedAt?: string;
};

export type VisionReadinessState = "ready" | "pending" | "blocked";

export type VisionReadinessItem = {
  state: VisionReadinessState;
  reason: string;
};

export type VisionReadiness = {
  localTraining: VisionReadinessItem;
  youyunTraining: VisionReadinessItem;
  inference: VisionReadinessItem;
};

const blockedProbeStatuses = new Set(["missing", "failed", "unsupported"]);

function blocked(reason: string): VisionReadinessItem {
  return { state: "blocked", reason };
}

function pending(reason: string): VisionReadinessItem {
  return { state: "pending", reason };
}

function ready(reason: string): VisionReadinessItem {
  return { state: "ready", reason };
}

function probeIsBlocked(model: VisionManagedModel) {
  return blockedProbeStatuses.has(model.probeStatus)
    || !["candidate", "passed"].includes(model.probeStatus);
}

export function visionModelMetadata(model: VisionManagedModel) {
  const identity = `${model.repoId} ${model.name}`.toLowerCase();
  const family = identity.includes("qwen2.5-vl")
    ? "Qwen2.5-VL"
    : identity.includes("qwen3-omni")
      ? "Qwen3-Omni"
    : identity.includes("qwen3-vl")
      ? "Qwen3-VL"
      : identity.includes("qwen2-vl")
        ? "Qwen2-VL"
        : "自定义";
  const scaleMatch = `${model.repoId} ${model.name}`.match(/(?:^|[-_/\s])(\d+(?:\.\d+)?B)(?:[-_/\s]|$)/i);
  return {
    family,
    parameterScale: scaleMatch?.[1]?.toUpperCase() ?? "其他",
  };
}

export function visionModelReadiness(model: VisionManagedModel): VisionReadiness {
  const capabilities = new Set(model.capabilities);
  const visual = capabilities.has("vision_language");
  const trains = capabilities.has("lora_training") || capabilities.has("qlora_training");
  const serves = capabilities.has("transformers_serving");
  const localProbePassed = model.supported
    && model.probeStatus === "passed"
    && Boolean(model.path.trim());
  const probeBlocked = probeIsBlocked(model);

  let localTraining: VisionReadinessItem;
  if (!visual || !trains) {
    localTraining = blocked("模型缺少视觉语言或 LoRA/QLoRA 训练能力");
  } else if (localProbePassed) {
    localTraining = ready("本地目录和训练能力已通过检查");
  } else if (probeBlocked) {
    localTraining = blocked(model.probeMessage || "本地模型探针未通过");
  } else {
    localTraining = pending("需要配置服务器可访问的本地模型目录");
  }

  let youyunTraining: VisionReadinessItem;
  if (!visual || !trains) {
    youyunTraining = blocked("模型缺少视觉语言或 LoRA/QLoRA 训练能力");
  } else if (probeBlocked) {
    youyunTraining = blocked(model.probeMessage || "模型探针状态不支持优云训练");
  } else if (model.availableOnYouyun) {
    youyunTraining = ready("Repo ID 已配置到优云可用模型目录");
  } else {
    youyunTraining = pending("Repo ID 尚未配置到优云可用模型目录");
  }

  let inference: VisionReadinessItem;
  if (!visual || !serves) {
    inference = blocked("模型缺少视觉语言或推理服务能力");
  } else if (localProbePassed) {
    inference = ready("本地目录和推理能力已通过检查");
  } else if (probeBlocked) {
    inference = blocked(model.probeMessage || "本地模型探针未通过");
  } else {
    inference = pending("需要配置服务器可访问的本地模型目录");
  }

  return { localTraining, youyunTraining, inference };
}

export function visionModelBucket(model: VisionManagedModel): "available" | "pending" | "error" {
  const readiness = Object.values(visionModelReadiness(model));
  if (readiness.some((item) => item.state === "ready")) return "available";
  if (readiness.some((item) => item.state === "blocked")) return "error";
  return "pending";
}

export function visionModelCounts(models: readonly VisionManagedModel[]) {
  return models.reduce(
    (counts, model) => {
      const readiness = visionModelReadiness(model);
      counts.total += 1;
      if (readiness.localTraining.state === "ready") counts.localReady += 1;
      if (readiness.youyunTraining.state === "ready") counts.youyunReady += 1;
      if (Object.values(readiness).some((item) => item.state !== "ready")) counts.attention += 1;
      return counts;
    },
    { total: 0, localReady: 0, youyunReady: 0, attention: 0 },
  );
}
