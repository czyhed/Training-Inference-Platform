import unittest

from backend.youyun_vision_acceptance import YouyunVisionProbeRunner
from backend.youyun_vision_probe import VisionProbeRequest, VisionProbeResult


class _Instance:
    id = "inst-vision-probe"


class _UHostInstance:
    uhost_id = "uhost-vision-probe"


class _Manager:
    def __init__(self, *, terminated: bool = True) -> None:
        self.terminated = terminated
        self.created = 0
        self.cleaned: list[str] = []
        self.cleanup_stop_first: list[bool] = []

    def create_one_shot(self, **kwargs):
        self.created += 1
        return _Instance()

    def ssh_credentials(self, instance):
        return {"host": "127.0.0.1", "port": 22, "user": "root", "password": "secret"}

    def cleanup_after_task(self, instance_id, *, stop_first=True):
        self.cleaned.append(instance_id)
        self.cleanup_stop_first.append(stop_first)
        return {"terminated": self.terminated, "error": None if self.terminated else "still exists"}


class _SSH:
    def connect(self, host, port, user, password):
        return None

    def close(self):
        return None


class _FlakySSH(_SSH):
    attempts = 0

    def connect(self, host, port, user, password):
        type(self).attempts += 1
        if type(self).attempts == 1:
            raise EOFError("SSH banner not ready")


class YouyunVisionAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_timeout_still_releases_instance(self) -> None:
        manager = _Manager()

        async def probe(ssh, request):
            await asyncio.sleep(1)
            return VisionProbeResult(True, {}, None, "qlora", 16, [])

        runner = YouyunVisionProbeRunner(
            manager,
            ssh_factory=_SSH,
            probe=probe,
            probe_timeout=0.01,
        )
        result = await runner.run(
            VisionProbeRequest("Qwen/Qwen2-VL-2B-Instruct", "/model/qwen", "qwen2_vl", "qlora"),
            {"gpuType": "4090", "gpuCount": 1, "cpuCores": 16, "memoryGb": 64, "imageId": "image-lf"},
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["cleanupState"], "released")
        self.assertEqual(manager.cleaned, ["inst-vision-probe"])

    async def test_probe_retries_until_ssh_banner_is_ready(self) -> None:
        manager = _Manager()
        _FlakySSH.attempts = 0

        async def probe(ssh, request):
            return VisionProbeResult(True, {}, None, "qlora", 16, [])

        runner = YouyunVisionProbeRunner(
            manager,
            ssh_factory=_FlakySSH,
            probe=probe,
            ssh_ready_timeout=1,
            ssh_retry_delay=0,
        )
        result = await runner.run(
            VisionProbeRequest("Qwen/Qwen2-VL-2B-Instruct", "/model/qwen", "qwen2_vl", "qlora"),
            {"gpuType": "4090", "gpuCount": 1, "cpuCores": 16, "memoryGb": 64, "imageId": "image-lf"},
        )

        self.assertTrue(result["passed"])
        self.assertEqual(_FlakySSH.attempts, 2)
        self.assertEqual(manager.cleanup_stop_first, [False])

    async def test_real_youyun_instance_uses_uhost_id_for_cleanup(self) -> None:
        manager = _Manager()
        manager.create_one_shot = lambda **kwargs: _UHostInstance()

        async def probe(ssh, request):
            return VisionProbeResult(True, {}, None, "qlora", 16, [])

        runner = YouyunVisionProbeRunner(manager, ssh_factory=_SSH, probe=probe)
        result = await runner.run(
            VisionProbeRequest("Qwen/Qwen2-VL-2B-Instruct", "/model/qwen", "qwen2_vl", "qlora"),
            {"gpuType": "4090", "gpuCount": 1, "cpuCores": 16, "memoryGb": 64, "imageId": "image-lf"},
        )

        self.assertEqual(result["remoteInstanceId"], "uhost-vision-probe")
        self.assertEqual(manager.cleaned, ["uhost-vision-probe"])
        self.assertEqual(manager.cleanup_stop_first, [False])

    async def test_successful_probe_releases_instance(self) -> None:
        manager = _Manager()

        async def probe(ssh, request):
            return VisionProbeResult(True, {"forward": {"status": "passed"}}, None, "qlora", 16, [])

        runner = YouyunVisionProbeRunner(manager, ssh_factory=_SSH, probe=probe)
        result = await runner.run(
            VisionProbeRequest("Qwen/Qwen2.5-VL-3B-Instruct", "/model/qwen", "qwen2_vl", "qlora"),
            {"gpuType": "RTX4090", "gpuCount": 1, "cpuCores": 16, "memoryGb": 64, "imageId": "image-lf"},
        )

        self.assertTrue(result["passed"])
        self.assertEqual(result["cleanupState"], "released")
        self.assertEqual(manager.cleaned, ["inst-vision-probe"])

    async def test_cleanup_failure_blocks_repeat_probe(self) -> None:
        manager = _Manager(terminated=False)

        async def probe(ssh, request):
            return VisionProbeResult(False, {}, "processor_load_failed", "qlora", 16, [])

        runner = YouyunVisionProbeRunner(manager, ssh_factory=_SSH, probe=probe)
        first = await runner.run(
            VisionProbeRequest("Qwen/Qwen2-VL-2B-Instruct", "/model/qwen", "qwen2_vl", "qlora"),
            {"gpuType": "RTX4090", "gpuCount": 1, "cpuCores": 16, "memoryGb": 64, "imageId": "image-lf"},
        )
        self.assertEqual(first["cleanupState"], "failed")
        self.assertTrue(first["retryBlocked"])
        with self.assertRaisesRegex(RuntimeError, "尚未释放"):
            await runner.run(
                VisionProbeRequest("Qwen/Qwen2-VL-2B-Instruct", "/model/qwen", "qwen2_vl", "qlora"),
                {"gpuType": "RTX4090", "gpuCount": 1, "cpuCores": 16, "memoryGb": 64, "imageId": "image-lf"},
            )


if __name__ == "__main__":
    unittest.main()
