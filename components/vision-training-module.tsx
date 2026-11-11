"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BackButton } from "./back-button";
import { apiUrl } from "../lib/api-config";
import { Dataset } from "../lib/types";
import { TrainingTaskDetailSummary } from "./training-task-detail";
import { EmptyState } from "./ui/empty-state";
import { PageHeader } from "./ui/page-header";
import { friendlyYouyunError, selectYouyunZone, youyunMachineIsUnavailable, youyunPreferredMode, youyunRegionLabel, type YouyunZone } from "../lib/youyun-location";
import { YouyunZonePicker } from "./youyun-zone-picker";
import { YouyunInventoryIndicator } from "./youyun-inventory-indicator";

type VisionModel = {
  id: string;
  name: string;
  repoId: string;
  revision: string;
  path: string;
  capabilities: string[];
  supported: boolean;
  probeStatus: string;
  probeMessage: string;
  availableOnYouyun?: boolean;
};

type VisionTaskState = "awaiting_confirmation" | "awaiting_executor" | "queued" | "preparing" | "running" | "stopping" | "collecting" | "configured" | "completed" | "stopped" | "failed";
type VisionTaskType = "SFT" | "DPO";

type VisionManifest = {
  totalSamples?: number;
  trainSamples?: number;
  validationSamples?: number;
  referencedImages?: number;
  template?: string;
  tuningMode?: string;
  taskType?: VisionTaskType;
  dpoBeta?: number | null;
};

type VisionPreflight = {
  ready: boolean;
  modelRepoId?: string | null;
  effectiveBatchSize: number;
  executionStatus: string;
  message: string;
  reasons: string[];
  gpu?: { available?: boolean; name?: string; memoryFreeMb?: number; memoryTotalMb?: number } | null;
};

type VisionRemoteProbeResult = {
  passed: boolean;
  blockingReason?: string | null;
  recommendedTuningMode?: "lora" | "qlora";
  estimatedVramGb?: number;
  cleanupState: "released" | "failed";
  cleanupError?: string | null;
  retryBlocked?: boolean;
};

type YouyunGpuSpecs = {
  instanceMode?: "pod" | "uhost" | "auto";
  fallback?: boolean;
  fallbackReason?: string;
  region: string;
  zone: string;
  gpuType: string;
  chargeType: string;
  imageId: string;
  recommendedImageId: string;
  recommendedImageName: string;
  recommendedCommunityImageId?: string;
  recommendedCommunityImageName?: string;
  communityImages?: { id?: string | null; name: string; versionName?: string; status: string; tags: string[]; description: string; available: boolean; source: string; verificationState?: "unverified" | "verified" | "failed"; verifiedAt?: string | null; verificationExpiresAt?: number | null; price?: unknown; supportedGpuTypes?: string[]; gpuSupported?: boolean; verificationMessage?: string | null; compatible?: boolean; selectable?: boolean; matchScore?: number; recommendationReason?: string }[];
  images: { id: string; name: string; type: string; status: string; tags: string[]; description: string }[];
  machineTypes: { name: string; zone: string; soldOut: boolean; description: string; graphicsMemoryGb: number | null; inventoryStatus?: "available" | "sold_out" | "unknown" | "error"; inventoryMessage?: string }[];
  capacity: { gpu: number; cpu: number; memoryGb: number; available: boolean }[];
  zones?: YouyunZone[];
};
// The API returns zones: { region, zone, is_pod } entries for the Pod/UHost selector.
// Backward-compatible default payload remains instanceMode: "pod" when no fallback is selected.
// zones: {} selectedYouyunRegion selectedYouyunZone className="youyun-region-list" selectYouyunZone
// region: selectedYouyunRegion, zone: selectedYouyunZone

type YouyunResource = {
  mode: "manual";
  instanceMode?: "pod" | "uhost";
  region: string;
  zone: string;
  gpuType: string;
  gpuCount: number;
  cpuCores: number;
  memoryGb: number;
  imageId: string;
  chargeType: string;
  price: { currency: string; instance: number; disks: number; systemDisks: number; image: number; total: number };
};

type VisionMetric = {
  step: number;
  epoch?: number;
  trainLoss?: number;
  evalLoss?: number;
  learningRate?: number;
};

type VisionArtifact = {
  type: string;
  name?: string;
  variant?: string;
  selected?: boolean;
  path?: string;
  checkpointStep?: number;
  trainLoss?: number;
  evalLoss?: number;
  metric?: string;
  sha256?: string;
};

type VisionTrainingTask = {
  id: string;
  name: string;
  taskType: string;
  state: VisionTaskState | string;
  status?: string;
  progress: number;
  currentStep: string;
  modelId: string;
  model: string;
  modelRepoId?: string;
  datasetId: string;
  dataset: string;
  method: string;
  tuningMode: "lora" | "qlora";
  provider: "local" | "youyun";
  outputName: string;
  owner: string;
  createdAt: string;
  updatedAt?: string;
  previewId?: string;
  configPath?: string;
  manifestPath?: string;
  manifest?: VisionManifest;
  yaml?: string;
  parameters?: Record<string, unknown>;
  preflight?: VisionPreflight;
  message?: string;
  clonedFrom?: string;
  logs?: string[];
  lossHistory?: VisionMetric[];
  artifacts?: VisionArtifact[];
  bestStep?: number;
  bestEvalLoss?: number;
  finalEvalLoss?: number;
  finalStep?: number;
  error?: string;
  stopReason?: string;
  confirmedAt?: string;
  resourceState?: string;
  cleanupState?: string;
  cleanupError?: string;
  cleanupCheckedAt?: string;
  failureCode?: string;
  phase?: string;
  failureMessage?: string;
  suggestedAction?: string;
  remoteHandle?: { instanceId?: string; state?: string } | null;
  instanceId?: string;
};

type TrainingView = "list" | "create" | "detail";
type StateFilter = "all" | "awaiting_confirmation" | "queued" | "running" | "completed" | "stopped" | "failed" | "awaiting_executor";
type VisionLogFilter = "all" | "errors" | "metrics";

type VisionTrainingForm = {
  name: string;
  outputName: string;
  datasetId: string;
  modelId: string;
  provider: "local" | "youyun";
  taskType: VisionTaskType;
  tuningMode: "lora" | "qlora";
  epochs: string;
  learningRate: string;
  batchSize: string;
  gradientAccumulation: string;
  validationSplit: string;
  imageMaxPixels: string;
  loraRank: string;
  loraAlpha: string;
  loraDropout: string;
  dpoBeta: string;
  gradientCheckpointing: boolean;
};

const defaultForm: VisionTrainingForm = {
  name: "",
  outputName: "",
  datasetId: "",
  modelId: "",
  provider: "youyun",
  taskType: "SFT",
  tuningMode: "qlora",
  epochs: "2",
  learningRate: "0.00002",
  batchSize: "1",
  gradientAccumulation: "8",
  validationSplit: "10",
  imageMaxPixels: "401408",
  loraRank: "64",
  loraAlpha: "128",
  loraDropout: "0.05",
  dpoBeta: "0.1",
  gradientCheckpointing: true,
};

const stateMeta: Record<string, { label: string; className: string }> = {
  awaiting_confirmation: { label: "待确认", className: "status-queued" },
  awaiting_executor: { label: "待确认", className: "status-queued" },
  queued: { label: "排队中", className: "status-queued" },
  preparing: { label: "准备中", className: "status-running" },
  running: { label: "训练中", className: "status-running" },
  stopping: { label: "停止中", className: "status-running" },
  collecting: { label: "收集产物", className: "status-running" },
  configured: { label: "已生成配置", className: "status-completed" },
  completed: { label: "已完成", className: "status-completed" },
  stopped: { label: "已停止", className: "status-stopped" },
  failed: { label: "失败", className: "status-failed" },
};

function isConfirmationPending(state: string) {
  return state === "awaiting_confirmation" || state === "awaiting_executor" || state === "configured";
}

async function visionRequest<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), {
    ...options,
    credentials: "include",
    cache: "no-store",
    headers: options?.body
      ? { "Content-Type": "application/json", ...(options.headers ?? {}) }
      : options?.headers,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail;
    const message = typeof detail === "string"
      ? detail
      : detail?.message ?? (detail?.reasons?.join(" ") || `请求失败（${response.status}）`);
    throw new Error(message);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function postJson<T>(path: string, body: Record<string, unknown>) {
  return visionRequest<T>(path, { method: "POST", body: JSON.stringify(body) });
}

function compactTime(value: string) {
  if (!value) return "—";
  return value.replace("T", " ").replace(/([+-]\d\d:\d\d|Z)$/, "");
}

function compactDate() {
  const now = new Date();
  return `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, "0")}${String(now.getDate()).padStart(2, "0")}`;
}

function modelShortName(value: string) {
  return value.includes("/") ? value.split("/").pop() ?? value : value;
}

function tuningLabel(value: VisionTrainingForm["tuningMode"]) {
  return value === "qlora" ? "QLoRA" : "LoRA";
}

function hasVisionTrainingCapability(model: VisionModel, tuningMode: VisionTrainingForm["tuningMode"]) {
  return model.capabilities.includes("vision_language")
    && model.capabilities.includes(`${tuningMode}_training`);
}

function isLocalProbePassed(model: VisionModel) {
  return model.supported && model.probeStatus === "passed";
}

function modelCanEnterTraining(
  model: VisionModel,
  provider: VisionTrainingForm["provider"],
  tuningMode: VisionTrainingForm["tuningMode"],
) {
  if (!hasVisionTrainingCapability(model, tuningMode)) return false;
  return isLocalProbePassed(model) || (provider === "youyun" && model.probeStatus === "candidate");
}

function taskTypeLabel(value: string | undefined): VisionTaskType {
  return value === "DPO" ? "DPO" : "SFT";
}

function parameterValue(task: VisionTrainingTask, key: string, fallback = "—") {
  const value = task.parameters?.[key];
  return value === undefined || value === null || value === "" ? fallback : String(value);
}

function resourceStateLabel(value: string | undefined) {
  return ({ none: "未占用", provisioning: "准备中", running: "占用中", stopping: "释放中", released: "已释放", cleanup_failed: "清理失败" } as Record<string, string>)[value ?? ""] ?? value ?? "未记录";
}

function cleanupStateLabel(value: string | undefined) {
  return ({ pending: "待核验", released: "已核验释放", failed: "核验失败" } as Record<string, string>)[value ?? ""] ?? value ?? "未记录";
}

function taskNeedsPolling(task: VisionTrainingTask) {
  const executionActive = ["queued", "preparing", "running", "stopping", "collecting"].includes(task.state);
  if (executionActive) return true;
  const cleanupPending = task.provider === "youyun"
    && task.resourceState !== "released"
    && (task.cleanupState === "releasing" || task.cleanupState === "failed" || task.resourceState === "cleanup_failed");
  return cleanupPending;
}

function taskStatusMessage(task: VisionTrainingTask) {
  if (task.state === "completed") return task.failureMessage || "训练已完成，Adapter 产物已就绪。";
  if (task.state === "failed") return task.failureMessage || task.error || "训练失败，请查看日志和失败阶段。";
  if (task.state === "stopped") return task.stopReason || "训练已停止，资源清理状态见下方。";
  return task.message || task.currentStep || "任务状态正在更新。";
}

const visionTaskStages = [
  { key: "preparing", label: "准备环境" },
  { key: "running", label: "训练中" },
  { key: "collecting", label: "收集产物" },
  { key: "completed", label: "已完成" },
] as const;

function logMatchesFilter(log: string, filter: VisionLogFilter) {
  if (filter === "errors") return /error|exception|failed|失败|错误/i.test(log);
  if (filter === "metrics") return /loss|eval_|train_|learning.?rate|step|epoch/i.test(log);
  return true;
}

function TaskStatus({ state }: { state: string }) {
  const meta = stateMeta[state] ?? { label: state, className: "status-stopped" };
  return <span className={`status ${meta.className}`}><span className="status-dot" />{meta.label}</span>;
}

function SuggestedText({ value, suggestion, onChange, placeholder }: { value: string; suggestion: string; onChange: (value: string) => void; placeholder?: string }) {
  return (
    <div className="suggested-name-input vision-suggested-input">
      <input className="input" value={value} onChange={(event) => onChange(event.target.value)} onKeyDown={(event) => { if (event.key === "Tab" && !value.trim() && suggestion) { event.preventDefault(); onChange(suggestion); } }} placeholder={placeholder ?? `例如：${suggestion}`} />
      {!value.trim() && suggestion && <span className="suggested-name-hint"><kbd>Tab</kbd> 应用建议</span>}
    </div>
  );
}

function ParameterInput({ label, value, onChange, step = "1", min, max }: { label: string; value: string; onChange: (value: string) => void; step?: string; min?: string; max?: string }) {
  return (
    <label className="parameter-field">
      <span>{label}</span>
      <input type="number" inputMode="decimal" step={step} min={min} max={max} value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function VisionLossChart({ metrics }: { metrics: VisionMetric[] }) {
  const trainPoints = metrics.filter((item) => typeof item.trainLoss === "number").map((item) => ({ step: item.step, value: item.trainLoss as number }));
  const evalPoints = metrics.filter((item) => typeof item.evalLoss === "number").map((item) => ({ step: item.step, value: item.evalLoss as number }));
  const allPoints = [...trainPoints, ...evalPoints];
  if (!allPoints.length) {
    return <section className="panel chart-card"><div className="panel-head"><div><h2>Loss 曲线</h2><p>训练日志产生指标后自动绘制</p></div></div><div className="loss-empty">暂无 Loss 记录</div></section>;
  }
  const width = 720;
  const height = 220;
  const plot = { left: 58, right: 700, top: 18, bottom: 180 };
  const steps = allPoints.map((item) => item.step);
  const values = allPoints.map((item) => item.value);
  const minStep = Math.min(...steps);
  const maxStep = Math.max(...steps);
  const rawMin = Math.min(...values);
  const rawMax = Math.max(...values);
  const padding = rawMin === rawMax ? Math.max(Math.abs(rawMin) * 0.08, 0.1) : (rawMax - rawMin) * 0.12;
  const minValue = rawMin - padding;
  const maxValue = rawMax + padding;
  const x = (step: number) => minStep === maxStep ? (plot.left + plot.right) / 2 : plot.left + ((step - minStep) / (maxStep - minStep)) * (plot.right - plot.left);
  const y = (value: number) => plot.bottom - ((value - minValue) / (maxValue - minValue)) * (plot.bottom - plot.top);
  const polyline = (points: Array<{ step: number; value: number }>) => points.map((point) => `${x(point.step)},${y(point.value)}`).join(" ");
  const yTicks = Array.from({ length: 5 }, (_, index) => maxValue - ((maxValue - minValue) * index) / 4);
  return <section className="panel chart-card"><div className="panel-head"><div><h2>Loss 曲线</h2><p>来自视觉 LLaMA-Factory Trainer 的真实日志指标</p></div><div className="chart-legends">{trainPoints.length > 0 && <span className="chart-legend"><i />训练 Loss</span>}{evalPoints.length > 0 && <span className="chart-legend eval"><i />验证 Loss</span>}</div></div><div className="loss-chart"><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="视觉训练 Loss 曲线">{yTicks.map((tick) => { const tickY = y(tick); return <g key={tick}><line x1={plot.left} x2={plot.right} y1={tickY} y2={tickY} /><text x={plot.left - 10} y={tickY + 4} textAnchor="end">{tick.toFixed(4)}</text></g>; })}{trainPoints.length > 1 && <polyline className="train-series" points={polyline(trainPoints)} />}{evalPoints.length > 1 && <polyline className="eval-series" points={polyline(evalPoints)} />}{trainPoints.map((point) => <circle className="train-point" key={`vision-train-${point.step}`} cx={x(point.step)} cy={y(point.value)} r="4"><title>Step {point.step} · 训练 Loss {point.value.toFixed(4)}</title></circle>)}{evalPoints.map((point) => <circle className="eval-point" key={`vision-eval-${point.step}`} cx={x(point.step)} cy={y(point.value)} r="4"><title>Step {point.step} · 验证 Loss {point.value.toFixed(4)}</title></circle>)}<text className="axis-label" x={x(minStep)} y={207} textAnchor={minStep === maxStep ? "middle" : "start"}>Step {minStep}</text>{minStep !== maxStep && <text className="axis-label" x={x(maxStep)} y={207} textAnchor="end">Step {maxStep}</text>}</svg></div>{allPoints.length === 1 && <div className="chart-note">当前仅有 1 个指标点，后续日志会自动形成曲线。</div>}</section>;
}

type VisionTrainingModuleProps = {
  datasets: Dataset[];
  onLoadDatasets?: () => Promise<void> | void;
  onOpenDatasets?: () => void;
};

export function VisionTrainingModule({ datasets, onLoadDatasets, onOpenDatasets }: VisionTrainingModuleProps) {
  const [view, setView] = useState<TrainingView>("list");
  const [tasks, setTasks] = useState<VisionTrainingTask[]>([]);
  const [models, setModels] = useState<VisionModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [backendState, setBackendState] = useState<"checking" | "online" | "offline">("checking");
  const [query, setQuery] = useState("");
  const [stateFilter, setStateFilter] = useState<StateFilter>("all");
  const [selectedId, setSelectedId] = useState("");
  const [detailTask, setDetailTask] = useState<VisionTrainingTask | null>(null);
  const [form, setForm] = useState<VisionTrainingForm>(defaultForm);
  const [preflight, setPreflight] = useState<VisionPreflight | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [showLeaveConfirm, setShowLeaveConfirm] = useState(false);
  const [showSubmitConfirm, setShowSubmitConfirm] = useState(false);
  const [showProbeConfirm, setShowProbeConfirm] = useState(false);
  const [probingRemote, setProbingRemote] = useState(false);
  const [remoteProbe, setRemoteProbe] = useState<VisionRemoteProbeResult | null>(null);
  const [logFilter, setLogFilter] = useState<VisionLogFilter>("all");
  const [autoScroll, setAutoScroll] = useState(true);
  const logRef = useRef<HTMLPreElement>(null);
  const [youyunSpecs, setYouyunSpecs] = useState<YouyunGpuSpecs | null>(null);
  const [youyunZones, setYouyunZones] = useState<YouyunZone[]>([]);
  const [youyunLoading, setYouyunLoading] = useState(false);
  const [youyunError, setYouyunError] = useState("");
  const [selectedYouyunGpuType, setSelectedYouyunGpuType] = useState("");
  const [selectedYouyunCpuCores, setSelectedYouyunCpuCores] = useState<number | null>(null);
  const [selectedYouyunMemoryGb, setSelectedYouyunMemoryGb] = useState<number | null>(null);
  const [selectedYouyunImageId, setSelectedYouyunImageId] = useState("");
  const [selectedYouyunRegion, setSelectedYouyunRegion] = useState("");
  const [selectedYouyunZone, setSelectedYouyunZone] = useState("");
  const [selectedYouyunInstanceMode, setSelectedYouyunInstanceMode] = useState<"pod" | "uhost">("pod");
  const [selectedYouyunChargeType, setSelectedYouyunChargeType] = useState<"Postpay" | "Spot">("Postpay");
  const [youyunResource, setYouyunResource] = useState<YouyunResource | null>(null);
  const [youyunPriceLoading, setYouyunPriceLoading] = useState(false);
  const [publishingDatasetId, setPublishingDatasetId] = useState("");
  const [datasetOverrides, setDatasetOverrides] = useState<Record<string, Dataset>>({});

  const effectiveDatasets = useMemo(
    () => datasets.map((dataset) => datasetOverrides[dataset.id] ?? dataset),
    [datasetOverrides, datasets],
  );
  const visualDatasets = useMemo(
    () => effectiveDatasets.filter((dataset) => dataset.type === "视觉理解" && dataset.structure === "IMAGE_TEXT" && dataset.state === "published"),
    [effectiveDatasets],
  );
  const readyVisualDatasets = useMemo(
    () => effectiveDatasets.filter((dataset) => dataset.type === "视觉理解" && dataset.structure === "IMAGE_TEXT" && dataset.state === "ready"),
    [effectiveDatasets],
  );
  const selectableModels = useMemo(
    () => models.filter((model) => modelCanEnterTraining(model, form.provider, form.tuningMode)),
    [form.provider, form.tuningMode, models],
  );
  const modelOptions = useMemo(
    () => models.filter((model) => hasVisionTrainingCapability(model, form.tuningMode)),
    [form.tuningMode, models],
  );
  const modelPickerModels = form.provider === "youyun" ? modelOptions : selectableModels;
  const selectedDataset = visualDatasets.find((dataset) => dataset.id === form.datasetId);
  const selectedModel = modelOptions.find((model) => model.id === form.modelId);
  const relevantYouyunImages = useMemo(() => {
    const images = youyunSpecs?.images ?? [];
    const filtered = images.filter((image) => image.type === "App" && /pytorch|torch|cuda|conda/i.test(image.name));
    const configured = images.find((image) => image.id === youyunSpecs?.imageId);
    return Array.from(new Map([...(configured ? [configured] : []), ...filtered].map((image) => [image.id, image])).values());
  }, [youyunSpecs]);
  const selectedCommunityImage = useMemo(
    () => youyunSpecs?.communityImages?.find((image) => image.id === selectedYouyunImageId),
    [selectedYouyunImageId, youyunSpecs],
  );
  const compatibleCommunityImages = useMemo(
    () => Array.from(new Map(
      (youyunSpecs?.communityImages ?? [])
        .filter((image) => image.compatible !== false || image.id === selectedYouyunImageId)
        .map((image) => [image.name.trim().toLocaleLowerCase(), image]),
    ).values()),
    [selectedYouyunImageId, youyunSpecs],
  );
  const youyunReady = form.provider !== "youyun" || Boolean(youyunResource && !youyunError && !youyunPriceLoading);
  const healthyYouyunZones = youyunZones.filter((zone) => zone.healthStatus !== "cooldown");
  const nameSuggestion = `${modelShortName(selectedModel?.repoId ?? selectedModel?.name ?? "Qwen2.5-VL")} ${selectedDataset?.name ?? "视觉数据"} ${form.taskType} ${tuningLabel(form.tuningMode)} 实验`;
  const outputSuggestion = `${modelShortName(selectedModel?.repoId ?? "vision-model").toLowerCase()}-${form.taskType.toLowerCase()}-${form.tuningMode}-${compactDate()}`;

  const visibleTasks = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return tasks.filter((task) => {
      const matchesState = stateFilter === "all"
        || (stateFilter === "running" ? ["queued", "preparing", "running", "stopping", "collecting"].includes(task.state) : task.state === stateFilter);
      const matchesQuery = !needle || [task.name, task.id, task.model, task.dataset, task.method].join(" ").toLowerCase().includes(needle);
      return matchesState && matchesQuery;
    });
  }, [query, stateFilter, tasks]);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const modelQuery = new URLSearchParams({ provider: form.provider });
      const [nextTasks, nextModels] = await Promise.all([
        visionRequest<VisionTrainingTask[]>("/vision/tasks"),
        visionRequest<VisionModel[]>(`/vision/models?${modelQuery.toString()}`),
      ]);
      setTasks(nextTasks);
      setModels(nextModels);
      setBackendState("online");
      setError("");
    } catch (reason) {
      setBackendState("offline");
      setError(reason instanceof Error ? reason.message : "读取视觉微调任务失败。");
    } finally {
      setLoading(false);
    }
  }, [form.provider]);

  useEffect(() => { void refresh(); }, [refresh]);

  const refreshYouyunSpecs = useCallback(async () => {
    setYouyunLoading(true);
    setYouyunError("");
    try {
      const query = new URLSearchParams({ instanceMode: selectedYouyunInstanceMode, chargeType: selectedYouyunChargeType });
      if (selectedYouyunRegion) query.set("region", selectedYouyunRegion);
      if (selectedYouyunZone) query.set("zone", selectedYouyunZone);
      if (selectedYouyunGpuType) query.set("gpuType", selectedYouyunGpuType);
      if (selectedYouyunImageId) query.set("imageId", selectedYouyunImageId);
      const suffix = query.size ? `?${query.toString()}` : "";
      const specs = await visionRequest<YouyunGpuSpecs>(`/youyun/gpu-specs${suffix}`);
      setYouyunSpecs(specs);
      setYouyunZones(specs.zones ?? []);
      setSelectedYouyunChargeType((current) => current || (specs.chargeType === "Spot" ? "Spot" : "Postpay"));
      setSelectedYouyunRegion((current) => current || specs.region);
      setSelectedYouyunZone((current) => current || specs.zone || selectYouyunZone(specs.zones ?? [], specs.region, selectedYouyunInstanceMode));
      setSelectedYouyunGpuType((current) => current || specs.gpuType);
      const imageId = selectedYouyunImageId || specs.recommendedImageId || specs.imageId;
      setSelectedYouyunImageId((current) => current || imageId);
      if (!selectedYouyunImageId && specs.recommendedImageId && specs.recommendedImageId !== specs.imageId) {
        setYouyunSpecs(null);
        setYouyunResource(null);
        return;
      }
      const selectedStillAvailable = specs.capacity.some(
        (item) => item.gpu === 1 && item.available && item.cpu === selectedYouyunCpuCores && item.memoryGb === selectedYouyunMemoryGb,
      );
      if (!selectedStillAvailable) {
        const first = specs.capacity.find((item) => item.gpu === 1 && item.available);
        setSelectedYouyunCpuCores(first?.cpu ?? null);
        setSelectedYouyunMemoryGb(first?.memoryGb ?? null);
      }
    } catch (reason) {
      setYouyunSpecs(null);
      setYouyunError(friendlyYouyunError(reason instanceof Error ? reason.message : "优云 GPU 库存查询失败。"));
    } finally {
      setYouyunLoading(false);
    }
  }, [selectedYouyunChargeType, selectedYouyunCpuCores, selectedYouyunGpuType, selectedYouyunImageId, selectedYouyunInstanceMode, selectedYouyunMemoryGb, selectedYouyunRegion, selectedYouyunZone]);

  useEffect(() => {
    if (view === "create" && form.provider === "youyun" && !youyunSpecs && !youyunLoading && !youyunError) {
      void refreshYouyunSpecs();
    }
  }, [form.provider, refreshYouyunSpecs, view, youyunError, youyunLoading, youyunSpecs]);

  useEffect(() => {
    if (view !== "create" || form.provider !== "youyun" || !youyunSpecs || !selectedYouyunCpuCores || !selectedYouyunMemoryGb) {
      setYouyunResource(null);
      return;
    }
    let cancelled = false;
    setYouyunPriceLoading(true);
    void postJson<YouyunResource>("/youyun/price", {
      mode: "manual",
      gpuType: selectedYouyunGpuType || youyunSpecs.gpuType,
      gpuCount: 1,
      cpuCores: selectedYouyunCpuCores,
      memoryGb: selectedYouyunMemoryGb,
      imageId: selectedYouyunImageId || youyunSpecs.recommendedImageId || youyunSpecs.imageId,
      instanceMode: selectedYouyunInstanceMode,
      chargeType: selectedYouyunChargeType,
      region: selectedYouyunRegion || youyunSpecs.region,
      zone: selectedYouyunZone || youyunSpecs.zone,
    }).then((resource) => {
      if (!cancelled) setYouyunResource(resource);
    }).catch((reason) => {
      if (!cancelled) {
        setYouyunResource(null);
        setYouyunError(reason instanceof Error ? reason.message : "优云价格查询失败。");
      }
    }).finally(() => {
      if (!cancelled) setYouyunPriceLoading(false);
    });
    return () => { cancelled = true; };
  }, [form.provider, selectedYouyunChargeType, selectedYouyunCpuCores, selectedYouyunGpuType, selectedYouyunImageId, selectedYouyunInstanceMode, selectedYouyunMemoryGb, selectedYouyunRegion, selectedYouyunZone, view, youyunSpecs]);

  useEffect(() => {
    const active = tasks.some(taskNeedsPolling);
    if (!active) return;
    const timer = window.setInterval(() => {
      void refresh();
      if (selectedId) {
        void visionRequest<VisionTrainingTask>(`/vision/tasks/${selectedId}`).then((detail) => {
          setDetailTask(detail);
          setTasks((items) => items.map((item) => item.id === detail.id ? detail : item));
        }).catch(() => undefined);
      }
    }, 2500);
    return () => window.clearInterval(timer);
  }, [tasks, refresh, selectedId]);

  useEffect(() => {
    setForm((current) => {
      const datasetId = visualDatasets.some((item) => item.id === current.datasetId) ? current.datasetId : (visualDatasets[0]?.id ?? "");
      const modelId = modelPickerModels.some((item) => item.id === current.modelId) ? current.modelId : (modelPickerModels[0]?.id ?? "");
      if (datasetId === current.datasetId && modelId === current.modelId) return current;
      return { ...current, datasetId, modelId };
    });
  }, [modelPickerModels, visualDatasets]);

  useEffect(() => {
    if (!autoScroll || view !== "detail" || !detailTask) return;
    const logNode = logRef.current;
    if (logNode) logNode.scrollTop = logNode.scrollHeight;
  }, [autoScroll, detailTask?.id, detailTask?.logs?.length, logFilter, view]);

  useEffect(() => {
    if (view !== "create" || !form.datasetId || !form.modelId || (form.provider === "youyun" && (!youyunReady || !youyunResource))) return;
    const timer = window.setTimeout(() => { void runAutomaticPreflight(); }, 350);
    return () => window.clearTimeout(timer);
  }, [form.datasetId, form.modelId, form.provider, form.taskType, form.tuningMode, form.batchSize, form.gradientAccumulation, form.validationSplit, form.imageMaxPixels, youyunReady, youyunResource, view]);

  function updateForm(values: Partial<VisionTrainingForm>) {
    setForm((current) => ({ ...current, ...values }));
    setPreflight(null);
    setError("");
  }

  async function publishAndSelectDataset(dataset: Dataset) {
    setPublishingDatasetId(dataset.id);
    setError("");
    setNotice("");
    try {
      const published = await visionRequest<Dataset>(`/datasets/${dataset.id}/publish`, { method: "POST" });
      setDatasetOverrides((current) => ({ ...current, [published.id]: published }));
      updateForm({ datasetId: published.id });
      await onLoadDatasets?.();
      setNotice(`数据集“${published.name}”已发布并选中。`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "发布视觉数据集失败。");
    } finally {
      setPublishingDatasetId("");
    }
  }

  function youyunSelectionPayload() {
    if (!youyunResource) return undefined;
    return {
      mode: "manual" as const,
      instanceMode: youyunResource.instanceMode === "uhost" ? "uhost" : "pod",
      region: selectedYouyunRegion || youyunResource.region,
      gpuType: youyunResource.gpuType,
      gpuCount: 1,
      cpuCores: youyunResource.cpuCores,
      memoryGb: youyunResource.memoryGb,
      zone: youyunResource.zone,
      imageId: youyunResource.imageId,
      chargeType: youyunResource.chargeType,
    };
  }

  function payload() {
    return {
      name: form.name.trim() || nameSuggestion,
      outputName: form.outputName.trim() || outputSuggestion,
      datasetId: form.datasetId,
      modelId: form.modelId,
      provider: form.provider,
      taskType: form.taskType,
      tuningMode: form.tuningMode,
      epochs: Number(form.epochs),
      learningRate: Number(form.learningRate),
      batchSize: Number(form.batchSize),
      gradientAccumulation: Number(form.gradientAccumulation),
      validationSplit: Number(form.validationSplit),
      imageMaxPixels: Number(form.imageMaxPixels),
      loraRank: Number(form.loraRank),
      loraAlpha: Number(form.loraAlpha),
      loraDropout: Number(form.loraDropout),
      dpoBeta: Number(form.dpoBeta),
      gradientCheckpointing: form.gradientCheckpointing,
      youyunResource: form.provider === "youyun" ? youyunSelectionPayload() : undefined,
    };
  }

  async function runPreflight(): Promise<VisionPreflight | null> {
    setError("");
    setNotice("");
    setPreflight(null);
    if (!youyunReady) {
      setError(youyunError || "请先完成优云 GPU、镜像和 CPU / 内存规格选择，等待价格查询完成后再执行前置检查。");
      return null;
    }
    if (!form.datasetId) { setError("请选择已发布的 IMAGE_TEXT 数据集。"); return null; }
    if (!form.modelId) {
      setError(form.provider === "youyun" ? "请选择可用于优云远程训练的视觉基座模型。" : "请选择已通过视觉探针的基座模型。");
      return null;
    }
    try {
      const result = await postJson<VisionPreflight>("/vision/tasks/preflight", {
        datasetId: form.datasetId,
        modelId: form.modelId,
        provider: form.provider,
        taskType: form.taskType,
        dpoBeta: Number(form.dpoBeta),
        tuningMode: form.tuningMode,
        batchSize: Number(form.batchSize),
        gradientAccumulation: Number(form.gradientAccumulation),
        validationSplit: Number(form.validationSplit),
        imageMaxPixels: Number(form.imageMaxPixels),
        youyunResource: form.provider === "youyun" ? youyunSelectionPayload() : undefined,
      });
      setPreflight(result);
      return result;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "视觉训练前置检查失败。");
      return null;
    }
  }

  async function runAutomaticPreflight() {
    if (!preflight || !preflight.ready) await runPreflight();
  }

  async function runRemoteProbe() {
    setShowProbeConfirm(false);
    if (!form.modelId || !youyunResource) return;
    setProbingRemote(true);
    setRemoteProbe(null);
    setError("");
    try {
      const result = await postJson<VisionRemoteProbeResult>(`/vision/models/${form.modelId}/remote-probe`, {
        modelPath: selectedModel?.path || "",
        tuningMode: form.tuningMode,
        youyunResource: youyunSelectionPayload(),
      });
      setRemoteProbe(result);
      if (result.cleanupState !== "released") {
        setError(`远端探针结束，但实例释放核验失败：${result.cleanupError || "请立即检查优云实例"}`);
      } else {
        setNotice(result.passed ? "远端兼容性探针通过，临时实例已释放。" : `远端探针未通过（${result.blockingReason || "未知阶段"}），临时实例已释放。`);
      }
      await refresh();
      setYouyunSpecs(null);
      setYouyunResource(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "优云远端兼容性探针失败。");
    } finally {
      setProbingRemote(false);
    }
  }

  async function requestSubmit() {
    if (!form.name.trim()) { setError("请填写任务名称。"); return; }
    if (!form.outputName.trim()) { setError("请填写 Adapter 输出名称。"); return; }
    const check = await runPreflight();
    if (!check?.ready) return;
    setShowSubmitConfirm(true);
  }

  async function submitTask() {
    setShowSubmitConfirm(false);
    if (!form.name.trim()) { setError("请填写任务名称。"); return; }
    if (!form.outputName.trim()) { setError("请填写 Adapter 输出名称。"); return; }
    setSubmitting(true);
    try {
      const check = await runPreflight();
      if (!check?.ready) return;
      const task = await postJson<VisionTrainingTask>("/vision/tasks", payload());
      setTasks((items) => [task, ...items.filter((item) => item.id !== task.id)]);
      setSelectedId(task.id);
      setDetailTask(task);
      setNotice(task.state === "queued" ? `视觉 ${form.taskType} 微调任务已确认并进入训练队列。` : `视觉 ${form.taskType} 微调任务已登记，配置已生成。`);
      setView("detail");
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "提交视觉微调任务失败。");
    } finally {
      setSubmitting(false);
    }
  }

  async function taskAction(task: VisionTrainingTask, action: "stop" | "retry" | "confirm") {
    setError("");
    try {
      const next = await visionRequest<VisionTrainingTask>(`/vision/tasks/${task.id}/${action}`, { method: "POST" });
      setTasks((items) => items.map((item) => item.id === next.id ? next : item));
      if (detailTask?.id === next.id) setDetailTask(next);
      setNotice(action === "stop" ? "已发送停止请求。" : action === "retry" ? "任务已重新进入训练队列。" : "任务已确认并进入训练队列。");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "视觉任务操作失败。");
    }
  }

  async function releaseTask(task: VisionTrainingTask) {
    setError("");
    try {
      await visionRequest<unknown>(`/vision/tasks/${task.id}/youyun/release`, { method: "POST" });
      const detail = await visionRequest<VisionTrainingTask>(`/vision/tasks/${task.id}`);
      setTasks((items) => items.map((item) => item.id === detail.id ? detail : item));
      if (detailTask?.id === detail.id) setDetailTask(detail);
      setNotice("已手动释放优云实例，资源核验状态已更新。");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "释放优云实例失败。");
    }
  }

  async function openArtifact(path: string) {
    try {
      const result = await visionRequest<{ message: string }>("/artifacts/open", {
        method: "POST",
        body: JSON.stringify({ path }),
      });
      setNotice(result.message);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法打开视觉训练产物目录。");
    }
  }

  async function openDetail(task: VisionTrainingTask) {
    setSelectedId(task.id);
    setDetailTask(task);
    setView("detail");
    try {
      const detail = await visionRequest<VisionTrainingTask>(`/vision/tasks/${task.id}`);
      setDetailTask(detail);
      setTasks((items) => items.map((item) => item.id === detail.id ? detail : item));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "读取视觉任务详情失败。");
    }
  }

  function closeDetail() {
    setView("list");
    setDetailTask(null);
    setSelectedId("");
    setError("");
  }

  async function cloneTask(task: VisionTrainingTask) {
    setError("");
    try {
      const clone = await visionRequest<VisionTrainingTask>(`/vision/tasks/${task.id}/clone`, { method: "POST" });
      setTasks((items) => [clone, ...items]);
      setNotice("任务副本已创建。");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "克隆视觉任务失败。");
    }
  }

  async function deleteTask(task: VisionTrainingTask) {
    if (!window.confirm(`确认删除视觉任务“${task.name}”吗？已生成的配置文件不会自动删除。`)) return;
    setError("");
    try {
      await visionRequest(`/vision/tasks/${task.id}`, { method: "DELETE" });
      setTasks((items) => items.filter((item) => item.id !== task.id));
      if (selectedId === task.id) closeDetail();
      setNotice("视觉任务已删除。");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "删除视觉任务失败。");
    }
  }

  function createFormDirty() {
    return Boolean(
      form.name.trim()
      || form.outputName.trim()
      || form.datasetId
      || form.modelId
      || form.taskType !== "SFT"
      || form.dpoBeta !== "0.1",
    );
  }

  function requestBack() {
    if (createFormDirty()) { setShowLeaveConfirm(true); return; }
    setView("list");
  }

  function jumpTo(id: string) {
    const target = document.getElementById(id);
    const container = document.querySelector<HTMLElement>(".main");
    if (!target || !container) return;
    const containerRect = container.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    container.scrollTo({ top: Math.max(0, container.scrollTop + targetRect.top - containerRect.top), behavior: "auto" });
  }

  const navItems = [
    { id: "vision-training-basic", index: "01", title: "基本信息", summary: `视觉理解 ${form.taskType} · ${tuningLabel(form.tuningMode)} · ${form.provider === "youyun" ? "远程优云 GPU" : "本地服务器"}` },
    { id: "vision-training-model-data", index: "02", title: "模型与数据", summary: `${selectedModel?.name ?? "视觉基座模型待填写"} · ${selectedDataset?.name ?? "视觉数据集待填写"}` },
    { id: "vision-training-parameters", index: "03", title: "训练参数", summary: `${form.epochs} Epochs · Batch ${form.batchSize} · 梯度累积 ${form.gradientAccumulation}` },
    { id: "vision-training-validation", index: "04", title: "验证与视觉设置", summary: `${form.validationSplit}% 验证 · ${form.imageMaxPixels} 最大像素` },
    { id: "vision-training-output", index: "05", title: "训练产出", summary: `${form.outputName.trim() || "Adapter 名称待填写"} · 视觉 Adapter` },
  ];

  if (view === "create") {
    return (
      <section className="content tuning-content vision-training-page">
        <div className="title-row">
          <div><BackButton label="返回微调任务" onClick={requestBack} /><h1>新建视觉微调任务</h1><div className="subtitle">创建视觉理解 IMAGE_TEXT {form.taskType} 微调任务</div></div>
          <div className="vision-create-state">服务状态 · {backendState === "online" ? "已连接" : backendState === "offline" ? "未连接" : "检查中"}</div>
        </div>
        <div className="tuning-create-layout">
          <div className="tuning-form-stack">
            <section id="vision-training-basic" className="panel tuning-section">
              <div className="section-heading"><div><h2>基本信息</h2><p>选择视觉理解的 SFT 指令微调或 DPO 偏好训练，训练方式支持 LoRA 与 QLoRA。</p></div><span className="section-index">01</span></div>
              <div className="field"><label className="field-label">任务名称 <span className="required">*</span></label><SuggestedText value={form.name} suggestion={nameSuggestion} onChange={(value) => updateForm({ name: value })} /></div>
              <div className="field"><label className="field-label">训练类型 <span className="required">*</span></label><div className="segmented tuning-mode-segmented"><button type="button" aria-pressed={form.taskType === "SFT"} className={form.taskType === "SFT" ? "selected" : ""} onClick={() => updateForm({ taskType: "SFT" })}><strong>SFT 指令微调</strong><small>messages 对话数据</small></button><button type="button" aria-pressed={form.taskType === "DPO"} className={form.taskType === "DPO" ? "selected" : ""} onClick={() => updateForm({ taskType: "DPO" })}><strong>DPO 偏好训练</strong><small>chosen / rejected 数据</small></button></div></div>
              <div className="field"><label className="field-label">执行环境</label><div className="segmented execution-mode-segmented"><button type="button" className={form.provider === "local" ? "selected" : ""} onClick={() => updateForm({ provider: "local" })}><strong>本地服务器</strong><small>提交前会校验本地 GPU</small></button><button type="button" className={form.provider === "youyun" ? "selected" : ""} onClick={() => updateForm({ provider: "youyun" })}><strong>远程优云 GPU</strong><small>提交后创建一次性训练实例</small></button></div></div>
              <div className="field"><label className="field-label">微调方式 <span className="required">*</span></label><div className="segmented tuning-mode-segmented"><button type="button" className={form.tuningMode === "lora" ? "selected" : ""} onClick={() => updateForm({ tuningMode: "lora" })}><strong>LoRA</strong><small>保留基模精度，训练 Adapter</small></button><button type="button" className={form.tuningMode === "qlora" ? "selected" : ""} onClick={() => updateForm({ tuningMode: "qlora" })}><strong>QLoRA</strong><small>4-bit 基座，降低显存占用</small></button></div></div>
              <div className="fixed-option-row"><div><span className="fixed-option-label">任务类型</span><strong>视觉理解 · {form.taskType}</strong></div><div><span className="fixed-option-label">数据格式</span><strong>IMAGE_TEXT</strong></div><div><span className="fixed-option-label">训练框架</span><strong>LLaMA-Factory</strong></div></div>
            </section>

            <section id="vision-training-model-data" className="panel tuning-section">
              <div className="section-heading"><div><h2>模型与数据</h2><p>{form.provider === "youyun" ? "远程优云训练可选择已通过探针或已识别 Repo ID 的视觉候选模型；" : "本地训练仅允许选择已通过视觉探针的模型；"}{form.taskType === "DPO" ? "DPO 记录还必须包含 chosen 与 rejected。" : "SFT 记录必须包含 answer。"}</p></div><span className="section-index">02</span></div>
              <div className="vision-create-grid">
                 <label className="field"><span className="field-label">视觉基座模型 <span className="required">*</span></span><select className="input" value={form.modelId} onChange={(event) => updateForm({ modelId: event.target.value })}><option value="">{form.provider === "youyun" ? "请选择优云公共模型库模型" : "请选择本地已通过探针的模型"}</option>{modelOptions.map((model) => { const canUse = modelCanEnterTraining(model, form.provider, form.tuningMode); const suffix = model.probeStatus === "candidate" ? " · 优云公共模型库" : " · 本地探针通过"; return <option key={model.id} value={model.id} disabled={form.provider === "local" && !canUse}>{model.name} · {model.repoId}{suffix}</option>; })}</select>{!modelPickerModels.length && <small className="vision-field-help">{form.provider === "youyun" ? "暂无可用于优云训练的公共模型，请先同步模型库。" : "暂无可用于本地训练的模型，请先补充服务器本地目录并通过探针。"}</small>}{selectedModel && <small className={`vision-field-help ${selectedModel.probeStatus === "candidate" ? "" : "success-text"}`}>{selectedModel.probeStatus === "candidate" ? "已登记为优云公共模型库模型 · 训练前会校验共享挂载路径。" : `本地探针通过 · ${selectedModel.capabilities.join(" / ") || "视觉能力已识别"}`}</small>}</label>
                <div className="field"><label className="field-label" htmlFor="vision-training-dataset">训练数据集 <span className="required">*</span></label><select id="vision-training-dataset" className="input" value={form.datasetId} onChange={(event) => updateForm({ datasetId: event.target.value })}><option value="">请选择已发布数据集</option>{visualDatasets.map((dataset) => <option key={dataset.id} value={dataset.id}>{dataset.name} · {dataset.count}</option>)}</select><div className="vision-dataset-controls"><button type="button" className="secondary-btn compact-btn" onClick={() => void onLoadDatasets?.()}>刷新数据集</button>{onOpenDatasets && <button type="button" className="link-btn" onClick={onOpenDatasets}>进入数据集管理</button>}</div>{!visualDatasets.length && !readyVisualDatasets.length && <small className="vision-field-help">暂无视觉理解数据集，请先上传、校验并发布 IMAGE_TEXT 数据集。</small>}{readyVisualDatasets.length > 0 && <div className="vision-ready-datasets"><strong>发现 {readyVisualDatasets.length} 个已校验、待发布的数据集</strong>{readyVisualDatasets.map((dataset) => <div className="vision-ready-dataset" key={dataset.id}><span><b>{dataset.name}</b><small>{dataset.count} · 发布后才能用于训练</small></span><button type="button" className="secondary-btn compact-btn" disabled={Boolean(publishingDatasetId)} onClick={() => void publishAndSelectDataset(dataset)}>{publishingDatasetId === dataset.id ? "发布中..." : "发布并选择"}</button></div>)}</div>}{selectedDataset && <small className="vision-field-help success-text">已选择 · 数据集版本 v{selectedDataset.version ?? 1} · {selectedDataset.validationMessage ?? "校验通过"}</small>}</div>
              </div>
              {form.provider === "youyun" && <div className="youyun-specs-panel vision-youyun-specs-panel youyun-specs-panel-v2">
                <div className="youyun-specs-head">
                  <div><strong>优云 GPU 配置、环境镜像与实时库存</strong><small>{youyunSpecs ? `${youyunSpecs.region} / ${youyunSpecs.zone} · ${youyunSpecs.chargeType === "Postpay" ? "按量计费" : youyunSpecs.chargeType}` : "正在查询可用资源..."}</small></div>
                  <button type="button" className="secondary-btn compact-btn" disabled={youyunLoading} onClick={() => { setYouyunSpecs(null); setYouyunError(""); setYouyunResource(null); }}>{youyunLoading ? "查询中..." : "刷新库存"}</button>
                </div>
                <div className="youyun-constraint-callout"><strong>当前仅支持单卡任务</strong><span>多卡并行暂未开放；训练优先复用镜像内兼容的 PyTorch 环境，缺少依赖时才补装；提交前会实时校验库存与价格。</span></div>
                {youyunError && <div className="youyun-specs-error">{youyunError}</div>}
                {!youyunSpecs && <div className="youyun-location-picker"><div className="youyun-field"><span className="youyun-field-label">运行模式（镜像兼容）</span><div className="segmented youyun-instance-mode"><button type="button" className={selectedYouyunInstanceMode === "pod" ? "selected" : ""} onClick={() => { setSelectedYouyunInstanceMode("pod"); setSelectedYouyunRegion(""); setSelectedYouyunZone(""); setYouyunResource(null); }}>优云 Pod</button><button type="button" className={selectedYouyunInstanceMode === "uhost" ? "selected" : ""} onClick={() => { setSelectedYouyunInstanceMode("uhost"); setSelectedYouyunRegion(""); setSelectedYouyunZone(""); setYouyunResource(null); }}>优云 UHost 兜底</button></div></div><YouyunZonePicker zones={healthyYouyunZones} selectedZone={selectedYouyunZone} onSelect={(zone) => { setSelectedYouyunInstanceMode(youyunPreferredMode(zone, undefined, selectedYouyunInstanceMode)); setSelectedYouyunRegion(zone.region); setSelectedYouyunZone(zone.zone); setYouyunError(""); setYouyunResource(null); }} /></div>}
                {youyunSpecs && <>
                  <div className="youyun-field-row">
                    <div className="youyun-field"><span className="youyun-field-label">计费类型</span><div className="segmented youyun-instance-mode"><button type="button" className={selectedYouyunChargeType === "Postpay" ? "selected" : ""} onClick={() => { setSelectedYouyunChargeType("Postpay"); setYouyunSpecs(null); setYouyunResource(null); }}>按量计费</button><button type="button" className={selectedYouyunChargeType === "Spot" ? "selected" : ""} onClick={() => { setSelectedYouyunChargeType("Spot"); setYouyunSpecs(null); setYouyunResource(null); }}>抢占式</button></div></div>
                    <div className="youyun-field"><span className="youyun-field-label">运行模式（镜像兼容）</span><div className="segmented youyun-instance-mode"><button type="button" className={selectedYouyunInstanceMode === "pod" ? "selected" : ""} onClick={() => { setSelectedYouyunInstanceMode("pod"); setSelectedYouyunRegion(""); setSelectedYouyunZone(""); setYouyunSpecs(null); setYouyunResource(null); }}>优云 Pod</button><button type="button" className={selectedYouyunInstanceMode === "uhost" ? "selected" : ""} onClick={() => { setSelectedYouyunInstanceMode("uhost"); setSelectedYouyunRegion(""); setSelectedYouyunZone(""); setYouyunSpecs(null); setYouyunResource(null); }}>优云 UHost 兜底</button></div></div>
                  </div>
                  <YouyunZonePicker zones={healthyYouyunZones} selectedZone={selectedYouyunZone} onSelect={(zone) => { setSelectedYouyunInstanceMode(youyunPreferredMode(zone, undefined, selectedYouyunInstanceMode)); setSelectedYouyunRegion(zone.region); setSelectedYouyunZone(zone.zone); setYouyunSpecs(null); setYouyunResource(null); setYouyunError(""); setSelectedYouyunCpuCores(null); setSelectedYouyunMemoryGb(null); }} />
                  <div className="youyun-divider" />
                  <div className="youyun-field"><span className="youyun-field-label">GPU 型号 · 单卡</span>
                  <div className="youyun-machine-list">
                    {Array.from(new Map(youyunSpecs.machineTypes.map((machine) => [`${machine.name}-${machine.graphicsMemoryGb}`, machine])).values()).map((machine) => (
                      <button type="button" key={`${machine.name}-${machine.graphicsMemoryGb}`} className={`youyun-machine-item${youyunMachineIsUnavailable(machine, "training") ? " sold-out" : ""}${selectedYouyunGpuType === machine.name ? " selected" : ""}`} disabled={youyunMachineIsUnavailable(machine, "training")} onClick={() => { setSelectedYouyunGpuType(machine.name); setSelectedYouyunCpuCores(null); setSelectedYouyunMemoryGb(null); setYouyunSpecs(null); setYouyunResource(null); setYouyunError(""); }}>
                        <strong>{machine.graphicsMemoryGb ? `${machine.graphicsMemoryGb}G` : "GPU"} {machine.name}</strong>
                        <small>{machine.graphicsMemoryGb ? `${machine.graphicsMemoryGb}GB 显存` : "显存待确认"}</small>
                        <YouyunInventoryIndicator machine={machine} />
                      </button>
                    ))}
                  </div>
                  </div>
                  <div className="youyun-divider" />
                  <label className="youyun-image-field"><span className="youyun-field-label">优云训练环境镜像</span><select className="input" value={selectedYouyunImageId || youyunSpecs.recommendedImageId || youyunSpecs.imageId} onChange={(event) => { setSelectedYouyunImageId(event.target.value); setSelectedYouyunCpuCores(null); setSelectedYouyunMemoryGb(null); setYouyunSpecs(null); setYouyunResource(null); setYouyunError(""); }}><optgroup label="优云实时可用镜像">{relevantYouyunImages.length ? relevantYouyunImages.map((image) => <option key={image.id} value={image.id}>{image.name || image.id}{image.id === youyunSpecs.recommendedImageId ? " · 视觉训练推荐" : image.id === youyunSpecs.imageId ? " · 当前配置" : ""}</option>) : <option value={youyunSpecs.imageId}>{youyunSpecs.imageId || "当前优云默认镜像"}</option>}</optgroup>{Boolean(compatibleCommunityImages.length) && <optgroup label="匹配的社区训练环境">{compatibleCommunityImages.map((image) => { const verified = image.verificationState === "verified"; const status = verified ? "已验证，可选择" : image.verificationState === "failed" ? "验证失败" : image.available ? "已匹配，待验证" : "当前区域无库存/未上架"; return <option key={`${image.id || "catalog"}:${image.name}:${image.versionName || ""}`} value={image.id || `catalog:${image.name}`} disabled={!image.available || !image.id || !verified || image.selectable === false}>{image.name}{image.versionName ? ` · ${image.versionName}` : ""} · {status}</option>; })}</optgroup>}</select><small><strong>推荐理由：</strong>{selectedCommunityImage?.recommendationReason || `${youyunSpecs.recommendedImageName || "当前平台镜像"} 已匹配视觉训练、PyTorch 版本和当前 GPU 库存。`} 无关的视频、数字人和文生图镜像已自动隐藏。</small></label>
                  {Boolean(compatibleCommunityImages.some((image) => image.id && image.available && image.verificationState !== "verified")) && <div className="vision-ready-datasets"><strong>发现匹配但待验证的社区环境</strong>{compatibleCommunityImages.filter((image) => image.id && image.available && image.verificationState !== "verified").slice(0, 6).map((image) => <div className="vision-ready-dataset" key={`probe:${image.id}`}><span><b>{image.name}{image.versionName ? ` · ${image.versionName}` : ""}</b><small>{image.recommendationReason || (image.supportedGpuTypes?.length ? `支持 ${image.supportedGpuTypes.join(" / ")}` : "支持当前 GPU")}</small></span><button type="button" className="secondary-btn compact-btn" disabled={probingRemote} onClick={() => { setSelectedYouyunImageId(image.id || ""); setSelectedYouyunCpuCores(null); setSelectedYouyunMemoryGb(null); setYouyunSpecs(null); setYouyunResource(null); setRemoteProbe(null); setYouyunError(""); }}>验证此环境</button></div>)}</div>}
                  <div className="youyun-divider" />
                  <div className="youyun-field"><span className="youyun-field-label">单卡规格 · CPU / 内存</span>
                  <div className="youyun-capacity-list youyun-single-capacity-list">
                    {youyunSpecs.capacity.filter((spec) => spec.gpu === 1).map((spec) => (
                      <button type="button" key={`${spec.gpu}-${spec.cpu}-${spec.memoryGb}`} disabled={!spec.available} className={`youyun-capacity-item${selectedYouyunCpuCores === spec.cpu && selectedYouyunMemoryGb === spec.memoryGb ? " selected" : ""}`} onClick={() => { setSelectedYouyunCpuCores(spec.cpu); setSelectedYouyunMemoryGb(spec.memoryGb); setYouyunResource(null); setYouyunError(""); }}>
                        <span>{spec.cpu} 核 / {spec.memoryGb}GB 内存</span>
                        <span className={spec.available ? "tag-ready" : "tag-off"}>{spec.available ? "可选" : "无库存"}</span>
                      </button>
                    ))}
                  </div>
                  </div>
                  {youyunSpecs.capacity.some((spec) => spec.gpu !== 1) && <div className="youyun-multi-capacity-list"><span className="youyun-capacity-label">多卡规格（暂不支持）</span>{youyunSpecs.capacity.filter((spec) => spec.gpu !== 1).map((spec) => <span className="youyun-capacity-disabled" key={`${spec.gpu}-${spec.cpu}-${spec.memoryGb}`}>{spec.gpu} 卡 / {spec.cpu} 核 / {spec.memoryGb}GB</span>)}</div>}
                  <div className="youyun-price-summary"><div><strong>预计按量费用</strong><span>{youyunPriceLoading ? "查询中..." : youyunResource ? `${youyunResource.price.currency === "CNY" ? "¥" : `${youyunResource.price.currency} `}${youyunResource.price.total.toFixed(2)} / 小时` : "暂不可获取"}</span></div>{youyunResource && <small>实例 ¥{youyunResource.price.instance.toFixed(2)} · 系统盘 ¥{youyunResource.price.systemDisks.toFixed(2)} · 数据盘 ¥{youyunResource.price.disks.toFixed(2)} · 镜像 ¥{youyunResource.price.image.toFixed(2)}；实际以优云账单为准</small>}</div>
                  {(selectedModel?.probeStatus !== "passed" || Boolean(selectedCommunityImage && selectedCommunityImage.verificationState !== "verified")) && <div className="vision-probe-action"><button type="button" className="secondary-btn" disabled={!youyunResource || probingRemote} onClick={() => setShowProbeConfirm(true)}>{probingRemote ? "远端探针执行中" : "创建临时实例并探针"}</button><small>{selectedCommunityImage ? `将验证社区镜像 ${selectedCommunityImage.name}${selectedCommunityImage.versionName ? ` · ${selectedCommunityImage.versionName}` : ""}；` : ""}探针结束后自动释放优云实例；释放失败将阻止再次创建。</small></div>}
                  {remoteProbe && <div className={`vision-training-preflight ${remoteProbe.passed && remoteProbe.cleanupState === "released" ? "ready" : "blocked"}`}><strong>{remoteProbe.passed ? "远端探针通过" : "远端探针未通过"}</strong><p>清理状态：{remoteProbe.cleanupState === "released" ? "实例已释放" : remoteProbe.cleanupError || "释放失败"}</p>{remoteProbe.estimatedVramGb && <p>建议显存：{remoteProbe.estimatedVramGb}GB+</p>}</div>}
                  {!youyunLoading && !youyunSpecs.capacity.some((spec) => spec.gpu === 1 && spec.available) && <div className="youyun-specs-error">当前{selectedYouyunInstanceMode === "pod" ? "Pod" : "UHost"} · {youyunRegionLabel(youyunSpecs.region)} · {youyunSpecs.gpuType} 没有可用的单卡 CPU / 内存组合，价格无法获取，提交已被门禁拦截。请切换地域或 GPU 后刷新库存。</div>}
                </>}
              </div>}
            </section>

            <section id="vision-training-parameters" className="panel tuning-section">
              <div className="section-heading"><div><h2>训练参数</h2><p>{form.taskType === "DPO" ? "DPO 额外提供偏好系数。" : "SFT 额外提供图片分辨率控制。"}</p></div><span className="section-index">03</span></div>
              <div className="parameter-grid vision-parameter-grid">
                <ParameterInput label="训练轮次 Epochs" value={form.epochs} onChange={(value) => updateForm({ epochs: value })} step="0.1" min="0.1" />
                <ParameterInput label="学习率" value={form.learningRate} onChange={(value) => updateForm({ learningRate: value })} step="0.000001" min="0" />
                <ParameterInput label="单卡 Batch Size" value={form.batchSize} onChange={(value) => updateForm({ batchSize: value })} min="1" />
                <ParameterInput label="梯度累积" value={form.gradientAccumulation} onChange={(value) => updateForm({ gradientAccumulation: value })} min="1" />
                <ParameterInput label="LoRA Rank" value={form.loraRank} onChange={(value) => updateForm({ loraRank: value })} min="1" max="256" />
                <ParameterInput label="LoRA Alpha" value={form.loraAlpha} onChange={(value) => updateForm({ loraAlpha: value })} min="1" max="512" />
                <ParameterInput label="LoRA Dropout" value={form.loraDropout} onChange={(value) => updateForm({ loraDropout: value })} step="0.01" min="0" max="0.99" />
                {form.taskType === "DPO" && <ParameterInput label="DPO Beta" value={form.dpoBeta} onChange={(value) => updateForm({ dpoBeta: value })} step="0.01" min="0.01" max="1" />}
              </div>
              <label className="toggle-row setting-toggle vision-setting-toggle"><span><strong>启用梯度检查点</strong><small>降低显存占用，适合视觉模型训练。</small></span><input type="checkbox" checked={form.gradientCheckpointing} onChange={(event) => updateForm({ gradientCheckpointing: event.target.checked })} /><i /></label>
            </section>

            <section id="vision-training-validation" className="panel tuning-section">
              <div className="section-heading"><div><h2>验证与视觉设置</h2><p>任务生成时按比例拆分验证集，原始数据集不会被修改；DPO 会保留 chosen / rejected 偏好对。</p></div><span className="section-index">04</span></div>
              <div className="validation-control"><div className="validation-control-head"><label className="field-label">验证集拆分比例</label><strong>{form.validationSplit}%</strong></div><input type="range" min="0" max="30" step="5" value={form.validationSplit} onChange={(event) => updateForm({ validationSplit: event.target.value })} /><div className="range-labels"><span>0%</span><span>30%</span></div><p>{Number(form.validationSplit) > 0 ? `将使用固定随机种子拆分 ${form.validationSplit}% 验证数据。` : "设置为 0% 表示只生成训练集，不执行验证。"}</p></div>
              <div className="parameter-grid vision-parameter-grid"><ParameterInput label="图片最大像素" value={form.imageMaxPixels} onChange={(value) => updateForm({ imageMaxPixels: value })} min="1024" /></div>
              <div className="hint vision-warning"><strong>执行说明</strong>提交前会再次执行数据、模型和 GPU 前检；确认后任务进入视觉训练队列，训练日志、Loss 和 Adapter 会在详情页持续更新。</div>
            </section>

            <section id="vision-training-output" className="panel tuning-section">
              <div className="section-heading"><div><h2>训练产出</h2><p>视觉 {form.taskType} 任务默认登记 LoRA Adapter，后续可接入模型管理和推理服务。</p></div><span className="section-index">05</span></div>
              <div className="field"><label className="field-label">Adapter 输出名称 <span className="required">*</span></label><SuggestedText value={form.outputName} suggestion={outputSuggestion} onChange={(value) => updateForm({ outputName: value })} /></div>
              <div className="fixed-option-row"><div><span className="fixed-option-label">默认产物</span><strong>视觉 {form.taskType} LoRA Adapter</strong></div><div><span className="fixed-option-label">配置目录</span><strong>任务提交后生成</strong></div><div><span className="fixed-option-label">合并模型</span><strong>暂不支持</strong></div></div>
            </section>

            <div className="vision-summary-note"><strong>自动校验</strong><p>{preflight ? (preflight.ready ? "当前配置已自动通过校验；正式提交前仍会再次复检。" : "自动校验发现阻塞项，请按下方提示调整。") : youyunReady ? "正在校验数据、模型、训练参数、镜像和库存。" : "资源配置就绪后自动执行，不创建优云实例。"}</p></div>
            {preflight && <div className={`vision-training-preflight ${preflight.ready ? "ready" : "blocked"}`}><div className="vision-preflight-header"><div><strong>{preflight.ready ? "前置检查通过" : "前置检查未通过"}</strong><p>{preflight.message}</p></div><span>有效 Batch Size：{preflight.effectiveBatchSize}</span></div><div className="vision-preflight-grid"><div className="vision-preflight-item"><span>数据集</span><strong className={form.datasetId ? "pass" : "blocked"}>{form.datasetId ? "已选择" : "待选择"}</strong></div><div className="vision-preflight-item"><span>视觉模型</span><strong className={form.modelId ? "pass" : "blocked"}>{form.modelId ? "已选择" : "待选择"}</strong></div><div className="vision-preflight-item"><span>训练参数</span><strong className={preflight.ready ? "pass" : "blocked"}>{preflight.ready ? "通过" : "需处理"}</strong></div><div className="vision-preflight-item"><span>{form.provider === "youyun" ? "优云资源" : "本地 GPU"}</span><strong className={youyunReady ? "pass" : "blocked"}>{youyunReady ? "已就绪" : "未就绪"}</strong></div></div>{!preflight.ready && <div className="vision-preflight-blockers"><strong>当前阻塞问题</strong>{preflight.reasons.length ? preflight.reasons.map((reason) => <p key={reason}>· {reason}</p>) : <p>· 请根据上方检查项补齐必填配置。</p>}</div>}</div>}
            {error && <div className="form-error">{error}</div>}
             <div className="training-form-footer"><button className="secondary-btn" disabled={submitting} onClick={requestBack}>取消</button><button className="secondary-btn" disabled={submitting || !form.datasetId || !form.modelId || !youyunReady} onClick={() => void runPreflight()}>执行前置检查</button><button type="button" className="primary-btn" data-testid="vision-confirm-submit-footer" disabled={submitting || backendState === "offline" || !modelOptions.length || !visualDatasets.length || !youyunReady} onClick={() => void requestSubmit()}>{submitting ? "正在提交训练" : "确认并提交训练"}</button></div>
          </div>
          <aside className="panel training-summary" aria-label="已填写内容导航">
            <div className="training-summary-head"><div><div className="summary-eyebrow">VISION TASK FLOW</div><h2>已填写内容</h2></div><BackButton className="training-summary-back" label="返回微调任务" onClick={requestBack} /></div>
            <nav className="training-summary-nav">{navItems.map((item) => <button type="button" className="training-summary-item" key={item.id} onClick={() => jumpTo(item.id)}><span className="training-summary-item-index">{item.index}</span><span className="training-summary-item-copy"><strong>{item.title}</strong><small>{item.summary}</small></span><span className="training-summary-item-arrow" aria-hidden="true">›</span></button>)}</nav>
            <div className="vision-summary-checks"><div className="vision-summary-checks-head"><strong>提交前检查</strong><span>{preflight ? (preflight.ready ? "已通过" : "有阻塞") : "未执行"}</span></div><div className="vision-summary-check"><span className={form.datasetId ? "pass" : "blocked"} />数据集<span>{selectedDataset?.name ?? "待选择"}</span></div><div className="vision-summary-check"><span className={form.modelId ? "pass" : "blocked"} />视觉模型<span>{selectedModel?.name ?? "待选择"}</span></div><div className="vision-summary-check"><span className={youyunReady ? "pass" : "blocked"} />{form.provider === "youyun" ? "优云资源" : "本地 GPU"}<span>{youyunReady ? "已就绪" : "待配置"}</span></div></div>
            <div className="vision-summary-note"><strong>提交后状态</strong><p>确认后任务会进入队列，状态、Loss、日志和 Adapter 产物在详情页自动刷新。</p></div>
          </aside>
        </div>
        {showLeaveConfirm && <div className="training-confirm-backdrop" role="presentation" onClick={() => setShowLeaveConfirm(false)}><div className="training-confirm-modal" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}><div className="training-confirm-icon">!</div><div><h2>确认返回微调任务？</h2><p>当前填写内容尚未提交，返回后将不会保存。</p></div><div className="training-confirm-actions"><button type="button" className="secondary-btn" onClick={() => setShowLeaveConfirm(false)}>继续填写</button><button type="button" className="primary-btn" onClick={() => { setShowLeaveConfirm(false); setView("list"); }}>确认返回</button></div></div></div>}
        {showProbeConfirm && youyunResource && <div className="training-confirm-backdrop" role="presentation" onClick={() => !probingRemote && setShowProbeConfirm(false)}><div className="training-confirm-modal vision-submit-confirm" role="dialog" aria-modal="true" aria-labelledby="vision-probe-title" onClick={(event) => event.stopPropagation()}><div className="training-confirm-icon">!</div><div><h2 id="vision-probe-title">确认创建临时探针实例？</h2><p>将按当前按量配置创建一台优云实例，只执行环境、Processor、LoRA 和最小前向探针，结束后自动释放。</p><div className="vision-confirm-summary"><span><b>模型</b>{selectedModel?.name ?? "—"}</span><span><b>镜像</b>{youyunResource.imageId}</span><span><b>资源</b>{youyunResource.gpuType} ×1 · {youyunResource.cpuCores} 核 / {youyunResource.memoryGb}GB</span><span><b>价格</b>¥{youyunResource.price.total.toFixed(2)} / 小时</span></div></div><div className="training-confirm-actions"><button type="button" className="secondary-btn" disabled={probingRemote} onClick={() => setShowProbeConfirm(false)}>取消</button><button type="button" className="primary-btn" disabled={probingRemote} onClick={() => void runRemoteProbe()}>{probingRemote ? "正在探针" : "确认并执行探针"}</button></div></div></div>}
        {showSubmitConfirm && <div className="training-confirm-backdrop" role="presentation" onClick={() => !submitting && setShowSubmitConfirm(false)}><div className="training-confirm-modal vision-submit-confirm" role="dialog" aria-modal="true" aria-labelledby="vision-submit-title" onClick={(event) => event.stopPropagation()}><div className="training-confirm-icon">✓</div><div><h2 id="vision-submit-title">确认提交视觉训练？</h2><p>确认后将使用当前配置进入 {form.provider === "youyun" ? "优云 GPU" : "本地 GPU"} 队列。</p><div className="vision-confirm-summary"><span><b>模型</b>{selectedModel?.name ?? "—"}</span><span><b>数据集</b>{selectedDataset?.name ?? "—"}</span><span><b>训练</b>视觉 {form.taskType} · {tuningLabel(form.tuningMode)}</span><span><b>验证</b>{form.validationSplit}% · 图片 {form.imageMaxPixels} px</span>{youyunResource && <span><b>优云资源</b>{youyunResource.gpuType} ×1 · {youyunResource.cpuCores} 核 / {youyunResource.memoryGb}GB · {youyunResource.imageId} · ¥{youyunResource.price.total.toFixed(2)}/小时</span>}</div></div><div className="training-confirm-actions"><button type="button" className="secondary-btn" disabled={submitting} onClick={() => setShowSubmitConfirm(false)}>返回修改</button><button type="button" className="primary-btn" data-testid="vision-confirm-start" disabled={submitting || !youyunReady} onClick={() => void submitTask()}>{submitting ? "正在提交" : "确认并开始训练"}</button></div></div></div>}
      </section>
    );
  }

  if (view === "detail" && detailTask) {
    const task = detailTask;
    const metrics = task.lossHistory ?? [];
    const evalMetrics = metrics.filter((item) => typeof item.evalLoss === "number");
    const bestEvalLoss = task.bestEvalLoss ?? (evalMetrics.length ? Math.min(...evalMetrics.map((item) => item.evalLoss as number)) : undefined);
    const finalEvalLoss = task.finalEvalLoss ?? (evalMetrics.length ? evalMetrics[evalMetrics.length - 1].evalLoss : undefined);
    const bestStep = task.bestStep ?? (evalMetrics.length ? evalMetrics.find((item) => item.evalLoss === bestEvalLoss)?.step : undefined);
    const finalStep = task.finalStep ?? (metrics.length ? metrics[metrics.length - 1].step : undefined);
    const logs = task.logs ?? [];
    const visibleLogs = logs.filter((log) => logMatchesFilter(log, logFilter));
    const activeStageIndex = task.state === "completed" ? visionTaskStages.length - 1 : task.state === "failed" || task.state === "stopped" ? 1 : Math.max(0, visionTaskStages.findIndex((stage) => stage.key === task.state));
    const canStop = ["queued", "preparing", "running", "stopping", "collecting"].includes(task.state);
    const canRetry = ["failed", "stopped"].includes(task.state);
    const canConfirm = isConfirmationPending(task.state);
    const canRelease = task.provider === "youyun"
      && (task.cleanupState === "pending" || task.cleanupState === "failed" || task.resourceState === "cleanup_failed");
    return (
      <section className="content tuning-content vision-training-page">
        <div className="vision-detail-head"><div><BackButton label="返回微调任务" onClick={closeDetail} /><div className="detail-title-line"><h1>{task.name}</h1><TaskStatus state={task.state} /></div><div className="subtitle">{task.id} · 创建于 {compactTime(task.createdAt)}</div></div><div className="form-actions">{canConfirm && <button className="primary-btn" onClick={() => void taskAction(task, "confirm")}>确认并提交训练</button>}{canStop && <button className="danger-btn" onClick={() => void taskAction(task, "stop")}>停止训练</button>}{canRetry && <button className="primary-btn" onClick={() => void taskAction(task, "retry")}>重新运行</button>}{canRelease && <button className="danger-btn" onClick={() => void releaseTask(task)}>释放实例</button>}<button className="secondary-btn" onClick={() => void cloneTask(task)}>克隆</button><button className="danger-btn" onClick={() => void deleteTask(task)}>删除</button></div></div>
        <div className={`vision-detail-notice ${task.state === "failed" ? "failed" : ""}`}><strong>视觉训练状态</strong><span>{taskStatusMessage(task)}</span>{task.phase && <small>失败阶段：{task.phase}</small>}{task.failureCode && <small>失败分类：{task.failureCode}</small>}{task.suggestedAction && <small>建议动作：{task.suggestedAction}</small>}</div>
        <TrainingTaskDetailSummary task={task} modality="vision" />
        <section className="vision-stage-timeline"><div className="panel-head"><div><span className="summary-eyebrow">TASK LIFECYCLE</span><h2>任务阶段</h2><p>根据执行器状态展示当前训练所处阶段。</p></div><TaskStatus state={task.state} /></div><div className="vision-stage-track">{visionTaskStages.map((stage, index) => { const status = index < activeStageIndex || task.state === "completed" ? "done" : index === activeStageIndex ? "active" : "pending"; return <div className={`vision-stage-step ${status}`} key={stage.key}><span className="vision-stage-marker">{status === "done" ? "✓" : String(index + 1).padStart(2, "0")}</span><div><strong>{stage.label}</strong><small>{status === "done" ? "已完成" : status === "active" ? task.currentStep : "等待"}</small></div></div>; })}</div></section>
        <div className="task-overview-grid vision-task-overview"><div className="panel progress-card"><div className="panel-head"><div><span className="summary-eyebrow">训练进度</span><h2>{task.currentStep}</h2></div><strong>{task.progress}%</strong></div><div className="large-progress"><span style={{ width: `${task.progress}%` }} /></div><div className="progress-meta"><span>{task.provider === "youyun" ? "远程优云视觉训练执行器" : "本地视觉 LLaMA-Factory 执行器"}</span><span>{task.state === "completed" ? "训练完成" : task.state === "failed" ? "需要处理" : "自动刷新"}</span></div>{task.error && <div className="form-error">{task.error}</div>}</div><div className="panel result-metric"><span>最佳 Step</span><strong>{bestStep ?? "--"}</strong><small>{bestStep ? "验证集最低 Loss" : "等待验证指标"}</small></div><div className="panel result-metric"><span>最佳验证 Loss</span><strong>{typeof bestEvalLoss === "number" ? bestEvalLoss.toFixed(4) : "--"}</strong><small>{evalMetrics.length ? "来自真实 Trainer 日志" : "尚未启用验证"}</small></div><div className="panel result-metric"><span>最终验证 Loss</span><strong>{typeof finalEvalLoss === "number" ? finalEvalLoss.toFixed(4) : "--"}</strong><small>{finalStep ? `最终 Step ${finalStep}` : "等待训练完成"}</small></div></div>
        <section className="panel youyun-task-resource vision-resource-card"><div><strong>{task.provider === "youyun" ? "远程优云 GPU" : "本地 GPU"}</strong><span>{task.provider === "youyun" ? "任务提交后由远程执行器分配资源" : "由服务器 GPU 探针和前置检查确认资源"}</span></div><div><strong>资源状态</strong><span>{resourceStateLabel(task.resourceState)}{task.remoteHandle?.instanceId || task.instanceId ? ` · 实例 ${task.remoteHandle?.instanceId || task.instanceId}` : ""}</span></div><div><strong>清理状态</strong><span>{cleanupStateLabel(task.cleanupState)}{task.cleanupError ? ` · ${task.cleanupError}` : task.cleanupCheckedAt ? ` · ${compactTime(task.cleanupCheckedAt)}` : ""}</span></div><div><strong>数据物化</strong><span>{task.manifest?.trainSamples ?? 0} 训练 · {task.manifest?.validationSamples ?? 0} 验证 · {task.manifest?.referencedImages ?? 0} 图片</span></div></section>
        <div className="detail-columns"><div className="detail-main-column"><VisionLossChart metrics={metrics} /><section className="panel logs-card"><div className="panel-head"><div><h2>运行日志</h2><p>原始执行器日志与训练指标分开展示，便于定位问题。</p></div><span className="logs-count">{visibleLogs.length} / {logs.length} 行</span></div><div className="vision-log-controls"><div className="vision-log-filter" role="group" aria-label="日志筛选"><button type="button" className={logFilter === "all" ? "active" : ""} onClick={() => setLogFilter("all")}>全部</button><button type="button" className={logFilter === "errors" ? "active" : ""} onClick={() => setLogFilter("errors")}>只看错误</button><button type="button" className={logFilter === "metrics" ? "active" : ""} onClick={() => setLogFilter("metrics")}>只看训练指标</button></div><label className="vision-log-autoscroll"><input type="checkbox" checked={autoScroll} onChange={(event) => setAutoScroll(event.target.checked)} />自动滚动</label><button type="button" className="compact-btn secondary-btn" disabled={!logs.length} onClick={() => { if (!navigator.clipboard) { setError("当前浏览器不支持复制日志。"); return; } void navigator.clipboard.writeText(logs.join("\n")).then(() => setNotice("日志已复制到剪贴板。")).catch(() => setError("复制日志失败。")); }}>复制日志</button></div><pre ref={logRef}>{visibleLogs.length ? visibleLogs.join("\n") : logs.length ? "当前筛选条件没有匹配的日志。" : "等待视觉训练执行器输出日志..."}</pre></section></div><aside className="detail-side-column"><section className="panel snapshot-card"><div className="snapshot-card-head"><div><span className="summary-eyebrow">CONFIGURATION</span><h2>配置快照</h2><p>提交训练时生效的关键参数</p></div><span className={`snapshot-card-badge ${isConfirmationPending(task.state) ? "snapshot-card-badge-pending" : ""}`}>{isConfirmationPending(task.state) ? "待确认" : "已确认"}</span></div><div className="snapshot-groups"><div className="snapshot-group"><h3>基础配置</h3><dl className="snapshot-grid"><div><dt>基础模型</dt><dd className="snapshot-value-code" title={task.model}>{task.model}</dd></div><div><dt>数据集</dt><dd className="snapshot-value-code" title={task.dataset}>{task.dataset}</dd></div><div><dt>训练框架</dt><dd><span className="snapshot-tag">LLaMA-Factory</span></dd></div><div><dt>训练方式</dt><dd><span className="snapshot-tag snapshot-tag-brand">{task.method}</span></dd></div></dl></div><div className="snapshot-group"><h3>训练超参</h3><dl className="snapshot-grid"><div><dt>Epochs</dt><dd>{parameterValue(task, "epochs")}</dd></div><div><dt>学习率</dt><dd className="snapshot-value-code">{parameterValue(task, "learningRate")}</dd></div><div><dt>Batch / 累积</dt><dd>{parameterValue(task, "batchSize")} / {parameterValue(task, "gradientAccumulation")}</dd></div><div><dt>图片最大像素</dt><dd>{parameterValue(task, "imageMaxPixels")}</dd></div><div><dt>LoRA Rank / Alpha</dt><dd>{parameterValue(task, "loraRank")} / {parameterValue(task, "loraAlpha")}</dd></div><div><dt>验证拆分</dt><dd>{parameterValue(task, "validationSplit", "0")}%</dd></div></dl></div></div></section><section className="panel output-card"><h2>训练输出</h2>{task.artifacts?.length ? task.artifacts.map((artifact) => <div className={`artifact ${artifact.selected ? "selected-artifact" : ""}`} key={`${artifact.type}-${artifact.variant ?? artifact.name ?? artifact.path}`}><span className="artifact-icon">{artifact.type === "adapter" ? "A" : artifact.type === "manifest" ? "M" : "J"}</span><div><strong>{artifact.variant === "best" ? "最佳 LoRA Adapter" : artifact.variant === "final" ? "最终 LoRA Adapter" : artifact.name ?? "训练产物"}</strong><small>{artifact.checkpointStep ? `Step ${artifact.checkpointStep} · ` : ""}{typeof artifact.evalLoss === "number" ? `验证 Loss ${artifact.evalLoss.toFixed(4)} · ` : ""}{artifact.path ?? "路径不可用"}</small>{artifact.path && <button className="artifact-path" onClick={() => void openArtifact(artifact.path!)} title="在文件资源管理器中打开">{artifact.path}</button>}</div></div>) : <div className="artifact"><span className="artifact-icon">A</span><div><strong>LoRA Adapter</strong><small>训练完成后自动生成</small></div></div>}<div className="output-path">视觉训练工作目录 / {task.previewId ?? task.id}</div></section></aside></div>
      </section>
    );
  }

  const activeCount = tasks.filter((task) => ["queued", "preparing", "running", "stopping", "collecting", "awaiting_executor"].includes(task.state)).length;
  const completedCount = tasks.filter((task) => task.state === "completed").length;
  const failedCount = tasks.filter((task) => task.state === "failed").length;

  return (
    <section className="content tuning-content vision-training-page">
      <PageHeader title="微调任务" description="管理视觉理解 IMAGE_TEXT SFT / DPO LoRA 与 QLoRA 微调任务" actions={<button className="primary-btn" onClick={() => { setForm(defaultForm); setPreflight(null); setError(""); setNotice(""); setView("create"); }}>新建微调任务</button>} />
      <div className={`backend-status backend-status-${backendState}`}><span className="status-dot" />{backendState === "online" ? "视觉训练服务已连接 · 支持确认提交、实时指标和 Adapter 产物" : backendState === "checking" ? "正在连接视觉训练服务" : "视觉训练服务未连接，请运行 npm run backend"}</div>
      {error && <div className="form-error vision-list-message">{error}</div>}
      {notice && <div className="vision-success vision-list-message">{notice}</div>}
      <div className="summary training-metrics"><div className="metric"><div className="metric-value">{tasks.length}</div><div className="metric-label">全部任务</div></div><div className="metric"><div className="metric-value blue">{activeCount}</div><div className="metric-label">排队与运行</div></div><div className="metric"><div className="metric-value green">{completedCount}</div><div className="metric-label">已完成</div></div><div className="metric"><div className="metric-value red">{failedCount}</div><div className="metric-label">失败</div></div></div>
      <div className="filters"><input className="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索任务名称或任务 ID" /><select className="filter-select" value={stateFilter} onChange={(event) => setStateFilter(event.target.value as StateFilter)}><option value="all">全部状态</option><option value="awaiting_confirmation">待确认</option><option value="queued">排队中</option><option value="running">运行中</option><option value="completed">已完成</option><option value="stopped">已停止</option><option value="failed">失败</option><option value="awaiting_executor">待执行器</option></select><div className="filter-spacer" /><button className="secondary-btn refresh" title="刷新任务列表" onClick={() => void refresh()} disabled={loading}>↻</button></div>
      <div className="table-wrap training-table vision-training-table"><div className="table-scroll"><table><thead><tr><th>任务名称</th><th>基础模型</th><th>数据集</th><th>训练方式</th><th>状态 / 进度</th><th>创建时间</th><th>操作</th></tr></thead><tbody>{visibleTasks.map((task) => <tr key={task.id}><td><button className="dataset-link" onClick={() => void openDetail(task)}>{task.name}</button><div className="row-sub">{task.id}</div></td><td>{task.model}<div className="row-sub">{task.modelRepoId ?? task.modelId}</div></td><td>{task.dataset}<div className="row-sub">{task.datasetId}</div></td><td>{task.method}<div className="row-sub">图片上限 {parameterValue(task, "imageMaxPixels")}</div></td><td><TaskStatus state={task.state} />{["queued", "preparing", "running", "stopping", "collecting"].includes(task.state) && <div className="table-progress"><span style={{ width: `${task.progress}%` }} /></div>}<div className="row-sub">{task.currentStep}</div></td><td>{compactTime(task.createdAt)}<div className="row-sub">{task.owner}</div></td><td><div className="actions">{isConfirmationPending(task.state) && <button className="link-btn" data-testid={`vision-confirm-${task.id}`} onClick={() => void taskAction(task, "confirm")}>确认提交</button>}{["queued", "preparing", "running", "stopping", "collecting"].includes(task.state) && <button className="link-btn danger" onClick={() => void taskAction(task, "stop")}>停止</button>}{["failed", "stopped"].includes(task.state) && <button className="link-btn" onClick={() => void taskAction(task, "retry")}>重试</button>}{task.provider === "youyun" && (task.cleanupState === "pending" || task.cleanupState === "failed" || task.resourceState === "cleanup_failed") && <button className="link-btn danger" onClick={() => void releaseTask(task)}>释放实例</button>}<button className="link-btn" onClick={() => void openDetail(task)}>详细</button><button className="link-btn" onClick={() => void cloneTask(task)}>克隆</button><button className="link-btn danger" onClick={() => void deleteTask(task)}>删除</button></div></td></tr>)}</tbody></table>{!visibleTasks.length && <EmptyState kind={loading ? "loading" : tasks.length ? "filtered" : "empty"} title={loading ? "正在读取视觉微调任务" : tasks.length ? "没有匹配的视觉微调任务" : "暂无视觉微调任务"} description={loading ? "正在同步任务状态。" : tasks.length ? "调整搜索词或状态筛选后重试。" : "创建视觉理解微调任务后，任务会显示在这里。"} />}</div><div className="pagination"><span>共 {visibleTasks.length} 条任务</span><span>第 1 / 1 页</span></div></div>
    </section>
  );
}
