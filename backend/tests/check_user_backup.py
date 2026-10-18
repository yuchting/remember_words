"""个人备份检查，始终使用临时数据库。"""
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from argon2 import PasswordHasher
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import db, user_backup as backup


class UserBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = db.DB_PATH
        db.DB_PATH = Path(self.temp.name) / 'words.sqlite3'
        db.initialize()
        self.hasher = PasswordHasher(time_cost=1, memory_cost=8192)
        with db.connect() as connection:
            for uid in (10, 20):
                connection.execute('INSERT INTO users VALUES(?,?,?,?,1)',
                                   (uid, f'private-{uid}', self.hasher.hash('test-password'), 'child'))
                connection.execute('INSERT INTO auth_sessions VALUES(?,?,?,?)', (f'token-{uid}', uid, 'csrf', 9999999999))
                connection.execute('INSERT INTO decks VALUES(?,?,?,?,?)', (uid, uid, '词库', 100.0, 101.0))
                connection.execute('''INSERT INTO words(id,deck_id,word,meaning,stage,due_at)
                    VALUES(?,?,?, ?,3,1000)''', (uid, uid, 'apple', '苹果'))
                connection.execute('INSERT INTO study_sessions VALUES(?,?,?,?,?,?)', (uid, uid, uid, 'new', 'active', 1))
                connection.execute('INSERT INTO study_items VALUES(?,?,?,?,?,?)', (uid, uid, uid, 1, 0, 0))
                connection.execute('INSERT INTO attempts VALUES(?,?,?,?,?)', (uid, uid, 'apple', 1, 102.0))
        self.uid = 10
        app = FastAPI()
        backup.register(app, lambda: {'id': self.uid}, self.hasher)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        db.DB_PATH = self.previous
        self.temp.cleanup()

    def export(self):
        response = self.client.post('/api/account/backup/export', json={'password': 'test-password'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('attachment', response.headers['content-disposition'])
        return response.json()

    def restore(self, value, **kwargs):
        return self.client.post('/api/account/backup/restore', json={
            'password': 'test-password', 'confirmation': 'OVERWRITE_MY_DATA', 'backup': value, **kwargs})

    def state(self):
        with db.connect() as connection:
            return {table: [tuple(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY 1')]
                    for table in ('users', 'auth_sessions', 'decks', 'words', 'study_sessions', 'study_items', 'attempts')}

    def test_round_trip_isolation_mapping_and_automatic_backup(self):
        value = self.export()
        def keys(node):
            if isinstance(node, dict):
                for key, child in node.items():
                    yield key
                    yield from keys(child)
            elif isinstance(node, list):
                for child in node:
                    yield from keys(child)
        self.assertFalse(set(keys(value)) & {'user_id', 'username', 'password', 'password_hash', 'role', 'auth_sessions'})
        self.assertEqual(value['decks'][0]['id'], 1)
        self.assertNotIn('private-10', json.dumps(value))
        before = self.state()
        # Import user 10's file into user 20, never use file IDs as database IDs.
        self.uid = 20
        self.assertEqual(self.restore(value).status_code, 200)
        after = self.state()
        self.assertEqual(before['users'], after['users'])
        self.assertEqual(before['auth_sessions'], after['auth_sessions'])
        for table in ('decks', 'words', 'study_sessions', 'study_items', 'attempts'):
            self.assertEqual([r for r in before[table] if r[0] == 10], [r for r in after[table] if r[0] == 10])
        result = self.export()
        expected = copy.deepcopy(value)
        expected['study_sessions'][0]['status'] = 'cancelled'
        self.assertEqual(result, expected)
        paths = list((db.DB_PATH.parent / 'backups').glob('user-20-*.json'))
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(paths[0].read_text()), value)
        with db.connect() as connection:
            self.assertFalse(connection.execute('PRAGMA foreign_key_check').fetchall())

    def test_invalid_input_and_password_leave_data_unchanged(self):
        value = self.export()
        before = self.state()
        self.assertEqual(self.restore(value, confirmation='WRONG').status_code, 422)
        self.assertEqual(self.restore(value, password='wrong').status_code, 403)
        self.assertEqual(self.client.post('/api/account/backup/export', json={'password': 'wrong'}).status_code, 403)
        mutations = [
            lambda v: v.update(version=2),
            lambda v: v.update(user_id=20),
            lambda v: v['study_items'][0].update(word_id=20),
            lambda v: v['study_sessions'][0].update(deck_id=20),
            lambda v: v['decks'][0]['words'][0].update(stage=6),
            lambda v: v['decks'][0]['words'][0].update(stage=True),
            lambda v: v['decks'][0]['words'][0].update(word=' '),
            lambda v: v['decks'][0]['words'].append(v['decks'][0]['words'][0].copy()),
            lambda v: v['decks'][0]['words'].append(dict(v['decks'][0]['words'][0], id=2, word=' APPLE ')),
            lambda v: v.update(version=True),
            lambda v: v['study_items'][0].update(hint_used=1.0),
            lambda v: v['attempts'].append(dict(v['attempts'][0], id=2)),
            lambda v: v['study_items'].append(dict(v['study_items'][0], id=2)),
            lambda v: v['attempts'][0].update(correct=True),
            lambda v: v['decks'][0].update(created_at=-1),
        ]
        for mutate in mutations:
            bad = copy.deepcopy(value)
            mutate(bad)
            self.assertEqual(self.restore(bad).status_code, 422, bad)
            self.assertEqual(self.state(), before)
        for bad in (float('inf'), float('nan')):
            value['decks'][0]['created_at'] = bad
            with self.assertRaises(backup.HTTPException) as caught:
                backup.validate(value)
            self.assertEqual(caught.exception.status_code, 422)
        self.assertFalse((db.DB_PATH.parent / 'backups').exists())

    def test_transaction_rolls_back_and_backup_failure_aborts(self):
        value = self.export()
        before = self.state()
        with db.connect() as connection:
            connection.execute('''CREATE TRIGGER reject_import BEFORE INSERT ON words
                BEGIN SELECT RAISE(ABORT, 'test rollback'); END''')
        with self.assertRaises(sqlite3.IntegrityError):
            self.restore(value)
        self.assertEqual(self.state(), before)
        with patch.object(backup, 'save_backup', side_effect=backup.HTTPException(503, 'test failure')):
            self.assertEqual(self.restore(value).status_code, 503)
        self.assertEqual(self.state(), before)

    def test_limits_and_empty_backup(self):
        value = self.export()
        with self.assertRaises(backup.HTTPException) as caught:
            backup.encode({'x': 'x' * backup.MAX_BYTES})
        self.assertEqual(caught.exception.status_code, 413)
        value.update(decks=[], study_sessions=[], study_items=[], attempts=[])
        self.assertEqual(self.restore(value).status_code, 200)
        self.assertEqual(self.export(), value)


if __name__ == '__main__':
    unittest.main()
