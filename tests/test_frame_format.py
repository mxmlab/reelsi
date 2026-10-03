# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Формат кадра — параметр насквозь: профиль спикера → XML → план → .jsx, превью, стоки.

Формат ролика жил числом 1080×1920 в полутора десятках мест, и «поменять формат»
означало найти их все. Теперь он объявлен один раз (`core/frame.py`), лежит полем
профиля спикера, а спикер нарезки — в сайдкаре `<стем>.project.json` рядом с XML:
тем же путём его достают LUT (`core/lutbake.py`) и обработка голоса
(`core/voicefx.py`). Здесь стережётся вся цепочка:

1. сам модуль: размер кадра, ориентация, масштаб «заполнить кадр» (в том числе
   для съёмки с поворотом из метаданных — 1280×720 с `rotation=90` показывается
   вертикально) и поворот сторон;
2. профиль спикера: `format` пишется, незнакомое значение — ValueError, а без
   поля ролик собирается 9:16, как собирался всегда;
3. XML: формат `1:1` даёт секвенцию 1080×1080, план `w=h=1080` и композицию
   .jsx 1080×1080; масштаб клипа камеры — «заполнить кадр» по ЕЁ пробе;
4. предпросмотр: `insPreviewBox`/`insVideoFill`/`insVideoPan` берут кадр из плана
   (реальные функции файла гоняются node'ом, копий в тесте нет).

Запуск: py -3.10 -m pytest tests/test_frame_format.py -q
"""
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

from core import frame, speakers, xml2ae, xmlbuild  # noqa: E402
from core.project_file import write_project  # noqa: E402

JS = os.path.join(ROOT, "static", "app", "85-inserts-view.js")
node = pytest.mark.skipif(not shutil.which("node"),
                          reason="контракт фронта требует node в PATH")


# --------------------------------------------------------------------------- #
# 1. Модуль формата
# --------------------------------------------------------------------------- #
def test_formats_and_default():
    """Что бывает и что по умолчанию: 9:16 — прежнее поведение, оно и дефолт."""
    assert frame.DEFAULT == "9:16"
    assert frame.FORMATS == {"9:16": (1080, 1920), "1:1": (1080, 1080),
                            "4:5": (1080, 1350), "16:9": (1920, 1080)}
    assert frame.frame_size(frame.DEFAULT) == (1080, 1920)


@pytest.mark.parametrize("bad", [None, "", "916", "вертикаль", 1080, ["9:16"], {"fmt": "1:1"}])
def test_unknown_format_falls_back_to_default(bad):
    """Незнакомое значение и None — DEFAULT, а не отказ: профиль правят руками."""
    assert frame.frame_size(bad) == (1080, 1920)


def test_orientation_for_every_format():
    """Ориентацию спрашивают стоки (Pexels: portrait/landscape/square)."""
    assert frame.orientation("9:16") == "portrait"
    assert frame.orientation("16:9") == "landscape"
    assert frame.orientation("1:1") == "square"
    assert frame.orientation("4:5") == "portrait"
    assert frame.orientation(None) == "portrait"


def test_cover_scale_fills_the_frame():
    """Масштаб «заполнить кадр»: 4K-вертикаль в вертикали 50 %, горизонталь — по ширине."""
    assert frame.cover_scale(2160, 3840, 1080, 1920) == 50.0
    assert frame.cover_scale(1080, 1920, 1080, 1920) == 100.0
    # 3840×2160 в вертикальном кадре: 1920/2160 = 88.888… — округление до сотых
    # (столько же знаков, сколько у остальных чисел XML)
    assert frame.cover_scale(3840, 2160, 1080, 1920) == 88.89
    # размер не прочитался — не масштабируем (и не делим на ноль)
    assert frame.cover_scale(0, 0, 1080, 1920) == 100.0


def test_cover_scale_of_rotated_source():
    """Повёрнутый исходник 1280×720 — это вертикаль 720×1280: 150 %, а не 266 %.

    Так снято у владельца (телефон/камера пишет кадр горизонтально, поворот —
    матрицей отображения). Считать по закодированному кадру нельзя: масштаб вышел
    бы 266.67, и вертикаль встала бы мимо кадра.
    """
    w, h = frame.display_size(1280, 720, 90)
    assert (w, h) == (720, 1280)
    assert frame.cover_scale(w, h, 1080, 1920) == 150.0
    # без поворота те же числа дают совсем другой масштаб — тест не пустой
    assert frame.cover_scale(1280, 720, 1080, 1920) == 266.67


@pytest.mark.parametrize("rot,size", [
    (0, (1280, 720)),
    (90, (720, 1280)),
    (-90, (720, 1280)),
    (270, (720, 1280)),
    (180, (1280, 720)),
    (None, (1280, 720)),
    ("мусор", (1280, 720)),
])
def test_display_size_rotation(rot, size):
    """Поворот на четверть оборота меняет стороны местами, всё прочее — нет."""
    assert frame.display_size(1280, 720, rot) == size


# --------------------------------------------------------------------------- #
# 2. Профиль спикера: поле format
# --------------------------------------------------------------------------- #
@pytest.fixture()
def speaker_dir(tmp_path, monkeypatch):
    """Своя папка спикеров: боевая `speakers/` — личные профили владельца."""
    d = tmp_path / "speakers"
    d.mkdir()
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(d))
    return d


def test_speaker_format_is_saved(speaker_dir):
    """Формат пишется в профиль и читается оттуда (и по имени, и по label)."""
    speakers.save("Квадрат", {"format": "1:1"})
    assert speakers.load("Квадрат")["format"] == "1:1"
    assert frame.speaker_format("Квадрат") == "1:1"
    assert frame.speaker_format(speakers.load("Квадрат")) == "1:1"


def test_speaker_unknown_format_raises(speaker_dir):
    """Неизвестный формат при ЗАПИСИ — ValueError (как у lut и voice_fx)."""
    with pytest.raises(ValueError) as e:
        speakers.save("Кривой", {"format": "916"})
    assert "формат" in str(e.value)
    with pytest.raises(ValueError):
        speakers.save("Кривой", {"format": 1080})
    assert os.listdir(str(speaker_dir)) == [], "битый профиль всё-таки записан"


def test_speaker_without_format_is_default(speaker_dir):
    """Профиль без поля — 9:16: профили, заведённые раньше, работают как работали."""
    speakers.save("Старый", {"cut": {"onset_db": 15}})
    assert "format" not in speakers.load("Старый")
    assert frame.speaker_format("Старый") == "9:16"
    assert frame.speaker_format(None) == "9:16"
    assert frame.speaker_format("НетТакого") == "9:16"
    assert frame.speaker_format({"format": "16:9"}) == "16:9"


def test_speaker_empty_format_is_not_written(speaker_dir):
    """Пустое поле = «формат по умолчанию»: в профиле его нет, как у lut."""
    speakers.save("Пусто", {"format": "  "})
    assert "format" not in speakers.load("Пусто")


# --------------------------------------------------------------------------- #
# 3. XML, план и .jsx в формате 1:1
# --------------------------------------------------------------------------- #
def _cam(tmp_path, name="cam1.mp4"):
    p = tmp_path / name
    p.write_bytes(b"video")
    return str(p)


def _probe(w, h, rot=0):
    return {"dur_s": 60.0, "width": w, "height": h, "timecode": "01:00:00:00",
            "fps": 25.0, "rotation": rot}


def _build_xml(tmp_path, monkeypatch, speaker=None, probes=None, name="clip.xml"):
    """XML тем же путём, что в бою: сайдкар со спикером рядом + xmlbuild.build."""
    cams = [_cam(tmp_path, "cam1.mp4"), _cam(tmp_path, "cam2.mp4")]
    out = tmp_path / name
    proj = {"cams": cams, "offsets": [0.0, 0.0], "fps": 60, "keep": [[0.0, 2.0]]}
    if speaker:
        proj["speaker"] = speaker
    write_project(str(tmp_path / (name.replace(".xml", "") + ".project.json")),  # type: ignore[arg-type]
                  proj)
    pr = probes or (lambda p: _probe(1920, 1080))
    monkeypatch.setattr(xmlbuild, "probe", lambda p, **k: pr(p))
    xmlbuild.build(cams, [(0.0, 2.0)], [0.0, 0.0], str(out), assign=[0])
    return str(out)


def _build_xml_at(tmp_path, monkeypatch, seq_w, seq_h, speaker=None, name="clip.xml"):
    """XML в заданном размере секвенции — как его пишет нарезка ДО сборки .jsx.

    Размер секвенции передаётся явно (`seq_w`/`seq_h`) ровно так же, как это делают
    `gigaam_cut.pipeline` и `omni_cut`: формат известен из параметров нарезки, а
    профиль спикера в этот момент ещё ни при чём. Сайдкар пишется только вместе со
    спикером — «чужой XML» это XML без сайдкара вовсе.
    """
    cams = [_cam(tmp_path, "cam1.mp4"), _cam(tmp_path, "cam2.mp4")]
    out = tmp_path / name
    if speaker:
        write_project(str(tmp_path / (name.replace(".xml", "") + ".project.json")),  # type: ignore[arg-type]
                      {"cams": cams, "offsets": [0.0, 0.0], "fps": 60,
                       "keep": [[0.0, 2.0]], "speaker": speaker})
    monkeypatch.setattr(xmlbuild, "probe", lambda p, **k: _probe(1920, 1080))
    xmlbuild.build(cams, [(0.0, 2.0)], [0.0, 0.0], str(out), assign=[0],
                   seq_w=seq_w, seq_h=seq_h)
    return str(out)


def test_xml_plan_and_jsx_in_1x1(tmp_path, monkeypatch, speaker_dir):
    """Формат 1:1 насквозь: секвенция 1080×1080, план w=h=1080, композиция 1080×1080."""
    speakers.save("Квадрат", {"format": "1:1"})
    xml = _build_xml(tmp_path, monkeypatch, speaker="Квадрат")

    text = open(xml, encoding="utf-8").read()
    assert 'MZ.Sequence.PreviewFrameSizeWidth="1080"' in text
    assert 'MZ.Sequence.PreviewFrameSizeHeight="1080"' in text
    assert "<width>1080</width><height>1080</height>" in text, \
        "секвенция собрана не в 1080×1080"
    assert "<width>1080</width><height>1920</height>" not in text, "в XML осталась вертикаль"

    meta, cams, _subs, _ins = xml2ae.parse_full(xml)
    assert (meta["w"], meta["h"]) == (1080, 1080)
    plan = xml2ae.scene_plan(xml, inserts=[])
    assert (plan["w"], plan["h"]) == (1080, 1080)

    jsx, _nc, _ns = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / "out.jsx"))
    js = open(jsx, encoding="utf-8-sig").read()
    assert re.search(r"var W=1080, H=1080,", js), "в .jsx уехал не тот кадр"
    # сама композиция создаётся по W/H шаблона — то есть по кадру плана
    assert re.search(r'var main = app\.project\.items\.addComp\("[^"]*", W, H, 1\.0,', js), \
        "композиция .jsx собрана не по кадру плана"
    # Та же сборка без формата — прежняя вертикаль: тест различает форматы, а не «что-то».
    # Файл свой: в clip.xml уже лежит квадрат, а кадр существующего XML без явного
    # формата не трогают (`core/frame.output_frame_size`) — сборка с нуля берёт дефолт.
    js_v = open(xml2ae.to_ae_full(_build_xml(tmp_path, monkeypatch, name="vert.xml"),
                                  jsx_path=str(tmp_path / "v.jsx"))[0],
                encoding="utf-8-sig").read()
    assert re.search(r"var W=1080, H=1920,", js_v), "9:16 перестал быть форматом по умолчанию"


# --------------------------------------------------------------------------- #
# 3a. Чей кадр главнее: XML или профиль
# --------------------------------------------------------------------------- #
def _untouched(path):
    """(mtime, содержимое) файла — чтобы поймать ЛЮБУЮ перезапись, а не только правку."""
    return os.path.getmtime(path), open(path, encoding="utf-8").read()


def test_ensure_frame_without_sidecar_keeps_xml_size(tmp_path, monkeypatch, speaker_dir):
    """Без сайдкара вовсе: XML 2160×3840 остаётся собой — кадр 2160×3840, файл не тронут.

    Профиля нет — значит и формата, который был бы главнее, нет. Раньше сюда
    подставлялся DEFAULT (1080×1920), и чужой 4K насильно ужимался: Premiere
    показывал 2160×3840, а .jsx собирался вчетверо мельче.
    """
    xml = _build_xml_at(tmp_path, monkeypatch, 2160, 3840)
    assert not os.path.exists(os.path.splitext(xml)[0] + ".project.json"), "сайдкар всё-таки есть"
    before = _untouched(xml)
    assert frame.ensure_frame(xml) == (2160, 3840)
    assert _untouched(xml) == before, "XML переписан без надобности"


def test_ensure_frame_speaker_without_format_takes_xml_size(tmp_path, monkeypatch, speaker_dir):
    """Спикер без поля `format` не приказывает: XML 1920×1080 остаётся собой.

    Профиль, заведённый до появления форматов, поля не имеет — и кадр такого
    ролика задаёт сам XML, а не DEFAULT.
    """
    speakers.save("БезФормата", {"cut": {"onset_db": 15}})
    xml = _build_xml_at(tmp_path, monkeypatch, 1920, 1080, speaker="БезФормата")
    before = _untouched(xml)
    assert frame.ensure_frame(xml) == (1920, 1080)
    assert _untouched(xml) == before, "XML переписан без надобности"


def test_ensure_frame_same_proportions_keeps_larger_xml(tmp_path, monkeypatch, speaker_dir):
    """Формат задан явно, но пропорции те же: XML 2160×3840 при формате 9:16 не трогают.

    Пиксели сравнивать нельзя: 2160×3840 — тот же формат 9:16, просто вдвое
    крупнее. Стиль под такой кадр пересчитывается сам (`scale_style`), а XML
    остаётся как нарезан.
    """
    speakers.save("Вертикаль", {"format": "9:16"})
    xml = _build_xml_at(tmp_path, monkeypatch, 2160, 3840, speaker="Вертикаль")
    before = _untouched(xml)
    assert frame.ensure_frame(xml) == (2160, 3840)
    assert _untouched(xml) == before, "4K-вертикаль пересобрали в 1080×1920"


def test_ensure_frame_other_proportions_rebuilds_xml(tmp_path, monkeypatch, speaker_dir):
    """Формат 1:1 против XML 1080×1920: пропорции разошлись — XML пересобран в квадрат.

    Обратная сторона правила: тут профиль действительно приказывает, и XML
    переписывается тем же путём, что «Сохранить» в редакторе нарезки.
    """
    speakers.save("Квадрат", {"format": "1:1"})
    xml = _build_xml_at(tmp_path, monkeypatch, 1080, 1920, speaker="Квадрат")
    assert frame.ensure_frame(xml) == (1080, 1080)
    text = open(xml, encoding="utf-8").read()
    assert "<width>1080</width><height>1080</height>" in text, \
        "XML остался в старом кадре — Premiere и AE разъедутся по формату"
    assert "<width>1080</width><height>1920</height>" not in text, "в XML осталась вертикаль"


def test_rebuild_frame_preserves_multicam_layout_and_creates_backup(tmp_path, monkeypatch, speaker_dir):
    """Смена формата кадра сохраняет раскладку двух камер и делает .xml.format.bak."""
    speakers.save("Квадрат", {"format": "1:1"})
    cams = [_cam(tmp_path, "cam1.mp4"), _cam(tmp_path, "cam2.mp4")]
    xml = tmp_path / "clip.xml"
    segments = [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)]
    assign = [0, 1, 0]
    proj_path = tmp_path / "clip.project.json"
    write_project(str(proj_path), {
        "cams": cams, "offsets": [0.0, 0.0], "fps": 60,
        "keep": segments, "assign": assign, "speaker": "Квадрат"
    })
    monkeypatch.setattr(xmlbuild, "probe", lambda p, **k: _probe(1920, 1080))
    xmlbuild.build(cams, segments, [0.0, 0.0], str(xml), assign=assign,
                   seq_w=1080, seq_h=1920)

    orig_text = open(xml, encoding="utf-8").read()
    assert "<width>1080</width><height>1920</height>" in orig_text

    res = frame.ensure_frame(str(xml))
    assert res == (1080, 1080)

    bak = tmp_path / "clip.xml.format.bak"
    assert bak.is_file(), "Бекап .xml.format.bak не создан перед пересборкой"
    bak_text = bak.read_text(encoding="utf-8")
    assert "<width>1080</width><height>1920</height>" in bak_text

    rebuilt_text = open(xml, encoding="utf-8").read()
    assert "<width>1080</width><height>1080</height>" in rebuilt_text

    _meta, parsed_cams, _subs, _ins = xml2ae.parse_full(str(xml))
    assert len(parsed_cams) == 2, "Вторая камера пропала из XML после пересборки формата"
    root = ET.fromstring(rebuilt_text)
    vtracks = root.findall(".//media/video/track")
    assert len(vtracks) >= 2, "В XML нет видеодорожки второй камеры"
    v2_clips = vtracks[1].findall("clipitem")
    enabled = [c.findtext("enabled") == "TRUE" for c in v2_clips]
    assert enabled == [False, True, False], f"Раскладка камер сломалась: {enabled}"



def test_xml_scale_is_cover_scale_of_each_camera(tmp_path, monkeypatch):
    """Масштаб клипа — свой у каждой камеры и по ЕЁ пробе (константы 50.4 больше нет).

    4K-вертикаль заполняет вертикальный кадр при 50 %, горизонтальная камера —
    по ширине (88.89). Раньше обе ехали с одним числом 50.4.
    """
    def pr(p):
        return _probe(2160, 3840) if "cam1" in str(p) else _probe(3840, 2160)

    xml = _build_xml(tmp_path, monkeypatch, probes=pr)
    text = open(xml, encoding="utf-8").read()
    scales = re.findall(r"<parameterid>scale</parameterid>\s*<name>Scale</name>"
                        r".*?<value>([\d.]+)</value>", text, re.S)
    assert scales, "в XML нет масштаба клипа вовсе"
    assert scales[0] == "50.0", f"4K-вертикаль встала не в кадр: {scales[0]}"
    assert all(v == "88.89" for v in scales[1:]), f"горизонтальная камера: {scales}"
    assert "50.4" not in text, "в XML вернулась прежняя константа"


def test_xml_scale_counts_rotation(tmp_path, monkeypatch):
    """Повёрнутая съёмка: масштаб считается по ВИДИМОМУ кадру (720×1280 → 150)."""
    xml = _build_xml(tmp_path, monkeypatch,
                     probes=lambda p: _probe(1280, 720, rot=90))
    text = open(xml, encoding="utf-8").read()
    assert "<value>150.0</value>" in text, "поворот из метаданных не учтён в масштабе"


# --------------------------------------------------------------------------- #
# 4. Предпросмотр: кадр берётся из плана (боевые функции файла, node)
# --------------------------------------------------------------------------- #
def _func(src, name):
    """Тело функции name из исходника (тот же приём, что в tests/test_ins_video_preview)."""
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


def _js():
    """Настоящие тела функций из static/app/85-inserts-view.js — для прогона в node."""
    with open(JS, "r", encoding="utf-8") as f:
        src = f.read()
    return "\n".join(_func(src, n) for n in
                     ("insPreviewBox", "insVideoFill", "insVideoPan", "ipvPlanWH"))


def _run_node(code):
    p = subprocess.run(["node", "-e", code], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@node
def test_preview_geometry_follows_the_plan_frame():
    """Кадр плана 1080×1080 даёт другие числа, чем 1080×1920, — во всех трёх функциях.

    Проверяются РЕАЛЬНЫЕ функции отгружаемого файла (вырезаны из него целиком):
    копия формул в тесте разошлась бы с превью молча.
    """
    code = _js() + """
    var IPV={plan:null};
    function look(w,h){
      IPV.plan={w:w,h:h};
      return {box:insPreviewBox(1000,2000,100,150,100),
              fill:insVideoFill(1920,1080,100),
              pan:insVideoPan(1920,1080,0,0)};}
    console.log(JSON.stringify({sq:look(1080,1080), tall:look(1080,1920)}));
    """
    out = _run_node(code)
    sq, tall = out["sq"], out["tall"]

    # коробка фото: у квадрата видимая часть упирается в высоту кадра (1080), у вертикали — нет
    assert tall["box"]["h"] == pytest.approx(792.0), tall["box"]
    assert sq["box"]["h"] == pytest.approx(528.0), sq["box"]
    assert tall["box"]["w"] == sq["box"]["w"] == pytest.approx(528.0), (tall["box"], sq["box"])
    # заполнение видео: горизонтальный ролик заполняет квадрат один в один, вертикаль — с запасом
    assert (sq["fill"]["w"], sq["fill"]["h"]) == (1920.0, 1080.0), sq["fill"]
    assert tall["fill"]["w"] == pytest.approx(3413.33, abs=0.01), tall["fill"]
    # запас панорамы — тоже от кадра: у квадрата 420 px, у вертикали 1166.67
    assert sq["pan"]["sx"] == pytest.approx(420.0), sq["pan"]
    assert tall["pan"]["sx"] == pytest.approx(1166.67, abs=0.01), tall["pan"]
    assert sq["pan"]["sy"] == tall["pan"]["sy"] == 0.0


@node
def test_preview_geometry_without_plan_keeps_vertical():
    """Без плана (ошибка запроса, стенды) функции считают по прежней вертикали."""
    code = _js() + """
    var IPV={plan:null};
    console.log(JSON.stringify({fill:insVideoFill(1920,1080,100),
                                pan:insVideoPan(1920,1080,0,0),
                                box:insPreviewBox(1000,2000,100,150,100)}));
    """
    out = _run_node(code)
    assert out["fill"]["w"] == pytest.approx(3413.33, abs=0.01), out["fill"]
    assert out["pan"]["sx"] == pytest.approx(1166.67, abs=0.01), out["pan"]
    assert out["box"]["h"] == pytest.approx(792.0), out["box"]
