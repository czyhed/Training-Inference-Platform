from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.image_generation_runtime import ImageGenerationRuntime


class FakeProvider:
    def __init__(self, runtime: ImageGenerationRuntime) -> None:
        self.runtime = runtime
        self.started: list[str] = []
        self.stopped: list[str] = []

    async def start(self, task_id: str, task: dict, **kwargs):
        self.started.append(task_id)
        on_stage = kwargs.get("on_stage")
        if on_stage:
            on_stage("running", "开始生成图片。", {"progress": 40})
        return {"status": "completed", "results": [{"id": "0", "fileName": "0.png"}]}

    async def stop(self, handle):
        self.stopped.append(str(handle.get("id") if isinstance(handle, dict) else handle))
        return {"terminated": True}


class ImageGenerationRuntimeTests(unittest.TestCase):
    def test_create_task_records_lifecycle_budget_and_price_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ImageGenerationRuntime(
                task_root=Path(directory),
                execution_lock=asyncio.Lock(),
                provider_factory=lambda runtime: FakeProvider(runtime),
            )
            task = runtime.create_task({"id": "igt-lifecycle", "provider": "youyun", "resource": {"price": {"total": 1.2}}})
            self.assertIsInstance(task.get("phaseTimings"), dict)
            self.assertTrue(task.get("lastHeartbeatAt"))
            self.assertGreater(float(task.get("expiresAtEpoch") or 0), time.time())
            self.assertEqual(task.get("priceSnapshot"), {"total": 1.2})

    def test_confirmation_is_separate_from_creation_and_queued_stop(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                providers: list[FakeProvider] = []

                def factory(runtime):
                    provider = FakeProvider(runtime)
                    providers.append(provider)
                    return provider

                runtime = ImageGenerationRuntime(
                    task_root=Path(directory),
                    execution_lock=asyncio.Lock(),
                    provider_factory=factory,
                )
                created = runtime.create_task({"id": "igt-1", "prompt": "cat", "state": "awaiting_confirmation"})
                self.assertEqual(created["state"], "awaiting_confirmation")
                confirmed = await runtime.confirm("igt-1")
                self.assertEqual(confirmed["state"], "queued")
                self.assertEqual(confirmed["status"], "queued")
                self.assertEqual(confirmed["currentStep"], "等待优云任务执行")
                stopped = await runtime.request_stop("igt-1")
                self.assertEqual(stopped["state"], "stopped")
                self.assertFalse(providers)

        asyncio.run(scenario())

    def test_failed_task_can_retry_and_provider_updates_result(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                runtime = ImageGenerationRuntime(
                    task_root=Path(directory),
                    execution_lock=asyncio.Lock(),
                    provider_factory=lambda runtime: FakeProvider(runtime),
                )
                runtime.create_task({"id": "igt-2", "state": "failed", "status": "failed", "error": "bad"})
                queued = await runtime.retry("igt-2")
                self.assertEqual(queued["state"], "queued")
                await runtime.start()
                await asyncio.wait_for(runtime._queue.join(), timeout=1)
                task = runtime.get("igt-2")
                self.assertEqual(task["state"], "completed")
                self.assertEqual(task["results"][0]["fileName"], "0.png")
                await runtime.stop()

        asyncio.run(scenario())

    def test_restart_marks_active_tasks_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ImageGenerationRuntime(
                task_root=Path(directory),
                execution_lock=asyncio.Lock(),
                provider_factory=lambda runtime: FakeProvider(runtime),
            )
            runtime.create_task({"id": "igt-3", "state": "running", "status": "running"})
            asyncio.run(runtime.start())
            self.assertEqual(runtime.get("igt-3")["state"], "failed")
            asyncio.run(runtime.stop())

    def test_restart_cleans_persisted_remote_handle(self) -> None:
        class RecoveryProvider(FakeProvider):
            def __init__(self, runtime):
                super().__init__(runtime)
                self.cleaned: list[dict] = []

            async def cleanup_persisted(self, value):
                self.cleaned.append(dict(value))
                return {"terminated": True}

        providers: list[RecoveryProvider] = []

        def factory(runtime):
            provider = RecoveryProvider(runtime)
            providers.append(provider)
            return provider

        with tempfile.TemporaryDirectory() as directory:
            runtime = ImageGenerationRuntime(
                task_root=Path(directory),
                execution_lock=asyncio.Lock(),
                provider_factory=factory,
            )
            runtime.create_task({
                "id": "igt-restart-orphan",
                "state": "running",
                "status": "running",
                "remoteHandle": {"instanceId": "uhost-orphan"},
            })
            asyncio.run(runtime.start())
            task = runtime.get("igt-restart-orphan")
            self.assertEqual(task["cleanupState"], "released")
            self.assertNotIn("remoteHandle", task)
            self.assertEqual(providers[0].cleaned, [{"instanceId": "uhost-orphan"}])
            asyncio.run(runtime.stop())

    def test_rejects_unsafe_task_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ImageGenerationRuntime(
                task_root=Path(directory),
                execution_lock=asyncio.Lock(),
                provider_factory=lambda runtime: FakeProvider(runtime),
            )
            with self.assertRaises(ValueError):
                runtime.get("../escape")

    def test_cleanup_failure_is_persisted_without_claiming_release(self) -> None:
        class CleanupFailureProvider(FakeProvider):
            async def start(self, task_id: str, task: dict, **kwargs):
                return {"status": "completed", "results": [], "cleanupState": "failed", "cleanupError": "欠费无法删除"}

        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                runtime = ImageGenerationRuntime(task_root=Path(directory), execution_lock=asyncio.Lock(), provider_factory=lambda runtime: CleanupFailureProvider(runtime))
                runtime.create_task({"id": "igt-cleanup", "state": "failed", "status": "failed"})
                await runtime.retry("igt-cleanup")
                await runtime.start()
                await asyncio.wait_for(runtime._queue.join(), timeout=1)
                task = runtime.get("igt-cleanup")
                self.assertEqual(task["cleanupState"], "failed")
                self.assertEqual(task["cleanupError"], "欠费无法删除")
                self.assertIn("清理失败", task["currentStep"])
                await runtime.stop()

        asyncio.run(scenario())

    def test_cleanup_failure_blocks_rerun_and_stop_retries_cleanup(self) -> None:
        class CleanupProvider(FakeProvider):
            async def cleanup_persisted(self, value):
                return {"terminated": True}

        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                runtime = ImageGenerationRuntime(task_root=Path(directory), execution_lock=asyncio.Lock(), provider_factory=lambda runtime: CleanupProvider(runtime))
                runtime.create_task({"id": "igt-orphan", "state": "failed", "status": "failed", "cleanupState": "failed", "cleanupError": "orphan", "remoteHandle": {"instanceId": "uhost-1"}})
                with self.assertRaises(ValueError):
                    await runtime.retry("igt-orphan")
                cleaned = await runtime.request_stop("igt-orphan")
                self.assertEqual(cleaned["cleanupState"], "released")
                self.assertNotIn("cleanupError", cleaned)

        asyncio.run(scenario())

    def test_provider_handle_is_persisted_for_restart_cleanup(self) -> None:
        class PersistedHandle:
            def persisted(self):
                return {"instanceId": "uhost-persisted", "workdir": "/root/cppt/task"}

        class HandleProvider(FakeProvider):
            async def start(self, task_id: str, task: dict, **kwargs):
                kwargs["on_stage"]("running", "实例已创建。", {"handle": PersistedHandle(), "progress": 10})
                return {"status": "completed", "results": [], "cleanupState": "failed", "cleanupError": "cleanup failed"}

        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                runtime = ImageGenerationRuntime(task_root=Path(directory), execution_lock=asyncio.Lock(), provider_factory=lambda runtime: HandleProvider(runtime))
                runtime.create_task({"id": "igt-handle", "state": "failed", "status": "failed"})
                await runtime.retry("igt-handle")
                await runtime.start()
                await asyncio.wait_for(runtime._queue.join(), timeout=1)
                task = runtime.get("igt-handle")
                self.assertEqual(task["remoteHandle"]["instanceId"], "uhost-persisted")
                await runtime.stop()

        asyncio.run(scenario())

    def test_stage_callback_persists_phase_timestamps_and_heartbeat(self) -> None:
        test_case = self

        class StageProvider(FakeProvider):
            async def start(self, task_id: str, task: dict, **kwargs):
                kwargs["on_stage"]("preparing_model", "正在加载模型。", {"progress": 12})
                first = self.runtime.get(task_id)
                test_case.assertIsNotNone(first)
                test_case.assertEqual(first["phase"], "preparing_model")
                test_case.assertTrue(first.get("phaseUpdatedAt"))
                test_case.assertTrue(first.get("lastHeartbeatAt"))
                kwargs["on_log"]("模型加载进度正常。")
                return {"status": "completed", "results": []}

        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                runtime = ImageGenerationRuntime(task_root=Path(directory), execution_lock=asyncio.Lock(), provider_factory=lambda runtime: StageProvider(runtime))
                runtime.create_task({"id": "igt-telemetry", "state": "failed", "status": "failed"})
                await runtime.retry("igt-telemetry")
                await runtime.start()
                await asyncio.wait_for(runtime._queue.join(), timeout=1)
                task = runtime.get("igt-telemetry")
                self.assertEqual(task["phase"], "completed")
                self.assertTrue(task.get("phaseUpdatedAt"))
                self.assertTrue(task.get("lastHeartbeatAt"))
                self.assertTrue(task.get("cleanupCheckedAt"))
                await runtime.stop()

        asyncio.run(scenario())

    def test_runtime_logs_strip_terminal_controls_and_skip_consecutive_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ImageGenerationRuntime(
                task_root=Path(directory),
                execution_lock=asyncio.Lock(),
                provider_factory=lambda runtime: FakeProvider(runtime),
            )
            runtime.create_task({"id": "igt-clean-log", "state": "running"})
            runtime.append_log("igt-clean-log", "Loading 50%\x1b[A\r")
            runtime.append_log("igt-clean-log", "Loading 50%\x1b[A\r")

            task = runtime.get("igt-clean-log")
            self.assertEqual(len(task["logs"]), 1)
            self.assertNotIn("\x1b", task["logs"][0])
            self.assertNotIn("\r", task["logs"][0])
            self.assertIn("Loading 50%", task["logs"][0])

    def test_terminal_task_normalization_repairs_stale_phase_and_cleanup_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ImageGenerationRuntime(
                task_root=Path(directory),
                execution_lock=asyncio.Lock(),
                provider_factory=lambda runtime: FakeProvider(runtime),
            )
            runtime.create_task({
                "id": "igt-stale-terminal",
                "provider": "youyun",
                "state": "completed",
                "phase": "collecting",
                "cleanupState": "released",
                "updatedAt": "2026-09-01T16:42:20+0800",
                "logs": [
                    "[16:42:12] Loading 50%\x1b[A",
                    "[16:42:13] Loading 50%\x1b[A",
                    "[16:42:14] Loading 100%\x1b[A",
                ],
            })

            task = runtime.get("igt-stale-terminal")
            self.assertEqual(task["phase"], "completed")
            self.assertEqual(task["cleanupCheckedAt"], "2026-09-01T16:42:20+0800")
            self.assertEqual(len(task["logs"]), 2)
            self.assertNotIn("\x1b", "".join(task["logs"]))

    def test_provider_failure_is_classified_and_exposes_suggested_action(self) -> None:
        class FailedProvider(FakeProvider):
            async def start(self, task_id: str, task: dict, **kwargs):
                kwargs["on_stage"]("running", "开始生成图片。", {"progress": 40})
                return {
                    "status": "failed",
                    "error": "worker exited",
                    "failureCode": "generation_failed",
                    "failureMessage": "远端生成进程提前退出。",
                    "suggestedAction": "检查运行日志后重试。",
                }

        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                runtime = ImageGenerationRuntime(task_root=Path(directory), execution_lock=asyncio.Lock(), provider_factory=lambda runtime: FailedProvider(runtime))
                runtime.create_task({"id": "igt-failed", "state": "failed", "status": "failed"})
                await runtime.retry("igt-failed")
                await runtime.start()
                await asyncio.wait_for(runtime._queue.join(), timeout=1)
                task = runtime.get("igt-failed")
                self.assertEqual(task["failureCode"], "generation_failed")
                self.assertEqual(task["failureMessage"], "远端生成进程提前退出。")
                self.assertEqual(task["suggestedAction"], "检查运行日志后重试。")
                await runtime.stop()

        asyncio.run(scenario())

    def test_cleanup_failure_does_not_replace_primary_failure_classification(self) -> None:
        class FailedCleanupProvider(FakeProvider):
            async def start(self, task_id: str, task: dict, **kwargs):
                return {
                    "status": "failed",
                    "error": "generation failed",
                    "failureCode": "generation_failed",
                    "failureMessage": "图片生成失败。",
                    "suggestedAction": "检查模型和提示词后重试。",
                    "cleanupState": "failed",
                    "cleanupError": "优云实例清理失败。",
                }

        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                runtime = ImageGenerationRuntime(task_root=Path(directory), execution_lock=asyncio.Lock(), provider_factory=lambda runtime: FailedCleanupProvider(runtime))
                runtime.create_task({"id": "igt-primary", "state": "failed", "status": "failed"})
                await runtime.retry("igt-primary")
                await runtime.start()
                await asyncio.wait_for(runtime._queue.join(), timeout=1)
                task = runtime.get("igt-primary")
                self.assertEqual(task["failureCode"], "generation_failed")
                self.assertEqual(task["cleanupState"], "failed")
                self.assertEqual(task["cleanupError"], "优云实例清理失败。")
                await runtime.stop()

        asyncio.run(scenario())

    def test_restart_recovery_classifies_interrupted_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ImageGenerationRuntime(task_root=Path(directory), execution_lock=asyncio.Lock(), provider_factory=lambda runtime: FakeProvider(runtime))
            runtime.create_task({"id": "igt-restart", "state": "running", "status": "running"})
            asyncio.run(runtime.start())
            task = runtime.get("igt-restart")
            self.assertEqual(task["failureCode"], "runtime_interrupted")
            self.assertTrue(task.get("failureMessage"))
            self.assertTrue(task.get("suggestedAction"))
            asyncio.run(runtime.stop())

    def test_provider_error_without_safe_message_uses_generic_telemetry_text(self) -> None:
        class UnsafeErrorProvider(FakeProvider):
            async def start(self, task_id: str, task: dict, **kwargs):
                return {"status": "failed", "error": "secret remote command and token"}

        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                runtime = ImageGenerationRuntime(task_root=Path(directory), execution_lock=asyncio.Lock(), provider_factory=lambda runtime: UnsafeErrorProvider(runtime))
                runtime.create_task({"id": "igt-safe-error", "state": "failed", "status": "failed"})
                await runtime.retry("igt-safe-error")
                await runtime.start()
                await asyncio.wait_for(runtime._queue.join(), timeout=1)
                task = runtime.get("igt-safe-error")
                self.assertEqual(task["failureCode"], "generation_failed")
                self.assertNotIn("secret remote command", task["failureMessage"])
                self.assertNotIn("secret remote command", task["currentStep"])
                await runtime.stop()

        asyncio.run(scenario())

    def test_cleanup_retry_clears_cleanup_only_failure_fields(self) -> None:
        class CleanupProvider(FakeProvider):
            async def cleanup_persisted(self, value):
                return {"terminated": True}

        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                runtime = ImageGenerationRuntime(task_root=Path(directory), execution_lock=asyncio.Lock(), provider_factory=lambda runtime: CleanupProvider(runtime))
                runtime.create_task({
                    "id": "igt-cleanup-only",
                    "state": "failed",
                    "status": "failed",
                    "cleanupState": "failed",
                    "cleanupError": "orphan",
                    "remoteHandle": {"instanceId": "uhost-1"},
                    "failureCode": "cleanup_failed",
                    "failureMessage": "orphan",
                    "suggestedAction": "重试清理优云实例。",
                })
                cleaned = await runtime.request_stop("igt-cleanup-only")
                self.assertEqual(cleaned["cleanupState"], "released")
                self.assertNotIn("failureCode", cleaned)
                self.assertNotIn("failureMessage", cleaned)
                self.assertNotIn("suggestedAction", cleaned)

        asyncio.run(scenario())

    def test_cleanup_retry_failure_preserves_primary_suggested_action(self) -> None:
        class CleanupProvider(FakeProvider):
            async def cleanup_persisted(self, value):
                return {"terminated": False, "error": "quota"}

        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                runtime = ImageGenerationRuntime(task_root=Path(directory), execution_lock=asyncio.Lock(), provider_factory=lambda runtime: CleanupProvider(runtime))
                runtime.create_task({
                    "id": "igt-cleanup-primary",
                    "state": "failed",
                    "status": "failed",
                    "cleanupState": "failed",
                    "cleanupError": "orphan",
                    "remoteHandle": {"instanceId": "uhost-1"},
                    "failureCode": "generation_failed",
                    "failureMessage": "图片生成失败。",
                    "suggestedAction": "检查模型后重试。",
                })
                failed = await runtime.request_stop("igt-cleanup-primary")
                self.assertEqual(failed["failureCode"], "generation_failed")
                self.assertEqual(failed["suggestedAction"], "检查模型后重试。")

        asyncio.run(scenario())

    def test_unexpected_provider_exception_uses_generation_failed_taxonomy(self) -> None:
        class RaisingProvider(FakeProvider):
            async def start(self, task_id: str, task: dict, **kwargs):
                raise RuntimeError("secret traceback")

        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                runtime = ImageGenerationRuntime(task_root=Path(directory), execution_lock=asyncio.Lock(), provider_factory=lambda runtime: RaisingProvider(runtime))
                runtime.create_task({"id": "igt-raise", "state": "failed", "status": "failed"})
                await runtime.retry("igt-raise")
                await runtime.start()
                await asyncio.wait_for(runtime._queue.join(), timeout=1)
                task = runtime.get("igt-raise")
                self.assertEqual(task["failureCode"], "generation_failed")
                self.assertNotIn("secret traceback", task["failureMessage"])
                await runtime.stop()

        asyncio.run(scenario())

    def test_task_history_defaults_to_unarchived_and_supports_filters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ImageGenerationRuntime(
                task_root=Path(directory),
                execution_lock=asyncio.Lock(),
                provider_factory=lambda runtime: FakeProvider(runtime),
            )
            runtime.create_task({"id": "igt-a", "serviceId": "svc-a", "name": "Cat result", "prompt": "a cat", "state": "completed"})
            runtime.create_task({"id": "igt-b", "serviceId": "svc-b", "name": "Dog result", "prompt": "a dog", "state": "failed"})
            runtime.create_task({"id": "igt-c", "serviceId": "svc-a", "name": "Archived cat", "prompt": "a cat at night", "state": "stopped"})
            archived = runtime.archive_task("igt-c")

            self.assertFalse(runtime.get("igt-a").get("archived"))
            self.assertIsNone(runtime.get("igt-a").get("archivedAt"))
            self.assertTrue(archived["archived"])
            self.assertTrue(archived["archivedAt"])
            self.assertEqual({item["id"] for item in runtime.list_tasks()}, {"igt-a", "igt-b"})
            self.assertEqual([item["id"] for item in runtime.list_tasks(service_id="svc-a")], ["igt-a"])
            self.assertEqual([item["id"] for item in runtime.list_tasks(state="failed")], ["igt-b"])
            self.assertEqual([item["id"] for item in runtime.list_tasks(query="NIGHT", include_archived=True)], ["igt-c"])
            self.assertEqual({item["id"] for item in runtime.list_tasks(include_archived=True)}, {"igt-a", "igt-b", "igt-c"})

    def test_clone_copies_configuration_without_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ImageGenerationRuntime(
                task_root=Path(directory),
                execution_lock=asyncio.Lock(),
                provider_factory=lambda runtime: FakeProvider(runtime),
            )
            source = runtime.create_task({
                "id": "igt-source",
                "name": "Source task",
                "serviceId": "svc-1",
                "modelId": "model-1",
                "modelRepoId": "acme/flux",
                "adapterLineage": {"variant": "best"},
                "adapterPath": "/models/adapter",
                "resource": {"gpuType": "A10"},
                "prompt": "original prompt",
                "negativePrompt": "blur",
                "width": 512,
                "height": 512,
                "steps": 20,
                "cfg": 7,
                "batchSize": 1,
                "seed": 42,
                "sampler": "euler",
                "state": "failed",
                "results": [{"id": "0", "fileName": "0.png"}],
                "logs": ["old log"],
                "remoteHandle": {"instanceId": "uhost-1"},
                "failureCode": "generation_failed",
                "failureMessage": "old failure",
                "cleanupState": "released",
                "cancelRequested": True,
                "phase": "failed",
                "phaseUpdatedAt": "old",
                "lastHeartbeatAt": "old",
            })
            clone = runtime.clone_task(source["id"], {"prompt": "edited prompt", "steps": 30})

            self.assertIsNotNone(clone)
            self.assertNotEqual(clone["id"], source["id"])
            self.assertEqual(clone["state"], "awaiting_confirmation")
            self.assertEqual(clone["status"], "draft")
            self.assertEqual(clone["prompt"], "edited prompt")
            self.assertEqual(clone["steps"], 30)
            self.assertEqual(clone["serviceId"], "svc-1")
            self.assertEqual(clone["modelRepoId"], "acme/flux")
            self.assertEqual(clone["results"], [])
            self.assertEqual(clone["logs"], [])
            for key in ("remoteHandle", "failureCode", "failureMessage", "cleanupState", "cancelRequested", "phase", "phaseUpdatedAt", "lastHeartbeatAt"):
                self.assertNotIn(key, clone)
            self.assertEqual(runtime.get(source["id"])["prompt"], "original prompt")

    def test_archive_restore_and_delete_enforce_task_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = ImageGenerationRuntime(
                task_root=root / "tasks",
                execution_lock=asyncio.Lock(),
                provider_factory=lambda runtime: FakeProvider(runtime),
            )
            runtime.create_task({"id": "igt-active", "state": "running"})
            with self.assertRaises(ValueError):
                runtime.archive_task("igt-active")
            with self.assertRaises(ValueError):
                runtime.delete_task("igt-active")

            task_root = root / "results" / "igt-done"
            task_root.mkdir(parents=True)
            (task_root / "0.png").write_bytes(b"png")
            runtime.create_task({"id": "igt-done", "state": "completed", "resultRoot": str(task_root)})
            archived = runtime.archive_task("igt-done")
            self.assertTrue(archived["archived"])
            with self.assertRaises(ValueError):
                runtime.archive_task("igt-done")
            restored = runtime.restore_task("igt-done")
            self.assertFalse(restored["archived"])
            self.assertIsNone(restored["archivedAt"])
            self.assertTrue(runtime.delete_task("igt-done"))
            self.assertIsNone(runtime.get("igt-done"))
            self.assertFalse(task_root.exists())

    def test_delete_keeps_task_when_result_cleanup_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = ImageGenerationRuntime(
                task_root=root / "tasks",
                execution_lock=asyncio.Lock(),
                provider_factory=lambda runtime: FakeProvider(runtime),
            )
            result_root = root / "results" / "igt-cleanup"
            result_root.mkdir(parents=True)
            runtime.create_task({"id": "igt-cleanup", "state": "completed", "resultRoot": str(result_root)})
            with patch("shutil.rmtree", side_effect=OSError("busy")):
                with self.assertRaises(OSError):
                    runtime.delete_task("igt-cleanup")
            self.assertIsNotNone(runtime.get("igt-cleanup"))


if __name__ == "__main__":
    unittest.main()
