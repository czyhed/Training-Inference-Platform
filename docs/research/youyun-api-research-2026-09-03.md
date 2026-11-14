# 优云 GPU API 与镜像/基模优化研究

> 研究日期：2026-09-02  
> 范围：优云智算（CompShare）GPU API 官方文档、官方 SDK 示例及本项目已有的优云适配代码。  
> 约束：本次仅做文档和接口研究，不创建实例、不启动训练、不产生费用。

## 结论摘要

1. **模型仓库、镜像和 GPU 库是三套不同的目录**：`DescribeModelRepositoryModels` 返回共享模型仓库；机型/库存接口决定当前地域是否有可用 GPU；镜像接口决定容器环境。不能把“目录里有模型”当成“当前实例可以挂载或可以训练”。
2. **创建实例前必须做四级预检**：地域与机型库存、镜像状态/能力、基座模型路径或下载策略、端口/磁盘配置。预检失败时不应创建实例。
3. **实例操作是异步的**：创建、启动、停止、释放后必须轮询实例详情/状态；遇到进行中错误（如 `226603`）应先查询状态再决定是否重试，不能盲目重复提交。
4. **本项目应采用“官方镜像优先、社区镜像探针后可用、自建镜像兜底”的策略**。社区镜像仅在真实探针通过且实例释放成功后标记为可训练/可推理。
5. **密钥只放后端**：官方 Python SDK 示例使用 `public_key`、`private_key` 创建客户端，前端只调用本项目后端 API，不暴露优云密钥。

## 官方接口核对

### 1. 模型仓库：DescribeModelRepositoryModels

官方页面：<https://www.compshare.cn/docs/gpus/instance/describemodelrepositorymodels>

请求参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `Action` | String | 是 | 固定为 `DescribeModelRepositoryModels` |
| `Region` | String | 是 | 地域，例如 `cn-wlcb` |
| `name` | String | 否 | 按模型名称模糊搜索，不区分大小写 |
| `tags` | String | 否 | 按标签筛选，例如 `LLM` |

响应的外层字段为 `Action`、`RetCode`、`Message` 和 `Models`。`RetCode=0` 表示请求成功；模型元素的字段以页面的 `Models` 元素结构为准，应用侧应保留未知字段，至少记录模型名称、仓库/模型标识、标签、大小和可挂载路径（如果返回）。

**工程含义**：目录接口只负责发现候选基模。应用必须再用镜像能力探针和当前 GPU 显存检查，才能把候选标记为“可训练”或“可推理”。视觉理解筛选应只接受带视觉语言能力/`VL` 标签的条目，排除纯文本、扩散、视频和语音模型；文生图筛选应单独维护 Diffusers/SDXL/FLUX/Qwen-Image 等目录。

### 2. 地域、机型和库存

官方 GPU API 首页（含“获取可用机型列表”“获取机型族列表”“查询资源库存/容量”）：<https://www.compshare.cn/docs/gpus/instance/describemodelrepositorymodels>

官方接口在请求中要求 `Region`。可用机型列表和资源容量查询返回的机型、GPU 数量和库存状态是**地域实时数据**，不能在前端写死。库存不足、机型停售或地域不匹配时，创建接口即使参数格式正确也会失败。

**工程含义**：

- 训练提交前调用库存/容量接口，并把 `region + gpu_type + gpu_count` 固化到任务快照。
- “无库存”只能显示为资源阻断，不应自动切换到另一种 GPU，除非用户明确允许并且显存/价格预算仍满足。
- 训练和推理的推荐机型分开：训练按最小显存和磁盘吞吐排序，推理按显存、启动时间和小时价排序。

### 3. 镜像目录与镜像能力

官方文档目录中的“实例镜像”章节：<https://www.compshare.cn/docs/gpus/instance/describemodelrepositorymodels>

官方 API 支持平台镜像、社区镜像和自定义/私有镜像的目录查询。镜像条目通常包含镜像 ID、名称/版本、状态及镜像数据版本；创建实例时使用真实的镜像 ID，而不是展示名称。

**镜像分层策略（本项目建议）**：

| 优先级 | 镜像来源 | 可用条件 | 适用场景 |
| --- | --- | --- | --- |
| P0 | 官方平台镜像 | 状态可用，且 CUDA/Torch 与模型要求匹配 | 默认训练和推理 |
| P1 | 社区镜像 | 真实临时实例探针通过，释放状态为 `released` | 缺少 LLaMA-Factory、Diffusers 等依赖时 |
| P2 | 自建私有镜像 | 构建清单固定、依赖锁定、探针通过 | 社区镜像缺包或版本漂移 |

探针至少检查：Python/Torch/CUDA 版本、Transformers/PEFT/TRL/LLaMA-Factory 或 Diffusers、Processor/Tokenizer、LoRA 注入、最小前向、模型路径可读、端口可监听。探针无论成功、失败还是超时都必须进入 `finally` 释放实例并复核释放状态。

### 4. 创建、查询、停止和释放实例

官方创建示例页面：<https://www.compshare.cn/docs/gpus/instance/createcompshareinstance>  
官方 GPU API 文档首页（左侧包含实例状态、停止/释放、访问地址等接口）：<https://www.compshare.cn/docs/gpus/instance/describemodelrepositorymodels>

创建请求的关键字段（以页面的 `CreateCompShareInstance` 请求参数为准）包括：地域、机型、镜像 ID、实例名称、系统盘/数据盘、GPU 数量及网络/计费相关配置。应用必须使用接口返回的真实 `CompShareInstanceId` 保存任务关联关系。

推荐状态机：

```text
预检 -> CreateCompShareInstance
     -> Pending/Creating（轮询实例详情）
     -> Running
     -> 探针/训练/推理
     -> Stop 或 Release
     -> Stopped/Released（再次查询确认）
```

不要把“创建接口返回成功”当成“实例可用”。只有实例详情显示运行态、软件端口可访问且探针通过后，才允许进入训练/推理。

**幂等与重试建议**：官方页面未给出客户端幂等 token；因此本项目应自行实现幂等键（任务 ID + 操作类型）和操作锁。提交前先查询任务是否已有实例 ID；收到超时、网络断开或进行中错误时先查询实例状态，再决定重试。释放操作同样要幂等：已释放/不存在应视为清理成功，未知状态继续轮询并告警。

### 5. 软件端口映射和访问地址

官方接口目录包含“查询软件端口映射列表”和“获取实例上软件的访问地址”：<https://www.compshare.cn/docs/gpus/instance/describemodelrepositorymodels>

端口映射不能假设为固定端口。实例创建后先查询软件端口映射，再获取访问地址；返回的公网/内网地址、端口和协议应写入实例运行记录。推理服务健康检查应访问映射后的地址，而不是直接拼接容器端口。

本项目建议：

- 训练服务和推理服务分别登记端口用途（API、WebUI、监控）。
- 只暴露必要端口；对外 API 通过本项目后端代理并加入 API Key/限流。
- 健康检查分为 TCP 可达、HTTP `/health`、模型就绪三层，全部通过才显示“可用”。

### 6. 价格与费用控制

官方文档目录包含“查询创建实例价格”“查询实例当前计费信息”“变更实例计费方式”：<https://www.compshare.cn/docs/gpus/instance/describemodelrepositorymodels>

价格是地域、机型、GPU 数量、磁盘和计费方式的函数，不能使用静态常量。创建前展示价格查询结果并记录快照；训练/探针预算应包含启动、下载、训练、释放全过程的上限。

**兜底机制**：

1. 创建后立即写入 `expires_at` 和最大运行时长。
2. 训练/推理任务使用 `try/finally` 清理；前端关闭页面不影响后端清理。
3. 后台看门狗按任务状态和最后心跳扫描，超时自动停止/释放。
4. 清理后再次查询实例，只有确认 `Released` 才结束任务；清理失败进入高优先级告警和人工重试队列。

## 面向本项目的镜像与基座模型方案

### 视觉理解

- 默认基座：优先选择优云模型仓库中可发现且有视觉语言标签的 Qwen2.5-VL/Qwen3-VL 小规格条目；2B/3B 作为 24GB 显存的训练起点，7B 以上只在库存和显存检查通过时展示。
- 训练镜像：优先官方 `cuda128_torch280_py312`（项目现有验证记录）；缺少依赖时使用已探针验证的 LLaMA-Factory 社区镜像。
- 推理镜像：固定 CUDA/Torch/Transformers 版本，启用量化或半精度；服务启动前验证 Processor、权重路径和最小图片请求。
- 模型路径：优先使用实例内已缓存/挂载路径；没有路径时才允许后端下载，并设置白名单 Repo ID、磁盘配额和下载超时。

### 文生图

- 默认基座：按显存和速度分级，SDXL/Flux/Qwen-Image 小规格优先；LoRA 训练与推理必须使用同一底模家族和 tokenizer/text encoder 组合。
- 训练镜像：优先官方 `cuda132_torch2130_py312`（项目现有验证记录）；社区镜像必须先做 Diffusers、xformers/attention、LoRA、VAE、最小采样探针。
- 推理镜像：固定 Diffusers、Torch、CUDA 和模型 revision；启动时校验 UNet/Transformer、VAE、text encoder、scheduler 文件完整，避免“服务启动但首张图失败”。
- 性能：首请求单独计时并预热；批量请求设置并发上限和队列；生成完成后立即将临时实例释放策略交给看门狗。

### 统一推荐算法

对每个候选组合计算：

```text
score = capability_match * 0.35
      + probe_pass * 0.30
      + gpu_fit * 0.20
      + inventory * 0.10
      + price_efficiency * 0.05
```

其中 `probe_pass` 是硬门槛：未探针或探针失败的社区镜像不得进入“可提交”列表。推荐结果必须同时显示镜像 ID、基座 Repo ID、远端路径、显存建议、探针时间和失败原因。

## 错误码、日志和诊断

官方错误码入口：<https://www.compshare.cn/docs/gpus/compshareerrorcode>  
官方文档示例中明确出现 `226603`：实例操作正在进行中（如启动/停止/释放尚未完成）。

应用侧应持久化：`Action`、`RetCode`、`Message`、请求参数摘要（脱敏）、实例 ID、地域、镜像 ID、模型 Repo ID、重试次数、开始/结束时间。错误展示分三类：

- **可重试**：网络超时、查询暂时失败、操作进行中；指数退避并先查询状态。
- **需换资源**：无库存、机型不支持、镜像与 GPU 不兼容；重新执行推荐，不重复提交同一创建请求。
- **不可重试**：参数错误、权限/签名错误、镜像 ID 不存在；直接阻断并显示修复建议。

## 鉴权与安全

官方 Python SDK 示例（各接口页面“请求示例”）：<https://www.compshare.cn/docs/gpus/instance/describemodelrepositorymodels>

示例通过 `ClientConfig` 配置 `public_key`、`private_key`、`region`，再调用 `client.invoke(...)`。生产实现应复用官方 SDK 或严格按官方签名实现；私钥仅保存在后端环境变量/密钥服务，禁止进入 Next.js bundle、浏览器 localStorage、任务日志和错误响应。建议给本项目后端增加优云 API 调用审计 ID，便于把一次业务任务对应到所有 Describe/Create/Release 请求。

## 实施顺序与验收标准

1. **目录同步**：定时刷新机型、库存、镜像和模型仓库；保留原始响应和抓取时间。
2. **组合预检**：训练/推理提交前完成 GPU、镜像、模型、磁盘、端口和价格检查。
3. **真实探针**：仅对用户确认的候选组合创建临时实例；超时/失败也必须自动释放。
4. **异步编排**：统一创建、轮询、健康检查、训练/推理、停止/释放状态机。
5. **费用兜底**：最大时长、心跳看门狗、清理复核、告警和人工重试。

验收指标：

- 创建前阻断所有已知的“无库存/镜像不兼容/模型路径缺失/端口不可用”组合。
- 探针失败实例 100% 进入释放流程，并可证明最终状态为 `Released`。
- 同一任务重复点击不会创建第二个实例。
- 训练和推理首请求成功率、平均启动时延、单位任务费用均有记录，可按镜像/基座模型对比。
- 前端不暴露优云密钥，日志可按任务 ID 完整还原一次调用链。
