# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Субтитры считаются по частоте проекта, а пишутся в кадры 60 (задание MB, п. 2).

ПОЧЕМУ эти тесты существуют. `_reproject_subs` строил отображение кадров одной и той же
частотой для СТАРОГО таймлайна и для НОВОГО, а роут звал его с `xmlbuild.FPS` (=60).
Стороны разные по природе: старые кадры — это кадры входного XML, то есть частота
проекта (25, 29.97 — что пришло из Премьера), новые — кадры нашей секвенции, `build`
всегда пишет 60. У 25-кадрового проекта слово уезжало в 60/25 = 2.4 раза дальше, чем
звучит; на экране субтитр подписывал чужую речь.

Контракт: старая сторона считается по частоте проекта, новая — по `xmlbuild.FPS`,
перевод идёт через секунды исходника. Путь «монтаж не менялся» (ранний возврат) остаётся
прежним только при равных частотах — при разных слова всё равно переводятся.

Ожидания здесь считаются ЧЕРЕЗ СЕКУНДЫ (независимой моделью), а не магическими кадрами.

Запуск: py -3.10 -m pytest tests/test_r12_mb_fps.py -q -p no:cacheprovider
"""
import gzip
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

from core import xml2ae  # noqa: E402

FIX = os.path.join(HERE, "fixtures")
H = {"Host": "127.0.0.1:5001"}
RATE_25 = "<rate><timebase>25</timebase><ntsc>FALSE</ntsc></rate>"
NEW_FPS = 60                       # xmlbuild.FPS: столько пишет build


# --------------------------------------------------------------------------- #
# Фикстуры и утилиты
# --------------------------------------------------------------------------- #
def _patch_seq_rate(text, rate):
    """Частота СЕКВЕНЦИИ: первый `<rate>` после `<sequence>` (у клипов свои частоты)."""
    i = text.index("<sequence")
    j = text.index("<rate>", i)
    k = text.index("</rate>", j) + len("</rate>")
    return text[:j] + rate + text[k:]


def _fixture_xml(tmp_path, rate=None, name="timeline.xml"):
    """Копия эталона с субтитрами (253 слова, 2 камеры) в tmp_path; rate — частота проекта."""
    with gzip.open(os.path.join(FIX, "timeline_subs.xml.gz"), "rb") as g:
        text = g.read().decode("utf-8")
    if rate is not None:
        text = _patch_seq_rate(text, rate)
    dst = str(tmp_path / name)
    with open(dst, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return dst


@pytest.fixture
def fake_probe(monkeypatch):
    """ffprobe вместо настоящего: медиа фикстур на диске не лежит."""
    from core import xmlbuild
    monkeypatch.setattr(xmlbuild, "probe", lambda p, **k: {
        "dur_s": 600.0, "width": 1920, "height": 1080, "timecode": "01:00:00:00"})


@pytest.fixture
def client():
    from flask import Flask
    import api
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def _state(path):
    st = os.stat(path)
    with open(path, "rb") as f:
        return f.read(), st.st_mtime_ns


def _route_project(tmp_path, rate):
    """XML в tmp_path + сайдкар проекта, как его кладёт редактор. -> (xml, subs, keep)"""
    from api.editor import _project_from_xml
    from core import xmlbuild
    xml = _fixture_xml(tmp_path, rate=rate)
    _meta, _cams, subs, _ins = xml2ae.parse_full(xml)
    proj = _project_from_xml(xml)                  # keep в секундах по частоте проекта
    with open(os.path.splitext(xml)[0] + ".project.json", "w", encoding="utf-8") as f:
        json.dump(proj, f, indent=1)
    assert proj["fps"] == (xmlbuild.FPS if rate is None else 25)
    return xml, subs, [(float(s), float(e)) for s, e in proj["keep"]]


def _spans_sec(keep, fps):
    """Куски в СЕКУНДАХ: [(начало_на_таймлайне, конец, начало_в_исходнике)] — курсором, как build."""
    out, tl = [], 0
    for s, e in keep:
        in0, out0 = round(s * fps), round(e * fps)
        if out0 - in0 <= 0:
            continue
        out.append((tl / fps, (tl + out0 - in0) / fps, in0 / fps))
        tl += out0 - in0
    return out


def _expected_words(old_subs, old_keep, new_keep, old_fps, new_fps):
    """Независимая модель ожидания: [(слово, секунда на НОВОМ таймлайне)].

    Только секунды: время слова на старом таймлайне -> время в исходнике -> время на новом
    таймлайне. Слова ближе 0.5с к краю куска пропускаем — там судьбу слова решает
    округление до кадра, и проверять на них нечего.
    """
    old_sp, new_sp = _spans_sec(old_keep, old_fps), _spans_sec(new_keep, new_fps)
    safe = 0.5
    out = []
    for (f0, _f1, word) in old_subs:
        t = f0 / old_fps
        src = next((s0 + (t - a) for a, b, s0 in old_sp if a <= t < b), None)
        if src is None:
            continue
        seg = next(((a2, b2, s2) for a2, b2, s2 in new_sp if s2 <= src < s2 + (b2 - a2)),
                   None)
        if seg is None:
            continue                                  # кусок исходника удалили
        if src - seg[2] < safe or (seg[2] + (seg[1] - seg[0])) - src < safe:
            continue
        out.append((word, seg[0] + (src - seg[2])))
    return out


def _check_words(subs_new, expected):
    """Каждое ожидаемое слово стоит на своей секунде (порядок слов сохраняется)."""
    j = 0
    for (s, _e, word) in subs_new:
        if j < len(expected) and expected[j][0] == word:
            want = expected[j][1]
            got = s / NEW_FPS
            assert abs(got - want) <= 2.0 / NEW_FPS, (
                f"«{word}»: кадр {s} = {got:.3f}с, а слово звучит на {want:.3f}с "
                f"нового таймлайна")
            j += 1
    assert j == len(expected), (
        f"сверено {j} слов из {len(expected)}: {expected[j][0]!r} ждали на "
        f"{expected[j][1]:.3f}с нового таймлайна, а в XML слова стоят иначе — "
        f"кадры считаются не по частоте проекта")
    return j


# --------------------------------------------------------------------------- #
# Роут: 25 к/с — слова едут по времени, а не по «кадрам проекта»
# --------------------------------------------------------------------------- #
def test_проект_25_слова_встают_на_своё_время(client, tmp_path, fake_probe):
    """PAL-проект (25 к/с): убрали первый блок — слово остаётся на своей СЕКУНДЕ."""
    xml, subs_old, old_keep = _route_project(tmp_path, RATE_25)
    assert len(old_keep) >= 2 and subs_old
    new_keep = [list(k) for k in old_keep[1:]]        # выбросили первый блок

    expected = _expected_words(subs_old, old_keep, new_keep, 25, NEW_FPS)
    assert len(expected) >= 20, "фикстура не годится: слишком мало слов для сверки"

    before = _state(xml)
    d = client.post("/api/editor_save",
                    json={"xml": xml, "keep": new_keep}, headers=H).get_json()
    assert d.get("ok"), d
    assert _state(xml) != before, "XML не пересобран — тест ничего не проверяет"

    meta_new, _cams, subs_new, _ins = xml2ae.parse_full(xml)
    assert meta_new["fps"] == NEW_FPS                 # наша секвенция всегда 60
    assert len(subs_new) < len(subs_old), "слова выброшенного блока остались в XML"

    assert _check_words(subs_new, expected) >= 20


def test_проект_60_числа_не_меняются(client, tmp_path, fake_probe):
    """60 к/с: поведение прежнее — те же кадры, что до правки (защита от регресса)."""
    from api.editor import _reproject_subs
    xml, subs_old, old_keep = _route_project(tmp_path, None)
    assert len(old_keep) >= 2 and subs_old
    new_keep = [list(k) for k in old_keep[1:]]

    expected = _expected_words(subs_old, old_keep, new_keep, 60, NEW_FPS)
    assert len(expected) >= 20

    before = _state(xml)
    d = client.post("/api/editor_save",
                    json={"xml": xml, "keep": new_keep}, headers=H).get_json()
    assert d.get("ok"), d
    assert _state(xml) != before

    meta_new, _cams, subs_new, _ins = xml2ae.parse_full(xml)
    assert meta_new["fps"] == NEW_FPS
    assert _check_words(subs_new, expected) >= 20

    # Прежнее поведение — одна частота на обе стороны — осталось тем же: new_fps по
    # умолчанию равен старой, поэтому старые вызовы функции ничего не теряют.
    words = [{"w": w, "start": int(s), "end": int(e)} for (s, e, w) in subs_old]
    new_keep_f = [(float(s), float(e)) for s, e in new_keep]
    assert (_reproject_subs(words, [], old_keep, new_keep_f, 60) ==
            _reproject_subs(words, [], old_keep, new_keep_f, 60, NEW_FPS))


# --------------------------------------------------------------------------- #
# Прямой вызов: две стороны — две частоты
# --------------------------------------------------------------------------- #
def test_прямой_вызов_разные_частоты():
    """Кадр 100 старого таймлайна (25 к/с) = 4с -> 6с исходника -> кадр 360 нового (60)."""
    from api.editor import _reproject_subs
    words = [{"w": "слово", "start": 100, "end": 110}]
    old_keep, new_keep = [(2.0, 10.0)], [(0.0, 10.0)]

    got, _yellow = _reproject_subs(words, [], old_keep, new_keep, 25, NEW_FPS)

    assert got and got[0]["start"] == 360, got
    assert got[0]["end"] - got[0]["start"] == round(10 / 25 * NEW_FPS)   # длина в кадрах 60

    # Одна частота на обе стороны (прежнее поведение) — 220 и 150 кадров: чужое время
    assert _reproject_subs(words, [], old_keep, new_keep, 60)[0][0]["start"] == 220
    assert _reproject_subs(words, [], old_keep, new_keep, 25)[0][0]["start"] == 150


def test_монтаж_не_менялся_но_частоты_разные():
    """Ранний возврат «монтаж тот же» верен только при равных частотах.

    Тот же keep с обеих сторон: слово на 4-й секунде старого таймлайна (кадр 100 при 25)
    обязано остаться на 4-й секунде нового — это кадр 240, а не 100."""
    from api.editor import _reproject_subs
    words = [{"w": "слово", "start": 100, "end": 110}]
    keep = [(2.0, 10.0)]

    got, _yellow = _reproject_subs(words, [], keep, keep, 25, NEW_FPS)

    assert got and got[0]["start"] == 240, got
    # при равных частотах ранний возврат по-прежнему не трогает кадры (случай cams_save)
    same, _y = _reproject_subs(words, [], keep, keep, NEW_FPS, NEW_FPS)
    assert same[0]["start"] == 100
