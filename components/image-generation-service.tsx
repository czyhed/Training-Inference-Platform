"use client";

import {
  ArrowLeft,
  ChevronDown,
  Cloud,
  Copy,
  Download,
  Eye,
  Plus,
  Play,
  RefreshCw,
  RotateCcw,
  Search,
  Square,
  Trash2,
} from "lucide-react";

const SERVICE_STATE_LABELS: Record<string, string> = {
  created: "待启动",
  starting: "启动中",
  running: "运行中",
  stopping: "停止中",
  stopped: "已停止",
  failed: "启动失败",
  recovering: "恢复中",
  cleanup_failed: "清理失败",
  archived: "已归档",
};
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiUrl } from "../lib/api-config";
import { ImageGenerationTasks } from "./image-generation-tasks";
import { ImageGenerationEvaluation } from "./image-generation-evaluation";
import { InferenceServiceDetailSummary } from "./inference-service-detail";
import { EmptyState } from "./ui/empty-state";
import { PageHeader } from "./ui/page-header";
import { selectYouyunZone, youyunMachineIsUnavailable, youyunZoneModes, type YouyunZone } from "../lib/youyun-location";
import { YouyunZonePicker } from "./youyun-zone-picker";
import { YouyunInventoryIndicator } from "./youyun-inventory-indicator";
import { useCapabilities } from "../lib/use-capabilities";

type ServiceView = "list" | "create" | "detail";
type DetailTab = "overview" | "generation" | "evaluation" | "api" | "logs";
const activeServiceStates = new Set(["starting", "running", "stopping", "recovering"]);
type Model = {
  id: string;
  label: string;
  group: "base" | "trained";
  repoId: string;
  modelType: string;
  taskId?: string;
  adapterVariant?: "best" | "final";
  supportedTrainingModes: Array<"lora" | "qlora">;
  readyForYouyun: boolean;
  reason?: string | null;
};
type Capacity = {
  gpu: number;
  cpu: number;
  memoryGb: number;
  available: boolean;
};
type Specs = {
  instanceMode?: "pod" | "uhost" | "auto";
  fallback?: boolean;
  fallbackEligible?: boolean;
  fallbackReason?: string;
  region: string;
  zone: string;
  chargeType: string;
  gpuType: string;
  recommendedImageId: string;
  recommendedImageName: string;
  recommendedImageReason?: string;
  requiresDiffusersInstall?: boolean;
  imageSelectionWarning?: string;
  images: Array<{
    id: string;
    name: string;
    description?: string;
    status?: string;
  }>;
  machineTypes?: Array<{
    name: string;
    zone?: string;
    soldOut?: boolean;
    description?: string;
    graphicsMemoryGb?: number;
    inventoryStatus?: "available" | "sold_out" | "unknown" | "error";
    inventoryMessage?: string;
  }>;
  capacity: Capacity[];
  zones?: YouyunZone[];
};
// Backward-compatible resource contract: instanceMode: "pod" remains accepted.
type PriceBreakdown = {
  currency: string;
  instance?: number;
  disks?: number;
  systemDisks?: number;
  image?: number;
  total: number;
};
type Price = { price?: PriceBreakdown };
type PreflightCheck = { status: string; message: string };
type Preflight = {
  ready: boolean;
  checkedAt?: string;
  expiresAtEpoch?: number;
  checks: Record<string, PreflightCheck>;
};
type ServiceConfig = {
  description?: string;
  modelId?: string;
  modelRepoId?: string;
  trainingMode?: string;
  adapterLineage?: { taskId?: string; variant?: string };
  width?: number;
  height?: number;
  steps?: number;
  cfg?: number;
  batchSize?: number;
  seed?: number;
  sampler?: string;
  negativePrompt?: string;
  precision?: "auto" | "fp16" | "bf16" | string;
  enableCpuOffload?: boolean;
  enableAttentionSlicing?: boolean;
  enableVaeTiling?: boolean;
  enableSafetyChecker?: boolean;
  loadTimeout?: number;
  maxConcurrentRequests?: number;
  idleTimeoutSeconds?: number;
  requestTimeoutSeconds?: number;
  resource?: {
    mode?: string;
    region?: string;
    zone?: string;
    gpuType?: string;
    gpuCount?: number;
    cpuCores?: number;
    memoryGb?: number;
    imageId?: string;
    instanceMode?: string;
    chargeType?: string;
    price?: PriceBreakdown;
    requiresDiffusersInstall?: boolean;
  };
  preflightSnapshot?: Preflight;
};
type Service = {
  id: string;
  name: string;
  state: string;
  config: ServiceConfig;
  createdAt?: string;
  updatedAt?: string;
  startupStage?: string | null;
  startupProgress?: number | null;
  startupStartedAt?: string | null;
  startupElapsedSeconds?: number | null;
  hasApiKey?: boolean;
  apiKeyCreatedAt?: string | null;
};
type ResultHistoryItem = {
  taskId: string;
  taskName?: string;
  prompt?: string;
  state?: string;
  archived?: boolean;
  createdAt?: string;
  updatedAt?: string;
  phase?: string;
  lastHeartbeatAt?: string;
  failureCode?: string | null;
  failureMessage?: string | null;
  suggestedAction?: string | null;
  cleanupState?: string | null;
  cleanupError?: string | null;
  logs?: string[];
  results?: Array<{
    id: string;
    fileName?: string;
    width?: number;
    height?: number;
    available?: boolean;
  }>;
};

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), {
    ...options,
    credentials: "include",
    cache: "no-store",
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(formatRequestError(body, response.status));
  return body as T;
}

function formatRequestError(body: unknown, status: number): string {
  if (!body || typeof body !== "object") return `请求失败（${status}）`;
  const detail =
    "detail" in body ? (body as { detail?: unknown }).detail : undefined;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (!item || typeof item !== "object") return "";
        const value = item as { loc?: unknown; msg?: unknown };
        const message = typeof value.msg === "string" ? value.msg.trim() : "";
        if (!message) return "";
        const location = Array.isArray(value.loc)
          ? value.loc
              .filter(
                (part): part is string | number =>
                  typeof part === "string" || typeof part === "number",
              )
              .join(".")
          : "";
        return location ? `校验字段 ${location}：${message}` : message;
      })
      .filter(Boolean);
    if (messages.length) return Array.from(new Set(messages)).join("；");
  }
  if (!detail || typeof detail !== "object") return `请求失败（${status}）`;
  const structured = detail as {
    message?: unknown;
    reasons?: unknown;
    checks?: unknown;
  };
  const reasons = Array.isArray(structured.reasons)
    ? structured.reasons.filter(
        (item): item is string =>
          typeof item === "string" && Boolean(item.trim()),
      )
    : [];
  const checkMessages =
    structured.checks && typeof structured.checks === "object"
      ? Object.values(structured.checks as Record<string, unknown>)
          .map((item) =>
            item && typeof item === "object" && "message" in item
              ? (item as { message?: unknown }).message
              : undefined,
          )
          .filter(
            (item): item is string =>
              typeof item === "string" && Boolean(item.trim()),
          )
      : [];
  const messages = Array.from(new Set([...reasons, ...checkMessages]));
  if (messages.length) return messages.join("；");
  return typeof structured.message === "string" && structured.message.trim()
    ? structured.message
    : `请求失败（${status}）`;
}

function compactTime(value?: string) {
  if (!value) return "--";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("zh-CN", { hour12: false });
}

function preflightState(preflight?: Preflight) {
  if (!preflight)
    return { label: "未检查", className: "status-stopped", expired: false };
  const expired = Boolean(
    preflight.expiresAtEpoch && Date.now() / 1000 >= preflight.expiresAtEpoch,
  );
  if (expired)
    return { label: "已过期", className: "status-queued", expired: true };
  return preflight.ready
    ? { label: "已通过", className: "status-completed", expired: false }
    : { label: "有阻塞", className: "status-failed", expired: false };
}

const STARTUP_STAGE_LABELS: Record<string, string> = {
  starting: "开始启动",
  creating_instance: "创建优云实例",
  instance_running: "等待实例就绪",
  ssh_connected: "建立 SSH 连接",
  preparing_runtime: "准备 Diffusers 运行环境",
  resolving_model: "解析基座模型",
  uploading_adapter: "上传 Adapter",
  finalizing: "写入服务配置",
  ready: "已就绪",
};

function startupStatusLabel(
  service: Pick<Service, "state" | "startupStage" | "startupProgress" | "startupStartedAt" | "updatedAt">,
) {
  if (service.state !== "starting") return null;
  const stageLabel = STARTUP_STAGE_LABELS[service.startupStage || "starting"] || "启动中";
  const startedAt = service.startupStartedAt || service.updatedAt;
  const startedMs = startedAt ? new Date(startedAt).getTime() : Number.NaN;
  const minutes = Number.isNaN(startedMs) ? null : Math.max(1, Math.round((Date.now() - startedMs) / 60000));
  const progress = typeof service.startupProgress === "number" ? service.startupProgress : null;
  const elapsed = minutes ? ` · 已进行 ${minutes} 分钟` : "";
  const percent = progress != null ? ` · 进度 ${progress}%` : "";
  return { label: stageLabel, progress, text: `启动中 · ${stageLabel}${elapsed}${percent}` };
}

function ParameterInput({
  label,
  value,
  onChange,
  min,
  max,
  step = 1,
  hintRange,
  softMin,
  softMax,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
  hintRange?: string;
  softMin?: number;
  softMax?: number;
}) {
  const belowSoft = softMin !== undefined && value < softMin;
  const aboveSoft = softMax !== undefined && value > softMax;
  return (
    <label className="parameter-field">
      <span>{label}{hintRange ? <em className="parameter-range-hint">{hintRange}</em> : null}</span>
      <input
        type="number"
        className="input"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      {(belowSoft || aboveSoft) && (
        <small className="parameter-range-warning">{belowSoft ? `低于推荐下限 ${softMin}` : `高于推荐上限 ${softMax}`}</small>
      )}
    </label>
  );
}

const SIZE_PRESETS = [
  { key: "1:1", w: 1024, h: 1024 },
  { key: "3:4", w: 896, h: 1152 },
  { key: "4:3", w: 1152, h: 896 },
  { key: "16:9", w: 1344, h: 768 },
];

export default function ImageGenerationService() {
  const { evaluationEnabled } = useCapabilities();
  const [view, setView] = useState<ServiceView>("list");
  const [models, setModels] = useState<Model[]>([]);
  const [services, setServices] = useState<Service[]>([]);
  const [specs, setSpecs] = useState<Specs | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [query, setQuery] = useState("");
  const [modelId, setModelId] = useState("");
  const [trainingMode, setTrainingMode] = useState<"lora" | "qlora">("lora");
  const [name, setName] = useState("文生图推理服务");
  const [description, setDescription] = useState("");
  const [width, setWidth] = useState(1024);
  const [height, setHeight] = useState(1024);
  const [steps, setSteps] = useState(30);
  const [cfg, setCfg] = useState(7);
  const [batchSize, setBatchSize] = useState(1);
  const [seed, setSeed] = useState(-1);
  const [sampler, setSampler] = useState("euler");
  const [negativePrompt, setNegativePrompt] = useState("");
  const [precision, setPrecision] = useState<"auto" | "fp16" | "bf16">("auto");
  const [enableCpuOffload, setEnableCpuOffload] = useState(false);
  const [enableAttentionSlicing, setEnableAttentionSlicing] = useState(false);
  const [enableVaeTiling, setEnableVaeTiling] = useState(false);
  const [enableSafetyChecker, setEnableSafetyChecker] = useState(false);
  const [loadTimeout, setLoadTimeout] = useState(180);
  const [maxConcurrentRequests, setMaxConcurrentRequests] = useState(1);
  const [idleTimeoutSeconds, setIdleTimeoutSeconds] = useState(900);
  const [requestTimeoutSeconds, setRequestTimeoutSeconds] = useState(600);
  const [imageId, setImageId] = useState("");
  const [gpuType, setGpuType] = useState("");
  const [cpuCores, setCpuCores] = useState(0);
  const [memoryGb, setMemoryGb] = useState(0);
  const [selectedYouyunRegion, setSelectedYouyunRegion] = useState("");
  const [selectedYouyunZone, setSelectedYouyunZone] = useState("");
  const [price, setPrice] = useState<Price | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [resourceLoading, setResourceLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [resourceError, setResourceError] = useState("");
  const [preflight, setPreflight] = useState<Preflight | null>(null);
  const [history, setHistory] = useState<ResultHistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [detailTab, setDetailTab] = useState<DetailTab>("overview");
  const [deleteTarget, setDeleteTarget] = useState<Service | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [serviceLogs, setServiceLogs] = useState<string[]>([]);
  const [apiKeyStatus, setApiKeyStatus] = useState<{ hasApiKey: boolean; createdAt?: string | null } | null>(null);
  const [issuedApiKey, setIssuedApiKey] = useState("");
  const [apiKeyBusy, setApiKeyBusy] = useState(false);

  const selectedModel = models.find((item) => item.id === modelId);
  const selectedService = services.find((item) => item.id === selectedId);
  const healthyYouyunZones = useMemo(
    () => (specs?.zones ?? []).filter((zone) => zone.healthStatus !== "cooldown"),
    [specs],
  );

  useEffect(() => {
    if (!selectedService || view !== "detail" || detailTab !== "api") return;
    void request<{ hasApiKey: boolean; createdAt?: string | null }>(`/inference/services/${selectedService.id}/api-key`)
      .then(setApiKeyStatus)
      .catch((reason) => setMessage(reason instanceof Error ? reason.message : "读取 API Key 状态失败。"));
  }, [detailTab, selectedService, view]);

  async function issueApiKey() {
    if (!selectedService) return;
    setApiKeyBusy(true);
    try {
      const result = await request<{ apiKey: string; createdAt?: string }>(`/inference/services/${selectedService.id}/api-key`, { method: "POST" });
      setIssuedApiKey(result.apiKey);
      setApiKeyStatus({ hasApiKey: true, createdAt: result.createdAt });
      setMessage("API Key 已生成；明文只在当前页面显示，请立即保存。");
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : "生成 API Key 失败。"); }
    finally { setApiKeyBusy(false); }
  }

  async function revokeApiKey() {
    if (!selectedService || !window.confirm("撤销后现有调用会立即失效，确认继续？")) return;
    setApiKeyBusy(true);
    try {
      await request(`/inference/services/${selectedService.id}/api-key`, { method: "DELETE" });
      setIssuedApiKey("");
      setApiKeyStatus({ hasApiKey: false, createdAt: null });
      setMessage("API Key 已撤销。");
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : "撤销 API Key 失败。"); }
    finally { setApiKeyBusy(false); }
  }
  const singleCardCapacities = useMemo(
    () => specs?.capacity.filter((item) => item.gpu === 1) ?? [],
    [specs],
  );
  const visibleServices = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) return services;
    return services.filter((service) =>
      [
        service.name,
        service.id,
        service.config.modelRepoId,
        service.config.modelId,
        service.config.adapterLineage?.taskId,
      ].some((value) =>
        String(value || "")
          .toLowerCase()
          .includes(keyword),
      ),
    );
  }, [query, services]);
  const resourceReady = Boolean(
    specs &&
    imageId &&
    cpuCores &&
    memoryGb &&
    price?.price &&
    !resourceLoading &&
    !resourceError,
  );

  function applySpecs(next: Specs) {
    const first = next.capacity.find(
      (item) => item.gpu === 1 && item.available,
    );
    setSpecs(next);
    setSelectedYouyunRegion(next.region);
    setSelectedYouyunZone(next.zone || selectYouyunZone(next.zones ?? [], next.region, next.instanceMode === "uhost" ? "uhost" : "pod"));
    setGpuType(next.gpuType);
    setImageId((current) =>
      current && next.images.some((item) => item.id === current)
        ? current
        : next.recommendedImageId || next.images[0]?.id || "",
    );
    const selectedStillAvailable = next.capacity.some(
      (item) =>
        item.gpu === 1 &&
        item.available &&
        item.cpu === cpuCores &&
        item.memoryGb === memoryGb,
    );
    if (!selectedStillAvailable) {
      setCpuCores(first?.cpu || 0);
      setMemoryGb(first?.memoryGb || 0);
    }
  }

  const loadServices = useCallback(async () => {
    setLoading(true);
    setMessage("");
    try {
      setServices(await request<Service[]>("/image-generation/services"));
    } catch (reason) {
      setMessage(
        reason instanceof Error ? reason.message : "文生图推理服务读取失败。",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  const loadCatalog = useCallback(async () => {
    setResourceError("");
    try {
      const [nextModels, nextSpecs] = await Promise.all([
        request<Model[]>("/image-generation/models?provider=youyun"),
        request<Specs>("/image-generation/youyun/gpu-specs?instanceMode=auto&purpose=inference"),
      ]);
      setModels(nextModels);
      applySpecs(nextSpecs);
      setModelId(
        (current) =>
          current ||
          nextModels.find(
            (item) => item.readyForYouyun && item.group === "trained",
          )?.id ||
          nextModels.find((item) => item.readyForYouyun)?.id ||
          "",
      );
    } catch (reason) {
      setResourceError(
        reason instanceof Error ? reason.message : "优云资源读取失败。",
      );
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const load = useCallback(async () => {
    await Promise.all([loadServices(), loadCatalog()]);
  }, [loadCatalog, loadServices]);

  useEffect(() => {
    void loadServices();
    void loadCatalog();
  }, [loadServices, loadCatalog]);

  const refreshServices = useCallback(async () => {
    try {
      const next = await request<Service[]>("/image-generation/services");
      setServices(next);
      return next;
    } catch {
      return null;
    }
  }, []);

  const hasStartingService = useMemo(
    () => services.some((service) => service.state === "starting"),
    [services],
  );

  useEffect(() => {
    if (!hasStartingService) return;
    const timer = setInterval(() => {
      void refreshServices();
    }, 5000);
    return () => clearInterval(timer);
  }, [hasStartingService, refreshServices]);

  const serviceLogsServiceRef = useRef("");

  useEffect(() => {
    if (view !== "detail" || detailTab !== "logs" || !selectedId) {
      return;
    }
    let active = true;
    if (serviceLogsServiceRef.current !== selectedId) {
      serviceLogsServiceRef.current = selectedId;
      setServiceLogs([]);
    }
    void request<{ logs: string[] }>(`/image-generation/services/${selectedId}/logs?limit=200`)
      .then((value) => {
        if (active) setServiceLogs(value.logs || []);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [selectedId, detailTab, view, services]);
  useEffect(() => {
    if (!specs || !imageId || !cpuCores || !memoryGb) {
      setPrice(null);
      return;
    }
    let active = true;
    setResourceLoading(true);
    setResourceError("");
    void request<Price>("/youyun/price", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mode: "manual",
          instanceMode: specs.instanceMode === "uhost" ? "uhost" : "auto",
          region: selectedYouyunRegion || specs.region,
          zone: selectedYouyunZone || specs.zone,
          gpuType,
        gpuCount: 1,
        cpuCores,
        memoryGb,
        imageId,
      }),
    })
      .then((value) => {
        if (active) setPrice(value);
      })
      .catch((reason) => {
        if (active) {
          setPrice(null);
          setResourceError(
            reason instanceof Error ? reason.message : "优云价格读取失败。",
          );
        }
      })
      .finally(() => {
        if (active) setResourceLoading(false);
      });
    return () => {
      active = false;
    };
  }, [cpuCores, gpuType, imageId, memoryGb, selectedYouyunRegion, selectedYouyunZone, specs]);
  useEffect(() => {
    setPreflight(null);
  }, [
    cfg,
    cpuCores,
    description,
    enableAttentionSlicing,
    enableCpuOffload,
    enableSafetyChecker,
    enableVaeTiling,
    height,
    imageId,
    loadTimeout,
    maxConcurrentRequests,
    idleTimeoutSeconds,
    requestTimeoutSeconds,
    memoryGb,
    modelId,
    name,
    negativePrompt,
    precision,
    steps,
    trainingMode,
    width,
    batchSize,
    sampler,
    seed,
  ]);
  useEffect(() => {
    if (view !== "detail" || !selectedId) {
      setHistory([]);
      return;
    }
    const savedServiceId = selectedId;
    let active = true;
    setHistoryLoading(true);
    setPreflight(
      services.find((item) => item.id === selectedId)?.config
        .preflightSnapshot || null,
    );
    void request<{ items?: ResultHistoryItem[] }>(
      `/image-generation/services/${savedServiceId}/results?includeArchived=true`,
    )
      .then((body) => {
        if (active) setHistory(Array.isArray(body.items) ? body.items : []);
      })
      .catch((reason) => {
        if (active)
          setMessage(
            reason instanceof Error ? reason.message : "生成历史读取失败。",
          );
      })
      .finally(() => {
        if (active) setHistoryLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selectedId, services, view]);

  async function refreshResources(
    selectedGpuType = gpuType,
    regionOverride = selectedYouyunRegion,
    zoneOverride = selectedYouyunZone,
  ) {
    setResourceLoading(true);
    setResourceError("");
    setPrice(null);
    setPreflight(null);
    try {
      applySpecs(
        await request<Specs>(
          `/image-generation/youyun/gpu-specs?${new URLSearchParams({
            instanceMode: "auto",
            purpose: "inference",
            ...(selectedGpuType ? { gpuType: selectedGpuType } : {}),
            ...(imageId ? { imageId } : {}),
            ...(regionOverride ? { region: regionOverride } : {}),
            ...(zoneOverride ? { zone: zoneOverride } : {}),
          }).toString()}`,
        ),
      );
    } catch (reason) {
      setResourceError(
        reason instanceof Error ? reason.message : "优云 GPU 库存查询失败。",
      );
    } finally {
      setResourceLoading(false);
    }
  }

  async function save() {
    if (!name.trim()) return setMessage("请填写服务名称。");
    if (!selectedModel)
      return setMessage("请选择基座模型或训练完成的 Adapter。");
    if (!selectedModel.readyForYouyun)
      return setMessage(
        selectedModel.reason || "所选模型尚未达到优云部署条件。",
      );
    if (!selectedModel.supportedTrainingModes.includes(trainingMode))
      return setMessage("当前模型不支持所选训练方式。");
    if (!specs || !resourceReady)
      return setMessage(
        "请选择有库存的优云 GPU 规格和 Diffusers 镜像，并等待价格确认。",
      );
    setSaving(true);
    setMessage("");
    try {
      const created = await request<Service>("/image-generation/services", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.trim(),
          description: description.trim(),
          modelId,
          taskId: selectedModel.taskId || null,
          adapterVariant: selectedModel.adapterVariant || null,
          trainingMode,
          precision,
          enableCpuOffload,
          enableAttentionSlicing,
          enableVaeTiling,
          enableSafetyChecker,
          loadTimeout,
          maxConcurrentRequests,
          idleTimeoutSeconds,
          requestTimeoutSeconds,
          negativePrompt: negativePrompt.trim(),
          width,
          height,
          steps,
          cfg,
          batchSize,
          seed,
          sampler,
          resource: {
            mode: "manual",
            instanceMode: specs.instanceMode === "uhost" ? "uhost" : "auto",
            region: selectedYouyunRegion || specs.region,
            zone: selectedYouyunZone || specs.zone,
            gpuType,
            gpuCount: 1,
            cpuCores,
            memoryGb,
            chargeType: specs.chargeType,
            imageId,
            price: price?.price,
            requiresDiffusersInstall: Boolean(specs.requiresDiffusersInstall),
          },
        }),
      });
      setServices((current) => [
        created,
        ...current.filter((item) => item.id !== created.id),
      ]);
      setSelectedId(created.id);
      setPreflight(null);
      setView("detail");
      setMessage("配置已保存，可在上方创建生成任务。");
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "配置保存失败。");
    } finally {
      setSaving(false);
    }
  }

  async function runPreflight(serviceId = selectedId) {
    setMessage("");
    try {
      if (!serviceId) throw new Error("请先保存服务配置，再执行启动前检查。");
      const next = await request<Preflight>(
        `/image-generation/services/${serviceId}/preflight`,
        { method: "POST" },
      );
      setPreflight(next);
      setServices((current) =>
        current.map((item) =>
          item.id === serviceId
            ? { ...item, config: { ...item.config, preflightSnapshot: next } }
            : item,
        ),
      );
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "启动前检查失败。");
    }
  }

  function openDetail(service: Service) {
    setMessage("");
    setSelectedId(service.id);
    setPreflight(service.config.preflightSnapshot || null);
    setDetailTab("overview");
    setView("detail");
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    setMessage("");
    try {
      await request<void>(`/inference/services/${deleteTarget.id}`, { method: "DELETE" });
      setServices((current) => current.filter((item) => item.id !== deleteTarget.id));
      if (selectedId === deleteTarget.id) {
        setSelectedId("");
        setView("list");
      }
      setDeleteTarget(null);
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "推理服务删除失败。");
    } finally {
      setDeleting(false);
    }
  }

  async function serviceAction(serviceId: string, action: "start" | "stop" | "restart") {
    setMessage(action === "start" ? "正在启动文生图推理服务…" : action === "restart" ? "正在重启文生图推理服务…" : "正在停止并释放优云实例…");
    try {
      const updated = await request<Service>(`/image-generation/services/${serviceId}/${action}`, { method: "POST" });
      setServices((current) => current.map((item) => item.id === serviceId ? updated : item));
      setPreflight(updated.config.preflightSnapshot || null);
      setMessage(action === "start" ? "文生图推理服务已启动，可以生成图片。" : action === "restart" ? "重启指令已下发，服务正在重新拉起。" : "文生图推理服务已停止，优云实例已释放。");
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : `${action === "start" ? "启动" : action === "restart" ? "重启" : "停止"}服务失败。`);
    }
  }

  const checkedCount = services.filter(
    (service) =>
      preflightState(service.config.preflightSnapshot).label === "已通过",
  ).length;
  const adapterCount = services.filter((service) =>
    Boolean(service.config.adapterLineage?.taskId),
  ).length;
  const deleteDialog = deleteTarget && <div className="modal-backdrop" role="presentation" onMouseDown={() => !deleting && setDeleteTarget(null)}><div className="modal" role="dialog" aria-modal="true" aria-labelledby="image-service-delete-title" onMouseDown={(event) => event.stopPropagation()}><h2 id="image-service-delete-title">确认删除推理服务</h2><p>将永久删除服务配置、日志和指标：服务“{deleteTarget.name}”。此操作不可撤销。</p><div className="modal-actions"><button type="button" className="secondary-btn" disabled={deleting} onClick={() => setDeleteTarget(null)}>取消</button><button type="button" className="danger-btn icon-text-btn" disabled={deleting} onClick={() => void confirmDelete()}><Trash2 size={15} />{deleting ? "删除中" : "永久删除"}</button></div></div></div>;

  if (view === "list")
    return (
      <section className="content image-generation-service-page image-generation-service-list-page">
        <PageHeader title="文生图推理服务" description="管理 SDXL 基座模型与训练 Adapter 的优云推理配置；保存服务不会创建实例" actions={<button type="button" className="primary-btn icon-text-btn" onClick={() => { setMessage(""); setPreflight(null); setView("create"); }}><Plus size={15} />创建推理服务</button>} />
        <div className="training-metrics image-generation-service-metrics">
          <div>
            <span>服务总数</span>
            <strong>{services.length}</strong>
          </div>
          <div>
            <span>Adapter 服务</span>
            <strong>{adapterCount}</strong>
          </div>
          <div>
            <span>预检可用</span>
            <strong>{checkedCount}</strong>
          </div>
          <div>
            <span>运行环境</span>
            <strong>优云</strong>
          </div>
        </div>
        <div className="filters image-generation-service-filters">
          <label className="search-wrap">
            <Search size={15} />
            <input
              className="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索服务名称或模型"
            />
          </label>
          <button
            type="button"
            className="secondary-btn icon-text-btn"
            disabled={loading}
            onClick={() => void load()}
          >
            <RefreshCw size={15} className={loading ? "spin" : ""} />
            刷新
          </button>
        </div>
        {message && <div className="form-error">{message}</div>}
        <div className="table-wrap training-table image-generation-service-table">
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  {/* 皮肤测试约定：表头必须保持单行连续标记。 */}
                  <th>服务名称</th><th>模型与 Adapter</th><th>默认生成参数</th><th>优云资源</th><th>启动前检查</th><th>更新时间</th><th>操作</th>
                </tr>
              </thead>
              <tbody>
                {visibleServices.map((service) => {
                  const config = service.config || {};
                  const resource = config.resource || {};
                  const check = preflightState(config.preflightSnapshot);
                  return (
                    <tr key={service.id}>
                      <td>
                        <button
                          type="button"
                          className="model-name-btn"
                          onClick={() => openDetail(service)}
                        >
                          <strong>{service.name}</strong>
                          <small>{service.id}</small>
                        </button>
                      </td>
                      <td>
                        <strong>
                          {config.modelRepoId || config.modelId || "--"}
                        </strong>
                        <div className="row-sub">
                          {config.adapterLineage?.taskId
                            ? `${config.adapterLineage.variant === "best" ? "Best" : "Final"} Adapter · ${config.adapterLineage.taskId}`
                            : "基座模型"}
                        </div>
                      </td>
                      <td>
                        {config.width || 1024} × {config.height || 1024}
                        <div className="row-sub">
                          {config.steps || 30} steps · CFG {config.cfg ?? 7} ·{" "}
                          {config.sampler || "euler"}
                        </div>
                      </td>
                      <td>
                        {resource.gpuType || "--"} × {resource.gpuCount || 1}
                        <div className="row-sub">
                          {resource.cpuCores || "--"} CPU ·{" "}
                          {resource.memoryGb || "--"}GB
                        </div>
                      </td>
                      <td>
                        <span className={`status ${check.className}`}>
                          <span className="status-dot" />
                          {check.label}
                        </span>
                        <div className="row-sub">
                          {compactTime(config.preflightSnapshot?.checkedAt)}
                        </div>
                      </td>
                      <td>
                        {compactTime(service.updatedAt || service.createdAt)}
                        <div className="row-sub">平台管理员</div>
                      </td>
                      <td><div className="actions"><button type="button" className="link-btn" onClick={() => openDetail(service)}><Eye size={14} />查看详情</button>{service.state === "starting" && <span className="field-hint">{startupStatusLabel(service)?.text}</span>}{service.state === "running" && <button type="button" className="link-btn" onClick={() => void serviceAction(service.id, "restart")}><RotateCcw size={14} />重启</button>}{service.state === "running" ? <button type="button" className="link-btn" onClick={() => void serviceAction(service.id, "stop")}><Square size={14} />停止</button> : !activeServiceStates.has(service.state) && <button type="button" className="link-btn" onClick={() => void serviceAction(service.id, "start")}><Play size={14} />启动</button>}{!activeServiceStates.has(service.state) && <button type="button" className="link-btn danger" title="删除推理服务" aria-label={`删除推理服务 ${service.name}`} onClick={() => setDeleteTarget(service)}><Trash2 size={14} />删除</button>}</div></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {!visibleServices.length && <EmptyState kind={loading ? "loading" : services.length ? "filtered" : "empty"} title={loading ? "正在读取文生图推理服务" : services.length ? "没有匹配的推理服务" : "还没有文生图推理服务"} description={loading ? "正在同步服务状态。" : services.length ? "清除搜索后可以查看全部服务。" : "先选择 SDXL 基模或训练 Adapter，保存配置后即可进入生成与验证评测。"} action={!loading && (services.length ? <button type="button" className="link-btn empty-action" onClick={() => setQuery("")}>清除搜索</button> : <button type="button" className="primary-btn empty-action" onClick={() => { setMessage(""); setPreflight(null); setView("create"); }}><Plus size={14} />创建推理服务</button>)} />}
          </div>
          <div className="pagination">
            <span>共 {visibleServices.length} 条服务</span>
            <span>第 1 / 1 页</span>
          </div>
        </div>
        {deleteDialog}
      </section>
    );

  if (view === "detail" && selectedService) {
    const config = selectedService.config || {};
    const resource = config.resource || {};
    const currentPreflight = preflight || config.preflightSnapshot;
    const state = preflightState(currentPreflight);
    return (
      <section className="content image-generation-service-page image-generation-service-detail-page">
        <div className="title-row">
          <div>
            <button
              type="button"
              className="back-button"
              onClick={() => {
                setMessage("");
                setView("list");
              }}
            >
              <ArrowLeft size={15} />
              返回推理服务
            </button>
            <h1>{selectedService.name}</h1>
            <div className="subtitle">
              {selectedService.id} · 保存配置不会创建实例，启动服务后才会产生费用
            </div>
          </div>
          {selectedService.state === "starting" && (() => {
            const status = startupStatusLabel(selectedService);
            if (!status) return null;
            const currentImage = specs?.images?.find((image) => image.id === resource.imageId);
            const recommendedImageId = specs?.recommendedImageId || "";
            const recommendedImageName = specs?.recommendedImageName || recommendedImageId;
            const planParts = [
              resource.gpuType ? `${resource.gpuType} × ${resource.gpuCount || 1} 卡` : "",
              resource.cpuCores ? `${resource.cpuCores} 核 / ${resource.memoryGb || "--"}GB 内存` : "",
              resource.region && resource.zone ? `${resource.region} / ${resource.zone}` : "",
              resource.instanceMode ? String(resource.instanceMode).toUpperCase() : "",
              resource.chargeType === "Postpay" ? "按量计费" : resource.chargeType || "",
              resource.price?.total != null ? `¥${resource.price.total}/小时` : "",
            ].filter(Boolean);
            return (
              <div className="notice" role="status">
                <strong>{status.text}</strong>
                <span style={{ display: "block", marginTop: 4 }}>
                  实例方案：{planParts.length > 0 ? planParts.join(" · ") : "未配置优云资源"}
                </span>
                <span style={{ display: "block", marginTop: 4 }}>
                  镜像：{resource.imageId || "--"}
                  {currentImage?.name ? `（${currentImage.name}）` : ""}
                  {resource.requiresDiffusersInstall ? " · 未预装 Diffusers，本次启动需远端安装依赖" : " · 预置 Diffusers 环境"}
                </span>
                {specs?.requiresDiffusersInstall ? (
                  <span style={{ display: "block", marginTop: 4 }}>
                    优云镜像目录当前没有预装 Diffusers 的镜像：启动时将在实例内在线安装 Diffusers 依赖（预计 20 分钟以上）。
                  </span>
                ) : (
                  recommendedImageId &&
                  recommendedImageId !== resource.imageId && (
                    <span style={{ display: "block", marginTop: 4 }}>
                      推荐镜像：{recommendedImageName}（{recommendedImageId}），改选预置 Diffusers 镜像可显著加快启动。
                    </span>
                  )
                )}
                <span style={{ display: "block", marginTop: 4 }}>
                  启动包含创建优云实例、安装运行环境、解析模型与上传 Adapter，可能需要较长时间；页面每 5 秒自动刷新状态，运行日志请查看「运行日志」页签。
                </span>
              </div>
            );
          })()}
          <div className="form-actions">{selectedService.state === "running" && <button type="button" className="secondary-btn icon-text-btn" disabled={deleting} onClick={() => void serviceAction(selectedService.id, "restart")}><RotateCcw size={15} />重启服务</button>}{selectedService.state === "running" && <button type="button" className="secondary-btn icon-text-btn" disabled={deleting} onClick={() => void serviceAction(selectedService.id, "stop")}><Square size={15} />停止服务</button>}{["created", "stopped", "failed"].includes(selectedService.state) && <button type="button" className="primary-btn icon-text-btn" disabled={deleting} onClick={() => void serviceAction(selectedService.id, "start")}><Play size={15} />启动服务</button>}{!activeServiceStates.has(selectedService.state) && <button type="button" className="danger-btn icon-text-btn" disabled={deleting} onClick={() => setDeleteTarget(selectedService)}><Trash2 size={15} />删除服务</button>}<button type="button" className="secondary-btn icon-text-btn" disabled={loading} onClick={() => void load()}><RefreshCw size={15} className={loading ? "spin" : ""} />刷新</button></div>
        </div>
        <div className="inference-meta-chips">
          <span className="inference-meta-chip">运行状态 <strong>{SERVICE_STATE_LABELS[selectedService.state] ?? selectedService.state}</strong></span>
          <button type="button" className="inference-meta-chip" title="复制服务 ID" onClick={() => { void navigator.clipboard?.writeText(selectedService.id); setMessage("服务 ID 已复制。"); }}>服务 ID <code>{selectedService.id}</code> <Copy size={12} /></button>
          <span className="inference-meta-chip">镜像 <code>{resource.imageId || "--"}</code></span>
          {resource.gpuType && <span className="inference-meta-chip">实例方案 <strong>{resource.gpuType} × {resource.gpuCount || 1}</strong></span>}
          <span className="inference-meta-chip">更新 <strong>{compactTime(selectedService.updatedAt || selectedService.createdAt)}</strong></span>
        </div>
        {message && (
          <div
            className={
              message.startsWith("配置已保存") ? "notice" : "form-error"
            }
          >
            {message}
          </div>
        )}
        <details className="inference-contract-fold">
          <summary><ChevronDown size={14} className="inference-contract-fold-chevron" /><strong>服务运行契约</strong><span>审计视图：模型血缘、资源清理与失败诊断字段，默认收起</span></summary>
          <InferenceServiceDetailSummary service={selectedService} modality="image-generation" />
        </details>
        <div
          className="image-generation-service-detail-tabs"
          role="tablist"
          aria-label="文生图服务详情"
        >
          <button
            type="button"
            role="tab"
            aria-selected={detailTab === "overview"}
            className={detailTab === "overview" ? "active" : ""}
            onClick={() => setDetailTab("overview")}
          >
            服务概览
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={detailTab === "generation"}
            className={detailTab === "generation" ? "active" : ""}
            onClick={() => setDetailTab("generation")}
          >
            图片生成
          </button>
          {evaluationEnabled && <button
            type="button"
            role="tab"
            aria-selected={detailTab === "evaluation"}
            className={detailTab === "evaluation" ? "active" : ""}
            onClick={() => setDetailTab("evaluation")}
          >
            验证评测
          </button>}
          <button
            type="button"
            role="tab"
            aria-selected={detailTab === "api"}
            className={detailTab === "api" ? "active" : ""}
            onClick={() => setDetailTab("api")}
          >
            API 接入
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={detailTab === "logs"}
            className={detailTab === "logs" ? "active" : ""}
            onClick={() => setDetailTab("logs")}
          >
            运行日志
          </button>
        </div>
        {detailTab === "overview" && (
          <>
            <div className="image-generation-service-detail-grid">
              <section className="panel">
                <div className="section-heading">
                  <div>
                    <h2>服务配置</h2>
                  </div>
                  <span className={`status ${state.className}`}>
                    {state.label}
                  </span>
                </div>
                <div className="detail-grid">
                  <div>
                    <div className="detail-label">模型</div>
                    <div className="detail-value">
                      {config.modelRepoId || config.modelId || "--"}
                    </div>
                  </div>
                  <div>
                    <div className="detail-label">Adapter</div>
                    <div className="detail-value">
                      {config.adapterLineage?.taskId
                        ? `${config.adapterLineage.variant || "best"} · ${config.adapterLineage.taskId}`
                        : "基座模型"}
                    </div>
                  </div>
                  <div>
                    <div className="detail-label">生成尺寸</div>
                    <div className="detail-value">
                      {config.width || 1024} × {config.height || 1024}
                    </div>
                  </div>
                  <div>
                    <div className="detail-label">采样参数</div>
                    <div className="detail-value">
                      {config.steps || 30} steps · CFG {config.cfg ?? 7} ·{" "}
                      {config.sampler || "euler"}
                    </div>
                  </div>
                  <div>
                    <div className="detail-label">运行参数</div>
                    <div className="detail-value">
                      {String(config.precision || "auto").toUpperCase()} ·{" "}
                      {config.loadTimeout || 180}s
                    </div>
                    <small>
                      {[
                        config.enableCpuOffload && "CPU Offload",
                        config.enableAttentionSlicing && "Attention Slicing",
                        config.enableVaeTiling && "VAE Tiling",
                        config.enableSafetyChecker && "安全检查",
                      ]
                        .filter(Boolean)
                        .join(" · ") || "默认运行模式"}
                    </small>
                  </div>
                  <div>
                    <div className="detail-label">优云资源</div>
                    <div className="detail-value">
                      {resource.gpuType || "--"} × {resource.gpuCount || 1} ·{" "}
                      {resource.cpuCores || "--"} CPU ·{" "}
                      {resource.memoryGb || "--"}GB
                    </div>
                  </div>
                  <div>
                    <div className="detail-label">Diffusers 镜像</div>
                    <div className="detail-value image-generation-service-image-id">
                      {resource.imageId || "--"}
                    </div>
                  </div>
                </div>
              </section>
              <section className="panel">
                <div className="section-heading">
                  <div>
                    <h2>启动前检查</h2>
                  </div>
                  <button
                    type="button"
                    className="secondary-btn"
                    onClick={() => void runPreflight()}
                    disabled={saving}
                  >
                    重新检查
                  </button>
                </div>
                {currentPreflight ? (
                  <div
                    className={`vision-training-preflight ${currentPreflight.ready && !state.expired ? "ready" : "blocked"}`}
                  >
                    <div className="vision-preflight-header">
                      <div>
                        <strong>
                          {state.expired
                            ? "检查已过期"
                            : currentPreflight.ready
                              ? "启动前检查通过"
                              : "启动前检查未通过"}
                        </strong>
                        <p>
                          {state.expired
                            ? "请重新检查实时库存和价格。"
                            : currentPreflight.ready
                              ? "模型、Adapter、镜像、库存和价格均已校验。"
                              : "请处理阻塞项后重新检查。"}
                        </p>
                      </div>
                      <span>{compactTime(currentPreflight.checkedAt)}</span>
                    </div>
                    <div className="image-generation-preflight-grid">
                      {Object.entries(currentPreflight.checks || {}).map(
                        ([key, check]) => (
                          <div className="vision-preflight-item" key={key}>
                            <span>{key}</span>
                            <strong
                              className={
                                check.status === "blocked" ? "blocked" : "pass"
                              }
                            >
                              {check.message}
                            </strong>
                          </div>
                        ),
                      )}
                    </div>
                  </div>
                ) : (
                  <div className="empty-state">尚未执行启动前检查。</div>
                )}
              </section>
            </div>
          </>
        )}
        {detailTab === "generation" && (
          <ImageGenerationTasks
            serviceId={selectedService.id}
            serviceName={selectedService.name}
            serviceState={selectedService.state}
            defaultConfig={config as Record<string, unknown>}
            embedded
            onOpenServiceOverview={() => setDetailTab("overview")}
          />
        )}
        {detailTab === "evaluation" && (
          <ImageGenerationEvaluation
            serviceId={selectedService.id}
            serviceName={selectedService.name}
            defaultConfig={config as Record<string, unknown>}
          />
        )}
        {detailTab === "api" && (
          <div className="inference-api-layout image-generation-service-api-layout">
            <section className="panel inference-api-main">
              <div className="section-heading"><div><h2>对外文生图 API</h2></div><Copy size={20} color="#3538cd" /></div>
              <div className="inference-endpoint"><span>POST</span><code>{apiUrl(`/public/image-generation/services/${selectedService.id}/generate`)}</code><button type="button" onClick={() => { void navigator.clipboard?.writeText(apiUrl(`/public/image-generation/services/${selectedService.id}/generate`)); setMessage("接口地址已复制。"); }} title="复制接口地址" aria-label="复制接口地址"><Copy size={14} /></button></div>
              <pre className="inference-code">{JSON.stringify({ prompt: "a product photo of a ceramic mug", negativePrompt: "blurry, watermark", width: 1024, height: 1024, steps: 30, cfg: 7, seed: 42 }, null, 2)}</pre>
              <div className="inference-code-head"><strong>Header</strong><span>Authorization: Bearer &lt;API_KEY&gt;</span></div>
              <div className="inference-summary-actions"><button type="button" className="primary-btn" disabled={apiKeyBusy} onClick={() => void issueApiKey()}>{apiKeyStatus?.hasApiKey ? "轮换 API Key" : "生成 API Key"}</button><button type="button" className="secondary-btn" disabled={apiKeyBusy || !apiKeyStatus?.hasApiKey} onClick={() => void revokeApiKey()}>撤销</button></div>
              {issuedApiKey && <div className="issued-api-key"><strong>本次生成的 API Key</strong><code>{issuedApiKey}</code><button type="button" className="secondary-btn compact-btn" onClick={() => { void navigator.clipboard?.writeText(issuedApiKey); setMessage("API Key 已复制。"); }}><Copy size={14} />复制</button><small>离开页面后不会再次显示明文。</small></div>}
            </section>
            <aside className="panel inference-api-side"><div className="section-heading"><div><h2>接入状态</h2></div></div><div className="inference-summary-list"><div><span>服务 ID</span><strong>{selectedService.id}</strong></div><div><span>API Key</span><strong>{apiKeyStatus?.hasApiKey ? `已配置 · ${apiKeyStatus.createdAt || ""}` : "未配置"}</strong></div><div><span>工作负载</span><strong>image_generation</strong></div></div></aside>
          </div>
        )}
        {detailTab === "logs" && (
          <section className="panel image-generation-service-history">
            <div className="section-heading">
              <div>
                <h2>服务日志</h2>
              </div>
              <span>
                {selectedService.state === "starting"
                  ? "启动中 · 每 5 秒自动刷新"
                  : serviceLogs.length > 0
                    ? `${serviceLogs.length} 条`
                    : ""}
              </span>
            </div>
            {serviceLogs.length === 0 ? (
              <div className="empty-state">暂无服务日志。</div>
            ) : (
              <div className="image-generation-service-log-lines">
                {serviceLogs.map((line, index) => (
                  <code key={`${index}-${line}`}>{line}</code>
                ))}
              </div>
            )}
            <div className="section-heading" style={{ marginTop: 28 }}>
              <div>
                <h2>运行日志</h2>
              </div>
              <span>
                {historyLoading ? "读取中" : `${history.length} 个任务`}
              </span>
            </div>
            {history.length === 0 ? (
              <div className="empty-state">暂无运行日志。</div>
            ) : (
              <div className="image-generation-history-list">
                {history.map((item) => (
                  <article
                    key={item.taskId}
                    className="image-generation-history-item"
                  >
                    <div className="history-item-head">
                      <strong>{item.taskName || item.taskId}</strong>
                      <span>{item.state}{item.failureCode ? ` · ${item.failureCode}` : ""}</span>
                    </div>
                    <p>{item.prompt}</p>
                    {item.failureMessage && <p className="diagnostic-failure">{item.failureMessage}</p>}
                    {item.suggestedAction && <small>建议：{item.suggestedAction}</small>}
                    {item.cleanupState && <small>实例清理：{item.cleanupState}{item.cleanupError ? ` · ${item.cleanupError}` : ""}</small>}
                    {item.lastHeartbeatAt && <small>最后进展：{compactTime(item.lastHeartbeatAt)}</small>}
                    <div className="image-generation-service-log-lines">
                      {(item.logs || []).slice(-80).map((line, index) => <code key={`${item.taskId}-${index}-${line}`}>{line}</code>)}
                    </div>
                    {!item.logs?.length && <small>{compactTime(item.updatedAt || item.createdAt)} · 暂无详细日志</small>}
                  </article>
                ))}
              </div>
            )}
          </section>
        )}
        {deleteDialog}
      </section>
    );
  }

  const navItems = [
    {
      id: "service-basic",
      index: "01",
      title: "服务基本信息",
      summary: name.trim() || "待填写",
    },
    {
      id: "service-model",
      index: "02",
      title: "模型与 Adapter",
      summary: selectedModel?.label || "待选择",
    },
    {
      id: "service-resource",
      index: "03",
      title: "优云运行环境",
      summary: specs
        ? `${gpuType || specs.gpuType} · ${cpuCores || "--"} CPU`
        : "待配置",
    },
    {
      id: "service-parameters",
      index: "04",
      title: "默认生成参数",
      summary: `${width}×${height} · ${steps} steps`,
    },
    {
      id: "service-runtime",
      index: "05",
      title: "运行参数",
      summary: `${precision.toUpperCase()} · ${loadTimeout}s`,
    },
    {
      id: "service-preflight",
      index: "06",
      title: "启动前检查",
      summary: "保存后执行",
    },
  ];
  const allYouyunZones = specs?.zones ?? [];
  const cooledYouyunZoneCount = allYouyunZones.filter((zone) => zone.healthStatus === "cooldown").length;
  const healthyPodZoneCount = healthyYouyunZones.filter((zone) => youyunZoneModes(zone).includes("pod")).length;
  const healthyUHostZoneCount = healthyYouyunZones.filter((zone) => youyunZoneModes(zone).includes("uhost")).length;
  return (
    <section className="content image-generation-service-page image-generation-service-create-page">
      <div className="title-row">
        <div>
          <button
            type="button"
            className="back-button"
            onClick={() => {
              setMessage("");
              setView("list");
            }}
          >
            <ArrowLeft size={15} />
            返回推理服务
          </button>
          <h1>创建文生图推理服务</h1>
          <div className="subtitle">
            选择 SDXL 基座模型或训练完成 Adapter，配置优云运行环境和默认生成参数
          </div>
        </div>
        <button
          type="button"
          className="secondary-btn icon-text-btn"
          onClick={() => void load()}
          disabled={loading}
        >
          <RefreshCw size={15} className={loading ? "spin" : ""} />
          刷新全部
        </button>
      </div>
      <div className="image-generation-service-create-layout">
        <div className="tuning-form-stack">
          <section className="panel tuning-section" id="service-basic">
            <div className="section-heading">
              <div>
                <h2>服务基本信息</h2>
              </div>
              <span>01</span>
            </div>
            <div className="field">
              <label className="field-label">
                服务名称 <span className="required">*</span>
              </label>
              <input
                className="input"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="例如：商品海报 SDXL 推理"
              />
            </div>
            <div className="field">
              <label className="field-label">执行环境</label>
              <select className="input" value="youyun" disabled>
                <option value="youyun">优云远程 GPU（当前可用）</option>
                <option value="local">本地（当前硬件不可用）</option>
              </select>
            </div>
            <div className="field">
              <label className="field-label">服务描述</label>
              <textarea
                className="input"
                rows={2}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="描述用途、模型版本或业务场景（可选）"
              />
            </div>
          </section>
          <section className="panel tuning-section" id="service-model">
            <div className="section-heading">
              <div>
                <h2>模型与 Adapter</h2>
              </div>
              <span>02</span>
            </div>
            <div className="field">
              <label className="field-label">
                基座模型 / 训练 Adapter <span className="required">*</span>
              </label>
              <select
                className="input"
                value={modelId}
                onChange={(event) => {
                  const next = models.find(
                    (item) => item.id === event.target.value,
                  );
                  setModelId(event.target.value);
                  if (
                    next &&
                    !next.supportedTrainingModes.includes(trainingMode)
                  )
                    setTrainingMode("lora");
                }}
              >
                <option value="">请选择文生图模型</option>
                <optgroup label="训练完成 Adapter">
                  {models
                    .filter((item) => item.group === "trained")
                    .map((item) => (
                      <option
                        key={item.id}
                        value={item.id}
                        disabled={!item.readyForYouyun}
                      >
                        {item.label}
                        {item.readyForYouyun
                          ? ""
                          : `（${item.reason || "不可部署"}）`}
                      </option>
                    ))}
                </optgroup>
                <optgroup label="基座模型">
                  {models
                    .filter((item) => item.group === "base")
                    .map((item) => (
                      <option
                        key={item.id}
                        value={item.id}
                        disabled={!item.readyForYouyun}
                      >
                        {item.label}
                        {item.readyForYouyun
                          ? ""
                          : `（${item.reason || "不可部署"}）`}
                      </option>
                    ))}
                </optgroup>
              </select>
            </div>
            {selectedModel && (
              <div className="image-generation-selected-model">
                <div>
                  <span>
                    {selectedModel.group === "trained"
                      ? "训练 Adapter"
                      : "基座模型"}
                  </span>
                  <strong>{selectedModel.label}</strong>
                  <code>{selectedModel.repoId}</code>
                </div>
                <div>
                  {selectedModel.taskId && (
                    <small>
                      任务 {selectedModel.taskId} ·{" "}
                      {selectedModel.adapterVariant === "best"
                        ? "Best Adapter"
                        : "Final Adapter"}
                    </small>
                  )}
                  <b>
                    {selectedModel.readyForYouyun ? "优云可用" : "不可部署"}
                  </b>
                </div>
              </div>
            )}
            <div className="field">
              <label className="field-label">加载方式</label>
              <div className="segmented tuning-mode-segmented">
                <button
                  type="button"
                  className={trainingMode === "lora" ? "selected" : ""}
                  onClick={() => setTrainingMode("lora")}
                >
                  <strong>LoRA</strong>
                  <small>16-bit 基座加载</small>
                </button>
                <button
                  type="button"
                  className={trainingMode === "qlora" ? "selected" : ""}
                  disabled={
                    !selectedModel?.supportedTrainingModes.includes("qlora")
                  }
                  onClick={() => setTrainingMode("qlora")}
                >
                  <strong>QLoRA</strong>
                  <small>需兼容性探针通过</small>
                </button>
              </div>
            </div>
          </section>
          <section className="panel tuning-section" id="service-resource">
            <div className="section-heading">
              <div>
                <h2>优云运行环境</h2>
              </div>
              <button
                type="button"
                className="secondary-btn icon-text-btn"
                disabled={resourceLoading}
                onClick={() => void refreshResources()}
              >
                <RefreshCw
                  size={14}
                  className={resourceLoading ? "spin" : ""}
                />
                刷新资源
              </button>
            </div>
            {specs && (
              <div className="youyun-specs-panel youyun-specs-panel-v2">
                <div className="youyun-constraint-callout">
                  <strong>当前仅支持单卡运行</strong>
                  <span>保存配置不会创建实例；启动时按当前 GPU、镜像与规格创建按量实例并实时校验库存与价格，优先使用预置 Diffusers 镜像。</span>
                </div>
                <div className="youyun-field">
                  <span className="youyun-field-label">GPU 型号 · 单卡</span>
                  <div className="youyun-machine-list">
                    {Array.from(new Map((specs.machineTypes && specs.machineTypes.length ? specs.machineTypes : [{ name: specs.gpuType }]).map((machine) => [`${machine.name}-${machine.graphicsMemoryGb ?? ""}`, machine])).values()).map((machine) => (
                      <button
                        type="button"
                        key={`${machine.name}-${machine.graphicsMemoryGb ?? ""}`}
                        disabled={youyunMachineIsUnavailable({ soldOut: Boolean(machine.soldOut), inventoryStatus: machine.inventoryStatus })}
                        className={`youyun-machine-item${youyunMachineIsUnavailable({ soldOut: Boolean(machine.soldOut), inventoryStatus: machine.inventoryStatus }) ? " sold-out" : ""}${(gpuType || specs.gpuType) === machine.name ? " selected" : ""}`}
                        onClick={() => {
                          setGpuType(machine.name);
                          setCpuCores(0);
                          setMemoryGb(0);
                          void refreshResources(machine.name);
                        }}
                      >
                        <strong>{machine.graphicsMemoryGb ? `${machine.graphicsMemoryGb}G` : "GPU"} {machine.name}</strong>
                        <small>{machine.graphicsMemoryGb ? `${machine.graphicsMemoryGb}GB 显存 · ` : ""}单卡</small>
                        <YouyunInventoryIndicator machine={{ soldOut: Boolean(machine.soldOut), inventoryStatus: machine.inventoryStatus, inventoryMessage: machine.inventoryMessage }} />
                      </button>
                    ))}
                  </div>
                </div>
                <div className="youyun-divider" />
                <div className="youyun-field">
                  <span className="youyun-field-label">地域 / 可用区</span>
                  <small className="vision-field-help">{specs.instanceMode === "uhost" ? "已启用 UHost 兜底" : `自动推荐健康 Pod${healthyUHostZoneCount > 0 ? "，创建失败时先释放实例再切换健康 UHost" : ""}；当前 ${healthyYouyunZones.length} 个可用区（Pod ${healthyPodZoneCount} / UHost ${healthyUHostZoneCount}）`}{cooledYouyunZoneCount > 0 ? `，已过滤 ${cooledYouyunZoneCount} 个近期不可用可用区` : ""}。推荐镜像：{specs.recommendedImageName || "预置 Diffusers 镜像"}</small>
                  {specs.fallbackReason && <small className="vision-field-help">{specs.fallbackReason}</small>}
                  <YouyunZonePicker zones={healthyYouyunZones} selectedZone={selectedYouyunZone} onSelect={(zone) => { setSelectedYouyunRegion(zone.region); setSelectedYouyunZone(zone.zone); void refreshResources(gpuType, zone.region, zone.zone); }} />
                </div>
                <div className="youyun-divider" />
                <label className="youyun-image-field">
                  <span className="youyun-field-label">优云运行镜像</span>
                  <select
                    className="input"
                    value={imageId}
                    onChange={(event) => setImageId(event.target.value)}
                  >
                    {specs.images.map((image) => (
                      <option key={image.id} value={image.id}>
                        {image.name}
                        {image.id === specs.recommendedImageId ? " · 推荐" : ""}
                      </option>
                    ))}
                  </select>
                  <small className="vision-field-help">
                    {specs.requiresDiffusersInstall
                      ? "当前镜像未预装 Diffusers：每次启动都要在新实例里在线安装依赖，预计需要 20 分钟以上；建议改选预置 Diffusers 镜像。"
                      : "当前镜像包含预置 Diffusers 环境。"}
                  </small>
                  {specs.imageSelectionWarning && (
                    <small className="vision-field-help warning-text">
                      {specs.imageSelectionWarning} 资源筛选仍可继续；启动前请配置预置镜像或显式开启远端安装兜底。
                    </small>
                  )}
                </label>
                <div className="youyun-field">
                  <span className="youyun-field-label">单卡 CPU / 内存规格</span>
                  <div className="youyun-capacity-list youyun-single-capacity-list">
                    {singleCardCapacities.map((item) => (
                      <button
                        type="button"
                        key={`${item.cpu}-${item.memoryGb}`}
                        disabled={!item.available}
                        className={`youyun-capacity-item${cpuCores === item.cpu && memoryGb === item.memoryGb ? " selected" : ""}`}
                        onClick={() => {
                          setCpuCores(item.cpu);
                          setMemoryGb(item.memoryGb);
                        }}
                      >
                        <span>
                          {item.cpu} 核 / {item.memoryGb}GB 内存
                        </span>
                        <span className={item.available ? "tag-ready" : "tag-off"}>{item.available ? "可选" : "无库存"}</span>
                      </button>
                    ))}
                  </div>
                </div>
                <div className="youyun-price-summary">
                  <div>
                    <strong>预计按量费用</strong>
                    <span>
                      {resourceLoading
                        ? "查询中..."
                        : price?.price
                          ? `${price.price.currency === "CNY" ? "¥" : price.price.currency} ${price.price.total.toFixed(2)} / 小时`
                          : "价格待确认"}
                    </span>
                  </div>
                  <small>保存配置不会创建实例；启动时才创建按量实例，实际费用以优云账单为准。</small>
                </div>
              </div>
            )}
            {resourceError && <div className="form-error">{resourceError}</div>}
          </section>
          <section className="panel tuning-section" id="service-parameters">
            <div className="section-heading">
              <div>
                <h2>默认生成参数</h2>
              </div>
              <span>04</span>
            </div>
            <div className="image-generation-parameter-grid">
              <div className="parameter-field image-size-field grid-span-2">
                <span>输出尺寸 <em className="parameter-range-hint">SDXL 原生约 104 万像素</em></span>
                <div className="image-size-presets">
                  {SIZE_PRESETS.map((preset) => (
                    <button
                      type="button"
                      key={preset.key}
                      className={`image-size-preset${width === preset.w && height === preset.h ? " selected" : ""}`}
                      onClick={() => { setWidth(preset.w); setHeight(preset.h); }}
                    >
                      <strong>{preset.key}</strong>
                      <small>{preset.w}×{preset.h}</small>
                    </button>
                  ))}
                </div>
                <div className="image-size-inputs">
                  <ParameterInput label="宽度 px" value={width} min={256} max={2048} onChange={setWidth} />
                  <ParameterInput label="高度 px" value={height} min={256} max={2048} onChange={setHeight} />
                </div>
                <small className={`image-size-hint${width > 0 && height > 0 && width * height > 1250000 ? " over-budget" : ""}`}>
                  {width > 0 && height > 0
                    ? `总像素 ≈ ${Math.round((width * height) / 10000)} 万${width * height > 1250000 ? " · 明显高于 SDXL 原生预算，显存与耗时显著增加" : " · SDXL 原生约 104 万"}`
                    : "请输入宽高"}
                </small>
              </div>
              <div className="parameter-field sampler-field grid-span-2">
                <span>采样器</span>
                <div className="sampler-chips">
                  <button type="button" className={`sampler-chip${sampler === "euler" ? " selected" : ""}`} onClick={() => setSampler("euler")}>
                    <strong>Euler</strong>
                    <small>默认 · 速度快，多数场景够用</small>
                  </button>
                  <button type="button" className={`sampler-chip${sampler === "dpmpp_2m" ? " selected" : ""}`} onClick={() => setSampler("dpmpp_2m")}>
                    <strong>DPM++ 2M</strong>
                    <small>细节更稳 · 相同步数质量更好</small>
                  </button>
                </div>
              </div>
              <ParameterInput label="采样步数" value={steps} min={1} max={100} hintRange="推荐 20–50" softMin={20} softMax={50} onChange={setSteps} />
              <ParameterInput label="CFG" value={cfg} min={0} max={30} step={0.5} hintRange="推荐 4–9" softMin={4} softMax={9} onChange={setCfg} />
              <ParameterInput label="单次张数" value={batchSize} min={1} max={4} hintRange="1–4" onChange={setBatchSize} />
              <div className="parameter-field">
                <span>随机种子{seed >= 0 ? <button type="button" className="seed-random-btn" onClick={() => setSeed(-1)}>回到随机</button> : <em className="parameter-range-hint">-1 = 每次随机</em>}</span>
                <input
                  type="number"
                  className="input"
                  value={seed}
                  min={-1}
                  max={2147483647}
                  onChange={(event) => setSeed(Number(event.target.value))}
                />
                <small className="vision-field-help">{seed < 0 ? "填入正整数可固定种子复现结果。" : `已固定种子 ${seed}，相同提示词与参数可复现此图。`}</small>
              </div>
            </div>
            <div className="field">
              <label className="field-label">默认负面提示词</label>
              <textarea
                className="input"
                rows={2}
                value={negativePrompt}
                onChange={(event) => setNegativePrompt(event.target.value)}
                placeholder="例如：模糊、低质量、畸形手指"
              />
            </div>
          </section>
          <section className="panel tuning-section" id="service-runtime">
            <div className="section-heading">
              <div>
                <h2>运行参数</h2>
              </div>
              <span>05</span>
            </div>
            <div className="image-generation-create-grid">
              <label className="field">
                <span className="field-label">推理精度</span>
                <select
                  className="input"
                  value={precision}
                  onChange={(event) =>
                    setPrecision(event.target.value as "auto" | "fp16" | "bf16")
                  }
                >
                  <option value="auto">Auto（推荐）</option>
                  <option value="fp16">FP16</option>
                  <option value="bf16">BF16</option>
                </select>
              </label>
              <ParameterInput
                label="模型加载超时（秒）"
                value={loadTimeout}
                min={30}
                max={3600}
                onChange={setLoadTimeout}
              />
              <ParameterInput
                label="最大并发请求数"
                value={maxConcurrentRequests}
                min={1}
                max={16}
                onChange={setMaxConcurrentRequests}
              />
              <ParameterInput
                label="空闲自动释放（秒，0=关闭）"
                value={idleTimeoutSeconds}
                min={0}
                max={86400}
                onChange={setIdleTimeoutSeconds}
              />
              <ParameterInput
                label="单次请求超时（秒）"
                value={requestTimeoutSeconds}
                min={5}
                max={3600}
                onChange={setRequestTimeoutSeconds}
              />
            </div>
            <div className="image-generation-runtime-options">
              <label>
                <input
                  type="checkbox"
                  checked={enableCpuOffload}
                  onChange={(event) =>
                    setEnableCpuOffload(event.target.checked)
                  }
                />
                <span>CPU Offload</span>
                <small>降低显存占用，可能增加延迟</small>
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={enableAttentionSlicing}
                  onChange={(event) =>
                    setEnableAttentionSlicing(event.target.checked)
                  }
                />
                <span>Attention Slicing</span>
                <small>降低注意力计算峰值</small>
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={enableVaeTiling}
                  onChange={(event) => setEnableVaeTiling(event.target.checked)}
                />
                <span>VAE Tiling</span>
                <small>适合高分辨率图片</small>
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={enableSafetyChecker}
                  onChange={(event) =>
                    setEnableSafetyChecker(event.target.checked)
                  }
                />
                <span>安全检查器</span>
                <small>由模型管线提供时启用</small>
              </label>
            </div>
          </section>
          <section className="panel tuning-section" id="service-preflight">
            <div className="section-heading">
              <div>
                <h2>启动前检查</h2>
              </div>
              <span>06</span>
            </div>
            <div className="vision-training-preflight pending">
              <div className="vision-preflight-header">
                <div>
                  <strong>保存后执行检查</strong>
                  <p>将校验模型、Adapter、Diffusers 镜像、实时库存与价格。</p>
                </div>
                <span>不创建实例</span>
              </div>
            </div>
          </section>
          {message && <div className="form-error">{message}</div>}
          <div className="training-form-footer">
            <button
              type="button"
              className="secondary-btn"
              onClick={() => setView("list")}
            >
              取消
            </button>
            <button
              type="button"
              className="primary-btn"
              disabled={saving || loading || !selectedModel || !resourceReady}
              onClick={() => void save()}
            >
              <Cloud size={15} />
              {saving ? "保存中" : "保存服务配置"}
            </button>
          </div>
        </div>
        <aside
          className="panel training-summary"
          aria-label="文生图推理服务配置导航"
        >
          <div className="training-summary-head">
            <div>
              <div className="summary-eyebrow">IMAGE INFERENCE FLOW</div>
              <h2>服务创建摘要</h2>
            </div>
            <Cloud size={18} />
          </div>
          <nav className="training-summary-nav">
            {navItems.map((item) => (
              <button
                type="button"
                className="training-summary-item"
                key={item.id}
                onClick={() =>
                  document
                    .getElementById(item.id)
                    ?.scrollIntoView({ behavior: "smooth", block: "start" })
                }
              >
                <span className="training-summary-item-index">
                  {item.index}
                </span>
                <span className="training-summary-item-copy">
                  <strong>{item.title}</strong>
                  <small>{item.summary}</small>
                </span>
                <span
                  className="training-summary-item-arrow"
                  aria-hidden="true"
                >
                  ›
                </span>
              </button>
            ))}
          </nav>
          <div className="vision-summary-checks">
            <div className="vision-summary-checks-head">
              <strong>保存前检查</strong>
              <span>
                {resourceReady && selectedModel && name.trim()
                  ? "可保存"
                  : "待完善"}
              </span>
            </div>
            <div className="vision-summary-check">
              <span className={name.trim() ? "pass" : "blocked"} />
              服务名称<span>{name.trim() || "待填写"}</span>
            </div>
            <div className="vision-summary-check">
              <span
                className={selectedModel?.readyForYouyun ? "pass" : "blocked"}
              />
              模型与 Adapter<span>{selectedModel?.label || "待选择"}</span>
            </div>
            <div className="vision-summary-check">
              <span className={resourceReady ? "pass" : "blocked"} />
              优云资源
              <span>{resourceReady ? "库存与价格已确认" : "待确认"}</span>
            </div>
          </div>
          <div className="vision-summary-note">
            <strong>费用边界</strong>
            <p>
              读取库存、查询价格、保存服务和启动前检查都不会创建实例；只有在生成任务中确认启动后才会申请优云
              GPU。
            </p>
          </div>
        </aside>
      </div>
    </section>
  );
}
