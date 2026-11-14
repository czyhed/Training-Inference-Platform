# Text-to-Image Inference Service Optimization Implementation Plan

> **For agentic workers:** Execute this plan task-by-task with test-first development and fresh verification at each checkpoint.

**Goal:** Make text-to-image inference services stable, fast to start, observable, and cost-safe when running on Youyun GPU instances.

**Architecture:** Keep the existing `InferenceRuntime` and `YouyunInferenceProvider` boundaries. Add lifecycle/cleanup bookkeeping in the store, fail-fast startup diagnostics and cache-aware remote preparation in the provider, bounded request behavior in the runtime, and an explicit idle-reclamation worker that only acts on remote services.

**Tech Stack:** FastAPI, asyncio, SQLite/PostgreSQL store, Paramiko SSH, vLLM OpenAI API, unittest/pytest.

**Spec:** Approved in chat on 2026-09-02.

## Global Constraints

- Do not create or start a Youyun instance during local tests.
- Preserve existing user changes and public API compatibility.
- Every production behavior change must have a failing regression test first.
- Cleanup must be idempotent and must persist `cleanup_pending` when remote deletion cannot be confirmed.

---

### Task 1: Lifecycle and cleanup safety

**Files:**
- Modify: `backend/inference_service.py`
- Modify: `backend/providers/youyun/inference.py`
- Test: `backend/test_inference_service.py`
- Test: `backend/test_youyun_image_generation.py` or a focused new provider test

- [x] Add failing tests for duplicate remote starts, cleanup-pending persistence, and idempotent stop.
- [x] Run the focused tests and confirm expected failures.
- [x] Implement one guarded remote runtime per service, persist cleanup reports, and make repeated stop calls safe.
- [x] Run focused tests to green.

### Task 2: Fail-fast startup diagnostics and stage timing

**Files:**
- Modify: `backend/providers/youyun/inference.py`
- Modify: `backend/inference_service.py`
- Test: `backend/test_youyun_image_generation.py`
- Test: `backend/test_inference_service.py`

- [x] Add failing tests for stage elapsed time, log-tail error classification, and early process-exit failure.
- [x] Run tests and confirm failures.
- [x] Implement stage timing metadata, bounded log-tail reads, and explicit error categories without waiting for the full timeout.
- [x] Run focused tests to green.

### Task 3: Remote cache and startup speed

**Files:**
- Modify: `backend/providers/youyun/inference.py`
- Test: provider tests for model-cache hit and adapter checksum skip.

- [x] Add failing tests for skipping model download when a complete model is present and skipping identical adapter uploads.
- [x] Run tests and confirm failures.
- [x] Implement complete-model detection, adapter manifest/checksum persistence, and cache-hit stage reporting.
- [x] Run focused tests to green.

### Task 4: Request timeout and concurrency protection

**Files:**
- Modify: `backend/inference_service.py`
- Test: `backend/test_inference_service.py`

- [x] Add failing tests for rejecting requests before `running`, bounded concurrent requests, and timeout classification.
- [x] Run tests and confirm failures.
- [x] Implement per-service semaphore, connect/read/total timeout settings, and stable error mapping.
- [x] Run focused tests to green.

### Task 5: Idle reclamation and cost guardrails

**Files:**
- Modify: `backend/inference_service.py`
- Modify: `backend/app.py` if lifecycle startup/shutdown hooks need wiring
- Test: `backend/test_inference_service.py`

- [x] Add failing tests for idle remote service reclamation, active-request protection, and configurable disablement.
- [x] Run tests and confirm failures.
- [x] Implement a cancellable background idle watcher and persist the reclamation result.
- [x] Run focused tests to green.

### Task 6: Verification and controlled cloud acceptance

- [x] Run backend focused tests, full backend suite, frontend tests, TypeScript check, and production build.
- [x] Review diff and verify no credentials or generated artifacts were added.
- [ ] Only after explicit cloud-acceptance confirmation, run one guarded Youyun acceptance with a single instance, maximum runtime, and immediate stop/delete in `finally`.
- [ ] Confirm the instance no longer exists before reporting acceptance.
