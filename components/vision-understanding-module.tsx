"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { apiUrl } from "../lib/api-config";
import { friendlyYouyunError, selectYouyunZone, youyunMachineIsUnavailable, youyunRegionLabel, type YouyunZone } from "../lib/youyun-location";
import { YouyunZonePicker } from "./youyun-zone-picker";
import { YouyunInventoryIndicator } from "./youyun-inventory-indicator";
import { Dataset } from "../lib/types";
import {
  DEFAULT_VISION_BASE_MODEL_FORM,
  applyVisionBaseModelPreset,
  type VisionBaseModelForm,
} from "../lib/vision-base-model-presets";
import type { VisionManagedModel } from "../lib/vision-model-management";
import { VisionInferenceServiceModule } from "./vision-inference-service-module";
import { VisionModelManagementView as RedesignedVisionModelManagementView } from "./vision-model-management-view";

type VisionTab = "deploy" | "training" | "inference" | "evaluation";
type VisionWorkspace = "all" | "training" | "models" | "services";

type VisionModel = VisionManagedModel;

type VisionService = {
  id: string;
  name: string;
  state: string;
  model: string;
  gpu: string;
  memory: string;
  error?: string;
  createdAt?: string;
};

type EvaluationJob = {
  id: string;
  status: string;
  serviceId: string;
  datasetPath: string;
  createdAt: string;
  error?: string;
  summary?: {
    total?: number;
    successful?: number;
    failed?: number;
    exactMatch?: number;
    tokenF1?: number;
    averageLatencyMs?: number;
  };
};

type TrainingPreview = {
  previewId: string;
  status: string;
  llamaFactoryContract: string;
  configPath: string;
  yaml: string;
  manifest: {
    totalSamples: number;
    trainSamples: number;
    validationSamples: number;
    referencedImages: number;
    template: string;
    tuningMode: string;
  };
};

type EvaluationDataset = {
  id: string;
  name: string;
  description?: string;
  datasetPath: string;
  metadataName: string;
  sampleCount: number;
  missingImages: number;
  valid: boolean;
};

type YouyunGpuSpecs = {
  region: string;
  zone: string;
  instanceMode?: "pod" | "uhost" | "auto";
  fallback?: boolean;
  fallbackReason?: string;
  zones?: YouyunZone[];
  gpuType: string;
  chargeType: string;
  imageId: string;
  recommendedImageId: string;
  recommendedImageName: string;
  communityImages?: { id?: string | null; name: string; versionName?: string; status: string; tags: string[]; description: string; available: boolean; source: string; verificationState?: "unverified" | "verified" | "failed"; verifiedAt?: string | null; verificationExpiresAt?: number | null; price?: unknown; supportedGpuTypes?: string[]; gpuSupported?: boolean; verificationMessage?: string | null }[];
  images: { id: string; name: string; type: string; status: string; tags: string[]; description: string }[];
  machineTypes: { name: string; zone: string; soldOut: boolean; description: string; graphicsMemoryGb: number | null; inventoryStatus?: "available" | "sold_out" | "unknown" | "error"; inventoryMessage?: string }[];
  capacity: { gpu: number; cpu: number; memoryGb: number; available: boolean }[];
};

type YouyunResource = {
  mode: "manual";
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

async function visionRequest<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), { ...options, credentials: "include", cache: "no-store" });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail;
    const message = typeof detail === "string"
      ? detail
      : detail?.message ?? (detail ? JSON.stringify(detail) : `请求失败（${response.status}）`);
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

function postJson<T>(path: string, body: Record<string, unknown>) {
  return visionRequest<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function patchJson<T>(path: string, body: Record<string, unknown>) {
  return visionRequest<T>(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function fileDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(new Error("读取图片失败。"));
    reader.readAsDataURL(file);
  });
}

function statusLabel(value: string) {
  return ({
    created: "待启动",
    starting: "启动中",
    running: "运行中",
    stopping: "停止中",
    stopped: "已停止",
    recovering: "恢复中",
    failed: "失败",
    queued: "排队中",
    completed: "已完成",
  } as Record<string, string>)[value] ?? value;
}

function initialTab(workspace: VisionWorkspace): VisionTab {
  return workspace === "training" ? "training" : "deploy";
}

export function VisionUnderstandingModule({ datasets, workspace = "all" }: { datasets: Dataset[]; workspace?: VisionWorkspace }) {
  const [tab, setTab] = useState<VisionTab>(() => initialTab(workspace));
  const [models, setModels] = useState<VisionModel[]>([]);
  const [youyunCatalog, setYouyunCatalog] = useState<VisionModel[]>([]);
  const [services, setServices] = useState<VisionService[]>([]);
  const [evaluations, setEvaluations] = useState<EvaluationJob[]>([]);
  const [evaluationDatasets, setEvaluationDatasets] = useState<EvaluationDataset[]>([]);
  const [evaluationDatasetsLoading, setEvaluationDatasetsLoading] = useState(false);
  const [validatingEvaluation, setValidatingEvaluation] = useState(false);
  const [creatingEvaluation, setCreatingEvaluation] = useState(false);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const [modelForm, setModelForm] = useState<VisionBaseModelForm>(() => ({ ...DEFAULT_VISION_BASE_MODEL_FORM }));
  const [deployForm, setDeployForm] = useState({
    name: "视觉理解推理服务",
    modelId: "",
    maxModelLength: "4096",
    gpuMemoryUtilization: "0.90",
    tensorParallelSize: "1",
  });
  const [trainingForm, setTrainingForm] = useState({
    datasetId: "",
    modelId: "",
    provider: "local",
    tuningMode: "qlora",
    batchSize: "1",
    gradientAccumulation: "8",
    validationSplit: "10",
    imageMaxPixels: "401408",
    epochs: "2",
    learningRate: "0.00002",
    loraRank: "64",
    loraAlpha: "128",
    loraDropout: "0.05",
    gradientCheckpointing: true,
  });
  const [preflight, setPreflight] = useState<null | {
    ready: boolean;
    effectiveBatchSize: number;
    executionStatus: string;
    message: string;
    reasons: string[];
  }>(null);
  const [trainingPreview, setTrainingPreview] = useState<TrainingPreview | null>(null);
  const [showTrainingConfirm, setShowTrainingConfirm] = useState(false);
  const [submittingTraining, setSubmittingTraining] = useState(false);
  const [submittedTraining, setSubmittedTraining] = useState<{ id: string; state: string } | null>(null);
  const [youyunSpecs, setYouyunSpecs] = useState<YouyunGpuSpecs | null>(null);
  const [youyunLoading, setYouyunLoading] = useState(false);
  const [youyunError, setYouyunError] = useState("");
  const [selectedYouyunGpuType, setSelectedYouyunGpuType] = useState("");
  const [selectedYouyunRegion, setSelectedYouyunRegion] = useState("");
  const [selectedYouyunZone, setSelectedYouyunZone] = useState("");
  const [youyunZones, setYouyunZones] = useState<YouyunZone[]>([]);
  const [selectedYouyunCpuCores, setSelectedYouyunCpuCores] = useState<number | null>(null);
  const [selectedYouyunMemoryGb, setSelectedYouyunMemoryGb] = useState<number | null>(null);
  const [selectedYouyunImageId, setSelectedYouyunImageId] = useState("");
  const [youyunResource, setYouyunResource] = useState<YouyunResource | null>(null);
  const [youyunPriceLoading, setYouyunPriceLoading] = useState(false);
  const [selectedServiceId, setSelectedServiceId] = useState("");
  const [imageFile, setImageFile] = useState<File | null>(null);
  const [imagePreview, setImagePreview] = useState("");
  const [question, setQuestion] = useState("请描述图片中的主要内容，并回答可见文字或关键信息。");
  const [answer, setAnswer] = useState("");
  const [sending, setSending] = useState(false);
  const [evaluationForm, setEvaluationForm] = useState({
    serviceId: "",
    datasetPath: "data/vision_understanding_eval_v1",
    metadataName: "metadata.jsonl",
    maxSamples: "30",
  });
  const [selectedEvaluationDatasetId, setSelectedEvaluationDatasetId] = useState("data/vision_understanding_eval_v1");
  const [datasetValidation, setDatasetValidation] = useState<null | {
    valid: boolean;
    sampleCount: number;
    missingImages: number;
    datasetPath: string;
  }>(null);

  const visualDatasets = useMemo(
    () => datasets.filter((item) => item.structure === "IMAGE_TEXT" && item.state === "published"),
    [datasets],
  );
  const passedModels = useMemo(() => models.filter((item) => item.supported && item.probeStatus === "passed"), [models]);
  const runningServices = useMemo(() => services.filter((item) => item.state === "running"), [services]);
  const selectedEvaluationDataset = useMemo(
    () => evaluationDatasets.find((item) => item.id === selectedEvaluationDatasetId) ?? null,
    [evaluationDatasets, selectedEvaluationDatasetId],
  );
  const relevantYouyunImages = useMemo(() => {
    const images = youyunSpecs?.images ?? [];
    const filtered = images.filter((image) => image.type === "App" && /pytorch|torch|cuda|conda/i.test(image.name));
    const configured = images.find((image) => image.id === youyunSpecs?.imageId);
    return Array.from(new Map([...(configured ? [configured] : []), ...filtered].map((image) => [image.id, image])).values());
  }, [youyunSpecs]);
  const availableTabs = useMemo<ReadonlyArray<readonly [VisionTab, string]>>(() => {
    if (workspace === "training") return [["training", "训练配置"]];
    if (workspace === "models") return [["deploy", "模型登记"]];
    if (workspace === "services") return [["deploy", "推理服务"]];
    return [["deploy", "部署"], ["training", "训练前检"], ["inference", "推理"], ["evaluation", "验证评测"]];
  }, [workspace]);

  const refreshYouyunSpecs = useCallback(async () => {
    setYouyunLoading(true);
    setYouyunError("");
    try {
      const query = new URLSearchParams();
      query.set("instanceMode", "auto");
      if (selectedYouyunRegion) query.set("region", selectedYouyunRegion);
      if (selectedYouyunZone) query.set("zone", selectedYouyunZone);
      if (selectedYouyunGpuType) query.set("gpuType", selectedYouyunGpuType);
      if (selectedYouyunImageId) query.set("imageId", selectedYouyunImageId);
      const suffix = query.size ? `?${query.toString()}` : "";
      const specs = await visionRequest<YouyunGpuSpecs>(`/youyun/gpu-specs${suffix}`);
      setYouyunSpecs(specs);
      setYouyunZones(specs.zones ?? []);
      setSelectedYouyunRegion((current) => current || specs.region);
      setSelectedYouyunZone((current) => current || specs.zone || selectYouyunZone(specs.zones ?? [], specs.region, "pod"));
      setSelectedYouyunGpuType((current) => current || specs.gpuType);
      setSelectedYouyunImageId((current) => current || specs.recommendedImageId || specs.imageId);
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
  }, [selectedYouyunCpuCores, selectedYouyunGpuType, selectedYouyunImageId, selectedYouyunMemoryGb, selectedYouyunRegion, selectedYouyunZone]);

  useEffect(() => {
    if (tab === "training" && trainingForm.provider === "youyun" && !youyunSpecs && !youyunLoading && !youyunError) {
      void refreshYouyunSpecs();
    }
  }, [refreshYouyunSpecs, tab, trainingForm.provider, youyunError, youyunLoading, youyunSpecs]);

  useEffect(() => {
    if (trainingForm.provider !== "youyun" || !youyunSpecs || !selectedYouyunCpuCores || !selectedYouyunMemoryGb) {
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
      imageId: selectedYouyunImageId || youyunSpecs.imageId,
      instanceMode: youyunSpecs.instanceMode === "uhost" ? "uhost" : "pod",
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
  }, [selectedYouyunCpuCores, selectedYouyunGpuType, selectedYouyunImageId, selectedYouyunMemoryGb, trainingForm.provider, youyunSpecs]);

  const youyunReady = trainingForm.provider !== "youyun" || Boolean(youyunResource && !youyunError && !youyunPriceLoading);
  const pageMeta = {
    all: { title: "视觉理解", subtitle: "管理视觉模型部署、图生文推理、训练前检与测试集评测" },
    training: { title: "视觉理解微调任务", subtitle: "配置视觉模型微调参数，执行训练前检并生成训练配置" },
    models: { title: "视觉理解模型管理", subtitle: "登记视觉基座模型并验证服务器本地模型能力" },
    services: { title: "视觉理解推理服务", subtitle: "" },
  }[workspace];

  useEffect(() => {
    setTab(initialTab(workspace));
    setError("");
    setNotice("");
  }, [workspace]);

  const refresh = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    if (!quiet) setEvaluationDatasetsLoading(true);
    try {
      const [nextModels, nextServices, nextEvaluations, nextEvaluationDatasets, nextCatalog] = await Promise.all([
        visionRequest<VisionModel[]>("/vision/models?includeArchived=true"),
        visionRequest<VisionService[]>("/vision/deployments"),
        visionRequest<EvaluationJob[]>("/vision/evaluations"),
        visionRequest<EvaluationDataset[]>("/vision/evaluation-datasets").catch(() => []),
        visionRequest<VisionModel[]>("/vision/youyun-catalog").catch(() => []),
      ]);
      setModels(nextModels);
      setYouyunCatalog(nextCatalog);
      setServices(nextServices);
      setEvaluations(nextEvaluations);
      setEvaluationDatasets(nextEvaluationDatasets);
      setDeployForm((current) => ({ ...current, modelId: current.modelId || nextModels.find((item) => !item.archived && item.supported)?.id || "" }));
      setTrainingForm((current) => ({
        ...current,
        modelId: current.modelId || nextModels.find((item) => !item.archived && item.supported)?.id || "",
        datasetId: current.datasetId || visualDatasets[0]?.id || "",
      }));
      const runningId = nextServices.find((item) => item.state === "running")?.id || "";
      setSelectedServiceId((current) => current || runningId);
      setEvaluationForm((current) => ({ ...current, serviceId: current.serviceId || runningId }));
      setEvaluationForm((current) => {
        const selected = nextEvaluationDatasets.find((item) => item.datasetPath === current.datasetPath);
        if (selected) {
          return { ...current, metadataName: selected.metadataName };
        }
        if (!current.datasetPath && nextEvaluationDatasets.some((item) => item.valid)) {
          const fallback = nextEvaluationDatasets.find((item) => item.valid) ?? nextEvaluationDatasets[0];
          return { ...current, datasetPath: fallback.datasetPath, metadataName: fallback.metadataName };
        }
        return current;
      });
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "读取视觉理解工作台失败。");
    } finally {
      if (!quiet) setEvaluationDatasetsLoading(false);
      if (!quiet) setLoading(false);
    }
  }, [visualDatasets]);

  useEffect(() => {
    const selected = evaluationDatasets.find((item) => item.datasetPath === evaluationForm.datasetPath);
    const nextId = selected?.id ?? "__manual__";
    setSelectedEvaluationDatasetId((current) => current === nextId ? current : nextId);
  }, [evaluationDatasets, evaluationForm.datasetPath]);

  useEffect(() => { void refresh(); }, [refresh]);

  useEffect(() => {
    if (!evaluations.some((item) => ["queued", "running"].includes(item.status))) return;
    const timer = window.setInterval(() => void refresh(true), 3000);
    return () => window.clearInterval(timer);
  }, [evaluations, refresh]);

  async function registerModel(): Promise<boolean> {
    setError("");
    setNotice("");
    try {
      const model = await postJson<VisionModel>("/vision/models/register", modelForm);
      setNotice(model.supported ? "模型已登记并通过视觉配置探针。" : "模型已登记，但尚未通过本地视觉配置探针。请填写服务器可访问的模型目录。 ");
      await refresh(true);
      return true;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "模型登记失败。");
      return false;
    }
  }

  async function probeModel(modelId: string) {
    setError("");
    try {
      const model = await postJson<VisionModel>(`/vision/models/${modelId}/probe`, {});
      setNotice(model.supported ? "模型探针通过，可以创建视觉推理服务。" : model.probeMessage);
      await refresh(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "模型探针失败。");
    }
  }

  async function createDeployment() {
    setError("");
    setNotice("");
    try {
      const service = await postJson<VisionService & { message?: string }>("/vision/deployments", {
        name: deployForm.name,
        modelId: deployForm.modelId,
        maxModelLength: Number(deployForm.maxModelLength),
        gpuMemoryUtilization: Number(deployForm.gpuMemoryUtilization),
        tensorParallelSize: Number(deployForm.tensorParallelSize),
      });
      setNotice(service.message ?? "视觉推理服务配置已创建。");
      await refresh(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建部署失败。");
    }
  }

  async function serviceAction(serviceId: string, action: "start" | "stop") {
    setError("");
    setNotice("");
    try {
      await postJson(`/inference/services/${serviceId}/${action}`, {});
      setNotice(action === "start" ? "启动请求已完成，请以运行状态和健康检查为准。" : "服务已停止。 ");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "服务操作失败。");
    } finally {
      await refresh(true);
    }
  }

  async function archiveModel(modelId: string, archived: boolean) {
    setError("");
    try {
      await patchJson<VisionModel>(`/vision/models/${modelId}`, { archived });
      setNotice(archived ? "视觉模型已移入回收站。" : "视觉模型已恢复。 ");
      await refresh(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "更新视觉模型状态失败。");
    }
  }

  function youyunSelectionPayload() {
    if (!youyunResource) return undefined;
    return {
      mode: "manual" as const,
      gpuType: youyunResource.gpuType,
      gpuCount: 1,
      cpuCores: youyunResource.cpuCores,
      memoryGb: youyunResource.memoryGb,
      zone: youyunResource.zone,
      imageId: youyunResource.imageId,
    };
  }

  async function runPreflight() {
    setError("");
    setNotice("");
    setPreflight(null);
    setTrainingPreview(null);
    if (!youyunReady) {
      setError(youyunError || "请先完成优云 GPU、镜像和 CPU / 内存规格选择，等待价格查询完成后再执行前置检查。");
      return;
    }
    try {
      const result = await postJson<typeof preflight extends null ? never : NonNullable<typeof preflight>>("/vision/tasks/preflight", {
        datasetId: trainingForm.datasetId,
        modelId: trainingForm.modelId,
        provider: trainingForm.provider,
        tuningMode: trainingForm.tuningMode,
        batchSize: Number(trainingForm.batchSize),
        gradientAccumulation: Number(trainingForm.gradientAccumulation),
        validationSplit: Number(trainingForm.validationSplit),
        imageMaxPixels: Number(trainingForm.imageMaxPixels),
        youyunResource: trainingForm.provider === "youyun" ? youyunSelectionPayload() : undefined,
      });
      setPreflight(result);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "训练前置检查失败。");
    }
  }

  function trainingPayload() {
    const tuningLabel = trainingForm.tuningMode.toUpperCase();
    return {
      name: `视觉理解 ${tuningLabel} 训练任务`,
      taskType: "SFT",
      outputName: `vision-${trainingForm.tuningMode}-adapter-${Date.now()}`,
      datasetId: trainingForm.datasetId,
      modelId: trainingForm.modelId,
      provider: trainingForm.provider,
      tuningMode: trainingForm.tuningMode,
      batchSize: Number(trainingForm.batchSize),
      gradientAccumulation: Number(trainingForm.gradientAccumulation),
      validationSplit: Number(trainingForm.validationSplit),
      imageMaxPixels: Number(trainingForm.imageMaxPixels),
      epochs: Number(trainingForm.epochs),
      learningRate: Number(trainingForm.learningRate),
      loraRank: Number(trainingForm.loraRank),
      loraAlpha: Number(trainingForm.loraAlpha),
      loraDropout: Number(trainingForm.loraDropout),
      gradientCheckpointing: trainingForm.gradientCheckpointing,
      youyunResource: trainingForm.provider === "youyun" ? youyunSelectionPayload() : undefined,
    };
  }

  async function submitTrainingTask() {
    if (!preflight?.ready) return;
    setSubmittingTraining(true);
    setError("");
    try {
      const task = await postJson<{ id: string; state: string }>("/vision/tasks", trainingPayload());
      setSubmittedTraining(task);
      setShowTrainingConfirm(false);
      setNotice(`视觉训练任务 ${task.id} 已确认并进入队列。`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "提交视觉训练任务失败。");
    } finally {
      setSubmittingTraining(false);
    }
  }

  async function materializeTrainingPreview() {
    setError("");
    setNotice("");
    try {
      const result = await postJson<TrainingPreview>("/vision/tasks/materialize-preview", trainingPayload());
      setTrainingPreview(result);
      setNotice(`训练配置 ${result.previewId} 已生成，可在确认后提交视觉训练队列。`);
    } catch (reason) {
      setTrainingPreview(null);
      setError(reason instanceof Error ? reason.message : "视觉训练配置生成失败。");
    }
  }

  function updateTrainingForm(values: Partial<typeof trainingForm>) {
    setTrainingForm((current) => ({ ...current, ...values }));
    setPreflight(null);
    setTrainingPreview(null);
    setSubmittedTraining(null);
  }

  function chooseImage(file: File | null) {
    if (imagePreview) URL.revokeObjectURL(imagePreview);
    setImageFile(file);
    setImagePreview(file ? URL.createObjectURL(file) : "");
    setAnswer("");
  }

  async function sendVisionChat() {
    if (!imageFile || !selectedServiceId) return;
    setSending(true);
    setError("");
    setAnswer("");
    try {
      const imageBase64 = await fileDataUrl(imageFile);
      const result = await postJson<{ answer: string }>(`/inference/services/${selectedServiceId}/vision-chat`, {
        serviceId: selectedServiceId,
        imageBase64,
        imageFilename: imageFile.name,
        question,
        maxTokens: 512,
        temperature: 0.2,
        topP: 0.9,
        stream: false,
      });
      setAnswer(result.answer);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "视觉推理失败。");
    } finally {
      setSending(false);
    }
  }

  function evaluationPayload() {
    return {
      serviceId: evaluationForm.serviceId,
      datasetPath: evaluationForm.datasetPath,
      metadataName: evaluationForm.metadataName,
      maxSamples: evaluationForm.maxSamples ? Number(evaluationForm.maxSamples) : null,
      maxTokens: 256,
      temperature: 0,
      topP: 1,
    };
  }

  function evaluationValidationPayload() {
    return {
      datasetPath: evaluationForm.datasetPath,
      metadataName: evaluationForm.metadataName,
      maxSamples: evaluationForm.maxSamples ? Number(evaluationForm.maxSamples) : null,
    };
  }

  async function validateEvaluation() {
    setError("");
    setDatasetValidation(null);
    setValidatingEvaluation(true);
    try {
      setDatasetValidation(await postJson("/vision/evaluations/validate", evaluationValidationPayload()));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "评测集校验失败。");
    } finally {
      setValidatingEvaluation(false);
    }
  }

  async function createEvaluation() {
    setError("");
    setCreatingEvaluation(true);
    try {
      const job = await postJson<EvaluationJob>("/vision/evaluations", evaluationPayload());
      setNotice(`评测任务 ${job.id} 已创建。`);
      await refresh(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建评测任务失败。");
    } finally {
      setCreatingEvaluation(false);
    }
  }

  if (workspace === "models") {
    return (
      <section className="content vision-workbench">
        <RedesignedVisionModelManagementView
          models={models}
          catalogModels={youyunCatalog}
          loading={loading}
          notice={notice}
          error={error}
          modelForm={modelForm}
          onModelFormChange={(field, value) => setModelForm((current) => ({ ...current, [field]: value }))}
          onModelPresetSelect={(preset) => setModelForm((current) => applyVisionBaseModelPreset(current, preset))}
          onRegister={registerModel}
          onProbe={probeModel}
          onArchive={archiveModel}
          onRefresh={() => refresh()}
        />
      </section>
    );
  }

  return (
    <section className="content vision-workbench">
      <div className="title-row">
        <div><h1>{pageMeta.title}</h1><div className="subtitle">{pageMeta.subtitle}</div></div>
        <button className="secondary-btn" onClick={() => void refresh()} disabled={loading}>刷新状态</button>
      </div>

      {workspace === "all" && <div className="vision-stage-strip" aria-label="视觉理解交付流程">
        <span>1 模型探针</span><b>→</b><span>2 部署并启动</span><b>→</b><span>3 单图推理</span><b>→</b><span>4 测试集评测</span>
      </div>}

      {availableTabs.length > 1 && <div className="vision-tabs" role="tablist">
        {availableTabs.map(([value, label]) => (
          <button key={value} className={tab === value ? "active" : ""} onClick={() => { setTab(value); setError(""); setNotice(""); }}>{label}</button>
        ))}
      </div>}

      {workspace !== "services" && error && <div className="form-error vision-global-message">{error}</div>}
      {workspace !== "services" && notice && <div className="vision-success vision-global-message">{notice}</div>}

      {tab === "deploy" && workspace === "services" && (
        <VisionInferenceServiceModule
          models={models}
          services={services}
          loading={loading}
          externalError={error}
          externalNotice={notice}
          onRefresh={refresh}
          onOpenInference={(serviceId) => {
            setSelectedServiceId(serviceId);
            setTab("inference");
          }}
          onOpenEvaluation={(serviceId) => {
            setSelectedServiceId(serviceId);
            setEvaluationForm((current) => ({ ...current, serviceId }));
            setTab("evaluation");
          }}
        />
      )}

      {tab === "deploy" && workspace !== "services" && (
        <div className={workspace === "all" ? "vision-two-column" : "vision-single-column"}>
          <section className="panel vision-section">
            <div className="section-heading"><div><h2>视觉模型登记与探针</h2><p>只有服务器本地 config.json 明确包含视觉配置时，模型才会标记为可部署。</p></div><span className="section-index">01</span></div>
            <div className="vision-form-grid">
              <label><span>显示名称</span><input className="input" value={modelForm.name} onChange={(event) => setModelForm({ ...modelForm, name: event.target.value })} /></label>
              <label><span>模型仓库</span><input className="input" value={modelForm.repoId} onChange={(event) => setModelForm({ ...modelForm, repoId: event.target.value })} /></label>
              <label><span>Revision</span><input className="input" value={modelForm.revision} onChange={(event) => setModelForm({ ...modelForm, revision: event.target.value })} /></label>
              <label className="vision-field-wide"><span>服务器本地目录</span><input className="input" value={modelForm.path} onChange={(event) => setModelForm({ ...modelForm, path: event.target.value })} placeholder="例如：/data/models/Qwen2.5-VL-3B-Instruct" /></label>
            </div>
            <div className="vision-actions"><button className="primary-btn" onClick={() => void registerModel()} disabled={!modelForm.name.trim() || !modelForm.repoId.trim()}>登记并探针</button></div>
            <div className="vision-table-wrap">
              <table className="vision-table"><thead><tr><th>模型</th><th>探针</th><th>能力</th><th>操作</th></tr></thead><tbody>
                {models.map((model) => <tr key={model.id}><td><strong>{model.name}</strong><small>{model.repoId}</small></td><td><span className={`vision-badge ${model.supported ? "passed" : "candidate"}`}>{model.supported ? "已通过" : "待确认"}</span><small>{model.probeMessage}</small></td><td><small>{model.capabilities.join(" / ") || "未识别"}</small></td><td><button className="link-btn" onClick={() => void probeModel(model.id)}>重新探针</button></td></tr>)}
                {!models.length && <tr><td colSpan={4} className="empty">尚未登记视觉模型</td></tr>}
              </tbody></table>
            </div>
          </section>

          <section className="panel vision-section">
            <div className="section-heading"><div><h2>创建视觉推理服务</h2><p>创建配置不等于部署成功；服务必须启动并通过 vLLM 健康检查。</p></div><span className="section-index">02</span></div>
            <div className="vision-form-grid">
              <label className="vision-field-wide"><span>服务名称</span><input className="input" value={deployForm.name} onChange={(event) => setDeployForm({ ...deployForm, name: event.target.value })} /></label>
              <label className="vision-field-wide"><span>已通过探针的模型</span><select className="input" value={deployForm.modelId} onChange={(event) => setDeployForm({ ...deployForm, modelId: event.target.value })}><option value="">请选择模型</option>{passedModels.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}</select></label>
              <label><span>最大上下文</span><input className="input" type="number" value={deployForm.maxModelLength} onChange={(event) => setDeployForm({ ...deployForm, maxModelLength: event.target.value })} /></label>
              <label><span>GPU 显存利用率</span><input className="input" type="number" step="0.01" value={deployForm.gpuMemoryUtilization} onChange={(event) => setDeployForm({ ...deployForm, gpuMemoryUtilization: event.target.value })} /></label>
              <label><span>Tensor Parallel</span><input className="input" type="number" value={deployForm.tensorParallelSize} onChange={(event) => setDeployForm({ ...deployForm, tensorParallelSize: event.target.value })} /></label>
            </div>
            <div className="vision-actions"><button className="primary-btn" disabled={!deployForm.modelId || !deployForm.name.trim()} onClick={() => void createDeployment()}>创建部署配置</button></div>
            <div className="vision-service-list">
              {services.map((service) => <div className="vision-service-row" key={service.id}><div><strong>{service.name}</strong><small>{service.id} · {service.model}</small>{service.error && <small className="danger-text">{service.error}</small>}</div><span className={`vision-badge ${service.state === "running" ? "passed" : service.state === "failed" ? "failed" : "candidate"}`}>{statusLabel(service.state)}</span><div className="vision-row-actions">{service.state === "running" ? <button className="secondary-btn" onClick={() => void serviceAction(service.id, "stop")}>停止</button> : <button className="primary-btn" onClick={() => void serviceAction(service.id, "start")} disabled={["starting", "stopping"].includes(service.state)}>启动</button>}</div></div>)}
              {!services.length && <div className="empty">尚未创建视觉推理服务</div>}
            </div>
          </section>
        </div>
      )}

      {tab === "training" && (
        <section className="panel vision-section vision-single-panel">
          <div className="section-heading"><div><h2>视觉训练配置与前检</h2><p>先校验数据、模型和资源，再确认提交到视觉 LLaMA-Factory 训练队列；任务详情会持续展示 Loss、日志和 Adapter。</p></div><span className="section-index">01</span></div>
          <div className="vision-form-grid vision-training-grid">
            <label><span>已发布 IMAGE_TEXT 数据集</span><select className="input" value={trainingForm.datasetId} onChange={(event) => updateTrainingForm({ datasetId: event.target.value })}><option value="">请选择数据集</option>{visualDatasets.map((dataset) => <option key={dataset.id} value={dataset.id}>{dataset.name} / {dataset.count}</option>)}</select></label>
            <label><span>视觉基座模型</span><select className="input" value={trainingForm.modelId} onChange={(event) => updateTrainingForm({ modelId: event.target.value })}><option value="">请选择模型</option>{models.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}</select></label>
            <label><span>执行环境</span><select className="input" value={trainingForm.provider} onChange={(event) => { updateTrainingForm({ provider: event.target.value }); setYouyunError(""); setYouyunResource(null); }}><option value="youyun">远程优云 GPU</option><option value="local">本地服务器</option></select></label>
            <label><span>微调方式</span><select className="input" value={trainingForm.tuningMode} onChange={(event) => updateTrainingForm({ tuningMode: event.target.value })}><option value="qlora">QLoRA</option><option value="lora">LoRA</option></select></label>
            <label><span>训练轮次</span><input className="input" type="number" min="0.1" step="0.1" value={trainingForm.epochs} onChange={(event) => updateTrainingForm({ epochs: event.target.value })} /></label>
            <label><span>学习率</span><input className="input" type="number" min="0" step="0.000001" value={trainingForm.learningRate} onChange={(event) => updateTrainingForm({ learningRate: event.target.value })} /></label>
            <label><span>单卡 Batch Size</span><input className="input" type="number" min="1" value={trainingForm.batchSize} onChange={(event) => updateTrainingForm({ batchSize: event.target.value })} /></label>
            <label><span>梯度累积</span><input className="input" type="number" min="1" value={trainingForm.gradientAccumulation} onChange={(event) => updateTrainingForm({ gradientAccumulation: event.target.value })} /></label>
            <label><span>验证集比例 %</span><input className="input" type="number" min="0" max="30" value={trainingForm.validationSplit} onChange={(event) => updateTrainingForm({ validationSplit: event.target.value })} /></label>
            <label><span>图片最大像素</span><input className="input" type="number" min="1024" value={trainingForm.imageMaxPixels} onChange={(event) => updateTrainingForm({ imageMaxPixels: event.target.value })} /></label>
            <label><span>LoRA Rank</span><input className="input" type="number" min="1" max="256" value={trainingForm.loraRank} onChange={(event) => updateTrainingForm({ loraRank: event.target.value })} /></label>
            <label><span>LoRA Alpha</span><input className="input" type="number" min="1" max="512" value={trainingForm.loraAlpha} onChange={(event) => updateTrainingForm({ loraAlpha: event.target.value })} /></label>
            <label><span>LoRA Dropout</span><input className="input" type="number" min="0" max="0.99" step="0.01" value={trainingForm.loraDropout} onChange={(event) => updateTrainingForm({ loraDropout: event.target.value })} /></label>
            <label className="vision-checkbox-field"><input type="checkbox" checked={trainingForm.gradientCheckpointing} onChange={(event) => updateTrainingForm({ gradientCheckpointing: event.target.checked })} /><span>启用梯度检查点</span></label>
          </div>
          {trainingForm.provider === "youyun" && <div className="youyun-specs-panel vision-youyun-specs-panel">
            <div className="youyun-specs-head">
              <div><strong>优云 GPU 配置、环境镜像与实时库存</strong><small>{youyunSpecs ? `${youyunRegionLabel(youyunSpecs.region)} / ${youyunSpecs.zone} · ${youyunSpecs.chargeType === "Postpay" ? "按量计费" : youyunSpecs.chargeType}` : "正在查询可用资源..."}</small></div>
              <button type="button" className="secondary-btn compact-btn" disabled={youyunLoading} onClick={() => { setYouyunSpecs(null); setYouyunError(""); setYouyunResource(null); }}>{youyunLoading ? "查询中..." : "刷新库存"}</button>
            </div>
            {youyunError && <div className="youyun-specs-error">{youyunError}</div>}
            <div className="youyun-location-picker"><YouyunZonePicker zones={youyunZones.filter((zone) => zone.healthStatus !== "cooldown")} selectedZone={selectedYouyunZone} onSelect={(zone) => { setSelectedYouyunRegion(zone.region); setSelectedYouyunZone(zone.zone); setSelectedYouyunGpuType(""); setSelectedYouyunImageId(""); setSelectedYouyunCpuCores(null); setSelectedYouyunMemoryGb(null); setYouyunSpecs(null); setYouyunResource(null); setYouyunError(""); }} /></div>
            {!youyunSpecs && <div className="youyun-location-picker"><YouyunZonePicker zones={youyunZones.filter((zone) => zone.healthStatus !== "cooldown")} selectedZone={selectedYouyunZone} onSelect={(zone) => { setSelectedYouyunRegion(zone.region); setSelectedYouyunZone(zone.zone); setYouyunSpecs(null); setYouyunError(""); }} hint="可用区来自优云实时目录；读取失败时请刷新重试。" /></div>}
            {youyunSpecs && <>
              <div className="youyun-selection-summary"><strong>选择单卡 GPU、优云 PyTorch/CUDA 镜像和 CPU / 内存规格</strong><small>训练会优先直接复用镜像中的兼容 PyTorch 环境，只在缺少依赖时补装；多卡训练暂未开放。</small></div>
              <div className="youyun-machine-list">
                {Array.from(new Map(youyunSpecs.machineTypes.map((machine) => [`${machine.name}-${machine.graphicsMemoryGb}`, machine])).values()).map((machine) => (
                  <button type="button" key={`${machine.name}-${machine.graphicsMemoryGb}`} className={`youyun-machine-item${youyunMachineIsUnavailable(machine, "inference") ? " sold-out" : ""}${selectedYouyunGpuType === machine.name ? " selected" : ""}`} disabled={youyunMachineIsUnavailable(machine, "inference")} onClick={() => { setSelectedYouyunGpuType(machine.name); setSelectedYouyunCpuCores(null); setSelectedYouyunMemoryGb(null); setYouyunSpecs(null); setYouyunResource(null); setYouyunError(""); }}>
                    <strong>{machine.graphicsMemoryGb ? `${machine.graphicsMemoryGb}G` : "GPU"} {machine.name}</strong>
                    <small>{machine.graphicsMemoryGb ? `${machine.graphicsMemoryGb}GB 显存` : "显存待确认"}</small>
                    <YouyunInventoryIndicator machine={machine} />
                  </button>
                ))}
              </div>
              <label className="youyun-image-field"><span>优云训练环境镜像</span><select className="input" value={selectedYouyunImageId || youyunSpecs.recommendedImageId || youyunSpecs.imageId} onChange={(event) => { setSelectedYouyunImageId(event.target.value); setSelectedYouyunCpuCores(null); setSelectedYouyunMemoryGb(null); setYouyunSpecs(null); setYouyunResource(null); setYouyunError(""); }}><optgroup label="优云实时可用镜像">{relevantYouyunImages.length ? relevantYouyunImages.map((image) => <option key={image.id} value={image.id}>{image.name || image.id}{image.id === youyunSpecs.recommendedImageId ? " · 视觉训练推荐" : image.id === youyunSpecs.imageId ? " · 当前配置" : ""}</option>) : <option value={youyunSpecs.imageId}>{youyunSpecs.imageId || "当前优云默认镜像"}</option>}</optgroup>{Boolean(youyunSpecs.communityImages?.length) && <optgroup label="社区训练环境">{youyunSpecs.communityImages?.map((image) => { const verified = image.verificationState === "verified"; const status = verified ? "已验证，可选择" : image.verificationState === "failed" ? "验证失败" : image.available ? "已发现，待验证" : "当前区域无库存/未上架"; return <option key={`${image.id || "catalog"}:${image.name}:${image.versionName || ""}`} value={image.id || `catalog:${image.name}`} disabled={!image.available || !image.id || !verified}>{image.name}{image.versionName ? ` · ${image.versionName}` : ""} · {status}</option>; })}</optgroup>}</select><small>社区镜像会先显示真实版本和库存；只有探针验证通过后才允许用于训练，平台不会使用虚构镜像 ID。</small></label>
              <div className="youyun-capacity-list youyun-single-capacity-list">
                {youyunSpecs.capacity.filter((spec) => spec.gpu === 1).map((spec) => (
                  <button type="button" key={`${spec.gpu}-${spec.cpu}-${spec.memoryGb}`} disabled={!spec.available} className={`youyun-capacity-item${selectedYouyunCpuCores === spec.cpu && selectedYouyunMemoryGb === spec.memoryGb ? " selected" : ""}`} onClick={() => { setSelectedYouyunCpuCores(spec.cpu); setSelectedYouyunMemoryGb(spec.memoryGb); setYouyunResource(null); setYouyunError(""); }}>
                    <span>{spec.cpu} 核 / {spec.memoryGb}GB 内存</span>
                    <span className={spec.available ? "tag-ready" : "tag-off"}>{spec.available ? "可选" : "无库存"}</span>
                  </button>
                ))}
              </div>
              {youyunSpecs.capacity.some((spec) => spec.gpu !== 1) && <div className="youyun-multi-capacity-list"><span className="youyun-capacity-label">多卡规格（暂不支持）</span>{youyunSpecs.capacity.filter((spec) => spec.gpu !== 1).map((spec) => <span className="youyun-capacity-disabled" key={`${spec.gpu}-${spec.cpu}-${spec.memoryGb}`}>{spec.gpu} 卡 / {spec.cpu} 核 / {spec.memoryGb}GB</span>)}</div>}
              <div className="youyun-price-summary"><div><strong>预计按量费用</strong><span>{youyunPriceLoading ? "查询中..." : youyunResource ? `${youyunResource.price.currency === "CNY" ? "¥" : `${youyunResource.price.currency} `}${youyunResource.price.total.toFixed(2)} / 小时` : "暂不可获取"}</span></div>{youyunResource && <small>实例 ¥{youyunResource.price.instance.toFixed(2)} · 系统盘 ¥{youyunResource.price.systemDisks.toFixed(2)} · 数据盘 ¥{youyunResource.price.disks.toFixed(2)} · 镜像 ¥{youyunResource.price.image.toFixed(2)}；实际以优云账单为准</small>}</div>
              {!youyunSpecs.capacity.some((spec) => spec.gpu === 1 && spec.available) && <div className="youyun-specs-error">当前单卡规格无库存，请更换 GPU 或稍后刷新。</div>}
            </>}
          </div>}
          <div className="vision-actions"><button className="primary-btn" disabled={!trainingForm.datasetId || !trainingForm.modelId || submittingTraining || !youyunReady} onClick={() => void runPreflight()}>执行前置检查</button><button className="secondary-btn" disabled={!preflight?.ready || submittingTraining || !youyunReady} onClick={() => void materializeTrainingPreview()}>生成训练配置</button><button className="primary-btn" disabled={!preflight?.ready || submittingTraining || !youyunReady} onClick={() => setShowTrainingConfirm(true)}>{submittingTraining ? "正在提交" : "确认并开始训练"}</button></div>
          {preflight && <div className={`vision-preflight ${preflight.ready ? "ready" : "blocked"}`}><strong>{preflight.ready ? "配置检查通过" : "当前不可执行"}</strong><span>有效 Batch Size：{preflight.effectiveBatchSize}</span><p>{preflight.message}</p>{preflight.reasons.map((reason) => <p key={reason}>· {reason}</p>)}</div>}
          {trainingPreview && <div className="vision-config-preview"><div className="vision-config-head"><div><strong>训练配置已物化</strong><span>{trainingPreview.previewId} · LLaMA-Factory {trainingPreview.llamaFactoryContract}</span></div><span>{trainingPreview.manifest.trainSamples} 训练 / {trainingPreview.manifest.validationSamples} 验证 / {trainingPreview.manifest.referencedImages} 图片</span></div><p>{trainingPreview.configPath}</p><pre>{trainingPreview.yaml}</pre></div>}
          {submittedTraining && <div className="vision-success vision-global-message">任务 {submittedTraining.id} 已提交，当前状态：{statusLabel(submittedTraining.state)}。请到“微调任务 · 视觉理解”查看实时结果。</div>}
        </section>
      )}

      {tab === "inference" && (
        <div className="vision-inference-layout">
          <section className="panel vision-section">
            <div className="section-heading"><div><h2>输入图片与问题</h2><p>图片只以 Base64 发送到当前服务，不接受远程图片 URL。</p></div><span className="section-index">01</span></div>
            <label className="vision-file-picker">选择图片<input type="file" accept="image/jpeg,image/png,image/webp" onChange={(event) => chooseImage(event.target.files?.[0] ?? null)} /></label>
            {imagePreview ? <img className="vision-chat-image" src={imagePreview} alt="待推理图片预览" /> : <div className="vision-image-empty">尚未选择图片</div>}
            <label className="vision-block-label"><span>当前视觉推理服务</span>{selectedServiceId ? <div className="vision-service-context">{runningServices.find((service) => service.id === selectedServiceId)?.name || selectedServiceId}<button type="button" className="link-btn" onClick={() => setSelectedServiceId("")}>切换</button></div> : <select className="input" value={selectedServiceId} onChange={(event) => setSelectedServiceId(event.target.value)}><option value="">请选择服务</option>{runningServices.map((service) => <option key={service.id} value={service.id}>{service.name}</option>)}</select>}</label>
            <label className="vision-block-label"><span>问题</span><textarea className="textarea" rows={4} value={question} onChange={(event) => setQuestion(event.target.value)} /></label>
            <div className="vision-actions"><button className="primary-btn" disabled={!imageFile || !question.trim() || !selectedServiceId || sending} onClick={() => void sendVisionChat()}>{sending ? "推理中" : "开始推理"}</button></div>
          </section>
          <section className="panel vision-section vision-answer-panel">
            <div className="section-heading"><div><h2>模型回答</h2><p>返回内容来自当前运行的 vLLM 视觉模型。</p></div><span className="section-index">02</span></div>
            <div className={`vision-answer ${answer ? "has-answer" : ""}`}>{answer || "完成图片选择、服务选择和问题输入后，回答会显示在这里。"}</div>
          </section>
        </div>
      )}

      {tab === "evaluation" && (
        <div className="vision-two-column">
          <section className="panel vision-section">
            <div className="section-heading"><div><h2>测试集校验与执行</h2><p>路径必须位于项目目录或数据集根目录，评测前必须有运行中的视觉服务。</p></div><span className="section-index">01</span></div>
            <div className="vision-form-grid">
              <label className="vision-field-wide"><span>当前视觉推理服务</span>{evaluationForm.serviceId ? <div className="vision-service-context">{runningServices.find((service) => service.id === evaluationForm.serviceId)?.name || evaluationForm.serviceId}<button type="button" className="link-btn" onClick={() => setEvaluationForm((current) => ({ ...current, serviceId: "" }))}>切换</button></div> : <select className="input" value={evaluationForm.serviceId} onChange={(event) => setEvaluationForm({ ...evaluationForm, serviceId: event.target.value })}><option value="">请选择服务</option>{runningServices.map((service) => <option key={service.id} value={service.id}>{service.name}</option>)}</select>}</label>
              <label className="vision-field-wide"><span>测试集</span><select className="input" value={selectedEvaluationDatasetId} disabled={evaluationDatasetsLoading} onChange={(event) => {
                const value = event.target.value;
                setSelectedEvaluationDatasetId(value);
                setDatasetValidation(null);
                const dataset = evaluationDatasets.find((item) => item.id === value);
                if (dataset) setEvaluationForm((current) => ({ ...current, datasetPath: dataset.datasetPath, metadataName: dataset.metadataName }));
              }}>
                <option value="">{evaluationDatasetsLoading ? "正在读取测试集..." : "请选择测试集"}</option>
                {evaluationDatasets.map((dataset) => <option key={dataset.id} value={dataset.id}>{dataset.name} · {dataset.sampleCount} 条{dataset.valid ? "" : " · 需修复"}</option>)}
                <option value="__manual__">手动输入路径...</option>
              </select></label>
              {selectedEvaluationDatasetId === "__manual__" && <label className="vision-field-wide"><span>测试集目录</span><input className="input" value={evaluationForm.datasetPath} placeholder="例如 data/vision_understanding_eval_v1" onChange={(event) => { setEvaluationForm((current) => ({ ...current, datasetPath: event.target.value })); setDatasetValidation(null); }} /><small className="vision-field-help">路径必须位于项目目录或配置的数据集根目录内。</small></label>}
              <label><span>Metadata 文件</span><input className="input" value={evaluationForm.metadataName} onChange={(event) => setEvaluationForm({ ...evaluationForm, metadataName: event.target.value })} /></label>
              <label><span>最大样本数</span><input className="input" type="number" value={evaluationForm.maxSamples} onChange={(event) => setEvaluationForm({ ...evaluationForm, maxSamples: event.target.value })} /></label>
            </div>
            {selectedEvaluationDataset && <div className="vision-preflight vision-dataset-summary"><div className="vision-preflight-header"><strong>{selectedEvaluationDataset.name}</strong><span>{selectedEvaluationDataset.valid ? "可直接评测" : "需要修复"}</span></div><p>{selectedEvaluationDataset.description || "已发现的视觉理解评测集"}</p><div className="vision-preflight-grid"><div className="vision-preflight-item"><span>样本</span><strong>{selectedEvaluationDataset.sampleCount} 条</strong></div><div className="vision-preflight-item"><span>缺失图片</span><strong className={selectedEvaluationDataset.missingImages ? "blocked" : "pass"}>{selectedEvaluationDataset.missingImages} 张</strong></div><div className="vision-preflight-item"><span>Metadata</span><strong>{selectedEvaluationDataset.metadataName}</strong></div><div className="vision-preflight-item"><span>目录</span><strong>{selectedEvaluationDataset.datasetPath}</strong></div></div></div>}
            <div className="vision-actions"><button className="secondary-btn" disabled={validatingEvaluation || !evaluationForm.datasetPath} onClick={() => void validateEvaluation()}>{validatingEvaluation ? "校验中..." : "校验测试集"}</button><button className="primary-btn" disabled={creatingEvaluation || validatingEvaluation || !datasetValidation?.valid || !evaluationForm.serviceId} onClick={() => void createEvaluation()}>{creatingEvaluation ? "创建中..." : "开始评测"}</button></div>
            {datasetValidation && <div className={`vision-preflight ${datasetValidation.valid ? "ready" : "blocked"}`}><strong>{datasetValidation.valid ? "测试集校验通过" : "测试集不可用"}</strong><span>样本 {datasetValidation.sampleCount} 条 · 缺失图片 {datasetValidation.missingImages} 张</span><p>{datasetValidation.datasetPath}</p></div>}
          </section>
          <section className="panel vision-section">
            <div className="section-heading"><div><h2>评测记录</h2><p>指标包含 Exact Match、Token F1、失败样本数和平均时延。</p></div><div className="vision-section-heading-actions"><button type="button" className="secondary-btn compact-btn" disabled={loading} onClick={() => void refresh(true)}>刷新</button><span className="section-index">02</span></div></div>
            <div className="vision-evaluation-list">
              {evaluations.map((job) => <div className="vision-evaluation-row" key={job.id}><div className="vision-eval-head"><strong>{job.id}</strong><span className={`vision-badge ${job.status === "completed" ? "passed" : job.status === "failed" ? "failed" : "candidate"}`}>{statusLabel(job.status)}</span></div><small>{job.datasetPath}</small>{job.summary && <div className="vision-metrics"><span>样本 <b>{job.summary.total ?? 0}</b></span><span>EM <b>{((job.summary.exactMatch ?? 0) * 100).toFixed(1)}%</b></span><span>F1 <b>{((job.summary.tokenF1 ?? 0) * 100).toFixed(1)}%</b></span><span>失败 <b>{job.summary.failed ?? 0}</b></span><span>时延 <b>{Math.round(job.summary.averageLatencyMs ?? 0)}ms</b></span></div>}{job.error && <div className="field-error">{job.error}</div>}</div>)}
              {!evaluations.length && <div className="empty">尚未创建评测任务</div>}
            </div>
          </section>
        </div>
      )}
      {showTrainingConfirm && <div className="training-confirm-backdrop" role="presentation" onClick={() => !submittingTraining && setShowTrainingConfirm(false)}><div className="training-confirm-modal vision-submit-confirm" role="dialog" aria-modal="true" aria-labelledby="vision-workbench-submit-title" onClick={(event) => event.stopPropagation()}><div className="training-confirm-icon">✓</div><div><h2 id="vision-workbench-submit-title">确认提交视觉训练？</h2><p>确认后将按照当前前置检查结果进入{trainingForm.provider === "youyun" ? "优云 GPU" : "本地 GPU"}训练队列。</p><div className="vision-confirm-summary"><span><b>模型</b>{models.find((item) => item.id === trainingForm.modelId)?.name ?? "—"}</span><span><b>数据集</b>{visualDatasets.find((item) => item.id === trainingForm.datasetId)?.name ?? "—"}</span><span><b>方式</b>视觉 SFT · {trainingForm.tuningMode.toUpperCase()}</span><span><b>验证</b>{trainingForm.validationSplit}% · 图片 {trainingForm.imageMaxPixels} px</span>{youyunResource && <span><b>优云资源</b>{youyunResource.gpuType} ×1 · {youyunResource.cpuCores} 核 / {youyunResource.memoryGb}GB · ¥{youyunResource.price.total.toFixed(2)}/小时</span>}</div></div><div className="training-confirm-actions"><button type="button" className="secondary-btn" disabled={submittingTraining} onClick={() => setShowTrainingConfirm(false)}>返回修改</button><button type="button" className="primary-btn" disabled={submittingTraining || !youyunReady} onClick={() => void submitTrainingTask()}>{submittingTraining ? "正在提交" : "确认并开始训练"}</button></div></div></div>}
    </section>
  );
}
