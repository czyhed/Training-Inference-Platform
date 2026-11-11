from __future__ import annotations

from backend.personal_youyun import bind_resource, resource_scope, scoped_resource, background_worker

"""Persistent queue and lifecycle for Diffusers image-generation tasks."""

import asyncio
import os
import inspect
import json
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from backend.resource_lifecycle import ResourceLeaseManager


GENERATION_FAILURE_MESSAGE = "远端文生图失败，请检查任务日志。"
RUNTIME_INTERRUPTED_MESSAGE = "文生图任务在后端重启时中断，请重试。"
ANSI_ESCAPE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _clean_runtime_text(value: Any) -> str:
    return ANSI_ESCAPE.sub("", str(value or "")).replace("\r", "").strip()


def _log_payload(value: str) -> str:
    prefix, separator, payload = value.partition("] ")
    return payload if separator and prefix.startswith("[") else value


def _normalize_logs(values: Iterable[Any]) -> list[str]:
    logs: list[str] = []
    for value in values:
        clean = _clean_runtime_text(value)
        if not clean or (logs and _log_payload(logs[-1]) == _log_payload(clean)):
            continue
        logs.append(clean[:8012])
    return logs[-5000:]


class ImageGenerationRuntime:
    ACTIVE_STATES = {"preparing", "running", "collecting", "stopping"}

    def __init__(
        self,
        *,
        task_root: Path,
        execution_lock: asyncio.Lock,
        provider_factory: Callable[["ImageGenerationRuntime"], Any],
        busy_check: Callable[[], bool] | None = None,
        service_handle_lookup: Callable[[str], Mapping[str, Any] | None] | None = None,
    ) -> None:
        self.task_root = task_root.expanduser().resolve()
        self.execution_lock = execution_lock
        self.provider_factory = provider_factory
        self.busy_check = busy_check
        self.service_handle_lookup = service_handle_lookup
        self._queue: asyncio.Queue[str] | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._active_task_id: str | None = None
        self._active_provider: Any | None = None
        self._active_handle: Any | None = None
        self._enqueued: set[str] = set()
        self._lock = threading.RLock()
        self.lease_manager = ResourceLeaseManager()

    @property
    def active_task_id(self) -> str | None:
        return self._active_task_id

    @property
    def worker_state(self) -> str:
        if self._worker_task is None:
            return "stopped"
        return "busy" if self._active_task_id else "idle"

    def _task_path(self, task_id: str) -> Path:
        if not task_id or Path(task_id).name != task_id or task_id in {".", ".."}:
            raise ValueError("文生图任务 ID 不合法。")
        return self.task_root / f"{task_id}.json"

    def _read_path(self, path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _write_path(self, path: Path, value: Mapping[str, Any]) -> None:
        self.task_root.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _normalize_task(task: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(task)
        state = str(value.get("state") or "")
        value["logs"] = _normalize_logs(value.get("logs") or [])
        remote = bool(value.get("remoteHandle") or value.get("instanceId") or value.get("provider") == "youyun")
        if not remote and state in {"awaiting_confirmation", "draft"}:
            value.pop("cleanupState", None)
            value.pop("cleanupCheckedAt", None)
            value.pop("resourceState", None)
            return value
        cleanup = str(value.get("cleanupState") or "").strip() or ("pending" if remote else "released")
        value["cleanupState"] = cleanup
        value.setdefault("cleanupCheckedAt", None)
        if cleanup in {"released", "failed"} and not value.get("cleanupCheckedAt"):
            value["cleanupCheckedAt"] = value.get("updatedAt")
        if state in {"completed", "failed", "stopped"}:
            if str(value.get("phase") or "") != state:
                value["phase"] = state
                value["phaseUpdatedAt"] = value.get("updatedAt") or value.get("phaseUpdatedAt")
        if cleanup == "failed":
            value["resourceState"] = "cleanup_failed"
        elif cleanup == "released" and remote:
            value["resourceState"] = "released"
        elif not value.get("resourceState"):
            if not remote:
                value["resourceState"] = "none"
            elif state in {"queued", "preparing", "provisioning"}:
                value["resourceState"] = "provisioning"
            elif state == "stopping":
                value["resourceState"] = "stopping"
            else:
                value["resourceState"] = "running"
        return value

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._read_path(self._task_path(task_id))
            return self._normalize_task(value) if value is not None else None

    def list_tasks(
        self,
        *,
        service_id: str | None = None,
        state: str | None = None,
        query: str | None = None,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        service_filter = str(service_id or "").strip()
        state_filter = str(state or "").strip()
        query_filter = str(query or "").strip().casefold()
        with self._lock:
            tasks = [
                task
                for path in self.task_root.glob("*.json")
                if (task := self._read_path(path)) is not None
                and (include_archived or not bool(task.get("archived")))
                and (not service_filter or str(task.get("serviceId") or "") == service_filter)
                and (not state_filter or str(task.get("state") or "") == state_filter)
                and (
                    not query_filter
                    or query_filter in str(task.get("id") or "").casefold()
                    or query_filter in str(task.get("name") or "").casefold()
                    or query_filter in str(task.get("prompt") or "").casefold()
                )
            ]
        return sorted((self._normalize_task(item) for item in tasks), key=lambda item: str(item.get("createdAt", "")), reverse=True)

    def save(self, task: Mapping[str, Any]) -> dict[str, Any]:
        value = self._normalize_task(task)
        if self.get(str(value["id"])) is None:
            bind_resource(str(value["id"]), remote=True, source_resource_id=value.get("serviceId"))
        with self._lock:
            self._write_path(self._task_path(str(value["id"])), value)
        return value

    def create_task(self, task: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(task)
        task_id = str(value.get("id") or "")
        self._task_path(task_id)
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        value.setdefault("state", "awaiting_confirmation")
        value.setdefault("status", "draft")
        value.setdefault("progress", 0)
        value.setdefault("currentStep", "等待提交到已运行的文生图服务")
        value.setdefault("logs", [])
        value.setdefault("results", [])
        value.setdefault("archived", False)
        value.setdefault("archivedAt", None)
        value.setdefault("createdAt", now)
        value.setdefault("updatedAt", now)
        if value.get("provider") == "youyun" or value.get("remoteHandle") or value.get("instanceId"):
            value.setdefault("phaseTimings", {})
            value.setdefault("lastHeartbeatAt", now)
            resource = value.get("resource") if isinstance(value.get("resource"), Mapping) else {}
            if resource.get("price") is not None:
                value.setdefault("priceSnapshot", resource.get("price"))
            value.setdefault("expiresAtEpoch", time.time() + float(value.get("ttlSeconds") or 3600))
        return self.save(value)

    def clone_task(self, task_id: str, overrides: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
        source = self.get(task_id)
        if source is None:
            return None
        allowed_fields = (
            "serviceId",
            "serviceName",
            "provider",
            "workloadType",
            "modelId",
            "modelRepoId",
            "modelType",
            "adapterLineage",
            "adapterPath",
            "trainingMode",
            "precision",
            "enableCpuOffload",
            "enableAttentionSlicing",
            "enableVaeTiling",
            "enableSafetyChecker",
            "loadTimeout",
            "resource",
            "prompt",
            "negativePrompt",
            "width",
            "height",
            "steps",
            "cfg",
            "batchSize",
            "seed",
            "sampler",
        )
        value = {key: source[key] for key in allowed_fields if key in source}
        supplied = dict(overrides or {})
        for key in ("prompt", "negativePrompt", "width", "height", "steps", "cfg", "batchSize", "seed", "sampler"):
            if key in supplied:
                value[key] = supplied[key]
        value.update(
            {
                "id": f"igt-{time.strftime('%y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}",
                "name": f"{source.get('name') or '文生图任务'} · 副本",
                "state": "awaiting_confirmation",
                "status": "draft",
                "progress": 0,
                "currentStep": "等待提交到已运行的文生图服务",
                "message": "服务实例由服务启动按钮创建，任务提交不会创建新实例。",
                "results": [],
                "logs": [],
                "archived": False,
                "archivedAt": None,
            }
        )
        return self.create_task(value)

    def archive_task(self, task_id: str) -> dict[str, Any] | None:
        task = self.get(task_id)
        if task is None:
            return None
        if bool(task.get("archived")):
            raise ValueError("文生图任务已经归档。")
        if str(task.get("state") or "") not in {"completed", "failed", "stopped"}:
            raise ValueError("只有已完成、失败或已停止的文生图任务可以归档。")
        return self.update(task_id, archived=True, archivedAt=time.strftime("%Y-%m-%dT%H:%M:%S%z"))

    def restore_task(self, task_id: str) -> dict[str, Any] | None:
        task = self.get(task_id)
        if task is None:
            return None
        if not bool(task.get("archived")):
            raise ValueError("文生图任务尚未归档。")
        task["archived"] = False
        task["archivedAt"] = None
        return self.save(task)

    def delete_task(self, task_id: str) -> bool:
        task = self.get(task_id)
        if task is None:
            return False
        state = str(task.get("state") or "")
        if state in self.ACTIVE_STATES or state == "queued":
            raise ValueError("运行中的文生图任务不能删除。")
        result_root = str(task.get("resultRoot") or "").strip()
        if result_root:
            result_path = Path(result_root).expanduser().resolve()
            if result_path.exists():
                shutil.rmtree(result_path, ignore_errors=False)
        self._task_path(task_id).unlink(missing_ok=True)
        return True

    def update(self, task_id: str, *, clear: Iterable[str] = (), **changes: Any) -> dict[str, Any] | None:
        with self._lock:
            task = self._read_path(self._task_path(task_id))
            if task is None:
                return None
            task = self._normalize_task(task)
            task.update({key: value for key, value in changes.items() if value is not None})
            for key in clear:
                task.pop(key, None)
            task["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            task = self._normalize_task(task)
            self._write_path(self._task_path(task_id), task)
            return task

    def update_telemetry(
        self,
        task_id: str,
        *,
        phase: str,
        message: str,
        progress: int | None = None,
        heartbeat: bool = True,
        failure_code: str | None = None,
        failure_message: str | None = None,
        suggested_action: str | None = None,
    ) -> dict[str, Any] | None:
        """Persist safe runtime progress and optional classified failure details."""
        task = self.get(task_id)
        if task is None:
            return None
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        changes: dict[str, Any] = {}
        normalized_phase = str(phase or "").strip()
        if normalized_phase and normalized_phase != str(task.get("phase") or ""):
            changes["phase"] = normalized_phase
            changes["phaseUpdatedAt"] = now
        elif normalized_phase and not task.get("phaseUpdatedAt"):
            changes["phase"] = normalized_phase
            changes["phaseUpdatedAt"] = now
        if heartbeat:
            changes["lastHeartbeatAt"] = now
        if normalized_phase:
            timings = dict(task.get("phaseTimings") or {})
            if not isinstance(timings.get(normalized_phase), Mapping):
                timings[normalized_phase] = {"startedAt": now}
            changes["phaseTimings"] = timings
        clean_message = _clean_runtime_text(message)
        if clean_message:
            changes["currentStep"] = clean_message
        if progress is not None:
            changes["progress"] = max(0, min(99, int(progress)))
        if failure_code is not None:
            changes["failureCode"] = str(failure_code)
        if failure_message is not None:
            changes["failureMessage"] = str(failure_message)
        if suggested_action is not None:
            changes["suggestedAction"] = str(suggested_action)
        return self.update(task_id, **changes)

    def append_log(self, task_id: str, message: str) -> None:
        clean = _clean_runtime_text(message)
        if not clean:
            return
        with self._lock:
            task = self._read_path(self._task_path(task_id))
            if task is None:
                return
            logs = _normalize_logs(task.get("logs") or [])
            if logs and _log_payload(logs[-1]) == clean:
                return
            logs.append(f"[{time.strftime('%H:%M:%S')}] {clean[:8000]}")
            task["logs"] = logs[-5000:]
            task["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            self._write_path(self._task_path(task_id), task)

    def enqueue(self, task_id: str) -> None:
        if self._queue is None:
            self._queue = asyncio.Queue()
        if self._worker_task is None:
            self._worker_task = asyncio.create_task(self._worker_loop(), name="image-generation-worker")
        if task_id not in self._enqueued:
            self._enqueued.add(task_id)
            self._queue.put_nowait(task_id)

    async def start(self) -> None:
        if self._worker_task is not None:
            return
        self.task_root.mkdir(parents=True, exist_ok=True)
        self._queue = asyncio.Queue()
        for task in self.list_tasks():
            task_id = str(task.get("id") or "")
            state = str(task.get("state") or "")
            if state in self.ACTIVE_STATES:
                cleanup_state: str | None = None
                cleanup_error: str | None = None
                remote_handle = task.get("remoteHandle")
                if isinstance(remote_handle, Mapping):
                    try:
                        with resource_scope(task_id):
                            provider = self.provider_factory(self)
                        cleanup = getattr(provider, "cleanup_persisted", None)
                        if callable(cleanup):
                            report = cleanup(dict(remote_handle))
                            if inspect.isawaitable(report):
                                report = await report
                            if isinstance(report, Mapping) and report.get("terminated"):
                                cleanup_state = "released"
                            else:
                                cleanup_state = "failed"
                                cleanup_error = str((report or {}).get("error") or "优云实例仍未释放。") if isinstance(report, Mapping) else "优云实例仍未释放。"
                        else:
                            cleanup_state = "failed"
                            cleanup_error = "当前执行器不支持重试优云实例清理。"
                    except Exception as exc:
                        cleanup_state = "failed"
                        cleanup_error = str(exc)[:8000]
                self.update(task_id, state="failed", status="failed", currentStep="后端重启，文生图任务未完成", error="文生图任务在后端重启时中断，请重试。")
                if cleanup_state:
                    if cleanup_state == "released":
                        self.update(task_id, cleanupState=cleanup_state, clear=("cleanupError", "remoteHandle"))
                        self.append_log(task_id, "后端重启检测到遗留优云实例，已完成释放。")
                    else:
                        self.update(task_id, cleanupState=cleanup_state, cleanupError=cleanup_error)
                        self.append_log(task_id, f"后端重启后的优云实例清理失败：{cleanup_error}")
                self.update_telemetry(
                    task_id,
                    phase="recovery",
                    message="后端重启，文生图任务未完成",
                    heartbeat=False,
                    failure_code="runtime_interrupted",
                    failure_message=RUNTIME_INTERRUPTED_MESSAGE,
                    suggested_action="确认优云实例状态后重试任务。",
                )
            elif state == "queued":
                self._enqueued.add(task_id)
                self._queue.put_nowait(task_id)
        self._worker_task = asyncio.create_task(self._worker_loop(), name="image-generation-worker")

    async def stop(self) -> None:
        if self._active_task_id:
            self.update(self._active_task_id, cancelRequested=True, state="stopping", status="stopping")
            if self._active_provider is not None and self._active_handle is not None:
                stop = getattr(self._active_provider, "stop", None)
                if callable(stop):
                    try:
                        await stop(self._active_handle)
                    except Exception:
                        pass
        worker = self._worker_task
        self._worker_task = None
        if worker is not None:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
        self._active_task_id = None
        self._active_provider = None
        self._active_handle = None

    async def confirm(self, task_id: str) -> dict[str, Any] | None:
        task = self.get(task_id)
        if task is None:
            return None
        if task.get("state") != "awaiting_confirmation":
            raise ValueError("只有待确认的文生图任务可以确认。")
        updated = self.update(task_id, state="queued", status="queued", currentStep="等待优云任务执行", progress=0, cancelRequested=False, clear=("error", "stopReason"))
        self.enqueue(task_id)
        return updated

    async def submit(self, task_id: str) -> dict[str, Any] | None:
        """Submit a saved task to an already running service."""
        task = self.get(task_id)
        if task is None:
            return None
        if task.get("state") not in {"awaiting_confirmation", "queued"}:
            raise ValueError("只有待提交或排队中的文生图任务可以提交。")
        updated = self.update(task_id, state="queued", status="queued", currentStep="已提交到文生图推理服务", progress=0, cancelRequested=False, clear=("error", "stopReason"))
        self.enqueue(task_id)
        return updated

    async def request_stop(self, task_id: str) -> dict[str, Any] | None:
        task = self.get(task_id)
        if task is None:
            return None
        state = str(task.get("state") or "")
        if state in {"awaiting_confirmation", "queued"}:
            return self.update(task_id, state="stopped", status="stopped", currentStep="由用户停止", cancelRequested=False, stopReason="manual")
        if state in self.ACTIVE_STATES:
            self.update(task_id, state="stopping", status="stopping", cancelRequested=True, currentStep="正在停止文生图任务")
            if task_id == self._active_task_id and self._active_provider is not None and self._active_handle is not None:
                stop = getattr(self._active_provider, "stop", None)
                if callable(stop):
                    await stop(self._active_handle)
            return self.get(task_id)
        if str(task.get("cleanupState") or "") == "failed":
            remote_handle = task.get("remoteHandle")
            if not isinstance(remote_handle, Mapping):
                raise ValueError("优云实例清理失败，但没有可重试的实例句柄。")
            with resource_scope(task_id):
                provider = self.provider_factory(self)
            cleanup = getattr(provider, "cleanup_persisted", None)
            if not callable(cleanup):
                raise ValueError("当前执行器不支持重试优云实例清理。")
            report = await cleanup(dict(remote_handle))
            if not isinstance(report, Mapping) or not report.get("terminated"):
                error = str((report or {}).get("error") or "优云实例仍未释放。") if isinstance(report, Mapping) else "优云实例仍未释放。"
                current = self.get(task_id) or {}
                self.update(task_id, cleanupState="failed", cleanupError=error, currentStep="优云实例清理失败，请重试清理。")
                self.update_telemetry(
                    task_id,
                    phase=str(current.get("phase") or "cleanup"),
                    message="优云实例清理失败，请重试清理。",
                    heartbeat=False,
                    failure_code=None if current.get("failureCode") else "cleanup_failed",
                    failure_message=None if current.get("failureCode") else error,
                    suggested_action=None if current.get("failureCode") else "重试清理优云实例。",
                )
                return self.get(task_id)
            current = self.get(task_id) or {}
            clear = ["cleanupError", "remoteHandle"]
            if current.get("failureCode") == "cleanup_failed":
                clear.extend(("failureCode", "failureMessage", "suggestedAction"))
            return self.update(task_id, cleanupState="released", clear=clear, currentStep="优云实例已释放。")
        return task

    async def retry(self, task_id: str) -> dict[str, Any] | None:
        task = self.get(task_id)
        if task is None:
            return None
        if task.get("state") not in {"failed", "stopped"}:
            raise ValueError("只有失败或已停止的文生图任务可以重新运行。")
        if str(task.get("cleanupState") or "") == "failed":
            raise ValueError("优云实例清理失败，请先重试清理实例后再重新运行。")
        task.update({"state": "queued", "status": "queued", "progress": 0, "currentStep": "等待优云任务执行", "cancelRequested": False, "results": [], "logs": ["任务已重新加入优云文生图队列。"]})
        for key in (
            "error",
            "stopReason",
            "instanceId",
            "cleanupState",
            "cleanupError",
            "failureCode",
            "failureMessage",
            "suggestedAction",
            "phase",
            "phaseUpdatedAt",
            "lastHeartbeatAt",
        ):
            task.pop(key, None)
        saved = self.save(task)
        self.enqueue(task_id)
        return saved

    @background_worker
    async def _worker_loop(self) -> None:
        assert self._queue is not None
        while True:
            task_id = await self._queue.get()
            self._enqueued.discard(task_id)
            try:
                task = self.get(task_id)
                if task is None or task.get("state") != "queued":
                    continue
                async with self.execution_lock:
                    _busy_wait_started = time.monotonic()
                    _busy_wait_cap = max(60.0, float(os.environ.get("XUNTUI_RESOURCE_WAIT_TIMEOUT_SECONDS", "600")))
                    while self.busy_check is not None and self.busy_check():
                        if time.monotonic() - _busy_wait_started > _busy_wait_cap:
                            self.update(task_id, state="failed", status="failed", currentStep="等待共享资源超时。", error="共享执行资源长时间被占用，请检查是否有启动或训练流程挂起。")
                            self.append_log(task_id, "等待共享执行资源超时，任务转失败。")
                            raise RuntimeError("等待共享执行资源超时，任务转失败。")
                        await asyncio.sleep(0.2)
                    self._active_task_id = task_id
                    with resource_scope(task_id):
                        self._active_provider = self.provider_factory(self)
                    try:
                        if self.service_handle_lookup is not None:
                            service_handle = self.service_handle_lookup(str(task.get("serviceId") or ""))
                            if service_handle:
                                task = {**task, "serviceHandle": dict(service_handle)}
                        self.update(task_id, state="preparing", status="preparing", currentStep="正在准备优云文生图", progress=0)
                        self.update_telemetry(task_id, phase="preparing", message="正在准备优云文生图", progress=0)
                        outcome = await self._run_provider(task_id, task, self._active_provider)
                        current = self.get(task_id) or {}
                        if current.get("cancelRequested") or outcome.get("status") == "stopped":
                            self.update(task_id, state="stopped", status="stopped", currentStep="文生图任务已停止", cancelRequested=False, stopReason="manual")
                            if outcome.get("failureCode"):
                                self.update_telemetry(
                                    task_id,
                                    phase="stopped",
                                    message=str(outcome.get("failureMessage") or "文生图任务已停止"),
                                    heartbeat=False,
                                    failure_code=str(outcome.get("failureCode")),
                                    failure_message=outcome.get("failureMessage"),
                                    suggested_action=outcome.get("suggestedAction"),
                                )
                        elif outcome.get("status") == "completed":
                            cleanup_state = outcome.get("cleanupState", "released")
                            completed_message = "图片生成完成，但优云实例清理失败" if cleanup_state == "failed" else "图片生成完成，实例已释放"
                            self.update(task_id, state="completed", status="completed", currentStep=completed_message, progress=100, cancelRequested=False, results=outcome.get("results") or [], cleanupState=cleanup_state, cleanupError=outcome.get("cleanupError"), cleanupCheckedAt=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
                            self.update_telemetry(
                                task_id,
                                phase="completed",
                                message=completed_message,
                                heartbeat=False,
                                failure_code=(outcome.get("failureCode") or "cleanup_failed") if cleanup_state == "failed" or outcome.get("failureCode") else None,
                                failure_message=(outcome.get("failureMessage") or outcome.get("cleanupError")) if cleanup_state == "failed" or outcome.get("failureCode") else None,
                                suggested_action=(outcome.get("suggestedAction") or "重试清理优云实例。") if cleanup_state == "failed" or outcome.get("failureCode") else None,
                            )
                        else:
                            safe_failure_message = str(outcome.get("failureMessage") or GENERATION_FAILURE_MESSAGE)
                            self.update(task_id, state="failed", status="failed", currentStep="文生图任务失败", error=str(outcome.get("error") or GENERATION_FAILURE_MESSAGE), cleanupState=outcome.get("cleanupState"), cleanupError=outcome.get("cleanupError"))
                            self.update_telemetry(
                                task_id,
                                phase="failed",
                                message=safe_failure_message,
                                heartbeat=False,
                                failure_code=outcome.get("failureCode") or "generation_failed",
                                failure_message=safe_failure_message,
                                suggested_action=outcome.get("suggestedAction") or "检查任务日志后重试。",
                            )
                    finally:
                        self._active_task_id = None
                        self._active_provider = None
                        self._active_handle = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.append_log(task_id, "文生图执行器发生未分类错误。")
                self.update(task_id, state="failed", status="failed", currentStep="文生图任务失败", error=str(exc))
                self.update_telemetry(
                    task_id,
                    phase="failed",
                    message="文生图任务失败",
                    heartbeat=False,
                    failure_code="generation_failed",
                    failure_message=GENERATION_FAILURE_MESSAGE,
                    suggested_action="检查任务日志后重试。",
                )
            finally:
                self._queue.task_done()

    async def _run_provider(self, task_id: str, task: Mapping[str, Any], provider: Any) -> dict[str, Any]:
        def on_stage(stage: str, message: str, details: Mapping[str, Any] | None = None) -> None:
            payload = dict(details or {})
            handle = payload.pop("handle", None)
            if handle is not None:
                self._active_handle = handle
                persisted = getattr(handle, "persisted", None)
                if callable(persisted):
                    remote_handle = dict(persisted())
                    self.update(task_id, remoteHandle=remote_handle)
                    lease = self.lease_manager.acquire(owner_id=task_id, provider="youyun", instance_id=remote_handle.get("instanceId"))
                    self.lease_manager.mark_running(lease.lease_id)
            state = payload.pop("state", None)
            progress = payload.pop("progress", None)
            if state is None:
                state = "running" if stage not in {"creating_instance", "checking_remote_model", "downloading_model"} else "preparing"
            changes: dict[str, Any] = {"state": state, "status": state, "currentStep": message, **payload}
            if progress is not None:
                changes["progress"] = max(0, min(99, int(progress)))
            self.update(task_id, **changes)
            self.update_telemetry(task_id, phase=stage, message=message, progress=progress)
            self.append_log(task_id, message)

        def on_log(message: str) -> None:
            current = self.get(task_id) or {}
            self.update_telemetry(
                task_id,
                phase=str(current.get("phase") or current.get("state") or "running"),
                message=str(message or ""),
            )
            self.append_log(task_id, message)

        outcome = await provider.start(task_id, dict(task), on_stage=on_stage, on_log=on_log)
        return dict(outcome or {})


__all__ = ["ImageGenerationRuntime"]
