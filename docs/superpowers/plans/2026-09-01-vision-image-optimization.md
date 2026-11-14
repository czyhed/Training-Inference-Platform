# 视觉理解与文生图功能优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让视觉理解、文生图训练和文生图生成达到 NLP 模块的状态一致性、资源安全、日志指标完整性和任务操作体验，并通过一次带费用兜底的真实优云验收。

**Architecture:** 保留三个现有运行时和模块边界，在任务记录中补齐统一资源/清理语义；通过启动核对、运行看门狗和 `finally` 清理形成三层资源兜底。前端复用 NLP 的任务列表/详情信息层级，后端列表返回摘要、详情返回完整观测数据。

**Tech Stack:** Python 3.12、FastAPI、asyncio、SQLite 文件任务存储、Youyun API、Next.js 14、React 18、TypeScript、Node 内置测试。

**Spec:** `docs/superpowers/specs/2026-09-01-vision-image-optimization-design.md`

## Global Constraints

- 远端任务一次只允许一个优云实例；确认实例消失前不得开始下一次真实验收。
- 任何成功、失败、停止、超时、取消、断连和下载异常都必须进入幂等清理流程。
- 清理未确认成功时不得写入 `cleanupState=released`，不得清除 `remoteHandle`/`instanceId`，不得允许直接重试创建实例。
- 不向浏览器返回优云私钥、SSH 密码、原始远程命令或未校验的文件路径。
- 保留现有 API 路径和兼容字段；新增字段使用 camelCase 响应名。
- 每个生产代码改动必须先有会失败的测试，再实现最小改动。
- 不回滚工作区中与本计划无关的既有用户改动。

---

### Task 1: 锁定统一生命周期与错误契约

**Files:**
- Modify: `backend/vision_runtime.py`
- Modify: `backend/image_generation_runtime.py`
- Modify: `backend/image_generation_training_runtime.py`
- Modify: `backend/vision_api.py`
- Modify: `backend/image_generation_api.py`
- Modify: `backend/image_generation_training_api.py`
- Test: `backend/test_vision_runtime.py`
- Test: `backend/test_image_generation_runtime.py`
- Test: `backend/test_image_generation_training_runtime.py`
- Test: `backend/test_vision_api.py`
- Test: `backend/test_image_generation_api.py`
- Test: `backend/test_image_generation_training_api.py`

**Interfaces:**
- Consumes: existing task JSON records and provider outcomes.
- Produces: normalized `resourceState`, `cleanupState`, `cleanupError`, `cleanupCheckedAt`, `failureCode`, `failureMessage`, `suggestedAction` fields; shared transition validation used by later tasks.

- [ ] **Step 1: Write failing lifecycle tests**

Add tests that create a remote task with an instance handle and assert:

```python
assert task["resourceState"] == "running"
assert task["cleanupState"] == "pending"
```

Add a separate cleanup-failure assertion for `failureCode == "cleanup_failed"`. Cover invalid retry while `cleanupState == "failed"`, repeated cleanup calls, and terminal success that keeps `cleanupState != "released"` when provider cleanup reports failure.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
python -m pytest backend/test_vision_runtime.py backend/test_image_generation_runtime.py backend/test_image_generation_training_runtime.py -q
```

Expected: failures for missing normalized fields or missing cleanup/retry guards.

- [ ] **Step 3: Implement the smallest normalization layer**

Add runtime helpers that normalize provider outcomes before persistence, preserve old `error`/`remoteHandle` fields, and reject retries until a failed cleanup is resolved. Add API serialization defaults so old records return explicit `resourceState`/`cleanupState` values without rewriting their logs or artifacts.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the same pytest command; all selected tests must pass with no unhandled warnings.

- [ ] **Step 5: Run API regression tests**

```powershell
python -m pytest backend/test_vision_api.py backend/test_image_generation_api.py backend/test_image_generation_training_api.py -q
```

- [ ] **Step 6: Commit**

```powershell
git add backend/vision_runtime.py backend/image_generation_runtime.py backend/image_generation_training_runtime.py backend/vision_api.py backend/image_generation_api.py backend/image_generation_training_api.py backend/test_vision_runtime.py backend/test_image_generation_runtime.py backend/test_image_generation_training_runtime.py backend/test_vision_api.py backend/test_image_generation_api.py backend/test_image_generation_training_api.py
git commit -m "feat: normalize vision and image task lifecycle"
```

### Task 2: Add remote reconciliation, watchdog, and idempotent cleanup

**Files:**
- Modify: `backend/providers/youyun/instance_manager.py`
- Modify: `backend/providers/youyun/remote_training.py`
- Modify: `backend/providers/youyun/image_generation_training.py`
- Modify: `backend/providers/youyun/diffusers.py`
- Modify: `backend/vision_runtime.py`
- Modify: `backend/image_generation_runtime.py`
- Modify: `backend/image_generation_training_runtime.py`
- Test: `backend/test_youyun_instance_manager.py`
- Test: `backend/test_youyun_remote_training.py`
- Test: `backend/test_youyun_image_generation_training.py`
- Test: `backend/test_youyun_diffusers.py`
- Test: `backend/test_vision_runtime.py`
- Test: `backend/test_image_generation_runtime.py`
- Test: `backend/test_image_generation_training_runtime.py`

**Interfaces:**
- Consumes: `YouyunClient.describe_instances`, persisted remote handles, existing provider cleanup methods.
- Produces: `reconcile_remote_task(task)`, `cleanup_remote_task(task)`, bounded retry/verification behavior, watchdog timeout configuration via environment variables.

- [ ] **Step 1: Write failing cleanup tests**

Cover these provider behaviors:

```python
report = await runtime.cleanup_remote_task(task)
assert report["terminated"] is True
assert client.describe_instance_calls == 2  # delete, then disappearance verification
```

Also cover an API outage, repeated cleanup, stale handle whose instance is already absent, and watchdog timeout that invokes cleanup exactly once.

- [ ] **Step 2: Run tests and verify RED**

```powershell
python -m pytest backend/test_youyun_instance_manager.py backend/test_youyun_remote_training.py backend/test_youyun_image_generation_training.py backend/test_youyun_diffusers.py -q
```

Expected: failures for missing reconciliation/watchdog behavior.

- [ ] **Step 3: Implement idempotent cleanup and reconciliation**

Add bounded cleanup retries with backoff, post-delete polling, and an explicit report containing `terminated`, `error`, `checkedAt`, and `attempts`. Treat an already-absent instance as released. Persist failed cleanup without dropping the handle. Add startup reconciliation before queues accept new remote work.

- [ ] **Step 4: Implement watchdog and hard timeout**

Track `lastHeartbeatAt`/`lastProgressAt`. Add environment-bounded values such as `XUNTUI_REMOTE_TASK_MAX_SECONDS` and `XUNTUI_REMOTE_TASK_STALL_SECONDS`, with conservative defaults suitable for acceptance. On timeout or stalled progress, stop the remote process, call cleanup in `finally`, and classify the timeout as `resource_timeout`; classify a failed delete or disappearance check as `cleanup_failed`.

- [ ] **Step 5: Run tests and verify GREEN**

Run the focused pytest command again, then:

```powershell
python -m pytest backend/test_vision_runtime.py backend/test_image_generation_runtime.py backend/test_image_generation_training_runtime.py -q
```

- [ ] **Step 6: Commit**

```powershell
git add backend/providers/youyun/instance_manager.py backend/providers/youyun/remote_training.py backend/providers/youyun/image_generation_training.py backend/providers/youyun/diffusers.py backend/vision_runtime.py backend/image_generation_runtime.py backend/image_generation_training_runtime.py backend/test_youyun_instance_manager.py backend/test_youyun_remote_training.py backend/test_youyun_image_generation_training.py backend/test_youyun_diffusers.py backend/test_vision_runtime.py backend/test_image_generation_runtime.py backend/test_image_generation_training_runtime.py
git commit -m "feat: guard remote task cleanup and recovery"
```

### Task 3: Make logs, metrics, and failure diagnostics durable

**Files:**
- Modify: `backend/vision_runtime.py`
- Modify: `backend/image_generation_training_runtime.py`
- Modify: `backend/image_generation_runtime.py`
- Modify: `backend/vision_api.py`
- Modify: `backend/image_generation_api.py`
- Modify: `backend/image_generation_training_api.py`
- Test: `backend/test_vision_runtime.py`
- Test: `backend/test_image_generation_training_runtime.py`
- Test: `backend/test_image_generation_runtime.py`
- Test: `backend/test_vision_api.py`
- Test: `backend/test_image_generation_api.py`
- Test: `backend/test_image_generation_training_api.py`

**Interfaces:**
- Consumes: provider events, existing `logs`, `lossHistory`/`metrics`, result records.
- Produces: stable detail payloads containing logs, train/eval loss, best/final summary, resource state, and classified failure suggestions; lightweight list summaries.

- [ ] **Step 1: Write failing persistence tests**

Run a Fake executor/provider, reload the runtime from disk, and assert logs and metrics remain available through the detail route. Add a result consistency test where sample outcomes determine `successRate` and failure count.

- [ ] **Step 2: Run tests and verify RED**

```powershell
python -m pytest backend/test_vision_runtime.py backend/test_image_generation_training_runtime.py backend/test_image_generation_runtime.py backend/test_vision_api.py backend/test_image_generation_api.py backend/test_image_generation_training_api.py -q
```

- [ ] **Step 3: Implement durable telemetry and error mapping**

Normalize event payloads to Step/Loss/Eval Loss, deduplicate by Step, cap retained logs/metrics using existing limits, and map provider exceptions to the stable error categories from the design. Ensure list responses omit large logs while detail responses include them.

- [ ] **Step 4: Run tests and verify GREEN**

Re-run the same pytest command and inspect one serialized detail payload for all required fields.

- [ ] **Step 5: Commit**

```powershell
git add backend/vision_runtime.py backend/image_generation_training_runtime.py backend/image_generation_runtime.py backend/vision_api.py backend/image_generation_api.py backend/image_generation_training_api.py backend/test_vision_runtime.py backend/test_image_generation_training_runtime.py backend/test_image_generation_runtime.py backend/test_vision_api.py backend/test_image_generation_api.py backend/test_image_generation_training_api.py
git commit -m "feat: persist telemetry and classify task failures"
```

### Task 4: Align visual understanding and image-generation UI with NLP

**Files:**
- Modify: `components/vision-training-module.tsx`
- Modify: `components/vision-understanding-module.tsx`
- Modify: `components/image-generation-training.tsx`
- Modify: `components/image-generation-tasks.tsx`
- Modify: `components/image-generation-inference-workspace.tsx`
- Modify: `app/globals.css`
- Modify: `lib/types.ts`
- Test: `lib/vision-training-provider.test.mjs`
- Test: `lib/image-generation.test.mjs`
- Test: `lib/inference-vision.test.mjs`

**Interfaces:**
- Consumes: normalized list/detail API payloads from Tasks 1-3.
- Produces: shared task summary/filter helpers, state-aware action matrix, detail tabs for overview/logs/metrics/artifacts or results, sample previews and explicit cleanup/error actions.

- [ ] **Step 1: Write failing pure UI contract tests**

Add Node tests for state labels, filter counts, action availability, cleanup-failed blocking, and result rendering decisions:

```js
assert.deepEqual(taskActions({ state: "failed", cleanupState: "failed" }), ["detail", "cleanup"]);
assert.equal(canDownloadResult({ state: "failed" }), false);
```

- [ ] **Step 2: Run frontend tests and verify RED**

```powershell
npm run test:frontend
```

- [ ] **Step 3: Implement shared presentation behavior**

Add summary counts, search/state filtering, compact timestamps, and state-aware actions. Add detail tabs showing resource/cleanup state, logs, Loss/Eval Loss, Best/Final Adapter, failure category and suggested action. Add visual IMAGE_TEXT thumbnails and image-generation result gallery with success-only preview/download.

- [ ] **Step 4: Run tests and type/build checks**

```powershell
npm run test:frontend
npx tsc --noEmit
npm run build
```

Fix all failures before proceeding; do not hide TypeScript errors with casts.

- [ ] **Step 5: Perform local browser smoke test**

With the existing local frontend/backend running, verify list filtering, detail refresh, log/metric tabs, cleanup-failed action, visual sample preview, and generated-image download using local Fake task data. Capture any mismatch as a follow-up fix before commit.

- [ ] **Step 6: Commit**

```powershell
git add components/vision-training-module.tsx components/vision-understanding-module.tsx components/image-generation-training.tsx components/image-generation-tasks.tsx components/image-generation-inference-workspace.tsx app/globals.css lib/types.ts lib/vision-training-provider.test.mjs lib/image-generation.test.mjs lib/inference-vision.test.mjs
git commit -m "feat: unify vision and image task experience"
```

### Task 5: Add guarded real Youyun acceptance harness

**Files:**
- Create: `scripts/verify_youyun_guarded_acceptance.py`
- Modify: `.env.example`
- Modify: `README.md`
- Test: `backend/test_youyun_instance_manager.py`
- Test: `backend/test_youyun_provider.py`

**Interfaces:**
- Consumes: `.env.local` Youyun credentials through `YouyunConfig.from_env`, existing provider/instance manager, one selected model/dataset/task payload.
- Produces: a command-line acceptance run that creates at most one instance, records lifecycle timestamps, enforces hard timeout, and always deletes/verifies the instance before exiting.

- [ ] **Step 1: Write failing harness safety tests**

Test the harness with a fake client:

```python
assert fake_client.max_created_instances == 1
assert fake_client.delete_calls == 1
assert result["instanceVerifiedAbsent"] is True
```

Cover provider exception, timeout, `KeyboardInterrupt`, and delete API failure. The harness must attempt cleanup in all cases and return non-zero when absence cannot be verified.

- [ ] **Step 2: Run tests and verify RED**

```powershell
python -m pytest backend/test_youyun_instance_manager.py backend/test_youyun_provider.py -q
```

- [ ] **Step 3: Implement the guarded harness**

Use an async/main `try/finally`, a process-level lock file, one-instance assertion, `XUNTUI_ACCEPTANCE_MAX_SECONDS` hard timeout, bounded cleanup retries, and final `DescribeCompShareInstance` polling. Never print credentials or raw SSH commands. Emit a concise JSON summary with instance ID, task IDs, timestamps, cleanup attempts, and `instanceVerifiedAbsent`.

- [ ] **Step 4: Run all automated gates before cloud use**

```powershell
python -m pytest backend -q
npm run test:frontend
npx tsc --noEmit
npm run build
```

Do not start an instance if any command fails.

- [ ] **Step 5: Run direct real Youyun acceptance**

Set a short acceptance timeout and run exactly one selected flow at a time. Before creation, query the instance list and assert there are no existing `cppt-*` acceptance instances. After the task completes or fails, let the harness stop/delete and poll until the instance disappears. If it cannot verify absence, stop and report the instance ID without starting another run.

- [ ] **Step 6: Verify no billable leftovers**

Run a final read-only instance query and record `LIVE_COUNT`, IDs, names, states, and whether each is related to this acceptance. The required result is `LIVE_COUNT=0` for acceptance-created instances.

- [ ] **Step 7: Commit**

```powershell
git add scripts/verify_youyun_guarded_acceptance.py .env.example README.md backend/test_youyun_instance_manager.py backend/test_youyun_provider.py
git commit -m "test: add guarded Youyun acceptance"
```

### Task 6: Final verification and issue-driven optimization loop

**Files:**
- Modify: files identified by failed verification only
- Test: focused regression tests for each failed criterion

**Interfaces:**
- Consumes: all prior task outputs and acceptance summary.
- Produces: verified implementation meeting every hard gate in the design document.

- [ ] **Step 1: Build an acceptance checklist from the design**

Check resource safety, state correctness, logs/metrics durability, result consistency, visual training validation, frontend actions, and real Youyun instance absence.

- [ ] **Step 2: Run the full verification set**

```powershell
python -m pytest backend -q
npm run test:frontend
npx tsc --noEmit
npm run build
```

- [ ] **Step 3: For every failure, add a regression test first**

Reproduce the exact failure, confirm the new test is RED, implement one focused fix, and rerun the smallest relevant command before repeating the full set.

- [ ] **Step 4: Re-run the guarded cloud check after code changes**

Never treat a prior cloud result as current evidence; each final claim must use a fresh instance-list query and acceptance summary.

- [ ] **Step 5: Commit final fixes**

Commit only the exact backend, frontend, script, or test files touched by the failing criterion, using:

```powershell
git commit -m "fix: close vision and image acceptance gaps"
```

Before committing, run `git status --short` and verify unrelated user changes remain unstaged.
