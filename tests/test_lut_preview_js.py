# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""LUT в превью: расчёт по таблице, привязка к камере и наложение в кадре.

Проверяется на node — в браузере эти места не увидеть без живого сервера и видео:

1. `lutSample` (чистая функция без WebGL) на тождественном LUT возвращает вход, а с
   доменом не 0..1 — считает по домену: второй копии формулы в шейдере быть не должно;
2. `lutPath` берёт таблицу из профиля СПИКЕРА КЛИПА и по номеру камеры с 1 — ключ
   `lut["2"]` это вторая камера, а не индекс массива;
3. нет WebGL2 — кадр идёт как есть, и в лог уходит ОДНА строка, а не по строке на кадр;
4. `ipvDrawFrame` (общая отрисовка кадра, ею же кладётся маска рото) рисует через
   `lutApply`: ни одного кадра в обход, у обеих камер.

Запуск:  python -m pytest tests/test_lut_preview_js.py -q
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("REELSI_NO_BROWSER", "1")

LUT_JS = ROOT / "static" / "app" / "86-lut.js"
VIEW_JS = ROOT / "static" / "app" / "85-inserts-view.js"
HTML = ROOT / "templates" / "index.html"

node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")

# Состояние модуля из 86-lut.js: в браузере оно живёт в общем скоупе страницы,
# в стенде его объявляем сами (как UICANCEL в test_markup_pool_js).
STATE = """
let LUTMAX=1920,LUT_GL=null,LUT_NOLOG=false,LUT_VFAIL=false;
const LOGS=[];
function uiLog(m){LOGS.push(String(m));}
function t(s){return s;}                       // переводчик страницы: на русском ключ как есть
// Дверь спикера клипа живёт в 40-queue.js: lutPath берёт спикера ТОЛЬКО у клипа
// (запасного пути на общий селектор у операции над клипом нет).
function clipSpeaker(c){return (c&&c.job&&c.job.speaker)||'';}
globalThis.document={createElement:()=>({getContext:()=>null})};   // WebGL2 в node нет
"""


def _func_src(src: str, name: str) -> str:
    """Текст функции `name` от объявления до сбалансированной закрывающей скобки."""
    m = re.search(r"function\s+%s\s*\(" % re.escape(name), src)
    assert m is not None, f"в 86-lut.js не нашлась функция {name}"
    depth = 0
    for i in range(m.start(), len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():i + 1]
    raise AssertionError(f"не нашлась закрывающая скобка функции {name}")


def _lut_src(*names: str) -> str:
    src = LUT_JS.read_text(encoding="utf-8")
    return "\n".join(_func_src(src, n) for n in names)


def _run_node(tmp_path: Path, script: str) -> Any:
    """Прогнать стенд под node и вернуть разобранный JSON с последней строки."""
    path = tmp_path / "lut_stand.js"
    path.write_text(script, encoding="utf-8")
    proc = subprocess.run(["node", str(path)], capture_output=True, text=True,
                          encoding="utf-8-sig", errors="replace", timeout=30,
                          cwd=str(ROOT))
    assert proc.returncode == 0, f"node упал: {proc.stderr or proc.stdout}"
    lines = [line.strip() for line in proc.stdout.strip().splitlines() if line.strip()]
    assert lines, "стенд ничего не напечатал"
    return json.loads(lines[-1])


# --------------------------------------------------------------------------- #
# 1-3. Расчёт по таблице, ключ камеры, отказ без WebGL2 — живые функции файла
# --------------------------------------------------------------------------- #
@node
def test_lut_stand(tmp_path: Path) -> None:
    body = """
const IDENT={size:2,domain_min:[0,0,0],domain_max:[1,1,1],data:[
 0,0,0, 1,0,0, 0,1,0, 1,1,0, 0,0,1, 1,0,1, 0,1,1, 1,1,1]};
const LOGD={size:2,domain_min:[-1,-1,-1],domain_max:[1,1,1],data:[
 -1,-1,-1, 1,-1,-1, -1,1,-1, 1,1,-1, -1,-1,1, 1,-1,1, -1,1,1, 1,1,1]};
const SWAP={size:2,domain_min:[0,0,0],domain_max:[1,1,1],data:[
 0,0,0, 0,0,1, 0,1,0, 0,1,1, 1,0,0, 1,0,1, 1,1,0, 1,1,1]};
const px=(lut,r,g,b)=>lutSample(lut,r,g,b).map(v=>Math.round(v*10000)/10000);

// Клип в превью — по IPV.xml, спикер у него свой (не выбранный в панели)
const IPV={xml:'/out/01.xml'};
const CLIPS=[{xml:'/out/01.xml',job:{speaker:'A'}},{xml:'/out/02.xml',job:{speaker:'B'}}];
function clipByXml(xml){return CLIPS.find(c=>c.xml===xml);}
const SPEAKERS={A:{lut:{'1':'C:/lut/cam1.cube','2':'C:/lut/cam2.cube'}},
                B:{lut:{'1':'C:/lut/b1.cube'}}};

// Нет WebGL2: кадр отдаём как есть, и в лог — одна строка на всё превью
const v={videoWidth:160,videoHeight:90};
function lutFor(ci){return {size:2,domain_min:[0,0,0],domain_max:[1,1,1],path:'x',
  data:[0,0,0, 1,0,0, 0,1,0, 1,1,0, 0,0,1, 1,0,1, 0,1,1, 1,1,1]};}
const sameFirst=lutApply(0,v)===v, sameAgain=lutApply(1,v)===v;
const logsAfter=LOGS.length;
const ks=lutKS(0,v);

console.log(JSON.stringify({
  ident:[px(IDENT,0,0,0),px(IDENT,1,1,1),px(IDENT,0.25,0.5,0.75),px(IDENT,0.5,0.5,0.5)],
  log:[px(LOGD,-1,-1,-1),px(LOGD,1,1,1),px(LOGD,0,0,0),px(LOGD,2,2,2)],
  swap:[px(SWAP,1,0,0),px(SWAP,0,0,1)],
  path:[lutPath(0),lutPath(1),lutPath(2)],
  noGl:[sameFirst,sameAgain,logsAfter,ks],
  logText:LOGS[0]
}));
"""
    res = _run_node(tmp_path, STATE + _lut_src("lutSample", "lutClip", "lutPath",
                                               "lutSize", "lutWH", "lutGL", "lutKS",
                                               "lutApply") + body)

    # 1. Тождественный LUT: цвет не меняется (в том числе между узлами таблицы)
    assert res["ident"] == [[0, 0, 0], [1, 1, 1], [0.25, 0.5, 0.75], [0.5, 0.5, 0.5]], res["ident"]
    # Домен не 0..1: вход растягивается по домену и обрезается по краям
    assert res["log"] == [[-1, -1, -1], [1, 1, 1], [0, 0, 0], [1, 1, 1]], res["log"]
    # Порядок данных как в файле: R меняется быстрее всех (иначе каналы поехали бы)
    assert res["swap"] == [[0, 0, 1], [1, 0, 0]], res["swap"]

    # 2. Ключ камеры — номер с 1; клип берём по IPV.xml, а не по выбранному в панели
    assert res["path"] == ["C:/lut/cam1.cube", "C:/lut/cam2.cube", ""], res["path"]

    # 3. Без WebGL2 кадр идёт как есть, и строка в логе ровно одна
    same_first, same_again, logs_after, ks = res["noGl"]
    assert same_first is True and same_again is True, "кадр не отдан как есть без WebGL2"
    assert logs_after == 1, f"в лог ушло {logs_after} строк вместо одной"
    assert ks == [1, 1], "без WebGL2 координаты вырезки масштабировать нечем"
    assert "WebGL2" in res["logText"], res["logText"]


# --------------------------------------------------------------------------- #
# 4. ipvCamPaint: оба кадра идут через lutApply
# --------------------------------------------------------------------------- #
def test_ipv_cam_paint_рисует_кадр_с_lut() -> None:
    """Кадр (Камера 1 и перебивка) идёт через lutApply — теперь в ipvDrawFrame.

    Отрисовка кадра вынесена в `ipvDrawFrame` (ею же кладётся маска рото): второй копии
    правил «вырезка, матрица, цвет» быть не должно. Мутация «вернуть drawImage(v, …)»
    обязана валить этот тест: без lutApply камера молча осталась бы без LUT, а заметить
    это можно только глазами.
    """
    src = VIEW_JS.read_text(encoding="utf-8")
    paint = _func_src(src, "ipvCamPaint")
    frame = _func_src(src, "ipvDrawFrame")
    calls = [frame[m.end():m.end() + 10] for m in re.finditer(r"drawImage\(", frame)]
    assert len(calls) == 1, f"в ipvDrawFrame ждали один drawImage, нашли {len(calls)}"
    assert "lutApply(" in frame and frame.index("lutApply(") < frame.index("drawImage("), (
        "кадр рисуется в обход lutApply — LUT не наложится: " + repr(calls))
    assert not re.search(r"drawImage\(\s*v\s*,", frame), \
        "вернулся прямой drawImage(v) — камера без LUT"
    ks = paint.index("lutKS(")
    assert ks < paint.index("ipvDrawFrame("), (
        "координаты вырезки не пересчитаны под размер LUT-canvas — зум поедет")


# --------------------------------------------------------------------------- #
# 5. Файл подключён к странице
# --------------------------------------------------------------------------- #
def test_страница_подключает_lut_js() -> None:
    """Теги собираются по содержимому static/app/ — новый файл обязан попасть в них
    сразу после 85-inserts-view.js: ipvCamPaint зовёт его функции."""
    from core import app_meta
    import webui

    names = [os.path.basename(p) for p in app_meta.app_js_files()]
    assert "86-lut.js" in names, "86-lut.js не грузится — превью останется без LUT"
    assert names.index("86-lut.js") == names.index("85-inserts-view.js") + 1, (
        "порядок загрузки не тот: файлы идут по именам")
    assert "/static/app/86-lut.js" in webui._page(), "тега 86-lut.js нет на странице"
    assert "__APP_JS__" in HTML.read_text(encoding="utf-8"), (
        "шаблон снова перечисляет скрипты руками")
