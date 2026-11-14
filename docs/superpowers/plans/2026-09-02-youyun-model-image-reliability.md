# 优云镜像与基座模型稳定性优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将优云模型仓库、镜像、库存、价格和实例生命周期统一为可验证、可回放、可自动释放的训练/推理链路，降低视觉理解与文生图失败率、启动耗时和无效费用。

**Architecture:** 后端新增官方模型仓库同步适配器，保留原始响应并生成统一候选资产；训练和推理共用镜像/基模能力评分与组合预检，但保留视觉理解和文生图的独立兼容矩阵。实例创建采用任务级幂等键、状态轮询、端口/健康检查和 watchdog 释放，前端只消费后端生成的推荐和阻断原因。

**Tech Stack:** Python 3.12、FastAPI、SQLite/PostgreSQL、现有 `YouyunClient`、React/Next.js、unittest、Node smoke tests。

**Spec:** `docs/research/youyun-api-research-2026-09-03.md`

## Global Constraints

- 不创建付费优云实例，除非用户后续明确进行真实验收。
- 优云私钥只允许存在后端环境变量或密钥服务，不进入前端、日志和错误响应。
- 创建实例前必须调用官方容量检查；价格、镜像状态和模型能力必须在提交前复核。
- 社区镜像必须真实探针通过且释放成功后才可提交；官方镜像也必须通过能力检查。
- 所有临时实例必须在成功、失败、停止、超时路径进入释放流程并复核最终状态。
- 不覆盖用户已有数据和无关改动；手工编辑使用 `apply_patch`。

---

### Task 1: 官方模型仓库同步与统一资产映射

**Files:**
- Modify: `backend/providers/youyun/client.py`
- Modify: `backend/providers/youyun/models.py`
- Modify: `backend/app.py`
- Test: `backend/test_youyun_client.py` (create if absent)
- Test: `backend/test_vision_api.py`

**Interfaces:**
- Produces `YouyunClient.describe_model_repository_models(name: str | None = None, tags: str | None = None) -> list[dict[str, Any]]`.
- Produces normalized model rows with `name`, `path`, `tags`, `size`, `create_time`, and preserved `raw` payload.
- `/api/vision/models?provider=youyun` merges live repository rows with existing registered/local rows without duplicate Repo IDs.

- [ ] **Step 1: Write the failing tests**

```python
def test_describe_model_repository_models_sends_official_parameters(self):
    client = YouyunClient(config=self.config, require_full=False)
    client._post = Mock(return_value={"Models": [{"Name": "Qwen2-VL-2B", "Path": "/models/qwen2-vl-2b", "Tag": "VL,Qwen", "Size": "4GB", "CreateTime": 1712563200}]})
    rows = client.describe_model_repository_models(name="qwen", tags="VL")
    sent = client._post.call_args.args
    self.assertEqual(sent[0], "DescribeModelRepositoryModels")
    self.assertEqual(sent[1], {"Region": self.config.region, "name": "qwen", "tags": "VL"})
    self.assertEqual(rows[0]["path"], "/models/qwen2-vl-2b")
    self.assertEqual(rows[0]["tags"], ["VL", "Qwen"])
```

```python
def test_vision_model_catalog_deduplicates_repository_and_registered_rows(self):
    response = self.client.get("/api/vision/models?provider=youyun")
    repos = [row["repoId"] for row in response.json()]
    self.assertEqual(len(repos), len(set(repos)))
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `python -m unittest backend.test_youyun_client backend.test_vision_api -v`

Expected: failure because `describe_model_repository_models` and live catalog merge are missing.

- [ ] **Step 3: Implement the minimal client and merge path**

Add the client method using action `DescribeModelRepositoryModels`, request keys `Region`, `name`, and `tags`; normalize `Models` while preserving `raw`. In the vision catalog route, use Repo ID/path as the deduplication key and retain the most recent live row metadata.

- [ ] **Step 4: Run the focused tests and the existing vision suite**

Run: `python -m unittest backend.test_youyun_client backend.test_vision_api -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/providers/youyun/client.py backend/providers/youyun/models.py backend/app.py backend/test_youyun_client.py backend/test_vision_api.py
git commit -m "feat: sync Youyun model repository catalog"
```

### Task 2: 镜像能力清单与视觉/文生图推荐矩阵

**Files:**
- Modify: `backend/providers/youyun/models.py`
- Modify: `backend/app.py`
- Modify: `backend/vision_api.py`
- Modify: `backend/image_generation_training_api.py`
- Modify: `backend/image_generation_api.py`
- Test: `backend/test_youyun_models.py` (create if absent)
- Test: `backend/test_vision_api.py`
- Test: `backend/test_image_generation_training_api.py`

**Interfaces:**
- Produces `assess_image_capabilities(image, workload, model) -> dict` with hard-gate fields for runtime, CUDA/Torch, model family, GPU fit, and probe state.
- Produces a shared recommendation payload containing `imageId`, `imageName`, `modelRepoId`, `score`, `reasons`, `gpuFit`, `probeState`, and `estimatedStartupSeconds`.

- [ ] **Step 1: Write failing tests**

```python
def test_visual_recommendation_prefers_official_torch_280_image_for_vision(self):
    rows = recommend_images(images, workload="vision_training", gpu_type="4090")
    self.assertEqual(rows[0]["name"], "cuda128_torch280_py312")
    self.assertTrue(rows[0]["gpuFit"])
```

```python
def test_unverified_community_image_is_not_selectable(self):
    row = assess_image_capabilities(community_row, workload="image_generation", model=sdxl_model)
    self.assertFalse(row["selectable"])
    self.assertEqual(row["probeState"], "unverified")
```

- [ ] **Step 2: Run tests to confirm failure**

Run: `python -m unittest backend.test_youyun_models backend.test_vision_api backend.test_image_generation_training_api -v`

- [ ] **Step 3: Implement workload-specific capability scoring**

Use separate matrices for `vision_training`, `vision_inference`, `image_generation_training`, and `image_generation_inference`. Treat community probe failure/unverified as a hard block, filter incompatible GPU types, and include model-family compatibility before price scoring.

- [ ] **Step 4: Wire recommendations into GPU specs and preflight responses**

Return the recommendation plus explicit blockers from `/api/youyun/gpu-specs`, visual preflight, image-generation preflight, and inference startup checks. Keep live platform image fallback when the community catalog is empty.

- [ ] **Step 5: Run tests and type/build checks**

Run: `python -m unittest backend.test_youyun_models backend.test_vision_api backend.test_image_generation_training_api -v`; `npm.cmd run build`.

- [ ] **Step 6: Commit**

```bash
git add backend/providers/youyun/models.py backend/app.py backend/vision_api.py backend/image_generation_training_api.py backend/image_generation_api.py backend/test_youyun_models.py backend/test_vision_api.py backend/test_image_generation_training_api.py
git commit -m "feat: unify Youyun image capability recommendations"
```

### Task 3: 实例幂等、异步状态机与端口健康检查

**Files:**
- Modify: `backend/providers/youyun/instance_manager.py`
- Modify: `backend/providers/youyun/client.py`
- Modify: `backend/vision.py`
- Modify: `backend/providers/youyun/remote_training.py`
- Modify: `backend/providers/youyun/inference.py`
- Modify: `backend/providers/youyun/image_generation_training.py`
- Test: `backend/test_youyun_instance_manager.py`
- Test: `backend/test_youyun_ssh.py`

**Interfaces:**
- Produces `create_one_shot(..., idempotency_key: str | None = None)` behavior that refuses duplicate active creation for the same task/phase.
- Produces state transitions `preflight -> creating -> starting -> running -> probing -> workload -> stopping -> releasing -> released|cleanup_failed`.
- Produces a health result with TCP, HTTP, model-ready, and mapped-port checks.

- [ ] **Step 1: Write failing tests**

```python
def test_same_idempotency_key_does_not_create_second_instance(self):
    first = manager.create_one_shot("task-1", idempotency_key="task-1:probe")
    second = manager.create_one_shot("task-1", idempotency_key="task-1:probe")
    self.assertEqual(first.uhost_id, second.uhost_id)
    self.assertEqual(fake_client.create_calls, 1)
```

```python
def test_cleanup_treats_missing_instance_as_released(self):
    report = manager.cleanup_after_task("uhost-missing")
    self.assertTrue(report["terminated"])
    self.assertEqual(report["error"], None)
```

- [ ] **Step 2: Run tests to confirm failure**

Run: `python -m unittest backend.test_youyun_instance_manager backend.test_youyun_ssh -v`

- [ ] **Step 3: Implement task-scoped idempotency and operation-in-progress handling**

Persist active instance handles by task/phase, check existing handles before create, and on operation-in-progress errors query `DescribeCompShareInstance` before retrying. Do not blindly repeat create, stop, or terminate calls.

- [ ] **Step 4: Add mapped-port and health phases**

After `Running`, query software port mappings, then perform TCP reachability, HTTP health, and model-ready checks. Store each result in the task/service diagnostic record.

- [ ] **Step 5: Run focused tests**

Run: `python -m unittest backend.test_youyun_instance_manager backend.test_youyun_ssh backend.test_youyun_guarded_acceptance -v`

- [ ] **Step 6: Commit**

```bash
git add backend/providers/youyun/instance_manager.py backend/providers/youyun/client.py backend/vision.py backend/providers/youyun/remote_training.py backend/providers/youyun/inference.py backend/providers/youyun/image_generation_training.py backend/test_youyun_instance_manager.py backend/test_youyun_ssh.py
git commit -m "feat: make Youyun instance lifecycle idempotent"
```

### Task 4: 费用看门狗、清理复核与日志指标

**Files:**
- Modify: `backend/app.py`
- Modify: `backend/vision_api.py`
- Modify: `backend/image_generation_training_runtime.py`
- Modify: `backend/inference_service.py`
- Modify: `backend/providers/youyun/instance_manager.py`
- Test: `backend/test_youyun_guarded_acceptance.py`
- Test: `backend/test_postgres_pool.py`

**Interfaces:**
- Produces task/service fields `budget`, `expiresAt`, `lastHeartbeat`, `instanceId`, `cleanupState`, `priceSnapshot`, and `phaseTimings`.
- Produces a background watchdog that stops/releases stale remote work and records cleanup failures without hiding the remote handle.

- [ ] **Step 1: Write failing tests**

```python
def test_stale_remote_task_is_marked_cleanup_failed_and_keeps_handle(self):
    task = make_remote_task(last_heartbeat=old_timestamp, instance_id="uhost-1")
    watchdog.scan_once(now=now)
    self.assertEqual(task["cleanupState"], "cleanup_failed")
    self.assertEqual(task["instanceId"], "uhost-1")
```

```python
def test_phase_timings_include_download_and_release(self):
    self.assertIn("model_download", task["phaseTimings"])
    self.assertIn("release", task["phaseTimings"])
```

- [ ] **Step 2: Run tests to confirm failure**

Run: `python -m unittest backend.test_youyun_guarded_acceptance backend.test_postgres_pool -v`

- [ ] **Step 3: Implement watchdog and budget fields**

Set a max runtime at creation, update heartbeats per phase, scan queued/running/stopping tasks, and invoke stop/release when stale. Keep failed cleanup handles visible and block duplicate reruns until resolved.

- [ ] **Step 4: Add structured phase logging and metrics**

Persist redacted action, return code, instance ID, image ID, model Repo ID, retry count, phase start/end, price snapshot, and cleanup result. Never persist private keys or raw signed request bodies.

- [ ] **Step 5: Run backend tests**

Run: `python -m unittest discover -s backend -p "test_*.py" -v`

- [ ] **Step 6: Commit**

```bash
git add backend/app.py backend/vision_api.py backend/image_generation_training_runtime.py backend/inference_service.py backend/providers/youyun/instance_manager.py backend/test_youyun_guarded_acceptance.py backend/test_postgres_pool.py
git commit -m "feat: add Youyun budget watchdog and cleanup diagnostics"
```

### Task 5: 前端推荐展示与最终验收

**Files:**
- Modify: `components/vision-training-module.tsx`
- Modify: `components/image-generation-training.tsx`
- Modify: `components/image-generation-service.tsx`
- Modify: `app/globals.css`
- Test: `lib/vision-training-provider.test.mjs`
- Test: `lib/image-generation.test.mjs`
- Test: `lib/navigation-responsive.test.mjs`

**Interfaces:**
- Consumes recommendation payloads from Tasks 1-4.
- Displays one recommended combination, explicit blockers, probe state, price snapshot, estimated startup time, and cleanup state.

- [ ] **Step 1: Write failing UI assertions**

Assert that the visual and image-generation forms render `推荐组合`, `镜像 ID`, `基座 Repo ID`, `探针状态`, `预估价格`, and `释放状态`, and disable submit when a hard gate is blocked.

- [ ] **Step 2: Run UI tests to confirm failure**

Run: `node --test lib/vision-training-provider.test.mjs lib/image-generation.test.mjs lib/navigation-responsive.test.mjs`

- [ ] **Step 3: Implement compact recommendation panels**

Use existing panel styles; keep the submit button disabled for hard blockers, provide one-click refresh for stale inventory/price, and show phase progress without exposing raw API payloads.

- [ ] **Step 4: Run complete verification**

Run: `npx.cmd tsc --noEmit`; `node --test lib/*.test.mjs`; `npm.cmd run build`; `python -m unittest discover -s backend -p "test_*.py" -v`.

- [ ] **Step 5: Verify local services without creating an instance**

Check `http://127.0.0.1:8010/api/health`, `/api/vision/models?provider=youyun`, `/api/youyun/gpu-specs`, and `http://127.0.0.1:3100/`. Confirm CSS/JS resources return `200`, no duplicate listeners exist, and no new Youyun instance was created.

- [ ] **Step 6: Commit**

```bash
git add components/vision-training-module.tsx components/image-generation-training.tsx components/image-generation-service.tsx app/globals.css lib/vision-training-provider.test.mjs lib/image-generation.test.mjs lib/navigation-responsive.test.mjs
git commit -m "feat: expose reliable Youyun recommendations in UI"
```
