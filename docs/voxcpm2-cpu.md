# VoxCPM2 CPU 语音部署

模型管理和推理服务中的「语音合成」先展示已部署模型列表，点击模型进入「模型详情、在线使用、任务记录、音频素材、接口文档」。`/speech` 提供同一工作台；`/speech?tab=use`、`/speech?tab=api` 可直接进入使用和文档。

## 测试工作台

- 声音设计：文本及括号中的声音/情绪描述。
- 音色克隆：上传参考音频，无需填写参考文本。
- 音频续写：上传提示音频并填写其对应文本。
- 克隆与续写组合：音色参考与上下文提示可分别指定。
- 参数：推理步数、CFG、随机种子、最小/最大长度、文本规范化、异常比例重试和流式输出。范围与默认值以 `backend/speech_contracts.py`、在线参数表和 OpenAPI 为准。
- 任务：批量排队、取消、重新生成、参数复用、状态/文本筛选、两条结果对比、参数和记录导出、终态任务删除。
- 音频：原始文件上传、标准化预览、使用和引用保护；输出波形、倍速试听、WAV 下载、耗时/时长/实时率。浏览器录音需要 HTTPS 或 localhost，HTTP 地址使用文件上传。

未部署独立 ZipEnhancer 降噪模型或 LoRA Adapter；这些选项不会伪装为已启用。模型训练与其他模型部署不属于该推理测试界面。

## API 调用

在线文档在模型详情的「接口文档」，包含 Python 标准库完整客户端、cURL 和已登录浏览器 JavaScript 示例，示例采用当前表单参数。机器可读定义：`GET /api/speech/openapi.json`。

XShipAI 部署的外部调用必须使用工作台会话：向工作台 `POST /api/login`（username/password 与匹配的 Origin），`GET /api/session` 取得 csrf，再 `POST /api/launch/cppt`（Origin、X-CSRF-Token），随后携带会话 Cookie 调用训推平台 `/api/speech/*`。不要把私有 worker key 作为浏览器/API 用户密钥。会话过期会由网关重定向到登录页。

先 `POST /assets?name=...` 上传音频原始字节（不是 multipart），再在 `/jobs` 或 `/batches` 中使用返回的 `aud-` ID；不接受客户端路径或远程音频 URL。单任务 `/jobs` 保留忙碌时 409 的兼容行为，`/batches` 接受 `{items:[...]}` 将任务排队。流式任务通过详情中的 `chunkCount` 查询片段数量，再按序读取 `/jobs/{id}/chunks/{index}`；各片段是独立 WAV 文件，完整结果始终通过 `/jobs/{id}/audio` 下载。

## 边界和状态

浏览器 → 平台 `/api/speech`（沿用平台登录边界）→ 私网语音 worker → 可终止的 CPU 模型子进程。worker 不发布主机端口，必须使用至少 32 字符的随机密钥；禁止把密钥放入 `NEXT_PUBLIC_*`。平台主进程不安装 VoxCPM，也不把语音服务注册为 vLLM。

管理员部署配置是服务登记来源；worker 是运行状态的唯一来源。初始 stopped，启动进入 loading，完成真实合成预热才进入 ready。停止会终止模型进程、释放内存并取消正在进行的任务。异常和超时进入 failed，需要重新启动。自动启动只在容器启动时生效，手动停止后本次容器生命周期内保持停止。

语音任务为异步 HTTP 202。每次执行一个任务，最多等待 5 个，每条最多 300 字符、4–20 步、运行限时 20 分钟（不含排队）；重复 requestId 和相同参数返回原任务，不重复推理，不同参数冲突。任务 JSON、WAV、流式片段与素材保存在独立结果目录，重启后未完成任务标记失败，保留最近 50 条。停止服务取消全部未完成任务；取消单个运行任务后重新加载模型并继续其他排队任务。平台数据库没有新增迁移。旧任务缺失的新参数按默认值解释，原有幂等标识继续有效。

音频素材最多 100 个、每个最大 10 MB、时长 0.5–30 秒。FFmpeg 在限时内解码为 24 kHz 单声道，限制协议及格式以拒绝远程播放列表。已被保留任务引用的素材不能删除。模型与结果目录在升级时保留，已有任务和音频不迁移也不覆盖。

## 安装

1. Python 3.11+ 执行 `python scripts/download_voxcpm2.py /absolute/model/path`。下载器读取官方 ModelScope manifest，逐文件验证大小和 SHA256；全部通过后写入 verified-manifest.json。目录以只读方式挂载，不允许运行期间改动权重。
2. 构建 `docker build -f deploy/docker/speech.Dockerfile -t xshipai/cppt-speech:voxcpm2-cpu .`。官方 VoxCPM 2.0.3，PyTorch/torchaudio 2.11.0 CPU 包；解析后的完整依赖记录在镜像 `/app/runtime-packages.txt`。
3. 参考 `deploy/speech.compose.yaml` 合入现有部署，结果目录须由 UID 1000 可写。限制 6 CPU / 12 GiB 内存，无 GPU、Docker socket 或云资源申请。
4. 平台后端设置 `XUNTUI_SPEECH_WORKER_URL=http://cppt-speech:8091` 和与 worker 相同的 `XUNTUI_SPEECH_WORKER_KEY`。外层网关必须保留原平台身份认证，尤其是后端 `AUTH_MODE=disabled` 的部署。
5. 构建并替换平台前后端，启动语音服务。在模型管理 → 语音合成检查状态，然后在测试页生成短句、播放和下载 WAV。

官方来源：[模型](https://www.modelscope.cn/models/OpenBMB/VoxCPM2)、[源码](https://github.com/OpenBMB/VoxCPM)、[安装文档](https://voxcpm.readthedocs.io/en/latest/installation.html)。按官方 `device="cpu", optimize=False, load_denoiser=False` 使用原始模型。输出 48 kHz PCM WAV；CPU 耗时随文字长度和步数增加，不保证实时速度。

## 验证和回滚

边界、失败测试、资源生命周期和回滚计划在实现前记录于 `.scratch/voxcpm2-cpu/spec.md`。先建立失败测试，再实现验证、幂等启动/提交、忙碌冲突、停止与超时清理、重启恢复、路径隔离和 API 错误封装。

验证：`python -m unittest backend.test_speech_runtime backend.test_speech_api backend.test_speech_worker backend.test_speech_workbench -v`；`npm run test:frontend`；`npm run build`。真实验收覆盖四种模式、流式片段、上传解码、API 鉴权示例、列表与详情导航、桌面/390px 布局及取消/重启。单元测试中的假模型仅测试进程状态，不作为真实推理成功证据。

部署前备份 compose、后端环境配置和原镜像 ID。回滚时恢复原前后端镜像和配置，仅停止/移除 cppt-speech 容器，保留模型及结果目录；不清空任何平台数据库或已有卷。日志只记录运行异常，不向用户暴露内部路径或密钥。
