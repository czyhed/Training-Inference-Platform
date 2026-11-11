# Training-Inference Platform（训推平台）

面向大模型的数据集管理、微调训练、模型资产管理与推理服务平台。前端 Next.js，后端 FastAPI，支持本地 GPU 运行时与远端按量 GPU 实例两种执行方式。

> 这是项目的公开导出副本：生产 IP 已替换为 RFC 5737 文档地址，内网域名已替换为 `example.com`，内部部署记录、人员信息、数据集与运行数据均已移除，仓库内不含任何真实凭据。

## 功能范围

| 模块 | 能力 |
| --- | --- |
| 数据集 | 文本 JSONL（SFT/DPO）上传与结构校验、错误行定位与修复、发布/克隆/分页预览；视觉 `IMAGE_TEXT` ZIP、文生图 ZIP |
| 微调任务 | LoRA / QLoRA，LLaMA-Factory 生成配置，Best/Final Adapter，早停、Loss 曲线、日志、停止/重试/克隆、Base 与 Adapter 对比 |
| 模型管理 | ModelScope 搜索、缓存扫描、基座注册、Adapter 血缘同步、重命名/归档/恢复 |
| 推理服务 | vLLM + LoRA，Base Model + Best Adapter，4-bit BitsAndBytes，托管参数与受校验的自定义命令，指标、日志、SSE 在线调试 |
| 视觉理解 | 多模态数据集、视觉 SFT/DPO、模型能力探测、视觉推理与评测 |
| 文生图 | SDXL LoRA 训练、Diffusers 任务运行时、生成工作台 |
| 语音合成 | VoxCPM2 CPU worker，声音设计/克隆/续写 |
| 资源调度 | 单卡 GPU 执行锁，训练与推理互斥；远端一次性 GPU 实例的创建、释放与清理确认 |

## 架构

```text
Next.js 14 (App Router) + React 18 + TypeScript
        │  /api
        ▼
FastAPI ── SQLite（本地队列/回退）
        ├─ PostgreSQL（共享元数据）
        ├─ MinIO（模型权重对象）
        ├─ LLaMA-Factory / vLLM 子进程（本地 GPU）
        └─ 远端 GPU 提供方（SSH/SFTP、一次性实例、远端日志跟随）
```

- 训练与推理共用一把执行锁，单卡环境下不会并发占用 GPU。
- 后端通过 `GET /api/capabilities` 下发运行时能力边界，并在任务创建、推理生命周期等入口做服务端校验。
- 云资源清理失败会保留远端句柄并阻止重跑，直到清理确认成功。

## 技术栈

Next.js 14 · React 18 · TypeScript · FastAPI · Python 3.11+ · SQLite / PostgreSQL · MinIO · LLaMA-Factory · vLLM · Docker Compose

## 本地运行

```bash
# 前端
npm install
npm run dev

# 后端
python -m venv .venv
pip install -r requirements-storage.txt
uvicorn backend.app:app --host 127.0.0.1 --port 8010
```

复制 `.env.example` 为 `.env` 并填写本地配置。默认使用 SQLite；接入 PostgreSQL / MinIO 时再打开共享存储开关。

## 测试与构建

```bash
npm run test:frontend      # 前端单元测试
npx tsc --noEmit           # TypeScript 类型检查
npm run build              # Next.js 生产构建
python -m unittest discover -s backend -p "test_*.py" -v   # 后端测试
```

后端测试中的 GPU、SSH、云厂商相关用例默认使用替身，不会真的创建实例；`scripts/` 下的验收脚本需要真实环境，按需运行。

## 目录结构

```text
app/          页面路由（概览、语音工作台）
components/   业务模块（数据集、训练、模型、推理、视觉、文生图、语音）
lib/          共享类型、API 客户端与前端测试
backend/      FastAPI 应用、领域服务、存储与执行器（含 76 个测试文件）
scripts/      启动、CLI、验收与运维辅助脚本
deploy/       镜像构建与远端训练运行时的参考定义
test-data/    最小示例数据
```

## 说明

- `deploy/` 中的 Dockerfile 是生产构建的参考实现，本地运行时依赖的 wheelhouse 需要替换为你自己的镜像源。
- 仓库不包含数据集、模型权重、运行时产物和任何密钥；`.env`、`data/`、`tls/`、`.secrets/` 已在 `.gitignore` 中排除。
- 生产部署相关的内网地址、主机名与操作记录不在本仓库中。
- 本仓库未附开源许可证，默认保留所有权利。
