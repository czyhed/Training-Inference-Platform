from __future__ import annotations

from typing import Any


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value is not None and value != "":
            return value
    return None


def service_modality(item: dict[str, Any]) -> str:
    config = _record(item.get("config"))
    workload = str(_first(item, "workloadType", "workload_type") or _first(config, "workloadType", "workload_type") or "text_generation").strip().lower()
    if workload in {"vision", "visual", "vision_language", "image_text"}:
        return "vision"
    if workload in {"image_generation", "text_to_image", "image"}:
        return "image-generation"
    return "nlp"


def normalize_platform_service(item: dict[str, Any], *, updated_at: str) -> dict[str, Any]:
    config = _record(item.get("config"))
    resource = _record(_first(item, "resource", "youyunResource") or _first(config, "resource", "youyunResource"))
    modality = service_modality(item)
    state = str(_first(item, "state", "status") or "")
    failure_message = _first(item, "failureMessage", "failure_message", "error", "cleanupError")
    return {
        "id": str(_first(item, "id", "serviceId") or ""),
        "name": str(_first(item, "name", "serviceName") or ""),
        "modality": modality,
        "state": state,
        "exposure": _first(item, "exposure", "exposureState") or "offline",
        "baseModel": _first(item, "baseModel", "modelRepoId", "model") or _first(config, "baseModel", "modelRepoId", "modelId") or "",
        "adapter": _first(item, "adapter", "adapterId") or _first(config, "adapter", "adapterId", "adapterVariant") or _record(config.get("adapterLineage")).get("variant") or "",
        "provider": _first(item, "provider", "executionMode") or _first(config, "provider") or "local",
        "executionMode": _first(item, "executionMode") or ("remote" if _first(item, "provider") == "youyun" or _first(config, "provider") == "youyun" else "local"),
        "gpu": _first(item, "gpu") or resource.get("gpuType") or "",
        "memory": _first(item, "memory") or "",
        "endpoint": _first(item, "endpoint") or "",
        "resourceState": _first(item, "resourceState", "resource_state") or _first(config, "resourceState", "resource_state") or "none",
        "cleanupState": _first(item, "cleanupState", "cleanup_state") or _first(config, "cleanupState", "cleanup_state") or "not_required",
        "failureCode": _first(item, "failureCode", "failure_code"),
        "failureMessage": failure_message,
        "suggestedAction": _first(item, "suggestedAction", "suggested_action"),
        "lastHeartbeatAt": _first(item, "lastHeartbeatAt", "last_heartbeat_at"),
        "createdAt": _first(item, "createdAt", "created_at") or "",
        "updatedAt": _first(item, "updatedAt", "updated_at") or updated_at,
    }


def build_service_listing(
    items: list[dict[str, Any]],
    *,
    modality: str = "all",
    state: str | None = None,
    query: str | None = None,
    page: int = 1,
    page_size: int = 20,
    updated_at: str,
) -> dict[str, Any]:
    normalized = [normalize_platform_service(item, updated_at=updated_at) for item in items]
    if modality != "all":
        normalized = [item for item in normalized if item["modality"] == modality]
    if state and state != "all":
        normalized = [item for item in normalized if item["state"] == state]
    needle = (query or "").strip().casefold()
    if needle:
        normalized = [
            item for item in normalized
            if needle in " ".join(str(item.get(key) or "") for key in ("id", "name", "baseModel", "adapter", "modality")).casefold()
        ]
    normalized.sort(key=lambda item: (str(item.get("updatedAt") or ""), str(item.get("id") or "")), reverse=True)
    safe_page = max(1, int(page))
    safe_page_size = min(100, max(1, int(page_size)))
    total = len(normalized)
    start = (safe_page - 1) * safe_page_size
    return {
        "items": normalized[start : start + safe_page_size],
        "page": safe_page,
        "pageSize": safe_page_size,
        "total": total,
        "hasNext": start + safe_page_size < total,
        "updatedAt": updated_at,
    }
