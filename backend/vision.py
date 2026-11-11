from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any, Mapping


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_VISION_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_VISION_DATASET_IMAGES = 200
VISION_REPO_HINTS = ("-vl", "_vl", "vision", "llava", "internvl", "qwen2-vl", "qwen3-omni")


def configured_youyun_model_repos(value: str | None = None) -> set[str]:
    """Return Repo IDs explicitly declared as mounted on the Youyun image."""
    raw = os.environ.get("YOUYUN_BASE_MODEL_REPOS", "") if value is None else value
    return {
        item.strip().strip("/").casefold()
        for item in re.split(r"[,;\n]+", raw)
        if item.strip().strip("/")
    }


def is_youyun_model_available(
    model: Mapping[str, Any], configured_repos: set[str] | None = None
) -> bool:
    """Check whether a registered model is declared on the Youyun image or mount."""
    if str(model.get("remotePath") or "").strip().startswith("/"):
        return True
    if bool(model.get("youyunCatalog")) or str(model.get("source") or "") == "youyun_model_catalog":
        # A catalog row is only authoritative when it carries the remote mount
        # path (or an explicit availability flag).  Older catalog snapshots
        # could contain a Repo ID without a usable path and caused the task
        # preflight to pass before remote model resolution failed.
        return bool(model.get("availableOnYouyun") or model.get("remotePath") or model.get("path"))
    repo_id = str(model.get("repoId") or "").strip().strip("/").casefold()
    capabilities = set(model.get("capabilities") or [])
    repos = configured_youyun_model_repos() if configured_repos is None else configured_repos
    return bool(repo_id and "vision_language" in capabilities and repo_id in repos)


def model_capabilities(repo_id: str, model_path: Path | None = None) -> list[str]:
    """Infer conservative capabilities from model identity and local config files."""
    normalized = repo_id.casefold()
    capabilities: set[str] = set()
    if any(hint in normalized for hint in VISION_REPO_HINTS):
        capabilities.update({"vision_language", "image_understanding", "image_qa"})

    if model_path and model_path.is_dir():
        for filename in ("config.json", "preprocessor_config.json", "processor_config.json"):
            candidate = model_path / filename
            if not candidate.is_file():
                continue
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            text = json.dumps(payload, ensure_ascii=False).casefold()
            if any(marker in text for marker in ("vision_config", "visual", "image_processor", "pixel_values")):
                capabilities.update({"vision_language", "image_understanding", "image_qa"})
            if "ocr" in text:
                capabilities.add("ocr")

    if "vision_language" in capabilities:
        capabilities.update({"lora_training", "qlora_training", "transformers_serving"})
    return sorted(capabilities)


def probe_model(repo_id: str, model_path: str | None) -> dict[str, Any]:
    path = Path(model_path).expanduser() if model_path else None
    capabilities = model_capabilities(repo_id, path)
    if not capabilities:
        return {
            "supported": False,
            "status": "unsupported",
            "capabilities": [],
            "message": "未识别到视觉语言模型特征；请提供视觉模型仓库或已下载模型目录。",
        }
    if path and not path.exists():
        return {
            "supported": False,
            "status": "missing",
            "capabilities": capabilities,
            "message": "模型路径不存在，无法完成本地探针。",
        }
    if path is None:
        return {
            "supported": False,
            "status": "candidate",
            "capabilities": capabilities,
            "message": "已识别为可能的视觉语言模型；可用于优云远程训练（训练实例会按 Repo ID 匹配预置或共享挂载的基座模型），但本地部署仍需填写模型目录并通过探针。",
        }
    return {
        "supported": True,
        "status": "passed",
        "capabilities": capabilities,
        "message": "已识别视觉语言模型能力；真实 Processor 和 GPU 加载仍需在目标环境执行。",
    }


def can_use_vision_model_for_training(
    model: Mapping[str, Any], tuning_mode: str, provider: str
) -> bool:
    """Return whether a registered vision model can enter the selected training path."""
    capabilities = set(model.get("capabilities") or [])
    required_capability = f"{tuning_mode}_training"
    if "vision_language" not in capabilities or required_capability not in capabilities:
        return False
    if model.get("supported") and model.get("probeStatus") == "passed":
        return True
    return (
        provider == "youyun"
        and model.get("probeStatus") == "candidate"
        and bool(str(model.get("repoId") or "").strip())
    )


def validate_image_bytes(data: bytes, filename: str = "image") -> str:
    if not data:
        raise ValueError("图片内容不能为空。")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("单张图片不能超过 20MB。")
    suffix = Path(filename).suffix.casefold()
    if suffix not in IMAGE_EXTENSIONS:
        raise ValueError("只支持 JPG、JPEG、PNG 和 WebP 图片。")
    if suffix in {".jpg", ".jpeg"} and not data.startswith(b"\xff\xd8\xff"):
        raise ValueError("图片扩展名与 JPEG 文件签名不一致。")
    if suffix == ".png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("图片扩展名与 PNG 文件签名不一致。")
    if suffix == ".webp" and not (len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
        raise ValueError("图片扩展名与 WebP 文件签名不一致。")
    return hashlib.sha256(data).hexdigest()


def safe_visual_member(name: str) -> Path:
    path = Path(name)
    if path.is_absolute() or ".." in path.parts or not name.strip():
        raise ValueError(f"压缩包包含不安全路径：{name}")
    return path


def validate_visual_understanding_bundle(
    archive_path: Path, dataset_dir: Path
) -> tuple[int, str | None, list[str]]:
    """Extract a controlled image-text ZIP and write normalized train.jsonl."""
    warnings: list[str] = []
    extracted_bytes = 0
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            if not members:
                return 0, "压缩包为空。", warnings
            for item in members:
                member_path = safe_visual_member(item.filename)
                if item.file_size > MAX_IMAGE_BYTES and member_path.suffix.lower() in IMAGE_EXTENSIONS:
                    return 0, f"图片 {item.filename} 超过 20MB。", warnings
                extracted_bytes += item.file_size
                if extracted_bytes > MAX_VISION_ARCHIVE_BYTES:
                    return 0, "视觉理解数据包解压后不能超过 2GB。", warnings

            manifest_item = next(
                (item for item in members if safe_visual_member(item.filename).as_posix() == "metadata.jsonl"),
                None,
            )
            if manifest_item is None:
                return 0, "压缩包根目录必须包含 metadata.jsonl。", warnings
            image_members = [
                item for item in members
                if safe_visual_member(item.filename).suffix.lower() in IMAGE_EXTENSIONS
            ]
            if not image_members:
                return 0, "压缩包中没有 JPG、PNG 或 WebP 图片。", warnings
            if len(image_members) > MAX_VISION_DATASET_IMAGES:
                return 0, f"单个视觉理解数据集最多包含 {MAX_VISION_DATASET_IMAGES} 张图片。", warnings
            for item in image_members:
                if safe_visual_member(item.filename).parts[:1] != ("images",):
                    return 0, f"图片必须位于 images/ 目录：{item.filename}", warnings

            for item in members:
                target = dataset_dir / safe_visual_member(item.filename)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)

            for item in image_members:
                image_path = dataset_dir / safe_visual_member(item.filename)
                validate_image_bytes(image_path.read_bytes(), image_path.name)

            records: list[dict[str, Any]] = []
            seen_images: set[str] = set()
            schema_kind: str | None = None
            with (dataset_dir / "metadata.jsonl").open(encoding="utf-8") as source:
                for line_number, line in enumerate(source, 1):
                    if not line.strip():
                        continue
                    try:
                        raw = json.loads(line)
                    except json.JSONDecodeError:
                        return len(records), f"metadata.jsonl 第 {line_number} 行不是合法 JSON。", warnings
                    if not isinstance(raw, dict):
                        return len(records), f"metadata.jsonl 第 {line_number} 行必须是 JSON 对象。", warnings
                    image_name = raw.get("file_name") or raw.get("image")
                    question = raw.get("question") or "请描述这张图片。"
                    if not isinstance(image_name, str) or not image_name.strip():
                        return len(records), f"metadata.jsonl 第 {line_number} 行缺少 file_name。", warnings
                    image_rel = safe_visual_member(image_name).as_posix()
                    image_path = dataset_dir / image_rel
                    if not image_rel.startswith("images/") or not image_path.is_file():
                        return len(records), f"metadata.jsonl 第 {line_number} 行引用的图片不存在：{image_name}", warnings
                    if not isinstance(question, str) or not question.strip():
                        return len(records), f"metadata.jsonl 第 {line_number} 行 question 不能为空。", warnings
                    has_preference = "chosen" in raw or "rejected" in raw
                    row_kind = "DPO" if has_preference else "SFT"
                    if schema_kind is None:
                        schema_kind = row_kind
                    elif schema_kind != row_kind:
                        return len(records), "metadata.jsonl 不能混用 answer 与 chosen/rejected 记录。", warnings
                    if has_preference:
                        chosen = raw.get("chosen")
                        rejected = raw.get("rejected")

                        def preference_value(value: Any, field: str) -> Any:
                            if isinstance(value, str) and value.strip():
                                return value.strip()
                            if isinstance(value, dict):
                                role = value.get("role", "assistant")
                                content = value.get("content")
                                if role == "assistant" and isinstance(content, str) and content.strip():
                                    return {"role": "assistant", "content": content.strip()}
                            if isinstance(value, list) and value:
                                normalized: list[dict[str, str]] = []
                                for message in value:
                                    if not isinstance(message, dict):
                                        raise ValueError(f"metadata.jsonl 第 {line_number} 行 {field} 消息格式无效。")
                                    role = message.get("role")
                                    content = message.get("content")
                                    if role not in {"system", "user", "assistant"} or not isinstance(content, str) or not content.strip():
                                        raise ValueError(f"metadata.jsonl 第 {line_number} 行 {field} 消息格式无效。")
                                    normalized.append({"role": role, "content": content.strip()})
                                return normalized
                            raise ValueError(f"metadata.jsonl 第 {line_number} 行 {field} 必须是非空偏好答案。")

                        try:
                            chosen = preference_value(chosen, "chosen")
                            rejected = preference_value(rejected, "rejected")
                        except ValueError as exc:
                            return len(records), str(exc), warnings
                        if json.dumps(chosen, ensure_ascii=False, sort_keys=True) == json.dumps(
                            rejected, ensure_ascii=False, sort_keys=True
                        ):
                            return len(records), f"metadata.jsonl 第 {line_number} 行 chosen 与 rejected 不能相同。", warnings
                        record: dict[str, Any] = {
                            "image": image_rel,
                            "question": question.strip(),
                            "chosen": chosen,
                            "rejected": rejected,
                        }
                    else:
                        answer = raw.get("text") or raw.get("answer")
                        if not isinstance(answer, str) or not answer.strip():
                            return len(records), f"metadata.jsonl 第 {line_number} 行必须包含非空 text 或 answer。", warnings
                        record = {
                            "image": image_rel,
                            "question": question.strip(),
                            "answer": answer.strip(),
                        }
                    if image_rel in seen_images:
                        warnings.append(f"metadata.jsonl 第 {line_number} 行重复引用图片：{image_rel}")
                    seen_images.add(image_rel)
                    records.append(record)
            if not records:
                return 0, "metadata.jsonl 没有可用记录。", warnings
            if len(seen_images) < len(image_members):
                warnings.append(f"有 {len(image_members) - len(seen_images)} 张图片未被 metadata.jsonl 引用。")
            with (dataset_dir / "train.jsonl").open("w", encoding="utf-8") as output:
                for record in records:
                    output.write(json.dumps(record, ensure_ascii=False) + "\n")
            return len(records), None, warnings
    except (zipfile.BadZipFile, UnicodeDecodeError, ValueError) as exc:
        message = str(exc) or "视觉理解数据包必须是有效 ZIP，metadata.jsonl 必须使用 UTF-8。"
        return 0, message, warnings


def validate_extracted_image_text_dataset(
    dataset_dir: Path,
) -> tuple[int, str | None, list[str]]:
    """Validate an extracted IMAGE_TEXT dataset directory without mutating it.

    Covers both flavors persisted by the upload endpoints: visual understanding
    datasets keep question/answer records in metadata.jsonl, text-to-image
    datasets keep file_name + caption rows. Images must stay under images/.
    """
    warnings: list[str] = []
    dataset_dir = Path(dataset_dir)
    manifest = dataset_dir / "metadata.jsonl"
    if not manifest.is_file():
        return 0, "数据集缺少 metadata.jsonl，无法校验。", warnings
    images_dir = dataset_dir / "images"
    if not images_dir.is_dir():
        return 0, "数据集缺少 images 目录。", warnings
    image_files = {item.name for item in images_dir.iterdir() if item.is_file()}
    if not image_files:
        return 0, "images 目录中没有图片文件。", warnings
    records = 0
    seen: set[str] = set()
    try:
        with manifest.open(encoding="utf-8") as source:
            for line_number, line in enumerate(source, 1):
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    return records, f"metadata.jsonl 第 {line_number} 行不是合法 JSON。", warnings
                if not isinstance(raw, dict):
                    return records, f"metadata.jsonl 第 {line_number} 行必须是 JSON 对象。", warnings
                image_name = raw.get("file_name") or raw.get("image")
                if not isinstance(image_name, str) or not image_name.strip():
                    return records, f"metadata.jsonl 第 {line_number} 行缺少 file_name。", warnings
                try:
                    image_rel = safe_visual_member(image_name).as_posix()
                except ValueError as exc:
                    return records, f"metadata.jsonl 第 {line_number} 行 {exc}", warnings
                if not image_rel.startswith("images/"):
                    return records, f"metadata.jsonl 第 {line_number} 行图片必须位于 images/ 目录：{image_name}", warnings
                if not (dataset_dir / image_rel).is_file():
                    return records, f"metadata.jsonl 第 {line_number} 行引用的图片不存在：{image_name}", warnings
                if "question" in raw:
                    question = raw.get("question")
                    if not isinstance(question, str) or not question.strip():
                        return records, f"metadata.jsonl 第 {line_number} 行 question 不能为空。", warnings
                    if "chosen" in raw or "rejected" in raw:
                        if not raw.get("chosen") or not raw.get("rejected"):
                            return records, f"metadata.jsonl 第 {line_number} 行 chosen 与 rejected 不能为空。", warnings
                    else:
                        answer = raw.get("text") or raw.get("answer")
                        if not isinstance(answer, str) or not answer.strip():
                            return records, f"metadata.jsonl 第 {line_number} 行必须包含非空 text 或 answer。", warnings
                else:
                    caption = raw.get("caption")
                    if not isinstance(caption, str) or not caption.strip():
                        return records, f"metadata.jsonl 第 {line_number} 行 caption 不能为空。", warnings
                if image_rel in seen:
                    warnings.append(f"metadata.jsonl 第 {line_number} 行重复引用图片：{image_rel}")
                seen.add(image_rel)
                records += 1
    except UnicodeDecodeError:
        return records, "metadata.jsonl 必须使用 UTF-8 编码。", warnings
    if not records:
        return 0, "metadata.jsonl 没有可用记录。", warnings
    unreferenced = len(image_files) - len(seen)
    if unreferenced > 0:
        warnings.append(f"有 {unreferenced} 张图片未被 metadata.jsonl 引用。")
    return records, None, warnings


def image_data_url(data: bytes, filename: str) -> str:
    suffix = Path(filename).suffix.casefold()
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}[suffix]
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def normalize_tokens(value: str) -> list[str]:
    return re.findall(r"[\w\u4e00-\u9fff]+", value.casefold())


def answer_metrics(prediction: str, reference: str) -> dict[str, float]:
    prediction = prediction.strip()
    reference = reference.strip()
    exact = 1.0 if prediction.casefold() == reference.casefold() else 0.0
    predicted = normalize_tokens(prediction)
    expected = normalize_tokens(reference)
    if not predicted or not expected:
        return {"exactMatch": exact, "tokenF1": 0.0}
    common: dict[str, int] = {}
    for token in predicted:
        common[token] = min(predicted.count(token), expected.count(token))
    overlap = sum(common.values())
    precision = overlap / len(predicted)
    recall = overlap / len(expected)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"exactMatch": exact, "tokenF1": f1}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"第 {line_number} 行必须是 JSON 对象。")
            records.append(value)
    if not records:
        raise ValueError("评测集没有有效样本。")
    return records


def summarize_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"count": 0, "total": 0, "success": 0, "successful": 0, "failed": 0, "averageLatencyMs": 0.0}
    exact = [row["metrics"]["exactMatch"] for row in rows if "metrics" in row]
    f1 = [row["metrics"]["tokenF1"] for row in rows if "metrics" in row]
    failed = sum(1 for row in rows if row.get("error"))
    latencies = [float(row["latencyMs"]) for row in rows if row.get("latencyMs") is not None]
    return {
        "count": len(rows),
        "total": len(rows),
        "success": len(rows) - failed,
        "successful": len(rows) - failed,
        "failed": failed,
        "exactMatch": sum(exact) / len(exact) if exact else 0.0,
        "tokenF1": sum(f1) / len(f1) if f1 else 0.0,
        "averageLatencyMs": sum(latencies) / len(latencies) if latencies else 0.0,
    }
