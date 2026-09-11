# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Словарь трудных терминов (terms.py): названия, которые ASR слышит как похожее
слово. Контракт проверяем целиком — модуль правит ТЕКСТ СУБТИТРОВ, а увидеть
ошибку юзер сможет только в After Effects.

Запуск:  python -m pytest reelsi/tests -q
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import terms as _terms  # noqa: E402


@pytest.fixture()
def terms(tmp_path, monkeypatch):
    """terms.py со своим файлом словаря — боевой terms.json тесты не трогают.
    Подменяем путь и чистим кэш, а не перезагружаем модуль (reload плодит новый объект
    модуля, и тесты, сверяющие константы по `is`, начинают падать через раз)."""
    monkeypatch.setattr(_terms, "TERMS_PATH", str(tmp_path / "terms.json"))
    monkeypatch.setattr(_terms, "_CACHE", {"mtime": -1, "data": None})
    return _terms


def _w(*pairs):
    """[(слово, старт), ...] -> лента ASR [{w,start,end}]."""
    return [{"w": w, "start": float(s), "end": float(s) + 0.4} for w, s in pairs]


def test_default_list_when_no_file(terms):
    """Файла ещё нет — отдаём стартовый список (он пуст), а не падение."""
    assert [t["term"] for t in terms.load()["terms"]] == terms.DEFAULT_TERMS


def test_fuzzy_fixes_near_miss(terms):
    """Промах в паре букв чинится сам: ради него не должно быть нужды заводить вариант."""
    terms.set_terms(["ротоскопинг"])
    out = terms.fix_words(_w(("Этот", 0.0), ("ротоскопинка", 0.5), ("тоже", 1.0)))
    assert [x["w"] for x in out] == ["Этот", "ротоскопинг", "тоже"]


def test_fuzzy_keeps_ordinary_words(terms):
    """Обычная речь не должна цепляться за словарь — подменённое НЕ ТО слово хуже
    непоправленного: юзер увидит его уже в AE."""
    terms.set_terms(["ротоскопинг", "Wi-Fi"])
    src = _w(("монтаж", 0.0), ("камера", 0.5), ("который", 1.0), ("картинка", 1.5))
    assert [x["w"] for x in terms.fix_words(src)] == [x["w"] for x in src]


def test_variant_merges_words_and_keeps_timings(terms):
    """Латиница/цифры по похожести не ловятся вовсе — их берёт вариант. Несколько слов
    склеиваются в один термин, тайминги — от первого до последнего (иначе субтитр
    показался бы не там, где звучит)."""
    terms.set_terms([{"term": "RTX5090", "variants": ["эр тэ икс"]}])
    out = terms.fix_words(_w(("Взял", 0.0), ("эр", 1.0), ("тэ", 1.5), ("икс", 2.0),
                             ("вчера", 3.0)))
    assert [x["w"] for x in out] == ["Взял", "RTX5090", "вчера"]
    assert out[1]["start"] == 1.0 and out[1]["end"] == pytest.approx(2.4)


def test_variant_normalization_ignores_case_hyphens_yo(terms):
    """«Wi-Fi», «ВАЙ-ФАЙ», «вай фай» — один и тот же ключ: иначе вариантов пришлось бы
    заводить по десятку на термин."""
    terms.set_terms([{"term": "Wi-Fi", "variants": ["ВАЙ-ФАЙ"]}])
    assert terms.fix_words(_w(("вай", 0.0), ("фай", 0.4)))[0]["w"] == "Wi-Fi"


def test_punctuation_tail_survives(terms):
    """Точка/запятая после слова остаются: без этого предложение в субтитрах
    разваливалось на каждом термине."""
    terms.set_terms(["ротоскопинг"])
    assert terms.fix_words(_w(("ротоскопинк,", 0.0)))[0]["w"] == "ротоскопинг,"


def test_fix_words_does_not_mutate_input(terms):
    """Ленту слов кэшируют выше по пайплайну — портить её нельзя."""
    terms.set_terms(["ротоскопинг"])
    src = _w(("ротоскопинк", 0.0))
    terms.fix_words(src)
    assert src[0]["w"] == "ротоскопинк"


def test_learn_only_for_known_term(terms):
    """Учимся ТОЛЬКО правкой на известный термин: иначе в словарь поехали бы обычные
    опечатки, а он должен оставаться списком названий."""
    terms.set_terms(["RTX5090"])
    assert terms.learn("эр тэ икс", "RTX5090") == "RTX5090"
    assert terms.learn("камира", "камера") is None          # не термин — не запоминаем


def test_learned_variant_applies_next_time(terms):
    """Ради чего всё: та же ослышка в следующем ролике чинится без участия юзера."""
    terms.set_terms(["RTX5090"])
    terms.learn("эртэикс", "RTX5090")
    assert terms.fix_words(_w(("эртэикс", 0.0)))[0]["w"] == "RTX5090"


def test_learn_refuses_translit_collision(terms):
    """Ослышка не должна запоминаться, если она похожа на транслит ДРУГОГО термина:
    «модси» — это как ASR слышит MOTS-C, и учить её под «ДСИП» — значит навсегда
    подменять MOTS-C на ДСИП (юзер чинил это руками пять раз). Буквенно «модси» и
    «MOTS-C» не совпадут никогда — работает сравнение по транслиту."""
    terms.set_terms([{"term": "MOTS-C"}, {"term": "ДСИП", "variants": []}])
    assert terms.learn("модси", "ДСИП") is None
    assert terms.learn("диссип", "ДСИП") == "ДСИП"     # чужая ослышка не задета


def test_learn_refuses_name_collision_via_core(terms):
    """Тот же гейт ловит и близкое имя по «ядру»: «модси» и транслит «моцк» делят
    «мо», и этого достаточно, чтобы не запоминать (правило: сомневаешься — не учи)."""
    terms.set_terms([{"term": "MOTS-C"}, {"term": "ЛПНП", "variants": []}])
    assert terms.learn("модси", "ЛПНП") is None


def test_learn_refuses_existing_variant_of_other_term(terms):
    """Вариант, который уже принадлежит другому термину, не дублируется под новым:
    после майнинга «модси» станет вариантом MOTS-C, и повторная правка «модси→ДСИП»
    не должна завести вторую копию."""
    terms.set_terms([{"term": "MOTS-C", "variants": ["модси"]},
                     {"term": "ДСИП", "variants": []}])
    assert terms.learn("модси", "ДСИП") is None


def test_set_terms_keeps_learned_variants(terms):
    """Юзер правит в ⚙ НАЗВАНИЯ. Стерев их список, он не должен терять память правок."""
    terms.set_terms(["RTX5090"])
    terms.learn("эр тэ икс", "RTX5090")
    terms.set_terms(["RTX5090", "ротоскопинг"])             # перезапись списка из UI
    saved = {t["term"]: t["variants"] for t in terms.load()["terms"]}
    assert saved["RTX5090"] == ["эр тэ икс"]


def test_empty_dictionary_is_noop(terms):
    """Пустой словарь не должен ни падать, ни трогать слова."""
    terms.set_terms([])
    src = _w(("слово", 0.0), ("другое", 0.5))
    assert [x["w"] for x in terms.fix_words(src)] == ["слово", "другое"]


def test_file_format_is_stable(terms, tmp_path):
    """Формат на диске — {"terms":[{term,variants}]}: файл правят и руками."""
    terms.set_terms([{"term": "Wi-Fi", "variants": ["вай фай"]}])
    d = json.load(open(str(tmp_path / "terms.json"), encoding="utf-8"))
    assert d["terms"] == [{"term": "Wi-Fi", "variants": ["вай фай"]}]
