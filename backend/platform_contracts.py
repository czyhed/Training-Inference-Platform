from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_SAFE_DETAIL_KEYS = {
    "stage",
    "phase",
    "retryable",
    "providerCode",
    "provider_code",
    "action",
    "attempt",
}


def _safe_detail(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _safe_detail(item)
            for key, item in value.items()
            if str(key) in _SAFE_DETAIL_KEYS
        }
    if isinstance(value, list):
        return [_safe_detail(item) for item in value[:20]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def serialize_platform_error(
    code: str,
    message: str,
    details: Mapping[str, Any] | list[Any] | None = None,
    *,
    retryable: bool = False,
) -> dict[str, Any]:
    """Build a safe public error payload without leaking commands or secrets."""
    safe_details = _safe_detail(details or {})
    if isinstance(safe_details, Mapping):
        safe_details = [dict(safe_details)]
    return {
        "code": str(code),
        "message": str(message),
        "details": safe_details,
        "retryable": bool(retryable),
    }


def _modality(payload: Mapping[str, Any]) -> str:
    value = payload.get("modality") or payload.get("domain")
    if value in {"vision", "visual", "视觉理解", "IMAGE_TEXT"}:
        return "vision"
    if value in {"image-generation", "image_generation", "文生图", "IMAGE_CAPTION"}:
        return "image-generation"
    task_type = str(payload.get("task_type") or payload.get("taskType") or "").upper()
    if "IMAGE" in task_type or "VISION" in task_type:
        return "vision" if "VISION" in task_type else "image-generation"
    identifier = str(payload.get("id") or payload.get("task_id") or "")
    if identifier.startswith("vt-"):
        return "vision"
    if identifier.startswith("igt-"):
        return "image-generation"
    return "nlp"


def normalize_task_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize legacy task payloads into the shared frontend read model."""
    progress = payload.get("progress")
    if progress is not None:
        try:
            progress = float(progress)
        except (TypeError, ValueError):
            progress = None
    return {
        "id": str(payload.get("id") or payload.get("task_id") or ""),
        "modality": _modality(payload),
        "name": str(payload.get("name") or payload.get("task_name") or ""),
        "state": str(payload.get("state") or payload.get("status") or ""),
        "phase": str(payload.get("phase") or payload.get("current_phase") or ""),
        "progress": progress,
        "resourceState": str(payload.get("resourceState") or payload.get("resource_state") or "none"),
        "cleanupState": str(payload.get("cleanupState") or payload.get("cleanup_state") or "not_required"),
        "failureCode": payload.get("failureCode") or payload.get("failure_code"),
        "updatedAt": str(payload.get("updatedAt") or payload.get("updated_at") or ""),
    }
