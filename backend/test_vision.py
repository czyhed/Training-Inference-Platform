from __future__ import annotations

import base64
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from backend.vision import (
    answer_metrics,
    image_data_url,
    probe_model,
    validate_extracted_image_text_dataset,
    validate_image_bytes,
    validate_visual_understanding_bundle,
    is_youyun_model_available,
)


class VisionUtilityTests(unittest.TestCase):
    def test_visual_model_availability_requires_a_real_public_mount_path(self) -> None:
        self.assertFalse(is_youyun_model_available({"repoId": "Qwen/Qwen2.5-VL-3B-Instruct"}))
        self.assertTrue(is_youyun_model_available({"repoId": "Qwen/Qwen2.5-VL-3B-Instruct", "remotePath": "/model/ModelScope/Qwen/Qwen2.5-VL-3B-Instruct"}))

    def test_probe_rejects_text_model(self) -> None:
        result = probe_model("Qwen/Qwen2.5-3B-Instruct", None)
        self.assertFalse(result["supported"])
        self.assertEqual(result["status"], "unsupported")

    def test_probe_accepts_visual_repo_hint(self) -> None:
        result = probe_model("Qwen/Qwen2.5-VL-3B-Instruct", None)
        self.assertFalse(result["supported"])
        self.assertEqual(result["status"], "candidate")
        self.assertIn("vision_language", result["capabilities"])

    def test_youyun_catalog_model_requires_a_real_remote_path(self) -> None:
        model = {
            "repoId": "Qwen/Qwen3-VL-32B-Thinking-FP8",
            "capabilities": ["vision_language"],
            "youyunCatalog": True,
            "availableOnYouyun": False,
            "remotePath": "",
        }
        self.assertFalse(is_youyun_model_available(model))

        model["availableOnYouyun"] = True
        model["remotePath"] = "/model/ModelScope/Qwen/Qwen3-VL-32B-Thinking-FP8"
        self.assertTrue(is_youyun_model_available(model))

    def test_legacy_model_with_catalog_remote_path_is_available_without_repo_allowlist(self) -> None:
        model = {
            "repoId": "Qwen/Qwen2.5-VL-3B-Instruct",
            "capabilities": ["vision_language", "qlora_training"],
            "source": "local_registration",
            "youyunCatalog": False,
            "availableOnYouyun": True,
            "remotePath": "/model/ModelScope/Qwen/Qwen2.5-VL-3B-Instruct",
        }
        self.assertTrue(is_youyun_model_available(model))

    def test_image_signature_and_data_url(self) -> None:
        data = b"\x89PNG\r\n\x1a\n" + b"0" * 20
        digest = validate_image_bytes(data, "sample.png")
        self.assertEqual(len(digest), 64)
        url = image_data_url(data, "sample.png")
        self.assertTrue(url.startswith("data:image/png;base64,"))
        self.assertEqual(base64.b64decode(url.split(",", 1)[1]), data)

    def test_image_extension_mismatch_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_image_bytes(b"not-a-png", "sample.png")

    def test_answer_metrics(self) -> None:
        exact = answer_metrics("红色汽车", "红色汽车")
        partial = answer_metrics("红色汽车", "蓝色汽车")
        self.assertEqual(exact["exactMatch"], 1.0)
        self.assertEqual(exact["tokenF1"], 1.0)
        self.assertLess(partial["tokenF1"], 1.0)

    def test_visual_probe_reads_local_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            path.write_text(json.dumps({"vision_config": {"model_type": "vision"}}), encoding="utf-8")
            result = probe_model("internal/custom-model", temp)
            self.assertTrue(result["supported"])
            self.assertIn("image_understanding", result["capabilities"])

    def test_visual_bundle_is_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "dataset.zip"
            target = root / "dataset"
            target.mkdir()
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("images/sample.png", b"\x89PNG\r\n\x1a\n" + b"image")
                bundle.writestr(
                    "metadata.jsonl",
                    json.dumps(
                        {
                            "file_name": "images/sample.png",
                            "question": "图片里有什么？",
                            "answer": "测试图片",
                        },
                        ensure_ascii=False,
                    )
                    + "\n",
                )
            count, error, warnings = validate_visual_understanding_bundle(archive, target)
            self.assertEqual(count, 1)
            self.assertIsNone(error)
            self.assertEqual(warnings, [])
            record = json.loads((target / "train.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(record["image"], "images/sample.png")
            self.assertEqual(record["answer"], "测试图片")

    def test_visual_bundle_keeps_dpo_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "dataset.zip"
            target = root / "dataset"
            target.mkdir()
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("images/sample.png", b"\x89PNG\r\n\x1a\n" + b"image")
                bundle.writestr(
                    "metadata.jsonl",
                    json.dumps(
                        {
                            "file_name": "images/sample.png",
                            "question": "图片里有什么？",
                            "chosen": "图片中有一辆汽车。",
                            "rejected": "图片内容无法判断。",
                        },
                        ensure_ascii=False,
                    )
                    + "\n",
                )
            count, error, warnings = validate_visual_understanding_bundle(archive, target)
            self.assertEqual(count, 1)
            self.assertIsNone(error)
            self.assertEqual(warnings, [])
            record = json.loads((target / "train.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(record["chosen"], "图片中有一辆汽车。")
            self.assertEqual(record["rejected"], "图片内容无法判断。")

    def test_visual_bundle_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "dataset.zip"
            target = root / "dataset"
            target.mkdir()
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("../escape.png", b"\x89PNG\r\n\x1a\n")
                bundle.writestr("metadata.jsonl", "{}\n")
            count, error, _ = validate_visual_understanding_bundle(archive, target)
            self.assertEqual(count, 0)
            self.assertIn("不安全路径", error or "")
            self.assertFalse((root / "escape.png").exists())


class ExtractedImageTextDatasetValidationTest(unittest.TestCase):
    def test_accepts_visual_flavor_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "images").mkdir()
            (root / "images" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            (root / "images" / "b.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            (root / "metadata.jsonl").write_text(
                json.dumps({"file_name": "images/a.png", "question": "颜色", "answer": "绿色"}, ensure_ascii=False)
                + "\n"
                + json.dumps(
                    {
                        "file_name": "images/b.png",
                        "question": "内容",
                        "chosen": "一辆车",
                        "rejected": "无法判断",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            count, error, warnings = validate_extracted_image_text_dataset(root)
            self.assertEqual(count, 2)
            self.assertIsNone(error)
            self.assertEqual(warnings, [])

    def test_accepts_caption_flavor_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "images").mkdir()
            (root / "images" / "cat.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            (root / "metadata.jsonl").write_text(
                json.dumps({"file_name": "images/cat.png", "caption": "一只猫"}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            count, error, warnings = validate_extracted_image_text_dataset(root)
            self.assertEqual(count, 1)
            self.assertIsNone(error)

    def test_reports_missing_referenced_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "images").mkdir()
            (root / "images" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            (root / "metadata.jsonl").write_text(
                json.dumps({"file_name": "images/ghost.png", "question": "颜色", "answer": "红"}, ensure_ascii=False)
                + "\n",
                encoding="utf-8",
            )
            count, error, _ = validate_extracted_image_text_dataset(root)
            self.assertEqual(count, 0)
            self.assertIn("不存在", error or "")

    def test_reports_blank_caption(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "images").mkdir()
            (root / "images" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            (root / "metadata.jsonl").write_text(
                json.dumps({"file_name": "images/a.png", "caption": "  "}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            count, error, _ = validate_extracted_image_text_dataset(root)
            self.assertEqual(count, 0)
            self.assertIn("caption", error or "")

    def test_reports_missing_manifest_or_images(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            count, error, _ = validate_extracted_image_text_dataset(root)
            self.assertEqual(count, 0)
            self.assertIn("metadata.jsonl", error or "")
            (root / "metadata.jsonl").write_text("", encoding="utf-8")
            count, error, _ = validate_extracted_image_text_dataset(root)
            self.assertIn("images", error or "")


if __name__ == "__main__":
    unittest.main()
