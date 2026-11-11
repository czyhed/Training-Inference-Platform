"""Speech deployment adapter. Browser requests remain inside platform auth."""
from datetime import datetime, timezone
import os
import re
import uuid
from urllib.parse import urlencode
from typing import Any

import httpx
from fastapi import APIRouter, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse, Response
from backend.speech_runtime import SpeechError, validate_request
from backend.speech_assets import MAX_UPLOAD


def envelope(data: Any = None, *, code: str | None = None, message: str | None = None) -> dict[str, Any]:
    request_id = 'req-' + uuid.uuid4().hex
    return {'requestId':request_id,'traceId':request_id,'data':data,'error':{'code':code,'message':message,'retryable':code=='SPEECH_WORKER_UNAVAILABLE'} if code else None,'meta':{'timestamp':datetime.now(timezone.utc).isoformat(),'apiVersion':'v1'}}


class SpeechClient:
    def __init__(self, url: str | None = None, key: str | None = None):
        self.url = (url if url is not None else os.environ.get('XUNTUI_SPEECH_WORKER_URL','')).rstrip('/')
        self.key = key if key is not None else os.environ.get('XUNTUI_SPEECH_WORKER_KEY','')

    async def request(self, method: str, path: str, payload: dict | bytes | None = None, *, audio: bool = False) -> Any:
        if not self.url or not self.key: raise SpeechError('SPEECH_NOT_CONFIGURED','CPU 语音服务尚未部署',503)
        try:
            async with httpx.AsyncClient(timeout=45 if isinstance(payload,bytes) else 20, follow_redirects=False, trust_env=False) as client:
                kwargs = {'content':payload} if isinstance(payload,bytes) else {'json':payload}
                response = await client.request(method,self.url+path,**kwargs,headers={'X-Worker-Key':self.key})
        except httpx.HTTPError:
            raise SpeechError('SPEECH_WORKER_UNAVAILABLE','语音服务暂时无法连接，请稍后刷新',503) from None
        if response.status_code >= 400:
            try: error=response.json()
            except ValueError: error={}
            code=error.get('code','SPEECH_WORKER_ERROR')
            # Worker is a trusted private boundary but raw upstream diagnostics never escape.
            messages={'SERVICE_BUSY':'CPU 正忙，请稍后重试','INFERENCE_NOT_READY':'模型尚未就绪，请先启动','MODEL_NOT_VERIFIED':'模型文件校验尚未完成','JOB_NOT_FOUND':'语音任务不存在','AUDIO_NOT_READY':'音频尚未生成','REQUEST_CONFLICT':'请求标识已用于不同的参数','INVALID_ARGUMENT':'请求参数不正确','INVALID_AUDIO':'无法读取音频，请上传 0.5–30 秒的有效音频','UPLOAD_TOO_LARGE':'音频不能超过 10 MB','ASSET_LIMIT':'素材数量已达 100，请先删除不用的素材','ASSET_REFERENCED':'素材被历史任务引用，请先删除相关任务','AUDIO_NOT_FOUND':'音频素材不存在','QUEUE_FULL':'等待队列已满（最多 5 个任务）'}
            raise SpeechError(code,messages.get(code,'语音操作失败，请刷新服务状态'),response.status_code if response.status_code in (400,404,409,413,422,503) else 502)
        if audio:
            if not response.content.startswith(b'RIFF'):raise SpeechError('INVALID_AUDIO','返回的音频格式不正确',502)
            return response.content
        try: return response.json()
        except ValueError: raise SpeechError('SPEECH_WORKER_ERROR','语音服务响应格式不正确',502) from None


class SpeechRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                return await original(request)
            except RequestValidationError:
                return JSONResponse(envelope(code='INVALID_ARGUMENT', message='请求格式错误，请检查文本和分页参数'), status_code=422)
        return handler


def create_speech_router(client: SpeechClient | None = None) -> APIRouter:
    adapter = client or SpeechClient()
    router = APIRouter(prefix='/api/speech',tags=['CPU speech'],route_class=SpeechRoute)

    async def dispatch(method: str, path: str, payload: dict | bytes | None = None, *, audio: bool = False) -> Response:
        try:
            result = await adapter.request(method,path,payload,audio=audio)
            if audio:return Response(result,media_type='audio/wav',headers={'Cache-Control':'private, no-store','Content-Disposition':'inline; filename="voxcpm2.wav"'})
            return JSONResponse(envelope(result),status_code=202 if method=='POST' and path in ('/jobs','/batches') else 201 if method=='POST' and path.startswith('/assets?') else 200)
        except SpeechError as error:
            return JSONResponse(envelope(code=error.code,message=str(error)),status_code=error.status)

    @router.get('/service')
    async def service() -> Response:return await dispatch('GET','/service')

    @router.post('/service/{action}')
    async def lifecycle(action: str) -> Response:
        if action not in ('start','stop'):return JSONResponse(envelope(code='INVALID_ARGUMENT',message='不支持的操作'),status_code=400)
        return await dispatch('POST','/service/'+action)

    @router.post('/jobs')
    async def submit(payload: dict[str,Any]) -> Response:
        try: clean=validate_request(payload)
        except SpeechError as error:return JSONResponse(envelope(code=error.code,message=str(error)),status_code=error.status)
        return await dispatch('POST','/jobs',clean)

    @router.get('/jobs')
    async def jobs(page: int=Query(1,ge=1),pageSize: int=Query(20,ge=1,le=50),query: str=Query('',max_length=300),state: str=Query('',pattern='^(|queued|running|completed|failed|cancelled)$')) -> Response:
        return await dispatch('GET','/jobs?'+urlencode({'page':page,'pageSize':pageSize,'query':query,'state':state}))

    @router.post('/batches')
    async def batch(payload: dict[str,Any]) -> Response:
        try:
            if set(payload)-{'items'} or not isinstance(payload.get('items'),list) or not 1 <= len(payload['items']) <= 5:
                raise SpeechError('INVALID_ARGUMENT','items 必须包含 1–5 个任务')
            clean = {'items':[validate_request(item) for item in payload['items']]}
        except SpeechError as error:
            return JSONResponse(envelope(code=error.code,message=str(error)),status_code=error.status)
        return await dispatch('POST','/batches',clean)

    @router.post('/jobs/{job_id}/{action}')
    async def job_action(job_id: str,action: str) -> Response:
        if not re.fullmatch(r'tts-[a-f0-9]{32}',job_id) or action not in ('cancel','retry'):
            return JSONResponse(envelope(code='INVALID_ARGUMENT',message='任务或操作不合法'),status_code=400)
        return await dispatch('POST',f'/jobs/{job_id}/{action}')

    @router.delete('/jobs/{job_id}')
    async def delete_job(job_id: str) -> Response:
        if not re.fullmatch(r'tts-[a-f0-9]{32}',job_id):
            return JSONResponse(envelope(code='JOB_NOT_FOUND',message='任务不存在'),status_code=404)
        return await dispatch('DELETE','/jobs/'+job_id)

    @router.get('/jobs/{job_id}/chunks/{index}')
    async def chunk(job_id: str,index: int) -> Response:
        if not re.fullmatch(r'tts-[a-f0-9]{32}',job_id) or not 0 <= index <= 2048:
            return JSONResponse(envelope(code='INVALID_ARGUMENT',message='音频片段不合法'),status_code=400)
        return await dispatch('GET',f'/jobs/{job_id}/chunks/{index}',audio=True)

    @router.get('/assets')
    async def assets(page: int=Query(1,ge=1),pageSize: int=Query(20,ge=1,le=100)) -> Response:
        return await dispatch('GET',f'/assets?page={page}&pageSize={pageSize}')

    @router.post('/assets')
    async def upload(request: Request,name: str=Query('参考音频',max_length=120)) -> Response:
        data=bytearray()
        async for part in request.stream():
            data.extend(part)
            if len(data)>MAX_UPLOAD:
                return JSONResponse(envelope(code='UPLOAD_TOO_LARGE',message='音频不能超过 10 MB'),status_code=413)
        return await dispatch('POST','/assets?'+urlencode({'name':name}),bytes(data))

    @router.get('/assets/{asset_id}/audio')
    async def asset_audio(asset_id: str) -> Response:
        if not re.fullmatch(r'aud-[a-f0-9]{32}',asset_id):
            return JSONResponse(envelope(code='AUDIO_NOT_FOUND',message='素材不存在'),status_code=404)
        return await dispatch('GET',f'/assets/{asset_id}/audio',audio=True)

    @router.delete('/assets/{asset_id}')
    async def delete_asset(asset_id: str) -> Response:
        if not re.fullmatch(r'aud-[a-f0-9]{32}',asset_id):
            return JSONResponse(envelope(code='AUDIO_NOT_FOUND',message='素材不存在'),status_code=404)
        return await dispatch('DELETE','/assets/'+asset_id)

    @router.get('/openapi.json')
    async def specification() -> dict:
        from backend.speech_openapi import specification
        return specification()

    @router.get('/jobs/{job_id}')
    async def job(job_id: str) -> Response:
        if not re.fullmatch(r'tts-[a-f0-9]{32}',job_id):return JSONResponse(envelope(code='JOB_NOT_FOUND',message='语音任务不存在'),status_code=404)
        return await dispatch('GET','/jobs/'+job_id)

    @router.get('/jobs/{job_id}/audio')
    async def audio(job_id: str) -> Response:
        if not re.fullmatch(r'tts-[a-f0-9]{32}',job_id):return JSONResponse(envelope(code='JOB_NOT_FOUND',message='语音任务不存在'),status_code=404)
        return await dispatch('GET','/jobs/'+job_id+'/audio',audio=True)

    return router
