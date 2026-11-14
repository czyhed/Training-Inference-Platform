# Youyun Resource Health and Training Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Filter temporarily unavailable Youyun candidates and make Pod-to-UHost fallback consistent for visual understanding and image-generation training.

**Architecture:** Add a small process-local cooldown registry at the provider boundary. Resource resolution consults it while preserving explicit selections, and remote runners retry a healthy alternate mode only after cleanup. Both APIs return the actual selected mode and fallback diagnostics.

**Tech Stack:** Python/FastAPI, existing Youyun client and fake-provider tests, existing React resource selectors.

**Spec:** `docs/superpowers/specs/2026-09-06-youyun-resource-health-fallback-design.md`

## Global Constraints

- Do not create or operate real GPU instances in tests.
- Preserve user changes and existing stable provider error codes.
- A resource handle must be cleaned before a replacement candidate is attempted.
- Explicit location selections are not silently changed; only `auto` may fall back.
- Health cooldown is bounded and process-local; no credentials or provider secrets are persisted.

### Task 1: Health registry and resource resolver

**Files:**
- Create: `backend/providers/youyun/resource_health.py`
- Modify: `backend/app.py`
- Test: `backend/test_youyun_resource_health.py`, `backend/test_youyun_provider.py`

- [ ] Write failing tests for cooldown expiry, transient create failure marking, healthy candidate filtering, and Pod-to-UHost auto resolution.
- [ ] Run the focused tests and confirm missing registry/resolver behavior fails.
- [ ] Implement `YouyunResourceHealthRegistry.mark_failure`, `.mark_success`, `.is_blocked`, `.filter_candidates` with monotonic expiry.
- [ ] Add resolver metadata and candidate filtering without changing explicit-mode semantics.
- [ ] Run focused tests to green.

### Task 2: Provider runtime fallback

**Files:**
- Modify: `backend/providers/youyun/instance_manager.py`
- Modify: `backend/providers/youyun/remote_training.py`
- Modify: `backend/providers/youyun/image_generation_training.py`
- Test: `backend/test_youyun_remote_training.py`, `backend/test_youyun_image_generation_training.py`

- [ ] Write failing fake-manager tests for Pod create failure followed by UHost success and cleanup-before-retry.
- [ ] Implement bounded alternate candidate attempts, mark transient failures, and persist `attemptedModes`/`fallbackReason`.
- [ ] Keep non-transient, explicit-selection failures terminal.
- [ ] Run focused runner tests to green.

### Task 3: API and UI candidate contract

**Files:**
- Modify: `backend/app.py`, `backend/vision_api.py`, `backend/image_generation_training_api.py`
- Modify: `components/vision-training-module.tsx`, `components/image-generation-training.tsx`
- Test: `backend/test_vision_api.py`, `backend/test_image_generation_training_api.py`, `lib/vision-training-provider.test.mjs`, `lib/image-generation.test.mjs`

- [ ] Write failing tests asserting cooled Pod candidates are omitted/marked unavailable and auto mode exposes UHost fallback.
- [ ] Return `healthStatus`, `healthCooldownUntil`, `fallbackCandidates`, and actual `instanceMode` in resource snapshots.
- [ ] Update both selectors to consume API candidates and display the fallback reason without hardcoded provider probing.
- [ ] Run backend and frontend focused tests to green.

### Task 4: Documentation and verification

**Files:**
- Modify: `.env.example`, `README.md`, `CURRENT_STATE.md`, `ARCHITECTURE.md`

- [ ] Document cooldown defaults, auto fallback, explicit-selection behavior, and rollback flag.
- [ ] Run `python -m pytest backend -q` with remote fallback disabled in the environment, `npm.cmd run test:frontend`, `npx.cmd tsc --noEmit`, `npm.cmd run build`, and local API health check.
- [ ] Confirm no real Youyun instance was created and inspect git diff for unrelated changes.
