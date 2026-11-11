from __future__ import annotations

import asyncio
import unittest

from backend.image_generation_service_runtime import ImageGenerationServiceRuntime


class FakeHandle:
    def __init__(self, instance_id: str = "uhost-svc-1") -> None:
        self.instance_id = instance_id

    def persisted(self) -> dict[str, str]:
        return {
            "instanceId": self.instance_id,
            "workdir": "/root/cppt/image-generation/services/igs-1",
            "state": "running",
        }


class FakeProvider:
    def __init__(self, *, fail: bool = False, stop_report: dict | None = None) -> None:
        self.fail = fail
        self.stop_report = stop_report or {"terminated": True}
        self.calls: list[str] = []
        self.progress: list[tuple[str, int]] = []
        self.stop_persisted_calls: list[dict] = []

    async def provision_service(self, service_id, task, report_progress=None):
        self.calls.append(service_id)
        if report_progress is not None:
            report_progress("creating_instance", 10, "正在创建优云实例…")
            report_progress("preparing_runtime", 45, "正在远端准备运行环境…")
        if self.fail:
            raise RuntimeError("boom")
        return {"handle": FakeHandle()}

    async def stop_persisted(self, remote: dict) -> dict:
        self.stop_persisted_calls.append(dict(remote))
        return dict(self.stop_report)


class FakeStore:
    def __init__(self, services: dict | None = None) -> None:
        self.services = services or {}
        self.log_lines: dict[str, list[str]] = {}

    def get(self, service_id):
        return self.services.get(service_id)

    def list(self, *, include_archived=False):
        return list(self.services.values())

    def update(self, service_id, *, config=None, **changes):
        service = self.services.get(service_id)
        if service is None:
            return None
        if config is not None:
            service["config"] = config
        service.update(changes)
        return service

    def append_log(self, service_id, message):
        self.log_lines.setdefault(service_id, []).append(message)

    def logs(self, service_id, limit=200):
        return list(reversed(self.log_lines.get(service_id, [])[-limit:]))


def make_service(state: str = "created", *, with_remote: bool = False, workload: str = "image_generation") -> dict:
    config: dict = {"workloadType": workload}
    if with_remote:
        config["remoteRuntime"] = {"instanceId": "uhost-held-1"}
    return {"id": "igs-1", "name": "svc", "state": state, "config": config}


class StartStopTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_reports_progress_and_marks_running(self) -> None:
        store = FakeStore({"igs-1": make_service()})
        provider = FakeProvider()
        runtime = ImageGenerationServiceRuntime(store, lambda: provider)

        result = await runtime.start("igs-1", preflight={"ready": True})

        self.assertEqual(result["state"], "running")
        self.assertEqual(provider.calls, ["igs-1"])
        config = store.services["igs-1"]["config"]
        self.assertEqual(config["remoteRuntime"]["instanceId"], "uhost-svc-1")
        self.assertEqual(config["preflightSnapshot"], {"ready": True})
        self.assertEqual(config["startup"]["stage"], "ready")
        self.assertEqual(config["startup"]["progress"], 100)
        logs = store.logs("igs-1")
        self.assertTrue(any("开始启动" in line for line in logs))
        self.assertTrue(any("已就绪" in line for line in logs))

    async def test_start_failure_marks_failed_and_keeps_last_stage(self) -> None:
        store = FakeStore({"igs-1": make_service()})
        provider = FakeProvider(fail=True)
        runtime = ImageGenerationServiceRuntime(store, lambda: provider)

        with self.assertRaises(RuntimeError):
            await runtime.start("igs-1")

        service = store.services["igs-1"]
        self.assertEqual(service["state"], "failed")
        self.assertIn("boom", service["error"])
        self.assertNotIn("remoteRuntime", service["config"])
        self.assertEqual(service["config"]["startup"]["stage"], "preparing_runtime")
        self.assertTrue(any("启动失败" in line for line in store.logs("igs-1")))

    async def test_start_fails_fast_while_another_operation_holds_lock(self) -> None:
        store = FakeStore({"igs-1": make_service()})
        provider = FakeProvider()
        runtime = ImageGenerationServiceRuntime(store, lambda: provider)

        async with runtime.lock:
            with self.assertRaisesRegex(RuntimeError, "已有启动/停止操作"):
                await runtime.start("igs-1")
        self.assertEqual(provider.calls, [])

    async def test_stop_fails_fast_while_another_operation_holds_lock(self) -> None:
        store = FakeStore({"igs-1": make_service(state="running", with_remote=True)})
        provider = FakeProvider()
        runtime = ImageGenerationServiceRuntime(store, lambda: provider)

        async with runtime.lock:
            with self.assertRaisesRegex(RuntimeError, "已有启动/停止操作"):
                await runtime.stop("igs-1")
        self.assertEqual(provider.stop_persisted_calls, [])

    async def test_start_rejects_when_service_already_transitioning(self) -> None:
        store = FakeStore({"igs-1": make_service(state="starting")})
        provider = FakeProvider()
        runtime = ImageGenerationServiceRuntime(store, lambda: provider)

        with self.assertRaisesRegex(RuntimeError, "启动/停止流程"):
            await runtime.start("igs-1")
        self.assertEqual(provider.calls, [])

    async def test_start_marks_failed_when_provider_factory_raises(self) -> None:
        store = FakeStore({"igs-1": make_service()})
        runtime = ImageGenerationServiceRuntime(store, lambda: (_ for _ in ()).throw(TypeError("missing argument")))

        with self.assertRaises(TypeError):
            await runtime.start("igs-1")

        service = store.services["igs-1"]
        self.assertEqual(service["state"], "failed")
        self.assertIn("missing argument", service["error"])

    async def test_stop_without_remote_handle_resets_to_stopped(self) -> None:
        service = make_service(state="running")
        service["config"]["startup"] = {"stage": "ready", "progress": 100}
        store = FakeStore({"igs-1": service})
        provider = FakeProvider()
        runtime = ImageGenerationServiceRuntime(store, lambda: provider)

        result = await runtime.stop("igs-1")

        self.assertEqual(result["state"], "stopped")
        self.assertNotIn("remoteRuntime", store.services["igs-1"]["config"])
        self.assertNotIn("startup", store.services["igs-1"]["config"])
        self.assertEqual(provider.stop_persisted_calls, [])

    async def test_stop_releases_persisted_instance_handle(self) -> None:
        store = FakeStore({"igs-1": make_service(state="running", with_remote=True)})
        provider = FakeProvider()
        runtime = ImageGenerationServiceRuntime(store, lambda: provider)

        result = await runtime.stop("igs-1")

        self.assertEqual(result["state"], "stopped")
        self.assertNotIn("remoteRuntime", store.services["igs-1"]["config"])
        self.assertEqual(provider.stop_persisted_calls, [{"instanceId": "uhost-held-1"}])

    async def test_stop_release_failure_keeps_handle_and_marks_failed(self) -> None:
        store = FakeStore({"igs-1": make_service(state="running", with_remote=True)})
        provider = FakeProvider(stop_report={"terminated": False, "error": "优云接口超时"})
        runtime = ImageGenerationServiceRuntime(store, lambda: provider)

        with self.assertRaisesRegex(RuntimeError, "优云接口超时"):
            await runtime.stop("igs-1")

        service = store.services["igs-1"]
        self.assertEqual(service["state"], "failed")
        self.assertIn("remoteRuntime", service["config"])
        self.assertTrue(any("释放失败" in line for line in store.logs("igs-1")))


class StartupRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_resets_orphaned_states_after_restart(self) -> None:
        services = {
            "igs-starting": make_service("igs-starting") | {"id": "igs-starting", "state": "starting"},
            "igs-recovering": make_service() | {"id": "igs-recovering", "state": "recovering"},
            "igs-stopping-clean": make_service() | {"id": "igs-stopping-clean", "state": "stopping"},
            "igs-stopping-held": make_service(with_remote=True) | {"id": "igs-stopping-held", "state": "stopping"},
            "igs-running": make_service(with_remote=True) | {"id": "igs-running", "state": "running"},
        }
        store = FakeStore(services)
        runtime = ImageGenerationServiceRuntime(store, FakeProvider)

        await runtime.startup()

        self.assertEqual(store.services["igs-starting"]["state"], "failed")
        self.assertIn("后端重启时中断", store.services["igs-starting"]["error"])
        self.assertEqual(store.services["igs-recovering"]["state"], "failed")
        self.assertIn("恢复进程在后端重启时中断", store.services["igs-recovering"]["error"])
        self.assertEqual(store.services["igs-stopping-clean"]["state"], "stopped")
        self.assertEqual(store.services["igs-stopping-held"]["state"], "failed")
        self.assertIn("remoteRuntime", store.services["igs-stopping-held"]["config"])
        self.assertEqual(store.services["igs-running"]["state"], "stopped")
        self.assertNotIn("remoteRuntime", store.services["igs-running"]["config"])
        self.assertTrue(any("对账" in line for line in store.logs("igs-running")))
        self.assertTrue(any("中断" in line for line in store.logs("igs-starting")))

    async def test_startup_reconciles_running_service_without_remote(self) -> None:
        service = make_service() | {"id": "igs-local", "state": "running"}
        store = FakeStore({"igs-local": service})
        runtime = ImageGenerationServiceRuntime(store, FakeProvider)

        await runtime.startup()

        self.assertEqual(store.services["igs-local"]["state"], "stopped")
        self.assertNotIn("remoteRuntime", store.services["igs-local"]["config"])
        self.assertTrue(any("对账" in line for line in store.logs("igs-local")))

    async def test_startup_running_release_failure_keeps_handle(self) -> None:
        store = FakeStore({"igs-1": make_service(state="running", with_remote=True)})
        provider = FakeProvider(stop_report={"terminated": False, "error": "优云接口超时"})
        runtime = ImageGenerationServiceRuntime(store, lambda: provider)

        await runtime.startup()

        service = store.services["igs-1"]
        self.assertEqual(service["state"], "failed")
        self.assertIn("remoteRuntime", service["config"])
        self.assertTrue(any("对账" in line for line in store.logs("igs-1")))

    async def test_startup_ignores_other_workloads(self) -> None:
        other = make_service("starting")
        other["id"] = "svc-text"
        other["config"] = {"workloadType": "vision_language"}
        store = FakeStore({"svc-text": other})
        runtime = ImageGenerationServiceRuntime(store, FakeProvider)

        await runtime.startup()

        self.assertEqual(store.services["svc-text"]["state"], "starting")


if __name__ == "__main__":
    unittest.main()
