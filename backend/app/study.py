import random
import unicodedata

from .fsrs_schedule import review

INTERVALS = (600, 86400, 3 * 86400, 7 * 86400, 14 * 86400, 30 * 86400)


def normalize(value):
    return unicodedata.normalize('NFC', value).strip().casefold()


def add_round(db, session_id, round_number, word_ids):
    random.shuffle(word_ids)
    db.executemany(
        'INSERT INTO study_items(session_id,word_id,round_number,position) VALUES(?,?,?,?)',
        [(session_id, word_id, round_number, pos) for pos, word_id in enumerate(word_ids)],
    )


def current_item(db, session_id):
    return db.execute('''SELECT i.*,w.word,w.meaning,w.example,w.example_translation,w.audio,w.phonetic,w.stage
        FROM study_items i JOIN words w ON w.id=i.word_id
        JOIN study_sessions s ON s.id=i.session_id
        LEFT JOIN attempts a ON a.item_id=i.id
        WHERE i.session_id=? AND s.status='active' AND i.round_number=s.round_number
        AND (a.id IS NULL OR i.practice_pending=1) ORDER BY i.position LIMIT 1''',
        (session_id,)).fetchone()


def content(row):
    return {key: row[key] for key in ('word', 'meaning', 'example', 'example_translation', 'audio', 'phonetic')} | {'word_id': row['word_id']}


def record_memory(db, word_id: int, correct: bool, now: float, effort: str = 'normal') -> None:
    word = db.execute('SELECT * FROM words WHERE id=?', (word_id,)).fetchone()
    card = review(word['fsrs_card'], word_id, correct, effort, now, word['last_seen_at'])
    if card is None:
        # Legacy/no-model exposure is not evidence for initializing Good parameters.
        due = word['due_at'] if word['due_at'] is not None else now + 600
        db.execute('UPDATE words SET stage=max(stage,0),due_at=?,last_seen_at=?,last_success_at=? WHERE id=?',
                   (due, now, now, word_id))
        return
    delay = (card.due - card.last_review).total_seconds()
    # stage remains a display/learned compatibility field, never input to FSRS.
    stage = -1 if word['stage'] < 0 and not correct else max(0, max(
        (index for index, interval in enumerate(INTERVALS) if interval <= delay), default=0))
    db.execute('UPDATE words SET stage=?,due_at=?,memory_interval=?,last_seen_at=?,last_success_at=CASE WHEN ? THEN ? ELSE last_success_at END,fsrs_card=? WHERE id=?',
        (stage, card.due.timestamp(), delay, now, int(correct), now, card.to_json(), word_id))


def submit(db, session, item, answer, skip, now, practice=False, effort='normal'):
    previous = db.execute('SELECT * FROM attempts WHERE item_id=?', (item['id'],)).fetchone()
    matched = not skip and normalize(answer) == normalize(item['word'])
    if previous:
        if practice and item['practice_pending']:
            db.execute('UPDATE words SET last_seen_at=? WHERE id=?', (now, item['word_id']))
        # Only explicit practice requests can finish practice; replaying first answer cannot.
        if practice and item['practice_pending'] and matched:
            db.execute('UPDATE study_items SET practice_pending=0 WHERE id=?', (item['id'],))
        correct = bool(previous['correct'])
    else:
        correct = matched and not item['hint_used']
        hard_used = db.execute('''SELECT 1 FROM attempts a JOIN study_items i ON i.id=a.item_id
            WHERE i.session_id=? AND i.word_id=? AND a.effort='hard' AND a.correct=1 LIMIT 1''',
            (session['id'], item['word_id'])).fetchone() is not None
        retry = not correct or (effort == 'hard' and not hard_used)
        seen = db.execute('SELECT last_seen_at FROM words WHERE id=?', (item['word_id'],)).fetchone()[0]
        delayed = not item['hint_used'] and seen is not None and now - seen >= 600
        db.execute('INSERT INTO attempts(item_id,answer,correct,created_at,effort,needs_retry,delayed_recall) VALUES(?,?,?,?,?,?,?)',
                   (item['id'], answer, int(correct), now, effort, int(retry), int(delayed)))
        record_memory(db, item['word_id'], correct, now, effort)
        db.execute('UPDATE study_items SET practice_pending=? WHERE id=?', (int(not correct), item['id']))
    if (session['status'] == 'active' and item['round_number'] == session['round_number']
            and current_item(db, session['id']) is None):
        finish_round(db, session, now)
    pending = bool(db.execute('SELECT practice_pending FROM study_items WHERE id=?', (item['id'],)).fetchone()[0])
    return content(item) | {'correct': bool(correct), 'practice_pending': pending,
                            'practice': bool(previous), 'spelling_correct': bool(matched),
                            'complete': current_item(db, session['id']) is None,
                            'round_finished': current_item(db, session['id']) is None or db.execute(
                                'SELECT round_number FROM study_sessions WHERE id=?', (session['id'],)).fetchone()[0] != item['round_number'],
                            'round_number': item['round_number']}


def finish_round(db, session, now):
    rows = db.execute('''SELECT i.*,a.correct,a.needs_retry FROM study_items i
        LEFT JOIN attempts a ON a.item_id=i.id WHERE i.session_id=? AND i.round_number=?
        ORDER BY i.position''', (session['id'], session['round_number'])).fetchall()
    failures = [row['word_id'] for row in rows if row['correct'] is None or row['needs_retry']]
    # Reconsider due cards only between rounds. Multi-deck tasks keep their original word set.
    if session['deck_id'] is not None:
        due = db.execute('SELECT id FROM words WHERE deck_id=? AND stage>=0 AND due_at<=? AND (last_seen_at IS NULL OR last_seen_at<=?)',
                         (session['deck_id'], now, now - 600)).fetchall()
    else:
        due = db.execute('''SELECT DISTINCT w.id FROM words w JOIN study_items i ON i.word_id=w.id
            WHERE i.session_id=? AND i.round_number=1 AND w.stage>=0 AND w.due_at<=? AND (w.last_seen_at IS NULL OR w.last_seen_at<=?)''',
                         (session['id'], now, now - 600)).fetchall()
    failures = list(dict.fromkeys(failures + [row['id'] for row in due]))
    if failures:
        number = session['round_number'] + 1
        add_round(db, session['id'], number, failures)
        db.execute('UPDATE study_sessions SET round_number=? WHERE id=?', (number, session['id']))
    else:
        db.execute("UPDATE study_sessions SET status='complete' WHERE id=?", (session['id'],))
