# 模型管理重构设计

日期：2026-09-04

## 背景与目标

当前 NLP 模型管理将数据获取、页面状态、筛选、资产操作和详情展示集中在一个组件中；视觉理解和文生图又分别维护自己的模型类型与可用性规则。目标是以 NLP 的成熟模型资产流程为事实基线，先完成 NLP 拆分，再让视觉理解、文生图复用统一资产外壳，同时保留各自的探针、优云目录和 Diffusers 能力。

## 现状证据

- 本地可读取的 `master` 基线为提交 `0efe992`，其中 `components/model-management-module.tsx` 已具备真实 `/api/model-assets` 读取、基模登记、缓存扫描、刷新、归档、共享存储与 Adapter 选择。
- 当前分支已增加 `components/model-asset-management-shell.tsx`、`vision-model-management-view.tsx`、`image-generation-models.tsx`，但三套字段和状态仍未完全统一。
- 当前工作树存在用户和前序任务的未提交改动，本次不得覆盖或回滚。

## 设计

### 1. 统一资产读取模型

前端新增 `lib/model-asset-contracts.ts`，定义 `ModelModality`、`ModelAssetType`、`ModelAssetStatus`、`ModelReadiness`、`ModelLineage` 和 `ModelAssetView`。旧 NLP、视觉、文生图 API 继续保留，由各自 adapter 转换到该 view。

```ts
type ModelAssetView = {
  id: string;
  modality: "nlp" | "vision" | "image-generation";
  assetType: "base" | "adapter" | "merged";
  name: string;
  repoId: string | null;
  revision: string | null;
  localPath: string | null;
  source: "modelscope-cache" | "youyun-catalog" | "manual" | "training-output";
  status: "available" | "pending" | "unavailable" | "archived";
  readiness: { localTraining: ModelReadiness; remoteTraining: ModelReadiness; inference: ModelReadiness };
  lineage: ModelLineage | null;
  storage: { state: "not-uploaded" | "uploading" | "available" | "failed"; bytes: number | null; manifest: string | null };
  updatedAt: string | null;
};
```

### 2. NLP 重构边界

`components/model-management-module.tsx` 保留页面装配，抽取 `lib/model-asset-api.ts` 负责请求、错误解析、刷新和操作；抽取 `components/model-asset-table.tsx` 负责表格；抽取 `components/model-asset-detail-drawer.tsx` 负责详情、归档、重命名、共享存储和 Adapter 变体。页面初始不再依赖静态示例资产，加载中、空数据、接口失败分别展示。

### 3. 视觉与文生图改造

视觉页面复用统一表格和统计区，保留视觉专属的模型目录、探针、优云训练、推理部署三出口。文生图页面复用同一资产表格，保留 SDXL/Diffusers、LoRA/QLoRA 和生成能力。目录候选永远不直接混入已登记资产列表。

### 4. 状态与安全

- “可用”只能来自后端探针、文件完整性和存储状态；前端不自行推断。
- 未加载统计显示“暂无数据”，不显示伪造 0。
- 归档采用软删除；被任务或服务引用的资产禁止物理删除。
- API Key、优云密钥、SSH 信息和远端绝对路径不得进入日志或公开错误。

## 数据流

```text
页面 -> model-asset-api -> 现有 FastAPI 资产接口 -> store/PostgreSQL/MinIO
                                     |
                                     +-> 视觉探针/优云目录 adapter
                                     +-> 文生图 Diffusers/Adapter adapter
```

## 验收标准

1. NLP、视觉理解、文生图三页使用同一表格密度、筛选、详情抽屉和状态语义。
2. 基模、训练 Adapter、归档资产和目录候选不混淆。
3. 资产改名、刷新、归档/恢复、Adapter 选择行为保持兼容。
4. 前端模型管理相关测试、后端资产接口测试、TypeScript、生产构建全部通过。
5. 不启动真实 GPU、vLLM 或优云实例；只使用 Fake/Mock 验证。

## 回滚

保留旧组件入口和 API，若新 adapter 出现问题，可按 modality 回退到原组件；不删除数据库记录、模型文件或现有任务引用。
