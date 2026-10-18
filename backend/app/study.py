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
        JOIN study_sessions s ON s.id=i.session_id
        LEFT JOIN attempts a ON a.item_id=i.id
        WHERE i.session_id=? AND s.status='active' AND i.round_number=s.round_number
        AND a.id IS NULL ORDER BY i.position LIMIT 1''',
        (session_id,)).fetchone()


def content(row):
    return {key: row[key] for key in ('word', 'meaning', 'example', 'example_translation', 'audio', 'phonetic')} | {'word_id': row['word_id']}


def submit(db, session, item, answer, skip, now):
    previous = db.execute('SELECT * FROM attempts WHERE item_id=?', (item['id'],)).fetchone()
    if previous:
        return content(item) | {'correct': bool(previous['correct']),
                                'round_finished': session['status'] == 'complete' or item['round_number'] < session['round_number'],
                                'round_number': item['round_number']}
    correct = not skip and not item['hint_used'] and normalize(answer) == normalize(item['word'])
    db.execute('INSERT INTO attempts(item_id,answer,correct,created_at) VALUES(?,?,?,?)',
               (item['id'], answer, int(correct), now))
    if current_item(db, session['id']) is None:
        finish_round(db, session, now)
    return content(item) | {'correct': bool(correct), 'complete': current_item(db, session['id']) is None,
                            'round_finished': current_item(db, session['id']) is None or db.execute(
                                'SELECT round_number FROM study_sessions WHERE id=?', (session['id'],)).fetchone()[0] != session['round_number'],
                            'round_number': session['round_number']}


def finish_round(db, session, now):
    rows = db.execute('''SELECT i.*,w.stage,a.correct FROM study_items i JOIN words w ON w.id=i.word_id
        LEFT JOIN attempts a ON a.item_id=i.id WHERE i.session_id=? AND i.round_number=?
        ORDER BY i.position''', (session['id'], session['round_number'])).fetchall()
    for item in rows:
        if item['correct'] != 1:
            continue
        failed = db.execute('''SELECT 1 FROM attempts a JOIN study_items i ON i.id=a.item_id
            WHERE i.session_id=? AND i.word_id=? AND a.correct=0 LIMIT 1''',
            (session['id'], item['word_id'])).fetchone() is not None
        stage, due = schedule(item['stage'], failed, now)
        db.execute('UPDATE words SET stage=?,due_at=? WHERE id=?', (stage, due, item['word_id']))
    failures = [row['word_id'] for row in rows if row['correct'] != 1]
    if failures:
        number = session['round_number'] + 1
        add_round(db, session['id'], number, failures)
        db.execute('UPDATE study_sessions SET round_number=? WHERE id=?', (number, session['id']))
    else:
        db.execute("UPDATE study_sessions SET status='complete' WHERE id=?", (session['id'],))
