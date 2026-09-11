# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Панорама полноэкранной видеовставки: предпросмотр и .jsx должны считать ОДНО.

Баг, ради которого тест: ролик 16:9, заполняющий кадр 1080×1920, вылезает по ширине
на ~1160 px в каждую сторону, а по высоте — ни на сколько. Сдвиг за эту границу
показывает не кадр, а пустоту. Ограничение написано ДВАЖДЫ и независимо: `fillSlack`
в xml2ae/layout.py (Python, геометрия переехала сюда из шаблона) и `insVideoPan`
в static/app/. Пока они считают одинаково, «что вижу в предпросмотре, то и
в After Effects»; разъедутся молча — пользователь узнает об этом только рендером.

Здесь гоняются НЕ копии формул, а сам отгружаемый код: JS-функция вырезается из
боевого файла и исполняется node'ом, Python-функция берётся из layout напрямую.
На одних и тех же входах.

Запуск: python -m pytest reelsi/tests -q
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

from core import xml2ae  # noqa: E402
from core.xml2ae.layout import _fill_slack, _fit_scale  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("node"),
                                reason="сверка двух реализаций требует node в PATH")

# кадр и набор пропорций: ландшафт, вертикаль, 4K, квадрат, уже кадра
W, H = 1080, 1920
SIZES = [(1920, 1080), (3840, 2160), (1080, 1920), (1000, 1000), (720, 1280), (640, 480)]


def _func(src, name):
    """Вырезать `function name(...){...}` целиком по балансу скобок."""
    m = re.search(r"function\s+%s\s*\(" % re.escape(name), src)
    assert m, f"в исходнике не нашлась функция {name} — её переименовали или удалили"
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


def _run_node(script):
    p = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr.strip()[:400]
    return json.loads(p.stdout)


@pytest.fixture(scope="module")
def app_js():
    """insVideoPan/insVideoFill — JS-предпросмотр вставок, вырезанные из боевого файла."""
    from core import app_meta
    pan = _func(app_meta.app_js_text(), "insVideoPan")
    fill = _func(app_meta.app_js_text(), "insVideoFill")
    script = (
        "var W=1080,H=1920;\n%s\n%s\n"
        "var out=%s.map(function(s){\n"
        "  var a=insVideoPan(s[0],s[1],0,0);\n"
        "  var far=insVideoPan(s[0],s[1],99999,-99999);\n"
        "  var fv=insVideoFill(s[0],s[1],100);\n"
        "  return {app:[a.sx,a.sy], far:[far.x,far.y], fill:fv.w/s[0]*100};});\n"
        "console.log(JSON.stringify(out));"
    ) % (pan, fill, json.dumps([list(s) for s in SIZES]))
    return _run_node(script)


def test_slack_matches_between_jsx_and_preview(app_js):
    """Главное: запас на панораму у layout.py и у предпросмотра совпадает."""
    for size, r in zip(SIZES, app_js):
        py = list(_fill_slack(size[0], size[1], W, H, 1.0))
        assert py == r["app"], f"{size[0]}x{size[1]}: layout {py} != предпросмотр {r['app']}"


def test_fit_matches_between_jsx_and_preview(app_js):
    """Масштаб заполнения кадра (уходит в .jsx как ins.fit) — тоже как в предпросмотре."""
    for size, r in zip(SIZES, app_js):
        py = _fit_scale(size[0], size[1], True, W, H, 1.0)
        assert abs(py - r["fill"]) < 1e-6, f"{size[0]}x{size[1]}: layout {py} != предпросмотр {r['fill']}"


def test_landscape_pans_wide_and_not_at_all_vertically(app_js):
    """16:9 в вертикальном кадре: по ширине есть что показывать, по высоте — нечего."""
    r = app_js[SIZES.index((1920, 1080))]
    assert r["app"][0] > 1000, "ландшафтный ролик обязан иметь запас по X"
    assert r["app"][1] == 0, "по высоте запаса нет — иначе в кадр въедет пустота"


def test_vertical_source_has_no_slack_at_all(app_js):
    """Ролик ровно по кадру двигать некуда ни по одной оси."""
    r = app_js[SIZES.index((1080, 1920))]
    assert r["app"] == [0, 0]


def test_far_drag_is_clamped_to_slack(app_js):
    """Утянутый в бесконечность ползунок упирается в границу, а не улетает за кадр."""
    for size, r in zip(SIZES, app_js):
        assert r["far"] == [r["app"][0], -r["app"][1]], f"{size[0]}x{size[1]}: {r['far']}"


def test_video_start_uses_clamped_sin():
    """`файл с` (sin) не должен уводить outPoint за конец исходника: AE ругается и
    вставки в проекте не будет. Раньше startTime считался от сырого ins.sin —
    контракт держим тем, что в шаблоне остаётся именно ограниченная переменная."""
    src = xml2ae.AE_FULL
    assert "vl.startTime=t0-vsin;" in src, "startTime снова считается от неограниченного sin"
    assert re.search(r"vsin\s*=\s*Math\.min\(\s*vsin\s*,\s*Math\.max\(0,\s*vdur", src), \
        "пропал зажим sin по длительности исходного файла"
