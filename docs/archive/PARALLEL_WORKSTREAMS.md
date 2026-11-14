# 并行开发任务说明

更新时间：2026-08-24

本文档用于后续并行开发对话，所有方向共享同一组产品约束。

## 共同约束

- 一级菜单固定为：概览、数据集、微调任务、模型管理、推理服务。
- Agent/RAG 本阶段只做阅读说明，不实现；RAG 由其他同事负责。
- 公网推理、API Key、计费、专业模型评测本阶段不做。
- 不读取、输出或提交 `.env`、密钥、数据库密码和 SSH 私钥。
- 不重置用户已有改动，不操作 PostgreSQL、MinIO、Redis、Nginx 等基础设施容器。
- 修改后至少运行相关测试、Python 编译、TypeScript 检查；前端改动还要运行 `npm run build`。
- 所有新增状态必须有明确的 API 来源，不能用静态假数据冒充真实运行状态。

## 方向一：概览与固定侧栏

目标：建立系统运行概览，固定左侧导航，仅中心内容滚动。

首版指标：后端健康、训练队列、任务状态、GPU/CPU/内存、推理服务状态、PostgreSQL/MinIO 可用性、优云实例状态、最近失败任务。

暂不做：公网计费、用户级用量账单、复杂监控告警平台。

主要文件：`app/page.tsx`、`app/globals.css`，必要时增加概览 API。

## 方向二：流程文档与小白教程

目标：用 Mermaid 梳理数据集、训练、模型管理、推理、优云和 Agent/RAG 参考流程。

每一步必须说明：解决什么问题、调用哪个 API、对应哪个文件/函数、主要参数、返回数据、流式或非流式、失败和兜底。

主要产物：Markdown 文档，不实现 Agent/RAG。

## 方向三：优云训练保留实例与效果对比

目标：优云训练完成后不自动销毁，先保留实例并完成训练前后对比，用户确认后释放。

建议状态：`training_completed_waiting_compare`、`comparing`、`awaiting_release_confirmation`、`released`、`debugging`。

释放入口：用户明确点击“结束任务并释放资源”或“销毁实例”。异常、超时和失败仍需要清理策略与日志。

主要文件：`backend/providers/youyun/remote_training.py`、`backend/providers/youyun/instance_manager.py`、`backend/app.py`、训练详情前端。

## 方向四：推理动态上下文与优云适配

目标：根据基模配置读取最大上下文长度，联动限制最大输出，并为优云推理复用 Provider 抽象。

约束：公网暴露、API Key、价格和计费留到后续；本阶段先跑通内网/受控环境。

主要文件：`backend/inference_service.py`、`backend/providers/youyun/`、`components/inference-module.tsx`。

## 方向五：数据集修复与进度显示

目标：结构化错误报告、一键低风险修复、手动编辑、修复预览、重新校验、原文件保留。

已确认规则：

- 原文件永不覆盖。
- 手动编辑采用结构化编辑与原始 JSONL 编辑双模式，先做出可用版本再迭代体验。
- 自动修复只处理低风险格式问题；语义问题必须人工确认。
- 已发布或已被任务引用的数据集不直接修改，生成新的修复版本。
- 修复完成后必须重新校验，通过后才可发布或训练。

主要文件：`backend/app.py`、`backend/store.py`、`lib/types.ts`、`app/page.tsx`。

## 并行合并顺序

1. 先合并后端状态/API契约，再合并前端页面。
2. 同一文件发生冲突时，以共享状态和 API 字段为准，不直接覆盖其他方向改动。
3. 每个方向完成后提供：改动文件、接口变化、测试结果、未完成事项。
