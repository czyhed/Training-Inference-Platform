"""Guarded one-shot Youyun execution for visual compatibility probes."""
from __future__ import annotations

import asyncio
import os
import socket
import time
from typing import Any, Awaitable, Callable, Mapping

from backend.providers.youyun.ssh import YouyunSSHClient
from backend.youyun_vision_probe import (
    VisionProbeRequest,
    VisionProbeResult,
    probe_remote_vision_environment,
)


class YouyunVisionProbeRunner:
    def __init__(
        self,
        manager: Any,
        *,
        ssh_factory: Callable[[], Any] = YouyunSSHClient,
        probe: Callable[[Any, VisionProbeRequest], Awaitable[VisionProbeResult]] = probe_remote_vision_environment,
        ssh_ready_timeout: float | None = None,
        ssh_retry_delay: float | None = None,
        probe_timeout: float | None = None,
    ) -> None:
        self.manager = manager
        self.ssh_factory = ssh_factory
        self.probe = probe
        self.ssh_ready_timeout = max(
            0.0,
            float(ssh_ready_timeout if ssh_ready_timeout is not None else os.environ.get("YOUYUN_SSH_READY_TIMEOUT_SECONDS", "300")),
        )
        self.ssh_retry_delay = max(
            0.0,
            float(ssh_retry_delay if ssh_retry_delay is not None else os.environ.get("YOUYUN_SSH_CONNECT_RETRY_DELAY_SECONDS", "5")),
        )
        self.probe_timeout = max(
            0.01,
            float(probe_timeout if probe_timeout is not None else os.environ.get("YOUYUN_VISION_PROBE_TIMEOUT_SECONDS", "1800")),
        )
        self._lock = asyncio.Lock()
        self.unresolved_instance_id: str | None = None

    async def run(
        self,
        request: VisionProbeRequest,
        resource: Mapping[str, Any],
    ) -> dict[str, Any]:
        if self.unresolved_instance_id:
            raise RuntimeError(
                f"优云实例 {self.unresolved_instance_id} 尚未释放，禁止创建新的视觉探针实例。"
            )
        if self._lock.locked():
            raise RuntimeError("已有优云视觉探针正在执行。")
        async with self._lock:
            instance: Any | None = None
            ssh: Any | None = None
            result: VisionProbeResult | None = None
            failure: str | None = None
            try:
                instance = await asyncio.to_thread(
                    self.manager.create_one_shot,
                    task_id="vision-probe",
                    gpu=int(resource.get("gpuCount") or 1),
                    cpu=int(resource.get("cpuCores") or 0) or None,
                    memory_gb=int(resource.get("memoryGb") or 0) or None,
                    gpu_type=str(resource.get("gpuType") or "") or None,
                    image_id=str(resource.get("imageId") or "") or None,
                    name="cppt-vision-probe",
                    idempotency_key=f"vision-probe:{resource.get('imageId') or 'default'}:{request.repo_id}",
                )
                credentials = self.manager.ssh_credentials(instance)
                if not credentials.get("host") or not credentials.get("password"):
                    raise RuntimeError("优云实例缺少可用 SSH 凭据。")
                ssh = self.ssh_factory()
                deadline = time.monotonic() + self.ssh_ready_timeout
                last_error: Exception | None = None
                while True:
                    get_instance = getattr(self.manager, "get", None)
                    if callable(get_instance):
                        try:
                            refreshed = get_instance(str(getattr(instance, "uhost_id", "") or getattr(instance, "id", "")))
                        except Exception:
                            refreshed = None
                        if refreshed is not None:
                            refreshed_credentials = self.manager.ssh_credentials(refreshed)
                            if refreshed_credentials.get("host") and refreshed_credentials.get("password"):
                                credentials = refreshed_credentials
                    try:
                        host = str(credentials["host"])
                        port = int(credentials.get("port") or 22)
                        if self.ssh_factory is YouyunSSHClient:
                            with socket.create_connection(
                                (host, port),
                                timeout=min(20.0, max(1.0, deadline - time.monotonic())),
                            ) as probe_socket:
                                probe_socket.settimeout(min(20.0, max(1.0, deadline - time.monotonic())))
                                banner = probe_socket.recv(128)
                                if not banner.startswith(b"SSH-"):
                                    raise OSError("SSH 端口已连接但未返回有效 banner")
                        await asyncio.to_thread(
                            ssh.connect,
                            host,
                            port,
                            credentials.get("user") or "root",
                            credentials["password"],
                        )
                        break
                    except Exception as exc:  # noqa: BLE001 - transient boot/SSH errors are retried
                        last_error = exc
                        if time.monotonic() >= deadline:
                            raise RuntimeError(
                                f"优云 SSH 连接超时（{credentials['host']}:{int(credentials.get('port') or 22)}）：{exc}"
                            ) from exc
                        try:
                            ssh.close()
                        except Exception:
                            pass
                        if self.ssh_retry_delay:
                            await asyncio.sleep(self.ssh_retry_delay)
                result = await asyncio.wait_for(self.probe(ssh, request), timeout=self.probe_timeout)
            except Exception as exc:  # noqa: BLE001 - returned as a bounded probe failure
                failure = str(exc) or type(exc).__name__
            finally:
                if ssh is not None:
                    try:
                        ssh.close()
                    except Exception:  # noqa: BLE001 - cleanup continues with provider release
                        pass

            # The real provider object exposes ``uhost_id``; ``id`` is kept for
            # lightweight test doubles and older manager adapters.
            instance_id = str(
                getattr(instance, "uhost_id", "")
                or getattr(instance, "id", "")
                or ""
            )
            cleanup = {"terminated": True, "error": None}
            if instance_id:
                cleanup = await asyncio.to_thread(
                    self.manager.cleanup_after_task,
                    instance_id,
                    stop_first=False,
                )
            released = bool(cleanup.get("terminated"))
            self.unresolved_instance_id = None if released else instance_id or "unknown"
            payload = result.to_dict() if result is not None else {
                "passed": False,
                "checks": {},
                "blockingReason": "probe_runtime_failed",
                "recommendedTuningMode": request.tuning_mode,
                "estimatedVramGb": 0,
                "logs": [],
            }
            if failure:
                payload["logs"] = [*payload.get("logs", []), failure]
            return {
                **payload,
                "remoteInstanceId": instance_id or None,
                "cleanupState": "released" if released else "failed",
                "cleanupError": cleanup.get("error"),
                "retryBlocked": not released,
            }


__all__ = ["YouyunVisionProbeRunner"]
