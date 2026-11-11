from __future__ import annotations

from backend.personal_youyun import bind_resource, resource_scope, scoped_resource, background_worker

"""Persistent queue and lifecycle for image-generation LoRA training."""

import asyncio
import os
import json
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Mapping

from backend.image_generation_executor import parse_sdxl_training_log
from backend.resource_lifecycle import ResourceLeaseManager
from backend.training_executor import TrainingEvent


GENERIC_FAILURE_MESSAGE = "文生图微调执行失败。"
RUNTIME_INTERRUPTED_MESSAGE = "文生图微调任务在后端重启时中断，请重试。"


class ImageGenerationTrainingRuntime:
    ACTIVE_STATES = {"preparing", "running", "collecting", "stopping"}

    def __init__(self, *, task_root: Path, execution_lock: asyncio.Lock, executor_factory: Callable[["ImageGenerationTrainingRuntime"], Any], busy_check: Callable[[], bool] | None = None, on_terminal: Callable[[Mapping[str, Any]], Any] | None = None) -> None:
        self.task_root = task_root.expanduser().resolve()
        self.preview_root = (self.task_root.parent / "image-generation-training-previews").resolve()
        self.execution_lock = execution_lock
        self.executor_factory = executor_factory
        self.busy_check = busy_check
        self.on_terminal = on_terminal
        self._queue: asyncio.Queue[str] | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._active_task_id: str | None = None
        self._active_executor: Any | None = None
        self._active_cancel: asyncio.Event | None = None
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
            raise ValueError("文生图微调任务 ID 不合法。")
        return self.task_root / f"{task_id}.json"

    def _read(self, path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _write(self, path: Path, value: Mapping[str, Any]) -> None:
        self.task_root.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(dict(value), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _normalize_task(task: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(task)
        state = str(value.get("state") or "")
        execution_status = {
            "awaiting_confirmation": "awaiting_confirmation",
            "draft": "awaiting_confirmation",
            "queued": "queued",
            "preparing": "preparing",
            "provisioning": "preparing",
            "running": "running",
            "stopping": "stopping",
            "collecting": "collecting",
            "completed": "completed",
            "failed": "failed",
            "stopped": "stopped",
        }.get(state)
        if execution_status:
            value["executionStatus"] = execution_status
        remote = bool(value.get("remoteHandle") or value.get("instanceId") or value.get("provider") == "youyun")
        if (
            state == "failed"
            and str(value.get("phase") or "") == "instance_provision"
            and not value.get("remoteHandle")
            and not value.get("instanceId")
        ):
            value["cleanupState"] = "released"
            value["resourceState"] = "released"
            value.setdefault("cleanupCheckedAt", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
            return value
        if state in {"completed", "stopped"} and not value.get("remoteHandle") and not value.get("instanceId"):
            value["cleanupState"] = "released"
            value["resourceState"] = "released"
            value.setdefault("cleanupCheckedAt", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
            return value
        if not remote and state in {"awaiting_confirmation", "draft"}:
            value.pop("cleanupState", None)
            value.pop("cleanupCheckedAt", None)
            value.pop("resourceState", None)
            return value
        cleanup = str(value.get("cleanupState") or "").strip() or ("pending" if remote else "released")
        value["cleanupState"] = cleanup
        value.setdefault("cleanupCheckedAt", None)
        if cleanup == "failed":
            value["resourceState"] = "cleanup_failed"
        elif cleanup in {"released", "completed"} and remote:
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
            value = self._read(self._task_path(task_id))
            return self._normalize_task(value) if value is not None else None

    def list_tasks(self, *, include_archived: bool = False, state: str | None = None, query: str | None = None) -> list[dict[str, Any]]:
        state_filter = str(state or "").strip()
        query_filter = str(query or "").strip().casefold()
        with self._lock:
            values = []
            for path in self.task_root.glob("*.json"):
                task = self._read(path)
                if task is None or (not include_archived and task.get("archived")):
                    continue
                if state_filter and str(task.get("state") or "") != state_filter:
                    continue
                if query_filter and query_filter not in " ".join(str(task.get(key) or "") for key in ("id", "name", "prompt")).casefold():
                    continue
                values.append(task)
        return sorted((self._normalize_task(item) for item in values), key=lambda item: str(item.get("createdAt") or ""), reverse=True)

    def save(self, task: Mapping[str, Any]) -> dict[str, Any]:
        value = self._normalize_task(task)
        if self.get(str(value["id"])) is None:
            bind_resource(str(value["id"]), remote=value.get("provider") == "youyun")
        with self._lock:
            self._write(self._task_path(str(value["id"])), value)
        return value

    def update(self, task_id: str, *, clear: Iterable[str] = (), **changes: Any) -> dict[str, Any] | None:
        with self._lock:
            task = self._read(self._task_path(task_id))
            if task is None:
                return None
            task = self._normalize_task(task)
            task.update({key: value for key, value in changes.items() if value is not None})
            for key in clear:
                task.pop(key, None)
            task["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            task = self._normalize_task(task)
            self._write(self._task_path(task_id), task)
            return task

    def create_task(self, task: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(task)
        task_id = str(value.get("id") or "")
        self._task_path(task_id)
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        value.setdefault("state", "awaiting_confirmation")
        value.setdefault("status", "draft")
        value.setdefault("progress", 0)
        value.setdefault("currentStep", "等待确认后提交优云训练任务")
        value.setdefault("logs", [])
        value.setdefault("metrics", [])
        value.setdefault("artifacts", [])
        value.setdefault("archived", False)
        value.setdefault("archivedAt", None)
        value.setdefault("createdAt", now)
        value.setdefault("updatedAt", now)
        if value.get("provider") == "youyun" or value.get("remoteHandle") or value.get("instanceId"):
            value.setdefault("phaseTimings", {})
            value.setdefault("lastHeartbeatAt", now)
            value.setdefault("lastHeartbeatAtEpoch", time.time())
            resource = value.get("resource") if isinstance(value.get("resource"), Mapping) else {}
            if resource.get("price") is not None:
                value.setdefault("priceSnapshot", resource.get("price"))
            value.setdefault("expiresAtEpoch", time.time() + float(value.get("ttlSeconds") or 6 * 3600))
        return self.save(value)

    def hydrate_observability(self, task_id: str) -> dict[str, Any]:
        task = self.get(task_id)
        if task is None:
            raise KeyError("文生图微调任务不存在。")
        if task.get("logs") and task.get("metrics"):
            return task
        preview_root = str(task.get("previewRoot") or "").strip()
        if not preview_root:
            return task
        log_path = Path(preview_root).expanduser().resolve() / "worker.log"
        if not log_path.is_file():
            return task
        parsed = parse_sdxl_training_log(log_path.read_text(encoding="utf-8", errors="replace"))
        changes: dict[str, Any] = {}
        if not task.get("logs") and parsed["logs"]:
            changes["logs"] = parsed["logs"]
        if not task.get("metrics") and parsed["metrics"]:
            changes["metrics"] = [
                {
                    "step": item.get("step"),
                    "loss": item.get("loss"),
                    "evalLoss": item.get("evalLoss"),
                    "learningRate": item.get("learningRate"),
                }
                for item in parsed["metrics"]
            ]
        return self.update(task_id, **changes) if changes else task

    def enqueue(self, task_id: str) -> None:
        if self._queue is None:
            self._queue = asyncio.Queue()
        if self._worker_task is None:
            self._worker_task = asyncio.create_task(self._worker_loop(), name="image-generation-training-worker")
        if task_id not in self._enqueued:
            self._enqueued.add(task_id)
            self._queue.put_nowait(task_id)

    async def start(self) -> None:
        if self._worker_task is not None:
            return
        self.task_root.mkdir(parents=True, exist_ok=True)
        self._queue = asyncio.Queue()
        for task in self.list_tasks(include_archived=True):
            task_id = str(task.get("id") or "")
            state = str(task.get("state") or "")
            if state in self.ACTIVE_STATES:
                cleanup_state = None
                cleanup_error = None
                try:
                    with resource_scope(task_id):
                        executor = self.executor_factory(self)
                    cleanup = getattr(executor, "cleanup_remote", None)
                    if callable(cleanup):
                        result = cleanup(task)
                        if isinstance(result, Awaitable):
                            result = await result
                        if isinstance(result, Mapping):
                            cleanup_state = result.get("cleanupState")
                            cleanup_error = result.get("cleanupError")
                except Exception as exc:
                    cleanup_state = "failed"
                    cleanup_error = str(exc)[:8000]
                self.update(
                    task_id,
                    state="failed",
                    status="failed",
                    currentStep="后端重启，文生图微调未完成",
                    failureCode="runtime_interrupted",
                    failureMessage=RUNTIME_INTERRUPTED_MESSAGE,
                    suggestedAction="确认优云实例状态后重试任务。",
                    cleanupState=cleanup_state,
                    cleanupError=cleanup_error,
                )
            elif state == "queued" and not task.get("archived"):
                self._enqueued.add(task_id)
                self._queue.put_nowait(task_id)
        self._worker_task = asyncio.create_task(self._worker_loop(), name="image-generation-training-worker")

    async def stop(self) -> None:
        if self._active_task_id and self._active_cancel is not None:
            self.update(self._active_task_id, state="stopping", status="stopping", currentStep="正在停止文生图微调任务")
            self._active_cancel.set()
        worker = self._worker_task
        self._worker_task = None
        if worker is not None:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

    async def confirm(self, task_id: str) -> dict[str, Any]:
        task = self.get(task_id)
        if task is None:
            raise KeyError("文生图微调任务不存在。")
        if task.get("archived"):
            raise ValueError("已归档文生图微调任务不能确认。")
        if str(task.get("state") or "") != "awaiting_confirmation":
            raise ValueError("当前文生图微调任务不在待确认状态。")
        value = self.update(task_id, state="queued", status="queued", currentStep="等待优云训练任务执行", confirmedAt=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        self.enqueue(task_id)
        return value or task

    async def request_stop(self, task_id: str) -> dict[str, Any]:
        task = self.get(task_id)
        if task is None:
            raise KeyError("文生图微调任务不存在。")
        if task.get("archived"):
            raise ValueError("已归档文生图微调任务不能停止。")
        state = str(task.get("state") or "")
        if state == "queued":
            return self.update(task_id, state="stopped", status="stopped", currentStep="任务已停止") or task
        if state in self.ACTIVE_STATES and self._active_task_id == task_id and self._active_cancel is not None:
            self._active_cancel.set()
            return self.update(task_id, state="stopping", status="stopping", currentStep="正在停止文生图微调任务") or task
        if state in {"failed", "stopped"}:
            return task
        raise ValueError("当前文生图微调任务不能停止。")

    @scoped_resource("task_id")
    async def force_cleanup_remote_task(
        self,
        task_id: str,
        *,
        reason: str = "自动清理",
        failure_code: str | None = None,
        failure_message: str | None = None,
        suggested_action: str | None = None,
    ) -> dict[str, Any] | None:
        """Idempotently release a persisted remote training instance."""
        task = self.get(task_id)
        if task is None:
            return None
        handle = task.get("remoteHandle") if isinstance(task.get("remoteHandle"), Mapping) else {}
        instance_id = str(handle.get("instanceId") or task.get("instanceId") or "").strip()
        if not instance_id:
            if str(task.get("provider") or "") == "youyun":
                return self.update(
                    task_id,
                    resourceState="released",
                    cleanupState="released",
                    cleanupCheckedAt=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    currentStep=f"{reason}：未发现优云实例句柄",
                    clear=("cleanupError",),
                )
            return task
        if str(task.get("cleanupState") or "") in {"released", "completed"}:
            return self.update(
                task_id,
                resourceState="released",
                cleanupState="released",
                currentStep=f"{reason}：优云实例已释放",
                clear=("cleanupError",),
            ) or task

        lease = self.lease_manager.acquire(owner_id=task_id, provider="youyun", instance_id=instance_id)
        self.lease_manager.mark_running(lease.lease_id)
        self.lease_manager.request_release(lease.lease_id, reason=reason)
        self.update(
            task_id,
            resourceState="releasing",
            cleanupState="releasing",
            currentStep=f"{reason}：正在释放优云实例",
        )
        self.append_log(task_id, f"开始{reason}优云实例 {instance_id}。")
        report: Mapping[str, Any]
        try:
            with resource_scope(task_id):
                executor = self._active_executor if task_id == self._active_task_id and self._active_executor is not None else self.executor_factory(self)
            cleanup = getattr(executor, "cleanup_remote", None)
            if not callable(cleanup):
                raise RuntimeError("优云文生图训练执行器未提供实例清理能力。")
            value = cleanup(self.get(task_id) or task)
            if isinstance(value, Awaitable):
                value = await value
            report = value if isinstance(value, Mapping) else {}
        except Exception as exc:
            report = {"cleanupState": "failed", "cleanupError": str(exc) or type(exc).__name__}

        checked_at = report.get("cleanupCheckedAt") or time.strftime("%Y-%m-%dT%H:%M:%S%z")
        if str(report.get("cleanupState") or "") in {"released", "completed"}:
            self.lease_manager.confirm_released(lease.lease_id)
            current = self.get(task_id) or task
            terminal_failure = {
                "state": "failed",
                "status": "failed",
                "failureCode": failure_code,
                "failureMessage": failure_message,
                "suggestedAction": suggested_action,
            } if failure_code else {}
            if not terminal_failure and str(current.get("state") or "") in self.ACTIVE_STATES | {"queued"}:
                terminal_failure = {
                    "state": "stopped",
                    "status": "stopped",
                    "failureCode": "training_stopped",
                    "failureMessage": "优云实例已手动释放，文生图微调未完成。",
                    "suggestedAction": "确认配置和资源后重新运行任务。",
                }
            updated = self.update(
                task_id,
                resourceState="released",
                cleanupState="released",
                cleanupCheckedAt=checked_at,
                currentStep=f"{reason}：优云实例已释放",
                clear=("cleanupError",),
                **terminal_failure,
            )
            self.append_log(task_id, f"优云实例 {instance_id} 已释放删除。")
            return updated

        error = str(report.get("cleanupError") or "实例删除未确认")[:8000]
        self.lease_manager.mark_cleanup_failed(lease.lease_id, error)
        updated = self.update(
            task_id,
            state="failed",
            status="failed",
            resourceState="cleanup_failed",
            cleanupState="failed",
            cleanupError=error,
            cleanupCheckedAt=checked_at,
            failureCode="cleanup_failed",
            failureMessage="优云实例清理失败。",
            suggestedAction="重试清理优云实例，确认实例消失后再重新运行。",
            currentStep=f"{reason}：优云实例释放失败",
        )
        self.append_log(task_id, f"优云实例 {instance_id} 释放失败：{error}。")
        return updated

    async def retry(self, task_id: str) -> dict[str, Any]:
        task = self.get(task_id)
        if task is None:
            raise KeyError("文生图微调任务不存在。")
        if task.get("archived"):
            raise ValueError("已归档文生图微调任务不能重试。")
        if task.get("cleanupState") == "failed":
            raise ValueError("优云实例清理失败，请先完成清理后再重试。")
        if str(task.get("state") or "") not in {"failed", "stopped"}:
            raise ValueError("只有失败或已停止的文生图微调任务可以重试。")
        value = self.update(
            task_id,
            state="queued",
            status="queued",
            progress=0,
            currentStep="等待优云训练任务执行",
            clear=(
                "failureCode",
                "failureMessage",
                "suggestedAction",
                "error",
                "metrics",
                "artifacts",
                "remoteHandle",
                "cleanupState",
                "cleanupError",
                "cleanupCheckedAt",
                "resourceState",
            ),
        ) or task
        self.enqueue(task_id)
        return value

    def clone_task(self, task_id: str, overrides: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
        source = self.get(task_id)
        if source is None:
            return None
        keys = ("datasetId", "datasetName", "modelId", "modelRepoId", "modelType", "trainingMode", "resource", "previewRoot", "manifestPath", "configPath", "resolution", "epochs", "maxTrainSteps", "learningRate", "loraRank", "batchSize", "gradientAccumulation", "validationSplit", "seed")
        value = {key: source[key] for key in keys if key in source}
        for key, override in dict(overrides or {}).items():
            if key in keys:
                value[key] = override
        value.update({"id": f"igt-{time.strftime('%y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}", "name": f"{source.get('name') or '文生图微调任务'} · 副本", "state": "awaiting_confirmation", "status": "draft", "progress": 0, "currentStep": "等待确认后提交优云训练任务", "logs": [], "metrics": [], "artifacts": [], "archived": False, "archivedAt": None})
        return self.create_task(value)

    async def archive_task(self, task_id: str) -> dict[str, Any]:
        task = self.get(task_id)
        if task is None:
            raise KeyError("文生图微调任务不存在。")
        if task.get("archived"):
            raise ValueError("文生图微调任务已经归档。")
        if str(task.get("state") or "") not in {"completed", "failed", "stopped"}:
            raise ValueError("只有已完成、失败或已停止的文生图微调任务可以归档。")
        return self.update(task_id, archived=True, archivedAt=time.strftime("%Y-%m-%dT%H:%M:%S%z")) or task

    async def restore_task(self, task_id: str) -> dict[str, Any]:
        task = self.get(task_id)
        if task is None:
            raise KeyError("文生图微调任务不存在。")
        if not task.get("archived"):
            raise ValueError("文生图微调任务尚未归档。")
        return self.update(task_id, archived=False, archivedAt=None) or task

    def delete_task(self, task_id: str) -> bool:
        task = self.get(task_id)
        if task is None:
            return False
        if str(task.get("state") or "") in self.ACTIVE_STATES | {"queued"}:
            raise ValueError("运行中的文生图微调任务不能删除。")
        if task.get("remoteHandle") and str(task.get("cleanupState") or "") not in {"completed", "released"}:
            raise ValueError("优云实例尚未完成清理，不能删除文生图微调任务。")
        preview_value = str(task.get("previewRoot") or "").strip()
        if preview_value:
            preview_root = Path(preview_value).expanduser().resolve()
            managed_root = self.preview_root
            if preview_root == managed_root or not preview_root.is_relative_to(managed_root):
                raise ValueError("训练目录不在受控文生图微调路径内，已拒绝删除。")
            if preview_root.exists():
                shutil.rmtree(preview_root)
        self._task_path(task_id).unlink(missing_ok=True)
        return True

    def append_log(self, task_id: str, message: str) -> None:
        clean = str(message or "").strip()
        if not clean:
            return
        task = self.get(task_id)
        if task is None:
            return
        logs = list(task.get("logs") or [])
        logs.append(clean[:8000])
        self.update(task_id, logs=logs[-5000:])

    def diagnostics(self, task_id: str) -> dict[str, Any]:
        task = self.get(task_id)
        if task is None:
            raise KeyError("文生图微调任务不存在。")
        failure_code = str(task.get("failureCode") or "")
        failure_message = str(task.get("failureMessage") or "").strip() or GENERIC_FAILURE_MESSAGE
        result: dict[str, Any] = {
            "taskId": task_id,
            "state": task.get("state"),
            "phase": task.get("phase"),
            "progress": int(task.get("progress") or 0),
            "failure": {"code": failure_code, "message": failure_message} if failure_code or task.get("state") == "failed" else None,
            "suggestedAction": task.get("suggestedAction"),
            "attempts": task.get("attempts") or [],
        }
        if task.get("cleanupState"):
            result["cleanup"] = {"state": task.get("cleanupState"), "error": "优云实例清理失败，请重试清理。" if task.get("cleanupState") == "failed" else None}
        return result

    @background_worker
    async def _worker_loop(self) -> None:
        assert self._queue is not None
        while True:
            task_id = await self._queue.get()
            self._enqueued.discard(task_id)
            try:
                task = self.get(task_id)
                if task is None or task.get("archived") or task.get("state") != "queued":
                    continue
                _busy_started = time.monotonic()
                _busy_cap = max(60.0, float(os.environ.get("XUNTUI_RESOURCE_WAIT_TIMEOUT_SECONDS", "600")))
                while self.busy_check is not None and self.busy_check():
                    if time.monotonic() - _busy_started > _busy_cap:
                        self.update(task_id, state="failed", status="failed", currentStep="等待共享资源超时。", error="共享执行资源长时间被占用，请检查是否有启动或训练流程挂起。")
                        raise RuntimeError("等待共享执行资源超时，任务转失败。")
                    await asyncio.sleep(0.05)
                async with self.execution_lock:
                    self._active_task_id = task_id
                    self._active_cancel = asyncio.Event()
                    with resource_scope(task_id):
                        self._active_executor = self.executor_factory(self)
                    current = self.get(task_id) or task
                    attempts = list(current.get("attempts") or [])
                    attempt = {
                        "number": max((int(item.get("number") or 0) for item in attempts if isinstance(item, Mapping)), default=0) + 1,
                        "status": "running",
                        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                        "finishedAt": None,
                        "durationSeconds": None,
                        "stages": [],
                    }
                    attempts.append(attempt)
                    self.update(task_id, attempts=attempts)
                    self.update(task_id, state="preparing", status="preparing", phase="preparing", currentStep="正在准备文生图 LoRA 训练")
                    try:
                        result = await self._active_executor.run(task, on_event=lambda event: self._handle_event(task_id, event), cancel_event=self._active_cancel)
                        status = str(result.get("status") or "failed")
                        cleanup_state = str(result.get("cleanupState") or "")
                        if status == "completed" and cleanup_state not in {"failed", "releasing"}:
                            self.update(task_id, state="completed", status="completed", progress=100, phase="completed", currentStep="文生图 LoRA 微调完成", artifacts=result.get("artifacts") or [])
                        elif status == "completed":
                            cleanup_error = str(result.get("cleanupError") or "实例删除未确认")
                            self.update(
                                task_id,
                                state="failed",
                                status="failed",
                                currentStep="文生图训练完成但优云实例清理失败",
                                failureCode="cleanup_failed",
                                failureMessage="文生图训练产物已生成，但优云实例清理未确认。",
                                suggestedAction="重试清理优云实例，确认实例释放后再重试或使用产物。",
                                cleanupState="failed",
                                cleanupError=cleanup_error,
                                resourceState="cleanup_failed",
                                artifacts=result.get("artifacts") or [],
                            )
                            self.append_log(task_id, f"训练产物已生成，但优云实例清理失败：{cleanup_error}。")
                            status = "failed"
                        elif status == "stopped":
                            self.update(task_id, state="stopped", status="stopped", currentStep="文生图微调任务已停止", failureCode=result.get("failureCode") or "training_stopped", failureMessage=result.get("failureMessage") or "文生图微调任务已停止。", suggestedAction="确认配置后重新提交任务。")
                        else:
                            failure_changes = {
                                "state": "failed",
                                "status": "failed",
                                "currentStep": "文生图微调执行失败",
                                "failureCode": result.get("failureCode") or "training_failed",
                                "failureMessage": result.get("failureMessage") or GENERIC_FAILURE_MESSAGE,
                                "suggestedAction": result.get("suggestedAction") or "检查训练日志和配置后重试。",
                                "error": result.get("error"),
                            }
                            if result.get("phase"):
                                failure_changes["phase"] = result.get("phase")
                            self.update(task_id, **failure_changes)
                            failure_code = str(result.get("failureCode") or "training_failed")
                            failure_message = str(result.get("failureMessage") or GENERIC_FAILURE_MESSAGE)
                            suggested_action = str(result.get("suggestedAction") or "检查训练日志和配置后重试。")
                            self.append_log(task_id, f"失败阶段：{result.get('phase') or 'unknown'}；失败码：{failure_code}；{failure_message} 建议：{suggested_action}")
                        self._finish_attempt(task_id, status, result.get("error"))
                        result_logs = result.get("logs")
                        result_metrics = result.get("metrics")
                        if isinstance(result_logs, list) or isinstance(result_metrics, list):
                            current = self.get(task_id) or {}
                            changes: dict[str, Any] = {}
                            if isinstance(result_logs, list):
                                merged_logs = list(current.get("logs") or [])
                                for message in result_logs:
                                    clean = str(message or "").strip()
                                    if clean and clean not in merged_logs:
                                        merged_logs.append(clean[:8000])
                                changes["logs"] = merged_logs[-5000:]
                            if isinstance(result_metrics, list):
                                by_step = {
                                    int(item.get("step") or 0): dict(item)
                                    for item in list(current.get("metrics") or [])
                                    if isinstance(item, Mapping)
                                }
                                for item in result_metrics:
                                    if isinstance(item, Mapping):
                                        by_step[int(item.get("step") or 0)] = dict(item)
                                changes["metrics"] = [by_step[step] for step in sorted(by_step)][-5000:]
                            self.update(task_id, **changes)
                        if result.get("cleanupState"):
                            self.update(task_id, cleanupState=result.get("cleanupState"), cleanupError=result.get("cleanupError"))
                        if self.on_terminal is not None:
                            terminal = self.get(task_id)
                            if terminal is not None:
                                try:
                                    callback_result = self.on_terminal(terminal)
                                    if isinstance(callback_result, Awaitable):
                                        await callback_result
                                except Exception:
                                    self.append_log(task_id, "Adapter 模型目录同步失败，任务产物仍已保留。")
                    except Exception as exc:
                        current = self.get(task_id) or {}
                        self.update(
                            task_id,
                            state="failed",
                            status="failed",
                            currentStep="文生图微调执行失败",
                            failureCode="training_failed",
                            failureMessage=GENERIC_FAILURE_MESSAGE,
                            suggestedAction="检查训练日志和配置后重试。",
                            error=str(exc)[:8000],
                            phase=current.get("phase") or "preparing",
                        )
                        self._finish_attempt(task_id, "failed", str(exc))
                        current = self.get(task_id) or {}
                        if (
                            current.get("remoteHandle")
                            and str(current.get("cleanupState") or "") not in {"released", "completed"}
                        ):
                            await self.force_cleanup_remote_task(task_id, reason="执行异常自动清理")
                    finally:
                        self._active_task_id = None
                        self._active_cancel = None
                        self._active_executor = None
            finally:
                self._queue.task_done()

    def _handle_event(self, task_id: str, event: TrainingEvent) -> None:
        payload = dict(event.payload or {})
        if event.type == "log":
            self.append_log(task_id, event.message)
            return
        if event.type == "metric":
            task = self.get(task_id)
            if task is None:
                return
            metrics = list(task.get("metrics") or [])
            metric = {"step": payload.get("step"), "loss": payload.get("loss"), "evalLoss": payload.get("eval_loss"), "learningRate": payload.get("learning_rate")}
            matching_index = next((index for index, item in enumerate(metrics) if item.get("step") == metric["step"]), None)
            if matching_index is None:
                metrics.append(metric)
            else:
                metrics[matching_index] = metric
            metrics.sort(key=lambda item: int(item.get("step") or 0))
            self.update(
                task_id,
                metrics=metrics,
                state="running",
                status="running",
                phase="running",
                progress=min(98, int(payload.get("progress") or 0)),
                currentStep=event.message or "训练中",
                lastHeartbeatAt=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                lastHeartbeatAtEpoch=time.time(),
            )
        else:
            task = self.get(task_id)
            if task is not None:
                attempts = list(task.get("attempts") or [])
                if attempts and isinstance(attempts[-1], Mapping) and attempts[-1].get("status") == "running":
                    latest = dict(attempts[-1])
                    stages = list(latest.get("stages") or [])
                    elapsed = payload.get("elapsedSeconds")
                    previous_elapsed = latest.get("lastStageElapsedSeconds")
                    duration = None
                    if isinstance(elapsed, (int, float)) and isinstance(previous_elapsed, (int, float)):
                        duration = round(max(0.0, float(elapsed) - float(previous_elapsed)), 1)
                    elif isinstance(elapsed, (int, float)):
                        duration = round(max(0.0, float(elapsed)), 1)
                    stages.append({
                        "message": event.message,
                        "phase": payload.get("phase") or "running",
                        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                        "durationSeconds": duration,
                        "elapsedSeconds": elapsed,
                    })
                    latest["stages"] = stages[-100:]
                    if isinstance(elapsed, (int, float)):
                        latest["lastStageElapsedSeconds"] = elapsed
                    attempts[-1] = latest
                    self.update(task_id, attempts=attempts)
            changes: dict[str, Any] = {
                "state": "collecting" if payload.get("phase") == "collecting" else "running",
                "status": "running",
                "phase": payload.get("phase") or "running",
                "progress": payload.get("progress"),
                "currentStep": event.message or None,
                "lastHeartbeatAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "lastHeartbeatAtEpoch": time.time(),
            }
            if isinstance(payload.get("remoteHandle"), Mapping):
                changes["remoteHandle"] = dict(payload["remoteHandle"])
                changes["resourceState"] = "running"
                changes["cleanupState"] = "pending"
            self.update(task_id, **changes)
            self.append_log(task_id, event.message)

    def _finish_attempt(self, task_id: str, status: str, error: Any = None) -> None:
        task = self.get(task_id)
        if task is None:
            return
        attempts = list(task.get("attempts") or [])
        if not attempts or not isinstance(attempts[-1], Mapping):
            return
        latest = dict(attempts[-1])
        if latest.get("status") != "running":
            return
        finished_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        started_epoch = _parse_epoch(latest.get("startedAt"))
        latest["status"] = status if status in {"completed", "failed", "stopped"} else "failed"
        latest["finishedAt"] = finished_at
        latest["durationSeconds"] = max(0, int(time.time() - started_epoch)) if started_epoch is not None else 0
        if error:
            latest["error"] = str(error)[:8000]
        remote_handle = task.get("remoteHandle")
        if isinstance(remote_handle, Mapping) and remote_handle.get("instanceId"):
            latest["instanceId"] = remote_handle.get("instanceId")
        attempts[-1] = latest
        self.update(task_id, attempts=attempts)


def _parse_epoch(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = time.strptime(text, "%Y-%m-%dT%H:%M:%S%z")
        return time.mktime(parsed)
    except (TypeError, ValueError, OverflowError):
        return None


__all__ = ["ImageGenerationTrainingRuntime"]
