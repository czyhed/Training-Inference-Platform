from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from backend.image_generation_executor import FakeImageGenerationTrainingExecutor
from backend.image_generation_training_runtime import ImageGenerationTrainingRuntime
from backend.training_executor import TrainingEvent


class ImageGenerationTrainingRuntimeTests(unittest.TestCase):
    def test_remote_task_records_lifecycle_budget_and_price_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ImageGenerationTrainingRuntime(
                task_root=Path(directory),
                execution_lock=asyncio.Lock(),
                executor_factory=lambda runtime: FakeImageGenerationTrainingExecutor(),
            )
            task = runtime.create_task({"id": "igt-lifecycle", "provider": "youyun", "resource": {"price": {"total": 2.5}}})
            self.assertIsInstance(task.get("phaseTimings"), dict)
            self.assertTrue(task.get("lastHeartbeatAt"))
            self.assertGreater(float(task.get("lastHeartbeatAtEpoch") or 0), 0)
            self.assertGreater(float(task.get("expiresAtEpoch") or 0), time.time())
            self.assertEqual(task.get("priceSnapshot"), {"total": 2.5})

    def test_remote_task_defaults_resource_and_cleanup_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ImageGenerationTrainingRuntime(
                task_root=Path(directory),
                execution_lock=asyncio.Lock(),
                executor_factory=lambda runtime: FakeImageGenerationTrainingExecutor(),
            )
            task = runtime.create_task({
                "id": "igt-remote-contract",
                "provider": "youyun",
                "state": "running",
                "remoteHandle": {"instanceId": "uhost-1"},
            })
            self.assertEqual(task["resourceState"], "running")
            self.assertEqual(task["cleanupState"], "pending")

    def test_failed_provision_without_instance_is_normalized_as_released(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ImageGenerationTrainingRuntime(
                task_root=Path(directory),
                execution_lock=asyncio.Lock(),
                executor_factory=lambda runtime: FakeImageGenerationTrainingExecutor(),
            )
            task = runtime.create_task({
                "id": "igt-provision-failed",
                "provider": "youyun",
                "state": "failed",
                "status": "failed",
                "phase": "instance_provision",
                "failureCode": "youyun_create_service_unavailable",
            })

            self.assertEqual(task["resourceState"], "released")
            self.assertEqual(task["cleanupState"], "released")
            self.assertTrue(task.get("cleanupCheckedAt"))

    def test_retry_is_blocked_when_remote_cleanup_failed(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                runtime = ImageGenerationTrainingRuntime(
                    task_root=Path(directory),
                    execution_lock=asyncio.Lock(),
                    executor_factory=lambda runtime: FakeImageGenerationTrainingExecutor(),
                )
                runtime.create_task({
                    "id": "igt-cleanup-failed",
                    "state": "failed",
                    "status": "failed",
                    "provider": "youyun",
                    "remoteHandle": {"instanceId": "uhost-1"},
                    "cleanupState": "failed",
                })
                with self.assertRaisesRegex(ValueError, "清理失败"):
                    await runtime.retry("igt-cleanup-failed")
        asyncio.run(scenario())

    def test_delete_removes_task_record_and_owned_training_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            preview = root / "image-generation-training-previews" / "preview-delete"
            output = preview / "output" / "best"
            output.mkdir(parents=True)
            (output / "pytorch_lora_weights.safetensors").write_bytes(b"weights")
            runtime = ImageGenerationTrainingRuntime(
                task_root=root / "image-generation-training-tasks",
                execution_lock=asyncio.Lock(),
                executor_factory=lambda runtime: FakeImageGenerationTrainingExecutor(),
            )
            runtime.create_task({
                "id": "igt-delete",
                "state": "failed",
                "previewRoot": str(preview),
                "outputDir": str(preview / "output"),
                "artifacts": [{"type": "adapter", "variant": "best", "path": str(output)}],
            })

            self.assertTrue(runtime.delete_task("igt-delete"))

            self.assertIsNone(runtime.get("igt-delete"))
            self.assertFalse(preview.exists())

    def test_delete_rejects_training_directory_outside_managed_runtime_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as external_directory:
            root = Path(directory)
            external = Path(external_directory)
            (external / "keep.txt").write_text("keep", encoding="utf-8")
            runtime = ImageGenerationTrainingRuntime(
                task_root=root / "image-generation-training-tasks",
                execution_lock=asyncio.Lock(),
                executor_factory=lambda runtime: FakeImageGenerationTrainingExecutor(),
            )
            runtime.create_task({"id": "igt-unsafe-delete", "state": "failed", "previewRoot": str(external)})

            with self.assertRaisesRegex(ValueError, "受控"):
                runtime.delete_task("igt-unsafe-delete")

            self.assertIsNotNone(runtime.get("igt-unsafe-delete"))
            self.assertTrue((external / "keep.txt").is_file())

    def test_confirm_runs_fake_training_and_persists_adapters(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                runtime = ImageGenerationTrainingRuntime(
                    task_root=Path(directory),
                    execution_lock=asyncio.Lock(),
                    executor_factory=lambda runtime: FakeImageGenerationTrainingExecutor(),
                )
                runtime.create_task({"id": "igt-1", "datasetId": "ds-1", "modelRepoId": "stabilityai/stable-diffusion-xl-base-1.0", "previewRoot": directory})
                confirmed = await runtime.confirm("igt-1")
                self.assertEqual(confirmed["state"], "queued")
                await runtime.start()
                await asyncio.wait_for(runtime._queue.join(), timeout=1)
                task = runtime.get("igt-1")
                self.assertEqual(task["state"], "completed")
                self.assertEqual({item["variant"] for item in task["artifacts"]}, {"best", "final"})
                self.assertTrue(task["metrics"])
                await runtime.stop()

        asyncio.run(scenario())

    def test_stop_queued_task_does_not_start_executor(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                started: list[str] = []

                class Executor(FakeImageGenerationTrainingExecutor):
                    async def run(self, task, **kwargs):
                        started.append(str(task["id"]))
                        return await super().run(task, **kwargs)

                runtime = ImageGenerationTrainingRuntime(task_root=Path(directory), execution_lock=asyncio.Lock(), executor_factory=lambda runtime: Executor())
                runtime.create_task({"id": "igt-2"})
                await runtime.confirm("igt-2")
                stopped = await runtime.request_stop("igt-2")
                self.assertEqual(stopped["state"], "stopped")
                await runtime.start()
                await asyncio.sleep(0)
                self.assertEqual(started, [])
                await runtime.stop()

        asyncio.run(scenario())

    def test_failed_task_can_retry_and_archived_actions_are_blocked(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                runtime = ImageGenerationTrainingRuntime(task_root=Path(directory), execution_lock=asyncio.Lock(), executor_factory=lambda runtime: FakeImageGenerationTrainingExecutor())
                runtime.create_task({"id": "igt-3", "state": "failed", "status": "failed", "error": "raw"})
                queued = await runtime.retry("igt-3")
                self.assertEqual(queued["state"], "queued")
                await runtime.start()
                await asyncio.wait_for(runtime._queue.join(), timeout=1)
                archived = await runtime.archive_task("igt-3")
                self.assertTrue(archived["archived"])
                with self.assertRaises(ValueError):
                    await runtime.retry("igt-3")
                restored = await runtime.restore_task("igt-3")
                self.assertFalse(restored["archived"])
                await runtime.stop()

        asyncio.run(scenario())

    def test_diagnostics_prefers_safe_failure_message(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ImageGenerationTrainingRuntime(task_root=Path(directory), execution_lock=asyncio.Lock(), executor_factory=lambda runtime: FakeImageGenerationTrainingExecutor())
            runtime.create_task({"id": "igt-4", "state": "failed", "failureCode": "training_failed", "failureMessage": "训练执行失败。", "error": "secret command"})
            diagnostics = runtime.diagnostics("igt-4")
            self.assertEqual(diagnostics["failure"], {"code": "training_failed", "message": "训练执行失败。"})
            self.assertNotIn("secret command", str(diagnostics))

    def test_diagnostics_includes_attempt_stage_timings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ImageGenerationTrainingRuntime(task_root=Path(directory), execution_lock=asyncio.Lock(), executor_factory=lambda runtime: FakeImageGenerationTrainingExecutor())
            runtime.create_task({
                "id": "igt-diagnostics-timing",
                "state": "failed",
                "attempts": [{"number": 1, "status": "failed", "durationSeconds": 42, "stages": [{"message": "连接 SSH", "durationSeconds": 18}]}],
            })
            diagnostics = runtime.diagnostics("igt-diagnostics-timing")
            self.assertEqual(diagnostics["attempts"][0]["durationSeconds"], 42)
            self.assertEqual(diagnostics["attempts"][0]["stages"][0]["durationSeconds"], 18)

    def test_worker_persists_unexpected_executor_error_for_diagnostics(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                class BrokenExecutor:
                    async def run(self, task, **kwargs):
                        raise RuntimeError("优云实例在启动阶段进入 Stopped 状态")

                runtime = ImageGenerationTrainingRuntime(
                    task_root=Path(directory),
                    execution_lock=asyncio.Lock(),
                    executor_factory=lambda runtime: BrokenExecutor(),
                )
                runtime.create_task({"id": "igt-error"})
                await runtime.confirm("igt-error")
                await runtime.start()
                await asyncio.wait_for(runtime._queue.join(), timeout=1)
                task = runtime.get("igt-error")
                self.assertEqual(task["state"], "failed")
                self.assertEqual(task["error"], "优云实例在启动阶段进入 Stopped 状态")
                await runtime.stop()

        asyncio.run(scenario())

    def test_worker_cleans_remote_instance_after_unexpected_executor_error(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                cleaned: list[str] = []

                class BrokenExecutor:
                    async def run(self, task, **kwargs):
                        on_event = kwargs["on_event"]
                        on_event(TrainingEvent(
                            "stage",
                            "优云训练实例已就绪。",
                            {
                                "phase": "ssh_connect",
                                "progress": 5,
                                "remoteHandle": {"instanceId": "uhost-fallback-cleanup"},
                            },
                        ))
                        raise RuntimeError("executor crashed outside provider guard")

                    async def cleanup_remote(self, task):
                        cleaned.append(task["remoteHandle"]["instanceId"])
                        return {"cleanupState": "completed", "cleanupError": None}

                runtime = ImageGenerationTrainingRuntime(
                    task_root=Path(directory),
                    execution_lock=asyncio.Lock(),
                    executor_factory=lambda runtime: BrokenExecutor(),
                )
                runtime.create_task({"id": "igt-unexpected-cleanup", "provider": "youyun"})
                await runtime.confirm("igt-unexpected-cleanup")
                await runtime.start()
                await asyncio.wait_for(runtime._queue.join(), timeout=1)

                task = runtime.get("igt-unexpected-cleanup")
                self.assertEqual(cleaned, ["uhost-fallback-cleanup"])
                self.assertEqual(task["state"], "failed")
                self.assertEqual(task["phase"], "ssh_connect")
                self.assertEqual(task["resourceState"], "released")
                self.assertEqual(task["cleanupState"], "released")
                await runtime.stop()

        asyncio.run(scenario())

    def test_force_cleanup_remote_task_persists_cleanup_failure(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                class Executor:
                    async def cleanup_remote(self, task):
                        if task["remoteHandle"]["instanceId"] != "uhost-stuck":
                            raise AssertionError("unexpected instance handle")
                        return {"cleanupState": "failed", "cleanupError": "delete not confirmed"}

                runtime = ImageGenerationTrainingRuntime(
                    task_root=Path(directory),
                    execution_lock=asyncio.Lock(),
                    executor_factory=lambda runtime: Executor(),
                )
                runtime.create_task({
                    "id": "igt-force-cleanup",
                    "provider": "youyun",
                    "state": "failed",
                    "remoteHandle": {"instanceId": "uhost-stuck"},
                })

                task = await runtime.force_cleanup_remote_task(
                    "igt-force-cleanup",
                    reason="测试清理",
                )

                self.assertEqual(task["resourceState"], "cleanup_failed")
                self.assertEqual(task["cleanupState"], "failed")
                self.assertEqual(task["failureCode"], "cleanup_failed")
                self.assertEqual(task["cleanupError"], "delete not confirmed")

        asyncio.run(scenario())

    def test_manual_cleanup_stops_active_task_after_release(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                class Executor:
                    async def cleanup_remote(self, task):
                        return {"cleanupState": "completed", "cleanupError": None}

                runtime = ImageGenerationTrainingRuntime(
                    task_root=Path(directory),
                    execution_lock=asyncio.Lock(),
                    executor_factory=lambda runtime: Executor(),
                )
                runtime.create_task({
                    "id": "igt-manual-release-active",
                    "provider": "youyun",
                    "state": "running",
                    "status": "running",
                    "remoteHandle": {"instanceId": "uhost-active-release"},
                })

                task = await runtime.force_cleanup_remote_task("igt-manual-release-active", reason="手动释放")

                self.assertEqual(task["state"], "stopped")
                self.assertEqual(task["status"], "stopped")
                self.assertEqual(task["cleanupState"], "released")
                self.assertEqual(task["resourceState"], "released")

        asyncio.run(scenario())

    def test_stage_event_persists_remote_handle_and_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ImageGenerationTrainingRuntime(
                task_root=Path(directory),
                execution_lock=asyncio.Lock(),
                executor_factory=lambda runtime: FakeImageGenerationTrainingExecutor(),
            )
            runtime.create_task({"id": "igt-handle"})
            runtime._handle_event(
                "igt-handle",
                TrainingEvent(
                    "stage",
                    "优云训练实例已就绪。",
                    {"phase": "preparing", "progress": 6, "remoteHandle": {"instanceId": "uhost-1"}},
                ),
            )
            task = runtime.get("igt-handle")
            self.assertEqual(task["remoteHandle"], {"instanceId": "uhost-1"})
            self.assertGreater(float(task.get("lastHeartbeatAtEpoch") or 0), 0)
            self.assertIn("优云训练实例已就绪。", task["logs"])

    def test_completed_remote_task_without_instance_has_released_resource_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ImageGenerationTrainingRuntime(
                task_root=Path(directory),
                execution_lock=asyncio.Lock(),
                executor_factory=lambda runtime: FakeImageGenerationTrainingExecutor(),
            )
            task = runtime.create_task({
                "id": "igt-fake-remote-completed",
                "provider": "youyun",
                "state": "completed",
                "status": "completed",
            })

            self.assertEqual(task["resourceState"], "released")
            self.assertEqual(task["cleanupState"], "released")

    def test_stage_history_records_duration_for_diagnosing_slow_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ImageGenerationTrainingRuntime(
                task_root=Path(directory),
                execution_lock=asyncio.Lock(),
                executor_factory=lambda runtime: FakeImageGenerationTrainingExecutor(),
            )
            runtime.create_task({
                "id": "igt-stage-duration",
                "state": "running",
                "status": "running",
                "attempts": [{"number": 1, "status": "running", "startedAt": "2026-09-02T12:00:00+0800", "stages": []}],
            })
            runtime._handle_event("igt-stage-duration", TrainingEvent("stage", "准备实例", {"phase": "preparing", "progress": 5, "elapsedSeconds": 2.0}))
            runtime._handle_event("igt-stage-duration", TrainingEvent("stage", "连接 SSH", {"phase": "preparing", "progress": 7, "elapsedSeconds": 5.5}))

            task = runtime.get("igt-stage-duration")
            stages = task["attempts"][-1]["stages"] if task.get("attempts") else []
            self.assertTrue(stages)
            self.assertIn("startedAt", stages[0])
            self.assertIn("durationSeconds", stages[0])
            self.assertEqual(stages[1]["durationSeconds"], 3.5)

    def test_log_events_append_without_changing_stage_and_metrics_replace_same_step(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ImageGenerationTrainingRuntime(
                task_root=Path(directory),
                execution_lock=asyncio.Lock(),
                executor_factory=lambda runtime: FakeImageGenerationTrainingExecutor(),
            )
            runtime.create_task({"id": "igt-stream", "state": "running", "status": "running", "progress": 10})

            runtime._handle_event("igt-stream", TrainingEvent("log", "Preparing dataset"))
            runtime._handle_event("igt-stream", TrainingEvent("metric", "Step 1", {"step": 1, "loss": 0.5, "learning_rate": 0.0001, "progress": 10}))
            runtime._handle_event("igt-stream", TrainingEvent("metric", "Step 1", {"step": 1, "loss": 0.4, "learning_rate": 0.0001, "progress": 10}))

            task = runtime.get("igt-stream")
            self.assertIn("Preparing dataset", task["logs"])
            self.assertEqual(task["metrics"], [{"step": 1, "loss": 0.4, "evalLoss": None, "learningRate": 0.0001}])
            self.assertEqual(task["state"], "running")

    def test_completed_task_backfills_observability_from_worker_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            preview = Path(directory) / "preview"
            preview.mkdir()
            (preview / "worker.log").write_text(
                "Preparing dataset\n"
                "Steps:  50%|#####     | 1/2 [00:01<00:01, 1.00it/s, lr=0.0001, step_loss=0.5]\r"
                "Steps: 100%|##########| 2/2 [00:02<00:00, 1.00it/s, lr=9e-05, step_loss=0.25]\n",
                encoding="utf-8",
            )
            runtime = ImageGenerationTrainingRuntime(
                task_root=Path(directory) / "tasks",
                execution_lock=asyncio.Lock(),
                executor_factory=lambda runtime: FakeImageGenerationTrainingExecutor(),
            )
            runtime.create_task({"id": "igt-history", "state": "completed", "status": "completed", "previewRoot": str(preview), "logs": [], "metrics": []})

            task = runtime.hydrate_observability("igt-history")

            self.assertEqual([item["step"] for item in task["metrics"]], [1, 2])
            self.assertEqual(task["metrics"][-1]["loss"], 0.25)
            self.assertIn("Preparing dataset", task["logs"])
            self.assertEqual(runtime.get("igt-history")["metrics"], task["metrics"])

    def test_worker_persists_executor_result_logs_and_metrics(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                class Executor:
                    async def run(self, task, **kwargs):
                        return {
                            "status": "completed",
                            "artifacts": [],
                            "logs": ["remote worker finished"],
                            "metrics": [{"step": 1, "loss": 0.25, "learningRate": 0.0001}],
                        }

                runtime = ImageGenerationTrainingRuntime(
                    task_root=Path(directory),
                    execution_lock=asyncio.Lock(),
                    executor_factory=lambda runtime: Executor(),
                )
                runtime.create_task({"id": "igt-result"})
                await runtime.confirm("igt-result")
                await runtime.start()
                await asyncio.wait_for(runtime._queue.join(), timeout=1)

                task = runtime.get("igt-result")
                self.assertEqual(task["state"], "completed")
                self.assertIn("remote worker finished", task["logs"])
                self.assertEqual(task["metrics"], [{"step": 1, "loss": 0.25, "learningRate": 0.0001}])
                await runtime.stop()

        asyncio.run(scenario())

    def test_start_cleans_remote_instance_for_interrupted_task(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                cleaned: list[str] = []

                class Executor(FakeImageGenerationTrainingExecutor):
                    async def cleanup_remote(self, task):
                        cleaned.append(str(task["id"]))
                        return {"cleanupState": "completed", "cleanupError": None}

                runtime = ImageGenerationTrainingRuntime(
                    task_root=Path(directory),
                    execution_lock=asyncio.Lock(),
                    executor_factory=lambda runtime: Executor(),
                )
                runtime.create_task({"id": "igt-interrupted", "state": "running", "status": "running", "remoteHandle": {"instanceId": "uhost-1"}})
                await runtime.start()
                task = runtime.get("igt-interrupted")
                self.assertEqual(cleaned, ["igt-interrupted"])
                self.assertEqual(task["state"], "failed")
                self.assertEqual(task["cleanupState"], "completed")
                await runtime.stop()

        asyncio.run(scenario())

    def test_retry_resets_remote_handle_and_marks_resource_for_new_attempt(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                runtime = ImageGenerationTrainingRuntime(
                    task_root=Path(directory),
                    execution_lock=asyncio.Lock(),
                    executor_factory=lambda runtime: FakeImageGenerationTrainingExecutor(),
                )
                runtime.create_task({
                    "id": "igt-retry-reset",
                    "provider": "youyun",
                    "state": "failed",
                    "status": "failed",
                    "remoteHandle": {"instanceId": "uhost-old", "state": "running"},
                    "cleanupState": "completed",
                    "resourceState": "running",
                    "failureCode": "training_failed",
                    "error": "EOFError",
                })
                retried = await runtime.retry("igt-retry-reset")
                self.assertEqual(retried["state"], "queued")
                self.assertNotIn("remoteHandle", retried)
                self.assertEqual(retried["resourceState"], "provisioning")
                self.assertEqual(retried["cleanupState"], "pending")
                await runtime.stop()

        asyncio.run(scenario())

    def test_executor_failure_preserves_phase_and_actionable_code(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                class Executor:
                    async def run(self, task, **kwargs):
                        return {
                            "status": "failed",
                            "failureCode": "dependency_install_failed",
                            "failureMessage": "优云 Diffusers 依赖安装失败。",
                            "suggestedAction": "使用预装 Diffusers 的镜像。",
                            "error": "pip download interrupted",
                            "phase": "environment_probe",
                        }

                runtime = ImageGenerationTrainingRuntime(
                    task_root=Path(directory),
                    execution_lock=asyncio.Lock(),
                    executor_factory=lambda runtime: Executor(),
                )
                runtime.create_task({"id": "igt-phase-failure"})
                await runtime.confirm("igt-phase-failure")
                await runtime.start()
                await asyncio.wait_for(runtime._queue.join(), timeout=1)
                task = runtime.get("igt-phase-failure")
                self.assertEqual(task["failureCode"], "dependency_install_failed")
                self.assertEqual(task["phase"], "environment_probe")
                self.assertEqual(task["suggestedAction"], "使用预装 Diffusers 的镜像。")
                self.assertTrue(any("失败码：dependency_install_failed" in item for item in task["logs"]))
                await runtime.stop()

        asyncio.run(scenario())

    def test_completed_training_is_failed_when_remote_cleanup_is_not_confirmed(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                class Executor:
                    async def run(self, task, **kwargs):
                        return {
                            "status": "completed",
                            "artifacts": [{"type": "adapter", "variant": "final"}],
                            "cleanupState": "failed",
                            "cleanupError": "delete timed out",
                        }

                runtime = ImageGenerationTrainingRuntime(
                    task_root=Path(directory),
                    execution_lock=asyncio.Lock(),
                    executor_factory=lambda runtime: Executor(),
                )
                runtime.create_task({
                    "id": "igt-cleanup-after-success",
                    "provider": "youyun",
                    "remoteHandle": {"instanceId": "uhost-success"},
                })
                await runtime.confirm("igt-cleanup-after-success")
                await runtime.start()
                await asyncio.wait_for(runtime._queue.join(), timeout=1)

                task = runtime.get("igt-cleanup-after-success")
                self.assertEqual(task["state"], "failed")
                self.assertEqual(task["status"], "failed")
                self.assertEqual(task["resourceState"], "cleanup_failed")
                self.assertEqual(task["cleanupState"], "failed")
                self.assertEqual(task["failureCode"], "cleanup_failed")
                self.assertEqual(task["artifacts"], [{"type": "adapter", "variant": "final"}])
                await runtime.stop()

        asyncio.run(scenario())

    def test_worker_records_attempt_timing(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                class Executor:
                    async def run(self, task, **kwargs):
                        return {"status": "completed", "artifacts": []}

                runtime = ImageGenerationTrainingRuntime(
                    task_root=Path(directory),
                    execution_lock=asyncio.Lock(),
                    executor_factory=lambda runtime: Executor(),
                )
                runtime.create_task({"id": "igt-attempt-timing"})
                await runtime.confirm("igt-attempt-timing")
                await runtime.start()
                await asyncio.wait_for(runtime._queue.join(), timeout=1)
                task = runtime.get("igt-attempt-timing")
                self.assertEqual(len(task["attempts"]), 1)
                attempt = task["attempts"][0]
                self.assertEqual(attempt["number"], 1)
                self.assertEqual(attempt["status"], "completed")
                self.assertIsNotNone(attempt["startedAt"])
                self.assertIsNotNone(attempt["finishedAt"])
                self.assertGreaterEqual(attempt["durationSeconds"], 0)
                await runtime.stop()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
