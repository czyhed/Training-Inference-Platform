# 微调远程链路与 vLLM 推理

## Confirmed scope

本笔记回答三个问题：优云远程微调如何传输数据、YAML、日志和权重；当前项目如何启动和使用 vLLM；LLaMA-Factory/vLLM 日志与 WebUI 参数的真实关系。外部公共云存储、GPU 多卡调度和独立评测结果不在本次范围。

## Call chain

### 优云微调

`components/training-module.tsx:681-718`
-> `POST /api/tasks` (`backend/app.py:3776-3811`)
-> `backend/app.py:1637-1680` worker 分流
-> `backend/providers/youyun/remote_training.py:run`
-> `YouyunInstanceManager.create_one_shot`
-> `YouyunSSHClient.connect/run/put_file/get_tree`
-> 远端 `python -m llamafactory.cli train train.yaml`
-> `backend/store.py:1203-1285` 日志、指标、产物元数据

### 推理服务

`components/inference-module.tsx:1638-1723`
-> `POST /api/inference/services` (`backend/inference_service.py:2522-2539`)
-> 只保存配置，状态为 `created`
-> 手动 `POST /api/inference/services/{id}/start`
-> `InferenceRuntime.start` (`backend/inference_service.py:1668-1945`)
-> 本地 `backend/inference_service.py:1324-1376` 启动 vLLM 子进程；或优云 `backend/providers/youyun/inference.py:769-1001`
-> `/health` 通过后标记 `running`

## 1. 优云远程微调

### 阶段目标

把一次平台训练任务变成远端一次性 GPU 实例上的 LLaMA-Factory 运行，并让本地页面看到可追踪的日志、Loss、状态和 Adapter 产物。

### 核心流程

1. 页面组装业务任务 JSON；优云资源包含 GPU、CPU、内存、可用区、镜像和价格快照。
2. 后端验证数据集已发布、任务类型匹配，并把任务放入 SQLite/PostgreSQL 队列。
3. Worker 为优云任务创建独立一次性实例，等待实例进入 Running，再轮询刷新 SSH 转发地址。
4. 通过 Paramiko SSH/SFTP 建目录，上传本地镜像中的数据文件和最终 `train.yaml`。
5. 远端以 `nohup bash -c ...` 启动 `python -m llamafactory.cli train train.yaml`，stdout/stderr 重定向到远端 `train.log`。
6. 本地每轮按字节 offset 读取 `tail -c +offset train.log`，逐行写入任务日志；识别 `loss/eval_loss/step` 后追加指标和进度。
7. 训练成功后用 SFTP 递归下载远端 `output/` 到本地镜像目录，再复用本地 `collect_artifacts` 生成 Best/Final Adapter 和 manifest。
8. 成功任务把实例标记为 `awaiting_comparison` 暂留；失败、停止或清理路径删除实例，删除失败会进入 `cleanup_failed` 并提示手工清理。

### 关键代码

```python
# backend/providers/youyun/remote_training.py:546-592
handle = await self.prepare_local(task)  # 本地先复制数据并生成、校验 train.yaml
remote_workdir = f"{self.remote_root}/{task_id}"
config = _load_yaml_dict(workdir / "train.yaml")
config["dataset_dir"] = f"{remote_workdir}/dataset"
config["output_dir"] = f"{remote_workdir}/output"
config["fp16"] = not use_bf16
config["bf16"] = use_bf16
remote_yaml = dump_yaml(config)

for item in sorted((workdir / "dataset").iterdir()):
    if item.is_file():
        ssh.put_file(item, f"{remote_workdir}/dataset/{item.name}")
ssh.put_text(remote_yaml, f"{remote_workdir}/train.yaml")
```

这说明远程不是把网页参数直接交给优云，而是先复用本地配置转换器生成 YAML，再只改写远端路径和精度。

```python
# backend/providers/youyun/remote_training.py:595-649
runner = (
    f"cd {remote_workdir} && PY=$(cat {_PYTHON_PATH_FILE}) && "
    f"$PY -m llamafactory.cli train train.yaml; echo $? > {code_path}"
)
launch = f"rm -f {code_path}; nohup bash -c {_posix_quote(runner)} >> {log_path} 2>&1 & echo $! > {pid_path}"
...
_, output = await asyncio.to_thread(
    ssh.run,
    f"echo code:$(cat {code_path} 2>/dev/null || echo none); "
    f"tail -c +{offset + 1} {log_path} 2>/dev/null || true",
    60,
    False,
)
```

`offset` 是本地已消费的 UTF-8 字节数，所以页面刷新不会每次重新读完整日志。`_ingest_chunk` 会把原始行写入日志表，同时调用 `parse_trainer_values` 解析训练指标。

```python
# backend/providers/youyun/remote_training.py:693-712
local_output = Path(handle.workdir) / "output"
await asyncio.to_thread(ssh.get_tree, f"{remote_workdir}/output", local_output)
artifacts = await self.collect_local(task)
self.sink.update(task_id, artifacts=[*artifacts, *instance_markers])
```

### 技术角色

- Paramiko SSH：执行远端命令、维持连接、自动重连。
- SFTP：上传 JSONL/YAML，递归下载 Adapter 文件。
- LLaMA-Factory：真正读取 YAML、加载基模并训练；平台不实现训练算法。
- SQLite/PostgreSQL store：保存任务状态、日志、Loss 指标和产物路径。

### 场景

用户选择 `Qwen/Qwen2.5-7B-Instruct`、DPO、QLoRA、4090 和已发布数据集，点击提交。后端创建 `uhost-xxx`，上传 `/root/cppt/task-1/dataset/train.jsonl` 与 `/root/cppt/task-1/train.yaml`，远端训练输出写入 `/root/cppt/task-1/train.log`。页面看到的“训练日志”来自这个文件的增量回传；训练完成后 `/root/cppt/task-1/output` 下载到本地 `runtime/remote-jobs/task-1/output`，Best/Final Adapter 由同一套本地收集器登记。

## 2. 当前 vLLM 推理与加速

### 阶段目标

把已训练任务的 Base Model 和指定 Adapter 作为一个 OpenAI 兼容服务运行，并提供 JSON/SSE 对话、日志、健康检查和 Prometheus 指标。

### 核心流程

1. 创建服务时读取任务的 Base Model 和 Best/Final Adapter，校验上下文上限；只保存配置，不占 GPU。
2. 启动时先检查 vLLM Python 环境；本地检查 `import vllm`，优云记录 Python/Torch/CUDA/vLLM 指纹并检查 GPU。
3. 托管模式生成固定 argv：`python -m vllm.entrypoints.openai.api_server --model ... --enable-lora --lora-modules ...`。
4. 本地把 stdout/stderr 接到 watcher；优云把远端 stdout/stderr 重定向到 `vllm.log`，再通过 SSH 增量读取。
5. 轮询 `/health`；通过后状态变成 `running`，请求代理到 `/v1/chat/completions`。
6. 运行中从 `/metrics` 读取 KV Cache 使用率、运行/等待请求、Prefix Cache 命中率、TTFT 等指标。

### 关键代码

```python
# backend/inference_service.py:1324-1376
command = [
    self._vllm_python(), "-m", "vllm.entrypoints.openai.api_server",
    "--model", model,
    "--served-model-name", service["id"],
    "--enable-lora", "--lora-modules", f"{service['id']}={config['adapter']}",
    "--host", "0.0.0.0", "--port", str(self.port),
    "--max-model-len", str(config["maxModelLength"]),
    "--max-num-seqs", str(config["maxNumSeqs"]),
    "--max-num-batched-tokens", str(config["maxNumBatchedTokens"]),
    "--gpu-memory-utilization", str(config["gpuMemoryUtilization"]),
    "--dtype", config["dtype"],
    "--tensor-parallel-size", str(config["tensorParallelSize"]),
]
```

项目当前真正使用的加速/资源能力：

- **PagedAttention**：vLLM 默认的 KV Cache 分页管理，减少连续大块显存分配造成的浪费。
- **连续批处理**：请求到达和完成时间不同也可以动态合批，提高 GPU 利用率。
- **Prefix Caching**：相同前缀的 KV 结果可复用，减少重复 prefill。
- **Chunked Prefill**：把长提示词的 prefill 分块，避免长请求独占整个调度周期。
- **BitsAndBytes 4-bit**：可选压低 Base Model 权重显存，但依赖运行环境和 GPU 兼容性。
- `max-num-seqs`、`max-num-batched-tokens`、`gpu-memory-utilization`：控制并发、批 token 上限和显存预算，属于调度/资源参数，不改变模型本身。

PagedAttention、连续批处理和 KV/prefix cache 属于 vLLM runtime 的服务侧优化，不是模型权重自带的“能力”；模型主要提供权重、架构和 tokenizer，vLLM 负责把模型高效地部署成服务。vLLM 也不只是日志工具，而是推理运行时、调度器、KV Cache 管理器和 OpenAI 兼容 API server 的组合。

### 为什么高级设置有些不能设置

前端虽然定义了 `kvCacheMode`、`kvCacheMemoryGiB`、`kvCacheDtype`、`tensorParallelSize`，但创建路由会主动覆盖：

```python
# backend/inference_service.py:2522-2533
payload = payload.model_copy(update={
    "kvCacheMode": "auto",
    "kvCacheMemoryGiB": None,
    "kvCacheDtype": "auto",
    "tensorParallelSize": 1,
})
```

原因是当前 MVP 的兼容矩阵还没有完成：远程 Provider 按单卡启动，FP8 KV Cache、手动 KV Cache 容量、多卡 Tensor Parallel 受 GPU 架构、vLLM 版本、显存和镜像影响。平台宁可统一安全默认值，也不把未验证组合写进服务配置。`dtype`、显存利用率、最大并发、批 token 上限和加载超时仍然可以设置；优云还会做小显存预检。

## 3. 日志与 LLaMA-Factory WebUI 的关系

### 训练日志

是的，主体来自 LLaMA-Factory/Trainer 的 stdout。平台额外插入阶段日志，例如“实例已就绪”“数据集已上传”“产物回传”，并把每行保存到 `task_logs`。Loss 不是另一个独立训练源，而是从同一批 stdout 文本解析后写入 `task_metrics`；同时，`trainer_state.json` 的 `log_history` 用于最终 Best/Final Adapter 选择。

### 推理日志

是的，主体来自 vLLM 进程 stdout/stderr。平台还会加入启动阶段、健康检查、清理失败等后端日志。优云远端日志通过 `read_logs()` 按 `vllm.log` 字节 offset 读取，`_clean_remote_log_line()` 去掉 ANSI 控制符和进程前缀后再写入 `inference_logs`。

### “点点点是否直接生成 train.yaml”

概念上接近，但不是 LLaMA-Factory WebUI 的原样行为：

1. 本项目自己的训练页面把表单字段提交到 `POST /api/tasks`，不是直接调用 LLaMA-Factory WebUI。
2. `LocalLlamaFactoryExecutor.prepare()` 调用 `platform_to_llamafactory_config()`，把 camelCase 业务参数转换成 LLaMA-Factory 字段，再写入隔离目录的 `train.yaml`。
3. 命令行模式只是把同一批参数拼成受控多行命令，发到 `POST /api/tasks/cli`；后端 `cli_parser.py` 解析、白名单校验后，仍然进入同一个任务队列和 YAML 生成路径。
4. 远程优云复用这份 YAML，只改写 `dataset_dir/output_dir` 和远端精度，然后上传。

因此可以说：**平台自己的 UI 是“表单 -> 平台 API -> 受控 YAML -> LLaMA-Factory CLI”，不是“浏览器直接操作 LLaMA-Factory WebUI”。** LLaMA-Factory WebUI 若单独使用，通常也是把表单参数组织成训练配置再调用自身训练入口，但那不是本项目当前页面的调用链。

## Interview wording

### One sentence

我负责梳理并实现了训练与推理的编排链路：训练侧用 SSH/SFTP 将平台生成的 LLaMA-Factory YAML 和数据上传到优云，增量回传 stdout 日志并解析 Loss，完成后下载 Adapter；推理侧用 vLLM 的 LoRA serving、PagedAttention、连续批处理和 KV Cache 能力提供 OpenAI 兼容服务，同时用健康检查和 Prometheus 指标管理运行状态。

### 30-60 second answer

用户在页面提交的不是一段可执行 Shell，而是结构化训练参数。后端先校验已发布数据集和资源，再由统一转换器生成隔离目录下的 `train.yaml`。优云任务创建一次性 GPU 实例，通过 Paramiko SSH/SFTP 上传数据和 YAML，在远端执行 `python -m llamafactory.cli train train.yaml`；远端 stdout/stderr 写入 `train.log`，平台按字节 offset 增量读取，日志原样入库，同时解析 Loss、Step 和进度。训练成功后把 `output` 下载回本地，复用同一套产物收集逻辑登记 Best/Final Adapter。推理服务则用 vLLM 启动 OpenAI 兼容 API，加载 Base Model 加 LoRA Adapter；PagedAttention、连续批处理、Prefix Caching 和 Chunked Prefill 是 vLLM runtime 的优化，不是模型权重本身的能力。高级参数有些被平台锁定，是因为当前只验证了单卡和自动 KV Cache 的兼容边界。

### 待确认

- 你的个人贡献边界需要结合 Git commit、任务单和实际部署记录确认；本笔记只描述仓库已实现的团队能力。
- 生产环境 PostgreSQL/MinIO 是否在你实际运行环境启用，需要查看对应 `.env.production` 和部署日志；本地默认仍可使用 SQLite/本地目录。


## 4. 训练完成后为什么可以先对比、再回传权重

### 4.1 生活化场景

把优云训练机想成“厨房”，平台本地镜像和 MinIO 是“仓库”。训练进程说“菜已经出锅”时，厨房里的 Adapter 文件已经可以被推理进程读取；把整箱菜搬回仓库只是后续同步动作，不应该阻塞第一口试吃。

因此现在的远程链路分成两条并行路径：

```text
训练进程退出
  ├─ 远程检查 adapter_config.json + 权重文件
  │    └─ 通过后：实例保留，效果对比立即可用
  └─ 后台 SSH 回传 output/ + 上传 MinIO
       └─ 完成后：syncState=available；失败：syncState=failed，可重试
```

### 4.2 关键状态

- `remote_ready`：远程 Adapter 已完整，优云实例上的对比可以开始。
- `syncing`：本地回传或 MinIO 上传进行中。
- `available`：本地目录和共享存储都已完成。
- `failed`：后台同步失败；优云实例仍可用于对比时，可以调用 `POST /api/tasks/{task_id}/artifacts/sync` 重试。

### 4.3 核心代码逐段解释

`backend/providers/youyun/remote_training.py` 的 `_inspect_remote_artifacts()` 只执行轻量检查：

```python
# 远程目录，例如 /root/cppt/ft-xxx/output
output = f"{remote_workdir}/output"

# 只检查配置文件和至少一个权重文件，不下载整个目录。
test -f "$OUT/adapter_config.json"
find "$OUT" -maxdepth 2 -type f \\
  \( -name '*.safetensors' -o -name '*.bin' -o -name '*.pt' \)

# 检查通过后，写入 remotePath 和 syncState=remote_ready。
{
    "type": "adapter",
    "variant": "final",
    "path": "本地镜像预期路径",
    "remotePath": "/root/cppt/任务ID/output",
    "syncState": "remote_ready"
}
```

逐行理解：

1. `output` 是服务端按任务 ID 生成的隔离目录，不能由浏览器传入。
2. `adapter_config.json` 表明这是一个可被 PEFT/Transformers 识别的 Adapter 目录。
3. `safetensors/bin/pt` 检查保证目录里不仅有配置，还有实际参数文件。
4. `remotePath` 只用于后端连接优云实例；前端不能凭空提交它。
5. `path` 是未来本地回传后的路径，所以在 `remote_ready` 阶段可以暂时不存在。

### 4.4 对比请求的安全边界

`backend/app.py` 的效果对比接口会先判断：

```python
remote_path = adapter.get("remotePath")
remote_ready = bool(remote_path) and adapter.get("syncState") in {
    "remote_ready", "syncing", "available",
}

if remote_ready:
    runtime_adapter_path = _validate_remote_adapter_path(
        payload.taskId,
        str(remote_path),
    )
else:
    runtime_adapter_path = validate_local_adapter_path(adapter["path"])
```

这里的重点是：

- 远程对比不等于信任任意路径。
- `_validate_remote_adapter_path()` 会把路径限制在 `/root/cppt/{task_id}/` 下。
- 实例释放后，接口仍返回 409，不允许绕过生命周期继续访问。

### 4.5 失败重试场景

```text
T0：训练完成，syncState=remote_ready
T1：用户立刻点击“开始效果对比”，请求发送到保留的优云实例
T2：后台同步 SSH 超时，syncState=failed
T3：用户再次点击“重新同步产物”
T4：POST /api/tasks/{id}/artifacts/sync 重新下载和上传
T5：成功后 syncState=available
```

## 5. WebSocket 日志：为什么不是简单的 setInterval

### 5.1 后端消息循环

核心位置：`backend/app.py` 的 `task_stream()`。

```python
@app.websocket("/api/tasks/{task_id}/stream")
async def task_stream(websocket: WebSocket, task_id: str) -> None:
    if not websocket_authenticated(websocket):
        await websocket.close(code=4401)
        return

    task = store.get(task_id)
    if task is None:
        await websocket.close(code=4404)
        return

    await websocket.accept()
    sent = max(0, int(websocket.query_params.get("afterId", "0")))
```

逐行解释：

1. `@app.websocket` 表示这不是一次 HTTP 请求，而是一条可持续连接。
2. 先鉴权，避免拿任务 ID 就能读取训练日志。
3. 先检查任务存在，再 accept，减少无意义连接。
4. `afterId` 是断线续传游标；例如客户端已经拿到日志 100，重连就带 `afterId=100`。
5. 后端只查询 `id > 100` 的日志，所以不会重复刷屏。

循环内部发送五类消息：

| type | 含义 | 前端动作 |
|---|---|---|
| `log` | 一行持久化日志 | 追加到日志面板 |
| `state` | 状态/进度/currentStep | 更新进度条和任务详情 |
| `metrics` | Loss、Step、学习率 | 更新曲线 |
| `terminal` | completed/failed/stopped | 停止重连，保留最终状态 |
| `heartbeat` | 连接仍然活着 | 防止代理把空闲连接当成死连接 |

### 5.2 为什么先落库再推送

训练器可能继续输出，而浏览器可能刷新、断网或被关闭。正确顺序是：

```text
训练器输出
  ↓
store.append_log(...)
  ↓
数据库中的 task_logs
  ↓
WebSocket 查询 afterId 后推送
```

这样 WebSocket 只是“实时通知”，数据库才是事实来源。刷新页面时，前端先拿任务详情或日志快照，再接增量消息。

### 5.3 前端断线场景

`components/training-module.tsx` 会记录 `lastLogId`：

```ts
const suffix = lastLogId > 0
  ? `?afterId=${lastLogId}`
  : "";

socket = new WebSocket(
  apiWebSocketUrl(`/tasks/${selectedId}/stream${suffix}`),
);
```

场景：

1. 收到日志 100。
2. Wi-Fi 断开。
3. 自动重连，URL 带 `afterId=100`。
4. 后端补发 101、102、103。
5. 前端通过 `seenLogIds` 再做一次幂等保护。

如果 WebSocket 始终不可用，训练页降级为 HTTP 定时刷新；所以“实时”是优先路径，不是唯一数据来源。

## 6. SSE 是什么，和 WebSocket 有什么区别

SSE 是 Server-Sent Events，意思是“服务端持续向浏览器发送事件”。它通常使用 `text/event-stream`，方向是服务端到客户端；WebSocket 则是双向通道。

本项目的分工：

```text
普通 HTTP：创建任务、停止任务、读取详情
WebSocket：训练/推理日志、状态、指标、心跳
SSE：vLLM 流式生成文本
```

场景化理解：

- WebSocket 像电话：两边都可以讲话，适合日志和控制状态。
- SSE 像电台：服务器不断播报，浏览器主要负责接收，适合“模型生成一段就显示一段”。

vLLM 流式链路：

```text
前端发送聊天请求
  ↓
后端代理请求 vLLM /v1/chat/completions，stream=true
  ↓
vLLM 持续返回 data: ... 事件
  ↓
后端解析事件并转发
  ↓
前端逐段追加 assistant.content
```

不要把训练日志和 SSE 混为一谈：当前训练日志明确使用 WebSocket JSON 消息；SSE 主要出现在在线推理的 token 流式响应中。
