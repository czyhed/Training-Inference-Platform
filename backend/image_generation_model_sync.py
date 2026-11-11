from __future__ import annotations

"""Idempotent publication of SDXL LoRA artifacts to the model catalog."""

from pathlib import Path
from typing import Any, Mapping


def sync_image_generation_adapters(store: Any, task: Mapping[str, Any]) -> list[dict[str, Any]]:
    if str(task.get("state") or "") not in {"completed", "stopped"}:
        return []
    artifacts = [item for item in (task.get("artifacts") or []) if isinstance(item, Mapping) and item.get("type") == "adapter"]
    if not artifacts:
        return []
    repo_id = str(task.get("modelRepoId") or "").strip()
    if not repo_id:
        return []
    base_path = str(task.get("baseModelPath") or "")
    ensure = getattr(store, "ensure_base_model", None)
    if callable(ensure):
        base = ensure(repo_id, path=base_path or repo_id.replace("/", "--"), source="文生图 SDXL")
        base_id = str(base.get("id") or "")
    else:
        base_id = str(task.get("modelId") or "")
    variants: list[dict[str, Any]] = []
    for artifact in artifacts:
        variant = str(artifact.get("variant") or "")
        path = Path(str(artifact.get("path") or "")).expanduser().resolve()
        if variant not in {"best", "final"}:
            continue
        status = "available" if path.is_dir() and any(item.is_file() and item.name in {"pytorch_lora_weights.safetensors", "adapter_model.safetensors", "adapter_model.bin", "pytorch_lora_weights.bin"} for item in path.iterdir()) else "unavailable"
        variants.append({"key": variant, "label": "最佳 Adapter" if variant == "best" else "最终 Adapter", "path": str(path), "status": status, "step": artifact.get("step") or artifact.get("checkpointStep"), "evalLoss": artifact.get("evalLoss"), "selected": bool(artifact.get("selected"))})
    if not variants:
        return []
    upsert = getattr(store, "upsert_trained_model_asset", None)
    if not callable(upsert):
        return variants
    return upsert(task_id=str(task.get("id") or ""), name=str(task.get("outputName") or task.get("name") or task.get("id") or "SDXL Adapter"), repo_id=repo_id, base_model_id=base_id, method="LoRA", dataset_name=str(task.get("datasetName") or task.get("datasetId") or ""), variants=variants)


__all__ = ["sync_image_generation_adapters"]
