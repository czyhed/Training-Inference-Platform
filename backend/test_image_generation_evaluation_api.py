from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi import FastAPI
from PIL import Image

from backend.image_generation_api import create_image_generation_router
from backend.image_generation_evaluation_executor import FakeImageGenerationEvaluationExecutor
from backend.image_generation_evaluation_runtime import ImageGenerationEvaluationRuntime


class EvaluationApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        images = root / "images"
        images.mkdir()
        Image.new("RGB", (32, 32), "white").save(images / "one.png")
        (root / "metadata.jsonl").write_text('{"file_name":"one.png","caption":"a white square"}\n', encoding="utf-8")

        class Store:
            def list_model_assets(self, **kwargs):
                return [{"id": "base-1", "kind": "base", "name": "SDXL", "repoId": "stabilityai/stable-diffusion-xl-base-1.0", "status": "available", "path": "/models/sdxl"}]

            def list_datasets(self):
                return [{"id": "ds-image", "name": "文生图测试集", "state": "published", "type": "文生图", "structure": "IMAGE_TEXT", "paths": {"manifestPath": str(root / "metadata.jsonl"), "mediaRoot": str(images)}}]

        class Services:
            def __init__(self):
                self.items = {"svc-image": {"id": "svc-image", "name": "SDXL 服务", "state": "created", "config": {"workloadType": "image_generation", "provider": "youyun", "modelId": "base-1", "modelRepoId": "stabilityai/stable-diffusion-xl-base-1.0", "trainingMode": "lora", "width": 512, "height": 512, "steps": 10, "cfg": 7, "seed": -1, "sampler": "euler", "resource": {"region": "cn", "zone": "cn-1", "gpuType": "A10", "gpuCount": 1, "cpuCores": 8, "memoryGb": 32, "imageId": "diffusers"}}}}

            def get(self, service_id):
                return self.items.get(service_id)

            def update(self, service_id, *, config=None, **changes):
                item = self.items[service_id]
                if config is not None:
                    item["config"] = config
                item.update(changes)
                return item

        self.runtime = ImageGenerationEvaluationRuntime(task_root=root / "evaluations", execution_lock=asyncio.Lock(), executor_factory=lambda runtime: FakeImageGenerationEvaluationExecutor())
        await self.runtime.start()

        async def resolve_resource(resource):
            return {**resource, "price": {"currency": "CNY", "total": 1.0}}

        async def check_image(image_id):
            return {"imageId": image_id, "name": "Diffusers", "message": "Diffusers 镜像可用。"}

        app = FastAPI()
        app.include_router(create_image_generation_router(Store(), Services(), evaluation_runtime=self.runtime, evaluation_root=root / "evaluation-results", youyun_resource_resolver=resolve_resource, youyun_image_checker=check_image))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://image.test")

    async def asyncTearDown(self) -> None:
        await self.runtime.stop()
        await self.client.aclose()
        self.temp.cleanup()

    async def test_catalog_validation_and_service_scoped_evaluation(self) -> None:
        catalog = await self.client.get("/api/image-generation/evaluation-datasets")
        self.assertEqual(catalog.status_code, 200)
        self.assertEqual(catalog.json()[0]["id"], "ds-image")

        created = await self.client.post("/api/image-generation/services/svc-image/evaluations", json={"datasetId": "ds-image"})
        self.assertEqual(created.status_code, 201, created.text)
        task = created.json()
        self.assertEqual(task["serviceId"], "svc-image")
        self.assertEqual(task["state"], "awaiting_confirmation")

        wrong_service = await self.client.get(f"/api/image-generation/services/other/evaluations/{task['id']}")
        self.assertEqual(wrong_service.status_code, 404)

        confirmed = await self.client.post(f"/api/image-generation/services/svc-image/evaluations/{task['id']}/confirm")
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        for _ in range(40):
            detail = await self.client.get(f"/api/image-generation/services/svc-image/evaluations/{task['id']}")
            if detail.json().get("state") == "completed":
                break
            await asyncio.sleep(0.02)
        self.assertEqual(detail.json()["summary"]["successRate"], 1.0)
        sample = await self.client.get(f"/api/image-generation/services/svc-image/evaluations/{task['id']}/samples/sample-1/generated")
        self.assertEqual(sample.status_code, 200)


if __name__ == "__main__":
    unittest.main()
