"""管理员命令检查：只操作临时数据库。"""
import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from argon2 import PasswordHasher

from backend.app import cli, db


class AdminTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(db, 'DB_PATH', Path(self.temp.name) / 'words.sqlite3')
        patcher.start()
        self.addCleanup(patcher.stop)
        db.initialize()
        self.parser = argparse.ArgumentParser()

    def run_admin(self, answers: list[str], passwords: list[str]) -> None:
        with patch('builtins.input', side_effect=answers), patch('getpass.getpass', side_effect=passwords):
            cli.manage_admin(self.parser)

    def test_create_reset_and_isolation(self) -> None:
        self.run_admin([' ADMIN '], ['password1', 'password1'])
        with db.connect() as connection:
            parent = connection.execute('SELECT * FROM users').fetchone()
            self.assertEqual(parent['username'], 'admin')
            connection.execute("INSERT INTO users VALUES(20,'other','unchanged','parent',1)")
            connection.execute("INSERT INTO users VALUES(30,'child','unchanged','child',1)")
            for uid in (parent['id'], 20, 30):
                connection.execute('INSERT INTO auth_sessions VALUES(?,?,?,?)', (str(uid), uid, 'csrf', 9999999999))
        self.run_admin(['1', 'y'], ['password2', 'password2'])
        with db.connect() as connection:
            users = connection.execute('SELECT * FROM users ORDER BY id').fetchall()
            self.assertTrue(PasswordHasher().verify(users[0]['password_hash'], 'password2'))
            self.assertEqual([row['password_hash'] for row in users[1:]], ['unchanged', 'unchanged'])
            self.assertEqual([row[0] for row in connection.execute('SELECT user_id FROM auth_sessions ORDER BY user_id')], [20, 30])

    def test_invalid_password_cancel_and_disabled_do_not_write(self) -> None:
        self.run_admin(['admin'], ['password1', 'password1'])
        with db.connect() as connection:
            before = tuple(connection.execute('SELECT * FROM users').fetchone())
        for passwords in (['short'], ['password2', 'different']):
            with self.assertRaises(SystemExit):
                self.run_admin(['y'], passwords)
        self.run_admin(['n'], [])
        with db.connect() as connection:
            self.assertEqual(tuple(connection.execute('SELECT * FROM users').fetchone()), before)
            connection.execute('UPDATE users SET is_active=0')
        with self.assertRaises(SystemExit):
            self.run_admin([], [])
        with db.connect() as connection:
            row = connection.execute('SELECT * FROM users').fetchone()
            self.assertEqual(row['password_hash'], before[2])
            self.assertEqual(row['is_active'], 0)

    def test_existing_child_username_not_promoted(self) -> None:
        with db.connect() as connection:
            connection.execute("INSERT INTO users VALUES(1,'child','unchanged','child',1)")
        with self.assertRaises(SystemExit):
            self.run_admin(['child'], ['password1', 'password1'])
        with db.connect() as connection:
            self.assertEqual(connection.execute('SELECT role,password_hash FROM users').fetchone()[:], ('child', 'unchanged'))


if __name__ == '__main__':
    unittest.main()
