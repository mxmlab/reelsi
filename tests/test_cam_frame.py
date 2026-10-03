# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Рамка кадра камеры: какая часть исходника попадает в кадр ролика.

Раньше обрезка всегда была по центру: кадр заполнялся целиком, лишнее срезалось
поровну. Теперь у КАЖДОЙ камеры спикера может быть своя рамка (поле `frame` профиля
рядом с LUT и форматом кадра): x/y — точка исходника, встающая в центр кадра ролика,
zoom — проценты от «кадр заполнен ровно». Камеры в поле нет или значения 0.5/0.5/100 —
поведение прежнее, и это стережётся отдельно: эталоны .jsx (tests/test_geometry_python)
и XML не должны меняться ничем, кроме строки масштаба клипов.

Здесь проверяется вся цепочка:

1. зажим (`core/frame.py`): сдвиг за край исходника не выходит — кадр всегда заполнен;
2. профиль: `frame` пишется, мусор — ValueError, дефолт ключа не заводит;
3. .jsx: масштаб и сдвиг клипа перебивки, те же числа у рото-копии и маски;
4. превью: вырезка из исходника — ровно та же, что даёт .jsx (реальные функции
   static/app/87-camframe.js гоняются node'ом, копий формул в тесте нет);
5. XML для Premiere: Basic Motion (Scale и Center) по рамке;
6. слежение за головой: с рамкой зажим считает края СМЕЩЁННОГО слоя, а не центрального.

Запуск: py -3.10 -m pytest tests/test_cam_frame.py -q
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

from core import frame, speakers, xml2ae, xmlbuild  # noqa: E402
from core.project_file import write_project  # noqa: E402
from core.xml2ae.layout import _cam1_follow_keys  # noqa: E402

CAMFRAME_JS = os.path.join(ROOT, "static", "app", "87-camframe.js")
node = pytest.mark.skipif(not shutil.which("node"),
                          reason="контракт фронта требует node в PATH")


# --------------------------------------------------------------------------- #
# 1. Зажим: кадр всегда заполнен
# --------------------------------------------------------------------------- #
def test_shift_is_zero_when_there_is_no_room():
    """Рамка 0.9 при 100 % на исходнике ровно по кадру: сдвигаться некуда — 0.

    Пропорции совпали (1080×1920 в вертикальном кадре) — видимый кусок равен
    исходнику, и любой сдвиг открыл бы по краю пустоту.
    """
    assert frame.frame_shift({"x": 0.9, "y": 0.5, "zoom": 100}, 1080, 1920, 1080, 1920) == (0.0, 0.0)
    assert frame.frame_crop({"x": 0.9, "y": 0.5, "zoom": 100}, 1080, 1920, 1080, 1920) == \
        (0.0, 0.0, 1080.0, 1920.0)


def test_shift_reaches_exactly_the_edge_at_zoom_200():
    """При 200 % видимый кусок вдвое меньше: сдвиг доходит РОВНО до края исходника.

    Кусок 540×960 из 1080×1920, центр 0.9 — это x=972, а дальше 810: правый край
    куска упирается в правый край исходника. Слой при этом сдвинут на
    (sw−W)/2 = 540 px — то есть на весь запас, который есть.
    """
    x, y, w, h = frame.frame_crop({"x": 0.9, "y": 0.5, "zoom": 200}, 1080, 1920, 1080, 1920)
    assert (w, h) == (540.0, 960.0)
    assert x == 540.0 and y == 480.0, (x, y)
    assert x + w == 1080.0, "кусок вышел за правый край исходника"
    dx, dy = frame.frame_shift({"x": 0.9, "y": 0.5, "zoom": 200}, 1080, 1920, 1080, 1920)
    assert dx == -540.0 and dy == 0.0, (dx, dy)


def test_crop_keeps_the_frame_aspect_on_a_wide_source():
    """Горизонтальный исходник в вертикальном кадре: кусок рамки — с пропорциями КАДРА.

    3840×2160 в 1080×1920: заполнение по высоте (0.888…), видимый кусок 1215×2160 —
    ширина больше кадра, и по горизонтали есть куда сдвигать; по вертикали запаса нет.
    """
    x, y, w, h = frame.frame_crop({"x": 0.0, "y": 0.9, "zoom": 100}, 3840, 2160, 1080, 1920)
    assert w == pytest.approx(1215.0, abs=0.01)
    assert h == 2160.0
    assert y == 0.0, "по вертикали запаса нет — кусок стоит по центру"
    assert x == 0.0, "сдвиг влево до края исходника"
    dx, dy = frame.frame_shift({"x": 0.0, "y": 0.9, "zoom": 100}, 3840, 2160, 1080, 1920)
    assert dx == pytest.approx(1166.67, abs=0.01)   # (3840·0.888… − 1080)/2 — весь запас по ширине
    assert dy == 0.0


def test_default_frame_is_exactly_zero_shift():
    """Дефолт (нет камеры в поле / 0.5/0.5/100) — ровно нули, а не «почти нули».

    Сборка по этому признаку не пишет в .jsx ни рамки, ни сдвига, и XML остаётся
    побайтово прежним: округление до сотых дало бы здесь 0.01 px и тихо разошлось.
    """
    for fr in (None, {}, {"x": 0.5, "y": 0.5, "zoom": 100}, {"zoom": 100}, {"x": "0.5", "y": 0.5}):
        assert frame.frame_shift(fr, 3840, 2160, 1080, 1920) == (0.0, 0.0)
        assert frame.is_frame_default(fr) is True
    assert frame.is_frame_default({"x": 0.5, "y": 0.5, "zoom": 101}) is False


# --------------------------------------------------------------------------- #
# 2. Профиль спикера: поле frame
# --------------------------------------------------------------------------- #
@pytest.fixture()
def speaker_dir(tmp_path, monkeypatch):
    """Своя папка спикеров: боевая `speakers/` — личные профили владельца."""
    d = tmp_path / "speakers"
    d.mkdir()
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(d))
    return d


def test_frame_is_saved_and_read(speaker_dir):
    """Рамка пишется в профиль и читается оттуда — по номеру камеры с 1, как LUT."""
    speakers.save("Кадр", {"frame": {"2": {"x": 0.3, "y": 0.4, "zoom": 150}}})
    prof = speakers.load("Кадр")
    assert prof["frame"] == {"2": {"x": 0.3, "y": 0.4, "zoom": 150.0}}
    assert frame.frames_of("Кадр")["2"]["zoom"] == 150.0
    assert frame.frame_of(prof["frame"], 2)["x"] == 0.3
    # камеры без рамки — дефолт: обрезка по центру
    assert frame.is_frame_default(frame.frame_of(prof["frame"], 1)) is True


@pytest.mark.parametrize("bad", [
    {"1": {"zoom": 50}},                       # меньше 100 — кадр не заполнен
    {"1": {"zoom": 401}},                      # больше 400 — уже мыло
    {"1": {"x": 1.5}},                         # доля кадра, а не пиксели
    {"1": {"y": -0.01}},
    {"0": {"x": 0.3}},                         # номер камеры с 1
    {"cam1": {"x": 0.3}},
    {"1": {"x": 0.3, "k": 5}},                 # чужой ключ
    {"1": 0.5},                                # не объект
    ["1"],
])
def test_bad_frame_raises(speaker_dir, bad):
    """Мусор в рамке при ЗАПИСИ — ValueError (как у lut и format), файла не остаётся."""
    with pytest.raises(ValueError):
        speakers.save("Кривой", {"frame": bad})
    assert os.listdir(str(speaker_dir)) == [], "битый профиль всё-таки записан"


def test_default_frame_is_not_written(speaker_dir):
    """Дефолт ключа не заводит: «профиль без правок» не растёт от пустой правки."""
    speakers.save("Ровный", {"frame": {"1": {"x": 0.5, "y": 0.5, "zoom": 100}}})
    assert "frame" not in speakers.load("Ровный")
    speakers.save("Пусто", {})
    assert "frame" not in speakers.load("Пусто")


def test_broken_frame_in_file_is_softened(speaker_dir):
    """Файл, правленный руками, сборку не роняет: мусор смягчается до дефолта.

    Проверка на записи строгая, значит сюда мусор приходит только мимо интерфейса —
    и ролик обязан собраться, а не упасть на первом же кадре.
    """
    with open(os.path.join(str(speaker_dir), "Ручной.json"), "w", encoding="utf-8") as f:
        json.dump({"label": "Ручной", "frame": {"1": {"x": "мусор", "zoom": "нет"},
                                                "2": {"x": 3, "zoom": 900},
                                                "мусор": {"x": 0.1}}}, f)
    prof = speakers.load("Ручной")
    assert frame.frame_of(prof["frame"], 1) == {"x": 0.5, "y": 0.5, "zoom": 100.0}
    got = frame.frames_of(prof)
    assert got["2"]["x"] == 1.0 and got["2"]["zoom"] == 400.0, got   # зажато в границы
    assert "мусор" not in got                                        # чужой ключ пропущен


# --------------------------------------------------------------------------- #
# 3-4. План, .jsx и превью: одни и те же числа
# --------------------------------------------------------------------------- #
CAM2_DIMS = (3840, 2160)          # перебивка снята горизонтально
CAM1_DIMS = (1080, 1920)
FRAME2 = {"x": 0.3, "y": 0.5, "zoom": 200}


def _dims(path):
    """Размер исходника по имени камеры: файлов в тесте нет, ffprobe не зовём."""
    return CAM2_DIMS if "cam2" in str(path) else CAM1_DIMS


@pytest.fixture()
def xml_frames(tmp_path, monkeypatch, speaker_dir):
    """XML эталонного таймлайна + сайдкар со спикером, у которого рамка камеры 2."""
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    speakers.save("Кадр", {"frame": {"2": dict(FRAME2)}})
    write_project(os.path.splitext(dst)[0] + ".project.json",
                  {"speaker": "Кадр"})  # type: ignore[arg-type]
    monkeypatch.setattr(xml2ae.build, "_media_dims", _dims)
    return dst


def _build(xml, tmp_path, name="out.jsx"):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name), inserts=[],
                                   style={"intro_riser": False}, disclaimer="",
                                   intro_riser=False, emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read()


def _cams_js(jsx):
    return json.loads(re.search(r"var CAM=(\[.*?\]);", jsx, re.S).group(1))


def test_plan_carries_the_frame_of_each_camera(xml_frames):
    """Рамка попадает в план числами: доли из профиля + готовый сдвиг в px кадра."""
    plan = xml2ae.scene_plan(xml_frames, inserts=[], style={"intro_riser": False},
                             disclaimer="", intro_riser=False, emit=lambda *a: None)
    cams = plan["cams"]
    assert "frame" not in cams[0], "камера без рамки не должна нести поле frame"
    fr = cams[1]["frame"]
    assert (fr["x"], fr["y"], fr["zoom"]) == (0.3, 0.5, 200.0)
    dx, dy = frame.frame_shift(FRAME2, *CAM2_DIMS, plan["w"], plan["h"])
    assert (fr["dx"], fr["dy"]) == (dx, dy)
    assert dx != 0, "тест бесполезен: сдвиг вышел нулевым"


def test_jsx_scale_and_position_of_the_insert(xml_frames, tmp_path):
    """У перебивки одна формула масштаба на все камеры и сдвиг рамки в позиции.

    Масштаб считается в AE по РЕАЛЬНОМУ размеру исходника (fitS × zoom), поэтому в
    .jsx едет множитель, а не число; сдвиг считает Python (размера исходника план не
    знает) — и он лежит в данных камеры рядом с долями рамки.
    """
    jsx = _build(xml_frames, tmp_path)
    cam2 = _cams_js(jsx)[1]
    assert cam2["frame"]["zoom"] == 200.0
    dx, dy = frame.frame_shift(FRAME2, *CAM2_DIMS, 1080, 1920)
    assert (cam2["frame"]["dx"], cam2["frame"]["dy"]) == (dx, dy)
    # масштаб: c[5] (масштаб из Премьера) больше не участвует ни у одной камеры
    assert "var csc = fitS*(track.frame?track.frame.zoom:100)/100*(isSecond?1:CAM1_FIT/100);" in jsx
    assert "isSecond ? c[5]" not in jsx, "в .jsx вернулся масштаб из Премьера"
    # позиция слоя — по рамке, в МИРОВЫХ координатах и до привязки к нулу
    assert "if (track.frame) try{ lay.property(\"ADBE Transform Group\")" in jsx
    assert ".setValue([W/2+track.frame.dx, H/2+track.frame.dy]); }catch(e){}" in jsx
    assert jsx.index("track.frame.dx") < jsx.index("lay.parent = nul;")


def test_jsx_roto_repeats_the_frame_of_its_camera(xml_frames, tmp_path):
    """Рото-копия и маска повторяют масштаб и сдвиг СВОЕЙ камеры.

    Рото-копия обязана лежать пиксель-в-пиксель с кадром: тот же масштаб (rfit × zoom
    своей камеры) и тот же сдвиг рамки. Числа берутся из тех же данных CAM[ci].frame,
    что у слоя клипа, — своей копии рамки у рото нет.
    """
    jsx = _build(xml_frames, tmp_path)
    assert "var rfr = CAM[ci] && CAM[ci].frame;" in jsx
    assert "if (rfr) rsc = rfit*rfr.zoom/100*(ci==0?CAM1_FIT/100:1);" in jsx
    assert '.setValue([rfr.dx,rfr.dy]); }catch(e){}' in jsx
    # у КАЖДОЙ из двух рото-копий (кадр и маска) — свой сдвиг
    assert jsx.count(".setValue([rfr.dx,rfr.dy])") == 2, "маска или копия остались без рамки"
    # и у камеры с рамкой это те же самые числа, что у клипа
    cam2 = _cams_js(jsx)[1]
    assert cam2["frame"]["dx"] != 0 and cam2["frame"]["dy"] == 0


def test_jsx_without_frames_is_untouched_except_insert_scale(tmp_path, monkeypatch, speaker_dir):
    """Без рамок .jsx не меняется ничем, кроме строки масштаба клипов.

    Ровно это обещано эталонам (fixtures/golden_geometry.jsx): ни данных рамки в CAM,
    ни подстановок сдвига, ни строчек у рото. Сравниваются ДВЕ сборки: со спикером без
    рамки и без спикера вовсе — они обязаны совпасть байт в байт.
    """
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    monkeypatch.setattr(xml2ae.build, "_media_dims", _dims)
    plain = _build(dst, tmp_path, "plain.jsx")
    speakers.save("БезРамки", {"cut": {"onset_db": 15}})
    write_project(os.path.splitext(dst)[0] + ".project.json",
                  {"speaker": "БезРамки"})  # type: ignore[arg-type]
    with_spk = _build(dst, tmp_path, "spk.jsx")
    assert plain == with_spk
    assert '"frame"' not in plain
    assert "track.frame" not in plain.replace(
        "fitS*(track.frame?track.frame.zoom:100)/100*(isSecond?1:CAM1_FIT/100)", "")
    assert "rfr" not in plain


def _js_funcs(names):
    """Настоящие тела функций (и нужные им константы) из static/app/87-camframe.js.

    Ни одной копии формулы в тесте: и функции, и числа берутся из отгружаемого файла.
    """
    with open(CAMFRAME_JS, "r", encoding="utf-8") as f:
        src = f.read()
    out = []
    consts = "\n".join(re.findall(r"^const CAMFRAME_\w+ = .*$", src, re.M))
    for name in names:
        m = re.search(r"function\s+%s\s*\(" % re.escape(name), src)
        assert m, f"в 87-camframe.js не нашлась функция {name}"
        i = src.index("{", m.end() - 1)
        depth = 0
        for j in range(i, len(src)):
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
                if depth == 0:
                    out.append(src[m.start():j + 1])
                    break
        else:
            raise AssertionError(f"не сошлись скобки у {name}")
    return consts + "\n" + "\n".join(out)


def _run_node(code):
    p = subprocess.run(["node", "-e", code], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@node
def test_preview_crop_is_the_same_as_jsx(xml_frames, tmp_path):
    """Превью режет исходник ровно там же, где .jsx: числа сходятся до сотых.

    Проверяются РЕАЛЬНЫЕ функции отгружаемого файла (вырезаны из него целиком):
    копия формул в тесте разошлась бы с превью молча. Мост между двумя языками —
    сам сдвиг: в .jsx он в пикселях кадра, в превью кусок в пикселях ИСХОДНИКА.
    """
    jsx = _build(xml_frames, tmp_path)
    cam2 = _cams_js(jsx)[1]["frame"]
    plan = xml2ae.scene_plan(xml_frames, inserts=[], style={"intro_riser": False},
                             disclaimer="", intro_riser=False, emit=lambda *a: None)
    vw, vh = CAM2_DIMS
    code = _js_funcs(("camFrameNorm", "camFrameDefault", "camFrameCrop", "camFrameShift",
                      "camFrameZoom")) + """
    var fr=%s;
    console.log(JSON.stringify({crop:camFrameCrop(%d,%d,%d,%d,fr),
                                shift:camFrameShift(%d,%d,%d,%d,fr),
                                zoom:camFrameZoom(fr)}));
    """ % (json.dumps(FRAME2), vw, vh, plan["w"], plan["h"], vw, vh, plan["w"], plan["h"])
    out = _run_node(code)
    sx, sy, sw, sh = out["crop"]
    # кусок превью и сдвиг .jsx — одно и то же место исходника
    dx, dy = cam2["dx"], cam2["dy"]
    f = max(plan["w"] / vw, plan["h"] / vh)
    z = cam2["zoom"] / 100.0
    assert out["zoom"] == pytest.approx(z, abs=1e-9)
    assert dx == pytest.approx((vw / 2 - (sx + sw / 2)) * f * z, abs=0.01), (dx, sx, sw)
    assert dy == pytest.approx((vh / 2 - (sy + sh / 2)) * f * z, abs=0.01)
    # видимый кусок закрывает кадр ровно: sw · f · z = W — иначе рамка «не заполнена»
    assert sw * f * z == pytest.approx(plan["w"], abs=0.01)
    py_x, py_y, py_w, py_h = frame.frame_crop(FRAME2, vw, vh, plan["w"], plan["h"])
    assert (sx, sy, sw, sh) == (py_x, py_y, py_w, py_h)


@node
def test_preview_crop_clamps_at_the_edge():
    """Кусок превью не выходит за исходник: тянуть мышкой за край нечем.

    Тот же зажим, что в core/frame.py, но на живых размерах: x=0.99 при 100 % на
    горизонтальном исходнике упирается в правый край и дальше не едет.
    """
    code = _js_funcs(("camFrameNorm", "camFrameDefault", "camFrameCrop")) + """
    var W=1080,H=1920,vw=3840,vh=2160;
    console.log(JSON.stringify({
      edge:camFrameCrop(vw,vh,W,H,{x:0.99,y:0.5,zoom:100}),
      none:camFrameCrop(1080,1920,W,H,{x:0.99,y:0.5,zoom:100})}));
    """
    out = _run_node(code)
    x, y, w, h = out["edge"]
    assert x + w == pytest.approx(3840.0, abs=0.01), "кусок вышел за правый край исходника"
    assert x >= 0 and y >= 0
    # пропорции исходника ровно по кадру: сдвигаться некуда — кусок стоит по центру
    assert out["none"][0] == 0.0 and out["none"][2] == 1080.0


# --------------------------------------------------------------------------- #
# 5. XML для Premiere: Basic Motion (Scale и Center)
# --------------------------------------------------------------------------- #
def _probe(w, h, rot=0):
    return {"dur_s": 60.0, "width": w, "height": h, "timecode": "01:00:00:00",
            "fps": 25.0, "rotation": rot}


def _build_xml(tmp_path, monkeypatch, speaker=None):
    """XML тем же путём, что в бою: сайдкар со спикером рядом + xmlbuild.build."""
    cams = []
    for name in ("cam1.mp4", "cam2.mp4"):
        p = tmp_path / name
        p.write_bytes(b"video")
        cams.append(str(p))
    out = tmp_path / "clip.xml"
    proj = {"cams": cams, "offsets": [0.0, 0.0], "fps": 60, "keep": [[0.0, 2.0]]}
    if speaker:
        proj["speaker"] = speaker
    write_project(str(tmp_path / "clip.project.json"), proj)      # type: ignore[arg-type]

    def pr(p):
        return _probe(*(CAM2_DIMS if "cam2" in str(p) else CAM1_DIMS))

    monkeypatch.setattr(xmlbuild, "probe", lambda p, **k: pr(p))
    xmlbuild.build(cams, [(0.0, 2.0)], [0.0, 0.0], str(out), assign=[0])
    return str(out)


def _clip_filters(text):
    return re.findall(r"<parameterid>scale</parameterid>\s*<name>Scale</name>.*?"
                      r"<value>([\d.]+)</value>.*?"
                      r"<parameterid>center</parameterid>.*?"
                      r"<horiz>(-?[\d.]+)</horiz><vert>(-?[\d.]+)</vert>", text, re.S)


def test_xml_basic_motion_follows_the_frame(tmp_path, monkeypatch, speaker_dir):
    """Масштаб и центр клипа камеры — по рамке (Basic Motion: Scale и Center).

    У камеры 2 рамка 0.3 / 200 %: масштаб удваивается, центр уезжает так, чтобы точка
    0.3 исходника встала в центр кадра. Первая камера без рамки — как после шага 1.
    """
    speakers.save("Кадр", {"frame": {"2": dict(FRAME2)}})
    xml = _build_xml(tmp_path, monkeypatch, speaker="Кадр")
    got = _clip_filters(open(xml, encoding="utf-8").read())
    assert len(got) >= 2, "в XML нет Basic Motion у клипов камер"
    # камера 1: исходник ровно по кадру — 100 % и центр кадра
    assert got[0] == ("100.0", "0", "0"), got[0]
    dx, dy = frame.frame_shift(FRAME2, *CAM2_DIMS, 1080, 1920)
    _x, _y, cw, ch = frame.frame_crop(FRAME2, *CAM2_DIMS, 1080, 1920)
    scale = round(frame.cover_scale(*CAM2_DIMS, 1080, 1920) * 2, 2)
    assert got[1][0] == "%g" % scale, got[1]
    assert (float(got[1][1]), float(got[1][2])) == (dx, dy), (got[1], dx, dy)
    assert cw * ch > 0


def test_xml_without_frame_is_precisely_the_old_one(tmp_path, monkeypatch, speaker_dir):
    """Без рамки XML остаётся прежним: масштаб «заполнить кадр» и центр 0/0.

    Побайтовое совпадение двух сборок — со спикером без рамки и вовсе без спикера:
    рамка на дефолте не имеет права тронуть ни одного числа.
    """
    plain = open(_build_xml(tmp_path, monkeypatch), encoding="utf-8").read()
    speakers.save("БезРамки", {"format": "9:16", "frame": {"2": {"x": 0.5, "y": 0.5, "zoom": 100}}})
    assert "frame" not in speakers.load("БезРамки")
    with_spk = open(_build_xml(tmp_path, monkeypatch, speaker="БезРамки"), encoding="utf-8").read()
    assert plain == with_spk
    assert "<horiz>0</horiz><vert>0</vert>" in plain
    assert "50.4" not in plain
    assert _clip_filters(plain)[1][0] == "%g" % frame.cover_scale(*CAM2_DIMS, 1080, 1920)


# --------------------------------------------------------------------------- #
# 6. Слежение за головой: зажим считает края смещённого слоя
# --------------------------------------------------------------------------- #
def _cams_for_follow():
    clips = [[0, 600, 0, 600, True, 100.0]]
    return [{"path": "cam1.mp4", "name": "cam1", "clips": clips},
            {"path": "cam2.mp4", "name": "cam2", "clips": [[0, 0, 0, 0, False, 100.0]]}]


def _layer_edges(s, fit_w, W, Cx, pan_x, frame_dx):
    """Края слоя на экране при зуме s — та же формула, что в зажиме слежения."""
    left = Cx + s * (W / 2.0 + frame_dx - fit_w / 2.0 - Cx) + pan_x
    right = Cx + s * (W / 2.0 + frame_dx + fit_w / 2.0 - Cx) + pan_x
    return left, right


def test_follow_keys_keep_the_frame_filled_with_a_camera_frame():
    """С рамкой (x=0.7, zoom=150) слежение не выводит край слоя внутрь кадра.

    Слой клипа с рамкой увеличен и смещён, поэтому зажим обязан считать края
    СМЕЩЁННОГО слоя: с прежним (центральным) зажимом ключ уводил бы правый край
    влево от края кадра — в кадре открылась бы пустота.
    """
    W, H = 1080, 1920
    w_src, h_src = CAM2_DIMS
    fr = {"x": 0.7, "y": 0.5, "zoom": 150}
    dx, dy = frame.frame_shift(fr, w_src, h_src, W, H)
    assert dx < 0, "рамка влево — сдвиг отрицательный, иначе тест не о том"
    pts = [[t / 10.0, 0.95] for t in range(0, 60)]      # голова у правого края исходника
    keys = _cam1_follow_keys(cams=_cams_for_follow(), pts=pts, w_src=w_src, h_src=h_src,
                             zoom_keys=[(0, 100.0)], holds=[False], fps=60.0, W=W, H=H,
                             cx=0.5, pan_x=0.0, cam1_fit=100.0, target=0.5, smooth_s=0.6,
                             min_scale=0.0, frame={"x": fr["x"], "y": fr["y"],
                                                   "zoom": float(fr["zoom"]), "dx": dx, "dy": dy})
    assert keys, "слежение не дало ни одного ключа — тест бесполезен"
    fit_w = w_src * max(W / w_src, H / h_src) * (fr["zoom"] / 100.0)
    for f, off in keys:
        left, right = _layer_edges(1.0, fit_w, W, 0.5 * W, 0.0, dx)
        assert left + off <= 1e-6, f"кадр открылся слева на кадре {f}: {left + off}"
        assert right + off >= W - 1e-6, f"кадр открылся справа на кадре {f}: {right + off}"
    # тест не пустой: прежний (центральный) зажим тот же сдвиг ПРОПУСТИЛ бы, и правый
    # край слоя встал бы внутри кадра
    blind_fit = w_src * max(W / w_src, H / h_src)
    blind_min = W / 2.0 - blind_fit / 2.0                  # низшая граница прежнего зажима
    left, right = _layer_edges(1.0, fit_w, W, 0.5 * W, 0.0, dx)
    assert right + blind_min < W - 1e-6, "прежний зажим справился бы — проверять нечего"


def test_follow_keys_without_frame_did_not_change():
    """Без рамки слежение считает ровно как раньше: те же ключи, что при явном дефолте."""
    args = dict(cams=_cams_for_follow(), pts=[[t / 10.0, 0.55] for t in range(0, 60)],
                w_src=CAM1_DIMS[0], h_src=CAM1_DIMS[1], zoom_keys=[(0, 120.0)],
                holds=[False], fps=60.0, W=1080, H=1920, cx=0.5, pan_x=0.0,
                cam1_fit=100.0, target=0.5, smooth_s=0.6, min_scale=0.0)
    old = _cam1_follow_keys(**args)                                    # type: ignore[arg-type]
    new = _cam1_follow_keys(frame={"x": 0.5, "y": 0.5, "zoom": 100.0, "dx": 0.0, "dy": 0.0},
                            **args)                                    # type: ignore[arg-type]
    assert old == new != []


# --------------------------------------------------------------------------- #
# 7. Черновик: обрезка по рамке
# --------------------------------------------------------------------------- #
def test_draft_crop_filter(monkeypatch):
    """Черновик обрезает исходник по рамке — тот же кусок, что у .jsx, и чётными px.

    Рамки нет — фильтра нет вовсе: фильтр-цепочка черновика остаётся прежней.
    """
    from core import draftrender
    monkeypatch.setattr(draftrender, "_display_dims", lambda src: (*CAM2_DIMS, False))
    fr = {"x": 0.3, "y": 0.5, "zoom": 200}
    x, y, w, h = frame.frame_crop(fr, *CAM2_DIMS, 1080, 1920)
    got = draftrender._crop_filter(fr, "cam2.mp4", 1080, 1920)
    assert got == "crop=%d:%d:%d:%d," % (int(w) // 2 * 2, int(h) // 2 * 2,
                                         int(x) // 2 * 2, int(y) // 2 * 2), got
    assert draftrender._crop_filter({"x": 0.5, "y": 0.5, "zoom": 100}, "cam2.mp4", 1080, 1920) == ""


def test_draft_proxy_key_follows_the_frame(tmp_path):
    """Прокси черновика пересобирается при правке рамки: старый кадр не переиспользуется."""
    from core import draftrender
    src = tmp_path / "cam2.mp4"
    src.write_bytes(b"video")
    a = draftrender._proxy_path(str(src), 720, 1280, str(tmp_path), "crop=100:100:0:0,")
    b = draftrender._proxy_path(str(src), 720, 1280, str(tmp_path), "")
    c = draftrender._proxy_path(str(src), 720, 1280, str(tmp_path), "crop=200:200:0:0,")
    assert len({a, b, c}) == 3, "смена рамки не поменяла имя прокси"
