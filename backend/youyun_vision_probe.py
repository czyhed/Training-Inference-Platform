"""Structured compatibility results for remote visual-model probes."""
from __future__ import annotations

from dataclasses import dataclass
import asyncio
import json
import shlex
from typing import Any, Mapping


@dataclass(frozen=True)
class VisionProbeRequest:
    repo_id: str
    model_path: str
    template: str
    tuning_mode: str
    image_path: str | None = None


@dataclass(frozen=True)
class VisionProbeResult:
    passed: bool
    checks: dict[str, dict[str, Any]]
    blocking_reason: str | None
    recommended_tuning_mode: str
    estimated_vram_gb: int
    logs: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": self.checks,
            "blockingReason": self.blocking_reason,
            "recommendedTuningMode": self.recommended_tuning_mode,
            "estimatedVramGb": self.estimated_vram_gb,
            "logs": list(self.logs),
        }


def _estimated_vram(repo_id: str) -> int:
    lowered = repo_id.casefold()
    if "72b" in lowered or "30b" in lowered:
        return 80
    if "32b" in lowered or "8b" in lowered or "7b" in lowered:
        return 48
    return 16


def evaluate_probe_results(
    request: VisionProbeRequest,
    exit_codes: Mapping[str, int],
) -> VisionProbeResult:
    """Convert command exit codes into a deterministic probe report."""
    checks: dict[str, dict[str, Any]] = {}
    logs: list[str] = []
    order = (
        ("python", "python_runtime"),
        ("torch", "torch_cuda"),
        ("dependencies", "dependencies"),
        ("processor", "processor"),
        ("lora", "lora_injection"),
        ("forward", "forward"),
    )
    blocking_reason: str | None = None
    for key, label in order:
        code = int(exit_codes.get(key, 1))
        passed = code == 0
        checks[label] = {
            "status": "passed" if passed else "failed",
            "exitCode": code,
        }
        logs.append(f"{label}: {'passed' if passed else 'failed'}")
        if not passed and blocking_reason is None:
            blocking_reason = {
                "python": "python_runtime",
                "torch": "torch_cuda",
                "dependencies": "dependencies_missing",
                "processor": "processor_load_failed",
                "lora": "lora_injection_failed",
                "forward": "forward_failed",
            }[key]
    if not request.repo_id.strip():
        blocking_reason = blocking_reason or "model_repo_missing"
        checks["model"] = {"status": "failed", "message": "Repo ID 为空。"}
    else:
        checks["model"] = {
            "status": "passed",
            "repoId": request.repo_id,
            "template": request.template,
        }
    return VisionProbeResult(
        passed=blocking_reason is None,
        checks=checks,
        blocking_reason=blocking_reason,
        recommended_tuning_mode="qlora" if request.tuning_mode == "qlora" else "lora",
        estimated_vram_gb=_estimated_vram(request.repo_id),
        logs=logs,
    )


async def probe_remote_vision_environment(ssh: Any, request: VisionProbeRequest) -> VisionProbeResult:
    """Run bounded environment checks through an existing SSH client."""
    def python_command(source: str) -> str:
        return f"python3 -c {shlex.quote(source)}"

    model_path = shlex.quote(request.model_path)
    commands = {
        "python": python_command("import sys; print(sys.version)"),
        "torch": python_command(
            "import torch; print(torch.__version__); "
            "assert torch.cuda.is_available(), 'CUDA unavailable'; print(torch.cuda.get_device_name(0))"
        ),
        "dependencies": python_command("import transformers, peft, bitsandbytes, llamafactory"),
        "processor": python_command(
            "from transformers import AutoProcessor; "
            f"AutoProcessor.from_pretrained({request.repo_id!r}, trust_remote_code=True); "
            f"print({request.template!r})"
        ),
        "lora": python_command(
            "from peft import LoraConfig; "
            "LoraConfig(r=4, lora_alpha=8, target_modules=['q_proj'])"
        ),
        "forward": f"test -d {model_path}",
    }
    exit_codes: dict[str, int] = {}
    logs: list[str] = []
    for key, command in commands.items():
        try:
            code, output = await asyncio.to_thread(ssh.run, command, 180, False)
        except Exception as exc:  # noqa: BLE001 - remote failures become structured probe failures
            code, output = 1, str(exc)
        exit_codes[key] = int(code)
        if output:
            logs.append(f"{key}: {str(output)[-1000:]}")
    result = evaluate_probe_results(request, exit_codes)
    return VisionProbeResult(
        passed=result.passed,
        checks=result.checks,
        blocking_reason=result.blocking_reason,
        recommended_tuning_mode=result.recommended_tuning_mode,
        estimated_vram_gb=result.estimated_vram_gb,
        logs=result.logs + logs,
    )


__all__ = ["VisionProbeRequest", "VisionProbeResult", "evaluate_probe_results"]
