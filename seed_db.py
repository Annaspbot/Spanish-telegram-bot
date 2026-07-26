# -*- coding: utf-8 -*-
"""
seed_db.py
Заполняет базу данных словами/фразами/сленгом и глаголами со спряжениями.
Можно запускать повторно — дубликаты игнорируются на уровне БД (UNIQUE).
"""

import database as db
from vocab import all_words
from verbs import VERB_LIST, conjugate_indefinido, conjugate_imperfecto, PRONOUNS


def seed_words():
    words = all_words()
    for spanish, translation, category, level, example in words:
        db.add_word(spanish, translation, category, level, example)
    print(f"Слова/фразы: добавлено (или уже было) {len(words)} записей.")


def seed_verbs():
    count = 0
    for infinitive, translation, is_irregular, level in VERB_LIST:
        verb_id = db.add_verb(infinitive, translation, is_irregular, level)

        indefinido_forms = conjugate_indefinido(infinitive)
        for pronoun, form in zip(PRONOUNS, indefinido_forms):
            db.add_conjugation(verb_id, "indefinido", pronoun, form)

        imperfecto_forms = conjugate_imperfecto(infinitive)
        for pronoun, form in zip(PRONOUNS, imperfecto_forms):
            db.add_conjugation(verb_id, "imperfecto", pronoun, form)

        count += 1
    print(f"Глаголы: обработано {count}, форм спряжения на глагол: 12 (6 Indefinido + 6 Imperfecto).")


def main():
    db.init_db()
    seed_words()
    seed_verbs()
    print(f"\nИтого в базе: {db.count_words()} слов/фраз, {db.count_conjugations()} форм спряжения.")


if __name__ == "__main__":
    main()
