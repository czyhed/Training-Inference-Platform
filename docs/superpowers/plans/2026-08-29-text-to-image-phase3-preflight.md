# 文生图阶段三启动前预检 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不创建优云实例的前提下，为文生图服务和任务增加可重复的优云启动前预检。

**Architecture:** 后端抽取纯函数式预检聚合器，重新读取服务模型/Adapter lineage，并通过注入的优云资源解析器校验镜像、区域、库存和价格。任务确认复用同一预检结果；前端显示分项状态和时间戳。所有测试使用 Fake Store、Fake Youyun resolver 和 Fake Provider。

**Tech Stack:** FastAPI, Pydantic, asyncio, Next.js 14, React 18, Node test runner, Python unittest.

**Spec:** `docs/superpowers/specs/2026-08-29-text-to-image-phase2-design.md` plus the approved phase-three design in chat.

## Global Constraints

- 预检不创建优云实例、不建立 SSH、不下载模型。
- 任务确认前必须重新执行预检；失败时任务保持 `awaiting_confirmation`。
- 模型和 Adapter lineage 只能由服务端重新解析，客户端不能提交路径覆盖。
- 不读取、提交或输出真实密钥、密码或 SSH 私钥。

### Task 1: 后端预检契约

**Files:**
- Modify: `backend/image_generation_api.py`
- Extend: `backend/test_image_generation_api.py`

- [x] 写失败测试：成功预检返回分项状态和时间戳；预检不触发 Provider/实例；模型、Adapter、资源或价格失败返回 `blocked`。
- [x] 实现 `run_service_preflight` 聚合函数和 `POST /api/image-generation/services/{service_id}/preflight`。
- [x] 让任务确认调用同一预检并在失败时保持 `awaiting_confirmation`。
- [x] 运行文生图 API 测试。

### Task 2: 前端预检状态

**Files:**
- Modify: `components/image-generation-service.tsx`
- Modify: `components/image-generation-tasks.tsx`
- Extend: `lib/image-generation.test.mjs`
- Extend: `lib/image-generation-runtime.test.mjs`

- [x] 写源码回归测试：预检按钮、分项状态、刷新检查、确认阻断文案。
- [x] 服务配置页增加预检结果区域和“启动前检查”。
- [x] 任务页在确认前展示服务预检状态，失败时不显示可确认操作。
- [x] 运行前端测试和 TypeScript 检查。

### Task 3: 文档与全量验收

**Files:**
- Modify: `README.md`
- Modify: `CURRENT_STATE.md`

- [x] 记录预检边界、状态含义和真实实例验收仍需人工执行。
- [x] 运行后端全量测试、前端测试、compileall、TypeScript、build 和 diff check。
- [ ] 提交阶段三改动。
