# Unified Console UI and Defense Document Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the platform easier for first-time users by unifying typography, spacing, navigation, empty states, form guidance, status feedback, and service workflows, then deliver a beginner-friendly but technically rigorous defense document.

**Architecture:** Preserve existing module APIs and business behavior. Improve the shared visual layer in `app/globals.css`, add lightweight navigation/empty-state affordances in existing React components, and keep service-scoped generation/evaluation flows intact. Produce Markdown as the source document and a rendered DOCX copy for presentation use.

**Tech Stack:** Next.js/React/TypeScript, FastAPI, existing CSS tokens, Python `python-docx`, Markdown, LibreOffice render QA.

## Global Constraints

- Preserve unrelated dirty-worktree changes.
- Do not start real Youyun instances or GPU training.
- Keep existing API contracts compatible.
- Prefer readable typography over dense dashboards: body 13–14px, helper text 11–12px, headings 16–24px.
- Keep actions discoverable through labels and familiar lucide icons.
- Do not expose raw commands, credentials, tracebacks, or internal paths in user-facing errors.

---

### Task 1: Shared UI Tokens and Responsive Baseline

**Files:**
- Modify: `app/globals.css`
- Test: `lib/image-generation.test.mjs`

**Interfaces:**
- Preserve existing class names and component contracts.
- Add shared CSS variables and predictable breakpoints used by all modules.

- [ ] **Step 1: Add CSS contract assertions**

Assert the stylesheet contains the shared font scale, compact spacing tokens, focus state, and mobile layout rules.

- [ ] **Step 2: Implement shared tokens**

Add `--font-body`, `--font-small`, `--font-title`, `--space-*`, and consistent button/input/table rules without changing business logic.

- [ ] **Step 3: Add responsive overflow protection**

Ensure tables scroll inside their container, title rows wrap, and action buttons remain reachable below 700px.

- [ ] **Step 4: Run frontend tests and TypeScript**

Run `node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --test lib/image-generation.test.mjs` and `node node_modules/typescript/bin/tsc --noEmit`.

### Task 2: First-use Navigation, Empty States, and Form Guidance

**Files:**
- Modify: `app/page.tsx`
- Modify: `components/image-generation-service.tsx`
- Modify: `components/image-generation-training.tsx`
- Modify: `components/image-generation-models.tsx`
- Modify: `components/inference-module.tsx`

**Interfaces:**
- Keep existing fetch paths and callbacks.
- Add next-step copy and primary actions only where data is empty or blocked.

- [ ] **Step 1: Add source-level tests for clear primary actions**

Check that empty states contain an action label such as `创建`, `上传`, `登记`, or `重新检查`, and that service detail tabs remain discoverable.

- [ ] **Step 2: Implement empty-state action affordances**

Replace ambiguous empty messages with a short explanation plus one clear next action, preserving existing permission and loading behavior.

- [ ] **Step 3: Normalize helper text and field labels**

Use short labels, units, recommended values, and explicit required markers. Avoid adding instructional paragraphs inside cards.

- [ ] **Step 4: Run focused UI tests and type checking**

Run the full `lib/image-generation.test.mjs` suite and TypeScript checking.

### Task 3: Service and Evaluation Workflow Polish

**Files:**
- Modify: `components/image-generation-evaluation.tsx`
- Modify: `components/image-generation-tasks.tsx`
- Modify: `components/image-generation-service.tsx`
- Modify: `components/vision-training.tsx` or the existing visual-understanding task/detail component discovered by `rg`

**Interfaces:**
- Keep service IDs bound in all generation/evaluation requests.
- Keep evaluation metrics auxiliary and clearly labeled.

- [ ] **Step 1: Add regression checks for service binding and status copy**

Assert that generation/evaluation views render service context, show progress/failure/suggested action, and never offer an unscoped service selector in embedded mode.

- [ ] **Step 2: Improve interaction hierarchy**

Place the primary action first, group secondary actions, keep destructive actions visually separate, and make the current tab obvious.

- [ ] **Step 3: Improve evaluation readability**

Use compact metric cards, visible dataset validation counts, sample comparison labels, and a concise explanation of CLIP limitations.

- [ ] **Step 4: Verify desktop and mobile browser states**

Inspect `1280x720` and `390x844` with the local frontend; do not trigger remote execution.

### Task 4: Defense Document Source

**Files:**
- Create: `docs/答辩文档-训推平台.md`

**Interfaces:**
- The document is self-contained and references only implemented behavior or explicitly marked limitations.

- [ ] **Step 1: Draft the beginner-first narrative**

Cover the problem, user journey, system modules, terminology, and a five-minute demo path.

- [ ] **Step 2: Add technical depth**

Cover architecture, data flow, model/Adapter lineage, training modes, service-scoped generation/evaluation, Youyun boundaries, failure handling, security, metrics, tests, and evidence.

- [ ] **Step 3: Add product analysis**

Include user personas, pain-point mapping, advantages, non-goals/current gaps, future roadmap, risks, and likely defense questions with answers.

- [ ] **Step 4: Audit claims against repository state**

Mark real Youyun acceptance as pending and distinguish fake/local verification from production acceptance.

### Task 5: Rendered DOCX Deliverable and Verification

**Files:**
- Create: `output/训推平台答辩文档.docx`
- Create: `output/训推平台答辩文档.pdf` (optional render artifact)

**Interfaces:**
- DOCX follows the `standard_business_brief` preset with readable headings, restrained color, real lists, and no clipped tables.

- [ ] **Step 1: Load workspace document dependencies and mark artifact operation**

Use the bundled runtime and run `mark_artifact_operation_started.mjs` before authoring.

- [ ] **Step 2: Build DOCX from the Markdown source**

Apply explicit page geometry, styles, heading hierarchy, tables, callouts, and footer metadata.

- [ ] **Step 3: Render DOCX to PNG/PDF**

Run `render_docx.py` and inspect every page image for clipping, overlap, broken glyphs, and bad page breaks.

- [ ] **Step 4: Run final verification**

Run focused backend tests, frontend tests, TypeScript, `python -m compileall -q backend`, and `git diff --check`.
