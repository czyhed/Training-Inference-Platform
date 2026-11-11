from __future__ import annotations

import asyncio
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import httpx
from fastapi import FastAPI
from PIL import Image

from backend.image_generation_api import build_image_generation_catalog, create_image_generation_router
from backend.image_generation_executor import FakeImageGenerationTrainingExecutor
from backend.image_generation_model_sync import sync_image_generation_adapters
from backend.image_generation_training_runtime import ImageGenerationTrainingRuntime
from backend.image_generation_evaluation_executor import FakeImageGenerationEvaluationExecutor
from backend.image_generation_evaluation_runtime import ImageGenerationEvaluationRuntime


class CatalogStore:
    def __init__(self, root: Path) -> None:
        self.assets = [{"id": "base-sdxl", "kind": "base", "name": "SDXL", "repoId": "stabilityai/stable-diffusion-xl-base-1.0", "status": "available", "path": str(root / "model")}]

    def ensure_base_model(self, repo_id: str, *, path: str, source: str):
        return {"id": "base-sdxl", "name": "SDXL", "repoId": repo_id, "status": "available", "path": path}

    def upsert_trained_model_asset(self, **kwargs):
        self.assets = [item for item in self.assets if item.get("id") != f"trained-{kwargs['task_id']}"]
        self.assets.append({"id": f"trained-{kwargs['task_id']}", "kind": "trained", "name": kwargs["name"], "repoId": kwargs["repo_id"], "status": "available", "baseModelId": kwargs["base_model_id"], "taskId": kwargs["task_id"], "dataset": kwargs["dataset_name"], "variants": kwargs["variants"]})
        return kwargs["variants"]

    def list_model_assets(self, **kwargs):
        return list(self.assets)


class ImageGenerationE2ETests(unittest.TestCase):
    def test_fake_training_publishes_adapter_catalog_entry(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                runtime = ImageGenerationTrainingRuntime(task_root=root / "tasks", execution_lock=asyncio.Lock(), executor_factory=lambda runtime: FakeImageGenerationTrainingExecutor(), on_terminal=lambda task: sync_image_generation_adapters(store, task))
                store = CatalogStore(root)
                preview = root / "preview"
                preview.mkdir()
                task = runtime.create_task({"id": "igt-e2e", "datasetId": "ds-1", "datasetName": "demo", "modelId": "base-sdxl", "modelRepoId": "stabilityai/stable-diffusion-xl-base-1.0", "previewRoot": str(preview), "outputDir": str(preview / "output")})
                self.assertEqual(task["state"], "awaiting_confirmation")
                await runtime.confirm(task["id"])
                await runtime.start()
                await asyncio.wait_for(runtime._queue.join(), timeout=1)
                completed = runtime.get(task["id"])
                self.assertEqual(completed["state"], "completed")
                with patch.dict("os.environ", {"XUNTUI_IMAGE_GENERATION_VERIFIED_REPOS": "stabilityai/stable-diffusion-xl-base-1.0"}, clear=False):
                    catalog = build_image_generation_catalog(store, provider="youyun")
                self.assertEqual({item["adapterVariant"] for item in catalog if item["group"] == "trained"}, {"best", "final"})
                await runtime.stop()

        asyncio.run(scenario())

    def test_service_scoped_evaluation_e2e_uses_fake_executor(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                images = root / "images"
                images.mkdir()
                Image.new("RGB", (32, 32), "white").save(images / "one.png")
                Image.new("RGB", (32, 32), "black").save(images / "two.png")
                (root / "metadata.jsonl").write_text(
                    '{"id":"sample-1","file_name":"one.png","caption":"a white square"}\n'
                    '{"id":"sample-2","file_name":"two.png","caption":"a black square"}\n',
                    encoding="utf-8",
                )

                class Store:
                    def list_model_assets(self, **kwargs):
                        return [{"id": "base", "kind": "base", "name": "SDXL", "repoId": "stabilityai/stable-diffusion-xl-base-1.0", "status": "available", "path": str(root / "model")}]

                    def list_datasets(self):
                        return [{"id": "ds-e2e", "name": "E2E", "state": "published", "type": "文生图", "structure": "IMAGE_TEXT", "paths": {"manifestPath": str(root / "metadata.jsonl"), "mediaRoot": str(images)}}]

                class Services:
                    def get(self, service_id):
                        return {"id": service_id, "name": "E2E 服务", "config": {"workloadType": "image_generation", "provider": "youyun", "modelId": "base", "modelRepoId": "stabilityai/stable-diffusion-xl-base-1.0", "width": 64, "height": 64, "steps": 1, "cfg": 5, "seed": 1, "sampler": "euler", "resource": {"region": "cn", "zone": "cn-1", "gpuCount": 1, "cpuCores": 4, "memoryGb": 16, "gpuType": "A10", "imageId": "diffusers"}}}

                runtime = ImageGenerationEvaluationRuntime(task_root=root / "tasks", execution_lock=asyncio.Lock(), executor_factory=lambda runtime: FakeImageGenerationEvaluationExecutor())
                await runtime.start()
                async def resolve_resource(resource):
                    return {**resource, "price": {"currency": "CNY", "total": 1.0}}

                async def check_image(image_id):
                    return {"imageId": image_id, "message": "ok"}

                app = FastAPI()
                app.include_router(create_image_generation_router(Store(), Services(), evaluation_runtime=runtime, evaluation_root=root / "results", youyun_resource_resolver=resolve_resource, youyun_image_checker=check_image))
                with patch.dict("os.environ", {"XUNTUI_IMAGE_GENERATION_VERIFIED_REPOS": "stabilityai/stable-diffusion-xl-base-1.0"}, clear=False):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://e2e.test") as client:
                        created = await client.post("/api/image-generation/services/svc-e2e/evaluations", json={"datasetId": "ds-e2e"})
                        self.assertEqual(created.status_code, 201, created.text)
                        task_id = created.json()["id"]
                        confirmed = await client.post(f"/api/image-generation/services/svc-e2e/evaluations/{task_id}/confirm")
                        self.assertEqual(confirmed.status_code, 200, confirmed.text)
                        for _ in range(40):
                            detail = (await client.get(f"/api/image-generation/services/svc-e2e/evaluations/{task_id}")).json()
                            if detail.get("state") == "completed":
                                break
                            await asyncio.sleep(0.02)
                        self.assertEqual(detail["summary"]["completedSamples"], 2)
                        self.assertEqual(detail["summary"]["successRate"], 1.0)
                        self.assertTrue((root / "results" / task_id / "generated" / "sample-1.png").is_file())
                await runtime.stop()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
