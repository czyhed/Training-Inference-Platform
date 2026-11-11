from __future__ import annotations

"""Execution contracts for SDXL LoRA training.

The real Youyun runner can implement the same ``run`` callback contract.  The
Fake executor is intentionally deterministic so API and runtime tests never
need a GPU or a cloud credential.
"""

import asyncio
import hashlib
import json
import math
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from backend.image_generation_training import resolve_sdxl_lora_entrypoint
from backend.training_executor import TrainingEvent


EventCallback = Callable[[TrainingEvent], Any]


_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_SDXL_STEP = re.compile(
    r"Steps:\s*(?P<progress>\d+)%.*?\|\s*(?P<step>\d+)\s*/\s*(?P<total>\d+)"
    r".*?\blr=(?P<learning_rate>[-+0-9.eE]+),\s*step_loss=(?P<loss>[-+0-9.eE]+)"
)


def parse_sdxl_training_log(text: str) -> dict[str, list[Any]]:
    """Normalize Diffusers tqdm output into readable logs and step metrics."""
    entries: list[tuple[str, Any]] = []
    seen_logs: set[str] = set()
    metric_by_step: dict[int, dict[str, Any]] = {}
    seen_steps: set[int] = set()
    normalized = _ANSI_ESCAPE.sub("", str(text or "")).replace("\r", "\n")
    for raw_line in normalized.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _SDXL_STEP.search(line)
        if match is not None:
            try:
                step = int(match.group("step"))
                total_steps = int(match.group("total"))
                progress = int(match.group("progress"))
                loss = float(match.group("loss"))
                learning_rate = float(match.group("learning_rate"))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(loss) or not math.isfinite(learning_rate):
                continue
            metric_by_step[step] = {
                "step": step,
                "loss": loss,
                "learningRate": learning_rate,
                "progress": max(0, min(100, progress)),
                "totalSteps": total_steps,
            }
            if step not in seen_steps:
                entries.append(("metric", step))
                seen_steps.add(step)
            continue
        if line not in seen_logs:
            entries.append(("log", line))
            seen_logs.add(line)

    logs: list[str] = []
    for kind, value in entries:
        if kind == "log":
            logs.append(str(value))
            continue
        metric = metric_by_step[int(value)]
        logs.append(
            f"Step {metric['step']}/{metric['totalSteps']} · "
            f"Loss {metric['loss']:.6f} · LR {metric['learningRate']:.6g} · "
            f"{metric['progress']}%"
        )
    metrics = [metric_by_step[step] for step in sorted(metric_by_step)]
    return {"logs": logs[-5000:], "metrics": metrics[-5000:]}


def _arg(value: Any) -> str:
    text = str(value or "")
    if not text or any(char in text for char in (";", "&&", "|", "\n", "\r")):
        raise ValueError("训练参数包含不安全字符。")
    return text


def build_sdxl_lora_command(config: Mapping[str, Any]) -> list[str]:
    repo_id = _arg(config.get("model_repo_id"))
    entrypoint = resolve_sdxl_lora_entrypoint(repo_id)
    python_executable = _arg(config.get("python_executable") or sys.executable)
    output_dir = _arg(config.get("output_dir"))
    train_file = _arg(config.get("train_file"))
    args = [
        python_executable,
        entrypoint,
        "--pretrained_model_name_or_path", _arg(config.get("model_path") or repo_id),
        "--variant", _arg(config.get("variant") or "fp16"),
        "--train_data_dir", _arg(config.get("dataset_dir") or Path(train_file).parent),
        "--caption_column", "text",
        # Hugging Face imagefolder exposes decoded files as the ``image``
        # column; ``file_name`` is only the metadata.jsonl locator key.
        "--image_column", "image",
        "--resolution", str(int(config.get("resolution") or 1024)),
        "--output_dir", output_dir,
        "--train_batch_size", str(int(config.get("train_batch_size") or 1)),
        "--gradient_accumulation_steps", str(int(config.get("gradient_accumulation_steps") or 1)),
        "--learning_rate", str(float(config.get("learning_rate") or 1e-4)),
        "--rank", str(int(config.get("lora_rank") or 16)),
        "--seed", str(int(config.get("seed") or 42)),
    ]
    if config.get("max_train_steps") is not None:
        args.extend(["--max_train_steps", str(int(config["max_train_steps"]))])
    else:
        args.extend(["--num_train_epochs", str(float(config.get("num_train_epochs") or 1))])
    if config.get("validation_prompt"):
        args.extend(["--validation_prompt", _arg(config["validation_prompt"])])
    return args


_WEIGHT_NAMES = {
    "pytorch_lora_weights.safetensors",
    "adapter_model.safetensors",
    "adapter_model.bin",
    "pytorch_lora_weights.bin",
}
_CONFIG_NAMES = {"adapter_config.json", "config.json"}


def _find_variant_dir(output_dir: Path, variant: str) -> Path | None:
    candidates = [output_dir / variant, output_dir / f"{variant}_adapter", output_dir / f"adapter_{variant}"]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def collect_image_generation_adapters(output_dir: Path, manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    root = output_dir.expanduser().resolve()
    if not root.is_dir():
        raise ValueError("训练输出目录不存在。")
    artifacts: list[dict[str, Any]] = []
    for variant in ("best", "final"):
        directory = _find_variant_dir(root, variant)
        if directory is None:
            continue
        weights = next((item for item in sorted(directory.iterdir()) if item.is_file() and item.name in _WEIGHT_NAMES), None)
        if weights is None:
            continue
        config_file = next((item for item in sorted(directory.iterdir()) if item.is_file() and item.name in _CONFIG_NAMES), None)
        digest = hashlib.sha256()
        for item in sorted(directory.iterdir()):
            if item.is_file() and item.name in _WEIGHT_NAMES | _CONFIG_NAMES:
                digest.update(item.name.encode())
                digest.update(item.read_bytes())
        artifacts.append({
            "type": "adapter",
            "variant": variant,
            "path": str(directory),
            "weightFile": weights.name,
            "configFile": config_file.name if config_file else None,
            "sha256": digest.hexdigest(),
            "datasetId": manifest.get("datasetId"),
            "modelRepoId": manifest.get("modelRepoId"),
            "manifestSha256": hashlib.sha256(json.dumps(dict(manifest), sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            "selected": variant == "best",
        })
    if not artifacts:
        raise ValueError("训练输出中没有可加载的 Best 或 Final LoRA Adapter。")
    if not any(item["variant"] == "best" for item in artifacts):
        artifacts[0]["selected"] = True
    return artifacts


class ImageGenerationTrainingExecutor:
    async def run(self, task: Mapping[str, Any], *, on_event: EventCallback | None = None, cancel_event: asyncio.Event | None = None) -> dict[str, Any]:
        raise NotImplementedError


class FakeImageGenerationTrainingExecutor(ImageGenerationTrainingExecutor):
    """Create tiny deterministic adapters and metrics for contract testing."""

    async def run(self, task: Mapping[str, Any], *, on_event: EventCallback | None = None, cancel_event: asyncio.Event | None = None) -> dict[str, Any]:
        root = Path(str(task.get("previewRoot") or task.get("workdir") or "")).expanduser().resolve()
        output_dir = Path(str(task.get("outputDir") or root / "output")).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        async def emit(event: TrainingEvent) -> None:
            if on_event is None:
                return
            result = on_event(event)
            if isinstance(result, Awaitable):
                await result

        await emit(TrainingEvent("stage", "开始准备 SDXL LoRA 训练。", {"phase": "preparing", "progress": 5}))
        for step in range(1, 3):
            if cancel_event is not None and cancel_event.is_set():
                return {"status": "stopped", "artifacts": [], "failureCode": "training_stopped"}
            await emit(TrainingEvent("metric", f"Step {step}", {"step": step, "totalSteps": 2, "loss": 1.0 / step, "progress": 20 + step * 25}))
            await asyncio.sleep(0)
        manifest = {"datasetId": task.get("datasetId"), "modelRepoId": task.get("modelRepoId")}
        for variant, filename in (("best", "pytorch_lora_weights.safetensors"), ("final", "adapter_model.safetensors")):
            directory = output_dir / variant
            directory.mkdir(parents=True, exist_ok=True)
            (directory / filename).write_bytes(f"fake-{variant}".encode())
            (directory / "adapter_config.json").write_text(json.dumps({"base_model_name_or_path": task.get("modelRepoId")}, ensure_ascii=False), encoding="utf-8")
        artifacts = collect_image_generation_adapters(output_dir, manifest)
        await emit(TrainingEvent("stage", "训练产物已收集。", {"phase": "collecting", "progress": 98, "artifacts": artifacts}))
        return {"status": "completed", "artifacts": artifacts, "progress": 100, "currentStep": "训练完成"}


__all__ = [
    "FakeImageGenerationTrainingExecutor",
    "ImageGenerationTrainingExecutor",
    "build_sdxl_lora_command",
    "collect_image_generation_adapters",
    "parse_sdxl_training_log",
]
