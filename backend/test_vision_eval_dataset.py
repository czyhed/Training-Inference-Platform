from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.app import validate_visual_understanding_bundle
from scripts.validate_vision_eval_dataset import build_manifest, validate_dataset


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"test-image"


def write_record(root: Path, record: dict) -> Path:
    metadata_path = root / "metadata.jsonl"
    metadata_path.write_text(
        json.dumps(record, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return metadata_path


class VisionEvalDatasetTest(unittest.TestCase):
    def test_valid_reviewed_dataset_builds_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "images" / "sample.png"
            image_path.parent.mkdir()
            image_path.write_bytes(PNG_BYTES)
            metadata_path = write_record(
                root,
                {
                    "sample_id": "eval-1",
                    "group_id": "group-1",
                    "file_name": "images/sample.png",
                    "task_type": "visual_question_answering",
                    "domain": "通用",
                    "difficulty": "easy",
                    "question": "图片中有什么？",
                    "answer": "一张测试图片。",
                    "expected_format": "text",
                    "review_status": "reviewed",
                },
            )

            report = validate_dataset(
                root,
                metadata_path,
                reference_metadata=[],
                allow_unreviewed=False,
            )

            self.assertTrue(report.ok, report.errors)
            manifest = build_manifest(root, metadata_path, report)
            self.assertEqual(manifest["counts"]["samples"], 1)
            self.assertEqual(manifest["counts"]["images"], 1)
            self.assertEqual(manifest["state"], "ready")
            self.assertEqual(len(manifest["files"]["dataset_sha256"]), 64)

    def test_placeholder_and_invalid_json_answer_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "images" / "sample.png"
            image_path.parent.mkdir()
            image_path.write_bytes(PNG_BYTES)
            metadata_path = write_record(
                root,
                {
                    "sample_id": "eval-1",
                    "group_id": "group-1",
                    "file_name": "images/sample.png",
                    "task_type": "structured_extraction",
                    "domain": "工业",
                    "difficulty": "hard",
                    "question": "输出 JSON。",
                    "answer": "待填写：JSON",
                    "expected_format": "json",
                    "review_status": "reviewed",
                },
            )

            report = validate_dataset(
                root,
                metadata_path,
                reference_metadata=[],
                allow_unreviewed=False,
            )

            self.assertFalse(report.ok)
            self.assertTrue(any("占位内容" in item for item in report.errors))
            self.assertTrue(any("不是合法 JSON" in item for item in report.errors))

    def test_reference_dataset_detects_group_and_image_leakage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            reference_root = base / "reference"
            eval_root = base / "eval"
            for root in (reference_root, eval_root):
                (root / "images").mkdir(parents=True)
                (root / "images" / "sample.png").write_bytes(PNG_BYTES)

            reference_metadata = write_record(
                reference_root,
                {
                    "sample_id": "train-1",
                    "group_id": "shared-group",
                    "file_name": "images/sample.png",
                },
            )
            eval_metadata = write_record(
                eval_root,
                {
                    "sample_id": "eval-1",
                    "group_id": "shared-group",
                    "file_name": "images/sample.png",
                    "task_type": "ocr",
                    "domain": "文档",
                    "difficulty": "medium",
                    "question": "识别文字。",
                    "answer": "测试",
                    "expected_format": "text",
                    "review_status": "reviewed",
                },
            )

            report = validate_dataset(
                eval_root,
                eval_metadata,
                reference_metadata=[reference_metadata],
                allow_unreviewed=False,
            )

            self.assertFalse(report.ok)
            self.assertTrue(any("group_id 与参考数据集重叠" in item for item in report.errors))
            self.assertTrue(any("图片与参考数据集重复" in item for item in report.errors))

    def test_visual_bundle_rejects_images_outside_images_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "bundle.zip"
            output_root = root / "output"
            output_root.mkdir()
            import zipfile

            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("outside.png", PNG_BYTES)
                archive.writestr(
                    "metadata.jsonl",
                    json.dumps(
                        {
                            "file_name": "images/outside.png",
                            "question": "图片中有什么？",
                            "answer": "测试",
                        },
                        ensure_ascii=False,
                    ),
                )

            count, error, warnings = validate_visual_understanding_bundle(
                archive_path, output_root
            )

            self.assertEqual(count, 0)
            self.assertIn("图片必须位于 images/ 目录", error or "")
            self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
