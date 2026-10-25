"""每词库批次、选择复习与旧库迁移回归。"""
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor

from backend.tests import check_core as core
from backend.app import db as database
from backend.app.main import start_study


class MultiStudyChecks(unittest.TestCase):
    setUp = core.CoreChecks.setUp
    login = core.CoreChecks.login
    post = core.CoreChecks.post
    make_deck = core.CoreChecks.make_deck

    def test_independent_rounds_and_concurrent_resume(self):
        first, second = self.make_deck(), self.make_deck()
        sid = self.post(f'/api/decks/{first}/study').json()['id']
        other = self.post(f'/api/decks/{second}/study').json()['id']
        self.assertNotEqual(sid, other)
        question = self.client.get(f'/api/study/{other}/next').json()
        item = self.client.get(f'/api/study/{sid}/next').json()['item']
        self.post(f'/api/study/{sid}/items/{item["id"]}/answer', {'skip': True})
        self.post(f'/api/study/{sid}/round-action', {'action': 'finish', 'round_number': 1})
        self.assertEqual(self.client.get(f'/api/study/{other}/next').json(), question)
        sessions = self.client.get('/api/study/active').json()['sessions']
        self.assertEqual([s['round_number'] for s in sessions], [2, 1])
        self.assertEqual([s['deck_id'] for s in sessions], [first, second])
        with database.connect() as db:
            account = dict(db.execute("SELECT * FROM users WHERE username='alice'").fetchone())
        third = self.make_deck()
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(lambda _: start_study(account, third)['id'], range(8)))
        self.assertEqual(len(set(ids)), 1)
        with database.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM study_items WHERE session_id=?', (ids[0],)).fetchone()[0], 2)

    def test_selected_reviews_eligibility_ownership_and_duplicates(self):
        first, second, third = self.make_deck(), self.make_deck(), self.make_deck()
        with database.connect() as db:
            db.execute('UPDATE words SET stage=0,due_at=0')
            # 即使异常旧数据设置了 due_at，未学词仍不得进入复习。
            db.execute('UPDATE words SET stage=-1 WHERE id=(SELECT min(id) FROM words WHERE deck_id=?)', (first,))
        due = self.client.get('/api/reviews/due').json()
        self.assertEqual(due['count'], 5)
        selected = self.post('/api/reviews/study', {'deck_ids': [first, second, first]}).json()
        self.assertEqual([s['deck_id'] for s in selected['sessions']], [None])
        self.assertEqual([s['total'] for s in selected['sessions']], [3])
        self.assertEqual(self.post('/api/reviews/study', {'deck_ids': [first, second]}).status_code, 409)
        with database.connect() as db:
            self.assertIsNone(db.execute('SELECT id FROM study_sessions WHERE deck_id=?', (third,)).fetchone())
        self.assertEqual(self.client.get('/api/reviews/due').json()['count'], 2)
        with database.connect() as db:
            account = dict(db.execute("SELECT * FROM users WHERE username='alice'").fetchone())
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as conflict:
            start_study(account, deck_ids=[first, second])
        self.assertEqual(conflict.exception.status_code, 409)
        for data in ({'deck_ids': []}, {'deck_ids': ['1']}, {'deck_ids': [True]}):
            self.assertEqual(self.post('/api/reviews/study', data).status_code, 422)
        self.assertEqual(self.post('/api/reviews/study', {'deck_ids': [third, 99999]}).status_code, 404)
        self.login('bob')
        self.assertEqual(self.post('/api/reviews/study', {'deck_ids': [first]}).status_code, 404)

    def test_existing_new_and_no_due(self):
        first, second = self.make_deck(), self.make_deck()
        self.assertEqual(self.post('/api/reviews/study', {'deck_ids': [first]}).status_code, 409)
        sid = self.post(f'/api/decks/{first}/study').json()['id']
        self.assertEqual(self.post('/api/reviews/study', {'deck_ids': [first]}).status_code, 409)
        self.assertEqual(self.post('/api/reviews/study', {'deck_ids': [second]}).status_code, 409)

    def test_legacy_review_resumes_without_overlap(self):
        first, second, third = self.make_deck(), self.make_deck(), self.make_deck()
        with database.connect() as db:
            uid = db.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
            sid = db.execute("INSERT INTO study_sessions(user_id,kind) VALUES(?,'review')", (uid,)).lastrowid
            words = db.execute('SELECT id FROM words WHERE deck_id IN (?,?)', (first, second)).fetchall()
            from backend.app.study import add_round
            add_round(db, sid, 1, [w['id'] for w in words])
        original = self.client.get(f'/api/study/{sid}/next').json()
        for did in (first, second):
            self.assertEqual(self.post(f'/api/decks/{did}/study').json()['id'], sid)
        self.assertEqual(self.post('/api/reviews/study', {'deck_ids': [first, second]}).status_code, 409)
        self.assertEqual(self.client.get(f'/api/study/{sid}/next').json(), original)
        self.assertNotEqual(self.post(f'/api/decks/{third}/study').json()['id'], sid)

    def test_backup_accepts_multiple_active_and_scoped_review(self):
        from backend.app import database_admin, user_backup
        first, second = self.make_deck(), self.make_deck()
        self.post(f'/api/decks/{first}/study')
        with database.connect() as db:
            db.execute('UPDATE words SET stage=0,due_at=0 WHERE deck_id=?', (second,))
        self.post('/api/reviews/study', {'deck_ids': [second]})
        with database.connect() as db:
            uid = db.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
            backup = user_backup.snapshot(db, uid)
            self.assertEqual(len(backup['study_sessions']), 2)
            self.assertEqual({s['kind'] for s in backup['study_sessions']}, {'new', 'review'})
        # 严格 schema 校验须同时支持旧版整库备份及新索引。
        for schema in (database.BASE_SCHEMA, database.SCHEMA_V2, database.SCHEMA):
            with sqlite3.connect(':memory:') as db:
                db.executescript(schema)
                db.execute('INSERT INTO users VALUES(1,?,?,?,1)', ('parent', core.hasher.hash('password123'), 'parent'))
                database_admin._validate(db)

    def test_failed_initialize_rolls_back_schema_and_index(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'old.sqlite3'
            with sqlite3.connect(path) as db:
                db.executescript(database.SCHEMA_V2)
                # 冲突索引模拟迁移中途失败；旧索引及版本必须回滚。
                db.execute('CREATE INDEX active_study_legacy ON study_sessions(kind)')
            with patch.object(database, 'DB_PATH', path):
                with self.assertRaises(sqlite3.OperationalError):
                    database.initialize()
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 2)
                sql = db.execute("SELECT sql FROM sqlite_master WHERE name='active_study'").fetchone()[0]
                self.assertIn('study_sessions(user_id)', sql)
                db.execute('DROP INDEX active_study_legacy')
            with patch.object(database, 'DB_PATH', path):
                database.initialize()
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 4)

    def test_migration_preserves_every_row_and_position(self):
        for schema in (database.BASE_SCHEMA, database.SCHEMA_V2):
            with self.subTest(schema=schema[-25:]), sqlite3.connect(':memory:') as db:
                db.executescript(schema)
                db.execute("INSERT INTO users VALUES(1,'old','hash','parent',1)")
                db.executemany('INSERT INTO decks VALUES(?,1,?,0,0)', [(1, 'one'), (2, 'two')])
                db.executemany("INSERT INTO words(id,deck_id,word,meaning,stage,due_at) VALUES(?,?,'word','词',0,0)", [(1, 1), (2, 2)])
                db.execute("INSERT INTO study_sessions(id,user_id,deck_id,kind,status,round_number) VALUES(1,1,NULL,'review','active',2)")
                db.execute('INSERT INTO study_items(id,session_id,word_id,round_number,position,hint_used) VALUES(1,1,1,1,0,1)')
                db.execute("INSERT INTO attempts VALUES(1,1,'wrong',0,1)")
                db.execute('INSERT INTO study_items(id,session_id,word_id,round_number,position,hint_used) VALUES(2,1,2,2,0,1)')
                if schema == database.SCHEMA_V2:
                    db.execute('UPDATE study_items SET practice_pending=1 WHERE id=2')
                tables = ('users', 'decks', 'words', 'study_sessions', 'attempts')
                before = {t: db.execute(f'SELECT * FROM {t}').fetchall() for t in tables}
                items = db.execute('SELECT id,session_id,word_id,round_number,position,hint_used FROM study_items').fetchall()
                database.migrate(db)
                database.migrate(db)
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 4)
                for table in tables:
                    self.assertEqual([row[:len(before[table][0])] for row in db.execute(f'SELECT * FROM {table}')], before[table])
                self.assertEqual(db.execute('SELECT id,session_id,word_id,round_number,position,hint_used FROM study_items').fetchall(), items)
                self.assertEqual(db.execute('SELECT practice_pending FROM study_items WHERE id=2').fetchone()[0], int(schema == database.SCHEMA_V2))
                db.execute("INSERT INTO study_sessions(id,user_id,deck_id,kind,status,round_number) VALUES(2,1,1,'new','active',1)")
                db.execute("INSERT INTO study_sessions(id,user_id,deck_id,kind,status,round_number) VALUES(3,1,2,'new','active',1)")
                for deck in ('1', 'NULL'):
                    with self.assertRaises(sqlite3.IntegrityError):
                        db.execute(f"INSERT INTO study_sessions(id,user_id,deck_id,kind,status,round_number) VALUES(4,1,{deck},'review','active',1)")


if __name__ == '__main__':
    unittest.main()
