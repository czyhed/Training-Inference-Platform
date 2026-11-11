from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


TEST_RUNTIME = tempfile.TemporaryDirectory()
TEST_ROOT = Path(TEST_RUNTIME.name)
os.environ["XUNTUI_STORAGE_BACKEND"] = "sqlite"
os.environ["XUNTUI_DATABASE_PATH"] = str(TEST_ROOT / "training.db")
os.environ["XUNTUI_RUNTIME_ROOT"] = str(TEST_ROOT / "runtime")
os.environ["XUNTUI_DATASET_ROOT"] = str(TEST_ROOT / "datasets")
os.environ["XUNTUI_ARTIFACT_ROOT"] = str(TEST_ROOT / "artifacts")

from fastapi import HTTPException, Request

from backend import app as app_module


class OAuthRedirectTest(unittest.TestCase):
    def test_oauth_failure_redirect_puts_error_in_query_string(self) -> None:
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/auth/callback",
                "headers": [],
                "query_string": b"",
                "scheme": "http",
                "server": ("127.0.0.1", 8010),
                "client": ("127.0.0.1", 12345),
            }
        )
        with patch.object(app_module, "FRONTEND_URL", "http://127.0.0.1:3102"):
            response = app_module._oauth_failure_redirect(request, "OAuth state 校验失败")

        self.assertEqual(
            response.headers["location"],
            "http://127.0.0.1:3102/?auth_error=OAuth+state+%E6%A0%A1%E9%AA%8C%E5%A4%B1%E8%B4%A5",
        )


class YouyunWatchdogTest(unittest.TestCase):
    def test_orphan_reconciliation_tracks_all_unreleased_task_handles(self) -> None:
        tasks = [
            {"provider": "youyun", "remoteHandle": {"instanceId": "uhost-remote"}},
            {"provider": "youyun", "artifacts": [{"type": "youyun-instance", "instanceId": "uhost-running", "state": "running"}]},
            {"provider": "youyun", "artifacts": [{"type": "youyun-instance", "instanceId": "uhost-released", "state": "released"}]},
            {"provider": "local", "instanceId": "local-instance"},
        ]
        self.assertEqual(
            app_module._youyun_task_instance_ids(tasks),
            {"uhost-remote", "uhost-running"},
        )

    def test_expired_remote_task_requires_active_budget_and_pending_cleanup(self) -> None:
        self.assertTrue(app_module._expired_remote_task({"provider": "youyun", "state": "running", "cleanupState": "pending", "expiresAtEpoch": 10}, now=10))
        self.assertFalse(app_module._expired_remote_task({"provider": "youyun", "state": "completed", "cleanupState": "pending", "expiresAtEpoch": 10}, now=10))
        self.assertFalse(app_module._expired_remote_task({"provider": "local", "state": "running", "expiresAtEpoch": 1}, now=10))

    def test_stalled_remote_task_requires_active_state_and_old_heartbeat(self) -> None:
        self.assertTrue(
            app_module._stalled_remote_task(
                {
                    "provider": "youyun",
                    "state": "running",
                    "cleanupState": "pending",
                    "lastHeartbeatAtEpoch": 100,
                },
                now=500,
                timeout=300,
            )
        )
        self.assertFalse(
            app_module._stalled_remote_task(
                {
                    "provider": "youyun",
                    "state": "running",
                    "cleanupState": "pending",
                    "lastHeartbeatAtEpoch": 250,
                },
                now=500,
                timeout=300,
            )
        )

    def test_cleanup_retry_is_due_only_for_failed_remote_cleanup(self) -> None:
        self.assertTrue(
            app_module._cleanup_retry_due(
                {
                    "provider": "youyun",
                    "state": "failed",
                    "cleanupState": "failed",
                    "cleanupNextRetryAtEpoch": 100,
                    "remoteHandle": {"instanceId": "uhost-1"},
                },
                now=100,
            )
        )
        self.assertFalse(
            app_module._cleanup_retry_due(
                {
                    "provider": "youyun",
                    "state": "failed",
                    "cleanupState": "failed",
                    "cleanupNextRetryAtEpoch": 200,
                    "remoteHandle": {"instanceId": "uhost-1"},
                },
                now=100,
            )
        )
        self.assertFalse(
            app_module._cleanup_retry_due(
                {
                    "provider": "youyun",
                    "state": "failed",
                    "cleanupState": "released",
                    "cleanupNextRetryAtEpoch": 0,
                    "remoteHandle": {"instanceId": "uhost-1"},
                },
                now=100,
            )
        )
        self.assertFalse(
            app_module._stalled_remote_task(
                {
                    "provider": "youyun",
                    "state": "completed",
                    "cleanupState": "pending",
                    "lastHeartbeatAtEpoch": 1,
                },
                now=500,
                timeout=1,
            )
        )


class YouyunCommunityImagesRouteTest(unittest.IsolatedAsyncioTestCase):
    async def test_community_route_returns_real_ids_and_verification_state(self) -> None:
        class FakeClient:
            config = SimpleNamespace(region="cn-wlcb", zone="cn-wlcb-01")

            def describe_community_images(self, **kwargs):
                return [
                    {
                        "image_id": "community-1",
                        "group_id": "group-1",
                        "name": "LLaMAFactory-WebUI",
                        "version_name": "cuda132",
                        "status": "Available",
                        "tags": ["Qwen2-VL"],
                        "description": "vision",
                        "price": 0,
                        "supported_gpu_types": ["4090"],
                        "container": True,
                        "auto_start": True,
                    }
                ]

        with patch("backend.app.build_youyun_runner", return_value=SimpleNamespace(manager=SimpleNamespace(client=FakeClient()))):
            response = await app_module.youyun_community_images(purpose="vision_training", keyword="LLaMA")

        self.assertEqual(response["source"], "community")
        self.assertEqual(response["images"][0]["id"], "community-1")
        self.assertEqual(response["images"][0]["verificationState"], "unverified")

    async def test_visual_probe_marks_community_image_verified(self) -> None:
        class Probe:
            async def run(self, request, resource):
                return {"passed": True, "cleanupState": "released", "logs": []}

        app_module.community_image_ids.add("community-verified")
        with patch.object(app_module, "_visual_probe_runner", Probe()):
            result = await app_module.run_visual_youyun_probe(
                object(), {"imageId": "community-verified"}
            )

        self.assertTrue(result["passed"])
        self.assertEqual(app_module.community_image_verifications["community-verified"]["state"], "verified")

    async def test_unverified_community_image_is_blocked_from_training(self) -> None:
        app_module.community_image_ids.add("community-blocked")
        app_module.community_image_verifications.pop("community-blocked", None)

        message = await app_module.validate_youyun_training_image("community-blocked")

        self.assertIn("尚未通过", message or "")

    async def test_verified_community_image_is_allowed_for_training(self) -> None:
        app_module.community_image_ids.add("community-ready")
        app_module.community_image_verifications["community-ready"] = {"state": "verified"}

        self.assertIsNone(await app_module.validate_youyun_training_image("community-ready"))

    async def test_expired_community_verification_is_blocked(self) -> None:
        app_module.community_image_ids.add("community-expired")
        app_module.community_image_verifications["community-expired"] = {
            "state": "verified",
            "expiresAtEpoch": 0,
        }

        message = await app_module.validate_youyun_training_image("community-expired")

        self.assertIn("尚未通过", message or "")

    async def test_image_generation_probe_verifies_only_exact_released_resource(self) -> None:
        class Probe:
            async def run(self, request, resource):
                return {"passed": True, "cleanupState": "released", "logs": []}

        resource = {
            "imageId": "community-sdxl",
            "region": "cn-wlcb",
            "zone": "cn-wlcb-01",
            "gpuType": "4090",
        }
        app_module.community_image_verifications["community-sdxl"] = {"state": "verified"}
        with patch.object(app_module, "_image_generation_probe_runner", Probe()):
            result = await app_module.run_image_generation_youyun_probe(
                app_module.ImageGenerationProbeRequest("stabilityai/stable-diffusion-xl-base-1.0"),
                resource,
            )

        self.assertEqual(result["verificationState"], "verified")
        self.assertEqual(
            app_module.community_image_verification(
                "image_generation_training",
                "community-sdxl",
                "cn-wlcb",
                "cn-wlcb-01",
                "4090",
            )["state"],
            "verified",
        )
        self.assertEqual(
            app_module.community_image_verification(
                "image_generation_training",
                "community-sdxl",
                "cn-sh2",
                "cn-sh2-02",
                "4090",
            ),
            {},
        )

    async def test_image_generation_probe_cleanup_failure_is_not_verified(self) -> None:
        class Probe:
            async def run(self, request, resource):
                return {
                    "passed": True,
                    "cleanupState": "failed",
                    "cleanupError": "instance still exists",
                    "retryBlocked": True,
                    "logs": [],
                }

        resource = {
            "imageId": "community-cleanup-failed",
            "region": "cn-wlcb",
            "zone": "cn-wlcb-01",
            "gpuType": "4090",
        }
        with patch.object(app_module, "_image_generation_probe_runner", Probe()):
            result = await app_module.run_image_generation_youyun_probe(
                app_module.ImageGenerationProbeRequest("stabilityai/stable-diffusion-xl-base-1.0"),
                resource,
            )

        self.assertEqual(result["verificationState"], "failed")
        self.assertTrue(result["retryBlocked"])
        self.assertEqual(
            app_module.community_image_verification(
                "image_generation_training",
                "community-cleanup-failed",
                "cn-wlcb",
                "cn-wlcb-01",
                "4090",
            )["state"],
            "failed",
        )


class _YouyunLifecycleStore:
    def __init__(self, task: dict) -> None:
        self.task = task
        self.messages: list[str] = []

    def get(self, task_id: str) -> dict | None:
        return self.task if task_id == self.task["id"] else None

    def list(self, *args: object, **kwargs: object) -> list[dict]:
        return [self.task]

    def update(self, task_id: str, **changes: object) -> dict:
        assert task_id == self.task["id"]
        mapping = {
            "current_step": "currentStep",
            "cancel_requested": "cancelRequested",
            "resource_state": "resourceState",
            "cleanup_state": "cleanupState",
            "cleanup_error": "cleanupError",
            "cleanup_checked_at": "cleanupCheckedAt",
        }
        self.task.update({mapping.get(key, key): value for key, value in changes.items()})
        return self.task

    def append_log(self, task_id: str, message: str) -> None:
        assert task_id == self.task["id"]
        self.messages.append(message)


class _YouyunLifecycleManager:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.cleaned: list[str] = []

    def cleanup_after_task(self, instance_id: str, stop_first: bool = True) -> dict:
        self.cleaned.append(instance_id)
        return self.result


class YouyunLifecycleApiTest(unittest.IsolatedAsyncioTestCase):
    def task(self, *, state: str = "completed", marker_state: str = "awaiting_comparison") -> dict:
        return {
            "id": "ft-youyun",
            "provider": "youyun",
            "state": state,
            "currentStep": "done",
            "resourceState": "provisioning",
            "cleanupState": "pending",
            "artifacts": [
                {
                    "type": "youyun-instance",
                    "instanceId": "uhost-1",
                    "state": marker_state,
                }
            ],
        }

    async def test_manual_release_finalizes_task_level_resource_state(self) -> None:
        store = _YouyunLifecycleStore(self.task())
        manager = _YouyunLifecycleManager({"terminated": True, "error": None, "checkedAt": "2026-09-20T15:00:00+08:00"})
        with patch.object(app_module, "store", store), patch.object(
            app_module,
            "build_youyun_runner",
            return_value=SimpleNamespace(manager=manager),
        ):
            await app_module.cleanup_youyun_task_instance("ft-youyun", reason="结束任务并释放")
        self.assertEqual(store.task["resourceState"], "released")
        self.assertEqual(store.task["cleanupState"], "released")
        self.assertEqual(store.task["cleanupCheckedAt"], "2026-09-20T15:00:00+08:00")

    async def test_manual_release_failure_marks_cleanup_failed(self) -> None:
        store = _YouyunLifecycleStore(self.task())
        manager = _YouyunLifecycleManager({"terminated": False, "error": "api down"})
        with patch.object(app_module, "store", store), patch.object(
            app_module,
            "build_youyun_runner",
            return_value=SimpleNamespace(manager=manager),
        ):
            with self.assertRaisesRegex(RuntimeError, "api down"):
                await app_module.cleanup_youyun_task_instance("ft-youyun", reason="销毁")
        self.assertEqual(store.task["resourceState"], "cleanup_failed")
        self.assertEqual(store.task["cleanupState"], "failed")
        self.assertEqual(store.task["cleanupError"], "api down")

    async def test_manual_release_self_heals_stale_task_level_state(self) -> None:
        # 存量数据：artifact 已 released 但任务级字段停留在 provisioning/pending。
        store = _YouyunLifecycleStore(self.task(marker_state="released"))
        with patch.object(app_module, "store", store):
            result = await app_module.cleanup_youyun_task_instance("ft-youyun", reason="结束任务并释放")
        self.assertEqual(result["resourceState"], "released")
        self.assertEqual(store.task["resourceState"], "released")
        self.assertEqual(store.task["cleanupState"], "released")

    async def test_manual_release_persists_released_marker(self) -> None:
        store = _YouyunLifecycleStore(self.task())
        manager = _YouyunLifecycleManager({"terminated": True, "error": None})
        with patch.object(app_module, "store", store), patch.object(
            app_module,
            "build_youyun_runner",
            return_value=SimpleNamespace(manager=manager),
        ):
            updated = await app_module.cleanup_youyun_task_instance(
                "ft-youyun", reason="结束任务并释放"
            )
        self.assertEqual(updated["artifacts"][0]["state"], "released")
        self.assertEqual(manager.cleaned, ["uhost-1"])
        self.assertTrue(any("已结束任务并释放" in message for message in store.messages))



    async def test_release_failure_remains_retryable(self) -> None:
        store = _YouyunLifecycleStore(self.task())
        manager = _YouyunLifecycleManager(
            {"terminated": False, "error": "billing lock"}
        )
        with patch.object(app_module, "store", store), patch.object(
            app_module,
            "build_youyun_runner",
            return_value=SimpleNamespace(manager=manager),
        ):
            with self.assertRaisesRegex(RuntimeError, "billing lock"):
                await app_module.cleanup_youyun_task_instance(
                    "ft-youyun", reason="销毁"
                )
        self.assertEqual(store.task["artifacts"][0]["state"], "cleanup_failed")
        self.assertEqual(
            store.task["artifacts"][0]["cleanupError"], "billing lock"
        )

    async def test_restart_reconciliation_cleans_interrupted_instance(self) -> None:
        store = _YouyunLifecycleStore(self.task(state="failed", marker_state="running"))
        manager = _YouyunLifecycleManager({"terminated": True, "error": None})
        with patch.object(app_module, "store", store), patch.object(
            app_module,
            "build_youyun_runner",
            return_value=SimpleNamespace(manager=manager),
        ):
            await app_module.reconcile_interrupted_youyun_instances()
        self.assertEqual(store.task["artifacts"][0]["state"], "released")
        self.assertEqual(manager.cleaned, ["uhost-1"])


class DatasetInteractionLogicTest(unittest.TestCase):
    def test_storage_backend_prefers_postgres_and_keeps_local_fallback(self) -> None:
        self.assertEqual(app_module.resolve_storage_backend("", "postgresql://local"), "postgres")
        self.assertEqual(app_module.resolve_storage_backend("", ""), "sqlite")
        self.assertEqual(app_module.resolve_storage_backend("sqlite", "postgresql://local"), "sqlite")

    def test_clone_names_increment_from_one_and_ignore_existing_source_suffix(self) -> None:
        source = {"name": "数据集"}
        existing = [
            {"name": "数据集"},
            {"name": "数据集 副本1"},
            {"name": "数据集 副本3"},
        ]
        self.assertEqual(app_module.next_clone_name(source, existing), "数据集 副本2")
        self.assertEqual(
            app_module.next_clone_name({"name": "数据集 副本1"}, existing),
            "数据集 副本2",
        )

    def test_import_source_must_be_jsonl_under_exports(self) -> None:
        self.assertEqual(
            app_module.normalize_import_source_key("/exports/team/train.jsonl"),
            "exports/team/train.jsonl",
        )
        with self.assertRaisesRegex(ValueError, "exports"):
            app_module.normalize_import_source_key("datasets/train.jsonl")
        with self.assertRaisesRegex(ValueError, "JSONL"):
            app_module.normalize_import_source_key("exports/team/manifest.json")


class _FakeDatasetStore:
    def __init__(self, datasets: list[dict]) -> None:
        self.datasets = {item["id"]: dict(item) for item in datasets}

    def get_dataset(self, dataset_id: str):
        return self.datasets.get(dataset_id)

    def list_datasets(self):
        return list(self.datasets.values())

    def create_dataset(self, payload):
        record = dict(payload)
        self.datasets[record["id"]] = record
        return dict(record)


def _visual_dataset_dir(dataset_id: str, *, missing_image: bool = False) -> Path:
    directory = app_module.DATASET_ROOT / dataset_id
    (directory / "images").mkdir(parents=True, exist_ok=True)
    (directory / "images" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    if missing_image:
        (directory / "metadata.jsonl").write_text(
            '{"file_name": "images/ghost.png", "question": "颜色", "answer": "绿色"}\n',
            encoding="utf-8",
        )
    else:
        (directory / "metadata.jsonl").write_text(
            '{"file_name": "images/a.png", "question": "颜色", "answer": "绿色"}\n',
            encoding="utf-8",
        )
        (directory / "train.jsonl").write_text(
            '{"image": "images/a.png", "question": "颜色", "answer": "绿色"}\n',
            encoding="utf-8",
        )
    return directory


def _visual_dataset_record(dataset_id: str) -> dict:
    directory = app_module.DATASET_ROOT / dataset_id
    return {
        "id": dataset_id,
        "name": "视觉理解集",
        "type": "视觉理解",
        "structure": "IMAGE_TEXT",
        "state": "ready",
        "sampleCount": 1,
        "paths": {
            "trainPath": str(directory / "train.jsonl"),
            "mediaRoot": str(directory / "images"),
            "manifestPath": str(directory / "metadata.jsonl"),
        },
    }


class ImageDatasetLifecycleTest(unittest.IsolatedAsyncioTestCase):
    def test_revalidate_keeps_valid_image_dataset_ready(self) -> None:
        _visual_dataset_dir("ds-ok")
        dataset = _visual_dataset_record("ds-ok")
        with patch.object(app_module, "store", _FakeDatasetStore([dataset])):
            result = asyncio.run(app_module.revalidate_dataset("ds-ok"))
        self.assertEqual(result["dataset"]["state"], "ready")
        self.assertIn("图片数据集校验通过", result["dataset"]["validationMessage"])

    def test_revalidate_marks_broken_image_dataset_failed(self) -> None:
        _visual_dataset_dir("ds-broken", missing_image=True)
        dataset = _visual_dataset_record("ds-broken")
        with patch.object(app_module, "store", _FakeDatasetStore([dataset])):
            result = asyncio.run(app_module.revalidate_dataset("ds-broken"))
        self.assertEqual(result["dataset"]["state"], "failed")
        self.assertIn("不存在", result["dataset"]["validationMessage"])

    async def test_clone_copies_image_dataset_directory(self) -> None:
        _visual_dataset_dir("ds-src")
        dataset = _visual_dataset_record("ds-src")
        fake_store = _FakeDatasetStore([dataset])
        with patch.object(app_module, "store", fake_store):
            cloned = await app_module.clone_dataset("ds-src")
        self.assertEqual(cloned["state"], "ready")
        self.assertEqual(cloned["sampleCount"], 1)
        self.assertNotEqual(cloned["id"], "ds-src")
        clone_dir = app_module.DATASET_ROOT / cloned["id"]
        self.assertTrue((clone_dir / "images" / "a.png").is_file())
        self.assertTrue((clone_dir / "train.jsonl").is_file())
        self.assertTrue((clone_dir / "metadata.jsonl").is_file())
        self.assertEqual(cloned["paths"]["manifestPath"], str(clone_dir / "metadata.jsonl"))
        self.assertEqual(cloned["paths"]["mediaRoot"], str(clone_dir / "images"))

    async def test_clone_image_dataset_fails_when_referenced_image_missing(self) -> None:
        _visual_dataset_dir("ds-src-bad", missing_image=True)
        dataset = _visual_dataset_record("ds-src-bad")
        with patch.object(app_module, "store", _FakeDatasetStore([dataset])):
            cloned = await app_module.clone_dataset("ds-src-bad")
        self.assertEqual(cloned["state"], "failed")
        self.assertIn("不存在", cloned["validationMessage"])

    async def test_repair_preview_rejects_image_dataset(self) -> None:
        _visual_dataset_dir("ds-fix")
        dataset = _visual_dataset_record("ds-fix")
        with patch.object(app_module, "store", _FakeDatasetStore([dataset])):
            with self.assertRaises(HTTPException) as caught:
                await app_module.preview_dataset_repair(
                    "ds-fix", app_module.DatasetRepairRequest(mode="manual", edits=[])
                )
        self.assertEqual(caught.exception.status_code, 409)


    def test_shared_storage_flag_supports_explicit_local_isolation(self) -> None:
        self.assertTrue(app_module.resolve_shared_storage_enabled(""))
        self.assertTrue(app_module.resolve_shared_storage_enabled("true"))
        self.assertFalse(app_module.resolve_shared_storage_enabled("false"))
        with self.assertRaisesRegex(ValueError, "XUNTUI_SHARED_STORAGE_ENABLED"):
            app_module.resolve_shared_storage_enabled("sometimes")


class YouyunImageCompatibilityTest(unittest.TestCase):
    def test_dual_mode_zones_accept_both_image_runtimes_and_expose_capabilities(self) -> None:
        zones = [
            {"region": "cn-wlcb", "zone": "cn-wlcb-01", "is_pod": True},
            {"region": "cn-sh2", "zone": "cn-sh2-02", "is_pod": True},
        ]
        container_locations = app_module._youyun_location_options(
            zones, image={"container": True, "image_type": "Community"}
        )
        vm_locations = app_module._youyun_location_options(
            zones, image={"container": False, "image_type": "System"}
        )
        for locations in (container_locations, vm_locations):
            by_zone = {item["zone"]: item for item in locations}
            self.assertEqual(by_zone["cn-wlcb-01"]["supportedModes"], ["pod", "uhost"])
            self.assertEqual(by_zone["cn-sh2-02"]["supportedModes"], ["pod", "uhost"])
            self.assertTrue(by_zone["cn-wlcb-01"]["supported"])
            self.assertTrue(by_zone["cn-sh2-02"]["supported"])
            self.assertTrue(by_zone["cn-wlcb-01"]["capabilities"]["fixedPublicIp"])

    def test_container_images_disable_uhost_zones(self) -> None:
        zones = [
            {"region": "cn-bj2", "zone": "cn-bj2-01", "is_pod": False},
            {"region": "cn-bj2", "zone": "cn-bj2-03", "is_pod": True},
        ]
        locations = app_module._youyun_location_options(
            zones, image={"container": True, "image_type": "Community"}
        )
        by_zone = {item["zone"]: item for item in locations}
        self.assertFalse(by_zone["cn-bj2-01"]["supported"])
        self.assertTrue(by_zone["cn-bj2-03"]["supported"])
        self.assertIn("容器", by_zone["cn-bj2-01"]["disabledReason"])

    def test_virtual_machine_images_disable_pod_zones(self) -> None:
        zones = [
            {"region": "cn-bj2", "zone": "cn-bj2-01", "is_pod": False},
            {"region": "cn-bj2", "zone": "cn-bj2-03", "is_pod": True},
        ]
        locations = app_module._youyun_location_options(
            zones, image={"container": False, "image_type": "Other"}
        )
        by_zone = {item["zone"]: item for item in locations}
        self.assertTrue(by_zone["cn-bj2-01"]["supported"])
        self.assertFalse(by_zone["cn-bj2-03"]["supported"])
        self.assertIn("虚机", by_zone["cn-bj2-03"]["disabledReason"])


class YouyunRegionSelectionTest(unittest.IsolatedAsyncioTestCase):
    async def test_resource_selection_keeps_container_default_on_pod_zone(self) -> None:
        class Client:
            config = SimpleNamespace(region="cn-wlcb", zone="cn-wlcb-01", gpu_type="4090", image_id="img", charge_type="Postpay")

            def describe_support_zones(self, region=None):
                return [
                    {"region": "cn-wlcb", "zone": "cn-wlcb-01", "is_pod": True},
                    {"region": "cn-wlcb", "zone": "cn-wlcb-02", "is_pod": False},
                ]

            def check_resource_capacity(self, **kwargs):
                return SimpleNamespace(specs=[SimpleNamespace(gpu=1, cpu=16, memory_gb=64, resource_enough=True)])

            def get_instance_price(self, **kwargs):
                return SimpleNamespace(currency="CNY", instance=1.0, disks=0.0, system_disks=0.0, image=0.0, total=1.0, charge_type="Postpay")

        client = Client()
        runner = SimpleNamespace(manager=SimpleNamespace(client=client))
        with patch.object(app_module, "build_youyun_runner", return_value=runner), patch.object(
            app_module.youyun_resource_health,
            "metadata",
            side_effect=lambda candidate: {"healthStatus": "cooldown"} if candidate.get("zone") == "cn-wlcb-01" else {"healthStatus": "unknown"},
        ):
            result = await app_module.resolve_youyun_resource(
                app_module.YouyunResourceSelection(mode="auto", instanceMode="auto", region="cn-wlcb")
            )

        self.assertEqual(result["instanceMode"], "pod")
        self.assertEqual(result["zone"], "cn-wlcb-01")
        self.assertFalse(result["fallback"])

    async def test_gpu_specs_skips_pod_zone_in_health_cooldown_and_uses_uhost(self) -> None:
        class Client:
            config = SimpleNamespace(region="cn-wlcb", zone="cn-wlcb-01", gpu_type="4090", image_id="img", charge_type="Postpay")

            def describe_support_zones(self, region=None):
                return [
                    {"region": "cn-wlcb", "zone": "cn-wlcb-01", "is_pod": True},
                    {"region": "cn-wlcb", "zone": "cn-wlcb-02", "is_pod": False},
                ]

            def describe_instance_types(self, **kwargs):
                return [SimpleNamespace(name="4090", zone=kwargs.get("zone"), sold_out=False, description="RTX", graphics_memory_gb=24)]

            def describe_images(self, **kwargs):
                return [{"image_id": "img", "name": "torch", "image_type": "App", "status": "Available", "tags": [], "description": ""}]

            def describe_community_images(self, **kwargs):
                return []

            def check_resource_capacity(self, **kwargs):
                return SimpleNamespace(specs=[SimpleNamespace(gpu=1, cpu=16, memory_gb=64, resource_enough=True)])

        client = Client()
        runner = SimpleNamespace(manager=SimpleNamespace(client=client))
        with patch.object(app_module, "build_youyun_runner", return_value=runner), patch.object(
            app_module.youyun_resource_health, "metadata", side_effect=lambda candidate: {"healthStatus": "cooldown"} if candidate.get("zone") == "cn-wlcb-01" else {"healthStatus": "unknown"}
        ):
            result = await app_module.youyun_gpu_specs(
                gpuType="4090", imageId="img", purpose="training", instanceMode="auto", region="cn-wlcb", zone=None
            )

        self.assertEqual(result["instanceMode"], "uhost")
        self.assertEqual(result["zone"], "cn-wlcb-02")
        self.assertIn("健康", result["fallbackReason"])
        zone_metadata = {item["zone"]: item for item in result["zones"]}
        self.assertEqual(zone_metadata["cn-wlcb-01"]["supportedModes"], ["pod", "uhost"])
        self.assertIn("capabilities", zone_metadata["cn-wlcb-01"])

    async def test_resource_selection_preserves_explicit_pod_region_and_zone(self) -> None:
        class Client:
            config = SimpleNamespace(
                region="cn-wlcb", zone="cn-wlcb-01", gpu_type="4090",
                image_id="img", charge_type="Postpay",
            )

            def describe_support_zones(self, region=None):
                return [{"region": "cn-sh2", "zone": "cn-sh2-02", "is_pod": True}]

            def check_resource_capacity(self, **kwargs):
                self.capacity_kwargs = kwargs
                return SimpleNamespace(specs=[SimpleNamespace(gpu=1, cpu=16, memory_gb=64, resource_enough=True)])

            def get_instance_price(self, **kwargs):
                self.price_kwargs = kwargs
                return SimpleNamespace(currency="CNY", instance=1.0, disks=0.0, system_disks=0.0, image=0.0, total=1.0, charge_type="Postpay")

        client = Client()
        runner = SimpleNamespace(manager=SimpleNamespace(client=client))
        with patch.object(app_module, "build_youyun_runner", return_value=runner):
            result = await app_module.resolve_youyun_resource(
                app_module.YouyunResourceSelection(
                    mode="manual", instanceMode="pod", region="cn-sh2", zone="cn-sh2-02",
                    gpuType="4090", gpuCount=1, cpuCores=16, memoryGb=64, imageId="img",
                )
            )

        self.assertEqual(result["region"], "cn-sh2")
        self.assertEqual(result["zone"], "cn-sh2-02")
        self.assertEqual(result["instanceMode"], "pod")
        self.assertEqual(client.capacity_kwargs["zone"], "cn-sh2-02")
        self.assertEqual(client.price_kwargs["zone"], "cn-sh2-02")

    async def test_gpu_specs_returns_pod_zone_catalog_for_selected_region(self) -> None:
        class Client:
            config = SimpleNamespace(region="cn-wlcb", zone="cn-wlcb-01", gpu_type="4090", image_id="img", charge_type="Postpay")

            def describe_support_zones(self, region=None):
                return [
                    {"region": "cn-wlcb", "zone": "cn-wlcb-03", "describe": "Pod", "is_pod": True},
                    {"region": "cn-sh2", "zone": "cn-sh2-02", "describe": "Pod", "is_pod": True},
                ]

            def describe_instance_types(self, **kwargs):
                return [SimpleNamespace(name="4090", zone="cn-sh2-02", sold_out=False, description="RTX", graphics_memory_gb=24)]

            def describe_images(self, **kwargs):
                return [{"image_id": "img", "name": "cuda128_torch280_py312", "image_type": "App", "status": "Available", "tags": [], "description": ""}]

            def describe_community_images(self, **kwargs):
                return []

            def check_resource_capacity(self, **kwargs):
                return SimpleNamespace(specs=[SimpleNamespace(gpu=1, cpu=16, memory_gb=64, resource_enough=True)])

        runner = SimpleNamespace(manager=SimpleNamespace(client=Client()))
        with patch.object(app_module, "build_youyun_runner", return_value=runner):
            result = await app_module.youyun_gpu_specs(
                gpuType="4090", imageId="img", purpose="training",
                instanceMode="pod", region="cn-sh2", zone="cn-sh2-02",
            )

        self.assertEqual(result["region"], "cn-sh2")
        self.assertEqual(result["zone"], "cn-sh2-02")
        self.assertEqual(result["instanceMode"], "pod")
        self.assertEqual(len(result["zones"]), 2)

    async def test_gpu_specs_returns_all_supported_regions_for_auto_catalog(self) -> None:
        class Client:
            config = SimpleNamespace(region="cn-wlcb", zone="cn-wlcb-01", gpu_type="4090", image_id="img", charge_type="Postpay")

            def describe_support_zones(self, region=None):
                self.requested_region = region
                return [
                    {"region": "cn-bj2", "zone": "cn-bj2-01", "describe": "Pod", "is_pod": True},
                    {"region": "cn-sh2", "zone": "cn-sh2-02", "describe": "Pod", "is_pod": True},
                    {"region": "cn-wlcb", "zone": "cn-wlcb-02", "describe": "UHost", "is_pod": False},
                ]

            def describe_instance_types(self, **kwargs):
                return [SimpleNamespace(name="4090", zone=kwargs.get("zone"), sold_out=False, description="RTX", graphics_memory_gb=24)]

            def describe_images(self, **kwargs):
                return [{"image_id": "img", "name": "cuda128_torch280_py312", "image_type": "App", "status": "Available", "tags": [], "description": ""}]

            def describe_community_images(self, **kwargs):
                return []

            def check_resource_capacity(self, **kwargs):
                return SimpleNamespace(specs=[SimpleNamespace(gpu=1, cpu=16, memory_gb=64, resource_enough=True)])

        client = Client()
        runner = SimpleNamespace(manager=SimpleNamespace(client=client))
        with patch.object(app_module, "build_youyun_runner", return_value=runner):
            result = await app_module.youyun_gpu_specs(
                gpuType="4090", imageId="img", purpose="inference", instanceMode="auto"
            )

        self.assertIsNone(client.requested_region)
        self.assertEqual({item["region"] for item in result["zones"]}, {"cn-bj2", "cn-sh2", "cn-wlcb"})
        self.assertEqual(result["zones"][0]["region"], "cn-bj2")

    async def test_gpu_specs_distinguishes_catalog_status_from_combination_capacity(self) -> None:
        class Client:
            config = SimpleNamespace(
                region="cn-wlcb", zone="cn-wlcb-01", gpu_type="4090",
                image_id="inventory-status-test", charge_type="Postpay",
            )

            def describe_support_zones(self, region=None):
                return [{"region": "cn-wlcb", "zone": "cn-wlcb-01", "describe": "华北二A", "is_pod": True}]

            def describe_instance_types(self, **kwargs):
                return [
                    SimpleNamespace(name="4090", zone="cn-wlcb-01", sold_out=False, description="RTX 4090", graphics_memory_gb=24),
                    SimpleNamespace(name="5090", zone="cn-wlcb-01", sold_out=False, description="RTX 5090", graphics_memory_gb=32),
                ]

            def describe_images(self, **kwargs):
                return [{"image_id": "inventory-status-test", "name": "Torch", "image_type": "App", "status": "Available", "tags": [], "description": ""}]

            def describe_community_images(self, **kwargs):
                return []

            def check_resource_capacity(self, **kwargs):
                return SimpleNamespace(specs=[SimpleNamespace(gpu=1, cpu=16, memory_gb=64, resource_enough=False)])

        runner = SimpleNamespace(manager=SimpleNamespace(client=Client()))
        with patch.object(app_module, "build_youyun_runner", return_value=runner):
            result = await app_module.youyun_gpu_specs(
                gpuType="4090", imageId="inventory-status-test", purpose="training",
                instanceMode="pod", region="cn-wlcb", zone="cn-wlcb-01",
            )

        machines = {item["name"]: item for item in result["machineTypes"]}
        self.assertEqual(machines["4090"]["inventoryStatus"], "sold_out")
        self.assertIn("CPU / 内存", machines["4090"]["inventoryMessage"])
        self.assertEqual(machines["5090"]["inventoryStatus"], "sold_out")
        self.assertFalse(machines["5090"]["soldOut"])

    async def test_gpu_specs_can_skip_community_discovery_for_text_selectors(self) -> None:
        community_calls = 0

        class Client:
            config = SimpleNamespace(
                region="cn-wlcb", zone="cn-wlcb-01", gpu_type="4090",
                image_id="catalog-only-test", charge_type="Postpay",
            )

            def describe_support_zones(self, region=None):
                return [{"region": "cn-wlcb", "zone": "cn-wlcb-01", "describe": "华北二A", "is_pod": True}]

            def describe_instance_types(self, **kwargs):
                return [SimpleNamespace(name="4090", zone="cn-wlcb-01", sold_out=False, description="RTX", graphics_memory_gb=24)]

            def describe_images(self, **kwargs):
                return [{"image_id": "catalog-only-test", "name": "Torch", "image_type": "App", "status": "Available", "tags": [], "description": ""}]

            def describe_community_images(self, **kwargs):
                nonlocal community_calls
                community_calls += 1
                return []

            def check_resource_capacity(self, **kwargs):
                return SimpleNamespace(specs=[SimpleNamespace(gpu=1, cpu=16, memory_gb=64, resource_enough=True)])

        runner = SimpleNamespace(manager=SimpleNamespace(client=Client()))
        with patch.object(app_module, "build_youyun_runner", return_value=runner):
            result = await app_module.youyun_gpu_specs(
                gpuType="4090", imageId="catalog-only-test", purpose="training",
                instanceMode="pod", region="cn-wlcb", zone="cn-wlcb-01", includeCommunity=False,
            )

        self.assertEqual(result["communityImages"], [])
        self.assertEqual(community_calls, 0)

    async def test_gpu_specs_precomputes_inventory_for_each_catalog_card(self) -> None:
        class Client:
            config = SimpleNamespace(
                region="cn-wlcb", zone="cn-wlcb-01", gpu_type="4090",
                image_id="catalog-precompute-test", charge_type="Postpay",
            )

            def describe_support_zones(self, region=None):
                return [{"region": "cn-wlcb", "zone": "cn-wlcb-01", "is_pod": True}]

            def describe_instance_types(self, **kwargs):
                return [
                    SimpleNamespace(name="4090", zone="cn-wlcb-01", sold_out=False, description="RTX 4090", graphics_memory_gb=24),
                    SimpleNamespace(name="5090", zone="cn-wlcb-01", sold_out=False, description="RTX 5090", graphics_memory_gb=32),
                ]

            def describe_images(self, **kwargs):
                return [{"image_id": "catalog-precompute-test", "name": "Torch", "image_type": "App", "container": True, "status": "Available", "tags": [], "description": ""}]

            def check_resource_capacity(self, **kwargs):
                available = kwargs.get("gpu_type") == "5090"
                return SimpleNamespace(specs=[SimpleNamespace(gpu=1, cpu=16, memory_gb=64, resource_enough=available)])

        runner = SimpleNamespace(manager=SimpleNamespace(client=Client()))
        with patch.object(app_module, "build_youyun_runner", return_value=runner):
            result = await app_module.youyun_gpu_specs(
                gpuType="4090", imageId="catalog-precompute-test", purpose="training",
                instanceMode="pod", region="cn-wlcb", zone="cn-wlcb-01", includeCommunity=False,
            )

        statuses = {item["name"]: item["inventoryStatus"] for item in result["machineTypes"]}
        self.assertEqual(statuses, {"4090": "sold_out", "5090": "available"})

    async def test_gpu_specs_reselects_zone_when_region_changes_without_explicit_zone(self) -> None:
        class Client:
            config = SimpleNamespace(region="cn-wlcb", zone="cn-wlcb-01", gpu_type="4090", image_id="img", charge_type="Postpay")

            def describe_support_zones(self, region=None):
                return [{"region": "cn-sh2", "zone": "cn-sh2-02", "describe": "UHost", "is_pod": False}]

            def describe_instance_types(self, **kwargs):
                self.instance_kwargs = kwargs
                return [SimpleNamespace(name="4090", zone="cn-sh2-02", sold_out=False, description="RTX", graphics_memory_gb=24)]

            def describe_images(self, **kwargs):
                self.image_kwargs = kwargs
                return [{"image_id": "img", "name": "cuda128_torch280_py312", "image_type": "App", "status": "Available", "tags": [], "description": ""}]

            def describe_community_images(self, **kwargs):
                return []

            def check_resource_capacity(self, **kwargs):
                self.capacity_kwargs = kwargs
                return SimpleNamespace(specs=[SimpleNamespace(gpu=1, cpu=16, memory_gb=64, resource_enough=True)])

        client = Client()
        runner = SimpleNamespace(manager=SimpleNamespace(client=client))
        with patch.object(app_module, "build_youyun_runner", return_value=runner):
            result = await app_module.youyun_gpu_specs(
                gpuType="4090", imageId="img", purpose="training",
                instanceMode="uhost", region="cn-sh2", zone=None,
            )

        self.assertEqual(result["zone"], "cn-sh2-02")
        self.assertEqual(client.instance_kwargs["zone"], "cn-sh2-02")
        self.assertEqual(client.image_kwargs["zone"], "cn-sh2-02")
        self.assertEqual(client.capacity_kwargs["zone"], "cn-sh2-02")

    async def test_gpu_specs_uses_compatible_training_image_when_default_is_incompatible(self) -> None:
        class Client:
            config = SimpleNamespace(
                region="cn-wlcb", zone="cn-wlcb-01", gpu_type="4090",
                image_id="torch291", charge_type="Postpay",
            )

            def describe_support_zones(self, region=None):
                return [{"region": "cn-wlcb", "zone": "cn-wlcb-01", "describe": "UHost", "is_pod": False}]

            def describe_instance_types(self, **kwargs):
                return [SimpleNamespace(name="4090", zone="cn-wlcb-01", sold_out=False, description="RTX", graphics_memory_gb=24)]

            def describe_images(self, **kwargs):
                return [
                    {"image_id": "torch291", "name": "cuda128_torch291_py312", "image_type": "App", "status": "Available", "tags": [], "description": ""},
                    {"image_id": "torch280", "name": "cuda128_torch280_py312", "image_type": "App", "status": "Available", "tags": [], "description": ""},
                ]

            def describe_community_images(self, **kwargs):
                return []

            def check_resource_capacity(self, **kwargs):
                self.capacity_kwargs = kwargs
                return SimpleNamespace(specs=[SimpleNamespace(gpu=1, cpu=16, memory_gb=64, resource_enough=True)])

        client = Client()
        runner = SimpleNamespace(manager=SimpleNamespace(client=client))
        with patch.object(app_module, "build_youyun_runner", return_value=runner):
            result = await app_module.youyun_gpu_specs(
                gpuType="4090", imageId=None, purpose="training",
                instanceMode="uhost", region="cn-wlcb", zone="cn-wlcb-01",
            )

        self.assertEqual(result["imageId"], "torch280")
        self.assertEqual(client.capacity_kwargs["image_id"], "torch280")


class DatasetApiConcurrencyTest(unittest.IsolatedAsyncioTestCase):
    async def test_dataset_catalog_runs_blocking_store_read_in_worker_thread(self) -> None:
        catalog = [{"id": "dataset-1"}]
        store = SimpleNamespace(list_datasets=lambda: catalog)

        with patch.object(app_module, "store", store):
            result = await app_module.datasets()

        self.assertEqual(result, catalog)

    async def test_task_catalog_runs_blocking_store_read_in_worker_thread(self) -> None:
        tasks = [{"id": "task-1"}]
        calls: list[dict] = []
        def list_page(**kwargs):
            calls.append(kwargs)
            return {"items": tasks, "total": 1, "page": 1, "pageSize": 20, "totalPages": 1}
        store = SimpleNamespace(list_page=list_page)

        with patch.object(app_module, "store", store):
            result = await app_module.list_tasks()

        self.assertEqual(result["items"], tasks)
        self.assertEqual(calls[0].get("include_details"), False)

    async def test_training_worker_claims_postgres_task_in_worker_thread(self) -> None:
        claims: list[dict] = []
        def claim_next(**kwargs):
            claims.append(kwargs)
            raise asyncio.CancelledError
        store = SimpleNamespace(claim_next=claim_next)
        sleep = AsyncMock(side_effect=asyncio.CancelledError)

        with patch.object(app_module, "store", store), patch.object(
            app_module.asyncio, "sleep", sleep
        ), patch.object(app_module, "local_runtime_enabled", return_value=False):
            with self.assertRaises(asyncio.CancelledError):
                await app_module.worker_loop()

        self.assertTrue(all("provider" in item for item in claims))

    async def test_overview_reads_task_summaries_without_logs_or_metrics(self) -> None:
        store = SimpleNamespace(
            list_datasets=Mock(return_value=[]),
            list=Mock(return_value=[]),
            list_model_assets=Mock(return_value=[]),
        )
        inference_store = SimpleNamespace(list=Mock(return_value=[]))

        with patch.object(app_module, "store", store), patch.object(
            app_module, "inference_store", inference_store
        ), patch.object(app_module, "probe_gpu_info", return_value={"available": False}), patch.object(
            app_module, "build_overview_snapshot", side_effect=lambda **payload: payload
        ):
            await app_module.overview()

        store.list.assert_called_once_with()

    async def test_platform_overview_degrades_when_one_domain_read_fails(self) -> None:
        store = SimpleNamespace(
            list_datasets=Mock(side_effect=RuntimeError("dataset store unavailable")),
            list=Mock(return_value=[]),
            list_model_assets=Mock(return_value=[]),
        )
        inference_store = SimpleNamespace(list=Mock(return_value=[]))

        with patch.object(app_module, "store", store), patch.object(
            app_module, "inference_store", inference_store
        ), patch.object(app_module, "probe_gpu_info", return_value={"available": False}), patch.object(
            app_module, "model_uploader", None
        ), patch.object(
            app_module, "build_overview_snapshot", wraps=app_module.build_overview_snapshot
        ):
            payload = await app_module.platform_overview()

        self.assertEqual(payload["datasets"]["total"], 0)
        self.assertTrue(
            any(issue.get("source") == "datasets" for issue in payload["issues"])
        )

    async def test_platform_tasks_merges_all_training_runtimes(self) -> None:
        store = SimpleNamespace(list=Mock(return_value=[{"id": "ft-1", "state": "completed", "name": "NLP"}]))
        vision_runtime = SimpleNamespace(list_tasks=Mock(return_value=[{"id": "vt-1", "state": "failed", "name": "视觉", "phase": "preparing"}]))
        image_runtime = SimpleNamespace(list_tasks=Mock(return_value=[{"id": "igt-1", "state": "queued", "name": "文生图"}]))

        with patch.object(app_module, "store", store), patch.object(
            app_module, "vision_training_runtime", vision_runtime
        ), patch.object(app_module, "image_generation_training_runtime", image_runtime):
            payload = await app_module.platform_tasks()

        self.assertEqual(payload["total"], 3)
        self.assertEqual({item["modality"] for item in payload["items"]}, {"nlp", "vision", "image-generation"})


class YouyunResourceModelTest(unittest.TestCase):
    def test_task_create_validates_nested_youyun_resource(self) -> None:
        payload = {
            "name": "远程优云测试",
            "executionMode": "remote",
            "provider": "youyun",
            "model": "Qwen/Qwen2.5-7B-Instruct",
            "datasetId": "dataset-1",
            "datasetName": "数据集",
            "outputName": "remote-test",
            "parameters": {
                "epochs": 1,
                "learningRate": "1e-4",
                "batchSize": 1,
                "gradientAccumulation": 1,
                "validationSteps": 10,
                "maxSequenceLength": 512,
                "loraRank": 8,
                "loraAlpha": 16,
                "loraDropout": 0.1,
                "lrScheduler": "cosine",
                "loraPlusRatio": 1,
                "targetModules": "all",
                "optimizer": "adamw_torch",
                "warmupRatio": 0.03,
                "weightDecay": 0,
                "maxGradNorm": 1,
                "seed": 42,
                "loggingSteps": 1,
                "packing": False,
                "gradientCheckpointing": True,
            },
            "youyunResource": {
                "mode": "manual",
                "gpuType": "4090",
                "gpuCount": 1,
                "cpuCores": 16,
                "memoryGb": 64,
            },
        }
        task = app_module.TaskCreate.model_validate(payload)
        self.assertEqual(task.youyunResource.cpuCores, 16)
        self.assertEqual(task.youyunResource.memoryGb, 64)


class GpuInfoTest(unittest.TestCase):
    def test_missing_nvidia_smi_uses_friendly_cpu_node_message(self) -> None:
        missing = FileNotFoundError(
            2, "No such file or directory", "nvidia-smi"
        )
        with patch("backend.app.subprocess.run", side_effect=missing):
            info = app_module.probe_gpu_info()

        self.assertEqual(
            info,
            {
                "available": False,
                "reason": "当前节点未检测到可用 GPU",
            },
        )

    def test_other_gpu_probe_errors_keep_diagnostic_detail(self) -> None:
        with patch(
            "backend.app.subprocess.run",
            side_effect=PermissionError("permission denied"),
        ):
            info = app_module.probe_gpu_info()

        self.assertFalse(info["available"])
        self.assertEqual(info["reason"], "GPU 状态读取失败：permission denied")


class CliTaskApiTest(unittest.IsolatedAsyncioTestCase):
    async def test_cli_submission_is_parsed_into_a_queued_task(self) -> None:
        task = await app_module.create_cli_task(
            app_module.CliTaskCreate(
                command=(
                    "python scripts/train_cli.py submit --name 'api cli check' "
                    "--model Qwen/Qwen3-1.7B --dataset-id qwenruozhi_v1 "
                    "--dataset-name '弱智吧语料 SFT v1' --output-name check "
                    "--validation-split 0 --early-stopping --trainer-param tf32=false"
                )
            )
        )
        self.assertEqual(task["state"], "queued")
        self.assertEqual(task["validationSplit"], 0)
        self.assertTrue(task["earlyStopping"])
        self.assertFalse(task["extraParameters"]["tf32"])
        self.assertEqual(task["dataset"], "弱智吧语料 SFT v1")

    async def test_cli_submission_rejects_shell_syntax(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            await app_module.create_cli_task(
                app_module.CliTaskCreate(
                    command=(
                        "python scripts/train_cli.py submit --name x --model m "
                        "--dataset-id qwenruozhi_v1 --dataset-name n "
                        "--output-name o; whoami"
                    )
                )
            )
        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("Shell", str(raised.exception.detail))

    async def test_cli_submission_rejects_unknown_dataset_id(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            await app_module.create_cli_task(
                app_module.CliTaskCreate(
                    command=(
                        "submit --name x --model m --dataset-id missing-dataset "
                        "--dataset-name n --output-name o"
                    )
                )
            )
        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("数据集不存在", str(raised.exception.detail))

    async def test_cli_submission_rejects_unpublished_dataset(self) -> None:
        app_module.store.upsert_dataset(
            {
                "id": "ready-dataset",
                "name": "待发布数据集",
                "type": "文本生成",
                "structure": "SFT",
                "state": "ready",
                "sampleCount": 1,
                "paths": {"trainPath": str(TEST_ROOT / "ready.jsonl")},
                "owner": "tester",
            }
        )
        with self.assertRaises(HTTPException) as raised:
            await app_module.create_cli_task(
                app_module.CliTaskCreate(
                    command=(
                        "submit --name x --model m --dataset-id ready-dataset "
                        "--dataset-name n --output-name o"
                    )
                )
            )
        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("已发布", str(raised.exception.detail))


if __name__ == "__main__":
    unittest.main()


class InferenceImageContractTest(unittest.IsolatedAsyncioTestCase):
    """Exercise raw provider metadata through the real HTTP create route."""

    async def _create_with_container(self, container):
        import httpx
        from fastapi import FastAPI
        from backend.providers.youyun.client import YouyunClient
        from backend.providers.youyun.models import YouyunConfig
        from backend.inference_service import InferenceRuntime, InferenceStore, create_inference_router

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = root / "adapter"
            adapter.mkdir()
            (adapter / "adapter_config.json").write_text("{}")
            (adapter / "adapter_model.safetensors").write_bytes(b"fixture")
            model = "/model/ModelScope/Qwen/Qwen2.5-3B-Instruct"
            task = {
                "id": "ft-public", "state": "completed", "model": model,
                "modelSource": "public", "baseModelMetadata": {"maxContextLength": 4096},
                "youyunResource": {"publicModelPath": model},
                "artifacts": [{"type": "adapter", "variant": "best", "path": str(adapter)}],
            }
            client = YouyunClient(YouyunConfig(
                region="cn-wlcb", zone="cn-wlcb-03", public_key="test", private_key="test",
            ))
            raw = {"CompShareImageId": "img-vllm", "Name": "vLLM v0.25.1",
                   "ImageType": "App", "Container": container, "Status": "Available"}
            client._post = Mock(return_value={"ImageSet": [raw]})
            client.describe_support_zones = Mock(return_value=[
                {"region": "cn-wlcb", "zone": "cn-wlcb-03", "is_pod": True},
            ])
            client.check_resource_capacity = Mock(return_value=SimpleNamespace(specs=[
                SimpleNamespace(gpu=1, cpu=16, memory_gb=64, resource_enough=True),
            ]))
            client.get_instance_price = Mock(return_value=SimpleNamespace(
                currency="CNY", instance=1.0, disks=0.0, system_disks=0.0,
                image=0.0, total=1.0, charge_type="Postpay",
            ))

            async def resolve(resource):
                return await app_module.resolve_youyun_resource(
                    app_module.YouyunResourceSelection.model_validate({**resource, "purpose": "inference"})
                )

            runtime = InferenceRuntime(
                InferenceStore(root / "services.db"), SimpleNamespace(get=lambda _: task),
                port=8011, training_busy=lambda: False, youyun_resource_resolver=resolve,
            )
            api = FastAPI()
            api.include_router(create_inference_router(runtime))
            with patch.object(app_module, "build_youyun_runner", return_value=SimpleNamespace(
                manager=SimpleNamespace(client=client)
            )), patch.object(app_module, "_YOUYUN_DISCOVERY_CACHE", {}), patch.object(
                app_module, "_YOUYUN_DISCOVERY_INFLIGHT", {}
            ), patch.object(app_module.youyun_resource_health, "metadata", return_value={"healthStatus": "unknown"}):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as http:
                    response = await http.post("/api/inference/services", json={
                        "name": "public model contract", "taskId": "ft-public", "provider": "youyun",
                        "youyunResource": {"mode": "manual", "gpuType": "4090", "gpuCount": 1,
                            "cpuCores": 16, "memoryGb": 64, "imageId": "img-vllm",
                            "region": "cn-wlcb", "zone": "cn-wlcb-03", "instanceMode": "pod"},
                    })
            # No GPU creation, SSH, or model downloads are permitted by this fixture.
            self.assertTrue(all(call.args[0] == "DescribeCompShareImages" for call in client._post.call_args_list))
            return response

    async def test_raw_container_flag_survives_public_model_service_creation(self):
        response = await self._create_with_container("True")
        self.assertEqual(response.status_code, 201, response.text)
        service = response.json()
        self.assertEqual(service["state"], "created")
        self.assertIs(service["config"]["youyunResource"]["container"], True)
        self.assertEqual(service["config"]["youyunResource"]["instanceMode"], "pod")
        self.assertEqual(service["config"]["youyunResource"]["publicModelPath"], service["model"])

    async def test_non_container_and_unknown_images_remain_rejected(self):
        for container in ("False", None):
            with self.subTest(container=container):
                response = await self._create_with_container(container)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIn("公共模型库仅支持容器镜像", response.json()["detail"])


class YouyunCapacityCacheContractTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cache = patch.object(app_module, "_youyun_capacity_cache", {})
        self.pending = patch.object(app_module, "_youyun_capacity_pending", {})
        self.cache.start()
        self.pending.start()
        self.addCleanup(self.cache.stop)
        self.addCleanup(self.pending.stop)
        self.client = SimpleNamespace(
            config=SimpleNamespace(api_base_url="https://example.invalid", public_key="test-only"),
            check_resource_capacity=Mock(),
        )
        self.args = dict(gpu_type="4090", image_id="img", charge_type="Postpay", region="cn-bj2", zone="cn-bj2-03")

    async def test_real_identity_returns_and_caches_capacity(self):
        capacity = SimpleNamespace(specs=[SimpleNamespace(gpu=1, cpu=14, memory_gb=32, resource_enough=True)])
        self.client.check_resource_capacity.return_value = capacity
        first = await app_module._cached_youyun_capacity(self.client, **self.args)
        self.assertIs(first, capacity)
        self.assertIs(await app_module._cached_youyun_capacity(self.client, **self.args), capacity)
        self.client.check_resource_capacity.assert_called_once()
        self.assertFalse(app_module._youyun_capacity_pending)

    async def test_concurrent_callers_receive_the_same_result(self):
        import threading
        started, release = threading.Event(), threading.Event()
        capacity = SimpleNamespace(specs=[])
        def fetch(**kwargs):
            started.set()
            if not release.wait(2):
                raise TimeoutError("fixture release timeout")
            return capacity
        self.client.check_resource_capacity.side_effect = fetch
        owner = asyncio.create_task(app_module._cached_youyun_capacity(self.client, **self.args))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            waiter = asyncio.create_task(app_module._cached_youyun_capacity(self.client, **self.args))
            await asyncio.sleep(0)
            release.set()
            results = await asyncio.wait_for(asyncio.gather(owner, waiter), 1)
            self.assertEqual(results, [capacity, capacity])
            self.client.check_resource_capacity.assert_called_once()
        finally:
            release.set()
            owner.cancel()

    async def test_legacy_signature_success_and_failure_settle_cache_owner(self):
        capacity = SimpleNamespace(specs=[])
        def legacy(gpu_type, image_id):
            return capacity
        self.client.check_resource_capacity = legacy
        self.assertIs(await app_module._cached_youyun_capacity(self.client, **self.args), capacity)
        app_module._youyun_capacity_cache.clear()
        def failed(gpu_type, image_id):
            raise RuntimeError("provider unavailable")
        self.client.check_resource_capacity = failed
        with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
            await app_module._cached_youyun_capacity(self.client, **self.args)
        self.assertFalse(app_module._youyun_capacity_pending)


    async def test_gpu_specs_exposes_available_capacity_with_real_client_identity(self):
        from backend.providers.youyun.models import YouyunConfig, GpuMachineType, GpuSpecCombination, ResourceCapacity
        self.client.config = YouyunConfig(region="cn-bj2", zone="cn-bj2-03", public_key="test", private_key="test", image_id="img")
        self.client.describe_support_zones = Mock(return_value=[{"region": "cn-bj2", "zone": "cn-bj2-03", "is_pod": True}])
        self.client.describe_images = Mock(return_value=[{
            "image_id": "img", "name": "vLLM v0.25.1", "image_type": "App", "container": True,
            "status": "Available", "tags": [], "description": "",
        }])
        self.client.describe_instance_types = Mock(return_value=[GpuMachineType(
            name="4090", zone="cn-bj2-03", status="Normal", description="RTX", graphics_memory_gb=24,
        )])
        self.client.check_resource_capacity.return_value = ResourceCapacity(
            gpu_type="4090", specs=[GpuSpecCombination(gpu=1, cpu=14, memory_gb=32, resource_enough=True)],
        )
        with patch.object(app_module, "build_youyun_runner", return_value=SimpleNamespace(manager=SimpleNamespace(client=self.client))), \
             patch.object(app_module, "_YOUYUN_DISCOVERY_CACHE", {}), \
             patch.object(app_module, "_YOUYUN_DISCOVERY_INFLIGHT", {}), \
             patch.object(app_module, "_youyun_gpu_specs_cache", {}), \
             patch.object(app_module.youyun_resource_health, "metadata", return_value={"healthStatus": "unknown"}):
            result = await app_module.youyun_gpu_specs(
                gpuType="4090", imageId="img", purpose="inference", instanceMode="pod",
                region="cn-bj2", zone="cn-bj2-03", includeCommunity=False,
            )
        self.assertEqual(result["capacity"], [{"gpu": 1, "cpu": 14, "memoryGb": 32, "available": True}])
        self.assertEqual(result["machineTypes"][0]["inventoryStatus"], "available")

    async def test_waiter_cancellation_does_not_cancel_owner_result(self):
        import threading
        started, release = threading.Event(), threading.Event()
        capacity = SimpleNamespace(specs=[])
        def fetch(**kwargs):
            started.set()
            release.wait(2)
            return capacity
        self.client.check_resource_capacity.side_effect = fetch
        owner = asyncio.create_task(app_module._cached_youyun_capacity(self.client, **self.args))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            waiter = asyncio.create_task(app_module._cached_youyun_capacity(self.client, **self.args))
            await asyncio.sleep(0)
            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiter
            release.set()
            self.assertIs(await asyncio.wait_for(owner, 1), capacity)
            self.assertFalse(app_module._youyun_capacity_pending)
        finally:
            release.set()
            owner.cancel()
