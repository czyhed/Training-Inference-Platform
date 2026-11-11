from __future__ import annotations

from backend.personal_youyun import bind_resource, resource_scope, scoped_resource, background_worker

"""Persistent queue and lifecycle for service-scoped evaluation tasks."""

import asyncio
import os
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .image_generation_evaluation_executor import ImageGenerationEvaluationExecutor


class ImageGenerationEvaluationRuntime:
    ACTIVE_STATES = {"preparing", "running", "collecting", "stopping"}

    def __init__(self, *, task_root: Path, execution_lock: asyncio.Lock, executor_factory: Callable[["ImageGenerationEvaluationRuntime"], ImageGenerationEvaluationExecutor], busy_check: Callable[[], bool] | None = None) -> None:
        self.task_root = task_root.expanduser().resolve()
        self.execution_lock = execution_lock
        self.executor_factory = executor_factory
        self.busy_check = busy_check
        self._queue: asyncio.Queue[str] | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._active_task_id: str | None = None
        self._active_executor: ImageGenerationEvaluationExecutor | None = None
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._enqueued: set[str] = set()
        self._lock = threading.RLock()

    @property
    def active_task_id(self) -> str | None:
        return self._active_task_id

    def _path(self, task_id: str) -> Path:
        if not task_id or Path(task_id).name != task_id:
            raise ValueError("评测任务 ID 不合法。")
        return self.task_root / f"{task_id}.json"

    def _read(self, path: Path) -> dict[str, Any] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _write(self, path: Path, value: Mapping[str, Any]) -> None:
        self.task_root.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._read(self._path(task_id))

    def list_tasks(self, *, service_id: str | None = None, include_archived: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            values = [task for path in self.task_root.glob("*.json") if (task := self._read(path)) is not None and (include_archived or not task.get("archived")) and (not service_id or str(task.get("serviceId")) == str(service_id))]
        return sorted(values, key=lambda item: str(item.get("createdAt") or ""), reverse=True)

    def save(self, task: Mapping[str, Any]) -> dict[str, Any]:
        if self.get(str(task["id"])) is None:
            bind_resource(str(task["id"]), remote=True, source_resource_id=task.get("serviceId"))
        value = dict(task)
        with self._lock:
            self._write(self._path(str(value["id"])), value)
        return value

    def create_task(self, task: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(task)
        value.setdefault("id", f"ige-{time.strftime('%y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}")
        value.setdefault("state", "awaiting_confirmation")
        value.setdefault("status", "draft")
        value.setdefault("phase", "awaiting_confirmation")
        value.setdefault("progress", 0)
        value.setdefault("logs", [])
        value.setdefault("results", [])
        value.setdefault("archived", False)
        value.setdefault("createdAt", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        value.setdefault("updatedAt", value["createdAt"])
        return self.save(value)

    def update(self, task_id: str, *, clear: Iterable[str] = (), **changes: Any) -> dict[str, Any] | None:
        with self._lock:
            task = self._read(self._path(task_id))
            if task is None:
                return None
            task.update({key: value for key, value in changes.items() if value is not None})
            for key in clear:
                task.pop(key, None)
            task["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            self._write(self._path(task_id), task)
            return task

    def append_log(self, task_id: str, message: str) -> None:
        task = self.get(task_id)
        if task is None:
            return
        logs = list(task.get("logs") or [])
        logs.append(f"[{time.strftime('%H:%M:%S')}] {str(message)[:2000]}")
        self.update(task_id, logs=logs[-2000:])

    async def start(self) -> None:
        if self._worker_task is not None:
            return
        self.task_root.mkdir(parents=True, exist_ok=True)
        self._queue = asyncio.Queue()
        for task in self.list_tasks(include_archived=True):
            if task.get("state") in self.ACTIVE_STATES:
                self.update(task["id"], state="failed", status="failed", phase="recovery", currentStep="后端重启，评测任务未完成", failureMessage="文生图评测任务在后端重启时中断，请重试。")
            elif task.get("state") == "queued":
                self._queue.put_nowait(str(task["id"]))
                self._enqueued.add(str(task["id"]))
        self._worker_task = asyncio.create_task(self._worker_loop(), name="image-generation-evaluation-worker")

    async def stop(self) -> None:
        if self._active_task_id:
            event = self._cancel_events.get(self._active_task_id)
            if event:
                event.set()
        worker = self._worker_task
        self._worker_task = None
        if worker:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
        self._active_task_id = None
        self._active_executor = None

    async def confirm(self, task_id: str) -> dict[str, Any] | None:
        task = self.get(task_id)
        if task is None:
            return None
        if task.get("state") != "awaiting_confirmation":
            raise ValueError("只有待确认的文生图评测任务可以确认。")
        updated = self.update(task_id, state="queued", status="queued", phase="queued", currentStep="等待评测任务执行", progress=0)
        if self._queue is None:
            self._queue = asyncio.Queue()
        if self._worker_task is None:
            self._worker_task = asyncio.create_task(self._worker_loop(), name="image-generation-evaluation-worker")
        self._queue.put_nowait(task_id)
        self._enqueued.add(task_id)
        return updated

    async def request_stop(self, task_id: str) -> dict[str, Any] | None:
        task = self.get(task_id)
        if task is None:
            return None
        if task.get("state") in {"awaiting_confirmation", "queued"}:
            return self.update(task_id, state="stopped", status="stopped", phase="stopped", currentStep="由用户停止")
        if task.get("state") in self.ACTIVE_STATES:
            self.update(task_id, state="stopping", status="stopping", phase="stopping", currentStep="正在停止评测任务")
            event = self._cancel_events.get(task_id)
            if event:
                event.set()
            return self.get(task_id)
        return task

    async def retry(self, task_id: str) -> dict[str, Any] | None:
        task = self.get(task_id)
        if task is None:
            return None
        if task.get("state") not in {"failed", "stopped"}:
            raise ValueError("只有失败或已停止的文生图评测任务可以重试。")
        value = self.update(task_id, state="queued", status="queued", phase="queued", progress=0, currentStep="等待评测任务执行", results=[])
        if value is not None:
            for key in ("failureCode", "failureMessage", "suggestedAction", "error", "cleanupError"):
                value.pop(key, None)
            value = self.save(value)
        if self._queue is None:
            self._queue = asyncio.Queue()
        if self._worker_task is None:
            self._worker_task = asyncio.create_task(self._worker_loop(), name="image-generation-evaluation-worker")
        self._queue.put_nowait(task_id)
        return value

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
                    _busy_started = time.monotonic()
                    _busy_cap = max(60.0, float(os.environ.get("XUNTUI_RESOURCE_WAIT_TIMEOUT_SECONDS", "600")))
                    while self.busy_check is not None and self.busy_check():
                        if time.monotonic() - _busy_started > _busy_cap:
                            self.update(task_id, state="failed", status="failed", currentStep="等待共享资源超时。", error="共享执行资源长时间被占用，请检查是否有启动或训练流程挂起。")
                            raise RuntimeError("等待共享执行资源超时，任务转失败。")
                        await asyncio.sleep(0.1)
                    self._active_task_id = task_id
                    with resource_scope(task_id):
                        self._active_executor = self.executor_factory(self)
                    cancel_event = asyncio.Event()
                    self._cancel_events[task_id] = cancel_event
                    self.update(task_id, state="preparing", status="preparing", phase="preparing", currentStep="正在准备文生图评测", progress=0)
                    def on_event(phase: str, message: str, details: Mapping[str, Any] | None = None) -> None:
                        payload = dict(details or {})
                        changes: dict[str, Any] = {
                            "state": "running",
                            "status": "running",
                            "phase": phase,
                            "currentStep": message,
                            "progress": payload.get("progress"),
                            "results": payload.get("results"),
                        }
                        if isinstance(payload.get("remoteHandle"), Mapping):
                            changes["remoteHandle"] = dict(payload["remoteHandle"])
                        self.update(task_id, **changes)
                        self.append_log(task_id, message)
                    outcome = await self._active_executor.run(task, on_event=on_event, cancel_event=cancel_event)
                    if outcome.get("status") == "completed":
                        self.update(task_id, state="completed", status="completed", phase="completed", currentStep="文生图验证评测完成", progress=100, results=outcome.get("results") or [], summary=outcome.get("summary") or {})
                    elif outcome.get("status") == "stopped" or cancel_event.is_set():
                        self.update(task_id, state="stopped", status="stopped", phase="stopped", currentStep="文生图验证评测已停止", results=outcome.get("results") or [], summary=outcome.get("summary") or {})
                    else:
                        self.update(task_id, state="failed", status="failed", phase="failed", currentStep="文生图验证评测失败", failureCode=outcome.get("failureCode") or "evaluation_failed", failureMessage=outcome.get("failureMessage") or "文生图验证评测失败。", suggestedAction=outcome.get("suggestedAction") or "检查评测日志后重试。", results=outcome.get("results") or [], summary=outcome.get("summary") or {})
            except asyncio.CancelledError:
                raise
            except Exception:
                self.update(task_id, state="failed", status="failed", phase="failed", currentStep="文生图验证评测失败", failureCode="evaluation_failed", failureMessage="文生图验证评测失败。", suggestedAction="检查评测日志后重试。")
            finally:
                self._cancel_events.pop(task_id, None)
                self._active_task_id = None
                self._active_executor = None
                self._queue.task_done()


__all__ = ["ImageGenerationEvaluationRuntime"]
