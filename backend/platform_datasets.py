from __future__ import annotations

from typing import Any


def dataset_domain(item: dict[str, Any]) -> str:
    dataset_type = str(item.get("type") or "")
    structure = str(item.get("structure") or "")
    if dataset_type == "文生图":
        return "image-generation"
    if dataset_type == "视觉理解" or structure == "IMAGE_TEXT":
        return "vision"
    return "nlp"


def dataset_modality(item: dict[str, Any]) -> str:
    domain = dataset_domain(item)
    if domain == "image-generation":
        return "IMAGE_CAPTION"
    if domain == "vision":
        return "IMAGE_TEXT"
    return "TEXT"


def normalize_dataset_item(item: dict[str, Any], *, updated_at: str) -> dict[str, Any]:
    state = str(item.get("state") or "")
    return {
        **item,
        "domain": dataset_domain(item),
        "modality": dataset_modality(item),
        "validationStatus": "failed" if state == "failed" else "ready" if state == "ready" else "published",
        "version": item.get("version") or 1,
        "updatedAt": item.get("updatedAt") or updated_at,
    }


def build_dataset_listing(
    items: list[dict[str, Any]],
    *,
    domain: str = "all",
    state: str | None = None,
    query: str | None = None,
    page: int = 1,
    page_size: int = 20,
    updated_at: str,
) -> dict[str, Any]:
    normalized = [normalize_dataset_item(item, updated_at=updated_at) for item in items]
    if domain != "all":
        normalized = [item for item in normalized if item["domain"] == domain]
    if state and state != "all":
        normalized = [item for item in normalized if item["state"] == state]
    needle = (query or "").strip().lower()
    if needle:
        normalized = [
            item
            for item in normalized
            if needle in " ".join(
                str(item.get(key) or "")
                for key in ("id", "name", "subtitle", "type", "structure")
            ).lower()
        ]
    normalized.sort(key=lambda item: (str(item.get("updatedAt") or ""), str(item.get("id") or "")), reverse=True)
    safe_page = max(1, int(page))
    safe_page_size = min(100, max(1, int(page_size)))
    total = len(normalized)
    start = (safe_page - 1) * safe_page_size
    page_items = normalized[start : start + safe_page_size]
    return {
        "items": page_items,
        "page": safe_page,
        "pageSize": safe_page_size,
        "total": total,
        "hasNext": start + safe_page_size < total,
        "updatedAt": updated_at,
    }
