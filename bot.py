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
import logging
import os
import sys

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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")

router = Router()

# В памяти: какое слово/форма сейчас "активны" в сессии пользователя,
# чтобы не плодить сложный FSM для простого drill-режима.
active_word: dict[int, dict] = {}
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


def show_answer_kb(word_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👀 Показать перевод", callback_data=f"word_show:{word_id}")],
    ])


def know_dontknow_kb(word_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Знаю", callback_data=f"word_know:{word_id}"),
            InlineKeyboardButton(text="❌ Не знаю", callback_data=f"word_dontknow:{word_id}"),
        ]
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
    await send_next_word(callback.from_user.id, callback.message)


@router.callback_query(F.data == "menu_verbs")
async def cb_menu_verbs(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await send_next_verb(callback.from_user.id, callback.message, state)


@router.callback_query(F.data == "menu_stats")
async def cb_menu_stats(callback: CallbackQuery):
    await callback.answer()
    await send_stats(callback.from_user.id, callback.message)


# ---------------------------------------------------------------------------
# Режим "Слова/фразы"
# ---------------------------------------------------------------------------

@router.message(Command("learn"))
async def cmd_learn(message: Message):
    db.ensure_user(message.from_user.id, message.from_user.username or "")
    await send_next_word(message.from_user.id, message)


async def send_next_word(user_id: int, message: Message):
    items = db.get_due_items(user_id, "word", limit=1)
    if not items:
        await message.answer("Слов для повторения пока нет 🤷 Попробуй позже.")
        return

    word = items[0]
    active_word[user_id] = word

    category_label = {
        "verb_inf": "глагол",
        "noun": "существительное",
        "adjective": "прилагательное",
        "phrase": "фраза/выражение",
        "slang": "разговорное/сленг",
    }.get(word["category"], word["category"])

    text = f"🇪🇸 <b>{word['spanish']}</b>\n<i>({category_label}, {word['level']})</i>"
    await message.answer(text, reply_markup=show_answer_kb(word["id"]))


@router.callback_query(F.data.startswith("word_show:"))
async def cb_word_show(callback: CallbackQuery):
    word_id = int(callback.data.split(":")[1])
    word = active_word.get(callback.from_user.id)
    await callback.answer()
    if not word or word["id"] != word_id:
        await callback.message.answer("Сессия устарела, жми /learn ещё раз.")
        return

    text = f"🇪🇸 <b>{word['spanish']}</b>\n➡️ {word['translation']}"
    if word.get("example"):
        text += f"\n\n💬 <i>{word['example']}</i>"
    await callback.message.edit_text(text, reply_markup=know_dontknow_kb(word_id))


@router.callback_query(F.data.startswith("word_know:") | F.data.startswith("word_dontknow:"))
async def cb_word_answer(callback: CallbackQuery):
    user_id = callback.from_user.id
    action, word_id_str = callback.data.split(":")
    word_id = int(word_id_str)
    correct = action == "word_know"

    await callback.answer("Записал ✅" if correct else "Ничего, повторим позже 💪")
    await update_progress(user_id, "word", word_id, correct)

    await callback.message.edit_reply_markup(reply_markup=continue_kb("next_word"))


@router.callback_query(F.data == "next_word")
async def cb_next_word(callback: CallbackQuery):
    await callback.answer()
    await send_next_word(callback.from_user.id, callback.message)


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
