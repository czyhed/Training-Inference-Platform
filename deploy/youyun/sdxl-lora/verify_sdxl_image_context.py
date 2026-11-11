from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

COMMIT = "c9ff360966327ace3faad3807dc871a4e5447501"
SIZE = 55_568
SHA256 = "f72ce7d06b1613a1f7e051cb8c8401f210791e0b6e57ccc7fd6223dbbc92f949"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    root = parser.parse_args(argv).root
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    script = root / "train_text_to_image_lora_sdxl.py"
    digest = hashlib.sha256(script.read_bytes()).hexdigest()
    if manifest.get("commit") != COMMIT or script.stat().st_size != SIZE or digest != SHA256:
        raise SystemExit("SDXL image context verification failed")
    print(json.dumps({"status": "verified", "commit": COMMIT, "sizeBytes": SIZE, "sha256": SHA256}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
