# -*- coding: utf-8 -*-
"""
text_utils.py
Нормализация и сравнение ответов пользователя для режима "Учить слова".

Три возможных результата сравнения:
    "correct" — совпадает точно (без учёта регистра, лишних пробелов,
                артиклей, и с учётом всех вариантов через "/" в базе)
    "almost"  — совпадает, если убрать ударения и заменить ñ -> n
                (пользователь помнит слово, но забыл диакритику)
    "wrong"   — не совпадает даже без учёта ударений
"""

import unicodedata

ARTICLES = ("el ", "la ", "los ", "las ", "un ", "una ")


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


def strip_article(word: str) -> str:
    """Убирает ведущий артикль (el/la/los/las/un/una), если он есть —
    пользователь может писать как с артиклем, так и без."""
    w = word.strip()
    lower = w.lower()
    for art in ARTICLES:
        if lower.startswith(art):
            return w[len(art):].strip()
    return w


def expand_answer_variants(spanish_field: str) -> list:
    """
    В базе слов встречаются 3 формата записи с "/":
      1. "el jefe / la jefa"    -> ["jefe", "jefa"]       (два полных слова)
      2. "profesor/profesora"  -> ["profesor", "profesora"] (два полных слова)
      3. "cansado/a"           -> ["cansado", "cansada"]  (суффиксная форма)

    Возвращает список всех допустимых ответов (артикли уже убраны).
    Если "/" в поле нет — возвращает список из одного варианта.
    """
    parts = [p.strip() for p in spanish_field.split("/")]
    if len(parts) == 1:
        return [strip_article(parts[0])]

    first = strip_article(parts[0])
    variants = [first]

    for p in parts[1:]:
        p_clean = strip_article(p)
        if len(p_clean) <= 2 and first:
            # Короткий "хвост" вроде "a"/"os"/"as" — суффиксная форма:
            # заменяем последнюю букву первого слова (cansado/a -> cansada).
            variants.append(first[:-1] + p_clean)
        else:
            # Самостоятельное слово вроде "jefa" или "profesora".
            variants.append(p_clean)

    return variants


def compare_answer(user_answer: str, correct_answer: str) -> str:
    """Возвращает 'correct' | 'almost' | 'wrong'.
    correct_answer может быть как одним словом, так и полем с вариантами
    через "/" (см. expand_answer_variants) — подходит любой из них."""
    norm_user = normalize_answer(strip_article(user_answer))
    variants = [normalize_answer(v) for v in expand_answer_variants(correct_answer)]

    if norm_user in variants:
        return "correct"

    stripped_user = strip_accents(norm_user)
    if any(stripped_user == strip_accents(v) for v in variants):
        return "almost"

    return "wrong"


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


def accent_hint(correct_answer: str) -> str:
    """Короткая дружелюбная подсказка о том, что именно забыто."""
    norm_correct = normalize_answer(correct_answer)
    if "ñ" in norm_correct:
        return " Обрати внимание на букву ñ."
    if any(ch in norm_correct for ch in "áéíóú"):
        return " Ты забыл(а) поставить ударение."
    return " Проверь написание — почти всё верно."
