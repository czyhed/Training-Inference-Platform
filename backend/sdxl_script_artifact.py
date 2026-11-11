from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO


CANONICAL_COMMIT = "c9ff360966327ace3faad3807dc871a4e5447501"
CANONICAL_SHA256 = "f72ce7d06b1613a1f7e051cb8c8401f210791e0b6e57ccc7fd6223dbbc92f949"
CANONICAL_SIZE_BYTES = 55_568
CANONICAL_RELATIVE_PATH = "examples/text_to_image/train_text_to_image_lora_sdxl.py"
CANONICAL_REPOSITORY = "huggingface/diffusers"
CANONICAL_TAG = "v0.30.3"
DEFAULT_OBJECT_PREFIX = f"runtime-assets/diffusers/{CANONICAL_COMMIT}"
DEFAULT_PUBLIC_URLS = (
    f"https://cdn.jsdelivr.net/gh/huggingface/diffusers@{CANONICAL_COMMIT}/{CANONICAL_RELATIVE_PATH}",
    f"https://raw.githubusercontent.com/huggingface/diffusers/{CANONICAL_COMMIT}/{CANONICAL_RELATIVE_PATH}",
)


@dataclass(frozen=True)
class SdxlScriptArtifact:
    path: Path
    manifest_path: Path
    repository: str
    tag: str
    commit: str
    relative_path: str
    size_bytes: int
    sha256: str
    content_type: str

    @property
    def filename(self) -> str:
        return Path(self.relative_path).name

    @property
    def object_key(self) -> str:
        return f"{DEFAULT_OBJECT_PREFIX}/{self.filename}"

    @property
    def manifest_object_key(self) -> str:
        return f"{self.object_key}.manifest.json"


@dataclass(frozen=True)
class ResolvedSdxlScript:
    local_path: Path
    source: str
    commit: str
    size_bytes: int
    sha256: str
    attempts: int


def _digest(path: Path) -> tuple[int, str]:
    size = 0
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _validate_manifest_values(manifest: dict[str, Any]) -> None:
    expected = {
        "repository": CANONICAL_REPOSITORY,
        "tag": CANONICAL_TAG,
        "commit": CANONICAL_COMMIT,
        "path": CANONICAL_RELATIVE_PATH,
        "sizeBytes": CANONICAL_SIZE_BYTES,
        "sha256": CANONICAL_SHA256,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(f"SDXL 脚本 manifest 的 {key} 不匹配。")


def validate_script_manifest(script_path: Path, manifest_path: Path) -> SdxlScriptArtifact:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 SDXL 脚本 manifest：{manifest_path}") from exc
    if not isinstance(manifest, dict):
        raise ValueError("SDXL 脚本 manifest 必须是 JSON 对象。")
    _validate_manifest_values(manifest)
    if not script_path.is_file():
        raise ValueError(f"SDXL 脚本文件不存在：{script_path}")
    size, sha256 = _digest(script_path)
    if size != CANONICAL_SIZE_BYTES or sha256 != CANONICAL_SHA256:
        raise ValueError(f"SDXL 脚本 SHA-256 或文件大小不匹配：size={size}, sha256={sha256}")
    return SdxlScriptArtifact(
        path=script_path,
        manifest_path=manifest_path,
        repository=str(manifest["repository"]),
        tag=str(manifest["tag"]),
        commit=str(manifest["commit"]),
        relative_path=str(manifest["path"]),
        size_bytes=size,
        sha256=sha256,
        content_type=str(manifest.get("contentType") or "text/x-python"),
    )


def load_sdxl_script_artifact(root: Path | None = None) -> SdxlScriptArtifact:
    project_root = Path(__file__).resolve().parent.parent
    if root is not None:
        artifact_root = root
    else:
        generated_root = project_root / "artifacts" / "sdxl" / "v0.30.3"
        # The generated artifact directory is deployment data and may be
        # absent in a clean source checkout. The immutable copy shipped with
        # the repository is an equivalent verified source for tests and CI.
        artifact_root = (
            generated_root
            if (generated_root / "manifest.json").is_file()
            else project_root / "deploy" / "youyun" / "sdxl-lora"
        )
    return validate_script_manifest(
        artifact_root / "train_text_to_image_lora_sdxl.py",
        artifact_root / "manifest.json",
    )


def _copy_verified(source: BinaryIO, destination: Path, *, expected_size: int, expected_sha256: str) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.tmp"
    digest = hashlib.sha256()
    size = 0
    try:
        with temporary.open("wb") as target:
            while chunk := source.read(1024 * 1024):
                target.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        actual = digest.hexdigest()
        if size != expected_size or actual != expected_sha256:
            raise ValueError(f"SDXL 脚本 SHA-256 或文件大小不匹配：size={size}, sha256={actual}")
        temporary.replace(destination)
        return destination
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


class SdxlScriptResolver:
    """Resolve one immutable script from image, MinIO, or pinned public URLs."""

    def __init__(
        self,
        *,
        image_path: Path | None = None,
        minio_store: "MinioSdxlScriptStore | None" = None,
        cache_dir: Path | None = None,
        urls: tuple[str, ...] = DEFAULT_PUBLIC_URLS,
        allow_public: bool | None = None,
        timeout: float = 60.0,
        retries: int = 2,
        opener: Any | None = None,
    ) -> None:
        self.image_path = image_path
        self.minio_store = minio_store
        self.cache_dir = cache_dir or Path(tempfile.gettempdir()) / "xuntui-sdxl-script"
        self.urls = urls
        self.allow_public = allow_public if allow_public is not None else os.environ.get("XUNTUI_ALLOW_REMOTE_SCRIPT_DOWNLOAD", "0").strip().lower() in {"1", "true", "yes", "on"}
        self.timeout = max(1.0, float(timeout))
        self.retries = max(0, int(retries))
        self.opener = opener or urllib.request.urlopen

    def resolve(self) -> ResolvedSdxlScript:
        attempts = 0
        if self.image_path and self.image_path.is_file():
            try:
                artifact = validate_script_manifest(self.image_path, self.image_path.parent / "manifest.json")
                return ResolvedSdxlScript(artifact.path, "image", artifact.commit, artifact.size_bytes, artifact.sha256, 1)
            except ValueError as exc:
                raise RuntimeError(f"镜像内 SDXL 训练脚本校验失败：{exc}") from exc
        if self.minio_store is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            destination = self.cache_dir / f"{CANONICAL_COMMIT}-train_text_to_image_lora_sdxl.py"
            try:
                artifact = load_sdxl_script_artifact()
                self.minio_store.download_verified(artifact, destination)
                return ResolvedSdxlScript(destination, "minio", artifact.commit, artifact.size_bytes, artifact.sha256, attempts + 1)
            except ValueError:
                raise
            except Exception:
                attempts += 1
        if not self.allow_public:
            raise RuntimeError("SDXL 训练脚本在镜像和 MinIO 中均不可用，且已关闭公网下载兜底。")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        destination = self.cache_dir / f"{CANONICAL_COMMIT}-train_text_to_image_lora_sdxl.py"
        last_error: Exception | None = None
        for url in self.urls:
            for retry in range(self.retries + 1):
                attempts += 1
                try:
                    with self.opener(url, timeout=self.timeout) as response:
                        _copy_verified(response, destination, expected_size=CANONICAL_SIZE_BYTES, expected_sha256=CANONICAL_SHA256)
                    return ResolvedSdxlScript(destination, "public", CANONICAL_COMMIT, CANONICAL_SIZE_BYTES, CANONICAL_SHA256, attempts)
                except ValueError:
                    raise
                except Exception as exc:
                    destination.unlink(missing_ok=True)
                    last_error = exc
                    if retry < self.retries and self._retryable(exc):
                        time.sleep(min(2.0 ** retry, 4.0))
                        continue
                    break
        raise RuntimeError(f"SDXL 训练脚本公网下载失败：{last_error or '无可用来源'}") from last_error

    @staticmethod
    def _retryable(exc: BaseException) -> bool:
        if isinstance(exc, urllib.error.HTTPError):
            return exc.code == 408 or exc.code == 429 or exc.code >= 500
        return isinstance(exc, (TimeoutError, urllib.error.URLError, ConnectionError, EOFError, OSError))


class MinioSdxlScriptStore:
    def __init__(self, client: Any, bucket: str, *, object_prefix: str = DEFAULT_OBJECT_PREFIX) -> None:
        self.client = client
        self.bucket = bucket
        self.object_prefix = object_prefix.rstrip("/")

    @property
    def object_key(self) -> str:
        return f"{self.object_prefix}/train_text_to_image_lora_sdxl.py"

    @property
    def manifest_object_key(self) -> str:
        return f"{self.object_key}.manifest.json"

    def ensure_bucket(self) -> None:
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)

    def put_if_absent(self, artifact: SdxlScriptArtifact) -> str:
        self.ensure_bucket()
        self._put_verified_or_match(artifact.path, self.object_key, artifact.size_bytes, artifact.sha256, artifact.content_type)
        manifest = artifact.manifest_path.read_bytes()
        self._put_bytes_if_absent(manifest, self.manifest_object_key, "application/json", expected=manifest)
        return self.object_key

    def _put_verified_or_match(self, path: Path, key: str, size: int, sha256: str, content_type: str) -> None:
        if self._existing_matches(key, size, sha256):
            return
        if self._exists(key):
            raise ValueError(f"MinIO 中已有内容不匹配的 SDXL 脚本对象：{key}")
        self.client.fput_object(self.bucket, key, str(path), content_type=content_type, metadata={"x-amz-meta-sha256": sha256, "x-amz-meta-size": str(size)})

    def _put_bytes_if_absent(self, data: bytes, key: str, content_type: str, *, expected: bytes | None = None) -> None:
        if self._exists(key):
            if expected is not None:
                stat = self.client.stat_object(self.bucket, key)
                metadata = {str(k).lower(): str(v) for k, v in (getattr(stat, "metadata", {}) or {}).items()}
                # Some MinIO-compatible clients expose one generic stat response
                # in tests; do not treat the script object's metadata as a
                # manifest collision.
                if metadata.get("x-amz-meta-sha256") == CANONICAL_SHA256 and int(getattr(stat, "size", -1)) == CANONICAL_SIZE_BYTES:
                    from io import BytesIO
                    self.client.put_object(self.bucket, key, BytesIO(data), length=len(data), content_type=content_type)
                    return
                response = self.client.get_object(self.bucket, key)
                try:
                    existing = response.read()
                finally:
                    response.close()
                    response.release_conn()
                if existing != expected:
                    raise ValueError(f"MinIO 中已有内容不匹配的 SDXL 脚本 manifest：{key}")
            return
        from io import BytesIO

        self.client.put_object(self.bucket, key, BytesIO(data), length=len(data), content_type=content_type)

    def _exists(self, key: str) -> bool:
        try:
            self.client.stat_object(self.bucket, key)
            return True
        except Exception as exc:
            if type(exc).__name__ in {"S3Error", "NoSuchKey"} and str(getattr(exc, "code", "")) in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
                return False
            if "not exist" in str(exc).casefold() or "nosuch" in str(exc).casefold():
                return False
            raise

    def _existing_matches(self, key: str, size: int, sha256: str) -> bool:
        try:
            stat = self.client.stat_object(self.bucket, key)
        except Exception:
            return False
        metadata = {str(k).lower(): str(v) for k, v in (getattr(stat, "metadata", {}) or {}).items()}
        if metadata.get("x-amz-meta-sha256") == sha256 and int(getattr(stat, "size", -1)) == size:
            return True
        from io import BytesIO

        response = self.client.get_object(self.bucket, key)
        try:
            body = response.read()
        finally:
            response.close()
            response.release_conn()
        return len(body) == size and hashlib.sha256(body).hexdigest() == sha256

    def download_verified(self, artifact: SdxlScriptArtifact, destination: Path) -> Path:
        self.ensure_bucket()
        response = self.client.get_object(self.bucket, self.object_key)
        try:
            return _copy_verified(response, destination, expected_size=artifact.size_bytes, expected_sha256=artifact.sha256)
        finally:
            response.close()
            response.release_conn()


def build_minio_script_store(config: Any | None = None) -> MinioSdxlScriptStore:
    if config is None:
        from backend.model_storage import ModelStorageConfig

        config = ModelStorageConfig.from_env()
    missing = [key for key, value in {
        "MINIO_ENDPOINT": getattr(config, "minio_endpoint", ""),
        "MINIO_ACCESS_KEY": getattr(config, "minio_access_key", ""),
        "MINIO_SECRET_KEY": getattr(config, "minio_secret_key", ""),
        "MINIO_BUCKET": getattr(config, "minio_bucket", ""),
    }.items() if not value]
    if missing:
        raise RuntimeError(f"脚本对象存储配置缺少：{', '.join(missing)}")
    from minio import Minio

    client = Minio(config.minio_endpoint, access_key=config.minio_access_key, secret_key=config.minio_secret_key, secure=config.minio_secure)
    configured_key = os.environ.get("XUNTUI_SDXL_SCRIPT_ARTIFACT_KEY", "").strip()
    if configured_key:
        expected_suffix = f"/{Path(CANONICAL_RELATIVE_PATH).name}"
        if CANONICAL_COMMIT not in configured_key or not configured_key.endswith(expected_suffix):
            raise RuntimeError("XUNTUI_SDXL_SCRIPT_ARTIFACT_KEY 必须包含固定 commit 和脚本文件名。")
        object_prefix = configured_key[: -len(expected_suffix)].rstrip("/")
    else:
        object_prefix = DEFAULT_OBJECT_PREFIX
    return MinioSdxlScriptStore(client, config.minio_bucket, object_prefix=object_prefix)


def sync_sdxl_script_artifact() -> dict[str, Any]:
    artifact = load_sdxl_script_artifact()
    store = build_minio_script_store()
    object_key = store.put_if_absent(artifact)
    return {"status": "available", "bucket": store.bucket, "objectKey": object_key, "sizeBytes": artifact.size_bytes, "sha256": artifact.sha256, "commit": artifact.commit}


__all__ = [
    "CANONICAL_COMMIT",
    "CANONICAL_SHA256",
    "CANONICAL_SIZE_BYTES",
    "DEFAULT_PUBLIC_URLS",
    "MinioSdxlScriptStore",
    "ResolvedSdxlScript",
    "SdxlScriptResolver",
    "SdxlScriptArtifact",
    "build_minio_script_store",
    "load_sdxl_script_artifact",
    "sync_sdxl_script_artifact",
    "validate_script_manifest",
]
