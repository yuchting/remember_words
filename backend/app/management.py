"""词库管理；注册函数复用现有认证依赖，避免循环导入。"""
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Literal
from fastapi import Depends, HTTPException, Query, Response
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

    @app.get('/api/statistics')
    def statistics(deck_id: int | None = None, period: Literal['7', '30', 'all'] = Query('7', alias='range'),
                   account=Depends(user)) -> dict:
        now = time.time()
        today = datetime.fromtimestamp(now, timezone.utc).date()
        start = None if period == 'all' else datetime.combine(
            today - timedelta(days=int(period) - 1), datetime.min.time(), timezone.utc).timestamp()
        with connect() as db:
            db.execute('BEGIN')
            if deck_id is not None:
                owned_deck(db, deck_id, account)
            scope = 'd.user_id=?' + (' AND d.id=?' if deck_id is not None else '')
            params = (account['id'],) if deck_id is None else (account['id'], deck_id)
            decks = [dict(row) for row in db.execute(f'''SELECT d.id,d.name,count(w.id) total,
                coalesce(sum(w.stage>=0),0) passed,
                coalesce(sum(w.stage>=0 AND w.due_at<=?),0) due
                FROM decks d LEFT JOIN words w ON w.deck_id=d.id
                WHERE {scope} GROUP BY d.id ORDER BY d.id''', (now, *params))]
            by_id = {deck['id']: deck for deck in decks}
            totals = {'total': 0, 'passed': 0, 'due': 0,
                      'stages': {str(stage): 0 for stage in range(-1, len(INTERVALS))}}
            for deck in decks:
                deck['stages'] = {str(stage): 0 for stage in range(-1, len(INTERVALS))}
                for key in ('total', 'passed', 'due'):
                    totals[key] += deck[key]
            for row in db.execute(f'''SELECT w.deck_id,w.stage,count(*) count FROM words w
                    JOIN decks d ON d.id=w.deck_id WHERE {scope} GROUP BY w.deck_id,w.stage''', params):
                by_id[row['deck_id']]['stages'][str(row['stage'])] = row['count']
                totals['stages'][str(row['stage'])] += row['count']
            # Only persisted first answers count; spelling practice never adds attempts.
            history = f'''WITH history AS (SELECT a.correct,a.created_at,a.delayed_recall,i.word_id,i.round_number,
                s.kind,w.word,w.meaning,d.id deck_id,d.name deck_name
                FROM attempts a JOIN study_items i ON i.id=a.item_id
                JOIN study_sessions s ON s.id=i.session_id JOIN words w ON w.id=i.word_id
                JOIN decks d ON d.id=w.deck_id WHERE {scope} AND s.user_id=?
                AND a.created_at<=? AND (? IS NULL OR a.created_at>=?)) '''
            history_params = (*params, account['id'], now, start, start)
            mistakes = [dict(row) for row in db.execute(history + '''SELECT word_id,word,meaning,
                deck_id,deck_name,count(*) attempts,sum(correct=0) wrong,
                1.0*sum(correct=0)/count(*) error_rate,
                max(CASE WHEN correct=0 THEN created_at END) last_wrong_at
                FROM history GROUP BY word_id HAVING wrong>0
                ORDER BY wrong DESC,last_wrong_at DESC,word_id LIMIT 50''', history_params)]
            first_round = {kind: {'attempts': 0, 'correct': 0, 'accuracy': None} for kind in ('new', 'review')}
            for row in db.execute(history + '''SELECT kind,count(*) attempts,sum(correct) correct
                    FROM history WHERE round_number=1 GROUP BY kind''', history_params):
                first_round[row['kind']] = {'attempts': row['attempts'], 'correct': row['correct'],
                                             'accuracy': row['correct'] / row['attempts']}
            delayed = db.execute(history + '''SELECT count(*) attempts,coalesce(sum(correct),0) correct
                FROM history WHERE delayed_recall=1''', history_params).fetchone()
            delayed_recall = dict(delayed) | {'accuracy': delayed['correct'] / delayed['attempts'] if delayed['attempts'] else None}
            daily_by_date = {row['date']: dict(row) for row in db.execute(history + '''SELECT
                date(created_at,'unixepoch') date,count(*) attempts,sum(correct) correct
                FROM history GROUP BY date ORDER BY date''', history_params)}
            first_day = (datetime.fromtimestamp(start, timezone.utc).date() if start is not None
                         else min((datetime.fromisoformat(day).date() for day in daily_by_date), default=today))
            daily = []
            for offset in range((today - first_day).days + 1):
                day = (first_day + timedelta(days=offset)).isoformat()
                daily.append(daily_by_date.get(day, {'date': day, 'attempts': 0, 'correct': 0})
                             | {'new_passed': None})
            midnight = datetime.combine(today, datetime.min.time(), timezone.utc).timestamp()
            due_counts = {row['date']: row['count'] for row in db.execute(f'''SELECT
                date(w.due_at,'unixepoch') date,count(*) count FROM words w
                JOIN decks d ON d.id=w.deck_id WHERE {scope} AND w.stage>=0
                AND w.due_at>=? AND w.due_at<? GROUP BY date''', (*params, midnight, midnight + 7 * 86400))}
        return {'deck_id': deck_id, 'range': period, 'timezone': 'UTC', 'generated_at': now,
                'since': start, 'decks': decks, 'totals': totals, 'mistakes': mistakes, 'delayed_recall': delayed_recall,
                'first_round': first_round, 'daily': daily,
                'new_passed_available': False,
                'new_passed_note': '历史未记录过关时间及阶段变更，无法可靠推导每日新过关数',
                'upcoming_due': [{'date': (today + timedelta(days=offset)).isoformat(),
                                  'count': due_counts.get((today + timedelta(days=offset)).isoformat(), 0)}
                                 for offset in range(7)]}

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
            for session in db.execute('SELECT id,scope_deck_ids,status FROM study_sessions WHERE user_id=?', (account['id'],)).fetchall():
                scope = json.loads(session['scope_deck_ids'])
                if did in scope:
                    db.execute('UPDATE study_sessions SET scope_deck_ids=?,status=? WHERE id=?',
                               (json.dumps([key for key in scope if key != did]),
                                'cancelled' if session['status'] == 'active' else session['status'], session['id']))
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
            db.execute('UPDATE words SET stage=-1,due_at=NULL,last_seen_at=NULL,last_success_at=NULL,memory_interval=0 WHERE id=?', (wid,))
        return {'ok': True}

    @app.get('/api/words/{wid}/history')
    def history(wid: int, account=Depends(user)):
        with connect() as db:
            word = owned_word(db, wid, account)
            attempts = [dict(row) for row in db.execute('''SELECT a.correct,a.created_at,a.answer,i.round_number
                FROM attempts a JOIN study_items i ON i.id=a.item_id WHERE i.word_id=? ORDER BY a.created_at,a.id''', (wid,))]
            return {'stage': word['stage'], 'due_at': word['due_at'], 'intervals': INTERVALS, 'attempts': attempts}
