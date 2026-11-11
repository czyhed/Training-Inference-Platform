"use client";

import { Archive, Download, Play, RefreshCw, RotateCcw, Square, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { apiUrl } from "../lib/api-config";

type Service = { id: string; name: string; state: string; config: Record<string, unknown> };
type Result = { id: string; fileName: string; width?: number; height?: number; seed?: number; createdAt?: string; available?: boolean };
type Task = {
  id: string;
  name: string;
  serviceId: string;
  serviceName?: string;
  prompt: string;
  negativePrompt?: string;
  state: string;
  status: string;
  progress: number;
  currentStep?: string;
  error?: string;
  cleanupState?: string;
  cleanupError?: string;
  results?: Result[];
  logs?: string[];
  createdAt?: string;
  updatedAt?: string;
  phase?: string;
  phaseUpdatedAt?: string;
  lastHeartbeatAt?: string;
  failureCode?: string;
  failureMessage?: string;
  suggestedAction?: string;
  archived?: boolean;
  archivedAt?: string | null;
  width?: number;
  height?: number;
  steps?: number;
  cfg?: number;
  batchSize?: number;
  seed?: number;
  sampler?: string;
};
type Diagnostics = { taskId: string; state: string; phase?: string; progress: number; lastHeartbeatAt?: string; failure?: { code: string; message: string } | null; cleanup?: { state?: string | null; error?: string | null } | null; suggestedAction?: string | null };
type Props = { serviceId: string; serviceName: string; serviceState?: string; defaultConfig?: Record<string, unknown>; embedded?: boolean; onOpenServiceOverview?: () => void };

const stages = [
  ["awaiting_confirmation", "待提交"],
  ["queued", "排队"],
  ["preparing", "实例与模型"],
  ["running", "生成"],
  ["collecting", "收集结果"],
  ["completed", "完成"],
] as const;
const activeStates = new Set(["queued", "preparing", "running", "collecting", "stopping"]);

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), { ...options, credentials: "include", cache: "no-store" });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body?.detail;
    if (typeof detail === "string") {
      const error = new Error(detail) as Error & { detail?: unknown; status?: number };
      error.detail = detail;
      error.status = response.status;
      throw error;
    }
    if (detail && typeof detail === "object") {
      const message = typeof detail.message === "string" ? detail.message : `请求失败（${response.status}）`;
      const checks = detail.preflight?.checks;
      const blocked = checks && typeof checks === "object"
        ? Object.values(checks as Record<string, { status?: string; message?: string }>)
          .filter((check) => check?.status === "blocked")
          .map((check) => check.message)
          .filter(Boolean)
        : [];
      const error = new Error([message, ...blocked].join(" ")) as Error & { detail?: unknown; status?: number };
      error.detail = detail;
      error.status = response.status;
      throw error;
    }
    const error = new Error(`请求失败（${response.status}）`) as Error & { detail?: unknown; status?: number };
    error.detail = detail;
    error.status = response.status;
    throw error;
  }
  return body as T;
}

function stageIndex(state: string) {
  if (state === "stopping" || state === "stopped" || state === "failed") return Math.max(0, stages.findIndex(([key]) => key === "running"));
  return Math.max(0, stages.findIndex(([key]) => key === state));
}

function blockedPreflightChecks(value: { checks?: Record<string, { status: string; message: string }> } | null) {
  return Object.entries(value?.checks || {}).filter(([, check]) => check.status === "blocked");
}

export function ImageGenerationTasks({ serviceId: scopedServiceId, serviceName, serviceState = "", defaultConfig = {}, embedded = false, onOpenServiceOverview }: Props) {
  const [services, setServices] = useState<Service[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [serviceId, setServiceId] = useState(scopedServiceId);
  const [filterServiceId, setFilterServiceId] = useState("");
  const [filterState, setFilterState] = useState("");
  const [filterQuery, setFilterQuery] = useState("");
  const [includeArchived, setIncludeArchived] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [negativePrompt, setNegativePrompt] = useState("");
  const [width, setWidth] = useState(512);
  const [height, setHeight] = useState(512);
  const [steps, setSteps] = useState(30);
  const [cfg, setCfg] = useState(7);
  const [seed, setSeed] = useState(-1);
  const [sampler, setSampler] = useState("euler");
  const [batchSize, setBatchSize] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [preflight, setPreflight] = useState<{ ready: boolean; checks?: Record<string, { status: string; message: string }>; checkedAt?: string; expiresAtEpoch?: number; resource?: { price?: { total?: number } } } | null>(null);
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(null);
  const [cloneSourceId, setCloneSourceId] = useState("");

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (scopedServiceId) params.set("serviceId", scopedServiceId);
      else if (filterServiceId) params.set("serviceId", filterServiceId);
      if (filterState) params.set("state", filterState);
      if (filterQuery.trim()) params.set("query", filterQuery.trim());
      if (includeArchived) params.set("includeArchived", "true");
      const taskPath = params.toString() ? `/image-generation/tasks?${params.toString()}` : "/image-generation/tasks";
      const [nextServices, nextTasks] = await Promise.all([
        scopedServiceId
          ? Promise.resolve<Service[]>([{ id: scopedServiceId, name: serviceName, state: serviceState, config: defaultConfig }])
          : request<Service[]>("/image-generation/services"),
        request<Task[]>(taskPath),
      ]);
      setServices(nextServices);
      setTasks(nextTasks);
      if (scopedServiceId) setServiceId(scopedServiceId);
      else setServiceId((current) => current || nextServices[0]?.id || "");
      setSelectedId((current) => current || nextTasks[0]?.id || "");
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "文生图任务读取失败。");
    }
  }, [defaultConfig, filterQuery, filterServiceId, filterState, includeArchived, scopedServiceId, serviceName, serviceState]);

  useEffect(() => { setServiceId(scopedServiceId); }, [scopedServiceId]);

  useEffect(() => { void load(); }, [load]);
  const hasActiveTask = tasks.some((task) => activeStates.has(task.state));
  useEffect(() => {
    if (!hasActiveTask) return;
    const timer = window.setInterval(() => void load(), 3000);
    return () => window.clearInterval(timer);
  }, [hasActiveTask, load]);

  const selected = useMemo(() => tasks.find((task) => task.id === selectedId) ?? tasks[0], [selectedId, tasks]);
  useEffect(() => { setPreflight(null); }, [selectedId]);
  useEffect(() => {
    if (!selected?.id) { setDiagnostics(null); return; }
    let active = true;
    void request<Diagnostics>(`/image-generation/tasks/${selected.id}/diagnostics`).then((value) => { if (active) setDiagnostics(value); }).catch(() => { if (active) setDiagnostics(null); });
    return () => { active = false; };
  }, [selected?.id, selected?.updatedAt, selected?.state]);

  async function createTask() {
    if (!serviceId) { setError("请先创建并选择文生图服务配置。"); return; }
    const service = services.find((item) => item.id === serviceId);
    if (service && service.state !== "running") { setError("文生图推理服务尚未运行，请先在服务详情页点击启动服务。"); return; }
    if (!prompt.trim()) { setError("请填写 Prompt。"); return; }
    setBusy(true); setError("");
    try {
      const task = await request<Task>(cloneSourceId ? `/image-generation/tasks/${cloneSourceId}/clone` : "/image-generation/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(cloneSourceId ? { prompt: prompt.trim(), negativePrompt: negativePrompt.trim(), width, height, steps, cfg, seed, sampler, batchSize } : { serviceId, prompt: prompt.trim(), negativePrompt: negativePrompt.trim(), width, height, steps, cfg, seed, sampler, batchSize }),
      });
      setTasks((current) => [task, ...current]);
      setSelectedId(task.id);
      setCloneSourceId("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "任务创建失败。"); }
    finally { setBusy(false); }
  }

  async function act(action: "confirm" | "stop" | "retry") {
    if (!selected) return;
    if (selected.archived) { setError("已归档文生图任务不能执行此操作。"); return; }
    setBusy(true); setError("");
    try {
      if (action === "confirm") {
        const task = await request<Task>(`/image-generation/tasks/${selected.id}/confirm`, { method: "POST" });
        setTasks((current) => current.map((item) => item.id === task.id ? task : item));
        setError("");
        return;
      }
      const task = await request<Task>(`/image-generation/tasks/${selected.id}/${action}`, { method: "POST" });
      setTasks((current) => current.map((item) => item.id === task.id ? task : item));
      setError("");
    } catch (reason) {
      const detail = reason && typeof reason === "object" && "detail" in reason ? (reason as { detail?: { preflight?: typeof preflight } }).detail : undefined;
      if (action === "confirm" && detail?.preflight) setPreflight(detail.preflight);
      setError(reason instanceof Error ? reason.message : "任务操作失败。");
    }
    finally { setBusy(false); }
  }


  async function lifecycleAction(action: "archive" | "restore" | "delete") {
    if (!selected) return;
    const labels = { archive: "归档", restore: "恢复", delete: "删除" } as const;
    if (action === "archive" && !window.confirm("归档后任务将从默认历史列表隐藏，确认继续？")) return;
    if (action === "delete" && !window.confirm("删除任务及其生成结果文件？此操作不可恢复。")) return;
    setBusy(true); setError("");
    try {
      const endpoint = action === "delete" ? `/image-generation/tasks/${selected.id}` : action === "archive" ? `/image-generation/tasks/${selected.id}/archive` : `/image-generation/tasks/${selected.id}/restore`;
      const response = action === "delete"
        ? await fetch(apiUrl(endpoint), { method: "DELETE", credentials: "include", cache: "no-store" })
        : await fetch(apiUrl(endpoint), { method: "POST", credentials: "include", cache: "no-store" });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `${labels[action]}失败（${response.status}）`);
      if (action === "delete") {
        setTasks((current) => current.filter((item) => item.id !== selected.id));
        setSelectedId("");
      } else {
        const next = body as Task;
        setTasks((current) => includeArchived || !next.archived ? current.map((item) => item.id === next.id ? next : item) : current.filter((item) => item.id !== next.id));
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : `${labels[action]}失败。`); }
    finally { setBusy(false); }
  }

  function cloneTask(task: Task) {
    setCloneSourceId(task.id);
    setServiceId(task.serviceId);
    setPrompt(task.prompt);
    setNegativePrompt(task.negativePrompt || "");
    setWidth(task.width || 512);
    setHeight(task.height || 512);
    setSteps(task.steps || 30);
    setCfg(task.cfg ?? 7);
    setSeed(task.seed ?? -1);
    setSampler(task.sampler || "euler");
    setBatchSize(task.batchSize || 1);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  const currentStage = selected ? stageIndex(selected.state) : 0;
  const serviceRunning = serviceState === "running";
  return <section className="content image-generation-tasks-page">
    <div className="title-row"><div><h1>{embedded ? "图片生成" : "生成任务 · 文生图"}</h1><div className="subtitle">{embedded ? `${serviceName} · 当前服务生成任务` : "创建任务后需单独确认，确认时才会申请优云 GPU 实例"}</div></div><button type="button" className="secondary-btn icon-text-btn" onClick={() => void load()}><RefreshCw size={15} />刷新</button></div>
    {error && <div className="error-banner">{error}</div>}
    {!embedded && <div className="image-generation-task-filters" aria-label="任务历史筛选"><select className="input" value={filterServiceId} onChange={(event) => setFilterServiceId(event.target.value)}><option value="">全部服务</option>{services.map((service) => <option key={service.id} value={service.id}>{service.name}</option>)}</select><select className="input" value={filterState} onChange={(event) => setFilterState(event.target.value)}><option value="">全部状态</option><option value="awaiting_confirmation">待确认</option><option value="queued">排队</option><option value="running">生成中</option><option value="completed">已完成</option><option value="failed">失败</option><option value="stopped">已停止</option></select><input className="input" value={filterQuery} onChange={(event) => setFilterQuery(event.target.value)} placeholder="搜索任务或 Prompt" /><label className="checkbox-field"><input type="checkbox" checked={includeArchived} onChange={(event) => setIncludeArchived(event.target.checked)} /> 已归档</label></div>}
    <div className="image-generation-task-layout">
      <div className="panel image-generation-task-form">
        <div className="section-heading"><div><h2>创建任务</h2><p>保存后处于待确认状态，不会立即产生云费用。</p></div></div>
        {!embedded && <label className="field"><span className="field-label">服务配置</span><select className="input" value={serviceId} onChange={(event) => setServiceId(event.target.value)}><option value="">请选择服务</option>{services.map((service) => <option key={service.id} value={service.id}>{service.name}</option>)}</select></label>}
        <label className="field"><span className="field-label">Prompt</span><textarea className="input task-prompt" value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="描述要生成的画面" /></label>
        <label className="field"><span className="field-label">Negative Prompt</span><textarea className="input" value={negativePrompt} onChange={(event) => setNegativePrompt(event.target.value)} placeholder="不希望出现的内容" /></label>
        <div className="image-generation-task-parameters">
          <label>宽度<input className="input" type="number" value={width} onChange={(event) => setWidth(Number(event.target.value))} /></label>
          <label>高度<input className="input" type="number" value={height} onChange={(event) => setHeight(Number(event.target.value))} /></label>
          <label>步数<input className="input" type="number" value={steps} onChange={(event) => setSteps(Number(event.target.value))} /></label>
          <label>CFG<input className="input" type="number" value={cfg} onChange={(event) => setCfg(Number(event.target.value))} /></label>
          <label>Seed<input className="input" type="number" value={seed} onChange={(event) => setSeed(Number(event.target.value))} /></label>
          <label>生成数量<input className="input" type="number" min={1} max={4} value={batchSize} onChange={(event) => setBatchSize(Number(event.target.value))} /></label>
          <label>采样器<select className="input" value={sampler} onChange={(event) => setSampler(event.target.value)}><option value="euler">Euler</option><option value="dpmpp_2m">DPM++ 2M</option></select></label>
        </div>
        <div className="image-generation-task-submit">
          <button type="button" className="primary-btn" disabled={busy || !services.length} onClick={() => void createTask()}>{cloneSourceId ? "复制并创建待确认任务" : "创建任务"}</button>
          {embedded && !serviceRunning && <div className="image-generation-service-blocker" role="status"><strong>当前服务未运行，暂时不能创建生图任务</strong><span>请先启动文生图推理服务，服务就绪后再提交生成。</span>{onOpenServiceOverview && <button type="button" className="link-btn" onClick={onOpenServiceOverview}>返回服务概览启动</button>}</div>}
        </div>
      </div>
      <div className="image-generation-task-list" aria-label="文生图任务列表">
        {tasks.length === 0 ? <div className="panel empty-state">暂无生成任务。</div> : tasks.map((task) => <button type="button" key={task.id} className={`image-generation-task-item ${selected?.id === task.id ? "selected" : ""}`} onClick={() => setSelectedId(task.id)}><span><strong>{task.name}</strong><small>{task.currentStep || task.state}</small></span><span className={`task-state state-${task.state}`}>{task.progress || 0}%</span></button>)}
      </div>
    </div>
    {selected && <div className="image-generation-task-detail">
      <div className="section-heading"><div><h2>{selected.name}</h2><p>{selected.prompt}</p></div><div className="task-actions">{!selected.archived && selected.state === "awaiting_confirmation" && <button type="button" className="primary-btn icon-text-btn" disabled={busy} onClick={() => void act("confirm")}><Play size={15} />提交生成</button>}{!selected.archived && activeStates.has(selected.state) && <button type="button" className="secondary-btn icon-text-btn" disabled={busy} onClick={() => void act("stop")}><Square size={14} />停止</button>}{!selected.archived && selected.cleanupState === "failed" && <button type="button" className="secondary-btn icon-text-btn" disabled={busy} onClick={() => void act("stop")}><RotateCcw size={15} />重试清理实例</button>}{!selected.archived && ["failed", "stopped"].includes(selected.state) && selected.cleanupState !== "failed" && <button type="button" className="secondary-btn icon-text-btn" disabled={busy} onClick={() => void act("retry")}><RotateCcw size={15} />重试</button>}<button type="button" className="secondary-btn icon-text-btn" disabled={busy} onClick={() => cloneTask(selected)}><RotateCcw size={15} />复制并重新生成</button>{selected.archived ? <button type="button" className="secondary-btn icon-text-btn" disabled={busy} onClick={() => void lifecycleAction("restore")}><Archive size={15} />恢复</button> : ["completed", "failed", "stopped"].includes(selected.state) && <button type="button" className="secondary-btn icon-text-btn" disabled={busy} onClick={() => void lifecycleAction("archive")}><Archive size={15} />归档</button>} {!activeStates.has(selected.state) && selected.state !== "queued" && <button type="button" className="danger-btn icon-text-btn" disabled={busy} onClick={() => void lifecycleAction("delete")}><Trash2 size={15} />删除</button>}</div></div>{!selected.archived && selected.state === "awaiting_confirmation" && (() => { const expired = Boolean(preflight?.expiresAtEpoch && Date.now() / 1000 >= preflight.expiresAtEpoch); return <div className={`image-generation-preflight ${preflight?.ready && !expired ? "ready" : preflight ? "blocked" : "pending"}`}><strong>启动前预检</strong>{preflight ? <>{expired ? <span>检查已过期，请重新预检</span> : <span>{preflight.ready ? "预检通过，可以确认" : "预检未通过"}</span>}<small>最近检查：{preflight.checkedAt || "刚刚"}</small>{preflight.expiresAtEpoch && <small>有效至：{new Date(preflight.expiresAtEpoch * 1000).toLocaleString()}</small>}</> : <span>服务启动前会校验模型、镜像、库存和价格</span>}</div>; })()}
       {selected.state === "awaiting_confirmation" && <div className="image-generation-pending-notice" role="status"><strong>任务已保存，尚未提交</strong><span>请先在服务详情页启动文生图推理服务，再提交生成。任务提交不会创建新的优云实例。</span></div>}
      {preflight && !preflight.ready && <div className="image-generation-preflight-blockers" role="alert"><strong>优云启动前检查未通过</strong>{blockedPreflightChecks(preflight).map(([key, check]) => <span key={key}>{key === "capacity" ? "所选 GPU 当前无库存" : key === "price" ? "价格校验未完成" : key}：{check.message}</span>)}<small>优云库存和价格会动态变化，请刷新资源后重新检查。</small><button type="button" className="secondary-btn icon-text-btn" disabled={busy} onClick={() => void act("confirm")}><RefreshCw size={14} />重新检查</button></div>}
      <div className="image-generation-timeline">{stages.map(([key, label], index) => <div key={key} className={`${index < currentStage ? "done" : index === currentStage ? "active" : ""}`}><span>{index + 1}</span><small>{label}</small></div>)}</div>
      <div className="task-progress"><span style={{ width: `${Math.max(0, Math.min(100, selected.progress || 0))}%` }} /></div>
      {diagnostics && <div className="image-generation-diagnostics"><strong>运行诊断 · {diagnostics.phase || diagnostics.state}</strong><span>进度：{diagnostics.progress}%</span>{diagnostics.lastHeartbeatAt && <small>最后进展：{diagnostics.lastHeartbeatAt}</small>}{diagnostics.failure && <span className="diagnostic-failure">{diagnostics.failure.code}：{diagnostics.failure.message}</span>}{diagnostics.cleanup && <span>清理状态：{diagnostics.cleanup.state}{diagnostics.cleanup.error ? ` · ${diagnostics.cleanup.error}` : ""}</span>}{diagnostics.suggestedAction && <small>建议：{diagnostics.suggestedAction}</small>}</div>}
      {(selected.error || selected.cleanupState === "failed") && <div className="error-banner">{selected.error || "图片已生成，但优云实例尚未释放。"}{selected.cleanupState === "failed" ? `；${selected.cleanupError || "请重试清理实例。"}` : ""}</div>}
      {!!selected.results?.length && <div className="image-generation-results">{selected.results.map((result) => { const url = apiUrl(`/image-generation/tasks/${selected.id}/results/${result.id}`); return <figure key={result.id} className={result.available === false ? "result-unavailable" : ""}>{result.available === false ? <div className="result-missing">结果文件不可用</div> : <img src={url} alt={`${selected.prompt} ${result.id}`} />}<figcaption><span>{result.width || width} × {result.height || height}</span>{result.available !== false && <a className="secondary-btn icon-text-btn" href={url} download={result.fileName}><Download size={14} />下载原图</a>}</figcaption></figure>; })}</div>}
      <div className="image-generation-task-logs">{(selected.logs || []).slice(-30).map((line, index) => <code key={`${index}-${line}`}>{line}</code>)}</div>
    </div>}
  </section>;
}

export default ImageGenerationTasks;
