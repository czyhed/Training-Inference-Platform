"""YouyunInstanceManager 生命周期与清理保证的单元测试（伪造客户端，不发真实请求）。"""
from __future__ import annotations

import unittest

from backend.providers.youyun.client import YouyunClient
from backend.providers.youyun.instance_manager import YouyunInstanceManager, YouyunProvisionCancelled, _decode_password
from backend.providers.youyun.models import GpuSpecCombination, YouyunConfig, YouyunInstance


def _make_instance(state: str = "Running", ip: str | None = "1.2.3.4") -> YouyunInstance:
    return YouyunInstance(
        uhost_id="uhost-1",
        name="cppt-t1",
        state=state,
        region="cn-wlcb",
        zone="cn-wlcb-01",
        gpu=1,
        gpu_type="4090",
        cpu=16,
        memory_mb=65536,
        ip=ip,
        ssh_port=60022,
        ssh_user="root",
        password_b64="cGFzc3dvcmQ=",
        image_id="img",
        os_type="Linux",
        charge_type="Postpay",
        instance_type="UHost",
        raw={},
    )


class FakeClient(YouyunClient):
    def __init__(self, states: list[str] | None = None, specs: list[GpuSpecCombination] | None = None) -> None:  # noqa: super 不校验
        self.config = YouyunConfig(public_key="pk", private_key="sk", region="cn-wlcb", zone="cn-wlcb-01")
        self.states = states or ["Install", "Starting", "Running"]
        self.specs = specs or [
            GpuSpecCombination(1, 16, 64, True),
            GpuSpecCombination(2, 32, 128, True),
            GpuSpecCombination(8, 92, 940, False),
        ]
        self.created: list[dict] = []
        self.deleted: list[str] = []
        self.stopped: list[str] = []
        self.describe_calls = 0
        self.create_error: Exception | None = None

    def check_resource_capacity(self, gpu_type=None, image_id=None, charge_type=None, cpu_platform="Intel/Auto"):  # type: ignore[override]
        from backend.providers.youyun.models import ResourceCapacity

        return ResourceCapacity(gpu_type=gpu_type or "4090", specs=self.specs)

    def create_instance(self, name, gpu, cpu, memory_gb, image_id=None, gpu_type=None, charge_type=None):  # type: ignore[override]
        self.created.append({"name": name, "gpu": gpu, "cpu": cpu, "memory_gb": memory_gb})
        if self.create_error is not None:
            raise self.create_error
        return ["uhost-1"]

    def describe_instance(self, uhost_id):  # type: ignore[override]
        self.describe_calls += 1
        index = min(self.describe_calls - 1, len(self.states) - 1)
        state = self.states[index]
        if state == "Deleted":
            # 真实 API 对已删除实例返回空列表，client 会抛“实例不存在”
            from backend.providers.youyun.models import YouyunApiError

            raise YouyunApiError("DescribeCompShareInstance", 8, "实例不存在")
        return _make_instance(state=state)

    def delete_instance(self, uhost_id):  # type: ignore[override]
        self.deleted.append(uhost_id)
        self.states = ["Deleted"]
        return None

    def stop_instance(self, uhost_id):  # type: ignore[override]
        self.stopped.append(uhost_id)
        self.states = ["Stopped"]
        return None


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 1.0
        return self.now


def _manager(client: FakeClient, **overrides) -> YouyunInstanceManager:
    params = dict(poll_interval=0, boot_timeout=30, terminate_timeout=15, sleep=lambda s: None, clock=_FakeClock())
    params.update(overrides)
    return YouyunInstanceManager(client, **params)


class CreateOneShotTest(unittest.TestCase):
    def test_create_wait_can_be_cancelled_after_provider_returns_instance(self) -> None:
        client = FakeClient(states=["Initializing"])
        manager = _manager(client)
        cancelled = True
        with self.assertRaises(YouyunProvisionCancelled):
            manager.create_one_shot("cancelled", should_cancel=lambda: cancelled)
        self.assertEqual(client.deleted, ["uhost-1"])

    def test_creates_with_smallest_available_spec_and_waits_running(self) -> None:
        client = FakeClient()
        manager = _manager(client)
        instance = manager.create_one_shot("t1")
        self.assertTrue(instance.running)
        self.assertEqual(client.created[0]["gpu"], 1)
        self.assertEqual(client.created[0]["cpu"], 16)
        self.assertEqual(client.created[0]["memory_gb"], 64)
        self.assertEqual(client.created[0]["name"], "cppt-t1")

    def test_respects_explicit_gpu_count(self) -> None:
        client = FakeClient()
        _manager(client).create_one_shot("t1", gpu=2)
        self.assertEqual(client.created[0]["gpu"], 2)

    def test_reports_instance_id_immediately_after_creation(self) -> None:
        client = FakeClient()
        created_ids: list[str] = []
        _manager(client).create_one_shot("t1", on_created=created_ids.append)
        self.assertEqual(created_ids, ["uhost-1"])

    def test_on_created_callback_failure_cleans_created_instance(self) -> None:
        client = FakeClient(states=["Running"])

        def fail_callback(_uhost_id: str) -> None:
            raise RuntimeError("marker persistence failed")

        with self.assertRaisesRegex(RuntimeError, "marker persistence failed"):
            _manager(client).create_one_shot("t1", on_created=fail_callback)

        self.assertEqual(client.deleted, ["uhost-1"])

    def test_no_inventory_raises(self) -> None:
        client = FakeClient(specs=[GpuSpecCombination(8, 92, 940, False)])
        with self.assertRaises(Exception) as ctx:
            _manager(client).create_one_shot("t1")
        self.assertIn("无可用规格库存", str(ctx.exception))

    def test_no_inventory_error_uses_selected_zone(self) -> None:
        client = FakeClient(specs=[GpuSpecCombination(8, 92, 940, False)])

        def capacity(*, gpu_type=None, image_id=None, charge_type=None, region=None, zone=None, **kwargs):
            from backend.providers.youyun.models import ResourceCapacity

            return ResourceCapacity(gpu_type=gpu_type or "4090", specs=client.specs)

        client.check_resource_capacity = capacity  # type: ignore[method-assign]
        with self.assertRaises(Exception) as ctx:
            _manager(client).create_one_shot("t1", region="cn-bj2", zone="cn-bj2-03")
        self.assertIn("cn-bj2-03", str(ctx.exception))

    def test_fail_state_raises(self) -> None:
        client = FakeClient(states=["Install", "Fail"])
        with self.assertRaises(Exception) as ctx:
            _manager(client).create_one_shot("t1")
        self.assertIn("异常状态", str(ctx.exception))

    def test_same_idempotency_key_reuses_active_instance(self) -> None:
        client = FakeClient(states=["Running"])
        manager = _manager(client)
        first = manager.create_one_shot("t1", idempotency_key="t1:probe")
        second = manager.create_one_shot("t1", idempotency_key="t1:probe")
        self.assertEqual(first.uhost_id, second.uhost_id)
        self.assertEqual(len(client.created), 1)

    def test_recovers_instance_created_before_transient_create_error(self) -> None:
        from backend.providers.youyun.models import YouyunApiError

        client = FakeClient(states=["Running"])
        client.create_error = YouyunApiError("CreateCompShareInstance", 8433, "Service error")

        def describe_instances(uhost_ids=None):
            if uhost_ids:
                return []
            return [_make_instance(state="Running")]

        client.describe_instances = describe_instances  # type: ignore[method-assign]
        instance = _manager(client).create_one_shot("t1")

        self.assertEqual(instance.uhost_id, "uhost-1")

    def test_retries_service_unavailable_create_error_before_failing(self) -> None:
        from backend.providers.youyun.models import YouyunApiError

        client = FakeClient(states=["Running"])
        create_calls = 0

        def create_instance(*args, **kwargs):
            nonlocal create_calls
            create_calls += 1
            if create_calls == 1:
                raise YouyunApiError("CreateCompShareInstance", 226645, "Service unavailable")
            return ["uhost-1"]

        client.create_instance = create_instance  # type: ignore[method-assign]
        client.describe_instances = lambda uhost_ids=None: []  # type: ignore[method-assign]
        instance = _manager(client).create_one_shot("t1")

        self.assertEqual(instance.uhost_id, "uhost-1")
        self.assertEqual(create_calls, 2)

    def test_forwards_selected_region_and_zone_to_capacity_and_create(self) -> None:
        client = FakeClient(states=["Running"])
        seen: dict[str, dict] = {}

        def capacity(*, gpu_type=None, image_id=None, charge_type=None, region=None, zone=None, **kwargs):
            seen["capacity"] = {"region": region, "zone": zone}
            from backend.providers.youyun.models import ResourceCapacity

            return ResourceCapacity(gpu_type=gpu_type or "4090", specs=client.specs)

        def create(*args, **kwargs):
            seen["create"] = {"region": kwargs.get("region"), "zone": kwargs.get("zone")}
            return ["uhost-1"]

        def describe(uhost_id, **kwargs):
            seen["describe"] = {"region": kwargs.get("region"), "zone": kwargs.get("zone")}
            return _make_instance(state="Running")

        client.check_resource_capacity = capacity  # type: ignore[method-assign]
        client.create_instance = create  # type: ignore[method-assign]
        client.describe_instance = describe  # type: ignore[method-assign]
        _manager(client).create_one_shot(
            "t1",
            region="cn-sh2",
            zone="cn-sh2-02",
        )

        self.assertEqual(seen["capacity"], {"region": "cn-sh2", "zone": "cn-sh2-02"})
        self.assertEqual(seen["create"], {"region": "cn-sh2", "zone": "cn-sh2-02"})
        self.assertEqual(seen["describe"], {"region": "cn-sh2", "zone": "cn-sh2-02"})

    def test_cleanup_forwards_selected_region_and_zone(self) -> None:
        client = FakeClient(states=["Stopped"])
        seen: dict[str, dict] = {}
        deleted = False

        def describe(uhost_id, **kwargs):
            if deleted:
                return _make_instance(state="Deleted")
            seen["describe"] = {"region": kwargs.get("region"), "zone": kwargs.get("zone")}
            return _make_instance(state="Stopped")

        def delete(uhost_id, **kwargs):
            nonlocal deleted
            deleted = True
            seen["delete"] = {"region": kwargs.get("region"), "zone": kwargs.get("zone")}

        client.describe_instance = describe  # type: ignore[method-assign]
        client.delete_instance = delete  # type: ignore[method-assign]
        client.describe_instances = lambda uhost_ids=None: []  # type: ignore[method-assign]
        manager = _manager(client)
        report = manager.cleanup_after_task("uhost-1", region="cn-sh2", zone="cn-sh2-02", stop_first=False)

        self.assertTrue(report["terminated"])
        self.assertEqual(seen["describe"], {"region": "cn-sh2", "zone": "cn-sh2-02"})
        self.assertEqual(seen["delete"], {"region": "cn-sh2", "zone": "cn-sh2-02"})


class CleanupTest(unittest.TestCase):
    def test_cleanup_orphaned_instances_only_releases_old_platform_instances(self) -> None:
        old_owned = _make_instance(state="Stopped")
        old_owned.name = "cppt-vt-orphan"
        old_owned.raw = {"CreateTime": 1_000}
        active_owned = _make_instance(state="Stopped")
        active_owned.uhost_id = "uhost-active"
        active_owned.name = "cppt-img-train-active"
        active_owned.raw = {"CreateTime": 1_000}
        recent_owned = _make_instance(state="Stopped")
        recent_owned.uhost_id = "uhost-recent"
        recent_owned.name = "cppt-img-eval-recent"
        recent_owned.raw = {"CreateTime": 9_800}
        foreign = _make_instance(state="Stopped")
        foreign.uhost_id = "uhost-foreign"
        foreign.name = "my-personal-gpu"
        foreign.raw = {"CreateTime": 1_000}
        client = FakeClient(states=["Stopped"])
        client.describe_instances = lambda uhost_ids=None: [old_owned, active_owned, recent_owned, foreign]  # type: ignore[method-assign]
        manager = _manager(client, clock=lambda: 10_000)
        reports = manager.cleanup_orphaned_instances(
            active_instance_ids={"uhost-active"},
            grace_seconds=300,
            now=10_000,
        )
        self.assertEqual([item["uhost_id"] for item in reports], ["uhost-1"])
        self.assertEqual(client.deleted, ["uhost-1"])

    def test_cleanup_orphaned_instances_skips_unknown_creation_time(self) -> None:
        orphan = _make_instance(state="Stopped")
        orphan.name = "cppt-vt-unknown"
        client = FakeClient(states=["Stopped"])
        client.describe_instances = lambda uhost_ids=None: [orphan]  # type: ignore[method-assign]
        reports = _manager(client).cleanup_orphaned_instances(active_instance_ids=set(), grace_seconds=0, now=10_000)
        self.assertEqual(reports, [])
        self.assertEqual(client.deleted, [])

    def test_terminate_never_raises_and_reports(self) -> None:
        client = FakeClient(states=["Running", "Deleted"])
        manager = _manager(client)
        report = manager.terminate("uhost-1")
        self.assertTrue(report["terminated"])
        self.assertEqual(client.deleted, ["uhost-1"])
        self.assertGreaterEqual(report["attempts"], 1)
        self.assertTrue(report["checkedAt"])

    def test_terminate_treats_already_absent_instance_as_released(self) -> None:
        client = FakeClient(states=["Deleted"])
        manager = _manager(client)
        report = manager.terminate("uhost-1")
        self.assertTrue(report["terminated"])
        self.assertEqual(client.deleted, ["uhost-1"])

    def test_cleanup_after_task_stops_then_deletes(self) -> None:
        client = FakeClient(states=["Running"])
        manager = _manager(client)
        report = manager.cleanup_after_task("uhost-1")
        self.assertTrue(report["terminated"])
        self.assertEqual(client.stopped, ["uhost-1"])
        self.assertEqual(client.deleted, ["uhost-1"])
        self.assertEqual(report["steps"][0]["step"], "stop")

    def test_cleanup_waits_for_stopped_before_delete(self) -> None:
        client = FakeClient(states=["Running", "Stopped", "Deleted"])
        client.stop_instance = lambda uhost_id: client.stopped.append(uhost_id)  # type: ignore[method-assign]
        manager = _manager(client)
        report = manager.cleanup_after_task("uhost-1")
        self.assertTrue(report["terminated"])
        self.assertGreaterEqual(client.describe_calls, 3)
        self.assertEqual(client.deleted, ["uhost-1"])

    def test_cleanup_retries_stop_after_operation_in_progress(self) -> None:
        client = FakeClient(states=["Running", "Stopped", "Deleted"])
        calls = 0

        def stop_with_conflict(uhost_id):
            nonlocal calls
            calls += 1
            if calls == 1:
                from backend.providers.youyun.models import YouyunApiError

                raise YouyunApiError("StopCompShareInstance", 409, "InstanceOperationInProgress")
            client.stopped.append(uhost_id)

        client.stop_instance = stop_with_conflict  # type: ignore[method-assign]
        report = _manager(client).cleanup_after_task("uhost-1")
        self.assertTrue(report["terminated"])
        self.assertEqual(calls, 2)

    def test_cleanup_after_task_preserves_terminate_diagnostics(self) -> None:
        client = FakeClient(states=["Running"])
        report = _manager(client).cleanup_after_task("uhost-1")
        self.assertGreaterEqual(report["attempts"], 1)
        self.assertTrue(report["checkedAt"])

    def test_terminate_skips_stop_for_shutdown_domain_even_when_state_is_initializing(self) -> None:
        client = FakeClient(states=["Initializing"])

        def describe_instance(uhost_id):
            if client.states[0] == "Deleted":
                from backend.providers.youyun.models import YouyunApiError

                raise YouyunApiError("DescribeCompShareInstance", 8, "实例不存在")
            item = _make_instance(state=client.states[0])
            item.raw = {"UHostDomainSate": "DOMAIN_SHUT_OFF", "StopTime": 123}
            return item

        client.describe_instance = describe_instance  # type: ignore[method-assign]

        def stop_should_not_run(uhost_id):
            from backend.providers.youyun.models import YouyunApiError

            raise YouyunApiError("StopCompShareInstance", 8903, "UHost is in the task")

        client.stop_instance = stop_should_not_run  # type: ignore[method-assign]
        report = _manager(client).terminate("uhost-1")
        self.assertTrue(report["terminated"])
        self.assertEqual(client.deleted, ["uhost-1"])

    def test_delete_failure_recorded_not_raised(self) -> None:
        client = FakeClient(states=["Running"])

        def fail_delete(uhost_id):
            from backend.providers.youyun.models import YouyunApiError

            raise YouyunApiError("TerminateCompShareInstance", 230, "欠费无法删除")

        client.delete_instance = fail_delete  # type: ignore[method-assign]
        report = _manager(client).terminate("uhost-1")
        self.assertFalse(report["terminated"])
        self.assertIn("欠费无法删除", str(report["error"]))

    def test_delete_operation_lock_retries_before_returning_retryable(self) -> None:
        client = FakeClient(states=["Stopped"])

        def pending_delete(uhost_id):
            from backend.providers.youyun.models import YouyunApiError

            raise YouyunApiError("TerminateCompShareInstance", 8903, "UHost is in the task")

        client.delete_instance = pending_delete  # type: ignore[method-assign]
        report = _manager(client, terminate_timeout=120).terminate("uhost-1")
        self.assertFalse(report["terminated"])
        self.assertTrue(report["retryable"])
        self.assertEqual(report["attempts"], 2)
        self.assertEqual(client.describe_calls, 1)

    def test_delete_already_absent_is_confirmed_released(self) -> None:
        client = FakeClient(states=["Stopped"])

        def already_absent(uhost_id):
            from backend.providers.youyun.models import YouyunApiError

            raise YouyunApiError("TerminateCompShareInstance", 8039, f"Resource not exist {uhost_id}")

        client.delete_instance = already_absent  # type: ignore[method-assign]
        report = _manager(client).terminate("uhost-1")
        self.assertTrue(report["terminated"])
        self.assertIsNone(report["error"])

    def test_string_not_found_provider_code_is_confirmed_released(self) -> None:
        client = FakeClient(states=["Stopped"])

        def already_absent(_uhost_id):
            from backend.providers.youyun.models import YouyunApiError

            raise YouyunApiError("TerminateCompShareInstance", "8039", "服务端未返回实例详情")  # type: ignore[arg-type]

        client.delete_instance = already_absent  # type: ignore[method-assign]
        report = _manager(client).terminate("uhost-1")

        self.assertTrue(report["terminated"])
        self.assertIsNone(report["error"])

    def test_describe_already_absent_code_is_confirmed_released(self) -> None:
        client = FakeClient(states=["Running"])

        def describe_absent(_uhost_id):
            from backend.providers.youyun.models import YouyunApiError

            raise YouyunApiError("DescribeCompShareInstance", 8039, "Resource not exist uhost-1")

        client.describe_instance = describe_absent  # type: ignore[method-assign]

        report = _manager(client).terminate("uhost-1")

        self.assertTrue(report["terminated"])
        self.assertIsNone(report["error"])
        self.assertEqual(client.deleted, ["uhost-1"])

    def test_generic_not_found_during_stop_wait_is_treated_as_gone(self) -> None:
        client = FakeClient(states=["Running"])

        def describe_absent(_uhost_id):
            raise RuntimeError("Resource not exist uhost-1")

        client.describe_instance = describe_absent  # type: ignore[method-assign]

        _manager(client)._stop_and_wait("uhost-1")

        self.assertEqual(client.stopped, ["uhost-1"])

    def test_generic_not_found_from_stop_command_is_treated_as_gone(self) -> None:
        client = FakeClient(states=["Running"])

        def stop_absent(_uhost_id):
            raise OSError("实例不存在 uhost-1")

        client.stop_instance = stop_absent  # type: ignore[method-assign]

        _manager(client)._stop_and_wait("uhost-1")

        self.assertEqual(client.stopped, [])

    def test_generic_not_found_during_preflight_is_confirmed_released(self) -> None:
        client = FakeClient(states=["Running"])

        def describe_absent(_uhost_id):
            raise OSError("Resource not exist uhost-1")

        client.describe_instance = describe_absent  # type: ignore[method-assign]

        report = _manager(client).terminate("uhost-1")

        self.assertTrue(report["terminated"])
        self.assertIsNone(report["error"])
        self.assertEqual(client.deleted, ["uhost-1"])

    def test_generic_not_found_after_delete_is_confirmed_released(self) -> None:
        client = FakeClient(states=["Stopped"])
        describe_calls = 0

        def describe_after_delete(_uhost_id):
            nonlocal describe_calls
            describe_calls += 1
            if describe_calls == 1:
                return _make_instance(state="Stopped")
            raise RuntimeError("实例不存在 uhost-1")

        client.describe_instance = describe_after_delete  # type: ignore[method-assign]

        report = _manager(client).terminate("uhost-1")

        self.assertTrue(report["terminated"])
        self.assertIsNone(report["error"])
        self.assertEqual(client.deleted, ["uhost-1"])

    def test_instance_name_between_not_found_words_is_confirmed_released(self) -> None:
        client = FakeClient(states=["Stopped"])
        describe_calls = 0

        def describe_after_delete(_uhost_id):
            nonlocal describe_calls
            describe_calls += 1
            if describe_calls == 1:
                return _make_instance(state="Stopped")
            raise RuntimeError("实例 cpod-1ux6ytu4a2la 不存在")

        client.describe_instance = describe_after_delete  # type: ignore[method-assign]

        report = _manager(client).terminate("cpod-1ux6ytu4a2la")

        self.assertTrue(report["terminated"])
        self.assertIsNone(report["error"])
        self.assertEqual(client.deleted, ["cpod-1ux6ytu4a2la"])

    def test_transient_describe_error_does_not_claim_release(self) -> None:
        client = FakeClient(states=["Stopped"])
        calls = 0

        def describe_transiently_fails(_uhost_id):
            nonlocal calls
            calls += 1
            if calls == 1:
                return _make_instance(state="Stopped")
            raise ConnectionError("temporary provider outage")

        client.describe_instance = describe_transiently_fails  # type: ignore[method-assign]

        report = _manager(client, terminate_timeout=3, cleanup_attempts=1).terminate("uhost-1")

        self.assertFalse(report["terminated"])
        self.assertIn("temporary provider outage", str(report["error"]))
        self.assertEqual(client.deleted, ["uhost-1"])

    def test_stop_wait_treats_absent_instance_as_gone(self) -> None:
        client = FakeClient(states=["Running"])

        def describe_absent(_uhost_id):
            from backend.providers.youyun.models import YouyunApiError

            raise YouyunApiError("DescribeCompShareInstance", 8039, "Resource not exist uhost-1")

        client.describe_instance = describe_absent  # type: ignore[method-assign]

        _manager(client)._stop_and_wait("uhost-1")

        self.assertEqual(client.stopped, ["uhost-1"])


class CredentialTest(unittest.TestCase):
    def test_ssh_credentials_decodes_password(self) -> None:
        manager = _manager(FakeClient())
        creds = manager.ssh_credentials(_make_instance())
        self.assertEqual(creds["password"], "password")
        self.assertEqual(creds["port"], 60022)

    def test_decode_password_invalid_returns_none(self) -> None:
        self.assertIsNone(_decode_password("!!!not-base64!!!"))
        self.assertIsNone(_decode_password(""))


if __name__ == "__main__":
    unittest.main()
