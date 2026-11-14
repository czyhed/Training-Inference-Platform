# 文生图阶段六结果管理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有文生图任务运行时上增加任务历史筛选、结果聚合、克隆重生成、归档恢复和安全删除能力。

**Architecture:** 继续使用 `ImageGenerationRuntime` 的 JSON 任务记录和任务结果目录，不引入新的数据库或对象存储。运行时负责任务级归档、克隆和删除状态约束；API 负责参数校验、筛选和服务结果聚合；Next.js 负责任务历史与服务详情中的只读结果视图。克隆只复制配置快照并生成新的 `awaiting_confirmation` 任务，只有用户后续确认时才进入现有优云队列。

**Tech Stack:** FastAPI, Pydantic, Python `unittest`, asyncio JSON runtime, Next.js 14, React 18, TypeScript, Node test runner.

**Spec:** `docs/superpowers/specs/2026-08-29-text-to-image-phase6-design.md`

## Global Constraints

- 复用现有文生图任务运行时、任务目录和结果接口，不新建独立结果存储系统。
- 仅保存和管理已经生成的本地结果文件；不在本阶段接入 MinIO 结果归档或对象生命周期策略。
- 任务创建、克隆、归档和恢复都不申请优云实例；只有用户确认任务时才进入现有优云运行链路。
- 本阶段不启动本机 GPU、Diffusers、SSH 或真实优云实例，全部通过 Fake Provider、API 测试和前端源代码测试验证。
- 不修改用户未跟踪目录，不执行工作区重置或清理。

---

### Task 1: Runtime 任务归档、克隆与删除

**Files:**
- Modify: `backend/image_generation_runtime.py`
- Test: `backend/test_image_generation_runtime.py`

**Interfaces:**
- `list_tasks(*, service_id: str | None = None, state: str | None = None, query: str | None = None, include_archived: bool = False) -> list[dict[str, Any]]`
- `clone_task(task_id: str, overrides: Mapping[str, Any] | None = None) -> dict[str, Any] | None`
- `archive_task(task_id: str) -> dict[str, Any] | None`
- `restore_task(task_id: str) -> dict[str, Any] | None`
- `delete_task(task_id: str) -> bool`

- [ ] **Step 1: Write failing runtime tests**

Add tests that create temporary JSON tasks and assert:

```python
task = runtime.create_task({"id": "igt-1", "serviceId": "svc", "prompt": "cat", "state": "completed", "results": []})
listed = runtime.list_tasks()
assert [item["id"] for item in listed] == ["igt-1"]
assert runtime.archive_task("igt-1")["archived"] is True
assert runtime.list_tasks() == []
assert runtime.list_tasks(include_archived=True)[0]["archivedAt"]
```

Cover these cases:

- `create_task` defaults `archived=False` and `archivedAt=None`.
- `list_tasks` filters by service ID, state, case-insensitive task name/Prompt query, and hides archived items by default.
- `clone_task` copies configuration fields and creates a new ID/state `awaiting_confirmation`, with empty results/logs and no `remoteHandle`, telemetry, failure, cleanup, or cancel fields.
- Clone overrides replace only supplied Prompt/parameter fields and leave the source JSON unchanged.
- `archive_task` allows only `completed`, `failed`, and `stopped`; active states raise `ValueError` and repeated archive is rejected.
- `restore_task` clears archive visibility without enqueueing or changing state.
- `delete_task` refuses active tasks, removes the JSON record and result directory on success, and keeps the JSON record when directory deletion raises `OSError`.

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```powershell
python -m unittest backend.test_image_generation_runtime -v
```

Expected: new tests fail because archive, clone, filtering, and deletion methods do not exist.

- [ ] **Step 3: Implement the minimal runtime contract**

Implement the methods above in `ImageGenerationRuntime`:

- Normalize archive fields in `create_task` and preserve them through `save`/`update`.
- Apply filters while holding the existing re-entrant lock, then sort by `createdAt` descending.
- Generate clone IDs with the existing `igt-YYMMDD-HHMMSS-xxxx` shape, copy only immutable service/model/resource/parameter fields, set `name` to `原名称 · 副本`, and call `create_task` so timestamps and defaults are consistent.
- Keep a clone's `results` and `logs` empty; never copy runtime handles, failure data, cleanup data, cancellation flags, or phase timestamps.
- Enforce archive state rules before writing `archived` and `archivedAt`.
- For deletion, reject `ACTIVE_STATES` and `queued`; remove the result root with `shutil.rmtree(..., ignore_errors=False)` and only unlink the JSON file after filesystem cleanup succeeds. Return `False` for missing tasks and surface a stable `OSError` message for cleanup failure.

- [ ] **Step 4: Run focused runtime tests**

Run the same unittest command and confirm all existing and new runtime tests pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/image_generation_runtime.py backend/test_image_generation_runtime.py
git commit -m "feat: add image generation task history lifecycle"
```

### Task 2: Image Generation History and Result APIs

**Files:**
- Modify: `backend/image_generation_api.py`
- Test: `backend/test_image_generation_api.py`

**Interfaces:**
- `ImageGenerationTaskClone` Pydantic model with optional `prompt`, `negativePrompt`, `width`, `height`, `steps`, `cfg`, `batchSize`, `seed`, and `sampler` fields using the same bounds as `ImageGenerationTaskCreate`.
- `GET /api/image-generation/tasks?serviceId=&state=&query=&includeArchived=` delegates filtering to `task_runtime.list_tasks`.
- `POST /api/image-generation/tasks/{task_id}/clone` returns a new awaiting-confirmation task.
- `POST /api/image-generation/tasks/{task_id}/archive` and `/restore` return the updated task.
- `DELETE /api/image-generation/tasks/{task_id}` removes a non-running task and its results.
- `GET /api/image-generation/services/{service_id}/results?state=&query=&includeArchived=&page=&pageSize=` returns `{serviceId, items, page, pageSize, total}` where each item contains task summary plus result metadata.

- [ ] **Step 1: Write failing API tests**

Extend the existing `ImageGenerationTaskApiTests` Fake runtime with the new runtime methods, then add tests for:

- Default task listing excludes archived records; `includeArchived=true`, `serviceId`, `state`, and `query` return the expected subset.
- Clone accepts overrides, returns `201`, has `awaiting_confirmation`, empty results, and does not call `confirm` or enqueue anything.
- Clone of a missing task returns `404`; malformed override values return `422`; clone revalidates the current service snapshot before creating the task.
- Archive succeeds for a completed task, rejects an active task with `409`, and restore returns the task with `archived=false`.
- Archived tasks reject confirm, stop, retry, and repeated archive with `409`.
- Delete removes the task and results; active-task deletion returns `409`; missing task returns `404`; cleanup failure returns `409` while preserving the task.
- Service result aggregation returns only the selected service's tasks, includes result metadata and task fields, supports state/query/archive filters and pagination, and still works for an archived service in read-only mode.
- Result downloads still reject path traversal and missing files after task lifecycle operations.

- [ ] **Step 2: Run focused API tests and verify failure**

Run:

```powershell
python -m unittest backend.test_image_generation_api -v
```

Expected: new route tests fail with `404` or missing Fake runtime methods.

- [ ] **Step 3: Implement the API models and routes**

Implement the clone model with `extra="forbid"` and validators matching `ImageGenerationTaskCreate`. Add route handlers that:

- Read the task through `get_image_generation_task` so `404` behavior is consistent.
- Reject archived tasks before confirm/stop/retry and return `HTTPException(status_code=409, detail=...)`.
- Pass only explicitly provided clone overrides to `task_runtime.clone_task` and translate `ValueError` to `409`.
- Use `Query` defaults that preserve the current no-argument list behavior.
- Build service result items from filtered tasks without exposing `resultRoot`, local paths, remote handles, or raw errors. Preserve only `id`, `taskId`, `taskName`, `prompt`, `negativePrompt`, generation parameters, model/Adapter identity, task state/progress, timestamps, and safe result metadata.
- Clamp `page` to `>=1` and `pageSize` to `1..100`; return deterministic newest-first ordering.

- [ ] **Step 4: Run focused API tests**

Run:

```powershell
python -m unittest backend.test_image_generation_api -v
```

Confirm all API tests pass and no test invokes a real Youyun client or provider.

- [ ] **Step 5: Commit**

```powershell
git add backend/image_generation_api.py backend/test_image_generation_api.py
git commit -m "feat: add image generation history APIs"
```

### Task 3: Task History and Service Result UI

**Files:**
- Modify: `components/image-generation-tasks.tsx`
- Modify: `components/image-generation-service.tsx`
- Modify: `lib/image-generation-runtime.test.mjs`
- Modify: `app/globals.css`

**Interfaces:**
- Task page requests filtered task lists and the clone/archive/restore/delete endpoints using `apiUrl`.
- Service page requests `GET /image-generation/services/{id}/results` and renders read-only result history.
- Existing preflight, confirmation, stop, retry, diagnostics, and result-download behavior remains intact.

- [ ] **Step 1: Write failing frontend source tests**

Add source assertions for:

- Service/state/query/archive filters and an archived-task view.
- `clone`, `archive`, `restore`, and `DELETE` route usage.
- Clone form initialization from the selected task and editable Prompt/parameter fields before submission.
- Disabled action buttons for active or archived tasks.
- Service result-history request, thumbnail rendering, task metadata, download link, and read-only archived-service state.
- No rendering of `resultRoot`, `remoteHandle`, raw `error`, or remote commands.

- [ ] **Step 2: Run the focused frontend test and verify failure**

Run:

```powershell
npm.cmd run test:frontend -- --test-name-pattern="image generation"
```

Expected: new source assertions fail because the controls and service history panel do not exist.

- [ ] **Step 3: Implement task history controls**

Update `ImageGenerationTasks` to:

- Track filter state and reload with URL-encoded query parameters.
- Add an archived toggle and service/state/query controls while retaining the existing polling behavior for active tasks.
- Add a clone mode that fills the existing creation form from the selected task, lets the user edit fields, then posts to `/tasks/{id}/clone` and selects the new draft.
- Add archive/restore/delete actions with confirmation prompts and safe error banners.
- Hide or disable actions according to the runtime state rules; keep confirmation as the only action that can start a paid run.
- Mark missing result files as unavailable from the API item state without breaking other result cards.

- [ ] **Step 4: Implement service result history**

Update `ImageGenerationService` to load the selected service's result aggregate, render a compact read-only gallery/table, and expose task navigation plus download links. Do not add task execution controls to archived services.

- [ ] **Step 5: Add responsive styles and run frontend checks**

Use existing panel, table, button, and gallery classes; add only scoped selectors for filters, clone mode, archive badges, result metadata, and unavailable files. Verify text remains within controls on narrow viewports.

Run:

```powershell
npm.cmd run test:frontend
npx.cmd tsc --noEmit
```

- [ ] **Step 6: Commit**

```powershell
git add components/image-generation-tasks.tsx components/image-generation-service.tsx lib/image-generation-runtime.test.mjs app/globals.css
git commit -m "feat: add image generation history workspace"
```

### Task 4: Documentation and Full Regression

**Files:**
- Modify: `README.md`
- Modify: `CURRENT_STATE.md`
- Extend: `backend/test_image_generation_runtime.py`
- Extend: `backend/test_image_generation_api.py`
- Extend: `lib/image-generation-runtime.test.mjs`

**Interfaces:**
- Documentation records the new task history, clone, archive, restore, delete, and service-result endpoints.
- Acceptance tests prove the full local Fake lifecycle without creating a real cloud resource.

- [ ] **Step 1: Add local Fake acceptance coverage**

Add one API-level acceptance test that creates two tasks for different services, completes one with Fake results, archives it, clones it with a Prompt override, verifies the clone is awaiting confirmation, and checks service aggregation plus result download. Assert no Youyun resolver, image checker, provider, SSH, or GPU process is called.

- [ ] **Step 2: Update project documentation**

Document the default archive filtering, clone semantics, deletion cleanup behavior, service result history, and the continued boundary that real Youyun acceptance is manual.

- [ ] **Step 3: Run the focused and full verification commands**

```powershell
python -m unittest backend.test_image_generation_runtime backend.test_image_generation_api -q
python -m unittest discover -s backend -p "test_*.py" -q
python -m compileall -q backend scripts
npm.cmd run test:frontend
npx.cmd tsc --noEmit
git diff --check
npm.cmd run build
```

Expected: all commands exit with code 0; no GPU, SSH, Diffusers, or real Youyun process is started.

- [ ] **Step 4: Commit**

```powershell
git add README.md CURRENT_STATE.md backend/test_image_generation_runtime.py backend/test_image_generation_api.py lib/image-generation-runtime.test.mjs
git commit -m "docs: complete image generation phase six"
```

## Self-Review

- Spec coverage: data fields and runtime lifecycle are covered by Task 1; all API contracts and safe result aggregation by Task 2; task/service UI behavior by Task 3; Fake acceptance and documentation by Task 4.
- Placeholder scan: no `TBD`, `TODO`, or unspecified implementation steps are used.
- Type consistency: API routes call the runtime methods defined in Task 1; frontend endpoints match Task 2 paths and payload names; tests use the same field names as the runtime and API contracts.

