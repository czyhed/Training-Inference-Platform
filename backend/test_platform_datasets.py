from __future__ import annotations

import unittest

from backend.platform_datasets import build_dataset_listing


class PlatformDatasetListingTest(unittest.TestCase):
    def test_normalizes_mixed_domains_and_applies_filters(self) -> None:
        result = build_dataset_listing(
            [
                {"id": "ds-v", "name": "视觉", "type": "视觉理解", "structure": "IMAGE_TEXT", "state": "published"},
                {"id": "ds-i", "name": "图像", "type": "文生图", "structure": "IMAGE_TEXT", "state": "ready"},
                {"id": "ds-n", "name": "文本", "type": "文本生成", "structure": "SFT", "state": "failed"},
            ],
            domain="vision",
            query="视觉",
            updated_at="2026-09-04T12:00:00+08:00",
        )

        self.assertEqual(result["total"], 1)
        self.assertFalse(result["hasNext"])
        item = result["items"][0]
        self.assertEqual(item["domain"], "vision")
        self.assertEqual(item["modality"], "IMAGE_TEXT")
        self.assertEqual(item["validationStatus"], "published")
        self.assertEqual(item["version"], 1)
        self.assertEqual(result["updatedAt"], "2026-09-04T12:00:00+08:00")

    def test_paginates_and_clamps_page_size(self) -> None:
        result = build_dataset_listing(
            [{"id": f"ds-{index}", "name": str(index), "type": "文本生成", "state": "ready"} for index in range(3)],
            page=2,
            page_size=500,
            updated_at="2026-09-04T12:00:00+08:00",
        )
        self.assertEqual(result["pageSize"], 100)
        self.assertEqual(result["page"], 2)
        self.assertEqual(result["items"], [])
        self.assertFalse(result["hasNext"])


if __name__ == "__main__":
    unittest.main()
