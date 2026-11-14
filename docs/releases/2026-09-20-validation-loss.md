# Validation Loss / artifact recovery release

Read AGENTS.md, engineering gate, README, ARCHITECTURE, CURRENT_STATE and deployment runbook. Base: 32a1eae. Previous running backend/frontend: v3.0.147.

Scope: UI configuration to executor YAML, Trainer logs to metric store, downloaded artifacts to metadata/preview. No schema changes or API removal. No original-worktree changes or unrelated demo files included.

Baseline regression: python -m unittest backend.test_validation_loss -q fails on periodic evaluation configuration and final loss parsing; integrated version passes. Artifact tests cover final step 897, final eval files, remote checkpoint paths, stopped checkpoints, restart recovery, corrupt/stale manifests and JSON preview.

Verification: 887 backend tests (4 environment skips), 151 frontend tests, tsc, production build and diff check. Windows TEMP normalized to long paths to avoid a pre-existing short-name assertion failure. Headless Edge desktop/mobile checks use fixture curves, not a real GPU run.

Resources: only CPU platform containers are deployed. No training/vLLM instances are started or user workloads stopped. Idle guard is mandatory before deployment. Persistent mounts, database, objects and historical tasks are preserved.

Rollback: saved prior compose/gateway config and backend/frontend v3.0.147 images, after an idle check. No schema migration is introduced. Use a new immutable tag after merge and upload the source bundle before tag publication.

Acceptance: image tags agree with health releaseVersion; containers healthy; database/object-storage read probe; Youyun-only capabilities; entry/static assets and training WebSocket. User will validate a real GPU task.

Tradeoff: configured validation/checkpoint intervals add runtime and I/O. Historical evaluation points that were not computed cannot be reconstructed. No historical backfill is performed during deployment.

## Container packaging follow-up

The v3.0.151 image gate failed before any production container switch: the backend Dockerfile omitted deploy/, including the canonical SDXL script/manifest required at runtime. New regression test_backend_image_artifacts fails on that Dockerfile. Include the tracked deploy directory (no credentials); retain previous images/data and rerun image-level tests. Gateway contract tests additionally need the repository-only .drone.yml fixture, mounted read-only for tests, not runtime. v3.0.151 is not deployed; use a new immutable release tag for this packaging correction.
