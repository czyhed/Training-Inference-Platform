# Architecture

## Snapshot

The published baseline is `v3.0.130` at commit `97b7b14` (2026-09-13), which matches `origin/master`. The production capability boundary below is the next unreleased change.

## Production capability boundary

Production sets `XUNTUI_LOCAL_RUNTIME_ENABLED=false` and `XUNTUI_EVALUATION_ENABLED=false`. The API publishes the active boundary through `GET /api/capabilities`; the frontend uses that response to expose only Youyun training and inference, hide CLI training and unfinished evaluation entry points, and label ModelScope/Hugging Face as remote download fallbacks. The backend enforces the same boundary for task creation, chat generation, worker queue claiming, inference lifecycle actions, and evaluation mutations, so saved requests cannot bypass the UI. Historical evaluation records remain readable.

Development keeps `XUNTUI_LOCAL_RUNTIME_ENABLED=true` by default and can continue to use the existing local LLaMA-Factory and vLLM paths. Production images build with `INSTALL_LOCAL_RUNTIME=0`, which leaves the local implementation in the source tree but omits its Torch, LLaMA-Factory, and vLLM environments from the deployed image.

When evaluation is enabled for later acceptance, it accepts only completed Youyun training tasks with a usable Adapter and a retained instance in `awaiting_comparison`. It reuses that instance and the exact base-model source recorded by the training task. A recorded Youyun public-model path takes priority; otherwise the same repository ID is downloaded on the Youyun instance through ModelScope with Hugging Face fallback. The production API server does not download or load that model.


## Remote log collection (unreleased)

Remote training and vLLM use `RemoteLogCollector` with a dedicated SSH transport, independent of control/health/chat/SFTP traffic. A short Python stdlib helper follows the file on the GPU host (100 ms local checks), emits at most 64 KiB raw-byte frames, and exits on SSH stdin EOF. A 32-frame queue applies backpressure. This removes repeated network `wc`/`tail` calls, not all local file polling; framework output buffering and network latency still apply.

Training log/Loss consumption is concurrent with exit/cancel checks. Inference keeps one collector across startup and running; health monitors and UI requests do not independently read remote files. REST refresh only ensures a missing collector on a nonterminal remote service. Stop/recovery/cleanup closes the owning collector before instance release. Final draining captures a finite EOF, including an unterminated line. Backend startup still does not create instances or resume training.

The collector commits raw-byte cursors at consumed line boundaries and preserves split UTF-8/CR/LF input. Inference checkpoints `logOffset`, `logFileId` (file identity/generation), and `logPython` in existing runtime metadata. After three stream failures it polls every five seconds and probes stream recovery every thirty seconds. File replacement/truncation emits a diagnostic; deleted uncollected files and crash-time non-atomic log/cursor writes are not covered by an exactly-once guarantee. Exception details and credentials are not emitted in collector diagnostics. Single lines over 1 MiB are explicitly split with a warning to bound memory.

`XUNTUI_REMOTE_LOG_FOLLOW_ENABLED=false` disables streaming but retains the single-owner incremental polling path; restart the backend to apply. WebSocket log/state/metrics/heartbeat messages, `afterId`, HTTP fallback, and the approximately 0.8-second database-to-browser dispatch loop are unchanged. CPU tests cover a real helper subprocess, partial input, backlog, transport degradation/recovery, and lifecycle ownership; no real GPU/SSH deployment acceptance has been performed.
## Current local path

```text
Browser (Next.js 14 / React / TypeScript)
        | hard-coded http://127.0.0.1:8010/api
        v
FastAPI (WSL Uvicorn)
        |-- SQLite: dataset, task, model-asset, inference-service, log, and metric metadata
        |-- WSL filesystem: uploaded JSONL and task artifacts
        |-- ModelScope SDK: model search/download/cache
        |-- single asyncio training worker
        |       `-- subprocess: Unsloth + TRL + PEFT SFT/DPO
        |               `-- CUDA GPU
        `-- single inference runtime
                `-- subprocess: vLLM OpenAI API server + LoRA
                        `-- same CUDA GPU (mutually exclusive with training)
```

`scripts/run_backend.py` discovers the WSL IP, starts Uvicorn in `Ubuntu-24.04`, and forwards Windows localhost port 8010. The frontend dev server is normally port 3001. After backend source changes, restart the existing Uvicorn process and verify newly added routes such as `/api/model-assets`; the 2026-08-18 model-management 404 was resolved this way. `NEXT_PUBLIC_MODEL_API_BASE` can point only the model-management preview at an alternate local API port when 8010 is occupied.

## Frontend boundaries

- `app/page.tsx` owns the overview shell, fixed navigation, dataset list/create, upload progress, publish/clone/delete, dataset prompt preview, and dataset-repair drawer.
- `components/overview-module.tsx` owns system-level overview polling using real health, GPU, task, dataset, inference, and Youyun APIs.
- `components/dataset-repair-panel.tsx` owns structured validation errors, clickable line定位, manual JSONL editing, preview, and revalidation; it does not auto-rewrite dataset content.
- `components/training-module.tsx` owns task create/list/detail, model search, parameter forms, the web/CLI execution-mode selector, multiline command editor, published-dataset binding for CLI mode, log/Loss display, chat comparison, and artifact actions.
- `components/model-management-module.tsx` owns model-asset list/filter/detail, cache scan, manual base registration, rename, path refresh, archive/restore, and training/deployment handoff actions.
- `components/inference-module.tsx` owns inference-service list/create/detail, lifecycle actions, exposure state, archive/restore/delete, Base Model + Best Adapter selection, managed vLLM configuration (including optional 4-bit BitsAndBytes quantization), custom-command creation, and service-scoped SSE online debug. Custom mode hides managed parameter controls and performs validation automatically when the service record is created.
- `lib/types.ts` defines the shared UI payload shapes.
- The active views use `fetch` against the FastAPI API. Static mock data is not a runtime source; frontend tests define any fixtures locally in the test that consumes them.

## Backend boundaries

- `backend/app.py` defines HTTP schemas/routes, text validation, ModelScope search, task configuration, chat process orchestration, and the single-worker lifecycle. `POST /api/tasks/cli` accepts a restricted multiline training configuration, parses it into the same task payload as the web form, validates it, and enqueues it without executing arbitrary Shell text.
- `backend/cli_parser.py` tokenizes one non-empty configuration line at a time, accepts the optional `python scripts/train_cli.py submit` prefix, reports syntax and unsafe-command errors with line numbers, validates task options and allowlisted `--trainer-param KEY=VALUE` entries, and never invokes a shell. The CLI task route then treats the selected dataset ID as authoritative for canonical name and structure checks.
- `backend/store.py` owns SQLite schema and serialization for datasets, tasks, logs, and metrics. Dataset rows also retain structured validation reports, repair history, and version numbers.
- `backend/store.py` also owns the local `model_assets` table, base-model registration/discovery records, trained-model synchronization from completed task artifacts, lineage serialization, Adapter variant metadata, and archive/name updates.
- `backend/model_storage.py` owns the shared model-storage boundary: PostgreSQL `model_files`/`model_file_entries` metadata, per-file MD5 and manifest MD5, MinIO object keys, upload status transitions, idempotent retries, and filtering of loadable base/Adapter files. SQLite remains the local training queue; PostgreSQL is the durable shared model metadata store.
- `backend/sdxl_script_artifact.py` owns the immutable SDXL Diffusers training-script artifact: manifest/digest validation, commit-addressed MinIO sync, bounded jsDelivr/GitHub fallback, and atomic local caching. The Youyun provider checks the image copy first and only starts training after remote SFTP upload plus `sha256sum` verification.
- `backend/providers/youyun/resource_health.py` owns the process-local cooldown registry for transient Pod/UHost create failures. Resource resolution and visual/image training runners consult it to filter unhealthy candidates and perform cleanup-confirmed Pod-to-UHost fallback.
- `backend/providers/youyun/inference.py` applies the same cooldown and cleanup-confirmed Pod-to-UHost fallback while starting visual and image-generation vLLM services; the runtime persists the actual fallback resource in the service config.
- The active training path is `LocalLlamaFactoryExecutor` -> `backend/llamafactory.py` -> administrator-configured `llamafactory-cli`; the Youyun runner reuses the same generated YAML and rewrites only remote paths and GPU precision. `backend/train_sft.py` is the retained legacy Unsloth/TRL runner used by focused compatibility tests, not the worker path selected by `backend/app.py`.
- `backend/chat_inference.py` loads a base model or Adapter and serves line-delimited multi-turn generation requests with thinking disabled.
- `backend/inference_service.py` owns inference-service SQLite persistence, Best-Adapter validation, cached ModelScope path resolution, managed/custom vLLM argv construction, restricted custom-command parsing, parameter-usage counting, process lifecycle, one-attempt recovery, archive/delete rules, chat/SSE proxying, log capture, and Prometheus metric parsing.
- `backend/vision.py` owns visual model capability probing, safe IMAGE_TEXT ZIP extraction, image signature checks, evaluation metrics, and OpenAI-compatible image Data URLs. `backend/vision_training.py` validates normalized image/question/answer records and deterministically materializes LLaMA-Factory multimodal ShareGPT train/validation files, copied image assets, `dataset_info.json`, `train.yaml`, and a reproducibility manifest. `backend/vision_executor.py` validates the materialized visual bundle and reuses the safe LLaMA-Factory subprocess lifecycle; `backend/vision_runtime.py` owns the persistent visual queue, confirmation, stop/retry state machine, metrics, logs, and artifact collection. `backend/vision_api.py` exposes visual model, deployment, training, inference, evaluation, preflight, and materialization-preview APIs. Visual jobs use their own queue and share the single-GPU execution lock with text training and inference.
- `backend/test_store.py` covers metric parsing, task lifecycle, best/final selection, artifact serialization, and legacy fallback. `backend/test_chat_api.py` covers selected-Adapter resolution and verifies that base comparison does not load an Adapter. GPU behavior is exercised separately by `scripts/verify_best_checkpoint_e2e.py`.
- `backend/test_inference_service.py` covers service persistence, managed/custom command construction, custom-command safety and artifact matching, Best-Adapter deployment validation, lifecycle behavior, archive/restore/delete, and recovery limits without launching a real GPU process. `backend/test_cli_parser.py` and `backend/test_app_cli.py` cover multiline training configuration parsing, line-numbered validation failures, shell-safety rejection, direct API queue submission, and dataset ID/state/type validation. `backend/test_train_sft.py` covers DPO normalization and sequence-length propagation without launching a GPU trainer.

## Data flow

1. Browser checks extension/size and sends a JSONL body to `POST /api/datasets/upload`.
2. FastAPI writes the file under the dataset root and returns a structured validation report for every checked JSON line. The UI lets the user click an issue to jump to the corresponding row, then previews and revalidates only explicitly submitted edits; untouched lines and blank lines remain unchanged, and original files remain preserved.
3. A non-failed dataset is `ready` (displayed as pending publish) until `POST /api/datasets/{id}/publish`.
4. Text task creation accepts one published SFT or DPO dataset whose structure matches `taskType`. Visual task creation accepts one published `IMAGE_TEXT` dataset, a probed visual base model, and SFT or DPO parameters. The visual UI runs preflight, shows an explicit confirmation dialog, then submits to its own queue; cloning creates an `awaiting_confirmation` task that must be confirmed before execution.
5. The text worker or visual worker claims one queued task, materializes an isolated dataset/config directory, lets LLaMA-Factory resolve or download the selected ModelScope model, and trains an Adapter locally or on a one-shot Youyun instance. Both workers share one execution lock so a training process, inference service, and visual training job cannot claim the same GPU concurrently.
6. LLaMA-Factory stdout supplies logs and train/eval metrics. The executor derives progress only after a real metric and total-step record is observed, then collects Best/Final Adapter metadata when those files exist. Visual task detail exposes the loss history, best/final validation summary, logs, manifest, and Adapter paths; failed or stopped tasks can be retried after confirmation. A text task with a usable Adapter keeps its completed or manually stopped Youyun instance in `awaiting_comparison` until manual or retention-window release; other terminal paths clean the instance.
7. Chat requests use either the base model or an explicit `selected`, `best`, or `final` Adapter variant. The default selected variant is best when available; the response reports the actual variant, step, and path. Test/validation prompts are read from dataset files; no durable evaluation result is stored yet.
8. Inference-service creation reads a completed task and rejects it unless an explicit Best Adapter path exists on disk. The service record stores a vLLM configuration snapshot and does not allocate GPU resources while merely created or stopped.
9. Start resolves the cached ModelScope base-model snapshot for local services, or creates a one-shot Youyun instance for remote services. Local services launch `/root/miniconda3/envs/vllm_env/bin/python -m vllm.entrypoints.openai.api_server`; remote services establish SSH, select a Python that can import vLLM (installing the configured package into a PyTorch environment when enabled), upload the service Adapter, launch vLLM privately on the instance, and wait for its local `/health` endpoint on port 8011.
10. FastAPI captures local or remote vLLM logs into service logs, detects an exited remote process during startup, proxies service-scoped JSON/SSE chat requests, polls `/metrics`, and marks the service running, stopped, failed, recovering, or archived. Training and inference leases are mutually exclusive.
11. Stopping terminates vLLM and releases GPU memory. Archive retains service configuration/logs/metrics and source lineage; permanent deletion is allowed only after archive and does not delete the training task or Adapter.

Training subprocesses receive the CUDA runtime, NVRTC, and cuDNN library directories through their environment. This addresses missing `libnvrtc.so`-style loader failures when the libraries exist in the WSL/Conda installation but are absent from `LD_LIBRARY_PATH`; it does not replace repairing a broken WSL or incomplete CUDA installation.

## Current API surface

Datasets: `GET /api/datasets`, `POST /api/datasets/upload`, `GET /api/datasets/{id}/validation`, `POST /api/datasets/{id}/repair/preview`, `POST /api/datasets/{id}/repair`, `POST /api/datasets/{id}/revalidate`, `POST /api/datasets/{id}/publish`, `POST /api/datasets/{id}/clone`, `DELETE /api/datasets/{id}`, `GET /api/datasets/{id}/prompts`, `GET /api/datasets/{id}/records?page=&pageSize=&query=`. The records endpoint provides server-side pagination for DPO, SFT, and future JSONL-backed dataset types.

Tasks: `GET/POST /api/tasks`, `POST /api/tasks/cli` (multiline controlled training configuration), `GET /api/tasks/{id}`, `POST /api/tasks/{id}/stop`, `/retry`, `/clone`, `DELETE /api/tasks/{id}`. Task payloads carry `taskType` (`SFT` or `DPO`) and must match the published dataset structure.

Models: `GET /api/models/search`, `GET /api/model-assets`, `POST /api/model-assets/scan`, `POST /api/model-assets/base`, `PATCH /api/model-assets/{id}`, `POST /api/model-assets/{id}/refresh`, `POST /api/model-assets/{id}/upload`, and `GET /api/model-files`. Base-model upload is explicit; completed/stopped task Adapter variants are uploaded automatically after local artifacts are saved.

Shared model storage: PostgreSQL stores one logical record per base model or Adapter variant plus one row per physical file in `model_file_entries`. The record is inserted as `pending_upload` before any MinIO write, then moves through `uploading` to `available`; failures remain queryable as `failed` for retry. MinIO stores raw loadable files under `models/<model-id>/...`, not zip archives, merged models, or intermediate checkpoints. The metadata keeps SFT/DPO, LoRA/QLoRA, base-model repository/revision, actual training-time base path, dataset ID, source task, uploader, size, MD5, bucket, and object prefix.

Availability boundary: the SQLite model catalog is the local source for model-list identity, paths, lineage, and archive state, while PostgreSQL is the authoritative shared-file metadata store. If PostgreSQL cannot be read, model-list responses preserve the local catalog and attach `sharedStorage.status=unavailable`; they do not claim objects are absent. Uploads do not use this degradation path: metadata must be inserted in PostgreSQL before MinIO writes begin. A PostgreSQL connection-capacity outage therefore degrades shared-storage visibility and blocks uploads without erasing local model-management functionality.

Chat/artifacts: `POST /api/chat`, `POST /api/artifacts/open`.

Vision understanding: `GET/POST /api/vision/models`, model probe/compatibility routes, `GET/POST /api/vision/deployments`, `POST /api/vision/tasks/preflight`, `POST /api/vision/tasks/materialize-preview`, `POST /api/vision/tasks`, `GET /api/vision/tasks`, task detail plus `/stop`, `/retry`, `/confirm`, `/clone`, and `DELETE`, `POST /api/vision/chat`, service-scoped `vision-chat`, and visual evaluation validate/create/list/detail routes. Visual tasks never enter the text training queue.

Inference services: `GET/POST /api/inference/services`, `GET /api/inference/services/{id}`, `POST /api/inference/services/{id}/start`, `/stop`, `/restart`, `/recover`, `/archive`, `/restore`, `/exposure`, `DELETE /api/inference/services/{id}`, `GET /api/inference/services/{id}/metrics`, `/logs`, and `POST /api/inference/services/{id}/chat`. Custom command support adds `POST /api/inference/commands/validate` and `GET /api/inference/command-usage`.

Custom deployment commands are parsed with `shlex` and accepted only when they represent vLLM serving. The backend never uses `shell=True`; it rejects shell composition and validates any supplied `--model`, `--lora-modules`, `--host`, and `--port` against the selected training artifact and the platform's private port. Quantization flags such as `--load-format bitsandbytes` and `--quantization bitsandbytes` are accepted as non-platform-owned options. The stored raw command is normalized at launch and platform-owned flags are replaced before the subprocess starts.

The backend interpreter remains the Unsloth environment. `XUNTUI_VLLM_PYTHON` defaults to `/root/miniconda3/envs/vllm_env/bin/python` so inference subprocesses use the isolated vLLM environment. The vLLM environment must provide `bitsandbytes` when 4-bit deployment is selected. `XUNTUI_INFERENCE_PORT` defaults to private port 8011.

## Target cloud shape (not connected)

```text
Next.js -> authenticated API
              |-- PostgreSQL: tenants/spaces, users/roles, immutable dataset versions, tasks, evaluations
              |-- Redis: durable queue, progress/events, leases/retries
              |-- MinIO: dataset files, manifests, checkpoints, adapters, merged models
              |-- GPU training workers: Unsloth/LLaMA-Factory text SFT/DPO and visual IMAGE_TEXT SFT/DPO
              `-- GPU serving workers: vLLM replicas behind authenticated routing
```

The migration must preserve API contracts where practical, add tenant/role checks before exposing shared data, and make artifacts immutable/versioned. Do not treat the current SQLite schema or local paths as a production contract.

## 2026-09-14 视觉理解/文生图融合合并（feature/nlp-vision-fusion → master）

- 在保留有云专用生产能力（require_provider 门控、审计日志、运行时能力开关、镜像目录）的前提下，合入视觉理解与文生图两个业务域：数据集（IMAGE_TEXT / 文生图 ZIP 上传）、微调（视觉理解 SFT/DPO、文生图 LoRA）、模型管理（域切换器）、推理服务（视觉推理 + 文生图工作台）。
- 前端保持既有 NLP 页面骨架，通过业务域切换条（NLP / 视觉理解 / 文生图）挂载新模块；NLP 侧页面与交互不变。
- 后端以 master 生产架构为底座：InferenceStore 增补服务 API Key（issue/authenticate/revoke，PostgreSQL 迁移 5）；优云推理 `_upload_tree` 通用化（进度回调 + adapter 文件过滤 + sha256 断点跳过）；新增优云看护任务（超时/心跳超时/清理重试）。
- 平台概览路由（/api/platform/*）服务旧版概览页；数据集媒体与社区镜像校验路由随视觉/文生图域引入。
