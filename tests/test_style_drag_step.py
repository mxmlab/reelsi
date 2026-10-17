# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
r"""Тяга значения мышью в панели стиля: шаг на несколько пикселей, Ctrl — точно, Shift — крупно.

ПОЧЕМУ этот тест есть. Тяга шла по схеме «1 пиксель = 1 шаг поля»: у «Масштаб, %» каждый
пиксель мыши давал целый процент, и попасть в нужное значение было нельзя. Теперь шаг — на
ST_DRAG_PX пикселей, Ctrl (или ⌘) делит шаг на 10, Shift умножает на 10.

Тест гоняет БОЕВОЙ `initNumDrag` из `static/app/94-stylepanel.js` под node: заглушки только
у событий окна и мини-элементов, сама тяга — из файла. Копии формулы в тесте нет, иначе он
сторожил бы сам себя. Порог «начали тянуть» (3 px) тоже проверяется как есть.

Запуск: python -m pytest tests/test_style_drag_step.py -q
"""
import io
import json
import os
import re
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PANEL_JS = os.path.join(ROOT, "static", "app", "94-stylepanel.js")

node = pytest.mark.skipif(not shutil.which("node"), reason="тяга проверяется под node в PATH")


def _panel_src():
    return io.open(PANEL_JS, encoding="utf-8").read()


def _func(src, name):
    """`function name(...){...}` целиком по балансу скобок."""
    m = re.search(r"function\s+%s\s*\(" % re.escape(name), src)
    assert m, "не нашлась функция %s" % name
    i = src.index("{", m.end() - 1)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():j + 1]
    raise AssertionError("не сошлись скобки у %s" % name)


def _const(src, name):
    """Строка `const NAME = …` боевого файла — цена пикселя берётся из кода, не копируется."""
    m = re.search(r"^const\s+%s\s*=.*$" % re.escape(name), src, re.M)
    assert m, "не нашлась константа %s" % name
    return m.group(0)


DRAG_STUBS = r"""
// Окно: события мыши, которые тяга вешает на window (mousemove/mouseup).
const WL={};
const window={addEventListener(t,f){(WL[t]=WL[t]||[]).push(f);},
  removeEventListener(t,f){WL[t]=(WL[t]||[]).filter(x=>x!==f);}};
function emit(t,ev){(WL[t]||[]).slice().forEach(f=>f(ev));}
const document={getElementById(){return null;}};
function stFieldEdit(f){}
function updateAngleDial(k,v){}
// Мини-элемент: textContent/style/value и слушатели, которые тяга вешает на span и input.
function mkEl(text){const L={};return {textContent:String(text),style:{},value:'',
  setAttribute(){},focus(){},select(){},
  addEventListener(t,f){(L[t]=L[t]||[]).push(f);},
  fire(t,ev){(L[t]||[]).forEach(f=>f(ev));}};}
// Одно протягивание: нажали у x=100, прошли dx пикселей с модификаторами, отпустили.
function drag(step,ctl,start,dx,mods){
  for(const k in WL)delete WL[k];
  const span=mkEl(start),input=mkEl(start);
  initNumDrag(span,input,{key:'k',ctl:ctl,step:step});
  span.fire('mousedown',{button:0,clientX:100,preventDefault(){}});
  emit('mousemove',Object.assign({clientX:100+dx},mods||{}));
  emit('mouseup',{});
  return Number(span.textContent);}
const out={};
out.normal=drag(1,'num',50,40);                    // 40 px = 10 шагов
out.back=drag(1,'num',50,-40);                     // назад: 40 px = -10 шагов
out.small=drag(1,'num',50,2);                      // порог 3 px не пройден — значение стоит
out.ctrl=drag(1,'num',50,40,{ctrlKey:true});       // точно: 10 шагов по 0.1 = 1
out.meta=drag(1,'num',50,40,{metaKey:true});       // ⌘ — то же, что Ctrl
out.shift=drag(1,'num',50,40,{shiftKey:true});     // крупно: 10 шагов ×10 = 100
out.dec=drag(0.1,'num',50,40,{ctrlKey:true});      // шаг 0.1 точно → 0.01: знаки не теряются
out.dec2=drag(0.1,'num',50,8,{ctrlKey:true});      // 8 px = 2 шага по 0.01: два знака, не один
out.decNormal=drag(0.1,'num',50,40);               // обычная тяга шагом 0.1: 10 шагов = 1.0
out.intCtrl=drag(1,'int',50,40,{ctrlKey:true});    // у целого поля Ctrl шаг не дробит
console.log(JSON.stringify(out));
"""


@node
def test_drag_moves_one_step_per_few_pixels_with_ctrl_and_shift(tmp_path):
    """Тяга: 40 px — 10 шагов, Ctrl — точно (1 шаг), Shift — крупно (100 шагов).

    «Масштаб, %» при старой схеме «1 px = 1 шаг» за 40 px уходил на 40 %; теперь обычная
    тяга — 10 %. Точная тяга дробит шаг на десятые долей и не теряет знаки после запятой,
    целому полю Ctrl ничего не даёт — тоньше единицы там не бывает.
    """
    src = _panel_src()
    panel = _const(src, "ST_DRAG_PX") + "\n" + _func(src, "initNumDrag") + "\n"
    script = panel + DRAG_STUBS
    path = tmp_path / "style_drag_step.js"
    with io.open(str(path), "w", encoding="utf-8") as f:
        f.write(script)
    proc = subprocess.run(["node", str(path)], capture_output=True, text=True,
                          encoding="utf-8-sig", errors="replace", timeout=60)
    assert proc.returncode == 0, (proc.stderr or proc.stdout).strip()[:800]
    res = json.loads(proc.stdout.strip().splitlines()[-1])

    assert res["normal"] == 60, res
    assert res["back"] == 40, res
    assert res["small"] == 50, res
    assert res["ctrl"] == 51, res
    assert res["meta"] == 51, res
    assert res["shift"] == 150, res
    assert res["dec"] == 50.1, res
    assert res["dec2"] == 50.02, res
    assert res["decNormal"] == 51, res
    assert res["intCtrl"] == 60, res
