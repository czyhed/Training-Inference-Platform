# 训推平台：LLaMA-Factory 本地/远程训练改造需求文档

- 文档版本：v1.2
- 编制日期：2026-08-21
- 项目：训推平台
- 当前代码目录：`D:\训推平台`
- 目标部署方式：GitHub + Drone + CPU 服务器
- 远程 GPU 平台：优云智算（CompShare）
- 训练框架：LLaMA-Factory

---

## 1. 文档目的

本文档用于明确训推平台下一阶段的产品、技术、数据、部署和验收要求，作为后续独立开发会话的统一上下文。

> **重要更正：LLaMA-Factory 训练的正式运行环境是部署后的 CPU 服务器。开发机上的本地运行只用于开发调试和冒烟验证，不是正式训练环境。**

本阶段的核心目标不是立即完成完整云 GPU 平台，而是先在正式部署的 CPU 服务器上建立一条可以持续演进的 LLaMA-Factory 训练链路。开发机可以提前跑通同一套流程，但必须以 CPU 服务器部署环境作为最终验收环境：

```text
CPU 服务器
  → 小模型
  → LLaMA-Factory
  → 本地训练
  → PostgreSQL 任务状态
  → MinIO 数据集/产物
  → 实时日志
```

在 CPU 服务器训练链路稳定后，再增加：

```text
优云 GPU 实例
  → 远程文件准备
  → 远程启动 LLaMA-Factory
  → 实时日志和训练指标
  → 下载/上传训练产物
  → 自动释放 GPU 实例
```

---

## 2. 当前项目基线

当前项目已经具备以下能力：

- Next.js + React 前端。
- FastAPI 后端。
- SQLite 任务、数据集、日志和推理服务原型。
- 当前训练框架为 Unsloth + TRL，训练入口主要位于 `backend/train_sft.py`。
- 支持文本 JSONL 数据集、SFT/DPO、LoRA/QLoRA 等现有参数模型。
- 已存在本地训练任务状态、训练日志、Loss 指标和 Adapter 产物逻辑。
- 已存在模型文件上传到 PostgreSQL + MinIO 的逻辑。
- 已存在模型管理页面和模型资产管理逻辑。
- 已存在本地 vLLM 推理服务原型。
- 当前本地启动脚本 `scripts/run_backend.py` 依赖 Windows + WSL，不可直接作为 CPU Linux 服务器生产启动方式。
- 前端部分 API 地址仍硬编码为 `http://127.0.0.1:8010/api`。
- 当前项目还没有正式的 Git 仓库、Drone 配置、Dockerfile 或生产部署配置。

当前需要保留的能力：

- 数据集管理页面。
- 模型管理页面的既有交互和资产展示逻辑。
- 训练任务的创建、详情、停止、重试、克隆、删除。
- 训练产物的 Best Adapter、Final Adapter、Merged Model 和指标记录。
- 推理服务与训练产物之间的关联关系。

当前需要替换或重构的能力：

- Unsloth/TRL 训练执行层替换为 LLaMA-Factory 执行层。
- SQLite 训练任务存储迁移到 PostgreSQL。
- SQLite 推理服务存储迁移到 PostgreSQL。
- 固定本地路径改为环境变量。
- 本地 WSL 启动方式改为可部署到 CPU Linux 服务器的启动方式。
- 前端轮询日志升级为 WebSocket 实时日志，同时保留 REST 降级方案。

---

## 3. 目标用户流程

### 3.1 CPU 服务器本地训练（正式环境）

用户在训练页面：

1. 选择数据集。
2. 选择基础模型。
3. 选择 LLaMA-Factory。
4. 选择执行位置为“本地训练”。

> 此处的“本地”指部署后的 CPU 服务器，不指开发机。

5. 选择 SFT 或 DPO。
6. 选择 LoRA 或 QLoRA；CPU 阶段优先支持小模型和 LoRA。
7. 填写训练参数。
8. 创建训练任务。
9. 任务进入排队、准备、训练状态。
10. 在训练详情页查看进度、Loss、日志和错误。
11. 训练结束后生成 Adapter、指标和可选的合并模型。
12. 产物写入 MinIO，并关联到模型管理和训练任务。

### 3.2 远程优云 GPU 训练

用户在训练页面：

1. 选择执行位置为“远程优云 GPU”。
2. 选择 GPU 规格、镜像或已有实例策略。
3. 创建训练任务。
4. 平台调用优云接口检查资源和创建/启动实例。
5. 等待实例进入可用状态。
6. 准备远程工作目录、训练配置和数据集。
7. 通过 SSH 或 Node.js Agent 启动 LLaMA-Factory。
8. 远程训练日志、状态、指标实时回传平台。
9. 前端在训练详情页的黑色日志窗口中实时展示日志。
10. 训练完成后校验并上传/下载训练产物。
11. 训练任务写入完成状态。
12. 根据任务策略停止或删除优云实例。

---

## 4. 业务范围

### 4.1 本阶段必须实现

- LLaMA-Factory 作为唯一训练框架的第一版适配。
- CPU 服务器上的小模型本地训练，作为第一条正式可用训练链路。
- 开发机本地运行只用于冒烟测试，必须与 CPU 服务器使用相同的配置生成器和执行器接口。
- CPU 服务器上的训练任务写入 PostgreSQL。
- CPU 服务器上的训练日志和 Loss 指标写入 PostgreSQL。
- 数据集从 MinIO 导入或按 MinIO 对象位置读取。
- 训练产物上传到 MinIO。
- 前端实时训练日志黑框。
- REST 状态查询和 WebSocket 日志通道。
- 所有路径、端口、数据库和对象存储配置环境化。
- GitHub + Drone + tag 触发部署到 CPU 服务器；Drone 从预先上传的 Git bundle 检出源码。
- 为远程优云训练预留统一执行器接口。

### 4.2 第二阶段必须实现

- 优云 GPU 实例创建、启动、状态查询、停止和删除。
- 优云实例库存检查。
- 远程训练数据和配置准备。
- 远程 LLaMA-Factory 启动。
- 远程任务停止。
- 远程训练产物回传。
- 远程任务异常清理。
- Node.js Agent 心跳、任务控制和日志回传。
- 前端展示远程实例信息和 GPU 监控。

### 4.3 后续范围

- 优云自定义 GPU 镜像。
- 镜像内置 Python、CUDA、PyTorch、LLaMA-Factory、Node.js Agent 和常用模型缓存。
- 多 GPU、多任务并发调度。
- Redis 或其他持久化队列。
- 训练任务自动重试和断点恢复。
- Kubernetes/GPU 集群调度。
- 多租户、用户、角色和权限。
- 远程推理服务部署。

### 4.4 本阶段不做

- 不实现多租户权限体系。
- 不实现图像训练和视觉语言模型训练。
- 不实现完整的 Kubernetes 调度。
- 不把优云 AccessKey 暴露给浏览器。
- 不允许用户提交任意 Shell 命令。
- 不把基础模型文件默认从本地上传到远程 GPU；优先使用远程缓存、MinIO 下载或镜像预置。

---

## 5. 统一训练执行架构

训练页面不直接绑定具体训练框架和机器类型，后端提供统一训练执行器接口。

建议抽象为：

```python
class TrainingExecutor:
    async def prepare(self, task): ...
    async def start(self, task): ...
    async def stop(self, task): ...
    async def status(self, task): ...
    async def events(self, task): ...
    async def collect_artifacts(self, task): ...
    async def cleanup(self, task): ...
```

第一版实现：

```text
LocalLlamaFactoryExecutor
```

第二阶段实现：

```text
YouyunLlamaFactoryExecutor
```

执行器公共输入：

```json
{
  "taskId": "ft-xxx",
  "framework": "llamafactory",
  "taskType": "SFT",
  "tuningMode": "lora",
  "model": "Qwen/Qwen2.5-0.5B-Instruct",
  "datasetId": "dataset-xxx",
  "parameters": {},
  "executionMode": "local"
}
```

执行器必须输出统一的：

- 任务状态。
- 任务阶段。
- 日志事件。
- 训练指标事件。
- GPU/CPU 资源事件。
- 错误事件。
- 训练产物清单。
- 清理结果。

---

## 6. LLaMA-Factory 适配要求

### 6.1 训练配置转换

平台前端继续使用业务友好的参数名，后端转换为 LLaMA-Factory YAML 或 JSON 配置。

平台参数与 LLaMA-Factory 参数至少需要覆盖：

| 平台参数 | LLaMA-Factory 配置 |
|---|---|
| `model` | `model_name_or_path` |
| `taskType=SFT` | `stage=sft` |
| `taskType=DPO` | `stage=dpo` |
| `tuningMode=lora` | `finetuning_type=lora` |
| `tuningMode=qlora` | `quantization_bit` 或对应量化配置 |
| `batchSize` | `per_device_train_batch_size` |
| `gradientAccumulation` | `gradient_accumulation_steps` |
| `learningRate` | `learning_rate` |
| `epochs` | `num_train_epochs` |
| `maxSequenceLength` | `cutoff_len` |
| `loraRank` | `lora_rank` |
| `loraAlpha` | `lora_alpha` |
| `loraDropout` | `lora_dropout` |
| `loggingSteps` | `logging_steps` |
| `validationSplit` | `val_size` 或显式验证集配置 |
| `outputName` | `output_dir` |
| `seed` | `seed` |

### 6.2 数据集适配

平台需要生成 LLaMA-Factory 可识别的数据集配置，包括：

- 数据集 ID。
- 数据集版本。
- MinIO bucket。
- MinIO object prefix。
- 本地物化路径。
- 数据集格式。
- 对话模板。
- SFT/DPO 字段映射。
- 训练集、验证集、测试集的对象清单。
- manifest 和哈希。

不允许仅使用数据集显示名称作为训练输入，必须使用数据集 ID + 版本或 manifest。

### 6.3 训练命令

最终执行命令应类似：

```bash
llamafactory-cli train /workspace/config/train.yaml
```

命令必须由平台后端生成，不能直接执行前端传入的原始 Shell 字符串。

---

## 7. 任务状态机

训练任务状态必须区分资源准备、文件准备、训练和产物处理阶段。

建议状态：

```text
queued              排队中
provisioning        准备远程资源
instance_starting   GPU 实例启动中
agent_connecting    等待 Agent 连接
preparing           准备工作目录和配置
uploading           上传数据/配置
running             训练中
stopping            停止训练中
collecting          收集训练产物
downloading         下载训练产物
uploading_artifact  上传产物到 MinIO
cleaning            清理远程资源
completed           已完成
failed              失败
stopped             已停止
unknown             远程状态未知
```

前端可以将内部状态映射为用户状态：

```text
准备中
运行中
停止中
已完成
已失败
已停止
```

每个状态变更必须写入 `training_task_events`，包括：

- 旧状态。
- 新状态。
- 事件时间。
- 事件来源。
- 任务阶段。
- 错误信息。
- 远程实例 ID。

---

## 8. 实时日志和 WebSocket

### 8.1 后端日志链路

```text
本地训练进程或远程 Agent
  ↓
FastAPI 事件接收层
  ↓
写入 PostgreSQL training_task_logs
  ↓
广播到浏览器 WebSocket
  ↓
训练详情页黑色日志窗口
```

### 8.2 WebSocket 接口

建议接口：

```text
GET /api/tasks/{task_id}/stream
```

客户端连接后：

1. 服务端先发送最近的历史日志。
2. 服务端发送当前任务状态。
3. 服务端发送后续实时日志。
4. 断线后客户端自动重连。
5. 客户端携带最后收到的 `seq`，服务端补发缺失日志。

日志事件格式：

```json
{
  "type": "log",
  "taskId": "ft-xxx",
  "seq": 182,
  "timestamp": "2026-08-21T10:30:00+08:00",
  "level": "INFO",
  "stage": "training",
  "message": "正在加载模型"
}
```

指标事件格式：

```json
{
  "type": "metric",
  "taskId": "ft-xxx",
  "seq": 183,
  "step": 120,
  "epoch": 0.8,
  "trainLoss": 1.234,
  "evalLoss": 1.456,
  "learningRate": 0.0002
}
```

状态事件格式：

```json
{
  "type": "state",
  "taskId": "ft-xxx",
  "state": "running",
  "stage": "training",
  "progress": 35,
  "currentStep": "Epoch 1 / 3 · Step 120"
}
```

### 8.3 前端黑框要求

训练详情页必须在训练进度下方嵌入黑色终端区域：

- 黑色背景，等宽字体。
- 自动滚动到底部。
- 支持暂停自动滚动。
- 支持复制日志。
- 显示连接状态。
- 显示日志行数。
- 显示断线重连提示。
- 页面刷新后恢复历史日志。
- 任务结束后保留完整日志。
- WebSocket 不可用时自动降级到 REST 轮询。

---

## 9. Node.js Agent 要求

Node.js Agent 部署在远程 GPU 实例内，目标是后续随镜像自动启动。

### 9.1 Agent 职责

- 启动时读取环境变量。
- 使用一次性任务 Token 主动连接平台 WebSocket。
- 上报 Agent ID、版本、实例 ID、GPU 信息和工作目录。
- 接收训练任务。
- 创建任务工作目录。
- 下载训练配置和数据。
- 启动 `llamafactory-cli train`。
- 读取 stdout 和 stderr。
- 上报日志、状态和指标。
- 接收停止命令。
- 发送安全停止信号。
- 打包和上传训练结果。
- 训练完成后上报产物 manifest。
- 断线自动重连。
- 避免同一个 Agent 并发执行未经授权的多个任务。

### 9.2 Agent 安全要求

- 不接收任意 Shell 字符串。
- 只接收平台生成的结构化训练任务。
- 任务 Token 有效期有限。
- Token 绑定任务 ID 和 Agent ID。
- 工作目录必须限制在 Agent 配置的根目录下。
- 禁止任务覆盖其他任务目录。
- 禁止执行 `rm -rf`、管道、重定向、命令替换等危险操作。
- Agent 与平台通信必须使用 TLS WebSocket。

### 9.3 第一版兼容方案

在 Agent 完成前，允许使用 SSH 作为临时执行器：

```text
Youyun SSH Executor
```

但 SSH 执行器必须遵循与 Agent 相同的任务状态、日志、产物和清理协议，后续可以无缝替换。

---

## 10. 优云远程 GPU Provider 要求

新增优云 Provider，不允许将优云调用逻辑散落在 `backend/app.py` 的路由中。

建议拆分：

```text
backend/providers/youyun/
  client.py
  models.py
  signer.py
  instance_manager.py
  storage.py
  ssh.py
```

### 10.1 必须支持的能力

- 配置 API Base URL、区域和可用区。
- 公钥/私钥签名认证。
- GPU 规格查询。
- 资源库存检查。
- 创建实例。
- 查询实例列表。
- 查询单个实例。
- 启动实例。
- 关闭实例。
- 删除实例。
- 获取实例 IP 和 SSH 信息。
- 查询实例监控数据。
- 选择镜像。
- 后续支持自定义镜像 ID。
- 文件上传/下载或 US3 挂载。
- API 超时、重试和错误码处理。

### 10.2 远程实例生命周期

```text
检查库存
  ↓
创建实例
  ↓
轮询实例状态
  ↓
获取 SSH 或 Agent 连接信息
  ↓
实例注册
  ↓
执行训练
  ↓
收集产物
  ↓
关闭实例
  ↓
按策略删除实例
```

### 10.3 资源释放策略

训练结束、失败、停止、超时、Agent 断线时，都必须执行清理：

1. 尝试停止训练进程。
2. 等待产物保存。
3. 上传或下载已完成产物。
4. 关闭优云实例。
5. 如果是临时实例，删除实例。
6. 保存清理结果和失败原因。

不能因为训练失败就跳过 GPU 释放。

---

## 11. 数据库要求

### 11.1 SQLite 保留范围

当前阶段 SQLite 只保留：

- 数据集本地索引和开发缓存。
- 模型管理页面的本地资产缓存和展示状态，保持既有模型管理逻辑。
- 必要的本地兼容数据。

SQLite 不再作为新训练任务和新推理服务的权威数据源。

### 11.2 PostgreSQL 范围

必须迁移到 PostgreSQL：

- 训练任务。
- 训练任务事件。
- 训练日志。
- 训练指标。
- 训练产物关联。
- 推理服务。
- 推理服务日志。
- 推理服务运行指标。
- 推理服务命令使用记录。
- 现有模型文件和文件条目元数据。

### 11.3 建议表

```text
training_tasks
training_task_events
training_task_logs
training_task_metrics
training_task_artifacts
inference_services
inference_service_logs
inference_service_metrics
inference_command_usage
model_files
model_file_entries
```

### 11.4 迁移要求

- 使用版本化数据库迁移脚本。
- 不允许应用启动时隐式破坏性改表。
- 现有 SQLite 任务可以生成一次性迁移脚本。
- 迁移后旧 SQLite 任务至少可以只读查看。
- 新任务只写 PostgreSQL。
- 新推理服务只写 PostgreSQL。
- 任务 ID 必须保持稳定，不能因迁移改变。
- 训练任务日志顺序必须保持。
- Loss 指标的 step、epoch、train/eval loss 必须保持。

---

## 12. MinIO 数据集和产物要求

### 12.1 数据集对象结构

建议：

```text
/datasets/{dataset_id}/{version}/manifest.json
/datasets/{dataset_id}/{version}/train.jsonl
/datasets/{dataset_id}/{version}/validation.jsonl
/datasets/{dataset_id}/{version}/test.jsonl
/datasets/{dataset_id}/{version}/challenge.jsonl
```

### 12.2 训练产物对象结构

建议：

```text
/models/{model_id}/...
/tasks/{task_id}/config/train.yaml
/tasks/{task_id}/logs/trainer.log
/tasks/{task_id}/metrics/train_metrics.json
/tasks/{task_id}/artifacts/adapter/...
/tasks/{task_id}/artifacts/final_adapter/...
/tasks/{task_id}/artifacts/merged/...
/tasks/{task_id}/manifest.json
```

### 12.3 文件完整性

每个远程任务必须生成 manifest，至少包含：

- 相对路径。
- 文件大小。
- MD5 或 SHA-256。
- 文件类型。
- 生成时间。
- 任务 ID。
- 基础模型 ID。
- 数据集 ID 和版本。

产物进入模型管理前必须校验 manifest。

---

## 13. 环境变量要求

所有部署环境相关配置必须从环境变量读取。

### 13.1 应用运行配置

```env
APP_ENV=production
XUNTUI_BACKEND_HOST=0.0.0.0
XUNTUI_BACKEND_PORT=8010
XUNTUI_CORS_ORIGINS=https://example.com
NEXT_PUBLIC_API_BASE_URL=/api
```

### 13.2 PostgreSQL

```env
POSTGRES_DSN=...
POSTGRES_POOL_MIN_SIZE=...
POSTGRES_POOL_MAX_SIZE=...
```

### 13.3 MinIO

```env
MINIO_ENDPOINT=...
MINIO_ACCESS_KEY=...
MINIO_SECRET_KEY=...
MINIO_BUCKET=...
MINIO_SECURE=false
MODEL_UPLOAD_USER=...
```

### 13.4 本地文件和运行目录

```env
XUNTUI_RUNTIME_ROOT=/data/xuntui/runtime
XUNTUI_DATASET_ROOT=/data/xuntui/datasets
XUNTUI_ARTIFACT_ROOT=/data/xuntui/artifacts
XUNTUI_MODELSCOPE_CACHE=/data/xuntui/modelscope
XUNTUI_TRAINING_WORKDIR=/data/xuntui/workspaces
```

### 13.5 Python 和训练框架

```env
XUNTUI_TRAINING_PYTHON=/opt/venvs/llamafactory/bin/python
XUNTUI_LLAMА_FACTORY_COMMAND=llamafactory-cli
```

实际变量名不能含有不可识别字符，正式实现时统一使用 ASCII，例如：

```env
XUNTUI_LLAMA_FACTORY_COMMAND=llamafactory-cli
```

### 13.6 优云

```env
YOUYUN_BASE_URL=...
YOUYUN_PUBLIC_KEY=...
YOUYUN_PRIVATE_KEY=...
YOUYUN_REGION=...
YOUYUN_ZONE=...
YOUYUN_GPU_TYPE=...
YOUYUN_IMAGE_ID=...
YOUYUN_CHARGE_TYPE=...
YOUYUN_DEFAULT_INSTANCE_POLICY=stop
```

### 13.7 WebSocket/Agent

```env
AGENT_WS_BASE_URL=wss://platform.example.com
AGENT_TOKEN_TTL_SECONDS=900
AGENT_HEARTBEAT_SECONDS=10
AGENT_RECONNECT_SECONDS=5
```

---

## 14. GitHub + Drone 部署要求

### 14.1 触发策略

- 普通分支 push：执行测试和构建，不部署。
- Pull Request：执行测试和构建，不部署。
- 版本 tag：执行完整构建并部署 CPU 服务器。
- 只允许符合版本规范的 tag 部署，例如 `v*`。

### 14.2 Drone 流水线阶段

```text
checkout
  ↓
安装 Node 依赖
  ↓
安装 Python 依赖
  ↓
TypeScript 检查
  ↓
Python 单元测试
  ↓
Next.js production build
  ↓
构建 Docker 镜像或发布压缩包
  ↓
推送镜像/制品
  ↓
SSH 或部署代理发布到 CPU 服务器
  ↓
执行数据库迁移
  ↓
重启服务
  ↓
调用健康检查
```

### 14.3 CPU 服务器启动要求

生产服务器不能依赖 Windows、WSL 或 `scripts/run_backend.py`。

必须提供 Linux 启动方案：

- Docker Compose，或
- systemd + Python venv + Node.js。

建议第一版使用 Docker Compose，至少包含：

```text
frontend
backend
```

PostgreSQL 和 MinIO 如果由外部服务提供，则通过环境变量连接，不必强制放入本项目 Compose。

### 14.4 Drone Secrets

以下信息只能通过 Drone Secrets 或服务器密钥管理注入：

- PostgreSQL DSN。
- MinIO AccessKey/SecretKey。
- 优云公钥/私钥。
- SSH 部署密钥。
- Agent 签发密钥。
- 生产域名和证书相关信息。

`.env` 不得提交到 GitHub，`.env.example` 只能包含占位符。

---

## 15. 后端 API 要求

### 15.1 训练任务

保留并扩展：

```text
GET    /api/tasks
POST   /api/tasks
GET    /api/tasks/{task_id}
POST   /api/tasks/{task_id}/stop
POST   /api/tasks/{task_id}/retry
POST   /api/tasks/{task_id}/clone
DELETE /api/tasks/{task_id}
GET    /api/tasks/{task_id}/logs
GET    /api/tasks/{task_id}/metrics
WS     /api/tasks/{task_id}/stream
```

创建任务新增字段：

```json
{
  "framework": "llamafactory",
  "executionMode": "local|remote",
  "provider": "local|youyun",
  "gpuProfile": "optional",
  "imageId": "optional",
  "instancePolicy": "reuse|create|stop|delete",
  "datasetVersion": "optional but recommended"
}
```

### 15.2 优云资源

建议增加内部 Provider API：

```text
GET  /api/providers/youyun/profiles
GET  /api/providers/youyun/availability
GET  /api/providers/youyun/instances
POST /api/providers/youyun/instances
GET  /api/providers/youyun/instances/{id}
POST /api/providers/youyun/instances/{id}/start
POST /api/providers/youyun/instances/{id}/stop
DELETE /api/providers/youyun/instances/{id}
```

前端是否开放这些接口可以后续决定，第一版也可以只让训练任务内部调用。

### 15.3 Agent

建议：

```text
WS /api/agents/connect
POST /api/tasks/{task_id}/agent-token
```

Agent 事件：

```text
hello
heartbeat
ready
log
metric
state
artifact
stopped
failed
```

---

## 16. 前端要求

训练创建页新增：

- 训练框架选择，第一版默认 LLaMA-Factory。
- 执行位置：本地训练/远程优云 GPU。
- GPU 规格选择，仅远程模式显示。
- 镜像选择，第二阶段启用。
- 实例策略：复用、创建后停止、创建后删除。

训练详情页新增：

- 训练阶段。
- 执行位置。
- Provider。
- 远程实例 ID。
- Agent 连接状态。
- GPU 型号和显存。
- 实时日志黑框。
- WebSocket 连接状态。
- 日志重连按钮。
- 远程资源清理状态。

当前模型管理页面交互保持不变，后端内部改为读取新的任务和产物关联。

---

## 17. 错误处理和恢复

必须覆盖：

- 优云库存不足。
- 创建实例失败。
- 实例启动超时。
- 实例 IP 未返回。
- SSH 连接失败。
- Agent 未连接。
- 文件上传失败。
- 文件下载失败。
- MinIO 不可用。
- PostgreSQL 不可用。
- LLaMA-Factory 命令不存在。
- Python 环境不完整。
- CUDA/GPU 不可用。
- 训练进程异常退出。
- 训练日志解析失败。
- 训练产物缺失。
- manifest 校验失败。
- 停止任务超时。
- GPU 实例释放失败。

每种错误必须保存：

- 错误阶段。
- 错误码或原始错误。
- 用户可读消息。
- 是否可重试。
- 是否已经释放资源。
- 远程实例 ID。

---

## 18. 安全要求

- 优云密钥只在后端使用。
- 浏览器不直接调用优云 API。
- Agent 使用短期任务 Token。
- WebSocket 必须使用 TLS。
- 训练命令使用结构化参数生成。
- 禁止前端传任意 Shell。
- 上传下载文件必须绑定 task_id。
- 产物必须校验 manifest。
- 任务工作目录必须隔离。
- 远程实例必须有最大存活时间。
- 任务异常时必须自动释放 GPU。
- `.env`、私钥、数据库密码不能进入 Git。
- Git 历史提交需要检查敏感信息。
- 生产 CORS 不能继续使用全开放配置。

---

## 19. 验收标准

### 19.1 CPU 服务器正式训练验收

- 可以在 CPU Linux 服务器启动前后端。
- 可以通过页面创建 LLaMA-Factory SFT 任务。
- 使用小模型和小数据集成功训练。
- 任务状态写入 PostgreSQL。
- 日志写入 PostgreSQL。
- Loss 指标写入 PostgreSQL。
- 前端可以实时或准实时看到日志。
- 页面刷新后日志不丢失。
- 任务完成后生成 Adapter 和 metrics。
- 产物上传 MinIO，并有 manifest。
- 模型管理页面能看到训练产物关联。
- 训练失败时任务显示明确错误。
- 点击停止后训练进程能够退出。

### 19.2 开发机冒烟验收

开发机验证只要求：

- 能启动同一套 LLaMA-Factory 配置生成器。
- 能使用极小模型或测试替身完成命令级冒烟。
- 能生成任务、日志和指标。
- 能验证 WebSocket 日志格式。
- 能验证失败、停止和产物清单逻辑。
- 不要求开发机具备 CPU 服务器的生产目录、域名、Drone 或完整资源配置。

### 19.3 远程优云验收

- 能检查目标 GPU 规格库存。
- 能自动创建 GPU 实例。
- 能等待实例进入 Running。
- 能取得 SSH 或 Agent 连接信息。
- 能创建远程任务工作目录。
- 能上传配置和数据，或从 MinIO 下载数据。
- 能启动 LLaMA-Factory。
- 能将远程日志回传到平台。
- 前端黑框能实时显示日志。
- 浏览器断线重连后能继续显示日志。
- 能停止远程训练。
- 能收集和校验产物。
- 能将产物写入 MinIO。
- 能关闭/删除优云实例。
- 失败、停止和超时场景也能释放实例。

### 19.4 Drone 部署验收

- GitHub 普通 push 不触发生产部署。
- GitHub 版本 tag 触发 Drone 部署。
- Drone 能完成前端构建和后端测试。
- Drone 不读取仓库中的真实 `.env`。
- 生产配置从 Secrets/环境变量注入。
- CPU 服务器不依赖 Windows 或 WSL。
- 部署后自动执行健康检查。
- 部署失败时保留旧版本或可以回滚。
- 服务重启后 PostgreSQL 任务和推理服务记录仍然存在。

---

## 20. 实施阶段

### 阶段一：配置和存储基础

1. 新增 PostgreSQL 迁移系统。
2. 创建训练任务、日志、指标、事件表。
3. 创建推理服务、日志、指标表。
4. 把新训练任务改为 PostgreSQL。
5. 把新推理服务改为 PostgreSQL。
6. 保持模型管理页面原有行为。
7. 整理所有路径和连接环境变量。
8. 增加 Linux 启动方式。

### 阶段二：LLaMA-Factory 开发机冒烟链路

1. 增加 LLaMA-Factory 参数模型。
2. 增加平台配置到 YAML 的转换器。
3. 增加统一的本地 LLaMA-Factory 执行器。
4. 在开发机使用小模型和小 JSONL 完成冒烟训练。
5. 保存日志、指标和产物。
6. 产物写入 MinIO。
7. 增加 REST 日志查询。
8. 增加 WebSocket 日志流。
9. 验证开发机冒烟链路，但不把开发机结果作为正式部署验收。

### 阶段三：Drone 部署到 CPU 服务器

1. 初始化 GitHub 仓库。
2. 增加 `.drone.yml`。
3. 增加 Dockerfile 或 Compose。
4. 增加测试和构建步骤。
5. 增加 tag 部署条件。
6. 使用 Drone Secrets 注入配置。
7. 部署到 CPU 服务器。
8. 执行健康检查和回滚验证。

### 阶段四：CPU 服务器正式链路验收

1. 在目标 CPU 服务器上使用生产配置启动前端和后端。
2. 从页面创建 LLaMA-Factory 训练任务。
3. 使用小模型和小数据集完成一次正式训练。
4. 验证 PostgreSQL 任务、日志、指标和推理服务数据。
5. 验证 MinIO 数据集读取和训练产物写入。
6. 验证服务重启后任务数据不丢失。
7. 验证 Drone tag 发布后的健康检查和回滚。
8. 完成 CPU 服务器训练验收后，才进入优云 GPU 开发。

### 阶段五：优云 SSH 远程训练

1. 增加优云 API 客户端。
2. 增加库存检查。
3. 增加创建/启动/查询/停止/删除实例。
4. 增加 SSH/SFTP 文件传输。
5. 增加远程 LLaMA-Factory 执行器。
6. 增加日志文件同步。
7. 增加远程产物下载和校验。
8. 增加异常资源清理。

### 阶段六：优云单实例多卡 DDP

1. 支持在同一台优云实例中选择实际可售的多 GPU 规格。
2. 使用 LLaMA-Factory / PyTorch DDP 在同一实例内启动多进程训练。
3. 聚合多 rank 日志、指标、退出状态和 GPU 信息。
4. 确保检查点和最终产物只由 rank 0 收集、回传和登记。
5. 完成单实例多卡真实训练与失败清理验收后，才评估多实例多节点集群。

### 阶段七：Node.js Agent 和 WebSocket

1. 创建 Agent 项目。
2. 实现 Agent 注册和心跳。
3. 实现结构化训练命令接收。
4. 实现 stdout/stderr 读取。
5. 实现指标解析和事件上报。
6. 实现安全停止。
7. 实现断线重连。
8. 实现 Agent 镜像启动。
9. 将 SSH 执行器切换为 Agent 执行器。

### 阶段八：GPU 镜像

1. 制作优云自定义镜像。
2. 预装 CUDA、PyTorch、LLaMA-Factory、Node.js Agent。
3. 可选预置基础模型缓存。
4. 启动实例后 Agent 自动上线。
5. 训练任务直接复用镜像环境。
6. 验证镜像版本和依赖锁定。

---

## 21. 需要注意的项目级问题

- 当前 `.env` 中存在数据库和 MinIO 凭据，禁止推送到 GitHub；正式接入前应检查 Git 历史并考虑轮换凭据。
- 当前项目目录还没有 Git 仓库和 Drone 配置。
- 当前 `scripts/run_backend.py` 是 Windows/WSL 本地开发脚本，必须和生产 Linux 启动脚本分离。
- 前端 API 地址需要从硬编码改成环境变量或同源 `/api` 反向代理。
- 当前模型文件 PostgreSQL 逻辑可以继续使用，但训练任务和推理服务需要新增 PG 持久化层。
- SQLite 数据集保留时必须定位为索引/缓存，MinIO 才应逐步成为数据集文件源。
- 远程 GPU 不能默认每次重复上传基础模型，优先使用远程 ModelScope 缓存、MinIO 下载或优云镜像。
- 远程实例必须有最大存活时间，避免任务失败后持续计费。
- 训练日志不能只保存在 WebSocket 内存中，必须先持久化再广播。
- 训练任务必须使用 manifest 和哈希校验，避免下载不完整产物进入模型管理。

---

## 22. 待补充的部署参数

开始实现优云 Provider 前，还需要确定：

- 优云 API 使用的区域和可用区。
- 目标 GPU 型号和显存。
- 计费模式：按量、包月或其他。
- 使用平台镜像还是自定义镜像。
- 实例系统盘和数据盘大小。
- 是否允许实例访问外网。
- 是否使用优云 US3，还是直接访问项目 MinIO。
- 优云实例是否有固定公网 IP。
- SSH 登录端口和认证方式。
- GPU 训练实例默认完成后关闭还是删除。
- CPU 服务器的部署目录。
- CPU 服务器是否使用 Docker。
- PostgreSQL 和 MinIO 是外部服务还是由部署环境提供。
- GitHub 仓库地址和 Drone Server 地址。
- Drone 是否有 Docker 构建权限。

---

## 23. 最终目标

最终系统应满足：

```text
用户创建训练任务
  ↓
选择 LLaMA-Factory
  ↓
选择 CPU 服务器本地训练或远程优云 GPU
  ↓
平台生成统一训练配置
  ↓
任务、日志、指标写入 PostgreSQL
  ↓
数据集从 MinIO 获取
  ↓
本地或远程执行训练
  ↓
前端实时显示状态、Loss、GPU 监控和黑色日志终端
  ↓
训练产物校验后写入 MinIO
  ↓
模型管理保持既有使用方式
  ↓
推理服务从 PostgreSQL 读取配置
  ↓
代码通过 GitHub + Drone + tag 部署到 CPU 服务器
  ↓
后续使用优云镜像快速启动 GPU 训练实例
```

---

## 24. 参考文档

- [优云 GPU 操作示例](https://www.compshare.cn/docs/gpus/operationexample)
- [优云创建 GPU 资源](https://www.compshare.cn/docs/gpus/instance/createcompshareinstance)
- [优云实例查询](https://www.compshare.cn/docs/gpus/instance/describecompshareinstance)
- [优云实例监控](https://www.compshare.cn/docs/gpus/instance/getcompshareinstancemonitor)
- [优云 US3 挂载](https://www.compshare.cn/docs/gpus/data/attachus3)
- [项目当前架构](ARCHITECTURE.md)
- [项目当前状态](CURRENT_STATE.md)




## 25. 已确认的 CPU 服务器部署参数

以下信息来自用户提供的服务器说明和宝塔面板截图；其中“已确认”仅表示已观察到或由用户明确提供，不代表已经完成连通性、权限和部署验收。

### 25.1 服务器与运行环境

- 服务器地址：`185.61.210.32`。
  - 用户曾输入 `18561.21.32`，本需求文档统一按截图及 PostgreSQL 主机信息中的 `185.61.210.32` 记录。
- 服务器用途：共享部署服务器，不作为 LLaMA-Factory 专用正式训练服务器；本项目的 CPU 训练正式运行环境仍部署在该服务器上。
- 操作系统：Ubuntu 24（宝塔首页只读显示）。
- 宝塔版本：`v13.0.0`（宝塔首页只读显示）。
- 硬件：40 个 CPU 核心、62.8 GB 内存、1.8 TB 根磁盘（宝塔首页只读显示）。
- Docker：已安装。
- Docker Compose：用户判断已安装，部署前必须由 Drone 或服务器执行 `docker compose version` 完成确认。
- LLaMA-Factory：尚未安装；第一版应通过项目镜像/Compose 固化运行环境，不建议手工依赖宿主机安装。
- 网络：用户确认相关网络均可访问；仍需在部署前验证 GitHub、Drone、PostgreSQL、MinIO 及镜像仓库的实际连通性。

### 25.2 已观察到的服务与端口风险

宝塔面板截图显示该服务器已有多项容器化服务，包括 PostgreSQL、MinIO、Redis、MongoDB、Ollama、Prometheus、Grafana 和 node_exporter 等。它们可能由其他系统使用，不能默认停止、重建或复用。

- 端口 `3000` 已存在 Grafana 的使用迹象，前端不应默认占用 `3000`。
- 生产部署优先采用 Nginx/宝塔反向代理的同源方案：前端对外提供站点入口，后端仅暴露内部端口；WebSocket 路径也由反向代理转发。
- 宝塔“网站”页面当前显示 Node 项目数量为 `0`，反向代理列表为空（页面同时出现“加载失败”提示，不能据此判断后端没有其他手工配置）。因此当前没有可直接复用的宝塔站点或反向代理，后续需要由部署方案明确创建/配置入口；本次未执行创建操作。
- 推荐的内部端口（暂定，不是已分配端口）：前端 `3100`，FastAPI `8010`，Agent/日志 WebSocket 由后端统一提供。
- 任何新端口在部署前必须先执行占用检查，并确认宝塔安全组/防火墙策略，不在本阶段自动修改防火墙。

### 25.3 代码仓库与部署方式

- GitHub 仓库页面：`https://github.com/leifengyang/cppt-platform`。
- GitHub HTTPS 仓库：`https://github.com/leifengyang/cppt-platform.git`。
- Drone：用户确认已经部署。
- 部署方式：Docker Compose。
- 发布触发：先上传 Git bundle，再向 GitHub 推送符合约定的版本标签；默认标签规则为 `v*`，最终以 `.drone.yml` 中的条件为准。
- 建议部署目录：优先评估 `/opt/cppt-platform`；若服务器运维规范以宝塔目录为主，可使用 `/www/wwwroot/cppt-platform`。目录必须由部署账号确认，当前不自动创建。
- Drone 需要具备：读取服务器上的发布 bundle、构建镜像、通过服务器 Runner 执行 Compose 发布、健康检查和失败回滚的权限。

### 25.5 宝塔 Docker 页面只读核实结果（2026-08-21）

本次通过用户已经登录的宝塔面板当前标签页，对 Docker 应用列表进行了只读查看；未执行安装、部署、停止、重启、重建、卸载、端口修改、防火墙修改或表单提交。

已观察到以下运行中的容器/应用：

| 应用/容器 | 状态 | 镜像/版本 | 面板显示端口 |
|---|---|---|---|
| `ollama_33Cd` | 运行中 | `latest` | `11434` |
| `node_exporter_j6pS` | 运行中 | `latest` | `9100` |
| `prometheus_ntfm` | 运行中 | `latest` | `9090` |
| `redis_jYEz` | 运行中 | `7.4.0` | `36739` |
| `mongodb_FnEw` | 运行中 | `8.2.2` | `27017` |
| `grafana_wJeA` | 运行中 | `latest` | `3000` |
| `minio_bRLa` | 运行中 | `RELEASE.2025-04-22T22-12-26Z-cpuv1` | `19000`、`19001` |
| `postgresql_18_Aer2` | 运行中 | `18.4` | `35432` |

这些端口是宝塔页面显示的映射信息，不等同于已确认的公网可访问性，也不代表可以直接复用。后续部署前仍需由服务器命令或 Drone 任务只读确认：Docker Engine 版本、`docker compose` 版本、容器网络、卷挂载、端口占用、PG 数据库/用户权限、MinIO bucket 及其访问策略。

本项目不得停止、重建或修改上述已有服务；训练平台应优先通过内部 Docker 网络访问现有 PostgreSQL、MinIO、Redis（如确有需要），对外仅暴露经过宝塔/Nginx 反向代理的前端入口和 WebSocket。

### 25.4 数据服务连接信息

- PostgreSQL 主机：`185.61.210.32`。
- PostgreSQL 端口：`35432`。
- MinIO：服务器上已有 MinIO 服务/容器迹象；访问端点、bucket、用户名和密钥不得硬编码或写入仓库，应通过环境变量或 Drone Secrets 注入。
- SQLite：本阶段可继续保留为数据集索引/兼容缓存；训练任务、训练日志、指标、训练产物索引和推理服务必须使用 PostgreSQL。

## 26. 当前仅需补齐的阻塞参数

为避免再次填写大表单，后续只需按部署实际顺序补齐以下少量信息；密码、私钥和访问密钥不应发送到聊天中，而应在服务器或 Drone Secret 中配置。

1. CPU 服务器 SSH：登录用户名、SSH 端口，以及 Drone 是否已经配置部署私钥。
2. Drone 发布方式：Drone Server 地址、Runner 类型，以及 Runner 是否允许 Docker 构建/调用 Docker Compose。
3. 站点访问方式：是否已有域名；若没有，第一版是否接受通过 `185.61.210.32` 加端口访问。
4. 生产目录：在 `/opt/cppt-platform` 与 `/www/wwwroot/cppt-platform` 中选择一个，或提供既有目录。
5. PG/MinIO 的使用方式：继续使用现有服务，还是由本项目 Compose 统一编排；只需要提供 Secret 名称，不需要在聊天中提供密钥值。
6. 标签规则：默认使用 `v*`；如已有发布规范，以现有规范为准。

### 26.1 后续优云 GPU 阶段再补齐

优云区域/可用区、GPU 型号与显存、镜像策略、磁盘大小、SSH 端口和账号、实例完成后的停止/删除策略、US3 或 MinIO 传输策略、实例最长存活时间等，不影响当前 CPU 服务器 LLaMA-Factory 链路的开发和验收。

## 27. 宝塔面板访问说明

本次只允许读取服务器部署事实，不执行安装、部署、重启、停止、删除、改端口、改防火墙或提交表单等操作。用户已在 Chrome 中登录宝塔面板，本次已复用其现有标签页读取 Docker 应用列表，没有新开宝塔页面。由于面板使用的 HTTPS 证书未被浏览器信任，从当前已授权页面直接导航到其他面板路径仍可能再次触发证书安全页；不得通过自动化方式绕过该提示。后续如需继续核实首页、网站、网络或 Compose 信息，应由用户在同一标签页手动进入相应页面后再进行只读读取。




## 28. 后续规划补充（2026-08-22）

- 远程训练执行方式：CPU 训练链路验收稳定后，在"新建微调任务"中增加执行环境选择（本地 CPU 服务器 / 远程优云 GPU）。远程模式按本文档既有设计执行：远程文件准备 → 远程启动 LLaMA-Factory → 实时日志与指标回传 → 产物回传 MinIO → 自动释放 GPU 实例。GPU 资源信息继续走 `/api/gpu-info` 动态探测，不写死节点信息。
- 导入数据集：在"数据集管理 → 新建数据集"旁增加"导入数据集"按钮，扫描 MinIO `rag-assets` 桶 `exports/` 目录（同事导出上传的数据集），列表选择后导入并走现有校验/发布流程。该目录清单由人工确认，可能不全，导入界面需支持展示目录内实际内容。

## 29. 阶段五需求确认（2026-08-23）

阶段一至四已完成（CPU 服务器正式链路验收通过，v3.0.7 发版）。进入阶段五（优云 SSH 远程训练）前已确认的决策：

- 优云 API 凭据已就绪（公钥/私钥、API 地址、区域可用区）。凭据只放 `.env` / Drone Secrets，禁止提交入库、禁止写入日志。
- GPU 实例采用一次性策略：每个远程任务临时创建实例，任务结束、失败、手动停止或超时后自动删除实例，不做保留复用。
- 系统镜像用优云官方 CUDA 公共镜像，开机后自动安装 LLaMA-Factory 环境再启动训练；阶段八的自定义镜像暂不提前。
- 基础模型第一版由远程实例通过 ModelScope 按需下载（验收用小模型，如 Qwen2.5-0.5B-Instruct）；大模型的镜像预置 / MinIO 中转优化后续再做。
- 日志与指标回传第一版用 SSH 轮询同步日志文件；WebSocket 实时流属于阶段七 Agent 方案。
- Provider 代码按第 10 节拆分到 `backend/providers/youyun/`，不得把优云调用逻辑散落在 `backend/app.py` 路由里。
- 后端任务模型已有 `provider: local | youyun` 字段（当前 youyun 被拒），前端"执行方式"当前只有 网页提交 / 命令行执行，需新增"远程优云 GPU"执行环境入口。

阶段五验收标准：

1. 新建微调任务可选执行环境：本地 CPU 服务器 / 远程优云 GPU；选远程时展示 GPU 规格与实时库存，无库存时明确报错。
2. 提交远程任务后自动完成：创建一次性实例 → 轮询至 Running → 安装 LLaMA-Factory 环境 → 上传数据集与训练 YAML → 远程启动训练 → 回传日志与指标 → 产物回传 MinIO → 删除实例。
3. 任意环节失败（含手动停止、超时）都必须释放并删除实例，清理结果与失败原因落库。
4. 用 Qwen2.5-0.5B 级小模型 + 小数据集在优云 GPU 上完成一次真实训练，任务详情页日志、损失曲线、产物与本地任务体验一致。
5. CPU 服务器上已有的本地训练链路不受影响（provider 默认 local）。

### 29.1 阶段五验收结果（2026-08-23）

- [x] Web 新建微调任务可选择“本地 CPU 服务器 / 远程优云 GPU”；远程模式展示 GPU 机型、规格组合与实时库存，无库存提交校验已由单测覆盖。
- [x] Web 真实任务 `ft-260823-141622-2390` 完成一次性实例创建、Running/SSH/GPU 探测、LLaMA-Factory 安装、数据集与 YAML 上传、ModelScope 模型下载、4090 QLoRA 2-step 训练、日志与指标回传、产物回传、本地镜像登记、共享存储上传和实例删除。
- [x] 失败清理已通过单测和 Web 验收期间真实失败路径验证；每个失败实例均记录错误并成功执行 `TerminateCompShareInstance`，没有残留计费实例。
- [x] 成功任务使用 `Qwen/Qwen2.5-0.5B-Instruct`、SFT 小数据集、QLoRA、`max_steps=2`；详情页显示 100%、真实 Loss 图、Final Step 2、远端运行日志、Final Adapter 与 manifest。
- [x] provider 默认仍为 `local`；Linux/WSL 后端全量 100 tests、Python compileall、TypeScript 与 Next.js production build 均通过。

Web 全链路验收额外发现并修复：任务 API 序列化遗漏 provider/executionMode、Worker 错把 provider 写入 executionMode、显式 eval_dataset 与 val_size 冲突、`targetModules=auto` 被当作真实模块名、优云新实例 SSH 会话失活与后台安装启动命令未落地。上述问题均已增加回归覆盖或幂等保护。

### 29.2 v3.0.8 发布与生产验收（2026-08-23）

- [x] 提交 `f934c82` 与 tag `v3.0.8` 已推送，Drone tag 流水线完成；生产 backend/frontend 均为 `v3.0.8` 且 healthy。
- [x] 生产健康检查确认 PostgreSQL 为权威存储，worker 与推理服务空闲，当前无活动训练任务。
- [x] 生产 `.env.production` 已备份后补齐本地验证过的 8 项 `YOUYUN_*` 运行配置；仅重建应用 backend，未操作共享基础设施容器。
- [x] `GET /api/youyun/gpu-specs` 返回 HTTP 200，4090 规格实时库存可售。
- [x] 通过 Control Center 正式入口打开 CPPT Platform；微调任务页连接生产后端并显示历史任务，新建任务页显示“远程优云 GPU / 一次性实例 · 按量计费”。

## 30. 阶段六需求确认：优云单实例多卡 DDP（2026-08-26）

### 30.1 目标和边界

本阶段让一个远程优云训练任务能够申请并使用**同一台实例中的多张 GPU**，以 PyTorch DistributedDataParallel（DDP）执行 LLaMA-Factory 的 SFT 或 DPO LoRA/QLoRA 训练。

- 首版只支持同一优云实例内的多卡；不创建或编排多个独立优云实例。
- 首版采用 Native PyTorch DDP；不新增 DeepSpeed、FSDP、ZeRO、断点续训或弹性训练产品入口。
- 复用阶段五的一次性实例、SSH/SFTP、日志轮询、产物回传和资源清理链路。
- `provider=youyun`、`executionMode=remote`、既有单卡任务、CPU 本地训练和本地推理服务必须保持兼容。
- 多卡 DDP 的目标是提高吞吐；不承诺它能让单卡无法容纳的完整模型直接训练。模型显存不足属于后续分片训练方案的范围。

多实例多节点集群不在本阶段实现。它需要优云确认并通过实测验证实例间私网互通、NCCL 所需端口与网卡选择、带宽/延迟、同时获得全部节点的调度能力，以及共享存储语义后，才可独立立项。

### 30.2 用户与资源选择

1. 用户在“远程优云 GPU”环境中选择 GPU 型号、GPU 数量、CPU 核数和内存。
2. GPU 数量不是固定选项；页面只能展示优云实时库存返回、且与当前 GPU 型号匹配的规格组合。
3. 后端在报价和创建前都必须按 GPU 型号、GPU 数量、CPU、内存和可用区重新校验库存；不得相信前端的旧价格或旧库存。
4. 用户选择 1 卡时，行为必须与阶段五单卡远程训练一致；选择大于 1 卡时，任务详情应明确显示“单实例多卡 DDP”、GPU 数和全局 world size。
5. 前端“单卡批次大小”保持每 GPU 进程的语义；有效全局批次展示为：`per_device_train_batch_size × gradient_accumulation_steps × GPU 数量`。
6. 资源规格、实时价格快照、实际创建的实例 ID、GPU 型号和 GPU 数量必须随任务持久化，供详情页、成本核对和故障排查使用。

### 30.3 远程执行要求

1. 远程实例完成 GPU 探测后，执行器必须确认探测到的可用 GPU 数不少于任务选择数量；不满足时任务失败并进入实例清理。
2. 多卡任务使用管理员控制的环境变量和启动参数启用 LLaMA-Factory 的 `torchrun` / DDP 启动路径，例如设置 `FORCE_TORCHRUN=1`，并仅暴露任务选择的 GPU 给训练进程。
3. 各 rank 使用同一份已校验的 YAML、数据集文件、模型版本、Python 依赖和随机种子；任何用户输入都不得拼接为可执行 Shell 片段。
4. 首版只允许单节点通信。必须记录 `WORLD_SIZE`、本地 GPU 列表、PyTorch/CUDA/NCCL 版本和 `nvidia-smi topo -m` 的脱敏诊断结果，便于定位拓扑或环境问题。
5. 训练启动前应设置足够的 DDP 超时；发生 rendezvous、NCCL 初始化或任一 rank 异常退出时，停止整个训练进程组，而不是让其他 rank 无限等待。
6. 远端安装依赖必须从“每次安装最新版”演进为由平台锁定的兼容版本组合；多卡验收前应固定 LLaMA-Factory、PyTorch、CUDA、NCCL、Transformers、PEFT 和 bitsandbytes 版本并写入运行日志。

### 30.4 日志、指标和产物

1. 远端训练日志必须保留 rank 标识。任务详情默认展示去重后的主日志，仍可查看所有 rank 的原始诊断日志。
2. Loss、学习率、进度和最终训练状态由 rank 0 作为任务权威来源；非 rank 0 的重复指标不得重复写入任务曲线。
3. checkpoint、Best Adapter、Final Adapter、manifest 和共享存储上传只允许 rank 0 执行。平台仍只登记一组 best/final Adapter，不改变现有模型管理和推理契约。
4. 用户停止、超时或任一 rank 失败时，执行器必须终止整个进程组；尽力回传 rank 0 已生成的有效产物，并将每个 rank 的退出状态写入任务日志。
5. 成功、失败、停止和超时的任何路径都必须沿用阶段五资源清理保证：优云实例删除结果可追踪；清理失败必须明确提示人工处理，避免持续计费。

### 30.5 后端、接口与数据模型

1. `YouyunResourceSelection.gpuCount` 和报价请求不再限制为 1，但应保留合理上限，并只接受优云实时容量中存在的规格。
2. `GET /api/youyun/gpu-specs` 返回的容量组合必须包含所有可售 GPU 数量，前端不应过滤为单卡。
3. `POST /api/youyun/price` 与 `POST /api/tasks` 对多卡资源使用同一份解析和校验逻辑，避免报价规格与实际创建规格不一致。
4. 远程任务 artifact 中的优云实例记录补充执行拓扑，例如 `distributedMode`、`worldSize`、实际可见 GPU 数和诊断摘要；不得记录 SSH 密码、私钥或未脱敏连接凭据。
5. 任务 API 与前端类型补充分布式执行摘要，但不得改变旧任务读取、重试、克隆、删除和单卡详情页的兼容性。

### 30.6 验收标准

实施前先完成不创建计费资源的单元测试和配置测试：

1. 单卡选择仍生成原有远程启动方式；2 卡及以上选择会生成受控的 DDP 启动环境，且不会拼接任意用户 Shell 文本。
2. 库存、报价、提交三处均能拒绝不存在、售罄或规格不匹配的多卡组合。
3. 多卡日志只将 rank 0 指标写入 Loss 曲线；任一 rank 失败、用户停止和超时均会终止完整进程组并调用实例清理。
4. 产物收集只登记一套 rank 0 Adapter/manifest，旧单卡任务与本地任务的回归测试通过。

真实优云验收使用同一可用区、同一镜像的至少 2 卡实例和小模型/小数据集完成：

1. 远端 `nvidia-smi`、PyTorch CUDA 可见卡数和任务选择数量一致。
2. LLaMA-Factory 日志明确表明分布式训练已启用，且训练能产生真实 Loss、最终 Adapter 和 manifest。
3. 相同训练配置下，多卡任务能完成；是否达到线性加速只作为性能观测，不作为首版功能通过条件。
4. 分别演练正常完成、一个训练 rank 异常、手动停止和远程超时；四种路径均验证任务终态、日志、产物边界和实例删除结果。
5. 记录 GPU 拓扑、训练耗时、峰值显存、吞吐和实例单价，为后续是否支持 4/8 卡及多节点集群提供决策依据。

### 30.7 多实例多节点集群的准入条件

在以下 POC 全部通过并形成记录前，界面、API 和任务模型不得宣称支持多节点训练：

1. 同一优云可用区的两台实例可通过私网稳定双向通信，并可由平台受控地开放 master rendezvous 与 NCCL 通信所需端口。
2. 使用目标镜像完成 `torch.distributed` 最小 all-reduce 和 `nccl-tests`，记录带宽、延迟、网卡和失败日志。
3. 两节点真实 LoRA 训练可稳定完成，节点中任一实例失败时可整体停止、回收全部实例且不产生孤儿计费资源。
4. 数据集、模型缓存和 checkpoint 的共享或同步策略经一致性、原子写入和清理测试确认。
5. 供应商或实测确认实例同时申请、网络拓扑、迁移/抢占和计费规则不会破坏任务生命周期保证。

### 30.8 技术依据

- LLaMA-Factory 分布式训练文档：<https://llamafactory.readthedocs.io/en/latest/advanced/distributed.html>
- PyTorch 分布式与 `torchrun` 文档：<https://docs.pytorch.org/docs/stable/distributed.html>
- NVIDIA NCCL 网络排障文档：<https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/troubleshooting/networking_troubleshooting.html>
- 优云 GPU 资源创建与规格查询文档：<https://www.compshare.cn/docs/operation/gpu/createresources>
