from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from backend.sdxl_script_artifact import (
    CANONICAL_SHA256,
    CANONICAL_SIZE_BYTES,
    MinioSdxlScriptStore,
    SdxlScriptResolver,
    load_sdxl_script_artifact,
    validate_script_manifest,
)


class SdxlScriptArtifactTests(unittest.TestCase):
    def test_canonical_sdxl_artifact_matches_manifest(self) -> None:
        artifact = load_sdxl_script_artifact()
        self.assertEqual(artifact.commit, "c9ff360966327ace3faad3807dc871a4e5447501")
        self.assertEqual(artifact.size_bytes, CANONICAL_SIZE_BYTES)
        self.assertEqual(artifact.sha256, CANONICAL_SHA256)

    def test_manifest_rejects_digest_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "script.py"
            script.write_bytes(b"bad")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"sha256": "bad", "sizeBytes": 1}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "manifest 的 repository"):
                validate_script_manifest(script, manifest)

    def test_minio_upload_is_idempotent_when_metadata_matches(self) -> None:
        artifact = load_sdxl_script_artifact()
        client = Mock()
        client.bucket_exists.return_value = True
        client.stat_object.return_value = SimpleNamespace(size=artifact.size_bytes, metadata={"X-Amz-Meta-Sha256": artifact.sha256})
        store = MinioSdxlScriptStore(client, "rag-assets")
        key = store.put_if_absent(artifact)
        self.assertEqual(key, artifact.object_key)
        client.fput_object.assert_not_called()

    def test_download_verified_rejects_bad_bytes_and_keeps_destination(self) -> None:
        artifact = load_sdxl_script_artifact()
        client = Mock()
        client.bucket_exists.return_value = True
        response = io.BytesIO(b"bad")
        response.close = Mock(wraps=response.close)
        response.release_conn = Mock()
        client.get_object.return_value = response
        store = MinioSdxlScriptStore(client, "rag-assets")
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / artifact.filename
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                store.download_verified(artifact, destination)
            self.assertFalse(destination.exists())
            response.close.assert_called_once()

    def test_resolver_prefers_verified_image(self) -> None:
        artifact = load_sdxl_script_artifact()
        resolved = SdxlScriptResolver(image_path=artifact.path, allow_public=False).resolve()
        self.assertEqual(resolved.source, "image")
        self.assertEqual(resolved.sha256, CANONICAL_SHA256)

    def test_resolver_uses_pinned_public_source_after_retryable_eof(self) -> None:
        artifact = load_sdxl_script_artifact()
        calls = []

        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *_): self.close()

        def opener(url, timeout):
            calls.append(url)
            if len(calls) == 1:
                raise EOFError("TLS EOF")
            return Response(artifact.path.read_bytes())

        with tempfile.TemporaryDirectory() as directory:
            resolved = SdxlScriptResolver(
                cache_dir=Path(directory), allow_public=True, retries=1, opener=opener,
            ).resolve()
            self.assertEqual(resolved.source, "public")
            self.assertEqual(resolved.attempts, 2)
            self.assertEqual(resolved.local_path.read_bytes(), artifact.path.read_bytes())

    def test_resolver_rejects_public_digest_mismatch_without_retry(self) -> None:
        calls = []

        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *_): self.close()

        def opener(url, timeout):
            calls.append(url)
            return Response(b"bad")

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                SdxlScriptResolver(cache_dir=Path(directory), allow_public=True, retries=3, opener=opener).resolve()
            self.assertEqual(len(calls), 1)

    def test_resolver_uses_minio_before_public(self) -> None:
        artifact = load_sdxl_script_artifact()

        class Store:
            def download_verified(self, _artifact, destination):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(artifact.path.read_bytes())
                return destination

        def unexpected_opener(*_args, **_kwargs):
            raise AssertionError("MinIO 命中后不应访问公网")

        with tempfile.TemporaryDirectory() as directory:
            resolved = SdxlScriptResolver(
                minio_store=Store(), cache_dir=Path(directory), allow_public=True, opener=unexpected_opener,
            ).resolve()
            self.assertEqual(resolved.source, "minio")

    def test_image_context_matches_canonical_artifact(self) -> None:
        from scripts.verify_sdxl_image_context import verify

        result = verify(Path("deploy/youyun/sdxl-lora"))
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["sha256"], CANONICAL_SHA256)

    def test_image_context_rejects_modified_script(self) -> None:
        from scripts.verify_sdxl_image_context import verify

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.json").write_bytes(Path("deploy/youyun/sdxl-lora/manifest.json").read_bytes())
            (root / "train_text_to_image_lora_sdxl.py").write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                verify(root)


if __name__ == "__main__":
    unittest.main()
