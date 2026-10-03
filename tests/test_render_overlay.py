# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Кадр рендера без After Effects: что в него НЕ попадает и как выглядит мозаика.

Страница рендера (`/render`) грузит ТЕ ЖЕ `static/app/*.js`, что играет предпросмотр
шага 3: второй копии отрисовки нет и быть не должно. Обратная сторона этого — в кадр
может попасть то, чего в ролике нет: элементы ПРАВКИ превью. Живой случай владельца:
под текстом интро в готовом ролике нашлась черта с ручкой масштаба интро (и то же
гнездо — маркер точки наезда и полоса подсказки рото).

Что стережётся здесь:

1. **Элементы правки в режиме рендера.** Класс `render-mode` вешает `ipvRenderAt`;
   в CSS у каждого элемента правки есть `display:none`, а часть их в этом режиме
   вообще не создаётся (ручка масштаба, маркер наезда). Обратная проверка тоже тут:
   в живом превью ручка на месте — иначе «починили» бы удалением правки.
2. **Мозаика вставки — пикселизация, а не размытие.** В AE видеовставка с галкой
   «мозаика» закрыта эффектом Mosaic (64×64), у нас был `filter:blur(10px)` — владелец
   видел размытие там, где в AE мозаика. Плюс размытие гасило пиксели у кромки коробки,
   и по краю кадра шла тёмная полоса. Проверяется и число блоков (арифметика свода),
   и то, что размер блока — тот же 64, что уезжает в `.jsx`.
3. **Тайминги открытия слова интро.** Анимация `reveal` открывает слово СЛЕВА НАПРАВО,
   как Percent Offset селектора в AE: буква показывается на своём месте в слове, а само
   появление играет F_DUR (0.3 с), а не длительность глитча. Раньше буквы проявлялись
   «россыпью» по хешу от номера кадра, да ещё по глитчевой длительности: на 3.0 с
   последняя буква ещё не показывалась — в AE уже «КУДА», у нас «КУД» (кадр владельца).
4. **Раскрытие ВИДНО с первого кадра, а счётчик играет HL_DUR.** Живой прогон: на 1.0 и
   3.0 с буквы раскрытия у нас не были видны вовсе (в AE они уже растут масштабом), а на
   2.5 с счётчик показывал 10 вместо 19. Причина первой — правило `.ipvintro span{opacity:0}`
   (класс `on` стоит на СЛОВЕ, а не на букве), второй — свои 1.5 с по кривой вместо
   линейных ключей слайдера `t0 … t0+HL_DUR·SQ` из `.jsx`. Обе проверки идут на ТЕЛЕ
   живого клипа (группы интро из плана сцены) и обе — с мутацией.

Числа плана для проверок 3 и 4 — из ЖИВОГО клипа владельца (C1476): слово «КУДА» начинает
появляться на 2.77 с, множитель сжатия 0.5847, кадр сверки — 3.0 с (раскрытие), «19» — на
2.1 с со сжатием 0.8482, кадр счётчика — 2.5 с. Это те самые числа, на которых дефекты и
были видны, а не выдуманные для теста.

Запуск: python -m pytest tests/test_render_overlay.py -q
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

JS85 = os.path.join(ROOT, "static", "app", "85-inserts-view.js")
JS95 = os.path.join(ROOT, "static", "app", "95-styles.js")
CSS = os.path.join(ROOT, "static", "app.css")
TEMPLATE = os.path.join(ROOT, "core", "xml2ae", "template.py")
node = pytest.mark.skipif(not shutil.which("node"), reason="контракт фронта требует node в PATH")

W, H, FPS = 1080, 1920, 60
# Живой клип владельца: группа интро с «КУДА ТЫ ЛЕЗЕШЬ», слово «КУДА» — на 2.77 с,
# появление слова 0.3 с (F_DUR — им же играют фейд, масштаб и up/left/right),
# кадр сверки — 3.0 с.
#
# Множитель сжатия появления в плане лежит ПО СЛОВАМ строки: у «КУДА» его нет (слово
# успевает доиграть до затухания группы), а 0.5847 — множитель «ЛЕЗЕШЬ». Именно на этом
# и разъехалась прошлая проверка: она считала ход «КУДА» с чужим числом (0.5847), у
# которого ход на 3.0 с выходил 1.31, и «все буквы стоят» было верно только на бумаге.
WORD_T, APPEAR_DUR, CHECK_T = 2.77, 0.3, 3.0
WORD_SQ = 1.0            # у «КУДА» сжатия нет — оно играет полные F_DUR
NEIGHBOUR_SQ = 0.5847    # множитель соседнего слова «ЛЕЗЕШЬ» — к «КУДА» не относится
SQ = NEIGHBOUR_SQ        # имя оставлено: им проверяется «чужое число к этому слову»
# Конец появления «КУДА»: 2.77 + 0.3 = 3.07 с. Момент ДО него берётся с запасом в
# десятую долю секунды (кадра мало: 1/60 = 0.0167 с).
REVEAL_END = WORD_T + APPEAR_DUR * WORD_SQ
BEFORE_T = round(REVEAL_END - 0.1, 6)

# Кадры владельца на 3 и 12 с: наш кадр БЕЗ цвета (рендер того же клипа с lm_on=false,
# `_tools_claude/render_cmp/C1476_lmoff.mp4`) против кадра эталона AE (exp/C1476.mov) —
# средние по бинам в 8 уровней яркости вместе с числом пикселей. Числа сняты с живых
# файлов, а не выдуманы: пара «источник -> эталон» — это и есть то, что обязана
# повторить кривая Lumetri. Пиксели выше 250 из средних исключаются: у нашего кадра
# там графика (субтитры, интро), её Lumetri не касается.
AE_CAMERA_FRAMES = {
    3.0: ((3.46, 5.03, 537968), (10.57, 13.21, 280647), (19.59, 23.95, 134565),
          (28.03, 33.97, 126983), (35.87, 43.29, 170434), (43.87, 52.77, 175214),
          (51.73, 61.80, 143200), (59.80, 71.38, 130043), (67.63, 80.40, 116480),
          (75.59, 89.22, 83236), (83.65, 98.50, 61602), (91.62, 107.87, 49023),
          (99.39, 116.79, 29916), (107.51, 125.75, 17071), (114.74, 132.03, 6109),
          (123.34, 131.28, 1765), (131.45, 133.85, 851), (139.12, 140.17, 774),
          (147.26, 143.82, 680), (155.34, 154.13, 857), (163.64, 174.69, 890),
          (171.78, 176.26, 932), (179.97, 189.71, 1065), (187.34, 206.04, 1055),
          (194.84, 212.92, 864), (202.85, 209.14, 500), (211.17, 215.80, 347),
          (219.04, 227.46, 236)),
    12.0: ((2.45, 4.08, 586865), (10.88, 13.94, 240831), (19.66, 24.49, 167352),
           (27.50, 33.60, 130230), (35.81, 43.48, 102620), (43.80, 52.96, 108268),
           (51.82, 62.46, 105880), (59.85, 71.87, 102051), (67.80, 81.20, 99935),
           (75.68, 90.40, 87844), (83.61, 99.56, 65420), (91.65, 108.73, 49281),
           (99.80, 118.61, 41251), (107.92, 128.25, 41811), (115.84, 137.13, 43990),
           (123.74, 145.76, 39600), (131.40, 153.68, 26868), (139.28, 162.36, 11602),
           (146.40, 168.89, 3379), (154.92, 155.10, 370), (194.94, 175.18, 231),
           (228.60, 237.15, 287), (243.78, 245.87, 454), (253.12, 253.84, 16539)),
}
# Настройки Lumetri того же стиля («ДжаггерНеу»): экспозиция +0.5 стопа, светлые +11,
# тени −5. Второй набор — «как было» (экспозиция в гамме) для проверки, что дефект пойман.
LM_STYLE = {"on": True, "exposure": 0.5, "highlights": 11, "shadows": -5,
            "whites": 0, "blacks": 0, "contrast": 0, "temp": 0, "tint": 0, "sat": 100}


# --------------------------------------------------------------------------- #
# Мини-DOM для стендов превью
# --------------------------------------------------------------------------- #
# Разбор разметки, которую боевой код отдаёт строками (`innerHTML`): превью собирает
# слова и буквы тегами `<span class="…" style="…">`, и стенду нужны РЕАЛЬНЫЕ дети с
# классами и стилями, иначе проверять нечего. Разбор простой (вложенные span, атрибуты
# class/style), но его хватает обеим разметкам — словам интро и строкам субтитров.
_HTML_PARSE_JS = r"""
function _htmlAttrs(el,attrs){
  var cm=String(attrs||'').match(/class="([^"]*)"/);if(cm)el.className=cm[1];
  var sm=String(attrs||'').match(/style="([^"]*)"/);
  if(sm)sm[1].split(';').forEach(function(pair){
    var kv=pair.split(':');if(kv.length<2)return;
    var key=kv[0].trim().replace(/-([a-z])/g,function(s,c){return c.toUpperCase();});
    el.style[key]=kv.slice(1).join(':').trim();});
}
function _htmlText(s){return String(s).replace(/&amp;/g,'&').replace(/&lt;/g,'<')
  .replace(/&gt;/g,'>').replace(/&quot;/g,'"').replace(/&#39;/g,"'");}
function parseHtml(html){
  html=String(html);var i=0,out=[];
  function readEl(){
    var m=/^<span([^>]*)>/.exec(html.slice(i));if(!m)return null;
    i+=m[0].length;
    var el=new El('span');_htmlAttrs(el,m[1]);
    var text='';
    while(i<html.length){
      if(html.slice(i,i+7)==='</span>'){i+=7;break;}
      if(html.slice(i,i+5)==='<span'){var kid=readEl();if(kid){kid.parentNode=el;el.children.push(kid);}continue;}
      var nxt=html.indexOf('<',i);if(nxt<0)nxt=html.length;
      text+=html.slice(i,nxt);i=nxt;
    }
    el._text=_htmlText(text);
    return el;
  }
  while(i<html.length){var e=readEl();if(e)out.push(e);else i++;}
  return out;
}
Object.defineProperty(El.prototype,'innerHTML',{set:function(v){this._html=String(v);
  this.children=parseHtml(v);},get:function(){return this._html;}});
"""


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _func(src, name):
    """Тело функции name из исходника (тот же приём, что в соседних тестах превью)."""
    m = re.search(r"(?:async\s+)?function\s+%s\s*\(" % re.escape(name), src)
    if not m:
        # Часть дверей объявлена переменной (`const ipvRenderModeOn=function …`).
        m = re.search(r"(?:const|let|var)\s+%s\s*=\s*function\s*\(" % re.escape(name), src)
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


def _run_node(code):
    """Прогнать стенд node'ом: код во временный файл (в `-e` он не влезет по длине)."""
    with tempfile.TemporaryDirectory(prefix="render_overlay_") as d:
        path = os.path.join(d, "stand.js")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        p = subprocess.run(["node", path], capture_output=True, text=True,
                           encoding="utf-8-sig", errors="replace", timeout=120, cwd=d)
    assert p.returncode == 0, (p.stderr or p.stdout)[-3000:]
    return json.loads(p.stdout.strip().splitlines()[-1])


# --------------------------------------------------------------------------- #
# 1. Элементы правки превью в режиме рендера
# --------------------------------------------------------------------------- #
# Всё, что JS рисует В КАДРЕ для правки: имя в разметке -> селектор CSS. Новый такой
# элемент обязан попасть и сюда, и в правило `.render-mode …{display:none}` в app.css.
EDIT_MARKS = {
    "intro-scale-handle": ".render-mode .ipvintro .intro-scale-handle",
    "zoommark": ".render-mode .zoommark",
    "rotomask": ".render-mode .rotomask",
}
# Где какой элемент создаётся: по этим файлам и проверяется, что элемент не переименовали.
EDIT_SOURCES = {
    "intro-scale-handle": "85-inserts-view.js",
    "zoommark": "95-styles.js",
}


def _css_rules(text):
    """Правила CSS: [(селектор, тело объявления)] — комментарии выброшены."""
    out = []
    for block in re.finditer(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", text, flags=re.S)):
        body = block.group(2)
        for sel in block.group(1).split(","):
            sel = sel.strip()
            if sel:
                out.append((sel, body))
    return out


def test_edit_marks_are_hidden_in_render_mode():
    """У каждого элемента правки в `.render-mode` есть `display:none !important`.

    `!important` не «на всякий случай»: показ и размеры этих элементов ставятся
    инлайново из JS (ipvIntro, zoomPickMark, rotoMask), и обычное правило проиграло бы
    инлайновому стилю — ровно так ручка масштаба и доехала до готового ролика.
    """
    rules = dict(_css_rules(_read(CSS)))
    for mark, sel in EDIT_MARKS.items():
        body = rules.get(sel)
        assert body is not None, (
            f"в app.css нет правила {sel!r} — элемент правки {mark!r} попадёт в кадр рендера")
        assert re.search(r"display\s*:\s*none", body), (sel, body)
        assert "!important" in body, (
            f"{sel}: без !important инлайновый стиль из JS перебьёт правило — {body!r}")


def test_edit_marks_are_created_by_the_code_the_rule_names():
    """Селекторы правил не висят в пустоте: элементы правки правда создаются в JS.

    Иначе сторож «спрятано» остался бы зелёным после того, как элемент переименовали:
    правило есть, прятать нечего.
    """
    for mark, fname in EDIT_SOURCES.items():
        src = _read(os.path.join(ROOT, "static", "app", fname))
        assert mark in src, f"{mark!r} больше не создаётся в {fname} — правило устарело"


# --------------------------------------------------------------------------- #
# 2. Мозаика: пикселизация по блокам AE, а не blur
# --------------------------------------------------------------------------- #
def test_mosaic_block_size_is_the_ae_template_size():
    """Размер блока мозаики в превью — тот же, что уезжает в .jsx (64×64).

    Вторая копия числа разошлась бы молча: в AE мозаика 64, в превью другая крупность —
    и «мозаика не как в AE» искали бы заново.
    """
    tpl = _read(TEMPLATE)
    m = re.search(r'function addMosaic\(L, on\).*?"ADBE Mosaic-0001",\s*(\d+)\)', tpl, re.S)
    assert m, "в шаблоне не нашлась мозаика (addMosaic) — проверять нечего"
    ae_block = int(m.group(1))
    js = _read(JS85)
    m2 = re.search(r"const MOSAIC_BLOCK=(\d+);", js)
    assert m2, "в 85-inserts-view.js нет MOSAIC_BLOCK"
    assert int(m2.group(1)) == ae_block, (
        f"блок мозаики превью {m2.group(1)}, а в шаблоне AE {ae_block}")


def test_mosaic_is_not_a_blur_anymore():
    """Размытия у вставки с мозаикой не осталось: ни в CSS, ни рядом с мозаикой в JS.

    Здесь было `filter:blur(10px)` на обёртке и на картинке — владелец видел размытие
    там, где в AE мозаика. Размытие вдобавок гасило пиксели у кромки коробки (тёмная
    полоса по краю кадра). Правило могло вернуться под другим именем — поэтому ищем
    сам `blur` рядом с `.mosaic`, а не одно снятое объявление.
    """
    guilty = [f"{sel} {{{body}}}" for sel, body in _css_rules(_read(CSS))
              if ".mosaic" in sel and "blur" in body]
    assert not guilty, f"app.css: мозаика снова размытие — {guilty}"
    # В JS — только строки, где мозаика и размытие стоят рядом: `blur` сам по себе в
    # файле есть (анимации появления слов) и к мозаике отношения не имеет.
    guilty = [ln.strip() for ln in _read(JS85).splitlines()
              if "mosaic" in ln.lower() and "blur" in ln.lower()
              and not ln.strip().startswith(("//", "*", "/*"))]
    assert not guilty, f"85-inserts-view.js: мозаика снова размытие — {guilty}"


def _mosaic_stand(cv_w, cv_h, plan_w=1080):
    """Стенд арифметики мозаики: боевой `drawMosaic` на поддельном холсте.

    `plan_w` — ширина кадра ролика в плане: по ней считается k (px превью на px кадра).
    Холст в 270 px при кадре 1080 — это стойка превью шага 2; кадр при этом тот же.
    """
    js = _read(JS85)
    return """
    var IPV={plan:{w:%(plan_w)d,h:1920}};
    var calls=[];
    function ctx(name){return {name:name,imageSmoothingEnabled:true,
      clearRect:function(){},drawImage:function(){calls.push({ctx:name,
        smooth:this.imageSmoothingEnabled,args:[].slice.call(arguments)});}};}
    function cv(w,h){return {width:w,height:h,getContext:function(){return ctx('big');}};}
    var document={createElement:function(){return {width:0,height:0,
      getContext:function(){return ctx('small');}};}};
    %(mosaic)s
    %(draw)s
    var r=drawMosaic(cv(%(cv_w)d,%(cv_h)d),{},2160,4096);
    console.log(JSON.stringify({r:r,calls:calls.map(function(c){
      return {ctx:c.ctx,smooth:c.smooth,args:c.args.map(function(v){
        return (typeof v==='object')?'[obj]':v;})};})}));
    """ % {"mosaic": "const MOSAIC_BLOCK=64;", "draw": _func(js, "drawMosaic"),
           "cv_w": cv_w, "cv_h": cv_h, "plan_w": plan_w}


def test_mosaic_blocks_are_counted_over_the_rendered_box():
    """Число блоков мозаики: сколько блоков по кадру и какой стороной они ложатся.

    Стенд зовёт БОЕВОЙ `drawMosaic` на поддельном холсте: важно не «функция вызвалась»,
    а числа — сколько блоков по ширине и высоте и что растяжение идёт БЕЗ сглаживания
    (со сглаживанием вышло бы размытие, то самое, что и было дефектом).
    """
    out = _run_node(_mosaic_stand(1080, 1920))

    assert out["r"], out
    # Кадр 1080x1920 и блок 64 px ИСХОДНИКА: сводим к 34x64 блоков (2160/64 и 4096/64).
    assert out["r"]["cols"] == 34 and out["r"]["rows"] == 64, out["r"]
    assert out["r"]["bw"] == 64 and out["r"]["bh"] == 64, out["r"]
    # Порядок отрисовки: сначала свод к блокам (малый холст 34x64) СО сглаживанием,
    # потом растяжение малого холста на весь кадр — БЕЗ него.
    small = [c for c in out["calls"] if c["ctx"] == "small"]
    big = [c for c in out["calls"] if c["ctx"] == "big"]
    assert small and small[0]["args"][3] == 34 and small[0]["args"][4] == 64, small
    assert small[0]["smooth"] is True, "свод к блокам идёт без сглаживания — блоки «звенят»"
    # Растяжение: drawImage(малый холст, 0, 0, 34, 64, 0, 0, 1080, 1920) — источник
    # в блоках, приёмник во весь кадр. args[0] — сам холст, поэтому номера сдвинуты.
    assert big and big[-1]["args"][5:9] == [0, 0, 1080, 1920], big
    assert big[-1]["smooth"] is False, (
        "растяжение блоков идёт СО сглаживанием — это размытие, а не мозаика")


def test_mosaic_block_in_the_frame_is_scaled_with_the_source():
    """Блок в кадре — 64 px ИСХОДНИКА, а не 64 px кадра: у 4K это 32 px.

    В AE эффект Mosaic стоит на слое ДО его Scale, поэтому блок 64 px — это 64 px
    ролика-источника, и в кадре он выходит 64·ks (ks — масштаб показа). Замер по кадру
    владельца (rb_30, вставка 2160×4096 в кадре 1080×1920): период мозаики в AE 34 px
    (дрожание блоков wiggle(1,15) размывает среднее), то есть вдвое меньше 64.
    """
    out = _run_node(_mosaic_stand(1080, 1920))

    r = out["r"]
    # 2160 px источника по 64 px = 33.75 блока, округляется до 34 — блок в кадре
    # 1080/34 = 31.8 px, то есть вдвое меньше «64 px кадра».
    block_in_frame = 1080.0 / r["cols"]          # 1080 px холста на 34 блока
    assert abs(block_in_frame - 32.0) < 0.5, (r, block_in_frame)
    # Блок масштабируется вместе с показом источника: холст 1080 при исходнике 2160 —
    # это 0.5, значит блок источника 64 px виден как 32 px кадра.
    assert abs(block_in_frame - 64 * 1080 / 2160) < 0.5, block_in_frame


def _mosaic_src_stand(js85=None):
    """Кого берёт мозаика источником пикселей: боевой `ipvInsSrc`.

    Разметка обёрток — как её строит ipvInsPlace: у видеовставки первый ребёнок
    обёртки и есть `<video>` (внутри него ничего нет), у фото — маска `.insmask`
    с картинкой внутри.
    """
    js = js85 if js85 is not None else _read(JS85)
    return """
    function el(tag,cls,kids){return {tagName:tag,className:cls||'',children:kids||[],
      querySelector:function(sel){
        var want=String(sel).split(',');
        for(var i=0;i<this.children.length;i++){
          var c=this.children[i];
          for(var j=0;j<want.length;j++)if(c.tagName===want[j].trim().toUpperCase())return c;}
        return null;}};}
    %(src)s
    var video=el('VIDEO');
    var img=el('IMG');
    var mask=el('DIV','insmask',[img]);
    console.log(JSON.stringify({
      video:ipvInsSrc(video)===video,
      img:ipvInsSrc(img)===img,
      mask:ipvInsSrc(mask)===img,
      none:ipvInsSrc(null)===null}));
    """ % {"src": _func(js, "ipvInsSrc")}


def test_mosaic_source_of_a_video_insert_is_the_video_itself():
    """Источник мозаики у видеовставки — сам `<video>`, а не «что-то с картинкой внутри».

    Здесь и был дефект владельца: мозаика искала источник как `el.querySelector('img,video')`,
    а у видеовставки первый ребёнок обёртки — САМ `<video>`, и внутри него ничего нет:
    поиск возвращал null, `ipvPixelate` не звался вовсе, и вставка выходила чистой
    (в AE — мозаика 64×64). Фото работало, поэтому дефект и не был виден раньше.
    """
    out = _run_node(_mosaic_src_stand())
    assert out["video"], "у видеовставки мозаика снова не находит источник"
    assert out["img"], "у фото источник потерялся"
    assert out["mask"], "у фото в маске источник потерялся"
    assert out["none"], "нулевой элемент должен давать null"


def test_mutation_video_mosaic_source_is_caught():
    """Мутация 1: вернуть прежний поиск источника — проверка обязана покраснеть.

    Тот же стенд, но `ipvInsSrc` собран из ИСПОРЧЕННОГО исходника (`querySelector` по
    обёртке вместо самого элемента), то есть ровно так, как было до правки. Так сторож
    не сможет молча перестать ловить «видеовставка без мозаики».
    """
    src = _read(JS85)
    marker = ("function ipvInsSrc(el){\n  if(!el)return null;\n"
              "  if(el.tagName==='IMG'||el.tagName==='VIDEO')return el;\n"
              "  if(!el.querySelector)return null;\n"
              "  return el.querySelector('img.iphoto')||el.querySelector('img,video');}")
    assert marker in src, "ipvInsSrc изменился — мутация устарела"
    broken = src.replace(marker, "function ipvInsSrc(el){\n"
                                 "  return (el&&el.querySelector)?el.querySelector('img,video'):null;}")
    out = _run_node(_mosaic_src_stand(broken))
    assert out["video"] is False, (
        "мутация не поймана: со старым поиском источника видеовставка всё ещё «с мозаикой»")


def _mosaic_order(src, fn):
    """Куски тела функции: порядок вызовов и что стоит до/после."""
    body = _func(src, fn)
    return body


def test_mosaic_is_drawn_after_the_inserts_are_seeked():
    """Мозаика рисуется ПОСЛЕ перемотки вставок и с `force` в рендере.

    Кадр холста обязан нести тот кадр вставки, который видно. В рендере время вставке
    ставят в ipvOverlayPlan — ДО ожидания кадра <video>, поэтому холст, нарисованный
    там же, остался бы с прошлым кадром ролика; в ipvRenderAt после ожидания кадров
    мозаика перерисовывается принудительно (`force`).
    """
    src = _read(JS85)
    plan_body = _mosaic_order(src, "ipvOverlayPlan")
    assert "ipvInsMosaics()" in plan_body, "в режиме плана мозаика не рисуется вовсе"
    assert plan_body.index("v.currentTime=want") < plan_body.index("ipvInsMosaics()"), (
        "мозаика рисуется ДО перемотки вставки — на холсте будет прошлый её кадр")
    live = _mosaic_order(src, "ipvOverlay")
    assert "ipvInsMosaics()" in live and (
        live.index("v.currentTime=want") < live.index("ipvInsMosaics()")), live[-400:]
    render = _mosaic_order(src, "ipvRenderAt")
    assert "ipvInsMosaics(true)" in render, (
        "в рендере мозаика не перерисовывается после ожидания кадров")
    assert render.index("await Promise.all") < render.index("ipvInsMosaics(true)"), render
    # Холст — поверх источника, а не флекс-соседом: иначе он встал бы РЯДОМ с видео.
    pixel = _func(src, "ipvPixelate")
    assert "translate(-50%,-50%)" in pixel, pixel[:400]
    rules = dict(_css_rules(_read(CSS)))
    canvas = [body for sel, body in rules.items() if sel == ".ipvins canvas.ipvmosaic"]
    assert canvas, "в app.css нет правила для холста мозаики"
    assert "position:absolute" in canvas[0] and "left:50%" in canvas[0], canvas[0]


def test_mosaic_scales_the_block_with_the_preview():
    """Число блоков одно и то же на любом размере превью: картинка та же, крупность та же.

    Блок считается от ИСХОДНИКА (64 px ролика), а не от пикселей стойки: иначе мозаика
    в превью шага 2 (стойка ~250 px) показывала бы блоки вчетверо крупнее готового
    ролика, и по превью нельзя было бы судить о результате. Проверка ровно на это: та
    же вставка 2160x4096 при кадре 1080 и холстах 1080 и 270 режется на ОДНО И ТО ЖЕ
    число блоков (34x64), а размеры блока относятся как 4:1 — как и сами холсты.
    """
    full = _run_node(_mosaic_stand(1080, 1920))
    small = _run_node(_mosaic_stand(270, 480))

    assert full["r"]["bw"] == 64 and full["r"]["bh"] == 64, full["r"]
    assert small["r"]["bw"] == 16 and small["r"]["bh"] == 16, small["r"]   # 64 * (270/1080)
    assert full["r"]["cols"] == small["r"]["cols"] == 34, (full["r"], small["r"])
    assert full["r"]["rows"] == small["r"]["rows"] == 64, (full["r"], small["r"])
    assert full["r"]["bw"] / small["r"]["bw"] == 4, (full["r"], small["r"])


# --------------------------------------------------------------------------- #
# 3. Тайминги интро: слово открывается по времени плана
# --------------------------------------------------------------------------- #
def test_reveal_duration_is_the_appear_duration_not_the_glitch_one():
    """«Раскрытие» играет F_DUR=0.3 с, а не длительность глитча (0.44).

    В плане `intro_anims.reveal.dur` лежит длительность ГЛИТЧА (одно поле на оба
    эффекта), а в .jsx раскрытие идёт `F_DUR` — тем же временем, что фейд, масштаб и
    up/left/right (`var F_DUR=0.3` в core/xml2ae/template.py). Превью брало 0.44 и
    открывало слово позже AE: на 3.0 с последняя буква ещё не показывалась.
    """
    js = _read(JS85)
    m = re.search(r"F_DUR\s*=\s*([0-9.]+)", _read(TEMPLATE))
    assert m, "в шаблоне не нашлась F_DUR — сверять нечего"
    f_dur = float(m.group(1))
    code = """
    var IPV={plan:{intro_anims:{glitch:{dur:0.44,op_keys:[[0,0]]},
                                reveal:{dur:0.44,blur:26.8,scale:0.7}}}};
    %(defs)s
    %(dur)s
    %(params)s
    console.log(JSON.stringify({d:ipvIntroDefAnims().reveal.dur,
                                p:ipvIntroAnimParams().reveal.dur,
                                glitch:ipvIntroAnimParams().glitch.dur}));
    """ % {"defs": _func(js, "ipvIntroDefAnims"), "dur": _func(js, "ipvIntroAppearDur"),
           "params": _func(js, "ipvIntroAnimParams")}
    out = _run_node(code)

    assert abs(out["d"] - f_dur) < 1e-9, (
        f"длительность раскрытия в превью {out['d']}, а в .jsx F_DUR={f_dur}")
    assert abs(out["p"] - f_dur) < 1e-9, (
        f"план подсунул раскрытию глитчевую длительность: {out['p']}")
    assert abs(out["glitch"] - 0.44) < 1e-9, (
        f"глитч потерял свою длительность из плана: {out['glitch']}")


def test_reveal_letter_ramp_matches_the_ae_percent_offset():
    """Буква открывается РАМПОЙ селектора, а не ступенькой по своей доле.

    Это Percent Offset селектора в AE (форма «Ramp Up»): выбранная часть текста едет по
    слову, и буква на своём месте попадает в рампу — доля открытия буквы с серединой p
    равна clamp(2u − p). Проверяются обе стороны: к концу появления видно ВСЁ слово, в
    начале — ничего.
    """
    js = _read(JS85)
    code = """
    %(reveal)s
    function at(u,n){var out=[];for(var i=0;i<n;i++)out.push(+ipvRevealU(u,i,n).toFixed(4));return out;}
    console.log(JSON.stringify({n4_0:at(0,4), n4_end:at(1,4), n4_half:at(0.5,4),
                                n1:at(0.5,1), n5_end:at(1,5), n4_up:at(0.75,4)}));
    """ % {"reveal": _func(js, "ipvRevealU")}
    out = _run_node(code)

    assert out["n4_0"] == [0, 0, 0, 0], out["n4_0"]
    assert out["n4_end"] == [1, 1, 1, 1], f"к концу появления слово не открылось: {out['n4_end']}"
    # Середина хода: буквы с серединой левее 2u=1 открыты, дальше — в рампах.
    # Середины букв: 0.125, 0.375, 0.625, 0.875.
    assert out["n4_half"] == [0.875, 0.625, 0.375, 0.125], out["n4_half"]
    # К концу слова последняя буква догоняет первой: у 3/4 хода она уже в рампах.
    assert out["n4_up"] == [1, 1, 0.875, 0.625], out["n4_up"]
    # Одна буква — её середина в середине слова.
    assert out["n1"] == [0.5], out["n1"]
    assert out["n5_end"] == [1, 1, 1, 1, 1], out["n5_end"]


def _letters_at(t):
    """Что видно по буквам слова «КУДА» на момент t — боевой `ipvRevealU`.

    Множитель сжатия берётся у ЭТОГО слова: в плане клипа он лежит по словам, и у
    «КУДА» его нет вовсе (0.5847 — множитель «ЛЕЗЕШЬ», ему не хватало времени до
    затухания группы). Пока превью брало чужое число, ход выходил в 1.31 и все буквы
    «стояли» ещё до конца появления — картинка расходилась с AE на букву.
    """
    js = _read(JS85)
    code = """
    %(reveal)s
    function at(t){var u=Math.min(1,Math.max(0,(t-%(word).6f)/(%(dur).6f*%(sq).6f)));
      var out=[];for(var i=0;i<4;i++)out.push(+ipvRevealU(u,i,4).toFixed(4));
      return {t:t,u:u,letters:out};}
    console.log(JSON.stringify(at(%(t).6f)));
    """ % {"reveal": _func(js, "ipvRevealU"), "word": WORD_T, "dur": APPEAR_DUR,
           "sq": WORD_SQ, "t": t}
    return _run_node(code)


def test_reveal_word_is_visible_at_the_owner_frame():
    """На 3.0 с буквы «КУДА» уже РАСТУТ: последняя не гаснет, а показывается.

    Кадр владельца: в AE «КУДА» (последняя буква в анимации), у нас было «КУД» — четвёртая
    буква не показывалась вовсе. Ход появления берётся из плана клипа: 0.23 с от начала
    слова при появлении F_DUR=0.3 с (у «КУДА» сжатия нет — 0.5847 в плане стоит у
    «ЛЕЗЕШЬ»), то есть u = 0.767. Первые три буквы открыты, последняя — на 0.66.
    """
    out = _letters_at(CHECK_T)

    assert abs(out["u"] - (CHECK_T - WORD_T) / APPEAR_DUR) < 1e-6, out
    assert min(out["letters"]) > 0.6, (
        f"на {CHECK_T} с последняя буква ещё не показывается: {out['letters']}")
    # Слева направо: чем дальше буква, тем она позже открывается.
    assert out["letters"] == sorted(out["letters"], reverse=True), out["letters"]
    # Но слово ещё НЕ открыто целиком — иначе проверка ничего не значит.
    assert out["letters"][-1] < 1.0 < out["letters"][0] + 1e-9, out["letters"]


def test_reveal_word_is_less_open_before_the_owner_frame():
    """Раньше по времени — буквы открыты меньше: это ход появления, а не «так видно».

    За десятую долю секунды до кадра владельца последняя буква открыта заметно меньше,
    а к самому кадру — больше: значит картинка едет по времени плана, а не по таймеру.
    """
    before = _letters_at(BEFORE_T)
    at = _letters_at(CHECK_T)

    assert before["letters"][-1] < at["letters"][-1], (before, at)
    assert before["letters"][-1] < 0.5, before


def test_reveal_sq_is_the_multiplier_of_its_own_word():
    """Сжатие появления — у КАЖДОГО слова своё: чужое число растягивает ход.

    В плане клипа `sq` лежит по словам строки: у «КУДА» множителя нет (слово успевает
    доиграть до затухания группы), а 0.5847 — у «ЛЕЗЕШЬ». Если применить чужое число к
    «КУДА», ход на 3.0 с выходит 1.31 вместо 0.767: картинка врёт и в начале, и в конце
    появления (на этом и разъехалась прошлая проверка).
    """
    js = _read(JS85)
    code = """
    function raw(t,sq){return (t-%(word).6f)/(%(dur).6f*sq);}
    console.log(JSON.stringify({own:raw(%(t).6f,%(own).6f), alien:raw(%(t).6f,%(alien).6f)}));
    """ % {"word": WORD_T, "dur": APPEAR_DUR, "t": CHECK_T,
           "own": WORD_SQ, "alien": NEIGHBOUR_SQ}
    out = _run_node(code)

    assert out["own"] < 1.0 < out["alien"], out
    # Боевая формула хода (см. ветку reveal в ipvIntro) — та же: dt/(dur*sq слова).
    assert "dt/(rDur*sq)" in _func(js, "ipvIntro"), (
        "ход раскрытия считается не от длительности появления со сжатием СВОЕГО слова")


def test_reveal_timing_comes_from_the_plan_not_from_a_timer():
    """Ход открытия — величина на момент кадра: та же `t` даёт ту же букву.

    В живом превью появление везёт CSS-переход по реальному времени (и это правильно:
    там играет человек). В рендере так нельзя: снимок поймал бы середину перехода, и
    картинка зависела бы от скорости съёмки. Стенд зовёт боевой `ipvRevealU` дважды с
    одним и тем же ходом — числа обязаны совпасть до знака.
    """
    js = _read(JS85)
    code = """
    %(reveal)s
    var u=(%(dt).6f)/(%(dur).6f*%(sq).6f);
    var a=[],b=[];
    for(var i=0;i<4;i++){a.push(ipvRevealU(u,i,4));b.push(ipvRevealU(u,i,4));}
    console.log(JSON.stringify({same:JSON.stringify(a)===JSON.stringify(b),a:a}));
    """ % {"reveal": _func(js, "ipvRevealU"), "dt": CHECK_T - WORD_T,
           "dur": APPEAR_DUR, "sq": WORD_SQ}
    out = _run_node(code)
    assert out["same"], out


# --------------------------------------------------------------------------- #
# 4. Ручка масштаба и маркер наезда в рендере не создаются вовсе
# --------------------------------------------------------------------------- #
# Мини-DOM ровно под ipvIntro: строки, слова, классы. Отрисовку кадра он не трогает —
# проверяется только то, что попадает в DOM оверлея.
_INTRO_DOM = r"""
function ClassList(el){this.el=el;}
ClassList.prototype._l=function(){return String(this.el.className||'').split(/\s+/).filter(Boolean);};
ClassList.prototype.contains=function(c){return this._l().indexOf(c)>=0;};
ClassList.prototype.add=function(c){if(!this.contains(c)){this.el.className=(this._l().concat([c])).join(' ');}};
ClassList.prototype.remove=function(c){this.el.className=this._l().filter(function(x){return x!==c;}).join(' ');};
function El(tag){this.tag=tag;this.children=[];this.parentNode=null;this.id='';this.className='';
  this.style={setProperty:function(k,v){this[k]=String(v);}};this.dataset={};this._text='';
  this._html='';this.classList=new ClassList(this);this.clientWidth=1080;this.clientHeight=1920;}
Object.defineProperty(El.prototype,'firstChild',{get:function(){return this.children[0]||null;}});
Object.defineProperty(El.prototype,'textContent',{get:function(){return this._text;},
  set:function(v){this._text=String(v==null?'':v);this.children=[];}});
__HTML_PARSE__
El.prototype.appendChild=function(c){c.parentNode=this;this.children.push(c);return c;};
El.prototype.remove=function(){if(!this.parentNode)return;
  var i=this.parentNode.children.indexOf(this);
  if(i>=0)this.parentNode.children.splice(i,1);this.parentNode=null;};
function textNode(txt){var n=new El('#text');n._text=String(txt);return n;}
El.prototype.querySelectorAll=function(sel){
  var all=[];(function walk(el){for(var i=0;i<el.children.length;i++){all.push(el.children[i]);
    walk(el.children[i]);}})(this);
  function has(el,name){
    name=String(name||'').trim();
    if(!name)return false;
    if(name.charAt(0)==='.')return el.classList.contains(name.slice(1));
    // Голое имя стенд зовёт и классом (`iline`), и тегом (`span`) — принимаем оба.
    return String(el.tag||'').toUpperCase()===name.toUpperCase()||el.classList.contains(name);}
  // Селекторы стенда: `.класс` и `.класс > тег` (последним живёт `ipvIntro`).
  var parts=String(sel).split('>').map(function(s){return s.trim();}).filter(Boolean);
  if(parts.length===1)return all.filter(function(el){return has(el,parts[0]);});
  var target=parts[parts.length-1],parent=parts[0];
  return all.filter(function(el){return has(el,target)&&el.parentNode&&has(el.parentNode,parent);});
};
El.prototype.querySelector=function(sel){var a=this.querySelectorAll(sel);return a.length?a[0]:null;};
function $(id){return id==='ipvintro'?INTRO:null;}
var INTRO=new El('div');INTRO.id='ipvintro';
var IPV={fps:60,plan:null,intro:[],introCur:-1};
var IPVMODE='ae';
var CURSTYLE={};
function t(s,vars){if(!vars)return s;var o=s;for(var k in vars)o=o.split('{'+k+'}').join(String(vars[k]));return o;}
function ipvToHex(c,fb){return (typeof c==='string')?c:(fb||'#ffffff');}
function ipvFontFor(){return null;}
function introMarkPlaying(){}
function ipvIntroPos(){}
function esc(s){return String(s==null?'':s);}
function fmtIns(){return '0';}
function rgb2hex(){return '#fff';}
var document={documentElement:{className:''},
  createElement:function(tag){return new El(tag);},
  createTextNode:function(txt){return textNode(txt);}};
document.documentElement.classList=new ClassList(document.documentElement);
// Группа — ровно та, что собрал план клипа владельца (C1476): «КУДА ТЫ ЛЕЗЕШЬ»,
// anim=reveal, времена слов, te/fade группы и множители сжатия ПО СЛОВАМ
// (у «КУДА» множителя нет — 0.5847 принадлежит «ЛЕЗЕШЬ»).
function planGroup(){return {inAt:2.77,outEnd:3.7,ts:2.77,te:3.7,fade:0.2046,ys:[960],
  sq:[[null,null,0.5847]],fonts:['BebasNeue-Bold'],
  lines:[{color:'white',anim:'reveal',words:['КУДА','ТЫ','ЛЕЗЕШЬ'],times:[2.77,3.03,3.32]}]};}
// Анимации интро — из плана того же клипа (intro_anims): у reveal `dur` — длительность
// ГЛИТЧА (одно поле на оба эффекта), а раскрытие играет F_DUR=0.3; Scale 3D аниматора
// 11 % — то, чем буква «не открыта»; shape=2 — форма «Ramp Up».
var INTRO_ANIMS={glitch:{dur:0.44,op_keys:[[0,0],[0.05,100],[0.1,100]]},
  reveal:{dur:0.44,blur:26.8,scale:0.7,scale_3d:[11,11,91.66667],shape:2,smoothness:100,
          ease:[10,95]}};
"""


# План стенда: тот же набор полей, что у страницы рендера, — анимации интро в нём
# объявлены литералом `INTRO_ANIMS` (числа .jsx). `_intro_plan_js` дописывает в него
# поля конкретной проверки (например `hl_dur` и `intro_anims` с тела клипа).
_INTRO_PLAN_JS = ("{w:1080,h:1920,fsize:140,intro_fsize:140,back_scale:0.69,"
                  "intro_comp_shadow:null,intro_word_fx:{},intro_anims:INTRO_ANIMS}")


def _intro_plan_js(extra=""):
    """Литерал плана стенда с добавкой: `extra` — поля JS через запятую."""
    return _INTRO_PLAN_JS[:-1] + (("," + extra) if extra else "") + "}"


def _intro_js(render, js85=None, t=CHECK_T, groups=None, plan=None):
    """Стенд отрисовки интро в режиме рендера (render) или в живом превью.

    Класс `render-mode` ставит БОЕВАЯ дверь (`ipvRenderModeOn` из 85-inserts-view.js) —
    иначе проверялось бы «в стенде», а не «в бою». Режим включает `IPV.renderT` — так же,
    как это делает `ipvRenderAt`.

    `t` — момент кадра, `groups` — группы интро выражением JS (по умолчанию группа
    клипа владельца из `planGroup`), `plan` — план стенда (по умолчанию `_intro_plan_js`):
    им живут счётчик (`hl_dur`) и раскрытие (`intro_anims`).
    """
    src = js85 if js85 is not None else _read(JS85)
    # Живое превью: класс с корня снимаем руками — в бою его снимает переход на другую
    # страницу, а стенд живёт одной страницей и «переключается» на месте. Время кадра
    # тоже своё: рендер просит КОНКРЕТНЫЙ момент (ipvIntro(t)), живое превью рисует
    # «сейчас» — тем же ipvIntro, но с текущим временем превью.
    live_reset = "" if render else "document.documentElement.className='';"
    return "\n".join([
        _HTML_PARSE_JS,
        _func(src, "aeEase"), _func(src, "bezierY"), _func(src, "bezierT"),
        _func(src, "ipvEase"), _func(src, "introGroupWindows"),
        _func(src, "ipvIntro"), _func(src, "ipvIntroAppearDur"),
        _func(src, "ipvIntroHlDur"), _func(src, "ipvIntroDefAnims"),
        _func(src, "ipvIntroAnimParams"),
        _func(src, "ipvGlitchOp"), _func(src, "ipvRenderChars"), _func(src, "ipvParseCount"),
        _func(src, "ipvRevealU"), _func(src, "ipvRenderMode"),
        _func(src, "ipvRenderModeOn"), _INTRO_DOM.replace("__HTML_PARSE__", ""), """
    IPV.plan=%(plan)s;
    IPV.intro=%(groups)s;
    IPV.renderT=%(rt)s;
    ipvRenderModeOn();
    %(reset)s
    ipvIntro(%(t)s);
    var DBG=(typeof INTRO !== 'undefined')?INTRO:null;
    // Всё, что видно в кадре по словам интро: прозрачность/масштаб/блюр СЛОВА (его в
    // AE ведёт слой) и то же по каждой букве (в AE букву двигает Scale 3D аниматора).
    function dword(sp){var cs=[];
      for(var c=0;c<(sp.children||[]).length;c++){var ch=sp.children[c];
        cs.push({ch:ch.textContent,op:ch.style.opacity||'',tr:ch.style.transform||''});}
      return {word:sp.dataset.origWord,anim:sp.dataset.anim||'',cnt:sp.dataset.isCount==='1',
        t:sp.dataset.t,op:sp.style.opacity||'',tr:sp.style.transform||'',
        flt:sp.style.filter||'',
        text:cs.length?cs.map(function(c){return c.ch;}).join(''):sp.textContent,chars:cs};}
    var ALL=[];
    if(DBG){var ws=DBG.querySelectorAll('iword');for(var q=0;q<ws.length;q++)ALL.push(dword(ws[q]));}
    var WORD=null;
    for(var w2=0;w2<ALL.length;w2++)if(ALL[w2].word==='КУДА')WORD=ALL[w2];
    console.log(JSON.stringify({root:document.documentElement.className,
      handles:DBG?DBG.querySelectorAll('intro-scale-handle').length:-1,
      lines:DBG?DBG.querySelectorAll('iline').length:-1,
      mode:typeof ipvRenderMode==='function'?ipvRenderMode():'nofn',
      wordOp:WORD?(WORD.op||''):null,
      wordTr:WORD?(WORD.tr||''):null,
      chars:WORD?WORD.chars:[],
      words:ALL,
      html:DBG?DBG.innerHTML:''}));
    """ % {"rt": (str(t) if render else "null"), "t": t, "reset": live_reset,
           "groups": groups if groups is not None else "[planGroup()]",
           "plan": plan if plan is not None else _intro_plan_js()}])


@node
def test_render_mode_intro_has_no_scale_handle():
    """Ручка масштаба интро в рендере не создаётся — её и не должно быть в снимке.

    Живой случай: черта с ручкой попала в готовый ролик под текстом интро. CSS её тоже
    прячет, но создавать элемент правки на странице рендера незачем вовсе.
    """
    out = _run_node(_intro_js(render=True))
    assert "render-mode" in out["root"].split(), out["root"]
    assert out["handles"] == 0, f"ручка масштаба создана в рендере: {out['html'][:200]}"
    assert out["lines"] > 0, "строки интро не отрисовались вовсе — проверять нечего"


@node
def test_live_preview_keeps_the_scale_handle():
    """Обратная сторона: в живом превью ручка масштаба на месте (правку не сломали)."""
    out = _run_node(_intro_js(render=False))
    assert "render-mode" not in out["root"].split(), out["root"]
    assert out["handles"] == 1, "в живом превью пропала ручка масштаба интро"


def test_render_mode_class_is_put_on_the_root_by_the_frame():
    """Класс `render-mode` вешает сам кадр рендера (ipvRenderAt) — на нём и держится CSS.

    Без этой двери все правила выше не включаются вовсе: страница рендера рисовала бы
    кадр как живое превью.
    """
    src = _read(JS85)
    body = _func(src, "ipvRenderAt")
    assert "ipvRenderModeOn()" in body, (
        "кадр рендера больше не ставит класс render-mode — элементы правки вернутся в кадр")
    mode = _func(src, "ipvRenderModeOn")
    assert "render-mode" in mode, mode


@node
def test_zoom_pick_mark_is_not_created_in_render_mode():
    """Маркер точки наезда в рендере не создаётся: `zoomPickMark` выходит до правки DOM.

    Обратная проверка — в живом превью маркер создаётся (при прицеле точки наезда),
    иначе «спрятали» элемент вместе с правкой.
    """
    src = _read(JS95)
    code = """
    var IPV={renderT:1.0};
    function ipvRenderMode(){return IPV.renderT!=null;}
    var STAGE={children:[],appendChild:function(c){this.children.push(c);}};
    function $(id){return id==='ipvstage'?STAGE:null;}
    function t(s){return s;}
    function el(tag){return {tag:tag,style:{}};}
    var document={createElement:function(tag){return el(tag);}};
    var ZOOM_PICK=true,ZOOM_HOVER=false,CURSTYLE={};
    %(mark)s
    zoomPickMark();
    var madeRender=STAGE.children.length;
    IPV.renderT=null;                      // живое превью
    zoomPickMark();
    console.log(JSON.stringify({render:madeRender,live:STAGE.children.length,
      shown:STAGE.children.length?String(STAGE.children[0].style.display):''}));
    """ % {"mark": chr(10).join(_func(src, n) for n in
                             ("zoomPickTarget", "zoomPickKeys", "zoomPickMark"))}
    out = _run_node(code)
    assert out["render"] == 0, "в рендере создан маркер точки наезда — он попадёт в кадр"
    assert out["live"] == 1, "в живом превью маркер не создан — правку сломали"


# --------------------------------------------------------------------------- #
# 5. Слово интро в кадре: буквы растут, а не гаснут
# --------------------------------------------------------------------------- #
@node
def test_render_mode_shows_all_four_letters_of_the_owner_word():
    """На 3.0 с в кадре видны ВСЕ четыре буквы «КУДА», последняя — в анимации.

    Боевой `ipvIntro` на боевых числах плана клипа (запуск — `_intro_js`). В AE четвёртая
    буква в этот момент ещё растёт масштабом (аниматор Scale 3D: 11 % → 100 %), но она
    ВИДНА; у нас она гасла прозрачностью — на кадре выходило «КУД».
    """
    out = _run_node(_intro_js(render=True))
    chars = out["chars"]
    assert len(chars) == 4, f"в слове «КУДА» не четыре буквы: {chars}"

    # Ни одна буква не погашена прозрачностью: её ведёт Scale аниматора.
    assert all(c["op"] in ("", "1") for c in chars), chars
    # Доли открытия букв на u = 0.767 по рамке селектора (clamp(2u − p), p — середина
    # буквы): 1.0, 1.0, 0.908, 0.658 → масштабы 1.0, 1.0, 0.918, 0.696. Последняя буква
    # ВИДНА (69 % размера) — в этом и была разница с «КУД».
    scales = []
    for c in chars:
        m = re.search(r"scale\(([0-9.]+)\)", c["tr"] or "")
        scales.append(1.0 if not m else float(m.group(1)))
    assert scales[:2] == [1.0, 1.0], (scales, chars)
    assert 0.9 < scales[2] < 0.95, (scales, chars)
    assert 0.65 < scales[3] < 0.75, f"последняя буква не в анимации: {scales}"


def test_mutation_step_wise_reveal_is_caught():
    """Мутация 2: вернуть «ступеньку» вместо рамки селектора — проверка краснеет.

    Прежняя формула открывала букву на её отрезке [i/n, (i+1)/n] и на кадре владельца
    отдавала последней букве 0.07 — она пропадала («КУД»). Стенд собирается из
    ИСПОРЧЕННОГО исходника, и последняя буква обязана выйти почти закрытой.
    """
    src = _read(JS85)
    marker = ("function ipvRevealU(u,ci,n){\n  const total=Math.max(1,+n||1);\n"
              "  const p=((+ci||0)+0.5)/total;                  // середина буквы в долях ширины слова\n"
              "  return Math.min(1,Math.max(0,2*(+u||0)-p));}")
    assert marker in src, "ipvRevealU изменился — мутация устарела"
    broken = src.replace(marker, "function ipvRevealU(u,ci,n){\n"
                                 "  const total=Math.max(1,+n||1);\n"
                                 "  return Math.min(1,Math.max(0,(+u||0)*total-(+ci||0)));}")
    out = _run_node(_intro_js(render=True, js85=broken))
    scales = []
    for c in out["chars"]:
        m = re.search(r"scale\(([0-9.]+)\)", c["tr"] or "")
        scales.append(1.0 if not m else float(m.group(1)))
    assert scales and scales[-1] < 0.2, (
        f"мутация не поймана: со «ступенькой» последняя буква всё ещё открыта — {scales}")


# --------------------------------------------------------------------------- #
# 5б. Тело клипа: буквы раскрытия видны с первого кадра, счётчик играет HL_DUR
# --------------------------------------------------------------------------- #
# Группы интро — из плана сцены ЖИВОГО клипа владельца (C1476): тело сборки
# (`render_cmp/c1476_body.json`) прогнано через `core.xml2ae.scene_plan`, и здесь лежат
# его готовые числа. Это ровно те кадры, на которых дефекты и были видны: 1.0 с («что» —
# строка заднего плана, раскрытие), 2.5 с («ТЕБЕ 19» — глитч со счётчиком), 3.0 с
# («КУДА» — раскрытие), 3.33 с (раскрытие доиграло и совпало).
CLIP_GROUPS = [
    # Группа 1: [0] строка заднего плана «что то вроде» (раскрытие, кегль back_scale),
    # [1] жёлтое «ТЕБЕ 19» — глитч и счётчик на втором слове (cnt_words: [1]).
    {"ts": 0.83, "te": 2.77, "fade": 0.2968, "ys": [960.0, 1078.72],
     "sq": [[None, None, None], [None, 0.8482]],
     "fonts": ["BebasNeue-Bold", "BebasNeue-Bold"],
     "lines": [
         {"color": "white", "back": True, "anim": "reveal",
          "words": ["что", "то", "вроде"], "times": [0.83, 0.98, 1.17]},
         {"color": "yellow", "anim": "glitch", "is_count": True, "dec": 0,
          "cnt": 19, "cnt_idx": 1,
          "cnts": [[1, 19, 'Math.round(effect("Slider Control")("Slider").value)', 0]],
          "words": ["ТЕБЕ", "19"], "times": [1.38, 2.1]},
     ]},
    # Группа 2: «КУДА ТЫ ЛЕЗЕШЬ» — раскрытие; у «ЛЕЗЕШЬ» своё сжатие 0.5847.
    {"ts": 2.77, "te": 3.7, "fade": 0.2046, "ys": [960.0],
     "sq": [[None, None, 0.5847]], "fonts": ["BebasNeue-Bold"],
     "lines": [{"color": "white", "anim": "reveal",
                "words": ["КУДА", "ТЫ", "ЛЕЗЕШЬ"], "times": [2.77, 3.03, 3.32]}]},
]
# Числа анимаций из плана того же клипа (intro_anims — они же уезжают в INTRO_ANIMS .jsx):
# блюр и масштаб СЛОЯ у раскрытия, масштаб неоткрытой БУКВЫ (Scale 3D аниматора, 11 %).
REVEAL_BLUR, REVEAL_SCALE, CHAR_SCALE = 26.8, 0.7, 0.11
# HL_DUR .jsx (core/xml2ae/layout.py) — им играет счётчик; план несёт его полем hl_dur.
CLIP_HL_DUR = 0.35
CLIP_ANIMS_JS = json.dumps({
    "glitch": {"dur": 0.44, "op_keys": [[0.0, 0], [0.05, 100], [0.1, 100], [0.1417, 0],
                                        [0.1833, 93], [0.225, 0], [0.2667, 100]]},
    "reveal": {"dur": 0.44, "blur": REVEAL_BLUR, "scale": REVEAL_SCALE,
               "scale_3d": [11, 11, 91.66667], "shape": 2, "smoothness": 100,
               "ease": [10, 95]}}, ensure_ascii=False)
# Кадры сверки и моменты слов — из тех же чисел плана выше (второй копии нет).
REVEAL_BACK_T, REVEAL_MAIN_T = 1.0, 3.0
REVEAL_BACK_WORD, REVEAL_MAIN_WORD = "что", "КУДА"
COUNT_LINE = CLIP_GROUPS[0]["lines"][1]
COUNT_WORD, COUNT_T0 = "19", COUNT_LINE["times"][1]        # слово «19» — на 2.1 с
COUNT_TARGET = COUNT_LINE["cnt"]                           # 19
COUNT_SQ = CLIP_GROUPS[0]["sq"][1][1]                      # 0.8482 — сжатие этого слова
COUNTER_T = 2.5                                            # кадр владельца: «ТЕБЕ 19»
# Прозрачность буквы ставится инлайном — по этому месту мутация и проверяется.
REVEAL_LETTER_VISIBLE = ("            chEl.style.opacity='1';\n"
                         "            chEl.style.transform=")


def _intro_clip_js(t, js85=None):
    """Стенд интро на ТЕЛЕ клипа: боевой `ipvIntro` и группы из плана сцены клипа.

    Группы проходят ту же дверь, что в бою (`introGroupWindows`: `ts`/`te` плана ->
    `inAt`/`outEnd` кадра), — иначе стенд проверял бы не боевой путь.
    """
    return _intro_js(render=True, js85=js85, t=t,
                     groups="introGroupWindows(%s)"
                            % json.dumps(CLIP_GROUPS, ensure_ascii=False),
                     plan=_intro_plan_js("hl_dur:%g,intro_anims:%s"
                                         % (CLIP_HL_DUR, CLIP_ANIMS_JS)))


def _clip_word(out, word):
    """Слово из дампа стенда (по его тексту в данных строки)."""
    for w in out["words"]:
        if w["word"] == word:
            return w
    raise AssertionError(f"в кадре нет слова {word!r}: {[w['word'] for w in out['words']]}")


def _letter_scales(word):
    """Масштаб каждой буквы слова: пустой transform — буква во весь размер (1.0)."""
    out = []
    for c in word["chars"]:
        m = re.search(r"scale\(([0-9.]+)\)", c["tr"] or "")
        out.append(1.0 if not m else float(m.group(1)))
    return out


def _css_hides_spans():
    """Прячет ли CSS любой span без класса `on` (`.ipvintro span{opacity:0}`)."""
    body = dict(_css_rules(_read(CSS))).get(".ipvintro span", "")
    return bool(re.search(r"opacity\s*:\s*0", body))


def _letters_visible(word, hidden_by_css):
    """Видна ли КАЖДАЯ буква слова — с учётом правила CSS, а не одного инлайна.

    `.ipvintro span{opacity:0}` прячет ЛЮБОЙ span без класса `on`, а `on` стоит на СЛОВЕ:
    буква `.ich` без своей прозрачности инлайном гаснет — из-за этого кадры 1.0 и 3.0 с
    в живом рендере и выходили пустыми.
    """
    if not word["chars"]:
        return False
    for c in word["chars"]:
        op = c["op"]
        if op == "":
            if hidden_by_css:
                return False
        elif float(op) <= 0:
            return False
    return True


@node
def test_reveal_letters_are_visible_at_the_start_of_the_appearance():
    """В начале раскрытия буквы ВИДНЫ (маленькие) — кадры 1.0 и 3.0 с не пустые.

    Живой прогон владельца: на 1.0 с («что») и 3.0 с («КУДА») в AE буквы уже растут
    масштабом, а наш рендер выходил ПУСТЫМ. Причина — правило `.ipvintro span{opacity:0}`:
    оно прячет каждый span без класса `on`, а `on` стоит на СЛОВЕ, и буквы `.ich`
    оставались с нулевой прозрачностью (превью очищало инлайн). В AE прозрачность буквы
    не анимируется вовсе: её ведёт Opacity СЛОЯ (`op.setValueAtTime(t0,0); …(t0+F_DUR,100);
    easePair(op)`), а букву двигает Scale 3D селектора (11 % → 100 %).
    """
    hidden = _css_hides_spans()
    assert hidden, ("правило `.ipvintro span{opacity:0}` пропало — проверка «буква видна» "
                    "перестала что-либо значить")
    for tm, word in ((REVEAL_BACK_T, REVEAL_BACK_WORD), (REVEAL_MAIN_T, REVEAL_MAIN_WORD)):
        out = _run_node(_intro_clip_js(tm))
        w = _clip_word(out, word)
        assert len(w["chars"]) == len(word), (tm, w)
        assert w["text"] == word, (tm, w)
        assert _letters_visible(w, hidden), (
            f"на {tm} с буквы слова «{word}» не видны: {w['chars']}")
        # Слой при этом ещё в появлении: прозрачность ведёт СЛОЙ, а не буква.
        assert 0.5 < float(w["op"]) < 1.0, (tm, w)
        # Открытие — рост МАСШТАБА буквы, слева направо (неоткрытая — на 11 %).
        scales = _letter_scales(w)
        assert scales == sorted(scales, reverse=True), (tm, scales)
        assert scales[0] > scales[-1] >= CHAR_SCALE - 1e-9, (tm, scales)


@node
def test_reveal_layer_plays_the_jsx_keys():
    """Масштаб и блюр раскрытия — ЛИНЕЙНО, прозрачность — по кривой: как ключи .jsx.

    Раскрытие в .jsx — это `setValueAtTime` без ease (Scale слоя 70→100, Gaussian Blur
    26.8→0, Percent Offset −100→100) и `easePair` ТОЛЬКО у Opacity. Превью вело по кривой
    всё сразу: на 1.0 с (ход 0.567) масштаб выходил 0.965 и блюр 3.1 px вместо 0.87 и
    11.6 px — буквы стояли крупнее и резче AE. Замер кадра AE (1.0 с): максимум яркости
    193 против 249 на 3.0 с — разница как раз от блюра, которого по кривой почти нет.
    """
    out = _run_node(_intro_clip_js(REVEAL_BACK_T))
    w = _clip_word(out, REVEAL_BACK_WORD)
    u = (REVEAL_BACK_T - CLIP_GROUPS[0]["lines"][0]["times"][0]) / APPEAR_DUR
    m = re.search(r"scale\(([0-9.]+)\)", w["tr"] or "")
    assert m, (w, "слой раскрытия не масштабируется вовсе")
    scale = float(m.group(1))
    bl = re.search(r"blur\(([0-9.]+)px\)", w["flt"] or "")
    assert bl, (w, "у слоя раскрытия нет блюра")
    blur = float(bl.group(1))

    assert abs(scale - (REVEAL_SCALE + (1 - REVEAL_SCALE) * u)) < 0.01, (scale, u)
    assert abs(blur - REVEAL_BLUR * (1 - u)) < 0.2, (blur, u)
    # Прозрачность — по кривой 35/90: заметно выше линейного хода (0.885 против 0.567).
    assert float(w["op"]) - u > 0.2, (w["op"], u)


@node
def test_counter_plays_hl_dur_and_matches_the_jsx_formula():
    """Счётчик идёт ЛИНЕЙНО за HL_DUR·SQ от СВОЕГО слова — как слайдер в .jsx.

    В .jsx счётчик — Slider Control с двумя ключами без ease: 0 на моменте своего слова
    (2.1 с, не 1.38 — это время «ТЕБЕ») и цель на `t0+HL_DUR*SQ` (`slP.setValueAtTime`
    в core/xml2ae/plan_intro_tpl.py), где HL_DUR = 0.35 с (core/xml2ae/layout.py; план
    несёт его полем `hl_dur`). Превью считало 1.5 с по кривой 35/90, и на кадре владельца
    2.5 с (dt = 0.4 с) выходило 10 вместо 19.
    """
    from core.xml2ae.layout import HL_DUR
    assert abs(HL_DUR - CLIP_HL_DUR) < 1e-9, (HL_DUR, CLIP_HL_DUR)
    dur = CLIP_HL_DUR * COUNT_SQ          # 0.2969 с — столько играют ключи слайдера
    # Число по формуле .jsx: Math.round(19 · clamp(dt/(HL_DUR·SQ))).
    for tm, want in ((1.6, "0"),                      # до своего слова — 0 (не «14» от 1.38)
                     (2.0, "0"),
                     (round(COUNT_T0 + dur * 3 / 4, 4), "14"),   # 19 · 0.75 = 14.25
                     (COUNTER_T, str(COUNT_TARGET))):  # 0.4 с > 0.2969 — счётчик доиграл
        out = _run_node(_intro_clip_js(tm))
        w = _clip_word(out, COUNT_WORD)
        assert w["text"] == want, f"на {tm} с счётчик показывает {w['text']!r}, а в .jsx {want!r}"


@node
def test_mutation_letters_hidden_at_the_start_are_caught():
    """Мутация: вернуть невидимость букв в начале раскрытия — проверка краснеет.

    Стенд собирается из ИСПОРЧЕННОГО исходника: буква снова остаётся без своей
    прозрачности (инлайн пустой), и правило `.ipvintro span{opacity:0}` гасит её — ровно
    то, из-за чего кадры 1.0 и 3.0 с в живом рендере выходили пустыми.
    """
    src = _read(JS85)
    assert REVEAL_LETTER_VISIBLE in src, "строка прозрачности буквы изменилась — мутация устарела"
    broken = src.replace(REVEAL_LETTER_VISIBLE,
                         "            chEl.style.opacity='';\n"
                         "            chEl.style.transform=")
    hidden = _css_hides_spans()
    assert hidden, "правило `.ipvintro span{opacity:0}` пропало — мутация ничего не гасит"
    out = _run_node(_intro_clip_js(REVEAL_BACK_T, js85=broken))
    w = _clip_word(out, REVEAL_BACK_WORD)
    assert not _letters_visible(w, hidden), (
        f"мутация не поймана: буквы всё ещё видны — {w['chars']}")
    assert [c["op"] for c in w["chars"]] == [""] * len(REVEAL_BACK_WORD), w["chars"]


@node
def test_mutation_counter_lag_is_caught():
    """Мутация: вернуть счётчику 1.5 с и кривую — проверка краснеет.

    На испорченном исходнике (прежние 1.5 с и `ipvEase`) кадр владельца 2.5 с обязан
    снова дать 10 — то самое число, что было в живом рендере.
    """
    src = _read(JS85)
    marker = ("        const uCnt=Math.min(1,Math.max(0,dt/(ipvIntroHlDur()*sq)));\n"
              "        const curVal=(+sp.dataset.cntTarget)*uCnt;")
    assert marker in src, "расчёт счётчика изменился — мутация устарела"
    broken = src.replace(marker,
                         "        const uCnt=Math.min(1,Math.max(0,dt/(1.5*sq)));\n"
                         "        const qCnt=ipvEase(uCnt);\n"
                         "        const curVal=(+sp.dataset.cntTarget)*qCnt;")
    out = _run_node(_intro_clip_js(COUNTER_T, js85=broken))
    w = _clip_word(out, COUNT_WORD)
    assert w["text"] != str(COUNT_TARGET), (
        f"мутация не поймана: с прежней формулой снова {w['text']}")
    assert w["text"] == "10", ("прежняя формула обязана дать число с кадра владельца", w)


@node
def test_render_mode_leaves_no_edit_underline_under_the_word():
    """Под словом интро нет черты правки: `.iword` теряет подчёркивание в рендере.

    Живой случай владельца: в готовом ролике под текстом интро шла тонкая линия — это
    `border-bottom:1px dashed` у слова (`.iword`), которым в превью правят текст. В
    живом превью линия обязана остаться (по ней видно, что слово кликабельно).
    """
    css = _read(CSS)
    rules = dict(_css_rules(css))
    hide = [body for sel, body in rules.items() if sel == ".render-mode .ipvintro .iword"]
    assert hide, "в app.css нет правила, гасящего подчёркивание слова интро в рендере"
    assert "border-bottom-color:transparent" in hide[0].replace(" ", ""), hide[0]
    # Линия живёт на `.iword` — это тот самый класс, что ставит ipvIntro словам.
    word = [body for sel, body in rules.items() if sel == ".iword"]
    assert word and "border-bottom" in word[0], word
    assert "className='iword'" in _read(JS85) or 'className="iword"' in _read(JS85), (
        "слова интро больше не помечаются классом .iword — правило выше гасит не то")


# --------------------------------------------------------------------------- #
# 6. Субтитры: шрифт из плана и посадка по базовой линии
# --------------------------------------------------------------------------- #
# Мини-DOM ровно под ipvSubs: контейнер, хост и строки слов. Отрисовку кадра он не
# трогает — проверяется разметка строк и её стили.
_SUBS_DOM = r"""
function ClassList(el){this.el=el;}
ClassList.prototype._l=function(){return String(this.el.className||'').split(/\s+/).filter(Boolean);};
ClassList.prototype.contains=function(c){return this._l().indexOf(c)>=0;};
ClassList.prototype.add=function(c){if(!this.contains(c)){this.el.className=(this._l().concat([c])).join(' ');}};
ClassList.prototype.remove=function(c){this.el.className=this._l().filter(function(x){return x!==c;}).join(' ');};
ClassList.prototype.toggle=function(c,on){if(on===undefined)on=!this.contains(c);if(on)this.add(c);else this.remove(c);};
// offsetTop/offsetHeight у строки и её опоры: 140 px коробка и спуск 28 px — замер
// кадра владельца (BebasNeue-Bold 140 px: winDescent 300/1000).
function El(tag){this.tag=tag;this.children=[];this.parentNode=null;this.id='';this.className='';
  this.style={setProperty:function(k,v){this[k]=String(v);},removeProperty:function(k){delete this[k];}};
  this.dataset={};this._text='';this._html='';this.classList=new ClassList(this);
  this.clientWidth=1080;this.clientHeight=1920;this.offsetTop=0;this.offsetHeight=140;}
Object.defineProperty(El.prototype,'childNodes',{get:function(){return this.children;}});
Object.defineProperty(El.prototype,'firstChild',{get:function(){return this.children[0]||null;}});
Object.defineProperty(El.prototype,'textContent',{get:function(){return this._text;},
  set:function(v){this._text=String(v==null?'':v);this.children=[];}});
__HTML_PARSE__
El.prototype.appendChild=function(c){c.parentNode=this;this.children.push(c);return c;};
El.prototype.remove=function(){if(!this.parentNode)return;
  var i=this.parentNode.children.indexOf(this);
  if(i>=0)this.parentNode.children.splice(i,1);this.parentNode=null;};
El.prototype.querySelectorAll=function(sel){
  var out=[];(function walk(el){for(var i=0;i<el.children.length;i++){out.push(el.children[i]);
    walk(el.children[i]);}})(this);
  var cls=String(sel).replace(/^\./,'');
  return out.filter(function(el){return el.classList.contains(cls);});};
El.prototype.querySelector=function(sel){var a=this.querySelectorAll(sel);return a.length?a[0]:null;};
var SUB=new El('div');SUB.id='ipvsub';
function $(id){return id==='ipvsub'?SUB:null;}
var IPV={fps:60,plan:null,renderT:2.0,playing:false};
var IPVMODE='ae';
function t(s,vars){if(!vars)return s;var o=s;for(var k in vars)o=o.split('{'+k+'}').join(String(vars[k]));return o;}
function esc(s){return String(s==null?'':s);}
function rgb2hex(c){return '#ffffff';}
function ipvRenderMode(){return IPV.renderT!=null;}
function ipvSubRawWidth(){return 0;}
function ipvSubsBgAt(){return 0;}
function ipvSubFullW(){return 0;}
function ipvSubWordAnim(){}
function keysAt(){return 0;}
function uiLog(){}
var document={documentElement:{className:''},head:new El('head'),
  getElementById:function(){return null;},
  createTextNode:function(txt){var n=new El('#text');n.textContent=txt;return n;},
  createElement:function(tag){var el=new El(tag);
    if(tag==='i'){el.offsetHeight=0;el.offsetTop=112;}   // опора базовой линии
    return el;}};
var IPV_FONT_FACES={};
// План: кегль 140, POSY 1132 — числа клипа владельца; шрифт субтитров — из ПЛАНА
// (его посчитал Python тем же стилем, что уехал в .jsx).
function planGroup(){return {w:1080,h:1920,fps:60,dur:48,posy:1132,hl_step:119.5,fsize:140,
  sub_scale:100,sub_font:'BebasNeue-Bold',sub_hl_font:null,hl_dur:0.35,hl_rise:0,layer_order:['subs','video','roto','photo','intro'],
  subs:[{s:29.1667,e:30.6833,gend:30.6833,w:'КУБИК',color:'yellow',row:0}]};}
var FONTS=[{ps:'BebasNeue-Bold',family:'Bebas Neue'},
           {ps:'SFPro-CondensedSemibold',family:'SF Pro'}];
"""


def _subs_js(curstyle, sub_font="'BebasNeue-Bold'", js85=None):
    """Стенд субтитров: боевой `ipvSubs` на плане клипа владельца.

    `curstyle` — то, что страница считает стилем: на странице рендера это `d.body.style`
    из тела сборки, а тело принимает и стиль-ИМЯ (строкой — так строит CLI).
    """
    src = js85 if js85 is not None else _read(JS85)
    return "\n".join([
        _HTML_PARSE_JS,
        _func(src, "ipvSubs"), _func(src, "ipvFontDecls"), _func(src, "ipvFontFor"),
        _func(src, "ensureFontFace"), _func(src, "ipvRenderMode"),
        _SUBS_DOM.replace("__HTML_PARSE__", ""), """
    IPV.plan=planGroup();
    IPV.plan.sub_font=%(sub_font)s;
    CURSTYLE=%(curstyle)s;
    ipvSubs(30.0);
    var host=SUB.querySelector('pvsubs_host');
    console.log(JSON.stringify({html:host?host.innerHTML:'',
      rows:(host?host.querySelectorAll('pvsubw'):[]).map(function(r){
        return {bottom:String(r.style.bottom||''),size:String(r.style.fontSize||'')};})}));
    """ % {"curstyle": curstyle, "sub_font": sub_font}])


@node
def test_subtitle_font_comes_from_the_plan_not_from_the_page_style():
    """Шрифт субтитров берётся из ПЛАНА — тем же он уехал и в .jsx.

    Тело сборки принимает стиль и ИМЕНЕМ (строкой — так его строит CLI-рендер), и тогда
    `CURSTYLE` на странице — строка: `s.font` пуст, и субтитры рисовались запасным
    SFPro-CondensedSemibold, хотя .jsx собрал BebasNeue-Bold. Замер по кадру владельца
    (30 с): «КУБИК» 344 px против 284 в AE при одинаковом кегле — расходился шрифт.
    """
    out = _run_node(_subs_js("'ДжаггерНеу'"))
    assert "reelsi-BebasNeue-Bold" in out["html"], (
        "шрифт субтитров взят не из плана — субтитры уедут запасным шрифтом: "
        + out["html"][:300])
    assert "reelsi-SFPro-CondensedSemibold" not in out["html"], out["html"][:300]
    # Обратная сторона: стиль страницы (словарём) тоже работает — план его не отменяет.
    out2 = _run_node(_subs_js("{font:'BebasNeue-Bold'}"))
    assert "reelsi-BebasNeue-Bold" in out2["html"], out2["html"][:300]


@node
def test_subtitle_rows_sit_on_the_baseline_like_ae():
    """Строка садится по БАЗОВОЙ линии (POSY в AE), а не по низу коробки.

    CSS `bottom` ставит нижний край коробки строки, а POSY в AE — базовая линия: глифы
    встают выше на спуск шрифта. Замер по кадру владельца (30 с, POSY 1132): низ глифов
    1102 у нас против 1130 в AE — те самые 28 px спуска BebasNeue-Bold при 140 px.
    Стенд меряет ЭТО: `bottom` строки опускается ровно на спуск (28/1920 от высоты кадра).
    """
    out = _run_node(_subs_js("{font:'BebasNeue-Bold'}"))
    assert out["rows"], out
    bottom = float(out["rows"][0]["bottom"].rstrip('%'))
    # POSY 1132 из 1920 — это 41.04 %; минус спуск 28 px (1.458 %) = 39.583 %.
    assert abs(bottom - ((1920 - 1132 - 28) / 1920 * 100)) < 0.01, out["rows"]
    # Разметку строки это не сломало: слово на месте.
    assert "КУБИК" in out["html"], out["html"][:200]


# --------------------------------------------------------------------------- #
# 7. Lumetri: экспозиция — стопы в ЛИНЕЙНОМ свете
# --------------------------------------------------------------------------- #
_LM_JS = r"""
// Веса приближения Lumetri — те же константы, что в 85-inserts-view.js.
const %(c)s;
function ipvLmToLin(c){return (c<=0.04045)?(c/12.92):Math.pow((c+0.055)/1.055,2.4);}
function ipvLmToSrgb(c){return (c<=0.0031308)?(c*12.92):(1.055*Math.pow(c,1/2.4)-0.055);}
function ipvLmSmooth(a,b,x){if(b<=a)return x>=b?1:0;
  var u=Math.max(0,Math.min(1,(x-a)/(b-a)));return u*u*(3-2*u);}
function ipvLmBand(lo,mid,hi,p){return ipvLmSmooth(lo,mid,p)*(1-ipvLmSmooth(mid,hi,p));}
%(tone)s
// ПРЕЖНЯЯ (до правки) формула: экспозиция в гамме плюс добавки тонов. На кадрах
// владельца она даёт плюс 13 процентов, новая обязана сойтись с эталоном в пределах 2.
function toneOld(x,lm){var ex=+lm.exposure||0,hl=+lm.highlights||0,sh=+lm.shadows||0;
  var y=x*Math.pow(2,ex);
  y+=0.25*(hl/100)*ipvLmSmooth(0.5,1,y);
  y+=0.25*(sh/100)*(1-ipvLmSmooth(0,0.5,y));
  return Math.max(0,Math.min(1,y));}
var LM=%(lm)s;
var FRAMES=%(frames)s;
// В бинах лежит НАШ кадр без цвета — это и есть источник, который красит Lumetri.
// Считаем по каналам (у них разные поканальные поправки входа), а сравниваем яркость.
function bright(f,x){var s=0;for(var c=0;c<3;c++)s+=f(x,LM,c);return s/3*255;}
var out={};
for(var key in FRAMES){var bins=FRAMES[key],n=0,sumOld=0,sumNew=0,sumAe=0;
  for(var b=0;b<bins.length;b++){var our=bins[b][0],ae=bins[b][1],cnt=bins[b][2];
    if(our>=250)continue;                       // графика: Lumetri её не касается
    sumOld+=toneOld(our/255,LM)*255*cnt;sumNew+=bright(ipvLumetriTone,our/255)*cnt;
    sumAe+=ae*cnt;n+=cnt;}
  out[key]={old:sumOld/n,new:sumNew/n,ae:sumAe/n};}
// Точность таблицы кривой: 256 точек против самой формулы.
var worst=0;
for(var i=0;i<=1000;i++){var x=i/1000;
  var exact=ipvLumetriTone(x,LM);
  var v=(i/1000)*255,w1=Math.floor(v),w2=Math.min(255,w1+1),f=v-w1;
  var tab=ipvLumetriTone(w1/255,LM)*(1-f)+ipvLumetriTone(w2/255,LM)*f;
  worst=Math.max(worst,Math.abs(tab-exact)*255);}
console.log(JSON.stringify({frames:out,tableErr:worst}));
"""


def _lm_js():
    js = _read(JS85)
    # Константы приближения берём из боевого файла, а не повторяем руками: разъедутся —
    # стенд будет проверять не то, что рисует превью.
    consts = ", ".join("%s=%s" % (n, v) for n, v in
                       re.findall(r"(IPV_LM_\w+)\s*=\s*(-?[\d.]+)", js))
    assert "IPV_LM_HL=" in consts, "в 85-inserts-view.js не нашлись константы Lumetri"
    return _LM_JS % {"c": consts,
                     "tone": _func(js, "ipvLumetriTone"),
                     "lm": json.dumps(LM_STYLE),
                     "frames": json.dumps({str(k): v for k, v in AE_CAMERA_FRAMES.items()})}


@node
def test_exposure_is_stops_in_linear_light():
    """Экспозиция Lumetri — умножение ЛИНЕЙНОЙ яркости на 2^стопы, а не гамма-значения.

    В панели AE «Exposure» — стопы: +0.5 значит «в два в степени 0.5 раза светлее», и
    считается это в линейном свете. Здесь стояло `x * 2^ex` прямо по гамме, и та же
    ручка 0.5 давала +41 % вместо +16 % — отсюда и «наш кадр светлее AE».
    """
    js = _read(JS85)
    code = """
    const %(c)s;
    %(lm)s
    function exact(x){return ipvLmToSrgb(ipvLmToLin(x)*Math.pow(2,0.5));}
    console.log(JSON.stringify({
      at02:ipvLumetriTone(0.2,{exposure:0.5}), exact02:exact(0.2),
      at05:ipvLumetriTone(0.5,{exposure:0.5}), exact05:exact(0.5),
      gamma02:0.2*Math.pow(2,0.5), zero:ipvLumetriTone(0.2,{exposure:0})}));
    """ % {"c": ", ".join("%s=%s" % (n, v) for n, v in
                          re.findall(r"(IPV_LM_\w+)\s*=\s*(-?[\d.]+)", js)),
           "lm": _func(js, "ipvLmToLin") + "\n" + _func(js, "ipvLmToSrgb") + "\n"
           + _func(js, "ipvLmSmooth") + "\n" + _func(js, "ipvLmBand") + "\n"
           + _func(js, "ipvLumetriTone")}
    out = _run_node(code)

    assert abs(out["at02"] - out["exact02"]) < 1e-9, out
    assert abs(out["at05"] - out["exact05"]) < 1e-9, out
    # По гамме вышло бы заметно светлее: 0.283 против 0.248 на x=0.2 — это и есть дефект.
    assert out["gamma02"] - out["at02"] > 0.03, out
    # Нулевая экспозиция — ни одного шага: числа кадра не меняются.
    assert abs(out["zero"] - 0.2) < 1e-9, out


@node
def test_owner_frames_mean_brightness_matches_ae():
    """Средняя яркость кадров владельца (3 и 12 с) сходится с AE в пределах 2 %.

    Числа — с живых файлов: наш кадр без цвета (источник, который красит Lumetri) и
    тот же кадр эталона AE, средние по бинам яркости. Так проверка ловит расхождение
    с AE без запуска AE. Старая формула (экспозиция в гамме) даёт +13 % — она в том же
    стенде и обязана провалиться, иначе проверка ничего не значит.
    """
    out = _run_node(_lm_js())
    for key, row in out["frames"].items():
        d_new = (row["new"] / row["ae"] - 1) * 100
        d_old = (row["old"] / row["ae"] - 1) * 100
        assert abs(d_new) < 2.0, (
            f"кадр {key} с: средняя яркость {row['new']:.2f} против {row['ae']:.2f} у AE "
            f"({d_new:+.2f} %) — расхождение больше 2 %")
        assert d_old > 10.0, (
            f"кадр {key} с: старая формула дала {d_old:+.2f} % — дефект не пойман")
    # Таблица фильтра (256 точек) обязана держать кривую: иначе цвет уедет на глаз.
    assert out["tableErr"] < 1.0, out["tableErr"]
