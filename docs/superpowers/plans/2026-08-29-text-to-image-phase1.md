# 文生图阶段一 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有文生图占位入口升级为可校验的数据集、模型目录和优云服务配置工作区，同时保持创建配置不启动 GPU 或优云实例。

**Architecture:** 在现有 FastAPI + SQLite 数据和 Next.js 单页工作区上增加文生图专用 API 层，但复用现有数据集生命周期、模型资产字段和优云资源查询客户端。前端按数据集、微调任务、模型管理、推理服务四个工作区分阶段呈现；阶段一只保存配置快照，阶段二再接入异步优云生成任务。

**Tech Stack:** FastAPI, Pydantic, SQLite/PostgreSQL-compatible stores, Next.js 14, React 18, TypeScript, existing Youyun provider client.

**Spec:** `docs/superpowers/specs/2026-08-29-text-to-image-phase1-design.md`

## Global Constraints

- 不在本机启动 Diffusers、ComfyUI 或文生图 GPU 服务。
- 不在阶段一创建真实优云实例。
- LoRA 是默认训练方式；QLoRA 只对通过镜像和模型兼容性验证的组合开放。
- 显式进程环境变量优先于本地配置文件；不得读取、提交或输出真实密钥。
- 保留现有工作区未提交修改，不执行 `git reset`、`git checkout --` 或覆盖无关文件。
- 创建服务只写入配置，不执行启动、实例创建或计费动作。

## File Map

- Create `backend/image_generation_api.py`: 文生图数据集摘要、模型目录聚合、服务配置 Pydantic 模型和 API 路由。
- Modify `backend/store.py`: 增加文生图数据集元数据/记录持久化，以及可按数据集类型读取的查询辅助。
- Modify `backend/app.py`: 注册文生图路由、处理图片批量上传、调用图片校验器和写入数据集生命周期。
- Create `backend/image_generation_validation.py`: 图片签名/解码、尺寸/大小限制、Caption 和 `metadata.jsonl` 规范化。
- Create `backend/test_image_generation_api.py`: API、校验、路径安全和“创建不启动”测试。
- Create `components/image-generation-module.tsx`: 文生图工作区壳、数据集/训练/模型/服务子视图和状态切换。
- Modify `app/page.tsx`: 将文生图三个 `DomainPlaceholder` 和数据集上传分支接入真实组件/API。
- Modify `app/globals.css`: 文生图批量图片列表、Caption 编辑、模型选择和优云配置的布局样式。
- Create `lib/image-generation.test.mjs`: 前端源码回归，覆盖工作区入口、LoRA/QLoRA、模型分组和配置只保存。

### Task 1: 图片数据集验证与规范化

**Files:**
- Create: `backend/image_generation_validation.py`
- Create: `backend/test_image_generation_api.py`
- Modify: `backend/app.py:2486-2520`
- Modify: `backend/store.py:466-500`

**Interfaces:**
- Produces `validate_image_upload(file_name: str, content: bytes) -> ImageAssetInspection`.
- Produces `normalize_image_dataset(records: list[ImageAssetInspection], captions: dict[str, str]) -> tuple[list[dict[str, str]], dict[str, Any]]`.
- `ImageAssetInspection` contains `file_name`, `mime_type`, `size_bytes`, `width`, `height`, and `sha256`.
- Normalized metadata records use `{ "file_name": "images/<safe-name>", "caption": "..." }`.

- [ ] **Step 1: Write failing tests**
  - Add tests for valid PNG/JPEG/WebP bytes, extension/signature mismatch, invalid bytes, 20MB boundary, 200-file limit, empty Caption, unsafe filename, and deterministic metadata ordering.
  - Add an API test that uploads a valid batch with `datasetType="文生图"` and expects `ready` plus `sampleCount` equal to the number of images.
- [ ] **Step 2: Run the focused tests and confirm failure**
  - Run `python -m unittest backend.test_image_generation_api -v`.
  - Expected failure: missing validation module or route.
- [ ] **Step 3: Implement the validation module**
  - Use Pillow only if already available; otherwise use the repository's existing image signature/decoder helper and return a clear dependency error.
  - Reject path separators, absolute paths, duplicate names, unsupported MIME/signature, files over 20MB, and batches over 200 files.
  - Generate captions from the sanitized stem only when a caption is not supplied; reject empty final captions.
- [ ] **Step 4: Wire persistence and upload handling**
  - Add a `datasetType="文生图"` branch that stores images under the existing dataset upload root, writes `metadata.jsonl`, and records validation summary without changing visual-understanding ZIP handling.
  - Keep published datasets immutable and preserve existing `ready`/`published` transitions.
- [ ] **Step 5: Run focused tests and commit the task**
  - Run `python -m unittest backend.test_image_generation_api -v`.
  - Expected: all image validation and upload tests pass.
  - Commit only the task files with `git add backend/image_generation_validation.py backend/test_image_generation_api.py backend/app.py backend/store.py && git commit -m "feat: add image generation dataset validation"`.

### Task 2: 文生图数据集前端工作区

**Files:**
- Create: `components/image-generation-module.tsx`
- Modify: `app/page.tsx:49-70, 497-610, 899-907`
- Modify: `app/globals.css`
- Modify: `lib/image-generation.test.mjs`

**Interfaces:**
- Consumes `GET /api/datasets?datasetType=文生图` and the upload response from Task 1.
- Produces a batch editor with `files`, `captionByFile`, validation state, and publish action.
- The component must not call any generation or training endpoint in stage one.

- [ ] **Step 1: Add failing source tests**
  - Assert the image-generation route renders a real component, accepts multiple image types, exposes Caption editing, and no longer contains the prototype-only upload rejection.
- [ ] **Step 2: Run the frontend test to confirm failure**
  - Run `node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --test lib/image-generation.test.mjs`.
- [ ] **Step 3: Implement the workspace**
  - Reuse the existing dataset list/upload patterns, but show thumbnail, filename, dimensions, bytes, Caption input, and row-level validation state.
  - Keep upload limits visible in the form and disable submit while processing.
  - Surface backend validation errors inline; do not silently discard invalid rows.
- [ ] **Step 4: Run frontend source tests and TypeScript**
  - Run `node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --test lib/image-generation.test.mjs` and `npx.cmd tsc --noEmit`.
- [ ] **Step 5: Commit the task**
  - Commit `app/page.tsx`, `components/image-generation-module.tsx`, `app/globals.css`, and `lib/image-generation.test.mjs` with `feat: add image generation dataset workspace`.

### Task 3: 文生图模型目录与 LoRA/QLoRA 兼容状态

**Files:**
- Modify: `backend/image_generation_api.py`
- Modify: `backend/store.py:764-980`
- Create: `components/image-generation-models.tsx`
- Modify: `app/page.tsx:902-904`
- Extend: `backend/test_image_generation_api.py`
- Extend: `lib/image-generation.test.mjs`

**Interfaces:**
- `GET /api/image-generation/models?provider=youyun` returns `{ id, label, group, repoId, modelType, taskId?, adapterVariant?, readyForYouyun, supportedTrainingModes, reason? }[]`.
- `supportedTrainingModes` is a subset of `["lora", "qlora"]`; an empty subset disables training selection.
- The API derives Adapter lineage from stored task/model records and never trusts a client-provided adapter path.

- [ ] **Step 1: Add failing model-catalog tests**
  - Cover base-model grouping, completed Best/Final Adapter grouping, lineage mismatch rejection, and QLoRA disabled when the model/image compatibility probe is absent.
- [ ] **Step 2: Implement catalog aggregation**
  - Reuse model asset synchronization and Youyun model/image selectors.
  - Mark LoRA as the default supported mode for verified candidates; expose QLoRA only when a compatibility flag is present in the server-side catalog.
- [ ] **Step 3: Implement the model-management view**
  - Show separate base and trained groups, Adapter variant, base Repo ID, remote readiness and training-mode badges.
  - Make QLoRA unavailable with an explicit reason instead of allowing a request that will fail remotely.
- [ ] **Step 4: Run focused tests and TypeScript**
  - Run backend image-generation tests, frontend image-generation tests, and `npx.cmd tsc --noEmit`.
- [ ] **Step 5: Commit the task**
  - Commit with `feat: add image generation model catalog`.

### Task 4: 优云服务配置快照

**Files:**
- Modify: `backend/image_generation_api.py`
- Modify: `backend/app.py` route registration area
- Create: `components/image-generation-service.tsx`
- Modify: `app/page.tsx:905-907`
- Modify: `app/globals.css`
- Extend: `backend/test_image_generation_api.py`
- Extend: `lib/image-generation.test.mjs`

**Interfaces:**
- `GET /api/image-generation/youyun/gpu-specs` returns region, zone, charge type, recommended inference image, machine types, and single-GPU capacity.
- `POST /api/image-generation/services` accepts `modelId`, optional `taskId`, optional `adapterVariant`, `trainingMode`, image parameters, and a resource snapshot; returns a `created` service record.
- The service endpoint must not call `create_instance`, `start_instance`, SSH, Diffusers, or ComfyUI.

- [ ] **Step 1: Add failing service tests**
  - Assert missing model/unsupported QLoRA/invalid dimensions/steps/CFG/resource mismatch return 4xx.
  - Patch the Youyun manager and assert service creation makes zero instance-creation calls.
- [ ] **Step 2: Implement Pydantic validation and route**
  - Validate width/height, total pixels, steps, CFG, batch size, seed, sampler, training mode, and resource snapshot.
  - Persist the complete configuration and source lineage in the existing service-compatible store or a dedicated stage-one table with an explicit type discriminator.
- [ ] **Step 3: Implement the create UI**
  - Add model selector, LoRA/QLoRA segmented control, prompt-generation defaults, dimensions, steps, CFG, seed, sampler, and Youyun GPU/image/price panel.
  - Display “配置已保存，启动阶段二实现” and keep the submit action separate from any future start action.
- [ ] **Step 4: Run focused tests and TypeScript**
  - Run the image-generation backend/frontend tests and `npx.cmd tsc --noEmit`.
- [ ] **Step 5: Commit the task**
  - Commit with `feat: add image generation youyun service configuration`.

### Task 5: 集成验收与文档

**Files:**
- Modify: `README.md` current scope and local testing notes
- Modify: `CURRENT_STATE.md` capability matrix and deferred scope
- Extend: `lib/image-generation.test.mjs`
- Extend: `backend/test_image_generation_api.py`

- [ ] **Step 1: Add end-to-end local contract tests**
  - Verify image upload -> validation -> publish -> model selection -> service configuration without GPU or instance creation.
- [ ] **Step 2: Run the complete focused verification**
  - Run `python -m unittest backend.test_image_generation_api`.
  - Run `node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --test lib/*.test.mjs`.
  - Run `python -m compileall -q backend scripts`, `npx.cmd tsc --noEmit`, and `git diff --check`.
- [ ] **Step 3: Browser acceptance**
  - Start only the local frontend/backend, verify the four workspaces and error states, and confirm no cloud instance API is called.
- [ ] **Step 4: Update current-state documentation**
  - Record implemented stage-one behavior and leave actual image generation, remote task execution, and QLoRA runtime validation listed as stage-two/three work.
- [ ] **Step 5: Commit the task**
  - Commit with `docs: record image generation phase one acceptance`.
