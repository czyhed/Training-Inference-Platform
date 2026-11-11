"""一次性优云 GPU 实例生命周期管理。

策略（需求 10.2/10.3、29 节确认）：
- 每个远程任务临时创建实例；任务结束、失败、停止、超时后必须删除实例。
- 创建前强制库存检查（官方要求，勿用 Describe 类接口代替）。
- 创建后轮询至 Running 并拿到 SSH 信息（IP/端口/密码）。
- terminate 永不抛异常：清理失败也要返回报告，由调用方落库。
"""
from __future__ import annotations

import base64
import datetime as _datetime
import logging
import re
import threading
import time
from typing import Any, Callable

from .client import YouyunClient
from .models import GpuSpecCombination, YouyunApiError, YouyunInstance
from .resource_health import youyun_resource_health

logger = logging.getLogger(__name__)

_PENDING_STATES = {"", "Installing", "Install", "Starting", "Pending", "Creating"}
_ABSENT_STATES = {"Deleted", "Terminated"}
_STOPPED_STATES = {"Stopped", "Stop"}
_PLATFORM_INSTANCE_PREFIXES = ("cppt-vt-", "cppt-img-train-", "cppt-img-eval-", "cppt-img-service-")
_TRANSIENT_CREATE_RETCODES = {8433, 226645}


class YouyunProvisionCancelled(RuntimeError):
    """创建实例等待期间收到任务停止请求。"""


def _decode_password(encoded: str | None) -> str | None:
    if not encoded:
        return None
    try:
        return base64.b64decode(encoded).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


class YouyunInstanceManager:
    def __init__(
        self,
        client: YouyunClient,
        poll_interval: float = 5.0,
        boot_timeout: float = 900.0,
        terminate_timeout: float = 300.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        cleanup_attempts: int = 2,
        create_attempts: int = 3,
    ) -> None:
        self.client = client
        self.poll_interval = poll_interval
        self.boot_timeout = boot_timeout
        self.terminate_timeout = terminate_timeout
        self._sleep = sleep
        self._clock = clock
        self.cleanup_attempts = max(1, int(cleanup_attempts))
        self.create_attempts = max(1, int(create_attempts))
        self._idempotent_instances: dict[str, str] = {}
        self._instance_locations: dict[str, tuple[str | None, str | None]] = {}
        self._idempotency_lock = threading.Lock()

    def _remember_location(self, uhost_id: str, region: str | None, zone: str | None) -> None:
        if region or zone:
            self._instance_locations[str(uhost_id)] = (region, zone)

    def _location(self, uhost_id: str, region: str | None = None, zone: str | None = None) -> tuple[str | None, str | None]:
        remembered_region, remembered_zone = self._instance_locations.get(str(uhost_id), (None, None))
        return region or remembered_region, zone or remembered_zone

    def _describe_instance(self, uhost_id: str, *, region: str | None = None, zone: str | None = None) -> YouyunInstance:
        region, zone = self._location(uhost_id, region, zone)
        if region or zone:
            return self.client.describe_instance(uhost_id, region=region, zone=zone)
        return self.client.describe_instance(uhost_id)

    def _stop_instance(self, uhost_id: str, *, region: str | None = None, zone: str | None = None) -> None:
        region, zone = self._location(uhost_id, region, zone)
        if region or zone:
            self.client.stop_instance(uhost_id, region=region, zone=zone)
        else:
            self.client.stop_instance(uhost_id)

    def _delete_instance(self, uhost_id: str, *, region: str | None = None, zone: str | None = None) -> None:
        region, zone = self._location(uhost_id, region, zone)
        if region or zone:
            self.client.delete_instance(uhost_id, region=region, zone=zone)
        else:
            self.client.delete_instance(uhost_id)

    # ---- 创建 ----

    def create_one_shot(
        self,
        task_id: str,
        gpu: int | None = None,
        cpu: int | None = None,
        memory_gb: int | None = None,
        gpu_type: str | None = None,
        image_id: str | None = None,
        charge_type: str | None = None,
        region: str | None = None,
        zone: str | None = None,
        name: str | None = None,
        on_created: Callable[[str], None] | None = None,
        idempotency_key: str | None = None,
        should_cancel: Callable[[], bool] | None = None,
        instance_mode: str | None = None,
    ) -> YouyunInstance:
        """库存检查 → 创建 → 轮询至 Running。失败抛 YouyunApiError。"""
        if idempotency_key:
            with self._idempotency_lock:
                existing_id = self._idempotent_instances.get(idempotency_key)
            if existing_id:
                existing = self.get(existing_id)
                if existing is not None and existing.state not in {"Deleted", "Terminated"}:
                    return existing
                with self._idempotency_lock:
                    self._idempotent_instances.pop(idempotency_key, None)
        capacity_kwargs = {"gpu_type": gpu_type, "image_id": image_id}
        if region:
            capacity_kwargs["region"] = region
        if zone:
            capacity_kwargs["zone"] = zone
        if charge_type:
            capacity_kwargs["charge_type"] = charge_type
        capacity = self.client.check_resource_capacity(**capacity_kwargs)
        spec = self._pick_spec(capacity.specs, gpu, cpu, memory_gb)
        if spec is None:
            selected_zone = zone or self.client.config.zone or "目标可用区"
            raise YouyunApiError(
                "CheckCompShareResourceCapacity",
                None,
                f"GPU {capacity.gpu_type} 在 {selected_zone} 无可用规格库存。",
            )
        instance_name = (name or f"cppt-{task_id}")[:63]
        health_candidate = {
            "instanceMode": instance_mode or "unknown",
            "region": region or self.client.config.region,
            "zone": zone or self.client.config.zone,
            "imageId": image_id or self.client.config.image_id,
            "gpuType": gpu_type or self.client.config.gpu_type,
            "chargeType": charge_type or self.client.config.charge_type,
        }
        ids: list[str] = []
        for attempt in range(self.create_attempts):
            try:
                create_kwargs = {
                    "name": instance_name,
                    "gpu": spec.gpu,
                    "cpu": spec.cpu,
                    "memory_gb": spec.memory_gb,
                    "gpu_type": gpu_type,
                    "image_id": image_id,
                }
                if region:
                    create_kwargs["region"] = region
                if zone:
                    create_kwargs["zone"] = zone
                if charge_type:
                    create_kwargs["charge_type"] = charge_type
                ids = self.client.create_instance(**create_kwargs)
                break
            except YouyunApiError as exc:
                youyun_resource_health.mark_failure(health_candidate, str(exc.ret_code or ""))
                # CreateCompShareInstance may return a transient service error
                # after the instance has already been committed. Recover by
                # the unique task name before retrying, so we never duplicate
                # a billable instance and can still clean it up.
                if exc.ret_code not in _TRANSIENT_CREATE_RETCODES:
                    raise
                recovered = self._recover_created_instance(instance_name, region=region, zone=zone)
                if recovered is not None:
                    ids = [recovered.uhost_id]
                    logger.warning(
                        "优云创建接口返回 %s，但已按名称接管实例：%s (%s)",
                        exc.ret_code,
                        instance_name,
                        recovered.uhost_id,
                    )
                    break
                if attempt >= self.create_attempts - 1:
                    raise
                delay = min(5.0, 1.5 * (attempt + 1))
                logger.warning(
                    "优云创建接口暂时不可用（RetCode=%s），%ss 后重试（第 %s/%s 次）",
                    exc.ret_code,
                    delay,
                    attempt + 2,
                    self.create_attempts,
                )
                self._sleep(delay)
        if not ids:
            raise YouyunApiError("CreateCompShareInstance", None, "创建实例未返回 ID。")
        uhost_id = ids[0]
        self._remember_location(uhost_id, region, zone)
        if on_created is not None:
            try:
                on_created(uhost_id)
            except Exception as exc:  # noqa: BLE001 - callback failures must not leak a billable instance
                # The callback is invoked before ``wait_running`` so callers can
                # persist the provider handle while the instance is still
                # booting.  If persistence fails, clean up here as well; the
                # outer runner may retry the same cleanup idempotently.
                try:
                    report = self.cleanup_after_task(uhost_id, stop_first=False, region=region, zone=zone)
                    detail = (
                        "实例已释放"
                        if report.get("terminated")
                        else f"实例释放失败：{report.get('error') or '未确认'}"
                    )
                except Exception as cleanup_exc:  # cleanup is best effort; retain the callback error
                    detail = f"实例释放异常：{cleanup_exc}"
                if idempotency_key:
                    with self._idempotency_lock:
                        if self._idempotent_instances.get(idempotency_key) == uhost_id:
                            self._idempotent_instances.pop(idempotency_key, None)
                try:
                    exc.add_note(f"优云实例 {uhost_id} 回调失败后的清理结果：{detail}")
                except Exception:
                    pass
                raise
        if idempotency_key:
            with self._idempotency_lock:
                self._idempotent_instances[idempotency_key] = uhost_id
        logger.info("优云实例已创建：%s（%s，规格 %s卡/%s核/%sGB）", uhost_id, capacity.gpu_type, spec.gpu, spec.cpu, spec.memory_gb)
        try:
            result = self.wait_running(uhost_id, should_cancel=should_cancel, region=region, zone=zone)
            youyun_resource_health.mark_success(health_candidate)
            return result
        except YouyunProvisionCancelled:
            raise
        except Exception as exc:
            # A boot timeout still leaves a billable instance. Best-effort cleanup here,
            # before the provider receives a handle, prevents orphaned GPU resources.
            report = self.cleanup_after_task(uhost_id, stop_first=False, region=region, zone=zone)
            detail = f"；实例清理结果：{'已释放' if report.get('terminated') else '失败：' + str(report.get('error'))}"
            if isinstance(exc, YouyunApiError):
                raise YouyunApiError(exc.action, exc.ret_code, f"{exc.message}{detail}") from exc
            raise RuntimeError(f"优云实例 {uhost_id} 启动失败：{exc}{detail}") from exc

    def _recover_created_instance(
        self,
        name: str,
        attempts: int = 3,
        *,
        region: str | None = None,
        zone: str | None = None,
    ) -> YouyunInstance | None:
        """Recover an instance committed before CreateCompShareInstance errored."""
        for attempt in range(max(1, attempts)):
            try:
                kwargs: dict[str, str] = {}
                if region:
                    kwargs["region"] = region
                if zone:
                    kwargs["zone"] = zone
                matches = [
                    item
                    for item in self.client.describe_instances(**kwargs)
                    if item.name == name and item.state not in _ABSENT_STATES
                ]
            except Exception:
                matches = []
            if matches:
                return matches[0]
            if attempt < attempts - 1:
                self._sleep(min(5.0, 1.0 * (attempt + 1)))
        return None

    def _pick_spec(
        self,
        specs: list[GpuSpecCombination],
        gpu: int | None,
        cpu: int | None,
        memory_gb: int | None,
    ) -> GpuSpecCombination | None:
        available = [s for s in specs if s.resource_enough]
        if gpu is not None:
            available = [s for s in available if s.gpu == gpu]
        if cpu is not None:
            available = [s for s in available if s.cpu == cpu]
        if memory_gb is not None:
            available = [s for s in available if s.memory_gb == memory_gb]
        return min(available, key=lambda s: (s.gpu, s.cpu, s.memory_gb)) if available else None

    def wait_running(
        self,
        uhost_id: str,
        *,
        should_cancel: Callable[[], bool] | None = None,
        region: str | None = None,
        zone: str | None = None,
    ) -> YouyunInstance:
        deadline = self._clock() + self.boot_timeout
        last_state = ""
        while self._clock() < deadline:
            if should_cancel is not None and should_cancel():
                report = self.cleanup_after_task(uhost_id, stop_first=False, region=region, zone=zone)
                detail = "实例已释放" if report.get("terminated") else f"实例释放失败：{report.get('error') or '未确认'}"
                raise YouyunProvisionCancelled(f"优云实例创建等待期间收到停止请求（{detail}）。")
            instance = self._describe_instance(uhost_id, region=region, zone=zone)
            last_state = instance.state
            if instance.running and instance.ip:
                return instance
            if instance.state in {"Fail", "Error", "Stopped", "Stop", "Terminated", "Deleted"}:
                raise YouyunApiError("DescribeCompShareInstance", None, f"实例 {uhost_id} 进入异常状态 {instance.state}。")
            self._sleep(self.poll_interval)
        raise YouyunApiError("DescribeCompShareInstance", None, f"实例 {uhost_id} 等待 Running 超时（最后状态 {last_state}）。")

    # ---- 查询 ----

    def get(self, uhost_id: str, *, region: str | None = None, zone: str | None = None) -> YouyunInstance | None:
        try:
            return self._describe_instance(uhost_id, region=region, zone=zone)
        except Exception:
            return None

    def ssh_credentials(self, instance: YouyunInstance) -> dict[str, Any]:
        return {
            "host": instance.ip,
            "port": instance.ssh_port or 22,
            "user": instance.ssh_user or "root",
            "password": _decode_password(instance.password_b64),
        }

    # ---- 释放（永不抛异常） ----

    def terminate(self, uhost_id: str, *, region: str | None = None, zone: str | None = None) -> dict[str, Any]:
        """删除一次性实例并等待消失。返回清理报告（含失败原因），不抛异常。"""
        report: dict[str, Any] = {
            "uhost_id": uhost_id,
            "terminated": False,
            "error": None,
            "attempts": 0,
            "checkedAt": None,
        }
        # UCloud/优云 requires a running instance to be stopped before it can
        # be terminated.  This also makes callers that pass stop_first=False
        # safe when a task exits during startup or a provider retry.
        ready_to_terminate = True
        try:
            current = self._describe_instance(uhost_id, region=region, zone=zone)
            if (
                current.state not in _STOPPED_STATES
                and current.state not in _ABSENT_STATES
                and not self._is_shutdown_instance(current)
            ):
                self._stop_and_wait(uhost_id, report, region=region, zone=zone)
        except YouyunApiError as exc:
            # Keep the historical delete call for ret=8 while accepting all
            # provider-specific "not found" forms (including ret=8039) as a
            # safe, idempotent precondition for termination.
            if not self._is_absent_error(exc):
                report["error"] = exc.message
                ready_to_terminate = False
        except Exception as exc:
            # Some SDK/transport adapters wrap a provider "not found" response
            # in a generic OSError/RuntimeError instead of YouyunApiError.  A
            # missing instance is already in the desired terminal state and
            # must remain safe to clean up idempotently.
            if not self._is_absent_error(exc):
                report["error"] = str(exc) or type(exc).__name__
                ready_to_terminate = False
        if not ready_to_terminate:
            report["checkedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            return report
        deadline = self._clock() + self.terminate_timeout
        for attempt in range(1, self.cleanup_attempts + 1):
            report["attempts"] = attempt
            try:
                self._delete_instance(uhost_id, region=region, zone=zone)
            except Exception as exc:  # provider outages must remain recoverable
                if self._is_absent_error(exc):
                    report["terminated"] = True
                    report["error"] = None
                    report["checkedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                    return report
                report["error"] = str(exc) or type(exc).__name__
                logger.error("优云实例 %s 删除指令失败：%s", uhost_id, exc)
                if self._is_operation_in_progress(exc):
                    report["retryable"] = True
                    if isinstance(exc, YouyunApiError):
                        report["providerCode"] = exc.ret_code
                    # 8903 means the provider is still applying a previous
                    # stop/delete operation. Retry within this cleanup call
                    # instead of abandoning the billable instance immediately.
                    if attempt < self.cleanup_attempts:
                        self._sleep(self.poll_interval)
                        continue
                    report["checkedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                    return report
            while self._clock() < deadline:
                report["checkedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                try:
                    current = self._describe_instance(uhost_id, region=region, zone=zone)
                except Exception as exc:  # transient provider errors are not proof of deletion
                    if self._is_absent_error(exc):
                        report["terminated"] = True
                        report["error"] = None
                        return report
                    report["error"] = report["error"] or str(exc) or type(exc).__name__
                    self._sleep(self.poll_interval)
                    continue
                if current.state in _ABSENT_STATES:
                    report["terminated"] = True
                    report["error"] = None
                    return report
                self._sleep(self.poll_interval)
            if report["terminated"]:
                return report
            if attempt < self.cleanup_attempts:
                deadline = self._clock() + self.terminate_timeout
        if report["error"] is None:
            report["error"] = f"删除后 {self.terminate_timeout}s 内实例仍存在。"
        report["checkedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        return report

    @staticmethod
    def _is_shutdown_instance(instance: YouyunInstance) -> bool:
        """Treat provider domain shutdown markers as safe to terminate directly."""
        raw = instance.raw if isinstance(instance.raw, dict) else {}
        domain_state = str(raw.get("UHostDomainSate") or raw.get("UHostDomainState") or "").upper()
        return domain_state in {"DOMAIN_SHUT_OFF", "DOMAIN_STOPPED", "SHUTOFF"} or bool(raw.get("StopTime"))

    @staticmethod
    def _is_absent_error(exc: BaseException) -> bool:
        if isinstance(exc, YouyunApiError):
            # SDK payloads normally decode RetCode as an integer, but some
            # gateways/proxies preserve it as a string.  Normalize both forms
            # so idempotent cleanup does not misclassify an already-gone host.
            if str(exc.ret_code or "").strip() in {"8", "404", "8039"}:
                return True
            detail = exc.message
        else:
            detail = str(exc)
        normalized = str(detail or "").casefold()
        if "resource not exist" in normalized or "资源不存在" in normalized or "实例不存在" in normalized:
            return True
        # The provider often inserts the resource ID between the subject and
        # the not-found phrase, e.g. "实例 cpod-... 不存在". Treat that form as
        # the same idempotent terminal condition.
        return bool(re.search(r"(?:实例|资源)\s+[^\s]+\s+不存在", normalized))

    def _is_operation_in_progress(self, exc: BaseException) -> bool:
        if isinstance(exc, YouyunApiError):
            detail = f"{exc.message} {exc.ret_code}".casefold()
        else:
            detail = str(exc).casefold()
        return (
            "operationinprogress" in detail
            or "operation in progress" in detail
            or "操作进行中" in detail
            or "uhost is in the task" in detail
            or " 8903" in f" {detail}"
        )

    def _stop_and_wait(
        self,
        uhost_id: str,
        report: dict[str, Any] | None = None,
        *,
        region: str | None = None,
        zone: str | None = None,
    ) -> None:
        """Stop an instance, tolerate provider operation conflicts, then wait for Stopped."""
        last_error: str | None = None
        for attempt in range(1, self.cleanup_attempts + 1):
            try:
                self._stop_instance(uhost_id, region=region, zone=zone)
                if report is not None:
                    report.setdefault("steps", []).append({"step": "stop", "ok": True, "attempt": attempt})
                break
            except Exception as exc:
                last_error = str(exc) or type(exc).__name__
                if report is not None:
                    report.setdefault("steps", []).append({"step": "stop", "ok": False, "attempt": attempt, "error": last_error})
                # A concurrent deletion can make the stop call report a
                # generic transport/runtime error rather than YouyunApiError.
                # The desired state is already reached, so do not retry or
                # surface a false cleanup failure.
                if self._is_absent_error(exc):
                    return
                if not self._is_operation_in_progress(exc) or attempt >= self.cleanup_attempts:
                    raise
                # The previous action may still be completing. Querying before
                # retrying avoids issuing duplicate stop operations.
                try:
                    current = self._describe_instance(uhost_id, region=region, zone=zone)
                    if current.state in _STOPPED_STATES:
                        break
                except Exception:
                    pass
                self._sleep(self.poll_interval)
        deadline = self._clock() + self.terminate_timeout
        last_state = ""
        while self._clock() < deadline:
            try:
                current = self._describe_instance(uhost_id, region=region, zone=zone)
            except Exception as exc:
                if self._is_absent_error(exc):
                    return
                if isinstance(exc, YouyunApiError):
                    last_error = exc.message
                    self._sleep(self.poll_interval)
                    continue
                # Preserve the previous behavior for unexpected transport or
                # programming errors while accepting generic not-found errors.
                raise
            last_state = current.state
            if current.state in _STOPPED_STATES:
                return
            if current.state in _ABSENT_STATES:
                return
            self._sleep(self.poll_interval)
        raise RuntimeError(last_error or f"实例 {uhost_id} 停机超时（最后状态 {last_state}）。")

    def cleanup_after_task(
        self,
        uhost_id: str,
        *,
        stop_first: bool = True,
        region: str | None = None,
        zone: str | None = None,
    ) -> dict[str, Any]:
        """任务收尾清理：可选先停机再删除，任何失败都记录进报告。"""
        report: dict[str, Any] = {
            "uhost_id": uhost_id,
            "terminated": False,
            "error": None,
            "attempts": 0,
            "checkedAt": None,
            "steps": [],
        }
        if stop_first:
            try:
                self._stop_and_wait(uhost_id, report, region=region, zone=zone)
            except Exception as exc:
                # 停机失败不阻断删除，仅记录
                report["error"] = str(exc)
        term = self.terminate(uhost_id, region=region, zone=zone)
        report["terminated"] = term["terminated"]
        report["error"] = term["error"]
        report["attempts"] = term["attempts"]
        report["checkedAt"] = term["checkedAt"]
        return report

    @staticmethod
    def _instance_created_epoch(instance: YouyunInstance) -> float | None:
        """Normalize provider creation timestamps to seconds since epoch."""
        raw = instance.raw if isinstance(instance.raw, dict) else {}
        value: Any = None
        for key in ("CreateTime", "CreatedAt", "CreateAt", "CreatedTime"):
            if raw.get(key) not in (None, ""):
                value = raw.get(key)
                break
        if value in (None, ""):
            return None
        if isinstance(value, (int, float)):
            timestamp = float(value)
            return timestamp / 1000.0 if timestamp > 10_000_000_000 else timestamp
        text = str(value).strip()
        try:
            timestamp = float(text)
            return timestamp / 1000.0 if timestamp > 10_000_000_000 else timestamp
        except ValueError:
            pass
        try:
            parsed = _datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_datetime.timezone.utc)
        return parsed.timestamp()

    def cleanup_orphaned_instances(
        self,
        *,
        active_instance_ids: set[str] | frozenset[str],
        grace_seconds: float = 600.0,
        now: float | None = None,
        prefixes: tuple[str, ...] = _PLATFORM_INSTANCE_PREFIXES,
    ) -> list[dict[str, Any]]:
        """Release old platform-owned instances absent from active task markers.

        Instances with unknown creation time are deliberately skipped. This keeps
        a transiently incomplete provider response from deleting a user's resource.
        """
        try:
            instances = self.client.describe_instances()
        except Exception as exc:  # provider query failures are retried by watchdog
            logger.warning("优云孤立实例巡检查询失败：%s", exc)
            return []
        active = {str(item) for item in active_instance_ids if str(item).strip()}
        cutoff = (time.time() if now is None else float(now)) - max(0.0, float(grace_seconds))
        reports: list[dict[str, Any]] = []
        for instance in instances:
            instance_id = str(instance.uhost_id or "")
            name = str(instance.name or "")
            if not instance_id or instance_id in active or not name.startswith(prefixes):
                continue
            created = self._instance_created_epoch(instance)
            if created is None or created > cutoff:
                continue
            report = self.cleanup_after_task(instance_id, stop_first=False)
            reports.append(report)
            if report.get("terminated"):
                logger.info("优云孤立实例已释放：%s（%s）", instance_id, name)
            else:
                logger.warning("优云孤立实例释放失败：%s（%s）", instance_id, report.get("error"))
        return reports
