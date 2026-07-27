# -*- coding: utf-8 -*-
"""
bot.py
Телеграм-бот для изучения испанского: слова/фразы/сленг (A2-B2) +
тренажёр спряжений глаголов (Pretérito Indefinido и Imperfecto).

Запуск:
    python bot.py
Перед первым запуском один раз выполните:
    python seed_db.py
"""

import asyncio
import json
import logging
import os
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
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
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

router = Router()

# Сколько слов дать за один урок (/learn). Сама сессия урока хранится в БД
# (таблица lesson_sessions), а не в памяти процесса — поэтому переживает
# перезапуск бота (важно для Railway, где рестарт происходит при каждом деплое).
LESSON_SIZE = 20

# Тренажёр спряжений пока держит текущую форму в памяти процесса на короткое
# время одного вопроса — это отдельная, более простая механика, не меняем её
# в этой правке.
active_conj: dict[int, dict] = {}


class VerbDrill(StatesGroup):
    waiting_answer = State()


# ---------------------------------------------------------------------------
# Клавиатуры
# ---------------------------------------------------------------------------

def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📚 Учить слова/фразы", callback_data="menu_learn")],
        [InlineKeyboardButton(text="🔤 Тренажёр спряжений", callback_data="menu_verbs")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="menu_stats")],
    ])


def continue_kb(action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➡️ Дальше", callback_data=action)],
    ])


TENSE_LABELS = {
    "indefinido": "Pretérito Indefinido (законченное прошедшее)",
    "imperfecto": "Pretérito Imperfecto (незаконченное/повторяющееся прошедшее)",
}


# ---------------------------------------------------------------------------
# /start и главное меню
# ---------------------------------------------------------------------------

@router.message(CommandStart())
async def cmd_start(message: Message):
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
async def cmd_menu(message: Message):
    await message.answer("Главное меню:", reply_markup=main_menu_kb())


@router.callback_query(F.data == "menu_learn")
async def cb_menu_learn(callback: CallbackQuery):
    await callback.answer()
    await cmd_learn_logic(callback.from_user.id, callback.message)


@router.callback_query(F.data == "menu_verbs")
async def cb_menu_verbs(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await send_next_verb(callback.from_user.id, callback.message, state)


@router.callback_query(F.data == "menu_stats")
async def cb_menu_stats(callback: CallbackQuery):
    await callback.answer()
    await send_stats(callback.from_user.id, callback.message)


# ---------------------------------------------------------------------------
# Режим "Слова/фразы" — версия 2.0
#
# Урок = фиксированный список слов (до LESSON_SIZE штук), сохранённый в БД
# в момент старта. Пользователь печатает перевод текстом; бот сравнивает
# ответ без учёта регистра, лишних пробелов, и мягко (с подсказкой) прощает
# отсутствие ударений/ñ. Прогресс урока (текущий индекс, счётчики) хранится
# в таблице lesson_sessions — не в памяти процесса, поэтому переживает
# перезапуск бота.
# ---------------------------------------------------------------------------

CATEGORY_LABELS = {
    "verb_inf": "глагол",
    "noun": "существительное",
    "adjective": "прилагательное",
    "phrase": "фраза/выражение",
    "slang": "разговорное/сленг",
}


@router.message(Command("learn"))
async def cmd_learn(message: Message):
    db.ensure_user(message.from_user.id, message.from_user.username or "")
    await cmd_learn_logic(message.from_user.id, message)


async def cmd_learn_logic(user_id: int, message: Message):
    session = db.get_active_session(user_id, "word")
    if session:
        ids = json.loads(session["item_ids"])
        remaining = len(ids) - session["current_index"]
        await message.answer(f"Продолжаем предыдущий урок ↩️ (осталось {remaining} слов)")
        await send_lesson_word(session, message)
        return

    due = db.get_due_items(user_id, "word", limit=LESSON_SIZE)
    if not due:
        await message.answer("Слов для повторения пока нет 🤷 Попробуй позже.")
        return

    item_ids = [w["id"] for w in due]
    session = db.create_lesson_session(user_id, "word", item_ids)
    await message.answer(f"Начинаем урок! Слов сегодня: {len(item_ids)} 📚")
    await send_lesson_word(session, message)


async def send_lesson_word(session: dict, message: Message):
    item_ids = json.loads(session["item_ids"])
    idx = session["current_index"]

    if idx >= len(item_ids):
        await finish_lesson(session, message)
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
        f"Слово {idx + 1} из {len(item_ids)}\n\n"
        f"🇷🇺 <b>{word['translation']}</b>\n"
        f"<i>({category_label}, {word['level']})</i>\n\n"
        f"Напиши перевод на испанский:"
    )
    await message.answer(text)


async def process_word_answer(session: dict, message: Message):
    item_ids = json.loads(session["item_ids"])
    idx = session["current_index"]

    if idx >= len(item_ids):
        await finish_lesson(session, message)
        return

    word = db.get_word_by_id(item_ids[idx])
    if word is None:
        session = db.record_lesson_answer(session["id"], "wrong")
        await send_lesson_word(session, message)
        return

    correct_answer = word["spanish"]
    user_answer = message.text
    outcome = compare_answer(user_answer, correct_answer)

    if outcome == "correct":
        await message.answer("✅ Верно!")
    elif outcome == "almost":
        hint = accent_hint(correct_answer)
        await message.answer(
            f"⚠️ Почти правильно!{hint}\n\n"
            f"Правильный ответ:\n<b>{correct_answer}</b>"
        )
    else:
        await message.answer(
            f"❌ Неправильно.\n\n"
            f"Правильный ответ:\n<b>{correct_answer}</b>\n\n"
            f"Твой ответ:\n{user_answer}"
        )

    # Для интервального повторения "почти правильно" считаем как "не знаю" —
    # слово вернётся на повторение раньше, но в статистике урока это отдельная,
    # не такая строгая категория (не пугаем пользователя как явной ошибкой).
    sr_correct = (outcome == "correct")
    await update_progress(message.from_user.id, "word", word["id"], sr_correct)

    session = db.record_lesson_answer(session["id"], outcome)
    await send_lesson_word(session, message)


async def finish_lesson(session: dict, message: Message):
    db.complete_lesson_session(session["id"])

    total = len(json.loads(session["item_ids"]))
    correct = session["correct_count"]
    almost = session["almost_count"]
    wrong = session["wrong_count"]
    accuracy = round((correct / total) * 100) if total else 0

    elapsed = int(time.time()) - session["started_at"]
    minutes, seconds = divmod(elapsed, 60)
    time_str = f"{minutes} мин {seconds} сек" if minutes else f"{seconds} сек"

    text = (
        "🎉 <b>Урок завершён!</b>\n\n"
        f"Всего слов: {total}\n"
        f"✅ Правильно: {correct}\n"
        f"⚠️ Почти правильно: {almost}\n"
        f"❌ Ошибок: {wrong}\n"
        f"🎯 Точность: {accuracy}%\n"
        f"⏱ Время: {time_str}\n\n"
        "Ещё урок — жми /learn"
    )
    await message.answer(text, reply_markup=main_menu_kb())


# ---------------------------------------------------------------------------
# Режим "Тренажёр спряжений"
# ---------------------------------------------------------------------------

@router.message(Command("verbs"))
async def cmd_verbs(message: Message, state: FSMContext):
    db.ensure_user(message.from_user.id, message.from_user.username or "")
    await send_next_verb(message.from_user.id, message, state)


async def send_next_verb(user_id: int, message: Message, state: FSMContext):
    items = db.get_due_items(user_id, "conjugation", limit=1)
    if not items:
        await message.answer("Форм для повторения пока нет 🤷 Попробуй позже.")
        return

    conj = items[0]
    active_conj[user_id] = conj
    await state.set_state(VerbDrill.waiting_answer)
    await state.update_data(conj_id=conj["id"])

    tense_label = TENSE_LABELS.get(conj["tense"], conj["tense"])
    text = (
        f"🔤 Глагол: <b>{conj['infinitive']}</b> ({conj['verb_translation']})\n"
        f"⏳ Время: {tense_label}\n"
        f"👤 Лицо: <b>{conj['pronoun']}</b>\n\n"
        f"Напиши правильную форму глагола:"
    )
    await message.answer(text)


@router.message(StateFilter(VerbDrill.waiting_answer))
async def handle_verb_answer(message: Message, state: FSMContext):
    user_id = message.from_user.id
    conj = active_conj.get(user_id)
    if not conj:
        await state.clear()
        await message.answer("Сессия устарела, жми /verbs ещё раз.")
        return

    user_answer = message.text.strip().lower()
    correct_answer = conj["form"].strip().lower()
    correct = user_answer == correct_answer

    if correct:
        await message.answer(f"✅ Правильно! <b>{conj['form']}</b>")
    else:
        await message.answer(
            f"❌ Не совсем. Правильный ответ: <b>{conj['form']}</b>"
        )

    await update_progress(user_id, "conjugation", conj["id"], correct)
    await state.clear()

    await message.answer("Продолжаем:", reply_markup=continue_kb("next_verb"))


@router.callback_query(F.data == "next_verb")
async def cb_next_verb(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await send_next_verb(callback.from_user.id, callback.message, state)


# ---------------------------------------------------------------------------
# Общий обработчик обычного текста — обрабатывает ответы в уроке слов.
#
# ВАЖНО: зарегистрирован ПОСЛЕ handle_verb_answer (который фильтруется по
# StateFilter(VerbDrill.waiting_answer)). aiogram проверяет хендлеры в порядке
# регистрации и останавливается на первом подходящем — поэтому пока активно
# состояние тренажёра спряжений, сообщение перехватит handle_verb_answer,
# и только если оно не подошло (state не активен) — дойдёт сюда.
# ---------------------------------------------------------------------------

@router.message(F.text & ~F.text.startswith("/"))
async def handle_plain_text(message: Message, state: FSMContext):
    # Страховка на случай гонки состояний (не должна срабатывать в норме).
    current_state = await state.get_state()
    if current_state == VerbDrill.waiting_answer.state:
        return

    user_id = message.from_user.id
    session = db.get_active_session(user_id, "word")
    if not session:
        await message.answer(
            "Не совсем понял 🙂 Чтобы начать урок слов — /learn, "
            "тренажёр спряжений — /verbs, статистика — /stats."
        )
        return

    await process_word_answer(session, message)


# ---------------------------------------------------------------------------
# Прогресс / статистика
# ---------------------------------------------------------------------------

async def update_progress(user_id: int, item_type: str, item_id: int, correct: bool):
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


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    await send_stats(message.from_user.id, message)


async def send_stats(user_id: int, message: Message):
    stats = db.get_stats(user_id)
    text = (
        "📊 <b>Твоя статистика</b>\n\n"
        f"Слов/фраз в базе: {stats['total_words']}\n"
        f"Просмотрено слов: {stats['seen_words']}\n"
        f"Хорошо выучено (5+ повторов): {stats['mastered_words']}\n\n"
        f"Форм глаголов в базе: {stats['total_conjugations']}\n"
        f"Просмотрено форм: {stats['seen_conjugations']}"
    )
    await message.answer(text, reply_markup=main_menu_kb())


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
