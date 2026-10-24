# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""«Без фона» у фото-вставки: флаг вставки `nobg` в плане сцены и ключ профиля спикера
`ins_nobg`.

Решение: флаг «без фона» живёт в САМОЙ вставке. План сцены и превью читают только его и
про спикера ничего не знают; спикер даёт значение по умолчанию, когда картинка встаёт во
вставку (это уже фронт — см. tests/test_two_speakers.py). Здесь — серверная половина:
1. `speakers.save`: ins_nobg — только bool; строка или число — ValueError; True сохраняется.
2. План сцены: фото с nobg=True идёт кэшем без фона (`insertlib.nobg_path`); nobg=False или
   поля нет — исходный файл; видео с nobg=True — исходный путь (фон у видео не снимаем).
3. Тот же путь доезжает до .jsx: сборка не снимает фон второй раз.

rembg не зовём: `insertlib.nobg_path` подменён, это единственная точка снятия фона.

Запуск:  py -3.10 -m pytest tests/test_speaker_nobg.py -q -p no:cacheprovider
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

os.environ.setdefault("REELSI_NO_BROWSER", "1")

from core import insertlib, speakers, xml2ae  # noqa: E402
from core.xml2ae import build as xml_build  # noqa: E402


@pytest.fixture()
def xml_subs(tmp_path):
    """Фикстура таймлайна (та же, что у test_plate_fix)."""
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


@pytest.fixture()
def nobg_calls(tmp_path, monkeypatch):
    """Подмена снятия фона. Как настоящий nobg_path: фон снимаем ОДИН раз на файл, дальше
    отдаём кэш рядом с исходником. Возвращает список реальных снятий — по нему видно,
    сколько раз rembg позвали бы на самом деле."""
    calls = []
    cache = {}

    def fake_nobg(media, emit=None):
        if media not in cache:
            calls.append(media)
            cache[media] = str(tmp_path / (os.path.basename(media) + ".nobg.png"))
        return cache[media]

    monkeypatch.setattr(insertlib, "nobg_path", fake_nobg)
    return calls


@pytest.fixture(autouse=True)
def _no_media_probe(monkeypatch):
    """Размер видео не читаем (ffprobe): геометрия видео тут не предмет проверки."""
    monkeypatch.setattr(xml_build, "_media_dims", lambda path: (1920, 1080))


def _png(path, w, h):
    from PIL import Image
    Image.new("RGBA", (int(w), int(h)), (180, 40, 40, 255)).save(str(path))
    return str(path)


def _plan(xml, inserts):
    return xml2ae.scene_plan(xml, inserts=[dict(x) for x in inserts], style={"intro_riser": False},
                             disclaimer="", intro_riser=False, emit=lambda *a, **k: None)


def _ins_jsons(jsx):
    return json.loads(re.search(r"var INSERTS=(\[.*?\]);", jsx).group(1))


# ---- 1. профиль спикера ----------------------------------------------------------------

@pytest.fixture()
def speaker_dir(tmp_path, monkeypatch):
    d = tmp_path / "speakers"
    d.mkdir()
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(d))
    return d


def test_speaker_save_accepts_only_bool_for_ins_nobg(speaker_dir):
    """Строка «yes» или число 1 — ValueError: любое непустое значение истинно и молча
    включило бы «без фона» у всех картинок спикера. True сохраняется; поля нет — выключено."""
    for bad in ("yes", 1, 0, ["x"]):
        with pytest.raises(ValueError):
            speakers.save("СпикерА", {"ins_nobg": bad})

    key, _path = speakers.save("СпикерА", {"ins_nobg": True})
    assert speakers.load(key)["ins_nobg"] is True

    key2, _path2 = speakers.save("СпикерБ", {"label": "СпикерБ"})
    assert "ins_nobg" not in speakers.load(key2)


# ---- 2. план сцены ---------------------------------------------------------------------

def test_plan_photo_with_nobg_uses_cache_path(xml_subs, tmp_path, nobg_calls):
    """Фото с nobg=True: в плане и в .jsx путь кэша без фона, фон снят ровно один раз."""
    photo = _png(tmp_path / "photo.png", 1408, 768)
    cache = str(tmp_path / "photo.png.nobg.png")
    ins = [{"type": "photo", "style": "cam2", "media": photo, "start_s": 8.0, "dur_s": 1.5,
            "nobg": True}]

    plan = _plan(xml_subs, ins)
    assert plan["inserts"][0]["media"] == cache, plan["inserts"][0]["media"]
    # метка для предпросмотра: путь плана уже кэш, карточка ищется по исходнику
    assert plan["inserts"][0].get("nobg") is True
    assert nobg_calls == [photo]

    jsx, _n, _s = xml2ae.to_ae_full(xml_subs, jsx_path=str(tmp_path / "out.jsx"),
                                    inserts=[dict(x) for x in ins], style={"intro_riser": False},
                                    disclaimer="", intro_riser=False, emit=lambda *a, **k: None)
    jsx_text = open(jsx, encoding="utf-8-sig").read()
    assert _ins_jsons(jsx_text)[0]["media"] == cache
    assert len(nobg_calls) == 1, "в сборке фон снимали второй раз"


@pytest.mark.parametrize("flag", [False, None], ids=["nobg_false", "nobg_absent"])
def test_plan_photo_without_nobg_keeps_source(xml_subs, tmp_path, nobg_calls, flag):
    """nobg=False или поля нет — исходный файл с фоном, rembg не зовём."""
    photo = _png(tmp_path / "photo.png", 800, 600)
    ins = {"type": "photo", "style": "cam2", "media": photo, "start_s": 8.0, "dur_s": 1.5}
    if flag is not None:
        ins["nobg"] = flag

    plan = _plan(xml_subs, [ins])
    assert plan["inserts"][0]["media"] == photo
    assert "nobg" not in plan["inserts"][0], "метка кэша без фона у вставки без снятия фона"
    assert nobg_calls == []


def test_plan_video_with_nobg_keeps_source(xml_subs, tmp_path, nobg_calls):
    """У видео флаг «без фона» не действует: путь исходный, фон не снимаем."""
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\0" * 16)
    ins = [{"type": "video", "style": "cam2", "media": str(clip), "start_s": 8.0,
            "dur_s": 1.5, "nobg": True}]

    plan = _plan(xml_subs, ins)
    assert plan["inserts"][0]["media"] == str(clip)
    assert nobg_calls == []

