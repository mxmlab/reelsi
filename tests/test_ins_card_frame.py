# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Пиксельные константы раскладки — под кадр ролика, а не под кадр 1080.

Карточка фотовставки (`INS_CARD_W/H`, `INS_CARD_H_CAM2`), размытие входа cam2-вставки
(`INS_BLUR`), выезд снизу (`INS_RISE_DY`), вылет Камеры 1 (`INS_C1_LOW/HIGH`) и
затемнение интро (`SHADE_*`) записаны в пикселях кадра шириной 1080. Под кадр ролика
их множит ОДНО правило — `layout._px_k`, то же, что у вида «size» стиля
(`core/style_geometry.scale_for`): `min(W, H)/1080`. Стерегутся три вещи:

1. план: в 2160×3840 карточка ровно вдвое больше, чем в 1080×1920, а в 1080×1080 —
   такая же, как в 1080×1920 (короткая сторона та же). Тем же правилом растут блюр,
   выезд и вылет вставки;
2. затемнение интро: короткая сторона кадра, а не прежнее `k = W/1080` — в 16:9
   (1920×1080) затемнение остаётся прежним, а не вырастает в 1.78 при неизменном
   тексте интро;
3. превью: `insPreviewBox` берёт коробку из плана (поле `ins_box`) — своей копии
   чисел (1030/528/2.2) у него нет. Гоняется РЕАЛЬНАЯ функция отгружаемого
   `static/app/85-inserts-view.js` (node), копии формулы в тесте нет.

Ролик — та же фикстура нарезки, что у соседних тестов геометрии; меняются только
размеры секвенции в XML (кадр плана берётся из XML).

Запуск: py -3.10 -m pytest tests/test_ins_card_frame.py -q
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

from PIL import Image  # noqa: E402
from core import styles, xml2ae  # noqa: E402
from core.xml2ae.layout import (INS_BLUR, INS_C1_HIGH, INS_C1_LOW, INS_CARD_H,  # noqa: E402
                                INS_CARD_H_CAM2, INS_CARD_W, INS_MASK_SQUARE_AR,
                                INS_RISE_DY, SHADE_BLUR, SHADE_DY, SHADE_H, SHADE_OX,
                                SHADE_OY, SHADE_SCALE, SHADE_W, SHADE_X)

JS = os.path.join(ROOT, "static", "app", "85-inserts-view.js")
node = pytest.mark.skipif(not shutil.which("node"),
                          reason="контракт фронта требует node в PATH")


@pytest.fixture(autouse=True)
def _isolate_censor(monkeypatch):
    """Детерминизм сборки: цензура читает поставочные списки, а не личный badwords.user.txt."""
    from core import censor
    monkeypatch.setattr(censor, "USER_PATHS", {"bad": "", "ok": ""})
    monkeypatch.setattr(censor, "_cache", {"bad": (None, None, censor.DEFAULT_BAD),
                                           "ok": (None, None, censor.DEFAULT_OK)})


def _xml(tmp_path, w, h, name):
    """Ролик фикстуры в кадре w×h: та же нарезка, другие размеры секвенции."""
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g:
        text = g.read().decode("utf-8")
    text = text.replace("<width>1080</width>", "<width>%d</width>" % w)
    text = text.replace("<height>1920</height>", "<height>%d</height>" % h)
    dst = str(tmp_path / name)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(text)
    return dst


def _png(path, w, h):
    Image.new("RGB", (int(w), int(h)), (200, 60, 60)).save(str(path))
    return str(path)


@pytest.fixture()
def photo(tmp_path):
    """Фото 2000×500 (4:1, ультравайд): видимая часть упирается в ширину кадра, поэтому
    карточка меряется ровно по `INS_CARD_W` — так множитель виден без маски-квадрата."""
    return _png(tmp_path / "photo.png", 2000, 500)


def _plan(xml, photo, style=None, start_s=8.0, dur_s=1.5):
    """План сцены с одной фотовставкой в этом кадре.

    start_s=8.0 — перебивка фикстуры: стиль фото «auto» выберет cam2 (наезд с блюром),
    start_s=1.0 — Камера 1 (вылет из-за спины).
    """
    ins = {"type": "photo", "style": "cam2", "media": photo,
           "start_s": start_s, "dur_s": dur_s}
    return xml2ae.scene_plan(xml, inserts=[ins], style=dict(style or {}),
                             disclaimer="", intro_riser=False, emit=lambda *a, **k: None)


# --------------------------------------------------------------------------- #
# 1. Карточка и анимация вставки: одно правило на весь кадр
# --------------------------------------------------------------------------- #
def test_card_doubles_in_a_4k_frame(tmp_path, photo):
    """4K-кадр 2160×3840: карточка ровно вдвое больше, чем в 1080×1920.

    Без множителя `_px_k` коробка карточки оставалась в пикселях 1080 и вчетверо
    больший кадр получал вдвое мельче задуманного карточку (и вдвое мельче её
    относительно кадра).
    """
    a = _plan(_xml(tmp_path, 1080, 1920, "v.xml"), photo)["inserts"][0]
    b = _plan(_xml(tmp_path, 2160, 3840, "k.xml"), photo)["inserts"][0]

    assert a["style"] == b["style"] == "cam2", "вставка уехала на другую камеру"
    assert a["card"]["w"] == pytest.approx(INS_CARD_W, abs=0.01), \
        f"в базовом кадре карточка не по INS_CARD_W: {a['card']}"
    assert b["card"]["w"] == pytest.approx(a["card"]["w"] * 2, abs=0.02), \
        f"ширина карточки не удвоилась: {a['card']} -> {b['card']}"
    assert b["card"]["h"] == pytest.approx(a["card"]["h"] * 2, abs=0.02), \
        f"высота карточки не удвоилась: {a['card']} -> {b['card']}"
    assert b["card"]["pw"] == pytest.approx(a["card"]["pw"] * 2, abs=0.02), \
        "фото в прекомпе не удвоилось"
    # Масштаб слоя (%) тот же: фото тянется под ширину композа, а композ вдвое шире —
    # без множителя он падал бы вдвое (47.7 вместо 95.4).
    assert b["scale"] == pytest.approx(a["scale"]), \
        f"масштаб слоя вставки разошёлся: {a['scale']} -> {b['scale']}"


def test_card_is_the_same_in_a_square_frame(tmp_path, photo):
    """1080×1080: карточка та же, что в 1080×1920 — короткая сторона кадра одна и та же.

    Правило `min(W, H)/1080` от пропорций не зависит: квадрат — не «мельче», а другой
    формат того же кадра 1080. Разошлись бы — правило было бы не одним.
    """
    v = _plan(_xml(tmp_path, 1080, 1920, "v.xml"), photo)["inserts"][0]
    sq = _plan(_xml(tmp_path, 1080, 1080, "sq.xml"), photo)["inserts"][0]
    assert sq["card"] == v["card"], f"карточка в квадрате не та: {v['card']} -> {sq['card']}"
    assert sq["scale"] == v["scale"], "масштаб слоя в квадрате не тот"


def test_cam2_blur_scales(tmp_path, photo):
    """Размытие входа cam2-вставки — `INS_BLUR` пикселей кадра 1080: в 4K вдвое больше."""
    a = _plan(_xml(tmp_path, 1080, 1920, "v.xml"), photo)["inserts"][0]
    b = _plan(_xml(tmp_path, 2160, 3840, "k.xml"), photo)["inserts"][0]
    blurs = [k[1] for k in a["anim"]["blur"]]
    assert max(blurs) == pytest.approx(INS_BLUR), f"блюр входа не по INS_BLUR: {a['anim']}"
    assert max(k[1] for k in b["anim"]["blur"]) == pytest.approx(INS_BLUR * 2), \
        f"блюр в 4K не удвоился: {b['anim']['blur']}"


def test_rise_offset_scales(tmp_path, photo):
    """Выезд снизу («rise»): подъём `INS_RISE_DY` — тоже пиксели кадра 1080.

    Точка покоя кам2 — доля высоты кадра (`insert_c2_y`), она растёт вместе с кадром
    сама; подъём от неё — константа, и без множителя в 4K вставка выезжала бы вдвое
    меньший путь.
    """
    c2y = styles.BASE["insert_c2_y"]
    for w, h, name, k in ((1080, 1920, "v.xml", 1), (2160, 3840, "k.xml", 2)):
        got = _plan(_xml(tmp_path, w, h, name), photo,
                    style={"insert_anim": "rise"})["inserts"][0]
        start_y = got["anim"]["position"][0][1][1]
        rest_y = round(h * c2y)
        assert start_y - rest_y == pytest.approx(INS_RISE_DY * k, abs=0.01), \
            f"кадр {w}×{h}: подъём вставки {start_y - rest_y}, ждали {INS_RISE_DY * k}"


def test_cam1_flyout_scales(tmp_path, photo):
    """Вылет Камеры 1: нижняя точка старта и высота подъёма — пиксели кадра 1080."""
    a = _plan(_xml(tmp_path, 1080, 1920, "v.xml"), photo, start_s=1.0)["inserts"][0]
    b = _plan(_xml(tmp_path, 2160, 3840, "k.xml"), photo, start_s=1.0)["inserts"][0]
    assert a["style"] == b["style"] == "cam1", "вставка уехала на перебивку"
    pa, pb = a["anim"]["position"], b["anim"]["position"]
    assert [k[0] for k in pa] == [k[0] for k in pb], "моменты ключей разошлись"
    assert len(pa) == len(pb) == 4, f"ключей вылета не четыре: {pa}"
    for (ta, va), (tb, vb) in zip(pa, pb):
        assert vb[1] == pytest.approx(va[1] * 2, abs=0.02), \
            f"ключ {ta}: {va} -> {vb} (ждали удвоения)"
    # точки — ровно те, что заданы константами: dn за спиной, up над спиной
    assert pa[0][1][1] == pytest.approx(INS_C1_LOW)
    assert pa[1][1][1] == pytest.approx(-INS_C1_HIGH)


# --------------------------------------------------------------------------- #
# 2. Затемнение интро — то же правило (короткая сторона)
# --------------------------------------------------------------------------- #
INTRO = [dict(words=["ПЕРВОЕ"], color="white", times=[2.0])]


def _shade(tmp_path, w, h, name):
    plan = xml2ae.scene_plan(_xml(tmp_path, w, h, name), intro=INTRO,
                             style={"intro_shade": True, "intro_y": 768},
                             disclaimer="", intro_riser=False, emit=lambda *a, **k: None)
    return plan["shade"]


def test_shade_follows_the_short_side_of_the_frame(tmp_path):
    """Затемнение растёт вместе с КОРОТКОЙ стороной кадра — как карточка и числа стиля.

    Прежде у затемнения было своё `k = W/1080`: в 4K-вертикали те же ×2, а в 16:9
    (1920×1080) — ×1.78, хотя короткая сторона там 1080 и текст интро не меняется
    вовсе (вид «size» у стиля от кадра не зависит). Правило стало одним.
    """
    base = _shade(tmp_path, 1080, 1920, "v.xml")
    wide = _shade(tmp_path, 1920, 1080, "w.xml")
    four = _shade(tmp_path, 2160, 3840, "k.xml")

    assert base["w"] == SHADE_W and base["h"] == SHADE_H, base
    assert base["x"] == SHADE_X, base
    assert base["blur"] == SHADE_BLUR and base["ox"] == SHADE_OX and base["oy"] == SHADE_OY
    # 16:9: короткая сторона та же 1080 — затемнение прежнее (прежнее правило дало бы 2517)
    assert wide["w"] == SHADE_W and wide["h"] == SHADE_H, f"16:9 раздуло затемнение: {wide}"
    assert wide["x"] == SHADE_X and wide["blur"] == SHADE_BLUR and wide["ox"] == SHADE_OX
    # intro_y — вид «y»: в 16:9 он сжат под высоту кадра (768 × 1080/1920), SHADE_DY — нет
    assert wide["y"] == pytest.approx(768 * 1080 / 1920 + SHADE_DY, abs=0.01), wide["y"]
    # 4K: короткая сторона вдвое — затемнение вдвое, как и было у прежнего правила
    assert four["w"] == SHADE_W * 2 and four["h"] == SHADE_H * 2
    assert four["blur"] == SHADE_BLUR * 2 and four["x"] == SHADE_X * 2
    assert four["y"] == pytest.approx(768 * 2 + SHADE_DY * 2, abs=0.01), four["y"]
    assert four["scale"] == wide["scale"] == base["scale"] == SHADE_SCALE, \
        "масштаб слоя — процент, от кадра не зависит"


# --------------------------------------------------------------------------- #
# 3. План несёт коробку карточки — превью не держит свою копию чисел
# --------------------------------------------------------------------------- #
def test_plan_carries_the_scaled_card_box(tmp_path, photo):
    """`plan["ins_box"]` — ширина, высоты Кам1/Кам2 и отношение маски в пикселях кадра."""
    base = _plan(_xml(tmp_path, 1080, 1920, "v.xml"), photo)["ins_box"]
    four = _plan(_xml(tmp_path, 2160, 3840, "k.xml"), photo)["ins_box"]
    assert base == {"w": INS_CARD_W, "h_cam1": INS_CARD_H,
                    "h_cam2": INS_CARD_H_CAM2, "sq": INS_MASK_SQUARE_AR}, base
    assert four["w"] == pytest.approx(INS_CARD_W * 2)
    assert four["h_cam1"] == pytest.approx(INS_CARD_H * 2)
    assert four["h_cam2"] == pytest.approx(INS_CARD_H_CAM2 * 2)
    assert four["sq"] == INS_MASK_SQUARE_AR, "отношение сторон маски от кадра не зависит"
    # то же число, что в самой карточке вставки: второго расчёта нет
    card = _plan(_xml(tmp_path, 2160, 3840, "k.xml"), photo)["inserts"][0]["card"]
    assert card["w"] == pytest.approx(four["w"], abs=0.01), (card, four)


# ---- node: РЕАЛЬНЫЕ функции отгружаемого файла (копий в тесте нет) ----

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


def _js():
    with open(JS, "r", encoding="utf-8") as f:
        src = f.read()
    return "\n".join(_func(src, n) for n in ("insPreviewBox", "ipvPlanWH"))


def _run_node(code):
    p = subprocess.run(["node", "-e", code], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@node
def test_preview_takes_the_card_box_from_the_plan():
    """Коробка превью — из плана, а не из своих чисел.

    Кадру плана 1080×1920 подсунута коробка 4K-кадра (2160×3840): своя формула дала бы
    прежние 1030, а превью обязано взять 2060 из плана — иначе копия чисел вернулась.
    """
    code = _js() + """
    var IPV={plan:null};
    function look(plan){IPV.plan=plan;return insPreviewBox(2000,500,100,100,100);}
    console.log(JSON.stringify({
      fromPlan:look({w:1080,h:1920,ins_box:{w:2060,h_cam1:1120,h_cam2:990,sq:2.2}}),
      four:look({w:2160,h:3840,ins_box:{w:2060,h_cam1:1120,h_cam2:990,sq:2.2}}),
      noBox:look({w:1080,h:1920})}));
    """
    out = _run_node(code)
    assert out["fromPlan"]["w"] == pytest.approx(2060.0), \
        f"превью не взяло ширину коробки из плана: {out['fromPlan']}"
    assert out["fromPlan"]["h"] == pytest.approx(515.0), out["fromPlan"]
    assert out["four"] == out["fromPlan"], "кадр плана перебил коробку из плана"
    # поля ins_box нет (старый сервер, стенд) — прежние числа базового кадра
    assert out["noBox"]["w"] == pytest.approx(1030.0), out["noBox"]
    assert out["noBox"]["h"] == pytest.approx(257.5), out["noBox"]
