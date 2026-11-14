# 2026-11-03 Personal Youyun accounts (XShipAI deployment)

- System settings now save each authenticated user's Public Key, Private Key, region and zone. Keys are encrypted with a durable server key; reads return configuration status and a masked public-key suffix only.
- New training tasks and inference services bind a private credential revision before entering the queue. Text, visual and image-generation workers, comparison, recovery and cleanup resolve the persisted binding. Changing personal settings affects new resources; existing resources retain their original revision.
- Datasets, model assets and artifacts remain shared. Mutations and inference consumption of cloud resources require their creator's identity. Historical unowned resources remain readable and available as clone/model sources; background maintenance retains the legacy cleanup configuration.
- XShipAI desktop now forwards stable password/Feishu user IDs through the authenticated gateway response. The gateway overwrites external identity headers; its identity takes precedence over an older local OAuth cookie. This deployment enables required backend authentication.
- Validation: 152 frontend tests and TypeScript passed. Linux backend suite: 962 tests, one environment skip. Actual PostgreSQL verification passed in an isolated temporary schema, including encryption, account isolation and rotation/restart recovery. Independent QA found and verified fixes for cross-task cleanup executor reuse and stale-cookie identity precedence. Browser QA covered desktop/390px, save/reload/masking, invalid zone and shared datasets. No billable GPU workload was started.
- Deployment/rollback: see `docs/changes/2026-11-03-personal-youyun.md`. Keep the `/workspace/runtime/personal-youyun.key` file with the persistent volume/database backups, or provide a stable Fernet `XUNTUI_CREDENTIAL_KEY` across replicas. Never roll back to an older runtime while personal-account cloud resources are active.

# Current State (code release v3.0.148; audited 2026-09-20)

## 2026-10-08 Gitea / Drone 仓库迁移

- 开发与发布仓库为 `http://git.leiziapi.cn:3003/leifengyang/cppt-platform`；桌面工作区 `origin` 和当前分支 upstream 指向 Gitea，`github` 仅保留历史读取、禁用推送。
- 新 Drone：`http://drone.leiziapi.cn:4400/leifengyang/cppt-platform`。普通 push / PR / 手动检查运行后端测试、前端测试/类型/构建、网关测试和 Compose 校验；tag 保持原有测试、镜像构建、空闲检查、部署、健康和 WebSocket 验收。
- 两条流水线直接从 Gitea 克隆对应 SHA。`scripts/release-bundle.sh` 保留旧入口名称，现只校验 Gitea 目标并推送指定 tag，无需上传 bundle 或服务器 root SSH。
- 本次迁移只验证 CI，不创建生产发布标签，不改变运行中的平台版本或云资源。

## 推理创建浏览器验收补充：库存缓存（下一版发布准备）

- v3.0.158 已恢复容器元数据；浏览器继续验收时发现正式分支库存缓存成功路径缺少返回值及 Future 完成通知，页面误显示无规格，并发请求可能挂起。
- 将成功结果缓存、返回及共享 Future 通知移到新旧客户端共同路径；异常/取消时清理 owner，单个 waiter 取消不再影响共享请求。
- 新增真实 client identity 的库存接口、缓存命中、并发完成、旧签名兼容及取消回归。Linux 全量 911 项（910 通过、1 跳过）通过。
- 部署注意：Drone 能接收标签，但 GitHub 配置拉取返回 Bad credentials；未改共享凭据。v3.0.158 使用已合并且校验 SHA 的标签源码，在服务器完成等价测试、镜像构建、空闲检查、健康检查及训练 WebSocket 验收后部署。下一版沿用此受控流程，保留前一版本镜像。

## 推理服务创建修复（发布准备）

- 根因：优云返回 `Container="True"`，但生产镜像客户端丢弃该字段，使公共模型推理创建错误返回 422。恢复容器标记、镜像来源、运行系统及区域元数据，并支持不受默认 Zone 限制的镜像目录查询。
- 保留严格校验：虚机/未知类型镜像仍不能使用公共模型库；不通过推断镜像名称或关闭校验绕过约束。
- 创建页面增加正在提交状态、防重复提交和按钮旁的可访问反馈；未知类型显示“镜像类型待确认”，不再误报为虚机。
- 回归覆盖原始供应商数据 → 真实客户端转换 → 资源校验 → HTTP 创建接口：修复前 422、修复后 201，且服务仍为 created、不分配 GPU。
- 发布前验收：Linux 隔离容器后端全量 906 项（905 通过、1 跳过），前端 152 项、TypeScript、生产构建通过；内置浏览器确认加载禁用、失败后可重试和就近错误提示。Windows 两项平台相关断言失败已在 Linux 全量验证通过。
- 部署使用正式 master 的 Tag/Drone 流程，保留 v3.0.157 镜像回滚；发布前及流水线内检查活动任务。部署后仅创建配置验收，不启动 GPU，不修改历史训练产物。

## 2026-09-21 SSH 重连修复发布准备

- 修复远程命令发送前 SSH 恢复失败直接穿透：有限重试、退避、取消检查与连接资源回收；非幂等命令响应丢失不盲目重放。
- `NoValidConnectionsError` 按 SSH 故障分类，不再误报依赖安装失败；明确的认证 transport EOF 可重试，真正认证拒绝/主机密钥错误不重试。
- 验证：155 项目标回归通过；全量后端 903 项（902 通过、1 跳过）；生产相同 Paramiko 3.5.1 下真实 loopback TCP 拒绝→恢复测试通过，启动命令仅发送一次。
- 云端验收限制：三轮受控同规格实例中，首轮认证 EOF 促成追加修复，后两轮 GPU 探测返回 NVML Unknown Error；均已删除且供应商查询确认为空。完整训练尚未验收，不能视为已解决 GPU 驱动问题。
- 风险接受：用户在知悉上述限制后明确要求部署并自行测试。发布前/流水线内检查无活动任务；通过 Tag/Drone 发布，发布后核对健康与代码；不自动创建或重试 GPU 任务。
- 回滚：保留上一版本 v3.0.152 的镜像；按既有发布流程回退本次代码，不迁移数据库、不删除历史任务或产物。

## 2026-09-20 Validation Loss and artifact recovery (release candidate)

- validationSteps now reaches eval_steps; validation uses step evaluation/checkpoint schedules without replacing final weights. Final eval_loss summaries are ingested into Loss history.
- Artifact metadata reads final evaluation results and actual global step, and maps remote best-checkpoint paths into downloaded mirrors.
- Manifest-verified current-attempt mirrors can be reindexed without reconnecting to a released GPU; existing local-first uploads remain intact.
- Training output separates model weights from JSON metadata. Individual JSON files open as text; mobile preview uses a body portal.
- Verification: 887 backend tests passed (4 environment skips), 151 frontend tests passed, TypeScript and production build passed. No GPU run was started.
- Integration base: 32a1eae. No schema changes. Production must pass the idle guard; the release action preserves all persistent storage. Previous running image version: v3.0.147. This candidate entry does not assert deployment success.


## 2026-09-20 日志流、产物同步与实时 Loss 修复（v3.0.148）

- 生产发布基线为 `v3.0.147`；本节记录 `v3.0.148` 的代码变更，实际部署状态以 `/api/health.releaseVersion` 和 Drone 结果为准。
- 平台网关配置由仓库管理，保留 `13101 → 13100` 和缓存策略，补齐 WebSocket Upgrade 转发；安装时校验、平滑 reload，失败恢复配置。任务终态发送后使用 1000 正常关闭，前端不再对终态连接启动断连轮询。
- 补齐保留实例产物回传入口；同步优先使用完整本地 Adapter，只有缺失时回传，暂存下载并保护已有完整文件。本地完整时，实例释放后也可重试上传；回传与共享存储上传错误分阶段记录，继续复用上传器幂等机制。
- 远程训练启动命令设置 `PYTHONUNBUFFERED=1`，确保训练器及其 Python 子进程在退出前及时写出 Loss；保留已发布的指标恢复和前端详情合并逻辑。
- CPU 回归覆盖进行中的真实子进程输出、Adapter 重试/文件保护、WebSocket 终态/心跳、真实 nginx 升级转发与安装失败回滚；发布前只读检查活跃任务/推理/上传，部署后检查真实网页入口的日志流。
- 操作与回滚步骤见 `docs/DEPLOYMENT-NEW-SERVER.md`；本次不启动新训练、不创建或主动释放 GPU 实例。


## 2026-09-16 训练诊断修复（待发布）

- 优云 GPU 库存的显式刷新会绕过前端短缓存；提交前仍由后端再次校验精确 GPU/CPU/内存组合。
- 训练详情合并实时 WebSocket 数据时不再用延迟的空 Loss 快照覆盖已有曲线；训练产物中的 `trainer_state.json` 也会在回传阶段补写 Loss/评估 Loss。
- 任务流遇到一次性数据库读取失败会保持 WebSocket 并重试；优云文本任务完成或手动停止且有 Adapter 时保留实例供效果对比，视觉训练仍按原策略清理。

## 2026-09-14 GitHub 主线与生产优云专用化（工作区，未发布）

- 生产能力配置新增 `XUNTUI_LOCAL_RUNTIME_ENABLED=false` 与 `XUNTUI_EVALUATION_ENABLED=false`：仅开放优云训练和推理，评测导航及写操作暂时关闭，历史评测仍可读取。
- 优云训练的基础模型选择默认进入公共模型库；仅在明确选择虚机镜像时使用远程 ModelScope/Hugging Face Repo ID 兜底。
- 模型管理按当前子页懒加载并复用已加载结果，模型资产目录在启动和训练完成时同步，读取接口复用五分钟快照。
- `xinceshi1` 的 V100S 失败已确认不是 bf16：`auto` 实际回退到 float16，失败来自官方 vLLM 镜像中的 PyTorch/CUDA 未包含 `sm_70` 内核。V100S 现保留在库存中但禁用推理；旧请求的 `auto` 会显式解析为 `float16`。
- 本节仍未发布；线上继续运行 `v3.0.130`，需经 GitHub tag 触发 Drone 后再做生产验收。

## 2026-09-13 v3.0.130 新服务器迁移部署完成（Drone 全链路打通，Control Center 暂时摘除）

- **服务器迁移**：生产从 `185.61.210.32` 迁到新服务器 `110.42.51.234`（代码仓库同步迁到 GitHub `leifengyang/cppt-platform`，Drone server+runner 也在新服务器上）。PostgreSQL/MinIO 数据经迁移备份恢复；deploy-data（datasets/runtime/training-work 等）已就位。
- **v3.0.130 经 Drone tag 流水线部署成功并验收**：build 七步全绿（fetch-source → verify-backend → verify-frontend → verify-compose → 双镜像构建 → deploy），backend/frontend 容器 healthy，`/api/health` 返回 `releaseVersion=v3.0.130`、queue=postgres、judge/embedding 已配置。平台入口 `http://110.42.51.234:13101/`。
- **为打通流水线修复的一串 CI 问题**（新服务器国际出网被掐是根源）：
  1. Drone 默认 clone 连不上 GitHub → `.drone.yml` 改 `clone: disable: true` + 自定义 `fetch-source` 步从上传到服务器的 git bundle checkout；发版脚本 `scripts/release-bundle.sh`（打 tag → scp bundle → push tag）。
  2. pip 静默忽略明文 HTTP find-links → 镜像构建加 `PIP_TRUSTED_HOST=110.42.51.234`（MinIO wheelhouse `http://110.42.51.234:19000/wheelhouse/` 托管全部 torch/vllm +cpu 轮子）。
  3. torch 安装姿势：同版本 pip 选索引不选 find-links，且 `--no-index` 会挡掉 torch 依赖 → 只给 `--find-links`，`+cpu` 本地版本号必走 wheelhouse（内网速度），依赖走清华源。
  4. 服务器 pg：`cppt_platform.dump` 恢复了 20 张表但角色 `cppt_platform_app` 没随 dump 恢复 → 重建角色、逐对象转移表属主、对齐 MinIO 凭据。
  5. 网关注册脚本 `configure-app-gateway.sh` 的 SSO 子检查写死 80 端口 → 改 `${CONTROL_SSO_PORT:-18080}`。
- **Control Center 已暂时摘除**（暂不使用 SSO）：网关脚本去掉 auth 机制（纯代理 13101→13100，重新部署不会反弹）；`.env.production` 设 `XUNTUI_AUTH_MODE=disabled`（平台匿名可访问）；control-frontend/backend/redis 容器已停止（数据保留，恢复路径记录在迁移 CHECKLIST）。**注意：当前平台无登录门，地址不要外传。**
- master 已快进到部署提交（含新版 `.drone.yml`），发版流程见 README「Deploy with GitHub tag + Drone」和 `docs/DEPLOYMENT-NEW-SERVER.md`。
## 2026-09-07 文生图推理服务启动死区与恢复修复

- 问题：文生图推理服务点击启动后停留在 `starting` 状态，前端对活动状态隐藏全部操作按钮且无进度反馈；`POST /start` 同步等待整个优云实例开通（建实例 → SSH → 远端安装 Diffusers → 解析模型 → 上传 Adapter），普通 PyTorch 镜像（`requiresDiffusersInstall=true`）下耗时 20 分钟以上，期间重复点击启动会排队等锁挂起。
- 修复（`image_generation_service_runtime.py`）：`start`/`stop` 在锁被占用时快速失败（409 + 明确文案）；启动过程把阶段进度写入 `config.startup`（stage/progress/startedAt/elapsedSeconds），成功置 `ready/100`；`startup()` 启动恢复把后端重启遗留的 `starting`/`recovering`（无实例句柄）复位为 `failed`、无句柄 `stopping` 复位为 `stopped`、有句柄 `stopping` 保留句柄置 `failed` 待再次停止释放。
- 修复（`providers/youyun/diffusers.py`）：`provision_service` 增加可选 `report_progress` 回调，按 creating_instance/instance_running/ssh_connected/preparing_runtime/resolving_model/uploading_adapter/finalizing 上报阶段；进度上报异常不中断开通。
- 修复（`inference_service.py`）：`last_recoverable()` 与空闲回收循环跳过 `workloadType == "image_generation"` 的服务——此前后端重启时通用 vLLM 自动恢复会把文生图服务误走 vLLM 路径（本次重启实测触发一次，该任务在模型解析前失败并自行释放实例，无实例泄漏）。
- 修复（`app.py` + `instance_manager.py`）：优云孤儿实例巡检前缀新增 `cppt-img-service-`，并把带 `remoteRuntime` 句柄的文生图服务实例计为 active，避免误删运行中的服务实例；泄漏的服务实例在超过 `XUNTUI_YOUYUN_ORPHAN_GRACE_SECONDS`（默认 900 秒）后由看门狗回收。
- 前端（`image-generation-service.tsx`）：`starting` 状态在列表与详情显示「启动中 · 阶段 · 已用时 · 进度」徽章，启动面板展示实例方案（GPU/CPU/内存/地域/计费/价格）、当前镜像与推荐镜像；存在启动中服务时每 5 秒自动轮询服务列表；服务运行日志固定显示在详情「运行日志」页签顶部的新「服务日志」区块（`limit=200`，启动中随轮询自动刷新）；镜像选择提示改为明确说明未预装 Diffusers 需远端安装 20 分钟以上并建议改选预置镜像。
- 新增接口：`GET /api/image-generation/services/{id}/logs`。
- 验证：后端新增 `test_image_generation_service_runtime.py`（10 项），扩展 diffusers 进度回调、API 日志接口、`last_recoverable` 跳过文生图等回归，相关 6 个模块 156 项测试与 compileall 通过；前端 143 项测试、`tsc --noEmit` 通过；本地后端已用新代码重启，`/api/health` 200，卡住任务 `igs-adc2781106b1` 已复位为 `failed` 终态，未创建真实优云实例。
- 遗留：文生图服务自身的空闲回收（`idleTimeoutSeconds`）尚未在 image runtime 实现（通用回收已明确跳过该负载）；启动接口仍是同步等待，未改造成异步任务模式。
- 追加根因（2026-09-07 15:00 定位）：文生图服务"卡在 starting"的真实根因是 `app.py` 把需要 1 个位置参数的 `build_image_generation_provider` 直接作为 `ImageGenerationServiceRuntime` 的零参数 `provider_factory`，`start()` 调用即抛 `TypeError`，服务永远停在 `starting`（此前误判为"开通流程慢"）。已改为 lambda 适配零参数调用，并把 `provider_factory()` 移入 try 使工厂异常落到 `failed` 终态；新增回归测试。修复后真实启动 `igs-e578e7c500d6`（cuda128_torch280_py312 镜像）：76 秒完成建实例，全流程 176 秒进入 `running`（实例 `uhost-1uzuekuvbpr3`），阶段进度上报与前端展示全程可见。注意：优云镜像目录当前无预装 Diffusers 镜像（前端已如实提示）；该镜像内已可导入 torch+diffusers+peft，无需远端安装；服务运行期间实例按量计费，需手动停止释放。
- 追加（2026-09-07 15:40）：文生图服务列表页加载过慢修复——列表接口本身仅 8ms，瓶颈是 `GET /api/image-generation/youyun/gpu-specs` 每次实时调优云 API（实测 7-10 秒）且前端 `load()` 用 `Promise.all` 阻塞列表渲染。前端拆分 `loadServices`（列表先行渲染）/`loadCatalog`（模型+库存后台加载）；后端为 gpu-specs 增加进程内 TTL 缓存（`XUNTUI_IMAGE_GENERATION_SPECS_CACHE_SECONDS`，默认 30 秒，按参数组缓存，提交/预检路径仍实时校验库存与价格）。实测：gpu-specs 冷查 9.6 秒 → 缓存命中 5ms。前端 144 项测试、tsc、后端相关测试与 compileall 通过。
- 追加（2026-09-07 16:20）：文生图与视觉推理服务列表布局统一收紧——行高 82/84→54px（显式覆盖统一壳的全局 `td { min-height: 68px }`）、表格取消 `min-width: 1320/1260px` 改为跟随容器宽度（长字段省略号截断）、文生图操作列加宽到 220px 使操作按钮保持一行；均用页面级选择器作用域，不影响其他页面。
- 追加（2026-09-07 16:40）：视觉模型管理新增「优云公共模型库」一级标签页（默认落点），对齐参考控制台方向：父组件 `vision-understanding-module` 刷新时并发拉取 `GET /api/vision/youyun-catalog` 实时目录（失败降级为空）；视图提供 公共模型/可用/不可用/大小未知 四张汇总卡、名称/Repo 搜索，表格列为 模型/来源徽章/容器内路径(remotePath, mono)/大小(sizeGb)/状态(availableOnYouyun + 模板与建议显存)/操作(用于训练、详情)；「用于训练」对未登记基模预填登记弹窗、已登记则跳转资产详情；目录详情抽屉展示挂载路径（可复制）、能力与探针说明。`ModelAssetTab` 联合类型扩展 `catalog`，文生图模型页 filterOptions 收窄为 `Exclude<ModelAssetTab, "catalog">` 保持兼容。原有资产视图（全部/基模/训练模型/回收站 + 三向就绪）与静态预设抽屉保留。
- 追加（2026-09-07 16:55）：视觉模型管理两个表格统一收紧到容器宽度——公共模型库表与本地资产表（`.vision-catalog-table` / `.unified-model-page .vision-model-table`）取消 `min-width`（原 1040/1180px）改为 `width: 100%`，资产表行高 82→60px；资产表操作列由灰色图标按钮（垃圾桶/恢复/⋯）改为文字链接（重新探针/归档[带确认]/恢复/详情），公共模型库表移除「⋯」图标框；长字段（探针说明、来源子行、模型名/Repo）单行省略号截断。注意：本地 dev 模式 `.next` 编译缓存多次出现陈旧下发（重启无效），本次通过整体移走 `.next`（保留为 `.next-stale-*`）后干净重启解决；后续遇到"改了样式不生效"优先清缓存重启。前端 150 项测试、tsc 通过。
- 追加（2026-09-07 17:15）：文生图模型管理配色降噪与列距收紧（`.model-page-image-generation` 作用域）——徽章体系从七种色相收敛为三种语义色：品牌靛蓝（基模头像/汇总卡图标）、绿（优云可用）、红（不可用/删除）；训练 Adapter 头像、来源徽章（基座模型/训练 Adapter）、LoRA/QLoRA 兼容标签、Adapter 版本徽章全部改中性灰（不可用模式为描边浅灰）；表格取消 `min-width: 1040px` 收满容器使各列间距均匀分布，行高 78→60px、单元格内边距 9×12px；操作列「⋯」灰色图标框换为文字「详情」链接。前端 151 项测试、tsc 通过。
- 追加（2026-09-07 17:30）：文生图微调训练列表同向收紧（`.image-generation-training-page` 作用域）——取消 `min-width: 1320px` 收满容器、行高 84→60px（显式覆盖全局 `min-height: 68px`）、操作列 190→230px 使「确认启动/详细/停止/重试/删除」尽量同行、次级行间距 4→2px。随后发现内容自然宽度仍可撑出横向滚动，改为 `table-layout: fixed` 按百分比固定七列宽度（16/17/14/11/17/12/13%），任务名/Repo/数据集 ID/失败信息全部单行省略号截断、进度条拉满列宽、操作按钮允许整齐折行——保证任何窗口宽度下都不横向溢出。前端 153 项测试、tsc 通过。
- 追加（2026-09-07 17:45）：视觉理解微调训练列表应用同一固定列宽方案（`.vision-training-page` 作用域，取消 `min-width: 1380px`，七列 16/17/14/11/17/12/13%，行高 84→60px，长文本截断、进度条满列宽、操作整齐折行）。前端 154 项测试、tsc 通过。
- 追加（2026-09-07 17:55）：数据集列表（三域共用，`app/page.tsx`）紧凑化——首列由 4 行（名称/说明/ID+复制/来源徽章独立行）合并为 3 行（来源徽章并入 ID 行）；表格 `min-width: 980px → 0` 全流式（原已 `table-layout: fixed` 百分比列宽保留）；说明文字与名称单行省略号、单元格内边距 12/14→7px、表头 40px。前端 155 项测试、tsc 通过。
- 追加（2026-09-07 18:10）：视觉微调（及所有使用通用 `GET /api/youyun/gpu-specs` 的表单）优云数据加载提速——实测冷查 15.6 秒。两项修复：① 社区镜像 4 路查询改为与机型/镜像/库存链路并发启动（它们之间无依赖）；② 增加进程内 TTL 缓存 `XUNTUI_YOUYUN_GPU_SPECS_CACHE_SECONDS`（默认 30 秒，按 7 个查询参数为键；价格 `POST /api/youyun/price` 与提交/预检路径仍实时询价校验）。实测冷查 15.6→11.2 秒（剩余为可用区→机型→库存的必要串行上游耗时），缓存命中 6ms；返回数据量不变（5 可用区/261 社区镜像/11 规格）。视觉微调任务列表本身 6ms，前端本就与优云面板分离加载。后端 compileall、youyun/app 73 项测试通过。
- 追加（2026-09-07 18:40）：优云地域选择改为平铺可用区（用户选定方案 B）。原"地域按钮 + 仅多可用区时显示下拉"的两级展示让 5 个可用区（华北一C/华北二C/上海二A/华北二A/上海二B，来自 `DescribeCompShareSupportZone` 的 describe 字段）被误读为"只有 3 个区"。新增共享组件 `components/youyun-zone-picker.tsx` 与 `youyunZoneLabel`（describe 优先、zone ID 兜底）。按用户反馈二次调整：选择器**不做实例模式过滤**，始终平铺全部（非冷却）可用区并带 Pod/UHost 标记；显式 Pod/UHost 分段的表单（视觉微调、文生图微调）点选时自动 `setSelectedYouyunInstanceMode(zone.is_pod ? "pod" : "uhost")`；自动模式表单（文生图推理、视觉推理、视觉理解部署）依赖后端 auto 模式按可用区 is_pod 推断实例模式。五个消费方全部替换；冷却中的可用区仍被过滤；空态如实提示。顺带移除视觉理解里硬编码的 `["cn-bj2","cn-wlcb","cn-sh2"]` 兜底。注意：本轮 Python 补丁曾把组件文件行尾翻成 CRLF 导致测试失配，已统一恢复 LF（后续脚本写文件用 write_bytes）。前端 156 项测试、tsc 通过；bundle 已验证含平铺渲染/模式标记/自动切换。
- 追加（2026-09-07 19:05）：可用区按钮排版优化——由 `flex: 1 1 30%` 换行（3+2 两行右侧留白、上下不对齐）改为 `repeat(auto-fill, minmax(148px, 1fr))` 等宽网格，按钮 36px 高、两端对齐（左侧区名 + 右侧 Pod/UHost 圆角小徽章），选中态品牌蓝描边。前端 157 项测试、tsc 通过。
- 追加（2026-09-07 19:30）：按 canvas 设计方案实施视觉微调页「优云 GPU 配置」区块改版（试点，`youyun-specs-panel-v2`）——① 标签上置：实例类型/计费类型（并排 field-row）/GPU 型号/镜像/单卡规格全部改为 `youyun-field`（label 在控件正上方），消除标签与控件 300px+ 断联与 `fixed-option-row youyun-instance-fixed` 三列卡片嵌套；② 约束置顶：新增 `youyun-constraint-callout`（红边 Callout「当前仅支持单卡任务」），替代原灰字 youyun-selection-summary；③ 扁平分组：GPU/镜像/规格三组之间用 `youyun-divider` 细分隔线分组，去掉多余嵌套层；④ 可用区悬停 title 增加 zone code。文生图两个表单的同类结构未动（试点验证后再推广）。设计原型见 canvas `youyun-gpu-config-ui-plan`。前端 158 项测试、tsc 通过；bundle/CSS 已验证下发（注意：编译器会把「·」转成 `\xb7` 转义，源码断言测试不要按编译产物匹配中文间隔号）。
- 追加（2026-09-07 19:45）：V2 布局推广到文生图微调创建页——同一套 `youyun-specs-panel-v2` 结构（标签上置/约束 Callout[文生图文案：单卡 + Diffusers 镜像兜底说明]/分隔线分组/GPU 与规格字段标签），移除该页的 GPU 型号与单卡规格两个灰字 summary 行；社区镜像探针列表保留原样式。剩余 `youyun-instance-fixed` 仅存于文生图推理服务表单（后续按需推广）。前端 159 项测试、tsc 通过；bundle 已验证（约束 Callout ×2、panel-v2 生效）。
- 追加（2026-09-07 20:00）：视觉微调创建页改为固定窗口 + 表单体内部滚动（`.vision-create-fixed`）——整页 flex 布局锁定一屏，标题行与提交按钮（取消/前置检查/确认提交）常驻可见，五个配置区块 + 前置检查结果在 `vision-create-scroll` 容器内滚轮上下翻（细滚动条样式），右侧"已填写内容"导航列独立滚动且 jumpTo 的 scrollIntoView 在内部容器中仍生效；≤1200px 窄屏自动回退为原整页滚动布局。首轮实现有两个 bug（用户反馈"还是很长"）：① `tuning-create-layout` 是 grid 且 `align-items:start`，行高随内容撑开导致滚动约束失效——改为 `grid-template-rows: minmax(0,1fr)` + `align-items:stretch`；② 视口偏移应为 74px（内容内边距 30+44，导航在侧栏不占纵向）而非 116px，并补 `100vh` 回退行（老浏览器不支持 dvh 时整条规则失效）。前端 160 项测试、tsc 通过；修正后 CSS 已验证下发。前端 149 项测试、tsc 通过。
- 追加（2026-09-07 21:30）：固定窗口第三轮修复（用户反馈"还是跨出去了"，改用浏览器实测定位而非目测截图）——两处真正的根因：① 自定义开关模式 `.toggle-row input { position: absolute; opacity: 0 }` 但 `.toggle-row` 本身无定位，隐藏 checkbox 的包含块一路解析到 `position: relative` 的 `main`，其静态位置被甩到 2620px 深处；绝对定位元素不受中间层 `overflow: hidden` 裁剪（裁剪只对包含块在其内部的元素生效），直接把 main 可滚动区撑到 2620px（720px 视口），整页因此仍可滚动——这就是"跨出去"的来源，NLP 等整页滚动页面从未暴露此隐患。修复：`.toggle-row { position: relative }`（全局基础规则，视觉零变化，所有使用开关的页面一并根治）。② 右侧"已填写内容"面板基础规则 `.training-summary { height: calc(100vh - 56px) }` 的显式高度盖过 grid 行拉伸（行高 486px 时它仍 664px），底部被裁。修复：`.vision-create-fixed .training-summary` 补 `position: static; top: auto; height: auto`，右栏吃满行高并内部滚动。验证：In-app 浏览器实测 1280×720 与 1600×900——main scrollHeight=视口高（整页不可滚）、逃逸绝对定位元素 0 个、右栏高度=grid 行高（486/666px）且内部滚动、底栏完整可见、表单容器内部滚轮；开关交互回归（label 点击切换、选中态样式不变）；前端 160 项测试通过。诊断路径备注：scrollHeight 与包含块测量 + 逐块 display:none 二分，比截图目测可靠。
- 追加（2026-09-07 21:50）：训练详情页日志窗口加高度上限——三个训练模块（NLP/视觉/文生图）详情页共用 `.logs-card pre`，原规则只有 `min-height: 230px` 没有上限，日志行多高页面就多长（实测任务 vt-467d800b8286 有 747 行日志、内容高 17126px）。修复：基础规则补 `max-height: min(60vh, 560px)` + 细滚动条，日志变为固定高度终端式窗口内部滚动；`logRef` 自动滚动直接操作 pre 的 scrollTop，与滚动容器语义天然兼容不受影响。实测 1600×900 视口：日志窗锁定 540px、内部滚动正常；推理服务日志面板与文生图任务日志（540px/260px 上限）本就有界，无同类问题。优云实例核查（只读 DescribeInstance）：远端实例总数 0，无未释放实例，igs-e578e7c500d6 已停止释放。前端 160 项测试通过。
- 追加（2026-09-07 22:05）：推理服务日志窗口同样加上限——NLP 与视觉理解推理服务详情页共用的 `.inference-log-viewer`（视觉服务日志页签，`GET /inference/services/{id}/logs?limit=200`）原规则只有 `min-height: 390px` 无上限，200 行日志会撑出约 4200px。修复：补 `max-height: min(60vh, 560px)` + 细滚动条（与 `.logs-card pre` 同一套参数）。实测视觉推理服务日志页签：日志窗锁定 540px、内容 4212px 内部滚动正常；文生图推理服务的"运行日志"页签是任务历史列表（有界），无需处理。前端 160 项测试通过。
- 追加（2026-09-07 22:20）：文生图推理服务日志页签补终端式窗口样式——`.image-generation-service-log-lines` 与 `.image-generation-history-list` 此前完全没有 CSS 规则：服务日志与每个生成任务内嵌的最多 80 行日志裸排无上限也无深色终端样式（实测 5 个任务各约 1800px 内容，整页可被撑到约 1.1 万像素）。新增规则：服务日志块 `max-height: min(60vh, 560px)`、任务内嵌日志块收窄到 260px（对齐 `.image-generation-task-logs` 先例），统一深色背景、Consolas 11px、pre-wrap 换行、细滚动条，历史列表加 grid 间距。实测：服务日志 286px 自然高度、5 个任务日志窗均锁 260px 内部滚动。前端 160 项测试通过。
- 追加（2026-09-07 22:40）：优云配置 V2 布局推广到文生图推理服务创建表单（最后一个使用旧结构的表单，`youyun-instance-fixed` 从此清零）——"优云运行环境"区块重构为 `youyun-specs-panel-v2`：① 约束置顶 Callout（单卡运行 + 保存不创建实例 + 优先预置 Diffusers 镜像）；② GPU 型号由 select 下拉改为 11 张机型卡片（显存标注 + 可售/售罄徽章，machineTypes 为空时回退单卡显示）；③ 实例类型与可用区合并为一个标签上置字段（自动模式说明 + 可用区计数 + 冷却过滤数 + 推荐镜像名收进帮助行，替代原 `youyun-resource-guidance` 信息块），YouyunZonePicker 平铺 5 区；④ 镜像字段标签上置（youyun-image-field），推荐标记与 Diffusers 安装兜底提示保留；⑤ 规格列表补 tag-ready/tag-off 徽章；⑥ 底部价格改为 youyun-price-summary 摘要卡（实测 ¥2.05/小时实时拉取）。交互语义不变：切机型重置规格并刷新库存、选区刷新资源、保存不建实例。tsc、前端 160 项测试通过（期间一次测试失败是 YouyunZonePicker 多行格式破坏了源码断言的正则，改回项目统一的单行写法后通过）；浏览器实测 V2 面板完整渲染、无横向溢出。
- 追加（2026-09-07 23:10）：文生图推理服务"默认生成参数"界面按 canvas 方案重构（设计稿见 canvas `image-gen-params-ui-plan`，含诊断/规则/交互原型/映射/清单）——数据契约不变（字段、默认值、提交结构均保持）：① 宽高合并为「输出尺寸」字段：4 枚 SDXL 原生比例预设芯片（1:1 1024×1024 / 3:4 896×1152 / 4:3 1152×896 / 16:9 1344×768，按当前宽高自动点亮），宽高数字框保留手动微调，实时显示总像素（>125 万黄字提示超预算）；② 采样步数/CFG/张数 label 行内挂推荐区间徽章（推荐 20–50 / 4–9 / 1–4），越界黄字轻提示不拦截（`ParameterInput` 新增 hintRange/softMin/softMax 可选属性）；③ 随机种子特殊化：-1 时徽章"-1 = 每次随机"+帮助文案，固定正数时显示"回到随机"按钮与复现说明；④ 采样器由 select 下拉改为两枚选择芯片（Euler 默认·速度快 / DPM++ 2M 细节更稳）；⑤ 运行参数网格 2→4 列与生成参数统一（≤1100px 回退 2 列），尺寸/采样器字段跨 2 列。新增 CSS 全部限定 `.image-generation-service-create-page` 作用域。浏览器实测：预设点击联动宽高与提示（16:9 → 1344×768·103 万像素）、芯片切换、种子固定/回到随机均正常，无横向溢出；tsc 与前端 160 项测试通过。
- 追加（2026-09-07 23:40）：视觉理解推理服务详情页按 canvas 方案优化（设计稿见 canvas `vision-inference-detail-ui-plan`）——核心问题是「服务运行摘要」契约面板 17 个字段整版铺在头部与页签之间、信息与概览页签三重复述。改动（共享组件 InferenceServiceDetailSummary 本身不动，NLP/文生图详情页不受影响，仅视觉模块内包裹）：① 契约面板包进 `details.inference-contract-fold` 折叠容器（默认收起，副标题注明"审计视图"），需要排查模型血缘/清理状态/失败编码时展开；② 头部正下方新增 meta 芯片行：服务 ID（点击复制）/ 提供方（优云远程 vLLM·本地 vLLM）/ 端点（点击复制）/ 更新时间，替代契约面板的常驻职责；③ 概览"运行状态"卡的状态徽章+提示语提升为浅灰底主行（视觉作用域）。实测：折叠收起时 17 字段不可见、详情页滚动高度 2204→1425（少滚约 780px），展开/收起与 ID 复制提示均正常；tsc 与前端 160 项测试通过。
- 追加（2026-09-07 23:55）：文生图推理服务详情页套用同一套已验证模式（设计稿见 canvas `image-gen-service-detail-ui-plan`，复用契约折叠与 meta 芯片全局样式，零新增组件）——① 17 字段契约面板包 `details.inference-contract-fold` 折叠（默认收起）；② 头部正下方新增 meta 芯片行：运行状态（中文标签：待启动/启动中/运行中/停止中/已停止/启动失败/恢复中）/ 服务 ID（点击复制）/ 镜像 imageId / 实例方案（GPU×卡数，配置了才显示）/ 更新时间；③ 启动中的实例方案/镜像 notice 保留不动（启动慢是关键决策信息）。`.inference-meta-chips` 容器样式提升为全局基类（原视觉作用域规则同值保留）。实测：折叠收起 17 字段不可见、详情页 2255→1476（省约 780px），运行状态"已停止"、镜像、4090×1 实例方案、ID 复制提示均正常；tsc 与前端 160 项测试通过。
- 追加（2026-09-08 00:05）：文生图推理服务详情页"服务概览"页签底部删除「最近生成」任务摘要区（与「运行日志」页签的任务历史/日志和「图片生成」页签完全重复，属三重复述的第三处）；概览页签现在只有 服务配置 + 启动前检查。运行日志页签的 5 条任务历史与 6 个日志窗实测不受影响（history 状态与 .image-generation-history-list 样式仍由日志页签使用）。tsc 与前端 160 项测试通过。
- 追加（2026-09-08 00:20）：视觉理解推理服务列表页删除说明框并收紧排版——① 整块删除 `.inference-scope-note` 说明框（"采用标准生命周期/创建配置不会立即占用 GPU/支持本地 vLLM 与优云远程 GPU"三段文字与 PageHeader 描述及工具行完全重复）；② 删除表格上方重复标题行 `.vision-service-toolbar`（第三次出现"视觉推理服务"标题），服务计数改为筛选行首的 `.filter-count` 芯片（实测"3 个服务"）；③ 连接状态行上移紧跟 PageHeader（margin 收紧 12px→0/14px）。删除后列表页整页一屏放下（main scrollHeight=900=视口高，900×1600 实测），汇总指标/筛选/表格不受影响。tsc 与前端 160 项测试通过。
- 追加（2026-09-08 00:35）：视觉理解推理服务列表页删除剩余三行解释性文案（用户反馈"这些解释没有必要存在"）——① 模块页头副标题"按标准推理服务生命周期部署…"（vision-understanding-module pageMeta.services.subtitle 置空）；② PageHeader 描述"管理视觉基模与 Adapter 的本地、优云推理服务…"（description 置空）；③ 连接说明行"已连接视觉推理服务后端，服务状态来自持久化存储"（renderConnection 函数与调用整体移除；后端不可用场景由 externalError 流入的 inline warning notice 承接，错误可见性不受影响）。页面现在为 标题+按钮 → 汇总指标 → 筛选行 → 表格 的纯功能结构，整页仍一屏放下。tsc 与前端 160 项测试通过（期间一处条件行重复的语法错误已修复）。
- 追加（2026-09-08 00:50）：用户反馈解释文案"都还在"——排查确认源码与 .next/static/chunks 均零残留，真凶是 `.next/static/webpack/app/page.*.hot-update.js` HMR 残留补丁仍含旧字符串（本项目第 4 次中招）。已按根治流程处理：终止 dev server（PID 20312）→ `.next` 整体移走为 `.next-stale-20260905-221608` → 干净重启（Ready 2.7s）→ 预热编译后全 `.next` 扫描零残留 → 浏览器全新加载验证：三行解释全部消失、计数芯片/表格正常、整页一屏（900=视口）。用户侧需 Ctrl+F5 强刷。
- 追加（2026-09-08 01:10）：视觉推理服务创建页与详情页的解释性段落全面清除（用户再次反馈截图里"还有"，分析工具无法解析其截图 CDN 链接，改为枚举整条路径的 DOM 文字后全量清理）——创建页删除 6 处：副标题"服务创建后默认不启动…"、选择基模/优云 GPU 资源/基础运行参数三个区块描述、右侧"创建前确认"描述"配置保存后可从服务列表手动启动"、整块删除"部署前置条件"面板（内容与执行环境选择控件的提示小字重复）；详情页概览删除 4 处区块描述（运行状态/视觉模型/运行配置/运行指标标题下的灰字）。保留控件内的功能性提示（"按量计费，启动时创建一次性实例"、"需要本地模型目录和视觉探针通过"等）。实测创建页 6 段、详情页 4 段全部不可见，功能性提示保留；tsc 与前端 160 项测试通过，模块内 9 个目标字符串源码零残留。
- 追加（2026-09-08 01:30）：用户明确指出目标是"标题下面的描述字段"——对视觉推理服务与文生图推理服务两个模块做全量清除：正则枚举全部 `<h2>标题</h2><p>描述</p>` 对共 17 处（视觉：图片与问题/模型回答/对外图生文 API/接入状态/运行日志 5 处；文生图：服务配置/启动前检查×2/对外文生图 API/接入状态/服务日志/运行日志/服务基本信息/模型与 Adapter/优云运行环境/默认生成参数/运行参数 12 处），脚本精确删除（write_bytes 保 LF，每处计数校验 n=1）。归档/删除确认弹窗的后果警告文字保留（破坏性操作说明，非页面装饰描述）。一个源码断言测试因引用被删的"服务日志"描述更新为断言日志窗类名。浏览器实测视觉详情全部页签与文生图详情/日志页签描述字段全部消失；tsc 与前端 160 项测试通过。
- 追加（2026-09-08 08:30）：为次日演示生成《视觉理解与文生图-微调推理全流程演示资料.md》，保存至用户指定目录 `C:\Users\ss\Desktop\演示资料文件夹\`。内容：平台总览 + 四条流程（视觉微调/视觉推理/文生图微调/文生图推理）各含原理讲人话版、超参数速查表（全部默认值从代码 useState 逐一核对：视觉微调 lr 2e-5/rank 64/alpha 128/累积 8 等；视觉推理 maxModelLength 4096/temp 0.2/gpuMemUtil 0.75 等；文生图微调 512px/100 步/lr 1e-4/rank 4/seed 42 等；文生图推理 1024²/steps 30/cfg 7/空闲释放 900s 等）、按页面顺序的具体操作流程、25 分钟演示动线、演示前准备清单（含文生图服务无自动空闲回收需手动停止、未预装 Diffusers 镜像冷启动 20+ 分钟、226645 供应商错误处置）与常见问题速答。纯文档工作，未启动任何 GPU/实例。
- 追加（2026-09-08 21:30）：与用户共同确定大纲（混合听众/30+ 分钟/现场实操+截图兜底/极简商务风）后生成演示 PPT《训推平台演示-视觉理解与文生图全流程.pptx》（29 页：23 正片 + 6 附录），保存至 `C:\Users\ss\Desktop\演示资料文件夹\`。深海军蓝底白字、微软雅黑、原生折线图（Loss 示意）与图形流程图；含数据集详细预览页（用户追加项）、种子复现杀手锏页、两页"粘贴截图"兜底页与附录超参全表/实例对照/FAQ/检查清单/术语表；关键页附演讲者备注。QA：python-pptx 打开校验 29 页形状完整；PowerPoint COM 成功打开并导出全部 29 页 PNG（文件有效性最强信号）；溢出启发式检出 3 处真实风险（第 5 页标题折行/第 7 页说明行/第 11 页芯片标签）已修复重建；像素级检查 29 页均非空白且无内容贴画布边缘。本环境图像读取通道不可用（Read 仅返回 CDN 上传），judge 视觉验收无法执行，已用上述程序化四重检查兜底；提示用户粘贴截图时顺手翻阅一遍。
- 追加（2026-09-08 21:45）：按用户要求重制浅色版《训推平台演示-视觉理解与文生图全流程-浅色版.pptx》（用户口述"安装 ppt-master skills"——本环境无该技能，继续使用 document-skills:pptx 完成并已向用户说明）。改动：① 全套浅色主题（白底、浅蓝灰卡片、深藏青正文、点缀色改用平台自身的选中蓝 #1B58F1，表格与图表配色同步适配浅底）；② 6 张平台真实界面截图（1600×900 浏览器实拍：数据集预览/视觉微调创建 V2/训练详情 Loss+日志/图生文调试/文生图微调创建 V2/文生图推理创建参数与种子），嵌入第 7 页（数据集预览页换实拍大图）与第 20/21 页（原"粘贴截图"虚线占位页改为"实拍界面 A/B"，不再需要用户手工粘贴）；③ 第 19 页演示动线兜底文案同步更新。因旧深色版被用户 PowerPoint 占用（EBUSY），浅色版以新文件名并存。QA：python-pptx 校验 29 页/6 张内嵌图/0 处 CJK 溢出嫌疑；PowerPoint COM 导出 29 页成功；像素级无空白无贴边。
- 追加（2026-09-08 22:10）：应用户提供的开源地址安装 PPT Master 技能——① 克隆 github.com/hugohe3/ppt-master 至 `C:\Users\ss\ppt-master`（GitHub 直连成功）；② 安全审查：MIT 协议，依赖全部为主流 PyPI 包（python-pptx/PyMuPDF/Pillow/flask/edge-tts 等），attribution_guard.py 为纯标准库本地哈希完整性门禁，网络调用仅存在于图片生成/网页抓取等预期工具；③ 依赖安装：Anaconda 全局目录无写权限（WinError 5），改用 `pip install --user`；skia-pathops 首次残缺安装后 `--force-reinstall --user` 修复（注意 pip 包名 skia-pathops 的导入名是 `pathops`），16 个依赖模块全部可导入；④ 运行技能强制完整性门禁 attribution_guard.py 通过（exit 0）；⑤ 注册技能副本至 `C:\Users\ss\.agents\skills\ppt-master`（103MB 含模板资产，供后续 Agent 会话调用；本会话技能列表启动时固定，新技能下会话生效，当前可按其 SKILL.md/workflows 手动执行）。与本项目仓库无关，未触碰 cppt-platform 代码。
- 追加（2026-09-08 22:30）：用 PPT Master 完整 Default 工作流重构演示 PPT——项目 `projects/demo-day-deck_20260907`（源：演示资料 MD + 浅色版 PPTX 结构参考 + 6 张实拍截图入库分析）。流程全记录：attribution_guard 通过 → 路由 Generate·Default → Stage-1 沟通契约 UI 确认（用户原样确认，自由设计）→ Stage-2 用户在页面改选 dark-tech 风格并加 AI 图源 → 因无图片后端，AI 行经用户确认取消（封面回退原生构图，规格/锁同步修复重验）→ design_spec（24 页名单）+ spec_lock 通过 validate（期间修两类格式：锁 images 行需 key 前缀；补 section_numeral 复现角色 160px）→ 图标池 tabler-outline 12 枚同步 → 校准 6 角色 → 实时预览 6060 → 逐页手写 SVG：P01-P05 早期门禁（修复 bounds 底界=末行基线+0.35×字号方法偏差）→ P19 原生表格（data-pptx-replace-with=table + semantic-table.v2 JSON + 可见回退三位一体并 stamp 基线）→ 最终门禁 3 轮集中修复（21px 归入 lead 24、P18 序号 40→28、P22 大字 44→48、marker 组补 bounds）0 错误 → 承载体复核（方向/层级语义由细线电路语言承载，预设族无岗位已记录理由）→ 24 页演讲者备注（notes/total.md 口语化）→ 导出原生 PPTX：24 页 + 24 备注 + 6 截图内嵌，PowerPoint 本体打开验证通过，postflight passed-with-warnings（56 条建议性警告非阻塞）。成品交付 `C:\Users\ss\Desktop\演示资料文件夹\训推平台演示-PPTMaster重构版.pptx`（与深色版/浅色版并存）。本环境图像查看通道不可用，建议用户翻阅一遍。
- 追加（2026-09-08 23:50）：视觉推理服务 API 实测与 Apifox 放行——`POST /api/public/vision/services/{id}/chat` 五用例全过（无 Key 401 / Key 签发一次性明文 / 错 Key 401 / 对 Key 到达运行时 409 服务未运行 / Key 状态查询）；后端重启携带 `XUNTUI_CORS_ORIGINS` 增补 `https://app.apifox.com`（预检 200），交付 Apifox 网页版测试步骤与请求模板；Key 已签发给 vsvc-f34f3891a358。
- 追加（2026-09-09 00:10）：视觉服务 vsvc-f34f3891a358"启动失败"根因分析与修复——服务曾正常启动（250 秒过健康检查、服务 2 次真实图生文请求），18:18:49 一条 HTTP 停止命令进入（用户否认点击；空闲回收/关闭钩子/启动失败三条自动路径逐一日志口径排除；本地管理 API 无来源审计，发起者不可考，不能排除本人当晚同窗口的浏览器自动化），停止释放本身干净；真正 bug 是停止期间 `_watch_remote` 健康监控未先取消，对拆除中实例连续探测 3 次失败，在已正常停止后把状态改写为 failed + 误导报错。修复三处（backend/inference_service.py）：① stop() 入口先取消并等待 `_remote_watcher_task`（新增跟踪引用）；② 监控器 3 次失败判定前检查状态，stopping/stopped 时记日志跳过不改写；③ stop 增加 source 参数（api/idle_reclaim/start_failure/restart）写入 inference_command_usage 审计，start 路由层同步审计。回归测试 3 项新增（RemoteStopWatcherGuardTests：取消监控落 stopped+审计 / stopping 态跳过判定不改写 / 空闲回收带 source 断言更新），test_inference_service 39 项、test_app_cli+test_inference_context 48 项全过，compileall 通过；后端已带 Apifox CORS 重启部署（200/预检 200）。回滚：revert 该文件与测试即可，无数据迁移。

## 2026-09-05 优云实例创建链路复核

- 远程视觉训练执行器现在消费任务快照中的 `youyunResource.region`、`zone`、`chargeType` 和 `imageId`；创建与后端重启清理不再回退到进程默认地域/可用区。实例句柄在创建返回后立即记录上述脱敏资源信息。
- `CreateCompShareInstance` 请求补齐官方创建契约中的 `MachineType=G` 和 `MinimalCpuPlatform=Auto`，并保留有限次数、幂等键和创建后按名称接管逻辑；`226645` 现在映射为 `youyun_create_service_unavailable`，不会被误判为数据集/模型错误。
- 2026-09-05 17:29、17:44 使用华北二 A、4090、官方 CUDA/Torch 镜像分别进行受保护真实创建探针；按量与抢占式均返回 `RetCode=226645 Service unavailable`，没有实例 ID，随后实例列表核对为 0。该结果证明当前阻塞仍在优云 `CreateCompShareInstance` 服务侧，不能宣称真实实例已跑通。
- 同期只读检查：`cn-wlcb/cn-wlcb-01` 的 4090 单卡库存与价格接口正常；上海二无单卡库存；优云配置、签名、地域/可用区查询和价格查询正常。后端本地健康检查和 246 个优云/视觉相关测试通过。
- 当前验收边界：本地提交前预检可用，真实实例创建仍需优云侧恢复 `226645` 或账号/区域创建权限确认；在此之前平台应显示供应商不可用并停止重复创建，不能以“库存可用”替代“实例创建成功”。
- 2026-09-05 资源状态兜底已修复：`CreateCompShareInstance` 未返回实例 ID 时，远程训练失败路径明确写入 `cleanupState=released`、`resourceState=released` 和 `cleanupCheckedAt`，并记录“未创建实例，无需释放”；历史 `instance_provision` 失败且无实例标记的任务在运行时读取时也会归一到该终态。已有实例句柄仍必须走真实删除与核验。
- 本次回归验证：优云远程训练、视觉运行时和视觉 API 共 185 项通过；未创建真实优云实例。
- 2026-09-05 最新视觉任务 `vt-1f45330b834f` 已完成真实训练：Qwen2.5-VL-3B、LoRA、28 条训练样本、4 条验证样本、8 个优化步，最终 `train_loss=2.8414`、`eval_loss=2.4032`，Adapter/metrics/manifest 均已回传。原先的清理失败是优云返回“实例 <ID> 不存在”被误判；现已按幂等语义视为已释放，任务状态已纠正为 `completed`、`resourceState=released`、`cleanupState=released`，当前步骤同步为“手动释放：优云实例已释放”。

## 2026-09-04 模型管理契约与页面重构

- 新增 `lib/model-asset-contracts.ts`，统一 NLP、视觉理解和文生图的模型资产类型、来源、归档状态、lineage、共享存储和本地/优云/推理三类 readiness；旧后端 payload 通过前端 adapter 转换，未改历史数据库结构。
- 新增 `lib/model-asset-api.ts`，统一模型资产请求、错误解析、凭据携带和无缓存读取；NLP 模型管理已去除静态示例数据作为初始状态，真实接口失败、加载中和空目录分别展示。
- 新增 `components/model-asset-table.tsx`。NLP “全部模型”使用统一资产表，基模/训练 Adapter 的详情、改名、归档、扫描、共享存储和 Adapter 选择操作仍由 `components/model-management-module.tsx` 装配；未接入的预留详情抽屉已在 2026-09-04 清理。
- 视觉理解和文生图模型管理已接入统一契约计算统计和状态；视觉专属探针、优云目录和文生图 Diffusers/LoRA/QLoRA 规则仍由各自模块维护。
- 按参考控制台布局移除三个模型管理页统计区下方的蓝色 lineage 提示条，目录说明保留在目录抽屉和页面副标题中，避免额外占用首屏空间。
- 验收：前端 113 项测试、后端 612 项测试、TypeScript、Next.js production build、`git diff --check` 通过；本地 `/api/health` 和 `/api/model-assets?includeArchived=true` 返回 200，当前本地模型目录为空，因此页面显示“暂无模型资产”，不伪造示例模型。

## 2026-09-02 优云视觉基模探针验收

- 已接入并验证小型视觉基模目录：`Qwen2-VL-2B-Instruct`、`Qwen2.5-VL-3B-Instruct`、`Qwen3-VL-2B/4B`；默认首验为 `Qwen2.5-VL-3B + QLoRA`。
- 真实优云只读检查通过：`cn-wlcb / cn-wlcb-01`、`4090`、`1卡/16核/64GB` 有库存；`cuda132_torch2130_py312` 可用，按量价格约 `2.05 CNY/小时`。
- 真实探针创建过两次临时实例，均在失败后自动删除并核验实例列表为空；失败原因是实例进入 `Running` 后 SSH 转发端口 `23` 未返回有效 `SSH-` banner，未进入模型依赖或 Processor 检查，因此**不能宣称视觉基模兼容性通过**。
- 探针兜底已加固：兼容真实实例字段 `uhost_id`、直接删除并核验释放、刷新 SSH 转发信息、TCP banner 探测与重试；默认仍禁止未通过探针的远程视觉训练。

## 2026-08-25 当前交接基线（v3.0.16）

这是当前优先采用的项目事实，覆盖本文后面的历史交接记录：

- 远程正式分支：`master`，当前发布提交 `0ce2e9b feat: 优化微调加载与推理存储交互`。
- 当前发布标签：`v3.0.16`；推送唯一新 Tag 触发 Drone 自动测试、构建和部署。
- 团队开发采用个人功能分支，不直接在 `master` 上同时施工。分支创建、同步、提交、合并、测试和发布流程见 [CONTRIBUTING.md](CONTRIBUTING.md)。
- `v3.0.15` 已整合老师需求相关流程，包括系统概览、数据集人工修复、训练参数审计、优云训练实例保留与释放、动态推理上下文及优云推理预留。
- `v3.0.16` 优化微调页面加载：列表初始只读取任务，模型、数据集、优云库存和价格按使用场景加载；仅存在进行中任务时轮询。
- 任务名称和模型名称支持在输入为空时按 `Tab` 应用动态建议，模型名称附加本地日期 `YYYYMMDD`。
- 配置 `POSTGRES_DSN` 且未显式选择其他后端时优先使用 PostgreSQL；SQLite 保留为本地兜底。推理页面通过 `/api/health` 显示真实存储后端。
- 数据集、微调和推理相关页面统一使用公共返回按钮组件。
- `v3.0.16` 发布前通过后端 `128 tests`、Python compileall、TypeScript、Next.js production build、Production Compose 示例配置和 `git diff --check`。
- 不要读取、提交或输出真实 `.env`、数据库密码、MinIO 密钥、优云密钥或 SSH 私钥。

## 2026-08-29 文生图阶段一与阶段二

- 已完成本地文生图数据集校验：JPG/JPEG/PNG/WebP、20MB 单图上限、200 张批量上限、Caption 规范化和 `metadata.jsonl` 生成。
- 已完成基座模型与训练完成 Best/Final Adapter 目录，服务端验证 Repo ID 和 lineage；LoRA 默认可用，QLoRA 仅在兼容性探针标记开启时开放。
- 已完成优云服务配置页面和接口：读取实时机型、单卡库存、Diffusers 镜像与按量价格，保存时由后端再次校验资源快照；保存配置不创建实例。
- 已完成阶段二 Diffusers 异步任务代码：创建与费用确认分离、持久化队列、共享 GPU 锁、一次性优云实例、SSH、镜像/共享模型优先、ModelScope/Hugging Face 下载兜底、Adapter 上传、进度/日志、PNG 结果下载、停止/重试和清理失败记录。清理失败会持久化远端实例句柄，禁止直接重跑，需先重试清理；服务端拒绝不安全 Repo ID，默认只接受 Diffusers 优云镜像，普通 PyTorch 镜像仅在显式开启远端安装兜底时可用。
- 优云资源健康保护已接入视觉理解和文生图训练：`8433`/`226645` 创建接口错误按实例模式、地域、可用区、镜像、GPU 和计费类型熔断五分钟；自动模式跳过冷却 Pod 并可切换健康 UHost，显式选择不静默改区。运行时 Pod 兜底前会先确认已捕获实例句柄释放，避免重复计费。
- 视觉理解和文生图优云推理服务复用同一健康筛选与兜底：创建时优先健康 Pod，服务启动遇到可重试创建服务错误时先释放已捕获 Pod，再用新幂等键切换同地域 UHost，并持久化实际资源模式、尝试顺序和兜底原因。
- 本地自动化测试继续使用 fake 优云 Manager/SSH，不启动本机 Diffusers。2026-09-01 已使用 `cuda132_torch2130_py312` 完成一次受保护的真实优云 SDXL LoRA 文生图功能验收：实例启动、SSH、Diffusers 依赖、基座模型、Adapter、512×512 两步生成、结果回传和实例释放均通过，最终实例列表为 0。两步图片仅验证执行链路，不代表生产图像质量验收。
- 阶段三已增加无实例启动前预检：`POST /api/image-generation/services/{id}/preflight` 重新校验模型、Adapter、Diffusers 镜像、优云库存和价格；阶段四将结果持久化为带配置指纹的 `preflightSnapshot`，默认有效期 10 分钟（可由 `XUNTUI_IMAGE_PREFLIGHT_TTL_SECONDS` 调整），确认任务时仅复用未过期且配置未变的通过快照，否则重新校验。前端展示最近检查时间、有效至时间和过期提示；失败或失配时不会进入队列或创建实例。预检结果不替代真实实例内的依赖导入、模型加载和图像质量验收。
- 阶段五增加文生图运行遥测与诊断：任务持久化 `phase`、`phaseUpdatedAt`、`lastHeartbeatAt`、`failureCode`、`failureMessage` 和 `suggestedAction`；优云 Diffusers Fake 执行器区分实例启动、运行环境、模型、Adapter、生成、结果下载和清理失败。`GET /api/image-generation/tasks/{id}/diagnostics` 只返回脱敏摘要，优先使用 `failureMessage`，不暴露 legacy 原始错误、远端命令、路径或凭据。任务详情显示阶段、最后进展、失败分类、清理状态和建议动作；清理失败仍需先重试释放实例后才能重跑。
- 阶段六增加文生图历史与结果管理：任务列表支持服务、状态、关键词和归档筛选；新增任务克隆并编辑后重新生成、归档/恢复、非运行任务删除及结果目录清理；新增服务级只读结果聚合接口和服务配置页生成历史面板。克隆、归档和删除均不申请优云实例，归档任务仍可只读查看和下载结果；本阶段仅使用 Fake Provider 验证。
- QLoRA 仍只对兼容性探针明确通过的模型/镜像组合开放；ComfyUI、公开 API、多 GPU 和自动扩缩容继续延期。

本文后续按时间记录了历史状态，其中“当前版本”“待发布”“工作区未提交”等表述可能已经过期；发生冲突时以本节、当前 Git 状态和真实 API 状态为准。

## 2026-08-24 当前交接基线（v3.0.13）

这是新对话开始时应优先采用的项目事实，覆盖本文后面的历史记录：

- Git 分支：`master`
- 当前提交：`f6d8c2c fix: rollback OAuth release and expose Swagger docs`
- 当前标签：`v3.0.13`
- 回退基线：代码已从 `v3.0.12` 回退到 `v3.0.11` 的祖先提交 `04c880f`；`v3.0.12` 的 OAuth2、本地 Session 和 API 登录保护不在当前代码中。
- 本次新增：Next.js 通过同源路径代理 FastAPI 文档：`/docs`、`/openapi.json`、`/redoc`。生产构建目标为 Docker Compose 内的 `http://backend:8010`，本地默认目标为 `http://127.0.0.1:8010`。
- 发布方式：推送 Git tag 触发 Drone；`v3.0.13` 已推送，线上部署以 Drone pipeline 结果为准。
- 总台入口：Control Center 应用网关仍使用 `13101 -> 13100 -> frontend:3100`；Swagger 预期从应用入口的 `/docs` 加载。
- 后端直连文档：`http://185.61.210.32:18010/docs`；总台入口登录后应访问对应应用路径 `/docs`，不要把前端 404 页面当成 FastAPI 文档故障。
- 当前工作区：文档更新前应先执行 `git status --short`；不要读取或输出真实 `.env`、数据库密码、MinIO 密钥或 SSH 私钥。

### 老师新要求与后续设计方向（待录入）

老师刚刚提出的新要求尚未在本轮对话中提供原文。下一次对话请把要求完整粘贴到这里，再据此拆分：

1. 产品目标与用户角色
2. 业务流程与页面/模块变化
3. 数据模型、接口和权限边界
4. 训练、评测、推理或部署方面的优化方向
5. 分阶段实施顺序、验收标准和暂不做事项

在老师原文补入前，上述内容不代表已确认需求，只是新对话的整理模板。


## 2026-08-22 Drone tag 部署配置（当前状态）

已将 tag-only Drone 流水线提交并推送到 Gitee：

- 远程仓库：`git@gitee.com:moyanoo/cppt-platform.git`
- 分支：`master`
- Drone 配置：`.drone.yml`
- 当前提交：`9233012 ci: add tag-based Drone deployment pipeline`
- 触发条件：仅 Git `tag` 事件
- 构建内容：`cppt-platform-backend:${DRONE_TAG}` 与 `cppt-platform-frontend:${DRONE_TAG}`
- 部署方式：在 Drone runner 所连接的 Docker Engine 上使用 `compose.production.yml` 重建前后端并等待 healthcheck

下次发布的基本操作：

```bash
git checkout master
git pull --ff-only origin master
git tag v2026.08.22-1
git push origin v2026.08.22-1
```

生产机必须提前准备 `/opt/cppt-platform/.env.production`，并确保 Drone runner 能访问目标 Docker daemon、Docker Compose、PostgreSQL 和 MinIO。流水线不会读取仓库中的真实 `.env`，也不会把镜像推送到外部 registry；镜像直接构建在 runner 使用的 Docker Engine 上。详细说明见 [README.md](README.md) 的 “Deploy with Gitee tag + Drone” 章节。

历史交接记录中的“未配置 Drone”“不是 Drone 自动部署”等内容反映的是当时的状态，不覆盖本节的当前配置。

## 2026-08-21 推送状态与下一步

阶段二代码已完成本地首提交并成功推送到 Gitee：

- 远程仓库：`git@gitee.com:moyanoo/cppt-platform.git`
- 分支：`master`
- 阶段二代码基线提交：`ffa20f8 阶段二：接入 LLaMA-Factory 训练链路`
- 当前 Git 分支：`master`；本次 Drone 配置提交已推送，工作区可能包含后续未提交修改，操作前请先查看 `git status`
- 已验证：后端 42 个单元测试、Python compileall、TypeScript 检查、Next.js production build、命令级 LLaMA-Factory 冒烟
- 尚待持续验收：首次通过 Drone tag 流程完成生产发布，以及真实生产环境的 PostgreSQL/MinIO 凭据、网络和回滚演练

### SDXL LoRA 训练脚本制品供应链（2026-09-06）

- 已固定 Diffusers `v0.30.3` commit `c9ff360966327ace3faad3807dc871a4e5447501` 的 SDXL LoRA 脚本，大小 55,568 字节，SHA-256 为 `f72ce7d06b1613a1f7e051cb8c8401f210791e0b6e57ccc7fd6223dbbc92f949`。
- 后端解析顺序为优云镜像目标路径、MinIO `runtime-assets/diffusers/<commit>/`、固定 commit 的 jsDelivr、固定 commit 的 GitHub Raw；公网下载默认关闭。MinIO 密钥只留在后端，不会发送到优云实例。
- 未命中镜像时，后端通过 SFTP 上传已校验脚本并在远端执行 `sha256sum`，校验失败不会启动训练；实例创建后的所有失败继续进入既有 cleanup 路径。
- `deploy/youyun/sdxl-lora/` 提供未来自定义优云镜像构建上下文；发布 `CompShareImageId` 和真实实例探针仍需运维人员执行。回滚时可将 `XUNTUI_SDXL_SCRIPT_ARTIFACT_ENABLED=0` 并保持镜像预置路径，MinIO 不可变制品不删除。

下一阶段如需追溯阶段二部署过程，可阅读归档文档 [PHASE2_DEPLOYMENT_HANDOFF.md](docs/archive/PHASE2_DEPLOYMENT_HANDOFF.md)，按其中“部署前检查 → 服务器环境 → Docker/Drone → 真实 CPU 冒烟 → 生产验收”的历史记录参考；当前发布和验收规则以本文顶部基线、`CONTRIBUTING.md` 和真实 Git 状态为准。

## Executive summary

The project has moved beyond a Figma-only prototype. The text-generation path is a local end-to-end MVP: Next.js calls a real FastAPI API; FastAPI persists metadata in SQLite, stores uploaded text files on the WSL filesystem, submits training work to a single in-process GPU worker that invokes Unsloth, and can launch one vLLM inference subprocess that loads a completed task's Base Model plus Best Adapter. SFT and DPO task paths are implemented, and a DPO task has completed through the local training path. A user has also manually completed the dataset-upload -> publish -> training-task -> result flow and the Best-Adapter -> vLLM startup -> chat flow. The cloud architecture and access-control model remain plans.

The active dataset, task, and model-management views load from the API; the former runtime-adjacent `lib/mock-data.ts` fixture module was removed during the 2026-09-04 cleanup. The default API URL is `http://127.0.0.1:8010/api`; model-management preview overrides may use `NEXT_PUBLIC_MODEL_API_BASE`.

Model-management startup incident resolved on 2026-08-18: the page briefly displayed `模型服务暂不可用 / Not Found` because an older Uvicorn process was still serving the API contract from before `/api/model-assets` existed. Restarting the backend with the current source restored the model-management API. This was a stale-process issue, not a cached-model or Adapter-path failure. After future backend route changes, restart Uvicorn and verify `/api/model-assets?includeArchived=true`.

Shared-PostgreSQL connection incident resolved on 2026-08-21: PostgreSQL at the configured remote endpoint exhausted its global client slots and returned `FATAL: sorry, too many clients already` to both Navicat and the application. The server remained online; this was not a frontend, MinIO, Adapter-path, password, or local FastAPI outage. The server-side connection pressure was subsequently cleared. Model-management reads now degrade gracefully when PostgreSQL metadata is temporarily unavailable: the API still returns the SQLite-backed local model catalog and marks shared-storage details as `unavailable`, instead of failing the entire page with HTTP 500 / `Failed to fetch`. Operations that require a PostgreSQL write, including new shared uploads, must still fail explicitly until PostgreSQL recovers.

## 2026-08-21 阶段一交接状态

需求文档第 20 节的阶段一（配置和存储基础）已经完成代码落地。详细历史交接信息见归档文档 `docs/archive/PHASE1_HANDOFF.md`。

本阶段新增 PostgreSQL 迁移和任务/日志/指标/事件/推理存储实现，应用通过 `XUNTUI_STORAGE_BACKEND=sqlite|postgres` 选择存储后端，并增加了 Linux/Uvicorn 启动脚本。SQLite 仍仅作为数据集、本地模型目录和兼容缓存；CPU 服务器部署必须显式使用 PostgreSQL。

阶段一验证结果：WSL 后端 36 个单元测试通过，Python 编译检查通过，`npx tsc --noEmit` 通过，`npm run build` 通过。已在用户明确授权范围内连接并核验 `cppt_platform` 数据库：迁移版本为 1，阶段一 9 张表均已存在；未修改其他数据库或既有系统服务。阶段二已经完成代码实现并推送，真实 CPU 部署验收仍待进行。

## Capability matrix

| Area | Current truth | Stage |
| --- | --- | --- |
| Figma screens | Dataset and task prototypes were made and saved by the user; no Figma source is part of this repository. | Prototype only |
| Dataset list/create | Text JSONL list, single-page create form, browser progress, extension/size checks, API upload. | Real local MVP |
| Text validation | Backend parses every non-empty line as JSON and validates the first SFT record plus every DPO record. DPO accepts conversational `messages` + assistant `chosen`/`rejected` values and the legacy string shape. | Real but intentionally narrow |
| Dataset lifecycle | `failed`, `ready` (UI: pending publish), `published`; publish and prompt preview are real API calls. | Real local MVP |
| Dataset clone/delete | Clone copies the training file and marks the copy `ready`; it inherits the old validation message. Delete protects only built-in data. | Temporary implementation / debt |
| Image/text-to-image datasets | Visual understanding datasets are implemented as ZIP bundles containing `images/` and `metadata.jsonl`, with signature, path, annotation, and SFT/DPO schema checks. Text-to-image datasets, SDXL LoRA training-bundle materialization, Fake/Youyun training runtime, Adapter catalog synchronization, and Diffusers inference-task wiring are implemented. A guarded real Youyun SDXL LoRA generation and verified cleanup completed on 2026-09-01; production image-quality acceptance remains separate. | Local contract complete; cloud functional acceptance passed |
| Training task UI/API | Create/list/detail/stop/retry/clone/delete, published SFT/DPO dataset selection with task-type matching, common/advanced parameters, LoRA/QLoRA selector, and a restricted command-line mode with a multiline one-option-per-line editor that validates and submits directly to the same queue. Storage can now be selected with `XUNTUI_STORAGE_BACKEND`; PostgreSQL is the phase-one production path. | Real local MVP; PostgreSQL path added |
| Training worker | Text tasks use the existing Unsloth/TRL path or the LLaMA-Factory executor. Visual `IMAGE_TEXT` tasks use a dedicated `VisionTrainingRuntime` and `LocalVisionTrainingExecutor`, with a separate queue, shared GPU lock, local/Youyun dispatch, real process lifecycle, logs, metrics, and Adapter collection. | Text and visual local MVP; target-GPU acceptance still required |
| Metrics | Trainer logs are parsed into train/eval loss history. Validation runs select the lowest eval Loss; `validationSplit=0` runs select the lowest train Loss and expose the metric source explicitly. | Real local MVP |
| Chat comparison | Multi-turn base and Adapter calls use `/api/chat`; thinking is disabled; the selected Adapter variant is resolved explicitly and returned in the response. Base and tuned comparison answers reuse one Adapter-backed model process, disabling the Adapter only for the base answer. | Real local MVP |
| Model management | SQLite-backed base-model registration/discovery, training-model synchronization from completed task artifacts, lineage, Adapter variants, rename, refresh, archive/restore, and training/deployment handoff actions. | Real local MVP |
| Inference service | Service records, Base Model + Best Adapter deployment through vLLM, start/stop/restart/recover, online/offline markers, archive/restore/delete, internal chat proxy, SSE forwarding, logs, and Prometheus metric polling. Storage supports PostgreSQL in phase-one mode and retains SQLite compatibility mode. Single GPU only. | Real local MVP; PostgreSQL path added |
| Auth/permissions | No login, registration, pre-provisioned accounts, or business-space enforcement. | Deferred |
| Redis | Not connected. | Deferred |
| PostgreSQL/MinIO model storage | `cppt_platform.public` is connected for `model_files` and `model_file_entries`; MinIO `rag-assets` is used under the `models/` prefix for raw base/Adapter files, with PG-first metadata and per-file MD5/status tracking. Model listing survives a temporary PG read outage using the local SQLite catalog. Phase-one task/log/metric/inference PostgreSQL stores are implemented separately; the phase-one task/inference migration has been executed and verified in `cppt_platform` (migration version 1; nine tables present). | Real shared model-storage path; phase-one task/inference migration ready |
| DPO/vision training | DPO task selection, API validation, CLI task type, DPO normalization, TRL DPO trainer path, and a completed local DPO run are implemented. Visual SFT/DPO selection, `IMAGE_TEXT` materialization, model preflight, confirmation, local/Youyun queueing, loss/log persistence, stop/retry, and Adapter manifests are implemented. Text-to-image SDXL LoRA selection, preflight, materialization, Fake/Youyun executor contract, lifecycle and Adapter publication are implemented; real model-quality acceptance is pending. | Text DPO, visual training and image-generation training contracts implemented |

## What was actually exercised

- Qwen3-1.7B QLoRA smoke task: `ft-260815-145307-baf2` (one step).
- Qwen3-1.7B QLoRA full task: `ft-260815-181749-2569`.
- Qwen2.5-3B LoRA: `ft-260815-215401-88a5`, completed with 909 steps, 10% validation split, final train metric about `1.009`, best observed eval loss about `1.102` near step 300, final eval loss about `1.227`. This old run predates best-Adapter retention: the UI can recover its best metric from history, but inference must use its only saved final Adapter.
- DPO LoRA: `ft-260819-172419-82e0`, using `Qwen/Qwen2.5-3B-Instruct` and dataset `DPO试验`; the run retained Best Adapter at step 45 and Final Adapter at step 50. This was a short link/behavior validation run, not evidence of final model quality.
- Qwen2.5-7B QLoRA smoke task: `ft-260820-220425-e36e`, using the 10-record dataset `ds-f311cd19fabe`. The run completed 10 steps without a validation split, retained the lowest-train-loss Best Adapter and Final Adapter, and automatically uploaded both variants to shared storage. PostgreSQL contains two `available` `model_files` rows and four `model_file_entries` rows per variant; MinIO contains the corresponding raw Adapter files under `rag-assets/models/<model-id>/...`.

These records demonstrate the local path, not production reliability or model quality. QLoRA and LoRA are selectable implementations, but the current comparison is not a controlled single-variable experiment because the defaults use different model sizes (3B vs 7B).

## Best-checkpoint verification status

The best/final Adapter contract is implemented and covered by Store and chat-resolution tests. Real QLoRA and DPO runs completed on the RTX 4070 Laptop GPU and retained explicit Best/Final Adapter directories. Those runs exposed two integration issues which are now fixed in source: the worker briefly reported `completed` before releasing the GPU lease, and rounded log history could disagree with exact Adapter metadata. The API now publishes `completed` only after the trainer process exits and prefers exact `checkpointStep`/`evalLoss` metadata from Adapter artifacts.

DPO preprocessing is intentionally strict at the trainer boundary: `messages` + assistant preference values and legacy string records are converted to `prompt`/`chosen`/`rejected`, then the original columns are removed before TRL processing. The selected maximum sequence length is passed as all three DPO length limits. Fractional training controls are parsed as decimals in the web form, while integer-only controls remain integer-only.

The Best Adapter from this run has since been loaded successfully by vLLM together with the cached Qwen3-1.7B base model, and a real chat-completions request returned through the inference proxy. Legacy completed tasks that expose only an unversioned/final Adapter remain final-only and are intentionally excluded from inference-service creation. Cloning those tasks and running them through the current validation-enabled trainer will produce explicit best and final variants.

## Training CLI configuration status

The training task form supports two execution modes. `网页提交` keeps the ordinary parameter form; `命令行执行` hides those panels and exposes a controlled multiline training configuration. The editor starts with `python scripts/train_cli.py submit`, followed by one option per line. Blank lines are allowed, values containing spaces use quotes, and shell continuation characters are neither required nor interpreted. The `复制配置` action copies this platform configuration format for saving, sharing, or pasting back into the editor; it is not advertised as a PowerShell/Bash script.

Clicking `提交训练任务` in command-line mode sends the current editor text to `POST /api/tasks/cli`. The backend tokenizes each non-empty line, reports syntax or unsafe-command errors with a line number, rejects shell operators and unknown arguments, validates advanced `--trainer-param KEY=VALUE` entries against an allowlist, converts the result into the same `TaskCreate` payload as web submission, and enqueues it in the existing WSL worker. The command itself determines LoRA/QLoRA and can set training-related options that are not exposed in the ordinary form. No configuration download or second terminal step is part of this flow.

CLI mode keeps a published-text-dataset selector visible even though the ordinary model/data panels are hidden. Changing the selection updates only the `--dataset-id`, canonical `--dataset-name`, and matching `--task-type` lines, preserving other hand-edited options. Dataset list rows and the dataset detail drawer/preview expose the ID with a `复制 ID` action. The CLI endpoint canonicalizes the dataset name from the selected ID, rejects unknown IDs, rejects unpublished datasets, and rejects SFT/DPO type mismatches before enqueueing.

The standalone `scripts/train_cli.py` client remains available for terminal users to submit, watch, stop, retry, and inspect artifacts through the same API. The platform editor and backend accept multiline input; direct terminal usage remains documented separately. CLI parser, API submission, shell-safety, line-number validation, unknown/unpublished dataset rejection, and dataset-type matching are covered by `backend/test_cli_parser.py` and `backend/test_app_cli.py`.

## Current delivery focus: harden the complete local loop

Inference deployment is now part of the local MVP. Do not expand into vision, authentication, cloud infrastructure, public serving, or multi-GPU scheduling yet. The immediate goal is to keep the existing path trustworthy as one flow:

```text
upload and publish dataset
  -> create SFT or DPO task with optional validation
  -> finish Unsloth LoRA/QLoRA training
  -> select the lowest-eval-loss Adapter
  -> retain the final Adapter
  -> compare base, best, and final models in chat
  -> create a vLLM service from the Best Adapter
  -> start, debug, stop, archive, and restore the service
```

This flow is accepted only when all of the following are true:

1. A newly uploaded JSONL dataset passes validation, can be published, and is selectable by a task.
2. A task completes through the real FastAPI, SQLite, WSL, GPU, and Unsloth path rather than frontend mock data.
3. Logs and train/eval Loss update during training, and the detail response remains internally consistent after completion.
4. With validation enabled, the API and UI identify the same best step, best eval Loss, selected Adapter variant, and selected path; the final Adapter remains available.
5. Base, best Adapter, and final Adapter chat requests all return a response and report the Adapter variant actually loaded.
6. Adapter directories, optional merged output, and `train_metrics.json` exist at the paths reported by the API.
7. Store/chat/inference contract tests and `scripts/verify_best_checkpoint_e2e.py` pass with the current source.
8. A service created from a real Best Adapter starts vLLM, passes `/health`, returns a real non-streaming and SSE chat response, exposes runtime metrics, and releases GPU memory after stop.

The next safety work remains dataset lifecycle enforcement: block hard deletion of published or task-referenced datasets and revalidate clones. Inference hardening should then replace remaining mock detail/log widgets, expose accurate runtime/config data everywhere, improve low-memory defaults, and make service startup asynchronous. Production infrastructure and deferred model types remain later work.

## Confirmed product direction: model management MVP

On 2026-08-18, the user superseded the earlier decision and approved standalone model management as an MVP module.

The implemented local MVP includes:

- Automatic discovery of ModelScope cache directories when a repository ID can be inferred.
- Manual base-model registration with required ModelScope repository ID and local path.
- SQLite persistence for base and trained model assets.
- Automatic synchronization of completed training tasks into trained-model records.
- Explicit best/final Adapter variants when task artifacts provide them; legacy final-only tasks remain final-only.
- Source lineage from trained model -> base model -> training task.
- Display-name editing, path refresh, archive/restore, and training/deployment handoff actions.

Base-model upload is explicit from model management, while completed/stopped task Adapter variants are uploaded automatically to MinIO `rag-assets` under `models/<model-id>/...`; PostgreSQL stores the metadata and per-file checksums first. Merge Model, model publishing/approval, permissions, public serving, and multi-GPU inference remain outside this MVP.

The model page treats the local SQLite model catalog and shared-storage metadata as separate availability domains. A PostgreSQL read failure no longer hides locally registered/cached/trained assets; it marks their shared-storage state unavailable. This is a read-only degradation path, not an offline upload queue: PG-first uploads are blocked until PostgreSQL is reachable again.

## New-feature session workflow

`AGENTS.md` is intentionally absent. For a new feature, a new chat should initially read this file and `PROJECT_BASELINE.md`. It must first confirm the feature goal, user flow, UI and API behavior, states, failure handling, MVP boundary, excluded scope, and acceptance criteria. Do not start services, inspect CUDA, migrate source, install dependencies, or edit code until the user confirms the requirement summary, unless the requested feature is itself an environment repair. After implementation, update this file; update `PROJECT_BASELINE.md` only for durable product decisions, `ARCHITECTURE.md` only for structural changes, and `README.md` only when setup or commands change.

## Runtime and dependencies

Windows project root: `D:\训推平台`.

- WSL distribution: `Ubuntu-24.04`
- Training Python: `/root/miniconda3/envs/unsloth_env/bin/python` (Python 3.11.15)
- PyTorch 2.5.0 + CUDA 12.4
- Unsloth 2026.8.18
- TRL 0.24.0
- PEFT 0.20.0
- Inference Python: `/root/miniconda3/envs/vllm_env/bin/python` (Python 3.11.15)
- vLLM 0.10.2, PyTorch 2.8.0+cu128, CUDA 12.8
- vLLM compatibility pins: Transformers 4.55.2, Tokenizers 0.21.4, Hugging Face Hub 0.34.4, NumPy 2.2.6, BitsAndBytes 0.50.1
- Observed GPU: RTX 4070 Laptop, 8 GB; BF16 and xFormers were detected.
- Node package versions: Next 14.2.15, React 18.3.1, TypeScript 5.7.2, Node types 22.10.1.

`scripts/run_backend.py` now passes `XUNTUI_VLLM_PYTHON=/root/miniconda3/envs/vllm_env/bin/python` by default; this prevents inference subprocesses from falling back to `unsloth_env`, where vLLM is not installed. The normal commands and test commands are in [README.md](README.md). TypeScript checking and the production frontend build pass. The inference environment import/CLI/CUDA test, real vLLM startup, LoRA loading, health check, Prometheus metrics, chat request, and 4-bit BitsAndBytes startup have all been exercised. Previous sessions saw unstable WSL startup (`wsl.exe` failure code `4294967295`), so every run must still verify `/api/health`, not merely an open port.

## Durable paths

| Purpose | Path |
| --- | --- |
| Built-in sample | `data/qwenruozhi_v1/` in the Windows project, mirrored through the WSL app root |
| SQLite metadata | `/root/xuntui-platform/training.db` |
| Uploaded datasets | `/root/xuntui-platform/datasets/<dataset-id>` |
| Task artifacts/checkpoints | `/root/xuntui-platform/artifacts/<task-id>` |
| ModelScope cache | `/root/.cache/modelscope` |
| Config/job runtime files | `/root/xuntui-platform/runtime/` when configured |
| vLLM environment | `/root/miniconda3/envs/vllm_env` |

The actual paths can be overridden with `XUNTUI_DATABASE_PATH`, `XUNTUI_DATASET_ROOT`, `XUNTUI_ARTIFACT_ROOT`, `XUNTUI_RUNTIME_ROOT`, and `XUNTUI_MODELSCOPE_CACHE`.

## Known bugs, temporary implementations, and debt

### P0: blocks trustworthy use

1. Dataset deletion has no state or task-reference guard. The store rejects only built-in datasets, so a published dataset or one used by a completed/running task can be deleted along with its local files.
2. Dataset clone copies `trainPath`, sets `ready`, and carries forward the old validation message/count without re-running validation. A future source change or corrupted copy can therefore appear validated.
3. The WSL/API startup path is not reliable enough to call the local environment healthy when the port accepts connections but the API does not answer.

Best-checkpoint selection now supports graceful early stopping: a callback retains the current lowest eval-loss trainable LoRA state on CPU when validation is enabled, or the lowest train-loss state when `validationSplit=0`. A manual stop request is handled at a trainer boundary, while optional automatic early stopping uses `patience=3` and `minDelta=0`. The final model state and selected best state are saved separately, and stopped tasks with a valid Best Adapter are deployable. True checkpoint resume remains deferred.

### P1: important before expanding scope

1. SQLite plus one in-process worker is local-only; PostgreSQL/MinIO are connected only for shared model-file metadata and objects. There is still no Redis queue, durable worker lease, restart-safe scheduling, or migration of task/dataset metadata to PostgreSQL.
2. No real account, business-space, role, or dataset/task authorization isolation.
3. SFT text validation remains deliberately first-record schema validation; DPO rows are checked against their preference schema, but duplicate detection, file manifests, immutable dataset versions, and robust error reports are absent.
4. Image upload/preview/caption/`metadata.jsonl`, vision-language LoRA, and held-out test/challenge evaluation records are absent. DPO has passed a short local link validation, but there is no durable preference-quality benchmark yet.
5. ModelScope download progress, async download jobs, and download retry are not a complete workflow; most downloading happens when training or chat starts.
6. Merge-model output needs disk/GPU capacity controls and clearer artifact semantics. The `metrics` artifact is a JSON metrics file, not model weights.
7. Dataset/task references are not a foreign-keyed domain model; task payload stores a dataset ID only.
8. CORS is permissive for local development and is not suitable for a shared deployment.
9. Inference start/restart/recover HTTP calls wait for the complete vLLM load and health-check cycle; startup should become an asynchronous operation with a separately polled status.
10. The inference detail page still contains prototype-only runtime values and mock log content in some widgets. Online debug is real, but every detail tab is not yet fully API-backed.
11. The frontend API example displays a direct `/v1/chat/completions` URL on port 8010 even though the implemented local proxy is service-scoped under `/api/inference/services/{id}/chat`.
12. Unquantized BF16 serving of Qwen2.5-3B is unsafe on the observed 8 GB laptop GPU: model weights used about 5.85 GiB and left no KV-cache memory under the tested settings. Prefer 4-bit BitsAndBytes with a short context, low concurrency, `--enforce-eager`, and approximately `--gpu-memory-utilization 0.80` for this hardware. The verified 4-bit Qwen2.5-3B launch used about 2.12 GiB for weights and exposed about 4.09 GiB for KV cache.
13. vLLM Prometheus exposes KV Cache usage, request counts, TTFT/TPOT, and prefix-cache counters, but not the total KV Cache bytes/token capacity currently expected by the UI. Startup logs do contain allocation data and GPU block count; the backend does not parse them yet.
14. A successful training run does not by itself prove that the model learned the desired preference. The current comparison can show Base/Best/Final Adapter responses and the actual selected variant/path, but it does not yet verify adapter weight deltas or provide a held-out preference win-rate evaluation. Identical answers can therefore result from a short/weak run, prompt/template effects, decoding settings, or an evaluation prompt that does not expose the learned behavior; they should not be labeled as “weights were definitely not loaded” without those checks.
15. The observed RTX 4070 Laptop GPU has about 8 GB VRAM. DPO LoRA with a 3B model and a 1024-token ceiling is memory- and throughput-sensitive; long local runtimes are expected under conservative batch/gradient settings. Batch size and gradient accumulation remain user-controlled rather than being auto-rewritten by the platform.
16. Shared PostgreSQL currently depends on server-level connection capacity controlled outside this repository. When all client slots are exhausted, Navicat and PG-backed API operations fail with `FATAL: sorry, too many clients already`. The model list now degrades to local metadata, but uploads and direct `/api/model-files` reads still require PostgreSQL. Production deployment needs connection monitoring, reserved admin capacity, sensible idle-session timeouts, and preferably a pooler such as PgBouncer.

Implementation order after the complete-loop acceptance gate:

1. Protect published and task-referenced datasets from hard deletion; revalidate clones and introduce explicit version/manifests.
2. Stabilize startup and add a lightweight health check; move the API URL and storage roots to environment configuration.
3. Add full-row validation, duplicate reporting, durable held-out evaluation records, and clearer artifact/capacity handling.
4. Add administrator-provisioned accounts, business-space roles, audit logs, Redis, durable workers, retries, observability, and production hardening for the existing PostgreSQL/MinIO integration before shared deployment.

### P2: usability and maintenance

1. Loss chart lacks best-point markers, smoothing, and an explicit overfitting signal.
2. “Open artifact directory” calls Windows Explorer and is local-machine-specific.
3. API URL is hard-coded; there is no environment-based frontend configuration.
4. Frontend has no auth/error states for forbidden business-space actions.
5. `.gitignore` is now present and the repository tracks `origin/master`; generated files remain easy to commit accidentally, so review `git status --short --ignored` before every push.
6. Running `next build` while `next dev` is active can mix `.next` chunks; stop dev first.

## Open compliance questions

- Who owns and authorizes `D:\qwenaidataset\qwenruozhi.jsonl` and the checked-in split files?
- Do uploaded datasets contain personal, customer, regulated, or confidential data? What are retention and deletion SLAs?
- Which exact ModelScope repositories and revisions are approved, and what are their license/commercial/notice obligations?
- Which roles may publish, delete, clone, train, open artifacts, or view logs in a business space?
- What audit trail, token/secret storage, network boundary, CORS policy, and backup policy are required for a shared deployment?

## Inference service implementation status (2026-08-20)

The first local inference-service backend MVP is implemented in `backend/inference_service.py` and mounted by `backend/app.py`. It persists service metadata, logs, and runtime metrics in the existing SQLite database, only accepts completed training tasks with an explicit Best Adapter, launches vLLM with Base Model + Best Adapter, enforces a single inference GPU lease against the training worker, supports start/stop/restart/recover/exposure actions, and attempts automatic recovery at most once. The API also exposes service-scoped internal chat proxying, SSE forwarding, logs, and runtime metric polling from the vLLM Prometheus endpoint.

vLLM is installed in the separate Miniconda environment `/root/miniconda3/envs/vllm_env`. The first launch exposed an incompatible Transformers 5.15.0 install; the environment was corrected to Transformers 4.55.2 with compatible Tokenizers/Hugging Face Hub versions and NumPy 2.2.6. The backend was restarted with `XUNTUI_VLLM_PYTHON` pointing to this interpreter. Service `svc-382e8b2e3b12`, created from task `ft-260817-154620-e0e6` and its Best Adapter, successfully loaded Qwen3-1.7B after reducing GPU memory utilization from 0.90 to 0.65. vLLM passed `/health`, exposed the service and LoRA model through `/v1/models`, returned a real chat completion through the FastAPI proxy, and reported Prometheus metrics. The user later stopped the service, and the GPU allocation was released.

The frontend service list, create action, lifecycle actions, archive/restore/delete actions, and online debug now call the real inference API when the backend is reachable. Online debug previously replayed a fixed prototype answer; it now sends the actual multi-turn conversation to the service-scoped chat endpoint and parses vLLM SSE chunks. The create-task selector previously rendered the static `artifacts` fixture even after fetching `/api/tasks`; it now renders the API-derived `availableArtifacts` list. Service-name validation remains enforced in both frontend and backend, and configuration details now use the persisted service snapshot instead of hard-coded values where wired.

The inference-service create flow now also supports a `deploymentMode=custom` option. In this mode the UI keeps only the service name, completed training-task selector, and a large vLLM command input; managed basic/advanced parameter panels are hidden. The user pastes a complex `vllm serve ...` or `python -m vllm.entrypoints.openai.api_server ...` command and clicks `确认创建`; the backend parser/validator runs automatically, saves the raw command without allocating GPU, and leaves the service in `created`/待启动 state. At start time the backend converts it to an argv list, forcibly binds the selected Base Model, Best Adapter, service ID, internal host, and allocated port, and rejects shell composition (`&&`, pipes, redirects, command substitution, `bash -c`, `eval`, `source`, and `exec`). Custom-flag usage is persisted in `inference_command_usage` for later product analysis; there is no statistics page in this MVP.

The managed inference form now includes a `权重量化` selector with `不量化（BF16/FP16）` and `4-bit BitsAndBytes`. The latter emits `--load-format bitsandbytes --quantization bitsandbytes`; the vLLM environment contains `bitsandbytes==0.50.1`. A real Qwen2.5-3B + Best Adapter launch from task `ft-260819-172419-82e0` was verified with `max_model_len=1024`, `max_num_seqs=2`, `max_num_batched_tokens=1024`, `gpu_memory_utilization=0.80`, `dtype=float16`, and `enforce_eager`; vLLM loaded the Adapter, passed `/health`, and served `/v1/models` on private port 8011. Quantized weights remain compressed in GPU memory; compute may use fused kernels or on-the-fly dequantization, while KV Cache is allocated separately.

Only explicit Best Adapter artifacts are deployable. The older completed tasks `ft-260815-222225-ede5`, `ft-260815-221217-434c`, `ft-260815-215401-88a5`, and `ft-260815-181749-2569` contain only legacy final/unspecified Adapter artifacts; `ft-260815-213634-5d90` is stopped and has no complete Adapter. Their saved configurations already use a 10% validation split. Completed legacy tasks must be cloned and rerun, while the stopped task may be retried; the current trainer will automatically save the lowest-eval-loss Adapter at `<artifact-root>/<task-id>/adapter` and the final Adapter at `<artifact-root>/<task-id>/final_adapter`. The training list now distinguishes “Best Adapter 可部署” from “仅 Final Adapter，不可部署”.

The latest phase-two verification passes: the WSL backend suite runs 42 tests successfully, Python compilation passes, `npx tsc --noEmit` passes, `npm run build` completes successfully, and the command-level LLaMA-Factory smoke path passes. The smoke command is a test double, not real model training, and does not replace real CPU LLaMA-Factory acceptance. The code was pushed to Gitee in commit `ffa20f8`; the historical deployment gate is preserved in [PHASE2_DEPLOYMENT_HANDOFF.md](docs/archive/PHASE2_DEPLOYMENT_HANDOFF.md). Remaining inference work includes fully API-backed logs and runtime widgets, accurate KV Cache capacity reporting, asynchronous startup, a corrected API-use example, a repeatable automated browser/SSE acceptance test, and a future command-usage statistics view.

## 2026-08-21 阶段二 CPU Linux 部署与真实验收（最新，覆盖此前“仅命令级 smoke”状态）

阶段二已部署到 CPU Linux 服务器 `/opt/cppt-platform`。Backend/Frontend Compose 服务均 healthy，Backend `/api/health` 为 `status=ok` 且使用 PostgreSQL 持久化，Frontend 返回 HTTP 200。未修改宝塔 Nginx，未部署或操作 Redis，未影响其他 Docker 服务。

真实验收任务 `ft-260821-191055-0395d8` 使用容器内 `/opt/venvs/llamafactory/bin/llamafactory-cli`（LLaMA-Factory 0.9.5）、Torch `2.7.1+cpu` 和 Qwen2.5-0.5B-Instruct，执行 SFT / LoRA、batch size 1、cutoff length 64、max steps 1。任务状态为 completed、finalStep=1，日志明确记录 `device: cpu`、`distributed training: False` 和 `Total optimization steps = 1`。

完整验收脚本返回 `accepted: true`：REST 日志 430 条、metric 1 条、WebSocket 日志 430 条及终态事件 1 条；manifest 含 22 个文件。PostgreSQL 中任务日志/指标/事件均有记录；Final Adapter 共享模型记录 `model-3dcbf0321bc748ac` 为 available。MinIO `rag-assets` 中有 6 个对象、13,611,639 字节，对象前缀严格为 `model/model-3dcbf0321bc748ac`。由于 validationSplit=0，本次正确地只生成并选择 Final Adapter，没有伪造 Best Adapter。

最终本地回归：48 个后端测试通过，compileall、TypeScript、Next.js build 和 production Compose config 均通过。当前 `HEAD` 与 `origin/master` 同为 `49f0ef9c62b5aac88f8b14f7aa1045954c93b110`；本轮部署与验收修改仍未提交，也未推送 Gitee。详细历史记录见 `docs/archive/PHASE2_DEPLOYMENT_HANDOFF.md`。

## 2026-08-22 临时公网手动测试入口

因暂时没有域名，服务器已切换为临时公网端口方案：

- 前端：`0.0.0.0:13100` → 容器 `3100`
- Backend API：`0.0.0.0:18010` → 容器 `8010`
- 推理服务：仍为 `127.0.0.1:8011`，未公网暴露

前端镜像已按公网 API 地址重新构建，公网访问路径为 `http://185.61.210.32:13100`，API 健康地址为 `http://185.61.210.32:18010/api/health`。服务器内部和本机外部请求均已返回成功。

该临时方案会直接暴露 Backend API，仅用于项目组手动测试；正式使用应切换为域名 + HTTPS + Nginx 反向代理，并收回 `18010` 公网暴露。

## 2026-08-22 Bug 修复新对话交接

当前项目已经完成阶段二真实 CPU 验收，但后续 Bug 修复必须基于当前未提交工作区继续，不能把工作区重置到 `HEAD`，也不要擅自提交或推送。

### 当前可访问环境

- 手动测试前端：`http://185.61.210.32:13100`
- 手动测试 Backend API：`http://185.61.210.32:18010/api`
- 健康检查：`http://185.61.210.32:18010/api/health`
- 推理服务 `8011` 仍只绑定服务器本机，不对公网开放
- 服务器部署目录：`/opt/cppt-platform`
- SSH：`root@185.61.210.32:22`，公钥登录已确认可用
- 当前线上运行版本最初通过 SSH 手动同步 + Docker Compose 部署；tag-only Drone 自动部署配置已就绪，但首次 tag 发布仍待验收

### 新 Bug 对话必须先读取

1. `docs/archive/PHASE1_HANDOFF.md`
2. `REQUIREMENTS_LLAMA_FACTORY_REMOTE_TRAINING.md`
3. `CURRENT_STATE.md`
4. `docs/archive/PHASE2_DEPLOYMENT_HANDOFF.md`
5. `docs/archive/BUGFIX_HANDOFF.md`

### 新对话执行边界

- 先复现并定位 Bug，再修改；不要把测试替身结果当成真实训练结果。
- 修改后至少运行相关后端测试、`compileall`、TypeScript 检查；涉及前端时运行 `npm run build`。
- 涉及部署时先检查端口和容器，不要停止、重启或替换服务器已有基础设施容器。
- 不重复执行阶段一 PostgreSQL 迁移，不部署新的 PostgreSQL、MinIO 或 Redis。
- 不读取、输出或提交 `.env`、数据库密码、MinIO 密钥和 SSH 私钥。
- 不修改宝塔 Nginx，除非用户明确要求改回正式域名入口。
- 不推送 Gitee；提交或推送必须等待用户明确确认。
- 临时公网模式下，前端使用公网 API 地址构建；如果改动 `NEXT_PUBLIC_API_BASE_URL`、CORS 或 Compose 端口，必须重新构建并验证公网前端和 API。

后续 Bug 修复对话支持直接发送截图和自然语言描述，不要求使用固定格式；助手根据截图和描述自行补充排查信息。

## 2026-08-22 CPU 冒烟任务 optimizer 与“释放 GPU”停滞修复

本轮针对用户任务日志中的 `ImportError: You need to install bitsandbytes` 以及页面长时间停留在“正在释放聊天模型占用的 GPU”完成了真实服务器排查、回归测试、修复、部署和 1-step CPU 训练验证。

### 根因

1. 失败任务 `ft-260822-031024-89bf92` 在 CPU-only LLaMA-Factory 环境中提交了 `optimizer=adamw_8bit`。该 optimizer 依赖 bitsandbytes，而正式 CPU 容器未安装该 GPU 量化优化器依赖，因此 Transformers 在创建 optimizer 时失败。日志中的 tokenizer PAD/BOS/EOS 对齐信息只是 warning，不是失败原因。
2. 原冒烟任务 `ft-260822-035301-1f6020` 不是死锁。Web 表单没有提交 `maxSteps`，CLI 示例还固定显示 `--max-steps -1`，任务实际按 1 epoch 跑 10 个 CPU steps；在 `cutoff_len=512` 下单步约需数分钟。
3. 后端进度解析只识别旧格式 `Total steps = N`，不识别当前 LLaMA-Factory 输出的 `Total optimization steps = N`，导致总步数未知、metric step 推断错误，页面长期显示 0% 和旧的资源释放文案。

### 修复

- CPU 执行器把 `adamw_8bit`、`paged_adamw_8bit` 自动降级为 `adamw_torch`，兼容数据库中已有任务参数。
- Web、mock 数据和冒烟/验收脚本默认 optimizer 改为 `adamw_torch`；8-bit 选项明确标注需要 bitsandbytes。
- Web 训练表单新增“最大训练步数（-1 按 Epoch）”，payload 和 CLI 预览均使用真实 `maxSteps`。
- 进度解析兼容 `Total optimization steps = N`，并结合 epoch 推断实际 step；事件携带 `step`、`total_steps` 和 `progress`。
- 任务阶段文案调整为“正在释放对话模型资源”“正在启动 LLaMA-Factory 训练进程”“训练已开始，等待首个训练指标”和“训练中 · Step X/Y”，不再把训练计算阶段误显示成释放 GPU。

### 验证

误配置的 10-step 任务 `ft-260822-035301-1f6020` 已通过应用 API 安全停止，终态为 stopped；没有重启或操作 PostgreSQL、MinIO、Redis、Nginx 等基础设施容器。

修复后的真实回归任务 `ft-260822-050144-abbfd4` 使用 Qwen2.5-0.5B-Instruct、SFT/LoRA、CPU、batch size 1、cutoff length 64、`max_steps=1`、`optim=adamw_torch`、`gradient_checkpointing=false`。任务于 2026-08-22 完成：state=completed、progress=100、finalStep=1、train loss=2.083，单步训练耗时约 204.7 秒。日志包含 `Total optimization steps = 1`，且没有 bitsandbytes ImportError。

任务生成 Final Adapter、checkpoint-1 和 manifest；Final Adapter 已写入共享模型文件元数据，记录 `model-bec927872f664971` 状态为 available，对象前缀为 `model/model-bec927872f664971`。公网 Backend 健康检查与前端均返回 HTTP 200，worker 已恢复 idle。

本地验证结果：后端 52 个测试通过，Python compileall、`npx tsc --noEmit`、`npm run build`、`git diff --check` 均通过。仅重建并重启本应用 backend/frontend 容器；本轮修改仍未提交、未推送。

## 2026-08-22 训练前后对比页 Adapter 路径与 CPU 推理修复

用户在真实完成任务 `ft-260822-051908-a269af` 的训练前后对比页看到“LoRA Adapter 路径无效”。排查确认：任务 Adapter 实际位于 `/workspace/training-work/ft-260822-051908-a269af/output`，文件存在且包含 `adapter_config.json`、`adapter_model.safetensors`；后端原先只允许 `/workspace/artifacts/<task-id>`，与真实 LLaMA-Factory 执行器的 `/workspace/training-work/<task-id>` 输出目录不一致，因此 API `/api/chat` 返回 403。

修复内容：

- Adapter 路径校验改为只允许当前任务自己的 `/workspace/artifacts/<task-id>` 或 `/workspace/training-work/<task-id>` 子目录，并要求目录真实存在；其他任务目录和越权路径仍拒绝。
- CPU 部署原先调用 `chat_inference.py` 时直接导入未安装的 `unsloth`，导致路径修复后继续出现 `No module named 'unsloth'`。新增标准 Transformers + PEFT fallback：GPU 优先使用 Unsloth，CPU 或未安装 Unsloth 时使用 `AutoModelForCausalLM` + `PeftModel` 加载 Adapter，并自动选择 CPU/GPU device。
- 对旧版 tokenizer 不支持 `enable_thinking` 的情况增加 chat template fallback。
- 生成输入不再硬编码 `.to("cuda")`；CPU 推理可正常运行。

验证：

- 新增 Adapter 允许/越权拒绝和 CPU loader/chat-template 回归测试。
- 后端全量测试：56 个通过。
- Python compileall：通过。
- TypeScript：通过（首次直接执行时因 `.next/types` 尚未生成报错，随后运行 `npm run build` 后再次执行通过）。
- Next.js production build：通过。
- `git diff --check`：通过。
- 仅重建并重启本应用 backend 容器；frontend 及 PostgreSQL、MinIO、Redis、Nginx 等基础设施未操作。
- 线上任务 `ft-260822-051908-a269af` 的真实 `/api/chat` Adapter 请求已成功返回：HTTP 200，`adapterVariant=final`、`adapterStep=1`、文本回答“你好！”，实际加载路径为 `/workspace/training-work/ft-260822-051908-a269af/output`。
- 公网 `/api/health` 返回 `status=ok`，worker=idle，activeTaskId=null。

## 2026-08-22 Bug 修复：训练模型未显示在模型管理

生产 PostgreSQL 模式下，训练任务存储在 `training_tasks`，模型管理目录仍使用 SQLite。原 `PostgresTaskStore` 未覆盖 `sync_model_assets()`，导致 `/api/model-assets` 只从 SQLite `tasks` 同步，训练模型计数为 0。另一个问题是训练资产根路径被错误固定为 `ARTIFACT_ROOT/<task-id>`，与实际 `/workspace/training-work/<task-id>/output` 不一致。

本轮已将同步逻辑重构为 `_sync_model_asset_rows()`，新增 PostgreSQL 任务行同步实现，并使用真实 Adapter variant 路径生成训练模型资产。已补回归测试并通过 58 个后端测试；compileall、TypeScript、Next.js production build 均通过。仅重建 backend 应用容器，线上 `/api/model-assets?includeArchived=true` 已返回 `trained-ft-260822-051908-a269af` 等训练模型，状态 available；基础设施容器未操作。未提交、未推送。

## 2026-08-22 推理服务支持自选 Best / Final Adapter

创建推理服务原先虽然会读取训练任务的 selected Adapter，但后端仍额外强制要求任务必须存在 Best Adapter；前端也只把每个任务折叠成一个默认产物，并固定提交任务 ID。因此只有 Final Adapter 的真实 CPU 任务 `ft-260822-051908-a269af` 会报“该训练任务没有可部署的 Best Adapter”。

本轮已改为显式选择 Adapter variant：

- 创建服务与自定义 vLLM 命令校验 API 新增 `adapterVariant: "best" | "final"`，旧客户端未传时仍默认 `best`。
- 后端严格按用户选择解析对应 Adapter；不再额外要求 Best Adapter。所选 variant 不存在或路径不存在时仍拒绝创建。
- 前端把已完成或已停止任务中的 Best、Final Adapter 展开为独立下拉选项，同一训练任务可显示两项；创建服务和命令校验均提交真实 `taskId` 与所选 `adapterVariant`。
- Final Adapter 没有 Eval Loss 时显示“无 Eval Loss”，详情页和创建摘要按所选 variant 展示 Best/Final。

本地验证：后端全量 61 tests passed，Python compileall、`npx tsc --noEmit`、Next.js production build、`git diff --check` 均通过。

线上只同步并重建本应用 backend/frontend，未操作 PostgreSQL、MinIO、Redis、Nginx 或其他基础设施容器。公网健康检查与前端均返回 HTTP 200。使用 Final-only 任务 `ft-260822-051908-a269af` 创建配置 `svc-c3d85c6ddad4` 成功，状态 `created`（未启动、未占 GPU），返回 `config.adapterVariant=final`，Adapter 为 `/workspace/training-work/ft-260822-051908-a269af/output`；同一任务显式选择不存在的 Best Adapter 仍正确返回 HTTP 400。

本轮修改未提交、未推送。

## 2026-08-22：PostgreSQL 推理存储与 CPU vLLM 改造（当前会话中断交接）

### 用户已确认的目标

用户已确认开始执行以下两项改造：

1. 生产推理服务的配置、状态、启动日志、运行指标、命令使用记录、归档/恢复等数据统一使用 PostgreSQL；SQLite 仅保留为本地兼容缓存，不得作为生产推理服务的权威存储。
2. 在现有 backend 镜像中增加 CPU 可用的 vLLM，建立独立运行环境 `/opt/venvs/vllm`，不得破坏已经验收通过的 `/opt/venvs/llamafactory` 训练环境。当前目标是 CPU 服务器跑通流程，后续切换优云 GPU 时再替换 GPU vLLM 环境。

### 本次中断前已完成的代码修改（尚未完整验证）

- `backend/inference_service.py`
  - 增加 SQLite 字符串时间与 PostgreSQL `datetime` 的统一序列化。
  - 修复 PostgreSQL 推理日志读取时对 `datetime.replace("T", " ")` 的错误调用；该问题此前会覆盖真实的 vLLM 启动错误。
  - 增加启动前 vLLM Python/模块诊断：检查 `XUNTUI_VLLM_PYTHON` 文件，并执行 `import vllm` 获取版本；失败时写入明确错误。
  - 增加 `XUNTUI_INFERENCE_DEVICE=cpu` 的 CPU 模式判断；CPU 模式不再传递 GPU memory utilization，并拒绝 bitsandbytes/FP8 参数。
  - 启动日志增加 Inference Python、vLLM 版本、设备、Base Model 和 Adapter 信息。
  - 支持通过 `XUNTUI_VLLM_LD_PRELOAD` 注入 CPU vLLM 所需的 `LD_PRELOAD`。
  - CPU 模式下运行状态的资源标签显示为 `CPU`，不再误显示 GPU。
- 新增 `requirements-vllm-cpu.txt`，暂记录官方 x86_64 CPU wheel URL：
  - vLLM `0.27.1`
  - `manylinux_2_34_x86_64`
  - `cp38-abi3`，可用于 Python 3.11
- 当前已通过：`python -m py_compile backend/inference_service.py`

### 当前仍未完成的工作

- 尚未把 `/opt/venvs/vllm` 安装步骤正式写入 `deploy/docker/backend.Dockerfile`。
- 尚未在 `compose.production.yml` 配置 `XUNTUI_VLLM_PYTHON=/opt/venvs/vllm/bin/python`、CPU vLLM 环境变量和独立运行时 PATH。
- 尚未补充 PostgreSQL fake adapter/回归测试，尤其是 PostgreSQL `datetime` 的服务序列化、日志、指标、归档恢复和命令使用记录。
- 尚未补充 vLLM 启动前诊断、CPU 参数过滤的单元测试。
- 尚未运行后端全量测试、compileall、前端检查、Docker build，也未部署或重建线上容器。
- 尚未读取、修改或操作真实 `.env`、密钥、基础设施容器；当前没有线上变更。

### 官方 CPU vLLM 调研结论（供下一会话继续）

官方文档：

- https://docs.vllm.ai/en/stable/getting_started/installation/cpu.html
- https://hub.docker.com/r/vllm/vllm-openai-cpu

官方文档当前说明：Intel/AMD x86 的 AVX512/AVX2 预构建 CPU wheel 从 vLLM 0.17.0 起提供；推荐使用带 `+cpu` 的 release wheel。当前调研时 GitHub latest release 为 `v0.27.1`，对应 wheel 为：

```text
https://github.com/vllm-project/vllm/releases/download/v0.27.1/vllm-0.27.1+cpu-cp38-abi3-manylinux_2_34_x86_64.whl
```

官方同时提示 CPU wheel 使用前需要设置 Intel OpenMP 的 `LD_PRELOAD`。下一会话应在镜像内安装并验证 `libiomp5.so` 的真实路径，不要猜路径；必要时通过环境变量注入。官方 CPU 运行时还支持 `VLLM_CPU_KVCACHE_SPACE` 和 `VLLM_CPU_OMP_THREADS_BIND`，冒烟阶段应使用保守值，避免占满 62 GiB 服务器内存。

### 继续执行时的安全边界

- 不要 `git reset`、覆盖、提交或推送。
- 不要读取或输出 `.env`、PostgreSQL 密码、MinIO 密钥、SSH 私钥。
- 不要停止、重启、重建或修改服务器已有 PostgreSQL、MinIO、Redis、Nginx 或其他基础设施容器。
- 只允许在验证通过后按需重建本应用 backend；不要因为安装 vLLM 而改动已验收的 LLaMA-Factory venv。
- 先本地回归测试和镜像验证，再部署；部署后先检查 `/api/health` 和应用容器日志，再做 Final Adapter 推理冒烟。

## 2026-08-22：PostgreSQL 推理存储与 CPU vLLM 最终复核

### 已完成并验证

- PostgreSQL 推理存储回归测试已覆盖 `TIMESTAMPTZ -> datetime` 的服务字段、日志、指标、命令使用记录，以及归档、恢复、永久删除和关联数据级联删除；测试使用 fake PostgreSQL adapter，不连接生产数据库。
- vLLM 启动诊断和 CPU 参数测试已覆盖独立 Python、`LD_PRELOAD`、真实 import 错误、CPU GPU-only 参数过滤，以及 vLLM 0.27.1 的 `--no-enable-log-requests` 参数。
- 新增 x86 CPU 指令集启动前诊断：`XUNTUI_INFERENCE_DEVICE=cpu` 时，如果 `/proc/cpuinfo` 明确显示缺少 AVX2，将在启动 vLLM 子进程之前返回清晰错误，避免只看到模型注册子进程的 `SIGILL`。
- backend 验证镜像保留两个独立环境：`/opt/venvs/llamafactory` 与 `/opt/venvs/vllm`。vLLM 环境验证版本为 torch `2.13.0+cpu`、torchaudio `2.11.0+cpu`、torchcodec `0.16.0+cpu`、vLLM `0.27.1`，`pip check` 通过。
- Dockerfile 已强制将 vLLM 间接安装的 CUDA torchaudio/torchcodec 替换为 PyTorch CPU index 的 CPU wheel，避免 `libcudart.so.13` 和 `libnvrtc.so.13` 错误。
- 本地最终验证：后端 `68 tests`、Python compileall、`npx tsc --noEmit`、Next.js production build、Production Compose config、`git diff --check` 全部通过。
- 服务器一次性验证镜像：`cppt-platform-backend:vllm-cpu-verify-20260822-c`，镜像 ID `sha256:33e03ef3ee764fbe5fd8ea1b8b4ebdb3ee38b4a895155671039c2ac1e604da4b`；未将该验证镜像切换为生产 backend。

### 生产 CPU 兼容性结论

目标服务器 CPU 为 `Intel Xeon E5-2680 v2`（Ivy Bridge），`x86_64`，有 AVX，但没有 AVX2/AVX512。vLLM 官方 CPU x86 文档说明预构建 wheel 支持 AVX512 或 AVX2；vLLM 0.27.1 的 x86 CPU 构建也只选择 AVX512 或 AVX2 编译路径，没有 AVX-only 路径。

在当前生产 CPU 上，普通 `import vllm` 能成功，但真实检查 `Qwen2ForCausalLM` 时，vLLM 模型注册子进程稳定退出为：

```text
subprocess.CalledProcessError: ... died with <Signals.SIGILL: 4>
```

因此当前服务器不能完成 vLLM engine、`/health`、`/v1/models` 或推理请求验收。可行生产路线是迁移到支持 AVX2/AVX512 的 CPU 节点，或后续切换 GPU 节点；不应把当前镜像的 import 成功描述为推理成功，也不应继续在 Ivy Bridge 上盲目启动服务。

### 当前线上状态和部署边界

- 生产 backend 仍为镜像 `sha256:895da90c7c588f59b156ae10fdde631001dfdbe3067c64ab008af7d3699b683b`，容器保持 healthy；本轮最终 AVX2 诊断修改没有切换到生产。
- 公网 `/api/health` 返回 `status=ok`、`queue=postgres`、`storageBackend=postgres`、`inference=idle`。
- `GET /api/inference/services/svc-329adf79cc38/logs` 返回 HTTP 200，PostgreSQL datetime 日志问题已不再导致 500。
- 因真实 vLLM 推理验收受 CPU ISA 阻塞，按照“验证通过后才重建”的约束，本轮没有再次重建或重启生产 backend，也没有操作 PostgreSQL、MinIO、Redis、Nginx 或其他基础设施容器。
- 所有修改仍未提交、未推送。

## 2026-08-22：CPU 节点 GPU 状态友好文案（待下次发版）

在继续线上诊断前已先确认生产容器：`cppt-platform-backend-1` 为 healthy，restart count 为 0，公网 `/api/health` 返回 `status=ok`、`storageBackend=postgres`。没有对刚完成 tag 部署的生产容器执行重建或重启。

本地已将 CPU 节点缺少 `nvidia-smi` 时的 GPU 状态提示由技术异常改为：

```text
当前节点未检测到可用 GPU
```

该文案同时用于 `/api/gpu-info` 和训练异常退出后的 GPU 诊断日志；其他非“命令不存在”的异常仍保留诊断详情。新增 2 项回归测试后，后端全量 `70 tests` 与 Python compileall 通过。修改尚未部署，计划与 CPU vLLM 推理修复一起进入下次发版。

## 2026-08-22（晚）：微调模块改版、时区修复、MinIO 合并与 v3.0.4 网关模式上线

### 微调任务模块改版（已随 v2026.08.22-2 上线）

- LoRA / QLoRA 选项只显示精度（16-bit / 4-bit），不再写死基模；切换微调方式不再强制换模型。
- 基础模型默认为空，点击输入框弹出下拉框（空关键词列本地缓存模型，输入走 ModelScope 搜索，粘贴 Repo ID 出现精确匹配项）。
- 训练数据集默认为空，选完基模才显示；选完数据集才展示训练参数（03/04/05 区块），之前显示引导提示。
- 训练产出的模型名称初始为空，选完基模按 `仓库名-任务类型-微调方式` 生成示例名 placeholder（如 `qwen2.5-7b-instruct-sft-qlora`）。
- GPU 自动分配行改为 `GET /api/gpu-info` 实时探测（nvidia-smi），前端不再写死节点信息，为优云 GPU 做准备。
- `/api/models/search?q=` 空关键词返回本地缓存模型。CLI（命令行执行）模式交互保持不变。

### 任务/日志时间统一为北京时间（已随 v3.0.4 验证生效）

- 根因：backend 容器为 UTC，且 postgres `created_at TIMESTAMPTZ` 用 `DEFAULT NOW()` + `::text` 渲染跟随会话时区（UTC）。
- 修复：`store.py` 时间戳与任务 ID 使用 `XUNTUI_TZ`（默认 Asia/Shanghai，缺 tzdata 时回退本地时区）；`pg_connect` 每连接 `SELECT set_config('TimeZone', %s, false)`；镜像安装 tzdata 并设 `TZ`；compose 注入 `TZ/XUNTUI_TZ`。
- 事故记录：v2026.08.22-3 首版用了 `SET TIME ZONE %s`（SET 不支持绑定参数），导致 backend 启动迁移崩溃、容器循环重启，公网 API 于 17:26–17:39 不可用；热修复改用 `set_config` 后随 v2026.08.22-4 / v3.0.3 恢复。v3.0.4 已实测：容器时间 CST、postgres 会话 `Asia/Shanghai`、`NOW()` 返回 +08。
- 注意：修复前写入的历史任务日志仍是 UTC 时间，不做改写；新任务全部为北京时间。

### MinIO rag-assets 桶目录合并（已完成）

- `models/` 下 8 个对象（两个 LoRA adapter 包）已合并到 `model/` 前缀并删除源对象，`models/` 已清空；工具脚本入库：`scripts/minio_merge_model_folders.py`（默认预演，`--apply` 执行；冲突跳过不删除）。
- `model-0.5b/` 等其他目录为同事手动上传，未动。

### 网关模式上线（v3.0.4，访问方式已变更）

- 服务器 `/opt/cppt-platform/.env.production`：`XUNTUI_FRONTEND_BIND=127.0.0.1`、`XUNTUI_CORS_ORIGINS=http://185.61.210.32,http://185.61.210.32:13100`（改前备份 `.env.production.bak-20260822`）。
- **13100 已收回公网直连；正式入口为 `http://185.61.210.32/`（Control Center 登录 → `/app/cppt-platform`）。**
- API `:18010` 暂保持公网（前端镜像仍以绝对地址构建），待网关支持 `/api` 反代后按归档文档 `docs/archive/PHASE2_DEPLOYMENT_HANDOFF.md`「网关模式切换清单」切回 `/api` 并收紧 backend 绑定。
- v3.0.4 部署后实测：双容器 healthy、断言通过、CORS 放行网关来源。

### 今日 tag 记录

`v2026.08.22-1`（CI 参数错误，已删除）→ `v2026.08.22-2`（微调模块 + CI 修正，首次成功部署）→ `v2026.08.22-3`（时区修复，引发 backend 启动崩溃）→ `v2026.08.22-4`（set_config 热修复）→ `v3.0.1`/`v3.0.2`（codex：网关与部署流程）→ `v3.0.3`（网关断言 + 热修复合并；断言失败但容器健康）→ `v3.0.4`（网关配置修正后全绿，当前线上版本）。

### 遗留事项

- codex 会话的推理服务修复（GPU 友好文案、CPU vLLM 诊断等）仍在工作区未提交，按其计划与 vLLM 后续修复一起发版。
- 后续规划（远程训练优云 GPU、MinIO exports/ 导入数据集）见 `REQUIREMENTS_LLAMA_FACTORY_REMOTE_TRAINING.md` 第 28 节。

## 2026-08-22 数据集交互修复（v3.0.6 发布内容）

本轮根据数据集管理页面反馈完成以下修改，纳入 v3.0.6 发布：

- 数据集列表页在“新建数据集”旁新增“导入数据集”按钮。导入弹窗读取 MinIO `rag-assets` 桶 `exports/` 目录中的对象，选择 JSONL 后填写名称和 SFT/DPO 结构，导入后复用现有校验与发布流程。
- 页面刷新通过 `sessionStorage` 恢复当前模块和视图；刷新列表、创建页、只读预览页不会强制跳转到数据集列表。只读预览会尝试恢复正在查看的数据集。
- 克隆名称改为从“副本1”开始递增；已有“副本1/副本3”时下一次会取最小可用编号“副本2”，重复克隆同一源数据集不再全部显示为无编号“副本”。
- 原“编辑”操作改名为“只读”；数据集内容预览页保持不可编辑，并移除表格中的“操作”列。

相关后端接口：

- `GET /api/datasets/import-sources`：列出 `exports/` 下可导入对象。
- `POST /api/datasets/import`：从 MinIO 下载 JSONL 到本地数据集目录，执行现有校验并写入数据集索引。

本轮回归结果：`python -m unittest backend.test_app_cli backend.test_store`（22 个通过）、`python -m compileall -q backend scripts`、`npx tsc --noEmit`、`npm run build` 均通过。

部署验收（2026-08-22 21:48，UTC+8）：v3.0.6 tag pipeline 已完成；生产 backend/frontend 镜像均为 v3.0.6，容器 healthy、restart=0。`/api/health` 返回 200，数据集导入接口校验返回 422（非法来源被正确拒绝）。

## 2026-08-23：阶段五优云 SSH 远程训练完成并随 v3.0.8 上线

- 优云 Provider、一次性实例生命周期、Paramiko SSH/SFTP、远程环境安装、训练日志/指标轮询、产物回传与 finally 清理已完成。
- Web UI 已提供本地 CPU / 远程优云 GPU 环境选择、GPU 机型和实时库存面板；远程任务无需本地模型缓存。
- 真实脚本冒烟 `smoke-08231240` 已完成 4090 QLoRA 6-step 训练并删除实例。
- Web 全链路成功任务：`ft-260823-141622-2390`，Qwen2.5-0.5B-Instruct、SFT、QLoRA、max_steps=2；终态 completed、progress=100、Final Step 2，详情页真实 Loss 图、日志、Final Adapter、manifest 均已核对，Final Adapter 已上传共享存储；实例 `uhost-1uee7f5am0t1` 已删除。
- Web 验收修复了 provider/executionMode 丢失与错映射、eval_dataset/val_size 冲突、auto LoRA target、SSH 失活重连、后台安装幂等启动，以及 Final-only 推理可用文案。
- 最终回归：Linux/WSL 后端 100 tests、Python compileall、TypeScript、Next.js production build、git diff check 全部通过。
- 发布提交 `f934c82 feat: 阶段五优云 SSH 远程训练` 与 tag `v3.0.8` 已推送；Drone tag 流水线完成，生产 backend/frontend 均切换到 `v3.0.8` 并保持 healthy。
- 生产 `/api/health` 返回 `status=ok`、`queue=postgres`、`storageBackend=postgres`、`worker=idle`、`activeTaskId=null`、`inference=idle`。
- 生产 `.env.production` 已在备份后安全补齐本地验证过的 8 项 `YOUYUN_*` 配置；只重建应用 backend，未操作 PostgreSQL、MinIO、Redis、Nginx 或其他基础设施容器。
- 生产 `GET /api/youyun/gpu-specs` 返回 HTTP 200，4090 规格 1 项且可售；生产任务列表无运行或排队任务。
- Control Center 正式入口可打开 CPPT Platform；微调任务页连接生产训练服务并显示 7 条历史任务，新建任务页显示“远程优云 GPU / 一次性实例 · 按量计费”。

## 2026-08-23：优云资源选择与推理边界补充（v3.0.9 待发布）

- 优云训练表单收敛为手动资源配置：GPU 型号、1 卡、CPU 核数和内存均由用户选择；多卡暂不支持。
- 后端通过 `GET /api/youyun/gpu-specs` 查询实时库存，通过 `POST /api/youyun/price` 查询当前按量价格，并在创建任务时再次校验库存与价格对应的规格，避免页面价格与实际创建规格脱节。
- 当前“自动配置”不再作为用户入口。后续若要恢复，应根据数据集规模、序列长度、训练方法和显存需求做推荐，而不是只选择最低成本规格。
- 本轮工作区还包含优云 Provider 的价格解析、选定规格传递、任务存储和回归测试改动，待统一提交发布。
- 推理服务已支持本地 vLLM 与优云远程 Provider：优云服务创建一次性 GPU 实例，通过 SSH 校验或安装远端 vLLM、上传 Adapter、启动私有 vLLM，并在成功、失败、停止或超时路径清理实例。远端 Python 可由 `XUNTUI_REMOTE_VLLM_PYTHON` 指定；镜像缺少 vLLM 时由 `XUNTUI_REMOTE_VLLM_INSTALL` 与 `XUNTUI_REMOTE_VLLM_PACKAGE` 控制安装。远端进程提前退出会立即进入失败清理，不再等待完整健康检查超时。公网路由仍不由远程 Provider 直接提供，服务 API 与聊天请求由后端代理。
## 2026-09-04 NIP 统一控制台增量

- 已按线上 NIP 实测值增加统一壳最终尺寸层：侧栏 216px、导航 42px/16px 文本、18px Lucide 图标、正文 1440px/30px 34px 44px、控件 40px、图标按钮 32px 触控区域；样式仅作用于 `.workspace-reference-skin-shell`。
- 新增 `backend/resource_lifecycle.py` provider-neutral 资源租约状态机，覆盖幂等获取、心跳、释放中、清理失败、释放确认和过期 watchdog；视觉训练、文生图运行时、推理服务运行时已建立租约同步入口。
- API Key 既有生命周期保持：明文只在创建响应出现，状态接口不返回 secret，轮换立即废止旧 Key，日志不记录完整 Key。
- 验证：资源租约测试通过；前端 111 项通过；推理服务相关后端测试通过；TypeScript 和生产构建通过；`git diff --check` 无错误。未启动真实 GPU、vLLM 或优云实例。
- 追加（2026-09-09 00:40）：vsvc-6138db99ec03"长时间未停止"根因分析与紧急处置——该服务实例 cpod-1v0b7ey9kpb3 位于 cn-sh2（5090），09-07 23:13 本地停止时，句柄恢复后用默认地域 cn-wlcb 调 TerminateCompShareInstance，优云报 RetCode=230（Params [UHostId] not available，实例不在该地域）；cleanup_after_task 在错误地域等待/重试无超时上限，stop() 永不返回；后端多次重启杀死挂死协程且启动恢复不处理 orphaned stopping，状态卡"释放中"约 25 小时，实例全程按量计费。紧急处置：用正确地域 cn-sh2/cn-sh2-01 一次调用 Terminate 成功并核验销毁；服务状态直改 stopped（清 remoteRuntime/cleanupStatus + 留痕日志）。同屏发现另一台在跑实例 uhost-1v0bzxx0pvo2（cppt-img-service-igs-95af6751ff55，cn-wlcb 4090，其服务 state=running，09-07 23:17 启动）——属用户有意启动的运行中服务，已向用户报告待决。更正记录：09-08 晨"远端实例总数 0"的核查结论有误（两台实例彼时已存在但未被列出，原因待查，疑 API 异常被静默吞掉），此前"无未释放实例"的结论作废。待办代码修复：① YouyunInferenceHandle 持久化 region/zone，恢复句柄的清理/终止带正确地域；② cleanup_after_task 增加总超时与失败即返回；③ 启动恢复把 orphaned stopping 归一终态并触发释放核验；④ describe_instances 空结果时增加可信度校验（防再次静默漏检）。

- 追加（2026-09-09 01:00）：应用户指令全部释放——停止 igs-95af6751ff55（文生图推理服务，正常走平台 stop 流程，状态机一致地落 stopped、句柄清空），实例 uhost-1v0bzxx0pvo2 随之销毁；双路核验归零：账号级列举 0 台 + 按 ID 探针确认不存在。当前优云零实例、零计费。

- 追加（2026-09-09 01:30）：三 P0 修复上线——① busy_check 语义纠偏：四个运行时（文生图生成/训练/评测、视觉训练）的共享资源互斥由 inference_runtime.active_service_id 改为 local_active（新增 property：仅本地 vLLM 进程占用才为真），远程优云服务不再阻塞其它运行时（此前视觉服务启动挂死会让文生图任务永久 queued，为本次实测任务卡队列的根因）；同时四个 worker 的 busy 等待增加截止（XUNTUI_RESOURCE_WAIT_TIMEOUT_SECONDS 默认 600 秒，超时任务转 failed 带明确报错）；② 推理启动总超时看门狗：start_background 用 asyncio.wait_for 包住（XUNTUI_INFERENCE_START_TIMEOUT_SECONDS 默认 1500 秒，下限 1 秒），超时经 stop(failed=True, source=start_watchdog) 释放——任何未知挂死变成有界失败；③ 优云句柄跨地域：YouyunInferenceHandle/YouyunDiffusersHandle 增加 region/zone 字段（persisted/restore 往返，构造取自实例），provider.stop/cleanup_persisted 及 diffusers 内部清理全部透传给 manager.cleanup_after_task——根治三起跨地域 RetCode=230 漏实例。回归测试：HandleRegionTests 2 项 + LocalActiveAndWatchdogTests 2 项新增，测试桩 cleanup 签名补 region/zone；受影响套件 test_inference_service/test_youyun_inference/test_youyun_diffusers/test_image_generation_runtime/test_image_generation_executor 共 116 项 + test_image_generation_training_runtime/test_vision_runtime/test_image_generation_service_runtime/test_app_cli 共 94 项全部通过，compileall 通过。已带新密钥与 Apifox CORS 重启部署（backend 200/预检 200）。待办：端到端真实验证（余额 ¥3.34 卡线，等用户充值或授权用余额测试）。

- 追加（2026-09-09 02:00）：P0-B 真正根因为 asyncio.Lock 自锁死锁——inference_service.start() 持 self.lock 后异常处理器调用 self.stop() 也需获取同一把不可重入锁 → 永久死锁 → 服务卡 starting 且实例泄漏。修复：新增 _fail_start() 内联清理方法（直接释放远端实例+写 failed 状态+审计，不获取锁），替换 start() 异常路径的 stop() 调用；_guarded_start 看门狗的超时路径调用的 stop() 不受影响（wait_for 取消后锁已释放）。本轮端到端测试消耗约 ¥0.17（两次创建+手动释放），当前余额待查。
- 追加（2026-09-09 02:30）：死锁修复后端到端验证完全通过——视觉推理服务 4 分钟完成启动（创建实例→环境检查→上传 Adapter→加载模型→健康检查通过，阶段进度全程可视无卡顿），API 真实调用 200 返回模型回答（纯色测试图模型表示看不清，但链路完整：认证→请求→vLLM 推理→回答→token 统计），停止后 15 秒内落 stopped、远端实例 0 台、余额 ¥2.52（本轮消耗 ¥0.17）。同时生成了两份标准训练集（视觉理解 8 条 SFT + 文生图 8 条 caption），均通过平台上传→校验→发布全流程。当前演示资料文件夹共 6 份文件：演示 PPT×3 + 演示资料 MD + 测试提示词 MD + 训练集 ZIP×2。
- 追加（2026-09-10 02:45）：实例泄漏"关机死锁"根因定位与双层防护上线——昨晚 00:32 用户经开始菜单关机（事件日志 1074），后端进程随系统死亡，其时文生图服务 igs-5a293b5ca37d 刚就绪 5 分钟（实例 uhost-1v397qj1h412，00:24 起计费）；空闲回收/停止/一切清理逻辑均在后端进程内，进程死亡后永不执行（此前修复的看门狗只管启动卡死，管不了进程消失），实例无人管理至余额（¥2.69）耗尽被优云自动回收。修复两层：① 启动对账——ImageGenerationServiceRuntime.startup() 新增 running 态分支（对账日志+stop() 释放，超时/失败保留句柄防丢）；InferenceRuntime.startup() 新增 _ghost_reconcile_candidates/_reconcile_ghost_services（除自动恢复目标外的 running/starting/recovering 非 image_generation 服务，remoteRuntime 句柄经 YouyunInferenceHandle.restore+provider.stop 释放后归一 stopped，失败转 failed 保留句柄，无句柄直接归一 stopped；有候选才起后台任务）；② 关机防护——两个运行时 shutdown() 的实例释放均加 wait_for 超时（XUNTUI_INFERENCE_RECONCILE_TIMEOUT_SECONDS，vLLM 侧默认 120s、文生图侧 60s），超时句柄保持持久化留给启动对账；新增 backend/shutdown_guard.py：Windows SetConsoleCtrlHandler 捕获 CTRL_CLOSE/LOGOFF/SHUTDOWN（约 5 秒窗口），只读 SQLite 收集持久化句柄（collect_persisted_handles，去重+非运行态过滤）后同步直调优云 Terminate（单调用 3 秒超时、0 重试、绝不抛出），app.py lifespan 装配 install_shutdown_guard()（XUNTUI_SHUTDOWN_GUARD 可关）。测试：新增/更新 test_image_generation_service_runtime（running 归一 3 例）+ test_inference_service（幽灵对账 2 例）+ 新文件 test_shutdown_guard（句柄收集 2 例）；回归 8 个测试文件 179 项全绿。顺手修复预存在的 test_app_cli overview 断言污染（真实 DB 的任务告警无 source 键导致 KeyError，改 .get("source")，经空 RUNTIME_ROOT 对照实验证明与本次改动无关）。后端已带新代码重启验证（health ok、服务状态 13 stopped/4 created/5 failed、无幽灵）。回滚方式：还原上述 5 个文件的对应 hunk 并重启后端即可，无数据迁移。遗留事实：账户余额 0（泄漏耗尽），演示前需充值；云端实例 TTL 兜底（控制台设置自动释放时间）仍建议配置，属进程外保险。

## 2026-09-14 视觉理解/文生图融合合并（feature/nlp-vision-fusion → master）

- 在保留有云专用生产能力（require_provider 门控、审计日志、运行时能力开关、镜像目录）的前提下，合入视觉理解与文生图两个业务域：数据集（IMAGE_TEXT / 文生图 ZIP 上传）、微调（视觉理解 SFT/DPO、文生图 LoRA）、模型管理（域切换器）、推理服务（视觉推理 + 文生图工作台）。
- 前端保持既有 NLP 页面骨架，通过业务域切换条（NLP / 视觉理解 / 文生图）挂载新模块；NLP 侧页面与交互不变。
- 后端以 master 生产架构为底座：InferenceStore 增补服务 API Key（issue/authenticate/revoke，PostgreSQL 迁移 5）；优云推理 `_upload_tree` 通用化（进度回调 + adapter 文件过滤 + sha256 断点跳过）；新增优云看护任务（超时/心跳超时/清理重试）。
- 平台概览路由（/api/platform/*）服务旧版概览页；数据集媒体与社区镜像校验路由随视觉/文生图域引入。

## XShipAI CPU speech workspace — 2026-11-01

The XShipAI deployment at 23.144.68.140 includes an isolated VoxCPM2 CPU worker. Speech starts with a model list and opens details, generation, tasks, audio assets and API documentation. Four conditioning modes, streaming chunks and bounded batch jobs use the private worker; platform authentication remains the existing gateway/session boundary. For speech changes, consult [the CPU speech runbook](docs/voxcpm2-cpu.md), `backend/speech_contracts.py`, and `lib/speech-api.ts`. Other release notes in this file describe the historical platform deployment, not the XShipAI speech service.
