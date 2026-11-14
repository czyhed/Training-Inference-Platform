# Diffusers SDXL LoRA 训练脚本稳定下载源研究

> 研究日期：2026-09-06  
> 目标文件：`examples/text_to_image/train_text_to_image_lora_sdxl.py`  
> 目标版本：Hugging Face Diffusers `v0.30.3`  
> 范围：只核对公开来源、版本固定性、内容一致性和下载链路风险；不修改生产代码或配置。

## 结论

短期可直接采用以下下载顺序：

1. **主地址：jsDelivr，固定到实际 commit**

   <https://cdn.jsdelivr.net/gh/huggingface/diffusers@c9ff360966327ace3faad3807dc871a4e5447501/examples/text_to_image/train_text_to_image_lora_sdxl.py>

2. **备用地址：GitHub Raw，固定到同一 commit**

   <https://raw.githubusercontent.com/huggingface/diffusers/c9ff360966327ace3faad3807dc871a4e5447501/examples/text_to_image/train_text_to_image_lora_sdxl.py>

3. 两个地址下载后都必须校验：

   ```text
   SHA-256: f72ce7d06b1613a1f7e051cb8c8401f210791e0b6e57ccc7fd6223dbbc92f949
   文件大小: 55,568 bytes
   ```

jsDelivr 适合作为当前外部主源：其官方说明支持按 GitHub commit 提供文件，静态 commit 使用长期缓存和永久对象存储，并通过多 CDN 提供服务。GitHub Raw 是上游官方源，但本次训练已经在该链路出现 TLS EOF，因此只作为备用。

生产环境更稳妥的最终方案仍然是：首次从上述固定来源拉取并校验后，存入平台自有对象存储或直接预置到训练镜像；训练任务优先读取本地/内部副本，不把第三方公网可达性放在每次创建 GPU 实例的关键路径上。

## 版本与提交核对

Diffusers 官方仓库的 `v0.30.3` 是 annotated tag：

```text
tag object: d2af4c634b52044280d4638068d6b09d6d9d4981
peeled commit: c9ff360966327ace3faad3807dc871a4e5447501
```

核对来源：

- 官方 Git 仓库：<https://github.com/huggingface/diffusers.git>
- 官方 release/tag：<https://github.com/huggingface/diffusers/releases/tag/v0.30.3>
- 实际 commit：<https://github.com/huggingface/diffusers/commit/c9ff360966327ace3faad3807dc871a4e5447501>

因此 URL 应固定到 `c9ff360...`，而不是 tag object `d2af4c...`。直接把 tag object 当成 commit 拼入 Raw/CDN URL 会得到 `404`。

虽然 `v0.30.3` tag 地址当前也返回相同内容，但 Git tag 在仓库管理员强制移动后理论上可能指向不同提交；完整 commit SHA 的不可漂移性更强。

## 内容一致性

2026-09-06 对下列地址按响应原始字节计算 SHA-256，结果完全一致：

| 来源 | 固定方式 | HTTP | 字节数 | SHA-256 |
| --- | --- | ---: | ---: | --- |
| GitHub Raw | `v0.30.3` tag | 200 | 55,568 | `f72ce7d06b1613a1f7e051cb8c8401f210791e0b6e57ccc7fd6223dbbc92f949` |
| GitHub Raw | `c9ff360...` commit | 200 | 55,568 | `f72ce7d06b1613a1f7e051cb8c8401f210791e0b6e57ccc7fd6223dbbc92f949` |
| jsDelivr | `v0.30.3` tag | 200 | 55,568 | `f72ce7d06b1613a1f7e051cb8c8401f210791e0b6e57ccc7fd6223dbbc92f949` |
| jsDelivr | `c9ff360...` commit | 200 | 55,568 | `f72ce7d06b1613a1f7e051cb8c8401f210791e0b6e57ccc7fd6223dbbc92f949` |

这证明本次核验时 jsDelivr 返回的是 Diffusers 官方 `v0.30.3` 脚本原文，而不是另一个版本或经过修改的副本。运行时仍需重新做 SHA-256 校验，不能把一次核验当成永久信任。

## 稳定性证据与边界

jsDelivr 官方 README 明确说明：

- GitHub 资源支持 `/gh/user/repo@version/file`，其中 `version` 可以是 release、commit 或 branch；
- 静态版本和 commit hash 的缓存策略是长期缓存，并写入永久 S3 存储；
- 服务使用多 CDN，并说明在中国城市附近有节点；
- GitHub 单文件默认限制为 20 MB，本脚本只有约 54 KiB，不触发该限制。

来源：<https://github.com/jsdelivr/jsdelivr#github>、<https://github.com/jsdelivr/jsdelivr#caching>、<https://github.com/jsdelivr/jsdelivr#multi-cdn>。

实际响应头也符合静态资源语义：commit URL 返回 `Cache-Control: public, max-age=31536000, s-maxage=31536000, immutable`；相较之下，GitHub Raw commit URL 本次返回 `Cache-Control: max-age=300`。

本机网络下的轻量探测结果仅作为可达性样本，不代表优云机房 SLA：

| 地址 | 连续请求 | 成功率 | 首次时延 | 后续时延范围 |
| --- | ---: | ---: | ---: | ---: |
| jsDelivr commit | 5 | 5/5 | 1266 ms | 272-280 ms |
| GitHub Raw commit | 5 | 5/5 | 1128 ms | 263-281 ms |

需要注意：jsDelivr 是可信的开源 CDN，但仍是 Hugging Face 之外的第三方服务。其公开说明不等于面向本项目或中国大陆的合同 SLA；DNS、跨境链路、TLS 和 CDN 节点仍可能短时失败。地址替换不能代替有限重试、超时、备用源和校验。

## Hugging Face Hub 与国内镜像核对

本次通过 Hugging Face Hub 公共 API 检索后，没有找到由 `huggingface` 或 `diffusers` 官方命名空间维护、且包含此 `v0.30.3` 训练脚本的等价 Hub 仓库。`diffusers/tools` 是公开的官方工具仓库，但其文件树中没有目标脚本。

核对入口：

- <https://huggingface.co/api/models?author=huggingface&search=diffusers&limit=100>
- <https://huggingface.co/api/datasets?author=huggingface&search=diffusers&limit=100>
- <https://huggingface.co/api/spaces?author=huggingface&search=diffusers&limit=100>
- <https://huggingface.co/api/models/diffusers/tools/tree/main?recursive=true&expand=false>

因此不能把 GitHub 路径机械改写成 `huggingface.co/.../resolve/...` 或 `hf-mirror.com/.../resolve/...`。`hf-mirror.com` 官方首页说明它通过 `HF_ENDPOINT` 镜像 Hugging Face Hub 上实际存在的模型和数据集仓库；它不会凭空镜像 GitHub 仓库中的 examples 文件。来源：<https://hf-mirror.com/>。

不建议将无明确运营主体、无固定内容承诺的 `ghproxy`、`fastgit` 或类似 Raw 代理作为生产主源。即使加入 SHA-256 能降低内容被替换的风险，也无法解决可用性、证书、域名失效和审计责任问题。

## 推荐下载策略

训练任务解析脚本时建议采用以下顺序：

```text
镜像预置路径
  -> 平台内部对象存储/共享缓存（commit 作为对象 key）
  -> jsDelivr commit URL
  -> GitHub Raw commit URL
  -> 明确失败并释放实例
```

外部下载建议至少满足：

1. 每个来源设置连接超时和总超时；只做有限次数指数退避，不无限等待 GPU 实例。
2. 对网络错误、TLS EOF、HTTP `408/429/5xx` 才切换来源；`404` 或哈希不匹配直接记录为来源异常。
3. 下载到临时文件，SHA-256 匹配后再原子移动到目标路径。
4. 日志记录来源 URL、commit、字节数、实际 SHA-256、重试次数和耗时。
5. 哈希不匹配时绝不执行脚本，也不要继续尝试同一来源的 tag/branch URL。
6. 无论下载最终成功或失败，都保持现有实例释放与复核流程。

## 推荐决策

- **现在每次外部兜底从哪里下**：先从 jsDelivr 的 `c9ff360...` commit URL 下载，失败再走 GitHub Raw 的同 commit URL。
- **如何保证拿到正确脚本**：只接受 SHA-256 `f72ce7d06b1613a1f7e051cb8c8401f210791e0b6e57ccc7fd6223dbbc92f949`。
- **如何彻底降低再次失败概率**：把校验通过的脚本同步到平台自有存储或优云镜像，后续训练从内部固定对象读取；公网源只用于补齐缓存。
