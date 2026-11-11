"use client";

import { FormEvent, useMemo, useState } from "react";
import {
  Check,
  CircleAlert,
  Clock3,
  LibraryBig,
  MoreHorizontal,
  Plus,
  RotateCcw,
  ScanSearch,
  Search,
  Trash2,
  X,
} from "lucide-react";
import {
  VISION_BASE_MODEL_PRESETS,
  type VisionBaseModelForm,
  type VisionBaseModelPreset,
} from "../lib/vision-base-model-presets";
import {
  visionModelBucket,
  visionModelMetadata,
  visionModelReadiness,
  type VisionManagedModel,
  type VisionReadinessItem,
} from "../lib/vision-model-management";
import { normalizeModelAsset, type ModelAssetView } from "../lib/model-asset-contracts";
import { ModelAssetManagementShell, type ModelAssetTab } from "./model-asset-management-shell";

type VisionModelFilter = "all" | "available" | "pending" | "error";
type EnvironmentFilter = "all" | "local" | "youyun";

function updatedAt(value?: string) {
  if (!value) return "未检测";
  return value.replace("T", " ").replace(/\.\d+(Z|[+-]\d\d:\d\d)?$/, "");
}

function capabilityLabel(value: string) {
  return ({
    vision_language: "视觉语言",
    image_understanding: "图像理解",
    image_qa: "视觉问答",
    ocr: "OCR",
    lora_training: "LoRA",
    qlora_training: "QLoRA",
    transformers_serving: "推理服务",
  } as Record<string, string>)[value] ?? value;
}

function readinessLabel(item: VisionReadinessItem) {
  if (item.state === "ready") return "可用";
  if (item.state === "pending") return "待处理";
  return "不可用";
}

function ReadinessCell({ item }: { item: VisionReadinessItem }) {
  const Icon = item.state === "ready" ? Check : item.state === "pending" ? Clock3 : CircleAlert;
  return (
    <span className={"vision-readiness-cell " + item.state} title={item.reason}>
      <Icon size={13} aria-hidden="true" />
      <span>{readinessLabel(item)}</span>
    </span>
  );
}

function AssetActions({
  model,
  probingId,
  onProbe,
  onArchive,
  onOpen,
}: {
  model: VisionManagedModel;
  probingId: string;
  onProbe: (modelId: string) => Promise<void>;
  onArchive: (modelId: string, archived: boolean) => Promise<void>;
  onOpen: (modelId: string) => void;
}) {
  return (
    <div className="model-row-actions">
      {!model.youyunCatalog && <button
        type="button"
        className="link-btn"
        disabled={probingId === model.id}
        onClick={() => void onProbe(model.id)}
      >
        {probingId === model.id ? "检查中" : "重新探针"}
      </button>}
      {!model.youyunCatalog && !model.archived && <button type="button" className="link-btn danger" onClick={() => { if (window.confirm("移入回收站后，该模型不会出现在训练和推理选择器中。确认继续？")) void onArchive(model.id, true); }}>
        归档
      </button>}
      {!model.youyunCatalog && model.archived && <button type="button" className="link-btn" onClick={() => void onArchive(model.id, false)}>
        恢复
      </button>}
      <button type="button" className="link-btn" onClick={() => onOpen(model.id)}>
        详情
      </button>
    </div>
  );
}

export function VisionModelManagementView({
  models,
  catalogModels,
  loading,
  notice,
  error,
  modelForm,
  onModelFormChange,
  onModelPresetSelect,
  onRegister,
  onProbe,
  onArchive,
  onRefresh,
}: {
  models: VisionManagedModel[];
  catalogModels: VisionManagedModel[];
  loading: boolean;
  notice: string;
  error: string;
  modelForm: VisionBaseModelForm;
  onModelFormChange: (field: keyof VisionBaseModelForm, value: string) => void;
  onModelPresetSelect: (preset: VisionBaseModelPreset) => void;
  onRegister: () => Promise<boolean>;
  onProbe: (modelId: string) => Promise<void>;
  onArchive: (modelId: string, archived: boolean) => Promise<void>;
  onRefresh: () => Promise<void>;
}) {
  const [bucket, setBucket] = useState<VisionModelFilter>("all");
  const [activeTab, setActiveTab] = useState<ModelAssetTab>("catalog");
  const [environment, setEnvironment] = useState<EnvironmentFilter>("all");
  const [family, setFamily] = useState("all");
  const [scale, setScale] = useState("all");
  const [query, setQuery] = useState("");
  const [detailId, setDetailId] = useState<string | null>(null);
  const [showCatalog, setShowCatalog] = useState(false);
  const [showRegister, setShowRegister] = useState(false);
  const [catalogQuery, setCatalogQuery] = useState("");
  const [libraryQuery, setLibraryQuery] = useState("");
  const [catalogDetailId, setCatalogDetailId] = useState<string | null>(null);
  const [probingId, setProbingId] = useState("");
  const assetViews = useMemo<ModelAssetView[]>(() => models.map((model) => normalizeModelAsset(model, "vision")), [models]);

  const activeModels = useMemo(() => models.filter((model) => !model.archived), [models]);
  const archivedModels = useMemo(() => models.filter((model) => model.archived), [models]);
  const metadata = useMemo(() => new Map(models.map((model) => [model.id, visionModelMetadata(model)])), [models]);
  const families = useMemo(() => Array.from(new Set(activeModels.map((model) => metadata.get(model.id)?.family))).filter(Boolean) as string[], [activeModels, metadata]);
  const scales = useMemo(() => Array.from(new Set(activeModels.map((model) => metadata.get(model.id)?.parameterScale))).filter(Boolean) as string[], [activeModels, metadata]);

  const visibleModels = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    const sourceModels = activeTab === "archived" ? archivedModels : activeModels;
    return sourceModels.filter((model) => {
      const readiness = visionModelReadiness(model);
      const modelBucket = visionModelBucket(model);
      const modelMetadata = metadata.get(model.id);
      const matchesTab = activeTab === "all" || activeTab === "base" || activeTab === "archived";
      const matchesBucket = bucket === "all" || modelBucket === bucket;
      const matchesEnvironment = environment === "all" || readiness[environment === "local" ? "localTraining" : "youyunTraining"].state === "ready";
      const matchesFamily = family === "all" || modelMetadata?.family === family;
      const matchesScale = scale === "all" || modelMetadata?.parameterScale === scale;
      const matchesQuery = !normalizedQuery || [model.name, model.repoId, model.revision, model.path, model.probeMessage, ...model.capabilities].join(" ").toLowerCase().includes(normalizedQuery);
      return matchesTab && matchesBucket && matchesEnvironment && matchesFamily && matchesScale && matchesQuery;
    });
  }, [activeModels, activeTab, archivedModels, bucket, environment, family, metadata, query, scale]);

  const detail = useMemo(() => models.find((model) => model.id === detailId) ?? null, [detailId, models]);
  const detailReadiness = detail ? visionModelReadiness(detail) : null;
  const registeredByRepoId = useMemo(() => {
    const map = new Map<string, VisionManagedModel>();
    for (const model of models) {
      const key = model.repoId.trim().toLowerCase();
      if (!map.has(key)) map.set(key, model);
    }
    return map;
  }, [models]);
  const libraryModels = useMemo(() => {
    const normalizedQuery = libraryQuery.trim().toLowerCase();
    if (!normalizedQuery) return catalogModels;
    return catalogModels.filter((model) => [model.name, model.repoId, model.remotePath ?? ""].join(" ").toLowerCase().includes(normalizedQuery));
  }, [catalogModels, libraryQuery]);
  const catalogDetail = useMemo(() => catalogModels.find((model) => model.id === catalogDetailId) ?? null, [catalogDetailId, catalogModels]);
  const selectedPreset = VISION_BASE_MODEL_PRESETS.find((preset) => preset.repoId.trim().toLowerCase() === modelForm.repoId.trim().toLowerCase()) ?? null;
  const registeredRepoIds = models.map((model) => model.repoId);
  const presetCatalog = VISION_BASE_MODEL_PRESETS.filter((preset) => {
    const normalizedQuery = catalogQuery.trim().toLowerCase();
    return !normalizedQuery || [preset.name, preset.repoId, preset.family, preset.parameterScale, preset.useCase].join(" ").toLowerCase().includes(normalizedQuery);
  });

  function resetFilters() {
    setActiveTab("all");
    setBucket("all");
    setEnvironment("all");
    setFamily("all");
    setScale("all");
    setQuery("");
  }

  function chooseCustomModel() {
    onModelFormChange("name", "");
    onModelFormChange("repoId", "");
    onModelFormChange("revision", "");
    onModelFormChange("path", "");
  }

  async function submitRegister(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (await onRegister()) setShowRegister(false);
  }

  async function handleProbe(modelId: string) {
    setProbingId(modelId);
    try {
      await onProbe(modelId);
    } finally {
      setProbingId("");
    }
  }

  function openPreset(preset: VisionBaseModelPreset) {
    const registered = models.find((model) => model.repoId.trim().toLowerCase() === preset.repoId.trim().toLowerCase());
    if (registered) {
      setShowCatalog(false);
      setDetailId(registered.id);
      return;
    }
    onModelPresetSelect(preset);
    setShowCatalog(false);
    setShowRegister(true);
  }

  function openRegisterForModel(model: VisionManagedModel) {
    onModelFormChange("name", model.name);
    onModelFormChange("repoId", model.repoId);
    onModelFormChange("revision", model.revision || "main");
    onModelFormChange("path", model.path);
    setDetailId(null);
    setShowRegister(true);
  }

  function useCatalogModelForTraining(model: VisionManagedModel) {
    const registered = registeredByRepoId.get(model.repoId.trim().toLowerCase());
    if (registered) {
      setCatalogDetailId(null);
      setDetailId(registered.id);
      return;
    }
    onModelFormChange("name", model.name);
    onModelFormChange("repoId", model.repoId);
    onModelFormChange("revision", model.revision || "main");
    onModelFormChange("path", "");
    setCatalogDetailId(null);
    setShowRegister(true);
  }

  const catalogActive = activeTab === "catalog";
  const summaries = catalogActive
    ? [
        { label: "公共模型", value: catalogModels.length, description: "当前查询总数", tone: "indigo" as const },
        { label: "可用", value: catalogModels.filter((model) => model.availableOnYouyun).length, description: "公共基模", tone: "green" as const },
        { label: "不可用", value: catalogModels.filter((model) => !model.availableOnYouyun).length, description: "公共基模", tone: "blue" as const },
        { label: "大小未知", value: catalogModels.filter((model) => model.sizeGb == null).length, description: "优云未提供大小", tone: "amber" as const },
      ]
    : [
        { label: "模型资产", value: assetViews.length, description: "已登记视觉基座模型", tone: "indigo" as const },
        { label: "本地可用", value: assetViews.filter((asset) => asset.readiness.localTraining.state === "ready").length, description: "通过本地探针和目录检查", tone: "green" as const },
        { label: "优云可用", value: assetViews.filter((asset) => asset.readiness.remoteTraining.state === "ready").length, description: "可用于远程训练或推理", tone: "blue" as const },
        { label: "待处理", value: assetViews.filter((asset) => asset.status !== "available").length, description: "需要补齐模型或探针信息", tone: "amber" as const },
      ];
  return (
    <ModelAssetManagementShell
      modelType="vision"
      eyebrow="视觉理解 / 模型资产"
      subtitle={catalogActive ? "浏览优云公共模型库的共享挂载基模，登记后用于训练与推理" : "登记视觉基座模型，统一查看本地训练、优云训练与推理部署出口"}
      headerActions={<><button type="button" className="secondary-btn icon-text-btn" onClick={() => setShowCatalog(true)}><LibraryBig size={16} />浏览基模目录</button><button type="button" className="primary-btn icon-text-btn" onClick={() => setShowRegister(true)}><Plus size={16} />登记基模</button></>}
      error={error}
      summaries={summaries}
      tabs={[{ key: "catalog", label: "优云公共模型库", count: catalogModels.length }, { key: "all", label: "全部模型", count: activeModels.length }, { key: "base", label: "基模", count: activeModels.length }, { key: "trained", label: "训练模型", count: 0 }, { key: "archived", label: "回收站", count: archivedModels.length }]}
      activeTab={activeTab}
      onTabChange={(next) => { setActiveTab(next); setBucket("all"); }}
      query={catalogActive ? libraryQuery : query}
      onQueryChange={catalogActive ? setLibraryQuery : setQuery}
      searchPlaceholder={catalogActive ? "搜索模型名称或公共模型 ID" : "搜索名称、ModelScope ID 或能力"}
      onRefresh={() => void onRefresh()}
      loading={loading}
      filters={catalogActive ? undefined : <><select value={environment} onChange={(event) => setEnvironment(event.target.value as EnvironmentFilter)} aria-label="按环境筛选"><option value="all">全部环境</option><option value="local">本地出口可用</option><option value="youyun">优云出口可用</option></select><select value={family} onChange={(event) => setFamily(event.target.value)} aria-label="按模型家族筛选"><option value="all">全部家族</option>{families.map((value) => <option key={value} value={value}>{value}</option>)}</select><select value={scale} onChange={(event) => setScale(event.target.value)} aria-label="按参数规模筛选"><option value="all">全部规模</option>{scales.map((value) => <option key={value} value={value}>{value}</option>)}</select>{(query || bucket !== "all" || environment !== "all" || family !== "all" || scale !== "all") && <button type="button" className="link-btn" onClick={resetFilters}>清除筛选</button>}</>}
    >
      {catalogActive ? (
        <div className="model-section">
          <div className="model-section-head"><div><h2>优云公共模型库</h2><p>优云实例内共享挂载的视觉基模；登记后进入模型资产，训练与推理使用容器内挂载路径</p></div><span>{libraryModels.length} / {catalogModels.length} 个</span></div>
          <div className="model-table-wrap">
            <table className="model-table vision-catalog-table"><thead><tr><th>模型</th><th>来源</th><th>容器内路径</th><th>大小</th><th>状态</th><th>操作</th></tr></thead><tbody>
              {libraryModels.map((model) => {
                const registered = registeredByRepoId.get(model.repoId.trim().toLowerCase());
                return <tr key={model.id}>
                  <td><button type="button" className="model-name-btn" onClick={() => setCatalogDetailId(model.id)}><span className="model-avatar base">云</span><span><strong>{model.name}</strong><small>{model.repoId}</small></span></button></td>
                  <td><span className="source-tag cache">优云公共模型库</span></td>
                  <td><code className="path-text">{model.remotePath || "--"}</code></td>
                  <td><span className="vision-catalog-size">{model.sizeGb != null ? `${model.sizeGb} GB` : "未知"}</span></td>
                  <td><span className={"availability " + (model.availableOnYouyun ? "available" : "unavailable")}><i />{model.availableOnYouyun ? "可用" : "不可用"}</span><small className="vision-model-subtext">{[model.template ? `模板 ${model.template}` : "", model.recommendedVramGb ? `建议 ${model.recommendedVramGb}GB+ 显存` : ""].filter(Boolean).join(" · ")}</small></td>
                  <td><div className="model-row-actions"><button type="button" className="link-btn" onClick={() => useCatalogModelForTraining(model)}>{registered ? "已登记 · 查看" : "用于训练"}</button></div></td>
                </tr>;
              })}
            </tbody></table>
            {!libraryModels.length && <div className="model-empty">{catalogModels.length ? <><span>没有匹配的公共基模</span><button type="button" className="link-btn" onClick={() => setLibraryQuery("")}>清除搜索</button></> : <><span>优云公共模型库为空</span><small>刷新后将重新读取优云共享模型目录。</small></>}</div>}
          </div>
        </div>
      ) : (
      <div className="model-section">
        <div className="model-section-head"><div><h2>{activeTab === "archived" ? "已归档模型" : activeTab === "trained" ? "视觉训练模型" : "已登记模型资产"}</h2><p>{activeTab === "archived" ? "归档模型不会出现在训练和推理选择器中，可随时恢复。" : "每个模型的本地训练、优云训练和推理部署出口单独判定"}</p></div><span>{visibleModels.length} / {activeTab === "archived" ? archivedModels.length : activeModels.length} 个</span></div>
        <div className="model-table-wrap vision-model-table-wrap">
          <table className="model-table vision-model-table"><thead><tr><th>模型</th><th>来源</th><th>训练与部署出口</th><th>最近检查</th><th>操作</th></tr></thead><tbody>
            {visibleModels.map((model) => {
              const readiness = visionModelReadiness(model);
              const modelMetadata = metadata.get(model.id);
              return <tr key={model.id}>
                <td><button type="button" className="model-name-btn" onClick={() => setDetailId(model.id)}><span className="model-avatar base">视</span><span><strong>{model.name}</strong><small>{model.repoId} · {model.revision || "main"}</small></span></button></td>
                <td><span className={"source-tag " + (model.path.trim() ? "manual" : model.youyunCatalog ? "cache" : "cache")}>{model.path.trim() ? "本地目录" : model.youyunCatalog ? "优云模型库" : "仓库候选"}</span><small className="vision-model-subtext">{modelMetadata?.family} · {modelMetadata?.parameterScale}{model.recommendedVramGb ? ` · 建议 ${model.recommendedVramGb}GB+` : ""}</small>{model.imageName && <small className="vision-model-subtext">镜像：{model.imageName}{model.imageStatus ? ` · ${model.imageStatus}` : ""}</small>}</td>
                <td><div className="vision-readiness-matrix"><div><span>本地训练</span><ReadinessCell item={readiness.localTraining} /></div><div><span>优云训练</span><ReadinessCell item={readiness.youyunTraining} /></div><div><span>推理部署</span><ReadinessCell item={readiness.inference} /></div></div></td>
                <td><span className="vision-model-checked">{updatedAt(model.updatedAt)}</span><small className="vision-model-message">{model.probeMessage || "暂无探针说明"}</small></td>
                <td><AssetActions model={model} probingId={probingId} onProbe={handleProbe} onArchive={onArchive} onOpen={setDetailId} /></td>
              </tr>;
            })}
          </tbody></table>
          <div className="vision-model-mobile-list">
            {visibleModels.map((model) => { const readiness = visionModelReadiness(model); return <article key={model.id} className="vision-model-mobile-item"><div className="vision-mobile-item-head"><button type="button" className="model-name-btn" onClick={() => setDetailId(model.id)}><span className="model-avatar base">视</span><span><strong>{model.name}</strong><small>{model.repoId}</small></span></button><button type="button" className="icon-only-btn" title="更多操作" aria-label="更多操作" onClick={() => setDetailId(model.id)}><MoreHorizontal size={17} /></button></div><div className="vision-mobile-exits"><ReadinessCell item={readiness.localTraining} /><ReadinessCell item={readiness.youyunTraining} /><ReadinessCell item={readiness.inference} /></div><div className="vision-mobile-item-meta"><span>{visionModelMetadata(model).family} · {visionModelMetadata(model).parameterScale}</span><span>{updatedAt(model.updatedAt)}</span></div><AssetActions model={model} probingId={probingId} onProbe={handleProbe} onArchive={onArchive} onOpen={setDetailId} /></article>; })}
          </div>
          {!visibleModels.length && <div className="model-empty">{(activeTab === "archived" ? archivedModels.length : activeModels.length) ? <><span>没有匹配的已登记模型</span><button type="button" className="link-btn" onClick={resetFilters}>清除筛选</button></> : activeTab === "trained" ? <><span>尚无视觉训练模型</span><small>视觉训练产物会在微调任务完成后进入训练模型页签。</small></> : activeTab === "archived" ? <><span>回收站为空</span><small>已归档的视觉模型会显示在这里。</small></> : <><span>尚未登记视觉基模</span><button type="button" className="link-btn" onClick={() => setShowCatalog(true)}>浏览基模目录</button></>}</div>}
        </div>
      </div>
      )}

      {notice && <div className="model-toast"><Check size={15} />{notice}</div>}

      {detail && detailReadiness && <div className="model-drawer-layer" onMouseDown={(event) => { if (event.target === event.currentTarget) setDetailId(null); }}><aside className="model-drawer vision-model-detail-drawer"><div className="model-drawer-head"><div><span className="drawer-kicker">{detail.archived ? "已归档视觉基模" : "已登记视觉基模"}</span><div className="drawer-title-line"><h2>{detail.name}</h2></div><p>{detail.repoId} · {detail.revision || "main"}</p></div><button type="button" className="icon-only-btn" title="关闭详情" aria-label="关闭详情" onClick={() => setDetailId(null)}><X size={19} /></button></div><div className="drawer-status-row"><span className={"source-tag " + (detail.path.trim() ? "manual" : "cache")}>{detail.path.trim() ? "本地目录" : detail.youyunCatalog ? "优云模型库" : "仓库候选"}</span><span className="method-tag">{visionModelMetadata(detail).family} · {visionModelMetadata(detail).parameterScale}</span>{detail.recommendedVramGb && <span className="method-tag">建议 {detail.recommendedVramGb}GB+ 显存</span>}{detail.archived && <span className="status status-draft">已归档</span>}</div><div className="vision-detail-exits"><div><span>本地训练</span><ReadinessCell item={detailReadiness.localTraining} /><p>{detailReadiness.localTraining.reason}</p></div><div><span>优云训练</span><ReadinessCell item={detailReadiness.youyunTraining} /><p>{detailReadiness.youyunTraining.reason}</p></div><div><span>推理部署</span><ReadinessCell item={detailReadiness.inference} /><p>{detailReadiness.inference.reason}</p></div></div>{!detail.archived && detailReadiness.localTraining.state !== "ready" && <div className="model-pending-actions"><strong>待处理</strong><p>{detailReadiness.localTraining.reason}</p><div className="drawer-action-grid">{!detail.path.trim() && <button type="button" className="secondary-btn icon-text-btn" onClick={() => openRegisterForModel(detail)}>补充目录</button>}<button type="button" className="secondary-btn icon-text-btn" disabled={probingId === detail.id} onClick={() => void handleProbe(detail.id)}><ScanSearch size={15} />重新探针</button><button type="button" className="link-btn" onClick={() => setShowCatalog(true)}>打开推荐目录</button></div></div>}<div className="drawer-section"><h3>模型路径</h3><div className="path-box"><code>{detail.path.trim() || "未配置本地目录"}</code>{detail.path.trim() && <button type="button" className="copy-path-btn" onClick={() => { void navigator.clipboard?.writeText(detail.path); }}>复制</button>}</div>{detail.remotePath && <div className="path-box"><span className="drawer-help">优云挂载路径</span><code>{detail.remotePath}</code><button type="button" className="copy-path-btn" onClick={() => { void navigator.clipboard?.writeText(detail.remotePath!); }}>复制</button></div>}<p className="drawer-help">本地训练和推理部署依赖服务器可访问的目录；优云模型库条目使用远端挂载路径并在实例内执行完整性探针。</p></div><div className="drawer-section"><h3>视觉能力</h3><div className="vision-model-detail-capabilities">{detail.capabilities.length ? detail.capabilities.map((capability) => <span key={capability}>{capabilityLabel(capability)}</span>) : <small>尚未识别</small>}</div></div><div className="drawer-section"><h3>探针诊断</h3><div className="vision-model-probe-detail"><strong>{detail.probeStatus || "pending"}</strong><p>{detail.probeMessage || "暂无探针说明"}</p>{detail.template && <small>训练模板：{detail.template}</small>}</div></div><div className="drawer-section"><div className="drawer-action-grid">{detail.archived ? <button type="button" className="secondary-btn icon-text-btn" onClick={() => void onArchive(detail.id, false)}><RotateCcw size={15} />恢复模型</button> : <button type="button" className="danger-btn icon-text-btn" onClick={() => { if (window.confirm("移入回收站后，该模型不会出现在训练和推理选择器中。确认继续？")) void onArchive(detail.id, true); }}><Trash2 size={15} />移入回收站</button>}<button type="button" className="secondary-btn" onClick={() => setDetailId(null)}>关闭</button></div></div></aside></div>}

      {catalogDetail && <div className="model-drawer-layer" onMouseDown={(event) => { if (event.target === event.currentTarget) setCatalogDetailId(null); }}><aside className="model-drawer vision-model-detail-drawer"><div className="model-drawer-head"><div><span className="drawer-kicker">优云公共模型库</span><div className="drawer-title-line"><h2>{catalogDetail.name}</h2></div><p>{catalogDetail.repoId} · {catalogDetail.revision || "main"}</p></div><button type="button" className="icon-only-btn" title="关闭详情" aria-label="关闭详情" onClick={() => setCatalogDetailId(null)}><X size={19} /></button></div><div className="drawer-status-row"><span className="source-tag cache">优云公共模型库</span><span className="method-tag">模板 {catalogDetail.template || "--"}</span>{catalogDetail.recommendedVramGb && <span className="method-tag">建议 {catalogDetail.recommendedVramGb}GB+ 显存</span>}<span className={"availability " + (catalogDetail.availableOnYouyun ? "available" : "unavailable")}><i />{catalogDetail.availableOnYouyun ? "可用" : "不可用"}</span></div><div className="drawer-section"><h3>容器内路径</h3><div className="path-box"><code>{catalogDetail.remotePath || "未提供挂载路径"}</code>{catalogDetail.remotePath && <button type="button" className="copy-path-btn" onClick={() => { void navigator.clipboard?.writeText(catalogDetail.remotePath!); }}>复制</button>}</div><p className="drawer-help">该路径挂载在优云 GPU 实例内，训练与推理直接读取，无需下载模型；大小{catalogDetail.sizeGb != null ? `约 ${catalogDetail.sizeGb}GB` : "优云未提供"}。</p></div><div className="drawer-section"><h3>视觉能力</h3><div className="vision-model-detail-capabilities">{catalogDetail.capabilities.length ? catalogDetail.capabilities.map((capability) => <span key={capability}>{capabilityLabel(capability)}</span>) : <small>尚未识别</small>}</div></div><div className="drawer-section"><h3>探针说明</h3><div className="vision-model-probe-detail"><strong>{catalogDetail.probeStatus || "candidate"}</strong><p>{catalogDetail.probeMessage}</p></div></div><div className="drawer-section"><div className="drawer-action-grid"><button type="button" className="primary-btn icon-text-btn" onClick={() => useCatalogModelForTraining(catalogDetail)}>{registeredByRepoId.has(catalogDetail.repoId.trim().toLowerCase()) ? "已登记 · 查看资产" : "用于训练"}</button><button type="button" className="secondary-btn" onClick={() => setCatalogDetailId(null)}>关闭</button></div></div></aside></div>}

      {showCatalog && <div className="model-drawer-layer" onMouseDown={(event) => { if (event.target === event.currentTarget) setShowCatalog(false); }}><aside className="vision-model-catalog-drawer"><div className="model-drawer-head"><div><span className="drawer-kicker">ModelScope / 优云</span><h2>基模目录</h2><p>常用视觉基模模板，仅登记后进入资产列表</p></div><button type="button" className="icon-only-btn" title="关闭基模目录" aria-label="关闭基模目录" onClick={() => setShowCatalog(false)}><X size={19} /></button></div><div className="model-search-wrap vision-catalog-search"><Search size={16} /><input value={catalogQuery} onChange={(event) => setCatalogQuery(event.target.value)} placeholder="搜索模型家族、规模或用途" aria-label="搜索基模目录" /></div><div className="vision-catalog-list">{presetCatalog.map((preset) => { const registered = registeredRepoIds.some((repoId) => repoId.trim().toLowerCase() === preset.repoId.trim().toLowerCase()); return <article key={preset.id} className="vision-catalog-row"><div className="vision-catalog-row-head"><div><strong>{preset.name}</strong><span>{preset.family} · {preset.parameterScale}</span></div><span className={preset.youyunAvailability === "ready" ? "catalog-ready" : "catalog-pending"}>{preset.youyunAvailability === "ready" ? "优云已就绪" : "需缓存"}</span></div><code>{preset.repoId}</code><p>{preset.useCase}</p><small>{preset.resourceHint}</small><button type="button" className={registered ? "secondary-btn compact-btn" : "primary-btn compact-btn"} onClick={() => openPreset(preset)}>{registered ? "查看资产" : "登记此基模"}</button></article>; })}</div></aside></div>}

      {showRegister && <div className="model-modal-layer" onMouseDown={(event) => { if (event.target === event.currentTarget) setShowRegister(false); }}><form className="model-modal vision-register-modal" onSubmit={(event) => void submitRegister(event)}><div className="model-modal-head"><div><span className="drawer-kicker">新增模型资产</span><h2>登记视觉基模</h2><p>确认仓库身份和服务器目录后，提交即执行视觉配置探针。</p></div><button type="button" className="icon-only-btn" title="关闭登记弹窗" aria-label="关闭登记弹窗" onClick={() => setShowRegister(false)}><X size={19} /></button></div><div className="vision-register-selected"><span>已选基模</span><strong>{selectedPreset?.name || "自定义视觉模型"}</strong><code>{modelForm.repoId || "请填写仓库 ID"}</code><button type="button" className="link-btn" onClick={() => { setShowRegister(false); setShowCatalog(true); }}>更换</button></div><div className="register-form"><label>显示名称 <b>*</b><input required value={modelForm.name} onChange={(event) => onModelFormChange("name", event.target.value)} placeholder="例如：Qwen2.5-VL 3B" /></label><label>视觉模型仓库 ID <b>*</b><input required value={modelForm.repoId} onChange={(event) => onModelFormChange("repoId", event.target.value)} placeholder="例如：Qwen/Qwen2.5-VL-3B-Instruct" /></label><div className="register-two-col"><label>Revision<input value={modelForm.revision} onChange={(event) => onModelFormChange("revision", event.target.value)} placeholder="main" /></label><label>服务器本地目录<input value={modelForm.path} onChange={(event) => onModelFormChange("path", event.target.value)} placeholder="例如：/data/models/Qwen2.5-VL-3B-Instruct" /></label></div></div><div className="register-tip"><span>i</span><p>没有本地目录时会登记为仓库候选；本地训练和推理部署需要探针通过，优云训练需要后端确认 Repo ID 已在可用目录中。</p></div><div className="model-modal-actions"><button type="button" className="secondary-btn" onClick={chooseCustomModel}>自定义模型</button><div className="modal-actions-spacer" /><button type="button" className="secondary-btn" onClick={() => setShowRegister(false)}>取消</button><button type="submit" className="primary-btn icon-text-btn" disabled={!modelForm.name.trim() || !modelForm.repoId.trim()}><ScanSearch size={15} />登记并检查</button></div></form></div>}
    </ModelAssetManagementShell>
  );
}
