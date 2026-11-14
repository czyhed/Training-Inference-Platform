# Vision Model Management Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor visual-understanding model management into a compact asset workspace with truthful local-training, Youyun-training, and inference-deployment readiness plus a separate model catalog drawer.

**Architecture:** The backend enriches the existing vision-model list with `availableOnYouyun`; a focused TypeScript helper module owns catalog matching, readiness derivation, aggregation, and filtering. `VisionModelManagementView` consumes those helpers and renders the status strip, asset table/mobile list, detail drawer, catalog drawer, and compact registration modal without changing model persistence or training/inference workflows.

**Tech Stack:** FastAPI, Python unittest, Next.js 14, React 18, TypeScript, lucide-react, Node test runner, CSS.

**Spec:** `docs/superpowers/specs/2026-08-27-vision-model-management-redesign-design.md`

## Global Constraints

- Preserve unrelated dirty-worktree changes and stage only files belonging to this feature.
- Do not start GPU training or a vLLM service.
- Keep existing `provider=local` and `provider=youyun` filtering behavior.
- Use backend `is_youyun_model_available` as the source of truth for remote readiness.
- Keep cards at 6px radius or less and use the existing restrained operational palette.
- Use lucide-react icons for new controls and provide accessible labels/tooltips for icon-only buttons.
- Do not add cross-page navigation, model deletion, archive, rename, upload, or new database tables.

---

### Task 1: Expose truthful Youyun availability

**Files:**
- Modify: `backend/test_vision_api.py:180-205`
- Modify: `backend/vision_api.py:346-363`

**Interfaces:**
- Produces: every item returned by `GET /api/vision/models` includes `availableOnYouyun: bool`.
- Preserves: `GET /api/vision/models?provider=local` returns only locally probed models; `provider=youyun` returns only remotely configured models.

- [ ] **Step 1: Write the failing API test**

Extend `test_vision_model_list_is_scoped_to_selected_provider` to request the unfiltered list inside the patched environment and assert:

```python
all_models = self.client.get("/api/vision/models")
self.assertEqual(all_models.status_code, 200, all_models.text)
availability = {
    model["id"]: model["availableOnYouyun"]
    for model in all_models.json()
}
self.assertFalse(availability[local_id])
self.assertTrue(availability[remote_id])
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python -m unittest backend.test_vision_api.VisionApiTest.test_vision_model_list_is_scoped_to_selected_provider`

Expected: failure because the unfiltered local model object has no `availableOnYouyun` key.

- [ ] **Step 3: Enrich list results once, then filter**

Change `list_vision_models` to build:

```python
models = [
    {**model, "availableOnYouyun": is_youyun_model_available(model)}
    for model in store.list_vision_models()
]
```

Return this list by default. Keep local filtering based on `supported` and `probeStatus`; keep Youyun filtering based on the enriched boolean.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run: `python -m unittest backend.test_vision_api.VisionApiTest.test_vision_model_list_is_scoped_to_selected_provider`

Expected: one test passes.

### Task 2: Add tested model-management derivation helpers

**Files:**
- Create: `lib/vision-model-management.ts`
- Create: `lib/vision-model-management.test.mjs`
- Modify: `lib/vision-base-model-presets.ts`

**Interfaces:**
- Consumes: `VisionBaseModelPreset` and `VISION_BASE_MODEL_PRESETS`.
- Produces:

```typescript
export type VisionManagedModel = {
  id: string;
  name: string;
  repoId: string;
  revision: string;
  path: string;
  capabilities: string[];
  supported: boolean;
  probeStatus: string;
  probeMessage: string;
  availableOnYouyun: boolean;
  updatedAt?: string;
};

export type VisionReadinessState = "ready" | "pending" | "blocked";
export type VisionReadiness = {
  localTraining: { state: VisionReadinessState; reason: string };
  youyunTraining: { state: VisionReadinessState; reason: string };
  inference: { state: VisionReadinessState; reason: string };
};

export function visionModelMetadata(model: VisionManagedModel): {
  family: string;
  parameterScale: string;
};
export function visionModelReadiness(model: VisionManagedModel): VisionReadiness;
export function visionModelBucket(model: VisionManagedModel): "available" | "pending" | "error";
export function visionModelCounts(models: readonly VisionManagedModel[]): {
  total: number;
  localReady: number;
  youyunReady: number;
  attention: number;
};
```

- [ ] **Step 1: Add catalog family metadata and write failing helper tests**

Add `family: "Qwen2-VL" | "Qwen2.5-VL" | "Qwen3-VL"` to every preset. Test that:

```javascript
assert.deepEqual(visionModelMetadata(localPassed), {
  family: "Qwen2.5-VL",
  parameterScale: "3B",
});
assert.deepEqual(visionModelMetadata(customModel), {
  family: "自定义",
  parameterScale: "其他",
});
```

- [ ] **Step 2: Add failing readiness tests**

Cover these real states:

```javascript
assert.deepEqual(visionModelReadiness(localPassed), {
  localTraining: { state: "ready", reason: "本地目录和训练能力已通过检查" },
  youyunTraining: { state: "pending", reason: "Repo ID 尚未配置到优云可用模型目录" },
  inference: { state: "ready", reason: "本地目录和推理能力已通过检查" },
});
assert.equal(visionModelBucket(remoteCandidate), "available");
assert.equal(visionModelBucket(missingPathModel), "error");
```

Also assert aggregation counts and that attention counts each model once even if multiple exits are pending or blocked.

- [ ] **Step 3: Run helper tests and verify RED**

Run: `npm run test:frontend -- --test-name-pattern="vision model management"`

Expected: failure because `lib/vision-model-management.ts` and preset `family` fields do not exist.

- [ ] **Step 4: Implement the pure helper module**

Use conservative rules from the spec:

```typescript
const visual = model.capabilities.includes("vision_language");
const trains = model.capabilities.includes("lora_training") || model.capabilities.includes("qlora_training");
const localProbe = model.supported && model.probeStatus === "passed" && Boolean(model.path.trim());
const serves = model.capabilities.includes("transformers_serving");
```

Treat `missing`, `failed`, `unsupported`, and any non-candidate/non-passed probe status as blocked. A remote candidate with `availableOnYouyun` and training capabilities is available even when local exits remain pending.

- [ ] **Step 5: Run helper and preset tests and verify GREEN**

Run: `npm run test:frontend`

Expected: all frontend Node tests pass.

### Task 3: Rebuild the model-management workspace

**Files:**
- Modify: `components/vision-understanding-module.tsx:1-535`
- Modify: `app/globals.css:365-420, 745-755`

**Interfaces:**
- Consumes: `VisionManagedModel`, `visionModelMetadata`, `visionModelReadiness`, `visionModelBucket`, and `visionModelCounts`.
- Preserves: existing `onRegister`, `onProbe`, `onRefresh`, preset selection, detail opening, and model-list loading behavior.

- [ ] **Step 1: Replace the local model type and status helpers**

Import the pure helper module, alias `VisionModel` to `VisionManagedModel`, and remove `visionModelState`, `visionModelStateLabel`, and recommendation-state rendering from the main page.

- [ ] **Step 2: Add view state for filters and drawers**

Use explicit state:

```typescript
const [bucket, setBucket] = useState<"all" | "available" | "pending" | "error">("all");
const [environment, setEnvironment] = useState<"all" | "local" | "youyun">("all");
const [family, setFamily] = useState("all");
const [scale, setScale] = useState("all");
const [showCatalog, setShowCatalog] = useState(false);
const [catalogQuery, setCatalogQuery] = useState("");
```

Filtering must combine all active controls without clearing them during refresh.

- [ ] **Step 3: Rebuild the header, status strip, and toolbar**

Use lucide icons `Plus`, `LibraryBig`, `RefreshCw`, `Search`, `SlidersHorizontal`, `MoreHorizontal`, and `X`. Render one primary `登记基模` button, one secondary `浏览基模目录` button, four compact counters, segmented bucket controls, search, three selects, and one icon-only refresh button.

- [ ] **Step 4: Rebuild the desktop table and mobile asset list**

Desktop columns are model, source, three-exit readiness matrix, last checked, and actions. Render each readiness cell from the helper result and expose its reason with `title`. Add a parallel `.vision-model-mobile-list` structure hidden on desktop and shown at `max-width: 700px`.

- [ ] **Step 5: Upgrade the detail drawer**

Render identity, the three readiness rows with reasons, full path/copy, capability tags, raw probe diagnostics, and `重新探针`/`关闭` actions. Do not add navigation or destructive controls.

- [ ] **Step 6: Add the catalog drawer and compact registration modal**

Catalog rows use preset metadata and a matching registered model. Registered rows open the asset drawer; unregistered rows call `onModelPresetSelect`, close the catalog, and open registration. The registration modal shows one selected-preset summary instead of the full preset grid and offers a `自定义模型` control that resets the form to blank repository fields.

- [ ] **Step 7: Implement loading and empty states**

Keep existing data visible on refresh. When there are no assets, show both catalog and custom-registration actions. When filters produce no matches, show `清除筛选` and reset only search/filter state.

- [ ] **Step 8: Replace obsolete CSS with scoped operational styles**

Remove `.vision-youyun-recommendation-list` main-page grid styles and add stable dimensions for the status strip, toolbar, readiness matrix, catalog drawer, asset rows, icon buttons, and mobile list. Ensure no text overlaps and no dominant single-hue treatment.

- [ ] **Step 9: Run frontend verification**

Run:

```powershell
npm run test:frontend
npx tsc --noEmit
npm run build
```

Expected: all commands exit 0.

### Task 4: Integration verification and UI acceptance

**Files:**
- Modify only if failures expose a defect in Task 1-3 files.

**Interfaces:**
- Verifies the complete default-list API and browser workflow.

- [ ] **Step 1: Run focused backend regression**

Run: `python -m unittest backend.test_vision_api`

Expected: all vision API tests pass without launching GPU training or vLLM.

- [ ] **Step 2: Run syntax and diff checks**

Run:

```powershell
python -m compileall -q backend
git diff --check
```

Expected: both commands exit 0.

- [ ] **Step 3: Start or reuse local services**

Check ports before starting. Reuse healthy frontend/backend processes; otherwise start the repository's existing backend command and `npm run dev` on an unused port. Do not start training or inference runtimes.

- [ ] **Step 4: Verify desktop layout at 1440px**

Open the visual-understanding model-management page and confirm the first viewport contains the header, compact counters, filters, and asset rows. Confirm catalog cards no longer occupy the main page, all drawer actions work, and the console has no errors.

- [ ] **Step 5: Verify tablet and mobile layouts**

At 1024px confirm toolbar wrapping and readable readiness cells. At 390px confirm the table is replaced by mobile asset items, no core horizontal scroll exists, and all buttons/text remain within their containers.

- [ ] **Step 6: Review the final diff against the spec**

Confirm every spec item is either implemented or explicitly listed as out of scope. Confirm unrelated working-tree changes are absent from the feature diff.

- [ ] **Step 7: Commit the implementation**

Stage only:

```powershell
git add -- backend/vision_api.py backend/test_vision_api.py lib/vision-base-model-presets.ts lib/vision-model-management.ts lib/vision-model-management.test.mjs components/vision-understanding-module.tsx app/globals.css docs/superpowers/plans/2026-08-27-vision-model-management-redesign.md
git commit -m "feat: redesign vision model management"
```
