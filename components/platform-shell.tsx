"use client";

import { Boxes, ChevronDown, Database, LayoutDashboard, ListChecks, ServerCog } from "lucide-react";
import type { ReactNode } from "react";

export type PlatformModule = "overview" | "datasets" | "training" | "models" | "services";
export type PlatformDomain = "nlp" | "vision" | "image-generation";

const navigation = [
  { module: "overview" as const, label: "概览", icon: LayoutDashboard },
  { module: "datasets" as const, label: "数据集", icon: Database },
  { module: "training" as const, label: "微调任务", icon: ListChecks },
  { module: "models" as const, label: "模型管理", icon: Boxes },
  { module: "services" as const, label: "推理服务", icon: ServerCog },
];

const domains: Array<{ value: PlatformDomain; label: string; shortLabel: string }> = [
  { value: "nlp", label: "NLP", shortLabel: "N" },
  { value: "vision", label: "视觉理解", shortLabel: "视" },
  { value: "image-generation", label: "文生图", shortLabel: "图" },
];

export function PlatformShell({
  module,
  domain,
  onModuleChange,
  onDomainChange,
  children,
}: {
  module: PlatformModule;
  domain: PlatformDomain;
  onModuleChange: (module: PlatformModule) => void;
  onDomainChange: (domain: PlatformDomain) => void;
  children: ReactNode;
}) {
  return (
    <div className="app-shell workspace-reference-skin-shell">
      <aside className="sidebar">
        <div className="brand"><img src="/logo.png" alt="" width={38} height={38} style={{ objectFit: "contain", flexShrink: 0 }} /><span className="brand-name">训推平台</span></div>
        <div className="space-switcher"><span className="space-label">当前业务空间</span><span className="space-name">大模型研发中心⌄</span></div>
        <nav className="nav" aria-label="主导航">
          {navigation.map((item) => {
            const Icon = item.icon;
            const active = module === item.module;
            const hasChildren = item.module !== "overview";
            return (
              <div className={`nav-group ${active ? "active" : ""}`} key={item.module}>
                <button type="button" title={item.label} onClick={() => onModuleChange(item.module)} className={`nav-item ${active ? "active" : ""}`} aria-current={active ? "page" : undefined} aria-expanded={hasChildren ? active : undefined} aria-controls={hasChildren ? `subnav-${item.module}` : undefined}>
                  <Icon className="nav-icon" aria-hidden="true" />
                  <span className="nav-label">{item.label}</span>
                  {hasChildren && <ChevronDown className={`nav-chevron ${active ? "expanded" : ""}`} aria-hidden="true" />}
                </button>
                {active && hasChildren && (
                  <div className="subnav" id={`subnav-${item.module}`} aria-label={`${item.label}业务域`}>
                    {domains.map((itemDomain) => (
                      <button type="button" title={`${item.label} · ${itemDomain.label}`} key={itemDomain.value} className={`subnav-item ${domain === itemDomain.value ? "active" : ""}`} onClick={() => onDomainChange(itemDomain.value)} aria-current={domain === itemDomain.value ? "page" : undefined}>
                        <span className="subnav-mark">{itemDomain.shortLabel}</span>
                        <span className="subnav-label">{itemDomain.label}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </nav>
      </aside>
      <main className="main workspace-reference-skin workspace-reference-skin-shell">{children}</main>
    </div>
  );
}
