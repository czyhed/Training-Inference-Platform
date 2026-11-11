"""Service-scoped lifecycle for remote Diffusers workers.

The service owns the cloud instance. Generation tasks only borrow the persisted
handle and therefore cannot accidentally create a second billable instance.
"""
from __future__ import annotations

from backend.personal_youyun import scoped_resource

import asyncio
import os
import time
from typing import Any, Callable, Mapping


class ImageGenerationServiceRuntime:
    def __init__(self, store: Any, provider_factory: Callable[[], Any]) -> None:
        self.store = store
        self.provider_factory = provider_factory
        self.lock = asyncio.Lock()
        self._providers: dict[str, Any] = {}
        self._handles: dict[str, Any] = {}

    def _service(self, service_id: str) -> dict[str, Any]:
        service = self.store.get(service_id)
        if service is None:
            raise KeyError("文生图服务不存在。")
        if (service.get("config") or {}).get("workloadType") != "image_generation":
            raise ValueError("所选服务不是文生图服务。")
        return service

    @staticmethod
    def _task_config(service: Mapping[str, Any]) -> dict[str, Any]:
        config = dict(service.get("config") or {})
        return {
            "id": f"service-{service.get('id')}",
            "resource": dict(config.get("resource") or {}),
            "modelRepoId": config.get("modelRepoId") or config.get("modelId") or "",
            "adapterPath": config.get("adapterPath") or "",
            "requiresDiffusersInstall": bool((config.get("resource") or {}).get("requiresDiffusersInstall")),
        }

    @staticmethod
    def _now_iso() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S%z")

    def _set_startup(
        self,
        service_id: str,
        stage: str,
        progress: int,
        started_mono: float,
        message: str | None = None,
    ) -> None:
        """Persist best-effort startup progress into config.startup for the UI."""

        def mutate(config: dict[str, Any]) -> None:
            previous = config.get("startup") or {}
            config["startup"] = {
                "stage": stage,
                "progress": max(0, min(100, int(progress))),
                "startedAt": previous.get("startedAt") or self._now_iso(),
                "elapsedSeconds": round(max(0.0, time.monotonic() - started_mono), 1),
            }

        try:
            service = self.store.get(service_id) or {}
            config = dict(service.get("config") or {})
            mutate(config)
            self.store.update(service_id, config=config)
        except Exception:
            # Progress reporting must never break the start flow itself.
            pass
        if message:
            try:
                self.store.append_log(service_id, message)
            except Exception:
                pass

    @scoped_resource("service_id")
    async def start(self, service_id: str, *, preflight: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if self.lock.locked():
            raise RuntimeError("已有启动/停止操作正在进行，请等待其完成后再试。")
        async with self.lock:
            service = self._service(service_id)
            state = str(service.get("state") or "created")
            if state == "running" and (service.get("config") or {}).get("remoteRuntime"):
                return service
            if state in {"starting", "stopping"}:
                raise RuntimeError("服务已在启动/停止流程中，请等待当前流程完成或失败后再试。")
            started_mono = time.monotonic()
            self.store.update(service_id, state="starting", exposure="test", gpu="优云 GPU · 启动中", memory="加载中", error=None)
            self._set_startup(service_id, "starting", 5, started_mono, "开始启动文生图推理服务。")

            def report_progress(stage: str, progress: int, message: str | None = None) -> None:
                self._set_startup(service_id, stage, progress, started_mono, message)

            try:
                provider = self.provider_factory()
                result = await provider.provision_service(service_id, self._task_config(service), report_progress=report_progress)
                handle = result["handle"] if isinstance(result, Mapping) else result
                self._providers[service_id] = provider
                self._handles[service_id] = handle
                self._set_startup(service_id, "ready", 100, started_mono)
                config = dict((self.store.get(service_id) or {}).get("config") or {})
                config["remoteRuntime"] = {**handle.persisted(), "state": "running", "startedAt": self._now_iso()}
                if preflight:
                    config["preflightSnapshot"] = dict(preflight)
                self.store.update(service_id, config=config, state="running", exposure="test", gpu="优云 GPU · 运行中", memory="已加载", error=None)
                self.store.append_log(service_id, f"文生图推理服务已就绪：{handle.instance_id}。")
                return self.store.get(service_id)  # type: ignore[return-value]
            except Exception as exc:
                self.store.update(service_id, state="failed", exposure="offline", gpu="未占用", memory="0 / 0 GB", error=str(exc))
                self.store.append_log(service_id, f"文生图推理服务启动失败：{exc}")
                raise

    @scoped_resource("service_id")
    async def stop(self, service_id: str) -> dict[str, Any]:
        if self.lock.locked():
            raise RuntimeError("已有启动/停止操作正在进行，请等待其完成后再试。")
        async with self.lock:
            service = self._service(service_id)
            config = dict(service.get("config") or {})
            remote = config.get("remoteRuntime")
            if not remote:
                config.pop("startup", None)
                self.store.update(service_id, config=config, state="stopped", exposure="offline", gpu="未占用", memory="0 / 0 GB", error=None)
                return self.store.get(service_id)  # type: ignore[return-value]
            self.store.update(service_id, state="stopping", gpu="优云实例释放中", memory="释放中")
            provider = self._providers.pop(service_id, None) or self.provider_factory()
            handle = self._handles.pop(service_id, None)
            report = await (provider.stop(handle) if handle is not None else provider.stop_persisted(dict(remote)))
            if not report.get("terminated"):
                self.store.update(service_id, state="failed", error=str(report.get("error") or "优云实例释放失败"), gpu="优云实例待清理", memory="清理失败")
                self.store.append_log(service_id, f"文生图实例释放失败：{report.get('error')}")
                raise RuntimeError(str(report.get("error") or "优云实例释放失败"))
            config.pop("remoteRuntime", None)
            config.pop("startup", None)
            self.store.update(service_id, config=config, state="stopped", exposure="offline", gpu="未占用", memory="0 / 0 GB", error=None)
            self.store.append_log(service_id, "文生图推理服务已停止，优云实例已释放。")
            return self.store.get(service_id)  # type: ignore[return-value]

    def handle(self, service_id: str) -> dict[str, Any] | None:
        service = self._service(service_id)
        runtime = (service.get("config") or {}).get("remoteRuntime")
        return dict(runtime) if isinstance(runtime, Mapping) else None

    async def startup(self) -> None:
        """Reset services whose start/stop process died with a backend restart.

        Handles with a persisted instance marker are intentionally kept so the
        next stop can still release the real Youyun instance.
        """
        try:
            services = self.store.list(include_archived=False)
        except Exception:
            return
        for service in services:
            try:
                if not isinstance(service, Mapping) or (service.get("config") or {}).get("workloadType") != "image_generation":
                    continue
                service_id = str(service.get("id") or "")
                if not service_id:
                    continue
                state = str(service.get("state") or "")
                config = dict(service.get("config") or {})
                remote = config.get("remoteRuntime")
                if state == "starting" and not remote:
                    self.store.update(
                        service_id,
                        state="failed",
                        exposure="offline",
                        gpu="未占用",
                        memory="0 / 0 GB",
                        error="文生图服务启动进程在后端重启时中断，请重新启动服务。",
                    )
                    self.store.append_log(service_id, "后端重启时启动流程中断，服务已标记为失败，可重新启动。")
                elif state == "recovering" and not remote:
                    self.store.update(
                        service_id,
                        state="failed",
                        exposure="offline",
                        gpu="未占用",
                        memory="0 / 0 GB",
                        error="文生图服务恢复进程在后端重启时中断，请重新启动服务。",
                    )
                    self.store.append_log(service_id, "后端重启时恢复流程中断，服务已标记为失败，可重新启动。")
                elif state == "stopping" and not remote:
                    self.store.update(service_id, state="stopped", exposure="offline", gpu="未占用", memory="0 / 0 GB", error=None)
                    self.store.append_log(service_id, "后端重启时停止流程中断，实例句柄已不存在，服务已复位为已停止。")
                elif state == "stopping" and remote:
                    self.store.update(
                        service_id,
                        state="failed",
                        exposure="offline",
                        gpu="优云实例待清理",
                        memory="清理失败",
                        error="服务停止进程在后端重启时中断，请再次停止服务以释放优云实例。",
                    )
                    self.store.append_log(service_id, "后端重启时停止流程中断，保留实例句柄等待下次停止释放。")
                elif state == "running":
                    # 后端进程死亡时服务仍在运行：管理会话已随进程消失，
                    # 空闲回收逻辑不再生效，优云实例处于无人管理状态。
                    # 立即走停止流程释放实例，防止持续计费泄漏。
                    self.store.append_log(service_id, "后端重启时服务仍在运行，正在对账并释放优云实例。")
                    try:
                        await asyncio.wait_for(self.stop(service_id), timeout=self._reconcile_timeout())
                    except Exception:
                        # stop() 失败时已写入 failed 态并保留实例句柄；
                        # 超时/异常时句柄仍持久化，等待手动停止或下次对账。
                        continue
            except Exception:
                continue

    @staticmethod
    def _reconcile_timeout() -> float:
        try:
            return max(5.0, float(os.environ.get("XUNTUI_INFERENCE_RECONCILE_TIMEOUT_SECONDS", "60")))
        except ValueError:
            return 60.0

    async def shutdown(self) -> None:
        for service in self.store.list(include_archived=False):
            if (service.get("config") or {}).get("workloadType") != "image_generation":
                continue
            if (service.get("config") or {}).get("remoteRuntime"):
                service_id = str(service.get("id") or "")
                try:
                    await asyncio.wait_for(self.stop(service_id), timeout=self._reconcile_timeout())
                except Exception:
                    # 关闭路径不抛出；句柄保持持久化，由下次启动对账兜底。
                    self.store.append_log(service_id, "后端关闭时文生图实例释放超时或失败，句柄已保留，将在下次启动对账时释放。")
