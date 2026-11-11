from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

MAX_IMAGE_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_IMAGE_DATASET_FILES = 200
SUPPORTED_EXTENSIONS = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


@dataclass(frozen=True)
class ImageAssetInspection:
    file_name: str
    mime_type: str
    size_bytes: int
    width: int
    height: int
    sha256: str


def _validate_name(file_name: str) -> str:
    if not isinstance(file_name, str) or not file_name.strip():
        raise ValueError("图片文件名不能为空。")
    if Path(file_name).is_absolute() or Path(file_name).name != file_name or any(part in {"", ".", ".."} for part in Path(file_name).parts):
        raise ValueError("图片文件名不安全。")
    if any(char in file_name for char in ("/", "\\")):
        raise ValueError("图片文件名不安全。")
    suffix = Path(file_name).suffix.casefold()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError("只支持 JPG、JPEG、PNG 和 WebP 图片。")
    return file_name


def validate_image_upload(file_name: str, content: bytes) -> ImageAssetInspection:
    safe_name = _validate_name(file_name)
    if not content:
        raise ValueError("图片内容不能为空。")
    if len(content) > MAX_IMAGE_UPLOAD_BYTES:
        raise ValueError("单张图片不能超过 20MB。")
    suffix = Path(safe_name).suffix.casefold()
    try:
        with Image.open(BytesIO(content)) as image:
            detected = image.format
            width, height = image.size
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("图片内容无法解码。") from exc
    expected = {"JPEG": {".jpg", ".jpeg"}, "PNG": {".png"}, "WEBP": {".webp"}}
    if detected not in expected or suffix not in expected[detected]:
        raise ValueError("图片扩展名与文件签名不一致。")
    return ImageAssetInspection(safe_name, SUPPORTED_EXTENSIONS[suffix], len(content), width, height, hashlib.sha256(content).hexdigest())


def normalize_image_dataset(records: list[ImageAssetInspection], captions: dict[str, str]) -> tuple[list[dict[str, str]], dict[str, Any]]:
    if len(records) > MAX_IMAGE_DATASET_FILES:
        raise ValueError(f"单个文生图数据集最多包含 {MAX_IMAGE_DATASET_FILES} 张图片。")
    seen: set[str] = set()
    output: list[dict[str, str]] = []
    for record in sorted(records, key=lambda item: item.file_name.casefold()):
        key = record.file_name
        if key.casefold() in seen:
            raise ValueError(f"图片文件名重复：{key}")
        seen.add(key.casefold())
        if key in captions:
            raw_caption = captions[key]
            if not isinstance(raw_caption, str) or not raw_caption.strip():
                raise ValueError(f"图片 Caption 不能为空：{key}")
            caption = raw_caption.strip()
        else:
            caption = re.sub(r"[_-]+", " ", Path(key).stem).strip()
        if not caption:
            raise ValueError(f"图片 Caption 不能为空：{key}")
        output.append({"file_name": f"images/{key}", "caption": caption})
    return output, {"checkedRows": len(output), "imageCount": len(output), "errorCount": 0}
