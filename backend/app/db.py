import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.environ.get('WORDS_DB', str(ROOT / 'data' / 'words.sqlite3')))

BASE_SCHEMA = '''
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('parent','child')), is_active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS auth_sessions (
 token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
 csrf_token TEXT NOT NULL, expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS decks (
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
 name TEXT NOT NULL, created_at REAL NOT NULL, started_at REAL
);
CREATE TABLE IF NOT EXISTS words (
 id INTEGER PRIMARY KEY, deck_id INTEGER NOT NULL REFERENCES decks(id),
 word TEXT NOT NULL, meaning TEXT NOT NULL, example TEXT NOT NULL DEFAULT '',
 example_translation TEXT NOT NULL DEFAULT '', audio TEXT NOT NULL DEFAULT '',
 phonetic TEXT NOT NULL DEFAULT '', stage INTEGER NOT NULL DEFAULT -1, due_at REAL
);
CREATE INDEX IF NOT EXISTS words_due ON words(due_at);
CREATE TABLE IF NOT EXISTS study_sessions (
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), deck_id INTEGER REFERENCES decks(id),
 kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', round_number INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS active_study ON study_sessions(user_id) WHERE status='active';
CREATE TABLE IF NOT EXISTS study_items (
 id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES study_sessions(id),
 word_id INTEGER NOT NULL REFERENCES words(id), round_number INTEGER NOT NULL,
 position INTEGER NOT NULL, hint_used INTEGER NOT NULL DEFAULT 0,
 UNIQUE(session_id,round_number,position)
);
CREATE TABLE IF NOT EXISTS attempts (
 id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL UNIQUE REFERENCES study_items(id),
 answer TEXT NOT NULL, correct INTEGER NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS dictionary_cache (
 word TEXT PRIMARY KEY, payload TEXT NOT NULL, fetched_at REAL NOT NULL
);
PRAGMA user_version=1;
'''

SCHEMA_V2 = BASE_SCHEMA + '''
ALTER TABLE study_items ADD COLUMN practice_pending INTEGER NOT NULL DEFAULT 0;
PRAGMA user_version=2;
'''

ACTIVE_INDEXES = '''
DROP INDEX active_study;
CREATE UNIQUE INDEX active_study ON study_sessions(user_id,deck_id) WHERE status='active' AND deck_id IS NOT NULL;
CREATE UNIQUE INDEX active_study_legacy ON study_sessions(user_id) WHERE status='active' AND deck_id IS NULL;
PRAGMA user_version=3;
'''
SCHEMA = SCHEMA_V2 + ACTIVE_INDEXES


def migrate(db: sqlite3.Connection) -> None:
    if db.execute('PRAGMA user_version').fetchone()[0] == 1:
        db.execute('ALTER TABLE study_items ADD COLUMN practice_pending INTEGER NOT NULL DEFAULT 0')
        db.execute('PRAGMA user_version=2')
    if db.execute('PRAGMA user_version').fetchone()[0] == 2:
        for statement in ACTIVE_INDEXES.split(';'):
            if statement.strip():
                db.execute(statement)


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def initialize():
    with connect() as db:
        db.execute('PRAGMA journal_mode=WAL')
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version == 0:
            db.executescript(SCHEMA)
        elif version in (1, 2):
            db.execute('BEGIN IMMEDIATE')
            migrate(db)
        elif version != 3:
            raise ValueError('不支持的数据库版本')
