# 阶段二部署交接：CPU Linux 真实训练与生产持久化验收

更新时间：2026-08-22

## 1. 结论

阶段二已经在目标 CPU Linux 服务器完成部署，并通过真实 LLaMA-Factory CPU 训练与完整生产持久化验收。

- 服务器：`185.61.210.32`，Ubuntu 24.04 LTS，x86_64
- 部署目录：`/opt/cppt-platform`
- Backend：`127.0.0.1:8010`
- Inference：`127.0.0.1:8011`
- Frontend：`127.0.0.1:3100`
- Backend/Frontend Compose 服务均为 `healthy`
- `/api/health` 返回 `status=ok`、`storageBackend=postgres`、`queue=postgres`
- Frontend 根路径返回 HTTP 200

未修改宝塔网站或 Nginx；首次验收全部通过服务器内部请求完成。未部署或操作 Redis，也未停止、重启或替换服务器上的其他 Docker 容器。

## 2. Git 与工作区状态

- 当前本地 `HEAD`：`49f0ef9c62b5aac88f8b14f7aa1045954c93b110`
- `origin/master`：同为 `49f0ef9c62b5aac88f8b14f7aa1045954c93b110`
- 阶段二部署、真实训练和验收修改仍在本地工作区，尚未提交
- 未推送 Gitee
- 不应在未获得用户明确确认前提交或推送这些修改

## 3. 本轮关键修复

1. `deploy/docker/frontend.Dockerfile`
   - build 阶段创建空 `public/`，避免 runtime COPY 不存在目录导致镜像构建失败。

2. `backend/model_storage.py`
   - MinIO 对象前缀由错误的 `models/<model-id>` 修正为契约要求的 `model/<model-id>`。
   - `backend/test_model_storage.py` 已增加回归测试。

3. `deploy/docker/backend.Dockerfile`
   - 使用 `torch 2.7.1+cpu`、`torchvision 0.22.1+cpu`、`torchaudio 2.7.1+cpu`。
   - 使用 `deploy/docker/torch-constraints.txt` 防止安装 LLaMA-Factory 依赖时替换为 CUDA Torch。
   - 创建并授权 `/workspace/.modelscope` 及训练持久化目录，适配 UID/GID 1000 非 root 运行。

4. CPU 训练参数链路
   - 验收任务使用 `maxSequenceLength=64`，后端允许该最小验收长度并转换为 `cutoff_len=64`。

5. `backend/local_llamafactory_executor.py`
   - 隔离工作目录中的数据集路径现在正确覆盖任务原始路径。
   - `validationSteps`、`loraPlusRatio` 等平台 UI 参数不再泄漏为不安全的额外 YAML 参数。
   - 已增加对应执行器回归测试。

6. `scripts/accept_phase2_cpu.py`
   - 共享模型记录状态按真实 schema 使用 `available`，不再错误等待不存在的 `uploaded` 状态。
   - 强制校验对象前缀为 `model/<model-id>`。
   - MinIO 列举使用 `<prefix>/`，避免相似前缀误匹配。
   - `backend/test_accept_phase2_cpu.py` 已覆盖 available 状态和错误前缀回归。

7. `backend/store.py`
   - 任务 API 序列化补回 `maxSteps`，确保验收和 UI/API 可核对实际任务上限。
   - `backend/test_store.py` 已增加 create/get/list 序列化回归测试。

## 4. 本地回归结果

最终源码状态已重新执行：

```text
WSL 后端 unittest：48 个通过
Python compileall（backend、scripts）：通过
npx tsc --noEmit：通过
npm run build：通过
Docker Compose production config：通过
```

## 5. 服务器运行时

Backend 镜像内真实运行时：

```text
LLaMA-Factory 0.9.5
Torch 2.7.1+cpu
CUDA available: False
llamafactory-cli: /opt/venvs/llamafactory/bin/llamafactory-cli
```

模型已持久化到：

```text
容器：/workspace/model-cache/Qwen2.5-0.5B-Instruct
宿主机：/opt/cppt-platform/deploy-data/model-cache/Qwen2.5-0.5B-Instruct
```

## 6. 真实 CPU 训练结果

成功验收任务：

```text
ft-260821-191055-0395d8
```

任务契约：

```text
模型：Qwen2.5-0.5B-Instruct
任务：SFT / LoRA
batch_size：1
cutoff_len / maxSequenceLength：64
max_steps：1
validationSplit：0
状态：completed
finalStep：1
selectedAdapterVariant：final
```

真实 trainer 日志包含：

```text
device: cpu
distributed training: False
Total optimization steps = 1
Number of trainable parameters = 540,672
```

实际生成的 `train.yaml` 明确包含：

```text
model_name_or_path: /workspace/model-cache/Qwen2.5-0.5B-Instruct
finetuning_type: lora
cutoff_len: 64
per_device_train_batch_size: 1
max_steps: 1
```

`validationSplit=0`，因此只生成并选择 Final Adapter，不伪造 Best Adapter；这符合当前 Adapter 契约。

以下两个任务为修复过程中的失败记录，不能作为验收成功结果：

- `ft-260821-190535-d9e365`：隔离工作目录数据集路径问题
- `ft-260821-190916-da6df2`：`loraPlusRatio` 泄漏到严格 YAML 参数问题

## 7. 完整验收结果

验收脚本对成功任务返回 `accepted: true`：

```text
REST logs：430
REST metrics：1
WebSocket log events：430
WebSocket terminal state events：1
manifest files：22
PostgreSQL training_task_logs：430
PostgreSQL training_task_metrics：1
PostgreSQL training_task_events：3
```

产物：

```text
/workspace/training-work/ft-260821-191055-0395d8/train.yaml
/workspace/training-work/ft-260821-191055-0395d8/dataset/dataset_info.json
/workspace/training-work/ft-260821-191055-0395d8/manifest.json
/workspace/training-work/ft-260821-191055-0395d8/output/train_results.json
/workspace/training-work/ft-260821-191055-0395d8/output/adapter_model.safetensors
/workspace/training-work/ft-260821-191055-0395d8/output/checkpoint-1/
```

Manifest SHA-256：

```text
30a73e21510ed46f6fc216d6477eed8f032c4771b369d8527f3f66800910979e
```

共享存储：

```text
PostgreSQL model file：model-3dcbf0321bc748ac
状态：available
MinIO bucket：rag-assets
对象前缀：model/model-3dcbf0321bc748ac
available entries：6
MinIO objects：6
MinIO bytes：13,611,639
```

生产持久化连接使用专属 PostgreSQL 数据库 `cppt_platform` 和目标 MinIO 服务；未连接或修改 `rag-postgres`、`chatbi-postgres`、另一套 `rag-minio`，未接入 Redis。

## 8. 后续操作边界

- 不要重复执行阶段一 PostgreSQL 迁移或基础表创建。
- 不要修改宝塔 Nginx，除非用户明确要求正式暴露入口。
- 不要把 `.env`、数据库密码、MinIO 密钥或 SSH 私钥写入文档、日志或 Git。
- 不要把两个失败任务或命令级测试替身描述为真实训练成功。
- 只有在用户明确确认后，才整理提交并推送当前阶段二修改。

## 2026-08-22 Drone tag 自动部署（当前配置）

已新增并推送 tag-only Drone 配置：

- 配置文件：`.drone.yml`
- Gitee 提交：`9233012 ci: add tag-based Drone deployment pipeline`
- 触发：只响应 Git `tag` 事件
- 验证：后端单元测试、Python compileall、TypeScript 检查、Next.js production build、生产 Compose config
- 镜像：`cppt-platform-backend:${DRONE_TAG}`、`cppt-platform-frontend:${DRONE_TAG}`
- 发布：使用 `compose.production.yml` 在 Drone runner 所连接的 Docker Engine 上 `up -d --no-build --force-recreate`
- 健康检查：等待 backend 和 frontend 两个 Compose healthcheck 均为 `healthy`；失败时打印 `ps` 和最近 200 行服务日志

发布前置条件：

1. Drone runner 必须能访问目标生产 Docker daemon；当前流水线不是远程 SSH 发布，也不推送外部镜像仓库。
2. 目标目录为 `/opt/cppt-platform`，并挂载为 Drone 的 `deploy-config` host volume。
3. 目标目录已有真实 `/opt/cppt-platform/.env.production`，不得使用 `.env.production.example` 直接部署。
4. `compose.production.yml`、Docker Compose、PostgreSQL、MinIO、数据目录权限和外部网络已经准备好。
5. 生产配置中的 `XUNTUI_DEPLOY_DATA_ROOT` 与流水线的持久化目录保持一致，避免容器重建后丢失 runtime、datasets、artifacts、uploads、model-cache 和 training-work 数据。

发布操作：

```bash
git checkout master
git pull --ff-only origin master
git tag v2026.08.22-1
git push origin v2026.08.22-1
```

每次发布使用新的唯一 tag；tag 会直接成为两个镜像的版本。首次正式发布前，建议先在 Drone 中验证 tag pipeline 的 runner 权限、Docker socket、生产 `.env.production` 和 healthcheck，不要把真实密钥写入仓库或聊天记录。

## 2026-08-22 临时公网测试模式

由于暂时没有域名，目标服务器当前采用临时高端口直连：

- 前端公网入口：`http://185.61.210.32:13100`
- Backend API：`http://185.61.210.32:18010/api`
- 推理端口 `8011` 仍只绑定 `127.0.0.1`

Compose 已支持 `XUNTUI_*_BIND` 变量。Frontend 已使用公网 API 地址重新构建。该次临时公网部署是在 tag-only 流水线建立之前，通过已确认可用的 SSH 手动同步和 Docker Compose 完成；当前 `.drone.yml` 已改为由 Git tag 触发验证、双镜像构建、Compose 重建和健康检查。

临时模式仅用于项目组手动测试；正式上线前应改为域名、HTTPS 和 Nginx 反向代理，并取消 Backend API 的公网端口。

## 2026-08-22 Bug 修复对话交接

后续 Bug 修复请基于当前工作区继续。当前阶段二部署已经完成真实 CPU 验收，临时公网手动测试入口如下：

- 前端：`http://185.61.210.32:13100`
- Backend API：`http://185.61.210.32:18010/api`
- 健康检查：`http://185.61.210.32:18010/api/health`
- 推理服务：`127.0.0.1:8011`，没有公网暴露

这里记录的这次部署不是 Drone 自动执行，而是 SSH 手动同步到 `/opt/cppt-platform` 后使用 Docker Compose 构建和启动。此后 `.drone.yml` 已更新为 tag-only 自动部署流程；首次 tag 发布仍需单独验证 runner 权限、Docker socket、生产配置和健康检查。

新 Bug 对话开始时，必须先读取：

```text
PHASE1_HANDOFF.md
REQUIREMENTS_LLAMA_FACTORY_REMOTE_TRAINING.md
CURRENT_STATE.md
PHASE2_DEPLOYMENT_HANDOFF.md
BUGFIX_HANDOFF.md
```

并确认：

- 当前工作区有大量阶段二未提交修改，不能执行 `git reset --hard` 或覆盖这些修改。
- 当前 `HEAD` 与 `origin/master` 仍为 `49f0ef9c62b5aac88f8b14f7aa1045954c93b110`；未经用户确认不要提交、推送或拉取覆盖。
- 不要读取或输出任何真实 `.env`、数据库密码、MinIO 密钥或 SSH 私钥。
- 不要停止、重启或替换服务器已有基础设施容器。
- 不要重复阶段一数据库迁移，不要部署新的 PostgreSQL、MinIO、Redis。
- 涉及公网配置时，注意 `13100` 是前端，`18010` 是 Backend API，`8011` 仅本机推理端口。

修复 Bug 的标准流程：先复现 → 定位根因 → 添加回归测试 → 运行相关测试和构建 → 必要时重新构建本应用镜像 → 验证内部与公网访问 → 更新交接文档。后续 Bug 修复可以直接通过截图和自然语言描述进行，不要求使用固定的技术格式；助手根据截图和描述完成复现、定位、修复和验证。

## 2026-08-22：网关模式切换清单（v3.0.3 部署断言失败的处理）

现象：tag `v3.0.3` 部署日志最后一步报 `frontend port 13100 must bind to 127.0.0.1, got 0.0.0.0`。容器已全部 healthy、服务正常，仅网关模式安全断言未过。

原因：服务器 `/opt/cppt-platform/.env.production` 仍是临时公网模式（`XUNTUI_FRONTEND_BIND=0.0.0.0`），与流水线网关模式断言不一致。另有配套问题：网关入口 `http://185.61.210.32/`（80 端口）加载的页面会跨源调用 `http://185.61.210.32:18010/api`，当前 `XUNTUI_CORS_ORIGINS` 只放行了 `:13100` 来源，80 端口入口的 API 调用会被浏览器 CORS 拦截。

服务器侧需改两行（在 `/opt/cppt-platform/.env.production`）：

```bash
XUNTUI_FRONTEND_BIND=127.0.0.1
XUNTUI_CORS_ORIGINS=http://185.61.210.32,http://185.61.210.32:13100
```

改完后重新打 tag 部署，断言即可通过。此后正式访问入口为 `http://185.61.210.32/`（80 端口网关 → 127.0.0.1:13100），13100 不再直接暴露公网。

注意事项：

- Backend `18010` 在前端镜像切换回 `/api` 同源构建之前必须保持公网可达（当前 `.drone.yml` 前端构建参数仍是绝对地址 `http://185.61.210.32:18010/api`）。
- 待网关支持 `/api` 反代到 backend 后，将 `.drone.yml` 前端构建参数改回 `/api`，再收紧 `XUNTUI_BACKEND_BIND=127.0.0.1`，同时从 `XUNTUI_CORS_ORIGINS` 移除 IP 来源（改用域名）。
- 在上述两行修改完成前，新 tag 部署都会在断言处失败（服务本身不受影响）；`.drone.yml` 失败信息中已附带本清单指引。

**状态更新（2026-08-22 晚）**：上述两行服务器配置已通过 SSH 完成（改前备份 `.env.production.bak-20260822`），tag `v3.0.4` 部署全绿并实测通过：双容器 healthy、13100 已收回公网、CORS 放行网关来源、postgres 会话时区 Asia/Shanghai。网关模式切换完成，仅剩「前端切回 /api 同源构建 + 收紧 backend 绑定」待网关支持 `/api` 反代后执行。

## 2026-08-22 数据集页面交互修复（v3.0.6 发布内容）

在阶段二部署工作区基础上新增一轮数据集管理页面改动，本次纳入 v3.0.6 发布：

1. 在数据集列表页“新建数据集”旁新增“导入数据集”。后端扫描 MinIO `rag-assets` 桶 `exports/` 前缀并返回实际对象清单；用户选择 JSONL、填写数据集名称和 SFT/DPO 结构后，后端下载到 `XUNTUI_DATASET_ROOT` 并沿用现有校验/发布生命周期。MinIO 凭据继续只从环境变量读取。
2. 使用浏览器 `sessionStorage` 记住当前模块、视图和只读预览数据集，刷新页面后保持原界面，不主动跳转到数据集列表。
3. 克隆命名从“副本1”开始，按最小可用序号递增。
4. 数据集列表原“编辑”操作改为“只读”；只读预览页的记录表移除“操作”列。

验证：后端相关单测 22 个通过，Python 编译检查、TypeScript 检查和 Next.js production build 均通过。2026-08-22 21:48（UTC+8）已完成 v3.0.6 tag pipeline 部署；生产 backend/frontend 均使用 v3.0.6 镜像且 healthy、restart=0，`/api/health` 返回 200，非法导入来源校验返回 422。
