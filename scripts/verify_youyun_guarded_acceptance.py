"""Guarded single-instance Youyun acceptance runner.

The harness is intentionally conservative: it refuses to overlap runs, creates at
most one instance through the supplied callback, and always performs a bounded
delete plus disappearance verification before returning.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

# Allow direct execution as `python scripts/verify_youyun_guarded_acceptance.py`
# while keeping imports anchored to the repository package layout.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.providers.youyun.client import YouyunClient
from backend.providers.youyun.instance_manager import YouyunInstanceManager
from backend.providers.youyun.models import YouyunConfig


class AcceptanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._owned = False

    def __enter__(self) -> "AcceptanceLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            self._owned = True
        except FileExistsError as exc:
            raise RuntimeError("已有优云验收正在运行，拒绝并发创建实例。") from exc
        return self

    def __exit__(self, *_: object) -> None:
        if self._owned:
            self.path.unlink(missing_ok=True)


async def guarded_acceptance(
    manager: YouyunInstanceManager,
    operation: Callable[[], Any | Awaitable[Any]],
    *,
    max_seconds: float,
    instance_id_getter: Callable[[], str | None] | None = None,
) -> dict[str, Any]:
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    instance_id: str | None = None
    result: dict[str, Any] = {"startedAt": started, "instanceVerifiedAbsent": False, "cleanupAttempts": 0}
    try:
        async def invoke() -> Any:
            value = operation()
            return await value if isinstance(value, Awaitable) else value

        value = await asyncio.wait_for(invoke(), timeout=max_seconds)
        if hasattr(value, "uhost_id"):
            instance_id = str(value.uhost_id)
        elif isinstance(value, dict):
            instance_id = str(value.get("instanceId") or value.get("uhost_id") or "") or None
        result["outcome"] = value if isinstance(value, (str, int, float, bool, type(None), dict, list)) else "completed"
    except asyncio.TimeoutError:
        result.update({"status": "failed", "failureCode": "resource_timeout", "error": "优云验收超过硬超时。"})
    except BaseException as exc:
        result.update({"status": "failed", "error": str(exc) or type(exc).__name__})
    finally:
        if not instance_id and instance_id_getter is not None:
            instance_id = instance_id_getter()
        if instance_id:
            report = await asyncio.to_thread(manager.cleanup_after_task, instance_id, stop_first=True)
            result["cleanupAttempts"] = int(report.get("attempts") or 0)
            result["cleanup"] = {"terminated": bool(report.get("terminated")), "error": report.get("error")}
            result["instanceVerifiedAbsent"] = bool(report.get("terminated"))
            result["instanceId"] = instance_id
        result["finishedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    if instance_id and not result["instanceVerifiedAbsent"]:
        result["status"] = "failed"
        result["failureCode"] = "cleanup_failed"
    else:
        result.setdefault("status", "completed")
    return result


def _live_instances(client: YouyunClient) -> list[dict[str, str]]:
    items = client.describe_instances()
    return [{"id": str(item.uhost_id), "name": str(item.name), "state": str(item.state)} for item in items]


def preflight_summary(live: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "status": "preflight",
        "liveCount": len(live),
        "liveInstances": live,
        "instanceVerifiedAbsent": not live,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="执行受保护的优云单实例验收/清理核对")
    parser.add_argument("--cleanup-instance", help="只清理并核验指定实例，不创建新实例")
    parser.add_argument("--create-instance", action="store_true", help="创建一个真实优云实例并验证启动后清理")
    parser.add_argument("--max-seconds", type=float, default=float(os.environ.get("XUNTUI_ACCEPTANCE_MAX_SECONDS", "900")))
    parser.add_argument("--lock-file", default=".scratch/youyun-acceptance.lock")
    args = parser.parse_args()
    config = YouyunConfig.from_env()
    if config.missing_keys():
        print(json.dumps({"status": "blocked", "error": "优云配置不完整。", "missing": config.missing_keys()}, ensure_ascii=False))
        return 2
    try:
        client = YouyunClient(config)
        manager = YouyunInstanceManager(client)
        with AcceptanceLock(Path(args.lock_file)):
            live = _live_instances(client)
            if args.cleanup_instance:
                async def operation() -> dict[str, Any]:
                    return {"instanceId": args.cleanup_instance}

                summary = asyncio.run(guarded_acceptance(manager, operation, max_seconds=args.max_seconds))
            elif args.create_instance:
                existing_acceptance = [item for item in live if item["name"].startswith("cppt-")]
                if existing_acceptance:
                    print(json.dumps({"status": "blocked", "error": "检测到已有 cppt-* 验收实例，拒绝创建新实例。", "liveInstances": live}, ensure_ascii=False))
                    return 2

                # Keep the cloud probe bounded even if the API reports a long default boot timeout.
                manager.boot_timeout = min(manager.boot_timeout, max(30.0, args.max_seconds * 0.65))
                manager.terminate_timeout = min(manager.terminate_timeout, max(30.0, args.max_seconds * 0.25))

                created: dict[str, str] = {}

                async def operation() -> dict[str, Any]:
                    instance = await asyncio.to_thread(
                        manager.create_one_shot,
                        f"acceptance-{int(time.time())}",
                        name=f"cppt-acceptance-{int(time.time())}",
                        on_created=lambda instance_id: created.__setitem__("instanceId", instance_id),
                    )
                    return {"instanceId": instance.uhost_id, "state": instance.state, "ipPresent": bool(instance.ip)}

                summary = asyncio.run(
                    guarded_acceptance(
                        manager,
                        operation,
                        max_seconds=args.max_seconds,
                        instance_id_getter=lambda: created.get("instanceId"),
                    )
                )
            else:
                summary = preflight_summary(live)
            print(json.dumps(summary, ensure_ascii=False))
            return 0 if summary.get("status") in {"completed", "preflight"} and summary.get("instanceVerifiedAbsent", True) else 1
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc) or type(exc).__name__}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
