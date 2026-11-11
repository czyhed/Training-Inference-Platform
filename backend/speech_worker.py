"""Private HTTP control plane for the isolated VoxCPM CPU runtime."""
from contextlib import asynccontextmanager
import hmac
import os
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool
from backend.speech_assets import MAX_UPLOAD
from fastapi.responses import FileResponse, JSONResponse
from backend.speech_runtime import SpeechError, SpeechRuntime


def create_app() -> FastAPI:
    secret = os.environ.get('XUNTUI_SPEECH_WORKER_KEY', '')
    if len(secret) < 32: raise RuntimeError('XUNTUI_SPEECH_WORKER_KEY must contain at least 32 characters')
    runtime = SpeechRuntime(Path(os.environ.get('XUNTUI_SPEECH_RESULTS','/results')), Path(os.environ.get('XUNTUI_SPEECH_MODEL_PATH','/model')), threads=int(os.environ.get('XUNTUI_SPEECH_THREADS','6')), startup_timeout=float(os.environ.get('XUNTUI_SPEECH_STARTUP_TIMEOUT','1200')), generation_timeout=float(os.environ.get('XUNTUI_SPEECH_GENERATION_TIMEOUT','1200')))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if os.environ.get('XUNTUI_SPEECH_AUTOSTART','false').lower() == 'true':
            if not (runtime.model_path/'verified-manifest.json').is_file():
                raise RuntimeError('Model must be verified before autostart')
            runtime.start()
        yield
        runtime.stop()

    app = FastAPI(title='Private VoxCPM CPU worker', lifespan=lifespan)

    def authorize(x_worker_key: str = Header(default='')) -> None:
        if not hmac.compare_digest(x_worker_key, secret): raise HTTPException(401, 'Unauthorized')

    @app.exception_handler(SpeechError)
    async def error_handler(request: Any, error: SpeechError) -> JSONResponse:
        return JSONResponse(status_code=error.status, content={'code':error.code,'message':str(error)})

    @app.get('/health')
    def health() -> dict[str, str]: return {'status':'ok'}

    @app.get('/service', dependencies=[Depends(authorize)])
    def service() -> dict[str, Any]: return runtime.status()

    @app.post('/service/start', dependencies=[Depends(authorize)])
    def start() -> dict[str, Any]:
        if not (runtime.model_path/'verified-manifest.json').is_file():
            raise SpeechError('MODEL_NOT_VERIFIED','模型文件尚未完成校验',409)
        return runtime.start()

    @app.post('/service/stop', dependencies=[Depends(authorize)])
    def stop() -> dict[str, Any]: return runtime.stop()

    @app.post('/jobs', status_code=202, dependencies=[Depends(authorize)])
    def submit(payload: dict[str, Any]) -> dict[str, Any]: return runtime.submit(payload)

    @app.get('/jobs', dependencies=[Depends(authorize)])
    def jobs(page: int = Query(1,ge=1), pageSize: int = Query(20,ge=1,le=50), query: str=Query('',max_length=300), state: str=Query('',pattern='^(|queued|running|completed|failed|cancelled)$')) -> dict[str, Any]: return runtime.list_jobs(page,pageSize,query,state)

    @app.post('/batches', status_code=202, dependencies=[Depends(authorize)])
    def batch(payload: dict[str,Any]) -> dict: return runtime.submit_batch(payload)

    @app.post('/jobs/{job_id}/{action}', dependencies=[Depends(authorize)])
    def job_action(job_id: str, action: str) -> dict:
        if action == 'cancel': return runtime.cancel(job_id)
        if action == 'retry': return runtime.retry(job_id)
        raise SpeechError('INVALID_ARGUMENT','不支持的操作')

    @app.delete('/jobs/{job_id}', dependencies=[Depends(authorize)])
    def delete_job(job_id: str) -> dict: return runtime.delete(job_id)

    @app.get('/jobs/{job_id}/chunks/{index}', dependencies=[Depends(authorize)])
    def chunk(job_id: str, index: int) -> FileResponse:
        return FileResponse(runtime.chunk(job_id,index),media_type='audio/wav')

    @app.get('/assets', dependencies=[Depends(authorize)])
    def assets(page: int=Query(1,ge=1),pageSize: int=Query(20,ge=1,le=100)) -> dict:
        items=runtime.assets.list(); start=(page-1)*pageSize
        return {'items':items[start:start+pageSize],'page':page,'pageSize':pageSize,'total':len(items),'hasNext':start+pageSize<len(items)}

    @app.post('/assets', status_code=201, dependencies=[Depends(authorize)])
    async def upload(request: Request, name: str=Query('参考音频',max_length=120)) -> dict:
        data=bytearray()
        async for part in request.stream():
            data.extend(part)
            if len(data)>MAX_UPLOAD: raise SpeechError('UPLOAD_TOO_LARGE','音频不能超过 10 MB',413)
        return await run_in_threadpool(runtime.assets.upload,bytes(data),name)

    @app.get('/assets/{asset_id}/audio', dependencies=[Depends(authorize)])
    def asset_audio(asset_id: str) -> FileResponse:
        return FileResponse(runtime.assets.path(asset_id),media_type='audio/wav')

    @app.delete('/assets/{asset_id}', dependencies=[Depends(authorize)])
    def delete_asset(asset_id: str) -> dict: return runtime.delete_asset(asset_id)

    @app.get('/jobs/{job_id}', dependencies=[Depends(authorize)])
    def job(job_id: str) -> dict[str, Any]: return runtime.job(job_id)

    @app.get('/jobs/{job_id}/audio', dependencies=[Depends(authorize)])
    def audio(job_id: str) -> FileResponse: return FileResponse(runtime.audio(job_id),media_type='audio/wav',filename='voxcpm2-'+job_id+'.wav')

    return app
