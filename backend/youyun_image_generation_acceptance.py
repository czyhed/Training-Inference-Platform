"""Guarded one-shot Youyun execution for SDXL community-image probes."""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Awaitable, Callable, Mapping

from backend.providers.youyun.ssh import YouyunSSHClient
from backend.youyun_image_generation_probe import (
    ImageGenerationProbeRequest,
    ImageGenerationProbeResult,
    probe_remote_image_generation_environment,
)


def _safe_failure(exc: BaseException) -> str:
    text = str(exc) or type(exc).__name__
    lowered = text.casefold()
    if any(marker in lowered for marker in ("password", "private_key", "ssh-rsa", "begin private")):
        return type(exc).__name__
    return text[:500]


class YouyunImageGenerationProbeRunner:
    def __init__(
        self,
        manager: Any,
        *,
        ssh_factory: Callable[[], Any] = YouyunSSHClient,
        probe: Callable[[Any, ImageGenerationProbeRequest], Awaitable[ImageGenerationProbeResult]] = probe_remote_image_generation_environment,
        ssh_ready_timeout: float | None = None,
        ssh_retry_delay: float | None = None,
        probe_timeout: float | None = None,
    ) -> None:
        self.manager = manager
        self.ssh_factory = ssh_factory
        self.probe = probe
        self.ssh_ready_timeout = max(0.0, float(ssh_ready_timeout if ssh_ready_timeout is not None else os.environ.get("YOUYUN_SSH_READY_TIMEOUT_SECONDS", "300")))
        self.ssh_retry_delay = max(0.0, float(ssh_retry_delay if ssh_retry_delay is not None else os.environ.get("YOUYUN_SSH_CONNECT_RETRY_DELAY_SECONDS", "5")))
        self.probe_timeout = max(0.01, float(probe_timeout if probe_timeout is not None else os.environ.get("YOUYUN_IMAGE_GENERATION_PROBE_TIMEOUT_SECONDS", "600")))
        self._lock = asyncio.Lock()
        self.unresolved_instance_id: str | None = None

    async def run(self, request: ImageGenerationProbeRequest, resource: Mapping[str, Any]) -> dict[str, Any]:
        if self.unresolved_instance_id:
            raise RuntimeError(f"优云实例 {self.unresolved_instance_id} 尚未释放，禁止创建新的文生图探针实例。")
        if self._lock.locked():
            raise RuntimeError("已有优云文生图探针正在执行。")
        async with self._lock:
            instance: Any | None = None
            ssh: Any | None = None
            result: ImageGenerationProbeResult | None = None
            failure: str | None = None
            region = str(resource.get("region") or "") or None
            zone = str(resource.get("zone") or "") or None
            try:
                instance = await asyncio.to_thread(
                    self.manager.create_one_shot,
                    task_id="image-generation-probe",
                    gpu=int(resource.get("gpuCount") or 1),
                    cpu=int(resource.get("cpuCores") or 0) or None,
                    memory_gb=int(resource.get("memoryGb") or 0) or None,
                    gpu_type=str(resource.get("gpuType") or "") or None,
                    image_id=str(resource.get("imageId") or "") or None,
                    charge_type=str(resource.get("chargeType") or "") or None,
                    region=region,
                    zone=zone,
                    name="cppt-image-generation-probe",
                    idempotency_key=f"image-generation-probe:{resource.get('imageId') or 'default'}:{region or ''}:{zone or ''}:{resource.get('gpuType') or ''}",
                )
                credentials = self.manager.ssh_credentials(instance)
                if not credentials.get("host") or not credentials.get("password"):
                    raise RuntimeError("优云实例缺少可用 SSH 凭据。")
                ssh = self.ssh_factory()
                deadline = time.monotonic() + self.ssh_ready_timeout
                while True:
                    try:
                        await asyncio.to_thread(ssh.connect, str(credentials["host"]), int(credentials.get("port") or 22), credentials.get("user") or "root", credentials["password"])
                        break
                    except Exception as exc:
                        if time.monotonic() >= deadline:
                            raise RuntimeError(f"优云 SSH 在限定时间内未就绪：{type(exc).__name__}") from exc
                        try:
                            ssh.close()
                        except Exception:
                            pass
                        if self.ssh_retry_delay:
                            await asyncio.sleep(self.ssh_retry_delay)
                result = await asyncio.wait_for(self.probe(ssh, request), timeout=self.probe_timeout)
            except Exception as exc:
                failure = _safe_failure(exc)
            finally:
                if ssh is not None:
                    try:
                        ssh.close()
                    except Exception:
                        pass

            instance_id = str(getattr(instance, "uhost_id", "") or getattr(instance, "id", "") or "")
            cleanup = {"terminated": True, "error": None}
            if instance_id:
                try:
                    cleanup = await asyncio.to_thread(self.manager.cleanup_after_task, instance_id, stop_first=False, region=region, zone=zone)
                except Exception as exc:
                    cleanup = {"terminated": False, "error": _safe_failure(exc)}
            released = bool(cleanup.get("terminated"))
            self.unresolved_instance_id = None if released else instance_id or "unknown"
            payload = result.to_dict() if result is not None else {"passed": False, "checks": {}, "blockingReason": "probe_runtime_failed", "estimatedVramGb": 0, "logs": []}
            if failure:
                payload["logs"] = [*payload.get("logs", []), failure]
            if not released:
                payload["passed"] = False
            return {
                **payload,
                "remoteInstanceId": instance_id or None,
                "cleanupState": "released" if released else "failed",
                "cleanupError": _safe_failure(RuntimeError(str(cleanup.get("error") or ""))) if cleanup.get("error") else None,
                "retryBlocked": not released,
            }


__all__ = ["YouyunImageGenerationProbeRunner"]
