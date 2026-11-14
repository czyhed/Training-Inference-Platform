# Service-Scoped Inference API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Bind visual and image-generation workflows to service details and expose rotatable, authenticated service APIs.

**Architecture:** Extend the existing inference service store with hashed key metadata and reuse current runtime proxies. Add small external route adapters in the inference, vision, and image-generation routers; update detail components to manage key state and use the selected service implicitly.

**Tech Stack:** FastAPI, SQLite, Pydantic, Next.js, React, TypeScript.

**Spec:** `docs/superpowers/specs/2026-09-02-service-scoped-inference-api-design.md`

## Global Constraints

- Preserve existing internal API paths and payloads.
- Never persist or log plaintext API keys.
- External errors must not expose local paths, remote handles, or credentials.
- Do not start GPU training, vLLM, or cloud instances for tests.

---

### Task 1: API key persistence and authentication

**Files:**
- Modify: `backend/inference_service.py`
- Test: `backend/test_inference_service.py`

- [x] Write the failing store test for issue, authenticate, and rotate.
- [x] Run `pytest backend/test_inference_service.py -k api_key_is_generated_once_and_rotated -q` and confirm missing-method failure.
- [x] Add a migration-safe `api_key_hash` and `api_key_created_at` schema path, plus `issue_api_key`, `authenticate_api_key`, and `revoke_api_key` methods.
- [x] Return `apiKey`, `createdAt`, and `hasApiKey` only from explicit key operations; keep normal service serialization secret-free.
- [x] Run the focused test and the full inference-store test class.

### Task 2: External service API routes

**Files:**
- Modify: `backend/inference_service.py`
- Modify: `backend/vision_api.py`
- Modify: `backend/image_generation_api.py`
- Test: `backend/test_inference_service.py`, `backend/test_vision_api.py`, `backend/test_image_generation_api.py`

- [x] Add key management routes under `/api/inference/services/{service_id}/api-key`.
- [x] Add authenticated external visual chat and image-generation endpoints that delegate to existing runtime logic.
- [x] Enforce workload type and running-state checks with sanitized 401/409 responses.
- [x] Add route coverage for missing and invalid keys plus key lifecycle behavior.

### Task 3: Service-detail UI and dataset binding

**Files:**
- Modify: `components/vision-inference-service-module.tsx`
- Modify: `components/vision-understanding-module.tsx`
- Modify: `components/image-generation-service.tsx`
- Modify: `app/globals.css`

- [x] Add API tab controls for issue/copy/rotate and display only masked key metadata after reload.
- [x] In visual service detail, route image-to-text and evaluation actions through the current service id.
- [x] Keep image-generation evaluation and generation bound to the opened service and show the service API tab.
- [x] Run TypeScript and frontend regression tests.

### Task 4: Verification

- [x] Run focused backend tests for all three routers.
- [x] Run `npm.cmd exec tsc -- --noEmit` and `npm.cmd run test:frontend`.
- [x] Probe local API route registration without starting GPU services.
