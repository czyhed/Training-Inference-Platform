"""Shared request contract for the platform adapter and private CPU worker."""
import math
import re
import uuid
from typing import Any


class SpeechError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


DEFAULTS = {
    'mode':'design', 'steps':10, 'cfg':2.0, 'seed':42,
    'minTokens':2, 'maxTokens':1024, 'normalize':False,
    'retryBadcase':False, 'retryMaxTimes':2, 'retryRatio':6.0,
    'streaming':False, 'referenceAudioId':None, 'promptAudioId':None, 'promptText':None,
}


def validate_request(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - (set(DEFAULTS) | {'text','requestId'}):
        raise SpeechError('INVALID_ARGUMENT', '不支持的请求参数')
    result = {**DEFAULTS, **value}
    text = result.get('text')
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 300:
        raise SpeechError('INVALID_ARGUMENT', '请输入 1–300 个字符')
    result['text'] = text.strip()
    for name, low, high in [('steps',4,20),('seed',0,2147483647),('minTokens',1,1024),('maxTokens',16,2048),('retryMaxTimes',0,3)]:
        if type(result[name]) is not int or not low <= result[name] <= high:
            raise SpeechError('INVALID_ARGUMENT', f'{name} 必须是 {low}–{high} 的整数')
    for name, low, high in [('cfg',1,4),('retryRatio',1,20)]:
        if type(result[name]) not in (int,float) or not math.isfinite(result[name]) or not low <= result[name] <= high:
            raise SpeechError('INVALID_ARGUMENT', f'{name} 必须在 {low}–{high} 之间')
        result[name] = float(result[name])
    for name in ('normalize','retryBadcase','streaming'):
        if type(result[name]) is not bool:
            raise SpeechError('INVALID_ARGUMENT', f'{name} 必须是布尔值')
    if result['minTokens'] >= result['maxTokens']:
        raise SpeechError('INVALID_ARGUMENT', '最小长度必须小于最大长度')
    mode = result['mode']
    if mode not in ('design','clone','continue','combined'):
        raise SpeechError('INVALID_ARGUMENT', '未知的合成模式')
    for key in ('referenceAudioId','promptAudioId'):
        if result[key] is not None and (not isinstance(result[key],str) or not re.fullmatch(r'aud-[a-f0-9]{32}',result[key])):
            raise SpeechError('INVALID_ARGUMENT', '音频素材标识不合法')
    needs_reference, needs_prompt = mode in ('clone','combined'), mode in ('continue','combined')
    if bool(result['referenceAudioId']) != needs_reference or bool(result['promptAudioId']) != needs_prompt:
        raise SpeechError('INVALID_ARGUMENT', '请选择当前模式所需的参考音频，移除其他模式的音频')
    if needs_prompt:
        if not isinstance(result['promptText'],str) or not 1 <= len(result['promptText'].strip()) <= 300:
            raise SpeechError('INVALID_ARGUMENT', '音频续写需要填写与提示音频一致的文本（1–300 字）')
        result['promptText'] = result['promptText'].strip()
    elif result['promptText'] is not None:
        raise SpeechError('INVALID_ARGUMENT', '当前模式不使用提示文本')
    request_id = value.get('requestId') or uuid.uuid4().hex
    if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',request_id):
        raise SpeechError('INVALID_ARGUMENT', '请求标识不合法')
    result['requestId'] = request_id
    return result
