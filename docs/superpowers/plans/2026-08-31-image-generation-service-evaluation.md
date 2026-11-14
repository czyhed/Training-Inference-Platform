# Image Generation Service Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move image generation into each image-generation service detail and add service-scoped dataset validation, batch generation, CLIP evaluation, results, and diagnostics.

**Architecture:** Keep the existing image-generation service and task APIs compatible, but render generation through a service-scoped frontend component. Add a separate persistent evaluation runtime with an injectable executor, a local fake executor for tests, and a Youyun batch executor that uses one instance for the whole dataset. Expose evaluation dataset and task APIs through the existing image-generation router and render them as a new service detail tab.

**Tech Stack:** Next.js 14, React, TypeScript, FastAPI, Pydantic, Python asyncio, JSON persistence, Diffusers, Transformers CLIP, existing Youyun SSH/provider utilities, Node test runner, Python unittest.

**Spec:** `docs/superpowers/specs/2026-08-31-image-generation-service-evaluation-design.md`

## Global Constraints

- Do not start a real Youyun instance during implementation or local verification.
- Keep `POST /api/image-generation/tasks` and existing task lifecycle endpoints compatible.
- Evaluation datasets must be published `IMAGE_TEXT` datasets whose dataset type is `文生图`.
- Caption is the generation Prompt and the dataset image is the reference image.
- One evaluation task uses one Youyun instance for the full batch.
- CLIP metrics are auxiliary cosine-similarity metrics; the UI must not label them as an automatic pass/fail decision.
- Prefer `failureMessage` and `suggestedAction`; never expose raw remote commands, credentials, or tracebacks.
- Preserve unrelated workspace changes. The shared worktree is dirty, so do not create implementation commits unless the staged diff contains only this feature's files and the user explicitly requests a commit.

---

### Task 1: Service-Scoped Image Generation Workspace

**Files:**
- Modify: `components/image-generation-inference-workspace.tsx`
- Modify: `components/image-generation-service.tsx`
- Modify: `components/image-generation-tasks.tsx`
- Modify: `lib/image-generation.test.mjs`

**Interfaces:**
- Consumes: existing `/api/image-generation/tasks?serviceId=...` and task lifecycle endpoints.
- Produces: `ImageGenerationTasks({ serviceId, serviceName, defaultConfig, embedded })` and service-detail tab state `"overview" | "generation" | "evaluation" | "logs"`.

- [ ] **Step 1: Add a failing frontend contract test**

```js
test("generation is scoped to the selected image-generation service", async () => {
  const workspaceSource = await readFile(inferenceWorkspace, "utf8");
  const serviceSource = await readFile(service, "utf8");
  const taskSource = await readFile(new URL("../components/image-generation-tasks.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(workspaceSource, /生成任务<\/button>/);
  assert.match(serviceSource, /服务概览/);
  assert.match(serviceSource, /图片生成/);
  assert.match(serviceSource, /验证评测/);
  assert.match(serviceSource, /运行日志/);
  assert.match(taskSource, /serviceId: string/);
  assert.match(taskSource, /serviceId=\$\{encodeURIComponent\(serviceId\)\}/);
  assert.doesNotMatch(taskSource, /<option value="">请选择服务<\/option>/);
});
```

- [ ] **Step 2: Run the test and verify RED**

Run: `node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --test lib/image-generation.test.mjs`

Expected: FAIL because the top-level task tab and service selector still exist.

- [ ] **Step 3: Refactor the task component to require a service**

```tsx
type Props = {
  serviceId: string;
  serviceName: string;
  defaultConfig?: Record<string, unknown>;
  embedded?: boolean;
};

export function ImageGenerationTasks({ serviceId, serviceName, defaultConfig = {}, embedded = false }: Props) {
  // Load only `/image-generation/tasks?serviceId=${encodeURIComponent(serviceId)}`.
  // Create tasks with the prop serviceId and remove the service selector.
}
```

- [ ] **Step 4: Add service detail tabs and remove the top-level task tab**

`ImageGenerationInferenceWorkspace` should render only `ImageGenerationService`. `ImageGenerationService` should render the service header once and switch the detail body between overview, generation, evaluation placeholder, and logs.

- [ ] **Step 5: Run focused frontend tests and type checking**

Run:

```powershell
node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --test lib\image-generation.test.mjs
node .\node_modules\typescript\bin\tsc --noEmit
```

Expected: all image-generation frontend tests pass and TypeScript exits 0.

---

### Task 2: Evaluation Dataset Catalog and Validation

**Files:**
- Create: `backend/image_generation_evaluation.py`
- Create: `backend/test_image_generation_evaluation.py`
- Modify: `backend/image_generation_api.py`

**Interfaces:**
- Consumes: store dataset rows and their `paths.manifestPath` / `paths.mediaRoot` values.
- Produces: `list_image_generation_evaluation_datasets(store) -> list[dict[str, Any]]` and `validate_image_generation_evaluation_dataset(store, dataset_id, max_samples=None) -> dict[str, Any]`.

- [ ] **Step 1: Write failing dataset catalog tests**

```python
def test_catalog_only_lists_published_image_generation_datasets(self) -> None:
    items = list_image_generation_evaluation_datasets(self.store)
    self.assertEqual([item["id"] for item in items], ["ds-image-published"])

def test_validation_rejects_missing_caption_and_reference_image(self) -> None:
    result = validate_image_generation_evaluation_dataset(self.store, "ds-broken")
    self.assertFalse(result["valid"])
    self.assertEqual(result["missingCaptions"], 1)
    self.assertEqual(result["missingImages"], 1)
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `python -m unittest backend.test_image_generation_evaluation -q`

Expected: import failure because the evaluation module does not exist.

- [ ] **Step 3: Implement safe manifest inspection**

```python
def validate_image_generation_evaluation_dataset(
    store: Any,
    dataset_id: str,
    max_samples: int | None = None,
) -> dict[str, Any]:
    dataset = _published_image_generation_dataset(store, dataset_id)
    records = _read_manifest(Path(dataset["paths"]["manifestPath"]))
    selected = records[:max_samples] if max_samples else records
    # Resolve every file under mediaRoot, verify signatures, and return a serializable sample snapshot.
```

Use existing image signature helpers where possible. Reject traversal and paths outside `mediaRoot`.

- [ ] **Step 4: Add catalog and validate endpoints**

```python
@router.get("/api/image-generation/evaluation-datasets")
async def evaluation_datasets() -> list[dict[str, Any]]:
    return list_image_generation_evaluation_datasets(store)

@router.post("/api/image-generation/evaluations/validate")
async def validate_evaluation(payload: ImageGenerationEvaluationValidate) -> dict[str, Any]:
    return validate_image_generation_evaluation_dataset(store, payload.datasetId, payload.maxSamples)
```

- [ ] **Step 5: Run dataset and API tests**

Run:

```powershell
python -m unittest backend.test_image_generation_evaluation backend.test_image_generation_api -q
```

Expected: catalog, validation, traversal, missing image, and existing API tests pass.

---

### Task 3: Persistent Evaluation Runtime and Fake Executor

**Files:**
- Create: `backend/image_generation_evaluation_runtime.py`
- Create: `backend/image_generation_evaluation_executor.py`
- Create: `backend/test_image_generation_evaluation_runtime.py`

**Interfaces:**
- Consumes: evaluation task snapshots and `executor_factory(runtime)`.
- Produces: `ImageGenerationEvaluationRuntime`, `ImageGenerationEvaluationExecutor`, and `FakeImageGenerationEvaluationExecutor`.

- [ ] **Step 1: Write failing lifecycle tests**

```python
async def test_confirm_runs_all_samples_and_aggregates_metrics(self) -> None:
    task = self.runtime.create_task(self.task_payload(sample_count=2))
    await self.runtime.confirm(task["id"])
    completed = await self.wait_for_state(task["id"], "completed")
    self.assertEqual(completed["summary"]["completedSamples"], 2)
    self.assertEqual(completed["summary"]["successRate"], 1.0)
    self.assertAlmostEqual(completed["summary"]["clipTextImageMean"], 0.75)
```

Also cover restart interruption, single-sample failure, stop, retry, progress heartbeat, cleanup failure, and safe diagnostics.

- [ ] **Step 2: Run the runtime tests and verify RED**

Run: `python -m unittest backend.test_image_generation_evaluation_runtime -q`

Expected: import failure because runtime and executor classes do not exist.

- [ ] **Step 3: Implement the executor contract and fake executor**

```python
class ImageGenerationEvaluationExecutor:
    async def run(
        self,
        task: Mapping[str, Any],
        *,
        on_event: EventCallback | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

class FakeImageGenerationEvaluationExecutor(ImageGenerationEvaluationExecutor):
    # Write deterministic PNG fixtures and return deterministic per-sample scores.
```

- [ ] **Step 4: Implement JSON persistence and queue lifecycle**

Mirror the proven `ImageGenerationRuntime` lifecycle but keep evaluation tasks in `runtime/image-generation-evaluations`. Persist task JSON, dataset snapshot, results JSONL, logs, structured failure fields, remote handle, and cleanup state.

- [ ] **Step 5: Run runtime tests**

Run: `python -m unittest backend.test_image_generation_evaluation_runtime -q`

Expected: all lifecycle tests pass without network or GPU access.

---

### Task 4: Service-Scoped Evaluation API and Application Lifecycle

**Files:**
- Modify: `backend/image_generation_api.py`
- Modify: `backend/app.py`
- Modify: `backend/test_image_generation_api.py`

**Interfaces:**
- Consumes: `ImageGenerationEvaluationRuntime` and Task 2 dataset validation functions.
- Produces: nested service evaluation endpoints and app startup/shutdown integration.

- [ ] **Step 1: Add failing API tests**

```python
def test_evaluation_uses_service_id_from_url(self) -> None:
    response = self.client.post(
        "/api/image-generation/services/svc-image/evaluations",
        json={"datasetId": "ds-image", "maxSamples": 2},
    )
    self.assertEqual(response.status_code, 201)
    self.assertEqual(response.json()["serviceId"], "svc-image")

def test_evaluation_confirm_rechecks_dataset_and_service_preflight(self) -> None:
    response = self.client.post(f"/api/image-generation/services/svc-image/evaluations/{self.task_id}/confirm")
    self.assertEqual(response.status_code, 409)

def test_evaluation_result_images_cannot_escape_task_roots(self) -> None:
    response = self.client.get(
        f"/api/image-generation/services/svc-image/evaluations/{self.task_id}/samples/sample-1/generated"
    )
    self.assertEqual(response.status_code, 200)
```

- [ ] **Step 2: Run the API tests and verify RED**

Run: `python -m unittest backend.test_image_generation_api -q`

Expected: 404 for the new nested evaluation routes.

- [ ] **Step 3: Add request schemas and nested routes**

```python
class ImageGenerationEvaluationCreate(BaseModel):
    datasetId: str = Field(min_length=1, max_length=120)
    maxSamples: int | None = Field(default=None, ge=1, le=1000)
    width: int | None = Field(default=None, ge=256, le=2048)
    height: int | None = Field(default=None, ge=256, le=2048)
    steps: int | None = Field(default=None, ge=1, le=150)
    cfg: float | None = Field(default=None, ge=0, le=30)
    seed: int | None = None
    sampler: str | None = None
```

Create/list/detail/confirm/stop/retry/log routes must verify that both service and evaluation task belong to the URL service ID. Add controlled `reference` and `generated` sample-image routes that resolve only paths already stored in the evaluation snapshot/result and reject files outside the dataset media root or evaluation result root.

- [ ] **Step 4: Wire runtime startup, shutdown, busy checks, and executor factory**

Development and tests use `FakeImageGenerationEvaluationExecutor`. Production uses the Youyun executor from Task 5. Include evaluation runtime activity in GPU/Youyun busy reporting without blocking independent remote tasks unnecessarily.

- [ ] **Step 5: Run API and app import tests**

Run:

```powershell
python -m unittest backend.test_image_generation_api backend.test_app_cli -q
python -m compileall -q backend
```

Expected: new and existing routes pass and backend modules compile.

---

### Task 5: Youyun Batch Evaluation Executor and CLIP Scorer

**Files:**
- Create: `backend/providers/youyun/image_generation_evaluation.py`
- Create: `backend/test_youyun_image_generation_evaluation.py`
- Modify: `.env.example`
- Modify: `.env.production.example`

**Interfaces:**
- Consumes: task snapshots from `ImageGenerationEvaluationRuntime`, existing `YouyunInstanceManager`, `YouyunSSHClient`, and safe resource/model resolution patterns from `YouyunDiffusersProvider`.
- Produces: `YouyunImageGenerationEvaluationExecutor.run(...)` returning `status`, `results`, `summary`, `cleanupState`, and structured failure fields.

- [ ] **Step 1: Write failing remote executor tests with fake manager and SSH**

```python
async def test_one_instance_processes_the_complete_dataset(self) -> None:
    result = await self.executor.run(self.task_with_two_samples())
    self.assertEqual(self.manager.create_calls, 1)
    self.assertEqual(result["summary"]["completedSamples"], 2)
    self.assertEqual(self.manager.delete_calls, 1)
```

Also cover remote dependency probe, configured CLIP model, result download, stop, cleanup failure, and redacted failure messages.

- [ ] **Step 2: Run the executor tests and verify RED**

Run: `python -m unittest backend.test_youyun_image_generation_evaluation -q`

Expected: import failure because the executor does not exist.

- [ ] **Step 3: Implement a controlled remote batch worker**

The uploaded worker must accept only a generated JSON config and fixed paths. It loads Diffusers and CLIP once, iterates the snapshot samples, writes each generated image and JSONL result immediately, updates `status.json`, and never evaluates user-provided shell text.

```python
text_score = cosine(text_embedding(prompt), image_embedding(generated))
reference_score = cosine(image_embedding(reference), image_embedding(generated))
```

- [ ] **Step 4: Add environment configuration**

```dotenv
XUNTUI_IMAGE_EVAL_CLIP_MODEL=openai/clip-vit-base-patch32
XUNTUI_REMOTE_IMAGE_EVAL_INSTALL=0
XUNTUI_REMOTE_IMAGE_EVAL_PACKAGE=transformers pillow
```

The prebuilt image remains preferred. Installation is an explicit fallback and must use the selected remote Python environment.

- [ ] **Step 5: Run remote executor tests and related provider regression tests**

Run:

```powershell
python -m unittest backend.test_youyun_image_generation_evaluation backend.test_youyun_diffusers backend.test_image_generation_runtime -q
```

Expected: all tests pass without creating a real instance.

---

### Task 6: Service Evaluation UI

**Files:**
- Create: `components/image-generation-evaluation.tsx`
- Modify: `components/image-generation-service.tsx`
- Modify: `app/globals.css`
- Modify: `lib/image-generation.test.mjs`

**Interfaces:**
- Consumes: Task 4 evaluation dataset and nested evaluation task endpoints.
- Produces: `ImageGenerationEvaluation({ serviceId, serviceName, defaultConfig })`.

- [ ] **Step 1: Add failing frontend evaluation tests**

```js
test("service evaluation validates a published dataset before task creation", async () => {
  const source = await readFile(new URL("../components/image-generation-evaluation.tsx", import.meta.url), "utf8");
  assert.match(source, /image-generation\/evaluation-datasets/);
  assert.match(source, /image-generation\/evaluations\/validate/);
  assert.match(source, /services\/\$\{serviceId\}\/evaluations/);
  assert.match(source, /校验评测集/);
  assert.match(source, /创建待确认评测/);
  assert.match(source, /CLIP/);
  assert.match(source, /辅助自动指标/);
});
```

- [ ] **Step 2: Run the frontend tests and verify RED**

Run: `node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --test lib/image-generation.test.mjs`

Expected: file-not-found failure for the new component.

- [ ] **Step 3: Implement dataset validation and task creation views**

The create view lists only API-provided datasets, shows validation counts, keeps the create button disabled until `validation.valid === true`, and creates an `awaiting_confirmation` task without starting Youyun.

- [ ] **Step 4: Implement evaluation list and detail views**

Render aggregate cards, task stages, cleanup status, logs, and a responsive sample comparison grid containing Prompt, reference image, generated image, latency, text-image score, reference-image score, and safe failure message.

- [ ] **Step 5: Add responsive CSS and run frontend verification**

Run:

```powershell
node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --test lib\image-generation.test.mjs
node .\node_modules\typescript\bin\tsc --noEmit
```

Expected: tests and type checking pass.

---

### Task 7: Local End-to-End Verification

**Files:**
- Modify: `backend/test_image_generation_e2e.py`
- Modify: `CURRENT_STATE.md`

**Interfaces:**
- Consumes: all earlier tasks.
- Produces: repeatable evidence that service-scoped generation and evaluation work without real Youyun resources.

- [ ] **Step 1: Add an end-to-end fake-provider test**

```python
async def test_service_scoped_evaluation_e2e(self) -> None:
    # Create service -> validate dataset -> create evaluation -> confirm -> wait completed.
    # Assert two generated files, results.jsonl, aggregate metrics, and cleanupState == "completed".
```

- [ ] **Step 2: Run the complete focused backend suite**

Run:

```powershell
python -m unittest backend.test_image_generation_evaluation backend.test_image_generation_evaluation_runtime backend.test_image_generation_api backend.test_youyun_image_generation_evaluation backend.test_image_generation_e2e -q
```

Expected: all focused backend tests pass.

- [ ] **Step 3: Run frontend and static verification**

Run:

```powershell
node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --test lib\image-generation.test.mjs
node .\node_modules\typescript\bin\tsc --noEmit
python -m compileall -q backend
git diff --check
```

Expected: every command exits 0. Line-ending warnings are acceptable; whitespace errors are not.

- [ ] **Step 4: Verify the local UI in the browser**

Use `http://127.0.0.1:3001/` and the local backend. Verify desktop `1280x720` and mobile `390x844`:

- Service list has no top-level generation-task tab.
- Service detail has four tabs.
- Generation is bound to the selected service.
- Evaluation dataset validation works.
- Fake evaluation completes and renders aggregate/sample results.
- No full-page horizontal overflow or console errors.
- Do not click an action that starts a real Youyun instance.

- [ ] **Step 5: Update current-state documentation**

Record the new service-scoped generation/evaluation flow, API surface, metric definitions, test commands, and the fact that real Youyun acceptance remains a separate explicitly approved step.
