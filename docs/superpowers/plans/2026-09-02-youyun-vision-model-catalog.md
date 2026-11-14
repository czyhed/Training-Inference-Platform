# 优云视觉基模目录映射实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 识别优云共享模型目录中的视觉基模，将 Repo ID、挂载路径、能力、模板和显存建议映射到视觉训练与推理选择器。

**Architecture:** 在现有视觉模型管理链路中增加一个可配置的优云视觉模型目录清单解析层。目录清单优先来自 `YOUYUN_BASE_MODEL_CATALOG`，没有配置时保留现有静态候选；每个识别出的模型通过统一能力推断和 Qwen 模板解析进入现有模型注册/前置检查链路。前端使用后端返回的可用性与资源建议，不把文生图、视频、语音模型误归为视觉理解模型。

**Tech Stack:** Python FastAPI、SQLite store、TypeScript React、Node test、Python unittest。

**Spec:** 用户确认的“识别优云模型库视觉基模并映射到应用”方案。

## Global Constraints

- 不创建优云实例，不启动训练；仅执行目录清单解析和本地接口验证。
- 不改变现有 `YOUYUN_ALLOW_MODEL_DOWNLOAD` 默认关闭策略。
- 只将具备 `vision_language` 能力且模板可解析的模型归入视觉基模。
- 保留用户工作区已有修改，不回滚无关文件。

---

### Task 1: 增加优云视觉模型目录解析器

**Files:**
- Create: `backend/youyun_vision_catalog.py`
- Test: `backend/test_youyun_vision_catalog.py`

- [ ] **Step 1: Write the failing tests**

覆盖目录条目解析、视觉/非视觉过滤、路径规范化、显存建议和重复 Repo 去重。

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest backend.test_youyun_vision_catalog -v`
Expected: FAIL because the catalog module does not exist.

- [ ] **Step 3: Implement the minimal parser**

提供 `parse_youyun_vision_catalog(raw)` 和 `load_youyun_vision_catalog()`，支持 JSON 数组或 JSONL，条目字段包含 `name`、`repoId`、`path`、`sizeGb`、`tags`。通过模型名、Repo ID、路径和标签识别视觉语言模型；排除 Stable Diffusion、FLUX、Krea、TTS、视频和纯文本模型；为 Qwen2/Qwen2.5/Qwen3-VL 生成对应模板。

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest backend.test_youyun_vision_catalog -v`
Expected: PASS.

### Task 2: 接入视觉模型 API 与存储模型资产

**Files:**
- Modify: `backend/vision_api.py`
- Modify: `backend/app.py`
- Modify: `.env.example`
- Test: `backend/test_vision_api.py`

- [ ] **Step 1: Write the failing tests**

验证 `GET /api/vision/youyun-catalog` 返回识别后的视觉基模，并验证已配置 Repo ID 的模型在 `/api/vision/models?provider=youyun` 中标记为可用。

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest backend.test_vision_api -v`
Expected: FAIL because the catalog endpoint and merge logic do not exist.

- [ ] **Step 3: Implement the minimal integration**

增加只读目录接口；将目录条目与已登记模型按 Repo ID 合并，补充 `availableOnYouyun`、`remotePath`、`sizeGb`、`template`、`resourceHint`。不自动写入数据库，避免目录刷新产生不可逆资产变更；用户点击登记后仍复用现有注册流程。

- [ ] **Step 4: Run focused backend tests**

Run: `python -m unittest backend.test_youyun_vision_catalog backend.test_vision_api -v`
Expected: PASS.

### Task 3: 接入前端目录与训练/推理选择器

**Files:**
- Modify: `components/vision-model-management-view.tsx`
- Modify: `components/vision-training-module.tsx`
- Modify: `components/vision-inference-service-module.tsx`
- Modify: `lib/vision-base-model-presets.ts`
- Test: `lib/vision-model-management-view.test.mjs`
- Test: `lib/vision-training-provider.test.mjs`
- Test: `lib/inference-vision.test.mjs`

- [ ] **Step 1: Write the failing tests**

验证目录中出现 Qwen3-VL 32B Thinking FP8、Qwen2.5-VL 72B 和 Qwen3-Omni 时，前端显示优云路径、显存建议和能力；文生图/视频/TTS 条目不进入视觉选择器。

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm run test:frontend -- lib/vision-model-management-view.test.mjs lib/vision-training-provider.test.mjs lib/inference-vision.test.mjs`
Expected: FAIL because the UI has no remote catalog state.

- [ ] **Step 3: Implement the minimal UI integration**

增加目录加载、筛选和登记入口；训练和推理选择器复用统一模型选项，并在资源不足时显示阻塞原因。Qwen3-VL Thinking 版本先标记为“需远端探针”，不直接宣称训练已验证。

- [ ] **Step 4: Run focused frontend tests**

Run: `npm run test:frontend -- lib/vision-model-management-view.test.mjs lib/vision-training-provider.test.mjs lib/inference-vision.test.mjs`
Expected: PASS.

### Task 4: 全量验证与运行服务检查

**Files:**
- No source changes expected.

- [ ] **Step 1: Run backend tests**

Run: `python -m unittest backend.test_youyun_vision_catalog backend.test_vision_api backend.test_vision_training -v`

- [ ] **Step 2: Run frontend checks**

Run: `npm run test:frontend` and `cmd /d /c "npx.cmd tsc --noEmit"`.

- [ ] **Step 3: Query local endpoints**

Verify `GET http://127.0.0.1:8010/api/vision/youyun-catalog`, `GET http://127.0.0.1:8010/api/vision/models?provider=youyun`, and `GET http://127.0.0.1:3100/` return successful responses.

- [ ] **Step 4: Report limitations**

明确说明优云模型库完整清单若未通过 `YOUYUN_BASE_MODEL_CATALOG` 或远端接口提供，应用只能映射已发现条目，不会凭空声称全量同步。
