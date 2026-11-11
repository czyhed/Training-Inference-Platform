from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from starlette.websockets import WebSocketDisconnect

os.environ["XUNTUI_STORAGE_BACKEND"] = "sqlite"
os.environ["POSTGRES_DSN"] = ""
os.environ.setdefault("XUNTUI_DATABASE_PATH", "/tmp/xuntui-diagnosis-release-test.db")
os.environ.setdefault("XUNTUI_RUNTIME_ROOT", "/tmp/xuntui-diagnosis-release-runtime")
os.environ.setdefault("XUNTUI_ARTIFACT_ROOT", "/tmp/xuntui-diagnosis-release-artifacts")
os.environ.setdefault("XUNTUI_DATASET_ROOT", "/tmp/xuntui-diagnosis-release-datasets")

from backend import app as app_module


class TrainingMetricsRecoveryTest(unittest.TestCase):
    def test_trainer_state_log_history_recovers_train_and_eval_loss(self) -> None:
        class MetricStore:
            def __init__(self) -> None:
                self.metrics: list[dict] = []

            def append_metric(self, task_id: str, **values: object) -> None:
                self.metrics.append({"taskId": task_id, **values})

        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "trainer_state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "log_history": [
                            {"loss": 2.3, "learning_rate": 0.0002, "epoch": 0.5, "step": 10},
                            {"eval_loss": 1.7, "epoch": 0.5, "step": 10},
                            {"loss": 1.9, "learning_rate": 0.0001, "epoch": 1.0, "step": 20},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            store = MetricStore()
            with patch.object(app_module, "store", store):
                recovered = app_module._recover_trainer_metrics("ft-metrics", state_path)

        self.assertEqual(recovered, 2)
        self.assertEqual(store.metrics[0]["step"], 10)
        self.assertEqual(store.metrics[0]["train_loss"], 2.3)
        self.assertEqual(store.metrics[0]["eval_loss"], 1.7)
        self.assertEqual(store.metrics[1]["step"], 20)


class _LifecycleStore:
    def __init__(self, task: dict) -> None:
        self.task = task
        self.messages: list[str] = []

    def get(self, task_id: str) -> dict | None:
        return self.task if task_id == self.task["id"] else None

    def update(self, task_id: str, **changes: object) -> dict:
        assert task_id == self.task["id"]
        self.task.update(changes)
        return self.task

    def append_log(self, task_id: str, message: str) -> None:
        assert task_id == self.task["id"]
        self.messages.append(message)


class _LifecycleManager:
    def __init__(self) -> None:
        self.cleaned: list[str] = []

    def cleanup_after_task(self, instance_id: str, stop_first: bool = True) -> dict:
        self.cleaned.append(instance_id)
        return {"terminated": True, "error": None}


class AsyncDiagnosisFixesTest(unittest.IsolatedAsyncioTestCase):
    async def test_manual_release_allows_stopped_task_with_retained_instance(self) -> None:
        task = {
            "id": "ft-stopped",
            "provider": "youyun",
            "state": "stopped",
            "artifacts": [{"type": "youyun-instance", "instanceId": "uhost-1", "state": "awaiting_comparison"}],
        }
        store = _LifecycleStore(task)
        manager = _LifecycleManager()
        with patch.object(app_module, "store", store), patch.object(
            app_module,
            "build_youyun_runner",
            return_value=SimpleNamespace(manager=manager),
        ):
            updated = await app_module.release_youyun_instance("ft-stopped")
        self.assertEqual(updated["state"], "stopped")
        self.assertEqual(updated["artifacts"][0]["state"], "released")
        self.assertEqual(manager.cleaned, ["uhost-1"])

    async def test_training_metrics_failure_keeps_websocket_open(self) -> None:
        class TaskStore:
            def get(self, task_id: str):
                return {"id": task_id, "state": "running", "progress": 40, "currentStep": "训练中"}

            def log_items(self, task_id: str, *, after_id: int = 0, limit: int = 500):
                return {"items": [], "hasMore": False}

            def metrics(self, task_id: str):
                if not getattr(self, "failed", False):
                    self.failed = True
                    raise RuntimeError("temporary metrics read failure")
                return [{"step": 2, "trainLoss": 1.2}]

        class WebSocket:
            query_params = {"afterId": "0"}

            def __init__(self) -> None:
                self.messages: list[dict] = []

            async def accept(self) -> None:
                return None

            async def send_json(self, payload: dict) -> None:
                self.messages.append(payload)

            async def close(self, code: int | None = None) -> None:
                return None

        websocket = WebSocket()
        original_store = app_module.store
        original_auth = app_module.websocket_authenticated
        app_module.store = TaskStore()  # type: ignore[assignment]
        app_module.websocket_authenticated = lambda _: True
        sleeps = 0

        async def fake_sleep(_: float) -> None:
            nonlocal sleeps
            sleeps += 1
            if sleeps >= 2:
                raise WebSocketDisconnect()

        try:
            with patch("backend.app.asyncio.sleep", side_effect=fake_sleep):
                await app_module.task_stream(websocket, "ft-stream")
        finally:
            app_module.store = original_store
            app_module.websocket_authenticated = original_auth

        self.assertTrue(any(message.get("type") == "metrics" for message in websocket.messages))
