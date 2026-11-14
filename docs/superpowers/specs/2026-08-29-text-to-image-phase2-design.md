# 文生图阶段二设计

日期：2026-08-29

## 目标

在阶段一配置快照的基础上，实现基于 Diffusers 的异步文生图任务链路。任务在用户确认后才申请优云一次性实例，远端生成结果后下载到本地任务目录，并持久化日志、进度、结果和实例清理状态。

## 范围

### 1. 任务模型与状态机

- 创建任务只保存配置，状态为 `awaiting_confirmation`，不创建实例。
- 确认后进入 `queued`，由独立的 `ImageGenerationRuntime` 串行消费。
- 状态包括 `awaiting_confirmation`、`queued`、`preparing`、`running`、`collecting`、`completed`、`stopping`、`stopped`、`failed`。
- 任务 JSON 持久化在运行时根目录，后端重启后将未完成任务标记为失败，并保留可重试信息。
- 只允许失败或停止任务重试；重试会清空旧结果和旧实例句柄。

### 2. Diffusers 优云执行器

- Provider 复用现有 `YouyunInstanceManager`、SSH 客户端和资源清理契约。
- 创建实例前再次校验服务快照、资源字段和模型/Adapter lineage。
- 远端优先使用预置 Diffusers 镜像、显式模型路径和共享挂载；无完整模型时按配置使用 ModelScope/Hugging Face 下载兜底。
- 只执行平台生成的 Python worker，不接受客户端 Shell 命令。
- Worker 写入 `status.json`、`worker.log` 和 PNG 文件；状态至少包含当前步骤、总步骤、生成结果文件名和错误信息。
- 远端进程提前退出时读取日志尾部并立即失败；成功、失败、停止和取消都执行实例清理。
- 清理失败必须落盘，后续停止/恢复操作可以再次尝试清理。

### 3. API

- `POST /api/image-generation/tasks`：创建待确认任务。
- `GET /api/image-generation/tasks`：按创建时间倒序列出任务。
- `GET /api/image-generation/tasks/{id}`：返回任务详情、日志、进度和结果元数据。
- `POST /api/image-generation/tasks/{id}/confirm`：重新校验并入队。
- `POST /api/image-generation/tasks/{id}/stop`：停止排队任务或请求远端取消。
- `POST /api/image-generation/tasks/{id}/retry`：重新入队失败/停止任务。
- `GET /api/image-generation/tasks/{id}/results/{result_id}`：读取已下载 PNG。
- `GET /api/image-generation/tasks/{id}/logs`：返回结构化日志列表。

任务参数包括服务 ID、Prompt、Negative Prompt、宽高、Steps、CFG、Seed、采样器和生成数量。所有参数在创建和确认时都执行类型、范围和服务快照校验。服务记录中的模型和 Adapter 是唯一可信血缘来源。

### 4. 前端

- 文生图工作区增加生成任务视图，与阶段一数据集、模型和服务配置视图并列。
- 创建表单包含服务选择、Prompt、Negative Prompt、宽高、Steps、CFG、Seed、采样器和生成数量。
- 创建按钮只产生待确认任务；确认按钮明确提示优云费用并触发远端执行。
- 任务详情显示阶段时间线、日志、进度、结果图、下载和失败重试。
- 前端不显示或编辑远端命令，不在创建阶段调用优云实例 API。

## 数据流

1. 前端从已保存的文生图服务读取模型、Adapter 和资源快照。
2. 创建任务 API 校验服务状态和生成参数，写入 `awaiting_confirmation` 任务。
3. 确认 API 再次读取服务和模型目录，校验通过后将任务写为 `queued` 并唤醒 Runtime。
4. Runtime 获取共享 GPU 锁，调用 Diffusers Provider 创建一次性实例。
5. Provider 建立 SSH，解析模型、上传受控 worker 配置并启动远端进程。
6. Runtime 轮询远端 `status.json` 和日志，更新任务状态与进度。
7. 生成完成后 Provider 下载 PNG 和元数据，Runtime 写入 `completed`；任何终态都执行实例清理。

## 失败处理

- 参数或服务快照失效返回 4xx，不进入队列。
- 优云配置、库存、SSH、模型下载或 Diffusers 导入失败写入任务错误和日志。
- 远端 worker 非零退出或状态文件报告错误时立即失败，不等待无意义的总超时。
- 实例清理失败不会覆盖原始错误；任务保留 `cleanupState=failed` 和实例 ID，允许再次停止/恢复。
- 后端重启时将 `preparing`、`running`、`collecting`、`stopping` 标记为失败，避免遗留任务被静默遗忘。

## 非目标

- 不在本机启动 Diffusers、ComfyUI 或 GPU 服务。
- 不创建真实优云实例作为本阶段自动测试的一部分。
- 不支持公开 API、多 GPU、自动扩缩容、工作流编辑器或任意远端命令。
- 不把 QLoRA 运行时兼容性扩大为未经探针验证的通用能力。

## 验收

- 后端单元测试覆盖状态机、确认前零实例调用、Provider 调用顺序、轮询、提前退出、结果收集、停止、重试和清理失败恢复。
- 前端源码测试覆盖任务表单、确认分离、时间线、结果和错误重试。
- `python -m unittest backend.test_image_generation_api backend.test_image_generation_runtime backend.test_youyun_diffusers`
- `node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --test lib/image-generation.test.mjs lib/image-generation-runtime.test.mjs`
- `python -m compileall -q backend scripts`
- `npx.cmd tsc --noEmit`
- `git diff --check`
