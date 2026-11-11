from __future__ import annotations

import unittest

from backend.platform_contracts import (
    normalize_task_summary,
    serialize_platform_error,
)


class PlatformContractTests(unittest.TestCase):
    def test_normalize_task_summary_converts_legacy_fields_and_preserves_missing_progress(self) -> None:
        payload = {
            "id": "vt-123",
            "task_type": "SFT",
            "name": "视觉任务",
            "state": "running",
            "phase": "training",
            "progress": None,
            "resource_state": "allocated",
            "cleanup_state": "not_required",
            "failure_code": None,
            "updated_at": "2026-09-04T20:00:00+08:00",
        }

        result = normalize_task_summary(payload)

        self.assertEqual(result["id"], "vt-123")
        self.assertEqual(result["modality"], "vision")
        self.assertIsNone(result["progress"])
        self.assertEqual(result["resourceState"], "allocated")
        self.assertEqual(result["cleanupState"], "not_required")
        self.assertEqual(result["updatedAt"], "2026-09-04T20:00:00+08:00")

    def test_serialize_platform_error_redacts_sensitive_details(self) -> None:
        result = serialize_platform_error(
            "YOUYUN_SERVICE_UNAVAILABLE",
            "优云服务暂时不可用",
            {
                "stage": "provisioning",
                "private_key": "secret-value",
                "command": "ssh root@example",
            },
            retryable=True,
        )

        self.assertEqual(result["code"], "YOUYUN_SERVICE_UNAVAILABLE")
        self.assertTrue(result["retryable"])
        self.assertEqual(result["details"], [{"stage": "provisioning"}])
        self.assertNotIn("secret-value", str(result))
        self.assertNotIn("ssh root@example", str(result))


if __name__ == "__main__":
    unittest.main()
