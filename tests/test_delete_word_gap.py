# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание MJ: удалённое слово отдаёт своё время следующему.

Владелец (2026-09-19): «Удалил несколько слов — место не занялось следующим словом, и
сломался слайдер». В `reelsi_batch.aep` после «РЕКОМЕНДУЕТ» (37.083–37.617) в субтитрах
осталась дыра 1.2 с, а «2.0» (Slider Control) всплывало только в 38.817.

`delete_word` вынимал клип слова из дорожки, а соседей это не двигает: время удалённого
пропадало. Теперь, если следующее слово шло подряд (зазор не больше
`highlights.GAP_JOIN_SEC` = 0.3 с), его начало переносится на начало удаляемого, конец не
меняется; несколько удалений подряд работают цепочкой. Всё производное от старта слова
считает сборка: счётчик (Slider Control) ставит ключи на `t0 = sw[0]/FPS`, то есть на
новый старт слова, а не на своё сохранённое время.

Фикстура `tests/fixtures/timeline_subs.xml.gz` (60 fps, слова идут подряд, зазор 0 кадров):
  4 «ТЕТУ» 67–89 -> 5 «ИПИСЦ» 89–112 (впритык — сдвиг), 6 «ГЭЛИТ» 114–142 (зазор 2 кадра);
  30 «ПИ» 734–744 -> 31 «ЭЛИТСЕДД» 768 (зазор 24 кадра = 0.4 с — это пауза, НЕ сдвиг);
  252 — последнее слово дорожки.
"""
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from core import xml2ae  # noqa: E402
from core.xml2ae import highlights  # noqa: E402
from core.xml2ae.layout import HL_DUR  # noqa: E402
from tests.test_hl_short import _STAND, _stand_code  # noqa: E402

node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")

FPS = 60.0
GAP_WORD = 4        # «ТЕТУ» 67–89: следующее слово идёт впритык
PAUSE_WORD = 30     # «ПИ» 734–744: следующее через 24 кадра (0.4 с) — пауза
NUMBER = "2.0"      # слово-число со счётчиком — как «2.0» из жалобы владельца

# addFX в боевом .jsx определён в шаблоне; в стенде его нет, а без него блок счётчика
# молча уходит в свой try/catch и ключей Slider Control не видно.
_ADD_FX = "function addFX(L, mn){ return L.property('ADBE Effect Parade').addProperty(mn); }\n"


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _subs(xml_path):
    """Слова по parse_full: [(start, end, word), ...] в кадрах."""
    return xml2ae.parse_full(xml_path)[2]


def _clip_element(xml_path, idx):
    """Клип-слово #idx (порядок parse_full) — сырой элемент XML."""
    root = ET.parse(xml_path).getroot()
    return highlights._sub_items(root.find(".//sequence"))[idx][2]


def _clip_fields(xml_path, idx):
    """Тайминги клипа-слова #idx: start/end и окно источника in/out + такты Премьера."""
    c = _clip_element(xml_path, idx)

    def v(tag):
        el = c.find(tag)
        return int(el.text) if el is not None and el.text is not None and el.text.strip() else None

    return dict(start=v("start"), end=v("end"), sin=v("in"), sout=v("out"),
                tin=v("pproTicksIn"), tout=v("pproTicksOut"))


def _set_word(xml_path, idx, text):
    """Переименовать слово #idx — как edit_word, но без блоба: сборке .jsx хватает
    имени эффекта (его читает parse_full)."""
    root = ET.parse(xml_path).getroot()
    items = highlights._sub_items(root.find(".//sequence"))
    items[idx][3].find("name").text = text
    ET.ElementTree(root).write(xml_path, encoding="utf-8", xml_declaration=True)


def _build(xml_path, tmp_path, hl_count=None, name="out.jsx"):
    path = str(tmp_path / name)
    xml2ae.to_ae_full(xml_path, jsx_path=path, hl_count=hl_count, intro=[], intro_remove=[],
                      intro_splits=[], disclaimer="", emit=lambda *a, **k: None)
    return open(path, encoding="utf-8-sig").read()


def _json_var(jsx, name="SUBS"):
    """Данные цикла из .jsx: var <name> = [...];"""
    m = re.search(r"var %s=(\[.*?\]);" % name, jsx)
    assert m, "в .jsx нет var %s" % name
    return json.loads(m.group(1))


def _run_loop(jsx, tmp_path, name="stand.js"):
    """Исполнить собранные циклы субтитров в node с заглушками AE (приём tests/test_hl_short)
    плюс addFX: проверяются реальные ключи слоёв, а не текст .jsx."""
    script = _STAND.replace("__CODE__", _ADD_FX + _stand_code(jsx))
    f = tmp_path / name
    f.write_text(script, encoding="utf-8")
    res = subprocess.run(["node", str(f)], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert res.returncode == 0, "node упал: %s" % (res.stderr or "")[-2000:]
    data = json.loads(res.stdout.strip().splitlines()[-1])
    assert not data["logs"], "стенд записал ошибки в _LOG: %s" % data["logs"]
    return data


def _slider_keys(layer):
    """Ключи счётчика слоя: [t, значение]."""
    fx = layer["fx"]["ADBE Slider Control"][0]
    return fx["params"]["ADBE Slider Control-0001"]["keys"]


# ------------------------------------------------- 1. слово в середине: время переходит
def test_next_word_takes_deleted_start(xml_subs):
    """Удалено слово в середине: следующее начинается там, где начиналось удалённое,
    конец у него прежний, остальные слова не сдвинулись."""
    before = _subs(xml_subs)
    gap = before[GAP_WORD + 1][0] - before[GAP_WORD][1]
    assert gap <= highlights.GAP_JOIN_SEC * FPS, "фикстура: слова не идут подряд"
    nxt_before = _clip_fields(xml_subs, GAP_WORD + 1)

    res = xml2ae.delete_word(xml_subs, GAP_WORD)
    assert res.get("ok") is True
    assert res.get("index") == GAP_WORD
    assert res.get("word") == before[GAP_WORD][2]

    after = _subs(xml_subs)
    assert len(after) == len(before) - 1
    assert after[:GAP_WORD] == before[:GAP_WORD], "слова до удалённого поехали"
    # Следующее слово: начало — от удалённого, конец и текст — свои.
    assert after[GAP_WORD] == (before[GAP_WORD][0], before[GAP_WORD + 1][1],
                               before[GAP_WORD + 1][2])
    assert after[GAP_WORD + 1:] == before[GAP_WORD + 2:], "остальные слова сдвинулись"

    # Окно источника растянулось вместе с клипом (out = in + длина клипа), а не разъехалось:
    # клип в Премьере остаётся целым, in-point графики не тронут.
    nxt_after = _clip_fields(xml_subs, GAP_WORD)
    assert nxt_after["end"] == nxt_before["end"], "конец слова уехал"
    assert nxt_after["sin"] == nxt_before["sin"], "in-point источника сдвинулся"
    assert nxt_after["sout"] - nxt_after["sin"] == nxt_after["end"] - nxt_after["start"], \
        "длительность источника разошлась с длительностью клипа"
    assert nxt_after["tin"] == nxt_before["tin"]
    tpu_before = (nxt_before["tout"] - nxt_before["tin"]) / (nxt_before["sout"] - nxt_before["sin"])
    tpu_after = (nxt_after["tout"] - nxt_after["tin"]) / (nxt_after["sout"] - nxt_after["sin"])
    assert tpu_after == pytest.approx(tpu_before), "такты pproTicks пересчитаны другим тактом"


# --------------------------------------- 2. пауза больше 0.3 с / последнее слово дорожки
def test_next_word_after_pause_stays_put(xml_subs):
    """Удалённое слово перед паузой > 0.3 с: следующее не двигается — на месте удалённого
    должна остаться тишина."""
    before = _subs(xml_subs)
    gap = before[PAUSE_WORD + 1][0] - before[PAUSE_WORD][1]
    assert gap > highlights.GAP_JOIN_SEC * FPS, "фикстура: после слова не пауза"

    res = xml2ae.delete_word(xml_subs, PAUSE_WORD)
    assert res.get("ok") is True

    after = _subs(xml_subs)
    assert len(after) == len(before) - 1
    assert after[:PAUSE_WORD] == before[:PAUSE_WORD]
    assert after[PAUSE_WORD:] == before[PAUSE_WORD + 1:], "слово после паузы поехало"


def test_last_word_of_track_moves_nothing(xml_subs):
    """Последнее слово дорожки: отдавать время некому, все прежние слова на местах."""
    before = _subs(xml_subs)
    res = xml2ae.delete_word(xml_subs, len(before) - 1)
    assert res.get("ok") is True
    assert _subs(xml_subs) == before[:-1]


# ------------------------------------------------------ 3. два удаления подряд — цепочка
def test_two_deletions_chain_to_first_start(xml_subs):
    """Два удаления подряд: следующее слово начинается со старта ПЕРВОГО удалённого."""
    before = _subs(xml_subs)
    first_start = before[GAP_WORD][0]

    xml2ae.delete_word(xml_subs, GAP_WORD)                 # «ТЕТУ»
    mid = _subs(xml_subs)
    assert mid[GAP_WORD][0] == first_start, "первое удаление не отдало время"

    xml2ae.delete_word(xml_subs, GAP_WORD)                 # «ИПИСЦ» — уже с чужим началом
    after = _subs(xml_subs)
    assert len(after) == len(before) - 2
    assert after[:GAP_WORD] == before[:GAP_WORD]
    third = after[GAP_WORD]                                # «ГЭЛИТ» (был через 2 кадра)
    assert third[2] == before[GAP_WORD + 2][2]
    assert third[0] == first_start, "цепочка не дотянулась до начала первого удалённого"
    assert third[1] == before[GAP_WORD + 2][1], "конец третьего слова уехал"


# ------------------------------------- 4. слово-число со счётчиком берёт новый старт
def test_counter_word_row_starts_at_new_start(xml_subs, tmp_path):
    """Слово-число со счётчиком после удалённого: в собранном .jsx его старт — новый,
    а ключи счётчика сборка ставит от старта слова (t0 = sw[0]/FPS), не от сохранённого."""
    _set_word(xml_subs, GAP_WORD + 1, NUMBER)
    before = _subs(xml_subs)
    old_start = before[GAP_WORD + 1][0]

    xml2ae.delete_word(xml_subs, GAP_WORD)
    after = _subs(xml_subs)
    new_start = after[GAP_WORD][0]
    assert after[GAP_WORD][2] == NUMBER
    assert new_start == before[GAP_WORD][0] < old_start

    jsx = _build(xml_subs, tmp_path, hl_count=[GAP_WORD], name="cnt.jsx")
    rows = [r for r in _json_var(jsx, "SUBS") if r[2] == NUMBER]
    assert len(rows) == 1, "слова-числа в данных сборки нет"
    row = rows[0]                      # [начало, конец, слово, hl, ряд, gend, счётчик] в КАДРАХ
    assert row[0] == new_start, "старт слова в .jsx не новый"
    assert row[0] != old_start, "старт слова остался прежним"
    assert row[1] == after[GAP_WORD][1], "конец слова уехал"
    assert row[6] == [2.0, 'effect("Slider Control")("Slider").value.toFixed(1)']

    # Ключи счётчика — от старта слова, а не от своего сохранённого времени.
    assert "var t0 = sw[0]/FPS;" in jsx
    assert "slP.setValueAtTime(t0, 0);" in jsx
    assert "slP.setValueAtTime(t0 + HL_DUR, cnt[0]);" in jsx


@node
def test_counter_slider_keys_start_at_new_start(xml_subs, tmp_path):
    """То же на исполненном цикле: слой «2.0» появляется с нового старта, и оба ключа
    Slider Control стоят от него (0 -> 2.0), а не от старта до удаления."""
    _set_word(xml_subs, GAP_WORD + 1, NUMBER)
    before = _subs(xml_subs)
    old_start = before[GAP_WORD + 1][0]

    xml2ae.delete_word(xml_subs, GAP_WORD)
    after = _subs(xml_subs)
    new_start = after[GAP_WORD][0]

    jsx = _build(xml_subs, tmp_path, hl_count=[GAP_WORD], name="cnt_node.jsx")
    run = _run_loop(jsx, tmp_path, "cnt_stand.js")
    hits = [l for l in run["layers"] if l["text"] == NUMBER]
    assert len(hits) == 1, "слоёв со словом-числом: %d" % len(hits)

    t0 = new_start / FPS
    lay = hits[0]
    assert lay["inPoint"] == pytest.approx(t0), "слово в AE появляется не с нового старта"
    assert lay["outPoint"] == pytest.approx(after[GAP_WORD][1] / FPS), "конец слоя уехал"
    keys = _slider_keys(lay)
    assert len(keys) == 2, "ключей счётчика не два: %r" % (keys,)
    assert keys[0] == pytest.approx([t0, 0]), "первый ключ счётчика не на новом старте"
    assert keys[1] == pytest.approx([t0 + HL_DUR, 2.0]), "второй ключ счётчика не от старта"
    assert keys[0][0] != pytest.approx(old_start / FPS), "ключи остались на старом старте"
