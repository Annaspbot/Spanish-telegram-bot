"""
database.py
Слой работы с SQLite: пользователи, слова/фразы, глаголы, спряжения,
прогресс по интервальному повторению (упрощённый алгоритм SM-2).
"""

import json
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

        # Сессия текущего урока — хранится в БД (не в памяти процесса), чтобы
        # пользователь мог продолжить урок с того же места даже после
        # перезапуска бота (например, при редеплое на Railway).
        c.execute("""
        CREATE TABLE IF NOT EXISTS lesson_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            item_type TEXT NOT NULL,       -- пока только 'word'
            item_ids TEXT NOT NULL,        -- JSON-список id в порядке показа за урок
            current_index INTEGER DEFAULT 0,
            correct_count INTEGER DEFAULT 0,
            almost_count INTEGER DEFAULT 0,
            wrong_count INTEGER DEFAULT 0,
            started_at INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'completed' | 'cancelled'
            finished_at INTEGER  -- unix timestamp, задаётся при завершении (для статистики по дням)
        )
        """)

        # Миграция для баз, созданных до появления этой колонки (например,
        # уже развёрнутой на Railway) — CREATE TABLE IF NOT EXISTS выше её
        # не добавит, если таблица уже существует, поэтому добавляем отдельно.
        try:
            c.execute("ALTER TABLE lesson_sessions ADD COLUMN finished_at INTEGER")
        except sqlite3.OperationalError:
            pass  # колонка уже есть — миграция уже применялась раньше

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


# ---------------------------------------------------------------------------
# Слова по id (нужно для восстановления сессии урока по сохранённым id)
# ---------------------------------------------------------------------------

def get_word_by_id(word_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM words WHERE id = ?", (word_id,)).fetchone()
        return dict(row) if row else None


def get_conjugation_by_id(conjugation_id: int):
    """Аналог get_word_by_id, но для конкретной формы спряжения — с join на
    verbs, чтобы сразу получить инфинитив и перевод глагола (та же форма
    данных, что возвращает get_due_items для item_type='conjugation')."""
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT c.*, v.infinitive, v.translation as verb_translation
            FROM conjugations c
            JOIN verbs v ON v.id = c.verb_id
            WHERE c.id = ?
            """,
            (conjugation_id,),
        ).fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Сессии урока — хранятся в БД, а не в памяти процесса, поэтому переживают
# перезапуск бота (важно для деплоя на Railway, где рестарты случаются
# при каждом обновлении кода).
# ---------------------------------------------------------------------------

def create_lesson_session(user_id: int, item_type: str, item_ids: list):
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO lesson_sessions
                (user_id, item_type, item_ids, current_index,
                 correct_count, almost_count, wrong_count, started_at, status)
            VALUES (?, ?, ?, 0, 0, 0, 0, ?, 'active')
            """,
            (user_id, item_type, json.dumps(item_ids), int(time.time())),
        )
        conn.commit()
        session_id = cur.lastrowid
        row = conn.execute(
            "SELECT * FROM lesson_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return dict(row)


def get_active_session(user_id: int, item_type: str = None):
    """
    Если item_type задан — ищет активную сессию именно этого режима (как раньше).
    Если item_type=None — ищет ЛЮБУЮ активную сессию пользователя, независимо
    от режима. Это нужно для универсального выхода в меню (см. reset_user_state
    и nav:* колбэки в bot.py) — новый режим, который в будущем начнёт хранить
    свою сессию в этой же таблице, автоматически подхватится без правок здесь.
    """
    with get_conn() as conn:
        if item_type:
            row = conn.execute(
                """
                SELECT * FROM lesson_sessions
                WHERE user_id = ? AND item_type = ? AND status = 'active'
                ORDER BY id DESC LIMIT 1
                """,
                (user_id, item_type),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT * FROM lesson_sessions
                WHERE user_id = ? AND status = 'active'
                ORDER BY id DESC LIMIT 1
                """,
                (user_id,),
            ).fetchone()
        return dict(row) if row else None


def get_session(session_id: int):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM lesson_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None


def record_lesson_answer(session_id: int, outcome: str):
    """
    outcome: 'correct' | 'almost' | 'wrong'.
    Увеличивает соответствующий счётчик и сдвигает current_index на 1.
    Возвращает обновлённую строку сессии.
    """
    column = {
        "correct": "correct_count",
        "almost": "almost_count",
        "wrong": "wrong_count",
    }[outcome]

    with get_conn() as conn:
        conn.execute(
            f"""
            UPDATE lesson_sessions
            SET {column} = {column} + 1,
                current_index = current_index + 1
            WHERE id = ?
            """,
            (session_id,),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM lesson_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return dict(row)


def complete_lesson_session(session_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE lesson_sessions SET status = 'completed', finished_at = ? WHERE id = ?",
            (int(time.time()), session_id),
        )


def cancel_active_sessions(user_id: int, item_type: str = None):
    """
    Отменяет активные сессии пользователя (ставит status='cancelled'), чтобы
    get_active_session их больше не находил. Не показывает статистику —
    в отличие от complete_lesson_session, это "тихий" выход без экрана
    результатов (используется при полном сбросе через /menu, /start,
    кнопку "🏠 Главное меню").
    Если item_type не задан — отменяет сессии ЛЮБОГО режима пользователя,
    что делает сброс универсальным для всех текущих и будущих режимов,
    использующих эту таблицу.
    """
    with get_conn() as conn:
        if item_type:
            conn.execute(
                """
                UPDATE lesson_sessions SET status = 'cancelled'
                WHERE user_id = ? AND item_type = ? AND status = 'active'
                """,
                (user_id, item_type),
            )
        else:
            conn.execute(
                """
                UPDATE lesson_sessions SET status = 'cancelled'
                WHERE user_id = ? AND status = 'active'
                """,
                (user_id,),
            )


def skip_lesson_item(session_id: int):
    """
    Пропускает текущий элемент урока без записи в счётчики correct/almost/wrong.
    Из UI сейчас не вызывается (кнопку "Дальше" убрали в версии 2.0), но
    оставлена как утилита — может пригодиться для будущих режимов.
    """
    with get_conn() as conn:
        conn.execute(
            "UPDATE lesson_sessions SET current_index = current_index + 1 WHERE id = ?",
            (session_id,),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM lesson_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return dict(row)


def count_new_items(user_id: int, item_type: str) -> int:
    """
    Сколько ещё есть предметов (слов или форм спряжения), которые
    пользователь НИ РАЗУ не видел (нет строки в user_progress). Используется,
    чтобы показать "новые слова на сегодня закончились" вместо обычного
    приветствия урока, когда остались только повторения.
    """
    with get_conn() as conn:
        if item_type == "word":
            row = conn.execute(
                """
                SELECT COUNT(*) c FROM words w
                WHERE w.id NOT IN (
                    SELECT item_id FROM user_progress
                    WHERE user_id = ? AND item_type = 'word'
                )
                """,
                (user_id,),
            ).fetchone()
        elif item_type == "conjugation":
            row = conn.execute(
                """
                SELECT COUNT(*) c FROM conjugations c
                WHERE c.id NOT IN (
                    SELECT item_id FROM user_progress
                    WHERE user_id = ? AND item_type = 'conjugation'
                )
                """,
                (user_id,),
            ).fetchone()
        else:
            return 0
        return row["c"] if row else 0


def get_admin_stats():
    """
    Статистика по ВСЕМ пользователям бота (не по одному) — для владельца.
    Использует те же таблицы, что и get_extended_stats, просто без фильтра
    по user_id, плюс окна активности (сегодня/неделя/месяц).
    """
    now = int(time.time())
    day_ago = now - 86400
    week_ago = now - 7 * 86400
    month_ago = now - 30 * 86400

    with get_conn() as conn:
        total_users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]

        new_today = conn.execute(
            "SELECT COUNT(*) c FROM users WHERE created_at >= ?", (day_ago,)
        ).fetchone()["c"]

        def active_since(ts):
            row = conn.execute(
                """
                SELECT COUNT(DISTINCT user_id) c FROM lesson_sessions
                WHERE started_at >= ?
                   OR (finished_at IS NOT NULL AND finished_at >= ?)
                """,
                (ts, ts),
            ).fetchone()
            return row["c"]

        active_today = active_since(day_ago)
        active_week = active_since(week_ago)
        active_month = active_since(month_ago)

        totals = conn.execute(
            """
            SELECT
                COUNT(*) AS sessions,
                COALESCE(SUM(correct_count), 0) AS correct,
                COALESCE(SUM(almost_count), 0) AS almost,
                COALESCE(SUM(wrong_count), 0) AS wrong,
                COALESCE(SUM(correct_count + almost_count + wrong_count), 0) AS total_items,
                COALESCE(SUM(
                    CASE WHEN finished_at IS NOT NULL
                         THEN finished_at - started_at ELSE 0 END
                ), 0) AS time_seconds
            FROM lesson_sessions
            WHERE status = 'completed'
            """
        ).fetchone()

        sessions = totals["sessions"]
        avg_items = round(totals["total_items"] / sessions, 1) if sessions else 0
        avg_minutes = round(totals["time_seconds"] / sessions / 60, 1) if sessions else 0

        word_users = conn.execute(
            "SELECT COUNT(DISTINCT user_id) c FROM lesson_sessions "
            "WHERE item_type = 'word' AND status = 'completed'"
        ).fetchone()["c"]
        conj_users = conn.execute(
            "SELECT COUNT(DISTINCT user_id) c FROM lesson_sessions "
            "WHERE item_type = 'conjugation' AND status = 'completed'"
        ).fetchone()["c"]

        total_answers = totals["correct"] + totals["almost"] + totals["wrong"]
        accuracy = round(totals["correct"] / total_answers * 100) if total_answers else 0

        return {
            "total_users": total_users,
            "new_today": new_today,
            "active_today": active_today,
            "active_week": active_week,
            "active_month": active_month,
            "sessions_completed": sessions,
            "avg_items_per_session": avg_items,
            "avg_session_minutes": avg_minutes,
            "accuracy": accuracy,
            "word_users": word_users,
            "conj_users": conj_users,
        }


def get_extended_stats(user_id: int):
    """
    Расширенная статистика — вся строится из уже существующих данных
    (lesson_sessions + user_progress), без отдельной системы XP/уровней.
    """
    with get_conn() as conn:
        total_words = conn.execute("SELECT COUNT(*) c FROM words").fetchone()["c"]
        total_conj = conn.execute("SELECT COUNT(*) c FROM conjugations").fetchone()["c"]

        seen_words = conn.execute(
            "SELECT COUNT(*) c FROM user_progress WHERE user_id=? AND item_type='word'",
            (user_id,),
        ).fetchone()["c"]
        mastered_words = conn.execute(
            "SELECT COUNT(*) c FROM user_progress WHERE user_id=? AND item_type='word' AND repetitions>=5",
            (user_id,),
        ).fetchone()["c"]
        seen_conj = conn.execute(
            "SELECT COUNT(*) c FROM user_progress WHERE user_id=? AND item_type='conjugation'",
            (user_id,),
        ).fetchone()["c"]
        mastered_conj = conn.execute(
            "SELECT COUNT(*) c FROM user_progress WHERE user_id=? AND item_type='conjugation' AND repetitions>=5",
            (user_id,),
        ).fetchone()["c"]

        totals = conn.execute(
            """
            SELECT
                COUNT(*) AS sessions,
                COALESCE(SUM(correct_count), 0) AS correct,
                COALESCE(SUM(almost_count), 0) AS almost,
                COALESCE(SUM(wrong_count), 0) AS wrong,
                COALESCE(SUM(
                    CASE WHEN finished_at IS NOT NULL
                         THEN finished_at - started_at ELSE 0 END
                ), 0) AS time_seconds
            FROM lesson_sessions
            WHERE user_id = ? AND status = 'completed'
            """,
            (user_id,),
        ).fetchone()

        by_day = conn.execute(
            """
            SELECT
                date(finished_at, 'unixepoch') AS day,
                SUM(correct_count + almost_count + wrong_count) AS items
            FROM lesson_sessions
            WHERE user_id = ? AND status = 'completed' AND finished_at IS NOT NULL
            GROUP BY day
            """,
            (user_id,),
        ).fetchall()

        days_active = len(by_day)
        best_day = max((row["items"] for row in by_day), default=0)
        avg_per_day = round(sum(row["items"] for row in by_day) / days_active) if days_active else 0

        return {
            "total_words": total_words,
            "total_conjugations": total_conj,
            "seen_words": seen_words,
            "mastered_words": mastered_words,
            "seen_conjugations": seen_conj,
            "mastered_conjugations": mastered_conj,
            "sessions_completed": totals["sessions"],
            "correct": totals["correct"],
            "almost": totals["almost"],
            "wrong": totals["wrong"],
            "time_seconds": totals["time_seconds"],
            "days_active": days_active,
            "best_day": best_day,
            "avg_per_day": avg_per_day,
        }
