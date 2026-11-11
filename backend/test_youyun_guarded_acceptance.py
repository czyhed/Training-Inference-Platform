from __future__ import annotations

import asyncio
import unittest

from scripts.verify_youyun_guarded_acceptance import guarded_acceptance, preflight_summary


class FakeManager:
    def __init__(self) -> None:
        self.cleaned: list[str] = []

    def cleanup_after_task(self, instance_id: str, *, stop_first: bool = True) -> dict[str, object]:
        self.cleaned.append(instance_id)
        return {"terminated": True, "error": None, "attempts": 2, "checkedAt": "2026-09-01T16:00:00+0800"}


class GuardedAcceptanceTest(unittest.IsolatedAsyncioTestCase):
    async def test_empty_preflight_reports_no_live_instances(self) -> None:
        result = preflight_summary([])
        self.assertEqual(result["liveCount"], 0)
        self.assertTrue(result["instanceVerifiedAbsent"])

    async def test_timeout_cleans_instance_registered_before_operation_returns(self) -> None:
        manager = FakeManager()
        created: dict[str, str] = {}

        async def operation() -> None:
            created["instanceId"] = "uhost-timeout"
            await asyncio.sleep(0.05)

        result = await guarded_acceptance(
            manager,  # type: ignore[arg-type]
            operation,
            max_seconds=0.001,
            instance_id_getter=lambda: created.get("instanceId"),
        )

        self.assertEqual(result["failureCode"], "resource_timeout")
        self.assertEqual(manager.cleaned, ["uhost-timeout"])
        self.assertEqual(result["cleanupAttempts"], 2)
        self.assertTrue(result["instanceVerifiedAbsent"])


if __name__ == "__main__":
    unittest.main()
