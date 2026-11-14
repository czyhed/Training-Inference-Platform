# SDXL Training Script Artifact Supply Chain Design

> Design status: proposed for review
> Date: 2026-09-06
> Scope: SDXL LoRA remote-training script distribution only

## Goal

Make the SDXL LoRA training script available without depending on a live GitHub
connection during every GPU run, while preserving a verified public fallback and
the existing Youyun cleanup guarantees.

## Context and Constraints

- The target script is `train_text_to_image_lora_sdxl.py` from Diffusers `v0.30.3`.
- The immutable upstream commit is
  `c9ff360966327ace3faad3807dc871a4e5447501`.
- The accepted artifact SHA-256 is
  `f72ce7d06b1613a1f7e051cb8c8401f210791e0b6e57ccc7fd6223dbbc92f949`.
- The current public fallback downloads from GitHub Raw and has already failed
  in a Youyun instance with `SSL: UNEXPECTED_EOF_WHILE_READING`.
- The local MinIO configuration is already used by the shared model-storage
  boundary. MinIO credentials must never be sent to a Youyun instance.
- There is no existing custom Youyun image that can be updated in this task.
- No implementation or test may create a real GPU instance.

## Architecture

The repository owns the canonical script manifest and a local copy of the
verified script. A sync command uploads that copy to the configured MinIO bucket
under a commit-addressed object key. The remote training executor resolves the
script in this order:

1. Script already present at the image path
   `/opt/diffusers/examples/text_to_image/train_text_to_image_lora_sdxl.py`.
2. A verified local cache populated from MinIO by the backend, then uploaded to
   the remote work directory over the existing SSH/SFTP channel.
3. jsDelivr at the immutable commit URL.
4. GitHub Raw at the same immutable commit URL.

Every downloaded or copied artifact is written to a temporary file, checked for
the exact byte count and SHA-256, and atomically moved into place only after the
check passes. A failed check is terminal for that source and cannot be executed.

The MinIO path is an internal backend operation. The remote instance receives
only the script bytes through the existing SSH client; it never receives MinIO
access keys, a presigned URL, or a public storage endpoint.

## Artifact Contract

The canonical repository files are:

- `artifacts/sdxl/v0.30.3/train_text_to_image_lora_sdxl.py`
- `artifacts/sdxl/v0.30.3/manifest.json`

The manifest contains the upstream repository, tag, peeled commit, relative
path, byte count, SHA-256, content type, and generation date. The runtime must
reject a manifest with a mismatched commit, path, size, or digest.

The MinIO object key is:

`runtime-assets/diffusers/c9ff360966327ace3faad3807dc871a4e5447501/train_text_to_image_lora_sdxl.py`

The manifest is stored beside it with the `.manifest.json` suffix. Upload is
idempotent: an existing object is accepted only when its metadata and bytes
match the manifest; otherwise the sync operation fails without overwriting it.

## Backend Interfaces

Add a focused artifact-store module rather than expanding the model-weight
uploader. It exposes:

- `SdxlScriptArtifact` - immutable metadata for the commit, relative path,
  byte count, SHA-256, and local source path.
- `load_sdxl_script_artifact() -> SdxlScriptArtifact` - loads and validates the
  repository copy plus manifest.
- `MinioSdxlScriptStore.put_if_absent(artifact) -> str` - ensures the bucket,
  writes the script and manifest idempotently, and returns the object key.
- `MinioSdxlScriptStore.download_verified(artifact, destination) -> Path` -
  downloads to a temporary file, validates size and digest, then atomically
  moves to `destination`.
- `sync_sdxl_script_artifact() -> dict[str, Any]` - CLI/service entry point that
  returns object key, byte count, digest, and storage status without exposing
  credentials.

The executor receives a small dependency-injected resolver with these outcomes:

- `image`: use the existing image file.
- `minio`: download through the backend store and upload over SSH.
- `public`: fetch jsDelivr, then GitHub Raw, with bounded retries and digest
  verification.
- `unavailable`: return `training_script_download_failed` and retain the
  original error detail in the task attempt.

The resolver must preserve the current phase name `script_setup`, but its result
must carry a stable source label (`image`, `minio`, or `public`) and the commit
digest in the platform log.

## Remote Training Flow

After model resolution and before launching the training process:

1. Check the configured image path with the existing SSH client.
2. If missing, ask the backend resolver for a verified artifact.
3. For MinIO, download to the local runtime cache and upload the file to the
   remote work directory, then run `sha256sum` remotely before moving it to the
   image path or invoking it.
4. For public fallback, download through the backend or a controlled remote
   command only when explicitly enabled; never skip verification.
5. Run the existing script setup command and continue with the current launch
   and polling lifecycle.

The remote script path remains configurable through
`XUNTUI_SDXL_LORA_SCRIPT`, but the default path and artifact filename remain
unchanged for image compatibility.

## Configuration

Add documented variables to `.env.example`:

- `XUNTUI_SDXL_SCRIPT_ARTIFACT_ENABLED=true` enables MinIO/internal lookup.
- `XUNTUI_SDXL_SCRIPT_ARTIFACT_KEY=runtime-assets/diffusers/.../train_text_to_image_lora_sdxl.py`
  defaults to the commit-addressed key above.
- `XUNTUI_ALLOW_REMOTE_SCRIPT_DOWNLOAD=1` remains the explicit switch for
  public fallback; it does not bypass digest verification.
- `XUNTUI_SDXL_SCRIPT_PUBLIC_URLS` optionally overrides the two fixed public
  URLs, but values must still resolve to the manifest digest.

The production default is image -> MinIO -> public fallback only when the
existing explicit flag is enabled. Local development uses the repository copy
and Fake MinIO tests; it does not require a running MinIO service.

## Image Build Material

Create a self-contained build context under `deploy/youyun/sdxl-lora/`:

- `Dockerfile` based on the chosen PyTorch/Diffusers image supplied by the
  operator.
- `train_text_to_image_lora_sdxl.py` copied from the canonical artifact.
- `manifest.json` copied beside it.
- `README.md` with the required `CompShareImageId` probe checklist.

The Dockerfile installs no new runtime dependency. It copies the script to
`/opt/diffusers/examples/text_to_image/`, verifies the SHA-256 during build, and
fails the build if the digest is wrong. Because no custom image exists today,
the repository can generate and test this context but cannot publish a Youyun
image automatically.

## Failure Handling and Resource Lifecycle

- MinIO connection, object-not-found, HTTP, digest, and local-disk errors map to
  `training_script_download_failed` with a source-specific diagnostic.
- Public TLS EOF, connection reset, HTTP 408/429/5xx, and timeout may move to the
  next source with bounded exponential backoff. A 404 or digest mismatch does
  not retry the same source.
- Script setup failures never launch the training process.
- Any failure after instance creation still enters the existing `finally` cleanup
  path, and the task is not considered released until the provider confirms the
  instance is absent.
- The task attempt records source, commit, byte count, digest, retry count, and
  the final cleanup state. It must not record MinIO credentials or full private
  commands.

## Testing and Acceptance

Add focused tests before implementation:

- Repository artifact and manifest load with exact size/digest checks.
- Fake MinIO upload idempotency and rejection of mismatched existing objects.
- Verified MinIO download using a temporary file and atomic replacement.
- Resolver precedence: image, MinIO, jsDelivr, GitHub Raw, then terminal failure.
- TLS EOF switches source and is classified as `training_script_download_failed`,
  not `ssh_channel_disconnected`.
- Remote checksum mismatch prevents launch.
- Docker build-context manifest verification using a shell-free test harness.
- Existing cleanup tests prove a script failure leaves `resourceState` released
  only after the fake provider confirms deletion.

Required validation commands after implementation:

```powershell
python -m pytest backend/test_sdxl_script_artifact.py backend/test_youyun_image_generation_training.py -q
npm.cmd run test:frontend
npx.cmd tsc --noEmit
npm.cmd run build
```

No acceptance step creates a real Youyun instance. A real image probe, if later
performed by an operator, must use the repository's guarded acceptance runner,
have a cost/time cap, and record final instance absence.

## Rollback

Rollback is configuration- and artifact-safe: disable
`XUNTUI_SDXL_SCRIPT_ARTIFACT_ENABLED`, retain the existing public fallback flag,
and revert the executor's resolver wiring. MinIO objects are immutable and may
remain for audit; no task, dataset, model, or remote instance is deleted by the
rollback.

## Non-Goals

- Creating or publishing a custom Youyun image from this repository.
- Moving SDXL base-model weights into MinIO as part of this change.
- Changing training hyperparameters, task states, or the Youyun instance API.
- Exposing MinIO credentials or presigned URLs to remote instances.
