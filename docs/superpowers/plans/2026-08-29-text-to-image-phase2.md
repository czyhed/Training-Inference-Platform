# 文生图阶段二 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在阶段一配置快照之上实现 Diffusers 优云异步文生图任务、结果收集和失败清理状态机。

**Architecture:** 新增持久化 `ImageGenerationRuntime` 管理待确认任务和单 worker 队列；新增 `YouyunDiffusersProvider` 负责一次性实例、SSH、远端 worker、模型下载兜底、结果下载和实例清理。API 只在确认后入队，前端通过轮询任务详情展示进度，所有本地测试均使用 fake manager/SSH，不创建真实优云实例。

**Tech Stack:** FastAPI, Pydantic, asyncio, SQLite-compatible inference service store, existing Youyun instance manager/SSH client, Next.js 14, React 18, TypeScript.

**Spec:** `docs/superpowers/specs/2026-08-29-text-to-image-phase2-design.md`

## Global Constraints

- 不在本机启动 Diffusers、ComfyUI 或文生图 GPU 服务。
- 不在自动化测试中创建真实优云实例。
- 创建任务只保存待确认配置；确认后才允许进入优云队列。
- 复用视觉理解的单 GPU 执行锁和优云清理契约。
- 远端只执行平台生成的 Diffusers worker，不接受任意 Shell 命令。
- 优先使用优云镜像和共享模型目录，只有开启配置时才下载模型兜底。
- 不读取、提交或输出真实密钥、数据库密码或 SSH 私钥。

### Task 1: 任务契约与 Runtime 状态机

**Files:**
- Create: `backend/image_generation_runtime.py`
- Create: `backend/test_image_generation_runtime.py`
- Modify: `backend/app.py`

**Interfaces:**
- `ImageGenerationRuntime(task_root: Path, execution_lock: asyncio.Lock, provider_factory: Callable[..., Any], busy_check: Callable[[], bool] | None = None)`
- `create_task(task: Mapping[str, Any]) -> dict[str, Any]`
- `get(task_id: str) -> dict[str, Any] | None`
- `list_tasks() -> list[dict[str, Any]]`
- `confirm(task_id: str) -> dict[str, Any] | None`
- `request_stop(task_id: str) -> Awaitable[dict[str, Any] | None]`
- `retry(task_id: str) -> Awaitable[dict[str, Any] | None]`
- `start()`, `stop()`, `enqueue(task_id: str)`

- [x] **Step 1: Write failing tests** for safe task IDs, persistence, confirmation transition, queued stop, failed/stopped retry, restart recovery, and shared execution-lock behavior.
- [x] **Step 2: Run `python -m unittest backend.test_image_generation_runtime -v`** and confirm the missing runtime/API causes expected failures.
- [x] **Step 3: Implement atomic JSON persistence and the single-worker state machine** using the same locking and timestamp style as `VisionTrainingRuntime`.
- [x] **Step 4: Add lifecycle callbacks** for provider stages, logs, progress, results, and cleanup reports; persist all updates before returning.
- [x] **Step 5: Run the focused runtime tests and commit** with `feat: add image generation task runtime`.

### Task 2: Diffusers 优云 Provider

**Files:**
- Create: `backend/providers/youyun/diffusers.py`
- Create: `backend/test_youyun_diffusers.py`
- Modify: `backend/providers/youyun/__init__.py`

**Interfaces:**
- `YouyunDiffusersHandle(instance_id: str, workdir: str, status_path: str, log_path: str, ssh: YouyunSSHClient | None = None)`
- `YouyunDiffusersProvider.start(task_id: str, task: Mapping[str, Any], on_stage: Callable[..., None] | None = None, on_log: Callable[[str], None] | None = None) -> Awaitable[dict[str, Any]]`
- `YouyunDiffusersProvider.stop(handle: YouyunDiffusersHandle) -> Awaitable[dict[str, Any]]`
- `YouyunDiffusersProvider.read_status(handle: YouyunDiffusersHandle) -> Awaitable[dict[str, Any]]`

- [x] **Step 1: Write failing fake-manager/fake-SSH tests** for image/resource arguments, image-first model resolution, download fallback, generated worker command, status polling, early process exit, result download and cleanup failure persistence.
- [x] **Step 2: Run `python -m unittest backend.test_youyun_diffusers -v`** and confirm the provider module is missing.
- [x] **Step 3: Implement the provider** with shell-quoted paths, a generated worker script, bounded polling, log-tail errors and mandatory cleanup in `finally`.
- [x] **Step 4: Keep model and Adapter lineage server-side** by accepting resolved paths only from the task payload assembled by the API/runtime.
- [x] **Step 5: Run focused provider tests and commit** with `feat: add youyun diffusers provider`.

### Task 3: FastAPI Task and Result Routes

**Files:**
- Modify: `backend/image_generation_api.py`
- Modify: `backend/app.py`
- Extend: `backend/test_image_generation_api.py`

**Interfaces:**
- `POST /api/image-generation/tasks`
- `GET /api/image-generation/tasks`
- `GET /api/image-generation/tasks/{task_id}`
- `POST /api/image-generation/tasks/{task_id}/confirm`
- `POST /api/image-generation/tasks/{task_id}/stop`
- `POST /api/image-generation/tasks/{task_id}/retry`
- `GET /api/image-generation/tasks/{task_id}/results/{result_id}`
- `GET /api/image-generation/tasks/{task_id}/logs`

- [x] **Step 1: Add failing API tests** for strict payload validation, service/model lineage lookup, confirmation separation, zero instance calls before confirmation, and result path traversal rejection.
- [x] **Step 2: Run `python -m unittest backend.test_image_generation_api -v`** and confirm the new endpoints are absent.
- [x] **Step 3: Implement Pydantic request models and router wiring** using injected `ImageGenerationRuntime`; revalidate service snapshots on confirmation.
- [x] **Step 4: Add result streaming** with task-root containment checks and `FileResponse` content type `image/png`.
- [x] **Step 5: Run image-generation API tests and commit** with `feat: add image generation task api`.

### Task 4: 文生图任务前端

**Files:**
- Create: `components/image-generation-tasks.tsx`
- Modify: `components/image-generation-module.tsx`
- Modify: `app/page.tsx`
- Modify: `app/globals.css`
- Create: `lib/image-generation-runtime.test.mjs`

**Interfaces:**
- Consumes the task/result endpoints from Task 3.
- Shows `awaiting_confirmation`, `queued`, `preparing`, `running`, `collecting`, `completed`, `stopped`, and `failed` states.
- Keeps task creation and cloud confirmation as separate actions.

- [x] **Step 1: Add failing source tests** for task form fields, confirmation separation, stage timeline, result thumbnails, download links and retry controls.
- [x] **Step 2: Run `node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --test lib/image-generation-runtime.test.mjs`** and confirm the component is missing.
- [x] **Step 3: Implement the task list/detail component** with bounded polling, abort on unmount, inline errors and no arbitrary command display.
- [x] **Step 4: Add styles** for timeline, progress, result grid and destructive confirmation states without nested cards.
- [x] **Step 5: Run frontend source tests and `npx.cmd tsc --noEmit`, then commit** with `feat: add image generation task workspace`.

### Task 5: Integration, documentation and acceptance

**Files:**
- Modify: `README.md`
- Modify: `CURRENT_STATE.md`
- Extend: `backend/test_image_generation_api.py`
- Extend: `lib/image-generation.test.mjs`

- [x] **Step 1: Add an end-to-end local contract test** covering service snapshot -> task creation -> confirmation -> fake provider completion -> result metadata, asserting no real Youyun manager is used.
- [x] **Step 2: Run focused and full verification:**
  - `python -m unittest backend.test_image_generation_api backend.test_image_generation_runtime backend.test_youyun_diffusers`
  - `node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --test lib/*.test.mjs`
  - `python -m compileall -q backend scripts`
  - `npx.cmd tsc --noEmit`
  - `git diff --check`
- [x] **Step 3: Update current-state documentation** with implemented task states and deferred real-instance acceptance.
- [x] **Step 4: Perform a code review of the diff** for secret leakage, path traversal, shell injection, orphan cleanup and shared GPU lock violations.
- [ ] **Step 5: Commit** with `docs: record image generation phase two acceptance`.
