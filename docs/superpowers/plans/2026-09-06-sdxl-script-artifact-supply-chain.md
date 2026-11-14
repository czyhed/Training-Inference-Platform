# SDXL Script Artifact Supply Chain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the verified Diffusers SDXL LoRA training script available through MinIO and SSH distribution, with image and public fallbacks, without exposing MinIO credentials or requiring a real GPU instance during tests.

**Architecture:** Keep one repository-owned script and manifest as the canonical artifact. Add a focused artifact module that uploads/downloads verified bytes through the existing MinIO configuration, then inject a resolver into the Youyun SDXL executor so it checks the image, MinIO, jsDelivr, and GitHub Raw in that order. Add a Docker build context that verifies and preinstalls the same artifact for future custom Youyun images.

**Tech Stack:** Python 3.11+, FastAPI runtime modules, existing `minio` client, existing Paramiko SSH/SFTP client, `unittest`/pytest-compatible backend tests, Dockerfile build context, PowerShell validation commands.

**Spec:** `docs/superpowers/specs/2026-09-06-sdxl-script-artifact-supply-chain-design.md`

## Global Constraints

- The canonical upstream commit is `c9ff360966327ace3faad3807dc871a4e5447501`.
- Accept only SHA-256 `f72ce7d06b1613a1f7e051cb8c8401f210791e0b6e57ccc7fd6223dbbc92f949` and exactly `55,568` bytes.
- MinIO credentials, presigned URLs, and private remote commands must never be sent to the Youyun instance or persisted in task logs.
- Tests use fake MinIO/SSH/provider boundaries and must not create, start, stop, or delete a real Youyun instance.
- Any failure after instance creation must use the existing cleanup path and must not claim `resourceState=released` without provider confirmation.
- Preserve existing user changes and do not modify unrelated model-storage or task-state behavior.
- Do not commit changes unless the user explicitly requests a commit.

---

### Task 1: Add the Canonical Script Artifact

**Files:**
- Create: `artifacts/sdxl/v0.30.3/train_text_to_image_lora_sdxl.py`
- Create: `artifacts/sdxl/v0.30.3/manifest.json`
- Create: `backend/test_sdxl_script_artifact.py`
- Modify: `.gitignore` only if repository rules currently exclude `artifacts/` and the canonical two files would otherwise be ignored.

**Interfaces:**
- Produces the immutable repository artifact consumed by Tasks 2–5.
- Manifest fields: `repository`, `tag`, `commit`, `path`, `sizeBytes`, `sha256`, `contentType`.

- [ ] **Step 1: Write the failing artifact-validation tests**

```python
def test_canonical_sdxl_artifact_matches_manifest():
    artifact = load_sdxl_script_artifact()
    assert artifact.commit == "c9ff360966327ace3faad3807dc871a4e5447501"
    assert artifact.size_bytes == 55_568
    assert artifact.sha256 == "f72ce7d06b1613a1f7e051cb8c8401f210791e0b6e57ccc7fd6223dbbc92f949"

def test_manifest_rejects_digest_mismatch(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"sha256": "bad", "sizeBytes": 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        validate_script_manifest(tmp_path / "script.py", manifest)
```

- [ ] **Step 2: Run the focused tests and confirm the expected red failure**

Run: `python -m pytest backend/test_sdxl_script_artifact.py -q`

Expected: FAIL because the artifact module and canonical files do not exist.

- [ ] **Step 3: Obtain the already-researched upstream bytes and write the manifest**

Use the fixed commit URL from `docs/research/diffusers-sdxl-lora-script-source-2026-09-06.md`; verify the downloaded bytes against the exact size and SHA-256 before placing them in `artifacts/sdxl/v0.30.3/`. Do not use a mutable branch or tag URL.

- [ ] **Step 4: Implement pure validation helpers**

Create `backend/sdxl_script_artifact.py` with:

```python
@dataclass(frozen=True)
class SdxlScriptArtifact:
    path: Path
    manifest_path: Path
    repository: str
    tag: str
    commit: str
    relative_path: str
    size_bytes: int
    sha256: str
    content_type: str

def validate_script_manifest(script_path: Path, manifest_path: Path) -> SdxlScriptArtifact: ...
def load_sdxl_script_artifact(root: Path | None = None) -> SdxlScriptArtifact: ...
```

Reject path, commit, size, or digest mismatches and return no partially validated artifact.

- [ ] **Step 5: Run the focused tests and confirm green**

Run: `python -m pytest backend/test_sdxl_script_artifact.py -q`

Expected: PASS with the exact byte count and digest assertions.

### Task 2: Add MinIO Artifact Storage and Sync

**Files:**
- Modify: `backend/sdxl_script_artifact.py`
- Create: `scripts/sync_sdxl_script_artifact.py`
- Modify: `.env.example`
- Test: `backend/test_sdxl_script_artifact.py`

**Interfaces:**
- Consumes `SdxlScriptArtifact` from Task 1 and `ModelStorageConfig`/MinIO dependency conventions from `backend/model_storage.py`.
- Produces:
  - `MinioSdxlScriptStore.put_if_absent(artifact) -> str`
  - `MinioSdxlScriptStore.download_verified(artifact, destination) -> Path`
  - `sync_sdxl_script_artifact() -> dict[str, Any]`

- [ ] **Step 1: Write failing Fake MinIO tests**

Cover bucket creation, commit-addressed object key, idempotent matching upload, rejection of an existing mismatched object, temporary download plus atomic replacement, and cleanup of a failed temporary download.

- [ ] **Step 2: Run the tests and verify they fail on missing storage methods**

Run: `python -m pytest backend/test_sdxl_script_artifact.py -q`

Expected: FAIL at the first missing `MinioSdxlScriptStore` interface.

- [ ] **Step 3: Implement the MinIO store using the existing `minio` package**

Use `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET`, and `MINIO_SECURE` through `ModelStorageConfig.from_env()`. Store the script at:

```text
runtime-assets/diffusers/c9ff360966327ace3faad3807dc871a4e5447501/train_text_to_image_lora_sdxl.py
runtime-assets/diffusers/c9ff360966327ace3faad3807dc871a4e5447501/train_text_to_image_lora_sdxl.py.manifest.json
```

Use object metadata and a read-back digest check for existing objects. Download to `destination.with_suffix(destination.suffix + ".tmp-<uuid>")`, validate bytes, then use `Path.replace()`.

- [ ] **Step 4: Add the sync command without exposing credentials**

The script calls `sync_sdxl_script_artifact()`, prints only bucket, object key, byte count, digest, and status, and returns nonzero on configuration, object, or digest errors.

- [ ] **Step 5: Document configuration and run the tests**

Add `XUNTUI_SDXL_SCRIPT_ARTIFACT_ENABLED=true` and `XUNTUI_SDXL_SCRIPT_ARTIFACT_KEY` to `.env.example`; keep the default object key commit-addressed. Run:

```powershell
python -m pytest backend/test_sdxl_script_artifact.py -q
```

Expected: PASS without requiring live PostgreSQL, MinIO, or Youyun services.

### Task 3: Add the Resolver and SSH Distribution Path

**Files:**
- Modify: `backend/sdxl_script_artifact.py`
- Modify: `backend/providers/youyun/image_generation_training.py`
- Modify: `backend/providers/youyun/ssh.py` only if an existing non-shell file upload helper cannot be reused.
- Test: `backend/test_sdxl_script_artifact.py`
- Test: `backend/test_youyun_image_generation_training.py`

**Interfaces:**
- Consumes Task 1 artifact validation and Task 2 MinIO store.
- Produces:
  - `SdxlScriptResolver.resolve() -> ResolvedSdxlScript`
  - `ResolvedSdxlScript.source` in `image | minio | public`
  - `ResolvedSdxlScript.local_path`, `commit`, `size_bytes`, `sha256`, `attempts`

- [ ] **Step 1: Write failing resolver tests**

Test image precedence, MinIO fallback, jsDelivr fallback after a simulated TLS EOF, GitHub Raw fallback after jsDelivr failure, digest mismatch rejection, and terminal failure when all sources fail. Assert that a TLS EOF during script setup yields `training_script_download_failed`, not `ssh_channel_disconnected`.

- [ ] **Step 2: Run the resolver tests and observe expected failures**

Run: `python -m pytest backend/test_sdxl_script_artifact.py backend/test_youyun_image_generation_training.py -k "script or resolver" -q`

Expected: FAIL because the resolver and source-specific setup are not implemented.

- [ ] **Step 3: Implement bounded source resolution**

Implement image check first. For MinIO, use the backend store to create a verified local cache file. For public URLs, use fixed commit URLs, bounded timeout/retry, and digest verification; only retry network/TLS/408/429/5xx failures, never 404 or digest mismatch.

- [ ] **Step 4: Upload the verified local file through SSH/SFTP**

When the image path is absent, upload to the task work directory with the existing SSH client, run a remote `sha256sum`, compare the expected digest, and only then copy/move to the configured script path. Do not send MinIO credentials or URLs.

- [ ] **Step 5: Wire the executor and preserve cleanup**

Replace the current direct remote public download block at `script_setup` with the resolver. Keep `XUNTUI_SDXL_LORA_SCRIPT` as the remote target path. Emit source, commit, size, digest, and attempt count in platform logs. Ensure exceptions still pass through the existing `finally` cleanup and that no launch occurs after checksum failure.

- [ ] **Step 6: Update failure classification and run focused tests**

Check `script_setup` and script-download context before generic EOF classification so the observed `SSL: UNEXPECTED_EOF_WHILE_READING` maps to `training_script_download_failed`. Run:

```powershell
python -m pytest backend/test_sdxl_script_artifact.py backend/test_youyun_image_generation_training.py -q
```

Expected: PASS, including fake cleanup assertions.

### Task 4: Generate the Future Youyun Image Build Context

**Files:**
- Create: `deploy/youyun/sdxl-lora/Dockerfile`
- Create: `deploy/youyun/sdxl-lora/train_text_to_image_lora_sdxl.py`
- Create: `deploy/youyun/sdxl-lora/manifest.json`
- Create: `deploy/youyun/sdxl-lora/README.md`
- Create: `scripts/verify_sdxl_image_context.py`
- Test: `backend/test_sdxl_script_artifact.py`

**Interfaces:**
- Consumes the canonical artifact and manifest from Task 1.
- Produces a portable build context that installs no new Python dependency and places the script at `/opt/diffusers/examples/text_to_image/train_text_to_image_lora_sdxl.py`.

- [ ] **Step 1: Write the failing build-context verification test**

Assert the context contains the exact script and manifest, and that the verifier rejects a modified script.

- [ ] **Step 2: Run the test and confirm it fails before the context exists**

Run: `python -m pytest backend/test_sdxl_script_artifact.py -k "image_context" -q`

Expected: FAIL because the Docker context and verifier do not exist.

- [ ] **Step 3: Create the Dockerfile and context files**

The Dockerfile accepts an operator-supplied base image via `ARG BASE_IMAGE`, copies the script and manifest, runs the verifier during build, and copies the script into `/opt/diffusers/examples/text_to_image/`. It must not contain credentials, model weights, or mutable URLs.

- [ ] **Step 4: Implement the shell-free verifier**

`verify_sdxl_image_context.py` accepts a context root and validates the same commit, byte count, and digest as Task 1. Use it from tests and from the Dockerfile with Python, not `shell=True` or ad hoc text substitution.

- [ ] **Step 5: Add operator instructions**

Document `docker build --build-arg BASE_IMAGE=<verified-pytorch-diffusers-image>`, the expected output path, the need to push/import the image into Youyun, the required real-instance probe, and the fact that this repository cannot publish a custom `CompShareImageId` automatically.

- [ ] **Step 6: Run context tests**

Run: `python -m pytest backend/test_sdxl_script_artifact.py -k "image_context" -q`

Expected: PASS without Docker daemon access.

### Task 5: Documentation, Configuration, and Full Verification

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `CURRENT_STATE.md`
- Modify: `ARCHITECTURE.md`
- Modify: `docs/youyun-community-image-runbook.md` if the existing runbook is the operator-facing image source.
- Test: `backend/test_sdxl_script_artifact.py`

**Interfaces:**
- Consumes the finalized artifact, resolver, sync command, and image context from Tasks 1–4.
- Produces operator-visible source precedence, configuration defaults, rollback guidance, and verification commands.

- [ ] **Step 1: Add documentation assertions**

Assert the documented default object key, commit, digest, source precedence, and no-real-instance test rule appear in the relevant operator documentation.

- [ ] **Step 2: Update docs and environment examples**

Document that MinIO is the internal source, the image path is the preferred source, public fallback is explicitly gated, and MinIO credentials never enter remote commands. Keep `.env.local` secrets and machine-specific endpoints out of tracked examples.

- [ ] **Step 3: Run the complete verification set**

```powershell
python -m pytest backend/test_sdxl_script_artifact.py backend/test_youyun_image_generation_training.py -q
npm.cmd run test:frontend
npx.cmd tsc --noEmit
npm.cmd run build
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8010/api/health
```

Expected: all focused tests pass, frontend tests/typecheck/build pass, and the local API health endpoint returns `200`. No command may create a real Youyun instance.

- [ ] **Step 4: Record rollback and residual risk**

Record that disabling `XUNTUI_SDXL_SCRIPT_ARTIFACT_ENABLED` restores the previous executor behavior, while MinIO objects remain immutable audit artifacts. Note that publishing the custom Youyun image remains an operator action and requires a separate guarded probe.
