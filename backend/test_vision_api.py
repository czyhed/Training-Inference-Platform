from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.store import TaskStore
from backend.training_executor import ExecutionHandle, TrainingEvent
from backend.vision_api import VisionDeploymentCreate, create_vision_router
from backend.vision_runtime import VisionTrainingRuntime
from backend.youyun_vision_probe import VisionProbeResult


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"vision-test"


class VisionDeploymentDefaultsTests(unittest.TestCase):
    def test_visual_deployment_defaults_fit_a_single_24gb_gpu(self) -> None:
        payload = VisionDeploymentCreate(name="视觉推理", modelId="vision-model")

        self.assertEqual(payload.gpuMemoryUtilization, 0.75)
        self.assertEqual(payload.maxNumSeqs, 1)
        self.assertEqual(payload.maxNumBatchedTokens, 2048)


class _InferenceStore:
    def __init__(self) -> None:
        self.services = {
            "vision-service": {
                "id": "vision-service",
                "state": "running",
                "config": {"workloadType": "vision_language"},
            }
        }

    def get(self, service_id: str):
        return self.services.get(service_id)

    def list(self, *, include_archived: bool = False):
        return list(self.services.values())

    def create(self, *, service_id, payload, model, adapter, endpoint):
        service = {
            "id": service_id,
            "name": payload.name,
            "taskId": payload.taskId,
            "state": "created",
            "model": model,
            "adapter": adapter,
            "endpoint": endpoint,
            "config": {**payload.model_dump(), "model": model, "adapter": adapter},
        }
        self.services[service_id] = service
        return service


class _InferenceRuntime:
    def __init__(self) -> None:
        self.store = _InferenceStore()
        self.service_id = "vision-service"
        self.active_service_id = "vision-service"
        self.last_body = None

    def _running_service(self, service_id: str):
        service = self.store.get(service_id)
        if service is None or service.get("state") != "running":
            raise RuntimeError("推理服务当前未运行。")
        return service

    def _post_json(self, body):
        self.last_body = body
        return json.dumps(
            {
                "choices": [{"message": {"content": "测试回答"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            },
            ensure_ascii=False,
        )


class VisionApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.project_root = root / "project"
        self.dataset_root = root / "datasets"
        self.runtime_root = root / "runtime"
        self.project_root.mkdir()
        self.dataset_root.mkdir()
        self.runtime_root.mkdir()
        self.store = TaskStore(root / "catalog.db")
        self.runtime = _InferenceRuntime()
        self.resolved_youyun_resources: list[dict] = []
        self.youyun_model_env = patch.dict(
            os.environ,
            {
                "YOUYUN_BASE_MODEL_REPOS": "Qwen/Qwen2.5-VL-3B-Instruct",
                "XUNTUI_REQUIRE_VISION_REMOTE_PROBE": "0",
            },
            clear=False,
        )
        self.youyun_model_env.start()

        async def resolve_youyun_resource(resource: dict) -> dict:
            self.resolved_youyun_resources.append(resource)
            return {
                "mode": "manual",
                "region": "cn-wlcb",
                "zone": "cn-wlcb-01",
                "gpuType": resource["gpuType"],
                "gpuCount": resource["gpuCount"],
                "cpuCores": resource["cpuCores"],
                "memoryGb": resource["memoryGb"],
                "chargeType": "Postpay",
                "price": {
                    "currency": "CNY",
                    "instance": 1.5,
                    "disks": 0.1,
                    "systemDisks": 0.1,
                    "image": 0.0,
                    "total": 1.7,
                },
            }

        app = FastAPI()
        app.include_router(
            create_vision_router(
                self.store,
                self.runtime,
                project_root=self.project_root,
                dataset_root=self.dataset_root,
                runtime_root=self.runtime_root,
                youyun_config_check=lambda: [],
                youyun_resource_resolver=resolve_youyun_resource,
            )
        )
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.youyun_model_env.stop()
        self.temp.cleanup()

    def test_vision_chat_rejects_a_text_inference_service(self) -> None:
        self.runtime.store.services["text-service"] = {
            "id": "text-service",
            "state": "running",
            "config": {"workloadType": "text_generation"},
        }

        response = self.client.post(
            "/api/inference/services/text-service/vision-chat",
            json={
                "serviceId": "ignored-by-route",
                "imageBase64": base64.b64encode(PNG_BYTES).decode("ascii"),
                "imageFilename": "sample.png",
                "question": "图中有什么？",
            },
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("视觉理解", response.json()["detail"])

    def test_remote_probe_route_returns_structured_report(self) -> None:
        async def resolve_resource(resource):
            return {**resource, "price": {"total": 1.7}}

        async def probe(request, resource):
            self.assertEqual(resource["gpuType"], "RTX4090")
            return VisionProbeResult(
                passed=True,
                checks={"forward": {"status": "passed"}},
                blocking_reason=None,
                recommended_tuning_mode=request.tuning_mode,
                estimated_vram_gb=16,
                logs=["fake probe"],
            )

        app = FastAPI()
        app.include_router(
            create_vision_router(
                self.store,
                self.runtime,
                project_root=self.project_root,
                dataset_root=self.dataset_root,
                runtime_root=self.runtime_root,
                youyun_vision_probe=probe,
                youyun_resource_resolver=resolve_resource,
            )
        )
        client = TestClient(app)
        model_id = self._register_local_visual_model()
        response = client.post(
            f"/api/vision/models/{model_id}/remote-probe",
            json={
                "modelPath": "/model/Qwen2.5-VL-3B-Instruct",
                "tuningMode": "qlora",
                "youyunResource": {
                    "gpuType": "RTX4090",
                    "gpuCount": 1,
                    "cpuCores": 16,
                    "memoryGb": 64,
                    "imageId": "image-lf",
                },
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["passed"])
        self.assertEqual(response.json()["recommendedTuningMode"], "qlora")
        model = self.store.get_vision_model(model_id)
        self.assertIsNotNone(model)
        self.assertEqual(model["probeStatus"], "passed")

    def _register_local_visual_model(
        self,
        *,
        repo_id: str = "Qwen/Qwen2.5-VL-3B-Instruct",
        name: str = "Qwen VL",
    ) -> str:
        model_path = self.project_root / "model"
        model_path.mkdir()
        (model_path / "config.json").write_text(
            json.dumps({"vision_config": {"model_type": "qwen2_5_vl"}}),
            encoding="utf-8",
        )
        response = self.client.post(
            "/api/vision/models/register",
            json={
                "name": name,
                "repoId": repo_id,
                "path": str(model_path),
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def _register_remote_visual_model(
        self,
        *,
        repo_id: str = "Qwen/Qwen2.5-VL-3B-Instruct",
        name: str = "Qwen VL Remote",
    ) -> str:
        response = self.client.post(
            "/api/vision/models/register",
            json={
                "name": name,
                "repoId": repo_id,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        model = response.json()
        self.assertEqual(model["probeStatus"], "candidate")
        self.assertFalse(model["supported"])
        return model["id"]

    def _write_visual_training_task(
        self,
        *,
        task_id: str,
        model_id: str,
        state: str = "completed",
        include_best: bool = True,
        include_final: bool = True,
    ) -> dict:
        task_root = self.runtime_root / "vision-tasks"
        task_root.mkdir(parents=True, exist_ok=True)
        artifacts = []
        for variant, selected in (("best", True), ("final", False)):
            if not ((variant == "best" and include_best) or (variant == "final" and include_final)):
                continue
            adapter_path = task_root / task_id / variant
            adapter_path.mkdir(parents=True, exist_ok=True)
            (adapter_path / "adapter_config.json").write_text("{}", encoding="utf-8")
            artifacts.append(
                {
                    "type": "adapter",
                    "variant": variant,
                    "selected": selected,
                    "path": str(adapter_path),
                }
            )
        task = {
            "id": task_id,
            "name": "已完成视觉训练任务",
            "state": state,
            "status": "completed" if state == "completed" else "failed",
            "modelId": model_id,
            "modelRepoId": "Qwen/Qwen2.5-VL-3B-Instruct",
            "model": "Qwen/Qwen2.5-VL-3B-Instruct",
            "provider": "local",
            "artifacts": artifacts,
            "createdAt": "2026-08-28T10:00:00+0800",
        }
        (task_root / f"{task_id}.json").write_text(
            json.dumps(task, ensure_ascii=False), encoding="utf-8"
        )
        return task

    def test_inference_model_catalog_groups_base_and_completed_adapters(self) -> None:
        model_id = self._register_local_visual_model()
        self._write_visual_training_task(task_id="vt-completed", model_id=model_id)
        self._write_visual_training_task(
            task_id="vt-failed", model_id=model_id, state="failed"
        )

        response = self.client.get("/api/vision/inference-models?provider=local")

        self.assertEqual(response.status_code, 200, response.text)
        models = response.json()
        self.assertEqual({model["group"] for model in models}, {"base", "trained"})
        base = next(model for model in models if model["group"] == "base")
        self.assertEqual(base["modelId"], model_id)
        self.assertIsNone(base.get("taskId"))
        trained = [model for model in models if model["group"] == "trained"]
        self.assertEqual({model["adapterVariant"] for model in trained}, {"best", "final"})
        self.assertTrue(all(model["taskId"] == "vt-completed" for model in trained))
        self.assertTrue(all(Path(model["adapterPath"]).is_dir() for model in trained))
        self.assertNotIn("vt-failed", {model.get("taskId") for model in models})

    def test_youyun_inference_model_catalog_is_empty_without_public_models(self) -> None:
        with patch.dict(
            os.environ,
            {"YOUYUN_BASE_MODEL_REPOS": ""},
            clear=False,
        ):
            response = self.client.get("/api/vision/inference-models?provider=youyun")

        self.assertEqual(response.status_code, 200, response.text)
        models = response.json()
        self.assertEqual(models, [])

    def test_create_trained_visual_deployment_resolves_base_and_adapter(self) -> None:
        model_id = self._register_local_visual_model()
        task = self._write_visual_training_task(task_id="vt-trained", model_id=model_id)

        response = self.client.post(
            "/api/vision/deployments",
            json={
                "name": "视觉 Adapter 推理服务",
                "modelType": "trained",
                "modelId": model_id,
                "taskId": task["id"],
                "adapterVariant": "best",
            },
        )

        self.assertEqual(response.status_code, 201, response.text)
        service = response.json()
        self.assertEqual(service["adapter"], task["artifacts"][0]["path"])
        self.assertEqual(service["config"]["modelType"], "trained")
        self.assertEqual(service["config"]["taskId"], task["id"])
        self.assertEqual(service["config"]["adapterVariant"], "best")
        self.assertEqual(service["config"]["modelId"], model_id)

    def test_vision_model_list_is_scoped_to_selected_provider(self) -> None:
        local_id = self._register_local_visual_model(
            repo_id="Qwen/Qwen2.5-VL-3B-Instruct",
            name="本地 Qwen VL",
        )
        remote_id = self._register_remote_visual_model(
            repo_id="Qwen/Qwen2-VL-7B-Instruct",
            name="优云 Qwen VL",
        )

        with patch.dict(
            os.environ,
            {
                "YOUYUN_BASE_MODEL_REPOS": "Qwen/Qwen2-VL-7B-Instruct",
            },
            clear=False,
        ):
            all_models = self.client.get("/api/vision/models")
            local = self.client.get("/api/vision/models?provider=local")
            remote = self.client.get("/api/vision/models?provider=youyun")

        self.assertEqual(all_models.status_code, 200, all_models.text)
        self.assertEqual(local.status_code, 200, local.text)
        self.assertEqual(remote.status_code, 200, remote.text)
        availability = {
            model["id"]: model["availableOnYouyun"]
            for model in all_models.json()
        }
        self.assertFalse(availability[local_id])
        self.assertTrue(availability[remote_id])
        self.assertEqual([model["id"] for model in local.json()], [local_id])
        self.assertEqual([model["id"] for model in remote.json()], [remote_id])

    def test_vision_model_archive_and_restore(self) -> None:
        model_id = self._register_remote_visual_model(
            repo_id="Qwen/Qwen2.5-VL-3B-Instruct",
            name="可归档视觉模型",
        )

        archived = self.client.patch(
            f"/api/vision/models/{model_id}",
            json={"archived": True},
        )
        self.assertEqual(archived.status_code, 200, archived.text)
        self.assertTrue(archived.json()["archived"])
        self.assertEqual(self.client.get("/api/vision/models").json(), [])

        restored = self.client.patch(
            f"/api/vision/models/{model_id}",
            json={"archived": False},
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertFalse(restored.json()["archived"])
        self.assertEqual(len(self.client.get("/api/vision/models").json()), 1)

    def test_youyun_model_list_is_empty_when_catalog_is_empty(self) -> None:
        with patch.dict(
            os.environ,
            {"YOUYUN_BASE_MODEL_REPOS": ""},
            clear=False,
        ):
            response = self.client.get("/api/vision/models?provider=youyun")

        self.assertEqual(response.status_code, 200, response.text)
        models = response.json()
        self.assertEqual(models, [])

    def test_youyun_model_list_keeps_small_base_models_when_large_catalog_is_present(self) -> None:
        catalog = json.dumps([
            {
                "name": "Qwen2.5-VL-72B-Instruct",
                "path": "/model/ModelScope/Qwen/Qwen2.5-VL-72B-Instruct",
                "sizeGb": 137,
            }
        ])
        with patch.dict(
            os.environ,
            {"YOUYUN_BASE_MODEL_CATALOG": catalog},
            clear=False,
        ):
            response = self.client.get("/api/vision/models?provider=youyun")

        self.assertEqual(response.status_code, 200, response.text)
        repos = {model["repoId"] for model in response.json()}
        self.assertIn("Qwen/Qwen2.5-VL-72B-Instruct", repos)
        self.assertNotIn("Qwen/Qwen2.5-VL-3B-Instruct", repos)
        self.assertEqual(repos, {"Qwen/Qwen2.5-VL-72B-Instruct"})
        self.assertEqual(repos, {"Qwen/Qwen2.5-VL-72B-Instruct"})

    def test_youyun_catalog_exposes_mounted_visual_base_models(self) -> None:
        catalog = json.dumps([
            {
                "name": "Qwen3-VL-32B-Thinking-FP8",
                "path": "/model/ModelScope/Qwen/Qwen3-VL-32B-Thinking-FP8",
                "sizeGb": 33,
            },
            {"name": "Qwen3-TTS-12Hz-1.7B-VoiceDesign", "path": "/model/tts"},
        ])
        with patch.dict(os.environ, {"YOUYUN_BASE_MODEL_CATALOG": catalog}, clear=False):
            response = self.client.get("/api/vision/youyun-catalog")

        self.assertEqual(response.status_code, 200, response.text)
        models = response.json()
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["repoId"], "Qwen/Qwen3-VL-32B-Thinking-FP8")
        self.assertEqual(models[0]["remotePath"], "/model/ModelScope/Qwen/Qwen3-VL-32B-Thinking-FP8")
        self.assertEqual(models[0]["template"], "qwen3_vl_nothink")

    def test_youyun_catalog_refreshes_live_repository_before_returning(self) -> None:
        async def load_catalog():
            return [{
                "Name": "Qwen3-VL-4B-Instruct",
                "Path": "/model/ModelScope/Qwen/Qwen3-VL-4B-Instruct",
                "Tag": "LLM,VL",
                "Size": "9GB",
            }]

        app = FastAPI()
        app.include_router(
            create_vision_router(
                self.store,
                self.runtime,
                project_root=self.project_root,
                dataset_root=self.dataset_root,
                runtime_root=self.runtime_root,
                youyun_model_catalog_loader=load_catalog,
            )
        )
        response = TestClient(app).get("/api/vision/youyun-catalog")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()[0]["remotePath"], "/model/ModelScope/Qwen/Qwen3-VL-4B-Instruct")

    def test_youyun_catalog_refresh_is_cached_for_short_interval(self) -> None:
        calls = 0

        async def load_catalog():
            nonlocal calls
            calls += 1
            return [{
                "Name": "Qwen3-VL-4B-Instruct",
                "Path": "/model/ModelScope/Qwen/Qwen3-VL-4B-Instruct",
            }]

        app = FastAPI()
        app.include_router(
            create_vision_router(
                self.store,
                self.runtime,
                project_root=self.project_root,
                dataset_root=self.dataset_root,
                runtime_root=self.runtime_root,
                youyun_model_catalog_loader=load_catalog,
            )
        )
        client = TestClient(app)
        self.assertEqual(client.get("/api/vision/youyun-catalog").status_code, 200)
        self.assertEqual(client.get("/api/vision/models?provider=youyun").status_code, 200)
        self.assertEqual(calls, 1)

    def test_preflight_backfills_remote_path_for_legacy_registered_model(self) -> None:
        model_id = self._register_remote_visual_model()
        dataset_id = self._create_visual_dataset()

        async def load_catalog():
            return [{
                "Name": "Qwen2.5-VL-3B-Instruct",
                "Path": "/model/ModelScope/Qwen/Qwen2.5-VL-3B-Instruct",
            }]

        async def resolve_resource(resource):
            return {**resource, "price": {"total": 1.7}}

        app = FastAPI()
        app.include_router(
            create_vision_router(
                self.store,
                self.runtime,
                project_root=self.project_root,
                dataset_root=self.dataset_root,
                runtime_root=self.runtime_root,
                youyun_model_catalog_loader=load_catalog,
                youyun_config_check=lambda: [],
                youyun_resource_resolver=resolve_resource,
            )
        )
        response = TestClient(app).post(
            "/api/vision/tasks/preflight",
            json={"modelId": model_id, "datasetId": dataset_id, "provider": "youyun"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["remoteModelPath"],
            "/model/ModelScope/Qwen/Qwen2.5-VL-3B-Instruct",
        )
    def test_create_task_persists_public_catalog_path_for_legacy_model(self) -> None:
        model_id = self._register_remote_visual_model()
        dataset_id = self._create_visual_dataset()

        async def load_catalog():
            return [{
                "Name": "Qwen2.5-VL-3B-Instruct",
                "Path": "/model/ModelScope/Qwen/Qwen2.5-VL-3B-Instruct",
            }]

        async def resolve_resource(resource):
            return {**resource, "price": {"total": 1.7}}

        class Runtime:
            def __init__(self):
                self.saved = None

            def save(self, task):
                self.saved = task

            def enqueue(self, task_id):
                return None

            def list_tasks(self, include_details=False):
                return []

        runtime = Runtime()
        app = FastAPI()
        app.include_router(
            create_vision_router(
                self.store,
                runtime,
                project_root=self.project_root,
                dataset_root=self.dataset_root,
                runtime_root=self.runtime_root,
                youyun_model_catalog_loader=load_catalog,
                youyun_config_check=lambda: [],
                youyun_resource_resolver=resolve_resource,
            )
        )
        response = TestClient(app).post(
            "/api/vision/tasks",
            json={
                "name": "公共库路径任务",
                "outputName": "public-catalog-path",
                "modelId": model_id,
                "datasetId": dataset_id,
                "provider": "youyun",
                "youyunResource": {
                    "mode": "manual",
                    "gpuType": "4090",
                    "gpuCount": 1,
                    "cpuCores": 16,
                    "memoryGb": 64,
                    "zone": "cn-wlcb-01",
                },
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(
            response.json()["remoteModelPath"],
            "/model/ModelScope/Qwen/Qwen2.5-VL-3B-Instruct",
        )
        self.assertGreater(response.json()["expiresAtEpoch"], time.time())
        self.assertGreater(response.json()["lastHeartbeatAtEpoch"], 0)

    def test_model_lists_merge_catalog_models_for_management_training_and_inference(self) -> None:
        catalog = json.dumps([
            {
                "name": "Qwen3-VL-32B-Thinking-FP8",
                "path": "/model/ModelScope/Qwen/Qwen3-VL-32B-Thinking-FP8",
                "sizeGb": 33,
            }
        ])
        with patch.dict(os.environ, {"YOUYUN_BASE_MODEL_CATALOG": catalog}, clear=False):
            management = self.client.get("/api/vision/models")
            remote = self.client.get("/api/vision/models?provider=youyun")

        self.assertEqual(management.status_code, 200, management.text)
        self.assertEqual(remote.status_code, 200, remote.text)
        self.assertIn("Qwen/Qwen3-VL-32B-Thinking-FP8", [item["repoId"] for item in management.json()])
        self.assertTrue(any(item["availableOnYouyun"] for item in remote.json()))

    def test_youyun_preflight_rejects_model_without_public_mount(self) -> None:
        model_id = self._register_remote_visual_model()
        dataset_id = self._create_visual_dataset()
        with patch.dict(
            os.environ,
            {"YOUYUN_BASE_MODEL_REPOS": ""},
            clear=False,
        ):
            response = self.client.post(
                "/api/vision/tasks/preflight",
                json={
                    "modelId": model_id,
                    "datasetId": dataset_id,
                    "provider": "youyun",
                    "youyunResource": {
                        "mode": "manual",
                        "gpuType": "4090",
                        "gpuCount": 1,
                        "cpuCores": 8,
                        "memoryGb": 32,
                        "zone": "cn-wlcb-01",
                        "imageId": "torch-image",
                    },
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertFalse(payload["ready"], payload)
        self.assertIn("公共模型库", " ".join(payload["reasons"]))

    def test_youyun_task_rejects_model_without_public_mount(self) -> None:
        model_id = self._register_remote_visual_model()
        dataset_id = self._create_visual_dataset()
        with patch.dict(
            os.environ,
            {"YOUYUN_BASE_MODEL_REPOS": ""},
            clear=False,
        ):
            response = self.client.post(
                "/api/vision/tasks",
                json={
                    "name": "优云视觉下载兜底任务",
                    "outputName": "vision-download-fallback",
                    "modelId": model_id,
                    "datasetId": dataset_id,
                    "provider": "youyun",
                },
            )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("公共模型库", response.text)

    def test_remote_candidate_not_in_youyun_catalog_is_rejected(self) -> None:
        model_id = self._register_remote_visual_model()
        dataset_id = self._create_visual_dataset()
        with patch.dict(
            os.environ,
            {
                "YOUYUN_BASE_MODEL_REPOS": "Qwen/Qwen2-VL-7B-Instruct",
            },
            clear=False,
        ):
            response = self.client.post(
                "/api/vision/tasks/preflight",
                json={
                    "modelId": model_id,
                    "datasetId": dataset_id,
                    "provider": "youyun",
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["ready"])
        self.assertIn("优云公共模型库", " ".join(response.json()["reasons"]))

    def _create_visual_dataset(self, *, dpo: bool = False) -> str:
        dataset_id = "vision-dataset"
        images = self.dataset_root / "images"
        images.mkdir(exist_ok=True)
        (images / "sample.png").write_bytes(PNG_BYTES)
        train_path = self.dataset_root / "train.jsonl"
        record = {
            "image": "images/sample.png",
            "question": "图片里有什么？",
            **(
                {
                    "chosen": "图片中有一张测试图片。",
                    "rejected": "图片中没有可识别内容。",
                }
                if dpo
                else {"answer": "测试图片"}
            ),
        }
        train_path.write_text(
            json.dumps(record,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        self.store.create_dataset(
            {
                "id": dataset_id,
                "name": "视觉数据",
                "type": "视觉理解",
                "structure": "IMAGE_TEXT",
                "state": "published",
                "sampleCount": 1,
                "paths": {"trainPath": str(train_path)},
                "validationMessage": "通过",
                "owner": "tester",
            }
        )
        return dataset_id

    def test_register_and_preflight_visual_model(self) -> None:
        model_id = self._register_local_visual_model()
        dataset_id = self._create_visual_dataset()
        remote = self.client.post(
            "/api/vision/tasks/preflight",
            json={"modelId": model_id, "datasetId": dataset_id, "provider": "youyun"},
        )
        self.assertEqual(remote.status_code, 200, remote.text)
        self.assertTrue(remote.json()["ready"])
        local = self.client.post(
            "/api/vision/tasks/preflight",
            json={"modelId": model_id, "datasetId": dataset_id, "provider": "local"},
        )
        self.assertFalse(local.json()["ready"])
        self.assertIn("未检测到可用 GPU", " ".join(local.json()["reasons"]))

    def test_remote_candidate_model_can_train_but_not_run_local_training(self) -> None:
        model_id = self._register_remote_visual_model()
        dataset_id = self._create_visual_dataset()
        remote = self.client.post(
            "/api/vision/tasks/preflight",
            json={"modelId": model_id, "datasetId": dataset_id, "provider": "youyun"},
        )
        self.assertEqual(remote.status_code, 200, remote.text)
        self.assertTrue(remote.json()["ready"], remote.text)

        preview = self.client.post(
            "/api/vision/tasks/materialize-preview",
            json={
                "name": "远程候选模型预览",
                "outputName": "remote-vision-adapter",
                "modelId": model_id,
                "datasetId": dataset_id,
                "provider": "youyun",
            },
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(
            preview.json()["config"]["model_name_or_path"],
            "Qwen/Qwen2.5-VL-3B-Instruct",
        )

        local = self.client.post(
            "/api/vision/tasks/preflight",
            json={"modelId": model_id, "datasetId": dataset_id, "provider": "local"},
        )
        self.assertEqual(local.status_code, 200, local.text)
        self.assertFalse(local.json()["ready"])
        self.assertIn("本地视觉训练要求", " ".join(local.json()["reasons"]))

    def test_remote_preflight_blocks_unprobed_model_when_gate_is_enabled(self) -> None:
        model_id = self._register_remote_visual_model()
        dataset_id = self._create_visual_dataset()
        with patch.dict(os.environ, {"XUNTUI_REQUIRE_VISION_REMOTE_PROBE": "1"}, clear=False):
            response = self.client.post(
                "/api/vision/tasks/preflight",
                json={"modelId": model_id, "datasetId": dataset_id, "provider": "youyun"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["ready"])
        self.assertIn("远端兼容性探针", " ".join(response.json()["reasons"]))

    def test_visual_task_persists_resolved_youyun_resource(self) -> None:
        model_id = self._register_remote_visual_model()
        dataset_id = self._create_visual_dataset()
        selection = {
            "mode": "manual",
            "gpuType": "4090",
            "gpuCount": 1,
            "cpuCores": 16,
            "memoryGb": 64,
            "zone": "cn-wlcb-01",
        }

        response = self.client.post(
            "/api/vision/tasks",
            json={
                "name": "视觉优云资源测试",
                "outputName": "vision-youyun-resource",
                "modelId": model_id,
                "datasetId": dataset_id,
                "provider": "youyun",
                "youyunResource": selection,
            },
        )

        self.assertEqual(response.status_code, 202, response.text)
        task = response.json()
        self.assertEqual(task["youyunResource"]["gpuType"], "4090")
        self.assertEqual(task["youyunResource"]["cpuCores"], 16)
        self.assertEqual(task["youyunResource"]["memoryGb"], 64)
        self.assertEqual(task["youyunResource"]["price"]["total"], 1.7)
        self.assertTrue(self.resolved_youyun_resources)
        self.assertEqual(self.resolved_youyun_resources[-1], selection)

    def test_materialize_visual_training_preview(self) -> None:
        model_id = self._register_local_visual_model()
        dataset_id = self._create_visual_dataset()
        response = self.client.post(
            "/api/vision/tasks/materialize-preview",
            json={
                "name": "视觉训练预览",
                "outputName": "vision-adapter",
                "modelId": model_id,
                "datasetId": dataset_id,
                "provider": "youyun",
                "tuningMode": "qlora",
                "validationSplit": 10,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()
        self.assertEqual(preview["status"], "materialized")
        self.assertEqual(preview["taskType"], "SFT")
        self.assertEqual(preview["config"]["template"], "qwen2_vl")
        self.assertEqual(preview["manifest"]["trainSamples"], 1)
        self.assertEqual(preview["manifest"]["validationSamples"], 0)
        self.assertTrue(Path(preview["configPath"]).is_file())

    def test_materialize_visual_dpo_training_preview(self) -> None:
        model_id = self._register_local_visual_model()
        dataset_id = self._create_visual_dataset(dpo=True)
        response = self.client.post(
            "/api/vision/tasks/materialize-preview",
            json={
                "name": "视觉 DPO 预览",
                "outputName": "vision-dpo-adapter",
                "modelId": model_id,
                "datasetId": dataset_id,
                "provider": "youyun",
                "taskType": "DPO",
                "dpoBeta": 0.2,
                "tuningMode": "qlora",
                "validationSplit": 0,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()
        self.assertEqual(preview["taskType"], "DPO")
        self.assertEqual(preview["config"]["stage"], "dpo")
        self.assertEqual(preview["config"]["pref_beta"], 0.2)
        self.assertEqual(preview["manifest"]["taskType"], "DPO")

    def test_dpo_preflight_rejects_sft_visual_dataset(self) -> None:
        model_id = self._register_local_visual_model()
        dataset_id = self._create_visual_dataset()
        response = self.client.post(
            "/api/vision/tasks/preflight",
            json={
                "modelId": model_id,
                "datasetId": dataset_id,
                "provider": "youyun",
                "taskType": "DPO",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["ready"])
        self.assertIn("chosen", " ".join(response.json()["reasons"]))

    def test_create_dpo_task_persists_training_type_and_beta(self) -> None:
        model_id = self._register_local_visual_model()
        dataset_id = self._create_visual_dataset(dpo=True)
        response = self.client.post(
            "/api/vision/tasks",
            json={
                "name": "视觉偏好任务",
                "outputName": "vision-dpo-adapter",
                "modelId": model_id,
                "datasetId": dataset_id,
                "provider": "youyun",
                "taskType": "DPO",
                "dpoBeta": 0.25,
                "tuningMode": "qlora",
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        task = response.json()
        self.assertEqual(task["taskType"], "DPO")
        self.assertEqual(task["method"], "视觉 DPO / QLORA")
        self.assertEqual(task["parameters"]["dpoBeta"], 0.25)
        self.assertIn("stage: dpo", task["yaml"])

    def test_create_list_clone_and_delete_visual_training_task(self) -> None:
        model_id = self._register_local_visual_model()
        dataset_id = self._create_visual_dataset()
        payload = {
            "name": "视觉理解任务",
            "outputName": "vision-adapter",
            "modelId": model_id,
            "datasetId": dataset_id,
            "provider": "youyun",
            "tuningMode": "qlora",
            "validationSplit": 10,
        }

        created = self.client.post("/api/vision/tasks", json=payload)
        self.assertEqual(created.status_code, 202, created.text)
        task = created.json()
        self.assertEqual(task["state"], "awaiting_executor")
        self.assertEqual(task["status"], "configured")
        self.assertTrue(task["yaml"])

        listed = self.client.get("/api/vision/tasks")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()[0]["id"], task["id"])
        self.assertNotIn("yaml", listed.json()[0])

        clone = self.client.post(f"/api/vision/tasks/{task['id']}/clone")
        self.assertEqual(clone.status_code, 201, clone.text)
        self.assertNotEqual(clone.json()["id"], task["id"])
        self.assertTrue(clone.json()["name"].endswith("副本"))

        deleted = self.client.delete(f"/api/vision/tasks/{task['id']}")
        self.assertEqual(deleted.status_code, 204, deleted.text)
        self.assertEqual(self.client.get(f"/api/vision/tasks/{task['id']}").status_code, 404)

    def test_delete_visual_task_blocks_when_remote_cleanup_failed(self) -> None:
        task_id = "vt-cleanup-guard"
        task_path = self.runtime_root / "vision-tasks" / f"{task_id}.json"
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(
            json.dumps(
                {
                    "id": task_id,
                    "provider": "youyun",
                    "state": "failed",
                    "status": "failed",
                    "cleanupState": "failed",
                    "resourceState": "cleanup_failed",
                    "remoteHandle": {"instanceId": "uhost-leaked"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        response = self.client.delete(f"/api/vision/tasks/{task_id}")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("清理失败", response.json()["detail"])
        self.assertTrue(task_path.exists())

    def test_materialize_preview_keeps_bounded_runtime_directories(self) -> None:
        model_id = self._register_local_visual_model()
        dataset_id = self._create_visual_dataset()
        preview_root = self.runtime_root / "vision-training-previews"
        now = time.time()
        for index in range(12):
            path = preview_root / f"old-{index:02d}"
            path.mkdir()
            os.utime(path, (now - index, now - index))

        response = self.client.post(
            "/api/vision/tasks/materialize-preview",
            json={
                "name": "预览目录上限",
                "outputName": "vision-adapter",
                "modelId": model_id,
                "datasetId": dataset_id,
                "provider": "youyun",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertLessEqual(len([path for path in preview_root.iterdir() if path.is_dir()]), 10)

    def test_visual_chat_uses_openai_multimodal_shape(self) -> None:
        response = self.client.post(
            "/api/inference/services/vision-service/vision-chat",
            json={
                "serviceId": "ignored-by-path",
                "imageBase64": base64.b64encode(PNG_BYTES).decode("ascii"),
                "imageFilename": "sample.png",
                "question": "图片里有什么？",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["answer"], "测试回答")
        content = self.runtime.last_body["messages"][0]["content"]
        self.assertEqual(content[0]["type"], "image_url")
        self.assertTrue(content[0]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_visual_deployment_creates_base_model_service(self) -> None:
        model_id = self._register_local_visual_model()
        response = self.client.post(
            "/api/vision/deployments",
            json={"name": "视觉理解服务", "modelId": model_id},
        )
        self.assertEqual(response.status_code, 201, response.text)
        service = response.json()
        self.assertEqual(service["state"], "created")
        self.assertEqual(service["adapter"], "")
        self.assertEqual(service["config"]["workloadType"], "vision_language")
        self.assertEqual(service["deploymentStatus"], "configured")

    def test_youyun_visual_deployment_accepts_remote_candidate_and_persists_resource(self) -> None:
        model_id = self._register_remote_visual_model()
        selection = {
            "mode": "manual",
            "gpuType": "4090",
            "gpuCount": 1,
            "cpuCores": 16,
            "memoryGb": 64,
        }

        response = self.client.post(
            "/api/vision/deployments",
            json={
                "name": "优云视觉理解服务",
                "modelId": model_id,
                "provider": "youyun",
                "youyunResource": selection,
            },
        )

        self.assertEqual(response.status_code, 201, response.text)
        service = response.json()
        self.assertEqual(service["state"], "created")
        self.assertEqual(service["model"], "Qwen/Qwen2.5-VL-3B-Instruct")
        self.assertEqual(service["config"]["workloadType"], "vision_language")
        self.assertEqual(service["config"]["provider"], "youyun")
        self.assertEqual(service["config"]["youyunResource"]["gpuType"], "4090")
        self.assertEqual(service["config"]["youyunResource"]["price"]["total"], 1.7)

    def test_youyun_visual_deployment_rejects_missing_resource(self) -> None:
        model_id = self._register_remote_visual_model()
        response = self.client.post(
            "/api/vision/deployments",
            json={
                "name": "缺少资源的优云视觉服务",
                "modelId": model_id,
                "provider": "youyun",
            },
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("youyunResource", response.json()["detail"])

    def test_youyun_visual_deployment_rejects_model_without_public_mount(self) -> None:
        model_id = self._register_remote_visual_model()
        with patch.dict(
            os.environ,
            {"YOUYUN_BASE_MODEL_REPOS": ""},
            clear=False,
        ):
            response = self.client.post(
                "/api/vision/deployments",
                json={
                    "name": "优云视觉在线下载服务",
                    "modelId": model_id,
                    "provider": "youyun",
                    "youyunResource": {
                        "mode": "manual",
                        "gpuType": "4090",
                        "gpuCount": 1,
                        "cpuCores": 16,
                        "memoryGb": 64,
                    },
                },
            )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("公共模型库", response.text)

    def test_evaluation_validation_rejects_path_outside_allowed_roots(self) -> None:
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        response = self.client.post(
            "/api/vision/evaluations/validate",
            json={"datasetPath": str(outside)},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("项目目录或数据集根目录", response.text)

    def test_evaluation_dataset_validation_does_not_require_service(self) -> None:
        evaluation = self.project_root / "evaluation"
        images = evaluation / "images"
        images.mkdir(parents=True)
        (images / "sample.png").write_bytes(PNG_BYTES)
        (evaluation / "metadata.jsonl").write_text(
            json.dumps(
                {
                    "image": "images/sample.png",
                    "question": "图片里有什么？",
                    "answer": "测试图片",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        response = self.client.post(
            "/api/vision/evaluations/validate",
            json={"datasetPath": str(evaluation)},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["valid"])
        self.assertEqual(response.json()["sampleCount"], 1)

    def test_evaluation_dataset_catalog_lists_valid_project_datasets(self) -> None:
        evaluation = self.project_root / "data" / "catalogued-eval"
        images = evaluation / "images"
        images.mkdir(parents=True)
        (images / "sample.png").write_bytes(PNG_BYTES)
        (evaluation / "README.md").write_text("# 可选视觉测试集\n用于本地评测。\n", encoding="utf-8")
        (evaluation / "metadata.jsonl").write_text(
            json.dumps({"image": "images/sample.png", "question": "图中有什么？", "answer": "测试图片"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (self.project_root / "data" / "not-a-dataset").mkdir(parents=True)

        response = self.client.get("/api/vision/evaluation-datasets")

        self.assertEqual(response.status_code, 200, response.text)
        catalog = response.json()
        item = next(entry for entry in catalog if entry["datasetPath"] == "data/catalogued-eval")
        self.assertEqual(item["name"], "可选视觉测试集")
        self.assertEqual(item["metadataName"], "metadata.jsonl")
        self.assertEqual(item["sampleCount"], 1)
        self.assertEqual(item["missingImages"], 0)
        self.assertTrue(item["valid"])
        self.assertFalse(any(entry["datasetPath"] == "data/not-a-dataset" for entry in catalog))


class _IntegrationProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.waited = False

    async def wait(self) -> int:
        self.waited = True
        self.returncode = 0
        return 0


class _IntegrationVisionExecutor:
    def __init__(self) -> None:
        self.handles: dict[str, ExecutionHandle] = {}

    async def prepare(self, task):
        root = Path(str(task["previewRoot"])).resolve()
        if not root.is_dir():
            raise ValueError("集成测试预览目录不存在。")
        output = root / "output"
        output.mkdir(parents=True, exist_ok=True)
        handle = ExecutionHandle(
            task_id=str(task["id"]),
            workdir=str(root),
            config_path=str(root / "train.yaml"),
            prepared=True,
        )
        self.handles[handle.task_id] = handle
        return handle

    async def start(self, task):
        handle = self.handles[str(task["id"])]
        handle.process = _IntegrationProcess()
        return handle

    async def stop(self, task):
        handle = self.handles.get(str(task["id"]))
        if handle and handle.process is not None:
            await handle.process.wait()

    async def status(self, task):
        handle = self.handles.get(str(task["id"]))
        return {"state": "running" if handle and handle.process else "prepared"}

    async def events(self, task):
        yield TrainingEvent("log", "集成测试视觉训练已启动")
        yield TrainingEvent(
            "metric",
            "{'loss': 1.1, 'eval_loss': 0.9, 'epoch': 1.0}",
            {"loss": 1.1, "eval_loss": 0.9, "epoch": 1.0, "step": 1, "total_steps": 1, "progress": 95},
        )

    async def collect_artifacts(self, task):
        root = Path(str(task["previewRoot"]))
        output = root / "output"
        (output / "adapter_config.json").write_text("{}", encoding="utf-8")
        return [{"type": "adapter", "variant": "final", "selected": True, "path": str(output)}]

    async def cleanup(self, task):
        await self.stop(task)


class VisionRuntimeApiIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.project_root = root / "project"
        self.dataset_root = root / "datasets"
        self.runtime_root = root / "runtime"
        self.project_root.mkdir()
        self.dataset_root.mkdir()
        self.runtime_root.mkdir()

        model_path = self.project_root / "model"
        model_path.mkdir()
        (model_path / "config.json").write_text(
            json.dumps({"vision_config": {"model_type": "qwen2_5_vl"}}),
            encoding="utf-8",
        )
        images = self.dataset_root / "images"
        images.mkdir()
        (images / "sample.png").write_bytes(PNG_BYTES)
        train_path = self.dataset_root / "train.jsonl"
        train_path.write_text(
            json.dumps(
                {
                    "image": "images/sample.png",
                    "question": "图片里有什么？",
                    "answer": "测试图片",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        self.store = TaskStore(root / "catalog.db")
        self.store.upsert_vision_model(
            {
                "id": "vision-integration-model",
                "name": "集成测试 Qwen VL",
                "repoId": "Qwen/Qwen2.5-VL-3B-Instruct",
                "revision": "main",
                "path": str(model_path),
                "capabilities": [
                    "vision_language",
                    "image_understanding",
                    "image_qa",
                    "lora_training",
                    "qlora_training",
                ],
                "supported": True,
                "probeStatus": "passed",
                "probeMessage": "集成测试模型已通过探针。",
            }
        )
        self.store.create_dataset(
            {
                "id": "vision-integration-dataset",
                "name": "集成测试视觉数据",
                "type": "视觉理解",
                "structure": "IMAGE_TEXT",
                "state": "published",
                "sampleCount": 1,
                "paths": {"trainPath": str(train_path)},
                "validationMessage": "通过",
                "owner": "tester",
            }
        )

        self.executor = _IntegrationVisionExecutor()
        self.runtime = VisionTrainingRuntime(
            task_root=self.runtime_root / "vision-tasks",
            local_executor=self.executor,
            execution_lock=asyncio.Lock(),
        )
        app = FastAPI()
        app.include_router(
            create_vision_router(
                self.store,
                _InferenceRuntime(),
                project_root=self.project_root,
                dataset_root=self.dataset_root,
                runtime_root=self.runtime_root,
                training_runtime=self.runtime,
                gpu_probe=lambda: {
                    "available": True,
                    "name": "Integration GPU",
                    "memoryFreeMb": 8192,
                    "memoryTotalMb": 8192,
                },
                youyun_config_check=lambda: ["YOUYUN_PUBLIC_KEY"],
            )
        )
        self.app = app
        await self.runtime.start()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://vision.test",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await self.runtime.stop()
        self.temp.cleanup()

    async def _wait_for_state(self, task_id: str, expected: str) -> dict:
        for _ in range(100):
            response = await self.client.get(f"/api/vision/tasks/{task_id}")
            self.assertEqual(response.status_code, 200, response.text)
            task = response.json()
            if task.get("state") == expected:
                return task
            await asyncio.sleep(0.01)
        self.fail(f"视觉任务未在测试窗口内进入 {expected}：{task}")

    async def test_confirmation_endpoint_enters_runtime_and_persists_results(self) -> None:
        payload = {
            "name": "视觉运行闭环集成测试",
            "outputName": "vision-integration-adapter",
            "modelId": "vision-integration-model",
            "datasetId": "vision-integration-dataset",
            "provider": "local",
            "tuningMode": "qlora",
            "validationSplit": 0,
        }
        created = await self.client.post("/api/vision/tasks", json=payload)
        self.assertEqual(created.status_code, 202, created.text)
        queued = created.json()
        self.assertEqual(queued["state"], "queued")

        completed = await self._wait_for_state(queued["id"], "completed")
        self.assertEqual(completed["progress"], 100)
        self.assertEqual(completed["lossHistory"][0]["evalLoss"], 0.9)
        self.assertEqual(completed["bestStep"], 1)
        self.assertEqual(completed["finalStep"], 1)
        self.assertEqual(completed["artifacts"][0]["type"], "adapter")
        process = self.executor.handles[queued["id"]].process
        assert process is not None
        self.assertTrue(process.waited)

        listed = await self.client.get("/api/vision/tasks")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertNotIn("logs", listed.json()[0])
        self.assertNotIn("lossHistory", listed.json()[0])

        clone = await self.client.post(f"/api/vision/tasks/{queued['id']}/clone")
        self.assertEqual(clone.status_code, 201, clone.text)
        clone_task = clone.json()
        self.assertEqual(clone_task["state"], "awaiting_confirmation")
        self.assertIsNone(clone_task["confirmedAt"])

        confirmed = await self.client.post(f"/api/vision/tasks/{clone_task['id']}/confirm")
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(confirmed.json()["state"], "queued")
        self.assertIsNotNone(confirmed.json()["confirmedAt"])
        rerun = await self._wait_for_state(clone_task["id"], "completed")
        self.assertEqual(rerun["finalStep"], 1)

    async def test_youyun_preflight_and_confirmation_require_provider_config(self) -> None:
        parameters = {
            "datasetId": "vision-integration-dataset",
            "modelId": "vision-integration-model",
            "provider": "youyun",
            "taskType": "SFT",
            "dpoBeta": 0.1,
            "tuningMode": "qlora",
            "imageMaxPixels": 401408,
            "batchSize": 1,
            "gradientAccumulation": 8,
            "validationSplit": 0,
        }
        preflight = await self.client.post("/api/vision/tasks/preflight", json=parameters)
        self.assertEqual(preflight.status_code, 200, preflight.text)
        self.assertFalse(preflight.json()["ready"])
        self.assertIn("YOUYUN_PUBLIC_KEY", " ".join(preflight.json()["reasons"]))

        self.runtime.save(
            {
                "id": "vt-youyun-pending",
                "name": "缺少优云配置的视觉任务",
                "state": "awaiting_confirmation",
                "status": "draft",
                "provider": "youyun",
                "parameters": parameters,
                "createdAt": "2026-08-27T00:00:00+0800",
            }
        )
        confirmed = await self.client.post("/api/vision/tasks/vt-youyun-pending/confirm")
        self.assertEqual(confirmed.status_code, 409, confirmed.text)
        self.assertIn("YOUYUN_PUBLIC_KEY", confirmed.text)
        task = self.runtime.get("vt-youyun-pending")
        assert task is not None
        self.assertEqual(task["state"], "awaiting_confirmation")


if __name__ == "__main__":
    unittest.main()
