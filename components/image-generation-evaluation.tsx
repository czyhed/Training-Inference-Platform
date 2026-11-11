"use client";

import { CheckCircle2, Download, Play, RefreshCw, RotateCcw, Square } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { apiUrl } from "../lib/api-config";

type Props = { serviceId: string; serviceName: string; defaultConfig?: Record<string, unknown> };
type Dataset = { id: string; name: string; sampleCount: number; validSampleCount: number; invalidSampleCount: number; valid: boolean; message?: string };
type Sample = { id: string; fileName: string; caption: string; referencePath?: string };
type Result = { sampleId: string; prompt?: string; state: string; fileName?: string; latencyMs?: number; clipTextImage?: number | null; clipReferenceImage?: number | null; error?: string };
type Evaluation = { id: string; name: string; serviceId: string; datasetId?: string; datasetName?: string; sampleCount: number; state: string; progress: number; currentStep?: string; samples?: Sample[]; results?: Result[]; summary?: { sampleCount?: number; completedSamples?: number; failedSamples?: number; successRate?: number; averageLatencyMs?: number; clipTextImageMean?: number | null; clipReferenceImageMean?: number | null }; failureMessage?: string; suggestedAction?: string; cleanupState?: string; logs?: string[]; createdAt?: string };

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), { ...options, credentials: "include", cache: "no-store" });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : typeof body.detail?.message === "string" ? body.detail.message : `请求失败（${response.status}）`);
  return body as T;
}

function metric(value: number | null | undefined, suffix = "") {
  return typeof value === "number" ? `${value.toFixed(3)}${suffix}` : "--";
}

export function ImageGenerationEvaluation({ serviceId, serviceName, defaultConfig = {} }: Props) {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [evaluations, setEvaluations] = useState<Evaluation[]>([]);
  const [datasetId, setDatasetId] = useState("");
  const [maxSamples, setMaxSamples] = useState("");
  const [validation, setValidation] = useState<Record<string, any> | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const width = Number(defaultConfig.width || 1024);
  const height = Number(defaultConfig.height || 1024);
  const steps = Number(defaultConfig.steps || 30);
  const cfg = Number(defaultConfig.cfg ?? 7);
  const seed = Number(defaultConfig.seed ?? -1);
  const sampler = String(defaultConfig.sampler || "euler");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [nextDatasets, nextEvaluations] = await Promise.all([
        request<Dataset[]>("/image-generation/evaluation-datasets"),
        request<Evaluation[]>(`/image-generation/services/${serviceId}/evaluations?includeArchived=true`),
      ]);
      setDatasets(nextDatasets);
      setEvaluations(nextEvaluations);
      setDatasetId((current) => current || nextDatasets.find((item) => item.valid)?.id || "");
      setSelectedId((current) => current || nextEvaluations[0]?.id || "");
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "文生图评测读取失败。");
    } finally {
      setLoading(false);
    }
  }, [serviceId]);

  useEffect(() => { void load(); }, [load]);
  const selectedDataset = useMemo(() => datasets.find((item) => item.id === datasetId), [datasetId, datasets]);
  const selected = useMemo(() => evaluations.find((item) => item.id === selectedId) || evaluations[0], [evaluations, selectedId]);
  const hasActive = evaluations.some((item) => ["queued", "preparing", "running", "collecting", "stopping"].includes(item.state));
  useEffect(() => {
    if (!hasActive) return;
    const timer = window.setInterval(() => void load(), 2500);
    return () => window.clearInterval(timer);
  }, [hasActive, load]);

  async function validate() {
    if (!datasetId) return setError("请选择文生图验证评测集。");
    setBusy(true); setError("");
    try {
      const next = await request<Record<string, any>>("/image-generation/evaluations/validate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ datasetId, maxSamples: maxSamples ? Number(maxSamples) : null }) });
      setValidation(next);
      if (!next.valid) setError("评测集校验未通过，请处理阻塞样本。");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "评测集校验失败。"); }
    finally { setBusy(false); }
  }

  async function create() {
    if (!validation?.valid) return setError("请先校验一个有效的文生图评测集。");
    setBusy(true); setError(""); setMessage("");
    try {
      const task = await request<Evaluation>(`/image-generation/services/${serviceId}/evaluations`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ datasetId, maxSamples: maxSamples ? Number(maxSamples) : null, width, height, steps, cfg, seed, sampler }) });
      setEvaluations((current) => [task, ...current]);
      setSelectedId(task.id);
      setMessage("评测任务已创建，确认后才会申请优云实例。");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "评测任务创建失败。"); }
    finally { setBusy(false); }
  }

  async function action(name: "confirm" | "stop" | "retry") {
    if (!selected) return;
    if (name === "confirm" && !window.confirm("确认后将申请优云实例并产生费用，继续吗？")) return;
    setBusy(true); setError("");
    try {
      const next = await request<Evaluation>(`/image-generation/services/${serviceId}/evaluations/${selected.id}/${name}`, { method: "POST" });
      setEvaluations((current) => current.map((item) => item.id === next.id ? next : item));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "评测任务操作失败。"); }
    finally { setBusy(false); }
  }

  const summary = selected?.summary || {};
  return <section className="image-generation-evaluation-page">
    <div className="title-row"><div><h1>验证评测</h1><div className="subtitle">{serviceName} · Caption 作为 Prompt，参考图用于图像相似度对比</div></div><button type="button" className="secondary-btn icon-text-btn" onClick={() => void load()} disabled={loading}><RefreshCw size={15} className={loading ? "spin" : ""} />刷新</button></div>
    {error && <div className="error-banner">{error}</div>}{message && <div className="notice">{message}</div>}
    <div className="image-generation-evaluation-layout">
      <section className="panel image-generation-evaluation-create"><div className="section-heading"><div><h2>创建验证评测</h2><p>使用已发布文生图 IMAGE_TEXT 数据集，不会修改原始数据。</p></div><span>01</span></div>
        <label className="field"><span className="field-label">测试集</span><select className="input" value={datasetId} onChange={(event) => { setDatasetId(event.target.value); setValidation(null); }}><option value="">请选择测试集</option>{datasets.map((dataset) => <option key={dataset.id} value={dataset.id}>{dataset.name} · {dataset.sampleCount} 条{dataset.valid ? "" : " · 需修复"}</option>)}</select></label>
        <label className="field"><span className="field-label">最大样本数</span><input className="input" type="number" min={1} max={1000} value={maxSamples} onChange={(event) => { setMaxSamples(event.target.value); setValidation(null); }} placeholder="默认全部样本" /></label>
        {selectedDataset && <div className={`image-generation-evaluation-dataset-summary ${selectedDataset.valid ? "valid" : "invalid"}`}><div><strong>{selectedDataset.name}</strong><span>{selectedDataset.message}</span></div><div><b>{selectedDataset.sampleCount}</b><small>总样本</small></div><div><b>{selectedDataset.validSampleCount}</b><small>可评测</small></div><div><b>{selectedDataset.invalidSampleCount}</b><small>需处理</small></div></div>}
        {validation && <div className={`vision-training-preflight ${validation.valid ? "ready" : "blocked"}`}><div className="vision-preflight-header"><strong>{validation.valid ? "评测集校验通过" : "评测集校验未通过"}</strong><span>{validation.sampleCount} / {validation.totalSampleCount} 条</span></div>{validation.issues?.slice(0, 4).map((issue: { sampleId: string; fileName: string; errors: string[] }) => <div className="vision-preflight-item" key={issue.sampleId}><span>{issue.fileName}</span><strong className="blocked">{issue.errors.join("；")}</strong></div>)}</div>}
        <div className="image-generation-evaluation-params"><div><span>尺寸</span><strong>{width} × {height}</strong></div><div><span>Steps / CFG</span><strong>{steps} / {cfg}</strong></div><div><span>Seed / 采样器</span><strong>{seed} / {sampler}</strong></div></div>
        <div className="form-footer"><button type="button" className="secondary-btn" disabled={busy || !datasetId} onClick={() => void validate()}>{busy ? "处理中" : "校验评测集"}</button><button type="button" className="primary-btn" disabled={busy || !validation?.valid} onClick={() => void create()}><Play size={15} />创建待确认评测</button></div>
      </section>
      <aside className="panel image-generation-evaluation-list"><div className="section-heading"><div><h2>评测历史</h2><p>当前服务的验证任务与指标。</p></div><span>{evaluations.length} 个任务</span></div>{evaluations.length === 0 ? <div className="empty-state">暂无验证评测任务。</div> : evaluations.map((item) => <button type="button" className={`image-generation-evaluation-item ${selected?.id === item.id ? "selected" : ""}`} key={item.id} onClick={() => setSelectedId(item.id)}><span><strong>{item.datasetName || item.name}</strong><small>{item.state} · {item.progress || 0}%</small></span><span>{item.summary?.successRate != null ? `${(item.summary.successRate * 100).toFixed(0)}%` : "--"}</span></button>)}</aside>
    </div>
    {selected && <section className="panel image-generation-evaluation-detail"><div className="section-heading"><div><h2>{selected.name}</h2><p>{selected.datasetName} · {selected.currentStep || selected.state}</p></div><div className="actions">{selected.state === "awaiting_confirmation" && <button type="button" className="primary-btn icon-text-btn" disabled={busy} onClick={() => void action("confirm")}><Play size={15} />确认并开始</button>}{["queued", "preparing", "running", "collecting", "stopping"].includes(selected.state) && <button type="button" className="secondary-btn icon-text-btn" disabled={busy} onClick={() => void action("stop")}><Square size={14} />停止</button>}{["failed", "stopped"].includes(selected.state) && <button type="button" className="secondary-btn icon-text-btn" disabled={busy} onClick={() => void action("retry")}><RotateCcw size={14} />重试</button>}</div></div>
      {selected.failureMessage && <div className="error-banner">{selected.failureMessage}{selected.suggestedAction ? ` ${selected.suggestedAction}` : ""}</div>}
      <div className="image-generation-evaluation-metrics"><div><span>样本总数</span><strong>{summary.sampleCount ?? selected.sampleCount}</strong></div><div><span>生成成功率</span><strong>{summary.successRate != null ? `${(summary.successRate * 100).toFixed(1)}%` : "--"}</strong></div><div><span>平均耗时</span><strong>{metric(summary.averageLatencyMs, " ms")}</strong></div><div><span>文本-图片 CLIP</span><strong>{metric(summary.clipTextImageMean)}</strong></div><div><span>生成-参考图 CLIP</span><strong>{metric(summary.clipReferenceImageMean)}</strong></div></div>
      <div className="image-generation-evaluation-note"><CheckCircle2 size={15} /><span>CLIP 分数是辅助自动指标，用于批量对比，不代表主观画质或自动通过结论。</span></div>
      <div className="image-generation-evaluation-samples">{(selected.results || []).map((result) => { const reference = apiUrl(`/image-generation/services/${serviceId}/evaluations/${selected.id}/samples/${result.sampleId}/reference`); const generated = apiUrl(`/image-generation/services/${serviceId}/evaluations/${selected.id}/samples/${result.sampleId}/generated`); return <article className="image-generation-evaluation-sample" key={result.sampleId}><div className="history-item-head"><strong>{result.sampleId}</strong><span>{result.state === "completed" ? "已完成" : "失败"}</span></div><p>{result.prompt}</p><div className="image-generation-evaluation-images"><figure><img src={reference} alt="参考图" /><figcaption>参考图</figcaption></figure><figure>{result.state === "completed" ? <img src={generated} alt="生成图" /> : <div className="result-missing">生成失败</div>}<figcaption>生成图</figcaption></figure></div><div className="image-generation-evaluation-sample-meta"><span>耗时 {metric(result.latencyMs, " ms")}</span><span>文本-图 {metric(result.clipTextImage)}</span><span>图-参考 {metric(result.clipReferenceImage)}</span></div>{result.error && <div className="field-error">{result.error}</div>}<a className="secondary-btn icon-text-btn" href={generated} download={result.fileName}><Download size={14} />下载生成图</a></article>; })}</div>
      {(selected.logs || []).length > 0 && <pre className="image-generation-task-logs">{selected.logs?.slice(-100).join("\n")}</pre>}
    </section>}
  </section>;
}

export default ImageGenerationEvaluation;
