export type DatasetType = "文本生成" | "文生图" | "视觉理解";
export type DatasetPurpose = "training" | "evaluation";

const IMAGE_EXTENSIONS = ["jpg", "jpeg", "png", "webp"];
const TEXT_EXTENSIONS = ["jsonl"];
const ARCHIVE_EXTENSIONS = ["zip"];
const IMAGE_MAX_BYTES = 20 * 1024 * 1024;
const IMAGE_GENERATION_MAX_FILES = 200;
const ARCHIVE_MAX_BYTES = 2 * 1024 * 1024 * 1024;
const TEXT_MAX_BYTES = 2 * 1024 * 1024 * 1024;
const EVALUATION_MAX_BYTES = 200 * 1024 * 1024;
const EVALUATION_MAX_FILES = 10;

function extensionOf(fileName: string): string {
  return fileName.split(".").pop()?.toLowerCase() ?? "";
}

export function validateDatasetUploadFiles(
  datasetType: DatasetType,
  datasetPurpose: DatasetPurpose,
  fileList: File[],
): string {
  const allowed =
    datasetType === "文生图"
      ? IMAGE_EXTENSIONS
      : datasetType === "视觉理解"
        ? ARCHIVE_EXTENSIONS
        : TEXT_EXTENSIONS;
  const invalidFormat = fileList.find((file) => !allowed.includes(extensionOf(file.name)));
  if (invalidFormat) return `文件 ${invalidFormat.name} 格式不支持，请上传 ${allowed.join(" / ")} 文件。`;
  if (datasetType === "文生图" && fileList.length > IMAGE_GENERATION_MAX_FILES) {
    return `单个文生图数据集最多上传 ${IMAGE_GENERATION_MAX_FILES} 张图片。`;
  }
  const oversizedImage = datasetType === "文生图" && fileList.find((file) => file.size > IMAGE_MAX_BYTES);
  if (oversizedImage) return `图片 ${oversizedImage.name} 超过 20MB。`;
  if (datasetPurpose === "evaluation" && fileList.length > EVALUATION_MAX_FILES) {
    return `单个评测集最多上传 ${EVALUATION_MAX_FILES} 个 JSONL 文件。`;
  }
  if (datasetPurpose === "evaluation" && fileList.some((file) => file.size > EVALUATION_MAX_BYTES)) {
    return "评测集单个文件不能超过 200MB。";
  }
  if (datasetType === "文本生成" && datasetPurpose !== "evaluation") {
    const oversized = fileList.find((file) => file.size > TEXT_MAX_BYTES);
    if (oversized) return `单个数据文件不能超过 2GB。`;
  }
  if (datasetType === "视觉理解") {
    const oversizedArchive = fileList.find((file) => file.size > ARCHIVE_MAX_BYTES);
    if (oversizedArchive) return `视觉理解数据包不能超过 2GB。`;
  }
  return "";
}
