"use client";

import type { ReactNode } from "react";

export type InferenceServiceDetailModality = "nlp" | "vision" | "image-generation";

type InferenceServiceDetailProps = {
  service: unknown;
  modality: InferenceServiceDetailModality;
};

type ServiceRecord = Record<string, unknown>;

function asRecord(value: unknown): ServiceRecord {
  return value && typeof value === "object" ? value as ServiceRecord : {};
}

function read(record: ServiceRecord, ...keys: string[]): unknown {
  for (const key of keys) {
    const value = record[key];
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return undefined;
}

function readNested(record: ServiceRecord, path: string[]): unknown {
  let current: unknown = record;
  for (const key of path) {
    if (!current || typeof current !== "object") return undefined;
    current = (current as ServiceRecord)[key];
  }
  return current;
}

function text(value: unknown): string {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "暂无数据";
}

function modalityLabel(modality: InferenceServiceDetailModality): string {
  return modality === "nlp" ? "NLP" : modality === "vision" ? "视觉理解" : "文生图";
}

function Field({ label, value, code = false }: { label: string; value: ReactNode; code?: boolean }) {
  return <div><dt>{label}</dt><dd className={code ? "snapshot-value-code" : undefined}>{value}</dd></div>;
}

export function InferenceServiceDetailSummary({ service: rawService, modality }: InferenceServiceDetailProps) {
  const service = asRecord(rawService);
  const config = asRecord(read(service, "config"));
  const state = text(read(service, "state", "status"));
  const model = text(read(service, "baseModel", "modelRepoId", "model") ?? read(config, "baseModel", "modelRepoId", "modelId"));
  const adapter = text(read(service, "adapter", "adapterId") ?? read(config, "adapter", "adapterId") ?? readNested(config, ["adapterLineage", "variant"]));
  const provider = text(read(service, "provider", "executionMode") ?? read(config, "provider"));
  const failureMessage = text(read(service, "failureMessage", "error", "cleanupError"));

  return (
    <section className="panel inference-service-contract-summary" aria-label="统一推理服务契约摘要">
      <div className="snapshot-card-head">
        <div>
          <span className="summary-eyebrow">SERVICE CONTRACT</span>
          <h2>服务运行摘要</h2>
          <p>统一展示服务状态、模型血缘、资源清理和失败诊断，启动与删除操作仍由当前模块负责。</p>
        </div>
        <span className="snapshot-card-badge">{modalityLabel(modality)}</span>
      </div>
      <div className="snapshot-groups">
        <div className="snapshot-group">
          <h3>服务与模型</h3>
          <dl className="snapshot-grid">
            <Field label="服务" value={text(read(service, "name", "id"))} />
            <Field label="服务 ID" value={text(read(service, "id"))} code />
            <Field label="模态" value={modalityLabel(modality)} />
            <Field label="运行状态" value={state} />
            <Field label="上线状态" value={text(read(service, "exposure"))} />
            <Field label="基础模型" value={model} code />
            <Field label="Adapter" value={adapter} code />
            <Field label="提供方 / 执行模式" value={provider} />
            <Field label="端点" value={text(read(service, "endpoint"))} code />
            <Field label="更新时间" value={text(read(service, "updatedAt", "createdAt"))} />
          </dl>
        </div>
        <div className="snapshot-group">
          <h3>资源与清理</h3>
          <dl className="snapshot-grid">
            <Field label="资源状态" value={text(read(service, "resourceState", "resource_state") ?? read(config, "resourceState"))} />
            <Field label="清理状态" value={text(read(service, "cleanupState", "cleanup_state") ?? read(config, "cleanupState"))} />
            <Field label="实例 / 远端句柄" value={text(read(service, "instanceId") ?? readNested(service, ["remoteHandle", "instanceId"]))} code />
            <Field label="清理检查时间" value={text(read(service, "cleanupCheckedAt", "cleanup_checked_at"))} />
          </dl>
        </div>
        <div className="snapshot-group">
          <h3>失败诊断</h3>
          <dl className="snapshot-grid">
            <Field label="失败编码" value={text(read(service, "failureCode", "failure_code"))} code />
            <Field label="失败信息" value={failureMessage} />
            <Field label="建议动作" value={text(read(service, "suggestedAction", "suggested_action"))} />
          </dl>
        </div>
      </div>
    </section>
  );
}
