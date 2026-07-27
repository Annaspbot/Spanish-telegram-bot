# -*- coding: utf-8 -*-
"""
text_utils.py
Нормализация и сравнение ответов пользователя для режима "Учить слова".

Три возможных результата сравнения:
    "correct" — совпадает точно (без учёта регистра и лишних пробелов)
    "almost"  — совпадает, если убрать ударения и заменить ñ -> n
                (пользователь помнит слово, но забыл диакритику)
    "wrong"   — не совпадает даже без учёта ударений
"""

import unicodedata


def normalize_answer(text: str) -> str:
    """
    Приводит строку к виду для сравнения:
    - убирает пробелы в начале/конце
    - схлопывает любые повторяющиеся пробелы/табы в один пробел
    - приводит к нижнему регистру
    """
    text = text.strip().lower()
    text = " ".join(text.split())
    return text


def strip_accents(text: str) -> str:
    """
    Убирает диакритические знаки для "мягкого" сравнения:
    á/é/í/ó/ú -> a/e/i/o/u, ñ -> n.
    Использует Unicode NFKD-разложение: буква с ударением распадается на
    базовую букву + отдельный комбинирующий знак ударения, который затем
    отбрасывается. ñ таким же образом распадается на n + тильда.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return without_marks


def compare_answer(user_answer: str, correct_answer: str) -> str:
    """Возвращает 'correct' | 'almost' | 'wrong'."""
    norm_user = normalize_answer(user_answer)
    norm_correct = normalize_answer(correct_answer)

    if norm_user == norm_correct:
        return "correct"

    if strip_accents(norm_user) == strip_accents(norm_correct):
        return "almost"

    return "wrong"


def accent_hint(correct_answer: str) -> str:
    """Короткая дружелюбная подсказка о том, что именно забыто."""
    norm_correct = normalize_answer(correct_answer)
    if "ñ" in norm_correct:
        return " Обрати внимание на букву ñ."
    if any(ch in norm_correct for ch in "áéíóú"):
        return " Ты забыл(а) поставить ударение."
    return " Проверь написание — почти всё верно."
