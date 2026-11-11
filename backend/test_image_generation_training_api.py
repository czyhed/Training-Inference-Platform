from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from backend.image_generation_executor import FakeImageGenerationTrainingExecutor
from backend.image_generation_training_api import create_image_generation_training_router
from backend.image_generation_training_runtime import ImageGenerationTrainingRuntime


class FakeStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.datasets = [{
            "id": "ds-img", "name": "产品图", "structure": "IMAGE_TEXT", "type": "文生图", "state": "published", "sampleCount": 2,
            "paths": {"mediaRoot": str(root / "images"), "manifestPath": str(root / "metadata.jsonl")},
        }, {
            "id": "ds-vision", "name": "视觉问答", "structure": "IMAGE_TEXT", "type": "视觉理解", "state": "published", "sampleCount": 2,
            "paths": {"mediaRoot": str(root / "images"), "manifestPath": str(root / "metadata.jsonl")},
        }]
        self.assets = [{"id": "base-sdxl", "kind": "base", "name": "SDXL", "repoId": "stabilityai/stable-diffusion-xl-base-1.0", "status": "available", "path": str(root / "model")}]

    def list_datasets(self): return list(self.datasets)
    def get_dataset(self, dataset_id): return next((item for item in self.datasets if item["id"] == dataset_id), None)
    def list_model_assets(self, **kwargs): return list(self.assets)
    def delete_model_asset(self, asset_id):
        before = len(self.assets)
        self.assets = [item for item in self.assets if item.get("id") != asset_id]
        return len(self.assets) != before


class ImageGenerationTrainingApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        (root / "images").mkdir()
        (root / "model").mkdir()
        from PIL import Image
        import json
        Image.new("RGB", (8, 8), (1, 2, 3)).save(root / "images" / "a.png")
        Image.new("RGB", (8, 8), (4, 5, 6)).save(root / "images" / "b.png")
        (root / "metadata.jsonl").write_text("".join(json.dumps({"file_name": f"images/{name}", "caption": name}) + "\n" for name in ("a.png", "b.png")), encoding="utf-8")
        self.store = FakeStore(root)
        self.runtime = ImageGenerationTrainingRuntime(task_root=root / "tasks", execution_lock=asyncio.Lock(), executor_factory=lambda runtime: FakeImageGenerationTrainingExecutor())
        async def resolve_resource(resource):
            fields = ("mode", "instanceMode", "region", "zone", "gpuType", "gpuCount", "cpuCores", "memoryGb", "chargeType", "imageId")
            return {**{key: resource[key] for key in fields if key in resource}, "price": {"currency": "CNY", "instance": 2.05, "disks": 0.0, "systemDisks": 0.0, "image": 0.0, "total": 2.05}}
        app = FastAPI()
        app.include_router(create_image_generation_training_router(self.store, self.runtime, runtime_root=root / "runtime", youyun_resource_resolver=resolve_resource))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://image-training.test")

    async def asyncTearDown(self) -> None:
        await self.runtime.stop()
        await self.client.aclose()
        self.temp.cleanup()

    async def test_models_and_datasets_filter_to_sdxl_and_published_image_text(self) -> None:
        models = await self.client.get("/api/image-generation/training/models")
        datasets = await self.client.get("/api/image-generation/training/datasets")
        self.assertEqual(models.status_code, 200)
        self.assertEqual(models.json()[0]["repoId"], "stabilityai/stable-diffusion-xl-base-1.0")
        self.assertEqual(datasets.status_code, 200)
        self.assertEqual([item["id"] for item in datasets.json()], ["ds-img"])

    async def test_models_accept_model_asset_repo_field(self) -> None:
        self.store.assets = [{"id": "base-sdxl", "kind": "base", "name": "SDXL", "repo": "stabilityai/stable-diffusion-xl-base-1.0", "status": "available", "path": str(self.store.root / "model")}]
        models = await self.client.get("/api/image-generation/training/models")
        self.assertEqual(models.status_code, 200)
        self.assertEqual(models.json()[0]["repoId"], "stabilityai/stable-diffusion-xl-base-1.0")

    async def test_preflight_materialize_confirm_and_diagnostics(self) -> None:
        payload = {"datasetId": "ds-img", "modelId": "base-sdxl", "name": "SDXL 微调", "outputName": "sdxl-lora", "resolution": 512, "epochs": 1, "maxTrainSteps": 2, "learningRate": 0.0001, "loraRank": 4, "batchSize": 1, "gradientAccumulation": 1, "validationSplit": 0, "seed": 42, "provider": "youyun", "resource": {"region": "cn-wlcb", "zone": "cn-wlcb-01", "gpuType": "4090", "gpuCount": 1, "cpuCores": 16, "memoryGb": 64, "imageId": "img-diffusers"}}
        preflight = await self.client.post("/api/image-generation/training/preflight", json=payload)
        self.assertEqual(preflight.status_code, 200, preflight.text)
        self.assertTrue(preflight.json()["ready"])
        preview = await self.client.post("/api/image-generation/training/materialize-preview", json=payload)
        self.assertEqual(preview.status_code, 200, preview.text)
        created = await self.client.post("/api/image-generation/training/tasks", json={**payload, "preflight": preflight.json()})
        self.assertEqual(created.status_code, 201, created.text)
        task_id = created.json()["id"]
        confirmed = await self.client.post(f"/api/image-generation/training/tasks/{task_id}/confirm")
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        await self.runtime.start()
        await asyncio.wait_for(self.runtime._queue.join(), timeout=1)
        detail = await self.client.get(f"/api/image-generation/training/tasks/{task_id}")
        self.assertEqual(detail.json()["state"], "completed")
        diag = await self.client.get(f"/api/image-generation/training/tasks/{task_id}/diagnostics")
        self.assertEqual(diag.status_code, 200)

    async def test_youyun_preflight_blocks_incomplete_resource_snapshot(self) -> None:
        payload = {"datasetId": "ds-img", "modelId": "base-sdxl", "provider": "youyun", "resource": {"imageId": "img-diffusers"}}
        preflight = await self.client.post("/api/image-generation/training/preflight", json=payload)
        self.assertEqual(preflight.status_code, 200, preflight.text)
        body = preflight.json()
        self.assertFalse(body["ready"])
        self.assertEqual(body["checks"]["capacity"]["status"], "blocked")
        self.assertEqual(body["checks"]["price"]["status"], "blocked")

    async def test_youyun_preflight_preserves_uhost_instance_mode_in_resource_snapshot(self) -> None:
        payload = {
            "datasetId": "ds-img",
            "modelId": "base-sdxl",
            "provider": "youyun",
            "resource": {
                "instanceMode": "uhost",
                "region": "cn-wlcb",
                "zone": "cn-wlcb-02",
                "gpuType": "4090",
                "gpuCount": 1,
                "cpuCores": 16,
                "memoryGb": 64,
                "imageId": "img-diffusers",
            },
        }
        preflight = await self.client.post("/api/image-generation/training/preflight", json=payload)
        self.assertEqual(preflight.status_code, 200, preflight.text)
        self.assertEqual(preflight.json()["youyunResource"]["instanceMode"], "uhost")

    async def test_preflight_blocks_known_24gb_oom_configuration(self) -> None:
        payload = {
            "datasetId": "ds-img",
            "modelId": "base-sdxl",
            "resolution": 1024,
            "loraRank": 16,
            "provider": "youyun",
            "resource": {
                "region": "cn-wlcb",
                "zone": "cn-wlcb-01",
                "gpuType": "4090",
                "gpuCount": 1,
                "cpuCores": 16,
                "memoryGb": 64,
                "imageId": "img-diffusers",
            },
        }
        preflight = await self.client.post("/api/image-generation/training/preflight", json=payload)
        self.assertEqual(preflight.status_code, 200, preflight.text)
        body = preflight.json()
        self.assertFalse(body["ready"])
        self.assertIn("24GB", body["checks"]["training"]["message"])

    async def _post_preflight_locked(self, payload: dict[str, object]):
        # 锁定脚本下载兜底开关，避免本机 .env.local 在 app 导入时污染 os.environ。
        with patch.dict(os.environ, {"XUNTUI_ALLOW_REMOTE_SCRIPT_DOWNLOAD": "0"}, clear=False):
            return await self.client.post("/api/image-generation/training/preflight", json=payload)

    async def test_preflight_blocks_diffusers_install_without_script_fallback(self) -> None:
        payload = {
            "datasetId": "ds-img",
            "modelId": "base-sdxl",
            "provider": "youyun",
            "resource": {
                "region": "cn-wlcb",
                "zone": "cn-wlcb-01",
                "gpuType": "4090",
                "gpuCount": 1,
                "cpuCores": 16,
                "memoryGb": 64,
                "imageId": "img-pytorch",
                "requiresDiffusersInstall": True,
            },
        }

        preflight = await self._post_preflight_locked(payload)
        self.assertEqual(preflight.status_code, 200, preflight.text)
        image_check = preflight.json()["checks"]["image"]
        self.assertEqual(image_check["status"], "blocked")
        self.assertIn("训练脚本", image_check["message"])
        self.assertTrue(preflight.json()["youyunResource"]["requiresDiffusersInstall"])

    async def test_preflight_blocks_scriptless_fallback_image_before_paid_run(self) -> None:
        payload = {
            "datasetId": "ds-img",
            "modelId": "base-sdxl",
            "provider": "youyun",
            "resource": {
                "region": "cn-wlcb",
                "zone": "cn-wlcb-01",
                "gpuType": "4090",
                "gpuCount": 1,
                "cpuCores": 16,
                "memoryGb": 64,
                "imageId": "img-pytorch",
                "requiresDiffusersInstall": True,
            },
        }
        with patch.dict(os.environ, {"XUNTUI_ALLOW_REMOTE_SCRIPT_DOWNLOAD": "0"}, clear=False):
            preflight = await self.client.post("/api/image-generation/training/preflight", json=payload)
        self.assertEqual(preflight.status_code, 200, preflight.text)
        body = preflight.json()
        self.assertFalse(body["ready"])
        self.assertEqual(body["checks"]["image"]["status"], "blocked")
        self.assertIn("预置", body["checks"]["image"]["message"])

    async def test_delete_blocks_active_model_catalog_reference(self) -> None:
        preview = Path(self.temp.name) / "runtime" / "image-generation-training-previews" / "preview-model-ref"
        preview.mkdir(parents=True)
        self.runtime.create_task({"id": "igt-model-ref", "state": "completed", "previewRoot": str(preview)})
        self.store.assets.append({
            "id": "trained-igt-model-ref",
            "kind": "trained",
            "name": "active adapter",
            "taskId": "igt-model-ref",
            "archived": False,
        })

        response = await self.client.delete("/api/image-generation/training/tasks/igt-model-ref")

        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("模型管理", response.text)
        self.assertIsNotNone(self.runtime.get("igt-model-ref"))

    async def test_delete_removes_archived_model_catalog_entry(self) -> None:
        preview = Path(self.temp.name) / "runtime" / "image-generation-training-previews" / "preview-archived-model"
        preview.mkdir(parents=True)
        self.runtime.create_task({"id": "igt-archived-model", "state": "completed", "previewRoot": str(preview)})
        self.store.assets.append({
            "id": "trained-igt-archived-model",
            "kind": "trained",
            "name": "archived adapter",
            "taskId": "igt-archived-model",
            "archived": True,
        })

        response = await self.client.delete("/api/image-generation/training/tasks/igt-archived-model")

        self.assertEqual(response.status_code, 204, response.text)
        self.assertFalse(any(item.get("taskId") == "igt-archived-model" for item in self.store.assets))
        self.assertFalse(preview.exists())

    async def test_delete_blocks_inference_service_adapter_reference(self) -> None:
        class FakeServiceStore:
            def list(self, *, include_archived: bool = False):
                return [{
                    "id": "igs-using-adapter",
                    "name": "商品图服务",
                    "state": "created",
                    "config": {"workloadType": "image_generation", "adapterLineage": {"taskId": "igt-service-ref", "variant": "best"}},
                }]

        preview = Path(self.temp.name) / "runtime" / "image-generation-training-previews" / "preview-service-ref"
        preview.mkdir(parents=True)
        self.runtime.create_task({"id": "igt-service-ref", "state": "failed", "previewRoot": str(preview)})
        self.runtime.service_store = FakeServiceStore()

        response = await self.client.delete("/api/image-generation/training/tasks/igt-service-ref")

        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("商品图服务", response.text)
        self.assertIsNotNone(self.runtime.get("igt-service-ref"))

    async def test_release_youyun_instance_uses_runtime_cleanup(self) -> None:
        cleaned: list[str] = []

        class CleanupExecutor:
            async def cleanup_remote(self, task):
                cleaned.append(task["remoteHandle"]["instanceId"])
                return {"cleanupState": "completed", "cleanupError": None}

        self.runtime.executor_factory = lambda runtime: CleanupExecutor()
        self.runtime.create_task({
            "id": "igt-release-api",
            "provider": "youyun",
            "state": "failed",
            "status": "failed",
            "remoteHandle": {"instanceId": "uhost-release-api"},
        })

        response = await self.client.post("/api/image-generation/training/tasks/igt-release-api/youyun/release")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(cleaned, ["uhost-release-api"])
        self.assertEqual(response.json()["resourceState"], "released")
        self.assertEqual(response.json()["cleanupState"], "released")


if __name__ == "__main__":
    unittest.main()
