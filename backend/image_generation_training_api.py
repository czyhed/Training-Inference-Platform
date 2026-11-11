from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from backend.image_generation_training import materialize_image_generation_training_bundle, validate_image_generation_training_dataset


class ImageGenerationTrainingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    datasetId: str = Field(min_length=1, max_length=120)
    modelId: str = Field(min_length=1, max_length=200)
    name: str = Field(default="SDXL LoRA 微调", min_length=1, max_length=100)
    outputName: str = Field(default="sdxl-lora-adapter", min_length=1, max_length=120)
    resolution: int = Field(default=512, ge=256, le=2048)
    epochs: float = Field(default=1, gt=0, le=100)
    maxTrainSteps: int = Field(default=100, ge=1, le=100000)
    learningRate: float = Field(default=1e-4, gt=0, le=1)
    loraRank: int = Field(default=4, ge=1, le=256)
    batchSize: int = Field(default=1, ge=1, le=16)
    gradientAccumulation: int = Field(default=1, ge=1, le=256)
    validationSplit: int = Field(default=0, ge=0, le=50)
    seed: int = Field(default=42, ge=0, le=2_147_483_647)
    provider: str = Field(default="youyun", pattern="^(local|youyun)$")
    resource: dict[str, Any] = Field(default_factory=dict)
    preflight: dict[str, Any] | None = None


def _ttl() -> int:
    try:
        value = int(os.environ.get("XUNTUI_IMAGE_PREFLIGHT_TTL_SECONDS", "600"))
    except ValueError:
        value = 600
    return max(30, min(86_400, value))


def _training_memory_warning(payload: ImageGenerationTrainingPayload, resource: Mapping[str, Any]) -> str | None:
    gpu_type = str(resource.get("gpuType") or "").upper()
    if gpu_type in {"4090", "A10", "A5000"} and payload.resolution >= 1024 and payload.loraRank >= 16 and payload.batchSize >= 1:
        return "当前配置很可能超出 24GB 显存：SDXL 1024px + LoRA Rank 16 在单卡训练中容易 CUDA OOM；建议使用 512px、Rank 4-8，或更大显存 GPU。"
    return None


def _remote_script_download_allowed() -> bool:
    return os.environ.get("XUNTUI_ALLOW_REMOTE_SCRIPT_DOWNLOAD", "0").strip().lower() in {"1", "true", "yes", "on"}


def create_image_generation_training_router(
    store: Any,
    training_runtime: Any,
    *,
    runtime_root: Path,
    youyun_resource_resolver: Callable[[Mapping[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    youyun_image_validator: Callable[[str, Mapping[str, Any]], Awaitable[str | None]] | None = None,
) -> APIRouter:
    router = APIRouter(tags=["image-generation-training"])
    preview_root = (runtime_root / "image-generation-training-previews").resolve()
    preview_root.mkdir(parents=True, exist_ok=True)
    training_runtime.preview_root = preview_root

    def datasets() -> list[dict[str, Any]]:
        return [
            item for item in (store.list_datasets() if hasattr(store, "list_datasets") else [])
            if item.get("structure") == "IMAGE_TEXT"
            and item.get("state") == "published"
            and item.get("type") == "文生图"
        ]

    def models() -> list[dict[str, Any]]:
        assets = store.list_model_assets(include_archived=False) if hasattr(store, "list_model_assets") else []
        verified = {item.strip().casefold() for item in os.environ.get("XUNTUI_IMAGE_GENERATION_VERIFIED_REPOS", "stabilityai/stable-diffusion-xl-base-1.0").split(",") if item.strip()}
        result = []
        for item in assets:
            repo = str(item.get("repoId") or item.get("repo") or "")
            if item.get("kind") != "base" or "sdxl" not in repo.casefold() and "stable-diffusion-xl" not in repo.casefold():
                continue
            ready = repo.casefold() in verified
            local_ready = str(item.get("status")) == "available"
            reason = None if ready else ("SDXL Repo ID 尚未通过服务端文生图校验。" if repo.casefold() not in verified else "SDXL 基座模型尚未通过服务端就绪校验。")
            result.append({"id": item.get("id"), "label": item.get("name") or repo, "repoId": repo, "modelType": "SDXL", "readyForYouyun": ready, "supportedTrainingModes": ["lora"] if ready else [], "reason": reason})
        return result

    def resolve(payload: ImageGenerationTrainingPayload) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        dataset = next((item for item in datasets() if str(item.get("id")) == payload.datasetId), None)
        if dataset is None:
            raise HTTPException(status_code=409, detail="请选择已发布的文生图 IMAGE_TEXT 数据集。")
        model = next((item for item in models() if str(item.get("id")) == payload.modelId), None)
        if model is None or not model.get("readyForYouyun"):
            raise HTTPException(status_code=409, detail="请选择已验证且可用于优云的 SDXL 基座模型。")
        resource = dict(payload.resource or {})
        return dataset, model, resource

    async def preflight_value(payload: ImageGenerationTrainingPayload) -> dict[str, Any]:
        dataset, model, resource = resolve(payload)
        paths = dataset.get("paths") or {}
        manifest_path = Path(str(paths.get("manifestPath") or ""))
        media_root = Path(str(paths.get("mediaRoot") or ""))
        try:
            samples = await asyncio.to_thread(validate_image_generation_training_dataset, manifest_path, media_root)
        except ValueError as exc:
            return {"ready": False, "reasons": [str(exc)], "checks": {"dataset": {"status": "blocked", "message": str(exc)}}}
        requires_install = bool(resource.get("requiresDiffusersInstall"))
        script_fallback_allowed = _remote_script_download_allowed()
        image_status = "ready"
        image_message = "将优先使用优云预置 Diffusers / SDXL LoRA 镜像。"
        if requires_install:
            image_status = "warning" if script_fallback_allowed else "blocked"
            image_message = (
                "优云未发现预置 Diffusers 镜像；将安装依赖，训练脚本缺失时允许远端下载兜底。"
                if script_fallback_allowed
                else "所选优云镜像需要安装 Diffusers，但未证明包含 SDXL LoRA 训练脚本；请更换预置训练镜像，或显式开启 XUNTUI_ALLOW_REMOTE_SCRIPT_DOWNLOAD。"
            )
        checks: dict[str, Any] = {
            "dataset": {"status": "ready", "message": f"已发布 IMAGE_TEXT 数据集，共 {samples} 条样本。"},
            "model": {"status": "ready", "message": "SDXL 基座模型已通过服务端校验。", "repoId": model["repoId"]},
            "training": {"status": "ready", "message": "LoRA 训练参数校验通过。"},
            "image": {
                "status": image_status,
                "message": image_message,
                "imageId": resource.get("imageId"),
            },
        }
        memory_warning = _training_memory_warning(payload, resource)
        if memory_warning:
            checks["training"] = {"status": "blocked", "message": memory_warning}
        if payload.provider == "youyun":
            missing = sorted({"region", "zone", "gpuType", "gpuCount", "cpuCores", "memoryGb", "imageId"}.difference(resource))
            if missing:
                checks["capacity"] = {"status": "blocked", "message": f"优云资源快照缺少字段：{', '.join(missing)}"}
                checks["price"] = {"status": "blocked", "message": "资源或价格校验未完成。"}
            elif youyun_image_validator is not None:
                image_error = await youyun_image_validator(str(resource.get("imageId") or ""), resource)
                if image_error:
                    checks["image"] = {
                        "status": "blocked",
                        "message": image_error,
                        "imageId": resource.get("imageId"),
                    }
        canonical = resource
        if not any(value.get("status") == "blocked" for value in checks.values()):
            if youyun_resource_resolver is not None and resource:
                try:
                    canonical = await youyun_resource_resolver(resource)
                    if resource.get("requiresDiffusersInstall"):
                        canonical["requiresDiffusersInstall"] = True
                    checks["capacity"] = {"status": "ready", "message": "优云库存校验通过。"}
                    checks["price"] = {"status": "ready", "message": "优云价格校验通过。", "price": canonical.get("price")}
                except Exception as exc:
                    checks["capacity"] = {"status": "blocked", "message": str(exc)}
                    checks["price"] = {"status": "blocked", "message": "资源或价格校验未完成。"}
            else:
                checks["capacity"] = {"status": "warning", "message": "未配置优云实时库存校验。"}
                checks["price"] = {"status": "warning", "message": "未配置优云实时价格校验。"}
        fingerprint = json.dumps({"datasetId": payload.datasetId, "modelId": payload.modelId, **payload.model_dump(exclude={"preflight"})}, sort_keys=True, ensure_ascii=False)
        blocked = any(value.get("status") == "blocked" for value in checks.values())
        return {"ready": not blocked, "checks": checks, "reasons": [value["message"] for value in checks.values() if value.get("status") == "blocked"], "fingerprint": fingerprint, "checkedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "expiresAtEpoch": time.time() + _ttl(), "sampleCount": samples, "youyunResource": canonical}

    @router.get("/api/image-generation/training/models")
    async def list_training_models() -> list[dict[str, Any]]:
        return models()

    @router.get("/api/image-generation/training/datasets")
    async def list_training_datasets() -> list[dict[str, Any]]:
        return datasets()

    @router.post("/api/image-generation/training/preflight")
    async def training_preflight(payload: ImageGenerationTrainingPayload) -> dict[str, Any]:
        return await preflight_value(payload)

    @router.post("/api/image-generation/training/materialize-preview")
    async def materialize_preview(payload: ImageGenerationTrainingPayload) -> dict[str, Any]:
        preflight = await preflight_value(payload)
        if not preflight.get("ready"):
            raise HTTPException(status_code=409, detail=preflight)
        dataset, model, _ = resolve(payload)
        paths = dataset.get("paths") or {}
        preview = preview_root / f"preview-{uuid.uuid4().hex[:12]}"
        return await asyncio.to_thread(materialize_image_generation_training_bundle, dataset_id=payload.datasetId, media_root=Path(str(paths["mediaRoot"])), manifest_path=Path(str(paths["manifestPath"])), destination=preview, model_repo_id=str(model["repoId"]), resolution=payload.resolution, epochs=payload.epochs, max_train_steps=payload.maxTrainSteps, learning_rate=payload.learningRate, lora_rank=payload.loraRank, batch_size=payload.batchSize, gradient_accumulation=payload.gradientAccumulation, validation_split=payload.validationSplit, seed=payload.seed)

    @router.post("/api/image-generation/training/tasks", status_code=status.HTTP_201_CREATED)
    async def create_training_task(payload: ImageGenerationTrainingPayload) -> dict[str, Any]:
        preflight = await preflight_value(payload)
        if not preflight.get("ready"):
            raise HTTPException(status_code=409, detail=preflight)
        dataset, model, _ = resolve(payload)
        preview = await materialize_preview(payload)
        task = {"id": f"igt-{time.strftime('%y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}", "name": payload.name, "outputName": payload.outputName, "datasetId": payload.datasetId, "datasetName": dataset.get("name"), "modelId": model["id"], "modelRepoId": model["repoId"], "modelType": "SDXL", "provider": payload.provider, "resource": preflight.get("youyunResource") or payload.resource, "previewRoot": preview["root"], "outputDir": preview["outputDir"], "manifestPath": preview["manifestPath"], "configPath": preview["configPath"], "resolution": payload.resolution, "epochs": payload.epochs, "maxTrainSteps": payload.maxTrainSteps, "learningRate": payload.learningRate, "loraRank": payload.loraRank, "batchSize": payload.batchSize, "gradientAccumulation": payload.gradientAccumulation, "validationSplit": payload.validationSplit, "seed": payload.seed, "preflight": preflight}
        return training_runtime.create_task(task)

    @router.get("/api/image-generation/training/tasks")
    async def list_training_tasks(includeArchived: bool = Query(False), state: str = Query(""), query: str = Query("")) -> list[dict[str, Any]]:
        return training_runtime.list_tasks(include_archived=includeArchived, state=state or None, query=query or None)

    @router.get("/api/image-generation/training/tasks/{task_id}")
    async def get_training_task(task_id: str) -> dict[str, Any]:
        try:
            task = training_runtime.hydrate_observability(task_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="文生图微调任务不存在。")
        return task

    @router.get("/api/image-generation/training/tasks/{task_id}/logs")
    async def training_logs(task_id: str) -> dict[str, Any]:
        try:
            task = training_runtime.hydrate_observability(task_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="文生图微调任务不存在。")
        return {"taskId": task_id, "items": list(task.get("logs") or [])}

    @router.get("/api/image-generation/training/tasks/{task_id}/metrics")
    async def training_metrics(task_id: str) -> dict[str, Any]:
        try:
            task = training_runtime.hydrate_observability(task_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="文生图微调任务不存在。")
        return {"taskId": task_id, "items": list(task.get("metrics") or [])}

    async def action(task_id: str, name: str) -> dict[str, Any]:
        try:
            result = await getattr(training_runtime, name)(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return result

    async def confirm_task(task_id: str) -> dict[str, Any]:
        task = training_runtime.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="文生图微调任务不存在。")
        snapshot = task.get("preflight") if isinstance(task.get("preflight"), Mapping) else None
        stale = not snapshot or float(snapshot.get("expiresAtEpoch") or 0) <= time.time()
        if not stale:
            current = ImageGenerationTrainingPayload.model_validate({key: task.get(key) for key in ("datasetId", "modelId", "name", "outputName", "resolution", "epochs", "maxTrainSteps", "learningRate", "loraRank", "batchSize", "gradientAccumulation", "validationSplit", "seed", "provider", "resource")})
            fresh = await preflight_value(current)
            stale = fresh.get("fingerprint") != snapshot.get("fingerprint")
        else:
            current = ImageGenerationTrainingPayload.model_validate({key: task.get(key) for key in ("datasetId", "modelId", "name", "outputName", "resolution", "epochs", "maxTrainSteps", "learningRate", "loraRank", "batchSize", "gradientAccumulation", "validationSplit", "seed", "provider", "resource")})
            fresh = await preflight_value(current)
        if stale:
            if not fresh.get("ready"):
                raise HTTPException(status_code=409, detail=fresh)
            training_runtime.update(task_id, preflight=fresh)
        return await action(task_id, "confirm")

    async def stop_task(task_id: str) -> dict[str, Any]:
        return await action(task_id, "request_stop")

    async def retry_task(task_id: str) -> dict[str, Any]:
        return await action(task_id, "retry")

    async def release_youyun_instance(task_id: str) -> dict[str, Any]:
        task = training_runtime.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="文生图微调任务不存在。")
        if str(task.get("provider") or "") != "youyun":
            raise HTTPException(status_code=409, detail="任务不是优云文生图微调任务。")
        result = await training_runtime.force_cleanup_remote_task(task_id, reason="手动释放")
        if result is None:
            raise HTTPException(status_code=404, detail="文生图微调任务不存在。")
        return result

    async def archive_task(task_id: str) -> dict[str, Any]:
        return await action(task_id, "archive_task")

    async def restore_task(task_id: str) -> dict[str, Any]:
        return await action(task_id, "restore_task")

    router.add_api_route("/api/image-generation/training/tasks/{task_id}/confirm", confirm_task, methods=["POST"])
    router.add_api_route("/api/image-generation/training/tasks/{task_id}/stop", stop_task, methods=["POST"])
    router.add_api_route("/api/image-generation/training/tasks/{task_id}/retry", retry_task, methods=["POST"])
    router.add_api_route("/api/image-generation/training/tasks/{task_id}/youyun/release", release_youyun_instance, methods=["POST"])
    router.add_api_route("/api/image-generation/training/tasks/{task_id}/archive", archive_task, methods=["POST"])
    router.add_api_route("/api/image-generation/training/tasks/{task_id}/restore", restore_task, methods=["POST"])

    @router.post("/api/image-generation/training/tasks/{task_id}/clone", status_code=status.HTTP_201_CREATED)
    async def clone_training_task(task_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        task = training_runtime.clone_task(task_id, payload or {})
        if task is None:
            raise HTTPException(status_code=404, detail="文生图微调任务不存在。")
        return task

    @router.get("/api/image-generation/training/tasks/{task_id}/diagnostics")
    async def training_diagnostics(task_id: str) -> dict[str, Any]:
        try:
            return training_runtime.diagnostics(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.delete("/api/image-generation/training/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_training_task(task_id: str) -> None:
        related_assets = [
            item
            for item in (store.list_model_assets(include_archived=True, kind="trained") if hasattr(store, "list_model_assets") else [])
            if str(item.get("taskId") or "") == task_id
        ]
        if any(not item.get("archived") for item in related_assets):
            raise HTTPException(status_code=409, detail="该任务的 Adapter 仍在模型管理中使用，请先归档对应模型。")
        service_store = getattr(training_runtime, "service_store", None)
        if service_store is not None and hasattr(service_store, "list"):
            references = []
            for service in service_store.list(include_archived=True):
                config = service.get("config") or {}
                lineage = config.get("adapterLineage") or {}
                if str(lineage.get("taskId") or service.get("taskId") or "") == task_id:
                    references.append(str(service.get("name") or service.get("id") or ""))
            if references:
                raise HTTPException(status_code=409, detail=f"该任务的 Adapter 正被推理服务使用：{', '.join(references[:3])}。请先删除相关服务。")
        try:
            deleted = training_runtime.delete_task(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="文生图微调任务不存在。")
        delete_model_asset = getattr(store, "delete_model_asset", None)
        if callable(delete_model_asset):
            for asset in related_assets:
                delete_model_asset(str(asset.get("id") or ""))

    return router


__all__ = ["ImageGenerationTrainingPayload", "create_image_generation_training_router"]
