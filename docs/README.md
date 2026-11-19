# 训推平台文档索引

## 架构与设计

- [架构说明](../ARCHITECTURE.md)
- [设计说明](../DESIGN.md)
- [工程规范与变更门禁](训推平台-工程规范与变更门禁.md)
- [设计工作流](design-workflow.md)
- [训练平台流程与参数](TRAINING_PLATFORM_FLOW_AND_PARAMETERS.md)
- [架构决策记录](adr/)

## 状态与基线

- [当前状态](../CURRENT_STATE.md)
- [项目基线](../PROJECT_BASELINE.md)
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

- [LlamaFactory 远程训练需求](../REQUIREMENTS_LLAMA_FACTORY_REMOTE_TRAINING.md)
- [调研记录](research/)
- [项目学习资料](project-learning/)
- [归档文档](archive/)

## 根目录保留文件

以下文件被 CI、Dockerfile、部署脚本或既有文档按根路径引用，继续保留在仓库根目录：

- `README.md`、`AGENTS.md`、`CONTRIBUTING.md`
- `ARCHITECTURE.md`、`DESIGN.md`、`CURRENT_STATE.md`、`PROJECT_BASELINE.md`
- `REQUIREMENTS_LLAMA_FACTORY_REMOTE_TRAINING.md`
- `requirements-*.txt`：`deploy/docker/backend.Dockerfile` 与 `.drone.yml` 按根路径复制或安装。
- `compose.production.yml`：`deploy/scripts/*` 与 `.drone.yml` 按根路径引用。

> 这些文件不移动，避免破坏镜像构建、CI 与部署脚本。
