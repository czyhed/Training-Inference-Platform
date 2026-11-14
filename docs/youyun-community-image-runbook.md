# 优云社区镜像接入运行手册

## 只读发现

平台通过 `DescribeCommunityImages` 查询当前区域的社区镜像组，并展开每个组的 `Data` 版本。训练页面优先检索 `LLaMAFactory`、`Qwen-VL`、`视觉`；文生图页面检索 `Diffusers`、`LoRA`、`Qwen-Image`。查询阶段不创建实例。

## 选择规则

- 必须存在真实 `CompShareImageId`。
- 状态必须是 `Available`、`Normal` 或 `Ready`。
- 镜像必须声明容器能力，并支持当前 GPU；否则只展示不可选。
- 社区镜像初始状态为 `unverified`，不能直接进入训练。

## 验证规则

视觉训练页面点击“验证此环境”后，再点击“创建临时实例并探针”。探针检查 Python、Torch/CUDA、Transformers/PEFT/TRL/LLaMA-Factory、Processor、LoRA 注入和最小前向。默认硬超时为 1800 秒，可通过 `YOUYUN_VISION_PROBE_TIMEOUT_SECONDS` 调整。无论成功、失败还是超时，都会执行实例释放并核验清理结果。

探针通过且清理状态为 `released` 后，镜像状态写为 `verified`，训练页面才允许提交。清理失败会保留阻塞状态，禁止重复创建实例。

## 当前已发现示例

在 `cn-wlcb / cn-wlcb-01` 区域可发现真实社区版本，例如：

- `LLaMAFactory-WebUI v1.0`：`compshareImage-13yp608dxe53`
- `LLaMA-Factory-YingHuoAI v1.0`：`compshareImage-1ahwi14871de`
- `LlamaFactory纯净版-微调使用 v1.1`：`compshareImage-1fhzfisvmkgn`

这些 ID 只是目录发现结果，仍需按当前 GPU 和视觉训练依赖完成探针，不应直接视为可训练环境。

## 自建发布兜底

如果现有社区镜像缺少视觉训练依赖，使用 `cuda132_torch2130_py312` 构建私有镜像，安装 LLaMA-Factory、Qwen-VL、PEFT、TRL 和必要运行库；清理密钥、缓存、历史日志后保存私有镜像。验证通过后再从优云控制台发布到社区，发布审核以优云当前规则为准。
