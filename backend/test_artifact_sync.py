from __future__ import annotations

import asyncio
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from backend import test_app_cli as bootstrap
from backend.providers.youyun.remote_training import YouyunTrainingRunner
from backend.model_storage import ModelArtifactUploader

api = bootstrap.app_module


def adapter(root: Path, content: bytes = b"weights") -> dict:
    root.mkdir(parents=True, exist_ok=True)
    (root / "adapter_config.json").write_text("{}", encoding="utf-8")
    (root / "adapter_model.safetensors").write_bytes(content)
    return {"type": "adapter", "variant": "final", "path": str(root)}


class Store:
    def __init__(self, task: dict) -> None:
        self.task = copy.deepcopy(task)
        self.logs: list[str] = []

    def get(self, task_id):
        return copy.deepcopy(self.task)

    def update(self, task_id, **changes):
        self.task.update(changes)
        return self.get(task_id)

    def append_log(self, task_id, message):
        self.logs.append(message)


class ArtifactSyncTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.task = {"id": "ft-sync", "state": "completed", "provider": "youyun",
                     "artifacts": [adapter(self.root / "adapter"),
                         {"type": "youyun-instance", "instanceId": "gpu-existing", "state": "released"}]}
        self.store = Store(self.task)
        self.enterContext(patch.object(api, "store", self.store))
        self.enterContext(patch.object(api, "sync_model_assets"))
        self.factory = self.enterContext(patch.object(api, "build_youyun_runner"))
        self.upload = self.enterContext(patch.object(api, "upload_completed_task_artifacts", new_callable=AsyncMock, return_value=[]))
        self.enterContext(patch.object(api, "artifact_sync_tasks", {}))

    async def test_complete_local_adapter_uploads_without_provider(self):
        await api.sync_remote_task_artifacts("ft-sync")
        self.factory.assert_not_called()
        self.upload.assert_awaited_once_with("ft-sync")
        self.assertEqual(self.store.task["artifacts"][0]["syncState"], "available")
        self.assertIsNone(self.store.task["artifacts"][0]["syncError"])
        self.assertEqual(self.store.task["state"], "completed")

    async def test_retry_accepts_complete_local_adapter_after_instance_release(self):
        with patch.object(api, "schedule_remote_artifact_sync") as schedule:
            await api.retry_task_artifact_sync("ft-sync")
        schedule.assert_called_once_with("ft-sync")

    async def test_missing_adapter_and_released_instance_is_rejected(self):
        (self.root / "adapter" / "adapter_model.safetensors").unlink()
        with self.assertRaises(HTTPException) as caught:
            await api.retry_task_artifact_sync("ft-sync")
        self.assertEqual(caught.exception.status_code, 409)

    async def test_no_adapter_is_not_success(self):
        self.store.task["provider"] = "local"
        self.store.task["artifacts"] = []
        await api.sync_remote_task_artifacts("ft-sync")
        self.upload.assert_not_awaited()
        self.assertTrue(any("失败" in line for line in self.store.logs))
        self.assertFalse(any("已完成本地回传" in line for line in self.store.logs))

    async def test_recovered_adapter_is_revalidated_before_upload(self):
        (self.root / "adapter" / "adapter_model.safetensors").unlink()
        self.store.task["artifacts"][1]["state"] = "awaiting_comparison"
        self.factory.return_value.sync_retained_artifacts = AsyncMock()
        await api.sync_remote_task_artifacts("ft-sync")
        self.upload.assert_not_awaited()
        self.assertEqual(self.store.task["artifacts"][0]["syncState"], "failed")

    async def test_missing_local_adapter_is_recovered_then_uploaded(self):
        (self.root / "adapter" / "adapter_model.safetensors").unlink()
        self.store.task["artifacts"][1]["state"] = "awaiting_comparison"
        async def recover(task_id, task):
            adapter(self.root / "adapter", b"recovered")
        self.factory.return_value.sync_retained_artifacts = AsyncMock(side_effect=recover)
        await api.sync_remote_task_artifacts("ft-sync")
        self.upload.assert_awaited_once_with("ft-sync")
        self.assertEqual(self.store.task["artifacts"][0]["syncState"], "available")

    async def test_failed_download_keeps_local_files_and_reports_stage(self):
        (self.root / "adapter" / "adapter_model.safetensors").unlink()
        self.store.task["artifacts"][1]["state"] = "awaiting_comparison"
        self.factory.return_value.sync_retained_artifacts = AsyncMock(side_effect=RuntimeError("connection lost"))
        await api.sync_remote_task_artifacts("ft-sync")
        self.upload.assert_not_awaited()
        self.assertIn("回传", self.store.task["artifacts"][0]["syncError"])
        self.assertTrue((self.root / "adapter" / "adapter_config.json").exists())

    async def test_upload_failure_reports_stage_and_can_retry(self):
        self.upload.return_value = ["object store unavailable"]
        await api.sync_remote_task_artifacts("ft-sync")
        self.assertIn("共享存储上传", self.store.task["artifacts"][0]["syncError"])
        self.assertEqual(self.store.task["artifacts"][0]["syncState"], "failed")
        self.assertTrue((self.root / "adapter" / "adapter_model.safetensors").exists())
        self.upload.return_value = []
        await api.sync_remote_task_artifacts("ft-sync")
        self.assertEqual(self.store.task["artifacts"][0]["syncState"], "available")

    async def test_duplicate_schedule_starts_one_job(self):
        gate = asyncio.Event()
        async def worker(task_id):
            await gate.wait()
        with patch.object(api, "sync_remote_task_artifacts", side_effect=worker) as run:
            api.schedule_remote_artifact_sync("ft-sync")
            first = api.artifact_sync_tasks["ft-sync"]
            api.schedule_remote_artifact_sync("ft-sync")
            self.assertIs(api.artifact_sync_tasks["ft-sync"], first)
            gate.set()
            await first
            run.assert_called_once_with("ft-sync")


class RetainedRunnerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workdir = Path(self.tmp.name) / "ft-sync"
        final = adapter(self.workdir / "output", b"preserve-original")
        best = {"type": "adapter", "variant": "best", "path": str(self.workdir / "output" / "checkpoint-10")}
        self.task = {"id": "ft-sync", "taskId": "ft-sync", "state": "completed", "artifacts": [final, best,
            {"type": "youyun-instance", "instanceId": "gpu-existing", "state": "awaiting_comparison"}]}
        self.store = Store(self.task)
        self.ssh = Mock()
        self.manager = Mock()
        self.runner = YouyunTrainingRunner(manager=self.manager, sink=self.store,
            mirror_root=self.workdir.parent,
            prepare_local=AsyncMock(return_value=SimpleNamespace(workdir=str(self.workdir))),
            collect_local=AsyncMock(return_value=[final, best]))
        self.runner._connect_ssh = Mock(return_value=self.ssh)
        self.runner._read_base_model_metadata = AsyncMock(return_value=None)

    async def test_real_retained_method_recovers_without_overwriting_complete_adapter(self):
        def download(remote, local):
            adapter(Path(local), b"must-not-overwrite")
            adapter(Path(local) / "checkpoint-10", b"recovered-best")
        self.ssh.get_tree.side_effect = download
        await self.runner.sync_retained_artifacts("ft-sync", self.task)
        self.assertEqual((self.workdir / "output" / "adapter_model.safetensors").read_bytes(), b"preserve-original")
        self.assertEqual((self.workdir / "output" / "checkpoint-10" / "adapter_model.safetensors").read_bytes(), b"recovered-best")
        self.ssh.close.assert_called_once()
        self.manager.create_one_shot.assert_not_called()
        self.manager.cleanup_after_task.assert_not_called()
        self.assertEqual(self.store.task["state"], "completed")

    async def test_interrupted_download_does_not_publish_partial_files(self):
        def download(remote, local):
            adapter(Path(local), b"bad-new-file")
            raise RuntimeError("SSH download interrupted")
        self.ssh.get_tree.side_effect = download
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            await self.runner.sync_retained_artifacts("ft-sync", self.task)
        self.assertEqual((self.workdir / "output" / "adapter_model.safetensors").read_bytes(), b"preserve-original")
        self.assertFalse((self.workdir / "output" / "checkpoint-10").exists())
        self.ssh.close.assert_called_once()
        self.manager.cleanup_after_task.assert_not_called()

    async def test_released_instance_never_connects(self):
        self.task["artifacts"][-1]["state"] = "released"
        with self.assertRaisesRegex(RuntimeError, "释放"):
            await self.runner.sync_retained_artifacts("ft-sync", self.task)
        self.runner._connect_ssh.assert_not_called()


class UploadRetryTest(unittest.TestCase):
    def test_available_upload_reuses_metadata_without_writing_objects(self):
        uploader = ModelArtifactUploader.__new__(ModelArtifactUploader)
        uploader.metadata = Mock()
        uploader.objects = Mock()
        uploader.metadata.find_source.return_value = {"id": "model-same", "status": "available"}
        uploader.metadata.list_entries.return_value = []
        for _ in range(2):
            result = uploader.upload_directory(Path("unused"), name="adapter", artifact_type="lora_adapter", source_task_id="ft-sync")
            self.assertEqual(result["id"], "model-same")
        uploader.metadata.create_pending.assert_not_called()
        uploader.objects.upload.assert_not_called()

    def test_failed_upload_reuses_model_id_on_retry(self):
        uploader = ModelArtifactUploader.__new__(ModelArtifactUploader)
        uploader.config = SimpleNamespace(minio_bucket="test-bucket")
        uploader.metadata = Mock()
        uploader.objects = Mock()
        uploader.metadata.find_source.return_value = {"id": "model-same", "status": "failed"}
        uploader.metadata.get.return_value = {"id": "model-same"}
        uploader.metadata.list_entries.return_value = []
        with tempfile.TemporaryDirectory() as directory:
            adapter(Path(directory))
            result = uploader.upload_directory(Path(directory), name="adapter", artifact_type="lora_adapter", source_task_id="ft-sync")
        self.assertEqual(result["id"], "model-same")
        self.assertEqual(uploader.metadata.reset_pending.call_args.args[0]["id"], "model-same")
        uploader.metadata.create_pending.assert_not_called()
        uploader.metadata.mark_available.assert_called_once_with("model-same")
