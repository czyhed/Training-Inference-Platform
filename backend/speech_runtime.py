"""One bounded CPU model process; durable results, explicit stop and timeout cleanup."""
from __future__ import annotations

import hashlib
import json
import math
import multiprocessing
import os
import random
import shutil
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


from backend.speech_contracts import DEFAULTS, SpeechError, validate_request
from backend.speech_assets import AudioAssets


def model_engine(connection: Any, model_path: str, threads: int) -> None:
    """Import heavy dependencies only in the killable, CPU-only child."""
    os.environ['CUDA_VISIBLE_DEVICES'] = ''
    os.environ['OMP_NUM_THREADS'] = str(threads)
    try:
        import numpy as np
        import soundfile as sf
        import torch
        from voxcpm import VoxCPM

        torch.set_num_threads(threads)
        torch.set_num_interop_threads(1)
        model = VoxCPM.from_pretrained(model_path, device='cpu', optimize=False, load_denoiser=False, local_files_only=True)
        # Readiness means actual synthesis, not just weights loaded.
        torch.manual_seed(42)
        np.random.seed(42)
        random.seed(42)
        warmup = model.generate(text='你好。', inference_timesteps=4, cfg_value=2.0, max_len=128, retry_badcase=False)
        if len(warmup) < 100 or not np.isfinite(warmup).all():
            raise ValueError('Invalid warmup audio')
        connection.send({'ready': True, 'sampleRate': model.tts_model.sample_rate})
        while True:
            job = connection.recv()
            started = time.monotonic()
            # VoxCPM 2.0.3 has no generate(seed=...) argument; seed its RNGs instead.
            torch.manual_seed(job['seed'])
            np.random.seed(job['seed'])
            random.seed(job['seed'])
            options = dict(text=job['text'], inference_timesteps=job['steps'], cfg_value=job['cfg'],
                min_len=job['minTokens'], max_len=job['maxTokens'], normalize=job['normalize'],
                retry_badcase=job['retryBadcase'] and not job['streaming'],
                retry_badcase_max_times=job['retryMaxTimes'], retry_badcase_ratio_threshold=job['retryRatio'],
                reference_wav_path=job.get('referencePath'), prompt_wav_path=job.get('promptPath'), prompt_text=job['promptText'])
            if job['streaming']:
                pieces = []
                seconds = 0.0
                folder = Path(job['chunksDir'])
                folder.mkdir(parents=True, exist_ok=True)
                for index, chunk in enumerate(model.generate_streaming(**options)):
                    if not np.isfinite(chunk).all():
                        raise ValueError('Invalid streaming audio')
                    pieces.append(chunk)
                    sf.write(str(folder/f'{index}.wav'), chunk, model.tts_model.sample_rate, subtype='PCM_16')
                    seconds += len(chunk)/model.tts_model.sample_rate
                    connection.send({'event':'chunk','chunkCount':index+1,'generatedSeconds':round(seconds,3),'elapsedSeconds':round(time.monotonic()-started,2)})
                wav = np.concatenate(pieces) if pieces else np.array([])
            else:
                wav = model.generate(**options)
            if len(wav) < 100 or not np.isfinite(wav).all():
                raise ValueError('Invalid synthesis output')
            sf.write(job['output'], wav, model.tts_model.sample_rate, subtype='PCM_16', format='WAV')
            connection.send({'durationSeconds': round(len(wav) / model.tts_model.sample_rate, 3), 'sampleRate': model.tts_model.sample_rate, 'generationSeconds': round(time.monotonic() - started, 2)})
    except (EOFError, BrokenPipeError):
        pass
    except Exception as error:
        import traceback
        traceback.print_exc()  # container diagnostics; never sent to the browser
        try: connection.send({'error': type(error).__name__})
        except (OSError, EOFError): pass
    finally:
        connection.close()


class SpeechRuntime:
    def __init__(self, results: Path, model_path: Path, *, engine: Callable = model_engine, threads: int = 6, startup_timeout: float = 1200, generation_timeout: float = 1200):
        self.results, self.model_path = results, model_path
        results.mkdir(parents=True, exist_ok=True)
        self.assets = AudioAssets(results/'assets')
        self.engine, self.threads = engine, threads
        self.startup_timeout, self.generation_timeout = startup_timeout, generation_timeout
        self.lock = threading.RLock()
        self.state, self.failure = 'stopped', None
        self.updated, self.generation = now(), 0
        self.process: Any = None
        self.connection: Any = None
        self.active: str | None = None
        self.jobs: dict[str, dict[str, Any]] = {}
        for path in results.glob('tts-*.json'):
            item = json.loads(path.read_text(encoding='utf-8'))
            self.jobs[item['id']] = item
            if item['state'] in ('queued', 'running'):
                item.update(state='failed', failureCode='WORKER_RESTARTED', message='服务已重启，请重新生成', updatedAt=now())
                self._save(item)

    def _save(self, item: dict[str, Any]) -> None:
        target = self.results / (item['id'] + '.json')
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(item, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, target)

    def _state(self, state: str, failure: str | None = None) -> None:
        self.state, self.failure, self.updated = state, failure, now()

    def status(self) -> dict[str, Any]:
        with self.lock:
            if self.state == 'ready' and (not self.process or not self.process.is_alive()):
                self._state('failed', 'MODEL_PROCESS_EXITED')
            manifest = self.model_path / 'verified-manifest.json'
            return {'id': 'svc-voxcpm2-cpu', 'name': 'VoxCPM2', 'repoId': 'OpenBMB/VoxCPM2', 'device': 'cpu', 'state': self.state, 'failureCode': self.failure, 'updatedAt': self.updated, 'activeJobId': self.active, 'queuedJobs':sum(j['state']=='queued' for j in self.jobs.values()), 'threads': self.threads, 'sampleRate': 48000, 'modelVerified': manifest.exists(), 'runtime': 'voxcpm', 'runtimeVersion': '2.0.3', 'maxCharacters': 300, 'capabilities':{'modes':['design','clone','continue','combined'],'streaming':True,'normalize':True,'denoise':False,'lora':False,'maxBatch':5,'maxAudioSeconds':30,'maxUploadBytes':10485760}}

    def start(self) -> dict[str, Any]:
        with self.lock:
            self.status()  # Observe a child exit before deciding start is idempotent.
            if self.state in ('loading', 'ready'):
                return self.status()
            if self.state == 'stopping':
                raise SpeechError('SERVICE_BUSY', '正在停止，请稍候', 409)
            self.generation += 1
            self._terminate(self.process, self.connection)
            if self.active:
                item = self.jobs[self.active]
                item.update(state='failed', failureCode='MODEL_PROCESS_EXITED', message='模型进程已退出，请重新生成', updatedAt=now())
                self._save(item)
                (self.results / (self.active + '.wav')).unlink(missing_ok=True)
                self.active = None
            generation = self.generation
            context = multiprocessing.get_context('spawn')
            connection, child = context.Pipe()
            process = context.Process(target=self.engine, args=(child, str(self.model_path), self.threads), daemon=True)
            self.connection, self.process = connection, process
            self._state('loading')
            try:
                process.start()
            except Exception:
                connection.close(); child.close()
                self.process, self.connection = None, None
                self._state('failed', 'MODEL_START_FAILED')
                raise SpeechError('MODEL_START_FAILED', '模型进程启动失败', 503) from None
            child.close()
            threading.Thread(target=self._loaded, args=(generation, process, connection), daemon=True).start()
            return self.status()

    def _receive(self, process: Any, connection: Any, timeout: float, on_chunk: Callable | None = None) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if connection.poll(0.1):
                data = connection.recv()
                if data.get('error'): raise RuntimeError('MODEL_FAILED')
                if data.get('event') == 'chunk':
                    if on_chunk: on_chunk(data)
                    continue
                return data
            if not process.is_alive(): raise RuntimeError('MODEL_PROCESS_EXITED')
        raise TimeoutError('MODEL_TIMEOUT')

    @staticmethod
    def _terminate(process: Any, connection: Any) -> None:
        if process and process.is_alive():
            process.terminate(); process.join(timeout=5)
            if process.is_alive(): process.kill(); process.join(timeout=5)
        elif process and process.pid is not None:
            process.join(timeout=1)
        if connection: connection.close()

    def _loaded(self, generation: int, process: Any, connection: Any) -> None:
        try:
            reply = self._receive(process, connection, self.startup_timeout)
            if not reply.get('ready'): raise RuntimeError('MODEL_NOT_READY')
            with self.lock:
                if generation == self.generation:
                    self._state('ready')
                    self._dispatch_next()
        except Exception:
            with self.lock:
                if generation != self.generation: return
                self._terminate(process, connection)
                self._state('failed', 'MODEL_START_FAILED')
                for item in self.jobs.values():
                    if item['state']=='queued':
                        item.update(state='failed',failureCode='MODEL_START_FAILED',message='模型加载失败，请重新启动后重试',updatedAt=now())
                        self._save(item)

    def stop(self) -> dict[str, Any]:
        with self.lock:
            self.generation += 1
            self._state('stopping')
            self._terminate(self.process, self.connection)
            self.process, self.connection = None, None
            if self.active:
                item = self.jobs[self.active]
                item.update(state='cancelled', failureCode='SERVICE_STOPPED', message='服务已停止', updatedAt=now())
                self._save(item)
                (self.results / (self.active + '.wav')).unlink(missing_ok=True)
                self.active = None
            for item in self.jobs.values():
                if item['state'] == 'queued':
                    item.update(state='cancelled', failureCode='SERVICE_STOPPED', message='服务已停止', updatedAt=now())
                    self._save(item)
            self._state('stopped')
            return self.status()

    def submit(self, value: dict[str, Any]) -> dict[str, Any]:
        request = validate_request(value)
        with self.lock:
            self._validate_assets(request)
            for old in self.jobs.values():
                if old.get('requestId') == request['requestId']:
                    if any(old.get(k, DEFAULTS.get(k)) != v for k, v in request.items()):
                        raise SpeechError('REQUEST_CONFLICT', '请求标识已用于不同的参数', 409)
                    return dict(old)
            if self.status()['state'] != 'ready':
                raise SpeechError('INFERENCE_NOT_READY', '请先启动模型，等待状态变为可用', 409)
            if self.active:
                raise SpeechError('SERVICE_BUSY', 'CPU 正在生成语音，请稍后重试', 409)
            item = {**request, 'id': 'tts-' + uuid.uuid4().hex, 'state': 'running', 'createdAt': now(), 'updatedAt': now(), 'failureCode': None}
            self.jobs[item['id']] = item
            self.active = item['id']; self._save(item)
            self._prune()
            threading.Thread(target=self._generate, args=(item['id'], self.generation, self.process, self.connection), daemon=True).start()
            return dict(item)

    def _validate_assets(self, request: dict) -> None:
        for name in ('referenceAudioId','promptAudioId'):
            if request[name]: self.assets.path(request[name])

    def submit_batch(self, payload: dict) -> dict:
        if not isinstance(payload,dict) or set(payload)-{'items'} or not isinstance(payload.get('items'),list) or not 1 <= len(payload['items']) <= 5:
            raise SpeechError('INVALID_ARGUMENT','批量请求需要 1–5 个 items')
        requests = [validate_request(item) for item in payload['items']]
        with self.lock:
            if self.status()['state'] != 'ready':
                raise SpeechError('INFERENCE_NOT_READY','请先启动模型',409)
            existing_ids = {j.get('requestId') for j in self.jobs.values()}
            new_count = sum(r['requestId'] not in existing_ids for r in requests)
            if sum(j['state']=='queued' for j in self.jobs.values())+new_count > 5:
                raise SpeechError('QUEUE_FULL','队列最多等待 5 个任务',409)
            if len({r['requestId'] for r in requests}) != len(requests):
                raise SpeechError('REQUEST_CONFLICT','批次内 requestId 不能重复',409)
            for request in requests:
                self._validate_assets(request)
                for old in self.jobs.values():
                    if old.get('requestId') == request['requestId'] and any(old.get(k, DEFAULTS.get(k)) != v for k,v in request.items()):
                        raise SpeechError('REQUEST_CONFLICT','请求标识已用于不同参数',409)
            items = []
            for request in requests:
                old = next((j for j in self.jobs.values() if j.get('requestId')==request['requestId']),None)
                if old:
                    items.append(old)
                    continue
                item = {**request,'id':'tts-'+uuid.uuid4().hex,'state':'queued','createdAt':now(),'updatedAt':now(),'failureCode':None,'chunkCount':0}
                self.jobs[item['id']] = item
                self._save(item)
                items.append(item)
            self._dispatch_next()
            self._prune()
            return {'items':[dict(j) for j in items]}

    def _dispatch_next(self) -> None:
        if self.active or self.state != 'ready': return
        item = next((j for j in self.jobs.values() if j['state']=='queued'),None)
        if not item: return
        item.update(state='running',startedAt=now(),updatedAt=now())
        self.active = item['id']
        self._save(item)
        threading.Thread(target=self._generate,args=(item['id'],self.generation,self.process,self.connection),daemon=True).start()

    def cancel(self, job_id: str) -> dict:
        with self.lock:
            item = self.job(job_id)
            if item['state'] not in ('queued','running'): return item
            if self.active == job_id:
                self.generation += 1
                self._terminate(self.process,self.connection)
                self.process,self.connection,self.active = None,None,None
                self._state('stopped')
            self.jobs[job_id].update(state='cancelled',failureCode='USER_CANCELLED',message='任务已取消',updatedAt=now())
            self._save(self.jobs[job_id])
            (self.results/(job_id+'.wav')).unlink(missing_ok=True)
            if self.state == 'stopped': self.start()
            return dict(self.jobs[job_id])

    def retry(self, job_id: str) -> dict:
        with self.lock:
            item = self.job(job_id)
            if item['state'] in ('running','queued'):
                raise SpeechError('SERVICE_BUSY','请等待任务完成后重新生成',409)
            from backend.speech_contracts import DEFAULTS
            request = {key:item[key] for key in DEFAULTS if key in item}
            request.update(text=item['text'],requestId=uuid.uuid4().hex)
            return self.submit_batch({'items':[request]})['items'][0]

    def delete(self, job_id: str) -> dict:
        with self.lock:
            if job_id not in self.jobs: return {'deleted':True}
            item = self.job(job_id)
            if item['state'] in ('queued','running'):
                raise SpeechError('SERVICE_BUSY','请先取消任务再删除',409)
            for suffix in ('.json','.wav'): (self.results/(job_id+suffix)).unlink(missing_ok=True)
            shutil.rmtree(self.results/'chunks'/job_id,ignore_errors=True)
            del self.jobs[job_id]
            return {'deleted':True}

    def delete_asset(self, asset_id: str) -> dict:
        with self.lock:
            if any(asset_id in (j.get('referenceAudioId'),j.get('promptAudioId')) for j in self.jobs.values()):
                raise SpeechError('ASSET_REFERENCED','该素材被历史任务引用，请先删除相关任务',409)
            self.assets.delete(asset_id)
            return {'deleted':True}

    def chunk(self, job_id: str, index: int) -> Path:
        item = self.job(job_id)
        if index < 0 or index >= item.get('chunkCount',0):
            raise SpeechError('AUDIO_NOT_READY','音频片段尚未生成',404)
        path = self.results/'chunks'/job_id/f'{index}.wav'
        if not path.is_file(): raise SpeechError('AUDIO_NOT_READY','音频片段不存在',404)
        return path

    def _generate(self, job_id: str, generation: int, process: Any, connection: Any) -> None:
        output = self.results / (job_id + '.wav')
        try:
            with self.lock:
                if generation != self.generation: return
                request = dict(self.jobs[job_id])
            paths = {'referencePath':str(self.assets.path(request['referenceAudioId'])) if request.get('referenceAudioId') else None,
                'promptPath':str(self.assets.path(request['promptAudioId'])) if request.get('promptAudioId') else None}
            connection.send({**request, **paths, 'output': str(output),'chunksDir':str(self.results/'chunks'/job_id)})
            def on_chunk(data):
                with self.lock:
                    if generation != self.generation: return
                    self.jobs[job_id].update({k:v for k,v in data.items() if k!='event'},updatedAt=now())
                    self._save(self.jobs[job_id])
            result = self._receive(process, connection, self.generation_timeout,on_chunk)
            if not output.is_file() or output.stat().st_size < 44: raise RuntimeError('INVALID_AUDIO')
            with output.open('rb') as stream: digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            with self.lock:
                if generation != self.generation: return
                self.jobs[job_id].update(result, state='completed', updatedAt=now(), audioBytes=output.stat().st_size, sha256=digest)
                self._save(self.jobs[job_id]); self.active = None
                self._dispatch_next()
        except Exception as error:
            with self.lock:
                if generation != self.generation: return
                self._terminate(process, connection)
                code = 'INFERENCE_TIMEOUT' if isinstance(error, TimeoutError) else 'INFERENCE_FAILED'
                self._state('failed', code)
                self.jobs[job_id].update(state='failed', failureCode=code, message='生成失败，请重新启动模型后重试', updatedAt=now())
                self._save(self.jobs[job_id]); self.active = None
                for queued in self.jobs.values():
                    if queued['state']=='queued':
                        queued.update(state='failed',failureCode='SERVICE_FAILED',message='服务失败，请重新启动后重试',updatedAt=now())
                        self._save(queued)
                output.unlink(missing_ok=True)

    def _prune(self) -> None:
        completed = sorted((j for j in self.jobs.values() if j['state'] not in ('queued','running')), key=lambda j:j['createdAt'], reverse=True)
        capacity = 50 - sum(j['state'] in ('queued','running') for j in self.jobs.values())
        for item in completed[capacity:]:
            for suffix in ('.json','.wav'): (self.results/(item['id']+suffix)).unlink(missing_ok=True)
            shutil.rmtree(self.results/'chunks'/item['id'],ignore_errors=True)
            del self.jobs[item['id']]

    def job(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            if not re.fullmatch(r'tts-[A-Za-z0-9]+', job_id) or job_id not in self.jobs:
                raise SpeechError('JOB_NOT_FOUND', '语音任务不存在', 404)
            return dict(self.jobs[job_id])

    def list_jobs(self, page: int = 1, page_size: int = 20, query: str = '', state: str = '') -> dict[str, Any]:
        with self.lock:
            items = sorted((j for j in self.jobs.values() if query.lower() in j['text'].lower() and (not state or j['state']==state)), key=lambda j:(j['createdAt'],j['id']), reverse=True)
            start = (page - 1) * page_size
            return {'items': [dict(j) for j in items[start:start+page_size]], 'page':page, 'pageSize':page_size, 'total':len(items), 'hasNext':start+page_size<len(items)}

    def audio(self, job_id: str) -> Path:
        item = self.job(job_id)
        if item['state'] != 'completed': raise SpeechError('AUDIO_NOT_READY', '音频尚未生成', 409)
        target = self.results / (job_id + '.wav')
        if not target.is_file(): raise SpeechError('AUDIO_NOT_FOUND', '音频文件不存在', 404)
        return target
