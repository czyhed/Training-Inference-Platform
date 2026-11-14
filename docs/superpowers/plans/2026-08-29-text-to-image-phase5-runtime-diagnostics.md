# 文生图阶段五运行验收与故障诊断 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不启动本机 GPU 或真实优云实例的前提下，为文生图任务增加可追踪的阶段遥测、故障分类、恢复诊断和 Fake 端到端验收能力。

**Architecture:** Runtime 负责持久化统一的阶段时间戳、心跳和故障字段；Diffusers Provider 通过受控的阶段回调返回稳定的失败分类，Fake Provider 测试覆盖成功、远端提前退出、结果损坏、清理失败和重启恢复。API 暴露只读诊断摘要，前端只展示状态、原因和建议动作，不暴露远端命令或敏感信息。

**Tech Stack:** FastAPI, asyncio, JSON task persistence, Python unittest, Next.js 14, React 18, Node test runner, TypeScript.

**Spec:** 已批准的阶段五方案（2026-08-29 对话设计）：运行验收与故障诊断，仅 Fake 验证。

## Global Constraints

- 不启动本机 Diffusers、ComfyUI 或 GPU 服务。
- 不创建真实优云实例，不建立真实 SSH，不读取或输出真实密钥、密码或 SSH 私钥。
- 诊断信息只返回脱敏后的阶段、错误分类、最后心跳和下一步建议，不返回远端命令、凭据或完整异常堆栈。
- 保持现有任务状态机、预检快照闸门、清理失败阻止重跑和单 GPU 执行锁语义不变。

### Task 1: Runtime 遥测与故障分类

**Files:**
- Modify: `backend/image_generation_runtime.py`
- Extend: `backend/test_image_generation_runtime.py`

**Interfaces:**
- `ImageGenerationRuntime.update_telemetry(task_id, *, phase, message, progress=None, heartbeat=True, failure_code=None, failure_message=None, suggested_action=None)` persists `phase`, `phaseUpdatedAt`, `lastHeartbeatAt`, `failureCode`, `failureMessage`, and `suggestedAction`.
- Runtime task records expose `phase`, `phaseUpdatedAt`, `lastHeartbeatAt`, `failureCode`, `failureMessage`, and `suggestedAction` when applicable.

- [x] **Step 1: Write failing tests** for phase timestamps on stage callbacks, heartbeat updates, classified provider failure, cleanup failure preserving the primary failure, and restart recovery classification.
- [x] **Step 2: Run focused runtime tests and verify the new assertions fail** because telemetry fields and failure classification do not exist.
- [x] **Step 3: Implement the minimal telemetry helper and wire it into stage/log/provider outcome paths**; keep existing state and progress fields compatible.
- [x] **Step 4: Run `python -m unittest backend.test_image_generation_runtime -v` and verify all runtime tests pass.**
- [x] **Step 5: Commit** with `feat: add image generation runtime telemetry`.

### Task 2: Diffusers Provider Failure Taxonomy and Fake Scenarios

**Files:**
- Modify: `backend/providers/youyun/diffusers.py`
- Extend: `backend/test_youyun_diffusers.py`

**Interfaces:**
- Provider outcomes include `failureCode` values from `instance_start_failed`, `runtime_unavailable`, `model_unavailable`, `adapter_unavailable`, `generation_failed`, `result_download_failed`, and `cleanup_failed` where relevant.
- Provider stage callbacks include stable stage names and do not include shell commands or credentials.

- [x] **Step 1: Write failing Fake Provider/SSH tests** for early process exit, invalid result download, model/runtime failures, and cleanup failure while preserving the primary failure code.
- [x] **Step 2: Run `python -m unittest backend.test_youyun_diffusers -v` and verify the new failure-code assertions fail.**
- [x] **Step 3: Add narrow exception-to-code mapping and preserve existing outcome status/cleanup fields.**
- [x] **Step 4: Run focused provider tests and confirm no secrets or commands appear in outcomes.**
- [x] **Step 5: Commit** with `feat: classify image generation provider failures`.

### Task 3: Diagnostic API and Task Workspace States

**Files:**
- Modify: `backend/image_generation_api.py`
- Extend: `backend/test_image_generation_api.py`
- Modify: `components/image-generation-tasks.tsx`
- Extend: `lib/image-generation-runtime.test.mjs`

**Interfaces:**
- `GET /api/image-generation/tasks/{task_id}/diagnostics` returns `{ taskId, state, phase, progress, lastHeartbeatAt, failure: { code, message } | null, cleanup: { state, error } | null, suggestedAction }`.
- The task detail view renders phase, last progress time, classified failure, cleanup state, and suggested action without rendering remote commands.

- [x] **Step 1: Write failing API/source tests** for diagnostic response fields, safe failure messages, task detail telemetry, and no remote-command rendering.
- [x] **Step 2: Run focused API and frontend tests and verify the new assertions fail.**
- [x] **Step 3: Implement the read-only diagnostics route and render telemetry in the task detail panel; keep confirmation and retry actions unchanged.**
- [x] **Step 4: Run focused API/frontend tests and `npx.cmd tsc --noEmit`.**
- [x] **Step 5: Commit** with `feat: expose image generation diagnostics`.

### Task 4: Local Acceptance, Documentation, and Release Check

**Files:**
- Modify: `README.md`
- Modify: `CURRENT_STATE.md`
- Extend: `backend/test_image_generation_api.py`
- Extend: `backend/test_image_generation_runtime.py`
- Extend: `lib/image-generation.test.mjs`

- [x] **Step 1: Add a local Fake end-to-end acceptance test** covering successful completion plus each classified failure path, asserting no real Youyun manager or local GPU process is invoked.
- [x] **Step 2: Update documentation** with telemetry fields, failure-code meanings, cleanup retry behavior, and the boundary that real Youyun acceptance remains manual.
- [x] **Step 3: Run full verification:**
  - `python -m unittest discover -s backend -p "test_*.py" -q`
  - `python -m compileall -q backend scripts`
  - `npm.cmd run test:frontend`
  - `npx.cmd tsc --noEmit`
  - `git diff --check`
  - `npm.cmd run build`
- [x] **Step 4: Commit** with `feat: add image generation runtime diagnostics`.
