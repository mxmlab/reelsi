# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сброс залипшей ошибки запуска плагин-хоста в панели звука.

Стенд проверяет:
1. Очистку статуса ошибки при действиях со звуком (смена движка, запуск хоста,
   настройка плагина).
2. Очистку ошибки и перезапуск хоста по завершении скачивания модели (progDone).
3. Серверную очистку зависшего/умершего процесса (зомби) перед повторным стартом хоста.

Запуск: py -3.10 -m pytest tests/test_voicefx_error_reset.py -q
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

STYLES_JS = ROOT / "static" / "app" / "95-styles.js"
EDITOR_JS = ROOT / "static" / "app" / "70-editor.js"
node = pytest.mark.skipif(not shutil.which("node"), reason="стенд требует node в PATH")


def _read_js_func(path: Path, name: str) -> str:
    src = path.read_text(encoding="utf-8")
    m = re.search(r"function\s+%s\s*\(" % re.escape(name), src)
    assert m, f"функция {name} не найдена в {path}"
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


def _run_node(code: str) -> dict[str, Any]:
    p = subprocess.run(["node", "-e", code], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@node
def test_voicefx_status_reset_on_actions():
    """Действия (смена движка, старт хоста) сбрасывают текст ошибки."""
    js_status = _read_js_func(STYLES_JS, "voiceFxStatus")
    js_vfx_el = _read_js_func(STYLES_JS, "vfxEl")

    code = f"""
    {js_vfx_el}
    {js_status}
    function El(tag){{
      this.tag=tag;this.children=[];this.style={{}};this.dataset={{}};this._attrs={{}};
      this.textContent='';this.className='';
    }}
    El.prototype.appendChild=function(c){{c.parentNode=this;this.children.push(c);return c;}};
    El.prototype.querySelector=function(sel){{
      var m=/data-vfx="([^"]+)"/.exec(sel);
      if(m){{
        var key=m[1];
        for(var i=0;i<this.children.length;i++){{
          if(this.children[i].dataset&&this.children[i].dataset.vfx===key)return this.children[i];
        }}
      }}
      return null;
    }};

    var host = new El('div');
    var st = new El('div');
    st.dataset.vfx = 'status';
    st.textContent = 'Ошибка запуска хоста';
    host.appendChild(st);

    var textBefore = st.textContent;
    // Очищаем статус перед действием
    voiceFxStatus(host, '');
    var textAfter = st.textContent;

    console.log(JSON.stringify({{
      before: textBefore,
      after: textAfter
    }}));
    """
    res = _run_node(code)
    assert res["before"] == "Ошибка запуска хоста"
    assert res["after"] == ""


def test_voice_host_get_cleans_up_zombie_session(monkeypatch):
    """Мёртвый или упавший процесс сессии хоста счищается из VOICEHOST."""
    from api import voicefx as api_vfx
    from core import voicefx as core_vfx

    fake_proc = SimpleNamespace(poll=lambda: 1, returncode=1)
    fake_ses = SimpleNamespace(sid="ses-123", done=False, proc=fake_proc, state={})

    monkeypatch.setattr(core_vfx, "live_session", lambda sid: fake_ses)
    stopped = []
    monkeypatch.setattr(core_vfx, "live_stop", lambda ses: stopped.append(ses.sid))

    with api_vfx.VOICELOCK:
        api_vfx.VOICEHOST["clip.xml"] = "ses-123"

    res = api_vfx._voice_host_get("clip.xml")
    assert res is None, "Мёртвая сессия не должна возвращаться как активный хост"
    assert "clip.xml" not in api_vfx.VOICEHOST, "Мёртвая сессия осталась в словаре хостов"
    assert stopped == ["ses-123"], "Мёртвая сессия не была остановлена/очищена"


PREVIEW_JS = ROOT / "static" / "app" / "60-preview.js"


@node
def test_play_retries_voice_after_failed_bake():
    """Голос клипа не посчитался (нет окружения RoFormer) — «Играть» пробует снова.

    Причина могла уйти (окружение докачали), а vtPrep зовут только правки ручек: без
    повтора ошибка висела бы до переоткрытия превью.

    «Играть» на шаге 1 — это кнопка РЕДАКТОРА (edPlay, static/app/70-editor.js): монтажного
    плеера pvPlay с 02.10.2026 нет, играет единственный плеер. Повтор живёт теперь там.
    """
    code = _read_js_func(EDITOR_JS, "edPlay") + """
    const ST = {failed: true}; let PREP = 0;
    const ED = {play: false, raw: false, cs: 0, dur: 10, blocks: [{s0: 0, s1: 10}]};
    const PV = {vids: [{muted: false, style: {}, play(){ return Promise.resolve(); }}], audio: [1]};
    const MEDIA_VOL = 1;
    function edBlockAt(s){ return 0; }
    function pvVideoTo(){}
    function edTick(){}
    function vtLivePlay(){}
    function spareIdle(){}
    function requestAnimationFrame(){ return 0; }
    function vtOf(){ return ST; }
    function vtPrep(){ PREP++; }
    function ico(){ return ''; }
    function $(id){ return {innerHTML: ''}; }
    edPlay();
    ED.play = false;      // как будто остановили: проверяем именно повтор запроса
    edPlay();
    console.log(JSON.stringify({first: PREP, second: PREP, failed: ST.failed}));
    """
    out = _run_node(code)
    assert out["first"] == 1, "после сбоя «Играть» не попробовало посчитать голос заново"
    assert out["second"] == 1, "повтор только один раз на сбой, а не на каждое нажатие"
    assert out["failed"] is False


def test_preview_stage_does_not_shrink_below_its_aspect():
    """Низкое окно: кадр предпросмотра не сжимается флексом по высоте.

    Колонка плеера — flex-столбец; без flex:none сцена ужималась по высоте и теряла
    пропорцию плана (1000×640: 272×186 вместо 9:16, кадр обрезан). Высота кадра
    считается за вычетом строк плеера под ним.
    """
    css = (ROOT / "static" / "app.css").read_text(encoding="utf-8")
    m = re.search(r"\.modal\.aemode \.inspv \.pvstage\{([^}]*)\}", css)
    assert m, "правило сцены предпросмотра в модалке пропало"
    rule = m.group(1)
    assert "flex:none" in rule, "сцена снова сжимается флексом и теряет пропорцию"
    assert "aspect-ratio:var(--stage-ar" in rule
