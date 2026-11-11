import { unzipSync } from "fflate";

export type ImageGenerationUploadMode = "images" | "archive";

export type CaptionSeed = {
  value: string;
  explicit: boolean;
};

export type ImageGenerationArchiveSummary = {
  archiveName: string;
  imageCount: number;
  metadataPresent: boolean;
  metadataRows: number;
  mappedCaptionCount: number;
  emptyCaptionCount: number;
  autoFilledCount: number;
};

export const IMAGE_GENERATION_MAX_ARCHIVE_BYTES = 200 * 1024 * 1024;

function imageMime(fileName: string) {
  const extension = fileName.split(".").pop()?.toLowerCase();
  return extension === "jpg" || extension === "jpeg" ? "image/jpeg" : extension === "webp" ? "image/webp" : "image/png";
}

function unsafeArchiveEntry(entryName: string) {
  const normalized = entryName.replaceAll("\\", "/");
  return normalized.startsWith("/")
    || /^[a-zA-Z]:\//.test(normalized)
    || normalized.includes("\0")
    || normalized.split("/").some((part) => part === "..");
}

function archiveImageName(entryName: string) {
  const normalized = entryName.replaceAll("\\", "/").replace(/^\.\//, "");
  const parts = normalized.split("/");
  if (parts.length < 2 || parts[0] !== "images" || parts.some((part) => !part || part === "." || part === "..")) return null;
  const fileName = parts.at(-1) ?? "";
  const extension = fileName.split(".").pop()?.toLowerCase() ?? "";
  return ["jpg", "jpeg", "png", "webp"].includes(extension) ? fileName : null;
}

export function isImageGenerationArchive(file: Pick<File, "name" | "type">) {
  return file.name.toLowerCase().endsWith(".zip") || file.type === "application/zip";
}

export function validateImageGenerationUploadSelection(
  mode: ImageGenerationUploadMode,
  files: Array<Pick<File, "name" | "type">>,
) {
  if (!files.length) return "";
  if (mode === "archive") {
    return files.length === 1 && isImageGenerationArchive(files[0]) ? "" : "ZIP 数据包模式仅接受一个 ZIP 压缩包。";
  }
  return files.some(isImageGenerationArchive) ? "当前为图片直传，请切换到 ZIP 数据包后重新选择。" : "";
}

export async function parseImageGenerationArchive(file: File) {
  if (file.size > IMAGE_GENERATION_MAX_ARCHIVE_BYTES) throw new Error("ZIP 压缩包不能超过 200MB。");

  let entries: Record<string, Uint8Array>;
  try {
    entries = unzipSync(new Uint8Array(await file.arrayBuffer()));
  } catch {
    throw new Error("ZIP 压缩包无法读取，请确认文件未损坏。");
  }

  const unsafeEntry = Object.keys(entries).find(unsafeArchiveEntry);
  if (unsafeEntry) throw new Error(`ZIP 中存在不安全路径：${unsafeEntry}`);

  const captions = new Map<string, CaptionSeed>();
  const metadataReferences = new Set<string>();
  const metadataEntry = Object.entries(entries).find(([entryName]) => entryName.replaceAll("\\", "/").split("/").at(-1) === "metadata.jsonl");
  let metadataRows = 0;
  let mappedCaptionCount = 0;
  let emptyCaptionCount = 0;

  if (metadataEntry) {
    let text: string;
    try {
      text = new TextDecoder("utf-8", { fatal: true }).decode(metadataEntry[1]);
    } catch {
      throw new Error("metadata.jsonl 不是有效的 UTF-8 文本，请转换编码后重新压缩。");
    }

    for (const [index, line] of text.split(/\r?\n/).entries()) {
      if (!line.trim()) continue;
      let record: unknown;
      try {
        record = JSON.parse(line);
      } catch {
        throw new Error(`metadata.jsonl 第 ${index + 1} 行不是有效 JSON。`);
      }
      if (!record || typeof record !== "object") throw new Error(`metadata.jsonl 第 ${index + 1} 行必须是对象。`);

      const value = record as Record<string, unknown>;
      const rawName = String(value.file_name ?? value.image ?? "").replaceAll("\\", "/");
      if (!rawName) throw new Error(`metadata.jsonl 第 ${index + 1} 行缺少 file_name。`);
      if (unsafeArchiveEntry(rawName)) throw new Error(`metadata.jsonl 第 ${index + 1} 行包含不安全图片路径。`);

      const fileName = rawName.split("/").at(-1) ?? "";
      const key = fileName.toLowerCase();
      if (!fileName) throw new Error(`metadata.jsonl 第 ${index + 1} 行的 file_name 无效。`);
      if (metadataReferences.has(key)) throw new Error(`metadata.jsonl 第 ${index + 1} 行重复引用图片：${fileName}`);
      metadataReferences.add(key);
      metadataRows += 1;

      const hasCaption = Object.prototype.hasOwnProperty.call(value, "caption") || Object.prototype.hasOwnProperty.call(value, "text");
      if (hasCaption) {
        const caption = String(value.caption ?? value.text ?? "").trim();
        captions.set(key, { value: caption, explicit: true });
        if (caption) mappedCaptionCount += 1;
        else emptyCaptionCount += 1;
      }
    }
  }

  const files: File[] = [];
  const seen = new Set<string>();
  for (const [entryName, bytes] of Object.entries(entries)) {
    const fileName = archiveImageName(entryName);
    if (!fileName) continue;
    const key = fileName.toLowerCase();
    if (seen.has(key)) throw new Error(`ZIP 中存在重复图片文件名：${fileName}`);
    seen.add(key);
    const blob = new Blob([bytes as unknown as BlobPart], { type: imageMime(fileName) });
    files.push(new File([blob], fileName, { type: imageMime(fileName), lastModified: file.lastModified }));
  }

  if (!files.length) throw new Error("ZIP 中没有找到 images/ 目录下的 JPG、JPEG、PNG 或 WebP 图片。");
  const missingReference = Array.from(metadataReferences).find((key) => !seen.has(key));
  if (missingReference) throw new Error(`metadata.jsonl 引用了不存在的图片：${missingReference}`);

  const summary: ImageGenerationArchiveSummary = {
    archiveName: file.name,
    imageCount: files.length,
    metadataPresent: Boolean(metadataEntry),
    metadataRows,
    mappedCaptionCount,
    emptyCaptionCount,
    autoFilledCount: files.filter((image) => !captions.has(image.name.toLowerCase())).length,
  };

  return { files, captions, summary };
}
