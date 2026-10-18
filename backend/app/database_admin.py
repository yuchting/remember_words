"""家长整库导出、覆盖恢复；调用方须串行化 API 请求及认证。"""
import base64
import os
import re
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

from argon2 import extract_parameters
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from . import db

MAX_BYTES = 50 * 1024 * 1024
CONFIRMATION = 'OVERWRITE_ALL_DATA'


class ExportInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    password: str = Field(min_length=1, max_length=128)


def _schema(connection):
    return [(kind, name, table, re.sub(r'\s+', ' ', sql.strip()) if sql else None)
            for kind, name, table, sql in connection.execute(
                'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]


def _validate(connection):
    connection.execute('PRAGMA query_only=ON')
    connection.execute('PRAGMA trusted_schema=OFF')
    version = connection.execute('PRAGMA user_version').fetchone()[0]
    if version not in (1, 2):
        raise ValueError('仅支持数据库版本 1、2')
    with closing(sqlite3.connect(':memory:')) as expected:
        expected.executescript(db.BASE_SCHEMA if version == 1 else db.SCHEMA)
        if _schema(connection) != _schema(expected):
            raise ValueError('数据库结构不匹配，或包含额外表、索引、视图、触发器')
    if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise ValueError('数据库完整性检查失败')
    if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
        raise ValueError('数据库外键检查失败')
    if not connection.execute("SELECT 1 FROM users WHERE role='parent' AND is_active=1 LIMIT 1").fetchone():
        raise ValueError('数据库必须至少包含一个启用的家长账户')
    for (password_hash,) in connection.execute('SELECT password_hash FROM users'):
        try:
            if not isinstance(password_hash, str) or len(password_hash) > 512:
                raise ValueError('哈希格式错误')
            if not re.fullmatch(
                r'\$argon2(?:id|i|d)\$v=(?:16|19)\$m=[0-9]+,t=[0-9]+,p=[0-9]+'
                r'\$[A-Za-z0-9+/]+\$[A-Za-z0-9+/]+', password_hash
            ):
                raise ValueError('哈希格式错误')
            parameters = extract_parameters(password_hash)
            if not (1 <= parameters.parallelism <= 16
                    and 8 * parameters.parallelism <= parameters.memory_cost <= 262144
                    and 1 <= parameters.time_cost <= 10
                    and 8 <= parameters.salt_len <= 64
                    and 16 <= parameters.hash_len <= 64):
                raise ValueError('哈希参数超出安全范围')
            for encoded in password_hash.split('$')[-2:]:
                decoded = base64.b64decode(encoded + '=' * (-len(encoded) % 4), validate=True)
                if base64.b64encode(decoded).decode('ascii').rstrip('=') != encoded:
                    raise ValueError('哈希编码错误')
        except (InvalidHashError, TypeError, ValueError) as error:
            raise ValueError('账户密码不是有效且参数安全的 Argon2 哈希') from error


def _clear_sessions(connection):
    connection.execute('PRAGMA secure_delete=ON')
    connection.execute('DELETE FROM auth_sessions')
    connection.commit()
    connection.execute('VACUUM')


def _backup_file(connection, path):
    # 独占创建，避免覆盖已有备份；从创建时起仅拥有者可读写。
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    with closing(sqlite3.connect(path)) as target:
        connection.backup(target)


def _restore(incoming):
    path = Path(db.DB_PATH)
    backups = path.parent / 'backups'
    backups.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    with closing(sqlite3.connect(path, timeout=10)) as current, closing(sqlite3.connect(':memory:')) as previous:
        current.backup(previous)
        _clear_sessions(previous)
        descriptor, backup_name = tempfile.mkstemp(prefix=f'pre-restore-{stamp}-', suffix='.sqlite3', dir=backups)
        os.close(descriptor)
        backup_path = Path(backup_name)
        with closing(sqlite3.connect(backup_path)) as saved:
            previous.backup(saved)
        try:
            incoming.backup(current)
        except Exception as error:
            try:
                with closing(sqlite3.connect(backup_path)) as saved:
                    saved.backup(current)
            except Exception as rollback_error:
                raise HTTPException(500, f'恢复失败且自动回滚失败；请停服并从备份 {backup_path.name} 恢复') from rollback_error
            raise HTTPException(500, f'恢复失败，已回滚原数据并清空会话；备份 {backup_path.name}') from error
    return {'ok': True, 'message': '整库已覆盖，所有会话已失效，请重新登录', 'backup': backup_path.name}


def register(app, parent, hasher):
    def verify(account, password):
        try:
            valid = hasher.verify(account['password_hash'], password)
        except (VerificationError, InvalidHashError):
            valid = False
        if not valid:
            raise HTTPException(401, '家长密码错误')

    @app.post('/api/admin/database/export')
    def export_database(data: ExportInput, account=Depends(parent)):
        verify(account, data.password)
        try:
            with tempfile.TemporaryDirectory() as directory, closing(sqlite3.connect(':memory:')) as snapshot:
                with closing(sqlite3.connect(db.DB_PATH, timeout=10)) as source:
                    source.backup(snapshot)
                _clear_sessions(snapshot)
                path = Path(directory) / 'words.sqlite3'
                _backup_file(snapshot, path)
                if path.stat().st_size > MAX_BYTES:
                    raise HTTPException(413, '数据库超过 50 MiB，不能通过网页导出')
                payload = path.read_bytes()
        except (sqlite3.Error, OSError) as error:
            raise HTTPException(500, '数据库导出失败') from error
        return Response(payload, media_type='application/vnd.sqlite3', headers={
            'Content-Disposition': 'attachment; filename="words.sqlite3"', 'Cache-Control': 'no-store'})

    @app.post('/api/admin/database/restore')
    async def restore_database(request: Request, account=Depends(parent)):
        password = unquote(request.headers.get('x-admin-password', ''))
        if not 1 <= len(password) <= 128:
            raise HTTPException(401, '家长密码错误')
        verify(account, password)
        if request.headers.get('x-restore-confirmation') != CONFIRMATION:
            raise HTTPException(403, '必须明确确认覆盖全部数据')
        payload = bytearray()
        async for chunk in request.stream():
            payload.extend(chunk)
            if len(payload) > MAX_BYTES:
                raise HTTPException(413, '数据库超过 50 MiB')
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'incoming.sqlite3'
                path.write_bytes(payload)
                path.chmod(0o600)
                with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as source:
                    _validate(source)
                    with closing(sqlite3.connect(':memory:')) as snapshot:
                        source.backup(snapshot)
                        db.migrate(snapshot)
                        _clear_sessions(snapshot)
                        try:
                            return _restore(snapshot)
                        except (sqlite3.Error, OSError) as error:
                            raise HTTPException(500, '恢复前备份失败，原数据未覆盖') from error
        except (sqlite3.Error, ValueError) as error:
            raise HTTPException(422, '数据库无效：' + str(error)) from error
        except OSError as error:
            raise HTTPException(500, '数据库恢复文件操作失败，原数据未覆盖') from error
