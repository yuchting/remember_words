import asyncio
import hashlib
import json
import os
import secrets
import sqlite3
import time
import unicodedata
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime
from urllib.parse import quote, urlsplit

import httpx
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, Field

from . import speech
from .db import ROOT, connect, initialize
from .study import add_round, content, current_item, finish_round, normalize, submit

hasher = PasswordHasher()
DUMMY_HASH = hasher.hash(secrets.token_urlsafe(24))
failures = defaultdict(deque)
speech_requests = defaultdict(deque)
COOKIE = 'words_session'
SECURE = os.environ.get('WORDS_HTTPS', '0') == '1'
BASE_PATH = os.environ.get('WORDS_BASE_PATH', '').rstrip('/')
if BASE_PATH and (not BASE_PATH.startswith('/') or any(
    not part or not all(c.isascii() and (c.isalnum() or c in '-_') for c in part)
    for part in BASE_PATH[1:].split('/')
)):
    raise ValueError('WORDS_BASE_PATH 必须为空或 /words 形式的路径')
COOKIE_PATH = BASE_PATH + '/'


@asynccontextmanager
async def lifespan(app):
    initialize()
    yield


app = FastAPI(lifespan=lifespan, root_path=BASE_PATH)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=[
    host.strip() for host in os.environ.get('WORDS_HOSTS', 'localhost,127.0.0.1,testserver').split(',') if host.strip()
])


# ponytail: single-worker API gate keeps restore exclusive; use maintenance mode before multi-worker deployment.
api_gate = asyncio.Lock()


@app.middleware('http')
async def api_exclusive(request, call_next):
    path = request.scope['path']
    if BASE_PATH and path.startswith(BASE_PATH + '/'):
        path = path[len(BASE_PATH):]
    request.state.api_path = path
    if path.startswith('/api/'):
        async with api_gate:
            return await limits(request, call_next)
    return await limits(request, call_next)


async def limits(request, call_next):
    path = request.state.api_path
    is_api = path.startswith('/api/')
    if is_api:
        maximum = 50 * 1024 * 1024 if path == '/api/admin/database/restore' else 2 * 1024 * 1024
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > maximum:
                return Response('请求超过允许大小', status_code=413)
        request._body = bytes(body)
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            if origin and urlsplit(origin).netloc != request.headers.get('host'):
                return Response('来源不允许', status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    if is_api:
        response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Frame-Options'] = 'DENY'
    return response


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def user(request: Request):
    token = request.cookies.get(COOKIE, '')
    with connect() as db:
        row = db.execute('''SELECT u.*,s.csrf_token FROM auth_sessions s JOIN users u ON u.id=s.user_id
            WHERE s.token_hash=? AND s.expires_at>? AND u.is_active=1''', (digest(token), time.time())).fetchone()
    if not row:
        raise HTTPException(401, '请先登录')
    if request.method not in ('GET', 'HEAD') and not secrets.compare_digest(
        request.headers.get('x-csrf-token', ''), row['csrf_token']
    ):
        raise HTTPException(403, '会话校验失败，请刷新页面')
    return dict(row)


def parent(account=Depends(user)):
    if account['role'] != 'parent':
        raise HTTPException(403, '需要家长权限')
    return account


class InputModel(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')


class Login(InputModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=8, max_length=128)


class UserPatch(InputModel):
    password: str | None = Field(default=None, min_length=8, max_length=128)
    is_active: bool | None = None


@app.post('/api/auth/login')
def login(data: Login, request: Request, response: Response):
    key = request.client.host if request.client else 'unknown'
    now = time.time()
    queue = failures[key]
    while queue and queue[0] < now - 300:
        queue.popleft()
    if len(queue) >= 10:
        raise HTTPException(429, '登录失败过多，5 分钟后再试')
    with connect() as db:
        row = db.execute('SELECT * FROM users WHERE username=? AND is_active=1', (normalize(data.username),)).fetchone()
        try:
            verified = hasher.verify(row['password_hash'] if row else DUMMY_HASH, data.password)
            valid = row is not None and verified
        except (VerificationError, InvalidHashError):
            valid = False
        if not valid:
            queue.append(now)
            raise HTTPException(401, '账户或密码错误')
        queue.clear()
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        db.execute('DELETE FROM auth_sessions WHERE expires_at<? OR token_hash=?',
                   (now, digest(request.cookies.get(COOKIE, ''))))
        db.execute('INSERT INTO auth_sessions VALUES(?,?,?,?)', (digest(token), row['id'], csrf, now + 7 * 86400))
        response.set_cookie(COOKIE, token, httponly=True, secure=SECURE, samesite='lax', max_age=7 * 86400, path=COOKIE_PATH)
        return {'id': row['id'], 'username': row['username'], 'role': row['role'], 'csrf_token': csrf}


@app.get('/api/auth/me')
def me(account=Depends(user)):
    return {k: account[k] for k in ('id', 'username', 'role', 'csrf_token')}


@app.post('/api/auth/logout')
def logout(request: Request, response: Response, account=Depends(user)):
    with connect() as db:
        db.execute('DELETE FROM auth_sessions WHERE token_hash=?', (digest(request.cookies.get(COOKIE, '')),))
    response.delete_cookie(COOKIE, path=COOKIE_PATH)
    return {'ok': True}


@app.get('/api/admin/users')
def users(account=Depends(parent)):
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT id,username,role,is_active FROM users WHERE role='child' ORDER BY id")]


@app.post('/api/admin/users')
def create_user(data: Login, account=Depends(parent)):
    name = normalize(data.username)
    if not name:
        raise HTTPException(422, '账户名不能为空')
    try:
        with connect() as db:
            result = db.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,'child')", (name, hasher.hash(data.password)))
            return {'id': result.lastrowid, 'username': name, 'role': 'child', 'is_active': True}
    except sqlite3.IntegrityError:
        raise HTTPException(409, '账户名已存在') from None


@app.patch('/api/admin/users/{uid}')
def patch_user(uid: int, data: UserPatch, account=Depends(parent)):
    with connect() as db:
        if not db.execute("SELECT id FROM users WHERE id=? AND role='child'", (uid,)).fetchone():
            raise HTTPException(404, '孩子账户不存在')
        if data.password is not None:
            db.execute('UPDATE users SET password_hash=? WHERE id=?', (hasher.hash(data.password), uid))
        if data.is_active is not None:
            db.execute('UPDATE users SET is_active=? WHERE id=?', (data.is_active, uid))
        db.execute('DELETE FROM auth_sessions WHERE user_id=?', (uid,))
    return {'ok': True}


@app.get('/api/admin/speech')
def speech_settings(account=Depends(parent)):
    return speech.public(speech.load())


@app.put('/api/admin/speech')
def speech_save(data: speech.Settings, account=Depends(parent)):
    return speech.save(data)


@app.get('/api/speech/settings')
def speech_status(account=Depends(user)):
    settings = speech.load()
    return {'enabled': settings.primary != 'off', 'browser_fallback': settings.browser_fallback}


@app.get('/api/admin/speech/cache')
def speech_cache(account=Depends(parent)):
    return speech.cache_stats()


@app.delete('/api/admin/speech/cache')
def speech_cache_clear(account=Depends(parent)):
    return speech.clear_cache()


class SpeechTestRequest(InputModel):
    provider: str = Field(pattern=r'^(azure|google)$')
    config: speech.Provider


@app.post('/api/admin/speech/test-provider')
async def speech_test_provider(data: SpeechTestRequest, account=Depends(parent)):
    config = data.config.model_copy(deep=True)
    if config.clear_key:
        raise HTTPException(422, '请先填写 API Key，或取消清除密钥')
    if not config.key:
        config.key = getattr(speech.load(), data.provider).key
    if not config.key or not all(c.isascii() and (c.isalnum() or c in '_-') for c in config.key):
        raise HTTPException(422, '请填写有效的 API Key')
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=False) as client:
            audio = await speech.synthesize(client, data.provider, config, 'Hello! Apple, banana, wonderful.')
        if not speech.valid_audio(audio):
            raise ValueError('invalid MP3')
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        raise HTTPException(502, '测试失败，请检查 API Key、区域、音色、额度或服务器网络') from None
    return Response(audio, media_type='audio/mpeg')


@app.post('/api/admin/speech/test')
async def speech_test(account=Depends(parent)):
    return Response(await speech.audio('Hello! Welcome to English practice. Apple, banana, wonderful.'), media_type='audio/mpeg')


class SpeechRequest(InputModel):
    word_id: int = Field(gt=0)
    kind: str = Field(pattern=r'^(word|example)$')


@app.post('/api/speech')
async def speak_word(data: SpeechRequest, account=Depends(user)):
    queue = speech_requests[account['id']]
    now = time.monotonic()
    while queue and queue[0] < now - 60:
        queue.popleft()
    if len(queue) >= 30:
        raise HTTPException(429, '朗读请求过多，请稍后再试')
    queue.append(now)
    with connect() as db:
        row = db.execute('''SELECT w.* FROM words w JOIN decks d ON d.id=w.deck_id
            WHERE w.id=? AND d.user_id=?''', (data.word_id, account['id'])).fetchone()
        if not row:
            raise HTTPException(404, '词条不存在')
        allowed = db.execute('''SELECT 1 FROM study_items i JOIN study_sessions s ON s.id=i.session_id
            LEFT JOIN attempts a ON a.item_id=i.id WHERE i.word_id=? AND s.user_id=?
            AND (i.hint_used=1 OR a.id IS NOT NULL) LIMIT 1''', (data.word_id, account['id'])).fetchone()
        if not allowed:
            raise HTTPException(403, '请先提交答案或查看提示')
        text = row[data.kind]
        if not text.strip():
            raise HTTPException(422, '暂无可朗读内容')
    return Response(await speech.audio(text), media_type='audio/mpeg')


class ImportData(InputModel):
    items: list = Field(min_length=1, max_length=5000)
    user_id: int | None = None
    name: str | None = Field(default=None, max_length=100)


def validate_words(items, reject_duplicates=False):
    result, seen = [], {}
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise HTTPException(422, f'第 {index} 项必须是对象')
        clean = {}
        for key, maximum in [('word', 100), ('meaning', 1000), ('example', 2000), ('example_translation', 2000)]:
            value = item.get(key, '')
            if not isinstance(value, str) or len(value) > maximum:
                raise HTTPException(422, f'第 {index} 项 {key} 类型或长度错误')
            clean[key] = unicodedata.normalize('NFC', value).strip()
        if not clean['word'] or not clean['meaning']:
            raise HTTPException(422, f'第 {index} 项缺少 word 或 meaning')
        key = normalize(clean['word'])
        if key in seen:
            if reject_duplicates:
                raise HTTPException(422, f'重复单词尚未处理：{clean["word"]}，请在预览中选择保留项')
            if seen[key] != clean['meaning']:
                raise HTTPException(422, f'第 {index} 项同词不同释义，请先合并释义：{clean["word"]}')
            continue
        seen[key] = clean['meaning']
        result.append(clean)
    return result


@app.post('/api/decks/import')
def import_deck(data: ImportData, account=Depends(user)):
    target = data.user_id if data.user_id is not None else account['id']
    if target != account['id'] and account['role'] != 'parent':
        raise HTTPException(403, '不能为其他账户导入')
    words = validate_words(data.items, reject_duplicates=True)
    with connect() as db:
        if not db.execute('SELECT id FROM users WHERE id=? AND is_active=1', (target,)).fetchone():
            raise HTTPException(404, '目标账户不可用')
        name = (data.name or '').strip() or datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        cursor = db.execute('INSERT INTO decks(user_id,name,created_at) VALUES(?,?,?)', (target, name, time.time()))
        deck_id = cursor.lastrowid
        db.executemany('INSERT INTO words(deck_id,word,meaning,example,example_translation) VALUES(?,?,?,?,?)',
                       [(deck_id, w['word'], w['meaning'], w['example'], w['example_translation']) for w in words])
    return {'id': deck_id, 'name': name, 'total': len(words)}


@app.get('/api/decks')
def decks(account=Depends(user)):
    with connect() as db:
        return [dict(r) for r in db.execute('''SELECT d.*,count(w.id) total,
            sum(CASE WHEN w.due_at<=? THEN 1 ELSE 0 END) due,
            sum(CASE WHEN w.stage>=0 THEN 1 ELSE 0 END) learned
            FROM decks d LEFT JOIN words w ON w.deck_id=d.id WHERE d.user_id=? GROUP BY d.id ORDER BY d.id DESC''',
            (time.time(), account['id']))]


def session_details(db: sqlite3.Connection, session: sqlite3.Row) -> dict:
    progress = round_progress(db, session['id'], session['round_number'])
    return dict(session) | progress | {'progress': progress}


def active_sessions(db: sqlite3.Connection, user_id: int) -> list[dict]:
    return [session_details(db, row) for row in db.execute('''SELECT s.id,s.deck_id,d.name deck_name,
        s.kind,s.round_number FROM study_sessions s LEFT JOIN decks d ON d.id=s.deck_id
        WHERE s.user_id=? AND s.status='active' ORDER BY s.id''', (user_id,))]


def active_for_deck(db: sqlite3.Connection, user_id: int, deck_id: int) -> sqlite3.Row | None:
    return db.execute('''SELECT s.id FROM study_sessions s WHERE s.user_id=? AND s.status='active'
        AND (s.deck_id=? OR (s.deck_id IS NULL AND EXISTS (
            SELECT 1 FROM study_items i JOIN words w ON w.id=i.word_id
            WHERE i.session_id=s.id AND w.deck_id=?))) ORDER BY s.id LIMIT 1''',
        (user_id, deck_id, deck_id)).fetchone()


AVAILABLE_WORD = '''NOT EXISTS (
    SELECT 1 FROM study_items i JOIN study_sessions s ON s.id=i.session_id
    WHERE i.word_id=w.id AND s.status='active')'''


@app.get('/api/reviews/due')
def due(account=Depends(user)):
    with connect() as db:
        sessions = active_sessions(db, account['id'])
        rows = db.execute(f'''SELECT d.id deck_id,d.name deck_name,count(w.id) count
            FROM decks d LEFT JOIN words w ON w.deck_id=d.id AND w.stage>=0 AND w.due_at<=?
            AND {AVAILABLE_WORD} WHERE d.user_id=? GROUP BY d.id ORDER BY d.id''',
            (time.time(), account['id'])).fetchall()
        decks = []
        for row in rows:
            active = active_for_deck(db, account['id'], row['deck_id'])
            decks.append(dict(row) | {'active_session': next(
                (s for s in sessions if active and s['id'] == active['id']), None)})
        return {'count': sum(row['count'] for row in rows), 'decks': decks, 'sessions': sessions}


def start_study(account: dict, deck_id: int | None = None, deck_ids: list[int] | None = None) -> dict:
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        owned = {row['id'] for row in db.execute('SELECT id FROM decks WHERE user_id=?', (account['id'],))}
        selected = [deck_id] if deck_id is not None else sorted(owned) if deck_ids is None else list(dict.fromkeys(deck_ids))
        if any(did not in owned for did in selected):
            raise HTTPException(404, '词库不存在')
        ids = []
        now = time.time()
        for did in selected:
            active = active_for_deck(db, account['id'], did)
            if active:
                if active['id'] not in ids:
                    ids.append(active['id'])
                continue
            condition = 'w.stage=-1' if deck_id is not None else 'w.stage>=0 AND w.due_at<=?'
            params = (did,) if deck_id is not None else (did, now)
            rows = db.execute(f'''SELECT w.id FROM words w WHERE w.deck_id=? AND {condition}
                AND {AVAILABLE_WORD} ORDER BY w.id''', params).fetchall()
            if not rows:
                continue
            sid = db.execute('INSERT INTO study_sessions(user_id,deck_id,kind) VALUES(?,?,?)',
                             (account['id'], did, 'new' if deck_id is not None else 'review')).lastrowid
            add_round(db, sid, 1, [row['id'] for row in rows])
            if deck_id is not None:
                db.execute('UPDATE decks SET started_at=COALESCE(started_at,?) WHERE id=?', (now, did))
            ids.append(sid)
        if not ids:
            raise HTTPException(409, '没有待学习单词，请等待复习到期')
        sessions = {s['id']: s for s in active_sessions(db, account['id'])}
        return {'id': ids[0], 'sessions': [sessions[sid] for sid in ids]}


@app.post('/api/decks/{deck_id}/study')
def start_deck(deck_id: int, account=Depends(user)):
    return start_study(account, deck_id)


class ReviewSelection(InputModel):
    deck_ids: list[int] = Field(min_length=1)


@app.post('/api/reviews/study')
def start_review(data: ReviewSelection | None = None, account=Depends(user)):
    return start_study(account, deck_ids=data.deck_ids if data is not None else None)


def owned_session(db, sid, account):
    session = db.execute('SELECT * FROM study_sessions WHERE id=? AND user_id=?', (sid, account['id'])).fetchone()
    if not session:
        raise HTTPException(404, '学习批次不存在')
    if session['status'] == 'cancelled':
        raise HTTPException(409, '词库已修改，该批次已结束，请返回词库重新开始')
    return session


def round_progress(db: sqlite3.Connection, sid: int, number: int) -> dict:
    row = db.execute('''SELECT count(*) total, count(a.id) answered,
        coalesce(sum(a.correct),0) correct FROM study_items i
        LEFT JOIN attempts a ON a.item_id=i.id
        WHERE i.session_id=? AND i.round_number=?''', (sid, number)).fetchone()
    return dict(row) | {'remaining': row['total'] - row['answered']}


@app.get('/api/study/active')
def active_study(account=Depends(user)):
    with connect() as db:
        sessions = active_sessions(db, account['id'])
        return {'sessions': sessions, 'session': sessions[0] if sessions else None}


@app.get('/api/study/{sid}/next')
def next_question(sid: int, account=Depends(user)):
    with connect() as db:
        session = owned_session(db, sid, account)
        item = current_item(db, sid)
        if not item:
            row = db.execute('''SELECT count(*) attempts,coalesce(sum(a.correct),0) correct FROM attempts a
                JOIN study_items i ON i.id=a.item_id WHERE i.session_id=?''', (sid,)).fetchone()
            return {'complete': True, 'summary': dict(row)}
        return {'complete': False, 'item': {'id': item['id'], 'meaning': item['meaning'],
                'round_number': session['round_number'], 'practice_pending': bool(item['practice_pending']),
                **round_progress(db, sid, session['round_number'])}}


@app.get('/api/study/{sid}/rounds/{number}')
def round_report(sid: int, number: int, account=Depends(user)):
    with connect() as db:
        session = owned_session(db, sid, account)
        if number < 1 or number > session['round_number'] or (session['status'] == 'active' and number == session['round_number']):
            raise HTTPException(409, '本轮尚未结算')
        rows = db.execute('''SELECT w.id word_id,w.word,w.meaning,w.stage,w.due_at,a.correct,
            i.hint_used FROM study_items i JOIN words w ON w.id=i.word_id
            LEFT JOIN attempts a ON a.item_id=i.id WHERE i.session_id=? AND i.round_number=?
            ORDER BY i.position''', (sid, number)).fetchall()
        words = [dict(row) | {'next_round_probability': 0 if row['correct'] == 1 else 100,
                             'evaluation': '独立答对' if row['correct'] == 1 else ('未答，不计分' if row['correct'] is None else '未独立答对')}
                 for row in rows]
        return {'round_number': number, 'total': len(words),
                'correct': sum(row['correct'] == 1 for row in rows),
                'wrong': sum(row['correct'] == 0 for row in rows),
                'unanswered': sum(row['correct'] is None for row in rows), 'words': words}


class RoundAction(InputModel):
    round_number: int = Field(ge=1)
    action: str


@app.post('/api/study/{sid}/round-action')
def round_action(sid: int, data: RoundAction, account=Depends(user)):
    if data.action not in ('interrupt', 'finish'):
        raise HTTPException(422, '不支持的轮次操作')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        session = owned_session(db, sid, account)
        if session['status'] != 'active' or session['round_number'] != data.round_number:
            raise HTTPException(409, '轮次已变化，请刷新')
        if db.execute('SELECT 1 FROM study_items WHERE session_id=? AND practice_pending=1', (sid,)).fetchone():
            raise HTTPException(409, '请先完成当前词的巩固拼写')
        if data.action == 'interrupt':
            db.execute('''DELETE FROM attempts WHERE item_id IN
                (SELECT id FROM study_items WHERE session_id=? AND round_number=?)''', (sid, data.round_number))
            db.execute('UPDATE study_items SET hint_used=0 WHERE session_id=? AND round_number=?', (sid, data.round_number))
        else:
            finish_round(db, session, time.time())
        return {'ok': True}


class Answer(InputModel):
    answer: str = Field(default='', max_length=200)
    skip: bool = False
    practice: bool = False


def get_item(db, sid, iid):
    return db.execute('''SELECT i.*,w.word,w.meaning,w.example,w.example_translation,w.audio,w.phonetic,w.stage
        FROM study_items i JOIN words w ON w.id=i.word_id WHERE i.id=? AND i.session_id=?''', (iid, sid)).fetchone()


@app.post('/api/study/{sid}/items/{iid}/reveal')
def reveal(sid: int, iid: int, account=Depends(user)):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        owned_session(db, sid, account)
        item = current_item(db, sid)
        if not item or item['id'] != iid:
            raise HTTPException(409, '题目已变化，请刷新')
        db.execute('UPDATE study_items SET hint_used=1 WHERE id=?', (iid,))
        return content(item)


@app.post('/api/study/{sid}/items/{iid}/answer')
def answer(sid: int, iid: int, data: Answer, account=Depends(user)):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        session = owned_session(db, sid, account)
        item = get_item(db, sid, iid)
        if not item:
            raise HTTPException(404, '题目不存在')
        already = db.execute('SELECT id FROM attempts WHERE item_id=?', (iid,)).fetchone()
        current = current_item(db, sid)
        if not already and (not current or current['id'] != iid):
            raise HTTPException(409, '请按当前题目顺序提交')
        if not data.answer.strip() and not data.skip:
            raise HTTPException(422, '请输入答案，或点击不会')
        if data.practice and not already:
            raise HTTPException(409, '当前题尚未进入巩固')
        return submit(db, session, item, data.answer, data.skip, time.time(), data.practice)


class ExamplePatch(InputModel):
    meaning: str | None = Field(default=None, min_length=1, max_length=1000)
    example: str | None = Field(default=None, max_length=2000)
    example_translation: str | None = Field(default=None, max_length=2000)


def owned_word(db, wid, account):
    row = db.execute('SELECT w.* FROM words w JOIN decks d ON d.id=w.deck_id WHERE w.id=? AND d.user_id=?',
                     (wid, account['id'])).fetchone()
    if not row:
        raise HTTPException(404, '词条不存在')
    return row


def cancel_word_sessions(db, word_ids):
    for wid in word_ids:
        db.execute('''UPDATE study_sessions SET status='cancelled' WHERE status='active' AND id IN
            (SELECT session_id FROM study_items WHERE word_id=?)''', (wid,))


@app.patch('/api/words/{wid}')
def update_example(wid: int, data: ExamplePatch, account=Depends(user)):
    fields = data.model_dump(exclude_unset=True)
    if not fields or any(value is None for value in fields.values()):
        raise HTTPException(422, '请提供要修改的字段，不允许 null')
    fields = {key: unicodedata.normalize('NFC', value).strip() for key, value in fields.items()}
    if 'meaning' in fields and not fields['meaning']:
        raise HTTPException(422, '释义不能为空')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        owned_word(db, wid, account)
        if 'meaning' in fields:
            cancel_word_sessions(db, [wid])
        db.execute('UPDATE words SET ' + ','.join(key + '=?' for key in fields) + ' WHERE id=?',
                   (*fields.values(), wid))
    return {'ok': True}


@app.post('/api/words/{wid}/enrich')
async def enrich(wid: int, account=Depends(user)):
    with connect() as db:
        row = db.execute('''SELECT w.* FROM words w JOIN decks d ON d.id=w.deck_id
            JOIN users u ON u.id=d.user_id WHERE w.id=? AND d.user_id=? AND u.is_active=1''',
                         (wid, account['id'])).fetchone()
        if not row:
            raise HTTPException(404, '词条不存在')
        # ponytail: only revealed or previously answered words; expand for parent editing later.
        allowed = db.execute('''SELECT 1 FROM study_items i JOIN study_sessions s ON s.id=i.session_id
            LEFT JOIN attempts a ON a.item_id=i.id WHERE i.word_id=? AND s.user_id=?
            AND (i.hint_used=1 OR a.id IS NOT NULL) LIMIT 1''', (wid, account['id'])).fetchone()
        if not allowed:
            raise HTTPException(403, '请先提交答案或查看提示')
        cached = db.execute('SELECT payload FROM dictionary_cache WHERE word=? AND fetched_at>?',
                            (normalize(row['word']), time.time() - 7 * 86400)).fetchone()
    if cached:
        payload = json.loads(cached['payload'])
    else:
        try:
            async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
                async with client.stream('GET', 'https://api.dictionaryapi.dev/api/v2/entries/en/' + quote(row['word'], safe='')) as response:
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 1024 * 1024:
                            raise ValueError('词典响应过大')
                    payload = json.loads(body)
            if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
                raise ValueError('词典响应格式错误')
        except (httpx.HTTPError, ValueError):
            raise HTTPException(502, '词典暂不可用或查无此词；仍可学习，可手工补充例句')
        with connect() as db:
            db.execute('INSERT OR REPLACE INTO dictionary_cache VALUES(?,?,?)',
                       (normalize(row['word']), json.dumps(payload), time.time()))
    entry = payload[0]
    audio = ''
    phonetic = str(entry.get('phonetic', ''))[:200]
    phonetics = entry.get('phonetics', [])
    for part in phonetics if isinstance(phonetics, list) else []:
        if not isinstance(part, dict):
            continue
        candidate = part.get('audio', '')
        if isinstance(candidate, str):
            if candidate.startswith('//'):
                candidate = 'https:' + candidate
            parsed = urlsplit(candidate)
            if parsed.scheme == 'https' and parsed.hostname in ('api.dictionaryapi.dev', 'ssl.gstatic.com'):
                audio = candidate
                break
    example = ''
    definitions_text = []
    meanings = entry.get('meanings', [])
    for meaning in meanings if isinstance(meanings, list) else []:
        if not isinstance(meaning, dict):
            continue
        definitions = meaning.get('definitions', [])
        for definition in definitions if isinstance(definitions, list) else []:
            if not isinstance(definition, dict):
                continue
            description = definition.get('definition')
            if isinstance(description, str) and len(definitions_text) < 5:
                definitions_text.append(description[:1000])
            value = definition.get('example')
            if not example and isinstance(value, str) and value.strip():
                example = value[:2000]
    with connect() as db:
        if not db.execute('SELECT w.id FROM words w JOIN decks d ON d.id=w.deck_id WHERE w.id=? AND d.user_id=?',
                          (wid, account['id'])).fetchone():
            raise HTTPException(404, '词条不存在')
        db.execute("UPDATE words SET audio=?,phonetic=?,example=CASE WHEN example='' AND example_translation='' THEN ? ELSE example END WHERE id=?",
                   (audio, phonetic, example, wid))
        updated = db.execute('SELECT *,id word_id FROM words WHERE id=?', (wid,)).fetchone()
        return content(updated) | {'definitions': definitions_text, 'source': 'Free Dictionary API', 'notice': '中文译文需导入或手工补充；发音可用性取决于音频和设备英语语音包'}


from .management import register as register_management
register_management(app, user, owned_word, cancel_word_sessions)

from .database_admin import register as register_database_admin
register_database_admin(app, parent, hasher)

from .user_backup import register as register_user_backup
register_user_backup(app, user, hasher)


class PreviewData(InputModel):
    items: list = Field(min_length=1, max_length=5000)


@app.post('/api/import/preview')
def preview(data: PreviewData, account=Depends(user)):
    items = []
    for index, item in enumerate(data.items, 1):
        try:
            items.append(validate_words([item])[0])
        except HTTPException as exc:
            raise HTTPException(422, f'第 {index} 行：{exc.detail}') from None
    groups = {}
    for index, item in enumerate(items):
        groups.setdefault(normalize(item['word']), []).append(index)
    return {'items': items, 'duplicates': [
        {'word': items[indices[0]]['word'], 'indices': indices}
        for indices in groups.values() if len(indices) > 1
    ]}


@app.post('/api/import/xlsx')
async def preview_xlsx(request: Request, account=Depends(user)):
    from .import_sources import parse_xlsx
    return parse_xlsx(await request.body())


class DriveData(InputModel):
    url: str = Field(min_length=1, max_length=2048)


@app.post('/api/import/drive')
async def preview_drive(data: DriveData, account=Depends(user)):
    from .import_sources import parse_xlsx, read_drive
    body = await read_drive(data.url, allow_private=account['role'] == 'parent')
    return parse_xlsx(body)


@app.get('/api/health')
def health():
    return {'ok': True}


DIST = ROOT / 'frontend' / 'dist'
if (DIST / 'assets').exists():
    app.mount('/assets', StaticFiles(directory=DIST / 'assets'), name='assets')


@app.get('/{path:path}')
def frontend(path: str):
    if path.startswith('api/') or path.startswith('assets/') or '.' in path.rsplit('/', 1)[-1]:
        raise HTTPException(404, '资源不存在')
    if not (DIST / 'index.html').exists():
        raise HTTPException(503, '请先构建 frontend，或使用 Vite 开发服务')
    return FileResponse(DIST / 'index.html')
