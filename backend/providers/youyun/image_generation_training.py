from __future__ import annotations

"""One-shot Youyun executor for the SDXL LoRA training contract."""

import asyncio
import json
import os
import posixpath
import shlex
import socket
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from backend.image_generation_executor import build_sdxl_lora_command, collect_image_generation_adapters, parse_sdxl_training_log
from backend.training_executor import TrainingEvent
from backend.sdxl_script_artifact import (
    CANONICAL_COMMIT,
    DEFAULT_PUBLIC_URLS,
    SdxlScriptResolver,
    build_minio_script_store,
    load_sdxl_script_artifact,
)

from .diffusers import YouyunDiffusersProvider
from .instance_manager import YouyunInstanceManager
from .models import YouyunApiError
from .ssh import YouyunSSHClient
from .resource_health import find_uhost_fallback


def _run_ssh(ssh: YouyunSSHClient, command: str, timeout: float, check: bool, *, retry_on_disconnect: bool = False) -> tuple[int, str]:
    try:
        return ssh.run(command, timeout, check, retry_on_disconnect=retry_on_disconnect)
    except TypeError as exc:
        if "retry_on_disconnect" not in str(exc):
            raise
        return ssh.run(command, timeout, check)


class YouyunImageGenerationTrainingExecutor:
    def __init__(self, manager: YouyunInstanceManager, *, ssh_factory: Callable[[], YouyunSSHClient] = YouyunSSHClient, remote_root: str = "/root/cppt/image-generation-training", remote_python: str | None = None, poll_interval: float = 5.0, timeout: float = 86_400.0, stall_timeout: float | None = None) -> None:
        self.manager = manager
        self.ssh_factory = ssh_factory
        self.remote_root = remote_root.rstrip("/")
        self.remote_python = (remote_python or os.environ.get("XUNTUI_IMAGE_GENERATION_TRAINING_PYTHON", "python3")).strip() or "python3"
        self.poll_interval = poll_interval
        self.timeout = timeout
        self.stall_timeout = stall_timeout if stall_timeout is not None else float(os.environ.get("XUNTUI_REMOTE_TASK_STALL_SECONDS", "900"))

    @staticmethod
    def allow_remote_script_download() -> bool:
        """Online GitHub fetches are opt-in; stable images should ship the script."""
        return os.environ.get("XUNTUI_ALLOW_REMOTE_SCRIPT_DOWNLOAD", "0").strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def failure_code(exc: BaseException, phase: str) -> str:
        """Map remote preparation failures to actionable, user-visible codes."""
        text = str(exc).casefold()
        if isinstance(exc, YouyunApiError):
            action = str(exc.action or "").casefold()
            ret_code = str(exc.ret_code or "").strip()
            if action == "checkcompshareresourcecapacity" or "无可用规格库存" in text or "库存" in text:
                return "youyun_create_no_capacity"
            if action == "createcompshareinstance" and (
                ret_code == "226645"
                or any(marker in text for marker in ("service unavailable", "服务不可用", "temporarily unavailable"))
            ):
                return "youyun_create_service_unavailable"
            if phase == "instance_provision":
                return "instance_provision_failed"
        if phase == "script_setup" or "训练脚本" in text or "script download" in text.casefold():
            return "training_script_download_failed"
        if isinstance(exc, (EOFError, ConnectionError, BrokenPipeError, OSError)) or any(
            marker in text for marker in ("eof", "channel closed", "connection reset", "socket is closed", "ssh session not active")
        ):
            return "ssh_channel_disconnected" if phase != "ssh_connect" else "ssh_handshake_eof"
        if phase == "dependency_install" or "diffusers 训练依赖安装失败" in text:
            return "dependency_install_failed"
        if phase == "model_download" or "基座模型下载" in text:
            return "base_model_download_failed"
        if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
            return "resource_timeout"
        if phase == "environment_probe" or "训练环境" in text:
            return "remote_environment_unavailable"
        return "training_failed"

    async def _emit(self, callback: Callable[[TrainingEvent], Any] | None, event: TrainingEvent) -> None:
        if callback is None:
            return
        result = callback(event)
        if hasattr(result, "__await__"):
            await result

    async def _emit_stage(
        self,
        callback: Callable[[TrainingEvent], Any] | None,
        message: str,
        *,
        phase: str,
        progress: int,
        run_started_at: float,
        **payload: Any,
    ) -> None:
        payload.update({"phase": phase, "progress": progress, "elapsedSeconds": round(time.monotonic() - run_started_at, 1)})
        await self._emit(callback, TrainingEvent("stage", message, payload))

    @staticmethod
    def _validate_local_bundle(root: Path) -> None:
        if not root.is_dir():
            raise RuntimeError("训练物化目录不存在。")
        required = (root / "train_config.json", root / "train.jsonl", root / "dataset")
        missing = [path.name for path in required if not path.exists()]
        if missing:
            raise RuntimeError(f"训练物化目录缺少必要文件：{', '.join(missing)}。")
        if not (root / "train.jsonl").read_text(encoding="utf-8").strip():
            raise RuntimeError("训练数据为空，未创建优云实例。")
        try:
            config = json.loads((root / "train_config.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("训练配置文件无效，未创建优云实例。") from exc
        if not isinstance(config, dict):
            raise RuntimeError("训练配置文件格式无效，未创建优云实例。")

    async def _emit_worker_updates(self, text: str, state: dict[str, Any], callback: Callable[[TrainingEvent], Any] | None) -> None:
        parsed = parse_sdxl_training_log(text)
        seen_logs = state.setdefault("logs", set())
        metric_signatures = state.setdefault("metrics", {})
        for message in parsed["logs"]:
            if message.startswith("Step ") or message in seen_logs:
                continue
            seen_logs.add(message)
            await self._emit(callback, TrainingEvent("log", message))
        for metric in parsed["metrics"]:
            step = int(metric.get("step") or 0)
            signature = (
                metric.get("loss"),
                metric.get("learningRate"),
                metric.get("progress"),
                metric.get("totalSteps"),
            )
            if metric_signatures.get(step) == signature:
                continue
            metric_signatures[step] = signature
            await self._emit(
                callback,
                TrainingEvent(
                    "metric",
                    f"Step {step}/{metric.get('totalSteps')} · Loss {float(metric.get('loss') or 0):.6f}",
                    {
                        "step": step,
                        "total_steps": metric.get("totalSteps"),
                        "loss": metric.get("loss"),
                        "learning_rate": metric.get("learningRate"),
                        "progress": metric.get("progress"),
                    },
                ),
            )

    @staticmethod
    def _error_text(exc: BaseException) -> str:
        message = str(exc).strip()
        return message or f"{type(exc).__name__}: {exc!r}"

    async def cleanup_remote(self, task: Mapping[str, Any]) -> dict[str, Any]:
        task_id = str(task.get("id") or "")
        resource = task.get("resource") if isinstance(task.get("resource"), Mapping) else {}
        region = str(resource.get("region") or "").strip() or None
        zone = str(resource.get("zone") or "").strip() or None
        handle = task.get("remoteHandle") if isinstance(task.get("remoteHandle"), Mapping) else {}
        instance_ids = [str(handle.get("instanceId") or "")]
        if not instance_ids[0]:
            instances = await asyncio.to_thread(self.manager.client.describe_instances)
            expected_name = f"cppt-img-train-{task_id}"
            instance_ids = [str(item.uhost_id) for item in instances if str(getattr(item, "name", "")) == expected_name]
        instance_ids = [item for item in dict.fromkeys(instance_ids) if item]
        errors: list[str] = []
        for instance_id in instance_ids:
            report = await asyncio.to_thread(
                self.manager.cleanup_after_task,
                instance_id,
                stop_first=True,
                region=region,
                zone=zone,
            )
            if not report.get("terminated"):
                errors.append(str(report.get("error") or f"实例 {instance_id} 未释放"))
        return {
            "cleanupState": "failed" if errors else "completed",
            "cleanupError": "；".join(errors) if errors else None,
        }

    def _connect(self, instance: Any) -> YouyunSSHClient:
        credentials = self.manager.ssh_credentials(instance)
        if not credentials.get("host") or not credentials.get("password"):
            raise RuntimeError("优云实例缺少 SSH 连接信息。")
        timeout = max(10.0, min(900.0, float(os.environ.get("YOUYUN_SSH_READY_TIMEOUT_SECONDS", "300"))))
        delay = max(1.0, min(30.0, float(os.environ.get("YOUYUN_SSH_CONNECT_RETRY_DELAY_SECONDS", "5"))))
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        attempts = 0
        while time.monotonic() < deadline:
            current = self.manager.get(str(getattr(instance, "uhost_id", "")))
            if current is not None and str(getattr(current, "state", "")) in {"Stopped", "Stop", "Fail", "Error", "Terminated", "Deleted"}:
                raise RuntimeError(f"优云实例进入异常状态 {getattr(current, 'state', '')}，无法建立 SSH 连接。")
            if current is not None:
                refreshed = self.manager.ssh_credentials(current)
                if refreshed.get("host") and refreshed.get("password"):
                    credentials = refreshed
            attempts += 1
            host = str(credentials["host"])
            port = int(credentials.get("port") or 22)
            if self.ssh_factory is YouyunSSHClient:
                try:
                    with socket.create_connection((host, port), timeout=min(20.0, max(1.0, deadline - time.monotonic()))) as probe:
                        probe.settimeout(min(20.0, max(1.0, deadline - time.monotonic())))
                        banner = probe.recv(128)
                        if not banner.startswith(b"SSH-"):
                            raise OSError("SSH 端口已连接但未返回有效 banner")
                except (OSError, TimeoutError) as exc:
                    last_error = exc
                    time.sleep(delay)
                    continue
            ssh = self.ssh_factory()
            try:
                ssh.connect(host, port, credentials.get("user") or "root", credentials["password"])
                return ssh
            except Exception as exc:
                last_error = exc
                try:
                    ssh.close()
                except Exception:
                    pass
                time.sleep(delay)
        raise RuntimeError(f"优云 SSH 连接超时（已尝试 {attempts} 次）：{last_error or '未收到 SSH 响应'}") from last_error

    @staticmethod
    def _upload_tree(ssh: YouyunSSHClient, local_root: Path, remote_root: str) -> None:
        ssh.mkdirs(remote_root)
        for path in sorted(item for item in local_root.rglob("*") if item.is_file()):
            relative = path.relative_to(local_root).as_posix()
            target = posixpath.join(remote_root, relative)
            ssh.mkdirs(posixpath.dirname(target))
            ssh.put_file(path, target)

    @staticmethod
    def _model_download_script() -> str:
        """Download only Diffusers files needed for SDXL inference/training."""
        return (
            "import importlib.util\n"
            "import sys\n"
            "allow=['model_index.json','configuration.json','scheduler/*.json',\n"
            "       'tokenizer/*','tokenizer_2/*',\n"
            "       'text_encoder/config.json','text_encoder/model.fp16.safetensors',\n"
            "       'text_encoder_2/config.json','text_encoder_2/model.fp16.safetensors',\n"
            "       'unet/config.json','unet/diffusion_pytorch_model.fp16.safetensors',\n"
            "       'vae/config.json','vae/diffusion_pytorch_model.fp16.safetensors']\n"
            "ignore=['*flax*','*.msgpack','*.onnx','*.ckpt','*.bin']\n"
            "repo,cache=sys.argv[1],sys.argv[2]\n"
            "workers=max(1,min(8,int(sys.argv[3] if len(sys.argv)>3 else '4')))\n"
            "error=None\n"
            "if importlib.util.find_spec('modelscope') is not None:\n"
            "    try:\n"
            "        from modelscope import snapshot_download\n"
            "        print(snapshot_download(repo, cache_dir=cache, allow_file_pattern=allow, ignore_file_pattern=ignore, max_workers=workers))\n"
            "        raise SystemExit(0)\n"
            "    except Exception as exc:\n"
            "        error=exc\n"
            "if importlib.util.find_spec('huggingface_hub') is None:\n"
            "    raise RuntimeError(f'优云实例缺少 ModelScope/Hugging Face 下载依赖: {error}')\n"
            "# 拒绝使用未认证 Hugging Face 下载，除非显式开启下载兜底。\n"
            "from huggingface_hub import snapshot_download\n"
            "print(snapshot_download(repo, cache_dir=cache, allow_patterns=allow, ignore_patterns=ignore, max_workers=workers, resume_download=True))\n"
        )

    @staticmethod
    def _build_script_resolver() -> SdxlScriptResolver:
        artifact = load_sdxl_script_artifact()
        local_source = os.environ.get("XUNTUI_SDXL_SCRIPT_LOCAL_PATH", "").strip()
        store = None
        enabled = os.environ.get("XUNTUI_SDXL_SCRIPT_ARTIFACT_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
        if enabled:
            try:
                store = build_minio_script_store()
            except (RuntimeError, ImportError):
                store = None
        cache = os.environ.get("XUNTUI_SDXL_SCRIPT_CACHE", "").strip()
        configured_url = os.environ.get("XUNTUI_SDXL_LORA_SCRIPT_URL", "").strip()
        public_urls = DEFAULT_PUBLIC_URLS
        if configured_url and CANONICAL_COMMIT in configured_url:
            public_urls = (configured_url, *[url for url in DEFAULT_PUBLIC_URLS if url != configured_url])
        return SdxlScriptResolver(
            image_path=Path(local_source) if local_source else None,
            minio_store=store,
            cache_dir=Path(cache) if cache else None,
            urls=public_urls,
            allow_public=YouyunImageGenerationTrainingExecutor.allow_remote_script_download(),
        )

    @staticmethod
    def _probe_runtime(ssh: YouyunSSHClient, python_executable: str, *, allow_install: bool = False) -> str:
        candidates = [python_executable, "/opt/conda/bin/python", "/usr/local/bin/python3", "/usr/bin/python3"]
        candidate_list = " ".join(shlex.quote(item) for item in candidates) + " /opt/conda/envs/*/bin/python /usr/local/miniconda3/envs/*/bin/python /root/miniconda3/envs/*/bin/python"
        probe = f"for candidate in {candidate_list}; do if [ -x \"$candidate\" ] && \"$candidate\" -c 'import torch, diffusers, peft' >/dev/null 2>&1; then printf '%s\\n' \"$candidate\"; exit 0; fi; done; exit 42"
        code, output = _run_ssh(ssh, probe, 90, False, retry_on_disconnect=True)
        if code == 0 and output.strip():
            return output.strip().splitlines()[-1]
        allow_install = allow_install or os.environ.get("XUNTUI_REMOTE_DIFFUSERS_INSTALL", "0").strip().lower() in {"1", "true", "yes", "on"}
        if not allow_install:
            raise RuntimeError("优云镜像未提供可导入的 torch、diffusers 和 peft 训练环境。")
        torch_probe = f"for candidate in {candidate_list}; do if [ -x \"$candidate\" ] && \"$candidate\" -c 'import torch' >/dev/null 2>&1 && \"$candidate\" -m pip --version >/dev/null 2>&1; then printf '%s\\n' \"$candidate\"; exit 0; fi; done; exit 42"
        torch_code, torch_output = _run_ssh(ssh, torch_probe, 90, False, retry_on_disconnect=True)
        if torch_code != 0 or not torch_output.strip():
            raise RuntimeError("优云镜像中没有同时具备 Torch 和 pip 的 Python 训练环境，请更换已预装 Diffusers 的镜像。")
        install_python = torch_output.strip().splitlines()[-1]
        packages = os.environ.get(
            "XUNTUI_REMOTE_DIFFUSERS_PACKAGE",
            "diffusers transformers accelerate datasets peft safetensors bitsandbytes modelscope",
        ).strip()
        sentinel = os.environ.get("XUNTUI_REMOTE_DIFFUSERS_SENTINEL", "/root/cppt/.cache/diffusers-ready").strip()
        if not sentinel.startswith("/"):
            raise RuntimeError("XUNTUI_REMOTE_DIFFUSERS_SENTINEL 必须是优云实例内的绝对路径。")
        install_command = (
            f"{shlex.quote(install_python)} -m pip install --disable-pip-version-check --no-input --retries 3 --timeout 60 {packages}; "
            f"code=$?; if [ $code -eq 0 ]; then mkdir -p {shlex.quote(posixpath.dirname(sentinel))}; touch {shlex.quote(sentinel)}; fi; exit $code"
        )
        install_code, output = _run_ssh(ssh, install_command, 3600, False, retry_on_disconnect=True)
        if install_code != 0:
            raise RuntimeError(f"远端 Diffusers 训练依赖安装失败：{output[-1200:]}")
        return install_python

    async def run(self, task: Mapping[str, Any], *, on_event: Callable[[TrainingEvent], Any] | None = None, cancel_event: asyncio.Event | None = None) -> dict[str, Any]:
        task_id = str(task.get("id") or "")
        resource = dict(task.get("resource") or {})
        instance = None
        provisioned_instance_id: str | None = None
        ssh: YouyunSSHClient | None = None
        result: dict[str, Any] = {}
        run_started_at = time.monotonic()
        phase = "preparing"
        try:
            preview_value = str(task.get("previewRoot") or "").strip()
            root = Path(preview_value).expanduser().resolve() if preview_value else None
            if root is not None:
                await self._emit_stage(on_event, "正在校验训练输入。", phase="preparing", progress=1, run_started_at=run_started_at)
                await asyncio.to_thread(self._validate_local_bundle, root)
                await self._emit_stage(on_event, "训练输入校验通过，开始创建优云实例。", phase="preparing", progress=3, run_started_at=run_started_at)
            phase = "instance_provision"
            await self._emit_stage(on_event, "正在创建优云 SDXL LoRA 训练实例。", phase=phase, progress=5, run_started_at=run_started_at)
            for provision_attempt in range(2):
                try:
                    def record_created_instance(uhost_id: str) -> None:
                        nonlocal provisioned_instance_id
                        provisioned_instance_id = str(uhost_id or "").strip() or None

                    instance = await asyncio.to_thread(
                        self.manager.create_one_shot,
                        task_id,
                        gpu=resource.get("gpuCount"),
                        cpu=resource.get("cpuCores"),
                        memory_gb=resource.get("memoryGb"),
                        gpu_type=resource.get("gpuType"),
                        image_id=resource.get("imageId"),
                        charge_type=resource.get("chargeType"),
                        region=resource.get("region"),
                        zone=resource.get("zone"),
                        name=f"cppt-img-train-{task_id}",
                        idempotency_key=f"{task_id}:image-training:{provision_attempt}",
                        instance_mode=resource.get("instanceMode"),
                        on_created=record_created_instance,
                    )
                    break
                except Exception as exc:
                    fallback = find_uhost_fallback(self.manager, resource)
                    if fallback is None or provision_attempt:
                        raise
                    if provisioned_instance_id:
                        cleanup = await asyncio.to_thread(self.manager.cleanup_after_task, provisioned_instance_id, stop_first=False, region=resource.get("region"), zone=resource.get("zone"))
                        if not cleanup.get("terminated"):
                            raise RuntimeError("Pod 创建失败后实例释放未确认，已停止切换 UHost。") from exc
                        provisioned_instance_id = None
                    resource = {**resource, **fallback, "attemptedModes": ["pod", "uhost"]}
                    result["fallbackReason"] = fallback.get("fallbackReason")
                    await self._emit_stage(on_event, "Pod 创建接口暂不可用，正在切换到 UHost 兜底。", phase=phase, progress=5, run_started_at=run_started_at)
            await self._emit_stage(
                on_event,
                f"优云训练实例已就绪：{instance.uhost_id}。",
                phase="ssh_connect",
                progress=6,
                run_started_at=run_started_at,
                remoteHandle={"instanceId": str(instance.uhost_id), "state": "running"},
            )
            phase = "ssh_connect"
            await self._emit_stage(on_event, "正在连接优云训练实例。", phase=phase, progress=6, run_started_at=run_started_at)
            ssh = await asyncio.to_thread(self._connect, instance)
            phase = "dependency_install" if bool(resource.get("requiresDiffusersInstall")) else "environment_probe"
            await self._emit_stage(on_event, "正在检测远端 Torch、pip 与 Diffusers 环境。", phase=phase, progress=6, run_started_at=run_started_at)
            self.remote_python = await asyncio.to_thread(
                self._probe_runtime,
                ssh,
                self.remote_python,
                allow_install=bool(resource.get("requiresDiffusersInstall")),
            )
            await self._emit_stage(on_event, f"远端训练环境已就绪：{self.remote_python}。", phase=phase, progress=7, run_started_at=run_started_at)
            if cancel_event and cancel_event.is_set():
                result = {"status": "stopped", "failureCode": "training_stopped"}
                return result
            if root is None:
                root = Path(str(task.get("previewRoot") or "")).expanduser().resolve()
                await asyncio.to_thread(self._validate_local_bundle, root)
            remote_workdir = posixpath.join(self.remote_root, task_id)
            remote_bundle = posixpath.join(remote_workdir, "bundle")
            phase = "upload"
            await self._emit_stage(on_event, "正在上传训练数据。", phase=phase, progress=7, run_started_at=run_started_at)
            await asyncio.to_thread(self._upload_tree, ssh, root, remote_bundle)
            model_repo = str(task.get("modelRepoId") or "")
            phase = "model_resolution"
            await self._emit_stage(on_event, "正在解析优云本地 SDXL 基座模型。", phase=phase, progress=8, run_started_at=run_started_at)
            code, output = await asyncio.to_thread(_run_ssh, ssh, YouyunDiffusersProvider._model_resolution_command(model_repo), 120, False, retry_on_disconnect=True)
            model_path = next((line.removeprefix("base-model-path:").strip() for line in output.splitlines() if line.startswith("base-model-path:")), "")
            if not model_path:
                allow_download = os.environ.get("YOUYUN_ALLOW_MODEL_DOWNLOAD", "0").strip().lower() in {"1", "true", "yes", "on"}
                if not allow_download:
                    raise RuntimeError("优云实例未找到完整 SDXL 基座模型，且已关闭模型下载兜底。")
                cache_root = os.environ.get("YOUYUN_MODELSCOPE_CACHE", "/root/cppt/modelscope-cache").strip()
                if not cache_root.startswith("/"):
                    raise RuntimeError("YOUYUN_MODELSCOPE_CACHE 必须是优云实例内的绝对路径。")
                download_script = self._model_download_script()
                download_log = posixpath.join(remote_workdir, "model-download.log")
                download_pid = posixpath.join(remote_workdir, "model-download.pid")
                download_command = (
                    f"mkdir -p {shlex.quote(cache_root)}; "
                    f"nohup {shlex.quote(self.remote_python)} -u -c {shlex.quote(download_script)} "
                    f"{shlex.quote(model_repo)} {shlex.quote(cache_root)} {shlex.quote(os.environ.get('YOUYUN_MODEL_DOWNLOAD_WORKERS', '4'))} > {shlex.quote(download_log)} 2>&1 "
                    f"& echo $! > {shlex.quote(download_pid)}"
                )
                phase = "model_download"
                await self._emit_stage(on_event, "正在下载 SDXL 基座模型。", phase=phase, progress=8, run_started_at=run_started_at)
                await asyncio.to_thread(ssh.run, download_command, 60, True, retry_on_disconnect=False)
                download_deadline = time.monotonic() + 3600.0
                download_output = ""
                while time.monotonic() < download_deadline:
                    current_instance = await asyncio.to_thread(self.manager.get, str(getattr(instance, "uhost_id", "")))
                    if current_instance is not None and str(getattr(current_instance, "state", "")) in {"Stopped", "Stop", "Fail", "Error", "Terminated", "Deleted"}:
                        raise RuntimeError(f"优云实例在基座模型下载过程中进入异常状态 {getattr(current_instance, 'state', '')}。")
                    code, status_output = await asyncio.to_thread(
                        _run_ssh,
                        ssh,
                        f"if kill -0 $(cat {shlex.quote(download_pid)}) 2>/dev/null; then echo running; else echo stopped; fi",
                        30,
                        False,
                        retry_on_disconnect=True,
                    )
                    if code == 0 and "stopped" in status_output:
                        _, download_output = await asyncio.to_thread(ssh.run, f"cat {shlex.quote(download_log)}", 60, False)
                        break
                    await asyncio.sleep(min(15.0, self.poll_interval))
                else:
                    _, download_output = await asyncio.to_thread(ssh.run, f"tail -n 80 {shlex.quote(download_log)}", 60, False)
                    raise RuntimeError(f"优云基座模型下载超时：{download_output[-1200:]}")
                if "Traceback (most recent call last)" in download_output or not download_output.strip():
                    raise RuntimeError(f"优云基座模型下载失败：{download_output[-1200:]}")
                model_path = next((line.strip() for line in reversed(download_output.splitlines()) if line.strip().startswith("/")), "")
                if not model_path:
                    raise RuntimeError("优云基座模型下载完成但未返回目录。")
                phase = "model_resolution"
                await self._emit_stage(on_event, "SDXL 基座模型准备完成。", phase=phase, progress=9, run_started_at=run_started_at)
            config = json.loads((root / "train_config.json").read_text(encoding="utf-8"))
            config.update({"model_repo_id": model_repo, "model_path": model_path, "dataset_dir": posixpath.join(remote_bundle, "dataset"), "train_file": posixpath.join(remote_bundle, "train.jsonl"), "output_dir": posixpath.join(remote_workdir, "output"), "python_executable": self.remote_python})
            command = build_sdxl_lora_command(config)
            script_path = os.environ.get("XUNTUI_SDXL_LORA_SCRIPT", "/opt/diffusers/examples/text_to_image/train_text_to_image_lora_sdxl.py")
            command[1] = script_path
            await asyncio.to_thread(ssh.mkdirs, posixpath.join(remote_workdir, "output"))
            command_text = shlex.join(command)
            pid_path = posixpath.join(remote_workdir, "worker.pid")
            log_path = posixpath.join(remote_workdir, "worker.log")
            phase = "script_setup"
            artifact = load_sdxl_script_artifact()
            remote_probe = f"if [ -s {shlex.quote(script_path)} ]; then sha256sum {shlex.quote(script_path)}; else exit 44; fi"
            setup_code, setup_output = await asyncio.to_thread(_run_ssh, ssh, remote_probe, 180, False, retry_on_disconnect=True)
            remote_digest = setup_output.strip().split()[0] if setup_code == 0 and setup_output.strip() else ""
            script_source = "image"
            attempts = 1
            if remote_digest != artifact.sha256:
                resolved = await asyncio.to_thread(self._build_script_resolver().resolve)
                staging = posixpath.join(remote_workdir, f".{artifact.filename}.uploading")
                await asyncio.to_thread(ssh.mkdirs, posixpath.dirname(script_path))
                await asyncio.to_thread(ssh.put_file, resolved.local_path, staging)
                digest_code, digest_output = await asyncio.to_thread(
                    _run_ssh, ssh, f"sha256sum {shlex.quote(staging)}", 180, False, retry_on_disconnect=True
                )
                uploaded_digest = digest_output.strip().split()[0] if digest_code == 0 and digest_output.strip() else ""
                if uploaded_digest != artifact.sha256:
                    raise RuntimeError(f"优云训练脚本远端 SHA-256 校验失败：{uploaded_digest or 'missing'}")
                move_code, move_output = await asyncio.to_thread(
                    _run_ssh, ssh, f"mv {shlex.quote(staging)} {shlex.quote(script_path)}", 60, False, retry_on_disconnect=True
                )
                if move_code != 0:
                    raise RuntimeError(f"优云训练脚本安装失败：{move_output[-1200:]}")
                script_source = resolved.source
                attempts = resolved.attempts
            await self._emit_stage(
                on_event,
                f"SDXL 训练脚本已就绪（来源：{script_source}，尝试 {attempts} 次）。",
                phase=phase,
                progress=9,
                run_started_at=run_started_at,
                scriptSource=script_source,
                scriptCommit=artifact.commit,
                scriptSizeBytes=artifact.size_bytes,
                scriptSha256=artifact.sha256,
                scriptAttempts=attempts,
            )
            launch = f"nohup {command_text} > {shlex.quote(log_path)} 2>&1 & echo $! > {shlex.quote(pid_path)}"
            await asyncio.to_thread(ssh.run, launch, 60, False, retry_on_disconnect=False)
            phase = "running"
            await self._emit_stage(on_event, "优云训练进程已启动。", phase=phase, progress=10, run_started_at=run_started_at)
            deadline = time.monotonic() + self.timeout
            last_progress_at = time.monotonic()
            last_output = ""
            worker_event_state: dict[str, Any] = {"logs": set(), "metrics": {}}
            while time.monotonic() < deadline:
                if cancel_event and cancel_event.is_set():
                    await asyncio.to_thread(ssh.run, f"kill $(cat {shlex.quote(pid_path)}) 2>/dev/null || true", 30, False)
                    result = {"status": "stopped", "failureCode": "training_stopped"}
                    return result
                # The provider can stop an unhealthy instance while the SSH
                # session is still half-open. Check control-plane state before
                # issuing another remote command so the task fails promptly.
                current_instance = await asyncio.to_thread(self.manager.get, str(getattr(instance, "uhost_id", "")))
                if current_instance is not None and str(getattr(current_instance, "state", "")) in {"Stopped", "Stop", "Fail", "Error", "Terminated", "Deleted"}:
                    raise RuntimeError(f"优云实例在训练过程中进入异常状态 {getattr(current_instance, 'state', '')}。")
                _, status = await asyncio.to_thread(_run_ssh, ssh, f"if kill -0 $(cat {shlex.quote(pid_path)}) 2>/dev/null; then echo running; else echo stopped; fi", 30, False, retry_on_disconnect=True)
                if on_event is not None:
                    _, worker_output = await asyncio.to_thread(_run_ssh, ssh, f"tail -c 262144 {shlex.quote(log_path)} 2>/dev/null || true", 30, False, retry_on_disconnect=True)
                    await self._emit_worker_updates(worker_output, worker_event_state, on_event)
                    if worker_output != last_output:
                        last_output = worker_output
                        last_progress_at = time.monotonic()
                if "stopped" in status:
                    break
                if self.stall_timeout > 0 and time.monotonic() - last_progress_at > self.stall_timeout:
                    raise TimeoutError(f"优云文生图微调超过 {int(self.stall_timeout)} 秒无进展。")
                await asyncio.sleep(self.poll_interval)
            phase = "collecting"
            await self._emit_stage(on_event, "正在收集文生图 LoRA 训练产物。", phase=phase, progress=98, run_started_at=run_started_at)
            local_output = Path(str(task.get("outputDir") or root / "output")).expanduser().resolve()
            try:
                await asyncio.to_thread(ssh.get_file, log_path, local_output.parent / "worker.log")
            except Exception:
                pass
            await asyncio.to_thread(ssh.get_tree, posixpath.join(remote_workdir, "output"), local_output)
            weights = next((item for item in local_output.iterdir() if item.is_file() and item.name in {"pytorch_lora_weights.safetensors", "adapter_model.safetensors"}), None) if local_output.is_dir() else None
            if weights is None:
                log_tail = ""
                log_file = local_output.parent / "worker.log"
                if log_file.is_file():
                    log_tail = log_file.read_text(encoding="utf-8", errors="replace")[-1200:]
                raise RuntimeError(f"优云训练完成但未找到 LoRA 权重文件。{log_tail}")
            for variant in ("best", "final"):
                target = local_output / variant
                target.mkdir(exist_ok=True)
                for source in local_output.iterdir():
                    if source.is_file() and source.name in {weights.name, "adapter_config.json"}:
                        target_source = target / source.name
                        if not target_source.exists():
                            target_source.write_bytes(source.read_bytes())
            artifacts = collect_image_generation_adapters(local_output, {"datasetId": task.get("datasetId"), "modelRepoId": model_repo})
            observability = {"logs": [], "metrics": []}
            worker_log = local_output.parent / "worker.log"
            if worker_log.is_file():
                observability = parse_sdxl_training_log(worker_log.read_text(encoding="utf-8", errors="replace"))
            result = {"status": "completed", "artifacts": artifacts, "progress": 100, **observability}
            return result
        except Exception as exc:
            code = self.failure_code(exc, phase)
            messages = {
                "ssh_handshake_eof": "优云实例 SSH 握手被远端关闭。",
                "ssh_channel_disconnected": "优云实例 SSH 通道中断。",
                "dependency_install_failed": "优云 Diffusers 依赖安装失败。",
                "training_script_download_failed": "优云训练脚本不可用。",
                "base_model_download_failed": "优云 SDXL 基座模型下载失败。",
                "resource_timeout": "优云 SDXL LoRA 训练超时。",
                "remote_environment_unavailable": "优云远端训练环境不可用。",
                "youyun_create_no_capacity": "优云所选地域和规格当前无库存。",
                "youyun_create_service_unavailable": "优云实例创建服务暂不可用。",
                "instance_provision_failed": "优云实例创建失败。",
            }
            actions = {
                "ssh_handshake_eof": "更换可用实例或镜像后重试。",
                "ssh_channel_disconnected": "检查实例状态和 SSH 日志后重试。",
                "dependency_install_failed": "使用预装 Diffusers 的镜像，避免在线 pip 安装。",
                "training_script_download_failed": "将训练脚本预置到镜像或显式开启下载兜底。",
                "base_model_download_failed": "将 SDXL 基座模型放入共享缓存后重试。",
                "resource_timeout": "确认实例状态并检查训练日志后重试。",
                "remote_environment_unavailable": "使用预装 Torch、Diffusers 和 PEFT 的镜像。",
                "youyun_create_no_capacity": "更换有库存的地域、可用区或 GPU 规格后重试；本次未创建实例。",
                "youyun_create_service_unavailable": "优云创建接口暂不可用；已停止重复创建并确认无实例句柄，稍后重试。",
                "instance_provision_failed": "检查优云地域、可用区、镜像和规格参数后重试；确认无实例句柄后再提交。",
            }
            result = {"status": "failed", "failureCode": code, "failureMessage": messages.get(code, "优云 SDXL LoRA 训练失败。"), "suggestedAction": actions.get(code, "检查优云镜像、基座模型和训练日志后重试。"), "error": self._error_text(exc), "phase": phase}
            return result
        finally:
            if ssh is not None:
                ssh.close()
            if instance is not None:
                try:
                    cleanup_kwargs: dict[str, Any] = {"stop_first": True}
                    if resource.get("region"):
                        cleanup_kwargs["region"] = resource.get("region")
                    if resource.get("zone"):
                        cleanup_kwargs["zone"] = resource.get("zone")
                    cleanup = await asyncio.to_thread(
                        self.manager.cleanup_after_task,
                        str(instance.uhost_id),
                        **cleanup_kwargs,
                    )
                    result["cleanupState"] = "completed" if cleanup.get("terminated") else "failed"
                    result["cleanupError"] = cleanup.get("error")
                except Exception as exc:
                    result["cleanupState"] = "failed"
                    result["cleanupError"] = self._error_text(exc)


__all__ = ["YouyunImageGenerationTrainingExecutor"]
