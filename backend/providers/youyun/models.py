"""优云 Provider 的配置与数据模型。"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping


_YOUYUN_REGION_LABELS = {
    "cn-bj2": "华北一区",
    "cn-wlcb": "华北二区",
    "cn-sh2": "上海二区",
}

YouyunInstanceMode = Literal["pod", "uhost"]

# These zones are documented by 优云 as supporting both container and VM
# instances. The provider's support-zone response exposes a single IsPod bit
# for a zone, so keep the product capability override at this boundary instead
# of making every caller infer the same exception independently.
YOUYUN_DUAL_MODE_ZONES = frozenset({"cn-wlcb-01", "cn-sh2-02"})


def youyun_zone_modes(zone: Mapping[str, Any]) -> tuple[YouyunInstanceMode, ...]:
    zone_id = str(zone.get("zone") or zone.get("Zone") or "").strip()
    if zone_id in YOUYUN_DUAL_MODE_ZONES:
        return ("pod", "uhost")
    return ("pod",) if bool(zone.get("is_pod", zone.get("IsPod"))) else ("uhost",)


def youyun_zone_supports_mode(zone: Mapping[str, Any], mode: str | None) -> bool:
    return mode in {None, *youyun_zone_modes(zone)}


def youyun_zone_default_mode(
    zone: Mapping[str, Any],
    preferred: str | None = None,
) -> YouyunInstanceMode:
    modes = youyun_zone_modes(zone)
    if preferred in modes:
        return preferred  # type: ignore[return-value]
    return modes[0]


def youyun_zone_capabilities(zone: Mapping[str, Any]) -> dict[str, Any]:
    """Return stable UI capabilities for one provider support zone."""
    zone_id = str(zone.get("zone") or zone.get("Zone") or "").strip()
    dual = zone_id in YOUYUN_DUAL_MODE_ZONES
    # cn-bj2-03 is the documented 华北一C zone: container-only, no fixed
    # public IP/data disk, and instance-level port configuration.
    if zone_id == "cn-bj2-03":
        return {
            "fixedPublicIp": False,
            "cloudStorage": True,
            "zonePortConfig": True,
        }
    if dual:
        return {
            "fixedPublicIp": True,
            "cloudStorage": True,
            "zonePortConfig": False,
        }
    return {
        "fixedPublicIp": None,
        "cloudStorage": None,
        "zonePortConfig": None,
    }


def build_region_capabilities(zones: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate zone-level Pod/UHost data into region-level capabilities.

    ``DescribeCompShareSupportZone`` reports the instance form for a *zone*,
    not for the whole region. A region is dual-mode when it has at least one
    Pod zone and one UHost zone. Keep the raw zone lists so callers can choose
    a deterministic zone while still exposing the complete provider response.
    """
    grouped: dict[str, dict[str, Any]] = {}
    for item in zones:
        region = str(item.get("region") or "").strip()
        zone = str(item.get("zone") or "").strip()
        if not region or not zone:
            continue
        capability = grouped.setdefault(
            region,
            {
                "region": region,
                "displayName": _YOUYUN_REGION_LABELS.get(region, region),
                "modes": {"pod": None, "uhost": None},
                "zones": {"pod": [], "uhost": []},
            },
        )
        for mode in youyun_zone_modes(item):
            if capability["modes"][mode] is None:
                capability["modes"][mode] = zone
            capability["zones"][mode].append(zone)

    return list(grouped.values())


def resolve_region_zone(
    zones: Iterable[dict[str, Any]],
    *,
    region: str,
    mode: str,
    preferred_zone: str | None = None,
) -> str | None:
    """Return the zone in ``region`` that matches the requested instance form."""
    candidates = [
        str(item.get("zone") or "").strip()
        for item in zones
        if str(item.get("region") or "").strip() == region
        and youyun_zone_supports_mode(item, mode)
        and str(item.get("zone") or "").strip()
    ]
    if preferred_zone and preferred_zone in candidates:
        return preferred_zone
    return candidates[0] if candidates else None


def _load_dotenv(path: Path) -> None:
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


@dataclass(frozen=True)
class YouyunConfig:
    api_base_url: str = "https://api.compshare.cn"
    public_key: str = ""
    private_key: str = ""
    region: str = ""
    zone: str = ""
    image_id: str = ""
    training_image_id: str = "compshareImage-1bvy5l0kjip2"
    gpu_type: str = "4090"
    charge_type: str = "Postpay"
    boot_disk_size_gb: int = 100
    timeout_seconds: float = 30.0
    max_retries: int = 3
    instance_mode: str = "uhost"
    pod_zone: str = ""
    public_model_roots: str = "/models,/model"

    @classmethod
    def from_env(cls, dotenv_path: Path | None = None) -> "YouyunConfig":
        _load_dotenv(dotenv_path or Path(__file__).resolve().parents[3] / ".env")
        from backend.personal_youyun import apply_account
        return apply_account(cls(
            api_base_url=os.environ.get("YOUYUN_API_BASE_URL", "https://api.compshare.cn").strip().rstrip("/"),
            public_key=os.environ.get("YOUYUN_PUBLIC_KEY", "").strip(),
            private_key=os.environ.get("YOUYUN_PRIVATE_KEY", "").strip(),
            region=os.environ.get("YOUYUN_REGION", "").strip(),
            zone=os.environ.get("YOUYUN_ZONE", "").strip(),
            image_id=os.environ.get("YOUYUN_IMAGE_ID", "").strip(),
            training_image_id=os.environ.get(
                "YOUYUN_TRAINING_IMAGE_ID", "compshareImage-1bvy5l0kjip2"
            ).strip(),
            gpu_type=os.environ.get("YOUYUN_GPU_TYPE", "4090").strip(),
            charge_type=os.environ.get("YOUYUN_CHARGE_TYPE", "Postpay").strip(),
            boot_disk_size_gb=int(os.environ.get("YOUYUN_BOOT_DISK_SIZE_GB", "100")),
            timeout_seconds=float(os.environ.get("YOUYUN_TIMEOUT_SECONDS", "30")),
            max_retries=int(os.environ.get("YOUYUN_MAX_RETRIES", "2")),
            instance_mode=os.environ.get("YOUYUN_INSTANCE_MODE", "uhost").strip().lower() or "uhost",
            pod_zone=os.environ.get("YOUYUN_POD_ZONE", "").strip(),
            public_model_roots=os.environ.get("YOUYUN_PUBLIC_MODEL_ROOTS", "/models,/model").strip() or "/models,/model",
        ))

    def missing_keys(self) -> list[str]:
        required = {
            "YOUYUN_PUBLIC_KEY": self.public_key,
            "YOUYUN_PRIVATE_KEY": self.private_key,
            "YOUYUN_REGION": self.region,
        }
        missing = [key for key, value in required.items() if not value]
        if not self.zone and not (self.instance_mode == "pod" and self.pod_zone):
            missing.append("YOUYUN_ZONE")
        return missing

    def with_overrides(
        self,
        region: str | None = None,
        zone: str | None = None,
        charge_type: str | None = None,
    ) -> "YouyunConfig":
        """用资源选择覆盖区域/可用区；空值回落到基础配置。

        优云要求请求中 Region 与 Zone 属于同一区域，创建/库存检查/实例操作
        都必须使用服务创建时选定的那一组，否则会被 RetCode=230 拒绝。
        """
        overrides: dict[str, str] = {}
        if region and region.strip():
            overrides["region"] = region.strip()
        if zone and zone.strip():
            overrides["zone"] = zone.strip()
        if charge_type and charge_type.strip():
            overrides["charge_type"] = charge_type.strip()
        return replace(self, **overrides) if overrides else self


class YouyunApiError(RuntimeError):
    """优云 API 返回 RetCode != 0 或网络层失败。"""

    def __init__(self, action: str, ret_code: int | None, message: str) -> None:
        super().__init__(f"优云 API {action} 失败（RetCode={ret_code}）：{message}")
        self.action = action
        self.ret_code = ret_code
        self.message = message


@dataclass
class GpuSpecCombination:
    gpu: int
    cpu: int
    memory_gb: int
    resource_enough: bool


@dataclass
class GpuMachineType:
    name: str
    zone: str
    status: str
    description: str
    graphics_memory_gb: float | None
    machine_sizes: list[dict[str, Any]] = field(default_factory=list)

    @property
    def sold_out(self) -> bool:
        return self.status != "Normal"


@dataclass
class ResourceCapacity:
    gpu_type: str
    specs: list[GpuSpecCombination]

    def first_available(self) -> GpuSpecCombination | None:
        return next((s for s in self.specs if s.resource_enough), None)


def youyun_machine_inventory_metadata(
    machine: GpuMachineType,
    capacity: ResourceCapacity,
    selected_gpu_type: str,
    capacity_by_gpu: Mapping[str, ResourceCapacity] | None = None,
    capacity_errors: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Describe the inventory scope represented by one GPU catalog card.

    ``DescribeAvailableCompShareInstanceTypes`` only tells us whether a GPU
    family is present in the catalog. ``CheckCompShareResourceCapacity`` is
    the stronger, image/CPU/memory-specific signal. Callers may provide a
    capacity map to precompute the compact status shown on every GPU card;
    without that map, only the selected GPU is considered queried.
    """
    if machine.sold_out:
        return {
            "inventoryStatus": "sold_out",
            "inventoryMessage": "当前 Zone 暂无该 GPU 目录库存",
        }
    if capacity_errors and machine.name in capacity_errors:
        return {
            "inventoryStatus": "error",
            "inventoryMessage": "库存查询失败，请刷新",
            "inventoryError": str(capacity_errors[machine.name]),
        }
    machine_capacity = (
        capacity_by_gpu.get(machine.name)
        if capacity_by_gpu is not None
        else capacity if machine.name == selected_gpu_type else None
    )
    if machine_capacity is None:
        return {
            "inventoryStatus": "unknown",
            "inventoryMessage": "机型可用，切换后查询组合库存",
        }
    single_card_specs = [spec for spec in machine_capacity.specs if spec.gpu == 1]
    if any(spec.resource_enough for spec in single_card_specs):
        return {
            "inventoryStatus": "available",
            "inventoryMessage": "当前 GPU 规格有库存",
        }
    if single_card_specs:
        return {
            "inventoryStatus": "sold_out",
            "inventoryMessage": "当前 GPU 的单卡 CPU / 内存组合暂无库存",
        }
    return {
        "inventoryStatus": "unknown",
        "inventoryMessage": "库存待确认，请刷新后重试",
    }


@dataclass
class YouyunPrice:
    charge_type: str
    instance: float
    disks: float
    system_disks: float
    image: float
    currency: str = "CNY"

    @property
    def total(self) -> float:
        return self.instance + self.disks + self.system_disks + self.image


@dataclass
class YouyunInstance:
    uhost_id: str
    name: str
    state: str
    region: str
    zone: str
    gpu: int
    gpu_type: str
    cpu: int
    memory_mb: int
    ip: str | None
    ssh_port: int | None
    ssh_user: str | None
    password_b64: str | None
    image_id: str
    os_type: str
    charge_type: str
    instance_type: str
    raw: dict[str, Any]

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "YouyunInstance":
        command = str(data.get("SshLoginCommand") or "")
        ip: str | None = None
        port: int | None = None
        user: str | None = None
        # 兼容 "ssh root@x.x.x.x -p 60022" 与 "ssh -p 23 root@x.x.x.x" 两种顺序
        if command:
            parts = command.split()
            for index, token in enumerate(parts):
                if "@" in token and not token.startswith("-"):
                    maybe_user, _, host = token.partition("@")
                    if host:
                        user, ip = maybe_user, host
                elif index > 0 and parts[index - 1] == "-p" and token.isdigit():
                    port = int(token)
        return cls(
            uhost_id=str(data.get("UHostId") or ""),
            name=str(data.get("Name") or ""),
            state=str(data.get("State") or ""),
            region=str(data.get("Region") or ""),
            zone=str(data.get("Zone") or ""),
            gpu=int(data.get("GPU") or 0),
            gpu_type=str(data.get("GpuType") or ""),
            cpu=int(data.get("CPU") or 0),
            memory_mb=int(data.get("Memory") or 0),
            ip=ip,
            ssh_port=port,
            ssh_user=user or "root",
            password_b64=(data.get("Password") or None),
            image_id=str(data.get("CompShareImageId") or ""),
            os_type=str(data.get("OsType") or ""),
            charge_type=str(data.get("ChargeType") or ""),
            instance_type=str(data.get("InstanceType") or ""),
            raw=data,
        )

    @property
    def running(self) -> bool:
        return self.state == "Running"


_VLLM_IMAGE_VERSION = re.compile(r"vllm\s*v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)", re.IGNORECASE)

_TORCH_IMAGE_VERSION = re.compile(r"torch(?P<major>\d)(?P<minor>\d{1,2})(?P<patch>\d)(?:_|$)", re.IGNORECASE)

_COMMUNITY_WORKLOAD_RULES: dict[str, dict[str, tuple[str, ...]]] = {
    "vision_training": {
        "positive": ("llamafactory", "llama-factory", "llama factory", "qwen-vl", "qwen2-vl", "qwen2.5-vl", "qwen3-vl", "qwenvl", "multimodal", "多模态", "视觉", "vision"),
        "negative": ("video", "minimax", "comfyui", "diffusers", "stable diffusion", "flux", "vllm"),
    },
    "image_generation": {
        "positive": ("diffusers", "stable diffusion", "sdxl", "flux", "qwen-image", "image generation", "文生图"),
        "negative": ("video", "minimax", "llamafactory", "qwen-vl", "comfyui"),
    },
    "image_generation_training": {
        "positive": ("diffusers", "stable diffusion", "sdxl", "ai-toolkit", "aitoolkit", "lora训练", "文生图"),
        "negative": ("video", "minimax", "llamafactory", "qwen-vl", "comfyui"),
    },
    "vision_inference": {
        "positive": ("vllm", "qwen-vl", "qwen2-vl", "qwen2.5-vl", "multimodal", "视觉", "vision"),
        "negative": ("video", "minimax", "diffusers", "stable diffusion", "flux"),
    },
}

COMMUNITY_YOUYUN_IMAGES: tuple[dict[str, Any], ...] = (
    {
        "name": "LLaMAFactory-WebUI",
        "tags": ["LLaMA-Factory", "Qwen2-VL", "LoRA", "QLoRA"],
        "description": "社区多模态训练环境候选，面向 Qwen2-VL/Qwen2.5-VL；需优云库存返回同名可售镜像后启用。",
    },
    {
        "name": "LLaMA-Factory-YingHuoAI",
        "tags": ["LLaMA-Factory", "多模态训练"],
        "description": "社区多模态训练环境备选，未完成真实探针前保持禁用。",
    },
)

# ==== fusion: 社区镜像与视觉镜像选择 ====
def assess_community_image_match(
    image: dict[str, Any],
    *,
    workload: str,
    gpu_type: str = "",
    verification_state: str = "unverified",
) -> dict[str, Any]:
    """Return deterministic compatibility metadata without creating a cloud instance."""
    rules = _COMMUNITY_WORKLOAD_RULES.get(workload, _COMMUNITY_WORKLOAD_RULES["vision_training"])
    text = " ".join(
        [str(image.get(key) or "") for key in ("name", "version_name", "description", "image_id")]
        + [str(tag) for tag in image.get("tags") or []]
    ).casefold()
    status = str(image.get("status") or "unknown").casefold()
    available = status in {"available", "normal", "ready"}
    supported_gpus = {str(item).casefold() for item in image.get("supported_gpu_types") or [] if str(item).strip()}
    gpu_supported = not supported_gpus or not gpu_type or gpu_type.casefold() in supported_gpus
    positive_hits = [marker for marker in rules["positive"] if marker.casefold() in text]
    negative_hits = [marker for marker in rules["negative"] if marker.casefold() in text]
    if workload == "vision_training":
        training_hits = [marker for marker in ("llamafactory", "llama-factory", "llama factory", "微调", "qlora", "lora训练") if marker in text]
        vision_hits = [marker for marker in ("qwen-vl", "qwen2-vl", "qwen2.5-vl", "qwen3-vl", "qwenvl", "multimodal", "多模态", "视觉") if marker in text]
        purpose_match = bool(training_hits) and bool(vision_hits)
    elif workload in {"image_generation", "image_generation_training"}:
        model_hits = [marker for marker in ("sdxl", "stable diffusion") if marker in text]
        runtime_hits = [marker for marker in ("diffusers", "lora", "训练", "ai-toolkit", "aitoolkit") if marker in text]
        purpose_match = bool(model_hits) and bool(runtime_hits)
    elif workload == "vision_inference":
        runtime_hits = [marker for marker in ("vllm", "sglang") if marker in text]
        vision_hits = [marker for marker in ("qwen-vl", "qwen2-vl", "qwen2.5-vl", "qwen3-vl", "qwenvl", "multimodal", "多模态", "视觉") if marker in text]
        purpose_match = bool(runtime_hits) and bool(vision_hits)
    else:
        purpose_match = bool(positive_hits)
    compatible = available and gpu_supported and purpose_match and not negative_hits
    selectable = compatible and verification_state == "verified"
    if not available:
        reason = "当前区域无库存或镜像尚未上架。"
    elif not gpu_supported:
        reason = f"该镜像不支持当前 GPU {gpu_type}。"
    elif negative_hits or not positive_hits:
        reason = "镜像用途与当前任务不匹配，已自动隐藏。"
    elif verification_state != "verified":
        reason = "镜像与当前任务匹配，但需要真实实例探针验证后才能选择。"
    else:
        reason = "镜像用途、GPU 和库存均匹配，已通过验证。"
    return {
        "compatible": compatible,
        "selectable": selectable,
        "available": available,
        "gpuSupported": gpu_supported,
        "matchScore": len(positive_hits) * 10 - len(negative_hits) * 20 + (5 if gpu_supported else 0) + (5 if verification_state == "verified" else 0),
        "recommendationReason": reason,
        "recommendation_reason": reason,
    }


def rank_community_image_candidates(
    images: list[dict[str, Any]],
    *,
    workload: str,
    gpu_type: str = "",
    verification_by_id: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Annotate and rank community images for a concrete workload and GPU."""
    verification_by_id = verification_by_id or {}
    ranked: list[dict[str, Any]] = []
    for image in images:
        image_id = str(image.get("image_id") or "")
        state = str((verification_by_id.get(image_id) or {}).get("state") or "unverified")
        ranked.append({**image, **assess_community_image_match(image, workload=workload, gpu_type=gpu_type, verification_state=state)})
    ranked.sort(key=lambda item: (bool(item.get("selectable")), bool(item.get("compatible")), int(item.get("matchScore") or 0)), reverse=True)
    return ranked


def recommend_image_candidates(
    images: list[dict[str, Any]],
    *,
    workload: str,
    gpu_type: str = "",
    verification_by_id: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Rank platform and community images using workload and hard probe gates."""
    verification_by_id = verification_by_id or {}
    ranked: list[dict[str, Any]] = []
    for image in images:
        image_id = str(image.get("image_id") or image.get("id") or "")
        name = str(image.get("name") or "")
        text = " ".join([
            name,
            str(image.get("version_name") or ""),
            str(image.get("description") or ""),
            " ".join(str(tag) for tag in image.get("tags") or []),
        ]).casefold()
        status = str(image.get("status") or "").casefold()
        available = status in {"available", "normal", "ready"}
        supported = {str(value).casefold() for value in image.get("supported_gpu_types") or [] if str(value).strip()}
        gpu_fit = not supported or not gpu_type or gpu_type.casefold() in supported
        image_type = str(image.get("image_type") or image.get("type") or "").casefold()
        community = image_type == "community" or str(image.get("source") or "").casefold() == "community"
        probe_state = str((verification_by_id.get(image_id) or {}).get("state") or ("unverified" if community else "not_required"))
        if workload == "vision_training":
            capability = bool(re.search(r"llama.?factory|qwen.?vl|vision|视觉|多模态", text)) or ("torch" in text and not community)
            version = _TORCH_IMAGE_VERSION.search(name)
            version_ok = version is None or tuple(int(version.group(key)) for key in ("major", "minor", "patch")) < (2, 9, 0)
        elif workload == "image_generation_training":
            capability = bool(re.search(r"diffusers|sdxl|stable diffusion|flux|文生图", text)) or ("torch" in text and not community)
            version_ok = True
        elif workload == "vision_inference":
            capability = bool(re.search(r"vllm|qwen.?vl|vision|视觉|多模态", text))
            version_ok = True
        else:
            capability = bool(re.search(r"diffusers|sdxl|stable diffusion|flux|qwen.?image|vllm", text))
            version_ok = True
        selectable = available and gpu_fit and capability and version_ok and (not community or probe_state == "verified")
        score = (35 if capability else 0) + (20 if gpu_fit else -40) + (15 if available else -50)
        if not community:
            score += 20
        if probe_state == "verified":
            score += 30
        if not version_ok:
            score -= 30
        if not available:
            reason = "镜像当前不可用。"
        elif not gpu_fit:
            reason = f"该镜像不支持当前 GPU {gpu_type}。"
        elif not capability or not version_ok:
            reason = "镜像运行时或版本与当前任务不匹配。"
        elif community and probe_state != "verified":
            reason = "社区镜像需真实探针通过后才能选择。"
        else:
            reason = "镜像能力、GPU、状态和探针条件均满足。"
        ranked.append({
            **image,
            "image_id": image_id,
            "gpuFit": gpu_fit,
            "probeState": probe_state,
            "selectable": selectable,
            "compatible": available and gpu_fit and capability and version_ok,
            "score": score,
            "matchScore": score,
            "recommendationReason": reason,
        })
    ranked.sort(key=lambda item: (bool(item.get("selectable")), bool(item.get("compatible")), int(item.get("score") or 0)), reverse=True)
    return ranked


def merge_community_youyun_images(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expose community image candidates without inventing a billable image ID."""
    result: list[dict[str, Any]] = []
    lowered_names = {str(item.get("name") or "").casefold() for item in images}
    for item in COMMUNITY_YOUYUN_IMAGES:
        match = next(
            (candidate for candidate in images if str(candidate.get("name") or "").casefold() == item["name"].casefold()),
            None,
        )
        if match is not None:
            result.append({**item, **match, "source": "community_catalog", "available": True})
        elif item["name"].casefold() not in lowered_names:
            result.append({**item, "source": "community_catalog", "available": False, "status": "CatalogOnly"})
    return result


def normalize_community_image_rows(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize live community rows for UI/API payloads without inventing IDs."""
    normalized: list[dict[str, Any]] = []
    for item in images:
        image_id = str(item.get("image_id") or "")
        if not image_id:
            continue
        status = str(item.get("status") or "Unknown")
        normalized.append(
            {
                **item,
                "source": "community",
                "available": status.casefold() in {"available", "normal", "ready"},
            }
        )
    return normalized


def select_visual_inference_image(
    images: list[dict[str, Any]], configured_image_id: str = ""
) -> dict[str, Any] | None:
    """Choose the newest available application image that explicitly bundles vLLM."""
    compatible: list[tuple[tuple[int, int, int], dict[str, Any]]] = []
    configured: dict[str, Any] | None = None
    for image in images:
        if str(image.get("image_id") or "") == configured_image_id:
            configured = image
        if str(image.get("image_type") or "").casefold() != "app":
            continue
        if str(image.get("status") or "").casefold() not in {"available", "normal"}:
            continue
        name = str(image.get("name") or "")
        match = _VLLM_IMAGE_VERSION.search(name)
        if match is None and "vllm" not in name.casefold():
            continue
        version = (
            tuple(int(match.group(key)) for key in ("major", "minor", "patch"))
            if match is not None
            else (0, 0, 0)
        )
        compatible.append((version, image))
    if compatible:
        return max(compatible, key=lambda item: item[0])[1]
    return configured


def select_visual_training_image(
    images: list[dict[str, Any]], configured_image_id: str = ""
) -> dict[str, Any] | None:
    """Choose the newest available application image with Torch < 2.9."""
    compatible: list[tuple[tuple[int, int, int], dict[str, Any]]] = []
    configured: dict[str, Any] | None = None
    for image in images:
        if str(image.get("image_id") or "") == configured_image_id:
            configured = image
        if str(image.get("image_type") or "").casefold() != "app":
            continue
        if str(image.get("status") or "").casefold() not in {"available", "normal"}:
            continue
        match = _TORCH_IMAGE_VERSION.search(str(image.get("name") or ""))
        if match is None:
            continue
        version = tuple(int(match.group(key)) for key in ("major", "minor", "patch"))
        if version < (2, 9, 0):
            compatible.append((version, image))
    if compatible:
        return max(compatible, key=lambda item: item[0])[1]
    return configured
