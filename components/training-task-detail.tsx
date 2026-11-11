"use client";

import type { ReactNode } from "react";

export type TrainingTaskDetailModality = "nlp" | "vision" | "image-generation";

type TrainingTaskDetailProps = {
  task: unknown;
  modality: TrainingTaskDetailModality;
};

type TaskRecord = Record<string, unknown>;

function asRecord(value: unknown): TaskRecord {
  return value && typeof value === "object" ? value as TaskRecord : {};
}

function read(task: TaskRecord, ...keys: string[]): unknown {
  for (const key of keys) {
    const value = task[key];
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return undefined;
}

function text(value: unknown, fallback = "暂无数据"): string {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return fallback;
}

function numberText(value: unknown, suffix = ""): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "暂无数据";
  return `${value}${suffix}`;
}

function modalityLabel(modality: TrainingTaskDetailModality): string {
  return modality === "nlp" ? "NLP" : modality === "vision" ? "视觉理解" : "文生图";
}

function Field({ label, value, code = false }: { label: string; value: ReactNode; code?: boolean }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd className={code ? "snapshot-value-code" : undefined}>{value}</dd>
    </div>
  );
}

export function TrainingTaskDetailSummary({ task: rawTask, modality }: TrainingTaskDetailProps) {
  const task = asRecord(rawTask);
  const state = text(read(task, "state", "status"));
  const phase = text(read(task, "phase", "currentStep"));
  const provider = text(read(task, "provider", "executionMode"));
  const failureMessage = text(read(task, "failureMessage", "error"));
  const taskName = text(read(task, "name", "id"));

  return (
    <section className="panel training-task-contract-summary" aria-label="统一任务契约摘要">
      <div className="snapshot-card-head">
        <div>
          <span className="summary-eyebrow">TASK CONTRACT</span>
          <h2>任务运行摘要</h2>
          <p>统一展示任务状态、资源生命周期和失败诊断，操作仍由当前模块负责。</p>
        </div>
        <span className="snapshot-card-badge">{modalityLabel(modality)}</span>
      </div>
      <div className="snapshot-groups">
        <div className="snapshot-group">
          <h3>任务与配置</h3>
          <dl className="snapshot-grid">
            <Field label="任务" value={taskName} />
            <Field label="任务 ID" value={text(read(task, "id"))} code />
            <Field label="模态" value={modalityLabel(modality)} />
            <Field label="状态 / 阶段" value={`${state} / ${phase}`} />
            <Field label="进度" value={numberText(read(task, "progress"), "%")} />
            <Field label="基础模型" value={text(read(task, "baseModel", "modelRepoId", "model"))} code />
            <Field label="数据集" value={text(read(task, "dataset", "datasetName", "datasetId"))} code />
            <Field label="训练方式" value={text(read(task, "tuningMode", "method", "taskType"))} />
            <Field label="执行提供方" value={provider} />
            <Field label="更新时间" value={text(read(task, "updatedAt", "createdAt"))} />
          </dl>
        </div>
        <div className="snapshot-group">
          <h3>资源与清理</h3>
          <dl className="snapshot-grid">
            <Field label="资源状态" value={text(read(task, "resourceState", "resource_state"))} />
            <Field label="清理状态" value={text(read(task, "cleanupState", "cleanup_state"))} />
            <Field label="实例 / 远端句柄" value={text(read(task, "instanceId"))} code />
            <Field label="最后心跳" value={text(read(task, "lastHeartbeatAt", "last_heartbeat_at"))} />
          </dl>
        </div>
        <div className="snapshot-group">
          <h3>失败诊断</h3>
          <dl className="snapshot-grid">
            <Field label="失败编码" value={text(read(task, "failureCode", "failure_code"))} code />
            <Field label="失败信息" value={failureMessage} />
            <Field label="建议动作" value={text(read(task, "suggestedAction", "suggested_action"))} />
          </dl>
        </div>
      </div>
    </section>
  );
}
