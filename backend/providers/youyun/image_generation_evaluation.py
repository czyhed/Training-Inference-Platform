from __future__ import annotations

"""One-shot Youyun executor for service-scoped image-generation evaluation.

The provider creates one temporary instance for a complete evaluation batch,
uploads a generated JSON configuration and reference images, then polls a
fixed worker status file.  User supplied values are data in JSON only; no
user supplied shell command is ever executed.
"""

import asyncio
import json
import os
import posixpath
import re
import shlex
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from backend.image_generation_evaluation_executor import _summary
from backend.providers.youyun.diffusers import YouyunDiffusersProvider

from .instance_manager import YouyunInstanceManager
from .ssh import YouyunSSHClient


class YouyunImageGenerationEvaluationExecutor:
    def __init__(
        self,
        manager: YouyunInstanceManager,
        *,
        ssh_factory: Callable[[], YouyunSSHClient] = YouyunSSHClient,
        remote_root: str = "/root/cppt/image-generation-evaluations",
        remote_python: str | None = None,
        poll_interval: float = 2.0,
        timeout: float = 86_400.0,
    ) -> None:
        self.manager = manager
        self.ssh_factory = ssh_factory
        self.remote_root = remote_root.rstrip("/")
        self.remote_python = (remote_python or os.environ.get("XUNTUI_DIFFUSERS_PYTHON", "python3")).strip() or "python3"
        self.poll_interval = poll_interval
        self.timeout = timeout

    @staticmethod
    def _safe_detail(value: Any) -> str:
        text = str(value or "").strip().splitlines()[0][:600] if str(value or "").strip() else ""
        lowered = text.casefold()
        if any(marker in lowered for marker in ("password=", "token=", "secret=", "authorization:", "traceback", "nohup ", "ssh ", "command=")):
            return "优云文生图评测执行失败。"
        return text

    @classmethod
    def _failure(cls, code: str, detail: Any = None) -> dict[str, str]:
        messages = {
            "instance_start_failed": "优云实例启动失败。",
            "runtime_unavailable": "优云实例缺少文生图评测运行环境。",
            "model_unavailable": "优云基座模型不可用。",
            "adapter_unavailable": "训练完成的 Adapter 不可用。",
            "evaluation_failed": "优云文生图评测执行失败。",
            "result_download_failed": "优云评测结果下载失败。",
            "cleanup_failed": "优云实例清理失败。",
        }
        actions = {
            "instance_start_failed": "检查优云库存、镜像和资源配置后重试。",
            "runtime_unavailable": "改用预置 Diffusers/Transformers 镜像，或显式开启安装兜底。",
            "model_unavailable": "确认优云镜像已挂载基座模型，或开启模型下载兜底。",
            "adapter_unavailable": "确认 Adapter 目录和 LoRA 权重文件完整后重试。",
            "evaluation_failed": "检查评测日志、生成参数和 CLIP 模型后重试。",
            "result_download_failed": "确认远端结果文件完整后重试。",
            "cleanup_failed": "重试清理优云实例，避免继续产生费用。",
        }
        normalized = code if code in messages else "evaluation_failed"
        return {
            "failureCode": normalized,
            "failureMessage": messages[normalized],
            "suggestedAction": actions[normalized],
            "error": cls._safe_detail(detail) or messages[normalized],
        }

    @staticmethod
    def _safe_sample_id(value: Any, index: int) -> str:
        raw = str(value or f"sample-{index + 1}")
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._")
        return (safe or f"sample-{index + 1}")[:100]

    def _connect(self, instance: Any) -> YouyunSSHClient:
        credentials = self.manager.ssh_credentials(instance)
        if not credentials.get("host") or not credentials.get("password"):
            raise RuntimeError("优云实例缺少 SSH 连接信息。")
        ssh = self.ssh_factory()
        ssh.connect(credentials["host"], int(credentials.get("port") or 22), credentials.get("user") or "root", credentials["password"])
        return ssh

    def _prepare_runtime(self, ssh: YouyunSSHClient) -> str:
        candidates = f"{shlex.quote(self.remote_python)} /usr/local/miniconda3/envs/*/bin/python /opt/conda/envs/*/bin/python /opt/conda/bin/python /root/miniconda3/envs/*/bin/python /usr/local/bin/python3 /usr/bin/python3"
        probe = (
            f"for candidate in {candidates}; do if [ -x \"$candidate\" ] && \"$candidate\" -c 'import torch, diffusers, transformers, PIL' >/dev/null 2>&1; then "
            "printf '%s\\n' \"$candidate\"; exit 0; fi; done; exit 42"
        )
        code, output = ssh.run(probe, 90, False)
        if code == 0 and output.strip():
            self.remote_python = output.strip().splitlines()[-1]
            return self.remote_python
        install = os.environ.get("XUNTUI_REMOTE_IMAGE_EVAL_INSTALL", "0").strip().casefold() in {"1", "true", "yes", "on"}
        if not install:
            raise RuntimeError("优云镜像未提供 torch、diffusers、transformers 和 Pillow 评测环境。")
        torch_probe = f"for candidate in {candidates}; do if [ -x \"$candidate\" ] && \"$candidate\" -c 'import torch' >/dev/null 2>&1; then printf '%s\\n' \"$candidate\"; exit 0; fi; done; exit 42"
        torch_code, torch_output = ssh.run(torch_probe, 90, False)
        install_python = torch_output.strip().splitlines()[-1] if torch_code == 0 and torch_output.strip() else self.remote_python
        package = os.environ.get("XUNTUI_REMOTE_IMAGE_EVAL_PACKAGE", "transformers pillow").strip()
        command = f"{shlex.quote(install_python)} -m pip install --disable-pip-version-check {package} && {shlex.quote(install_python)} -c 'import torch, diffusers, transformers, PIL'"
        code, output = ssh.run(command, 3600, False)
        if code != 0:
            raise RuntimeError(f"远端文生图评测依赖安装失败：{output[-1200:]}")
        self.remote_python = install_python
        return install_python

    _probe_runtime = _prepare_runtime

    @staticmethod
    def _worker_script() -> str:
        return '''from __future__ import annotations
import argparse, json, pathlib, time

def write_status(path, payload):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)

def cosine(left, right):
    import torch
    left = left / left.norm(dim=-1, keepdim=True)
    right = right / right.norm(dim=-1, keepdim=True)
    return float((left * right).sum(dim=-1).item())

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
    result_file = results_dir / "results.jsonl"
    try:
        import torch
        from diffusers import DiffusionPipeline, DPMSolverMultistepScheduler, EulerDiscreteScheduler
        from PIL import Image
        from transformers import CLIPModel, CLIPProcessor
        device = "cuda" if torch.cuda.is_available() else "cpu"
        write_status(status_path, {"state": "running", "progress": 1, "results": []})
        pipe = DiffusionPipeline.from_pretrained(config["modelPath"], torch_dtype=torch.float16 if device == "cuda" else torch.float32, variant=config.get("variant"))
        pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config) if config.get("sampler") == "dpmpp_2m" else EulerDiscreteScheduler.from_config(pipe.scheduler.config)
        pipe.to(device)
        if config.get("adapterPath") and hasattr(pipe, "load_lora_weights"):
            pipe.load_lora_weights(config["adapterPath"])
        clip = CLIPModel.from_pretrained(config.get("clipModel") or "openai/clip-vit-base-patch32").to(device)
        processor = CLIPProcessor.from_pretrained(config.get("clipModel") or "openai/clip-vit-base-patch32")
        samples = list(config.get("samples") or [])
        results = []
        with result_file.open("w", encoding="utf-8") as stream:
            for index, sample in enumerate(samples):
                started = time.perf_counter()
                sample_id = str(sample.get("id") or f"sample-{index + 1}")
                try:
                    seed = int(config.get("seed", -1))
                    generator = torch.Generator(device=device).manual_seed(seed + index) if seed >= 0 else None
                    image = pipe(prompt=str(sample.get("caption") or ""), width=int(config["width"]), height=int(config["height"]), num_inference_steps=int(config["steps"]), guidance_scale=float(config["cfg"]), generator=generator).images[0]
                    filename = sample_id + ".png"
                    image.save(results_dir / filename)
                    inputs = processor(text=[str(sample.get("caption") or "")], images=image, return_tensors="pt", padding=True).to(device)
                    text_score = cosine(clip.get_text_features(input_ids=inputs.input_ids, attention_mask=inputs.attention_mask), clip.get_image_features(pixel_values=inputs.pixel_values))
                    reference = Image.open(sample["referencePath"]).convert("RGB")
                    ref_inputs = processor(images=[reference, image], return_tensors="pt").to(device)
                    ref_features = clip.get_image_features(pixel_values=ref_inputs.pixel_values)
                    reference_score = cosine(ref_features[0:1], ref_features[1:2])
                    row = {"sampleId": sample_id, "prompt": str(sample.get("caption") or ""), "state": "completed", "fileName": filename, "latencyMs": round((time.perf_counter() - started) * 1000, 2), "clipTextImage": text_score, "clipReferenceImage": reference_score}
                except Exception:
                    row = {"sampleId": sample_id, "prompt": str(sample.get("caption") or ""), "state": "failed", "error": "单样本生成失败，请查看远端评测日志。"}
                results.append(row)
                stream.write(json.dumps(row, ensure_ascii=False) + "\\n")
                stream.flush()
                write_status(status_path, {"state": "running" if index + 1 < len(samples) else "completed", "progress": int((index + 1) * 100 / max(1, len(samples))), "results": results})
    except Exception:
        write_status(status_path, {"state": "failed", "progress": 0, "error": "评测 worker 初始化失败，请查看远端评测日志。"})
        raise

if __name__ == "__main__":
    main()
'''

    async def run(self, task: Mapping[str, Any], *, on_event: Callable[[str, str, Mapping[str, Any] | None], None] | None = None, cancel_event: asyncio.Event | None = None) -> dict[str, Any]:
        task_id = str(task.get("id") or "evaluation")
        resource = dict(task.get("resource") or {})
        instance = None
        ssh: YouyunSSHClient | None = None
        results: list[dict[str, Any]] = []
        failure_code = "evaluation_failed"
        stopped = False
        try:
            if on_event:
                on_event("preparing", "正在创建优云文生图评测实例。", {"progress": 5})
            instance = await asyncio.to_thread(self.manager.create_one_shot, task_id, gpu=resource.get("gpuCount"), cpu=resource.get("cpuCores"), memory_gb=resource.get("memoryGb"), gpu_type=resource.get("gpuType"), image_id=resource.get("imageId"), name=f"cppt-img-eval-{task_id}", idempotency_key=f"{task_id}:image-evaluation")
            if on_event:
                on_event(
                    "preparing",
                    f"优云评测实例已就绪：{instance.uhost_id}。",
                    {"progress": 6, "remoteHandle": {"instanceId": str(instance.uhost_id), "state": "running"}},
                )
            ssh = await asyncio.to_thread(self._connect, instance)
            await asyncio.to_thread(self._prepare_runtime, ssh)
            failure_code = "model_unavailable"
            model_path = await asyncio.to_thread(YouyunDiffusersProvider._resolve_remote_model, YouyunDiffusersProvider(self.manager, ssh_factory=self.ssh_factory, remote_python=self.remote_python), ssh, str(task.get("modelRepoId") or ""))
            workdir = posixpath.join(self.remote_root, task_id)
            refs_dir = posixpath.join(workdir, "references")
            results_dir = posixpath.join(workdir, "results")
            await asyncio.to_thread(ssh.mkdirs, refs_dir)
            await asyncio.to_thread(ssh.mkdirs, results_dir)
            remote_adapter = ""
            adapter = Path(str(task.get("adapterPath") or "")).expanduser()
            if str(task.get("adapterPath") or "").strip():
                failure_code = "adapter_unavailable"
                if not adapter.is_dir():
                    raise RuntimeError("Adapter 路径不存在或不是目录。")
                remote_adapter = posixpath.join(workdir, "adapter")
                await asyncio.to_thread(ssh.mkdirs, remote_adapter)
                uploaded = 0
                for item in sorted(adapter.iterdir()):
                    if item.is_file() and item.name in YouyunDiffusersProvider._ADAPTER_FILES:
                        await asyncio.to_thread(ssh.put_file, item, posixpath.join(remote_adapter, item.name))
                        uploaded += 1
                if uploaded == 0:
                    raise RuntimeError("Adapter 目录不包含可加载的 LoRA 权重。")
            samples = []
            for index, sample in enumerate(task.get("samples") or []):
                if not isinstance(sample, Mapping):
                    continue
                sample_id = self._safe_sample_id(sample.get("id"), index)
                reference = Path(str(sample.get("referencePath") or "")).expanduser().resolve()
                if not reference.is_file():
                    raise RuntimeError("评测参考图片不存在。")
                remote_reference = posixpath.join(refs_dir, sample_id + reference.suffix.lower())
                await asyncio.to_thread(ssh.put_file, reference, remote_reference)
                samples.append({"id": sample_id, "caption": str(sample.get("caption") or ""), "referencePath": remote_reference})
            config = {"modelPath": model_path, "variant": "fp16" if "sdxl" in str(task.get("modelRepoId") or "").casefold() or "stable-diffusion-xl" in str(task.get("modelRepoId") or "").casefold() else None, "adapterPath": remote_adapter, "width": int(task.get("width") or 1024), "height": int(task.get("height") or 1024), "steps": int(task.get("steps") or 30), "cfg": float(task.get("cfg") or 7), "seed": int(task.get("seed") if task.get("seed") is not None else -1), "sampler": str(task.get("sampler") or "euler"), "clipModel": os.environ.get("XUNTUI_IMAGE_EVAL_CLIP_MODEL", "openai/clip-vit-base-patch32"), "samples": samples}
            config_path = posixpath.join(workdir, "task.json")
            status_path = posixpath.join(workdir, "status.json")
            worker_path = posixpath.join(workdir, "evaluation_worker.py")
            log_path = posixpath.join(workdir, "worker.log")
            pid_path = posixpath.join(workdir, "worker.pid")
            await asyncio.to_thread(ssh.put_text, json.dumps(config, ensure_ascii=False), config_path)
            await asyncio.to_thread(ssh.put_text, self._worker_script(), worker_path)
            launch = f"nohup {shlex.quote(self.remote_python)} {shlex.quote(worker_path)} --config-path {shlex.quote(config_path)} --status-path {shlex.quote(status_path)} --results-dir {shlex.quote(results_dir)} > {shlex.quote(log_path)} 2>&1 & echo $! > {shlex.quote(pid_path)}"
            await asyncio.to_thread(ssh.run, launch, 60, True)
            failure_code = "evaluation_failed"
            if on_event:
                on_event("running", "优云评测 worker 已启动。", {"progress": 5})
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                code, output = await asyncio.to_thread(ssh.run, f"cat {shlex.quote(status_path)}", 20, False)
                status: dict[str, Any] = {}
                if code == 0:
                    try:
                        value = json.loads(output.strip().splitlines()[-1])
                        status = value if isinstance(value, dict) else {}
                    except (ValueError, IndexError, json.JSONDecodeError):
                        status = {}
                results = [dict(item) for item in status.get("results") or [] if isinstance(item, Mapping)]
                if on_event and status.get("progress") is not None:
                    on_event("running", f"优云文生图评测中 · {int(status['progress'])}%", {"progress": int(status["progress"]), "results": results})
                if status.get("state") == "completed":
                    break
                if status.get("state") == "failed":
                    raise RuntimeError(str(status.get("error") or "远端评测 worker 失败"))
                if cancel_event and cancel_event.is_set():
                    stopped = True
                    await asyncio.to_thread(ssh.run, f"if [ -f {shlex.quote(pid_path)} ]; then kill -TERM $(cat {shlex.quote(pid_path)}) 2>/dev/null || true; fi", 30, False)
                    break
                await asyncio.sleep(self.poll_interval)
            else:
                raise RuntimeError("优云文生图评测任务超时，未收到完成状态。")
            if not stopped:
                local_root = Path(str(task.get("resultRoot") or (Path.cwd() / ".image-generation-results" / task_id))).expanduser().resolve()
                generated = local_root / "generated"
                generated.mkdir(parents=True, exist_ok=True)
                for item in results:
                    if item.get("state") != "completed":
                        continue
                    filename = Path(str(item.get("fileName") or "")).name
                    if not filename.endswith(".png"):
                        continue
                    await asyncio.to_thread(ssh.get_file, posixpath.join(results_dir, filename), generated / filename)
                    item["path"] = str(generated / filename)
                    item["fileName"] = filename
                results = [dict(item) for item in results]
        except asyncio.CancelledError:
            stopped = True
        except Exception as exc:
            failure = self._failure(failure_code, exc)
            if ssh is not None:
                try:
                    ssh.close()
                except Exception:
                    pass
            cleanup = await asyncio.to_thread(self.manager.cleanup_after_task, str(getattr(instance, "uhost_id", "")), stop_first=False) if instance is not None else {"terminated": True, "error": None}
            cleanup_state = "released" if cleanup.get("terminated") else "failed"
            return {"status": "failed", "results": results, "summary": _summary(results, len(task.get("samples") or [])), "cleanupState": cleanup_state, "cleanupError": self._safe_detail(cleanup.get("error")) or None, **failure}
        finally:
            if ssh is not None:
                try:
                    ssh.close()
                except Exception:
                    pass
        cleanup = await asyncio.to_thread(self.manager.cleanup_after_task, str(getattr(instance, "uhost_id", "")), stop_first=False) if instance is not None else {"terminated": True, "error": None}
        cleanup_state = "released" if cleanup.get("terminated") else "failed"
        cleanup_error = self._safe_detail(cleanup.get("error")) or (None if cleanup_state == "released" else "优云实例清理失败。")
        summary = _summary(results, len(task.get("samples") or []))
        if stopped:
            return {"status": "stopped", "results": results, "summary": summary, "cleanupState": cleanup_state, "cleanupError": cleanup_error}
        if cleanup_state == "failed":
            return {"status": "completed", "results": results, "summary": summary, "cleanupState": cleanup_state, "cleanupError": cleanup_error, **self._failure("cleanup_failed", cleanup_error)}
        return {"status": "completed", "results": results, "summary": summary, "cleanupState": cleanup_state, "cleanupError": None}


__all__ = ["YouyunImageGenerationEvaluationExecutor"]
