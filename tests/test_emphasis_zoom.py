# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание WX, пункт 3: наезд хайлайта — только на самые сильные жёлтые слова.

Правило: на кусок камеры — наезд на фразы с наибольшей силой, не больше `hl_zoom_max_per_piece`
(дефолт 2); второй — только если кусок не короче ручки «второй наезд — кусок от, с» и
между наездами помещается отъезд по действующему правилу; фраза ниже ручки «Порог силы, %»
(по умолчанию 70 — p70 жёлтых ролика по замеру 02.10.2026, core/styles.py; 0 — порог не
режет никого) не наезжает вовсе. Жёлтые, оставшиеся без наезда, — только цвет текста.

Здесь проверяется само правило (`layout._take_zoom_segment_keys`) и его связка с
планом сцены: сайдкар `core/emphasis.py` читается планом, а сил нет — поведение
прежнее и строка в лог. Плюс быстрый роут `/api/scene` модель эмфазы не грузит.
"""
import gzip
import json
import os
import shutil
import random
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import emphasis, xml2ae  # noqa: E402
from core.xml2ae import layout  # noqa: E402

FPS = 60.0


def _take(words, strengths=None, clip_scores=None, strong=True, **kw):
    """`take` камеры: фразы (начало, конец) в кадрах + силы фраз и ручки правила.

    `strengths` — сила КАЖДОЙ фразы (её считает камера как максимум сил её слов),
    `clip_scores` — силы всех жёлтых слов клипа: по ним считается порог силы. В тестах
    фразы однословные, поэтому обычно один список обслуживает и то и другое.
    Ручки правила (`min_pct`, `max_per_piece`, `second_min_s`) кладутся в `take` теми же
    ключами, что читает `layout._take_zoom_segment_keys` (`yellow_*`).
    """
    take = {"min_s": 8.0, "lo": 25.0, "hi": 40.0, "hold_s": 2.0, "out_s": 2.4,
            "long_on": False, "yellow_on": True, "words": words}
    if strengths is not None:
        take["yellow_strengths"] = [float(s) for s in strengths]
        take["yellow_clip_scores"] = list(clip_scores if clip_scores is not None else strengths)
        take["yellow_strong"] = strong
    take.update({"yellow_" + k: v for k, v in kw.items()})
    return take


def _keys(seg_end, words, f=0, lead=0, strengths=None, clip_scores=None, strong=True, **kw):
    return layout._take_zoom_segment_keys(
        f=f, seg_end=seg_end, v=100.0, fps=FPS,
        take=_take(words, strengths, clip_scores, strong, **kw),
        trng=random.Random(42), lead_min_frame=lead)


def _cycles(keys):
    """Кадры начала каждого цикла наезда (ключи с mode=1 — подъезд)."""
    return [k[0] for k in keys if k[2] == 1]


# --------------------------------------------------------------------------- #
# 1. Пять жёлтых в куске -> ОДИН наезд на самое сильное слово
# --------------------------------------------------------------------------- #

def test_пять_жёлтых_дают_один_наезд_на_самое_сильное():
    """Кусок 7.67 с (короче порога второго наезда), пять РАЗНЫХ фраз, лучшая — третья.

    Наезд РОВНО один и именно на фразу с максимальным score; остальные жёлтые
    остаются цветом текста (ключей по ним нет).
    """
    # Фразы по 20 кадров с паузой 40 кадров: пауза > TAKE_PHRASE_GAP_S (0.6 с = 36 кадров),
    # поэтому это пять РАЗНЫХ фраз, а не одна длинная.
    words = [(100.0, 120.0), (160.0, 180.0), (220.0, 240.0), (280.0, 300.0), (340.0, 360.0)]
    strengths = [0.20, 0.30, 0.95, 0.40, 0.50]      # по фразе, в порядке куска
    keys = _keys(460, words, strengths=strengths)

    assert len(_cycles(keys)) == 1, "наезд обязан быть ровно один: %r" % (keys,)
    # Лучшая фраза — третья (старт 220 кадров): подъезд за 0.1 с до её начала
    assert keys[0][0] == 220.0 - round(0.1 * FPS), "наезд не на самое сильное слово: %r" % (keys,)


def test_без_сил_поведение_прежнее_наезд_на_каждую_фразу():
    """Сил нет (сайдкар не посчитан) — правило не работает, наезд на каждую фразу."""
    words = [(100.0, 120.0), (400.0, 420.0), (700.0, 720.0), (1000.0, 1020.0), (1300.0, 1320.0)]
    old = _keys(1800, words, strengths=None)
    assert len(_cycles(old)) > 1, "без сил наезд обязан остаться на фразах: %r" % (old,)

    # Те же слова, но с силами: циклов становится меньше — правило включилось
    new = _keys(1800, words, strengths=[0.20, 0.25, 0.30, 0.90, 0.35])
    assert len(_cycles(new)) == 2, new
    assert _cycles(new)[0] == 1000.0 - round(0.1 * FPS), new


def test_сильные_выбираются_даже_если_они_в_конце_куска():
    """Лучшее слово — последнее в куске: наезд ставится на него, не на первое.

    Порог силы — ручка «Порог силы, %» (`hl_zoom_min_pct`), и по умолчанию он 70 —
    p70 жёлтых клипа (замер 02.10.2026, core/styles.py). Для набора `clip` (шесть
    значений) p70 = 0.65: слабая фраза куска (0.20) до него не дотягивается, порог
    проходит только сильная (0.90, в конце куска).
    """
    words = [(100.0, 120.0), (500.0, 520.0), (900.0, 920.0)]
    # `clip_scores` — силы ВСЕХ жёлтых ролика: слова из других кусков поднимают порог
    # выше 0.2, иначе порог прошла бы и вторая фраза этого куска.
    clip = [0.10, 0.20, 0.90, 0.55, 0.60]
    keys = _keys(4000, words, strengths=[0.10, 0.20, 0.90], clip_scores=clip)
    assert _cycles(keys) == [900.0 - round(0.1 * FPS)], keys

    # Порог 0 — не режет никого: предел «не больше двух на кусок» оставляет две самые
    # сильные фразы (0.90 и 0.20), а наезд идёт по времени, а не по силе
    keys = _keys(4000, words, strengths=[0.10, 0.20, 0.90], clip_scores=clip, min_pct=0.0)
    assert _cycles(keys) == [500.0 - round(0.1 * FPS), 900.0 - round(0.1 * FPS)], keys

    # Предел 1: в кусок идёт РОВНО один наезд, и он на самое сильное слово (в конце куска)
    keys = _keys(4000, words, strengths=[0.10, 0.20, 0.90], clip_scores=clip,
                 max_per_piece=1)
    assert _cycles(keys) == [900.0 - round(0.1 * FPS)], keys


def test_порог_100_оставляет_только_самое_сильное_слово():
    """Верх ручки — максимум шкалы клипа, а не «никто»: порог режет, а не выключает.

    На 100 проходит фраза ровно с максимальной силой; предел на кусок тут ни при чём —
    второй наезд просто не проходит порог. На 0 та же шкала даёт оба наезда по пределу.
    """
    words = [(600.0, 620.0), (1800.0, 1820.0), (3000.0, 3020.0)]
    clip = [0.90, 0.80, 0.10]
    keys = _keys(3600, words, strengths=clip, clip_scores=clip, min_pct=100.0)
    assert _cycles(keys) == [600.0 - round(0.1 * FPS)], keys
    assert len(_cycles(_keys(3600, words, strengths=clip, clip_scores=clip,
                             min_pct=0.0))) == 2


# --------------------------------------------------------------------------- #
# 2. Длинный кусок -> два наезда с отъездом между ними
# --------------------------------------------------------------------------- #

def test_длинный_кусок_два_наезда_с_отъездом():
    """Кусок 60 с, две сильные фразы далеко друг от друга: два полных цикла.

    Пары фраз держатся врозь: между их отъездами обязан вместиться отъезд. Порог силы
    привязан явно (50, медиана набора), иначе дефолтный p70 оставил бы сильной только
    первую — и проверять «два наезда» стало бы не на чем.

    Между циклами обязан поместиться отъезд: хвост первого (четвёртый ключ) идёт
    РАНЬШЕ подъезда второго (пятый ключ).
    """
    words = [(600.0, 620.0), (1800.0, 1820.0), (3000.0, 3020.0)]
    keys = _keys(3600, words, strengths=[0.90, 0.80, 0.10], min_pct=50.0)
    assert len(_cycles(keys)) == 2, "в длинном куске ожидались два наезда: %r" % (keys,)
    assert len(keys) == 8, "каждый наезд — полный цикл (4 ключа): %r" % (keys,)
    assert keys[3][0] < keys[4][0], "отъезд первого цикла не успевает до второго"


def test_короткий_кусок_второго_наезда_не_даёт():
    """Тот же набор сил в куске 7 с (короче 8 с): наезд только один."""
    words = [(100.0, 120.0), (250.0, 270.0)]
    keys = _keys(int(7 * FPS), words, strengths=[0.90, 0.85])
    assert len(_cycles(keys)) == 1, "в коротком куске второго наезда быть не должно: %r" % (keys,)


def test_второй_наезд_только_если_влезает_отъезд():
    """Две сильные фразы почти встык: отъезд первого не влезает — наезд один."""
    words = [(100.0, 120.0), (180.0, 200.0)]
    keys = _keys(2000, words, strengths=[0.90, 0.85])
    assert len(_cycles(keys)) == 1, keys
    assert keys[-1][3] == 1, "пик обязан держаться до склейки (отъезда нет)"


# --------------------------------------------------------------------------- #
# 3. Слабый кусок -> наезда нет вовсе
# --------------------------------------------------------------------------- #

def test_слабый_кусок_не_наезжает():
    """Лучшее слово куска ниже ПОРОГА силы жёлтых КЛИПА — наезда нет, жёлтые только цвет.

    Порог по умолчанию — 70, p70 жёлтых клипа (замер 02.10.2026, core/styles.py). Он
    считается по всем жёлтым ролика (в куске их два, остальные — в других кусках),
    поэтому «слабый» здесь — свойство куска, а не набора внутри него.
    """
    words = [(100.0, 120.0), (160.0, 180.0)]
    # Жёлтых клипа шесть: p70 = 0.61, у слов этого куска 0.10 и 0.12
    clip = [0.10, 0.12, 0.45, 0.50, 0.55, 0.95]
    keys = _keys(1000, words, strengths=clip[:len(words)], clip_scores=clip)
    assert keys == [], "слабый кусок не должен наезжать: %r" % (keys,)

    # Ровно на пороге — уже наезжает: порог «ниже порога», не «строго ниже»
    clip2 = [0.61, 0.12, 0.45, 0.50, 0.55, 0.95]
    assert _cycles(_keys(1000, words, strengths=clip2[:len(words)], clip_scores=clip2)), \
        "слово на пороге обязано наезжать"

    # Порог 0 (ручка) — не режет никого: наезжает и заведомо слабый кусок
    assert _cycles(_keys(1000, words, strengths=clip[:len(words)], clip_scores=clip,
                         min_pct=0.0)), "с нулевым порогом кусок с жёлтыми обязан наезжать"


# --------------------------------------------------------------------------- #
# 4. Связка с планом сцены: сайдкар читается, сил нет — прежнее поведение и лог
# --------------------------------------------------------------------------- #

@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


# Жёлтые ЧЕРЕЗ слово: индексный разрыв > 1 делает их РАЗНЫМИ фразами. Иначе все жёлтые
# склеились бы в одну длинную фразу, и правило «не больше двух наездов на кусок» нечего
# было бы ограничивать.
HL = [6, 8, 10, 12, 14, 16, 18, 20, 22, 24]


def _style():
    return {"cam1_zoom": "jump", "cam1_take_zoom": False, "cam1_yellow_zoom": True}


def _sidecar(xml, highlights, scores, mode="emotion"):
    """Сайдкар силы с честным ключом: слова, набор жёлтых, версия формулы.

    `scores` — сила по слову: число (положится и в `emo`, и в `stress`: шкала одна) или
    готовые компоненты `{"emo": ..., "stress": ...}`. Формат сайдкара один на любой
    способ оценки — план лишь ВЫБИРАЕТ компоненту по ключу стиля.
    """
    meta, cams, subs, _xi = xml2ae.parse_full(xml)
    words = emphasis.word_refs(subs, float(meta["fps"]))
    key = emphasis.cache_key(words, (), (cams[0].get("path") or ""), highlights)
    out = {}
    for k, v in scores.items():
        out[str(k)] = v if isinstance(v, dict) else {"emo": float(v), "stress": float(v)}
    with open(emphasis.emph_path(xml), "w", encoding="utf-8") as f:
        json.dump({"key": key, "scores": out}, f)
    return key


def _plan(xml, **kw):
    return xml2ae.scene_plan(xml, style=kw.pop("style", _style()), highlights=kw.pop("highlights", HL),
                             intro=[], intro_remove=[], **kw)


def test_план_читает_сайдкар_и_ставит_меньше_наездов(xml_subs):
    """С сайдкаром наездов в плане МЕНЬШЕ, чем без него (силы доехали до правила)."""
    scores = {k: round(0.10 + 0.04 * i, 4) for i, k in enumerate(HL)}
    scores[HL[-1]] = 0.99                    # одно явно сильное слово

    no = _plan(xml_subs, emit=lambda *a, **k: None)
    _sidecar(xml_subs, HL, scores)
    yes = _plan(xml_subs, emit=lambda *a, **k: None)
    assert yes["zoom"]["keys"], "наезд пропал вовсе — правило отсёкло все фразы"
    assert len(yes["zoom"]["keys"]) < len(no["zoom"]["keys"]), (
        "правило «только сильные» не уменьшило наездов: было %d, стало %d"
        % (len(no["zoom"]["keys"]), len(yes["zoom"]["keys"])))


def test_нет_сайдкара_поведение_прежнее_и_лог(xml_subs):
    """Сайдкара нет — план считает как раньше и честно пишет, что силы не посчитаны."""
    lines = []
    plan = _plan(xml_subs, emit=lambda line="", **vars: lines.append(line))
    assert plan["zoom"]["keys"], "без сайдкара наезды должны остаться"
    assert any("сила жёлтых не посчитана" in str(s) for s in lines), lines
    assert not os.path.isfile(emphasis.emph_path(xml_subs)), \
        "план сцены не имеет права считать силу (это GPU-этап)"


def test_галка_выключена_силы_не_действуют(xml_subs):
    """Галка «только сильные жёлтые» снята — наезды как раньше, даже с сайдкаром."""
    # Силы как в соседнем тесте: часть фраз выше порога силы, часть ниже — правилу есть
    # что ограничивать, и разница «галка снята / стоит» видна.
    scores = {k: round(0.10 + 0.04 * i, 4) for i, k in enumerate(HL)}
    scores[HL[-1]] = 0.99
    _sidecar(xml_subs, HL, scores)
    off = _plan(xml_subs, style=dict(_style(), cam1_yellow_zoom_strong=False),
                emit=lambda *a, **k: None)
    on = _plan(xml_subs, emit=lambda *a, **k: None)
    assert len(off["zoom"]["keys"]) > len(on["zoom"]["keys"]), \
        "снятая галка обязана вернуть прежнее поведение"


# --------------------------------------------------------------------------- #
# 5. План сцены (быстрый роут) модель НЕ грузит
# --------------------------------------------------------------------------- #

@pytest.fixture()
def client():
    from flask import Flask

    os.environ.setdefault("REELSI_NO_BROWSER", "1")
    import api

    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def test_api_scene_не_грузит_модель_эмфазы(xml_subs, client, monkeypatch):
    """`/api/scene` — быстрый роут: модель эмфазы не зовётся, сайдкар не пересчитывается.

    Заглушка `load_emo_model` бросает: если бы план решил посчитать силу сам, роут
    вернул бы ошибку (или перезаписал сайдкар). Он не делает ни того, ни другого —
    сила приезжает в план ТОЛЬКО из готового сайдкара.
    """
    def boom(*a, **k):
        raise AssertionError("план сцены не имеет права грузить модель эмфазы")

    monkeypatch.setattr(emphasis, "load_emo_model", boom)
    hl = [6, 8, 10]
    _sidecar(xml_subs, hl, {6: 0.9, 8: 0.2, 10: 0.1})
    sidecar = emphasis.emph_path(xml_subs)
    before = os.path.getmtime(sidecar)

    res = client.post("/api/scene", json={"xml": xml_subs, "style": _style(), "highlights": hl})
    d = res.get_json()
    assert res.status_code == 200 and d.get("ok"), d
    assert d["plan"]["zoom"]["keys"], "наезд пропал — план не прочитал сайдкар"
    assert os.path.getmtime(sidecar) == before, "план пересчитал сайдкар (модель звали)"


# --------------------------------------------------------------------------- #
# 6. Слова интро: предрасчёт и план считают ОДИН набор слов
# --------------------------------------------------------------------------- #

def test_предрасчёт_силы_и_план_видят_одни_слова_интро(xml_subs):
    """Выделенная цветом строка интро: силы слов интро лежат в том же сайдкаре.

    Проверяется стык, который легко разъехался бы: слова интро лежат ВНЕ `subs` (их
    вынул `plan_words`), и в план они приходят своим индексным рядом. Набор таких слов
    обязан считаться ОДНОЙ дверью (`plan_intro.intro_hl_words`) и у предрасчёта, и у
    плана — иначе сайдкар не совпадёт по ключу и правило силы молча выключится.

    Исходника фикстуры на диске нет: звук не читается, силы нейтральны — этого довольно,
    чтобы проверить КЛЮЧ и покрытие индексов.
    """
    from core.xml2ae import precompute

    meta, _c, subs, _xi = xml2ae.parse_full(xml_subs)
    fps = float(meta["fps"])
    a, b = 6, 8                                  # два соседних слова ролика
    row = {"words": [subs[a][2], subs[b][2]],
           "times": [round(subs[a][0] / fps, 2), round(subs[b][0] / fps, 2)],
           "color": "yellow"}

    view = precompute.emphasis_precompute(xml_subs, _style(), idx=[6, 8, 10], emit=lambda *a2, **k: None,
                                          intro=[row], intro_splits=[], intro_remove=[a, b])
    assert view.valid, "сайдкар интро-слов не читается сразу после расчёта"
    assert not view.uncomputed, view.uncomputed
    # Индексы слов интро продолжают ряд слов ролика, оставшихся ПОСЛЕ вырезания интро
    # (plan_words вынул из титров два слова — 6 и 8): это нумер плана, и он же у плана
    # при чтении, поэтому сайдкар и совпадает по ключу.
    assert {len(subs) - 2, len(subs) - 1} <= set(view.scores), sorted(view.scores)
    assert 8 in view.scores, sorted(view.scores)      # жёлтое 10 после переиндексации — 8

    # План с той же строкой интро обязан прочитать этот сайдкар: то же число наездов,
    # что и с готовым сайдкаром, и без строки «сила не посчитана»
    lines = []
    plan = xml2ae.scene_plan(xml_subs, style=_style(), highlights=[6, 8, 10], intro=[row],
                             intro_remove=[a, b], emit=lambda line="", **vars: lines.append(line))
    assert plan["zoom"]["keys"], "наезд пропал — план не нашёл силы интро-слов"
    assert not any("сила жёлтых не посчитана" in str(s) for s in lines), lines

