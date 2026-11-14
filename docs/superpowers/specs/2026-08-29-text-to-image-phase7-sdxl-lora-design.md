# 文生图阶段七设计：SDXL LoRA 微调到推理闭环

## 目标

在现有文生图数据集、模型目录、优云服务预检和 Diffusers 运行时之上，补齐一条可验证的 SDXL LoRA 微调链路：

```text
IMAGE_TEXT 数据集 → 优云训练预检/确认 → Diffusers SDXL LoRA 训练
→ Best/Final Adapter → 模型目录 → 优云推理服务 → 文生图任务与结果
```

首版不在本机执行 GPU/Diffusers 训练；本地只运行 Fake Provider、API、状态机、配置物化和前端契约测试。代码和契约测试通过后，才使用用户已授权的真实优云实例验收，并在验收结束后清理实例。

## 范围与边界

- 首个基座模型族为 SDXL，训练方式为 LoRA；QLoRA 仅保留兼容性门控，不在本阶段承诺可用。
- 训练使用 Diffusers 官方 SDXL LoRA 脚本或镜像内等价入口，不引入自维护训练算法。
- 优云训练使用独立的 SDXL LoRA 镜像，预置 Diffusers、Transformers、Accelerate、PEFT、bitsandbytes 和 CUDA 依赖。
- 训练完成后同步 Best/Final Adapter 到现有模型目录和共享模型元数据；推理服务可选择基座模型或任一可用 Adapter，但不会自动创建服务。
- 复用视觉训练的状态机语义：预检、确认、排队、单 GPU 锁、日志、指标、停止、重试和安全诊断。
- 不实现多卡训练、自动资源推荐、在线 LoRA 合并、MinIO 结果归档、公共推理路由和本机真实 GPU 验收。

## 数据与配置模型

文生图数据集沿用现有 `IMAGE_TEXT` 目录：`images/<file>` 与 `metadata.jsonl`。训练物化器将每行转换为 Diffusers caption 数据集格式，并复制图片到隔离任务目录。物化产物至少包含：

- `dataset/images/`
- `dataset/metadata.jsonl`
- `train.yaml` 或等价 JSON 配置
- `manifest.json`（源数据集哈希、样本拆分、模型 Repo、参数和文件清单）
- `output/`（训练脚本输出）

训练任务参数固定为：数据集、分辨率、Epochs/最大步数、学习率、LoRA Rank、Batch Size、梯度累积、验证比例、Seed；服务端重新校验整数/范围、像素上限和数据集必须为已发布 `IMAGE_TEXT`。

任务快照包含 `modelId`、`modelRepoId`、`datasetId`、`trainingMode`、资源选择、镜像 ID、训练参数和物化 manifest。任何重试都使用快照，不读取用户后来修改的服务配置。

## 后端组件

### `backend/image_generation_training.py`

负责纯函数式校验和确定性物化：

- `validate_image_generation_training_dataset(train_path) -> int`
- `materialize_image_generation_training_bundle(...) -> dict`
- `resolve_sdxl_lora_entrypoint(repo_id, model_path="") -> str`

该模块不创建实例、不执行命令；所有路径必须在任务根目录内，caption 和文件名必须经过现有安全校验。

### `backend/image_generation_executor.py`

定义本地/Fake/优云执行器共享的事件协议，负责：

- 校验物化 bundle 和 SDXL LoRA 配置；
- 生成受控 Diffusers 官方脚本参数；
- 接收日志、step、loss、eval loss 和 checkpoint 事件；
- 收集 Best/Final Adapter，并生成安全产物 manifest；
- 不使用 `shell=True`，不把命令、密钥或远端绝对路径写入用户可见诊断。

### `backend/image_generation_training_runtime.py`

持久化文生图微调队列和生命周期。状态至少包括：`awaiting_confirmation`、`queued`、`preparing`、`running`、`collecting`、`completed`、`failed`、`stopped`、`stopping`。运行时复用视觉训练的：

- `execution_lock` 单 GPU 互斥；
- `confirm/stop/retry/clone` 语义；
- `failureCode/failureMessage/suggestedAction` 安全诊断；
- 优云远端句柄持久化和清理失败闸门；
- 后端重启恢复为 `runtime_interrupted`。

### `backend/image_generation_training_api.py`

提供：

- `GET /api/image-generation/training/models`
- `GET /api/image-generation/training/datasets`
- `POST /api/image-generation/training/preflight`
- `POST /api/image-generation/training/materialize-preview`
- `POST /api/image-generation/training/tasks`
- `GET /api/image-generation/training/tasks` 与详情、日志、指标、诊断
- `POST /api/image-generation/training/tasks/{id}/confirm|stop|retry|clone`
- `POST /api/image-generation/training/tasks/{id}/archive|restore`
- `DELETE /api/image-generation/training/tasks/{id}`

预检必须验证：发布状态和样本数、SDXL Repo ID/模型目录、LoRA 参数、优云镜像策略、库存、价格、远端 Python/依赖策略和配置 fingerprint。预检快照默认 10 分钟有效；确认时重新验证过期或 fingerprint 变化的快照。

## Adapter 与推理绑定

训练完成或停止后，执行器只发布存在且包含可加载 LoRA 权重的 `best`、`final` 目录。每个 Adapter 元数据记录：基座 Repo/版本、实际训练路径、数据集 ID、源任务 ID、训练方式、checkpoint step、eval/train loss、manifest 哈希和本地/共享存储状态。

现有文生图模型目录扩展为：

- SDXL 基座模型：`group=base`；
- 训练产物：`group=trained`、`adapterVariant=best|final`；
- 只有基座和 Adapter lineage 均可解析、状态为 `available` 的条目才可用于优云服务。

服务创建保持“只保存配置，不创建实例”。服务的 `modelId` 可指向基座或 Adapter；若指向 Adapter，服务快照同时保存 `baseModelId`、`adapterVariant` 和 Adapter 路径。推理任务创建时重新解析服务快照，防止已归档或失效产物进入队列。

## 优云执行与错误处理

训练 Provider 采用一次性实例：创建 → SSH → 解析预置 SDXL 镜像运行时 → 上传物化 bundle → 执行官方 LoRA 脚本 → 下载 Adapter/manifest → 清理实例。优先使用镜像内完整模型和依赖；只有显式开启对应环境开关时才允许 ModelScope/Hugging Face 下载或远端安装兜底。

失败分类固定为：`instance_start_failed`、`runtime_unavailable`、`model_unavailable`、`dataset_upload_failed`、`training_failed`、`adapter_collect_failed`、`result_download_failed`、`cleanup_failed`。用户可见诊断优先 `failureMessage`，不得回退到原始 `task.error`；原始错误仅保留在兼容字段，不进入诊断 API 或 UI。

## 前端工作区

文生图页面统一为四个工作区，与视觉理解一致：

1. 数据集：沿用现有图片/Captions 上传、校验、发布和预览。
2. 微调任务：新增 SDXL LoRA 参数表单、数据集/基座模型选择、优云资源与价格、预检结果、确认弹窗、任务列表、日志、loss 曲线、Best/Final Adapter。
3. 模型管理：基座模型、训练 Adapter、lineage、可部署状态和失败原因分组展示；支持刷新和打开产物目录。
4. 推理服务：选择基座或 Adapter、Diffusers 镜像和资源，保存配置后执行启动前检查；服务启动后复用现有任务历史、结果和诊断界面。

微调表单不得在未选择已发布数据集、未选择可用 SDXL 模型、预检未通过或价格/库存未确认时提交。所有操作按钮遵循现有视觉理解图标、禁用和错误文案约定。

## 测试与验收

### 本地契约测试

- 训练数据物化、拆分、manifest 哈希、路径边界和非法参数；
- Fake 训练 Provider 从确认到完成，产生 Best/Final Adapter；
- 失败、停止、重试、清理失败和后端重启恢复；
- 预检 fingerprint/TTL、模型与 Adapter 兼容性、服务快照重解析；
- API 状态码、归档闸门、诊断安全文案和模型目录；
- 前端 TypeScript、组件源代码契约和生产构建。

### 真实优云验收

仅在上述本地测试全部通过后执行一次：创建独立 SDXL LoRA 优云实例，使用小型已发布数据集和最小步数训练，确认 Best/Final Adapter 可同步到模型目录，创建服务并完成一张图片推理。验收结束后停止/删除实例，记录任务、服务和结果摘要，不提交密钥、SSH 信息或原始远端命令。

