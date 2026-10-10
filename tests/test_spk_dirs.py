# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Папка .jsx у каждого спикера СВОЯ — одна лестница на клип.

Владелец нарезал спикера 1, потом выбрал спикера 2, потом собрал спикера 1 — и его
`.jsx` легли в папку спикера 2. Причина была в одной строке: `effOutdir` знала только
`jsxdir` профиля, и клип спикера без `jsxdir` (а у одного из профилей он пустой) падал на ОБЩЕЕ
поле `AEGLOBAL`. А `AEGLOBAL` переписывает КАЖДЫЙ выбор спикера (`applySpeakerDirs`,
`setClipSpeaker`) — поэтому клипы собирались в папку последнего выбранного.

Теперь правило живёт ОДНОЙ функцией `effOutdir(c)`:

    jsxdir профиля спикера -> outdir профиля (папка нарезки того же спикера) -> папка XML

Отдельного поля в интерфейсе нет вовсе: общего значения, которое могло бы переписаться,
больше не существует, а клип без тега кладёт .jsx рядом со своим XML — ровно то, что
api/build.py делает с пустым outdir. Та же лестница
у папки рендера (`effRenderdir`), но без последней ступени: у рендера «рядом с XML»
нет, дефолт — общий `AERENDER` от сервера.

Тесты гоняют БОЕВЫЕ функции из `static/app/95-styles.js` под node (как соседние тесты
интерфейса): функция вырезается по балансу скобок, состояние и внешние двери
подставляются заглушками.

Запуск: python -m pytest tests/test_spk_dirs.py -q
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

STYLES_JS = os.path.join(ROOT, "static", "app", "95-styles.js")
HTML = os.path.join(ROOT, "templates", "index.html")

node = pytest.mark.skipif(not shutil.which("node"),
                          reason="контракт фронта требует node в PATH")


def _src():
    return io.open(STYLES_JS, encoding="utf-8").read()


def _func(src, name):
    """Вырезать `[async] function name(...){...}` целиком по балансу скобок."""
    m = re.search(r"(?:async\s+)?function\s+%s\s*\(" % re.escape(name), src)
    assert m, "в 95-styles.js не нашлась функция %s" % name
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


# Заглушки — только внешние двери. Всё, что проверяется, — боевые функции из 95-styles.js.
# `$` — реестр по id (как в браузере): подпись под полем пишется в элемент, а не в никуда,
# и тест читает написанное ОТТУДА, где его увидит человек.
STUBS = r"""
const ELS={};
function $(id){return ELS[id]||(ELS[id]={id:id,value:'',textContent:''});}
function val(id){return '';}
function t(s,vars){return String(s).replace(/\{(\w+)\}/g,(m,k)=>String((vars||{})[k]));}
function saveState(){}
// Дверь спикера клипа живёт в 40-queue.js и вырезается в других тестах: здесь она
// внешняя дверь вырезанных функций (effOutdir/effRenderdir ходят за спикером клипа).
function clipSpeaker(c){return (c&&c.job&&c.job.speaker)||'';}
function samePath(a,b){return String(a||'')===String(b||'');}
function openClipSpeaker(){
  if(curAE<0||!CLIPS[curAE])return null;
  const k=(CLIPS[curAE].job||{}).speaker;
  return k?(SPEAKERS[k]||null):null;}
"""


def _run(tmp_path, name, funcs, body, speakers_js):
    src = _src()
    script = (STUBS + "\nconst SPEAKERS=" + json.dumps(speakers_js, ensure_ascii=False) + ";\n"
              + "const CLIPS=[];let curAE=-1;let AERENDER='';\n"
              + "\n".join(_func(src, f) for f in funcs) + "\n"
              + body)
    path = str(tmp_path / name)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(script)
    p = subprocess.run(["node", path], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=60)
    assert p.returncode == 0, (p.stderr or p.stdout).strip()[:900]
    return json.loads(p.stdout.strip().splitlines()[-1])


# Три спикера ровно по диагнозу: у А есть jsxdir, у Б — только outdir, у В — ничего.
SPEAKERS = {
    "a": {"label": "А", "jsxdir": "C:/out/A", "outdir": "C:/cut/A", "renderdir": "C:/exp/A"},
    "b": {"label": "Б", "jsxdir": "", "outdir": "C:/cut/B"},
    "c": {"label": "В"},
}

FUNCS = ["xmlDirOf", "effOutdir", "effRenderdir"]


@node
def test_jsx_folder_ladder_per_speaker(tmp_path):
    """Лестница папок .jsx: jsxdir -> outdir спикера -> папка XML; поля в интерфейсе нет.

    Главное здесь — клип спикера Б: у него jsxdir пуст, и раньше он уезжал в общее поле
    (то есть в папку ПОСЛЕДНЕГО выбранного спикера). Теперь — в свою папку нарезки.
    """
    out = _run(tmp_path, "spk_ladder.js", FUNCS, r"""
const A={xml:'C:/cut/A/01.xml',job:{speaker:'a'}};
const B={xml:'C:/cut/B/02.xml',job:{speaker:'b'}};
const C={xml:'C:/cut/C/03.xml',job:{speaker:'c'}};
const N={xml:'C:/cut/N/04.xml',job:{}};          // клип без тега спикера
console.log(JSON.stringify({a:effOutdir(A),b:effOutdir(B),c:effOutdir(C),n:effOutdir(N),
  bNoJob:effOutdir({xml:'C:/cut/B/05.xml'}), empty:effOutdir(null)}));
""", SPEAKERS)
    assert out["a"] == "C:/out/A", "у спикера с jsxdir папка не из профиля: %r" % out["a"]
    assert out["b"] == "C:/cut/B", (
        "клип спикера без jsxdir уехал не в свою папку нарезки: %r" % out["b"])
    assert out["c"] == "C:/cut/C", (
        "клип спикера без обеих папок уехал не в папку XML: %r" % out["c"])
    assert out["n"] == "C:/cut/N", (
        "клип без тега не получил папку своего XML: %r" % out["n"])
    assert out["bNoJob"] == "C:/cut/B", (
        "у клипа со спикером, но без задания, потерялась папка XML: %r" % out["bNoJob"])
    assert out["empty"] is None, "клипа нет — папки нет"


@node
def test_jsx_folder_depends_only_on_the_clips_own_speaker(tmp_path):
    """Папка клипа зависит ТОЛЬКО от профиля его спикера: общего поля нет.

    Это и была жалоба: нарезал спикера 1, выбрал спикера 2, собрал спикера 1 — .jsx
    спикера 1 уехали в папку спикера 2. Общего поля (`AEGLOBAL`) в интерфейсе больше нет
    вовсе: переписывать папку чужих клипов нечем.
    """
    out = _run(tmp_path, "spk_switch.js", FUNCS, r"""
const B={xml:'C:/cut/B/02.xml',job:{speaker:'b'}};
const N={xml:'C:/cut/N/04.xml',job:{}};
const first={b:effOutdir(B),n:effOutdir(N)};
const again={b:effOutdir(B),n:effOutdir(N)};
console.log(JSON.stringify({first,again,keys:Object.keys(globalThis)
  .filter(k=>k==='AEGLOBAL')}));
""", SPEAKERS)
    assert out["first"] == {"b": "C:/cut/B", "n": "C:/cut/N"}, out["first"]
    assert out["again"] == out["first"], (
        "папка клипа поехала от повторного чтения: %r" % out["again"])
    assert out["keys"] == [], "общее поле папки .jsx (AEGLOBAL) вернулось в интерфейс"


@node
def test_render_folder_ladder(tmp_path):
    """Папка рендера — та же лестница, но без папки XML: последняя ступень — общее поле.

    Общее поле здесь ЗАКОННО (дефолт `exp` от сервера): у рендера нет папки «рядом с
    XML». Клип Б без renderdir обязан получить общую, а не пустоту.
    """
    out = _run(tmp_path, "spk_render.js", FUNCS, r"""
AERENDER='C:/exp';
const A={xml:'C:/cut/A/01.xml',job:{speaker:'a'}};
const B={xml:'C:/cut/B/02.xml',job:{speaker:'b'}};
const C={xml:'C:/cut/C/03.xml',job:{speaker:'c'}};
const N={xml:'C:/cut/N/04.xml',job:{}};
console.log(JSON.stringify({a:effRenderdir(A),b:effRenderdir(B),c:effRenderdir(C),
  n:effRenderdir(N)}));
""", SPEAKERS)
    assert out["a"] == "C:/exp/A", "renderdir профиля не подхватился: %r" % out["a"]
    assert out["b"] == "C:/exp", "клип без renderdir не получил общую папку: %r" % out["b"]
    assert out["c"] == "C:/exp", "у спикера без renderdir потерялась общая папка: %r" % out["c"]
    assert out["n"] == "C:/exp", "клип без тега не получил общую папку рендера: %r" % out["n"]


@node
def test_render_folder_note_says_where_the_folder_came_from(tmp_path):
    """Подпись под папкой рендера называет ступень лестницы, а не молчит.

    Раньше подпись знала два случая, и на клипе спикера без renderdir (а папка уже
    бралась из общего `AERENDER`) человек видел путь и не знал, чей он.
    """
    out = _run(tmp_path, "spk_note.js", FUNCS + ["renderDirNote", "openClipSpeaker"], r"""
CLIPS.push({xml:'C:/cut/B/02.xml',job:{speaker:'a'}});
curAE=0;
AERENDER='C:/exp';
renderDirNote();
const own=$('aerenderdirnote').textContent;
CLIPS[0].job={speaker:'b'};
renderDirNote();
const shared=$('aerenderdirnote').textContent;
console.log(JSON.stringify({own,shared}));
""", SPEAKERS)
    assert "профил" in out["own"], (
        "подпись не говорит, что папка рендера из профиля спикера: %r" % out["own"])
    assert "renderdir" in out["shared"], (
        "подпись не говорит, что у спикера renderdir не задан: %r" % out["shared"])


def test_html_has_no_step3_music_brightness_and_jsx_folder_fields():
    """Нижний блок шага 3 убран: ни папки .jsx, ни яркости, ни музыки/цензуры клипа.

    Музыка и цензура переехали в панель стиля (группа «Аудио», строится по схеме),
    яркость убрана совсем (Lumetri настраивается стилем), папку .jsx задаёт профиль
    спикера. Вернувшееся поле завело бы ВТОРОЕ хранилище значения — ровно то, от чего
    уходили.
    """
    from core import style_schema

    html = io.open(HTML, encoding="utf-8").read()
    for el_id in ("aeoutdir", "aeoutdir3", "aeexposure", "censor",
                  "musicown", "musicovr", "musictrack", "musicreroll",
                  "musicmode", "musicsrc", "musicdir", "musicdl"):
        assert 'id="%s"' % el_id not in html, "поле %s вернулось на шаг 3" % el_id

    keys = set()

    def walk(items):
        for it in items:
            if it.get("type") == "group":
                walk(it.get("items", []))
            elif it.get("type") == "field":
                keys.add(it["key"])

    for layer in style_schema.LAYERS:
        walk(layer.get("items", []))
    for key in ("music_mode", "music_dir", "music_src", "censor"):
        assert key in keys, "ручка %s не заведена в панели стиля" % key
