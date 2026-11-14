# 团队协作与发布规范

本文档适用于两人或多人共同开发本项目。目标是让每个人的半成品互不干扰，并确保只有经过检查的代码进入正式分支和生产环境。

迁移说明（2026-10-08）：Gitea 现有默认分支为 `claude/gallant-kepler-89b691`，桌面工作区继续跟踪该分支。下文 `master` 是团队约定的发布分支；发布前须先确认已合入预期代码和最新 Gitea 流水线，不要仅凭默认分支名称选择版本。

## 1. 分支职责

- `master`：正式集成分支，只保存已经验收、可以进入发布检查的代码。
- 个人功能分支：每项功能独立开发，例如 `silentfire/nlp_training`、`classmate/model-ui`。
- Git Tag：正式发布标记，例如 `v3.0.16`。Tag 不是开发分支，推送 Tag 会触发 Drone 测试、构建和部署。

不要两个人直接在 `master` 上同时开发，也不要在个人功能分支上打发布 Tag。

## 2. 开始新功能

开始前确认工作区没有未提交内容：

```powershell
git status
```

从最新正式代码创建个人功能分支：

```powershell
git switch master
git pull --ff-only origin master
git switch -c 负责人/功能名称
git push -u origin 负责人/功能名称
```

示例：

```powershell
git switch -c silentfire/nlp_training
git push -u origin silentfire/nlp_training
```

分支名称使用英文、数字、短横线或下划线，包含负责人和功能范围。不要两个人共用同一个功能分支。

## 3. 日常开发与推送

每次开始工作先确认分支：

```powershell
git branch --show-current
git status
git pull --ff-only
```

完成一个可说明的改动后提交。优先明确添加本次涉及的文件，不要只凭记忆提交：

```powershell
git add app/page.tsx app/globals.css components backend
git diff --cached --name-status
git commit -m "feat: 简短说明本次功能"
git push
```

常用提交前缀：

- `feat:` 新功能或交互改进。
- `fix:` Bug 修复。
- `docs:` 只修改文档。
- `test:` 只补充测试。
- `refactor:` 不改变功能的代码整理。

提交前必须完成一次文件完整性检查，避免漏掉并行修改或误加入运行时文件：

```powershell
git status --short
git diff --stat
git diff --name-only
git diff --cached --name-status
git diff --cached --check
git status --short --ignored
```

确认本次需求涉及的每个新增、修改、删除或移动文件都出现在 `git diff --cached --name-status` 中，再提交。提交后再次执行 `git status --short --branch`，确认工作区干净、提交位于当前功能分支并已跟踪远程分支。不要把 `.env`、密钥、数据库、模型、日志、缓存或无关文件加入提交。

如果使用 Codex 或其他并行工作区协作，所有改动最终都会汇总到共享工作区；不要只提交自己记得的文件，也不要在未检查 `git status` 的情况下直接推送。

## 4. 同步其他人的最新代码

当 `master` 已经合入其他人的修改，而你的分支还要继续开发时：

```powershell
git status
git fetch origin
git merge origin/master
```

执行前先提交自己的改动或保持工作区干净。发生冲突时不要盲目选择“全部接受当前”或“全部接受传入”；应逐处确认双方业务逻辑，解决后重新运行测试。

## 5. 合并到 master

个人分支开发完成后：

1. 推送个人分支。
2. 在 Gitea 创建从个人分支到 `master` 的合并请求。
3. 检查改动文件、冲突、测试结果和页面效果。
4. 审核通过后合并。
5. 合并后在最新 `master` 上重新完成发布前检查。

普通分支推送和合并 `master` 不会触发当前生产部署。普通 push / PR 运行 Drone 自动检查，只有 Tag 推送触发生产部署。

## 6. 测试与人工验收

代码合并前至少执行与改动范围相匹配的测试。发布前建议执行完整检查：

```powershell
python -m unittest discover -s backend -p "test_*.py"
python -m compileall -q backend scripts
npx tsc --noEmit
npm run build
$env:XUNTUI_ENV_FILE='.env.production.example'
docker compose --env-file .env.production.example -f compose.production.yml config --quiet
git diff --check
```

自动测试不能代替人工验收。前端改动需要在浏览器检查布局、文案、交互、加载状态和完整用户流程；训练、推理、PostgreSQL、MinIO 或优云改动需要按风险决定是否进行真实环境冒烟。

## 7. 发布与 Tag

只有最新 `master` 通过发布前检查后才能打 Tag：

默认 `origin` 是 `http://git.leiziapi.cn:3003/leifengyang/cppt-platform.git`；本机 `github` 仅保留历史读取并禁用推送。所有分支与 Tag 都推到 Gitea。下面的兼容脚本现在直接推送指定 Tag，Drone 从 Gitea 克隆，无需上传 bundle。

```powershell
git switch master
git pull --ff-only origin master
git tag -a v3.0.17 -m "v3.0.17: 发布说明"
bash scripts/release-bundle.sh v3.0.17
```

- 每次使用新的版本号，不重复使用已推送的 Tag。
- 推送 Tag 后，Drone 会再次执行自动测试、镜像构建、部署和容器健康检查。
- Drone 成功后仍需人工检查线上入口和本次修改涉及的核心流程。
- Drone 失败时先修复代码并合入 `master`，再使用下一个新版本号。

## 8. 共用 Gitea 账号时

共用账号可以推送不同分支，但 Gitea 的账号操作记录无法区分两个人。至少在各自电脑设置不同的 Git 提交作者：

```powershell
git config --local user.name "开发者姓名"
git config --local user.email "开发者邮箱"
```

更推荐每人使用自己的 Gitea 账号，并将同学添加为仓库协作者，以便区分权限、提交、审核和操作日志。

## 9. 与 Codex 协作时的推荐说法

开发阶段：

> 我现在在 `负责人/功能名称` 分支。先确认需求并实现，不要提交、推送、合并或打标签。

分支交付：

> 我已人工验收，请运行测试，然后提交并推送当前功能分支，不要合并和打标签。

合并阶段：

> 检查当前功能分支与远程 master 的差异，测试通过后合并到 master，先不要打标签。

发布阶段：

> 在最新 master 上完成发布前检查，全部通过后创建并推送下一个版本标签。

## 10. 禁止事项

- 不提交或输出真实 `.env`、密码、Token、SSH 私钥和云服务密钥。
- 不使用 `git reset --hard`、强制推送或删除远程分支来处理不清楚的问题。
- 不在有未提交修改时随意切换分支。
- 不把未人工验收的页面直接合入 `master` 并发布。
- 不因为测试环境需要数据而连接或修改生产数据库、训练队列和云资源。
