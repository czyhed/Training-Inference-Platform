"use client";

import {
  Activity,
  Archive,
  ArrowLeft,
  Check,
  ChevronDown,
  CircleAlert,
  Copy,
  FileText,
  Gauge,
  Info,
  Play,
  RefreshCw,
  RotateCw,
  Search,
  Send,
  Server,
  Square,
  Trash2,
  Upload,
  Wrench,
  X,
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { apiUrl } from "../lib/api-config";
import { VisionServiceEvaluation } from "./vision-service-evaluation";
import { InferenceServiceDetailSummary } from "./inference-service-detail";
import { friendlyYouyunError, selectYouyunZone, youyunMachineIsUnavailable, youyunRegionLabel, youyunZoneModes, type YouyunZone } from "../lib/youyun-location";
import { YouyunZonePicker } from "./youyun-zone-picker";
import { YouyunInventoryIndicator } from "./youyun-inventory-indicator";
import { useCapabilities } from "../lib/use-capabilities";
import { EmptyState } from "./ui/empty-state";
import { PageHeader } from "./ui/page-header";

type ServiceState =
  | "created"
  | "starting"
  | "running"
  | "stopping"
  | "stopped"
  | "recovering"
  | "failed"
  | "cleanup_failed"
  | "archived";

type ExposureState = "test" | "online" | "offline";
type DetailTab = "overview" | "debug" | "evaluation" | "api" | "logs";
type View = "list" | "create" | "detail";

type VisionModelRecord = {
  id: string;
  name: string;
  repoId: string;
  revision?: string;
  path: string;
  capabilities?: string[];
  supported: boolean;
  probeStatus: string;
  probeMessage?: string;
  availableOnYouyun?: boolean;
};

type VisionInferenceModelOption = {
  id: string;
  label: string;
  group: "base" | "trained";
  modelId: string;
  repoId: string;
  modelPath: string;
  taskId?: string;
  adapterVariant?: "best" | "final";
  adapterPath?: string;
  supported: boolean;
  probeStatus: string;
  availableOnYouyun?: boolean;
  readyForLocal: boolean;
  readyForYouyun: boolean;
  ready: boolean;
  reason?: string | null;
};

export type VisionInferenceServiceRecord = {
  id: string;
  name: string;
  state: string;
  model: string;
  adapter?: string;
  gpu: string;
  memory: string;
  description?: string;
  taskId?: string;
  owner?: string;
  exposure?: string;
  endpoint?: string;
  error?: string | null;
  failureCode?: string | null;
  failureMessage?: string | null;
  suggestedAction?: string | null;
  resourceState?: string | null;
  cleanupState?: string | null;
  cleanupError?: string | null;
  cleanupCheckedAt?: string | null;
  remoteHandle?: { instanceId?: string; state?: string } | null;
  createdAt?: string;
  updatedAt?: string;
  recoveryAttempts?: number;
  config?: Record<string, unknown>;
  hasApiKey?: boolean;
  apiKeyCreatedAt?: string | null;
  startup?: {
    stage?: string;
    message?: string;
    progress?: number;
    startedAt?: string;
    elapsedSeconds?: number;
  } | null;
};

type CreateForm = {
  name: string;
  description: string;
  modelId: string;
  maxModelLength: string;
  maxTokens: string;
  temperature: string;
  topP: string;
  dtype: "auto" | "float16" | "bfloat16";
  gpuMemoryUtilization: string;
  maxNumSeqs: string;
  maxNumBatchedTokens: string;
  enablePrefixCaching: boolean;
  enableChunkedPrefill: boolean;
  tensorParallelSize: string;
  loadTimeout: string;
  provider: "local" | "youyun";
  youyunGpuType: string;
  youyunCpu: string;
  youyunMemory: string;
};

type YouyunSpecs = {
  instanceMode?: "pod" | "uhost" | "auto";
  fallback?: boolean;
  fallbackEligible?: boolean;
  fallbackReason?: string;
  region: string;
  zone: string;
  gpuType: string;
  chargeType: string;
  imageId: string;
  recommendedInferenceImageId?: string;
  recommendedInferenceImageName?: string;
  machineTypes: Array<{
    name: string;
    zone: string;
    soldOut: boolean;
    description: string;
    graphicsMemoryGb: number | null;
    inventoryStatus?: "available" | "sold_out" | "unknown" | "error";
    inventoryMessage?: string;
  }>;
  capacity: Array<{
    gpu: number;
    cpu: number;
    memoryGb: number;
    available: boolean;
  }>;
  zones?: YouyunZone[];
};
// zones: Array<{ region, zone, is_pod }> is used to constrain GPU capacity queries.
// className="youyun-region-list" selectYouyunZone instanceMode: "pod" region: selectedYouyunRegion zone: selectedYouyunZone

type YouyunResource = {
  mode: "manual" | "auto";
  instanceMode?: "pod" | "uhost" | "auto";
  region: string;
  zone: string;
  gpuType: string;
  gpuCount: number;
  cpuCores: number;
  memoryGb: number;
  chargeType: string;
  imageId: string;
  price: {
    currency: string;
    instance: number;
    disks: number;
    systemDisks: number;
    image: number;
    total: number;
  };
};

type Metrics = Record<string, unknown> & {
  source?: string;
  updatedAt?: string | null;
  state?: string;
  runningRequests?: number;
  waitingRequests?: number;
  batchSize?: number;
  ttftMs?: number | null;
  decodeTokensPerSecond?: number | null;
  kvCacheUsagePercent?: number | null;
};

type ConfirmAction = { type: "archive" | "delete"; serviceId: string } | null;

const stateMeta: Record<ServiceState, { label: string; hint: string; className: string }> = {
  created: { label: "待启动", hint: "配置保留，尚未占用 GPU", className: "inference-created" },
  starting: { label: "启动中", hint: "正在加载视觉模型并执行健康检查", className: "inference-starting" },
  running: { label: "运行中", hint: "服务已通过健康检查", className: "inference-running" },
  stopping: { label: "停止中", hint: "正在释放运行资源", className: "inference-stopping" },
  stopped: { label: "已停止", hint: "配置保留，GPU 已释放", className: "inference-stopped" },
  recovering: { label: "恢复中", hint: "正在执行一次自动恢复", className: "inference-recovering" },
  failed: { label: "启动失败", hint: "请查看错误信息或运行日志", className: "inference-failed" },
  cleanup_failed: { label: "清理失败", hint: "优云实例尚未确认释放，请重试清理", className: "inference-failed" },
  archived: { label: "已归档", hint: "配置保留，不参与默认列表", className: "inference-archived" },
};

const exposureMeta: Record<ExposureState, { label: string; className: string }> = {
  test: { label: "测试中", className: "exposure-test" },
  online: { label: "已上线", className: "exposure-online" },
  offline: { label: "已下线", className: "exposure-offline" },
};

const activeStates: ServiceState[] = ["starting", "running", "stopping", "recovering"];

async function requestJson<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), {
    ...options,
    credentials: "include",
    cache: "no-store",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail;
    const message = typeof detail === "string"
      ? detail
      : detail?.message ?? (detail ? JSON.stringify(detail) : `请求失败（${response.status}）`);
    throw new ApiRequestError(response.status, message);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

class ApiRequestError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
  }
}

function asState(value: string): ServiceState {
  return value in stateMeta ? value as ServiceState : "created";
}

function formatTime(value?: string | null) {
  if (!value) return "—";
  return value.replace("T", " ").replace(/\.\d+(Z|[+-]\d\d:\d\d)?$/, "");
}

function displayValue(value: unknown, fallback = "—") {
  if (value === undefined || value === null || value === "") return fallback;
  return String(value);
}

function configValue(service: VisionInferenceServiceRecord, key: string, fallback?: unknown) {
  return service.config?.[key] ?? fallback;
}

function statusBadge(service: VisionInferenceServiceRecord) {
  const state = asState(service.state);
  const meta = stateMeta[state];
  return <span className={`status inference-status ${meta.className}`}><i />{meta.label}</span>;
}

function exposureBadge(service: VisionInferenceServiceRecord) {
  const state = asState(service.state);
  const exposure = (service.exposure ?? (activeStates.includes(state) ? "test" : "offline")) as ExposureState;
  const meta = exposureMeta[exposure] ?? exposureMeta.offline;
  return <span className={`inference-exposure ${meta.className}`}>{meta.label}</span>;
}

function modelForService(service: VisionInferenceServiceRecord, models: VisionModelRecord[]) {
  const modelId = String(configValue(service, "modelId", ""));
  return models.find((model) => model.id === modelId)
    ?? models.find((model) => model.path && service.model && model.path === service.model)
    ?? null;
}

function modelLabel(service: VisionInferenceServiceRecord, models: VisionModelRecord[]) {
  return modelForService(service, models)?.repoId || service.model || "—";
}

function modelPath(service: VisionInferenceServiceRecord, models: VisionModelRecord[]) {
  return modelForService(service, models)?.path || service.model || "未记录本地目录";
}

function resourceStateLabel(value: string | null | undefined) {
  return ({ none: "未占用", provisioning: "准备中", running: "占用中", stopping: "释放中", released: "已释放", cleanup_failed: "清理失败" } as Record<string, string>)[value ?? ""] ?? value ?? "未记录";
}

function cleanupStateLabel(value: string | null | undefined) {
  return ({ pending: "待核验", released: "已核验释放", failed: "核验失败" } as Record<string, string>)[value ?? ""] ?? value ?? "未记录";
}

function initialCreateForm(models: VisionModelRecord[]): CreateForm {
  const first = models.find((model) => model.supported && model.probeStatus === "passed" && model.path.trim());
  const preferredProvider = first ? "local" : "youyun";
  return {
    name: "视觉理解推理服务",
    description: "视觉理解图生文推理服务",
    modelId: first?.id ?? "",
    maxModelLength: "4096",
    maxTokens: "512",
    temperature: "0.2",
    topP: "0.9",
    dtype: "auto",
    gpuMemoryUtilization: "0.75",
    maxNumSeqs: "1",
    maxNumBatchedTokens: "2048",
    enablePrefixCaching: true,
    enableChunkedPrefill: true,
    tensorParallelSize: "1",
    loadTimeout: "900",
    provider: preferredProvider,
    youyunGpuType: "4090",
    youyunCpu: "8",
    youyunMemory: "32",
  };
}

function baseInferenceOptions(models: VisionModelRecord[]): VisionInferenceModelOption[] {
  return models.map((model) => ({
    id: model.id,
    label: model.name,
    group: "base",
    modelId: model.id,
    repoId: model.repoId,
    modelPath: model.path,
    supported: model.supported,
    probeStatus: model.probeStatus,
    availableOnYouyun: model.availableOnYouyun,
    readyForLocal: Boolean(model.supported && model.probeStatus === "passed" && model.path.trim()),
    readyForYouyun: Boolean(model.availableOnYouyun),
    ready: Boolean(model.supported && model.probeStatus === "passed" && model.path.trim()),
    reason: model.probeMessage,
  }));
}

function preferredInferenceProvider(models: VisionModelRecord[]): "local" | "youyun" {
  return models.some((model) => model.supported && model.probeStatus === "passed" && model.path.trim())
    ? "local"
    : "youyun";
}

export function VisionInferenceServiceModule({
  models,
  services,
  loading,
  externalError,
  externalNotice,
  onRefresh,
  onOpenInference,
  onOpenEvaluation,
}: {
  models: VisionModelRecord[];
  services: VisionInferenceServiceRecord[];
  loading: boolean;
  externalError?: string;
  externalNotice?: string;
  onRefresh: (quiet?: boolean) => Promise<void>;
  onOpenInference: (serviceId: string) => void;
  onOpenEvaluation?: (serviceId: string) => void;
}) {
  const [view, setView] = useState<View>("list");
  const [selectedId, setSelectedId] = useState("");
  const [detailTab, setDetailTab] = useState<DetailTab>("overview");
  const { evaluationEnabled } = useCapabilities();
  const [query, setQuery] = useState("");
  const [stateFilter, setStateFilter] = useState<"all" | ServiceState>("all");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [actionBusy, setActionBusy] = useState("");
  const [confirmAction, setConfirmAction] = useState<ConfirmAction>(null);
  const [createForm, setCreateForm] = useState<CreateForm>(() => initialCreateForm(models));
  const [creating, setCreating] = useState(false);
  const [providerModels, setProviderModels] = useState<VisionInferenceModelOption[]>(baseInferenceOptions(models));
  const [providerModelsLoading, setProviderModelsLoading] = useState(false);
  const [providerModelsError, setProviderModelsError] = useState("");
  const [youyunSpecs, setYouyunSpecs] = useState<YouyunSpecs | null>(null);
  const [youyunLoading, setYouyunLoading] = useState(false);
  const [youyunError, setYouyunError] = useState("");
  const [youyunAuthRequired, setYouyunAuthRequired] = useState(false);
  const [youyunResource, setYouyunResource] = useState<YouyunResource | null>(null);
  const [selectedYouyunRegion, setSelectedYouyunRegion] = useState("");
  const [selectedYouyunZone, setSelectedYouyunZone] = useState("");
  const [youyunPriceLoading, setYouyunPriceLoading] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const [logsLoading, setLogsLoading] = useState(false);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [metricsLoading, setMetricsLoading] = useState(false);
  const [debugImage, setDebugImage] = useState<File | null>(null);
  const [debugPreview, setDebugPreview] = useState("");
  const [debugImageError, setDebugImageError] = useState("");
  const [debugQuestion, setDebugQuestion] = useState("请描述图片中的主要内容，并回答可见文字或关键信息。");
  const [debugAnswer, setDebugAnswer] = useState("");
  const [debugSending, setDebugSending] = useState(false);
  const [apiKeyStatus, setApiKeyStatus] = useState<{ hasApiKey: boolean; createdAt?: string | null } | null>(null);
  const [issuedApiKey, setIssuedApiKey] = useState("");
  const [apiKeyBusy, setApiKeyBusy] = useState(false);

  const normalizedServices = useMemo(() => services.map((service) => ({
    ...service,
    description: service.description ?? "",
    adapter: service.adapter ?? "",
    endpoint: service.endpoint ?? "/v1/chat/completions",
  })), [services]);

  const providerModelOptions = providerModels;
  const deployableModels = useMemo(
    () => providerModelOptions.filter((model) => createForm.provider === "youyun"
      ? model.readyForYouyun
      : model.readyForLocal),
    [createForm.provider, providerModelOptions],
  );

  const selectedService = useMemo(
    () => normalizedServices.find((service) => service.id === selectedId) ?? null,
    [normalizedServices, selectedId],
  );

  const filteredServices = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return normalizedServices.filter((service) => {
      const state = asState(service.state);
      const matchesState = stateFilter === "all"
        ? state !== "archived"
        : state === stateFilter;
      if (!matchesState) return false;
      if (!normalizedQuery) return true;
      return [
        service.name,
        service.id,
        service.description,
        service.taskId,
        service.model,
        modelLabel(service, models),
      ].filter(Boolean).join(" ").toLowerCase().includes(normalizedQuery);
    });
  }, [models, normalizedServices, query, stateFilter]);

  useEffect(() => {
    if (selectedId && normalizedServices.some((service) => service.id === selectedId)) return;
    setSelectedId(normalizedServices.find((service) => asState(service.state) !== "archived")?.id ?? normalizedServices[0]?.id ?? "");
  }, [normalizedServices, selectedId]);

  useEffect(() => {
    if (!createForm.modelId && deployableModels[0]) {
      setCreateForm((current) => ({ ...current, modelId: deployableModels[0].id }));
    }
  }, [createForm.modelId, deployableModels]);

  async function refreshProviderModels(provider: "local" | "youyun") {
    setProviderModelsLoading(true);
    setProviderModelsError("");
    try {
      const query = new URLSearchParams({ provider });
      const nextModels = await requestJson<VisionInferenceModelOption[]>(`/vision/inference-models?${query.toString()}`);
      setProviderModels(nextModels);
      setCreateForm((current) => ({
        ...current,
        modelId: nextModels.some((model) => model.id === current.modelId && (provider === "youyun" ? model.readyForYouyun : model.readyForLocal))
          ? current.modelId
          : nextModels.find((model) => provider === "youyun" ? model.readyForYouyun : model.readyForLocal)?.id ?? "",
      }));
    } catch (reason) {
      setProviderModelsError(reason instanceof Error ? reason.message : "视觉基模列表读取失败。");
      if (provider === "local") setProviderModels(baseInferenceOptions(models));
    } finally {
      setProviderModelsLoading(false);
    }
  }

  async function refreshYouyunSpecs(
    gpuType = createForm.youyunGpuType,
    regionOverride = selectedYouyunRegion,
    zoneOverride = selectedYouyunZone,
  ) {
    setYouyunLoading(true);
    setYouyunError("");
    setYouyunAuthRequired(false);
    try {
      const params = new URLSearchParams({ purpose: "inference", instanceMode: "auto" });
      if (regionOverride) params.set("region", regionOverride);
      if (zoneOverride) params.set("zone", zoneOverride);
      if (gpuType) params.set("gpuType", gpuType);
      const query = `?${params.toString()}`;
      const specs = await requestJson<YouyunSpecs>(`/youyun/gpu-specs${query}`);
      setYouyunSpecs(specs);
      setSelectedYouyunRegion(specs.region);
      const healthyZones = (specs.zones ?? []).filter((zone) => zone.healthStatus !== "cooldown");
      setSelectedYouyunZone(specs.zone || selectYouyunZone(healthyZones, specs.region, specs.instanceMode === "uhost" ? "uhost" : "pod"));
      const available = specs.capacity.filter((item) => item.gpu === 1 && item.available);
      const currentValid = available.some(
        (item) => item.cpu === Number(createForm.youyunCpu) && item.memoryGb === Number(createForm.youyunMemory),
      );
      const first = available[0];
      setCreateForm((current) => ({
        ...current,
        youyunGpuType: specs.gpuType,
        youyunCpu: currentValid ? current.youyunCpu : first ? String(first.cpu) : "",
        youyunMemory: currentValid ? current.youyunMemory : first ? String(first.memoryGb) : "",
      }));
    } catch (reason) {
      setYouyunSpecs(null);
      setYouyunResource(null);
      setYouyunError(friendlyYouyunError(reason instanceof Error ? reason.message : "优云 GPU 库存查询失败。"));
      setYouyunAuthRequired(reason instanceof ApiRequestError && reason.status === 401);
    } finally {
      setYouyunLoading(false);
    }
  }

  useEffect(() => {
    if (view !== "create") return;
    void refreshProviderModels(createForm.provider);
    if (createForm.provider === "youyun") void refreshYouyunSpecs();
    else {
      setYouyunSpecs(null);
      setYouyunResource(null);
      setYouyunError("");
      setYouyunAuthRequired(false);
    }
    // The create view loads the catalog once per provider selection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, createForm.provider]);

  useEffect(() => {
    if (createForm.provider !== "youyun" || !youyunSpecs || !createForm.youyunCpu || !createForm.youyunMemory) {
      setYouyunResource(null);
      return;
    }
    let cancelled = false;
    setYouyunPriceLoading(true);
    void requestJson<YouyunResource>("/youyun/price", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        mode: "manual",
        gpuType: createForm.youyunGpuType,
        gpuCount: 1,
        cpuCores: Number(createForm.youyunCpu),
        memoryGb: Number(createForm.youyunMemory),
        imageId: youyunSpecs.recommendedInferenceImageId || youyunSpecs.imageId,
        instanceMode: "auto",
        region: selectedYouyunRegion || youyunSpecs.region,
        zone: selectedYouyunZone || youyunSpecs.zone,
      }),
    })
      .then((resource) => {
        if (!cancelled) setYouyunResource(resource);
      })
      .catch((reason) => {
        if (!cancelled) {
          setYouyunResource(null);
          setYouyunError(reason instanceof Error ? reason.message : "优云价格查询失败。");
        }
      })
      .finally(() => {
        if (!cancelled) setYouyunPriceLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [createForm.provider, createForm.youyunCpu, createForm.youyunGpuType, createForm.youyunMemory, selectedYouyunRegion, selectedYouyunZone, youyunSpecs]);

  useEffect(() => {
    return () => {
      if (debugPreview) URL.revokeObjectURL(debugPreview);
    };
  }, [debugPreview]);

  useEffect(() => {
    if (view !== "detail" || !selectedService) return;
    const service = selectedService;
    let cancelled = false;
    async function loadDetails() {
      if (detailTab === "logs") setLogsLoading(true);
      if (detailTab === "overview") setMetricsLoading(true);
      try {
        const requests: Promise<unknown>[] = [];
        if (detailTab === "logs") {
          requests.push(requestJson<{ logs?: string[] }>(`/inference/services/${service.id}/logs?limit=200`));
        }
        if (detailTab === "overview") {
          requests.push(requestJson<Metrics>(`/inference/services/${service.id}/metrics`));
        }
        if (detailTab === "api") {
          requests.push(requestJson<{ hasApiKey: boolean; createdAt?: string | null }>(`/inference/services/${service.id}/api-key`));
        }
        const results = await Promise.all(requests);
        if (cancelled) return;
        if (detailTab === "logs") setLogs((results[0] as { logs?: string[] }).logs ?? []);
        if (detailTab === "overview") setMetrics(results[0] as Metrics);
        if (detailTab === "api") setApiKeyStatus(results[0] as { hasApiKey: boolean; createdAt?: string | null });
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "读取服务详情失败。");
      } finally {
        if (!cancelled) {
          setLogsLoading(false);
          setMetricsLoading(false);
        }
      }
    }
    void loadDetails();
    const shouldPoll = activeStates.includes(asState(service.state)) || detailTab === "logs";
    if (!shouldPoll) return () => { cancelled = true; };
    const timer = window.setInterval(() => {
      void onRefresh(true);
      void loadDetails();
    }, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [detailTab, onRefresh, selectedService, view]);

  function openDetail(id: string, nextTab: DetailTab = "overview") {
    setSelectedId(id);
    setDetailTab(nextTab);
    setView("detail");
    setError("");
    setNotice("");
    setLogs([]);
    setMetrics(null);
  }

  function openCreate() {
    const defaultProvider = preferredInferenceProvider(models);
    setCreateForm((current) => ({
      ...initialCreateForm(models),
      ...current,
      provider: current.provider === "local" && defaultProvider === "youyun"
        ? defaultProvider
        : current.provider,
      modelId: current.modelId && deployableModels.some((model) => model.id === current.modelId)
        ? current.modelId
        : deployableModels[0]?.id ?? "",
    }));
    setView("create");
    setError("");
    setNotice("");
  }

  async function submitCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const selectedModel = providerModelOptions.find((model) => model.id === createForm.modelId);
    const selectedModelReady = selectedModel && (createForm.provider === "youyun" ? selectedModel.readyForYouyun : selectedModel.readyForLocal);
    if (!selectedModel || !selectedModelReady) {
      setError(createForm.provider === "youyun"
        ? "请选择可用于优云推理的基座模型或训练完成模型。"
        : "请选择已通过探针且存在本地目录的基座模型或训练完成模型。");
      return;
    }
    if (createForm.provider === "youyun" && (!youyunSpecs || !youyunResource)) {
      setError(youyunPriceLoading ? "正在查询优云规格价格，请稍后再提交。" : "请先选择有库存的优云 GPU 规格。");
      return;
    }
    setCreating(true);
    setError("");
    setNotice("");
    try {
      const service = await requestJson<VisionInferenceServiceRecord & { message?: string }>("/vision/deployments", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
           name: createForm.name.trim(),
           description: createForm.description.trim(),
           modelType: selectedModel.group,
           modelId: selectedModel.modelId,
           taskId: selectedModel.taskId ?? null,
           adapterVariant: selectedModel.adapterVariant ?? null,
          owner: "平台管理员",
          provider: createForm.provider,
          youyunResource: createForm.provider === "youyun" && youyunResource
              ? {
                mode: "manual",
                instanceMode: "auto",
                fallbackEligible: true,
                region: selectedYouyunRegion || youyunResource.region,
                zone: selectedYouyunZone || youyunResource.zone,
                imageId: youyunResource.imageId,
                gpuType: youyunResource.gpuType,
                gpuCount: 1,
                cpuCores: youyunResource.cpuCores,
                memoryGb: youyunResource.memoryGb,
              }
            : null,
          maxModelLength: Number(createForm.maxModelLength),
          maxTokens: Number(createForm.maxTokens),
          temperature: Number(createForm.temperature),
          topP: Number(createForm.topP),
          dtype: createForm.dtype,
          gpuMemoryUtilization: Number(createForm.gpuMemoryUtilization),
          maxNumSeqs: Number(createForm.maxNumSeqs),
          maxNumBatchedTokens: Number(createForm.maxNumBatchedTokens),
          enablePrefixCaching: createForm.enablePrefixCaching,
          enableChunkedPrefill: createForm.enableChunkedPrefill,
          tensorParallelSize: Number(createForm.tensorParallelSize),
          loadTimeout: Number(createForm.loadTimeout),
        }),
      });
      await onRefresh(true);
      setSelectedId(service.id);
      setView("detail");
      setNotice(service.message ?? "视觉推理服务配置已创建，启动并通过健康检查后才会运行。");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建视觉推理服务失败。");
    } finally {
      setCreating(false);
    }
  }

  async function serviceAction(id: string, action: "start" | "stop" | "restart" | "recover" | "restore") {
    const service = normalizedServices.find((item) => item.id === id);
    if (!service) return;
    const state = asState(service.state);
    if (action === "start" || action === "recover") {
      const conflict = normalizedServices.find((item) => item.id !== id && activeStates.includes(asState(item.state)));
      if (conflict) {
        setError(`当前仅允许一个视觉推理服务占用 GPU，请先停止“${conflict.name}”。`);
        return;
      }
    }
    if (action === "start" && state === "archived") {
      setError("已归档服务不能直接启动，请先恢复服务。");
      return;
    }
    setActionBusy(`${id}:${action}`);
    setError("");
    try {
      const result = await requestJson<VisionInferenceServiceRecord>(`/inference/services/${id}/${action}`, { method: "POST" });
      await onRefresh(true);
      setSelectedId(id);
      setView("detail");
      setNotice(action === "start"
        ? result.state === "running" ? "视觉 vLLM 健康检查通过，服务已运行。" : "启动请求已提交，请在运行日志查看模型加载进度。"
        : action === "stop" ? "服务已停止，GPU 资源已释放。"
          : action === "restart" ? "服务已重启，请以健康检查结果为准。"
            : action === "recover" ? "恢复请求已提交，请查看运行状态和日志。"
              : "归档服务已恢复为已停止状态。"
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "服务操作失败。");
      await onRefresh(true);
    } finally {
      setActionBusy("");
    }
  }

  function requestArchive(id: string) {
    const service = normalizedServices.find((item) => item.id === id);
    if (!service) return;
    if (activeStates.includes(asState(service.state))) {
      setError("运行中的服务不能归档，请先停止服务。");
      return;
    }
    setConfirmAction({ type: "archive", serviceId: id });
  }

  function requestDelete(id: string) {
    const service = normalizedServices.find((item) => item.id === id);
    if (!service) return;
    if (asState(service.state) !== "archived") {
      setError("只有已归档服务可以永久删除。");
      return;
    }
    setConfirmAction({ type: "delete", serviceId: id });
  }

  async function confirmServiceAction() {
    if (!confirmAction) return;
    const { type, serviceId } = confirmAction;
    setConfirmAction(null);
    setActionBusy(`${serviceId}:${type}`);
    setError("");
    try {
      if (type === "archive") {
        await requestJson(`/inference/services/${serviceId}/archive`, { method: "POST" });
        setNotice("服务已归档，配置、日志和指标仍会保留。");
      } else {
        await requestJson(`/inference/services/${serviceId}`, { method: "DELETE" });
        setNotice("服务已永久删除，视觉模型资产不受影响。");
        setView("list");
      }
      await onRefresh(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : type === "archive" ? "归档服务失败。" : "删除服务失败。");
    } finally {
      setActionBusy("");
    }
  }

  async function toggleExposure(service: VisionInferenceServiceRecord) {
    if (asState(service.state) !== "running") {
      setError("只有运行中的服务可以上线；上线仅代表允许内部模块调用，不暴露公网。");
      return;
    }
    const current = (service.exposure ?? "test") as ExposureState;
    const exposure: ExposureState = current === "online" ? "offline" : "online";
    setActionBusy(`${service.id}:exposure`);
    setError("");
    try {
      await requestJson(`/inference/services/${service.id}/exposure`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ exposure }),
      });
      await onRefresh(true);
      setNotice(exposure === "online" ? "服务已上线，可供内部模块调用。" : "服务已下线，运行进程仍保留。");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "更新上线状态失败。");
    } finally {
      setActionBusy("");
    }
  }

  async function loadLogs() {
    if (!selectedService) return;
    setLogsLoading(true);
    setError("");
    try {
      const result = await requestJson<{ logs?: string[] }>(`/inference/services/${selectedService.id}/logs?limit=200`);
      setLogs(result.logs ?? []);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "读取运行日志失败。");
    } finally {
      setLogsLoading(false);
    }
  }

  function downloadLogs() {
    if (!selectedService) return;
    const blob = new Blob([logs.join("\n")], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${selectedService.id}-runtime.log`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  async function issueApiKey() {
    if (!selectedService) return;
    setApiKeyBusy(true);
    setError("");
    try {
      const result = await requestJson<{ apiKey: string; createdAt?: string }>(`/inference/services/${selectedService.id}/api-key`, { method: "POST" });
      setIssuedApiKey(result.apiKey);
      setApiKeyStatus({ hasApiKey: true, createdAt: result.createdAt });
      setNotice("API Key 已生成；明文只在当前页面显示，请立即保存。 ");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "生成 API Key 失败。"); }
    finally { setApiKeyBusy(false); }
  }

  async function revokeApiKey() {
    if (!selectedService || !window.confirm("撤销后现有调用会立即失效，确认继续？")) return;
    setApiKeyBusy(true);
    setError("");
    try {
      await requestJson(`/inference/services/${selectedService.id}/api-key`, { method: "DELETE" });
      setIssuedApiKey("");
      setApiKeyStatus({ hasApiKey: false, createdAt: null });
      setNotice("API Key 已撤销。");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "撤销 API Key 失败。"); }
    finally { setApiKeyBusy(false); }
  }

  function clearDebugImage() {
    if (debugPreview) URL.revokeObjectURL(debugPreview);
    setDebugImage(null);
    setDebugPreview("");
    setDebugImageError("");
    setDebugAnswer("");
  }

  function chooseDebugImage(file: File | null) {
    clearDebugImage();
    if (!file) return;
    if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
      setDebugImageError("仅支持 PNG、JPEG、WebP 图片，请重新选择。");
      return;
    }
    if (file.size > 10 * 1024 * 1024) {
      setDebugImageError("图片大小不能超过 10MB，请压缩后重新选择。");
      return;
    }
    setDebugImage(file);
    setDebugPreview(URL.createObjectURL(file));
  }

  async function sendDebugRequest() {
    if (!selectedService || !debugImage || !debugQuestion.trim()) return;
    setDebugSending(true);
    setError("");
    setDebugAnswer("");
    try {
      const imageBase64 = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result ?? ""));
        reader.onerror = () => reject(new Error("读取图片失败。"));
        reader.readAsDataURL(debugImage);
      });
      const result = await requestJson<{ answer?: string }>(`/inference/services/${selectedService.id}/vision-chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          serviceId: selectedService.id,
          imageBase64,
          imageFilename: debugImage.name,
          question: debugQuestion,
          maxTokens: 512,
          temperature: 0.2,
          topP: 0.9,
          stream: false,
        }),
      });
      setDebugAnswer(result.answer ?? "模型没有返回文本回答。");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "视觉推理失败。");
    } finally {
      setDebugSending(false);
    }
  }

  function renderList() {
    const visibleServices = filteredServices;
    return (
      <div className="vision-inference-standard">
        <PageHeader title="视觉推理服务" description="" actions={<button className="primary-btn icon-text-btn" onClick={openCreate}><Server size={15} />创建推理服务</button>} />
        {(externalError || externalNotice || error || notice) && (
          <div className={`inference-inline-notice ${externalError || error ? "warning-notice" : ""}`}>
            {error || externalError || notice || externalNotice}
            <button aria-label="关闭提示" onClick={() => { setError(""); setNotice(""); }}>×</button>
          </div>
        )}
        <div className="summary">
          <div className="metric"><div className="metric-value">{normalizedServices.filter((service) => asState(service.state) !== "archived").length}</div><div className="metric-label">当前服务</div></div>
          <div className="metric"><div className="metric-value blue">{normalizedServices.filter((service) => asState(service.state) === "running").length}</div><div className="metric-label">运行中</div></div>
          <div className="metric"><div className="metric-value green">{normalizedServices.filter((service) => service.exposure === "online").length}</div><div className="metric-label">已上线</div></div>
          <div className="metric"><div className="metric-value red">{normalizedServices.filter((service) => asState(service.state) === "failed").length}</div><div className="metric-label">启动失败</div></div>
        </div>
        <div className="filters">
          <span className="filter-count">{visibleServices.length} 个服务</span>
          <label className="vision-service-search"><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索服务、模型或训练任务" /></label>
          <select className="filter-select" value={stateFilter} onChange={(event) => setStateFilter(event.target.value as "all" | ServiceState)} aria-label="服务状态筛选">
            <option value="all">全部状态</option>
            {(Object.keys(stateMeta) as ServiceState[]).map((state) => <option key={state} value={state}>{stateMeta[state].label}</option>)}
          </select>
          <div className="filter-spacer" />
          <button className="secondary-btn refresh icon-only-btn" title="刷新列表" aria-label="刷新列表" onClick={() => void onRefresh()} disabled={loading}><RefreshCw size={16} className={loading ? "spin" : ""} /></button>
        </div>
        <div className="table-wrap">
          <div className="table-scroll">
            <table className="inference-table vision-inference-table">
              <thead><tr><th>服务名称</th><th>运行状态</th><th>上线状态</th><th>模型与 Adapter</th><th>GPU 资源</th><th>更新时间</th><th>操作</th></tr></thead>
              <tbody>
                {visibleServices.map((service) => {
                  const state = asState(service.state);
                  const busy = actionBusy.startsWith(`${service.id}:`);
                  return (
                    <tr key={service.id}>
                      <td><button className="dataset-link" onClick={() => openDetail(service.id)}>{service.name}</button><div className="row-sub">{service.id} · {service.description || "视觉理解图生文服务"}</div></td>
                      <td>{statusBadge(service)}<div className="row-sub">{stateMeta[state].hint}</div>{state === "failed" && <div className="row-sub">点击“详细”查看失败原因与运行日志</div>}</td>
                      <td>{exposureBadge(service)}</td>
                      <td><strong className="inference-model-name">{modelLabel(service, models)}</strong><div className="row-sub">{service.adapter || "视觉基模服务（无 Adapter）"}</div></td>
                      <td>{service.gpu || "未占用"}<div className="row-sub">显存 {service.memory || "0 / 0 GB"}</div></td>
                      <td>{formatTime(service.updatedAt)}<div className="row-sub">创建于 {formatTime(service.createdAt)}</div></td>
                      <td><div className="table-actions">
                        <button onClick={() => openDetail(service.id)}><FileText size={13} />详细</button>
                        {state === "archived" ? <><button onClick={() => void serviceAction(service.id, "restore")} disabled={busy}><RotateCw size={13} />恢复</button><button className="danger-text" onClick={() => requestDelete(service.id)} disabled={busy}><Trash2 size={13} />删除</button></>
                          : state === "running" ? <><button onClick={() => onOpenInference(service.id)}><Wrench size={13} />调试</button><button className="danger-text" onClick={() => void serviceAction(service.id, "stop")} disabled={busy}><Square size={13} />停止</button></>
                            : state === "failed" ? <><button onClick={() => void serviceAction(service.id, "recover")} disabled={busy}><RotateCw size={13} />恢复</button><button onClick={() => requestArchive(service.id)} disabled={busy}><Archive size={13} />归档</button></>
                              : state === "starting" || state === "recovering" ? <button className="danger-text" onClick={() => void serviceAction(service.id, "stop")} disabled={busy}><Square size={13} />停止</button>
                                : state === "stopping" ? null : <><button onClick={() => void serviceAction(service.id, "start")} disabled={busy}><Play size={13} />启动</button><button onClick={() => requestArchive(service.id)} disabled={busy}><Archive size={13} />归档</button></>}
                      </div></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {!visibleServices.length && <EmptyState kind={normalizedServices.length ? "filtered" : "empty"} title={normalizedServices.length ? "没有符合筛选条件的视觉推理服务" : "尚未创建视觉推理服务"} description={normalizedServices.length ? "调整搜索词或服务状态筛选后重试。" : "请先选择本地或优云可用的视觉基模。"} />}
        </div>
        <div className="inference-state-legend"><strong>生命周期状态</strong>{(Object.keys(stateMeta) as ServiceState[]).map((state) => <span key={state} className={`status inference-status ${stateMeta[state].className}`}>{stateMeta[state].label}</span>)}</div>
        {confirmAction && renderConfirmDialog()}
      </div>
    );
  }

  function renderCreate() {
    const selectedModel = providerModelOptions.find((model) => model.id === createForm.modelId) ?? null;
    const isYouyun = createForm.provider === "youyun";
    const selectedModelReady = deployableModels.some((model) => model.id === createForm.modelId);
    return (
      <div className="vision-inference-standard">
        <div className="vision-standard-back"><button className="back-button" onClick={() => setView("list")}><ArrowLeft size={15} />返回推理服务</button></div>
        <div className="vision-standard-subhead"><div><h2>创建视觉推理服务</h2></div><span className="vision-create-state">视觉理解 · {isYouyun ? "优云远程 vLLM" : "本地 vLLM"}</span></div>
        <div className="inference-steps"><div className="active"><span>1</span>选择视觉基模</div><i /><div className="active"><span>2</span>配置运行参数</div><i /><div><span>3</span>确认并创建</div></div>
        {(error || notice) && <div className={`inference-inline-notice ${error ? "warning-notice" : ""}`}>{error || notice}<button aria-label="关闭提示" onClick={() => { setError(""); setNotice(""); }}>×</button></div>}
        <form onSubmit={(event) => void submitCreate(event)}>
          <div className="inference-create-layout vision-service-create-layout">
            <div className="inference-form-stack">
              <section className="panel inference-form-section"><div className="section-heading"><div><h2>选择视觉基模</h2></div><span className="section-index">01</span></div>
                <div className="inference-field"><label>服务名称</label><input value={createForm.name} onChange={(event) => setCreateForm({ ...createForm, name: event.target.value })} required /></div>
                <div className="inference-field"><label>服务说明</label><input value={createForm.description} onChange={(event) => setCreateForm({ ...createForm, description: event.target.value })} /></div>
                <div className="inference-field"><label>运行环境</label><div className="inference-provider-options"><button type="button" className={!isYouyun ? "selected" : ""} onClick={() => setCreateForm((current) => ({ ...current, provider: "local", modelId: "" }))}><span className="inference-mode-radio" /><span><strong>本地 GPU / CPU</strong><small>需要本地模型目录和视觉探针通过</small></span></button><button type="button" className={isYouyun ? "selected" : ""} onClick={() => setCreateForm((current) => ({ ...current, provider: "youyun", modelId: "" }))}><span className="inference-mode-radio" /><span><strong>优云远程 GPU</strong><small>按量计费，启动时创建一次性实例</small></span></button></div></div>
                 <div className="inference-field"><label>{isYouyun ? "优云可用模型" : "本地可用模型"}</label><select value={createForm.modelId} onChange={(event) => setCreateForm({ ...createForm, modelId: event.target.value })} required disabled={providerModelsLoading}><option value="">{providerModelsLoading ? "正在读取推理模型..." : "请选择基座模型或训练完成模型"}</option>{(["base", "trained"] as const).map((group) => <optgroup key={group} label={group === "base" ? "基座模型" : "训练完成模型"}>{providerModelOptions.filter((model) => model.group === group).map((model) => { const available = deployableModels.some((item) => item.id === model.id); return <option key={model.id} value={model.id} disabled={!available}>{model.label} · {model.repoId}{model.adapterVariant ? ` · ${model.adapterVariant.toUpperCase()} Adapter` : ""}{available ? "" : "（不可部署）"}</option>; })}</optgroup>)}</select></div>
                 {providerModelsError && <div className="field-error">{providerModelsError}</div>}
                 {!isYouyun && deployableModels.length === 0 && <div className="inference-scope-note"><Info className="inference-note-icon-svg" size={16} /><div><strong>没有本地可用模型</strong><p>当前登记的基座模型没有本地目录或未通过探针；切换到优云后可按 Repo ID 选择基座模型，暂不执行本地真实推理。</p></div></div>}
                 {selectedModel && <div className={`vision-model-selection-note ${selectedModelReady ? "ready" : "blocked"}`}><strong>{selectedModelReady ? "模型可用于视觉推理服务" : "当前模型不可用于视觉推理服务"}</strong><span>{selectedModel.group === "trained" ? `${selectedModel.label} · ${selectedModel.adapterVariant?.toUpperCase()} Adapter` : selectedModel.repoId}</span><small>{selectedModel.group === "trained" ? `关联训练任务 ${selectedModel.taskId}，服务启动时加载基座并挂载 Adapter。` : selectedModel.reason || (isYouyun ? "Repo ID 已匹配优云公共模型库。" : "已通过服务器视觉配置探针。")}</small></div>}
              </section>
              {isYouyun && <section className="panel inference-form-section"><div className="section-heading"><div><h2>优云 GPU 资源</h2></div><span className="section-index">02</span></div>
                {youyunError && <div className="field-error">{youyunError}{youyunAuthRequired && <button type="button" className="link-btn" onClick={() => window.location.assign(apiUrl("/auth/login"))}>登录中台</button>}</div>}
                <div className="youyun-specs-panel"><div className="youyun-specs-head"><div><strong>实时库存与按量价格</strong><small>{youyunSpecs ? `${youyunRegionLabel(youyunSpecs.region)} / ${youyunSpecs.zone} · ${youyunSpecs.chargeType}` : youyunError ? "暂无法获取可用规格" : "正在查询可用规格..."}</small></div><button type="button" className="secondary-btn compact-btn" disabled={youyunLoading} onClick={() => void refreshYouyunSpecs()}>{youyunLoading ? "查询中..." : "刷新库存"}</button></div>
                  {youyunSpecs && <div className="youyun-resource-guidance"><strong>{youyunSpecs.fallback ? "已启用 UHost 兜底" : "自动资源推荐已启用"}</strong><span>{youyunSpecs.fallbackReason || `优先选择健康 Pod；当前目录 ${(youyunSpecs.zones ?? []).filter((zone) => zone.healthStatus !== "cooldown").length} 个可用区（Pod ${(youyunSpecs.zones ?? []).filter((zone) => zone.healthStatus !== "cooldown" && youyunZoneModes(zone).includes("pod")).length} / UHost ${(youyunSpecs.zones ?? []).filter((zone) => zone.healthStatus !== "cooldown" && youyunZoneModes(zone).includes("uhost")).length}）。`}</span>{(youyunSpecs.zones ?? []).some((zone) => zone.healthStatus === "cooldown") && <small>已过滤 {(youyunSpecs.zones ?? []).filter((zone) => zone.healthStatus === "cooldown").length} 个近期不可用可用区；Pod 创建失败后将先释放实例，再切换健康 UHost。</small>}<small>推荐镜像：{youyunSpecs.recommendedInferenceImageName || "预置 vLLM 镜像"}</small></div>}
                  {youyunSpecs?.zones?.length ? <div className="youyun-location-picker">{youyunSpecs.fallbackReason && <small className="vision-field-help">{youyunSpecs.fallbackReason}</small>}<YouyunZonePicker zones={youyunSpecs.zones.filter((zone) => zone.healthStatus !== "cooldown")} selectedZone={selectedYouyunZone} onSelect={(zone) => { setSelectedYouyunRegion(zone.region); setSelectedYouyunZone(zone.zone); setYouyunSpecs(null); setYouyunResource(null); void refreshYouyunSpecs(createForm.youyunGpuType, zone.region, zone.zone); }} /></div> : null}
                  {youyunSpecs && <><div className="youyun-machine-list">{youyunSpecs.machineTypes.map((machine) => <button type="button" key={`${machine.name}-${machine.graphicsMemoryGb}`} className={`youyun-machine-item${youyunMachineIsUnavailable(machine, "inference") ? " sold-out" : ""}${createForm.youyunGpuType === machine.name ? " selected" : ""}`} disabled={youyunMachineIsUnavailable(machine, "inference")} onClick={() => { setCreateForm((current) => ({ ...current, youyunGpuType: machine.name, youyunCpu: "", youyunMemory: "" })); setYouyunSpecs(null); setYouyunResource(null); void refreshYouyunSpecs(machine.name); }}><strong>{machine.graphicsMemoryGb ? `${machine.graphicsMemoryGb}G` : "GPU"} {machine.name}</strong><small>{machine.graphicsMemoryGb ? `${machine.graphicsMemoryGb}GB 显存` : "显存待确认"}</small><YouyunInventoryIndicator machine={machine} /></button>)}</div><div className="youyun-capacity-list youyun-single-capacity-list">{youyunSpecs.capacity.filter((spec) => spec.gpu === 1).map((spec) => <button type="button" key={`${spec.gpu}-${spec.cpu}-${spec.memoryGb}`} disabled={!spec.available} className={`youyun-capacity-item${Number(createForm.youyunCpu) === spec.cpu && Number(createForm.youyunMemory) === spec.memoryGb ? " selected" : ""}`} onClick={() => setCreateForm((current) => ({ ...current, youyunCpu: String(spec.cpu), youyunMemory: String(spec.memoryGb) }))}><span>{spec.cpu} 核 / {spec.memoryGb}GB 内存</span><span className={spec.available ? "tag-ready" : "tag-off"}>{spec.available ? "可选" : "无库存"}</span></button>)}</div><div className="youyun-price-summary"><strong>预计费用</strong><span>{youyunPriceLoading ? "查询中..." : youyunResource ? `${youyunResource.price.currency === "CNY" ? "¥" : `${youyunResource.price.currency} `}${youyunResource.price.total.toFixed(2)} / 小时` : "暂不可获取"}</span></div></>}
                </div>
              </section>}
              <section className="panel inference-form-section"><div className="section-heading"><div><h2>基础运行参数</h2></div><span className="section-index">{isYouyun ? "03" : "02"}</span></div>
                <div className="vision-service-form-grid">
                  <div className="inference-field"><label>最大上下文长度</label><input type="number" min="128" value={createForm.maxModelLength} onChange={(event) => setCreateForm({ ...createForm, maxModelLength: event.target.value })} /></div>
                  <div className="inference-field"><label>默认最大输出</label><input type="number" min="1" value={createForm.maxTokens} onChange={(event) => setCreateForm({ ...createForm, maxTokens: event.target.value })} /></div>
                  <div className="inference-field"><label>Temperature</label><input type="number" min="0" max="2" step="0.1" value={createForm.temperature} onChange={(event) => setCreateForm({ ...createForm, temperature: event.target.value })} /></div>
                  <div className="inference-field"><label>Top P</label><input type="number" min="0.01" max="1" step="0.01" value={createForm.topP} onChange={(event) => setCreateForm({ ...createForm, topP: event.target.value })} /></div>
                  <div className="inference-field"><label>数据类型</label><select value={createForm.dtype} onChange={(event) => setCreateForm({ ...createForm, dtype: event.target.value as CreateForm["dtype"] })}><option value="auto">自动</option><option value="float16">Float16</option><option value="bfloat16">BFloat16</option></select></div>
                  <div className="inference-field"><label>GPU 显存利用率</label><input type="number" min="0.01" max="0.99" step="0.01" value={createForm.gpuMemoryUtilization} onChange={(event) => setCreateForm({ ...createForm, gpuMemoryUtilization: event.target.value })} /></div>
                  <div className="inference-field"><label>最大并发序列</label><input type="number" min="1" value={createForm.maxNumSeqs} onChange={(event) => setCreateForm({ ...createForm, maxNumSeqs: event.target.value })} /></div>
                  <div className="inference-field"><label>最大批处理 Token</label><input type="number" min="128" value={createForm.maxNumBatchedTokens} onChange={(event) => setCreateForm({ ...createForm, maxNumBatchedTokens: event.target.value })} /></div>
                  <div className="inference-field"><label>Tensor Parallel</label><input type="number" min="1" max="8" value={createForm.tensorParallelSize} onChange={(event) => setCreateForm({ ...createForm, tensorParallelSize: event.target.value })} /></div>
                  <div className="inference-field"><label>模型加载超时（秒）</label><input type="number" min="30" value={createForm.loadTimeout} onChange={(event) => setCreateForm({ ...createForm, loadTimeout: event.target.value })} /></div>
                </div>
                <div className="vision-service-toggle-grid"><label><input type="checkbox" checked={createForm.enablePrefixCaching} onChange={(event) => setCreateForm({ ...createForm, enablePrefixCaching: event.target.checked })} /><span>启用 Prefix Caching</span></label><label><input type="checkbox" checked={createForm.enableChunkedPrefill} onChange={(event) => setCreateForm({ ...createForm, enableChunkedPrefill: event.target.checked })} /><span>启用 Chunked Prefill</span></label></div>
              </section>
            </div>
             <aside className="panel inference-create-summary"><div className="section-heading"><div><h2>创建前确认</h2></div><Gauge size={20} color="#3538cd" /></div><div className="inference-summary-list"><div><span>服务类型</span><strong>视觉理解 / 图生文</strong></div><div><span>基模</span><strong>{selectedModel?.repoId || "未选择"}</strong></div><div><span>部署方式</span><strong>{isYouyun ? "优云远程 vLLM" : "本地 vLLM"}</strong></div><div><span>模型版本</span><strong>{selectedModel?.group === "trained" ? `${selectedModel.adapterVariant?.toUpperCase()} Adapter` : "基座模型"}</strong></div><div><span>GPU 资源</span><strong>{isYouyun && youyunResource ? `${youyunResource.gpuType} · ${youyunResource.price.total.toFixed(2)} / 小时` : isYouyun ? "待选择优云规格" : "启动时分配"}</strong></div></div><div className="inference-summary-actions"><button type="button" className="secondary-btn" onClick={() => setView("list")}>取消</button><button type="submit" className="primary-btn icon-text-btn" disabled={creating || providerModelsLoading || !createForm.name.trim() || !createForm.modelId || !selectedModelReady || (isYouyun && (!youyunSpecs || !youyunResource))}>{creating ? <RefreshCw size={15} className="spin" /> : <Check size={15} />}{creating ? "创建中" : "确认创建"}</button></div></aside>
          </div>
        </form>
      </div>
    );
  }

  function renderDetail() {
    if (!selectedService) return renderList();
    const state = asState(selectedService.state);
    const model = modelForService(selectedService, models);
    const busy = actionBusy.startsWith(`${selectedService.id}:`);
    return (
      <div className="vision-inference-standard">
        <div className="vision-standard-back"><button className="back-button" onClick={() => setView("list")}><ArrowLeft size={15} />返回推理服务</button></div>
        <div className="inference-detail-head"><div><div className="inference-detail-title"><h2>{selectedService.name}</h2>{statusBadge(selectedService)}{exposureBadge(selectedService)}</div><p>{selectedService.id} · {selectedService.description || "视觉理解图生文推理服务"}</p></div><div className="vision-detail-actions">
          <button className="secondary-btn icon-text-btn" onClick={() => setDetailTab("debug")}><Wrench size={14} />图生文</button>{evaluationEnabled && <button className="secondary-btn icon-text-btn" onClick={() => setDetailTab("evaluation")}><FileText size={14} />验证评测</button>}
          {state === "running" && <><button className="primary-btn icon-text-btn" onClick={() => setDetailTab("debug")}><Wrench size={14} />调试</button><button className="secondary-btn icon-text-btn" onClick={() => void serviceAction(selectedService.id, "stop")} disabled={busy}><Square size={14} />停止</button><button className="secondary-btn icon-only-btn" title="重启服务" aria-label="重启服务" onClick={() => void serviceAction(selectedService.id, "restart")} disabled={busy}><RotateCw size={15} /></button></>}
          {(state === "created" || state === "stopped") && <button className="primary-btn icon-text-btn" onClick={() => void serviceAction(selectedService.id, "start")} disabled={busy}><Play size={14} />启动</button>}
          {state === "failed" && <button className="primary-btn icon-text-btn" onClick={() => void serviceAction(selectedService.id, "recover")} disabled={busy}><RotateCw size={14} />恢复</button>}
          {state === "archived" && <button className="primary-btn icon-text-btn" onClick={() => void serviceAction(selectedService.id, "restore")} disabled={busy}><RotateCw size={14} />恢复服务</button>}
          {state === "running" && <button className="secondary-btn" onClick={() => void toggleExposure(selectedService)} disabled={busy}>{selectedService.exposure === "online" ? "下线" : "上线"}</button>}
        </div></div>
        <div className="inference-meta-chips">
          <button type="button" className="inference-meta-chip" title="复制服务 ID" onClick={() => { void navigator.clipboard?.writeText(selectedService.id); setNotice("服务 ID 已复制。"); }}>服务 ID <code>{selectedService.id}</code> <Copy size={12} /></button>
          <span className="inference-meta-chip">提供方 <strong>{configValue(selectedService, "provider") === "youyun" ? "优云远程 vLLM" : "本地 vLLM"}</strong></span>
          <button type="button" className="inference-meta-chip" title="复制服务端点" onClick={() => { void navigator.clipboard?.writeText(selectedService.endpoint || ""); setNotice("服务端点已复制。"); }}>端点 <code>{selectedService.endpoint || "—"}</code> <Copy size={12} /></button>
          <span className="inference-meta-chip">更新 <strong>{formatTime(selectedService.updatedAt)}</strong></span>
        </div>
        {(selectedService.error || selectedService.failureMessage || selectedService.cleanupError) && (
          <div className="inference-error-card">
            <CircleAlert size={18} color="#b42318" />
            <div>
              <strong>{selectedService.cleanupState === "failed" || state === "cleanup_failed" ? "服务资源清理失败" : "服务启动或运行失败"}</strong>
              <p>{selectedService.failureMessage || selectedService.error || selectedService.cleanupError}</p>
              {selectedService.failureCode && <small>失败分类：{selectedService.failureCode}</small>}
              {selectedService.suggestedAction && <small>建议动作：{selectedService.suggestedAction}</small>}
            </div>
            <button className="link-btn" data-error={selectedService.error || undefined} aria-label={selectedService.error ? "查看日志" : "打开运行日志"} onClick={() => setDetailTab("logs")}>查看日志</button>
          </div>
        )}
        {error && <div className="inference-inline-notice warning-notice">{error}<button aria-label="关闭提示" onClick={() => setError("")}>×</button></div>}
        {notice && <div className="inference-inline-notice">{notice}<button aria-label="关闭提示" onClick={() => setNotice("")}>×</button></div>}
        <details className="inference-contract-fold">
          <summary><ChevronDown size={14} className="inference-contract-fold-chevron" /><strong>服务运行契约</strong><span>审计视图：模型血缘、资源清理与失败诊断字段，默认收起</span></summary>
          <InferenceServiceDetailSummary service={selectedService} modality="vision" />
        </details>
        <div className="inference-tabs" role="tablist">{([ ["overview", "概览"], ["debug", "图生文"], ...(evaluationEnabled ? [["evaluation", "验证评测"] as const] : []), ["api", "API 接入"], ["logs", "运行日志"] ] as Array<[DetailTab, string]>).map(([key, label]) => <button key={key} className={detailTab === key ? "active" : ""} onClick={() => setDetailTab(key)}>{label}</button>)}</div>
        {detailTab === "overview" && <div className="inference-detail-grid">
          <section className="panel inference-runtime-card"><div className="section-heading"><div><h2>运行状态</h2></div><Activity size={20} color="#027a48" /></div><div className="inference-runtime-summary"><div className="inference-runtime-state">{statusBadge(selectedService)}<span>{stateMeta[state].hint}</span></div><div className="inference-resource-lines"><div><span>GPU 资源</span><strong>{selectedService.gpu || "未占用"}</strong></div><div><span>显存</span><strong>{selectedService.memory || "0 / 0 GB"}</strong></div><div><span>服务端点</span><strong>{selectedService.endpoint}</strong></div><div><span>上线状态</span><strong>{exposureMeta[(selectedService.exposure ?? "offline") as ExposureState]?.label ?? "已下线"}</strong></div><div><span>资源状态</span><strong>{resourceStateLabel(selectedService.resourceState)}</strong></div><div><span>清理状态</span><strong>{cleanupStateLabel(selectedService.cleanupState)}{selectedService.cleanupCheckedAt ? ` · ${formatTime(selectedService.cleanupCheckedAt)}` : ""}</strong></div></div></div>{(selectedService.remoteHandle?.instanceId || selectedService.cleanupError) && <div className="inference-resource-alert"><strong>优云资源核验</strong><span>{selectedService.remoteHandle?.instanceId ? `实例 ${selectedService.remoteHandle.instanceId}` : "未记录实例 ID"}{selectedService.cleanupError ? ` · ${selectedService.cleanupError}` : ""}</span></div>}{selectedService.startup && (state === "starting" || state === "recovering") && <div className="inference-startup-progress"><div className="inference-startup-progress-head"><strong>{selectedService.startup.message || "正在启动"}</strong><span>{selectedService.startup.progress ?? 0}%</span></div><div className="inference-startup-progress-track"><span style={{ width: `${Math.max(0, Math.min(100, selectedService.startup.progress ?? 0))}%` }} /></div></div>}</section>
           <section className="panel inference-artifact-card"><div className="section-heading"><div><h2>视觉模型</h2></div><Server size={20} color="#3538cd" /></div><div className="inference-model-identity"><strong>{model?.repoId || selectedService.model}</strong><span>{model?.name || "已登记视觉模型"}</span><code>{configValue(selectedService, "provider") === "youyun" ? "优云远程 Repo ID" : modelPath(selectedService, models)}</code></div><div className="inference-capabilities"><div><span>工作负载</span><strong>Vision Language</strong><small>图像 + 文本输入</small></div><div><span>运行环境</span><strong>{configValue(selectedService, "provider") === "youyun" ? "优云 GPU" : "本地 GPU / CPU"}</strong><small>{configValue(selectedService, "provider") === "youyun" ? "启动时创建远程实例" : "使用本地视觉运行时"}</small></div><div><span>模型版本</span><strong>{configValue(selectedService, "adapterVariant") ? `${String(configValue(selectedService, "adapterVariant")).toUpperCase()} Adapter` : "基座模型"}</strong><small>{selectedService.adapter ? "已关联训练任务产物" : "纯基座推理"}</small></div></div></section>
          <section className="panel inference-config-card"><div className="section-heading"><div><h2>运行配置</h2></div><Gauge size={20} color="#6941c6" /></div><div className="inference-config-grid vision-config-grid"><div><span>最大上下文</span><strong>{displayValue(configValue(selectedService, "maxModelLength"))}</strong></div><div><span>最大输出</span><strong>{displayValue(configValue(selectedService, "maxTokens"))}</strong></div><div><span>Temperature</span><strong>{displayValue(configValue(selectedService, "temperature"))}</strong></div><div><span>Top P</span><strong>{displayValue(configValue(selectedService, "topP"))}</strong></div><div><span>数据类型</span><strong>{displayValue(configValue(selectedService, "dtype"))}</strong></div><div><span>显存利用率</span><strong>{displayValue(configValue(selectedService, "gpuMemoryUtilization"))}</strong></div><div><span>最大并发</span><strong>{displayValue(configValue(selectedService, "maxNumSeqs"))}</strong></div><div><span>Tensor Parallel</span><strong>{displayValue(configValue(selectedService, "tensorParallelSize"))}</strong></div></div></section>
          <section className="panel inference-performance-card"><div className="section-heading"><div><h2>运行指标</h2></div><button className="secondary-btn icon-text-btn" onClick={() => { setDetailTab("overview"); void onRefresh(true); }} disabled={metricsLoading}><RefreshCw size={14} className={metricsLoading ? "spin" : ""} />刷新</button></div><div className="inference-runtime-metrics"><div><span>运行中请求</span><strong>{displayValue(metrics?.runningRequests, "0")}</strong></div><div><span>等待中请求</span><strong>{displayValue(metrics?.waitingRequests, "0")}</strong></div><div><span>批处理大小</span><strong>{displayValue(metrics?.batchSize, "0")}</strong></div><div><span>首 Token 时延</span><strong>{metrics?.ttftMs == null ? "—" : `${metrics.ttftMs} ms`}</strong></div></div><p className="inference-metric-note">当前视觉服务的健康检查和生命周期状态优先于空闲指标；真实图生文请求请进入“调试”页验证。</p></section>
        </div>}
        {detailTab === "debug" && renderDebug()}
        {detailTab === "evaluation" && <VisionServiceEvaluation serviceId={selectedService.id} serviceName={selectedService.name} />}
        {detailTab === "api" && renderApi()}
        {detailTab === "logs" && renderLogs()}
        {confirmAction && renderConfirmDialog()}
      </div>
    );
  }

  function renderDebug() {
    if (!selectedService) return null;
    return <div className="vision-service-debug-grid"><section className="panel vision-section"><div className="section-heading"><div><h2>图片与问题</h2></div><span className="section-index">01</span></div><label className="vision-file-picker"><Upload size={15} />选择图片<input type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => chooseDebugImage(event.target.files?.[0] ?? null)} /></label>{debugPreview ? <div className="vision-selected-image"><img className="vision-chat-image" src={debugPreview} alt="待推理图片预览" /><button type="button" className="link-btn" onClick={clearDebugImage}>删除图片</button></div> : <div className="vision-image-empty">尚未选择图片</div>}{debugImageError && <div className="field-error">{debugImageError}</div>}<label className="vision-block-label"><span>问题</span><textarea className="textarea" rows={5} value={debugQuestion} onChange={(event) => setDebugQuestion(event.target.value)} /></label><div className="vision-actions"><button className="primary-btn icon-text-btn" disabled={selectedService.state !== "running" || !debugImage || !debugQuestion.trim() || debugSending} onClick={() => void sendDebugRequest()}>{debugSending ? <RefreshCw size={14} className="spin" /> : <Send size={14} />}{debugSending ? "推理中" : "发送推理请求"}</button></div>{selectedService.state !== "running" && <div className="vision-preflight blocked"><strong>服务尚未运行</strong><p>请先启动服务并通过健康检查，再执行图生文调试。</p></div>}</section><section className="panel vision-section vision-answer-panel"><div className="section-heading"><div><h2>模型回答</h2></div><span className="section-index">02</span></div><div className={`vision-answer ${debugAnswer ? "has-answer" : ""}`}>{debugAnswer || "完成图片选择、问题输入并发送请求后，回答会显示在这里。"}</div></section></div>;
  }

  function renderApi() {
    if (!selectedService) return null;
    const endpoint = apiUrl(`/public/vision/services/${selectedService.id}/chat`);
    const example = JSON.stringify({ imageBase64: "<data:image/png;base64,...>", imageFilename: "sample.png", question: "请描述图片内容", maxTokens: 512, temperature: 0.2, topP: 0.9 }, null, 2);
    return <div className="inference-api-layout vision-service-api-layout"><section className="panel inference-api-main"><div className="section-heading"><div><h2>对外图生文 API</h2></div><FileText size={20} color="#3538cd" /></div><div className="inference-endpoint"><span>POST</span><code>{endpoint}</code><button onClick={() => { void navigator.clipboard?.writeText(endpoint); setNotice("接口地址已复制。"); }} title="复制接口地址" aria-label="复制接口地址"><Copy size={14} /></button></div><div className="inference-code-head"><strong>请求示例</strong><span>Header: Authorization: Bearer &lt;API_KEY&gt;</span></div><pre className="inference-code">{example}</pre><div className="inference-summary-actions"><button type="button" className="primary-btn" disabled={apiKeyBusy} onClick={() => void issueApiKey()}>{apiKeyStatus?.hasApiKey ? "轮换 API Key" : "生成 API Key"}</button><button type="button" className="secondary-btn" disabled={apiKeyBusy || !apiKeyStatus?.hasApiKey} onClick={() => void revokeApiKey()}>撤销</button></div>{issuedApiKey && <div className="issued-api-key"><strong>本次生成的 API Key</strong><code>{issuedApiKey}</code><button type="button" className="secondary-btn compact-btn" onClick={() => { void navigator.clipboard?.writeText(issuedApiKey); setNotice("API Key 已复制。"); }}><Copy size={14} />复制</button><small>离开页面后不会再次显示明文。</small></div>}</section><aside className="panel inference-api-side"><div className="section-heading"><div><h2>接入状态</h2></div><Info size={20} color="#6941c6" /></div><div className="inference-api-health"><span className={selectedService.state === "running" ? "healthy" : ""} /><div><strong>{selectedService.state === "running" ? "健康检查通过" : "等待服务启动"}</strong><p>{selectedService.state === "running" ? "外部请求会进入当前视觉服务。" : "启动服务后，外部请求才会返回模型结果。"}</p></div></div><div className="inference-summary-list"><div><span>服务 ID</span><strong>{selectedService.id}</strong></div><div><span>API Key</span><strong>{apiKeyStatus?.hasApiKey ? `已配置 · ${formatTime(apiKeyStatus.createdAt)}` : "未配置"}</strong></div><div><span>工作负载</span><strong>vision_language</strong></div></div></aside></div>;
  }

  function renderLogs() {
    if (!selectedService) return null;
    return <section className="panel inference-logs-panel"><div className="section-heading"><div><h2>运行日志</h2></div><div className="vision-log-actions"><button className="secondary-btn icon-text-btn" onClick={() => void loadLogs()} disabled={logsLoading}><RefreshCw size={14} className={logsLoading ? "spin" : ""} />刷新日志</button><button className="secondary-btn icon-text-btn" onClick={downloadLogs} disabled={!logs.length}><Copy size={14} />下载</button></div></div><pre className="inference-log-viewer">{logs.length ? logs.join("\n") : logsLoading ? "正在读取运行日志..." : "暂无运行日志。"}</pre><div className="inference-log-footer"><span>{logs.length} 条日志 · 自动刷新间隔 5 秒（服务运行中）</span><span>服务：{selectedService.id}</span></div></section>;
  }

  function renderConfirmDialog() {
    if (!confirmAction) return null;
    const isDelete = confirmAction.type === "delete";
    return <div className="inference-config-modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setConfirmAction(null); }}><div className="inference-config-modal vision-confirm-modal"><div className="inference-config-modal-head"><div><h2>{isDelete ? "永久删除视觉推理服务" : "归档视觉推理服务"}</h2><p>{isDelete ? "删除后服务配置、日志和指标将不再保留，视觉模型资产不受影响。" : "归档后服务不会出现在默认列表中，但配置、日志和指标仍会保留。"}</p></div><button className="close-btn" onClick={() => setConfirmAction(null)} aria-label="关闭"><X size={17} /></button></div><div className={`vision-confirm-warning ${isDelete ? "danger" : ""}`}><CircleAlert size={17} /><span>{isDelete ? "该操作不可撤销，请确认服务已经归档且不再需要。" : "运行中的服务不能归档；归档不会自动释放未清理的远程资源。"}</span></div><div className="inference-summary-actions"><button className="secondary-btn" onClick={() => setConfirmAction(null)}>取消</button><button className={isDelete ? "danger-btn" : "primary-btn"} onClick={() => void confirmServiceAction()} disabled={Boolean(actionBusy)}>{isDelete ? <Trash2 size={14} /> : <Archive size={14} />}{isDelete ? "确认删除" : "确认归档"}</button></div></div></div>;
  }

  if (view === "create") return renderCreate();
  if (view === "detail") return renderDetail();
  return renderList();
}
