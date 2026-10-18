"""运行：.venv/bin/python -m unittest backend.tests.check_database_admin -v"""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

from backend.tests import check_core
from backend.app import db, database_admin
from backend.app.main import app, hasher, parent
from fastapi import HTTPException

if not any(route.path == '/api/admin/database/export' for route in app.routes):
    database_admin.register(app, parent, hasher)


class DatabaseAdminChecks(unittest.TestCase):
    login = check_core.CoreChecks.login
    post = check_core.CoreChecks.post
    make_deck = check_core.CoreChecks.make_deck

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'live.sqlite3'
        patcher = patch.object(db, 'DB_PATH', self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        check_core.CoreChecks.setUp(self)

    def export(self, password='password123'):
        return self.post('/api/admin/database/export', {'password': password})

    def restore(self, payload, password='password123', confirmation=database_admin.CONFIRMATION):
        return self.client.post('/api/admin/database/restore', content=payload, headers=self.headers | {
            'X-Admin-Password': quote(password, safe=''), 'X-Restore-Confirmation': confirmation})

    def uploaded(self, mutate=None):
        path = Path(self.directory.name) / 'upload.sqlite3'
        with closing(sqlite3.connect(path)) as connection:
            connection.executescript(db.SCHEMA)
            connection.execute('INSERT INTO users VALUES(10,?,?,?,1)',
                               ('restored', hasher.hash('新密码 password'), 'parent'))
            connection.execute("INSERT INTO auth_sessions VALUES('incoming-token',10,'csrf',9999999999)")
            if mutate:
                mutate(connection)
            connection.commit()
        return path.read_bytes()

    def test_permissions_password_confirmation(self):
        self.assertEqual(self.export().status_code, 403)
        self.assertEqual(self.restore(b'bad').status_code, 403)
        self.login('parent')
        self.assertEqual(self.export('bad').status_code, 401)
        self.assertEqual(self.restore(b'bad', password='bad').status_code, 401)
        self.assertEqual(self.restore(b'bad', confirmation='yes').status_code, 403)
        self.client.cookies.clear()
        self.assertEqual(self.export().status_code, 401)

    def test_export_readable_without_sessions(self):
        self.make_deck()
        self.login('parent')
        result = self.export()
        self.assertEqual(result.status_code, 200, result.text if result.status_code != 200 else '')
        self.assertIn('attachment', result.headers['content-disposition'])
        path = Path(self.directory.name) / 'export.sqlite3'
        path.write_bytes(result.content)
        with closing(sqlite3.connect(path)) as saved:
            self.assertEqual(saved.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(saved.execute('SELECT count(*) FROM auth_sessions').fetchone()[0], 0)
            self.assertEqual(saved.execute('SELECT count(*) FROM words').fetchone()[0], 2)
        with db.connect() as current:
            self.assertGreater(current.execute('SELECT count(*) FROM auth_sessions').fetchone()[0], 0)

    def test_invalid_upload_preserves_current(self):
        self.make_deck()
        self.login('parent')
        cases = [b'not sqlite',
                 self.uploaded(lambda c: c.execute('PRAGMA user_version=99'))]
        # 每次重新创建输入库，避免同名账户重复。
        mutations = [
            "CREATE TABLE extra(id)",
            "CREATE VIEW malicious AS SELECT * FROM users",
            "CREATE TRIGGER malicious AFTER INSERT ON users BEGIN DELETE FROM words; END",
            'DROP INDEX active_study',
            "UPDATE users SET is_active=0",
            "UPDATE users SET password_hash='not-argon2'",
            "INSERT INTO decks VALUES(1,999,'orphan',1,NULL)",
        ]
        for sql in mutations:
            (Path(self.directory.name) / 'upload.sqlite3').unlink()
            cases.append(self.uploaded(lambda c, sql=sql: c.execute(sql)))
        for payload in cases:
            with self.subTest(size=len(payload)):
                self.assertEqual(self.restore(payload).status_code, 422)
                self.assertEqual(self.client.get('/api/auth/me').status_code, 200)
                with db.connect() as current:
                    self.assertEqual(current.execute('SELECT count(*) FROM words').fetchone()[0], 2)
        self.assertFalse((self.path.parent / 'backups').exists())

    def test_unsafe_or_malformed_hash_preserves_current(self):
        self.make_deck()
        self.login('parent')
        salt = 'c2FsdHNhbHQ'
        digest = 'aGFzaGhhc2hoYXNoaGFzaA'
        valid = f'$argon2id$v=19$m=65536,t=3,p=4${salt}${digest}'
        invalid = [
            valid.replace('m=65536', 'm=262145'),
            valid.replace('t=3', 't=11'),
            valid.replace('p=4', 'p=17'),
            valid.replace('m=65536', 'm=0'),
            valid.replace('m=65536', 'm=31'),
            valid.replace('t=3', 't=0'),
            valid.replace('p=4', 'p=0'),
            valid.replace('v=19', 'v=20'),
            valid.replace(salt, 'c2FsdA'),
            valid.replace(digest, 'aGFzaA'),
            valid.replace(salt, 'c2FsdHNhbHRzYWx0' * 8),
            valid.replace(digest, 'aGFzaGhhc2hoYXNo' * 8),
            valid.replace(salt, '!!!!!!!!!!!'),
            valid.replace(salt, 'A' * 13),
            valid.replace(salt, 'c2FsdHNhbHR'),  # 非规范 Base64 尾位。
            valid.replace('t=3', 't=-1'),
            valid.replace('p=4', 'p=4,p=4'),
            valid + 'x' * 513,
        ]
        for password_hash in invalid:
            with self.subTest(password_hash=password_hash[:100]):
                path = Path(self.directory.name) / 'upload.sqlite3'
                path.unlink(missing_ok=True)
                payload = self.uploaded(lambda c: c.execute(
                    'UPDATE users SET password_hash=?', (password_hash,)))
                result = self.restore(payload)
                self.assertEqual(result.status_code, 422, result.text)
                self.assertEqual(self.client.get('/api/auth/me').status_code, 200)
                with db.connect() as current:
                    self.assertEqual(current.execute('SELECT count(*) FROM words').fetchone()[0], 2)
                    self.assertEqual(current.execute("SELECT count(*) FROM users WHERE username='parent'").fetchone()[0], 1)
        self.assertFalse((self.path.parent / 'backups').exists())

    def test_restore_overwrites_revokes_and_backs_up(self):
        self.make_deck()
        self.login('parent')
        result = self.restore(self.uploaded())
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()['ok'])
        name = result.json()['backup']
        self.assertEqual(Path(name).name, name)
        backup = self.path.parent / 'backups' / name
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        with closing(sqlite3.connect(backup)) as saved:
            self.assertEqual(saved.execute('SELECT count(*) FROM words').fetchone()[0], 2)
            self.assertEqual(saved.execute('SELECT count(*) FROM auth_sessions').fetchone()[0], 0)
        with db.connect() as current:
            self.assertEqual(current.execute('SELECT username FROM users').fetchall()[0][0], 'restored')
            self.assertEqual(current.execute('SELECT count(*) FROM users').fetchone()[0], 1)
            self.assertEqual(current.execute('SELECT count(*) FROM words').fetchone()[0], 0)
            self.assertEqual(current.execute('SELECT count(*) FROM auth_sessions').fetchone()[0], 0)
        self.assertEqual(self.client.get('/api/auth/me').status_code, 401)
        login = self.client.post('/api/auth/login', json={'username': 'restored', 'password': '新密码 password'})
        self.assertEqual(login.status_code, 200, login.text)
        self.headers = {'X-CSRF-Token': login.json()['csrf_token']}
        self.assertEqual(self.restore(b'bad', password='新密码 password').status_code, 422)

    def test_limit(self):
        self.login('parent')
        with patch.object(database_admin, 'MAX_BYTES', 10):
            self.assertEqual(self.restore(b'x' * 11).status_code, 413)
            self.assertEqual(self.export().status_code, 413)

    def test_failed_restore_rolls_back(self):
        self.make_deck()
        class BrokenSnapshot:
            def backup(self, target):
                target.execute('DELETE FROM words')
                target.commit()
                raise sqlite3.OperationalError('simulated write failure')
        with self.assertRaises(HTTPException) as error:
            database_admin._restore(BrokenSnapshot())
        self.assertEqual(error.exception.status_code, 500)
        self.assertIn('已回滚', error.exception.detail)
        with db.connect() as current:
            self.assertEqual(current.execute('SELECT count(*) FROM words').fetchone()[0], 2)
            self.assertEqual(current.execute('SELECT count(*) FROM auth_sessions').fetchone()[0], 0)
        self.assertEqual(len(list((self.path.parent / 'backups').glob('pre-restore-*.sqlite3'))), 1)


if __name__ == '__main__':
    unittest.main()
