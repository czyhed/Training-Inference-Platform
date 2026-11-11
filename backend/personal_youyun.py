"""Personal cloud credentials and durable resource ownership.

Only encrypted revisions live here. Shared task/model payloads contain no keys.
An explicit resource scope overrides request identity for background recovery,
but rejects a request by a different owner before touching the provider.
"""
from __future__ import annotations

import inspect
import json
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from fastapi import APIRouter, HTTPException, Request


class AccountAccessError(HTTPException):
    def __init__(self, message='请先在系统配置中保存自己的优云智算账号。', status_code=409):
        super().__init__(status_code=status_code, detail=message)


credential_store: CredentialStore | None = None
current_user: ContextVar[str | None] = ContextVar('youyun_user', default=None)
current_resource: ContextVar[str | None] = ContextVar('youyun_resource', default=None)


class CredentialStore:
    def __init__(self, backend: str, path: Path, dsn: str, key_root: Path):
        self.backend, self.path, self.dsn = backend, path, dsn
        self._lock = threading.RLock()
        key = os.environ.get('XUNTUI_CREDENTIAL_KEY', '').strip()
        if not key:
            key_root.mkdir(parents=True, exist_ok=True)
            key_path = key_root / 'personal-youyun.key'
            try:
                fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                key = key_path.read_text(encoding='ascii').strip()
            else:
                key = Fernet.generate_key().decode('ascii')
                with os.fdopen(fd, 'w', encoding='ascii') as stream:
                    stream.write(key)
                    stream.flush()
                    os.fsync(stream.fileno())
        self.cipher = Fernet(key.encode('ascii'))

    @contextmanager
    def connect(self):
        if self.backend == 'postgres':
            from backend.postgres_storage import pg_connect
            with pg_connect(self.dsn) as connection:
                yield connection
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=30)
            connection.row_factory = sqlite3.Row
            try:
                with connection:
                    yield connection
            finally:
                connection.close()

    def execute(self, connection, sql, values=()):
        return connection.execute(sql.replace('?', '%s') if self.backend == 'postgres' else sql, values)

    def initialize(self):
        with self.connect() as connection:
            for sql in (
                'CREATE TABLE IF NOT EXISTS personal_youyun_revisions (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, encrypted TEXT NOT NULL, created_at TEXT NOT NULL)',
                'CREATE TABLE IF NOT EXISTS personal_youyun_settings (owner_id TEXT PRIMARY KEY, revision_id TEXT NOT NULL REFERENCES personal_youyun_revisions(id))',
                'CREATE TABLE IF NOT EXISTS personal_youyun_resources (resource_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, revision_id TEXT REFERENCES personal_youyun_revisions(id))',
            ):
                connection.execute(sql)

    def revision(self, owner: str, connection=None):
        if connection is None:
            with self.connect() as connection:
                return self.revision(owner, connection)
        row = self.execute(connection, 'SELECT revision_id FROM personal_youyun_settings WHERE owner_id = ?', (owner,)).fetchone()
        return row['revision_id'] if row else None

    def decrypt(self, revision: str | None):
        if revision is None:
            return {}
        with self.connect() as connection:
            row = self.execute(connection, 'SELECT encrypted FROM personal_youyun_revisions WHERE id = ?', (revision,)).fetchone()
        if row is None:
            raise AccountAccessError('任务绑定的优云配置已缺失，请联系管理员。')
        try:
            return json.loads(self.cipher.decrypt(row['encrypted'].encode('ascii')))
        except Exception:
            raise AccountAccessError('优云配置无法解密，请检查服务器凭据加密密钥。') from None

    def status(self, owner):
        value = self.decrypt(self.revision(owner))
        public_key = value.get('public_key', '')
        return {'configured': bool(public_key and value.get('private_key')),
                'publicKeyMasked': ('••••' + public_key[-4:]) if public_key else '',
                'region': value.get('region', 'cn-wlcb'), 'zone': value.get('zone', 'cn-wlcb-01')}

    def save(self, owner: str, payload: dict):
        allowed = {'publicKey', 'privateKey', 'region', 'zone'}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise AccountAccessError('优云配置字段无效。', 422)
        if any(not isinstance(v, str) or len(v) > 4096 for v in payload.values()):
            raise AccountAccessError('优云配置必须为有效文本。', 422)
        # Serialize local edits; the database pointer update is atomic across processes.
        with self._lock:
            old = self.decrypt(self.revision(owner))
            value = {key: payload.get(field, '').strip() or old.get(key, '')
                     for key, field in (('public_key', 'publicKey'), ('private_key', 'privateKey'))}
            value.update(region=payload.get('region', old.get('region', '')).strip(),
                         zone=payload.get('zone', old.get('zone', '')).strip())
            if not value['public_key'] or not value['private_key']:
                raise AccountAccessError('请填写 Public Key 和 Private Key。', 422)
            if any(re.search(r'\s', value[key]) for key in ('public_key', 'private_key')):
                raise AccountAccessError('API Key 不应包含空白字符。', 422)
            if not re.fullmatch(r'[a-z][a-z0-9-]{1,63}', value['region']) or not re.fullmatch(re.escape(value['region']) + r'-[a-z0-9]+', value['zone']):
                raise AccountAccessError('请填写匹配的地域和可用区，例如 cn-wlcb / cn-wlcb-01。', 422)
            revision = 'yc-' + uuid.uuid4().hex
            encrypted = self.cipher.encrypt(json.dumps(value).encode('utf-8')).decode('ascii')
            with self.connect() as connection:
                self.execute(connection, 'INSERT INTO personal_youyun_revisions VALUES (?, ?, ?, ?)',
                             (revision, owner, encrypted, datetime.now(timezone.utc).isoformat()))
                self.execute(connection, 'INSERT INTO personal_youyun_settings VALUES (?, ?) ON CONFLICT(owner_id) DO UPDATE SET revision_id = excluded.revision_id', (owner, revision))
        return self.status(owner)

    def binding(self, resource_id):
        with self.connect() as connection:
            row = self.execute(connection, 'SELECT owner_id, revision_id FROM personal_youyun_resources WHERE resource_id = ?', (resource_id,)).fetchone()
        return dict(row) if row else None

    def bind(self, resource_id, owner, remote, source_resource_id=None):
        if source_resource_id:
            source = self.binding(source_resource_id)
            if not source or source['owner_id'] != owner:
                raise AccountAccessError('只能使用自己的推理服务生成内容。', 403)
            revision = source['revision_id']
        else:
            revision = self.revision(owner) if remote else None
        if remote and not revision:
            raise AccountAccessError()
        with self.connect() as connection:
            self.execute(connection, 'INSERT INTO personal_youyun_resources VALUES (?, ?, ?) ON CONFLICT(resource_id) DO NOTHING', (resource_id, owner, revision))
        existing = self.binding(resource_id)
        if existing['owner_id'] != owner:
            raise AccountAccessError('只能操作自己的算力任务或推理服务。', 403)


@contextmanager
def user_scope(user_id):
    token = current_user.set(user_id)
    try:
        yield
    finally:
        current_user.reset(token)


def bind_resource(resource_id: str, *, remote=True, source_resource_id=None):
    if credential_store is not None and current_user.get():
        credential_store.bind(resource_id, current_user.get(), remote, source_resource_id)


def check_resource_access(resource_id):
    if credential_store is None or not current_user.get():
        return
    binding = credential_store.binding(resource_id)
    if not binding or binding['owner_id'] != current_user.get():
        raise AccountAccessError('只能操作自己的算力任务或推理服务；共享模型可用于创建自己的服务。', 403)


def check_request_access(method, path):
    if method not in {'POST', 'PUT', 'PATCH', 'DELETE'} or path.endswith('/clone'):
        return
    match = re.match(r'^/api/(?:public/)?(?:tasks|vision/tasks|inference/services|image-generation/(?:training/tasks|tasks|services))/([^/]+)(?:/|$)', path)
    if match and match[1] not in {'preflight', 'materialize-preview', 'cli'}:
        check_resource_access(match[1])
        return match[1]


@contextmanager
def resource_scope(resource_id):
    check_resource_access(resource_id)
    token = current_resource.set(resource_id)
    try:
        yield
    finally:
        current_resource.reset(token)


def scoped_resource(parameter):
    """Restore persisted account in request, worker, restart and cleanup paths."""
    def decorate(function):
        signature = inspect.signature(function)
        def resource(args, kwargs):
            value = signature.bind(*args, **kwargs).arguments.get(parameter)
            return str(value.get('id') or value.get('taskId')) if isinstance(value, dict) else value
        if inspect.iscoroutinefunction(function):
            @wraps(function)
            async def wrapped(*args, **kwargs):
                with resource_scope(resource(args, kwargs)):
                    return await function(*args, **kwargs)
        else:
            @wraps(function)
            def wrapped(*args, **kwargs):
                with resource_scope(resource(args, kwargs)):
                    return function(*args, **kwargs)
        return wrapped
    return decorate


def apply_account(config):
    if credential_store is None:
        return config
    resource_id = current_resource.get()
    if resource_id:
        binding = credential_store.binding(resource_id)
        if binding is None:  # Historical resources: background maintenance only.
            return config
        value = credential_store.decrypt(binding['revision_id'])
    elif current_user.get():
        value = credential_store.decrypt(credential_store.revision(current_user.get()))
    else:
        return config
    # Explicitly clear both server keys when a logged-in user has no settings.
    return replace(config, public_key=value.get('public_key', ''), private_key=value.get('private_key', ''),
                   region=value.get('region', config.region), zone=value.get('zone', config.zone),
                   pod_zone=value.get('zone', config.pod_zone))


def background_worker(function):
    """Queue workers must not inherit the identity of the request starting them."""
    @wraps(function)
    async def wrapped(*args, **kwargs):
        user_token = current_user.set(None)
        resource_token = current_resource.set(None)
        try:
            return await function(*args, **kwargs)
        finally:
            current_resource.reset(resource_token)
            current_user.reset(user_token)
    return wrapped


def create_settings_router():
    router = APIRouter(prefix='/api/settings/youyun', tags=['Personal settings'])

    def owner():
        if not current_user.get() or credential_store is None:
            raise AccountAccessError('请先登录。', 401)
        return current_user.get()

    @router.get('')
    async def get_settings():
        return credential_store.status(owner())

    @router.put('')
    async def save_settings(request: Request):
        user = owner()
        if len(await request.body()) > 18000:
            raise AccountAccessError('配置内容过长。', 422)
        try:
            payload = await request.json()
        except Exception:
            raise AccountAccessError('配置格式无效。', 422) from None
        return credential_store.save(user, payload)

    @router.post('/test')
    async def test_connection():
        owner()
        import asyncio
        from backend.providers.youyun.client import YouyunClient
        try:
            client = YouyunClient()
            zones = await asyncio.to_thread(client.describe_support_zones)
        except Exception:
            raise AccountAccessError('连接失败，请检查已保存的 API Key、账号权限或稍后重试。', 400) from None
        return {'connected': True, 'message': '优云智算连接成功，未创建任何计费实例。', 'zoneCount': len(zones)}

    return router
