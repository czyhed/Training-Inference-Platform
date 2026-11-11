from __future__ import annotations

import asyncio
from contextvars import ContextVar
import hashlib
import hmac
import inspect
import json
import logging
import math
import os
import posixpath
import random
import re
import shutil
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Literal, Mapping

from fastapi import FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from backend import personal_youyun
from backend.personal_youyun import scoped_resource
from backend.vision import (
    MAX_VISION_ARCHIVE_BYTES,
    safe_visual_member,
    validate_extracted_image_text_dataset,
    validate_visual_understanding_bundle,
)
from backend.image_generation_validation import normalize_image_dataset, validate_image_upload
from backend.image_generation_api import create_image_generation_router
from backend.image_generation_api import _diffusers_image, create_image_generation_router, select_image_generation_images
from backend.image_generation_runtime import ImageGenerationRuntime
from backend.image_generation_service_runtime import ImageGenerationServiceRuntime
from backend.shutdown_guard import install as install_shutdown_guard
from backend.image_generation_evaluation_runtime import ImageGenerationEvaluationRuntime
from backend.image_generation_evaluation_executor import FakeImageGenerationEvaluationExecutor
from backend.image_generation_training_api import create_image_generation_training_router
from backend.image_generation_executor import FakeImageGenerationTrainingExecutor
from backend.image_generation_training_runtime import ImageGenerationTrainingRuntime
from backend.image_generation_model_sync import sync_image_generation_adapters
from backend.vision_api import create_vision_router
from backend.speech_api import create_speech_router
from backend.training_artifacts import verified_local_training_artifacts
from backend.local_llamafactory_executor import LocalLlamaFactoryExecutor
from backend.vision_executor import LocalVisionTrainingExecutor
from backend.vision_runtime import VisionTrainingRuntime
from backend.youyun_vision_acceptance import YouyunVisionProbeRunner
from backend.youyun_image_generation_acceptance import YouyunImageGenerationProbeRunner
from backend.youyun_image_generation_probe import ImageGenerationProbeRequest
from backend.capabilities import (
    local_runtime_enabled,
    require_evaluation_enabled,
    require_local_runtime,
    require_provider,
    runtime_capabilities,
)
from backend.evaluation_store import EvaluationStore
from backend.inference_service import (
    InferenceRuntime,
    InferenceStore,
    create_inference_router,
)
from backend.model_storage import ModelArtifactUploader, ModelStorageConfig, adapter_is_complete, _md5
from backend.overview import build_overview_snapshot
from backend.platform_datasets import build_dataset_listing
from backend.platform_services import build_service_listing
from backend.platform_tasks import build_task_listing
from backend.providers.youyun.resource_health import youyun_resource_health
from backend.providers.youyun.models import (
    YouyunConfig,
    youyun_zone_capabilities,
    youyun_zone_default_mode,
    youyun_zone_modes,
    youyun_zone_supports_mode,
    merge_community_youyun_images,
    normalize_community_image_rows,
    rank_community_image_candidates,
    recommend_image_candidates,
    select_visual_inference_image,
    select_visual_training_image,
    youyun_machine_inventory_metadata,
)
from backend.local_llamafactory_executor import LocalLlamaFactoryExecutor
from backend.training_executor import TrainingEvent
from backend.postgres_storage import PostgresInferenceStore, PostgresTaskStore, close_pg_pools
from backend.cli_parser import CliValidationError, parse_cli_command, validate_extra_parameters
from backend.store import (
    NUM_EPOCHS_PATTERN,
    TOTAL_STEPS_PATTERN,
    TaskStore,
    infer_metric_step,
    parse_trainer_values,
    resolve_adapter_artifact,
    resolve_youyun_instance_artifact,
)
from backend.auth import (
    AUTH_COOKIE_NAME,
    OAUTH_STATE_COOKIE_NAME,
    OAUTH_VERIFIER_COOKIE_NAME,
    AuthStore,
    OAuthClientConfigStore,
    OAuthLoginError,
    SessionManager,
    create_pkce,
    exchange_code,
    fetch_userinfo,
)
from backend.governance import AuditStore, cleanup_runtime_logs
from backend.distributed import validate_single_gpu_training


logger = logging.getLogger(__name__)
WEBSOCKET_HEARTBEAT_INTERVAL_SECONDS = 15.0


PROJECT_ROOT = Path(__file__).resolve().parent.parent

_YOUYUN_IMAGES_CACHE: tuple[float, list[dict[str, Any]], str | None] | None = None
_YOUYUN_IMAGES_CACHE_TTL_SECONDS = 300.0
_YOUYUN_INFERENCE_IMAGES_CACHE: tuple[float, list[dict[str, Any]], str | None] | None = None
# Discovery metadata changes much less often than capacity. Keep it briefly so
# the initial catalog request, follow-up capacity request, and price validation
# share the same provider snapshot without hiding fresh inventory checks.
_YOUYUN_DISCOVERY_CACHE_TTL_SECONDS = 30.0
_YOUYUN_DISCOVERY_CACHE: dict[tuple[str, str, str], tuple[float, Any]] = {}
_YOUYUN_DISCOVERY_INFLIGHT: dict[tuple[str, str, str], asyncio.Task[Any]] = {}
_MODEL_ASSET_SYNC_TTL_SECONDS = 300.0
_MODEL_ASSET_SYNCED_AT = 0.0
_MODEL_ASSET_SYNCED_STORE_ID: int | None = None
_MODEL_ASSET_SYNC_LOCK = threading.Lock()


_YOUYUN_ZONE_DISPLAY_ORDER = {
    "cn-sh2-01": 0,  # 上海二A
    "cn-bj2-03": 1,  # 华北一C
    "cn-wlcb-03": 2,  # 华北二C
    "cn-wlcb-01": 3,  # 华北二A
    "cn-sh2-02": 4,  # 上海二B
}


def _youyun_cache_identity(client: Any) -> str | None:
    """Return a stable provider identity, or None for uncached lightweight fakes."""
    config = getattr(client, "config", None)
    api_base_url = str(getattr(config, "api_base_url", "") or "").strip()
    public_key = str(getattr(config, "public_key", "") or "").strip()
    if api_base_url or public_key:
        return f"{api_base_url}|{public_key}"
    # Lightweight test fakes and legacy adapters do not expose provider
    # credentials. Bypassing the process cache avoids cross-request fixture
    # contamination when Python reuses object ids.
    return None


async def _cached_youyun_discovery(
    client: Any,
    kind: str,
    scope: str,
    loader: Callable[[], Awaitable[Any]],
) -> Any:
    identity = _youyun_cache_identity(client)
    if identity is None:
        return await loader()
    key = (identity, kind, scope)
    now = time.monotonic()
    cached = _YOUYUN_DISCOVERY_CACHE.get(key)
    if cached is not None and now - cached[0] < _YOUYUN_DISCOVERY_CACHE_TTL_SECONDS:
        return cached[1]
    pending = _YOUYUN_DISCOVERY_INFLIGHT.get(key)
    if pending is None:
        pending = asyncio.create_task(loader())
        _YOUYUN_DISCOVERY_INFLIGHT[key] = pending
    try:
        value = await pending
        _YOUYUN_DISCOVERY_CACHE[key] = (time.monotonic(), value)
        return value
    finally:
        if _YOUYUN_DISCOVERY_INFLIGHT.get(key) is pending:
            _YOUYUN_DISCOVERY_INFLIGHT.pop(key, None)


async def _cached_youyun_support_zones(client: Any) -> list[dict[str, Any]]:
    return await _cached_youyun_discovery(
        client,
        "support-zones",
        "all",
        lambda: asyncio.to_thread(client.describe_support_zones),
    )


async def _cached_youyun_images(client: Any, region: str) -> list[dict[str, Any]]:
    async def load() -> list[dict[str, Any]]:
        describe_images = getattr(client, "describe_images", None)
        if not callable(describe_images):
            return []
        try:
            return await asyncio.to_thread(
                describe_images, region=region, use_config_zone=False
            )
        except TypeError:
            # Keep compatibility with lightweight provider fakes and older clients.
            return await asyncio.to_thread(describe_images)

    return await _cached_youyun_discovery(client, "images", region, load)


async def _cached_youyun_zone_images(client: Any, region: str, zone: str) -> list[dict[str, Any]]:
    """Cache platform-image discovery for one concrete placement zone."""
    async def load() -> list[dict[str, Any]]:
        describe_images = getattr(client, "describe_images", None)
        if not callable(describe_images):
            return []
        try:
            return await asyncio.to_thread(describe_images, region=region, zone=zone)
        except TypeError:
            return await asyncio.to_thread(describe_images)

    return await _cached_youyun_discovery(client, "zone-images", f"{region}|{zone}", load)


async def _cached_youyun_instance_types_for_zone(client: Any, region: str, zone: str) -> list[Any]:
    """Cache GPU catalog discovery without conflating it with capacity."""
    async def load() -> list[Any]:
        describe_instance_types = getattr(client, "describe_instance_types", None)
        if not callable(describe_instance_types):
            return []
        try:
            return await asyncio.to_thread(describe_instance_types, region=region, zone=zone)
        except TypeError:
            return await asyncio.to_thread(describe_instance_types)

    return await _cached_youyun_discovery(client, "zone-instance-types", f"{region}|{zone}", load)


async def _cached_youyun_community_images(
    client: Any,
    region: str,
    zone: str,
    keyword: str | None = None,
) -> list[dict[str, Any]]:
    """Coalesce community-image catalog calls across selectors."""
    async def load() -> list[dict[str, Any]]:
        describe_community_images = getattr(client, "describe_community_images", None)
        if not callable(describe_community_images):
            return []
        kwargs: dict[str, Any] = {"limit": 100}
        if keyword:
            kwargs["keyword"] = keyword
        try:
            return await asyncio.to_thread(
                describe_community_images,
                region=region,
                zone=zone,
                **kwargs,
            )
        except TypeError:
            return await asyncio.to_thread(describe_community_images, **kwargs)

    scope = "|".join((region, zone, keyword or "all"))
    return await _cached_youyun_discovery(client, "community-images", scope, load)


async def _cached_youyun_instance_types(client: Any, region: str) -> list[Any]:
    return await _cached_youyun_discovery(
        client,
        "instance-types",
        region,
        lambda: asyncio.to_thread(client.describe_instance_types, instance_type="uhost"),
    )


def _youyun_image_container(image: dict[str, Any]) -> bool | None:
    """Read the provider's Container flag without inferring it from ImageType.

    ``YouyunClient`` normalizes ``Container`` to ``container``. Accepting the
    provider spelling here as well keeps this boundary safe for raw fixtures
    and older callers, including ``ImageType=Other`` records whose instance
    form is not derivable from the image type alone.
    """
    value = image["container"] if "container" in image else image.get("Container")
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    return None


def _youyun_image_instance_mode(image: dict[str, Any]) -> Literal["pod", "uhost"]:
    """Describe image runtime metadata; placement mode comes from the Zone."""
    container = _youyun_image_container(image)
    if container is not None:
        return "pod" if container else "uhost"
    image_type = str(image.get("image_type") or image.get("ImageType") or "").strip().lower()
    return "uhost" if image_type in {"system", "other"} else "pod"


def _youyun_image_mode_label(image: dict[str, Any]) -> str:
    return "容器 Pod" if _youyun_image_instance_mode(image) == "pod" else "虚机 UHost"


def _youyun_image_source_label(image: dict[str, Any]) -> str:
    image_type = str(image.get("image_type") or "").strip().lower()
    return {
        "community": "社区镜像",
        "shared": "私有镜像/共享",
        "private": "私有镜像/共享",
        "other": "第三方镜像",
        "system": "平台系统镜像",
        "app": "平台应用镜像",
    }.get(image_type, "优云镜像")


def _youyun_image_is_selectable_for_training(image: dict[str, Any]) -> bool:
    """Keep container training images plus Ubuntu/third-party virtual images."""
    if _youyun_image_instance_mode(image) == "pod":
        return _is_training_image(image)
    text = " ".join(
        str(image.get(key) or "")
        for key in ("name", "description", "softwares")
    ).lower()
    return "windows" not in text


def _youyun_location_options(
    support_zones: list[dict[str, Any]],
    *,
    image_mode: Literal["pod", "uhost"] | None = None,
    image: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Expose provider Zones and disable entries incompatible with the image runtime."""
    resolved_mode = image_mode
    if resolved_mode is None and image is not None:
        container = _youyun_image_container(image)
        if container is not None:
            resolved_mode = "pod" if container else "uhost"
    options: list[dict[str, Any]] = []
    for raw in sorted(
        support_zones,
        key=lambda item: _YOUYUN_ZONE_DISPLAY_ORDER.get(str(item.get("zone") or ""), 100),
    ):
        region = str(raw.get("region") or "").strip()
        zone = str(raw.get("zone") or "").strip()
        if not region or not zone:
            continue
        modes = list(youyun_zone_modes(raw))
        default_mode = youyun_zone_default_mode(raw, resolved_mode)
        supported = resolved_mode is None or resolved_mode in modes
        options.append({
            "region": region,
            "zone": zone,
            "actualZone": zone,
            "displayName": str(raw.get("describe") or zone),
            "describe": str(raw.get("describe") or ""),
            # Kept for old clients. New callers should use supportedModes and
            # choose the requested image mode when a zone supports both.
            "isPod": default_mode == "pod",
            "supportedModes": modes,
            "capabilities": youyun_zone_capabilities(raw),
            "supported": supported,
            "disabledReason": None if supported else (
                "当前容器镜像仅支持 Pod 可用区" if resolved_mode == "pod"
                else "当前虚机镜像仅支持 UHost 可用区"
            ),
            "unsupportedImageTypes": list(raw.get("unsupported_image_types") or []),
        })
    return options


def _find_location_option(options: list[dict[str, Any]], zone: str | None) -> dict[str, Any] | None:
    if not zone:
        return None
    return next((item for item in options if item.get("zone") == zone or item.get("actualZone") == zone), None)


def _normalize_youyun_region_zone(
    region: str | None,
    zone: str | None,
    support_zones: list[dict[str, Any]],
) -> tuple[str, str | None]:
    """Resolve region/zone inputs that may arrive swapped or zone-shaped.

    The frontend refresh chain caches one ``gpu-specs`` response per parameter
    tuple, so a stale module state can submit a zone code such as
    ``cn-bj2-03`` in the ``region`` field. The provider table is the source of
    truth: when either input matches a known zone code, derive the region from
    the zone record instead of failing with a 422 that the form cannot recover
    from without a manual reload.
    """
    region = (region or "").strip()
    zone = (zone or "").strip() or None

    def region_of(candidate: str) -> str | None:
        return next(
            (
                str(item.get("region") or "").strip()
                for item in support_zones
                if str(item.get("zone") or "").strip() == candidate
            ),
            None,
        )

    # The zone wins: it names the concrete placement, so when both inputs are
    # zone codes the region must follow the zone's provider record.
    zone_region = region_of(zone) if zone else None
    region_region = next(
        (
            str(item.get("region") or "").strip()
            for item in support_zones
            if str(item.get("region") or "").strip() == region
        ),
        None,
    )
    if zone_region:
        region = zone_region
    elif region and region_region is None:
        # region was a zone code and zone is absent/unknown: derive from it.
        derived = region_of(region)
        if derived:
            region = derived
    if zone and not any(str(item.get("zone") or "").strip() == zone for item in support_zones):
        derived = region_of(zone)
        if derived:
            zone = next(
                str(item.get("zone") or "").strip()
                for item in support_zones
                if str(item.get("region") or "").strip() == derived
                and str(item.get("zone") or "").strip()
            )
    return region, zone


def _is_training_image(image: dict[str, Any]) -> bool:
    """Keep only official CUDA/PyTorch/vLLM images useful for remote fine-tuning."""
    haystack = " ".join(
        str(image.get(key) or "")
        for key in ("name", "image_type", "description", "tags")
    ).lower()
    excluded = ("windows", "comfyui", "ollama", "sglang", "sd-webui", "stable diffusion")
    if any(term in haystack for term in excluded):
        return False
    return any(term in haystack for term in ("cuda", "pytorch", "torch", "vllm", "llama-factory", "llamafactory"))


def _is_official_vllm_image(image: dict[str, Any]) -> bool:
    """Accept only provider-managed official images that include vLLM."""
    image_type = str(image.get("image_type") or "").strip().lower()
    if image_type in {"community", "shared", "private", "custom", "market"}:
        return False
    haystack = " ".join(
        str(image.get(key) or "")
        for key in ("name", "description", "tags", "source")
    ).lower()
    if any(term in haystack for term in ("community", "shared", "private")):
        return False
    return "vllm" in haystack and image_type in {"", "system", "app", "official", "public", "base"}


def _youyun_image_version(image: dict[str, Any]) -> str | None:
    """Return only an explicit provider version, never a guessed CUDA version."""
    for tag in image.get("tags") or []:
        value = str(tag or "").strip()
        if re.fullmatch(r"v?\d+\.\d+(?:\.\d+)?(?:[-+][\w.-]+)?", value, re.IGNORECASE):
            return value
    name = str(image.get("name") or "")
    match = re.search(r"vllm[^\d]{0,12}v(\d+\.\d+(?:\.\d+)?)", name, re.IGNORECASE)
    return match.group(1) if match else None


def _version_sort_key(version: str | None) -> tuple[int, tuple[int, ...]]:
    """Order provider versions newest-first; images without a version sort last.

    The provider never returns a structured version, so the only reliable input
    is the string `_youyun_image_version` extracted from a tag or the name.
    """
    if not version:
        return (1, ())
    numbers = re.findall(r"\d+", version)
    if not numbers:
        return (1, ())
    # Negate so a plain ascending sort puts the newest version first.
    return (0, tuple(-int(part) for part in numbers[:4]))


def _is_rtx5090_only_image(image: dict[str, Any]) -> bool:
    """A provider image naming 5090 targets that card's architecture only."""
    haystack = " ".join(
        str(image.get(key) or "") for key in ("name", "description", "tags")
    ).replace(" ", "")
    return "5090" in haystack


def _training_image_priority(image: dict[str, Any], default_name: str) -> tuple[int, int, str]:
    haystack = " ".join(str(image.get(key) or "") for key in ("name", "tags")).lower()
    default_tokens = [token for token in re.split(r"[^a-z0-9]+", default_name.lower()) if token]
    preferred = 0 if default_tokens and all(token in haystack for token in default_tokens) else 1
    # CUDA/PyTorch is the current remote training baseline; vLLM remains selectable.
    family = 0 if any(term in haystack for term in ("cuda", "pytorch", "torch")) else 1
    return preferred, family, str(image.get("name") or image.get("id") or "")


def env_or_default(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _load_project_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def resolve_storage_backend(configured: str, postgres_dsn: str) -> str:
    backend = configured.strip().lower()
    if not backend:
        backend = "postgres" if postgres_dsn.strip() else "sqlite"
    if backend not in {"sqlite", "postgres"}:
        raise RuntimeError("XUNTUI_STORAGE_BACKEND 只能是 sqlite 或 postgres。")
    return backend


_load_project_env(PROJECT_ROOT / ".env")
STORAGE_BACKEND = resolve_storage_backend(
    os.environ.get("XUNTUI_STORAGE_BACKEND", ""),
    os.environ.get("POSTGRES_DSN", ""),
)

RUNTIME_ROOT = Path(
    os.environ.get("XUNTUI_RUNTIME_ROOT", "/root/xuntui-platform/runtime")
)
ARTIFACT_ROOT = Path(os.environ.get("XUNTUI_ARTIFACT_ROOT", "/root/xuntui-platform/artifacts"))
DATASET_ROOT = Path(os.environ.get("XUNTUI_DATASET_ROOT", "/root/xuntui-platform/datasets"))
MODEL_CACHE_ROOT = Path(
    os.environ.get("XUNTUI_MODELSCOPE_CACHE", "/root/.cache/modelscope")
) / "models"
CORS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "XUNTUI_CORS_ORIGINS",
        "http://127.0.0.1:3000,http://localhost:3000,http://127.0.0.1:3001,http://localhost:3001,http://127.0.0.1:3100,http://localhost:3100,http://127.0.0.1:4000,http://localhost:4000",
    ).split(",")
    if origin.strip()
]
DEFAULT_PUBLIC_HOST = "http://203.0.113.11"
DEFAULT_APP_GATEWAY_URL = f"{DEFAULT_PUBLIC_HOST}:13101/"
CONTROL_CENTER_URL = env_or_default("CONTROL_CENTER_URL", DEFAULT_PUBLIC_HOST).rstrip("/")
OAUTH_AUTHORIZE_URL = env_or_default(
    "OAUTH_AUTHORIZE_URL",
    env_or_default("CPPT_OAUTH_AUTHORIZE_URL", f"{CONTROL_CENTER_URL}/api/oauth/authorize"),
)
OAUTH_TOKEN_URL = env_or_default(
    "OAUTH_TOKEN_URL",
    env_or_default("CPPT_OAUTH_TOKEN_URL", f"{CONTROL_CENTER_URL}/api/oauth/token"),
)
OAUTH_USERINFO_URL = env_or_default(
    "OAUTH_USERINFO_URL",
    env_or_default("CPPT_OAUTH_USERINFO_URL", f"{CONTROL_CENTER_URL}/api/oauth/userinfo"),
)
OAUTH_CLIENT_KEY = env_or_default(
    "OAUTH_CLIENT_KEY", env_or_default("CPPT_OAUTH_CLIENT_ID", "cppt-platform")
)
OAUTH_APPS_DSN = env_or_default(
    "OAUTH_APPS_DSN", env_or_default("CONTROL_CENTER_POSTGRES_DSN", "")
)
OAUTH_FALLBACK_REDIRECT_URI = env_or_default(
    "OAUTH_REDIRECT_URI",
    env_or_default("CPPT_OAUTH_REDIRECT_URI", DEFAULT_APP_GATEWAY_URL),
)
FRONTEND_URL = env_or_default("CPPT_FRONTEND_URL", DEFAULT_APP_GATEWAY_URL).rstrip("/")
OAUTH_TIMEOUT = float(
    os.environ.get("OAUTH_HTTP_TIMEOUT_SECONDS", os.environ.get("CPPT_OAUTH_TIMEOUT", "15"))
)
OAUTH_STATE_TTL = max(60, int(os.environ.get("OAUTH_STATE_TTL_SECONDS", "600")))
SESSION_TTL = int(
    os.environ.get("OAUTH_SESSION_TTL_SECONDS", os.environ.get("CPPT_SESSION_TTL", "3600"))
)
SESSION_SECRET = os.environ.get(
    "OAUTH_SESSION_SECRET", os.environ.get("CPPT_SESSION_SECRET", "")
)
SESSION_SECURE = os.environ.get(
    "OAUTH_COOKIE_SECURE", os.environ.get("CPPT_SESSION_SECURE", "false")
).strip().lower() in {"1", "true", "yes"}
AUTH_MODE = os.environ.get("XUNTUI_AUTH_MODE", "required").strip().lower()
if AUTH_MODE not in {"disabled", "required"}:
    raise RuntimeError("XUNTUI_AUTH_MODE must be 'disabled' or 'required'.")
DATABASE_PATH = Path(
    os.environ.get("XUNTUI_DATABASE_PATH", str(RUNTIME_ROOT / "training.db"))
)
TRAINER_PATH = Path(__file__).resolve().parent / "train_sft.py"
CHAT_PATH = Path(__file__).resolve().parent / "chat_inference.py"
YOUYUN_RETENTION_HOURS = max(1, int(os.environ.get("XUNTUI_YOUYUN_RETENTION_HOURS", "6")))
YOUYUN_RETENTION_CHECK_MINUTES = max(
    1, int(os.environ.get("XUNTUI_YOUYUN_RETENTION_CHECK_MINUTES", "5"))
)
# 对比请求整体预算：必须小于前端代理/网关的断开阈值，超时返回可重试的 503。
REMOTE_CHAT_WAIT_SECONDS = 25
BUILTIN_DATASETS = {
    "qwenruozhi_v1": {
        "id": "qwenruozhi_v1",
        "name": "弱智吧语料 SFT v1",
        "trainPath": str(PROJECT_ROOT / "data" / "qwenruozhi_v1" / "train.jsonl"),
        "validationPath": str(PROJECT_ROOT / "data" / "qwenruozhi_v1" / "val.jsonl"),
        "testPath": str(PROJECT_ROOT / "data" / "qwenruozhi_v1" / "test.jsonl"),
        "challengePath": str(PROJECT_ROOT / "data" / "qwenruozhi_v1" / "challenge.jsonl"),
        "sampleCount": 1196,
        "type": "文本生成",
        "structure": "SFT",
        "state": "published",
        "validationMessage": "内置数据集已校验，可用于本地训练。",
        "owner": "平台管理员",
        "builtin": True,
    }
}

_catalog_store = TaskStore(DATABASE_PATH)
store = (
    PostgresTaskStore(os.environ.get("POSTGRES_DSN", ""), _catalog_store)
    if STORAGE_BACKEND == "postgres"
    else _catalog_store
)
auth_store = AuthStore(STORAGE_BACKEND, DATABASE_PATH, os.environ.get("POSTGRES_DSN", ""))
evaluation_store = EvaluationStore(STORAGE_BACKEND, DATABASE_PATH, os.environ.get("POSTGRES_DSN", ""))
auth_config_store = OAuthClientConfigStore(
    OAUTH_CLIENT_KEY, OAUTH_APPS_DSN, OAUTH_FALLBACK_REDIRECT_URI
)
session_manager = SessionManager(
    SESSION_SECRET or secrets.token_urlsafe(32), SESSION_TTL
)
for builtin_dataset in BUILTIN_DATASETS.values():
    store.upsert_dataset(
        {
            **builtin_dataset,
            "paths": {
                "trainPath": builtin_dataset["trainPath"],
                "validationPath": builtin_dataset["validationPath"],
                "testPath": builtin_dataset["testPath"],
                "challengePath": builtin_dataset["challengePath"],
            },
        }
    )
worker_task: asyncio.Task[None] | None = None
active_process: asyncio.subprocess.Process | None = None
active_task_id: str | None = None
remote_training_tasks: dict[str, asyncio.Task[None]] = {}
artifact_sync_tasks: dict[str, asyncio.Task[None]] = {}
local_executor = LocalLlamaFactoryExecutor(
    work_root=Path(os.environ.get("XUNTUI_TRAINING_WORKDIR", str(RUNTIME_ROOT / "training-work"))),
    command=os.environ.get("XUNTUI_LLAMA_FACTORY_COMMAND", "llamafactory-cli"),
    python_executable=os.environ.get("XUNTUI_TRAINING_PYTHON") or None,
)
# 优云远程任务的本地镜像执行器：prepare 在镜像目录生成数据集副本与 YAML，
# collect_artifacts 在产物下载回镜像后复用本地收集逻辑。
youyun_mirror_root = Path(os.environ.get("XUNTUI_REMOTE_MIRROR_ROOT", str(RUNTIME_ROOT / "remote-jobs"))).resolve()
youyun_mirror_executor = LocalLlamaFactoryExecutor(
    work_root=youyun_mirror_root,
    command=os.environ.get("XUNTUI_LLAMA_FACTORY_COMMAND", "llamafactory-cli"),
    python_executable=os.environ.get("XUNTUI_TRAINING_PYTHON") or None,
)


def build_youyun_runner(
    *,
    region: str | None = None,
    zone: str | None = None,
    charge_type: str | None = None,
    sink: Any | None = None,
    prepare_local: Any | None = None,
    collect_local: Any | None = None,
    mirror_root: Path | None = None,
    retain_for_comparison: bool = True,
):
    """构造优云远程训练编排器；配置缺失时抛错（不缓存，便于运行中修复配置）。"""
    from dataclasses import replace as dc_replace

    from backend.providers.youyun.client import YouyunClient
    from backend.providers.youyun.instance_manager import YouyunInstanceManager
    from backend.providers.youyun.models import YouyunConfig
    from backend.providers.youyun.remote_training import YouyunTrainingRunner

    base_config = YouyunConfig.from_env().with_overrides(
        region=region,
        zone=zone,
        charge_type=charge_type,
    )
    client = YouyunClient(config=base_config)
    manager = YouyunInstanceManager(client)
    return YouyunTrainingRunner(
        manager=manager,
        sink=sink or store,
        mirror_root=mirror_root or youyun_mirror_root,
        remote_root=os.environ.get("XUNTUI_REMOTE_ROOT", "/root/cppt"),
        prepare_local=prepare_local or youyun_mirror_executor.prepare,
        collect_local=collect_local or youyun_mirror_executor.collect_artifacts,
        on_retained=_warmup_retained_comparison,
        retain_for_comparison=retain_for_comparison,
    )


def _replace_youyun_instance_marker(
    task: dict[str, Any], state: str, *, error: str | None = None,
    extra: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for item in task.get("artifacts") or []:
        if item.get("type") != "youyun-instance":
            artifacts.append(item)
            continue
        marker = {**item, "state": state}
        if error:
            marker["cleanupError"] = error
        else:
            marker.pop("cleanupError", None)
        if extra:
            marker.update(extra)
        artifacts.append(marker)
    return artifacts


@scoped_resource("task_id")
async def cleanup_youyun_task_instance(
    task_id: str, *, reason: str
) -> dict[str, Any]:
    task = store.get(task_id)
    if task is None:
        raise KeyError("任务不存在。")
    marker = resolve_youyun_instance_artifact(task.get("artifacts") or [])
    if marker is None:
        raise ValueError("任务没有可释放的优云实例记录。")
    if marker.get("state") == "released":
        # 存量数据自愈：artifact 已释放但任务级字段未同步时，顺手矫正，
        # 让监控/对账不再把已释放任务误报为占用中。
        if str(task.get("resourceState") or "") not in {"released", "none"} or str(task.get("cleanupState") or "") != "released":
            store.update(
                task_id,
                resource_state="released",
                cleanup_state="released",
            )
            task = store.get(task_id) or task
        return task
    instance_id = str(marker["instanceId"])
    close_remote_chat_handle(task_id)
    store.update(
        task_id,
        artifacts=_replace_youyun_instance_marker(task, "releasing"),
    )
    store.append_log(task_id, f"开始{reason}优云实例 {instance_id}。")
    try:
        resource = dict(task.get("youyunResource") or {})
        runner = build_youyun_runner(region=resource.get("region"), zone=resource.get("zone"))
        report = await asyncio.to_thread(
            runner.manager.cleanup_after_task, instance_id, stop_first=False
        )
    except Exception as exc:  # noqa: BLE001 - 持久化失败结果供人工重试
        report = {"terminated": False, "error": str(exc)}
    if not report.get("terminated"):
        error = str(report.get("error") or "实例删除未确认")
        current = store.get(task_id) or task
        store.update(
            task_id,
            artifacts=_replace_youyun_instance_marker(
                current, "cleanup_failed", error=error
            ),
            resource_state="cleanup_failed",
            cleanup_state="failed",
            cleanup_error=error,
        )
        store.append_log(
            task_id,
            f"优云实例 {instance_id} 释放失败：{error}（可再次点击销毁实例重试）。",
        )
        raise RuntimeError(error)
    current = store.get(task_id) or task
    store.update(
        task_id,
        artifacts=_replace_youyun_instance_marker(current, "released"),
        resource_state="released",
        cleanup_state="released",
        cleanup_checked_at=report.get("checkedAt"),
        current_step="任务已结束，优云实例已释放",
    )
    store.append_log(task_id, f"优云实例 {instance_id} 已{reason}。")
    return store.get(task_id) or task


async def reconcile_interrupted_youyun_instances() -> None:
    """后端重启后回收未进入人工保留态的远程训练实例。"""
    for task in store.list(include_details=False):
        marker = resolve_youyun_instance_artifact(task.get("artifacts") or [])
        if marker is None or marker.get("state") not in {
            "running",
            "releasing",
            "cleanup_failed",
        }:
            continue
        if task.get("state") not in {"failed", "stopped"}:
            continue
        try:
            await cleanup_youyun_task_instance(task["id"], reason="重启恢复清理")
        except (RuntimeError, ValueError, KeyError):
            continue


_remote_chat_manager: Any = None
_remote_chat_managers: dict[str, Any] = {}


def get_remote_chat_manager():
    """优云对比会话管理器单例；handle 缓存在内存中，必须跨请求复用。"""
    if _remote_chat_manager is not None:
        return _remote_chat_manager
    identity = personal_youyun.current_resource.get() or personal_youyun.current_user.get() or "legacy"
    if identity not in _remote_chat_managers:
        from backend.providers.youyun.remote_chat import RemoteComparisonManager

        runner = build_youyun_runner()
        _remote_chat_managers[identity] = RemoteComparisonManager(
            manager=runner.manager,
            remote_root=runner.remote_root,
            ssh_ready_timeout=runner.ssh_ready_timeout,
            ssh_connect_timeout=runner.ssh_connect_timeout,
        )
    return _remote_chat_managers[identity]


def close_remote_chat_handle(task_id: str) -> None:
    """释放实例前后关闭该任务的远程对比会话。"""
    manager = _remote_chat_managers.pop(task_id, None) or _remote_chat_manager
    if manager is not None:
        try:
            manager.close_handle(task_id)
        except Exception:  # noqa: BLE001 - 会话清理失败不阻断实例释放
            pass


def touch_youyun_retention(task_id: str) -> None:
    """对比成功后续期保留窗口，避免对比中途被自动释放。"""
    try:
        task = store.get(task_id)
        if task is None:
            return
        marker = resolve_youyun_instance_artifact(task.get("artifacts") or [])
        if marker is None or marker.get("state") != "awaiting_comparison":
            return
        from datetime import datetime, timezone

        store.update(
            task_id,
            artifacts=_replace_youyun_instance_marker(
                task,
                "awaiting_comparison",
                extra={"retainedAt": datetime.now(timezone.utc).isoformat()},
            ),
        )
    except Exception:  # noqa: BLE001 - 续期失败不影响本次对比结果
        pass


@scoped_resource("task_id")
async def _warmup_retained_comparison(task_id: str, task: dict[str, Any]) -> None:
    """实例保留期间预建远程对比会话，让首次对比点击无需等待模型加载。"""
    # on_retained 传入的是训练启动时的任务快照，此时 Adapter 产物尚未写入；
    # 必须以 store 中的最新任务为准，否则预热会被静默跳过。
    task = store.get(task_id) or task
    adapter = resolve_adapter_artifact(task.get("artifacts") or [], "selected")
    if adapter is None:
        store.append_log(task_id, "优云对比模型预热跳过：任务没有可用 Adapter 产物。")
        return
    try:
        ready = await get_remote_chat_manager().warmup(
            task,
            str(adapter.get("remotePath") or adapter["path"]),
            load_in_4bit=task.get("tuningMode") == "qlora",
        )
    except Exception as exc:  # noqa: BLE001 - 预热失败降级为按需加载
        store.append_log(task_id, f"优云对比模型预热失败：{exc}")
        return
    if ready:
        store.append_log(task_id, "优云 GPU 对比模型已就绪（实例保留期间保持加载）。")


async def warmup_retained_comparisons() -> None:
    """backend 启动时对所有保留中的实例预热对比会话（重启自愈）。"""
    for task in store.list(include_details=False):
        if task.get("state") not in {"completed", "stopped"}:
            continue
        marker = resolve_youyun_instance_artifact(task.get("artifacts") or [])
        if marker is None or marker.get("state") != "awaiting_comparison":
            continue
        try:
            asyncio.create_task(_warmup_retained_comparison(task["id"], task))
        except Exception:  # noqa: BLE001 - 单个任务预热失败不影响其余
            continue


async def auto_release_expired_youyun_instances() -> int:
    """释放超过保留时长的 awaiting_comparison 实例，返回本次释放数量。"""
    from datetime import datetime, timezone

    released = 0
    for task in store.list(include_details=False):
        if task.get("state") not in {"completed", "stopped"}:
            continue
        marker = resolve_youyun_instance_artifact(task.get("artifacts") or [])
        if marker is None or marker.get("state") != "awaiting_comparison":
            continue
        retained_at = str(marker.get("retainedAt") or "").strip()
        if not retained_at:
            # 历史任务没有保留时间戳：补盖当前时间，从本版本部署起算一个保留窗口。
            store.update(
                task["id"],
                artifacts=_replace_youyun_instance_marker(
                    task,
                    "awaiting_comparison",
                    extra={"retainedAt": datetime.now(timezone.utc).isoformat()},
                ),
            )
            continue
        try:
            retained_at_value = datetime.fromisoformat(retained_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if retained_at_value.tzinfo is None:
            retained_at_value = retained_at_value.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - retained_at_value).total_seconds() / 3600
        if age_hours < YOUYUN_RETENTION_HOURS:
            continue
        try:
            await cleanup_youyun_task_instance(
                task["id"], reason="超过保留时长，自动释放"
            )
            released += 1
        except (RuntimeError, ValueError, KeyError):
            continue
    return released


chat_process: asyncio.subprocess.Process | None = None
chat_process_key: str | None = None
chat_lock = asyncio.Lock()
inference_store = (PostgresInferenceStore(os.environ.get("POSTGRES_DSN", "")) if STORAGE_BACKEND == "postgres" else InferenceStore(DATABASE_PATH))
audit_store = AuditStore(STORAGE_BACKEND, DATABASE_PATH, os.environ.get("POSTGRES_DSN", ""))
inference_runtime = InferenceRuntime(
    inference_store,
    store,
    port=int(os.environ.get("XUNTUI_INFERENCE_PORT", "8011")),
    training_busy=lambda: active_task_id is not None or vision_training_runtime.active_task_id is not None or image_generation_runtime.active_task_id is not None,
    youyun_resource_resolver=lambda resource: resolve_youyun_resource(
        YouyunResourceSelection.model_validate(resource)
    ),
    artifact_restorer=lambda task_id, variant: require_model_uploader().restore_adapter(
        task_id, variant, ARTIFACT_ROOT / "minio-cache"
    ),
)

def resolve_shared_storage_enabled(configured: str) -> bool:
    value = configured.strip().lower()
    if not value:
        return True
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError("XUNTUI_SHARED_STORAGE_ENABLED 必须是 true 或 false。")

# ==== fusion: 视觉/文生图运行时与优云看护 ====
training_execution_lock = asyncio.Lock()
community_image_verifications: dict[str, dict[str, Any]] = {}
community_image_ids: set[str] = set()
def _community_verification_key(
    purpose: str,
    image_id: str,
    region: str = "",
    zone: str = "",
    gpu_type: str = "",
) -> str:
    # Preserve the historical visual-training key while isolating all new
    # probes by workload and concrete resource location.
    if purpose == "vision_training":
        return image_id
    return "|".join((purpose, image_id, region, zone, gpu_type.casefold()))
def community_image_verification(
    purpose: str,
    image_id: str,
    region: str = "",
    zone: str = "",
    gpu_type: str = "",
) -> dict[str, Any]:
    record = community_image_verifications.get(
        _community_verification_key(purpose, image_id, region, zone, gpu_type), {}
    )
    if record.get("state") == "verified" and float(record.get("expiresAtEpoch") or 0) <= time.time():
        return {**record, "state": "expired"}
    return record


def _expired_remote_task(task: Mapping[str, Any], now: float | None = None) -> bool:
    """Return true only for remote tasks with an explicit, elapsed budget."""
    if str(task.get("provider") or "") != "youyun":
        return False
    if str(task.get("cleanupState") or "") in {"released", "failed"}:
        return False
    if str(task.get("state") or "") not in {"queued", "preparing", "provisioning", "running", "collecting", "stopping"}:
        return False
    try:
        expires = float(task.get("expiresAtEpoch") or 0)
    except (TypeError, ValueError):
        return False
    return expires > 0 and expires <= (time.time() if now is None else now)
def _stalled_remote_task(
    task: Mapping[str, Any],
    *,
    now: float | None = None,
    timeout: float = 300.0,
) -> bool:
    """Return true when an active remote task stopped heartbeating."""
    if str(task.get("provider") or "") != "youyun":
        return False
    if str(task.get("cleanupState") or "") in {"released", "failed"}:
        return False
    if str(task.get("state") or "") not in {"preparing", "provisioning", "running", "collecting", "stopping"}:
        return False
    try:
        heartbeat = float(task.get("lastHeartbeatAtEpoch") or 0)
        budget = max(1.0, float(timeout))
    except (TypeError, ValueError):
        return False
    if heartbeat <= 0:
        return False
    return (time.time() if now is None else now) - heartbeat >= budget
def _cleanup_retry_due(task: Mapping[str, Any], now: float | None = None) -> bool:
    """Return true when a failed remote cleanup is ready for another attempt."""
    if str(task.get("provider") or "") != "youyun":
        return False
    if str(task.get("cleanupState") or "") != "failed":
        return False
    if not (task.get("remoteHandle") or task.get("instanceId") or resolve_youyun_instance_artifact(task.get("artifacts") or [])):
        return False
    try:
        retry_at = float(task.get("cleanupNextRetryAtEpoch") or 0)
    except (TypeError, ValueError):
        return False
    return retry_at <= (time.time() if now is None else now)
def _schedule_cleanup_retry(runtime: Any, task_id: str, task: Mapping[str, Any], *, error: str | None = None) -> None:
    """Persist bounded cleanup retry metadata without reopening the training task."""
    try:
        attempt = max(0, int(task.get("cleanupRetryCount") or 0)) + 1
    except (TypeError, ValueError):
        attempt = 1
    base = max(30.0, float(os.environ.get("XUNTUI_YOUYUN_CLEANUP_RETRY_SECONDS", "120")))
    maximum = max(base, float(os.environ.get("XUNTUI_YOUYUN_CLEANUP_RETRY_MAX_SECONDS", "1800")))
    delay = min(maximum, base * (2 ** min(attempt - 1, 5)))
    changes: dict[str, Any] = {
        "cleanupRetryCount": attempt,
        "cleanupNextRetryAtEpoch": time.time() + delay,
    }
    if error:
        changes["cleanupError"] = str(error)[:8000]
    update = getattr(runtime, "update", None)
    if callable(update):
        update(task_id, **changes)
def _youyun_task_instance_ids(tasks: Iterable[Mapping[str, Any]]) -> set[str]:
    """Collect instance handles still referenced by task state."""
    active: set[str] = set()
    for task in tasks:
        if str(task.get("provider") or "") != "youyun":
            continue
        remote = task.get("remoteHandle")
        if isinstance(remote, Mapping) and remote.get("instanceId"):
            active.add(str(remote["instanceId"]))
        marker = resolve_youyun_instance_artifact(task.get("artifacts") or [])
        if marker and marker.get("state") != "released" and marker.get("instanceId"):
            active.add(str(marker["instanceId"]))
        if task.get("instanceId"):
            active.add(str(task["instanceId"]))
    return active
async def _reconcile_orphaned_youyun_instances(
    runtimes: Iterable[Any],
    service_store: Any | None = None,
) -> list[dict[str, Any]]:
    """Best-effort scan for old platform-owned instances with no task handle."""
    all_tasks: list[Mapping[str, Any]] = []
    for runtime in runtimes:
        try:
            tasks = runtime.list_tasks(include_archived=True)
        except TypeError:
            try:
                tasks = runtime.list_tasks()
            except Exception:
                continue
        except Exception:
            continue
        all_tasks.extend(item for item in tasks if isinstance(item, Mapping))
    active_ids = _youyun_task_instance_ids(all_tasks)
    if service_store is not None:
        # Running image-generation services own long-lived instances; their
        # persisted handles must count as active so they are never orphan-cleaned.
        try:
            service_rows = service_store.list(include_archived=True)
        except Exception:
            service_rows = []
        for service in service_rows:
            if not isinstance(service, Mapping):
                continue
            config = service.get("config") or {}
            if config.get("workloadType") != "image_generation":
                continue
            remote = config.get("remoteRuntime")
            if isinstance(remote, Mapping) and remote.get("instanceId"):
                active_ids.add(str(remote["instanceId"]))
    try:
        manager = build_youyun_runner().manager
    except Exception as exc:  # missing credentials/config: no destructive action
        logger.debug("优云孤立实例巡检跳过：%s", exc)
        return []
    grace = max(60.0, float(os.environ.get("XUNTUI_YOUYUN_ORPHAN_GRACE_SECONDS", "900")))
    try:
        reports = await asyncio.to_thread(
            manager.cleanup_orphaned_instances,
            active_instance_ids=active_ids,
            grace_seconds=grace,
        )
    except Exception as exc:  # provider outage is retried on the next watchdog tick
        logger.warning("优云孤立实例巡检失败：%s", exc)
        return []
    if reports:
        logger.info("优云孤立实例巡检完成：处理 %s 台实例。", len(reports))
    return reports
async def youyun_cost_watchdog() -> None:
    """Stop and release expired remote tasks without touching manual hold states."""
    interval = max(15.0, float(os.environ.get("XUNTUI_YOUYUN_WATCHDOG_INTERVAL", "60")))
    heartbeat_timeout = max(60.0, float(os.environ.get("XUNTUI_YOUYUN_HEARTBEAT_TIMEOUT_SECONDS", "300")))
    runtimes = (vision_training_runtime, image_generation_runtime, image_generation_training_runtime, image_generation_evaluation_runtime)
    while True:
        await _reconcile_orphaned_youyun_instances(runtimes, service_store=inference_store)
        for runtime in runtimes:
            try:
                tasks = runtime.list_tasks(include_archived=True)
            except TypeError:
                tasks = runtime.list_tasks()
            except Exception:
                continue
            for task in tasks:
                expired = _expired_remote_task(task)
                stalled = _stalled_remote_task(task, timeout=heartbeat_timeout)
                cleanup_retry = _cleanup_retry_due(task)
                if not expired and not stalled and not cleanup_retry:
                    continue
                task_id = str(task.get("id") or "")
                try:
                    reason = "任务超时" if expired else ("任务心跳超时" if stalled else "清理失败重试")
                    request_stop = getattr(runtime, "request_stop", None)
                    if not cleanup_retry and callable(request_stop) and str(task.get("state") or "") in {"queued", "preparing", "running", "collecting"}:
                        result = request_stop(task_id)
                        if inspect.isawaitable(result):
                            await result
                    force_cleanup = getattr(runtime, "force_cleanup_remote_task", None)
                    if callable(force_cleanup):
                        failure_code = "remote_task_timeout" if expired else ("remote_task_stalled" if stalled else None)
                        failure_message = "优云远程任务超时，实例已自动释放。" if expired else ("优云远程任务心跳超时，实例已自动释放。" if stalled else None)
                        result = force_cleanup(
                            task_id,
                            reason=f"{reason}自动清理",
                            failure_code=failure_code,
                            failure_message=failure_message,
                            suggested_action="检查阶段日志、镜像和远程进程后重试。",
                        )
                        if inspect.isawaitable(result):
                            await result
                        latest = runtime.get(task_id) if callable(getattr(runtime, "get", None)) else task
                        if isinstance(latest, Mapping) and str(latest.get("cleanupState") or "") == "failed":
                            _schedule_cleanup_retry(runtime, task_id, latest, error=str(latest.get("cleanupError") or ""))
                        continue
                    remote = task.get("remoteHandle")
                    provider = None
                    instance_id = ""
                    if isinstance(remote, Mapping):
                        provider = runtime.provider_factory(runtime) if hasattr(runtime, "provider_factory") else None
                    cleanup = getattr(provider, "cleanup_persisted", None) if provider is not None else None
                    if not callable(cleanup) and isinstance(remote, Mapping) and callable(getattr(runtime, "executor_factory", None)):
                        # Image-generation training/evaluation runtimes expose the
                        # Youyun manager through their executor rather than a
                        # provider_factory. Reuse that manager for watchdog cleanup.
                        executor = runtime.executor_factory(runtime)
                        manager = getattr(executor, "manager", None)
                        cleanup = getattr(manager, "cleanup_after_task", None)
                        if callable(cleanup):
                            instance_id = str(remote.get("instanceId") or "")

                    if callable(cleanup) and isinstance(remote, Mapping):
                        if instance_id:
                            result = cleanup(instance_id, stop_first=False)
                        else:
                            result = cleanup(dict(remote))
                        if inspect.isawaitable(result):
                            result = await result
                        if isinstance(result, Mapping) and result.get("terminated"):
                            runtime.update(task_id, cleanupState="released", clear=("remoteHandle",), currentStep="任务超时，优云实例已自动释放")
                        else:
                            error = str((result or {}).get("error") or "超时任务实例仍未释放")
                            runtime.update(task_id, cleanupState="failed", cleanupError=error)
                            _schedule_cleanup_retry(runtime, task_id, task, error=error)
                except Exception as exc:
                    try:
                        runtime.update(task_id, cleanupState="failed", cleanupError=str(exc)[:8000])
                        _schedule_cleanup_retry(runtime, task_id, task, error=str(exc))
                    except Exception:
                        pass
        await asyncio.sleep(interval)

# ==== fusion: 视觉理解/文生图运行时 ====
vision_preview_root = (RUNTIME_ROOT / "vision-training-previews").resolve()
vision_training_root = (RUNTIME_ROOT / "vision-training-tasks").resolve()
vision_executor = LocalVisionTrainingExecutor(
    work_root=vision_training_root,
    allowed_root=vision_preview_root,
    command=os.environ.get("XUNTUI_LLAMA_FACTORY_COMMAND", "llamafactory-cli"),
    python_executable=os.environ.get("XUNTUI_TRAINING_PYTHON") or None,
)
@scoped_resource("task")
def build_visual_youyun_runner(sink: Any, task: Mapping[str, Any] | None = None):
    """Build the visual runner from the task's resolved Youyun resource.

    Resource discovery and task execution are separate requests.  Reusing the
    process-default client here can silently switch back to the default region
    or zone after the user selected another one in the UI, so execution must
    consume the persisted snapshot.
    """
    resource = dict((task or {}).get("youyunResource") or {})
    if not resource:
        marker = resolve_youyun_instance_artifact((task or {}).get("artifacts") or [])
        if isinstance(marker, Mapping):
            resource = {
                key: marker.get(key)
                for key in ("region", "zone", "chargeType", "imageId")
                if marker.get(key)
            }
    return build_youyun_runner(
        region=str(resource.get("region") or "").strip() or None,
        zone=str(resource.get("zone") or "").strip() or None,
        charge_type=str(resource.get("chargeType") or "").strip() or None,
        sink=sink,
        mirror_root=(RUNTIME_ROOT / "vision-remote-jobs").resolve(),
        prepare_local=vision_executor.prepare,
        collect_local=vision_executor.collect_artifacts,
        retain_for_comparison=False,
    )
_visual_probe_runner: YouyunVisionProbeRunner | None = None
_image_generation_probe_runner: YouyunImageGenerationProbeRunner | None = None
def visual_probe_runner() -> YouyunVisionProbeRunner:
    if _visual_probe_runner is not None:
        return _visual_probe_runner
    from backend.providers.youyun.client import YouyunClient
    from backend.providers.youyun.instance_manager import YouyunInstanceManager
    return YouyunVisionProbeRunner(YouyunInstanceManager(YouyunClient()))

async def run_visual_youyun_probe(request: Any, resource: Any) -> dict[str, Any]:
    result = await visual_probe_runner().run(request, resource)
    report = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    image_id = str((resource or {}).get("imageId") or "")
    if image_id and image_id in community_image_ids:
        passed = bool(report.get("passed")) and report.get("cleanupState") == "released"
        community_image_verifications[image_id] = {
            "state": "verified" if passed else "failed",
            "verifiedAt": datetime.now().isoformat(timespec="seconds"),
            "expiresAtEpoch": time.time() + 24 * 60 * 60 if passed else 0,
            "message": report.get("blockingReason") or report.get("cleanupError") or ("探针通过" if passed else "探针未通过"),
        }
    return report
def image_generation_probe_runner() -> YouyunImageGenerationProbeRunner:
    if _image_generation_probe_runner is not None:
        return _image_generation_probe_runner
    from backend.providers.youyun.client import YouyunClient
    from backend.providers.youyun.instance_manager import YouyunInstanceManager
    return YouyunImageGenerationProbeRunner(YouyunInstanceManager(YouyunClient()))

async def run_image_generation_youyun_probe(
    request: ImageGenerationProbeRequest,
    resource: Mapping[str, Any],
) -> dict[str, Any]:
    result = await image_generation_probe_runner().run(request, resource)
    report = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    image_id = str(resource.get("imageId") or "")
    key = _community_verification_key(
        "image_generation_training",
        image_id,
        str(resource.get("region") or ""),
        str(resource.get("zone") or ""),
        str(resource.get("gpuType") or ""),
    )
    passed = bool(report.get("passed")) and report.get("cleanupState") == "released"
    community_image_verifications[key] = {
        "state": "verified" if passed else "failed",
        "verifiedAt": datetime.now().isoformat(timespec="seconds"),
        "expiresAtEpoch": time.time() + 24 * 60 * 60 if passed else 0,
        "message": report.get("blockingReason")
        or report.get("cleanupError")
        or ("探针通过" if passed else "探针未通过"),
    }
    verification = community_image_verifications[key]
    return {
        **report,
        "purpose": "image_generation_training",
        "imageId": image_id,
        "verificationState": verification["state"],
        "verifiedAt": verification["verifiedAt"],
        "verificationExpiresAt": verification["expiresAtEpoch"],
    }
async def validate_image_generation_training_image(
    image_id: str,
    resource: Mapping[str, Any],
) -> str | None:
    if image_id not in community_image_ids:
        try:
            runner = build_youyun_runner(
                region=str(resource.get("region") or "") or None,
                zone=str(resource.get("zone") or "") or None,
            )
            rows = await asyncio.to_thread(
                runner.manager.client.describe_community_images,
                limit=100,
            )
            if any(str(item.get("image_id") or "") == image_id for item in rows):
                community_image_ids.add(image_id)
        except Exception:
            # Unknown image IDs are left to the normal platform-image checks;
            # a community-directory outage must not turn a platform image into
            # a false community rejection.
            return None
    if image_id not in community_image_ids:
        return None
    verification = community_image_verification(
        "image_generation_training",
        image_id,
        str(resource.get("region") or ""),
        str(resource.get("zone") or ""),
        str(resource.get("gpuType") or ""),
    )
    if verification.get("state") == "verified":
        return None
    return "所选文生图社区镜像尚未通过当前地域、可用区和 GPU 的真实实例探针。"
async def load_youyun_model_repository_for_vision() -> list[dict[str, Any]]:
    """Load the official public model repository without affecting local fallback paths."""
    try:
        runner = build_youyun_runner()
        client = runner.manager.client
        # The public API's tag filter is inconsistent (the documented VL tag
        # currently returns no rows), so combine the unfiltered catalog with
        # targeted name searches and deduplicate by mounted path/repo name.
        queries = [None, "Qwen3-VL", "Qwen2.5-VL", "Qwen2-VL", "LLaVA", "InternVL"]
        responses = await asyncio.gather(
            *(
                asyncio.to_thread(
                    client.describe_model_repository_models,
                    name=query,
                )
                for query in queries
            ),
            return_exceptions=True,
        )
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for response in responses:
            if not isinstance(response, list):
                continue
            for row in response:
                if not isinstance(row, dict):
                    continue
                key = str(row.get("path") or row.get("name") or "").strip().casefold()
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(row)
        return merged
    except Exception:
        return []
async def validate_youyun_training_image(image_id: str) -> str | None:
    """Reject unverified community images while allowing live platform images."""
    configured = image_id.strip()
    if not configured:
        return "优云训练环境缺少真实镜像 ID。"
    if configured in community_image_ids:
        verification = community_image_verifications.get(configured, {})
        expires_at = verification.get("expiresAtEpoch")
        if verification.get("state") == "verified" and (expires_at is None or float(expires_at) > time.time()):
            return None
        return "所选社区训练环境尚未通过真实实例探针，请先点击“验证此环境”。"
    try:
        client = build_youyun_runner().manager.client
        platform_images = await asyncio.to_thread(client.describe_images)
        if any(str(item.get("image_id") or "") == configured for item in platform_images):
            return None
        results = await asyncio.gather(
            *(asyncio.to_thread(client.describe_community_images, keyword=keyword, limit=100) for keyword in ("LLaMAFactory", "Qwen-VL", "视觉")),
            return_exceptions=True,
        )
        for result in results:
            if not isinstance(result, list):
                continue
            community_image_ids.update(str(item.get("image_id") or "") for item in result if item.get("image_id"))
    except Exception as exc:  # noqa: BLE001 - validation must fail closed
        return f"优云镜像目录校验失败：{exc}"
    if configured in community_image_ids:
        verification = community_image_verifications.get(configured, {})
        expires_at = verification.get("expiresAtEpoch")
        if verification.get("state") == "verified" and (expires_at is None or float(expires_at) > time.time()):
            return None
        return "所选社区训练环境尚未通过真实实例探针，请先点击“验证此环境”。"
    return "所选优云镜像未在当前平台或社区目录中找到，请刷新镜像列表。"
vision_training_runtime = VisionTrainingRuntime(
    task_root=(RUNTIME_ROOT / "vision-tasks").resolve(),
    local_executor=vision_executor,
    execution_lock=training_execution_lock,
    remote_runner_factory=build_visual_youyun_runner,
    busy_check=lambda: inference_runtime.local_active,
)
def build_image_generation_provider(_: Any):
    from backend.providers.youyun.client import YouyunClient
    from backend.providers.youyun.diffusers import YouyunDiffusersProvider
    from backend.providers.youyun.instance_manager import YouyunInstanceManager

    return YouyunDiffusersProvider(
        YouyunInstanceManager(YouyunClient()),
        remote_root=os.environ.get("XUNTUI_IMAGE_GENERATION_REMOTE_ROOT", "/root/cppt/image-generation"),
        remote_python=os.environ.get("XUNTUI_DIFFUSERS_PYTHON") or None,
    )
async def check_image_generation_image(image_id: str) -> dict[str, Any]:
    from backend.providers.youyun.client import YouyunClient
    from backend.providers.youyun.models import YouyunApiError

    try:
        client = YouyunClient()
        images = await asyncio.to_thread(client.describe_images)
        configured = image_id.strip()
        selected = next((item for item in images if str(item.get("image_id") or "") == configured), None)
        allowed, _ = select_image_generation_images(images, configured_image_id=configured)
    except (YouyunApiError, ValueError) as exc:
        raise RuntimeError(str(exc)) from exc
    if selected is None or not any(str(item.get("image_id") or "") == configured for item in allowed):
        raise RuntimeError("所选优云镜像不是当前策略允许的 Diffusers 镜像。")
    requires_install = not _diffusers_image(selected)
    return {
        "imageId": configured,
        "name": selected.get("name"),
        "requiresDiffusersInstall": requires_install,
        "message": "优云预置 Diffusers 镜像可用。" if not requires_install else "将使用 CUDA/PyTorch 镜像并安装 Diffusers。",
    }
image_generation_runtime = ImageGenerationRuntime(
    task_root=(RUNTIME_ROOT / "image-generation-tasks").resolve(),
    execution_lock=training_execution_lock,
    provider_factory=build_image_generation_provider,
    busy_check=lambda: inference_runtime.local_active or vision_training_runtime.active_task_id is not None or active_task_id is not None,
)
image_generation_service_runtime = ImageGenerationServiceRuntime(
    inference_store,
    lambda: build_image_generation_provider(None),
)
image_generation_runtime.service_handle_lookup = image_generation_service_runtime.handle
def build_image_generation_evaluation_executor(_: Any):
    mode = os.environ.get("XUNTUI_IMAGE_GENERATION_EVALUATION_EXECUTOR", "fake").strip().lower()
    if mode == "fake":
        return FakeImageGenerationEvaluationExecutor()
    if mode == "youyun":
        from backend.providers.youyun.image_generation_evaluation import YouyunImageGenerationEvaluationExecutor
        from backend.providers.youyun.client import YouyunClient
        from backend.providers.youyun.instance_manager import YouyunInstanceManager
        return YouyunImageGenerationEvaluationExecutor(
            YouyunInstanceManager(YouyunClient()),
            remote_root=os.environ.get("XUNTUI_IMAGE_GENERATION_EVALUATION_REMOTE_ROOT", "/root/cppt/image-generation-evaluations"),
            remote_python=os.environ.get("XUNTUI_DIFFUSERS_PYTHON") or None,
        )
    raise RuntimeError("XUNTUI_IMAGE_GENERATION_EVALUATION_EXECUTOR 只能是 fake 或 youyun。")
image_generation_evaluation_runtime = ImageGenerationEvaluationRuntime(
    task_root=(RUNTIME_ROOT / "image-generation-evaluations").resolve(),
    execution_lock=training_execution_lock,
    executor_factory=build_image_generation_evaluation_executor,
    busy_check=lambda: inference_runtime.local_active or vision_training_runtime.active_task_id is not None or image_generation_runtime.active_task_id is not None or image_generation_training_runtime.active_task_id is not None or active_task_id is not None,
)
def build_image_generation_training_executor(_: Any):
    """Use Fake execution by default; real Youyun execution is explicit."""
    mode = os.environ.get("XUNTUI_IMAGE_GENERATION_TRAINING_EXECUTOR", "fake").strip().lower()
    if mode == "fake":
        return FakeImageGenerationTrainingExecutor()
    if mode == "youyun":
        from backend.providers.youyun.client import YouyunClient
        from backend.providers.youyun.image_generation_training import YouyunImageGenerationTrainingExecutor
        from backend.providers.youyun.instance_manager import YouyunInstanceManager
        return YouyunImageGenerationTrainingExecutor(
            YouyunInstanceManager(YouyunClient()),
            remote_root=os.environ.get("XUNTUI_IMAGE_GENERATION_TRAINING_REMOTE_ROOT", "/root/cppt/image-generation-training"),
            remote_python=os.environ.get("XUNTUI_IMAGE_GENERATION_TRAINING_PYTHON") or None,
        )
    raise RuntimeError("XUNTUI_IMAGE_GENERATION_TRAINING_EXECUTOR 只能是 fake 或 youyun。")
image_generation_training_runtime = ImageGenerationTrainingRuntime(
    task_root=(RUNTIME_ROOT / "image-generation-training-tasks").resolve(),
    execution_lock=training_execution_lock,
    executor_factory=build_image_generation_training_executor,
    busy_check=lambda: inference_runtime.local_active or vision_training_runtime.active_task_id is not None or image_generation_runtime.active_task_id is not None or active_task_id is not None,
    on_terminal=lambda task: sync_image_generation_adapters(store, task),
)

class YouyunCommunityImageProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: Literal["image_generation_training"]
    imageId: str = Field(min_length=1, max_length=160)
    modelRepoId: str = Field(min_length=1, max_length=240)
    resource: YouyunResourceSelection

def _youyun_resource_selection(resource: Mapping[str, Any]) -> YouyunResourceSelection:
    """Accept canonical resource snapshots that also carry the resolved region."""
    values = {
        key: resource[key]
        for key in ("mode", "gpuType", "gpuCount", "cpuCores", "memoryGb", "region", "zone", "imageId", "instanceMode", "chargeType", "fallbackEligible")
        if key in resource
    }
    return YouyunResourceSelection.model_validate(values)

model_storage_config = ModelStorageConfig.from_env(PROJECT_ROOT / ".env")
model_uploader: ModelArtifactUploader | None = None
model_storage_error: str | None = None
dataset_minio_client: Any | None = None
dataset_minio_error: str | None = None
retention_task: asyncio.Task[None] | None = None
youyun_watchdog_task: asyncio.Task[None] | None = None
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
DOWNLOAD_PROGRESS = re.compile(r"([^/\\]+\.safetensors):\s+(\d+)%")


class DatasetImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sourceKey: str = Field(min_length=1, max_length=1024)
    name: str = Field(min_length=1, max_length=200)
    structure: Literal["SFT", "DPO"]


class DatasetRepairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["manual"] = "manual"
    content: str | None = Field(default=None, max_length=50_000_000)
    edits: list[dict[str, Any]] = Field(default_factory=list, max_length=200)


class TrainingParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    epochs: float = Field(gt=0, le=100)
    learningRate: str
    batchSize: int = Field(ge=1, le=64)
    gradientAccumulation: int = Field(ge=1, le=256)
    validationSteps: int = Field(ge=1)
    maxSequenceLength: int = Field(ge=8, le=32768)
    loraRank: int = Field(ge=1, le=256)
    loraAlpha: int = Field(ge=1, le=512)
    loraDropout: float = Field(ge=0, lt=1)
    lrScheduler: str
    loraPlusEnabled: bool = False
    loraPlusRatio: float = Field(gt=0)
    targetModules: str
    optimizer: str
    warmupRatio: float = Field(ge=0, lt=1)
    weightDecay: float = Field(ge=0)
    maxGradNorm: float = Field(gt=0)
    seed: int
    loggingSteps: int = Field(ge=1)
    packing: bool
    gradientCheckpointing: bool


class ChatParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    maxNewTokens: int = Field(default=256, ge=1, le=2048)
    temperature: float = Field(default=0.2, ge=0, le=2)
    topP: float = Field(default=0.9, gt=0, le=1)
    seed: int = 42


class EvaluationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    datasetId: str = Field(min_length=1)
    datasetVersion: int | None = Field(default=None, ge=1)
    targetTaskId: str = Field(min_length=1)
    targetVariant: Literal["selected", "best", "final"] = "selected"
    dimensions: list[str] = Field(default_factory=lambda: ["correctness", "relevance", "completeness"], max_length=10)
    generationParameters: ChatParameters | None = None
    judgeBaseUrl: str | None = Field(default=None, max_length=1000)
    judgeModel: str | None = Field(default=None, max_length=200)
    useTeacher: bool = True
    useSimilarity: bool = True


class EvaluationReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: float = Field(ge=0, le=5)
    dimensions: dict[str, float] = Field(default_factory=dict)
    attribution: Literal["model_wrong", "reference_wrong", "acceptable_different", "both_wrong", "needs_expert", "other"]
    note: str = Field(default="", max_length=10000)


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=20000)
    elapsedSeconds: float | None = Field(default=None, ge=0)
    outputTokens: int | None = Field(default=None, ge=0)


class YouyunResourceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["auto", "manual"] = "manual"
    gpuType: str | None = Field(default=None, min_length=1, max_length=40)
    # 当前生产边界：暂时只允许单卡单进程训练；多卡字段保留用于未来兼容。
    gpuCount: int = Field(default=1, ge=1, le=8)
    distributedStrategy: Literal["single", "ddp", "deepspeed_zero2"] = "single"
    cpuCores: int | None = Field(default=None, ge=1, le=1024)
    memoryGb: int | None = Field(default=None, ge=1, le=4096)
    region: str | None = Field(default=None, min_length=1, max_length=80)
    zone: str | None = Field(default=None, min_length=1, max_length=80)
    imageId: str | None = Field(default=None, min_length=1, max_length=200)
    imageName: str | None = Field(default=None, max_length=120)
    imageVersion: str | None = Field(default=None, max_length=120)
    purpose: Literal["training", "inference"] = "training"
    instanceMode: Literal["uhost", "pod", "auto"] | None = None
    publicModelPath: str | None = Field(default=None, min_length=1, max_length=1000)
    chargeType: Literal["Postpay", "Spot"] | None = None
    fallbackEligible: bool | None = None


class YouyunPriceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["auto", "manual"] = "manual"
    gpuType: str | None = Field(default=None, min_length=1, max_length=40)
    gpuCount: int = Field(default=1, ge=1, le=8)
    distributedStrategy: Literal["single", "ddp", "deepspeed_zero2"] = "single"
    cpuCores: int | None = Field(default=None, ge=1, le=1024)
    memoryGb: int | None = Field(default=None, ge=1, le=4096)
    region: str | None = Field(default=None, min_length=1, max_length=80)
    zone: str | None = Field(default=None, min_length=1, max_length=80)
    imageId: str | None = Field(default=None, min_length=1, max_length=200)
    imageName: str | None = Field(default=None, max_length=120)
    imageVersion: str | None = Field(default=None, max_length=120)
    purpose: Literal["training", "inference"] = "training"
    instanceMode: Literal["uhost", "pod", "auto"] | None = None
    publicModelPath: str | None = Field(default=None, min_length=1, max_length=1000)


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    framework: Literal["llamafactory"] = "llamafactory"
    executionMode: Literal["local", "remote"] = "local"
    provider: Literal["local", "youyun"] = "local"
    datasetVersion: str | None = None
    model: str = Field(min_length=1)
    modelSource: str = "modelscope"
    taskType: Literal["SFT", "DPO"] = "SFT"
    tuningMode: Literal["lora", "qlora"] = "qlora"
    datasetId: str
    datasetName: str
    owner: str = "平台管理员"
    mergeModel: bool = False
    validationSplit: int = Field(default=0, ge=0, le=30)
    outputName: str = Field(min_length=1, max_length=100)
    checkpointLimit: int = Field(default=2, ge=1, le=20)
    checkpointStrategy: str = "epoch"
    checkpointInterval: int = Field(default=100, ge=1)
    maxSteps: int = Field(default=-1, ge=-1)
    earlyStopping: bool = False
    patience: int = Field(default=3, ge=1, le=100)
    minDelta: float = Field(default=0, ge=0)
    extraParameters: dict[str, Any] = Field(default_factory=dict)
    parameters: TrainingParameters
    baselineMessages: list[ChatMessage] = Field(default_factory=list, max_length=60)
    chatParameters: ChatParameters = Field(default_factory=ChatParameters)
    youyunResource: YouyunResourceSelection | None = None


class ArtifactOpenRequest(BaseModel):
    path: str = Field(min_length=1)


class ArtifactPreviewRequest(BaseModel):
    path: str = Field(min_length=1, max_length=2000)
    relativePath: str | None = Field(default=None, max_length=1000)


class CliTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1, max_length=20000)


class BaseModelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    repo: str = Field(min_length=1, max_length=200)
    revision: str = Field(default="main", min_length=1, max_length=120)
    path: str = Field(min_length=1, max_length=1000)


class ModelAssetPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    archived: bool | None = None


class ModelAssetUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uploadedBy: str = Field(default="local-admin", min_length=1, max_length=120)


class AdapterSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant: Literal["best", "final"]


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["base", "adapter"]
    model: str = Field(min_length=1)
    modelSource: str = "modelscope"
    tuningMode: Literal["lora", "qlora"] = "qlora"
    taskId: str | None = None
    adapterVariant: Literal["selected", "best", "final"] = "selected"
    messages: list[ChatMessage] = Field(min_length=1, max_length=60)
    parameters: ChatParameters = Field(default_factory=ChatParameters)


def task_config(task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    dataset = store.get_dataset(payload["datasetId"])
    if dataset is None:
        raise ValueError("数据集不存在或已被删除。")
    if dataset["state"] != "published":
        raise ValueError("数据集尚未发布，不能用于训练。")
    task_type = payload.get("taskType", "SFT")
    dataset_structure = dataset.get("structure") or "SFT"
    if task_type != dataset_structure:
        raise ValueError(f"训练类型 {task_type} 与数据集结构 {dataset_structure} 不匹配。")
    return {
        **payload,
        **dataset["paths"],
        "id": task_id,
        "datasetName": payload["datasetName"],
        "artifactRoot": str(ARTIFACT_ROOT),
        "runtimeRoot": str(RUNTIME_ROOT),
        "stopFlagPath": str(RUNTIME_ROOT / "jobs" / f"{task_id}.stop"),
    }


def _validate_dpo_value(value: Any, field: str, line_number: int) -> str | None:
    if isinstance(value, str):
        if value.strip():
            return None
        return f"第 {line_number} 行 {field} 字段不能为空。"
    if isinstance(value, dict):
        if value.get("role") != "assistant":
            return f"第 {line_number} 行 {field} 消息 role 必须是 assistant。"
        if not isinstance(value.get("content"), str) or not value["content"].strip():
            return f"第 {line_number} 行 {field} 消息 content 不能为空。"
        return None
    if isinstance(value, list):
        if not value:
            return f"第 {line_number} 行 {field} 消息不能为空。"
        for message_index, message in enumerate(value, 1):
            if not isinstance(message, dict):
                return f"第 {line_number} 行 {field} 第 {message_index} 条消息必须是对象。"
            if message.get("role") not in {"system", "user", "assistant"}:
                return f"第 {line_number} 行 {field} 第 {message_index} 条消息 role 不支持。"
            if not isinstance(message.get("content"), str) or not message["content"].strip():
                return f"第 {line_number} 行 {field} 第 {message_index} 条消息 content 不能为空。"
        return None
    return f"第 {line_number} 行 {field} 字段格式不支持。"


def _validate_dpo_record(record: dict[str, Any], line_number: int) -> str | None:
    # Keep the original prompt/chosen/rejected string contract while accepting
    # the conversational JSONL shape used by the supplied DPO file.
    if "prompt" in record:
        for field in ("prompt", "chosen", "rejected"):
            if not isinstance(record.get(field), str) or not record[field].strip():
                return f"第 {line_number} 行 {field} 字段不能为空。"
        return None

    messages = record.get("messages")
    if not isinstance(messages, list) or not messages:
        return f"第 {line_number} 行 messages 必须是非空消息数组。"
    has_user = False
    for message_index, message in enumerate(messages, 1):
        if not isinstance(message, dict):
            return f"第 {line_number} 行 messages 第 {message_index} 条消息必须是对象。"
        if message.get("role") not in {"system", "user", "assistant"}:
            return f"第 {line_number} 行 messages 第 {message_index} 条消息 role 不支持。"
        if message.get("role") == "user":
            has_user = True
        if not isinstance(message.get("content"), str) or not message["content"].strip():
            return f"第 {line_number} 行 messages 第 {message_index} 条消息 content 不能为空。"
    if not has_user:
        return f"第 {line_number} 行 messages 至少需要一条 user 消息。"
    for field in ("chosen", "rejected"):
        error = _validate_dpo_value(record.get(field), field, line_number)
        if error:
            return error
    return None


def validate_text_dataset(path: Path, structure: str) -> tuple[int, str | None]:
    sample_count = 0
    try:
        with path.open(encoding="utf-8") as source:
            for line_number, line in enumerate(source, 1):
                if not line.strip():
                    continue
                sample_count += 1
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    return sample_count, f"第 {line_number} 行不是合法 JSON。"
                if not isinstance(record, dict):
                    return sample_count, f"第 {line_number} 行必须是 JSON 对象。"
                if structure == "DPO":
                    validation_error = _validate_dpo_record(record, line_number)
                    if validation_error:
                        return sample_count, validation_error
                    continue
                if sample_count != 1:
                    continue
                messages = record.get("messages")
                if not isinstance(messages, list) or len(messages) < 2:
                    return sample_count, "第一行 messages 必须至少包含 user 和 assistant。"
                roles: list[str] = []
                for message_index, message in enumerate(messages, 1):
                    if not isinstance(message, dict):
                        return sample_count, f"第一行第 {message_index} 条消息必须是对象。"
                    role = message.get("role")
                    content = message.get("content")
                    if role not in {"system", "user", "assistant"}:
                        return sample_count, f"第一行第 {message_index} 条消息 role 不支持。"
                    if not isinstance(content, str) or not content.strip():
                        return sample_count, f"第一行第 {message_index} 条消息 content 不能为空。"
                    roles.append(role)
                dialogue_roles = roles[1:] if roles[0] == "system" else roles
                if not dialogue_roles or dialogue_roles[0] != "user":
                    return sample_count, "第一行对话必须从 user 开始，system 可选。"
                if dialogue_roles[-1] != "assistant":
                    return sample_count, "第一行必须以 assistant 回复结束。"
                for role_index, role in enumerate(dialogue_roles):
                    expected = "user" if role_index % 2 == 0 else "assistant"
                    if role != expected:
                        return sample_count, "第一行 user 与 assistant 消息必须交替出现。"
    except UnicodeDecodeError:
        return sample_count, "文件必须使用 UTF-8 编码。"
    if sample_count == 0:
        return 0, "文件没有可用数据。"
    return sample_count, None


def _validation_issue(
    code: str,
    message: str,
    *,
    line: int | None = None,
    path: str = "",
    fixable: bool = False,
    suggestion: str = "",
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": "error",
        "line": line,
        "path": path,
        "message": message,
        "fixable": fixable,
        "suggestion": suggestion,
    }


def _validate_sft_record(record: dict[str, Any], line_number: int) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    messages = record.get("messages")
    if not isinstance(messages, list) or len(messages) < 2:
        return [_validation_issue(
            "SFT_MESSAGES_REQUIRED",
            f"第 {line_number} 行 messages 必须至少包含 user 和 assistant。",
            line=line_number,
            path="messages",
            suggestion="请提供至少一条 user 消息和一条 assistant 消息。",
        )]
    roles: list[str] = []
    for message_index, message in enumerate(messages, 1):
        path = f"messages[{message_index - 1}]"
        if not isinstance(message, dict):
            issues.append(_validation_issue(
                "MESSAGE_OBJECT_REQUIRED",
                f"第 {line_number} 行第 {message_index} 条消息必须是对象。",
                line=line_number,
                path=path,
                suggestion='格式示例：{"role":"user","content":"..."}。',
            ))
            continue
        role = message.get("role")
        content = message.get("content")
        roles.append(str(role))
        if role not in {"system", "user", "assistant"}:
            issues.append(_validation_issue(
                "ROLE_UNSUPPORTED",
                f"第 {line_number} 行第 {message_index} 条消息 role 不支持。",
                line=line_number,
                path=f"{path}.role",
                suggestion="请人工确认这条消息的角色，只能填写 system、user 或 assistant。",
            ))
        if not isinstance(content, str) or not content.strip():
            issues.append(_validation_issue(
                "CONTENT_REQUIRED",
                f"第 {line_number} 行第 {message_index} 条消息 content 不能为空。",
                line=line_number,
                path=f"{path}.content",
                fixable=False,
                suggestion="请填写非空文本内容。",
            ))
    dialogue_roles = roles[1:] if roles and roles[0] == "system" else roles
    if dialogue_roles and dialogue_roles[0] != "user":
        issues.append(_validation_issue(
            "DIALOGUE_START",
            f"第 {line_number} 行对话必须从 user 开始，system 可选。",
            line=line_number,
            path="messages",
            suggestion="将第一条对话消息改为 user。",
        ))
    if dialogue_roles and dialogue_roles[-1] != "assistant":
        issues.append(_validation_issue(
            "DIALOGUE_END",
            f"第 {line_number} 行必须以 assistant 回复结束。",
            line=line_number,
            path="messages",
            suggestion="补充 assistant 回复，或将最后一条消息改为 assistant。",
        ))
    for role_index, role in enumerate(dialogue_roles):
        expected = "user" if role_index % 2 == 0 else "assistant"
        if role != expected:
            issues.append(_validation_issue(
                "DIALOGUE_ORDER",
                f"第 {line_number} 行 user 与 assistant 消息必须交替出现。",
                line=line_number,
                path="messages",
                suggestion="请按 user、assistant、user、assistant 的顺序排列。",
            ))
            break
    return issues


def validate_text_dataset_report(path: Path, structure: str, *, max_errors: int = 100) -> dict[str, Any]:
    """Return a structured report while keeping the legacy tuple validator intact."""
    checked_rows = 0
    invalid_lines: set[int] = set()
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8-sig") as source:
            for line_number, raw_line in enumerate(source, 1):
                if not raw_line.strip():
                    warnings.append({
                        "code": "BLANK_LINE",
                        "severity": "warning",
                        "line": line_number,
                        "path": "",
                        "message": f"第 {line_number} 行为空行。",
                        "fixable": False,
                        "suggestion": "请人工确认是否删除该空行。",
                    })
                    continue
                checked_rows += 1
                try:
                    record = json.loads(raw_line)
                except json.JSONDecodeError:
                    invalid_lines.add(line_number)
                    errors.append(_validation_issue(
                        "INVALID_JSON",
                        f"第 {line_number} 行不是合法 JSON。",
                        line=line_number,
                        fixable=False,
                        suggestion="请检查引号、逗号和括号。",
                    ))
                    if len(errors) >= max_errors:
                        break
                    continue
                if not isinstance(record, dict):
                    invalid_lines.add(line_number)
                    errors.append(_validation_issue(
                        "JSON_OBJECT_REQUIRED",
                        f"第 {line_number} 行必须是 JSON 对象。",
                        line=line_number,
                        fixable=False,
                        suggestion="每一行应是一个 JSON 对象，而不是数组或字符串。",
                    ))
                elif structure == "DPO":
                    message = _validate_dpo_record(record, line_number)
                    if message:
                        invalid_lines.add(line_number)
                        errors.append(_validation_issue(
                            "DPO_SCHEMA_ERROR",
                            message,
                            line=line_number,
                            path="",
                            fixable=False,
                            suggestion="请检查 messages、chosen 和 rejected 字段。",
                        ))
                else:
                    row_errors = _validate_sft_record(record, line_number)
                    if row_errors:
                        invalid_lines.add(line_number)
                        errors.extend(row_errors)
                if len(errors) >= max_errors:
                    break
    except UnicodeDecodeError:
        errors.append(_validation_issue(
            "INVALID_ENCODING",
            "文件必须使用 UTF-8 编码。",
            fixable=False,
            suggestion="请将文件另存为 UTF-8 编码。",
        ))
    except OSError as exc:
        errors.append(_validation_issue("FILE_READ_ERROR", f"读取数据集文件失败：{exc}"))
    errors = errors[:max_errors]
    status_value = "failed" if errors else "ready"
    return {
        "status": status_value,
        "checkedRows": checked_rows,
        "validRows": max(0, checked_rows - len(invalid_lines)),
        "invalidRows": len(invalid_lines),
        "errors": errors,
        "warnings": warnings[:max_errors],
        "autoFixableCount": 0,
        "validatorVersion": "text-jsonl-v2",
        "checkedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def repair_text_dataset(
    source_path: Path,
    target_path: Path,
    structure: str,
    edits: list[dict[str, Any]] | None = None,
) -> dict[str, int]:
    """Write a new dataset using only the rows explicitly edited by the user."""
    repaired = 0
    unchanged_invalid = 0
    edit_map = {
        int(item.get("line")): item
        for item in (edits or [])
        if str(item.get("line", "")).isdigit()
    }
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with source_path.open(encoding="utf-8-sig") as source, target_path.open("w", encoding="utf-8", newline="\n") as target:
        for line_number, raw_line in enumerate(source, 1):
            edit = edit_map.get(line_number)
            if edit is not None and isinstance(edit.get("raw"), str):
                target.write(str(edit["raw"]).rstrip("\r\n") + "\n")
                repaired += 1
                continue
            if edit is not None and isinstance(edit.get("record"), dict):
                target.write(json.dumps(edit["record"], ensure_ascii=False, separators=(",", ":")) + "\n")
                repaired += 1
                continue
            target.write(raw_line)
            if raw_line.strip():
                try:
                    record = json.loads(raw_line)
                except json.JSONDecodeError:
                    unchanged_invalid += 1
                    continue
                if not isinstance(record, dict):
                    unchanged_invalid += 1
            continue
    return {"repairedRows": repaired, "removedBlankLines": 0, "unchangedInvalidRows": unchanged_invalid}


def file_sha256(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


async def stop_chat_process() -> None:
    global chat_process, chat_process_key
    process = chat_process
    chat_process = None
    chat_process_key = None
    if process is None or process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=10)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


async def read_chat_event(
    process: asyncio.subprocess.Process,
    *,
    event_type: str,
    request_id: str | None = None,
) -> dict[str, Any]:
    assert process.stdout is not None
    while True:
        line = await process.stdout.readline()
        if not line:
            raise RuntimeError("推理进程已退出。")
        text = ANSI_ESCAPE.sub("", line.decode("utf-8", errors="replace")).strip()
        if not text.startswith("__XUNTUI_CHAT__"):
            continue
        event = json.loads(text.removeprefix("__XUNTUI_CHAT__"))
        if event.get("type") in {"fatal", "error"}:
            raise RuntimeError(str(event.get("message", "推理失败。")))
        if event.get("type") != event_type:
            continue
        if request_id is not None and event.get("requestId") != request_id:
            continue
        return event


async def ensure_chat_process(
    *,
    model: str,
    source: str,
    adapter_path: str | None,
    load_in_4bit: bool,
) -> asyncio.subprocess.Process:
    global chat_process, chat_process_key
    key = json.dumps([model, source, adapter_path, load_in_4bit], ensure_ascii=False)
    if (
        chat_process is not None
        and chat_process.returncode is None
        and chat_process_key == key
    ):
        return chat_process

    await stop_chat_process()
    command = [
        sys.executable,
        str(CHAT_PATH),
        "--model",
        model,
        "--source",
        source,
    ]
    if adapter_path:
        command.extend(["--adapter", adapter_path])
    if load_in_4bit:
        command.append("--load-in-4bit")
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
    environment.setdefault("UNSLOTH_COMPILE_LOCATION", "/root/unsloth_compiled_cache")
    chat_process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=environment,
    )
    chat_process_key = key
    await asyncio.wait_for(
        read_chat_event(chat_process, event_type="ready"), timeout=240
    )
    return chat_process


async def consume_output(task_id: str, process: asyncio.subprocess.Process) -> str:
    assert process.stdout is not None
    completed_event = False
    stopped_event = False
    stop_deadline: float | None = None
    payload = store.get_payload(task_id)
    logging_steps = max(1, int(payload["parameters"].get("loggingSteps", 10)))
    total_steps: int | None = None
    total_epochs: float | None = None
    last_train_step = 0
    while True:
        if store.is_cancel_requested(task_id):
            if stop_deadline is None:
                stop_deadline = asyncio.get_running_loop().time() + 60
                stop_flag = Path(
                    str(payload.get("stopFlagPath") or RUNTIME_ROOT / "jobs" / f"{task_id}.stop")
                )
                stop_flag.parent.mkdir(parents=True, exist_ok=True)
                stop_flag.touch(exist_ok=True)
                store.append_log(task_id, "已请求安全停止，等待训练器保存最佳 Adapter。")
                store.update(task_id, current_step="正在停止训练并保存最佳 Adapter")
            if asyncio.get_running_loop().time() > stop_deadline:
                store.append_log(task_id, "训练器未在安全停止窗口内退出，执行强制终止。")
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=10)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
                store.update(
                    task_id,
                    state="stopped",
                    current_step="由用户停止（未完成安全保存）",
                    cancel_requested=False,
                    stop_reason="manual_timeout",
                )
                return "stopped"

        try:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=0.5)
        except asyncio.TimeoutError:
            continue
        if not line:
            break
        text = ANSI_ESCAPE.sub("", line.decode("utf-8", errors="replace")).strip()
        if not text:
            continue
        if not text.startswith("__XUNTUI_EVENT__"):
            if text.startswith(
                "[unsloth_zoo.log|WARNING]Unsloth: Could not build the patched trl.trainer.grpo_trainer"
            ):
                task_type_label = str(
                    payload.get("taskType") or payload.get("task_type") or "SFT"
                ).upper()
                store.append_log(
                    task_id,
                    "Unsloth 的 GRPO 加速补丁与当前 Triton 不兼容；"
                    f"当前任务为 {task_type_label}，不受影响。",
                )
                continue
            download = DOWNLOAD_PROGRESS.search(text)
            if download:
                filename, percent_text = download.groups()
                percent = int(percent_text)
                if "00001-of" in filename and percent % 2 == 0:
                    store.update(
                        task_id,
                        progress=min(10, percent // 10),
                        current_step=f"下载基础模型 · {percent}%",
                    )
                continue
            if "%|" in text:
                continue
            steps_match = TOTAL_STEPS_PATTERN.search(text)
            if steps_match:
                total_steps = int(steps_match.group(1).replace(",", ""))
            epochs_match = NUM_EPOCHS_PATTERN.search(text)
            if epochs_match:
                total_epochs = float(epochs_match.group(1))
            values = parse_trainer_values(text)
            if values is not None:
                step = infer_metric_step(
                    values,
                    total_steps=total_steps,
                    total_epochs=total_epochs,
                    last_train_step=last_train_step,
                    logging_steps=logging_steps,
                )
                if "loss" in values:
                    last_train_step = step
                train_loss = values.get("loss")
                eval_loss = values.get("eval_loss")
                store.append_metric(
                    task_id,
                    step=step,
                    epoch=values.get("epoch"),
                    train_loss=train_loss,
                    eval_loss=eval_loss,
                    learning_rate=values.get("learning_rate"),
                )
                progress = (
                    min(95, int(step * 95 / total_steps)) if total_steps else None
                )
                store.update(
                    task_id,
                    progress=progress,
                    current_step=(
                        f"训练中 · Step {step}/{total_steps}"
                        if total_steps
                        else f"训练中 · Step {step}"
                    ),
                    loss=train_loss if train_loss is not None else eval_loss,
                )
            store.append_log(task_id, text)
            continue
        try:
            event = json.loads(text.removeprefix("__XUNTUI_EVENT__"))
        except json.JSONDecodeError:
            store.append_log(task_id, text)
            continue
        event_type = event.get("type")
        message = str(event.get("message", ""))
        if message:
            store.append_log(task_id, message)
        if event_type == "status":
            store.update(task_id, current_step=message)
        elif event_type == "progress":
            train_loss = (
                float(event["trainLoss"])
                if event.get("trainLoss") is not None
                else None
            )
            eval_loss = (
                float(event["evalLoss"])
                if event.get("evalLoss") is not None
                else None
            )
            store.append_metric(
                task_id,
                step=int(event.get("step", 0)),
                epoch=(
                    float(event["epoch"])
                    if event.get("epoch") is not None
                    else None
                ),
                train_loss=train_loss,
                eval_loss=eval_loss,
                learning_rate=(
                    float(event["learningRate"])
                    if event.get("learningRate") is not None
                    else None
                ),
            )
            store.update(
                task_id,
                progress=int(event.get("progress", 0)),
                current_step=message,
                loss=train_loss if train_loss is not None else eval_loss,
            )
        elif event_type == "completed":
            # Keep the task running until the trainer process exits and the worker
            # releases active_task_id; chat must never observe completed+GPU-busy.
            completed_event = True
            store.update(
                task_id,
                progress=100,
                current_step=message or "训练完成，Adapter 已保存",
                loss=(float(event["loss"]) if event.get("loss") is not None else None),
                artifacts=event.get("artifacts", []),
            )
        elif event_type == "stopped":
            stopped_event = True
            store.update(
                task_id,
                progress=int(event.get("progress", 100)),
                current_step=message or "训练已停止，Adapter 已保存",
                loss=(float(event["loss"]) if event.get("loss") is not None else None),
                artifacts=event.get("artifacts", []),
                stop_reason=str(event.get("stopReason") or "manual"),
            )
        elif event_type == "failed":
            error_message = message or "Unsloth 训练失败。"
            store.update(
                task_id,
                state="failed",
                current_step="训练失败",
                error=error_message,
            )
            trace = event.get("traceback")
            if trace:
                store.append_log(task_id, trace)

    return_code = await process.wait()
    current = store.get(task_id)
    if current and current["state"] == "running":
        if return_code == 0 and completed_event:
            return "completed"
        if return_code == 0 and stopped_event:
            store.update(
                task_id,
                state="stopped",
                current_step="训练已停止，Adapter 已保存",
                cancel_requested=False,
            )
            return "stopped"
        message = f"训练进程异常退出，返回码 {return_code}。"
        store.append_log(task_id, message)
        diagnostics: list[str] = []
        try:
            gpu_status = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total,memory.used,memory.free",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            gpu_text = gpu_status.stdout.strip() or gpu_status.stderr.strip()
            if gpu_text:
                diagnostics.append(f"GPU 状态：{gpu_text}")
        except Exception as exc:
            diagnostics.append(format_gpu_probe_error(exc))
        payload = store.get_payload(task_id) or {}
        parameters = payload.get("parameters") or {}
        diagnostics.append(
            "训练配置："
            f"taskType={payload.get('taskType', 'SFT')}，"
            f"model={payload.get('model', '未知')}，"
            f"tuningMode={payload.get('tuningMode', '未知')}，"
            f"batchSize={parameters.get('batchSize', '未知')}，"
            f"gradientAccumulation={parameters.get('gradientAccumulation', '未知')}，"
            f"maxSequenceLength={parameters.get('maxSequenceLength', '未知')}"
        )
        if payload.get("taskType", "SFT") == "DPO":
            diagnostics.append(
                "DPO 训练未输出 Python traceback，可能是 CUDA/WSL GPU 底层进程退出；"
                "请优先降低单卡批次或最大序列长度后重试。"
            )
        for diagnostic in diagnostics:
            store.append_log(task_id, diagnostic)
        store.update(
            task_id,
            state="failed",
            current_step="训练进程异常退出",
            error=message,
        )
    return "failed"



async def consume_executor_events(task_id: str, task: dict[str, Any]) -> str:
    """Consume the framework-neutral local executor stream into the existing store contract."""
    global active_process
    handle = await local_executor.start(task)
    active_process = handle.process
    store.update(task_id, current_step="训练进程已启动，等待首个训练指标")
    stop_requested = False
    failed_event = False
    try:
        async for event in local_executor.events(task):
            current = store.get(task_id)
            if not stop_requested and store.is_cancel_requested(task_id):
                stop_requested = True
                store.update(task_id, state="stopping", current_step="正在停止训练进程")
                await local_executor.stop(task)
            message = event.message.strip()
            payload = event.payload
            if message:
                store.append_log(task_id, message)
            if event.type == "metric":
                values = payload
                if not ("loss" in values or "eval_loss" in values):
                    values = parse_trainer_values(message) or {}
                if values:
                    step = int(values.get("step") or payload.get("step") or 0)
                    if step <= 0:
                        previous = store.metrics(task_id)
                        step = (previous[-1]["step"] if previous else 0) + 1
                    store.append_metric(
                        task_id,
                        step=step,
                        epoch=float(values["epoch"]) if values.get("epoch") is not None else None,
                        train_loss=float(values["loss"]) if values.get("loss") is not None else None,
                        eval_loss=float(values["eval_loss"]) if values.get("eval_loss") is not None else None,
                        learning_rate=float(values["learning_rate"]) if values.get("learning_rate") is not None else None,
                    )
                    total_steps = int(payload.get("total_steps") or 0)
                    store.update(
                        task_id,
                        progress=min(95, int(payload.get("progress", current.get("progress", 0)) if current else 0)),
                        current_step=(
                            f"训练中 · Step {step}/{total_steps}"
                            if total_steps > 0
                            else f"训练中 · Step {step}"
                        ),
                        loss=(float(values["loss"]) if values.get("loss") is not None else None),
                    )
            elif event.type == "status":
                store.update(task_id, current_step=message or "准备训练环境")
            elif event.type == "log" and "***** Running training *****" in message:
                store.update(task_id, current_step="训练已开始，等待首个训练指标")
            elif event.type == "failed":
                failed_event = True
                store.update(task_id, state="failed", current_step="训练失败", error=message or "LLaMA-Factory 训练失败。")
                if payload.get("traceback"):
                    store.append_log(task_id, str(payload["traceback"]))
            elif event.type == "stopped":
                stop_requested = True
        process = handle.process
        return_code = await process.wait() if process is not None else 0
        if not stop_requested and store.is_cancel_requested(task_id):
            stop_requested = True
        if stop_requested:
            store.update(task_id, state="stopped", current_step="训练已停止", cancel_requested=False, stop_reason="manual")
            return "stopped"
        if failed_event or return_code != 0:
            if not failed_event:
                store.update(task_id, state="failed", current_step="训练进程异常退出", error=f"LLaMA-Factory 进程返回码 {return_code}。")
            return "failed"
        artifacts = await local_executor.collect_artifacts(task)
        store.update(task_id, artifacts=artifacts, progress=100, current_step="训练完成，Adapter 已保存")
        return "completed"
    except Exception as exc:
        store.append_log(task_id, f"LLaMA-Factory 执行器错误：{exc}")
        store.update(task_id, state="failed", current_step="任务启动失败", error=str(exc))
        return "failed"


@scoped_resource("task_id")
async def run_youyun_training(task_id: str, task: dict[str, Any]) -> str:
    """优云远程任务入口：Provider 未配置时优雅失败，实例清理由 runner 内部保证。"""
    try:
        resource = dict(task.get("youyunResource") or {})
        runner = build_youyun_runner(region=resource.get("region"), zone=resource.get("zone"))
    except Exception as exc:
        store.append_log(task_id, f"优云 Provider 未就绪：{exc}")
        store.update(task_id, state="failed", current_step="远程训练未启动", error=str(exc))
        return "failed"
    return await runner.run(task_id, task)


async def validate_youyun_ready() -> None:
    """提交远程任务前校验优云配置与实时库存（验收标准 1：无库存明确报错）。"""
    from backend.providers.youyun.models import YouyunApiError

    try:
        runner = build_youyun_runner()
        capacity = await asyncio.to_thread(
            runner.manager.client.check_resource_capacity,
            image_id=(runner.manager.client.config.training_image_id or runner.manager.client.config.image_id),
        )
    except YouyunApiError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if capacity.first_available() is None:
        raise HTTPException(
            status_code=409,
            detail=f"优云 {runner.manager.client.config.gpu_type} 当前无可用库存，请稍后重试或更换 GPU 型号。",
        )


def _youyun_resource_payload(selection: YouyunResourceSelection | None) -> dict[str, Any]:
    return selection.model_dump(exclude_none=True) if selection is not None else {"mode": "manual", "gpuCount": 1, "distributedStrategy": "single"}


async def _validate_youyun_mode_zone(
    client: Any,
    *,
    mode: str,
    zone: str,
    region: str,
    zones: list[dict[str, Any]] | None = None,
) -> None:
    """Reject instance mode/zone combinations the provider cannot create."""
    from backend.providers.youyun.models import YouyunApiError

    describe_support_zones = getattr(client, "describe_support_zones", None)
    if not callable(describe_support_zones):
        return
    if zones is None:
        try:
            zones = await asyncio.to_thread(describe_support_zones, region)
        except YouyunApiError as exc:
            raise HTTPException(status_code=502, detail=f"优云可用区校验失败：{exc}") from exc
    selected = next((item for item in zones if item.get("zone") == zone), None)
    if selected is None:
        raise HTTPException(status_code=422, detail=f"优云可用区 {zone} 不在当前区域支持列表中，请刷新后重试。")
    zone_region = str(selected.get("region") or "")
    if region and zone_region and zone_region != region:
        raise HTTPException(
            status_code=422,
            detail=f"优云可用区 {zone} 属于区域 {zone_region}，与所选区域 {region} 不一致，请刷新后重新选择。",
        )
    if not youyun_zone_supports_mode(selected, mode):
        is_pod = bool(selected.get("is_pod"))
        if mode == "pod" and not is_pod:
            raise HTTPException(status_code=422, detail=f"Pod 模式不能使用整机可用区 {zone}，请选择 Pod 可用区。")
        if mode == "uhost" and is_pod:
            raise HTTPException(status_code=422, detail=f"整机 UHost 模式不能使用 Pod 可用区 {zone}，请选择 UHost 可用区。")


def _youyun_capacity_http_error(exc: Any, *, gpu_type: str, zone: str) -> HTTPException:
    """Translate provider capacity failures without confusing support and stock."""
    message = str(getattr(exc, "message", "") or exc)
    if getattr(exc, "ret_code", None) == 230 and (
        "GpuType" in message or "machine type not configured" in message.lower()
    ):
        return HTTPException(
            status_code=422,
            detail=f"优云可用区 {zone} 不提供 GPU {gpu_type}，请选择该可用区支持的 GPU 或切换地域。",
        )
    return HTTPException(status_code=502, detail=str(exc))


async def resolve_youyun_resource(selection: YouyunResourceSelection | None) -> dict[str, Any]:
    """Resolve image-first mode/location and validate the exact resource tuple."""
    from backend.providers.youyun.models import YouyunApiError

    requested = selection or YouyunResourceSelection()
    try:
        validate_single_gpu_training(requested.gpuCount, requested.distributedStrategy)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        runner = build_youyun_runner()
    except YouyunApiError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    config = runner.manager.client.config
    configured_region = getattr(config, "region", "")
    configured_zone = getattr(config, "zone", "")
    configured_pod_zone = getattr(config, "pod_zone", "")
    configured_training_image = getattr(config, "training_image_id", "")
    configured_image = getattr(config, "image_id", "")
    configured_gpu_type = getattr(config, "gpu_type", "4090")
    selected_region = requested.region or configured_region
    if requested.purpose == "inference" and not requested.imageId:
        raise HTTPException(status_code=422, detail="优云远程推理必须选择官方 vLLM 镜像。")

    try:
        zones = await _cached_youyun_support_zones(runner.manager.client)
    except YouyunApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    # 修复前端状态串位时把可用区代码提交为 region 的情况（如 region=cn-bj2-03）。
    # 以优云可用区表为准自动纠错，避免任务提交被 422 卡死。
    requested_region, requested_zone = _normalize_youyun_region_zone(
        requested.region, requested.zone, zones
    )
    if requested_region and requested_region != (requested.region or ""):
        try:
            requested = requested.model_copy(update={"region": requested_region, "zone": requested_zone})
        except AttributeError:
            requested.region = requested_region
            requested.zone = requested_zone
    elif requested_zone and requested_zone != requested.zone:
        try:
            requested = requested.model_copy(update={"zone": requested_zone})
        except AttributeError:
            requested.zone = requested_zone
    selected_region = requested_region or configured_region
    images: list[dict[str, Any]] = []
    if requested.imageId:
        describe_images = getattr(runner.manager.client, "describe_images", None)
        if callable(describe_images):
            try:
                images = await _cached_youyun_images(runner.manager.client, selected_region)
            except YouyunApiError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc

    image_id = requested.imageId or configured_training_image or configured_image
    selected_image = next((item for item in images if item.get("image_id") == image_id), None)
    configured_default = image_id in {configured_training_image, configured_image}
    if selected_image is None and configured_default and requested.purpose != "inference":
        selected_image = {
            "image_id": image_id,
            "name": "LLaMA-Factory 社区训练镜像",
            "image_type": "Community",
            "container": True,
            "status": "Available",
            "tags": [],
            "description": "平台默认训练镜像",
        }
    if selected_image is None:
        raise HTTPException(status_code=409, detail="所选优云镜像不可用，请刷新后重试。")
    if requested.purpose == "inference":
        if not _is_official_vllm_image(selected_image):
            raise HTTPException(status_code=409, detail="所选优云官方 vLLM 镜像不可用，请刷新后重试。")
    elif not _youyun_image_is_selectable_for_training(selected_image):
        raise HTTPException(status_code=409, detail="所选优云镜像不支持远程训练，请选择容器训练镜像或 Ubuntu/第三方镜像。")

    requested_mode = requested.instanceMode if requested.instanceMode in {"uhost", "pod"} else None
    image_container = _youyun_image_container(selected_image)
    if requested.publicModelPath and image_container is not True:
        raise HTTPException(status_code=422, detail="优云公共模型库仅支持容器镜像，请选择支持公共模型库的优云镜像。")
    image_mode = None if image_container is None else ("pod" if image_container else "uhost")
    if image_mode is not None and requested_mode and requested_mode != image_mode:
        required = "Pod" if image_mode == "pod" else "UHost"
        raise HTTPException(status_code=422, detail=f"所选镜像仅支持 {required} 实例，请重新选择兼容的可用区。")

    locations = _youyun_location_options(zones, image_mode=image_mode, image=selected_image)
    # fusion: 挂接资源健康元数据，自动选址时跳过冷却中的可用区/机型组合
    for _location in locations:
        location_mode = youyun_zone_default_mode(
            {"zone": _location.get("zone"), "is_pod": _location.get("isPod")},
            image_mode or requested_mode,
        )
        _location["health"] = youyun_resource_health.metadata({
            "instanceMode": location_mode,
            "region": _location.get("region") or selected_region,
            "zone": _location.get("zone"),
            "imageId": requested.imageId or "",
            "gpuType": requested.gpuType or "",
            "chargeType": (getattr(requested, "chargeType", None) or "") if isinstance(requested, dict) is False else "",
        })
    selected_location = _find_location_option(locations, requested.zone)
    if selected_location is not None and selected_location.get("supported") is False:
        required = "Pod" if image_mode == "pod" else "UHost"
        raise HTTPException(status_code=422, detail=f"所选镜像与当前可用区不兼容，请选择 {required} 可用区。")
    health_skipped = False
    if selected_location is None:
        def _location_healthy(item: dict[str, Any]) -> bool:
            return (item.get("health") or {}).get("healthStatus") != "cooldown"

        def _healthy_first(pred: Any) -> dict[str, Any] | None:
            nonlocal health_skipped
            fallback_item: dict[str, Any] | None = None
            for item in locations:
                if not pred(item):
                    continue
                if _location_healthy(item):
                    return item
                if fallback_item is None:
                    fallback_item = item
            if fallback_item is not None:
                health_skipped = True
            return None

        def _any_first(pred: Any) -> dict[str, Any] | None:
            return next((item for item in locations if pred(item)), None)

        def _region_item(item: dict[str, Any]) -> bool:
            return item.get("region") == selected_region

        zone_item = lambda item: _region_item(item) and item.get("zone") == configured_zone and item.get("supported") is not False  # noqa: E731
        mode_item = lambda item: _region_item(item) and item.get("supported") is not False and ((image_mode or requested_mode) is None or (image_mode or requested_mode) in item.get("supportedModes", []))  # noqa: E731

        if requested_mode:
            selected_location = _healthy_first(mode_item)
        selected_location = selected_location or _healthy_first(zone_item)
        selected_location = selected_location or _healthy_first(lambda item: _region_item(item) and item.get("supported") is not False)
        # 全部健康候选都不可用时才退回到任意候选，避免完全阻塞任务提交。
        if selected_location is None:
            selected_location = (requested_mode and _any_first(mode_item)) or _any_first(zone_item) or _any_first(lambda item: _region_item(item) and item.get("supported") is not False)
    if selected_location is None:
        raise HTTPException(status_code=422, detail=f"区域 {selected_region} 没有可用区。")
    if selected_location.get("region") != selected_region:
        raise HTTPException(status_code=422, detail="所选地域与可用区不一致，请刷新后重试。")
    selected_zone = str(selected_location.get("zone") or "")
    location_zone = selected_zone
    if not selected_zone:
        raise HTTPException(status_code=422, detail="当前请求没有匹配的优云可用区。")
    selected_mode: Literal["pod", "uhost"] = youyun_zone_default_mode(
        {"zone": selected_location.get("zone"), "is_pod": selected_location.get("isPod")},
        image_mode or requested_mode,
    )
    if selected_region != configured_region or selected_zone != configured_zone:
        try:
            runner = build_youyun_runner(region=selected_region, zone=selected_zone)
        except YouyunApiError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        config = runner.manager.client.config

    image_name = requested.imageName or selected_image.get("name")
    image_version = requested.imageVersion or _youyun_image_version(selected_image)
    gpu_type = requested.gpuType or configured_gpu_type
    normalized_gpu_type = gpu_type.replace(" ", "").replace("_", "").replace("-", "").lower()
    if normalized_gpu_type == "p40":
        raise HTTPException(status_code=422, detail="当前平台 CUDA/vLLM 镜像未覆盖 P40 架构，请选择其他 GPU。")
    await _validate_youyun_mode_zone(
        runner.manager.client,
        mode=selected_mode,
        zone=selected_zone,
        region=selected_region,
        zones=zones,
    )
    try:
        capacity = await asyncio.to_thread(
            runner.manager.client.check_resource_capacity,
            gpu_type=gpu_type,
            image_id=image_id,
            zone=selected_zone,
        )
    except YouyunApiError as exc:
        raise _youyun_capacity_http_error(exc, gpu_type=gpu_type, zone=selected_zone) from exc
    specs = [spec for spec in capacity.specs if spec.resource_enough]
    if requested.mode == "manual":
        if requested.cpuCores is None or requested.memoryGb is None:
            raise HTTPException(status_code=422, detail="手动配置必须选择 CPU 核数和系统内存。")
        selected = next(
            (
                spec for spec in specs
                if spec.gpu == requested.gpuCount
                and spec.cpu == requested.cpuCores
                and spec.memory_gb == requested.memoryGb
            ),
            None,
        )
        if selected is None:
            raise HTTPException(status_code=409, detail="所选优云 GPU 规格当前无库存，请刷新后重试。")
        candidates = [selected]
    else:
        candidates = specs
        if not candidates:
            raise HTTPException(status_code=409, detail=f"优云 {gpu_type} 当前无可用库存，请稍后重试。")
    try:
        priced = [
            (
                candidate,
                await asyncio.to_thread(
                    runner.manager.client.get_instance_price,
                    gpu=candidate.gpu,
                    cpu=candidate.cpu,
                    memory_gb=candidate.memory_gb,
                    gpu_type=gpu_type,
                    image_id=image_id,
                    zone=selected_zone,
                ),
            )
            for candidate in candidates
        ]
    except YouyunApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    selected, price = min(priced, key=lambda item: (item[1].total, item[0].gpu, item[0].cpu, item[0].memory_gb))
    gpu_memory_gb = {
        "2080": 8, "2080ti": 11, "3080ti": 12, "3090": 24, "4090": 24,
        "409048g": 48, "5090": 32, "p40": 24, "v100s": 32, "a100": 80,
        "h100": 80, "h200": 141,
    }.get(normalized_gpu_type)
    return {
        "mode": requested.mode,
        "instanceMode": selected_mode,
        "region": selected_region,
        "zone": selected_zone,
        "locationZone": location_zone,
        "gpuType": gpu_type,
        "gpuMemoryGb": gpu_memory_gb,
        "gpuCount": selected.gpu,
        "distributedStrategy": requested.distributedStrategy,
        "cpuCores": selected.cpu,
        "memoryGb": selected.memory_gb,
        "imageId": image_id,
        "imageName": image_name,
        "imageVersion": image_version,
        "imageType": selected_image.get("image_type"),
        "container": _youyun_image_container(selected_image),
        "publicModelPath": requested.publicModelPath,
        "fallback": health_skipped and selected_location is not None and str(selected_location.get("zone") or "") != configured_zone,
        "chargeType": price.charge_type,
        "price": {
            "currency": price.currency,
            "instance": price.instance,
            "disks": price.disks,
            "systemDisks": price.system_disks,
            "image": price.image,
            "total": price.total,
        },
    }


@scoped_resource("task")
async def execute_claimed_training_task(task: dict[str, Any]) -> None:
    global active_process, active_task_id
    require_provider(task.get("provider"))
    task_id = task["id"]
    is_remote = task.get("provider") == "youyun" or task.get("executionMode") == "remote"
    if not is_remote:
        active_task_id = task_id
    try:
        if not is_remote:
            store.update(task_id, current_step="正在释放对话模型资源")
            async with chat_lock:
                await stop_chat_process()
        config = task_config(task_id, store.get_payload(task_id))
        jobs_dir = RUNTIME_ROOT / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        stop_flag_path = Path(config["stopFlagPath"])
        stop_flag_path.unlink(missing_ok=True)
        config_path = jobs_dir / f"{task_id}.json"
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        execution_task = {
            **config,
            "taskId": task_id,
            "framework": "llamafactory",
            "executionMode": task.get("executionMode") or "local",
            "provider": task.get("provider") or "local",
            "dataset": {"paths": {key: config[key] for key in ("trainPath", "validationPath", "testPath", "challengePath") if config.get(key)}},
        }
        if is_remote:
            parameters = dict(execution_task.get("parameters") or {})
            parameters["useCpu"] = False
            execution_task["parameters"] = parameters
            store.append_log(task_id, "远程优云 GPU 任务：创建一次性实例执行训练。")
            outcome = await run_youyun_training(task_id, execution_task)
        else:
            store.append_log(task_id, "准备 LLaMA-Factory YAML 配置和隔离工作目录。")
            store.update(task_id, current_step="正在启动 LLaMA-Factory 训练进程")
            outcome = await consume_executor_events(task_id, execution_task)
        if outcome == "completed":
            if not is_remote:
                active_process = None
                active_task_id = None
                store.update(task_id, state="completed", progress=100)
                await upload_completed_task_artifacts(task_id)
            else:
                # The remote runner already verified the adapter on the GPU
                # host. Mark the task complete now; SSH download and MinIO
                # upload continue in a detached, retryable background task.
                store.update(task_id, state="completed", progress=100)
                schedule_remote_artifact_sync(task_id)
        elif outcome == "stopped":
            await upload_completed_task_artifacts(task_id)
    except asyncio.CancelledError:
        if not is_remote and active_process and active_process.returncode is None:
            active_process.terminate()
            await active_process.wait()
        raise
    except Exception as exc:
        store.append_log(task_id, f"Worker 错误：{exc}")
        store.update(
            task_id,
            state="failed",
            current_step="任务启动失败",
            error=str(exc),
        )
    finally:
        current = store.get(task_id)
        if current and current.get("state") == "completed":
            try:
                await asyncio.to_thread(sync_model_assets, force=True)
            except Exception:
                logger.exception("训练完成后的模型资产同步失败：%s", task_id)
        try:
            (RUNTIME_ROOT / "jobs" / f"{task_id}.stop").unlink(missing_ok=True)
        except OSError:
            pass
        if not is_remote:
            active_process = None
            active_task_id = None


async def worker_loop() -> None:
    global active_task_id
    local_task: asyncio.Task[None] | None = None
    try:
        while True:
            if local_task is not None and local_task.done():
                await asyncio.gather(local_task, return_exceptions=True)
                local_task = None
            for task_id, task in list(remote_training_tasks.items()):
                if task.done():
                    await asyncio.gather(task, return_exceptions=True)
                    remote_training_tasks.pop(task_id, None)

            dispatched = False
            if local_runtime_enabled() and local_task is None and not inference_runtime.local_service_active:
                try:
                    local = store.claim_next(provider="local")
                except Exception as exc:
                    logger.warning("训练队列读取失败（local），将在下个轮询周期重试：%s", exc)
                    local = None
                if local is not None:
                    active_task_id = local["id"]
                    local_task = asyncio.create_task(
                        execute_claimed_training_task(local),
                        name=f"local-training-{local['id']}",
                    )
                    dispatched = True

            while True:
                try:
                    remote = store.claim_next(provider="youyun")
                except Exception as exc:
                    logger.warning("训练队列读取失败（youyun），将在下个轮询周期重试：%s", exc)
                    await asyncio.sleep(1.0)
                    break
                if remote is None:
                    break
                task = asyncio.create_task(
                    execute_claimed_training_task(remote),
                    name=f"youyun-training-{remote['id']}",
                )
                remote_training_tasks[remote["id"]] = task
                dispatched = True
                await asyncio.sleep(0)

            await asyncio.sleep(0.1 if dispatched else 0.8)
    finally:
        tasks = [task for task in [local_task, *remote_training_tasks.values()] if task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        remote_training_tasks.clear()


def create_dataset_minio_client() -> Any:
    try:
        from minio import Minio
    except ImportError as exc:
        raise RuntimeError("缺少 minio 依赖，请安装 requirements-storage.txt。") from exc
    config = model_storage_config
    missing = [
        key
        for key, value in {
            "MINIO_ENDPOINT": config.minio_endpoint,
            "MINIO_ACCESS_KEY": config.minio_access_key,
            "MINIO_SECRET_KEY": config.minio_secret_key,
            "MINIO_BUCKET": config.minio_bucket,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(f"数据集导入存储配置缺少：{', '.join(missing)}")
    return Minio(
        config.minio_endpoint,
        access_key=config.minio_access_key,
        secret_key=config.minio_secret_key,
        secure=config.minio_secure,
    )


def require_dataset_minio_client() -> Any:
    global dataset_minio_client, dataset_minio_error
    if dataset_minio_client is None:
        try:
            dataset_minio_client = create_dataset_minio_client()
            dataset_minio_error = None
        except Exception as exc:
            dataset_minio_error = str(exc)
            raise RuntimeError(dataset_minio_error) from exc
    return dataset_minio_client


def normalize_import_source_key(source_key: str) -> str:
    normalized = source_key.strip().lstrip("/")
    parts = Path(normalized).parts
    if not normalized.startswith("exports/") or ".." in parts or not normalized.lower().endswith(".jsonl"):
        raise ValueError("只能导入 MinIO rag-assets 桶 exports/ 目录下的 JSONL 文件。")
    return normalized


def dataset_import_source_sort_key(item: dict[str, Any]) -> tuple[bool, bool, float, str]:
    raw_last_modified = item.get("lastModified")
    timestamp: float | None = None
    if isinstance(raw_last_modified, str) and raw_last_modified:
        try:
            parsed = datetime.fromisoformat(raw_last_modified.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            timestamp = parsed.timestamp()
        except ValueError:
            pass
    return (
        not bool(item.get("supported")),
        timestamp is None,
        -timestamp if timestamp is not None else 0,
        str(item.get("key", "")).casefold(),
    )


def list_dataset_import_sources() -> list[dict[str, Any]]:
    client = require_dataset_minio_client()
    bucket = model_storage_config.minio_bucket
    objects: list[dict[str, Any]] = []
    for item in client.list_objects(bucket, prefix="exports/", recursive=True):
        key = str(getattr(item, "object_name", ""))
        if not key or key.endswith("/"):
            continue
        last_modified = getattr(item, "last_modified", None)
        objects.append(
            {
                "key": key,
                "name": Path(key).name,
                "sizeBytes": int(getattr(item, "size", 0) or 0),
                "lastModified": last_modified.isoformat() if last_modified else None,
                "supported": key.lower().endswith(".jsonl"),
            }
        )
    return sorted(objects, key=dataset_import_source_sort_key)


def import_dataset_from_minio(source_key: str, name: str, structure: str) -> dict[str, Any]:
    safe_key = normalize_import_source_key(source_key)
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("请填写数据集名称。")
    if structure not in {"SFT", "DPO"}:
        raise ValueError("数据结构只能是 SFT 或 DPO。")

    dataset_id = f"ds-{uuid.uuid4().hex[:12]}"
    dataset_dir = DATASET_ROOT / dataset_id
    dataset_dir.mkdir(parents=True, exist_ok=False)
    source_path = dataset_dir / Path(safe_key).name
    response = None
    size = 0
    digest = hashlib.sha256()
    try:
        client = require_dataset_minio_client()
        response = client.get_object(model_storage_config.minio_bucket, safe_key)
        with source_path.open("wb") as target:
            while True:
                chunk = response.read(8 * 1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
                if size > 2 * 1024 * 1024 * 1024:
                    raise ValueError("数据文件不能超过 2GB。")
                target.write(chunk)
        report = validate_text_dataset_report(source_path, structure)
        sample_count = int(report["checkedRows"])
        validation_error = report["errors"][0]["message"] if report["errors"] else None
        return store.create_dataset(
            {
                "id": dataset_id,
                "name": clean_name,
                "type": "文本生成",
                "structure": structure,
                "state": "failed" if validation_error else "ready",
                "sampleCount": sample_count,
                "paths": {"trainPath": str(source_path)},
                "sourceType": "minio",
                "sourceBucket": model_storage_config.minio_bucket,
                "sourceKey": safe_key,
                "sourceUri": f"s3://{model_storage_config.minio_bucket}/{safe_key}",
                "sizeBytes": size,
                "contentSha256": digest.hexdigest(),
                "validationMessage": validation_error or "格式校验通过，可以发布。",
                "validationReport": report,
                "warningCount": 0,
                "owner": "平台管理员",
                "builtin": False,
            }
        )
    except Exception:
        shutil.rmtree(dataset_dir, ignore_errors=True)
        raise
    finally:
        if response is not None:
            response.close()
            response.release_conn()


def clone_base_name(name: str) -> str:
    return re.sub(r"\s+副本(?:\s*\d+)?$", "", name).strip() or name.strip()


def _normalize_youyun_gpu_name(value: str) -> str:
    return value.replace(" ", "").replace("_", "").replace("-", "").lower()


def _youyun_gpu_platform_support(value: str) -> dict[str, Any]:
    normalized = _normalize_youyun_gpu_name(value)
    if normalized == "p40":
        return {
            "trainingSupported": False,
            "inferenceSupported": False,
            "supportReason": "当前平台 CUDA/vLLM 镜像未覆盖 P40 架构",
        }
    if normalized.startswith("v100"):
        return {
            "trainingSupported": True,
            "inferenceSupported": False,
            "supportReason": "当前优云官方 vLLM 镜像未包含 V100S（计算能力 7.0）CUDA 内核",
        }
    return {
        "trainingSupported": True,
        "inferenceSupported": True,
        "supportReason": None,
    }


def next_clone_name(dataset: dict[str, Any], existing: list[dict[str, Any]]) -> str:
    base_name = clone_base_name(str(dataset["name"]))
    used_numbers = {
        int(match.group(1))
        for item in existing
        if (match := re.fullmatch(rf"{re.escape(base_name)}\s+副本(\d+)", str(item.get("name", ""))))
    }
    number = 1
    while number in used_numbers:
        number += 1
    return f"{base_name} 副本{number}"


@asynccontextmanager
async def lifespan(_: FastAPI):
    global worker_task, model_uploader, model_storage_error, dataset_minio_client, dataset_minio_error, retention_task, youyun_watchdog_task
    personal_youyun.credential_store = personal_youyun.CredentialStore(STORAGE_BACKEND, DATABASE_PATH, os.environ.get("POSTGRES_DSN", ""), RUNTIME_ROOT)
    await asyncio.to_thread(personal_youyun.credential_store.initialize)
    await asyncio.to_thread(auth_store.initialize)
    await asyncio.to_thread(audit_store.initialize)
    try:
        model_uploader = await asyncio.to_thread(
            ModelArtifactUploader, model_storage_config
        )
        await asyncio.to_thread(model_uploader.initialize)
        model_storage_error = None
    except Exception as exc:
        model_uploader = None
        model_storage_error = str(exc)
    try:
        dataset_minio_client = await asyncio.to_thread(create_dataset_minio_client)
        dataset_minio_error = None
    except Exception as exc:
        dataset_minio_client = None
        dataset_minio_error = str(exc)
    try:
        await asyncio.to_thread(sync_model_assets, force=True)
    except Exception:
        logger.exception("模型资产目录启动同步失败；首次读取时将重试。")
    reconcile = getattr(store, "reconcile_interrupted_tasks", None)
    if callable(reconcile):
        await asyncio.to_thread(reconcile)
    await reconcile_interrupted_youyun_instances()
    await warmup_retained_comparisons()
    await inference_runtime.startup()
    await vision_training_runtime.start()
    await image_generation_runtime.start()
    await image_generation_service_runtime.startup()
    await image_generation_training_runtime.start()
    await image_generation_evaluation_runtime.start()
    async def retention_loop() -> None:
        while True:
            try:
                await asyncio.to_thread(cleanup_runtime_logs, backend=STORAGE_BACKEND, sqlite_path=DATABASE_PATH, postgres_dsn=os.environ.get("POSTGRES_DSN", ""))
                await asyncio.to_thread(audit_store.cleanup)
            except Exception:
                pass
            await asyncio.sleep(24 * 60 * 60)
    retention_task = asyncio.create_task(retention_loop(), name="retention-cleanup")

    async def youyun_release_loop() -> None:
        while True:
            try:
                await auto_release_expired_youyun_instances()
            except Exception:
                pass
            await asyncio.sleep(YOUYUN_RETENTION_CHECK_MINUTES * 60)

    youyun_release_task = asyncio.create_task(
        youyun_release_loop(), name="youyun-instance-auto-release"
    )
    worker_task = asyncio.create_task(worker_loop(), name="single-gpu-worker")
    youyun_watchdog_task = asyncio.create_task(youyun_cost_watchdog(), name="youyun-cost-watchdog")
    yield
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass
    if retention_task is not None:
        retention_task.cancel()
        try:
            await retention_task
        except asyncio.CancelledError:
            pass
    youyun_release_task.cancel()
    try:
        await youyun_release_task
    except asyncio.CancelledError:
        pass
    for manager in list(_remote_chat_managers.values()):
        await asyncio.to_thread(manager.close_all)
    _remote_chat_managers.clear()
    if _remote_chat_manager is not None:
        await asyncio.to_thread(_remote_chat_manager.close_all)
    async with chat_lock:
        await stop_chat_process()
    await image_generation_runtime.stop()
    await image_generation_service_runtime.shutdown()
    await image_generation_training_runtime.stop()
    await image_generation_evaluation_runtime.stop()
    await vision_training_runtime.stop()
    if youyun_watchdog_task is not None:
        youyun_watchdog_task.cancel()
        try:
            await youyun_watchdog_task
        except asyncio.CancelledError:
            pass
        youyun_watchdog_task = None
    await inference_runtime.shutdown()
    if STORAGE_BACKEND == "postgres":
        await asyncio.to_thread(close_pg_pools)


app = FastAPI(title="训推平台本地训练 API", version="0.1.0", lifespan=lifespan)
app.include_router(create_speech_router())
PUBLIC_API_PATHS = {
    "/api/health",
    "/api/auth/login",
    "/api/auth/callback",
    "/api/auth/me",
    "/api/auth/logout",
}
request_user_context: ContextVar[dict[str, Any] | None] = ContextVar(
    "request_user_context", default=None
)


def authenticated_user_or_default() -> dict[str, Any]:
    return request_user_context.get() or {"name": "平台管理员"}


def websocket_authenticated(websocket: WebSocket) -> bool:
    """Apply the same cookie/gateway authentication as HTTP API requests."""
    if AUTH_MODE == "disabled":
        return True
    if session_manager.read(websocket.cookies.get(AUTH_COOKIE_NAME)) is not None:
        return True
    return bool(
        websocket.headers.get("x-control-user-id")
        or websocket.headers.get("x-control-username")
    )


def record_audit(action: str, object_type: str, object_id: str, *, result: str = "success", error: str | None = None, details: dict[str, Any] | None = None) -> None:
    try:
        audit_store.record(actor=authenticated_user_or_default(), action=action, object_type=object_type, object_id=object_id, result=result, error=error, details=details)
    except Exception:
        # 审计故障不能阻断核心业务动作；错误仍由应用日志暴露。
        pass


def gateway_user_from_request(request: Request) -> dict[str, Any] | None:
    """Read the identity injected by the Control Center application gateway."""
    # 网关转发的中文名可能带 URL 编码（如 %E5%88%98…），统一解码后再入库。
    user_id = urllib.parse.unquote(request.headers.get("x-control-user-id", "")).strip()
    username = urllib.parse.unquote(request.headers.get("x-control-username", "")).strip()
    if not user_id and not username:
        return None
    roles = request.headers.get("x-control-roles", "").strip()
    return {
        "id": user_id or username,
        "uid": user_id or username,
        "sub": user_id or username,
        "name": username or user_id,
        "roles": [role for role in roles.split(",") if role],
    }


def frontend_redirect(request: Request, query: str = "") -> str:
    target = FRONTEND_URL or str(request.base_url).rstrip("/")
    return f"{target}/?{query}" if query else f"{target}/"


def _set_oauth_cookie(response: Response, name: str, value: str) -> None:
    response.set_cookie(
        name,
        value,
        max_age=OAUTH_STATE_TTL,
        httponly=True,
        secure=SESSION_SECURE,
        samesite="lax",
        path="/",
    )


def _oauth_failure_redirect(request: Request, message: str) -> Response:
    response = RedirectResponse(
        frontend_redirect(request, urllib.parse.urlencode({"auth_error": message})),
        status_code=303,
    )
    response.delete_cookie(OAUTH_STATE_COOKIE_NAME, path="/")
    response.delete_cookie(OAUTH_VERIFIER_COOKIE_NAME, path="/")
    return response


@app.middleware("http")
async def require_local_session(request: Request, call_next):
    if request.method == "OPTIONS" or request.url.path in PUBLIC_API_PATHS:
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        user = gateway_user_from_request(request)
        user = user or (None if AUTH_MODE == "disabled" else session_manager.read(request.cookies.get(AUTH_COOKIE_NAME)))
        if AUTH_MODE == "disabled":
            user = user or {"name": "平台管理员", "id": "local-admin", "uid": "local-admin", "sub": "local-admin"}
        if user is None:
            return JSONResponse(status_code=401, content={"detail": "请先完成中台登录。"})
        request.state.user = user
        token = request_user_context.set(user)
        account_token = personal_youyun.current_user.set(str(user.get("sub") or user.get("uid") or user.get("id")))
        resource_token = None
        try:
            try:
                resource_id = personal_youyun.check_request_access(request.method, request.url.path)
                if resource_id:
                    resource_token = personal_youyun.current_resource.set(resource_id)
            except personal_youyun.AccountAccessError as exc:
                return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
            return await call_next(request)
        finally:
            if resource_token is not None:
                personal_youyun.current_resource.reset(resource_token)
            personal_youyun.current_user.reset(account_token)
            request_user_context.reset(token)
    return await call_next(request)


app.include_router(personal_youyun.create_settings_router())

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(
    create_inference_router(
        inference_runtime,
        audit_callback=record_audit,
        websocket_auth=websocket_authenticated,
    )
)

# ==== fusion: 视觉/文生图路由挂载 ====
app.include_router(
    create_image_generation_router(
        store,
        inference_store,
        task_runtime=image_generation_runtime,
        service_runtime=image_generation_service_runtime,
        result_root=(RUNTIME_ROOT / "image-generation-results").resolve(),
        evaluation_runtime=image_generation_evaluation_runtime,
        evaluation_root=(RUNTIME_ROOT / "image-generation-evaluation-results").resolve(),
        youyun_resource_resolver=lambda resource: resolve_youyun_resource(
            _youyun_resource_selection(resource)
        ),
        youyun_image_checker=check_image_generation_image,
        community_image_verification_lookup=community_image_verification,
    )
)
app.include_router(
    create_image_generation_training_router(
        store,
        image_generation_training_runtime,
        runtime_root=RUNTIME_ROOT,
        youyun_resource_resolver=lambda resource: resolve_youyun_resource(
            _youyun_resource_selection(resource)
        ),
        youyun_image_validator=validate_image_generation_training_image,
    )
)
app.include_router(
    create_vision_router(
        store,
        inference_runtime,
        project_root=PROJECT_ROOT,
        dataset_root=DATASET_ROOT,
        runtime_root=RUNTIME_ROOT,
        training_runtime=vision_training_runtime,
        gpu_probe=lambda: probe_gpu_info(),
        youyun_config_check=lambda: YouyunConfig.from_env(PROJECT_ROOT / ".env").missing_keys(),
        youyun_resource_resolver=lambda resource: resolve_youyun_resource(
            _youyun_resource_selection(resource)
        ),
        youyun_vision_probe=run_visual_youyun_probe,
        youyun_image_validator=validate_youyun_training_image,
        youyun_model_catalog_loader=load_youyun_model_repository_for_vision,
    )
)



# ==== fusion: 平台概览/数据集媒体/社区镜像路由 ====
@app.get("/api/youyun/community-images")
async def youyun_community_images(
    purpose: str = "vision_training",
    keyword: str | None = None,
    gpuType: str | None = None,
    region: str | None = None,
    zone: str | None = None,
) -> dict[str, Any]:
    """查询优云社区镜像；社区目录不可用时不影响平台镜像流程。"""
    if purpose not in {"vision_training", "image_generation", "image_generation_training", "training", "inference"}:
        raise HTTPException(status_code=422, detail="purpose 参数不合法。")
    try:
        runner = build_youyun_runner(region=region, zone=zone)
        client = runner.manager.client
        rows = await asyncio.to_thread(client.describe_community_images, keyword=keyword, limit=100)
    except Exception as exc:  # noqa: BLE001 - 社区目录是增强能力，保留平台回退
        return {"source": "community", "available": False, "error": str(exc), "images": []}

    community_image_ids.update(str(item.get("image_id") or "") for item in rows if item.get("image_id"))

    selected_gpu = gpuType or getattr(client.config, "gpu_type", "")
    workload = "image_generation_training" if purpose in {"image_generation", "image_generation_training"} else purpose
    verification_by_id = {
        str(item.get("image_id") or ""): community_image_verification(
            workload,
            str(item.get("image_id") or ""),
            str(region or client.config.region or ""),
            str(zone or client.config.zone or ""),
            str(selected_gpu or ""),
        )
        for item in rows
        if item.get("image_id")
    }
    ranked = rank_community_image_candidates(
        rows,
        workload=workload,
        gpu_type=selected_gpu,
        verification_by_id=verification_by_id,
    )
    normalized: list[dict[str, Any]] = []
    for item in ranked:
        image_id = str(item.get("image_id") or "")
        verification = verification_by_id.get(image_id, {})
        status = str(item.get("status") or "Unknown")
        available = bool(image_id) and status.casefold() in {"available", "normal", "ready"}
        supported = [str(value) for value in item.get("supported_gpu_types") or []]
        gpu_supported = not supported or selected_gpu.casefold() in {value.casefold() for value in supported}
        normalized.append(
            {
                "id": image_id or None,
                "groupId": item.get("group_id") or None,
                "name": item.get("name") or "未命名社区镜像",
                "versionName": item.get("version_name") or "",
                "status": status,
                "tags": item.get("tags") or [],
                "description": item.get("description") or "",
                "price": item.get("price"),
                "supportedGpuTypes": supported,
                "container": bool(item.get("container")),
                "autoStart": bool(item.get("auto_start")),
                "gpuSupported": gpu_supported,
                "available": available and gpu_supported,
                "verificationState": verification.get("state", "unverified"),
                "verifiedAt": verification.get("verifiedAt"),
                "verificationExpiresAt": verification.get("expiresAtEpoch"),
                "verificationMessage": verification.get("message"),
                "compatible": bool(item.get("compatible")),
                "selectable": bool(item.get("selectable")),
                "matchScore": item.get("matchScore", 0),
                "recommendationReason": item.get("recommendationReason"),
            }
        )
    return {"source": "community", "available": True, "region": client.config.region, "zone": client.config.zone, "images": normalized}
@app.post("/api/youyun/community-images/probe")
async def probe_youyun_community_image(payload: YouyunCommunityImageProbeRequest) -> dict[str, Any]:
    resource = payload.resource.model_dump(exclude_none=True)
    resource["imageId"] = payload.imageId
    required = ("region", "zone", "gpuType", "gpuCount", "cpuCores", "memoryGb", "chargeType")
    missing = [key for key in required if resource.get(key) in (None, "")]
    if missing:
        raise HTTPException(status_code=422, detail=f"社区镜像探针资源缺少字段：{', '.join(missing)}")
    try:
        runner = build_youyun_runner(
            region=str(resource["region"]),
            zone=str(resource["zone"]),
            charge_type=str(resource["chargeType"]),
        )
        rows = await asyncio.to_thread(runner.manager.client.describe_community_images, limit=100)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"优云社区镜像目录查询失败：{type(exc).__name__}") from exc
    image = next((item for item in rows if str(item.get("image_id") or "") == payload.imageId), None)
    if image is None:
        raise HTTPException(status_code=404, detail="所选社区镜像不在优云当前目录中。")
    match = rank_community_image_candidates(
        [image],
        workload=payload.purpose,
        gpu_type=str(resource.get("gpuType") or ""),
    )[0]
    if not match.get("compatible"):
        raise HTTPException(status_code=409, detail=match.get("recommendationReason") or "所选社区镜像与 SDXL LoRA 训练不兼容。")
    community_image_ids.add(payload.imageId)
    try:
        return await run_image_generation_youyun_probe(
            ImageGenerationProbeRequest(payload.modelRepoId),
            resource,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
@app.get("/api/platform/datasets")
async def platform_datasets(
    domain: Literal["all", "nlp", "vision", "image-generation"] = "all",
    state: str | None = Query(default=None),
    query: str | None = Query(default=None, max_length=120),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    payload = await asyncio.to_thread(store.list_datasets)
    return build_dataset_listing(
        payload,
        domain=domain,
        state=state,
        query=query,
        page=page,
        page_size=pageSize,
        updated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
    )
def _page_view(items: list[dict[str, Any]], page: int | None, pageSize: int) -> Any:
    """Serve both legacy list consumers and page-based consumers of /api/model-assets."""
    if page is None:
        return items
    total = len(items)
    start = (page - 1) * pageSize
    return {
        "items": items[start : start + pageSize],
        "total": total,
        "page": page,
        "pageSize": pageSize,
        "totalPages": max(1, (total + pageSize - 1) // pageSize),
    }
@app.get("/api/datasets/{dataset_id}/media/{media_path:path}")
async def dataset_media(dataset_id: str, media_path: str) -> FileResponse:
    dataset = store.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="数据集不存在。")
    raw_media_root = (dataset.get("paths") or {}).get("mediaRoot")
    if not isinstance(raw_media_root, str) or not raw_media_root.strip():
        raise HTTPException(status_code=404, detail="该数据集没有可访问的图片目录。")
    media_root = Path(raw_media_root).expanduser().resolve()
    if not media_root.is_relative_to(DATASET_ROOT.expanduser().resolve()):
        raise HTTPException(status_code=403, detail="数据集图片目录无效。")
    try:
        safe_path = safe_visual_member(media_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if safe_path.parts[:1] == ("images",):
        safe_path = Path(*safe_path.parts[1:])
    requested = (media_root / safe_path).resolve()
    if not requested.is_relative_to(media_root) or not requested.is_file():
        raise HTTPException(status_code=404, detail="数据集图片不存在。")
    return FileResponse(requested)
@app.get("/api/platform/tasks")
async def platform_tasks(
    modality: Literal["all", "nlp", "vision", "image-generation"] = "all",
    state: str | None = Query(default=None),
    query: str | None = Query(default=None, max_length=120),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    async def read(reader: Any, **kwargs: Any) -> list[dict[str, Any]]:
        try:
            return list(await asyncio.to_thread(reader, **kwargs) or [])
        except Exception:
            return []

    nlp_tasks, vision_tasks, image_generation_tasks = await asyncio.gather(
        read(store.list, include_details=False),
        read(vision_training_runtime.list_tasks),
        read(image_generation_training_runtime.list_tasks, include_archived=False),
    )
    safe_state = state if isinstance(state, str) else None
    safe_query = query if isinstance(query, str) else None
    safe_page = page if isinstance(page, int) else 1
    safe_page_size = pageSize if isinstance(pageSize, int) else 20
    return build_task_listing(
        nlp_tasks=nlp_tasks,
        vision_tasks=vision_tasks,
        image_generation_tasks=image_generation_tasks,
        modality=modality,
        state=safe_state,
        query=safe_query,
        page=safe_page,
        page_size=safe_page_size,
        updated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
    )
@app.get("/api/platform/services")
async def platform_services(
    modality: Literal["all", "nlp", "vision", "image-generation"] = "all",
    state: str | None = Query(default=None),
    query: str | None = Query(default=None, max_length=120),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    try:
        services = list(await asyncio.to_thread(inference_store.list, include_archived=True) or [])
    except Exception:
        services = []
    return build_service_listing(
        services,
        modality=modality,
        state=state,
        query=query,
        page=page,
        page_size=pageSize,
        updated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
    )
async def _read_overview_domain(
    source: str,
    reader: Any,
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    try:
        value = await asyncio.to_thread(reader, **kwargs)
        return list(value or []), None
    except Exception:
        module = {
            "datasets": "datasets",
            "tasks": "training",
            "models": "models",
            "services": "inference",
        }[source]
        return [], {
            "id": f"overview-{source}-read-failed",
            "source": source,
            "severity": "warning",
            "module": module,
            "title": f"{source}概览数据暂不可用",
            "detail": "该领域读取失败，已保留其他领域概览；请稍后刷新或检查存储连接。",
        }
@app.get("/api/platform/overview")
async def platform_overview() -> dict[str, Any]:
    results = await asyncio.gather(
        _read_overview_domain("datasets", store.list_datasets),
        _read_overview_domain("tasks", store.list, include_details=False),
        _read_overview_domain("tasks", vision_training_runtime.list_tasks),
        _read_overview_domain("tasks", image_generation_training_runtime.list_tasks, include_archived=False),
        _read_overview_domain("models", store.list_model_assets, include_archived=False),
        _read_overview_domain("services", inference_store.list, include_archived=False),
    )
    datasets_payload = results[0][0]
    tasks_payload = results[1][0]
    vision_tasks_payload = results[2][0]
    image_generation_tasks_payload = results[3][0]
    model_assets_payload = results[4][0]
    services_payload = results[5][0]
    domain_issues = [result[1] for result in results if result[1] is not None]
    gpu_payload = await asyncio.to_thread(probe_gpu_info)
    active_service = next(
        (service for service in services_payload if service.get("state") == "running"),
        None,
    )
    inference_metrics_payload = None
    if active_service is not None:
        try:
            inference_metrics_payload = await inference_runtime.metrics(active_service["id"])
        except (KeyError, OSError, RuntimeError, TimeoutError):
            inference_metrics_payload = None
    return build_overview_snapshot(
        datasets=datasets_payload,
        tasks=tasks_payload,
        vision_tasks=vision_tasks_payload,
        image_generation_tasks=image_generation_tasks_payload,
        model_assets=model_assets_payload,
        services=services_payload,
        health=health_snapshot(),
        gpu=gpu_payload,
        shared_storage={
            "status": "ready" if model_uploader is not None else "unavailable",
            "error": model_storage_error,
        },
        inference_metrics=inference_metrics_payload,
        generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        domain_issues=domain_issues,
    )

@app.get("/api/auth/login")
async def oauth_login(response: Response) -> Response:
    try:
        client = await asyncio.to_thread(auth_config_store.load)
    except OAuthLoginError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    state = secrets.token_urlsafe(32)
    verifier, challenge = create_pkce()
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client.client_id,
            "redirect_uri": client.redirect_uri,
            "scope": "profile roles",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    redirect = RedirectResponse(f"{OAUTH_AUTHORIZE_URL}?{query}", status_code=302)
    _set_oauth_cookie(redirect, OAUTH_STATE_COOKIE_NAME, state)
    _set_oauth_cookie(redirect, OAUTH_VERIFIER_COOKIE_NAME, verifier)
    return redirect


async def _oauth_callback(
    request: Request,
    code: str | None,
    state: str | None,
    error: str | None,
    error_description: str | None,
) -> Response:
    expected_state = request.cookies.get(OAUTH_STATE_COOKIE_NAME)
    verifier = request.cookies.get(OAUTH_VERIFIER_COOKIE_NAME)
    if not expected_state or not state or not hmac.compare_digest(state, expected_state):
        return _oauth_failure_redirect(request, "OAuth state 校验失败")
    if error or not code or not verifier:
        return _oauth_failure_redirect(request, "中台授权失败，请重新登录")
    try:
        client = await asyncio.to_thread(auth_config_store.load)
        token_response = await asyncio.to_thread(
            exchange_code,
            token_url=OAUTH_TOKEN_URL,
            client_id=client.client_id,
            redirect_uri=client.redirect_uri,
            code=code,
            code_verifier=verifier,
            timeout=OAUTH_TIMEOUT,
        )
        profile = await asyncio.to_thread(
            fetch_userinfo,
            userinfo_url=OAUTH_USERINFO_URL,
            access_token=str(token_response["access_token"]),
            timeout=OAUTH_TIMEOUT,
        )
        user = await asyncio.to_thread(auth_store.upsert_user, profile)
    except OAuthLoginError:
        return _oauth_failure_redirect(request, "中台登录失败，请重新登录")
    redirect = RedirectResponse(frontend_redirect(request), status_code=303)
    redirect.delete_cookie(OAUTH_STATE_COOKIE_NAME, path="/")
    redirect.delete_cookie(OAUTH_VERIFIER_COOKIE_NAME, path="/")
    redirect.set_cookie(
        AUTH_COOKIE_NAME,
        session_manager.create(user),
        max_age=session_manager.ttl_seconds,
        httponly=True,
        secure=SESSION_SECURE,
        samesite="lax",
        path="/",
    )
    return redirect


@app.get("/api/auth/callback")
async def oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> Response:
    return await _oauth_callback(request, code, state, error, error_description)


@app.get("/oauth/callback")
async def oauth_callback_alias(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> Response:
    return await _oauth_callback(request, code, state, error, error_description)


@app.get("/")
async def root_oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> Response:
    if not any((code, state, error, error_description)):
        return RedirectResponse(frontend_redirect(request), status_code=303)
    return await _oauth_callback(request, code, state, error, error_description)


@app.get("/api/auth/me")
async def auth_me(request: Request) -> dict[str, Any]:
    user = gateway_user_from_request(request)
    user = user or (None if AUTH_MODE == "disabled" else session_manager.read(request.cookies.get(AUTH_COOKIE_NAME)))
    if AUTH_MODE == "disabled":
        user = user or {"id": "local-admin", "uid": "local-admin", "sub": "local-admin", "name": "平台管理员", "email": None}
    if user is None:
        raise HTTPException(status_code=401, detail="请先完成中台登录。")
    return {"id": user.get("uid"), "sub": user.get("sub"), "name": user.get("name"), "email": user.get("email"), "expiresAt": user.get("exp")}


@app.post("/api/auth/logout")
async def auth_logout() -> Response:
    response = JSONResponse({"ok": True})
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")
    return response


_youyun_gpu_specs_cache: dict[tuple[str, ...], tuple[float, dict[str, Any]]] = {}
_youyun_gpu_specs_cache_ttl = max(0.0, float(os.environ.get("XUNTUI_YOUYUN_GPU_SPECS_CACHE_SECONDS", "30")))
_youyun_capacity_cache: dict[tuple[str, ...], tuple[float, Any]] = {}
_youyun_capacity_pending: dict[tuple[str, ...], asyncio.Future[Any]] = {}
_youyun_capacity_cache_ttl = max(0.0, float(os.environ.get("XUNTUI_YOUYUN_CAPACITY_CACHE_SECONDS", "10")))


async def _cached_youyun_capacity(
    client: Any,
    *,
    gpu_type: str,
    image_id: str,
    charge_type: str,
    region: str,
    zone: str,
) -> Any:
    """Cache/coalesce short-lived capacity probes used by the GPU card strip."""
    identity = _youyun_cache_identity(client)
    if identity is None:
        try:
            return await asyncio.to_thread(
                client.check_resource_capacity,
                gpu_type=gpu_type,
                image_id=image_id,
                charge_type=charge_type,
                region=region,
                zone=zone,
            )
        except TypeError:
            return await asyncio.to_thread(
                client.check_resource_capacity,
                gpu_type=gpu_type,
                image_id=image_id,
            )
    key = (identity, gpu_type, image_id, charge_type, region, zone)
    now = time.monotonic()
    cached = _youyun_capacity_cache.get(key)
    if _youyun_capacity_cache_ttl > 0 and cached is not None and now - cached[0] < _youyun_capacity_cache_ttl:
        return cached[1]
    pending = _youyun_capacity_pending.get(key)
    if pending is not None:
        return await asyncio.shield(pending)
    loop = asyncio.get_running_loop()
    future: asyncio.Future[Any] = loop.create_future()
    _youyun_capacity_pending[key] = future
    try:
        try:
            value = await asyncio.to_thread(
                client.check_resource_capacity,
                gpu_type=gpu_type,
                image_id=image_id,
                charge_type=charge_type,
                region=region,
                zone=zone,
            )
        except TypeError:
            # Lightweight provider fakes and older clients only accept the core
            # placement pair; mirror the discovery helpers' compatibility fallback.
            value = await asyncio.to_thread(
                client.check_resource_capacity,
                gpu_type=gpu_type,
                image_id=image_id,
            )
        if _youyun_capacity_cache_ttl > 0:
            _youyun_capacity_cache[key] = (time.monotonic(), value)
        future.set_result(value)
        return value
    except BaseException as exc:
        # Always settle shared waiters, including owner cancellation.
        future.set_exception(exc)
        # Consume the exception for any waiter that races with this owner.
        future.exception()
        raise
    finally:
        _youyun_capacity_pending.pop(key, None)


@app.get("/api/youyun/gpu-specs")
async def youyun_gpu_specs(
    gpuType: str | None = None,
    imageId: str | None = None,
    purpose: str = "training",
    instanceMode: Literal["uhost", "pod", "auto"] | None = None,
    chargeType: Literal["Postpay", "Spot"] | None = None,
    region: str | None = None,
    zone: str | None = None,
    includeCommunity: bool = True,
) -> dict[str, Any]:
    """前端远程任务表单展示 GPU 机型与实时库存。"""
    from backend.providers.youyun.models import YouyunApiError

    specs_cache_key = (
        gpuType or "", imageId or "", purpose, instanceMode or "",
        chargeType or "", region or "", zone or "", str(includeCommunity),
    )
    cached_specs = _youyun_gpu_specs_cache.get(specs_cache_key)
    if _youyun_gpu_specs_cache_ttl > 0 and cached_specs is not None and (time.monotonic() - cached_specs[0]) < _youyun_gpu_specs_cache_ttl:
        return cached_specs[1]

    try:
        base_config = YouyunConfig.from_env()
        selected_charge_type = chargeType or base_config.charge_type
        runner = build_youyun_runner(charge_type=selected_charge_type)
    except YouyunApiError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    config = runner.manager.client.config
    selected_region = region.strip() if region and region.strip() else config.region
    requested_auto_mode = instanceMode == "auto" or instanceMode is None
    selected_mode = instanceMode if instanceMode in {"uhost", "pod"} else "pod"
    selected_zone = zone.strip() if zone and zone.strip() else config.zone
    try:
        # The selector needs the complete provider catalog so a region switch
        # can be handled without a second discovery request. Inventory and
        # pricing remain scoped to the selected region/zone below.
        zones = await _cached_youyun_support_zones(runner.manager.client)
    except AttributeError:
        zones = []
    support_zones = [dict(item) for item in (zones or [])]
    normalized_region, normalized_zone = _normalize_youyun_region_zone(
        region, zone, support_zones
    )
    selected_region = normalized_region or selected_region or config.region
    if normalized_zone:
        selected_zone = normalized_zone
    image_id = imageId or getattr(config, "training_image_id", "") or config.image_id
    try:
        image_catalog = await _cached_youyun_images(runner.manager.client, selected_region)
    except YouyunApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    selected_image = next((item for item in image_catalog if item.get("image_id") == image_id), None)
    if selected_image is None and image_id in {getattr(config, "training_image_id", ""), config.image_id}:
        selected_image = {
            "image_id": image_id,
            "name": "LLaMA-Factory 社区训练镜像",
            "image_type": "Community",
            "container": True,
        }
    # ImageType is a safe fallback for System/Other records when older provider
    # responses omit the explicit Container flag. App/Community records without
    # that flag stay mode-neutral so automatic placement can still probe the
    # provider's healthy zone (real records normally carry ``Container``).
    image_container = _youyun_image_container(selected_image) if selected_image else None
    if image_container is not None:
        image_mode = "pod" if image_container else "uhost"
    elif selected_image and str(selected_image.get("image_type") or "").strip().lower() in {"system", "other"}:
        image_mode = "uhost"
    else:
        image_mode = None
    if image_mode is not None:
        selected_mode = image_mode
        requested_auto_mode = False
    health_image = imageId or config.image_id
    health_gpu = gpuType or config.gpu_type
    fallback_reason = ""
    if zones:
        zones = [
            {
                **item,
                "supportedModes": list(youyun_zone_modes(item)),
                "capabilities": youyun_zone_capabilities(item),
                **youyun_resource_health.metadata({
                    "instanceMode": youyun_zone_default_mode(item, image_mode or selected_mode),
                    "region": str(item.get("region") or selected_region),
                    "zone": item.get("zone"),
                    "imageId": health_image,
                    "gpuType": health_gpu,
                    "chargeType": selected_charge_type,
                }),
            }
            for item in zones
        ]
        healthy_zones = [item for item in zones if item.get("healthStatus") != "cooldown"]
        if requested_auto_mode and image_mode is None:
            preferred_healthy = [item for item in healthy_zones if item.get("region") == selected_region and youyun_zone_supports_mode(item, "pod")]
            if not preferred_healthy:
                preferred_healthy = [item for item in healthy_zones if item.get("region") == selected_region and youyun_zone_supports_mode(item, "uhost")]
                if preferred_healthy and selected_mode == "pod":
                    selected_mode = "uhost"
                    fallback_reason = "Pod 可用区近期创建接口不可用，已切换到健康的 UHost 可用区。"
            if preferred_healthy and (not zone or any(item.get("zone") == selected_zone and item.get("healthStatus") == "cooldown" for item in zones)):
                selected_zone = str(preferred_healthy[0].get("zone") or selected_zone)
        elif any(item.get("zone") == selected_zone and item.get("healthStatus") == "cooldown" for item in zones):
            raise HTTPException(status_code=409, detail="所选优云可用区近期创建接口不可用，请刷新后更换可用区。")
    locations = _youyun_location_options(
        support_zones,
        image_mode=image_mode,
        image=selected_image,
    )
    for location in locations:
        health = next(
            (item for item in zones if item.get("zone") == location.get("zone")),
            {},
        )
        location["health"] = {
            key: health.get(key)
            for key in ("healthStatus", "healthMessage", "lastFailureAt")
            if health.get(key) is not None
        }
    selected_location = _find_location_option(locations, selected_zone)
    if selected_location is not None and selected_location.get("supported") is False:
        selected_location = None
    if selected_location is None:
        selected_location = next(
            (
                item for item in locations
                if item.get("region") == selected_region
                and item.get("supported") is not False
                and (item.get("health") or {}).get("healthStatus") != "cooldown"
            ),
            None,
        ) or next(
            (item for item in locations if item.get("supported") is not False),
            None,
        )
    if selected_location is None:
        raise HTTPException(status_code=422, detail=f"区域 {selected_region} 没有可用的镜像运行可用区。")
    selected_region = str(selected_location.get("region") or selected_region)
    selected_zone = str(selected_location.get("zone") or selected_zone)
    selected_mode = youyun_zone_default_mode(
        {"zone": selected_location.get("zone"), "is_pod": selected_location.get("isPod")},
        image_mode or (None if requested_auto_mode else selected_mode),
    )
    if zones:
        # A region switch must not carry the previous region's default zone.
        # The provider rejects combinations such as cn-sh2 + cn-wlcb-01 with
        # RetCode=230 before it can return inventory or pricing.
        if not zone:
            current_zone_info = next((item for item in zones if item.get("zone") == selected_zone), None)
            if (
                current_zone_info is None
                or current_zone_info.get("region") != selected_region
                or not youyun_zone_supports_mode(current_zone_info, selected_mode)
                or current_zone_info.get("healthStatus") == "cooldown"
            ):
                matching_zone = next(
                    (
                        item
                        for item in zones
                        if item.get("region") == selected_region
                        and youyun_zone_supports_mode(item, selected_mode)
                        and item.get("healthStatus") != "cooldown"
                    ),
                    None,
                )
                if matching_zone is not None:
                    selected_zone = str(matching_zone.get("zone") or selected_zone)
        selected_zone_info = next(
            (
                item for item in zones
                if item.get("region") == selected_region and item.get("zone") == selected_zone
            ),
            None,
        )
        if selected_zone_info is None:
            raise HTTPException(status_code=422, detail="所选优云可用区不在当前区域支持列表中，请刷新后重试。")
        if not youyun_zone_supports_mode(selected_zone_info, selected_mode):
            raise HTTPException(status_code=422, detail="所选实例模式与可用区不匹配，请重新选择。")
    if selected_region != config.region or selected_zone != config.zone:
        runner = build_youyun_runner(region=selected_region, zone=selected_zone, charge_type=selected_charge_type)
        config = runner.manager.client.config
    selected_image_id = imageId or image_id or config.image_id
    community_keywords = ["LLaMAFactory", "Qwen-VL", "视觉"] if purpose == "training" else ["Diffusers", "LoRA", "Qwen-Image"]
    # Community catalog discovery is independent of machine/image inventory and
    # capacity; start it first so the upstream calls overlap instead of queueing.
    community_fetch = (
        asyncio.gather(
            _cached_youyun_community_images(runner.manager.client, selected_region, selected_zone),
            *(_cached_youyun_community_images(runner.manager.client, selected_region, selected_zone, keyword) for keyword in community_keywords),
            return_exceptions=True,
        )
        if includeCommunity
        else None
    )
    try:
        machine_types, images = await asyncio.gather(
            _cached_youyun_instance_types_for_zone(runner.manager.client, selected_region, selected_zone),
            _cached_youyun_zone_images(runner.manager.client, selected_region, selected_zone),
        )
        available_gpu_types = [item.name for item in machine_types if item.name]
        selected_gpu_type = gpuType if gpuType in available_gpu_types else (
            available_gpu_types[0] if available_gpu_types else config.gpu_type
        )
        if purpose not in {"training", "inference"}:
            raise HTTPException(status_code=422, detail="purpose 只能是 training 或 inference。")
        recommended_image = (
            select_visual_inference_image(images, config.image_id)
            if purpose == "inference"
            else select_visual_training_image(images, config.image_id)
        )
        if not imageId and recommended_image and recommended_image.get("image_id"):
            selected_image_id = str(recommended_image["image_id"])
        async def probe_catalog_capacity(
            probe_client: Any,
            probe_machine_types: list[Any],
            probe_image_id: str,
        ) -> tuple[dict[str, Any], dict[str, YouyunApiError]]:
            results: dict[str, Any] = {}
            errors: dict[str, YouyunApiError] = {}

            async def probe(machine: Any) -> None:
                name = str(getattr(machine, "name", "") or "")
                if not name or bool(getattr(machine, "sold_out", False)):
                    return
                try:
                    results[name] = await _cached_youyun_capacity(
                        probe_client,
                        gpu_type=name,
                        image_id=probe_image_id,
                        charge_type=selected_charge_type,
                        region=selected_region,
                        zone=selected_zone,
                    )
                except YouyunApiError as exc:
                    errors[name] = exc

            await asyncio.gather(*(probe(machine) for machine in probe_machine_types))
            return results, errors

        capacity_by_gpu, capacity_errors = await probe_catalog_capacity(
            runner.manager.client, machine_types, selected_image_id
        )
        if selected_gpu_type in capacity_errors:
            raise capacity_errors[selected_gpu_type]
        capacity = capacity_by_gpu.get(selected_gpu_type)
        if capacity is None:
            from backend.providers.youyun.models import ResourceCapacity

            capacity = ResourceCapacity(gpu_type=selected_gpu_type, specs=[])
        # Automatic mode falls back once from Pod to a matching UHost zone when
        # the selected Pod has no usable single-card CPU/memory combination.
        if requested_auto_mode and not any(item.gpu == 1 and item.resource_enough for item in capacity.specs):
            uhost_zone = next((item["zone"] for item in zones if item.get("region") == selected_region and youyun_zone_supports_mode(item, "uhost")), None)
            if uhost_zone:
                selected_mode = "uhost"
                selected_zone = uhost_zone
                runner = build_youyun_runner(region=selected_region, zone=selected_zone, charge_type=selected_charge_type)
                config = runner.manager.client.config
                machine_types, images = await asyncio.gather(
                    _cached_youyun_instance_types_for_zone(runner.manager.client, selected_region, selected_zone),
                    _cached_youyun_zone_images(runner.manager.client, selected_region, selected_zone),
                )
                available_gpu_types = [item.name for item in machine_types if item.name]
                selected_gpu_type = gpuType if gpuType in available_gpu_types else (available_gpu_types[0] if available_gpu_types else config.gpu_type)
                recommended_image = (
                    select_visual_inference_image(images, config.image_id)
                    if purpose == "inference"
                    else select_visual_training_image(images, config.image_id)
                )
                selected_image_id = imageId or str((recommended_image or {}).get("image_id") or config.image_id)
                capacity_by_gpu, capacity_errors = await probe_catalog_capacity(
                    runner.manager.client, machine_types, selected_image_id
                )
                if selected_gpu_type in capacity_errors:
                    raise capacity_errors[selected_gpu_type]
                capacity = capacity_by_gpu.get(selected_gpu_type)
                if capacity is None:
                    from backend.providers.youyun.models import ResourceCapacity

                    capacity = ResourceCapacity(gpu_type=selected_gpu_type, specs=[])
        community_results = await community_fetch if community_fetch is not None else []
        community_images = []
        for result in community_results:
            if isinstance(result, list):
                community_images.extend(result)
        community_images = list({str(item.get("image_id")): item for item in community_images if item.get("image_id")}.values())
        community_image_ids.update(
            str(item.get("image_id") or "") for item in community_images if item.get("image_id")
        )
    except YouyunApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    community_candidates: dict[tuple[str, str], dict[str, Any]] = {}
    if includeCommunity:
        for item in merge_community_youyun_images(images):
            if not item.get("image_id") and str(item.get("name") or "").casefold() in {
                str(row.get("name") or "").casefold() for row in community_images
            }:
                continue
            key = (str(item.get("name") or "").casefold(), str(item.get("version_name") or "").casefold())
            community_candidates[key] = item
        for item in normalize_community_image_rows(community_images):
            key = (str(item.get("name") or "").casefold(), str(item.get("version_name") or "").casefold())
            community_candidates[key] = item
    community_payload: list[dict[str, Any]] = []
    workload = "vision_training" if purpose == "training" else "vision_inference"
    ranked_community = {
        str(item.get("image_id") or ""): item
        for item in rank_community_image_candidates(
            community_images,
            workload=workload,
            gpu_type=selected_gpu_type,
            verification_by_id=community_image_verifications,
        )
        if item.get("image_id")
    }
    for item in community_candidates.values():
        image_id = str(item.get("image_id") or "")
        verification = community_image_verifications.get(image_id, {})
        match = ranked_community.get(image_id, {})
        item_status = str(item.get("status") or "CatalogOnly")
        item_available = bool(item.get("available")) and item_status.casefold() in {"available", "normal", "ready"}
        community_payload.append(
            {
                "id": image_id or None,
                "name": item.get("name") or "未命名社区镜像",
                "versionName": item.get("version_name") or "",
                "status": item_status,
                "tags": item.get("tags") or [],
                "description": item.get("description") or "",
                "available": item_available,
                "source": item.get("source") or "community_catalog",
                "verificationState": verification.get("state", "unverified"),
                "verifiedAt": verification.get("verifiedAt"),
                "verificationExpiresAt": verification.get("expiresAtEpoch"),
                "verificationMessage": verification.get("message"),
                "price": item.get("price"),
                "supportedGpuTypes": item.get("supported_gpu_types") or [],
                "compatible": bool(match.get("compatible")) if image_id else False,
                "selectable": bool(match.get("selectable")) if image_id else False,
                "gpuSupported": bool(match.get("gpuSupported")) if image_id else False,
                "matchScore": match.get("matchScore", 0),
                "recommendationReason": match.get("recommendationReason") or ("目录候选，等待真实镜像 ID 和库存" if not image_id else "镜像用途与当前任务不匹配，已自动隐藏。"),
            }
        )
    community_payload.sort(
        key=lambda item: (
            bool(item.get("selectable")),
            bool(item.get("compatible")),
            int(item.get("matchScore") or 0),
        ),
        reverse=True,
    )
    ranked_compatible = [item for item in ranked_community.values() if item.get("compatible")]
    recommended_community = next((item for item in ranked_compatible if item.get("selectable")), ranked_compatible[0] if ranked_compatible else None)
    platform_recommendations = recommend_image_candidates(
        images,
        workload=("vision_training" if purpose == "training" else "vision_inference"),
        gpu_type=selected_gpu_type,
    )
    recommended_platform = next(
        (item for item in platform_recommendations if item.get("selectable")),
        next((item for item in platform_recommendations if item.get("compatible")), None),
    )
    return_payload = {
        "region": selected_region,
        "zone": selected_zone,
        "instanceMode": selected_mode,
        "fallbackEligible": requested_auto_mode,
        "fallback": bool(fallback_reason),
        "fallbackReason": fallback_reason,
        "zones": zones,
        "locations": locations,
        "locationZone": selected_zone,
        "gpuType": selected_gpu_type,
        "chargeType": selected_charge_type,
        "imageId": selected_image_id,
        "recommendedImageId": str((recommended_image or {}).get("image_id") or selected_image_id),
        "recommendedImageName": str((recommended_image or {}).get("name") or ""),
        "recommendedImageScore": int((recommended_platform or {}).get("score") or 0),
        "recommendedImageReason": str((recommended_platform or {}).get("recommendationReason") or ""),
        "recommendedInferenceImageId": str(
            (select_visual_inference_image(images, config.image_id) or {}).get("image_id") or selected_image_id
        ),
        "recommendedInferenceImageName": str(
            (select_visual_inference_image(images, config.image_id) or {}).get("name") or ""
        ),
        "recommendedCommunityImageId": str((recommended_community or {}).get("image_id") or ""),
        "recommendedCommunityImageName": str((recommended_community or {}).get("name") or ""),
        "imageType": (selected_image or {}).get("image_type"),
        "container": _youyun_image_container(selected_image or {}),
        "communityImages": community_payload,
        "images": [
            {
                "id": item["image_id"],
                "name": item["name"],
                "type": item["image_type"],
                "status": item["status"],
                "tags": item["tags"],
                "description": item["description"],
            }
            for item in images
            if item.get("image_id") and item.get("status") not in {"Deleted", "Unavailable"}
        ],
        "machineTypes": [
            {
                "name": item.name,
                "zone": item.zone,
                "soldOut": item.sold_out,
                "description": item.description,
                "graphicsMemoryGb": item.graphics_memory_gb,
                **youyun_machine_inventory_metadata(
                    item,
                    capacity,
                    selected_gpu_type,
                    capacity_by_gpu=capacity_by_gpu,
                    capacity_errors={name: str(error) for name, error in capacity_errors.items()},
                ),
            }
            for item in machine_types
            if item.zone == selected_zone
        ],
        "capacity": [
            {"gpu": spec.gpu, "cpu": spec.cpu, "memoryGb": spec.memory_gb, "available": spec.resource_enough}
            for spec in capacity.specs
        ],
    }
    if _youyun_gpu_specs_cache_ttl > 0:
        _youyun_gpu_specs_cache[specs_cache_key] = (time.monotonic(), return_payload)
    return return_payload

@app.get("/api/youyun/images")
async def youyun_images() -> dict[str, Any]:
    """列出适用于远程微调的优云官方 CUDA/PyTorch/vLLM 镜像。"""
    global _YOUYUN_IMAGES_CACHE
    from backend.providers.youyun.models import YouyunApiError

    now = time.monotonic()
    if _YOUYUN_IMAGES_CACHE is not None:
        cached_at, cached_images, cached_default = _YOUYUN_IMAGES_CACHE
        if now - cached_at < _YOUYUN_IMAGES_CACHE_TTL_SECONDS:
            return {"images": cached_images, "defaultImageId": cached_default}

    try:
        runner = build_youyun_runner()
        images = await _cached_youyun_images(
            runner.manager.client, getattr(runner.manager.client.config, "region", "")
        )
    except YouyunApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    default_name = os.environ.get("YOUYUN_DEFAULT_IMAGE_NAME", "cuda_pytorch").strip().lower()
    configured_image_id = (
        runner.manager.client.config.training_image_id
        or runner.manager.client.config.image_id
    )
    normalized = [
        {
            "id": item.get("image_id"),
            "name": item.get("name") or item.get("image_id"),
            "type": item.get("image_type"),
            "container": _youyun_image_container(item),
            "instanceMode": _youyun_image_instance_mode(item),
            "status": item.get("status"),
            "tags": item.get("tags") or [],
            "description": item.get("description") or "",
            "source": _youyun_image_source_label(item),
        }
        for item in images
        if item.get("image_id")
        and str(item.get("status") or "").lower() not in {"deleted", "unavailable"}
        and (_youyun_image_is_selectable_for_training(item) or item.get("image_id") == configured_image_id)
    ]
    # DescribeCompShareImages may omit a configured community/shared image even
    # though it remains valid for training. Keep that default selectable so a
    # user can always switch back after choosing another image.
    if configured_image_id and not any(item["id"] == configured_image_id for item in normalized):
        normalized.append(
            {
                "id": configured_image_id,
                "name": "LLaMA-Factory 社区训练镜像",
                "type": "Community",
                "container": True,
                "instanceMode": "pod",
                "status": "Available",
                "tags": [],
                "description": "平台默认训练镜像",
                "source": "社区镜像",
            }
        )
    normalized.sort(key=lambda item: _training_image_priority(item, default_name))
    default = next((item for item in normalized if item["id"] == configured_image_id), None)
    if default is None:
        default = next(
            (
                item
                for item in normalized
                if all(token in str(item["name"]).lower() for token in default_name.split("_") if token)
            ),
            None,
        )
    if default is None and normalized:
        default = normalized[0]
    default_id = default["id"] if default else None
    if default_id:
        # Keep the default visible even when the provider returns a long mixed list.
        for item in normalized:
            item["isDefault"] = item["id"] == default_id
        default_item = next((item for item in normalized if item["id"] == default_id), None)
        if default_item is not None:
            normalized = [default_item, *(item for item in normalized if item["id"] != default_id)]
    normalized = normalized[:100]
    _YOUYUN_IMAGES_CACHE = (now, normalized, default_id)
    return {"images": normalized, "defaultImageId": default_id}


@app.get("/api/youyun/inference-images")
async def youyun_inference_images() -> dict[str, Any]:
    """列出可用于远程推理的优云官方 vLLM 镜像。"""
    global _YOUYUN_INFERENCE_IMAGES_CACHE
    from backend.providers.youyun.models import YouyunApiError

    now = time.monotonic()
    if _YOUYUN_INFERENCE_IMAGES_CACHE is not None:
        cached_at, cached_images, cached_default = _YOUYUN_INFERENCE_IMAGES_CACHE
        if now - cached_at < _YOUYUN_IMAGES_CACHE_TTL_SECONDS:
            return {"images": cached_images, "defaultImageId": cached_default}
    try:
        runner = build_youyun_runner()
        images = await _cached_youyun_images(
            runner.manager.client, getattr(runner.manager.client.config, "region", "")
        )
    except YouyunApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    def image_compatibility(item: dict[str, Any]) -> dict[str, Any]:
        # Follow whatever the provider returns. The platform deliberately keeps
        # no per-version acceptance table here: the provider replaces its
        # official vLLM image over time, and a hardcoded list would keep
        # recommending a version the provider no longer publishes. The only
        # image-level constraint that survives is the 5090 architecture gate.
        version = _youyun_image_version(item)
        if _is_rtx5090_only_image(item):
            return {
                "version": version,
                "supportedGpu": ["RTX 5090"],
                "recommendation": "rtx5090",
                "recommendationReason": "镜像面向 RTX 5090，需搭配 5090 使用。",
            }
        return {
            "version": version,
            "supportedGpu": ["计算能力不低于 7.5 的 NVIDIA GPU"],
            "recommendation": "general",
            "recommendationReason": "优云官方 vLLM 镜像；当前镜像运行时要求 GPU 计算能力不低于 7.5。",
        }

    normalized = [
        {
            "id": item.get("image_id"),
            "name": item.get("name") or item.get("image_id"),
            "type": item.get("image_type"),
            "container": _youyun_image_container(item),
            "instanceMode": _youyun_image_instance_mode(item),
            "status": item.get("status"),
            "tags": item.get("tags") or [],
            "description": item.get("description") or "",
            "source": _youyun_image_source_label(item),
            "compatibility": image_compatibility(item),
        }
        for item in images
        if item.get("image_id")
        and str(item.get("status") or "").lower() not in {"deleted", "unavailable"}
        and _is_official_vllm_image(item)
    ]
    normalized.sort(
        key=lambda item: (
            _version_sort_key(item["compatibility"]["version"]),
            str(item.get("name") or ""),
        )
    )
    preferred_id = os.environ.get("YOUYUN_INFERENCE_IMAGE_ID", "").strip()
    # Without an explicit override, default to the newest general-purpose image
    # so an ordinary GPU never lands on a 5090-only image it cannot use.
    default = (
        next((item for item in normalized if item["id"] == preferred_id), None)
        or next(
            (
                item
                for item in normalized
                if item["compatibility"]["recommendation"] == "general"
            ),
            None,
        )
        or (normalized[0] if normalized else None)
    )
    default_id = default["id"] if default else None
    for item in normalized:
        item["isDefault"] = item["id"] == default_id
    normalized = normalized[:20]
    _YOUYUN_INFERENCE_IMAGES_CACHE = (now, normalized, default_id)
    return {"images": normalized, "defaultImageId": default_id}


@app.post("/api/youyun/price")
async def youyun_price(payload: YouyunPriceRequest) -> dict[str, Any]:
    from backend.providers.youyun.models import YouyunApiError

    try:
        return await resolve_youyun_resource(
            YouyunResourceSelection(
                mode=payload.mode,
                gpuType=payload.gpuType,
                gpuCount=payload.gpuCount,
                distributedStrategy=payload.distributedStrategy,
                cpuCores=payload.cpuCores,
                memoryGb=payload.memoryGb,
                region=payload.region,
                zone=payload.zone,
                imageId=payload.imageId,
                imageName=payload.imageName,
                imageVersion=payload.imageVersion,
                purpose=payload.purpose,
                instanceMode=payload.instanceMode,
                publicModelPath=payload.publicModelPath,
            )
        )
    except YouyunApiError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def health_snapshot() -> dict[str, Any]:
    remote_task_ids = [
        task_id
        for task_id, task in remote_training_tasks.items()
        if not task.done()
    ]
    active_task_ids = ([active_task_id] if active_task_id else []) + remote_task_ids
    return {
        "status": "ok",
        "releaseVersion": os.environ.get("XUNTUI_RELEASE_VERSION", "unknown"),
        "evaluationProviders": {
            "judgeConfigured": all(os.environ.get(key, "").strip() for key in ("XUNTUI_EVALUATION_JUDGE_BASE_URL", "XUNTUI_EVALUATION_JUDGE_API_KEY", "XUNTUI_EVALUATION_JUDGE_MODEL")),
            "embeddingConfigured": all(os.environ.get(key, "").strip() for key in ("XUNTUI_EVALUATION_EMBEDDING_BASE_URL", "XUNTUI_EVALUATION_EMBEDDING_API_KEY", "XUNTUI_EVALUATION_EMBEDDING_MODEL")),
        },
        "worker": "busy" if active_task_ids else "idle",
        "activeTaskId": active_task_id or (remote_task_ids[0] if remote_task_ids else None),
        "activeTaskIds": active_task_ids,
        "queue": "postgres" if STORAGE_BACKEND == "postgres" else "sqlite-local",
        "storageBackend": STORAGE_BACKEND,
        "artifactRoot": str(ARTIFACT_ROOT),
        "runtimeRoot": str(RUNTIME_ROOT),
        "inference": "ready" if chat_process and chat_process.returncode is None else "idle",
    }


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return health_snapshot()


GPU_NOT_DETECTED_REASON = "当前节点未检测到可用 GPU"


def format_gpu_probe_error(exc: Exception) -> str:
    if isinstance(exc, FileNotFoundError):
        return GPU_NOT_DETECTED_REASON
    return f"GPU 状态读取失败：{exc}"


def probe_gpu_info() -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception as exc:
        return {"available": False, "reason": format_gpu_probe_error(exc)}
    first_line = (result.stdout.strip().splitlines() or [""])[0].strip()
    if result.returncode != 0 or not first_line:
        return {"available": False, "reason": GPU_NOT_DETECTED_REASON}
    fields = [part.strip() for part in first_line.split(",")]
    if len(fields) < 4:
        return {"available": False, "reason": f"GPU 信息格式无法解析：{first_line}"}

    def parse_memory(value: str) -> int | None:
        try:
            return int(float(value))
        except ValueError:
            return None

    info: dict[str, Any] = {"available": True, "name": fields[0]}
    for key, raw in (("memoryTotalMb", fields[1]), ("memoryUsedMb", fields[2]), ("memoryFreeMb", fields[3])):
        parsed = parse_memory(raw)
        if parsed is not None:
            info[key] = parsed
    return info


@app.get("/api/gpu-info")
async def gpu_info() -> dict[str, Any]:
    return await asyncio.to_thread(probe_gpu_info)


@app.get("/api/datasets")
async def datasets() -> list[dict[str, Any]]:
    return store.list_datasets()


@app.get("/api/audit-logs")
async def get_audit_logs(limit: int = Query(default=100, ge=1, le=500)) -> list[dict[str, Any]]:
    # 审计查询保持轻量，详情字段原样返回给管理员页面。
    if STORAGE_BACKEND == "postgres":
        with audit_store._pg_connect() as connection:
            rows = connection.execute("SELECT id, occurred_at::text AS occurred_at, actor_id, actor_name, action, object_type, object_id, result, error, details FROM audit_logs ORDER BY id DESC LIMIT %s", (limit,)).fetchall()
    else:
        import sqlite3
        with sqlite3.connect(DATABASE_PATH) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("SELECT id, occurred_at, actor_id, actor_name, action, object_type, object_id, result, error, details FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if isinstance(item.get("details"), str):
            try: item["details"] = json.loads(item["details"] or "{}")
            except json.JSONDecodeError: item["details"] = {}
        result.append(item)
    return result


@app.get("/api/datasets/{dataset_id}/versions")
async def dataset_versions(dataset_id: str) -> list[dict[str, Any]]:
    if store.get_dataset(dataset_id) is None:
        raise HTTPException(status_code=404, detail="数据集不存在。")
    return store.list_dataset_versions(dataset_id)


def is_model_cached(repo_id: str) -> bool:
    snapshots = MODEL_CACHE_ROOT / repo_id.replace("/", "--") / "snapshots"
    return snapshots.is_dir() and any(snapshots.iterdir())


def model_cache_path(repo_id: str) -> Path:
    return MODEL_CACHE_ROOT / repo_id.replace("/", "--")


def scan_cached_model_repos() -> list[str]:
    if not MODEL_CACHE_ROOT.is_dir():
        return []
    repositories: list[str] = []
    try:
        children = list(MODEL_CACHE_ROOT.iterdir())
    except OSError:
        return []
    for child in children:
        if not child.is_dir() or "--" not in child.name:
            continue
        snapshots = child / "snapshots"
        if not snapshots.is_dir():
            continue
        repo_id = child.name.replace("--", "/")
        if repo_id.count("/") == 1:
            repositories.append(repo_id)
    return sorted(set(repositories))


def sync_model_assets(*, force: bool = False) -> bool:
    global _MODEL_ASSET_SYNCED_AT, _MODEL_ASSET_SYNCED_STORE_ID
    with _MODEL_ASSET_SYNC_LOCK:
        now = time.monotonic()
        store_id = id(store)
        if (
            not force
            and _MODEL_ASSET_SYNCED_STORE_ID == store_id
            and now - _MODEL_ASSET_SYNCED_AT < _MODEL_ASSET_SYNC_TTL_SECONDS
        ):
            return False
        store.sync_model_assets(MODEL_CACHE_ROOT)
        _MODEL_ASSET_SYNCED_AT = time.monotonic()
        _MODEL_ASSET_SYNCED_STORE_ID = store_id
        return True


def require_model_uploader() -> ModelArtifactUploader:
    if model_uploader is None:
        raise RuntimeError(model_storage_error or "模型共享存储尚未初始化。")
    return model_uploader


def _adapter_sync_state(task_id: str, state: str, *, error: str | None = None) -> None:
    """Persist the user-visible state of remote-to-local artifact synchronization."""
    task = store.get(task_id)
    if not task:
        return None
    artifacts = []
    for artifact in task.get("artifacts") or []:
        if artifact.get("type") != "adapter":
            artifacts.append(artifact)
            continue
        updated = {**artifact, "syncState": state, "syncError": error}
        artifacts.append(updated)
    store.update(task_id, artifacts=artifacts)


def _validate_remote_adapter_path(task_id: str, remote_path: str) -> str:
    """Only accept paths generated below this task's isolated remote directory."""
    root = os.environ.get("XUNTUI_REMOTE_ROOT", "/root/cppt").rstrip("/")
    normalized = posixpath.normpath(str(remote_path).replace("\\", "/"))
    prefix = f"{root}/{task_id}/"
    if not normalized.startswith(prefix) or normalized == prefix.rstrip("/"):
        raise HTTPException(status_code=403, detail="远程 LoRA Adapter 路径无效。")
    return normalized


def _verified_task_mirror(task_id: str, task: dict[str, Any]) -> bool:
    # A previous attempt may leave a perfectly valid but stale directory. Only
    # trust the manifest registered on this attempt's persisted artifacts.
    workdir = (youyun_mirror_root / task_id).resolve()
    if not workdir.is_relative_to(youyun_mirror_root.resolve()):
        return False
    manifest = next((item for item in task.get("artifacts", []) if item.get("type") == "manifest"), None)
    if not manifest or not isinstance(manifest.get("sha256"), str) or not manifest.get("path"):
        return False
    if Path(manifest["path"]).resolve() != workdir / "manifest.json":
        return False
    return verified_local_training_artifacts(workdir, expected_manifest_sha256=manifest["sha256"])


def _local_task_adapters_complete(task: Mapping[str, Any]) -> bool:
    adapters = [item for item in task.get("artifacts") or [] if item.get("type") == "adapter"]
    return bool(adapters) and all(
        item.get("path") and adapter_is_complete(Path(str(item["path"])))
        for item in adapters
    )


@scoped_resource("task_id")
async def sync_remote_task_artifacts(task_id: str) -> None:
    """Publish complete local Adapters, recovering remote files only when needed."""
    task = store.get(task_id)
    if not task:
        return
    phase = "本地产物校验"
    try:
        if task.get("provider") == "youyun" and await asyncio.to_thread(_verified_task_mirror, task_id, task):
            # Restore metadata from a verified current-attempt mirror without
            # reconnecting to a GPU that may already have been released.
            phase = "本地产物元数据恢复"
            workdir = youyun_mirror_root / task_id
            remote_root = os.environ.get("XUNTUI_REMOTE_ROOT", "/root/cppt")
            refreshed = await youyun_mirror_executor.collect_artifacts({
                **task, "_remoteOutputDir": f"{remote_root}/{task_id}/output",
            })
            previous = {item.get("variant"): item for item in task.get("artifacts", []) if item.get("type") == "adapter"}
            for index, item in enumerate(refreshed):
                if item.get("type") == "adapter":
                    relative = Path(item["path"]).resolve().relative_to((workdir / "output").resolve()).as_posix()
                    remote_path = f"{remote_root}/{task_id}/output" + (f"/{relative}" if relative != "." else "")
                    lineage = {key: value for key, value in previous.get(item.get("variant"), {}).items() if key in {"baseModelPath", "baseModelMetadata"}}
                    refreshed[index] = {**lineage, **item, "remotePath": remote_path, "syncState": "available", "syncError": None}
            markers = [item for item in task.get("artifacts", []) if item.get("type") == "youyun-instance"]
            store.update(task_id, artifacts=[*refreshed, *markers])
            store.append_log(task_id, "本地镜像通过产物清单校验，已重建训练指标与 Adapter 元数据，无需重新连接 GPU。")
            task = store.get(task_id) or task
        if not await asyncio.to_thread(_local_task_adapters_complete, task):
            if task.get("provider") == "youyun":
                phase = "远程产物回传"
                resource = dict(task.get("youyunResource") or {})
                runner = build_youyun_runner(region=resource.get("region"), zone=resource.get("zone"))
                await runner.sync_retained_artifacts(task_id, task)
                task = store.get(task_id) or task
            if not await asyncio.to_thread(_local_task_adapters_complete, task):
                raise RuntimeError("没有完整的本地 Adapter 配置和权重文件。")
        phase = "共享存储上传"
        errors = await upload_completed_task_artifacts(task_id)
        if errors:
            raise RuntimeError("；".join(errors))
        _adapter_sync_state(task_id, "available")
        store.append_log(task_id, "训练产物已完成本地回传并上传共享存储。")
    except Exception as exc:  # noqa: BLE001 - persist the failed stage for retry
        error = f"{phase}失败：{exc}"
        _adapter_sync_state(task_id, "failed", error=error)
        store.append_log(task_id, f"训练产物后台同步失败：{error}（可重试）")
    finally:
        try:
            await asyncio.to_thread(sync_model_assets, force=True)
        except Exception:
            logger.exception("远程产物同步后的模型资产刷新失败：%s", task_id)
        artifact_sync_tasks.pop(task_id, None)


def schedule_remote_artifact_sync(task_id: str) -> None:
    existing = artifact_sync_tasks.get(task_id)
    if existing is not None and not existing.done():
        return
    _adapter_sync_state(task_id, "syncing")
    task = asyncio.create_task(sync_remote_task_artifacts(task_id))
    artifact_sync_tasks[task_id] = task


def _recover_trainer_metrics(task_id: str, state_path: Path) -> int:
    """Recover structured Loss when live SSH polling missed Trainer output."""
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return 0
    history = payload.get("log_history") if isinstance(payload, dict) else None
    if not isinstance(history, list):
        return 0

    def number(value: Any) -> float | None:
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return result if math.isfinite(result) else None

    by_step: dict[int, dict[str, Any]] = {}
    next_step = 0
    for entry in history:
        if not isinstance(entry, dict):
            continue
        train_loss = number(entry.get("loss"))
        if train_loss is None:
            train_loss = number(entry.get("train_loss"))
        eval_loss = number(entry.get("eval_loss"))
        if train_loss is None and eval_loss is None:
            continue
        try:
            step = int(float(entry.get("step") or 0))
        except (TypeError, ValueError, OverflowError):
            step = 0
        if step <= 0:
            step = next_step + 1
        next_step = max(next_step, step)
        current = by_step.setdefault(step, {"step": step})
        if train_loss is not None:
            current["train_loss"] = train_loss
        if eval_loss is not None:
            current["eval_loss"] = eval_loss
        epoch = number(entry.get("epoch"))
        learning_rate = number(entry.get("learning_rate"))
        if learning_rate is None:
            learning_rate = number(entry.get("lr"))
        if epoch is not None:
            current["epoch"] = epoch
        if learning_rate is not None:
            current["learning_rate"] = learning_rate

    for step in sorted(by_step):
        metric = by_step[step]
        store.append_metric(
            task_id,
            step=step,
            epoch=metric.get("epoch"),
            train_loss=metric.get("train_loss"),
            eval_loss=metric.get("eval_loss"),
            learning_rate=metric.get("learning_rate"),
        )
    return len(by_step)


def _recover_task_metrics(task_id: str, task: dict[str, Any] | None = None) -> int:
    """Read saved Trainer history from local output/checkpoint directories."""
    task = task or store.get(task_id)
    if not task:
        return 0
    state_paths: set[Path] = set()
    for artifact in task.get("artifacts") or []:
        if artifact.get("type") != "adapter" or not artifact.get("path"):
            continue
        path = Path(str(artifact["path"]))
        state_paths.add(path / "trainer_state.json")
        state_paths.add(path.parent / "trainer_state.json")
    return sum(
        _recover_trainer_metrics(task_id, path)
        for path in state_paths
        if path.is_file()
    )


async def upload_completed_task_artifacts(task_id: str, variant_filter: str | None = None) -> list[str]:
    task = store.get(task_id)
    if not task:
        return ["训练任务不存在。"]
    _recover_task_metrics(task_id, task)
    uploader = model_uploader
    if uploader is None:
        store.append_log(
            task_id,
            f"共享存储未启用，Adapter 暂留本地：{model_storage_error or '配置不可用'}",
        )
        return [model_storage_error or "模型共享存储尚未初始化。"]
    payload = store.get_payload(task_id)
    source_dataset = store.get_dataset(str(payload.get("datasetId"))) if payload.get("datasetId") else None
    base_asset = store.get_model_asset(store._base_model_id(str(payload.get("model", ""))))
    errors: list[str] = []
    for artifact in task.get("artifacts", []):
        if artifact.get("type") != "adapter":
            continue
        variant = artifact.get("variant")
        if variant not in {"best", "final"}:
            variant = "final"
        if variant_filter is not None and variant != variant_filter:
            continue
        path = Path(str(artifact.get("path", "")))
        try:
            if not artifact.get("path") or not adapter_is_complete(path):
                raise ValueError("服务器上的 Adapter 配置或权重文件不完整。")
            result = await asyncio.to_thread(
                uploader.upload_directory,
                path,
                name=f"{payload.get('outputName') or payload.get('name') or task_id} · {variant}",
                artifact_type="lora_adapter",
                artifact_variant=variant,
                training_type=str(payload.get("taskType") or "SFT").upper(),
                tuning_method=("LoRA" if payload.get("tuningMode") == "lora" else "QLoRA"),
                base_model_id=base_asset.get("id") if base_asset else None,
                base_model_name=base_asset.get("name") if base_asset else str(payload.get("model", "")),
                base_model_repo=str(payload.get("model", "")),
                base_model_revision=base_asset.get("revision") if base_asset else "main",
                base_model_local_path=(
                    artifact.get("baseModelPath")
                    or (
                        base_asset.get("path")
                        if base_asset
                        else str(model_cache_path(str(payload.get("model", ""))))
                    )
                ),
                dataset_id=payload.get("datasetId"),
                source_dataset_id=payload.get("datasetId"),
                source_dataset_name=(source_dataset or {}).get("name") or payload.get("datasetName"),
                source_dataset_sha256=(source_dataset or {}).get("contentSha256"),
                source_task_name=payload.get("name") or task_id,
                source_snapshot={
                    "datasetId": payload.get("datasetId"),
                    "datasetName": payload.get("datasetName"),
                    "datasetSha256": (source_dataset or {}).get("contentSha256"),
                    "structure": (source_dataset or {}).get("structure"),
                    "taskParameters": payload.get("parameters") or {},
                },
                source_platform="训练任务",
                source_uri=str(path),
                source_task_id=task_id,
                uploaded_by=str(payload.get("owner") or os.environ.get("MODEL_UPLOAD_USER", "local-admin")),
                extra_metadata={
                    "checkpointStep": artifact.get("checkpointStep"),
                    "evalLoss": artifact.get("evalLoss"),
                    "trainLoss": artifact.get("trainLoss"),
                    "baseModelMetadata": artifact.get("baseModelMetadata"),
                    "selectionMetric": task.get("selectionMetric"),
                },
            )
            store.append_log(
                task_id,
                f"{variant.title()} Adapter 已上传共享存储：s3://{result['minio_bucket']}/{result['minio_object_prefix']}",
            )
        except Exception as exc:
            store.append_log(task_id, f"{variant.title()} Adapter 上传共享存储失败：{exc}")
            errors.append(f"{variant.title()} Adapter：{exc}")
    return errors


@app.get("/api/capabilities")
async def capabilities() -> dict[str, Any]:
    return runtime_capabilities()


@app.get("/api/models/search")
async def search_models(
    q: str,
    limit: int = 20,
    source: Literal["public", "modelscope"] = "modelscope",
) -> list[dict[str, Any]]:
    if source != "public":
        require_local_runtime()
    query = q.strip()
    result_limit = min(max(limit, 1), 50 if source == "public" else 20)
    if source == "public":
        from backend.providers.youyun.models import YouyunApiError

        try:
            runner = build_youyun_runner()
            models = await asyncio.to_thread(
                runner.manager.client.describe_model_repository_models,
                name=query or None,
            )
        except YouyunApiError as exc:
            raise HTTPException(status_code=502, detail=f"优云公共模型库搜索失败：{exc}") from exc
        return [
            {
                "id": item["path"],
                "label": item["name"],
                "fileSize": 0,
                "downloads": 0,
                "cached": True,
                "publicModelPath": item["path"],
                "source": "youyun-public",
                "tag": item.get("tag") or None,
                "size": item.get("size") or None,
            }
            for item in models[:result_limit]
        ]
    if not query:
        return [
            {
                "id": repo_id,
                "label": repo_id.split("/")[-1],
                "fileSize": 0,
                "downloads": 0,
                "cached": True,
            }
            for repo_id in scan_cached_model_repos()[:result_limit]
        ]
    if len(query) < 2:
        return []

    def search() -> list[dict[str, Any]]:
        from modelscope.hub.api import HubApi

        result_limit = min(max(limit, 1), 20)
        page = HubApi()._api.list_repos(
            "model",
            search=query,
            sort="downloads",
            page_size=min(max(result_limit * 2, 20), 100),
        )
        results: list[dict[str, Any]] = []
        for model in page.items:
            tasks = list(model.tasks or [])
            if tasks and "text-generation" not in tasks:
                continue
            results.append(
                {
                    "id": model.repo_id,
                    "label": model.display_name or model.name,
                    "fileSize": int(model.file_size or 0),
                    "downloads": int(model.downloads or 0),
                    "cached": is_model_cached(model.repo_id),
                }
            )
            if len(results) >= result_limit:
                break
        return results

    try:
        return await asyncio.to_thread(search)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"ModelScope 搜索失败：{exc}") from exc


def _public_model_updated_at(value: Any) -> str:
    if value is None or isinstance(value, bool) or not str(value).strip():
        return "暂无数据"
    beijing = timezone(timedelta(hours=8))
    try:
        numeric = float(value)
    except (ValueError, TypeError):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=beijing)
            return parsed.astimezone(beijing).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return "暂无数据"
    try:
        if abs(numeric) >= 100_000_000_000:
            numeric /= 1000
        return datetime.fromtimestamp(numeric, beijing).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OverflowError, OSError):
        return "暂无数据"


def _public_model_size(item: dict[str, Any]) -> str:
    label = str(item.get("size") or "").strip()
    if label:
        return label
    size = item.get("sizeBytes")
    if isinstance(size, (int, float)) and not isinstance(size, bool) and size > 0:
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024 or unit == "TB":
                return f"{size:.1f} {unit}"
            size /= 1024
    return "暂无数据"


@app.get("/api/models/public")
async def list_public_models(
    q: str = Query("", max_length=200),
    page: int = Query(1, ge=1),
    pageSize: int = Query(5, ge=1, le=100),
) -> dict[str, Any]:
    """Return the provider public model catalog in UI-friendly pages."""
    from backend.providers.youyun.models import YouyunApiError

    query = q if isinstance(q, str) else ""
    try:
        runner = build_youyun_runner()
        models = await asyncio.to_thread(
            runner.manager.client.describe_model_repository_models,
            name=query.strip() or None,
        )
    except YouyunApiError as exc:
        raise HTTPException(status_code=502, detail=f"优云公共模型库读取失败：{exc}") from exc

    total = len(models)
    total_pages = max(1, (total + pageSize - 1) // pageSize)
    start = (page - 1) * pageSize
    items: list[dict[str, Any]] = []
    for item in models[start : start + pageSize]:
        path = item["path"]
        items.append(
            {
                "id": f"public-{hashlib.sha1(path.encode('utf-8')).hexdigest()[:12]}",
                "kind": "base",
                "name": item["name"],
                "repo": item["name"],
                "revision": item.get("tag") or "public",
                "path": path,
                "publicModelPath": path,
                "publicModelId": item.get("id") or None,
                "tag": item.get("tag") or " / ".join(str(tag) for tag in item.get("tags", [])) or "暂无数据",
                "size": _public_model_size(item),
                "status": "available" if str(item.get("status") or "active").lower() in {"active", "available"} else "unavailable",
                "source": "优云公共模型库",
                "updatedAt": _public_model_updated_at(item.get("updateTime") or item.get("createTime")),
                "archived": False,
            }
        )
    return {
        "items": items,
        "page": page,
        "pageSize": pageSize,
        "total": total,
        "totalPages": total_pages,
    }


@app.get("/api/model-assets")
async def list_model_assets(
    includeArchived: bool = False,
    archivedOnly: bool = False,
    kind: Literal["base", "trained"] | None = None,
    q: str | None = None,
    page: int | None = Query(default=None, ge=1),
    pageSize: int = Query(default=20, ge=1, le=100),
) -> Any:
    await asyncio.to_thread(sync_model_assets)
    if page is not None:
        result = await asyncio.to_thread(
            store.list_model_assets_page,
            page=page,
            page_size=pageSize,
            include_archived=includeArchived,
            archived_only=archivedOnly,
            kind=kind,
            query=q,
        )
    else:
        result = await asyncio.to_thread(
            store.list_model_assets,
            include_archived=includeArchived,
            archived_only=archivedOnly,
            kind=kind,
            query=q,
        )
    assets = result["items"] if page is not None else result
    for asset in assets:
        if asset["kind"] == "trained":
            for variant in asset.get("variants", []):
                variant["status"] = "available" if variant.get("path") and adapter_is_complete(Path(variant["path"])) else "unavailable"
    uploader = model_uploader
    if uploader is None:
        for asset in assets:
            asset["sharedStorage"] = {
                "status": "unavailable",
                "error": model_storage_error,
            }
            for variant in asset.get("variants", []):
                variant["sharedStorage"] = {"status": "unavailable", "error": model_storage_error}
        return result
    try:
        records = await asyncio.to_thread(uploader.metadata.list)
    except Exception as exc:
        # The local SQLite asset catalog is still useful when the shared PG
        # service is temporarily unavailable (for example, connection slots
        # exhausted on the shared server). Do not make the whole model page
        # disappear behind a generic frontend "Failed to fetch" error.
        storage_error = str(exc)
        for asset in assets:
            asset["sharedStorage"] = {
                "status": "unavailable",
                "error": storage_error,
            }
            if asset["kind"] == "trained":
                for variant in asset.get("variants", []):
                    variant["sharedStorage"] = {
                        "status": "unavailable",
                        "error": storage_error,
                    }
        return result
    for asset in assets:
        if asset["kind"] == "base":
            match = next(
                (
                    item
                    for item in records
                    if item["artifact_type"] == "base_model"
                    and item.get("source_uri") == asset.get("path")
                ),
                None,
            )
            asset["sharedStorage"] = shared_storage_payload(match)
        else:
            for variant in asset.get("variants", []):
                match = next(
                    (
                        item
                        for item in records
                        if item.get("source_task_id") == asset.get("taskId")
                        and item.get("artifact_variant") == variant.get("key")
                    ),
                    None,
                )
                variant["sharedStorage"] = shared_storage_payload(match)
    return result


def shared_storage_payload(record: dict[str, Any] | None) -> dict[str, Any]:
    if not record:
        return {"status": "not_uploaded"}
    bucket = record.get("minio_bucket")
    prefix = record.get("minio_object_prefix")
    return {
        "id": record.get("id"),
        "status": record.get("status"),
        "sizeBytes": record.get("total_size_bytes"),
        "manifestMd5": record.get("manifest_md5"),
        "bucket": bucket,
        "objectPrefix": prefix,
        "uri": f"s3://{bucket}/{prefix}" if bucket and prefix else None,
        "uploadedAt": record.get("uploaded_at"),
        "uploadedBy": record.get("uploaded_by"),
        "error": record.get("error_message"),
    }


@app.post("/api/model-assets/scan")
async def scan_model_assets() -> dict[str, Any]:
    repositories = scan_cached_model_repos()
    for repo_id in repositories:
        store.ensure_base_model(
            repo_id,
            path=str(model_cache_path(repo_id)),
            source="ModelScope 缓存",
        )
    sync_model_assets(force=True)
    return {
        "discovered": len(repositories),
        "registered": len(repositories),
        "assets": store.list_model_assets(include_archived=True),
    }


@app.post("/api/model-assets/base", status_code=status.HTTP_201_CREATED)
async def register_base_model(payload: BaseModelCreate) -> dict[str, Any]:
    try:
        return store.ensure_base_model(
            payload.repo,
            path=payload.path,
            source="手工登记",
            name=payload.name,
            revision=payload.revision,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


_adapter_uploads: set[str] = set()


@app.post("/api/model-assets/{asset_id}/variants/{variant}/upload")
async def upload_model_variant(asset_id: str, variant: Literal["best", "final"]) -> dict[str, Any]:
    asset = store.get_model_asset(asset_id)
    if asset is None or asset.get("kind") != "trained":
        raise HTTPException(status_code=404, detail="训练权重不存在。")
    await upload_model_asset(asset_id, variant)
    record = await asyncio.to_thread(
        require_model_uploader().metadata.find_source,
        artifact_type="lora_adapter", artifact_variant=variant, source_task_id=asset["taskId"],
    )
    return {"assetId": asset_id, "sharedStorage": shared_storage_payload(record)}


@app.post("/api/model-assets/{asset_id}/upload")
async def upload_model_asset(asset_id: str, variant: Literal["best", "final"] | None = None) -> dict[str, Any]:
    asset = store.get_model_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="模型资产不存在。")
    if asset.get("kind") == "trained":
        if variant and not any(item.get("key") == variant for item in asset.get("variants", [])):
            raise HTTPException(status_code=404, detail="训练权重版本不存在。")
        task_id = asset["taskId"]
        if task_id in _adapter_uploads:
            raise HTTPException(status_code=409, detail="该任务的权重正在上传，请稍后刷新状态。")
        _adapter_uploads.add(task_id)
        try:
            errors = await upload_completed_task_artifacts(task_id, variant)
            if errors:
                raise HTTPException(status_code=502, detail="上传 MinIO 失败：" + "；".join(errors))
            return {"assetId": asset_id, "status": "available"}
        finally:
            _adapter_uploads.discard(task_id)
    try:
        uploader = require_model_uploader()
        result = await asyncio.to_thread(
            uploader.upload_directory,
            Path(asset["path"]),
            name=asset["name"],
            artifact_type="base_model",
            artifact_variant="base",
            base_model_id=asset["id"],
            base_model_name=asset["name"],
            base_model_repo=asset["repo"],
            base_model_revision=asset["revision"],
            base_model_local_path=asset["path"],
            source_platform=(
                "ModelScope"
                if asset.get("source") == "ModelScope 缓存"
                else "手工登记"
            ),
            source_uri=asset["path"],
            uploaded_by=os.environ.get("MODEL_UPLOAD_USER", "local-admin"),
        )
        return {"assetId": asset_id, "sharedStorage": shared_storage_payload(result)}
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"上传 MinIO 失败：{exc}") from exc


@app.get("/api/model-files")
async def list_model_files(sourceTaskId: str | None = None) -> list[dict[str, Any]]:
    try:
        uploader = require_model_uploader()
        return await asyncio.to_thread(
            uploader.metadata.list, source_task_id=sourceTaskId
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/model-files/{model_id}/preview")
async def preview_model_files(model_id: str) -> dict[str, Any]:
    try:
        uploader = require_model_uploader()
        result = await asyncio.to_thread(uploader.preview_listing, model_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="模型共享存储记录不存在。")
    if result.get("status") != "available":
        raise HTTPException(status_code=409, detail="模型文件尚未完成上传，暂时无法预览。")
    return result


@app.get("/api/model-files/{model_id}/preview/file")
async def preview_model_file(model_id: str, path: str = Query(..., min_length=1, max_length=1000)) -> dict[str, Any]:
    try:
        uploader = require_model_uploader()
        result = await asyncio.to_thread(uploader.preview_file, model_id, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="模型共享存储记录不存在。")
    if result.get("status") != "available":
        raise HTTPException(status_code=409, detail="模型文件尚未完成上传，暂时无法预览。")
    return result


@app.patch("/api/model-assets/{asset_id}")
async def update_model_asset(asset_id: str, payload: ModelAssetPatch) -> dict[str, Any]:
    try:
        asset = store.update_model_asset(
            asset_id,
            name=payload.name,
            archived=payload.archived,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if asset is None:
        raise HTTPException(status_code=404, detail="模型资产不存在。")
    return asset


@app.post("/api/model-assets/{asset_id}/refresh")
async def refresh_model_asset(asset_id: str) -> dict[str, Any]:
    asset = store.refresh_model_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="模型资产不存在。")
    return asset



EVALUATION_MAX_FILE_BYTES = 200 * 1024 * 1024
EVALUATION_MAX_FILES = 10


def _evaluation_value_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict) and isinstance(value.get("content"), str):
        return value["content"].strip()
    if isinstance(value, list):
        return "\n".join(
            str(item.get("content", "")).strip()
            for item in value if isinstance(item, dict) and str(item.get("content", "")).strip()
        )
    return ""


def normalize_evaluation_record(record: dict[str, Any], source_file: str, source_line: int) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    prompt = _evaluation_value_text(record.get("prompt") or record.get("question"))
    reference = _evaluation_value_text(record.get("completion") or record.get("reference") or record.get("chosen"))
    system_prompt = _evaluation_value_text(record.get("system"))
    messages = record.get("messages")
    if isinstance(messages, list):
        if not prompt:
            prompt = next((_evaluation_value_text(item) for item in messages if isinstance(item, dict) and item.get("role") == "user"), "")
        if not reference:
            reference = next((_evaluation_value_text(item) for item in reversed(messages) if isinstance(item, dict) and item.get("role") == "assistant"), "")
        if not system_prompt:
            system_prompt = next((_evaluation_value_text(item) for item in messages if isinstance(item, dict) and item.get("role") == "system"), "")
    if not prompt:
        issues.append(_validation_issue("EVALUATION_PROMPT_REQUIRED", f"第 {source_line} 行缺少非空 prompt/question 或 user 消息。", line=source_line, path="prompt", suggestion="请填写评测问题。"))
    if not reference:
        issues.append(_validation_issue("EVALUATION_REFERENCE_REQUIRED", f"第 {source_line} 行缺少非空 completion/reference/chosen 或 assistant 消息。", line=source_line, path="completion", suggestion="请填写标准答案。"))
    if len(prompt) > 50000 or len(reference) > 50000:
        issues.append(_validation_issue("EVALUATION_TEXT_TOO_LONG", f"第 {source_line} 行问题或标准答案超过 50000 字符。", line=source_line, suggestion="请拆分或缩短该记录。"))
    if issues:
        return None, issues
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    if record.get("category") is not None:
        metadata = {**metadata, "category": record.get("category")}
    return {
        "id": str(record.get("id") or f"{Path(source_file).stem}-{source_line}"),
        "prompt": prompt,
        "completion": reference,
        "system": system_prompt,
        "metadata": metadata,
        "_sourceFile": source_file,
        "_sourceLine": source_line,
    }, []


def validate_evaluation_dataset_report(path: Path, *, max_errors: int = 100) -> dict[str, Any]:
    checked = 0
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    try:
        with path.open(encoding="utf-8-sig") as source:
            for line_number, raw in enumerate(source, 1):
                if not raw.strip():
                    warnings.append({"code": "BLANK_LINE", "severity": "warning", "line": line_number, "message": f"第 {line_number} 行为空行。", "fixable": False})
                    continue
                checked += 1
                try:
                    record = json.loads(raw)
                except json.JSONDecodeError:
                    errors.append(_validation_issue("INVALID_JSON", f"第 {line_number} 行不是合法 JSON。", line=line_number))
                    continue
                if not isinstance(record, dict):
                    errors.append(_validation_issue("JSON_OBJECT_REQUIRED", f"第 {line_number} 行必须是 JSON 对象。", line=line_number))
                    continue
                normalized, row_errors = normalize_evaluation_record(record, str(record.get("_sourceFile") or path.name), int(record.get("_sourceLine") or line_number))
                errors.extend(row_errors)
                if normalized:
                    external_id = normalized["id"]
                    if external_id in seen_ids:
                        errors.append(_validation_issue("DUPLICATE_EVALUATION_ID", f"第 {line_number} 行 ID {external_id} 重复。", line=line_number, path="id"))
                    seen_ids.add(external_id)
                if len(errors) >= max_errors:
                    break
    except UnicodeDecodeError:
        errors.append(_validation_issue("INVALID_ENCODING", "文件必须使用 UTF-8 编码。"))
    if checked == 0:
        errors.append(_validation_issue("EMPTY_DATASET", "文件没有可用数据。"))
    return {"status": "failed" if errors else "ready", "checkedRows": checked, "validRows": max(0, checked-len({e.get('line') for e in errors if e.get('line')})), "invalidRows": len({e.get('line') for e in errors if e.get('line')}), "errors": errors[:max_errors], "warnings": warnings[:max_errors], "autoFixableCount": 0, "validatorVersion": "evaluation-jsonl-v1", "checkedAt": datetime.now(timezone.utc).isoformat()}


def evaluation_records_from_dataset(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    source_path = _dataset_source_path(dataset)
    records: list[dict[str, Any]] = []
    with source_path.open(encoding="utf-8-sig") as source:
        for line_number, raw in enumerate(source, 1):
            if not raw.strip():
                continue
            record = json.loads(raw)
            normalized, errors = normalize_evaluation_record(record, str(record.get("_sourceFile") or source_path.name), int(record.get("_sourceLine") or line_number))
            if errors or normalized is None:
                raise HTTPException(status_code=409, detail=errors[0]["message"] if errors else "评测集记录无法解析。")
            records.append({"prompt": normalized["prompt"], "referenceAnswer": normalized["completion"], "systemPrompt": normalized["system"], "metadata": normalized["metadata"], "sourceFile": normalized["_sourceFile"], "sourceLine": normalized["_sourceLine"]})
    return records


async def _upload_dataset_image_text(
    request: Request,
    name: str,
    structure: Literal["SFT", "DPO", "IMAGE_TEXT"],
    filename: str = "",
    datasetType: Literal["文本生成", "文生图", "视觉理解"] = "文本生成",
) -> dict[str, Any]:
    clean_name = name.strip()
    safe_filename = Path(filename).name
    if not clean_name:
        raise HTTPException(status_code=422, detail="请填写数据集名称。")
    is_visual = datasetType == "视觉理解" or structure == "IMAGE_TEXT"
    is_image_generation = datasetType == "文生图"
    if is_image_generation:
        if structure != "IMAGE_TEXT":
            raise HTTPException(status_code=422, detail="文生图数据集必须使用 IMAGE_TEXT 结构。")
    elif is_visual and (datasetType != "视觉理解" or structure != "IMAGE_TEXT"):
        raise HTTPException(status_code=422, detail="视觉理解数据集必须使用 IMAGE_TEXT 结构。")
    if is_visual and not is_image_generation and Path(safe_filename).suffix.lower() != ".zip":
        raise HTTPException(status_code=422, detail="视觉理解数据集必须上传包含 metadata.jsonl 和 images/ 的 ZIP 数据包。")
    if not is_visual and not is_image_generation and Path(safe_filename).suffix.lower() != ".jsonl":
        raise HTTPException(status_code=422, detail="文本数据集仅支持 JSONL 文件。")

    dataset_id = f"ds-{uuid.uuid4().hex[:12]}"
    dataset_dir = DATASET_ROOT / dataset_id
    dataset_dir.mkdir(parents=True, exist_ok=False)
    source_path = dataset_dir / safe_filename
    size = 0
    digest = hashlib.sha256()
    try:
        if is_image_generation:
            import base64
            try:
                payload = json.loads((await request.body()).decode("utf-8"))
                files = payload.get("files") if isinstance(payload, dict) else None
                if not isinstance(files, list):
                    raise ValueError("文生图上传必须提供 files 数组。")
                inspections = []
                contents: dict[str, bytes] = {}
                captions: dict[str, str] = {}
                for item in files:
                    if not isinstance(item, dict):
                        raise ValueError("图片记录格式无效。")
                    name = str(item.get("file_name") or item.get("fileName") or "")
                    content = base64.b64decode(str(item.get("content_base64") or item.get("contentBase64") or ""), validate=True)
                    inspections.append(validate_image_upload(name, content))
                    contents[name] = content
                    if "caption" in item:
                        captions[name] = item["caption"]
                normalized, report = normalize_image_dataset(inspections, captions)
            except (ValueError, json.JSONDecodeError, base64.binascii.Error) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            images_dir = dataset_dir / "images"
            images_dir.mkdir(parents=True, exist_ok=True)
            for record in inspections:
                (images_dir / record.file_name).write_bytes(contents[record.file_name])
            (dataset_dir / "metadata.jsonl").write_text(
                "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in normalized),
                encoding="utf-8",
            )
            all_bytes = b"".join(contents[name] for name in sorted(contents))
            report["errors"] = []
            return store.create_dataset({
                "id": dataset_id, "name": clean_name, "type": datasetType, "structure": structure,
                "state": "ready", "sampleCount": len(inspections),
                "paths": {
                    "trainPath": str(dataset_dir / "metadata.jsonl"),
                    "mediaRoot": str(images_dir),
                    "manifestPath": str(dataset_dir / "metadata.jsonl"),
                },
                "sourceType": "local", "sourceUri": dataset_dir.resolve().as_uri(),
                "sizeBytes": len(all_bytes), "contentSha256": hashlib.sha256(all_bytes).hexdigest(),
                "validationMessage": "图片校验通过，可以发布。", "validationReport": report,
                "warningCount": 0, "owner": "平台管理员", "builtin": False,
            })
        with source_path.open("wb") as target:
            async for chunk in request.stream():
                size += len(chunk)
                digest.update(chunk)
                if size > MAX_VISION_ARCHIVE_BYTES:
                    raise HTTPException(status_code=413, detail="数据文件不能超过 2GB。")
                target.write(chunk)
        if is_visual:
            sample_count, validation_error, warnings = validate_visual_understanding_bundle(
                source_path, dataset_dir
            )
            report = {
                "checkedRows": sample_count,
                "errors": ([{"line": None, "message": validation_error}] if validation_error else []),
                "warnings": [{"line": None, "message": message} for message in warnings],
            }
            paths = {
                "trainPath": str(dataset_dir / "train.jsonl"),
                "mediaRoot": str(dataset_dir / "images"),
                "manifestPath": str(dataset_dir / "metadata.jsonl"),
                "archivePath": str(source_path),
            }
            validation_message = validation_error or "视觉理解数据包校验通过，可以发布。"
        else:
            report = validate_text_dataset_report(source_path, structure)
            sample_count = int(report["checkedRows"])
            validation_error = report["errors"][0]["message"] if report["errors"] else None
            warnings = []
            paths = {"trainPath": str(source_path)}
            validation_message = validation_error or "格式校验通过，可以发布。"
        return store.create_dataset(
            {
                "id": dataset_id,
                "name": clean_name,
                "type": datasetType,
                "structure": structure,
                "state": "failed" if validation_error else "ready",
                "sampleCount": sample_count,
                "paths": paths,
                "sourceType": "local",
                "sourceUri": source_path.resolve().as_uri(),
                "sizeBytes": size,
                "contentSha256": digest.hexdigest(),
                "validationMessage": validation_message,
                "validationReport": report,
                "warningCount": len(warnings),
                "owner": "平台管理员",
                "builtin": False,
            }
        )
    except Exception:
        shutil.rmtree(dataset_dir, ignore_errors=True)
        raise


async def _upload_dataset_text(
    request: Request,
    name: str,
    structure: Literal["SFT", "DPO"],
    filename: str,
    datasetType: Literal["training", "evaluation"] = "training",
    datasetId: str | None = None,
) -> dict[str, Any]:
    if datasetType == "evaluation":
        require_evaluation_enabled()
    clean_name = name.strip()
    safe_filename = Path(filename).name
    if not clean_name:
        raise HTTPException(status_code=422, detail="请填写数据集名称。")
    if Path(safe_filename).suffix.lower() != ".jsonl":
        raise HTTPException(status_code=422, detail="文本数据集仅支持 JSONL 文件。")

    existing = store.get_dataset(datasetId) if datasetId else None
    if datasetId and (existing is None or existing.get("purpose") != "evaluation"):
        raise HTTPException(status_code=404, detail="待追加的评测集不存在。")
    dataset_id = datasetId or f"ds-{uuid.uuid4().hex[:12]}"
    dataset_dir = DATASET_ROOT / dataset_id
    dataset_dir.mkdir(parents=True, exist_ok=bool(existing))
    source_path = dataset_dir / ("evaluation.jsonl" if datasetType == "evaluation" else safe_filename)
    upload_part = dataset_dir / f".upload-{uuid.uuid4().hex}.jsonl"
    size = 0
    digest = hashlib.sha256()
    try:
        with upload_part.open("wb") as target:
            async for chunk in request.stream():
                size += len(chunk)
                if size > (EVALUATION_MAX_FILE_BYTES if datasetType == "evaluation" else 2 * 1024 * 1024 * 1024):
                    limit = "200MB" if datasetType == "evaluation" else "2GB"
                    raise HTTPException(status_code=413, detail=f"数据文件不能超过 {limit}。")
                target.write(chunk)
        if datasetType == "evaluation":
            part_count = len(list(dataset_dir.glob("source-*.jsonl")))
            if part_count >= EVALUATION_MAX_FILES:
                raise HTTPException(status_code=413, detail="一个评测集最多上传 10 个文件。")
            raw_copy = dataset_dir / f"source-{part_count + 1:02d}-{safe_filename}"
            upload_part.replace(raw_copy)
            with raw_copy.open(encoding="utf-8-sig") as source, source_path.open("a", encoding="utf-8", newline="\n") as merged:
                for line_number, raw in enumerate(source, 1):
                    if not raw.strip():
                        continue
                    try:
                        record = json.loads(raw)
                    except json.JSONDecodeError:
                        record = {"_invalidRaw": raw.rstrip("\r\n"), "_sourceFile": safe_filename, "_sourceLine": line_number}
                    if isinstance(record, dict):
                        record["_sourceFile"] = safe_filename
                        record["_sourceLine"] = line_number
                    merged.write(json.dumps(record, ensure_ascii=False) + "\n")
            report = validate_evaluation_dataset_report(source_path)
            purpose = "evaluation"
            validation_message = report["errors"][0]["message"] if report["errors"] else "评测集格式校验通过，可以发布。"
        else:
            upload_part.replace(source_path)
            report = validate_text_dataset_report(source_path, structure)
            purpose = "training"
            validation_message = report["errors"][0]["message"] if report["errors"] else "格式校验通过，可以发布。"
        digest = hashlib.sha256()
        with source_path.open("rb") as hashed_source:
            while chunk := hashed_source.read(8 * 1024 * 1024):
                digest.update(chunk)
        sample_count = int(report["checkedRows"])
        result = store.create_dataset({
            "id": dataset_id, "name": clean_name, "type": "文本生成", "purpose": purpose,
            "structure": structure, "state": "failed" if report["errors"] else "ready",
            "sampleCount": sample_count, "paths": {"trainPath": str(source_path)},
            "sourceType": "local", "sourceUri": source_path.resolve().as_uri(),
            "sizeBytes": source_path.stat().st_size, "contentSha256": digest.hexdigest(),
            "validationMessage": validation_message, "validationReport": report,
            "warningCount": len(report.get("warnings", [])), "owner": authenticated_user_or_default().get("name", "平台管理员"), "builtin": False,
        })
        record_audit("create" if not existing else "append", "dataset", dataset_id, details={"name": clean_name, "structure": structure, "purpose": purpose, "filename": safe_filename})
        return result
    except Exception:
        upload_part.unlink(missing_ok=True)
        if not existing:
            shutil.rmtree(dataset_dir, ignore_errors=True)
        raise


@app.post("/api/datasets/upload")
async def upload_dataset(
    request: Request,
    name: str,
    structure: Literal["SFT", "DPO", "IMAGE_TEXT"],
    filename: str = "",
    datasetType: Literal["training", "evaluation", "文本生成", "文生图", "视觉理解"] = "training",
    datasetId: str | None = None,
) -> dict[str, Any]:
    if structure == "IMAGE_TEXT" or datasetType in {"文生图", "视觉理解"}:
        return await _upload_dataset_image_text(request, name, structure, filename, datasetType)
    return await _upload_dataset_text(request, name, structure, filename, datasetType, datasetId)



@app.get("/api/datasets/import-sources")
async def dataset_import_sources() -> list[dict[str, Any]]:
    try:
        return await asyncio.to_thread(list_dataset_import_sources)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"读取 MinIO 导入目录失败：{exc}") from exc


@app.post("/api/datasets/import", status_code=status.HTTP_201_CREATED)
async def import_dataset(payload: DatasetImportRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            import_dataset_from_minio, payload.sourceKey, payload.name, payload.structure
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"导入 MinIO 数据集失败：{exc}") from exc


def _dataset_source_path(dataset: dict[str, Any]) -> Path:
    raw = str((dataset.get("paths") or {}).get("trainPath") or "")
    if not raw:
        raise HTTPException(status_code=409, detail="数据集没有可用的训练文件。")
    path = Path(raw).expanduser().resolve()
    root = DATASET_ROOT.expanduser().resolve()
    if not path.is_file() or not path.is_relative_to(root):
        raise HTTPException(status_code=409, detail="数据集内容文件不存在或不在受控目录内。")
    return path


def _dataset_is_image_text(dataset: dict[str, Any]) -> bool:
    structure = str(dataset.get("structure") or "")
    return structure == "IMAGE_TEXT" or str(dataset.get("type") or "") in {"视觉理解", "文生图"}


def _image_text_dataset_dir(dataset: dict[str, Any]) -> Path:
    root = DATASET_ROOT.expanduser().resolve()
    manifest = str((dataset.get("paths") or {}).get("manifestPath") or "")
    if manifest:
        directory = Path(manifest).expanduser().resolve().parent
    else:
        directory = (root / str(dataset.get("id", ""))).resolve()
    if not directory.is_dir() or not directory.is_relative_to(root):
        raise HTTPException(status_code=409, detail="图片数据集目录不存在或不在受控目录内。")
    return directory


def _image_text_report(directory: Path) -> dict[str, Any]:
    checked, error, warnings = validate_extracted_image_text_dataset(directory)
    return {
        "checkedRows": checked,
        "errors": [{"line": None, "message": error}] if error else [],
        "warnings": [{"line": None, "message": message} for message in warnings],
    }


def _directory_sha256(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        digest.update(str(path.relative_to(directory)).encode("utf-8"))
        with path.open("rb") as source:
            while chunk := source.read(8 * 1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


def _dataset_is_referenced(dataset_id: str) -> bool:
    try:
        return any(
            str((task.get("payload") or {}).get("datasetId")) == dataset_id
            for task in store.list()
        )
    except Exception:
        return False


def _repair_candidate(dataset: dict[str, Any], payload: DatasetRepairRequest, target: Path) -> dict[str, int]:
    source_path = _dataset_source_path(dataset)
    if not payload.edits and payload.content is None:
        raise HTTPException(status_code=422, detail="请先修改至少一条数据，再预览修复结果。")
    if payload.edits:
        return repair_text_dataset(source_path, target, dataset.get("structure") or "SFT", payload.edits)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload.content or "", encoding="utf-8", newline="\n")
    return {"repairedRows": 0, "removedBlankLines": 0, "unchangedInvalidRows": 0}


@app.get("/api/datasets/{dataset_id}/validation")
async def dataset_validation(dataset_id: str) -> dict[str, Any]:
    dataset = store.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="数据集不存在。")
    if _dataset_is_image_text(dataset):
        # Image datasets are validated as a directory (metadata.jsonl + images/).
        report = await asyncio.to_thread(_image_text_report, _image_text_dataset_dir(dataset))
        return {"datasetId": dataset_id, "version": dataset.get("version", 1), "report": report}
    path = _dataset_source_path(dataset)
    # Re-run the current validator without mutating the source file.
    report = await asyncio.to_thread(validate_text_dataset_report, path, dataset.get("structure") or "SFT")
    return {"datasetId": dataset_id, "version": dataset.get("version", 1), "report": report}


@app.get("/api/datasets/{dataset_id}/validation/rows")
async def dataset_validation_rows(dataset_id: str) -> dict[str, Any]:
    """Return only invalid rows for the structured repair editor."""
    dataset = store.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="数据集不存在。")
    if _dataset_is_image_text(dataset):
        # Image datasets fail as a whole (missing/broken manifest or image);
        # per-line text repair does not apply.
        return {"datasetId": dataset_id, "rows": []}
    path = _dataset_source_path(dataset)
    report = await asyncio.to_thread(validate_text_dataset_report, path, dataset.get("structure") or "SFT")
    line_numbers = {
        int(item["line"])
        for item in [*(report.get("errors") or []), *(report.get("warnings") or [])]
        if item.get("line") is not None
    }
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as source:
        for line_number, raw_line in enumerate(source, 1):
            if line_number not in line_numbers:
                continue
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError:
                record = None
            rows.append({"line": line_number, "record": record, "raw": raw_line.rstrip("\r\n")})
    return {"datasetId": dataset_id, "rows": rows}


@app.post("/api/datasets/{dataset_id}/repair/preview")
async def preview_dataset_repair(dataset_id: str, payload: DatasetRepairRequest) -> dict[str, Any]:
    dataset = store.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="数据集不存在。")
    if _dataset_is_image_text(dataset):
        raise HTTPException(status_code=409, detail="图片类数据集不支持在线文本修复，请重新上传或使用重新校验。")
    source_path = _dataset_source_path(dataset)
    preview_path = source_path.parent / f".repair-preview-{uuid.uuid4().hex}.jsonl"
    try:
        stats = await asyncio.to_thread(_repair_candidate, dataset, payload, preview_path)
        report = await asyncio.to_thread(
            validate_text_dataset_report, preview_path, dataset.get("structure") or "SFT"
        )
        return {
            "datasetId": dataset_id,
            "mode": payload.mode,
            "stats": stats,
            "report": report,
            "canApply": not report.get("errors"),
        }
    finally:
        preview_path.unlink(missing_ok=True)


@app.post("/api/datasets/{dataset_id}/repair")
async def repair_dataset(dataset_id: str, payload: DatasetRepairRequest) -> dict[str, Any]:
    dataset = store.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="数据集不存在。")
    if dataset.get("builtin"):
        raise HTTPException(status_code=409, detail="内置数据集不能直接修复。")
    if _dataset_is_image_text(dataset):
        raise HTTPException(status_code=409, detail="图片类数据集不支持在线文本修复，请重新上传或使用重新校验。")
    source_path = _dataset_source_path(dataset)
    referenced = dataset.get("state") == "published" or _dataset_is_referenced(dataset_id)
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    # 修复始终生成独立数据集，避免覆盖原始文件和训练来源。
    new_id = f"ds-{uuid.uuid4().hex[:12]}"
    target_dir = DATASET_ROOT / new_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"repaired-{timestamp}.jsonl"
    original_copy = target_dir / f"original-v{int(dataset.get('version', 1))}.jsonl"
    try:
        stats = await asyncio.to_thread(_repair_candidate, dataset, payload, target_path)
        # 复制 validation/test/challenge 等训练相关文件，只有 train 文件应用修复结果。
        copied_paths: dict[str, str] = {"trainPath": str(target_path)}
        for key, raw in (dataset.get("paths") or {}).items():
            if key == "trainPath" or not raw:
                continue
            source = Path(str(raw))
            if source.is_file():
                destination = target_dir / source.name
                shutil.copy2(source, destination)
                copied_paths[key] = str(destination)
        report = await asyncio.to_thread(
            validate_text_dataset_report, target_path, dataset.get("structure") or "SFT"
        )
        if report.get("errors"):
            target_path.unlink(missing_ok=True)
            raise HTTPException(status_code=409, detail={"message": "修复后仍有校验错误。", "report": report, "stats": stats})
        history = list(dataset.get("repairHistory") or [])
        history.append({
            "mode": payload.mode,
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "fromVersion": int(dataset.get("version", 1)),
            "toVersion": int(dataset.get("version", 1)) + 1,
            "stats": stats,
        })
        updated = {
            **dataset,
            "id": new_id,
            "name": f"{dataset['name']} 修复版" if referenced else dataset["name"],
            "state": "ready",
            "sampleCount": report.get("checkedRows", 0),
            "validationMessage": "修复完成，格式校验通过，可以发布。",
            "validationReport": report,
            "repairHistory": history,
            "version": int(dataset.get("version", 1)) + 1,
            "paths": copied_paths,
            "owner": dataset.get("owner", "平台管理员"),
            "builtin": False,
        }
        result = store.create_dataset(updated) if referenced else store.create_dataset(updated)
        record_audit("repair", "dataset", dataset_id, details={"newDatasetId": new_id, "stats": stats})
        return {"dataset": result, "stats": stats, "report": report, "createdNewVersion": True}
    except HTTPException:
        raise
    except Exception:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise


@app.post("/api/datasets/{dataset_id}/revalidate")
async def revalidate_dataset(dataset_id: str) -> dict[str, Any]:
    dataset = store.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="数据集不存在。")
    if _dataset_is_image_text(dataset):
        report = await asyncio.to_thread(_image_text_report, _image_text_dataset_dir(dataset))
        message = (
            report["errors"][0]["message"] if report["errors"] else "图片数据集校验通过，可以发布。"
        )
    else:
        path = _dataset_source_path(dataset)
        report = await asyncio.to_thread(
            validate_evaluation_dataset_report, path
        ) if dataset.get("purpose") == "evaluation" else await asyncio.to_thread(
            validate_text_dataset_report, path, dataset.get("structure") or "SFT"
        )
        message = report["errors"][0]["message"] if report.get("errors") else "格式校验通过，可以发布。"
    updated = store.create_dataset({
        **dataset,
        "state": "failed" if report.get("errors") else "ready",
        "sampleCount": report.get("checkedRows", 0),
        "validationMessage": message,
        "validationReport": report,
    })
    return {"dataset": updated, "report": report}


@app.post("/api/datasets/{dataset_id}/publish")
async def publish_dataset(dataset_id: str) -> dict[str, Any]:
    dataset = store.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="数据集不存在。")
    report = dataset.get("validationReport") or {}
    if dataset["state"] == "failed" or report.get("errors"):
        raise HTTPException(status_code=409, detail="格式校验失败的数据集不能发布。")
    if dataset["state"] not in {"ready", "published"}:
        raise HTTPException(status_code=409, detail="数据集尚未完成校验，不能发布。")
    published = store.update_dataset_state(dataset_id, "published")
    assert published is not None
    record_audit("publish", "dataset", dataset_id)
    return published


@app.post("/api/datasets/{dataset_id}/clone", status_code=status.HTTP_201_CREATED)
async def clone_dataset(dataset_id: str) -> dict[str, Any]:
    dataset = store.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="数据集不存在。")
    clone_id = f"ds-{uuid.uuid4().hex[:12]}"
    clone_dir = DATASET_ROOT / clone_id
    if _dataset_is_image_text(dataset):
        source_dir = _image_text_dataset_dir(dataset)
        try:
            # 图片数据集按目录克隆：metadata/训练清单与 images/ 全量复制，
            # 原始上传 ZIP 不复制（体积大且校验不依赖它）。
            clone_dir.mkdir(parents=True, exist_ok=False)
            for item in sorted(source_dir.iterdir()):
                if item.is_file() and item.suffix.lower() == ".zip":
                    continue
                target = clone_dir / item.name
                if item.is_dir():
                    shutil.copytree(item, target)
                else:
                    shutil.copy2(item, target)
        except OSError as exc:
            shutil.rmtree(clone_dir, ignore_errors=True)
            raise HTTPException(status_code=409, detail=f"克隆图片数据集失败：{exc}") from exc
        clone_name = next_clone_name(dataset, store.list_datasets())
        report = await asyncio.to_thread(_image_text_report, clone_dir)
        paths = {
            key: str(clone_dir / Path(str(raw)).name)
            for key, raw in (dataset.get("paths") or {}).items()
            if raw and Path(str(raw)).suffix.lower() != ".zip"
        }
        validation_error = report["errors"][0]["message"] if report["errors"] else None
        return store.create_dataset(
            {
                "id": clone_id,
                "name": clone_name,
                "type": dataset["type"],
                "structure": dataset.get("structure"),
                "state": "failed" if validation_error else "ready",
                "sampleCount": report["checkedRows"],
                "paths": paths,
                "validationMessage": validation_error or "克隆完成，图片数据集校验通过，可以发布。",
                "validationReport": report,
                "warningCount": len(report.get("warnings", [])),
                "sourceType": "local",
                "sourceUri": clone_dir.resolve().as_uri(),
                "sizeBytes": sum(path.stat().st_size for path in clone_dir.rglob("*") if path.is_file()),
                "contentSha256": await asyncio.to_thread(_directory_sha256, clone_dir),
                "owner": "平台管理员",
                "builtin": False,
            }
        )
    source_path = Path(dataset["paths"]["trainPath"])
    if not source_path.exists() or not source_path.is_file():
        raise HTTPException(status_code=409, detail="数据集内容文件不存在，无法克隆。")
    clone_dir.mkdir(parents=True, exist_ok=False)
    clone_path = clone_dir / source_path.name
    shutil.copy2(source_path, clone_path)
    clone_name = next_clone_name(dataset, store.list_datasets())
    report = validate_evaluation_dataset_report(clone_path) if dataset.get("purpose") == "evaluation" else validate_text_dataset_report(clone_path, dataset.get("structure") or "SFT")
    validation_error = report["errors"][0]["message"] if report["errors"] else None
    return store.create_dataset(
        {
            "id": clone_id,
            "name": clone_name,
            "type": dataset["type"],
            "structure": dataset.get("structure"),
            "state": "failed" if validation_error else "ready",
            "sampleCount": report["checkedRows"],
            "paths": {"trainPath": str(clone_path)},
            "validationMessage": validation_error or "克隆完成，格式校验通过，可以发布。",
            "validationReport": report,
            "warningCount": len(report.get("warnings", [])),
            "sourceType": "local",
            "sourceUri": clone_path.resolve().as_uri(),
            "sizeBytes": clone_path.stat().st_size,
            "contentSha256": file_sha256(clone_path),
            "owner": "平台管理员",
            "builtin": False,
        }
    )


@app.delete("/api/datasets/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dataset(dataset_id: str) -> Response:
    try:
        dataset = store.delete_dataset(dataset_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if dataset is None:
        raise HTTPException(status_code=404, detail="数据集不存在。")
    # 软删除只隐藏业务记录；文件暂不物理删除，便于模型来源追溯和人工恢复。
    # IMAGE_TEXT 数据集是成批图片资产（可达数 GB），删除时物理清理目录。
    paths_meta = dataset.get("paths") or {}
    is_image_dataset = dataset.get("structure") == "IMAGE_TEXT" or (
        bool(paths_meta.get("manifestPath")) and not paths_meta.get("trainPath")
    )
    if is_image_dataset:
        dataset_root = DATASET_ROOT.resolve()
        dataset_dir = (dataset_root / dataset_id).resolve()
        if dataset_dir.is_relative_to(dataset_root):
            shutil.rmtree(dataset_dir, ignore_errors=True)
    record_audit("delete", "dataset", dataset_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/datasets/{dataset_id}/prompts")
async def dataset_prompts(
    dataset_id: str,
    split: Literal["train", "validation", "test", "challenge"] = "train",
    limit: int = 20,
    validation_split: int = 10,
    seed: int = 42,
) -> list[dict[str, str]]:
    dataset = store.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="数据集不存在。")
    paths = dataset["paths"]
    path_key = {
        "train": "trainPath",
        "validation": "validationPath",
        "test": "testPath",
        "challenge": "challengePath",
    }[split]
    configured_path = paths.get(path_key)
    if split in {"test", "challenge"} and not configured_path:
        return []

    records: list[dict[str, Any]]
    if split in {"train", "validation"} and not paths.get("validationPath") and validation_split > 0:
        # Preview sampling does not load a training runtime. This is a stable
        # preview partition, not a claim to reproduce the trainer's split.
        def preview_partition() -> list[dict[str, Any]]:
            with Path(paths["trainPath"]).open(encoding="utf-8") as source:
                rows = [json.loads(line) for line in source if line.strip()]
            random.Random(seed).shuffle(rows)
            count = min(len(rows), math.ceil(len(rows) * min(max(validation_split, 1), 30) / 100))
            return (rows[count:] if split == "train" else rows[:count])[:min(max(limit, 1), 100)]

        records = await asyncio.to_thread(preview_partition)
    else:
        path = Path(configured_path or paths["trainPath"])
        records = []
        with path.open(encoding="utf-8") as source:
            for line in source:
                if len(records) >= min(max(limit, 1), 100):
                    break
                if line.strip():
                    records.append(json.loads(line))

    prompts: list[dict[str, str]] = []
    for index, record in enumerate(records):
        messages = record.get("messages", [])
        user = next(
            (item.get("content", "") for item in messages if item.get("role") == "user"),
            "",
        )
        system = next(
            (item.get("content", "") for item in messages if item.get("role") == "system"),
            "",
        )
        reference = next(
            (item.get("content", "") for item in messages if item.get("role") == "assistant"),
            "",
        )
        if user:
            prompts.append(
                {
                    "id": f"{split}-{index + 1}",
                    "prompt": user,
                    "system": system,
                    "reference": reference,
                }
            )
    return prompts


@app.get("/api/datasets/{dataset_id}/records")
async def dataset_records(
    dataset_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, alias="pageSize", ge=1, le=100),
    query: str = Query("", max_length=200),
) -> dict[str, Any]:
    """Read a paginated, type-agnostic view of a dataset's source records."""
    dataset = store.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="数据集不存在。")
    paths = dataset.get("paths") or {}
    configured_source = paths.get("trainPath") or (
        paths.get("manifestPath") if dataset.get("structure") == "IMAGE_TEXT" else ""
    )
    source_path = Path(str(configured_source or ""))
    if not source_path.exists() or not source_path.is_file():
        raise HTTPException(status_code=404, detail="数据集内容文件不存在。")

    normalized_query = query.strip().casefold()
    matched_total = 0
    start = (page - 1) * page_size
    end = start + page_size
    items: list[dict[str, Any]] = []
    with source_path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                record: Any = json.loads(line)
            except json.JSONDecodeError:
                record = {"_error": f"第 {line_number} 行不是合法 JSON。", "_line": line_number}
            if normalized_query and normalized_query not in json.dumps(
                record, ensure_ascii=False, default=str
            ).casefold():
                continue
            record_index = matched_total + 1
            matched_total += 1
            if start < record_index <= end:
                items.append({"index": record_index, "record": record})

    total_pages = max(1, (matched_total + page_size - 1) // page_size)
    return {
        "items": items,
        "page": min(page, total_pages),
        "pageSize": page_size,
        "total": matched_total,
        "totalPages": total_pages,
    }


@scoped_resource("task")
async def run_youyun_chat(task: dict[str, Any], payload: ChatRequest) -> dict[str, Any]:
    """优云任务的效果对比：只在保留中的远程 GPU 实例上推理，不回退本地。"""
    from backend.providers.youyun.remote_chat import RemoteComparisonError

    marker = resolve_youyun_instance_artifact(task.get("artifacts") or [])
    if marker is None or str(marker.get("state")) != "awaiting_comparison":
        raise HTTPException(
            status_code=409,
            detail="优云实例已释放，无法进行效果对比；请重新运行任务后再试。",
        )
    tuning_mode = task.get("tuningMode", "qlora")
    adapter = resolve_adapter_artifact(task.get("artifacts", []), payload.adapterVariant)
    if adapter is None:
        label = {
            "selected": "可用",
            "best": "最佳",
            "final": "最终",
        }[payload.adapterVariant]
        raise HTTPException(status_code=404, detail=f"该任务没有{label} LoRA Adapter。")
    remote_path = adapter.get("remotePath")
    remote_ready = bool(remote_path) and str(adapter.get("syncState") or "") in {
        "remote_ready", "syncing", "available",
    }
    if remote_ready:
        runtime_adapter_path = _validate_remote_adapter_path(payload.taskId, str(remote_path))
    else:
        resolved = Path(adapter["path"]).expanduser().resolve()
        task_adapter_roots = (
            (ARTIFACT_ROOT / payload.taskId).resolve(),
            (local_executor.work_root / payload.taskId).resolve(),
            (youyun_mirror_executor.work_root / payload.taskId).resolve(),
        )
        if (
            not any(resolved.is_relative_to(root) for root in task_adapter_roots)
            or not resolved.is_dir()
        ):
            raise HTTPException(status_code=403, detail="LoRA Adapter 路径无效。")
        runtime_adapter_path = str(adapter["path"])
    adapter_variant = adapter.get("variant")
    if adapter_variant not in {"best", "final"}:
        adapter_variant = "final"
    adapter_step = int(adapter["checkpointStep"]) if adapter.get("checkpointStep") is not None else None
    touch_youyun_retention(task["id"])
    manager = get_remote_chat_manager()
    try:
        event = await asyncio.wait_for(
            asyncio.shield(
                manager.compare(
                    task,
                    runtime_adapter_path,
                    messages=[
                        {"role": item.role, "content": item.content} for item in payload.messages
                    ],
                    parameters=payload.parameters.model_dump(),
                    adapter_enabled=payload.mode == "adapter",
                    load_in_4bit=tuning_mode == "qlora",
                )
            ),
            timeout=REMOTE_CHAT_WAIT_SECONDS,
        )
    except asyncio.TimeoutError:
        # shield 保证被取消的只是等待：后台会话/模型加载继续完成，重试即命中。
        raise HTTPException(
            status_code=503,
            detail="优云对比模型正在预热（首次约需 1 分钟），请稍候几秒后重试。",
        ) from None
    except RemoteComparisonError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    touch_youyun_retention(task["id"])
    response: dict[str, Any] = {
        "text": event["text"],
        "elapsedSeconds": event["elapsedSeconds"],
        "outputTokens": event["outputTokens"],
        "thinking": False,
    }
    if payload.mode == "adapter":
        response.update(
            adapterVariant=adapter_variant,
            adapterStep=adapter_step,
            adapterPath=adapter["path"],
        )
    return response


@app.post("/api/chat")
async def chat(payload: ChatRequest) -> dict[str, Any]:
    if payload.mode == "adapter" and not payload.taskId:
        raise HTTPException(
            status_code=422,
            detail="微调模型对话需要任务 ID。",
        )
    if payload.taskId:
        youyun_task = store.get(payload.taskId)
        if youyun_task is None:
            raise HTTPException(status_code=404, detail="训练任务不存在。")
        if youyun_task.get("provider") == "youyun":
            return await run_youyun_chat(youyun_task, payload)
    require_local_runtime()
    if active_task_id is not None:
        raise HTTPException(
            status_code=409,
            detail="GPU 正在执行训练任务，请等待训练完成后再进行对话。",
        )

    runtime_adapter_path: str | None = None
    adapter_variant: str | None = None
    adapter_step: int | None = None
    tuning_mode = payload.tuningMode
    if payload.taskId:
        task = store.get(payload.taskId)
        if task is None:
            raise HTTPException(status_code=404, detail="训练任务不存在。")
        tuning_mode = task.get("tuningMode", "qlora")
        if task["state"] != "completed":
            raise HTTPException(status_code=409, detail="训练完成后才能加载 LoRA Adapter。")
        # A comparison keeps one adapter-backed process alive. Base generation
        # temporarily disables the adapter; tuned generation enables it again.
        adapter = resolve_adapter_artifact(
            task.get("artifacts", []), payload.adapterVariant
        )
        if adapter is None:
            label = {
                "selected": "可用",
                "best": "最佳",
                "final": "最终",
            }[payload.adapterVariant]
            raise HTTPException(
                status_code=404, detail=f"该任务没有{label} LoRA Adapter。"
            )
        resolved = Path(adapter["path"]).expanduser().resolve()
        task_adapter_roots = (
            (ARTIFACT_ROOT / payload.taskId).resolve(),
            (local_executor.work_root / payload.taskId).resolve(),
            (youyun_mirror_executor.work_root / payload.taskId).resolve(),
        )
        if (
            not any(resolved.is_relative_to(root) for root in task_adapter_roots)
            or not resolved.is_dir()
        ):
            raise HTTPException(status_code=403, detail="LoRA Adapter 路径无效。")
        # Keep the stored spelling for the subprocess/API contract after the
        # resolved path has passed the controlled-root check. This preserves
        # Windows path casing/short-name semantics used by local callers.
        runtime_adapter_path = str(adapter["path"])
        adapter_variant = adapter.get("variant")
        if adapter_variant not in {"best", "final"}:
            adapter_variant = "final"
        if adapter.get("checkpointStep") is not None:
            adapter_step = int(adapter["checkpointStep"])

    async with chat_lock:
        if active_task_id is not None:
            raise HTTPException(
                status_code=409,
                detail="GPU 正在执行训练任务，请等待训练完成后再进行对话。",
            )
        try:
            process = await ensure_chat_process(
                model=payload.model,
                source=payload.modelSource,
                adapter_path=runtime_adapter_path,
                load_in_4bit=tuning_mode == "qlora",
            )
            assert process.stdin is not None
            request_id = uuid.uuid4().hex
            request = {
                "requestId": request_id,
                "messages": [
                    {"role": item.role, "content": item.content}
                    for item in payload.messages
                ],
                "parameters": payload.parameters.model_dump(),
                "adapterEnabled": payload.mode == "adapter",
            }
            process.stdin.write((json.dumps(request, ensure_ascii=False) + "\n").encode())
            await process.stdin.drain()
            event = await asyncio.wait_for(
                read_chat_event(
                    process,
                    event_type="response",
                    request_id=request_id,
                ),
                timeout=180,
            )
        except (OSError, RuntimeError, asyncio.TimeoutError) as exc:
            await stop_chat_process()
            detail = "推理超时。" if isinstance(exc, asyncio.TimeoutError) else str(exc)
            raise HTTPException(status_code=500, detail=detail) from exc
    response: dict[str, Any] = {
        "text": event["text"],
        "elapsedSeconds": event["elapsedSeconds"],
        "outputTokens": event["outputTokens"],
        "thinking": False,
    }
    if payload.mode == "adapter" and runtime_adapter_path is not None:
        response.update(
            adapterVariant=adapter_variant,
            adapterStep=adapter_step,
            adapterPath=runtime_adapter_path,
        )
    return response


def _artifact_preview_roots() -> tuple[Path, ...]:
    return tuple(
        dict.fromkeys(
            root.resolve()
            for root in (
                ARTIFACT_ROOT,
                local_executor.work_root,
                youyun_mirror_executor.work_root,
            )
        )
    )


def _resolve_artifact_path(path: str, *, relative_path: str | None = None) -> Path:
    roots = _artifact_preview_roots()
    requested = Path(path).expanduser().resolve()
    if not any(requested.is_relative_to(root) for root in roots):
        raise HTTPException(status_code=403, detail="只能访问当前项目的训练产物目录。")
    if not requested.exists():
        raise HTTPException(status_code=404, detail="产物路径不存在。")
    if relative_path:
        if not requested.is_dir():
            raise HTTPException(status_code=422, detail="产物预览根路径必须是目录。")
        target = (requested / relative_path).resolve()
        if not target.is_relative_to(requested):
            raise HTTPException(status_code=403, detail="文件路径无效。")
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=404, detail="产物文件不存在。")
        return target
    return requested


@app.post("/api/artifacts/preview")
async def preview_artifact(payload: ArtifactPreviewRequest) -> dict[str, Any]:
    requested = _resolve_artifact_path(payload.path, relative_path=payload.relativePath)
    if payload.relativePath or requested.is_file():
        suffix = requested.suffix.lower()
        text_suffixes = {
            ".json", ".jsonl", ".txt", ".md", ".py", ".yaml", ".yml", ".ini",
            ".cfg", ".conf", ".xml", ".csv", ".tsv", ".log",
        }
        stat = requested.stat()
        result: dict[str, Any] = {
            "path": payload.relativePath or str(requested),
            "sizeBytes": stat.st_size,
            "modifiedAt": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "previewable": suffix in text_suffixes,
            "status": "available",
            "md5": (await asyncio.to_thread(_md5, requested))[0],
        }
        if suffix not in text_suffixes:
            result.update({
                "kind": "binary",
                "message": "这是二进制权重或模型文件，平台不直接展示原始内容。",
            })
            return result
        limit = 512 * 1024
        with requested.open("rb") as source:
            raw = source.read(limit + 1)
        result.update({
            "kind": "text",
            "truncated": len(raw) > limit,
            "content": raw[:limit].decode("utf-8", errors="replace"),
        })
        return result

    if not requested.is_dir():
        requested = requested.parent
    files: list[dict[str, Any]] = []
    for item in sorted(
        (
            entry
            for entry in requested.rglob("*")
            if entry.is_file() and entry.resolve().is_relative_to(requested)
        ),
        key=lambda entry: str(entry).lower(),
    )[:200]:
        stat = item.stat()
        files.append({
            "path": item.relative_to(requested).as_posix(),
            "sizeBytes": stat.st_size,
            "modifiedAt": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "status": "available",
        })
    return {
        "path": str(requested),
        "name": requested.name,
        "files": files,
        "truncated": len(files) >= 200,
    }


@app.post("/api/artifacts/open")
async def open_artifact(payload: ArtifactOpenRequest) -> dict[str, str]:
    requested = _resolve_artifact_path(payload.path)

    target = requested if requested.is_dir() else requested.parent
    try:
        converted = subprocess.run(
            ["wslpath", "-w", str(target)],
            check=True,
            capture_output=True,
            text=True,
        )
        windows_path = converted.stdout.strip()
        if not windows_path:
            raise RuntimeError("wslpath 未返回 Windows 路径")
        subprocess.Popen(
            ["explorer.exe", windows_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        raise HTTPException(status_code=500, detail=f"无法打开产物目录：{exc}") from exc
    return {"message": "已在文件资源管理器中打开产物目录。", "path": windows_path}


@app.get("/api/tasks")
async def list_tasks(
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=100),
    state: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    # The list view only needs task summaries. Logs, metrics, and full payloads
    # are loaded lazily by the task detail endpoint to keep entry fast.
    return store.list_page(
        page=page,
        page_size=pageSize,
        state=state,
        query=query,
        include_details=False,
    )


@app.post("/api/tasks", status_code=status.HTTP_201_CREATED)
async def create_task(payload: TaskCreate) -> dict[str, Any]:
    require_provider(payload.provider)
    user = authenticated_user_or_default()
    values = payload.model_dump()
    values["owner"] = user.get("name") or user.get("sub") or values.get("owner")
    if payload.framework != "llamafactory":
        raise HTTPException(status_code=422, detail="当前阶段只支持 LLaMA-Factory。")
    if payload.provider not in {"local", "youyun"}:
        raise HTTPException(status_code=422, detail="不支持的执行 Provider。")
    if payload.provider == "local" and payload.executionMode != "local":
        raise HTTPException(status_code=409, detail="本地任务必须使用本地执行方式。")
    if payload.provider == "youyun":
        if payload.executionMode != "remote":
            raise HTTPException(status_code=409, detail="远程优云任务必须选择远程执行方式。")
        values["youyunResource"] = await resolve_youyun_resource(payload.youyunResource)
    elif payload.youyunResource is not None:
        raise HTTPException(status_code=409, detail="本地任务不能配置优云 GPU 资源。")
    dataset = store.get_dataset(values["datasetId"])
    if dataset is None:
        raise HTTPException(
            status_code=422,
            detail="数据集不存在或尚未完成上传。",
        )
    if dataset["state"] != "published":
        raise HTTPException(status_code=409, detail="请选择已发布的数据集。")
    if dataset.get("purpose", "training") != "training":
        raise HTTPException(status_code=409, detail="评测集不能用于微调训练。")
    if payload.taskType != (dataset.get("structure") or "SFT"):
        raise HTTPException(status_code=409, detail="训练类型与数据集结构不匹配，请重新选择数据集。")
    current_version = str(dataset.get("version") or 1)
    if payload.datasetVersion and payload.datasetVersion != current_version:
        raise HTTPException(status_code=409, detail="所选数据集版本不是当前可训练版本。")
    values["datasetVersion"] = current_version
    try:
        validate_extra_parameters(payload.extraParameters)
    except CliValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return store.create(values)


@app.post("/api/tasks/cli", status_code=status.HTTP_201_CREATED)
async def create_cli_task(payload: CliTaskCreate) -> dict[str, Any]:
    require_local_runtime()
    user = authenticated_user_or_default()
    try:
        values = parse_cli_command(payload.command)
    except CliValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    dataset = store.get_dataset(values["datasetId"])
    if dataset is None:
        raise HTTPException(status_code=422, detail="数据集不存在或尚未完成上传。")
    if dataset["state"] != "published":
        raise HTTPException(status_code=409, detail="请选择已发布的数据集。")
    if dataset.get("purpose", "training") != "training":
        raise HTTPException(status_code=409, detail="评测集不能用于微调训练。")
    dataset_structure = str(dataset.get("structure") or "SFT")
    if values["taskType"] != dataset_structure:
        raise HTTPException(
            status_code=422,
            detail=(
                f"训练类型 {values['taskType']} 与数据集结构 "
                f"{dataset_structure} 不匹配。"
            ),
        )
    values["datasetName"] = dataset["name"]
    values["datasetVersion"] = str(dataset.get("version") or 1)
    values["owner"] = user.get("name") or user.get("sub") or values.get("owner")
    try:
        validated = TaskCreate.model_validate(values)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"命令参数校验失败：{exc}") from exc
    if validated.taskType != (dataset.get("structure") or "SFT"):
        raise HTTPException(status_code=409, detail="训练类型与数据集结构不匹配，请重新选择数据集。")
    if validated.provider == "youyun":
        if validated.youyunResource is None:
            raise HTTPException(status_code=409, detail="远程优云任务必须配置单卡 GPU 资源。")
        values["youyunResource"] = await resolve_youyun_resource(validated.youyunResource)
    return store.create(values)


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str) -> dict[str, Any]:
    task = store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在。")
    return task


@app.post("/api/tasks/{task_id}/artifacts/sync")
async def retry_task_artifact_sync(task_id: str) -> dict[str, Any]:
    task = store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在。")
    if task.get("state") not in {"completed", "stopped"}:
        raise HTTPException(status_code=409, detail="只有已完成或已停止任务才能同步产物。")
    marker = resolve_youyun_instance_artifact(task.get("artifacts") or [])
    if (task.get("provider") == "youyun"
            and not await asyncio.to_thread(_local_task_adapters_complete, task)
            and not await asyncio.to_thread(_verified_task_mirror, task_id, task)):
        if not marker or not marker.get("instanceId") or marker.get("state") not in {"awaiting_comparison", "debugging"}:
            raise HTTPException(status_code=409, detail="本地权重不完整且没有可回传的保留实例。")
    schedule_remote_artifact_sync(task_id)
    return store.get(task_id) or task


@app.get("/api/tasks/{task_id}/logs")
async def get_task_logs(task_id: str, limit: int = Query(default=300, ge=1, le=5000), attemptId: str | None = None, afterId: int = Query(default=0, ge=0)) -> dict[str, Any]:
    task = store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在。")
    if hasattr(store, "log_items"):
        return store.log_items(task_id, attempt_id=attemptId, after_id=afterId, limit=limit)
    return {"taskId": task_id, "logs": store.logs(task_id, limit=limit)}


@app.get("/api/tasks/{task_id}/attempts")
async def get_task_attempts(task_id: str) -> list[dict[str, Any]]:
    if store.get(task_id) is None:
        raise HTTPException(status_code=404, detail="任务不存在。")
    return store.list_attempts(task_id) if hasattr(store, "list_attempts") else []


@app.get("/api/tasks/{task_id}/metrics")
async def get_task_metrics(task_id: str, attemptId: str | None = None) -> dict[str, Any]:
    task = store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在。")
    return {"taskId": task_id, "metrics": store.metrics(task_id, attempt_id=attemptId) if attemptId else store.metrics(task_id)}


@app.websocket("/api/tasks/{task_id}/stream")
async def task_stream(websocket: WebSocket, task_id: str) -> None:
    if not websocket_authenticated(websocket):
        await websocket.close(code=4401)
        return
    task = store.get(task_id)
    if task is None:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    try:
        sent = max(0, int(websocket.query_params.get("afterId", "0")))
    except (TypeError, ValueError):
        sent = 0
    initial_snapshot = sent == 0
    last_state: tuple[Any, ...] | None = None
    last_metrics: str | None = None
    last_heartbeat = time.monotonic()
    heartbeat_interval = WEBSOCKET_HEARTBEAT_INTERVAL_SECONDS
    try:
        while True:
            try:
                current = store.get(task_id)
            except Exception as exc:  # noqa: BLE001 - transient DB errors must not close the log stream
                logger.warning("[task-stream] 读取任务状态失败，保持 WebSocket 并重试：%s", exc)
                await asyncio.sleep(0.8)
                continue
            if current is None:
                await websocket.close(code=4404)
                return
            if hasattr(store, "log_items"):
                while True:
                    try:
                        payload = store.log_items(task_id, after_id=sent, limit=500)
                    except Exception as exc:  # noqa: BLE001 - log polling is best effort
                        logger.warning("[task-stream] 读取任务日志失败，保持 WebSocket：%s", exc)
                        break
                    previous = sent
                    for item in payload.get("items", []):
                        item_id = int(item.get("id") or 0)
                        if item_id <= sent:
                            continue
                        sent = item_id
                        await websocket.send_json({
                            "type": "log",
                            "taskId": task_id,
                            "id": item_id,
                            "seq": item_id,
                            "attemptId": item.get("attempt_id") or payload.get("attemptId"),
                            "level": item.get("level"),
                            "source": item.get("source"),
                            "createdAt": item.get("created_at").isoformat()
                            if isinstance(item.get("created_at"), datetime)
                            else item.get("created_at"),
                            "snapshot": initial_snapshot,
                            "message": item.get("message") or "",
                        })
                    if not payload.get("hasMore") or sent == previous:
                        break
            state_key = (current.get("state"), current.get("progress"), current.get("currentStep"))
            if state_key != last_state:
                await websocket.send_json({
                    "type": "state",
                    "taskId": task_id,
                    "state": current.get("state"),
                    "progress": current.get("progress"),
                    "currentStep": current.get("currentStep"),
                    "task": current,
                    "snapshot": initial_snapshot,
                })
                last_state = state_key
            if hasattr(store, "metrics"):
                try:
                    metrics = store.metrics(task_id)
                except Exception as exc:  # noqa: BLE001 - a metric read must not terminate logs
                    logger.warning("[task-stream] 读取任务指标失败，保持 WebSocket：%s", exc)
                else:
                    metrics_key = json.dumps(metrics, sort_keys=True, ensure_ascii=False, default=str)
                    if metrics_key != last_metrics:
                        await websocket.send_json({"type": "metrics", "taskId": task_id, "metrics": metrics})
                        last_metrics = metrics_key
            if current.get("state") in {"completed", "failed", "stopped"}:
                await websocket.send_json({
                    "type": "terminal",
                    "taskId": task_id,
                    "state": current.get("state"),
                    "task": current,
                })
                await websocket.close(code=1000)
                return
            now = time.monotonic()
            if now - last_heartbeat >= heartbeat_interval:
                await websocket.send_json({
                    "type": "heartbeat",
                    "taskId": task_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                last_heartbeat = now
            initial_snapshot = False
            await asyncio.sleep(0.8)
    except WebSocketDisconnect:
        return


@app.post("/api/tasks/{task_id}/adapter-selection")
async def select_task_adapter(
    task_id: str, payload: AdapterSelectionRequest
) -> dict[str, Any]:
    try:
        task = store.select_adapter(task_id, payload.variant)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在。")
    sync_model_assets(force=True)
    return task


@app.post("/api/tasks/{task_id}/stop")
async def stop_task(task_id: str) -> dict[str, Any]:
    task = store.request_stop(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在。")
    record_audit("stop", "task", task_id)
    return task


@app.post("/api/tasks/{task_id}/youyun/release")
async def release_youyun_instance(task_id: str) -> dict[str, Any]:
    task = store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在。")
    marker = resolve_youyun_instance_artifact(task.get("artifacts") or [])
    if task.get("provider") != "youyun" or marker is None:
        raise HTTPException(status_code=409, detail="任务没有可释放的优云实例。")
    if task.get("state") not in {"completed", "stopped"} or marker.get("state") not in {
        "awaiting_comparison",
        "debugging",
        "cleanup_failed",
    }:
        raise HTTPException(status_code=409, detail="训练尚未进入人工释放阶段。")
    try:
        return await cleanup_youyun_task_instance(task_id, reason="结束任务并释放")
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"释放优云实例失败：{exc}") from exc


@app.post("/api/tasks/{task_id}/youyun/destroy")
async def destroy_youyun_instance(task_id: str) -> dict[str, Any]:
    task = store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在。")
    if task.get("state") not in {"completed", "failed", "stopped"}:
        raise HTTPException(status_code=409, detail="训练仍在运行，请先停止任务。")
    try:
        return await cleanup_youyun_task_instance(task_id, reason="销毁")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"销毁优云实例失败：{exc}") from exc


@app.post("/api/tasks/{task_id}/retry")
async def retry_task(task_id: str) -> dict[str, Any]:
    current = store.get(task_id)
    if current is None:
        raise HTTPException(status_code=404, detail="任务不存在。")
    require_provider(current.get("provider"))
    if current.get("provider") == "youyun":
        resource = current.get("youyunResource") or {}
        await resolve_youyun_resource(YouyunResourceSelection.model_validate(resource))
    try:
        task = store.retry(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在。")
    record_audit("retry", "task", task_id)
    return task


@app.post("/api/tasks/{task_id}/clone", status_code=status.HTTP_201_CREATED)
async def clone_task(task_id: str) -> dict[str, Any]:
    source = store.get(task_id)
    if source is None:
        raise HTTPException(status_code=404, detail="任务不存在。")
    require_provider(source.get("provider"))
    if source.get("provider") == "youyun":
        # 与创建任务一致：点击克隆时同步校验库存，无库存立即失败并提示更换。
        resource = dict(source.get("youyunResource") or {})
        field_names = set(YouyunResourceSelection.model_fields)
        try:
            selection = YouyunResourceSelection.model_validate(
                {key: value for key, value in resource.items() if key in field_names}
            )
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail="克隆任务的优云资源配置无效，请新建任务并选择可用 GPU。",
            ) from exc
        await resolve_youyun_resource(selection)
    task = store.clone(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在。")
    return task


@app.delete("/api/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: str) -> Response:
    try:
        deleted = store.delete(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="任务不存在。")
    record_audit("delete", "task", task_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/overview")
async def overview() -> dict[str, Any]:
    datasets_payload = store.list_datasets()
    tasks_payload = store.list()
    model_assets_payload = store.list_model_assets(include_archived=False)
    services_payload = inference_store.list(include_archived=False)
    gpu_payload = await asyncio.to_thread(probe_gpu_info)
    active_service = next(
        (service for service in services_payload if service.get("state") == "running"),
        None,
    )
    inference_metrics_payload = None
    if active_service is not None:
        try:
            inference_metrics_payload = await inference_runtime.metrics(active_service["id"])
        except (KeyError, OSError, RuntimeError, TimeoutError):
            inference_metrics_payload = None
    return build_overview_snapshot(
        datasets=datasets_payload,
        tasks=tasks_payload,
        model_assets=model_assets_payload,
        services=services_payload,
        health=health_snapshot(),
        gpu=gpu_payload,
        shared_storage={
            "status": "ready" if model_uploader is not None else "unavailable",
            "error": model_storage_error,
        },
        inference_metrics=inference_metrics_payload,
        generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
    )

# Durable model-evaluation API. The records are immutable snapshots created from a published evaluation dataset.
def _evaluation_cosine(left: str, right: str) -> float:
    def counts(value: str) -> dict[str, int]:
        output: dict[str, int] = {}
        for token in re.findall(r"[\w\u4e00-\u9fff]+", value.lower()):
            output[token] = output.get(token, 0) + 1
        return output
    a, b = counts(left), counts(right)
    if not a or not b:
        return 0.0
    return sum(a.get(key, 0) * value for key, value in b.items()) / ((sum(v * v for v in a.values()) ** .5) * (sum(v * v for v in b.values()) ** .5))


def _evaluation_similarity(left: str, right: str) -> dict[str, Any]:
    base_url = os.environ.get("XUNTUI_EVALUATION_EMBEDDING_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("XUNTUI_EVALUATION_EMBEDDING_API_KEY", "")
    model = os.environ.get("XUNTUI_EVALUATION_EMBEDDING_MODEL", "")
    if not base_url or not api_key or not model:
        return {"score": _evaluation_cosine(left, right), "kind": "lexical_cosine", "status": "fallback", "note": "Embedding provider is not configured."}
    body = json.dumps({"model": model, "input": [left, right]}).encode("utf-8")
    request = urllib.request.Request(base_url + "/embeddings", data=body, headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json", "User-Agent": "Xuntui-Evaluation/1.0"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            vectors = [item["embedding"] for item in json.loads(response.read().decode("utf-8"))["data"]]
        if len(vectors) != 2:
            raise RuntimeError("embedding response must contain two vectors")
        dot = sum(float(a) * float(b) for a, b in zip(vectors[0], vectors[1]))
        left_norm = sum(float(a) * float(a) for a in vectors[0]) ** .5
        right_norm = sum(float(b) * float(b) for b in vectors[1]) ** .5
        return {"score": dot / (left_norm * right_norm) if left_norm and right_norm else 0.0, "kind": "embedding_cosine", "status": "computed", "model": model}
    except Exception as exc:
        return {"score": _evaluation_cosine(left, right), "kind": "lexical_cosine", "status": "fallback", "error": str(exc)}


def _evaluation_teacher(config: dict[str, Any], prompt: str, reference: str, answer: str, dimensions: list[str]) -> dict[str, Any]:
    base_url = str(config.get("baseUrl") or os.environ.get("XUNTUI_EVALUATION_JUDGE_BASE_URL", "")).rstrip("/")
    api_key = os.environ.get("XUNTUI_EVALUATION_JUDGE_API_KEY", "")
    model = str(config.get("model") or os.environ.get("XUNTUI_EVALUATION_JUDGE_MODEL", ""))
    if not base_url or not api_key or not model:
        raise RuntimeError("Teacher/Judge is not configured; manual review remains available.")
    rubric = ", ".join(dimensions) or "correctness, relevance, completeness"
    content = "You are a strict answer judge. Evaluate the model answer against the question and reference answer on " + rubric + ". Return JSON only with score (0..5), dimensions, reason, suggestedAttribution.\nQuestion: " + prompt + "\nReference: " + reference + "\nAnswer: " + answer
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": content}], "temperature": 0, "response_format": {"type": "json_object"}}).encode("utf-8")
    request = urllib.request.Request(base_url + "/chat/completions", data=body, headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json", "User-Agent": "Xuntui-Evaluation/1.0"}, method="POST")
    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.loads(json.loads(response.read().decode("utf-8"))["choices"][0]["message"]["content"])
    score = float(result["score"])
    if not 0 <= score <= 5:
        raise RuntimeError("Teacher returned an invalid score.")
    return {"score": score, "dimensions": result.get("dimensions") or {}, "reason": str(result.get("reason") or ""), "suggestedAttribution": str(result.get("suggestedAttribution") or "")}


def require_evaluation_target(target: dict[str, Any] | None, variant: str = "selected") -> None:
    if target is None or target.get("state") != "completed":
        raise HTTPException(status_code=409, detail="被测对象必须是已完成训练任务。")
    if target.get("provider") != "youyun":
        raise HTTPException(status_code=409, detail="评测仅支持优云训练任务，历史本地评测只能查看结果。")
    adapter = resolve_adapter_artifact(target.get("artifacts") or [], variant)
    remote_ready = bool(adapter and adapter.get("remotePath") and adapter.get("syncState") in {"remote_ready", "syncing", "available"})
    if not adapter or not (remote_ready or (adapter.get("path") and adapter_is_complete(Path(adapter["path"])))):
        raise HTTPException(status_code=409, detail="被测任务没有可用 Adapter。")
    marker = resolve_youyun_instance_artifact(target.get("artifacts") or [])
    if marker is None or marker.get("state") != "awaiting_comparison":
        raise HTTPException(status_code=409, detail="优云实例已释放或不可复用，请重新运行训练任务后评测。")


async def _run_evaluation_task(evaluation_id: str) -> None:
    task = evaluation_store.get_task(evaluation_id)
    if not task:
        return
    target = store.get(task["targetTaskId"])
    if target is None:
        evaluation_store.update_task(evaluation_id, state="failed", error="Target training task does not exist.")
        return
    try:
        require_evaluation_target(target, task.get("targetVariant") or "selected")
    except HTTPException as exc:
        evaluation_store.update_task(evaluation_id, state="failed", error=str(exc.detail))
        return
    evaluation_store.update_task(evaluation_id, state="generating", error=None)
    generated = 0
    parameters = task.get("generationParameters") or {"maxNewTokens": 256, "temperature": 0.2, "topP": 0.9, "seed": 42}
    for item in evaluation_store.list_items(evaluation_id, limit=100000):
        current = evaluation_store.get_task(evaluation_id)
        if not current or current["state"] == "cancelled":
            return
        messages: list[dict[str, str]] = []
        if item.get("systemPrompt"):
            messages.append({"role": "system", "content": item["systemPrompt"]})
        messages.append({"role": "user", "content": item["prompt"]})
        try:
            result = await chat(ChatRequest.model_validate({"mode": "adapter", "taskId": target["id"], "adapterVariant": task.get("targetVariant") or "selected", "model": target["model"], "modelSource": target.get("modelSource", "modelscope"), "messages": messages, "parameters": parameters}))
            answer = result["text"]
            changes: dict[str, Any] = {"modelAnswer": answer, "status": "generated"}
            if task["judgeConfig"].get("useSimilarity", True):
                changes["similarityResult"] = await asyncio.to_thread(_evaluation_similarity, answer, item.get("referenceAnswer") or "")
            if task["judgeConfig"].get("useTeacher", True):
                try:
                    changes["teacherResult"] = await asyncio.to_thread(_evaluation_teacher, task["judgeConfig"], item["prompt"], item.get("referenceAnswer") or "", answer, task.get("dimensions") or [])
                except Exception as exc:
                    changes["teacherResult"] = {"status": "failed", "error": str(exc)}
            evaluation_store.update_item(item["id"], **changes)
            generated += 1
            evaluation_store.update_task(evaluation_id, generated_items=generated)
        except Exception as exc:
            evaluation_store.update_item(item["id"], status="failed", error=str(exc))
    evaluation_store.update_task(evaluation_id, state="waiting_human_review", generated_items=generated)


@app.get("/api/evaluations/targets")
async def evaluation_targets() -> list[dict[str, Any]]:
    def eligible_targets() -> list[dict[str, Any]]:
        targets: list[dict[str, Any]] = []
        for target in store.list(include_details=True):
            try:
                require_evaluation_target(target)
            except HTTPException:
                continue
            targets.append(target)
        return targets

    return await asyncio.to_thread(eligible_targets)


@app.get("/api/evaluations")
async def evaluation_tasks() -> list[dict[str, Any]]:
    return evaluation_store.list_tasks()


@app.post("/api/evaluations", status_code=status.HTTP_201_CREATED)
async def create_evaluation_task(payload: EvaluationCreateRequest) -> dict[str, Any]:
    require_evaluation_enabled()
    dataset = store.get_dataset(payload.datasetId)
    if dataset is None or dataset.get("purpose") != "evaluation":
        raise HTTPException(status_code=422, detail="请选择已发布的评测集。")
    if dataset.get("state") != "published":
        raise HTTPException(status_code=409, detail="评测集必须发布后才能评测。")
    target = store.get(payload.targetTaskId)
    require_evaluation_target(target, payload.targetVariant)
    records = await asyncio.to_thread(evaluation_records_from_dataset, dataset)
    task = evaluation_store.create_task({"name": payload.name, "datasetId": dataset["id"], "datasetVersion": payload.datasetVersion or int(dataset.get("version") or 1), "targetTaskId": target["id"], "targetVariant": payload.targetVariant, "dimensions": payload.dimensions, "generationParameters": (payload.generationParameters or ChatParameters()).model_dump(), "judgeConfig": {"baseUrl": payload.judgeBaseUrl or "", "model": payload.judgeModel or "", "useTeacher": payload.useTeacher, "useSimilarity": payload.useSimilarity}}, records)
    record_audit("create", "evaluation", task["id"], details={"datasetId": dataset["id"], "targetTaskId": target["id"]})
    return task


@app.get("/api/evaluations/{evaluation_id}")
async def evaluation_task(evaluation_id: str) -> dict[str, Any]:
    task = evaluation_store.get_task(evaluation_id)
    if task is None:
        raise HTTPException(status_code=404, detail="评测任务不存在。")
    return task


@app.post("/api/evaluations/{evaluation_id}/start")
async def start_evaluation_task(evaluation_id: str) -> dict[str, Any]:
    require_evaluation_enabled()
    task = evaluation_store.get_task(evaluation_id)
    if task is None:
        raise HTTPException(status_code=404, detail="评测任务不存在。")
    require_evaluation_target(store.get(task["targetTaskId"]), task.get("targetVariant") or "selected")
    if task["state"] in {"queued", "generating", "scoring"}:
        raise HTTPException(status_code=409, detail="评测任务正在运行。")
    queued = evaluation_store.update_task(evaluation_id, state="queued") or task
    asyncio.create_task(_run_evaluation_task(evaluation_id), name="evaluation-" + evaluation_id)
    return queued


@app.post("/api/evaluations/{evaluation_id}/retry")
async def retry_evaluation_task(evaluation_id: str) -> dict[str, Any]:
    require_evaluation_enabled()
    task = evaluation_store.get_task(evaluation_id)
    if task is None:
        raise HTTPException(status_code=404, detail="评测任务不存在。")
    require_evaluation_target(store.get(task["targetTaskId"]), task.get("targetVariant") or "selected")
    if task["state"] in {"queued", "generating", "scoring"}:
        raise HTTPException(status_code=409, detail="评测任务正在运行。")
    queued = evaluation_store.update_task(evaluation_id, state="queued", error=None) or task
    asyncio.create_task(_run_evaluation_task(evaluation_id), name="evaluation-retry-" + evaluation_id)
    return queued


@app.post("/api/evaluations/{evaluation_id}/cancel")
async def cancel_evaluation_task(evaluation_id: str) -> dict[str, Any]:
    task = evaluation_store.get_task(evaluation_id)
    if task is None:
        raise HTTPException(status_code=404, detail="评测任务不存在。")
    return evaluation_store.update_task(evaluation_id, state="cancelled") or task


@app.get("/api/evaluations/{evaluation_id}/items")
async def evaluation_items(evaluation_id: str, status: str | None = None, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)) -> list[dict[str, Any]]:
    if evaluation_store.get_task(evaluation_id) is None:
        raise HTTPException(status_code=404, detail="评测任务不存在。")
    return evaluation_store.list_items(evaluation_id, status=status, limit=limit, offset=offset)


@app.patch("/api/evaluations/{evaluation_id}/items/{item_id}/review")
async def review_evaluation_item(evaluation_id: str, item_id: str, payload: EvaluationReviewRequest) -> dict[str, Any]:
    require_evaluation_enabled()
    item = evaluation_store.update_item(item_id, humanResult={"score": payload.score, "dimensions": payload.dimensions}, attribution=payload.attribution, reviewNote=payload.note, status="reviewed", reviewedBy=authenticated_user_or_default().get("name", "平台管理员"), reviewedAt=datetime.now(timezone.utc).isoformat())
    if item is None or item["evaluationId"] != evaluation_id:
        raise HTTPException(status_code=404, detail="评测记录不存在。")
    return item


@app.get("/api/evaluations/{evaluation_id}/summary")
async def evaluation_task_summary(evaluation_id: str) -> dict[str, Any]:
    if evaluation_store.get_task(evaluation_id) is None:
        raise HTTPException(status_code=404, detail="评测任务不存在。")
    return evaluation_store.summary(evaluation_id)
