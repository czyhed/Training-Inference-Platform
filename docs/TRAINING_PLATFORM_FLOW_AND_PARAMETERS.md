# 训练平台全流程、参数与数据记录说明

> 审计日期：2026-09-02
> 代码快照：最新发布标签 `v3.0.79`（`b58deae`）；当前分支 `36bc775`，工作区另有未提交推理日志分页改动。
> 阅读目的：先讲清当前系统真实做了什么，再讨论参数和流程如何优化。本文不把规划、旧代码或页面文案当成已经生效的能力。

## 1. 先记住这句话

这个平台把用户上传的 JSONL 训练数据校验并登记起来，用户选择基础模型和训练参数后创建任务；后端把平台参数转换成受控的 LLaMA-Factory YAML，在本地 CPU 或优云一次性 GPU 实例上执行训练，持续保存任务状态、日志和 Loss 指标，最后收集 Adapter 文件，并在共享存储可用时把 Adapter 元数据写入 PostgreSQL、文件上传到 MinIO。

## 2. 面试表达

### 2.1 30 秒版本

我做的是一个文本大模型微调与推理平台，目前支持 SFT、DPO、LoRA 和 QLoRA。用户上传 JSONL 数据后，平台先做格式校验和发布控制，再把网页或受控命令行提交统一转换成任务配置。生产环境用 PostgreSQL 保存任务、日志、指标和数据集元数据，训练文件保存在受控目录，模型文件上传到 MinIO。训练由后端队列调度，本地走 CPU LLaMA-Factory，远程走优云一次性 GPU；训练日志会解析成 Step、训练 Loss、验证 Loss 和学习率，完成后收集 Adapter 和清单文件，再用于效果对比或推理部署。

### 2.2 2 分钟版本

平台主流程分成五层。第一层是数据集管理：上传 SFT 或 DPO 的 JSONL 文件，后端边接收边计算 SHA256，并进行逐行结构校验；通过后才能发布给训练任务使用。第二层是任务配置：网页和命令行最终都会生成同一个结构化任务对象，后端再次校验数据集状态、训练类型和数据结构是否匹配。第三层是训练调度：任务先写入数据库队列，本地任务串行执行，优云任务各自创建一次性 GPU 实例并可并发执行。第四层是训练执行：平台复制一份隔离数据，生成 `dataset_info.json` 和 `train.yaml`，再由服务端固定命令启动 LLaMA-Factory，浏览器不能直接传任意 Shell 命令。第五层是结果管理：后端把训练日志解析为 Step、Loss 和学习率，收集 Final Adapter；有有效验证记录和最佳检查点时再收集 Best Adapter，同时生成文件清单。共享存储开启后，Adapter 的业务元数据进入 PostgreSQL，真实权重文件进入 MinIO。

这个项目当前最值得优化的不是盲目调大学习率或训练轮数，而是继续补齐 DPO 验证集、Best Adapter 语义和有效配置快照，保证用户看到的配置与实际训练一致。LoRA+ 与检查点配置已经完成从页面到执行器的接入。

## 3. 小白术语表

- **SFT（监督微调）**：给模型看“问题和标准答案”，让它学习按示例回答。
- **DPO（直接偏好优化）**：给模型看“更好的回答和更差的回答”，让它学习人的偏好。
- **基础模型（Base Model）**：还没有经过本次项目数据训练的原始模型。
- **LoRA**：只训练模型中少量新增参数，降低训练成本，产物通常是较小的 Adapter。
- **QLoRA**：先用低位数压缩基础模型，再训练 LoRA，从而进一步节省显存。
- **Adapter**：微调后新增的轻量权重，使用时需要和对应的基础模型一起加载。
- **LLaMA-Factory**：负责读取模型、数据和参数并真正执行训练的开源训练框架。
- **YAML**：一种便于人阅读的配置文件格式，本项目生成 `train.yaml` 交给训练框架。
- **API**：前端与后端约定好的请求入口，例如“创建任务”或“读取日志”。
- **PostgreSQL**：生产环境使用的关系型数据库，用表来长期保存业务记录。
- **SQLite**：把数据库保存在单个文件里的轻量方案，本项目用于本地或兼容场景。
- **MinIO**：用于保存模型权重等大文件的对象存储，可以理解为内部文件仓库。
- **Checkpoint（检查点）**：训练中途保存的一份模型状态，可用于选择较好的结果或恢复训练。
- **Loss（损失）**：模型回答和训练目标之间的差距，通常越低越好，但训练 Loss 低不等于真实泛化能力一定更好。
- **Epoch（轮次）**：完整看完一遍训练数据算一轮。
- **Step（步数）**：完成一次参数更新算一步。
- **有效批次大小**：一次参数更新实际综合使用的样本数，当前单卡近似为 `batchSize × gradientAccumulation`。
- **随机种子（Seed）**：固定随机过程的起点，便于重复数据拆分和实验。
- **软删除**：业务页面不再展示记录，但数据库或文件暂时保留，方便追溯。

## 4. 当前系统边界

当前可运行范围：

- 文本生成数据集，文件格式为 JSONL。
- SFT 和 DPO 训练。
- LoRA 和 QLoRA；本地执行器固定为 CPU，因此本地选择 QLoRA 时会退化为普通 LoRA，远程 GPU 才会下发 4-bit 量化。
- 本地 CPU 训练一次只运行一个任务。
- 优云任务各自使用一次性 GPU 实例，可以并发；训练成功后实例会进入等待效果对比状态，人工释放或默认保留窗口到期后才删除。
- 训练日志、Loss、Adapter、效果对比和推理服务管理。

当前不应对外宣称：

- 多卡或分布式训练已经开放。
- 页面上的所有参数都已生效。
- 任务可以从任意 Checkpoint 真正断点续训。
- 没有验证集时一定会保存“训练 Loss 最低”的 Best Adapter。
- 训练完成后一定会生成合并后的完整模型。
- 上传、模型下载、SFTP 回传和 MinIO 上传都有真实逐字节百分比。

## 5. 全流程与每一步记录位置

```text
浏览器上传 JSONL
  -> FastAPI 写入数据集目录并校验
  -> PostgreSQL/SQLite 登记数据集及版本
  -> 用户发布数据集
  -> 网页表单或受控 CLI 创建训练任务
  -> PostgreSQL/SQLite 写入任务 payload，状态 queued
  -> Worker 领取任务
  -> 写入任务 JSON 快照
  -> 隔离复制数据并生成 dataset_info.json、train.yaml
  -> 本地 CPU LLaMA-Factory 或优云 GPU LLaMA-Factory
  -> stdout 日志解析为日志、Step、Loss、学习率和进度
  -> 收集 Final/Best Adapter 与 manifest.json
  -> PostgreSQL 写模型元数据，MinIO 写模型文件
  -> 页面效果对比或创建推理服务
```

### 5.1 数据上传

1. 前端把文件发送到 `POST /api/datasets/upload`。
2. 后端限制为 `.jsonl`，最大 2 GB。
3. 文件写到 `${XUNTUI_DATASET_ROOT}/<dataset-id>/<filename>`。
4. 写入时同步记录文件大小和 SHA256。
5. 后端逐行校验 SFT/DPO 数据结构。
6. 通过后状态为 `ready`，失败为 `failed`；此时还不能训练。

记录位置：

| 内容 | 生产 PostgreSQL | 本地 SQLite | 文件系统 |
|---|---|---|---|
| 数据集名称、类型、结构、状态、样本数 | `datasets` | `datasets` | 无 |
| 版本、来源、大小、SHA256、路径 | `dataset_versions` | 合并在 `datasets` 字段中 | 无 |
| 原始 JSONL 内容 | 不直接存整文件 | 不直接存整文件 | `XUNTUI_DATASET_ROOT/<dataset-id>/...` |
| 创建操作 | `audit_logs` | `audit_logs` | 无 |

### 5.2 校验、修复与发布

- 修复会先生成候选文件并重新校验，不直接悄悄改写原文件。
- 数据集被训练任务引用后，修复会形成新的数据集版本或新记录，保留来源。
- `POST /api/datasets/{id}/publish` 把可用数据集改为 `published`。
- 创建任务时后端再次检查数据集存在、已经发布，并且 SFT/DPO 类型匹配。
- 删除采用软删除，业务记录被隐藏，但文件暂不物理删除，便于来源追溯。

### 5.3 创建训练任务

网页提交使用 `POST /api/tasks`；命令行提交使用 `POST /api/tasks/cli`。命令行文本只会被解析成白名单参数，不会直接交给 Shell 执行。

创建时完整配置被保存到任务的 `payload`，包括：

- 任务名、基础模型、数据集 ID 和数据集版本。
- SFT/DPO、LoRA/QLoRA、本地/优云。
- 全部训练参数、验证参数、输出参数和效果对比参数。
- 优云 GPU、CPU、内存、镜像与价格快照。
- 当前登录用户作为 owner。

记录位置：

| 内容 | 生产 PostgreSQL | 本地 SQLite |
|---|---|---|
| 任务配置和当前状态 | `training_tasks.payload/state/progress/current_step` | `tasks.payload/state/progress/current_step` |
| 第几次运行 | `training_task_attempts` | `training_task_attempts` |
| 状态变化 | `training_task_events` | 当前 SQLite 主表和 attempt 为主，事件能力弱于生产库 |
| 创建/重试/停止/删除操作 | `audit_logs` | `audit_logs` |

任务创建后初始状态为 `queued`。重试不会新建任务 ID，而是新增 attempt，并清空本次旧产物引用后重新排队。

### 5.4 Worker 调度

- 本地队列：一次领取一个本地任务。
- 优云队列：每个任务启动独立异步执行和一次性实例，可并发。
- PostgreSQL 使用 `FOR UPDATE SKIP LOCKED` 领取任务，避免多个 Worker 重复领取同一条记录。
- 后端重启时，失去进程的活动任务会被标记为失败，提示重新运行；当前不是断点续训。
- 重试会复用同一个任务 ID、运行时 JSON 和工作目录，不会为每个 attempt 自动生成不可变的独立文件快照。

### 5.5 生成真实训练配置

Worker 先写一份平台任务快照：

```text
${XUNTUI_RUNTIME_ROOT}/jobs/<task-id>.json
```

执行器再建立隔离工作目录：

```text
${XUNTUI_TRAINING_WORKDIR}/<task-id>/
  dataset/              # 复制后的训练/验证文件
  dataset/dataset_info.json
  train.yaml            # 最终交给 LLaMA-Factory 的配置
  output/               # LLaMA-Factory 输出
  manifest.json         # 产物文件清单和 SHA256
```

真正执行的命令由服务端固定生成：

```text
llamafactory-cli train <workdir>/train.yaml
```

优云路径先在本地生成并校验同一份配置，再把路径改成：

```text
/root/cppt/<task-id>/dataset
/root/cppt/<task-id>/output
```

然后通过 SSH/SFTP 上传数据和 YAML，在远端运行 LLaMA-Factory。远端根据 GPU 型号选择 FP16 或 BF16；未知或老架构默认使用兼容性更高的 FP16。

训练成功后，优云实例不会立刻删除，而是标记为 `awaiting_comparison` 供训练前后效果对比继续使用。用户可以手动释放；若没有操作，当前默认保留 6 小时，由定时清理释放，避免长期计费。

### 5.6 日志、指标和进度

训练框架的标准输出会被逐行读取：

| 数据 | 来源 | 记录位置 |
|---|---|---|
| 原始训练日志 | LLaMA-Factory stdout、后端和远端步骤 | `training_task_logs` / `task_logs` |
| Step、Epoch | Trainer 日志 | `training_task_metrics` / `task_metrics` |
| train Loss、eval Loss | Trainer 日志 | 同上 |
| learning rate | Trainer 日志 | 同上 |
| 当前状态和进度 | 指标事件和 Worker 阶段 | `training_tasks` / `tasks` |
| 运行次数和本次错误 | attempt | `training_task_attempts` |

训练百分比只在获得真实总步数和指标后计算：`min(95, step × 95 / total_steps)`；产物收集成功后才到 100%。页面目前每 2 秒读取一次任务详情，虽然后端有 WebSocket 接口，训练详情尚未使用它。

日志保留规则：成功任务日志保留 7 天，其他状态日志保留 30 天；业务任务摘要、指标、产物元数据不是按这两个期限一起删除。操作审计日志保留 30 天。

### 5.7 Adapter 和模型文件

LLaMA-Factory 的原始输出在：

```text
${XUNTUI_TRAINING_WORKDIR}/<task-id>/output
```

`XUNTUI_ARTIFACT_ROOT` 仍用于兼容路径和受控预览，但当前 LLaMA-Factory 主执行器的真实输出根目录是 `XUNTUI_TRAINING_WORKDIR`，排查产物时应优先看这里。

收集规则以当前代码为准：

- 总会尝试收集 Final Adapter。
- 只有 `trainer_state.json` 同时存在有效 `eval_loss` 和 `best_model_checkpoint` 时，才收集 Best Adapter。
- 没有验证记录时，当前执行器只返回 Final Adapter；它不会根据训练 Loss 复制一个 Best Adapter。
- `manifest.json` 记录任务、模型、数据集、每个输出文件的大小和 SHA256。
- 优云训练先把远端 `/root/cppt/<task-id>/output` 回传到本地镜像目录，再执行同一套收集逻辑。

共享存储开启时：

| 内容 | 保存位置 |
|---|---|
| 每个 Base/Best/Final 逻辑对象 | PostgreSQL `model_files` |
| 每个实际文件的相对路径、大小、MD5、状态 | PostgreSQL `model_file_entries` |
| `.safetensors`、配置和 tokenizer 等真实文件 | MinIO `model/<model-id>/...` |
| 任务详情中的本地 Adapter 路径 | `training_tasks.artifacts` / `tasks.artifacts` |

上传顺序是先创建 PostgreSQL 元数据，再上传 MinIO 文件，最后把状态改为 `available`；失败时保留 `failed` 记录供排查和重试。共享存储不可用时，任务仍可完成，Adapter 暂留本地并写日志说明。

### 5.8 效果对比和推理

- 效果对比参数 `maxNewTokens`、`temperature`、`topP` 和 chat seed 只影响生成回答，不进入训练 YAML。
- 基础模型和 Adapter 使用同一套生成参数，便于做相对公平的对比。
- 当前没有把每次效果对比的回答作为独立评测结果长期入库；主要保存在页面会话状态和接口响应中。
- 创建推理服务时读取已完成任务和明确的 Best Adapter；仅有 Final Adapter 的旧任务可能不能直接部署。
- 优云效果对比依赖训练后暂留的实例；实例释放后不能继续在该实例上对比，重新运行会创建新实例。

## 6. 参数字典

状态含义：

- **生效**：当前主链路会写入 LLaMA-Factory 配置或执行逻辑。
- **条件生效**：只在某种任务或环境下生效。
- **只保存**：数据库和页面能看到，但当前训练器没有使用。
- **独立用途**：用于聊天、资源或业务控制，不属于训练超参数。

### 6.1 训练核心参数

| 平台参数 | 默认值 | 通俗解释 | 影响 | 当前状态 |
|---|---:|---|---|---|
| `epochs` | 3 | 整套数据重复学习几遍 | 太少可能没学会，太多可能过拟合 | 生效，写入 `num_train_epochs` |
| `learningRate` | `2e-4` | 每一步调整参数的幅度 | 太大容易震荡，太小收敛慢 | 生效，写入 `learning_rate` |
| `batchSize` | 1 | 单次放进设备的样本数 | 越大通常越快但更占内存/显存 | 生效，写入 `per_device_train_batch_size` |
| `gradientAccumulation` | 8 | 累积几小批后再更新一次 | 增大有效批次但降低更新频率 | 生效，写入 `gradient_accumulation_steps` |
| `maxSequenceLength` | 1024 | 每条样本最多保留多少 token | 越长越耗内存和计算，截断更少 | 生效，写入 `cutoff_len` |
| `lrScheduler` | `linear` | 学习率随训练进度怎么变化 | 影响前后期收敛节奏 | 生效，写入 `lr_scheduler_type` |
| `warmupRatio` | 0.1 | 前 10% 步数逐渐升高学习率 | 减少训练刚开始时的不稳定 | 生效，写入 `warmup_ratio` |
| `weightDecay` | 0.01 | 对过大的参数施加轻微惩罚 | 可缓解过拟合，过大也会伤效果 | 生效，写入 `weight_decay` |
| `maxGradNorm` | 1 | 限制单次梯度变化的最大幅度 | 防止梯度突然爆炸 | 生效，写入 `max_grad_norm` |
| `seed` | 42 | 固定随机过程起点 | 便于复现实验，但不保证所有硬件完全一致 | 生效，写入 `seed` |
| `loggingSteps` | 10 | 每隔多少步输出一次训练日志 | 越小观察更细，但日志更多 | 生效，写入 `logging_steps` |
| `packing` | false | 把多个短样本拼到一个长序列中 | 可提高吞吐，但需要确认样本边界处理 | 生效，写入 `packing` |
| `gradientCheckpointing` | true | 少存中间结果，需要时重新计算 | 节省内存/显存，但训练更慢 | 生效，写入 `gradient_checkpointing` |
| `optimizer` | `adamw_torch` | 决定如何根据梯度更新参数 | 影响内存、速度和稳定性 | 条件生效；本地 CPU 把 8-bit 选项降级为 `adamw_torch` |
| `maxSteps` | -1 | 是否用固定步数覆盖 Epoch | `-1` 按 Epoch，正数按固定步数 | 生效；`0` 也会写入，业务上应避免 |

当前默认有效批次大小约为 `1 × 8 = 8`。如果以后开放多卡，还需要再乘设备数量。

### 6.2 LoRA/QLoRA 参数

| 参数 | 默认值 | 通俗解释 | 影响 | 当前状态 |
|---|---:|---|---|---|
| `tuningMode` | QLoRA | 选择普通 LoRA 还是量化基础模型后的 LoRA | QLoRA 更省显存，但依赖 GPU 和量化库 | 条件生效；本地 CPU 退化为 LoRA，优云 GPU 下发 4-bit |
| `loraRank` | 16 | Adapter 可学习信息的容量 | 越大容量越强，文件和计算也更大 | 生效，写入 `lora_rank` |
| `loraAlpha` | 16 | LoRA 更新的缩放强度 | 与 rank 一起决定更新幅度 | 生效，写入 `lora_alpha` |
| `loraDropout` | 0 | 训练时随机丢弃部分 LoRA 信号 | 可缓解过拟合，但可能降低学习速度 | 生效，写入 `lora_dropout` |
| `targetModules` | `auto` | 指定模型中的哪些层接受 LoRA | 范围越广，容量和成本越高 | 生效；`auto/attention` 映射 q/k/v/o，`all-linear` 再加 gate/up/down |
| `loraPlusEnabled` / `loraPlusRatio` | 关闭 / 16 | 可选开启 LoRA+，让两组 LoRA 参数使用不同学习率 | 可能改变收敛速度；建议先用普通 LoRA 建立基线 | **条件生效**；开启时写入 `loraplus_lr_ratio` |

### 6.3 验证、检查点和产出参数

| 参数 | 默认值 | 通俗解释 | 当前状态 |
|---|---:|---|---|
| `validationSplit` | 10% | 从训练数据留一部分只用于检查效果 | SFT 单文件上传时生效为 `val_size`；DPO 单文件上传时当前不生效，只有显式 `validationPath` 才验证；当前不要填 1% |
| `validationSteps` | 50 | 每隔多少步做一次验证 | **只保存，执行器明确移除** |
| `checkpointStrategy` | `epoch` | 按轮次还是按步数保存 | **生效**，写入 `save_strategy`；页面提供“每轮保存”或“按步数” |
| `checkpointInterval` | 100 | 按步数保存时，每多少步保存一次 | **条件生效**，按步数时写入 `save_steps` |
| `checkpointLimit` | 2 | 最多保留多少份中间结果 | **生效**，写入 `save_total_limit` |
| `earlyStopping` | false | 指标长期不改善时自动结束 | **只保存，没有接入当前 LLaMA-Factory 回调** |
| `patience` | 3 | 允许连续几次没有改善 | **只保存** |
| `minDelta` | 0 | 多大变化才算真正改善 | **只保存** |
| `mergeModel` | false | 训练后把基础模型和 Adapter 合成完整模型 | **只保存，当前执行器不合并** |
| `outputName` | 必填 | 模型在业务页面和共享存储中的显示名 | 元数据生效；不决定真实训练目录 |
| `extraParameters` | 空 | 给高级用户开放少量白名单训练参数 | 生效，但只允许固定白名单和简单标量，不能覆盖路径或命令 |

### 6.4 数据和任务参数

| 参数 | 解释 | 当前状态 |
|---|---|---|
| `taskType` | SFT 或 DPO | 生效，写入 LLaMA-Factory `stage`，并强制匹配数据集结构 |
| `model` | ModelScope 仓库 ID 或受控本地路径 | 生效，写入 `model_name_or_path` |
| `datasetId` | 绑定已发布的数据集 | 生效，同时保留数据集版本和 SHA256 来源 |
| `provider` | `local` 或 `youyun` | 生效，决定本地 CPU 或远程 GPU 队列 |
| `executionMode` | `local` 或 `remote` | 生效，并与 provider 组合校验 |
| `owner` | 任务负责人 | 由当前登录用户覆盖前端传值，用于追溯 |

### 6.5 优云资源参数

| 参数 | 通俗解释 | 当前状态 |
|---|---|---|
| `gpuType` | GPU 型号 | 提交时根据实时库存再次校验 |
| `gpuCount` | GPU 数量 | 当前固定为 1，多卡不开放 |
| `cpuCores` | 远端实例 CPU 核数 | 必须是实时库存中可用组合 |
| `memoryGb` | 远端实例内存 | 必须是实时库存中可用组合 |
| `zone` | 实例所在可用区 | 必须与服务端配置一致，不能只信浏览器 |
| `imageId` | 远端训练环境镜像 | 页面从官方候选中选择，创建实例时使用 |
| `price` | 当前规格预计小时价格 | 保存快照用于展示，实际费用以优云账单为准 |

### 6.6 效果对比参数

| 参数 | 默认值 | 解释 |
|---|---:|---|
| `maxNewTokens` | 256 | 本次回答最多生成多少新 token |
| `temperature` | 0.2 | 回答随机程度，越低通常越稳定 |
| `topP` | 0.9 | 只从累计概率较高的候选词中采样 |
| chat `seed` | 42 | 尽量固定生成随机性 |

这些参数只用于 `/api/chat`，不改变模型训练结果。

## 7. 参数如何优化

### P0：先保证页面承诺和真实执行一致

1. 已完成：LoRA+、检查点策略/间隔/保留数量已接入执行器并有回归测试。当前仍需继续处理的是 `validationSteps`、早停和模型合并：要么接入执行器并补测试，要么在页面禁用并明确“暂未支持”。
2. 修复 DPO 的验证逻辑。当前上传数据通常只有 `trainPath`，但 DPO 不使用 `validationSplit`；需要真正拆出验证文件或使用 LLaMA-Factory 支持的 DPO 验证配置。
3. 修正文档和页面中“无验证集会按训练 Loss 保存 Best Adapter”的说法。当前代码和测试的真实行为是只选择 Final Adapter。
4. 任务详情的“提交训练时生效的关键参数”目前会展示部分只保存字段，应改成“提交配置”与“最终生效配置”两组。
5. 修复 `validationSplit=1` 的边界错误：API 把它当 1%，转换器却把它当 100%，任务提交成功后会在 Worker 准备阶段失败。
6. 统一显式验证集行为：本地 SFT 可能同时写 `eval_dataset` 和 `val_size`，远程路径会删除冲突的 `val_size`，本地尚未做同样处理。
7. 决定本地停止任务是否应该尽力收集现有 Adapter；当前本地停止后通常直接进入 stopped，不再执行产物收集。

影响：这组优化主要提升正确性和可信度，不一定直接提高模型分数，但面试和生产都最值得先做。

### P1：让实验可复现、可解释

1. 把最终 `train.yaml` 的脱敏快照或字段摘要保存到任务记录，而不只保存在临时工作目录。
2. 保存 LLaMA-Factory、Transformers、PEFT、TRL、CUDA、Torch 和 GPU 型号版本。
3. 保存基础模型的精确 revision/commit，而不是只记录默认 `main`。
4. 在任务详情直接显示有效批次大小、预计总 Step、训练样本数、验证样本数和截断比例。
5. 为每个 attempt 保留独立产物关联，避免重试后旧结果只能靠外部存储追溯。
6. 查询指标时默认限定到当前 attempt；当前生产 PostgreSQL 在未传 `attemptId` 时可能把多次重试的指标一起返回。
7. 共享模型的来源快照应保存完整任务级参数；当前主要保存嵌套 `parameters`，缺少部分验证、检查点和高级参数。
8. 为任务创建和完成补操作审计；当前通用 `audit_logs` 主要记录任务停止、重试和删除，生命周期另由 `training_task_events` 记录。

影响：出现“同一参数为什么结果不同”时，可以从数据、代码、环境和硬件四个方向定位。

### P1：按任务类型提供不同默认预设

当前 SFT 和 DPO 共用 `2e-4` 等默认值，不应假设同一套参数适合两种目标。建议先提供“保守起点”，再通过小规模实验确定：

| 场景 | 建议起点 | 调整依据 |
|---|---|---|
| 小数据 SFT LoRA/QLoRA | Epoch 2-3、有效批次 8、LR 从 `1e-4` 到 `2e-4` 对比 | 验证 Loss、固定题集质量、是否过拟合 |
| DPO | 单独建立更低学习率预设，不直接复用 SFT 默认 | chosen/rejected 差异、验证偏好准确率和回答退化 |
| 长文本 | 先统计 token 长度分布，再定 1024/2048/4096 | 截断比例、显存和吞吐 |
| 小显存 GPU | QLoRA、batch 1、提高梯度累积、启用梯度检查点 | OOM、吞吐和训练稳定性 |
| 大量短样本 | 对比开启/关闭 packing | 每秒样本数和样本边界正确性 |

不要一次同时修改多个关键参数。每轮只改变一个主要变量，固定数据版本、seed 和基础模型 revision，结果才容易解释。

### P2：补齐评测闭环

1. 把“效果对比”从临时聊天升级为评测任务，保存问题集版本、模型版本、生成参数和回答。
2. SFT 至少记录验证 Loss 和固定问题集的人工/规则评分。
3. DPO 增加偏好准确率、奖励差值或项目认可的等价指标，而不只看训练 Loss。
4. Best Adapter 的选择标准应与任务目标一致，并把选择原因写入产物元数据。

影响：训练是否变好不再只靠肉眼看一两条回答。

## 8. 当前已确认的代码与文档差距

| 主题 | 页面或既有文档说法 | 当前代码真实行为 |
|---|---|---|
| 无验证 Best Adapter | 按训练 Loss 选择 Best | `collect_artifacts()` 没有 eval 记录时只收集 Final；测试也断言 Final-only |
| DPO 验证拆分 | 选择 10% 后会随机拆分 | 页面预览会拆，但 DPO 训练 YAML 不写 `val_size`，普通上传也没有 `validationPath` |
| 1% 验证比例 | API 允许 1% | 转换器把数值 1 当作比例 1.0，随后因必须小于 1 而在 Worker 阶段失败 |
| 显式验证文件 | 应优先使用独立验证集 | 本地 SFT 仍可能同时写 `eval_dataset` 和 `val_size`；仅远程路径删除冲突项 |
| 自动早停 | 页面可启用 | payload 保存，但主执行器未使用 |
| 检查点策略/数量 | 页面可设置 | 已映射到 `save_strategy/save_steps/save_total_limit`；当前仍不支持断点续训 |
| LoRA+ | 页面可设置比例 | 默认关闭；开启时映射为 `loraplus_lr_ratio` |
| 合并模型 | 页面可开启 | 当前只收集 Adapter 和 manifest |
| 配置快照标题 | “提交训练时生效的关键参数” | 当前页面已改为“提交时保存的任务配置”，但仍需进一步区分最终 YAML 与只保存字段 |
| 梯度检查点提示 | “适合本地 8GB 显卡” | 当前本地入口是 CPU 服务器，该提示已经过时 |
| MinIO 对象前缀 | 旧文档写 `models/<id>` | 当前上传源码实际使用 `model/<id>` |
| 优云实例释放 | 旧文档称训练结束立即删除 | 当前成功后暂留用于效果对比，默认 6 小时或人工释放 |
| 本地手动停止 | 页面暗示会保存停止产物 | 本地停止路径通常不再调用 Adapter 收集；远程会尽力回传 |

## 9. 实际验证方法

以下检查不会启动 GPU 训练或 vLLM。

### 9.1 查看平台状态

```powershell
Invoke-RestMethod http://127.0.0.1:8010/api/health
```

重点看：`storageBackend`、`queue`、`worker`、`activeTaskId` 和 `artifactRoot`。

### 9.2 查看某个任务保存了什么

```powershell
Invoke-RestMethod http://127.0.0.1:8010/api/tasks/<task-id> | ConvertTo-Json -Depth 20
Invoke-RestMethod http://127.0.0.1:8010/api/tasks/<task-id>/attempts | ConvertTo-Json -Depth 10
Invoke-RestMethod http://127.0.0.1:8010/api/tasks/<task-id>/logs?limit=100 | ConvertTo-Json -Depth 10
Invoke-RestMethod http://127.0.0.1:8010/api/tasks/<task-id>/metrics | ConvertTo-Json -Depth 10
```

检查 `parameters`、`validationSplit`、`artifacts`、`selectionMetric`、Loss 历史和 attempt。

### 9.3 查看真正下发的训练配置

```powershell
Get-Content "$env:XUNTUI_TRAINING_WORKDIR/<task-id>/train.yaml"
Get-Content "$env:XUNTUI_TRAINING_WORKDIR/<task-id>/dataset/dataset_info.json"
Get-Content "$env:XUNTUI_RUNTIME_ROOT/jobs/<task-id>.json"
```

如果环境变量未直接设置，生产示例默认分别是 `/workspace/training-work` 和 `/workspace/runtime`；这些是 Linux 容器或 WSL 内路径。

验证“参数是否真正生效”时，以 `train.yaml` 为最终证据，不要只看网页配置快照。

### 9.4 查看生产 PostgreSQL 记录

```sql
SELECT id, state, progress, current_step, payload, artifacts, created_at, updated_at
FROM training_tasks
WHERE id = '<task-id>';

SELECT attempt_no, state, started_at, finished_at, error
FROM training_task_attempts
WHERE task_id = '<task-id>'
ORDER BY attempt_no;

SELECT step, epoch, train_loss, eval_loss, learning_rate, created_at
FROM training_task_metrics
WHERE task_id = '<task-id>'
ORDER BY step;

SELECT artifact_variant, status, source_task_id, source_dataset_sha256,
       minio_bucket, minio_object_prefix, created_at, uploaded_at
FROM model_files
WHERE source_task_id = '<task-id>'
ORDER BY created_at;
```

### 9.5 查看本地文件

重点确认：

- 数据集文件存在，SHA256 与数据库一致。
- `train.yaml` 中确实出现预期字段。
- `output/adapter_config.json` 存在。
- 有验证时，Best 路径确实包含 `adapter_config.json`。
- `manifest.json` 的文件列表、大小和 SHA256 与实际文件一致。
- MinIO 对象前缀与 `model_files.minio_object_prefix` 一致，当前应为 `model/<model-id>`。

### 9.6 运行不需要 GPU 的聚焦测试

```powershell
wsl.exe -d Ubuntu-24.04 --cd "/mnt/d/训推平台" -- /root/miniconda3/envs/unsloth_env/bin/python -m unittest backend.test_llamafactory backend.test_store backend.test_app_cli backend.test_cli_parser -v
```

这些测试会核对参数转换、受控命令解析、任务存储、指标和 Adapter 选择，不会启动真实 GPU 训练。

## 10. 代码证据索引

- 前端默认参数和表单提交：`components/training-module.tsx:150`、`:678`、`:997`。
- 页面轮询任务详情：`components/training-module.tsx:500`、`:528`。
- API 参数校验和任务创建：`backend/app.py:514`、`:587`、`:3694`。
- 数据上传、SHA256、校验和发布：`backend/app.py:2940`、`:3193`。
- 平台任务快照和 Worker：`backend/app.py:1593`。
- 日志和指标写入：`backend/app.py:1369`。
- 参数折叠及明确移除项：`backend/local_llamafactory_executor.py:86`。
- 隔离工作目录、数据复制、YAML：`backend/local_llamafactory_executor.py:127`。
- Adapter 和 manifest 收集：`backend/local_llamafactory_executor.py:262`。
- LLaMA-Factory 字段映射：`backend/llamafactory.py:189`。
- PostgreSQL 表结构和任务持久化：`backend/postgres_storage.py:33`、`:424`。
- SQLite 兼容表结构：`backend/store.py:150`。
- 模型元数据和 MinIO 文件清单：`backend/model_storage.py:13`。
- 优云上传、远端运行和产物回传：`backend/providers/youyun/remote_training.py:502`、`:541`、`:638`。
- 日志保留与操作审计：`backend/governance.py:11`。
- 无验证 Final-only 回归测试：`backend/test_llamafactory.py:242`。

概念参考：

- LLaMA-Factory 官方文档：https://llamafactory.readthedocs.io/
- Transformers Trainer 官方文档：https://huggingface.co/docs/transformers/main_classes/trainer
- PEFT LoRA 官方文档：https://huggingface.co/docs/peft/package_reference/lora
- TRL DPO Trainer 官方文档：https://huggingface.co/docs/trl/dpo_trainer

## 11. 后续实施顺序建议

1. 先让产品确认未生效参数是“接入”还是“暂时移除”。
2. 修复 DPO 验证拆分和无验证 Best Adapter 的页面/文档说法。
3. 保存最终生效 YAML 和运行环境版本。
4. 增加 SFT/DPO 分开的参数预设和实验记录。
5. 把效果对比升级为可持久化、可重复的评测流程。

在 DPO 验证语义和有效配置快照完成前，不建议先做大范围参数调优，因为实验结论仍可能建立在不完整的配置认知上。
