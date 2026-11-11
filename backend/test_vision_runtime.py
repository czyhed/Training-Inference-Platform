from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from backend.training_executor import ExecutionHandle, TrainingEvent
from backend.vision_runtime import VisionTrainingRuntime


class _Process:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.waited = False

    async def wait(self) -> int:
        self.waited = True
        return self.returncode


class _Executor:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.stopped = False
        self.started_process: _Process | None = None

    async def prepare(self, task):
        workdir = self.root / task["id"]
        workdir.mkdir(parents=True, exist_ok=True)
        return ExecutionHandle(task_id=task["id"], workdir=str(workdir), config_path=str(workdir / "train.yaml"), prepared=True)

    async def start(self, task):
        self.started_process = _Process()
        return SimpleNamespace(process=self.started_process)

    async def stop(self, task):
        self.stopped = True

    async def events(self, task):
        yield TrainingEvent("log", "***** Running training *****")
        yield TrainingEvent("metric", "{'loss': 1.2, 'epoch': 1.0}", {"loss": 1.2, "epoch": 1.0, "step": 1, "total_steps": 2, "progress": 45})
        yield TrainingEvent("metric", "{'loss': 0.8, 'eval_loss': 0.9, 'epoch': 2.0}", {"loss": 0.8, "eval_loss": 0.9, "epoch": 2.0, "step": 2, "total_steps": 2, "progress": 95})

    async def collect_artifacts(self, task):
        return [{"type": "adapter", "variant": "final", "selected": True, "path": str(self.root / task["id"] / "output")}]


class VisionTrainingRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_remote_runner_factory_receives_selected_youyun_resource(self) -> None:
        """Remote execution must use the task's resolved region/zone, not env defaults."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            received: list[dict] = []

            class _RemoteRunner:
                async def run(self, task_id, task):
                    return "failed"

            def runner_factory(sink, task):
                received.append(dict(task.get("youyunResource") or {}))
                return _RemoteRunner()

            runtime = VisionTrainingRuntime(
                task_root=root / "tasks",
                local_executor=_Executor(root / "work"),
                execution_lock=asyncio.Lock(),
                remote_runner_factory=runner_factory,
            )
            runtime.save(
                {
                    "id": "vt-region-propagation",
                    "state": "queued",
                    "status": "queued",
                    "provider": "youyun",
                    "youyunResource": {
                        "region": "cn-sh2",
                        "zone": "cn-sh2-02",
                        "chargeType": "Spot",
                    },
                    "createdAt": "2026-09-05T00:00:00+0800",
                }
            )
            await runtime.start()
            for _ in range(30):
                task = runtime.get("vt-region-propagation")
                if task and task.get("state") == "failed":
                    break
                await asyncio.sleep(0.02)
            self.assertEqual(received, [{"region": "cn-sh2", "zone": "cn-sh2-02", "chargeType": "Spot"}])
            await runtime.stop()

    async def test_remote_failure_diagnostics_survive_runtime_state_update(self) -> None:
        class _RemoteRunner:
            def __init__(self, sink):
                self.sink = sink

            async def run(self, task_id, task):
                self.sink.update(
                    task_id,
                    failure_code="base_model_unavailable",
                    failure_message="优云视觉基座模型不可用。",
                    suggested_action="将模型预置到镜像或共享缓存。",
                    phase="model_resolution",
                )
                return "failed"

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = None

            def runner_factory(sink):
                return _RemoteRunner(sink)

            runtime = VisionTrainingRuntime(
                task_root=root / "tasks",
                local_executor=_Executor(root / "work"),
                execution_lock=asyncio.Lock(),
                remote_runner_factory=runner_factory,
            )
            runtime.save({
                "id": "vt-remote-failure",
                "state": "queued",
                "status": "queued",
                "provider": "youyun",
                "createdAt": "2026-09-02T00:00:00+0800",
            })
            await runtime.start()
            for _ in range(30):
                task = runtime.get("vt-remote-failure")
                if task and task.get("state") == "failed":
                    break
                await asyncio.sleep(0.02)
            task = runtime.get("vt-remote-failure")
            assert task is not None
            self.assertEqual(task["failureCode"], "base_model_unavailable")
            self.assertEqual(task["failureMessage"], "优云视觉基座模型不可用。")
            self.assertEqual(task["suggestedAction"], "将模型预置到镜像或共享缓存。")
            self.assertEqual(task["phase"], "model_resolution")
            await runtime.stop()

    async def test_local_task_persists_metrics_logs_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executor = _Executor(root / "work")
            runtime = VisionTrainingRuntime(
                task_root=root / "tasks",
                local_executor=executor,
                execution_lock=asyncio.Lock(),
            )
            runtime.save(
                {
                    "id": "vt-test",
                    "name": "视觉运行测试",
                    "state": "queued",
                    "status": "queued",
                    "provider": "local",
                    "progress": 0,
                    "currentStep": "等待视觉训练资源",
                    "createdAt": "2026-08-26T00:00:00+0800",
                    "parameters": {},
                    "artifacts": [],
                }
            )
            await runtime.start()
            for _ in range(30):
                task = runtime.get("vt-test")
                if task and task.get("state") == "completed":
                    break
                await asyncio.sleep(0.02)
            task = runtime.get("vt-test")
            self.assertIsNotNone(task)
            assert task is not None
            self.assertEqual(task["state"], "completed")
            self.assertEqual(task["progress"], 100)
            self.assertEqual(len(task["lossHistory"]), 2)
            self.assertEqual(task["bestStep"], 2)
            self.assertEqual(task["bestEvalLoss"], 0.9)
            self.assertEqual(task["finalStep"], 2)
            self.assertTrue(task["logs"])
            self.assertEqual(task["artifacts"][0]["type"], "adapter")
            assert executor.started_process is not None
            self.assertTrue(executor.started_process.waited)
            await runtime.stop()

    async def test_remote_runner_field_names_are_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = VisionTrainingRuntime(
                task_root=root / "tasks",
                local_executor=_Executor(root / "work"),
                execution_lock=asyncio.Lock(),
            )
            runtime.save({"id": "vt-remote", "state": "running", "createdAt": "2026-08-26T00:00:00+0800"})
            runtime.update(
                "vt-remote",
                current_step="远程训练中",
                cancel_requested=True,
                stop_reason="manual",
            )
            task = runtime.get("vt-remote")
            assert task is not None
            self.assertEqual(task["currentStep"], "远程训练中")
            self.assertTrue(task["cancelRequested"])
            self.assertEqual(task["stopReason"], "manual")
            self.assertNotIn("current_step", task)
            self.assertNotIn("cancel_requested", task)
            await runtime.stop()

    async def test_retry_requeues_failed_task(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = VisionTrainingRuntime(
                task_root=root / "tasks",
                local_executor=_Executor(root / "work"),
                execution_lock=asyncio.Lock(),
            )
            runtime.save({
                "id": "vt-failed",
                "state": "failed",
                "status": "failed",
                "createdAt": "2026-08-26T00:00:00+0800",
                "phase": "dependency_install",
                "failureCode": "dependency_install_failed",
                "failureMessage": "优云视觉训练依赖安装失败。",
                "suggestedAction": "使用预装视觉训练环境的镜像。",
            })
            await runtime.start()
            task = await runtime.retry("vt-failed")
            self.assertIsNotNone(task)
            assert task is not None
            self.assertEqual(task["state"], "queued")
            self.assertNotIn("phase", task)
            self.assertNotIn("failureCode", task)
            self.assertNotIn("failureMessage", task)
            self.assertNotIn("suggestedAction", task)
            await runtime.stop()

    async def test_remote_task_defaults_resource_and_cleanup_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = VisionTrainingRuntime(
                task_root=root / "tasks",
                local_executor=_Executor(root / "work"),
                execution_lock=asyncio.Lock(),
            )
            task = runtime.save({
                "id": "vt-remote-contract",
                "provider": "youyun",
                "state": "running",
                "remoteHandle": {"instanceId": "uhost-1"},
            })
            self.assertEqual(task["resourceState"], "running")
            self.assertEqual(task["cleanupState"], "pending")

    async def test_failed_provision_without_instance_is_normalized_to_released(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = VisionTrainingRuntime(
                task_root=root / "tasks",
                local_executor=_Executor(root / "work"),
                execution_lock=asyncio.Lock(),
            )
            task = runtime.save({
                "id": "vt-create-unavailable",
                "provider": "youyun",
                "state": "failed",
                "phase": "instance_provision",
                "failureCode": "youyun_create_service_unavailable",
                "cleanupState": "pending",
                "resourceState": "provisioning",
                "artifacts": [],
            })
            self.assertEqual(task["resourceState"], "released")
            self.assertEqual(task["cleanupState"], "released")

    async def test_terminal_state_syncs_execution_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = VisionTrainingRuntime(
                task_root=root / "tasks",
                local_executor=_Executor(root / "work"),
                execution_lock=asyncio.Lock(),
            )
            runtime.save({"id": "vt-terminal", "state": "queued", "status": "queued"})
            failed = runtime.update(
                "vt-terminal", state="failed", status="failed", current_step="训练失败"
            )
            assert failed is not None
            self.assertEqual(failed["executionStatus"], "failed")
            completed = runtime.update(
                "vt-terminal", state="completed", status="completed", progress=100
            )
            assert completed is not None
            self.assertEqual(completed["executionStatus"], "completed")

    async def test_retry_is_blocked_when_remote_cleanup_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = VisionTrainingRuntime(
                task_root=root / "tasks",
                local_executor=_Executor(root / "work"),
                execution_lock=asyncio.Lock(),
            )
            runtime.save({
                "id": "vt-cleanup-failed",
                "state": "failed",
                "status": "failed",
                "provider": "youyun",
                "remoteHandle": {"instanceId": "uhost-1"},
                "cleanupState": "failed",
            })
            with self.assertRaisesRegex(ValueError, "清理失败"):
                await runtime.retry("vt-cleanup-failed")

    async def test_force_cleanup_remote_task_releases_instance_and_persists_state(self) -> None:
        class _Manager:
            def cleanup_after_task(self, instance_id: str, *, stop_first: bool = True):
                self.instance_id = instance_id
                self.stop_first = stop_first
                return {"terminated": True, "checkedAt": "2026-09-03T16:00:00+0800"}

        class _RemoteRunner:
            def __init__(self, sink, manager):
                self.sink = sink
                self.manager = manager

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = _Manager()
            runtime = VisionTrainingRuntime(
                task_root=root / "tasks",
                local_executor=_Executor(root / "work"),
                execution_lock=asyncio.Lock(),
                remote_runner_factory=lambda sink: _RemoteRunner(sink, manager),
            )
            runtime.save({
                "id": "vt-force-cleanup",
                "provider": "youyun",
                "state": "running",
                "status": "running",
                "artifacts": [{
                    "type": "youyun-instance",
                    "provider": "youyun",
                    "instanceId": "uhost-force",
                    "state": "running",
                }],
                "cleanupError": "UHost is in the task",
                "createdAt": "2026-09-03T15:00:00+0800",
            })

            task = await runtime.force_cleanup_remote_task(
                "vt-force-cleanup",
                reason="自动超时清理",
                failure_code="remote_task_timeout",
                failure_message="优云视觉训练超时。",
                suggested_action="检查日志后重试。",
            )

            assert task is not None
            marker = next(item for item in task["artifacts"] if item.get("type") == "youyun-instance")
            self.assertEqual(manager.instance_id, "uhost-force")
            self.assertFalse(manager.stop_first)
            self.assertEqual(marker["state"], "released")
            self.assertEqual(task["cleanupState"], "released")
            self.assertEqual(task["resourceState"], "released")
            self.assertIn("自动超时清理", task["currentStep"])
            self.assertEqual(task["state"], "failed")
            self.assertEqual(task["failureCode"], "remote_task_timeout")
            self.assertNotIn("cleanupError", task)

    async def test_force_cleanup_already_released_refreshes_current_step(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = VisionTrainingRuntime(
                task_root=root / "tasks",
                local_executor=_Executor(root / "work"),
                execution_lock=asyncio.Lock(),
            )
            runtime.save({
                "id": "vt-released-step",
                "provider": "youyun",
                "state": "stopping",
                "status": "stopping",
                "currentStep": "清理失败重试自动清理：优云实例释放失败",
                "artifacts": [{
                    "type": "youyun-instance",
                    "provider": "youyun",
                    "instanceId": "cpod-gone",
                    "state": "released",
                }],
            })

            task = await runtime.force_cleanup_remote_task("vt-released-step", reason="手动释放")

            assert task is not None
            self.assertEqual(task["cleanupState"], "released")
            self.assertEqual(task["resourceState"], "released")
            self.assertEqual(task["currentStep"], "手动释放：优云实例已释放")

    async def test_remote_cleanup_telemetry_uses_camel_case_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = VisionTrainingRuntime(
                task_root=root / "tasks",
                local_executor=_Executor(root / "work"),
                execution_lock=asyncio.Lock(),
            )
            runtime.save({
                "id": "vt-cleanup-telemetry",
                "provider": "youyun",
                "state": "running",
                "remoteHandle": {"instanceId": "uhost-1"},
            })
            runtime.update(
                "vt-cleanup-telemetry",
                resource_state="released",
                cleanup_state="released",
                cleanup_error=None,
                cleanup_checked_at="2026-09-02T00:00:00+0800",
                failure_code="cleanup_failed",
                failure_message="旧的清理失败",
                suggested_action="重试清理",
            )
            task = runtime.get("vt-cleanup-telemetry")
            assert task is not None
            self.assertEqual(task["resourceState"], "released")
            self.assertEqual(task["cleanupState"], "released")
            self.assertEqual(task["cleanupCheckedAt"], "2026-09-02T00:00:00+0800")
            self.assertEqual(task["failureCode"], "cleanup_failed")
            self.assertNotIn("resource_state", task)
            self.assertNotIn("cleanup_state", task)

    async def test_legacy_config_task_requires_confirmation_after_runtime_start(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = VisionTrainingRuntime(
                task_root=root / "tasks",
                local_executor=_Executor(root / "work"),
                execution_lock=asyncio.Lock(),
            )
            runtime.save(
                {
                    "id": "vt-legacy",
                    "state": "awaiting_executor",
                    "status": "configured",
                    "currentStep": "配置已生成，等待视觉训练执行器接入",
                    "createdAt": "2026-08-26T00:00:00+0800",
                    "logs": [],
                }
            )
            await runtime.start()
            task = runtime.get("vt-legacy")
            self.assertIsNotNone(task)
            assert task is not None
            self.assertEqual(task["state"], "awaiting_confirmation")
            self.assertEqual(task["status"], "draft")
            self.assertEqual(task["currentStep"], "等待确认后提交训练")
            self.assertEqual(task["executionStatus"], "awaiting_confirmation")
            self.assertIn("历史视觉任务已迁移为待确认状态", task["logs"][-1])
            await runtime.stop()

    async def test_start_releases_orphaned_remote_instance_after_restart(self) -> None:
        class _Manager:
            def __init__(self) -> None:
                self.calls: list[tuple[str, bool]] = []

            def cleanup_after_task(self, instance_id: str, *, stop_first: bool = True):
                self.calls.append((instance_id, stop_first))
                return {"terminated": True, "checkedAt": "2026-09-03T12:00:00+0800"}

        class _RemoteRunner:
            def __init__(self, sink, manager):
                self.sink = sink
                self.manager = manager

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = _Manager()
            runtime = VisionTrainingRuntime(
                task_root=root / "tasks",
                local_executor=_Executor(root / "work"),
                execution_lock=asyncio.Lock(),
                remote_runner_factory=lambda sink: _RemoteRunner(sink, manager),
            )
            runtime.save({
                "id": "vt-orphan",
                "state": "failed",
                "status": "failed",
                "provider": "youyun",
                "currentStep": "后端重启，视觉训练未完成",
                "artifacts": [{
                    "type": "youyun-instance",
                    "provider": "youyun",
                    "instanceId": "uhost-orphan",
                    "state": "running",
                }],
                "createdAt": "2026-09-03T11:00:00+0800",
            })

            await runtime.start()
            task = runtime.get("vt-orphan")
            assert task is not None
            self.assertEqual(manager.calls, [("uhost-orphan", False)])
            marker = next(item for item in task["artifacts"] if item.get("type") == "youyun-instance")
            self.assertEqual(marker["state"], "released")
            self.assertEqual(task["cleanupState"], "released")
            self.assertEqual(task["resourceState"], "released")
            self.assertTrue(any("重启恢复清理" in log for log in task["logs"]))
            await runtime.stop()

    async def test_start_records_cleanup_failure_and_keeps_instance_handle(self) -> None:
        class _Manager:
            def cleanup_after_task(self, instance_id: str, *, stop_first: bool = True):
                return {"terminated": False, "error": "删除接口超时", "checkedAt": "2026-09-03T12:00:00+0800"}

        class _RemoteRunner:
            def __init__(self, sink):
                self.sink = sink
                self.manager = _Manager()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = VisionTrainingRuntime(
                task_root=root / "tasks",
                local_executor=_Executor(root / "work"),
                execution_lock=asyncio.Lock(),
                remote_runner_factory=lambda sink: _RemoteRunner(sink),
            )
            runtime.save({
                "id": "vt-orphan-failed-cleanup",
                "state": "failed",
                "status": "failed",
                "provider": "youyun",
                "artifacts": [{
                    "type": "youyun-instance",
                    "provider": "youyun",
                    "instanceId": "uhost-stuck",
                    "state": "running",
                }],
                "createdAt": "2026-09-03T11:00:00+0800",
            })

            await runtime.start()
            task = runtime.get("vt-orphan-failed-cleanup")
            assert task is not None
            marker = next(item for item in task["artifacts"] if item.get("type") == "youyun-instance")
            self.assertEqual(marker["state"], "cleanup_failed")
            self.assertEqual(marker["cleanupError"], "删除接口超时")
            self.assertEqual(task["cleanupState"], "failed")
            self.assertEqual(task["resourceState"], "cleanup_failed")
            self.assertEqual(task["cleanupError"], "删除接口超时")
            await runtime.stop()


if __name__ == "__main__":
    unittest.main()
