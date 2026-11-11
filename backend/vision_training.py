from __future__ import annotations

"""Deterministic LLaMA-Factory materialization for visual SFT/DPO datasets."""

import hashlib
import json
import random
import shutil
from pathlib import Path
from typing import Any, Mapping

from backend.llamafactory import dump_yaml
from backend.vision import load_jsonl, safe_visual_member, validate_image_bytes


VISION_DATASET_FORMAT_VERSION = "llamafactory-mllm-v1"
VISION_DATASET_COLUMNS = {"messages": "messages", "images": "images"}
VISION_DPO_DATASET_COLUMNS = {
    "messages": "messages",
    "chosen": "chosen",
    "rejected": "rejected",
    "images": "images",
}
VISION_DATASET_TAGS = {
    "role_tag": "role",
    "content_tag": "content",
    "user_tag": "user",
    "assistant_tag": "assistant",
}


def resolve_vision_template(repo_id: str, model_path: str = "") -> str:
    """Resolve only model families whose LLaMA-Factory template is known."""
    markers = [repo_id.casefold()]
    path = Path(model_path).expanduser() if model_path else None
    if path and path.is_dir():
        config_path = path / "config.json"
        if config_path.is_file():
            try:
                markers.append(config_path.read_text(encoding="utf-8").casefold())
            except (OSError, UnicodeDecodeError):
                pass
    identity = " ".join(markers)
    if any(marker in identity for marker in ("qwen2-vl", "qwen2.5-vl", "qwen2_vl", "qwen2_5_vl")):
        return "qwen2_vl"
    if any(marker in identity for marker in ("qwen3-vl", "qwen3_vl", "qwen3-omni", "qwen3_omni")):
        return "qwen3_vl_nothink"
    raise ValueError("当前视觉训练配置仅验证了 Qwen2-VL、Qwen2.5-VL 和 Qwen3-VL 模板。")


def _dataset_name(dataset_id: str) -> str:
    value = "".join(char if char.isalnum() or char in "._-" else "-" for char in dataset_id)
    value = value.strip(".-")
    if not value:
        raise ValueError("数据集 ID 无法转换为安全的 LLaMA-Factory 名称。")
    return f"vision_{value}"


def _safe_source_image(source_root: Path, image_value: Any, line_number: int) -> tuple[str, Path]:
    if not isinstance(image_value, str) or not image_value.strip():
        raise ValueError(f"train.jsonl 第 {line_number} 行缺少 image。")
    relative = safe_visual_member(image_value.strip())
    if relative.parts[:1] != ("images",):
        raise ValueError(f"train.jsonl 第 {line_number} 行图片必须位于 images/ 目录。")
    source = (source_root / relative).resolve()
    root = source_root.resolve()
    if not source.is_relative_to(root) or not source.is_file():
        raise ValueError(f"train.jsonl 第 {line_number} 行引用的图片不存在：{relative.as_posix()}")
    validate_image_bytes(source.read_bytes(), source.name)
    return relative.as_posix(), source


def _normalize_record(
    record: Mapping[str, Any],
    source_root: Path,
    line_number: int,
    task_type: str = "SFT",
) -> tuple[dict[str, Any], Path, str]:
    if task_type not in {"SFT", "DPO"}:
        raise ValueError("视觉训练类型只能是 SFT 或 DPO。")
    image_rel, image_path = _safe_source_image(
        source_root, record.get("image") or record.get("file_name"), line_number
    )
    question = record.get("question") or "请描述这张图片。"
    if not isinstance(question, str) or not question.strip():
        raise ValueError(f"train.jsonl 第 {line_number} 行 question 不能为空。")
    question = question.strip()
    placeholder_count = question.count("<image>")
    if placeholder_count > 1:
        raise ValueError(f"train.jsonl 第 {line_number} 行只能包含一个 <image> 占位符。")
    if placeholder_count == 0:
        question = f"<image>{question}"
    if task_type == "SFT":
        answer = record.get("answer") or record.get("text")
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError(f"train.jsonl 第 {line_number} 行 answer 不能为空。")
        sample = {
            "messages": [
                {"role": "user", "content": question},
                {"role": "assistant", "content": answer.strip()},
            ],
            "images": [image_rel],
        }
        return sample, image_path, image_rel

    def normalize_preference(value: Any, field: str) -> dict[str, str] | list[dict[str, str]]:
        if isinstance(value, str):
            if not value.strip():
                raise ValueError(f"train.jsonl 第 {line_number} 行 {field} 不能为空。")
            return {"role": "assistant", "content": value.strip()}
        if isinstance(value, Mapping):
            role = value.get("role", "assistant")
            content = value.get("content")
            if role != "assistant" or not isinstance(content, str) or not content.strip():
                raise ValueError(
                    f"train.jsonl 第 {line_number} 行 {field} 必须是 assistant 消息或非空字符串。"
                )
            return {"role": "assistant", "content": content.strip()}
        if isinstance(value, list) and value:
            messages: list[dict[str, str]] = []
            for message_index, message in enumerate(value, 1):
                if not isinstance(message, Mapping):
                    raise ValueError(
                        f"train.jsonl 第 {line_number} 行 {field} 第 {message_index} 条消息必须是对象。"
                    )
                role = message.get("role")
                content = message.get("content")
                if role not in {"system", "user", "assistant"} or not isinstance(content, str) or not content.strip():
                    raise ValueError(
                        f"train.jsonl 第 {line_number} 行 {field} 包含无效消息。"
                    )
                messages.append({"role": role, "content": content.strip()})
            return messages
        raise ValueError(
            f"train.jsonl 第 {line_number} 行 {field} 必须是 assistant 消息或非空字符串。"
        )

    chosen = normalize_preference(record.get("chosen"), "chosen")
    rejected = normalize_preference(record.get("rejected"), "rejected")
    if json.dumps(chosen, ensure_ascii=False, sort_keys=True) == json.dumps(
        rejected, ensure_ascii=False, sort_keys=True
    ):
        raise ValueError(f"train.jsonl 第 {line_number} 行 chosen 与 rejected 不能相同。")
    sample = {
        "messages": [{"role": "user", "content": question}],
        "chosen": chosen,
        "rejected": rejected,
        "images": [image_rel],
    }
    return sample, image_path, image_rel


def _split_indices(sample_count: int, validation_split: int, seed: int) -> tuple[list[int], list[int]]:
    if sample_count <= 0:
        raise ValueError("视觉训练数据集没有有效样本。")
    indices = list(range(sample_count))
    random.Random(seed).shuffle(indices)
    if validation_split <= 0 or sample_count < 2:
        return indices, []
    validation_count = min(sample_count - 1, max(1, (sample_count * validation_split + 99) // 100))
    validation = sorted(indices[:validation_count])
    training = sorted(indices[validation_count:])
    return training, validation


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def validate_vision_training_dataset(*, train_path: Path, task_type: str = "SFT") -> int:
    """Validate the selected visual dataset without creating a training bundle."""
    source = train_path.expanduser().resolve()
    if not source.is_file():
        raise ValueError("视觉数据集 trainPath 不存在。")
    if source.name.casefold() != "train.jsonl":
        raise ValueError("视觉数据集必须使用上传流程生成的 train.jsonl。")
    records = load_jsonl(source)
    for line_number, record in enumerate(records, 1):
        _normalize_record(record, source.parent, line_number, task_type)
    return len(records)


def materialize_vision_training_bundle(
    *,
    dataset_id: str,
    train_path: Path,
    destination: Path,
    model: str,
    template: str,
    tuning_mode: str,
    image_max_pixels: int,
    batch_size: int,
    gradient_accumulation: int,
    validation_split: int,
    epochs: float,
    learning_rate: float,
    lora_rank: int,
    lora_alpha: int,
    lora_dropout: float,
    gradient_checkpointing: bool,
    task_type: str = "SFT",
    dpo_beta: float = 0.1,
    seed: int = 42,
) -> dict[str, Any]:
    """Validate, split, copy, and emit a complete visual SFT/DPO preview bundle."""
    source = train_path.expanduser().resolve()
    if not source.is_file():
        raise ValueError("视觉数据集 trainPath 不存在。")
    if source.name.casefold() != "train.jsonl":
        raise ValueError("视觉数据集必须使用上传流程生成的 train.jsonl。")
    if tuning_mode not in {"lora", "qlora"}:
        raise ValueError("视觉训练仅支持 LoRA 或 QLoRA。")
    if task_type not in {"SFT", "DPO"}:
        raise ValueError("视觉训练类型只能是 SFT 或 DPO。")
    if not 0 < dpo_beta <= 1:
        raise ValueError("DPO Beta 必须大于 0 且不超过 1。")

    source_root = source.parent
    raw_records = load_jsonl(source)
    samples: list[dict[str, Any]] = []
    image_sources: dict[str, Path] = {}
    image_hashes: dict[str, str] = {}
    for line_number, record in enumerate(raw_records, 1):
        sample, image_path, image_rel = _normalize_record(
            record, source_root, line_number, task_type
        )
        samples.append(sample)
        image_sources[image_rel] = image_path
        image_hashes[image_rel] = hashlib.sha256(image_path.read_bytes()).hexdigest()

    train_indices, validation_indices = _split_indices(len(samples), validation_split, seed)
    train_records = [samples[index] for index in train_indices]
    validation_records = [samples[index] for index in validation_indices]

    target_root = destination.expanduser().resolve()
    dataset_dir = target_root / "dataset"
    output_dir = target_root / "output"
    if target_root.exists():
        raise ValueError("视觉训练预览目录已存在，请使用新的预览 ID。")
    dataset_dir.mkdir(parents=True)
    output_dir.mkdir()

    for image_rel, image_path in sorted(image_sources.items()):
        target = dataset_dir / image_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(image_path, target)

    dataset_name = _dataset_name(dataset_id)
    train_name = "vision_train.jsonl"
    validation_name = "vision_validation.jsonl"
    _write_jsonl(dataset_dir / train_name, train_records)
    if validation_records:
        _write_jsonl(dataset_dir / validation_name, validation_records)

    info_entry = {
        "file_name": train_name,
        "formatting": "sharegpt",
        "columns": VISION_DPO_DATASET_COLUMNS if task_type == "DPO" else VISION_DATASET_COLUMNS,
        "tags": VISION_DATASET_TAGS,
    }
    if task_type == "DPO":
        info_entry["ranking"] = True
    dataset_info: dict[str, Any] = {dataset_name: info_entry}
    validation_name_id = f"{dataset_name}_validation"
    if validation_records:
        dataset_info[validation_name_id] = {**info_entry, "file_name": validation_name}
    _write_json(dataset_dir / "dataset_info.json", dataset_info)

    config: dict[str, Any] = {
        "model_name_or_path": model,
        "trust_remote_code": True,
        "image_max_pixels": image_max_pixels,
        "stage": task_type.lower(),
        "do_train": True,
        "finetuning_type": "lora",
        "lora_rank": lora_rank,
        "lora_alpha": lora_alpha,
        "lora_dropout": lora_dropout,
        "lora_target": "all",
        "freeze_vision_tower": True,
        "freeze_multi_modal_projector": True,
        "freeze_language_model": False,
        "dataset_dir": str(dataset_dir),
        "dataset": dataset_name,
        "template": template,
        "cutoff_len": 2048,
        "preprocessing_num_workers": 4,
        "dataloader_num_workers": 2,
        "output_dir": str(output_dir),
        "logging_steps": 5,
        "save_strategy": "steps",
        "save_steps": 50,
        "plot_loss": True,
        "overwrite_output_dir": True,
        "report_to": "none",
        "per_device_train_batch_size": batch_size,
        "gradient_accumulation_steps": gradient_accumulation,
        "learning_rate": learning_rate,
        "num_train_epochs": epochs,
        "lr_scheduler_type": "cosine",
        "warmup_ratio": 0.1,
        "gradient_checkpointing": gradient_checkpointing,
        # Local Turing-class cards such as RTX 2060 do not provide BF16;
        # the remote GPU runner upgrades this pair to BF16 when appropriate.
        "bf16": False,
        "fp16": True,
        "seed": seed,
        "ddp_timeout": 180000000,
    }
    if task_type == "DPO":
        config["pref_beta"] = dpo_beta
        config["pref_loss"] = "sigmoid"
    if tuning_mode == "qlora":
        config["quantization_bit"] = 4
    if validation_records:
        config.update(
            do_eval=True,
            eval_dataset=validation_name_id,
            per_device_eval_batch_size=1,
            eval_strategy="steps",
            eval_steps=50,
        )
    else:
        config["do_eval"] = False

    yaml_text = dump_yaml(config)
    (target_root / "train.yaml").write_text(yaml_text, encoding="utf-8")
    manifest = {
        "formatVersion": VISION_DATASET_FORMAT_VERSION,
        "datasetId": dataset_id,
        "sourceTrainPath": str(source),
        "sourceSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "model": model,
        "template": template,
        "tuningMode": tuning_mode,
        "taskType": task_type,
        "dpoBeta": dpo_beta if task_type == "DPO" else None,
        "seed": seed,
        "totalSamples": len(samples),
        "trainSamples": len(train_records),
        "validationSamples": len(validation_records),
        "referencedImages": len(image_sources),
        "imageSha256": image_hashes,
        "files": {
            "train": train_name,
            "validation": validation_name if validation_records else None,
            "datasetInfo": "dataset_info.json",
            "config": "train.yaml",
        },
    }
    _write_json(target_root / "manifest.json", manifest)
    return {
        "root": str(target_root),
        "datasetDir": str(dataset_dir),
        "outputDir": str(output_dir),
        "configPath": str(target_root / "train.yaml"),
        "manifestPath": str(target_root / "manifest.json"),
        "config": config,
        "yaml": yaml_text,
        "manifest": manifest,
    }


__all__ = [
    "VISION_DATASET_FORMAT_VERSION",
    "materialize_vision_training_bundle",
    "resolve_vision_template",
    "validate_vision_training_dataset",
]
