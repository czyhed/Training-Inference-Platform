export type VisionBaseModelForm = {
  name: string;
  repoId: string;
  revision: string;
  path: string;
};

export type VisionBaseModelPreset = {
  id: string;
  name: string;
  repoId: string;
  revision: string;
  family: "Qwen2-VL" | "Qwen2.5-VL" | "Qwen3-VL" | "Qwen3-Omni";
  parameterScale: string;
  useCase: string;
  resourceHint: string;
  youyunAvailability: "ready" | "cache_required";
  recommended?: boolean;
};

export type VisionBaseModelRecommendationState = "registered" | VisionBaseModelPreset["youyunAvailability"];

export const VISION_BASE_MODEL_PRESETS: readonly VisionBaseModelPreset[] = [
  {
    id: "qwen25-vl-3b",
    name: "Qwen2.5-VL 3B Instruct",
    repoId: "Qwen/Qwen2.5-VL-3B-Instruct",
    revision: "main",
    family: "Qwen2.5-VL",
    parameterScale: "3B",
    useCase: "通用图像描述、视觉问答与 OCR，适合作为首次训练基模",
    resourceHint: "单卡训练优先，建议使用 24GB 显存与 QLoRA",
    youyunAvailability: "ready",
    recommended: true,
  },
  {
    id: "qwen25-vl-7b",
    name: "Qwen2.5-VL 7B Instruct",
    repoId: "Qwen/Qwen2.5-VL-7B-Instruct",
    revision: "main",
    family: "Qwen2.5-VL",
    parameterScale: "7B",
    useCase: "复杂视觉问答、文档理解和更高质量的细节识别",
    resourceHint: "建议 48GB 显存；24GB 环境需 QLoRA 并控制图像分辨率",
    youyunAvailability: "cache_required",
  },
  {
    id: "qwen2-vl-2b",
    name: "Qwen2-VL 2B Instruct",
    repoId: "Qwen/Qwen2-VL-2B-Instruct",
    revision: "main",
    family: "Qwen2-VL",
    parameterScale: "2B",
    useCase: "低成本流程验证、轻量图像描述与快速实验",
    resourceHint: "适合 16GB 至 24GB 显存环境进行 LoRA 或 QLoRA 验证",
    youyunAvailability: "cache_required",
  },
  {
    id: "qwen2-vl-7b",
    name: "Qwen2-VL 7B Instruct",
    repoId: "Qwen/Qwen2-VL-7B-Instruct",
    revision: "main",
    family: "Qwen2-VL",
    parameterScale: "7B",
    useCase: "成熟通用视觉理解，适合已有 Qwen2-VL 数据和任务迁移",
    resourceHint: "建议 48GB 显存；资源受限时使用 QLoRA",
    youyunAvailability: "cache_required",
  },
  {
    id: "qwen3-vl-2b",
    name: "Qwen3-VL 2B Instruct",
    repoId: "Qwen/Qwen3-VL-2B-Instruct",
    revision: "main",
    family: "Qwen3-VL",
    parameterScale: "2B",
    useCase: "轻量视觉问答、OCR 和新一代 Qwen3-VL 流程验证",
    resourceHint: "需缓存并复检训练环境，优先使用单卡 QLoRA",
    youyunAvailability: "cache_required",
  },
  {
    id: "qwen3-vl-4b",
    name: "Qwen3-VL 4B Instruct",
    repoId: "Qwen/Qwen3-VL-4B-Instruct",
    revision: "main",
    family: "Qwen3-VL",
    parameterScale: "4B",
    useCase: "兼顾效果与资源成本的文档、图表和通用视觉理解",
    resourceHint: "需缓存并复检训练环境，建议优云 24GB 以上显存",
    youyunAvailability: "cache_required",
  },
  {
    id: "qwen3-vl-8b",
    name: "Qwen3-VL 8B Instruct",
    repoId: "Qwen/Qwen3-VL-8B-Instruct",
    revision: "main",
    family: "Qwen3-VL",
    parameterScale: "8B",
    useCase: "复杂视觉推理、长文档和高质量多模态任务",
    resourceHint: "需缓存并复检训练环境，建议优云大显存 GPU 与 QLoRA",
    youyunAvailability: "cache_required",
  },
  {
    id: "qwen25-vl-72b",
    name: "Qwen2.5-VL 72B Instruct",
    repoId: "Qwen/Qwen2.5-VL-72B-Instruct",
    revision: "main",
    family: "Qwen2.5-VL",
    parameterScale: "72B",
    useCase: "复杂文档理解、图像问答和高质量视觉推理",
    resourceHint: "优云共享模型约 137GB，建议 80GB 以上显存并先执行远端探针",
    youyunAvailability: "ready",
  },
  {
    id: "qwen3-vl-32b-thinking-fp8",
    name: "Qwen3-VL 32B Thinking FP8",
    repoId: "Qwen/Qwen3-VL-32B-Thinking-FP8",
    revision: "main",
    family: "Qwen3-VL",
    parameterScale: "32B FP8",
    useCase: "视觉推理、图表和长文档理解",
    resourceHint: "优云共享模型约 33GB，建议 48GB 以上显存并先执行远端探针",
    youyunAvailability: "ready",
  },
  {
    id: "qwen3-omni-30b",
    name: "Qwen3-Omni 30B A3B Instruct",
    repoId: "Qwen/Qwen3-Omni-30B-A3B-Instruct",
    revision: "main",
    family: "Qwen3-Omni",
    parameterScale: "30B A3B",
    useCase: "视觉、多模态和跨模态理解",
    resourceHint: "优云共享模型约 66GB，建议 80GB 以上显存并先执行远端探针",
    youyunAvailability: "ready",
  },
];

export const DEFAULT_VISION_BASE_MODEL_FORM: VisionBaseModelForm = {
  name: VISION_BASE_MODEL_PRESETS[0].name,
  repoId: VISION_BASE_MODEL_PRESETS[0].repoId,
  revision: VISION_BASE_MODEL_PRESETS[0].revision,
  path: "",
};

export function applyVisionBaseModelPreset(
  current: VisionBaseModelForm,
  preset: VisionBaseModelPreset,
): VisionBaseModelForm {
  return {
    name: preset.name,
    repoId: preset.repoId,
    revision: preset.revision,
    path: current.path,
  };
}

export function visionBaseModelRecommendationState(
  preset: VisionBaseModelPreset,
  registeredRepoIds: readonly string[],
): VisionBaseModelRecommendationState {
  const normalizedRepoId = preset.repoId.trim().toLowerCase();
  const registered = registeredRepoIds.some((repoId) => repoId.trim().toLowerCase() === normalizedRepoId);
  return registered ? "registered" : preset.youyunAvailability;
}
