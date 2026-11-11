from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from backend.image_generation_training import (
    materialize_image_generation_training_bundle,
    resolve_sdxl_lora_entrypoint,
    validate_image_generation_training_dataset,
)


def make_image_dataset(root: Path, names: list[str]) -> Path:
    images = root / "images"
    images.mkdir(parents=True)
    records = []
    for index, name in enumerate(names):
        Image.new("RGB", (32 + index, 32 + index), (10, 20, 30)).save(images / name)
        records.append({"file_name": f"images/{name}", "caption": f"caption {index}"})
    manifest = root / "metadata.jsonl"
    manifest.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")
    return root


class ImageGenerationTrainingTests(unittest.TestCase):
    def test_materialize_caption_bundle_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_image_dataset(root / "source", ["a.png", "b.png", "c.png"])
            kwargs = {
                "dataset_id": "ds-1",
                "media_root": source / "images",
                "manifest_path": source / "metadata.jsonl",
                "model_repo_id": "stabilityai/stable-diffusion-xl-base-1.0",
                "resolution": 1024,
                "epochs": 1,
                "max_train_steps": 3,
                "learning_rate": 1e-4,
                "lora_rank": 16,
                "batch_size": 1,
                "gradient_accumulation": 1,
                "validation_split": 33,
                "seed": 7,
            }
            first = materialize_image_generation_training_bundle(destination=root / "one", **kwargs)
            second = materialize_image_generation_training_bundle(destination=root / "two", **kwargs)
            self.assertEqual(first["manifest"]["split"], second["manifest"]["split"])
            self.assertEqual(
                (root / "one" / "train.jsonl").read_text(),
                (root / "two" / "train.jsonl").read_text(),
            )
            self.assertTrue((root / "one" / "dataset" / "dataset_info.json").is_file())
            self.assertTrue((root / "one" / "output").is_dir())
            info = json.loads((root / "one" / "dataset" / "dataset_info.json").read_text(encoding="utf-8"))
            self.assertEqual(info["image_column"], "image")

    def test_validation_rejects_path_escape_and_empty_caption(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            images = root / "images"
            images.mkdir(parents=True)
            Image.new("RGB", (8, 8), (1, 2, 3)).save(images / "ok.png")
            manifest = root / "metadata.jsonl"
            manifest.write_text(json.dumps({"file_name": "../ok.png", "caption": "x"}) + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                validate_image_generation_training_dataset(manifest, images)
            manifest.write_text(json.dumps({"file_name": "images/ok.png", "caption": "  "}) + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                validate_image_generation_training_dataset(manifest, images)

    def test_sdxl_entrypoint_requires_sdxl_repo(self) -> None:
        self.assertEqual(
            resolve_sdxl_lora_entrypoint("stabilityai/stable-diffusion-xl-base-1.0"),
            "train_text_to_image_lora_sdxl.py",
        )
        with self.assertRaises(ValueError):
            resolve_sdxl_lora_entrypoint("Qwen/Qwen2.5-7B-Instruct")


if __name__ == "__main__":
    unittest.main()
