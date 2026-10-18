"""词库管理；注册函数复用现有认证依赖，避免循环导入。"""
import json
from fastapi import Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from .db import connect
from .study import INTERVALS


def register(app, user, owned_word, cancel_word_sessions):
    def owned_deck(db, did, account):
        row = db.execute('SELECT * FROM decks WHERE id=? AND user_id=?', (did, account['id'])).fetchone()
        if not row:
            raise HTTPException(404, '词库不存在')
        return row

    def remove_word(db, wid):
        cancel_word_sessions(db, [wid])
        db.execute('DELETE FROM attempts WHERE item_id IN (SELECT id FROM study_items WHERE word_id=?)', (wid,))
        db.execute('DELETE FROM study_items WHERE word_id=?', (wid,))
        db.execute('DELETE FROM words WHERE id=?', (wid,))

    class Rename(BaseModel):
        model_config = ConfigDict(strict=True, extra='forbid')
        name: str = Field(min_length=1, max_length=100)

    @app.get('/api/decks/{did}')
    def details(did: int, account=Depends(user)):
        with connect() as db:
            deck = dict(owned_deck(db, did, account))
            deck['words'] = [dict(row) for row in db.execute('''SELECT w.*,
                count(a.id) attempts,coalesce(sum(a.correct),0) correct,max(a.created_at) last_answer_at
                FROM words w LEFT JOIN study_items i ON i.word_id=w.id
                LEFT JOIN attempts a ON a.item_id=i.id WHERE w.deck_id=? GROUP BY w.id ORDER BY w.id''', (did,))]
            return deck

    @app.patch('/api/decks/{did}')
    def rename(did: int, data: Rename, account=Depends(user)):
        if not data.name.strip():
            raise HTTPException(422, '词库名不能为空')
        with connect() as db:
            owned_deck(db, did, account)
            db.execute('UPDATE decks SET name=? WHERE id=?', (data.name.strip(), did))
        return {'ok': True}

    @app.get('/api/decks/{did}/export')
    def export(did: int, account=Depends(user)):
        with connect() as db:
            owned_deck(db, did, account)
            rows = [dict(row) for row in db.execute('SELECT word,meaning,example,example_translation FROM words WHERE deck_id=? ORDER BY id', (did,))]
        return Response(json.dumps(rows, ensure_ascii=False, indent=2), media_type='application/json',
                        headers={'Content-Disposition': f'attachment; filename="deck-{did}.json"'})

    @app.delete('/api/decks/{did}')
    def delete_deck(did: int, account=Depends(user)):
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            owned_deck(db, did, account)
            ids = [r[0] for r in db.execute('SELECT id FROM words WHERE deck_id=?', (did,))]
            for wid in ids:
                remove_word(db, wid)
            db.execute('UPDATE study_sessions SET deck_id=NULL WHERE deck_id=?', (did,))
            db.execute('DELETE FROM decks WHERE id=?', (did,))
        return {'ok': True}

    @app.delete('/api/words/{wid}')
    def delete_word(wid: int, account=Depends(user)):
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            owned_word(db, wid, account)
            remove_word(db, wid)
        return {'ok': True}

    @app.post('/api/words/{wid}/reset')
    def reset_word(wid: int, account=Depends(user)):
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            owned_word(db, wid, account)
            cancel_word_sessions(db, [wid])
            db.execute('DELETE FROM attempts WHERE item_id IN (SELECT id FROM study_items WHERE word_id=?)', (wid,))
            db.execute('DELETE FROM study_items WHERE word_id=?', (wid,))
            db.execute('UPDATE words SET stage=-1,due_at=NULL WHERE id=?', (wid,))
        return {'ok': True}

    @app.get('/api/words/{wid}/history')
    def history(wid: int, account=Depends(user)):
        with connect() as db:
            word = owned_word(db, wid, account)
            attempts = [dict(row) for row in db.execute('''SELECT a.correct,a.created_at,a.answer,i.round_number
                FROM attempts a JOIN study_items i ON i.id=a.item_id WHERE i.word_id=? ORDER BY a.created_at,a.id''', (wid,))]
            return {'stage': word['stage'], 'due_at': word['due_at'], 'intervals': INTERVALS, 'attempts': attempts}
