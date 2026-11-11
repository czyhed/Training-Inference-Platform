from __future__ import annotations

"""Dataset catalog and safe validation for service-scoped image evaluation."""

import json
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, UnidentifiedImageError


def _dataset_rows(store: Any) -> list[dict[str, Any]]:
    listed = store.list_datasets() if hasattr(store, "list_datasets") else []
    return [item for item in listed if isinstance(item, Mapping)]


def _published_image_dataset(store: Any, dataset_id: str) -> dict[str, Any]:
    wanted = str(dataset_id or "").strip()
    for item in _dataset_rows(store):
        if str(item.get("id") or "") != wanted:
            continue
        if item.get("state") != "published" or item.get("structure") != "IMAGE_TEXT" or item.get("type") not in {"文生图", None}:
            raise ValueError("评测集必须是已发布的文生图 IMAGE_TEXT 数据集。")
        return dict(item)
    raise ValueError("文生图评测集不存在或尚未发布。")


def _read_manifest(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError("评测集 metadata 文件不存在。")
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError("评测集 metadata 文件无法读取。") from exc
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"评测集 metadata 第 {number} 行不是有效 JSON。") from exc
        if not isinstance(item, dict):
            raise ValueError(f"评测集 metadata 第 {number} 行必须是对象。")
        records.append(item)
    return records


def _safe_media_path(media_root: Path, file_name: str) -> Path:
    raw = str(file_name or "").strip()
    relative = raw.removeprefix("images/").removeprefix("images\\")
    candidate = (media_root / relative).expanduser().resolve()
    root = media_root.expanduser().resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("评测集图片路径超出媒体目录。")
    if Path(relative).name != relative or not relative or relative in {".", ".."}:
        raise ValueError("评测集图片文件名不安全。")
    return candidate


def _snapshot_record(record: Mapping[str, Any], media_root: Path, index: int) -> tuple[dict[str, Any], list[str]]:
    file_name = str(record.get("file_name") or record.get("fileName") or record.get("image") or "").strip()
    caption = str(record.get("caption") or record.get("prompt") or "").strip()
    errors: list[str] = []
    if not caption:
        errors.append("Caption 为空。")
    path: Path | None = None
    try:
        path = _safe_media_path(media_root, file_name)
    except ValueError as exc:
        errors.append(str(exc))
    if path is not None:
        if not path.is_file():
            errors.append("参考图片不存在。")
        else:
            try:
                with Image.open(path) as image:
                    image.verify()
                with Image.open(path) as image:
                    width, height = image.size
            except (OSError, UnidentifiedImageError, ValueError):
                errors.append("参考图片无法读取。")
                width = height = None
        if not errors and path is not None:
            return {
                "id": str(record.get("id") or f"sample-{index + 1}"),
                "fileName": file_name,
                "caption": caption,
                "referencePath": str(path),
                "width": width,
                "height": height,
            }, errors
    return {
        "id": str(record.get("id") or f"sample-{index + 1}"),
        "fileName": file_name,
        "caption": caption,
        "referencePath": str(path) if path else "",
    }, errors


def list_image_generation_evaluation_datasets(store: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in _dataset_rows(store):
        if item.get("state") != "published" or item.get("structure") != "IMAGE_TEXT" or item.get("type") not in {"文生图", None}:
            continue
        paths = item.get("paths") or {}
        manifest = Path(str(paths.get("manifestPath") or ""))
        media_root = Path(str(paths.get("mediaRoot") or ""))
        try:
            records = _read_manifest(manifest)
            valid = sum(not _snapshot_record(record, media_root, index)[1] for index, record in enumerate(records))
            errors = sum(bool(_snapshot_record(record, media_root, index)[1]) for index, record in enumerate(records))
        except ValueError as exc:
            records, valid, errors = [], 0, 1
            message = str(exc)
        else:
            message = "可直接用于文生图验证评测。" if errors == 0 else "评测集存在需要修复的样本。"
        result.append({
            "id": str(item.get("id") or ""),
            "name": str(item.get("name") or item.get("id") or "文生图评测集"),
            "datasetType": item.get("type") or "文生图",
            "structure": "IMAGE_TEXT",
            "sampleCount": len(records),
            "validSampleCount": valid,
            "invalidSampleCount": errors,
            "valid": errors == 0 and bool(records),
            "datasetPath": str(media_root.parent if media_root.name == "images" else media_root),
            "manifestPath": str(manifest),
            "message": message,
        })
    return sorted(result, key=lambda item: str(item.get("name") or "").casefold())


def validate_image_generation_evaluation_dataset(store: Any, dataset_id: str, max_samples: int | None = None) -> dict[str, Any]:
    dataset = _published_image_dataset(store, dataset_id)
    paths = dataset.get("paths") or {}
    manifest = Path(str(paths.get("manifestPath") or ""))
    media_root = Path(str(paths.get("mediaRoot") or ""))
    records = _read_manifest(manifest)
    selected = records[:max_samples] if max_samples else records
    samples: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    missing_captions = 0
    missing_images = 0
    for index, record in enumerate(selected):
        snapshot, errors = _snapshot_record(record, media_root, index)
        samples.append(snapshot)
        if errors:
            if any("Caption" in error for error in errors):
                missing_captions += 1
            if any("图片" in error for error in errors):
                missing_images += 1
            issues.append({"sampleId": snapshot["id"], "fileName": snapshot["fileName"], "errors": errors})
    return {
        "valid": not issues and bool(samples),
        "datasetId": str(dataset.get("id") or dataset_id),
        "datasetName": str(dataset.get("name") or dataset_id),
        "sampleCount": len(samples),
        "totalSampleCount": len(records),
        "missingCaptions": missing_captions,
        "missingImages": missing_images,
        "issues": issues,
        "samples": samples,
        "snapshot": {"datasetId": dataset.get("id"), "datasetName": dataset.get("name"), "manifestPath": str(manifest), "mediaRoot": str(media_root)},
    }


__all__ = ["list_image_generation_evaluation_datasets", "validate_image_generation_evaluation_dataset"]
