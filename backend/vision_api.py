from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Mapping

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.personal_youyun import check_resource_access
from backend.vision import (
    answer_metrics,
    can_use_vision_model_for_training,
    image_data_url,
    is_youyun_model_available,
    load_jsonl,
    probe_model,
    summarize_metrics,
    validate_image_bytes,
)
from backend.vision_training import (
    materialize_vision_training_bundle,
    resolve_vision_template,
    validate_vision_training_dataset,
)
from backend.youyun_vision_catalog import load_youyun_vision_catalog, parse_youyun_vision_catalog
from backend.youyun_vision_probe import (
    VisionProbeRequest,
    probe_remote_vision_environment,
)


class VisionModelRegister(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    repoId: str = Field(min_length=1, max_length=240)
    revision: str = Field(default="main", min_length=1, max_length=120)
    path: str = Field(default="", max_length=2000)


class VisionModelPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    archived: bool


class VisionChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    serviceId: str = Field(min_length=1, max_length=120)
    imageBase64: str = Field(min_length=16, max_length=30_000_000)
    imageFilename: str = Field(default="image.png", min_length=1, max_length=120)
    question: str = Field(min_length=1, max_length=20_000)
    maxTokens: int = Field(default=512, ge=1, le=32768)
    temperature: float = Field(default=0.2, ge=0, le=2)
    topP: float = Field(default=0.9, gt=0, le=1)
    stream: bool = False


class PublicVisionChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    imageBase64: str = Field(min_length=16, max_length=30_000_000)
    imageFilename: str = Field(default="image.png", min_length=1, max_length=120)
    question: str = Field(min_length=1, max_length=20_000)
    maxTokens: int = Field(default=512, ge=1, le=32768)
    temperature: float = Field(default=0.2, ge=0, le=2)
    topP: float = Field(default=0.9, gt=0, le=1)


class VisionEvaluationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    serviceId: str = Field(min_length=1, max_length=120)
    datasetPath: str = Field(min_length=1, max_length=2000)
    metadataName: str = Field(default="metadata.jsonl", min_length=1, max_length=120)
    maxSamples: int | None = Field(default=None, ge=1, le=10000)
    maxTokens: int = Field(default=256, ge=1, le=4096)
    temperature: float = Field(default=0.0, ge=0, le=2)
    topP: float = Field(default=1.0, gt=0, le=1)


class VisionEvaluationValidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    serviceId: str = Field(default="", max_length=120)
    datasetPath: str = Field(min_length=1, max_length=2000)
    metadataName: str = Field(default="metadata.jsonl", min_length=1, max_length=120)
    maxSamples: int | None = Field(default=None, ge=1, le=10000)
    maxTokens: int = Field(default=256, ge=1, le=4096)
    temperature: float = Field(default=0.0, ge=0, le=2)
    topP: float = Field(default=1.0, gt=0, le=1)


class VisionDeploymentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    modelType: Literal["base", "trained"] = "base"
    modelId: str = Field(min_length=1, max_length=120)
    taskId: str | None = Field(default=None, max_length=120)
    adapterVariant: Literal["best", "final"] | None = None
    description: str = Field(default="", max_length=500)
    owner: str = Field(default="平台管理员", min_length=1, max_length=120)
    maxModelLength: int = Field(default=4096, ge=128, le=131072)
    maxTokens: int = Field(default=512, ge=1, le=32768)
    temperature: float = Field(default=0.2, ge=0, le=2)
    topP: float = Field(default=0.9, gt=0, le=1)
    dtype: str = Field(default="auto", pattern="^(auto|float16|bfloat16)$")
    gpuMemoryUtilization: float = Field(default=0.75, gt=0, le=0.99)
    maxNumSeqs: int = Field(default=1, ge=1, le=1024)
    maxNumBatchedTokens: int = Field(default=2048, ge=128, le=131072)
    enablePrefixCaching: bool = True
    enableChunkedPrefill: bool = True
    tensorParallelSize: int = Field(default=1, ge=1, le=8)
    loadTimeout: int = Field(default=900, ge=30, le=3600)
    provider: Literal["local", "youyun"] = "local"
    youyunResource: dict[str, Any] | None = None


class _VisionInferenceStorePayload(BaseModel):
    name: str
    description: str
    taskId: str
    modelType: Literal["base", "trained"] = "base"
    adapterVariant: Literal["best", "final"] | None = None
    owner: str
    modelId: str
    workloadType: str = "vision_language"
    maxModelLength: int
    maxTokens: int
    temperature: float
    topP: float
    stream: bool = False
    dtype: str
    quantization: str = "none"
    gpuMemoryUtilization: float
    kvCacheMode: str = "auto"
    kvCacheMemoryGiB: float | None = None
    kvCacheDtype: str = "auto"
    maxNumSeqs: int
    maxNumBatchedTokens: int
    enablePrefixCaching: bool
    enableChunkedPrefill: bool
    tensorParallelSize: int
    loadTimeout: int
    deploymentMode: str = "managed"
    customCommand: str | None = None
    provider: Literal["local", "youyun"] = "local"
    youyunResource: dict[str, Any] | None = None


class VisionTaskPreflight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    datasetId: str = Field(min_length=1, max_length=120)
    modelId: str = Field(min_length=1, max_length=120)
    taskType: str = Field(default="SFT", pattern="^(SFT|DPO)$")
    dpoBeta: float = Field(default=0.1, gt=0, le=1)
    tuningMode: str = Field(default="qlora", pattern="^(lora|qlora)$")
    provider: str = Field(default="youyun", pattern="^(local|youyun)$")
    imageMaxPixels: int = Field(default=401408, gt=0, le=4_000_000)
    batchSize: int = Field(default=1, ge=1, le=16)
    gradientAccumulation: int = Field(default=8, ge=1, le=256)
    validationSplit: int = Field(default=10, ge=0, le=30)
    youyunResource: dict[str, Any] | None = None


class VisionTaskCreate(VisionTaskPreflight):
    name: str = Field(min_length=1, max_length=100)
    outputName: str = Field(min_length=1, max_length=120)
    epochs: float = Field(default=2, gt=0, le=100)
    learningRate: float = Field(default=2e-5, gt=0, le=10)
    loraRank: int = Field(default=64, ge=1, le=256)
    loraAlpha: int = Field(default=128, ge=1, le=512)
    loraDropout: float = Field(default=0.05, ge=0, lt=1)
    gradientCheckpointing: bool = True


class VisionRemoteProbe(BaseModel):
    modelPath: str = Field(default="", max_length=2000)
    template: str = Field(default="qwen2_vl", min_length=1, max_length=80)
    tuningMode: Literal["lora", "qlora"] = "qlora"
    imagePath: str | None = Field(default=None, max_length=2000)
    youyunResource: dict[str, Any]


def _decode_image(value: str) -> bytes:
    raw = value.strip()
    if raw.startswith("data:") and "," in raw:
        raw = raw.split(",", 1)[1]
    try:
        return base64.b64decode(raw, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("imageBase64 不是合法的 Base64 图片内容。") from exc


def _safe_dataset_path(value: str, project_root: Path, dataset_root: Path) -> Path:
    requested = Path(value).expanduser()
    if not requested.is_absolute():
        requested = project_root / requested
    resolved = requested.resolve()
    allowed_roots = [project_root.resolve(), dataset_root.resolve()]
    if not any(resolved.is_relative_to(root) for root in allowed_roots):
        raise ValueError("评测集路径必须位于项目目录或数据集根目录内。")
    if not resolved.is_dir():
        raise ValueError("评测集目录不存在。")
    return resolved


def _message_text(response: dict[str, Any]) -> str:
    choices = response.get("choices") or []
    if not choices:
        raise ValueError("推理响应没有 choices。")
    content = choices[0].get("message", {}).get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(item.get("text", "")) for item in content if isinstance(item, dict)
        )
    return str(content)


def create_vision_router(
    store: Any,
    inference_runtime: Any,
    *,
    project_root: Path,
    dataset_root: Path,
    runtime_root: Path,
    training_runtime: Any | None = None,
    gpu_probe: Callable[[], dict[str, Any]] | None = None,
    youyun_config_check: Callable[[], list[str]] | None = None,
    youyun_resource_resolver: Callable[[Mapping[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    youyun_vision_probe: Callable[[VisionProbeRequest, Mapping[str, Any]], Awaitable[Any]] | None = None,
    youyun_image_validator: Callable[[str], Awaitable[str | None]] | None = None,
    youyun_model_catalog_loader: Callable[[], Awaitable[list[dict[str, Any]]]] | None = None,
) -> APIRouter:
    router = APIRouter(tags=["vision"])
    evaluation_root = runtime_root / "vision-evaluations"
    evaluation_root.mkdir(parents=True, exist_ok=True)
    training_preview_root = runtime_root / "vision-training-previews"
    training_preview_root.mkdir(parents=True, exist_ok=True)
    vision_task_root = Path(
        getattr(training_runtime, "task_root", runtime_root / "vision-tasks")
    ).expanduser().resolve()
    vision_task_root.mkdir(parents=True, exist_ok=True)
    jobs: dict[str, dict[str, Any]] = {}
    job_tasks: set[asyncio.Task[Any]] = set()
    live_youyun_catalog: list[dict[str, Any]] = []
    live_youyun_catalog_refreshed_at = 0.0

    async def refresh_live_youyun_catalog() -> None:
        nonlocal live_youyun_catalog, live_youyun_catalog_refreshed_at
        if youyun_model_catalog_loader is None:
            return
        now = time.monotonic()
        refresh_interval = max(
            5.0,
            float(os.environ.get("XUNTUI_YOUYUN_MODEL_CATALOG_CACHE_SECONDS", "30")),
        )
        if now - live_youyun_catalog_refreshed_at < refresh_interval:
            return
        try:
            rows = await youyun_model_catalog_loader()
        except Exception:
            live_youyun_catalog_refreshed_at = now
            return
        live_youyun_catalog = parse_youyun_vision_catalog(rows)
        live_youyun_catalog_refreshed_at = now

    def youyun_catalog_models() -> list[dict[str, Any]]:
        import hashlib

        result: list[dict[str, Any]] = []
        configured_and_live = [*load_youyun_vision_catalog(), *live_youyun_catalog]
        seen_repos: set[str] = set()
        for item in configured_and_live:
            repo_id = str(item.get("repoId") or "").strip()
            revision = str(item.get("revision") or "main").strip()
            repo_key = repo_id.casefold()
            if not repo_id or repo_key in seen_repos:
                continue
            seen_repos.add(repo_key)
            model_id = "vision-" + hashlib.sha1(
                f"{repo_id}@{revision}".encode("utf-8")
            ).hexdigest()[:12]
            remote_path = str(item.get("path") or "")
            result.append(
                {
                    "id": model_id,
                    "name": str(item.get("name") or repo_id),
                    "repoId": repo_id,
                    "revision": revision,
                    "path": "",
                    "remotePath": remote_path,
                    "sizeGb": item.get("sizeGb"),
                    "recommendedVramGb": item.get("recommendedVramGb"),
                    "capabilities": list(item.get("capabilities") or []),
                    "supported": False,
                    "probeStatus": "candidate",
                    "probeMessage": "优云模型库已提供共享挂载路径，训练前将执行远端模型完整性探针。",
                    "availableOnYouyun": bool(remote_path),
                    "youyunCatalog": True,
                    "source": "youyun_model_catalog",
                    "template": item.get("template"),
                    "imageId": item.get("imageId"),
                    "imageName": item.get("imageName"),
                    "imageStatus": item.get("imageStatus"),
                }
            )
        return sorted(
            result,
            key=lambda item: (
                int(item.get("recommendedVramGb") or 999),
                str(item.get("name") or "").casefold(),
            ),
        )

    def vision_model_for_training(model_id: str) -> dict[str, Any] | None:
        model = store.get_vision_model(model_id)
        if model is not None and model.get("archived"):
            return None
        if model is not None:
            catalog_match = next(
                (
                    item
                    for item in youyun_catalog_models()
                    if str(item.get("repoId") or "").casefold()
                    == str(model.get("repoId") or "").casefold()
                    and str(item.get("remotePath") or "").strip()
                ),
                None,
            )
            if catalog_match is not None and not str(model.get("remotePath") or "").strip():
                model = {
                    **model,
                    "remotePath": catalog_match["remotePath"],
                    "availableOnYouyun": True,
                }
            return model
        catalog_model = next(
            (item for item in youyun_catalog_models() if item["id"] == model_id),
            None,
        )
        if catalog_model is not None:
            return catalog_model
        return None

    def save_job(job: dict[str, Any]) -> None:
        (evaluation_root / f"{job['id']}.json").write_text(
            json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def load_jobs() -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for path in evaluation_root.glob("*.json"):
            try:
                result.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
        return sorted(result, key=lambda item: item.get("createdAt", ""), reverse=True)

    def cleanup_training_previews() -> None:
        root = training_preview_root.resolve()
        now = time.time()
        candidates: list[Path] = []
        for path in training_preview_root.iterdir():
            resolved = path.resolve()
            if not path.is_dir() or resolved.parent != root:
                continue
            try:
                if now - path.stat().st_mtime > 24 * 60 * 60:
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    candidates.append(path)
            except OSError:
                continue
        for path in sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True)[9:]:
            shutil.rmtree(path, ignore_errors=True)

    def vision_task_path(task_id: str) -> Path:
        if not task_id or Path(task_id).name != task_id or task_id in {".", ".."}:
            raise HTTPException(status_code=400, detail="视觉任务 ID 不合法。")
        return vision_task_root / f"{task_id}.json"

    def save_vision_task(task: dict[str, Any]) -> None:
        if training_runtime is not None:
            training_runtime.save(task)
            return
        vision_task_path(str(task["id"])).write_text(
            json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def read_json_object(path: Path) -> dict[str, Any] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def normalize_legacy_vision_task(task: Mapping[str, Any]) -> dict[str, Any]:
        """Hide fields from the removed online-download fallback in old records."""
        normalized = dict(task)
        normalized.pop("downloadFallback", None)
        normalized.pop("downloadAllowed", None)
        preflight = normalized.get("preflight")
        if isinstance(preflight, Mapping):
            clean_preflight = dict(preflight)
            clean_preflight.pop("downloadFallback", None)
            clean_preflight.pop("downloadAllowed", None)
            normalized["preflight"] = clean_preflight
        return normalized

    def load_vision_tasks(*, include_details: bool = False) -> list[dict[str, Any]]:
        source_tasks = (
            training_runtime.list_tasks()
            if training_runtime is not None
            else [
                task
                for path in vision_task_root.glob("*.json")
                if (task := read_json_object(path)) is not None
            ]
        )
        result: list[dict[str, Any]] = []
        for task in source_tasks:
            task = normalize_legacy_vision_task(task)
            if not include_details:
                task = {
                    key: value
                    for key, value in task.items()
                    if key not in {"yaml", "preflight", "logs", "lossHistory"}
                }
            result.append(task)
        return sorted(result, key=lambda item: item.get("createdAt", ""), reverse=True)

    def _adapter_for_vision_task(
        task: Mapping[str, Any], variant: str | None = None
    ) -> dict[str, Any] | None:
        artifacts = task.get("artifacts") or []
        candidates = [
            artifact
            for artifact in artifacts
            if isinstance(artifact, Mapping)
            and artifact.get("type") == "adapter"
            and artifact.get("variant") in {"best", "final"}
            and Path(str(artifact.get("path") or "")).expanduser().is_dir()
        ]
        if variant:
            candidates = [item for item in candidates if item.get("variant") == variant]
        else:
            candidates.sort(key=lambda item: (not bool(item.get("selected")), item.get("variant") != "best"))
        return dict(candidates[0]) if candidates else None

    def _vision_inference_model_options(provider: str) -> list[dict[str, Any]]:
        if provider not in {"local", "youyun"}:
            raise HTTPException(status_code=400, detail="视觉模型来源只能是 local 或 youyun。")
        options: list[dict[str, Any]] = []
        catalog_models = list(store.list_vision_models())
        seen_catalog_repos = {str(model.get("repoId") or "").strip().casefold() for model in catalog_models}
        catalog_models.extend(
            model
            for model in youyun_catalog_models()
            if str(model.get("repoId") or "").strip().casefold() not in seen_catalog_repos
        )
        seen_repos: set[str] = set()
        for model in catalog_models:
            seen_repos.add(str(model.get("repoId") or "").strip().casefold())
            remote_available = is_youyun_model_available(model)
            local_ready = bool(
                model.get("supported")
                and model.get("probeStatus") == "passed"
                and Path(str(model.get("path") or "")).expanduser().is_dir()
            )
            youyun_ready = remote_available
            ready = local_ready if provider == "local" else youyun_ready
            options.append(
                {
                    "id": str(model["id"]),
                    "label": str(model.get("name") or model.get("repoId") or model["id"]),
                    "group": "base",
                    "modelId": str(model["id"]),
                    "repoId": str(model.get("repoId") or ""),
                    "modelPath": str(model.get("remotePath") or model.get("path") or ""),
                    "supported": bool(model.get("supported")),
                    "probeStatus": str(model.get("probeStatus") or ""),
                    "availableOnYouyun": remote_available,
                    "readyForLocal": local_ready,
                    "readyForYouyun": youyun_ready,
                    "ready": ready,
                    "reason": None if ready else (
                        "模型目录不存在或视觉探针未通过。"
                        if provider == "local"
                        else "优云公共模型库没有可用的基座模型。"
                    ),
                }
            )

        for task in load_vision_tasks(include_details=True):
            if task.get("state") not in {"completed", "stopped"}:
                continue
            base_model_id = str(task.get("modelId") or "")
            base_model = vision_model_for_training(base_model_id)
            if base_model is None:
                continue
            remote_available = is_youyun_model_available(base_model)
            youyun_ready = remote_available
            local_ready = bool(
                base_model.get("supported")
                and base_model.get("probeStatus") == "passed"
                and Path(str(base_model.get("path") or "")).expanduser().is_dir()
            )
            for artifact in (task.get("artifacts") or []):
                if not isinstance(artifact, Mapping) or artifact.get("type") != "adapter":
                    continue
                variant = str(artifact.get("variant") or "")
                adapter_path = Path(str(artifact.get("path") or "")).expanduser()
                if variant not in {"best", "final"} or not adapter_path.is_dir():
                    continue
                ready = local_ready if provider == "local" else youyun_ready
                options.append(
                    {
                        "id": f"{task['id']}:{variant}",
                        "label": f"{task.get('name') or task['id']} · {variant.title()} Adapter",
                        "group": "trained",
                        "modelId": base_model_id,
                        "repoId": str(base_model.get("repoId") or ""),
                        "modelPath": str(base_model.get("remotePath") or base_model.get("path") or ""),
                        "taskId": str(task["id"]),
                        "adapterVariant": variant,
                        "adapterPath": str(adapter_path.resolve()),
                        "supported": True,
                        "probeStatus": str(base_model.get("probeStatus") or ""),
                        "availableOnYouyun": remote_available,
                        "readyForLocal": local_ready,
                        "readyForYouyun": youyun_ready,
                        "ready": ready,
                        "reason": None if ready else (
                            "训练任务的基座模型未准备好本地目录。"
                            if provider == "local"
                            else "训练任务的基座模型不可用于优云推理。"
                        ),
                    }
                )
        return options

    def read_vision_task(task_id: str) -> dict[str, Any] | None:
        if training_runtime is not None:
            return training_runtime.get(task_id)
        path = vision_task_path(task_id)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=500, detail=f"视觉任务记录不可读取：{exc}") from exc

    async def materialize_training_payload(
        payload: VisionTaskCreate, preflight: dict[str, Any]
    ) -> dict[str, Any]:
        dataset = store.get_dataset(payload.datasetId)
        model = vision_model_for_training(payload.modelId)
        assert dataset is not None and model is not None
        train_path = Path(str((dataset.get("paths") or {}).get("trainPath") or ""))
        preview_id = f"vtp-{uuid.uuid4().hex[:12]}"
        preview_root = training_preview_root / preview_id
        try:
            await asyncio.to_thread(cleanup_training_previews)
            template = resolve_vision_template(
                str(model.get("repoId") or ""), str(model.get("path") or "")
            )
            bundle = await asyncio.to_thread(
                materialize_vision_training_bundle,
                dataset_id=payload.datasetId,
                train_path=train_path,
                destination=preview_root,
                model=str(model.get("repoId") or model.get("path") or ""),
                template=template,
                tuning_mode=payload.tuningMode,
                task_type=payload.taskType,
                dpo_beta=payload.dpoBeta,
                image_max_pixels=payload.imageMaxPixels,
                batch_size=payload.batchSize,
                gradient_accumulation=payload.gradientAccumulation,
                validation_split=payload.validationSplit,
                epochs=payload.epochs,
                learning_rate=payload.learningRate,
                lora_rank=payload.loraRank,
                lora_alpha=payload.loraAlpha,
                lora_dropout=payload.loraDropout,
                gradient_checkpointing=payload.gradientCheckpointing,
            )
        except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            shutil.rmtree(preview_root, ignore_errors=True)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "previewId": preview_id,
            "status": "materialized",
            "taskType": payload.taskType,
            "llamaFactoryContract": "0.9.5",
            "preflight": preflight,
            **bundle,
        }

    @router.get("/api/vision/models")
    async def list_vision_models(
        provider: str | None = None,
        includeArchived: bool = False,
    ) -> list[dict[str, Any]]:
        if provider not in {None, "local", "youyun"}:
            raise HTTPException(status_code=400, detail="视觉模型来源只能是 local 或 youyun。")
        if provider in {None, "youyun"}:
            await refresh_live_youyun_catalog()
        models = [
            {
                **model,
                "availableOnYouyun": is_youyun_model_available(model),
            }
            for model in store.list_vision_models(include_archived=includeArchived)
        ]
        if provider is None:
            seen = {str(model.get("repoId") or "").strip().casefold() for model in models}
            models.extend(
                model
                for model in youyun_catalog_models()
                if str(model.get("repoId") or "").strip().casefold() not in seen
            )
        if provider == "local":
            return [
                model
                for model in models
                if model.get("supported") and model.get("probeStatus") == "passed"
            ]
        if provider == "youyun":
            available = [
                model
                for model in models
                if model["availableOnYouyun"]
            ]
            seen = {str(model.get("repoId") or "").strip().casefold() for model in available}
            for model in youyun_catalog_models():
                if not model.get("availableOnYouyun"):
                    continue
                repo_id = str(model.get("repoId") or "").strip().casefold()
                if repo_id and repo_id not in seen:
                    available.append(model)
                    seen.add(repo_id)
            return available
        return models

    @router.get("/api/vision/youyun-catalog")
    async def list_youyun_vision_catalog() -> list[dict[str, Any]]:
        await refresh_live_youyun_catalog()
        return youyun_catalog_models()

    @router.post("/api/vision/models/{model_id}/remote-probe")
    async def remote_probe_vision_model(
        model_id: str,
        payload: VisionRemoteProbe,
    ) -> dict[str, Any]:
        model = vision_model_for_training(model_id)
        if model is None:
            raise HTTPException(status_code=404, detail="视觉模型不存在。")
        if youyun_vision_probe is None:
            raise HTTPException(status_code=503, detail="当前服务未配置优云视觉远端探针。")
        if youyun_resource_resolver is None:
            raise HTTPException(status_code=503, detail="当前服务未配置优云资源解析器。")
        try:
            resource = await youyun_resource_resolver(payload.youyunResource)
        except HTTPException:
            raise
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        request = VisionProbeRequest(
            repo_id=str(model.get("repoId") or ""),
            model_path=payload.modelPath.strip() or str(model.get("remotePath") or model.get("path") or ""),
            template=payload.template.strip() or str(model.get("template") or "qwen2_vl"),
            tuning_mode=payload.tuningMode,
            image_path=payload.imagePath,
        )
        result = await youyun_vision_probe(request, resource)
        report = result.to_dict() if hasattr(result, "to_dict") else dict(result)
        if report.get("passed"):
            stored = store.get_vision_model(model_id)
            source = stored or model
            store.upsert_vision_model(
                {
                    **source,
                    "supported": True,
                    "probeStatus": "passed",
                    "probeMessage": (
                        f"优云远端探针通过；推荐 {report.get('recommendedTuningMode') or payload.tuningMode}，"
                        f"预计至少 {report.get('estimatedVramGb') or 0}GB 显存。"
                    ),
                }
            )
        return report

    @router.post("/api/vision/models/register", status_code=status.HTTP_201_CREATED)
    async def register_vision_model(payload: VisionModelRegister) -> dict[str, Any]:
        import hashlib

        model_id = "vision-" + hashlib.sha1(
            f"{payload.repoId}@{payload.revision}".encode("utf-8")
        ).hexdigest()[:12]
        result = probe_model(payload.repoId, payload.path or None)
        return store.upsert_vision_model(
            {
                "id": model_id,
                "name": payload.name.strip(),
                "repoId": payload.repoId.strip(),
                "revision": payload.revision.strip(),
                "path": payload.path.strip(),
                "capabilities": result["capabilities"],
                "supported": result["supported"],
                "probeStatus": result["status"],
                "probeMessage": result["message"],
            }
        )

    @router.post("/api/vision/models/{model_id}/probe")
    async def probe_vision_model(model_id: str) -> dict[str, Any]:
        model = store.get_vision_model(model_id)
        if model is None:
            raise HTTPException(status_code=404, detail="视觉模型不存在。")
        result = probe_model(model["repoId"], model.get("path"))
        return store.upsert_vision_model(
            {
                **model,
                "capabilities": result["capabilities"],
                "supported": result["supported"],
                "probeStatus": result["status"],
                "probeMessage": result["message"],
            }
        )

    @router.get("/api/vision/models/{model_id}/compatibility")
    async def vision_model_compatibility(
        model_id: str, datasetId: str | None = None
    ) -> dict[str, Any]:
        model = store.get_vision_model(model_id)
        if model is None:
            raise HTTPException(status_code=404, detail="视觉模型不存在。")
        reasons: list[str] = []
        if not model.get("supported"):
            reasons.append(model.get("probeMessage") or "模型未通过视觉探针。")
        if datasetId:
            dataset = store.get_dataset(datasetId)
            if dataset is None:
                reasons.append("数据集不存在。")
            elif dataset.get("structure") != "IMAGE_TEXT":
                reasons.append("视觉任务只能绑定 IMAGE_TEXT 数据集。")
            elif dataset.get("state") != "published":
                reasons.append("视觉任务只能绑定已发布数据集。")
        return {"modelId": model_id, "compatible": not reasons, "reasons": reasons}

    @router.get("/api/vision/inference-models")
    async def list_vision_inference_models(provider: str = "local") -> list[dict[str, Any]]:
        return _vision_inference_model_options(provider)

    @router.get("/api/vision/deployments")
    async def list_vision_deployments() -> list[dict[str, Any]]:
        return [
            service
            for service in inference_runtime.store.list(include_archived=True)
            if service.get("config", {}).get("workloadType") == "vision_language"
        ]

    def inspect_evaluation_dataset(
        dataset_path: str,
        metadata_name: str = "metadata.jsonl",
        max_samples: int | None = None,
    ) -> dict[str, Any]:
        root = _safe_dataset_path(dataset_path, project_root, dataset_root)
        metadata = root / metadata_name
        if not metadata.is_file():
            raise ValueError("评测集 metadata 文件不存在。")
        records = load_jsonl(metadata)
        if max_samples:
            records = records[:max_samples]
        missing = 0
        for record in records:
            image_name = record.get("file_name") or record.get("image")
            if not isinstance(image_name, str):
                missing += 1
                continue
            image_path = (root / image_name).resolve()
            if not image_path.is_relative_to(root) or not image_path.is_file():
                missing += 1
        return {
            "valid": bool(records) and missing == 0,
            "datasetPath": str(root),
            "metadata": str(metadata),
            "sampleCount": len(records),
            "missingImages": missing,
        }

    def evaluation_dataset_display_path(root: Path) -> str:
        resolved = root.resolve()
        project = project_root.resolve()
        if resolved.is_relative_to(project):
            return resolved.relative_to(project).as_posix() or "."
        return str(resolved)

    def evaluation_dataset_name(root: Path) -> tuple[str, str]:
        manifest = root / "manifest.json"
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                name = str(data.get("name") or data.get("title") or "").strip()
                description = str(data.get("description") or "").strip()
                if name:
                    return name, description
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                pass
        readme = root / "README.md"
        if readme.is_file():
            try:
                lines = readme.read_text(encoding="utf-8").splitlines()
                heading = next((line.lstrip("# ").strip() for line in lines if line.startswith("#")), "")
                description = next((line.strip() for line in lines if line.strip() and not line.startswith("#")), "")
                return heading or root.name, description
            except (OSError, UnicodeDecodeError):
                pass
        return root.name, ""

    def evaluation_dataset_candidates() -> list[Path]:
        roots = [project_root.resolve(), dataset_root.resolve()]
        candidates: set[Path] = set()
        for root in roots:
            if root.is_dir():
                candidates.add(root)
                try:
                    candidates.update(child.resolve() for child in root.iterdir() if child.is_dir())
                except OSError:
                    continue
        project_data = project_root.resolve() / "data"
        if project_data.is_dir():
            try:
                candidates.update(child.resolve() for child in project_data.iterdir() if child.is_dir())
            except OSError:
                pass
        return sorted(candidates, key=lambda path: evaluation_dataset_display_path(path).casefold())

    @router.get("/api/vision/evaluation-datasets")
    async def list_evaluation_datasets() -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for root in evaluation_dataset_candidates():
            metadata_files = sorted(root.glob("metadata*.jsonl"))
            if not metadata_files:
                continue
            metadata = next((item for item in metadata_files if item.name == "metadata.jsonl"), metadata_files[0])
            try:
                inspection = inspect_evaluation_dataset(str(root), metadata.name)
            except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
                continue
            name, description = evaluation_dataset_name(root)
            result.append({
                "id": evaluation_dataset_display_path(root),
                "name": name,
                "description": description,
                "datasetPath": evaluation_dataset_display_path(root),
                "metadataName": metadata.name,
                "sampleCount": inspection["sampleCount"],
                "missingImages": inspection["missingImages"],
                "valid": inspection["valid"],
            })
        return result

    @router.post(
        "/api/vision/deployments", status_code=status.HTTP_201_CREATED
    )
    async def create_vision_deployment(
        payload: VisionDeploymentCreate,
    ) -> dict[str, Any]:
        task: dict[str, Any] | None = None
        adapter_ref = ""
        base_model_id = payload.modelId
        if payload.modelType == "trained":
            if not payload.taskId or not payload.adapterVariant:
                raise HTTPException(
                    status_code=422,
                    detail="训练完成模型必须提供 taskId 和 adapterVariant。",
                )
            task = read_vision_task(payload.taskId)
            if task is None:
                raise HTTPException(status_code=404, detail="视觉训练任务不存在。")
            if task.get("state") not in {"completed", "stopped"}:
                raise HTTPException(status_code=409, detail="视觉训练任务尚未完成，暂不能部署。")
            base_model_id = str(task.get("modelId") or "")
            if base_model_id != payload.modelId:
                raise HTTPException(status_code=409, detail="训练模型与所选基座模型不匹配。")
            artifact = _adapter_for_vision_task(task, payload.adapterVariant)
            if artifact is None:
                raise HTTPException(status_code=409, detail="所选 Adapter 产物不存在或目录不可访问。")
            adapter_ref = str(Path(str(artifact["path"])).expanduser().resolve())
        model = vision_model_for_training(base_model_id)
        if model is None:
            raise HTTPException(status_code=404, detail="视觉模型不存在。")
        model_ref: str
        resolved_youyun_resource: dict[str, Any] | None = None
        if payload.provider == "local":
            if not model.get("supported") or model.get("probeStatus") != "passed":
                raise HTTPException(
                    status_code=409,
                    detail=model.get("probeMessage") or "视觉模型尚未通过本地探针。",
                )
            model_path = Path(str(model.get("path") or "")).expanduser()
            if not model_path.is_dir():
                raise HTTPException(
                    status_code=409,
                    detail="视觉部署要求模型已下载到可访问的本地目录。",
                )
            model_ref = str(model_path.resolve())
        else:
            if youyun_config_check is not None:
                missing_youyun = youyun_config_check()
                if missing_youyun:
                    raise HTTPException(
                        status_code=409,
                        detail=f"优云推理配置缺少：{', '.join(missing_youyun)}。",
                    )
            repo_id = str(model.get("repoId") or "").strip()
            if not is_youyun_model_available(model):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "所选视觉基座模型不在优云公共模型库中；请选择带 /model/... 共享挂载路径的模型。"
                    ),
                )
            if not payload.youyunResource:
                raise HTTPException(status_code=409, detail="优云推理服务必须提供 youyunResource 资源配置。")
            if youyun_resource_resolver is None:
                raise HTTPException(status_code=409, detail="当前服务未配置优云资源解析器。")
            try:
                resolved_youyun_resource = await youyun_resource_resolver(payload.youyunResource)
            except HTTPException:
                raise
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            # The public catalog exposes an already-mounted /model path. Keep
            # it as the deployment reference so the remote provider does not
            # fall back to an unnecessary repository lookup or download.
            model_ref = str(model.get("remotePath") or repo_id)
            if not model_ref:
                raise HTTPException(status_code=409, detail="视觉模型缺少有效 Repo ID。")
        service_id = "vsvc-" + uuid.uuid4().hex[:12]
        store_values = payload.model_dump(
            exclude={"provider", "youyunResource", "modelType", "taskId", "adapterVariant"}
        )

        store_payload = _VisionInferenceStorePayload(
            **store_values,
            taskId=payload.taskId if payload.modelType == "trained" else f"vision-model:{payload.modelId}",
            modelType=payload.modelType,
            adapterVariant=payload.adapterVariant,
            provider=payload.provider,
            youyunResource=resolved_youyun_resource,
        )
        service = inference_runtime.store.create(
            service_id=service_id,
            payload=store_payload,
            model=model_ref,
            adapter=adapter_ref,
            endpoint="/v1/chat/completions",
        )
        return {
            **service,
            "deploymentStatus": "configured",
            "message": service.get("message") or "视觉推理服务配置已创建；启动后通过健康检查才算部署成功。",
        }

    @router.patch("/api/vision/models/{model_id}")
    async def update_vision_model(
        model_id: str,
        payload: VisionModelPatch,
    ) -> dict[str, Any]:
        model = store.update_vision_model(model_id, archived=payload.archived)
        if model is None:
            raise HTTPException(status_code=404, detail="视觉模型不存在。")
        return model

    @router.post("/api/vision/tasks/preflight")
    async def vision_task_preflight(payload: VisionTaskPreflight) -> dict[str, Any]:
        if payload.provider == "youyun":
            await refresh_live_youyun_catalog()
        dataset = store.get_dataset(payload.datasetId)
        model = vision_model_for_training(payload.modelId)
        reasons: list[str] = []
        if dataset is None:
            reasons.append("数据集不存在。")
        elif dataset.get("state") != "published":
            reasons.append("视觉任务只能绑定已发布数据集。")
        elif dataset.get("structure") != "IMAGE_TEXT":
            reasons.append("视觉任务只能绑定 IMAGE_TEXT 数据集。")
        if dataset is not None and dataset.get("structure") == "IMAGE_TEXT" and dataset.get("state") == "published":
            train_path = Path(str((dataset.get("paths") or {}).get("trainPath") or ""))
            try:
                sample_count = await asyncio.to_thread(
                    validate_vision_training_dataset,
                    train_path=train_path,
                    task_type=payload.taskType,
                )
                if sample_count <= 0:
                    reasons.append("视觉训练数据集没有有效样本。")
            except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
                reasons.append(f"视觉 {payload.taskType} 数据格式校验失败：{exc}")
        if model is None:
            reasons.append("视觉基座模型未登记。")
        else:
            if payload.provider == "youyun" and not is_youyun_model_available(model):
                reasons.append(
                    "所选视觉基座模型不在优云公共模型库中；请选择带共享挂载路径的模型。"
                )
            if (
                payload.provider == "youyun"
                and os.environ.get("XUNTUI_REQUIRE_VISION_REMOTE_PROBE", "1").strip() == "1"
                and model.get("probeStatus") != "passed"
            ):
                reasons.append("优云视觉训练要求先通过远端兼容性探针。")
            local_probe_passed = bool(
                model.get("supported") and model.get("probeStatus") == "passed"
            )
            needed = {f"{payload.tuningMode}_training", "vision_language"}
            missing = sorted(needed.difference(model.get("capabilities", [])))
            if missing:
                reasons.append(f"模型缺少能力标签：{', '.join(missing)}。")
            elif not can_use_vision_model_for_training(
                model, payload.tuningMode, payload.provider
            ):
                if payload.provider == "local" and not local_probe_passed:
                    reasons.append(
                        "本地视觉训练要求模型目录存在并通过视觉探针；当前模型仍是仓库候选。"
                    )
                else:
                    reasons.append(model.get("probeMessage") or "视觉模型未通过训练能力校验。")
            try:
                resolve_vision_template(
                    str(model.get("repoId") or ""), str(model.get("path") or "")
                )
            except ValueError as exc:
                reasons.append(str(exc))
        gpu = gpu_probe() if payload.provider == "local" and gpu_probe else None
        if payload.provider == "local" and not (gpu and gpu.get("available")):
            reasons.append("当前节点未检测到可用 GPU；视觉训练本地执行暂不可用。")
        if payload.provider == "youyun" and youyun_config_check is not None:
            missing_youyun = youyun_config_check()
            if missing_youyun:
                reasons.append(f"优云训练配置缺少：{', '.join(missing_youyun)}。")
        resolved_youyun_resource: dict[str, Any] | None = None
        if payload.provider == "youyun" and payload.youyunResource is not None and youyun_image_validator is not None:
            image_error = await youyun_image_validator(str(payload.youyunResource.get("imageId") or ""))
            if image_error:
                reasons.append(image_error)
        if payload.provider == "youyun" and payload.youyunResource is not None and not reasons:
            if youyun_resource_resolver is None:
                reasons.append("当前服务未配置优云资源解析器。")
            else:
                try:
                    resolved_youyun_resource = await youyun_resource_resolver(payload.youyunResource)
                except HTTPException as exc:
                    reasons.append(str(exc.detail))
                except (TypeError, ValueError) as exc:
                    reasons.append(str(exc))
        effective_batch = payload.batchSize * payload.gradientAccumulation
        return {
            "ready": not reasons,
            "datasetId": payload.datasetId,
            "modelId": payload.modelId,
            "modelRepoId": str(model.get("repoId") or "") if model else None,
            "remoteModelPath": str(model.get("remotePath") or "") if model else None,
            "provider": payload.provider,
            "taskType": payload.taskType,
            "dpoBeta": payload.dpoBeta,
            "tuningMode": payload.tuningMode,
            "effectiveBatchSize": effective_batch,
            "imageMaxPixels": payload.imageMaxPixels,
            "validationSplit": payload.validationSplit,
            "gpu": gpu,
            "youyunResource": resolved_youyun_resource,
            "reasons": reasons,
            "executionStatus": (
                "blocked"
                if reasons
                else "ready_for_queue"
                if training_runtime is not None
                else "awaiting_executor"
            ),
            "message": (
                (
                    (
                        "视觉任务已通过数据、模型和资源检查，可以确认并进入视觉训练队列。"
                    )
                    if training_runtime is not None
                    else (
                        "视觉任务已通过配置检查，但当前服务未配置视觉训练执行器。"
                    )
                )
                if not reasons
                else "视觉任务未通过前置检查，不会进入视觉训练队列。"
            ),
        }

    @router.post("/api/vision/tasks/materialize-preview")
    async def materialize_vision_task_preview(
        payload: VisionTaskCreate,
    ) -> dict[str, Any]:
        preflight = await vision_task_preflight(
            VisionTaskPreflight(
                datasetId=payload.datasetId,
                modelId=payload.modelId,
                taskType=payload.taskType,
                dpoBeta=payload.dpoBeta,
                tuningMode=payload.tuningMode,
                provider=payload.provider,
                imageMaxPixels=payload.imageMaxPixels,
                batchSize=payload.batchSize,
                gradientAccumulation=payload.gradientAccumulation,
                validationSplit=payload.validationSplit,
                youyunResource=payload.youyunResource,
            )
        )
        if not preflight["ready"]:
            raise HTTPException(status_code=409, detail=preflight)
        return await materialize_training_payload(payload, preflight)

    @router.post("/api/vision/tasks", status_code=status.HTTP_202_ACCEPTED)
    async def create_vision_task(payload: VisionTaskCreate) -> dict[str, Any]:
        preflight = await vision_task_preflight(
            VisionTaskPreflight(
                datasetId=payload.datasetId,
                modelId=payload.modelId,
                taskType=payload.taskType,
                dpoBeta=payload.dpoBeta,
                tuningMode=payload.tuningMode,
                provider=payload.provider,
                imageMaxPixels=payload.imageMaxPixels,
                batchSize=payload.batchSize,
                gradientAccumulation=payload.gradientAccumulation,
                validationSplit=payload.validationSplit,
                youyunResource=payload.youyunResource,
            )
        )
        if not preflight["ready"]:
            raise HTTPException(status_code=409, detail=preflight)
        preview = await materialize_training_payload(payload, preflight)
        dataset = store.get_dataset(payload.datasetId)
        model = vision_model_for_training(payload.modelId)
        assert dataset is not None and model is not None
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        heartbeat_epoch = time.time()
        remote_budget = max(
            300.0,
            float(os.environ.get("XUNTUI_VISION_REMOTE_TASK_TIMEOUT_SECONDS", "3600")),
        )
        executor_enabled = training_runtime is not None
        parameters = payload.model_dump()
        task = {
            "id": f"vt-{uuid.uuid4().hex[:12]}",
            "name": payload.name.strip(),
            "taskType": payload.taskType,
            "state": "queued" if executor_enabled else "awaiting_executor",
            "status": "queued" if executor_enabled else "configured",
            "progress": 0,
            "currentStep": "等待视觉训练资源" if executor_enabled else "配置已生成，等待视觉训练执行器接入",
            "modelId": payload.modelId,
            "modelRepoId": str(model.get("repoId") or "") if model else None,
            "remoteModelPath": str(model.get("remotePath") or "") if model else None,
            "model": model.get("name") or model.get("repoId"),
            "datasetId": payload.datasetId,
            "dataset": dataset.get("name") or payload.datasetId,
            "method": f"视觉 {payload.taskType} / {payload.tuningMode.upper()}",
            "tuningMode": payload.tuningMode,
            "provider": payload.provider,
            "youyunResource": preflight.get("youyunResource"),
            "outputName": payload.outputName,
            "owner": "平台管理员",
            "createdAt": now,
            "updatedAt": now,
            "previewId": preview["previewId"],
            "configPath": preview["configPath"],
            "manifestPath": preview["manifestPath"],
            "manifest": preview["manifest"],
            "yaml": preview["yaml"],
            "parameters": parameters,
            "preflight": preflight,
            "previewRoot": preview["root"],
            "logs": [
                "视觉训练任务已确认并提交。"
                if executor_enabled
                else "视觉训练配置已生成，当前未配置视觉执行器。"
            ],
            "lossHistory": [],
            "artifacts": [],
            "cancelRequested": False,
            "confirmedAt": now,
            "executionStatus": "queued" if executor_enabled else "awaiting_executor",
            "lastHeartbeatAtEpoch": heartbeat_epoch if payload.provider == "youyun" else None,
            "expiresAtEpoch": heartbeat_epoch + remote_budget if payload.provider == "youyun" else None,
            "remoteTimeoutSeconds": int(remote_budget) if payload.provider == "youyun" else None,
            "message": (
                f"视觉 {payload.taskType} 训练任务已提交，正在进入视觉训练队列。"
                if executor_enabled
                else f"视觉 {payload.taskType} 训练配置已生成；当前版本不会将任务送入文本训练队列，需接入视觉训练执行器后运行。"
            ),
        }
        save_vision_task(task)
        if executor_enabled:
            try:
                training_runtime.enqueue(task["id"])
            except Exception as exc:  # noqa: BLE001 - persist submission failure for the UI
                task.update(
                    state="failed",
                    status="failed",
                    currentStep="视觉训练任务提交失败",
                    error=str(exc),
                    executionStatus="failed",
                )
                save_vision_task(task)
                raise HTTPException(status_code=503, detail=f"视觉训练队列不可用：{exc}") from exc
        return task

    @router.get("/api/vision/tasks")
    async def list_vision_tasks() -> list[dict[str, Any]]:
        return load_vision_tasks()

    @router.get("/api/vision/tasks/{task_id}")
    async def get_vision_task(task_id: str) -> dict[str, Any]:
        task = read_vision_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="视觉微调任务不存在。")
        return normalize_legacy_vision_task(task)

    @router.post("/api/vision/tasks/{task_id}/stop")
    async def stop_vision_task(task_id: str) -> dict[str, Any]:
        if training_runtime is None:
            raise HTTPException(status_code=409, detail="视觉训练执行器尚未配置。")
        task = await training_runtime.request_stop(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="视觉微调任务不存在。")
        return task

    @router.post("/api/vision/tasks/{task_id}/retry")
    async def retry_vision_task(task_id: str) -> dict[str, Any]:
        if training_runtime is None:
            raise HTTPException(status_code=409, detail="视觉训练执行器尚未配置。")
        try:
            task = await training_runtime.retry(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if task is None:
            raise HTTPException(status_code=404, detail="视觉微调任务不存在。")
        return task

    @router.post("/api/vision/tasks/{task_id}/youyun/release")
    async def release_vision_youyun_instance(task_id: str) -> dict[str, Any]:
        if training_runtime is None:
            raise HTTPException(status_code=409, detail="视觉训练执行器尚未配置。")
        task = read_vision_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="视觉微调任务不存在。")
        if str(task.get("provider") or "") != "youyun":
            raise HTTPException(status_code=409, detail="任务不是优云视觉任务。")
        result = await training_runtime.force_cleanup_remote_task(task_id, reason="手动释放")
        if result is None:
            raise HTTPException(status_code=404, detail="视觉微调任务不存在。")
        return result

    @router.post("/api/vision/tasks/{task_id}/confirm")
    async def confirm_vision_task(task_id: str) -> dict[str, Any]:
        if training_runtime is None:
            raise HTTPException(status_code=409, detail="视觉训练执行器尚未配置。")
        task = read_vision_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="视觉微调任务不存在。")
        if task.get("state") not in {"awaiting_confirmation", "awaiting_executor", "configured"}:
            raise HTTPException(status_code=409, detail="当前视觉任务不在待确认状态。")
        try:
            parameters = task.get("parameters") or {}
            preflight_payload = VisionTaskPreflight.model_validate(
                {
                    key: parameters[key]
                    for key in VisionTaskPreflight.model_fields
                    if key in parameters
                }
            )
        except ValidationError as exc:
            raise HTTPException(status_code=409, detail="视觉任务配置不完整，请克隆后重新填写。") from exc
        preflight = await vision_task_preflight(preflight_payload)
        if not preflight["ready"]:
            raise HTTPException(status_code=409, detail=preflight)
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        task.update(
            state="queued",
            status="queued",
            progress=0,
            currentStep="等待视觉训练资源",
            confirmedAt=now,
            executionStatus="queued",
            cancelRequested=False,
            error=None,
            preflight=preflight,
            youyunResource=preflight.get("youyunResource"),
        )
        if str(task.get("provider") or "") == "youyun":
            heartbeat_epoch = time.time()
            remote_budget = max(
                300.0,
                float(os.environ.get("XUNTUI_VISION_REMOTE_TASK_TIMEOUT_SECONDS", "3600")),
            )
            task["lastHeartbeatAtEpoch"] = heartbeat_epoch
            task["expiresAtEpoch"] = heartbeat_epoch + remote_budget
            task["remoteTimeoutSeconds"] = int(remote_budget)
        save_vision_task(task)
        try:
            training_runtime.enqueue(task_id)
        except Exception as exc:  # noqa: BLE001
            task.update(state="failed", status="failed", currentStep="视觉训练任务提交失败", error=str(exc))
            save_vision_task(task)
            raise HTTPException(status_code=503, detail=f"视觉训练队列不可用：{exc}") from exc
        return task

    @router.post("/api/vision/tasks/{task_id}/clone", status_code=status.HTTP_201_CREATED)
    async def clone_vision_task(task_id: str) -> dict[str, Any]:
        source = read_vision_task(task_id)
        if source is None:
            raise HTTPException(status_code=404, detail="视觉微调任务不存在。")
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        clone = {
            **source,
            "id": f"vt-{uuid.uuid4().hex[:12]}",
            "name": f"{source.get('name', '视觉微调任务')} 副本",
            "state": "awaiting_confirmation",
            "status": "draft",
            "progress": 0,
            "currentStep": "等待确认后提交训练",
            "createdAt": now,
            "updatedAt": now,
            "clonedFrom": source["id"],
            "confirmedAt": None,
            "executionStatus": "awaiting_confirmation",
            "cancelRequested": False,
            "error": None,
            "logs": ["任务副本已创建，等待确认提交。"],
            "lossHistory": [],
            "artifacts": [],
        }
        save_vision_task(clone)
        return clone

    @router.delete("/api/vision/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_vision_task(task_id: str) -> None:
        path = vision_task_path(task_id)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="视觉微调任务不存在。")
        task = read_vision_task(task_id)
        if task is not None and task.get("state") in {
            "queued", "preparing", "running", "stopping", "collecting"
        }:
            raise HTTPException(status_code=409, detail="运行中的视觉任务需要先停止。")
        if task is not None and (
            str(task.get("cleanupState") or "") == "failed"
            or str(task.get("resourceState") or "") == "cleanup_failed"
        ):
            raise HTTPException(
                status_code=409,
                detail="优云实例清理失败，请先完成实例清理后再删除任务。",
            )
        try:
            path.unlink()
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"删除视觉任务失败：{exc}") from exc

    @router.post("/api/vision/chat")
    async def vision_chat(payload: VisionChatRequest) -> dict[str, Any]:
        check_resource_access(payload.serviceId)
        try:
            image = _decode_image(payload.imageBase64)
            image_hash = validate_image_bytes(image, payload.imageFilename)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            service = inference_runtime._running_service(payload.serviceId)  # noqa: SLF001
            if service.get("config", {}).get("workloadType") != "vision_language":
                raise RuntimeError("该推理服务不是视觉理解服务，不能进行图像推理。")
            body = {
                "model": service["id"],
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": image_data_url(image, payload.imageFilename)}},
                            {"type": "text", "text": payload.question},
                        ],
                    }
                ],
                "max_tokens": payload.maxTokens,
                "temperature": payload.temperature,
                "top_p": payload.topP,
                "stream": False,
            }
            response = json.loads(await asyncio.to_thread(inference_runtime._post_json, body))  # noqa: SLF001
            return {
                "requestId": f"vreq-{uuid.uuid4().hex[:12]}",
                "serviceId": payload.serviceId,
                "answer": _message_text(response),
                "imageSha256": image_hash,
                "usage": response.get("usage", {}),
            }
        except (KeyError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"视觉推理失败：{exc}") from exc

    @router.post("/api/inference/services/{service_id}/vision-chat")
    async def service_vision_chat(
        service_id: str, payload: VisionChatRequest
    ) -> dict[str, Any]:
        return await vision_chat(payload.model_copy(update={"serviceId": service_id}))

    @router.post("/api/public/vision/services/{service_id}/chat")
    async def public_vision_chat(
        service_id: str,
        payload: PublicVisionChatRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="缺少有效的 API Key。")
        service = inference_runtime.store.authenticate_api_key(service_id, authorization[7:].strip())
        if service is None or service.get("state") == "archived":
            raise HTTPException(status_code=401, detail="API Key 无效或已失效。")
        if (service.get("config") or {}).get("workloadType") != "vision_language":
            raise HTTPException(status_code=409, detail="该 API Key 不适用于视觉理解服务。")
        return await vision_chat(VisionChatRequest(serviceId=service_id, **payload.model_dump()))

    @router.post("/api/vision/evaluations/validate")
    async def validate_evaluation_dataset(payload: VisionEvaluationValidate) -> dict[str, Any]:
        try:
            return inspect_evaluation_dataset(payload.datasetPath, payload.metadataName, payload.maxSamples)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    async def run_evaluation(job: dict[str, Any], payload: VisionEvaluationCreate) -> None:
        try:
            root = _safe_dataset_path(payload.datasetPath, project_root, dataset_root)
            records = load_jsonl(root / payload.metadataName)
            if payload.maxSamples:
                records = records[: payload.maxSamples]
            job["status"] = "running"
            save_job(job)
            rows: list[dict[str, Any]] = []
            for index, record in enumerate(records, 1):
                image_name = record.get("file_name") or record.get("image")
                question = str(record.get("question") or "请描述这张图片。")
                reference = str(record.get("answer") or record.get("text") or "")
                if not isinstance(image_name, str) or not reference:
                    rows.append({"index": index, "error": "样本缺少图片或答案。"})
                    continue
                image_path = (root / image_name).resolve()
                if not image_path.is_relative_to(root) or not image_path.is_file():
                    rows.append({"index": index, "error": "图片不存在或路径越界。"})
                    continue
                image = image_path.read_bytes()
                validate_image_bytes(image, image_path.name)
                body = {
                    "model": payload.serviceId,
                    "messages": [{"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": image_data_url(image, image_path.name)}},
                        {"type": "text", "text": question},
                    ]}],
                    "max_tokens": payload.maxTokens,
                    "temperature": payload.temperature,
                    "top_p": payload.topP,
                    "stream": False,
                }
                started = time.perf_counter()
                try:
                    inference_runtime._running_service(payload.serviceId)  # noqa: SLF001
                    raw = await asyncio.to_thread(inference_runtime._post_json, body)  # noqa: SLF001
                    prediction = _message_text(json.loads(raw))
                    row = {"index": index, "prediction": prediction, "reference": reference,
                           "latencyMs": round((time.perf_counter() - started) * 1000, 2),
                           "metrics": answer_metrics(prediction, reference)}
                    rows.append(row)
                except Exception as exc:
                    rows.append({"index": index, "error": str(exc)})
            job["status"] = "completed"
            job["summary"] = summarize_metrics(rows)
            result_path = evaluation_root / f"{job['id']}.results.jsonl"
            result_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
            job["resultPath"] = str(result_path)
            job["results"] = rows
            save_job(job)
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = str(exc)
            save_job(job)

    @router.post("/api/vision/evaluations", status_code=status.HTTP_202_ACCEPTED)
    async def create_evaluation(payload: VisionEvaluationCreate) -> dict[str, Any]:
        service = inference_runtime.store.get(payload.serviceId)
        if service is None:
            raise HTTPException(status_code=404, detail="推理服务不存在。")
        if service["state"] != "running" or inference_runtime.active_service_id != payload.serviceId:
            raise HTTPException(status_code=409, detail="评测需要先启动视觉推理服务。")
        job = {
            "id": f"ve-{uuid.uuid4().hex[:12]}",
            "status": "queued",
            "serviceId": payload.serviceId,
            "datasetPath": payload.datasetPath,
            "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        jobs[job["id"]] = job
        save_job(job)
        task = asyncio.create_task(run_evaluation(job, payload))
        job_tasks.add(task)
        task.add_done_callback(job_tasks.discard)
        return job

    @router.get("/api/vision/evaluations")
    async def list_evaluations() -> list[dict[str, Any]]:
        return load_jobs()

    @router.get("/api/vision/evaluations/{evaluation_id}")
    async def get_evaluation(evaluation_id: str) -> dict[str, Any]:
        job = jobs.get(evaluation_id)
        if job is None:
            path = evaluation_root / f"{evaluation_id}.json"
            if not path.is_file():
                raise HTTPException(status_code=404, detail="评测任务不存在。")
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise HTTPException(status_code=500, detail=f"评测结果不可读取：{exc}") from exc
        return job

    return router
