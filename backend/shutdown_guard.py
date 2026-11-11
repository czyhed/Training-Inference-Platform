"""Windows 关机守卫：系统关机/控制台关闭时尽力同步释放优云实例。

正常退出路径由 uvicorn 优雅关闭（lifespan shutdown）释放实例；但 Windows
开始菜单关机不会给后台 Python 进程发 SIGINT/SIGTERM，进程在窗口期后被
直接终止，泄漏的实例只能等下次启动对账。本守卫通过
SetConsoleCtrlHandler 捕获 CTRL_CLOSE/CTRL_LOGOFF/CTRL_SHUTDOWN 事件
（系统默认给约 5 秒），在剩余预算内只读本地 SQLite 收集持久化实例句柄，
直接调用优云 API 终止对应实例。

约束：
- 处理函数运行在独立线程，必须快速返回且绝不抛出；
- 只读数据库（mode=ro），不写日志——审计由下次启动对账补齐；
- 单个 API 调用超时 3 秒，总预算超限即放弃，兜底交给启动对账。
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

_TRAPPED_EVENTS = {2, 5, 6}  # CTRL_CLOSE, CTRL_LOGOFF, CTRL_SHUTDOWN
_handler_ref: Any = None  # 防止 ctypes 回调被垃圾回收


def collect_persisted_handles(db_path: Path) -> list[dict[str, str]]:
    """只读扫描推理服务表，返回待释放的优云实例句柄列表。"""
    if not db_path.exists():
        return []
    handles: list[dict[str, str]] = []
    try:
        connection = sqlite3.connect(
            f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=1
        )
    except sqlite3.Error:
        return []
    try:
        rows = connection.execute(
            """
            SELECT id, config FROM inference_services
            WHERE state IN ('running', 'starting', 'stopping', 'recovering')
            """
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        connection.close()
    seen: set[str] = set()
    for _service_id, config_json in rows:
        try:
            config = json.loads(config_json or "{}")
        except (TypeError, ValueError):
            continue
        remote = config.get("remoteRuntime")
        if not isinstance(remote, dict):
            continue
        instance_id = str(remote.get("instanceId") or remote.get("instance_id") or "").strip()
        if not instance_id or instance_id in seen:
            continue
        seen.add(instance_id)
        handles.append(
            {
                "instanceId": instance_id,
                "region": str(remote.get("region") or "") or "",
                "zone": str(remote.get("zone") or "") or "",
            }
        )
    return handles


def terminate_persisted_instances(runtime_root: Path) -> int:
    """同步终止所有持久化句柄对应的优云实例，返回成功数。"""
    handles = collect_persisted_handles(runtime_root / "training.db")
    if not handles:
        return 0
    try:
        from backend.providers.youyun.client import YouyunClient
        from backend.providers.youyun.models import YouyunConfig

        config = YouyunConfig.from_env()
        config.timeout_seconds = 3.0
        config.max_retries = 0
        client = YouyunClient(config, require_full=False)
    except Exception:  # noqa: BLE001 - 守卫绝不抛出
        return 0
    released = 0
    for handle in handles:
        try:
            client.delete_instance(
                handle["instanceId"],
                region=handle["region"] or None,
                zone=handle["zone"] or None,
            )
            released += 1
        except Exception:  # noqa: BLE001 - 单实例失败继续处理下一个
            continue
    return released


def install() -> bool:
    """注册 Windows 控制台关机回调；非 Windows 或被禁用时返回 False。"""
    global _handler_ref
    if os.name != "nt":
        return False
    if os.environ.get("XUNTUI_SHUTDOWN_GUARD", "true").strip().lower() in {
        "0", "false", "no", "off"
    }:
        return False
    try:
        import ctypes

        handler_routine = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)

        def handler(event_type: int) -> int:
            if event_type not in _TRAPPED_EVENTS:
                return 0
            try:
                runtime_root = Path(
                    os.environ.get("XUNTUI_RUNTIME_ROOT", ".runtime-local")
                )
                terminate_persisted_instances(runtime_root)
            except Exception:  # noqa: BLE001 - 守卫绝不抛出
                pass
            return 0  # 交还默认处理（进程退出）

        _handler_ref = handler_routine(handler)
        return bool(
            ctypes.windll.kernel32.SetConsoleCtrlHandler(_handler_ref, True)
        )
    except Exception:  # noqa: BLE001 - 注册失败静默降级
        return False


__all__ = ["collect_persisted_handles", "install", "terminate_persisted_instances"]
