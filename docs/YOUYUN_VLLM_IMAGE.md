# 优云预装 vLLM 镜像验收

生产优云推理默认要求使用已经安装 vLLM 的镜像。启动流程会在 SSH 建立后记录 Python、Torch、CUDA、vLLM 和 `nvidia-smi` 信息；只有预检通过后才上传 Adapter。

## 镜像版本来源

镜像清单与版本以优云控制台 / `DescribeCompShareImages` 返回为准。平台不在代码里维护版本白名单：
`GET /api/youyun/inference-images` 过滤出官方（非社区）vLLM 镜像后按版本号降序返回，默认选中
**版本最高的通用镜像**；`YOUYUN_INFERENCE_IMAGE_ID` 可显式覆盖该默认。优云发布新版本时平台自动跟随，
无需改代码或发版。截至 2026-09-07，优云官网提供的官方 vLLM 镜像版本为 **v0.25.1**。

镜像名或标签中含 `5090` 的镜像被视为 RTX 5090 专用，仅在所选 GPU 为 5090 时可选——这是真实的架构约束，
与版本无关。同理，P40（`sm_61`）在 GPU 目录中标记为不支持推理。

## GPU 兼容结论

V100S 的 Volta 架构（计算能力 `7.0 / sm_70`）处于当前 vLLM 镜像的支持下限，**V100S（32GB）可以作为
远程推理服务 GPU 使用**，单卡推理是当前支持边界。

这是一项基于硬件架构的静态兼容判断，不等同于任一具体镜像版本已经完成真实 `/health` 和首个请求验收。
首次正式使用某个镜像版本仍应按本文“镜像验收标准”采集运行时指纹并保留日志。

V100S 服务建议使用以下配置：

| 项目 | 建议值 | 说明 |
| --- | --- | --- |
| `dtype` | `float16` | V100S 不应选择 `bfloat16` |
| 权重量化 | `none` | 首次验收先不启用 BitsAndBytes |
| KV Cache dtype | `auto` | 不使用 FP8 KV Cache |
| Tensor Parallel | `1` | 当前远程 Provider 按单卡运行 |
| 最大上下文 | `4096` 起步 | 根据模型大小和显存占用再调高 |
| GPU 显存利用率 | `0.75` 起步 | 避免启动阶段把显存压满 |
| 并发/批处理 | 默认值起步 | 先确认单请求稳定后再增加 |

适配范围上，0.5B、3B、7B 级 LoRA 推理更适合作为首轮验证对象；14B 及更大模型需要结合上下文长度、并发和 Adapter 大小重新评估。不要把 P40 的经验直接套用到 V100S：P40 是 `sm_61`，而当前 PyTorch/CUDA 训练镜像明确不包含该架构；V100S 的 `sm_70` 处于当前推理镜像的支持下限。

## 生产实测记录

- **官方 v0.25.1 镜像不含 `ninja`**：vLLM 启动 warmup 阶段编译 flashinfer 采样算子时会以
  `FileNotFoundError: 'ninja'` 崩溃。平台已在远端 vLLM 环境就绪后自动检测并安装（`pip` + 软链到
  `/usr/local/bin`，尽力而为、失败不阻断启动），见 `backend/providers/youyun/inference.py`
  的 `_ensure_remote_ninja`。实测 2 秒完成，v0.25.1 + LoRA 顺利拉起。
- **v0.12.0 镜像分发异常**：`compshareImage-1k99uunq6tuw` 在 cn-bj2-03 两次开通均卡在 Install Fail，
  同区同规格的 LLaMA-Factory 镜像正常。若优云仍在清单中返回该版本，选择前需注意此风险。

## 镜像验收标准

在优云镜像中固定一套兼容组合，并用镜像实际提供的 Python 路径作为 `XUNTUI_REMOTE_VLLM_PYTHON`：

```bash
PY=/usr/local/miniconda3/envs/py312/bin/python
$PY --version
$PY -c 'import torch; print(torch.__version__); print(torch.version.cuda); print(torch.cuda.is_available()); print(torch.cuda.device_count())'
$PY -c 'import vllm; print(vllm.__version__)'
nvidia-smi
```

验收时还应确认：

- `python -m vllm.entrypoints.openai.api_server --help` 能正常退出；
- `pip check` 无依赖冲突；
- Torch 的 CUDA 编译版本与镜像驱动兼容；
- SSH 登录用户具备读取模型缓存、写入 `/root/cppt/inference` 的权限；
- 镜像启动后公网 SSH 转发信息能在实例状态中稳定返回。

针对 V100S，还应额外确认：

- 运行时指纹中的 GPU 名称为 `Tesla V100S`（或优云返回的等价名称）；
- Torch 能报告 `torch.cuda.is_available() == True`，且未出现“不兼容当前 PyTorch 安装”的架构警告；
- vLLM 启动参数使用 `--dtype float16`，没有 `bfloat16` 或 `fp8`；
- `/health` 通过后再发送一条最小非流式请求，确认模型和 LoRA Adapter 均已加载。

## 平台配置

在生产 `.env.production` 中填写镜像 Python，关闭启动时安装：

```env
XUNTUI_REMOTE_VLLM_PYTHON=/usr/local/miniconda3/envs/py312/bin/python
XUNTUI_REMOTE_VLLM_INSTALL=0
# 可选：留空则推理镜像下拉自动默认选中优云最新官方 vLLM 镜像。
YOUYUN_INFERENCE_IMAGE_ID=
# 优云 Provider 的兜底镜像 ID（客户端级默认，非推理下拉默认）。
YOUYUN_IMAGE_ID=<预装 vLLM 的优云镜像 ID>
```

`YOUYUN_INFERENCE_IMAGE_ID` 只影响推理创建表单的默认选中项；不设置时默认取优云返回的版本最高的
通用官方 vLLM 镜像。只有需要把生产钉在某个特定版本时才填。

可先运行镜像/库存联通性检查：

```bash
python -m backend.providers.youyun.check
```

若镜像暂时缺少 vLLM，仅在临时环境显式设置 `XUNTUI_REMOTE_VLLM_INSTALL=1`。此时安装会作为远端后台任务运行，日志会显示安装尾部，取消启动会终止安装进程；生产默认不走该兜底路径。

## 发布顺序

1. 在优云控制台确认官方 vLLM 镜像的当前版本与镜像 ID（推理使用官方镜像，不需要自制镜像）。
2. 用 `backend.providers.youyun.check` 验证镜像 ID、区域、GPU 规格和库存。
3. 确认生产 `.env.production` 的 `XUNTUI_REMOTE_VLLM_PYTHON`；仅在需要钉版本时设置 `YOUYUN_INFERENCE_IMAGE_ID`。
4. 发布平台 tag，启动一个最小模型服务，确认日志出现 `runtime.diagnostic.end`、`vllm.version` 和 `health_check_passed`。
5. 验证停止服务后实例删除，并将本次实际使用的镜像版本记录到发布单。
