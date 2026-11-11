"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { LibraryBig, Plus, RefreshCw, ScanSearch, Search, Trash2, X } from "lucide-react";
import { apiUrl } from "../lib/api-config";
import { modelAssetRequest } from "../lib/model-asset-api";
import { ModelAssetManagementShell, type ModelAssetTab } from "./model-asset-management-shell";
import { normalizeModelAsset, type ModelAssetView } from "../lib/model-asset-contracts";

type CatalogItem = {
  id: string;
  label: string;
  group: "base" | "trained";
  repoId: string;
  modelType: string;
  taskId?: string;
  adapterVariant?: "best" | "final";
  readyForYouyun: boolean;
  supportedTrainingModes: Array<"lora" | "qlora">;
  reason?: string | null;
  archived?: boolean;
};

type ModelFilter = ModelAssetTab | "pending";

type ImageBaseModelPreset = {
  id: string;
  name: string;
  repoId: string;
  revision: string;
  modelType: "SDXL";
  workload: "文生图";
  capabilities: Array<"inference" | "lora">;
  useCase: string;
  resourceHint: string;
  youyunAvailability: "ready" | "cache_required";
  recommended?: boolean;
};

const emptyRegisterForm = { name: "", repo: "", revision: "main", path: "" };

function suggestedModelPath(repoId: string) {
  const modelName = repoId.trim().split("/").filter(Boolean).pop() || "model";
  return `D:\\models\\${modelName}`;
}

const IMAGE_BASE_MODEL_PRESETS: readonly ImageBaseModelPreset[] = [
  {
    id: "sdxl-base-1-0",
    name: "Stable Diffusion XL Base 1.0",
    repoId: "stabilityai/stable-diffusion-xl-base-1.0",
    revision: "main",
    modelType: "SDXL",
    workload: "文生图",
    capabilities: ["inference", "lora"],
    useCase: "当前文生图微调契约的首选 SDXL 基座，适合 Caption 数据集和 LoRA 训练。",
    resourceHint: "建议使用优云 Diffusers 镜像与 24GB 以上显存。",
    youyunAvailability: "ready",
    recommended: true,
  },
  {
    id: "sdxl-turbo",
    name: "Stable Diffusion XL Turbo",
    repoId: "stabilityai/sdxl-turbo",
    revision: "main",
    modelType: "SDXL",
    workload: "文生图",
    capabilities: ["inference"],
    useCase: "低步数快速文生图验证，适合实时预览和吞吐优先的推理场景。",
    resourceHint: "需要优云缓存与 Diffusers 探针；不作为当前 LoRA 默认训练基模。",
    youyunAvailability: "cache_required",
  },
  {
    id: "ssd-1b",
    name: "Segmind SSD-1B",
    repoId: "segmind/SSD-1B",
    revision: "main",
    modelType: "SDXL",
    workload: "文生图",
    capabilities: ["inference"],
    useCase: "更小体量的 SDXL 衍生模型，适合资源受限的文生图推理验证。",
    resourceHint: "需要优云缓存与 Diffusers 探针；训练兼容性需单独确认。",
    youyunAvailability: "cache_required",
  },
  {
    id: "dreamshaper-xl-1-0",
    name: "DreamShaper XL 1.0",
    repoId: "Lykon/dreamshaper-xl-1-0",
    revision: "main",
    modelType: "SDXL",
    workload: "文生图",
    capabilities: ["inference", "lora"],
    useCase: "基于 SDXL Base 微调的通用风格模型，适合创意图像推理和风格实验。",
    resourceHint: "需要优云缓存与 Diffusers 探针；登记后再确认 LoRA 训练兼容性。",
    youyunAvailability: "cache_required",
  },
];

const DEFAULT_IMAGE_BASE_MODEL_PRESET = IMAGE_BASE_MODEL_PRESETS[0];

type ImageGenerationModelTab = Exclude<ModelAssetTab, "catalog">;
const filterOptions: Array<[ImageGenerationModelTab, string]> = [
  ["all", "全部模型"],
  ["base", "基模"],
  ["trained", "训练模型"],
  ["archived", "回收站"],
];

function groupLabel(group: CatalogItem["group"]) {
  return group === "base" ? "基座模型" : "训练 Adapter";
}

function modeLabel(mode: "lora" | "qlora") {
  return mode === "lora" ? "LoRA" : "QLoRA";
}

function readinessLabel(item: CatalogItem) {
  return item.readyForYouyun ? "优云可用" : "待处理";
}

export function ImageGenerationModels() {
  const [items, setItems] = useState<CatalogItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<ModelFilter>("all");
  const [query, setQuery] = useState("");
  const [detailId, setDetailId] = useState<string | null>(null);
  const [showRegister, setShowRegister] = useState(false);
  const [registerForm, setRegisterForm] = useState(emptyRegisterForm);
  const [selectedPresetId, setSelectedPresetId] = useState<string | null>(null);
  const [recommendationsEnabled, setRecommendationsEnabled] = useState(true);
  const [registering, setRegistering] = useState(false);
  const [registerError, setRegisterError] = useState("");
  const [showCatalog, setShowCatalog] = useState(false);
  const [catalogQuery, setCatalogQuery] = useState("");
  const [refreshingId, setRefreshingId] = useState("");
  const assetViews = useMemo<ModelAssetView[]>(() => items.map((item) => normalizeModelAsset(item, "image-generation")), [items]);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const body = await modelAssetRequest<unknown>("/image-generation/models?provider=youyun&includeArchived=true");
      setItems(Array.isArray(body) ? body as CatalogItem[] : []);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "读取模型目录失败。");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  useEffect(() => {
    if (!detailId) return undefined;
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape") setDetailId(null); };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [detailId]);

  const counts = useMemo(() => ({
    all: items.filter((item) => !item.archived).length,
    base: items.filter((item) => !item.archived && item.group === "base").length,
    trained: items.filter((item) => !item.archived && item.group === "trained").length,
    archived: items.filter((item) => item.archived).length,
    pending: items.filter((item) => !item.archived && !item.readyForYouyun).length,
    ready: items.filter((item) => !item.archived && item.readyForYouyun).length,
  }), [items]);

  const visibleItems = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return items.filter((item) => {
      const matchesFilter = filter === "archived" ? Boolean(item.archived) : !item.archived && (filter === "all" || filter === item.group || (filter === "pending" && !item.readyForYouyun));
      const haystack = [item.label, item.repoId, item.modelType, item.taskId, item.adapterVariant, item.reason].filter(Boolean).join(" ").toLowerCase();
      return matchesFilter && (!normalizedQuery || haystack.includes(normalizedQuery));
    });
  }, [filter, items, query]);

  const detail = useMemo(() => items.find((item) => item.id === detailId) ?? null, [detailId, items]);
  const registeredRepoIds = useMemo(() => items.filter((item) => item.group === "base").map((item) => item.repoId), [items]);
  const catalogModels = useMemo(() => {
    const normalizedQuery = catalogQuery.trim().toLowerCase();
    return IMAGE_BASE_MODEL_PRESETS.filter((preset) => preset.workload === "文生图" && preset.modelType === "SDXL" && preset.capabilities.includes("inference") && preset.recommended !== false && (!normalizedQuery || [preset.name, preset.repoId, preset.modelType, preset.workload, preset.useCase].join(" ").toLowerCase().includes(normalizedQuery)));
  }, [catalogQuery]);
  const selectedPreset = useMemo(() => IMAGE_BASE_MODEL_PRESETS.find((preset) => preset.id === selectedPresetId) ?? null, [selectedPresetId]);
  const recommendationPreset = selectedPreset ?? (recommendationsEnabled ? DEFAULT_IMAGE_BASE_MODEL_PRESET : null);
  const suggestedRepo = recommendationPreset?.repoId ?? "";
  const suggestedPath = useMemo(() => suggestedModelPath(registerForm.repo || suggestedRepo), [registerForm.repo, suggestedRepo]);
  const suggestedName = recommendationPreset?.name ?? "";
  const suggestedRevision = recommendationPreset?.revision ?? "";

  function resetFilters() {
    setFilter("all");
    setQuery("");
  }

  function assetIdForItem(item: CatalogItem) {
    return item.group === "trained" ? item.id.split(":", 1)[0] : item.id;
  }

  async function updateArchive(item: CatalogItem, archived: boolean) {
    if (archived && !window.confirm("移入回收站后，该模型不会出现在训练和推理选择器中。确认继续？")) return;
    setError("");
    try {
      const response = await fetch(apiUrl(`/model-assets/${assetIdForItem(item)}`), {
        method: "PATCH",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ archived }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `更新模型状态失败（${response.status}）`);
      setDetailId(archived ? null : item.id);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "更新模型状态失败。");
    }
  }

  async function refreshAsset(item: CatalogItem) {
    const assetId = assetIdForItem(item);
    setRefreshingId(assetId);
    setError("");
    try {
      const response = await fetch(apiUrl(`/model-assets/${assetId}/refresh`), { method: "POST", credentials: "include" });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `刷新模型校验失败（${response.status}）`);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "刷新模型校验失败。");
    } finally {
      setRefreshingId("");
    }
  }

  function updateRegisterForm(field: keyof typeof emptyRegisterForm, value: string) {
    if (field === "repo" && value.trim() && selectedPreset && value.trim().toLowerCase() !== selectedPreset.repoId.trim().toLowerCase()) {
      setSelectedPresetId(null);
    }
    setRegisterForm((current) => ({ ...current, [field]: value }));
  }

  function chooseCustomModel() {
    setSelectedPresetId(null);
    setRecommendationsEnabled(false);
    setRegisterForm(emptyRegisterForm);
    setRegisterError("");
  }

  function fillSuggestedField(field: keyof typeof emptyRegisterForm, suggestion: string) {
    if (!suggestion.trim()) return;
    setRegisterForm((current) => (current[field].trim() ? current : { ...current, [field]: suggestion }));
  }

  function fillSuggestedPath() {
    fillSuggestedField("path", suggestedPath);
  }

  function openPreset(preset: ImageBaseModelPreset) {
    const registered = items.find((item) => item.group === "base" && item.repoId.trim().toLowerCase() === preset.repoId.trim().toLowerCase());
    if (registered) {
      setShowCatalog(false);
      setDetailId(registered.id);
      return;
    }
    setRecommendationsEnabled(true);
    setSelectedPresetId(preset.id);
    setRegisterForm((current) => ({ ...current, name: preset.name, repo: preset.repoId, revision: preset.revision }));
    setRegisterError("");
    setShowCatalog(false);
    setShowRegister(true);
  }

  async function submitRegister(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (registering) return;
    if (!registerForm.name.trim() || !registerForm.repo.trim() || !registerForm.path.trim()) {
      setRegisterError("请填写显示名称、ModelScope Repo ID 和服务器本地目录。");
      return;
    }
    setRegistering(true);
    setRegisterError("");
    try {
      const response = await fetch(apiUrl("/model-assets/base"), {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: registerForm.name.trim(),
          repo: registerForm.repo.trim(),
          revision: registerForm.revision.trim() || "main",
          path: registerForm.path.trim(),
        }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `登记基模失败（${response.status}）`);
      setShowRegister(false);
      setRegisterForm(emptyRegisterForm);
      setSelectedPresetId(null);
      setRecommendationsEnabled(true);
      await load();
    } catch (cause) {
      setRegisterError(cause instanceof Error ? cause.message : "登记基模失败。");
    } finally {
      setRegistering(false);
    }
  }

  return (
    <ModelAssetManagementShell
      modelType="image-generation"
      eyebrow="文生图 / 模型资产"
      subtitle="集中查看基座模型、训练 Adapter、兼容模式与优云部署状态"
      headerActions={<><button type="button" className="secondary-btn icon-text-btn" onClick={() => setShowCatalog(true)}><LibraryBig size={16} />浏览基模目录</button><button type="button" className="primary-btn icon-text-btn" onClick={() => { setSelectedPresetId(null); setRecommendationsEnabled(true); setRegisterError(""); setShowRegister(true); }}><Plus size={16} />登记基模</button></>}
      error={error}
      summaries={[
        { label: "模型资产", value: assetViews.filter((asset) => !asset.archived).length, description: "已登记基座与训练产物", tone: "indigo" },
        { label: "优云可用", value: assetViews.filter((asset) => !asset.archived && asset.readiness.remoteTraining.state === "ready").length, description: "通过文生图服务端校验", tone: "green" },
        { label: "基座模型", value: counts.base, description: "用于微调和推理的模型", tone: "blue" },
        { label: "待处理", value: assetViews.filter((asset) => !asset.archived && asset.status !== "available").length, description: "需要补齐文件或服务端校验", tone: "amber" },
      ]}
      tabs={filterOptions.map(([key, label]) => ({ key, label, count: counts[key] }))}
      activeTab={filter === "pending" ? "all" : filter}
      onTabChange={(next) => setFilter(next)}
      query={query}
      onQueryChange={setQuery}
      searchPlaceholder="搜索名称、ModelScope Repo ID 或训练任务 ID"
      onRefresh={() => void load()}
      loading={loading}
      filters={<button type="button" className={`secondary-btn compact-btn${filter === "pending" ? " selected" : ""}`} onClick={() => setFilter(filter === "pending" ? "all" : "pending")}>待处理 {counts.pending}</button>}
    >
      <div className="model-section image-generation-model-group">
        <div className="model-section-head"><div><h2>模型资产目录</h2><p>LoRA / QLoRA 兼容性和优云可用性按资产单独判定</p></div><span>{visibleItems.length} / {items.length} 个</span></div>
        <div className="model-table-wrap image-generation-model-table-wrap">
          {loading && !items.length ? <div className="model-empty">正在读取模型目录...</div> : visibleItems.length === 0 ? <div className="model-empty first-use-empty"><strong>{items.length ? "没有匹配的模型" : "还没有文生图模型资产"}</strong><span>{items.length ? "请调整搜索词或筛选条件。" : "先从推荐目录选择一个 SDXL 基模，或登记已有本地模型；训练 Adapter 完成后会自动同步。"}</span>{items.length ? <button type="button" className="link-btn empty-action" onClick={resetFilters}>清除筛选</button> : <button type="button" className="primary-btn compact-btn empty-action" onClick={() => setShowRegister(true)}><Plus size={14} />登记基模</button>}</div> : (
            <table className="model-table image-generation-model-table"><thead><tr><th>模型资产</th><th>类型 / 来源</th><th>训练兼容</th><th>优云状态</th><th>Adapter 版本 / 任务</th><th>操作</th></tr></thead><tbody>{visibleItems.map((item) => <tr key={item.id}>
              <td><button type="button" className="model-name-btn" title="打开模型详情" aria-label="打开模型详情" onClick={() => setDetailId(item.id)}><span className={`model-avatar ${item.group}`} aria-hidden="true">{item.group === "base" ? "基" : "A"}</span><span><strong>{item.label}</strong><small>{item.repoId}</small></span></button></td>
              <td><span className={`source-tag ${item.group === "base" ? "manual" : "cache"}`}>{groupLabel(item.group)}</span><span className="image-generation-model-subtext">{item.modelType}</span></td>
              <td><div className="image-generation-mode-list">{(["lora", "qlora"] as const).map((mode) => <span className={`method-tag ${item.supportedTrainingModes.includes(mode) ? "supported" : "unsupported"}`} key={mode}>{modeLabel(mode)}{!item.supportedTrainingModes.includes(mode) && " 不可用"}</span>)}</div></td>
              <td><span className={`availability ${item.readyForYouyun ? "available" : "unavailable"}`} title={item.readyForYouyun ? undefined : item.reason || undefined}><i />{readinessLabel(item)}</span>{item.reason && !item.readyForYouyun && <span className="image-generation-model-message">{item.reason}</span>}</td>
              <td>{item.group === "trained" ? <><div className="adapter-pills"><span className={`adapter-pill ${item.adapterVariant === "best" ? "best" : "final"}`}>{item.adapterVariant === "best" ? "Best Adapter" : "Final Adapter"}</span></div><span className="image-generation-model-subtext">任务：{item.taskId || "未知"}</span></> : <span className="image-generation-model-subtext">基座模型本体</span>}</td>
              <td><div className="model-row-actions">{item.archived ? <button type="button" className="link-btn" onClick={() => void updateArchive(item, false)}>恢复</button> : <button type="button" className="link-btn danger-link" onClick={() => void updateArchive(item, true)}>删除</button>}<button type="button" className="link-btn" onClick={() => setDetailId(item.id)}>详情</button></div></td>
            </tr>)}</tbody></table>
          )}
        </div>
      </div>

      {detail && <div className="model-drawer-layer" role="presentation" onMouseDown={() => setDetailId(null)}><aside className="model-drawer image-generation-model-detail-drawer" role="dialog" aria-modal="true" aria-labelledby="image-generation-model-detail-title" onMouseDown={(event) => event.stopPropagation()}>
        <div className="model-drawer-head"><div><span className="drawer-kicker">文生图模型详情</span><div className="drawer-title-line"><span className={`model-avatar ${detail.group}`} aria-hidden="true">{detail.group === "base" ? "基" : "A"}</span><h2 id="image-generation-model-detail-title">模型详情</h2></div><p>{detail.label}</p></div><button type="button" className="close-btn" title="关闭模型详情" aria-label="关闭模型详情" onClick={() => setDetailId(null)}><X size={18} /></button></div>
        <div className="drawer-status-row"><span className={`availability ${detail.readyForYouyun ? "available" : "unavailable"}`}><i />{readinessLabel(detail)}</span><span className={`source-tag ${detail.group === "base" ? "manual" : "cache"}`}>{groupLabel(detail.group)}</span>{detail.archived && <span className="status status-draft">已归档</span>}</div>
        <div className="drawer-info-grid"><div><span>ModelScope Repo ID</span><strong title={detail.repoId}>{detail.repoId || "未提供"}</strong></div><div><span>模型类型</span><strong>{detail.modelType || "未识别"}</strong></div><div><span>LoRA</span><strong>{detail.supportedTrainingModes.includes("lora") ? "兼容" : "不可用"}</strong></div><div><span>QLoRA</span><strong>{detail.supportedTrainingModes.includes("qlora") ? "兼容" : "不可用"}</strong></div></div>
        {detail.group === "trained" && <section className="drawer-section"><h3>Adapter 血缘</h3><div className="lineage-card"><div className="lineage-node"><span>基座模型</span><button type="button" onClick={() => setFilter("base")}>{detail.repoId}</button></div><b>+</b><div className="lineage-node"><span>训练产物</span><strong>{detail.adapterVariant === "best" ? "Best Adapter" : "Final Adapter"}</strong><small>任务：{detail.taskId || "未知"}</small></div></div></section>}
        <section className="drawer-section"><h3>优云可用性</h3><div className="image-generation-detail-note">{detail.readyForYouyun ? "已通过文生图服务端校验，可用于优云服务配置和推理任务。" : detail.reason || "需要完成模型文件或服务端兼容性校验后才能部署。"}</div>{!detail.readyForYouyun && !detail.archived && <button type="button" className="secondary-btn compact-btn drawer-inline-action" disabled={refreshingId === assetIdForItem(detail)} onClick={() => void refreshAsset(detail)}><RefreshCw size={14} className={refreshingId === assetIdForItem(detail) ? "spin" : ""} />{refreshingId === assetIdForItem(detail) ? "检查中" : "刷新模型校验"}</button>}</section>
        <section className="drawer-section"><h3>兼容模式</h3><div className="image-generation-mode-list image-generation-detail-modes">{(["lora", "qlora"] as const).map((mode) => <span className={`method-tag ${detail.supportedTrainingModes.includes(mode) ? "supported" : "unsupported"}`} key={mode}>{modeLabel(mode)}{detail.supportedTrainingModes.includes(mode) ? "兼容" : "不可用"}</span>)}</div></section>
        <section className="drawer-section drawer-footer-action"><div className="drawer-action-grid">{detail.archived ? <button type="button" className="secondary-btn icon-text-btn" onClick={() => void updateArchive(detail, false)}><RefreshCw size={15} />恢复模型</button> : <button type="button" className="danger-btn icon-text-btn" onClick={() => void updateArchive(detail, true)}><Trash2 size={15} />删除模型</button>}<button type="button" className="secondary-btn" onClick={() => setDetailId(null)}>关闭</button></div></section>
      </aside></div>}

      {showCatalog && <div className="model-drawer-layer" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setShowCatalog(false); }}><aside className="vision-model-catalog-drawer image-generation-model-catalog-drawer"><div className="model-drawer-head"><div><span className="drawer-kicker">ModelScope / 优云</span><h2>推荐基模</h2><p>仅展示文生图推理候选；“文生图可用”代表服务端已验证，其余需缓存后复检。</p></div><button type="button" className="icon-only-btn" title="关闭推荐基模目录" aria-label="关闭推荐基模目录" onClick={() => setShowCatalog(false)}><X size={19} /></button></div><div className="model-search-wrap vision-catalog-search"><Search size={16} /><input value={catalogQuery} onChange={(event) => setCatalogQuery(event.target.value)} placeholder="搜索模型名称或 Repo ID" aria-label="搜索推荐基模" /></div><div className="vision-catalog-list">{catalogModels.length === 0 ? <div className="model-empty">没有匹配的文生图推荐基模</div> : catalogModels.map((preset) => { const registered = registeredRepoIds.some((repoId) => repoId.trim().toLowerCase() === preset.repoId.trim().toLowerCase()); return <article key={preset.id} className="vision-catalog-row"><div className="vision-catalog-row-head"><div><strong>{preset.name}</strong><span>{preset.workload} · {preset.modelType} · {preset.capabilities.map((capability) => capability === "inference" ? "推理" : "LoRA").join(" / ")}</span></div><span className={preset.youyunAvailability === "ready" ? "catalog-ready" : "catalog-pending"}>{preset.youyunAvailability === "ready" ? "文生图可用" : "需缓存 / 探针"}</span></div><code>{preset.repoId}</code><p>{preset.useCase}</p><small>{preset.resourceHint}</small><button type="button" className={registered ? "secondary-btn compact-btn" : "primary-btn compact-btn"} onClick={() => openPreset(preset)}>{registered ? "查看资产" : "登记此基模"}</button></article>; })}</div></aside></div>}

      {showRegister && <div className="model-modal-layer" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setShowRegister(false); }}><form className="model-modal image-generation-model-register-modal" onSubmit={(event) => void submitRegister(event)}>
        <div className="model-modal-head"><div><span className="drawer-kicker">新增模型资产</span><h2>登记基模</h2><p>提交后写入统一模型资产目录；不会自动创建优云实例。</p></div><button type="button" className="icon-only-btn" title="关闭登记弹窗" aria-label="关闭登记弹窗" onClick={() => setShowRegister(false)}><X size={19} /></button></div>
        {selectedPreset && <div className="vision-register-selected"><span>已选推荐基模</span><strong>{selectedPreset.name}</strong><code>{selectedPreset.repoId}</code><button type="button" className="link-btn" onClick={() => { setShowRegister(false); setShowCatalog(true); }}>更换</button></div>}
        <div className="register-form"><label>显示名称 <b>*</b><input required value={registerForm.name} onChange={(event) => updateRegisterForm("name", event.target.value)} placeholder="例如：FLUX.1-dev" aria-describedby="image-generation-name-recommendation" onKeyDown={(event) => { if (event.key === "Tab" && !registerForm.name.trim()) fillSuggestedField("name", suggestedName); }} />{suggestedName && <span id="image-generation-name-recommendation" className="image-generation-path-recommendation"><span>推荐名称：<code>{suggestedName}</code></span><button type="button" className="link-btn" onClick={() => fillSuggestedField("name", suggestedName)} disabled={Boolean(registerForm.name.trim())} aria-label="填入推荐显示名称，按 Tab 自动填入">填入推荐名称</button></span>}</label><label>ModelScope Repo ID <b>*</b><input required value={registerForm.repo} onChange={(event) => updateRegisterForm("repo", event.target.value)} placeholder="例如：black-forest-labs/FLUX.1-dev" aria-describedby="image-generation-repo-recommendation" onKeyDown={(event) => { if (event.key === "Tab" && !registerForm.repo.trim()) fillSuggestedField("repo", suggestedRepo); }} />{suggestedRepo && <span id="image-generation-repo-recommendation" className="image-generation-path-recommendation"><span>推荐 Repo ID：<code>{suggestedRepo}</code></span><button type="button" className="link-btn" onClick={() => fillSuggestedField("repo", suggestedRepo)} disabled={Boolean(registerForm.repo.trim())} aria-label="填入推荐 ModelScope Repo ID，按 Tab 自动填入">填入推荐 Repo ID</button></span>}</label><div className="register-two-col"><label>Revision<input value={registerForm.revision} onChange={(event) => updateRegisterForm("revision", event.target.value)} placeholder="main" aria-describedby="image-generation-revision-recommendation" onKeyDown={(event) => { if (event.key === "Tab" && !registerForm.revision.trim()) fillSuggestedField("revision", suggestedRevision); }} />{suggestedRevision && <span id="image-generation-revision-recommendation" className="image-generation-path-recommendation"><span>推荐 Revision：<code>{suggestedRevision}</code></span><button type="button" className="link-btn" onClick={() => fillSuggestedField("revision", suggestedRevision)} disabled={Boolean(registerForm.revision.trim())} aria-label="填入推荐 Revision，按 Tab 自动填入">填入推荐 Revision</button></span>}</label><label>服务器本地目录 <b>*</b><input required value={registerForm.path} onChange={(event) => updateRegisterForm("path", event.target.value)} placeholder="例如：D:\\models\\FLUX.1-dev" aria-describedby="image-generation-path-recommendation" onKeyDown={(event) => { if (event.key === "Tab" && !registerForm.path.trim() && registerForm.repo.trim()) fillSuggestedPath(); }} /><span id="image-generation-path-recommendation" className="image-generation-path-recommendation"><span>推荐地址：<code>{suggestedPath}</code></span><button type="button" className="link-btn" onClick={fillSuggestedPath} disabled={!registerForm.repo.trim() || Boolean(registerForm.path.trim())} aria-label="填入推荐地址，按 Tab 自动填入">填入推荐地址</button></span></label></div></div>
        <div className="register-tip"><span>i</span><p>本地目录用于资产探针和后续训练配置。登记后，只有服务端验证通过的文生图基模才会显示为优云可用。</p></div>
        {registerError && <div className="model-error-banner image-generation-register-error"><strong>登记失败</strong><span>{registerError}</span></div>}
        <div className="model-modal-actions"><button type="button" className="secondary-btn" onClick={chooseCustomModel} disabled={registering}>自定义模型</button><div className="modal-actions-spacer" /><button type="button" className="secondary-btn" onClick={() => setShowRegister(false)} disabled={registering}>取消</button><button type="submit" className="primary-btn icon-text-btn" disabled={registering}><ScanSearch size={15} />{registering ? "登记中" : "登记并检查"}</button></div>
      </form></div>}
    </ModelAssetManagementShell>
  );
}

export default ImageGenerationModels;
