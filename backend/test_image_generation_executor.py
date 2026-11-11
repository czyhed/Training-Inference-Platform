from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from backend.image_generation_executor import (
    FakeImageGenerationTrainingExecutor,
    build_sdxl_lora_command,
    collect_image_generation_adapters,
    parse_sdxl_training_log,
)


class ImageGenerationExecutorTests(unittest.TestCase):
    def test_command_contains_official_entrypoint_without_shell_text(self) -> None:
        args = build_sdxl_lora_command(
            {
                "model_repo_id": "stabilityai/stable-diffusion-xl-base-1.0",
                "resolution": 1024,
                "train_file": "/work/train.jsonl",
                "output_dir": "/work/output",
            }
        )
        self.assertIn("train_text_to_image_lora_sdxl.py", args)
        self.assertTrue(all("&&" not in item and ";" not in item for item in args))
        self.assertIn("--pretrained_model_name_or_path", args)
        self.assertEqual(args[args.index("--image_column") + 1], "image")
        self.assertEqual(args[args.index("--variant") + 1], "fp16")

    def test_command_uses_resolved_remote_model_path(self) -> None:
        args = build_sdxl_lora_command({
            "model_repo_id": "stabilityai/stable-diffusion-xl-base-1.0",
            "model_path": "/root/models/sdxl",
            "resolution": 1024,
            "train_file": "/work/train.jsonl",
            "output_dir": "/work/output",
        })
        self.assertEqual(args[args.index("--pretrained_model_name_or_path") + 1], "/root/models/sdxl")

    def test_validation_manifest_is_not_used_as_generation_prompt(self) -> None:
        args = build_sdxl_lora_command({
            "model_repo_id": "stabilityai/stable-diffusion-xl-base-1.0",
            "train_file": "/work/train.jsonl",
            "validation_file": "/work/validation.jsonl",
            "output_dir": "/work/output",
        })
        self.assertNotIn("--validation_prompt", args)

    def test_explicit_validation_prompt_is_forwarded(self) -> None:
        args = build_sdxl_lora_command({
            "model_repo_id": "stabilityai/stable-diffusion-xl-base-1.0",
            "train_file": "/work/train.jsonl",
            "validation_prompt": "a small ceramic teapot",
            "output_dir": "/work/output",
        })
        self.assertEqual(args[args.index("--validation_prompt") + 1], "a small ceramic teapot")

    def test_collect_adapters_requires_loadable_weight(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            best = root / "best"
            final = root / "final"
            best.mkdir()
            final.mkdir()
            (best / "pytorch_lora_weights.safetensors").write_bytes(b"best")
            (final / "adapter_model.safetensors").write_bytes(b"final")
            artifacts = collect_image_generation_adapters(root, {"datasetId": "ds-1"})
            self.assertEqual({item["variant"] for item in artifacts}, {"best", "final"})
            self.assertTrue(all(item["type"] == "adapter" for item in artifacts))

    def test_fake_executor_emits_metrics_and_artifacts(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                events = []
                executor = FakeImageGenerationTrainingExecutor()
                result = await executor.run(
                    {"id": "igt-1", "previewRoot": str(root), "outputDir": str(root / "output")},
                    on_event=events.append,
                )
                self.assertEqual(result["status"], "completed")
                self.assertEqual({item["variant"] for item in result["artifacts"]}, {"best", "final"})
                self.assertTrue(any(event.type == "metric" for event in events))

        asyncio.run(scenario())

    def test_parse_sdxl_training_log_deduplicates_steps_and_strips_ansi(self) -> None:
        parsed = parse_sdxl_training_log(
            "\x1b[32mPreparing dataset\x1b[0m\n"
            "Steps:  10%|#         | 1/10 [00:01<00:09, 1.00it/s, lr=0.0001, step_loss=0.5]\r"
            "Steps:  10%|#         | 1/10 [00:01<00:09, 1.00it/s, lr=0.0001, step_loss=0.4]\r"
            "Steps:  20%|##        | 2/10 [00:02<00:08, 1.00it/s, lr=9e-05, step_loss=0.25]\n"
        )

        self.assertEqual(parsed["metrics"], [
            {"step": 1, "loss": 0.4, "learningRate": 0.0001, "progress": 10, "totalSteps": 10},
            {"step": 2, "loss": 0.25, "learningRate": 0.00009, "progress": 20, "totalSteps": 10},
        ])
        self.assertEqual(parsed["logs"], [
            "Preparing dataset",
            "Step 1/10 · Loss 0.400000 · LR 0.0001 · 10%",
            "Step 2/10 · Loss 0.250000 · LR 9e-05 · 20%",
        ])


if __name__ == "__main__":
    unittest.main()
