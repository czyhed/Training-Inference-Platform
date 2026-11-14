# Product Baseline

This file is the stable product baseline. It is intentionally shorter than the implementation handoff and does not record run history or temporary code behavior. See [CURRENT_STATE.md](CURRENT_STATE.md) for what is actually implemented.

## New requirements intake

Teacher-provided requirements and future design directions are not yet recorded in this baseline. When they are supplied, update `CURRENT_STATE.md` first with the verbatim requirement summary, then promote only confirmed durable decisions into this file. Keep proposals, open questions, rejected options, and deferred work clearly separated from the current MVP contract.

## Product context

- Internal enterprise platform for controlled model training and inference workflows.
- Administrators provision accounts; public registration is not part of the product.
- The long-term authorization boundary is the business space: datasets, tasks, models, artifacts, and logs must be isolated by space.
- Login/authentication and the resource center are deferred because the core dataset-to-training-to-chat loop must be reliable first.

## Current MVP boundary

The runnable MVP is text-generation SFT and DPO:

- Upload a text JSONL dataset, validate it, and publish it.
- Create a task from exactly one published text SFT or DPO dataset; the task type must match the dataset structure.
- Select a ModelScope base model by preset, keyword search, or exact repository ID.
- Choose LoRA or QLoRA.
- Run on an automatically selected GPU through the Unsloth worker.
- With validation enabled, save the lowest-eval-loss Adapter and the final Adapter; without validation, save the lowest-train-loss Adapter and the final Adapter while clearly labeling the metric limitation.
- Inspect task state, logs, train/eval loss, best/final step and Loss, artifacts, and base-vs-Adapter multi-turn chat.
- For DPO, accept conversational `messages` + `chosen`/`rejected` records and legacy string `prompt`/`chosen`/`rejected` records; normalize both to `prompt`/`chosen`/`rejected` before TRL training.
- Use train/validation data for the first comparison; keep test/challenge evaluation as a later, held-out step.
- The MVP product flow is dataset -> training -> model management -> inference/deployment. Model management is a small standalone asset registry for base models and trained Adapters.
- Create an inference service only from a completed task with an explicit Best Adapter. The deployment unit is Base Model + Best Adapter; base-only, Final-Adapter fallback, and merged-model deployment are excluded.
- Run inference through vLLM with basic settings shown first and advanced scheduling/cache boundaries behind an expandable section. PagedAttention and continuous batching are read-only engine capabilities; KV Cache and acceleration effects are reported from runtime data where available.
- Support one local inference process at a time with start, stop, restart, one recovery attempt, test/online/offline exposure markers, online debug, archive, restore, and permanent deletion after archive. Managed and validated custom-command services share the same lifecycle: creation never allocates GPU, and only an explicit start executes vLLM.
- “Online” is an internal product state for future module integration. It does not publish the service to the public internet.

## Dataset baseline

The list presents three durable states:

```text
validation failed | pending publish | published
```

Uploading and processing feedback belongs to the create form, not to the persistent list state for this MVP. A failed dataset cannot be published or selected for training. A published dataset is an immutable version from the product perspective; edits should create a new version or clone that is revalidated.

Text formats:

- SFT: JSONL records whose first record contains alternating `user`/`assistant` messages, with an optional leading `system` message.
- DPO: JSONL records may use the conversational `messages` + `chosen`/`rejected` shape (where the preference values are assistant messages), and the legacy non-empty string `prompt`/`chosen`/`rejected` shape remains accepted. DPO task creation, normalization, and the TRL trainer path are implemented and have passed a short local run; a durable preference-quality benchmark remains future work.

Published or task-referenced dataset versions must not be hard-deleted. The safe product operation is archive or create a new version. This rule is a target contract; the current local code still needs enforcement.

## Training baseline

- Framework: Unsloth + TRL + PEFT, selected because it is already available in WSL and is suitable for the user's single-GPU memory constraints.
- Scope: text SFT and DPO; no full-parameter fine-tuning.
- One published SFT or DPO dataset per task; the task type must match the dataset structure and GPU/resource choice is automatic.
- LoRA: 16-bit base loading, default `Qwen/Qwen2.5-3B-Instruct`.
- QLoRA: 4-bit base loading, default `Qwen/Qwen2.5-7B-Instruct`.
- Both modes save Adapter output. Validation runs select the lowest-eval-loss Adapter and retain the final Adapter for comparison. Current no-validation runs select the lowest observed train-Loss Adapter and also retain the final Adapter; only legacy runs without explicit variants remain final-only. Merged-model output is optional and is based on the selected Adapter.
- Legacy completed tasks with only a final/unspecified Adapter are valid training results but are not deployable by the inference-service MVP. Clone and rerun them with validation under the current trainer to generate an explicit Best Adapter.
- Common parameters have defaults and are user-adjustable; less common controls belong in advanced settings.
- The selected maximum sequence length is the DPO ceiling for `max_prompt_length`, `max_completion_length`, and `max_length`. Fractional controls such as warmup ratio, weight decay, LoRA dropout, temperature, top-p, min-delta, and epochs accept decimal input; integer-only controls remain integer-only.
- Validation is optional: `validationSplit=0` is valid. When validation is enabled, the lowest `eval_loss` selects Best Adapter; otherwise the lowest observed training Loss is used with an explicit warning that it is not a generalization signal.
- Manual early stopping is available for every running task and saves Best plus Final Adapter outputs. Optional automatic early stopping uses `patience=3` and `minDelta=0`; stopped tasks with a valid Best Adapter may be deployed. True checkpoint resume is deferred.
- The training task has a controlled command-line execution mode for advanced users. Its editor uses one training option per line, allows blank lines, treats line breaks as argument separators, and submits directly to the same validated task queue. It does not execute arbitrary Shell text, require shell continuation characters, or promise that copied configuration is directly executable in PowerShell/Bash. The command itself determines LoRA/QLoRA and may set allowlisted advanced trainer parameters.
- CLI mode keeps published text-dataset selection in the UI and writes the selected dataset's canonical ID, name, and SFT/DPO type into the configuration. Dataset management exposes a copyable dataset ID for terminal/API users. The backend treats the ID as authoritative and rejects unknown, unpublished, or structure-mismatched datasets before queueing.
- The comparison is not a controlled LoRA/QLoRA benchmark when the default model sizes differ.
- Chat supports multiple turns and disables thinking output.
- Training diagnostics include task-type-aware warnings, GPU/configuration context, Python exception details, and CUDA/NVRTC/cuDNN library-path context. These improve diagnosis but do not substitute for a real environment repair when WSL/CUDA installation is damaged.

## Deferred scope

- Image/text-to-image datasets and training: planned flow includes JPG/PNG/WebP limits, batch/folder upload, previews, caption editing, automatic captioning, human review, server-generated `metadata.jsonl`, and Diffusers + Accelerate + PEFT.
- Vision-language LoRA and image/text-to-image training.
- Authentication, administrator account management, business-space enforcement, audit logs.
- Redis queue, PostgreSQL metadata, MinIO artifacts, multi-worker scheduling, and cloud deployment.
- Model publishing, approval, ratings, and broad model dashboards.
- Public inference endpoints, API-key/auth enforcement, autoscaling, multi-GPU tensor parallel scheduling, multi-service GPU placement, rolling upgrades, and production serving SLOs.

## Technology direction

- Frontend: React + TypeScript + Next.js, selected by the user.
- Local validation backend: FastAPI + SQLite + WSL filesystem, selected as the lowest-friction way to validate the loop on one machine.
- Production target: FastAPI services with PostgreSQL, Redis, and MinIO for durable metadata, asynchronous work, and shared object storage.
- Model source: ModelScope through presets, fuzzy search, or an exact repository ID, with credentials kept server-side.

## Durable decisions and rejected alternatives

| Decision | Reason or implication |
| --- | --- |
| Unsloth is the current training implementation. | LLaMA-Factory was previously used only through a preconfigured cloud/compute image; its setup commands and portable artifacts were not retained, so it is historical context rather than the current framework. |
| One published SFT or DPO dataset is selected per task. | The task type must match the dataset structure; this keeps the MVP form, task payload, and reproducibility contract understandable. |
| GPU selection remains automatic. | GPU/resource configuration is not exposed until there is a real scheduler and the user has a reason to control it. |
| Adapter is the default output; merged model is optional. | Adapter output is smaller and is the normal fine-tuning artifact; merging consumes additional disk and compute and is mainly useful for deployment. |
| Model management is a small standalone MVP asset registry. | Base models can be discovered from cache or manually registered; completed tasks create trained-model records with immutable lineage and best/final Adapter metadata. The API stores paths and metadata without copying model files. |
| Merge Model is outside the first deployment MVP. | Prefer loading a base model with the selected Adapter; generate a merged standalone model only when the inference service requires it. |
| Lowest validation Loss selects the default Adapter. | Validation runs retain both best and final variants so the selected artifact is explicit and the final state remains available for comparison. |
| Manual early stopping is a first-class training action. | A safe stop preserves Best/Final Adapter outputs; optional automatic early stopping is opt-in and warns that the selected result may not be globally best. |
| Advanced users may submit a validated multiline training configuration. | The frontend submits the editor content directly to `POST /api/tasks/cli`; the backend parses one option per line, reports line-numbered errors, rejects shell composition, and converts valid input into the same queue payload as web submission. |
| LoRA and QLoRA are both selectable. | This supports practical local experiments, but the default 3B LoRA versus 7B QLoRA setup is not a controlled single-variable benchmark. |
| Train and validation prompts are used first. | Test and challenge sets remain held out until the training loop and evaluation method are stable. |
| Text-to-image and vision-language training remain deferred. | Text SFT and DPO task paths share the same queue and artifact contract. |
| Do not delete Figma prototypes without confirmation. | The user's Figma free quota is limited; confirm that designs were saved before cleanup. |
| vLLM is the inference engine for the deployment MVP. | It provides OpenAI-compatible serving, PagedAttention, continuous batching, Prefix Caching, Chunked Prefill, LoRA loading, and Prometheus metrics without introducing a second serving abstraction. |
| Inference deploys Base Model + explicit Best Adapter only. | This preserves traceability to the lowest validation Loss and avoids silently serving a final-only legacy artifact. |
| Training and inference are mutually exclusive on the local GPU. | The current machine has one RTX 4070 Laptop GPU with about 8 GB VRAM and no scheduler capable of safely sharing it. |
| Service startup is manual; automatic recovery attempts at most once. | Creating a service must not allocate GPU resources, and failure must become visible instead of causing an unlimited restart loop. |
| Basic vLLM controls are primary; advanced controls are expandable. | New users can use safe defaults while experienced users can set dtype, GPU utilization, KV Cache mode/dtype, batch limits, Prefix Caching, Chunked Prefill, and timeout. |
| PagedAttention and continuous batching are read-only capabilities. | The platform exposes scheduling boundaries, not engine-internal implementation switches; actual cache and acceleration effects come from runtime metrics. |
| Archive precedes permanent deletion. | Archive stops/default-hides the service while retaining configuration, logs, metrics, and training lineage; permanent deletion removes service records only, never the source task or Adapter. |
| Complex vLLM deployments may use a validated custom command. | The command is saved but does not run at creation; startup executes only an allowlisted vLLM command as argv, rebinds Base Model + Best Adapter + service-owned networking fields, rejects shell syntax, and records flag frequency without exposing a statistics page in the MVP. |
