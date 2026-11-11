"use client";

import { useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronDown,
  ChevronUp,
  Cpu,
  Layers3,
  Play,
  RotateCcw,
  Server,
  SlidersHorizontal,
  Sparkles,
  TriangleAlert,
  WandSparkles,
  X
} from "lucide-react";

type VariantKey = "A" | "B" | "C";
type SourceKey = "training" | "managed" | "general";

const variants: Record<VariantKey, string> = {
  A: "分步向导",
  B: "工作台布局",
  C: "资产优先"
};

const sources: Array<{ key: SourceKey; label: string; detail: string }> = [
  { key: "training", label: "训练任务产出", detail: "选择成功任务和 checkpoint" },
  { key: "managed", label: "模型管理权重", detail: "部署已登记的模型资产" },
  { key: "general", label: "通用大模型", detail: "部署平台已验证的基模" }
];

const gpuCandidates = [
  { name: "3090 · 24G", stock: 6, status: "首选", tone: "good", fit: "适合 7B BF16" },
  { name: "4090 · 24G", stock: 3, status: "候选 2", tone: "neutral", fit: "适合 7B BF16" },
  { name: "4090 · 48G", stock: 1, status: "候选 3", tone: "neutral", fit: "适合 14B 量化" },
  { name: "3080 Ti · 12G", stock: 0, status: "已过滤", tone: "warn", fit: "当前模型显存不足" }
];

function useVariant() {
  const [variant, setVariant] = useState<VariantKey>("A");
  useEffect(() => {
    const value = new URLSearchParams(window.location.search).get("variant") as VariantKey | null;
    if (value && value in variants) setVariant(value);
  }, []);
  const change = (next: VariantKey) => {
    setVariant(next);
    const params = new URLSearchParams(window.location.search);
    params.set("variant", next);
    window.history.replaceState({}, "", `${window.location.pathname}?${params.toString()}`);
  };
  return { variant, change };
}

function PrototypeSwitcher({ variant, change }: { variant: VariantKey; change: (next: VariantKey) => void }) {
  const keys = Object.keys(variants) as VariantKey[];
  const move = (direction: number) => {
    const index = keys.indexOf(variant);
    change(keys[(index + direction + keys.length) % keys.length]);
  };
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if (event.key === "ArrowLeft") move(-1);
      if (event.key === "ArrowRight") move(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
  return <div className="prototype-switcher"><button aria-label="上一个版本" onClick={() => move(-1)}><ArrowLeft size={15} /></button><span>原型 {variant} · {variants[variant]}</span><button aria-label="下一个版本" onClick={() => move(1)}><ArrowRight size={15} /></button></div>;
}

function Sidebar() {
  return <aside className="proto-sidebar"><div className="proto-brand">训推平台<small>MODEL OPERATIONS</small></div><nav>{["概览", "数据集", "微调任务", "模型管理", "推理服务"].map((item) => <div className={item === "推理服务" ? "active" : ""} key={item}>{item}</div>)}</nav><div className="proto-user"><strong>平台管理员</strong><span>优云远程 GPU 已启用</span></div></aside>;
}

function Header({ title = "创建推理服务" }: { title?: string }) {
  return <header className="proto-header"><span>推理服务 / 创建服务</span><span className="header-env"><span className="live-dot" />优云远程 GPU</span></header>;
}

function SourcePicker({ source, setSource, compact = false }: { source: SourceKey; setSource: (value: SourceKey) => void; compact?: boolean }) {
  return <div className={compact ? "source-picker compact" : "source-picker"}><div className="eyebrow">部署对象</div><h2>先选择要部署的模型资产</h2><p className="muted">选择来源后，平台会同步可用权重、模型规格和兼容镜像。</p><div className="source-grid">{sources.map((item) => <button type="button" key={item.key} className={source === item.key ? "source-card selected" : "source-card"} onClick={() => setSource(item.key)}><span className="source-icon">{item.key === "training" ? <Layers3 size={17} /> : item.key === "managed" ? <Server size={17} /> : <Sparkles size={17} />}</span><strong>{item.label}</strong><small>{item.detail}</small>{source === item.key && <Check className="source-check" size={17} />}</button>)}</div><div className="asset-row"><label>{source === "training" ? "训练任务" : "模型资产"}<select defaultValue={source === "training" ? "客服问答微调 · Qwen2.5-7B" : source === "managed" ? "Qwen2.5-7B · 基模" : "Qwen2.5-7B-Instruct · 通用版"}><option>客服问答微调 · Qwen2.5-7B</option><option>Qwen2.5-7B · 基模</option><option>Qwen2.5-14B · 量化版</option><option>Llama-3.1-8B · 通用版</option></select></label><label>{source === "training" ? "可部署权重" : "版本"}<select defaultValue="checkpoint-1200 · 最优"><option>checkpoint-1200 · 最优</option><option>checkpoint-980 · 最近</option><option>merged-model · 合并权重</option></select></label></div><div className="asset-summary"><div><span className="status-pill green">可部署</span><strong>{source === "training" ? "Qwen2.5-7B · LoRA 已合并权重" : source === "managed" ? "Qwen2.5-7B · 官方基模" : "Qwen2.5-7B-Instruct · 通用大模型"}</strong><small>参数规模 7.6B · 权重精度 BF16 · 最大上下文 32K</small></div><button className="link-btn" type="button">更换权重</button></div></div>;
}

function Recommendation({ source }: { source: SourceKey }) {
  return <section className="recommendation"><div className="section-head"><div><div className="eyebrow">自动推荐</div><h2>稳定优先</h2></div><span className="status-pill blue"><Sparkles size={13} />已生成</span></div><div className="recommend-hero"><strong>3090 · 24G</strong><span>vLLM 官方镜像 · FP16</span><small>根据模型规模、权重精度与优云实时库存推荐</small></div><ul><li><Check size={14} />{source === "training" ? "训练权重已合并，可直接启动" : "模型资产已通过部署检查"}</li><li><Check size={14} />优先选择库存充足的兼容 GPU</li><li><Check size={14} />为 7B 模型保留显存安全余量</li></ul><button className="ghost-button" type="button"><RotateCcw size={14} />恢复推荐值</button></section>;
}

function GpuPanel({ strategy, setStrategy, shortage, setShortage }: { strategy: string; setStrategy: (value: string) => void; shortage: boolean; setShortage: (value: boolean) => void }) {
  return <section className="gpu-panel"><div className="section-head"><div><div className="eyebrow">运行资源</div><h2>动态 GPU 调度</h2></div><span className="status-pill green"><Cpu size={13} />实时库存</span></div><p className="muted">GPU 型号来自优云接口，平台只维护显存、CUDA、计算能力和镜像兼容规则。</p><div className="strategy-line"><label>调度策略<select value={strategy} onChange={(event) => setStrategy(event.target.value)}><option>兼容优先，按候选顺序自动尝试</option><option>显存优先，优先选择更大显存</option><option>成本优先，优先选择低价库存</option><option>手动指定 GPU</option></select></label><div className="failure-rule"><TriangleAlert size={15} />无兼容库存时立即失败并提示，不创建等待实例。</div></div><div className="gpu-list">{gpuCandidates.map((gpu) => <div className={`gpu-item ${gpu.tone}`} key={gpu.name}><div className="gpu-main"><strong>{gpu.name}</strong><span>{gpu.status}</span></div><small>{gpu.fit} · 库存 {gpu.stock}</small>{gpu.tone === "warn" && <span className="gpu-note">不参与本次调度</span>}</div>)}</div><button type="button" className="shortage-btn" onClick={() => setShortage(!shortage)}>{shortage ? "恢复有库存状态" : "模拟无库存失败"}</button>{shortage && <div className="error-banner"><TriangleAlert size={16} /><div><strong>资源不足，服务创建失败</strong><span>当前没有满足模型与镜像兼容条件的优云 GPU，请调整模型量化或稍后重试。</span></div><X size={16} /></div>}</section>;
}

function Params({ expert, setExpert }: { expert: boolean; setExpert: (value: boolean) => void }) {
  return <section className="params-panel"><div className="section-head"><div><div className="eyebrow">参数方案</div><h2>关键参数</h2></div><button className="link-btn" type="button" onClick={() => setExpert(!expert)}>{expert ? <ChevronUp size={15} /> : <ChevronDown size={15} />}专家与兼容参数</button></div><div className="param-grid">{[["最大上下文", "4096 tokens"], ["计算精度", "FP16 · 推荐"], ["显存利用率", "0.80"], ["最大并发序列", "4"], ["批处理 Token", "8192"]].map(([label, value]) => <label key={label}>{label}<div className="fake-input">{value}<ChevronDown size={14} /></div></label>)}</div><div className="request-defaults"><span>API 请求默认值</span><strong>最大输出 512</strong><strong>Temperature 0.7</strong><strong>Top P 0.9</strong><strong>SSE 开启</strong></div>{expert && <div className="expert-box"><label>KV Cache 模式<select defaultValue="自动"><option>自动</option><option>固定大小</option></select></label><label>Prefix Caching<select defaultValue="平台管理"><option>平台管理</option><option>关闭</option></select></label><label>启动兼容模式<select defaultValue="按需"><option>按需</option><option>强制启用</option></select></label></div>}</section>;
}

function Footer({ created, setCreated }: { created: boolean; setCreated: (value: boolean) => void }) {
  return <div className="proto-footer"><div><span>启动摘要</span><strong>训练权重 · 优云动态调度 · 3090 24G · vLLM 官方镜像 · FP16 · 4096 context</strong></div>{created ? <div className="created-state"><Check size={15} />已创建原型服务</div> : <button className="primary-button" type="button" onClick={() => setCreated(true)}><Play size={15} />创建服务</button>}</div>;
}

function Shell({ children }: { children: React.ReactNode }) {
  return <div className="proto-shell"><Sidebar /><main className="proto-main"><Header />{children}</main></div>;
}

function VariantA() {
  const [source, setSource] = useState<SourceKey>("training");
  const [strategy, setStrategy] = useState("兼容优先，按候选顺序自动尝试");
  const [expert, setExpert] = useState(false);
  const [shortage, setShortage] = useState(false);
  const [created, setCreated] = useState(false);
  return <Shell><div className="page-title"><div><h1>创建推理服务</h1><p>四步完成部署：部署对象 → 运行资源 → 参数方案 → 确认创建</p></div><span className="prototype-note">可点击原型 · 不会创建真实服务</span></div><div className="stepper">{["部署对象", "运行资源", "参数方案", "确认创建"].map((item, index) => <div className={index === 0 ? "step active" : "step"} key={item}><span>{index + 1}</span>{item}</div>)}</div><div className="a-layout"><SourcePicker source={source} setSource={setSource} /><Recommendation source={source} /></div><GpuPanel strategy={strategy} setStrategy={setStrategy} shortage={shortage} setShortage={setShortage} /><Params expert={expert} setExpert={setExpert} /><Footer created={created} setCreated={setCreated} /></Shell>;
}

function VariantB() {
  const [source, setSource] = useState<SourceKey>("managed");
  const [strategy, setStrategy] = useState("显存优先，优先选择更大显存");
  const [expert, setExpert] = useState(false);
  const [shortage, setShortage] = useState(false);
  const [created, setCreated] = useState(false);
  return <Shell><div className="page-title compact-title"><div><h1>推理服务工作台</h1><p>所有配置集中在一页，适合熟悉模型部署的用户快速检查。</p></div><span className="status-pill green"><Sparkles size={13} />自动推荐已启用</span></div><div className="b-toolbar"><button className="tab active" type="button">部署配置</button><button className="tab" type="button">启动预览</button><button className="tab" type="button">请求示例</button><span className="toolbar-spacer" /><span className="muted">草稿 · 未提交</span></div><div className="b-layout"><div><SourcePicker source={source} setSource={setSource} compact /><GpuPanel strategy={strategy} setStrategy={setStrategy} shortage={shortage} setShortage={setShortage} /></div><div><Recommendation source={source} /><Params expert={expert} setExpert={setExpert} /></div></div><Footer created={created} setCreated={setCreated} /></Shell>;
}

function VariantC() {
  const [source, setSource] = useState<SourceKey>("general");
  const [strategy, setStrategy] = useState("成本优先，优先选择低价库存");
  const [expert, setExpert] = useState(false);
  const [shortage, setShortage] = useState(false);
  const [created, setCreated] = useState(false);
  return <Shell><div className="page-title"><div><h1>选择模型资产</h1><p>从资产开始，右侧实时预览最终服务能力。</p></div><button className="secondary-button" type="button"><WandSparkles size={15} />导入新模型</button></div><div className="c-layout"><div className="asset-column"><SourcePicker source={source} setSource={setSource} compact /><div className="capability-card"><div className="eyebrow">模型能力预览</div><h2>Qwen2.5-7B-Instruct</h2><div className="capability-grid"><span><small>最大上下文</small><strong>32K</strong></span><span><small>推荐精度</small><strong>FP16</strong></span><span><small>量化支持</small><strong>AWQ / GPTQ</strong></span><span><small>镜像</small><strong>vLLM 官方</strong></span></div></div></div><div className="c-preview"><div className="preview-head"><span className="eyebrow">部署预览</span><span className="status-pill blue">随选择实时更新</span></div><h2>通用大模型 · Qwen2.5-7B-Instruct</h2><p className="muted">这个布局把模型、上下文和部署结果放在一起，适合新手先确认“我要部署什么”。</p><div className="preview-stack"><Recommendation source={source} /><GpuPanel strategy={strategy} setStrategy={setStrategy} shortage={shortage} setShortage={setShortage} /><Params expert={expert} setExpert={setExpert} /></div></div></div><Footer created={created} setCreated={setCreated} /></Shell>;
}

export default function InferenceCreatePrototype() {
  const { variant, change } = useVariant();
  const component = useMemo(() => variant === "B" ? <VariantB /> : variant === "C" ? <VariantC /> : <VariantA />, [variant]);
  return <><style jsx global>{styles}</style>{component}<PrototypeSwitcher variant={variant} change={change} /></>;
}

const styles = `
:root { --ink:#102a43; --muted:#627d98; --line:#d9e2ec; --bg:#f4f7fb; --blue:#1769e0; --green:#087f5b; --mint:#e8faf4; --warn:#c05621; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font-family:Inter,\"Noto Sans SC\",\"Microsoft YaHei\",sans-serif; }
button,select { font:inherit; }
button { cursor:pointer; }
.proto-shell { min-height:100vh; display:flex; }
.proto-sidebar { width:208px; flex:0 0 208px; min-height:100vh; padding:28px 16px; background:#112f49; color:#9fb3c8; display:flex; flex-direction:column; }
.proto-brand { color:#fff; font-size:21px; font-weight:750; letter-spacing:.02em; padding:0 12px 42px; }.proto-brand small { display:block; margin-top:5px; color:#9fb3c8; font-size:8px; font-weight:500; letter-spacing:.08em; }
.proto-sidebar nav { display:grid; gap:7px; }.proto-sidebar nav div { padding:11px 12px; border-radius:6px; font-size:13px; }.proto-sidebar nav .active { background:#1b4868; color:#fff; font-weight:700; box-shadow:inset 3px 0 #27c499; }
.proto-user { margin-top:auto; display:grid; gap:5px; padding:12px; font-size:11px; }.proto-user strong { color:#fff; font-size:12px; }.proto-user span { color:#9fb3c8; }
.proto-main { min-width:0; width:calc(100% - 208px); padding-bottom:104px; }.proto-header { height:72px; padding:0 38px; display:flex; align-items:center; justify-content:space-between; background:#fff; border-bottom:1px solid #e7edf4; color:var(--ink); font-size:14px; font-weight:650; }.header-env { display:flex; gap:8px; align-items:center; color:var(--green); font-size:12px; }.live-dot { width:7px; height:7px; border-radius:50%; background:#27c499; box-shadow:0 0 0 3px #e8faf4; }
.page-title { max-width:1180px; margin:34px auto 22px; padding:0 12px; display:flex; align-items:flex-end; justify-content:space-between; gap:18px; }.page-title h1 { margin:0; font-size:28px; letter-spacing:-.02em; }.page-title p { margin:9px 0 0; color:var(--muted); font-size:13px; }.prototype-note { color:#7b61ff; background:#f1edff; border:1px solid #ddd5ff; border-radius:999px; padding:7px 12px; font-size:11px; white-space:nowrap; }.compact-title { align-items:center; margin-bottom:18px; }
.stepper { max-width:1180px; margin:0 auto 20px; padding:14px 18px; display:flex; gap:0; align-items:center; background:#fff; border:1px solid var(--line); border-radius:8px; }.step { flex:1; display:flex; align-items:center; gap:9px; color:#829ab1; font-size:12px; font-weight:600; }.step:not(:last-child)::after { content:""; height:1px; flex:1; margin:0 18px; background:#e1e8f0; }.step span { width:25px; height:25px; display:grid; place-items:center; border-radius:50%; background:#eef2f7; border:1px solid var(--line); }.step.active { color:var(--blue); }.step.active span { color:#fff; border-color:var(--blue); background:var(--blue); }
.a-layout,.b-layout,.c-layout { max-width:1180px; margin:0 auto; padding:0 12px; display:grid; gap:18px; }.a-layout { grid-template-columns:minmax(0,1fr) 340px; }.b-layout { grid-template-columns:minmax(0,1.1fr) minmax(320px,.9fr); }.c-layout { grid-template-columns:minmax(0,660px) minmax(0,1fr); align-items:start; }
.source-picker,.recommendation,.gpu-panel,.params-panel,.capability-card,.c-preview { background:#fff; border:1px solid var(--line); border-radius:8px; }.source-picker,.recommendation,.gpu-panel,.params-panel { padding:24px; }.source-picker.compact { padding:20px; }.eyebrow { color:#7890a7; font-size:10px; font-weight:750; letter-spacing:.09em; text-transform:uppercase; }.source-picker h2,.recommendation h2,.gpu-panel h2,.params-panel h2,.capability-card h2,.c-preview h2 { margin:7px 0 0; font-size:19px; }.muted { color:var(--muted); font-size:12px; line-height:1.6; }.source-picker > .muted { margin:8px 0 20px; }
.source-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:9px; }.source-card { position:relative; min-height:105px; padding:13px; display:flex; flex-direction:column; align-items:flex-start; gap:7px; text-align:left; background:#f8fafc; border:1px solid #e5ebf2; border-radius:7px; color:var(--ink); }.source-card:hover { border-color:#9bbce8; }.source-card.selected { border-color:#7ba9e7; background:#eff6ff; box-shadow:0 0 0 2px #dbeafe; }.source-card strong { font-size:12px; }.source-card small { color:var(--muted); font-size:10px; line-height:1.4; }.source-icon { color:var(--blue); }.source-check { position:absolute; top:11px; right:10px; color:var(--blue); }.asset-row { display:grid; grid-template-columns:1fr 1fr; gap:13px; margin-top:21px; }.asset-row label,.strategy-line label,.param-grid label,.expert-box label { display:grid; gap:7px; color:#486581; font-size:11px; font-weight:650; }.asset-row select,.strategy-line select,.expert-box select { width:100%; min-width:0; height:39px; padding:0 10px; color:var(--ink); background:#fff; border:1px solid var(--line); border-radius:5px; font-size:12px; }.asset-summary { margin-top:18px; padding:14px; display:flex; align-items:center; justify-content:space-between; gap:12px; background:#f8fafc; border:1px solid #e5ebf2; border-radius:6px; }.asset-summary > div { min-width:0; display:grid; gap:7px; }.asset-summary strong { font-size:13px; }.asset-summary small { color:var(--muted); font-size:10px; }.status-pill { display:inline-flex; align-items:center; gap:5px; width:max-content; padding:5px 8px; border-radius:999px; font-size:10px; font-weight:700; }.status-pill.green { color:var(--green); background:var(--mint); }.status-pill.blue { color:#1254a3; background:#e8f1ff; }.link-btn,.ghost-button { display:inline-flex; align-items:center; gap:5px; border:0; background:none; color:var(--blue); font-size:11px; font-weight:650; }.section-head { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; }.recommendation { min-height:100%; }.recommend-hero { display:grid; gap:6px; margin:18px 0; padding:16px; border:1px solid #b8d3f6; border-radius:6px; background:#ecf4ff; }.recommend-hero strong { color:#1254a3; font-size:17px; }.recommend-hero span { color:var(--blue); font-size:12px; font-weight:650; }.recommend-hero small { color:#55738f; font-size:10px; line-height:1.5; }.recommendation ul { display:grid; gap:11px; padding:0; margin:0; list-style:none; }.recommendation li { display:flex; gap:7px; align-items:flex-start; color:#486581; font-size:11px; line-height:1.45; }.recommendation li svg { color:#27a879; flex:0 0 auto; }.recommendation .ghost-button { margin-top:22px; }
.gpu-panel { max-width:1180px; margin:18px auto 0; }.gpu-panel > .muted { max-width:780px; margin:9px 0 20px; }.strategy-line { display:grid; grid-template-columns:minmax(260px,330px) 1fr; gap:24px; align-items:end; }.failure-rule { display:flex; align-items:center; gap:7px; color:#9c4221; background:#fff8f0; border:1px solid #f3c99a; padding:10px 12px; border-radius:5px; font-size:11px; }.gpu-list { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:9px; margin-top:18px; }.gpu-item { min-height:91px; padding:13px; border:1px solid var(--line); border-radius:6px; background:#fff; }.gpu-item.good { border-color:#74a8e8; background:#eff6ff; }.gpu-item.warn { border-color:#f3c99a; background:#fff8f0; }.gpu-main { display:flex; justify-content:space-between; gap:5px; }.gpu-main strong { font-size:12px; }.gpu-main span { color:#627d98; font-size:10px; }.gpu-item small { display:block; margin-top:12px; color:var(--muted); font-size:10px; }.gpu-note { display:block; margin-top:6px; color:var(--warn); font-size:10px; }.shortage-btn { margin-top:14px; padding:7px 10px; color:var(--warn); background:#fff; border:1px dashed #efb97d; border-radius:5px; font-size:10px; }.error-banner { display:flex; align-items:flex-start; gap:10px; margin-top:12px; padding:12px; color:#8c3d1e; background:#fff4e8; border:1px solid #f3c99a; border-radius:6px; }.error-banner > svg:first-child { flex:0 0 auto; }.error-banner div { display:grid; gap:3px; flex:1; }.error-banner strong { font-size:12px; }.error-banner span { font-size:10px; line-height:1.5; }
.params-panel { max-width:1180px; margin:18px auto 0; }.param-grid { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:10px; margin-top:18px; }.fake-input { height:37px; padding:0 10px; display:flex; justify-content:space-between; align-items:center; color:var(--ink); background:#fff; border:1px solid var(--line); border-radius:5px; font-size:11px; font-weight:500; }.request-defaults { display:flex; flex-wrap:wrap; gap:15px; align-items:center; margin-top:17px; padding-top:14px; border-top:1px solid #eef2f7; color:var(--muted); font-size:10px; }.request-defaults strong { color:#486581; font-weight:600; }.expert-box { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin-top:15px; padding:14px; background:#f8fafc; border:1px dashed var(--line); border-radius:6px; }.proto-footer { max-width:1180px; margin:18px auto 0; padding:13px 16px; display:flex; align-items:center; justify-content:space-between; gap:15px; background:#fff; border:1px solid var(--line); border-radius:8px; }.proto-footer > div:first-child { min-width:0; display:flex; align-items:center; gap:14px; }.proto-footer span { color:#486581; font-size:10px; font-weight:700; }.proto-footer strong { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--muted); font-size:10px; font-weight:500; }.primary-button,.secondary-button { display:inline-flex; align-items:center; justify-content:center; gap:6px; border-radius:5px; padding:9px 14px; font-size:11px; font-weight:700; }.primary-button { color:#fff; background:var(--blue); border:1px solid var(--blue); }.secondary-button { color:var(--blue); background:#fff; border:1px solid #b8d3f6; }.created-state { display:flex; align-items:center; gap:6px; color:var(--green); font-size:11px; font-weight:700; }.created-state svg { color:#27a879; }
.b-toolbar { max-width:1180px; margin:0 auto 15px; padding:0 12px; display:flex; align-items:center; gap:20px; border-bottom:1px solid var(--line); }.tab { position:relative; padding:11px 3px; color:var(--muted); background:none; border:0; font-size:12px; }.tab.active { color:var(--blue); font-weight:700; }.tab.active::after { content:""; position:absolute; bottom:-1px; left:0; right:0; height:2px; background:var(--blue); }.toolbar-spacer { flex:1; }.b-layout .gpu-panel { margin-top:18px; }.b-layout .params-panel { margin-top:18px; }.b-layout .gpu-list { grid-template-columns:repeat(2,minmax(0,1fr)); }.b-layout .strategy-line { grid-template-columns:1fr; gap:12px; }.b-layout .failure-rule { font-size:10px; }.b-layout .param-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }.b-layout .request-defaults { display:grid; grid-template-columns:1fr 1fr; gap:10px; }.b-layout .request-defaults span { grid-column:1 / -1; }
.capability-card { margin-top:18px; padding:20px; border-top:3px solid #06b6d4; }.capability-card h2 { margin-bottom:18px; }.capability-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:8px; }.capability-grid span { display:grid; gap:6px; padding:10px; background:#f8fafc; border-radius:5px; }.capability-grid small { color:var(--muted); font-size:10px; }.capability-grid strong { font-size:12px; }.c-preview { padding:22px; border-top:3px solid #7c5ce6; }.preview-head { display:flex; align-items:center; justify-content:space-between; }.c-preview h2 { font-size:22px; margin-top:16px; }.preview-stack { display:grid; gap:14px; margin-top:22px; }.preview-stack .recommendation,.preview-stack .gpu-panel,.preview-stack .params-panel { max-width:none; margin:0; }.preview-stack .gpu-list { grid-template-columns:repeat(2,minmax(0,1fr)); }.preview-stack .strategy-line { grid-template-columns:1fr; gap:12px; }.preview-stack .param-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
.prototype-switcher { position:fixed; z-index:10; bottom:20px; left:50%; transform:translateX(-50%); display:flex; align-items:center; gap:8px; padding:7px 9px; background:#102a43; color:#fff; border:1px solid #244d6c; border-radius:999px; box-shadow:0 12px 30px rgb(16 42 67 / 24%); }.prototype-switcher button { width:27px; height:27px; display:grid; place-items:center; color:#fff; background:#1f4e70; border:0; border-radius:50%; }.prototype-switcher span { min-width:130px; text-align:center; font-size:11px; font-weight:650; }
@media (max-width:1000px) { .proto-sidebar { width:170px; flex-basis:170px; }.proto-main { width:calc(100% - 170px); }.a-layout,.b-layout,.c-layout { grid-template-columns:1fr; }.recommendation { min-height:0; }.gpu-list { grid-template-columns:repeat(2,minmax(0,1fr)); }.param-grid { grid-template-columns:repeat(3,minmax(0,1fr)); } }
@media (max-width:680px) { .proto-sidebar { display:none; }.proto-main { width:100%; }.proto-header { padding:0 18px; }.page-title { margin-top:22px; align-items:flex-start; flex-direction:column; }.source-grid,.asset-row,.strategy-line,.param-grid,.expert-box,.capability-grid { grid-template-columns:1fr; }.gpu-list { grid-template-columns:1fr; }.proto-footer,.proto-footer > div:first-child { align-items:flex-start; flex-direction:column; }.proto-footer strong { white-space:normal; }.prototype-switcher { bottom:12px; }.stepper { overflow-x:auto; }.step { min-width:145px; } }
`;
