"use client";

import { ChangeEvent, DragEvent, useEffect, useMemo, useRef, useState } from "react";
import { Search, Trash2, X } from "lucide-react";
import { apiUrl } from "../lib/api-config";
import {
  parseImageGenerationArchive,
  validateImageGenerationUploadSelection,
} from "../lib/image-generation-upload";
import type {
  CaptionSeed,
  ImageGenerationArchiveSummary,
  ImageGenerationUploadMode,
} from "../lib/image-generation-upload";
import { Dataset } from "../lib/types";
import { BackButton } from "./back-button";

type ImageRow = {
  id: string;
  file: File;
  preview: string;
  width: number | null;
  height: number | null;
  caption: string;
  error: string;
};

type ProcessingStage = "idle" | "parsing" | "uploading" | "validating" | "complete";

type Props = {
  onCreated: (dataset: Dataset) => void;
  onCancel: () => void;
};

const MAX_BYTES = 20 * 1024 * 1024;
const MAX_FILES = 200;
const IMAGE_ACCEPT = "image/jpeg,image/png,image/webp";
const ARCHIVE_ACCEPT = ".zip,application/zip";

function bytesLabel(bytes: number) {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function readDimensions(file: File) {
  return new Promise<{ width: number; height: number }>((resolve, reject) => {
    const image = new Image();
    const url = URL.createObjectURL(file);
    image.onload = () => { URL.revokeObjectURL(url); resolve({ width: image.naturalWidth, height: image.naturalHeight }); };
    image.onerror = () => { URL.revokeObjectURL(url); reject(new Error("图片无法读取")); };
    image.src = url;
  });
}

function fileToBase64(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] ?? "");
    reader.onerror = () => reject(new Error("图片读取失败"));
    reader.readAsDataURL(file);
  });
}

function rowHasIssue(row: ImageRow) {
  return Boolean(row.error || !row.caption.trim());
}

function stageMessage(stage: ProcessingStage, progress: number) {
  if (stage === "parsing") return "正在本地解析图片并准备上传数据...";
  if (stage === "uploading") return `正在上传图片 ${progress}%`;
  if (stage === "validating") return "上传完成，正在执行服务端校验...";
  if (stage === "complete") return "校验完成，正在返回数据集列表...";
  return "";
}

export function ImageGenerationModule({ onCreated, onCancel }: Props) {
  const [name, setName] = useState("");
  const [rows, setRows] = useState<ImageRow[]>([]);
  const [uploadMode, setUploadMode] = useState<ImageGenerationUploadMode>("images");
  const [archiveSummary, setArchiveSummary] = useState<ImageGenerationArchiveSummary | null>(null);
  const [error, setError] = useState("");
  const [processing, setProcessing] = useState(false);
  const [parsingFiles, setParsingFiles] = useState(false);
  const [progress, setProgress] = useState(0);
  const [stage, setStage] = useState<ProcessingStage>("idle");
  const [dragActive, setDragActive] = useState(false);
  const [showFormatGuide, setShowFormatGuide] = useState(false);
  const [query, setQuery] = useState("");
  const [issuesOnly, setIssuesOnly] = useState(false);
  const rowsRef = useRef(rows);
  rowsRef.current = rows;

  useEffect(() => () => {
    rowsRef.current.forEach((row) => URL.revokeObjectURL(row.preview));
  }, []);

  const invalidCount = useMemo(() => rows.filter(rowHasIssue).length, [rows]);
  const emptyCaptionCount = useMemo(() => rows.filter((row) => !row.caption.trim()).length, [rows]);
  const validCount = rows.length - invalidCount;
  const totalBytes = useMemo(() => rows.reduce((total, row) => total + row.file.size, 0), [rows]);
  const visibleRows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return rows.filter((row) => {
      if (issuesOnly && !rowHasIssue(row)) return false;
      return !needle || [row.file.name, row.caption, row.error].join(" ").toLowerCase().includes(needle);
    });
  }, [issuesOnly, query, rows]);
  const busy = processing || parsingFiles;

  async function addFiles(input: File[], captions = new Map<string, CaptionSeed>()) {
    setError("");
    if (!input.length) return;
    if (input.length + rows.length > MAX_FILES) { setError(`单个文生图数据集最多上传 ${MAX_FILES} 张图片。`); return; }
    setParsingFiles(true);
    setStage("parsing");
    const existingNames = new Set(rows.map((row) => row.file.name.toLowerCase()));
    const batchNames = new Set<string>();
    const next: ImageRow[] = [];
    try {
      for (const file of input) {
        const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
        const key = file.name.toLowerCase();
        let rowError = ["jpg", "jpeg", "png", "webp"].includes(ext) ? "" : "仅支持 JPG、JPEG、PNG、WebP。";
        if (!rowError && (existingNames.has(key) || batchNames.has(key))) rowError = "文件名重复，请重命名后重新添加。";
        if (!rowError && file.size > MAX_BYTES) rowError = "单张图片不能超过 20MB。";
        let dimensions: { width: number; height: number } | null = null;
        if (!rowError) {
          try { dimensions = await readDimensions(file); } catch { rowError = "图片无法读取。"; }
        }
        const captionSeed = captions.get(key);
        batchNames.add(key);
        next.push({
          id: `${file.name}-${file.lastModified}-${Math.random()}`,
          file,
          preview: URL.createObjectURL(file),
          width: dimensions?.width ?? null,
          height: dimensions?.height ?? null,
          caption: captionSeed ? captionSeed.value : file.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " "),
          error: rowError,
        });
      }
      setRows((current) => [...current, ...next]);
    } finally {
      setParsingFiles(false);
      setStage("idle");
    }
  }

  async function addArchive(file: File) {
    setError("");
    setParsingFiles(true);
    setStage("parsing");
    try {
      if (rows.length) throw new Error("ZIP 需要单独上传，请先清空已选择的图片。");
      const unpacked = await parseImageGenerationArchive(file);
      if (unpacked.files.length > MAX_FILES) throw new Error(`单个文生图数据集最多上传 ${MAX_FILES} 张图片。`);
      setArchiveSummary(unpacked.summary);
      setParsingFiles(false);
      await addFiles(unpacked.files, unpacked.captions);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "ZIP 压缩包读取失败。");
    } finally {
      setParsingFiles(false);
      setStage("idle");
    }
  }

  function addInput(input: File[]) {
    const selectionError = validateImageGenerationUploadSelection(uploadMode, input);
    if (selectionError) {
      setError(selectionError);
      return;
    }
    setError("");
    if (uploadMode === "archive") void addArchive(input[0]);
    else void addFiles(input);
  }

  function changeUploadMode(nextMode: ImageGenerationUploadMode) {
    if (nextMode === uploadMode || busy) return;
    if (rows.length || archiveSummary) {
      setError("切换上传方式前请先清空当前已选内容。");
      return;
    }
    setUploadMode(nextMode);
    setError("");
    setDragActive(false);
  }

  function onInput(event: ChangeEvent<HTMLInputElement>) {
    const input = Array.from(event.target.files ?? []);
    event.target.value = "";
    addInput(input);
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragActive(false);
    if (busy) return;
    addInput(Array.from(event.dataTransfer.files));
  }

  function updateCaption(id: string, caption: string) {
    setRows((current) => current.map((row) => row.id === id ? { ...row, caption } : row));
  }

  function removeRow(id: string) {
    const row = rows.find((item) => item.id === id);
    if (row) URL.revokeObjectURL(row.preview);
    const nextRows = rows.filter((item) => item.id !== id);
    setRows(nextRows);
    if (!nextRows.length) setArchiveSummary(null);
  }

  function clearRows() {
    rows.forEach((row) => URL.revokeObjectURL(row.preview));
    setRows([]);
    setArchiveSummary(null);
    setQuery("");
    setIssuesOnly(false);
    setError("");
    setProgress(0);
    setStage("idle");
  }

  function fillEmptyCaptions() {
    setRows((current) => current.map((row) => row.caption.trim() ? row : {
      ...row,
      caption: row.file.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " "),
    }));
    setError("");
  }

  function removeIssueRows() {
    rows.filter(rowHasIssue).forEach((row) => URL.revokeObjectURL(row.preview));
    const nextRows = rows.filter((row) => !rowHasIssue(row));
    setRows(nextRows);
    if (!nextRows.length) setArchiveSummary(null);
    setIssuesOnly(false);
    setError("");
  }

  function uploadDataset(files: Array<{ file_name: string; content_base64: string; caption: string }>) {
    return new Promise<Dataset>((resolve, reject) => {
      const params = new URLSearchParams({ name: name.trim(), structure: "IMAGE_TEXT", datasetType: "文生图" });
      const request = new XMLHttpRequest();
      request.open("POST", `${apiUrl("/datasets/upload")}?${params.toString()}`);
      request.withCredentials = true;
      request.timeout = 30 * 60 * 1000;
      request.setRequestHeader("Content-Type", "application/json");
      request.upload.onprogress = (event) => {
        if (event.lengthComputable) setProgress(Math.round((event.loaded / event.total) * 100));
      };
      request.upload.onload = () => {
        setProgress(100);
        setStage("validating");
      };
      request.onerror = () => reject(new Error("上传失败，请检查网络和训练服务后重试。"));
      request.ontimeout = () => reject(new Error("上传或校验超时，请减少图片数量后重试。"));
      request.onload = () => {
        let body: Record<string, unknown> = {};
        try { body = JSON.parse(request.responseText); } catch { /* Use the HTTP status fallback. */ }
        if (request.status >= 200 && request.status < 300) resolve(body as Dataset);
        else reject(new Error(typeof body.detail === "string" ? body.detail : `上传失败（${request.status}）`));
      };
      setStage("uploading");
      request.send(JSON.stringify({ files }));
    });
  }

  async function submit() {
    if (!name.trim()) { setError("请填写数据集名称。"); return; }
    if (!rows.length) { setError("请先选择图片。"); return; }
    if (invalidCount) { setIssuesOnly(true); setError("请先修正异常图片并补充 Caption。"); return; }
    setProcessing(true);
    setProgress(0);
    setError("");
    setStage("parsing");
    try {
      const files = [];
      for (const row of rows) {
        files.push({ file_name: row.file.name, content_base64: await fileToBase64(row.file), caption: row.caption.trim() });
      }
      const dataset = await uploadDataset(files);
      setStage("complete");
      onCreated(dataset);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建数据集失败。");
      setStage("idle");
    } finally {
      setProcessing(false);
    }
  }

  return <section className="content image-generation-workspace">
    <div className="title-row"><div><BackButton label="返回数据集列表" onClick={onCancel} disabled={busy} /><h1>新建数据集 · 文生图</h1><div className="subtitle">上传参考图片、校对 Caption，由系统完成格式检查和数据集初始化</div></div></div>
    <div className="image-generation-create-layout">
      <div className="panel image-generation-panel">
        <div className="field"><label className="field-label">数据集名称 <span className="required">*</span></label><input className="input" value={name} onChange={(event) => { setName(event.target.value); setError(""); }} placeholder="例如：商品主图 Caption v1" disabled={busy} /></div>
        <div className="field"><div className="field-label-row"><label className="field-label">数据格式</label><button type="button" className="text-action" onClick={() => setShowFormatGuide(true)}>格式要求与示例</button></div><div className="hint"><strong>图片直传或 ZIP 数据包</strong>两种方式使用同一套图片与 Caption 校验；ZIP 支持 images/ 和 metadata.jsonl，散图默认按文件名预填 Caption。</div></div>
        <div className="field">
          <label className="field-label">上传方式</label>
          <div className="segmented image-generation-upload-mode" role="group" aria-label="上传方式">
            <button type="button" className={uploadMode === "images" ? "selected" : ""} onClick={() => changeUploadMode("images")} disabled={busy}><strong>图片直传</strong><span>批量选择 JPG、PNG、WebP</span></button>
            <button type="button" className={uploadMode === "archive" ? "selected" : ""} onClick={() => changeUploadMode("archive")} disabled={busy}><strong>ZIP 数据包</strong><span>解析 images/ 与 metadata.jsonl</span></button>
          </div>
        </div>
        <div className="field">
          <label className="field-label">上传文件 <span className="required">*</span></label>
          <div className={`dropzone image-generation-dropzone ${dragActive ? "drag-active" : ""} ${busy ? "is-uploading" : ""}`} onDragEnter={(event) => { event.preventDefault(); if (!busy) setDragActive(true); }} onDragOver={(event) => event.preventDefault()} onDragLeave={() => setDragActive(false)} onDrop={onDrop}>
            <div className="dropzone-title">{uploadMode === "images" ? "拖拽图片到这里，或从本地批量选择" : "拖拽一个 ZIP 数据包到这里，或从本地选择"}</div>
            <div className="dropzone-sub">{uploadMode === "images" ? "支持 JPG、JPEG、PNG、WebP；单图不超过 20MB，最多 200 张" : "ZIP 不超过 200MB；必须包含 images/，metadata.jsonl 可选"}</div>
            <label className="secondary-btn file-pick">{uploadMode === "images" ? (rows.length ? "继续添加图片" : "选择图片") : "选择 ZIP 数据包"}<input key={uploadMode} type="file" hidden multiple={uploadMode === "images"} accept={uploadMode === "images" ? IMAGE_ACCEPT : ARCHIVE_ACCEPT} onChange={onInput} disabled={busy} /></label>
          </div>
        </div>

        {archiveSummary && <div className="image-generation-archive-summary" aria-label="ZIP 解析摘要">
          <div className="image-generation-archive-summary-head"><div><strong>{archiveSummary.archiveName}</strong><span>{archiveSummary.metadataPresent ? "已读取 metadata.jsonl" : "未包含 metadata.jsonl，Caption 将按文件名自动补全"}</span></div><span>ZIP 解析完成</span></div>
          <div className="image-generation-archive-metrics"><div><strong>{archiveSummary.imageCount}</strong><span>图片数量</span></div><div><strong>{archiveSummary.metadataRows}/{archiveSummary.imageCount}</strong><span>元数据覆盖</span></div><div><strong>{archiveSummary.mappedCaptionCount}</strong><span>有效 Caption</span></div><div><strong>{archiveSummary.autoFilledCount}</strong><span>自动补全</span></div><div className={invalidCount ? "has-error" : ""}><strong>{invalidCount}</strong><span>当前异常</span></div></div>
        </div>}

        {rows.length > 0 && <>
          <div className="image-generation-upload-summary" aria-label="文件校验统计"><div><strong>{rows.length}</strong><span>图片总数</span></div><div><strong>{validCount}</strong><span>有效图片</span></div><div className={invalidCount ? "has-error" : ""}><strong>{invalidCount}</strong><span>异常项</span></div><div><strong>{bytesLabel(totalBytes)}</strong><span>文件总大小</span></div></div>
          <div className="image-generation-table-toolbar"><div className="image-generation-search"><Search size={15} aria-hidden="true" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索文件名或 Caption" aria-label="搜索文件名或 Caption" /></div><div className="segmented compact"><button type="button" className={!issuesOnly ? "selected" : ""} onClick={() => setIssuesOnly(false)}>全部 {rows.length}</button><button type="button" className={issuesOnly ? "selected" : ""} onClick={() => setIssuesOnly(true)}>仅看异常 {invalidCount}</button></div><div className="image-generation-batch-actions"><button type="button" className="secondary-btn compact-btn" onClick={fillEmptyCaptions} disabled={busy || !emptyCaptionCount}>按文件名补全空 Caption</button><button type="button" className="secondary-btn compact-btn danger-text-btn" onClick={removeIssueRows} disabled={busy || !invalidCount}><Trash2 size={14} />删除异常项</button><button type="button" className="secondary-btn compact-btn" onClick={clearRows} disabled={busy}><Trash2 size={14} />清空</button></div></div>
          <div className="image-generation-table"><div className="image-generation-table-head"><span>缩略图</span><span>文件信息</span><span>Caption</span><span>校验结果</span><span>操作</span></div>{visibleRows.length ? visibleRows.map((row) => <div className={`image-generation-row ${rowHasIssue(row) ? "has-error" : ""}`} key={row.id}><img src={row.preview} alt={row.file.name} /><div className="image-generation-file-info"><strong title={row.file.name}>{row.file.name}</strong><small>{row.width && row.height ? `${row.width} × ${row.height}` : "尺寸读取失败"} · {bytesLabel(row.file.size)}</small></div><input className="input caption-input" value={row.caption} onChange={(event) => updateCaption(row.id, event.target.value)} aria-label={`${row.file.name} Caption`} disabled={busy} /><div className="image-generation-row-status">{row.error || (!row.caption.trim() ? "请填写 Caption" : "已通过")}</div><button type="button" className="icon-only-btn danger-icon-btn" title="移除图片" aria-label={`移除 ${row.file.name}`} onClick={() => removeRow(row.id)} disabled={busy}><Trash2 size={15} /></button></div>) : <div className="image-generation-filter-empty">没有匹配的图片</div>}</div>
        </>}
        {error && <div className="form-error" role="alert">{error}</div>}
        {stage !== "idle" && <div className="processing-notice"><span className="processing-spinner" />{stageMessage(stage, progress)}<small>请勿关闭页面，失败后已选图片和 Caption 会保留</small></div>}
        <div className="form-footer"><button type="button" className="secondary-btn" onClick={onCancel} disabled={busy}>取消</button><button type="button" className="primary-btn" onClick={() => void submit()} disabled={busy || !rows.length}>{parsingFiles ? "正在解析" : processing ? "正在处理" : "创建并校验"}</button></div>
      </div>

      <aside className="guide image-generation-create-guide">
        <h2>处理流程</h2><p>系统只展示真实可观测阶段，校验失败不会清空已编辑内容。</p>
        <div className="pipeline pipeline-live image-generation-pipeline"><span className={rows.length ? "done" : "active"}>选择文件</span><b>→</b><span className={stage === "parsing" ? "active" : rows.length ? "done" : "pending"}>本地解析</span><b>→</b><span className={stage === "uploading" ? "active" : stage === "validating" || stage === "complete" ? "done" : "pending"}>上传中</span><b>→</b><span className={stage === "validating" ? "active" : stage === "complete" ? "done" : "pending"}>服务端校验</span><b>→</b><span className={stage === "complete" ? "done" : "pending"}>校验结果</span></div>
        <div className="guide-list"><div className="guide-item"><span className="guide-num">1</span><span><strong>自动预填 Caption</strong><span>默认使用文件名生成，可在列表中直接校对。</span></span></div><div className="guide-item"><span className="guide-num">2</span><span><strong>快速定位异常</strong><span>重复文件、无法解码、超限和空 Caption 集中筛选。</span></span></div><div className="guide-item"><span className="guide-num">3</span><span><strong>失败保留现场</strong><span>上传或校验失败时保留图片、Caption 和错误原因。</span></span></div></div>
        <div className="warning"><strong>提交标准</strong>至少一张有效图片；所有 Caption 非空；文件名唯一；图片可正常读取。</div>
      </aside>
    </div>

    {showFormatGuide && <div className="modal-backdrop"><div className="modal format-guide-modal" role="dialog" aria-modal="true" aria-labelledby="image-generation-format-title"><div className="modal-head"><div><h2 id="image-generation-format-title">文生图数据格式要求</h2><p>可直接选择图片，也可上传标准 ZIP；两种方式都会进入同一校验流程。</p></div><button type="button" className="close-btn" onClick={() => setShowFormatGuide(false)} aria-label="关闭格式要求"><X size={16} /></button></div><div className="format-guide-grid"><div><h3>图片直传</h3><p>支持 JPG、JPEG、PNG、WebP；单图最大 20MB，总数最多 200 张。</p></div><div><h3>ZIP 数据包</h3><code>{`dataset.zip\n  images/\n    0001.jpg\n  metadata.jsonl`}</code></div><div><h3>必填字段</h3><p><strong>file_name：</strong>图片相对路径</p><p><strong>caption：</strong>训练提示词；缺失时页面按文件名预填。</p></div><div><h3>校验规则</h3><p>ZIP 签名、路径安全、文件名唯一、图片解码、大小限制、图片映射、JSONL 编码和 Caption 非空。</p></div></div><div className="format-example"><h3>正确示例</h3><pre>{`{"file_name":"images/0001.jpg","caption":"白色背景上的红色运动鞋"}`}</pre><h3>错误示例</h3><pre>{`{"file_name":"images/missing.jpg","caption":""}`}</pre></div><div className="modal-actions"><button type="button" className="primary-btn" onClick={() => setShowFormatGuide(false)}>知道了</button></div></div></div>}
  </section>;
}

export default ImageGenerationModule;
