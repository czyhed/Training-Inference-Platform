from __future__ import annotations

"""优云 Diffusers 文生图执行器。

该模块只负责受控的远端生命周期；客户端不能注入任意命令。所有
实例、SSH、模型解析、状态轮询和清理动作都通过现有优云 Provider 完成。
"""

import asyncio
import hashlib
import json
import os
import posixpath
import re
import shlex
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .instance_manager import YouyunInstanceManager
from .models import YouyunApiError
from .resource_health import find_uhost_fallback, is_transient_capacity_failure, youyun_resource_health
from .ssh import YouyunSSHClient


@dataclass
class YouyunDiffusersHandle:
    instance_id: str
    workdir: str
    status_path: str
    log_path: str
    results_dir: str
    pid_path: str
    region: str | None = None
    zone: str | None = None
    ssh: YouyunSSHClient | None = None

    def persisted(self) -> dict[str, str]:
        return {
            "instanceId": self.instance_id,
            "workdir": self.workdir,
            "statusPath": self.status_path,
            "logPath": self.log_path,
            "resultsDir": self.results_dir,
            "pidPath": self.pid_path,
            "region": self.region or "",
            "zone": self.zone or "",
        }


class YouyunDiffusersProvider:
    _ADAPTER_FILES = {"adapter_config.json", "adapter_model.safetensors", "adapter_model.bin", "pytorch_lora_weights.safetensors"}
    _FAILURE_MESSAGES = {
        "instance_start_failed": "优云实例启动失败。",
        "runtime_unavailable": "优云实例运行环境不可用。",
        "model_unavailable": "优云基座模型不可用。",
        "adapter_unavailable": "训练完成的 Adapter 不可用。",
        "generation_failed": "远端文生图生成失败。",
        "result_download_failed": "生成结果下载失败。",
        "cleanup_failed": "优云实例清理失败。",
    }
    _FAILURE_ACTIONS = {
        "instance_start_failed": "检查优云库存、镜像和资源配置后重试。",
        "runtime_unavailable": "改用预置 Diffusers 镜像，或检查远端 Python 环境。",
        "model_unavailable": "确认镜像已挂载基座模型，或开启模型下载兜底后重试。",
        "adapter_unavailable": "确认 Adapter 目录和 LoRA 权重文件完整后重试。",
        "generation_failed": "检查生成参数和远端 worker 日志后重试。",
        "result_download_failed": "确认远端结果文件完整后重试任务。",
        "cleanup_failed": "重试清理优云实例，避免继续产生费用。",
    }
    def __init__(
        self,
        manager: YouyunInstanceManager,
        *,
        ssh_factory: Callable[[], YouyunSSHClient] = YouyunSSHClient,
        remote_root: str = "/root/cppt/image-generation",
        remote_python: str | None = None,
        poll_interval: float = 2.0,
        startup_timeout: float = 1800.0,
    ) -> None:
        self.manager = manager
        self.ssh_factory = ssh_factory
        self.remote_root = remote_root.rstrip("/")
        self.remote_python = (remote_python or os.environ.get("XUNTUI_DIFFUSERS_PYTHON", "python3")).strip() or "python3"
        self.poll_interval = poll_interval
        self.startup_timeout = startup_timeout

    @staticmethod
    def _stage(callback: Callable[..., None] | None, stage: str, message: str, **details: Any) -> None:
        if callback is None:
            return
        try:
            callback(stage, message, details)
        except TypeError:
            callback(message)

    @classmethod
    def _safe_detail(cls, value: Any) -> str:
        """Keep legacy error field useful without leaking commands or credentials."""
        text = str(value or "").strip()
        if not text:
            return ""
        text = text.splitlines()[0].strip()[:600]
        lowered = text.lower()
        sensitive_markers = (
            "password=",
            "passwd=",
            "token=",
            "api_key",
            "access_key",
            "access token",
            "secret=",
            "authorization:",
            "private key",
            "-----begin",
            "command=",
            "nohup ",
            "curl ",
            "ssh ",
            "traceback",
            "/root/",
            "/models",
            "--config-path",
            "exit=",
        )
        credential_key = re.search(r"(?:password|passwd|token|secret|api[_-]?key|access[_-]?key|authorization)\s*[:=]", lowered)
        known_token = re.search(r"(?:sk|hf|ghp|github_pat)_[A-Za-z0-9_-]{12,}|AKIA[0-9A-Z]{12,}", text)
        command_like = re.search(r"(?:^|\s)(?:sudo|bash|sh|python(?:3)?|pip|curl|wget|ssh)\s+[-/A-Za-z0-9]", lowered)
        if any(marker in lowered for marker in sensitive_markers) or credential_key or known_token or command_like:
            return "远端文生图执行失败。"
        return text

    @classmethod
    def _failure_payload(cls, code: str, detail: Any = None) -> dict[str, str]:
        normalized = code if code in cls._FAILURE_MESSAGES else "generation_failed"
        safe_detail = cls._safe_detail(detail)
        return {
            "failureCode": normalized,
            "failureMessage": cls._FAILURE_MESSAGES[normalized],
            "suggestedAction": cls._FAILURE_ACTIONS[normalized],
            "error": safe_detail or cls._FAILURE_MESSAGES[normalized],
        }

    def _connect(self, instance: Any) -> YouyunSSHClient:
        credentials = self.manager.ssh_credentials(instance)
        if not credentials.get("host") or not credentials.get("password"):
            raise RuntimeError("优云实例缺少 SSH 连接信息（IP 或密码为空）。")
        timeout = max(10.0, min(900.0, float(os.environ.get("YOUYUN_SSH_READY_TIMEOUT_SECONDS", "300"))))
        delay = max(1.0, min(30.0, float(os.environ.get("YOUYUN_SSH_CONNECT_RETRY_DELAY_SECONDS", "5"))))
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        attempts = 0
        while time.monotonic() < deadline:
            get_instance = getattr(self.manager, "get", None)
            current = get_instance(str(getattr(instance, "uhost_id", ""))) if callable(get_instance) else None
            if current is not None and str(getattr(current, "state", "")) in {"Stopped", "Stop", "Fail", "Error", "Terminated", "Deleted"}:
                raise RuntimeError(f"优云实例进入异常状态 {getattr(current, 'state', '')}，无法建立 SSH 连接。")
            if current is not None:
                refreshed = self.manager.ssh_credentials(current)
                if refreshed.get("host") and refreshed.get("password"):
                    credentials = refreshed

            attempts += 1
            host = str(credentials["host"])
            port = int(credentials.get("port") or 22)
            remaining = max(1.0, deadline - time.monotonic())
            if self.ssh_factory is YouyunSSHClient:
                try:
                    with socket.create_connection((host, port), timeout=min(20.0, remaining)) as probe:
                        probe.settimeout(min(20.0, remaining))
                        banner = probe.recv(128)
                        if not banner.startswith(b"SSH-"):
                            raise OSError("SSH 端口已连接但未返回有效 banner")
                except (OSError, TimeoutError) as exc:
                    last_error = exc
                    time.sleep(min(delay, max(0.0, deadline - time.monotonic())))
                    continue

            try:
                try:
                    ssh = self.ssh_factory(
                        connect_timeout=float(os.environ.get("YOUYUN_SSH_CONNECT_TIMEOUT_SECONDS", "20"))
                    )
                except TypeError:
                    ssh = self.ssh_factory()
                ssh.connect(host, port, credentials.get("user") or "root", credentials["password"])
                return ssh
            except Exception as exc:
                last_error = exc
                try:
                    ssh.close()
                except Exception:
                    pass
                time.sleep(min(delay, max(0.0, deadline - time.monotonic())))
        raise RuntimeError(f"优云 SSH 连接超时（已尝试 {attempts} 次）：{last_error or '未收到 SSH 响应'}") from last_error

    def _prepare_runtime(self, ssh: YouyunSSHClient, *, allow_install: bool = False) -> str:
        configured = shlex.quote(self.remote_python)
        candidates = f"{configured} /usr/local/miniconda3/envs/*/bin/python /opt/conda/envs/*/bin/python /opt/conda/bin/python /root/miniconda3/envs/*/bin/python /root/miniconda3/bin/python /usr/local/bin/python3 /usr/bin/python3"
        probe = (
            f"for candidate in {candidates}; do "
            "if [ -x \"$candidate\" ] && \"$candidate\" -c 'import torch, diffusers, peft' >/dev/null 2>&1; then "
            "printf '%s\\n' \"$candidate\"; exit 0; fi; done; exit 42"
        )
        code, output = ssh.run(probe, 60, False)
        if code == 0 and output.strip():
            self.remote_python = output.strip().splitlines()[-1]
            return self.remote_python
        install = allow_install or os.environ.get("XUNTUI_REMOTE_DIFFUSERS_INSTALL", "0").strip().lower() in {"1", "true", "yes", "on"}
        if not install:
            raise RuntimeError("优云镜像中未找到可导入 torch 和 diffusers 的 Python；请使用预置 Diffusers 镜像或显式开启安装兜底。")
        torch_probe = (
            f"for candidate in {candidates}; do "
            "if [ -x \"$candidate\" ] && \"$candidate\" -c 'import torch' >/dev/null 2>&1; then "
            "printf '%s\\n' \"$candidate\"; exit 0; fi; done; exit 42"
        )
        torch_code, torch_output = ssh.run(torch_probe, 60, False)
        install_python = torch_output.strip().splitlines()[-1] if torch_code == 0 and torch_output.strip() else self.remote_python
        configured_install_python = shlex.quote(install_python)
        package = os.environ.get("XUNTUI_REMOTE_DIFFUSERS_PACKAGE", "diffusers transformers accelerate safetensors peft").strip()
        command = f"{configured_install_python} -m pip install --disable-pip-version-check {package} && {configured_install_python} -c 'import torch, diffusers, peft'"
        code, output = ssh.run(command, 1800, False)
        if code != 0:
            raise RuntimeError(f"远端 Diffusers 安装失败：{output[-2000:]}")
        self.remote_python = install_python
        return self.remote_python

    def _upload_adapter(self, ssh: YouyunSSHClient, local_root: Path, remote_root: str) -> None:
        for item in sorted(local_root.iterdir()):
            if not item.is_file() or item.name not in self._ADAPTER_FILES:
                continue
            remote_path = posixpath.join(remote_root, item.name)
            try:
                digest = hashlib.sha256(item.read_bytes()).hexdigest()
                code, output = ssh.run(
                    f"if [ -f {shlex.quote(remote_path)} ]; then sha256sum {shlex.quote(remote_path)}; fi",
                    20,
                    False,
                )
                if code == 0 and output.strip().split() and output.strip().split()[0] == digest:
                    continue
            except Exception:
                pass
            ssh.put_file(item, remote_path)

    @staticmethod
    def _model_download_script() -> str:
        """Download the minimal fp16 Diffusers files required by SDXL."""
        return (
            "import importlib.util,sys\n"
            "allow=['model_index.json','configuration.json','scheduler/*.json',\n"
            "       'tokenizer/*','tokenizer_2/*',\n"
            "       'text_encoder/config.json','text_encoder/model.fp16.safetensors',\n"
            "       'text_encoder_2/config.json','text_encoder_2/model.fp16.safetensors',\n"
            "       'unet/config.json','unet/diffusion_pytorch_model.fp16.safetensors',\n"
            "       'vae/config.json','vae/diffusion_pytorch_model.fp16.safetensors']\n"
            "ignore=['*flax*','*.msgpack','*.onnx','*.ckpt','*.bin']\n"
            "repo,cache=sys.argv[1],sys.argv[2]\n"
            "error=None\n"
            "if importlib.util.find_spec('modelscope') is not None:\n"
            "    try:\n"
            "        from modelscope import snapshot_download\n"
            "        print(snapshot_download(repo, cache_dir=cache, allow_file_pattern=allow, ignore_file_pattern=ignore, max_workers=2))\n"
            "        raise SystemExit(0)\n"
            "    except Exception as exc:\n"
            "        error=exc\n"
            "if importlib.util.find_spec('huggingface_hub') is None:\n"
            "    raise RuntimeError(f'优云实例缺少 ModelScope/Hugging Face 下载依赖: {error}')\n"
            "from huggingface_hub import snapshot_download\n"
            "print(snapshot_download(repo, cache_dir=cache, allow_patterns=allow, ignore_patterns=ignore, max_workers=2))\n"
        )

    @staticmethod
    def _model_resolution_command(repo_id: str) -> str:
        parts = str(repo_id or "").strip().split("/")
        if len(parts) != 2 or any(not part or part in {".", ".."} or not all(ch.isalnum() or ch in "._-" for ch in part) for part in parts):
            raise ValueError("优云模型 Repo ID 不安全或格式无效。")
        roots = os.environ.get(
            "YOUYUN_BASE_MODEL_ROOT",
            "/root/models:/models:/workspace/models:/mnt/models:/data/models:/root/cppt/modelscope-cache:/root/.cache/modelscope/hub:/root/.cache/huggingface/hub",
        ).strip()
        explicit = os.environ.get("YOUYUN_BASE_MODEL_PATH", "").strip()
        slug = "___".join(parts)
        explicit_loop = ""
        if explicit:
            explicit_loop = (
                f"for candidate in {shlex.quote(explicit)}; do "
                "if is_complete_model \"$candidate\"; then printf 'base-model-path:%s\\n' \"$candidate\"; exit 0; fi; done; "
            )
        return (
            "is_complete_model() { d=\"$1\"; "
            "[ -d \"$d\" ] && { [ -f \"$d/model_index.json\" ] || [ -f \"$d/config.json\" ]; } && "
            "find \"$d\" -maxdepth 4 -type f \\( -name '*.safetensors' -o -name '*.bin' \\) -print -quit | grep -q .; }; "
            f"{explicit_loop}"
            + (
                f"for root in {shlex.join(roots.split(':'))}; do "
                "if [ -d \"$root\" ]; then for candidate in "
                f"\"$root/{slug}\" \"$root/{repo_id}\" \"$root/models--{repo_id.replace('/', '--')}/snapshots\"/*; do "
                "if is_complete_model \"$candidate\"; then printf 'base-model-path:%s\\n' \"$candidate\"; exit 0; fi; "
                "done; fi; done; "
                if roots
                else ""
            )
            + "printf 'base-model-error:no matching complete model\\n'"
        )

    def _resolve_remote_model(self, ssh: YouyunSSHClient, repo_id: str) -> str:
        _, output = ssh.run(self._model_resolution_command(repo_id), 60, False)
        for line in output.splitlines():
            if line.startswith("base-model-path:"):
                candidate = line.removeprefix("base-model-path:").strip()
                if candidate.startswith("/"):
                    return candidate
        allow_download = os.environ.get("YOUYUN_ALLOW_MODEL_DOWNLOAD", "0").strip().lower() in {"1", "true", "yes", "on"}
        if not allow_download:
            raise RuntimeError(f"未找到优云本地基座模型 {repo_id}，且已关闭 ModelScope/Hugging Face 下载兜底。")
        cache_root = os.environ.get("YOUYUN_MODELSCOPE_CACHE", "/root/cppt/modelscope-cache").strip()
        if not cache_root.startswith("/"):
            raise ValueError("YOUYUN_MODELSCOPE_CACHE 必须使用优云实例内的绝对路径。")
        is_sdxl = "sdxl" in repo_id.casefold() or "stable-diffusion-xl" in repo_id.casefold()
        script = self._model_download_script() if is_sdxl else (
            "import importlib.util, sys\n"
            "if importlib.util.find_spec('modelscope') is not None:\n"
            " from modelscope import snapshot_download\n"
            "else:\n"
            " from huggingface_hub import snapshot_download\n"
            "print(snapshot_download(sys.argv[1], cache_dir=sys.argv[2]))\n"
        )
        command = (
            f"mkdir -p {shlex.quote(cache_root)}; {shlex.quote(self.remote_python)} -c "
            f"{shlex.quote(script)} {shlex.quote(repo_id)} {shlex.quote(cache_root)}"
        )
        code, downloaded = ssh.run(command, 3600 if is_sdxl else 1800, False)
        if code != 0:
            raise RuntimeError(f"ModelScope/Hugging Face 下载基座模型失败：{downloaded[-2000:]}")
        for line in reversed(downloaded.splitlines()):
            value = line.strip()
            if value.startswith("/"):
                return value
        raise RuntimeError("模型下载完成但未返回有效的基座模型目录。")

    async def provision_service(
        self,
        service_id: str,
        task: Mapping[str, Any],
        report_progress: Callable[[str, int, str | None], None] | None = None,
    ) -> dict[str, Any]:
        """Create and prepare one long-lived Diffusers service lease.

        The returned handle intentionally keeps its SSH session open.  The
        service runtime owns it until an explicit stop releases the instance.
        """

        def notify(stage: str, progress: int, message: str | None = None) -> None:
            if report_progress is None:
                return
            try:
                report_progress(stage, progress, message)
            except Exception:
                # Progress reporting must never break provisioning.
                pass

        resource = dict(task.get("resource") or {})
        instance = None
        ssh: YouyunSSHClient | None = None
        try:
            notify("creating_instance", 10, "正在创建优云 GPU 实例…")
            for attempt in range(2):
                try:
                    instance = await asyncio.to_thread(
                        self.manager.create_one_shot,
                        service_id,
                        gpu=resource.get("gpuCount"), cpu=resource.get("cpuCores"),
                        memory_gb=resource.get("memoryGb"), gpu_type=resource.get("gpuType"),
                        image_id=resource.get("imageId"), charge_type=resource.get("chargeType"),
                        region=resource.get("region"), zone=resource.get("zone"),
                        instance_mode="pod" if resource.get("instanceMode") in {None, "auto"} else resource.get("instanceMode"),
                        name=f"cppt-img-service-{service_id}", idempotency_key=f"{service_id}:image-service:{attempt}",
                    )
                    break
                except Exception as exc:
                    automatic = resource.get("instanceMode") in {None, "auto"} or bool(resource.get("fallbackEligible"))
                    transient = is_transient_capacity_failure(exc)
                    if not automatic or not transient or attempt:
                        raise
                    fallback_resource = {**resource, "instanceMode": "pod"}
                    code = str(getattr(exc, "ret_code", None) or "capacity_unavailable")
                    youyun_resource_health.mark_failure(fallback_resource, code)
                    fallback = find_uhost_fallback(self.manager, fallback_resource)
                    if fallback is None:
                        raise
                    resource = {**resource, **fallback, "attemptedModes": ["pod", "uhost"], "fallback": True}
            notify("instance_running", 25, "优云实例已创建并进入运行状态。")
            handle = YouyunDiffusersHandle(
                instance_id=str(instance.uhost_id),
                workdir=posixpath.join(self.remote_root, f"services/{service_id}"),
                status_path=posixpath.join(self.remote_root, f"services/{service_id}/status.json"),
                log_path=posixpath.join(self.remote_root, f"services/{service_id}/service.log"),
                results_dir=posixpath.join(self.remote_root, f"services/{service_id}/results"),
                pid_path=posixpath.join(self.remote_root, f"services/{service_id}/service.pid"),
                region=getattr(instance, "region", None),
                zone=getattr(instance, "zone", None),
            )
            ssh = await asyncio.to_thread(self._connect, instance)
            handle.ssh = ssh
            notify("ssh_connected", 35, "已建立 SSH 连接。")
            allow_install = bool(resource.get("requiresDiffusersInstall"))
            notify(
                "preparing_runtime",
                45,
                "正在远端安装 Diffusers 依赖，可能需要 10-30 分钟…" if allow_install else "正在检查 Diffusers 运行环境…",
            )
            await asyncio.to_thread(self._prepare_runtime, ssh, allow_install=allow_install)
            notify("resolving_model", 60, "正在解析基座模型…")
            model_path = await asyncio.to_thread(self._resolve_remote_model, ssh, str(task.get("modelRepoId") or ""))
            await asyncio.to_thread(ssh.mkdirs, handle.workdir)
            await asyncio.to_thread(ssh.mkdirs, handle.results_dir)
            adapter_path = Path(str(task.get("adapterPath") or "")).expanduser()
            remote_adapter = ""
            if str(task.get("adapterPath") or "").strip():
                if not adapter_path.is_dir():
                    raise RuntimeError(f"Adapter 路径不存在或不是目录：{adapter_path}")
                notify("uploading_adapter", 75, "正在上传 Adapter…")
                remote_adapter = posixpath.join(handle.workdir, "adapter")
                await asyncio.to_thread(ssh.mkdirs, remote_adapter)
                await asyncio.to_thread(self._upload_adapter, ssh, adapter_path, remote_adapter)
            notify("finalizing", 90, "正在写入服务配置…")
            await asyncio.to_thread(ssh.put_text, json.dumps({"modelPath": model_path, "adapterPath": remote_adapter}, ensure_ascii=False), posixpath.join(handle.workdir, "service.json"))
            return {"handle": handle, "modelPath": model_path, "adapterPath": remote_adapter}
        except Exception:
            if ssh is not None:
                try:
                    ssh.close()
                except Exception:
                    pass
            if instance is not None:
                try:
                    await asyncio.to_thread(self.manager.cleanup_after_task, str(instance.uhost_id), stop_first=False, region=getattr(instance, "region", None), zone=getattr(instance, "zone", None))
                except Exception:
                    pass
            raise

    @staticmethod
    def _worker_script() -> str:
        return '''from __future__ import annotations
import argparse, json, pathlib, traceback

def write_status(path, payload):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-path", required=True)
    parser.add_argument("--status-path", required=True)
    parser.add_argument("--results-dir", required=True)
    args = parser.parse_args()
    config = json.loads(pathlib.Path(args.config_path).read_text(encoding="utf-8"))
    status_path = pathlib.Path(args.status_path)
    results_dir = pathlib.Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    try:
        import torch
        from diffusers import DiffusionPipeline, DPMSolverMultistepScheduler, EulerDiscreteScheduler
        write_status(status_path, {"state": "running", "progress": 5, "step": 0, "totalSteps": int(config["steps"])})
        precision = str(config.get("precision") or "auto")
        dtype = torch.bfloat16 if precision == "bf16" else torch.float16
        pipe = DiffusionPipeline.from_pretrained(config["modelPath"], torch_dtype=dtype, variant=config.get("variant"))
        if config.get("sampler") == "dpmpp_2m":
            pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config)
        else:
            pipe.scheduler = EulerDiscreteScheduler.from_config(pipe.scheduler.config)
        if config.get("enableCpuOffload") and hasattr(pipe, "enable_model_cpu_offload"):
            pipe.enable_model_cpu_offload()
        else:
            pipe.to("cuda")
        if config.get("enableAttentionSlicing") and hasattr(pipe, "enable_attention_slicing"):
            pipe.enable_attention_slicing()
        if config.get("enableVaeTiling") and getattr(pipe, "vae", None) is not None and hasattr(pipe.vae, "enable_tiling"):
            pipe.vae.enable_tiling()
        if config.get("enableSafetyChecker") and getattr(pipe, "safety_checker", None) is None:
            raise RuntimeError("当前 Diffusers 管线不支持安全检查器。")
        adapter = config.get("adapterPath")
        if adapter and hasattr(pipe, "load_lora_weights"):
            pipe.load_lora_weights(adapter)
        results = []
        total = int(config["batchSize"])
        for index in range(total):
            if config.get("seed", -1) >= 0:
                generator = torch.Generator(device="cuda").manual_seed(int(config["seed"]) + index)
            else:
                generator = None
            image = pipe(prompt=config["prompt"], negative_prompt=config.get("negativePrompt") or None,
                         width=int(config["width"]), height=int(config["height"]),
                         num_inference_steps=int(config["steps"]), guidance_scale=float(config["cfg"]),
                         generator=generator).images[0]
            name = f"{index}.png"
            image.save(results_dir / name)
            results.append({"id": str(index), "fileName": name, "width": int(config["width"]), "height": int(config["height"])})
            write_status(status_path, {"state": "running" if index + 1 < total else "completed", "progress": int((index + 1) * 100 / total), "step": int(config["steps"]), "totalSteps": int(config["steps"]), "results": results})
    except Exception as exc:
        write_status(status_path, {"state": "failed", "progress": 0, "error": str(exc), "traceback": traceback.format_exc()[-4000:]})
        raise

if __name__ == "__main__":
    main()
'''

    async def start(
        self,
        task_id: str,
        task: Mapping[str, Any],
        *,
        on_stage: Callable[..., None] | None = None,
        on_log: Callable[[str], None] | None = None,
        on_handle: Callable[[YouyunDiffusersHandle], None] | None = None,
    ) -> dict[str, Any]:
        resource = dict(task.get("resource") or {})
        provisioned_instance_id: str | None = None
        persisted_service_handle = task.get("serviceHandle")
        try:
            if isinstance(persisted_service_handle, Mapping) and persisted_service_handle.get("instanceId"):
                get_instance = getattr(self.manager, "get", None)
                instance = get_instance(str(persisted_service_handle["instanceId"])) if callable(get_instance) else None
                if instance is None:
                    raise RuntimeError("文生图服务实例不可用，请先重启服务。")
                provisioned_instance_id = None
            else:
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
                        instance_mode="pod" if resource.get("instanceMode") in {None, "auto"} else resource.get("instanceMode"),
                        name=f"cppt-img-{task_id}",
                        idempotency_key=f"{task_id}:image-inference:{provision_attempt}",
                        on_created=record_created_instance,
                    )
                    break
                except Exception as exc:
                    automatic_mode = resource.get("instanceMode") in {None, "auto"} or (resource.get("instanceMode") == "pod" and bool(resource.get("fallbackEligible")))
                    fallback_resource = {**resource, "instanceMode": "pod"}
                    transient = is_transient_capacity_failure(exc)
                    if transient:
                        failure_code = str(exc.ret_code or "capacity_unavailable")
                        youyun_resource_health.mark_failure(fallback_resource, failure_code)
                    fallback = find_uhost_fallback(self.manager, fallback_resource) if automatic_mode and transient else None
                    if fallback is None or provision_attempt:
                        raise
                    if provisioned_instance_id:
                        try:
                            cleanup = await asyncio.to_thread(self.manager.cleanup_after_task, provisioned_instance_id, stop_first=False, region=resource.get("region"), zone=resource.get("zone"))
                        except TypeError:
                            cleanup = await asyncio.to_thread(self.manager.cleanup_after_task, provisioned_instance_id, stop_first=False)
                        if not cleanup.get("terminated"):
                            raise RuntimeError("Pod 文生图推理实例创建失败后释放未确认，已停止切换 UHost。") from exc
                        provisioned_instance_id = None
                    resource = {**resource, **fallback, "attemptedModes": ["pod", "uhost"], "fallback": True}
                    self._stage(on_stage, "resource_fallback", "Pod 文生图推理实例库存或创建接口暂不可用，正在切换到 UHost 兜底。", fallbackReason=resource.get("fallbackReason"), instanceMode="uhost", resource=dict(resource))
        except Exception as exc:  # noqa: BLE001 - classify provider failures for runtime telemetry
            failure = self._failure_payload("instance_start_failed", exc)
            self._stage(on_stage, "failed", failure["failureMessage"], failureCode=failure["failureCode"])
            if on_log:
                on_log(failure["error"])
            return {"status": "failed", "results": [], "cleanupState": "unknown", "cleanupError": None, **failure}
        workdir = posixpath.join(self.remote_root, task_id)
        handle = YouyunDiffusersHandle(
            instance_id=str(instance.uhost_id),
            workdir=workdir,
            status_path=posixpath.join(workdir, "status.json"),
            log_path=posixpath.join(workdir, "worker.log"),
            results_dir=posixpath.join(workdir, "results"),
            pid_path=posixpath.join(workdir, "worker.pid"),
            region=getattr(instance, "region", None),
            zone=getattr(instance, "zone", None),
        )
        if on_handle:
            on_handle(handle)
        self._stage(on_stage, "instance_running", f"优云实例已就绪：{handle.instance_id}。", instanceId=handle.instance_id, handle=handle)
        if on_log:
            on_log(f"优云实例已就绪：{handle.instance_id}。")
        ssh: YouyunSSHClient | None = None
        generation_error: str | None = None
        failure_code: str | None = "runtime_unavailable"
        failure: dict[str, str] = {}
        stopped = False
        results: list[dict[str, Any]] = []
        try:
            self._stage(on_stage, "ssh_connecting", "等待 SSH 连接可用。")
            if on_log:
                on_log("等待 SSH 连接可用。")
            ssh = await asyncio.to_thread(self._connect, instance)
            handle.ssh = ssh
            self._stage(on_stage, "ssh_connected", "SSH 连接已建立。")
            if on_log:
                on_log("SSH 连接已建立。")
            runtime_message = "正在安装远端 Diffusers 依赖。" if resource.get("requiresDiffusersInstall") else "正在检查远端 Diffusers 环境。"
            self._stage(on_stage, "preparing_runtime", runtime_message)
            if on_log:
                on_log(runtime_message)
            await asyncio.to_thread(
                self._prepare_runtime,
                ssh,
                allow_install=bool(resource.get("requiresDiffusersInstall")),
            )
            if on_log:
                on_log("远端 Diffusers 环境已就绪。")
            failure_code = "model_unavailable"
            self._stage(on_stage, "checking_remote_model", "正在检查优云基座模型。")
            if on_log:
                on_log("正在检查优云基座模型。")
            model_path = await asyncio.to_thread(self._resolve_remote_model, ssh, str(task.get("modelRepoId") or task.get("model") or ""))
            if on_log:
                on_log("优云基座模型已就绪。")
            remote_adapter = ""
            adapter_path = Path(str(task.get("adapterPath") or "")).expanduser()
            if str(task.get("adapterPath") or "").strip():
                failure_code = "adapter_unavailable"
                if not adapter_path.is_dir():
                    raise RuntimeError(f"Adapter 路径不存在或不是目录：{adapter_path}")
                remote_adapter = posixpath.join(workdir, "adapter")
                await asyncio.to_thread(ssh.mkdirs, remote_adapter)
                await asyncio.to_thread(self._upload_adapter, ssh, adapter_path, remote_adapter)
                if not any(item.is_file() and item.name in self._ADAPTER_FILES for item in adapter_path.iterdir()):
                    raise RuntimeError("Adapter 目录不包含可加载的 LoRA 权重。")
                if on_log:
                    on_log("Adapter 已上传到优云实例。")
            failure_code = "generation_failed"
            config = {
                "modelPath": model_path,
                "variant": "fp16" if "sdxl" in str(task.get("modelRepoId") or "").casefold() or "stable-diffusion-xl" in str(task.get("modelRepoId") or "").casefold() else None,
                "adapterPath": remote_adapter,
                "prompt": str(task.get("prompt") or ""),
                "negativePrompt": str(task.get("negativePrompt") or ""),
                "width": int(task.get("width") or 1024),
                "height": int(task.get("height") or 1024),
                "steps": int(task.get("steps") or 30),
                "cfg": float(task.get("cfg") or 7),
                "seed": int(task.get("seed") if task.get("seed") is not None else -1),
                "batchSize": int(task.get("batchSize") or 1),
                "sampler": str(task.get("sampler") or "euler"),
                "precision": str(task.get("precision") or "auto"),
                "enableCpuOffload": bool(task.get("enableCpuOffload")),
                "enableAttentionSlicing": bool(task.get("enableAttentionSlicing")),
                "enableVaeTiling": bool(task.get("enableVaeTiling")),
                "enableSafetyChecker": bool(task.get("enableSafetyChecker")),
            }
            await asyncio.to_thread(ssh.mkdirs, workdir)
            await asyncio.to_thread(ssh.mkdirs, handle.results_dir)
            config_path = posixpath.join(workdir, "task.json")
            worker_path = posixpath.join(workdir, "diffusers_worker.py")
            await asyncio.to_thread(ssh.put_text, json.dumps(config, ensure_ascii=False), config_path)
            await asyncio.to_thread(ssh.put_text, self._worker_script(), worker_path)
            launch = (
                f"nohup {shlex.quote(self.remote_python)} {shlex.quote(worker_path)} "
                f"--config-path {shlex.quote(config_path)} --status-path {shlex.quote(handle.status_path)} "
                f"--results-dir {shlex.quote(handle.results_dir)} > {shlex.quote(handle.log_path)} 2>&1 & "
                f"echo $! > {shlex.quote(handle.pid_path)}"
            )
            await asyncio.to_thread(ssh.run, launch, 60, True)
            self._stage(on_stage, "running", "Diffusers worker 已启动，开始生成图片。", progress=5, handle=handle)
            if on_log:
                on_log("Diffusers worker 已启动，开始生成图片。")
            seen_remote_logs: set[str] = set()
            deadline = time.monotonic() + float(task.get("loadTimeout") or self.startup_timeout)
            while time.monotonic() < deadline:
                remote_log = await self.read_log_tail(handle, 40)
                for line in remote_log.splitlines():
                    clean_line = line.strip()
                    if clean_line and clean_line not in seen_remote_logs:
                        seen_remote_logs.add(clean_line)
                        if on_log:
                            on_log(clean_line[:8000])
                status = await self.read_status(handle)
                if status:
                    progress = status.get("progress")
                    if progress is not None:
                        self._stage(on_stage, "running", f"图片生成中 · {int(progress)}%", progress=int(progress), handle=handle)
                    if status.get("state") == "completed":
                        results = [item for item in status.get("results") or [] if isinstance(item, Mapping)]
                        self._stage(on_stage, "collecting", "正在下载生成结果。", progress=98, handle=handle)
                        try:
                            results = await self._download_results(handle, results, task)
                        except Exception as exc:  # noqa: BLE001 - preserve download-specific diagnosis
                            failure_code = "result_download_failed"
                            raise RuntimeError(self._safe_detail(exc) or "生成结果下载失败。") from exc
                        break
                    if status.get("state") == "failed":
                        raise RuntimeError(str(status.get("error") or "远端 Diffusers worker 失败"))
                if not await self.process_alive(handle):
                    raise RuntimeError("远端 Diffusers 进程已退出。")
                await asyncio.sleep(self.poll_interval)
            else:
                raise RuntimeError("优云 Diffusers 任务超时，未收到完成状态。")
        except asyncio.CancelledError:
            stopped = True
            generation_error = None
        except Exception as exc:  # noqa: BLE001 - persist generation failure and clean instance
            generation_error = str(exc)
            failure = self._failure_payload(failure_code or "generation_failed", generation_error)
            self._stage(on_stage, "failed", failure["failureMessage"], failureCode=failure["failureCode"])
            if on_log:
                on_log(failure["error"])
        finally:
            if ssh is not None:
                try:
                    ssh.close()
                except Exception:
                    pass
            handle.ssh = None
        if isinstance(persisted_service_handle, Mapping) and persisted_service_handle.get("instanceId"):
            cleanup = {"terminated": True}
        else:
            try:
                cleanup = await asyncio.to_thread(self.manager.cleanup_after_task, handle.instance_id, stop_first=False, region=getattr(handle, "region", None), zone=getattr(handle, "zone", None))
            except Exception as exc:  # noqa: BLE001 - cleanup must not hide primary result
                cleanup = {"terminated": False, "error": self._safe_detail(exc) or "优云实例清理失败。"}
        cleanup_state = "released" if cleanup.get("terminated") else "failed"
        cleanup_error = self._safe_detail(cleanup.get("error")) or (None if cleanup_state == "released" else "优云实例清理失败。")
        if generation_error:
            return {"status": "failed", "results": results, "cleanupState": cleanup_state, "cleanupError": cleanup_error, **failure, **({} if cleanup_state == "released" else {"primaryFailureCode": failure.get("failureCode")})}
        if stopped:
            if cleanup_state == "failed":
                failure = self._failure_payload("cleanup_failed", cleanup_error)
                return {"status": "stopped", "results": results, "cleanupState": cleanup_state, "cleanupError": cleanup_error, **failure}
            return {"status": "stopped", "results": results, "cleanupState": cleanup_state, "cleanupError": cleanup_error}
        if cleanup_state == "failed":
            failure = self._failure_payload("cleanup_failed", cleanup_error)
            return {"status": "completed", "results": results, "cleanupState": cleanup_state, "cleanupError": cleanup_error, **failure}
        return {"status": "completed", "results": results, "cleanupState": cleanup_state, "cleanupError": cleanup_error}

    async def read_status(self, handle: YouyunDiffusersHandle) -> dict[str, Any]:
        if handle.ssh is None:
            return {}
        code, output = await asyncio.to_thread(handle.ssh.run, f"cat {shlex.quote(handle.status_path)}", 10, False)
        if code != 0:
            return {}
        try:
            value = json.loads(output.strip().splitlines()[-1])
        except (ValueError, IndexError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    async def process_alive(self, handle: YouyunDiffusersHandle) -> bool:
        if handle.ssh is None:
            return False
        code, _ = await asyncio.to_thread(handle.ssh.run, f"if [ -s {shlex.quote(handle.pid_path)} ] && kill -0 $(cat {shlex.quote(handle.pid_path)}) 2>/dev/null; then exit 0; else exit 1; fi", 10, False)
        return code == 0

    async def read_log_tail(self, handle: YouyunDiffusersHandle, lines: int = 80) -> str:
        if handle.ssh is None:
            return ""
        code, output = await asyncio.to_thread(handle.ssh.run, f"tail -n {int(lines)} {shlex.quote(handle.log_path)}", 20, False)
        return output if code == 0 else ""

    async def _download_results(self, handle: YouyunDiffusersHandle, results: list[Mapping[str, Any]], task: Mapping[str, Any]) -> list[dict[str, Any]]:
        root = Path(str(task.get("resultRoot") or (Path.cwd() / ".image-generation-results" / str(task.get("id") or "task")))).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        downloaded: list[dict[str, Any]] = []
        for index, item in enumerate(results):
            filename = Path(str(item.get("fileName") or f"{index}.png")).name
            if filename != str(item.get("fileName") or filename) or not filename.lower().endswith(".png"):
                continue
            local = root / filename
            await asyncio.to_thread(handle.ssh.get_file, posixpath.join(handle.results_dir, filename), local)  # type: ignore[union-attr]
            downloaded.append({**dict(item), "id": str(item.get("id") or index), "fileName": filename, "path": str(local), "contentType": "image/png"})
        return downloaded

    async def stop(self, handle: YouyunDiffusersHandle) -> dict[str, Any]:
        if handle.ssh is not None and handle.pid_path:
            await asyncio.to_thread(handle.ssh.run, f"if [ -f {shlex.quote(handle.pid_path)} ]; then kill -TERM $(cat {shlex.quote(handle.pid_path)}) 2>/dev/null || true; fi", 30, False)
            handle.ssh.close()
            handle.ssh = None
        return await asyncio.to_thread(
            self.manager.cleanup_after_task,
            handle.instance_id,
            stop_first=False,
            region=getattr(handle, "region", None),
            zone=getattr(handle, "zone", None),
        )

    async def cleanup_persisted(self, value: Mapping[str, Any]) -> dict[str, Any]:
        instance_id = str(value.get("instanceId") or value.get("instance_id") or "").strip()
        if not instance_id:
            return {"terminated": False, "error": "持久化优云句柄缺少实例 ID。"}
        return await asyncio.to_thread(
            self.manager.cleanup_after_task,
            instance_id,
            stop_first=False,
            region=str(value.get("region") or "") or None,
            zone=str(value.get("zone") or "") or None,
        )

    async def stop_persisted(self, value: Mapping[str, Any]) -> dict[str, Any]:
        """Release a service handle restored after a process restart."""
        return await self.cleanup_persisted(value)


__all__ = ["YouyunDiffusersHandle", "YouyunDiffusersProvider"]
