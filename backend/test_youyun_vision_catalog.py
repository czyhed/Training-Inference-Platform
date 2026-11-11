import unittest
from unittest.mock import patch

from backend.youyun_vision_catalog import parse_youyun_vision_catalog


class YouyunVisionCatalogTests(unittest.TestCase):
    def test_does_not_load_repository_fixture_without_explicit_configuration(self) -> None:
        from backend.youyun_vision_catalog import load_youyun_vision_catalog

        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(load_youyun_vision_catalog(), [])

    def test_keeps_small_qwen_vl_models_and_assigns_lower_vram(self) -> None:
        rows = parse_youyun_vision_catalog([
            {
                "name": "Qwen2-VL-2B-Instruct",
                "path": "/model/ModelScope/Qwen/Qwen2-VL-2B-Instruct",
                "sizeGb": 5,
            },
            {
                "name": "Qwen2.5-VL-3B-Instruct",
                "path": "/model/ModelScope/Qwen/Qwen2.5-VL-3B-Instruct",
                "sizeGb": 7,
            },
        ])

        self.assertEqual(
            [item["repoId"] for item in rows],
            ["Qwen/Qwen2-VL-2B-Instruct", "Qwen/Qwen2.5-VL-3B-Instruct"],
        )
        self.assertEqual(rows[0]["recommendedVramGb"], 16)
        self.assertEqual(rows[1]["recommendedVramGb"], 16)

    def test_preserves_community_image_metadata(self) -> None:
        rows = parse_youyun_vision_catalog([
            {
                "name": "Qwen2.5-VL-3B-Instruct",
                "repoId": "Qwen/Qwen2.5-VL-3B-Instruct",
                "path": "/model/ModelScope/Qwen/Qwen2.5-VL-3B-Instruct",
                "imageId": "image-lf-webui",
                "imageName": "LLaMAFactory-WebUI",
            },
        ])

        self.assertEqual(rows[0]["imageId"], "image-lf-webui")
        self.assertEqual(rows[0]["imageName"], "LLaMAFactory-WebUI")

    def test_parses_visual_models_and_excludes_generation_audio_and_text_entries(self) -> None:
        rows = parse_youyun_vision_catalog([
            {
                "name": "Qwen3-VL-32B-Thinking-FP8",
                "path": "/model/ModelScope/Qwen/Qwen3-VL-32B-Thinking-FP8",
                "sizeGb": 33,
            },
            {
                "name": "Qwen2.5-VL-72B-Instruct",
                "path": "/model/ModelScope/Qwen/Qwen2.5-VL-72B-Instruct",
                "sizeGb": 137,
            },
            {
                "name": "Qwen3-Omni-30B-A3B-Instruct",
                "path": "/model/ModelScope/Qwen/Qwen3-Omni-30B-A3B-Instruct",
                "sizeGb": 66,
            },
            {"name": "Qwen3-TTS-12Hz-1.7B-VoiceDesign", "path": "/model/tts"},
            {"name": "Qwen-Image-2512-FP8", "path": "/model/image"},
            {"name": "Qwen-72B-Chat", "path": "/model/chat"},
        ])

        self.assertEqual(
            [item["repoId"] for item in rows],
            [
                "Qwen/Qwen3-VL-32B-Thinking-FP8",
                "Qwen/Qwen2.5-VL-72B-Instruct",
                "Qwen/Qwen3-Omni-30B-A3B-Instruct",
            ],
        )
        self.assertEqual(rows[0]["template"], "qwen3_vl_nothink")
        self.assertIn("vision_language", rows[0]["capabilities"])
        self.assertGreaterEqual(rows[1]["recommendedVramGb"], 48)

    def test_deduplicates_repo_ids_and_normalizes_model_paths(self) -> None:
        rows = parse_youyun_vision_catalog(
            '[{"name":"Qwen3-VL-32B-Thinking-FP8","path":"/model/ModelScope/Qwen/Qwen3-VL-32B-Thinking-FP8/"},'
            '{"name":"duplicate","repoId":"qwen/qwen3-vl-32b-thinking-fp8","path":"/other"}]'
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["repoId"], "Qwen/Qwen3-VL-32B-Thinking-FP8")
        self.assertEqual(rows[0]["path"], "/model/ModelScope/Qwen/Qwen3-VL-32B-Thinking-FP8")

    def test_maps_official_repository_model_name_without_path(self) -> None:
        rows = parse_youyun_vision_catalog([
            {"name": "Qwen2-VL-2B-Instruct", "tags": ["VL"], "size": "4GB"}
        ])
        self.assertEqual(rows[0]["repoId"], "Qwen/Qwen2-VL-2B-Instruct")

    def test_preserves_remote_path_returned_by_public_model_repository(self) -> None:
        rows = parse_youyun_vision_catalog([
            {
                "Name": "Qwen3-VL-4B-Instruct",
                "Path": "/model/ModelScope/Qwen/Qwen3-VL-4B-Instruct",
                "Tag": "LLM,VL",
                "Size": "9GB",
            }
        ])
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["path"],
            "/model/ModelScope/Qwen/Qwen3-VL-4B-Instruct",
        )

    def test_excludes_embedding_models_from_visual_chat_catalog(self) -> None:
        rows = parse_youyun_vision_catalog([
            {
                "Name": "Qwen3-VL-Embedding-8B",
                "Path": "/model/ModelScope/Qwen/Qwen3-VL-Embedding-8B",
            },
            {
                "Name": "Qwen3-VL-4B-Instruct",
                "Path": "/model/ModelScope/Qwen/Qwen3-VL-4B-Instruct",
            },
        ])
        self.assertEqual([row["repoId"] for row in rows], ["Qwen/Qwen3-VL-4B-Instruct"])


if __name__ == "__main__":
    unittest.main()
