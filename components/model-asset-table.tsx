"use client";

import { Check, CircleAlert, Clock3, MoreHorizontal } from "lucide-react";
import type { ReactNode } from "react";
import type { ModelAssetView, ModelReadiness } from "../lib/model-asset-contracts";

function readinessLabel(item: ModelReadiness) {
  return item.state === "ready" ? "可用" : item.state === "pending" ? "待处理" : "不可用";
}

function Readiness({ item }: { item: ModelReadiness }) {
  const Icon = item.state === "ready" ? Check : item.state === "pending" ? Clock3 : CircleAlert;
  return <span className={`vision-readiness-cell ${item.state}`} title={item.reason}><Icon size={13} aria-hidden="true" /><span>{readinessLabel(item)}</span></span>;
}

export function ModelAssetTable({
  assets,
  onOpen,
  renderActions,
}: {
  assets: ModelAssetView[];
  onOpen?: (asset: ModelAssetView) => void;
  renderActions?: (asset: ModelAssetView) => ReactNode;
}) {
  return <div className="model-table-wrap"><table className="model-table"><thead><tr><th>模型</th><th>来源</th><th>训练与部署出口</th><th>最近检查</th><th>操作</th></tr></thead><tbody>{assets.map((asset) => <tr key={asset.id}>
    <td><button type="button" className="model-name-btn" onClick={() => onOpen?.(asset)}><span className={`model-avatar ${asset.assetType === "base" ? "base" : "trained"}`}>{asset.assetType === "base" ? "基" : "A"}</span><span><strong>{asset.name}</strong><small>{asset.repoId ?? "未登记 Repo ID"}{asset.revision ? ` · ${asset.revision}` : ""}</small></span></button></td>
    <td><span className={`source-tag ${asset.source === "manual" ? "manual" : "cache"}`}>{asset.source === "modelscope-cache" ? "ModelScope 缓存" : asset.source === "youyun-catalog" ? "优云模型库" : asset.source === "training-output" ? "训练产物" : "手工登记"}</span><small className="vision-model-subtext">{asset.assetType === "adapter" ? asset.lineage?.method ?? "Adapter" : "基座模型"}</small></td>
    <td><div className="vision-readiness-matrix"><div><span>本地训练</span><Readiness item={asset.readiness.localTraining} /></div><div><span>优云训练</span><Readiness item={asset.readiness.remoteTraining} /></div><div><span>推理部署</span><Readiness item={asset.readiness.inference} /></div></div></td>
    <td><span>{asset.updatedAt ? asset.updatedAt.replace("T", " ") : "未检测"}</span><small className="vision-model-message">{asset.status === "available" ? "后端状态已确认" : asset.status === "pending" ? "等待后端校验" : asset.status === "archived" ? "已归档" : "需要处理"}</small></td>
    <td>{renderActions ? renderActions(asset) : <button type="button" className="icon-only-btn" title="打开模型详情" aria-label="打开模型详情" onClick={() => onOpen?.(asset)}><MoreHorizontal size={17} /></button>}</td>
  </tr>)}</tbody></table>{assets.length === 0 && <div className="model-empty">暂无模型资产</div>}</div>;
}
