"use client";

import { ReactNode } from "react";
import { RefreshCw, Search } from "lucide-react";
import { EmptyState } from "./ui/empty-state";
import { PageHeader } from "./ui/page-header";

export type ModelAssetTab = "catalog" | "all" | "base" | "trained" | "archived";

export type ModelAssetSummary = {
  label: string;
  value: number | string;
  description: string;
  tone?: "indigo" | "green" | "blue" | "amber";
};

export type ModelAssetTabOption = {
  key: ModelAssetTab;
  label: string;
  count: number;
};

export function ModelAssetManagementShell({
  modelType,
  eyebrow,
  title = "模型管理",
  subtitle,
  headerActions,
  error,
  summaries,
  tabs,
  activeTab,
  onTabChange,
  query,
  onQueryChange,
  searchPlaceholder,
  onRefresh,
  loading = false,
  filters,
  children,
}: {
  modelType: "vision" | "image-generation";
  eyebrow: string;
  title?: string;
  subtitle: string;
  headerActions: ReactNode;
  error?: string;
  summaries: ModelAssetSummary[];
  tabs: ModelAssetTabOption[];
  activeTab: ModelAssetTab;
  onTabChange: (tab: ModelAssetTab) => void;
  query: string;
  onQueryChange: (value: string) => void;
  searchPlaceholder: string;
  onRefresh: () => void;
  loading?: boolean;
  filters?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className={`content model-page unified-model-page model-page-${modelType}`}>
      <PageHeader eyebrow={<span className="eyebrow"><span className="eyebrow-dot" />{eyebrow}</span>} title={title} description={subtitle} actions={<div className="model-head-actions">{headerActions}</div>} />

      {error && <EmptyState kind="error" title="模型服务暂不可用" description={error} action={<button type="button" className="link-btn" onClick={onRefresh}>重试</button>} />}

      <div className="model-summary">
        {summaries.map((summary) => <div className="model-stat" key={summary.label}>
          <span className={`model-stat-icon ${summary.tone ?? "indigo"}`}>{summary.label.slice(0, 1)}</span>
          <div><strong>{summary.value}</strong><span>{summary.label}</span></div>
          <small>{summary.description}</small>
        </div>)}
      </div>

      <div className="model-tabs" role="tablist" aria-label={`${title}模型类型`}>
        {tabs.map((tab) => <button type="button" key={tab.key} role="tab" aria-selected={activeTab === tab.key} className={activeTab === tab.key ? "active" : ""} onClick={() => onTabChange(tab.key)}>{tab.label}<span>{tab.count}</span></button>)}
      </div>

      <div className="model-toolbar">
        <div className="model-search-wrap"><Search size={16} /><input value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder={searchPlaceholder} aria-label={searchPlaceholder} /></div>
        {filters}
        <button type="button" className="secondary-btn icon-text-btn" disabled={loading} onClick={onRefresh}><RefreshCw size={15} className={loading ? "spin" : ""} />刷新</button>
      </div>

      {children}
    </section>
  );
}
