# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Окно правки кусков подкаста (П5): чистые функции `46-podcast-editor.js` и статический каркас.

Чистые функции (без DOM) гоняем под node: файл грузится целиком, перед ним — заглушка
`document` (его трогает только регистрация клавиш). Проверяется:

1. `podParseTime` — «1:02:03.5», «02:03», «75»; мусор → null;
2. `podFmtTime` — «h:mm:ss.s» от часа, иначе «mm:ss.s»;
3. `podClampSeg` — наезд на соседа режется по его краю, короче минимума — до минимума,
   края записи не переходятся;
4. `podSnap` — прилипание к началу/концу ближайшего слова, дальше 1 с — не прилипает;
5. статика: в `index.html` есть окно `mbPod` и все его id; заглушка из `45-podcast.js`
   удалена, а `openPodcastEditor` живёт в `46-podcast-editor.js`.

Запуск: py -3.10 -m pytest tests/test_podcast_editor_js.py -q
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
APP = ROOT / "static" / "app"
EDITOR_JS = APP / "46-podcast-editor.js"
PODCAST_JS = APP / "45-podcast.js"
INDEX = ROOT / "templates" / "index.html"

node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")


def _read(path: Path) -> str:
    with io.open(str(path), encoding="utf-8") as f:
        return f.read()


def _run_node(tmp_path: Path, body: str) -> Any:
    """Файл редактора + заглушка DOM + кусок проверки. Возвращает JSON из последней строки stdout."""
    stub = "const document={addEventListener(){},removeEventListener(){},getElementById(){return null}};\n"
    # 00-core.js первым: 46 строит POD_HIST на histStack (общая механика отмены)
    script = stub + _read(APP / "00-core.js") + "\n" + _read(EDITOR_JS) + "\n" + body + "\n"
    path = tmp_path / "pod_editor_check.js"
    with io.open(str(path), "w", encoding="utf-8") as f:
        f.write(script)
    proc = subprocess.run(["node", str(path)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)
    assert proc.returncode == 0, ("node упал:\n" + (proc.stderr or proc.stdout)).strip()[:1500]
    lines = [ln.strip() for ln in (proc.stdout or "").strip().splitlines() if ln.strip()]
    assert lines, "node ничего не вывел"
    return json.loads(lines[-1])


# --- podParseTime / podFmtTime ---------------------------------------------------------

@node
def test_parse_time_formats(tmp_path):
    res = _run_node(tmp_path, """
console.log(JSON.stringify({
  hms: podParseTime("1:02:03.5"), ms: podParseTime("02:03"), sec: podParseTime("75"),
  bad: podParseTime("abc"), empty: podParseTime(""), dec: podParseTime("12.5"),
  two_colons_fraction: podParseTime("1.5:03"), neg: podParseTime("-5")
}));
""")
    assert res["hms"] == 3723.5
    assert res["ms"] == 123
    assert res["sec"] == 75
    assert res["bad"] is None
    assert res["empty"] is None
    assert res["dec"] == 12.5
    # дробь допустима только в последней части: «1.5:03» — не время
    assert res["two_colons_fraction"] is None
    assert res["neg"] is None


@node
def test_fmt_time(tmp_path):
    res = _run_node(tmp_path, """
console.log(JSON.stringify({
  h: podFmtTime(3723.5), m: podFmtTime(123), zero: podFmtTime(0), round: podFmtTime(59.96)
}));
""")
    assert res["h"] == "1:02:03.5"
    assert res["m"] == "02:03.0"
    assert res["zero"] == "00:00.0"
    assert res["round"] == "01:00.0"


# --- podClampSeg -----------------------------------------------------------------------

@node
def test_clamp_neighbour_on_the_right_cuts_by_its_start(tmp_path):
    """Наезд на соседа справа обрезается по его началу (25), а не отменяет движение."""
    res = _run_node(tmp_path, """
const segs=[{id:'a',start:10,end:20},{id:'b',start:25,end:40}];
console.log(JSON.stringify(podClampSeg(segs,0,10,27,100)));
""")
    assert res == {"start": 10, "end": 25}


@node
def test_clamp_short_result_goes_back_to_minimum(tmp_path):
    """Тянут конец на 2 с от начала → кусок не сжимается ниже минимума 5 с."""
    res = _run_node(tmp_path, """
const segs=[{id:'a',start:10,end:20},{id:'b',start:25,end:40}];
console.log(JSON.stringify(podClampSeg(segs,0,10,12,100)));
""")
    assert res == {"start": 10, "end": 15}


@node
def test_clamp_neighbour_on_the_left_and_record_edges(tmp_path):
    res = _run_node(tmp_path, """
const segs=[{id:'a',start:10,end:20},{id:'b',start:25,end:40}];
console.log(JSON.stringify({
  left: podClampSeg(segs,1,18,40,100),        // начало не левее конца соседа (20)
  end_edge: podClampSeg(segs,1,25,200,100),   // конец не дальше длины записи
  start_edge: podClampSeg(segs,0,-30,20,100)  // начало не левее нуля
}));
""")
    assert res["left"] == {"start": 20, "end": 40}
    assert res["end_edge"] == {"start": 25, "end": 100}
    assert res["start_edge"] == {"start": 0, "end": 20}


# --- podSnap ---------------------------------------------------------------------------

@node
def test_snap_to_nearest_word_edge_within_one_second(tmp_path):
    res = _run_node(tmp_path, """
const words=[{w:'a',start:10,end:10.5},{w:'b',start:11,end:11.6}];
console.log(JSON.stringify({
  end_near: podSnap(11.4,words,'end'),        // ближайший конец — 11.6
  start_near: podSnap(11.8,words,'start'),    // ближайшее начало — 11
  start_far: podSnap(12.5,words,'start'),     // до ближайшего слова 1.5 с — не прилипает
  no_words: podSnap(7.25,[],'end')
}));
""")
    assert res["end_near"] == 11.6
    assert res["start_near"] == 11
    assert res["start_far"] == 12.5
    assert res["no_words"] == 7.25


# --- разрез, место под новый кусок, видимость слова (П-P1t) ---------------------------------

@node
def test_split_at_playhead_makes_two_pieces(tmp_path):
    """C: кусок [10,70], плейхед 40 → [10,40] и [40,70]. Точка прилипает к концу слова в пределах
    1 с (слово 38–39.6 → разрез 39.6, слово целиком слева). Мутация (2): без проверки 5 с
    разрез [10,12] проходит — тест краснеет (см. test_split_refuses_piece_shorter_than_minimum)."""
    res = _run_node(tmp_path, """
const segs=[{id:'a',start:10,end:70}];
console.log(JSON.stringify({
  plain: podSplitAt(segs,40,[]),
  snapped: podSplitAt(segs,40,[{w:'x',start:38,end:39.6}]),
  farWord: podSplitAt(segs,40,[{w:'x',start:20,end:21}])
}));
""")
    assert res["plain"] == {"i": 0, "a": {"start": 10, "end": 40}, "b": {"start": 40, "end": 70}}
    assert res["snapped"]["a"] == {"start": 10, "end": 39.6}
    assert res["snapped"]["b"] == {"start": 39.6, "end": 70}
    assert res["farWord"]["a"] == {"start": 10, "end": 40}      # слово в 19 с — не прилипает


@node
def test_split_refuses_piece_shorter_than_minimum(tmp_path):
    """Оба куска не короче 5 с: у края или вне куска — null (тост у вызывающего)."""
    res = _run_node(tmp_path, """
const segs=[{id:'a',start:10,end:70}];
console.log(JSON.stringify({
  nearLeft: podSplitAt(segs,12,[]),
  nearRight: podSplitAt(segs,68,[]),
  outside: podSplitAt(segs,5,[]),
  edgeOk: podSplitAt(segs,15,[]) !== null
}));
""")
    assert res["nearLeft"] is None
    assert res["nearRight"] is None
    assert res["outside"] is None
    assert res["edgeOk"] is True                                 # ровно 5 с — уже можно


@node
def test_gap_for_double_click_is_neighbours_clamped_to_60s(tmp_path):
    """Двойной клик в дырке между [0,30] и [100,160] в t=60 → [30,90]: 60 с вокруг точки,
    обрезано левым соседом (30). Мутация (1): без ограничения 60 с вышло бы [30,100] — тест краснеет.
    В дырке 3 с — отказ; внутри куска — отказ; пустая запись — 60 с вокруг точки."""
    res = _run_node(tmp_path, """
const two=[{start:0,end:30},{start:100,end:160}];
console.log(JSON.stringify({
  gap: podGapAt(two,60,200),
  tiny: podGapAt([{start:0,end:30},{start:33,end:60}],31.5,200),
  inside: podGapAt(two,15,200),
  empty: podGapAt([],100,300),
  edgeClip: podGapAt([{start:0,end:30}],35,40)
}));
""")
    assert res["gap"] == {"start": 30, "end": 90}
    assert res["tiny"] is None
    assert res["inside"] is None
    assert res["empty"] == {"start": 70, "end": 130}
    assert res["edgeClip"] == {"start": 30, "end": 40}          # конец записи — край


@node
def test_word_bubbles_never_overlap_and_stay_in_ribbon(tmp_path):
    """Баблы: жадно слева направо, между соседями зазор ≥ 4 px, ни один не вылезает за край ленты.

    Окно 15 с на 600 px (40 px/с). Слова по 0,5 с (20 px): «a» (бабл 20 px) проходят, «слово»
    (42 px) наехал бы на «a» справа и пропускается. Из 30 слов рисуются 15 «a», без наездов.
    """
    res = _run_node(tmp_path, """
const measure=t=>t.length*6;
const words=[];
for(let i=0;i<30;i++)words.push({w:(i%2?'слово':'a'),start:i*0.5,end:i*0.5+0.5});
const out=podWordBubbles(words,0,15,600,measure);
let minGap=Infinity;
for(let i=1;i<out.length;i++)minGap=Math.min(minGap,out[i].x-(out[i-1].x+out[i-1].w));
const inside=out.every(b=>b.x>=0&&b.x+b.w<=600+1e-9);
console.log(JSON.stringify({n:out.length,minGap:minGap,inside:inside,texts:[...new Set(out.map(b=>b.text))]}));
""")
    assert res["n"] == 15, res
    assert res["texts"] == ["a"], "«слово» (42 px) не влезает в зазор у «a» — пропускается"
    assert res["minGap"] >= 4 - 1e-9, "баблы наезжают друг на друга или зазор меньше 4 px"
    assert res["inside"] is True, "бабл вылез за край ленты"


@node
def test_dense_speech_bubbles_show_enough_words(tmp_path):
    """Живая проверка: плотная речь (слова 0,3 с, паузы 0,05 с) на 1280 px.

    32 с (40 px/с): рисуется не меньше 20 % слов окна. 6 с (213 px/с): рисуется каждое слово
    окна (последнее целиком влезает в ленту — иначе это край, а не раскладка).
    """
    res = _run_node(tmp_path, """
const measure=t=>t.length*6;
const mk=n=>{const a=[];for(let i=0;i<n;i++)a.push({w:'слово',start:i*0.35,end:i*0.35+0.3});return a;};
const wide=mk(92), zoom=mk(16);
const wideIn=wide.filter(w=>w.start<32).length, zoomIn=zoom.filter(w=>w.start<6).length;
const wideOut=podWordBubbles(wide,0,32,1280,measure).length;
const zoomOut=podWordBubbles(zoom,0,6,1280,measure).length;
console.log(JSON.stringify({wideIn:wideIn,wideOut:wideOut,zoomIn:zoomIn,zoomOut:zoomOut}));
""")
    assert res["wideOut"] >= 0.2 * res["wideIn"], f"на 32 с нарисовано мало: {res}"
    assert res["zoomOut"] == res["zoomIn"], f"на 6 с нарисовано не все слова: {res}"


@node
def test_word_bubble_width_is_text_or_time_span(tmp_path):
    """Ширина бабла = max(текст + 12 px, ширина слова по времени); вне окна слова не рисуются."""
    res = _run_node(tmp_path, """
const measure=t=>t.length*6;
const words=[{w:'ab',start:0,end:1},{w:'ab',start:5,end:5.1},{w:'x',start:20,end:21}];
const out=podWordBubbles(words,0,10,600,measure);
console.log(JSON.stringify(out.map(b=>({t:b.text,x:b.x,w:b.w}))));
""")
    # 'ab' за 1 с при 60 px/с: времени хватает на текст с зазором → 60 − 4 = 56 (зазор внутри)
    # 'ab' с 5 до 5,1 с: окно 6 px, времени мало → текст 24 px, x=300, влезает
    # 'x' с 20 с — вне окна 0–10 с → не рисуется
    assert res == [{"t": "ab", "x": 0, "w": 56}, {"t": "ab", "x": 300, "w": 24}]


@node
def test_contiguous_speech_draws_every_word_with_gap(tmp_path):
    """Речь без пауз: слова подряд по 0,3 с на окне 5 с при 1280 px (≈77 px на слово).

    Растянутый бабл оставляет зазор внутри своего времени — рисуются все слова, зазоры ≥ 4 px.
    16 слов: последний (4,5–4,8 с) целиком влезает в 1280 px.
    """
    res = _run_node(tmp_path, """
const measure=t=>t.length*6;
const words=[];
for(let i=0;i<16;i++)words.push({w:'слово',start:i*0.3,end:i*0.3+0.3});
const out=podWordBubbles(words,0,5,1280,measure);
let minGap=Infinity;
for(let i=1;i<out.length;i++)minGap=Math.min(minGap,out[i].x-(out[i-1].x+out[i-1].w));
console.log(JSON.stringify({inView:words.length,drawn:out.length,minGap:minGap}));
""")
    assert res["drawn"] == res["inView"], f"растянутый бабл съел соседей: {res}"
    assert res["minGap"] >= 4 - 1e-9, f"зазор меньше 4 px: {res}"


@node
def test_word_bubble_pushes_next_out_instead_of_overlapping(tmp_path):
    """Длинный бабл занимает место: следующее слово, которое ближе 4 px к его правому краю, пропускается."""
    res = _run_node(tmp_path, """
const measure=t=>t.length*6;
// 60 px/с: A 0–1 с, текст 12 символов = 72 px + 12 = 84 px; B стартует в 60 px — ближе 4 px к 84
const words=[{w:'длинноеслово',start:0,end:1},{w:'ab',start:1,end:2}];
const out=podWordBubbles(words,0,10,600,measure);
console.log(JSON.stringify(out.map(b=>({t:b.text,x:b.x,w:b.w}))));
""")
    assert res == [{"t": "длинноеслово", "x": 0, "w": 84}]



# --- статика: каркас окна и заглушка ---------------------------------------------------

PART_B_IDS = ["pod_title", "pod_convert", "pod_stage", "pod_video", "pod_time", "pod_play",
              "pod_segs", "pod_add", "pod_tl"]


def _pod_block() -> str:
    """Окно mbPod целиком: от его backdrop-div до комментария следующего окна."""
    html = _read(INDEX)
    start = html.index('<div class="backdrop" id="mbPod"')
    return html[start:html.index("MODAL: inserts", start)]


def test_index_has_pod_window_with_part_b_ids():
    block = _pod_block()
    assert 'class="backdrop" id="mbPod"' in block
    assert 'aria-label="Куски подкаста"' in block
    assert 'class="modal wide' in block
    for element_id in PART_B_IDS:
        assert f'id="{element_id}"' in block, f"в окне нет id {element_id}"


def test_pod_window_handlers_are_defined_in_js():
    names = set(re.findall(r'on(?:click|change|input)="(\w+)\(', _pod_block()))
    assert names, "в окне нет обработчиков"
    js = _read(EDITOR_JS) + _read(PODCAST_JS)
    missing = [n for n in sorted(names) if not re.search(r"function\s+%s\s*\(" % re.escape(n), js)]
    assert not missing, f"обработчики окна без функции: {missing}"


def test_stub_removed_from_podcast_js_and_editor_defined():
    podcast_js = _read(PODCAST_JS)
    assert "в следующей версии" not in podcast_js
    assert "function openPodcastEditor" not in podcast_js
    assert re.search(r"function openPodcastEditor\(dir\)\{", _read(EDITOR_JS)) or \
        re.search(r"async function openPodcastEditor\(dir\)\{", _read(EDITOR_JS))


def test_editor_registers_keys_and_modal_stack_is_untouched():
    """Клавиши окна — свои, в Escape-стеке 50-chrome.js окно не числится (оно закрывается сами)."""
    src = _read(EDITOR_JS)
    assert "document.addEventListener('keydown',podKey)" in src
    for key in ("' '", "'i'", "'o'"):
        assert key in src, f"клавиша {key} не обработана"
    # резать/удалить — таблица одна на три полосы (tlEditKey в 00-core.js)
    assert "tlEditKey(e)" in src, "окно правки не берёт клавиши резать/удалить из общей таблицы"


# ---------- вёрстка окна правки на узком экране (В2) ----------
# Статика, без Chrome: окно 892×621 раньше давало `.pod-top` фиксированные 299 px, видео
# налезало на таймлайны, список кусков сжимался до 0 px. Проверяем правила в app.css.

CSS = ROOT / "static" / "app.css"


def _css_block(css: str, selector: str) -> str:
    m = re.search(r"(?<![\w.-])%s\{([^}]*)\}" % re.escape(selector), css)
    assert m, f"в app.css нет правила {selector}"
    return m.group(1)


def test_pod_top_has_no_fixed_height():
    block = _css_block(_read(CSS), ".pod-top")
    assert not re.search(r"(?<![\w-])(max-)?height\s*:", block), block


def test_pod_video_is_capped_at_half_viewport_height():
    block = _css_block(_read(CSS), ".pod-video")
    assert re.search(r"max-height\s*:\s*50vh", block), block
    assert "aspect-ratio:16/9" in block.replace(" ", "")


def test_narrow_window_stacks_columns_and_segments_have_natural_height():
    css = _read(CSS)
    media = re.search(r"@media\s*\(\s*max-width\s*:\s*1099px\s*\)\s*\{[^@]*", css)
    assert media, "нет медиа-запроса для окна < 1100 px"
    body = media.group(0)
    assert ".pod-right" in body and "100%" in body, body
    segs = _css_block(css, ".pod-segs")
    assert re.search(r"min-height\s*:\s*120px", segs), segs
    assert not re.search(r"(?<![\w-])height\s*:\s*(?!auto)", segs), segs


@pytest.mark.parametrize(("selector", "height"), [(".pod-tl", "76px")])
def test_timelines_do_not_shrink_in_short_window(selector, height):
    # Окно 892×621: .mbody — колонка flex, и таймлайн без минимума сжимался до 2 px (видна только
    # рамка). «Вся запись» теперь растёт на всё место (flex:1 1 auto), а min-height держит холст
    # не ниже 76 px даже на низком окне.
    raw = _css_block(_read(CSS), selector)
    compact = raw.replace(" ", "")
    assert "flex:1 1 auto" in raw, raw
    assert re.search(r"min-height:%s" % re.escape(height), compact), raw
    assert not re.search(r"(?<![\w-])height:76px", compact), "фиксированная высота 76 px вернулась: %s" % raw


# ---------- «Вся запись» как таймлайн шага 1 (П14): окно, зум, выбор кусков ----------

@node
def test_view_mapping_roundtrip(tmp_path):
    """podX2S — обратное к podS2X: время → пиксели → время без потерь."""
    res = _run_node(tmp_path, """
const v0=12.5,v1=70.25,w=740;
console.log(JSON.stringify([0,12.5,33.3,70.25].map(t=>podX2S(podS2X(t,v0,v1,w),v0,v1,w))));
""")
    assert res == pytest.approx([0, 12.5, 33.3, 70.25], abs=1e-9)


@node
def test_zoom_keeps_time_under_cursor(tmp_path):
    """Зум к курсору: время под курсором (доля a окна) не уезжает. Без привязки к курсору
    (зум всегда от центра или от левого края) тест краснеет — это мутация (1) П14."""
    res = _run_node(tmp_path, """
const cases=[[0,100,1.35,0.2,100],[30,60,2,0.7,200],[0,100,1/1.35,0.9,100]];
console.log(JSON.stringify(cases.map(([v0,v1,f,a,d])=>{
  const r=podZoomSpan(v0,v1,f,a,d);
  return [v0+a*(v1-v0), r.v0+a*(r.v1-r.v0)];
})));
""")
    for before, after in res:
        assert after == pytest.approx(before, abs=1e-9)


@node
def test_view_bounds_min_five_seconds_and_whole_recording(tmp_path):
    res = _run_node(tmp_path, """
console.log(JSON.stringify({
  deep: podZoomSpan(0,100,1000,0.5,100),   // до минимума 5 с, по центру
  out: podZoomSpan(0,50,0.1,0.5,100),      // дальше всей записи — вся запись
  clampLo: podClampView(-5,20,100),        // к нулю
  clampHi: podClampView(90,200,100),       // к концу записи
  short: podClampView(0,2,3),              // запись короче 5 с — окно = вся запись
  tiny: podClampView(50,51,100)            // уже 5 с не делаем
}));
""")
    assert res["deep"] == {"v0": 47.5, "v1": 52.5}
    assert res["out"] == {"v0": 0, "v1": 100}
    assert res["clampLo"] == {"v0": 0, "v1": 25}
    assert res["clampHi"] == {"v0": 0, "v1": 100}
    assert res["short"] == {"v0": 0, "v1": 3}
    assert res["tiny"]["v1"] - res["tiny"]["v0"] == 5


@node
def test_pick_one_toggle_range(tmp_path):
    """podPick: один клик, Ctrl-переключение, Shift-диапазон по порядку start (не по массиву).
    Без сортировки по start (мутация (2) П14) диапазон тест ломает: вернёт ['c','a']."""
    res = _run_node(tmp_path, """
const segs=[{id:'c',start:50},{id:'a',start:10},{id:'b',start:30}];
console.log(JSON.stringify({
  one: podPick(['a','b'],segs,'c','one','a'),
  add: podPick(['a'],segs,'b','toggle','a'),
  remove: podPick(['a','b'],segs,'a','toggle','a'),
  range: podPick([],segs,'c','range','a'),
  rangeBack: podPick([],segs,'a','range','c'),
  noActive: podPick([],segs,'c','range','')
}));
""")
    assert res["one"] == ["c"]
    assert res["add"] == ["a", "b"]
    assert res["remove"] == ["b"]
    assert res["range"] == ["a", "b", "c"]
    assert res["rangeBack"] == ["a", "b", "c"]
    assert res["noActive"] == ["c"]


def test_timeline_is_canvas_inside_pod_tl():
    block = _pod_block()
    assert re.search(r'id="pod_tl"[^>]*>\s*<canvas', block), "#pod_tl должен держать холст"


def test_timeline_draws_waveform_from_audio16k_with_agreed_colours():
    src = _read(EDITOR_JS)
    assert "/api/waveform" in src and "audio16k.wav" in src
    # цвета волны и блоков живут в TL_COL (00-core.js) — общие с редактором шага 1
    core = _read(APP / "00-core.js")
    for colour in ("#6fce9e", "#3ca06e"):
        assert colour in core, f"нет цвета {colour} из редактора шага 1 (TL_COL)"
    assert "TL_COL" in src, "окно подкаста рисует цвета не из TL_COL"
    # плейхед — синий, как у двух других полос времени; жёлтого в рисовании не осталось
    assert "#f5c518" not in src, "жёлтый плейхед вернулся в окно подкаста"


_CORE_STUB = "const document={addEventListener(){},removeEventListener(){},getElementById(){return null}};\n"


def _run_node_core(tmp_path: Path, body: str) -> Any:
    """00-core.js (общие функции таймлайнов) + заглушка DOM + кусок проверки."""
    script = _CORE_STUB + _read(APP / "00-core.js") + "\n" + body + "\n"
    path = tmp_path / "core_timeline_check.js"
    with io.open(str(path), "w", encoding="utf-8") as f:
        f.write(script)
    proc = subprocess.run(["node", str(path)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)
    assert proc.returncode == 0, ("node упал:\n" + (proc.stderr or proc.stdout)).strip()[:1500]
    lines = [ln.strip() for ln in (proc.stdout or "").strip().splitlines() if ln.strip()]
    return json.loads(lines[-1])


@node
def test_tick_step_is_one_function_for_all_timelines(tmp_path):
    """tlTickStep: наименьший шаг из набора подкаста, при котором подписи не теснее 64 css px."""
    res = _run_node_core(tmp_path, """
console.log(JSON.stringify({
  two_hours_1300: tlTickStep(7200, 1300),   // 0.18 px/с: 300 с дают 54 px — мало, 600 с — 108
  s79_1300: tlTickStep(79, 1300),           // 16.5 px/с: 2 с дают 33 px, 5 с — 82
  hour_1300: tlTickStep(3600, 1300),        // 0.36 px/с: 120 с дают 43 px, 300 с — 108
  short_fine: tlTickStep(10, 1300),         // 130 px/с: самый мелкий шаг 0.5 с
  huge: tlTickStep(40000, 1300),            // дальше набора — последний шаг (час)
  label_hour: tlRulerLabel(3723, 7200),
  label_short: tlRulerLabel(65, 100),
  label_hour_zoomed: tlRulerLabel(65, 7200),
  ph_fallback: TL_COL.ph,
  wave_kept: TL_COL.wave
}));
""")
    assert res["two_hours_1300"] == 600
    assert res["s79_1300"] == 5
    assert res["hour_1300"] == 300
    assert res["short_fine"] == 0.5
    assert res["huge"] == 3600
    assert res["label_hour"] == "1:02:03"
    assert res["label_short"] == "1:05"
    # формат подписи зависит от ВСЕЙ записи, а не от окна: при зуме не прыгает
    assert res["label_hour_zoomed"] == "0:01:05"
    assert res["ph_fallback"] == "#6ea8ff"    # без стилей — запасное значение (синий --blue)
    assert res["wave_kept"] == "#6fce9e"


@node
def test_playhead_draws_dark_outline_first_then_blue(tmp_path):
    """tlDrawPlayhead: сначала тёмный штрих и тёмный флажок (шире на 2 css px), потом синие поверх."""
    res = _run_node_core(tmp_path, """
const log = []; const st = {};
const ctx = {
  set strokeStyle(v){ st.stroke = v; }, set fillStyle(v){ st.fill = v; }, set lineWidth(v){ st.lw = v; },
  beginPath(){}, moveTo(){}, lineTo(){}, closePath(){},
  stroke(){ log.push(['stroke', st.stroke, st.lw]); },
  fill(){ log.push(['fill', st.fill]); }
};
tlDrawPlayhead(ctx, 10, 50, 2);
console.log(JSON.stringify(log));
""")
    assert [e[0] for e in res] == ["stroke", "fill", "stroke", "fill"], res
    assert res[0][1] == "#0d0d0d" and res[1][1] == "#0d0d0d", "тёмная подложка должна идти первой"
    assert res[2][1] == "#6ea8ff" and res[3][1] == "#6ea8ff", "синий плейхед — поверх тёмной подложки"
    # при dpr 2 линия шире на 2 css px = 4 канвас-пикселя
    assert res[0][2] - res[2][2] == 4, res


def test_playhead_helper_is_shared_by_both_canvases():
    """Одна функция рисования плейхеда (00-core.js), обе canvas-полосы её зовут."""
    assert "function tlDrawPlayhead(" in _read(APP / "00-core.js")
    assert "tlDrawPlayhead(" in _read(EDITOR_JS), "окно подкаста не зовёт tlDrawPlayhead"
    assert "tlDrawPlayhead(" in _read(APP / "70-editor.js"), "редактор нарезки не зовёт tlDrawPlayhead"


@node
def test_wheel_helpers_pan_on_shift_and_horizontal(tmp_path):
    """Горизонтальное колесо — прокрутка, вертикальное без Shift — зум (не прокрутка)."""
    res = _run_node_core(tmp_path, """
console.log(JSON.stringify({
  horiz: tlWheelIsPan({shiftKey:false,deltaX:7,deltaY:0}),
  vert: tlWheelIsPan({shiftKey:false,deltaX:0,deltaY:3}),
  shift: tlWheelIsPan({shiftKey:true,deltaX:0,deltaY:3}),
  horiz_delta: tlWheelDelta({shiftKey:false,deltaX:7,deltaY:0}),
  shift_delta: tlWheelDelta({shiftKey:true,deltaX:0,deltaY:-3})
}));
""")
    assert res["horiz"] is True
    assert res["vert"] is False
    assert res["shift"] is True
    assert res["horiz_delta"] == 7
    assert res["shift_delta"] == -3


def test_pick_buttons_exist_and_send_ids():
    block = _pod_block()
    assert 'id="pod_del_picked"' in block and 'id="pod_conv_picked"' in block
    src = _read(EDITOR_JS)
    assert "function podDeletePicked" in src and "function podConvertPicked" in src
    assert "ids:ids" in src, "перевод выбранных должен слать ids на /api/podcast_convert"


# ---------- П15: прокси в окне — та же строка, что в превью шага 1; пан средней кнопкой ----------

def _editor_fn(src: str, start: str, end: str) -> str:
    """Тело функции редактора от её объявления до следующего маркера (для статических проверок)."""
    return src[src.index(start):src.index(end, src.index(start) + len(start))]


@node
def test_pan_by_shifts_by_pixels_in_window_units(tmp_path):
    """podPanBy: сдвиг = dx·(v1−v0)/width. Окно 10 с на 100 px: 50 px — это 5 с; длина окна та же."""
    res = _run_node(tmp_path, """
console.log(JSON.stringify({
  right: podPanBy(10,20,50,100,100),     // 50 px вправо → +5 с
  left: podPanBy(10,20,-30,100,100),     // 30 px влево → −3 с
  eight: podPanBy(10,18,20,100,100)      // окно 8 с на 100 px: 20 px = 1.6 с
}));
""")
    assert res["right"] == pytest.approx([15, 25], abs=1e-9)
    assert res["left"] == pytest.approx([7, 17], abs=1e-9)
    assert res["eight"] == pytest.approx([11.6, 19.6], abs=1e-9)


@node
def test_pan_by_stops_at_both_ends_of_recording(tmp_path):
    """Упор в 0 и в длину записи, длина окна не меняется. Без упора (мутация: вместо
    podClampView в podPanBy сырой [v0+ds, v1+ds]) тест краснеет — окно уехало бы в минус."""
    res = _run_node(tmp_path, """
console.log(JSON.stringify({
  toStart: podPanBy(5,15,-1000,100,100),   // далеко влево — упёрлись в 0
  toEnd: podPanBy(80,90,1000,100,100)      // далеко вправо — упёрлись в длину 100
}));
""")
    assert res["toStart"] == pytest.approx([0, 10], abs=1e-9)
    assert res["toEnd"] == pytest.approx([90, 100], abs=1e-9)


def test_proxy_progress_goes_through_the_shared_row():
    """Строка сборки прокси в окне — та же, что у превью шага 1: pvProxyBlock и pvProgDrop.
    Своей разметки (`pvpx_bar`), своей строки «прокси N %» и `#pod_proxy` быть не должно.
    Мутация: вернуть свою строку «прокси N %» вместо pvProxyBlock — тест краснеет."""
    src = _read(EDITOR_JS)
    assert "pvProxyBlock(" in src and "pvProgDrop(" in src
    for banned in ("pvpx_bar", "pvpx_fill", "прокси {p} %", "pod_proxy"):
        assert banned not in src, f"своя строка прокси в окне подкаста: {banned}"
    poll = _editor_fn(src, "async function podProxyTick(", "async function podProxyUse(")
    assert "pvProxyBlock(stage" in poll, "опрос не вызывает общую строку прогресса"
    assert poll.count("pvProgDrop(stage,'proxy')") >= 2, "строка не снимается при готовности и при ошибке"
    assert "toast('⚠ '+errText(s))" in poll, "ошибка сборки должна уходить тостом целиком"


def test_proxy_row_is_dropped_on_close_and_open_and_stage_is_a_container():
    src = _read(EDITOR_JS)
    assert "pvProgDrop($('pod_stage'),'proxy')" in _editor_fn(src, "function podDoClose(", "async function podSave(")
    assert "pvProgDrop($('pod_stage'),'proxy')" in _editor_fn(src, "async function openPodcastEditor(", "// Прокси для браузера")
    block = _pod_block()
    assert re.search(r'<div id="pod_stage" class="pod-stage"><video id="pod_video"', block), \
        "видео окна должно стоять в стойке #pod_stage (position:relative для строки прогресса)"
    assert 'id="pod_proxy"' not in block
    assert "position:relative" in _css_block(_read(CSS), ".pod-stage").replace(" ", "")


def test_middle_button_pans_both_timelines_and_guards_autoscroll():
    """Средняя кнопка: mousedown гасит браузерный autoscroll, auxclick — тоже (как в 70-editor.js),
    захват указателя ставится при pointerdown, тяга зовёт podPanBy."""
    src = _read(EDITOR_JS)
    hook = _editor_fn(src, "function podHook()", "// Тихая смена источника")
    # mousedown и auxclick гасит одна tlMidGuard (00-core.js): «Вся запись» её зовёт
    assert "tlMidGuard(tl)" in hook, "средняя кнопка «Вся запись» не гасит браузерный autoscroll"
    core0 = _read(APP / "00-core.js")
    guard = core0[core0.index("function tlMidGuard("):core0.index("function tlPanning(")]
    assert "'mousedown',e=>{if(e.button===1)e.preventDefault()" in guard, "mousedown средней кнопки"
    assert "'auxclick',e=>{if(e.button===1)e.preventDefault()" in guard, "auxclick средней кнопки"
    assert "podMid(e)" in hook, "средняя кнопка не заведена в pointerdown"
    core = _read(APP / "00-core.js")
    pan = core[core.index("function tlMidPan("):core.index("function tlMidGuard(")]
    assert "setPointerCapture(" in pan, "протяжка не захватывает указатель"
    assert "podPanBy(" in _editor_fn(src, "function podMid(", "// Курсор над плейхедом"), (
        "протяжка окна не зовёт podPanBy")
    assert "'grabbing'" in core, "при пане курсор должен быть grabbing"


def test_left_button_scrubs_playhead_and_empty_space_no_longer_pans():
    """Левая кнопка на плейхеде, линейке и пустом месте — перемотка тягой (kind 'seek'),
    а не сдвиг окна; ew-resize над плейхедом."""
    src = _read(EDITOR_JS)
    assert "kind:'pan'" not in src, "левая кнопка на пустом месте всё ещё двигает окно"
    down = _editor_fn(src, "function podDownTl(", "function podMid(")
    assert "kind:'seek'" in down and "podTlWhere(" in down and "podSeek(" in down
    assert "ew-resize" in src, "над плейхедом нет курсора ew-resize"
    assert "POD_TL_PH" in src


def test_timeline_tooltips_describe_playhead_seek_and_middle_pan():
    block = _pod_block()
    # жесты — одна формулировка на три полосы (00-core.js не разметка: фраза живёт в index.html)
    phrase = "протяжка по полю и по плейхеду — перемотка · протяжка по линейке или средняя кнопка — прокрутка окна"
    assert block.count(phrase) == 1, "жесты «Вся запись» не совпадают с общей формулировкой трёх полос"
    assert "Shift+колесо или пустое место тяни" not in block, "старый тултип про пан пустым местом"


# ---------- K1: одна карточка и один выбор (куски подкаста и вставки); автосохранение ----------

INS_JS = APP / "80-inserts.js"


def _css_rule_with(css: str, marker: str) -> str:
    """Тело правила, в селекторе которого есть marker (правила могут делить селектор-список)."""
    # правило начинается с новой строки: комментарии со ссылкой на тот же класс не в счёт
    m = re.search(r"\n[^\n{}/*]*%s[^\n{}]*\{([^}]*)\}" % re.escape(marker), css)
    assert m, f"в app.css нет правила с селектором {marker}"
    return m.group(1)


def test_edcard_base_and_blue_selection_in_css():
    """Общая карточка `.edcard` и выбор `.sel`: синяя рамка и синяя подсветка фона сзади."""
    css = _read(CSS)
    assert ".edcard" in css, "нет общего класса карточки .edcard"
    sel = _css_rule_with(css, ".edcard.sel").replace(" ", "")
    assert "border-color:var(--blue)" in sel, sel
    assert "rgba(110,168,255,.08)" in sel, sel
    assert "border-left-color:var(--blue)" in sel, "у выбранной полоска должна быть синей"


def test_playing_card_is_blue_not_yellow():
    """У выбранной вставки (класс .playing из предпросмотра) — синий, жёлтого не осталось."""
    playing = _css_rule_with(_read(CSS), ".inscard.playing")
    assert "var(--yel)" not in playing and "#f5c518" not in playing, playing
    assert "var(--blue)" in playing.replace(" ", ""), playing


def test_chosen_is_only_a_strip_colour():
    """Зелёная («файл выбран осознанно») — только полоска: рамку и фон не красит."""
    chosen = _css_rule_with(_read(CSS), ".inscard.chosen").replace(" ", "")
    assert "border-left-color:var(--ok)" in chosen
    assert "border-color:" not in chosen and "background" not in chosen, chosen


def test_cards_of_inserts_and_segments_share_edcard_class():
    ins = _read(INS_JS)
    assert "'edcard inscard '" in ins, "карточка вставки не получила класс edcard"
    podjs = _read(EDITOR_JS)
    assert "'podseg edcard'" in podjs, "строка куска не получила класс edcard"
    # выбор у кусков — тот же .sel, что у карточек вставок (.playing ведёт предпросмотр)
    assert "' sel'" in podjs


def test_insert_delete_is_trash_button_not_cross():
    """Удаление вставки — мусорка в button.icon (как у кусков), крестика в карточке не осталось."""
    ins = _read(INS_JS)
    line = next(ln for ln in ins.splitlines() if "insDel('+i+')" in ln)
    assert "ico('trash')" in line and "<button" in line, line
    assert "ico('x')" not in line, line
    assert "iconBtn('trash'" in _read(EDITOR_JS), "у куска нет мусорки"


def test_pod_window_has_no_save_button_and_panel_order():
    """«Сохранить» убрано (правки сохраняются сами); «+ Кусок» спереди, ↶ ↷ справа."""
    block = _pod_block()
    assert 'id="pod_save"' not in block and "podSave()" not in block
    order = [block.index(f'id="{x}"') for x in
             ("pod_add", "pod_conv_picked", "pod_del_picked", "podUndoBtn", "podRedoBtn")]
    assert order == sorted(order), "порядок панели кусков: + Кусок, в рилсы, удалить, ↶, ↷"
    assert 'id="pod_pick_n"' not in block, "счётчик «выбрано N из M» должен уйти"


def test_pod_undo_keys_and_no_leave_dialog_left():
    src = _read(EDITOR_JS)
    key = _editor_fn(src, "function podKey(", "document.addEventListener('keydown',podKey)")
    assert "podUndo()" in key and "podRedo()" in key
    # клавиши (и русская раскладка «я»/«н») — в общей histKey: подкаст её не дублирует
    assert "histKey(e)" in key, "подкаст держит свою таблицу клавиш отмены"
    core = _read(APP / "00-core.js")
    hk = core[core.index("function histKey("):core.index("function histKey(") + 400]
    assert "'я'" in hk and "'н'" in hk, "русская раскладка Ctrl+Z/Ctrl+Y пропала из histKey"
    assert "Закрыть без сохранения" not in src and "askConfirm" not in src, \
        "вопрос «Закрыть без сохранения?» и подтверждение удаления должны уйти"


_POD_STUB = (
    "const document={addEventListener(){},removeEventListener(){},getElementById(){return null}};\n"
    "const toast=()=>{};\n"
    "const podcastList=()=>{}; const openModal=()=>{}; const closeModal=()=>{};\n"
    "const pvProgDrop=()=>{}; const pvProxyBlock=()=>{};\n"
)

_POD_TAIL = (
    "const TIMERS=[]; const CALLS=[]; const FAILS=[];\n"
    "globalThis.setTimeout=(fn,ms)=>{TIMERS.push({fn,ms});return TIMERS.length;};\n"
    "globalThis.clearTimeout=(id)=>{if(TIMERS[id-1])TIMERS[id-1].fn=null;};\n"
    "globalThis.fetch=(url,opt)=>{const b=JSON.parse(opt.body);CALLS.push({url,b});\n"
    "  const r=FAILS.length?FAILS.shift():{ok:true,project:{segments:b.segments}};\n"
    "  return Promise.resolve({json:()=>Promise.resolve(r)});};\n"
    "const live=()=>TIMERS.filter(x=>x.fn&&x.ms===600);\n"
)


def _run_node_pod(tmp_path: Path, body: str) -> Any:
    """Редактор + заглушки окна и таймеров. body — async-код; последняя строка stdout — JSON."""
    script = (_POD_STUB + _read(APP / "00-core.js") + "\n" + _read(EDITOR_JS) + "\n" + _POD_TAIL
              + "(async()=>{\n" + body + "\n})().then(r=>console.log(JSON.stringify(r)));\n")
    path = tmp_path / "pod_undo_check.js"
    with io.open(str(path), "w", encoding="utf-8") as f:
        f.write(script)
    proc = subprocess.run(["node", str(path)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)
    assert proc.returncode == 0, ("node упал:\n" + (proc.stderr or proc.stdout)).strip()[:1500]
    lines = [ln.strip() for ln in (proc.stdout or "").strip().splitlines() if ln.strip()]
    assert lines, "node ничего не вывел"
    return json.loads(lines[-1])


_ONE_SEG = ("POD.dir='/p';POD.open=true;POD.dur=300;POD.words=[];POD.picked=[];"
            "POD.segs=[{id:'a',start:10,end:30,title:'',why:'',reel:''}];POD.sel='a';POD_HIST.reset();")


@node
def test_pod_undo_redo_add_segment(tmp_path):
    """Добавить кусок → ↶ → куска нет → ↷ → есть."""
    res = _run_node_pod(tmp_path, _ONE_SEG + """
  const before=POD.segs.length;
  podAdd();
  const added=POD.segs.length;
  podUndo();
  const undone=POD.segs.length;
  podRedo();
  const redone=POD.segs.length;
  live().forEach(x=>x.fn());                   // сохранили текущее (два куска) ...
  await POD.saveChain;
  const saves1=CALLS.filter(c=>c.url==='/api/podcast_save').length;
  podUndo();                                   // ... и откат тоже должен уйти на сервер (один кусок)
  live().forEach(x=>x.fn());
  await POD.saveChain;
  const saves=CALLS.filter(c=>c.url==='/api/podcast_save');
  return {before,added,undone,redone,saves1,saves2:saves.length,
          savedAfterUndo:saves[saves.length-1].b.segments.length};
""")
    assert res == {"before": 1, "added": 2, "undone": 1, "redone": 2,
                   "saves1": 1, "saves2": 2, "savedAfterUndo": 1}, res


@node
def test_pod_drag_is_one_history_step(tmp_path):
    """Тяга края: 50 движений — один шаг истории; ↶ возвращает край к началу жеста.
    Снимок на каждое движение (мутация: podRefresh пишет историю) — тест краснеет: шагов было бы 50."""
    res = _run_node_pod(tmp_path, _ONE_SEG + """
  POD.drag={id:'a',zone:'tl',kind:'l',t0:0,moved:false,orig:{start:10,end:30}};
  for(let k=1;k<=50;k++){POD.segs[0].start=10+k*0.1;POD.drag.moved=true;podRefresh();}
  podUp();
  const hadStep=POD_HIST.canUndo();
  const moved=POD.segs[0].start;
  podUndo();
  return {hadStep,stepsLeft:POD_HIST.canUndo(),moved,back:POD.segs[0].start};
""")
    # один шаг: после отката шагов не осталось; снимок на каждое движение оставил бы ещё
    assert res["hadStep"] is True and res["stepsLeft"] is False, res
    assert res["moved"] == pytest.approx(15, abs=1e-9)
    assert res["back"] == 10


@node
def test_pod_autosave_one_request_after_600ms(tmp_path):
    """После правки — ровно один /api/podcast_save через 600 мс; частые правки дают один запрос."""
    res = _run_node_pod(tmp_path, _ONE_SEG + """
  podAdd();
  POD.segs[0].title='x';podEdited();       // вторая правка до срабатывания — таймер перезаводится
  const pend=live();
  const delays=pend.map(x=>x.ms);
  pend.forEach(x=>x.fn());
  await POD.saveChain;
  const saves=CALLS.filter(c=>c.url==='/api/podcast_save');
  return {n:pend.length,delays,saves:saves.length,dirty:POD.dirty,
          segs:saves[0]?saves[0].b.segments.length:0};
""")
    assert res["n"] == 1, res
    assert res["delays"] == [600]
    assert res["saves"] == 1
    assert res["dirty"] is False
    assert res["segs"] == 2


@node
def test_pod_save_error_keeps_edit_and_retries_with_next(tmp_path):
    """Сервер отверг — тост, правка в окне остаётся; следующая правка уходит вместе с ней."""
    res = _run_node_pod(tmp_path, _ONE_SEG + """
  FAILS.push({error:'overlap'});
  podAdd();
  live().forEach(x=>x.fn());
  await POD.saveChain;
  const afterError={dirty:POD.dirty,segs:POD.segs.length};
  POD.segs[0].title='y';podEdited();
  live().forEach(x=>x.fn());
  await POD.saveChain;
  const saves=CALLS.filter(c=>c.url==='/api/podcast_save');
  return {afterError,saves:saves.length,last:saves[1].b.segments.length,
          lastTitle:saves[1].b.segments[0].title,dirty:POD.dirty};
""")
    assert res["afterError"] == {"dirty": True, "segs": 2}, res
    assert res["saves"] == 2
    assert res["last"] == 2 and res["lastTitle"] == "y"
    assert res["dirty"] is False


@node
def test_pod_close_waits_for_pending_save(tmp_path):
    """Закрытие: отложенное сохранение уходит сначала и только потом окно закрывается."""
    res = _run_node_pod(tmp_path, _ONE_SEG + """
  podAdd();
  podClose();
  const openWhileSaving=POD.open;
  for(let i=0;i<10;i++)await Promise.resolve();
  await POD.saveChain;
  for(let i=0;i<10;i++)await Promise.resolve();
  return {openWhileSaving,openAfter:POD.open,saves:CALLS.filter(c=>c.url==='/api/podcast_save').length};
""")
    assert res == {"openWhileSaving": True, "openAfter": False, "saves": 1}, res


@node
def test_pod_close_stays_open_when_server_refuses(tmp_path):
    """Сервер не принял при закрытии — окно не закрываем, правки не теряем."""
    res = _run_node_pod(tmp_path, _ONE_SEG + """
  FAILS.push({error:'overlap'});
  podAdd();
  podClose();
  for(let i=0;i<10;i++)await Promise.resolve();
  await POD.saveChain;
  for(let i=0;i<10;i++)await Promise.resolve();
  return {open:POD.open,dirty:POD.dirty,segs:POD.segs.length};
""")
    assert res == {"open": True, "dirty": True, "segs": 2}, res


# ---------- P1t: одна полоса слов на «Вся запись»; правка кусков как в редакторе нарезки ----------

def test_no_words_strip_left_in_podcast_window():
    """Полосы «Слова» (#pod_zoom) больше нет: слова — строка на «Вся запись»."""
    block = _pod_block()
    assert 'id="pod_zoom"' not in block and "Слова</span>" not in block
    assert "pod_zoom" not in _read(EDITOR_JS)


def test_header_order_cut_delete_left_zoom_right():
    """Шапка «Вся запись»: [подпись][!][Резать][Удалить][grow][+][−][⛶] — Резать и Удалить слева."""
    block = _pod_block()
    head = block[block.index('<div class="pod-tlhead tlhead">'):block.index('id="pod_tl"')]
    order = re.findall(r'<span class="edlbl">|<span class="i"|id="pod_cut"|id="pod_del"|<span class="grow"></span>'
                       r'|aria-label="Приблизить"|aria-label="Отдалить"|aria-label="Показать всю запись"', head)
    assert order == ['<span class="edlbl">', '<span class="i"', 'id="pod_cut"', 'id="pod_del"',
                     '<span class="grow"></span>', 'aria-label="Приблизить"', 'aria-label="Отдалить"',
                     'aria-label="Показать всю запись"'], order
    assert 'onclick="podCut()"' in head and 'onclick="podDelKey()"' in head
    assert 'data-ic="cut"' in head and 'data-ic="trash"' in head
    assert head.count('class="sm"') == 2 and head.count('class="icon"') == 3
    assert "\u2702" not in block and "\U0001F5D1" not in block, "эмодзи в хроме интерфейса запрещены"


def test_double_click_on_empty_timeline_adds_segment():
    """Двойной клик по пустому месту «Вся запись» — podDblTl: место через podGapAt, без старого zoom-fit."""
    src = _read(EDITOR_JS)
    hook = _editor_fn(src, "function podHook()", "// Тихая смена источника")
    assert "tl.addEventListener('dblclick',podDblTl)" in hook
    assert "podZoomFit()" not in hook, "двойной клик больше не переключает на «вся запись»"
    dbl = _editor_fn(src, "function podDblTl(", "// Копия массива")
    assert "podGapAt(" in dbl and "podTlHit(" in dbl and "podRefresh()" in dbl


def test_keys_c_and_d_are_bound_with_cyrillic_twins():
    src = _read(EDITOR_JS)
    key = _editor_fn(src, "function podKey(", "document.addEventListener('keydown',podKey)")
    assert "podCut()" in key and "podDelKey()" in key and "tlEditKey(e)" in key
    core = _read(APP / "00-core.js")
    te = core[core.index("function tlEditKey("):core.index("function tlEditKey(") + 500]
    assert "'с'" in te and "'в'" in te and "'Delete'" in te and "'Backspace'" in te, (
        "C/D в русской раскладке и Delete/Backspace пропали из tlEditKey")


def test_edge_snap_on_release_applies_on_the_timeline():
    """Прилипание при отпускании — у краёв куска (середину не трогаем), без привязки к зоне."""
    src = _read(EDITOR_JS)
    up = _editor_fn(src, "function podUp(", "// --- плеер ---")
    assert "d.kind!=='m'" in up and "podSnap(s,POD.words,'start')" in up and "podSnap(en,POD.words,'end')" in up
    assert "d.zone" not in up, "прилипание не должно зависеть от зоны"


def test_words_are_drawn_on_timeline_canvas_in_shared_window():
    src = _read(EDITOR_JS)
    body = _editor_fn(src, "function podRenderTl()", "// Пики волны всей записи")
    assert "POD.words" in body and "podWordBubbles(" in body and "podRoundRect(" in body
    assert "POD_TL_RIBBON" in src and "POD_BUBBLE_PAD" in src


def test_no_word_ticks_on_timeline_canvas():
    """Чёрточек слов нет: ни тонкой риски-прямоугольника, ни ветки «podWordsVisible»."""
    src = _read(EDITOR_JS)
    body = _editor_fn(src, "function podRenderTl()", "// Пики волны всей записи")
    assert "podWordsVisible" not in src and "POD_WORD_MIN_PX" not in src
    assert "fillRect(Math.round(x0)" not in body, "риска слова осталась на canvas"


def test_segment_title_css_takes_free_width():
    """Название — всё свободное место строки (basis 0), минимум 80 px, обрезка «…» с полным текстом
    в title. Хвостовой группы и контейнерного переноса больше нет: строка куска одна."""
    css = _read(CSS)
    rule = _css_block(css, ".podseg-title")
    assert "flex:1 1 0" in rule and "min-width:80px" in rule, rule
    assert "overflow:hidden" in rule and "text-overflow:ellipsis" in rule, rule
    assert "podseg-tail" not in css, "хвостовая группа строки куска должна уйти из CSS"
    assert ".pod-right{container-type" not in css, "контейнерный запрос строки куска должен уйти"


def test_segment_row_is_one_line_without_tail_group():
    src = _read(EDITOR_JS)
    body = _editor_fn(src, "function podRenderSegs()", "// Клик по строке/блоку")
    assert "podseg-tail" not in body
    assert "row.append(cb,n,title,podTimeInput(sg,'start'),podTimeInput(sg,'end'),dur," in body


def test_segment_card_has_no_colored_stripe():
    """Карточка куска — как карточка вставки: без цветной полоски слева (владелец 2026-10-12).
    Рамка — общая var(--bd2), выбор — синий (.sel)."""
    css = _read(CSS)
    rules = [m for m in re.finditer(r"([^{}]*podseg[^{}]*)\{([^}]*)\}", css)]
    assert rules, "нет правил строки куска в app.css"
    for m in rules:
        body = m.group(2)
        assert "border-left-color" not in body, f"у {m.group(1).strip()} цветная полоска: {body}"
        assert "#3ca06e" not in body, f"у {m.group(1).strip()} зелёный цвет: {body}"
    assert ".edcard.sel,.inscard.playing{border-color:var(--blue)" in css.replace(" ", "")


_SANDBOX_ERR = ("Access is denied", "crash server failed to launch", "platform_channel")
_CHROME = (shutil.which("chrome") or shutil.which("chrome.exe")
           or r"C:\Program Files\Google\Chrome\Application\chrome.exe")
_NO_CHROME = pytest.mark.skipif(not os.path.exists(_CHROME), reason="нет Chrome для замера геометрии")


def _run_chrome_dump(tmp_path: Path, name: str, page: str, width: int, height: int):
    """Страница в безголовом Chrome; dump-dom → JSON из <pre id="geom">. None — Chrome не стартует
    в этой песочнице (mojo/named pipes): тест пропускается с причиной."""
    p = tmp_path / name
    p.write_text(page, encoding="utf-8")
    cmd = [_CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
           "--window-size=%d,%d" % (width, height), "--virtual-time-budget=3000",
           "--dump-dom", p.resolve().as_uri()]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=60)
    if proc.returncode != 0:
        if any(e in proc.stderr for e in _SANDBOX_ERR):
            return None
        raise AssertionError("Chrome не отработал: %s" % proc.stderr[:400])
    m = re.search(r'<pre id="geom">(.*?)</pre>', proc.stdout, re.DOTALL)
    assert m, "нет замеров в выводе Chrome: %s" % proc.stdout[:400]
    return json.loads(m.group(1))


_SEG_ROW = ('<div class="podseg edcard"><input type="checkbox" class="podseg-cb">'
            '<span class="podseg-n mono muted">%d</span>'
            '<input type="text" class="podseg-title" value="Что важно знать о здоровье и сне">'
            '<input type="text" class="pod-time mono" value="00:10.0">'
            '<input type="text" class="pod-time mono" value="01:10.0">'
            '<span class="podseg-dur mono muted">01:00.0</span>'
            '<button type="button" class="icon"><svg class="ic" viewBox="0 0 24 24"></svg></button>'
            '<button type="button" class="icon"><svg class="ic" viewBox="0 0 24 24"></svg></button></div>')


def _segment_row_geometry(tmp_path: Path, width: int):
    """Строка куска в настоящем Chrome с app.css: панель шириной width, замеры — в <pre id="geom">."""
    css_uri = (ROOT / "static" / "app.css").resolve().as_uri()
    panel = '<div class="pod-right" style="width:%dpx"><div class="pod-segs">%s</div></div>' % (width, _SEG_ROW % 1)
    script = (
        'var q=function(s){return document.querySelector(s).getBoundingClientRect();};'
        'var row=q(".podseg"), title=q(".podseg-title"), time=q(".pod-time"), dur=q(".podseg-dur");'
        'var b=document.querySelectorAll(".podseg button.icon"), last=b[b.length-1].getBoundingClientRect();'
        'var out={rowH:row.height,rowTop:row.top,rowRight:row.right,title:title.width,'
        'titleTop:title.top,timeTop:time.top,durTop:dur.top,lastRight:last.right};'
        'document.getElementById("geom").textContent=JSON.stringify(out);')
    page = ('<!doctype html><html><head><meta charset="utf-8">'
            '<link rel="stylesheet" href="%s"></head><body style="margin:0">%s'
            '<pre id="geom"></pre>'
            '<script>window.addEventListener("load",function(){%s});</script></body></html>') % (css_uri, panel, script)
    return _run_chrome_dump(tmp_path, "seg_row_%d.html" % width, page, width + 100, 600)


@_NO_CHROME
def test_segment_row_is_one_line_in_556px_panel(tmp_path):
    """Панель 556 px (владелец): строка куска в одну строку (высота ≤ 44 px), название ≥ 80 px,
    все кнопки внутри строки. Мутация: вернуть перенос хвоста на вторую строку — высота вырастет."""
    geo = _segment_row_geometry(tmp_path, 556)
    if geo is None:
        pytest.skip("Chrome не запускается в этой песочнице (mojo/named pipes) — замер не снят")
    assert geo["rowH"] <= 44, "строка куска больше одной строки: %r" % (geo,)
    assert geo["title"] >= 80, "название куска уже 80 px: %r" % (geo,)
    assert geo["timeTop"] >= geo["rowTop"] and geo["timeTop"] < geo["rowTop"] + geo["rowH"], geo
    assert geo["lastRight"] <= geo["rowRight"] + 0.5, "кнопки вылезают за строку: %r" % (geo,)


# --- «Вся запись» на всю высоту окна (П23): Chrome-стенд на настоящей разметке и CSS ---

def _pod_window_page(rows: int) -> str:
    """Окно mbPod из index.html (вырезано, не скопировано) с настоящим app.css; rows — строк кусков."""
    html = _read(INDEX)
    tail = html[html.index('<div class="backdrop" id="mbPod"'):]
    end = tail.index("<!-- ============ MODAL: inserts")
    block = tail[:tail.rindex("</div>", 0, end) + len("</div>")]
    block = block.replace('class="backdrop" id="mbPod"', 'class="backdrop on" id="mbPod"', 1)
    block = re.sub(r'<span data-ic="[^"]*"[^>]*></span>', '<svg class="ic" viewBox="0 0 24 24"></svg>', block)
    segs = "".join(_SEG_ROW % (i + 1) for i in range(rows))
    block = block.replace('<div id="pod_segs" class="pod-segs"></div>',
                          '<div id="pod_segs" class="pod-segs">%s</div>' % segs, 1)
    css_uri = (ROOT / "static" / "app.css").resolve().as_uri()
    script = (
        'var g=function(s){var el=document.querySelector(s);var r=el.getBoundingClientRect();'
        'return {top:r.top,bottom:r.bottom,height:r.height,width:r.width,clientH:el.clientHeight,scrollH:el.scrollHeight};};'
        'var res={vh:window.innerHeight,modal:g("#mbPod .modal"),body:g("#mbPod .mbody"),'
        'top:g("#mbPod .pod-top"),head:g("#mbPod .pod-tlhead"),tl:g("#pod_tl"),cv:g("#pod_tl_cv"),'
        'page:{sh:document.documentElement.scrollHeight,ch:document.documentElement.clientHeight}};'
        'document.getElementById("geom").textContent=JSON.stringify(res);')
    return ('<!doctype html><html><head><meta charset="utf-8">'
            '<link rel="stylesheet" href="%s"><style>html,body{margin:0;padding:0;width:100%%;height:100%%}'
            '#mbPod .modal{animation:none !important}'
            '#geom{position:absolute;left:0;top:0;width:0;height:0;overflow:hidden}</style></head>'
            '<body>%s<pre id="geom"></pre><script>window.addEventListener("load",function(){%s});</script>'
            '</body></html>') % (css_uri, block, script)


_CHROME_UI_H = 91   # рамка окна Chrome (адресная строка, вкладки): --window-size задаёт окно, не видимую область


def _pod_window_geometry(tmp_path: Path, width: int, height: int, rows: int):
    """Видимая область — width×height (проверяется по innerHeight в тесте), окно Chrome — на рамку выше."""
    return _run_chrome_dump(tmp_path, "pod_window_%dx%d_%d.html" % (width, height, rows),
                            _pod_window_page(rows), width, height + _CHROME_UI_H)


@_NO_CHROME
@pytest.mark.parametrize(("width", "height", "rows"), [(1373, 849, 0), (1373, 849, 5), (1413, 888, 0)])
def test_pod_timeline_takes_the_rest_of_window_height(tmp_path, width, height, rows):
    """«Вся запись»: холст ≥ 200 px на 1373×849 (с 0 и с 5 кусками) и на 1413×888; тело окна не
    прокручивается. Мутация: вернуть фиксированные 76 px у .pod-tl — холст станет 76, тест краснеет."""
    m = _pod_window_geometry(tmp_path, width, height, rows=rows)
    if m is None:
        pytest.skip("Chrome не запускается в этой песочнице (mojo/named pipes) — замер не снят")
    assert abs(m["vh"] - height) <= 2, "стенд снял видимую область %s вместо %s: %r" % (m["vh"], height, m)
    assert m["cv"]["height"] >= 200, "холст «Вся запись» не растянулся по высоте окна: %r" % (m,)
    assert m["body"]["scrollH"] <= m["body"]["clientH"] + 1, "тело окна прокручивается: %r" % (m["body"],)
    assert m["modal"]["height"] >= 0.9 * m["vh"], "окно не занимает экран: %r" % (m["modal"],)
    assert m["page"]["sh"] <= m["page"]["ch"] + 1, "страница прокручивается: %r" % (m["page"],)


@_NO_CHROME
def test_pod_timeline_keeps_76px_minimum_on_low_window(tmp_path):
    """Низкое окно (≤ 620 px): холст не ниже 76 px, тело окна прокручивается как раньше."""
    m = _pod_window_geometry(tmp_path, 1000, 600, rows=3)
    if m is None:
        pytest.skip("Chrome не запускается в этой песочнице (mojo/named pipes) — замер не снят")
    # 76 px — высота рамки таймлайна (box-sizing: border-box), холст внутри неё на 2 px меньше
    assert m["tl"]["height"] >= 75.5, "таймлайн «Вся запись» ниже минимума 76 px: %r" % (m["tl"],)


@node
def test_ruler_labels_have_tenths_under_one_second_step_and_never_clash(tmp_path):
    """tlRulerLabel: при шаге 0,5 с подписи с десятыми и уникальны; при 5 с — как раньше, без десятых;
    соседние подписи не совпадают ни на одном окне из проверочного набора."""
    res = _run_node_core(tmp_path, """
const out = {};
const st6 = tlTickStep(6, 1300);
out.step6 = st6;
out.labels6 = [];
for(let k = 0; k * st6 <= 6 + 1e-6; k++) out.labels6.push(tlRulerLabel(k * st6, 6, st6));
const st79 = tlTickStep(79, 1300);
out.step79 = st79;
out.labels79 = [];
for(let k = 0; k * st79 <= 79 + 1e-6; k++) out.labels79.push(tlRulerLabel(k * st79, 79, st79));
out.hour_fine = tlRulerLabel(75.5, 7200, 0.5);
out.no_step = tlRulerLabel(75.5, 100);
let clash = 0;
for(const span of [0.7, 1, 3, 6, 12, 30, 79, 200, 600, 3000, 7200]){
  const st = tlTickStep(span, 1300);
  let prev = null;
  for(let k = 0; k * st <= span + 1e-6; k++){
    const lb = tlRulerLabel(k * st, 7200, st);
    if(lb === prev) clash++;
    prev = lb;
  }
}
out.clash = clash;
console.log(JSON.stringify(out));
""")
    assert res["step6"] == 0.5 and res["step79"] == 5
    assert res["labels6"][:4] == ["0:00.0", "0:00.5", "0:01.0", "0:01.5"], res["labels6"]
    assert len(set(res["labels6"])) == len(res["labels6"]), "подписи окна 6 с повторяются"
    assert all("." in lb for lb in res["labels6"]), "при шаге 0,5 с нет десятых"
    assert all("." not in lb for lb in res["labels79"]), "при шаге 5 с десятые лишние"
    assert len(set(res["labels79"])) == len(res["labels79"])
    assert res["hour_fine"] == "0:01:15.5"
    assert res["no_step"] == "1:15"
    assert res["clash"] == 0, "соседние подписи совпали"


def _ruler_label_calls():
    """Все вызовы tlRulerLabel( в static/app/*.js (объявление не считается) с аргументами верхнего уровня."""
    found = []
    for path in sorted(APP.glob("*.js")):
        src = _read(path)
        for m in re.finditer(r"tlRulerLabel\(", src):
            if src[max(0, m.start() - 9):m.start()].endswith("function "):
                continue
            depth, args, cur, j = 1, [], "", m.end()
            while True:
                ch = src[j]
                if ch in "([{":
                    depth += 1
                elif ch in ")]}":
                    depth -= 1
                    if depth == 0:
                        break
                if ch == "," and depth == 1:
                    args.append(cur)
                    cur = ""
                else:
                    cur += ch
                j += 1
            args.append(cur)
            found.append((path.name, src.count("\n", 0, m.start()) + 1, [a.strip() for a in args]))
    return found


def test_every_ruler_label_call_passes_the_step():
    """Три таймлайна (редактор подкаста, редактор нарезки, превью вставок) передают шаг рисок третьим
    аргументом: без него подписи при шаге 0,5 с снова совпадают соседними. Мутация: убрать `,st` в
    любом вызове — тест краснеет и называет файл и строку."""
    calls = _ruler_label_calls()
    # редактор и «Вся запись» — одна tlPaintRuler в 00-core.js; превью вставок — свой itlDraw
    assert len(calls) >= 2, "вызовов tlRulerLabel меньше двух: %r" % (calls,)
    for name, line, args in calls:
        assert len(args) == 3 and all(args), "%s:%d tlRulerLabel без шага: %r" % (name, line, args)


@node
def test_pod_timeline_grab_edge_beats_playhead_same_order_as_editor(tmp_path):
    """«Вся запись»: край в 3 px и плейхед в 2 px — берётся край; один плейхед в допуске — плейхед.

    Порядок общий с редактором шага 1 (tlHitOrder, 00-core.js): край > плейхед > линейка > середина.
    Холст 1000 px на окно 0–100 с: 1 с = 10 px. Край куска (10 с) — x=100, плейхед — x=105 (10.5 с).
    """
    res = _run_node_pod(tmp_path, _ONE_SEG + """
POD.v0=0;POD.v1=100;
podTlCanvas=()=>({getBoundingClientRect:()=>({left:0,top:0,width:1000,height:200})});
podNow=()=>10.5;
const edgeWins=podTlWhere(103,50);     // край в 3 px, плейхед в 2 px
podNow=()=>50;
const headOnly=podTlWhere(502,50);     // плейхед в допуске, краёв рядом нет
podNow=()=>10.5;
const ruler=podTlWhere(200,5);         // внутри куска, у верхней линейки: линейка важнее середины
const mid=podTlWhere(200,50);          // внутри куска, без линейки и плейхеда — середина
return {edgeWins,headOnly,ruler,mid};
""")
    assert res["edgeWins"] == {"id": "a", "kind": "l"}, res
    assert res["headOnly"] == {"id": None, "kind": "ph"}, res
    assert res["ruler"] == {"id": None, "kind": "ruler"}, res
    assert res["mid"] == {"id": "a", "kind": "m"}, res
