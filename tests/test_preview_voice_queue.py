# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Очередь подключения звука в превью не копит элементы закрытых плееров.

`VOICEPEND` (static/app/60-preview.js) держит медиа-элементы, которые ждут подключения к
звуковому графу: voiceWiring кладёт, voiceEnsure разбирает. Раньше элемент закрытого плеера
из очереди никто не убирал: он висел там с буферами, а когда граф понадобился, voiceEnsure
подключал уже мёртвый элемент. Теперь:

1. дверь освобождения медиа `mediaFree` (static/app/00-core.js) убирает элемент из очереди;
2. voiceEnsure не подключает элемент, который стоял в документе при постановке и выпал из
   него мимо mediaFree (страховка);
3. дублёр дорожки голоса (`vtSpareOf`) в документ не вставляется никогда, и он по-прежнему
   подключается к графу: голое правило «только в документе» выбросило бы его и сломало
   громкость голоса.

Стенд гоняет БОЕВЫЕ функции из обоих файлов под node с заглушками (образец — tests/test_bulk_delete.py):
тела вырезаются по балансу скобок, копий в тесте нет. Внешние двери — только заглушки
(AudioContext, узлы графа, DOM из трёх методов).

Запуск:  py -3.10 -m pytest tests/test_preview_voice_queue.py -q
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

CORE_JS = os.path.join(ROOT, "static", "app", "00-core.js")
PREVIEW_JS = os.path.join(ROOT, "static", "app", "60-preview.js")

node = pytest.mark.skipif(not shutil.which("node"),
                          reason="стенд требует node в PATH")

FUNCS_CORE = ("mediaFree",)
FUNCS_PREVIEW = ("audioGraph", "audioWake", "voiceGraphWire", "voiceWiring",
                 "voiceGraphNeeded", "voiceEnsure")
# Строка `let AUDIO=null,VG=null,MG=null;` объявляет и граф целиком.
LETS_PREVIEW = ("AUDIO", "VOICEPEND")


def _src(path):
    return io.open(path, encoding="utf-8").read()


def _func(src, name):
    """Вырезать `[async] function name(...){...}` целиком по балансу скобок."""
    m = re.search(r"(?:async\s+)?function\s+%s\s*\(" % re.escape(name), src)
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


def _let(src, name):
    m = re.search(r"^let %s=.*$" % re.escape(name), src, re.M)
    assert m, "в 60-preview.js нет объявления let %s" % name
    return m.group(0)


# Заглушки внешних дверей: звуковой граф, DOM (только то, что трогает mediaFree и
# voiceEnsure), состояние стиля. Граф записывает, какие элементы к нему подключили.
STUBS = r"""
const WIRED=[];
let CURSTYLE={voice_db:0};
class El{constructor(n){this.name=n;this.isConnected=false;this.parentNode=null;}
  pause(){} removeAttribute(){} load(){}}
const stage={kids:[],removeChild(v){this.kids=this.kids.filter(k=>k!==v);
  v.parentNode=null;v.isConnected=false;}};
function attach(v){stage.kids.push(v);v.parentNode=stage;v.isConnected=true;}
"""

# Граф поднят заглушкой: audioGraph видит AUDIO и ничего не создаёт.
GRAPH = r"""
AUDIO={state:'running',createMediaElementSource(v){WIRED.push(v.name);return {connect(){}};}};
VG={connect(){},gain:{value:1}};
MG={connect(){},gain:{value:1}};
"""


def _run(tmp_path, body):
    core = _src(CORE_JS)
    prev = _src(PREVIEW_JS)
    script = (STUBS + "\n"
              + "\n".join(_let(prev, d) for d in LETS_PREVIEW) + "\n"
              + "\n".join(_func(core, f) for f in FUNCS_CORE) + "\n"
              + "\n".join(_func(prev, f) for f in FUNCS_PREVIEW) + "\n"
              + GRAPH + body)
    path = str(tmp_path / "zs_voice_queue.js")
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(script)
    p = subprocess.run(["node", path], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=60)
    assert p.returncode == 0, (p.stderr or p.stdout).strip()[:800]
    return json.loads(p.stdout.strip().splitlines()[-1])


@node
def test_media_free_takes_the_element_out_of_the_queue(tmp_path):
    """Закрытый плеер: элемент ждал в очереди, mediaFree его освобождает — очередь пуста,
    и последующий voiceEnsure мёртвый элемент к графу не подключает."""
    out = _run(tmp_path, r"""
const a=new El('A');
attach(a);
voiceWiring(a);
const before=VOICEPEND.map(v=>v.name);
mediaFree(a);
const after=VOICEPEND.map(v=>v.name);
voiceEnsure();
console.log(JSON.stringify({before,after,wired:WIRED}));
""")
    assert out["before"] == ["A"], "элемент не встал в очередь — стенд не проверяет то, что обещает"
    assert out["after"] == [], (
        "mediaFree не убрал элемент закрытого плеера из VOICEPEND: %r" % out["after"])
    assert out["wired"] == [], "мёртвый элемент подключён к графу после mediaFree"


@node
def test_element_removed_without_media_free_is_not_wired(tmp_path):
    """Страховка: элемент стоял в документе, при разборе очереди выпал из него мимо
    mediaFree — к графу его не подключаем."""
    out = _run(tmp_path, r"""
const b=new El('B');
attach(b);
voiceWiring(b);
stage.removeChild(b);
voiceEnsure();
console.log(JSON.stringify({wired:WIRED,queue:VOICEPEND.length}));
""")
    assert out["wired"] == [], (
        "voiceEnsure подключил элемент, удалённый из документа мимо mediaFree: %r" % out["wired"])
    assert out["queue"] == 0, "очередь не разобрана"


@node
def test_live_and_spare_elements_are_still_wired(tmp_path):
    """Живой элемент в документе и дублёр дорожки голоса (в документ не вставляется
    никогда) — оба подключаются. Дублёр — проверка, что страховка не выбрасывает его."""
    out = _run(tmp_path, r"""
const live=new El('live');
attach(live);
voiceWiring(live);
const spare=new El('spare');
voiceWiring(spare);
voiceEnsure();
console.log(JSON.stringify({wired:WIRED}));
""")
    assert sorted(out["wired"]) == ["live", "spare"], (
        "страховка выбросила элемент из графа: %r" % out["wired"])
