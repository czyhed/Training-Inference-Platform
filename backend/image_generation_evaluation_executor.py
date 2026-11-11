from __future__ import annotations

"""Execution contracts for image-generation evaluation batches."""

import asyncio
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from PIL import Image, ImageDraw

EventCallback = Callable[[str, str, Mapping[str, Any] | None], None]


class ImageGenerationEvaluationExecutor:
    async def run(self, task: Mapping[str, Any], *, on_event: EventCallback | None = None, cancel_event: asyncio.Event | None = None) -> dict[str, Any]:
        raise NotImplementedError


class FakeImageGenerationEvaluationExecutor(ImageGenerationEvaluationExecutor):
    def __init__(self, *, delay: float = 0.0, failed_sample_ids: set[str] | None = None) -> None:
        self.delay = delay
        self.failed_sample_ids = set(failed_sample_ids or set())

    async def run(self, task: Mapping[str, Any], *, on_event: EventCallback | None = None, cancel_event: asyncio.Event | None = None) -> dict[str, Any]:
        result_root = Path(str(task.get("resultRoot") or "")).expanduser().resolve()
        generated_root = result_root / "generated"
        generated_root.mkdir(parents=True, exist_ok=True)
        samples = [item for item in task.get("samples") or [] if isinstance(item, Mapping)]
        results: list[dict[str, Any]] = []
        for index, sample in enumerate(samples):
            if cancel_event and cancel_event.is_set():
                return {"status": "stopped", "results": results, "summary": _summary(results, len(samples)), "cleanupState": "released"}
            if self.delay:
                await asyncio.sleep(self.delay)
            sample_id = str(sample.get("id") or f"sample-{index + 1}")
            if sample_id in self.failed_sample_ids:
                results.append({"sampleId": sample_id, "prompt": sample.get("caption") or "", "state": "failed", "error": "假执行器模拟样本失败。"})
            else:
                output = generated_root / f"{sample_id}.png"
                image = Image.new("RGB", (int(task.get("width") or 64), int(task.get("height") or 64)), (40 + index * 20 % 180, 90, 180))
                ImageDraw.Draw(image).text((4, 4), sample_id[:20], fill="white")
                image.save(output)
                results.append({"sampleId": sample_id, "prompt": sample.get("caption") or "", "state": "completed", "path": str(output), "fileName": output.name, "latencyMs": 10 + index, "clipTextImage": 0.75, "clipReferenceImage": 0.68})
            if on_event:
                on_event("running", f"已完成 {index + 1} / {len(samples)} 条样本。", {"progress": int((index + 1) * 100 / max(1, len(samples))), "results": results})
        return {"status": "completed", "results": results, "summary": _summary(results, len(samples)), "cleanupState": "released"}


def _summary(results: list[Mapping[str, Any]], total: int) -> dict[str, Any]:
    completed = [item for item in results if item.get("state") == "completed"]
    latencies = [float(item["latencyMs"]) for item in completed if isinstance(item.get("latencyMs"), (int, float))]
    text_scores = [float(item["clipTextImage"]) for item in completed if isinstance(item.get("clipTextImage"), (int, float))]
    reference_scores = [float(item["clipReferenceImage"]) for item in completed if isinstance(item.get("clipReferenceImage"), (int, float))]
    return {"sampleCount": total, "completedSamples": len(completed), "failedSamples": max(0, total - len(completed)), "successRate": len(completed) / total if total else 0.0, "averageLatencyMs": sum(latencies) / len(latencies) if latencies else 0.0, "clipTextImageMean": sum(text_scores) / len(text_scores) if text_scores else None, "clipReferenceImageMean": sum(reference_scores) / len(reference_scores) if reference_scores else None, "computedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


__all__ = ["ImageGenerationEvaluationExecutor", "FakeImageGenerationEvaluationExecutor"]
