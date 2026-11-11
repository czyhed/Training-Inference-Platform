from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.vision_training import (
    materialize_vision_training_bundle,
    resolve_vision_template,
)


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"vision-training-test"


class VisionTrainingMaterializationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        (self.source / "images").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_dataset(self, records: list[dict[str, str]]) -> Path:
        for name in {record["image"].split("/")[-1] for record in records if record.get("image", "").startswith("images/")}:
            (self.source / "images" / name).write_bytes(PNG_BYTES)
        path = self.source / "train.jsonl"
        path.write_text(
            "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
            encoding="utf-8",
        )
        return path

    def _materialize(self, destination: str = "preview", **overrides):
        values = {
            "dataset_id": "vision-dataset",
            "train_path": self.source / "train.jsonl",
            "destination": self.root / destination,
            "model": "Qwen/Qwen2.5-VL-3B-Instruct",
            "template": "qwen2_vl",
            "tuning_mode": "qlora",
            "image_max_pixels": 401408,
            "batch_size": 1,
            "gradient_accumulation": 8,
            "validation_split": 25,
            "epochs": 2,
            "learning_rate": 2e-5,
            "lora_rank": 64,
            "lora_alpha": 128,
            "lora_dropout": 0.05,
            "gradient_checkpointing": True,
            "seed": 42,
        }
        values.update(overrides)
        return materialize_vision_training_bundle(**values)

    def test_materializes_official_multimodal_sharegpt_contract(self) -> None:
        self._write_dataset(
            [
                {"image": "images/a.png", "question": "图中是什么？", "answer": "A"},
                {"image": "images/b.png", "question": "<image>读出文字", "answer": "B"},
                {"image": "images/c.png", "question": "描述图片", "answer": "C"},
            ]
        )
        result = self._materialize()

        manifest = result["manifest"]
        self.assertEqual(manifest["totalSamples"], 3)
        self.assertEqual(manifest["trainSamples"], 2)
        self.assertEqual(manifest["validationSamples"], 1)
        self.assertEqual(manifest["referencedImages"], 3)
        self.assertEqual(result["config"]["template"], "qwen2_vl")
        self.assertEqual(result["config"]["quantization_bit"], 4)
        self.assertTrue(result["config"]["freeze_vision_tower"])
        self.assertEqual(result["config"]["lora_target"], "all")

        dataset_dir = Path(result["datasetDir"])
        info = json.loads((dataset_dir / "dataset_info.json").read_text(encoding="utf-8"))
        entry = info["vision_vision-dataset"]
        self.assertEqual(entry["formatting"], "sharegpt")
        self.assertEqual(entry["columns"], {"messages": "messages", "images": "images"})
        records = [
            json.loads(line)
            for name in ("vision_train.jsonl", "vision_validation.jsonl")
            for line in (dataset_dir / name).read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(len(records), 3)
        self.assertTrue(all(item["messages"][0]["content"].count("<image>") == 1 for item in records))
        self.assertTrue((dataset_dir / "images" / "a.png").is_file())

    def test_materializes_visual_dpo_contract(self) -> None:
        self._write_dataset(
            [
                {
                    "image": "images/a.png",
                    "question": "图中是什么？",
                    "chosen": "图片中是一辆红色汽车。",
                    "rejected": "图片中没有可识别内容。",
                },
                {
                    "image": "images/b.png",
                    "question": "请读取图片文字。",
                    "chosen": {"role": "assistant", "content": "门牌号是 101。"},
                    "rejected": {"role": "assistant", "content": "无法读取。"},
                },
            ]
        )
        result = self._materialize(task_type="DPO", dpo_beta=0.2, validation_split=0)

        self.assertEqual(result["config"]["stage"], "dpo")
        self.assertEqual(result["config"]["pref_beta"], 0.2)
        self.assertEqual(result["manifest"]["taskType"], "DPO")
        self.assertEqual(result["manifest"]["dpoBeta"], 0.2)
        dataset_dir = Path(result["datasetDir"])
        info = json.loads((dataset_dir / "dataset_info.json").read_text(encoding="utf-8"))
        entry = info["vision_vision-dataset"]
        self.assertTrue(entry["ranking"])
        self.assertEqual(
            entry["columns"],
            {"messages": "messages", "chosen": "chosen", "rejected": "rejected", "images": "images"},
        )
        record = json.loads((dataset_dir / "vision_train.jsonl").read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(len(record["messages"]), 1)
        self.assertEqual(record["chosen"]["role"], "assistant")
        self.assertEqual(record["rejected"]["role"], "assistant")
        self.assertIn(record["images"], [["images/a.png"], ["images/b.png"]])

    def test_dpo_rejects_missing_or_equal_preferences(self) -> None:
        self._write_dataset(
            [{"image": "images/a.png", "question": "问题", "chosen": "好答案"}]
        )
        with self.assertRaisesRegex(ValueError, "rejected"):
            self._materialize(task_type="DPO", validation_split=0)

        self._write_dataset(
            [
                {
                    "image": "images/a.png",
                    "question": "问题",
                    "chosen": "相同答案",
                    "rejected": "相同答案",
                }
            ]
        )
        with self.assertRaisesRegex(ValueError, "不能相同"):
            self._materialize("equal", task_type="DPO", validation_split=0)

    def test_split_is_deterministic(self) -> None:
        self._write_dataset(
            [
                {"image": f"images/{name}.png", "question": name, "answer": name.upper()}
                for name in ("a", "b", "c", "d")
            ]
        )
        first = self._materialize("first")
        second = self._materialize("second")
        first_dir = Path(first["datasetDir"])
        second_dir = Path(second["datasetDir"])
        for name in ("vision_train.jsonl", "vision_validation.jsonl"):
            self.assertEqual(
                (first_dir / name).read_text(encoding="utf-8"),
                (second_dir / name).read_text(encoding="utf-8"),
            )

    def test_rejects_missing_or_traversing_image(self) -> None:
        path = self.source / "train.jsonl"
        path.write_text(
            json.dumps({"image": "images/missing.png", "question": "Q", "answer": "A"}) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "图片不存在"):
            self._materialize()

        path.write_text(
            json.dumps({"image": "../outside.png", "question": "Q", "answer": "A"}) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "不安全路径"):
            self._materialize()

    def test_rejects_multiple_image_placeholders(self) -> None:
        self._write_dataset(
            [{"image": "images/a.png", "question": "<image>A<image>B", "answer": "A"}]
        )
        with self.assertRaisesRegex(ValueError, "只能包含一个"):
            self._materialize(validation_split=0)

    def test_resolves_only_verified_qwen_vl_templates(self) -> None:
        self.assertEqual(resolve_vision_template("Qwen/Qwen2.5-VL-3B-Instruct"), "qwen2_vl")
        self.assertEqual(resolve_vision_template("Qwen/Qwen3-VL-4B-Instruct"), "qwen3_vl_nothink")
        with self.assertRaisesRegex(ValueError, "仅验证了"):
            resolve_vision_template("generic/vision-model")


if __name__ == "__main__":
    unittest.main()
