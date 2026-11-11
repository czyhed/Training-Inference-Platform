"""Bounded audio uploads. No paths or remote URLs are accepted from callers."""
import hashlib
import json
import os
import re
import subprocess
import threading
import uuid
import wave
from pathlib import Path
from datetime import datetime, timezone
from backend.speech_contracts import SpeechError

MAX_UPLOAD = 10 * 1024 * 1024


class AudioAssets:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def get(self, asset_id: str) -> dict:
        if not re.fullmatch(r'aud-[a-f0-9]{32}', asset_id):
            raise SpeechError('AUDIO_NOT_FOUND', '音频素材不存在', 404)
        try:
            return json.loads((self.root / (asset_id+'.json')).read_text(encoding='utf-8'))
        except FileNotFoundError:
            raise SpeechError('AUDIO_NOT_FOUND', '音频素材不存在', 404) from None

    def path(self, asset_id: str) -> Path:
        self.get(asset_id)
        path = self.root / (asset_id+'.wav')
        if not path.is_file():
            raise SpeechError('AUDIO_NOT_FOUND', '音频文件不存在', 404)
        return path

    def list(self) -> list[dict]:
        with self.lock:
            return sorted([json.loads(p.read_text(encoding='utf-8')) for p in self.root.glob('aud-*.json')],key=lambda x:(x['createdAt'],x['id']),reverse=True)

    def upload(self, data: bytes, name: str) -> dict:
        if not data:
            raise SpeechError('INVALID_AUDIO', '音频文件为空，请选择有效的音频', 400)
        if len(data) > MAX_UPLOAD:
            raise SpeechError('UPLOAD_TOO_LARGE', '音频文件必须在 1 字节至 10 MB 之间', 413)
        with self.lock:
            if len(self.list()) >= 100:
                raise SpeechError('ASSET_LIMIT', '最多保留 100 个音频素材，请先删除不用的素材', 409)
            asset_id = 'aud-' + uuid.uuid4().hex
            raw, target = self.root/(asset_id+'.upload'), self.root/(asset_id+'.wav')
            try:
                raw.write_bytes(data)
                subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-protocol_whitelist','file,pipe','-format_whitelist','wav,mp3,flac,ogg,mov,matroska,webm,aac','-i',str(raw),'-t','31','-ac','1','-ar','24000','-c:a','pcm_s16le',str(target)],check=True,capture_output=True,timeout=30)
                with wave.open(str(target)) as audio:
                    duration = audio.getnframes()/audio.getframerate()
                if not .5 <= duration <= 30:
                    raise SpeechError('INVALID_AUDIO', '参考音频需要 0.5–30 秒，建议使用 3–15 秒清晰人声', 400)
                metadata = {'id':asset_id,'name':name[:120] or '参考音频','durationSeconds':round(duration,3),'sampleRate':24000,'audioBytes':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'createdAt':datetime.now(timezone.utc).isoformat()}
                temporary = self.root/(asset_id+'.tmp')
                temporary.write_text(json.dumps(metadata,ensure_ascii=False),encoding='utf-8')
                os.replace(temporary,self.root/(asset_id+'.json'))
                return metadata
            except (subprocess.SubprocessError, wave.Error, EOFError):
                target.unlink(missing_ok=True)
                raise SpeechError('INVALID_AUDIO','无法读取音频，请上传 WAV、MP3、FLAC、M4A 或 WebM 音频',400) from None
            except Exception:
                target.unlink(missing_ok=True)
                raise
            finally:
                raw.unlink(missing_ok=True)

    def delete(self, asset_id: str) -> None:
        with self.lock:
            self.get(asset_id)
            for suffix in ('.json','.wav'):
                (self.root/(asset_id+suffix)).unlink(missing_ok=True)
