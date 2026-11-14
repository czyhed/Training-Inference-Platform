# Model Management Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make visual-understanding and text-to-image model management follow the main NLP asset-management interaction model without changing backend APIs.

**Architecture:** Add a shared presentational shell for model asset lists, filters, summary, tables, and detail entry points. Domain adapters keep visual probe/readiness and image-generation compatibility/catalog logic while mapping records into the shared shell.

**Tech Stack:** Next.js 14, React, TypeScript, lucide-react, Node `node:test` source-level regression tests.

**Spec:** `docs/superpowers/specs/2026-08-31-model-management-unification-design.md`

## Global Constraints

- Preserve existing `/api/vision/*`, `/api/model-assets`, and model registration contracts.
- Do not start GPU training, vLLM, or real Youyun instances.
- Keep unrelated uncommitted work intact.
- Use the existing light workspace skin and compact table/drawer patterns.

---

### Task 1: Shared Model Asset Shell

**Files:**
- Create: `components/model-asset-management-shell.tsx`
- Test: `lib/model-management-unification.test.mjs`

**Interfaces:**
- Consumes normalized base/trained rows, summary counts, domain labels, and callbacks.
- Produces a shared header, summary, tabs, search toolbar, base/trained tables, empty states, and detail trigger callbacks.

- [ ] Write failing source tests asserting shared tabs, separate base/trained sections, detail triggers, and empty-state actions.
- [ ] Run `node --test lib/model-management-unification.test.mjs` and confirm failure because the shell is absent.
- [ ] Implement typed shell props and render the NLP-equivalent structure with accessible tabs and table rows.
- [ ] Run the focused test and confirm it passes.

### Task 2: Visual Understanding Adapter

**Files:**
- Modify: `components/vision-model-management-view.tsx`
- Test: `lib/inference-vision.test.mjs`

**Interfaces:**
- Consumes current `VisionManagedModel` records and existing probe/catalog/register callbacks.
- Produces normalized rows for the shared shell and preserves visual readiness detail, catalog, and registration dialogs.

- [ ] Add a failing assertion that visual model management renders shared base/trained sections and the four primary asset tabs.
- [ ] Replace the current bespoke list layout with the shared shell while retaining domain-specific readiness filters in the toolbar.
- [ ] Keep model probe, catalog, register, and detail drawer actions wired to existing callbacks.
- [ ] Run visual inference/model-management tests and confirm they pass.

### Task 3: Text-to-Image Adapter

**Files:**
- Modify: `components/image-generation-models.tsx`
- Test: `lib/image-generation.test.mjs`

**Interfaces:**
- Consumes current image-generation asset records and existing catalog/register/detail state.
- Produces normalized base/trained rows with LoRA/QLoRA and Youyun readiness columns.

- [ ] Add a failing assertion that image-generation model management renders the shared asset tabs and separate base/trained sections.
- [ ] Replace the current single asset table with the shared shell and map image-generation-specific fields into row/detail renderers.
- [ ] Preserve recommendation directory, Tab-to-fill registration fields, and Adapter detail behavior.
- [ ] Run image-generation model-management tests and confirm they pass.

### Task 4: Integration Verification

**Files:**
- Modify: `app/page.tsx` only if adapter props require a wiring change.

- [ ] Run all focused model-management and inference frontend tests.
- [ ] Run `npx tsc --noEmit`.
- [ ] Run `npm run build`.
- [ ] Check `http://127.0.0.1:3001/` returns HTTP 200 and inspect the model-management routes without starting remote compute.
