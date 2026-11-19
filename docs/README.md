# 训推平台文档索引

## 架构与设计

- [架构说明](ARCHITECTURE.md)
- [设计说明](DESIGN.md)
- [工程规范与变更门禁](训推平台-工程规范与变更门禁.md)
- [设计工作流](design-workflow.md)
- [训练平台流程与参数](TRAINING_PLATFORM_FLOW_AND_PARAMETERS.md)
- [架构决策记录](adr/)

## 状态与基线

- [当前状态](CURRENT_STATE.md)
- [项目基线](PROJECT_BASELINE.md)
- [问题与 BUG 审计报告](训推平台-问题与BUG审计报告.md)
- [变更记录](changes/)

## 部署与发布

- [新服务器部署](DEPLOYMENT-NEW-SERVER.md)
- [GitHub / 优云生产发布](RELEASE-GITHUB-YOUYUN-PRODUCTION.md)
- [v3.0.97 发布说明](RELEASE-v3.0.97.md)
- [优云社区镜像手册](youyun-community-image-runbook.md)
- [优云 vLLM 镜像](YOUYUN_VLLM_IMAGE.md)
- [voxcpm2 CPU 部署](voxcpm2-cpu.md)
- [发布记录](releases/)

## 需求与调研

- [LlamaFactory 远程训练需求](REQUIREMENTS_LLAMA_FACTORY_REMOTE_TRAINING.md)
- [调研记录](research/)
- [项目学习资料](project-learning/)
- [归档文档](archive/)

## 仓库目录结构

- `app/`、`components/`、`lib/`：Next.js 页面、组件与客户端逻辑。
- `backend/`：FastAPI 服务。后端测试与源码同目录，`.drone.yml` 以 `python -m unittest discover -s backend -p 'test_*.py'` 运行；保留原位是为了不破坏测试中基于文件位置的相对路径。
- `data/`：内置样例数据集，被前端与 `scripts/` 按相对路径引用，不可移动。
- `test-data/`：脚本测试样本，被 `scripts/verify_best_checkpoint_e2e.py` 默认引用。
- `deploy/`：镜像构建、部署脚本与优云配置。
- `docs/`：架构、状态、部署、需求与调研文档。
- `scripts/`：训练、验收与评测脚本。
- `public/`：Next.js 静态资源。

## 根目录保留文件

以下文件被 CI、Dockerfile、部署脚本或既有流程按根路径引用，继续保留在仓库根目录：

- `README.md`、`AGENTS.md`、`CONTRIBUTING.md`：仓库入口与协作约定。
- `requirements-*.txt`：`deploy/docker/backend.Dockerfile` 与 `.drone.yml` 按根路径复制或安装。
- `compose.production.yml`：`deploy/scripts/*` 与 `.drone.yml` 按根路径引用。

> 这些文件不移动，避免破坏镜像构建、CI 与部署脚本。架构、状态、设计、基线与需求类文档统一收在本目录。
