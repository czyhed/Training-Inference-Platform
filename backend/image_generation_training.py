from __future__ import annotations

"""Deterministic SDXL LoRA training bundle materialization.

This module deliberately has no cloud, subprocess, or GPU dependencies.  It
only validates an IMAGE_TEXT dataset and emits a self-contained bundle that a
Diffusers executor can upload to a worker.
"""

import hashlib
import json
import random
import re
import shutil
from pathlib import Path
from typing import Any, Mapping

from backend.image_generation_validation import validate_image_upload


IMAGE_GENERATION_DATASET_FORMAT_VERSION = "diffusers-sdxl-lora-v1"
MAX_RESOLUTION = 2048
MAX_TOTAL_PIXELS = 2_097_152


def _safe_repo_id(repo_id: str) -> str:
    value = str(repo_id or "").strip()
    parts = value.split("/")
    if len(parts) != 2 or any(
        not part or part in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9._-]+", part)
        for part in parts
    ):
        raise ValueError("SDXL 模型 Repo ID 不安全或格式无效。")
    if "sdxl" not in value.casefold() and "stable-diffusion-xl" not in value.casefold():
        raise ValueError("当前文生图微调仅支持 SDXL 基座模型。")
    return value


def resolve_sdxl_lora_entrypoint(repo_id: str, model_path: str = "") -> str:
    """Return the official Diffusers SDXL LoRA script name for a verified model."""
    value = _safe_repo_id(repo_id)
    if model_path:
        path = Path(model_path).expanduser()
        if path.exists() and not path.is_dir():
            raise ValueError("SDXL 基座模型路径不是目录。")
    return "train_text_to_image_lora_sdxl.py"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError("文生图训练清单无法读取。") from exc
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"文生图训练清单第 {line_number} 行不是有效 JSON。") from exc
        if not isinstance(value, dict):
            raise ValueError(f"文生图训练清单第 {line_number} 行必须是对象。")
        records.append(value)
    if not records:
        raise ValueError("文生图训练清单没有有效样本。")
    return records


def _resolve_image_path(media_root: Path, file_name: Any, line_number: int) -> tuple[str, Path]:
    if not isinstance(file_name, str) or not file_name.strip():
        raise ValueError(f"文生图训练清单第 {line_number} 行缺少 file_name。")
    relative = Path(file_name.strip().replace("\\", "/"))
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError(f"文生图训练清单第 {line_number} 行图片路径不安全。")
    if relative.parts[:1] == ("images",):
        source = (media_root.parent / relative).resolve()
        normalized = relative.as_posix()
    else:
        source = (media_root / relative).resolve()
        normalized = f"images/{relative.as_posix()}"
    root = media_root.resolve()
    if not source.is_file() or not source.is_relative_to(root):
        raise ValueError(f"文生图训练清单第 {line_number} 行引用的图片不存在。")
    try:
        inspection = validate_image_upload(source.name, source.read_bytes())
    except ValueError as exc:
        raise ValueError(f"文生图训练清单第 {line_number} 行图片校验失败：{exc}") from exc
    return normalized, source


def _normalize_records(manifest_path: Path, media_root: Path) -> tuple[list[dict[str, str]], dict[str, Path], dict[str, str]]:
    records = _read_jsonl(manifest_path)
    samples: list[dict[str, str]] = []
    sources: dict[str, Path] = {}
    hashes: dict[str, str] = {}
    seen: set[str] = set()
    for line_number, record in enumerate(records, 1):
        image_name, image_path = _resolve_image_path(media_root, record.get("file_name") or record.get("image"), line_number)
        key = image_name.casefold()
        if key in seen:
            raise ValueError(f"文生图训练清单图片重复：{image_name}")
        seen.add(key)
        caption = record.get("caption") or record.get("text")
        if not isinstance(caption, str) or not caption.strip():
            raise ValueError(f"文生图训练清单第 {line_number} 行 Caption 不能为空。")
        content = image_path.read_bytes()
        samples.append({"file_name": image_name, "text": caption.strip()})
        sources[image_name] = image_path
        hashes[image_name] = hashlib.sha256(content).hexdigest()
    return samples, sources, hashes


def validate_image_generation_training_dataset(manifest_path: Path, media_root: Path | None = None) -> int:
    manifest = manifest_path.expanduser().resolve()
    if not manifest.is_file():
        raise ValueError("文生图训练清单不存在。")
    root = (media_root or manifest.parent / "images").expanduser().resolve()
    if not root.is_dir():
        raise ValueError("文生图数据集 images 目录不存在。")
    samples, _, _ = _normalize_records(manifest, root)
    return len(samples)


def _validate_parameters(*, resolution: int, epochs: float, max_train_steps: int, learning_rate: float, lora_rank: int, batch_size: int, gradient_accumulation: int, validation_split: int) -> None:
    if not 256 <= resolution <= MAX_RESOLUTION or resolution % 8:
        raise ValueError("SDXL 分辨率必须是 256-2048 之间的 8 的倍数。")
    if resolution * resolution > MAX_TOTAL_PIXELS:
        raise ValueError("图片总像素不能超过 2,097,152。")
    if epochs <= 0 or max_train_steps < 1 or learning_rate <= 0:
        raise ValueError("训练轮数、最大步数和学习率必须大于 0。")
    if not 1 <= lora_rank <= 256:
        raise ValueError("LoRA Rank 必须在 1-256 之间。")
    if not 1 <= batch_size <= 16 or not 1 <= gradient_accumulation <= 128:
        raise ValueError("Batch Size 和梯度累积必须在有效范围内。")
    if not 0 <= validation_split <= 50:
        raise ValueError("验证比例必须在 0-50% 之间。")


def _split(samples: list[dict[str, str]], validation_split: int, seed: int) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    indices = list(range(len(samples)))
    random.Random(seed).shuffle(indices)
    if validation_split <= 0 or len(samples) < 2:
        return [samples[index] for index in sorted(indices)], []
    count = min(len(samples) - 1, max(1, round(len(samples) * validation_split / 100)))
    validation_indices = sorted(indices[:count])
    train_indices = sorted(indices[count:])
    return [samples[index] for index in train_indices], [samples[index] for index in validation_indices]


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, records: list[Mapping[str, Any]]) -> None:
    path.write_text("".join(json.dumps(dict(item), ensure_ascii=False) + "\n" for item in records), encoding="utf-8")


def materialize_image_generation_training_bundle(*, dataset_id: str, media_root: Path, manifest_path: Path, destination: Path, model_repo_id: str, resolution: int, epochs: float, max_train_steps: int, learning_rate: float, lora_rank: int, batch_size: int, gradient_accumulation: int, validation_split: int, seed: int) -> dict[str, Any]:
    repo_id = _safe_repo_id(model_repo_id)
    _validate_parameters(
        resolution=resolution, epochs=epochs, max_train_steps=max_train_steps,
        learning_rate=learning_rate, lora_rank=lora_rank, batch_size=batch_size,
        gradient_accumulation=gradient_accumulation, validation_split=validation_split,
    )
    manifest = manifest_path.expanduser().resolve()
    root = media_root.expanduser().resolve()
    samples, sources, hashes = _normalize_records(manifest, root)
    train_records, validation_records = _split(samples, validation_split, seed)
    target = destination.expanduser().resolve()
    if target.exists():
        raise ValueError("文生图训练目录已存在，请使用新的任务目录。")
    dataset_dir = target / "dataset"
    output_dir = target / "output"
    dataset_images = dataset_dir / "images"
    dataset_images.mkdir(parents=True)
    output_dir.mkdir()
    for relative, source in sorted(sources.items()):
        target_image = dataset_dir / relative
        target_image.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target_image)
    _write_jsonl(target / "train.jsonl", train_records)
    _write_jsonl(dataset_dir / "metadata.jsonl", train_records)
    if validation_records:
        _write_jsonl(target / "validation.jsonl", validation_records)
    dataset_info = {
        "format": "imagefolder",
        "train_file": "train.jsonl",
        "validation_file": "validation.jsonl" if validation_records else None,
        # imagefolder decodes the metadata.jsonl file_name into its ``image``
        # feature. Keep file_name only as the source manifest locator.
        "image_column": "image",
        "caption_column": "text",
    }
    _write_json(target / "dataset_info.json", dataset_info)
    _write_json(dataset_dir / "dataset_info.json", dataset_info)
    config = {
        "entrypoint": resolve_sdxl_lora_entrypoint(repo_id),
        "model_repo_id": repo_id,
        "dataset_dir": str(dataset_dir),
        "train_file": str(target / "train.jsonl"),
        "validation_file": str(target / "validation.jsonl") if validation_records else None,
        "output_dir": str(output_dir),
        "resolution": resolution,
        "num_train_epochs": epochs,
        "max_train_steps": max_train_steps,
        "learning_rate": learning_rate,
        "lora_rank": lora_rank,
        "train_batch_size": batch_size,
        "gradient_accumulation_steps": gradient_accumulation,
        "validation_split": validation_split,
        "seed": seed,
    }
    _write_json(target / "train_config.json", config)
    split_manifest = {
        "train": [item["file_name"] for item in train_records],
        "validation": [item["file_name"] for item in validation_records],
    }
    bundle_manifest = {
        "formatVersion": IMAGE_GENERATION_DATASET_FORMAT_VERSION,
        "datasetId": dataset_id,
        "modelRepoId": repo_id,
        "sourceManifest": str(manifest),
        "sourceSha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "imageSha256": hashes,
        "split": split_manifest,
        "trainSamples": len(train_records),
        "validationSamples": len(validation_records),
        "config": config,
        "files": {"train": "train.jsonl", "validation": "validation.jsonl" if validation_records else None, "datasetInfo": "dataset_info.json", "config": "train_config.json"},
    }
    _write_json(target / "manifest.json", bundle_manifest)
    return {
        "root": str(target),
        "datasetDir": str(dataset_dir),
        "outputDir": str(output_dir),
        "manifestPath": str(target / "manifest.json"),
        "configPath": str(target / "train_config.json"),
        "manifest": bundle_manifest,
        "config": config,
    }


__all__ = [
    "IMAGE_GENERATION_DATASET_FORMAT_VERSION",
    "materialize_image_generation_training_bundle",
    "resolve_sdxl_lora_entrypoint",
    "validate_image_generation_training_dataset",
]
