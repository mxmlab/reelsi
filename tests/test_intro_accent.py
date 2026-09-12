# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Акцентный шрифт на строке интро (задание R).

Флаг — на СТРОКЕ группы интро рядом с color (в разобранном проекте 10 слов переведены
на другой шрифт и регистр, 9 из 10 занимают отдельную строку). Ключи стиля:
accent_font (PostScript-имя, пусто = выключено) и accent_case (title/as-is/upper).

Здесь:
  * golden: без единой галки .jsx побайтово прежний (плейсхолдеры шаблона пусты);
  * со строкой-акцентом — слово уезжает в .jsx с accent_font и в нужном регистре,
    цвет строки не меняется;
  * пустой accent_font — галка ничего не делает (данные строки прежние);
  * план несёт accent_font + готовый регистр (превью рисует тем же шрифтом);
  * регистры title / as-is / upper.
"""
import gzip
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import app_meta  # noqa: E402
from core import xml2ae  # noqa: E402
from core.xml2ae.build import _accent_word  # noqa: E402

T_CAM1, T_CAM2 = 1.0, 8.3          # секунды: в кадре Камера 1 / перебивка (см. test_intro_on_cam2)

node = pytest.mark.skipif(not shutil.which("node"), reason="контракт фронта требует node в PATH")


def _func(src, name):
    """Вырезать `function name(...){...}` целиком по балансу скобок."""
    m = re.search(r"function\s+%s\s*\(" % re.escape(name), src)
    assert m, f"в исходнике не нашлась функция {name}"
    i = src.index("{", m.end() - 1)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():j + 1]
    raise AssertionError(f"не сошлись скобки у {name}")


def _run_node(script):
    p = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=60)
    assert p.returncode == 0, p.stderr.strip()[:400]
    return json.loads(p.stdout)


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _build(xml, tmp_path, intro, style=None, mode="word"):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / "out.jsx"), intro=intro,
                                   intro_splits=[1], style=style or {},
                                   intro_mode=mode, disclaimer="", emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read()


def _groups(jsx):
    return json.loads(re.search(r"var INTRO_GROUPS=(\[.*?\]);", jsx).group(1))


def _plain_intro():
    return [dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
            dict(words=["СДО*НУТЬ"], color="white", times=[T_CAM2])]


def test_без_галок_jsx_побайтово_прежний(xml_subs, tmp_path):
    """Стиль без accent_font и без галок — .jsx как был: плейсхолдеры шаблона пусты."""
    jsx = _build(xml_subs, tmp_path, _plain_intro(), style={"accent_font": "", "accent_case": "title"})
    for g in _groups(jsx):
        for ln in g:
            assert "accent_font" not in ln
            assert ln["words"] == ln["words"]  # регистр не тронут (проверим ниже отдельно)
    # галка в данных при ПУСТОМ accent_font не должна ничего менять
    with_acc = _build(xml_subs, tmp_path,
                      [dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1], accent=True),
                       dict(words=["СДО*НУТЬ"], color="white", times=[T_CAM2])],
                      style={"accent_font": ""})
    assert with_acc == jsx


def test_акцент_меняет_шрифт_и_регистр(xml_subs, tmp_path):
    jsx = _build(xml_subs, tmp_path, _plain_intro(),
                 style={"accent_font": "TeddyBear-Regular", "accent_case": "title"})
    # галки нет — прежний
    assert "accent_font" not in _groups(jsx)[0][0]
    jsx = _build(xml_subs, tmp_path,
                 [dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
                  dict(words=["СДО*НУТЬ"], color="white", times=[T_CAM2], accent=True)],
                 style={"accent_font": "TeddyBear-Regular", "accent_case": "title"})
    g1, g2 = _groups(jsx)
    assert "accent_font" not in g1[0]                    # строка без галки не тронута
    assert g2[0]["accent_font"] == "TeddyBear-Regular"
    assert g2[0]["words"] == ["Сдо*нуть"]                # title: Заглавная первая
    assert g2[0]["color"] == "white"                     # цвет строки прежний
    # introDoc принимает акцентный шрифт отдельным аргументом
    assert "function introDoc(tl, txt, col,af)" in jsx
    assert ",ln.accent_font)" in jsx


def test_акцент_не_ломает_цвет_жёлтого(xml_subs, tmp_path):
    """Акцент НЕ переопределяет цвет: жёлтая строка остаётся жёлтой."""
    jsx = _build(xml_subs, tmp_path,
                 [dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
                  dict(words=["ЛИБИДО"], color="yellow", times=[T_CAM2], accent=True)],
                 style={"accent_font": "TeddyBear-Regular", "accent_case": "title"})
    g1, g2 = _groups(jsx)
    assert g2[0]["color"] == "yellow"
    assert g2[0]["accent_font"] == "TeddyBear-Regular"


def test_регистры_upper_as_is(xml_subs, tmp_path):
    jsx = _build(xml_subs, tmp_path,
                 [dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
                  dict(words=["качественную"], color="white", times=[T_CAM2], accent=True)],
                 style={"accent_font": "F", "accent_case": "upper"})
    assert _groups(jsx)[1][0]["words"] == ["КАЧЕСТВЕННУЮ"]
    jsx = _build(xml_subs, tmp_path,
                 [dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
                  dict(words=["Качественную"], color="white", times=[T_CAM2], accent=True)],
                 style={"accent_font": "F", "accent_case": "as-is"})
    assert _groups(jsx)[1][0]["words"] == ["Качественную"]


def test_акцент_в_плане_несёт_шрифт_и_регистр(xml_subs):
    """Превью рисует тем же шрифтом и тем же текстом: план несёт accent_font и готовый регистр."""
    plan = xml2ae.scene_plan(xml_subs, disclaimer="", intro=[
        dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
        dict(words=["СДО*НУТЬ"], color="white", times=[T_CAM2], accent=True)],
        intro_splits=[1], style={"accent_font": "TeddyBear-Regular", "accent_case": "title"})
    lines = plan["intro"][1]["lines"]
    assert lines[0]["accent_font"] == "TeddyBear-Regular"
    assert lines[0]["words"] == ["Сдо*нуть"]
    assert lines[0]["color"] == "white"


def test_пустой_accent_font_галку_игнорирует(xml_subs, tmp_path):
    """Пустой accent_font — галка ничего не делает (и это видно): данные строки прежние."""
    jsx = _build(xml_subs, tmp_path, _plain_intro(), style={"accent_font": "F"})
    no_acc = _build(xml_subs, tmp_path, _plain_intro(), style={})
    assert jsx == no_acc


def test_accent_word():
    assert _accent_word("СДО*НУТЬ", "title") == "Сдо*нуть"
    assert _accent_word("СДО*НУТЬ", "as-is") == "СДО*НУТЬ"
    assert _accent_word("качественную", "upper") == "КАЧЕСТВЕННУЮ"
    assert _accent_word("", "title") == ""


@node
def test_frontend_resolve_carries_accent_on_lines():
    """resolveIntroFor проносит accent на каждой строке (флаг — на строке, не только на голове)."""
    src = app_meta.app_js_text()
    body = "\n".join(_func(src, n) for n in ("introSortRows", "resolveIntroFor"))
    out = _run_node(
        "%s\nvar WORDS=[{w:'А',start:1,i:0},{w:'Б',start:2,i:1},{w:'В',start:3,i:2},"
        "{w:'Г',start:4,i:3}];"
        "console.log(JSON.stringify(resolveIntroFor("
        "[{count:1,color:'white',accent:true},"
        "{count:1,color:'yellow',break:true,from:1}],WORDS)));" % body)
    assert out["splits"] == [1]
    assert out["lines"][0]["accent"] is True
    assert out["lines"][1]["accent"] is False
