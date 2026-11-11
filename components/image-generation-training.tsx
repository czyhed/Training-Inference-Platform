"use client";

import { ArrowLeft, Cloud, Copy, Play, Plus, RefreshCw, RotateCcw, Square, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { apiUrl } from "../lib/api-config";
import { BackButton } from "./back-button";
import { TrainingTaskDetailSummary } from "./training-task-detail";
import { EmptyState } from "./ui/empty-state";
import { PageHeader } from "./ui/page-header";
import { selectYouyunZone, youyunMachineIsUnavailable, youyunPreferredMode, type YouyunZone } from "../lib/youyun-location";
import { YouyunZonePicker } from "./youyun-zone-picker";
import { YouyunInventoryIndicator } from "./youyun-inventory-indicator";

type Dataset = { id: string; name: string; sampleCount?: number; state: string; structure?: string; type?: string };
type Model = { id: string; label: string; repoId: string; modelType: string; readyForYouyun: boolean; supportedTrainingModes: string[]; reason?: string | null };
type TrainingMetric = { step?: number; loss?: number; evalLoss?: number; learningRate?: number };
type TrainingArtifact = { type?: string; variant?: string; name?: string; path?: string; selected?: boolean; checkpointStep?: number; evalLoss?: number };
type TrainingStage = { message?: string; phase?: string; durationSeconds?: number | null; elapsedSeconds?: number | null; startedAt?: string };
type TrainingAttempt = { number?: number; status?: string; startedAt?: string; finishedAt?: string | null; durationSeconds?: number | null; stages?: TrainingStage[] };
type TrainingTask = {
  id: string;
  name: string;
  state: string;
  status?: string;
  progress?: number;
  currentStep?: string;
  modelId?: string;
  modelRepoId?: string;
  datasetId?: string;
  datasetName?: string;
  provider?: string;
  owner?: string;
  outputName?: string;
  resolution?: number;
  epochs?: number;
  maxTrainSteps?: number;
  learningRate?: number;
  loraRank?: number;
  batchSize?: number;
  gradientAccumulation?: number;
  validationSplit?: number;
  seed?: number;
  resource?: Partial<YouyunResource>;
  logs?: string[];
  metrics?: TrainingMetric[];
  artifacts?: TrainingArtifact[];
  attempts?: TrainingAttempt[];
  failureCode?: string;
  failureMessage?: string;
  suggestedAction?: string;
  cleanupState?: string;
  resourceState?: string;
  createdAt?: string;
  updatedAt?: string;
};
type TrainingView = "list" | "create" | "detail";
type StateFilter = "all" | "awaiting_confirmation" | "queued" | "running" | "completed" | "stopped" | "failed";
type PriceBreakdown = { currency: string; instance: number; disks: number; systemDisks: number; image: number; total: number };
type YouyunResource = { mode: string; instanceMode?: "pod" | "uhost"; region: string; zone: string; gpuType: string; gpuCount: number; cpuCores: number; memoryGb: number; chargeType: string; imageId: string; price: PriceBreakdown };
// Backward-compatible payload contract: instanceMode: "pod" remains accepted by the API.
type PreflightCheck = { status: string; message: string; price?: PriceBreakdown };
type Preflight = { ready: boolean; checks: Record<string, PreflightCheck>; reasons?: string[]; checkedAt?: string; expiresAtEpoch?: number; youyunResource?: YouyunResource };
type YouyunSpecs = {
  instanceMode?: "pod" | "uhost" | "auto";
  fallbackEligible?: boolean;
  fallback?: boolean;
  fallbackReason?: string;
  region: string;
  zone: string;
  chargeType: string;
  gpuType: string;
  imageId?: string;
  recommendedImageId: string;
  recommendedImageName: string;
  recommendedCommunityImageId?: string;
  recommendedCommunityImageName?: string;
  requiresDiffusersInstall?: boolean;
  images: Array<{ id: string; name: string; type?: string; description?: string; status?: string }>;
  communityImages?: Array<{ id?: string | null; name: string; versionName?: string; status?: string; available?: boolean; verificationState?: "unverified" | "verified" | "failed" | "expired"; verificationExpiresAt?: number | null; verificationMessage?: string; supportedGpuTypes?: string[]; compatible?: boolean; selectable?: boolean; gpuSupported?: boolean; matchScore?: number; recommendationReason?: string }>;
  machineTypes: Array<{ name: string; zone: string; soldOut: boolean; description?: string; graphicsMemoryGb?: number | null; inventoryStatus?: "available" | "sold_out" | "unknown" | "error"; inventoryMessage?: string }>;
  capacity: Array<{ gpu: number; cpu: number; memoryGb: number; available: boolean }>;
  zones?: YouyunZone[];
};
type CommunityImage = NonNullable<YouyunSpecs["communityImages"]>[number];
type CommunityProbeResult = { passed: boolean; cleanupState: "released" | "failed"; cleanupError?: string | null; retryBlocked?: boolean; blockingReason?: string | null; verificationState: string; logs?: string[] };
// Resource payload preserves region: specs.region and zone: specs.zone when no override is selected.

const activeStates = new Set(["preparing", "running", "collecting", "stopping"]);

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), { ...options, credentials: "include", cache: "no-store" });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(formatRequestError(body, response.status));
  return body as T;
}

function formatRequestError(body: unknown, status: number): string {
  if (!body || typeof body !== "object") return `请求失败（${status}）`;
  const detail = "detail" in body ? (body as { detail?: unknown }).detail : undefined;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (!detail || typeof detail !== "object") return `请求失败（${status}）`;
  const structured = detail as { message?: unknown; reasons?: unknown; checks?: unknown };
  const reasons = Array.isArray(structured.reasons)
    ? structured.reasons.filter((item): item is string => typeof item === "string" && Boolean(item.trim()))
    : [];
  const checkMessages = structured.checks && typeof structured.checks === "object"
    ? Object.values(structured.checks as Record<string, unknown>)
      .map((item) => item && typeof item === "object" && "message" in item ? (item as { message?: unknown }).message : undefined)
      .filter((item): item is string => typeof item === "string" && Boolean(item.trim()))
    : [];
  const messages = Array.from(new Set([...reasons, ...checkMessages]));
  if (messages.length) return messages.join("；");
  if (typeof structured.message === "string" && structured.message.trim()) return structured.message;
  return `请求失败（${status}）`;
}

function ParameterInput({ label, value, onChange, min, max, step = 1 }: { label: string; value: number; onChange: (value: number) => void; min?: number; max?: number; step?: number }) {
  return <label className="parameter-field"><span>{label}</span><input type="number" className="input" value={value} min={min} max={max} step={step} onChange={(event) => onChange(Number(event.target.value))} /></label>;
}

function statusLabel(state: string) {
  return ({ awaiting_confirmation: "待确认", queued: "排队中", preparing: "准备环境", running: "训练中", collecting: "收集产物", completed: "已完成", failed: "失败", stopped: "已停止", stopping: "停止中" } as Record<string, string>)[state] || state;
}

function statusClass(state: string) {
  if (state === "completed") return "status-completed";
  if (state === "failed") return "status-failed";
  if (["running", "preparing", "collecting", "stopping"].includes(state)) return "status-running";
  if (["queued", "awaiting_confirmation"].includes(state)) return "status-queued";
  return "status-stopped";
}

function compactTime(value?: string) {
  if (!value) return "--";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

const trainingStages = [
  ["awaiting_confirmation", "待确认"],
  ["queued", "排队"],
  ["preparing", "准备环境"],
  ["running", "训练"],
  ["collecting", "收集产物"],
  ["completed", "完成"],
] as const;

function stageIndex(task: { state: string; phase?: string | null }) {
  // 失败/停止的任务没有"进行中"的阶段；运行中的任务按真实 phase 定位。
  if (task.state === "failed" || task.state === "stopped") return -1;
  const key = task.state === "stopping" ? task.phase || "running" : task.state;
  return Math.max(0, trainingStages.findIndex(([stage]) => stage === key));
}

function ImageTrainingLossChart({ metrics }: { metrics: TrainingMetric[] }) {
  const trainPoints = metrics.filter((item): item is TrainingMetric & { step: number; loss: number } => typeof item.step === "number" && typeof item.loss === "number");
  const evalPoints = metrics.filter((item): item is TrainingMetric & { step: number; evalLoss: number } => typeof item.step === "number" && typeof item.evalLoss === "number");
  if (!trainPoints.length && !evalPoints.length) return <section className="panel image-training-loss-card"><div className="panel-head"><div><h2>Loss 指标</h2><p>训练开始后展示真实 Diffusers Trainer 指标。</p></div><span>0 个点</span></div><div className="empty-state">等待训练指标。</div></section>;
  const values = [...trainPoints.map((item) => item.loss), ...evalPoints.map((item) => item.evalLoss)];
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = Math.max(0.000001, max - min);
  const toCoordinate = (item: { step: number }, value: number, index: number, total: number) => `${total === 1 ? 50 : (index / (total - 1)) * 100},${88 - ((value - min) / range) * 72}`;
  const trainCoordinates = trainPoints.map((item, index) => toCoordinate(item, item.loss, index, trainPoints.length)).join(" ");
  const evalCoordinates = evalPoints.map((item, index) => toCoordinate(item, item.evalLoss, index, evalPoints.length)).join(" ");
  const latest = trainPoints[trainPoints.length - 1];
  const latestEval = evalPoints[evalPoints.length - 1];
  const lowestEval = evalPoints.length ? Math.min(...evalPoints.map((item) => item.evalLoss)) : null;
  return <section className="panel image-training-loss-card"><div className="panel-head"><div><h2>Loss 指标</h2><p>训练 Loss 与验证 Loss 随 Step 变化，用于观察收敛趋势。</p></div><span>{trainPoints.length + evalPoints.length} 个点</span></div><div className="image-training-loss-summary"><div><span>最新 Step</span><strong>{latest?.step ?? latestEval?.step ?? "--"}</strong></div><div><span>最新训练 Loss</span><strong>{latest ? latest.loss.toFixed(4) : "--"}</strong></div><div><span>最新验证 Loss</span><strong>{latestEval ? latestEval.evalLoss.toFixed(4) : "未启用"}</strong></div><div><span>最低 Loss</span><strong>{min.toFixed(4)}</strong></div><div><span>最低验证 Loss</span><strong>{lowestEval !== null ? lowestEval.toFixed(4) : "未启用"}</strong></div></div><div className="image-training-loss-chart"><svg viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label="文生图训练 Loss 曲线"><line x1="0" y1="88" x2="100" y2="88" /><line x1="0" y1="16" x2="0" y2="88" />{trainPoints.length > 0 && <polyline className="train-series" points={trainCoordinates} />}{evalPoints.length > 0 && <polyline className="eval-series" points={evalCoordinates} />}</svg></div><div className="image-training-loss-legend"><span><span className="swatch train" />训练 Loss</span><span><span className="swatch eval" />验证 Loss（虚线）</span></div></section>;
}

export function ImageGenerationTraining() {
  const [view, setView] = useState<TrainingView>("list");
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [tasks, setTasks] = useState<TrainingTask[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [detailTask, setDetailTask] = useState<TrainingTask | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [stateFilter, setStateFilter] = useState<StateFilter>("all");
  const [datasetId, setDatasetId] = useState("");
  const [modelId, setModelId] = useState("");
  const [trainingMode, setTrainingMode] = useState<"lora" | "qlora">("lora");
  const [name, setName] = useState("SDXL LoRA 微调");
  const [outputName, setOutputName] = useState("sdxl-lora-adapter");
  const [resolution, setResolution] = useState(512);
  const [epochs, setEpochs] = useState(1);
  const [maxTrainSteps, setMaxTrainSteps] = useState(100);
  const [learningRate, setLearningRate] = useState(0.0001);
  const [loraRank, setLoraRank] = useState(4);
  const [batchSize, setBatchSize] = useState(1);
  const [gradientAccumulation, setGradientAccumulation] = useState(1);
  const [validationSplit, setValidationSplit] = useState(0);
  const [seed, setSeed] = useState(42);
  const [specs, setSpecs] = useState<YouyunSpecs | null>(null);
  const [gpuType, setGpuType] = useState("");
  const [imageId, setImageId] = useState("");
  const [cpuCores, setCpuCores] = useState(0);
  const [memoryGb, setMemoryGb] = useState(0);
  const [selectedYouyunInstanceMode, setSelectedYouyunInstanceMode] = useState<"pod" | "uhost">("pod");
  const [selectedYouyunRegion, setSelectedYouyunRegion] = useState("");
  const [selectedYouyunZone, setSelectedYouyunZone] = useState("");
  const [resource, setResource] = useState<YouyunResource | null>(null);
  const [resourceLoading, setResourceLoading] = useState(false);
  const [resourceError, setResourceError] = useState("");
  const [preflight, setPreflight] = useState<Preflight | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [pendingAction, setPendingAction] = useState("");
  const [message, setMessage] = useState("");
  const [notice, setNotice] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<TrainingTask | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [probeTarget, setProbeTarget] = useState<CommunityImage | null>(null);
  const [probeBusy, setProbeBusy] = useState(false);
  const [probeResult, setProbeResult] = useState<CommunityProbeResult | null>(null);
  const [backendState, setBackendState] = useState<"checking" | "online" | "offline">("checking");

  const selectedDataset = datasets.find((item) => item.id === datasetId);
  const selectedModel = models.find((item) => item.id === modelId);
  const qloraSupported = Boolean(selectedModel?.supportedTrainingModes.includes("qlora"));
  const relevantImages = useMemo(() => {
    const filtered = (specs?.images || []).filter((image) => /diffusers|stable diffusion|sdxl|pytorch|torch|cuda|conda/i.test(`${image.name} ${image.description || ""}`));
    const selected = specs?.images.find((image) => image.id === imageId);
    return Array.from(new Map([...(selected ? [selected] : []), ...filtered].map((image) => [image.id, image])).values());
  }, [imageId, specs]);
  const compatibleImages = useMemo(
    () => Array.from(new Map(
      (specs?.communityImages || [])
        .filter((image) => image.compatible !== false || image.id === imageId)
        .map((image) => [image.name.trim().toLocaleLowerCase(), image]),
    ).values()),
    [imageId, specs],
  );
  const selectedCommunityImage = useMemo(
    () => specs?.communityImages?.find((image) => image.id === imageId),
    [imageId, specs],
  );
  const singleCardCapacities = useMemo(() => specs?.capacity.filter((item) => item.gpu === 1) || [], [specs]);
  const healthyYouyunZones = useMemo(
    () => (specs?.zones ?? []).filter((zone) => zone.healthStatus !== "cooldown"),
    [specs],
  );
  const visibleTasks = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    return tasks.filter((task) => {
      const stateMatches = stateFilter === "all" || task.state === stateFilter || (stateFilter === "running" && activeStates.has(task.state));
      const textMatches = !keyword || [task.name, task.id, task.modelRepoId, task.datasetName, task.datasetId].some((value) => String(value || "").toLowerCase().includes(keyword));
      return stateMatches && textMatches;
    });
  }, [query, stateFilter, tasks]);
  const resourceReady = Boolean(specs && resource && !resourceLoading && !resourceError && imageId && cpuCores && memoryGb);
  const effectiveBatchSize = batchSize * gradientAccumulation;
  const payload = useMemo(() => ({
    datasetId,
    modelId,
    name,
    outputName,
    resolution,
    epochs,
    maxTrainSteps,
    learningRate,
    loraRank,
    batchSize,
    gradientAccumulation,
    validationSplit,
    seed,
    provider: "youyun",
    resource: specs && imageId && cpuCores && memoryGb ? {
      mode: "manual",
      instanceMode: selectedYouyunInstanceMode,
      region: selectedYouyunRegion || specs.region,
      zone: selectedYouyunZone || specs.zone,
      gpuType: gpuType || specs.gpuType,
      gpuCount: 1,
      cpuCores,
      memoryGb,
      chargeType: specs.chargeType,
      imageId,
      requiresDiffusersInstall: Boolean(specs.requiresDiffusersInstall),
    } : {},
  }), [batchSize, cpuCores, datasetId, epochs, gpuType, gradientAccumulation, imageId, learningRate, loraRank, maxTrainSteps, memoryGb, modelId, name, outputName, resolution, seed, selectedYouyunInstanceMode, selectedYouyunRegion, selectedYouyunZone, specs, validationSplit]);

  async function fetchSpecs(selectedGpuType = "", selectedImageId = "", selectedMode = selectedYouyunInstanceMode) {
    const query = new URLSearchParams({ purpose: "training", instanceMode: selectedMode });
    if (selectedYouyunRegion) query.set("region", selectedYouyunRegion);
    if (selectedYouyunZone) query.set("zone", selectedYouyunZone);
    if (selectedGpuType) query.set("gpuType", selectedGpuType);
    if (selectedImageId) query.set("imageId", selectedImageId);
    let next = await request<YouyunSpecs>(`/image-generation/youyun/gpu-specs?${query.toString()}`);
    const recommended = selectedImageId || next.recommendedImageId || next.imageId || next.images[0]?.id || "";
    if (!selectedImageId && recommended && recommended !== next.imageId) {
      query.set("imageId", recommended);
      next = await request<YouyunSpecs>(`/image-generation/youyun/gpu-specs?${query.toString()}`);
    }
    return next;
  }

  function applySpecs(next: YouyunSpecs, requestedImageId = "") {
    const nextImageId = requestedImageId || next.recommendedImageId || next.imageId || next.images[0]?.id || "";
    const selectedStillAvailable = next.capacity.some((item) => item.gpu === 1 && item.available && item.cpu === cpuCores && item.memoryGb === memoryGb);
    const first = next.capacity.find((item) => item.gpu === 1 && item.available);
    const healthyZones = (next.zones ?? []).filter((zone) => zone.healthStatus !== "cooldown");
    setSpecs(next);
    setSelectedYouyunInstanceMode(next.instanceMode === "uhost" ? "uhost" : "pod");
    setSelectedYouyunRegion(next.region);
    setSelectedYouyunZone(next.zone || selectYouyunZone(healthyZones, next.region, next.instanceMode === "uhost" ? "uhost" : selectedYouyunInstanceMode));
    setGpuType(next.gpuType);
    setImageId(nextImageId);
    if (!selectedStillAvailable) {
      setCpuCores(first?.cpu || 0);
      setMemoryGb(first?.memoryGb || 0);
    }
  }

  async function load() {
    setLoading(true); setMessage(""); setResourceError("");
    try {
      const [nextDatasets, nextModels, nextTasks] = await Promise.all([
        request<Dataset[]>("/image-generation/training/datasets"),
        request<Model[]>("/image-generation/training/models"),
        request<TrainingTask[]>("/image-generation/training/tasks"),
      ]);
      setDatasets(nextDatasets); setModels(nextModels); setTasks(nextTasks);
      setBackendState("online");
      setDatasetId((current) => current || nextDatasets[0]?.id || "");
      setModelId((current) => current || nextModels.find((item) => item.readyForYouyun)?.id || "");
    } catch (reason) { setBackendState("offline"); setMessage(reason instanceof Error ? reason.message : "文生图微调配置读取失败。"); }
    try { applySpecs(await fetchSpecs()); }
    catch (reason) { setSpecs(null); setResourceError(reason instanceof Error ? reason.message : "优云 GPU 库存查询失败。"); }
    finally { setLoading(false); }
  }

  async function refreshResources(nextGpuType = gpuType, nextImageId = imageId, nextMode = selectedYouyunInstanceMode) {
    setResourceLoading(true); setResourceError(""); setResource(null); setPreflight(null);
    try { applySpecs(await fetchSpecs(nextGpuType, nextImageId, nextMode), nextImageId); }
    catch (reason) { setSpecs(null); setResourceError(reason instanceof Error ? reason.message : "优云 GPU 库存查询失败。"); }
    finally { setResourceLoading(false); }
  }

  async function refreshTasks() {
    setLoading(true); setMessage("");
    try { setTasks(await request<TrainingTask[]>("/image-generation/training/tasks")); setBackendState("online"); }
    catch (reason) { setBackendState("offline"); setMessage(reason instanceof Error ? reason.message : "文生图微调任务读取失败。"); }
    finally { setLoading(false); }
  }

  async function refreshDetail(taskId: string) {
    setDetailLoading(true);
    try {
      const [task, logBody, metricBody] = await Promise.all([
        request<TrainingTask>(`/image-generation/training/tasks/${taskId}`),
        request<{ items?: string[] }>(`/image-generation/training/tasks/${taskId}/logs`),
        request<{ items?: TrainingMetric[] }>(`/image-generation/training/tasks/${taskId}/metrics`),
      ]);
      const detail = { ...task, logs: Array.isArray(logBody.items) ? logBody.items : task.logs || [], metrics: Array.isArray(metricBody.items) ? metricBody.items : task.metrics || [] };
      setDetailTask(detail);
      setTasks((items) => items.map((item) => item.id === detail.id ? detail : item));
      setBackendState("online");
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "文生图微调任务详情读取失败。");
    } finally {
      setDetailLoading(false);
    }
  }

  async function openDetail(task: TrainingTask) {
    setSelectedId(task.id);
    setDetailTask(task);
    setMessage("");
    setNotice("");
    setView("detail");
    await refreshDetail(task.id);
  }

  function closeDetail() {
    setView("list");
    setSelectedId("");
    setDetailTask(null);
    setMessage("");
  }

  useEffect(() => { void load(); }, []);
  useEffect(() => {
    if (view !== "create" || specs || resourceLoading || resourceError) return;
    void refreshResources(gpuType, imageId, selectedYouyunInstanceMode);
  }, [gpuType, imageId, resourceError, resourceLoading, selectedYouyunInstanceMode, selectedYouyunRegion, selectedYouyunZone, specs, view]);
  useEffect(() => {
    if (!specs || !imageId || !cpuCores || !memoryGb) { setResource(null); return; }
    let active = true;
    setResourceLoading(true); setResourceError("");
    void request<YouyunResource>("/youyun/price", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        mode: "manual",
        instanceMode: selectedYouyunInstanceMode,
        region: selectedYouyunRegion || specs.region,
        zone: selectedYouyunZone || specs.zone,
        chargeType: specs.chargeType,
        gpuType: gpuType || specs.gpuType,
        gpuCount: 1,
        cpuCores,
        memoryGb,
        imageId,
      }),
    }).then((value) => { if (active) setResource(value); })
      .catch((reason) => { if (active) { setResource(null); setResourceError(reason instanceof Error ? reason.message : "优云价格查询失败。"); } })
      .finally(() => { if (active) setResourceLoading(false); });
    return () => { active = false; };
  }, [cpuCores, gpuType, imageId, memoryGb, selectedYouyunInstanceMode, selectedYouyunRegion, selectedYouyunZone, specs, view]);
  useEffect(() => { setPreflight(null); }, [batchSize, datasetId, epochs, gradientAccumulation, learningRate, loraRank, maxTrainSteps, modelId, name, outputName, resolution, seed, trainingMode, validationSplit]);
  useEffect(() => {
    if (view !== "create" || !resourceReady || !datasetId || !modelId || trainingMode !== "lora") return;
    const timer = window.setTimeout(() => { void runAutomaticPreflight(); }, 350);
    return () => window.clearTimeout(timer);
  }, [datasetId, modelId, payload, resourceReady, trainingMode, view]);
  useEffect(() => {
    const hasActive = tasks.some((item) => activeStates.has(item.state) || item.state === "queued");
    if (!hasActive) return;
    const timer = window.setInterval(() => { void request<TrainingTask[]>("/image-generation/training/tasks").then(setTasks).catch(() => undefined); }, 2500);
    return () => window.clearInterval(timer);
  }, [tasks]);
  useEffect(() => {
    if (view !== "detail" || !selectedId || !detailTask || !(activeStates.has(detailTask.state) || detailTask.state === "queued")) return;
    const timer = window.setInterval(() => { void refreshDetail(selectedId); }, 2500);
    return () => window.clearInterval(timer);
  }, [detailTask?.state, selectedId, view]);

  async function runPreflight() {
    if (!resourceReady) { setMessage(resourceError || "请先完成优云 GPU、环境镜像和 CPU / 内存规格选择，并等待价格查询完成。"); return; }
    if (trainingMode !== "lora") { setMessage("当前 SDXL 训练链路尚未通过 QLoRA 兼容性验证，请选择 LoRA。"); return; }
    setBusy(true); setMessage("");
    try { setPreflight(await request<Preflight>("/image-generation/training/preflight", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })); }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : "启动前检查失败。"); }
    finally { setBusy(false); }
  }

  async function runAutomaticPreflight() {
    try {
      setPreflight(await request<Preflight>("/image-generation/training/preflight", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }));
    } catch (reason) {
      setPreflight(null);
      setMessage(reason instanceof Error ? reason.message : "自动校验失败，请刷新资源后重试。");
    }
  }

  async function createTask() {
    if (!preflight?.ready) { setMessage("请先执行并通过启动前检查。"); return; }
    setBusy(true); setMessage("");
    try {
      const created = await request<TrainingTask>("/image-generation/training/tasks", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...payload, preflight }) });
      setTasks((current) => [created, ...current]);
      setNotice("任务已创建，确认后才会进入优云训练队列。");
      setView("list");
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : "微调任务创建失败。"); }
    finally { setBusy(false); }
  }

  async function action(taskId: string, actionName: string) {
    setBusy(true); setPendingAction(`${taskId}:${actionName}`); setMessage("");
    setNotice(actionName === "confirm" ? "正在确认并复检优云资源，请稍候。" : "");
    try {
      if (actionName === "clone") {
        const cloned = await request<TrainingTask>(`/image-generation/training/tasks/${taskId}/clone`, { method: "POST" });
        setTasks((current) => [cloned, ...current.filter((item) => item.id !== cloned.id)]);
        setNotice(`已克隆任务，新任务“${cloned.name}”待确认后才会进入训练队列。`);
        return;
      }
      const endpoint = actionName === "release" ? `/image-generation/training/tasks/${taskId}/youyun/release` : `/image-generation/training/tasks/${taskId}/${actionName}`;
      await request(endpoint, { method: "POST" });
      setTasks(await request<TrainingTask[]>("/image-generation/training/tasks"));
      if (selectedId === taskId && view === "detail") await refreshDetail(taskId);
      setNotice(actionName === "confirm" ? "任务已确认并进入训练队列。" : actionName === "retry" ? "任务已重新进入训练队列。" : actionName === "release" ? "优云实例已释放并完成状态确认。" : "已发送停止请求。");
    } catch (reason) {
      setNotice("");
      setMessage(reason instanceof Error ? reason.message : "任务操作失败。");
    } finally {
      setBusy(false); setPendingAction("");
    }
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    setDeleting(true); setMessage(""); setNotice("");
    try {
      await request<void>(`/image-generation/training/tasks/${deleteTarget.id}`, { method: "DELETE" });
      setTasks((current) => current.filter((item) => item.id !== deleteTarget.id));
      if (selectedId === deleteTarget.id) closeDetail();
      setDeleteTarget(null);
      setNotice(`微调任务“${deleteTarget.name}”已永久删除。`);
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "微调任务删除失败。");
    } finally {
      setDeleting(false);
    }
  }

  async function confirmCommunityProbe() {
    if (!probeTarget?.id || !selectedModel || !specs || !cpuCores || !memoryGb) {
      setMessage("请先选择基座模型和可用的 GPU、CPU、内存规格。");
      return;
    }
    setProbeBusy(true); setMessage(""); setProbeResult(null);
    try {
      const result = await request<CommunityProbeResult>("/youyun/community-images/probe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          purpose: "image_generation_training",
          imageId: probeTarget.id,
          modelRepoId: selectedModel.repoId,
          resource: {
            mode: "manual",
            instanceMode: selectedYouyunInstanceMode,
            region: selectedYouyunRegion || specs.region,
            zone: selectedYouyunZone || specs.zone,
            gpuType: gpuType || specs.gpuType,
            gpuCount: 1,
            cpuCores,
            memoryGb,
            chargeType: specs.chargeType,
            imageId: probeTarget.id,
          },
        }),
      });
      setProbeResult(result);
      if (result.passed && result.cleanupState === "released") {
        const verifiedName = probeTarget.name;
        const verifiedId = probeTarget.id;
        setProbeTarget(null);
        setNotice(`社区镜像“${verifiedName}”验证通过，临时实例已释放。`);
        await refreshResources(gpuType, verifiedId, selectedYouyunInstanceMode);
      } else {
        setMessage(result.cleanupState !== "released" ? "探针实例未确认释放，已阻止再次验证，请先处理资源清理。" : result.blockingReason || "社区镜像探针未通过。");
      }
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "社区镜像探针执行失败。");
    } finally {
      setProbeBusy(false);
    }
  }

  const navItems = [
    { id: "image-training-basic", index: "01", title: "基本信息", summary: `${trainingMode === "lora" ? "LoRA" : "QLoRA"} · 远程优云 GPU` },
    { id: "image-training-model-data", index: "02", title: "模型与数据", summary: `${selectedModel?.label || "基模待选择"} · ${selectedDataset?.name || "数据集待选择"}` },
    { id: "image-training-resource", index: "03", title: "训练环境", summary: resourceReady ? `${gpuType} · ${cpuCores} 核 / ${memoryGb}GB` : "优云资源待就绪" },
    { id: "image-training-parameters", index: "04", title: "训练参数", summary: `${epochs} Epochs · ${maxTrainSteps} Steps · Batch ${effectiveBatchSize}` },
    { id: "image-training-output", index: "05", title: "训练产出", summary: outputName || "Adapter 名称待填写" },
  ];

  const activeCount = tasks.filter((task) => activeStates.has(task.state) || task.state === "queued").length;
  const completedCount = tasks.filter((task) => task.state === "completed").length;
  const failedCount = tasks.filter((task) => task.state === "failed").length;
  const deleteDialog = deleteTarget && <div className="modal-backdrop" role="presentation" onMouseDown={() => !deleting && setDeleteTarget(null)}><div className="modal" role="dialog" aria-modal="true" aria-labelledby="image-training-delete-title" onMouseDown={(event) => event.stopPropagation()}><h2 id="image-training-delete-title">确认删除微调任务</h2><p>将永久删除任务记录、日志、训练目录和 Adapter 产物：任务“{deleteTarget.name}”。此操作不可撤销。</p><div className="modal-actions"><button type="button" className="secondary-btn" disabled={deleting} onClick={() => setDeleteTarget(null)}>取消</button><button type="button" className="danger-btn icon-text-btn" disabled={deleting} onClick={() => void confirmDelete()}><Trash2 size={15} />{deleting ? "删除中" : "永久删除"}</button></div></div></div>;

  if (view === "detail" && detailTask) {
    const task = detailTask;
    const metrics = task.metrics || [];
    const trainMetrics = metrics.filter((item) => typeof item.loss === "number");
    const evalMetrics = metrics.filter((item) => typeof item.evalLoss === "number");
    const bestMetric = evalMetrics.length
      ? evalMetrics.reduce((best, item) => (item.evalLoss as number) < (best.evalLoss as number) ? item : best)
      : trainMetrics.length ? trainMetrics.reduce((best, item) => (item.loss as number) < (best.loss as number) ? item : best) : undefined;
    const finalMetric = metrics[metrics.length - 1];
    const currentStage = stageIndex(task);
    const resourceSnapshot = task.resource || {};
    const attempts = task.attempts || [];
    return <section className="content tuning-content image-generation-training-page image-generation-training-detail-page">
      <div className="vision-detail-head"><div><BackButton label="返回微调任务" onClick={closeDetail} /><div className="detail-title-line"><h1>{task.name}</h1><span className={`status ${statusClass(task.state)}`}><span className="status-dot" />{statusLabel(task.state)}</span></div><div className="subtitle">{task.id} · 创建于 {compactTime(task.createdAt)}</div></div><div className="form-actions">{task.state === "awaiting_confirmation" && <button type="button" className="primary-btn" disabled={busy} onClick={() => void action(task.id, "confirm")}><Play size={15} />确认启动</button>}{activeStates.has(task.state) && <button type="button" className="danger-btn" disabled={busy} onClick={() => void action(task.id, "stop")}><Square size={15} />停止训练</button>}{["failed", "stopped"].includes(task.state) && <button type="button" className="primary-btn" disabled={busy} onClick={() => void action(task.id, "retry")}><RotateCcw size={15} />重新运行</button>}{task.provider === "youyun" && task.cleanupState && !["released", "completed"].includes(task.cleanupState) && <button type="button" className="danger-btn icon-text-btn" disabled={busy} onClick={() => void action(task.id, "release")}><Cloud size={15} />释放优云实例</button>}{!activeStates.has(task.state) && task.state !== "queued" && <button type="button" className="danger-btn icon-text-btn" disabled={busy} onClick={() => setDeleteTarget(task)}><Trash2 size={15} />删除任务</button>}<button type="button" className="secondary-btn icon-text-btn" disabled={busy} aria-busy={pendingAction === `${task.id}:clone`} onClick={() => void action(task.id, "clone")}><Copy size={15} />克隆任务</button><button type="button" className="secondary-btn icon-text-btn" disabled={detailLoading} onClick={() => void refreshDetail(task.id)}><RefreshCw size={15} className={detailLoading ? "spin" : ""} />刷新</button></div></div>
      {message && <div className="form-error vision-list-message">{message}</div>}{notice && <div className="vision-success vision-list-message">{notice}</div>}
      <div className={`vision-detail-notice${task.state === "failed" ? " failed" : ""}`}><strong>文生图训练状态</strong><span>{task.failureMessage || task.currentStep || "等待任务状态更新"}</span>{task.suggestedAction && <small>建议：{task.suggestedAction}</small>}</div>
      <TrainingTaskDetailSummary task={task} modality="image-generation" />
      <section className="vision-stage-timeline"><div className="panel-head"><div><span className="summary-eyebrow">TASK LIFECYCLE</span><h2>任务阶段</h2><p>展示文生图 LoRA 从确认、排队到 Adapter 收集的完整过程。</p></div><span className={`status ${statusClass(task.state)}`}>{statusLabel(task.state)}</span></div><div className="vision-stage-track">{trainingStages.map(([key, label], index) => { const state = task.state === "completed" || index < currentStage ? "done" : index === currentStage ? "active" : "pending"; return <div className={`vision-stage-step ${state}`} key={key}><span className="vision-stage-marker">{state === "done" ? "✓" : String(index + 1).padStart(2, "0")}</span><div><strong>{label}</strong><small>{state === "done" ? "已完成" : state === "active" ? task.currentStep || statusLabel(task.state) : "等待"}</small></div></div>; })}</div></section>
      <div className="task-overview-grid vision-task-overview"><div className="panel progress-card"><div className="panel-head"><div><span className="summary-eyebrow">训练进度</span><h2>{task.currentStep || statusLabel(task.state)}</h2></div><strong>{Math.max(0, Math.min(100, task.progress || 0))}%</strong></div><div className="large-progress"><span style={{ width: `${Math.max(0, Math.min(100, task.progress || 0))}%` }} /></div><div className="progress-meta"><span>远程优云 SDXL LoRA 执行器</span><span>{activeStates.has(task.state) ? "自动刷新" : statusLabel(task.state)}</span></div></div><div className="panel result-metric"><span>最佳 Step</span><strong>{bestMetric?.step ?? "--"}</strong><small>{evalMetrics.length ? "验证 Loss 最低" : trainMetrics.length ? "训练 Loss 最低" : "等待指标"}</small></div><div className="panel result-metric"><span>最佳 Loss</span><strong>{typeof bestMetric?.evalLoss === "number" ? bestMetric.evalLoss.toFixed(4) : typeof bestMetric?.loss === "number" ? bestMetric.loss.toFixed(4) : "--"}</strong><small>{evalMetrics.length ? "验证指标" : "训练指标"}</small></div><div className="panel result-metric"><span>最终 Loss</span><strong>{typeof finalMetric?.evalLoss === "number" ? finalMetric.evalLoss.toFixed(4) : typeof finalMetric?.loss === "number" ? finalMetric.loss.toFixed(4) : "--"}</strong><small>{finalMetric?.step ? `Step ${finalMetric.step}` : "等待训练"}</small></div></div>
      <section className="panel youyun-task-resource vision-resource-card"><div><strong>远程优云 GPU</strong><span>{resourceSnapshot.gpuType || "--"} × {resourceSnapshot.gpuCount || 1} · {resourceSnapshot.cpuCores || "--"} 核 / {resourceSnapshot.memoryGb || "--"}GB</span></div><div><strong>训练环境镜像</strong><span>{resourceSnapshot.imageId || "--"}</span></div><div><strong>数据与模型</strong><span>{task.datasetName || task.datasetId || "--"} · {task.modelRepoId || task.modelId || "--"}</span></div></section>
      <section className="panel training-attempts-card"><div className="panel-head"><div><span className="summary-eyebrow">TIMING</span><h2>执行耗时</h2><p>按尝试记录实例、SSH、模型准备和训练阶段耗时，定位失败等待点。</p></div><span>{attempts.length ? `${attempts.length} 次尝试` : "暂无记录"}</span></div>{attempts.length ? <div className="training-attempt-list">{attempts.slice().reverse().map((attempt) => <div className="training-attempt" key={`${attempt.number}-${attempt.startedAt}`}><div><strong>第 {attempt.number || "--"} 次 · {attempt.status || "运行中"}</strong><small>{attempt.startedAt ? compactTime(attempt.startedAt) : "--"}{attempt.finishedAt ? ` → ${compactTime(attempt.finishedAt)}` : ""}</small></div><strong>{typeof attempt.durationSeconds === "number" ? `${attempt.durationSeconds}s` : "进行中"}</strong><div className="training-stage-duration-list">{(attempt.stages || []).map((stage, index) => <span key={`${stage.message}-${index}`}>{stage.message || "阶段"}：{typeof stage.durationSeconds === "number" ? `${stage.durationSeconds}s` : "--"}</span>)}</div></div>)}</div> : <div className="empty-state">任务开始后自动记录每个阶段的耗时。</div>}</section>
      <div className="detail-columns"><div className="detail-main-column"><ImageTrainingLossChart metrics={metrics} /><section className="panel logs-card"><div className="panel-head"><div><h2>运行日志</h2><p>显示优云环境准备、训练与产物收集日志。</p></div><span className="logs-count">{(task.logs || []).length} 行</span></div><pre>{(task.logs || []).length ? (task.logs || []).slice(-300).join("\n") : "等待文生图训练执行器输出日志..."}</pre></section></div><aside className="detail-side-column"><section className="panel snapshot-card"><div className="snapshot-card-head"><div><span className="summary-eyebrow">CONFIGURATION</span><h2>配置快照</h2><p>任务创建时生效的模型、数据和训练参数</p></div><span className="snapshot-card-badge">{task.state === "awaiting_confirmation" ? "待确认" : "已确认"}</span></div><div className="snapshot-groups"><div className="snapshot-group"><h3>基础配置</h3><dl className="snapshot-grid"><div><dt>基础模型</dt><dd className="snapshot-value-code" title={task.modelRepoId}>{task.modelRepoId || task.modelId || "--"}</dd></div><div><dt>数据集</dt><dd className="snapshot-value-code" title={task.datasetName}>{task.datasetName || task.datasetId || "--"}</dd></div><div><dt>训练框架</dt><dd><span className="snapshot-tag">Diffusers + PEFT</span></dd></div><div><dt>训练方式</dt><dd><span className="snapshot-tag snapshot-tag-brand">LoRA</span></dd></div></dl></div><div className="snapshot-group"><h3>训练超参</h3><dl className="snapshot-grid"><div><dt>分辨率</dt><dd>{task.resolution || "--"}px</dd></div><div><dt>Epochs / Steps</dt><dd>{task.epochs ?? "--"} / {task.maxTrainSteps ?? "--"}</dd></div><div><dt>学习率</dt><dd className="snapshot-value-code">{task.learningRate ?? "--"}</dd></div><div><dt>Batch / 累积</dt><dd>{task.batchSize ?? "--"} / {task.gradientAccumulation ?? "--"}</dd></div><div><dt>LoRA Rank</dt><dd>{task.loraRank ?? "--"}</dd></div><div><dt>验证拆分</dt><dd>{task.validationSplit ?? 0}%</dd></div><div><dt>随机种子</dt><dd>{task.seed ?? "--"}</dd></div></dl></div></div></section><section className="panel output-card"><h2>训练输出</h2>{task.artifacts?.length ? task.artifacts.map((artifact) => <div className={`artifact ${artifact.selected ? "selected-artifact" : ""}`} key={`${artifact.variant || artifact.name}-${artifact.path}`}><span className="artifact-icon">A</span><div><strong>{artifact.variant === "best" ? "最佳 LoRA Adapter" : artifact.variant === "final" ? "最终 LoRA Adapter" : artifact.name || "训练产物"}</strong><small>{artifact.checkpointStep ? `Step ${artifact.checkpointStep} · ` : ""}{typeof artifact.evalLoss === "number" ? `Loss ${artifact.evalLoss.toFixed(4)} · ` : ""}{artifact.path || "路径不可用"}</small>{artifact.path && <code className="image-training-artifact-path">{artifact.path}</code>}</div></div>) : <div className="artifact"><span className="artifact-icon">A</span><div><strong>Best / Final LoRA Adapter</strong><small>训练完成后自动登记到模型管理。</small></div></div>}<div className="output-path">文生图训练工作目录 / {task.id}</div></section></aside></div>
      {deleteDialog}
    </section>;
  }

  if (view === "list") {
    return <section className="content tuning-content image-generation-training-page image-generation-training-list-page">
      <PageHeader title="微调任务" description="管理文生图 IMAGE_TEXT SDXL LoRA 微调任务" actions={<button type="button" className="primary-btn" onClick={() => { setMessage(""); setNotice(""); setPreflight(null); setView("create"); }}>新建微调任务</button>} />
      <div className={`backend-status backend-status-${backendState}`}><span className="status-dot" />{backendState === "online" ? "文生图训练服务已连接 · 支持优云预检、确认提交和 Adapter 产物" : backendState === "checking" ? "正在连接文生图训练服务" : "文生图训练服务未连接，请检查后端服务"}</div>
      {message && <div className="form-error vision-list-message">{message}</div>}
      {notice && <div className="vision-success vision-list-message">{notice}</div>}
      <div className="summary training-metrics"><div className="metric"><div className="metric-value">{tasks.length}</div><div className="metric-label">全部任务</div></div><div className="metric"><div className="metric-value blue">{activeCount}</div><div className="metric-label">排队与运行</div></div><div className="metric"><div className="metric-value green">{completedCount}</div><div className="metric-label">已完成</div></div><div className="metric"><div className="metric-value red">{failedCount}</div><div className="metric-label">失败</div></div></div>
      <div className="filters"><input className="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索任务名称或任务 ID" /><select className="filter-select" value={stateFilter} onChange={(event) => setStateFilter(event.target.value as StateFilter)}><option value="all">全部状态</option><option value="awaiting_confirmation">待确认</option><option value="queued">排队中</option><option value="running">运行中</option><option value="completed">已完成</option><option value="stopped">已停止</option><option value="failed">失败</option></select><div className="filter-spacer" /><button type="button" className="secondary-btn refresh" title="刷新任务列表" aria-label="刷新任务列表" onClick={() => void refreshTasks()} disabled={loading}><RefreshCw size={15} className={loading ? "spin" : ""} /></button></div>
      <div className="table-wrap training-table image-generation-training-table"><div className="table-scroll"><table><thead><tr><th>任务名称</th><th>基础模型</th><th>数据集</th><th>训练方式</th><th>状态 / 进度</th><th>创建时间</th><th>操作</th></tr></thead><tbody>{visibleTasks.map((task) => <tr key={task.id}><td><button type="button" className="dataset-link" onClick={() => void openDetail(task)}>{task.name}</button><div className="row-sub">{task.id}</div></td><td>Stable Diffusion XL<div className="row-sub" title={task.modelRepoId}>{task.modelRepoId || task.modelId || "--"}</div></td><td>{task.datasetName || "--"}<div className="row-sub">{task.datasetId || "--"}</div></td><td>文生图 LoRA<div className="row-sub">{task.resolution || 512}px · Rank {task.loraRank || 4}</div></td><td><span className={`status ${statusClass(task.state)}`}><span className="status-dot" />{statusLabel(task.state)}</span>{["queued", "preparing", "running", "stopping", "collecting"].includes(task.state) && <div className="table-progress"><span style={{ width: `${Math.max(0, Math.min(100, task.progress || 0))}%` }} /></div>}<div className="row-sub">{task.failureMessage || task.currentStep || "--"}</div></td><td>{compactTime(task.createdAt)}<div className="row-sub">{task.owner || "平台管理员"}</div></td><td><div className="actions">{task.state === "awaiting_confirmation" && <button type="button" className="link-btn" aria-busy={pendingAction === `${task.id}:confirm`} disabled={busy} onClick={() => void action(task.id, "confirm")}><Play size={14} />{pendingAction === `${task.id}:confirm` ? "确认中" : "确认启动"}</button>}<button type="button" className="link-btn" onClick={() => void openDetail(task)}>详细</button><button type="button" className="link-btn" aria-busy={pendingAction === `${task.id}:clone`} disabled={busy} onClick={() => void action(task.id, "clone")}>克隆</button>{activeStates.has(task.state) && <button type="button" className="link-btn danger" disabled={busy} onClick={() => void action(task.id, "stop")}><Square size={14} />停止</button>}{["failed", "stopped"].includes(task.state) && <button type="button" className="link-btn" disabled={busy} onClick={() => void action(task.id, "retry")}><RotateCcw size={14} />重试</button>}{task.provider === "youyun" && task.cleanupState && !["released", "completed"].includes(task.cleanupState) && <button type="button" className="link-btn danger" disabled={busy} onClick={() => void action(task.id, "release")}><Cloud size={14} />释放实例</button>}{!activeStates.has(task.state) && task.state !== "queued" && <button type="button" className="link-btn danger" title="删除微调任务" aria-label={`删除微调任务 ${task.name}`} disabled={busy} onClick={() => setDeleteTarget(task)}><Trash2 size={14} />删除</button>}{task.state === "completed" && (task.artifacts || []).map((artifact) => <span className="status-ready" key={artifact.variant}>{artifact.variant === "best" ? "Best Adapter" : "Final Adapter"}</span>)}</div></td></tr>)}</tbody></table>{!visibleTasks.length && <EmptyState kind={loading ? "loading" : tasks.length ? "filtered" : "empty"} title={loading ? "正在读取文生图微调任务" : tasks.length ? "没有匹配的文生图微调任务" : "还没有文生图微调任务"} description={loading ? "正在同步任务状态。" : tasks.length ? "调整搜索词或状态筛选后重试。" : "选择已发布的 IMAGE_TEXT 数据集和 SDXL 基模后，即可创建 LoRA 任务。"} action={!loading && !tasks.length ? <button type="button" className="primary-btn empty-action" onClick={() => { setMessage(""); setNotice(""); setPreflight(null); setView("create"); }}><Plus size={14} />新建微调任务</button> : undefined} />}</div><div className="pagination"><span>共 {visibleTasks.length} 条任务</span><span>第 1 / 1 页</span></div></div>
      {deleteDialog}
    </section>;
  }

  return <section className="content tuning-content image-generation-training-page">
    <div className="title-row"><div><button type="button" className="back-button" onClick={() => { setMessage(""); setView("list"); }}><ArrowLeft size={15} />返回微调任务</button><h1>新建文生图微调任务</h1><div className="subtitle">选择 SDXL 基模、IMAGE_TEXT 数据集和优云实时资源，创建 LoRA 训练任务</div></div><button type="button" className="secondary-btn icon-text-btn" onClick={() => void load()} disabled={loading}><RefreshCw size={15} className={loading ? "spin" : ""} />刷新全部</button></div>

    <div className="tuning-create-layout image-generation-create-layout">
      <div className="tuning-form-stack">
        <section id="image-training-basic" className="panel tuning-section">
          <div className="section-heading"><div><h2>基本信息</h2><p>文生图当前使用 SDXL LoRA；本机只保存配置和执行预检，确认任务后才创建优云实例。</p></div><span className="section-index">01</span></div>
          <div className="field"><label className="field-label">任务名称 <span className="required">*</span></label><input className="input" value={name} onChange={(event) => setName(event.target.value)} /></div>
          <div className="field"><label className="field-label">执行环境</label><div className="segmented execution-mode-segmented"><button type="button" disabled><strong>本地服务器</strong><small>当前电脑配置不满足 SDXL 训练要求</small></button><button type="button" className="selected" aria-pressed="true"><strong>远程优云 GPU</strong><small>确认后创建一次性训练实例</small></button></div></div>
          <div className="field"><label className="field-label">微调方式 <span className="required">*</span></label><div className="segmented tuning-mode-segmented"><button type="button" className={trainingMode === "lora" ? "selected" : ""} onClick={() => setTrainingMode("lora")}><strong>LoRA</strong><small>当前已完成真实训练验证</small></button><button type="button" disabled={!qloraSupported} className={trainingMode === "qlora" ? "selected" : ""} onClick={() => qloraSupported && setTrainingMode("qlora")}><strong>QLoRA</strong><small>{qloraSupported ? "4-bit 基座训练" : "SDXL 兼容性尚未验证"}</small></button></div></div>
          <div className="fixed-option-row"><div><span className="fixed-option-label">任务类型</span><strong>文生图 · IMAGE_TEXT</strong></div><div><span className="fixed-option-label">模型架构</span><strong>Stable Diffusion XL</strong></div><div><span className="fixed-option-label">训练框架</span><strong>Diffusers + PEFT</strong></div></div>
        </section>

        <section id="image-training-model-data" className="panel tuning-section">
          <div className="section-heading"><div><h2>模型与数据</h2><p>只展示已通过服务端校验的 SDXL 基模，以及已发布的文生图 IMAGE_TEXT 数据集。</p></div><span className="section-index">02</span></div>
          <div className="image-generation-create-grid"><label className="field"><span className="field-label">SDXL 基座模型 <span className="required">*</span></span><select className="input" value={modelId} onChange={(event) => setModelId(event.target.value)} disabled={busy || loading}><option value="">请选择 SDXL 模型</option>{models.map((item) => <option key={item.id} value={item.id} disabled={!item.readyForYouyun}>{item.label} · {item.repoId}{item.readyForYouyun ? "" : "（不可用）"}</option>)}</select>{selectedModel && <small className="vision-field-help success-text">服务端校验通过 · {selectedModel.repoId}</small>}{selectedModel?.reason && <small className="vision-field-help">{selectedModel.reason}</small>}</label><label className="field"><span className="field-label">已发布文生图数据集 <span className="required">*</span></span><select className="input" value={datasetId} onChange={(event) => setDatasetId(event.target.value)} disabled={busy || loading}><option value="">请选择数据集</option>{datasets.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.sampleCount || 0} 张</option>)}</select>{selectedDataset && <small className="vision-field-help">IMAGE_TEXT · {selectedDataset.sampleCount || 0} 条样本 · 已发布</small>}</label></div>
        </section>

        <section id="image-training-resource" className="panel tuning-section">
          <div className="section-heading"><div><h2>训练环境</h2><p>选择优云 GPU、SDXL LoRA 环境镜像和单卡 CPU / 内存规格，价格由优云实时返回。</p></div><span className="section-index">03</span></div>
          <div className="youyun-specs-panel image-generation-youyun-specs-panel youyun-specs-panel-v2">
            <div className="youyun-specs-head"><div><strong>优云 GPU 配置、环境镜像与实时库存</strong><small>{specs ? `${specs.region} / ${specs.zone} · ${specs.chargeType === "Postpay" ? "按量计费" : specs.chargeType}` : "正在查询可用资源..."}</small></div><button type="button" className="secondary-btn compact-btn" disabled={loading || resourceLoading} onClick={() => void refreshResources()}>{resourceLoading ? "查询中..." : "刷新库存"}</button></div>
            <div className="youyun-constraint-callout"><strong>当前仅支持单卡任务</strong><span>文生图训练只开放单卡；优先使用预置 Diffusers 镜像，未命中时按所选镜像安装兜底；切换 GPU 型号会重新查询库存，提交前仍会实时校验价格。</span></div>
            {resourceError && <div className="youyun-specs-error">{resourceError}</div>}
            {specs && <>
              <div className="youyun-location-picker"><div className="youyun-field"><span className="youyun-field-label">运行模式（镜像兼容）</span><div className="segmented youyun-instance-mode"><button type="button" className={selectedYouyunInstanceMode === "pod" ? "selected" : ""} onClick={() => { setSelectedYouyunInstanceMode("pod"); setSelectedYouyunRegion(""); setSelectedYouyunZone(""); setSpecs(null); setResource(null); setResourceError(""); setCpuCores(0); setMemoryGb(0); }} aria-pressed={selectedYouyunInstanceMode === "pod"}>优云 Pod</button><button type="button" className={selectedYouyunInstanceMode === "uhost" ? "selected" : ""} onClick={() => { setSelectedYouyunInstanceMode("uhost"); setSelectedYouyunRegion(""); setSelectedYouyunZone(""); setSpecs(null); setResource(null); setResourceError(""); setCpuCores(0); setMemoryGb(0); }} aria-pressed={selectedYouyunInstanceMode === "uhost"}>优云 UHost 兜底</button></div></div>{specs.fallbackReason && <small className="vision-field-help">{specs.fallbackReason}</small>}<YouyunZonePicker zones={healthyYouyunZones} selectedZone={selectedYouyunZone} onSelect={(zone) => { setSelectedYouyunInstanceMode(youyunPreferredMode(zone, undefined, selectedYouyunInstanceMode)); setSelectedYouyunRegion(zone.region); setSelectedYouyunZone(zone.zone); setSpecs(null); setResource(null); setResourceError(""); }} /></div>
              <div className="youyun-divider" />
              <div className="youyun-field"><span className="youyun-field-label">GPU 型号 · 单卡</span>
              <div className="youyun-machine-list">{Array.from(new Map(specs.machineTypes.map((machine) => [machine.name, machine])).values()).map((machine) => <button type="button" key={machine.name} className={`youyun-machine-item${youyunMachineIsUnavailable(machine, "training") ? " sold-out" : ""}${gpuType === machine.name ? " selected" : ""}`} disabled={youyunMachineIsUnavailable(machine, "training") || resourceLoading} onClick={() => { setGpuType(machine.name); setCpuCores(0); setMemoryGb(0); void refreshResources(machine.name, imageId); }}><strong>{machine.graphicsMemoryGb ? `${machine.graphicsMemoryGb}G ` : ""}{machine.name}</strong><small>{machine.graphicsMemoryGb ? `${machine.graphicsMemoryGb}GB 显存` : "显存待确认"}</small><YouyunInventoryIndicator machine={machine} /></button>)}</div>
              </div>
              <div className="youyun-divider" />
              <label className="youyun-image-field"><span className="youyun-field-label">优云训练环境镜像</span><select className="input" value={imageId} disabled={resourceLoading} onChange={(event) => { const value = event.target.value; setImageId(value); setCpuCores(0); setMemoryGb(0); void refreshResources(gpuType, value); }}><optgroup label="优云实时可用镜像">{relevantImages.map((image) => <option key={image.id} value={image.id}>{image.name || image.id}{image.id === specs.recommendedImageId ? (specs.requiresDiffusersInstall ? " · Diffusers 安装兜底" : " · SDXL LoRA 推荐") : ""}</option>)}</optgroup>{Boolean(compatibleImages.length) && <optgroup label="匹配的社区训练环境">{compatibleImages.map((image) => { const verified = image.verificationState === "verified"; const available = Boolean(image.id && image.available); return <option key={`${image.id || "catalog"}:${image.name}:${image.versionName || ""}`} value={image.id || `catalog:${image.name}`} disabled={!available || !verified || image.selectable === false}>{image.name}{image.versionName ? ` · ${image.versionName}` : ""} · {verified ? "已验证，可选择" : available ? "已匹配，待验证" : "当前区域无库存/未上架"}</option>; })}</optgroup>}</select><small><strong>推荐理由：</strong>{selectedCommunityImage?.recommendationReason || (specs.requiresDiffusersInstall ? `未发现预置 Diffusers 镜像，推荐 ${specs.recommendedImageName || "PyTorch/CUDA 镜像"} 并安装 Diffusers 兜底。` : `${specs.recommendedImageName || "当前镜像"} 已匹配 SDXL、Diffusers 和当前 GPU。`)} 无关社区镜像已自动隐藏。</small></label>
              {Boolean(compatibleImages.length) && <div className="community-image-probe-list"><div className="youyun-selection-summary"><strong>社区镜像筛选探针</strong><small>只显示与 SDXL LoRA 和当前 GPU 匹配的候选；验证不会启动训练。</small></div>{compatibleImages.map((image) => <div className="community-image-probe-item" key={`probe:${image.id || image.name}`}><div><strong>{image.name}{image.versionName ? ` · ${image.versionName}` : ""}</strong><small>{image.recommendationReason}</small>{image.verificationMessage && <small>{image.verificationMessage}</small>}</div><span className={image.verificationState === "verified" ? "tag-ready" : image.verificationState === "failed" ? "tag-off" : "status-pending"}>{image.verificationState === "verified" ? "已验证" : image.verificationState === "failed" ? "验证失败" : image.verificationState === "expired" ? "验证已过期" : "待验证"}</span>{image.verificationState !== "verified" && <button type="button" className="secondary-btn compact-btn" disabled={probeBusy || resourceLoading || !image.id || !image.available || !cpuCores || !memoryGb || !selectedModel} onClick={() => { setProbeResult(null); setProbeTarget(image); }}>验证此镜像</button>}</div>)}</div>}
              <div className="youyun-divider" />
              <div className="youyun-field"><span className="youyun-field-label">单卡规格 · CPU / 内存</span>
              <div className="youyun-capacity-list youyun-single-capacity-list">{singleCardCapacities.map((item) => <button type="button" key={`${item.cpu}-${item.memoryGb}`} disabled={!item.available || resourceLoading} className={`youyun-capacity-item${cpuCores === item.cpu && memoryGb === item.memoryGb ? " selected" : ""}`} onClick={() => { setCpuCores(item.cpu); setMemoryGb(item.memoryGb); setResource(null); setPreflight(null); }}><span>{item.cpu} 核 / {item.memoryGb}GB 内存</span><span className={item.available ? "tag-ready" : "tag-off"}>{item.available ? "可选" : "无库存"}</span></button>)}</div>
              </div>
              {specs.capacity.some((item) => item.gpu !== 1) && <div className="youyun-multi-capacity-list"><span className="youyun-capacity-label">多卡规格（暂不支持）</span>{specs.capacity.filter((item) => item.gpu !== 1).map((item) => <span className="youyun-capacity-disabled" key={`${item.gpu}-${item.cpu}-${item.memoryGb}`}>{item.gpu} 卡 / {item.cpu} 核 / {item.memoryGb}GB</span>)}</div>}
              <div className="youyun-price-summary"><div><strong>预计按量费用</strong><span>{resourceLoading ? "查询中..." : resource ? `${resource.price.currency === "CNY" ? "¥" : `${resource.price.currency} `}${resource.price.total.toFixed(2)} / 小时` : "暂不可获取"}</span></div>{resource && <small>实例 ¥{resource.price.instance.toFixed(2)} · 系统盘 ¥{resource.price.systemDisks.toFixed(2)} · 数据盘 ¥{resource.price.disks.toFixed(2)} · 镜像 ¥{resource.price.image.toFixed(2)}；实际以优云账单为准</small>}</div>
            </>}
          </div>
        </section>

        <section id="image-training-parameters" className="panel tuning-section">
          <div className="section-heading"><div><h2>训练参数</h2><p>参数与真实 SDXL LoRA 执行器保持一致；最大步数优先于训练轮次。</p></div><span className="section-index">04</span></div>
          <div className="parameter-grid image-generation-parameter-grid"><ParameterInput label="训练分辨率" value={resolution} onChange={setResolution} min={256} max={2048} step={8} /><ParameterInput label="训练轮次 Epochs" value={epochs} onChange={setEpochs} min={0.1} max={100} step={0.1} /><ParameterInput label="最大训练步数" value={maxTrainSteps} onChange={setMaxTrainSteps} min={1} max={100000} /><ParameterInput label="学习率" value={learningRate} onChange={setLearningRate} min={0.000001} max={1} step={0.00001} /><ParameterInput label="LoRA Rank" value={loraRank} onChange={setLoraRank} min={1} max={256} /><ParameterInput label="单卡 Batch Size" value={batchSize} onChange={setBatchSize} min={1} max={16} /><ParameterInput label="梯度累积" value={gradientAccumulation} onChange={setGradientAccumulation} min={1} max={256} /><ParameterInput label="随机种子 Seed" value={seed} onChange={setSeed} min={0} max={2147483647} /></div>
          <div className="effective-batch">有效 Batch Size：{effectiveBatchSize}（单卡 Batch {batchSize} × 梯度累积 {gradientAccumulation}）</div>
          <div className="validation-control image-generation-validation-control"><div className="validation-control-head"><label className="field-label">验证集拆分比例</label><strong>{validationSplit}%</strong></div><input type="range" min="0" max="50" step="5" value={validationSplit} onChange={(event) => setValidationSplit(Number(event.target.value))} /><div className="range-labels"><span>0%</span><span>50%</span></div><p>{validationSplit ? `将使用固定随机种子拆分 ${validationSplit}% 验证数据。` : "设置为 0% 时只训练并收集 Best / Final Adapter，不额外生成验证图。"}</p></div>
        </section>

        <section id="image-training-output" className="panel tuning-section">
          <div className="section-heading"><div><h2>训练产出</h2><p>训练完成后登记 Best 与 Final Adapter，可直接进入文生图模型管理和优云推理服务。</p></div><span className="section-index">05</span></div>
          <div className="field"><label className="field-label">Adapter 输出名称 <span className="required">*</span></label><input className="input" value={outputName} onChange={(event) => setOutputName(event.target.value)} /></div>
          <div className="fixed-option-row"><div><span className="fixed-option-label">默认产物</span><strong>Best / Final LoRA Adapter</strong></div><div><span className="fixed-option-label">权重格式</span><strong>Safetensors</strong></div><div><span className="fixed-option-label">后续用途</span><strong>模型管理与优云推理</strong></div></div>
        </section>

        <div className="vision-summary-note"><strong>自动校验</strong><p>{preflight ? (preflight.ready ? "当前配置已自动通过校验；提交前仍会再次复检。" : "自动校验发现阻塞项，请按下方提示调整。") : resourceReady ? "正在校验数据、模型、镜像、库存和价格。" : "资源配置就绪后自动执行，不创建优云实例。"}</p></div>
        {preflight && <div className={`vision-training-preflight ${preflight.ready ? "ready" : "blocked"}`}><div className="vision-preflight-header"><div><strong>{preflight.ready ? "启动前检查通过" : "启动前检查未通过"}</strong><p>{preflight.ready ? "数据、模型、训练参数、镜像、库存和价格均已校验。" : "请处理下列阻塞项后重新检查。"}</p></div><span>{preflight.checkedAt || "刚刚"}</span></div><div className="image-generation-preflight-grid">{Object.entries(preflight.checks).map(([key, check]) => <div className="vision-preflight-item" key={key}><span>{key}</span><strong className={check.status === "blocked" ? "blocked" : "pass"}>{check.message}</strong></div>)}</div>{!preflight.ready && <div className="vision-preflight-blockers"><strong>当前阻塞问题</strong>{preflight.reasons?.map((reason) => <p key={reason}>· {reason}</p>)}</div>}</div>}
        {message && <div className="form-error">{message}</div>}
        <div className="vision-create-footer"><div className="training-form-footer"><button type="button" className="secondary-btn" disabled={busy || loading || !datasetId || !modelId || !resourceReady} onClick={() => void runPreflight()}>执行启动前检查</button><button type="button" className="primary-btn" disabled={busy || loading || !preflight?.ready || !selectedDataset || !selectedModel || !resourceReady} onClick={() => void createTask()}><Play size={15} />{busy ? "处理中" : "创建待确认任务"}</button></div></div>
      </div>

      <aside className="panel training-summary" aria-label="文生图微调配置导航"><div className="training-summary-head"><div><div className="summary-eyebrow">IMAGE TRAINING FLOW</div><h2>已填写内容</h2></div><Cloud size={18} /></div><nav className="training-summary-nav">{navItems.map((item) => <button type="button" className="training-summary-item" key={item.id} onClick={() => document.getElementById(item.id)?.scrollIntoView({ behavior: "smooth", block: "start" })}><span className="training-summary-item-index">{item.index}</span><span className="training-summary-item-copy"><strong>{item.title}</strong><small>{item.summary}</small></span><span className="training-summary-item-arrow" aria-hidden="true">›</span></button>)}</nav><div className="vision-summary-checks"><div className="vision-summary-checks-head"><strong>提交前检查</strong><span>{preflight ? (preflight.ready ? "已通过" : "有阻塞") : "未执行"}</span></div><div className="vision-summary-check"><span className={datasetId ? "pass" : "blocked"} />数据集<span>{selectedDataset?.name || "待选择"}</span></div><div className="vision-summary-check"><span className={modelId ? "pass" : "blocked"} />SDXL 基模<span>{selectedModel?.label || "待选择"}</span></div><div className="vision-summary-check"><span className={resourceReady ? "pass" : "blocked"} />优云资源<span>{resourceReady ? "已就绪" : "待配置"}</span></div></div><div className="vision-summary-note"><strong>费用边界</strong><p>读取库存、查询价格和执行启动前检查都不会创建实例；只有确认待确认任务后才会申请优云 GPU。</p></div></aside>
    </div>

    {probeTarget && <div className="modal-backdrop" role="presentation" onMouseDown={() => !probeBusy && setProbeTarget(null)}><div className="modal" role="dialog" aria-modal="true" aria-labelledby="community-probe-title" onMouseDown={(event) => event.stopPropagation()}><h2 id="community-probe-title">验证社区镜像</h2><p>将验证“{probeTarget.name}”是否满足 SDXL、Diffusers、PEFT、LoRA 注入与 CUDA dry-run。探针会创建临时优云实例，不会启动训练。</p><div className="vision-model-probe-detail"><strong>费用与释放边界</strong><p>{resource ? `按当前配置预计 ¥${resource.price.total.toFixed(2)} / 小时，按实际占用计费。` : "费用以优云实时账单为准。"} 无论通过、失败或超时，后端都会释放实例；只有确认释放成功后才标记镜像可选。</p></div>{probeResult && <div className="vision-model-probe-detail"><strong>最近结果</strong><p>{probeResult.passed ? "环境检查通过" : probeResult.blockingReason || "环境检查未通过"} · cleanupState={probeResult.cleanupState}</p></div>}<div className="modal-actions"><button type="button" className="secondary-btn" disabled={probeBusy} onClick={() => setProbeTarget(null)}>取消</button><button type="button" className="primary-btn" disabled={probeBusy} onClick={() => void confirmCommunityProbe()}>{probeBusy ? "验证并清理中..." : "确认创建临时实例"}</button></div></div></div>}
  </section>;
}

export default ImageGenerationTraining;
