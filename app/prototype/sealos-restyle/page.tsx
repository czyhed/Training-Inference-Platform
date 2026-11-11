"use client";

/**
 * SealOS 风格原型（纯静态展示，不接业务数据）。
 * 访问 /prototype/sealos-restyle 预览；侧边栏可切换视图，仅用于评审视觉方向。
 */

import { useState } from "react";

const css = `
:root {
  --sl-primary: #1b58f1;
  --sl-primary-hover: #1749c8;
  --sl-bg: #f2f3f7;
  --sl-surface: #ffffff;
  --sl-line: #e4e7ee;
  --sl-text: #242d3f;
  --sl-muted: #8a93a6;
  --sl-danger: #e0484d;
  --sl-success: #3fb78f;
  --sl-warning: #e69b3d;
  --sl-purple: #8d6fd1;
}
.sl-root { display: flex; min-height: 100vh; background: var(--sl-bg); color: var(--sl-text); font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans SC", sans-serif; font-size: 14px; }
.sl-root * { box-sizing: border-box; }

/* Sidebar */
.sl-side { width: 200px; flex: 0 0 200px; background: var(--sl-surface); border-right: 1px solid var(--sl-line); padding: 20px 12px; display: flex; flex-direction: column; gap: 4px; }
.sl-brand { display: flex; align-items: center; gap: 10px; padding: 4px 10px 18px; }
.sl-brand-mark { width: 30px; height: 30px; border-radius: 8px; background: var(--sl-primary); display: grid; place-items: center; color: #fff; font-weight: 700; font-size: 12px; }
.sl-brand-name { font-size: 15px; font-weight: 600; }
.sl-nav-item { display: flex; align-items: center; gap: 10px; height: 38px; padding: 0 12px; border: 0; border-radius: 6px; background: transparent; color: var(--sl-text); font-size: 13px; text-align: left; cursor: pointer; }
.sl-nav-item:hover { background: #f5f6fa; }
.sl-nav-item.active { background: #eef3fe; color: var(--sl-primary); font-weight: 600; }
.sl-nav-ico { width: 16px; text-align: center; opacity: .75; }
.sl-side-foot { margin-top: auto; display: flex; align-items: center; gap: 10px; padding: 10px 8px; border-top: 1px solid var(--sl-line); }
.sl-avatar { width: 30px; height: 30px; border-radius: 50%; background: #eef3fe; color: var(--sl-primary); display: grid; place-items: center; font-size: 12px; font-weight: 700; }

/* Content */
.sl-main { flex: 1; min-width: 0; padding: 24px 32px 48px; }
.sl-title-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.sl-h1 { margin: 0; font-size: 20px; font-weight: 600; }
.sl-sub { color: var(--sl-muted); font-size: 12px; margin-top: 4px; }

/* Buttons */
.sl-btn { height: 34px; padding: 0 14px; border-radius: 6px; border: 1px solid transparent; font-size: 13px; cursor: pointer; display: inline-flex; align-items: center; gap: 6px; }
.sl-btn-primary { background: var(--sl-primary); color: #fff; }
.sl-btn-primary:hover { background: var(--sl-primary-hover); }
.sl-btn-default { background: var(--sl-surface); border-color: var(--sl-line); color: var(--sl-text); }
.sl-btn-text { border: 0; background: transparent; color: var(--sl-primary); padding: 0; height: auto; font-size: 13px; }
.sl-btn-text.danger { color: var(--sl-danger); }

/* Stat cards */
.sl-stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 16px; }
.sl-stat { background: var(--sl-surface); border: 1px solid var(--sl-line); border-radius: 8px; padding: 18px 20px; }
.sl-stat b { display: block; font-size: 24px; font-weight: 600; }
.sl-stat span { display: block; margin-top: 6px; color: var(--sl-muted); font-size: 12px; }

/* Card + table */
.sl-card { background: var(--sl-surface); border: 1px solid var(--sl-line); border-radius: 8px; margin-bottom: 16px; }
.sl-card-head { display: flex; justify-content: space-between; align-items: center; padding: 16px 20px; border-bottom: 1px solid var(--sl-line); }
.sl-card-head h2 { margin: 0; font-size: 15px; font-weight: 600; }
.sl-card-body { padding: 16px 20px; }
.sl-table { width: 100%; border-collapse: collapse; }
.sl-table th { text-align: left; font-weight: 500; color: var(--sl-muted); font-size: 12px; padding: 12px 20px; border-bottom: 1px solid var(--sl-line); background: #fafbfc; }
.sl-table td { padding: 14px 20px; font-size: 13px; border-bottom: 1px solid var(--sl-line); color: var(--sl-text); }
.sl-table tr:last-child td { border-bottom: 0; }
.sl-tag { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; }
.sl-tag i { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.sl-name-cell { font-weight: 600; }

/* Progress */
.sl-progress { width: 120px; height: 5px; border-radius: 99px; background: #edf0f5; overflow: hidden; }
.sl-progress span { display: block; height: 100%; border-radius: inherit; background: var(--sl-primary); }
.sl-progress-cell { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--sl-muted); }

/* Resource rows (overview) */
.sl-res-row { display: flex; align-items: center; gap: 12px; padding: 14px 0; border-bottom: 1px solid var(--sl-line); }
.sl-res-row:last-child { border-bottom: 0; }
.sl-res-badge { width: 34px; height: 34px; flex: 0 0 34px; border-radius: 6px; background: #eef3fe; color: var(--sl-primary); display: grid; place-items: center; font-size: 11px; font-weight: 700; }
.sl-res-badge.green { background: #ecf8f3; color: var(--sl-success); }
.sl-res-copy { min-width: 0; }
.sl-res-copy strong { display: block; font-size: 13px; font-weight: 600; }
.sl-res-copy small { display: block; margin-top: 3px; color: var(--sl-muted); font-size: 12px; }
.sl-res-state { margin-left: auto; color: var(--sl-muted); font-size: 12px; }
.sl-gpu-note { margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--sl-line); color: var(--sl-muted); font-size: 12px; }

/* Form */
.sl-form-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px 20px; }
.sl-field label { display: block; font-size: 13px; margin-bottom: 6px; }
.sl-field label em { color: var(--sl-danger); font-style: normal; }
.sl-field small { display: block; margin-top: 5px; color: var(--sl-muted); font-size: 12px; }
.sl-input, .sl-select, .sl-textarea { width: 100%; height: 34px; border: 1px solid var(--sl-line); border-radius: 6px; padding: 0 10px; background: var(--sl-surface); color: var(--sl-text); font: inherit; font-size: 13px; outline: none; }
.sl-textarea { height: auto; min-height: 72px; padding: 8px 10px; resize: vertical; }
.sl-input:focus, .sl-select:focus, .sl-textarea:focus { border-color: var(--sl-primary); box-shadow: 0 0 0 2px rgba(27, 88, 241, .12); }
.sl-input:disabled { background: #f7f8fa; color: var(--sl-muted); }

/* Segmented */
.sl-segmented { display: inline-flex; padding: 3px; border-radius: 6px; background: #eef0f4; gap: 3px; }
.sl-segmented button { height: 30px; padding: 0 16px; border: 0; border-radius: 5px; background: transparent; color: var(--sl-muted); font-size: 12px; cursor: pointer; }
.sl-segmented button.on { background: var(--sl-surface); color: var(--sl-primary); font-weight: 600; box-shadow: 0 1px 2px rgba(36, 45, 63, .08); }

/* Option cards */
.sl-option-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }
.sl-option { border: 1px solid var(--sl-line); border-radius: 8px; padding: 14px; text-align: left; background: var(--sl-surface); cursor: pointer; }
.sl-option.on { border-color: var(--sl-primary); box-shadow: 0 0 0 1px var(--sl-primary); }
.sl-option strong { display: block; font-size: 13px; font-weight: 600; }
.sl-option small { display: block; margin-top: 4px; color: var(--sl-muted); font-size: 12px; }

/* Toggle */
.sl-toggle-row { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 12px 0; border-bottom: 1px solid var(--sl-line); }
.sl-toggle-row:last-child { border-bottom: 0; }
.sl-toggle-copy strong { display: block; font-size: 13px; font-weight: 600; }
.sl-toggle-copy small { display: block; margin-top: 3px; color: var(--sl-muted); font-size: 12px; }
.sl-switch { position: relative; width: 36px; height: 20px; flex: 0 0 36px; border-radius: 99px; background: #cdd3de; transition: .2s; }
.sl-switch::after { content: ""; position: absolute; top: 2px; left: 2px; width: 16px; height: 16px; border-radius: 50%; background: #fff; box-shadow: 0 1px 2px rgba(0,0,0,.2); transition: .2s; }
.sl-switch.on { background: var(--sl-primary); }
.sl-switch.on::after { transform: translateX(16px); }

.sl-toolbar { display: flex; gap: 10px; margin-bottom: 16px; }
.sl-tip { margin-top: 16px; padding: 10px 12px; border-radius: 6px; background: #f7f8fa; color: var(--sl-muted); font-size: 12px; }
.sl-foot-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 16px; }
`;

const statusColor: Record<string, string> = {
  已发布: "var(--sl-success)",
  校验中: "var(--sl-warning)",
  待发布: "var(--sl-muted)",
  运行中: "var(--sl-primary)",
  已完成: "var(--sl-success)",
  已停止: "var(--sl-muted)",
  失败: "var(--sl-danger)",
  在线: "var(--sl-success)",
  已下线: "var(--sl-muted)",
};

function Tag({ status }: { status: string }) {
  return (
    <span className="sl-tag" style={{ color: statusColor[status] ?? "var(--sl-muted)" }}>
      <i />
      {status}
    </span>
  );
}

/* ---------------- Overview ---------------- */
function OverviewView() {
  return (
    <>
      <div className="sl-stats">
        <div className="sl-stat"><b>12</b><span>数据集</span></div>
        <div className="sl-stat"><b>5</b><span>微调任务</span></div>
        <div className="sl-stat"><b>9</b><span>模型资产</span></div>
        <div className="sl-stat"><b>3</b><span>推理服务</span></div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 16 }}>
        <div className="sl-card" style={{ margin: 0 }}>
          <div className="sl-card-head"><h2>计算资源</h2><span style={{ color: "var(--sl-muted)", fontSize: 12 }}>资源空闲</span></div>
          <div className="sl-card-body">
            <div className="sl-res-row">
              <div className="sl-res-badge">GPU</div>
              <div className="sl-res-copy">
                <strong>RTX 4090 · 24GB</strong>
                <small>本地单卡，训练与推理互斥使用</small>
              </div>
              <span className="sl-res-state">空闲</span>
            </div>
            <div className="sl-res-row">
              <div className="sl-res-badge green">存储</div>
              <div className="sl-res-copy">
                <strong>共享模型存储 · 82% 已用</strong>
                <small>业务数据 46GB / 模型权重 210GB</small>
              </div>
              <span className="sl-res-state">正常</span>
            </div>
            <p className="sl-gpu-note">本地训练与本地推理共用单卡并保持互斥；优云训练和推理按独立实例分配，可同时运行。</p>
          </div>
        </div>
        <div className="sl-card" style={{ margin: 0 }}>
          <div className="sl-card-head"><h2>当前负载</h2></div>
          <div className="sl-card-body">
            <div className="sl-res-row">
              <div className="sl-res-badge">训</div>
              <div className="sl-res-copy">
                <strong>qwen2.5-7b-sft-v2</strong>
                <small>训练中 · 第 3/3 epoch · 68%</small>
              </div>
              <span className="sl-res-state">›</span>
            </div>
            <div className="sl-res-row">
              <div className="sl-res-badge green">推</div>
              <div className="sl-res-copy">
                <strong>qwen2.5-7b-chat</strong>
                <small>运行中 · QPS 4.2</small>
              </div>
              <span className="sl-res-state">›</span>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

/* ---------------- Datasets ---------------- */
const datasetRows = [
  { name: "客服对话语体-v3", type: "文本生成", count: "12,400 条", status: "已发布", time: "2026-08-28 14:02" },
  { name: "图文对齐-营销素材", type: "文生图", count: "3,120 条", status: "校验中", time: "2026-08-27 09:41" },
  { name: "指令微调-内部工单", type: "文本生成", count: "860 条", status: "待发布", time: "2026-08-26 18:20" },
];

function DatasetsView() {
  return (
    <div className="sl-card">
      <div className="sl-card-head">
        <h2>数据集列表</h2>
        <div className="sl-toolbar" style={{ margin: 0 }}>
          <input className="sl-input" style={{ width: 220 }} placeholder="搜索数据集名称" />
          <select className="sl-select" style={{ width: 130 }}><option>全部类型</option></select>
          <select className="sl-select" style={{ width: 130 }}><option>全部状态</option></select>
        </div>
      </div>
      <table className="sl-table">
        <thead>
          <tr><th>数据集名称</th><th>任务类型</th><th>数据量</th><th>状态</th><th>更新时间</th><th>操作</th></tr>
        </thead>
        <tbody>
          {datasetRows.map((r) => (
            <tr key={r.name}>
              <td className="sl-name-cell">{r.name}</td>
              <td>{r.type}</td>
              <td>{r.count}</td>
              <td><Tag status={r.status} /></td>
              <td style={{ color: "var(--sl-muted)" }}>{r.time}</td>
              <td>
                <button className="sl-btn-text">查看</button>
                <button className="sl-btn-text danger" style={{ marginLeft: 12 }}>删除</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ---------------- Training ---------------- */
const trainingRows = [
  { name: "qwen2.5-7b-sft-v2", base: "Qwen2.5-7B-Instruct", dataset: "客服对话语体-v3", method: "SFT · LoRA", status: "运行中", progress: 68, time: "2026-08-28 10:12" },
  { name: "llama3-8b-dpo", base: "Llama-3-8B", dataset: "指令微调-内部工单", method: "DPO · QLoRA", status: "已完成", progress: 100, time: "2026-08-27 16:40" },
  { name: "qwen2.5-3b-ops", base: "Qwen2.5-3B", dataset: "客服对话语体-v3", method: "SFT · full", status: "失败", progress: 42, time: "2026-08-26 09:05" },
];

function TrainingView() {
  return (
    <div className="sl-card">
      <div className="sl-card-head">
        <h2>微调任务</h2>
        <div className="sl-toolbar" style={{ margin: 0 }}>
          <input className="sl-input" style={{ width: 220 }} placeholder="搜索任务名称或任务 ID" />
          <select className="sl-select" style={{ width: 130 }}><option>全部状态</option></select>
          <button className="sl-btn sl-btn-primary">新建微调任务</button>
        </div>
      </div>
      <table className="sl-table">
        <thead>
          <tr><th>任务名称</th><th>基础模型</th><th>数据集</th><th>训练方式</th><th>状态 / 进度</th><th>创建时间</th><th>操作</th></tr>
        </thead>
        <tbody>
          {trainingRows.map((r) => (
            <tr key={r.name}>
              <td className="sl-name-cell">{r.name}</td>
              <td>{r.base}</td>
              <td>{r.dataset}</td>
              <td>{r.method}</td>
              <td>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <Tag status={r.status} />
                  <span className="sl-progress-cell">
                    <span className="sl-progress"><span style={{ width: `${r.progress}%`, background: r.status === "失败" ? "var(--sl-danger)" : undefined }} /></span>
                    {r.progress}%
                  </span>
                </div>
              </td>
              <td style={{ color: "var(--sl-muted)" }}>{r.time}</td>
              <td>
                <button className="sl-btn-text">详情</button>
                {r.status === "失败" && <button className="sl-btn-text" style={{ marginLeft: 12 }}>重试</button>}
                {r.status === "运行中" && <button className="sl-btn-text danger" style={{ marginLeft: 12 }}>停止</button>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ---------------- Models ---------------- */
const modelRows = [
  { name: "qwen2.5-7b-sft-v2", source: "微调 · qwen2.5-7b-sft-v2", size: "14.5 GB", status: "可用", time: "2026-08-28 12:30" },
  { name: "llama3-8b-dpo-best", source: "微调 · llama3-8b-dpo", size: "15.9 GB", status: "可用", time: "2026-08-27 20:11" },
  { name: "Qwen2.5-72B-Instruct", source: "基础模型 · 远端", size: "—", status: "不可用", time: "2026-08-20 11:00" },
];

function ModelsView() {
  return (
    <div className="sl-card">
      <div className="sl-card-head">
        <h2>模型管理</h2>
        <div className="sl-toolbar" style={{ margin: 0 }}>
          <input className="sl-input" style={{ width: 220 }} placeholder="搜索模型名称" />
          <select className="sl-select" style={{ width: 130 }}><option>全部来源</option></select>
        </div>
      </div>
      <table className="sl-table">
        <thead>
          <tr><th>模型名称</th><th>来源</th><th>大小</th><th>状态</th><th>更新时间</th><th>操作</th></tr>
        </thead>
        <tbody>
          {modelRows.map((r) => (
            <tr key={r.name}>
              <td className="sl-name-cell">{r.name}</td>
              <td>{r.source}</td>
              <td>{r.size}</td>
              <td><Tag status={r.status} /></td>
              <td style={{ color: "var(--sl-muted)" }}>{r.time}</td>
              <td>
                <button className="sl-btn-text">用于训练</button>
                <button className="sl-btn-text" style={{ marginLeft: 12 }}>用于部署</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ---------------- Inference ---------------- */
const inferenceRows = [
  { name: "qwen2.5-7b-chat", model: "qwen2.5-7b-sft-v2", gpu: "RTX 4090 ×1", status: "运行中", endpoint: "http://10.0.0.8:8000/v1", time: "2026-08-28 09:00" },
  { name: "llama3-8b-api", model: "llama3-8b-dpo-best", gpu: "优云 A800 ×1", status: "在线", endpoint: "http://优云实例:8000/v1", time: "2026-08-27 15:22" },
  { name: "qwen2.5-3b-test", model: "qwen2.5-3b-ops", gpu: "—", status: "已下线", endpoint: "—", time: "2026-08-25 17:48" },
];

function InferenceView() {
  return (
    <div className="sl-card">
      <div className="sl-card-head">
        <h2>推理服务</h2>
        <div className="sl-toolbar" style={{ margin: 0 }}>
          <input className="sl-input" style={{ width: 220 }} placeholder="搜索服务名称" />
          <select className="sl-select" style={{ width: 130 }}><option>全部状态</option></select>
          <button className="sl-btn sl-btn-primary">创建推理服务</button>
        </div>
      </div>
      <table className="sl-table">
        <thead>
          <tr><th>服务名称</th><th>模型</th><th>资源</th><th>状态</th><th>端点</th><th>创建时间</th><th>操作</th></tr>
        </thead>
        <tbody>
          {inferenceRows.map((r) => (
            <tr key={r.name}>
              <td className="sl-name-cell">{r.name}</td>
              <td>{r.model}</td>
              <td>{r.gpu}</td>
              <td><Tag status={r.status} /></td>
              <td><code style={{ fontSize: 12, color: "var(--sl-muted)" }}>{r.endpoint}</code></td>
              <td style={{ color: "var(--sl-muted)" }}>{r.time}</td>
              <td>
                <button className="sl-btn-text">详情</button>
                {r.status === "已下线"
                  ? <button className="sl-btn-text" style={{ marginLeft: 12 }}>启动</button>
                  : <button className="sl-btn-text danger" style={{ marginLeft: 12 }}>停止</button>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ---------------- Create form example ---------------- */
function CreateFormView() {
  const [mode, setMode] = useState<"sft" | "dpo">("sft");
  const [model, setModel] = useState("qwen7b");
  const [adv, setAdv] = useState(false);
  return (
    <>
      <div className="sl-card">
        <div className="sl-card-head"><h2>基础配置</h2></div>
        <div className="sl-card-body">
          <div className="sl-form-grid">
            <div className="sl-field">
              <label>任务名称 <em>*</em></label>
              <input className="sl-input" placeholder="例如：qwen2.5-7b-sft-v2" />
              <small>支持小写字母、数字与连字符。</small>
            </div>
            <div className="sl-field">
              <label>训练模式 <em>*</em></label>
              <div className="sl-segmented">
                <button className={mode === "sft" ? "on" : ""} onClick={() => setMode("sft")}>SFT 监督微调</button>
                <button className={mode === "dpo" ? "on" : ""} onClick={() => setMode("dpo")}>DPO 偏好对齐</button>
              </div>
              <small>{mode === "sft" ? "基于指令数据集进行监督微调。" : "基于偏好对比数据进行人类偏好对齐。"}</small>
            </div>
          </div>
          <div className="sl-field" style={{ marginTop: 16 }}>
            <label>基础模型 <em>*</em></label>
            <div className="sl-option-grid">
              <button className={`sl-option${model === "qwen7b" ? " on" : ""}`} onClick={() => setModel("qwen7b")}>
                <strong>Qwen2.5-7B-Instruct</strong>
                <small>已下载 · 14.5 GB · 上下文 32K</small>
              </button>
              <button className={`sl-option${model === "llama8b" ? " on" : ""}`} onClick={() => setModel("llama8b")}>
                <strong>Llama-3-8B</strong>
                <small>已下载 · 15.9 GB · 上下文 8K</small>
              </button>
            </div>
          </div>
          <div className="sl-form-grid" style={{ marginTop: 16 }}>
            <div className="sl-field">
              <label>训练数据集 <em>*</em></label>
              <select className="sl-select"><option>客服对话语体-v3（12,400 条）</option></select>
            </div>
            <div className="sl-field">
              <label>输出目录名称</label>
              <input className="sl-input" disabled value="自动按任务名生成" />
            </div>
          </div>
        </div>
      </div>

      <div className="sl-card">
        <div className="sl-card-head">
          <h2>高级设置</h2>
          <button className="sl-btn sl-btn-default" onClick={() => setAdv(!adv)}>{adv ? "收起" : "展开"}</button>
        </div>
        {adv && (
          <div className="sl-card-body" style={{ borderTop: "1px solid var(--sl-line)" }}>
            <div className="sl-form-grid">
              <div className="sl-field"><label>学习率</label><input className="sl-input" defaultValue="5e-5" /></div>
              <div className="sl-field"><label>Batch Size</label><select className="sl-select"><option>4</option><option>8</option></select></div>
              <div className="sl-field"><label>训练轮数</label><input className="sl-input" defaultValue="3" /></div>
              <div className="sl-field"><label>LoRA 秩 (r)</label><input className="sl-input" defaultValue="8" /></div>
            </div>
            <div style={{ marginTop: 8 }}>
              <div className="sl-toggle-row">
                <div className="sl-toggle-copy"><strong>启用梯度累积</strong><small>显存不足时以时间换显存。</small></div>
                <span className="sl-switch on" />
              </div>
              <div className="sl-toggle-row">
                <div className="sl-toggle-copy"><strong>训练后自动评估</strong><small>使用验证集生成评估指标。</small></div>
                <span className="sl-switch" />
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="sl-tip">本地训练与本地推理共用单卡并保持互斥；提交前会自动校验资源与参数。</div>
      <div className="sl-foot-actions">
        <button className="sl-btn sl-btn-default">取消</button>
        <button className="sl-btn sl-btn-primary">提交训练任务</button>
      </div>
    </>
  );
}

/* ---------------- Shell ---------------- */
const navItems = [
  { key: "overview", name: "概览", icon: "▣" },
  { key: "datasets", name: "数据集", icon: "⛁" },
  { key: "training", name: "微调任务", icon: "⚗" },
  { key: "models", name: "模型管理", icon: "◈" },
  { key: "inference", name: "推理服务", icon: "◉" },
  { key: "create", name: "创建表单示例", icon: "✎" },
] as const;

type ViewKey = (typeof navItems)[number]["key"];

const viewMeta: Record<ViewKey, { title: string; sub: string }> = {
  overview: { title: "系统概览", sub: "监控数据资产、训练、模型、推理服务与当前计算资源" },
  datasets: { title: "数据集", sub: "管理用于文本生成与文生图微调的数据资产" },
  training: { title: "微调任务", sub: "管理 LLaMA-Factory 文本生成 SFT / DPO LoRA 与 QLoRA 训练任务" },
  models: { title: "模型管理", sub: "管理基础模型与微调产出的模型资产" },
  inference: { title: "推理服务", sub: "管理 vLLM 推理服务的创建、上线与监控" },
  create: { title: "新建微调任务", sub: "创建表单在新风格下的样式示例（含分段选择、选项卡、开关）" },
};

export default function SealosRestylePrototypePage() {
  const [view, setView] = useState<ViewKey>("datasets");
  return (
    <div className="sl-root">
      <style dangerouslySetInnerHTML={{ __html: css }} />
      <aside className="sl-side">
        <div className="sl-brand">
          <div className="sl-brand-mark">训</div>
          <div className="sl-brand-name">训推平台</div>
        </div>
        {navItems.map((item) => (
          <button key={item.key} className={`sl-nav-item${view === item.key ? " active" : ""}`} onClick={() => setView(item.key)}>
            <span className="sl-nav-ico">{item.icon}</span>
            {item.name}
          </button>
        ))}
        <div className="sl-side-foot">
          <div className="sl-avatar">研</div>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600 }}>大模型研发中心</div>
            <div style={{ fontSize: 11, color: "var(--sl-muted)" }}>研发空间</div>
          </div>
        </div>
      </aside>

      <main className="sl-main">
        <div className="sl-title-row">
          <div>
            <h1 className="sl-h1">{viewMeta[view].title}</h1>
            <p className="sl-sub">{viewMeta[view].sub}</p>
          </div>
          {view === "datasets" && (
            <div style={{ display: "flex", gap: 10 }}>
              <button className="sl-btn sl-btn-default">导入数据集</button>
              <button className="sl-btn sl-btn-primary">+ 新建数据集</button>
            </div>
          )}
        </div>
        {view === "overview" && <OverviewView />}
        {view === "datasets" && <DatasetsView />}
        {view === "training" && <TrainingView />}
        {view === "models" && <ModelsView />}
        {view === "inference" && <InferenceView />}
        {view === "create" && <CreateFormView />}
      </main>
    </div>
  );
}
