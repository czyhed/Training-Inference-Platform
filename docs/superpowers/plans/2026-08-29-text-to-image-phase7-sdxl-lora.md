# 文生图阶段七实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 SDXL LoRA 文生图微调、Adapter 发布、优云推理服务绑定与文生图推理的完整可验证闭环。

**Architecture:** 新增独立的文生图训练物化器、执行器、运行时和 API，复用视觉理解的状态机、预检、确认闸门、单 GPU 锁、优云资源解析和安全诊断约定。训练产物进入现有模型资产目录；推理服务仅保存基座/Adapter 配置，确认启动时再创建一次性优云实例。

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, asyncio, SQLite/现有 ModelStore, Diffusers 官方 SDXL LoRA 脚本, Next.js, React, TypeScript, lucide-react。

**Spec:** `docs/superpowers/specs/2026-08-29-text-to-image-phase7-sdxl-lora-design.md`

## Global Constraints

- 不在本机启动 GPU、Diffusers、SSH 或真实优云实例；本地验证只使用 Fake Provider、ASGI API 测试、TypeScript 和生产构建。
- 不重置工作区，不修改或删除用户未跟踪内容，不读取或提交 `.env`、访问密钥和 SSH 私钥。
- 首版仅承诺 SDXL + LoRA；QLoRA 只能由兼容性探针显式放行。
- 所有远端命令使用参数数组或受控字符串，不使用 `shell=True`；诊断优先 `failureMessage`，不暴露 `task.error` 原文、命令、凭据或远端绝对路径。
- 图片总像素不超过 `2_097_152`；训练任务只能使用已发布 `IMAGE_TEXT` 数据集。
- 预检快照默认有效 600 秒，确认时对过期或 fingerprint 变化快照强制重检。

### Task 1: 训练数据物化与 SDXL 配置契约

**Files:**
- Create: `backend/image_generation_training.py`
- Test: `backend/test_image_generation_training.py`

**Interfaces:**
- Consumes: 文生图数据集 `paths.mediaRoot` 与 `paths.manifestPath`、训练任务参数。
- Produces: `validate_image_generation_training_dataset(train_path: Path) -> int`、`resolve_sdxl_lora_entrypoint(repo_id: str, model_path: str = "") -> str`、`materialize_image_generation_training_bundle(...) -> dict[str, Any]`。

- [ ] **Step 1: Write failing tests for dataset validation and deterministic split**

```python
def test_materialize_caption_bundle_is_deterministic(tmp_path):
    source = make_image_dataset(tmp_path, ["a.png", "b.png", "c.png"])
    first = materialize_image_generation_training_bundle(
        dataset_id="ds-1", media_root=source / "images", manifest_path=source / "metadata.jsonl",
        destination=tmp_path / "one", model_repo_id="stabilityai/stable-diffusion-xl-base-1.0",
        resolution=1024, epochs=1, max_train_steps=3, learning_rate=1e-4,
        lora_rank=16, batch_size=1, gradient_accumulation=1, validation_split=33, seed=7,
    )
    second = materialize_image_generation_training_bundle(
        dataset_id="ds-1", media_root=source / "images", manifest_path=source / "metadata.jsonl",
        destination=tmp_path / "two", model_repo_id="stabilityai/stable-diffusion-xl-base-1.0",
        resolution=1024, epochs=1, max_train_steps=3, learning_rate=1e-4,
        lora_rank=16, batch_size=1, gradient_accumulation=1, validation_split=33, seed=7,
    )
    assert first["manifest"]["split"] == second["manifest"]["split"]
    assert (tmp_path / "one" / "train.jsonl").read_text() == (tmp_path / "two" / "train.jsonl").read_text()
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `python -m unittest backend.test_image_generation_training -v`
Expected: FAIL because the module and materializer do not exist.

- [ ] **Step 3: Implement safe validation, split, copy and manifest output**

Implement JSONL parsing for `{file_name, caption}` records, verify each file stays under `media_root`, validate image bytes with `validate_image_upload`, reject empty captions, duplicate names and non-SDXL Repo IDs, and write `train.jsonl`, optional `validation.jsonl`, `dataset_info.json`, `train_config.json`, `manifest.json`, `dataset/images/` and `output/`. Use `random.Random(seed)` and include source/manifest SHA-256 values.

- [ ] **Step 4: Run focused tests and compile check**

Run: `python -m unittest backend.test_image_generation_training -v` and `python -m py_compile backend/image_generation_training.py`
Expected: PASS; invalid paths, dimensions, rank and resolution are rejected with stable `ValueError` text.

- [ ] **Step 5: Commit**

```bash
git add backend/image_generation_training.py backend/test_image_generation_training.py
git commit -m "feat: materialize sdxl lora training bundles"
```

### Task 2: Fake/local executor and Adapter artifact contract

**Files:**
- Create: `backend/image_generation_executor.py`
- Test: `backend/test_image_generation_executor.py`

**Interfaces:**
- Consumes: Task 1 bundle; existing `TrainingEvent` shape from `backend/training_executor.py`.
- Produces: `ImageGenerationTrainingExecutor` protocol, `FakeImageGenerationTrainingExecutor`, `collect_image_generation_adapters(output_dir, manifest) -> list[dict[str, Any]]`, `build_sdxl_lora_command(config) -> list[str]`。

- [ ] **Step 1: Write failing tests for command safety and Best/Final collection**

```python
def test_command_contains_official_sdxl_lora_entrypoint_without_shell_text():
    args = build_sdxl_lora_command({"model_repo_id": "stabilityai/stable-diffusion-xl-base-1.0", "resolution": 1024})
    assert "train_text_to_image_lora_sdxl.py" in args
    assert all("&&" not in item and ";" not in item for item in args)

def test_collect_adapters_requires_loadable_weight(tmp_path):
    best = tmp_path / "best"; final = tmp_path / "final"
    best.mkdir(); final.mkdir()
    (best / "pytorch_lora_weights.safetensors").write_bytes(b"best")
    (final / "adapter_model.safetensors").write_bytes(b"final")
    artifacts = collect_image_generation_adapters(tmp_path, {"datasetId": "ds-1"})
    assert {item["variant"] for item in artifacts} == {"best", "final"}
```

- [ ] **Step 2: Run focused tests to verify failure**

Run: `python -m unittest backend.test_image_generation_executor -v`
Expected: FAIL because executor functions do not exist.

- [ ] **Step 3: Implement executor contract**

Use `async def run(task, on_event, on_log, on_handle, cancel_event)` and emit preparing/running/collecting events. Fake executor writes deterministic adapter files and metric events. Real command builder returns an argv list for the official SDXL LoRA script, pins `--pretrained_model_name_or_path`, `--train_data_dir`, `--output_dir`, resolution, epochs/max steps, learning rate, rank, batch and seed. Artifact collector accepts only known LoRA weight/config files, writes `artifact-manifest.json`, and marks `selected` Best when present, otherwise Final.

- [ ] **Step 4: Run focused tests**

Run: `python -m unittest backend.test_image_generation_executor -v`
Expected: PASS, including unsafe Repo ID, missing adapter, duplicate variant and cancellation cases.

- [ ] **Step 5: Commit**

```bash
git add backend/image_generation_executor.py backend/test_image_generation_executor.py
git commit -m "feat: add sdxl lora executor and adapter artifacts"
```

### Task 3: Persistent training runtime and lifecycle

**Files:**
- Create: `backend/image_generation_training_runtime.py`
- Test: `backend/test_image_generation_training_runtime.py`

**Interfaces:**
- Consumes: Task 1 bundle and Task 2 executor; shared `asyncio.Lock` and optional remote runner factory.
- Produces: `ImageGenerationTrainingRuntime.create_task`, `confirm`, `request_stop`, `retry`, `clone_task`, `archive_task`, `restore_task`, `delete_task`, `list_tasks`, `get`, `diagnostics`, `start`, `stop`。

- [ ] **Step 1: Write failing lifecycle tests**

Cover awaiting-confirmation creation, confirm-to-queued, Fake completion with artifacts, stop before execution, retry from failed, cleanup failure blocking rerun, archived-action 409 semantics, and restart recovery to `runtime_interrupted`.

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m unittest backend.test_image_generation_training_runtime -v`
Expected: FAIL because runtime module does not exist.

- [ ] **Step 3: Implement persistent JSON task store and worker loop**

Persist tasks under `runtime_root/image-generation-training-tasks/*.json`. Mirror visual runtime field aliases and telemetry; clamp progress to 0-99 while running and set 100 only on completed. Never copy results/logs/handles when cloning. Use the shared lock and `busy_check`; reject retries when `cleanupState == "failed"`; preserve primary failure classification when cleanup also fails.

- [ ] **Step 4: Run focused runtime tests**

Run: `python -m unittest backend.test_image_generation_training_runtime -v`
Expected: PASS with no real subprocess, SSH or GPU calls.

- [ ] **Step 5: Commit**

```bash
git add backend/image_generation_training_runtime.py backend/test_image_generation_training_runtime.py
git commit -m "feat: add image generation training runtime"
```

### Task 4: Training API, preflight and app wiring

**Files:**
- Create: `backend/image_generation_training_api.py`
- Modify: `backend/app.py`
- Modify: `backend/image_generation_api.py`
- Test: `backend/test_image_generation_training_api.py`

**Interfaces:**
- Consumes: store datasets/model assets, Task 1-3 runtime, existing `resolve_youyun_resource`, `youyun_gpu_specs`, price and image checks.
- Produces: all `/api/image-generation/training/*` routes from the design; image-generation catalog entries for completed SDXL adapters.

- [ ] **Step 1: Write failing API tests**

Test published `IMAGE_TEXT` filtering, verified SDXL base model filtering, preflight fingerprint/TTL, price/capacity blockers, materialize preview, confirm queueing, diagnostics safe message, archive gates, and catalog visibility after Fake completion.

- [ ] **Step 2: Run focused API tests to verify failure**

Run: `python -m unittest backend.test_image_generation_training_api -v`
Expected: FAIL because router and app wiring do not exist.

- [ ] **Step 3: Implement Pydantic payloads and router**

Use strict `extra="forbid"` models. Preflight returns `{ready, checks, reasons, fingerprint, checkedAt, expiresAtEpoch, youyunResource}` and stores it in the task snapshot. Confirm re-runs preflight when stale or changed. Materialize preview never enqueues. Task creation stores model/dataset/resource snapshot and only queues after confirm.

- [ ] **Step 4: Wire runtime into `backend/app.py` lifespan and shared lock**

Instantiate `image_generation_training_runtime` beside visual/image-generation runtimes, include the router before generic routes, and add its active task ID to `training_busy` checks. Build a Youyun training provider factory that receives the task snapshot and uses a dedicated SDXL LoRA image policy; default to prevalidated image and only allow download/install fallback behind explicit env flags.

- [ ] **Step 5: Extend model catalog and service binding**

Expose completed Best/Final adapter assets with `baseModelId`, `adapterVariant`, `adapterPath`, lineage and readiness fields. Update service resolution so Adapter entries retain their base Repo and variant; reject missing or archived lineage at task creation.

- [ ] **Step 6: Run focused API tests and compile**

Run: `python -m unittest backend.test_image_generation_training_api backend.test_image_generation_runtime backend.test_image_generation_api -v` and `python -m py_compile backend/image_generation_training_api.py backend/app.py backend/image_generation_api.py`
Expected: PASS; existing image-generation tests remain green.

- [ ] **Step 7: Commit**

```bash
git add backend/image_generation_training_api.py backend/app.py backend/image_generation_api.py backend/test_image_generation_training_api.py
git commit -m "feat: expose image generation training APIs"
```

### Task 5: Adapter synchronization into model management

**Files:**
- Modify: `backend/store.py`
- Modify: `backend/app.py`
- Modify: `backend/model_storage.py`
- Test: `backend/test_image_generation_model_sync.py`

**Interfaces:**
- Consumes: runtime task artifact manifests and completed task snapshots.
- Produces: idempotent `sync_image_generation_adapters(task)` used after completion/stop and model-management list entries.

- [ ] **Step 1: Write failing synchronization tests**

Assert Best/Final rows are created with stable IDs, base lineage, source task, variant, loss/step and manifest hash; rerunning synchronization does not duplicate rows; missing files remain `failed` and are not deployable.

- [ ] **Step 2: Run focused tests to verify failure**

Run: `python -m unittest backend.test_image_generation_model_sync -v`
Expected: FAIL because synchronization function does not exist.

- [ ] **Step 3: Implement idempotent local catalog update and optional shared upload**

Reuse existing model asset serialization and uploader ordering. Do not upload intermediate checkpoints or merged models. Set status `available` only after directory and manifest validation; retain `pending_upload`/`failed` state otherwise.

- [ ] **Step 4: Run model and storage regression tests**

Run: `python -m unittest backend.test_image_generation_model_sync backend.test_model_storage backend.test_inference_service -v`
Expected: PASS without network credentials.

- [ ] **Step 5: Commit**

```bash
git add backend/store.py backend/app.py backend/model_storage.py backend/test_image_generation_model_sync.py
git commit -m "feat: sync image generation adapters into model catalog"
```

### Task 6: SDXL LoRA micro-tuning UI and four-workspace navigation

**Files:**
- Create: `components/image-generation-training.tsx`
- Modify: `components/image-generation-module.tsx`
- Modify: `components/image-generation-models.tsx`
- Modify: `components/image-generation-service.tsx`
- Modify: `app/page.tsx`
- Modify: `app/globals.css`
- Test: `components/image-generation-training.test.tsx` or existing frontend source-contract test location

**Interfaces:**
- Consumes: Task 4 API payloads and Task 5 catalog fields; follows `components/vision-training-module.tsx` layout/state conventions.
- Produces: dataset, training, models and inference-service workspaces with consistent tabs, preflight panel, confirmation modal, task detail, adapter cards and disabled states.

- [ ] **Step 1: Write failing frontend contract tests**

Assert the form renders dataset/model selectors, SDXL-only model gating, LoRA controls, resource/price/preflight states, confirmation action, Best/Final artifact labels, and no “创建实例” action before confirmation.

- [ ] **Step 2: Run frontend focused test to verify failure**

Run: `npm test -- --runInBand image-generation-training`
Expected: FAIL because the component and route are absent.

- [ ] **Step 3: Implement training workspace**

Mirror visual understanding's section navigation and summary sidebar. Load published `IMAGE_TEXT` datasets and ready SDXL models, reset stale preflight on any configuration edit, show checks/reasons, require a successful preflight before opening the confirmation modal, and submit only after explicit confirmation. Poll task detail/logs/metrics while active and show safe diagnostics using `failure.message` first.

- [ ] **Step 4: Integrate four-workspace navigation and model/service selectors**

Keep existing dataset upload and history behavior. Add trained Adapter group to model view, show base Repo and variant lineage, allow service creation from base or Adapter, and preserve service history/result panels. Ensure archived services/tasks cannot expose start or retry controls.

- [ ] **Step 5: Add responsive styles and accessibility labels**

Use existing panel/grid tokens, stable input dimensions, icon buttons with tooltips, no nested cards, and no horizontal overflow at desktop/mobile widths.

- [ ] **Step 6: Run TypeScript, frontend tests and build**

Run: `npx tsc --noEmit`, `npm test -- --runInBand`, `npm run build`
Expected: PASS; existing 39 frontend tests plus new training contracts pass.

- [ ] **Step 7: Commit**

```bash
git add components/image-generation-training.tsx components/image-generation-module.tsx components/image-generation-models.tsx components/image-generation-service.tsx app/page.tsx app/globals.css components/*image-generation*test*
git commit -m "feat: add image generation lora training workspace"
```

### Task 7: End-to-end Fake acceptance and regression verification

**Files:**
- Create: `backend/test_image_generation_e2e.py`
- Modify: `README.md`
- Modify: `CURRENT_STATE.md`

- [ ] **Step 1: Write failing Fake acceptance test**

Exercise upload/published dataset fixture → preflight → materialize → confirm → Fake training completion → model catalog Best Adapter → service creation → existing Fake image generation provider → PNG download. Assert no Youyun client or real subprocess was called.

- [ ] **Step 2: Run the test to verify failure**

Run: `python -m unittest backend.test_image_generation_e2e -v`
Expected: FAIL until all previous tasks are wired.

- [ ] **Step 3: Implement only missing glue discovered by the test**

Keep fixes scoped to route registration, snapshot field names, artifact lineage and service model resolution; do not broaden the feature.

- [ ] **Step 4: Run full local verification**

Run: `python -m unittest discover -s backend -p "test_*.py" -v`, `python -m compileall backend`, `npx tsc --noEmit`, `npm test -- --runInBand`, `git diff --check`, `npm run build`
Expected: all backend/frontend tests pass; no GPU, Diffusers, SSH or Youyun process starts.

- [ ] **Step 5: Commit docs and verification**

```bash
git add backend/test_image_generation_e2e.py README.md CURRENT_STATE.md
git commit -m "test: verify image generation finetune inference flow"
```

### Task 8: Authorized real Youyun acceptance

**Files:**
- No source changes unless a verified compatibility defect is found.
- Evidence: local untracked acceptance report under `.scratch/` only; never include credentials.

- [ ] **Step 1: Verify environment flags without printing secrets**

Check only presence/boolean state of Youyun credentials, selected SDXL image ID, remote Python policy, and download/install fallback flags.

- [ ] **Step 2: Create the smallest published IMAGE_TEXT dataset fixture**

Use 2-3 images, resolution 512 or lower where accepted, one epoch or max 2 steps, batch size 1 and LoRA rank 4/8 according to the image's minimum requirements.

- [ ] **Step 3: Run one confirmed Youyun training task**

Observe preflight, instance creation, SSH/runtime readiness, training metrics, Best/Final Adapter download and cleanup. Stop immediately on a cleanup failure and retry cleanup before any new run.

- [ ] **Step 4: Bind Adapter to a Youyun inference service and generate one image**

Run service preflight, confirm/start, create one task, download PNG, verify dimensions and record safe task/service IDs.

- [ ] **Step 5: Stop/delete all instances and record outcome**

Confirm no active instances remain via the provider's read-only listing. Update `CURRENT_STATE.md` only with safe summary and known residual risks.

