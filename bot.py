# -*- coding: utf-8 -*-
"""
bot.py
Телеграм-бот для изучения испанского: слова/фразы/сленг (A2-B2) +
тренажёр спряжений глаголов (Pretérito Indefinido и Imperfecto).

Версия 2.0 — оба режима идут единым, непрерывным потоком без лишних кнопок:
ответил -> сразу увидел результат -> через ~1 сек сразу следующее задание.
Во время тренировки доступны только 2 постоянные кнопки: Главное меню и
Закончить тренировку. Оба режима используют одну и ту же архитектуру сессии
в БД (таблица lesson_sessions), поэтому ощущаются как единое приложение,
а не как два разных бота.

Запуск:
    python bot.py
Перед первым запуском один раз выполните:
    python seed_db.py
"""

import asyncio
import json
import logging
import os
import random
import sys
import time

try:
    # Только для локальной разработки: подтягивает переменные из файла .env.
    # На Railway (и любом другом хостинге) переменные окружения задаются
    # платформой напрямую, поэтому наличие python-dotenv там не обязательно —
    # импорт обёрнут в try/except, чтобы не падать, если его нет.
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import database as db
import spaced_repetition as sr
from text_utils import compare_answer, accent_hint

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
# Ограничивает команду /admin только владельцем бота. Если не задан —
# команда /admin молча ничего не делает ни для кого (безопасное поведение
# по умолчанию, а не "открыто для всех").
ADMIN_USER_ID = os.environ.get("ADMIN_USER_ID")

router = Router()

# Сколько заданий дать за одну тренировку (слов или форм глаголов). Сессия
# хранится в БД (таблица lesson_sessions), а не в памяти процесса — поэтому
# переживает перезапуск бота (важно для Railway, где рестарт происходит при
# каждом деплое).
LESSON_SIZE = 20

# Небольшая пауза перед автоматическим показом следующего задания — чтобы
# пользователь успел увидеть результат, но не пришлось ничего нажимать.
AUTO_ADVANCE_DELAY = 0.9

PRAISE_PHRASES = [
    "🎉 Отлично!",
    "🚀 Уже намного лучше!",
    "⭐ Почти идеально!",
    "🔥 Так держать!",
    "💪 Отличная работа!",
]


# ---------------------------------------------------------------------------
# Универсальный выход в главное меню.
#
# Работает одинаково для ЛЮБОГО режима обучения — текущего и будущего —
# без необходимости писать отдельный обработчик выхода для каждого режима.
# Правило простое: если новый режим хранит сессию в таблице lesson_sessions —
# cancel_active_sessions уже достаточно; если у него будет собственное
# in-memory хранилище — он один раз регистрирует функцию очистки через
# @register_cleanup, и она вызовется автоматически вместе со всеми остальными.
# ---------------------------------------------------------------------------

_cleanup_hooks: list = []


def register_cleanup(fn):
    """Декоратор: регистрирует функцию очистки in-memory состояния конкретного
    режима. Вызывается автоматически внутри reset_user_state."""
    _cleanup_hooks.append(fn)
    return fn


async def reset_user_state(user_id: int, state: FSMContext):
    """Полный сброс пользователя: FSM-состояние (на будущее — сейчас не
    используется ни одним режимом), все активные сессии в БД (независимо от
    режима) и in-memory состояние всех зарегистрированных режимов.
    Вызывается из /start, /menu и кнопки '🏠 Главное меню'."""
    await state.clear()
    db.cancel_active_sessions(user_id)
    for hook in _cleanup_hooks:
        hook(user_id)


# ---------------------------------------------------------------------------
# Клавиатуры
# ---------------------------------------------------------------------------

def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📚 Учить слова/фразы", callback_data="menu_learn")],
        [InlineKeyboardButton(text="🔤 Тренажёр спряжений", callback_data="menu_verbs")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="menu_stats")],
    ])


def nav_kb() -> InlineKeyboardMarkup:
    """
    Единственная клавиатура во время любой тренировки (слова, спряжения и
    любые будущие режимы) — ровно 2 постоянные кнопки. Кнопки "Дальше" больше
    нет: после ответа бот сам показывает следующее задание.
    """
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:menu"),
        InlineKeyboardButton(text="❌ Закончить тренировку", callback_data="nav:stop"),
    ]])


TENSE_LABELS = {
    "indefinido": "Pretérito Indefinido (законченное прошедшее)",
    "imperfecto": "Pretérito Imperfecto (незаконченное/повторяющееся прошедшее)",
}

CATEGORY_LABELS = {
    "verb_inf": "глагол",
    "noun": "существительное",
    "adjective": "прилагательное",
    "phrase": "фраза/выражение",
    "slang": "разговорное/сленг",
}

KIND_LABELS = {
    "word": "слов",
    "conjugation": "форм глаголов",
}


def friendly_interval(days: float) -> str:
    """Человеко-понятный текст вместо сырого числа дней."""
    if days < 1:
        return "совсем скоро"
    d = round(days)
    if d == 1:
        return "завтра"
    if d < 7:
        return f"через {d} дн."
    if d < 30:
        weeks = max(1, round(d / 7))
        return "через неделю" if weeks == 1 else f"через {weeks} нед."
    if d < 365:
        months = max(1, round(d / 30))
        return "через месяц" if months == 1 else f"через {months} мес."
    return "через год и больше"


def progress_stars(repetitions: int, cap: int = 6) -> str:
    filled = min(repetitions, cap)
    return "⭐" * filled + "☆" * (cap - filled)


# ---------------------------------------------------------------------------
# /start и главное меню
# ---------------------------------------------------------------------------

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await reset_user_state(message.from_user.id, state)
    db.ensure_user(message.from_user.id, message.from_user.username or "")
    await message.answer(
        "¡Hola! 👋 Это бот для изучения испанского (уровень A2-B2).\n\n"
        "Здесь есть:\n"
        "📚 Слова, фразы и разговорный сленг\n"
        "🔤 Тренажёр спряжений глаголов в прошедших временах "
        "(Pretérito Indefinido и Imperfecto)\n\n"
        "Выбирай режим:",
        reply_markup=main_menu_kb(),
    )


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext):
    await reset_user_state(message.from_user.id, state)
    await message.answer("Главное меню:", reply_markup=main_menu_kb())


@router.callback_query(F.data == "menu_learn")
async def cb_menu_learn(callback: CallbackQuery):
    await callback.answer()
    await cmd_learn_logic(callback.from_user.id, callback.message)


@router.callback_query(F.data == "menu_verbs")
async def cb_menu_verbs(callback: CallbackQuery):
    await callback.answer()
    await cmd_verbs_logic(callback.from_user.id, callback.message)


@router.callback_query(F.data == "menu_stats")
async def cb_menu_stats(callback: CallbackQuery):
    await callback.answer()
    await send_stats(callback.from_user.id, callback.message)


# ---------------------------------------------------------------------------
# Режим "Слова/фразы"
#
# Тренировка = фиксированный список слов (до LESSON_SIZE штук), сохранённый
# в БД в момент старта. Пользователь печатает перевод текстом; бот сравнивает
# ответ без учёта регистра, лишних пробелов, и мягко (с подсказкой) прощает
# отсутствие ударений/ñ. Прогресс (индекс, счётчики) хранится в таблице
# lesson_sessions — не в памяти процесса, поэтому переживает перезапуск бота.
# После ответа следующее задание показывается автоматически, без кнопки.
# ---------------------------------------------------------------------------

@router.message(Command("learn"))
async def cmd_learn(message: Message):
    db.ensure_user(message.from_user.id, message.from_user.username or "")
    await cmd_learn_logic(message.from_user.id, message)


async def cmd_learn_logic(user_id: int, message: Message):
    # Только один активный режим одновременно — иначе непонятно, куда
    # относить следующий текстовый ответ пользователя.
    db.cancel_active_sessions(user_id, item_type="conjugation")

    session = db.get_active_session(user_id, "word")
    if session:
        ids = json.loads(session["item_ids"])
        remaining = len(ids) - session["current_index"]
        await send_lesson_word(
            session, message,
            intro=f"Продолжаем предыдущий урок ↩️ (осталось {remaining} слов)\n\n",
        )
        return

    due = db.get_due_items(user_id, "word", limit=LESSON_SIZE)
    if not due:
        await message.answer("Слов для повторения пока нет 🤷 Попробуй позже.")
        return

    item_ids = [w["id"] for w in due]
    new_left = db.count_new_items(user_id, "word")

    session = db.create_lesson_session(user_id, "word", item_ids)

    if new_left == 0:
        intro = (
            "🎉 Новые слова на сегодня закончились — повторяем то, что уже учили.\n\n"
        )
    else:
        intro = f"📚 Начинаем урок! Слов сегодня: {len(item_ids)}\n\n"

    await send_lesson_word(session, message, intro=intro)


async def send_lesson_word(session: dict, message: Message, intro: str = ""):
    item_ids = json.loads(session["item_ids"])
    idx = session["current_index"]

    if idx >= len(item_ids):
        await finish_training(session, message, kind="word")
        return

    word = db.get_word_by_id(item_ids[idx])
    if word is None:
        # Слово могло быть удалено из базы между сессиями — пропускаем,
        # не ломая урок целиком.
        session = db.record_lesson_answer(session["id"], "wrong")
        await send_lesson_word(session, message)
        return

    category_label = CATEGORY_LABELS.get(word["category"], word["category"])
    text = (
        f"{intro}"
        f"📚 Слово {idx + 1} из {len(item_ids)}\n\n"
        f"🇷🇺 <b>{word['translation']}</b>\n"
        f"<i>({category_label}, {word['level']})</i>\n\n"
        f"Напиши перевод на испанский:"
    )
    await message.answer(text, reply_markup=nav_kb())


async def process_word_answer(session: dict, message: Message):
    item_ids = json.loads(session["item_ids"])
    idx = session["current_index"]

    if idx >= len(item_ids):
        await finish_training(session, message, kind="word")
        return

    word = db.get_word_by_id(item_ids[idx])
    if word is None:
        session = db.record_lesson_answer(session["id"], "wrong")
        await send_lesson_word(session, message)
        return

    correct_answer = word["spanish"]
    user_answer = message.text
    outcome = compare_answer(user_answer, correct_answer)

    sr_correct = (outcome == "correct")
    new_reps, new_interval = await update_progress(
        message.from_user.id, "word", word["id"], sr_correct
    )

    await message.answer(
        build_result_text(outcome, correct_answer, user_answer, new_reps, new_interval)
    )

    session = db.record_lesson_answer(session["id"], outcome)

    await asyncio.sleep(AUTO_ADVANCE_DELAY)
    await send_lesson_word(session, message)


# ---------------------------------------------------------------------------
# Режим "Тренажёр спряжений"
#
# Та же самая механика, что и у слов: сессия в БД, ответ текстом, следующее
# задание автоматически. Раньше это был отдельный механизм на FSM + памяти
# процесса — теперь оба режима устроены одинаково.
# ---------------------------------------------------------------------------

@router.message(Command("verbs"))
async def cmd_verbs(message: Message):
    db.ensure_user(message.from_user.id, message.from_user.username or "")
    await cmd_verbs_logic(message.from_user.id, message)


async def cmd_verbs_logic(user_id: int, message: Message):
    db.cancel_active_sessions(user_id, item_type="word")

    session = db.get_active_session(user_id, "conjugation")
    if session:
        ids = json.loads(session["item_ids"])
        remaining = len(ids) - session["current_index"]
        await send_lesson_verb(
            session, message,
            intro=f"Продолжаем предыдущую тренировку ↩️ (осталось {remaining} форм)\n\n",
        )
        return

    due = db.get_due_items(user_id, "conjugation", limit=LESSON_SIZE)
    if not due:
        await message.answer("Форм для повторения пока нет 🤷 Попробуй позже.")
        return

    item_ids = [c["id"] for c in due]
    new_left = db.count_new_items(user_id, "conjugation")

    session = db.create_lesson_session(user_id, "conjugation", item_ids)

    if new_left == 0:
        intro = (
            "🎉 Новые формы на сегодня закончились — повторяем то, что уже учили.\n\n"
        )
    else:
        intro = f"🔤 Начинаем тренировку! Форм сегодня: {len(item_ids)}\n\n"

    await send_lesson_verb(session, message, intro=intro)


async def send_lesson_verb(session: dict, message: Message, intro: str = ""):
    item_ids = json.loads(session["item_ids"])
    idx = session["current_index"]

    if idx >= len(item_ids):
        await finish_training(session, message, kind="conjugation")
        return

    conj = db.get_conjugation_by_id(item_ids[idx])
    if conj is None:
        session = db.record_lesson_answer(session["id"], "wrong")
        await send_lesson_verb(session, message)
        return

    tense_label = TENSE_LABELS.get(conj["tense"], conj["tense"])
    text = (
        f"{intro}"
        f"🔤 Форма {idx + 1} из {len(item_ids)}\n\n"
        f"Глагол: <b>{conj['infinitive']}</b> ({conj['verb_translation']})\n"
        f"⏳ Время: {tense_label}\n"
        f"👤 Лицо: <b>{conj['pronoun']}</b>\n\n"
        f"Напиши правильную форму:"
    )
    await message.answer(text, reply_markup=nav_kb())


async def process_verb_answer(session: dict, message: Message):
    item_ids = json.loads(session["item_ids"])
    idx = session["current_index"]

    if idx >= len(item_ids):
        await finish_training(session, message, kind="conjugation")
        return

    conj = db.get_conjugation_by_id(item_ids[idx])
    if conj is None:
        session = db.record_lesson_answer(session["id"], "wrong")
        await send_lesson_verb(session, message)
        return

    correct_answer = conj["form"]
    user_answer = message.text
    outcome = compare_answer(user_answer, correct_answer)

    sr_correct = (outcome == "correct")
    new_reps, new_interval = await update_progress(
        message.from_user.id, "conjugation", conj["id"], sr_correct
    )

    await message.answer(
        build_result_text(outcome, correct_answer, user_answer, new_reps, new_interval)
    )

    session = db.record_lesson_answer(session["id"], outcome)

    await asyncio.sleep(AUTO_ADVANCE_DELAY)
    await send_lesson_verb(session, message)


# ---------------------------------------------------------------------------
# Общий текст результата — используется и словами, и спряжениями, чтобы оба
# режима выглядели и ощущались одинаково (единый стиль).
# ---------------------------------------------------------------------------

def build_result_text(outcome: str, correct_answer: str, user_answer: str,
                       new_reps: int, new_interval: float) -> str:
    if outcome == "correct":
        praise = random.choice(PRAISE_PHRASES)
        lines = [f"✅ Верно! {praise}"]
    elif outcome == "almost":
        hint = accent_hint(correct_answer)
        lines = [
            f"⚠️ Почти правильно!{hint}",
            f"Правильный ответ: <b>{correct_answer}</b>",
        ]
    else:
        lines = [
            "❌ Неправильно.",
            f"Правильный ответ: <b>{correct_answer}</b>",
            f"Твой ответ: {user_answer}",
        ]

    if new_reps > 0:
        lines.append(f"\n{progress_stars(new_reps)}  ({min(new_reps, 6)}/6 повторений)")
    lines.append(f"📅 Следующее повторение: {friendly_interval(new_interval)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Общий обработчик обычного текста — обрабатывает ответы и в словах, и в
# спряжениях (только один режим может быть активен одновременно — см.
# cmd_learn_logic/cmd_verbs_logic, которые отменяют сессию другого режима).
# ---------------------------------------------------------------------------

@router.message(F.text & ~F.text.startswith("/"))
async def handle_plain_text(message: Message):
    user_id = message.from_user.id

    word_session = db.get_active_session(user_id, "word")
    if word_session:
        await process_word_answer(word_session, message)
        return

    verb_session = db.get_active_session(user_id, "conjugation")
    if verb_session:
        await process_verb_answer(verb_session, message)
        return

    await message.answer(
        "Не совсем понял 🙂 Чтобы начать урок слов — /learn, "
        "тренажёр спряжений — /verbs, статистика — /stats."
    )


# ---------------------------------------------------------------------------
# Универсальная навигация — работает для ЛЮБОГО текущего или будущего режима:
#   nav:menu — полностью выйти в главное меню (сбрасывает всё)
#   nav:stop — закончить текущую тренировку (со статистикой) и вернуться в меню
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "nav:menu")
async def cb_nav_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await reset_user_state(callback.from_user.id, state)
    await callback.message.answer("Главное меню:", reply_markup=main_menu_kb())


@router.callback_query(F.data == "nav:stop")
async def cb_nav_stop(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id

    word_session = db.get_active_session(user_id, "word")
    if word_session:
        await finish_training(word_session, callback.message, kind="word")
        return

    verb_session = db.get_active_session(user_id, "conjugation")
    if verb_session:
        await finish_training(verb_session, callback.message, kind="conjugation")
        return

    await callback.message.answer(
        "Активная тренировка не найдена.", reply_markup=main_menu_kb()
    )


# ---------------------------------------------------------------------------
# Единый экран завершения тренировки — для слов и для спряжений одинаковый.
# ---------------------------------------------------------------------------

async def finish_training(session: dict, message: Message, kind: str):
    db.complete_lesson_session(session["id"])

    total = len(json.loads(session["item_ids"]))
    correct = session["correct_count"]
    almost = session["almost_count"]
    wrong = session["wrong_count"]
    accuracy = round((correct / total) * 100) if total else 0

    elapsed = int(time.time()) - session["started_at"]
    minutes, seconds = divmod(elapsed, 60)
    time_str = f"{minutes} мин {seconds} сек" if minutes else f"{seconds} сек"

    kind_label = KIND_LABELS.get(kind, kind)

    lines = [
        "✅ <b>Тренировка завершена!</b>\n",
        f"📚 Выполнено {kind_label}: {total}",
        f"✅ Правильных: {correct}",
    ]
    if almost:
        lines.append(f"⚠️ Почти правильно: {almost}")
    lines += [
        f"❌ Ошибок: {wrong}",
        f"📈 Точность: {accuracy}%",
        f"⏱ Время: {time_str}",
        "",
        "🔥 Отличная работа!" if accuracy >= 80 else "💪 Продолжай в том же духе!",
        "",
        "Ещё раз — /learn (слова) или /verbs (спряжения)",
    ]
    await message.answer("\n".join(lines), reply_markup=main_menu_kb())


# ---------------------------------------------------------------------------
# Прогресс / статистика
# ---------------------------------------------------------------------------

async def update_progress(user_id: int, item_type: str, item_id: int, correct: bool):
    """Возвращает (новое_число_повторов, новый_интервал_в_днях) — нужно, чтобы
    показать пользователю прогресс и время следующего повторения."""
    existing = db.get_progress(user_id, item_type, item_id)
    reps = existing["repetitions"] if existing else 0
    ease = existing["ease_factor"] if existing else 2.5
    interval = existing["interval_days"] if existing else 0

    quality = sr.quality_from_bool(correct)
    new_reps, new_ease, new_interval, next_review = sr.review(reps, ease, interval, quality)

    db.upsert_progress(
        user_id, item_type, item_id, new_reps, new_ease, new_interval,
        next_review, "correct" if correct else "wrong",
    )
    return new_reps, new_interval


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    await send_stats(message.from_user.id, message)


async def send_stats(user_id: int, message: Message):
    s = db.get_extended_stats(user_id)

    total_answers = s["correct"] + s["almost"] + s["wrong"]
    accuracy = round(s["correct"] / total_answers * 100) if total_answers else 0

    hours, remainder = divmod(s["time_seconds"], 3600)
    minutes = remainder // 60
    time_str = f"{hours} ч {minutes} мин" if hours else f"{minutes} мин"

    learning_words = s["seen_words"] - s["mastered_words"]
    learning_conj = s["seen_conjugations"] - s["mastered_conjugations"]

    lines = [
        "📊 <b>Твоя статистика</b>\n",
        "📚 <b>Слова и фразы</b>",
        f"Всего в базе: {s['total_words']}",
        f"Ещё не начато: {s['total_words'] - s['seen_words']}",
        f"Изучаю: {learning_words}",
        f"🏆 Закреплено: {s['mastered_words']}",
        "",
        "🔤 <b>Спряжения</b>",
        f"Всего форм: {s['total_conjugations']}",
        f"Изучаю: {learning_conj}",
        f"🏆 Закреплено: {s['mastered_conjugations']}",
        "",
        "📈 <b>Активность</b>",
        f"Тренировок завершено: {s['sessions_completed']}",
        f"✅ Правильных ответов: {s['correct']}",
    ]
    if s["almost"]:
        lines.append(f"⚠️ Почти правильно: {s['almost']}")
    lines += [
        f"❌ Ошибок: {s['wrong']}",
        f"🎯 Точность: {accuracy}%",
        f"⏱ Время в тренировках: {time_str}",
    ]

    if s["days_active"]:
        lines += [
            "",
            "📅 <b>По дням</b>",
            f"Дней занимался: {s['days_active']}",
            f"Лучший день: {s['best_day']} заданий",
            f"В среднем за день: {s['avg_per_day']} заданий",
        ]

    await message.answer("\n".join(lines), reply_markup=main_menu_kb())


@router.message(Command("admin"))
async def cmd_admin(message: Message):
    # Тихо игнорируем любого, кто не владелец — не подтверждаем даже сам
    # факт существования команды (не пишем "нет доступа" и т.п.).
    if not ADMIN_USER_ID or str(message.from_user.id) != str(ADMIN_USER_ID):
        return

    s = db.get_admin_stats()
    lines = [
        "👑 <b>Админ-статистика</b>\n",
        f"👥 Пользователей всего: {s['total_users']}",
        f"🆕 Новых сегодня: {s['new_today']}",
        f"Активных сегодня: {s['active_today']}",
        f"Активных за неделю: {s['active_week']}",
        f"Активных за месяц: {s['active_month']}",
        "",
        "📈 <b>Активность (все пользователи)</b>",
        f"Тренировок завершено: {s['sessions_completed']}",
        f"Среднее заданий за тренировку: {s['avg_items_per_session']}",
        f"Среднее время тренировки: {s['avg_session_minutes']} мин",
        f"Общая точность: {s['accuracy']}%",
        "",
        "📚 <b>Режимы</b>",
        f"Пользуются словами: {s['word_users']}",
        f"Пользуются спряжениями: {s['conj_users']}",
    ]
    await message.answer("\n".join(lines))


# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------

async def main():
    if not BOT_TOKEN:
        logger.error(
            "Не задана переменная окружения BOT_TOKEN.\n"
            "  Локально: создай файл .env (по образцу .env.example) с строкой "
            "BOT_TOKEN=твой_токен\n"
            "  На Railway: Settings -> Variables -> добавь BOT_TOKEN."
        )
        sys.exit(1)

    db.init_db()
    if db.count_words() == 0:
        logger.info("База пуста — запускаю первичное заполнение (seed)...")
        import seed_db
        seed_db.main()

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    logger.info("Бот запущен, жду сообщений...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
