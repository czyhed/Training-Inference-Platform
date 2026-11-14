# 文生图阶段四预检快照 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 持久化文生图优云启动前预检结果，并在确认任务时用 TTL 与配置指纹阻止过期或失配快照绕过校验。

**Architecture:** 预检根据服务端解析出的模型、Adapter lineage、资源和运行参数计算指纹，将结果保存到服务 config 的 `preflightSnapshot`。默认有效期 10 分钟，可由环境变量调整；确认时复用未过期且指纹一致的快照，否则重新执行预检。前端展示快照时间和过期状态。

**Tech Stack:** FastAPI, SQLite-compatible inference store, Pydantic, React 18, Node test runner, Python unittest.

**Global Constraints**

- 预检和快照读取不创建优云实例、不建立 SSH、不下载模型。
- 服务端重新解析模型和 Adapter 路径，客户端不能覆盖 lineage 或路径。
- 过期/失配/阻断快照不得确认任务或进入队列。
- 不读取、提交或输出真实密钥、密码或 SSH 私钥。

### Task 1: 快照指纹与 TTL

**Files:** `backend/image_generation_api.py`, `backend/test_image_generation_api.py`

- [x] 写失败测试：预检保存 `preflightSnapshot`，包含 fingerprint、checkedAt、expiresAt；未过期且配置未变时可复用，过期或资源变化时重新校验。
- [x] 实现稳定指纹、TTL 和服务 config 快照持久化。
- [x] 保持现有分项 `checks` 和 `ready` 响应兼容。

### Task 2: 确认闸门与前端状态

**Files:** `backend/image_generation_api.py`, `components/image-generation-service.tsx`, `components/image-generation-tasks.tsx`, tests

- [x] 写失败测试：确认复用有效快照；快照过期或 fingerprint 失配时确认前重新检查；失败保持 `awaiting_confirmation`。
- [x] 实现确认闸门并返回快照状态。
- [x] 前端显示最近检查时间、有效期和“检查已过期”提示。

### Task 3: 文档、全量验证与提交

**Files:** `README.md`, `CURRENT_STATE.md`

- [x] 记录快照 TTL、失效条件和真实实例验收边界。
- [x] 运行后端/前端全量测试、compileall、TypeScript、build、diff check。
- [ ] 提交阶段四改动。
