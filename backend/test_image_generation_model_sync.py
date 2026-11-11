from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.image_generation_model_sync import sync_image_generation_adapters


class FakeStore:
    def __init__(self) -> None:
        self.rows = {}

    def ensure_base_model(self, repo_id, *, path, source):
        return {"id": "base-sdxl", "name": "SDXL"}

    def upsert_trained_model_asset(self, **kwargs):
        self.rows[kwargs["task_id"]] = kwargs
        return kwargs["variants"]


class ImageGenerationModelSyncTests(unittest.TestCase):
    def test_sync_is_idempotent_and_marks_invalid_adapter_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            best = root / "best"; best.mkdir()
            (best / "adapter_model.safetensors").write_bytes(b"x")
            task = {"id": "igt-1", "state": "completed", "modelRepoId": "stabilityai/stable-diffusion-xl-base-1.0", "artifacts": [{"type": "adapter", "variant": "best", "path": str(best), "selected": True}, {"type": "adapter", "variant": "final", "path": str(root / "missing")}]}
            store = FakeStore()
            first = sync_image_generation_adapters(store, task)
            second = sync_image_generation_adapters(store, task)
            self.assertEqual(first, second)
            self.assertEqual({item["status"] for item in first}, {"available", "unavailable"})
            self.assertEqual(len(store.rows), 1)


if __name__ == "__main__":
    unittest.main()
