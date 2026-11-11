from __future__ import annotations

import base64
import asyncio
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi import Request
from PIL import Image

from backend import app as app_module
from backend import image_generation_api as image_generation_api_module
from backend.image_generation_api import ImageGenerationServiceCreate, build_image_generation_catalog, create_image_generation_router
from backend.image_generation_runtime import ImageGenerationRuntime
from backend.image_generation_validation import (
    MAX_IMAGE_DATASET_FILES,
    MAX_IMAGE_UPLOAD_BYTES,
    ImageAssetInspection,
    normalize_image_dataset,
    validate_image_upload,
)


def image_bytes(fmt: str) -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (3, 2), (20, 40, 60)).save(stream, format=fmt)
    return stream.getvalue()


class ImageGenerationValidationTest(unittest.TestCase):
    def test_dataset_records_reads_image_generation_manifest_without_train_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset_dir = root / "ds-image"
            images_dir = dataset_dir / "images"
            images_dir.mkdir(parents=True)
            manifest_path = dataset_dir / "metadata.jsonl"
            manifest_path.write_text(
                json.dumps({"file_name": "images/one.png", "caption": "a white square"}) + "\n",
                encoding="utf-8",
            )
            dataset = {
                "id": "ds-image",
                "type": "文生图",
                "structure": "IMAGE_TEXT",
                "paths": {"mediaRoot": str(images_dir), "manifestPath": str(manifest_path)},
            }
            with patch.object(app_module.store, "get_dataset", return_value=dataset):
                result = asyncio.run(app_module.dataset_records("ds-image", page=1, page_size=10, query=""))
            self.assertEqual(result["total"], 1)
            self.assertEqual(result["items"][0]["record"]["caption"], "a white square")

    def test_service_schema_accepts_diffusers_runtime_options_with_compatible_defaults(self) -> None:
        payload = ImageGenerationServiceCreate(
            name="runtime options",
            modelId="base-1",
            resource={"region": "cn", "zone": "cn-1", "gpuType": "A10", "gpuCount": 1, "cpuCores": 8, "memoryGb": 32, "imageId": "diffusers"},
        )
        self.assertEqual(payload.precision, "auto")
        self.assertFalse(payload.enableCpuOffload)
        self.assertFalse(payload.enableAttentionSlicing)
        self.assertFalse(payload.enableVaeTiling)
        self.assertFalse(payload.enableSafetyChecker)
        self.assertEqual(payload.loadTimeout, 180)

    def test_service_schema_accepts_inference_cost_guardrails(self) -> None:
        payload = ImageGenerationServiceCreate(
            name="guardrails", modelId="base-1", maxConcurrentRequests=2,
            idleTimeoutSeconds=900, requestTimeoutSeconds=120,
            resource={"region": "cn", "zone": "cn-1", "gpuType": "A10", "gpuCount": 1, "cpuCores": 8, "memoryGb": 32, "imageId": "diffusers"},
        )
        self.assertEqual(payload.maxConcurrentRequests, 2)
        self.assertEqual(payload.idleTimeoutSeconds, 900)
        self.assertEqual(payload.requestTimeoutSeconds, 120)

    def test_service_schema_rejects_invalid_precision_and_timeout(self) -> None:
        with self.assertRaises(ValueError):
            ImageGenerationServiceCreate(
                name="invalid", modelId="base-1", precision="int8",
                resource={"region": "cn", "zone": "cn-1", "gpuType": "A10", "gpuCount": 1, "cpuCores": 8, "memoryGb": 32, "imageId": "diffusers"},
            )

        with self.assertRaises(ValueError):
            ImageGenerationServiceCreate(
                name="invalid", modelId="base-1", loadTimeout=10,
                resource={"region": "cn", "zone": "cn-1", "gpuType": "A10", "gpuCount": 1, "cpuCores": 8, "memoryGb": 32, "imageId": "diffusers"},
            )

    def test_image_selection_uses_cuda_torch_fallback_when_no_diffusers_image_exists(self) -> None:
        images = [
            {"image_id": "comfy", "name": "ComfyUI基础镜像", "description": "", "image_type": "App", "status": "Available"},
            {"image_id": "cuda-torch", "name": "cuda128_torch291_py312", "description": "CUDA + PyTorch", "image_type": "App", "status": "Available"},
        ]
        with patch.dict(os.environ, {"XUNTUI_REMOTE_DIFFUSERS_INSTALL": "1"}, clear=True):
            allowed, selected = image_generation_api_module.select_image_generation_images(images)
        self.assertEqual([item["image_id"] for item in allowed], ["cuda-torch"])
        self.assertEqual(selected["image_id"], "cuda-torch")

    def test_image_selection_prefers_recommended_cuda132_image_over_legacy_config(self) -> None:
        images = [
            {"image_id": "legacy", "name": "cuda128_torch291_py312", "description": "PyTorch", "image_type": "App", "status": "Available"},
            {"image_id": "recommended", "name": "cuda132_torch2130_py312", "description": "CUDA + PyTorch", "image_type": "App", "status": "Available"},
        ]
        with patch.dict(os.environ, {"XUNTUI_REMOTE_DIFFUSERS_INSTALL": "1"}, clear=False):
            allowed, selected = image_generation_api_module.select_image_generation_images(images, configured_image_id="legacy")
        self.assertEqual([item["image_id"] for item in allowed], ["legacy", "recommended"])
        self.assertEqual(selected["image_id"], "recommended")

    def test_image_selection_honors_explicit_image_choice(self) -> None:
        images = [
            {"image_id": "legacy", "name": "cuda128_torch291_py312", "description": "PyTorch", "image_type": "App", "status": "Available"},
            {"image_id": "recommended", "name": "cuda132_torch2130_py312", "description": "CUDA + PyTorch", "image_type": "App", "status": "Available"},
        ]
        with patch.dict(os.environ, {"XUNTUI_REMOTE_DIFFUSERS_INSTALL": "1"}, clear=False):
            _, selected = image_generation_api_module.select_image_generation_images(
                images,
                configured_image_id="legacy",
                prefer_configured_image=True,
            )
        self.assertEqual(selected["image_id"], "legacy")

    def test_image_selection_requires_diffusers_unless_install_fallback_is_enabled(self) -> None:
        images = [
            {"image_id": "plain", "name": "cuda128_torch280_py312", "description": "PyTorch", "image_type": "App", "status": "Available"},
            {"image_id": "diffusers", "name": "Diffusers 0.35", "description": "torch + diffusers", "image_type": "App", "status": "Available"},
            {"image_id": "system", "name": "Ubuntu", "description": "system", "image_type": "System", "status": "Available"},
        ]
        with patch.dict(os.environ, {"XUNTUI_REMOTE_DIFFUSERS_INSTALL": "0"}, clear=False):
            allowed, selected = image_generation_api_module.select_image_generation_images(images, configured_image_id="plain")
        self.assertEqual([item["image_id"] for item in allowed], ["diffusers"])
        self.assertEqual(selected["image_id"], "diffusers")

        without_diffusers = [images[0], images[2]]
        with patch.dict(os.environ, {"XUNTUI_REMOTE_DIFFUSERS_INSTALL": "0"}, clear=False):
            with self.assertRaises(ValueError):
                image_generation_api_module.select_image_generation_images(without_diffusers, configured_image_id="plain")
        with patch.dict(os.environ, {"XUNTUI_REMOTE_DIFFUSERS_INSTALL": "1"}, clear=False):
            allowed, selected = image_generation_api_module.select_image_generation_images(without_diffusers, configured_image_id="plain")
        self.assertEqual([item["image_id"] for item in allowed], ["plain"])
        self.assertEqual(selected["image_id"], "plain")

    def test_image_selection_allows_explicit_training_install_fallback_without_global_flag(self) -> None:
        images = [{
            "image_id": "cuda132",
            "name": "cuda132_torch2130_py312",
            "description": "CUDA + PyTorch",
            "image_type": "App",
            "status": "Available",
        }]
        with patch.dict(os.environ, {"XUNTUI_REMOTE_DIFFUSERS_INSTALL": "0"}, clear=False):
            allowed, selected = image_generation_api_module.select_image_generation_images(
                images,
                allow_install_fallback=True,
            )
        self.assertEqual([item["image_id"] for item in allowed], ["cuda132"])
        self.assertEqual(selected["image_id"], "cuda132")

    def test_image_selection_falls_back_to_community_images_when_no_app_images_exist(self) -> None:
        # Youyun accounts can expose a community-only catalog; the install
        # fallback must still find a usable base image there.
        images = [
            {"image_id": "llamafactory", "name": "LLaMA-Factory 社区训练镜像", "description": "平台默认训练镜像", "image_type": "Community", "status": "Available"},
            {"image_id": "offline", "name": "Diffusers 社区镜像", "description": "diffusers", "image_type": "Community", "status": "Offline"},
        ]
        with patch.dict(os.environ, {"XUNTUI_REMOTE_DIFFUSERS_INSTALL": "0"}, clear=False):
            allowed, selected = image_generation_api_module.select_image_generation_images(
                images, allow_install_fallback=True
            )
        self.assertEqual([item["image_id"] for item in allowed], ["llamafactory"])
        self.assertEqual(selected["image_id"], "llamafactory")
        self.assertTrue(selected["_requiresDiffusersInstall"])

    def test_image_selection_still_raises_on_community_catalog_without_install_fallback(self) -> None:
        images = [
            {"image_id": "llamafactory", "name": "LLaMA-Factory 社区训练镜像", "description": "平台默认训练镜像", "image_type": "Community", "status": "Available"},
        ]
        with patch.dict(os.environ, {"XUNTUI_REMOTE_DIFFUSERS_INSTALL": "0"}, clear=False):
            with self.assertRaises(ValueError):
                image_generation_api_module.select_image_generation_images(images)

    def test_image_selection_prefers_app_diffusers_over_community_torch(self) -> None:
        images = [
            {"image_id": "comm-torch", "name": "LLaMA-Factory 社区训练镜像", "description": "平台默认训练镜像", "image_type": "Community", "status": "Available"},
            {"image_id": "app-diff", "name": "Diffusers 0.35", "description": "torch + diffusers", "image_type": "App", "status": "Available"},
        ]
        with patch.dict(os.environ, {"XUNTUI_REMOTE_DIFFUSERS_INSTALL": "1"}, clear=False):
            allowed, selected = image_generation_api_module.select_image_generation_images(images)
        self.assertEqual([item["image_id"] for item in allowed], ["app-diff"])
        self.assertEqual(selected["image_id"], "app-diff")
        self.assertNotIn("_requiresDiffusersInstall", selected)

    def test_community_diffusers_image_is_not_treated_as_prebuilt(self) -> None:
        # Community diffusers images must stay behind the install-fallback /
        # probe gates; they never satisfy the prebuilt-diffusers requirement.
        images = [
            {"image_id": "comm-diff", "name": "SDXL Diffusers 社区镜像", "description": "sdxl + diffusers", "image_type": "Community", "status": "Available"},
        ]
        with patch.dict(os.environ, {"XUNTUI_REMOTE_DIFFUSERS_INSTALL": "0"}, clear=False):
            with self.assertRaises(ValueError):
                image_generation_api_module.select_image_generation_images(images)
        with patch.dict(os.environ, {"XUNTUI_REMOTE_DIFFUSERS_INSTALL": "1"}, clear=False):
            allowed, selected = image_generation_api_module.select_image_generation_images(images)
        self.assertEqual(selected["image_id"], "comm-diff")
        self.assertTrue(selected["_requiresDiffusersInstall"])
        self.assertTrue(selected["_requiresDiffusersInstall"])

    def test_catalog_groups_verified_base_and_completed_adapters(self) -> None:
        class FakeStore:
            def list_model_assets(self, **kwargs):
                return [
                    {"id": "base-1", "kind": "base", "name": "Flux", "repoId": "acme/flux", "status": "available", "path": "/models/flux"},
                    {"id": "trained-t1", "kind": "trained", "name": "Run", "repoId": "acme/flux", "status": "available", "baseModelId": "base-1", "taskId": "t1", "dataset": "ds", "variants": [{"key": "best", "status": "available"}, {"key": "final", "status": "available"}]},
                ]
        with patch.dict(os.environ, {"XUNTUI_IMAGE_GENERATION_VERIFIED_REPOS": "acme/flux"}, clear=False):
            catalog = build_image_generation_catalog(FakeStore(), provider="youyun")
        self.assertEqual([item["group"] for item in catalog], ["base", "trained", "trained"])
        self.assertEqual({item["adapterVariant"] for item in catalog if item["group"] == "trained"}, {"best", "final"})
        self.assertEqual(catalog[0]["supportedTrainingModes"], ["lora"])

    def test_catalog_defaults_to_verified_sdxl_base(self) -> None:
        class FakeStore:
            def list_model_assets(self, **kwargs):
                return [{"id": "base-1", "kind": "base", "name": "SDXL", "repoId": "stabilityai/stable-diffusion-xl-base-1.0", "status": "available", "path": "/models/sdxl"}]

        with patch.dict(os.environ, {}, clear=True):
            catalog = build_image_generation_catalog(FakeStore(), provider="youyun")
        self.assertEqual(len(catalog), 1)
        self.assertTrue(catalog[0]["readyForYouyun"])
        self.assertEqual(catalog[0]["modelType"], "SDXL")
        self.assertEqual(catalog[0]["supportedTrainingModes"], ["lora"])

    def test_catalog_accepts_model_asset_repo_field(self) -> None:
        class FakeStore:
            def list_model_assets(self, **kwargs):
                return [{"id": "base-1", "kind": "base", "name": "SDXL", "repo": "stabilityai/stable-diffusion-xl-base-1.0", "status": "available", "path": "/models/sdxl"}]

        with patch.dict(os.environ, {}, clear=True):
            catalog = build_image_generation_catalog(FakeStore(), provider="youyun")
        self.assertEqual(catalog[0]["repoId"], "stabilityai/stable-diffusion-xl-base-1.0")
        self.assertEqual(catalog[0]["modelType"], "SDXL")
        self.assertTrue(catalog[0]["readyForYouyun"])

    def test_catalog_allows_verified_repo_download_for_youyun_but_not_local(self) -> None:
        class FakeStore:
            def list_model_assets(self, **kwargs):
                return [{"id": "base-1", "kind": "base", "name": "SDXL", "repo": "stabilityai/stable-diffusion-xl-base-1.0", "status": "unavailable", "path": "/models/missing"}]

        with patch.dict(os.environ, {}, clear=True):
            youyun_catalog = build_image_generation_catalog(FakeStore(), provider="youyun")
            local_catalog = build_image_generation_catalog(FakeStore(), provider="local")
        self.assertTrue(youyun_catalog[0]["readyForYouyun"])
        self.assertEqual(youyun_catalog[0]["supportedTrainingModes"], ["lora"])
        self.assertIn("QLoRA", youyun_catalog[0]["reason"])
        self.assertFalse(local_catalog[0]["readyForYouyun"])
        self.assertIn("本地目录", local_catalog[0]["reason"])

    def test_catalog_rejects_lineage_mismatch_and_unknown_repo(self) -> None:
        class FakeStore:
            def list_model_assets(self, **kwargs):
                return [
                    {"id": "base-1", "kind": "base", "name": "Flux", "repoId": "acme/flux", "status": "available", "path": "/models/flux"},
                    {"id": "bad", "kind": "trained", "name": "Bad", "repoId": "other/flux", "status": "available", "baseModelId": "missing", "taskId": "t1", "variants": [{"key": "best", "status": "available"}]},
                    {"id": "unknown", "kind": "base", "name": "Unknown", "repoId": "random/model", "status": "available", "path": "/models/random"},
                ]
        with patch.dict(os.environ, {"XUNTUI_IMAGE_GENERATION_VERIFIED_REPOS": "acme/flux"}, clear=False):
            catalog = build_image_generation_catalog(FakeStore(), provider="youyun")
        self.assertNotIn("bad", {item["id"] for item in catalog})
        unknown = next(item for item in catalog if item["id"] == "unknown")
        self.assertFalse(unknown["readyForYouyun"])
        self.assertEqual(unknown["supportedTrainingModes"], [])

    def test_catalog_disables_qlora_without_compatibility_probe(self) -> None:
        class FakeStore:
            def list_model_assets(self, **kwargs):
                return [{"id": "base-1", "kind": "base", "name": "Flux", "repoId": "acme/flux", "status": "available", "path": "/models/flux"}]
        with patch.dict(os.environ, {"XUNTUI_IMAGE_GENERATION_VERIFIED_REPOS": "acme/flux"}, clear=False):
            with patch.dict(os.environ, {"XUNTUI_IMAGE_QLORA_COMPATIBLE": "0"}, clear=False):
                item = build_image_generation_catalog(FakeStore(), provider="youyun")[0]
        self.assertEqual(item["supportedTrainingModes"], ["lora"])
        self.assertIn("QLoRA", item["reason"])
    def test_accepts_png_jpeg_and_webp(self) -> None:
        for name, fmt, mime in (("a.png", "PNG", "image/png"), ("b.jpg", "JPEG", "image/jpeg"), ("c.webp", "WEBP", "image/webp")):
            result = validate_image_upload(name, image_bytes(fmt))
            self.assertEqual(result.mime_type, mime)
            self.assertEqual((result.width, result.height), (3, 2))

    def test_rejects_mismatch_invalid_unsafe_and_oversize(self) -> None:
        with self.assertRaises(ValueError):
            validate_image_upload("a.jpg", image_bytes("PNG"))
        with self.assertRaises(ValueError):
            validate_image_upload("../a.png", image_bytes("PNG"))
        with self.assertRaises(ValueError):
            validate_image_upload("a.png", b"not-an-image")
        with self.assertRaises(ValueError):
            validate_image_upload("a.png", b"x" * (MAX_IMAGE_UPLOAD_BYTES + 1))

    def test_normalizes_captions_and_deterministic_order(self) -> None:
        records = [
            validate_image_upload("z.png", image_bytes("PNG")),
            validate_image_upload("a.png", image_bytes("PNG")),
        ]
        normalized, summary = normalize_image_dataset(records, {"a.png": "A caption"})
        self.assertEqual([item["file_name"] for item in normalized], ["images/a.png", "images/z.png"])
        self.assertEqual(normalized[1]["caption"], "z")
        self.assertEqual(summary["checkedRows"], 2)
        with self.assertRaises(ValueError):
            normalize_image_dataset([records[0]], {"z.png": "   "})
        with self.assertRaises(ValueError):
            normalize_image_dataset([records[0], records[0]], {})
        with self.assertRaises(ValueError):
            normalize_image_dataset([records[0]] * (MAX_IMAGE_DATASET_FILES + 1), {})

    async def _request(self, payload: dict) -> Request:
        body = json.dumps(payload).encode()
        async def receive() -> dict:
            return {"type": "http.request", "body": body, "more_body": False}
        return Request({"type": "http", "method": "POST", "path": "/api/datasets/upload", "headers": [], "query_string": b"", "receive": receive})

    def test_api_uploads_image_batch_as_ready_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(app_module, "DATASET_ROOT", root), patch.object(app_module.store, "create_dataset", side_effect=lambda x: x):
                payload = {"files": [{"file_name": "one.png", "content_base64": base64.b64encode(image_bytes("PNG")).decode()}, {"file_name": "two.webp", "content_base64": base64.b64encode(image_bytes("WEBP")).decode(), "caption": "second"}]}
                async def receive() -> dict:
                    body = json.dumps(payload).encode()
                    return {"type": "http.request", "body": body, "more_body": False}
                request = Request({"type": "http", "method": "POST", "path": "/api/datasets/upload", "headers": [], "query_string": b""}, receive)
                import asyncio
                result = asyncio.run(app_module.upload_dataset(request, "demo", "IMAGE_TEXT", "", "文生图"))
                self.assertEqual(result["state"], "ready")
                self.assertEqual(result["sampleCount"], 2)
                self.assertTrue((root / result["id"] / "metadata.jsonl").is_file())
                self.assertEqual(result["paths"]["trainPath"], result["paths"]["manifestPath"])

    def test_api_deletes_image_dataset_without_train_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset_dir = root / "ds-image"
            images_dir = dataset_dir / "images"
            images_dir.mkdir(parents=True)
            manifest_path = dataset_dir / "metadata.jsonl"
            manifest_path.write_text('{"file_name":"images/one.png"}\n', encoding="utf-8")
            dataset = {
                "id": "ds-image",
                "paths": {
                    "mediaRoot": str(images_dir),
                    "manifestPath": str(manifest_path),
                },
            }
            with patch.object(app_module, "DATASET_ROOT", root), patch.object(
                app_module.store,
                "delete_dataset",
                return_value=dataset,
            ):
                response = asyncio.run(app_module.delete_dataset("ds-image"))
            self.assertEqual(response.status_code, 204)
            self.assertFalse(dataset_dir.exists())


class FakeImageGenerationRuntime:
    def __init__(self, root: Path) -> None:
        self.task_root = root
        self.tasks: dict[str, dict] = {}
        self.confirmed: list[str] = []

    def create_task(self, task):
        self.tasks[task["id"]] = dict(task)
        return self.tasks[task["id"]]

    def list_tasks(self, *, service_id=None, state=None, query=None, include_archived=False):
        query_value = str(query or "").casefold()
        return [
            task
            for task in self.tasks.values()
            if (include_archived or not task.get("archived"))
            and (not service_id or task.get("serviceId") == service_id)
            and (not state or task.get("state") == state)
            and (not query_value or query_value in str(task.get("name") or "").casefold() or query_value in str(task.get("prompt") or "").casefold())
        ]

    def get(self, task_id):
        return self.tasks.get(task_id)

    def update(self, task_id, **changes):
        task = self.tasks.get(task_id)
        if task:
            task.update(changes)
        return task

    async def confirm(self, task_id):
        self.confirmed.append(task_id)
        task = self.tasks.get(task_id)
        if task:
            task.update(state="queued", status="queued")
        return task

    async def request_stop(self, task_id):
        task = self.tasks.get(task_id)
        if task:
            task.update(state="stopped", status="stopped")
        return task

    async def retry(self, task_id):
        task = self.tasks.get(task_id)
        if task:
            task.update(state="queued", status="queued")
        return task

    def clone_task(self, task_id, overrides=None):
        source = self.tasks.get(task_id)
        if source is None:
            return None
        clone = dict(source)
        clone.update(dict(overrides or {}))
        clone.update({"id": f"clone-{len(self.tasks) + 1}", "name": f"{source.get('name', '任务')} · 副本", "state": "awaiting_confirmation", "status": "draft", "results": [], "logs": [], "archived": False, "archivedAt": None})
        for key in ("failureCode", "failureMessage", "suggestedAction", "cleanupState", "cleanupError", "remoteHandle", "phase", "phaseUpdatedAt", "lastHeartbeatAt", "cancelRequested"):
            clone.pop(key, None)
        self.tasks[clone["id"]] = clone
        return clone

    def archive_task(self, task_id):
        task = self.tasks.get(task_id)
        if task is None:
            return None
        if task.get("state") not in {"completed", "failed", "stopped"}:
            raise ValueError("只有已完成、失败或已停止的文生图任务可以归档。")
        task.update({"archived": True, "archivedAt": "2026-08-29T12:00:00+0800"})
        return task

    def restore_task(self, task_id):
        task = self.tasks.get(task_id)
        if task is None:
            return None
        task.update({"archived": False, "archivedAt": None})
        return task

    def delete_task(self, task_id):
        task = self.tasks.get(task_id)
        if task is None:
            return False
        if task.get("state") in {"queued", "preparing", "running", "collecting", "stopping"}:
            raise ValueError("运行中的文生图任务不能删除。")
        del self.tasks[task_id]
        return True


class FakeImageServiceStore:
    def __init__(self) -> None:
        self.service_logs: dict[str, list[str]] = {}
        self.services = {
            "igs-ready": {
                "id": "igs-ready",
                "name": "Flux service",
                "state": "created",
                "config": {
                    "workloadType": "image_generation",
                    "provider": "youyun",
                    "modelId": "base-1",
                    "modelRepoId": "acme/flux",
                    "trainingMode": "lora",
                    "width": 512,
                    "height": 512,
                    "steps": 20,
                    "cfg": 7,
                    "batchSize": 1,
                    "seed": -1,
                    "sampler": "euler",
                    "resource": {"region": "cn-wlcb", "zone": "cn-wlcb-01", "gpuType": "A10", "gpuCount": 1, "cpuCores": 8, "memoryGb": 32, "imageId": "diffusers"},
                },
            }
        }

    def get(self, service_id):
        return self.services.get(service_id)

    def list(self, *, include_archived=False):
        return list(self.services.values())

    def update(self, service_id, *, config=None, **changes):
        service = self.services.get(service_id)
        if service is None:
            return None
        if config is not None:
            service["config"] = dict(config)
        service.update(changes)
        return service

    def append_log(self, service_id, message):
        self.service_logs.setdefault(service_id, []).append(message)

    def logs(self, service_id, limit=200):
        return list(reversed(self.service_logs.get(service_id, [])[-limit:]))


class FakeImageGenerationServiceRuntime:
    def __init__(self, services):
        self.services = services
        self.started = []
        self.stopped = []

    async def start(self, service_id, *, preflight=None):
        self.started.append((service_id, preflight))
        service = self.services.get(service_id)
        service["state"] = "running"
        service["config"]["remoteRuntime"] = {"instanceId": "uhost-service-1"}
        return service

    async def stop(self, service_id):
        self.stopped.append(service_id)
        service = self.services.get(service_id)
        service["state"] = "stopped"
        service["config"].pop("remoteRuntime", None)
        return service


class ImageGenerationTaskApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)

        class FakeCatalogStore:
            def list_model_assets(self, **kwargs):
                return [{"id": "base-1", "kind": "base", "name": "Flux", "repoId": "acme/flux", "status": "available", "path": "/models/flux"}]

        self.runtime = FakeImageGenerationRuntime(root / "tasks")
        self.services = FakeImageServiceStore()
        self.service_runtime = FakeImageGenerationServiceRuntime(self.services.services)
        self.preflight_calls: list[dict] = []

        async def resolve_resource(resource: dict) -> dict:
            self.preflight_calls.append(dict(resource))
            return {
                **resource,
                "region": "cn-wlcb",
                "zone": "cn-wlcb-01",
                "chargeType": "Postpay",
                "price": {"currency": "CNY", "instance": 1.5, "disks": 0.1, "systemDisks": 0.1, "image": 0.0, "total": 1.7},
            }

        async def check_image(image_id: str) -> dict:
            if image_id != "diffusers":
                raise ValueError("所选镜像不是已验证的 Diffusers 镜像。")
            return {"imageId": image_id, "name": "Diffusers 0.35", "status": "ready"}

        app = FastAPI()
        app.include_router(create_image_generation_router(FakeCatalogStore(), self.services, task_runtime=self.runtime, result_root=root / "results", youyun_resource_resolver=resolve_resource, youyun_image_checker=check_image))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://image.test")
        self.env = patch.dict(os.environ, {"XUNTUI_IMAGE_GENERATION_VERIFIED_REPOS": "acme/flux"}, clear=False)
        self.env.start()

    async def asyncTearDown(self) -> None:
        self.env.stop()
        await self.client.aclose()
        self.temp.cleanup()

    async def test_service_results_include_task_logs_and_failure_diagnostics(self) -> None:
        created = await self.client.post("/api/image-generation/tasks", json={"serviceId": "igs-ready", "prompt": "logged cat"})
        self.assertEqual(created.status_code, 201, created.text)
        task_id = created.json()["id"]
        self.runtime.tasks[task_id].update({
            "state": "failed",
            "failureCode": "model_unavailable",
            "failureMessage": "优云基座模型不可用。",
            "suggestedAction": "检查模型后重试。",
            "logs": ["SSH 连接已建立。", "模型加载失败。"],
        })
        response = await self.client.get("/api/image-generation/services/igs-ready/results")
        self.assertEqual(response.status_code, 200, response.text)
        item = next(value for value in response.json()["items"] if value["taskId"] == task_id)
        self.assertEqual(item["logs"], ["SSH 连接已建立。", "模型加载失败。"])

    async def test_service_lifecycle_blocks_generation_until_started(self) -> None:
        app = FastAPI()
        async def resolve_resource(resource):
            return {**resource, "price": {"total": 1.0}}
        async def check_image(image_id):
            return {"imageId": image_id, "name": "Diffusers", "status": "ready"}
        app.include_router(create_image_generation_router(
            type("Catalog", (), {"list_model_assets": lambda self, **kwargs: [{"id": "base-1", "kind": "base", "name": "Flux", "repoId": "acme/flux", "status": "available", "path": "/models/flux"}]})(),
            self.services,
            task_runtime=self.runtime,
            service_runtime=self.service_runtime,
            result_root=Path(self.temp.name),
            youyun_resource_resolver=resolve_resource,
            youyun_image_checker=check_image,
        ))
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://image.test")
        try:
            draft = await client.post("/api/image-generation/tasks", json={"serviceId": "igs-ready", "prompt": "cat"})
            self.assertEqual(draft.status_code, 409, draft.text)
            started = await client.post("/api/image-generation/services/igs-ready/start")
            self.assertEqual(started.status_code, 200, started.text)
            task = await client.post("/api/image-generation/tasks", json={"serviceId": "igs-ready", "prompt": "cat"})
            self.assertEqual(task.status_code, 201, task.text)
            self.assertEqual(task.json()["state"], "queued")
            self.assertEqual(self.service_runtime.started[0][0], "igs-ready")
        finally:
            await client.aclose()

    async def test_service_logs_endpoint_returns_store_logs(self) -> None:
        self.services.append_log("igs-ready", "开始启动文生图推理服务。")
        missing = await self.client.get("/api/image-generation/services/igs-missing/logs")
        self.assertEqual(missing.status_code, 404, missing.text)
        response = await self.client.get("/api/image-generation/services/igs-ready/logs")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["serviceId"], "igs-ready")
        self.assertIn("开始启动文生图推理服务。", payload["logs"])

    async def test_create_is_awaiting_confirmation_and_confirm_is_separate(self) -> None:
        response = await self.client.post("/api/image-generation/tasks", json={"serviceId": "igs-ready", "prompt": "a product photo"})
        self.assertEqual(response.status_code, 201, response.text)
        task = response.json()
        self.assertEqual(task["state"], "awaiting_confirmation")
        self.assertEqual(self.runtime.confirmed, [])
        self.assertEqual(task["modelRepoId"], "acme/flux")
        confirmed = await self.client.post(f"/api/image-generation/tasks/{task['id']}/confirm")
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(confirmed.json()["state"], "queued")
        self.assertEqual(self.runtime.confirmed, [task["id"]])

    async def test_task_inherits_runtime_options_from_service(self) -> None:
        self.services.get("igs-ready")["config"].update({
            "negativePrompt": "blurry",
            "precision": "fp16",
            "enableCpuOffload": True,
            "enableAttentionSlicing": True,
            "enableVaeTiling": True,
            "enableSafetyChecker": True,
            "loadTimeout": 240,
        })
        response = await self.client.post("/api/image-generation/tasks", json={"serviceId": "igs-ready", "prompt": "a product photo"})
        self.assertEqual(response.status_code, 201, response.text)
        task = response.json()
        self.assertEqual(task["negativePrompt"], "blurry")
        self.assertEqual(task["precision"], "fp16")
        self.assertTrue(task["enableCpuOffload"])
        self.assertTrue(task["enableAttentionSlicing"])
        self.assertTrue(task["enableVaeTiling"])
        self.assertTrue(task["enableSafetyChecker"])
        self.assertEqual(task["loadTimeout"], 240)

    async def test_preflight_returns_checks_without_creating_instance(self) -> None:
        response = await self.client.post("/api/image-generation/services/igs-ready/preflight")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["ready"])
        self.assertEqual(body["checks"]["model"]["status"], "ready")
        self.assertEqual(body["checks"]["adapter"]["status"], "not_applicable")
        self.assertEqual(body["checks"]["image"]["status"], "ready")
        self.assertEqual(body["checks"]["capacity"]["status"], "ready")
        self.assertEqual(body["checks"]["price"]["status"], "ready")
        self.assertTrue(body["checkedAt"])
        self.assertEqual(len(self.preflight_calls), 1)

    async def test_gpu_specs_uses_selected_diffusers_image_and_gpu_for_capacity(self) -> None:
        capacity_calls: list[tuple[str, str]] = []

        class FakeYouyunClient:
            def __init__(self) -> None:
                self.config = SimpleNamespace(
                    region="cn-wlcb",
                    zone="cn-wlcb-01",
                    charge_type="Postpay",
                    gpu_type="4090",
                    image_id="plain",
                )

            def describe_instance_types(self):
                return [SimpleNamespace(name="4090", zone="cn-wlcb-01", sold_out=False, description="RTX 4090", graphics_memory_gb=24)]

            def describe_images(self):
                return [
                    {"image_id": "plain", "name": "cuda128_torch280_py312", "description": "PyTorch", "image_type": "App", "status": "Available", "tags": []},
                    {"image_id": "diffusers", "name": "Diffusers 0.35", "description": "SDXL Diffusers", "image_type": "App", "status": "Available", "tags": ["diffusers"]},
                ]

            def describe_community_images(self, **kwargs):
                return [
                    {"image_id": "community-video", "name": "Minimax video", "status": "Available", "supported_gpu_types": ["L40S"]},
                    {"image_id": "community-sdxl", "name": "SDXL Diffusers", "status": "Available", "tags": ["Diffusers", "LoRA"], "supported_gpu_types": ["L40S"]},
                ]

            def check_resource_capacity(self, *, gpu_type: str, image_id: str):
                capacity_calls.append((gpu_type, image_id))
                return SimpleNamespace(specs=[SimpleNamespace(gpu=1, cpu=16, memory_gb=64, resource_enough=True)])

        with patch("backend.providers.youyun.client.YouyunClient", FakeYouyunClient):
            response = await self.client.get(
                "/api/image-generation/youyun/gpu-specs",
                params={"gpuType": "L40S", "imageId": "diffusers"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["gpuType"], "L40S")
        self.assertEqual(body["imageId"], "diffusers")
        self.assertEqual(body["recommendedImageId"], "diffusers")
        self.assertFalse(body["requiresDiffusersInstall"])
        self.assertEqual([item["id"] for item in body["images"]], ["diffusers"])
        self.assertTrue(body["communityImages"][0]["compatible"])
        self.assertEqual(body["communityImages"][0]["id"], "community-sdxl")
        self.assertFalse(body["communityImages"][1]["compatible"])
        self.assertEqual(capacity_calls, [("L40S", "diffusers")])

    async def test_gpu_specs_reuses_shared_youyun_caches(self) -> None:
        """文生图资源发现必须复用 NLP 域的进程级共享缓存（容量 singleflight + 目录缓存）。"""
        from backend import app as app_module

        calls = {"capacity": 0, "zones": 0, "images": 0, "instance_types": 0}
        real_capacity = app_module._cached_youyun_capacity
        real_zones = app_module._cached_youyun_support_zones
        real_zone_images = app_module._cached_youyun_zone_images
        real_instance_types = app_module._cached_youyun_instance_types_for_zone

        async def spy_capacity(client, **kwargs):
            calls["capacity"] += 1
            return await real_capacity(client, **kwargs)

        async def spy_zones(client):
            calls["zones"] += 1
            return await real_zones(client)

        async def spy_zone_images(client, region, zone):
            calls["images"] += 1
            return await real_zone_images(client, region, zone)

        async def spy_instance_types(client, region, zone):
            calls["instance_types"] += 1
            return await real_instance_types(client, region, zone)

        class FakeYouyunClient:
            def __init__(self) -> None:
                self.config = SimpleNamespace(
                    region="cn-wlcb", zone="cn-wlcb-01", charge_type="Postpay",
                    gpu_type="4090", image_id="diffusers",
                )

            def describe_support_zones(self, region=None):
                return [{"region": "cn-wlcb", "zone": "cn-wlcb-01", "is_pod": True}]

            def describe_instance_types(self, **kwargs):
                return [SimpleNamespace(name="4090", zone="cn-wlcb-01", sold_out=False, description="RTX 4090", graphics_memory_gb=24)]

            def describe_images(self, **kwargs):
                return [{"image_id": "diffusers", "name": "Diffusers SDXL", "description": "SDXL", "image_type": "App", "status": "Available", "tags": ["diffusers"]}]

            def describe_community_images(self, **kwargs):
                return []

            def check_resource_capacity(self, *, gpu_type: str, image_id: str):
                return SimpleNamespace(specs=[SimpleNamespace(gpu=1, cpu=16, memory_gb=64, resource_enough=True)])

        with patch("backend.providers.youyun.client.YouyunClient", FakeYouyunClient), \
             patch.object(app_module, "_cached_youyun_capacity", spy_capacity), \
             patch.object(app_module, "_cached_youyun_support_zones", spy_zones), \
             patch.object(app_module, "_cached_youyun_zone_images", spy_zone_images), \
             patch.object(app_module, "_cached_youyun_instance_types_for_zone", spy_instance_types):
            response = await self.client.get(
                "/api/image-generation/youyun/gpu-specs",
                params={"purpose": "training", "instanceMode": "auto"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertGreaterEqual(calls["capacity"], 1, "必须走共享容量缓存（singleflight）")
        self.assertGreaterEqual(calls["zones"], 1, "可用区目录必须走共享缓存")
        self.assertGreaterEqual(calls["images"], 1, "镜像目录必须走共享缓存")
        self.assertGreaterEqual(calls["instance_types"], 1, "机型目录必须走共享缓存")

    async def test_gpu_specs_auto_mode_skips_cooled_pod_and_returns_uhost(self) -> None:
        class FakeYouyunClient:
            def __init__(self) -> None:
                self.config = SimpleNamespace(
                    region="cn-wlcb", zone="cn-wlcb-01", charge_type="Postpay",
                    gpu_type="4090", image_id="diffusers",
                )

            def describe_support_zones(self, region=None):
                return [
                    {"region": "cn-wlcb", "zone": "cn-wlcb-01", "is_pod": True},
                    {"region": "cn-wlcb", "zone": "cn-wlcb-02", "is_pod": False},
                ]

            def describe_instance_types(self, **kwargs):
                return [SimpleNamespace(name="4090", zone=kwargs.get("zone", "cn-wlcb-02"), sold_out=False, description="RTX 4090", graphics_memory_gb=24)]

            def describe_images(self, **kwargs):
                return [{"image_id": "diffusers", "name": "Diffusers SDXL", "description": "SDXL", "image_type": "App", "status": "Available", "tags": ["diffusers"]}]

            def describe_community_images(self, **kwargs):
                return []

            def check_resource_capacity(self, **kwargs):
                return SimpleNamespace(specs=[SimpleNamespace(gpu=1, cpu=16, memory_gb=64, resource_enough=True)])

        with patch("backend.providers.youyun.client.YouyunClient", FakeYouyunClient), patch.object(
            image_generation_api_module.youyun_resource_health,
            "metadata",
            side_effect=lambda candidate: {"healthStatus": "cooldown"} if candidate.get("zone") == "cn-wlcb-01" else {"healthStatus": "unknown"},
        ):
            response = await self.client.get(
                "/api/image-generation/youyun/gpu-specs",
                params={"purpose": "inference", "instanceMode": "auto"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["instanceMode"], "uhost")
        self.assertEqual(body["zone"], "cn-wlcb-02")
        self.assertTrue(body["fallback"])
        self.assertEqual(body["zones"][0]["healthStatus"], "cooldown")

    async def test_gpu_specs_auto_mode_returns_all_supported_regions(self) -> None:
        class FakeYouyunClient:
            def __init__(self) -> None:
                self.config = SimpleNamespace(
                    region="cn-wlcb", zone="cn-wlcb-01", charge_type="Postpay",
                    gpu_type="4090", image_id="diffusers",
                )
                self.zones_region = "unset"

            def describe_support_zones(self, region=None):
                self.zones_region = region
                return [
                    {"region": "cn-bj2", "zone": "cn-bj2-01", "is_pod": True},
                    {"region": "cn-sh2", "zone": "cn-sh2-02", "is_pod": True},
                    {"region": "cn-wlcb", "zone": "cn-wlcb-02", "is_pod": False},
                ]

            def describe_instance_types(self, **kwargs):
                return [SimpleNamespace(name="4090", zone=kwargs.get("zone"), sold_out=False, description="RTX 4090", graphics_memory_gb=24)]

            def describe_images(self, **kwargs):
                return [{"image_id": "diffusers", "name": "Diffusers SDXL", "description": "SDXL", "image_type": "App", "status": "Available", "tags": ["diffusers"]}]

            def describe_community_images(self, **kwargs):
                return []

            def check_resource_capacity(self, **kwargs):
                return SimpleNamespace(specs=[SimpleNamespace(gpu=1, cpu=16, memory_gb=64, resource_enough=True)])

        client_instance = FakeYouyunClient()
        with patch("backend.providers.youyun.client.YouyunClient", lambda: client_instance):
            response = await self.client.get(
                "/api/image-generation/youyun/gpu-specs",
                params={"purpose": "inference", "instanceMode": "auto"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIsNone(client_instance.zones_region)
        self.assertEqual({item["region"] for item in body["zones"]}, {"cn-bj2", "cn-sh2", "cn-wlcb"})

    async def test_gpu_specs_training_uses_torch_image_when_diffusers_is_unavailable(self) -> None:
        class FakeYouyunClient:
            def __init__(self) -> None:
                self.config = SimpleNamespace(
                    region="cn-wlcb",
                    zone="cn-wlcb-01",
                    charge_type="Postpay",
                    gpu_type="4090",
                    image_id="plain",
                )

            def describe_instance_types(self):
                return [SimpleNamespace(name="4090", zone="cn-wlcb-01", sold_out=False, description="RTX 4090", graphics_memory_gb=24)]

            def describe_images(self):
                return [
                    {"image_id": "plain", "name": "cuda132_torch2130_py312", "description": "CUDA + PyTorch", "image_type": "App", "status": "Available", "tags": []},
                ]

            def describe_community_images(self, **kwargs):
                return []

            def check_resource_capacity(self, *, gpu_type: str, image_id: str):
                return SimpleNamespace(specs=[SimpleNamespace(gpu=1, cpu=16, memory_gb=64, resource_enough=True)])

        with patch("backend.providers.youyun.client.YouyunClient", FakeYouyunClient):
            response = await self.client.get(
                "/api/image-generation/youyun/gpu-specs",
                params={"purpose": "training"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["imageId"], "plain")
        self.assertTrue(body["requiresDiffusersInstall"])
        self.assertEqual(body["recommendedImageName"], "cuda132_torch2130_py312")

    async def test_gpu_specs_inference_keeps_resource_catalog_when_diffusers_is_unavailable(self) -> None:
        class FakeYouyunClient:
            def __init__(self) -> None:
                self.config = SimpleNamespace(
                    region="cn-wlcb",
                    zone="cn-wlcb-01",
                    charge_type="Postpay",
                    gpu_type="4090",
                    image_id="plain",
                )

            def describe_instance_types(self):
                return [SimpleNamespace(name="4090", zone="cn-wlcb-01", sold_out=False, description="RTX 4090", graphics_memory_gb=24)]

            def describe_images(self):
                return [
                    {"image_id": "plain", "name": "cuda132_torch2130_py312", "description": "CUDA + PyTorch", "image_type": "App", "status": "Available", "tags": []},
                ]

            def describe_community_images(self, **kwargs):
                return []

            def check_resource_capacity(self, *, gpu_type: str, image_id: str):
                return SimpleNamespace(specs=[SimpleNamespace(gpu=1, cpu=16, memory_gb=64, resource_enough=True)])

        with patch("backend.providers.youyun.client.YouyunClient", FakeYouyunClient), patch.dict(
            os.environ, {"XUNTUI_REMOTE_DIFFUSERS_INSTALL": "0"}, clear=False
        ):
            response = await self.client.get(
                "/api/image-generation/youyun/gpu-specs",
                params={"purpose": "inference"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["imageId"], "plain")
        self.assertTrue(body["requiresDiffusersInstall"])
        self.assertEqual(body["images"][0]["id"], "plain")

    async def test_gpu_specs_training_uses_verified_community_image_without_platform_diffusers_image(self) -> None:
        capacity_calls: list[tuple[str, str]] = []

        class FakeYouyunClient:
            def __init__(self) -> None:
                self.config = SimpleNamespace(
                    region="cn-wlcb",
                    zone="cn-wlcb-01",
                    charge_type="Postpay",
                    gpu_type="4090",
                    image_id="plain",
                )

            def describe_instance_types(self):
                return [SimpleNamespace(name="4090", zone="cn-wlcb-01", sold_out=False, description="RTX 4090", graphics_memory_gb=24)]

            def describe_images(self):
                return []

            def describe_community_images(self, **kwargs):
                return [
                    {"image_id": "community-sdxl", "name": "SDXL Diffusers LoRA", "status": "Available", "tags": ["SDXL", "Diffusers", "LoRA"], "supported_gpu_types": ["4090"]},
                ]

            def check_resource_capacity(self, *, gpu_type: str, image_id: str):
                capacity_calls.append((gpu_type, image_id))
                return SimpleNamespace(specs=[SimpleNamespace(gpu=1, cpu=16, memory_gb=64, resource_enough=True)])

        app = FastAPI()
        app.include_router(create_image_generation_router(
            SimpleNamespace(list_model_assets=lambda **kwargs: []),
            self.services,
            task_runtime=self.runtime,
            community_image_verification_lookup=lambda purpose, image_id, region, zone, gpu_type: {
                "state": "verified",
                "cleanupState": "released",
            } if (purpose, image_id, region, zone, gpu_type) == (
                "image_generation_training",
                "community-sdxl",
                "cn-wlcb",
                "cn-wlcb-01",
                "4090",
            ) else {"state": "unverified"},
        ))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://community.test") as client:
            with (
                patch("backend.providers.youyun.client.YouyunClient", FakeYouyunClient),
                patch.dict(os.environ, {"XUNTUI_REMOTE_DIFFUSERS_INSTALL": "0"}, clear=False),
            ):
                response = await client.get(
                    "/api/image-generation/youyun/gpu-specs",
                    params={"purpose": "training", "gpuType": "4090", "imageId": "community-sdxl"},
                )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["imageId"], "community-sdxl")
        self.assertFalse(body["requiresDiffusersInstall"])
        self.assertEqual(body["images"], [])
        self.assertTrue(body["communityImages"][0]["selectable"])
        self.assertEqual(capacity_calls, [("4090", "community-sdxl")])

    async def test_gpu_specs_uses_selected_pod_region_and_zone(self) -> None:
        calls: list[tuple[str, dict]] = []

        class FakeYouyunClient:
            def __init__(self, config=None) -> None:
                self.config = config or SimpleNamespace(
                    region="cn-wlcb",
                    zone="cn-wlcb-01",
                    charge_type="Postpay",
                    gpu_type="4090",
                    image_id="diffusers",
                )

            def describe_support_zones(self, region=None):
                calls.append(("zones", {"region": region}))
                return [
                    {"region": "cn-wlcb", "zone": "cn-wlcb-03", "describe": "Pod", "is_pod": True},
                    {"region": "cn-sh2", "zone": "cn-sh2-02", "describe": "Pod", "is_pod": True},
                ]

            def describe_instance_types(self, **kwargs):
                calls.append(("types", kwargs))
                return [SimpleNamespace(name="4090", zone="cn-sh2-02", sold_out=False, description="RTX 4090", graphics_memory_gb=24)]

            def describe_images(self, **kwargs):
                calls.append(("images", kwargs))
                return [
                    {"image_id": "diffusers", "name": "Diffusers 0.35", "description": "SDXL Diffusers", "image_type": "App", "status": "Available", "tags": ["diffusers"]},
                ]

            def describe_community_images(self, **kwargs):
                return []

            def check_resource_capacity(self, **kwargs):
                calls.append(("capacity", kwargs))
                return SimpleNamespace(specs=[SimpleNamespace(gpu=1, cpu=16, memory_gb=64, resource_enough=True)])

        with (
            patch("backend.providers.youyun.client.YouyunClient", FakeYouyunClient),
            patch("backend.providers.youyun.models.YouyunConfig.from_env", return_value=FakeYouyunClient().config),
        ):
            response = await self.client.get(
                "/api/image-generation/youyun/gpu-specs",
                params={
                    "gpuType": "4090",
                    "imageId": "diffusers",
                    "instanceMode": "pod",
                    "region": "cn-sh2",
                    "zone": "cn-sh2-02",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["instanceMode"], "pod")
        self.assertEqual(body["region"], "cn-sh2")
        self.assertEqual(body["zone"], "cn-sh2-02")
        self.assertEqual(body["zones"][1]["zone"], "cn-sh2-02")
        self.assertIn(("capacity", {"gpu_type": "4090", "image_id": "diffusers", "charge_type": "Postpay", "region": "cn-sh2", "zone": "cn-sh2-02"}), calls)

    async def test_gpu_specs_accepts_explicit_uhost_zone_and_returns_mode(self) -> None:
        class FakeYouyunClient:
            def __init__(self, config=None) -> None:
                self.config = config or SimpleNamespace(region="cn-wlcb", zone="cn-wlcb-01", charge_type="Postpay", gpu_type="4090", image_id="diffusers")

            def describe_support_zones(self, region=None):
                return [
                    {"region": "cn-wlcb", "zone": "cn-wlcb-01", "describe": "Pod", "is_pod": True},
                    {"region": "cn-wlcb", "zone": "cn-wlcb-02", "describe": "UHost", "is_pod": False},
                ]

            def describe_instance_types(self, **kwargs):
                return [SimpleNamespace(name="4090", zone="cn-wlcb-02", sold_out=False, description="RTX 4090", graphics_memory_gb=24)]

            def describe_images(self, **kwargs):
                return [{"image_id": "diffusers", "name": "Diffusers", "description": "SDXL Diffusers", "image_type": "App", "status": "Available", "tags": ["diffusers"]}]

            def describe_community_images(self, **kwargs):
                return []

            def check_resource_capacity(self, **kwargs):
                return SimpleNamespace(specs=[SimpleNamespace(gpu=1, cpu=16, memory_gb=64, resource_enough=True)])

        with patch("backend.providers.youyun.client.YouyunClient", FakeYouyunClient):
            response = await self.client.get(
                "/api/image-generation/youyun/gpu-specs",
                params={"instanceMode": "uhost", "region": "cn-wlcb", "zone": "cn-wlcb-02"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["instanceMode"], "uhost")
        self.assertEqual(body["zone"], "cn-wlcb-02")

    async def test_gpu_specs_auto_accepts_selected_uhost_zone(self) -> None:
        class FakeYouyunClient:
            def __init__(self, config=None) -> None:
                self.config = config or SimpleNamespace(region="cn-wlcb", zone="cn-wlcb-01", charge_type="Postpay", gpu_type="4090", image_id="diffusers")

            def describe_support_zones(self, region=None):
                return [
                    {"region": "cn-wlcb", "zone": "cn-wlcb-01", "describe": "Pod", "is_pod": True},
                    {"region": "cn-wlcb", "zone": "cn-wlcb-02", "describe": "UHost", "is_pod": False},
                ]

            def describe_instance_types(self, **kwargs):
                return [SimpleNamespace(name="4090", zone=kwargs.get("zone"), sold_out=False, description="RTX 4090", graphics_memory_gb=24)]

            def describe_images(self, **kwargs):
                return [{"image_id": "diffusers", "name": "Diffusers", "description": "SDXL Diffusers", "image_type": "App", "status": "Available", "tags": ["diffusers"]}]

            def describe_community_images(self, **kwargs):
                return []

            def check_resource_capacity(self, **kwargs):
                return SimpleNamespace(specs=[SimpleNamespace(gpu=1, cpu=16, memory_gb=64, resource_enough=True)])

        with patch("backend.providers.youyun.client.YouyunClient", FakeYouyunClient):
            response = await self.client.get(
                "/api/image-generation/youyun/gpu-specs",
                params={"instanceMode": "auto", "region": "cn-wlcb", "zone": "cn-wlcb-02"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["instanceMode"], "uhost")

    async def test_gpu_specs_allows_explicit_uhost_with_dual_mode_zone(self) -> None:
        class FakeYouyunClient:
            def __init__(self):
                self.config = SimpleNamespace(region="cn-wlcb", zone="cn-wlcb-01", charge_type="Postpay", gpu_type="4090", image_id="diffusers")

            def describe_support_zones(self, region=None):
                return [{"region": "cn-wlcb", "zone": "cn-wlcb-01", "describe": "Pod", "is_pod": True}]

            def describe_instance_types(self, **kwargs):
                return []

            def describe_images(self, **kwargs):
                return [{"image_id": "diffusers", "name": "Diffusers", "description": "SDXL Diffusers", "image_type": "App", "status": "Available", "tags": ["diffusers"]}]

            def describe_community_images(self, **kwargs):
                return []

            def check_resource_capacity(self, **kwargs):
                return SimpleNamespace(specs=[])

        with patch("backend.providers.youyun.client.YouyunClient", FakeYouyunClient):
            response = await self.client.get(
                "/api/image-generation/youyun/gpu-specs",
                params={"instanceMode": "uhost", "region": "cn-wlcb", "zone": "cn-wlcb-01"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["instanceMode"], "uhost")
        self.assertEqual(response.json()["zone"], "cn-wlcb-01")

    async def test_gpu_specs_auto_falls_back_to_uhost_when_pod_has_no_capacity(self) -> None:
        calls: list[dict] = []

        class FakeYouyunClient:
            def __init__(self, config=None):
                self.config = config or SimpleNamespace(region="cn-wlcb", zone="cn-wlcb-03", charge_type="Postpay", gpu_type="4090", image_id="diffusers")

            def describe_support_zones(self, region=None):
                return [
                    {"region": "cn-wlcb", "zone": "cn-wlcb-03", "describe": "Pod", "is_pod": True},
                    {"region": "cn-wlcb", "zone": "cn-wlcb-02", "describe": "UHost", "is_pod": False},
                ]

            def describe_instance_types(self, **kwargs):
                return [SimpleNamespace(name="4090", zone=kwargs.get("zone"), sold_out=False, description="RTX 4090", graphics_memory_gb=24)]

            def describe_images(self, **kwargs):
                return [{"image_id": "diffusers", "name": "Diffusers", "description": "SDXL Diffusers", "image_type": "App", "status": "Available", "tags": ["diffusers"]}]

            def describe_community_images(self, **kwargs):
                return []

            def check_resource_capacity(self, **kwargs):
                calls.append(dict(kwargs))
                available = kwargs.get("zone") == "cn-wlcb-02"
                return SimpleNamespace(specs=[SimpleNamespace(gpu=1, cpu=16, memory_gb=64, resource_enough=available)])

        with patch("backend.providers.youyun.client.YouyunClient", FakeYouyunClient):
            response = await self.client.get(
                "/api/image-generation/youyun/gpu-specs",
                params={"instanceMode": "auto", "region": "cn-wlcb"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["instanceMode"], "uhost")
        self.assertEqual(body["zone"], "cn-wlcb-02")
        self.assertTrue(body["fallback"])
        self.assertIn("UHost", body["fallbackReason"])
        self.assertEqual([call.get("zone") or "cn-wlcb-03" for call in calls], ["cn-wlcb-03", "cn-wlcb-02"])

    async def test_preflight_persists_snapshot_and_confirmation_reuses_fresh_snapshot(self) -> None:
        response = await self.client.post("/api/image-generation/services/igs-ready/preflight")
        self.assertEqual(response.status_code, 200, response.text)
        snapshot = self.services.get("igs-ready")["config"].get("preflightSnapshot")
        self.assertIsInstance(snapshot, dict)
        self.assertTrue(snapshot["fingerprint"])
        self.assertGreater(snapshot["expiresAtEpoch"], snapshot["checkedAtEpoch"])
        created = await self.client.post("/api/image-generation/tasks", json={"serviceId": "igs-ready", "prompt": "cat"})
        confirmed = await self.client.post(f"/api/image-generation/tasks/{created.json()['id']}/confirm")
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(len(self.preflight_calls), 1)
        self.assertEqual(confirmed.json()["preflight"]["fingerprint"], snapshot["fingerprint"])

    async def test_expired_snapshot_is_rechecked_before_confirmation(self) -> None:
        response = await self.client.post("/api/image-generation/services/igs-ready/preflight")
        self.assertEqual(response.status_code, 200, response.text)
        config = self.services.get("igs-ready")["config"]
        config["preflightSnapshot"]["expiresAtEpoch"] = 0
        created = await self.client.post("/api/image-generation/tasks", json={"serviceId": "igs-ready", "prompt": "cat"})
        confirmed = await self.client.post(f"/api/image-generation/tasks/{created.json()['id']}/confirm")
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(len(self.preflight_calls), 2)

    async def test_changed_resource_invalidates_snapshot_before_confirmation(self) -> None:
        response = await self.client.post("/api/image-generation/services/igs-ready/preflight")
        self.assertEqual(response.status_code, 200, response.text)
        self.services.get("igs-ready")["config"]["resource"]["cpuCores"] = 16
        created = await self.client.post("/api/image-generation/tasks", json={"serviceId": "igs-ready", "prompt": "cat"})
        confirmed = await self.client.post(f"/api/image-generation/tasks/{created.json()['id']}/confirm")
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(len(self.preflight_calls), 2)

    async def test_confirmation_is_blocked_when_preflight_fails(self) -> None:
        async def blocked_image(image_id: str) -> dict:
            raise ValueError("Diffusers 镜像不可用。")

        class FakeCatalog:
            def list_model_assets(self, **kwargs):
                return [{"id": "base-1", "kind": "base", "name": "Flux", "repoId": "acme/flux", "status": "available", "path": "/models/flux"}]

        runtime = FakeImageGenerationRuntime(self.runtime.task_root)
        app = FastAPI()
        app.include_router(create_image_generation_router(FakeCatalog(), self.services, task_runtime=runtime, result_root=Path(self.temp.name), youyun_resource_resolver=lambda resource: {**resource, "price": {"currency": "CNY", "total": 1}}, youyun_image_checker=blocked_image))
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://image.test")
        try:
            created = await client.post("/api/image-generation/tasks", json={"serviceId": "igs-ready", "prompt": "cat"})
            self.assertEqual(created.status_code, 201, created.text)
            response = await client.post(f"/api/image-generation/tasks/{created.json()['id']}/confirm")
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(runtime.confirmed, [])
            self.assertEqual(runtime.get(created.json()["id"])["state"], "awaiting_confirmation")
        finally:
            await client.aclose()

    async def test_rejects_invalid_service_and_result_path_traversal(self) -> None:
        missing = await self.client.post("/api/image-generation/tasks", json={"serviceId": "missing", "prompt": "cat"})
        self.assertEqual(missing.status_code, 404)
        created = await self.client.post("/api/image-generation/tasks", json={"serviceId": "igs-ready", "prompt": "cat"})
        task = created.json()
        self.runtime.tasks[task["id"]]["results"] = [{"id": "bad", "fileName": "../secret.png", "path": str(Path(self.temp.name) / "secret.png")}]
        result = await self.client.get(f"/api/image-generation/tasks/{task['id']}/results/bad")
        self.assertEqual(result.status_code, 404)

    async def test_diagnostics_returns_safe_failure_and_telemetry_fields(self) -> None:
        created = await self.client.post("/api/image-generation/tasks", json={"serviceId": "igs-ready", "prompt": "cat"})
        task_id = created.json()["id"]
        self.runtime.tasks[task_id].update({
            "state": "failed",
            "phase": "collecting",
            "progress": 98,
            "lastHeartbeatAt": "2026-08-29T12:00:00+0800",
            "failureCode": "result_download_failed",
            "failureMessage": "生成结果下载失败。",
            "error": "raw remote command should not be returned",
            "cleanupState": "failed",
            "cleanupError": "raw cleanup detail should not be returned",
            "suggestedAction": "确认远端结果文件完整后重试任务。",
        })
        response = await self.client.get(f"/api/image-generation/tasks/{task_id}/diagnostics")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["taskId"], task_id)
        self.assertEqual(body["phase"], "collecting")
        self.assertEqual(body["failure"], {"code": "result_download_failed", "message": "生成结果下载失败。"})
        self.assertEqual(body["cleanup"], {"state": "failed", "error": "优云实例清理失败，请重试清理。"})
        self.assertEqual(body["suggestedAction"], "确认远端结果文件完整后重试任务。")
        self.assertNotIn("raw remote command", response.text)
        self.assertNotIn("raw cleanup detail", response.text)

    async def test_local_contract_reaches_result_without_real_youyun_manager(self) -> None:
        started: list[str] = []

        class FakeProvider:
            async def start(self, task_id: str, task: dict, **kwargs):
                started.append(task_id)
                result_root = Path(task["resultRoot"])
                result_root.mkdir(parents=True, exist_ok=True)
                image_path = result_root / "0.png"
                Image.new("RGB", (4, 4), (10, 20, 30)).save(image_path)
                kwargs["on_stage"]("collecting", "正在下载生成结果。", {"progress": 98})
                return {"status": "completed", "cleanupState": "released", "results": [{"id": "0", "fileName": "0.png", "width": 4, "height": 4, "path": str(image_path)}]}

        class FakeCatalog:
            def list_model_assets(self, **kwargs):
                return [{"id": "base-1", "kind": "base", "name": "Flux", "repoId": "acme/flux", "status": "available", "path": "/models/flux"}]

        runtime = ImageGenerationRuntime(task_root=self.runtime.task_root, execution_lock=asyncio.Lock(), provider_factory=lambda runtime: FakeProvider())
        app = FastAPI()
        app.include_router(create_image_generation_router(FakeCatalog(), self.services, task_runtime=runtime, result_root=Path(self.temp.name) / "contract-results"))
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://image.test")
        try:
            created = await client.post("/api/image-generation/tasks", json={"serviceId": "igs-ready", "prompt": "cat"})
            self.assertEqual(created.status_code, 201, created.text)
            task_id = created.json()["id"]
            confirmed = await client.post(f"/api/image-generation/tasks/{task_id}/confirm")
            self.assertEqual(confirmed.status_code, 200, confirmed.text)
            await runtime.start()
            await asyncio.wait_for(runtime._queue.join(), timeout=1)
            detail = await client.get(f"/api/image-generation/tasks/{task_id}")
            self.assertEqual(detail.json()["state"], "completed")
            result = await client.get(f"/api/image-generation/tasks/{task_id}/results/0")
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.headers["content-type"], "image/png")
            self.assertEqual(started, [task_id])
        finally:
            await runtime.stop()
            await client.aclose()

    async def test_task_history_filters_archived_records_and_clones_with_overrides(self) -> None:
        first = await self.client.post("/api/image-generation/tasks", json={"serviceId": "igs-ready", "prompt": "cat"})
        second = await self.client.post("/api/image-generation/tasks", json={"serviceId": "igs-ready", "prompt": "dog"})
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        first_id = first.json()["id"]
        second_id = second.json()["id"]
        self.runtime.tasks[first_id].update({"state": "completed", "status": "completed", "name": "Cat completed"})
        self.runtime.tasks[second_id].update({"state": "failed", "status": "failed", "name": "Dog failed"})
        archived = await self.client.post(f"/api/image-generation/tasks/{first_id}/archive")
        self.assertEqual(archived.status_code, 200, archived.text)

        default_list = await self.client.get("/api/image-generation/tasks")
        self.assertEqual([item["id"] for item in default_list.json()], [second_id])
        filtered = await self.client.get("/api/image-generation/tasks", params={"includeArchived": "true", "query": "cat"})
        self.assertEqual([item["id"] for item in filtered.json()], [first_id])

        cloned = await self.client.post(f"/api/image-generation/tasks/{first_id}/clone", json={"prompt": "edited cat", "steps": 40})
        self.assertEqual(cloned.status_code, 201, cloned.text)
        clone = cloned.json()
        self.assertEqual(clone["state"], "awaiting_confirmation")
        self.assertEqual(clone["prompt"], "edited cat")
        self.assertEqual(clone["steps"], 40)
        self.assertEqual(clone["results"], [])
        self.assertEqual(self.runtime.confirmed, [])

    async def test_archived_task_actions_are_blocked_and_restore_is_read_only(self) -> None:
        created = await self.client.post("/api/image-generation/tasks", json={"serviceId": "igs-ready", "prompt": "archive me"})
        task_id = created.json()["id"]
        self.runtime.tasks[task_id].update({"state": "completed", "status": "completed"})
        archived = await self.client.post(f"/api/image-generation/tasks/{task_id}/archive")
        self.assertEqual(archived.status_code, 200, archived.text)
        for action in ("confirm", "stop", "retry", "archive"):
            response = await self.client.post(f"/api/image-generation/tasks/{task_id}/{action}")
            self.assertEqual(response.status_code, 409, response.text)
        restored = await self.client.post(f"/api/image-generation/tasks/{task_id}/restore")
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertFalse(restored.json()["archived"])
        self.assertEqual(restored.json()["state"], "completed")

    async def test_task_delete_and_service_result_aggregation(self) -> None:
        first = await self.client.post("/api/image-generation/tasks", json={"serviceId": "igs-ready", "prompt": "result cat"})
        second = await self.client.post("/api/image-generation/tasks", json={"serviceId": "igs-ready", "prompt": "result dog"})
        first_id = first.json()["id"]
        second_id = second.json()["id"]
        result_root = Path(self.temp.name) / "results" / first_id
        result_root.mkdir(parents=True)
        image_path = result_root / "0.png"
        Image.new("RGB", (4, 4), (1, 2, 3)).save(image_path)
        self.runtime.tasks[first_id].update({"state": "completed", "status": "completed", "results": [{"id": "0", "fileName": "0.png", "width": 4, "height": 4, "path": str(image_path)}]})
        self.runtime.tasks[second_id].update({"state": "failed", "status": "failed"})

        aggregate = await self.client.get("/api/image-generation/services/igs-ready/results", params={"page": 1, "pageSize": 1})
        self.assertEqual(aggregate.status_code, 200, aggregate.text)
        body = aggregate.json()
        self.assertEqual(body["serviceId"], "igs-ready")
        self.assertEqual(body["total"], 2)
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["items"][0]["taskId"], first_id)
        self.assertEqual(body["items"][0]["results"][0]["id"], "0")
        self.assertNotIn("resultRoot", aggregate.text)
        self.assertNotIn("remoteHandle", aggregate.text)

        deleted = await self.client.delete(f"/api/image-generation/tasks/{second_id}")
        self.assertEqual(deleted.status_code, 204, deleted.text)
        active_id = (await self.client.post("/api/image-generation/tasks", json={"serviceId": "igs-ready", "prompt": "active"})).json()["id"]
        self.runtime.tasks[active_id]["state"] = "running"
        blocked = await self.client.delete(f"/api/image-generation/tasks/{active_id}")
        self.assertEqual(blocked.status_code, 409, blocked.text)

    async def test_clone_rechecks_effective_pixel_limit_when_one_dimension_is_overridden(self) -> None:
        created = await self.client.post("/api/image-generation/tasks", json={"serviceId": "igs-ready", "prompt": "large"})
        task_id = created.json()["id"]
        self.runtime.tasks[task_id].update({"width": 2048, "height": 2048})
        response = await self.client.post(f"/api/image-generation/tasks/{task_id}/clone", json={"width": 2048})
        self.assertEqual(response.status_code, 422, response.text)

    async def test_fake_history_acceptance_covers_archive_clone_aggregate_and_download(self) -> None:
        created = await self.client.post("/api/image-generation/tasks", json={"serviceId": "igs-ready", "prompt": "acceptance image"})
        self.assertEqual(created.status_code, 201, created.text)
        task_id = created.json()["id"]
        result_root = Path(self.temp.name) / "results" / task_id
        result_root.mkdir(parents=True)
        image_path = result_root / "0.png"
        Image.new("RGB", (5, 5), (5, 10, 15)).save(image_path)
        self.runtime.tasks[task_id].update({"state": "completed", "status": "completed", "results": [{"id": "0", "fileName": "0.png", "width": 5, "height": 5, "path": str(image_path)}]})

        archived = await self.client.post(f"/api/image-generation/tasks/{task_id}/archive")
        self.assertEqual(archived.status_code, 200, archived.text)
        cloned = await self.client.post(f"/api/image-generation/tasks/{task_id}/clone", json={"prompt": "acceptance variant"})
        self.assertEqual(cloned.status_code, 201, cloned.text)
        clone = cloned.json()
        self.assertEqual(clone["state"], "awaiting_confirmation")
        self.assertEqual(clone["prompt"], "acceptance variant")
        self.assertEqual(clone["results"], [])

        aggregate = await self.client.get("/api/image-generation/services/igs-ready/results", params={"includeArchived": "true"})
        self.assertEqual(aggregate.status_code, 200, aggregate.text)
        self.assertEqual({item["taskId"] for item in aggregate.json()["items"]}, {task_id, clone["id"]})
        result = await self.client.get(f"/api/image-generation/tasks/{task_id}/results/0")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers["content-type"], "image/png")
        self.assertEqual(self.preflight_calls, [])
