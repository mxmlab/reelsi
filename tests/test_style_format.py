# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Формат ролика и стиль: разметка полей, пересчёт под кадр, формат у спикера.

Стиль ОДИН на все форматы (9:16, 1:1, 4:5, 16:9), а его числа задуманы в кадре
1080×1920. При сборке они пересчитываются под кадр формата — правило объявлено
один раз (`core/style_geometry.py`) и уезжает фронту полем `geo` схемы панели.
Здесь стерегутся четыре вещи:

1. разметка: КАЖДЫЙ числовой ключ `styles.BASE` либо размечен видом (`x`/`y`/
   `size`/`frac`), либо стоит в явном списке «от кадра не зависит» — новое поле
   без пометки роняет тест с подсказкой, куда вписать;
2. пересчёт: при 1080×1920 — тождество (эталоны .jsx не меняются), в 1:1
   `y`-поля × 0.5625, `x` и `size` — без изменений; в 16:9 `x` × 1.778,
   `y` × 0.5625, `size` × 1;
3. сборка: план и .jsx берут пересчитанные числа, а формат у спикера главнее
   того, что записано в XML (XML при расхождении пересобирается);
4. фронт: пересчёт превью (`static/app/95-styles.js`) даёт те же числа, что
   Python, — таблица берётся из ответа схемы, функции гоняются node'ом как есть;
5. список форматов в редакторе профиля: его порядок берётся из `core/frame.py`
   (поле `format_order`), а не из ключей словаря — у словаря JSON порядка нет,
   и формат по умолчанию 9:16 стоял бы в списке последним.

Запуск: py -3.10 -m pytest tests/test_style_format.py -q
"""
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

from core import frame, speakers, style_geometry, style_schema, styles, xml2ae, xmlbuild  # noqa: E402
from core.project_file import write_project  # noqa: E402

STYLES_JS = os.path.join(ROOT, "static", "app", "95-styles.js")
node = pytest.mark.skipif(not shutil.which("node"),
                          reason="контракт фронта требует node в PATH")

H = {"Host": "127.0.0.1:5001"}


@pytest.fixture
def client():
    """Клиент Flask с тем же блюпринтом, что у сервера (образец — test_style_schema)."""
    from flask import Flask

    import api
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


# Поля с реальными числами — на них проверяется пересчёт: ноль умножить на что
# угодно можно, и тест не заметил бы, что множитель не тот.
STYLE_NUM = {
    "intro_x": 50.0,          # x, px
    "intro_y": 100.0,         # y, px
    "insert_c1_y": -40.0,     # y, px
    "top_line_w": 969.0,      # size, px
    "caption_size": 26.0,     # size, px
    "caption_x": 55.5,        # x, px
    "sub_bg_h": 160.0,        # size, px
    "sub_y": 0.5964,          # frac — не меняется
    "insert_c2_y": 0.172,     # frac — не меняется
    "cam1_zoom_cx": 0.5,      # frac — не меняется
}


def _style_with_numbers():
    """Базовый стиль с ненулевыми проверяемыми числами (BASE не мутируем)."""
    st = dict(styles.BASE)
    st.update(STYLE_NUM)
    return st


# --------------------------------------------------------------------------- #
# 1. Разметка полей
# --------------------------------------------------------------------------- #
def _numeric_base_keys():
    return {k for k, v in styles.BASE.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)}


def _nullable_base_keys():
    """Ключи BASE со значением None, у которых есть пометка вида.

    Их не видно в ``_numeric_base_keys`` (None — не число), но размером они быть не
    перестают: ``sub_anim_amt`` — пиксели подъёма субтитров, просто «пусто» у него
    значит «как задумано пресетом». Размеченное поле с None в BASE — не расхождение
    таблицы с BASE, и сторож обязан его пропускать; иначе выбор был бы между
    «не размечать размер» и «завести в BASE число-заглушку».
    """
    return {k for k, v in styles.BASE.items() if v is None and style_geometry.kind_of(k)}


def test_every_numeric_style_key_is_marked_or_independent():
    """Сторож разметки: числовое поле стиля либо размечено, либо явно независимо.

    Новое поле без пометки — красный тест с подсказкой, куда вписать: без этого
    оно молча осталось бы в пикселях 1080×1920 и в квадратном ролике уехало бы
    за край (или наоборот не поехало, если это размер).
    """
    numeric = _numeric_base_keys()
    marked = set(style_geometry.FIELD_KIND)
    indep = set(style_geometry.FRAME_INDEPENDENT)
    missing = sorted(numeric - marked - indep)
    assert not missing, (
        "числовые ключи styles.BASE без разметки: " + ", ".join(missing)
        + ".\nВпиши каждый в core/style_geometry.py: FIELD_KIND (вид x/y/size/frac, "
          "если поле зависит от размера кадра) или FRAME_INDEPENDENT "
          "(если не зависит — секунды, проценты, цвета, флаги).")
    extra = sorted((marked | indep) - numeric - _nullable_base_keys())
    assert not extra, (
        "в разметке есть ключи, которых нет среди числовых полей styles.BASE: "
        + ", ".join(extra) + " — таблица разошлась с BASE, убери или заведи поле.")


def test_kind_of_every_marked_field_is_known():
    """Вид поля — только x/y/size/frac: иначе фронт не поймёт, чем умножать."""
    bad = {k: v for k, v in style_geometry.FIELD_KIND.items()
           if v not in style_geometry.KINDS}
    assert not bad, f"неизвестные виды полей: {bad}"


def test_marking_is_disjoint():
    """Поле не может быть одновременно размеченным и «от кадра не зависит»."""
    both = sorted(set(style_geometry.FIELD_KIND) & set(style_geometry.FRAME_INDEPENDENT))
    assert not both, f"поля в обеих таблицах сразу: {both}"


def test_schema_gives_marking_to_frontend():
    """Схема панели отдаёт таблицу фронту полем geo — второй копии нет."""
    sch = style_schema.schema()
    assert sch["geo"] == style_geometry.FIELD_KIND
    assert set(sch["geo"].values()) <= set(style_geometry.KINDS)
    assert "geo" in sch and isinstance(sch["geo"], dict) and sch["geo"], "geo пусто"


# --------------------------------------------------------------------------- #
# 2. Пересчёт стиля
# --------------------------------------------------------------------------- #
def test_scale_style_identity_at_base_frame():
    """При 1080×1920 пересчёт — тождество: эталоны .jsx не меняются ни на байт."""
    st = _style_with_numbers()
    got = style_geometry.scale_style(st, 1080, 1920)
    assert got == st, "стиль в базовом кадре пересчитан — сломаются эталоны"


def test_scale_style_square_frame():
    """1:1: `y` × 0.5625, `x` и `size` без изменений."""
    st = _style_with_numbers()
    got = style_geometry.scale_style(st, 1080, 1080)
    assert got["intro_y"] == pytest.approx(56.25)        # 100 × 1080/1920
    assert got["insert_c1_y"] == pytest.approx(-22.5)    # −40 × 0.5625
    assert got["intro_x"] == 50.0, "x в квадрате не должен меняться"
    assert got["caption_x"] == 55.5, "x в квадрате не должен меняться"
    assert got["top_line_w"] == 969.0, "size в квадрате не должен меняться"
    assert got["caption_size"] == 26.0, "size в квадрате не должен меняться"
    # доли кадра не трогаются вовсе
    assert got["sub_y"] == st["sub_y"] and got["insert_c2_y"] == st["insert_c2_y"]
    assert got["cam1_zoom_cx"] == st["cam1_zoom_cx"]


def test_scale_style_landscape_frame():
    """16:9: `x` × 1.778, `y` × 0.5625, `size` × 1 (короткая сторона та же — 1080)."""
    st = _style_with_numbers()
    got = style_geometry.scale_style(st, 1920, 1080)
    assert got["intro_x"] == pytest.approx(88.89, abs=0.01)     # 50 × 1920/1080
    assert got["caption_x"] == pytest.approx(98.67, abs=0.01)   # 55.5 × 1.7778
    assert got["intro_y"] == pytest.approx(56.25)               # 100 × 1080/1920
    assert got["top_line_w"] == 969.0, "size в 16:9 не должен меняться"
    assert got["caption_size"] == 26.0


def test_scale_style_does_not_mutate_input():
    """Исходный стиль остаётся прежним: числа пользователя не трогаем."""
    st = _style_with_numbers()
    before = json.dumps(st, sort_keys=True)
    style_geometry.scale_style(st, 1080, 1080)
    assert json.dumps(st, sort_keys=True) == before


def test_scale_style_fills_missing_marked_fields_from_base():
    """Поля из таблицы, которого в стиле нет, берут дефолт BASE — но пересчитанный.

    Иначе стиль без ключа собрался бы по дефолту BASE (1080×1920) и разошёлся бы
    с тем же стилем, где ключ задан.
    """
    got = style_geometry.scale_style({}, 1080, 1080)
    assert got["intro_y"] == pytest.approx(styles.BASE["intro_y"] * 0.5625)
    assert got["top_line_w"] == styles.BASE["top_line_w"]
    assert got["caption_x"] == styles.BASE["caption_x"]
    # ключи вне таблицы не подставляются: их читает тот, кто знает их смысл
    assert "label" not in got


def test_scale_style_of_garbage_frame_is_identity():
    """Размер кадра не прочитался (0, мусор) — стиль как есть, а не деление на ноль."""
    st = _style_with_numbers()
    for wh in [(0, 0), (None, None), ("мусор", 1080)]:
        assert style_geometry.scale_style(st, *wh) == st


def test_mutation_removing_one_marking_turns_guard_red():
    """Мутация сторожа: сняли пометку с поля — тест краснеет (проверка теста).

    Копия таблицы урезается, оригинал не трогается: сторож обязан заметить
    числовое поле без разметки, иначе он стережёт пустоту.
    """
    saved = dict(style_geometry.FIELD_KIND)
    try:
        del style_geometry.FIELD_KIND["intro_y"]
        with pytest.raises(AssertionError) as e:
            test_every_numeric_style_key_is_marked_or_independent()
        assert "intro_y" in str(e.value), "сторож не назвал поле без разметки"
    finally:
        style_geometry.FIELD_KIND.clear()
        style_geometry.FIELD_KIND.update(saved)
    # вернули — снова зелено
    test_every_numeric_style_key_is_marked_or_independent()


# --------------------------------------------------------------------------- #
# 3. План и .jsx в кадре формата
# --------------------------------------------------------------------------- #
def _func(src, name):
    """Тело функции name из исходника (тот же приём, что в tests/test_frame_format)."""
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


def _run_node(code):
    p = subprocess.run(["node", "-e", code], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def _node_prelude():
    """Стенд для фронтовых функций: базовый кадр стиля — тот же, что у Python.

    Константа берётся из исходника, а не переписывается в тесте: разъехалась бы
    молча, и пересчёт превью поехал бы вместе с ней.
    """
    with open(STYLES_JS, "r", encoding="utf-8") as f:
        src = f.read()
    m = re.search(r"const\s+ST_BASE_W\s*=\s*(\d+)\s*,\s*ST_BASE_H\s*=\s*(\d+)\s*;", src)
    assert m, "в static/app/95-styles.js нет констант базового кадра ST_BASE_W/ST_BASE_H"
    return "const ST_BASE_W=%s,ST_BASE_H=%s;\n" % (m.group(1), m.group(2))


@node
def test_frontend_scaling_matches_python():
    """Фронтовый пересчёт даёт те же числа, что Python, — на всех трёх видах.

    Гоняются РЕАЛЬНЫЕ функции отгружаемого файла (вырезаны из него целиком), а
    таблица «поле -> вид» приезжает ответом схемы: копия в тесте разошлась бы с
    бэкендом молча.
    """
    geo = json.dumps(style_schema.schema()["geo"], ensure_ascii=False)
    style = json.dumps(_style_with_numbers(), ensure_ascii=False)
    with open(STYLES_JS, "r", encoding="utf-8") as f:
        src = f.read()
    code = (_node_prelude()
            + "".join(_func(src, n) for n in
                      ("stGeoTable", "stScaleFactor", "stScaleGeo", "stScaleRound", "stScaleStyle"))
            + "\nvar STSCHEMA={geo:%s};\nvar s=%s;\n" % (geo, style)
            + "console.log(JSON.stringify([stScaleStyle(s,1080,1920),stScaleStyle(s,1080,1080),"
              "stScaleStyle(s,1920,1080)]));")
    out = _run_node(code)
    for got, wh in zip(out, [(1080, 1920), (1080, 1080), (1920, 1080)]):
        want = style_geometry.scale_style(_style_with_numbers(), *wh)
        for key in STYLE_NUM:
            assert got[key] == pytest.approx(want[key]), (
                f"{key} в кадре {wh}: фронт {got[key]}, Python {want[key]}")


@node
def test_frontend_without_schema_keeps_base_numbers():
    """Схема не приехала — пересчёта нет: превью работает как раньше, а не падает."""
    with open(STYLES_JS, "r", encoding="utf-8") as f:
        src = f.read()
    code = (_node_prelude()
            + "".join(_func(src, n) for n in
                      ("stGeoTable", "stScaleFactor", "stScaleGeo", "stScaleRound", "stScaleStyle"))
            + "\nvar STSCHEMA=null;\nvar s={intro_y:100};\n"
              "console.log(JSON.stringify([stScaleStyle(s,1080,1080),stScaleGeo('intro_y',1080,1080)]));")
    out = _run_node(code)
    assert out[0]["intro_y"] == 100
    assert out[1] == 1


@node
def test_frontend_unscale_is_inverse_of_scale():
    """Обратный пересчёт драга возвращает базовую единицу (иначе уехало бы в 1.78)."""
    geo = json.dumps(style_schema.schema()["geo"], ensure_ascii=False)
    with open(STYLES_JS, "r", encoding="utf-8") as f:
        src = f.read()
    code = (_node_prelude()
            + "".join(_func(src, n) for n in
                      ("stGeoTable", "stScaleFactor", "stScaleGeo", "stScaleRound",
                       "stScaleStyle", "stUnscaleGeo"))
            + "\nvar STSCHEMA={geo:%s};\n" % geo
            + "console.log(JSON.stringify([stUnscaleGeo(56.25,'y',1080,1080),"
              "stUnscaleGeo(88.89,'x',1920,1080),stUnscaleGeo(26,'size',1080,1080)]));")
    out = _run_node(code)
    assert out[0] == pytest.approx(100.0), out
    assert out[1] == pytest.approx(50.0, abs=0.01), out
    assert out[2] == pytest.approx(26.0), out


@node
def test_frontend_drag_axis_scales_by_clip_format():
    """Драг в превью другого формата: ось переводится в базовые единицы по формату клипа.

    Гоняется `camFrameAxisK` (static/app/87-camframe.js): она берёт кадр из профиля
    СПИКЕРА КЛИПА, а формулу — из пересчёта стиля (`stScaleKind`). Без этого
    перетащил на квадрате — а в вертикали уехало (кадры разной высоты).
    """
    geo = json.dumps(style_schema.schema()["geo"], ensure_ascii=False)
    axis = json.dumps(style_schema.schema()["geo_axis"], ensure_ascii=False)
    # Размеры форматов — тот же ответ, что уходит фронту с /api/speakers (core/frame.py).
    formats = json.dumps({k: list(v) for k, v in frame.FORMATS.items()}, ensure_ascii=False)
    with open(STYLES_JS, "r", encoding="utf-8") as f:
        styles_src = f.read()
    with open(os.path.join(ROOT, "static", "app", "87-camframe.js"), "r",
              encoding="utf-8") as f:
        cam_src = f.read()
    code = (_node_prelude()
            + "".join(_func(styles_src, n) for n in
                      ("stGeoTable", "stScaleFactor", "stScaleGeo", "stAxisKey", "stScaleKind"))
            + "".join(_func(cam_src, n) for n in
                      ("camFrameSpeaker", "camFrameProfile", "camFrameWH", "camFrameAxisK"))
            + "\nvar STSCHEMA={geo:%s,geo_axis:%s};\nvar SPKFORMATS=%s;\n"
              % (geo, axis, formats)
            + "var SPEAKERS={v:{label:'вертикаль',format:'9:16'},s:{format:'1:1'},"
              "f:{format:'4:5'},w:{format:'16:9'},n:{label:'без формата'}};\n"
              "var CLIPS={0:{job:{speaker:'v'}},1:{job:{speaker:'s'}},2:{job:{speaker:'f'}},"
              "3:{job:{speaker:'w'}},4:{job:{speaker:'n'}},5:{job:{}}};\n"
              "var cur=0;\n"
              "// Клип открытого превью в стенде — сам CLIPS (боевой camFrameClip ищет его\n"
              "// по IPV.xml через clipByXml; здесь эта цепочка не проверяется).\n"
              "function camFrameClip(){return CLIPS[cur];}\n"
              "// Дверь спикера клипа (40-queue.js): формат кадра берётся у ЕГО спикера.\n"
              "function clipSpeaker(c){return (c&&c.job&&c.job.speaker)||'';}\n"
              "var out={};for(var k=0;k<6;k++){cur=k;"
              "out[k]=[camFrameWH(),camFrameAxisK()];}\n"
              "console.log(JSON.stringify(out));")
    out = _run_node(code)
    assert out["0"][1] == {"x": 1, "y": 1}, out["0"]          # 9:16 — база, множители 1
    assert out["1"][0] == [1080, 1080]
    assert out["1"][1]["x"] == pytest.approx(1.0)
    assert out["1"][1]["y"] == pytest.approx(0.5625), out["1"]
    assert out["2"][1]["y"] == pytest.approx(1350 / 1920), out["2"]
    assert out["3"][0] == [1920, 1080]
    assert out["3"][1]["x"] == pytest.approx(1920 / 1080), out["3"]
    assert out["3"][1]["y"] == pytest.approx(0.5625), out["3"]
    # профиля нет / формат не задан — прежняя вертикаль, а не отказ
    for key in ("4", "5"):
        assert out[key][0] == [1080, 1920], out[key]
        assert out[key][1] == {"x": 1, "y": 1}, out[key]


@node
def test_frontend_drag_round_trip_keeps_screen_distance():
    """Сдвиг туда и обратно: сколько пикселей кадра протянули, столько и нарисовалось.

    Драг делит экранные px на множитель оси (пишет базовые единицы), показ умножает
    обратно — на 4:5 при протяжке 100 px кадра в базе 142.22, на экране снова 100.
    """
    geo = json.dumps(style_schema.schema()["geo"], ensure_ascii=False)
    with open(STYLES_JS, "r", encoding="utf-8") as f:
        src = f.read()
    code = (_node_prelude()
            + "".join(_func(src, n) for n in
                      ("stGeoTable", "stScaleFactor", "stScaleGeo", "stScaleRound",
                       "stScaleStyle", "stUnscaleGeo"))
            + "\nvar STSCHEMA={geo:%s};\n" % geo
            + "var k=stUnscaleGeo(100,'y',1080,1350);"      # экранные px -> базовые
              "console.log(JSON.stringify([k,stScaleGeo('intro_y',1080,1350)*k]));")
    out = _run_node(code)
    assert out[0] == pytest.approx(142.22, abs=0.01), out
    assert out[1] == pytest.approx(100.0, abs=0.01), out


# --------------------------------------------------------------------------- #
# 4. Формат у спикера: план, XML и .jsx
# --------------------------------------------------------------------------- #
@pytest.fixture()
def speaker_dir(tmp_path, monkeypatch):
    """Своя папка спикеров: боевая `speakers/` — личные профили владельца."""
    d = tmp_path / "speakers"
    d.mkdir()
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(d))
    return d


def _cam(tmp_path, name="cam1.mp4"):
    p = tmp_path / name
    p.write_bytes(b"video")
    return str(p)


def _probe(w, h, rot=0):
    return {"dur_s": 60.0, "width": w, "height": h, "timecode": "01:00:00:00",
            "fps": 25.0, "rotation": rot}


def _build_xml(tmp_path, monkeypatch, speaker=None, name="clip.xml"):
    """XML тем же путём, что в бою: сайдкар со спикером рядом + xmlbuild.build."""
    cams = [_cam(tmp_path, "cam1.mp4"), _cam(tmp_path, "cam2.mp4")]
    out = tmp_path / name
    proj = {"cams": cams, "offsets": [0.0, 0.0], "fps": 60, "keep": [[0.0, 2.0]]}
    if speaker:
        proj["speaker"] = speaker
    write_project(str(tmp_path / (name.replace(".xml", "") + ".project.json")), proj)  # type: ignore[arg-type]
    monkeypatch.setattr(xmlbuild, "probe", lambda p, **k: _probe(1920, 1080))
    xmlbuild.build(cams, [(0.0, 2.0)], [0.0, 0.0], str(out), assign=[0])
    return str(out)


def test_plan_uses_scaled_style_in_square(tmp_path, monkeypatch, speaker_dir):
    """Спикер 1:1, XML 1080×1920 -> XML пересобран, план 1080×1080, числа стиля сжаты."""
    speakers.save("Квадрат", {"format": "1:1"})
    xml = _build_xml(tmp_path, monkeypatch, speaker="Квадрат")
    st = _style_with_numbers()
    st.update(caption=True)
    plan = xml2ae.scene_plan(xml, caption="тест", inserts=[], style=st,
                             emit=lambda *a, **k: None)
    assert (plan["w"], plan["h"]) == (1080, 1080), "план собран не в кадре спикера"
    assert plan["caption"]["size"] == pytest.approx(26.0), "size в квадрате не меняется"
    assert plan["caption"]["x"] == pytest.approx(55.5), "x в квадрате не меняется"
    assert plan["caption"]["y"] == pytest.approx(styles.BASE["caption_y"] * 0.5625), \
        "y подписи не сжат под квадрат"
    assert plan["caption"]["bg_round"] == pytest.approx(styles.BASE["caption_bg_round"]), \
        "size-поле не должно меняться в квадрате: короткая сторона та же (1080)"
    # XML действительно пересобран: в файле секвенция 1080×1080
    text = open(xml, encoding="utf-8").read()
    assert "<width>1080</width><height>1080</height>" in text, \
        "XML остался в старом кадре — Premiere и AE разъедутся по формату"


def test_xml_rebuilt_only_on_mismatch(tmp_path, monkeypatch, speaker_dir):
    """XML, уже собранный в кадре спикера, не переписывается: лишней работы нет."""
    speakers.save("Квадрат", {"format": "1:1"})
    xml = _build_xml(tmp_path, monkeypatch, speaker="Квадрат")
    full = os.path.splitext(xml)[0] + ".project.json"
    before_xml = os.path.getmtime(xml)
    before_proj = os.path.getmtime(full)
    assert frame.ensure_frame(xml) == (1080, 1080)
    assert os.path.getmtime(xml) == before_xml, "XML переписан без надобности"
    assert os.path.getmtime(full) == before_proj, "сайдкар переписан без надобности"


def test_ensure_frame_without_speaker_keeps_xml_frame(tmp_path, monkeypatch, speaker_dir):
    """Спикера нет — кадр задаёт сам XML: сборка без профиля работает как раньше."""
    xml = _build_xml(tmp_path, monkeypatch)
    assert frame.ensure_frame(xml) == (1080, 1920)
    text = open(xml, encoding="utf-8").read()
    assert "<width>1080</width><height>1920</height>" in text


def test_unbuilt_xml_does_not_block_the_speaker_frame(tmp_path, monkeypatch, speaker_dir):
    """Пересобрать XML не удалось — ролик всё равно собирается в кадре спикера.

    Так выглядит смена формата у уже нарезанного клипа, когда исходников под рукой
    нет: XML остался вертикальным, а ролик заказан квадратным. Ронять сборку из-за
    этого нельзя — .jsx и превью берут кадр спикера, а о расхождении сказано в логе.
    """
    speakers.save("Вертикаль", {"format": "9:16"})
    speakers.save("Квадрат", {"format": "1:1"})
    xml = _build_xml(tmp_path, monkeypatch, speaker="Вертикаль", name="sq.xml")
    # формат спикера сменили ПОСЛЕ нарезки: в XML вертикаль, у спикера — квадрат
    proj = str(tmp_path / "sq.project.json")
    data = json.load(open(proj, encoding="utf-8"))
    data["speaker"] = "Квадрат"
    write_project(proj, data)  # type: ignore[arg-type]
    before = open(xml, encoding="utf-8").read()
    assert "<width>1080</width><height>1920</height>" in before
    # пересборка падает (нет исходника) — XML обязан остаться нетронутым
    monkeypatch.setattr(xmlbuild, "build",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("исходник пропал")))
    logs = []
    assert frame.ensure_frame(xml, emit=logs.append) == (1080, 1080)
    assert open(xml, encoding="utf-8").read() == before, "битая пересборка переписала XML"
    assert any("пересобрать не удалось" in s for s in logs), logs
    plan = xml2ae.scene_plan(xml, inserts=[], style=_style_with_numbers(),
                             emit=lambda *a, **k: None)
    assert (plan["w"], plan["h"]) == (1080, 1080), "план уехал в кадр несобранного XML"


def test_jsx_subtitle_y_scales_in_square(tmp_path, monkeypatch, speaker_dir):
    """Тест .jsx в 1:1: Y субтитров в .jsx = базовый × 0.5625.

    `posy` плана — это `sub_y × высота кадра` (plan_subs), и то же число уезжает
    подстановкой в шаблон: субтитры стоят внутри квадратного кадра, а не за краем.
    """
    speakers.save("Квадрат", {"format": "1:1"})
    xml = _build_xml(tmp_path, monkeypatch, speaker="Квадрат")
    st = _style_with_numbers()
    plan = xml2ae.scene_plan(xml, inserts=[], style=st, emit=lambda *a, **k: None)
    jsx, _nc, _ns = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / "sq.jsx"),
                                      style=st, emit=lambda *a, **k: None)
    js = open(jsx, encoding="utf-8-sig").read()
    want = round(st["sub_y"] * 1080)
    assert plan["posy"] == want, f"posy плана {plan['posy']}, ждали {want}"
    m = re.search(r"var FONT_SIZE = \d+, FILL = \[[^\]]*\], POSY = (\d+);", js)
    assert m, "в .jsx нет строки POSY"
    assert int(m.group(1)) == want, f"POSY в .jsx {m.group(1)}, ждали {want}"
    # и то же самое в вертикали — базовая высота, тест различает форматы, а не «что-то»
    xml_v = _build_xml(tmp_path, monkeypatch, name="vert.xml")
    plan_v = xml2ae.scene_plan(xml_v, inserts=[], style=st, emit=lambda *a, **k: None)
    assert plan_v["posy"] == round(st["sub_y"] * 1920), "9:16 перестал быть базовым кадром"


def test_speaker_format_route_lists_formats(client):
    """Роут спикеров отдаёт форматы из core/frame.py: списка форматов в JS нет."""
    res = client.get("/api/speakers", headers=H)
    assert res.status_code == 200
    d = res.get_json()
    assert d["ok"], d
    assert {k: tuple(v) for k, v in d["formats"].items()} == frame.FORMATS, \
        "форматы разъехались с core/frame.py"
    assert d["formats"]["1:1"] == [1080, 1080]
    assert d["default_format"] == frame.DEFAULT


# --------------------------------------------------------------------------- #
# 5. Порядок форматов в списке редактора профиля
# --------------------------------------------------------------------------- #
def test_speaker_format_route_gives_format_order(client):
    """Роут отдаёт порядок форматов отдельным полем: у словаря JSON порядка нет.

    `formats` — словарь (по нему берут размеры кадра), и Flask сортирует его ключи
    по алфавиту: список «Формат» в редакторе профиля шёл бы 16:9, 1:1, 4:5, 9:16 —
    формат по умолчанию последним. Порядок — это ключи `core/frame.py` по порядку.
    """
    d = client.get("/api/speakers", headers=H).get_json()
    assert d.get("format_order") == list(frame.FORMATS), \
        f"порядок форматов разъехался с core/frame.py: {d.get('format_order')}"
    assert d["format_order"][0] == frame.DEFAULT, "формат по умолчанию не первый"
    assert set(d["format_order"]) == set(d["formats"]), \
        "format_order и formats разошлись по составу"


@node
def test_frontend_format_options_follow_backend_order(client):
    """Пункты «Формат» идут в порядке core/frame.py, а не по алфавиту ключей JSON.

    Гоняется РЕАЛЬНЫЙ `spkFormatFill` из отгружаемого файла. Словарь форматов в
    стенде — алфавитный: ровно так его отдаёт Flask, и по `Object.keys` первым
    встал бы 16:9. Порядок берётся из ответа роута, а не переписан в тесте: копия
    разошлась бы с бэкендом молча. Второй прогон — без поля (старый сервер):
    список обязан строиться как раньше, по ключам словаря.
    """
    d = client.get("/api/speakers", headers=H).get_json()
    order = json.dumps(d["format_order"], ensure_ascii=False)
    formats = json.dumps({k: list(v) for k, v in sorted(frame.FORMATS.items())},
                         ensure_ascii=False)
    with open(STYLES_JS, "r", encoding="utf-8") as f:
        src = f.read()
    code = (_func(src, "spkFormatFill")
            + "\nfunction El(tag){this.tag=tag;this.kids=[];this.value='';this.textContent='';}\n"
              "El.prototype.appendChild=function(o){this.kids.push(o);return o;};\n"
              "var SEL=new El('select');\n"
              "Object.defineProperty(SEL,'innerHTML',{set:function(v){if(!v)this.kids=[];},"
              "get:function(){return '';}});\n"
              "var document={createElement:function(tag){return new El(tag);}};\n"
              "function $(id){return id==='spk_format'?SEL:null;}\n"
              "function t(s){return s;}\n"
              "var SPKFORMATS=%s;\nvar SPKDEF_FORMAT='9:16';\n" % formats
            + "function vals(){spkFormatFill();"
              "return SEL.kids.map(function(o){return o.value;});}\n"
              "var SPKFORMAT_ORDER=%s;\n" % order
            + "var withOrder=vals();\n"
              "SPKFORMAT_ORDER=[];\n"     # поля нет (старый сервер) — прежнее поведение
              "var withoutOrder=vals();\n"
              "console.log(JSON.stringify([withOrder,withoutOrder]));")
    out = _run_node(code)
    assert out[0] == list(frame.FORMATS), \
        f"пункты формата идут не в порядке core/frame.py: {out[0]}"
    assert out[0][0] == frame.DEFAULT, "первым пунктом должен быть формат по умолчанию"
    assert out[1] == sorted(frame.FORMATS), \
        f"без format_order список должен строиться как раньше, по ключам словаря: {out[1]}"
