from __future__ import annotations

import unittest

from backend.platform_services import build_service_listing, normalize_platform_service


class PlatformServiceContractTests(unittest.TestCase):
    def test_normalize_service_maps_workload_and_lifecycle_fields(self) -> None:
        item = normalize_platform_service(
            {
                "id": "svc-vision-1",
                "name": "视觉问答",
                "state": "failed",
                "exposure": "offline",
                "model": "Qwen2.5-VL-3B",
                "adapter": "Best Adapter",
                "config": {
                    "workloadType": "vision_language",
                    "provider": "youyun",
                    "resourceState": "released",
                    "cleanupState": "released",
                },
                "failureCode": "HEALTH_CHECK_FAILED",
                "failureMessage": "健康检查失败",
                "suggestedAction": "查看运行日志后重试",
                "lastHeartbeatAt": "2026-09-04T18:00:00+08:00",
            },
            updated_at="2026-09-04T18:01:00+08:00",
        )

        self.assertEqual(item["modality"], "vision")
        self.assertEqual(item["baseModel"], "Qwen2.5-VL-3B")
        self.assertEqual(item["provider"], "youyun")
        self.assertEqual(item["resourceState"], "released")
        self.assertEqual(item["cleanupState"], "released")
        self.assertEqual(item["failureCode"], "HEALTH_CHECK_FAILED")
        self.assertEqual(item["lastHeartbeatAt"], "2026-09-04T18:00:00+08:00")

    def test_listing_filters_modality_state_query_and_pages_stably(self) -> None:
        result = build_service_listing(
            [
                {"id": "svc-1", "name": "文生图服务", "state": "running", "config": {"workloadType": "image_generation"}},
                {"id": "svc-2", "name": "视觉问答", "state": "failed", "config": {"workloadType": "vision_language"}},
                {"id": "svc-3", "name": "文本助手", "state": "running", "config": {"workloadType": "text_generation"}},
            ],
            modality="vision",
            state="failed",
            query="问答",
            page=1,
            page_size=20,
            updated_at="2026-09-04T18:02:00+08:00",
        )

        self.assertEqual(result["total"], 1)
        self.assertFalse(result["hasNext"])
        self.assertEqual(result["items"][0]["id"], "svc-2")
        self.assertEqual(result["items"][0]["updatedAt"], "2026-09-04T18:02:00+08:00")


if __name__ == "__main__":
    unittest.main()
