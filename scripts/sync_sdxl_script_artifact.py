from __future__ import annotations

import json
import sys

from backend.sdxl_script_artifact import sync_sdxl_script_artifact


def main() -> int:
    try:
        print(json.dumps(sync_sdxl_script_artifact(), ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)[:4000]}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
