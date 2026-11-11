from __future__ import annotations

from typing import Any

from backend.platform_contracts import normalize_task_summary


def normalize_platform_task(item: dict[str, Any], *, modality: str | None = None) -> dict[str, Any]:
    summary = normalize_task_summary({**item, **({"modality": modality} if modality else {})})
    updated_at = item.get("updatedAt") or item.get("updated_at") or ""
    return {
        **summary,
        "taskType": item.get("taskType") or item.get("task_type") or "SFT",
        "tuningMode": item.get("tuningMode") or item.get("tuning_mode") or "lora",
        "baseModel": item.get("model") or item.get("modelRepoId") or item.get("modelId") or "",
        "dataset": item.get("dataset") or item.get("datasetName") or item.get("datasetId") or "",
        "method": item.get("method") or "",
        "provider": item.get("provider") or "local",
        "executionMode": item.get("executionMode") or ("remote" if item.get("provider") == "youyun" else "local"),
        "createdAt": item.get("createdAt") or item.get("created_at") or "",
        "updatedAt": updated_at,
        "phaseUpdatedAt": item.get("phaseUpdatedAt") or item.get("phase_updated_at"),
        "lastHeartbeatAt": item.get("lastHeartbeatAt") or item.get("last_heartbeat_at"),
        "failureMessage": item.get("failureMessage") or item.get("error"),
    }


def build_task_listing(
    *,
    nlp_tasks: list[dict[str, Any]],
    vision_tasks: list[dict[str, Any]],
    image_generation_tasks: list[dict[str, Any]],
    modality: str = "all",
    state: str | None = None,
    query: str | None = None,
    page: int = 1,
    page_size: int = 20,
    updated_at: str,
) -> dict[str, Any]:
    grouped = [
        *(normalize_platform_task(item, modality="nlp") for item in nlp_tasks),
        *(normalize_platform_task(item, modality="vision") for item in vision_tasks),
        *(normalize_platform_task(item, modality="image-generation") for item in image_generation_tasks),
    ]
    if modality != "all":
        grouped = [item for item in grouped if item["modality"] == modality]
    if state and state != "all":
        grouped = [item for item in grouped if item["state"] == state]
    needle = (query or "").strip().casefold()
    if needle:
        grouped = [
            item for item in grouped
            if needle in " ".join(str(item.get(key) or "") for key in ("id", "name", "baseModel", "dataset")).casefold()
        ]
    grouped.sort(key=lambda item: (str(item.get("createdAt") or ""), str(item.get("id") or "")), reverse=True)
    for item in grouped:
        if not item.get("updatedAt"):
            item["updatedAt"] = updated_at
    safe_page = max(1, int(page))
    safe_page_size = min(100, max(1, int(page_size)))
    total = len(grouped)
    start = (safe_page - 1) * safe_page_size
    return {
        "items": grouped[start : start + safe_page_size],
        "page": safe_page,
        "pageSize": safe_page_size,
        "total": total,
        "hasNext": start + safe_page_size < total,
        "updatedAt": updated_at,
    }
