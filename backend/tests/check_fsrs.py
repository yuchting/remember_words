"""FSRS regression checks, isolated before importing application modules."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

TEMP = tempfile.TemporaryDirectory()
os.environ['WORDS_DB'] = str(Path(TEMP.name) / 'fsrs.sqlite3')

from backend.app import db
from backend.app.fsrs_schedule import load_card, review, SCHEDULER
from backend.app.study import record_memory, finish_round

db.DB_PATH = Path(TEMP.name) / 'fsrs.sqlite3'
assert db.DB_PATH.resolve().is_relative_to(Path(TEMP.name)), 'Production DB forbidden'


class FsrsChecks(unittest.TestCase):
    def connection(self):
        connection = sqlite3.connect(':memory:')
        connection.row_factory = sqlite3.Row
        connection.executescript(db.SCHEMA)
        connection.execute("INSERT INTO users VALUES(1,'test','unused','parent',1)")
        connection.execute("INSERT INTO decks VALUES(1,1,'test',0,NULL)")
        connection.execute("INSERT INTO words(id,deck_id,word,meaning) VALUES(1,1,'hello','你好')")
        self.addCleanup(connection.close)
        return connection

    def test_library_and_recent_exposure_even_when_due(self):
        from importlib.metadata import version
        self.assertEqual(version('fsrs'), '6.3.2')
        self.assertEqual(SCHEDULER.maximum_interval, 30)
        self.assertFalse(SCHEDULER.enable_fuzzing)
        c = self.connection()
        record_memory(c, 1, False, 1000)
        original = c.execute('SELECT fsrs_card FROM words').fetchone()[0]
        card = load_card(original)
        self.assertEqual(card.due.timestamp(), 1060)
        c.execute('UPDATE words SET last_seen_at=1050')  # practice, not FSRS review
        record_memory(c, 1, True, 1060)
        self.assertEqual(c.execute('SELECT fsrs_card FROM words').fetchone()[0], original)
        self.assertEqual(c.execute('SELECT last_seen_at FROM words').fetchone()[0], 1060)
        c.execute("INSERT INTO study_sessions(id,user_id,deck_id,kind) VALUES(1,1,1,'new')")
        c.execute('INSERT INTO study_items(id,session_id,word_id,round_number,position) VALUES(1,1,1,1,0)')
        c.execute("INSERT INTO attempts(item_id,answer,correct,created_at) VALUES(1,'hello',1,1060)")
        finish_round(c, c.execute('SELECT * FROM study_sessions').fetchone(), 1060)
        self.assertEqual(c.execute('SELECT status FROM study_sessions').fetchone()[0], 'complete')
        record_memory(c, 1, True, 1660)
        self.assertNotEqual(c.execute('SELECT fsrs_card FROM words').fetchone()[0], original)

    def test_legacy_recent_exposure_does_not_initialize(self):
        c = self.connection()
        c.execute('UPDATE words SET last_seen_at=100')
        record_memory(c, 1, True, 101)
        self.assertEqual(tuple(c.execute('SELECT stage,due_at,fsrs_card FROM words').fetchone()), (0, 701, None))
        record_memory(c, 1, True, 701)
        self.assertIsNotNone(c.execute('SELECT fsrs_card FROM words').fetchone()[0])

    def test_due_reselect_scope(self):
        for multi in (False, True):
            c = self.connection()
            c.execute("INSERT INTO words(id,deck_id,word,meaning,stage,due_at,last_seen_at) VALUES(2,1,'old','旧',0,100,0)")
            c.execute('UPDATE words SET stage=0,due_at=100,last_seen_at=0')
            c.execute("INSERT INTO study_sessions(id,user_id,deck_id,kind) VALUES(1,1,?,'review')", (None if multi else 1,))
            c.execute('INSERT INTO study_items(id,session_id,word_id,round_number,position) VALUES(1,1,1,1,0)')
            c.execute("INSERT INTO attempts(item_id,answer,correct,created_at) VALUES(1,'hello',1,0)")
            finish_round(c, c.execute('SELECT * FROM study_sessions').fetchone(), 86400)
            ids = {r[0] for r in c.execute('SELECT word_id FROM study_items WHERE round_number=2')}
            self.assertEqual(ids, {1} if multi else {1, 2})

    def test_migrate_all_old_versions_without_fake_parameters(self):
        for schema in (db.BASE_SCHEMA, db.SCHEMA_V2, db.SCHEMA_V3, db.SCHEMA_V4):
            c = sqlite3.connect(':memory:')
            self.addCleanup(c.close)
            c.executescript(schema)
            c.execute("INSERT INTO users VALUES(1,'test','unused','parent',1)")
            c.execute("INSERT INTO decks VALUES(1,1,'test',0,NULL)")
            c.execute("INSERT INTO words(id,deck_id,word,meaning,stage,due_at) VALUES(1,1,'hello','你好',2,123)")
            c.execute("INSERT INTO study_sessions(id,user_id,deck_id,kind) VALUES(1,1,1,'new')")
            c.execute('INSERT INTO study_items(id,session_id,word_id,round_number,position) VALUES(1,1,1,1,0)')
            c.execute("INSERT INTO attempts(item_id,answer,correct,created_at) VALUES(1,'wrong',0,10)")
            db.migrate(c)
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], 5)
            self.assertEqual(c.execute('SELECT stage,due_at,fsrs_card FROM words').fetchone(), (2, 123, None))
            self.assertEqual(c.execute('SELECT answer FROM attempts').fetchone()[0], 'wrong')
            self.assertEqual(c.execute('SELECT count(*) FROM study_items').fetchone()[0], 1)

    def test_backup_fsrs_validation_and_old_fields(self):
        from backend.app import user_backup, database_admin
        from backend.app.main import hasher
        from fastapi import HTTPException
        c = self.connection()
        c.execute('UPDATE users SET password_hash=?', (hasher.hash('password123'),))
        record_memory(c, 1, False, 1000)
        backup = user_backup.snapshot(c, 1)
        original = dict(backup['decks'][0]['words'][0])
        user_backup.replace(c, 1, backup)
        restored = user_backup.snapshot(c, 1)['decks'][0]['words'][0]
        self.assertEqual(original, restored)
        c.row_factory = None
        database_admin._validate(c)
        c.execute('PRAGMA query_only=OFF')
        backup['decks'][0]['words'][0]['fsrs_card'] = '{}'
        with self.assertRaises(HTTPException):
            user_backup.validate(backup)
        c.execute("UPDATE words SET fsrs_card='{}'")
        with self.assertRaises(ValueError):
            database_admin._validate(c)

    def test_json_boundary(self):
        card = review(None, 1, True, 'normal', 1000, None)
        valid = card.to_dict()
        for key, value in [('stability', float('nan')), ('difficulty', 11), ('state', True), ('step', 100), ('due', '9999-12-31T23:59:59+01:00'), ('last_review', None), ('card_id', -1)]:
            with self.subTest(key=key):
                broken = valid | {key: value}
                with self.assertRaises(ValueError):
                    load_card(json.dumps(broken))
        with self.assertRaises(ValueError):
            load_card(' ' * 2049)


if __name__ == '__main__':
    unittest.main()
