# 阶段一交接说明：LLaMA-Factory CPU 改造

更新时间：2026-08-21
需求依据：`D:\训推平台\REQUIREMENTS_LLAMA_FACTORY_REMOTE_TRAINING.md`

## 当前结论

需求文档第 20 节的**阶段一：配置和存储基础**已经完成代码落地并通过静态/单元验证。下一次开发会话不需要重做阶段一，应直接进入需求文档中的**阶段二：LLaMA-Factory 开发机冒烟链路**。

注意：需求文档中的“正式训练环境”是部署后的 CPU Linux 服务器；Windows/WSL 仅用于开发调试和冒烟验证。

## 阶段一已完成内容

### PostgreSQL

新增 `D:\训推平台\backend\postgres_storage.py`：

- `schema_migrations` 迁移记录表。
- 当前迁移版本为 `1`。
- 训练表：`training_tasks`、`training_task_logs`、`training_task_metrics`、`training_task_events`。
- 推理表：`inference_services`、`inference_logs`、`inference_metrics`、`inference_command_usage`。
- 启动迁移使用 PostgreSQL advisory lock，避免多进程同时启动时发生迁移竞态。
- `PostgresTaskStore` 提供任务、状态、日志、指标、停止、重试、克隆、删除和 Adapter 选择能力。
- `PostgresInferenceStore` 兼容原推理服务接口，JSON 字段使用 JSONB。

SQLite 仍保留为数据集本地索引、模型管理兼容目录和开发缓存；新训练任务与新推理服务在 PostgreSQL 模式下不再以 SQLite 为权威数据源。已有模型文件/条目元数据继续使用 `backend/model_storage.py` 的 PostgreSQL + MinIO 实现。

### 应用配置

修改 `D:\训推平台\backend\app.py`：

- 读取项目根目录 `.env`。
- 通过 `XUNTUI_STORAGE_BACKEND=sqlite|postgres` 选择后端。
- 路径、数据库、CORS 和推理端口支持环境变量。
- `/api/health` 返回 `storageBackend`、队列和运行目录信息。
- 本地开发默认仍为 SQLite，避免没有显式配置时连接生产数据库；CPU 服务器部署必须显式设置 `XUNTUI_STORAGE_BACKEND=postgres`。

### Linux 启动和部署样例

- 新增 `D:\训推平台\scripts\run_backend_linux.py`，直接使用 Uvicorn 启动 FastAPI，不依赖 Windows/WSL。
- 更新 `D:\训推平台\.env.example`，包括 PostgreSQL、MinIO、CPU 服务器路径、端口、CORS、训练工作目录和 LLaMA-Factory 命令变量。
- 更新 `D:\训推平台\requirements-storage.txt`，包含 `psycopg[binary]`、MinIO、FastAPI、Uvicorn 和 Pydantic。

### 兼容性修复

`D:\训推平台\backend\inference_service.py` 现在要求任务存在真正的 Best Adapter 才能创建推理服务；只有 Final Adapter 的历史任务不能直接部署。

## 已验证结果

以下验证均已通过：

```text
WSL Python compile: passed
Backend unittest: 36 tests, OK
npx tsc --noEmit: passed
npm run build: passed
```

验证命令使用 WSL 环境：

```powershell
wsl.exe -d Ubuntu-24.04 --cd "/mnt/d/训推平台" -- /root/miniconda3/envs/unsloth_env/bin/python -m unittest discover -s backend -p "test_*.py" -v
```

## 阶段一收尾核验

- 已连接并核验 PostgreSQL 阶段一迁移：目标数据库为 `cppt_platform`，迁移版本为 `1`，阶段一 9 张表均已存在。
- 尚未部署或修改现有 PostgreSQL、MinIO、Redis 等服务。
- 尚未创建 Dockerfile、Compose、Drone 配置。
- 尚未把旧的 `train_sft.py`/Unsloth worker 替换成 LLaMA-Factory。
- 项目当前不是 Git 仓库，不能用 `git diff` 或 `git status` 作为变更依据。

项目根目录的真实 `.env` 不应提交或复制到代码仓库；其中的连接信息和密钥只应通过服务器环境或 Drone Secrets 注入。

## 下一步：阶段二实现顺序

建议新会话按以下顺序执行，不要一开始修改远程部署：

1. 阅读本交接文件、需求文档第 6、7、8、20.2 节以及当前 `backend/app.py`、`backend/store.py`、`backend/train_sft.py`。
2. 新增 LLaMA-Factory 参数模型，优先覆盖 CPU 小模型 + SFT + LoRA；保留 DPO/QLoRA 的参数扩展点。
3. 新增平台参数到 LLaMA-Factory YAML 的纯函数转换器，并为路径、数据集、输出目录做安全校验。
4. 新增统一 `TrainingExecutor` 接口，至少包含 `prepare`、`start`、`stop`、`status`、`events`、`collect_artifacts`、`cleanup`。
5. 实现 `LocalLlamaFactoryExecutor`：使用 `llamafactory-cli train <yaml>`，禁止任意 Shell 拼接，命令必须使用 argv。
6. 先补充转换器和执行器单元测试，再把 worker 的训练执行层切换过去；不要破坏现有任务状态、日志、指标、Best/Final Adapter API 契约。
7. 实现训练日志/指标解析和产物收集，输出结构继续写入当前 `store` 接口。
8. 增加 REST 日志查询；再增加 WebSocket 日志流，并保留 REST 降级。
9. 使用小模型和小 JSONL 做本地冒烟训练；不要把开发机结果当作 CPU 服务器正式验收。
10. 阶段二稳定后，再进入 Drone/CPU 服务器部署阶段。

## 阶段二实现约束

- 不实现优云 GPU、SSH、Node.js Agent 或完整远程调度；这些属于后续阶段。
- 不允许用户提交任意 Shell 命令。
- 训练任务、训练日志、指标和产物关联继续使用 PostgreSQL 存储模式。
- 产物最终通过现有 `ModelArtifactUploader` 写入 MinIO；缺少共享存储配置时应明确记录错误，不得静默伪装上传成功。
- 需要兼容现有前端任务创建表单，优先在后端完成参数转换，避免第一步大规模重写前端。
