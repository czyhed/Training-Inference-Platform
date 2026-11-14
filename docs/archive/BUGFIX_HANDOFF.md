# Bug 修复对话交接说明

更新时间：2026-08-23

## 当前项目状态

阶段二已经部署到 CPU Linux 服务器，并完成真实 LLaMA-Factory CPU 验收。当前服务仍在运行：

- 前端：`http://185.61.210.32:13100`
- Backend API：`http://185.61.210.32:18010/api`
- API 健康检查：`http://185.61.210.32:18010/api/health`
- 推理服务：服务器本机 `127.0.0.1:8011`，未公网暴露
- 部署目录：`/opt/cppt-platform`
- 服务：`cppt-platform-backend-1`、`cppt-platform-frontend-1`
- 当前临时公网端口：前端 `13100`，Backend `18010`

临时公网模式只用于项目组手动测试。正式上线前应切换到域名、HTTPS、Nginx 反向代理，并关闭 Backend 公网端口。

## 真实 CPU 验收基线

成功任务：`ft-260821-191055-0395d8`

- Qwen2.5-0.5B-Instruct
- SFT / LoRA
- batch size 1
- cutoff length 64
- max steps 1
- 状态 completed
- finalStep 1
- 使用真实 `/opt/venvs/llamafactory/bin/llamafactory-cli`
- LLaMA-Factory 0.9.5
- Torch 2.7.1+cpu
- PostgreSQL / MinIO 持久化验收通过

## 当前代码与 Git 边界

- 工作区包含阶段二部署、真实训练和验收修改，均未提交。
- `HEAD` 和 `origin/master` 都是 `49f0ef9c62b5aac88f8b14f7aa1045954c93b110`。
- 不要执行 `git reset --hard`、批量覆盖工作区、提交或推送，除非用户明确要求。
- 不要输出 `.env`、数据库密码、MinIO 密钥或 SSH 私钥。
- 不要推送 Gitee。

## 必须先读的文件

```text
PHASE1_HANDOFF.md
REQUIREMENTS_LLAMA_FACTORY_REMOTE_TRAINING.md
CURRENT_STATE.md
PHASE2_DEPLOYMENT_HANDOFF.md
BUGFIX_HANDOFF.md
```

## Bug 修复说明

新对话中可以直接发送 Bug 截图，再用自然语言说明哪里不对即可，不需要填写固定格式或技术字段。

助手会根据截图和描述自行复现、定位、修复和验证；如果还需要其他信息，会再向你询问。

### 建议处理流程

1. 根据截图和描述复现并定位问题。
2. 定位根因后，优先增加回归测试，再修改实现。
3. 运行相关后端测试；涉及 Python 时运行：

```powershell
wsl.exe -d Ubuntu-24.04 --cd "/mnt/d/训推平台" -- /root/miniconda3/envs/unsloth_env/bin/python -m unittest discover -s backend -p 'test_*.py'
wsl.exe -d Ubuntu-24.04 --cd "/mnt/d/训推平台" -- /root/miniconda3/envs/unsloth_env/bin/python -m compileall -q backend scripts
```

6. 涉及前端时运行：

```powershell
npx tsc --noEmit
npm run build
```

7. 涉及 Compose 时运行 production config 检查；涉及部署时只重建本应用的 backend/frontend，不操作其他服务容器。
8. 修复后同时验证服务器内部请求和公网入口，并更新 `CURRENT_STATE.md` 与本文件。

## 部署方式说明

本次不是通过 Drone 自动部署：

- 使用 SSH 手动同步代码到 `/opt/cppt-platform`。
- 使用 Docker Compose 构建 `cppt-platform-backend:local` 和 `cppt-platform-frontend:local`。
- 使用 Compose 启动本应用的 backend/frontend。
- `.drone.yml` 只定义 CI 验证、镜像发布和需显式授权的生产 promotion，不代表本次已执行 Drone 发布。

## 新对话可直接粘贴的首条消息

```text
请继续修复 D:\训推平台 的 Bug。

先读取：
- D:\训推平台\PHASE1_HANDOFF.md
- D:\训推平台\REQUIREMENTS_LLAMA_FACTORY_REMOTE_TRAINING.md
- D:\训推平台\CURRENT_STATE.md
- D:\训推平台\PHASE2_DEPLOYMENT_HANDOFF.md
- D:\训推平台\BUGFIX_HANDOFF.md

当前阶段二已经完成真实 CPU 验收，服务正在服务器运行：
- 前端：http://185.61.210.32:13100
- Backend API：http://185.61.210.32:18010/api
- 健康检查：http://185.61.210.32:18010/api/health
- 部署目录：/opt/cppt-platform
- SSH：root@185.61.210.32:22

当前工作区有大量未提交阶段二修改。不要 reset、覆盖、提交或推送，除非我明确要求；不要读取或输出 .env、数据库密码、MinIO 密钥和 SSH 私钥；不要操作服务器上已有的 PostgreSQL、MinIO、Redis、Nginx 或其他 Docker 容器。

先复现和定位问题，再添加回归测试、修复、运行验证，并在必要时只重建本应用容器。我的 Bug 描述如下：
我会直接发送 Bug 截图和描述，请根据截图帮我复现、定位并修复问题。
```

## 2026-08-22：CPU optimizer 与冒烟任务 0%/资源释放文案停滞

### 用户现象

- 训练在创建 optimizer 时抛出缺少 bitsandbytes 的 ImportError。
- 新冒烟任务五六分钟仍显示“正在释放聊天模型占用的 GPU”，进度为 0%。

### 排查结论

- tokenizer token 对齐 warning 不是根因。
- CPU-only 训练不能直接使用 `adamw_8bit`；失败任务参数确实为该 optimizer。
- 长任务 `ft-260822-035301-1f6020` 的训练进程一直处于真实 CPU 计算状态，并非死锁。它未配置 `maxSteps`，因此实际执行 10 steps；`cutoff_len=512` 下已观测到前 5 steps 累计约 30 分 49 秒。
- 页面保持 0% 是两个问题叠加：Web 未提交 `maxSteps`；后端不识别 LLaMA-Factory 新日志 `Total optimization steps = 10`，无法正确计算总步数和 metric step。

### 已修改文件

- `backend/llamafactory.py`
- `backend/local_llamafactory_executor.py`
- `backend/store.py`
- `backend/app.py`
- `backend/test_llamafactory.py`
- `backend/test_store.py`
- `components/training-module.tsx`
- `lib/mock-data.ts`
- `lib/types.ts`
- `scripts/submit_smoke_task.py`
- `scripts/verify_best_checkpoint_e2e.py`

核心行为：CPU 下把 8-bit optimizer 降级为 `adamw_torch`；Web 可设置 `maxSteps`；进度解析兼容 `Total optimization steps` 并按 epoch 推断 step；训练阶段显示准确状态文案。

### 测试与部署

- 新增 optimizer fallback、总步数格式和 metric step/progress 回归测试。
- 后端全量 52 tests：通过。
- Python compileall：通过。
- TypeScript：通过。
- Next.js production build：通过。
- `git diff --check`：通过。
- 只同步并重建 `/opt/cppt-platform` 的 backend/frontend；未操作服务器已有基础设施容器。

### 真实 CPU 回归结果

- 原误配置任务 `ft-260822-035301-1f6020` 已通过应用 API 停止，终态 stopped。
- 新任务：`ft-260822-050144-abbfd4`
- 配置：`max_steps: 1`、`optim: adamw_torch`、`cutoff_len: 64`、`gradient_checkpointing: false`
- 终态：completed、progress=100、finalStep=1、loss=2.083
- 日志：`Total optimization steps = 1`；没有 bitsandbytes ImportError
- 真实单步耗时：约 204.7 秒。CPU 首个 step 完成前没有 loss metric 属于正常现象，页面现在会显示“训练已开始，等待首个训练指标”，而不是继续显示资源释放。
- 产物：Final Adapter、checkpoint-1、manifest 均存在。
- 共享模型记录：`model-bec927872f664971`，状态 available。
- 公网验证：Backend `/api/health` HTTP 200，Frontend HTTP 200，worker=idle。

不要把已停止的 `ft-260822-035301-1f6020` 误报为成功任务；本轮真实成功回归任务是 `ft-260822-050144-abbfd4`。

## 2026-08-22：训练前后对比页显示“LoRA Adapter 路径无效”

### 现象

完成训练后进入“训练前后效果对比”，训练后区域无法生成回答，页面显示：

```text
LoRA Adapter 路径无效。
```

### 根因

真实 CPU 训练执行器将任务产物写入：

```text
/workspace/training-work/<task-id>/output
```

但 `/api/chat` 原校验只允许：

```text
/workspace/artifacts/<task-id>
```

因此，即使 Adapter 目录存在且文件完整，也会被错误返回 403。

修复后允许的范围仅为当前任务自己的目录：

```text
/workspace/artifacts/<当前 task-id>/...
/workspace/training-work/<当前 task-id>/...
```

其他任务目录或任意越权路径仍会被拒绝。

### 第二个关联问题

CPU backend 镜像不安装 `unsloth`，所以路径白名单修复后直接加载 Adapter 还会遇到：

```text
No module named 'unsloth'
```

已在 `backend/chat_inference.py` 增加标准 Transformers + PEFT fallback：

- GPU 且安装 Unsloth：使用原 Unsloth 路径。
- CPU 或未安装 Unsloth：使用 `AutoModelForCausalLM` + `PeftModel`。
- 输入 tensor 根据实际 device 放置，不再硬编码 CUDA。
- 兼容不支持 `enable_thinking` 的 tokenizer。

### 验收结果

线上真实任务：`ft-260822-051908-a269af`

线上 `/api/chat` 已成功返回：

```json
{
  "adapterVariant": "final",
  "adapterStep": 1,
  "adapterPath": "/workspace/training-work/ft-260822-051908-a269af/output",
  "text": "你好！"
}
```

Backend `/api/health`：`status=ok`、worker=idle、无活动训练任务。

### 测试

- 后端全量：56 tests passed
- Python compileall：passed
- TypeScript：passed
- Next.js build：passed
- git diff --check：passed

仅部署了本应用 backend 容器；未操作 PostgreSQL、MinIO、Redis、Nginx 或其他已有容器。

## 2026-08-22：训练完成但模型管理只显示基模

### 用户现象

训练任务已完成，但模型管理页面显示“可用基模 1、训练模型 0”，训练模型列表为空。

### 根因

生产环境使用 `XUNTUI_STORAGE_BACKEND=postgres`：训练任务权威数据存储在 PostgreSQL 的 `training_tasks` 表，而模型资产目录仍是 SQLite 兼容目录。原 `PostgresTaskStore` 未实现 `sync_model_assets()`，通过 `__getattr__` 委托到 SQLite `TaskStore` 后，资产同步只查询 SQLite 的 `tasks` 表，因此看不到 PostgreSQL 中已完成的训练任务。

同时，原同步逻辑把训练模型资产根路径固定构造成 `ARTIFACT_ROOT/<task-id>`，但 LLaMA-Factory 执行器实际将 Adapter 写入 `/workspace/training-work/<task-id>/output`，导致即使生成目录记录，路径也可能无效。

### 修复

- 新增 `PostgresTaskStore.sync_model_assets()`：读取 PostgreSQL `training_tasks` 的任务、状态和 artifacts，交给 SQLite catalog 的统一同步逻辑。
- 将 `TaskStore` 的同步逻辑拆成 `_sync_model_asset_rows()`，SQLite 和 PostgreSQL 使用同一套资产生成逻辑。
- 训练模型资产 `path` 改为真实 Adapter variant 所在任务目录的父目录；variant path 保留真实 `output` 目录。
- 增加回归测试：覆盖真实 Adapter 路径、完成任务生成训练资产，以及 PostgreSQL 行同步委托。

### 验证

- 后端全量：58 tests passed。
- Python compileall：passed。
- TypeScript：passed。
- Next.js production build：passed。
- 线上仅重建并重启本应用 backend，未操作 PostgreSQL、MinIO、Redis、Nginx 或其他基础设施容器。
- 线上 `/api/health`：HTTP 200，`storageBackend=postgres`，worker=idle。
- 线上 `/api/model-assets?includeArchived=true` 已返回训练模型：
  - `trained-ft-260822-051908-a269af`，状态 `available`，Adapter 路径 `/workspace/training-work/ft-260822-051908-a269af/output`。
  - 另外同步出此前已完成的 `ft-260822-050144-abbfd4`、`ft-260821-191055-0395d8`。
- 最近任务 `ft-260822-051908-a269af` 的共享存储记录 `model-e2328698179c41b6` 状态为 `available`。
- 服务器应用容器 backend/frontend 均为 running；frontend 无需重建。

未提交、未推送。

## 2026-08-22：创建推理服务可自选 Best / Final Adapter

### 用户现象

真实 CPU 训练任务 `ft-260822-051908-a269af` 只有 Final Adapter。模型管理已能看到训练模型，但创建推理服务时报：

```text
该训练任务没有可部署的 Best Adapter。
```

### 根因

- `InferenceRuntime._artifact_for_task()` 先读取 selected Adapter，却又额外检查 Best Adapter 必须存在，导致 Final-only 任务必然失败。
- `InferenceCreate` 和自定义命令校验请求没有 Adapter variant 字段，后端无法知道用户要部署 Best 还是 Final。
- 前端每个任务只生成一个默认产物选项，且文案和请求都固定为 Best Adapter。

### 修复

- `InferenceCreate`、`InferenceCommandValidate` 新增 `adapterVariant`，允许 `best` / `final`，默认 `best` 兼容旧请求。
- `_artifact_for_task(task_id, adapter_variant)` 严格解析用户选择的 variant，不再强制存在 Best Adapter。
- 创建服务和自定义 vLLM 命令预检都使用所选 variant；`--lora-modules` 必须匹配所选 Adapter 路径。
- 前端将任务中的 Best、Final Adapter 展开为独立选择项，并分别提交真实任务 ID 和 variant。
- 已完成和已停止任务均可展示可用产物；Final-only 任务可直接部署。
- 创建页、摘要、详情和调试说明改为按实际 variant 显示。

### 验证

- 新增/更新回归测试：Final-only 任务部署、Best/Final 同时存在时显式选 Final、请求不存在的 variant、Final 自定义命令校验与保存。
- 后端全量：61 tests passed。
- Python compileall：passed。
- TypeScript：passed。
- Next.js production build：passed。
- `git diff --check`：passed。
- 仅重建并重启本应用 backend/frontend；未操作服务器 PostgreSQL、MinIO、Redis、Nginx 或其他已有容器。
- 线上 `/api/health`：HTTP 200，worker=idle，inference=idle；前端 HTTP 200。
- Final-only 任务 `ft-260822-051908-a269af` 线上创建成功：服务 `svc-c3d85c6ddad4`，state=created，`config.adapterVariant=final`，Adapter 路径 `/workspace/training-work/ft-260822-051908-a269af/output`。该服务仅保存配置，未启动且未占用 GPU。
- 同一任务显式选择 `best` 返回 HTTP 400“该训练任务没有可部署的 Best Adapter”，证明后端没有错误回退到 Final。

未提交、未推送。

## 2026-08-22：PostgreSQL 推理存储 + CPU vLLM 改造交接（会话中断）

用户已确认开始执行：

1. 推理服务生产数据统一切换/确认使用 PostgreSQL，SQLite 只能作为本地兼容缓存。
2. backend 镜像增加独立的 CPU vLLM 环境 `/opt/venvs/vllm`，保留 `/opt/venvs/llamafactory` 不变，先跑通 CPU 流程，后续再切换 GPU 环境。

### 已定位的原始问题

服务 `svc-329adf79cc38` 启动失败的第一层根因是 backend 容器内没有 vLLM，日志为：

```text
ModuleNotFoundError: No module named 'vllm'
```

由于生产 PostgreSQL 返回 `datetime`，旧的 SQLite 日志格式化代码又执行了：

```python
row["created_at"].replace("T", " ")[:19]
```

这触发了：

```text
TypeError: 'str' object cannot be interpreted as an integer
```

第二个异常覆盖了第一个真实错误，并导致 `/api/inference/services/{id}/logs` 返回 HTTP 500。

### 已进行但尚未完整验收的修改

文件：`D:\训推平台\backend\inference_service.py`

- 统一 PostgreSQL `datetime` / SQLite 字符串时间的 API 序列化。
- 修复推理日志时间格式化。
- 启动 vLLM 前检查 `XUNTUI_VLLM_PYTHON` 和 `import vllm`，记录版本和环境诊断。
- 增加 CPU 推理模式：`XUNTUI_INFERENCE_DEVICE=cpu`；过滤/拒绝不适用于 CPU 的 GPU memory、bitsandbytes、FP8 参数。
- 使用 `XUNTUI_VLLM_LD_PRELOAD` 传递 CPU vLLM 需要的 OpenMP 动态库。
- CPU 服务状态显示为 CPU 资源。

文件：`D:\训推平台\requirements-vllm-cpu.txt`

- 暂记录官方 vLLM `0.27.1` x86_64 CPU wheel URL，尚未写入 Dockerfile 安装流程。

已执行检查：

```text
python -m py_compile backend/inference_service.py  # passed
```

### 下一会话必须继续完成

1. 增加 PostgreSQL 存储回归测试：服务时间字段、日志、指标、命令使用记录、归档/恢复/删除，使用 fake PostgreSQL adapter，不连接线上数据库。
2. 增加 vLLM 启动诊断和 CPU 参数过滤的单元测试，mock `subprocess.run`，不能真实启动 vLLM。
3. 修改 `deploy/docker/backend.Dockerfile`：
   - 保留 `/opt/venvs/llamafactory` 及其 CPU Torch 约束。
   - 新建 `/opt/venvs/vllm`。
   - 安装官方 x86_64 CPU wheel及其依赖。
   - 安装/确认 Intel OpenMP 动态库并配置 `LD_PRELOAD`，必须通过镜像内 `find` 或 Python/ldd 验证真实路径。
   - 构建阶段验证 `import vllm` 和版本输出。
4. 修改 `compose.production.yml` 和 `.env.production.example`：
   - `XUNTUI_VLLM_PYTHON=/opt/venvs/vllm/bin/python`
   - CPU 推理相关环境变量使用保守默认值。
   - 不将真实密钥写入文件或聊天。
5. 运行后端全量测试、compileall、前端 TypeScript/build、Compose config 检查。
6. 仅构建/重建本应用 backend，不能操作 PostgreSQL、MinIO、Redis、Nginx 或其他已有容器。
7. 线上验证顺序：
   - `/api/health` 返回 200 且 `storageBackend=postgres`。
   - 推理服务日志 API 不再 500。
   - 用已有 Final Adapter 任务创建/启动服务。
   - 检查 `/health`、`/v1/models` 和一次小请求；失败时优先读取 PostgreSQL 原始日志。

### 当前线上和工作区状态

- 线上仍是上一次已部署版本，没有本轮 vLLM/时间兼容修改。
- 当前工作区有大量阶段二未提交修改，不能以 Git reset/覆盖方式清理。
- 本轮未提交、未推送、未部署。
- 尚未操作服务器已有 PostgreSQL、MinIO、Redis、Nginx 或其他 Docker 容器。

### 新对话建议首条消息

```text
请继续执行 D:\训推平台 的 PostgreSQL 推理存储和 CPU vLLM 改造。

先读取：
- D:\训推平台\CURRENT_STATE.md
- D:\训推平台\BUGFIX_HANDOFF.md
- D:\训推平台\PHASE2_DEPLOYMENT_HANDOFF.md

不要 reset、覆盖、提交或推送；不要读取或输出 .env、数据库密码、MinIO 密钥和 SSH 私钥；不要操作服务器已有 PostgreSQL、MinIO、Redis、Nginx 或其他基础设施容器。

当前用户已确认：
1. 生产推理服务数据统一使用 PostgreSQL；
2. backend 镜像安装独立 CPU vLLM 到 /opt/venvs/vllm，保留 /opt/venvs/llamafactory。

请从“复核当前未完成修改和测试缺口”开始，先补回归测试，再完成 Dockerfile/Compose，最后只验证和重建本应用 backend。线上入口：
- 前端：http://185.61.210.32:13100
- Backend：http://185.61.210.32:18010/api
- 健康检查：http://185.61.210.32:18010/api/health
- 部署目录：/opt/cppt-platform
```

## 2026-08-22：CPU vLLM Ivy Bridge 指令集阻塞与最终验证

### 最终验证结果

- PostgreSQL datetime、日志、指标、命令使用、归档/恢复/删除回归测试已补齐。
- vLLM 独立 Python、`LD_PRELOAD`、import 错误、CPU 参数过滤和 `--no-enable-log-requests` 测试已补齐。
- 后端全量 `68 tests`、compileall、TypeScript、Next.js build、Compose config 和 `git diff --check` 均通过。
- 验证镜像 `cppt-platform-backend:vllm-cpu-verify-20260822-c` 构建成功；双 venv、LLaMA-Factory、vLLM 0.27.1、CPU torch、CPU torchaudio/torchcodec、API server import 和 `pip check` 均通过。

### 不能继续做真实 vLLM 冒烟的原因

生产机 CPU：

```text
Intel Xeon E5-2680 v2 @ 2.80GHz
x86_64
AVX2: no
AVX512: no
```

官方 vLLM CPU x86 构建最低使用 AVX2 或 AVX512。当前机器执行 Qwen2 模型架构检查时，注册子进程稳定收到 `SIGILL`。普通 `import vllm` 通过不代表 engine 可运行。

本地实现已新增启动前 CPU ISA 诊断：在 x86 CPU 模式明确缺少 AVX2 时直接提示迁移到 AVX2/AVX512 CPU 或 GPU 节点，不再进入必然 `SIGILL` 的 vLLM 子进程。对应回归测试已通过。

### 当前操作状态

- 生产 backend 容器仍 healthy，健康接口确认 `storageBackend=postgres`。
- PostgreSQL 推理日志接口已确认 HTTP 200。
- 本轮只构建并运行一次性验证镜像，没有切换生产镜像，没有再次重建/重启生产 backend。
- 未操作 PostgreSQL、MinIO、Redis、Nginx 或其他基础设施容器；未读取或输出真实 `.env` 和密钥。
- 未提交、未推送。

### 后续行动

1. 提供支持 AVX2/AVX512 的 CPU 节点，或切换计划中的 GPU 节点。
2. 在兼容节点重新构建/运行当前 backend 镜像，并依次验证模型 registry、vLLM `/health`、`/v1/models` 和一次最小推理请求。
3. 以上全部通过后，才允许只重建本应用 backend；不得联动基础设施容器。

## 2026-08-22：CPU 节点 GPU 状态文案修复（待发布）

线上诊断前已确认刚完成 tag 部署的 `cppt-platform-backend-1` 容器 healthy、restart count 为 0，健康接口正常；本轮未重建或重启生产容器。

本地修改：

- `backend/app.py`：缺少 `nvidia-smi` 的 `FileNotFoundError` 统一显示“当前节点未检测到可用 GPU”。
- `/api/gpu-info` 与训练进程失败后的 GPU 诊断日志使用同一友好文案。
- 其他 GPU 探测错误仍显示具体错误，避免丢失诊断信息。
- `backend/test_app_cli.py`：新增缺少 `nvidia-smi` 和其他探测错误的回归测试。

验证：后端全量 `70 tests`、Python compileall、`git diff --check` 通过。该修改仅在本地工作区，未部署、未提交、未推送，计划与 CPU vLLM 推理修复一起进入下次发版。

## 2026-08-23：数据集只读预览改版与 v3.0.7 发版

### 本次发布内容（tag v3.0.7，已推送 Gitee）

- `44c7ffb feat: 数据集只读预览改版为主从分栏并调整导入名称默认值`
  - 只读预览从扁平宽表 + 悬停卡片，改为主从分栏：左侧样本列表（约 30%，SFT 为 # / System / User / Assistant，DPO 为 # / User / Assistant-chosen / Assistant-rejected，两行截断、点击切换、选中行高亮），右侧详情区（约 70%，System 灰底 / User 蓝底 / Assistant 灰底 / Chosen 绿底 / Rejected 红底消息块）。
  - 默认选中当前页第一条样本；搜索、分页、跳页保持原有行为；≤1000px 上下堆叠。
  - 导入数据集弹窗“数据集名称”默认为空，打开弹窗与切换文件都不再自动填充文件名，留空提交时提示“请填写数据集名称。”。
- `f514e3a fix: 完善 GPU 探测文案与 CPU vLLM 启动前 ISA 诊断`（上一会话遗留的工作区改动，按交接计划随本版发布）
- `36ec0e2 ci: 新增 tag 触发的 Drone 部署流水线`（.drone.yaml，首个 tag 触发即为 v3.0.7）

### 已知坑（下个会话排障先看这里）

- 全局样式 table 的 min-width 1060px（app/globals.css，为主列表设计）会泄漏到新建表格。数据集记录列表已用 .record-list-table 的 min-width 0 覆盖；以后新增内嵌小表格同样要覆盖。
- 本地前端直连后端时 env 必须带 /api 后缀：NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8010/api npm run dev。后端路由本身带 /api 前缀，少写后缀会导致所有请求 404 且列表页静默不报错。
- 停 npm run backend 只会结束 Windows 侧 supervisor，WSL 内 uvicorn 仍占 8010。重启后端报 address already in use 时执行：wsl.exe -d Ubuntu-24.04 -- pkill -f "uvicorn backend.app:app"。
- 本会话结束时本地 dev 服务（前端 3000 / 后端 8010）可能仍在运行，新会话开工前先检查端口。

### 验证记录

- npx tsc --noEmit、npm run build 通过。
- 本地起前后端实测：上传 SFT/DPO 测试数据集走通只读视图（分栏比例、行切换、选中高亮、列可见性均用 DOM 度量确认），导入弹窗空名称与切换文件行为实测通过；测试数据集与文件已删除，列表仅剩原有 qwenruozhi_v1。
- v3.0.7 的 Drone 流水线为首次运行，结果未验证。下个会话第一件事：确认流水线执行与线上版本（http://185.61.210.32:13100 与 /api/health）。

**2026-08-23 线上核验结果**：流水线部署正常，无需处理。

- `http://185.61.210.32:18010/api/health` → 200，worker idle、queue postgres，任务接口 `/api/tasks` → 200。
- `http://185.61.210.32:13100` 公网拒绝连接是**预期状态**：v3.0.4 起切换网关模式，前端容器只绑 127.0.0.1:13100（Drone 部署步骤强制断言），早期文档里 13100 直连地址已过时。
- 正式入口：`http://185.61.210.32/`（Control Center 门户，80 端口 200）；应用网关入口 `:13101` 未登录时 302 回门户登录页，行为正常。
- 网关 80 端口 `/api/health` 可通，但 `/api/tasks` 等业务路由返回网关自己的 404（“接口不存在”）——网关仅反代了 health，前端镜像仍按绝对地址 `http://185.61.210.32:18010/api` 构建直连后端，属已知待办（见 PHASE2_DEPLOYMENT_HANDOFF.md「前端切回 /api 同源构建」）。
- 剩余无法匿名核验项：前端确为 v3.0.7 构建（应用在登录墙后）。需登录门户进入应用，确认数据集只读预览已是主从分栏（v3.0.7 特征）。
  **2026-08-23 用户已登录确认：线上前端为新版主从分栏，v3.0.7 发版验证完结。**

## 2026-08-23：阶段五优云 SSH 远程训练开发（进行中）

### 已落地代码（工作区未提交）

- `backend/providers/youyun/`：signer（与官方 ucloud-sdk verify_ac 逐字节一致，含 doctest 基准测试）、models（配置/实例/规格）、client（签名请求、超时重试、数组拍平、全部生命周期与发现类 API）、instance_manager（一次性实例：库存检查→创建→轮询 Running→SSH 凭据；terminate 永不抛异常并返回清理报告）、ssh（paramiko 惰性导入，run/SFTP/put_text/get_tree 递归下载）、remote_training（编排器：安装环境→上传→启动→SSH 轮询日志/指标→产物回传→finally 清理实例）、check（分步引导式联通性检查）。
- `backend/app.py`：provider=youyun 任务分发（run_youyun_training）、提交校验 validate_youyun_ready（无库存 409）、`GET /api/youyun/gpu-specs`、本地镜像执行器（youyun_mirror_executor）。
- 前端 `components/training-module.tsx`：执行环境选择（本地 CPU / 远程优云 GPU）、优云规格库存面板（机型售罄状态 + 实时库存 + 无库存阻断提交）；远程任务不再要求本地模型已下载。`app/globals.css` 配套样式。
- 依赖：`requirements-storage.txt` 增加 `paramiko>=3.4,<4`。WSL 后端环境（unsloth_env）已装 paramiko。
- 测试：`backend/test_youyun_provider.py` / `test_youyun_instance_manager.py` / `test_youyun_remote_training.py` 共 24 个单测全过（成功/失败/停止/清理失败路径全覆盖）；`npx tsc --noEmit`、`npm run build` 通过。Windows 下 `python -m unittest discover` 有 12 个历史环境性失败（基线对比确认与本次改动无关，正式回归以 Drone Linux 为准）。

### 优云配置（.env，已验证连通）

- 凭据有效；区域 cn-wlcb / 可用区 cn-wlcb-01；镜像 `compshareImage-1minbz219ceq`（cuda128_torch291_py312）；GPU 4090 可售，1卡/16核/64GB 有库存；计费 Postpay。
- `python -m backend.providers.youyun.check` 三步引导：只填公私钥即可自动查出可用区与镜像建议。

### 关键实现决策

- 签名算法：拼接 `key值key值…私钥` 取 SHA-1（无 urlencode、无分隔符；bool 小写、整数浮点去尾）。经典 UCloud 查询串签名在此平台不可用（RetCode=171）。
- 远程 YAML：本地镜像目录完成路径校验生成 train.yaml，上传前改写 dataset_dir/output_dir 为远端镜像并强制 bf16=true（转换器默认面向 CPU）。
- 停止机制复用 store cancel_requested 标记，runner 每 5 秒轮询；手动停止后尽力回传产物，任何路径 finally 删除实例，清理失败写日志提示手动删除。
- 远程模型下载：USE_MODELSCOPE_HUB=1，模型缓存在实例 /root/cppt/modelscope-cache。

### 真实冒烟已通过（2026-08-23 12:40）

scripts/youyun_smoke.py（WSL unsloth_env python 运行）完整链路一次跑通：库存检查 → 创建一次性 4090（uhost-1ueamxb9ygpr）→ SSH + GPU 识别 → 环境安装 → 上传 → ModelScope 下载 Qwen2.5-0.5B → QLoRA 6 步训练（返回码 0，可训练参数 108 万）→ 日志/指标实时回传 → 产物回传本地镜像（adapter + checkpoint-6 + manifest）→ 实例释放删除（API 确认无残留）。产物保留在 runtime/remote-jobs/smoke-08231240/。

冒烟过程发现并修复的问题（都已带回归测试）：
- 优云 Terminate/Stop/Start 需要 Region+Zone+单数 UHostId（缺 Zone 时报 RetCode=8433 Service error，易误判为服务端故障）。
- SshLoginCommand 实际为 `ssh -p 23 root@ip`（端口在前），解析需位置无关。
- CUDA 镜像的 torch 在 /usr/local/miniconda3/envs/py312/bin/python，/usr/bin/python3 无 pip；安装脚本需按 conda env 探测。
- 新版 llamafactory 不再捆绑 bitsandbytes，需显式安装（QLoRA 必需）。
- 转换器 bug：finetuning_type 曾原样下发 "qlora"（LLaMA-Factory 只认 lora/oft/freeze/full），已改为 qlora→lora+quantization_bit:4（CPU 路径保持不量化）。
- WSL DNS 偶发解析失败（Errno -3），client 网络重试提高到 3 次。

### 待办

- 全链路验收：通过 Web UI 创建远程任务，核对任务详情页日志/损失曲线/产物与本地一致（验收标准 2、4 的 UI 部分）；发版 tag 走 Drone 部署后再在生产验收。
- REQUIREMENTS 第 29 节验收清单逐条勾稽后归档阶段五。

## 2026-08-23：会话中止交接指引（给下一位助手，任意模型通用）

### 当前工作区状态（未提交，全部在磁盘上）

阶段五「优云 SSH 远程训练」**代码与真实冒烟已全部完成**，工作区改动清单：

- 新增 `backend/providers/youyun/`：`signer.py`（UCloud 式签名，与官方 SDK verify_ac 逐字节一致）、`models.py`（配置/实例/规格）、`client.py`（API 客户端+重试+数组拍平）、`instance_manager.py`（一次性实例生命周期，terminate 永不抛异常）、`ssh.py`（paramiko 惰性导入）、`remote_training.py`（远程训练编排器）、`check.py`（联通性分步检查，`python -m backend.providers.youyun.check`）。
- 新增测试：`backend/test_youyun_provider.py`、`test_youyun_instance_manager.py`、`test_youyun_remote_training.py`（共 24 个，全过）。
- 新增 `scripts/youyun_smoke.py`（真实冒烟脚本，WSL 运行：`wsl.exe -d Ubuntu-24.04 -- bash -lc "cd /mnt/d/训推平台 && PYTHONUNBUFFERED=1 /root/miniconda3/envs/unsloth_env/bin/python scripts/youyun_smoke.py"`）。
- 修改 `backend/app.py`：provider=youyun 任务分发、`GET /api/youyun/gpu-specs`、提交校验 validate_youyun_ready。
- 修改 `backend/llamafactory.py`：qlora→lora+quantization_bit:4 修复（CPU 不量化）。
- 修改 `components/training-module.tsx`、`app/globals.css`：执行环境选择 + 优云库存面板。
- 修改 `requirements-storage.txt`（+paramiko）、`.env`/`.env.example`（YOUYUN_* 配置）。
- `.env` 中优云凭据已配好且验证有效：cn-wlcb / cn-wlcb-01 / 镜像 compshareImage-1minbz219ceq / 4090 / Postpay。

### 下一步（按顺序做）

1. **Web UI 全链路验收**（验收标准 1、2、4 的 UI 部分）：
   - 起后端（WSL）：`npm run backend`（内部用 /root/miniconda3/envs/unsloth_env/bin/python 起 uvicorn 8010；端口被占时 `wsl.exe -d Ubuntu-24.04 -- pkill -f "uvicorn backend.app:app"`）。
   - 起前端：`NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8010/api npm run dev`（**必须带 /api 后缀**，否则全部 404）。
   - 页面新建微调任务 → 执行环境选「远程优云 GPU」→ 确认库存面板展示 → 提交 → 任务详情页核对日志、损失曲线、产物与本地任务一致 → 停止/失败场景确认实例被删。
   - 注意：本地 .env 的 POSTGRES_DSN 指向生产库（185.61.210.32:35432），本地测试任务会出现在生产任务列表，测试完删除。
2. **回归与提交**：`python -m unittest discover -s backend -p "test_*.py"` 在 Windows 有 12 个历史环境性失败（基线对比过，与本次无关；Linux/Drone 全绿为准）；`npx tsc --noEmit`、`npm run build` 应通过。然后按仓库惯例提交（feat: 阶段五优云 SSH 远程训练）。
3. **发版**：打 tag（v3.0.8）触发 Drone → 验证 http://185.61.210.32:18010/api/health 与门户。
4. **归档**：对照 REQUIREMENTS_LLAMA_FACTORY_REMOTE_TRAINING.md 第 29 节验收清单逐条勾稽，更新文档。

### 关键坑（排障先看）

- 优云实例操作（Start/Stop/Terminate）必须带 Region+Zone+单数 UHostId；缺 Zone 报 RetCode=8433（伪装成服务端错误）。
- SshLoginCommand 是 `ssh -p 23 root@ip` 顺序。
- CUDA 镜像 torch 在 /usr/local/miniconda3/envs/py312/bin/python，安装脚本已自动探测。
- llamafactory 需显式装 bitsandbytes（QLoRA 必需）。
- 远程训练 YAML 由本地镜像生成后改写 dataset_dir/output_dir/bf16=true 再上传（bf16 只影响远程）。
- 每次冒烟会真实计费（4090 Postpay 约 ¥2-3/小时，全流程约 15 分钟）；失败路径会自动删实例，若进程被强杀需手动 `TerminateCompShareInstance`（带 Zone）清理。

## 2026-08-23：阶段五 Web UI 全链路验收完成并随 v3.0.8 发布

成功任务：`ft-260823-141622-2390`

- 页面选择“远程优云 GPU”，库存面板展示 4090、cn-wlcb/cn-wlcb-01、1 卡/16 核/64GB 有库存。
- Qwen2.5-0.5B-Instruct、SFT、QLoRA、batch 1、gradient accumulation 1、cutoff 64、max_steps 2、logging_steps 1。
- 真实 4090 实例 `uhost-1uee7f5am0t1`：SSH/GPU 探测、环境安装、数据与 YAML 上传、ModelScope 下载、2-step 训练、验证、日志/指标回传、产物回传全部完成，返回码 0。
- 页面终态 completed / 100%，Loss 图可见，Final Step 2；Final Adapter 与 manifest 路径可见，Final Adapter 已上传共享存储。
- 实例已在 finally 中释放删除，无残留。

Web 验收期间发现并修复：

1. `TaskStore._serialize()` 未返回 provider/executionMode，Worker 把远程任务当本地任务执行。
2. Worker 组装执行任务时把 provider 错写到 executionMode，触发 `LlamaFactoryTask` 校验失败。
3. 显式验证集与 `val_size` 同时下发，新版 LLaMA-Factory 拒绝；远程 YAML 现以 eval_dataset 为准。
4. UI 默认 `targetModules=auto` 被当成真实模块名；现映射到 Q/K/V/O attention projections。
5. 优云新实例 SSH 可能在首次命令后失活；SSH 客户端现支持 keepalive 与发送前重连。
6. SSH 抖动可能让后台安装启动命令未落地；现使用远端 started 标记与幂等补发。
7. Final-only 任务列表文案错误声称不能创建推理服务；已改为可创建推理服务。

最终回归：Linux/WSL 后端 100 tests、Python compileall、`npx tsc --noEmit`、`npm run build`、`git diff --check` 全部通过。

### v3.0.8 线上验收

- 提交 `f934c82 feat: 阶段五优云 SSH 远程训练`、tag `v3.0.8` 已推送，Drone tag 流水线成功完成。
- 生产 `cppt-platform-backend-1` 与 `cppt-platform-frontend-1` 均运行 `v3.0.8` 且 healthy。
- `/api/health` 返回 `status=ok`、`queue=postgres`、`storageBackend=postgres`、`worker=idle`、`activeTaskId=null`、`inference=idle`。
- 生产 `.env.production` 已备份后补齐 8 项 `YOUYUN_*` 运行配置，只重建应用 backend；未改动 PostgreSQL、MinIO、Redis、Nginx 或其他基础设施容器。
- `GET /api/youyun/gpu-specs` 返回 HTTP 200，4090 规格可售；生产当前无运行或排队训练任务。
- 从 Control Center 正式入口打开 CPPT Platform，微调任务页成功连接生产后端；新建任务页已核对“远程优云 GPU / 一次性实例 · 按量计费”入口。

## 2026-08-23：优云手动资源与推理服务边界（本轮交接）

### 已完成

- 移除微调页中仅按最低成本选择规格的自动配置入口，保留手动选择 GPU 型号、单卡、CPU 核数和内存。
- 新增优云实例价格查询与规格校验：页面调用 `/api/youyun/price` 展示按量价格，创建任务时后端再次按实时库存和所选规格校验。
- 优云训练任务会持久化 `youyunResource`，远程 runner 使用同一组 GPU/CPU/内存参数创建一次性实例；训练结束、失败或取消后走 finally 清理并删除实例。
- “命令行执行”仍复用同一任务 API：本地执行在本地训练节点，远程优云执行在 SSH 连接的优云 GPU 实例。

### 推理服务现状

当前 `backend/inference_service.py` 只负责本地 vLLM 子进程，默认使用后端主机的本地 GPU lease 和 `127.0.0.1:8011` 私有端口。它没有调用 `backend/providers/youyun`，因此暂时不能直接在优云创建实例部署 vLLM，也没有远程模型上传/下载、SSH 启停和实例回收流程。若要用优云部署模型做 vLLM 测试，应新增独立的 Youyun remote inference Provider/Runner，并明确实例类型、镜像、权重传输和停止/删除策略。

### 下一步

1. 先完成本轮回归、提交并发布 `v3.0.9`。
2. 若需要优云 vLLM 测试，另立需求实现远程推理生命周期，不把训练 runner 临时改造成推理服务。
