# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Вставки по названиям: слово из личного словаря прозвучало -> картинка из библиотеки.

Проход детерминированный и идёт ПОСЛЕ ответа модели (`core/aicut/commands.py`): названия
и их формы берутся из личного словаря `named_inserts.json` (корень репозитория), сравнение —
по ОСНОВЕ слова («кофемашины» = «кофемашина», а «кофе» — не «кофемашина»), картинка ищется
в базе вставок по имени файла и описанию (`insertlib.find_named`). Обязательные слова
картинки (`_prefer`) и штрафные (`_avoid`) тоже из словаря. Квота вставок спикера остаётся
жёсткой: вставки по названиям входят в неё и вытесняют самые слабые ИИ-вставки.

Словаря в репозитории нет (только пример), поэтому тесты пишут свой, с нейтральными
словами: публичный код не должен знать ничьих личных названий (сторож — test_named_guard.py).

Запуск: py -3.10 -m pytest tests/test_inserts_named.py -q
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from core import insertlib, speakers  # noqa: E402
from core.aicut import commands  # noqa: E402

# Словарь теста. «box» — обязательное слово картинки (как упаковка у препарата), «receipt»
# и «price» — сюжетные слова (штраф); «кофе» — ложный друг: его в словаре нет вовсе.
TEST_DICT: dict[str, Any] = {
    "_": "тестовый словарь",
    "_prefer": ["коробк*", "box", "boxes", "упаков*"],
    "_avoid": ["receipt", "price", "broken", "report"],
    "кофемашина": ["кофемашина", "кофемашины", "кофемашину", "кофемашиной", "coffee machine"],
    "ноутбук": ["ноутбук", "ноутбука", "ноутбуке", "laptop"],
    "secondary": {"кофемашина": ["эспрессо", "espresso"]},
}
MACHINE = "coffee-machine-box.png"
# Профиль спикера: полный набор 10 фото + 3 видео (квота не мешает проверкам).
PROF: dict[str, Any] = {"label": "Спикер", "inserts": {"photo": 10, "video": 3}}
# Фото модели: ни одно не ближе ±4 с к упоминанию на 34.2 с (иначе предмет не встанет).
AI_PHOTOS = (6.0, 15.0, 25.0, 45.0, 55.0, 65.0, 75.0, 85.0, 95.0, 100.0)
AI_VIDEOS = (105.0, 110.0, 115.0)


class _Emit:
    """Сбор строк лога шага: emit(msg, **vars) — как console_emit."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, msg: str = "", /, **vars: Any) -> None:
        try:
            self.lines.append(str(msg).format(**vars))
        except Exception:
            self.lines.append(str(msg))


def _words(spec: list[tuple[str, float]]) -> list[tuple[int, str, float, float]]:
    """Лента слов ролика: (слово, старт) -> (индекс, слово, старт, конец)."""
    return [(k, w, s, s + 0.5) for k, (w, s) in enumerate(spec)]


def _use_dict(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    """Подменяет путь личного словаря и сбрасывает его кэш (кэш ключуется по mtime)."""
    monkeypatch.setattr(commands, "NAMED_INSERTS_PATH", path)
    monkeypatch.setitem(commands._NAMED_CACHE, "mtime", None)
    monkeypatch.setitem(commands._NAMED_CACHE, "cfg", None)


@pytest.fixture(autouse=True)
def _named_dict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Личный словарь на время теста — во временной папке, не в корне репозитория."""
    path = tmp_path / "named_inserts.json"
    path.write_text(json.dumps(TEST_DICT, ensure_ascii=False), encoding="utf-8")
    _use_dict(monkeypatch, str(path))
    return path


def _library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
             items: list[dict[str, Any]]) -> None:
    """Свой индекс базы вставок на время теста. Файлы создаются настоящие: find_named
    проверяет, что путь жив, и запись с пропавшим файлом не отдаёт."""
    lib = tmp_path / "lib"
    lib.mkdir(exist_ok=True)
    records: list[dict[str, Any]] = []
    for raw in items:
        rec: dict[str, Any] = {"type": "photo", "used": 1, "desc": "", "ru": "", "vis": "",
                               "emb": None}
        rec.update(raw)
        if rec.get("make", True):
            f = lib / str(rec.get("name") or "x.png")
            f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 16)
            rec["path"] = str(f)
        rec.pop("make", None)
        records.append(rec)
    idx = tmp_path / "insertlib.json"
    idx.write_text(json.dumps({"items": records, "dirs": [str(lib)], "emb_model": "",
                               "emb_tag": insertlib.EMB_TAG}, ensure_ascii=False),
                   encoding="utf-8")
    monkeypatch.setattr(insertlib, "INDEX_PATH", str(idx))
    insertlib._CACHE["data"] = None


def _ai_answer(photos: tuple[float, ...] = AI_PHOTOS,
               videos: tuple[float, ...] = AI_VIDEOS) -> dict[str, Any]:
    """Ответ модели: полный набор, `phrase` пустая — тайминги не снапаются к цитате."""
    ins = [{"type": "photo", "start_sec": t, "duration_sec": 2.5, "phrase": "",
            "prompt": "", "query": f"photo {t}"} for t in photos]
    ins += [{"type": "video", "start_sec": t, "duration_sec": 2.5, "phrase": "",
             "prompt": "", "query": f"video {t}"} for t in videos]
    return {"inserts": ins}


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, words: list[tuple[int, str, float, float]],
         speaker: Any = None, answer: dict[str, Any] | None = None,
         rejected: list[dict[str, Any]] | None = None) -> tuple[dict[str, Any], _Emit]:
    monkeypatch.setattr(commands, "_words_from_xml", lambda p: list(words))
    monkeypatch.setattr(commands, "_ask_json", lambda *a, **k: answer or _ai_answer())
    em = _Emit()
    res = commands.cmd_inserts(str(tmp_path / "clip.xml"), speaker=speaker or PROF,
                               rejected=rejected, emit=em)
    return res, em


def _named(res: dict[str, Any]) -> list[dict[str, Any]]:
    return [x for x in res["inserts"] if x.get("auto") == "named"]


def _find(terms: list[str], secondary: list[str] | None = None) -> dict[str, Any] | None:
    """find_named со словарём теста (обязательные и штрафные слова — оттуда же)."""
    return insertlib.find_named(terms, secondary=secondary or [],
                                prefer=TEST_DICT["_prefer"], avoid=TEST_DICT["_avoid"])


# ================== 1. НАЗВАННОЕ СЛОВО -> ВСТАВКА С КАРТИНКОЙ ==================
def test_mentioned_name_gets_library_picture_at_the_word(tmp_path, monkeypatch):
    """«поставил кофемашины» + картинка в базе -> фото-вставка на время слова с её media.

    Форма в речи в падеже («кофемашины»), имя файла — в основе (coffee-machine-box): пара
    находится сравнением по основе, картинка ставится сразу (media заполнено, фронту её
    искать не надо), а в лог идёт строка про предмет.
    """
    _library(tmp_path, monkeypatch, [{"name": MACHINE}])
    words = _words([("Сегодня", 0.0), ("поставил", 1.0), ("кофемашины", 34.2),
                    ("и", 35.0), ("закончили", 120.0)])
    res, em = _run(tmp_path, monkeypatch, words)

    got = _named(res)
    assert len(got) == 1, res["inserts"]
    it = got[0]
    assert it["start_sec"] == 34.2, it
    assert it["type"] == "photo" and it["duration_sec"] == 2.5, it
    assert os.path.basename(str(it["media"])) == MACHINE, it
    assert it["query"] == "кофемашина" and it["phrase"] == "кофемашины", it
    # В квоте: фото в наборе ровно 10, а не 11 (одна ИИ-вставка уступила место).
    assert len([x for x in res["inserts"] if x.get("type") != "video"]) == 10, res["inserts"]
    assert ("по названиям: кофемашина 34.2 с (картинка %s)" % MACHINE) in em.lines, em.lines
    saved = json.loads((tmp_path / "clip.inserts.json").read_text(encoding="utf-8"))
    assert any(x.get("auto") == "named" and x.get("media") for x in saved["inserts"]), saved


def test_name_is_matched_by_word_stem_not_by_spelling(tmp_path, monkeypatch):
    """«ноутбуком» — тот же предмет, что «ноутбук»: падеж ловится основой слова."""
    _library(tmp_path, monkeypatch, [{"name": "laptop-box.png"}])
    words = _words([("про", 30.0), ("ноутбуком", 34.2), ("закончили", 120.0)])
    res, em = _run(tmp_path, monkeypatch, words)
    got = _named(res)
    assert len(got) == 1 and got[0]["query"] == "ноутбук", res["inserts"]
    assert "по названиям: ноутбук 34.2 с (картинка laptop-box.png)" in em.lines, em.lines


def test_multiword_name_matches_neighboring_words(tmp_path, monkeypatch):
    """«coffee machine» — форма из двух слов: вставка встаёт на ПЕРВОЕ слово пары."""
    _library(tmp_path, monkeypatch, [{"name": MACHINE}])
    words = _words([("уровень", 30.0), ("coffee", 30.5), ("machine", 31.0),
                    ("в", 31.5), ("кухне", 32.0), ("закончили", 120.0)])
    res, em = _run(tmp_path, monkeypatch, words)
    got = _named(res)
    assert len(got) == 1, res["inserts"]
    assert got[0]["start_sec"] == 30.5 and got[0]["query"] == "кофемашина", got[0]
    assert any("кофемашина 30.5 с" in ln for ln in em.lines), em.lines


def test_common_words_are_not_names(tmp_path, monkeypatch):
    """«кофе», «кофейник», «кофеварка» — обычные слова рядом с предметом, а не он: вставок
    нет и в логе тихо.

    Короткая «кофе» не сводится к «кофемашине» (основа иная), окончания сравниваются только
    падежные, поэтому «кофейник» и «кофеварка» тоже мимо.
    """
    _library(tmp_path, monkeypatch, [{"name": MACHINE}])
    words = _words([("кофе", 10.0), ("попили", 10.5), ("кофейник", 15.0),
                    ("кофеварка", 20.0), ("на", 25.0), ("кухне", 30.0), ("закончили", 120.0)])
    res, em = _run(tmp_path, monkeypatch, words)
    assert not _named(res), res["inserts"]
    assert not any(ln.startswith("по названиям:") for ln in em.lines), em.lines


# ======================== 2. КАРТИНКИ НЕТ — ВСТАВКИ НЕТ ========================
def test_name_without_picture_gives_log_line_only(tmp_path, monkeypatch):
    """Предмет назван, но картинки в базе нет -> вставки нет, а в логе «картинки нет».

    Молчать про это нельзя: владелец пополнит базу — и вставка появится сама.
    """
    _library(tmp_path, monkeypatch, [{"name": MACHINE}])
    words = _words([("Начало", 0.0), ("ноутбуком", 34.2), ("закончили", 120.0)])
    res, em = _run(tmp_path, monkeypatch, words)
    assert not _named(res), res["inserts"]
    assert len([x for x in res["inserts"] if x.get("type") != "video"]) == 10, res["inserts"]
    assert "по названиям: ноутбук — картинки нет" in em.lines, em.lines


def test_gone_record_is_not_a_picture(tmp_path, monkeypatch):
    """Запись базы с пропавшим файлом (gone) картинкой не считается."""
    _library(tmp_path, monkeypatch, [{"name": MACHINE, "gone": True}])
    words = _words([("кофемашины", 34.2), ("закончили", 120.0)])
    res, em = _run(tmp_path, monkeypatch, words)
    assert not _named(res), res["inserts"]
    assert any("кофемашина — картинки нет" in ln for ln in em.lines), em.lines


def test_no_personal_dictionary_means_no_named_inserts(tmp_path, monkeypatch):
    """Личного словаря нет — механизм молча выключен: ни вставок, ни ошибки, ни строки в логе."""
    _library(tmp_path, monkeypatch, [{"name": MACHINE}])
    _use_dict(monkeypatch, str(tmp_path / "нет-такого-файла.json"))
    words = _words([("кофемашины", 34.2), ("закончили", 120.0)])
    res, em = _run(tmp_path, monkeypatch, words)
    assert not _named(res), res["inserts"]
    assert not any(ln.startswith("по названиям:") for ln in em.lines), em.lines


def test_example_dictionary_is_valid(monkeypatch):
    """Пример в репозитории (data/named_inserts.example.json) разбирается: имена, формы,
    обязательные и штрафные слова; служебные ключи «_…» именами не становятся."""
    _use_dict(monkeypatch, str(ROOT / "data" / "named_inserts.example.json"))
    cfg = commands._named_config()
    assert set(cfg["aliases"]) == {"кофемашина", "ноутбук"}, cfg["aliases"]
    assert cfg["prefer"] and cfg["avoid"], cfg
    assert cfg["secondary"] == {"ноутбук": ["макбук", "macbook"]}, cfg["secondary"]
    assert not any(k.startswith("_") for k in cfg["aliases"]), cfg["aliases"]


# ================== 3. ПОВТОРЫ И УЖЕ СТОЯЩИЕ ВСТАВКИ ==================
def test_two_mentions_within_twenty_seconds_give_one_insert(tmp_path, monkeypatch):
    """Два упоминания подряд (34.2 и 40.0) — ОДНА вставка: окно 20 с на название."""
    _library(tmp_path, monkeypatch, [{"name": MACHINE}])
    words = _words([("кофемашины", 34.2), ("потом", 36.0), ("кофемашину", 40.0),
                    ("закончили", 120.0)])
    res, _em = _run(tmp_path, monkeypatch, words)
    got = _named(res)
    assert len(got) == 1 and got[0]["start_sec"] == 34.2, res["inserts"]


def test_existing_ai_insert_nearby_is_not_duplicated(tmp_path, monkeypatch):
    """Рядом (±4 с) уже стоит ИИ-вставка — название туда не дублируется."""
    _library(tmp_path, monkeypatch, [{"name": MACHINE}])
    words = _words([("кофемашины", 34.2), ("закончили", 120.0)])
    answer = _ai_answer(photos=(6.0, 15.0, 25.0, 36.0, 50.0, 60.0, 75.0, 85.0, 95.0, 100.0))
    res, _em = _run(tmp_path, monkeypatch, words, answer=answer)
    assert not _named(res), res["inserts"]


def test_deleted_named_insert_is_not_brought_back(tmp_path, monkeypatch):
    """Память правок: удалённую вставку по названию повторный подбор не возвращает."""
    _library(tmp_path, monkeypatch, [{"name": MACHINE}])
    words = _words([("кофемашины", 34.2), ("закончили", 120.0)])
    rejected = [{"type": "photo", "start_sec": 34.2, "query": "кофемашина"}]
    res, _em = _run(tmp_path, monkeypatch, words, rejected=rejected)
    assert not _named(res), res["inserts"]


# ================== 4. КВОТА СПИКЕРА И ВЫКЛЮЧАТЕЛЬ ==================
def test_name_displaces_the_weakest_ai_photo_when_quota_is_full(tmp_path, monkeypatch):
    """Квота фото 1 и одна ИИ-вставка: вставка по названию остаётся, уходит ИИ-вставка.

    Вставка поставлена по названному слову — её место не выдумано моделью, и в квоте она
    приоритетнее.
    """
    _library(tmp_path, monkeypatch, [{"name": MACHINE}])
    words = _words([("кофемашины", 34.2), ("закончили", 120.0)])
    prof = {"label": "Спикер", "inserts": {"photo": 1, "video": 0}}
    res, _em = _run(tmp_path, monkeypatch, words, speaker=prof,
                    answer=_ai_answer(photos=(6.0,), videos=()))
    assert res["ins_target"] == 1, res["ins_target"]
    photos = [x for x in res["inserts"] if x.get("type") != "video"]
    assert len(photos) == 1 and photos[0].get("auto") == "named", res["inserts"]


def test_speaker_switch_off_disables_named_inserts(tmp_path, monkeypatch):
    """Выключатель профиля спикера: named_inserts false — вставок по названиям нет вовсе.

    Старое поле drug_inserts (профили до переименования) выключает так же.
    """
    _library(tmp_path, monkeypatch, [{"name": MACHINE}])
    words = _words([("кофемашины", 34.2), ("закончили", 120.0)])
    for key in ("named_inserts", "drug_inserts"):
        prof = {"label": "Спикер", "inserts": {"photo": 10, "video": 3}, key: False}
        res, em = _run(tmp_path, monkeypatch, words, speaker=prof)
        assert not _named(res), res["inserts"]
        assert not any(ln.startswith("по названиям:") for ln in em.lines), em.lines
    assert commands.named_inserts_on({"label": "Спикер", "named_inserts": False}) is False
    assert commands.named_inserts_on({"label": "Спикер", "drug_inserts": False}) is False
    assert commands.named_inserts_on({"label": "Спикер"}) is True
    assert commands.named_inserts_on(None) is True


def test_speakers_save_validates_the_named_switch(tmp_path, monkeypatch):
    """speakers.save: named_inserts — только true/false, мусор не записывается."""
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(tmp_path / "speakers"))
    for bad in ("нет", 1, "false"):
        with pytest.raises(ValueError):
            speakers.save("spk", {"named_inserts": bad})
    for good in (True, False):
        key, path = speakers.save("spk", {"named_inserts": good})
        assert os.path.isfile(path)
        assert (speakers.load(key) or {}).get("named_inserts") is good


def test_speakers_save_moves_legacy_switch_to_the_new_name(tmp_path, monkeypatch):
    """Старый ключ drug_inserts при сохранении переезжает в named_inserts и из профиля уходит —
    иначе он перебивал бы новую галку на чтении."""
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(tmp_path / "speakers"))
    key, _path = speakers.save("spk", {"drug_inserts": False})
    prof = speakers.load(key) or {}
    assert prof.get("named_inserts") is False and "drug_inserts" not in prof, prof


# ================== 5. ПОИСК В БАЗЕ, ОБЯЗАТЕЛЬНЫЕ И ШТРАФНЫЕ СЛОВА ==================
def test_picture_is_the_package_not_the_scene(tmp_path, monkeypatch):
    """Картинка предмета — его УПАКОВКА (обязательное слово), а не сюжет вокруг него.

    Запись с чеком в имени шла в базе ПЕРВОЙ; запись без слова упаковки — тоже. Теперь:
    без обязательного слова запись не годится; при наличии — меньше штрафных слов; при
    равенстве — короче имя файла.
    """
    _library(tmp_path, monkeypatch, [
        {"name": "receipt-coffee-machine-box.png"},   # упаковка, но чек: штраф
        {"name": "coffee-tag.png"},                   # ценник без упаковки: без штрафа и короче
        {"name": MACHINE},                            # упаковка без штрафа — единственно годная
    ])
    hit = _find(["кофемашина", "coffee machine"])
    assert hit and os.path.basename(str(hit["path"])) == MACHINE, hit


def test_name_without_package_word_gets_no_picture(tmp_path, monkeypatch):
    """Есть только чек с предметом, упаковки нет -> картинки нет (вставки нет)."""
    _library(tmp_path, monkeypatch, [{"name": "receipt-for-coffee-machine.png"}])
    assert _find(["кофемашина", "coffee machine"]) is None


def test_name_in_file_name_beats_description_only(tmp_path, monkeypatch):
    """(а) Название в ИМЕНИ файла выигрывает у записи, где оно только в описании,
    даже если у той имя короче: порядок ключа — уровень, имя файла, потом длина."""
    _library(tmp_path, monkeypatch, [
        {"name": "img-0007-box.png", "desc": "coffee machine box"},
        {"name": "coffee-machine-box-with-cap.png"},
    ])
    hit = _find(["кофемашина", "coffee machine"])
    assert hit and os.path.basename(str(hit["path"])) == "coffee-machine-box-with-cap.png", hit


def test_secondary_form_does_not_take_the_main_pictures_place(tmp_path, monkeypatch):
    """(б) Вторичная форма («эспрессо») — не основное название: при наличии картинки
    основного предмета картинка другого предмета с этой формой ему не достаётся."""
    _library(tmp_path, monkeypatch, [
        {"name": "espresso-grinder-box.png"},
        {"name": "coffee-machine-in-box-with-filter.png"},
    ])
    hit = _find(["кофемашина", "coffee machine"], secondary=["эспрессо", "espresso"])
    assert hit and os.path.basename(str(hit["path"])) == "coffee-machine-in-box-with-filter.png", hit


def test_secondary_form_is_used_only_without_main_form(tmp_path, monkeypatch):
    """Вторичная форма ищет картинку только если по основным совпадений вовсе нет."""
    _library(tmp_path, monkeypatch, [{"name": "espresso-grinder-box.png"}])
    hit = _find(["кофемашина", "coffee machine"], secondary=["эспрессо", "espresso"])
    assert hit and os.path.basename(str(hit["path"])) == "espresso-grinder-box.png", hit


def test_find_named_searches_name_description_and_ru(tmp_path, monkeypatch):
    """find_named: имя файла, описание (desc/ru) и пропавшие записи.

    Картинка находится не только по имени файла: у скачанных картинок бывают осмысленные
    описания, и по ним запись обязана находиться.
    """
    _library(tmp_path, monkeypatch, [
        {"name": "img_0042.png", "desc": "coffee machine in a box with filter"},
        {"name": "ноутбук-упаковка.png"},
        {"name": "laptop-box-gone.png", "gone": True},
    ])
    hit = _find(["кофемашина", "coffee machine"])
    assert hit and os.path.basename(str(hit["path"])) == "img_0042.png", hit
    hit_ru = _find(["ноутбук", "laptop"])
    assert hit_ru and os.path.basename(str(hit_ru["path"])) == "ноутбук-упаковка.png", hit_ru
    assert _find(["препарата-нет"]) is None
    assert _find([]) is None


def test_prefer_star_matches_word_start_only():
    """«box*» ловит «boxes», а короткое «pen» не ловит «pending»."""
    rules = insertlib._word_rules(["pen", "box*"])
    assert insertlib._has_word({"pending"}, rules) is False
    assert insertlib._has_word({"pen"}, rules) is True
    assert insertlib._has_word({"boxes"}, rules) is True


def test_word_stem_compares_words_by_their_basis():
    """Основа слова: падежи срезаются, похожие слова не путаются."""
    assert insertlib.word_stem("Кофемашины") == "кофемашин"
    assert insertlib.word_stem("ноутбуком") == "ноутбук"
    assert insertlib.word_stem("деки") == "дек"
    assert insertlib.stem_match("кофемашины", "кофемашина") is True
    assert insertlib.stem_match("деки", "дека") is True
    assert insertlib.stem_match("laptop", "laptop") is True
    # Одностороннего совпадения по началу мало: «кофе» — не «кофемашина».
    assert insertlib.stem_match("кофемашина", "кофе") is False
    assert insertlib.stem_match("кофе машина", "кофе") is False
