"""
database.py
Слой работы с SQLite: пользователи, слова/фразы, глаголы, спряжения,
прогресс по интервальному повторению (упрощённый алгоритм SM-2).
"""

import os
import sqlite3
import time
from contextlib import contextmanager

# По умолчанию — файл рядом с кодом (как и раньше, для локального запуска).
# На Railway с подключённым Volume задайте DB_PATH через переменную окружения,
# указав путь внутри смонтированного тома (например /data/spanish_bot.db),
# иначе база будет обнуляться при каждом передеплое.
DB_PATH = os.environ.get("DB_PATH", "spanish_bot.db")


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        c = conn.cursor()

        c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            created_at INTEGER
        )
        """)

        # Словарные единицы: слова, фразы, сленг
        c.execute("""
        CREATE TABLE IF NOT EXISTS words (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spanish TEXT NOT NULL,
            translation TEXT NOT NULL,
            category TEXT,           -- напр. 'noun', 'adjective', 'phrase', 'slang', 'verb_inf'
            level TEXT,              -- 'A2', 'B1', 'B2'
            example TEXT,
            UNIQUE(spanish, translation)
        )
        """)

        # Глаголы (инфинитив)
        c.execute("""
        CREATE TABLE IF NOT EXISTS verbs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            infinitive TEXT NOT NULL UNIQUE,
            translation TEXT NOT NULL,
            is_irregular INTEGER DEFAULT 0,
            level TEXT
        )
        """)

        # Формы спряжения: конкретное лицо/время для конкретного глагола
        c.execute("""
        CREATE TABLE IF NOT EXISTS conjugations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            verb_id INTEGER NOT NULL REFERENCES verbs(id) ON DELETE CASCADE,
            tense TEXT NOT NULL,      -- 'indefinido' | 'imperfecto'
            pronoun TEXT NOT NULL,    -- 'yo','tú','él/ella/usted','nosotros','vosotros','ellos/ellas/ustedes'
            form TEXT NOT NULL,
            UNIQUE(verb_id, tense, pronoun)
        )
        """)

        # Прогресс пользователя по каждой обучающей единице (слово или форма глагола)
        c.execute("""
        CREATE TABLE IF NOT EXISTS user_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            item_type TEXT NOT NULL,   -- 'word' | 'conjugation'
            item_id INTEGER NOT NULL,
            repetitions INTEGER DEFAULT 0,
            ease_factor REAL DEFAULT 2.5,
            interval_days REAL DEFAULT 0,
            next_review INTEGER DEFAULT 0,   -- unix timestamp
            last_result TEXT,
            UNIQUE(user_id, item_type, item_id)
        )
        """)

        conn.commit()


def ensure_user(user_id: int, username: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, username, created_at) VALUES (?, ?, ?)",
            (user_id, username, int(time.time())),
        )


def add_word(spanish, translation, category, level, example=""):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO words (spanish, translation, category, level, example) "
            "VALUES (?, ?, ?, ?, ?)",
            (spanish, translation, category, level, example),
        )


def add_verb(infinitive, translation, is_irregular, level):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO verbs (infinitive, translation, is_irregular, level) "
            "VALUES (?, ?, ?, ?)",
            (infinitive, translation, int(is_irregular), level),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM verbs WHERE infinitive = ?", (infinitive,)
        ).fetchone()
        return row["id"] if row else cur.lastrowid


def add_conjugation(verb_id, tense, pronoun, form):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO conjugations (verb_id, tense, pronoun, form) "
            "VALUES (?, ?, ?, ?)",
            (verb_id, tense, pronoun, form),
        )


def count_words():
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) c FROM words").fetchone()["c"]


def count_conjugations():
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) c FROM conjugations").fetchone()["c"]


def get_due_items(user_id: int, item_type: str, limit: int = 1):
    """
    Возвращает предметы для повторения:
    - сначала те, что уже показывались и наступил срок повторения (next_review <= now)
    - затем новые (без записи в user_progress), если старых не хватает
    """
    now = int(time.time())
    with get_conn() as conn:
        if item_type == "word":
            due = conn.execute(
                """
                SELECT w.* FROM words w
                JOIN user_progress p
                  ON p.item_id = w.id AND p.item_type = 'word' AND p.user_id = ?
                WHERE p.next_review <= ?
                ORDER BY p.next_review ASC
                LIMIT ?
                """,
                (user_id, now, limit),
            ).fetchall()

            remaining = limit - len(due)
            new_items = []
            if remaining > 0:
                new_items = conn.execute(
                    """
                    SELECT w.* FROM words w
                    WHERE w.id NOT IN (
                        SELECT item_id FROM user_progress
                        WHERE user_id = ? AND item_type = 'word'
                    )
                    ORDER BY RANDOM()
                    LIMIT ?
                    """,
                    (user_id, remaining),
                ).fetchall()
            return [dict(r) for r in due] + [dict(r) for r in new_items]

        elif item_type == "conjugation":
            due = conn.execute(
                """
                SELECT c.*, v.infinitive, v.translation as verb_translation
                FROM conjugations c
                JOIN verbs v ON v.id = c.verb_id
                JOIN user_progress p
                  ON p.item_id = c.id AND p.item_type = 'conjugation' AND p.user_id = ?
                WHERE p.next_review <= ?
                ORDER BY p.next_review ASC
                LIMIT ?
                """,
                (user_id, now, limit),
            ).fetchall()

            remaining = limit - len(due)
            new_items = []
            if remaining > 0:
                new_items = conn.execute(
                    """
                    SELECT c.*, v.infinitive, v.translation as verb_translation
                    FROM conjugations c
                    JOIN verbs v ON v.id = c.verb_id
                    WHERE c.id NOT IN (
                        SELECT item_id FROM user_progress
                        WHERE user_id = ? AND item_type = 'conjugation'
                    )
                    ORDER BY RANDOM()
                    LIMIT ?
                    """,
                    (user_id, remaining),
                ).fetchall()
            return [dict(r) for r in due] + [dict(r) for r in new_items]

        return []


def get_progress(user_id, item_type, item_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM user_progress WHERE user_id=? AND item_type=? AND item_id=?",
            (user_id, item_type, item_id),
        ).fetchone()
        return dict(row) if row else None


def upsert_progress(user_id, item_type, item_id, repetitions, ease_factor,
                     interval_days, next_review, last_result):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO user_progress
                (user_id, item_type, item_id, repetitions, ease_factor,
                 interval_days, next_review, last_result)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, item_type, item_id) DO UPDATE SET
                repetitions=excluded.repetitions,
                ease_factor=excluded.ease_factor,
                interval_days=excluded.interval_days,
                next_review=excluded.next_review,
                last_result=excluded.last_result
            """,
            (user_id, item_type, item_id, repetitions, ease_factor,
             interval_days, next_review, last_result),
        )


def get_stats(user_id):
    with get_conn() as conn:
        total_words = conn.execute("SELECT COUNT(*) c FROM words").fetchone()["c"]
        total_conj = conn.execute("SELECT COUNT(*) c FROM conjugations").fetchone()["c"]
        seen_words = conn.execute(
            "SELECT COUNT(*) c FROM user_progress WHERE user_id=? AND item_type='word'",
            (user_id,),
        ).fetchone()["c"]
        seen_conj = conn.execute(
            "SELECT COUNT(*) c FROM user_progress WHERE user_id=? AND item_type='conjugation'",
            (user_id,),
        ).fetchone()["c"]
        mastered_words = conn.execute(
            "SELECT COUNT(*) c FROM user_progress WHERE user_id=? AND item_type='word' AND repetitions>=5",
            (user_id,),
        ).fetchone()["c"]
        return {
            "total_words": total_words,
            "total_conjugations": total_conj,
            "seen_words": seen_words,
            "seen_conjugations": seen_conj,
            "mastered_words": mastered_words,
        }
