from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.sdxl_script_artifact import validate_script_manifest


def verify(root: Path) -> dict[str, str | int]:
    artifact = validate_script_manifest(
        root / "train_text_to_image_lora_sdxl.py",
        root / "manifest.json",
    )
    return {
        "status": "verified",
        "commit": artifact.commit,
        "sizeBytes": artifact.size_bytes,
        "sha256": artifact.sha256,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the immutable SDXL Diffusers image artifact context.")
    parser.add_argument("root", nargs="?", default=".", type=Path)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(verify(args.root.resolve()), ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)[:4000]}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
