from __future__ import annotations

from backend.personal_youyun import bind_resource, scoped_resource, background_worker

"""Persistent queue and lifecycle for visual understanding training tasks."""

import asyncio
import inspect
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Mapping

from backend.training_executor import TrainingEvent
from backend.resource_lifecycle import LeaseState, ResourceLeaseManager


class VisionTrainingRuntime:
    ACTIVE_STATES = {"preparing", "running", "stopping", "collecting"}
    LEGACY_PENDING_STATES = {"awaiting_executor", "configured"}
    FIELD_ALIASES = {
        "current_step": "currentStep",
        "cancel_requested": "cancelRequested",
        "stop_reason": "stopReason",
        "config_path": "configPath",
        "manifest_path": "manifestPath",
        "preview_root": "previewRoot",
        "execution_status": "executionStatus",
        "confirmed_at": "confirmedAt",
        "loss_history": "lossHistory",
        "best_step": "bestStep",
        "best_eval_loss": "bestEvalLoss",
        "final_step": "finalStep",
        "final_eval_loss": "finalEvalLoss",
        "resource_state": "resourceState",
        "cleanup_state": "cleanupState",
        "cleanup_error": "cleanupError",
        "cleanup_checked_at": "cleanupCheckedAt",
        "failure_code": "failureCode",
        "failure_message": "failureMessage",
        "suggested_action": "suggestedAction",
        "last_heartbeat_at_epoch": "lastHeartbeatAtEpoch",
        "expires_at_epoch": "expiresAtEpoch",
    }

    def __init__(
        self,
        *,
        task_root: Path,
        local_executor: Any,
        execution_lock: asyncio.Lock,
        remote_runner_factory: Callable[..., Any] | None = None,
        busy_check: Callable[[], bool] | None = None,
    ) -> None:
        self.task_root = task_root.expanduser().resolve()
        self.local_executor = local_executor
        self.execution_lock = execution_lock
        self.remote_runner_factory = remote_runner_factory
        self.busy_check = busy_check
        self._queue: asyncio.Queue[str] | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._active_task_id: str | None = None
        self._enqueued: set[str] = set()
        self._lock = threading.RLock()
        self.lease_manager = ResourceLeaseManager()

    def _remote_runner(self, task: Mapping[str, Any]) -> Any:
        """Build the remote runner with the task resource snapshot when supported.

        Older test/integration factories accepted only the runtime sink, so keep
        that contract compatible while allowing production factories to consume
        the resolved region, zone, charge type, and image from the task.
        """
        factory = self.remote_runner_factory
        if factory is None:
            raise RuntimeError("未配置优云视觉训练执行器。")
        try:
            signature = inspect.signature(factory)
            positional = [
                parameter
                for parameter in signature.parameters.values()
                if parameter.kind
                in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            ]
            accepts_varargs = any(
                parameter.kind == inspect.Parameter.VAR_POSITIONAL
                for parameter in signature.parameters.values()
            )
        except (TypeError, ValueError):
            positional = []
            accepts_varargs = True
        if accepts_varargs or len(positional) >= 2:
            return factory(self, task)
        return factory(self)

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
            raise ValueError("视觉任务 ID 不合法。")
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
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    @staticmethod
    def _normalize_task(task: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(task)
        state = str(value.get("state") or "")
        # Keep the execution-facing status authoritative. Older tasks only
        # persisted state/status, which left the UI stuck at queued forever.
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
        cleanup = str(value.get("cleanupState") or "").strip()
        # A provider can fail before CreateCompShareInstance returns a handle.
        # Historical records from that path were normalized as
        # `provisioning/pending` solely because provider=youyun was present.
        # Treat a failed provisioning task with no persisted instance marker as
        # a terminal no-resource outcome so old tasks do not show a fake leak.
        artifact_handles = any(
            isinstance(item, Mapping)
            and str(item.get("type") or "") == "youyun-instance"
            and str(item.get("instanceId") or "").strip()
            for item in (value.get("artifacts") or [])
        )
        if (
            state == "failed"
            and str(value.get("phase") or "") == "instance_provision"
            and not value.get("remoteHandle")
            and not value.get("instanceId")
            and not artifact_handles
        ):
            value["cleanupState"] = "released"
            value["resourceState"] = "released"
            value.setdefault("cleanupCheckedAt", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
            return value
        if not remote and state in {"awaiting_confirmation", "draft"}:
            value.pop("cleanupState", None)
            value.pop("cleanupCheckedAt", None)
            value.pop("resourceState", None)
            return value
        if not cleanup:
            cleanup = "pending" if remote else "released"
        value["cleanupState"] = cleanup
        value.setdefault("cleanupCheckedAt", None)
        if cleanup == "failed":
            value["resourceState"] = "cleanup_failed"
            return value
        if cleanup == "released" and remote:
            value["resourceState"] = "released"
            return value
        if value.get("resourceState"):
            return value
        if not remote:
            resource = "none"
        elif state in {"queued", "preparing", "provisioning"}:
            resource = "provisioning"
        elif state in {"stopping"}:
            resource = "stopping"
        else:
            resource = "running"
        value["resourceState"] = resource
        return value

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._read_path(self._task_path(task_id))
            return self._normalize_task(value) if value is not None else None

    def list_tasks(self) -> list[dict[str, Any]]:
        with self._lock:
            tasks = [
                task
                for path in self.task_root.glob("*.json")
                if (task := self._read_path(path)) is not None
            ]
        return sorted((self._normalize_task(task) for task in tasks), key=lambda item: str(item.get("createdAt", "")), reverse=True)

    def save(self, task: Mapping[str, Any]) -> dict[str, Any]:
        value = self._normalize_task(task)
        if self.get(str(value["id"])) is None:
            bind_resource(str(value["id"]), remote=value.get("provider") == "youyun")
        with self._lock:
            self._write_path(self._task_path(str(value["id"])), value)
        return value

    def update(
        self,
        task_id: str,
        *,
        clear: Iterable[str] = (),
        **changes: Any,
    ) -> dict[str, Any] | None:
        with self._lock:
            task = self._read_path(self._task_path(task_id))
            if task is None:
                return None
            task = self._normalize_task(task)
            for key, value in changes.items():
                key = self.FIELD_ALIASES.get(key, key)
                if value is not None:
                    task[key] = value
            for key in clear:
                task.pop(self.FIELD_ALIASES.get(key, key), None)
            task["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            task = self._normalize_task(task)
            self._write_path(self._task_path(task_id), task)
            return task

    def append_log(self, task_id: str, message: str) -> None:
        clean = str(message or "").strip()
        if not clean:
            return
        with self._lock:
            task = self._read_path(self._task_path(task_id))
            if task is None:
                return
            logs = list(task.get("logs") or [])
            stamp = time.strftime("%H:%M:%S")
            logs.append(f"[{stamp}] {clean[:8000]}")
            task["logs"] = logs[-5000:]
            task["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            self._write_path(self._task_path(task_id), task)

    def append_metric(
        self,
        task_id: str,
        *,
        step: int,
        epoch: float | None = None,
        train_loss: float | None = None,
        eval_loss: float | None = None,
        learning_rate: float | None = None,
    ) -> None:
        if train_loss is None and eval_loss is None:
            return
        with self._lock:
            task = self._read_path(self._task_path(task_id))
            if task is None:
                return
            history = list(task.get("lossHistory") or [])
            current = next((item for item in history if item.get("step") == step), None)
            if current is None:
                current = {"step": step}
                history.append(current)
            values = {
                "epoch": epoch,
                "trainLoss": train_loss,
                "evalLoss": eval_loss,
                "learningRate": learning_rate,
            }
            for key, value in values.items():
                if value is not None:
                    current[key] = value
            task["lossHistory"] = sorted(history, key=lambda item: int(item.get("step", 0)))
            task["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            self._write_path(self._task_path(task_id), task)

    def metrics(self, task_id: str) -> list[dict[str, Any]]:
        task = self.get(task_id)
        return list(task.get("lossHistory") or []) if task else []

    def is_cancel_requested(self, task_id: str) -> bool:
        task = self.get(task_id)
        return bool(task and task.get("cancelRequested"))

    def enqueue(self, task_id: str) -> None:
        if self._queue is None:
            self._queue = asyncio.Queue()
            if self._worker_task is None:
                self._worker_task = asyncio.create_task(self._worker_loop(), name="vision-training-worker")
        if task_id in self._enqueued:
            return
        self._enqueued.add(task_id)
        self._queue.put_nowait(task_id)

    def _migrate_legacy_pending_task(self, task: Mapping[str, Any]) -> None:
        """Keep old config-only tasks safe while exposing the new confirmation step."""
        task_id = str(task["id"])
        marker = "历史视觉任务已迁移为待确认状态，请确认后进入训练队列。"
        logs = list(task.get("logs") or [])
        if marker not in logs:
            logs.append(marker)
        self.update(
            task_id,
            state="awaiting_confirmation",
            status="draft",
            currentStep="等待确认后提交训练",
            executionStatus="awaiting_confirmation",
            cancelRequested=False,
            message="该视觉任务来自旧版本配置，确认后将进入视觉训练队列。",
            logs=logs,
            clear=("confirmedAt", "error", "stopReason"),
        )

    @staticmethod
    def _remote_instance_marker(task: Mapping[str, Any]) -> dict[str, Any] | None:
        """Find the persisted Youyun instance handle used by restart recovery."""
        for item in task.get("artifacts") or []:
            if not isinstance(item, Mapping) or item.get("type") != "youyun-instance":
                continue
            instance_id = str(item.get("instanceId") or "").strip()
            if instance_id:
                return dict(item)
        remote = task.get("remoteHandle")
        if isinstance(remote, Mapping):
            instance_id = str(remote.get("instanceId") or remote.get("instance_id") or "").strip()
            if instance_id:
                return {
                    "type": "youyun-instance",
                    "provider": "youyun",
                    "instanceId": instance_id,
                    "state": "running",
                }
        return None

    def _set_remote_cleanup_marker(
        self,
        task: Mapping[str, Any],
        marker: Mapping[str, Any],
        *,
        state: str,
        error: str | None = None,
    ) -> list[dict[str, Any]]:
        """Update the instance marker without discarding saved training artifacts."""
        instance_id = str(marker.get("instanceId") or "")
        lease = self.lease_manager.acquire(owner_id=str(task.get("id") or ""), provider="youyun", instance_id=instance_id)
        if state in {"running", "releasing", "released", "cleanup_failed"}:
            self.lease_manager.mark_running(lease.lease_id)
        if state == "releasing":
            self.lease_manager.request_release(lease.lease_id, reason="task_cleanup")
        elif state == "released":
            self.lease_manager.confirm_released(lease.lease_id)
        elif state == "cleanup_failed":
            self.lease_manager.mark_cleanup_failed(lease.lease_id, error or "实例删除未确认")
        artifacts = [
            dict(item)
            for item in task.get("artifacts") or []
            if isinstance(item, Mapping) and item.get("type") != "youyun-instance"
        ]
        updated = {**dict(marker), "type": "youyun-instance", "provider": "youyun", "instanceId": instance_id, "state": state}
        if error:
            updated["cleanupError"] = error
        else:
            updated.pop("cleanupError", None)
        artifacts.append(updated)
        return artifacts

    @scoped_resource("task")
    async def _cleanup_interrupted_remote_task(self, task: Mapping[str, Any]) -> None:
        """Recover a Youyun handle left behind by a backend restart."""
        task_id = str(task.get("id") or "")
        marker = self._remote_instance_marker(task)
        if not task_id or marker is None or str(marker.get("state") or "") == "released":
            return
        instance_id = str(marker.get("instanceId") or "")
        self.update(task_id, currentStep="后端重启，正在清理遗留优云实例")
        report: Mapping[str, Any]
        try:
            if self.remote_runner_factory is None:
                raise RuntimeError("未配置优云视觉训练执行器，无法清理遗留实例。")
            runner = self._remote_runner(task)
            manager = getattr(runner, "manager", None)
            cleanup = getattr(manager, "cleanup_after_task", None)
            if not callable(cleanup):
                raise RuntimeError("优云执行器未提供实例清理能力。")
            value = await asyncio.to_thread(cleanup, instance_id, stop_first=False)
            report = value if isinstance(value, Mapping) else {}
        except Exception as exc:  # noqa: BLE001 - preserve handle for manual retry
            report = {"terminated": False, "error": str(exc) or type(exc).__name__}

        current = self.get(task_id) or dict(task)
        if bool(report.get("terminated")):
            self.update(
                task_id,
                artifacts=self._set_remote_cleanup_marker(current, marker, state="released"),
                resource_state="released",
                cleanup_state="released",
                cleanup_checked_at=report.get("checkedAt"),
                current_step="后端重启，遗留优云实例已释放",
            )
            self.append_log(task_id, f"后端重启恢复清理：优云实例 {instance_id} 已释放删除。")
            return

        error = str(report.get("error") or "实例删除未确认")
        self.update(
            task_id,
            artifacts=self._set_remote_cleanup_marker(current, marker, state="cleanup_failed", error=error),
            resource_state="cleanup_failed",
            cleanup_state="failed",
            cleanup_error=error,
            cleanup_checked_at=report.get("checkedAt"),
            failure_code="cleanup_failed",
            failure_message="优云实例清理失败。",
            suggested_action="重试清理优云实例，确认实例消失后再重新运行。",
            current_step="后端重启，优云实例清理失败",
        )
        self.append_log(task_id, f"后端重启恢复清理失败：优云实例 {instance_id}：{error}。")

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
        """Stop/delete a persisted Youyun instance and verify the cleanup state.

        This path is intentionally idempotent so watchdogs, shutdown recovery,
        and a user retry can safely race without losing the remote handle.
        """
        task = self.get(task_id)
        if task is None:
            return None
        marker = self._remote_instance_marker(task)
        if marker is None:
            if str(task.get("provider") or "") == "youyun":
                return self.update(
                    task_id,
                    resource_state="released",
                    cleanup_state="released",
                    cleanup_checked_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    current_step=f"{reason}：未发现优云实例句柄",
                )
            return task
        if str(marker.get("state") or "") == "released":
            return self.update(
                task_id,
                clear=("cleanupError",),
                resource_state="released",
                cleanup_state="released",
                current_step=f"{reason}：优云实例已释放",
            ) or task
        instance_id = str(marker.get("instanceId") or "")
        self.update(
            task_id,
            artifacts=self._set_remote_cleanup_marker(task, marker, state="releasing"),
            resource_state="releasing",
            cleanup_state="releasing",
            current_step=f"{reason}：正在释放优云实例",
        )
        self.append_log(task_id, f"开始{reason}优云实例 {instance_id}。")
        report: Mapping[str, Any]
        try:
            if self.remote_runner_factory is None:
                raise RuntimeError("未配置优云视觉训练执行器，无法清理实例。")
            runner = self._remote_runner(task)
            manager = getattr(runner, "manager", None)
            cleanup = getattr(manager, "cleanup_after_task", None)
            if not callable(cleanup):
                raise RuntimeError("优云执行器未提供实例清理能力。")
            value = await asyncio.to_thread(cleanup, instance_id, stop_first=False)
            report = value if isinstance(value, Mapping) else {}
        except Exception as exc:  # noqa: BLE001 - preserve handle for retry
            report = {"terminated": False, "error": str(exc) or type(exc).__name__}

        current = self.get(task_id) or task
        checked_at = report.get("checkedAt") or time.strftime("%Y-%m-%dT%H:%M:%S%z")
        if bool(report.get("terminated")):
            terminal_failure = {
                "state": "failed",
                "status": "failed",
                "failure_code": failure_code,
                "failure_message": failure_message,
                "suggested_action": suggested_action,
                "error": failure_message,
            } if failure_code else {}
            updated = self.update(
                task_id,
                clear=("cleanupError",),
                artifacts=self._set_remote_cleanup_marker(current, marker, state="released"),
                resource_state="released",
                cleanup_state="released",
                cleanup_checked_at=checked_at,
                current_step=f"{reason}：优云实例已释放",
                **terminal_failure,
            )
            self.append_log(task_id, f"优云实例 {instance_id} 已释放删除。")
            return updated

        error = str(report.get("error") or "实例删除未确认")
        updated = self.update(
            task_id,
            artifacts=self._set_remote_cleanup_marker(current, marker, state="cleanup_failed", error=error),
            resource_state="cleanup_failed",
            cleanup_state="failed",
            cleanup_error=error,
            cleanup_checked_at=checked_at,
            failure_code="cleanup_failed",
            failure_message="优云实例清理失败。",
            suggested_action="重试清理优云实例，确认实例消失后再重新运行。",
            current_step=f"{reason}：优云实例释放失败",
        )
        self.append_log(task_id, f"优云实例 {instance_id} 释放失败：{error}。")
        return updated

    async def start(self) -> None:
        if self._worker_task is not None:
            return
        self.task_root.mkdir(parents=True, exist_ok=True)
        self._queue = asyncio.Queue()
        for task in self.list_tasks():
            # Persist compatibility fields for tasks created by older
            # releases so raw-file readers and the API see the same status.
            raw = self._read_path(self._task_path(str(task["id"])))
            if raw is not None and raw != task:
                self.save(task)
            if str(task.get("provider") or "") == "youyun" and not task.get("expiresAtEpoch"):
                now = time.time()
                budget = max(300.0, float(os.environ.get("XUNTUI_VISION_REMOTE_TASK_TIMEOUT_SECONDS", "3600")))
                self.update(
                    str(task["id"]),
                    last_heartbeat_at_epoch=now,
                    expires_at_epoch=now + budget,
                )
                task = self.get(str(task["id"])) or task
            state = str(task.get("state", ""))
            if state in self.ACTIVE_STATES:
                self.update(
                    str(task["id"]),
                    state="failed",
                    status="failed",
                    currentStep="后端重启，视觉训练未完成",
                    error="视觉训练进程在后端重启时中断，请重试。",
                )
                task = self.get(str(task["id"])) or task
                state = "failed"
            marker = self._remote_instance_marker(task)
            if marker is not None and state in {"failed", "stopped"} and str(marker.get("state") or "") != "released":
                await self._cleanup_interrupted_remote_task(task)
            elif state == "queued":
                self._enqueued.add(str(task["id"]))
                self._queue.put_nowait(str(task["id"]))
            elif state in self.LEGACY_PENDING_STATES:
                self._migrate_legacy_pending_task(task)
        self._worker_task = asyncio.create_task(self._worker_loop(), name="vision-training-worker")

    async def stop(self) -> None:
        current = self.get(self._active_task_id) if self._active_task_id else None
        if current is not None:
            self.update(current["id"], cancelRequested=True, state="stopping", status="stopping")
            if current.get("provider") == "local":
                try:
                    await self.local_executor.stop(current)
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

    async def request_stop(self, task_id: str) -> dict[str, Any] | None:
        task = self.get(task_id)
        if task is None:
            return None
        state = str(task.get("state", ""))
        if state == "queued":
            return self.update(
                task_id,
                state="stopped",
                status="stopped",
                progress=0,
                currentStep="由用户停止",
                cancelRequested=False,
                stopReason="manual",
            )
        if state in self.ACTIVE_STATES:
            self.update(task_id, state="stopping", status="stopping", cancelRequested=True, currentStep="正在停止视觉训练")
            if task_id == self._active_task_id and task.get("provider") == "local":
                await self.local_executor.stop(task)
            return self.get(task_id)
        return task

    async def retry(self, task_id: str) -> dict[str, Any] | None:
        task = self.get(task_id)
        if task is None:
            return None
        if task.get("state") not in {"failed", "stopped"}:
            raise ValueError("只有失败或已停止的视觉任务可以重新运行。")
        if str(task.get("cleanupState") or "") == "failed":
            raise ValueError("优云实例清理失败，请先完成清理后再重试。")
        task.update(
            state="queued",
            status="queued",
            progress=0,
            currentStep="等待视觉训练资源",
            error=None,
            stopReason=None,
            cancelRequested=False,
            artifacts=[],
            lossHistory=[],
            bestStep=None,
            bestEvalLoss=None,
            finalStep=None,
            finalEvalLoss=None,
            workdir=None,
            configPath=None,
            logs=["任务已重新加入视觉训练队列。"],
        )
        for key in ("phase", "failureCode", "failureMessage", "suggestedAction"):
            task.pop(key, None)
        if str(task.get("provider") or "") == "youyun":
            now = time.time()
            budget = max(300.0, float(os.environ.get("XUNTUI_VISION_REMOTE_TASK_TIMEOUT_SECONDS", "3600")))
            task["lastHeartbeatAtEpoch"] = now
            task["expiresAtEpoch"] = now + budget
            task["remoteTimeoutSeconds"] = int(budget)
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
                    _busy_started = time.monotonic()
                    _busy_cap = max(60.0, float(os.environ.get("XUNTUI_RESOURCE_WAIT_TIMEOUT_SECONDS", "600")))
                    while self.busy_check is not None and self.busy_check():
                        if time.monotonic() - _busy_started > _busy_cap:
                            self.update(task_id, state="failed", status="failed", currentStep="等待共享资源超时。", error="共享执行资源长时间被占用，请检查是否有启动或训练流程挂起。")
                            raise RuntimeError("等待共享执行资源超时，任务转失败。")
                        await asyncio.sleep(0.8)
                    self._active_task_id = task_id
                    try:
                        if task.get("provider") == "youyun":
                            await self._run_remote(task)
                        else:
                            await self._run_local(task)
                    finally:
                        self._active_task_id = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - persist worker failures for UI recovery
                self.update(
                    task_id,
                    state="failed",
                    status="failed",
                    currentStep="视觉训练启动失败",
                    error=str(exc),
                )
            finally:
                self._queue.task_done()

    async def _run_local(self, task: Mapping[str, Any]) -> None:
        task_id = str(task["id"])
        self.update(task_id, state="preparing", status="preparing", currentStep="正在准备视觉训练环境", progress=0)
        if self.is_cancel_requested(task_id):
            self.update(task_id, state="stopped", status="stopped", currentStep="由用户停止", cancelRequested=False)
            return
        try:
            handle = await self.local_executor.prepare(task)
            self.update(task_id, workdir=handle.workdir, configPath=handle.config_path)
            if self.is_cancel_requested(task_id):
                self.update(
                    task_id,
                    state="stopped",
                    status="stopped",
                    currentStep="由用户停止",
                    cancelRequested=False,
                    stopReason="manual",
                )
                return
            started_handle = await self.local_executor.start(task)
            if started_handle is not None:
                handle = started_handle
            self.update(task_id, state="running", status="running", currentStep="训练进程已启动，等待首个训练指标")
            async for event in self.local_executor.events(task):
                await self._consume_event(task_id, event)
                if self.is_cancel_requested(task_id):
                    self.update(task_id, state="stopping", status="stopping", currentStep="正在停止视觉训练")
                    await self.local_executor.stop(task)
                    break
            handle_process = getattr(handle, "process", None)
            return_code = await handle_process.wait() if handle_process is not None else 0
            if self.is_cancel_requested(task_id):
                self.update(
                    task_id,
                    state="stopped",
                    status="stopped",
                    progress=min(100, int((self.get(task_id) or {}).get("progress", 0))),
                    currentStep="训练已停止",
                    cancelRequested=False,
                    stopReason="manual",
                )
                return
            if return_code != 0:
                self.update(
                    task_id,
                    state="failed",
                    status="failed",
                    currentStep="视觉训练失败",
                    error=f"LLaMA-Factory 进程返回码 {return_code}。",
                )
                return
            self.update(task_id, state="collecting", status="collecting", currentStep="正在收集视觉训练产物")
            artifacts = await self.local_executor.collect_artifacts(task)
            self.update(
                task_id,
                state="completed",
                status="completed",
                progress=100,
                currentStep="训练完成，视觉 Adapter 已保存",
                artifacts=artifacts,
                cancelRequested=False,
            )
            self._update_summary(task_id)
        except Exception as exc:  # noqa: BLE001 - expose actionable executor error
            self.append_log(task_id, f"视觉训练执行器错误：{exc}")
            self.update(task_id, state="failed", status="failed", currentStep="视觉训练失败", error=str(exc))

    @scoped_resource("task")
    async def _run_remote(self, task: Mapping[str, Any]) -> None:
        task_id = str(task["id"])
        self.update(task_id, state="preparing", status="preparing", currentStep="正在准备优云视觉训练", progress=0)
        if self.remote_runner_factory is None:
            self.update(
                task_id,
                state="failed",
                status="failed",
                currentStep="远程视觉训练未配置",
                error="未配置优云视觉训练执行器。",
                phase="instance_provision",
                failure_code="remote_training_failed",
                failure_message="优云远程视觉训练失败。",
                suggested_action="检查优云镜像、模型和远程训练执行器配置。",
            )
            return
        runner = self._remote_runner(task)
        outcome = await runner.run(task_id, task)
        current = self.get(task_id) or {}
        if outcome == "completed":
            self._update_summary(task_id)
            self.update(
                task_id,
                state="completed",
                status="completed",
                progress=100,
                currentStep=current.get("currentStep") or "训练完成",
                clear=("phase", "failureCode", "failureMessage", "suggestedAction"),
            )
        elif outcome == "stopped":
            self.update(
                task_id,
                state="stopped",
                status="stopped",
                currentStep=current.get("currentStep") or "训练已停止",
                cancelRequested=False,
                clear=("phase", "failureCode", "failureMessage", "suggestedAction"),
            )
        else:
            self.update(task_id, state="failed", status="failed", currentStep=current.get("currentStep") or "远程视觉训练失败")

    async def _consume_event(self, task_id: str, event: TrainingEvent) -> None:
        if event.message:
            self.append_log(task_id, event.message)
        if event.type == "metric":
            values = event.payload
            step = int(values.get("step") or 0)
            if step <= 0:
                previous = self.metrics(task_id)
                step = int(previous[-1].get("step", 0) if previous else 0) + 1
            self.append_metric(
                task_id,
                step=step,
                epoch=float(values["epoch"]) if values.get("epoch") is not None else None,
                train_loss=float(values["loss"]) if values.get("loss") is not None else None,
                eval_loss=float(values["eval_loss"]) if values.get("eval_loss") is not None else None,
                learning_rate=float(values["learning_rate"]) if values.get("learning_rate") is not None else None,
            )
            total_steps = int(values.get("total_steps") or 0)
            self.update(
                task_id,
                progress=min(95, int(values.get("progress", 0))),
                currentStep=(f"训练中 · Step {step}/{total_steps}" if total_steps else f"训练中 · Step {step}"),
                loss=float(values["loss"]) if values.get("loss") is not None else None,
            )
        elif event.type == "status" and event.message:
            self.update(task_id, currentStep=event.message)

    def _update_summary(self, task_id: str) -> None:
        task = self.get(task_id)
        if task is None:
            return
        metrics = list(task.get("lossHistory") or [])
        eval_metrics = [item for item in metrics if item.get("evalLoss") is not None]
        best = min(eval_metrics, key=lambda item: float(item["evalLoss"])) if eval_metrics else None
        final = max(metrics, key=lambda item: int(item.get("step", 0))) if metrics else None
        self.update(
            task_id,
            bestStep=best.get("step") if best else None,
            bestEvalLoss=float(best["evalLoss"]) if best else None,
            finalStep=final.get("step") if final else None,
            finalEvalLoss=float(final["evalLoss"]) if final and final.get("evalLoss") is not None else None,
        )


__all__ = ["VisionTrainingRuntime"]
