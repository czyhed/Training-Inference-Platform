from __future__ import annotations

import unittest

from backend.overview import build_overview_snapshot


class OverviewSnapshotTest(unittest.TestCase):
    def test_aggregates_counts_runtime_alerts_and_recent_activity(self) -> None:
        snapshot = build_overview_snapshot(
            datasets=[
                {"id": "ds-1", "name": "ready", "state": "published", "updatedAt": "2026-08-25 09:00"},
                {"id": "ds-2", "name": "bad", "state": "failed", "validationMessage": "第 3 行错误", "updatedAt": "2026-08-25 10:00"},
            ],
            tasks=[
                {
                    "id": "ft-running",
                    "name": "running task",
                    "state": "running",
                    "progress": 45,
                    "currentStep": "Step 9",
                    "model": "Qwen/Test",
                    "provider": "local",
                    "createdAt": "2026-08-25 11:00",
                },
                {
                    "id": "ft-failed",
                    "name": "failed task",
                    "state": "failed",
                    "error": "out of memory",
                    "createdAt": "2026-08-25 08:00",
                },
            ],
            model_assets=[
                {"id": "base-1", "name": "base", "kind": "base", "status": "available", "updatedAt": "2026-08-24 12:00"},
                {"id": "trained-1", "name": "adapter", "kind": "trained", "status": "unavailable", "updatedAt": "2026-08-25 07:00"},
            ],
            services=[
                {
                    "id": "svc-1",
                    "name": "chat",
                    "state": "running",
                    "exposure": "online",
                    "model": "Qwen/Test",
                    "gpu": "GPU 0",
                    "memory": "8 / 24 GB",
                    "updatedAt": "2026-08-25T11:30:00+08:00",
                    "config": {"adapterVariant": "best"},
                }
            ],
            health={
                "status": "ok",
                "worker": "busy",
                "activeTaskId": "ft-running",
                "queue": "sqlite-local",
                "storageBackend": "sqlite",
                "inference": "idle",
            },
            gpu={
                "available": True,
                "name": "RTX 4090",
                "memoryTotalMb": 24000,
                "memoryUsedMb": 6000,
                "memoryFreeMb": 18000,
            },
            shared_storage={"status": "ready", "error": None},
            inference_metrics={"runningRequests": 2, "ttftMs": 80},
            generated_at="2026-08-25T12:00:00+08:00",
        )

        self.assertEqual(snapshot["counts"]["datasets"]["failed"], 1)
        self.assertEqual(snapshot["counts"]["training"]["active"], 1)
        self.assertEqual(snapshot["counts"]["models"]["unavailable"], 1)
        self.assertEqual(snapshot["counts"]["inference"]["online"], 1)
        self.assertEqual(snapshot["runtime"]["resourceOwner"], "training")
        self.assertEqual(snapshot["runtime"]["gpu"]["memoryUsagePercent"], 25.0)
        self.assertEqual(snapshot["activeTraining"]["id"], "ft-running")
        self.assertEqual(snapshot["activeInference"]["id"], "svc-1")
        self.assertEqual(snapshot["recentActivity"][0]["id"], "svc-1")
        self.assertTrue(any(item["id"] == "task-ft-failed" for item in snapshot["alerts"]))
        self.assertEqual(snapshot["status"], "attention")

    def test_idle_snapshot_keeps_unknown_metrics_out_of_the_response(self) -> None:
        snapshot = build_overview_snapshot(
            datasets=[],
            tasks=[],
            model_assets=[],
            services=[],
            health={"status": "ok", "worker": "idle"},
            gpu={"available": False, "reason": "当前节点未检测到可用 GPU"},
            shared_storage={"status": "ready", "error": None},
            inference_metrics=None,
            generated_at="2026-08-25T12:00:00+08:00",
        )

        self.assertEqual(snapshot["runtime"]["resourceOwner"], "idle")
        self.assertIsNone(snapshot["activeTraining"])
        self.assertIsNone(snapshot["activeInference"])
        self.assertEqual(snapshot["inferenceMetrics"], {"updatedAt": "2026-08-25T12:00:00+08:00"})
        self.assertIsNone(snapshot["runtime"]["gpu"]["memoryUsagePercent"])
        self.assertEqual(snapshot["alerts"][0]["id"], "gpu-unavailable")

    def test_stale_running_task_does_not_claim_runtime_resource(self) -> None:
        snapshot = build_overview_snapshot(
            datasets=[],
            tasks=[{"id": "ft-stale", "name": "stale", "state": "running"}],
            model_assets=[],
            services=[],
            health={"status": "ok", "worker": "idle", "activeTaskId": None},
            gpu={"available": False, "reason": "CPU node"},
            shared_storage={"status": "ready", "error": None},
            inference_metrics=None,
            generated_at="2026-08-25T12:00:00+08:00",
        )

        self.assertEqual(snapshot["runtime"]["resourceOwner"], "idle")
        self.assertTrue(
            any(item["id"] == "stale-task-ft-stale" for item in snapshot["alerts"])
        )

    def test_exposes_unified_sections_with_update_timestamps(self) -> None:
        generated_at = "2026-09-04T12:00:00+08:00"
        snapshot = build_overview_snapshot(
            datasets=[{"id": "ds-1", "state": "published"}],
            tasks=[{"id": "vt-1", "state": "completed"}],
            model_assets=[{"id": "mdl-1", "kind": "base", "status": "available"}],
            services=[{"id": "svc-1", "state": "stopped", "exposure": "offline"}],
            health={"status": "ok", "worker": "idle"},
            gpu={"available": False, "reason": "CPU node"},
            shared_storage={"status": "ready"},
            inference_metrics=None,
            generated_at=generated_at,
        )

        for section in ("datasets", "tasks", "models", "services", "resources"):
            self.assertIn(section, snapshot)
            self.assertEqual(snapshot[section]["updatedAt"], generated_at)
        self.assertIn("inferenceMetrics", snapshot)
        self.assertEqual(snapshot["inferenceMetrics"]["updatedAt"], generated_at)
        self.assertEqual(snapshot["issues"], snapshot["alerts"])
        self.assertEqual(snapshot["recentEvents"], snapshot["recentActivity"])

    def test_merges_vision_and_image_generation_tasks_into_training_counts(self) -> None:
        snapshot = build_overview_snapshot(
            datasets=[],
            tasks=[{"id": "ft-1", "state": "completed", "createdAt": "2026-09-05T10:00:00+08:00"}],
            vision_tasks=[{"id": "vt-1", "state": "failed", "createdAt": "2026-09-05T11:00:00+08:00"}],
            image_generation_tasks=[{"id": "igt-1", "state": "queued", "createdAt": "2026-09-05T12:00:00+08:00"}],
            model_assets=[],
            services=[],
            health={"status": "ok", "worker": "idle"},
            gpu={"available": False, "reason": "CPU node"},
            shared_storage={"status": "ready"},
            inference_metrics=None,
            generated_at="2026-09-05T12:00:00+08:00",
        )

        self.assertEqual(snapshot["counts"]["training"]["total"], 3)
        self.assertEqual(snapshot["counts"]["training"]["queued"], 1)
        self.assertEqual(snapshot["counts"]["training"]["failed"], 1)
        self.assertTrue(any(item["id"] == "task-vt-1" for item in snapshot["alerts"]))


if __name__ == "__main__":
    unittest.main()
