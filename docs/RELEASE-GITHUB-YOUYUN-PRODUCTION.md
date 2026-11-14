# GitHub 主线迁移与生产环境优云专用化

## 发布内容

- 默认 Git 远程改为 GitHub `origin`，Gitee 仅保留为兼容远程。
- `scripts/release-bundle.sh` 在上传 bundle 后向 GitHub `origin` 推送发布 tag，由 Drone 按 tag 构建和部署。
- 生产环境通过 `XUNTUI_LOCAL_RUNTIME_ENABLED=false` 仅开放优云训练和推理；评测功能在数据集与流程验收完成前通过 `XUNTUI_EVALUATION_ENABLED=false` 隐藏并禁止写操作。开发环境默认继续保留本地运行与评测能力。
- 生产镜像通过 `INSTALL_LOCAL_RUNTIME=0` 跳过 Torch、LLaMA-Factory 和 CPU vLLM，只安装 API、存储与优云编排依赖。
- 后续启用评测时，仅接受仍保留优云实例的已完成优云任务，并复用任务记录的原始 Base Model 来源和 Adapter。

## 发布前检查

1. 仓库管理员在 GitHub 将默认分支设置为 `master`。
2. 确认 `origin` 指向 `https://github.com/leifengyang/cppt-platform.git`，当前发布分支跟踪 `origin/*`。
3. 确认生产环境包含：

   ```env
   XUNTUI_LOCAL_RUNTIME_ENABLED=false
   XUNTUI_EVALUATION_ENABLED=false
   XUNTUI_MODEL_CATALOG=youyun-public
   ```

4. 运行后端测试、前端测试、TypeScript 检查、Next.js 构建与 `docker compose --env-file .env.production.example -f compose.production.yml config --no-env-resolution --quiet`。
5. 使用 `scripts/release-bundle.sh <tag>` 上传 bundle 并推送 GitHub tag，等待 Drone 完成部署。

## 上线验收

- `/api/health` 返回正常，并显示 `storageBackend=postgres`。
- `/api/capabilities` 返回 `localRuntimeEnabled=false`，训练和推理 provider 均只有 `youyun`。
- 页面不显示本地训练、本地推理和命令行训练入口；历史本地记录仍可查看和归档。
- 分别完成一次优云训练和优云推理；评测验收推迟到评测数据集与功能完成后。
- 后续启用评测前，验证它使用训练任务记录的公共模型路径或相同仓库 ID 的优云远程下载，没有替换 Base Model。

## 回滚

本次不修改数据库结构。保留上一版本镜像 tag；如上线验收失败，将生产服务切回上一 tag，并保留失败版本日志用于排查。
