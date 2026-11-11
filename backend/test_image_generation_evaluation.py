from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from backend.image_generation_evaluation import (
    list_image_generation_evaluation_datasets,
    validate_image_generation_evaluation_dataset,
)


class FakeStore:
    def __init__(self, datasets: list[dict]) -> None:
        self.datasets = datasets

    def list_datasets(self):
        return self.datasets


class ImageGenerationEvaluationDatasetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.images = self.root / "images"
        self.images.mkdir()
        Image.new("RGB", (32, 24), "white").save(self.images / "one.png")
        Image.new("RGB", (16, 16), "black").save(self.images / "two.png")
        self.manifest = self.root / "metadata.jsonl"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def dataset(self, dataset_id: str, *, state: str = "published", dataset_type: str = "文生图") -> dict:
        return {"id": dataset_id, "name": dataset_id, "state": state, "type": dataset_type, "structure": "IMAGE_TEXT", "paths": {"manifestPath": str(self.manifest), "mediaRoot": str(self.images)}}

    def test_catalog_only_lists_published_image_generation_datasets(self) -> None:
        self.manifest.write_text('{"file_name":"one.png","caption":"one"}\n', encoding="utf-8")
        store = FakeStore([self.dataset("ds-image-published"), self.dataset("ds-draft", state="ready"), self.dataset("ds-vision", dataset_type="视觉理解")])
        items = list_image_generation_evaluation_datasets(store)
        self.assertEqual([item["id"] for item in items], ["ds-image-published"])
        self.assertTrue(items[0]["valid"])
        self.assertEqual(items[0]["sampleCount"], 1)

    def test_validation_rejects_missing_caption_and_reference_image(self) -> None:
        self.manifest.write_text('{"file_name":"missing.png","caption":""}\n', encoding="utf-8")
        result = validate_image_generation_evaluation_dataset(FakeStore([self.dataset("ds-broken")]), "ds-broken")
        self.assertFalse(result["valid"])
        self.assertEqual(result["missingCaptions"], 1)
        self.assertEqual(result["missingImages"], 1)
        self.assertEqual(result["issues"][0]["sampleId"], "sample-1")

    def test_validation_respects_max_samples_and_rejects_path_escape(self) -> None:
        self.manifest.write_text('{"file_name":"one.png","caption":"one"}\n{"file_name":"../outside.png","caption":"two"}\n', encoding="utf-8")
        store = FakeStore([self.dataset("ds-samples")])
        limited = validate_image_generation_evaluation_dataset(store, "ds-samples", max_samples=1)
        self.assertTrue(limited["valid"])
        self.assertEqual(limited["sampleCount"], 1)
        escaped = validate_image_generation_evaluation_dataset(store, "ds-samples")
        self.assertFalse(escaped["valid"])
        self.assertEqual(escaped["missingImages"], 1)

    def test_validation_rejects_unpublished_or_wrong_type_dataset(self) -> None:
        self.manifest.write_text('{"file_name":"one.png","caption":"one"}\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "已发布"):
            validate_image_generation_evaluation_dataset(FakeStore([self.dataset("ds-draft", state="ready")]), "ds-draft")
        with self.assertRaisesRegex(ValueError, "文生图"):
            validate_image_generation_evaluation_dataset(FakeStore([self.dataset("ds-vision", dataset_type="视觉理解")]), "ds-vision")


if __name__ == "__main__":
    unittest.main()
