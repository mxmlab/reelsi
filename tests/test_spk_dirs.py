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

Общее поле в лестницу не входит вовсе: оно только для клипа БЕЗ тега. Та же лестница
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
              + "const CLIPS=[];let curAE=-1;let AEGLOBAL='',AERENDER='';\n"
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
    """Лестница папок .jsx: jsxdir -> outdir спикера -> папка XML; без тега — общее поле.

    Главное здесь — клип спикера Б: у него jsxdir пуст, и раньше он уезжал в общее поле
    (то есть в папку ПОСЛЕДНЕГО выбранного спикера). Теперь — в свою папку нарезки.
    """
    out = _run(tmp_path, "spk_ladder.js", FUNCS, r"""
AEGLOBAL='C:/out/A';            // общее поле стоит на папке спикера А — как после его выбора
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
def test_switching_speaker_does_not_move_the_clip(tmp_path):
    """Выбор ДРУГОГО спикера не двигает папку клипа: общее поле в лестницу не входит.

    Это и была жалоба: нарезал спикера 1, выбрал спикера 2, собрал спикера 1 — .jsx
    спикера 1 уехали в папку спикера 2. Лестница читает ТОЛЬКО профиль спикера клипа.
    """
    out = _run(tmp_path, "spk_switch.js", FUNCS, r"""
const B={xml:'C:/cut/B/02.xml',job:{speaker:'b'}};
const N={xml:'C:/cut/N/04.xml',job:{}};
const beforeB=effOutdir(B), beforeN=effOutdir(N), globBefore=AEGLOBAL;
AEGLOBAL='C:/out/A';            // выбрали спикера А — общее поле переписалось
const afterB=effOutdir(B), afterN=effOutdir(N);
AEGLOBAL='C:/out/C';            // выбрали спикера В
const lastB=effOutdir(B), lastN=effOutdir(N);
console.log(JSON.stringify({beforeB,beforeN,afterB,afterN,lastB,lastN,globBefore}));
""", SPEAKERS)
    assert out["beforeB"] == out["afterB"] == out["lastB"] == "C:/cut/B", (
        "папка клипа спикера Б поехала за выбранным спикером: %r / %r / %r"
        % (out["beforeB"], out["afterB"], out["lastB"]))
    assert out["beforeN"] == out["afterN"] == "C:/cut/N", (
        "папка клипа без тега не должна зависеть от выбора спикера: %r" % out["afterN"])


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
def test_note_says_where_the_folder_came_from(tmp_path):
    """Подпись под полем называет ступень лестницы, а не молчит.

    Раньше подпись знала два случая, и на клипе спикера без jsxdir (а папка уже бралась
    из outdir или XML) человек видел путь и не знал, чей он.
    """
    out = _run(tmp_path, "spk_note.js", FUNCS + ["jsxDirNote"], r"""
CLIPS.push({xml:'C:/cut/B/02.xml',job:{speaker:'b'}});
curAE=0;
jsxDirNote();
const note=$('aeoutdirnote')?$('aeoutdirnote').textContent:'';
console.log(JSON.stringify({note}));
""", SPEAKERS)
    assert "нарезк" in out["note"], (
        "подпись не говорит, что папка взята из папки нарезки спикера: %r" % out["note"])


def test_html_has_the_per_clip_music_block_once():
    """Разметка блока музыки клипа: три поля и «Другой трек», каждое — ровно один раз.

    Проба на дубли: значение музыки больше не живёт в общих полях шага 3, и второй
    набор полей завёл бы второе хранилище, которое разъедется с первым.
    """
    html = io.open(HTML, encoding="utf-8").read()
    for el_id in ("musicown", "musicovr", "musictrack", "musicreroll",
                  "musicmode", "musicsrc", "musicdir", "musicdl"):
        assert html.count('id="%s"' % el_id) == 1, "поля %s нет или оно не одно" % el_id
    for gone in ("aemusic", "aemusicdir", "musicinbox", "musicinlbl", "musicpick"):
        assert 'id="%s"' % gone not in html, "старое общее поле музыки %s вернулось" % gone
