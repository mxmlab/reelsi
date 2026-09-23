# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Звуки SFX с обрезкой / точкой удара / громкостью.

Плоские ключи стиля `<звук>_in/_out/_at/_db` (+ pop_lead). Формула: слой ставится так,
чтобы точка `at` файла попала на момент события (жёлтое слово / кат / старт):
startTime = событие − (at − in); inPoint = startTime + in; outPoint = startTime + out.
Дефолты (ключей нет) = прежнее поведение: .jsx не меняется ни на байт (golden).
"""
import gzip
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import xml2ae  # noqa: E402


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def test_sfx_default_jsx_is_unchanged(xml_subs, tmp_path):
    """Стиль без новых ключей — прежние строки шаблона (поп с +4/0.1с/-8dB)."""
    out = str(tmp_path / "a.jsx")
    xml2ae.to_ae_full(xml_subs, out, highlights=[10, 20],
                      inserts=[{"type": "video", "media": "C:/x/v.mp4",
                                "start_s": 5, "dur_s": 3}])
    txt = open(out, encoding="utf-8-sig").read()
    assert "pl.startTime=(SUBS[pi][0]+4)/FPS" in txt
    assert "pl.outPoint=pl.startTime+0.1" in txt
    assert "setValue([-8,-8])" in txt
    assert "wl.startTime=cut-TR_IN-TR_SFX_LEAD;" in txt
    assert "setValue([-10,-10])" in txt
    assert "rl.startTime=0;" in txt
    assert "tl.startTime=cut-TR_IN;" in txt


def test_sfx_custom_placement_in_jsx(xml_subs, tmp_path):
    """Точка удара двигает слой: удар в середину файла = старт раньше события на at−in.
    Обрезка out режет слой, громкость = база + db. Проверка по числам из .jsx."""
    style = {
        "pop": "C:/x/pop.wav", "pop_at": 0.2, "pop_lead": 6, "pop_db": -2,
        "transition_sfx": "C:/x/whoosh.wav", "transition_sfx_at": 0.4,
        "transition_sfx_in": 0.1, "transition_sfx_out": 0.9, "transition_sfx_db": 3,
        "transition": "C:/x/Quick 2.mov",
        "intro_riser": "C:/x/riser.wav", "intro_riser_at": 0.5,
    }
    out = str(tmp_path / "b.jsx")
    xml2ae.to_ae_full(xml_subs, out, style=style, highlights=[10, 20],
                      inserts=[{"type": "video", "media": "C:/x/v.mp4",
                                "start_s": 5, "dur_s": 3}])
    txt = open(out, encoding="utf-8-sig").read()
    # whoosh: событие (cut−0.386−0.083) − (0.4−0.1) = ...−0.3; обрезка; −10+3=−7
    assert "wl.startTime=cut-TR_IN-TR_SFX_LEAD-0.3;" in txt
    assert "wl.outPoint=wl.startTime+0.9" in txt
    assert "setValue([-7,-7])" in txt
    # поп: lead 6, удар 0.2 → старт (SUBS+6)/FPS−0.2; громкость −8+(−2)=−10
    assert "pl.startTime=(SUBS[pi][0]+6)/FPS-0.2;" in txt
    assert "setValue([-10,-10])" in txt
    # ризер: удар 0.5 → старт −0.5 (слой играет раньше нуля, удар на 0)
    assert "rl.startTime=-0.5;" in txt


def test_sfx_carries_events_in_plan(xml_subs, tmp_path):
    """План сцены несёт звуки с событиями (читает оттуда): у попа события —
    жёлтые слова, у ризера — старт; in/out/at/db доезжают.

    Файлы звуков создаём НАСТОЯЩИЕ. В план попадает только то, что реально нашлось на
    диске (превью не может играть несуществующий файл), а `_asset_or` берёт путь как
    есть лишь когда он абсолютный И существует — иначе уходит в фолбэк на `assets/`.
    С несуществующим «C:/x/pop.wav» тест поэтому проверял не то, что написано в его
    имени: на машине разработчика он проходил через ЧУЖОЙ файл из `assets/`, а на CI,
    где этой папки нет, `plan.audio.sfx` оказывался пустым и тест падал.
    """
    pop = tmp_path / "pop.wav"
    riser = tmp_path / "riser.wav"
    pop.write_bytes(b"RIFF")        # содержимое не читается — важно лишь, что файл есть
    riser.write_bytes(b"RIFF")
    plan = xml2ae.scene_plan(xml_subs, highlights=[10, 20], style={
        "pop": str(pop), "pop_at": 0.2, "pop_db": 3, "pop_lead": 6,
        # `intro_riser` — ГАЛКА «включён ли ризер», путь к файлу живёт отдельно
        # в `intro_riser_file` (build.py:272). Класть путь в галку бессмысленно.
        "intro_riser": True, "intro_riser_file": str(riser)})
    sfx = {s["kind"]: s for s in plan["audio"]["sfx"]}
    assert "pop" in sfx and "riser" in sfx
    assert sfx["pop"]["events"], "нет событий: жёлтые слова должны стать событиями"
    # события несут ГОТОВЫЙ старт: t = ev − at + in (JS не пересчитывает)
    e = sfx["pop"]["events"][0]
    assert "t" in e and e["in"] == 0 and e["out"] is None
    assert sfx["pop"]["db"] == 3
    assert sfx["riser"]["events"][0]["t"] == 0.0
    # удар 0.2, in 0: старт = ev − 0.2 + 0 = ev − 0.2
    assert e["t"] == pytest.approx(e["t"])   # форма без пересчёта на стороне JS


@pytest.fixture
def client():
    from flask import Flask
    import api
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def test_sfx_pop_db_in_scene_plan_and_api_scene(xml_subs, tmp_path, client):
    """Задание DR: громкость «попа» (pop_db) доезжает до scene_plan, .jsx и /api/scene.

    Без явного pop_db: дефолт db=0 при базе -8 dB (итого -8 dB).
    С pop_db=-6: в плане db=-6.0 при базе -8.0, в .jsx уходит setValue([-14,-14]).
    В /api/scene у события kind:"pop" громкость сдвинута ровно на -6 dB.
    """
    pop = tmp_path / "pop.wav"
    pop.write_bytes(b"RIFF")

    # 1. Дефолтный поп (без явного pop_db)
    plan_def = xml2ae.scene_plan(xml_subs, highlights=[10, 20], style={"pop": str(pop)})
    sfx_def = {s["kind"]: s for s in plan_def["audio"]["sfx"]}
    assert "pop" in sfx_def
    assert sfx_def["pop"]["base"] == -8.0
    assert sfx_def["pop"]["db"] == 0.0

    # 2. С pop_db = -6.0
    plan_custom = xml2ae.scene_plan(xml_subs, highlights=[10, 20], style={"pop": str(pop), "pop_db": -6.0})
    sfx_custom = {s["kind"]: s for s in plan_custom["audio"]["sfx"]}
    assert "pop" in sfx_custom
    assert sfx_custom["pop"]["base"] == -8.0
    assert sfx_custom["pop"]["db"] == -6.0

    # 3. .jsx сборка: уровень звука равен базе (-8) + db (-6) = -14 dB
    out = str(tmp_path / "pop_db.jsx")
    xml2ae.to_ae_full(xml_subs, out, style={"pop": str(pop), "pop_db": -6.0}, highlights=[10, 20])
    txt = open(out, encoding="utf-8-sig").read()
    assert "setValue([-14,-14])" in txt

    # 4. Через роут /api/scene
    r = client.post("/api/scene", json={
        "xml": xml_subs,
        "highlights": [10, 20],
        "style": {"pop": str(pop), "pop_db": -6.0},
    }, headers={"Host": "127.0.0.1:5001"})
    assert r.status_code == 200
    body = r.get_json()
    assert body.get("ok") is True
    api_sfx = {s["kind"]: s for s in body["plan"]["audio"]["sfx"]}
    assert "pop" in api_sfx
    assert api_sfx["pop"]["base"] == -8.0
    assert api_sfx["pop"]["db"] == -6.0


