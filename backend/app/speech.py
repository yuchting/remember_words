"""云端朗读：固定服务端点、独立密钥文件、有界音频缓存。"""
import base64
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Literal
from xml.sax.saxutils import escape

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import db

MAX_CACHE = 100 * 1024 * 1024
MAX_AUDIO = 4 * 1024 * 1024


class Provider(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    key: str = Field(default='', max_length=512)
    clear_key: bool = False
    region: str = Field(default='eastasia', pattern=r'^[a-z][a-z0-9-]{0,39}$')
    voice: str = Field(default='en-US-JennyNeural', pattern=r'^en-[A-Z]{2}-[A-Za-z0-9-]{1,80}$')


class Settings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    primary: Literal['off', 'azure', 'google'] = 'off'
    fallback: Literal['off', 'azure', 'google'] = 'off'
    browser_fallback: bool = False
    azure: Provider = Field(default_factory=Provider)
    google: Provider = Field(default_factory=lambda: Provider(voice='en-US-Neural2-F'))


def directory() -> Path:
    return db.DB_PATH.parent / 'speech'


def load() -> Settings:
    path = directory() / 'settings.json'
    if not path.exists():
        return Settings()
    try:
        return Settings.model_validate_json(path.read_text())
    except (ValueError, OSError):
        raise HTTPException(503, '语音配置无法读取，请家长重新配置') from None


def public(settings: Settings) -> dict:
    result = settings.model_dump()
    for name in ('azure', 'google'):
        result[name]['key_set'] = bool(result[name].pop('key'))
        result[name].pop('clear_key')
    return result


def save(settings: Settings) -> dict:
    settings = settings.model_copy(deep=True)
    old = load()
    for name in ('azure', 'google'):
        provider = getattr(settings, name)
        if provider.clear_key:
            provider.key = ''
        elif not provider.key:
            provider.key = getattr(old, name).key
        if provider.key and not re.fullmatch(r'[A-Za-z0-9_\-]+', provider.key):
            raise HTTPException(422, 'API Key 格式错误')
        provider.clear_key = False
    if settings.primary != 'off':
        for name in {settings.primary, settings.fallback} - {'off'}:
            if not getattr(settings, name).key:
                raise HTTPException(422, f'{name} 尚未配置 API Key')
    root = directory()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = root / 'settings.tmp'
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(settings.model_dump_json())
        temporary.replace(root / 'settings.json')
    except OSError:
        raise HTTPException(503, '语音配置保存失败，请检查服务器磁盘空间和目录权限') from None
    finally:
        temporary.unlink(missing_ok=True)
    return public(settings)


def cache_files() -> list[Path]:
    return list((directory() / 'cache').glob('*.mp3'))


def cache_stats() -> dict:
    files = cache_files()
    return {'files': len(files), 'bytes': sum(p.stat().st_size for p in files), 'max_bytes': MAX_CACHE}


def clear_cache() -> dict:
    for path in cache_files():
        path.unlink(missing_ok=True)
    return cache_stats()


async def synthesize(client: httpx.AsyncClient, name: str, provider: Provider, text: str) -> bytes:
    if not provider.key:
        raise ValueError('missing key')
    if name == 'azure':
        body = (f'<speak version="1.0" xml:lang="{provider.voice[:5]}">'
                f'<voice name="{provider.voice}"><prosody rate="-10%">{escape(text)}</prosody></voice></speak>')
        async with client.stream('POST', f'https://{provider.region}.tts.speech.microsoft.com/cognitiveservices/v1',
                                 headers={'Ocp-Apim-Subscription-Key': provider.key,
                                          'Content-Type': 'application/ssml+xml',
                                          'X-Microsoft-OutputFormat': 'audio-24khz-48kbitrate-mono-mp3'},
                                 content=body.encode()) as response:
            response.raise_for_status()
            if 'audio/' not in response.headers.get('content-type', ''):
                raise ValueError('invalid audio')
            result = bytearray()
            async for chunk in response.aiter_bytes():
                result.extend(chunk)
                if len(result) > MAX_AUDIO:
                    raise ValueError('audio too large')
            return bytes(result)
    async with client.stream('POST', 'https://texttospeech.googleapis.com/v1/text:synthesize',
                             headers={'X-Goog-Api-Key': provider.key}, json={
                                 'input': {'text': text},
                                 'voice': {'languageCode': provider.voice[:5], 'name': provider.voice},
                                 'audioConfig': {'audioEncoding': 'MP3', 'speakingRate': 0.9},
                             }) as response:
        response.raise_for_status()
        result = bytearray()
        async for chunk in response.aiter_bytes():
            result.extend(chunk)
            if len(result) > MAX_AUDIO * 2:
                raise ValueError('audio too large')
        return base64.b64decode(json.loads(result)['audioContent'], validate=True)


def valid_audio(data: bytes) -> bool:
    return bool(data) and len(data) <= MAX_AUDIO and (data.startswith(b'ID3') or (len(data) > 1 and data[0] == 255 and data[1] & 224 == 224))


async def audio(text: str) -> bytes:
    settings = load()
    if settings.primary == 'off':
        raise HTTPException(409, '云端朗读未启用')
    names = list(dict.fromkeys(n for n in (settings.primary, settings.fallback) if n != 'off'))
    # Check every configured cache before making a network request, including fallback cache.
    paths = []
    for name in names:
        provider = getattr(settings, name)
        key = hashlib.sha256(json.dumps([name, provider.region, provider.voice, 0.9, text], ensure_ascii=False).encode()).hexdigest()
        path = directory() / 'cache' / f'{key}.mp3'
        paths.append(path)
        if path.exists():
            try:
                if 0 < path.stat().st_size <= MAX_AUDIO:
                    return path.read_bytes()
                path.unlink()
            except OSError:
                raise HTTPException(503, '语音缓存读取失败，请检查目录权限或清空缓存') from None
    async with httpx.AsyncClient(timeout=12, follow_redirects=False) as client:
        for name, path in zip(names, paths):
            try:
                data = await synthesize(client, name, getattr(settings, name), text)
                if not valid_audio(data):
                    raise ValueError('invalid MP3')
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                continue
            try:
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                files = sorted(cache_files(), key=lambda p: p.stat().st_mtime)
                size = sum(p.stat().st_size for p in files)
                while files and size + len(data) > MAX_CACHE:
                    oldest = files.pop(0)
                    size -= oldest.stat().st_size
                    oldest.unlink()
                temporary = path.with_suffix('.tmp')
                temporary.write_bytes(data)
                temporary.replace(path)
            except OSError:
                raise HTTPException(503, '语音缓存写入失败，请检查服务器磁盘空间和目录权限') from None
            return data
    raise HTTPException(502, '云端语音服务失败，请检查密钥、区域、额度或网络；可配置备用服务')
