from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from fastapi import APIRouter, Header, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.personal_youyun import check_resource_access
from backend.image_generation_evaluation import list_image_generation_evaluation_datasets, validate_image_generation_evaluation_dataset
from backend.providers.youyun.models import (
    rank_community_image_candidates,
    youyun_zone_capabilities,
    youyun_zone_default_mode,
    youyun_zone_modes,
    youyun_zone_supports_mode,
    youyun_machine_inventory_metadata,
)
from backend.providers.youyun.resource_health import youyun_resource_health


def _verified_repos() -> set[str]:
    raw = os.environ.get("XUNTUI_IMAGE_GENERATION_VERIFIED_REPOS", "stabilityai/stable-diffusion-xl-base-1.0")
    return {item.strip().casefold() for item in raw.split(",") if item.strip()}


def _qlora_compatible() -> bool:
    return os.environ.get("XUNTUI_IMAGE_QLORA_COMPATIBLE", "").strip().lower() in {"1", "true", "yes", "on"}


def _model_type(repo_id: str) -> str:
    value = repo_id.casefold()
    if "flux" in value:
        return "FLUX"
    if "sdxl" in value or "stable-diffusion-xl" in value:
        return "SDXL"
    if "stable-diffusion" in value or "sd-" in value:
        return "Stable Diffusion"
    return "Diffusion"


def _diffusers_image(item: Mapping[str, Any]) -> bool:
    text = " ".join(str(item.get(key) or "") for key in ("name", "description", "image_id"))
    text = f"{text} {' '.join(str(tag) for tag in item.get('tags') or [])}".casefold()
    return any(marker in text for marker in ("diffusers", "stable diffusion", "sdxl", "flux"))


_TORCH_IMAGE_VERSION = re.compile(r"torch(?P<major>\d)(?P<minor>\d{1,2})(?P<patch>\d)", re.IGNORECASE)
_RECOMMENDED_TORCH_IMAGE_NAME = "cuda132_torch2130_py312"


def _fallback_image_key(item: Mapping[str, Any]) -> tuple[int, tuple[int, int, int], int]:
    """Prefer a CUDA/PyTorch application image for remote Diffusers install fallback."""
    text = " ".join(str(item.get(key) or "") for key in ("name", "description", "image_id")).casefold()
    if any(marker in text for marker in ("comfy", "vllm", "ollama", "sglang")):
        return (-1, (0, 0, 0), 0)
    match = _TORCH_IMAGE_VERSION.search(text)
    version = (
        tuple(int(match.group(key)) for key in ("major", "minor", "patch"))
        if match
        else (0, 0, 0)
    )
    return (2 if "torch" in text or "pytorch" in text else 1 if "cuda" in text else 0, version, 1 if "cuda" in text else 0)


def _image_status_ok(item: Mapping[str, Any]) -> bool:
    return str(item.get("status") or "").casefold() in {"available", "normal"}


def _is_app_image(item: Mapping[str, Any]) -> bool:
    return str(item.get("image_type") or "").casefold() in {"app", "application"}


def _is_community_image(item: Mapping[str, Any]) -> bool:
    # Youyun has moved most published images to the community catalog; those
    # rows are valid install-fallback bases but never count as prebuilt
    # Diffusers images (community picks stay behind the probe gate).
    return str(item.get("image_type") or item.get("type") or "").casefold() == "community"


def install_fallback_pool(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """App images first, then available community images, as remote-install bases."""
    apps = [item for item in images if _is_app_image(item) and _image_status_ok(item)]
    community = [item for item in images if _is_community_image(item) and _image_status_ok(item)]
    return apps + community


def select_image_generation_images(
    images: list[dict[str, Any]],
    *,
    configured_image_id: str = "",
    prefer_configured_image: bool = False,
    allow_install_fallback: bool | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    available = [item for item in images if _is_app_image(item) and _image_status_ok(item)]
    diffusers = [item for item in available if _diffusers_image(item)]
    install_fallback = (
        allow_install_fallback
        if allow_install_fallback is not None
        else os.environ.get("XUNTUI_REMOTE_DIFFUSERS_INSTALL", "0").strip().lower() in {"1", "true", "yes", "on"}
    )
    if diffusers:
        selected = next((item for item in diffusers if str(item.get("image_id") or "") == configured_image_id), diffusers[0])
        return diffusers, selected
    if not install_fallback:
        raise ValueError("未发现可用的 Diffusers 优云镜像；请配置 XUNTUI_IMAGE_GENERATION_INFERENCE_IMAGE，或显式开启远端安装兜底。")
    fallback_images = install_fallback_pool(images)
    if not fallback_images:
        raise ValueError("未发现可用的优云应用镜像，无法作为 Diffusers 安装兜底。")
    candidates = [item for item in fallback_images if _fallback_image_key(item)[0] > 0]
    fallback_images = candidates or fallback_images
    recommended = next(
        (
            item
            for item in fallback_images
            if str(item.get("name") or "").strip().casefold() == _RECOMMENDED_TORCH_IMAGE_NAME
        ),
        None,
    )
    configured = next(
        (item for item in fallback_images if str(item.get("image_id") or "") == configured_image_id),
        None,
    )
    selected = (configured if prefer_configured_image else None) or recommended or configured or max(
        fallback_images,
        key=_fallback_image_key,
    )
    return fallback_images, {**selected, "_requiresDiffusersInstall": True}


class ImageGenerationServiceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    modelId: str = Field(min_length=1, max_length=200)
    taskId: str | None = Field(default=None, max_length=120)
    adapterVariant: str | None = None
    trainingMode: str = "lora"
    precision: str = "auto"
    enableCpuOffload: bool = False
    enableAttentionSlicing: bool = False
    enableVaeTiling: bool = False
    enableSafetyChecker: bool = False
    loadTimeout: int = Field(default=180, ge=30, le=3600)
    maxConcurrentRequests: int = Field(default=1, ge=1, le=16)
    idleTimeoutSeconds: int = Field(default=900, ge=0, le=86_400)
    requestTimeoutSeconds: int = Field(default=600, ge=5, le=3600)
    negativePrompt: str = Field(default="", max_length=2000)
    width: int = Field(default=1024, ge=256, le=2048)
    height: int = Field(default=1024, ge=256, le=2048)
    steps: int = Field(default=30, ge=1, le=100)
    cfg: float = Field(default=7.0, ge=0, le=30)
    batchSize: int = Field(default=1, ge=1, le=4)
    seed: int = Field(default=-1, ge=-1, le=2_147_483_647)
    sampler: str = Field(default="euler", min_length=1, max_length=40)
    resource: dict[str, Any]

    @field_validator("trainingMode")
    @classmethod
    def valid_mode(cls, value: str) -> str:
        if value not in {"lora", "qlora"}:
            raise ValueError("训练方式只能是 LoRA 或 QLoRA。")
        return value

    @field_validator("precision")
    @classmethod
    def valid_precision(cls, value: str) -> str:
        if value not in {"auto", "fp16", "bf16"}:
            raise ValueError("精度只能是 Auto、FP16 或 BF16。")
        return value

    @field_validator("description", "negativePrompt")
    @classmethod
    def clean_optional_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("adapterVariant")
    @classmethod
    def valid_variant(cls, value: str | None) -> str | None:
        if value is not None and value not in {"best", "final"}:
            raise ValueError("Adapter 版本只能是 Best 或 Final。")
        return value

    @field_validator("sampler")
    @classmethod
    def valid_sampler(cls, value: str) -> str:
        if value not in {"euler", "dpmpp_2m"}:
            raise ValueError("采样器只能是 Euler 或 DPM++ 2M。")
        return value


class ImageGenerationTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    serviceId: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=4000)
    negativePrompt: str = Field(default="", max_length=2000)
    width: int | None = Field(default=None, ge=256, le=2048)
    height: int | None = Field(default=None, ge=256, le=2048)
    steps: int | None = Field(default=None, ge=1, le=100)
    cfg: float | None = Field(default=None, ge=0, le=30)
    batchSize: int | None = Field(default=None, ge=1, le=4)
    seed: int | None = Field(default=None, ge=-1, le=2_147_483_647)
    sampler: str | None = Field(default=None, min_length=1, max_length=40)

    @field_validator("prompt")
    @classmethod
    def clean_prompt(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("Prompt 不能为空。")
        return clean

    @field_validator("sampler")
    @classmethod
    def valid_sampler(cls, value: str | None) -> str | None:
        if value is not None and value not in {"euler", "dpmpp_2m"}:
            raise ValueError("采样器只能是 Euler 或 DPM++ 2M。")
        return value


class ImageGenerationPublicRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=4000)
    negativePrompt: str = Field(default="", max_length=2000)
    width: int | None = Field(default=None, ge=256, le=2048)
    height: int | None = Field(default=None, ge=256, le=2048)
    steps: int | None = Field(default=None, ge=1, le=100)
    cfg: float | None = Field(default=None, ge=0, le=30)
    batchSize: int | None = Field(default=None, ge=1, le=4)
    seed: int | None = Field(default=None, ge=-1, le=2_147_483_647)
    sampler: str | None = Field(default=None, min_length=1, max_length=40)

    @field_validator("prompt")
    @classmethod
    def clean_prompt(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Prompt 不能为空。")
        return value


class ImageGenerationTaskClone(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str | None = Field(default=None, min_length=1, max_length=4000)
    negativePrompt: str | None = Field(default=None, max_length=2000)
    width: int | None = Field(default=None, ge=256, le=2048)
    height: int | None = Field(default=None, ge=256, le=2048)
    steps: int | None = Field(default=None, ge=1, le=100)
    cfg: float | None = Field(default=None, ge=0, le=30)
    batchSize: int | None = Field(default=None, ge=1, le=4)
    seed: int | None = Field(default=None, ge=-1, le=2_147_483_647)
    sampler: str | None = Field(default=None, min_length=1, max_length=40)

    @field_validator("prompt")
    @classmethod
    def clean_prompt(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip()
        if not clean:
            raise ValueError("Prompt 不能为空。")
        return clean

    @field_validator("sampler")
    @classmethod
    def valid_sampler(cls, value: str | None) -> str | None:
        if value is not None and value not in {"euler", "dpmpp_2m"}:
            raise ValueError("采样器只能是 Euler 或 DPM++ 2M。")
        return value


class ImageGenerationEvaluationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    datasetId: str = Field(min_length=1, max_length=120)
    maxSamples: int | None = Field(default=None, ge=1, le=1000)
    width: int | None = Field(default=None, ge=256, le=2048)
    height: int | None = Field(default=None, ge=256, le=2048)
    steps: int | None = Field(default=None, ge=1, le=100)
    cfg: float | None = Field(default=None, ge=0, le=30)
    seed: int | None = Field(default=None, ge=-1, le=2_147_483_647)
    sampler: str | None = Field(default=None, min_length=1, max_length=40)

    @field_validator("sampler")
    @classmethod
    def valid_sampler(cls, value: str | None) -> str | None:
        if value is not None and value not in {"euler", "dpmpp_2m"}:
            raise ValueError("采样器只能是 Euler 或 DPM++ 2M。")
        return value


def build_image_generation_catalog(
    store: Any,
    *,
    provider: str = "local",
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    """Build a read-only image-generation model catalog from verified local records."""
    verified = _verified_repos()
    qlora = _qlora_compatible()
    assets = store.list_model_assets(include_archived=include_archived)
    bases = {str(item.get("id")): item for item in assets if item.get("kind") == "base"}
    catalog: list[dict[str, Any]] = []

    for base in bases.values():
        repo_id = str(base.get("repoId") or base.get("repo") or "").strip()
        repo_verified = repo_id.casefold() in verified
        local_ready = str(base.get("status")) == "available"
        ready = repo_verified and (provider == "youyun" or local_ready)
        modes = ["lora"] if ready else []
        reason = None
        if not repo_verified:
            reason = "Repo ID 未在服务端文生图白名单中。"
        elif provider != "youyun" and not local_ready:
            reason = "模型文件目录尚未就绪，请确认服务器本地目录存在。"
        elif not qlora:
            reason = "QLoRA 需要服务端镜像兼容性探针通过；当前仅支持 LoRA。"
        catalog.append(
            {
                "id": str(base.get("id")),
                "label": str(base.get("name") or repo_id),
                "group": "base",
                "repoId": repo_id,
                "modelType": _model_type(repo_id),
                "readyForYouyun": bool(ready),
                "supportedTrainingModes": modes + (["qlora"] if ready and qlora else []),
                "reason": reason,
                "archived": bool(base.get("archived")),
            }
        )

    for trained in assets:
        if trained.get("kind") != "trained":
            continue
        base = bases.get(str(trained.get("baseModelId")))
        repo_id = str(trained.get("repoId") or trained.get("repo") or "").strip()
        if not base or repo_id.casefold() != str(base.get("repoId") or base.get("repo") or "").strip().casefold():
            continue
        base_verified = repo_id.casefold() in verified and (provider == "youyun" or str(base.get("status")) == "available")
        variants = trained.get("variants") or []
        for variant in variants:
            if not isinstance(variant, Mapping):
                continue
            key = str(variant.get("key") or variant.get("variant") or "")
            if key not in {"best", "final"}:
                continue
            variant_ready = base_verified and str(variant.get("status")) == "available"
            modes = ["lora"] if variant_ready else []
            reason = None
            if not variant_ready:
                reason = "基座模型或 Adapter 产物尚未通过服务端就绪校验。"
            elif not qlora:
                reason = "QLoRA 需要服务端镜像兼容性探针通过；当前仅支持 LoRA。"
            catalog.append(
                {
                    "id": f"{trained.get('id')}:{key}",
                    "label": f"{trained.get('name') or trained.get('id')} · {key.title()} Adapter",
                    "group": "trained",
                    "repoId": repo_id,
                    "modelType": _model_type(repo_id),
                    "taskId": str(trained.get("taskId") or ""),
                    "adapterVariant": key,
                    "readyForYouyun": bool(variant_ready),
                    "supportedTrainingModes": modes + (["qlora"] if variant_ready and qlora else []),
                    "reason": reason,
                    "archived": bool(trained.get("archived")),
                }
            )
    return catalog


def _adapter_path_for_model(store: Any, model_id: str) -> str:
    if ":" not in model_id:
        return ""
    trained_id, variant_key = model_id.rsplit(":", 1)
    for asset in store.list_model_assets(include_archived=False):
        if str(asset.get("id")) != trained_id or asset.get("kind") != "trained":
            continue
        for variant in asset.get("variants") or []:
            if not isinstance(variant, Mapping):
                continue
            key = str(variant.get("key") or variant.get("variant") or "")
            if key == variant_key and str(variant.get("status")) == "available":
                return str(variant.get("path") or "")
    return ""


def _preflight_ttl_seconds() -> int:
    try:
        value = int(os.environ.get("XUNTUI_IMAGE_PREFLIGHT_TTL_SECONDS", "600"))
    except ValueError:
        value = 600
    return max(30, min(value, 86_400))


def _preflight_fingerprint(snapshot: Mapping[str, Any]) -> str:
    config = dict(snapshot.get("config") or {})
    model = dict(snapshot.get("model") or {})
    resource = dict(snapshot.get("resource") or {})
    payload = {
        "model": {key: model.get(key) for key in ("id", "repoId", "modelType", "group", "taskId", "adapterVariant")},
        "adapterPath": snapshot.get("adapterPath") or "",
        "parameters": {key: config.get(key) for key in ("trainingMode", "precision", "enableCpuOffload", "enableAttentionSlicing", "enableVaeTiling", "enableSafetyChecker", "loadTimeout", "negativePrompt", "width", "height", "steps", "cfg", "batchSize", "seed", "sampler")},
        "resource": {key: resource.get(key) for key in ("mode", "region", "zone", "gpuType", "gpuCount", "cpuCores", "memoryGb", "imageId")},
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def create_image_generation_router(
    store: Any,
    service_store: Any | None = None,
    *,
    task_runtime: Any | None = None,
    service_runtime: Any | None = None,
    result_root: Path | None = None,
    youyun_resource_resolver: Callable[[Mapping[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    youyun_image_checker: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
    evaluation_runtime: Any | None = None,
    evaluation_root: Path | None = None,
    community_image_verification_lookup: Callable[[str, str, str, str, str], Mapping[str, Any]] | None = None,
) -> APIRouter:
    router = APIRouter()
    resolved_result_root = (result_root or Path(os.environ.get("XUNTUI_IMAGE_GENERATION_RESULT_ROOT", "/root/xuntui-platform/runtime/image-generation-results"))).expanduser().resolve()
    resolved_evaluation_root = (evaluation_root or Path(os.environ.get("XUNTUI_IMAGE_GENERATION_EVALUATION_ROOT", "/root/xuntui-platform/runtime/image-generation-evaluations"))).expanduser().resolve()

    @router.get("/api/image-generation/models")
    async def list_image_generation_models(
        provider: str = Query("local"),
        includeArchived: bool = False,
    ) -> list[dict[str, Any]]:
        if provider not in {"local", "youyun"}:
            from fastapi import HTTPException

            raise HTTPException(status_code=400, detail="模型来源只能是 local 或 youyun。")
        return build_image_generation_catalog(
            store,
            provider=provider,
            include_archived=includeArchived,
        )

    def get_service(service_id: str) -> dict[str, Any]:
        if service_store is None or not hasattr(service_store, "get"):
            raise HTTPException(status_code=503, detail="文生图服务存储未配置。")
        service = service_store.get(service_id)
        if service is None:
            raise HTTPException(status_code=404, detail="文生图服务不存在。")
        config = dict(service.get("config") or {})
        if config.get("workloadType") != "image_generation":
            raise HTTPException(status_code=409, detail="所选服务不是文生图服务。")
        if service.get("state") == "archived":
            raise HTTPException(status_code=409, detail="已归档文生图服务不能创建任务。")
        return service

    def require_public_key(service_id: str, authorization: str | None) -> dict[str, Any]:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="缺少有效的 API Key。")
        service = service_store.authenticate_api_key(service_id, authorization[7:].strip()) if service_store is not None else None
        if service is None or service.get("state") == "archived":
            raise HTTPException(status_code=401, detail="API Key 无效或已失效。")
        if (service.get("config") or {}).get("workloadType") != "image_generation":
            raise HTTPException(status_code=409, detail="该 API Key 不适用于文生图服务。")
        return service

    def resolve_service_snapshot(service: Mapping[str, Any]) -> dict[str, Any]:
        config = dict(service.get("config") or {})
        catalog = build_image_generation_catalog(store, provider="youyun")
        model = next((item for item in catalog if item["id"] == config.get("modelId")), None)
        if model is None:
            raise HTTPException(status_code=409, detail="服务关联的文生图模型已不存在。")
        if not model.get("readyForYouyun"):
            raise HTTPException(status_code=409, detail=model.get("reason") or "服务关联模型当前不可用于优云。")
        training_mode = str(config.get("trainingMode") or "lora")
        if training_mode not in model.get("supportedTrainingModes", []):
            raise HTTPException(status_code=409, detail="服务关联的训练模式当前不可用。")
        resource = dict(config.get("resource") or config.get("youyunResource") or {})
        required = {"region", "zone", "gpuType", "gpuCount", "cpuCores", "memoryGb", "imageId"}
        missing = sorted(required.difference(resource))
        if missing:
            raise HTTPException(status_code=409, detail=f"文生图服务资源快照缺少字段：{', '.join(missing)}")
        adapter_path = _adapter_path_for_model(store, str(model["id"]))
        if model.get("group") == "trained" and not adapter_path:
            raise HTTPException(status_code=409, detail="服务关联的 Adapter 路径已失效。")
        return {"config": config, "model": model, "resource": resource, "adapterPath": adapter_path}

    def persist_preflight(service: Mapping[str, Any], preflight: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(preflight)
        now = time.time()
        value["checkedAtEpoch"] = now
        value["expiresAtEpoch"] = now + _preflight_ttl_seconds()
        config = dict(service.get("config") or {})
        config["preflightSnapshot"] = value
        if service_store is not None and hasattr(service_store, "update"):
            service_store.update(str(service.get("id") or ""), config=config)
        return value

    async def run_service_preflight(service: Mapping[str, Any], *, force: bool = True) -> dict[str, Any]:
        checked_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        if not force:
            config = dict(service.get("config") or {})
            cached = config.get("preflightSnapshot")
            try:
                snapshot = resolve_service_snapshot(service)
                fingerprint = _preflight_fingerprint(snapshot)
                if isinstance(cached, Mapping) and cached.get("ready") and cached.get("fingerprint") == fingerprint and float(cached.get("expiresAtEpoch") or 0) > time.time():
                    return {**dict(cached), "reused": True, "stale": False}
            except (HTTPException, TypeError, ValueError):
                pass
        checks: dict[str, dict[str, Any]] = {}
        try:
            snapshot = resolve_service_snapshot(service)
        except HTTPException as exc:
            checks["model"] = {"status": "blocked", "message": str(exc.detail)}
            return persist_preflight(service, {"serviceId": str(service.get("id") or ""), "ready": False, "checkedAt": checked_at, "checks": checks})

        model = snapshot["model"]
        checks["model"] = {"status": "ready", "message": "基座模型 Repo ID 和服务端 lineage 校验通过。", "modelId": model["id"], "repoId": model["repoId"], "modelType": model["modelType"]}
        if model.get("group") == "trained":
            checks["adapter"] = {"status": "ready", "message": "训练完成 Adapter 目录已解析。", "taskId": model.get("taskId"), "variant": model.get("adapterVariant"), "path": snapshot.get("adapterPath")}
        else:
            checks["adapter"] = {"status": "not_applicable", "message": "当前服务使用基座模型，不挂载 Adapter。"}

        resource = dict(snapshot["resource"])
        image_id = str(resource.get("imageId") or "")
        if youyun_image_checker is None:
            checks["image"] = {"status": "warning", "message": "未配置优云镜像实时检查；确认时仍会使用服务端镜像策略。", "imageId": image_id}
        else:
            try:
                image = await youyun_image_checker(image_id)
                requires_install = bool(image.get("requiresDiffusersInstall"))
                if requires_install:
                    resource["requiresDiffusersInstall"] = True
                checks["image"] = {"status": "warning" if requires_install else "ready", "message": str(image.get("message") or "Diffusers 镜像可用。"), "imageId": image.get("imageId") or image_id, "name": image.get("name")}
            except Exception as exc:
                checks["image"] = {"status": "blocked", "message": str(exc)}

        if checks["image"]["status"] == "blocked":
            return persist_preflight(service, {"serviceId": str(service.get("id") or ""), "ready": False, "checkedAt": checked_at, "checks": checks, "resource": resource, "fingerprint": _preflight_fingerprint(snapshot)})
        if youyun_resource_resolver is None:
            checks["capacity"] = {"status": "warning", "message": "未配置优云资源实时校验。"}
            checks["price"] = {"status": "warning", "message": "未配置优云价格实时校验。"}
            canonical_resource = resource
        else:
            try:
                canonical_resource = await youyun_resource_resolver(resource)
                if resource.get("requiresDiffusersInstall"):
                    canonical_resource["requiresDiffusersInstall"] = True
                capacity_fields = ("gpuType", "gpuCount", "cpuCores", "memoryGb")
                checks["capacity"] = {"status": "ready", "message": "优云实时库存校验通过。", **{key: canonical_resource.get(key) for key in capacity_fields}}
                price = canonical_resource.get("price") or {}
                if not isinstance(price, Mapping) or not isinstance(price.get("total"), (int, float)):
                    raise ValueError("优云价格接口未返回有效金额。")
                checks["price"] = {"status": "ready", "message": "优云按量价格校验通过。", "currency": price.get("currency") or "CNY", "total": price["total"]}
            except Exception as exc:
                checks["capacity"] = {"status": "blocked", "message": str(exc)}
                checks["price"] = {"status": "blocked", "message": "资源或价格校验未完成。"}
                return persist_preflight(service, {"serviceId": str(service.get("id") or ""), "ready": False, "checkedAt": checked_at, "checks": checks, "resource": resource, "fingerprint": _preflight_fingerprint(snapshot)})

        install_fallback = bool(resource.get("requiresDiffusersInstall")) or os.environ.get("XUNTUI_REMOTE_DIFFUSERS_INSTALL", "0").strip().lower() in {"1", "true", "yes", "on"}
        checks["runtimePolicy"] = {"status": "warning" if install_fallback else "ready", "message": "允许远端安装 Diffusers 作为兜底；实例内安装和导入仍需在真实验收时确认。" if install_fallback else "优先使用预置 Diffusers 镜像；实例内导入仍需在真实验收时确认。"}
        blocked = any(item.get("status") == "blocked" for item in checks.values())
        return persist_preflight(service, {"serviceId": str(service.get("id") or ""), "ready": not blocked, "checkedAt": checked_at, "checks": checks, "resource": canonical_resource, "fingerprint": _preflight_fingerprint({**snapshot, "resource": canonical_resource})})

    @router.post("/api/image-generation/services/{service_id}/preflight")
    async def image_generation_service_preflight(service_id: str) -> dict[str, Any]:
        return await run_service_preflight(get_service(service_id), force=True)

    @router.post("/api/image-generation/services/{service_id}/start")
    async def start_image_generation_service(service_id: str) -> dict[str, Any]:
        service = get_service(service_id)
        preflight = await run_service_preflight(service, force=False)
        if not preflight.get("ready"):
            raise HTTPException(status_code=409, detail={"message": "文生图服务启动前检查未通过。", "preflight": preflight})
        if service_runtime is None:
            raise HTTPException(status_code=503, detail="文生图服务执行器未配置。")
        try:
            return await service_runtime.start(service_id, preflight=preflight)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (RuntimeError, TimeoutError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/api/image-generation/services/{service_id}/stop")
    async def stop_image_generation_service(service_id: str) -> dict[str, Any]:
        if service_runtime is None:
            raise HTTPException(status_code=503, detail="文生图服务执行器未配置。")
        try:
            return await service_runtime.stop(service_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/api/image-generation/services/{service_id}/logs")
    async def image_generation_service_logs(service_id: str, limit: int = 200) -> dict[str, Any]:
        service = service_store.get(service_id) if service_store is not None and hasattr(service_store, "get") else None
        if service is None:
            raise HTTPException(status_code=404, detail="文生图服务不存在。")
        if (service.get("config") or {}).get("workloadType") != "image_generation":
            raise HTTPException(status_code=409, detail="所选服务不是文生图服务。")
        logs = service_store.logs(service_id, limit=limit) if hasattr(service_store, "logs") else []
        return {"serviceId": service_id, "state": service.get("state"), "logs": logs}

    specs_cache: dict[tuple[str, ...], tuple[float, dict[str, Any]]] = {}
    specs_cache_ttl = max(0.0, float(os.environ.get("XUNTUI_IMAGE_GENERATION_SPECS_CACHE_SECONDS", "30")))
    discovery_cache: dict[tuple[str, str], tuple[float, Any]] = {}
    discovery_cache_ttl = max(0.0, float(os.environ.get("XUNTUI_YOUYUN_DISCOVERY_CACHE_SECONDS", "30")))

    async def cached_discovery(kind: str, scope: str, loader: Callable[[], Awaitable[Any]]) -> Any:
        """Reuse slow, read-only provider catalogs across regional selector requests."""
        key = (kind, scope)
        cached = discovery_cache.get(key)
        if discovery_cache_ttl > 0 and cached is not None and (time.monotonic() - cached[0]) < discovery_cache_ttl:
            return cached[1]
        value = await loader()
        if discovery_cache_ttl > 0:
            discovery_cache[key] = (time.monotonic(), value)
        return value

    @router.get("/api/image-generation/youyun/gpu-specs")
    async def image_generation_gpu_specs(
        gpuType: str | None = None,
        imageId: str | None = None,
        instanceMode: str | None = None,
        region: str | None = None,
        zone: str | None = None,
        purpose: str | None = None,
        includeCommunity: bool = True,
    ) -> dict[str, Any]:
        cache_key = (
            gpuType or "", imageId or "", instanceMode or "",
            region or "", zone or "", purpose or "", str(includeCommunity),
        )
        cached = specs_cache.get(cache_key)
        if specs_cache_ttl > 0 and cached is not None and (time.monotonic() - cached[0]) < specs_cache_ttl:
            return cached[1]
        from backend.providers.youyun.client import YouyunClient
        from backend.providers.youyun.models import YouyunApiError
        # 复用 NLP 域的进程级共享缓存：目录（zones/images/机型）30s TTL + 容量
        # singleflight（10s）。此前闭包级缓存导致两域各自冷启动、容量查询
        # 每请求直连优云（auto 模式还会因可用区切换重复查）。
        from backend.app import (
            _cached_youyun_capacity,
            _cached_youyun_instance_types_for_zone,
            _cached_youyun_support_zones,
            _cached_youyun_zone_images,
        )

        try:
            client = YouyunClient()
            # Include the client implementation in discovery-cache keys. The
            # production client is stable across requests, while tests and
            # injected providers may expose different catalogs behind the
            # same region/zone values.
            client_scope = f"{type(client).__module__}:{type(client).__qualname__}"
            selected_region = (region or client.config.region).strip()
            automatic_mode = instanceMode in {None, "auto"}
            selected_mode = instanceMode if instanceMode in {"pod", "uhost"} else "pod"
            fallback_reason = ""
            selected_zone = (zone or client.config.zone).strip()
            zones_loader = getattr(client, "describe_support_zones", None)
            # Return the full region/zone catalog for the selector. The
            # inventory calls below remain scoped to the selected location.
            zones = (
                await _cached_youyun_support_zones(client)
                if callable(zones_loader)
                else []
            )
            health_image = imageId or client.config.image_id
            health_gpu = gpuType or client.config.gpu_type
            zones = [
                {
                    **item,
                    "supportedModes": list(youyun_zone_modes(item)),
                    "capabilities": youyun_zone_capabilities(item),
                    **youyun_resource_health.metadata({
                        "instanceMode": youyun_zone_default_mode(item, selected_mode),
                        "region": str(item.get("region") or selected_region),
                        "zone": item.get("zone"),
                        "imageId": health_image,
                        "gpuType": health_gpu,
                        "chargeType": client.config.charge_type,
                    }),
                }
                for item in (zones or [])
            ]
            healthy_zones = [item for item in zones if item.get("healthStatus") != "cooldown"]
            if zones and selected_zone:
                selected_zone_info = next(
                    (
                        item for item in zones
                        if item.get("region") == selected_region and item.get("zone") == selected_zone
                    ),
                    None,
                )
                selected_zone_is_cooldown = bool(selected_zone_info and selected_zone_info.get("healthStatus") == "cooldown")
                if automatic_mode:
                    # In auto mode an explicitly selected healthy zone is a
                    # user preference, including a UHost zone. Infer the mode
                    # before validating the zone/mode pair below.
                    if selected_zone_info is not None and not selected_zone_is_cooldown:
                        selected_mode = youyun_zone_default_mode(selected_zone_info, selected_mode)
                    preferred = [item for item in healthy_zones if item.get("region") == selected_region and youyun_zone_supports_mode(item, selected_mode)]
                    if selected_zone_is_cooldown or not preferred:
                        preferred = [item for item in healthy_zones if item.get("region") == selected_region and youyun_zone_supports_mode(item, "uhost")]
                        if preferred and selected_mode == "pod":
                            selected_mode = "uhost"
                            fallback_reason = "Pod 可用区近期创建接口不可用，已切换到健康的 UHost 可用区。" if selected_zone_is_cooldown else "Pod 当前无健康可用区，已切换到健康的 UHost 可用区。"
                    if preferred and (not zone or selected_zone_is_cooldown or not youyun_zone_supports_mode(preferred[0], selected_mode)):
                        selected_zone = str(preferred[0].get("zone") or selected_zone)
                        selected_zone_info = preferred[0]
                elif selected_zone_is_cooldown:
                    raise HTTPException(status_code=409, detail="所选优云可用区近期创建接口不可用，请刷新后更换可用区。")
                if not zone and not automatic_mode:
                    matching_zone = next(
                        (
                            item for item in healthy_zones
                            if item.get("region") == selected_region
                            and youyun_zone_supports_mode(item, selected_mode)
                        ),
                        None,
                    )
                    if matching_zone is not None:
                        selected_zone = str(matching_zone.get("zone") or selected_zone)
                        selected_zone_info = matching_zone
                if selected_zone_info is None or not youyun_zone_supports_mode(selected_zone_info, selected_mode):
                    raise HTTPException(status_code=422, detail="所选实例模式与可用区不匹配，请重新选择。")
            location_kwargs = {} if selected_region == client.config.region and selected_zone == client.config.zone else {"region": selected_region, "zone": selected_zone}
            community_task = (
                asyncio.create_task(
                    cached_discovery(
                        "community-images",
                        f"{client_scope}|global",
                        lambda: asyncio.to_thread(
                            client.describe_community_images,
                            limit=100,
                        ),
                    )
                )
                if includeCommunity
                else None
            )
            machine_types, images = await asyncio.gather(
                _cached_youyun_instance_types_for_zone(client, selected_region, selected_zone),
                _cached_youyun_zone_images(client, selected_region, selected_zone),
            )
            try:
                community_images = await community_task if community_task is not None else []
            except Exception:
                community_images = []
            configured_image = imageId or os.environ.get("XUNTUI_IMAGE_GENERATION_INFERENCE_IMAGE", "").strip() or client.config.image_id
            selected_gpu_type = gpuType or client.config.gpu_type
            verification_by_id = {
                str(item.get("image_id") or ""): dict(
                    community_image_verification_lookup(
                        "image_generation_training",
                        str(item.get("image_id") or ""),
                        selected_region,
                        selected_zone,
                        selected_gpu_type,
                    )
                )
                for item in community_images
                if item.get("image_id") and community_image_verification_lookup is not None
            }
            ranked_community = rank_community_image_candidates(
                community_images,
                workload="image_generation_training",
                gpu_type=selected_gpu_type,
                verification_by_id=verification_by_id,
            )
            selected_community = next(
                (item for item in ranked_community if str(item.get("image_id") or "") == configured_image and item.get("selectable")),
                None,
            )
            image_selection_warning = ""
            if selected_community is not None:
                selected_images = []
                selected_image = selected_community
            else:
                try:
                    selected_images, selected_image = select_image_generation_images(
                        images,
                        configured_image_id=configured_image,
                        prefer_configured_image=bool(imageId or os.environ.get("XUNTUI_IMAGE_GENERATION_INFERENCE_IMAGE", "").strip()),
                        allow_install_fallback=purpose == "training",
                    )
                except ValueError as exc:
                    # Resource discovery must remain usable even when no prebuilt
                    # Diffusers image is published. Keep a healthy compute catalog
                    # visible and mark the selected Torch image for preflight.
                    if purpose != "inference":
                        raise HTTPException(status_code=409, detail=str(exc)) from exc
                    available = [
                        item
                        for item in install_fallback_pool(images)
                    ]
                    if not available:
                        raise HTTPException(status_code=409, detail=str(exc)) from exc
                    available.sort(key=_fallback_image_key, reverse=True)
                    selected_image = next(
                        (item for item in available if str(item.get("image_id") or "") == configured_image),
                        available[0],
                    )
                    selected_image = {**selected_image, "_requiresDiffusersInstall": True}
                    selected_images = available
                    image_selection_warning = str(exc)
            selected_image_id = str(selected_image.get("image_id") or "")
            capacity = await _cached_youyun_capacity(
                client,
                gpu_type=selected_gpu_type,
                image_id=selected_image_id,
                charge_type=client.config.charge_type,
                region=selected_region,
                zone=selected_zone,
            )
            if automatic_mode and not any(item.gpu == 1 and item.resource_enough for item in capacity.specs):
                fallback_zone = next((item.get("zone") for item in healthy_zones if item.get("region") == selected_region and youyun_zone_supports_mode(item, "uhost")), None)
                if fallback_zone:
                    selected_mode = "uhost"
                    fallback_reason = "Pod 当前无可用单卡 CPU/内存组合，已切换到 UHost。"
                    selected_zone = str(fallback_zone)
                    location_kwargs = {"region": selected_region, "zone": selected_zone}
                    client = YouyunClient()
                    machine_types, images = await asyncio.gather(
                        _cached_youyun_instance_types_for_zone(client, selected_region, selected_zone),
                        _cached_youyun_zone_images(client, selected_region, selected_zone),
                    )
                    capacity = await _cached_youyun_capacity(
                        client,
                        gpu_type=selected_gpu_type,
                        image_id=selected_image_id,
                        charge_type=client.config.charge_type,
                        region=selected_region,
                        zone=selected_zone,
                    )
        except YouyunApiError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        recommended_community = next((item for item in ranked_community if item.get("selectable")), next((item for item in ranked_community if item.get("compatible")), None))
        payload = {
            "instanceMode": selected_mode,
            "fallback": bool(fallback_reason),
            "fallbackReason": fallback_reason,
            "fallbackEligible": automatic_mode,
            "region": selected_region,
            "zone": selected_zone,
            "zones": zones,
            "chargeType": client.config.charge_type,
            "gpuType": selected_gpu_type,
            "imageId": selected_image_id,
            "recommendedImageId": selected_image_id,
            "recommendedImageName": str((selected_image or {}).get("name") or "Diffusers 镜像"),
            "recommendedCommunityImageId": str((recommended_community or {}).get("image_id") or ""),
            "recommendedCommunityImageName": str((recommended_community or {}).get("name") or ""),
            "requiresDiffusersInstall": False if selected_community is not None else bool(selected_image.get("_requiresDiffusersInstall")) or not _diffusers_image(selected_image),
            "imageSelectionWarning": image_selection_warning,
            "images": [{"id": item.get("image_id"), "name": item.get("name"), "description": item.get("description"), "status": item.get("status")} for item in selected_images],
            "communityImages": [
                {
                    "id": item.get("image_id"),
                    "name": item.get("name") or "未命名社区镜像",
                    "versionName": item.get("version_name") or "",
                    "status": item.get("status") or "Unknown",
                    "tags": item.get("tags") or [],
                    "description": item.get("description") or "",
                    "available": bool(item.get("image_id")) and str(item.get("status") or "").casefold() in {"available", "normal", "ready"},
                    "verificationState": str((verification_by_id.get(str(item.get("image_id") or "")) or {}).get("state") or "unverified"),
                    "verificationExpiresAt": (verification_by_id.get(str(item.get("image_id") or "")) or {}).get("expiresAtEpoch"),
                    "verificationMessage": (verification_by_id.get(str(item.get("image_id") or "")) or {}).get("message"),
                    "supportedGpuTypes": item.get("supported_gpu_types") or [],
                    "compatible": bool(item.get("compatible")),
                    "selectable": bool(item.get("selectable")),
                    "gpuSupported": bool(item.get("gpuSupported")),
                    "matchScore": item.get("matchScore", 0),
                    "recommendationReason": item.get("recommendationReason") or "镜像用途与当前任务不匹配，已自动隐藏。",
                }
                for item in ranked_community
            ],
            "machineTypes": [
                {
                    "name": item.name,
                    "zone": item.zone,
                    "soldOut": item.sold_out,
                    "description": item.description,
                    "graphicsMemoryGb": item.graphics_memory_gb,
                    **youyun_machine_inventory_metadata(item, capacity, selected_gpu_type),
                }
                for item in machine_types
                if item.zone == selected_zone
            ],
            "capacity": [{"gpu": item.gpu, "cpu": item.cpu, "memoryGb": item.memory_gb, "available": item.resource_enough} for item in capacity.specs],
            "source": "youyun-live",
        }
        if specs_cache_ttl > 0:
            specs_cache[cache_key] = (time.monotonic(), payload)
        return payload

    @router.post("/api/image-generation/services", status_code=status.HTTP_201_CREATED)
    async def create_image_generation_service(payload: ImageGenerationServiceCreate) -> dict[str, Any]:
        catalog = build_image_generation_catalog(store, provider="youyun")
        model = next((item for item in catalog if item["id"] == payload.modelId), None)
        if model is None:
            raise HTTPException(status_code=404, detail="文生图模型不存在或未登记。")
        if not model["readyForYouyun"]:
            raise HTTPException(status_code=409, detail=model.get("reason") or "模型尚未达到优云部署条件。")
        if payload.trainingMode not in model["supportedTrainingModes"]:
            raise HTTPException(status_code=409, detail="当前模型不支持所选训练方式；QLoRA 需要兼容性探针通过。")
        if payload.width * payload.height > 2_097_152:
            raise HTTPException(status_code=422, detail="图片总像素不能超过 2,097,152。")
        adapter_path = _adapter_path_for_model(store, payload.modelId)
        if model.get("group") == "trained" and not adapter_path:
            raise HTTPException(status_code=409, detail="所选 Adapter 路径不存在或尚未就绪。")
        resource = payload.resource
        required = {"region", "zone", "gpuType", "gpuCount", "cpuCores", "memoryGb", "imageId"}
        missing = sorted(required.difference(resource))
        if missing:
            raise HTTPException(status_code=422, detail=f"优云资源快照缺少字段：{', '.join(missing)}")
        if youyun_resource_resolver is not None:
            try:
                requires_diffusers_install = bool(resource.get("requiresDiffusersInstall"))
                resource = await youyun_resource_resolver(resource)
                if requires_diffusers_install:
                    resource["requiresDiffusersInstall"] = True
            except Exception as exc:
                raise HTTPException(status_code=409, detail=f"优云资源校验失败：{exc}") from exc
        config = payload.model_dump()
        config.update({"resource": resource, "workloadType": "image_generation", "provider": "youyun", "modelRepoId": model["repoId"], "adapterPath": adapter_path, "adapterLineage": {"taskId": model.get("taskId"), "variant": model.get("adapterVariant")}})
        service_id = f"igs-{uuid.uuid4().hex[:12]}"
        if service_store is not None and hasattr(service_store, "create"):
            from backend.inference_service import InferenceCreate
            base_payload = InferenceCreate(name=payload.name, taskId=payload.taskId or f"image-generation:{payload.modelId}", provider="youyun", owner="平台管理员", youyunResource=resource)
            created = service_store.create(service_id=service_id, payload=base_payload, model=model["repoId"], adapter="", endpoint="")
            created_config = dict(created.get("config") or {})
            created_config.update(config)
            return service_store.update(service_id, config=created_config) or created
        record = {"id": service_id, "name": payload.name, "state": "created", "config": config, "message": "配置已保存，可创建文生图任务。"}
        return record

    @router.get("/api/image-generation/services")
    async def list_image_generation_services() -> list[dict[str, Any]]:
        if service_store is None or not hasattr(service_store, "list"):
            return []
        return [
            service
            for service in service_store.list(include_archived=False)
            if (service.get("config") or {}).get("workloadType") == "image_generation"
        ]

    @router.post("/api/image-generation/tasks", status_code=status.HTTP_201_CREATED)
    async def create_image_generation_task(payload: ImageGenerationTaskCreate) -> dict[str, Any]:
        if task_runtime is None:
            raise HTTPException(status_code=503, detail="文生图任务执行器未配置。")
        check_resource_access(payload.serviceId)
        service = get_service(payload.serviceId)
        if service_runtime is not None and str(service.get("state") or "") != "running":
            raise HTTPException(status_code=409, detail="文生图推理服务尚未运行，请先启动服务。")
        snapshot = resolve_service_snapshot(service)
        config = snapshot["config"]
        model = snapshot["model"]
        width = payload.width if payload.width is not None else int(config.get("width") or 1024)
        height = payload.height if payload.height is not None else int(config.get("height") or 1024)
        if width * height > 2_097_152:
            raise HTTPException(status_code=422, detail="图片总像素不能超过 2,097,152。")
        task_id = f"igt-{time.strftime('%y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
        task = {
            "id": task_id,
            "name": f"{service.get('name') or '文生图'} · {payload.prompt[:24]}",
            "serviceId": payload.serviceId,
            "serviceName": service.get("name"),
            "provider": "youyun",
            "workloadType": "image_generation",
            "modelId": model["id"],
            "modelRepoId": model["repoId"],
            "modelType": model["modelType"],
            "adapterLineage": config.get("adapterLineage") or {},
            "adapterPath": snapshot.get("adapterPath") or "",
            "trainingMode": config.get("trainingMode") or "lora",
            "resource": snapshot["resource"],
            "prompt": payload.prompt,
            "negativePrompt": payload.negativePrompt or str(config.get("negativePrompt") or ""),
            "precision": config.get("precision") or "auto",
            "enableCpuOffload": bool(config.get("enableCpuOffload")),
            "enableAttentionSlicing": bool(config.get("enableAttentionSlicing")),
            "enableVaeTiling": bool(config.get("enableVaeTiling")),
            "enableSafetyChecker": bool(config.get("enableSafetyChecker")),
            "loadTimeout": int(config.get("loadTimeout") or 180),
            "width": width,
            "height": height,
            "steps": payload.steps if payload.steps is not None else int(config.get("steps") or 30),
            "cfg": payload.cfg if payload.cfg is not None else float(config.get("cfg") or 7),
            "batchSize": payload.batchSize if payload.batchSize is not None else int(config.get("batchSize") or 1),
            "seed": payload.seed if payload.seed is not None else int(config.get("seed") if config.get("seed") is not None else -1),
            "sampler": payload.sampler or str(config.get("sampler") or "euler"),
            "state": "awaiting_confirmation",
            "status": "draft",
            "progress": 0,
            "currentStep": "等待提交到已运行的文生图服务",
            "message": "服务实例由服务启动按钮创建，任务提交不会创建新实例。",
            "results": [],
            "logs": ["任务配置已保存，尚未创建优云实例。"],
            "resultRoot": str((resolved_result_root / task_id).resolve()),
        }
        created = task_runtime.create_task(task)
        # A task can be drafted before the service is started, but only a
        # running service may submit work.  Submission never provisions a
        # cloud instance; the service runtime owns that lease.
        if str(service.get("state") or "") == "running":
            submit = getattr(task_runtime, "submit", None)
            if callable(submit):
                submitted = submit(task_id)
                if inspect.isawaitable(submitted):
                    submitted = await submitted
                return submitted or created
            confirmed = getattr(task_runtime, "confirm", None)
            if callable(confirmed):
                submitted = confirmed(task_id)
                if inspect.isawaitable(submitted):
                    submitted = await submitted
                return submitted or created
        return created

    @router.post("/api/public/image-generation/services/{service_id}/generate", status_code=status.HTTP_201_CREATED)
    async def public_image_generation_task(
        service_id: str,
        payload: ImageGenerationPublicRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        require_public_key(service_id, authorization)
        return await create_image_generation_task(
            ImageGenerationTaskCreate(serviceId=service_id, **payload.model_dump())
        )

    def ensure_task_action_allowed(task: Mapping[str, Any], action: str) -> None:
        if task.get("archived"):
            raise HTTPException(status_code=409, detail=f"已归档文生图任务不能{action}。")

    @router.get("/api/image-generation/tasks")
    async def list_image_generation_tasks(
        serviceId: str | None = Query(default=None),
        state: str | None = Query(default=None),
        query: str | None = Query(default=None),
        includeArchived: bool = Query(default=False),
    ) -> list[dict[str, Any]]:
        if task_runtime is None:
            return []
        return task_runtime.list_tasks(service_id=serviceId, state=state, query=query, include_archived=includeArchived)

    @router.get("/api/image-generation/tasks/{task_id}")
    async def get_image_generation_task(task_id: str) -> dict[str, Any]:
        if task_runtime is None:
            raise HTTPException(status_code=503, detail="文生图任务执行器未配置。")
        try:
            task = task_runtime.get(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if task is None:
            raise HTTPException(status_code=404, detail="文生图任务不存在。")
        return task

    @router.post("/api/image-generation/tasks/{task_id}/confirm")
    async def confirm_image_generation_task(task_id: str) -> dict[str, Any]:
        task = await get_image_generation_task(task_id)
        ensure_task_action_allowed(task, "确认")
        service = get_service(str(task.get("serviceId") or ""))
        if service_runtime is not None and str(service.get("state") or "") != "running":
            raise HTTPException(status_code=409, detail="文生图推理服务尚未运行，请先启动服务。")
        preflight = await run_service_preflight(service, force=False)
        if not preflight.get("ready"):
            raise HTTPException(status_code=409, detail={"message": "优云启动前预检未通过。", "preflight": preflight})
        snapshot = resolve_service_snapshot(service)
        task_runtime.update(task_id, modelRepoId=snapshot["model"]["repoId"], adapterPath=snapshot.get("adapterPath") or "", resource=preflight.get("resource") or snapshot["resource"], preflight=preflight)
        try:
            submit = getattr(task_runtime, "submit", None) or getattr(task_runtime, "confirm", None)
            confirmed = submit(task_id) if callable(submit) else None
            if inspect.isawaitable(confirmed):
                confirmed = await confirmed
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if confirmed is None:
            raise HTTPException(status_code=404, detail="文生图任务不存在。")
        return confirmed

    @router.post("/api/image-generation/tasks/{task_id}/stop")
    async def stop_image_generation_task(task_id: str) -> dict[str, Any]:
        if task_runtime is None:
            raise HTTPException(status_code=503, detail="文生图任务执行器未配置。")
        task = await get_image_generation_task(task_id)
        ensure_task_action_allowed(task, "停止")
        task = await task_runtime.request_stop(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="文生图任务不存在。")
        return task

    @router.post("/api/image-generation/tasks/{task_id}/retry")
    async def retry_image_generation_task(task_id: str) -> dict[str, Any]:
        if task_runtime is None:
            raise HTTPException(status_code=503, detail="文生图任务执行器未配置。")
        existing = await get_image_generation_task(task_id)
        ensure_task_action_allowed(existing, "重试")
        resolve_service_snapshot(get_service(str(existing.get("serviceId") or "")))
        try:
            task = await task_runtime.retry(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if task is None:
            raise HTTPException(status_code=404, detail="文生图任务不存在。")
        return task

    @router.post("/api/image-generation/tasks/{task_id}/clone", status_code=status.HTTP_201_CREATED)
    async def clone_image_generation_task(task_id: str, payload: ImageGenerationTaskClone) -> dict[str, Any]:
        if task_runtime is None:
            raise HTTPException(status_code=503, detail="文生图任务执行器未配置。")
        source = await get_image_generation_task(task_id)
        service = get_service(str(source.get("serviceId") or ""))
        snapshot = resolve_service_snapshot(service)
        overrides = payload.model_dump(exclude_none=True)
        effective_width = int(overrides.get("width", source.get("width") or snapshot["config"].get("width") or 1024))
        effective_height = int(overrides.get("height", source.get("height") or snapshot["config"].get("height") or 1024))
        if effective_width * effective_height > 2_097_152:
            raise HTTPException(status_code=422, detail="图片总像素不能超过 2,097,152。")
        try:
            clone = task_runtime.clone_task(task_id, overrides)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if clone is None:
            raise HTTPException(status_code=404, detail="文生图任务不存在。")
        clone["modelRepoId"] = snapshot["model"]["repoId"]
        clone["adapterPath"] = snapshot.get("adapterPath") or ""
        clone["resource"] = snapshot["resource"]
        if hasattr(task_runtime, "save"):
            clone = task_runtime.save(clone)
        return clone

    @router.post("/api/image-generation/tasks/{task_id}/archive")
    async def archive_image_generation_task(task_id: str) -> dict[str, Any]:
        if task_runtime is None:
            raise HTTPException(status_code=503, detail="文生图任务执行器未配置。")
        existing = await get_image_generation_task(task_id)
        if existing.get("archived"):
            raise HTTPException(status_code=409, detail="已归档文生图任务不能再次归档。")
        try:
            task = task_runtime.archive_task(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if task is None:
            raise HTTPException(status_code=404, detail="文生图任务不存在。")
        return task

    @router.post("/api/image-generation/tasks/{task_id}/restore")
    async def restore_image_generation_task(task_id: str) -> dict[str, Any]:
        if task_runtime is None:
            raise HTTPException(status_code=503, detail="文生图任务执行器未配置。")
        await get_image_generation_task(task_id)
        try:
            task = task_runtime.restore_task(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if task is None:
            raise HTTPException(status_code=404, detail="文生图任务不存在。")
        return task

    @router.delete("/api/image-generation/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_image_generation_task(task_id: str) -> None:
        if task_runtime is None:
            raise HTTPException(status_code=503, detail="文生图任务执行器未配置。")
        await get_image_generation_task(task_id)
        try:
            deleted = task_runtime.delete_task(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=409, detail="文生图结果文件清理失败，任务记录已保留。") from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="文生图任务不存在。")
        return None

    @router.get("/api/image-generation/services/{service_id}/results")
    async def image_generation_service_results(
        service_id: str,
        state: str | None = Query(default=None),
        query: str | None = Query(default=None),
        includeArchived: bool = Query(default=False),
        page: int = Query(default=1, ge=1),
        pageSize: int = Query(default=20, ge=1, le=100),
    ) -> dict[str, Any]:
        if service_store is None or not hasattr(service_store, "get"):
            raise HTTPException(status_code=503, detail="文生图服务存储未配置。")
        service = service_store.get(service_id)
        if service is None:
            raise HTTPException(status_code=404, detail="文生图服务不存在。")
        if (service.get("config") or {}).get("workloadType") != "image_generation":
            raise HTTPException(status_code=409, detail="所选服务不是文生图服务。")
        if task_runtime is None:
            return {"serviceId": service_id, "items": [], "page": page, "pageSize": pageSize, "total": 0}
        tasks = task_runtime.list_tasks(service_id=service_id, state=state, query=query, include_archived=includeArchived)

        def public_result(task: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
            item = {key: result.get(key) for key in ("id", "fileName", "width", "height", "seed", "createdAt") if result.get(key) is not None}
            task_id = str(task.get("id") or "")
            task_root = (resolved_result_root / task_id).resolve()
            candidate = Path(str(result.get("path") or task_root / Path(str(result.get("fileName") or "")).name)).expanduser().resolve()
            item["available"] = candidate.parent == task_root and candidate.suffix.lower() == ".png" and candidate.is_file()
            return item

        safe_items: list[dict[str, Any]] = []
        for task in tasks:
            safe_items.append(
                {
                    "taskId": task.get("id"),
                    "taskName": task.get("name"),
                    "serviceId": task.get("serviceId"),
                    "prompt": task.get("prompt"),
                    "negativePrompt": task.get("negativePrompt"),
                    "width": task.get("width"),
                    "height": task.get("height"),
                    "steps": task.get("steps"),
                    "cfg": task.get("cfg"),
                    "batchSize": task.get("batchSize"),
                    "seed": task.get("seed"),
                    "sampler": task.get("sampler"),
                    "modelId": task.get("modelId"),
                    "modelRepoId": task.get("modelRepoId"),
                    "adapterLineage": task.get("adapterLineage") or {},
                    "trainingMode": task.get("trainingMode"),
                    "state": task.get("state"),
                    "progress": task.get("progress") or 0,
                    "archived": bool(task.get("archived")),
                    "createdAt": task.get("createdAt"),
                    "updatedAt": task.get("updatedAt"),
                    "logs": list(task.get("logs") or [])[-200:],
                    "phase": task.get("phase") or task.get("state"),
                    "lastHeartbeatAt": task.get("lastHeartbeatAt"),
                    "failureCode": task.get("failureCode"),
                    "failureMessage": task.get("failureMessage"),
                    "suggestedAction": task.get("suggestedAction"),
                    "cleanupState": task.get("cleanupState"),
                    "cleanupError": task.get("cleanupError"),
                    "results": [public_result(task, result) for result in task.get("results") or [] if isinstance(result, Mapping)],
                }
            )
        total = len(safe_items)
        start = (page - 1) * pageSize
        return {"serviceId": service_id, "items": safe_items[start : start + pageSize], "page": page, "pageSize": pageSize, "total": total}

    def get_evaluation(task_id: str, service_id: str) -> dict[str, Any]:
        if evaluation_runtime is None:
            raise HTTPException(status_code=503, detail="文生图评测执行器未配置。")
        task = evaluation_runtime.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="文生图评测任务不存在。")
        if str(task.get("serviceId") or "") != str(service_id):
            raise HTTPException(status_code=404, detail="文生图评测任务不存在。")
        return task

    @router.get("/api/image-generation/evaluation-datasets")
    async def image_generation_evaluation_datasets() -> list[dict[str, Any]]:
        try:
            return await asyncio.to_thread(list_image_generation_evaluation_datasets, store)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/api/image-generation/evaluations/validate")
    async def validate_image_generation_evaluation(payload: dict[str, Any]) -> dict[str, Any]:
        dataset_id = str(payload.get("datasetId") or "").strip()
        max_samples_raw = payload.get("maxSamples")
        max_samples = int(max_samples_raw) if max_samples_raw not in (None, "") else None
        try:
            return await asyncio.to_thread(validate_image_generation_evaluation_dataset, store, dataset_id, max_samples)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/api/image-generation/services/{service_id}/evaluations")
    async def list_image_generation_evaluations(service_id: str, includeArchived: bool = Query(default=False)) -> list[dict[str, Any]]:
        get_service(service_id)
        if evaluation_runtime is None:
            return []
        return evaluation_runtime.list_tasks(service_id=service_id, include_archived=includeArchived)

    @router.post("/api/image-generation/services/{service_id}/evaluations", status_code=status.HTTP_201_CREATED)
    async def create_image_generation_evaluation(service_id: str, payload: ImageGenerationEvaluationCreate) -> dict[str, Any]:
        service = get_service(service_id)
        if evaluation_runtime is None:
            raise HTTPException(status_code=503, detail="文生图评测执行器未配置。")
        config = dict(service.get("config") or {})
        try:
            validation = await asyncio.to_thread(validate_image_generation_evaluation_dataset, store, payload.datasetId, payload.maxSamples)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not validation.get("valid"):
            raise HTTPException(status_code=409, detail={"message": "评测集校验未通过。", **validation})
        task_id = f"ige-{time.strftime('%y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
        task = evaluation_runtime.create_task({
            "id": task_id,
            "name": f"{service.get('name') or service_id} · 验证评测",
            "serviceId": service_id,
            "serviceName": service.get("name"),
            "datasetId": validation["datasetId"],
            "datasetName": validation["datasetName"],
            "datasetSnapshot": validation["snapshot"],
            "samples": validation["samples"],
            "sampleCount": validation["sampleCount"],
            "width": payload.width or config.get("width") or 1024,
            "height": payload.height or config.get("height") or 1024,
            "steps": payload.steps or config.get("steps") or 30,
            "cfg": payload.cfg if payload.cfg is not None else config.get("cfg") or 7,
            "seed": payload.seed if payload.seed is not None else config.get("seed") if config.get("seed") is not None else -1,
            "sampler": payload.sampler or config.get("sampler") or "euler",
            "modelRepoId": config.get("modelRepoId"),
            "adapterPath": _adapter_path_for_model(store, str(config.get("modelId") or "")) or "",
            "resource": config.get("resource") or {},
            "resultRoot": str(resolved_evaluation_root / task_id),
        })
        return task

    @router.get("/api/image-generation/services/{service_id}/evaluations/{evaluation_id}")
    async def get_image_generation_evaluation(service_id: str, evaluation_id: str) -> dict[str, Any]:
        return get_evaluation(evaluation_id, service_id)

    @router.post("/api/image-generation/services/{service_id}/evaluations/{evaluation_id}/confirm")
    async def confirm_image_generation_evaluation(service_id: str, evaluation_id: str) -> dict[str, Any]:
        task = get_evaluation(evaluation_id, service_id)
        service = get_service(service_id)
        if task.get("state") != "awaiting_confirmation":
            raise HTTPException(status_code=409, detail="只有待确认的文生图评测任务可以确认。")
        try:
            preflight = await run_service_preflight(service, force=True)
        except HTTPException as exc:
            raise HTTPException(status_code=409, detail=str(exc.detail)) from exc
        if not preflight.get("ready"):
            raise HTTPException(status_code=409, detail=preflight)
        try:
            fresh = await asyncio.to_thread(validate_image_generation_evaluation_dataset, store, str(task.get("datasetId") or ""), int(task.get("sampleCount") or 0) or None)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not fresh.get("valid"):
            raise HTTPException(status_code=409, detail={"message": "评测集在确认前校验未通过。", **fresh})
        evaluation_runtime.update(evaluation_id, preflight=preflight, samples=fresh["samples"], datasetSnapshot=fresh["snapshot"])
        try:
            confirmed = await evaluation_runtime.confirm(evaluation_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return confirmed or task

    @router.post("/api/image-generation/services/{service_id}/evaluations/{evaluation_id}/stop")
    async def stop_image_generation_evaluation(service_id: str, evaluation_id: str) -> dict[str, Any]:
        get_evaluation(evaluation_id, service_id)
        try:
            task = await evaluation_runtime.request_stop(evaluation_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return task or get_evaluation(evaluation_id, service_id)

    @router.post("/api/image-generation/services/{service_id}/evaluations/{evaluation_id}/retry")
    async def retry_image_generation_evaluation(service_id: str, evaluation_id: str) -> dict[str, Any]:
        get_evaluation(evaluation_id, service_id)
        try:
            task = await evaluation_runtime.retry(evaluation_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return task or get_evaluation(evaluation_id, service_id)

    @router.get("/api/image-generation/services/{service_id}/evaluations/{evaluation_id}/logs")
    async def image_generation_evaluation_logs(service_id: str, evaluation_id: str) -> dict[str, Any]:
        task = get_evaluation(evaluation_id, service_id)
        return {"evaluationId": evaluation_id, "logs": list(task.get("logs") or [])}

    @router.get("/api/image-generation/services/{service_id}/evaluations/{evaluation_id}/samples/{sample_id}/{kind}")
    async def image_generation_evaluation_sample(service_id: str, evaluation_id: str, sample_id: str, kind: str) -> FileResponse:
        task = get_evaluation(evaluation_id, service_id)
        if kind not in {"reference", "generated"}:
            raise HTTPException(status_code=404, detail="评测样本图片类型不存在。")
        sample = next((item for item in task.get("results") or [] if str(item.get("sampleId") or "") == sample_id), None)
        if kind == "reference":
            sample = next((item for item in task.get("samples") or [] if str(item.get("id") or "") == sample_id), None)
            candidate = Path(str((sample or {}).get("referencePath") or "")).expanduser().resolve()
            root = Path(str((task.get("datasetSnapshot") or {}).get("mediaRoot") or candidate.parent)).expanduser().resolve()
        else:
            candidate = Path(str((sample or {}).get("path") or "")).expanduser().resolve()
            root = (resolved_evaluation_root / evaluation_id).resolve()
        if sample is None or not candidate.is_file() or (candidate != root and root not in candidate.parents):
            raise HTTPException(status_code=404, detail="评测样本图片不存在。")
        return FileResponse(candidate, media_type="image/png", filename=candidate.name)

    @router.get("/api/image-generation/tasks/{task_id}/logs")
    async def image_generation_task_logs(task_id: str) -> dict[str, Any]:
        task = await get_image_generation_task(task_id)
        return {"taskId": task_id, "logs": list(task.get("logs") or [])}

    @router.get("/api/image-generation/tasks/{task_id}/diagnostics")
    async def image_generation_task_diagnostics(task_id: str) -> dict[str, Any]:
        task = await get_image_generation_task(task_id)
        failure_code = str(task.get("failureCode") or "").strip()
        failure_messages = {
            "instance_start_failed": "优云实例启动失败。",
            "runtime_unavailable": "优云实例运行环境不可用。",
            "model_unavailable": "优云基座模型不可用。",
            "adapter_unavailable": "训练完成的 Adapter 不可用。",
            "generation_failed": "远端文生图生成失败。",
            "result_download_failed": "生成结果下载失败。",
            "cleanup_failed": "优云实例清理失败。",
            "runtime_interrupted": "文生图任务在后端重启时中断，请重试。",
        }
        failure_message = failure_messages.get(failure_code, "文生图任务失败。") if failure_code else ""
        cleanup_state = str(task.get("cleanupState") or "").strip()
        cleanup_error = "优云实例清理失败，请重试清理。" if cleanup_state == "failed" else None
        failure = {"code": failure_code, "message": failure_message} if failure_code or failure_message else None
        cleanup = {"state": cleanup_state or None, "error": cleanup_error} if cleanup_state else None
        return {
            "taskId": task_id,
            "state": task.get("state"),
            "phase": task.get("phase") or task.get("state"),
            "progress": task.get("progress") or 0,
            "lastHeartbeatAt": task.get("lastHeartbeatAt"),
            "failure": failure,
            "cleanup": cleanup,
            "suggestedAction": task.get("suggestedAction"),
        }

    @router.get("/api/image-generation/tasks/{task_id}/results/{result_id}")
    async def image_generation_task_result(task_id: str, result_id: str) -> FileResponse:
        task = await get_image_generation_task(task_id)
        result = next((item for item in task.get("results") or [] if str(item.get("id")) == result_id), None)
        if result is None:
            raise HTTPException(status_code=404, detail="文生图结果不存在。")
        task_root = (resolved_result_root / task_id).resolve()
        candidate = Path(str(result.get("path") or task_root / Path(str(result.get("fileName") or "")).name)).expanduser().resolve()
        if candidate.parent != task_root or not candidate.is_file() or candidate.suffix.lower() != ".png":
            raise HTTPException(status_code=404, detail="文生图结果文件不存在。")
        return FileResponse(candidate, media_type="image/png", filename=candidate.name)

    return router
