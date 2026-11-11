from __future__ import annotations

import unittest

from backend.platform_tasks import build_task_listing


class PlatformTaskListingTest(unittest.TestCase):
    def test_merges_nlp_vision_and_image_generation_tasks(self) -> None:
        result = build_task_listing(
            nlp_tasks=[{"id": "ft-1", "name": "文本", "state": "completed", "model": "Qwen", "createdAt": "2026-09-04T10:00:00+08:00"}],
            vision_tasks=[{"id": "vt-1", "name": "视觉", "state": "failed", "modelId": "Qwen-VL", "datasetId": "ds-v", "phase": "preparing", "failureCode": "MODEL_PROBE_FAILED", "createdAt": "2026-09-04T11:00:00+08:00"}],
            image_generation_tasks=[{"id": "igt-1", "name": "文生图", "state": "queued", "modelRepoId": "sdxl", "datasetName": "ds-i", "createdAt": "2026-09-04T12:00:00+08:00"}],
            modality="vision",
            updated_at="2026-09-04T12:30:00+08:00",
        )

        self.assertEqual(result["total"], 1)
        item = result["items"][0]
        self.assertEqual(item["modality"], "vision")
        self.assertEqual(item["failureCode"], "MODEL_PROBE_FAILED")
        self.assertEqual(item["baseModel"], "Qwen-VL")
        self.assertEqual(item["updatedAt"], "2026-09-04T12:30:00+08:00")

    def test_paginates_stably_and_preserves_phase_fields(self) -> None:
        result = build_task_listing(
            nlp_tasks=[{"id": "ft-1", "name": "one", "state": "running", "createdAt": "2026-09-04T10:00:00+08:00", "phaseUpdatedAt": "2026-09-04T10:01:00+08:00"}],
            vision_tasks=[],
            image_generation_tasks=[{"id": "igt-1", "name": "two", "state": "queued", "createdAt": "2026-09-04T11:00:00+08:00"}],
            page=2,
            page_size=1,
            updated_at="2026-09-04T12:30:00+08:00",
        )
        self.assertEqual(result["items"][0]["id"], "ft-1")
        self.assertEqual(result["items"][0]["phaseUpdatedAt"], "2026-09-04T10:01:00+08:00")
        self.assertFalse(result["hasNext"])


if __name__ == "__main__":
    unittest.main()
