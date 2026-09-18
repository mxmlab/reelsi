# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание ZL: интро собирается и в режиме строк субтитров (`sub_words_per_row` > 1).

Задание CH запрещало сборку интро при строках длиннее одного слова («со строками эта
связь ещё не продумана»). Связь как раз прямая и уже была в коде: слова интро вынимаются
из `subs` (`intro_remove` -> переиндексация `hl/brk/cnt/joins`) РАНЬШЕ, чем строятся
строки (`raw_lines`), поэтому строки собираются из оставшихся слов, а интро от режима
субтитров не зависит — отдельного правила ему не нужно.

Здесь:
  * `plan["intro"]` и `INTRO_GROUPS` в `.jsx` при `sub_words_per_row=3` равны тому, что
    при `sub_words_per_row=1` (группа из двух строк — проверяется и раскладка строк);
  * кегль интро в режиме строк — кегль ДО ужатия строк, а не ужатый автофит субтитров
    (доработка ZL): `plan["intro_fsize"]` и `dd.fontSize` в `.jsx` — как при одном слове;
  * слов из `intro_remove` нет ни в одной строке (`plan["subs"]`, `SUB_ROWS`), остальные
    все на месте и по порядку; `.srt` в режиме строк пишет те же слова, что режим по слову;
  * жёлтые и склейки после переиндексации — на своих словах: слово, жёлтое до удаления
    интро, жёлтое и в строке.
"""
import gzip
import json
import os
import re
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import fonts, xml2ae  # noqa: E402

T_CAM1, T_CAM2 = 1.0, 8.3        # окна камер фикстуры: 1-я секунда — кам1, 8-я — перебивка
# Та же обрезка знаков по краям, что делает core/subs.build_sub_rows: в строке слово
# показывается без точки и запятой, и ожидание в тесте обязано совпадать со строками.
PUNCT = ".,!?;:…«»\"'()[]-‐‑ "


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _clean(w):
    """Слово, как его увидит строка субтитров: без знаков по краям (core/subs)."""
    return str(w).strip(PUNCT).strip()


def _intro_of(subs, indexes):
    """Строки интро по ИСХОДНЫМ индексам слов фикстуры: первая — на кам1, вторая — на
    перебивке (окна камер в фикстуре), поэтому группа интро уезжает на свой нул."""
    times = [T_CAM1, T_CAM2]
    return [dict(words=[subs[k][2]], color="white", times=[times[i]])
            for i, k in enumerate(indexes)]


def _plan(xml, per_row, intro, remove, splits=(1,), **kw):
    return xml2ae.scene_plan(xml, disclaimer="", intro=intro, intro_remove=list(remove),
                             intro_splits=list(splits), style={"sub_words_per_row": per_row},
                             emit=lambda *a: None, **kw)


def _build(xml, tmp_path, per_row, intro, remove, splits=(1,), name="out.jsx", **kw):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name), disclaimer="",
                                   intro=intro, intro_remove=list(remove),
                                   intro_splits=list(splits),
                                   style={"sub_words_per_row": per_row},
                                   emit=lambda *a: None, **kw)
    return open(path, encoding="utf-8-sig").read(), path


def _groups(jsx):
    return json.loads(re.search(r"var INTRO_GROUPS=(\[.*?\]);", jsx).group(1))


def _intro_font(jsx):
    """Кегль текста интро из .jsx: подстановка introDoc — `FONT_SIZE` (кегли субтитров и
    интро совпали, режим по слову) либо число (интро неужатого кегля). Строка ищется по
    `fauxBold` — только у introDoc он зависит от col, у дисклеймера кегль свой."""
    return re.search(r"&&HL_BOLD\);\}catch\(e\)\{\} dd\.fontSize=([^;]+);", jsx).group(1)


def _sub_font(jsx):
    return int(re.search(r"var FONT_SIZE = (\d+)", jsx).group(1))


def _sub_rows(jsx):
    return json.loads(re.search(r"var SUB_ROWS\s*=\s*(\[.*?\]);", jsx).group(1))


def _row_words(plan):
    """Слова строк плана по порядку — так же, как они лягут в титр."""
    return [wd["w"] for line in plan["subs"] for wd in (line.get("words") or [])]


def _kept_words(subs, remove):
    """Ожидание: слова фикстуры без ушедших в интро, по порядку (многословные элементы
    build_sub_rows разворачивает на слова)."""
    return [_clean(p) for k, (_s, _e, w) in enumerate(subs) if k not in remove
            for p in str(w).split()]


def _srt_words(path):
    """Слова из .srt по порядку: индекс и время реплики — служебные строки."""
    words = []
    for block in open(path, encoding="utf-8").read().strip().split("\n\n"):
        lines = [ln for ln in block.strip().splitlines() if ln.strip()]
        words += " ".join(lines[2:]).split()
    return words


def test_plan_and_groups_equal_in_rows_and_word_modes(xml_subs, tmp_path):
    """plan["intro"] и INTRO_GROUPS при 3 словах в строке — те же, что при одном слове.

    Группа из двух строк: равенство ловит не только строки и времена, но и Y базовых
    линий (ys) — раскладка строк интро в режиме строк не пересчитывается по-своему.
    """
    intro = [dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
             dict(words=["ВТОРОЕ"], color="white", times=[T_CAM1 + 0.5]),
             dict(words=["СДО*НУТЬ"], color="white", times=[T_CAM2])]
    splits = (2,)                      # группа 0 — две строки, группа 1 — одна

    p1 = _plan(xml_subs, 1, intro, [0], splits=splits)
    p3 = _plan(xml_subs, 3, intro, [0], splits=splits)

    assert len(p1["intro"]) == 2 and len(p1["intro"][0]["lines"]) == 2
    assert p1["intro"] == p3["intro"]
    assert p3["intro"], "интро в режиме строк обязано собираться"

    jsx1, _ = _build(xml_subs, tmp_path, 1, intro, [0], splits=splits, name="w1.jsx")
    jsx3, _ = _build(xml_subs, tmp_path, 3, intro, [0], splits=splits, name="w3.jsx")

    assert _groups(jsx1) == _groups(jsx3) != []
    assert len(_groups(jsx3)) == 2
    # в плане и в .jsx группы одни и те же (шаблон читает INTRO_GROUPS, превью — план)
    assert p3["_ae"]["intro_groups"] != "[]"
    assert _groups(jsx3) == [p["lines"] for p in p3["intro"]]


def test_intro_words_absent_from_rows(xml_subs, tmp_path):
    """Слов из intro_remove нет ни в одной строке, остальные все на месте и по порядку."""
    _meta, _cams, subs, _ins = xml2ae.parse_full(xml_subs)
    remove = [0, 1]                    # «ЛОРЕМ», «СУ» — в фикстуре встречаются по разу
    kept = _kept_words(subs, remove)
    intro = _intro_of(subs, remove)

    p3 = _plan(xml_subs, 3, intro, remove)
    rows = _row_words(p3)
    assert rows == kept

    # Уникальность слов интро проверяем явно: иначе «нет в строках» ловило бы совпадение
    # строк, а не само ушедшее в интро слово.
    all_words = [_clean(w) for _s, _e, w in subs]
    for k in remove:
        assert all_words.count(_clean(subs[k][2])) == 1
        assert _clean(subs[k][2]) not in rows

    jsx3, _ = _build(xml_subs, tmp_path, 3, intro, remove, name="rows.jsx")
    jsx_rows = [wd[1] for _s, _e, _row, _fsz, wds in _sub_rows(jsx3) for wd in wds]
    assert jsx_rows == kept, "SUB_ROWS в .jsx собираются из тех же слов"


def test_srt_rows_keeps_same_intro_rule(xml_subs, tmp_path):
    """.srt в режиме строк — те же слова, что в режиме по слову: слова интро вынуты.

    Второго правила про интро .srt не заводит: он пишет `plan["subs"]`, а в нём слов интро
    уже нет — их вынимает общий блок переиндексации, один на оба режима.
    """
    _meta, _cams, subs, _ins = xml2ae.parse_full(xml_subs)
    remove = [0, 1]
    kept = _kept_words(subs, remove)
    intro = _intro_of(subs, remove)

    _jsx1, path1 = _build(xml_subs, tmp_path, 1, intro, remove, name="srt1.jsx")
    _jsx3, path3 = _build(xml_subs, tmp_path, 3, intro, remove, name="srt3.jsx")

    words1 = _srt_words(os.path.splitext(path1)[0] + ".srt")
    words3 = _srt_words(os.path.splitext(path3)[0] + ".srt")

    assert words3 == kept
    assert words1 == kept
    for k in remove:
        assert _clean(subs[k][2]) not in words3
        assert _clean(subs[k][2]) not in words1


def test_yellow_and_joins_stay_on_their_words(xml_subs, tmp_path):
    """Жёлтые и склейки после удаления интро — на своих словах, и в строках тоже.

    Разметка (highlights/hl_joins) приходит из UI по ИСХОДНЫМ индексам слов; удаление слов
    интро её переиндексирует, и проверка тут — что жёлтым остаётся ровно то слово, которое
    человек пометил: в режиме по слову (там же видна и склейка — два слова в одном ряду)
    и в режиме строк.
    """
    _meta, _cams, subs, _ins = xml2ae.parse_full(xml_subs)
    remove = [0, 1]                    # интро: «ЛОРЕМ» и «СУ»
    intro = _intro_of(subs, remove)
    hl, joins = [2, 3], [2]            # «РС*И» и «Т» — жёлтые, склеены в одно слово

    p1 = _plan(xml_subs, 1, intro, remove, highlights=hl, hl_joins=joins)
    assert [x["w"] for x in p1["subs"][:2]] == [subs[2][2], subs[3][2]]
    assert all(x["color"] == "yellow" for x in p1["subs"][:2])
    # склейка: два жёлтых слова стоят в одном ряду стопки (режим по слову)
    assert p1["subs"][0]["row"] == p1["subs"][1]["row"]

    p3 = _plan(xml_subs, 3, intro, remove, highlights=hl, hl_joins=joins)
    yellow = [wd["w"] for line in p3["subs"] for wd in line["words"]
              if wd["color"] == "yellow"]
    assert yellow == [subs[2][2], subs[3][2]]

    jsx3, _ = _build(xml_subs, tmp_path, 3, intro, remove, name="hl3.jsx",
                     highlights=hl, hl_joins=joins)
    yellow_js = [wd[1] for _s, _e, _row, _fsz, wds in _sub_rows(jsx3)
                 for wd in wds if wd[2] == 1]
    assert yellow_js == [subs[2][2], subs[3][2]]


def test_intro_font_size_not_shrunk_by_rows(xml_subs, tmp_path, monkeypatch):
    """Кегль интро в режиме строк — неужатый кегль субтитров, а не автофит строк (ZL).

    Автофит строк ужимает единый FONT_SIZE субтитров под самую длинную строку, а интро
    в AE читало тот же FONT_SIZE и выходило в разы мельче, чем в режиме по слову. Теперь
    у интро свой кегль: `plan["intro_fsize"]` (и `dd.fontSize` в `.jsx`) — кегль режима по
    слову, а `plan["fsize"]` субтитров остаётся ужатым.
    """
    # Ширина текста — программно (0.6 кегля на знак), как в tests/test_zoom_ze.py: автофит
    # строк меряет `core.fonts.text_width` по ФАЙЛАМ шрифтов, а на раннере CI шрифтов нет —
    # функция возвращает None, кегль не ужимается вовсе, и проверка «строки реально ужали»
    # падала на 140 < 140, не поймав ничего. Тест обязан мерить известной шириной.
    monkeypatch.setattr(fonts, "text_width",
                        lambda ps, text, size: float(size) * len(text or "") * 0.6)

    _meta, _cams, subs, _ins = xml2ae.parse_full(xml_subs)
    remove = [0, 1]
    intro = _intro_of(subs, remove)

    p1 = _plan(xml_subs, 1, intro, remove)
    p3 = _plan(xml_subs, 3, intro, remove)

    # строки при 3 словах реально ужали кегль субтитров — иначе проверка ничего не ловит
    assert p3["fsize"] < p1["fsize"]
    assert p1["intro_fsize"] == p1["fsize"]
    assert p3["intro_fsize"] == p1["fsize"], "интро не должно ужиматься со строками"

    jsx1, _ = _build(xml_subs, tmp_path, 1, intro, remove, name="fs1.jsx")
    jsx3, _ = _build(xml_subs, tmp_path, 3, intro, remove, name="fs3.jsx")

    # режим по слову: кегли совпали — подстановка остаётся FONT_SIZE (.jsx прежний)
    assert _intro_font(jsx1) == "FONT_SIZE"
    assert _sub_font(jsx1) == p1["fsize"]
    assert _sub_font(jsx3) == p3["fsize"]
    assert _intro_font(jsx3) == str(p1["fsize"])
