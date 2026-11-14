# Antfu UI 基础层 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 建立轻量可复用的 UI 基础层，并将平台壳层与概览页面迁移到统一的按钮、状态、面板和响应式契约。

**Architecture:** 新增纯展示型共享组件，组件不请求 API、不推断业务状态；页面上层继续负责数据获取与回调。先收敛 CSS 令牌和交互语义，再逐页迁移，避免改变训练、推理和优云数据流。

**Tech Stack:** Next.js 14、React 18、TypeScript、CSS、Node test runner。

**Spec:** `docs/superpowers/specs/2026-09-05-antfu-ui-foundation-design.md`

## Global Constraints

- 不修改后端 API、数据库、训练执行器和优云 Provider。
- 不启动真实 GPU、vLLM、优云实例或训练任务。
- 共享组件只展示上层传入的数据，不自行推断状态。
- 类型与常量显式声明，避免复杂内联类型和隐式 CSS 覆盖。
- 每个任务先写失败测试，再写最小实现。

---

### Task 1: UI foundation components and tokens

**Files:**
- Create: `components/ui/page-header.tsx`
- Create: `components/ui/status-badge.tsx`
- Create: `components/ui/empty-state.tsx`
- Create: `components/ui/section-panel.tsx`
- Create: `components/ui/ui-types.ts`
- Modify: `app/globals.css`
- Test: `lib/ui-foundation.test.mjs`

**Interfaces:**
- `PageHeader({ title, description, actions })`
- `StatusBadge({ tone, label, detail? })`
- `EmptyState({ kind, title, description, action? })`
- `SectionPanel({ title, description?, actions?, children })`

- [ ] **Step 1: Write failing tests**

  Assert that the four components exist, expose accessible names, and the CSS contains shared focus, button and responsive token rules.

- [ ] **Step 2: Run the focused test and verify it fails**

  Run: `node --test lib/ui-foundation.test.mjs`
  Expected: FAIL because the shared component files do not exist.

- [ ] **Step 3: Implement the smallest component contracts**

  Keep components stateless and render only their props. Define `StatusTone` and `EmptyStateKind` in `ui-types.ts`.

- [ ] **Step 4: Add shared CSS tokens and focus rules**

  Add a single final CSS section using existing colors and dimensions. Do not remove unrelated legacy selectors in this task.

- [ ] **Step 5: Run the focused test and TypeScript check**

  Run: `node --test lib/ui-foundation.test.mjs` and `npx tsc --noEmit`
  Expected: PASS with no TypeScript errors.

### Task 2: Platform shell migration

**Files:**
- Modify: `components/platform-shell.tsx`
- Modify: `app/globals.css`
- Test: `lib/platform-shell.test.mjs`

**Interfaces:**
- Preserve the existing `PlatformShell` props and navigation callbacks.
- Add stable `aria-current`, `aria-controls`, and keyboard-visible focus styles without changing routing.

- [ ] **Step 1: Add failing assertions**

  Assert that active navigation exposes `aria-current`, the subnavigation has a stable label, and the shell contains the responsive content wrapper class.

- [ ] **Step 2: Run the focused test and verify it fails**

  Run: `node --test lib/platform-shell.test.mjs`
  Expected: FAIL on the new accessibility assertions.

- [ ] **Step 3: Implement shell semantics**

  Keep navigation data explicit and add only semantic attributes and class hooks. Do not add new state or API calls.

- [ ] **Step 4: Add responsive shell rules**

  At 1024px keep the sidebar visible without covering content; at 700px stack subnavigation and let content use the full width.

- [ ] **Step 5: Run focused tests and build checks**

  Run: `node --test lib/platform-shell.test.mjs lib/navigation-responsive.test.mjs` and `npx tsc --noEmit`.

### Task 3: Overview migration and regression verification

**Files:**
- Modify: `components/overview-module.tsx`
- Modify: `app/globals.css`
- Test: `lib/platform-overview.test.mjs`
- Test: `lib/ui-layout-regressions.test.mjs`

**Interfaces:**
- Preserve the existing `onOpenModule` callback and snapshot data flow.
- Use shared `SectionPanel`, `StatusBadge`, `EmptyState`, and `PageHeader` only for presentation.

- [ ] **Step 1: Add failing assertions**

  Assert that the overview includes the shared page header marker, explicit loading/empty/error states, and accessible labels for active training and inference entries.

- [ ] **Step 2: Run the focused test and verify it fails**

  Run: `node --test lib/platform-overview.test.mjs lib/ui-layout-regressions.test.mjs`
  Expected: FAIL on shared component markers.

- [ ] **Step 3: Migrate overview markup**

  Replace repeated wrapper markup only; keep all existing API calls, refresh behavior, and navigation callbacks unchanged.

- [ ] **Step 4: Verify desktop and narrow viewport contracts**

  Run: `npm run test:frontend`, `npx tsc --noEmit`, and `npm run build`.

- [ ] **Step 5: Browser smoke check**

  Check the local page at 1440px, 1024px, 700px, and 390px. Verify navigation, overview cards, loading state, and empty/error copy remain readable and clickable.
