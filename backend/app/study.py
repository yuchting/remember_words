import random
import unicodedata

INTERVALS = (600, 86400, 3 * 86400, 7 * 86400, 14 * 86400, 30 * 86400)


def normalize(value):
    return unicodedata.normalize('NFC', value).strip().casefold()


def schedule(stage, failed, now):
    next_stage = 0 if failed else min(stage + 1, len(INTERVALS) - 1)
    return next_stage, now + INTERVALS[next_stage]


def add_round(db, session_id, round_number, word_ids):
    random.shuffle(word_ids)
    db.executemany(
        'INSERT INTO study_items(session_id,word_id,round_number,position) VALUES(?,?,?,?)',
        [(session_id, word_id, round_number, pos) for pos, word_id in enumerate(word_ids)],
    )


def current_item(db, session_id):
    return db.execute('''SELECT i.*,w.word,w.meaning,w.example,w.example_translation,w.audio,w.phonetic,w.stage
        FROM study_items i JOIN words w ON w.id=i.word_id
        LEFT JOIN attempts a ON a.item_id=i.id
        WHERE i.session_id=? AND a.id IS NULL ORDER BY i.round_number,i.position LIMIT 1''',
        (session_id,)).fetchone()


def content(row):
    return {key: row[key] for key in ('word', 'meaning', 'example', 'example_translation', 'audio', 'phonetic')} | {'word_id': row['word_id']}


def submit(db, session, item, answer, skip, now):
    previous = db.execute('SELECT * FROM attempts WHERE item_id=?', (item['id'],)).fetchone()
    if previous:
        return content(item) | {'correct': bool(previous['correct'])}
    correct = not skip and not item['hint_used'] and normalize(answer) == normalize(item['word'])
    db.execute('INSERT INTO attempts(item_id,answer,correct,created_at) VALUES(?,?,?,?)',
               (item['id'], answer, int(correct), now))
    if correct:
        failed = db.execute('''SELECT 1 FROM attempts a JOIN study_items i ON i.id=a.item_id
            WHERE i.session_id=? AND i.word_id=? AND a.correct=0 LIMIT 1''',
            (session['id'], item['word_id'])).fetchone() is not None
        stage, due = schedule(item['stage'], failed, now)
        db.execute('UPDATE words SET stage=?,due_at=? WHERE id=?', (stage, due, item['word_id']))
    if current_item(db, session['id']) is None:
        failures = db.execute('''SELECT i.word_id FROM study_items i JOIN attempts a ON a.item_id=i.id
            WHERE i.session_id=? AND i.round_number=? AND a.correct=0''',
            (session['id'], session['round_number'])).fetchall()
        if failures:
            number = session['round_number'] + 1
            add_round(db, session['id'], number, [row['word_id'] for row in failures])
            db.execute('UPDATE study_sessions SET round_number=? WHERE id=?', (number, session['id']))
        else:
            db.execute("UPDATE study_sessions SET status='complete' WHERE id=?", (session['id'],))
    return content(item) | {'correct': bool(correct), 'complete': current_item(db, session['id']) is None}
