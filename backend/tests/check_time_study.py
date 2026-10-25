"""固定跨库队列、真实时间证据与备份回归；仅临时库。"""
import sqlite3
import unittest
from unittest.mock import patch

from backend.tests import check_core as core
from backend.app import db as database, user_backup, database_admin


class TimeStudyChecks(unittest.TestCase):
    setUp = core.CoreChecks.setUp
    login = core.CoreChecks.login
    post = core.CoreChecks.post
    make_deck = core.CoreChecks.make_deck

    def answer_current(self, sid: int, now: float, **extra) -> dict:
        item = self.client.get(f'/api/study/{sid}/next').json()['item']
        with database.connect() as db:
            word = db.execute('SELECT w.word FROM words w JOIN study_items i ON i.word_id=w.id WHERE i.id=?', (item['id'],)).fetchone()[0]
        with patch('backend.app.main.time.time', return_value=now):
            result = self.post(f'/api/study/{sid}/items/{item["id"]}/answer', {'answer': word, **extra})
        self.assertEqual(result.status_code, 200, result.text)
        return item

    def test_cross_day_fixed_scope_and_delayed_evidence(self):
        first, second = self.make_deck(), self.make_deck()
        with database.connect() as db:
            db.execute('UPDATE words SET stage=0,due_at=1000,last_seen_at=0')
            outside = db.execute('SELECT min(id) FROM words').fetchone()[0]
            db.execute('UPDATE words SET due_at=200000 WHERE id=?', (outside,))
        with patch('backend.app.main.time.time', return_value=100000):
            result = self.post('/api/reviews/study', {'deck_ids': [first, second]}).json()
        sid = result['id']
        self.assertEqual(len(result['sessions']), 1)
        self.assertIsNone(result['sessions'][0]['deck_id'])
        first_item = self.answer_current(sid, 100000)
        with database.connect() as db:
            state = tuple(db.execute('SELECT stage,due_at,last_seen_at FROM words WHERE id=(SELECT word_id FROM study_items WHERE id=?)', (first_item['id'],)).fetchone())
        self.assertEqual(state, (1, 300000, 100000))
        # Replay after two days cannot alter scheduling or add evidence.
        with patch('backend.app.main.time.time', return_value=272800):
            self.post(f'/api/study/{sid}/items/{first_item["id"]}/answer', {'answer': 'anything', 'effort': 'hard'})
            question = self.client.get(f'/api/study/{sid}/next').json()
        self.assertEqual(question['additional_due_count'], 1)
        self.assertEqual(question['item']['total'], 3)
        self.answer_current(sid, 272800)
        self.answer_current(sid, 272801)
        summary = self.client.get(f'/api/study/{sid}/next').json()['summary']
        self.assertEqual(summary['delayed_recall'], {'attempts': 3, 'correct': 3, 'accuracy': 1.0})
        stats = self.client.get('/api/statistics?range=all').json()
        self.assertEqual(stats['delayed_recall']['attempts'], 3)
        # Single-library study includes words newly due after task completion.
        self.assertEqual(self.post(f'/api/decks/{first}/study').status_code, 200)

    def test_hard_once_reveal_and_backup(self):
        deck = self.post('/api/decks/import', {'items': [{'word': 'hello', 'meaning': '你好'}]}).json()['id']
        sid = self.post('/api/reviews/study', {'deck_ids': [deck], 'include_unlearned': True}).json()['id']
        item = self.answer_current(sid, 100000, effort='hard')
        report = self.client.get(f'/api/study/{sid}/rounds/1').json()['words'][0]
        self.assertTrue(report['correct'])
        self.assertTrue(report['needs_retry'])
        self.answer_current(sid, 100001, effort='hard')
        self.assertTrue(self.client.get(f'/api/study/{sid}/next').json()['complete'])
        with database.connect() as db:
            uid = db.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
            backup = user_backup.snapshot(db, uid)
            self.assertEqual(backup['study_sessions'][0]['scope_deck_ids'], [1])
            self.assertEqual(backup['attempts'][0]['effort'], 'hard')
            user_backup.replace(db, uid, backup)
            self.assertEqual(db.execute('SELECT last_seen_at FROM words').fetchone()[0], 100001)
            db.execute('UPDATE words SET due_at=0,last_seen_at=0')
        sid = self.post(f'/api/decks/{deck}/study').json()['id']
        item = self.client.get(f'/api/study/{sid}/next').json()['item']
        with patch('backend.app.main.time.time', return_value=200000):
            self.post(f'/api/study/{sid}/items/{item["id"]}/reveal')
        self.answer_current(sid, 200001)
        self.answer_current(sid, 200002, practice=True)
        self.answer_current(sid, 200003)
        with database.connect() as db:
            self.assertEqual(db.execute('SELECT sum(delayed_recall) FROM attempts').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT stage FROM words').fetchone()[0], 0)

    def test_backup_rejects_item_outside_scope_and_accepts_old_empty_scope(self):
        from fastapi import HTTPException
        first, second = self.make_deck(), self.make_deck()
        self.post(f'/api/decks/{first}/study')
        with database.connect() as db:
            uid = db.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
            backup = user_backup.snapshot(db, uid)
        backup['study_sessions'][0]['scope_deck_ids'] = [2]
        with self.assertRaises(HTTPException) as invalid:
            user_backup.validate(backup)
        self.assertEqual(invalid.exception.status_code, 422)
        backup['study_sessions'][0].pop('scope_deck_ids')
        for deck in backup['decks']:
            for word in deck['words']:
                for field in ('last_seen_at', 'last_success_at', 'memory_interval'):
                    word.pop(field)
        self.assertEqual(user_backup.validate(backup)['study_sessions'][0]['scope_deck_ids'], [])

    def test_delete_empty_selected_deck_cancels_and_cleans_scope(self):
        first, second = self.make_deck(), self.make_deck()
        with database.connect() as db:
            db.execute('UPDATE words SET stage=0,due_at=0 WHERE deck_id=?', (first,))
            db.execute('DELETE FROM words WHERE deck_id=?', (second,))
        sid = self.post('/api/reviews/study', {'deck_ids': [first, second]}).json()['id']
        response = self.client.delete(f'/api/decks/{second}', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(f'/api/study/{sid}/next').status_code, 409)
        with database.connect() as db:
            uid = db.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
            self.assertEqual(user_backup.snapshot(db, uid)['study_sessions'][0]['scope_deck_ids'], [1])

    def test_recent_success_preserves_due_and_hard_shrinks_interval(self):
        from backend.app.study import record_memory
        self.make_deck()
        with database.connect() as db:
            wid = db.execute('SELECT min(id) FROM words').fetchone()[0]
            db.execute('''UPDATE words SET stage=4,memory_interval=1209600,
                last_seen_at=1000,last_success_at=1000,due_at=1210600 WHERE id=?''', (wid,))
            record_memory(db, wid, True, 2000)
            self.assertEqual(tuple(db.execute('SELECT memory_interval,due_at FROM words WHERE id=?', (wid,)).fetchone()), (1209600, 1210600))
            record_memory(db, wid, True, 2001, 'hard')
            self.assertEqual(tuple(db.execute('SELECT memory_interval,due_at,stage FROM words WHERE id=?', (wid,)).fetchone()), (600, 2601, 0))
            record_memory(db, wid, True, 2002)
            self.assertEqual(tuple(db.execute('SELECT memory_interval,due_at FROM words WHERE id=?', (wid,)).fetchone()), (600, 2601))
            record_memory(db, wid, True, 302002)
            self.assertEqual(tuple(db.execute('SELECT memory_interval,due_at,stage FROM words WHERE id=?', (wid,)).fetchone()), (600000, 902002, 2))

    def test_v3_migration_keeps_task_and_evidence_conservative(self):
        with sqlite3.connect(':memory:') as db:
            db.executescript(database.SCHEMA_V3)
            db.execute("INSERT INTO users VALUES(1,'old',?,'parent',1)", (core.hasher.hash('password123'),))
            db.execute("INSERT INTO decks VALUES(1,1,'old',0,NULL)")
            db.execute("INSERT INTO words(id,deck_id,word,meaning,stage,due_at) VALUES(1,1,'hello','你好',2,100)")
            db.execute("INSERT INTO study_sessions VALUES(1,1,NULL,'review','active',1)")
            db.execute('INSERT INTO study_items(id,session_id,word_id,round_number,position) VALUES(1,1,1,1,0)')
            db.execute("INSERT INTO attempts VALUES(1,1,'hello',1,10)")
            database_admin._validate(db)
            db.execute('PRAGMA query_only=OFF')
            database.migrate(db)
            self.assertEqual(db.execute('SELECT scope_deck_ids FROM study_sessions').fetchone()[0], '[1]')
            self.assertEqual(db.execute('SELECT stage,due_at,last_seen_at,last_success_at FROM words').fetchone(), (2, 100, 10, 10))
            self.assertEqual(db.execute('SELECT delayed_recall FROM attempts').fetchone()[0], 0)
            database_admin._validate(db)


if __name__ == '__main__':
    unittest.main()
