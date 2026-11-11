"""优云远程 LLaMA-Factory 训练编排器。

生命周期（需求 10.2 / 29 节）：
创建一次性实例 → SSH 就绪 → 安装 LLaMA-Factory → 上传数据集与 YAML →
远程启动训练 → SSH 轮询日志回传（日志 + 指标，解析逻辑与本地执行器一致）→
下载产物到本地镜像 → 文本训练在有 Adapter 时保留实例等待效果对比；
失败、无产物停止以及视觉训练统一清理并核验实例已删除。

sink 协议与 backend.store 一致：append_log / append_metric / update /
is_cancel_requested / metrics。凭据与实例密码不写入日志。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import posixpath
import re
import socket
import threading
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from backend.providers.youyun.log_follow import RemoteLogCollector
from backend.model_storage import adapter_is_complete, iter_model_files

from backend.store import (
    NUM_EPOCHS_PATTERN,
    TOTAL_STEPS_PATTERN,
    infer_metric_step,
    parse_trainer_values,
)

from .instance_manager import YouyunInstanceManager, YouyunProvisionCancelled
from .models import YouyunInstance
from .ssh import YouyunSSHClient
from .resource_health import find_uhost_fallback

logger = logging.getLogger(__name__)

_INSTALL_COMMAND = (
    "mkdir -p /root/cppt; CUDA_PATH_FILE=/root/cppt/cuda-library-path; BASE_PY=''; CORE_PY=''; REUSE_PY=''; PY=''; "
    # 优云 CUDA 镜像的 torch 位于 miniconda env（如 /usr/local/miniconda3/envs/py312）
    "for c in /usr/local/miniconda3/envs/*/bin/python /opt/conda/envs/*/bin/python "
    "/opt/conda/bin/python /root/miniconda3/envs/*/bin/python /root/miniconda3/bin/python "
    "/usr/local/bin/python3 /usr/bin/python3; do "
    "if [ -x \"$c\" ] && \"$c\" -c 'import torch' >/dev/null 2>&1; then "
    "if [ -z \"$BASE_PY\" ]; then BASE_PY=\"$c\"; fi; "
    "if [ -z \"$CORE_PY\" ] && \"$c\" -c \"import torch, torchvision, torchaudio; "
    "v=tuple(int(x) for x in torch.__version__.split('+')[0].split('.')[:2]); "
    "assert v < (2, 9); assert torch.cuda.is_available()\" >/dev/null 2>&1; then CORE_PY=\"$c\"; fi; "
    "if \"$c\" -c \"import torch, torchvision, torchaudio, transformers, llamafactory, bitsandbytes, modelscope; "
    "v=tuple(int(x) for x in torch.__version__.split('+')[0].split('.')[:2]); "
    "assert v < (2, 9); assert torch.cuda.is_available()\" >/dev/null 2>&1; then REUSE_PY=\"$c\"; break; fi; fi; done; "
    "if [ -n \"$REUSE_PY\" ]; then "
    "PY=\"$REUSE_PY\"; BASE_PREFIX=$(dirname \"$(dirname \"$PY\")\"); "
    "CUDA_LIBS=$(find \"$BASE_PREFIX/lib\" -type d -path '*/site-packages/nvidia/*/lib' -print 2>/dev/null | tr '\\n' ':'); "
    "echo \"视觉训练环境探测通过：$PY\"; "
    "elif [ -n \"$CORE_PY\" ]; then "
    "PY=\"$CORE_PY\"; BASE_PREFIX=$(dirname \"$(dirname \"$PY\")\"); "
    "CUDA_LIBS=$(find \"$BASE_PREFIX/lib\" -type d -path '*/site-packages/nvidia/*/lib' -print 2>/dev/null | tr '\\n' ':'); "
    "export LD_LIBRARY_PATH=\"$CUDA_LIBS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}\" && "
    "\"$PY\" -m pip --version >/dev/null 2>&1 || \"$PY\" -m ensurepip --upgrade; "
    "TORCH_VER=$(\"$PY\" -c \"import torch; print(torch.__version__.split('+')[0])\"); "
    "VISION_VER=$(\"$PY\" -c \"import torchvision; print(torchvision.__version__.split('+')[0])\"); "
    "AUDIO_VER=$(\"$PY\" -c \"import torchaudio; print(torchaudio.__version__.split('+')[0])\"); "
    "printf 'torch==%s\\ntorchvision==%s\\ntorchaudio==%s\\n' \"$TORCH_VER\" \"$VISION_VER\" \"$AUDIO_VER\" > /root/cppt/torch-constraints.txt; "
    "\"$PY\" -m pip install --no-cache-dir -U --constraint /root/cppt/torch-constraints.txt "
    "llamafactory modelscope tensorboard bitsandbytes && "
    "echo \"已复用优云 PyTorch 环境并补齐视觉训练依赖：$PY\"; "
    "elif [ -n \"$BASE_PY\" ]; then "
    "PY=\"$BASE_PY\"; BASE_PREFIX=$(dirname \"$(dirname \"$PY\")\"); "
    "ENV_ROOT=/root/cppt/venv; "
    "if [ ! -x \"$ENV_ROOT/bin/python\" ]; then \"$PY\" -m venv \"$ENV_ROOT\"; fi; "
    "PY=\"$ENV_ROOT/bin/python\"; "
    "\"$PY\" -m pip --version >/dev/null 2>&1 || \"$PY\" -m ensurepip --upgrade; "
    "\"$PY\" -m pip install --no-cache-dir -U pip wheel && "
    # LLaMA-Factory currently rejects torch 2.9+ for visual Conv3D models.
    # Install the compatible torch into the isolated environment so the image
    # system environment is not uninstalled or mutated in place.
    "\"$PY\" -m pip install --no-cache-dir --ignore-installed --no-deps 'torch==2.8.0' "
    "'torchvision==0.23.0' 'torchaudio==2.8.0' && "
    "printf 'torch==2.8.0\\ntorchvision==0.23.0\\ntorchaudio==2.8.0\\n' > /root/cppt/torch-constraints.txt && "
    # Keep CUDA libraries bundled in the isolated environment ahead of any
    # incompatible system CUDA runtime when importing torchaudio/bitsandbytes.
    "CUDA_LIBS=$(find \"$ENV_ROOT/lib\" \"$BASE_PREFIX/lib\" -type d -path '*/site-packages/nvidia/*/lib' -print 2>/dev/null | tr '\\n' ':'); "
    "export LD_LIBRARY_PATH=\"$CUDA_LIBS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}\" && "
    # Resolve LLaMA-Factory's runtime dependencies (notably transformers) while
    # constraining pip from replacing the compatible visual-training torch.
    "\"$PY\" -m pip install --no-cache-dir -U --constraint /root/cppt/torch-constraints.txt "
    "llamafactory modelscope tensorboard bitsandbytes; "
    "else echo 'no compatible torch python found' >&2; false; fi; "
    # Single-card LLaMA-Factory does not use DeepSpeed. Some community
    # images ship the optional package without the CUDA toolkit's nvcc binary;
    # importing transformers/accelerate then fails while checking compatibility.
    "if [ -n \"$PY\" ] && ! command -v nvcc >/dev/null 2>&1 && \"$PY\" -c 'import importlib.util; raise SystemExit(0 if importlib.util.find_spec(\"deepspeed\") else 1)' >/dev/null 2>&1; then "
    "echo 'deepspeed detected without nvcc; removing optional dependency'; \"$PY\" -m pip uninstall -y deepspeed >/dev/null 2>&1 || true; fi"
)
_PYTHON_PATH_FILE = "/root/cppt/python-path"
_CUDA_LIBRARY_PATH_FILE = "/root/cppt/cuda-library-path"

# PID files are plain text and can outlive a crashed installer.  Before
# signalling a PID, verify that it still belongs to our detached installer
# script and (when available) to the active generation.  This avoids killing a
# recycled PID belonging to an unrelated process on the shared instance.
_INSTALL_PID_GUARD = (
    "is_env_install_pid() { "
    "p=\"$1\"; expected=\"$2\"; "
    "case \"$p\" in ''|*[!0-9]*) return 1;; esac; "
    "[ \"$p\" -gt 1 ] 2>/dev/null || return 1; "
    "[ -r \"/proc/$p/cmdline\" ] || return 1; "
    "cmd=$(tr '\\0' ' ' < \"/proc/$p/cmdline\" 2>/dev/null || true); "
    "case \"$cmd\" in *\"/tmp/env-install.sh\"*) ;; *) return 1;; esac; "
    "if [ -n \"$expected\" ]; then "
    "case \"$cmd\" in *\"/tmp/env-install.sh $expected\"*) ;; *) return 1;; esac; "
    "fi; return 0; }; "
    "signal_env_install_pid() { "
    "p=\"$1\"; expected=\"$2\"; "
    "if is_env_install_pid \"$p\" \"$expected\"; then "
    "pgid=$(awk '{print $5}' \"/proc/$p/stat\" 2>/dev/null || true); "
    "if [ \"$pgid\" = \"$p\" ]; then kill -TERM -$p 2>/dev/null || true; fi; "
    "kill -TERM $p 2>/dev/null || true; "
    "fi; }; "
    "force_kill_env_install_pid() { "
    "p=\"$1\"; expected=\"$2\"; "
    "if is_env_install_pid \"$p\" \"$expected\"; then "
    "pgid=$(awk '{print $5}' \"/proc/$p/stat\" 2>/dev/null || true); "
    "if [ \"$pgid\" = \"$p\" ]; then kill -KILL -$p 2>/dev/null || true; fi; "
    "kill -KILL $p 2>/dev/null || true; "
    "fi; }; "
)
_MODEL_CONTEXT_KEYS = (
    "max_position_embeddings",
    "max_seq_len",
    "max_sequence_length",
    "seq_length",
    "max_context_length",
)


class _RemoteStopRequested(RuntimeError):
    """内部控制流：用户在远程准备阶段请求停止。"""


class _GpuUnavailable(RuntimeError):
    """优云实例已创建，但 GPU 驱动不可用，无法承载训练。"""


class _LogParser:
    """与本地执行器 events() 相同的单行解析（总步数/轮数/指标）。"""

    def __init__(self, logging_steps: int) -> None:
        self.total_steps: int | None = None
        self.total_epochs: float | None = None
        self.last_train_step = 0
        self.logging_steps = max(1, logging_steps)

    def parse(self, text: str) -> tuple[str, dict[str, Any]]:
        """返回 (event_type, payload)；payload 为指标时含 step/total_steps/progress。"""
        steps_match = TOTAL_STEPS_PATTERN.search(text)
        if steps_match:
            self.total_steps = int(steps_match.group(1).replace(",", ""))
        epochs_match = NUM_EPOCHS_PATTERN.search(text)
        if epochs_match:
            self.total_epochs = float(epochs_match.group(1))
        values = parse_trainer_values(text)
        if values:
            step = infer_metric_step(
                values,
                total_steps=self.total_steps,
                total_epochs=self.total_epochs,
                last_train_step=self.last_train_step,
                logging_steps=self.logging_steps,
            )
            if "loss" in values:
                self.last_train_step = step
            payload: dict[str, Any] = {**values, "step": step}
            if self.total_steps:
                payload["total_steps"] = self.total_steps
                payload["progress"] = min(95, int(step * 95 / self.total_steps))
            return "metric", payload
        return "log", {"raw": text}


def _posix_quote(text: str) -> str:
    """POSIX 单引号转义（用于服务端控制的脚本片段）。"""
    return "'" + text.replace("'", "'\\''") + "'"


_TASK_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _validate_task_id(task_id: str) -> str:
    if not _TASK_ID_PATTERN.fullmatch(task_id):
        raise ValueError("taskId 包含不安全字符。")
    return task_id


def _load_yaml_dict(path: Path) -> dict[str, Any]:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _mask_host(host: str | None) -> str:
    """日志中只保留 IP 的前三段，避免把远程地址写入任务详情。"""
    if not host:
        return "<unknown>"
    parts = host.split(".")
    return ".".join([*parts[:3], "x"]) if len(parts) == 4 else "<present>"


def _connection_error(exc: Exception, stage: str) -> tuple[str, bool]:
    """把底层异常归类为可展示阶段和是否值得重试。"""
    name = type(exc).__name__.lower()
    detail = str(exc).strip() or name
    lowered = detail.lower()
    if "transport shut down or saw eof" in lowered and YouyunSSHClient._is_disconnect_error(exc):
        return "SSH 认证响应前连接被关闭（transport EOF）", True
    if "auth" in name or "authentication" in lowered or "permission denied" in lowered:
        return f"认证失败：{detail}", False
    if "hostkey" in name or "bad host key" in lowered:
        return f"主机密钥校验失败：{detail}", False
    if stage == "tcp":
        return f"TCP 连接失败：{detail}", True
    if "banner" in lowered or "eof" in name or "eof" in lowered:
        return f"SSH 协议握手未完成：{detail}", True
    if "timeout" in name or "timed out" in lowered or "connection reset" in lowered or "connection refused" in lowered:
        return f"SSH 连接失败：{detail}", True
    # Paramiko 的 SSHException 还可能包装云端 sshd 尚未就绪的瞬态错误。
    return f"SSH 连接失败：{detail}", True


def _exception_detail(exc: BaseException) -> str:
    """保留空消息异常的类型，避免任务详情出现空错误。"""
    detail = str(exc).strip()
    return detail or type(exc).__name__


def _is_ssh_setup_failure(exc: BaseException) -> bool:
    """Only transient SSH setup failures may trigger one replacement instance."""
    text = str(exc).casefold()
    if any(marker in text for marker in ("认证失败", "authentication failed", "permission denied", "主机密钥")):
        return False
    return any(
        marker in text
        for marker in (
            "ssh 连接超时",
            "ssh 连接失败",
            "ssh 协议握手",
            "protocol banner",
            "connection reset",
            "connection refused",
            "channel closed",
            "eof",
        )
    )


def _failure_code(exc: BaseException, phase: str) -> str:
    text = str(exc).casefold()
    if "优云配置缺少" in text or "youyun_public_key" in text:
        return "youyun_config_missing"
    if "226645" in text and "createcompshareinstance" in text:
        return "youyun_create_service_unavailable"
    if isinstance(exc, _GpuUnavailable) or "failed to initialize nvml" in text or (
        phase == "gpu_probe" and "unknown error" in text
    ):
        return "gpu_unavailable"

    timeout_markers = ("超时", "timeout", "timed out")
    if phase == "ssh_connect" and any(marker in text for marker in timeout_markers):
        return "ssh_handshake_timeout"
    if phase == "training" and (
        isinstance(exc, TimeoutError) or any(marker in text for marker in timeout_markers)
    ):
        return "training_timeout"

    # Share the typed SSH/network classifier; disk and permission failures
    # must keep their own phase rather than masquerading as a broken connection.
    if YouyunSSHClient._is_disconnect_error(exc):
        return "ssh_handshake_eof" if phase == "ssh_connect" else "ssh_channel_disconnected"

    if phase == "model_resolution" or "未找到优云本地基座模型" in text:
        return "base_model_unavailable"
    if "python-path" in text or "解释器路径" in text:
        return "dependency_python_path_missing"
    if "启动失败" in text or "launch-failed" in text:
        return "dependency_install_launch_failed"
    if phase == "dependency_install" or "依赖安装失败" in text:
        return "dependency_install_failed"
    if phase == "artifact_collection" or "产物" in text:
        return "artifact_collection_failed"
    return "remote_training_failed"


_FAILURE_MESSAGES = {
    "youyun_config_missing": "优云配置不完整。",
    "youyun_create_service_unavailable": "优云实例创建服务暂不可用。",
    "ssh_handshake_eof": "优云实例 SSH 握手被远端关闭。",
    "ssh_handshake_timeout": "优云实例 SSH 握手超时。",
    "ssh_channel_disconnected": "优云实例 SSH 通道中断。",
    "gpu_unavailable": "优云实例 GPU 驱动不可用。",
    "base_model_unavailable": "优云视觉基座模型不可用。",
    "dependency_python_path_missing": "优云训练解释器路径缺失。",
    "dependency_install_launch_failed": "优云视觉训练依赖安装任务启动失败。",
    "dependency_install_failed": "优云视觉训练依赖安装失败。",
    "training_timeout": "优云视觉训练超时。",
    "artifact_collection_failed": "优云视觉训练产物回传失败。",
    "remote_training_failed": "优云远程视觉训练失败。",
}

_FAILURE_ACTIONS = {
    "youyun_config_missing": "补齐优云密钥、区域和可用区配置后重试。",
    "youyun_create_service_unavailable": "库存与价格预检已通过，但优云创建接口暂不可用；已停止重复创建，稍后重试或联系优云确认区域/账户创建权限。",
    "ssh_handshake_eof": "等待实例 SSH 服务就绪或更换实例后重试。",
    "ssh_handshake_timeout": "已释放当前实例；建议更换优云实例或镜像后重试。",
    "ssh_channel_disconnected": "检查优云实例状态和 SSH 日志后重试。",
    "gpu_unavailable": "已释放当前实例；更换可用区、GPU 规格或带正确 NVIDIA 驱动的镜像后重试。",
    "base_model_unavailable": "将 Qwen-VL 基座模型预置到镜像或共享缓存。",
    "dependency_python_path_missing": "重新执行环境安装；若仍失败，请更换预装 Python 与 LLaMA-Factory 的视觉训练镜像。",
    "dependency_install_launch_failed": "检查镜像是否包含 nohup/bash，并优先使用预装视觉训练环境的镜像。",
    "dependency_install_failed": "使用预装 LLaMA-Factory、Torch 和视觉依赖的镜像。",
    "training_timeout": "降低数据规模或检查远程训练日志后重试。",
    "artifact_collection_failed": "确认远端输出目录和磁盘空间后重试。",
    "remote_training_failed": "检查优云镜像、模型和远程训练日志后重试。",
}


class YouyunTrainingRunner:
    def __init__(
        self,
        *,
        manager: YouyunInstanceManager,
        sink: Any,
        ssh_factory: Callable[[], YouyunSSHClient] = YouyunSSHClient,
        remote_root: str = "/root/cppt",
        mirror_root: Path | None = None,
        prepare_local: Callable[[Mapping[str, Any]], Any] | None = None,
        collect_local: Callable[[Mapping[str, Any]], list[dict[str, Any]]] | None = None,
        on_retained: Callable[[str, Mapping[str, Any]], Any] | None = None,
        retain_for_comparison: bool = True,
        poll_interval: float = 5.0,
        ssh_ready_timeout: float | None = None,
        ssh_connect_timeout: float | None = None,
        install_timeout: float = 3600.0,
        training_timeout: float | None = None,
        training_poll_retries: int | None = None,
        training_poll_backoff: float | None = None,
        youyun_base_model_root: str | None = None,
        youyun_base_model_path: str | None = None,
        ssh_instance_retries: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.manager = manager
        self.sink = sink
        self.ssh_factory = ssh_factory
        self.remote_root = remote_root.rstrip("/")
        self.mirror_root = (mirror_root or Path(os.environ.get("XUNTUI_REMOTE_MIRROR_ROOT", "./runtime/remote-jobs"))).expanduser().resolve()
        self.prepare_local = prepare_local
        self.collect_local = collect_local
        self.poll_interval = poll_interval
        self.ssh_ready_timeout = (
            ssh_ready_timeout
            if ssh_ready_timeout is not None
            else float(os.environ.get("YOUYUN_SSH_READY_TIMEOUT_SECONDS", "180"))
        )
        self.ssh_connect_timeout = (
            ssh_connect_timeout
            if ssh_connect_timeout is not None
            else float(os.environ.get("YOUYUN_SSH_CONNECT_TIMEOUT_SECONDS", "20"))
        )
        self.install_timeout = install_timeout
        self.training_timeout = training_timeout if training_timeout is not None else float(os.environ.get("YOUYUN_TRAINING_TIMEOUT_SECONDS", 86400))
        self.training_poll_retries = max(
            0,
            training_poll_retries
            if training_poll_retries is not None
            else int(os.environ.get("YOUYUN_TRAINING_POLL_RETRIES", "4")),
        )
        self.training_poll_backoff = max(
            0.0,
            training_poll_backoff
            if training_poll_backoff is not None
            else float(os.environ.get("YOUYUN_TRAINING_POLL_BACKOFF_SECONDS", "2")),
        )
        self.youyun_base_model_root = (
            youyun_base_model_root
            if youyun_base_model_root is not None
            else os.environ.get(
                "YOUYUN_BASE_MODEL_ROOT",
                "/model:/root/models:/models:/workspace/models:/mnt/models:/data/models",
            )
        ).strip()
        self.youyun_base_model_path = (
            youyun_base_model_path
            if youyun_base_model_path is not None
            else os.environ.get("YOUYUN_BASE_MODEL_PATH", "")
        ).strip()
        self.ssh_instance_retries = max(
            0,
            int(ssh_instance_retries)
            if ssh_instance_retries is not None
            else int(os.environ.get("YOUYUN_SSH_INSTANCE_RETRIES", "1")),
        )
        self._sleep = sleep
        self._clock = clock
        self._stopped = False
        self.on_retained = on_retained
        self.retain_for_comparison = retain_for_comparison
        self._retained_callbacks: set[asyncio.Task[None]] = set()

    # ---- 对外主入口 ----

    async def run(self, task_id: str, task: Mapping[str, Any]) -> str:
        _validate_task_id(task_id)
        self._stopped = False
        instance: YouyunInstance | None = None
        # ``create_one_shot`` invokes ``on_created`` before it waits for the
        # instance to become Running.  Keep that handle locally as well as in
        # the task marker so a cancellation or callback failure cannot bypass
        # the final cleanup when the call never returns an instance object.
        provisioned_instance_id: str | None = None
        provision_cancelled = threading.Event()
        provision_holder: dict[str, str] = {}
        cleanup_succeeded: set[str] = set()
        cleanup_lock = asyncio.Lock()
        ssh: YouyunSSHClient | None = None
        stop_requested = False
        keep_instance = False
        phase = "instance_provision"

        async def cleanup_instance_once(instance_id: str) -> bool:
            normalized = str(instance_id or "").strip()
            if not normalized:
                return True
            if normalized in cleanup_succeeded:
                return True
            async with cleanup_lock:
                if normalized in cleanup_succeeded:
                    return True
                cleaned = await self._cleanup_instance(task_id, normalized)
                if cleaned:
                    cleanup_succeeded.add(normalized)
                return cleaned

        def consume_background_task(background: asyncio.Task[Any]) -> None:
            if background.cancelled():
                return
            try:
                background.exception()
            except Exception:
                # Detached cleanup is best effort; the task marker/watchdog is
                # the durable retry path if the event loop closes first.
                pass

        async def drain_cancelled_provision(provision_task: asyncio.Task[Any]) -> None:
            result: Any = None
            try:
                # Shield the worker future so cancellation of ``run`` cannot
                # cancel the thread that may still create a billable instance.
                result = await asyncio.shield(provision_task)
            except BaseException:
                pass
            instance_id = provision_holder.get("instanceId") or str(getattr(result, "uhost_id", "") or "")
            if instance_id:
                await cleanup_instance_once(instance_id)

        try:
            resource = dict(task.get("youyunResource") or {})
            for replacement_attempt in range(self.ssh_instance_retries + 1):
                self.sink.update(task_id, current_step="正在检查库存并创建优云 GPU 实例")

                def record_created_instance(uhost_id: str) -> None:
                    nonlocal provisioned_instance_id
                    normalized = str(uhost_id or "").strip()
                    provisioned_instance_id = normalized or None
                    if normalized:
                        provision_holder["instanceId"] = normalized
                    self._record_instance_id(task_id, uhost_id, resource=resource)

                try:
                    provision_task = asyncio.create_task(
                        asyncio.to_thread(
                            self.manager.create_one_shot,
                            task_id,
                            gpu=resource.get("gpuCount"),
                            cpu=resource.get("cpuCores"),
                            memory_gb=resource.get("memoryGb"),
                            gpu_type=resource.get("gpuType"),
                            image_id=resource.get("imageId"),
                            charge_type=resource.get("chargeType"),
                            instance_mode=resource.get("instanceMode"),
                            region=resource.get("region"),
                            zone=resource.get("zone"),
                            idempotency_key=f"{task_id}:vision-training:{replacement_attempt}",
                            on_created=record_created_instance,
                            should_cancel=lambda: self.sink.is_cancel_requested(task_id) or provision_cancelled.is_set(),
                        )
                    )
                    try:
                        instance = await asyncio.shield(provision_task)
                    except asyncio.CancelledError:
                        provision_cancelled.set()
                        background = asyncio.create_task(drain_cancelled_provision(provision_task))
                        background.add_done_callback(consume_background_task)
                        raise
                    except Exception as exc:
                        fallback = find_uhost_fallback(self.manager, resource)
                        if fallback is None or replacement_attempt >= self.ssh_instance_retries:
                            raise
                        captured_id = provision_holder.get("instanceId") or provisioned_instance_id
                        if captured_id and not await cleanup_instance_once(captured_id):
                            raise RuntimeError("Pod 创建失败后实例释放未确认，已停止切换 UHost。") from exc
                        provision_holder.pop("instanceId", None)
                        provisioned_instance_id = None
                        self.sink.append_log(task_id, "Pod 创建接口暂不可用，正在切换到 UHost 兜底。")
                        resource = {**resource, **fallback, "attemptedModes": ["pod", "uhost"], "fallback": True}
                        self.sink.update(task_id, youyun_resource=resource, current_step="正在切换 UHost 兜底")
                        continue
                except YouyunProvisionCancelled:
                    self._stopped = True
                    stop_requested = True
                    raise _RemoteStopRequested()
                self.sink.append_log(
                    task_id,
                    f"优云实例已就绪：{instance.uhost_id}（{instance.gpu_type} ×{instance.gpu}，{instance.zone}）",
                )
                self.sink.update(task_id, last_heartbeat_at_epoch=time.time())
                self._record_instance(task_id, instance, state="running")

                if self.sink.is_cancel_requested(task_id):
                    self._stopped = True
                    raise _RemoteStopRequested()
                phase = "ssh_connect"
                try:
                    ssh = await asyncio.to_thread(self._connect_ssh, instance, task_id)
                except Exception as exc:
                    if replacement_attempt >= self.ssh_instance_retries or not _is_ssh_setup_failure(exc):
                        raise
                    self.sink.append_log(
                        task_id,
                        f"SSH 连接失败，正在释放实例 {instance.uhost_id} 并更换优云实例重试（第 {replacement_attempt + 1} 次）。",
                    )
                    if ssh is not None:
                        ssh.close()
                        ssh = None
                    if not await cleanup_instance_once(instance.uhost_id):
                        raise RuntimeError("SSH 连接失败后实例释放未确认，已停止更换实例。") from exc
                    instance = None
                    provisioned_instance_id = None
                    continue
                phase = "gpu_probe"
                try:
                    _, gpu_info = await asyncio.to_thread(
                        self._run_idempotent_query,
                        ssh,
                        "nvidia-smi --query-gpu=name,memory.total --format=csv,noheader || true",
                        60,
                    )
                    if (
                        not gpu_info.strip()
                        or "Failed to initialize NVML" in gpu_info
                        or "Unknown Error" in gpu_info
                    ):
                        raise _GpuUnavailable(gpu_info.strip() or "empty gpu probe")
                except _GpuUnavailable as exc:
                    if replacement_attempt >= self.ssh_instance_retries:
                        raise
                    self.sink.append_log(
                        task_id,
                        f"GPU 不可用，正在释放实例 {instance.uhost_id} 并更换优云实例重试（第 {replacement_attempt + 1} 次）。",
                    )
                    if ssh is not None:
                        ssh.close()
                        ssh = None
                    if not await cleanup_instance_once(instance.uhost_id):
                        raise RuntimeError("GPU 不可用后实例释放未确认，已停止更换实例。") from exc
                    instance = None
                    provisioned_instance_id = None
                    continue
                self.sink.append_log(task_id, f"GPU：{gpu_info.strip().splitlines()[0]}")
                break
            else:
                raise RuntimeError("SSH 连接失败，未能获取可用优云实例。")
            if self.sink.is_cancel_requested(task_id):
                self._stopped = True
                raise _RemoteStopRequested()

            remote_model_path: str | None = None
            model_repo_id = str(task.get("modelRepoId") or "").strip()
            explicit_model_path = str(task.get("remoteModelPath") or "").strip()
            if model_repo_id or explicit_model_path:
                phase = "model_resolution"
                self.sink.update(task_id, current_step="正在检查优云本地基座模型")
                remote_model_path = await asyncio.to_thread(
                    self._resolve_remote_model,
                    task_id,
                    ssh,
                    model_repo_id,
                    explicit_model_path,
                )
            phase = "dependency_install"
            await self._install_environment(task_id, ssh)
            phase = "upload"
            handle = await self._upload_task(
                task_id,
                task,
                ssh,
                remote_model_path=remote_model_path,
            )
            phase = "training"
            returncode = await self._train_and_follow(task_id, task, ssh, handle)

            if self._stopped:
                self.sink.update(task_id, state="stopping", current_step="正在回传训练产物")
                retained = await self._try_collect_artifacts(task_id, task, ssh, handle)
                if self.retain_for_comparison and retained:
                    self._retain_instance(task_id, instance.uhost_id, stopped=True)
                    self._notify_retained(task_id, task)
                    keep_instance = True
                stop_requested = True
                return "stopped"
            if returncode != 0:
                message = f"远程训练进程异常退出，返回码 {returncode}。"
                self.sink.update(
                    task_id,
                    state="failed",
                    current_step="远程训练失败",
                    error=message,
                    phase="training",
                    failure_code="remote_training_failed",
                    failure_message=_FAILURE_MESSAGES["remote_training_failed"],
                    suggested_action=_FAILURE_ACTIONS["remote_training_failed"],
                )
                return "failed"
            phase = "artifact_collection"
            await self._collect_artifacts(task_id, task, ssh, handle)
            retained = self.retain_for_comparison and self._has_comparable_adapter(task_id)
            if retained:
                self._retain_instance(task_id, instance.uhost_id)
                self._notify_retained(task_id, task)
                keep_instance = True
                self.sink.update(task_id, progress=100, current_step="训练完成，等待效果对比（实例待手动释放）")
            else:
                self.sink.update(task_id, progress=100, current_step="训练完成，正在释放优云实例")
            return "completed"
        except _RemoteStopRequested:
            self._stopped = True
            stop_requested = True
            self.sink.update(task_id, state="stopping", current_step="正在清理优云实例")
            return "stopped"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("优云远程训练失败：%s", task_id)
            detail = _exception_detail(exc)
            code = _failure_code(exc, phase)
            message = _FAILURE_MESSAGES[code]
            action = _FAILURE_ACTIONS[code]
            self.sink.append_log(task_id, f"优云远程训练错误（阶段={phase}，失败码={code}）：{detail}")
            # CreateCompShareInstance can fail before returning a provider
            # handle.  In that branch there is no instance to terminate, but
            # the task must still leave the resource lifecycle in a terminal
            # state; otherwise the runtime infers `provisioning/pending` from
            # `provider=youyun` and presents a false cleanup leak.
            no_instance_allocated = instance is None and not provisioned_instance_id
            if no_instance_allocated:
                cleanup_checked_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                self.sink.append_log(task_id, "优云未创建实例，无需释放；资源状态已标记为 released。")
                self.sink.update(
                    task_id,
                    state="failed",
                    current_step="远程训练失败（优云实例未创建）",
                    error=detail,
                    phase=phase,
                    failure_code=code,
                    failure_message=message,
                    suggested_action=action,
                    resource_state="released",
                    cleanup_state="released",
                    cleanup_checked_at=cleanup_checked_at,
                )
            else:
                self.sink.update(
                    task_id,
                    state="failed",
                    current_step="远程训练失败",
                    error=detail,
                    phase=phase,
                    failure_code=code,
                    failure_message=message,
                    suggested_action=action,
                )
            return "failed"
        finally:
            if ssh is not None:
                ssh.close()
            cleanup_instance_id = (
                instance.uhost_id if instance is not None else provisioned_instance_id
            )
            if cleanup_instance_id and not keep_instance:
                await cleanup_instance_once(cleanup_instance_id)
            elif cleanup_instance_id:
                self.sink.append_log(
                    task_id,
                    f"优云实例 {cleanup_instance_id} 暂不释放，等待效果对比完成后人工确认。",
                )
            if stop_requested:
                self.sink.update(
                    task_id,
                    state="stopped",
                    current_step=(
                        "训练已停止，可进行效果对比（实例待手动释放）"
                        if keep_instance
                        else "训练已停止"
                    ),
                    cancel_requested=False,
                    stop_reason="manual",
                )

    # ---- 各阶段 ----

    def _notify_retained(self, task_id: str, task: Mapping[str, Any]) -> None:
        """异步触发保留实例的对比预热；预热失败不改变训练结果。"""
        if self.on_retained is None:
            return

        async def _runner() -> None:
            try:
                callback_task = self.sink.get(task_id) if callable(getattr(self.sink, "get", None)) else None
                await self.on_retained(task_id, callback_task or task)
            except Exception as exc:  # noqa: BLE001 - 预热失败不影响训练结果
                logger.warning("优云对比预热回调失败：%s %s", task_id, exc)
                try:
                    self.sink.append_log(task_id, f"优云对比模型预热失败：{exc}")
                except Exception:  # noqa: BLE001 - 仅用于补充日志
                    pass

        try:
            callback_task = asyncio.get_running_loop().create_task(_runner())
        except RuntimeError:
            return
        self._retained_callbacks.add(callback_task)
        callback_task.add_done_callback(self._retained_callbacks.discard)

    def _has_comparable_adapter(self, task_id: str) -> bool:
        return any(
            item.get("type") == "adapter"
            and (item.get("path") or item.get("remotePath"))
            for item in self._artifacts(task_id)
        )

    def _connect_ssh(self, instance: YouyunInstance, task_id: str | None = None) -> YouyunSSHClient:
        creds = self.manager.ssh_credentials(instance)
        if not creds["host"] or not creds["password"]:
            raise RuntimeError("实例缺少 SSH 连接信息（IP 或密码为空）。")
        deadline = self._clock() + self.ssh_ready_timeout
        last_error: Exception | None = None
        last_stage = "准备连接"
        attempts = 0
        started_at = self._clock()
        while self._clock() < deadline:
            if task_id and self.sink.is_cancel_requested(task_id):
                self._stopped = True
                raise _RemoteStopRequested()

            # Running 后再次查询，避免优云稍后才补齐/变更公网 SSH 转发信息。
            get_instance = getattr(self.manager, "get", None)
            if callable(get_instance):
                try:
                    refreshed = get_instance(instance.uhost_id)
                except Exception:  # noqa: BLE001 - 保留旧凭据继续重试
                    refreshed = None
                if refreshed is not None:
                    refreshed_creds = self.manager.ssh_credentials(refreshed)
                    if refreshed_creds.get("host") and refreshed_creds.get("password"):
                        creds = refreshed_creds

            attempts += 1
            endpoint = f"{_mask_host(str(creds['host']))}:{int(creds.get('port') or 22)}"
            elapsed = max(0, int(self._clock() - started_at))
            if task_id:
                self.sink.update(task_id, current_step=f"等待 SSH 连接（第 {attempts} 次，{endpoint}，已等待 {elapsed}s）")

            # 真实 Paramiko 路径先探测 TCP；注入的测试/集成工厂不做外部网络探测。
            if self.ssh_factory is YouyunSSHClient:
                try:
                    with socket.create_connection(
                        (str(creds["host"]), int(creds.get("port") or 22)),
                        timeout=min(self.ssh_connect_timeout, max(1.0, deadline - self._clock())),
                    ):
                        pass
                except Exception as exc:  # noqa: BLE001 - 端口尚未可达时继续等待
                    last_stage = "TCP 连接"
                    last_error = exc
                    message, retryable = _connection_error(exc, "tcp")
                    if task_id:
                        self.sink.append_log(task_id, f"SSH 第 {attempts} 次（{endpoint}）：{message}")
                    if not retryable:
                        raise RuntimeError(f"SSH 连接失败（{endpoint}）：{message}") from exc
                    self._sleep(self.poll_interval)
                    continue

            ssh = self._new_ssh_client(task_id)
            try:
                ssh.connect(creds["host"], creds["port"], creds["user"], creds["password"])
                if task_id and attempts > 1:
                    self.sink.append_log(task_id, f"SSH 连接已建立（{endpoint}，第 {attempts} 次）。")
                return ssh
            except Exception as exc:  # noqa: BLE001 - sshd 可能尚未就绪，重试
                last_stage = "SSH 协议握手"
                last_error = exc
                ssh.close()
                message, retryable = _connection_error(exc, "ssh")
                if task_id:
                    self.sink.append_log(task_id, f"SSH 第 {attempts} 次（{endpoint}）：{message}")
                if not retryable:
                    raise RuntimeError(f"SSH 连接失败（{endpoint}）：{message}") from exc
                self._sleep(self.poll_interval)
        endpoint = f"{_mask_host(str(creds['host']))}:{int(creds.get('port') or 22)}"
        waited = max(0, int(self._clock() - started_at))
        detail = str(last_error).strip() if last_error else "未收到 SSH 响应"
        message = f"SSH 连接超时（{endpoint}）：阶段={last_stage}，已等待 {waited}s，尝试 {attempts} 次，最后错误：{detail}"
        if task_id:
            self.sink.append_log(task_id, message)
        raise RuntimeError(message)

    def _new_ssh_client(self, task_id: str | None = None) -> YouyunSSHClient:
        def check_cancelled() -> None:
            if task_id and self.sink.is_cancel_requested(task_id):
                self._stopped = True
                raise _RemoteStopRequested()

        def record_retry(message: str) -> None:
            if task_id:
                self.sink.append_log(task_id, message)
                self.sink.update(task_id, last_heartbeat_at_epoch=time.time())

        try:
            return self.ssh_factory(
                connect_timeout=self.ssh_connect_timeout,
                check_cancelled=check_cancelled,
                on_connection_retry=record_retry,
            )
        except TypeError:
            return self.ssh_factory()

    @staticmethod
    def _model_resolution_command(
        repo_id: str,
        roots: str,
        explicit_path: str = "",
    ) -> str:
        clean_repo = repo_id.strip().strip("/")
        if not clean_repo:
            raise ValueError("视觉基座模型 Repo ID 不能为空。")
        root_values = [value.strip().rstrip("/") for value in roots.split(":") if value.strip()]
        if not root_values and not explicit_path:
            raise ValueError(
                "未配置 YOUYUN_BASE_MODEL_PATH 或 YOUYUN_BASE_MODEL_ROOT，无法使用优云本地基座模型。"
            )
        invalid = [value for value in root_values if not posixpath.isabs(value)]
        if invalid:
            raise ValueError("YOUYUN_BASE_MODEL_ROOT 必须使用优云实例内的绝对路径。")
        if explicit_path and not posixpath.isabs(explicit_path):
            raise ValueError("YOUYUN_BASE_MODEL_PATH 必须使用优云实例内的绝对路径。")

        leaf = clean_repo.rsplit("/", 1)[-1]
        slug = clean_repo.replace("/", "--")
        candidates: list[str] = [explicit_path] if explicit_path else []
        snapshot_roots: list[str] = []
        for root in root_values:
            candidates.extend(
                [
                    posixpath.join(root, clean_repo),
                    posixpath.join(root, slug),
                    posixpath.join(root, leaf),
                    posixpath.join(root, slug, "snapshots", "master"),
                    posixpath.join(root, "models", slug, "snapshots", "master"),
                    posixpath.join(root, f"models--{slug}", "snapshots", "master"),
                ]
            )
            snapshot_roots.extend(
                [
                    posixpath.join(root, slug, "snapshots"),
                    posixpath.join(root, "models", slug, "snapshots"),
                    posixpath.join(root, f"models--{slug}", "snapshots"),
                ]
            )

        unique_candidates = list(dict.fromkeys(candidates))
        candidate_words = " ".join(_posix_quote(value) for value in unique_candidates)
        snapshot_words = " ".join(_posix_quote(value) for value in snapshot_roots)
        checked_roots = ", ".join(
            [value for value in [explicit_path, *root_values] if value]
        )
        snapshot_loop = ""
        if snapshot_words:
            snapshot_loop = (
                f"for snapshots in {snapshot_words}; do "
                "if [ -d \"$snapshots\" ]; then for candidate in \"$snapshots\"/*; do "
                "if is_complete_model \"$candidate\"; then printf 'base-model-path:%s\\n' \"$candidate\"; exit 0; fi; "
                "done; fi; done; "
            )
        return (
            "is_complete_model() { d=\"$1\"; "
            "[ -f \"$d/config.json\" ] && [ -f \"$d/tokenizer_config.json\" ] && "
            "{ [ -f \"$d/preprocessor_config.json\" ] || [ -f \"$d/processor_config.json\" ]; } && "
            "for weight in \"$d\"/*.safetensors \"$d\"/pytorch_model*.bin; do "
            "[ -f \"$weight\" ] && return 0; done; return 1; }; "
            f"for candidate in {candidate_words}; do "
            "if is_complete_model \"$candidate\"; then printf 'base-model-path:%s\\n' \"$candidate\"; exit 0; fi; done; "
            f"{snapshot_loop}"
            f"printf 'base-model-error:no matching complete model under %s\\n' {_posix_quote(checked_roots)}"
        )

    def _resolve_remote_model(
        self,
        task_id: str,
        ssh: YouyunSSHClient,
        repo_id: str,
        explicit_path: str = "",
    ) -> str | None:
        explicit_path = explicit_path.strip()
        if explicit_path and not posixpath.isabs(explicit_path):
            raise ValueError("公共模型库 remotePath 必须使用优云实例内的绝对路径。")
        if not repo_id and explicit_path:
            repo_id = posixpath.basename(explicit_path)
        if not self.youyun_base_model_path and not self.youyun_base_model_root and not explicit_path:
            raise RuntimeError(
                f"未配置优云公共模型库基座模型路径（{repo_id}）。"
                "请选择带 remotePath 的优云公共模型，或配置 YOUYUN_BASE_MODEL_ROOT。"
            )
        command = self._model_resolution_command(
            repo_id,
            self.youyun_base_model_root,
            explicit_path or self.youyun_base_model_path,
        )
        _, output = self._run_idempotent_query(ssh, command, 60, check=False)
        model_path = ""
        error_detail = "未返回模型检查结果"
        for line in output.splitlines():
            if line.startswith("base-model-path:"):
                model_path = line.removeprefix("base-model-path:").strip()
                break
            if line.startswith("base-model-error:"):
                error_detail = line.removeprefix("base-model-error:").strip() or error_detail
        if not model_path or not posixpath.isabs(model_path):
            raise RuntimeError(
                f"未找到优云本地基座模型 {repo_id}：{error_detail}。"
                "请确认模型已存在于优云公共模型库共享挂载目录，并带有有效 remotePath。"
            )
        self.sink.append_log(task_id, f"优云本地基座模型已确认（base-model-path:{model_path}）。")
        return model_path

    async def _stop_remote_install(self, ssh: YouyunSSHClient) -> None:
        await asyncio.to_thread(
            ssh.run,
            "OLD_GEN=$(head -n 1 /tmp/env-install.generation 2>/dev/null || true); "
            + _INSTALL_PID_GUARD
            + "for f in /tmp/env-install.pid /tmp/env-install.launch.pid; do "
            "p=$(head -n 1 \"$f\" 2>/dev/null || true); "
            "signal_env_install_pid \"$p\" \"$OLD_GEN\"; "
            "done; "
            "sleep 1; "
            "for f in /tmp/env-install.pid /tmp/env-install.launch.pid; do "
            "p=$(head -n 1 \"$f\" 2>/dev/null || true); "
            "force_kill_env_install_pid \"$p\" \"$OLD_GEN\"; "
            "done; "
            # Remove legacy symlink aliases only after both processes have
            # been signalled.  This guarantees a new generation cannot be
            # skipped by the idempotent launch guard because an old
            # /tmp/env-install.started link is still present.
            "rm -f /tmp/env-install.done /tmp/env-install.done.tmp /tmp/env-install.exit "
            "/tmp/env-install.exit.tmp /tmp/env-install.pid /tmp/env-install.pid.tmp "
            "/tmp/env-install.launch /tmp/env-install.launch.tmp /tmp/env-install.launch.pid "
            "/tmp/env-install.launch.pid.tmp /tmp/env-install.started /tmp/env-install.log "
            "/tmp/env-install.generation /tmp/env-install.generation.tmp; "
            "find /tmp -maxdepth 1 \\( -type f -o -type l \\) -name 'env-install.*.*' -delete 2>/dev/null || true; true",
            60,
            False,
        )

    @staticmethod
    def _install_launch_command(generation: str, *, force: bool = False) -> str:
        """Build an idempotent detached installer launcher.

        Every attempt gets its own marker namespace.  The legacy marker paths
        remain symlinks to the current namespace so older diagnostics and
        operators still see the same files, while an EXIT trap from an older
        process cannot overwrite the current attempt's state.
        """
        if not re.fullmatch(r"[A-Za-z0-9._-]{8,64}", generation):
            raise ValueError("安装尝试标识包含不安全字符。")
        quoted_generation = _posix_quote(generation)
        reset = "true" if force else "false"
        stop_prefix = ""
        if force:
            # Keep the kill and marker rotation in one remote command.  This
            # closes the window where a delayed old installer can publish a
            # failure marker after the new attempt has started.
            stop_prefix = (
                "OLD_GEN=$(head -n 1 /tmp/env-install.generation 2>/dev/null || true); "
                + _INSTALL_PID_GUARD
                + "for f in /tmp/env-install.pid /tmp/env-install.launch.pid; do "
                "p=$(head -n 1 \"$f\" 2>/dev/null || true); "
                "signal_env_install_pid \"$p\" \"$OLD_GEN\"; "
                "done; sleep 1; "
                "for f in /tmp/env-install.pid /tmp/env-install.launch.pid; do "
                "p=$(head -n 1 \"$f\" 2>/dev/null || true); "
                "force_kill_env_install_pid \"$p\" \"$OLD_GEN\"; "
                "done; "
                "find /tmp -maxdepth 1 \\( -type f -o -type l \\) -name 'env-install.*.*' -delete 2>/dev/null || true; "
            )
        guard = "" if force else "if [ ! -f /tmp/env-install.started ]; then "
        guard_end = "" if force else " fi"
        return (
            stop_prefix
            + f"GEN={quoted_generation}; ROOT=/tmp/env-install.$GEN; RESET={reset}; "
            + guard
            + (
                "if [ \"$RESET\" = true ]; then "
                "rm -f /tmp/env-install.done /tmp/env-install.done.tmp /tmp/env-install.exit "
                "/tmp/env-install.exit.tmp /tmp/env-install.pid /tmp/env-install.pid.tmp "
                "/tmp/env-install.launch /tmp/env-install.launch.tmp /tmp/env-install.launch.pid "
                "/tmp/env-install.launch.pid.tmp /tmp/env-install.log; "
                "fi; "
                # Each generation has its own marker namespace.  Create it
                # before any redirection; otherwise every marker/log write
                # fails and the probe reports `install-dead` with no cause.
                "mkdir -p \"$ROOT\"; "
                "rm -f \"$ROOT.done\" \"$ROOT.done.tmp\" \"$ROOT.exit\" \"$ROOT.exit.tmp\" "
                "\"$ROOT.pid\" \"$ROOT.pid.tmp\" \"$ROOT.launch\" \"$ROOT.launch.tmp\" "
                "\"$ROOT.launch.pid\" \"$ROOT.launch.pid.tmp\" \"$ROOT.log\"; "
                "date -Iseconds > \"$ROOT.started\"; "
                "for name in started done done.tmp exit exit.tmp pid pid.tmp launch launch.tmp launch.pid launch.pid.tmp log; do "
                "rm -f \"/tmp/env-install.$name\"; "
                "ln -s \"$ROOT.$name\" \"/tmp/env-install.$name\" 2>/dev/null || true; "
                "done; "
                "date -Iseconds > /tmp/env-install.started; "
                "printf '%s\\n' \"$GEN\" > /tmp/env-install.generation.tmp && mv -f /tmp/env-install.generation.tmp /tmp/env-install.generation; "
                "BASH=$(command -v bash 2>/dev/null || true); "
                "NOHUP=$(command -v nohup 2>/dev/null || true); "
                "SETSID=$(command -v setsid 2>/dev/null || true); "
                "printf 'generation:%s\\nbash:%s\\nnohup:%s\\nsetsid:%s\\n' \"$GEN\" \"$BASH\" \"$NOHUP\" \"$SETSID\" > \"$ROOT.launch.tmp\"; "
                ": > \"$ROOT.log\"; : > /tmp/env-install.log; "
                "if [ -z \"$BASH\" ]; then "
                "printf 'launch-failed\\n' > \"$ROOT.done.tmp\"; mv -f \"$ROOT.done.tmp\" \"$ROOT.done\"; "
                "printf 'bash command not found\\n' >> \"$ROOT.launch.tmp\"; "
                "elif [ -z \"$NOHUP\" ] && [ -z \"$SETSID\" ]; then "
                "printf 'launch-failed\\n' > \"$ROOT.done.tmp\"; mv -f \"$ROOT.done.tmp\" \"$ROOT.done\"; "
                "printf 'neither nohup nor setsid is available\\n' >> \"$ROOT.launch.tmp\"; "
                "else "
                "launch_pid=''; "
                "if [ -n \"$SETSID\" ] && [ -n \"$NOHUP\" ]; then \"$NOHUP\" \"$SETSID\" \"$BASH\" /tmp/env-install.sh \"$GEN\" </dev/null >> \"$ROOT.log\" 2>&1 & launch_pid=$!; "
                "elif [ -n \"$NOHUP\" ]; then \"$NOHUP\" \"$BASH\" /tmp/env-install.sh \"$GEN\" </dev/null >> \"$ROOT.log\" 2>&1 & launch_pid=$!; "
                "else \"$SETSID\" \"$BASH\" /tmp/env-install.sh \"$GEN\" </dev/null >> \"$ROOT.log\" 2>&1 & launch_pid=$!; fi; "
                "printf 'launcher-pid:%s\\nlaunch-ok\\n' \"$launch_pid\" >> \"$ROOT.launch.tmp\"; "
                "printf '%s\\n' \"$launch_pid\" > \"$ROOT.launch.pid.tmp\" && mv -f \"$ROOT.launch.pid.tmp\" \"$ROOT.launch.pid\"; "
                "fi; mv -f \"$ROOT.launch.tmp\" \"$ROOT.launch\";"
            )
            + guard_end
        )

    @staticmethod
    def _install_probe_command() -> str:
        """Probe both installer and launcher PIDs without trusting launch-ok."""
        return (
            "cat /tmp/env-install.done 2>/dev/null; "
            "if [ -f /tmp/env-install.exit ]; then echo install-exit:$(cat /tmp/env-install.exit); fi; "
            "if [ -f /tmp/env-install.started ]; then echo install-started; fi; "
            "if [ -f /tmp/env-install.launch ]; then cat /tmp/env-install.launch; fi; "
            "if [ -f /tmp/env-install.launch.pid ]; then echo launch-pid:$(cat /tmp/env-install.launch.pid); fi; "
            "child=$(cat /tmp/env-install.pid 2>/dev/null || true); "
            "launcher=$(cat /tmp/env-install.launch.pid 2>/dev/null || true); "
            "child_alive=0; launcher_alive=0; "
            "case \"$child\" in ''|*[!0-9]*) ;; *) [ \"$child\" -gt 1 ] 2>/dev/null && kill -0 \"$child\" 2>/dev/null && child_alive=1 ;; esac; "
            "case \"$launcher\" in ''|*[!0-9]*) ;; *) [ \"$launcher\" -gt 1 ] 2>/dev/null && kill -0 \"$launcher\" 2>/dev/null && launcher_alive=1 ;; esac; "
            "[ \"$child_alive\" -eq 1 ] && echo install-child-running || echo install-child-dead; "
            "[ \"$launcher_alive\" -eq 1 ] && echo install-launcher-running || echo install-launcher-dead; "
            "if [ \"$child_alive\" -eq 1 ] || [ \"$launcher_alive\" -eq 1 ]; then echo install-running; else echo install-dead; fi; "
            "echo ---; tail -n 20 /tmp/env-install.log 2>/dev/null"
        )

    async def _install_environment(self, task_id: str, ssh: YouyunSSHClient) -> None:
        self.sink.update(task_id, current_step="正在安装 LLaMA-Factory 训练环境（首次约 3-10 分钟）")
        if self.sink.is_cancel_requested(task_id):
            self._stopped = True
            raise _RemoteStopRequested()
        # 把安装体先写成独立脚本，再由 setsid/nohup 脱离 SSH 会话执行。
        # 这样 Bash/启动器错误也会落盘，避免远程进程退出后只剩“无安装日志”。
        generation = uuid.uuid4().hex
        install_script = (
            "#!/usr/bin/env bash\n"
            "set -u\n"
            "GEN=\"${1:-}\"\n"
            "case \"$GEN\" in ''|*[!A-Za-z0-9._-]*) echo 'invalid install generation' >&2; exit 64;; esac\n"
            "ROOT=\"/tmp/env-install.${GEN}\"\n"
            "PID_FILE=\"$ROOT.pid\"; EXIT_FILE=\"$ROOT.exit\"; DONE_FILE=\"$ROOT.done\"; LOG_FILE=\"$ROOT.log\"\n"
            "write_exit_marker() { "
            "rc=$?; "
            "printf 'installer-exit:%s\\n' \"$rc\" >> \"$LOG_FILE\" 2>/dev/null || true; "
            "if [ ! -f \"$EXIT_FILE\" ]; then "
            "printf '%s\\n' \"$rc\" > \"$EXIT_FILE.tmp\" 2>/dev/null && mv -f \"$EXIT_FILE.tmp\" \"$EXIT_FILE\" 2>/dev/null || true; "
            "fi; "
            "if [ ! -f \"$DONE_FILE\" ]; then "
            "printf 'fail\\n' > \"$DONE_FILE.tmp\" 2>/dev/null && mv -f \"$DONE_FILE.tmp\" \"$DONE_FILE\" 2>/dev/null || true; "
            "fi; "
            "}\n"
            "trap write_exit_marker EXIT\n"
            "echo $$ > \"$PID_FILE.tmp\" && mv -f \"$PID_FILE.tmp\" \"$PID_FILE\"\n"
            f"{_INSTALL_COMMAND}\n"
            "code=$?\n"
            "if [ \"$code\" -eq 0 ]; then "
            "if [ -z \"${PY:-}\" ] || [ ! -x \"$PY\" ]; then "
            "echo 'python-path missing after dependency installation' >&2; code=1; "
            "elif [ \"$(cat /tmp/env-install.generation 2>/dev/null || true)\" != \"$GEN\" ]; then "
            "echo 'stale install generation; refusing to publish python-path' >&2; code=1; "
            "else "
            "if ! printf '%s\\n' \"$PY\" > /root/cppt/python-path.tmp; then "
            "echo 'unable to persist python-path' >&2; code=1; "
            "elif ! mv -f /root/cppt/python-path.tmp /root/cppt/python-path; then "
            "echo 'unable to publish python-path' >&2; code=1; "
            "fi; "
            "if [ \"$code\" -eq 0 ]; then "
            "if ! printf '%s\\n' \"${CUDA_LIBS:-}\" > /root/cppt/cuda-library-path.tmp; then "
            "echo 'unable to persist cuda-library-path' >&2; code=1; "
            "elif ! mv -f /root/cppt/cuda-library-path.tmp /root/cppt/cuda-library-path; then "
            "echo 'unable to publish cuda-library-path' >&2; code=1; "
            "fi; fi; "
            "fi; fi\n"
            "printf '%s\\n' \"$code\" > \"$EXIT_FILE.tmp\" && "
            "mv -f \"$EXIT_FILE.tmp\" \"$EXIT_FILE\"\n"
            "if [ \"$code\" -eq 0 ]; then "
            "printf 'ok\\npython-path-ready\\n' > \"$DONE_FILE.tmp\" && mv -f \"$DONE_FILE.tmp\" \"$DONE_FILE\"; "
            "else "
            "printf 'fail\\n' > \"$DONE_FILE.tmp\" && mv -f \"$DONE_FILE.tmp\" \"$DONE_FILE\"; "
            "fi\n"
        )
        script_path = "/tmp/env-install.sh"
        await asyncio.to_thread(ssh.put_text, install_script, script_path)
        # Clean up any stale process left by a previous attempt before exposing
        # the new marker namespace.  This is intentionally best effort; the
        # installer itself will still report a launch failure if the tools are
        # unavailable.
        await self._stop_remote_install(ssh)
        # Always initialize a fresh namespace for a new invocation.  A stale
        # legacy marker from a previous SSH session must not suppress launch.
        launch = self._install_launch_command(generation, force=True)
        await asyncio.to_thread(ssh.run, launch, 30)
        deadline = self._clock() + self.install_timeout
        # The launch shell may need a few seconds before its PID and log file
        # become visible over SSH. Do not classify that visibility window as a
        # dead installer; rely on the explicit exit/done markers afterwards.
        launch_grace_deadline = self._clock() + max(10.0, self.poll_interval * 2)
        install_restart_attempts = max(0, int(os.environ.get("YOUYUN_INSTALL_RESTART_ATTEMPTS", "2")))
        install_restarts = 0
        last_tail = ""
        while self._clock() < deadline:
            self.sink.update(task_id, last_heartbeat_at_epoch=time.time())
            if self.sink.is_cancel_requested(task_id):
                self._stopped = True
                self.sink.update(task_id, state="stopping", current_step="正在停止远程环境安装")
                await self._stop_remote_install(ssh)
                raise _RemoteStopRequested()
            _, output = await asyncio.to_thread(
                self._run_idempotent_query,
                ssh,
                self._install_probe_command(),
                60,
                False,
            )
            head, _, tail = output.partition("---")
            status_lines = {line.strip() for line in head.splitlines() if line.strip()}
            if "launch-failed" in status_lines:
                diagnostics = tail[-1500:] or head[-1500:]
                raise RuntimeError(f"远程依赖安装启动失败：{diagnostics or '未返回启动诊断'}")
            tail = tail.strip()
            if tail and tail != last_tail:
                last_tail = tail
                self.sink.append_log(task_id, f"安装进度：{tail.splitlines()[-1][:200]}")
            if "fail" in status_lines:
                raise RuntimeError(f"远程依赖安装失败：{tail[-1500:]}")
            if "ok" in status_lines and "python-path-ready" not in status_lines:
                raise RuntimeError(
                    "远程依赖安装完成但未生成可用 python-path："
                    f"{tail[-1500:] or head[-1500:] or '未返回路径诊断'}"
                )
            if "ok" in status_lines:
                self.sink.append_log(task_id, "LLaMA-Factory 环境安装完成。")
                return
            installer_running = bool(
                {
                    "install-running",
                    "install-child-running",
                    "install-launcher-running",
                    # Older launcher probes emitted this shorter diagnostic.
                    "launcher-running",
                }
                & status_lines
            )
            if (
                {"install-dead", "install-started"}.issubset(status_lines)
                and not installer_running
                and self._clock() >= launch_grace_deadline
            ):
                if install_restarts < install_restart_attempts:
                    install_restarts += 1
                    self.sink.append_log(
                        task_id,
                        f"安装进程未存活，重新启动安装（第 {install_restarts}/{install_restart_attempts} 次）。",
                    )
                    # Rotate the generation only after terminating both the
                    # child and launcher.  A late EXIT trap from the old
                    # process therefore cannot publish into the new namespace.
                    await self._stop_remote_install(ssh)
                    generation = uuid.uuid4().hex
                    await asyncio.to_thread(
                        ssh.run,
                        self._install_launch_command(generation, force=True),
                        30,
                    )
                    launch_grace_deadline = self._clock() + max(10.0, self.poll_interval * 2)
                else:
                    diagnostics = tail[-1500:] or head[-1500:]
                    raise RuntimeError(
                        "远程依赖安装进程已退出但未生成完成标记："
                        f"{diagnostics or '无安装日志；请检查 env-install.launch 与 Bash/启动器探测结果'}"
                    )
            if not head.strip() and not tail:
                await asyncio.to_thread(
                    ssh.run,
                    self._install_launch_command(generation),
                    30,
                )
            self._sleep(self.poll_interval)
        raise RuntimeError("远程依赖安装超时。")

    async def _inspect_model_cache(self, task_id: str, task: Mapping[str, Any], ssh: YouyunSSHClient) -> str | None:
        """Resolve the exact recorded model on the remote instance.

        Public catalog models must exist at their recorded path. Repository IDs
        use the shared remote downloader, which tries ModelScope and then the
        same ID on Hugging Face without substituting a similarly named model.
        """
        model_id = str(task.get("model") or task.get("model_name_or_path") or "").strip()
        if not model_id:
            return None
        resource = dict(task.get("youyunResource") or {})
        configured = str(resource.get("publicModelPath") or "").strip()
        _, python_output = await asyncio.to_thread(
            ssh.run, f"cat {_PYTHON_PATH_FILE} 2>/dev/null || command -v python3", 30, False
        )
        remote_python = next((line.strip() for line in python_output.splitlines() if line.strip()), "python3")
        from backend.providers.youyun.inference import YouyunInferenceProvider

        resolver = YouyunInferenceProvider(
            self.manager,
            remote_root=self.remote_root,
            remote_python=remote_python,
            poll_interval=self.poll_interval,
            model_prepare_timeout=self.install_timeout,
        )

        def report(stage: str, message: str, **_details: Any) -> None:
            if stage in {"downloading_model", "model_ready"}:
                self.sink.append_log(task_id, message)

        return await resolver._prepare_remote_model(
            ssh,
            model=model_id,
            workdir=f"{self.remote_root}/{task_id}",
            public_model_path=configured or None,
            on_stage=report,
            cancel_requested=lambda: self.sink.is_cancel_requested(task_id),
        )

    async def _upload_task(
        self,
        task_id: str,
        task: Mapping[str, Any],
        ssh: YouyunSSHClient,
        *,
        remote_model_path: str | None = None,
    ) -> Any:
        if self.prepare_local is None:
            raise RuntimeError("缺少本地镜像 prepare 回调。")
        if self.sink.is_cancel_requested(task_id):
            self._stopped = True
            raise _RemoteStopRequested()
        self.sink.update(task_id, current_step="正在准备配置并上传数据集")
        handle = await self.prepare_local(task)
        if self.sink.is_cancel_requested(task_id):
            self._stopped = True
            raise _RemoteStopRequested()
        workdir = Path(handle.workdir)
        remote_workdir = f"{self.remote_root}/{task_id}"
        await asyncio.to_thread(ssh.mkdirs, f"{remote_workdir}/dataset")
        await asyncio.to_thread(ssh.mkdirs, f"{remote_workdir}/output")

        # YAML 在本地镜像上完成路径校验后，把 dataset_dir/output_dir 改写为
        # 结构一致的远端镜像路径，再上传；GPU 实例启用 bf16（转换器默认面向
        # CPU 部署输出 fp16/bf16=False）。
        from backend.llamafactory import dump_yaml

        config = _load_yaml_dict(workdir / "train.yaml")
        config["dataset_dir"] = f"{remote_workdir}/dataset"
        config["output_dir"] = f"{remote_workdir}/output"
        config["fp16"] = False
        config["bf16"] = True
        if remote_model_path:
            config["model_name_or_path"] = remote_model_path
        # LLaMA-Factory rejects val_size when an explicit validation dataset
        # is present. The local mirror may generate both for split datasets;
        # the uploaded validation file is authoritative on remote workers.
        if config.get("eval_dataset"):
            config.pop("val_size", None)
        remote_yaml = dump_yaml(config)

        def upload() -> None:
            dataset_root = workdir / "dataset"
            for item in sorted(dataset_root.rglob("*")):
                if not item.is_file():
                    continue
                relative = item.relative_to(dataset_root).as_posix()
                remote_path = posixpath.join(remote_workdir, "dataset", relative)
                parent = posixpath.dirname(remote_path)
                if parent:
                    ssh.mkdirs(parent)
                ssh.put_file(item, remote_path)
            ssh.put_text(remote_yaml, f"{remote_workdir}/train.yaml")

        await asyncio.to_thread(upload)
        if self.sink.is_cancel_requested(task_id):
            self._stopped = True
            raise _RemoteStopRequested()
        self.sink.append_log(task_id, f"数据集与训练配置已上传：{remote_workdir}")
        handle.remote_workdir = remote_workdir  # type: ignore[attr-defined]
        handle.remote_model_path = remote_model_path  # type: ignore[attr-defined]
        return handle

    async def _train_and_follow(self, task_id: str, task: Mapping[str, Any], ssh: YouyunSSHClient, handle: Any) -> int:
        remote_workdir = getattr(handle, "remote_workdir", f"{self.remote_root}/{task_id}")
        log_path = f"{remote_workdir}/train.log"
        pid_path = f"{remote_workdir}/train.pid"
        code_path = f"{remote_workdir}/exit.code"
        runner = (
            f"cd {remote_workdir} && PY=$(cat {_PYTHON_PATH_FILE}) && "
            f"CUDA_LIBS=$(cat {_CUDA_LIBRARY_PATH_FILE} 2>/dev/null || true); "
            "export LD_LIBRARY_PATH=\"$CUDA_LIBS:$LD_LIBRARY_PATH\" && "
            # Flush Loss before process exit, including launcher-spawned workers.
            f"PYTHONUNBUFFERED=1 $PY -m llamafactory.cli train train.yaml; echo $? > {code_path}"
        )
        launch = f"rm -f {code_path}; nohup bash -c {_posix_quote(runner)} >> {log_path} 2>&1 & echo $! > {pid_path}"
        self.sink.update(
            task_id,
            state="running",
            status="running",
            current_step="正在启动远程训练进程",
        )
        _, path_probe = await asyncio.to_thread(
            self._run_idempotent_query,
            ssh,
            f"if [ -s {_PYTHON_PATH_FILE} ] && [ -x \"$(cat {_PYTHON_PATH_FILE})\" ]; then echo python-path-ready; else echo python-path-missing; fi",
            30,
            False,
        )
        if "python-path-ready" not in path_probe:
            raise RuntimeError(
                "远程训练环境缺少可用 python-path，已阻止启动训练进程。"
            )
        await asyncio.to_thread(ssh.run, launch, 30)

        parameters = dict(task.get("parameters") or {})
        parser = _LogParser(int(parameters.get("loggingSteps", 10)))
        try:
            _, python_path = await asyncio.to_thread(
                self._run_idempotent_query, ssh,
                f"cat {_PYTHON_PATH_FILE} 2>/dev/null || command -v python3", 30, False,
            )
        except Exception:
            python_path = "python3"
            self.sink.append_log(task_id, "日志 Python 路径探测失败，采集器使用 python3 并自动重试。")
        collector = RemoteLogCollector(
            ssh.log_connection, log_path,
            python=python_path.strip().splitlines()[-1] if python_path.strip() else "python3",
            on_line=lambda line: self._ingest_chunk(task_id, parser, line),
            on_status=lambda line: self.sink.append_log(task_id, line),
        )
        collector.start()
        try:
            deadline = self._clock() + self.training_timeout
            while self._clock() < deadline:
                self.sink.update(task_id, last_heartbeat_at_epoch=time.time())
                if self.sink.is_cancel_requested(task_id):
                    self.sink.update(task_id, state="stopping", current_step="正在停止远程训练进程")
                    await asyncio.to_thread(
                        ssh.run,
                        f"p=$(cat {pid_path} 2>/dev/null || true); "
                        f"if [ -n \"$p\" ]; then kill -TERM -$p 2>/dev/null || kill -TERM $p 2>/dev/null || true; "
                        f"sleep 5; kill -KILL -$p 2>/dev/null || kill -KILL $p 2>/dev/null || true; fi; "
                        f"echo 130 > {code_path}",
                        60,
                        False,
                    )
                    self._stopped = True
                poll_command = (
                    f"echo code:$(cat {code_path} 2>/dev/null || echo none)"
                )
                output: str | None = None
                last_poll_error: Exception | None = None
                for retry_index in range(self.training_poll_retries + 1):
                    try:
                        _, output = await asyncio.to_thread(ssh.run, poll_command, 60, False)
                        break
                    except Exception as exc:  # noqa: BLE001 - the log query is safe to retry after SSH loss
                        last_poll_error = exc
                        detail = _exception_detail(exc)
                        attempt = retry_index + 1
                        if retry_index >= self.training_poll_retries:
                            break
                        self.sink.append_log(
                            task_id,
                            f"远程训练日志轮询失败（第 {attempt} 次）：{detail}，正在重连 SSH。",
                        )
                        try:
                            await asyncio.to_thread(self._reconnect_ssh, ssh)
                        except Exception as reconnect_exc:  # noqa: BLE001 - keep retrying transient SSH failures
                            reconnect_detail = _exception_detail(reconnect_exc)
                            self.sink.append_log(
                                task_id,
                                f"SSH 重连失败（第 {attempt} 次）：{reconnect_detail}。",
                            )
                        delay = min(30.0, self.training_poll_backoff * (2**retry_index))
                        if delay:
                            await asyncio.to_thread(self._sleep, delay)
                if output is None:
                    detail = _exception_detail(last_poll_error or RuntimeError("未收到远程日志"))
                    attempts = self.training_poll_retries + 1
                    self.sink.update(
                        task_id,
                        current_step=f"远程训练日志连接中断，正在等待恢复（已重试 {attempts} 次）",
                    )
                    raise RuntimeError(
                        f"远程训练日志轮询连续失败 {attempts} 次：{detail}。"
                        "训练进程可能仍在优云实例中运行，请检查远程实例后重试。"
                    ) from last_poll_error
                exit_code: int | None = None
                for line in output.splitlines():
                    if line.startswith("code:"):
                        value = line.removeprefix("code:").strip()
                        if value != "none":
                            exit_code = int(value)
                if exit_code is not None:
                    await collector.close(drain=True)
                    if self._stopped:
                        return 130
                    self.sink.append_log(task_id, f"远程训练进程退出，返回码 {exit_code}。")
                    return exit_code
                if self._sleep is time.sleep:
                    await asyncio.sleep(self.poll_interval)
                else:
                    await asyncio.to_thread(self._sleep, self.poll_interval)
            raise RuntimeError(f"远程训练超过时限（{int(self.training_timeout)} 秒）。")

        finally:
            await collector.close()

    @staticmethod
    def _reconnect_ssh(ssh: YouyunSSHClient) -> None:
        """重建 SSH 会话；测试替身可提供同语义的 reset_connection。"""
        reconnect = getattr(ssh, "reconnect", None)
        if callable(reconnect):
            reconnect()
            return
        reset = getattr(ssh, "reset_connection", None)
        if callable(reset):
            reset()
            return
        ssh.close()

    def _run_idempotent_query(
        self,
        ssh: YouyunSSHClient,
        command: str,
        timeout: float = 120,
        check: bool = True,
    ) -> tuple[int, str]:
        """Run a read-only remote query, rebuilding SSH after transient disconnects."""
        attempts = max(1, self.training_poll_retries + 1)
        for attempt in range(attempts):
            try:
                try:
                    return ssh.run(
                        command,
                        timeout,
                        check,
                        retry_on_disconnect=True,
                    )
                except TypeError as exc:
                    # Test doubles and older clients may not expose the keyword.
                    if "retry_on_disconnect" not in str(exc):
                        raise
                    return ssh.run(command, timeout, check)
            except Exception as exc:  # noqa: BLE001 - transient SSH loss is retryable
                if attempt >= attempts - 1 or not YouyunSSHClient._is_disconnect_error(exc):
                    raise
                self._reconnect_ssh(ssh)
                if self.training_poll_backoff:
                    self._sleep(min(30.0, self.training_poll_backoff * (attempt + 1)))
        raise RuntimeError("幂等远程查询失败。")

    def _ingest_chunk(self, task_id: str, parser: _LogParser, chunk: str) -> None:
        for raw_line in chunk.splitlines():
            text = raw_line.strip()
            if not text:
                continue
            self.sink.append_log(task_id, text)
            if "***** Running training *****" in text:
                self.sink.update(task_id, current_step="训练已开始，等待首个训练指标")
            event_type, payload = parser.parse(text)
            if event_type != "metric":
                continue
            values = payload
            if not ("loss" in values or "eval_loss" in values):
                continue
            step = int(values.get("step") or 0)
            if step <= 0:
                previous = self.sink.metrics(task_id)
                step = (previous[-1]["step"] if previous else 0) + 1
            self.sink.append_metric(
                task_id,
                step=step,
                epoch=float(values["epoch"]) if values.get("epoch") is not None else None,
                train_loss=float(values["loss"]) if values.get("loss") is not None else None,
                eval_loss=float(values["eval_loss"]) if values.get("eval_loss") is not None else None,
                learning_rate=float(values["learning_rate"]) if values.get("learning_rate") is not None else None,
            )
            total_steps = int(payload.get("total_steps") or 0)
            self.sink.update(
                task_id,
                progress=min(95, int(payload.get("progress", 0))),
                current_step=f"训练中 · Step {step}/{total_steps}" if total_steps > 0 else f"训练中 · Step {step}",
                loss=(float(values["loss"]) if values.get("loss") is not None else None),
            )

    async def sync_retained_artifacts(self, task_id: str, task: Mapping[str, Any]) -> None:
        """Recover an existing instance's output without restarting or releasing it."""
        marker = next((item for item in task.get("artifacts") or [] if item.get("type") == "youyun-instance"), None)
        if not marker or not marker.get("instanceId"):
            raise RuntimeError("任务没有可同步的优云实例。")
        if marker.get("state") not in {"awaiting_comparison", "debugging"}:
            raise RuntimeError("优云实例已释放，无法继续回传权重。")
        if self.prepare_local is None:
            raise RuntimeError("缺少本地产物准备回调。")
        ssh: YouyunSSHClient | None = None
        try:
            instance = await asyncio.to_thread(self.manager.client.describe_instance, str(marker["instanceId"]))
            ssh = await asyncio.to_thread(self._connect_ssh, instance, task_id)
            handle = await self.prepare_local(task)
            await self._collect_artifacts(task_id, task, ssh, handle, retained=True)
        finally:
            if ssh is not None:
                ssh.close()

    def _download_retained_output(self, task_id: str, ssh: YouyunSSHClient, remote_workdir: str, output: Path) -> None:
        """Stage the transfer first; never overwrite files of a complete Adapter."""
        output.mkdir(parents=True, exist_ok=True)
        protected: set[Path] = set()
        expected: list[Path] = []
        for item in self._artifacts(task_id):
            if item.get("type") != "adapter" or not item.get("path"):
                continue
            root = Path(str(item["path"])).resolve()
            if adapter_is_complete(root):
                protected.update(path.resolve() for path, _ in iter_model_files(root, "lora_adapter"))
            elif root.is_relative_to(output.resolve()):
                expected.append(root.relative_to(output.resolve()))
        with tempfile.TemporaryDirectory(prefix=".artifact-sync-", dir=output.parent) as directory:
            staging = Path(directory) / "output"
            ssh.get_tree(f"{remote_workdir}/output", staging)
            complete = any(adapter_is_complete(path.parent) for path in staging.rglob("adapter_config.json"))
            if not complete or any(not adapter_is_complete(staging / relative) for relative in expected):
                raise RuntimeError("远程回传的 Adapter 配置或权重文件不完整。")
            for source in staging.rglob("*"):
                if source.is_symlink():
                    raise RuntimeError("产物中不允许符号链接。")
                if not source.is_file():
                    continue
                target = output / source.relative_to(staging)
                if not target.resolve().is_relative_to(output.resolve()):
                    raise RuntimeError("产物目标路径越界。")
                if target.resolve() in protected:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                source.replace(target)

    async def _collect_artifacts(self, task_id: str, task: Mapping[str, Any], ssh: YouyunSSHClient, handle: Any, *, retained: bool = False) -> None:
        if self.collect_local is None:
            raise RuntimeError("缺少本地产物收集回调。")
        if not retained:
            self.sink.update(task_id, current_step="正在回传训练产物")
        remote_workdir = getattr(handle, "remote_workdir", f"{self.remote_root}/{task_id}")
        local_output = Path(handle.workdir) / "output"
        if retained:
            await asyncio.to_thread(self._download_retained_output, task_id, ssh, remote_workdir, local_output)
        else:
            await asyncio.to_thread(ssh.get_tree, f"{remote_workdir}/output", local_output)
        artifacts = await self.collect_local(task)
        if retained:
            # Preserve complete registered variants and keep missing variants
            # visible so the caller cannot report a partial recovery as success.
            for item in self._artifacts(task_id):
                if item.get("type") != "adapter":
                    continue
                variant = item.get("variant", "final")
                matches = [a for a in artifacts if a.get("type") == "adapter" and a.get("variant", "final") == variant]
                if not matches or (item.get("path") and adapter_is_complete(Path(str(item["path"])))):
                    artifacts = [a for a in artifacts if a not in matches] + [item]
        base_model_metadata = await self._read_base_model_metadata(ssh, local_output)
        if base_model_metadata:
            artifacts = [
                {**artifact, "baseModelMetadata": base_model_metadata}
                if artifact.get("type") == "adapter"
                else artifact
                for artifact in artifacts
            ]
        self.sink.append_log(task_id, f"产物已回传本地镜像：{local_output}（{len(artifacts)} 项）")
        existing = self._artifacts(task_id)
        instance_markers = [item for item in existing if item.get("type") == "youyun-instance"]
        self.sink.update(task_id, artifacts=[*artifacts, *instance_markers])

    async def _read_base_model_metadata(
        self, ssh: YouyunSSHClient, local_output: Path
    ) -> dict[str, Any] | None:
        adapter_config = local_output / "adapter_config.json"
        try:
            adapter = json.loads(adapter_config.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        base_path = adapter.get("base_model_name_or_path") if isinstance(adapter, dict) else None
        if not isinstance(base_path, str) or not posixpath.isabs(base_path):
            return None
        temporary_config = local_output.parent / ".xuntui-base-model-config.json"
        try:
            await asyncio.to_thread(
                ssh.get_file,
                posixpath.join(base_path, "config.json"),
                temporary_config,
            )
            config = json.loads(temporary_config.read_text(encoding="utf-8-sig"))
        except (OSError, RuntimeError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        finally:
            temporary_config.unlink(missing_ok=True)
        if not isinstance(config, dict):
            return None
        sections = [config]
        sections.extend(
            value for value in (config.get("text_config"), config.get("llm_config"))
            if isinstance(value, dict)
        )
        for section in sections:
            for key in _MODEL_CONTEXT_KEYS:
                try:
                    value = int(section.get(key))
                except (TypeError, ValueError, OverflowError):
                    continue
                if 128 <= value <= 1_048_576:
                    return {"maxContextLength": value, "source": f"remote-config.json:{key}"}
        return None

    def _artifacts(self, task_id: str) -> list[dict[str, Any]]:
        getter = getattr(self.sink, "get", None)
        if not callable(getter):
            return []
        try:
            return list((getter(task_id) or {}).get("artifacts") or [])
        except Exception:  # noqa: BLE001 - 生命周期日志不能被兼容 store 读取失败阻断
            return []

    def _record_instance(self, task_id: str, instance: YouyunInstance, *, state: str) -> None:
        artifacts = [item for item in self._artifacts(task_id) if item.get("type") != "youyun-instance"]
        artifacts.append(
            {
                "type": "youyun-instance",
                "provider": "youyun",
                "instanceId": instance.uhost_id,
                "gpuType": instance.gpu_type,
                "gpuCount": instance.gpu,
                "zone": instance.zone,
                "state": state,
            }
        )
        self.sink.update(task_id, artifacts=artifacts)

    def _record_instance_id(
        self,
        task_id: str,
        uhost_id: str,
        *,
        resource: Mapping[str, Any] | None = None,
    ) -> None:
        """Persist the provider handle before create_one_shot waits for Running."""
        instance_id = str(uhost_id or "").strip()
        if not instance_id:
            return
        artifacts = [item for item in self._artifacts(task_id) if item.get("type") != "youyun-instance"]
        marker: dict[str, Any] = {
            "type": "youyun-instance",
            "provider": "youyun",
            "instanceId": instance_id,
            "state": "provisioning",
        }
        for source, target in (
            ("region", "region"),
            ("zone", "zone"),
            ("chargeType", "chargeType"),
            ("imageId", "imageId"),
        ):
            value = str((resource or {}).get(source) or "").strip()
            if value:
                marker[target] = value
        artifacts.append(marker)
        self.sink.update(task_id, artifacts=artifacts, resource_state="provisioning", cleanup_state="pending", last_heartbeat_at_epoch=time.time())

    def _set_instance_state(
        self,
        task_id: str,
        uhost_id: str,
        state: str,
        *,
        error: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        artifacts = self._artifacts(task_id)
        updated = False
        for index, item in enumerate(artifacts):
            if item.get("type") == "youyun-instance" and item.get("instanceId") == uhost_id:
                marker = {**item, "state": state}
                if error:
                    marker["cleanupError"] = error
                else:
                    marker.pop("cleanupError", None)
                if extra:
                    marker.update(extra)
                artifacts[index] = marker
                updated = True
                break
        if updated:
            self.sink.update(task_id, artifacts=artifacts)

    def _retain_instance(self, task_id: str, uhost_id: str, *, stopped: bool = False) -> None:
        """Keep a usable Adapter's instance available for the comparison flow."""
        self._set_instance_state(
            task_id,
            uhost_id,
            "awaiting_comparison",
            extra={
                "retainedAt": datetime.now(timezone.utc).isoformat(),
                "comparisonReady": True,
            },
        )
        if stopped:
            self.sink.update(task_id, current_step="训练已停止，可进行效果对比（实例待手动释放）")
            self.sink.append_log(
                task_id,
                "训练已停止且 Adapter 已保存：优云实例暂不释放，可先进行效果对比；完成后请手动释放。",
            )
        else:
            self.sink.append_log(
                task_id,
                "训练已完成且 Adapter 已保存：优云实例暂不释放，可进行效果对比；完成后请手动释放。",
            )

    async def _try_collect_artifacts(self, task_id: str, task: Mapping[str, Any], ssh: YouyunSSHClient, handle: Any) -> bool:
        try:
            await self._collect_artifacts(task_id, task, ssh, handle)
        except Exception as exc:  # noqa: BLE001 - 停止场景尽力回传，失败不阻断
            self.sink.append_log(task_id, f"停止后回传产物未完成：{exc}")
            return False
        return self._has_comparable_adapter(task_id)

    async def _cleanup_instance(self, task_id: str, uhost_id: str) -> bool:
        try:
            report = await asyncio.to_thread(self.manager.cleanup_after_task, uhost_id, stop_first=False)
        except Exception as exc:  # noqa: BLE001 - 清理失败也要记录并继续
            report = {"terminated": False, "error": str(exc)}
        if report.get("terminated"):
            self._set_instance_state(task_id, uhost_id, "released")
            self.sink.update(
                task_id,
                resource_state="released",
                cleanup_state="released",
                cleanup_checked_at=report.get("checkedAt"),
            )
            self.sink.append_log(task_id, f"优云实例 {uhost_id} 已释放删除。")
            return True
        else:
            error = str(report.get("error") or "实例删除未确认")
            self._set_instance_state(task_id, uhost_id, "cleanup_failed", error=error)
            self.sink.update(
                task_id,
                resource_state="cleanup_failed",
                cleanup_state="failed",
                cleanup_error=error,
                cleanup_checked_at=report.get("checkedAt"),
                failure_code="cleanup_failed",
                failure_message="优云实例清理失败。",
                suggested_action="重试清理优云实例，确认实例消失后再重新运行。",
            )
            self.sink.append_log(
                task_id,
                f"优云实例 {uhost_id} 清理未完成：{error}（请到控制台手动删除，避免持续计费）",
            )
            return False


__all__ = ["YouyunTrainingRunner"]
