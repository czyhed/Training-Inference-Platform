"""Fail closed before deployment when existing workloads or uploads are active."""
from __future__ import annotations

import argparse
import json
import urllib.request

IDLE_STATES = {"completed", "failed", "stopped", "cancelled", "archived", "created", "draft", "idle"}


def ensure_idle(read) -> dict:
    health = read("/health")
    if health.get("status") != "ok" or health.get("activeTaskIds") or health.get("activeTaskId") or health.get("inference") not in {None, "idle"}:
        raise RuntimeError("Backend is not idle; deployment must wait, not stop workloads.")
    counts = {}
    for endpoint in ("/platform/tasks", "/platform/services", "/tasks"):
        page = 1
        count = 0
        while True:
            payload = read(f"{endpoint}?page={page}&pageSize=100")
            if not isinstance(payload.get("items"), list):
                raise RuntimeError(f"Cannot verify workload listing: {endpoint}")
            for item in payload["items"]:
                count += 1
                if item.get("state") not in IDLE_STATES:
                    raise RuntimeError(f"Active or unknown workload: {item.get('id')} ({item.get('state')})")
                if endpoint == "/tasks":
                    detail = read(f"/tasks/{item['id']}")
                    if any(a.get("syncState") in {"syncing", "uploading"} for a in detail.get("artifacts", [])):
                        raise RuntimeError(f"Artifact sync in progress: {item['id']}")
            if not payload.get("hasNext", page * 100 < payload.get("total", count)):
                break
            page += 1
            if page > 1000:
                raise RuntimeError("Workload pagination did not terminate")
        counts[endpoint] = count
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", required=True)
    args = parser.parse_args()
    def read(path):
        with urllib.request.urlopen(args.api.rstrip("/") + path, timeout=15) as response:
            return json.load(response)
    print("PASS: idle deployment preflight", ensure_idle(read))


if __name__ == "__main__":
    main()
