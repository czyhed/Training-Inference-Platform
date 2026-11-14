# 优云视觉社区镜像与小型基模接入实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将社区镜像和 2B/3B/4B/7B/8B 视觉基模接入视觉理解选择、探针和小样本验收流程，并保证优云实例可回收。

**Architecture:** 保留现有 `youyun_vision_catalog.py` 的目录解析边界，新增社区镜像能力模型和结构化远端探针层；`vision_api.py` 负责只读目录、探针任务和报告，`vision_runtime.py` 只接受探针通过的组合。前端复用现有视觉模型管理和训练模块，按模型梯度显示资源建议与阻塞原因。正式优云训练仍由现有 runner 执行，所有退出路径统一调用实例释放。

**Tech Stack:** FastAPI、Pydantic、Python unittest、React/TypeScript、Node test、现有 Youyun client/instance manager/SSH。

**Spec:** `docs/superpowers/specs/2026-09-02-youyun-vision-model-catalog-design.md`

## Global Constraints

- 默认首验顺序为 `Qwen2.5-VL-3B + QLoRA`，失败后使用 `Qwen2-VL-2B`。
- `Qwen3-VL-2B/4B` 必须通过 Qwen3 模板和 Processor 探针后才能训练。
- `LLaMAFactory-WebUI` 是首选社区镜像；`LLaMA-Factory-YingHuoAI` 未验证前只能作为备选。
- 未经探针通过不得显示“可训练”，不得创建正式付费训练实例。
- 优云实例在成功、失败、停止、超时和连接中断路径都必须释放；释放失败保留句柄并阻止重复启动。
- 不修改视觉数据集格式，不自动下载大型模型，不触碰工作区其他已有修改。

---

### Task 1: 扩展视觉模型与社区镜像数据契约

**Files:**
- Modify: `backend/youyun_vision_catalog.py`
- Modify: `data/youyun-vision-catalog.json`
- Modify: `lib/vision-base-model-presets.ts`
- Test: `backend/test_youyun_vision_catalog.py`
- Test: `lib/vision-base-model-presets.test.mjs`

**Interfaces:**
- Produces `parse_youyun_vision_catalog(raw) -> list[dict]` entries with `repoId`, `template`, `recommendedVramGb`, `capabilities`, `source`, and optional `imageId`/`imageName`/`imageStatus`.
- Produces `VISION_BASE_MODEL_PRESETS` entries for Qwen2-VL-2B, Qwen2.5-VL-3B, Qwen3-VL-2B/4B, 7B and 8B with explicit `recommended` and `youyunAvailability` values.

- [x] **Step 1: Write failing catalog tests**

```python
def test_keeps_small_qwen_vl_models_and_assigns_lower_vram(self):
    rows = parse_youyun_vision_catalog([
        {"name": "Qwen2-VL-2B-Instruct", "path": "/model/ModelScope/Qwen/Qwen2-VL-2B-Instruct", "sizeGb": 5},
        {"name": "Qwen2.5-VL-3B-Instruct", "path": "/model/ModelScope/Qwen/Qwen2.5-VL-3B-Instruct", "sizeGb": 7},
    ])
    self.assertEqual([item["repoId"] for item in rows], [
        "Qwen/Qwen2-VL-2B-Instruct", "Qwen/Qwen2.5-VL-3B-Instruct",
    ])
    self.assertEqual(rows[0]["recommendedVramGb"], 16)
    self.assertEqual(rows[1]["recommendedVramGb"], 16)

def test_community_image_metadata_is_preserved(self):
    rows = parse_youyun_vision_catalog([{
        "name": "Qwen2.5-VL-3B-Instruct",
        "repoId": "Qwen/Qwen2.5-VL-3B-Instruct",
        "path": "/model/ModelScope/Qwen/Qwen2.5-VL-3B-Instruct",
        "imageId": "image-lf-webui",
        "imageName": "LLaMAFactory-WebUI",
    }])
    self.assertEqual(rows[0]["imageName"], "LLaMAFactory-WebUI")
```

- [x] **Step 2: Run focused tests and verify the new assertions fail**

Run: `python -m unittest backend.test_youyun_vision_catalog -v` and `npm run test:frontend -- lib/vision-base-model-presets.test.mjs`

Expected: the new metadata and small-model expectations fail before implementation.

- [x] **Step 3: Implement catalog and preset changes**

Update `_recommended_vram` so models below 12GB map to 16GB, preserve community image fields, and keep existing non-visual filtering. Add the small-model preset records while keeping exactly one default recommendation (`Qwen2.5-VL-3B`).

- [x] **Step 4: Run tests and commit**

Run: `python -m unittest backend.test_youyun_vision_catalog -v` and `npm run test:frontend -- lib/vision-base-model-presets.test.mjs`

Expected: PASS.

```bash
git add backend/youyun_vision_catalog.py data/youyun-vision-catalog.json lib/vision-base-model-presets.ts backend/test_youyun_vision_catalog.py lib/vision-base-model-presets.test.mjs
git commit -m "feat: add small vision model recommendations"
```

### Task 2: Build the structured remote compatibility probe

**Files:**
- Create: `backend/youyun_vision_probe.py`
- Modify: `backend/providers/youyun/ssh.py` only if a reusable command helper is required
- Test: `backend/test_youyun_vision_probe.py`

**Interfaces:**
- `VisionProbeRequest(repo_id: str, model_path: str, template: str, tuning_mode: str, image_path: str | None)`.
- `VisionProbeResult.to_dict() -> dict` with `passed`, `checks`, `blockingReason`, `recommendedTuningMode`, `estimatedVramGb`, `logs`.
- `async probe_remote_vision_environment(ssh, request) -> VisionProbeResult`.

- [x] **Step 1: Write failing unit tests with a fake SSH client**

```python
def test_probe_reports_missing_dependency_without_claiming_passed(self):
    result = run_probe_with_output({"python": 0, "torch": 0, "dependencies": 1})
    self.assertFalse(result.passed)
    self.assertEqual(result.blockingReason, "dependencies_missing")
    self.assertEqual(result.checks["dependencies"]["status"], "failed")

def test_probe_accepts_small_qwen_vl_when_processor_and_lora_pass(self):
    result = run_probe_with_output({"python": 0, "torch": 0, "dependencies": 0, "processor": 0, "lora": 0, "forward": 0})
    self.assertTrue(result.passed)
    self.assertEqual(result.recommendedTuningMode, "qlora")
```

- [x] **Step 2: Run the probe tests and verify they fail**

Run: `python -m unittest backend.test_youyun_vision_probe -v`

Expected: FAIL because the probe module and result contract do not exist.

- [x] **Step 3: Implement bounded probe commands**

Use non-shell-composed, quoted commands to collect Python/Torch/CUDA/GPU versions, import `transformers`, `peft`, `bitsandbytes`, and `llamafactory`, load Config/Tokenizer/Processor, resolve the Qwen template, inject LoRA/QLoRA, and run one image forward/backward. Convert each command result into a named check and stop at the first blocking check while retaining logs.

- [x] **Step 4: Run probe tests and commit**

Run: `python -m unittest backend.test_youyun_vision_probe -v`

Expected: PASS with deterministic fake SSH output.

```bash
git add backend/youyun_vision_probe.py backend/test_youyun_vision_probe.py backend/providers/youyun/ssh.py
git commit -m "feat: add structured vision environment probe"
```

### Task 3: Expose catalog and probe APIs with training gates

**Files:**
- Modify: `backend/vision_api.py`
- Modify: `backend/app.py`
- Modify: `backend/vision_runtime.py`
- Test: `backend/test_vision_api.py`
- Test: `backend/test_vision_runtime.py`

**Interfaces:**
- `GET /api/vision/youyun-catalog` returns small and large model entries plus image metadata.
- `POST /api/vision/models/{model_id}/remote-probe` returns the serialized `VisionProbeResult`.
- `GET /api/vision/models/{model_id}/compatibility` includes probe state, blocking reason, recommended tuning mode, and estimated VRAM.
- Training preflight rejects a Youyun model unless `probeStatus == "passed"` and the selected image is compatible.

- [x] **Step 1: Add failing API tests**

```python
def test_catalog_exposes_small_model_first(self):
    response = self.client.get("/api/vision/youyun-catalog")
    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.json()[0]["repoId"], "Qwen/Qwen2.5-VL-3B-Instruct")
    self.assertEqual(response.json()[0]["recommendedVramGb"], 16)

def test_preflight_blocks_unprobed_remote_model(self):
    response = self.client.post("/api/vision/tasks/preflight", json={"datasetId": "vision-train", "modelId": "vision-unprobed", "provider": "youyun"})
    self.assertEqual(response.status_code, 400)
    self.assertIn("探针", response.json()["detail"])
```

- [x] **Step 2: Run focused API tests and verify they fail**

Run: `python -m unittest backend.test_vision_api backend.test_vision_runtime -v`

Expected: FAIL on the new route and gate assertions.

- [x] **Step 3: Implement routes and gate integration**

Keep catalog reads side-effect free. Persist only the latest probe status/report on the model asset or task metadata already used by the visual flow. Pass the selected image ID and model path into the probe factory. Make preflight return a structured blocking reason instead of a generic validation error.

- [x] **Step 4: Run focused tests and commit**

Run: `python -m unittest backend.test_vision_api backend.test_vision_runtime -v`

Expected: PASS.

```bash
git add backend/vision_api.py backend/app.py backend/vision_runtime.py backend/test_vision_api.py backend/test_vision_runtime.py
git commit -m "feat: gate vision training on remote probe"
```

### Task 4: Add model/image status to the visual UI

**Files:**
- Modify: `components/vision-model-management-view.tsx`
- Modify: `components/vision-training-module.tsx`
- Modify: `components/vision-inference-service-module.tsx`
- Modify: `lib/types.ts`
- Test: `lib/vision-model-management-view.test.mjs`
- Test: `lib/vision-training-provider.test.mjs`
- Test: `lib/inference-vision.test.mjs`

**Interfaces:**
- UI model option fields: `parameterScale`, `recommendedVramGb`, `imageName`, `probeStatus`, `probeMessage`, `recommendedTuningMode`.
- UI actions call `/api/vision/models/{id}/remote-probe` and disable training until the response reports `passed`.

- [x] **Step 1: Add failing source-contract tests**

```javascript
test("visual model UI shows small-model resource guidance and probe state", async () => {
  const source = await readFile(new URL("../components/vision-model-management-view.tsx", import.meta.url), "utf8");
  assert.match(source, /recommendedVramGb/);
  assert.match(source, /remote-probe/);
  assert.match(source, /LLaMAFactory-WebUI/);
});
```

- [x] **Step 2: Run frontend tests and verify the new assertions fail**

Run: `npm run test:frontend -- lib/vision-model-management-view.test.mjs lib/vision-training-provider.test.mjs lib/inference-vision.test.mjs`

- [x] **Step 3: Implement the status-aware UI**

Group models by parameter scale, put the 3B recommendation first, show 2B fallback and 4B/7B/8B alternatives, show image status and VRAM guidance, and render a blocking message when a model is unprobed, incompatible, or under-provisioned. Keep inference selection separate from training eligibility.

- [x] **Step 4: Run frontend tests, TypeScript check, and commit**

Run: `npm run test:frontend -- lib/vision-model-management-view.test.mjs lib/vision-training-provider.test.mjs lib/inference-vision.test.mjs` and `cmd /d /c "npx.cmd tsc --noEmit"`

Expected: PASS.

```bash
git add components/vision-model-management-view.tsx components/vision-training-module.tsx components/vision-inference-service-module.tsx lib/types.ts lib/vision-model-management-view.test.mjs lib/vision-training-provider.test.mjs lib/inference-vision.test.mjs
git commit -m "feat: show vision model probe readiness"
```

### Task 5: Add guarded Youyun small-sample acceptance and cleanup verification

**Files:**
- Create: `backend/youyun_vision_acceptance.py`
- Modify: `backend/app.py`
- Modify: `backend/providers/youyun/instance_manager.py` only for idempotent release/query helpers
- Test: `backend/test_youyun_guarded_acceptance.py`
- Test: `backend/test_youyun_instance_manager.py`

**Interfaces:**
- `async run_guarded_vision_acceptance(model, image, dataset, budget) -> dict`.
- Result fields: `phase`, `probe`, `training`, `artifacts`, `cleanupState`, `cleanupError`, `remoteInstanceId`.

- [x] **Step 1: Write failing lifecycle tests**

```python
def test_failed_acceptance_releases_instance_and_reports_cleanup(self):
    result = await run_acceptance(fake_probe_pass=True, fake_training_error="oom", fake_release=True)
    self.assertEqual(result["phase"], "training")
    self.assertEqual(result["cleanupState"], "released")

def test_cleanup_failure_blocks_repeat_start(self):
    result = await run_acceptance(fake_probe_pass=True, fake_training_error="ssh", fake_release=False)
    self.assertEqual(result["cleanupState"], "failed")
    self.assertTrue(result["retryBlocked"])
```

- [x] **Step 2: Run lifecycle tests and verify they fail**

Run: `python -m unittest backend.test_youyun_guarded_acceptance backend.test_youyun_instance_manager -v`

- [x] **Step 3: Implement guarded acceptance**

Use a single acceptance lock, enforce an explicit budget, create only after inventory/price resolution, run the remote probe, materialize at most 10 train and 5 validation samples, collect Adapter/Best/Final/Loss/log manifest, and execute idempotent release in `finally`. Preserve the remote handle on release failure and reject a new acceptance while a handle is unresolved.

- [x] **Step 4: Run lifecycle tests and commit**

Run: `python -m unittest backend.test_youyun_guarded_acceptance backend.test_youyun_instance_manager -v`

Expected: PASS.

```bash
git add backend/youyun_vision_acceptance.py backend/app.py backend/providers/youyun/instance_manager.py backend/test_youyun_guarded_acceptance.py backend/test_youyun_instance_manager.py
git commit -m "feat: guard Youyun vision acceptance and cleanup"
```

### Task 6: Full verification and controlled remote acceptance

**Files:**
- Modify: `docs/superpowers/plans/2026-09-02-youyun-vision-small-models.md` to check completed steps
- Modify: `CURRENT_STATE.md` only after acceptance evidence exists

- [x] **Step 1: Run backend focused suite**

Run: `python -m unittest backend.test_youyun_vision_catalog backend.test_youyun_vision_probe backend.test_vision_api backend.test_vision_runtime backend.test_youyun_guarded_acceptance -v`

- [x] **Step 2: Run frontend and TypeScript verification**

Run: `npm run test:frontend` and `cmd /d /c "npx.cmd tsc --noEmit"`.

- [x] **Step 3: Perform local no-instance verification**

Query `GET http://127.0.0.1:8010/api/vision/youyun-catalog` and verify the 3B recommendation, 2B fallback, image status and probe fields. Do not create a paid instance in this step.

- [ ] **Step 4: Request explicit remote acceptance confirmation**

Before creating an instance, display the selected model, image ID, GPU type, estimated price, maximum budget, sample counts and cleanup policy. Create one instance only after confirmation.

- [ ] **Step 5: Run remote acceptance and verify zero residual instances**

Execute the probe and small-sample run for `Qwen2.5-VL-3B`; if it fails, run the `Qwen2-VL-2B` fallback. Release the instance and query the provider until it is absent or released. Record probe report, task logs, Loss, Adapter paths, cleanup result and provider instance count.

- [ ] **Step 6: Update durable status and commit evidence**

Update `CURRENT_STATE.md` with only observed results, limitations and exact model/image status. Commit the plan checklist and status evidence separately.

```bash
git add docs/superpowers/plans/2026-09-02-youyun-vision-small-models.md CURRENT_STATE.md
git commit -m "docs: record vision model acceptance evidence"
```
