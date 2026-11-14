# Xuntui Platform

Internal model training and inference platform MVP. The current runnable scope is text-generation and visual-understanding SFT/DPO with LLaMA-Factory, using either LoRA (16-bit) or QLoRA (4-bit). Visual training requires a published `IMAGE_TEXT` dataset and a compatible probed Qwen-VL family model. The backend supports PostgreSQL as the preferred shared task and inference store, with SQLite retained as a local fallback; it is not yet the complete production cloud architecture.

## Current handoff baseline

The latest published release in this repository is `v3.0.130` (2026-09-13), deployed by the tag-triggered Drone pipeline to the production host `110.42.51.234` (new server since 2026-09-13; the retired host was `185.61.210.32`). Since `v3.0.68`, the platform made LoRA+ and checkpoint settings effective in the generated LLaMA-Factory YAML, added server-side model-asset pagination, request-scoped inference generation controls (JSON or SSE), incremental inference-log polling plus complete-log download, ModelScope-to-Hugging-Face fallback for remote base-model downloads with observable diagnostics, PostgreSQL queue-read retry behavior, Asia/Shanghai audit timestamps, more reliable Youyun SSH transfer and remote-environment recovery, preflight GPU-runtime checks, archive-time remote-instance cleanup verification, structured training-submit errors, short-run train-Loss recovery, resilient inference/fine-tuning WebSocket log streaming with PostgreSQL datetime serialization and heartbeats, automatic retry for transient Youyun SSH channel timeouts, single-GPU deployment enforcement with a visible release version, the evaluation-datasets and model-evaluation workflow, and the new-server deployment pipeline (bundle-based source fetch, MinIO wheelhouse builds). Production currently runs without the Control Center login gate (`XUNTUI_AUTH_MODE=disabled`; Control Center stopped, data kept). The next unreleased production configuration keeps the unfinished evaluation workflow hidden with `XUNTUI_EVALUATION_ENABLED=false` until its datasets and acceptance flow are ready. See [CURRENT_STATE.md](CURRENT_STATE.md) for the full audited per-release status, [docs/DEPLOYMENT-NEW-SERVER.md](docs/DEPLOYMENT-NEW-SERVER.md) for the production runbook, and [CONTRIBUTING.md](CONTRIBUTING.md) before creating, merging, or releasing a branch.

The current published release commit is `97b7b14` (`v3.0.130`) and matches `origin/master`.
## Current scope

- Dataset upload, schema validation, publish, clone, delete, and paginated content preview for text JSONL datasets. DPO uploads accept conversational `messages` + `chosen`/`rejected` records as well as the legacy string shape.
- One published SFT or DPO dataset per training task; the task type must match the published dataset structure.
- DPO records are normalized before TRL training to the canonical `prompt`/`chosen`/`rejected` fields. Conversational input is converted without retaining extra source columns, avoiding TRL chat-template key errors.
- For DPO, the UI's maximum sequence-length value is used consistently as `max_prompt_length`, `max_completion_length`, and `max_length`.
- ModelScope keyword search and exact repository IDs.
- Unsloth + TRL + PEFT training on a single WSL GPU worker.
- LoRA default: `Qwen/Qwen2.5-3B-Instruct`, 16-bit base loading.
- QLoRA default: `Qwen/Qwen2.5-7B-Instruct`, 4-bit base loading.
- Task queue/status, logs, parsed train/eval loss history, best/final Adapter artifacts, optional merged model, and base-vs-Adapter chat comparison.
- Validation-enabled runs retain the lowest-eval-loss Adapter and the final Adapter. Runs without validation retain the lowest-train-loss Adapter and the final Adapter, with an explicit warning that training Loss is not a generalization signal. Chat defaults to the selected best variant.
- Model management: discover ModelScope cache entries, manually register base models, sync completed-task Adapters with base-model lineage, rename, refresh, archive/restore, and hand off to training/deployment.
- PostgreSQL-backed inference services in configured server environments, with SQLite retained as a local fallback. Service configuration, status, logs, metrics, command usage, archive, restore, and deletion use the selected storage backend.
- vLLM Base Model + Best Adapter serving, optional 4-bit BitsAndBytes quantization, one local GPU lease, lifecycle/exposure actions, service-scoped JSON/SSE online debug, logs, and runtime metrics.
- Youyun (CompShare) one-shot remote GPU training with manual resource selection, live stock validation, and live pay-as-you-go price display.
- Youyun community-image discovery now reads real `CompShareImageId` versions from the community catalog, shows GPU/price metadata, and gates visual training on a time-bounded probe with automatic instance cleanup; platform-image fallback remains available when the community catalog is empty.
- Visual understanding: ZIP import with `images/` + `metadata.jsonl`, image/signature and annotation validation, visual model registration/probing, visual SFT/DPO preflight, isolated LLaMA-Factory materialization, local/Youyun execution, confirmation, stop/retry, loss/log tracking, and Best/Final Adapter manifests. Text-to-image supports local dataset validation, Caption/metadata generation, verified base/Adapter cataloging, SDXL LoRA training-bundle materialization, Fake/Youyun training executors, Best/Final Adapter synchronization, LoRA/QLoRA gating, live Youyun resource snapshots, a no-instance startup preflight, and a confirmation-gated Diffusers task runtime with remote progress, result download, retry, cleanup tracking, and safe runtime diagnostics. Cleanup failures retain the remote handle and block reruns until cleanup succeeds. Remote Repo IDs are validated server-side and the image selector only falls back to a plain PyTorch image when remote Diffusers installation is explicitly enabled. Preflight snapshots persist a configuration fingerprint and default 10-minute TTL; confirmation rechecks expired or changed snapshots. Runtime diagnostics classify instance, environment, model, Adapter, generation, download, and cleanup failures without exposing raw commands or credentials. Task history now supports service/state/query filters, clone-and-edit regeneration, archive/restore, safe deletion with result cleanup, and read-only service result aggregation. Local Fake tests cover the micro-tuning-to-catalog contract. On September 1, 2026, a guarded real Youyun SDXL LoRA generation completed through dependency preparation, model/Adapter loading, image download, and verified instance release; the two-step smoke image validates function and resource cleanup, not production image quality.

Training forms accept decimal values where the parameter semantics are fractional (for example warmup ratio, weight decay, LoRA dropout, temperature, top-p, min-delta, and epochs); integer-only controls remain integer-only.

Control Center OAuth2 login is supported with Authorization Code + PKCE (S256), short-lived signed local sessions, and `cppt-platform` as the default client key. Business-space authorization remains deferred. PostgreSQL and MinIO are now connected for shared model-weight storage; Redis, text-to-video generation, public inference, and multi-GPU/multi-worker scheduling remain outside the runnable MVP. Text-to-image phase two provides the Diffusers/Youyun execution contract but has not yet created a real instance during acceptance. Phase-two LLaMA-Factory configuration, text/visual executors, local process lifecycle, artifact manifests, real CPU acceptance, Docker packaging, and the tag-based Drone deployment pipeline are now implemented. Production deployment still requires validating the target server secrets, external PostgreSQL/MinIO connectivity, and the first tagged release.

### Youyun GPU training boundary

The training form currently exposes **manual Youyun resource selection** only: GPU model, single-card count, CPU cores, and system memory. The backend validates the selected combination against live Youyun capacity and calls the price API before submission, so the displayed hourly amount follows the exact selected specification. Multi-card resources and AI-driven recommendation based on dataset/training requirements are intentionally deferred; the former is not enabled by the current remote runner, and the latter is a future optimization rather than a lowest-price shortcut.

Both web submission and the restricted command-line submission use the same backend queue. When a task is marked `provider=youyun` and `executionMode=remote`, LLaMA-Factory runs on the temporary Youyun GPU instance over SSH; local command-line execution remains the local CPU/GPU server path. The remote runner requires a complete model under `YOUYUN_BASE_MODEL_PATH` or `YOUYUN_BASE_MODEL_ROOT` by default. `YOUYUN_ALLOW_MODEL_DOWNLOAD` defaults to `0`; enable it explicitly with `1` only when the extra preparation time and network cost are acceptable, which lets LLaMA-Factory download the selected ModelScope repository into `/root/cppt/modelscope-cache`. A completed text run, or a manually stopped text run with a usable Adapter, keeps the one-shot instance in `awaiting_comparison` until the user releases it; failures, cancellations without a usable Adapter, and visual-training runs upload available artifacts and delete the instance in their cleanup path.

Remote Youyun inference verifies that the selected remote Python can import vLLM before loading the model. It first resolves a complete base model from `YOUYUN_BASE_MODEL_PATH`/`YOUYUN_BASE_MODEL_ROOT` (including image and shared mounts); only when no complete model is found and `YOUYUN_ALLOW_MODEL_DOWNLOAD=1` does it use ModelScope and `YOUYUN_MODELSCOPE_CACHE` as a fallback. `XUNTUI_REMOTE_VLLM_INSTALL` defaults to `0`, so the provider uses an image-provided, prevalidated vLLM environment without mutating it; set the flag to `1` only when the target PyTorch environment is known to be compatible with `XUNTUI_REMOTE_VLLM_PACKAGE`. Set `XUNTUI_REMOTE_VLLM_PYTHON` to the image's working vLLM interpreter. A remote process that exits before `/health` now fails immediately with its log tail instead of remaining in the model-loading stage until timeout.

For remote SDXL LoRA training, the stable path is an image that already contains the Diffusers/PEFT stack, the SDXL training script, and a mounted base-model cache. The pinned script is Diffusers commit `c9ff360966327ace3faad3807dc871a4e5447501`, SHA-256 `f72ce7d06b1613a1f7e051cb8c8401f210791e0b6e57ccc7fd6223dbbc92f949`, and is stored in MinIO under `runtime-assets/diffusers/<commit>/`. At `script_setup`, the provider checks the image path, then resolves a verified MinIO copy and uploads it over SFTP; only a remote SHA-256 match permits launch. `XUNTUI_ALLOW_REMOTE_SCRIPT_DOWNLOAD` defaults to `0`; the pinned jsDelivr/GitHub URLs are an explicit emergency fallback only. Preparation failures are classified as `ssh_handshake_eof`, `ssh_channel_disconnected`, `dependency_install_failed`, `training_script_download_failed`, and `base_model_download_failed`, and the task detail records the phase and suggested action instead of collapsing every error into `training_failed`. MinIO credentials never enter remote commands or task logs. Build a future custom image from [deploy/youyun/sdxl-lora](deploy/youyun/sdxl-lora) and run `python scripts/verify_sdxl_image_context.py deploy/youyun/sdxl-lora` before publishing it to Youyun.

Youyun resource selection records transient create failures (`8433`/`226645`) in a process-local cooldown registry. Automatic mode omits cooled-down Pod zones and may select a healthy same-region UHost; explicit selections are never silently changed. Visual-understanding and SDXL training only switch from Pod to UHost after any captured Pod handle is confirmed released. Disable the guard with `XUNTUI_YOUYUN_RESOURCE_HEALTH_ENABLED=0`.
The same health filter and automatic fallback apply to visual-understanding and text-to-image remote inference services. Their saved resource snapshot is updated with the actual `instanceMode`, fallback reason, and attempted modes when a Pod startup must move to UHost.

Inference services support local vLLM and the Youyun remote provider. Local services use the backend host's private inference port and GPU lease. Youyun services create a one-shot GPU instance, establish SSH, verify or install vLLM in a remote PyTorch environment, upload the selected Adapter, run vLLM privately on the instance, and delete the instance on completion or failure. Public routing remains outside the remote provider; service-scoped API and chat requests are proxied by the backend.

## Gitea development and Drone releases

The canonical repository is [Gitea leifengyang/cppt-platform](http://git.leiziapi.cn:3003/leifengyang/cppt-platform), configured as `origin`. The optional `github` remote is retained for historical reads; push is disabled in this desktop checkout. Ordinary `git push` sends the current branch to Gitea.

[Drone](http://drone.leiziapi.cn:4400/leifengyang/cppt-platform) reads [`.drone.yml`](.drone.yml) directly from Gitea:

- Branch pushes, pull requests, and manual builds run `gitea-ci`: backend tests, frontend tests/type checks/production build, gateway tests, and production Compose validation.
- Release tags run `cppt-platform-tag-deploy`: the same verification, backend/frontend image builds, the production idle guard, Compose deployment, health checks, gateway installation, and WebSocket verification.

Both pipelines clone the exact Gitea revision. No Git bundle upload or production SSH login is needed to submit a release. After the intended release commit has passed review and testing:

```bash
git tag -a <new-version> -m "Release notes"
bash scripts/release-bundle.sh <new-version>
# The compatibility helper validates origin and pushes only the requested tag.
```

Use a new unique version for each release. Before tagging, confirm that the intended reviewed commit contains the current Gitea pipeline. The helper accepts only this project's Gitea HTTP or SSH remote and rejects multiple push destinations.

The production runner uses the Docker Engine on `110.42.51.234`, builds `cppt-platform-backend:<tag>` and `cppt-platform-frontend:<tag>`, and requires trusted repository access to `/var/run/docker.sock` and `/opt/cppt-platform`. The existing `/opt/cppt-platform/.env.production`, PostgreSQL/MinIO settings, and persistent data remain required; do not commit production credentials. Production images use `INSTALL_LOCAL_RUNTIME=0`.

A successful push starts CI; production changes occur only for a release tag. Keep the previous images for rollback and follow [the deployment runbook](docs/DEPLOYMENT-NEW-SERVER.md). CI checks do not create GPU instances.

## Start locally

Prerequisites: Windows with WSL2, the `Ubuntu-24.04` distribution, the `unsloth_env` training environment, and the `vllm_env` inference environment described in [CURRENT_STATE.md](CURRENT_STATE.md).

```powershell
npm install
npm run dev -- --hostname 127.0.0.1 --port 3100
python scripts\run_backend.py
```

- Frontend: `http://127.0.0.1:3100`
- Backend/API: `http://127.0.0.1:8010`
- OpenAPI: `http://127.0.0.1:8010/docs`
- Through the deployed application gateway, Swagger is exposed at `/docs`; the frontend proxies `/docs`, `/openapi.json`, and `/redoc` to the FastAPI backend.

OAuth2 deployment configuration lives in `.env.production` (see [.env.production.example](.env.production.example)). Set `OAUTH_CLIENT_KEY=cppt-platform`, the three Control Center endpoint URLs, a private `OAUTH_SESSION_SECRET` of at least 32 bytes, and preferably a read-only `OAUTH_APPS_DSN` so the client ID and registered gateway callback are loaded from the Control Center `apps` table. The frontend starts the browser redirect through `/api/auth/login`; token exchange and userinfo calls stay server-side.

Model-management API checks:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8010/api/health
Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8010/api/model-assets?includeArchived=true"
```

The model page reads the second endpoint. After changing backend source, restart the existing Uvicorn process; an old process can still answer `/docs` while returning `404 Not Found` for the newer `/api/model-assets` route. The frontend displays that response as `模型服务暂不可用`, which means the API route is missing, not that a model file is necessarily broken. The observed local occurrence was resolved by restarting the backend with the current source. If port `8010` is occupied, use another backend port for a local preview and set `NEXT_PUBLIC_MODEL_API_BASE` for the frontend. Keep port `8011` available when the vLLM inference service uses its private default port.

Model shared storage uses `POSTGRES_DSN`, `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET`, and `MINIO_SECURE` from `.env`. The current bucket is `rag-assets`; model objects use the `models/<model-id>/...` prefix. The backend writes PostgreSQL metadata before uploading raw model files to MinIO. Base models are uploaded explicitly from the model-management detail view; Best/Final Adapter directories are uploaded automatically when a task completes or stops with saved artifacts. See [requirements-storage.txt](requirements-storage.txt) for the pinned Python dependencies.

PostgreSQL-backed task, dataset, and inference stores share one in-process Psycopg connection pool. The default local/one-process sizing is `POSTGRES_POOL_MIN_SIZE=2` and `POSTGRES_POOL_MAX_SIZE=6`; pool wait timeout, waiting-request limit, idle lifetime, connection lifetime, and TCP connect timeout are configurable through the matching variables in `.env.example`. Increase the pool only after accounting for every API/worker process and the database server's remaining connection capacity.

For an isolated local UI/backend run, set `XUNTUI_STORAGE_BACKEND=sqlite`, `XUNTUI_SHARED_STORAGE_ENABLED=false`, and `XUNTUI_INFERENCE_AUTO_RECOVERY=false` in the process environment or `.env.local`. This keeps datasets, training tasks, and inference services in the local SQLite database, skips PostgreSQL/MinIO shared-storage initialization, and prevents stale remote inference records from triggering Youyun recovery during local startup.

`npm run backend` applies that isolated profile by default, even when `.env.local` contains shared deployment credentials. Shared-storage integration from the local WSL runner must be explicitly enabled with `XUNTUI_LOCAL_STORAGE_BACKEND=postgres`, `XUNTUI_LOCAL_SHARED_STORAGE_ENABLED=true`, and `XUNTUI_LOCAL_INFERENCE_AUTO_RECOVERY=true`.

PostgreSQL and MinIO have different failure boundaries. If PostgreSQL metadata reads are temporarily unavailable, `/api/model-assets` still returns the local SQLite model catalog and marks shared-storage details unavailable; uploads remain blocked because the required order is PostgreSQL metadata first, MinIO objects second. `FATAL: sorry, too many clients already` means the PostgreSQL server is online but its global connection slots are exhausted. Resolve that on the database server by inspecting `pg_stat_activity`, clearing confirmed stale/idle clients, and reviewing `max_connections` or a connection pooler; repeatedly reconnecting Navicat or restarting the local frontend does not fix it.

Useful shared-storage checks:

```sql
SELECT current_database(), current_schema(), current_user,
       inet_server_addr(), inet_server_port();
SELECT id, artifact_variant, status, source_task_id, minio_bucket,
       minio_object_prefix, created_at, uploaded_at
FROM public.model_files
ORDER BY created_at DESC;
```

`scripts/run_backend.py` starts Uvicorn inside WSL and forwards the API to Windows localhost. It passes `/root/miniconda3/envs/vllm_env/bin/python` to vLLM subprocesses by default, so no manual `WSLENV` setup is required. Override `XUNTUI_WSL_DISTRO`, `XUNTUI_WSL_PYTHON`, `XUNTUI_VLLM_PYTHON`, and `XUNTUI_BACKEND_PORT` when needed.

Stop the dev server before building, because Next dev and build share `.next`:

```powershell
npx tsc --noEmit
wsl.exe -d Ubuntu-24.04 --cd "/mnt/d/训推平台" -- /root/miniconda3/envs/unsloth_env/bin/python -m py_compile backend/app.py backend/store.py backend/train_sft.py backend/chat_inference.py backend/inference_service.py backend/cli_parser.py backend/test_store.py backend/test_chat_api.py backend/test_inference_service.py backend/test_cli_parser.py backend/test_app_cli.py backend/test_train_sft.py scripts/verify_best_checkpoint_e2e.py
wsl.exe -d Ubuntu-24.04 --cd "/mnt/d/训推平台" -- /root/miniconda3/envs/unsloth_env/bin/python -m unittest discover -s backend -p "test_*.py" -v
npm run build
```

Run the repeatable GPU path after starting the backend. Use the WSL interpreter shown below; the script uploads and publishes a small test dataset, trains a three-step QLoRA task with per-step validation, verifies both Adapter variants, and sends an Adapter chat request:

```powershell
wsl.exe -d Ubuntu-24.04 --cd "/mnt/d/训推平台" -- /root/miniconda3/envs/unsloth_env/bin/python scripts/verify_best_checkpoint_e2e.py
```

Training tasks can also be submitted and controlled from the command line. The CLI uses the same FastAPI queue and artifact rules as the web UI:

```powershell
python scripts\train_cli.py submit --name "Qwen SFT 实验" --model Qwen/Qwen3-1.7B --dataset-id qwenruozhi_v1 --dataset-name "弱智吧语料 SFT v1" --output-name qwen3-sft --tuning-mode qlora --validation-split 10
python scripts\train_cli.py watch <task-id>
python scripts\train_cli.py stop <task-id>
python scripts\train_cli.py artifacts <task-id>
```

Use `--early-stopping` with `--patience 3` and `--min-delta 0` to enable automatic early stopping in a CLI configuration; the web form exposes the same controls. A `validationSplit` of `0` is allowed; in that case the best Adapter is selected from training Loss and the CLI/UI warn that this is not a validation-based generalization signal.

The new-task form also has an execution-mode selector. Choose `命令行执行` to hide the regular parameter panels and edit, paste, or copy the restricted training configuration. The editor uses one option per line (blank lines are allowed); the platform treats line breaks as argument separators and does not require shell continuation characters. In this mode, choose a published text dataset from the dataset selector above the editor; changing the selection updates only its canonical `--dataset-id`, `--dataset-name`, and matching `--task-type`, preserving other hand-edited parameters. Dataset management also shows a copyable ID for terminal/API users. The backend treats the ID as authoritative and rejects unknown, unpublished, or SFT/DPO-mismatched datasets. Clicking submit starts the same WSL training queue directly; no config download or second terminal step is required.

The worker emits task-type-aware diagnostics. DPO runs are labeled as DPO in compatibility warnings and failure logs; CUDA/NVRTC/cuDNN runtime paths are added to the training subprocess environment. A dynamic-library error still means the WSL environment needs repair and should be diagnosed from the emitted GPU/library details rather than inferred from the frontend.

Do not run a training job or start a vLLM service as part of a documentation check.

Inference services also support a custom vLLM command mode. In the create form, choose `自定义命令行`; only the service name, completed training-task selector, and command editor remain visible. Paste a serving command and click `确认创建`; the backend parses and validates it automatically, saves the configuration as `待启动`, and allocates GPU only after `启动服务` is clicked. The backend accepts vLLM serving commands only, binds the selected completed task's Base Model and Best Adapter plus the private service port, rejects arbitrary shell syntax, and records custom-flag usage for future product decisions. The command mode does not support Base Model-only, Final Adapter-only, merged-model deployment, public serving, or arbitrary shell commands.

### 4-bit inference on an 8 GB GPU

The inference form includes `权重量化` with `4-bit BitsAndBytes`. This adds `--load-format bitsandbytes` and `--quantization bitsandbytes` to managed vLLM launches; the Adapter remains attached to the same Base Model. The inference environment must contain `bitsandbytes` (verified local version `0.50.1`).

Verified custom command for task `ft-260819-172419-82e0` and `Qwen/Qwen2.5-3B-Instruct`:

```bash
python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen2.5-3B-Instruct \
  --load-format bitsandbytes \
  --quantization bitsandbytes \
  --dtype float16 \
  --enable-lora \
  --lora-modules adapter=/root/xuntui-platform/artifacts/ft-260819-172419-82e0/adapter \
  --max-model-len 1024 \
  --max-num-seqs 2 \
  --max-num-batched-tokens 1024 \
  --gpu-memory-utilization 0.80 \
  --enforce-eager \
  --host 0.0.0.0 \
  --port 8011
```

The platform replaces the service ID, Adapter name, and private port after validation. Quantized weights remain compressed in GPU memory; computation may use fused kernels or on-the-fly dequantization. KV Cache is a separate allocation.

## Phase-two LLaMA-Factory status

The phase-two backend includes `LlamaFactoryParameters`/task models, platform-to-YAML conversion, `TrainingExecutor`, `LocalLlamaFactoryExecutor`, isolated task work directories, dataset materialization, safe `create_subprocess_exec` launching, logs/metrics/events, Best/Final Adapter artifact collection, and SHA-256 manifests. The visual path adds `LocalVisionTrainingExecutor` and `VisionTrainingRuntime`, which validate the materialized multimodal bundle, run visual SFT/DPO in a separate queue, share the GPU execution lock, and persist confirmation, stop/retry, loss, logs, and artifacts. The local smoke command at `scripts/llamafactory_smoke_cli.py` validates the process contract only; it does not download a model or perform training.

The phase-two code was pushed to Gitee in commit `ffa20f8` on branch `master`. The historical deployment and real CPU acceptance checklist is preserved in [PHASE2_DEPLOYMENT_HANDOFF.md](docs/archive/PHASE2_DEPLOYMENT_HANDOFF.md); it may contain superseded status details.

## Documentation map

- [CONTRIBUTING.md](CONTRIBUTING.md): team branch workflow, testing, merge requests, release tags, shared-account precautions, and recommended Codex instructions.
- [CURRENT_STATE.md](CURRENT_STATE.md): the primary context for a new feature chat; includes audited implementation status, the current end-to-end acceptance gate, priorities, runtime locations, bugs, and technical debt.
- [ARCHITECTURE.md](ARCHITECTURE.md): current local architecture and target cloud migration shape.
- [PROJECT_BASELINE.md](PROJECT_BASELINE.md): stable product scope, durable decisions, reasons, and rejected alternatives; it is intentionally not a changelog.
- [docs/archive/](docs/archive/): historical handoff and workstream notes. Read these only when tracing an older decision, bug, or deployment event; current rules come from `CONTRIBUTING.md`, `CURRENT_STATE.md`, and the live Git state.

`AGENTS.md` is intentionally absent. For a new feature chat, read `CONTRIBUTING.md`, `CURRENT_STATE.md`, and `PROJECT_BASELINE.md` first. Confirm requirements and acceptance criteria with the user before checking the runtime environment or editing code.

## Data, artifacts, and Git

Source code is under `app/`, `components/`, `lib/`, `backend/`, and `scripts/`. The sample dataset is under `data/qwenruozhi_v1`; its authorization is not yet confirmed. User uploads, SQLite databases, WSL artifacts/checkpoints, model caches, `.next`, `node_modules`, Python caches, and `unsloth_compiled_cache` are runtime or cache data and must not be committed. The repository uses Gitea `origin` at `http://git.leiziapi.cn:3003/leifengyang/cppt-platform.git`; all branch and tag pushes go there. Team members develop on separate personal feature branches and merge reviewed work into `master`; only tested `master` commits receive release tags. See [CONTRIBUTING.md](CONTRIBUTING.md). `.env`, runtime outputs, caches, dependencies, databases, logs, and build artifacts are ignored; review `git status --short --ignored` before every push.

## Security and licensing gate

Before any shared or cloud deployment, confirm the source license for `data/qwenruozhi_v1`, the license and commercial terms for each ModelScope model (including Qwen variants), privacy/secret handling for uploaded data, retention/deletion policy, authentication and business-space isolation, and production CORS/path access rules. Never put a ModelScope token in browser code.
