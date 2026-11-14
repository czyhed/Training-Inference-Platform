# Task 3 Report: 文生图模型目录与 LoRA/QLoRA 兼容状态

## Implemented

- Added `backend/image_generation_api.py` with a read-only `GET /api/image-generation/models` route.
- Catalog groups verified base assets and completed trained Best/Final Adapter variants.
- Adapter entries are derived from stored model-asset lineage; mismatched base IDs or repository IDs are rejected.
- Unknown repositories and unavailable files are never marked ready for Youyun.
- LoRA is supported for verified candidates by default. QLoRA is added only when `XUNTUI_IMAGE_QLORA_COMPATIBLE` is enabled; otherwise the response includes an explicit reason.
- Added `components/image-generation-models.tsx` and wired it into the 文生图 model-management view in `app/page.tsx`.
- Extended backend and source tests for grouping, lineage rejection, unknown candidates, and QLoRA gating.

## Verification

- `python -m unittest backend.test_image_generation_api -v` (7 passed)
- `node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --test lib/image-generation.test.mjs` (3 passed)
- `npx.cmd tsc --noEmit` (passed)
- `git diff --check` (passed; pre-existing line-ending warnings only)

The working tree contains unrelated visual-understanding and platform changes from parallel work; they were left untouched.

No GPU, Diffusers, ComfyUI, SSH, or real Youyun instance was started.
