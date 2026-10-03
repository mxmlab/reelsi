# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Способ оценки силы жёлтых, ручки правила наезда и окна чтения звука.

1. `hl_zoom_strength` — выпадашка «по голосу» (`voice`, только ударение) / «по эмоциям»
   (`emotion`, только GigaAM-Emo). Сайдкар `<стем>.emph.json` хранит ОБЕ составляющие по
   слову, поэтому переключение способа НЕ пересчитывает ни звук, ни модель — план лишь
   выбирает компоненту. Смена ключа не трогает сайдкар (проверяется по mtime).
2. Ручки правила (общие для обеих камер): «Порог силы, %» (`hl_zoom_min_pct`),
   «Наездов на кусок, макс.» (`hl_zoom_max_per_piece`), «Второй наезд — кусок от, с»
   (`hl_zoom_second_min_s`). Каждая обязана менять план. Ручки видны всегда (`show_if`
   с них снят, WX4): действуют они на камеры, где стоит «Только сильные жёлтые».
3. Режим «по голосу» не грузит модель эмоций ВООБЩЕ: нужна только акустика.
4. `core/emphasis.py` читает звук ОКНАМИ вокруг жёлтых слов, а не исходником целиком:
   число прочитанных окон зависит от числа жёлтых, а не от длины исходника.
5. Новые ручки переведены и описаны подсказками (`docs/DESIGN.md` — справка в `data-t`).

Запуск: python -m pytest tests/test_emphasis_mode.py -q
"""
import gzip
import json
import os
import random
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import emphasis, style_schema, xml2ae  # noqa: E402
from core.xml2ae import precompute  # noqa: E402

FPS = 60.0
# Жёлтые ЧЕРЕЗ слово: индексный разрыв > 1 делает их РАЗНЫМИ фразами (как в
# test_emphasis_zoom.py) — иначе все склеились бы в одну длинную и ручкам было бы
# нечего ограничивать.
HL = [6, 8, 10, 12, 14, 16, 18, 20, 22, 24]


@pytest.fixture()
def xml_subs(tmp_path):
    """Фикстура таймлайна: у неё уже есть жёлтые слова и камеры с кусками."""
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _style(**over):
    st = {"cam1_zoom": "jump", "cam1_take_zoom": False, "cam1_yellow_zoom": True}
    st.update(over)
    return st


def _sidecar(xml, highlights, stress, emo):
    """Сайдкар силы с честным ключом: обе компоненты по каждому слову.

    Порядок `stress` и `emo` ЗАДАЁТСЯ вызывающим — на нём и проверяется выбор способа:
    у одной и той же пары слов «по голосу» и «по эмоциям» сильными оказываются разные.
    """
    meta, cams, subs, _xi = xml2ae.parse_full(xml)
    words = emphasis.word_refs(subs, float(meta["fps"]))
    key = emphasis.cache_key(words, (), (cams[0].get("path") or ""), highlights)
    scores = {str(k): {"emo": float(emo[k]), "stress": float(stress[k])} for k in highlights}
    with open(emphasis.emph_path(xml), "w", encoding="utf-8") as f:
        json.dump({"key": key, "scores": scores}, f)


def _plan(xml, **kw):
    """План сцены на фикстуре: стиль, жёлтые и пустое интро (его в этих тестах нет)."""
    kw.setdefault("style", _style())
    kw.setdefault("highlights", HL)
    kw.setdefault("intro", [])
    kw.setdefault("intro_remove", [])
    return xml2ae.scene_plan(xml, emit=lambda *a, **k: None, **kw)


def _cycles(keys):
    """Кадры начала каждого цикла наезда (ключи с mode=1 — подъезд)."""
    return [k[0] for k in keys if k[2] == 1]


def _hl_cycles(keys):
    """Циклы ТОЛЬКО по жёлтым: подъезд на базовом 100% (у длинного куска свой масштаб)."""
    return [k[0] for k in keys if k[2] == 1 and k[1] == 100.0]


# --------------------------------------------------------------------------- #
# 1. Способ оценки выбирает разные слова на ОДНИХ И ТЕХ ЖЕ силах
# --------------------------------------------------------------------------- #

def test_voice_и_emotion_выбирают_разные_слова(xml_subs):
    """Порядок компонент обратный — способы выбирают разные слова, звук не пересчитан.

    Наборы НЕ зеркальны, а независимы: у каждого свой максимум, и оба стоят в первом
    куске клипа (иначе дефолтный порог p70 отсекает «сильное» слово куска вовсе, и
    сравнивать способы не на чем). У `stress` максимум позже, чем у `emo`, поэтому
    «по голосу» наезжает позже. Если план игнорирует ключ стиля (мутация), оба способа
    выберут одно и то же слово, и тест краснеет.
    """
    stress = {6: 0.1, 8: 0.2, 10: 0.3, 12: 0.4, 14: 0.92, 16: 0.6, 18: 0.5, 20: 0.3, 22: 0.2, 24: 0.1}
    emo = {6: 0.92, 8: 0.7, 10: 0.6, 12: 0.4, 14: 0.3, 16: 0.2, 18: 0.1, 20: 0.2, 22: 0.1, 24: 0.1}
    _sidecar(xml_subs, HL, stress, emo)
    before = os.path.getmtime(emphasis.emph_path(xml_subs))

    # Наезд в первом кадре ролика снят: он есть у обоих способов и мешал бы сравнению
    em = _plan(xml_subs, style=_style(hl_zoom_strength="emotion", cam1_zoom_start=False))
    vo = _plan(xml_subs, style=_style(hl_zoom_strength="voice", cam1_zoom_start=False))

    em_cy, vo_cy = _hl_cycles(em["zoom"]["keys"]), _hl_cycles(vo["zoom"]["keys"])
    assert em_cy and vo_cy, "без наездов правило не проверить: %r / %r" % (em_cy, vo_cy)
    assert em_cy != vo_cy, (
        "способы оценки дали один и тот же план — план игнорирует hl_zoom_strength")
    # Самое сильное слово по эмоциям — первое жёлтое, по голосу — среднее: наезды
    # приходят на РАЗНЫЕ кадры, и «голос» наезжает позже.
    assert min(vo_cy) > min(em_cy), (
        "«по голосу» обязан выбрать поздние слова: %r vs %r" % (vo_cy[:3], em_cy[:3]))
    assert os.path.getmtime(emphasis.emph_path(xml_subs)) == before, \
        "смена способа пересчитала сайдкар — переключение обязано быть бесплатным"


def test_смена_ключа_не_пересчитывает_сайдкар(xml_subs):
    """Второй план с другим способом читает тот же сайдкар (mtime не менялся)."""
    _sidecar(xml_subs, HL, {k: 0.5 for k in HL}, {k: 0.5 for k in HL})
    path = emphasis.emph_path(xml_subs)
    before = os.path.getmtime(path)
    for mode in ("emotion", "voice", "emotion", "voice"):
        _plan(xml_subs, style=_style(hl_zoom_strength=mode))
    assert os.path.getmtime(path) == before, "план сцены не имеет права считать сайдкар"


def test_voice_читает_компоненту_ударения_а_emotion_эмоции(xml_subs):
    """Обе компоненты лежат рядом, и каждая читается своим способом.

    Проверяется на уровне `read_emphasis`: у одного и того же сайдкара `scores` разные,
    а `components` — одни и те же числа.
    """
    stress = {k: round(0.10 + 0.08 * i, 3) for i, k in enumerate(HL)}
    emo = {k: round(0.90 - 0.08 * i, 3) for i, k in enumerate(HL)}
    _sidecar(xml_subs, HL, stress, emo)
    meta, cams, subs, _xi = xml2ae.parse_full(xml_subs)
    words = emphasis.word_refs(subs, float(meta["fps"]))
    src = cams[0].get("path") or ""
    em = emphasis.read_emphasis(xml_subs, words, (), HL, src, idx=HL, mode="emotion")
    vo = emphasis.read_emphasis(xml_subs, words, (), HL, src, idx=HL, mode="voice")
    assert em.scores == {k: pytest.approx(emo[k]) for k in HL}
    assert vo.scores == {k: pytest.approx(stress[k]) for k in HL}
    assert em.components == vo.components, "компоненты обязаны читаться одинаково"
    assert em.mode == "emotion" and vo.mode == "voice"


# --------------------------------------------------------------------------- #
# 2. Ручки правила меняют план
# --------------------------------------------------------------------------- #

def test_порог_силы_меняет_план(xml_subs):
    """«Порог силы, %»: выше порог — меньше наездов; 0 не режет никого.

    Порог — процентиль силы жёлтых клипа (0 — минимум, 50 — медиана, 70 — дефолт,
    100 — максимум). Проверяется, что ручка доезжает до правила и режет наезды монотонно;
    точные границы (p70, максимум) стережёт правило — `test_emphasis_zoom.py`.
    """
    stress = {k: round(0.10 + 0.08 * i, 3) for i, k in enumerate(HL)}
    emo = {k: round(0.10 + 0.08 * (len(HL) - 1 - i), 3) for i, k in enumerate(HL)}
    _sidecar(xml_subs, HL, stress, emo)
    counts = []
    for pct in (0.0, 50.0, 100.0):
        plan = _plan(xml_subs, style=_style(hl_zoom_strength="voice", hl_zoom_min_pct=pct,
                                            cam1_zoom_start=False))
        counts.append(len(_hl_cycles(plan["zoom"]["keys"])))
    assert counts[0] > 0, "с порогом 0 наезды обязаны остаться"
    assert counts == sorted(counts, reverse=True), (
        "порог обязан резать наезды тем сильнее, чем он выше: %r" % (counts,))
    assert counts[-1] < counts[0], (
        "порог 100%% обязан отсечь слабые слова: %r" % (counts,))


def test_ровные_силы_наезжают_при_любом_пороге(xml_subs):
    """Все жёлтые одинаковой силы: порог не режет никого — сравнивать не с чем.

    Процентиль ровного набора равен самой силе (минимум, медиана и максимум — одно
    число), и фраза проходит порог при любом значении ручки. Счёт «доля слов ниже
    порога» оставил бы на 50 ноль наездов вовсе: жёлтые равны, значит ниже порога нет
    никого, — и правило молча выключилось бы на ровном сайдкаре (нет звука, все
    эмоции нейтральны).
    """
    _sidecar(xml_subs, HL, {k: 0.5 for k in HL}, {k: 0.5 for k in HL})
    for pct in (0.0, 50.0, 100.0):
        plan = _plan(xml_subs, style=_style(hl_zoom_strength="voice", hl_zoom_min_pct=pct,
                                            cam1_zoom_start=False))
        assert _hl_cycles(plan["zoom"]["keys"]), (
            "на ровных силах наезд обязан остаться (порог %r)" % (pct,))


def test_предел_наездов_на_кусок_меняет_план(xml_subs):
    """`hl_zoom_max_per_piece`: 3 против 1 — в куске разное число жёлтых циклов.

    Проверяется на уровне правила (`layout._take_zoom_segment_keys`): три сильные фразы
    далеко друг от друга в длинном куске — предел 3 даёт три полных цикла, предел 1
    оставляет ровно один, на самое сильное слово. Там же и порог длины куска: в куске
    10 с второй наезд помещается, в куске длиннее 20 с — нет.
    """
    from core.xml2ae import layout

    words = [(100.0, 120.0), (900.0, 920.0), (1700.0, 1720.0)]
    strengths = [0.70, 0.80, 0.90]

    def keys(cap, seg=5000, sec=3.0):
        take = {"min_s": 8.0, "lo": 25.0, "hi": 40.0, "hold_s": 2.0, "out_s": 2.4,
                "long_on": False, "yellow_on": True, "words": words,
                "yellow_strengths": strengths, "yellow_clip_scores": strengths,
                "yellow_strong": True, "yellow_min_pct": 0.0,
                "yellow_max_per_piece": cap, "yellow_second_min_s": sec}
        return layout._take_zoom_segment_keys(
            f=0, seg_end=seg, v=100.0, fps=FPS, take=take,
            trng=random.Random(42), lead_min_frame=0)

    three, one = keys(3), keys(1)
    assert len(_cycles(three)) == 3, "предел 3 обязан дать три наезда: %r" % (three,)
    assert len(_cycles(one)) == 1, "предел 1 обязан дать ровно один наезд: %r" % (one,)
    # Выбран самый сильный, а не первый по времени
    assert _cycles(one) == [1700.0 - round(0.1 * FPS)], _cycles(one)

    # И тот же предел в коротком куске (10 с): второй наезд требует порога длины
    words2 = [(100.0, 120.0), (400.0, 420.0)]
    strengths2 = [0.90, 0.80]

    def keys2(sec):
        take = {"min_s": 8.0, "lo": 25.0, "hi": 40.0, "hold_s": 2.0, "out_s": 2.4,
                "long_on": False, "yellow_on": True, "words": words2,
                "yellow_strengths": strengths2, "yellow_clip_scores": strengths2,
                "yellow_strong": True, "yellow_min_pct": 0.0,
                "yellow_max_per_piece": 2, "yellow_second_min_s": sec}
        return layout._take_zoom_segment_keys(
            f=0, seg_end=600, v=100.0, fps=FPS, take=take,
            trng=random.Random(42), lead_min_frame=0)

    two_short, one_short = keys2(3.0), keys2(20.0)
    assert len(_cycles(two_short)) == 2 and len(_cycles(one_short)) == 1, (
        "порог длины куска не отменил второй наезд: %r / %r" % (two_short, one_short))


def test_второй_наезд_порог_длины_куска_меняет_план(xml_subs, monkeypatch):
    """`hl_zoom_second_min_s` доходит до правила из стиля: значение читается в `take`.

    Само ограничение проверяется и на уровне правила (соседний тест — короткий кусок);
    здесь стык «ключ стиля -> план»: разные значения порога дают разные ключи, потому
    что второй наезд либо разрешён, либо нет.

    Фикстура — монтаж из коротких кусков, и в её самом длинном куске (7.4 с) отъезд
    после третьей фразы уже не помещается: второй наезд был бы не из-за порога, а из-за
    раскладки. Поэтому кусок для проверки задаётся один и длинный (склейки камеры 1
    подменяются на один отрезок 0–1000, это 16.7 с): тогда решение принимает ровно ручка.

    Силы подобраны так, что порог по умолчанию 70 (p70 жёлтых клипа) оставляет
    сильными ДВЕ далеко стоящие фразы (первую и восьмую по времени): невысокий порог
    длины разрешает второй наезд, высокий — запрещает.
    """
    from core.xml2ae import layout

    def _one_piece(cams, cam, fps=60.0, min_gap_sec=2.0):
        return ([0.0], [1000.0]) if cam == 0 else ([], [])

    monkeypatch.setattr(layout, "_camera_cut_segments", _one_piece)
    vals = {6: 0.9, 8: 0.4, 10: 0.45, 12: 0.5, 14: 0.55, 16: 0.6, 18: 0.35, 20: 0.8, 22: 0.7, 24: 0.25}
    _sidecar(xml_subs, HL, vals, vals)
    common = dict(hl_zoom_strength="voice", cam1_zoom_start=False)
    short = _plan(xml_subs, style=_style(hl_zoom_second_min_s=3.0, **common))
    long_ = _plan(xml_subs, style=_style(hl_zoom_second_min_s=20.0, **common))
    assert short["zoom"]["keys"] != long_["zoom"]["keys"], (
        "порог длины куска не доехал до правила: %r vs %r"
        % (short["zoom"]["keys"][:6], long_["zoom"]["keys"][:6]))
    assert len(_hl_cycles(short["zoom"]["keys"])) == 2, (
        "невысокий порог обязан разрешить второй наезд: %r" % (_hl_cycles(short["zoom"]["keys"]),))
    assert _hl_cycles(long_["zoom"]["keys"]), "первый наезд обязан остаться при любом пороге"


# --------------------------------------------------------------------------- #
# 3. Режим «по голосу» модель эмоций не грузит
# --------------------------------------------------------------------------- #

def test_voice_без_сайдкара_не_зовёт_модель(xml_subs, monkeypatch):
    """Сайдкара нет, способ — «по голосу»: модель эмоций не грузится вовсе.

    Исходника фикстуры на диске нет, звук не читается — акустика даст нейтральные
    признаки, и это нормально: проверяется, что `gigaam.load_model` НЕ звали.
    """
    def boom(*a, **k):
        raise AssertionError("в режиме «по голосу» модель эмоций грузить нельзя")

    monkeypatch.setattr(emphasis, "load_emo_model", boom)
    view = precompute.emphasis_precompute(
        xml_subs, _style(hl_zoom_strength="voice"), idx=HL, emit=lambda *a, **k: None)
    assert view.mode == "voice", "предрасчёт обязан читать способ из ключа стиля"


def test_voice_считает_только_ударение(xml_subs, monkeypatch, tmp_path):
    """`compute_emphasis(mode="voice")` пишет в сайдкар `stress`, а `emo` не считает."""
    calls = {"emo": 0}

    def no_emo(*a, **k):
        calls["emo"] += 1
        raise AssertionError("модель эмоций в режиме «по голосу» не нужна")

    monkeypatch.setattr(emphasis, "load_emo_model", no_emo)
    meta, cams, subs, xi = xml2ae.parse_full(xml_subs)
    xml = str(tmp_path / "clip.xml")
    open(xml, "w", encoding="utf-8").close()
    scores = emphasis.compute_emphasis(emphasis.EmphasisInputs(
        words=emphasis.word_refs(subs, float(meta["fps"])), parsed=(meta, cams, subs, xi),
        xml_path=xml, idx=HL, mode="voice", emit=lambda *a, **k: None))
    assert calls["emo"] == 0
    assert set(scores) == set(HL)
    data = json.loads(open(emphasis.emph_path(xml), encoding="utf-8").read())
    # Сайдкар версии 2: обе компоненты по слову (вторая посчитана и в «голосе» —
    # переключение способа потом ничего не пересчитывает).
    assert all(isinstance(v, dict) for v in data["scores"].values())
    assert data["key"]["v"] == emphasis.EMPH_VERSION
    assert emphasis.EMPH_VERSION >= 2


def test_voice_не_оставляет_нулей_на_месте_эмоций(xml_subs, monkeypatch, tmp_path):
    """Сайдкар «по голосу» идёт БЕЗ `emo`, и переключение на эмоции честно это видит.

    Ноль на месте несчитанной компоненты — это «фраза нейтральна»: все слова сравнялись
    бы, и наезд пошёл бы на первые по времени вместо сильных. Отсутствие компоненты план
    читает как пропуск и возвращается к прежнему правилу (наезд на каждую фразу).
    """
    def boom(*a, **k):
        raise AssertionError("в режиме «по голосу» модель эмоций грузить нельзя")

    monkeypatch.setattr(emphasis, "load_emo_model", boom)
    meta, cams, subs, xi = xml2ae.parse_full(xml_subs)
    words = emphasis.word_refs(subs, float(meta["fps"]))
    xml = str(tmp_path / "clip.xml")
    open(xml, "w", encoding="utf-8").close()
    emphasis.compute_emphasis(emphasis.EmphasisInputs(
        words=words, parsed=(meta, cams, subs, xi), xml_path=xml, idx=HL, mode="voice",
        emit=lambda *a, **k: None))
    data = json.loads(open(emphasis.emph_path(xml), encoding="utf-8").read())
    assert all("emo" not in v for v in data["scores"].values()), data["scores"]
    src = cams[0].get("path") or ""
    vo = emphasis.read_emphasis(xml, words, (), HL, src, idx=HL, mode="voice")
    assert vo.valid and not vo.uncomputed, "ударение посчитано — «по голосу» читается"
    em = emphasis.read_emphasis(xml, words, (), HL, src, idx=HL, mode="emotion")
    assert em.valid and em.uncomputed == list(HL), "эмоций в сайдкаре нет — это пропуск"


# --------------------------------------------------------------------------- #
# 4. Звук читается ОКНАМИ вокруг жёлтых, а не исходником целиком
# --------------------------------------------------------------------------- #

def test_audio_windows_зависит_от_числа_жёлтых():
    """Окна считаются вокруг жёлтых и их соседей, а не по всему ролику.

    Слова идут по секунде, ролик короткий; окна склеиваются в один отрезок вокруг
    жёлтых, а не покрывают весь исходник. Пустой набор слов не даёт ни одного окна —
    ни одного чтения.
    """
    n = 20
    words = [emphasis.WordRef(idx=k, text="С%d" % k, start=float(k), end=k + 0.4)
             for k in range(n)]
    src = [(w.start, w.end) for w in words]
    hl = [8, 9, 10]                     # середина окна — 9.2 с
    windows = emphasis.audio_windows(
        [emphasis.stress_spans(src, hl)[i] for i in hl], "emotion", [src[i] for i in hl])
    assert windows, "окон не запрошено вовсе"
    # Окно ударения — слово плюс ±STRESS_NEIGHBORS слов, эмоция — 2.5 с с запасом
    lo = min(s for s, _e in windows)
    hi = max(e for _s, e in windows)
    assert -0.2 <= lo <= 3.1, "левый край окон не там: %r" % (lo,)
    assert 15.0 <= hi <= 16.6, "правый край окон не там: %r" % (hi,)
    assert len(windows) == 1, "окна не склеились: %r" % (windows,)
    # В окне ударения — само слово и его соседи: пять слов вокруг индекса 9
    assert emphasis.stress_spans(src, hl)[9] == pytest.approx((4.0, 14.4))
    # Пустой набор слов — ни одного окна (и ни одного чтения)
    assert emphasis.audio_windows([], "emotion", []) == []


def test_окна_не_покрывают_далёкие_жёлтые(tmp_path, monkeypatch):
    """Второе скопление жёлтых далеко в ролике: его окно читается отдельно.

    Ролик длиной 200 с, а читаются два узких отрезка вокруг скоплений — длина ролика в
    сумме окон не отражается.
    """
    n = 200
    words = [emphasis.WordRef(idx=k, text="С%d" % k, start=float(k), end=k + 0.4)
             for k in range(n)]
    src = [(w.start, w.end) for w in words]
    hl = [8, 9, 10]                            # середина окна — 9.2 с
    far = 150.0                                # второе скопление жёлтых
    sp = emphasis.stress_spans(src, [int(far), int(far) + 1])
    windows = emphasis.audio_windows(
        [sp[int(far)], sp[int(far) + 1]], "emotion", [src[int(far)], src[int(far) + 1]])
    near = emphasis.audio_windows(
        [emphasis.stress_spans(src, hl)[i] for i in hl], "emotion", [src[i] for i in hl])
    assert near and windows, "по обоим скоплениям обязаны быть окна"
    # Ни одно окно не тянется через весь ролик и не трогает середину между скоплениями
    assert max(e for _s, e in windows) < 170.0, windows
    assert min(s for s, _e in windows) > 140.0, windows
    assert sum(e - s for s, e in near) < 40.0, near
    assert sum(e - _s for _s, e in windows) < 40.0, windows
    assert emphasis.audio_windows([], "emotion", []) == []


def test_compute_emphasis_читает_окна_а_не_исходник(tmp_path, monkeypatch):
    """Звук читается отрезками ВОКРУГ жёлтых, и признаки считаются только им.

    Ролик «длиной» 195 с (40 слов каждые 5 с), жёлтые стоят кучно. Проверяется, что
    `prosody` получил ровно запрошенные слова (жёлтые и их соседи) — а не все 40, — и
    что окно много уже исходника. `_load_audio` (чтение ЦЕЛИКОМ) не зовётся вовсе.
    """
    n, asked = 200, []
    got = []

    def boom(*a, **k):
        raise AssertionError("звук читается целиком — расчёт снова зависит от длины исходника")

    monkeypatch.setattr(emphasis, "_load_audio", boom)
    monkeypatch.setattr(emphasis, "load_emo_model", lambda: object())
    monkeypatch.setattr(emphasis, "release_emo", lambda model: None)
    monkeypatch.setattr(emphasis, "emotion_probs", lambda window, model, sr: {"neutral": 0.5})

    def prosody(audio, sr, spans):
        got.extend(i for i, _s, _e in spans)
        return {i: (-30.0, 120.0, 0.2) for i, _s, _e in spans}

    monkeypatch.setattr(emphasis, "prosody", prosody)

    def read_window(src, w0, w1):
        import numpy as np
        asked.append((w0, w1))
        # Звук отрезка — ровно той длины, что просили: признаки считаются по нему
        return np.zeros(max(int(0.3 * 22050), int((w1 - w0) * 22050)), dtype="float32"), 22050

    words = [emphasis.WordRef(idx=k, text="С%d" % k, start=k * 2.0, end=k * 2.0 + 0.4)
             for k in range(n)]
    cams = [{"path": "C:/footage/cam1.mp4", "name": "cam1",
             "clips": [(0.0, float(n) * 120, 0.0, float(n) * 120, True)]}]
    xml = str(tmp_path / "clip.xml")
    open(xml, "w", encoding="utf-8").close()
    hl = [150, 151, 152]                # жёлтые рядом, в середине ролика
    emphasis.compute_emphasis(emphasis.EmphasisInputs(
        words=words, parsed=({"fps": FPS, "dur": float(n)}, cams, [], []), xml_path=xml,
        idx=hl, audio_window=read_window, emit=lambda *a, **k: None))

    assert asked, "ни одного окна не запрошено: %r" % (asked,)
    # Признаки посчитаны ровно для жёлтых и их окружения (не для всего ролика)
    want = set(range(145, 158))
    assert set(got) == want, "признаки посчитаны не по окрестности жёлтых: %r" % (sorted(set(got)),)
    assert len(got) == len(want), "часть слов посчитана дважды: %r" % (got,)
    # Прочитанная окрестность много уже ролика (его длина — 400 с): чтение зависит от
    # числа жёлтых, а не от длины исходника
    got_len = sum(w1 - w0 for w0, w1 in asked)
    assert got_len < words[-1].end / 4, (
        "прочитан почти весь исходник (%r с из %r с)" % (got_len, words[-1].end))


# --------------------------------------------------------------------------- #
# 5. Схема: ручки на месте, с подсказками и переводом
# --------------------------------------------------------------------------- #

def _fields():
    out = {}

    def walk(items):
        for it in items:
            if it.get("type") == "group":
                walk(it.get("items", []))
            elif it.get("type") == "field" and it.get("key"):
                out[it["key"]] = it

    for layer in style_schema.LAYERS:
        walk(layer.get("items", []))
    return out


def test_ручки_в_схеме_с_подсказками_и_по_умолчанию_эмоции():
    """Выпадашка способа и три числовые ручки — в схеме, с `tip` и дефолтами WX."""
    from core import styles
    f = _fields()
    for k in ("hl_zoom_strength", "hl_zoom_min_pct", "hl_zoom_max_per_piece",
              "hl_zoom_second_min_s"):
        assert k in f, "нет ручки %s в схеме стиля" % k
        assert str(f[k].get("tip") or "").strip(), "у ручки %s пустая подсказка" % k
        # WX4: ручки видны ВСЕГДА. Раньше show_if вешал их на галку «Только сильные
        # жёлтые» камеры 1, и у владельца (наезды в основном по камере 2) они оставались
        # скрытыми. Правило общее для обеих камер — это и сказано в подсказке.
        assert "show_if" not in f[k] or not f[k]["show_if"], \
            "ручка %s снова прячется за show_if" % k
        # регистр не важен: фраза в подсказке стоит с большой буквы после точки
        assert "действует на камеры, где включено «только сильные жёлтые»" \
            in f[k]["tip"].lower(), \
            "подсказка %s не говорит, на какие камеры ручка действует" % k
    assert f["hl_zoom_strength"]["ctl"] == "select"
    assert [o[1] for o in f["hl_zoom_strength"]["options"]] == ["по эмоциям", "по голосу"]
    assert styles.BASE["hl_zoom_strength"] == "emotion", "дефолт — «по эмоциям»"
    # Дефолты — правила WX: порог p70 жёлтых клипа, два наезда на кусок, второй — в куске от 8 с
    assert styles.BASE["hl_zoom_min_pct"] == 70.0, "дефолт порога — p70 жёлтых (замер 02.10.2026)"
    assert styles.BASE["hl_zoom_max_per_piece"] == 2, "дефолт предела наездов — 2 (WX)"
    assert styles.BASE["hl_zoom_second_min_s"] == 8.0, "дефолт длины куска — 8 с (WX)"
    assert f["hl_zoom_max_per_piece"]["min"] == 1 and f["hl_zoom_max_per_piece"]["max"] == 3
    assert f["hl_zoom_second_min_s"]["step"] == 0.5
    assert f["hl_zoom_min_pct"]["step"] == 5


def test_ручки_переведены_и_строки_в_словаре():
    """Каждая подпись и подсказка новых ручек есть в static/i18n/en.json."""
    en = json.loads(open(os.path.join(ROOT, "static", "i18n", "en.json"),
                         encoding="utf-8").read())
    missing = []
    for it in _fields().values():
        if not str(it.get("key", "")).startswith("hl_zoom_"):
            continue
        for s in (it.get("label"), it.get("tip")):
            if s and s not in en:
                missing.append(s[:60])
        for opt in it.get("options") or []:
            if isinstance(opt, (list, tuple)) and len(opt) > 1 and opt[1] not in en:
                missing.append(opt[1])
    assert not missing, "нет перевода: %r" % missing


def test_два_переключателя_в_словаре_переведены():
    """Варианты выпадашки — «по эмоциям» и «по голосу» — с непустым английским."""
    en = json.loads(open(os.path.join(ROOT, "static", "i18n", "en.json"),
                         encoding="utf-8").read())
    for s in ("по эмоциям", "по голосу"):
        assert en.get(s), "нет перевода варианта %r" % s
