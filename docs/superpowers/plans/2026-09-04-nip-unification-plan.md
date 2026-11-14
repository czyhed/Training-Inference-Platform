# NIP 统一训推平台复刻与改造实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以仓库 `master` 分支的 NIP 核心实现为基线，保留当前分支的视觉理解、文生图和优云能力，改造成统一的五大模块训推控制台。

**Architecture:** 采用“统一应用壳 + 统一查询契约 + 领域适配器”的渐进式方案。`master` 的 NLP 数据集、微调、模型和推理能力作为稳定基线；当前分支的视觉理解、文生图、评测和优云 Provider 通过 `modality` 与统一生命周期适配到同一套 UI。旧 API 保持兼容，新页面优先走统一 client，避免一次性重写现有运行时。

**Tech Stack:** Next.js 14、React 18、TypeScript、FastAPI、SQLite/PostgreSQL、MinIO、LLaMA-Factory、vLLM、Diffusers、优云 Provider、Vitest/Node test、pytest。

**Spec:** `docs/训推平台-工程规范与变更门禁.md`、`ARCHITECTURE.md`、`CURRENT_STATE.md`、仓库 `master` 分支实现和线上 NIP UI 行为。

## Global Constraints

- 以 `master` 分支代码作为 NIP 后端和基础业务的事实基线；线上地址只用于核对可见 UI、字段和交互。
- 不覆盖、回滚或删除当前工作区已有改动；所有修改必须先检查 `git status --short`。
- 前端不得直接调用优云、ModelScope、MinIO、SSH 或本地文件系统；所有访问经过 FastAPI。
- 新增接口使用统一响应结构；旧接口保持兼容，不做无计划的破坏性迁移。
- 页面不能用伪造的 0、假进度、假实例状态或假价格；无真实数据时显示“暂无数据”。
- 训练、推理、评测和模型下载不得在本计划验证阶段启动真实 GPU、vLLM 或优云实例。
- 任何优云实例必须有创建、连接、执行、停止、删除、清理确认和超时兜底记录。
- 任务只有在产物、指标、Manifest 和资源清理均满足条件时才能标记为完成。
- 发布、部署和回收站操作必须遵守引用保护，不物理删除仍被任务引用的数据和模型。

---

## 1. 对照结论与目标边界

### 1.1 `master` 已有的 NIP 基线

| 领域 | `master` 事实能力 | 目标处理 |
| --- | --- | --- |
| 数据集 | JSONL 上传、SFT/DPO 校验、发布、修复、预览、克隆、删除 | 保留接口，统一列表、详情和版本视图 |
| 微调任务 | Web/CLI 提交、参数校验、队列、日志、Loss、停止、重试、产物 | 接入统一任务状态和资源视图 |
| 模型管理 | 基模登记、缓存扫描、训练模型同步、Adapter 变体、归档恢复 | 扩展优云公共模型和视觉/图像能力 |
| 推理服务 | vLLM 创建、启动、停止、恢复、日志、指标、聊天、API Key | 接入视觉图生文和文生图服务 |
| 概览 | 数据集、任务、模型、服务、资源和异常汇总 | 扩展三种 modality 和资源租约 |
| 资源 | 本地单 GPU 与优云 Provider 接口 | 统一训练、推理、评测清理机制 |

### 1.2 当前分支必须保留的能力

- `vision_api.py`、`vision_runtime.py`、`vision_training.py`、`vision_executor.py` 的视觉理解训练、推理、评测和模型探测。
- `image_generation_api.py`、`image_generation_training_api.py`、`image_generation_runtime.py`、`image_generation_training_runtime.py` 的文生图训练、推理、结果和评测。
- `providers/youyun/` 下的 GPU 库存、价格、镜像、SSH、远程训练、Diffusers 和推理实现。
- 当前数据集图片缩略图、图片预览、Prompt/Negative Prompt、验证集和 API Key 功能。

### 1.3 本轮明确不做

- 不重写 LLaMA-Factory、vLLM、Diffusers 或优云 SDK。
- 不实现多用户组织权限重建、多 GPU 调度或新的计费系统。
- 不删除旧 API，不迁移历史数据到新表后再删除旧表。
- 不把线上 NIP 的编译产物或不可见实现复制进仓库。

---

## 2. 目标架构：从当前多模块到统一控制台

### 2.1 当前状态

```text
app/page.tsx
  -> NLP: datasets / training / models / services
  -> Vision: training / models / services
  -> Image Generation: datasets / training / models / services / evaluation

多个组件各自 fetch
  -> 多套响应结构
  -> 多套状态名称
  -> 训练、推理、评测资源清理逻辑分散
```

### 2.2 目标状态

```text
统一 PlatformShell
  -> 概览
  -> 数据集
  -> 微调任务
  -> 模型管理
  -> 推理服务

统一 platform client / types
  -> modality: NLP | VISION | IMAGE_GENERATION
  -> dataset/task/model/service read model
  -> state / phase / resourceState / cleanupState

FastAPI 兼容适配层
  -> 现有 NLP API
  -> 现有 Vision API
  -> 现有 Image Generation API
  -> 现有 Youyun Provider
```

### 2.3 用户可见导航

左侧导航固定五项，顶部提供业务域切换：

```text
概览
数据集
微调任务
模型管理
推理服务

业务域：NLP | 视觉理解 | 文生图
```

导航跳转必须携带 `domain` 和必要的 `entityId`，浏览器刷新后可恢复当前页面，不再依赖组件内部临时状态。

---

## 3. 统一数据与状态契约

### 3.1 统一读取模型

新增 `lib/platform-contracts.ts`，只定义前端读取和操作需要的稳定字段：

```ts
export type Modality = "nlp" | "vision" | "image-generation";
export type ResourceState = "none" | "provisioning" | "allocated" | "connected" | "releasing" | "released";
export type CleanupState = "not_required" | "pending" | "running" | "released" | "failed";

export type PlatformTaskSummary = {
  id: string;
  modality: Modality;
  name: string;
  state: string;
  phase: string;
  progress: number | null;
  resourceState: ResourceState;
  cleanupState: CleanupState;
  failureCode: string | null;
  updatedAt: string;
};
```

后端对应增加 `backend/platform_contracts.py` 和序列化函数，内部继续使用 snake_case，边界处转换为 camelCase。

### 3.2 状态迁移原则

任务状态和资源状态分开保存，不能用一个 `status` 字段同时表达训练进度和实例清理：

```text
queued
  -> provisioning
  -> preparing
  -> running
  -> collecting
  -> uploading_artifact
  -> completed

任一运行阶段 -> stopping -> stopped
任一非终态阶段 -> failed -> cleanup pending -> released
```

终态要求：

- `completed`：训练/推理产物可用、指标和 Manifest 完整、`cleanupState=released` 或 `resourceState=none`。
- `failed`：保存稳定 `failureCode`、脱敏 `failureMessage`、`suggestedAction`，并进入清理流程。
- `stopped`：保存停止原因，资源状态已确认释放。

### 3.3 统一 API 响应

新增接口统一返回：

```json
{
  "requestId": "req-...",
  "traceId": "trace-...",
  "data": {},
  "error": null,
  "meta": {
    "timestamp": "2026-09-04T18:00:00+08:00",
    "apiVersion": "v1"
  }
}
```

旧 `/api/tasks`、`/api/vision/tasks`、`/api/image-generation/training/tasks` 等接口保留原结构，由 client 适配，不在本轮强制迁移。

---

## 4. 文件级改造地图

### 4.1 前端文件

| 文件 | 从什么状态 | 改成什么状态 |
| --- | --- | --- |
| `app/page.tsx` | 页面壳、域切换和组件装配集中在一个大文件 | 只保留路由状态和模块装配，公共导航/面包屑/域切换下沉 |
| `app/globals.css` | NLP、视觉、文生图样式并存，部分页面密度不同 | 增加 NIP 统一 token、表格、状态、抽屉和响应式规则 |
| `lib/types.ts` | 业务类型分散，部分字段只在组件中声明 | 引用统一平台契约，保留领域专属类型 |
| `lib/platform-contracts.ts` | 不存在 | 新增统一 modality、状态、资源、分页、事件类型 |
| `lib/platform-api.ts` | 不存在，组件重复 fetch | 新增统一请求、错误解析、取消请求、轮询和 SSE 工具 |
| `components/platform-shell.tsx` | 不存在 | 新增左侧导航、顶部域切换、面包屑和页面容器 |
| `components/platform-stats.tsx` | 各模块自行渲染统计 | 新增统一统计卡片，支持真实值/暂无数据 |
| `components/lifecycle-badge.tsx` | 各组件自行定义状态文案 | 新增统一状态标签和资源清理提示 |
| `components/overview-module.tsx` | 只聚合现有接口 | 增加三种 modality、资源租约、异常待处理和实时指标 |
| `components/training-module.tsx` | NLP 独立任务页面 | 使用统一任务表格和详情结构，保留 NLP 参数表单 |
| `components/vision-training-module.tsx` | 视觉专用页面 | 接入统一任务壳，保留视觉预检/图片数据/评测配置 |
| `components/image-generation-training.tsx` | 文生图专用页面 | 接入统一任务壳，保留 LoRA/SDXL 参数和素材预览 |
| `components/model-management-module.tsx` | NLP 模型页面 | 接入统一模型资产表格和来源链路 |
| `components/vision-model-management-view.tsx` | 视觉模型页面 | 改为统一模型资产详情的视觉扩展 |
| `components/image-generation-models.tsx` | 文生图模型页面 | 改为统一模型资产详情的图像扩展 |
| `components/inference-module.tsx` | NLP 推理服务页面 | 接入统一服务列表、生命周期和 API Key 操作 |
| `components/vision-inference-service-module.tsx` | 视觉推理服务页面 | 接入统一服务详情，保留图片调试和评测入口 |
| `components/image-generation-inference-workspace.tsx` | 文生图工作区 | 接入统一服务详情，保留图片生成、结果和 API Key |
| `components/image-generation-evaluation.tsx` | 文生图独立评测 | 由服务详情进入，保留评测任务、日志和样本结果 |

### 4.2 后端文件

| 文件 | 从什么状态 | 改成什么状态 |
| --- | --- | --- |
| `backend/app.py` | NLP、数据集和公共接口集中，文件较大 | 保留旧路由，挂载统一平台聚合路由和序列化适配 |
| `backend/platform_contracts.py` | 不存在 | 新增统一 DTO、状态、错误和分页模型 |
| `backend/platform_api.py` | 不存在 | 新增概览、跨域列表、实体时间线和统一操作入口 |
| `backend/overview.py` | 聚合 NLP/部分资源 | 扩展三种 modality、资源租约、异常和更新时间 |
| `backend/store.py` | SQLite 领域存储和兼容迁移 | 增加统一字段读取、事件时间线和引用检查，不改变旧字段语义 |
| `backend/postgres_storage.py` | PostgreSQL 任务、服务、数据集和模型文件表 | 增加统一查询索引和幂等事件字段，保持迁移幂等 |
| `backend/resource_lifecycle.py` | 不存在，清理逻辑分散 | 新增资源租约、释放确认、超时扫描和幂等清理接口 |
| `backend/inference_service.py` | NLP vLLM 服务运行时 | 抽取共享生命周期接口，保留 NLP 专属命令构建 |
| `backend/vision_runtime.py` | 视觉任务队列和实例处理 | 接入共享资源租约和统一事件 |
| `backend/image_generation_runtime.py` | 文生图任务运行时 | 接入共享资源租约和统一事件 |
| `backend/providers/youyun/*` | 多处直接维护错误与清理分支 | 统一 Provider 错误分类、幂等键、实例状态和释放确认 |
| `backend/test_platform_contracts.py` | 不存在 | 新增统一契约和错误映射测试 |
| `backend/test_resource_lifecycle.py` | 不存在 | 新增优云/本地资源生命周期 Fake 测试 |

### 4.3 文档与验证文件

- 更新 `ARCHITECTURE.md`：记录统一平台壳、适配层和资源租约。
- 更新 `CURRENT_STATE.md`：记录阶段完成状态、兼容接口和未完成边界。
- 新增 `docs/adr/2026-09-04-nip-unification.md`：记录采用渐进适配而非一次性重构的决策。
- 新增 `lib/platform-shell.test.mjs`、`lib/platform-api.test.mjs`、`lib/platform-navigation.test.mjs`。

---

## 5. 详细实施任务

### Task 0: 基线冻结与差异矩阵

**Files:**
- Read: `README.md`, `CURRENT_STATE.md`, `ARCHITECTURE.md`, `docs/训推平台-工程规范与变更门禁.md`
- Create: `.scratch/nip-unification-baseline.md`
- Test: existing backend and frontend suites

**Interfaces:**
- Produces: 当前分支改动清单、`master` 能力清单、线上 NIP 页面字段清单、失败测试基线。

- [ ] **Step 1: 记录工作区和分支**

```powershell
git status --short
git branch --show-current
git diff --stat master...HEAD
```

- [ ] **Step 2: 建立最小测试基线**

```powershell
python -m pytest backend -q
npx tsc --noEmit
npm run test:frontend
npm run build
```

- [ ] **Step 3: 记录基线结果**

把命令、失败用例、受影响边界和回滚点写入 `.scratch/nip-unification-baseline.md`，不把真实密钥、远端 IP 或完整 token 写入文件。

**验收:** 基线文件存在，任何已有失败都被区分为历史失败或本次改造新增失败。

### Task 1: 统一契约和错误模型

**Files:**
- Create: `backend/platform_contracts.py`
- Create: `lib/platform-contracts.ts`
- Create: `lib/platform-api.ts`
- Modify: `lib/types.ts`
- Test: `backend/test_platform_contracts.py`, `lib/platform-api.test.mjs`

**Interfaces:**
- `serialize_platform_error(code, message, details, retryable) -> dict`
- `normalize_task_summary(payload) -> PlatformTaskSummary`
- `platformRequest<T>(path, options?, signal?) -> Promise<T>`

- [x] **Step 1: 写错误和状态失败测试**

覆盖 `camelCase` 转换、未知状态降级、供应商错误脱敏、分页字段缺失和 AbortSignal 取消。

- [x] **Step 2: 运行失败测试**

```powershell
python -m pytest backend/test_platform_contracts.py -q
node --test lib/platform-api.test.mjs
```

预期：新测试先失败，因为统一契约尚未存在。

- [x] **Step 3: 实现最小契约**

只实现本计划定义的字段、错误映射和请求取消，不改现有领域运行时。

- [x] **Step 4: 验证契约**

```powershell
python -m pytest backend/test_platform_contracts.py -q
node --test lib/platform-api.test.mjs
```

### Task 2: 统一应用壳和导航

**Files:**
- Create: `components/platform-shell.tsx`
- Create: `components/platform-stats.tsx`
- Create: `components/lifecycle-badge.tsx`
- Modify: `app/page.tsx`
- Modify: `app/globals.css`
- Test: `lib/platform-navigation.test.mjs`, `lib/navigation-responsive.test.mjs`

**Interfaces:**
- `PlatformShell({ module, domain, onModuleChange, onDomainChange, children })`
- `LifecycleBadge({ state, resourceState, cleanupState })`

- [x] **Step 1: 写导航失败测试**

验证五项导航、三种业务域、刷新后状态恢复、未知路径回退概览、移动端不产生横向溢出。

- [x] **Step 2: 实现壳层**

从 `app/page.tsx` 移出导航和页面容器，保留现有模块渲染入口，先不改变业务 API。

- [x] **Step 3: 接入 NIP 视觉规范**

统一页面背景、白色内容面板、蓝色主操作、状态标签、表格密度、抽屉层级和 390px 断点。

- [x] **Step 4: 验证**

```powershell
node --test lib/platform-navigation.test.mjs lib/navigation-responsive.test.mjs
npx tsc --noEmit
```

### Task 3: 概览统一聚合

**Files:**
- Modify: `backend/overview.py`
- Modify: `backend/app.py`
- Modify: `components/overview-module.tsx`
- Test: `backend/test_overview.py`, `lib/platform-overview.test.mjs`

**Interfaces:**
- `GET /api/platform/overview`
- 返回 `datasets`、`tasks`、`models`、`services`、`resources`、`inferenceMetrics`、`issues`、`recentEvents`。

- [x] **Step 1: 写聚合失败测试**

覆盖 NLP、视觉理解、文生图混合统计，资源无实例，单个领域接口失败时局部降级，所有数值带 `updatedAt`。

- [x] **Step 2: 实现聚合适配**

复用现有 Store 和运行时查询，不让概览直接触发破坏性操作；错误进入 `issues`。

- [x] **Step 3: 替换前端假数据**

概览卡片、资源、负载、指标、异常和动态全部来自 API；空值显示“暂无数据”。

- [x] **Step 4: 验证**

```powershell
python -m pytest backend/test_overview.py -q
node --test lib/platform-overview.test.mjs
```

实施记录（2026-09-04）：新增 `/api/platform/overview`，旧 `/api/overview` 保持兼容；四个领域读取采用局部降级，失败项进入脱敏 `issues`；聚合区块统一返回 `updatedAt`，并保留 `counts`、`alerts`、`recentActivity` 兼容字段。验证：`backend/test_overview.py`、`backend/test_app_cli.py`、`lib/platform-overview.test.mjs`、前端全量测试、后端全量测试、TypeScript 和生产构建均通过。

### Task 4: 数据集统一视图

**Files:**
- Modify: `app/page.tsx`
- Modify: `backend/app.py`
- Modify: `backend/store.py`
- Modify: `components/dataset-repair-panel.tsx`
- Test: existing dataset tests plus `lib/dataset-management.test.mjs`

**目标迁移:**

```text
旧：每个业务域各自显示数据集列表
新：统一列表 + modality 筛选 + 版本/校验/发布状态 + 视觉/图像详情扩展
```

- [x] **Step 1: 写数据集契约测试**

覆盖文本 JSONL、视觉 ZIP、文生图 metadata、校验失败、发布保护、被任务引用时删除返回 `DATASET_REFERENCED`。

- [x] **Step 2: 统一列表和筛选**

字段固定为名称、业务域、结构、数据量、版本、状态、更新时间、创建人、操作。

- [x] **Step 3: 统一详情和预览**

文本显示消息；视觉显示图片、问题、答案；文生图显示图片、Prompt、Negative Prompt 和原始元数据。

- [x] **Step 4: 验证上传、校验、发布和只读流程**

```powershell
python -m pytest backend/test_store.py backend/test_vision_api.py backend/test_image_generation_api.py -q
node --test lib/dataset-management.test.mjs lib/image-generation-upload.test.mjs
```

实施记录（2026-09-04）：新增 `/api/platform/datasets`，支持 `domain`、`state`、`query`、`page/pageSize` 服务端筛选分页，返回 `domain`、`modality`、`structure`、`validationStatus`、`version` 和 `updatedAt`；前端数据集列表改用统一 API Client 和统一读取契约，保留原上传、发布、删除、记录预览及旧 `/api/datasets` 接口兼容。视觉和文生图详情继续提供缩略图、问题/答案、Prompt、Negative Prompt 与原始元数据。验证：平台数据集契约测试、现有数据集/上传回归、前端全量、后端全量、TypeScript 和生产构建均通过。

### Task 5: 微调任务统一视图

**Files:**
- Create: `components/training-task-detail.tsx`
- Modify: `components/training-module.tsx`
- Modify: `components/vision-training-module.tsx`
- Modify: `components/image-generation-training.tsx`
- Modify: `backend/app.py`, `backend/vision_api.py`, `backend/image_generation_training_api.py`
- Test: `backend/test_platform_task_state.py`, `lib/training-task-view.test.mjs`

**目标迁移:**

```text
旧：NLP、视觉、文生图各自创建/展示任务
新：统一任务列表和详情，表单按 modality 插入专属区块
```

- [x] **Step 1: 写状态迁移和日志测试**

覆盖队列、实例准备、依赖安装、训练、产物收集、失败、停止、重试、Loss 和 Eval Loss 来源。

- [x] **Step 2: 统一任务列表**

字段固定为任务名称、业务域、基座模型、数据集版本、训练方式、状态/阶段、进度、创建时间、操作。

- [x] **Step 3: 统一任务详情**

详情页分为基本信息、模型与数据、训练参数、日志、指标、资源、产物和事件时间线。

- [ ] **Step 4: 接入专属表单**

NLP 保留 SFT/DPO；视觉保留图片/问答和验证集；文生图保留 SDXL/LoRA、分辨率和素材配置。

- [x] **Step 5: 验证**

```powershell
python -m pytest backend/test_app_cli.py backend/test_vision_runtime.py backend/test_image_generation_training_runtime.py -q
node --test lib/training-task-view.test.mjs
```

实施记录（2026-09-04）：新增 `/api/platform/tasks`，合并 NLP、视觉理解、文生图三个运行时任务，支持 `modality`、`state`、`query`、`page/pageSize` 筛选分页；统一返回 `phase`、`resourceState`、`cleanupState`、`failureCode`、`failureMessage`、`lastHeartbeatAt`、`updatedAt` 等字段，旧任务接口保持兼容。新增 `components/training-task-detail.tsx`，由 NLP、视觉理解、文生图详情页共同展示任务/配置、资源与清理、失败诊断摘要；字段缺失统一显示“暂无数据”，不承载停止、重试、删除等破坏性操作。验证：共享详情测试、前端 108 项、后端 607 项、TypeScript 和生产构建均通过；后端仅有 Windows `.pytest_cache` 写入权限警告。

### Task 6: 模型资产统一视图

**Files:**
- Create: `components/model-asset-detail.tsx`
- Modify: `components/model-management-module.tsx`
- Modify: `components/vision-model-management-view.tsx`
- Modify: `components/image-generation-models.tsx`
- Modify: `backend/app.py`, `backend/store.py`, `backend/model_storage.py`
- Test: `backend/test_model_asset_contract.py`, `lib/model-management-unification.test.mjs`

**目标迁移:**

```text
旧：基模、Adapter、视觉模型、图像模型分散展示
新：统一资产类型 + 来源链路 + 能力标签 + 可训练/可部署状态
```

- [ ] **Step 1: 写模型可用性测试**

只有路径存在、Manifest/Hash 完整、共享存储状态为 `available` 的模型才能进入选择器。

- [ ] **Step 2: 统一模型列表**

支持全部、基模、训练模型、视觉模型、文生图模型、回收站筛选；显示来源、路径、大小、状态、更新时间和操作。

- [ ] **Step 3: 统一来源链路**

训练 Adapter 必须关联基座模型、基座 revision、训练任务、数据集版本、Best/Final 变体和指标。

- [ ] **Step 4: 接入优云公共模型**

公共模型只能作为已探测的容器路径使用；不存在本地路径时不得显示为可训练。

- [ ] **Step 5: 验证**

```powershell
python -m pytest backend/test_model_storage.py backend/test_youyun_vision_catalog.py -q
node --test lib/model-management-unification.test.mjs
```

### Task 7: 推理服务统一视图和 API Key

**Files:**
- Create: `components/inference-service-detail.tsx`
- Modify: `components/inference-module.tsx`
- Modify: `components/vision-inference-service-module.tsx`
- Modify: `components/image-generation-inference-workspace.tsx`
- Modify: `backend/inference_service.py`, `backend/vision_api.py`, `backend/image_generation_api.py`
- Test: `backend/test_inference_service.py`, `backend/test_vision_api.py`, `backend/test_image_generation_api.py`

**目标迁移:**

```text
旧：NLP、视觉、文生图服务分别创建和调试
新：统一服务列表/详情/生命周期，按 modality 渲染聊天或图片工作区
```

- [ ] **Step 1: 写服务状态测试**

覆盖配置创建不占 GPU、启动分配资源、健康检查失败、停止释放、归档/恢复、删除保护和 API Key 撤销。

- [ ] **Step 2: 统一服务列表**

字段固定为服务名称、业务域、运行状态、上线状态、Base Model、Adapter、GPU、创建时间、操作。

- [ ] **Step 3: 统一创建向导**

选择训练产物、Base Model 锁定、Best/Final Adapter、标准/自定义命令、运行环境、镜像、GPU、量化、上下文长度和创建摘要。

- [ ] **Step 4: 保留专属调试区**

NLP 使用对话；视觉使用图片+问题；文生图使用 Prompt、Negative Prompt、尺寸、步数和结果历史。

- [ ] **Step 5: API Key 安全处理**

创建后只返回一次明文，列表和日志只显示前缀；后端不得记录完整密钥，撤销后立即失效。

- [ ] **Step 6: 验证**

```powershell
python -m pytest backend/test_inference_service.py backend/test_vision_api.py backend/test_image_generation_api.py -q
node --test lib/inference-vision.test.mjs lib/image-generation.test.mjs
```

实施记录（2026-09-04，增量）：新增 `components/inference-service-detail.tsx`，由 NLP、视觉理解和文生图服务详情共同展示服务状态、上线状态、基础模型、Adapter、提供方、端点、资源状态、清理状态、失败编码和建议动作；文生图实际详情位于 `image-generation-service.tsx`，保持原有服务 tab、调试、评测、API Key 和日志行为不变。新增 `backend/platform_services.py` 与 `GET /api/platform/services`，按 `modality`、状态、关键词和分页聚合 NLP、视觉理解、文生图服务，统一返回资源/清理状态、失败诊断、模型与 Adapter 信息，旧 `/api/inference/services` 接口保持兼容。新增平台服务契约测试并通过；最终验证：前端 110 项通过，后端 609 项通过，TypeScript 检查通过，生产构建通过，`git diff --check` 无错误（仅保留现有 CRLF 转换提示）。Task 7 的租约接入、API Key 统一生命周期和破坏性操作边界仍按后续步骤继续收敛。

实施记录（2026-09-04，尺寸与租约增量）：根据线上 NIP 浏览器实测值，在 `.workspace-reference-skin-shell` 末尾增加最终尺寸层，固定侧栏 216px、导航项 42px/16px、Lucide 图标 18px、内容区 1440px/30px 34px 44px、控件 40px、图标按钮 32px 触控区域；新增 `lib/reference-console-skin.test.mjs` 防止尺寸回退。新增 `backend/resource_lifecycle.py`，并在视觉训练、文生图运行时、推理服务运行时创建/同步租约，覆盖幂等获取、心跳、释放中、清理失败和释放确认；Fake watchdog 测试已通过。全量验证：前端 111 项、后端 612 项、TypeScript、生产构建和 `git diff --check` 均通过；未启动真实 GPU、vLLM 或优云实例。

### Task 8: 资源租约和失败兜底统一

**Files:**
- Create: `backend/resource_lifecycle.py`
- Modify: `backend/providers/youyun/instance_manager.py`
- Modify: `backend/providers/youyun/remote_training.py`
- Modify: `backend/providers/youyun/inference.py`
- Modify: `backend/vision_runtime.py`, `backend/image_generation_runtime.py`, `backend/inference_service.py`
- Test: `backend/test_resource_lifecycle.py`, existing Youyun provider tests

**Interfaces:**
- `acquire_lease(owner_id, provider, request) -> ResourceLease`
- `mark_connected(lease_id, connection) -> ResourceLease`
- `release_lease(lease_id, reason) -> CleanupResult`
- `reconcile_leases(now) -> list[CleanupResult]`

- [x] **Step 1: 写 Fake Provider 失败测试**

覆盖创建失败、SSH 超时、依赖安装失败、训练异常、推理健康检查失败、重复释放、释放接口超时和进程失联。

- [x] **Step 2: 实现租约状态机**

持久化任务 ID、实例 ID、幂等键、创建时间、最后心跳、释放请求时间和释放确认时间。

- [x] **Step 3: 接入三个运行时**

视觉、文生图和 NLP 推理均通过租约接口，禁止在领域运行时自行删除实例后直接写成功状态。

- [ ] **Step 4: 增加 watchdog**

watchdog 只处理明确超时和失联租约；不可重试错误不通过无限重试掩盖，释放失败进入 `cleanup_failed` 和人工处理列表。

- [ ] **Step 5: 验证**

```powershell
python -m pytest backend/test_resource_lifecycle.py backend/test_youyun_instance_manager.py backend/test_youyun_remote_training.py backend/test_youyun_inference.py -q
```

### Task 9: 统一事件、日志、指标和详情时间线

**Files:**
- Modify: `backend/store.py`, `backend/postgres_storage.py`
- Modify: `backend/app.py`, `backend/vision_api.py`, `backend/image_generation_api.py`, `backend/inference_service.py`
- Create: `components/entity-event-timeline.tsx`
- Test: `backend/test_event_timeline.py`, `lib/entity-event-timeline.test.mjs`

- [ ] **Step 1: 写事件顺序测试**

同一实体的事件必须按 `sequence` 和 `timestamp` 稳定排序，SSE 断线重连不能重复显示。

- [ ] **Step 2: 统一日志查询**

支持 `page`、`pageSize`、时间范围、级别和关键字；单次响应不得无界加载全部日志。

- [ ] **Step 3: 统一指标来源**

训练显示 train/eval Loss、step、吞吐和耗时；推理显示请求数、排队、TTFT、Decode、KV Cache；每个指标返回来源和更新时间。

- [ ] **Step 4: 接入详情时间线**

详情页统一显示提交、预检、实例、SSH、依赖、执行、产物、清理和终态事件。

### Task 10: 回归、兼容和发布门禁

**Files:**
- Modify: `README.md`, `ARCHITECTURE.md`, `CURRENT_STATE.md`
- Create: `docs/adr/2026-09-04-nip-unification.md`
- Test: all changed tests and production build

- [ ] **Step 1: 运行后端全量测试**

```powershell
python -m pytest backend -q
```

- [ ] **Step 2: 运行前端测试和类型检查**

```powershell
npm run test:frontend
npx tsc --noEmit
```

- [ ] **Step 3: 运行生产构建**

```powershell
npm run build
```

- [ ] **Step 4: 做本地 Fake 全流程验收**

```text
创建文本数据集 -> 发布 -> 创建 NLP 微调任务 -> Fake 完成 -> 选择 Best Adapter
创建视觉数据集 -> 预检 -> Fake 视觉任务 -> 视觉推理调试
创建文生图数据集 -> 预检 -> Fake SDXL LoRA 任务 -> 图片推理调试
```

- [ ] **Step 5: 更新文档和回滚指针**

记录每阶段提交、数据库迁移版本、旧接口兼容情况、失败测试和回滚命令；未通过验收不得标记完成。

---

## 6. 数据迁移与兼容策略

### 6.1 数据字段迁移

只允许新增字段或索引，迁移必须幂等：

```text
datasets: modality, active_version_id, lifecycle_state
tasks: modality, dataset_version_id, phase, resource_state, cleanup_state,
       config_fingerprint, failure_code, suggested_action
model_assets: modality, base_model_id, base_model_revision, manifest_sha256
inference_services: modality, resource_state, cleanup_state, api_key_version
events: entity_type, entity_id, sequence, event_type, payload, request_id
```

SQLite 兼容迁移沿用 `backend/store.py` 的启动迁移方式；PostgreSQL 迁移通过 `schema_migrations` 记录版本。不会把本机绝对路径写入跨环境契约。

### 6.2 API 兼容

- 旧接口继续可用，统一 client 负责适配旧响应。
- 新接口使用 `/api/platform/*` 前缀，避免与旧路由冲突。
- 旧任务 ID、数据集 ID、模型 ID 和服务 ID 不变。
- 同一实体的统一详情必须能回链旧领域详情接口。

### 6.3 失败回滚

每个 Task 完成后形成独立可回滚点：

```text
Task 1 契约 -> Task 2 应用壳 -> Task 3 概览 -> Task 4 数据集
-> Task 5 任务 -> Task 6 模型 -> Task 7 服务
-> Task 8 资源 -> Task 9 事件 -> Task 10 发布
```

任何阶段失败时：

1. 停止后续阶段。
2. 保留失败测试和诊断日志。
3. 通过功能开关切回旧组件。
4. 数据库只回滚本阶段新增索引/字段，不能删除已有数据。
5. 不释放或覆盖用户已有模型、Adapter、数据集和任务记录。

---

## 7. 验收标准

### 功能验收

- 五项 NIP 主导航均可访问，三种业务域可切换，刷新后路由状态可恢复。
- 数据集支持列表、搜索、筛选、详情、只读、校验、发布、版本和引用保护。
- 微调任务支持创建、预检、排队、日志、Loss、停止、重试、产物和详情时间线。
- 模型管理能区分公共模型、本地基模、Best/Final Adapter、不可用模型和回收站。
- 推理服务支持创建、启动、停止、重试、恢复、归档、删除、API Key 和调试。
- 视觉理解图生文和文生图图片生成均从统一服务详情进入，不丢失原有专属能力。

### 稳定性验收

- 任意 Fake 优云创建/SSH/依赖/训练/推理失败后，任务进入失败终态并确认资源释放。
- 重复提交、重复停止、重复重试和重复释放均为幂等操作。
- 组件卸载后轮询、SSE 和请求不会继续更新状态。
- API Key、SSH 私钥、数据库密码和完整 token 不出现在日志、错误详情和测试输出。

### UI 验收

- 1440px 桌面端布局稳定，表格密度和 NIP 参考页面一致。
- 390px 移动端无横向溢出，创建向导、详情抽屉和操作按钮可用。
- 所有状态、价格、日志、Loss、实例和指标显示来源与更新时间。
- 无 mock 数据冒充真实运行状态；无“进程退出即成功”的文案。

### 工程验收

```powershell
python -m pytest backend -q
npm run test:frontend
npx tsc --noEmit
npm run build
```

四项命令必须通过；若已有历史失败，必须在基线文件中说明并证明未由本次改造引入。

---

## 8. 实施顺序与审核点

建议按以下顺序执行，每个阶段完成后暂停审核：

1. Task 0：基线和差异矩阵
2. Task 1：统一契约
3. Task 2：应用壳和导航
4. Task 3：概览
5. Task 4：数据集
6. Task 5：微调任务
7. Task 6：模型管理
8. Task 7：推理服务
9. Task 8：资源租约
10. Task 9：日志、指标和事件
11. Task 10：全量回归和发布门禁

**第一阶段不启动任何真实 GPU、vLLM 或优云实例。** 完成 Task 0-2 后先做本地 UI 和契约审核；完成 Task 8 后再做 Fake 资源清理验收；真实优云验收必须另行确认并采用单实例、预算上限和自动释放兜底。

---

## 9. 当前审核结论

- 方案基线已经从“线上页面推测”修正为“`master` 源码 + 线上 UI 交互核对”。
- 实施采用兼容适配，不直接重写已有 NLP、视觉理解和文生图运行时。
- 方案覆盖前端、后端、数据模型、状态机、优云资源、日志、指标、测试、文档和回滚。
- 当前只生成方案文档，尚未修改业务代码，尚未启动服务或实例。

---

## 10. 二次核验：更优的实施策略

### 10.1 结论

直接把 `master` 的页面文件整份覆盖到当前分支，会丢失视觉理解、文生图和优云能力；直接把当前所有组件继续堆进 `app/page.tsx`，又会延续重复状态、重复请求和 mock 残留。因此采用第三种方案：

```text
master 的 NIP 核心契约和稳定交互
        ↓
统一平台壳和跨域 read model
        ↓
NLP / 视觉理解 / 文生图领域适配器
        ↓
保留各领域真正不同的训练、推理和评测表单
```

这个方案比“整份覆盖”更安全，比“继续并列维护三套页面”更容易测试和长期维护。覆盖原有文件仍然执行，但覆盖的是明确列出的目标文件，并且每次覆盖前保存差异、先过失败测试、可通过功能开关回滚。

### 10.2 代码替换原则

| 类型 | 处理方式 |
| --- | --- |
| `master` 中稳定且当前分支未扩展的 NIP 代码 | 直接移植并覆盖目标模块 |
| 当前分支已有视觉/文生图专属逻辑 | 保留，通过适配器接入统一页面 |
| 仅用于演示的 mock 数据 | 删除，不再进入生产组件 |
| 与统一契约冲突的重复 fetch/状态 | 替换为 `platform-api.ts` 和统一状态解析 |
| 用户已有未提交改动 | 不覆盖；先拆分为独立补丁并在变更记录中列出 |
| 训练、推理、优云 Provider 核心实现 | 不整份替换，只改接口适配和生命周期调用点 |

---

## 11. 小功能拆分与边界清单

### 11.1 应用壳与导航

| 小功能 | 输入 | 输出 | 边界 | 测试 |
| --- | --- | --- | --- | --- |
| 左侧主导航 | 当前模块 | URL 状态 | 只负责导航，不请求业务数据 | 五项导航可达 |
| 业务域切换 | `nlp/vision/image-generation` | `domain` 查询参数 | 不改变实体数据 | 域切换保持列表筛选 |
| 面包屑 | 模块、实体 ID | 可返回路径 | 不执行删除或启动 | 详情返回列表 |
| 页面刷新恢复 | URL 参数 | 页面状态 | 不读取组件旧 state | 刷新后模块/域/实体一致 |
| 响应式布局 | viewport | CSS 布局 | 不改变业务状态 | 1440px/390px 无溢出 |

### 11.2 概览

| 小功能 | 数据来源 | 处理 | 不负责 |
| --- | --- | --- | --- |
| 资产统计 | datasets/tasks/models/services | 服务端聚合 | 不在概览发布、删除或启动 |
| 资源卡片 | lease/provider | 显示实例、GPU、计费状态 | 不自行推断释放 |
| 当前负载 | active tasks/services | 显示阶段和数量 | 不修改任务 |
| 推理指标 | inference metrics | 显示请求、TTFT、Decode、KV Cache | 不伪造空值 |
| 异常列表 | failure events | 跳转详情 | 不执行破坏性操作 |
| 最近动态 | entity events | 按 sequence 排序 | 不直接读取运行时日志文件 |

### 11.3 数据集

| 小功能 | 入口 | 服务端边界 | 结果 |
| --- | --- | --- | --- |
| 新建空数据集 | 表单 | 创建 draft 元数据 | 返回 datasetId |
| 上传文本 | JSONL | 文件大小、编码、行号校验 | 生成 dataset version |
| 上传视觉 ZIP | ZIP | 路径、图片签名、metadata 校验 | 生成 manifest |
| 上传文生图素材 | 图片/metadata | 图片 hash、Caption、孤立文件校验 | 生成 IMAGE_CAPTION 版本 |
| 版本校验 | datasetVersionId | 领域 validator | validation report |
| 发布 | versionId | 仅允许无阻断错误版本 | lifecycleState=published |
| 只读预览 | versionId/page/query | 分页返回样本 | 不修改原文件 |
| 修复 | versionId + patch | 生成新版本 | 保留原报告和修复历史 |
| 删除/归档 | datasetId | 检查任务/评测引用 | 归档或返回 409 |

### 11.4 微调任务

| 小功能 | 统一部分 | 领域专属部分 |
| --- | --- | --- |
| 基本信息 | 名称、域、执行环境、幂等键 | 文生图的输出类型 |
| 模型选择 | Base Model、revision、能力探测 | Qwen-VL processor、SDXL pipeline |
| 数据选择 | 发布版本、训练/验证引用 | IMAGE_TEXT / IMAGE_CAPTION 格式 |
| 参数表单 | LoRA/QLoRA、学习率、步数、seed | 像素上限、分辨率、caption dropout |
| 预检 | 配置 fingerprint、库存、价格、镜像 | 视觉 template、Diffusers 组件 |
| 运行状态 | 队列、阶段、日志、Loss、资源 | 训练器特有指标 |
| 产物 | Best/Final、Manifest、Hash | Adapter 或 Diffusers 权重 |
| 失败处理 | 错误码、重试、停止、清理 | Provider/模型专属建议 |

### 11.5 模型管理

| 小功能 | 规则 |
| --- | --- |
| 公共模型发现 | 只显示 API 返回且路径 probe 通过的模型 |
| 本地缓存扫描 | 只登记可推断 Repo ID 且目录完整的缓存 |
| 基模登记 | 保存元数据，不隐式复制文件 |
| Adapter 同步 | 只同步完整 Best/Final 目录 |
| 能力标签 | `text`、`vision`、`image-generation` 可多选 |
| 部署选择器 | 过滤不可用、归档、未入库、revision 不匹配资产 |
| 归档/恢复 | 只改变生命周期，不删除对象 |

### 11.6 推理服务

| 小功能 | NLP | 视觉理解 | 文生图 |
| --- | --- | --- | --- |
| 创建 | Base + Adapter + vLLM | Base + Adapter + processor | Base + LoRA + Diffusers |
| 调试输入 | messages | image + question | prompt + negativePrompt |
| 健康检查 | `/health` + chat | `/health` + image chat | pipeline load + image generation |
| 指标 | TTFT、Decode、KV Cache | 请求/响应延迟、图像尺寸 | 生成耗时、图片大小、队列 |
| 结果 | assistant text | answer text | result image + metadata |
| API Key | 服务级 | 服务级 | 服务级 |
| 释放 | vLLM 进程 + lease | vLLM/vision 进程 + lease | Diffusers 进程 + lease |

---

## 12. 数据传输标准：从浏览器到 Provider

### 12.1 请求链路

```text
Browser
  -> platform-api.ts
  -> FastAPI route/schema
  -> domain service/runtime
  -> store / object storage / provider boundary
  -> normalized response
  -> UI state
```

前端禁止把对象存储 key、SSH 信息、优云实例参数或本地绝对路径直接交给浏览器；浏览器只提交实体 ID、版本 ID、用户输入和受限配置。

### 12.2 创建任务请求

```json
{
  "name": "视觉问答 LoRA",
  "modality": "vision",
  "datasetVersionId": "dsv-...",
  "baseModelId": "mdl-...",
  "baseModelRevision": "main",
  "tuningMode": "lora",
  "provider": "youyun",
  "executionMode": "remote",
  "parameters": {
    "epochs": 3,
    "learningRate": "2e-4",
    "validationSplit": "0.1",
    "seed": 42
  },
  "idempotencyKey": "sha256:..."
}
```

服务端重新读取数据集版本和模型资产，不能信任浏览器传入的名称、路径、数据量或可用状态。

### 12.3 上传传输

```text
pending
  -> uploading
  -> uploaded
  -> validating
  -> available / failed
```

- 小文件可由 FastAPI 接收并落盘到临时目录。
- 大文件和 ZIP 必须流式写入，限制总字节数、成员数量和解压后大小。
- 校验成功后生成 manifest 和 sha256；失败时保留原始文件与脱敏报告。
- MinIO 对象 key 只由后端生成，前端不拼接 key。

### 12.4 异步进度传输

查询接口返回当前快照；SSE 只传增量事件：

```json
{
  "eventId": "evt-...",
  "entityType": "trainingTask",
  "entityId": "vt-...",
  "eventType": "phaseChanged",
  "sequence": 18,
  "timestamp": "2026-09-04T20:30:00+08:00",
  "payload": {
    "phase": "collectingArtifacts",
    "resourceState": "releasing"
  }
}
```

客户端保存最后 `eventId/sequence`，断线后带游标重连；重复事件按 sequence 去重。

### 12.5 错误传输

```json
{
  "requestId": "req-...",
  "traceId": "trace-...",
  "data": null,
  "error": {
    "code": "YOUYUN_SERVICE_UNAVAILABLE",
    "message": "优云服务暂时不可用",
    "details": [{"stage": "provisioning", "retryable": true}],
    "retryable": true
  },
  "meta": {"timestamp": "2026-09-04T20:30:00+08:00", "apiVersion": "v1"}
}
```

供应商原始错误只保存脱敏摘要、阶段和 RetCode；不把完整请求、密钥、SSH 命令或签名 URL 返回前端。

---

## 13. 无效代码清理和文件覆盖清单

### 13.1 明确删除的生产 mock

在统一 API 接入并通过回归测试后删除：

- `lib/mock-data.ts` 中被生产组件引用的初始化数据。
- `components/inference-module.tsx` 的 `initialServices` 原型服务数组及“后端不可用时继续使用原型数据”的分支。
- `components/model-management-module.tsx` 的 `initialBaseModels`、`initialTrainedModels` 原型数组。
- 生产页面中显示“原型数据”“fallback”“后端连接后生效”的用户提示。
- 任何把空响应转换为成功、0、可用或已运行的 UI 兜底。

删除前必须执行：

```powershell
rg -n "mock-data|initialServices|initialBaseModels|initialTrainedModels|原型数据|fallback" app components lib
```

确认只剩测试 fixture 或明确的异常降级文案后，才可删除文件/分支。

### 13.2 受控覆盖文件

以下文件允许按本计划整段替换，但必须先生成备份差异并保留专属能力：

- `app/page.tsx`
- `app/globals.css`
- `components/overview-module.tsx`
- `components/training-module.tsx`
- `components/model-management-module.tsx`
- `components/inference-module.tsx`
- `backend/app.py`
- `backend/overview.py`
- `backend/store.py`
- `backend/inference_service.py`

以下文件禁止整段覆盖，只允许接口级修改：

- `backend/vision_runtime.py`
- `backend/vision_training.py`
- `backend/vision_api.py`
- `backend/image_generation_runtime.py`
- `backend/image_generation_training_runtime.py`
- `backend/image_generation_api.py`
- `backend/providers/youyun/*`

原因：这些文件包含当前分支已经验证过的视觉理解、文生图和资源清理逻辑，整段覆盖会重新引入历史失败。

### 13.3 覆盖操作门禁

每个受控覆盖文件必须按此顺序：

1. 保存当前文件哈希和 `git diff`。
2. 记录要保留的函数、路由和测试。
3. 先写覆盖后的最小失败测试。
4. 使用 `apply_patch` 替换目标区块。
5. 运行该文件对应测试和类型检查。
6. 对比旧 API 路由、导出符号和数据库字段。
7. 验证通过后才进入下一文件。

禁止使用 `git checkout --`、`git reset --hard`、批量删除或无差别复制 `master` 文件。

---

## 14. 更细的交付批次

每个批次只解决一个边界，完成后形成独立提交或可回滚补丁：

1. **契约批次：** 统一类型、错误、分页、事件和 API client。
2. **壳层批次：** 覆盖 `app/page.tsx` 的装配逻辑，新增平台壳。
3. **样式批次：** 统一 NIP 表格、状态、抽屉、移动端样式。
4. **概览批次：** 新增聚合接口，删除假统计和假资源。
5. **数据集批次：** 统一文本、视觉、文生图列表和详情。
6. **任务批次：** 统一任务表格、详情、日志、Loss 和产物。
7. **模型批次：** 统一公共模型、本地模型、Adapter 和能力标签。
8. **服务批次：** 统一 NLP、视觉、文生图服务和 API Key。
9. **清理批次：** 统一 lease、释放确认、watchdog 和重启恢复。
10. **删除批次：** 删除 mock、重复状态和不可达分支。
11. **回归批次：** 全量测试、生产构建、Fake 全流程验收。

每个批次的完成条件都必须同时包含：代码、失败测试、通过测试、文档记录和回滚指针。

---

## 15. 复核后的最终验收门槛

### 必须达到

- 所有 NIP 主功能均能从统一五项导航进入。
- 三种业务域的列表、详情、创建和生命周期操作不互相串数据。
- 浏览器传输只包含业务字段和实体 ID，不包含 Provider 凭据、远端路径和内部命令。
- 所有异步实体均能查询 `state/phase/resourceState/cleanupState`。
- 所有优云失败路径最终有资源终态，不出现“任务失败但实例未知”的假完成。
- 生产组件不再引用 `lib/mock-data.ts` 或原型初始化数组。
- 受控覆盖文件的旧 API 路由、导出组件和专属测试不丢失。
- 数据集、任务、模型、服务的删除都遵守引用保护和软删除规则。
- 视觉理解和文生图原有预览、评测、Adapter、API Key 功能全部回归通过。

### 达不到时的处理

任何一项不满足，都不能进入真实优云验收；保留当前阶段代码和失败证据，先回滚到最近一个通过批次，再修复单一根因。

---

## 16. 审核请求

本次二次核验后的方案已经补充：

- 每个模块的小功能拆分
- 每个边界的输入、输出和禁止职责
- 浏览器、FastAPI、存储、Provider 的数据传输标准
- mock 和无效代码的明确删除清单
- 哪些文件允许覆盖、哪些文件只能接口级修改
- 覆盖原有文件时的备份、测试、差异和回滚门禁
- 更细的 11 个交付批次和最终验收门槛

当前仍未修改业务代码。审核通过后，严格从“契约批次”开始；不会直接整份覆盖整个仓库，也不会删除现有任务、数据集、模型或实例记录。
