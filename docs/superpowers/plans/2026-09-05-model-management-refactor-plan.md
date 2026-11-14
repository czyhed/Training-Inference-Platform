# 模型管理重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 先将 NLP 模型管理拆成可维护的真实资产流程，再让视觉理解与文生图模型管理复用统一契约和界面。

**Architecture:** 保留旧 FastAPI 路由，在前端增加模型资产 adapter 和共享视图组件。NLP 先完成拆分并通过回归测试，视觉与文生图随后只替换展示层和字段映射，不重写探针、优云或 Diffusers provider。

**Tech Stack:** Next.js 14、React 18、TypeScript、FastAPI、SQLite/PostgreSQL、Vitest/Node test、pytest。

**Spec:** `docs/superpowers/specs/2026-09-05-model-management-refactor-design.md`

## Global Constraints

- 不覆盖或回滚当前工作树已有改动。
- 前端只调用后端 API，不直接访问优云、ModelScope、MinIO、SSH 或本地文件系统。
- 旧接口保持兼容，新字段通过 adapter 转换。
- 未加载数据不得伪造 0；模型可用性由后端事实字段决定。
- 不启动真实 GPU、vLLM 或优云实例。

---

### Task 1: 统一模型资产契约

**Files:**
- Create: `lib/model-asset-contracts.ts`
- Create: `lib/model-asset-api.ts`
- Test: `lib/model-asset-contracts.test.mjs`

**Interfaces:**
- `normalizeModelAsset(payload, modality): ModelAssetView`
- `modelAssetRequest<T>(path, options?): Promise<T>`
- `modelAssetStatusLabel(status): string`

- [ ] **Step 1: Write failing tests**

覆盖 NLP、视觉、文生图旧 payload 的转换、缺失字段显示 `null`、归档状态和错误响应脱敏。

- [ ] **Step 2: Run focused tests**

运行 `npm.cmd run test:frontend -- --test-name-pattern="model asset contract"`，确认新模块尚不存在或断言失败。

- [x] **Step 3: Implement contracts and adapter**

只在 serializer 层做 snake_case/camelCase 转换，不改后端旧接口。

- [x] **Step 4: Run focused tests**

同一命令应全部通过。

### Task 2: NLP 模型管理拆分

**Files:**
- Modify: `components/model-management-module.tsx`
- Create: `components/model-asset-table.tsx`
- Create: `components/model-asset-detail-drawer.tsx`
- Test: `lib/model-management-refactor.test.mjs`

**Interfaces:**
- 表格接收 `ModelAssetView[]`、筛选条件和 `onOpen`。
- 详情抽屉接收资产和重命名、归档、刷新、Adapter 选择回调。

- [ ] **Step 1: Write failing tests**

断言 NLP 页面使用真实 `/api/model-assets`、无静态初始资产依赖、加载/空数据/失败状态分开、保留登记/扫描/归档/Adapter 操作。

- [x] **Step 2: Implement extracted table and drawer**

从现有组件搬移展示逻辑，先保持请求路径和返回结构不变。

- [x] **Step 3: Replace static fallback with explicit states**

列表为空时显示“暂无模型资产”；请求失败时显示错误与重试；统计未加载时显示“暂无数据”。

- [x] **Step 4: Run NLP tests and TypeScript**

运行 `npm.cmd run test:frontend -- --test-name-pattern="model management"` 与 `npm.cmd exec tsc -- --noEmit`。

### Task 3: 后端模型资产统一字段

**Files:**
- Modify: `backend/store.py`
- Modify: `backend/app.py`
- Create: `backend/test_model_asset_contract.py`

**Interfaces:**
- `serialize_model_asset_for_platform(asset) -> dict`
- 旧 `/api/model-assets` 返回保持兼容；新增统一字段可选输出。

- [ ] **Step 1: Write failing tests**

覆盖 base/adapter/merged、来源、归档、共享存储、lineage 和引用保护。

- [ ] **Step 2: Implement serializer only**

从现有 `model_assets` 和任务 artifacts 组合统一字段，不修改历史数据库结构。

- [ ] **Step 3: Run focused backend tests**

运行 `python -m pytest backend/test_model_asset_contract.py backend/test_store.py -q`。

### Task 4: 视觉理解模型管理接入统一资产视图

**Files:**
- Modify: `components/vision-model-management-view.tsx`
- Modify: `lib/vision-model-management.ts`
- Test: `lib/vision-model-management-view.test.mjs`

- [ ] **Step 1: Write failing tests**

覆盖目录候选与资产分离、本地训练/优云训练/推理三种 readiness、探针失败和归档状态。

- [x] **Step 2: Reuse shared contract for readiness/statistics**

复用 NLP 表格与详情布局，视觉只注入 readiness 单元格和目录操作。

- [ ] **Step 3: Run visual model tests**

运行 `npm.cmd run test:frontend -- --test-name-pattern="vision model"`。

### Task 5: 文生图模型管理接入统一资产视图

**Files:**
- Modify: `components/image-generation-models.tsx`
- Test: `lib/image-generation-models.test.mjs`

- [ ] **Step 1: Write failing tests**

覆盖 SDXL 基模、LoRA/QLoRA、Diffusers 推理能力、训练 Adapter lineage、目录候选与归档。

- [x] **Step 2: Reuse shared contract for readiness/statistics**

保留现有 `/api/image-generation/models` 接口，通过 adapter 转为统一资产视图。

- [ ] **Step 3: Run image model tests**

运行 `npm.cmd run test:frontend -- --test-name-pattern="image generation model"`。

### Task 6: 页面视觉验收与发布门禁

**Files:**
- Modify: `app/globals.css`
- Modify: `CURRENT_STATE.md`
- Test: `lib/model-management-visual-regression.test.mjs`

- [ ] **Step 1: Lock measured dimensions**

固定桌面侧栏、内容区、统计卡、表格行高、按钮和图标尺寸，移动端禁止横向溢出。

- [x] **Step 2: Run complete verification**

运行 `npm.cmd run test:frontend`、`python -m pytest backend -q`、`npm.cmd exec tsc -- --noEmit`、`npm.cmd run build`、`git diff --check`。

- [ ] **Step 3: Update state and rollback notes**

记录实际完成项、未完成项、验证命令和回滚指针，不宣称未验证的远程模型可用。
