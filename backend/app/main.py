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

from .db import ROOT, connect, initialize
from .study import add_round, content, current_item, normalize, submit

hasher = PasswordHasher()
DUMMY_HASH = hasher.hash(secrets.token_urlsafe(24))
failures = defaultdict(deque)
COOKIE = 'words_session'
SECURE = os.environ.get('WORDS_HTTPS', '0') == '1'


@asynccontextmanager
async def lifespan(app):
    initialize()
    yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=[
    host.strip() for host in os.environ.get('WORDS_HOSTS', 'localhost,127.0.0.1,testserver').split(',') if host.strip()
])


@app.middleware('http')
async def limits(request, call_next):
    if request.url.path.startswith('/api/'):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 2 * 1024 * 1024:
                return Response('请求超过 2 MiB', status_code=413)
        request._body = bytes(body)
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            if origin and urlsplit(origin).netloc != request.headers.get('host'):
                return Response('来源不允许', status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    if request.url.path.startswith('/api/'):
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
        response.set_cookie(COOKIE, token, httponly=True, secure=SECURE, samesite='lax', max_age=7 * 86400, path='/')
        return {'id': row['id'], 'username': row['username'], 'role': row['role'], 'csrf_token': csrf}


@app.get('/api/auth/me')
def me(account=Depends(user)):
    return {k: account[k] for k in ('id', 'username', 'role', 'csrf_token')}


@app.post('/api/auth/logout')
def logout(request: Request, response: Response, account=Depends(user)):
    with connect() as db:
        db.execute('DELETE FROM auth_sessions WHERE token_hash=?', (digest(request.cookies.get(COOKIE, '')),))
    response.delete_cookie(COOKIE)
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


class ImportData(InputModel):
    items: list = Field(min_length=1, max_length=5000)
    user_id: int | None = None
    name: str | None = Field(default=None, max_length=100)


def validate_words(items):
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
    words = validate_words(data.items)
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
            FROM decks d JOIN words w ON w.deck_id=d.id WHERE d.user_id=? GROUP BY d.id ORDER BY d.id DESC''',
            (time.time(), account['id']))]


@app.get('/api/reviews/due')
def due(account=Depends(user)):
    with connect() as db:
        count = db.execute('''SELECT count(*) FROM words w JOIN decks d ON d.id=w.deck_id
            WHERE d.user_id=? AND w.due_at<=? AND NOT EXISTS (
                SELECT 1 FROM study_items i JOIN study_sessions s ON s.id=i.session_id
                WHERE i.word_id=w.id AND s.status='active'
            )''', (account['id'], time.time())).fetchone()[0]
        return {'count': count}


def start_study(account, deck_id=None):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if deck_id is not None and not db.execute('SELECT id FROM decks WHERE id=? AND user_id=?', (deck_id, account['id'])).fetchone():
            raise HTTPException(404, '词库不存在')
        active = db.execute("SELECT id FROM study_sessions WHERE user_id=? AND status='active'", (account['id'],)).fetchone()
        if active:
            return {'id': active['id']}
        if deck_id is not None:
            rows = db.execute('SELECT id FROM words WHERE deck_id=? AND stage=-1', (deck_id,)).fetchall()
        else:
            rows = db.execute('''SELECT w.id FROM words w JOIN decks d ON d.id=w.deck_id
                WHERE d.user_id=? AND w.due_at<=?''', (account['id'], time.time())).fetchall()
        if not rows:
            raise HTTPException(409, '没有待学习单词，请等待复习到期')
        cursor = db.execute('INSERT INTO study_sessions(user_id,deck_id,kind) VALUES(?,?,?)',
                            (account['id'], deck_id, 'new' if deck_id is not None else 'review'))
        sid = cursor.lastrowid
        add_round(db, sid, 1, [r['id'] for r in rows])
        if deck_id is not None:
            db.execute('UPDATE decks SET started_at=COALESCE(started_at,?) WHERE id=?', (time.time(), deck_id))
        return {'id': sid}


@app.post('/api/decks/{deck_id}/study')
def start_deck(deck_id: int, account=Depends(user)):
    return start_study(account, deck_id)


@app.post('/api/reviews/study')
def start_review(account=Depends(user)):
    return start_study(account)


def owned_session(db, sid, account):
    session = db.execute('SELECT * FROM study_sessions WHERE id=? AND user_id=?', (sid, account['id'])).fetchone()
    if not session:
        raise HTTPException(404, '学习批次不存在')
    return session


@app.get('/api/study/{sid}/next')
def next_question(sid: int, account=Depends(user)):
    with connect() as db:
        session = owned_session(db, sid, account)
        item = current_item(db, sid)
        if not item:
            row = db.execute('''SELECT count(*) attempts,coalesce(sum(a.correct),0) correct FROM attempts a
                JOIN study_items i ON i.id=a.item_id WHERE i.session_id=?''', (sid,)).fetchone()
            return {'complete': True, 'summary': dict(row)}
        remaining = db.execute('''SELECT count(*) FROM study_items i LEFT JOIN attempts a ON a.item_id=i.id
            WHERE i.session_id=? AND a.id IS NULL''', (sid,)).fetchone()[0]
        return {'complete': False, 'item': {'id': item['id'], 'meaning': item['meaning'],
                'round_number': session['round_number'], 'remaining': remaining}}


class Answer(InputModel):
    answer: str = Field(default='', max_length=200)
    skip: bool = False


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
        return submit(db, session, item, data.answer, data.skip, time.time())


class ExamplePatch(InputModel):
    example: str = Field(min_length=1, max_length=2000)
    example_translation: str = Field(min_length=1, max_length=2000)


@app.patch('/api/words/{wid}')
def update_example(wid: int, data: ExamplePatch, account=Depends(user)):
    with connect() as db:
        row = db.execute('SELECT d.user_id FROM words w JOIN decks d ON d.id=w.deck_id WHERE w.id=?', (wid,)).fetchone()
        if not row or (row['user_id'] != account['id'] and account['role'] != 'parent'):
            raise HTTPException(404, '词条不存在')
        if not data.example.strip() or not data.example_translation.strip():
            raise HTTPException(422, '例句和译文不能为空')
        db.execute('UPDATE words SET example=?,example_translation=? WHERE id=?',
                   (data.example.strip(), data.example_translation.strip(), wid))
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
