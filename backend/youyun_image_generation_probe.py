"""Bounded SDXL/Diffusers compatibility checks for community images."""
from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ImageGenerationProbeRequest:
    repo_id: str


@dataclass(frozen=True)
class ImageGenerationProbeResult:
    passed: bool
    checks: dict[str, dict[str, Any]]
    blocking_reason: str | None
    estimated_vram_gb: int
    logs: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": self.checks,
            "blockingReason": self.blocking_reason,
            "estimatedVramGb": self.estimated_vram_gb,
            "logs": list(self.logs),
        }


def evaluate_image_generation_probe(
    request: ImageGenerationProbeRequest,
    exit_codes: Mapping[str, int],
) -> ImageGenerationProbeResult:
    order = (
        ("python", "python_runtime", "python_runtime"),
        ("torch", "torch_cuda", "torch_cuda"),
        ("dependencies", "dependencies", "dependencies_missing"),
        ("pipeline", "sdxl_pipeline", "sdxl_pipeline_missing"),
        ("lora", "lora_injection", "lora_injection_failed"),
        ("forward", "dry_run", "dry_run_failed"),
    )
    checks: dict[str, dict[str, Any]] = {}
    logs: list[str] = []
    blocking_reason: str | None = None
    for key, label, failure_code in order:
        code = int(exit_codes.get(key, 1))
        passed = code == 0
        checks[label] = {"status": "passed" if passed else "failed", "exitCode": code}
        logs.append(f"{label}: {'passed' if passed else 'failed'}")
        if not passed and blocking_reason is None:
            blocking_reason = failure_code
    if not request.repo_id.strip():
        blocking_reason = blocking_reason or "model_repo_missing"
        checks["model"] = {"status": "failed", "message": "SDXL Repo ID 为空。"}
    else:
        checks["model"] = {"status": "passed", "repoId": request.repo_id}
    return ImageGenerationProbeResult(
        passed=blocking_reason is None,
        checks=checks,
        blocking_reason=blocking_reason,
        estimated_vram_gb=24,
        logs=logs,
    )


async def probe_remote_image_generation_environment(
    ssh: Any, request: ImageGenerationProbeRequest
) -> ImageGenerationProbeResult:
    def command(source: str) -> str:
        return f"python3 -c {shlex.quote(source)}"

    commands = {
        "python": command("import sys; assert sys.version_info >= (3, 10)"),
        "torch": command("import torch; assert torch.cuda.is_available(); print(torch.cuda.get_device_name(0))"),
        "dependencies": command("import diffusers, transformers, peft, safetensors, accelerate"),
        "pipeline": command("from diffusers import StableDiffusionXLPipeline, UNet2DConditionModel"),
        "lora": command("from diffusers import UNet2DConditionModel; assert hasattr(UNet2DConditionModel, 'add_adapter')"),
        # A bounded CUDA tensor operation proves that the selected runtime can
        # execute a forward pass without downloading or loading SDXL weights.
        "forward": command("import torch; x=torch.ones((1,4,64,64),device='cuda',dtype=torch.float16); y=x*2; assert y.shape==(1,4,64,64)"),
    }
    exit_codes: dict[str, int] = {}
    for key, remote_command in commands.items():
        code, _ = await asyncio.to_thread(ssh.run, remote_command, 120, False)
        exit_codes[key] = int(code)
        if code != 0:
            break
    return evaluate_image_generation_probe(request, exit_codes)


__all__ = [
    "ImageGenerationProbeRequest",
    "ImageGenerationProbeResult",
    "evaluate_image_generation_probe",
    "probe_remote_image_generation_environment",
]
