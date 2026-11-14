# 训推平台：库存、多卡训练与 vLLM 原理总教程

这是一份面向“已经跑过单卡微调和 vLLM，但还没有真正做过分布式训练”的学习文档。文档中的事实以当前仓库代码为准，示例代码用于理解原理，不能直接当成已经上线的功能。

## 先区分需求和截图

附件截图是当前页面状态和问题证据，不是需要执行的指令。真正的需求是：解释 GPU 库存显示、理解多卡训练、理解 vLLM 与缓存、理解 Unsloth，并准备面试表达。

本文使用以下标记：

- **当前实现**：仓库已经存在并会影响运行结果的代码。
- **当前限制**：当前版本明确没有开放的能力。
- **改造方案**：未来实现多卡或更准确库存显示时的设计。
- **教学示例**：为了理解原理而写的最小代码，不代表平台已经采用。

## 贯穿场景：从选卡到上线客服模型

下面所有解释都围绕同一个**假设场景**：小林在训推平台上用已经发布的客服数据集微调 7B 模型，先申请优云 4090，再把 Adapter 部署成 vLLM 服务。第一版资源只有 1 卡；未来改造目标是 2 张 24GB 卡的 DeepSpeed ZeRO-2。

时间线如下：

1. 09:00，小林打开资源选择页，看到 4090 机型卡显示“有库存”，但选择 14 核/64GB 后所有组合都不可选。
2. 09:02，页面按具体 `zone + image + gpu + cpu + memory` 查询容量；小林改选可用组合并提交，后端在创建前再次校验。
3. 09:10，单卡训练 7B 因激活和优化器状态 OOM；小林把同一任务规划为 2 卡 ZeRO-2，使用两个进程和 NCCL 同步。
4. 11:00，训练完成后启动 vLLM，20 个客服请求同时到达；调度器用 continuous batching、PagedAttention 和 chunked prefill 处理请求。
5. 11:05，多个请求共享系统提示词和产品手册，第二个请求命中 Prefix Cache；服务重启后缓存清空，长期记忆仍需数据库或 RAG。

这条时间线用于区分“用户动作、后端状态变化和模型运行时行为”；后文出现的数值都优先沿用这个场景。

## 一、为什么 GPU 没库存，卡片还显示“有库存”

### 1. 页面实际上查了两种库存

优云接口把“机型目录”和“可创建规格”分成了两个接口：

1. `DescribeAvailableCompShareInstanceTypes`：返回某个区域里有哪些 GPU 机型，以及机型状态是否为 `Normal`。
2. `CheckCompShareResourceCapacity`：检查指定 GPU、可用区、镜像和 CPU/内存组合是否真的能创建。

当前后端在 [`backend/app.py`](../../backend/app.py) 的 `/api/youyun/gpu-specs` 中先读取机型目录，选择了 `gpuType` 后才读取具体容量。`GpuMachineType.sold_out` 在 [`backend/providers/youyun/models.py`](../../backend/providers/youyun/models.py) 中定义为：

```python
@property
def sold_out(self) -> bool:
    # 这里只看机型目录返回的 Status。
    # Status=Normal 只能说明机型在目录中可售，
    # 不能说明当前可用区、镜像、CPU 和内存组合一定可创建。
    return self.status != "Normal"
```

前端卡片目前直接根据 `machine.soldOut` 显示“GPU 有库存”或“GPU 无库存”；CPU/内存按钮则根据 `capacity[].available` 显示是否可选。因此会出现：

```text
机型目录：4090 / Status=Normal       -> GPU 有库存
具体容量：1卡 + 14核 + 64GB          -> ResourceEnough=false
页面结果：GPU 卡片绿色，CPU/内存全部红色
```

这不是“库存文字定死”，而是两个接口的语义不同。当前 Pod 可用区和 UHost 可用区也可能拥有完全不同的库存。

后端把这两次调用组合成页面响应，关键代码可以缩写成下面这样（**当前实现**）：

```python
machine_types = await asyncio.to_thread(
    runner.manager.client.describe_instance_types,
    instance_type="uhost",
)  # 第一次调用：拿机型目录和 Status。

capacity = None  # 没选 GPU 时，不提前查询具体 CPU/内存组合。
if selected_gpu_type:
    capacity = await asyncio.to_thread(
        runner.manager.client.check_resource_capacity,
        gpu_type=selected_gpu_type,
        image_id=image_id or config.training_image_id or config.image_id,
        zone=selected_zone,
    )  # 第二次调用：拿当前 zone、镜像下的 ResourceEnough。

response_capacity = [
    {"gpu": spec.gpu, "cpu": spec.cpu, "memoryGb": spec.memory_gb,
     "available": spec.resource_enough}
    for spec in (capacity.specs if capacity is not None else [])
]  # 把供应商字段转成前端能读的 available。
```

逐行对应到上面的场景：

1. `describe_instance_types` 只决定 4090 这张机型卡是否存在于目录中。
2. `capacity = None` 表示用户还没选机型，页面不能声称某个 CPU/内存规格有货。
3. `check_resource_capacity` 把 4090、镜像和 zone 一起交给供应商检查，结果不是一个固定数字。
4. 列表推导把每个组合的 `ResourceEnough` 映射为 `available`，所以前端可以逐个禁用无货组合。

未来前端可以使用下面的**教学示例**计算卡片状态；这里的 `capacity` 是上一个接口返回的数组：

```typescript
const hasOneGpuCapacity = selectedGpuType
  ? capacity.some((item) => item.gpu === 1 && item.available)
  : null;
// 有 GPU 选择时，只有至少一个“1 卡且 available”的组合才显示可创建。
// 没选择 GPU 时返回 null，显示“机型可售，需查询具体规格”。
```

这个判断解决的是页面语义，不能替代提交时的实时校验；用户停留在页面期间，最后一台实例仍可能被别人占用。

### 2. 为什么提交时仍然失败

创建**优云**训练任务或推理服务时，后端会再次调用容量接口，并只接受 `ResourceEnough=true` 的组合；本地服务不经过优云库存流程。库存检查和实例创建之间仍然可能发生竞争，所以这个二次校验必须保留。

**场景例子：**

- 10:00，页面查询到 4090 的 1 卡规格可用。
- 10:01，另一个任务租走了最后一台实例。
- 10:01:02，用户点击创建。
- 后端重新查询得到无库存，返回 409，避免创建一个实际上不存在的实例。

### 3. 推荐的修复方式

机型目录状态和可创建状态应该分开返回，不要复用 `soldOut` 表示两件事。例如：

```json
{
  "name": "4090",
  "soldOut": false,
  "capacityAvailable": false,
  "stockStatus": "catalog_available_capacity_unavailable"
}
```

前端可以按下面的规则展示：

- 未选择 GPU：`机型可售，需查询具体规格`。
- 已选择 GPU 且存在 `gpu=1 && available=true`：`当前有可创建规格`。
- 已选择 GPU 但所有单卡规格不可用：`当前规格无库存`。
- 最终创建：仍由后端实时复核。

**面试表达：**

> 我们发现 GPU 机型目录状态和资源容量状态不是同一个概念。目录的 `Status=Normal` 只能说明机型存在，真正决定能否创建的是指定 zone、镜像、GPU 数量、CPU 和内存组合的 `ResourceEnough`。所以我会把 `catalogStatus` 和 `capacityAvailable` 分开，并在提交前再次校验，因为库存存在瞬时竞争。

## 二、从单卡到 DeepSpeed ZeRO 多卡训练

### 1. 先理解单卡 OOM

训练显存不只存模型权重，还要存：

- 梯度：反向传播计算出的参数更新方向。
- 优化器状态：Adam 通常还要保存一阶、二阶动量。
- 激活值：前向计算中为了反向传播暂存的中间结果。
- LoRA 参数、临时张量和 CUDA workspace。

**场景例子：**7B 模型用 BF16 权重大约需要 14GB，但训练时加上梯度、Adam 状态和长序列激活，24GB 显卡仍可能 OOM。QLoRA 把基础权重压到 4-bit，LoRA 只训练少量参数；Gradient Checkpointing 则释放部分激活，需要反向时重新计算。

### 2. 多卡里的几个词

- `world size`：参与本次训练的进程总数，单机 2 卡通常是 2。
- `rank`：进程编号，通常从 0 到 `world_size-1`。
- `local rank`：进程在当前机器上的 GPU 编号。
- NCCL：NVIDIA GPU 之间进行集合通信的库，负责梯度同步和参数通信。
- DDP：每张卡一个进程，每个进程通常持有一份模型，在反向后同步梯度。

普通 DDP 能提高吞吐，但模型和优化器可能在每张卡都复制一份；当每张卡都放不下时，需要 ZeRO。

### 3. ZeRO-1、ZeRO-2、ZeRO-3

| 阶段 | 切分内容 | 适用直觉 |
| --- | --- | --- |
| ZeRO-1 | 优化器状态 | 模型能放下，但 Adam 状态太大 |
| ZeRO-2 | 优化器状态 + 梯度 | 常见的单机多卡训练起点 |
| ZeRO-3 | 优化器状态 + 梯度 + 参数 | 模型参数本身也放不进单卡 |

ZeRO-2 不等于把显存简单相加：普通模型参数通常仍会在每张卡保留一份；通信和同步还会占用时间。在 LoRA/QLoRA 中，基座参数大多被冻结，ZeRO-2 的主要收益来自切分 Adapter 的梯度和优化器状态，而不是自动把冻结基座切开。只有使用 ZeRO-3 等参数分片方案，才会进一步降低每张卡的参数副本。

### 4. 最小 ZeRO-2 配置示例

下面是**教学示例**，保存为 `ds_zero2.json`：

```json
{
  "train_batch_size": "auto",
  "gradient_accumulation_steps": "auto",
  "zero_optimization": {
    "stage": 2,
    "offload_optimizer": {
      "device": "none"
    },
    "contiguous_gradients": true
  },
  "fp16": {
    "enabled": true
  }
}
```

逐行理解：

- `train_batch_size=auto`：让训练框架根据每卡 batch 和梯度累积计算全局 batch。
- `gradient_accumulation_steps=auto`：避免配置文件和训练参数互相矛盾。
- `stage=2`：切分优化器状态和梯度。
- `offload_optimizer.device=none`：先不把优化器状态搬到 CPU，减少 CPU/PCIe 通信；显存仍不够时再评估 `cpu`。
- `contiguous_gradients=true`：让梯度尽量连续存放，减少内存碎片。
- `fp16.enabled=true`：使用 FP16 混合精度；实际是否使用 FP16/BF16 要看 GPU 架构和训练框架版本。

启动命令的形式取决于安装的 DeepSpeed 和 LLaMA-Factory 版本，下面只是**教学示例**，上线前必须在目标镜像中做一次启动冒烟：

```bash
# --num_gpus=2 表示启动两个训练进程。
# --module 让 DeepSpeed 以 Python 模块方式启动 LLaMA-Factory。
# deepspeed 参数应与当前镜像中的 LLaMA-Factory 版本做一次冒烟验证。
deepspeed --num_gpus 2 --module llamafactory.cli train train.yaml
```

有些 LLaMA-Factory 版本使用 `FORCE_TORCHRUN=1 llamafactory-cli train train.yaml`，当前项目的远程 runner 也保留了自己的 `python -m llamafactory.cli train ...` 启动路径。因此不能只把命令字符串从单卡改成 `deepspeed`，必须确认目标镜像的入口、配置文件格式和日志事件格式。

### 5. 结合当前项目的改造边界

**当前限制：**

- 前端多卡规格只展示为“暂不支持”。
- `TaskCreate`、`YouyunResourceSelection`（训练资源请求）以及 `InferenceCreate` 关联的优云资源当前都把 `gpuCount` 限制为 1。
- 推理服务的 `tensorParallelSize` 当前也被强制重置为 1。
- active training path 是 `LocalLlamaFactoryExecutor -> LLaMA-Factory`；[`backend/train_sft.py`](../../backend/train_sft.py) 是保留的兼容 runner，不是当前主 worker。

**未来改造顺序：**

1. 资源模型增加 `gpuCount` 上限、`distributedStrategy` 和 `zeroStage`，第一版只允许单机 2/4/8 卡。
2. `/api/youyun/gpu-specs` 和资源解析逻辑按同一 zone、镜像和 GPU 数量检查 `ResourceEnough`。
3. 远程 runner 为任务生成 `ds_zero2.json` 或 `ds_zero3.json`。
4. 启动时固定 `CUDA_VISIBLE_DEVICES`、`WORLD_SIZE`、`MASTER_ADDR` 和 `MASTER_PORT`，并使用 DeepSpeed launcher。
5. 只有 rank 0 写最终日志摘要、Best/Final Adapter 和 checkpoint 元数据，避免多个进程重复上传。
6. 训练完成、失败和取消时统一回收远程实例。

### 6. 多卡失败排查

**场景 A：`NCCL error`**

- 先检查 `nvidia-smi`、驱动、CUDA、Torch CUDA 版本是否匹配。
- 检查进程数是否等于实际 GPU 数量。
- 检查 `MASTER_PORT` 是否被占用。
- 单机调试时可临时设置 `NCCL_DEBUG=INFO`；不要把它永久当作性能配置。

**场景 B：2 卡仍然 OOM**

- 先确认实际使用的是 2 个进程，而不是只启动了一个进程。
- 降低每卡 batch、序列长度和 `max_grad_norm` 不能直接解决激活占用时，应优先启用 Gradient Checkpointing 或改用 ZeRO-3。
- 检查是否因为保存合并模型、评估 batch 或临时 checkpoint 额外占用显存。

**场景 C：训练结束出现多个重复 Adapter**

- 输出目录和 checkpoint 写入必须由 rank 0 控制。
- 后端入库动作应具备幂等键，例如 `task_id + artifact_variant`。
- 日志可以收集所有 rank，但前端默认展示 rank 0 摘要和聚合进度。

**面试表达：**

> 我会先用单机多卡 DeepSpeed ZeRO-2，因为它能切分优化器状态和梯度，改造成本低于 ZeRO-3。若模型参数本身也无法放进单卡，再切换 ZeRO-3。工程上不能只把 GPU 数量改成 2，还要同步处理容量校验、进程启动、NCCL、rank 0 写文件、日志聚合、checkpoint 幂等和失败清理。

## 三、vLLM 为什么能加速推理

### 1. 20 个客服请求的场景

普通 Transformers 服务经常按“一个请求跑完，再处理下一个请求”的思路组织 GPU；vLLM 会把正在等待、正在 prefill、正在 decode 的请求动态安排到同一批次中。

一个请求通常分两个阶段：

- **Prefill**：一次处理用户输入，计算所有输入 token 的注意力信息。
- **Decode**：每次生成一个或少量新 token，反复读取已有上下文。

vLLM 的主要收益来自：

1. PagedAttention：把 KV Cache 按块管理，减少连续大内存分配和碎片。
2. Continuous batching：新请求可以加入正在运行的批次，不必等待整批结束。
3. Chunked prefill：长输入分块处理，避免一个长文档阻塞短请求的首 token。
4. 高效 CUDA kernel 和调度器：减少 Python 层等待和 GPU 空转。

### 2. 当前项目实际生成的命令

下面代码来自 [`backend/inference_service.py`](../../backend/inference_service.py) 的 managed vLLM 命令构造逻辑，删去与本教程无关的分支并加上解释：

```python
command = [
    self._vllm_python(),                  # 使用独立的 vllm_env Python。
    "-m", "vllm.entrypoints.openai.api_server",  # 启动 OpenAI 兼容服务。
    "--model", model,                     # 加载 Base Model。
    "--max-model-len", str(config["maxModelLength"]),  # 限制单请求上下文长度。
    "--max-num-seqs", str(config["maxNumSeqs"]),       # 同时调度的序列上限。
    "--max-num-batched-tokens", str(config["maxNumBatchedTokens"]),
                                             # 一次调度允许处理的 token 上限。
    "--gpu-memory-utilization", str(config["gpuMemoryUtilization"]),
                                             # vLLM 预留的 GPU 显存比例。
    "--dtype", config["dtype"],           # 权重/计算精度，如 auto、float16。
    "--tensor-parallel-size", str(config["tensorParallelSize"]),
                                             # 将模型张量分布到几张 GPU。
]

if config.get("enablePrefixCaching"):
    command.append("--enable-prefix-caching")  # 复用相同前缀的 KV。

if config.get("enableChunkedPrefill"):
    command.append("--enable-chunked-prefill")  # 长输入分块 prefill。

if config.get("kvCacheDtype") == "fp8" and not self._cpu_inference():
    command.extend(["--kv-cache-dtype", "fp8"])  # GPU 兼容时用 FP8 保存 KV Cache。

if config.get("kvCacheMode") == "manual" and config.get("kvCacheMemoryGiB"):
    cache_bytes = int(float(config["kvCacheMemoryGiB"]) * 1024**3)
    command.extend(["--kv-cache-memory-bytes", str(cache_bytes)])
    # 把 GiB 转成 vLLM 接受的字节数；当前创建接口会把 manual 重置成 auto。
```

上面最后两段是**当前命令构造器支持的分支**，但普通 `POST /api/inference/services` 创建时会先覆盖配置，所以通过页面创建的新服务通常不会走到 FP8 或手动容量分支。远程优云 runner 也有对应的参数构造逻辑，仍需和目标 vLLM 版本一起验证。

参数调节建议：

- 显存不足：先降低 `maxModelLength`、`maxNumSeqs` 或 `maxNumBatchedTokens`，再考虑 4-bit 权重量化。
- 首 token 慢：检查长 prompt、排队长度和 chunked prefill。
- 吞吐低：逐步提高 `maxNumSeqs` 和 batched tokens，同时观察 KV Cache 使用率和等待请求数。
- 输出质量变化：优先保持 `dtype` 和采样参数稳定，不要为了吞吐随意改精度。
- 多卡部署：`tensorParallelSize` 必须与实际 GPU 数量、模型结构和镜像兼容。

### 3. 高级参数：BitsAndBytes、FP8 KV Cache 与 Tensor Parallel

- **BitsAndBytes 4-bit**：`--quantization bitsandbytes` 和 `--load-format bitsandbytes` 主要压缩模型权重。它不会自动把 KV Cache 变成 4-bit；KV Cache 仍按 vLLM 的默认 dtype 保存，除非另外设置 `--kv-cache-dtype`。
- **FP8 KV Cache**：在支持的 GPU、CUDA、vLLM 版本组合上，FP8 每个 KV 元素占用更少显存，可以容纳更长上下文或更多并发；代价是需要额外量化/反量化处理，老架构或不匹配的镜像可能启动失败或影响质量。当前代码在 CPU 冒烟环境明确拒绝 FP8。
- **Tensor Parallel**：`--tensor-parallel-size 2` 表示两个 GPU 进程共同承载模型张量，适合单卡放不下但模型结构支持切分的场景。它不是把两个独立服务拼在一起，GPU 之间仍要通过 NCCL 通信。

**场景例子：**一个 20 并发客服服务使用 2 张 GPU。先用 4-bit 权重降低模型占用，再根据 GPU 架构尝试 FP8 KV Cache；如果模型仍无法装入单卡，才把 `tensorParallelSize` 调到 2，并观察首 token 延迟、吞吐和错误日志。

### 4. 当前高级设置为什么没有全部生效

推理服务创建路由在 [`backend/inference_service.py`](../../backend/inference_service.py) 中会把以下字段强制恢复为平台默认值：

```python
payload = payload.model_copy(
    update={
        "kvCacheMode": "auto",
        "kvCacheMemoryGiB": None,
        "kvCacheDtype": "auto",
        "tensorParallelSize": 1,
    }
)
```

所以当前页面可以展示部分高级字段，但创建新服务时，手动 KV Cache 容量、KV dtype 和 Tensor Parallel 并没有完全开放。这样做是为了在兼容矩阵和资源校验完成前避免用户提交无法启动的组合。

未来开放时需要同时增加：GPU 数量校验、dtype/架构矩阵、CPU 与 GPU 参数边界、启动前显存预估、失败回滚和可观测指标，而不是只删除这段覆盖逻辑。

## 四、KV Cache、Prefix Cache 和长期记忆

### 1. KV Cache 是什么

Transformer 每层注意力都会产生 Key 和 Value。生成第二个 token 时，历史 token 的 Key/Value 通常不必重新计算，服务把它们保存在显存中，这就是 KV Cache。

**场景：**用户输入 2,000 token，模型生成 300 token。生成第 299 个 token 时，前面 2,000 个输入和已经生成的 token 的 KV 可以直接复用，因此不用每一步重新计算完整历史。

KV Cache 的代价是显存会随以下因素增加：

- 层数和隐藏维度。
- 上下文长度。
- 并发请求数。
- KV Cache 数据类型。

### 2. Prefix Cache 是什么

Prefix Cache 是对 KV Cache 的进一步复用：如果多个请求经过同一个 tokenizer 后，开头 token 序列完全相同，例如相同的系统提示词和相同的长文档，后一个请求可以复用前一个请求已经算好的前缀 KV。只要空格、模板版本、特殊 token 或系统提示词发生变化，就可能无法命中。

```text
请求 A：系统提示词 + 产品手册 + “如何退款？”
请求 B：系统提示词 + 产品手册 + “如何换货？”
                         ^^^^^^^^^^^^^^^^^^^^^
                         这段相同前缀可以复用
```

因此两者关系是：

- KV Cache 是生成过程中保存注意力中间结果的总称。
- Prefix Cache 是把共享前缀的 KV 跨请求复用的机制。
- Prefix Cache 不是数据库记忆，也不是模型权重更新。

### 3. 它们是长期记忆还是短期记忆

它们属于推理过程中的短期缓存：

1. 第一次请求计算系统提示词和长文档的 KV。
2. 第二次请求命中相同前缀，减少 prefill 计算。
3. 缓存被淘汰、服务重启或模型实例销毁后，缓存消失。
4. 模型不会因为 KV Cache 永久记住用户信息。

真正的长期记忆需要额外系统，例如 PostgreSQL 保存结构化事实、向量数据库保存检索片段、摘要服务保存用户偏好，再在下一次请求前重新拼接到 prompt 中。

**面试表达：**

> KV Cache 是当前推理上下文的计算缓存，减少 decode 阶段重复计算；Prefix Cache 是对共享 prompt 前缀的跨请求 KV 复用。它们都属于短期运行时缓存，不会修改模型，也不会自动形成长期记忆。长期记忆需要数据库、向量检索或摘要系统参与。

## 五、Unsloth 如何加速训练

Unsloth 和 vLLM 解决的是不同阶段的问题：Unsloth 主要优化训练，vLLM 主要优化推理服务。

### 1. 训练场景

假设要用 24GB GPU 微调 7B 模型：

- LoRA 只训练低秩 Adapter，不更新全部基础模型参数。
- QLoRA 用 4-bit 保存基础权重，进一步降低显存。
- Gradient Checkpointing 不保存所有激活，反向时重新计算一部分。
- Packing 把多个短样本拼到同一个序列，减少 padding 浪费。
- Unsloth 通过 Triton/fused kernel、张量布局和训练路径优化降低额外开销。

这些优化不是“免费显存”：Checkpointing 会增加计算，4-bit 可能引入反量化开销，Packing 需要确认样本边界和 chat template 正确。

### 2. 当前仓库中的边界

当前主训练链路是：

```text
训练任务 -> LocalLlamaFactoryExecutor -> backend/llamafactory.py -> llamafactory-cli
```

[`backend/chat_inference.py`](../../backend/chat_inference.py) 仍保留 Unsloth 优先、Transformers + PEFT fallback 的加载逻辑；[`backend/train_sft.py`](../../backend/train_sft.py) 也保留了旧的 Unsloth/TRL runner。阅读代码时要区分“兼容路径”和“当前 worker 使用的主路径”。

仓库中的 Unsloth 优先加载片段如下，已经把每一行的作用写在右侧：

```python
if torch.cuda.is_available():
    # 只有 CUDA GPU 环境才尝试 Unsloth；CPU 环境直接走下面的 fallback。
    try:
        from unsloth import FastLanguageModel
        # FastLanguageModel 是 Unsloth 提供的高效模型加载和推理入口。

        model_path = adapter_path or resolve_model(model_id, source)
        # 有 Adapter 时加载 Adapter 路径，否则解析 Base Model 路径。

        model_dtype = (
            None
            if load_in_4bit
            else (torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16)
        )
        # 4-bit 加载时让量化流程决定权重类型；否则优先 BF16，不支持时用 FP16。

        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=model_path,
            max_seq_length=2048,
            dtype=model_dtype,
            load_in_4bit=load_in_4bit,
            trust_remote_code=False,
        )
        # 读取模型和 tokenizer；load_in_4bit=True 会降低基础权重显存占用。

        FastLanguageModel.for_inference(model)
        # 切换到 Unsloth 优化过的推理路径，减少不必要的训练相关开销。

        return model, tokenizer, torch.device("cuda"), "unsloth"
        # 返回模型、分词器、设备和 loader 名称，供日志和后续生成使用。
    except ImportError:
        pass
        # 当前环境没有安装 Unsloth 时不让整个聊天服务失败。

return _load_model_with_transformers(model_id, source, adapter_path, torch)
# fallback 使用标准 Transformers + PEFT，保证 CPU 或缺少 Unsloth 时仍可工作。
```

这段代码说明了一个重要工程取舍：加速库是可选依赖，不能让 CPU 验收或基础推理因为 `import unsloth` 失败而整体不可用。

**面试表达：**

> Unsloth 主要通过更高效的 kernel、显存布局和训练实现降低训练开销；LoRA/QLoRA 则从算法和参数存储角度减少需要更新的内容。vLLM 是推理服务引擎，两者优化目标不同。在这个项目里，当前主训练执行器是 LLaMA-Factory，Unsloth 主要出现在保留的兼容训练和模型加载路径。

## 六、面试场景题速答

### 一句话项目介绍

我负责理解和改造训推平台的 GPU 资源校验、单机多卡训练规划与 vLLM 推理参数边界，重点保证目录库存、真实容量、运行时缓存和任务生命周期的语义一致。

### 30—60 秒回答模板

这个平台前端展示的 GPU 机型库存来自目录接口，真正能不能创建还要看指定可用区、镜像、CPU、内存和 GPU 数量的容量接口，所以我会把两种状态拆开并在提交前二次校验。多卡第一版我会选择单机 DeepSpeed ZeRO-2：每张卡一个进程，用 NCCL 同步，切分优化器状态和梯度；如果模型参数本身放不进单卡，再升级 ZeRO-3。推理侧 vLLM 通过 PagedAttention、continuous batching 和 chunked prefill 提高并发吞吐；KV Cache 和 Prefix Cache 都是可淘汰的短期计算缓存，长期记忆要由数据库、向量检索或摘要系统提供。当前仓库主训练路径仍是 LLaMA-Factory，Unsloth 属于兼容加载或旧 runner 路径，不能把教学方案描述成已经上线的能力。

### 问：多卡显存是不是直接相加？

答：不是。普通 DDP 通常每张卡都有一份模型，显存不会完全相加；DeepSpeed ZeRO 会切分优化器、梯度甚至参数，才可以更充分地利用多卡显存。同时要付出 NCCL 通信、同步和调度开销，所以吞吐和可训练模型规模都会受到通信与 batch 配置影响。

### 问：为什么选择 ZeRO-2 而不是一开始就 ZeRO-3？

答：ZeRO-2 已经能解决大量优化器和梯度占用问题，通信与改造复杂度相对可控。只有当模型参数本身也无法放入单卡，或者需要进一步降低每卡参数占用时，再使用 ZeRO-3，并重点验证参数分片、通信、保存和恢复性能。

### 问：vLLM 的核心加速点是什么？

答：核心是 PagedAttention 管理 KV Cache、Continuous Batching 动态混合请求，以及针对 prefill/decode 的调度和 kernel 优化。它减少显存碎片和 GPU 空转，提高并发吞吐；具体收益仍取决于模型、上下文长度、并发和硬件。

### 问：Prefix Cache 能不能当用户长期记忆？

答：不能。它只是相同前缀的 KV 计算结果复用，服务重启或缓存淘汰就会消失。长期记忆要落到数据库、向量库或摘要系统，并在下一次请求中重新注入上下文。

### 问：库存显示问题如何修？

答：把机型目录状态与可创建容量拆成两个字段。目录 `Normal` 只显示机型存在；具体卡片状态根据当前 zone、镜像和 `ResourceEnough` 计算。提交前再做一次实时校验，处理库存竞争。

## 七、阅读和落地顺序

建议按这个顺序在仓库里对照阅读：

1. [`backend/app.py`](../../backend/app.py)：`/api/youyun/gpu-specs` 和资源解析流程。
2. [`backend/providers/youyun/client.py`](../../backend/providers/youyun/client.py)：优云容量接口字段映射。
3. [`backend/providers/youyun/instance_manager.py`](../../backend/providers/youyun/instance_manager.py)：库存检查、规格选择和实例创建。
4. [`backend/llamafactory.py`](../../backend/llamafactory.py)：训练参数如何转换为 YAML。
5. [`backend/inference_service.py`](../../backend/inference_service.py)：vLLM 参数模型、命令构造和指标解析。
6. [`components/inference-module.tsx`](../../components/inference-module.tsx)：前端高级设置和库存展示。

当前版本的安全落地顺序应是：先修库存语义，再做单机多卡 DeepSpeed ZeRO-2 的小规模冒烟，最后逐项开放 vLLM Tensor Parallel、KV dtype 和手动 Cache 容量。每一步都要保留启动前校验、失败清理和指标观测。


## 八、当前分布式训练落地状态（实现对应代码）

当前平台不是“多张卡显存自动拼成一张大卡”。当前实现是**单实例多卡 DDP**：一台优云实例里有 N 张 GPU，`torchrun` 启动 N 个进程，每个进程绑定一张卡；每张卡拥有完整模型副本和自己的一批数据，N 个进程在反向传播后用 NCCL 做梯度同步。

### 8.1 单卡和多卡的成本判断

假设：

- H100 单卡价格高，但显存大、通信少；
- 4 张低价卡总价低，但每张卡都要放得下完整模型；
- 任务每步处理的数据量相同。

不能直接说“多卡一定便宜”，应该比较：

```text
总成本 = GPU 小时单价 × GPU 数量 × 训练时长
单位样本成本 = 总成本 / 实际处理样本数
```

DDP 的有效全局批次为：

```text
per_device_batch × gradient_accumulation × gpu_count
```

多卡可能降低总训练时间，但要付出梯度通信、初始化、机器调度和故障排查成本。如果单卡 H100 能把模型和 batch 放进显存，单卡可能反而拥有更好的吞吐/稳定性；如果任务是吞吐型、低价卡库存充足，多卡才可能有更低单位样本成本。

### 8.2 当前启动代码

`backend/providers/youyun/remote_training.py`：

```python
def _ddp_launch_env(gpu_count: int) -> str:
    if gpu_count <= 1:
        return ""
    visible = ",".join(str(index) for index in range(gpu_count))
    return (
        f"FORCE_TORCHRUN=1 NPROC_PER_NODE={gpu_count} "
        "MASTER_ADDR=127.0.0.1 MASTER_PORT=29500 "
        f"CUDA_VISIBLE_DEVICES={visible} "
    )
```

逐行：

1. 单卡返回空字符串，保持旧链路不变。
2. `NPROC_PER_NODE=N` 要求本机启动 N 个训练进程。
3. `MASTER_ADDR/PORT` 让进程完成 rendezvous。
4. `CUDA_VISIBLE_DEVICES` 限制训练只看到平台校验过的 GPU。
5. `FORCE_TORCHRUN=1` 让 LLaMA-Factory 使用 torchrun 启动。

启动前还会执行 `nvidia-smi`，如果用户选择 4 卡但实例只暴露 2 卡，任务失败并进入实例清理，不会带着错误配置开始训练。

### 8.3 DeepSpeed ZeRO-2 的第一版实现

当前新增 `backend/distributed.py`：

```python
def build_deepspeed_zero2_config() -> str:
    config = {
        "train_batch_size": "auto",
        "train_micro_batch_size_per_gpu": "auto",
        "gradient_accumulation_steps": "auto",
        "zero_optimization": {
            "stage": 2,
            "reduce_scatter": True,
            "allgather_partitions": True,
            "overlap_comm": True,
            "contiguous_gradients": True,
        },
        "fp16": {"enabled": "auto"},
        "bf16": {"enabled": "auto"},
    }
    return json.dumps(config, ensure_ascii=False, indent=2) + "\n"
```

ZeRO-2 不复制每张卡的优化器状态和梯度，而是把它们分片；它仍然是数据并行训练，不等于把模型任意切开。第一版只在至少 2 张 GPU 时允许 `deepspeed_zero2`，暂不承诺 ZeRO-3、QLoRA + ZeRO-3 或多机多节点。

远程训练会把配置写成 `ds_zero2.json`，再在 LLaMA-Factory YAML 中设置：

```yaml
deepspeed: /root/cppt/<task-id>/ds_zero2.json
```

这条路径由平台生成，用户不能在浏览器输入任意文件路径。
