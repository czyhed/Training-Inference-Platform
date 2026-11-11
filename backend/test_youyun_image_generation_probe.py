import asyncio
import unittest

from backend.youyun_image_generation_probe import (
    ImageGenerationProbeRequest,
    ImageGenerationProbeResult,
    evaluate_image_generation_probe,
)
from backend.youyun_image_generation_acceptance import YouyunImageGenerationProbeRunner


class _Instance:
    uhost_id = "uhost-image-probe"


class _Manager:
    def __init__(self, *, terminated: bool = True) -> None:
        self.terminated = terminated
        self.created = []
        self.cleaned = []

    def create_one_shot(self, **kwargs):
        self.created.append(kwargs)
        return _Instance()

    def ssh_credentials(self, instance):
        return {"host": "127.0.0.1", "port": 22, "user": "root", "password": "secret"}

    def cleanup_after_task(self, instance_id, *, stop_first=True, region=None, zone=None):
        self.cleaned.append((instance_id, stop_first, region, zone))
        return {"terminated": self.terminated, "error": None if self.terminated else "still exists"}


class _SSH:
    def connect(self, host, port, user, password):
        return None

    def close(self):
        return None


class ImageGenerationProbeTests(unittest.TestCase):
    def test_all_required_sdxl_checks_must_pass(self) -> None:
        request = ImageGenerationProbeRequest("stabilityai/stable-diffusion-xl-base-1.0")
        result = evaluate_image_generation_probe(
            request,
            {"python": 0, "torch": 0, "dependencies": 0, "pipeline": 0, "lora": 0, "forward": 0},
        )
        self.assertTrue(result.passed)
        self.assertIn("sdxl_pipeline", result.checks)
        self.assertEqual(result.estimated_vram_gb, 24)

    def test_dependency_failure_blocks_the_image(self) -> None:
        result = evaluate_image_generation_probe(
            ImageGenerationProbeRequest("stabilityai/stable-diffusion-xl-base-1.0"),
            {"python": 0, "torch": 0, "dependencies": 1},
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.blocking_reason, "dependencies_missing")


class ImageGenerationAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_uses_full_resource_snapshot_and_releases_instance(self) -> None:
        manager = _Manager()

        async def probe(ssh, request):
            return ImageGenerationProbeResult(True, {"dry_run": {"status": "passed"}}, None, 24, [])

        runner = YouyunImageGenerationProbeRunner(manager, ssh_factory=_SSH, probe=probe)
        result = await runner.run(
            ImageGenerationProbeRequest("stabilityai/stable-diffusion-xl-base-1.0"),
            {"region": "cn-wlcb", "zone": "cn-wlcb-01", "gpuType": "4090", "gpuCount": 1, "cpuCores": 16, "memoryGb": 64, "imageId": "community-sdxl", "chargeType": "Postpay"},
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["cleanupState"], "released")
        self.assertEqual(manager.cleaned, [("uhost-image-probe", False, "cn-wlcb", "cn-wlcb-01")])
        self.assertEqual(manager.created[0]["image_id"], "community-sdxl")
        self.assertEqual(manager.created[0]["charge_type"], "Postpay")
        self.assertEqual(manager.created[0]["region"], "cn-wlcb")
        self.assertEqual(manager.created[0]["zone"], "cn-wlcb-01")

    async def test_timeout_releases_but_does_not_verify(self) -> None:
        manager = _Manager()

        async def probe(ssh, request):
            await asyncio.sleep(1)
            return ImageGenerationProbeResult(True, {}, None, 24, [])

        runner = YouyunImageGenerationProbeRunner(manager, ssh_factory=_SSH, probe=probe, probe_timeout=0.01)
        result = await runner.run(ImageGenerationProbeRequest("sdxl"), {"imageId": "community-sdxl"})
        self.assertFalse(result["passed"])
        self.assertEqual(result["cleanupState"], "released")

    async def test_cleanup_failure_blocks_repeat_probe(self) -> None:
        manager = _Manager(terminated=False)

        async def probe(ssh, request):
            return ImageGenerationProbeResult(True, {}, None, 24, [])

        runner = YouyunImageGenerationProbeRunner(manager, ssh_factory=_SSH, probe=probe)
        first = await runner.run(ImageGenerationProbeRequest("sdxl"), {"imageId": "community-sdxl"})
        self.assertTrue(first["retryBlocked"])
        with self.assertRaisesRegex(RuntimeError, "尚未释放"):
            await runner.run(ImageGenerationProbeRequest("sdxl"), {"imageId": "community-sdxl"})


if __name__ == "__main__":
    unittest.main()
