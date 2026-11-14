# 训推平台问题与 BUG 审计报告

> 审计日期：2026-09-04（Asia/Shanghai）  
> 审计对象：Xuntui 训推平台（Next.js 前端、FastAPI 后端、PostgreSQL/SQLite/MinIO、优云 GPU 训练与推理链路）  
> 结论性质：代码、配置、运行态和测试基线审计，不等同于生产环境全量验收。

## 1. 执行摘要

本次审计覆盖数据集管理、视觉理解训练与推理、文生图训练与推理、模型资产、任务队列、优云实例、SSH/远程安装、数据库、对象存储、认证、前端构建和部署配置。

当前本地服务健康检查通过：后端 `http://127.0.0.1:8010/api/health` 返回正常，存储后端为 PostgreSQL，PostgreSQL pool ready，worker、visionTraining、inference 均为 idle；当前优云实例巡检数量为 **0**，未发现正在计费的优云实例。

测试基线通过：后端 `598 tests` 全部通过；前端 `npm.cmd run test:frontend` 共 `102 tests` 全部通过；`npx.cmd tsc --noEmit` 和 `npm.cmd run build` 通过。`npm test` 失败是因为 `package.json` 没有 `test` script，属于命令契约问题，不是测试用例失败。

但代码和运行记录仍存在阻断级及高风险缺陷。最近两次视觉训练失败的直接原因是优云创建实例接口返回 `RetCode=226645, Service unavailable`，不是基座模型、数据集或镜像本身；历史失败任务曾保留 `cleanupState=pending`、`resourceState=provisioning`，造成资源状态误导。该无实例句柄的状态残留已在本轮修复并增加回归测试；历史日志还显示远程依赖安装链路出现 `python-path` 缺失、`bash: -m: command not found`、`install-dead` 和版本漂移问题。

**重要边界：** 本报告没有声称“所有问题已经修复”。当前验证证明的是本地服务和现有测试基线可用；优云上游可用性、真实 GPU 训练、真实 vLLM 启动、对象存储故障恢复和生产网关安全仍需独立验收。

## 2. 审计范围与方法

### 2.1 范围

- 前端：`app/`、`components/`、`lib/`、Next.js 代理和构建配置。
- 后端：`backend/app.py`、数据集、模型、训练、视觉理解、文生图、推理服务和运行时模块。
- 持久化：SQLite、PostgreSQL、MinIO、任务 JSON 运行态和日志/指标文件。
- 云资源：`backend/providers/youyun/` 中 API、实例管理、SSH、镜像/模型探针、远程训练和清理。
- 部署：`README.md`、`CURRENT_STATE.md`、`ARCHITECTURE.md`、`.env*`、`compose.production.yml`、`.drone.yml`。
- 验证：健康检查、静态代码阅读、现有后端/前端测试、TypeScript 检查和生产构建。

### 2.2 方法

1. 先读取仓库说明和当前状态文档，建立模块边界。
2. 对关键 API、状态机、存储写入和资源清理路径做源码追踪。
3. 对最近任务 JSON 和日志做运行态比对。
4. 执行不需要 GPU/优云实例的本地测试、类型检查和构建。
5. 不启动 GPU 训练、不启动 vLLM、不创建云实例，避免审计产生额外费用。

## 3. 严重级别定义

| 级别 | 含义 | 处理要求 |
|---|---|---|
| P0 | 数据丢失、错误计费、权限绕过、资源无法清理或主流程阻断 | 立即修复并增加回归测试；发布前必须验收 |
| P1 | 高概率导致训练/推理失败、状态错误、数据不一致或安全风险 | 近期版本修复；必须有监控和失败补偿 |
| P2 | 功能缺口、维护性问题、运维体验差或指标不完整 | 纳入迭代计划，避免继续扩散 |

## 4. P0 阻断问题

### P0-01 数据集删除没有状态和任务引用保护

- **模块/证据：** 数据集存储与 API；`backend/store.py:657-665`、`backend/postgres_storage.py:601-609`、`backend/app.py:3567-3579`。
- **现象：** 当前只禁止删除 builtin 数据集；已发布、被训练任务引用或被推理评测引用的数据集仍可删除。
- **根因：** 任务只保存 dataset ID，缺少数据库外键/引用模型和删除前状态检查；记录删除与文件删除分为两个动作。
- **影响：** 任务无法复现、历史版本失联、训练中途读取失败；文件删除失败时还会出现元数据与文件不一致。
- **复现条件：** 创建任务引用数据集后直接调用删除接口，或在对象存储删除阶段制造网络/权限错误。
- **当前状态：** 未修复；本地测试通过不能证明该保护存在。
- **修复建议：** 删除前拒绝 `training/running/published` 或存在活动引用的数据集；改为软删除；引入 dataset version 引用表和外键；文件清理由独立 outbox/补偿队列处理。
- **验收标准：** 被任务引用的数据集删除返回明确 `409`；软删除不影响历史任务读取；对象删除失败可重试且有告警；并发删除/训练有一致结果。

### P0-02 IMAGE_TEXT 复用了文本数据集校验、修复和克隆接口

- **模块/证据：** `backend/app.py:3381-3389`、`3392-3415`、`3418-3438`、`3441-3494`、`3497-3511`、`3529-3564`。
- **现象：** 视觉理解和文生图数据集重新校验、修复、克隆时走 `validate_text_dataset_report` 等文本路径。
- **根因：** 数据集类型分支不完整，图片引用、图片目录、Caption、对话消息和多模态 schema 没有独立处理器。
- **影响：** 图片路径信息可能丢失；合法多模态样本被误报；修复操作可能生成不可训练数据；用户无法依据校验结果信任上传内容。
- **复现条件：** 对 `IMAGE_TEXT` 数据集执行校验、修复或克隆接口。
- **当前状态：** 未修复；已有测试覆盖文本和部分视觉路径，但没有覆盖所有复用接口的文件级断言。
- **修复建议：** 按 `TEXT`、`IMAGE_TEXT`、`IMAGE_CAPTION`、DPO 等 schema 注册独立 validator/repair/clone handler；统一输出错误码、行号、图片相对路径和修复建议。
- **验收标准：** 视觉/图片数据集的校验、修复、克隆全链路保留图片哈希和相对路径；非法图片、空 Caption、缺文件、重复文件名均能定位到样本和行号。

### P0-03 优云失败任务的清理状态可能永久残留

- **模块/证据：** `.scratch/local-runtime/vision-tasks/vt-f84476d9ddd9.json`、`vt-b8a228767530.json`。
- **现象：** 任务已 `state=failed`、`executionStatus=failed`，但仍为 `cleanupState=pending`、`resourceState=provisioning`，没有实例 ID。
- **根因：** 实例创建在返回资源 ID 前失败时，失败状态落库与清理状态更新不是同一事务；没有独立 reconciliation 扫描将“无实例 ID 的 provisioning”归零。
- **影响：** 前端误显示仍有资源；重试、删除和费用判断可能被阻塞；后续清理逻辑可能反复等待不存在的实例。
- **复现条件：** `CreateCompShareInstance` 返回 `226645` 等服务端错误，或创建响应丢失后进程退出。
- **当前状态：** 当前实际优云实例数为 0；新失败路径已在没有实例 ID 时写入 `cleanupState=released`、`resourceState=released`，运行时读取历史 `instance_provision` 失败且无实例标记的任务也会自动归一；存在实例 ID 时仍走真实删除核验。
- **修复建议：** 已完成无实例句柄的终态落库和历史状态归一；后续仍需增加持久化清理队列和定时 reconciliation，清理必须可重复执行。
- **验收标准：** 任何创建失败任务在 60 秒内进入终态；无实例 ID 不得保持 provisioning；巡检结果和前端状态一致；清理失败有告警和重试上限。本轮已用失败注入测试覆盖 226645 路径。

补充：2026-09-05 最新任务 `vt-1f45330b834f` 的训练、验证和产物回传均成功；清理阶段收到“实例 `<ID>` 不存在”，已修复为幂等释放成功并将任务纠正为 completed/released。

### P0-04 本地启动与旧进程管理不可靠

- **模块/证据：** `README.md:102`、`CURRENT_STATE.md:237-259`。
- **现象：** 历史出现旧 backend/Next 进程、端口可连但 API 路由不存在、前端代理 `ECONNREFUSED`，用户表现为“网站打不开”或页面连接旧版本。
- **根因：** 启动脚本没有统一的 PID/健康检查/版本标识和 stale process 清理；前后端端口、代理和环境变量存在多套约定。
- **影响：** 测试环境不可重复，用户难以确认实际运行代码；错误排查会误判为业务 BUG。
- **复现条件：** 重复启动、异常退出后再次启动、切换 `.env` 或 Next 构建产物未清理。
- **当前状态：** 当前后端健康，但运维风险仍在。
- **修复建议：** 启动器记录 PID、commit/build ID 和端口；启动前执行端口归属检查；健康检查必须包含 API 版本和数据库状态；前端代理只从单一配置读取 API 地址。
- **验收标准：** 重启 3 次无旧进程残留；访问首页和 `/api/health` 均指向同一 build ID；端口冲突给出明确错误而不是静默覆盖。

## 5. P1 高风险问题

### P1-01 SQLite、PostgreSQL、MinIO 处于混合存储状态

- **模块/证据：** `backend/app.py:2181-2239`、`backend/postgres_storage.py:440`。
- **现象：** 部分任务/数据集/推理数据进入 PostgreSQL，模型管理仍通过 SQLite `__getattr__` 代理；文件对象在 MinIO。
- **根因：** 存储迁移未完成，读写边界和一致性策略不统一。
- **影响：** 页面、任务、模型资产和数据集来源可能不一致；连接池耗尽时接口表现不一致，部分接口降级、部分直接失败。
- **复现条件：** PostgreSQL 短暂不可用、进程重启或同一资源在两套存储中更新。
- **当前状态：** 当前 `storageBackend=postgres`，但混合代码仍存在。
- **修复建议：** 明确单一事实源；完成模型资产和任务数据迁移；禁止隐式 `__getattr__` 跨库代理；为降级策略定义可读/不可写接口矩阵。
- **验收标准：** 同一资源在 API、列表、详情、任务回放中来源一致；数据库故障时返回统一可识别状态；迁移前后哈希和计数一致。

### P1-02 PostgreSQL 版本历史不具备不可变语义

- **模块/证据：** `backend/postgres_storage.py:515-571`；`backend/store.py:627-645`。
- **现象：** `upsert_dataset` 在现有版本上更新，`ON CONFLICT(dataset_id, version)` 不能天然形成追加式版本；SQLite 的 `list_dataset_versions` 基本只返回当前记录。
- **根因：** 版本号没有强制递增策略，版本记录与当前指针没有拆分。
- **影响：** 数据集训练不可审计，历史任务可能无法复原当时的文件和校验报告。
- **修复建议：** 使用不可变 `dataset_versions` 表，版本由服务端事务生成；当前数据集表仅保存 active version；保存 manifest/hash/validator version。
- **验收标准：** 任意更新都生成新版本；旧版本只读；任务固定到具体 version；删除数据集不删除已引用版本。

### P1-03 MinIO 元数据与对象上传不是原子事务

- **模块/证据：** `backend/model_storage.py:209-243`、`429-435`。
- **现象：** 先写 pending 元数据，再逐文件上传；中途失败会留下 pending/partial 对象。
- **根因：** 没有事务 outbox、完整性 manifest 和孤儿对象扫描。
- **影响：** 模型列表出现无法下载的资产；磁盘占用增长；恢复时无法区分半成品和有效版本。
- **修复建议：** 上传到临时前缀，完成哈希/大小/文件数校验后原子 promote；元数据状态机 `pending -> ready/failed`；定时清理孤儿对象。
- **验收标准：** 任意单文件上传失败不会产生可见 ready 资产；重试可复用已上传分片；扫描能发现并处理孤儿对象。

### P1-04 认证边界信任未签名网关 Header

- **模块/证据：** `backend/app.py:2259-2272`、`2302-2319`、`3953-3966`。
- **现象：** 直接信任 `x-control-user-id` / `x-control-username`。
- **根因：** 后端假定只能被受信网关访问，但没有网络边界或签名校验作为代码级保障。
- **影响：** 若 backend 端口被公网暴露，攻击者可伪造用户身份访问管理接口。
- **修复建议：** 后端仅绑定内网；网关注入带时间戳、nonce、签名的身份头；后端校验签名和过期时间；管理接口增加服务间 token。
- **验收标准：** 直接伪造 Header 返回 `401/403`；合法网关请求通过；重放签名失败；生产部署扫描确认 backend 不公开暴露。

### P1-05 session secret 缺失时静默随机生成

- **模块/证据：** `backend/app.py` 中 `SessionManager(SESSION_SECRET or secrets.token_urlsafe(32), ...)`；`backend/auth.py:206-210`。
- **现象：** 未配置固定 secret 时服务仍启动，每次重启 session 全失效，多进程间认证不一致。
- **根因：** 将生产配置错误当作可恢复默认值。
- **影响：** 用户被随机登出；横向扩容时请求可能交替认证失败；安全审计无法确认密钥生命周期。
- **修复建议：** 生产环境缺失 secret 直接拒绝启动；开发环境才允许明确标记的临时 secret；提供 secret rotation 兼容窗口。
- **验收标准：** 生产模式无 secret 启动失败；同一 secret 多进程 session 一致；轮换期间旧 session 按策略失效。

### P1-06 SSH 使用 AutoAddPolicy，未校验主机密钥

- **模块/证据：** `backend/providers/youyun/ssh.py:52-66`。
- **现象：** Paramiko 自动接受首次见到的 host key。
- **根因：** 连接成功优先，没有受信 host key 或固定指纹配置。
- **影响：** 存在中间人攻击风险，尤其是训练数据、模型权重和命令在公网 SSH 链路上传输时。
- **修复建议：** 使用受信 known_hosts 或优云实例指纹；密钥不匹配立即失败；日志记录指纹但不记录私钥。
- **验收标准：** 未知/变更指纹连接失败；合法实例指纹通过；测试覆盖首次连接和轮换场景。

### P1-07 优云业务错误重试策略不足

- **模块/证据：** `backend/providers/youyun/client.py:208-239`、`instance_manager.py:101-139`。
- **现象：** `_post` 主要只对网络异常重试，业务 `RetCode=226645/8433` 通常直接失败；实例管理虽有限重试，仍受上游持续不可用影响。
- **根因：** 没有按错误码分类的退避、熔断、幂等和资源状态补偿。
- **影响：** 上游瞬时不可用时任务快速失败；重复创建风险和用户等待时间增加。
- **修复建议：** 对 `226645` 等明确可重试码采用指数退避+抖动和总时限；对不可重试码立即失败；使用幂等键；达到阈值后熔断并提示维护窗口。
- **验收标准：** 同一任务最多创建一个实例；可重试错误在预算内退避；熔断期间不继续计费请求；所有重试可观测。

### P1-08 远程依赖安装链路复杂且版本漂移

- **模块/证据：** `backend/providers/youyun/remote_training.py`；历史日志出现 `python-path` 缺失、`bash: -m: command not found`、`install-dead`，以及 Python/Torch/Transformers/TRL 等版本漂移。
- **现象：** 依赖安装进程结束但没有完成标记；检测到 Python 路径却被错误解析；安装时间长且结果依赖实时 PyPI。
- **根因：** 安装、探针、完成标记和训练启动由多段 shell/nohup/setsid 链接；镜像没有锁定完整依赖闭包。
- **影响：** 实例已计费但训练未开始；失败原因难以定位；同一配置在不同实例结果不一致。
- **修复建议：** 优先使用预装且版本锁定的社区镜像；启动前执行单命令环境探针；Python 路径通过 `command -v python` 和绝对路径校验；安装过程写入退出码、stderr 和原子完成标记。
- **验收标准：** 探针在 30 秒内返回 Python/Torch/CUDA/LLaMA-Factory 版本；依赖安装成功率和耗时可统计；失败必有最后 200 行 stderr；禁止把安装日志列表误当作 python-path。

### P1-09 SSH 失败与实例释放存在时序风险

- **模块/证据：** `remote_training.py` 的 shield、callback、cleanup/watchdog 路径。
- **现象：** SSH 超时、握手失败、进程退出与清理可能同时发生；实例回调落库失败时留下 `cleanup_failed/pending` 标记。
- **根因：** 清理依赖单进程内回调，缺少持久化 outbox 和独立 reconciliation worker。
- **影响：** 训练失败后资源可能继续计费，或前端误报未释放。
- **修复建议：** 实例 ID/创建请求/任务 ID建立唯一关联；清理命令幂等；清理事件写 outbox；独立 worker 周期扫描 `provisioning/running/cleanup_pending`。
- **验收标准：** TCP 超时、握手失败、远程进程崩溃、服务重启四类故障均能释放；无实例 ID 的任务自动归零；巡检和账单接口一致。

### P1-10 推理服务启动可能同步等待 vLLM 完整加载

- **模块/证据：** `CURRENT_STATE.md:273` 及相关推理启动路径。
- **现象：** 大模型加载期间请求超时，前端只显示笼统的启动中/失败。
- **根因：** 部分启动路径仍把进程创建和 ready probe 合并为同步请求。
- **影响：** 用户重复点击启动；服务被误判失败；GPU 已占用但没有可用接口。
- **修复建议：** 启动 API 立即返回任务 ID；状态细分 `provisioning/loading/ready/degraded/stopping`; readiness probe 与 liveness probe 分离；前端展示预计阶段和最近日志。
- **验收标准：** 启动请求在 2 秒内返回；加载期间不会阻塞 API worker；ready 只在模型接口实测成功后置位；超时自动释放并保留诊断信息。

### P1-11 vLLM 推理详情仍有 prototype/mock 指标

- **模块/证据：** `CURRENT_STATE.md:274`。
- **现象：** 部分日志、运行指标、KV Cache 展示不是完全 API-backed，页面可能显示“成功”但指标不完整。
- **根因：** UI 先于真实运行指标接口落地，存在 mock/prototype 残留。
- **影响：** 运维人员依据错误指标判断吞吐、显存和服务健康。
- **修复建议：** 所有指标标注来源和采样时间；无真实值显示“暂无数据”而不是 0；删除活跃页面对 mock-data 的引用。
- **验收标准：** 页面每个指标都能追溯到服务 API/Prometheus；服务停止后指标状态变为 stale；测试断言不存在 mock 响应。

### P1-12 视觉评测指标过于简化

- **模块/证据：** `backend/vision_api.py:302-317`。
- **现象：** 主要使用 token F1/Exact Match，无法全面衡量图像理解、OCR、定位、医学报告解读等质量。
- **根因：** 缺少按任务类型划分的可复现评测集和人工/模型裁判协议。
- **影响：** 微调前后质量差异可能被误判；DPO 无持久化 win-rate 等真实质量基准。
- **修复建议：** 按 OCR、Caption、VQA、结构化抽取、拒答安全建立指标；保存样本级预测、评测版本、模型 hash；DPO 持久化 win-rate、平均 margin 和置信区间。
- **验收标准：** 每种任务有固定验证集、阈值和回归报告；同一模型重复评测结果可复现；质量未达标禁止发布。

### P1-13 远程模型路径/Repo ID 严格但前端体验仍易误导

- **模块/证据：** `backend/vision_api.py:990-1061`、`backend/providers/youyun/remote_training.py:733-833`。
- **现象：** 优云模型必须存在于远端共享目录且完整；前端可能允许先选模型，提交后才发现路径不可用或需重新下载。
- **根因：** 候选模型、可用模型、探针已确认模型三种状态没有统一为可提交状态。
- **影响：** 训练先创建实例再失败，增加等待和费用；用户误以为公共模型库已自动可用。
- **修复建议：** 提交前强制远端 `path + config + tokenizer + processor` 探针；模型状态显示 `catalog/candidate/verified/missing`; 缺失时禁止提交或明确选择下载策略。
- **验收标准：** 未 verified 模型不能进入队列；错误提示包含缺失文件和预计下载成本；公共模型库路径与训练配置完全一致。

## 6. P2 一般缺陷与维护问题

### P2-01 loss 图缺少最佳点、平滑和过拟合提示

- **证据：** `CURRENT_STATE.md:291`。
- **影响：** 用户只能看原始曲线，无法判断最佳 checkpoint、训练是否过拟合或何时提前停止。
- **建议/验收：** 展示 train/eval loss、最佳点、滑动平均、相邻评估差值和 early-stopping 建议；无 eval 数据时明确标注。

### P2-02 “打开产物目录”依赖 Windows Explorer

- **证据：** `CURRENT_STATE.md:292`。
- **影响：** Linux/Docker/远程部署不可用。
- **建议/验收：** 抽象为下载、对象存储浏览或服务端打包；非 Windows 环境不显示无效按钮。

### P2-03 API 示例与真实接口不一致

- **证据：** `CURRENT_STATE.md:275`。
- **现象：** 文档示例直接调用 `/v1/chat/completions`，实现是 `/api/inference/services/{id}/chat`。
- **建议/验收：** 统一 OpenAPI 示例、README、前端 API client 和 curl；示例必须可在测试环境运行。

### P2-04 API 地址和环境变量存在历史硬编码/多套配置

- **证据：** `lib/api-config.ts`、`README.md:102`、`.env.example`。
- **影响：** 前端可能连接旧 backend，产生页面打不开或接口 404。
- **建议/验收：** 只保留一个公开 API base URL 配置；启动时打印非敏感配置摘要和 build ID。

### P2-05 `lib/mock-data.ts`（已解决）

- **原影响：** 维护者可能误将 mock 接入新组件，测试与生产行为分叉。
- **处理：** 2026-09-04 删除 `lib/mock-data.ts`；全仓业务、测试和脚本无引用，测试 fixture 保持在各自测试文件中。
- **验收：** 活跃页面继续只读取 FastAPI；引用扫描不再发现该模块的运行时 import。

### P2-06 `/api/datasets/import` 后端残留

- **证据：** `backend/app.py:3325-3346`。
- **现象：** 产品约定已删除导入入口，但后端接口仍保留。
- **影响：** API surface 与 UI 约定不一致，增加未审计的上传路径。
- **建议/验收：** 确认无兼容调用后下线或加弃用头；新建数据集统一走同一校验流程。

### P2-07 `backend/app.py` 存在重复 import

- **影响：** 代码卫生问题，增加模块维护噪声。
- **建议/验收：** lint 规则阻止重复 import，CI 纳入 `ruff`/等价检查。

### P2-08 没有 `npm test` script

- **证据：** `package.json`。
- **现象：** `npm test` 直接失败；实际前端测试命令为 `npm run test:frontend`。
- **建议/验收：** 增加 `test` script 指向前端测试，或在 README/CI 中统一命令；开发者执行 `npm test` 应得到有效结果。

### P2-09 日志和指标容量策略粗糙

- **现象：** 视觉/图片任务通过 JSON 文件保留数千条日志/指标，没有分页、压缩和生命周期清理。
- **影响：** 长任务文件膨胀，读写变慢，前端一次加载过多内容。
- **建议/验收：** 日志按任务分片/分页，指标按时间序列存储；保留周期、压缩和最大文件大小可配置。

### P2-10 进程内队列和 worker 不支持横向扩展

- **现象：** 视觉、文生图、文本训练仍有进程内队列/锁。
- **影响：** 进程崩溃丢队列；多副本可能重复执行；无法保证 exactly-once。
- **建议/验收：** 迁移到 Redis/PostgreSQL-backed queue；任务状态使用幂等状态机；重复 worker 只允许一个 lease 持有者。

### P2-11 任务与数据集缺少数据库外键和引用模型

- **证据：** `CURRENT_STATE.md:271`。
- **影响：** 数据集删除、版本漂移和审计追踪无法在数据库层阻止。
- **建议/验收：** 训练/评测任务引用 dataset version；外键限制删除；详情页显示不可变版本和 manifest hash。

### P2-12 合并模型/产物语义和容量控制不足

- **证据：** `CURRENT_STATE.md:270`。
- **现象：** metrics 文件不是权重；磁盘/GPU 容量检查不够强。
- **影响：** 用户误以为已有可部署模型；训练过程中可能因空间或显存不足失败。
- **建议/验收：** 产物类型显式区分 adapter/merged/full checkpoint/metrics；提交前估算磁盘、显存和临时空间并设硬阈值。

### P2-13 生产部署依赖人工 secrets 和外部服务准备

- **证据：** `.drone.yml`、`compose.production.yml`、`.env*`。
- **影响：** CI 的配置格式和容器健康不能替代真实 PostgreSQL、MinIO、网关认证和优云验收。
- **建议/验收：** 增加 staging smoke test、secret presence check、对象存储读写探针、数据库迁移检查和回滚演练。

## 7. 最近优云失败案例

### 7.1 2026-09-04 视觉 SFT/DPO 两次失败

- 任务 `vt-b8a228767530`：17:00:18 创建，17:03:10 失败。
- 任务 `vt-f84476d9ddd9`：17:11:41 创建，17:24:54 失败。
- 两次均在 `phase=instance_provision` 失败，错误为 `CreateCompShareInstance RetCode=226645: Service unavailable`。
- 两次均没有实例 ID，任务文件却保留 `cleanupState=pending` 和 `resourceState=provisioning`。
- 结论：直接故障属于优云实例创建服务不可用；本地清理状态机存在残留缺陷。当前巡检实例数为 0，因此本次未发现仍在计费的实例。

### 7.2 历史远程安装失败模式

- `cat: /root/cppt/python-path: No such file or directory`。
- `bash: line 1: -m: command not found`。
- `dependency_python_path_missing`：已安装依赖但没有生成可用 python-path。
- `dependency_install_failed`：出现 `install-started ... install-dead`，没有完成标记。
- 依赖版本随实时镜像/PyPI 变化，安装时间和结果不稳定。

### 7.3 SSH 失败模式

- 首次 TCP 连接超时、SSH banner 读取失败、随后重试成功的情况均出现过。
- 当前实例释放逻辑对部分路径有效，但必须用独立 reconciliation 验证“失败任务、无实例 ID、实例已释放”三者最终一致。

## 8. 已修复或当前已验证正常，但容易回归的部分

- 本地后端健康检查、PostgreSQL pool、worker/visionTraining/inference idle 状态当前正常。
- 视觉和文生图相关现有测试、前端测试、TypeScript 和生产构建当前通过。
- 优云实例巡检当前为 0，未发现遗留实例。
- 实例管理已包含部分错误码重试、SSH watchdog、cleanup/shield 保护；这些属于已有防线，不代表所有异常时序都已覆盖。
- 视觉训练已增加阶段化错误码和远端基座模型探针；仍需用故障注入验证服务不可用、依赖安装中断和 SSH 失败。

## 9. 架构性风险

1. **状态机分散：** 任务 JSON、数据库记录、前端状态和优云真实状态没有统一事件源。
2. **持久化边界混乱：** SQLite/PostgreSQL/MinIO/本地文件同时承担事实源、缓存和产物存储。
3. **资源生命周期不闭环：** 创建、训练、停止、释放、对账依赖同一进程的回调，缺少独立控制平面。
4. **配置漂移：** 镜像、Python、Torch、Transformers、TRL、模型路径和前端 API 地址均可能来自不同来源。
5. **质量门禁不足：** 训练完成更多表示进程结束，不一定表示验证集达标、产物完整或可部署。

## 10. 测试与运维缺口

- 没有对优云 `226645/8433`、实例创建响应丢失、重复创建和清理回调丢失做端到端故障注入。
- 没有真实 GPU staging 验收矩阵：预装镜像、公共模型库路径、QLoRA、SFT/DPO、验证集、产物上传、推理启动和释放。
- 没有对 MinIO 半上传、PostgreSQL 连接耗尽、服务重启恢复、队列重复消费做灾备测试。
- 没有统一 `npm test` 入口和 API contract test。
- 没有对认证 Header 伪造、SSH host key 变更、session secret 缺失做安全回归。

## 11. 优先级修复路线

### 阶段一：立即止血（P0，1 个版本内）

1. 修复数据集删除保护和 dataset version 引用。
2. 为 IMAGE_TEXT 建立独立校验/修复/克隆处理器。
3. 把无实例 ID 的失败任务自动归零，建立清理 outbox 和 reconciliation worker。
4. 统一本地启动器、端口检查、健康检查和 build ID。
5. 生产环境强制 `SESSION_SECRET`，限制 backend 网络暴露。

### 阶段二：稳定训练与推理（P1）

1. 发布锁定依赖的视觉/文生图预装镜像，启动前只做环境探针。
2. 对优云业务错误码实施分类重试、退避、熔断和幂等请求。
3. 统一 SSH known_hosts/指纹校验。
4. 将推理启动改为异步状态机，ready 由真实接口探针确认。
5. 将模型、数据集、任务、产物全部绑定不可变 version/manifest/hash。

### 阶段三：一致性与可观测性（P2）

1. 完成 PostgreSQL 单一事实源迁移，收敛 SQLite 代理。
2. MinIO 采用临时前缀+原子 promote+孤儿扫描。
3. 队列迁移为持久化 lease 模式，支持多副本和幂等执行。
4. 统一日志、指标、事件和告警；按任务分页和生命周期清理。
5. 建立视觉/文生图按任务类型的验证集和发布质量门禁。

## 12. 近期可执行任务清单

- [ ] 增加 `dataset_references` 和不可变 `dataset_versions` 表。
- [ ] 增加 `IMAGE_TEXT` validator/repair/clone 单元测试及接口测试。
- [ ] 增加 `cleanup_reconciliation` 定时任务和清理状态转移测试。
- [ ] 为优云 client 增加错误码分类测试：重试、不可重试、熔断、幂等。
- [ ] 增加远程环境探针脚本，输出 Python、Torch、CUDA、LLaMA-Factory、模型路径和磁盘空间。
- [ ] 替换 SSH `AutoAddPolicy`，增加 host key fixture。
- [ ] 增加推理 `provisioning/loading/ready/degraded/stopping` 状态和前端回归测试。
- [ ] 增加 `npm test` script，并在 CI 中统一调用。
- [ ] 清理活跃页面对 mock 数据的引用。
- [ ] 建立 staging 优云验收脚本，成功/失败都验证实例最终释放。

## 13. 验收清单

### 数据与存储

- [ ] 数据集删除会检查状态、任务引用和版本引用。
- [ ] 视觉/图片数据集校验能报告行号、图片路径、尺寸、Caption 和哈希。
- [ ] 数据集版本不可变，历史任务可复现。
- [ ] MinIO 半上传、重试、孤儿对象均有处理结果。

### 训练

- [ ] 模型路径在创建实例前已远端验证。
- [ ] 镜像依赖版本锁定，环境探针成功后才进入训练。
- [ ] SFT/DPO/QLoRA 训练均能读取训练集和验证集，并持久化 loss/metrics/产物状态。
- [ ] 优云服务不可用、SSH 超时、安装失败、训练崩溃都能自动释放实例。

### 推理

- [ ] 启动 API 不同步等待完整加载。
- [ ] ready 状态由真实推理请求确认。
- [ ] 日志和指标均来自真实 API，不能用 mock 值冒充。
- [ ] 停止、超时和异常退出后资源状态最终归零。

### 安全与部署

- [ ] backend 不接受伪造身份 Header。
- [ ] 生产缺失 session secret 时拒绝启动。
- [ ] SSH host key 变更会失败并告警。
- [ ] staging smoke test 覆盖 PostgreSQL、MinIO、网关和优云清理。

## 14. 明确不应宣称已解决的事项

以下结论目前没有足够证据，不应在页面、简历、交付说明或验收记录中写成“已彻底解决”：

- 优云实例创建服务持续可用；最近明确存在 `226645 Service unavailable`。
- 视觉理解和文生图真实 GPU 微调已经稳定跑通。
- 远程依赖安装在所有镜像和 Python 版本上稳定。
- 所有训练失败都能自动释放并完成账单对账。
- vLLM 指标、KV Cache 和日志已经全部真实 API 化。
- 数据集删除、版本、MinIO 上传已经具备完整事务一致性。
- 生产认证、SSH 主机密钥和 secret 管理已经完成安全验收。

## 15. 附录：关键文件与验证命令

### 15.1 关键文件索引

- 后端入口与路由：`backend/app.py`
- 数据集/任务 SQLite：`backend/store.py`
- PostgreSQL 存储：`backend/postgres_storage.py`
- 模型对象存储：`backend/model_storage.py`
- 视觉接口与评测：`backend/vision_api.py`
- 优云客户端：`backend/providers/youyun/client.py`
- 优云实例管理：`backend/providers/youyun/instance_manager.py`
- SSH：`backend/providers/youyun/ssh.py`
- 远程训练：`backend/providers/youyun/remote_training.py`
- 当前运行态：`CURRENT_STATE.md`
- 架构说明：`ARCHITECTURE.md`
- 生产编排：`compose.production.yml`
- CI：`.drone.yml`、`.drone.yaml`

### 15.2 已执行验证命令与结果

```text
GET http://127.0.0.1:8010/api/health
结果：healthy；storageBackend=postgres；PostgreSQL pool ready；worker/visionTraining/inference=idle

后端测试
结果：598 passed

npm.cmd run test:frontend
结果：102 passed

npx.cmd tsc --noEmit
结果：通过

npm.cmd run build
结果：通过

npm test
结果：失败，package.json 没有 test script；不是测试用例失败

优云实例巡检
结果：0 个实例
```

### 15.3 审计限制

本报告基于当前工作树、可见本地运行态、仓库内任务记录和用户提供的失败日志。优云控制台实时账单、生产网关、外部 PostgreSQL/MinIO 集群和未提交的外部改动不在本地静态审计的可验证范围内；上线前应按第 13 节执行 staging/production 验收。
