"use client";

import { RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { apiUrl } from "../lib/api-config";

type Dataset = { id: string; name: string; datasetPath: string; metadataName: string; sampleCount: number; missingImages: number; valid: boolean; description?: string };
type Evaluation = { id: string; serviceId?: string; status: string; datasetPath?: string; summary?: { total?: number; exactMatch?: number; tokenF1?: number; failed?: number; averageLatencyMs?: number }; error?: string };

const EVALUATION_STATUS_LABELS: Record<string, string> = {
  queued: "排队中",
  pending: "等待中",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
};

function evaluationStatusLabel(status: string) {
  return EVALUATION_STATUS_LABELS[status] ?? status;
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), { ...options, credentials: "include", cache: "no-store" });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `请求失败（${response.status}）`);
  return body as T;
}

export function VisionServiceEvaluation({ serviceId, serviceName }: { serviceId: string; serviceName: string }) {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [evaluations, setEvaluations] = useState<Evaluation[]>([]);
  const [datasetId, setDatasetId] = useState("");
  const [validation, setValidation] = useState<Dataset | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function load() {
    setBusy(true); setError("");
    try {
      const [nextDatasets, nextEvaluations] = await Promise.all([
        request<Dataset[]>("/vision/evaluation-datasets"),
        request<Evaluation[]>("/vision/evaluations"),
      ]);
      setDatasets(nextDatasets);
      setEvaluations(nextEvaluations.filter((item) => item.serviceId === serviceId));
      if (!datasetId && nextDatasets[0]) setDatasetId(nextDatasets[0].id);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "读取视觉评测数据失败。"); }
    finally { setBusy(false); }
  }

  useEffect(() => { void load(); }, [serviceId]);

  // 评测排队/运行中时静默轮询，状态与结果无需手动刷新。
  const hasActiveEvaluation = evaluations.some((item) => ["queued", "running", "pending"].includes(item.status));
  useEffect(() => {
    if (!hasActiveEvaluation) return;
    const timer = window.setInterval(() => {
      void request<Evaluation[]>("/vision/evaluations")
        .then((next) => setEvaluations(next.filter((item) => item.serviceId === serviceId)))
        .catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [hasActiveEvaluation, serviceId]);

  async function validate() {
    const selected = datasets.find((item) => item.id === datasetId);
    if (!selected) return;
    setBusy(true); setError("");
    try {
      const result = await request<Dataset>("/vision/evaluations/validate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ serviceId, datasetPath: selected.datasetPath, metadataName: selected.metadataName, maxSamples: selected.sampleCount }) });
      setValidation(result);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "验证集校验失败。"); }
    finally { setBusy(false); }
  }

  async function create() {
    const selected = datasets.find((item) => item.id === datasetId);
    if (!selected || !validation?.valid) return;
    setBusy(true); setError("");
    try {
      await request("/vision/evaluations", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ serviceId, datasetPath: selected.datasetPath, metadataName: selected.metadataName, maxSamples: selected.sampleCount }) });
      await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "创建评测任务失败。"); }
    finally { setBusy(false); }
  }

  const selected = datasets.find((item) => item.id === datasetId);
  return <div className="vision-two-column"><section className="panel vision-section"><div className="section-heading"><div><h2>验证评测 · {serviceName}</h2><p>当前服务固定用于图像问答验证，不再需要重复选择服务。</p></div><button type="button" className="secondary-btn icon-text-btn" onClick={() => void load()} disabled={busy}><RefreshCw size={14} className={busy ? "spin" : ""} />刷新</button></div><label className="vision-field-wide"><span>验证集</span><select className="input" value={datasetId} onChange={(event) => { setDatasetId(event.target.value); setValidation(null); }}><option value="">请选择已发现的验证集</option>{datasets.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.sampleCount} 条{item.valid ? "" : " · 需修复"}</option>)}</select></label>{selected && <div className={`vision-preflight ${selected.valid ? "ready" : "blocked"}`}><strong>{selected.valid ? "验证集可用" : "验证集需要修复"}</strong><span>样本 {selected.sampleCount} 条 · 缺失图片 {selected.missingImages} 张</span><p>{selected.description || selected.datasetPath}</p></div>}{validation && <div className={`vision-preflight ${validation.valid ? "ready" : "blocked"}`}><strong>{validation.valid ? "校验通过" : "校验未通过"}</strong><p>{validation.datasetPath}</p></div>}<div className="vision-actions"><button type="button" className="secondary-btn" disabled={busy || !selected} onClick={() => void validate()}>校验验证集</button><button type="button" className="primary-btn" disabled={busy || !validation?.valid} onClick={() => void create()}>开始评测</button></div>{error && <div className="field-error">{error}</div>}</section><section className="panel vision-section"><div className="section-heading"><div><h2>当前服务评测记录</h2><p>只显示该服务发起的视觉评测。</p></div></div><div className="vision-evaluation-list">{evaluations.map((item) => <div className="vision-evaluation-row" key={item.id}><div className="vision-eval-head"><strong>{item.id}</strong><span>{evaluationStatusLabel(item.status)}</span></div>{item.summary && <div className="vision-metrics"><span>样本 <b>{item.summary.total ?? 0}</b></span><span>EM <b>{((item.summary.exactMatch ?? 0) * 100).toFixed(1)}%</b></span><span>F1 <b>{((item.summary.tokenF1 ?? 0) * 100).toFixed(1)}%</b></span><span>失败 <b>{item.summary.failed ?? 0}</b></span></div>}{item.error && <div className="field-error">{item.error}</div>}</div>)}{!evaluations.length && <div className="empty">尚未创建评测任务</div>}</div></section></div>;
}
