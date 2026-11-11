from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from backend.image_generation_evaluation_executor import FakeImageGenerationEvaluationExecutor
from backend.image_generation_evaluation_runtime import ImageGenerationEvaluationRuntime


class ImageGenerationEvaluationRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = ImageGenerationEvaluationRuntime(
            task_root=Path(self.temp.name),
            execution_lock=asyncio.Lock(),
            executor_factory=lambda runtime: FakeImageGenerationEvaluationExecutor(),
        )
        await self.runtime.start()

    async def asyncTearDown(self) -> None:
        await self.runtime.stop()
        self.temp.cleanup()

    async def wait_state(self, task_id: str, state: str) -> dict:
        for _ in range(40):
            task = self.runtime.get(task_id) or {}
            if task.get("state") == state:
                return task
            await asyncio.sleep(0.02)
        self.fail(f"task did not reach {state}: {self.runtime.get(task_id)}")

    def payload(self, samples: list[dict]) -> dict:
        return {"id": "ige-test", "serviceId": "svc-image", "serviceName": "测试服务", "samples": samples, "resultRoot": str(Path(self.temp.name) / "ige-test")}

    async def test_confirm_runs_all_samples_and_aggregates_metrics(self) -> None:
        task = self.runtime.create_task(self.payload([{"id": "sample-1", "caption": "one"}, {"id": "sample-2", "caption": "two"}]))
        await self.runtime.confirm(task["id"])
        completed = await self.wait_state(task["id"], "completed")
        self.assertEqual(completed["summary"]["completedSamples"], 2)
        self.assertEqual(completed["summary"]["failedSamples"], 0)
        self.assertEqual(completed["summary"]["successRate"], 1.0)
        self.assertAlmostEqual(completed["summary"]["clipTextImageMean"], 0.75)
        self.assertTrue((Path(self.temp.name) / "ige-test" / "generated" / "sample-1.png").is_file())

    async def test_single_sample_failure_keeps_batch_completed_with_failure_count(self) -> None:
        self.runtime.executor_factory = lambda runtime: FakeImageGenerationEvaluationExecutor(failed_sample_ids={"sample-2"})
        task = self.runtime.create_task(self.payload([{"id": "sample-1", "caption": "one"}, {"id": "sample-2", "caption": "two"}]))
        await self.runtime.confirm(task["id"])
        completed = await self.wait_state(task["id"], "completed")
        self.assertEqual(completed["summary"]["completedSamples"], 1)
        self.assertEqual(completed["summary"]["failedSamples"], 1)
        self.assertEqual(completed["results"][1]["state"], "failed")

    async def test_stop_marks_queued_task_stopped(self) -> None:
        task = self.runtime.create_task(self.payload([{"id": "sample-1", "caption": "one"}]))
        stopped = await self.runtime.request_stop(task["id"])
        self.assertEqual(stopped["state"], "stopped")

    async def test_event_remote_handle_is_persisted_for_cleanup_watchdog(self) -> None:
        class Executor(FakeImageGenerationEvaluationExecutor):
            async def run(self, task, *, on_event=None, cancel_event=None):
                if on_event:
                    on_event("preparing", "实例已就绪", {"progress": 6, "remoteHandle": {"instanceId": "uhost-eval-1", "state": "running"}})
                return {"status": "failed", "failureCode": "evaluation_failed", "cleanupState": "failed", "cleanupError": "delete failed"}

        self.runtime.executor_factory = lambda runtime: Executor()
        task = self.runtime.create_task(self.payload([{"id": "sample-1", "caption": "one"}]))
        await self.runtime.confirm(task["id"])
        failed = await self.wait_state(task["id"], "failed")
        self.assertEqual(failed["remoteHandle"]["instanceId"], "uhost-eval-1")

    async def test_update_can_clear_remote_handle_after_cleanup(self) -> None:
        task = self.runtime.create_task(self.payload([]))
        self.runtime.update(task["id"], remoteHandle={"instanceId": "uhost-eval-1"})
        updated = self.runtime.update(task["id"], cleanupState="released", clear=("remoteHandle",))
        self.assertNotIn("remoteHandle", updated or {})


if __name__ == "__main__":
    unittest.main()
