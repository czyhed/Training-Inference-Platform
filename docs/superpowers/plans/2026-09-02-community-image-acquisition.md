# 优云社区镜像获取与验证 Implementation Plan

> **For agentic workers:** Execute this plan task-by-task with tests first.

**Goal:** Discover real Youyun community image IDs, verify them safely, and expose only verified candidates to visual and image-generation training.

**Architecture:** Extend the existing Youyun client with a best-effort community catalog call, normalize versions into a stable API payload, and gate selection on server-side verification status. Reuse one-shot instance cleanup for probes and preserve the existing platform-image fallback when the community API is unavailable.

**Tech Stack:** Python/FastAPI, unittest, TypeScript/React, existing Youyun signed POST client and one-shot instance manager.

**Spec:** `docs/superpowers/specs/2026-09-02-community-image-acquisition-design.md`

## Global Constraints

- Never invent a billable image ID.
- Unverified, unavailable, reviewing, or missing-ID community images are display-only.
- Probe instances require explicit confirmation and automatic cleanup verification.
- Existing platform-image selection and training contracts remain compatible.

---

### Task 1: Community API parser and client contract

**Files:**
- Modify: `backend/providers/youyun/client.py`
- Test: `backend/test_youyun_provider.py`

- [ ] Add failing tests for the community action name, request filters, pagination normalization, nested group/version response, and empty/error response.
- [ ] Run `python -m unittest backend.test_youyun_provider.CommunityImageClientTests -v` and confirm failure.
- [ ] Implement `describe_community_images` as a best-effort signed POST with bounded pagination and normalized `image_id`, `group_id`, `version_name`, `status`, `tags`, `price`, `supported_gpu_types`, `container`, and `auto_start` fields.
- [ ] Keep API errors observable to the caller while allowing the route layer to fall back to platform images.
- [ ] Run the focused tests and the existing Youyun provider tests.

### Task 2: Catalog merge and server endpoint

**Files:**
- Modify: `backend/providers/youyun/models.py`
- Modify: `backend/app.py`
- Test: `backend/test_youyun_provider.py`
- Test: `backend/test_vision_api.py`

- [ ] Add failing tests proving community rows with real IDs are returned as `source=community`, rows without IDs remain unavailable, and a community API failure does not remove platform images.
- [ ] Run the focused tests and confirm failure.
- [ ] Implement normalized community catalog merging, purpose filtering for `vision_training` and `image_generation`, and a `GET /api/youyun/community-images` route with region/zone and optional query filters.
- [ ] Update `/api/youyun/gpu-specs` to fetch both platform and community images best-effort and include verification state without changing existing fields.
- [ ] Run backend focused tests.

### Task 3: Verification state and probe gating

**Files:**
- Modify: `backend/youyun_vision_probe.py` or the existing shared probe module
- Modify: `backend/app.py`
- Test: `backend/test_youyun_vision_probe.py`
- Test: `backend/test_youyun_guarded_acceptance.py`

- [ ] Add failing tests for verified, unavailable, timeout, dependency failure, and cleanup-failure states.
- [ ] Run the tests and confirm failure.
- [ ] Add a short-lived in-process verification record keyed by image ID, GPU type, region, and purpose; run the existing one-shot lifecycle with a hard timeout and cleanup verification.
- [ ] Require `verified` plus a real image ID in remote training submission validation.
- [ ] Return actionable failure reasons and the last verification timestamp.
- [ ] Run the focused probe and guarded-acceptance tests.

### Task 4: Frontend community image UX

**Files:**
- Modify: `components/vision-training-module.tsx`
- Modify: `components/vision-understanding-module.tsx`
- Modify: `lib/types.ts`
- Test: `lib/vision-training-provider.test.mjs`

- [ ] Add failing tests for status labels, disabled unverified options, verified option selection, and refresh behavior.
- [ ] Run the focused frontend tests and confirm failure.
- [ ] Render community image versions with status, price, supported GPU, and verification timestamp; keep unverified rows visible but disabled.
- [ ] Add a refresh action that only queries metadata and a separate confirmation action for probes.
- [ ] Ensure existing platform image fallback remains selectable when the community endpoint is unavailable.
- [ ] Run frontend tests and TypeScript checks.

### Task 5: Build/publish runbook and final verification

**Files:**
- Create: `docs/youyun-community-image-runbook.md`
- Modify: `README.md`

- [ ] Document the self-build fallback from `cuda132_torch2130_py312`, secret/cache cleanup, private-image validation, and community publication steps.
- [ ] Document the 30-minute probe TTL, cleanup verification, and no-inventory behavior.
- [ ] Run backend tests, frontend tests, TypeScript, and production build.
- [ ] Perform one metadata-only live catalog check; do not create a GPU instance without an explicit user confirmation for the probe.
