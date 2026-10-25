"""运行：.venv/bin/python -m unittest backend.tests.check_core -v"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TEMP = tempfile.TemporaryDirectory()
os.environ['WORDS_DB'] = str(Path(TEMP.name) / 'test.sqlite3')

from fastapi.testclient import TestClient
from backend.app.db import connect, initialize
from backend.app.main import app, hasher
from backend.app.study import schedule


class CoreChecks(unittest.TestCase):
    def setUp(self):
        initialize()
        with connect() as db:
            for table in ('attempts', 'study_items', 'study_sessions', 'words', 'decks', 'auth_sessions', 'users', 'dictionary_cache'):
                db.execute(f'DELETE FROM {table}')
            for name, role in [('parent', 'parent'), ('alice', 'child'), ('bob', 'child')]:
                db.execute('INSERT INTO users(username,password_hash,role) VALUES(?,?,?)',
                           (name, hasher.hash('password123'), role))
        self.client = TestClient(app)
        self.login('alice')

    def login(self, name):
        self.client.cookies.clear()
        result = self.client.post('/api/auth/login', json={'username': name, 'password': 'password123'})
        self.assertEqual(result.status_code, 200, result.text)
        self.headers = {'X-CSRF-Token': result.json()['csrf_token']}

    def post(self, path, data=None):
        return self.client.post(path, json=data, headers=self.headers)

    def make_deck(self):
        result = self.post('/api/decks/import', {'items': [
            {'id': 0, 'word': 'hello', 'meaning': '你好'},
            {'id': 0, 'word': 'world', 'meaning': '世界'}]})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()['id']

    def test_import_boundaries_and_isolation(self):
        deck = self.make_deck()
        result = self.post('/api/decks/import', {'items': [{'word': 'ok', 'meaning': '好'}, {'word': 'bad'}]})
        self.assertEqual(result.status_code, 422)
        self.assertEqual(len(self.client.get('/api/decks').json()), 1)
        self.login('bob')
        self.assertEqual(self.post(f'/api/decks/{deck}/study').status_code, 404)
        self.assertEqual(self.client.get('/api/decks').json(), [])
        self.assertEqual(self.post('/api/admin/users', {'username': 'x', 'password': 'password123'}).status_code, 403)
        self.assertEqual(self.client.post('/api/reviews/study').status_code, 403)

    def test_return_finishes_answered_round_and_keeps_records(self):
        deck = self.make_deck()
        sid = self.post(f'/api/decks/{deck}/study').json()['id']
        item = self.client.get(f'/api/study/{sid}/next').json()['item']
        word = 'hello' if item['meaning'] == '你好' else 'world'
        self.post(f'/api/study/{sid}/items/{item["id"]}/answer', {'answer': word})
        with connect() as db:
            self.assertEqual([r[0] for r in db.execute('SELECT stage FROM words')], [-1, -1])
        self.assertEqual(self.client.get(f'/api/study/{sid}/rounds/1').status_code, 409)
        self.assertEqual(self.post(f'/api/study/{sid}/round-action', {'action': 'interrupt', 'round_number': 1}).status_code, 422)
        with connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0], 1)
        self.assertEqual(self.post(f'/api/study/{sid}/round-action', {'action': 'finish', 'round_number': 1}).status_code, 200)
        report = self.client.get(f'/api/study/{sid}/rounds/1').json()
        self.assertEqual((report['correct'], report['wrong'], report['unanswered']), (1, 0, 1))
        self.assertEqual([w['next_round_probability'] for w in report['words']], [0, 100])
        self.assertEqual(self.post(f'/api/study/{sid}/round-action', {'action': 'finish', 'round_number': 1}).status_code, 409)
        next_item = self.client.get(f'/api/study/{sid}/next').json()['item']
        self.assertEqual((next_item['round_number'], next_item['total']), (2, 1))
        with connect() as db:
            self.assertEqual(sorted(r[0] for r in db.execute('SELECT stage FROM words')), [-1, 0])
        self.post(f'/api/study/{sid}/items/{next_item["id"]}/reveal')
        self.assertEqual(self.post(f'/api/study/{sid}/round-action', {'action': 'finish', 'round_number': 2}).status_code, 200)
        unchanged = self.client.get(f'/api/study/{sid}/next').json()['item']
        self.assertEqual(unchanged['round_number'], 2, '未答题返回不生成空轮次')
        with connect() as db:
            self.assertEqual(db.execute('SELECT hint_used FROM study_items WHERE id=?', (next_item['id'],)).fetchone()[0], 1)
        self.login('bob')
        self.assertEqual(self.client.get(f'/api/study/{sid}/rounds/1').status_code, 404)

    def test_return_during_practice_preserves_wrong_answer(self):
        deck = self.make_deck()
        sid = self.post(f'/api/decks/{deck}/study').json()['id']
        item = self.client.get(f'/api/study/{sid}/next').json()['item']
        base = f'/api/study/{sid}/items/{item["id"]}'
        self.post(base + '/reveal')
        self.post(base + '/answer', {'skip': True})
        self.assertEqual(self.post(f'/api/study/{sid}/round-action', {'action': 'finish', 'round_number': 1}).status_code, 200)
        report = self.client.get(f'/api/study/{sid}/rounds/1').json()
        self.assertEqual((report['correct'], report['wrong'], report['unanswered']), (0, 1, 1))
        next_item = self.client.get(f'/api/study/{sid}/next').json()['item']
        self.assertEqual((next_item['round_number'], next_item['total']), (2, 2))
        self.assertEqual(self.post(base + '/answer', {'answer': 'hello', 'practice': True}).status_code, 409)
        self.assertEqual(self.post(f'/api/study/{sid}/round-action', {'action': 'finish', 'round_number': 1}).status_code, 409)
        with connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM attempts WHERE correct=0').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT hint_used FROM study_items WHERE id=?', (item['id'],)).fetchone()[0], 1)
            self.assertEqual([r[0] for r in db.execute('SELECT stage FROM words')], [-1, -1])

    def test_round_progress_survives_relogin(self):
        deck = self.make_deck()
        sid = self.post(f'/api/decks/{deck}/study').json()['id']
        for number in (1, 2, 3):
            active = self.client.get('/api/study/active').json()['session']
            self.assertEqual((active['id'], active['round_number'], active['total'], active['answered']),
                             (sid, number, 2 if number == 1 else 1, 0))
            question = self.client.get(f'/api/study/{sid}/next').json()['item']
            self.assertNotIn('word', question)
            iid = question['id']
            self.login('alice')
            self.assertEqual(self.client.get(f'/api/study/{sid}/next').json()['item'], question)
            self.assertEqual(self.post('/api/reviews/study').json()['id'], sid)
            self.assertFalse(self.post(f'/api/study/{sid}/items/{iid}/answer', {'skip': True}).json()['correct'])
            with connect() as db:
                spelling = db.execute('SELECT w.word FROM words w JOIN study_items i ON i.word_id=w.id WHERE i.id=?', (iid,)).fetchone()[0]
            self.post(f'/api/study/{sid}/items/{iid}/answer', {'answer': spelling, 'practice': True})
            if number == 1:
                progress = self.client.get('/api/study/active').json()['session']
                self.assertEqual((progress['answered'], progress['remaining']), (1, 1))
                second = self.client.get(f'/api/study/{sid}/next').json()['item']
                word = 'hello' if second['meaning'] == '你好' else 'world'
                self.post(f'/api/study/{sid}/items/{second["id"]}/answer', {'answer': word})
        final = self.client.get(f'/api/study/{sid}/next').json()['item']
        self.assertEqual((final['round_number'], final['total']), (4, 1))
        word = 'hello' if final['meaning'] == '你好' else 'world'
        self.post(f'/api/study/{sid}/items/{final["id"]}/answer', {'answer': word})
        self.assertIsNone(self.client.get('/api/study/active').json()['session'])
        self.login('bob')
        self.assertIsNone(self.client.get('/api/study/active').json()['session'])
        self.assertEqual(self.client.get(f'/api/study/{sid}/next').status_code, 404)

    def test_rounds_resume_hints_idempotency(self):
        deck = self.make_deck()
        sid = self.post(f'/api/decks/{deck}/study').json()['id']
        self.assertEqual(self.post(f'/api/decks/{deck}/study').json()['id'], sid)
        first = self.client.get(f'/api/study/{sid}/next').json()['item']
        base = f'/api/study/{sid}/items/{first["id"]}'
        shown = self.post(base + '/reveal').json()
        result = self.post(base + '/answer', {'answer': shown['word']})
        self.assertFalse(result.json()['correct'])
        replay = self.post(base + '/answer', {'answer': shown['word']}).json()
        self.assertTrue(replay['practice_pending'])
        self.post(base + '/answer', {'answer': shown['word'], 'practice': True})
        second = self.client.get(f'/api/study/{sid}/next').json()['item']
        self.assertNotEqual(second['id'], first['id'])
        expected = 'world' if shown['word'] == 'hello' else 'hello'
        self.assertTrue(self.post(f'/api/study/{sid}/items/{second["id"]}/answer', {'answer': '  ' + expected.upper() + ' '}).json()['correct'])
        retry = self.client.get(f'/api/study/{sid}/next').json()['item']
        self.assertEqual(retry['round_number'], 2)
        self.assertEqual(retry['remaining'], 1)
        self.post(f'/api/study/{sid}/items/{retry["id"]}/answer', {'answer': shown['word']})
        result = self.client.get(f'/api/study/{sid}/next').json()
        self.assertTrue(result['complete'])
        self.assertEqual(result['summary']['attempts'], 3)
        with connect() as db:
            self.assertEqual([r[0] for r in db.execute('SELECT stage FROM words')], [0, 0])
            db.execute('UPDATE words SET due_at=0')
        review = self.post('/api/reviews/study').json()['id']
        for _ in range(2):
            item = self.client.get(f'/api/study/{review}/next').json()['item']
            with connect() as db:
                word = db.execute('SELECT w.word FROM words w JOIN study_items i ON i.word_id=w.id WHERE i.id=?', (item['id'],)).fetchone()[0]
            self.post(f'/api/study/{review}/items/{item["id"]}/answer', {'answer': word})
        with connect() as db:
            self.assertEqual([r[0] for r in db.execute('SELECT stage FROM words')], [1, 1])

    def test_wrong_review_resets_and_blocks_future_items(self):
        deck = self.make_deck()
        sid = self.post(f'/api/decks/{deck}/study').json()['id']
        with connect() as db:
            rows = db.execute('SELECT id,word_id FROM study_items WHERE session_id=? ORDER BY position', (sid,)).fetchall()
        future = rows[1]['id']
        self.assertEqual(self.post(f'/api/study/{sid}/items/{future}/answer', {'answer': 'world'}).status_code, 409)
        for _ in range(2):
            item = self.client.get(f'/api/study/{sid}/next').json()['item']
            with connect() as db:
                word = db.execute('SELECT w.word FROM words w JOIN study_items i ON i.word_id=w.id WHERE i.id=?', (item['id'],)).fetchone()[0]
            self.post(f'/api/study/{sid}/items/{item["id"]}/answer', {'answer': word})
        with connect() as db:
            db.execute('UPDATE words SET stage=4,due_at=0')
        review = self.post('/api/reviews/study').json()['id']
        for _ in range(2):
            item = self.client.get(f'/api/study/{review}/next').json()['item']
            self.post(f'/api/study/{review}/items/{item["id"]}/answer', {'skip': True})
            with connect() as db:
                spelling = db.execute('SELECT w.word FROM words w JOIN study_items i ON i.word_id=w.id WHERE i.id=?', (item['id'],)).fetchone()[0]
            self.post(f'/api/study/{review}/items/{item["id"]}/answer', {'answer': spelling, 'practice': True})
        for _ in range(2):
            item = self.client.get(f'/api/study/{review}/next').json()['item']
            with connect() as db:
                word = db.execute('SELECT w.word FROM words w JOIN study_items i ON i.word_id=w.id WHERE i.id=?', (item['id'],)).fetchone()[0]
            self.post(f'/api/study/{review}/items/{item["id"]}/answer', {'answer': word})
        with connect() as db:
            self.assertEqual([r[0] for r in db.execute('SELECT stage FROM words')], [0, 0])

    def test_request_security(self):
        self.assertEqual(self.client.get('/api/auth/me', headers={'Host': 'evil.example'}).status_code, 400)
        self.assertEqual(self.client.post('/api/reviews/study', headers=self.headers | {'Origin': 'https://evil.example'}).status_code, 403)
        self.assertEqual(self.client.post('/api/decks/import', content=b'x' * (2 * 1024 * 1024 + 1), headers=self.headers).status_code, 413)
        deck = self.make_deck()
        sid = self.post(f'/api/decks/{deck}/study').json()['id']
        self.login('bob')
        self.assertEqual(self.client.get(f'/api/study/{sid}/next').status_code, 404)
        self.assertEqual(self.post('/api/decks/import', {'user_id': 1, 'items': [{'word': 'x', 'meaning': 'x'}]}).status_code, 403)

    def test_dictionary_preserves_imported_pair(self):
        import json
        import time
        deck = self.make_deck()
        sid = self.post(f'/api/decks/{deck}/study').json()['id']
        item = self.client.get(f'/api/study/{sid}/next').json()['item']
        info = self.post(f'/api/study/{sid}/items/{item["id"]}/reveal').json()
        with connect() as db:
            db.execute('UPDATE words SET example=?,example_translation=? WHERE id=?', ('Original sentence.', '原句。', info['word_id']))
            db.execute('INSERT OR REPLACE INTO dictionary_cache VALUES(?,?,?)',
                       (info['word'], json.dumps([{'meanings': [{'definitions': [{'example': 'Different sentence.'}]}], 'phonetics': [{'audio': 'https://evil.example/audio.mp3'}]}]), time.time()))
        result = self.post(f'/api/words/{info["word_id"]}/enrich')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['example'], 'Original sentence.')
        self.assertEqual(result.json()['example_translation'], '原句。')
        self.assertEqual(result.json()['audio'], '')
        saved = self.client.patch(f'/api/words/{info["word_id"]}', json={'example': 'Hello, teacher.', 'example_translation': '老师，你好。'}, headers=self.headers)
        self.assertEqual(saved.status_code, 200)
        self.login('bob')
        self.assertEqual(self.client.patch(f'/api/words/{info["word_id"]}', json={'example': 'x', 'example_translation': 'x'}, headers=self.headers).status_code, 404)

    def test_schema_upgrade_preserves_data(self):
        import sqlite3
        from backend.app import db as database
        with sqlite3.connect(':memory:') as connection:
            connection.executescript(database.BASE_SCHEMA)
            connection.execute("INSERT INTO users VALUES(1,'old','hash','parent',1)")
            database.migrate(connection)
            database.migrate(connection)
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 3)
            self.assertEqual(connection.execute('SELECT username FROM users').fetchone()[0], 'old')
            self.assertIn('practice_pending', [r[1] for r in connection.execute('PRAGMA table_info(study_items)')])

    def test_persistent_practice_and_last_report(self):
        deck = self.post('/api/decks/import', {'items': [{'word': 'hello', 'meaning': '你好'}]}).json()['id']
        sid = self.post(f'/api/decks/{deck}/study').json()['id']
        item = self.client.get(f'/api/study/{sid}/next').json()['item']
        base = f'/api/study/{sid}/items/{item["id"]}'

        def snapshot():
            with connect() as db:
                return (
                    [tuple(row) for row in db.execute('SELECT stage,due_at FROM words ORDER BY id')],
                    [tuple(row) for row in db.execute('SELECT * FROM attempts ORDER BY id')],
                    db.execute('SELECT round_number FROM study_sessions WHERE id=?', (sid,)).fetchone()[0],
                    [tuple(row) for row in db.execute('SELECT * FROM study_items ORDER BY id')],
                )

        initial = snapshot()
        self.assertEqual(self.post(base + '/answer', {'answer': 'hello', 'practice': True}).status_code, 409)
        self.assertEqual(snapshot(), initial)
        first = self.post(base + '/answer', {'answer': 'bad'}).json()
        self.assertTrue(first['practice_pending'])
        self.assertFalse(first['round_finished'])
        pending = snapshot()
        self.assertEqual(pending[0], initial[0])
        self.assertEqual(len(pending[1]), 1)
        self.assertEqual(pending[2], 1)
        self.login('bob')
        for path, payload in (
            (base + '/answer', {'answer': 'hello', 'practice': True}),
            (base + '/reveal', None),
            (f'/api/study/{sid}/round-action', {'action': 'finish', 'round_number': 1}),
            (f'/api/study/{sid}/round-action', {'action': 'interrupt', 'round_number': 1}),
        ):
            with self.subTest(path=path, payload=payload):
                self.assertEqual(self.post(path, payload).status_code, 404)
                self.assertEqual(snapshot(), pending)
        self.login('alice')
        restored = self.client.get(f'/api/study/{sid}/next').json()['item']
        self.assertEqual(restored['id'], item['id'])
        self.assertTrue(restored['practice_pending'])
        for payload in ({'answer': 'bad'}, {'answer': 'hello'}, {'answer': 'bad', 'practice': True}, {'skip': True, 'practice': True}):
            with self.subTest(payload=payload):
                response = self.post(base + '/answer', payload)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.json()['practice_pending'])
                self.assertEqual(snapshot(), pending)
        self.assertEqual(self.post(f'/api/study/{sid}/round-action', {'action': 'interrupt', 'round_number': 1}).status_code, 422)
        self.assertEqual(snapshot(), pending)
        self.assertEqual(self.client.get(f'/api/study/{sid}/rounds/1').status_code, 409)
        self.assertEqual(self.post(base + '/reveal').status_code, 200)
        done = self.post(base + '/answer', {'answer': 'HELLO', 'practice': True}).json()
        self.assertFalse(done['correct'])
        self.assertFalse(done['practice_pending'])
        self.assertTrue(done['round_finished'])
        advanced = snapshot()
        self.assertEqual(advanced[0], initial[0])
        self.assertEqual(advanced[1], pending[1])
        self.assertEqual(advanced[2], 2)
        for payload in ({'answer': 'bad'}, {'answer': 'HELLO', 'practice': True}):
            response = self.post(base + '/answer', payload)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(snapshot(), advanced)
        report = self.client.get(f'/api/study/{sid}/rounds/1').json()
        self.assertEqual((report['correct'], report['wrong']), (0, 1))
        with connect() as db:
            self.assertEqual(db.execute('SELECT stage FROM words').fetchone()[0], -1)
            self.assertEqual(tuple(db.execute('SELECT answer,correct FROM attempts').fetchone()), ('bad', 0))
            self.assertEqual(db.execute('SELECT count(*) FROM study_items').fetchone()[0], 2)
        retry = self.client.get(f'/api/study/{sid}/next').json()['item']
        self.assertEqual(retry['round_number'], 2)
        retry_base = f'/api/study/{sid}/items/{retry["id"]}'
        with patch('backend.app.main.time.time', return_value=1000):
            response = self.post(retry_base + '/answer', {'answer': 'hello'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['complete'])
        complete = snapshot()
        self.assertEqual(complete[0], [(0, 1600)])
        self.assertEqual(len(complete[1]), 2)
        self.assertEqual(complete[2], 2)
        for path, payload in (
            (retry_base + '/answer', {'answer': 'hello'}),
            (base + '/answer', {'answer': 'bad'}),
            (base + '/answer', {'answer': 'HELLO', 'practice': True}),
        ):
            response = self.post(path, payload)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(snapshot(), complete)

    def test_schedule(self):
        self.assertEqual(schedule(-1, False, 100), (0, 700))
        self.assertEqual(schedule(3, True, 100), (0, 700))
        self.assertEqual(schedule(5, False, 100), (5, 2592100))

    def test_content_failure_and_account_revocation(self):
        deck = self.make_deck()
        sid = self.post(f'/api/decks/{deck}/study').json()['id']
        item = self.client.get(f'/api/study/{sid}/next').json()['item']
        info = self.post(f'/api/study/{sid}/items/{item["id"]}/reveal').json()
        import httpx
        with patch('httpx.AsyncClient.stream', side_effect=httpx.ConnectError('offline')):
            self.assertEqual(self.post(f'/api/words/{info["word_id"]}/enrich').status_code, 502)
        child = self.client
        self.client = TestClient(app)
        self.login('parent')
        uid = next(u['id'] for u in self.client.get('/api/admin/users').json() if u['username'] == 'alice')
        self.client.patch(f'/api/admin/users/{uid}', json={'is_active': False}, headers=self.headers)
        self.assertEqual(child.get('/api/auth/me').status_code, 401)


if __name__ == '__main__':
    unittest.main()
