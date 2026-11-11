"""Parse visual base models exposed by the Youyun shared model catalog."""
from __future__ import annotations

import json
import os
import posixpath
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping


_VISUAL_MARKERS = (
    "-vl",
    "_vl",
    "vision",
    "llava",
    "internvl",
    "qwen3-omni",
)
_NON_VISUAL_MARKERS = (
    "tts",
    "voice",
    "image",
    "diffusion",
    "stable-diffusion",
    "flux",
    "krea",
    "video",
    "chat",
    "text",
    "talk",
    "interrogator",
    "embedding",
    "reranker",
)


def _records(raw: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            parsed: list[Mapping[str, Any]] = []
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                value = json.loads(line)
                if isinstance(value, Mapping):
                    parsed.append(value)
            return parsed
    if isinstance(raw, Mapping):
        raw = raw.get("models") or raw.get("items") or raw.get("data") or []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def _normalize_path(value: Any) -> str:
    path = str(value or "").strip().replace("\\", "/")
    if not path:
        return ""
    normalized = posixpath.normpath(path)
    return normalized if normalized.startswith("/") else f"/{normalized}"


def _repo_from_path(path: str) -> str:
    parts = list(PurePosixPath(path).parts)
    lowered = [part.casefold() for part in parts]
    for marker in ("modelscope", "huggingface"):
        if marker in lowered:
            index = lowered.index(marker)
            tail = parts[index + 1 :]
            if len(tail) >= 2:
                return "/".join(tail[-2:])
            if tail:
                return tail[-1]
    return parts[-1] if parts else ""


def _repo_from_name(name: str) -> str:
    normalized = name.casefold().replace("_", "-").replace(" ", "-")
    aliases = {
        "qwen2-vl-2b": "Qwen/Qwen2-VL-2B-Instruct",
        "qwen2-vl-2b-instruct": "Qwen/Qwen2-VL-2B-Instruct",
        "qwen2.5-vl-3b": "Qwen/Qwen2.5-VL-3B-Instruct",
        "qwen2.5-vl-3b-instruct": "Qwen/Qwen2.5-VL-3B-Instruct",
        "qwen2.5-vl-7b": "Qwen/Qwen2.5-VL-7B-Instruct",
        "qwen2.5-vl-7b-instruct": "Qwen/Qwen2.5-VL-7B-Instruct",
        "qwen3-vl-2b": "Qwen/Qwen3-VL-2B-Instruct",
        "qwen3-vl-2b-instruct": "Qwen/Qwen3-VL-2B-Instruct",
        "qwen3-vl-4b": "Qwen/Qwen3-VL-4B-Instruct",
        "qwen3-vl-4b-instruct": "Qwen/Qwen3-VL-4B-Instruct",
        "qwen3-vl-8b": "Qwen/Qwen3-VL-8B-Instruct",
        "qwen3-vl-8b-instruct": "Qwen/Qwen3-VL-8B-Instruct",
    }
    return aliases.get(normalized, "")


def _template(repo_id: str) -> str | None:
    identity = repo_id.casefold()
    if "qwen3-vl" in identity or "qwen3-omni" in identity:
        return "qwen3_vl_nothink"
    if "qwen2.5-vl" in identity or "qwen2-vl" in identity:
        return "qwen2_vl"
    return None


def _is_visual(identity: str) -> bool:
    normalized = identity.casefold()
    if any(marker in normalized for marker in _NON_VISUAL_MARKERS):
        return "qwen3-omni" in normalized
    return any(marker in normalized for marker in _VISUAL_MARKERS)


def _recommended_vram(size_gb: Any) -> int:
    try:
        size = float(size_gb or 0)
    except (TypeError, ValueError):
        size = 0
    if size >= 60:
        return 80
    if size >= 30:
        return 48
    if size >= 12:
        return 24
    return 16


def parse_youyun_vision_catalog(raw: Any) -> list[dict[str, Any]]:
    """Return deduplicated visual-language models from a catalog response."""
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in _records(raw):
        path = _normalize_path(item.get("path") or item.get("Path") or item.get("modelPath") or item.get("mountPath") or item.get("remotePath"))
        repo_id = str(item.get("repoId") or item.get("RepoId") or item.get("repo_id") or "").strip().strip("/")
        if not repo_id:
            repo_id = _repo_from_path(path)
        if not repo_id:
            repo_id = _repo_from_name(str(item.get("name") or item.get("label") or ""))
        if not repo_id:
            continue
        name = str(item.get("name") or item.get("Name") or item.get("label") or repo_id.rsplit("/", 1)[-1]).strip()
        tags = item.get("tags") or item.get("Tag") or []
        if isinstance(tags, str):
            tags = [tags]
        tag_text = " ".join(str(tag) for tag in tags)
        identity = f"{name} {repo_id} {path} {tag_text}"
        template = _template(repo_id)
        if not _is_visual(identity) or template is None:
            continue
        key = repo_id.casefold()
        if key in seen:
            continue
        seen.add(key)
        size_gb = item.get("sizeGb") or item.get("size_gb") or item.get("size") or item.get("Size")
        capabilities = [
            "vision_language",
            "image_understanding",
            "image_qa",
            "lora_training",
            "qlora_training",
            "transformers_serving",
        ]
        normalized: dict[str, Any] = {
                "name": name,
                "repoId": repo_id,
                "revision": str(item.get("revision") or "main"),
                "path": path,
                "sizeGb": size_gb,
                "recommendedVramGb": _recommended_vram(size_gb),
                "capabilities": capabilities,
                "template": template,
                "availableOnYouyun": bool(path),
                "source": "youyun_model_catalog",
            }
        for field in ("imageId", "imageName", "imageStatus", "imageSource"):
            value = item.get(field)
            if value is not None and str(value).strip():
                normalized[field] = str(value).strip()
        result.append(normalized)
    return result


def load_youyun_vision_catalog() -> list[dict[str, Any]]:
    """Load an explicitly configured JSON/JSONL catalog without a paid API call.

    The repository copy is documentation/fixture data and must not silently
    become a production catalog or contaminate an empty-provider response.
    """
    configured = os.environ.get("YOUYUN_BASE_MODEL_CATALOG", "").strip()
    if not configured:
        return []
    if configured.startswith("[") or configured.startswith("{"):
        return parse_youyun_vision_catalog(configured)
    try:
        with open(configured, "r", encoding="utf-8") as handle:
            return parse_youyun_vision_catalog(handle.read())
    except (OSError, UnicodeDecodeError):
        return []


__all__ = ["load_youyun_vision_catalog", "parse_youyun_vision_catalog"]
