# 训练参数与真实进度审计

审计日期：2026-09-02

代码快照：最新发布标签 `v3.0.79`（`b58deae`）；当前分支 `36bc775`，另有未提交推理日志分页改动。

## 1. 老师要求与审计边界

需求基线来自 `REQUIREMENTS_LLAMA_FACTORY_REMOTE_TRAINING.md`：

- 平台参数必须由后端转换成 LLaMA-Factory YAML，不能执行浏览器传入的任意 Shell。
- 本地 CPU 和优云 GPU 必须共享统一执行器输入、日志、指标、产物与清理协议。
- 训练详情必须展示真实状态、日志和 Loss；远程任务必须回传产物并在各种终态释放实例。
- 不得把开发冒烟、测试替身或推断值描述为真实训练结果。

本审计不修改数据集校验/拆分核心逻辑，也不修改优云实例创建、停止或删除流程。

## 2. 当前真正执行的训练链路

当前 worker 的正式路径是：

```text
训练页结构化表单 / 受控 CLI 文本
  -> POST /api/tasks 或 POST /api/tasks/cli
  -> TaskCreate / cli_parser.py
  -> Store 保存原始 payload
  -> app.py:task_config()
  -> LocalLlamaFactoryExecutor._canonical_task()
  -> llamafactory.py:platform_to_llamafactory_config()
  -> <task-workdir>/train.yaml
  -> llamafactory-cli train <train.yaml>
```

优云路径复用同一份本地镜像 YAML，然后仅改写：

- `dataset_dir` 为 `/root/cppt/<task-id>/dataset`
- `output_dir` 为 `/root/cppt/<task-id>/output`
- `fp16=false`、`bf16=true`
- 存在显式 `eval_dataset` 时移除冲突的 `val_size`

最终远端命令是管理员固定生成的：

```text
USE_MODELSCOPE_HUB=1 MODELSCOPE_CACHE=/root/cppt/modelscope-cache \
  $PY -m llamafactory.cli train train.yaml
```

`backend/train_sft.py` 是保留的 Unsloth/TRL 旧执行器和兼容测试目标。`backend/app.py` 当前 worker 不启动它，因此其中支持的自动早停、合并模型等行为不能作为现行 LLaMA-Factory 链路已经生效的证据；LoRA+ 与检查点配置则已在当前 LLaMA-Factory 链路中接入。

## 3. 参数来源、范围与最终落点

范围说明：网页提交先经过 `backend/app.py:TrainingParameters/TaskCreate`；CLI 只做部分手工校验，随后还会由 `backend/llamafactory.py:LlamaFactoryParameters` 校验进入 YAML 的字段。CLI 没有为所有数字参数复制网页 API 的范围，错误值可能延迟到 worker prepare 阶段才失败。

### 3.1 训练参数

| 平台参数 | 默认来源 | 可选值/范围 | 当前 LLaMA-Factory 最终落点 | `train_sft.py` 旧落点 | 审计结论 |
|---|---|---|---|---|---|
| `epochs` | UI/CLI `3` | Web/LF `(0,100]` | `num_train_epochs` | `TrainingArguments.num_train_epochs` | 生效 |
| `learningRate` | UI/CLI `2e-4` | LF 可转为数字，`(0,10]` | `learning_rate` | `learning_rate` | 生效 |
| `batchSize` | UI/CLI `1` | Web/LF `1..64` | `per_device_train_batch_size` | 同名 Trainer 字段 | 生效 |
| `gradientAccumulation` | UI/CLI `8` | Web `1..256`；LF `1..4096` | `gradient_accumulation_steps` | 同名 Trainer 字段 | 生效，Web 范围更窄 |
| `validationSteps` | UI/CLI `50` | Web `>=1` | `_canonical_task()` 明确移除，不写 YAML | 有验证集时写 `eval_steps` | **现行链路不生效** |
| `maxSequenceLength` | UI/CLI `1024` | Web/LF `8..32768` | `cutoff_len` | SFT `max_length`；DPO 同时控制 prompt/target/total length | 生效 |
| `loraRank` | UI/CLI `16` | `1..256` | `lora_rank` | PEFT/Unsloth `r` | 生效 |
| `loraAlpha` | UI/CLI `16` | `1..512` | `lora_alpha` | `lora_alpha` | 生效 |
| `loraDropout` | UI/CLI `0` | `[0,1)` | `lora_dropout` | `lora_dropout` | 生效 |
| `lrScheduler` | UI/CLI `linear` | UI/CLI：`linear/cosine/constant`；API 仅要求字符串 | `lr_scheduler_type` | `lr_scheduler_type` | 生效；最终合法性由 LLaMA-Factory 判断 |
| `loraPlusEnabled` | UI/CLI `false` | bool | 控制是否写入 `loraplus_lr_ratio` | 旧执行器独立处理 | 条件生效；默认关闭 |
| `loraPlusRatio` | UI/CLI `16` | Web `>0` | 开启 LoRA+ 时写入 `loraplus_lr_ratio` | `create_loraplus_optimizer(...loraplus_lr_ratio)` | **条件生效** |
| `targetModules` | UI/CLI `auto` | UI：`auto/attention/all-linear`；后端也允许安全的逗号列表 | `lora_target`；`auto/attention` => q/k/v/o，`all-linear` 再加 gate/up/down | Unsloth `target_modules` | 生效 |
| `optimizer` | UI/CLI `adamw_torch` | UI/CLI：Torch、AdamW 8-bit、Paged 8-bit | `optim`；本地 CPU 对两个 bitsandbytes 选项降级为 `adamw_torch`；优云 GPU 原样传递 | LoRA+ optimizer 构造 | 生效；CLI 默认已与网页一致 |
| `warmupRatio` | UI/CLI `0.1` | `[0,1)` | `warmup_ratio` | 先换算成 `warmup_steps` | 生效 |
| `weightDecay` | UI/CLI `0.01` | Web `>=0`；LF `0..10` | `weight_decay` | optimizer/Trainer | 生效 |
| `maxGradNorm` | UI/CLI `1` | `>0` | `max_grad_norm` | `max_grad_norm` | 生效 |
| `seed` | UI/CLI `42` | Web 任意 int；LF `0..2147483647` | `seed` | `seed`、`data_seed`、数据拆分随机种子 | 生效；CLI 最终范围由 LF 校验 |
| `loggingSteps` | UI/CLI `10` | Web `>=1`；LF `1..100000` | `logging_steps`；同时用于日志缺少 step 时的保守推断 | `logging_steps` | 生效 |
| `packing` | UI/CLI `false` | bool | `packing` | SFT `packing` | 生效 |
| `gradientCheckpointing` | UI/CLI `true` | bool | `gradient_checkpointing` | Unsloth/Trainer gradient checkpointing | 生效 |

LLaMA-Factory 转换器还注入平台固定项：`do_train=true`、`report_to=none`、`ddp_timeout=180000000`。本地固定 `use_cpu=true/fp16=false/bf16=false`；优云把 `use_cpu` 改为 `false` 并使用 `bf16=true`。

### 3.2 任务级训练控制

| 参数 | 默认/范围 | 当前最终落点 | 审计结论 |
|---|---|---|---|
| `taskType` | `SFT`；`SFT/DPO`，必须匹配已发布数据集结构 | `stage=sft/dpo` | 生效 |
| `tuningMode` | `qlora`；`lora/qlora` | `finetuning_type=lora`；仅优云 QLoRA 增加 `quantization_bit=4` | 生效；本地 CPU QLoRA 明确退化为普通 LoRA |
| `validationSplit` | UI/CLI `10`；`0..30` 百分比 | 转为 `val_size=0..0.3`；显式验证集优先 | 生效 |
| `maxSteps` | `-1`；`>=-1` | `>=0` 时写 `max_steps`；`-1` 不写，按 Epoch | 生效；`0` 会写入 YAML，建议只使用 `-1` 或正数 |
| `outputName` | 必填，最长 100 | 用于任务/共享资产显示名称；训练目录仍由受控 `<work-root>/<task-id>/output` 决定 | 元数据生效，不控制真实目录 |
| `checkpointLimit` | `2`；Web `1..20` | `save_total_limit` | 生效 |
| `checkpointStrategy` | `epoch`；UI/CLI `epoch/steps` | `save_strategy` | 生效 |
| `checkpointInterval` | `100`；`>=1` | `save_steps`（仅 `checkpointStrategy=steps`） | 条件生效 |
| `earlyStopping` | `false` | 未传给 LLaMA-Factory callback | **现行链路不生效** |
| `patience` | `3`；Web `1..100` | 仅存储/展示 | **现行链路不生效** |
| `minDelta` | `0`；`>=0` | 仅存储/展示 | **现行链路不生效** |
| `mergeModel` | `false` | 当前 executor 只收集 Adapter + manifest，不执行 merge | **现行链路不生效** |
| `extraParameters` | 空对象 | 仅白名单 snake_case 标量写入 YAML；路径和命令类字段不可覆盖 | 生效，白名单定义在 `cli_parser.py:EXTRA_PARAM_TYPES` |

### 3.3 优云资源参数

| 参数 | 来源/范围 | 最终位置 |
|---|---|---|
| `gpuType` | 实时机型/库存接口；字符串最长 40 | `CheckCompShareResourceCapacity.GpuType`、价格接口、`CreateCompShareInstance.GpuType` |
| `gpuCount` | UI 固定 1；API `1..1` | 库存规格过滤和创建请求 `GPU` |
| `cpuCores` | 只能选择实时返回且有库存的组合；API `1..1024` | 规格过滤和创建请求 `CPU` |
| `memoryGb` | 同上；API `1..4096` | 创建请求 `Memory=memoryGb*1024` |
| `zone` | Provider 环境配置与库存响应 | 提交时校验必须等于服务端配置 zone；不信任浏览器改写 |
| `region/chargeType/imageId/bootDisk` | 仅服务端环境变量 | 优云库存、价格和创建 API；不由浏览器提交密钥或镜像生命周期指令 |

Provider 运行时还使用服务端超时：实例启动默认 900 秒、删除确认默认 300 秒、远程训练默认 86400 秒；这些不是用户训练参数。

### 3.4 效果对比生成参数

`maxNewTokens=256 (1..2048)`、`temperature=0.2 (0..2)`、`topP=0.9 (0,1]`、`seed=42` 只传给 `/api/chat` 和 `backend/chat_inference.py`，不进入训练 YAML。训练前和训练后请求使用同一组参数，返回真实 `elapsedSeconds`、`outputTokens` 和实际加载的 Adapter variant/step。

## 4. 耗时流程的进度真实性

| 流程 | 当前展示 | 数据来源 | 结论 |
|---|---|---|---|
| 数据集浏览器上传 | 百分比 | XHR `upload.onprogress` 的真实已发送/总字节 | 真实字节进度 |
| 基础模型选择 | 本地缓存或“训练时下载” | `/api/models/search.cached` | 只表达缓存事实，不再伪装成已下载完成 |
| LLaMA-Factory 模型下载 | 任务阶段和原始日志 | LLaMA-Factory stdout；优云推理 Base Model 准备阶段记录 ModelScope/Hugging Face 两路结果 | **现行执行器没有可靠统一的下载百分比**；不得用定时器补百分比。远端推理优先 ModelScope，失败后降级 Hugging Face，并保留诊断日志 |
| 优云实例创建/启动 | 当前步骤、实例日志 | 真实库存 API、创建 API、实例状态轮询 | 真实阶段，无虚构百分比 |
| 远程环境安装 | 当前步骤 + 安装日志 tail | 远端 `/tmp/env-install.done` 和真实 pip 日志 | 真实阶段，无虚构百分比 |
| 数据/配置上传优云 | 当前步骤 + 完成日志 | SFTP 调用返回 | 真实完成状态；目前没有逐字节回调 |
| 训练 | Step、Loss、0..95% | LLaMA-Factory 的 `Total optimization steps`、epoch 和 loss/eval_loss 日志 | 真实指标派生；首个指标前保持阶段文案，不制造增长 |
| 远程产物回传 | 当前步骤 +文件数日志 | SFTP `get_tree` 完成及本地产物扫描 | 真实完成状态；目前没有逐字节百分比 |
| Adapter 上传 MinIO | 每个 variant 成功/失败日志 | `upload_directory` 的真实返回/异常 | 无逐字节百分比；任务先完成再上传，详情状态不会显示上传中的数值进度 |
| 训练前后效果对比 | 明确阶段 + 已等待秒数 | 两次真实 `/api/chat` 串行请求和 `Date.now()` elapsed timer | 秒数是真实等待时间，不是完成百分比；回答、token、耗时均来自后端 |

训练百分比公式为 `min(95, floor(step*95/total_steps))`，其中 step/total_steps 来自真实 Trainer 日志；只有产物收集成功后才置 100。日志缺少显式 step 时，会结合 epoch、总步数和 `loggingSteps` 推断指标所在 step，这属于对真实日志的确定性换算，不是假训练数据。

## 5. 本轮发现与处理

已处理的小范围问题：

1. 模型搜索曾把未缓存模型在前端内部统一标成 `ready`，同时显示“可下载”，但页面没有独立下载任务或真实下载进度。现改为“训练时下载”，并保留 `/api/models/search.cached` 的真实缓存状态。
2. CLI 默认 optimizer 仍是 `adamw_8bit`，与网页和 CPU 验收默认值冲突。现统一为 `adamw_torch` 并增加回归断言。
3. 停止确认曾承诺一定保存最低 Loss Adapter，停止详情也固定写“已保存早停产物”。现改为只承诺请求停止，并以真实产物列表为准。
4. 效果对比第一阶段曾写成同时加载基模与 Adapter，但实际第一请求只加载基模。现按两次真实 `/api/chat` 调用分别显示阶段。
5. `ARCHITECTURE.md` 曾把 `train_sft.py` 的旧 Unsloth 行为描述成现行 worker 数据流。现明确实际 LLaMA-Factory 主链路。

仍需后续产品决策、未在本轮扩大修改的差距：

- `validationSteps`、自动早停、`mergeModel` 仍在页面可配置但现行执行器不下发；LoRA+ 和检查点策略/数量已完成接入。
- 活跃模型下载、SFTP 上传/回传和 MinIO 上传没有跨实现稳定的字节总量，因此只显示真实阶段/日志，不应补模拟百分比；优云 Base Model 下载失败时保留 ModelScope/Hugging Face 诊断。
- 前端当前每 2 秒 REST 刷新任务；后端虽有 WebSocket 端点，但训练模块未使用需求文档中的断线续传、连接状态、暂停滚动和复制日志完整交互。

这些差距不影响已存在的真实 Step/Loss 训练进度，但 `validationSteps`、自动早停和模型合并等相关 UI 控件不应对外承诺已生效，后续应选择“接入 LLaMA-Factory 对应字段并补端到端测试”或“从当前表单移除/禁用”之一。
