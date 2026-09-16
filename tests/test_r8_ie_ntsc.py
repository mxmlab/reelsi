# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""NTSC-частота в разборе XML (задание IE).

Секвенцию 29.97 и Премьер, и наш же `xmlbuild` пишут как
`<timebase>30</timebase><ntsc>TRUE</ntsc>`. Пока `parse_full` читал только
`<timebase>`, частота выходила 30, а кадры XML (`start`/`end` слов, клипов,
`<duration>`) считаются в НОМИНАЛЕ: секунды = кадры/30 вместо кадры/29.97 —
за час монтажа импорт уезжал на 3.6 с.

Здесь проверяется и обратное: эталон круга 8 (60 без NTSC) разбирается ровно как
раньше — частота остаётся целым 60, а не 59.94. Эталоны не перегенерируются:
NTSC-вариант — КОПИЯ фикстуры в tmp_path с подменённым `<rate>` ТОЛЬКО у
секвенции (у клипов свои частоты, их трогать нельзя).

Запуск: py -3.10 -m pytest tests/test_r8_ie_ntsc.py -q -p no:cacheprovider
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
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

from core import xml2ae  # noqa: E402

FIX = os.path.join(HERE, "fixtures")
RATE_NTSC = "<rate><timebase>30</timebase><ntsc>TRUE</ntsc></rate>"
RATE_ZERO = "<rate><timebase>0</timebase><ntsc>FALSE</ntsc></rate>"
FPS_NTSC = 30000 / 1001                     # 29.97002997…
H = {"Host": "127.0.0.1:5001"}


def _patch_seq_rate(text, rate):
    """Частота СЕКВЕНЦИИ: первый `<rate>` после `<sequence>`. Остальные `<rate>` в
    файле — частоты исходников, у них своя жизнь (и свой timebase/ntsc)."""
    i = text.index("<sequence")
    j = text.index("<rate>", i)
    k = text.index("</rate>", j) + len("</rate>")
    return text[:j] + rate + text[k:]


def _fixture_text(name):
    """Текст эталона как есть (newline="" — иначе CRLF фикстуры превратился бы в LF)."""
    if name.endswith(".gz"):
        with gzip.open(os.path.join(FIX, name), "rb") as g:
            return g.read().decode("utf-8")
    with open(os.path.join(FIX, name), encoding="utf-8", newline="") as f:
        return f.read()


def _copy(tmp_path, name, rate=None, out=None):
    """Копия эталона в tmp_path; rate — подменить частоту секвенции."""
    text = _fixture_text(name)
    if rate is not None:
        text = _patch_seq_rate(text, rate)
    dst = str(tmp_path / (out or name.replace(".gz", "")))
    with open(dst, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return dst


@pytest.fixture
def ntsc_nosubs(tmp_path):
    return _copy(tmp_path, "timeline_nosubs.xml", RATE_NTSC)


@pytest.fixture
def ntsc_subs(tmp_path):
    return _copy(tmp_path, "timeline_subs.xml.gz", RATE_NTSC, out="timeline.xml")


# --------------------------------------------------------------------------- #
# Разбор частоты
# --------------------------------------------------------------------------- #
def test_golden_fixture_stays_60_without_ntsc(tmp_path):
    """Эталон круга 8 (60 без NTSC) — прежний разбор: целое 60, никакого 59.94."""
    meta, _cams, _subs, _ins = xml2ae.parse_full(_copy(tmp_path, "timeline_nosubs.xml"))
    assert meta["fps"] == 60 and isinstance(meta["fps"], int)
    assert meta["timebase"] == 60
    assert meta["ntsc"] is False


def test_ntsc_sequence_is_29_97(ntsc_nosubs):
    """timebase 30 + ntsc TRUE = 29.97 (30*1000/1001), а не 30."""
    meta, _cams, _subs, _ins = xml2ae.parse_full(ntsc_nosubs)
    assert meta["fps"] == pytest.approx(FPS_NTSC)
    assert meta["timebase"] == 30
    assert meta["ntsc"] is True
    assert meta["fps"] != 30


def test_ntsc_scene_plan_dur_uses_29_97(ntsc_nosubs):
    """dur плана = кадры/29.97 (было кадры/30): 10192 кадра — это 340.07 с, не 339.73."""
    plan = xml2ae.scene_plan(ntsc_nosubs, inserts=[], disclaimer="", intro_riser=False,
                             emit=lambda *a: None)
    assert plan["fps"] == pytest.approx(FPS_NTSC)
    assert plan["dur"] == pytest.approx(10192 / FPS_NTSC)
    assert plan["dur"] > 10192 / 30 + 0.3


@pytest.mark.parametrize("rate,expect,timebase,ntsc", [
    ("<rate><timebase>30</timebase><ntsc>TRUE</ntsc></rate>", 30000 / 1001, 30, True),
    ("<rate><timebase>60</timebase><ntsc>TRUE</ntsc></rate>", 60000 / 1001, 60, True),
    ("<rate><timebase>30</timebase><ntsc>true</ntsc></rate>", 30000 / 1001, 30, True),   # регистр
    ("<rate><timebase>25</timebase><ntsc>FALSE</ntsc></rate>", 25, 25, False),           # PAL
    ("<rate><timebase>60</timebase></rate>", 60, 60, False),                             # без <ntsc>
])
def test_sequence_rate_matrix(tmp_path, rate, expect, timebase, ntsc):
    """Таблица частот: NTSC — производная от номинала, всё остальное — как раньше."""
    xml = _copy(tmp_path, "timeline_nosubs.xml", rate)
    meta, _cams, _subs, _ins = xml2ae.parse_full(xml)
    assert meta["fps"] == pytest.approx(expect)
    assert meta["timebase"] == timebase and meta["ntsc"] is ntsc


def test_timebase_zero_falls_back_to_60(tmp_path):
    """`<timebase>0</timebase>` — пустая частота: 60 вместо ZeroDivisionError."""
    xml = _copy(tmp_path, "timeline_nosubs.xml", RATE_ZERO)
    meta, _cams, _subs, _ins = xml2ae.parse_full(xml)
    assert meta["fps"] == 60 and meta["timebase"] == 60
    plan = xml2ae.scene_plan(xml, inserts=[], disclaimer="", intro_riser=False,
                             emit=lambda *a: None)
    assert plan["dur"] == pytest.approx(10192 / 60)      # dur = кадры/dur секвенции


# --------------------------------------------------------------------------- #
# Потребители частоты: кадры XML делятся на НАСТОЯЩИЕ секунды
# --------------------------------------------------------------------------- #
def test_ntsc_word_seconds_use_29_97(ntsc_subs):
    """Слова: секунды = кадры/29.97 (потребитель `aicut.commands._words_from_xml`)."""
    from core.aicut.commands import _words_from_xml

    _meta, _cams, subs, _ins = xml2ae.parse_full(ntsc_subs)
    words = _words_from_xml(ntsc_subs)
    assert subs and len(words) == len(subs)
    for k in (0, len(subs) - 1):                        # первое и последнее слово
        assert words[k][2] == pytest.approx(subs[k][0] / FPS_NTSC)
        assert words[k][3] == pytest.approx(subs[k][1] / FPS_NTSC)
    # на последнем слове разница со старой арифметикой (кадры/30) — уже 0.2 с
    assert words[-1][3] > subs[-1][1] / 30 + 0.1


def test_ntsc_scene_plan_dur_and_word_times(ntsc_subs):
    """dur плана и тайминги субтитров — в секундах NTSC, а не в кадрах/30."""
    meta, _cams, _subs, _ins = xml2ae.parse_full(ntsc_subs)
    plan = xml2ae.scene_plan(ntsc_subs, inserts=[], disclaimer="", intro_riser=False,
                             emit=lambda *a: None)
    assert plan["fps"] == pytest.approx(FPS_NTSC)
    assert plan["dur"] == pytest.approx(meta["dur"] / FPS_NTSC)
    assert plan["dur"] > meta["dur"] / 30 + 0.2         # 6740 кадров: 224.667 -> 224.891
    first = plan["subs"][0]
    assert first["e"] == pytest.approx(24 / FPS_NTSC, rel=1e-4)


def test_jsx_fps_line_is_fractional(ntsc_nosubs, tmp_path):
    """В .jsx частота — дробное 29.97003, а не усечённое 29/30: по ней AE делит кадры."""
    meta, _cams, _subs, _ins = xml2ae.parse_full(ntsc_nosubs)
    jsx = open(xml2ae.to_ae_full(ntsc_nosubs, jsx_path=str(tmp_path / "out.jsx"),
                                 inserts=[], style={"intro_riser": False},
                                 disclaimer="", intro_riser=False,
                                 emit=lambda *a: None)[0], encoding="utf-8-sig").read()
    m = re.search(r"var W=(\d+), H=(\d+), FPS=([\d.]+), DUR=([\d.]+);", jsx)
    assert m, "строка W/H/FPS/DUR потерялась из шаблона"
    assert float(m.group(3)) == pytest.approx(FPS_NTSC, rel=1e-5)
    assert "FPS=29.97003," in jsx
    assert "FPS=30," not in jsx and "FPS=29," not in jsx
    assert float(m.group(4)) == pytest.approx(meta["dur"] / FPS_NTSC, rel=1e-4)


def _check_syntax(jsx_path):
    """node --check не принимает расширение .jsx — копируем в .js (как CI-джоба jsx)."""
    tmp = jsx_path + ".check.js"
    try:
        shutil.copy(jsx_path, tmp)
        p = subprocess.run(["node", "--check", tmp],
                           capture_output=True, text=True, timeout=120)
        assert p.returncode == 0, f"отрендеренный .jsx не прошёл node --check:\n{p.stderr[:500]}"
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def test_jsx_fps_line_stays_60(tmp_path):
    """Целая частота печатается как раньше (`60`) — эталон .jsx не меняется."""
    xml = _copy(tmp_path, "timeline_nosubs.xml")
    jsx = open(xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / "out.jsx"),
                                 inserts=[], style={"intro_riser": False},
                                 disclaimer="", intro_riser=False,
                                 emit=lambda *a: None)[0], encoding="utf-8-sig").read()
    assert "FPS=60," in jsx and "FPS=60.0" not in jsx


def test_rendered_ntsc_jsx_passes_node_check(ntsc_nosubs, tmp_path):
    """Собранный .jsx с дробной частотой — валидный JS, а не только верная строка."""
    if not shutil.which("node"):
        pytest.skip("node --check требует node в PATH (в CI он есть)")
    out = str(tmp_path / "ntsc.jsx")
    xml2ae.to_ae_full(ntsc_nosubs, out, inserts=[], style={"intro_riser": False},
                      disclaimer="", intro_riser=False, emit=lambda *a: None)
    _check_syntax(out)


# --------------------------------------------------------------------------- #
# Экспорт .drp: таймлайн шаблона — 60 fps
# --------------------------------------------------------------------------- #
@pytest.fixture
def client():
    from flask import Flask
    import api
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def fake_probe(monkeypatch):
    """ffprobe вместо настоящего: медиа фикстуры на диске не лежит."""
    from core import xmlbuild
    monkeypatch.setattr(xmlbuild, "probe", lambda p, still_ok=True: {
        "width": 3840, "height": 2160, "dur_s": 600.0,
        "timecode": "00;00;00;00", "fps": 29.97})


def test_export_drp_refuses_non_60(client, ntsc_nosubs):
    """NTSC-проект (29.97): шаблон .drp — таймлайн 60 fps, молча собирать нельзя."""
    d = client.post("/api/export_drp", json={"xml": ntsc_nosubs}, headers=H).get_json()
    assert d.get("ok") is not True and d["err"] == "drp_fps_unsupported", d
    assert d["err_vars"]["fps"] == pytest.approx(FPS_NTSC)
    # отказ ДО сборки: сайдкар показывает, откуда взялась частота
    proj = json.load(open(os.path.splitext(ntsc_nosubs)[0] + ".project.json",
                          encoding="utf-8"))
    assert proj["fps"] == pytest.approx(FPS_NTSC)


def test_export_drp_refuses_30_fps_project(client, tmp_path):
    """Частота проекта 30 (кадровая) — тоже отказ, а не .drp с чужой математикой."""
    xml = _copy(tmp_path, "timeline_nosubs.xml")
    _meta, cams, _subs, _ins = xml2ae.parse_full(xml)
    with open(os.path.splitext(xml)[0] + ".project.json", "w", encoding="utf-8") as f:
        json.dump({"cams": [c["path"] for c in cams], "offsets": [0.0] * len(cams),
                   "keep": [[0.0, 5.0]], "fps": 30}, f)
    d = client.post("/api/export_drp", json={"xml": xml}, headers=H).get_json()
    assert d.get("ok") is not True and d["err"] == "drp_fps_unsupported", d
    assert d["err_vars"]["fps"] == 30


def test_export_drp_still_builds_60(client, tmp_path, fake_probe):
    """Контроль: на 60 fps роут по-прежнему собирает .drp, а не отказывает."""
    xml = _copy(tmp_path, "timeline_nosubs.xml")
    r = client.post("/api/export_drp", json={"xml": xml}, headers=H)
    assert r.status_code == 200
    assert r.data[:2] == b"PK"
