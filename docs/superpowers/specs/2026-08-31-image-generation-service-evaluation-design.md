# 文生图服务内生成与验证评测设计

## 背景

当前文生图推理工作区在服务列表之外单独提供“生成任务”页签。用户创建生成任务时仍需再次选择服务，生成历史也分散在全局任务页和服务详情中。文生图尚无验证评测 API、持久化任务和指标计算能力。

目标是参考现有推理服务控制台的交互方式，将生成和验证评测收进具体服务详情，使模型、Adapter、优云资源、生成任务和评测结果都围绕同一服务组织。

## 目标

- 文生图推理入口首先展示服务列表，不再展示全局“生成任务”顶层页签。
- 服务详情提供“服务概览”“图片生成”“验证评测”“运行日志”四个页签。
- 图片生成固定绑定当前服务，不再显示服务选择器。
- 验证评测复用已发布的文生图 `IMAGE_TEXT` 数据集。
- Caption 作为生成 Prompt，原图作为参考图。
- 真实计算生成成功率、失败数、平均耗时、文本-图片 CLIP 相似度和生成图-参考图 CLIP 相似度。
- 一个评测任务只申请一个优云实例，在实例内完成整批生成和指标计算，最后回收实例。
- 本地开发和自动化测试不得启动真实优云实例。

## 非目标

- 本阶段不新增评测集上传格式。
- 本阶段不增加 FID、KID、人工打分或大模型审美评分。
- 本阶段不把文生图服务改造成常驻多租户推理集群。
- 本阶段不移除现有生成任务后端接口，避免破坏已有记录和调用方。
- 本阶段不修改视觉理解或 NLP 推理服务的信息架构。

## 用户界面

### 服务列表

文生图推理入口直接渲染服务列表。删除工作区顶部的“推理服务 / 生成任务”切换。服务名称和“详细”按钮进入同一个服务详情。

### 服务详情

详情页顶部保留：

- 返回推理服务
- 服务名称、服务 ID、当前状态
- 启动前检查状态
- 启动、停止、重试、归档等现有生命周期操作

详情页正文增加页签：

1. **服务概览**：模型与 Adapter、优云资源、默认生成参数、启动前检查和近期生成摘要。
2. **图片生成**：生成表单、费用边界、任务阶段、生成结果和当前服务的任务历史。
3. **验证评测**：评测集选择与校验、评测参数、任务确认、汇总指标和逐样本结果。
4. **运行日志**：服务日志、生成任务日志和评测任务日志，按来源筛选。

### 图片生成页签

复用现有生成任务能力，但组件接收必填的 `serviceId` 和服务快照：

- 不显示“服务配置”选择器。
- 默认参数读取当前服务配置，用户可以为本次任务覆盖 Prompt、Negative Prompt、尺寸、Steps、CFG、Seed、采样器和生成数量。
- 创建后进入 `awaiting_confirmation`，不立即产生云费用。
- 确认时重新执行服务预检，并明确提示将申请优云实例。
- 历史列表仅查询当前服务；支持查看、停止、重试、复制生成、归档和删除。

现有 `ImageGenerationTasks` 应拆分为可复用的服务范围工作区，避免把全局服务加载、服务筛选和具体服务生成逻辑继续放在同一组件中。

### 验证评测页签

评测表单包含：

- 已发布文生图测试集
- 最大样本数，可空，默认全部
- 每个 Prompt 的生成数量，第一阶段固定为 1
- 尺寸、Steps、CFG、Seed 和采样器，默认继承服务配置
- “校验评测集”和“创建待确认评测”两个动作

校验结果显示：

- 数据集名称和 ID
- 总样本数和本次评测样本数
- Caption 缺失数
- 参考图片缺失或不可读数量
- 图片格式与尺寸异常
- 是否可用于评测

评测任务详情显示：

- 任务阶段与进度
- 样本总数、成功数、失败数和生成成功率
- 平均生成耗时
- 文本-图片 CLIP 相似度均值
- 生成图-参考图 CLIP 相似度均值
- 逐样本 Prompt、参考图、生成图、耗时、分项分数和失败原因
- 运行日志和优云实例清理状态

CLIP 相似度使用余弦值，保留三位小数。界面明确标注它们是辅助自动指标，不把参考图相似度等同于主观画质。

## 数据集契约

评测集直接来自平台已发布的数据集，必须满足：

- `structure == "IMAGE_TEXT"`
- `datasetType == "文生图"`
- `state == "published"`
- 每条记录具有非空 Caption
- 每条记录引用一张位于数据集媒体目录中的可读取图片

评测任务创建时保存数据集快照：数据集 ID、名称、版本（若存储层提供）、manifest 路径、媒体根目录、选中样本和样本顺序。任务执行期间不重新读取列表状态，避免数据集修改导致结果不可复现。

样本顺序使用 manifest 顺序。设置最大样本数时取前 N 条，不做随机抽样；后续需要抽样时再增加显式随机种子。

## 后端接口

### 评测集

- `GET /api/image-generation/evaluation-datasets`
  - 返回已发布且符合文生图类型的评测集目录。
- `POST /api/image-generation/evaluations/validate`
  - 输入 `datasetId`、可选 `maxSamples`。
  - 只读取和校验本地数据，不申请优云实例。

### 服务范围评测任务

- `GET /api/image-generation/services/{service_id}/evaluations`
- `POST /api/image-generation/services/{service_id}/evaluations`
- `GET /api/image-generation/services/{service_id}/evaluations/{evaluation_id}`
- `POST /api/image-generation/services/{service_id}/evaluations/{evaluation_id}/confirm`
- `POST /api/image-generation/services/{service_id}/evaluations/{evaluation_id}/stop`
- `POST /api/image-generation/services/{service_id}/evaluations/{evaluation_id}/retry`
- `GET /api/image-generation/services/{service_id}/evaluations/{evaluation_id}/logs`

创建接口只保存配置快照并返回 `awaiting_confirmation`。确认接口重新执行服务预检、评测集有效性检查、优云库存和价格检查，通过后才入队。

### 现有生成接口兼容

继续保留：

- `POST /api/image-generation/tasks`
- `GET /api/image-generation/tasks?serviceId=...`
- 现有任务生命周期和结果下载接口

前端从服务详情传入固定 `serviceId`，不要求后端立即迁移历史数据。

## 评测任务模型

评测任务至少持久化：

- `id`
- `serviceId`、`serviceName`
- `datasetId`、`datasetName`、数据集快照
- 生成参数快照
- `state`、`phase`、`progress`、`currentStep`
- `sampleCount`、`completedSamples`、`failedSamples`
- 汇总指标
- 逐样本结果
- `logs`
- `failureCode`、`failureMessage`、`suggestedAction`
- `cleanupState`、`cleanupError`
- `createdAt`、`updatedAt`、`startedAt`、`finishedAt`

状态沿用生成任务语义：

`awaiting_confirmation -> queued -> preparing -> running -> collecting -> completed`

停止和失败状态为 `stopping`、`stopped`、`failed`。失败信息优先使用结构化字段，页面不得直接暴露原始远程命令或敏感环境信息。

## 优云执行流程

1. 校验服务未归档，模型、Adapter 和资源快照仍有效。
2. 校验评测集快照和所有输入文件。
3. 创建一个优云实例并等待 SSH 就绪。
4. 优先使用服务配置的文生图镜像；依赖安装仅作为受控兜底。
5. 上传或挂载模型、Adapter 和评测 manifest。
6. 加载一次 Diffusers Pipeline，在同一进程中依次处理所有样本。
7. 每完成一个样本立即记录生成耗时、结果路径和心跳进度。
8. 使用固定的 CLIP 模型计算文本-生成图和参考图-生成图嵌入余弦相似度。
9. 写入逐样本结果和汇总指标，回传生成图片与结果清单。
10. 无论成功、失败或停止，都进入 finally 清理实例；清理失败单独记录并允许重试。

CLIP 模型通过 `XUNTUI_IMAGE_EVAL_CLIP_MODEL` 配置，默认使用平台验证过的模型。执行器启动时记录实际模型仓库和 revision，保证指标可追溯。

## 指标定义

- `successRate = completedSamples / sampleCount`
- `averageLatencyMs = 成功样本生成耗时总和 / completedSamples`
- `clipTextImageMean = 成功样本文本嵌入与生成图嵌入余弦相似度均值`
- `clipReferenceImageMean = 成功样本参考图嵌入与生成图嵌入余弦相似度均值`

单样本失败不终止整批评测；记录失败原因并继续后续样本。模型加载失败、实例异常、磁盘不足等批次级错误使任务失败。汇总指标只统计成功样本，同时保留总样本数和失败数。

第一阶段不设置自动通过阈值，避免用单一 CLIP 分数替代人工判断。界面提供排序和失败筛选，便于答辩和人工复核。

## 存储与文件

评测元数据由文生图评测运行时持久化，文件位于独立目录：

`runtime/image-generation-evaluations/{evaluation_id}/`

目录包含：

- `evaluation.json`
- `dataset-snapshot.json`
- `results.jsonl`
- `logs.txt`
- `generated/{sample_id}.png`

服务归档不删除评测结果。删除策略不在本阶段开放；先保证历史结果可追溯，后续再增加显式归档和删除规则。

## 错误处理

- 数据集无效：创建评测前阻断，不创建云实例。
- 服务预检失效：确认时阻断并展示具体检查项。
- 单样本生成失败：记录样本失败并继续。
- CLIP 单样本计算失败：该样本生成结果保留，指标标为不可用并计入评测告警。
- 批次执行失败：任务进入 `failed`，保留已完成样本。
- 实例清理失败：保留评测终态并设置 `cleanupState=failed`，提供重试清理动作。
- 页面错误信息优先使用 `failureMessage` 和 `suggestedAction`，不回退展示可能包含敏感信息的原始异常。

## 前端组件边界

- `ImageGenerationInferenceWorkspace`：只负责渲染文生图服务工作区，不再维护顶层生成任务页签。
- `ImageGenerationService`：维护服务 list/create/detail 和详情页签状态。
- `ImageGenerationTasks`：改为服务范围组件，接收 `serviceId`、服务名称和默认参数。
- 新增 `ImageGenerationEvaluation`：负责评测集目录、校验、任务创建、轮询、汇总和逐样本结果。
- 结果图片继续通过后端受控下载接口提供，不在前端拼接本地文件路径。

## 测试与验收

### 后端测试

- 只列出已发布的文生图 `IMAGE_TEXT` 数据集。
- 校验缺失 Caption、缺失图片、非法路径和不可读图片。
- 评测任务始终绑定 URL 中的服务 ID，忽略客户端伪造的其他服务 ID。
- 创建任务不申请实例，确认任务才执行预检和入队。
- 假 Provider 下完成多样本生成、指标聚合和文件回传。
- 单样本失败不终止批次，批次级失败进入失败状态。
- 停止、重试、心跳、结构化诊断和 finally 清理实例。
- CLIP 指标使用可注入的评分器测试，不在单元测试下载真实模型。

### 前端测试

- 文生图推理入口不再显示顶层“生成任务”页签。
- 服务详情包含四个页签。
- 图片生成不出现服务选择器，所有请求使用当前服务 ID。
- 评测集只能选择已发布且有效的数据集。
- 未校验或校验失败时不能创建评测任务。
- 评测详情展示汇总指标、逐样本结果和失败原因。
- 桌面和移动端无整页横向溢出，长 Prompt、路径和错误信息可换行。

### 本地验收

- 使用假优云 Provider 和固定的本地图片结果跑通创建、确认、生成、评分、汇总和清理流程。
- 浏览器验证服务列表、服务详情四页签、生成任务和评测任务完整交互。
- 不启动真实优云实例，不下载大型模型，不执行真实 GPU 推理。

## 迁移顺序

1. 将现有生成任务组件改为服务范围组件，并放入服务详情。
2. 删除顶层生成任务页签，保留后端兼容接口。
3. 实现评测集目录和校验接口。
4. 实现评测任务存储、状态机和假执行器测试。
5. 接入优云批处理执行器和可配置 CLIP 评分器。
6. 实现服务详情验证评测页签与响应式结果页面。
7. 完成本地全链路测试后，再单独申请真实优云验收。
