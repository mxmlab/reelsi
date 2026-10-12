# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Порядок клипов прогона нарезки в списке (параллельные ролики отдают готовое вразнобой).

Просьба владельца (2026-10-11): клипы встают в список в порядке ОЧЕРЕДИ, а не готовности;
появляются сразу, как готовы, но тот, кто шёл раньше, встаёт раньше. Относится к любой
нарезке. Клипы, бывшие в списке до прогона, не двигаются.

Тесты гоняют БОЕВЫЕ функции из `static/app/40-queue.js` (cutAdopt, cutRunStart,
cutInsertRunClip, _spliceClip, clipKey…) и normInsPath из `85-inserts-view.js` под node;
внешние двери (сервер, DOM, тосты) — заглушки, как в соседних тестах интерфейса.

1. Приходят готовые qi=2, потом qi=0, потом qi=1 (статус отдаёт весь results на каждом
   опросе): список [A, q0, q1, q2] после каждого шага корректен, а curAE, указывавший на
   q2, после вставки q0 и q1 указывает на тот же клип;
2. два готовых за один опрос (qi=1 и qi=0) встают по очереди, а не в порядке прихода;
3. F5 посреди прогона: база из состояния (как её сохраняет stateObj) даёт тот же порядок;
4. клип, убранный ДО базы прогона (удаление во время нарезки), сдвигает базу — клипы
   прогона остаются блоком, порядок не ломается.

Мутации (проверены вручную, см. отчёт): push вместо вставки по qi — тест краснеет;
непривязанный curAE (без сдвига) — тест краснеет.

Запуск: python -m pytest tests/test_ui_cut_order.py -q -p no:cacheprovider
"""
import io
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

QUEUE_JS = os.path.join(ROOT, "static", "app", "40-queue.js")
INSERTS_VIEW_JS = os.path.join(ROOT, "static", "app", "85-inserts-view.js")

node = pytest.mark.skipif(not shutil.which("node"),
                          reason="контракт фронта требует node в PATH")


def _read(path):
    return io.open(path, encoding="utf-8").read()


def _func(src, name):
    """Вырезать `[async] function name(...){...}` целиком по балансу скобок."""
    m = re.search(r"(?:async\s+)?function\s+%s\s*\(" % re.escape(name), src)
    assert m, "в исходнике нет функции %s" % name
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


def _let(src, name):
    m = re.search(r"^let %s=.*$" % re.escape(name), src, re.M)
    assert m, "в 40-queue.js нет объявления let %s" % name
    return m.group(0)


STUBS = r"""
let TOASTS=[],LOGS=[],SAVES=0,RENDERS=0;
function t(s,vars){return String(s).replace(/\{(\w+)\}/g,(m,k)=>String((vars||{})[k]));}
function uiLog(m){LOGS.push(String(m));}
function saveState(){SAVES++;}
function renderClips1(){RENDERS++;}
function refreshStatuses(){}
function val(){return '';}
function defJob(){return {};}
let curAE=-1,curEdit=-1,curIns=-1,LASTCLIP={1:'',2:'',3:''};
"""


def _run(tmp_path, name, funcs, body):
    """Заглушки + боевые объявления и функции + сценарий; вернуть разобранный JSON."""
    qsrc = _read(QUEUE_JS)
    isrc = _read(INSERTS_VIEW_JS)
    script = (STUBS + "\n"
              + _func(isrc, "normInsPath") + "\n"
              + "\n".join(_let(qsrc, d) for d in ("CLIPS", "CUTRUN", "SEL_ANCHOR", "DEL_CLIP_IDXS", "DLFMT_WANT")) + "\n"
              + "let SPEAKERS={},STYLES={};\n"
              + "\n".join(_func(qsrc, f) for f in funcs) + "\n"
              + body)
    path = str(tmp_path / name)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(script)
    p = subprocess.run(["node", path], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=60)
    assert p.returncode == 0, (p.stderr or p.stdout).strip()[:800]
    return json.loads(p.stdout.strip().splitlines()[-1])


FUNCS = ["clipPathFix", "clipKey", "clipByXml", "newClip", "cutRunStart",
         "cutInsertRunClip", "cutAdopt", "_spliceClip"]


@node
def test_arrival_out_of_order_lands_in_queue_order(tmp_path):
    """1. q2, потом q0, потом q1 — список [A, q0, q1, q2]; curAE на q2 не теряет клип."""
    out = _run(tmp_path, "zs_order.js", FUNCS, r"""
CLIPS=[newClip('/out/a.xml')];
cutRunStart();
const names=()=>CLIPS.map(c=>c.name);
const steps=[];
const on=(res,ord)=>{cutAdopt({results:res,result_order:ord});steps.push(names());};
on(['/out/q2.xml'],[2]);
curAE=CLIPS.findIndex(c=>c.name==='q2.xml');
if(curAE<0)throw new Error('q2 не в списке');
on(['/out/q2.xml','/out/q0.xml'],[2,0]);
const curAfter0=CLIPS[curAE]?CLIPS[curAE].name:null;
on(['/out/q2.xml','/out/q0.xml','/out/q1.xml'],[2,0,1]);
const curAfter1=CLIPS[curAE]?CLIPS[curAE].name:null;
console.log(JSON.stringify({steps,curAfter0,curAfter1,base:CUTRUN.base,saves:SAVES}));
""")
    assert out["steps"][0] == ["a.xml", "q2.xml"], out["steps"]
    assert out["steps"][1] == ["a.xml", "q0.xml", "q2.xml"], (
        "q0 пришёл позже q2, но должен встать перед ним: %r" % out["steps"][1])
    assert out["steps"][2] == ["a.xml", "q0.xml", "q1.xml", "q2.xml"], (
        "очередь не восстановилась: %r" % out["steps"][2])
    assert out["curAfter0"] == "q2.xml", "curAE после вставки q0 съехал с q2"
    assert out["curAfter1"] == "q2.xml", "curAE после вставки q1 съехал с q2"
    assert out["base"] == 1, "база прогона должна остаться длиной списка до прогона"


@node
def test_two_ready_in_one_poll_follow_queue(tmp_path):
    """2. За один опрос готовы q1 и q0 (в ответе — в порядке прихода): встают по очереди."""
    out = _run(tmp_path, "zs_batch.js", FUNCS, r"""
CLIPS=[newClip('/out/a.xml')];
cutRunStart();
cutAdopt({results:['/out/q1.xml','/out/q0.xml'],result_order:[1,0]});
console.log(JSON.stringify({names:CLIPS.map(c=>c.name)}));
""")
    assert out["names"] == ["a.xml", "q0.xml", "q1.xml"], out["names"]


@node
def test_f5_mid_run_keeps_queue_order(tmp_path):
    """3. F5 после первого клипа: база из состояния, порядок тот же."""
    out = _run(tmp_path, "zs_f5.js", FUNCS, r"""
CLIPS=[newClip('/out/a.xml')];
cutRunStart();
cutAdopt({results:['/out/q2.xml'],result_order:[2]});
const saved=JSON.parse(JSON.stringify({clips:CLIPS.map(c=>({xml:c.xml})),cutrun:CUTRUN}));
// перезагрузка: клипы из состояния, база из состояния, прогон идёт дальше
CLIPS=saved.clips.map(c=>newClip(c.xml));
CUTRUN=saved.cutrun;
cutAdopt({results:['/out/q2.xml','/out/q0.xml'],result_order:[2,0]});
console.log(JSON.stringify({names:CLIPS.map(c=>c.name)}));
""")
    assert out["names"] == ["a.xml", "q0.xml", "q2.xml"], (
        "после F5 порядок по очереди не восстановился: %r" % out["names"])


@node
def test_removed_before_base_moves_base(tmp_path):
    """4. Убрали клип из списка ДО базы прогона — база съехала, клипы прогона остались блоком."""
    out = _run(tmp_path, "zs_base.js", FUNCS, r"""
CLIPS=[newClip('/out/a.xml'),newClip('/out/b.xml')];
cutRunStart();
cutAdopt({results:['/out/q1.xml'],result_order:[1]});
_spliceClip(0);
const baseAfterSplice=CUTRUN.base;
cutAdopt({results:['/out/q1.xml','/out/q0.xml'],result_order:[1,0]});
console.log(JSON.stringify({names:CLIPS.map(c=>c.name),baseAfterSplice}));
""")
    assert out["baseAfterSplice"] == 1, (
        "база прогона не сдвинулась после удаления клипа до неё: %r" % out["baseAfterSplice"])
    assert out["names"] == ["b.xml", "q0.xml", "q1.xml"], out["names"]


@node
def test_open_delete_window_follows_its_clip(tmp_path):
    """5. Окно удаления открыто на q2 (индекс 1), пришёл q0 — индекс указывает на тот же клип.

    Иначе «Удалить» после вставки снесло бы соседний клип. То же для окна выбора формата
    скачивания (DLFMT_WANT). Клип до вставки (индекс 0) индексов не трогает.
    """
    out = _run(tmp_path, "zs_delwin.js", FUNCS, r"""
CLIPS=[newClip('/out/a.xml')];
cutRunStart();
cutAdopt({results:['/out/q2.xml'],result_order:[2]});
DEL_CLIP_IDXS=[1];
DLFMT_WANT=1;
cutAdopt({results:['/out/q2.xml','/out/q0.xml'],result_order:[2,0]});
console.log(JSON.stringify({
  names:CLIPS.map(c=>c.name),
  del:DEL_CLIP_IDXS.map(i=>CLIPS[i]?CLIPS[i].name:null),
  fmt:CLIPS[DLFMT_WANT]?CLIPS[DLFMT_WANT].name:null}));
""")
    assert out["names"] == ["a.xml", "q0.xml", "q2.xml"], out["names"]
    assert out["del"] == ["q2.xml"], (
        "окно удаления после вставки указывает не на свой клип: %r" % out["del"])
    assert out["fmt"] == "q2.xml", (
        "окно выбора формата после вставки указывает не на свой клип: %r" % out["fmt"])

