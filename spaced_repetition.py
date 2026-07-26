"""
spaced_repetition.py
Упрощённая версия алгоритма SM-2 (как в Anki).

quality: качество ответа от 0 до 5, но т.к. в боте всего варианты
"Знаю" / "Не знаю" / (для глаголов) правильный/неправильный текстовый ответ,
используем упрощённую шкалу:
    0 -> "не знаю" / ошибся
    4 -> "знаю" / ответил верно
    5 -> ответил верно быстро / уверенно (не используется пока, задел на будущее)
"""

import time

MIN_EASE = 1.3
DAY_SECONDS = 24 * 60 * 60


def review(repetitions: int, ease_factor: float, interval_days: float, quality: int):
    """
    Возвращает (new_repetitions, new_ease_factor, new_interval_days, next_review_ts)
    """
    if quality < 3:
        # Забыл / ошибся -> сброс повторов, но не полностью в ноль по интервалу,
        # показываем снова скоро (через несколько минут в течение той же сессии
        # логика на стороне бота, здесь - краткосрочный интервал в днях)
        repetitions = 0
        interval_days = 0.02  # ~30 минут - для повторного показа почти сразу
    else:
        if repetitions == 0:
            interval_days = 1
        elif repetitions == 1:
            interval_days = 3
        else:
            interval_days = round(interval_days * ease_factor, 2)
        repetitions += 1

    ease_factor = ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    ease_factor = max(MIN_EASE, ease_factor)

    next_review = int(time.time() + interval_days * DAY_SECONDS)
    return repetitions, round(ease_factor, 3), interval_days, next_review


def quality_from_bool(correct: bool) -> int:
    return 4 if correct else 1
