"""个人 JSON 备份；文件 ID 仅用于文件内关联，不包含账户或会话凭证。"""
import json
import os
import secrets
from datetime import datetime, timezone
from typing import Annotated, Literal
from urllib.parse import urlsplit

from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import db
from .study import normalize

MAX_BYTES = 2 * 1024 * 1024
Key = Annotated[int, Field(ge=1, le=9223372036854775807)]
Number = Annotated[int, Field(ge=1, le=1000000)]
Timestamp = Annotated[float, Field(ge=0, le=253402300799, allow_inf_nan=False)]


class Model(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')


class Word(Model):
    id: Key
    word: str = Field(min_length=1, max_length=100)
    meaning: str = Field(min_length=1, max_length=1000)
    example: str = Field(max_length=2000)
    example_translation: str = Field(max_length=2000)
    audio: str = Field(max_length=4096)
    phonetic: str = Field(max_length=1000)
    stage: int = Field(ge=-1, le=5)
    due_at: Timestamp | None
    memory_interval: float = Field(default=0, ge=0, le=2592000, allow_inf_nan=False)
    last_seen_at: Timestamp | None = None
    last_success_at: Timestamp | None = None


class Deck(Model):
    id: Key
    name: str = Field(min_length=1, max_length=100)
    created_at: Timestamp
    started_at: Timestamp | None
    words: list[Word] = Field(max_length=5000)


class Session(Model):
    id: Key
    deck_id: Key | None
    kind: Literal['new', 'review']
    status: Literal['active', 'complete', 'cancelled']
    round_number: Number
    scope_deck_ids: list[Key] = Field(default_factory=list, max_length=5000)


class Item(Model):
    id: Key
    session_id: Key
    word_id: Key
    round_number: Number
    position: int = Field(ge=0, le=100000)
    hint_used: Literal[0, 1]


class Attempt(Model):
    id: Key
    item_id: Key
    answer: str = Field(max_length=200)
    correct: Literal[0, 1]
    created_at: Timestamp
    effort: Literal['normal', 'hard'] = 'normal'
    needs_retry: Literal[0, 1] = 0
    delayed_recall: Literal[0, 1] = 0


class Backup(Model):
    format: Literal['remember_words_user']
    version: Literal[1]
    decks: list[Deck] = Field(max_length=5000)
    study_sessions: list[Session] = Field(max_length=50000)
    study_items: list[Item] = Field(max_length=100000)
    attempts: list[Attempt] = Field(max_length=100000)


class Export(Model):
    password: str = Field(min_length=1, max_length=128)


class Restore(Export):
    confirmation: Literal['OVERWRITE_MY_DATA']
    backup: dict


def encode(value):
    try:
        content = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    except (TypeError, ValueError, UnicodeError):
        raise HTTPException(422, '备份 JSON 无效') from None
    if len(content) > MAX_BYTES:
        raise HTTPException(413, '个人备份超过 2 MiB')
    return content


def validate(value):
    encode(value)
    # Old v1 exports lack scheduling evidence; preserve history without inventing evidence.
    value = json.loads(json.dumps(value))
    for attempt in value.get('attempts', []):
        if isinstance(attempt, dict):
            attempt.setdefault('needs_retry', int(attempt.get('correct') == 0))
    try:
        backup = Backup.model_validate(value)
    except ValidationError:
        raise HTTPException(422, '个人备份结构、版本或字段无效') from None

    def index(rows):
        result = {r.id: r for r in rows}
        if len(result) != len(rows):
            raise ValueError('重复内部 ID')
        return result

    try:
        decks = index(backup.decks)
        words = index([w for d in backup.decks for w in d.words])
        if len(words) > 5000:
            raise ValueError('最多 5000 个单词')
        owners = {}
        for deck in backup.decks:
            seen = set()
            if not deck.name.strip():
                raise ValueError('词库名为空')
            for word in deck.words:
                key = normalize(word.word)
                if not key or not word.meaning.strip() or key in seen:
                    raise ValueError('单词为空或同库重复')
                seen.add(key)
                if word.audio:
                    parsed = urlsplit(word.audio)
                    if parsed.scheme != 'https' or parsed.netloc not in ('api.dictionaryapi.dev', 'ssl.gstatic.com'):
                        raise ValueError('音频地址不属于允许的词典来源')
                if (word.stage == -1) != (word.due_at is None):
                    raise ValueError('复习阶段与到期时间不符')
                owners[word.id] = deck.id
        sessions = index(backup.study_sessions)
        active_decks = [s.deck_id for s in sessions.values() if s.status == 'active']
        if len(active_decks) != len(set(active_decks)):
            raise ValueError('同一词库存在多个活动批次')
        for session in sessions.values():
            if any(did not in decks for did in session.scope_deck_ids):
                raise ValueError('批次范围词库不存在')
            if session.deck_id is not None and session.deck_id not in decks:
                raise ValueError('批次词库不存在')
            if session.kind == 'new' and session.deck_id is None and session.status == 'active':
                raise ValueError('活动学习批次缺少词库')
        occupied = set()
        for session in sessions.values():
            if session.status != 'active':
                continue
            scope = set(session.scope_deck_ids)
            if session.deck_id is not None:
                scope.add(session.deck_id)
            scope.update(owners[item.word_id] for item in backup.study_items
                         if item.session_id == session.id and item.word_id in owners)
            if occupied & scope:
                raise ValueError('活动批次词库范围重叠')
            occupied.update(scope)
        items = index(backup.study_items)
        positions, round_words = set(), set()
        for item in items.values():
            session = sessions.get(item.session_id)
            if session is None or item.word_id not in words:
                raise ValueError('题目引用不存在')
            if item.round_number > session.round_number:
                raise ValueError('题目轮次超出批次')
            if session.deck_id is not None and owners[item.word_id] != session.deck_id:
                raise ValueError('题目不属于批次词库')
            if session.scope_deck_ids and owners[item.word_id] not in session.scope_deck_ids:
                raise ValueError('题目不属于批次选择范围')
            position = (item.session_id, item.round_number, item.position)
            word_key = (item.session_id, item.round_number, item.word_id)
            if position in positions or word_key in round_words:
                raise ValueError('重复题目或位置')
            positions.add(position)
            round_words.add(word_key)
        index(backup.attempts)
        answered = set()
        for attempt in backup.attempts:
            if attempt.item_id not in items or attempt.item_id in answered:
                raise ValueError('答题引用不存在或重复')
            answered.add(attempt.item_id)
        # Literal compares bool and int equally; reject bool explicitly at this boundary.
        if type(value['version']) is not int or any(
            type(row[field]) is not int
            for key, field in [('study_items', 'hint_used'), ('attempts', 'correct'), ('attempts', 'needs_retry'), ('attempts', 'delayed_recall')]
            for row in value[key] if field in row
        ):
            raise ValueError('标记必须为整数')
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return backup.model_dump()


FIELDS = {
    'decks': ('id', 'name', 'created_at', 'started_at'),
    'words': ('id', 'word', 'meaning', 'example', 'example_translation', 'audio', 'phonetic', 'stage', 'due_at', 'last_seen_at', 'last_success_at', 'memory_interval'),
    'study_sessions': ('id', 'deck_id', 'kind', 'status', 'round_number', 'scope_deck_ids'),
    'study_items': ('id', 'session_id', 'word_id', 'round_number', 'position', 'hint_used'),
    'attempts': ('id', 'item_id', 'answer', 'correct', 'created_at', 'effort', 'needs_retry', 'delayed_recall'),
}


def snapshot(connection, uid):
    def rows(table, query, args):
        return [{key: row[key] for key in FIELDS[table]} for row in connection.execute(query, args)]

    decks = rows('decks', 'SELECT * FROM decks WHERE user_id=? ORDER BY id', (uid,))
    words = []
    for deck in decks:
        deck['words'] = rows('words', 'SELECT * FROM words WHERE deck_id=? ORDER BY id', (deck['id'],))
        words.extend(deck['words'])
    sessions = rows('study_sessions', 'SELECT * FROM study_sessions WHERE user_id=? ORDER BY id', (uid,))
    for session in sessions:
        session['scope_deck_ids'] = json.loads(session['scope_deck_ids'])
    items = rows('study_items', '''SELECT i.* FROM study_items i JOIN study_sessions s ON s.id=i.session_id
        WHERE s.user_id=? ORDER BY i.id''', (uid,))
    attempts = rows('attempts', '''SELECT a.* FROM attempts a JOIN study_items i ON i.id=a.item_id
        JOIN study_sessions s ON s.id=i.session_id WHERE s.user_id=? ORDER BY a.id''', (uid,))
    groups = {'decks': decks, 'words': words, 'study_sessions': sessions, 'study_items': items, 'attempts': attempts}
    maps = {name: {row['id']: n for n, row in enumerate(group, 1)} for name, group in groups.items()}
    for name, group in groups.items():
        for row in group:
            row['id'] = maps[name][row['id']]
            if name == 'study_sessions':
                # Deleted decks may remain in historical scope; omit stale references.
                row['scope_deck_ids'] = [maps['decks'][did] for did in row['scope_deck_ids'] if did in maps['decks']]
            for field, target in [('deck_id', 'decks'), ('session_id', 'study_sessions'), ('word_id', 'words'), ('item_id', 'study_items')]:
                if field in row and row[field] is not None:
                    if row[field] not in maps[target]:
                        raise HTTPException(409, '当前个人数据存在跨账户或失效引用')
                    row[field] = maps[target][row[field]]
    return validate({'format': 'remember_words_user', 'version': 1, 'decks': decks,
                     'study_sessions': sessions, 'study_items': items, 'attempts': attempts})


def verify_password(connection, uid, password, hasher):
    row = connection.execute('SELECT password_hash FROM users WHERE id=? AND is_active=1', (uid,)).fetchone()
    try:
        valid = row is not None and hasher.verify(row['password_hash'], password)
    except (VerificationError, InvalidHashError):
        valid = False
    if not valid:
        raise HTTPException(403, '当前密码错误')


def save_backup(content, uid):
    folder = db.DB_PATH.parent / 'backups'
    path = folder / f'user-{uid}-{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")}-{secrets.token_hex(8)}.json'
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with open(path, 'xb', opener=lambda name, flags: os.open(name, flags, 0o600)) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        return path.name
    except OSError:
        raise HTTPException(503, '自动备份失败，未覆盖个人数据') from None


def replace(connection, uid, backup):
    connection.execute('''DELETE FROM attempts WHERE item_id IN (SELECT i.id FROM study_items i
        JOIN study_sessions s ON s.id=i.session_id WHERE s.user_id=?)''', (uid,))
    connection.execute('DELETE FROM study_items WHERE session_id IN (SELECT id FROM study_sessions WHERE user_id=?)', (uid,))
    connection.execute('DELETE FROM study_sessions WHERE user_id=?', (uid,))
    connection.execute('DELETE FROM words WHERE deck_id IN (SELECT id FROM decks WHERE user_id=?)', (uid,))
    connection.execute('DELETE FROM decks WHERE user_id=?', (uid,))
    maps = {name: {} for name in FIELDS}

    def insert(table, row, extra=None):
        fields = {key: value for key, value in row.items() if key not in ('id', 'words')}
        fields.update(extra or {})
        if table == 'study_sessions':
            fields['scope_deck_ids'] = json.dumps([maps['decks'][did] for did in row['scope_deck_ids']])
        cursor = connection.execute(f'INSERT INTO {table} ({",".join(fields)}) VALUES ({",".join("?" for _ in fields)})', tuple(fields.values()))
        maps[table][row['id']] = cursor.lastrowid
        return cursor.lastrowid

    for deck in backup['decks']:
        did = insert('decks', deck, {'user_id': uid})
        for word in deck['words']:
            insert('words', word, {'deck_id': did})
    for session in backup['study_sessions']:
        insert('study_sessions', session, {'user_id': uid,
               'deck_id': maps['decks'][session['deck_id']] if session['deck_id'] is not None else None,
               'status': 'cancelled' if session['status'] == 'active' else session['status']})
    for item in backup['study_items']:
        insert('study_items', item, {'session_id': maps['study_sessions'][item['session_id']], 'word_id': maps['words'][item['word_id']]})
    for attempt in backup['attempts']:
        insert('attempts', attempt, {'item_id': maps['study_items'][attempt['item_id']]})


def register(app, user, hasher):
    @app.post('/api/account/backup/export')
    def export(data: Export, account=Depends(user)):
        with db.connect() as connection:
            connection.execute('BEGIN')
            verify_password(connection, account['id'], data.password, hasher)
            content = encode(snapshot(connection, account['id']))
        return Response(content, media_type='application/json', headers={
            'Content-Disposition': 'attachment; filename="remember_words_user.json"'})

    @app.post('/api/account/backup/restore')
    def restore(data: Restore, account=Depends(user)):
        backup = validate(data.backup)
        with db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            verify_password(connection, account['id'], data.password, hasher)
            saved = save_backup(encode(snapshot(connection, account['id'])), account['id'])
            replace(connection, account['id'], backup)
        return {'ok': True, 'backup': saved}
