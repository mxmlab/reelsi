# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Положения: «+ = вниз» у всех ручек и точка покоя фотовставок «Кам 2» на кадре Камеры 1.

1. Высота субтитров была «% снизу» (инверсия, «+ = вверх») — стала «Положение субтитров по Y,
   % кадра» (от верха, «+ = вниз», как у остальных ручек). Хранимое sub_y не меняется: это та же
   доля от верха, меняется только показ (`stConv.top_pct`).
2. У фотовставок стиля «Кам 2» была одна точка покоя (insert_c2_x/y), и она двигала и вставки,
   попавшие на кадр Камеры 1. Теперь на кадре Камеры 1 своя точка — `insert_c2on1_x/y`; пусто —
   как у «Кам 2». Сдвиг = разность точек, он прибавляется к x/y ДО анимаций, шаблон .jsx не меняется.
"""
import gzip
import os
import shutil
import sys

import pytest
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from core import style_schema, styles, xml2ae  # noqa: E402
from core.xml2ae.plan_style import read_style  # noqa: E402
from test_style_panel_js import _run_node, node  # noqa: E402


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


@pytest.fixture()
def photo(tmp_path):
    p = tmp_path / "photo.png"
    Image.new("RGB", (640, 480), (200, 60, 60)).save(str(p))
    return str(p)


@pytest.fixture(autouse=True)
def _isolate_censor(monkeypatch):
    """Детерминизм сборки: цензура читает поставочные списки, а не личный badwords.user.txt."""
    from core import censor
    monkeypatch.setattr(censor, "USER_PATHS", {"bad": "", "ok": ""})
    monkeypatch.setattr(censor, "_cache", {"bad": (None, None, censor.DEFAULT_BAD),
                                           "ok": (None, None, censor.DEFAULT_OK)})


def _plan(xml, photo, style, start_s):
    """План с одной фотовставкой; стиль фото — из `insert_style` (стиль «Кам 2» задан явно)."""
    ins = {"type": "photo", "media": photo, "start_s": start_s, "dur_s": 1.5}
    return xml2ae.scene_plan(xml, inserts=[ins], style=dict(style), disclaimer="",
                             intro_riser=False, emit=lambda *a, **k: None)


# ---------------------------------------------------------------------------
# 1. Субтитры: «+ = вниз», от верха
# ---------------------------------------------------------------------------

TOP_PCT = r"""
const out = [
  stConv.top_pct.toView(0.5964), stConv.top_pct.toStore(59.6),
  stConv.top_pct.toView(0.6), stConv.top_pct.toStore(60),
  stConv.top_pct.toStore(200), stConv.top_pct.toStore(0),
  stConv.top_pct.toStore('abc'), stConv.top_pct.toStore(10),
  stConv.top_pct.toView(null)
];
console.log(JSON.stringify(out));
"""


@node
def test_top_pct_conv_is_top_based_and_round_trips_default(tmp_path):
    """stConv.top_pct (боевой 94-stylepanel.js под node): 0.5964 ↔ 59.6, зажим 2…98 %,
    нечисло — дефолт, пустое значение — дефолт вида."""
    res = _run_node(tmp_path, "top_pct.js", TOP_PCT)
    assert res[0] == 59.6 and res[1] == 0.5964, "дефолт не сходится туда и обратно"
    assert res[2] == 60 and res[3] == 0.6, "проценты от верха, без инверсии"
    assert res[4] == 0.98 and res[5] == 0.02, "зажим: 2…98 %"
    assert res[6] == 0.5964, "нечисло — дефолт"
    assert res[7] == 0.1, "10 % от верха — нижний предел поля"
    assert res[8] == 59.6, "пусто в стиле показывает дефолт"


def test_sub_y_field_is_top_based_with_schema_range():
    field = next(f for L in style_schema.LAYERS for f in _fields(L.get("items", []))
                 if f.get("key") == "sub_y")
    assert field["conv"] == "top_pct"
    assert field["label"] == "Положение субтитров по Y, % кадра"
    assert (field["min"], field["max"], field["step"]) == (10, 98, 0.5)
    assert styles.BASE["sub_y"] == 0.5964, "хранимое значение не меняется"


def _fields(items):
    for it in items:
        if it.get("type") == "group":
            yield from _fields(it.get("items", []))
        elif it.get("type") == "field":
            yield it


# ---------------------------------------------------------------------------
# 2. Стиль: точка «Кам 2» на кадре Камеры 1 — пара к insert_c2_x/y
# ---------------------------------------------------------------------------

def test_style_c2on1_defaults_follow_c2():
    st = read_style(styles.resolve({}))
    assert st.insert_c2on1_x == st.insert_c2_x == 0.5
    assert st.insert_c2on1_y == st.insert_c2_y == 0.172
    assert st.insert_c2on1_set is False


def test_style_c2on1_set_when_any_knob_set():
    st = read_style(styles.resolve({"insert_c2on1_y": 0.3}))
    assert st.insert_c2on1_set is True
    assert st.insert_c2on1_x == 0.5, "X без своего значения — точка «Кам 2»"
    assert st.insert_c2on1_y == 0.3


def test_schema_c2on1_fields_pair_and_show_if():
    fields = {f["key"]: f for L in style_schema.LAYERS for f in _fields(L.get("items", []))}
    for suf, base in (("x", "insert_c2_x"), ("y", "insert_c2_y")):
        f = fields[f"insert_c2on1_{suf}"]
        assert f["conv"] == "frac_pct" and f["nullable"] is True
        assert f["fallback_key"] == base
        assert f["show_if"] == {"key": "insert_style", "eq": "cam2"}
    assert fields["insert_c2_x"]["label"] == "Вставки «Кам 2» X, %"
    assert "только на кадре камеры 2" in fields["insert_c2_x"]["tip"]


# ---------------------------------------------------------------------------
# 3. План: сдвиг только на кадре Камеры 1, и только при заданной ручке
# ---------------------------------------------------------------------------

def test_cam1_frame_gets_point_difference(xml_subs, photo):
    plain = _plan(xml_subs, photo, {"insert_style": "cam2"}, 1.0)
    own = _plan(xml_subs, photo, {"insert_style": "cam2", "insert_c2on1_y": 0.3}, 1.0)
    a, b = plain["inserts"][0], own["inserts"][0]
    H = own["h"]
    dy = round(H * (0.3 - styles.BASE["insert_c2_y"]))
    assert b["c2d"] == [0, dy], f"сдвиг {b.get('c2d')}, ждали [0, {dy}]"
    assert b["y"] == pytest.approx(a["y"] + dy), "сдвиг прибавлен к y плана"
    assert b["x"] == a["x"]


def test_cam2_frame_is_untouched(xml_subs, photo):
    """Фото «Кам 2» на перебивке камеры 2 — точка «Кам 2», как и была."""
    plain = _plan(xml_subs, photo, {"insert_style": "cam2"}, 8.0)
    own = _plan(xml_subs, photo, {"insert_style": "cam2", "insert_c2on1_y": 0.3,
                                  "insert_c2on1_x": 0.4}, 8.0)
    a, b = plain["inserts"][0], own["inserts"][0]
    assert "c2d" not in b
    assert b["x"] == a["x"] and b["y"] == a["y"]


def test_no_knobs_plan_is_identical(xml_subs, photo):
    """Ручек нет (или пусты) — план побайтово тот же, что без них."""
    a = _plan(xml_subs, photo, {"insert_style": "cam2"}, 1.0)["inserts"]
    b = _plan(xml_subs, photo, {"insert_style": "cam2", "insert_c2on1_x": None,
                                "insert_c2on1_y": None}, 1.0)["inserts"]
    assert a == b
    assert all("c2d" not in x for x in a)


def test_both_axes_and_shift_before_rise_animation(xml_subs, photo):
    """X и Y заданы; подъём (rise) берёт сдвиг через x/y плана — позиция конца подъёма
    смещена ровно на dx, а не только в превью."""
    base = {"insert_style": "cam2", "insert_anim": "rise"}
    plan_a = _plan(xml_subs, photo, base, 1.0)
    a = plan_a["inserts"][0]
    b = _plan(xml_subs, photo, dict(base, insert_c2on1_x=0.4, insert_c2on1_y=0.3), 1.0)["inserts"][0]
    W, H = plan_a["w"], plan_a["h"]
    dx = round(W * (0.4 - styles.BASE["insert_c2_x"]))
    dy = round(H * (0.3 - styles.BASE["insert_c2_y"]))
    assert b["c2d"] == [dx, dy]
    assert b["x"] == pytest.approx(a["x"] + dx) and b["y"] == pytest.approx(a["y"] + dy)
    pa = a["anim"]["position"][-1][1]
    pb = b["anim"]["position"][-1][1]
    assert pb[0] - pa[0] == pytest.approx(dx, abs=1), "подъём не взял сдвиг по X"
    assert pb[1] - pa[1] == pytest.approx(dy, abs=1), "подъём не взял сдвиг по Y"
