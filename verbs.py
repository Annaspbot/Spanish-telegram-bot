# -*- coding: utf-8 -*-
"""
data/verbs.py
Список глаголов + генератор форм для Pretérito Indefinido и Pretérito Imperfecto.

Подход:
- Правильные глаголы (-ar/-er/-ir) спрягаются по стандартным окончаниям
  (с учётом орфографических изменений -car/-gar/-zar в 1-м лице ед.ч. Indefinido,
  и y-изменений леer/oír/construir и т.п.)
- Глаголы с изменением корня в 3-м лице Indefinido (e->i, o->u): pedir, dormir, morir...
- Полностью неправильные глаголы (ser, ir, tener, hacer, decir и т.д.) — захардкожены,
  т.к. не подчиняются регулярным правилам.
- В Imperfecto нерегулярны только 3 глагола: ser, ir, ver — остальные всегда правильные.

Местоимения (порядок форм везде одинаковый):
    yo, tú, él/ella/usted, nosotros, vosotros, ellos/ellas/ustedes
"""

PRONOUNS = ["yo", "tú", "él/ella/usted", "nosotros", "vosotros", "ellos/ellas/ustedes"]

# ---------------------------------------------------------------------------
# 1. Полностью неправильные глаголы в Indefinido (окончания не подчиняются
#    стандартной модели -ar/-er/-ir)
# ---------------------------------------------------------------------------
IRREGULAR_INDEFINIDO = {
    "ser":      ["fui", "fuiste", "fue", "fuimos", "fuisteis", "fueron"],
    "ir":       ["fui", "fuiste", "fue", "fuimos", "fuisteis", "fueron"],
    "estar":    ["estuve", "estuviste", "estuvo", "estuvimos", "estuvisteis", "estuvieron"],
    "tener":    ["tuve", "tuviste", "tuvo", "tuvimos", "tuvisteis", "tuvieron"],
    "hacer":    ["hice", "hiciste", "hizo", "hicimos", "hicisteis", "hicieron"],
    "poder":    ["pude", "pudiste", "pudo", "pudimos", "pudisteis", "pudieron"],
    "poner":    ["puse", "pusiste", "puso", "pusimos", "pusisteis", "pusieron"],
    "saber":    ["supe", "supiste", "supo", "supimos", "supisteis", "supieron"],
    "querer":   ["quise", "quisiste", "quiso", "quisimos", "quisisteis", "quisieron"],
    "venir":    ["vine", "viniste", "vino", "vinimos", "vinisteis", "vinieron"],
    "decir":    ["dije", "dijiste", "dijo", "dijimos", "dijisteis", "dijeron"],
    "traer":    ["traje", "trajiste", "trajo", "trajimos", "trajisteis", "trajeron"],
    "dar":      ["di", "diste", "dio", "dimos", "disteis", "dieron"],
    "ver":      ["vi", "viste", "vio", "vimos", "visteis", "vieron"],
    "andar":    ["anduve", "anduviste", "anduvo", "anduvimos", "anduvisteis", "anduvieron"],
    "conducir": ["conduje", "condujiste", "condujo", "condujimos", "condujisteis", "condujeron"],
    "traducir": ["traduje", "tradujiste", "tradujo", "tradujimos", "tradujisteis", "tradujeron"],
    "producir": ["produje", "produjiste", "produjo", "produjimos", "produjisteis", "produjeron"],
    "caber":    ["cupe", "cupiste", "cupo", "cupimos", "cupisteis", "cupieron"],
    "haber":    ["hube", "hubiste", "hubo", "hubimos", "hubisteis", "hubieron"],
    "reír":     ["reí", "reíste", "rio", "reímos", "reísteis", "rieron"],
    "oír":      ["oí", "oíste", "oyó", "oímos", "oísteis", "oyeron"],
    "leer":     ["leí", "leíste", "leyó", "leímos", "leísteis", "leyeron"],
    "creer":    ["creí", "creíste", "creyó", "creímos", "creísteis", "creyeron"],
    "construir": ["construí", "construiste", "construyó", "construimos", "construisteis", "construyeron"],
}

# ---------------------------------------------------------------------------
# 2. Глаголы -ir с изменением корня в 3-м лице ед./мн.ч. Indefinido (e->i, o->u)
#    Указываем только изменённую основу; окончания -ió/-ieron стандартные для -ir.
# ---------------------------------------------------------------------------
STEM_CHANGING_IR_INDEFINIDO = {
    # infinitivo: (stem_for_regular_forms, stem_for_3rd_person_forms)
    "pedir":    ("ped", "pid"),
    "servir":   ("serv", "sirv"),
    "seguir":   ("segu", "sigu"),
    "repetir":  ("repet", "repit"),
    "vestir":   ("vest", "vist"),
    "dormir":   ("dorm", "durm"),
    "morir":    ("mor", "mur"),
    "sentir":   ("sent", "sint"),
    "preferir": ("prefer", "prefir"),
    "mentir":   ("ment", "mint"),
    "divertir": ("divert", "divirt"),
}

# ---------------------------------------------------------------------------
# 3. Неправильные глаголы в Imperfecto — их всего три во всём испанском языке
# ---------------------------------------------------------------------------
IRREGULAR_IMPERFECTO = {
    "ser": ["era", "eras", "era", "éramos", "erais", "eran"],
    "ir":  ["iba", "ibas", "iba", "íbamos", "ibais", "iban"],
    "ver": ["veía", "veías", "veía", "veíamos", "veíais", "veían"],
}


def _spelling_fix_yo_indefinido(infinitive: str, yo_form: str) -> str:
    """Орфографические замены в 1-м л. ед.ч. Indefinido: -car->qu, -gar->gu, -zar->c"""
    if infinitive.endswith("car"):
        return yo_form[:-2] + "qué" if yo_form.endswith("é") else yo_form
    if infinitive.endswith("gar"):
        return yo_form[:-2] + "gué" if yo_form.endswith("é") else yo_form
    if infinitive.endswith("zar"):
        return yo_form[:-2] + "cé" if yo_form.endswith("é") else yo_form
    return yo_form


def conjugate_indefinido(infinitive: str):
    """Возвращает список из 6 форм Pretérito Indefinido для данного инфинитива."""
    if infinitive in IRREGULAR_INDEFINIDO:
        return IRREGULAR_INDEFINIDO[infinitive][:]

    if infinitive in STEM_CHANGING_IR_INDEFINIDO:
        reg_stem, irr_stem = STEM_CHANGING_IR_INDEFINIDO[infinitive]
        return [
            reg_stem + "í",
            reg_stem + "iste",
            irr_stem + "ió",
            reg_stem + "imos",
            reg_stem + "isteis",
            irr_stem + "ieron",
        ]

    stem = infinitive[:-2]
    if infinitive.endswith("ar"):
        forms = [stem + e for e in ["é", "aste", "ó", "amos", "asteis", "aron"]]
        forms[0] = _spelling_fix_yo_indefinido(infinitive, forms[0])
        return forms
    elif infinitive.endswith("er") or infinitive.endswith("ir") or infinitive.endswith("ír"):
        return [stem + e for e in ["í", "iste", "ió", "imos", "isteis", "ieron"]]

    raise ValueError(f"Unknown verb ending: {infinitive}")


def conjugate_imperfecto(infinitive: str):
    """Возвращает список из 6 форм Pretérito Imperfecto для данного инфинитива."""
    if infinitive in IRREGULAR_IMPERFECTO:
        return IRREGULAR_IMPERFECTO[infinitive][:]

    stem = infinitive[:-2]
    if infinitive.endswith("ar"):
        return [stem + e for e in ["aba", "abas", "aba", "ábamos", "abais", "aban"]]
    elif infinitive.endswith("er") or infinitive.endswith("ir") or infinitive.endswith("ír"):
        return [stem + e for e in ["ía", "ías", "ía", "íamos", "íais", "ían"]]

    raise ValueError(f"Unknown verb ending: {infinitive}")


# ---------------------------------------------------------------------------
# 4. Полный список глаголов для тренажёра: (инфинитив, перевод, is_irregular, level)
#    Смешиваем правильные и неправильные — самые частотные глаголы A2-B2.
# ---------------------------------------------------------------------------
VERB_LIST = [
    ("hablar", "говорить", False, "A2"),
    ("trabajar", "работать", False, "A2"),
    ("estudiar", "учиться/изучать", False, "A2"),
    ("comprar", "покупать", False, "A2"),
    ("escuchar", "слушать", False, "A2"),
    ("mirar", "смотреть", False, "A2"),
    ("llegar", "прибывать", False, "A2"),
    ("llamar", "звать/звонить", False, "A2"),
    ("necesitar", "нуждаться", False, "A2"),
    ("cocinar", "готовить (еду)", False, "A2"),
    ("viajar", "путешествовать", False, "A2"),
    ("cambiar", "менять", False, "A2"),
    ("terminar", "заканчивать", False, "A2"),
    ("comer", "есть", False, "A2"),
    ("beber", "пить", False, "A2"),
    ("aprender", "учиться (чему-то)", False, "A2"),
    ("vender", "продавать", False, "A2"),
    ("responder", "отвечать", False, "A2"),
    ("leer", "читать", True, "A2"),
    ("creer", "верить/думать", True, "A2"),
    ("vivir", "жить", False, "A2"),
    ("escribir", "писать", False, "A2"),
    ("abrir", "открывать", False, "A2"),
    ("recibir", "получать", False, "A2"),
    ("decidir", "решать", False, "B1"),
    ("subir", "подниматься", False, "A2"),
    ("ser", "быть (постоянный признак)", True, "A2"),
    ("estar", "быть (состояние/место)", True, "A2"),
    ("tener", "иметь", True, "A2"),
    ("hacer", "делать", True, "A2"),
    ("ir", "идти/ехать", True, "A2"),
    ("poder", "мочь", True, "A2"),
    ("decir", "говорить/сказать", True, "A2"),
    ("ver", "видеть", True, "A2"),
    ("dar", "давать", True, "A2"),
    ("saber", "знать (факт)", True, "A2"),
    ("querer", "хотеть/любить", True, "A2"),
    ("poner", "класть/ставить", True, "A2"),
    ("venir", "приходить", True, "A2"),
    ("traer", "приносить", True, "B1"),
    ("andar", "ходить/гулять", True, "B1"),
    ("conducir", "водить (машину)", True, "B1"),
    ("caber", "помещаться", True, "B2"),
    ("haber", "иметь (вспомогательный)", True, "B1"),
    ("pedir", "просить/заказывать", True, "A2"),
    ("servir", "служить/подавать", True, "B1"),
    ("seguir", "продолжать/следовать", True, "A2"),
    ("repetir", "повторять", True, "A2"),
    ("vestir", "одевать", True, "B1"),
    ("dormir", "спать", True, "A2"),
    ("morir", "умирать", True, "B1"),
    ("sentir", "чувствовать", True, "A2"),
    ("preferir", "предпочитать", True, "B1"),
    ("mentir", "лгать", True, "B1"),
    ("divertir", "развлекать", True, "B1"),
    ("oír", "слышать", True, "A2"),
    ("reír", "смеяться", True, "B1"),
    ("construir", "строить", True, "B1"),
    ("empezar", "начинать", False, "A2"),
    ("jugar", "играть", False, "A2"),
    ("llegar_dup", "прибывать", False, "A2"),  # placeholder removed at seed time if dup
]

# Убираем случайные дубликаты/заглушки при импорте
VERB_LIST = [v for v in VERB_LIST if not v[0].endswith("_dup")]
