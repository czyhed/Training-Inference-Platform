# 新服务器部署运维手册（110.42.51.234）

更新时间：2026-10-08（Git / Drone 迁移到 Gitea；本次未发布生产版本）

本文是新服务器的部署事实与排障记录。迁移过程的全景（数据搬运、控制中心踩坑等）在迁移交接文档里，本文只保留**以后发版和排障每天会用到的部分**。

## 1. 生产拓扑速查

| 组件 | 位置 |
|---|---|
| 平台入口 | `http://110.42.51.234:13101/`（nginx 网关 → `127.0.0.1:13100` 前端，无登录门） |
| 平台容器 | `cppt-platform-backend-1`（8010，宿主映射 18010）/ `cppt-platform-frontend-1`（3100 → 127.0.0.1:13100） |
| Compose 目录 | `/opt/cppt-platform`（`.env.production` + `compose.production.yml` + `deploy-data/`） |
| PostgreSQL | 容器 `postgresql_18_test-postgresql_18_test-1`，宿主 `:35432`，平台库 `cppt_platform`，应用角色 `cppt_platform_app` |
| MinIO | 容器 `minio_test-minio_test-1`，宿主 `:19000`，桶 `rag-assets`；另有 `wheelhouse` 桶（构建轮子，勿删） |
| Drone | `http://drone.leiziapi.cn:4400/leifengyang/cppt-platform`，新 Gitea Drone 和 runner，仍在生产 Docker 主机 |
| Git 远程 | `origin` = Gitea `http://git.leiziapi.cn:3003/leifengyang/cppt-platform.git`；本机 `github` 仅历史读取、禁用推送 |
| Control Center | **已停止**（数据保留），恢复方式见 §7 |

服务器到 GitHub / pytorch.org 等国际站点出网被掐（单连接 10–60KB/s 且常 reset），到国内镜像（aliyun/tuna/npmmirror）正常。**所有 CI 出网必须走国内镜像或本机 MinIO wheelhouse。**

## 2. 发版 SOP

普通 `git push` 推送到 Gitea，并触发 `gitea-ci` 自动检查。发布前确认已验收提交包含最新 `.drone.yml`；当前 Gitea 默认分支为 `claude/gallant-kepler-89b691`，不要把旧 `master` 或旧标签当作最新代码。

```bash
git tag -a <new-version> -m "release note"
bash scripts/release-bundle.sh <new-version>
```

兼容脚本现在只校验 `origin` 的 Gitea 推送地址，再执行 `git push origin refs/tags/<new-version>`。新 Drone 直接克隆 Gitea 对应 SHA；无需 bundle、scp 或服务器 root SSH。脚本在 WSL 中可复用 Windows `git.exe` 和凭据管理器。

- 普通 push / PR / 手动检查只跑测试与前端构建；tag 才执行生产镜像构建和部署。
- Drone 仓库配置 `.drone.yml`、Active、Trusted，timeout 240 分钟；Webhook 指向新 Drone `:4400`。
- 发布继续使用 `/opt/cppt-platform/.env.production`、Docker socket 和原持久化目录；先检查活动任务，再按原健康检查与回滚流程部署。
- 从 [Drone 构建页](http://drone.leiziapi.cn:4400/leifengyang/cppt-platform) 查看状态和步骤日志；旧 `:4399` 是历史 GitHub Drone。
- 不复用已有标签，不批量推送旧标签来测试迁移。

## 3. 镜像构建规则（backend.Dockerfile）

- pip 环境四件套（在 Dockerfile ENV 里）：`PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple`、`PIP_FIND_LINKS=http://110.42.51.234:19000/wheelhouse/index.html`、`PIP_TRUSTED_HOST=110.42.51.234`、`PIP_RETRIES=10 PIP_TIMEOUT=60`。
- **三条 pip 铁律**（每个都是实测踩坑）：
  1. 明文 HTTP 的 find-links 会被 pip **静默忽略**——必须 trusted-host，否则报 "no matching distribution"。
  2. 索引和 find-links 有同版本候选时 pip 选索引——所以 torch 系列安装**不能**给 `--index-url download.pytorch.org`。
  3. `--no-index` 会连 torch 的普通依赖（filelock 等）一起挡掉——torch 安装只给 `--find-links`：`+cpu` 本地版本号只有 wheelhouse 有，wheel 必走本地（内网速度），依赖自动走清华。
- apt 用 aliyun（deb822 格式 sed 替换）；apk 用 aliyun；npm 已锁 npmmirror（package-lock.json）。
- **不要 `docker pull python:3.11-slim-bookworm`**——会刷新 digest 使全部缓存层失效。
- wheelhouse 桶现有内容：torch 2.7.1/2.13.0 两个世代 +cpu 全家桶、vllm 0.27.1+cpu、10 个精确版本依赖轮子、index.html。缺轮子时兜底：本地或服务器 `pip download <pkg>==<ver> -d /opt/wheelhouse` 循环重试 + mc cp 进桶 + 重建 index.html（mc 用法见迁移 CHECKLIST）。

## 4. 服务器侧关键配置

- `/opt/cppt-platform/.env.production`：真实 DSN/MinIO 凭据（MinIO 用容器 printenv MINIO_ROOT_*，.env 里自带的那对不对）；`XUNTUI_AUTH_MODE=disabled`（当前无登录门）。备份后缀 `.bak-YYYYMMDD-*`。
- pg 容器内 127.0.0.1 是 **trust**（pg_hba），本地 psql「登录成功」不代表密码对；验证凭据必须走非 127.0.0.1 的 scram 路径（如 `docker run --network host` + psycopg 重放 DSN）。
- psql `docker exec -e PW=... psql -c "... PASSWORD '$PW'"` 不会做 shell 替换——密码会被设成字面量 `$PW`。bash 内联展开再传。
- `cppt_platform` 库的表属主是 `cppt_platform_app`（20 张表）；如果从 dump 重新恢复库，必须重做：CREATE ROLE + GRANT + 逐对象 ALTER OWNER（`REASSIGN OWNED` 会撞系统必需对象）。
- 从 v3.0.148 起，平台专用网关由仓库 `deploy/nginx/cppt-platform.production.conf` 和 `scripts/install-platform-gateway.sh` 管理；Drone 拷贝到 `/opt/cppt-platform` 后安装 `/etc/nginx/conf.d/cppt-platform.conf`，不再调用 Control Center 的共享网关生成脚本。保留纯代理/无登录门、静态资源缓存和页面不缓存策略，并转发 WebSocket Upgrade。

## 5. 常见排障路径

| 症状 | 先查 |
|---|---|
| Drone clone 失败 | 检查 Gitea 仓库权限、OAuth 凭据、网络和构建 SHA；新流水线不使用 bundle |
| build-backend-image 报 no matching distribution | wheelhouse 缺轮子或 PIP_TRUSTED_HOST 没生效 |
| backend 起不来，日志 FATAL password authentication | pg 角色密码 vs .env.production DSN；用 §4 的 scram 路径验证 |
| backend 起不来，must be owner of table X | 库被重新恢复过，属主又变回 postgres 了，重做 ALTER OWNER |
| 13101 返回 500/404 | `/etc/nginx/conf.d/cppt-platform.conf` + `docker ps \| grep control`（控制中心停了就对了，网关是纯代理） |
| 容器反复重启 | `docker logs --tail 50 cppt-platform-backend-1`；健康检查是 `curl 127.0.0.1:8010/api/health` |
| 前端 API 404/CORS | 前端按 `/api` 同源构建，网关只反代 13100；检查 XUNTUI_CORS_ORIGINS 是否含访问来源 |

## 6. 已知遗留

- backend 仍绑 `0.0.0.0:18010`（公网可达，历史过渡模式）；前端已收 127.0.0.1。网关支持 `/api` 反代后收紧 `XUNTUI_BACKEND_BIND=127.0.0.1`。
- 生产 Dockerfile 默认 `INSTALL_LOCAL_RUNTIME=0`；Compose 强制 `XUNTUI_LOCAL_RUNTIME_ENABLED=false`。发布后确认 `/api/capabilities`，构建耗时以 Drone 实测为准。
- 平台当前无登录门（AUTH_MODE=disabled），地址不要外传。
- 服务器 root 密码 / 控制中心 pg 密码曾在聊天和文档中暴露，建议轮换；优云/评测 API Key 择机轮换。
- 本地工作区 4 个未提交文件（讲义/文档/globals.css 格式化）与部署无关。

## 7. Control Center 恢复（如果以后要 SSO）

1. 还原网关脚本认证块和 `.env.production` 的 `XUNTUI_AUTH_MODE=required`（备份 `.bak-20260913-*`）。
2. `docker compose --project-directory /opt/control-center/control -f /opt/control-center/control/docker-compose.yml up -d` + `docker start control-redis`。
3. 重建 backend 容器（让 AUTH_MODE 生效）。
4. 立即改 admin 密码（用户表在 pg `postgres` 库，admin 的引导密码 change-me-now 已暴露）。

## 8. 优云专用版本发布检查

生产 API venv 位于 `/opt/venvs/api`，只安装 storage 依赖和 PyYAML。Drone 显式传入 `INSTALL_LOCAL_RUNTIME=0`；不创建本地训练或 vLLM venv。开发验收可构建 `--build-arg INSTALL_LOCAL_RUNTIME=1`，运行时配置 `XUNTUI_LOCAL_RUNTIME_ENABLED=true`、`XUNTUI_LLAMA_FACTORY_COMMAND=/opt/venvs/llamafactory/bin/llamafactory-cli`、`XUNTUI_TRAINING_PYTHON=/opt/venvs/llamafactory/bin/python` 和 `XUNTUI_VLLM_PYTHON=/opt/venvs/vllm/bin/python`（CPU 验收另设 CPU 与预加载参数）。

- 发布前保存上一版本镜像 tag；不做数据库迁移、不删除历史数据。
- 发布后 `/api/health` 应返回 `storageBackend=postgres`；`/api/capabilities` 应返回 `localRuntimeEnabled=false`。
- 验证本地训练、CLI、本地推理生命周期和本地聊天返回 409，历史详情与产物仍可读取。
- 优云训练/推理在远程实例加载模型；生产评测当前关闭。后续启用时仅复用完成训练后仍保留的实例，原公共模型路径不可用时明确失败，不按名称替换其他模型。
- 默认公共模型库；准确 Repo ID 可作为远程 ModelScope/Hugging Face 兜底，生产服务器不提供 ModelScope SDK 搜索。
- 回滚时使用上一版本的代码与两个镜像 tag，通过 Gitea 新标签流水线部署或使用已保留镜像恢复；保留旧 tag，不覆盖它。

Gitea 当前默认分支与桌面工作区保持一致；变更默认分支前先核对提交关系和团队集成状态。


## 训练日志与产物同步修复验收（v3.0.148）

变更边界：Nginx/Next/FastAPI 日志流、优云回传/本地文件/共享存储、训练 stdout/指标解析。基线症状是网关握手 404（直连 101）、调用未实现的 `sync_retained_artifacts`、训练器 stdout 缓冲导致进度先于 Loss。

发布流水线在重建服务前运行 `scripts/check_release_idle.py --api http://127.0.0.1:18010/api`。发现活跃或未知任务状态、推理进程、产物上传时必须等待，不自动停止它们。脚本用于已有生产环境的更新，首次安装须先完成初始化。

安装器只管理 `cppt-platform.conf`，备份保存在 `/var/backups/cppt-platform-gateway/`；配置校验或 reload 失败会恢复旧配置并尝试重新加载。其他站点不得覆盖。回滚发布时用部署前记录的镜像 tag 和同一 `.env.production`/数据卷恢复 backend/frontend，再恢复对应网关备份并执行 `nginx -t && nginx -s reload`；不得删除 Adapter、MinIO 对象或上传元数据。

只读日志流验收（不创建训练任务）：

```bash
python scripts/check_training_websocket.py --api http://127.0.0.1:13101/api --task-id EXISTING_TASK_ID
```

不指定任务时选择现有已完成任务；无任务明确输出 SKIP。已完成任务必须收到状态、指标、terminal 和 1000 正常关闭；运行中任务必须收到心跳。人工同时核对 log 计数及页面曲线。

补同步使用现有 `POST /api/tasks/{id}/artifacts/sync`，仅针对指定已完成任务。先保存原 Adapter 校验和，成功后检查 `syncState=available`、`syncError=null`、PostgreSQL 文件记录与 MinIO 对象大小/MD5 一致，再重试确认模型 ID 未增加。实例已释放但本地文件完整时也可补传，不需要重新训练。

回归命令：`python -m unittest backend.test_artifact_sync backend.test_training_websocket backend.test_youyun_training_log_stream backend.test_platform_gateway backend.test_release_idle -v`；真实 Nginx 集成测试需要 Linux 与 nginx，使用独立临时目录/端口，不改系统运行配置。另执行完整 CPU 后端测试、前端测试、类型检查和构建。
