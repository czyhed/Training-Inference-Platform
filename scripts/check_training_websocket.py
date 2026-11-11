"""Read-only training-stream acceptance; never create a task or allocate a GPU."""
from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from collections import Counter

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect


def check(api: str, task_id: str, timeout: float = 25) -> dict:
    parsed = urllib.parse.urlsplit(api.rstrip("/") + f"/tasks/{urllib.parse.quote(task_id, safe='')}/stream")
    url = urllib.parse.urlunsplit(("wss" if parsed.scheme == "https" else "ws", parsed.netloc, parsed.path, "", ""))
    counts: Counter = Counter()
    deadline = time.monotonic() + timeout
    terminal = False
    with connect(url, open_timeout=min(timeout, 10), close_timeout=3) as socket:
        while time.monotonic() < deadline:
            try:
                message = json.loads(socket.recv(timeout=max(0.01, deadline - time.monotonic())))
            except ConnectionClosed as exc:
                if not terminal or exc.rcvd is None or exc.rcvd.code != 1000:
                    raise AssertionError(f"unexpected stream close; terminal={terminal}") from exc
                break
            except TimeoutError as exc:
                raise AssertionError("stream did not deliver a heartbeat or clean terminal close") from exc
            kind = message.get("type")
            counts[kind] += 1
            terminal = terminal or kind == "terminal"
            if kind == "heartbeat" and not terminal:
                break
        else:
            raise AssertionError("stream acceptance deadline exceeded")
    if not counts["state"] or not counts["metrics"] or not (terminal or counts["heartbeat"]):
        raise AssertionError(f"incomplete stream events: {dict(counts)}")
    return {"taskId": task_id, "counts": dict(counts), "terminal": terminal}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", required=True, help="API base including /api")
    parser.add_argument("--task-id", help="Defaults to a completed task from the first task page")
    parser.add_argument("--timeout", type=float, default=25)
    args = parser.parse_args()
    task_id = args.task_id
    if not task_id:
        with urllib.request.urlopen(args.api.rstrip("/") + "/tasks?pageSize=20", timeout=10) as response:
            payload = json.load(response)
        tasks = payload if isinstance(payload, list) else payload.get("items", [])
        if not tasks:
            print("SKIP: no existing training task; no task was created")
            return
        task_id = next((task["id"] for task in tasks if task.get("state") == "completed"), tasks[0]["id"])
    print("PASS", json.dumps(check(args.api, task_id, args.timeout), ensure_ascii=False))


if __name__ == "__main__":
    main()
