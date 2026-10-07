# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Окно «Вставки» — постоянный размер: высота одна и та же при 2 и при 30 вставках.

Жалоба владельца (02.10.2026): «когда много вставок — уезжает за экран внизу таймлайн,
когда удаляю их он становится меньше; хочу чтобы он был на одном экране константно».
Причина: высоту модалки задавала колонка карточек (.inshostscroll с max-height:88vh-150px),
и вместе с ней ехали таймлайн и кнопки. Теперь модалка тянется от окна браузера
(#mbInserts .modal, образец — #mbAISettings), а прокручивается ТОЛЬКО список карточек.

Жалоба владельца (03.10.2026): «окно — маленький прямоугольник посреди экрана, видео
крошечное». Причина: потолок min(720px,…) у каркаса и базовый .pvstage{max-width:320px},
державший кадр 320x569 на любой высоте окна. Теперь высота — 94vh, как у превью шага 3,
а кадр считается от высоты колонки (потолки 480px у .inspv и 320px у .pvstage сняты).

Жалоба владельца (03.10.2026, второй раз): «что ты сделал с 3-й страницей, я не просил
визуально её менять». Каркас окна вставок действовал на ОБЕ ступени — правило по id
(#mbInserts .modal …) перебивало .modal.aemode по специфичности, и в превью AE вылезала
лишняя колонка карточек, а панель слов ужималась. Теперь каркас помечен `:not(.aemode)`:
шаг 2 — новая раскладка, шаг 3 — ровно прежняя (сверяется со стендом на CSS из cc632e3^).

Стенд — безголовый Chrome на РЕАЛЬНОЙ разметке модалки из templates/index.html и
РЕАЛЬНОМ static/app.css: копии разметки в тесте нет, иначе он стерёг бы свою копию.

Запуск: py -3.10 -m pytest tests/test_wv_ins_modal_geometry.py -q
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

CSS = os.path.join(ROOT, "static", "app.css")
HTML = os.path.join(ROOT, "templates", "index.html")

_CHROME = (shutil.which("chrome")
           or shutil.which("chrome.exe")
           or r"C:\Program Files\Google\Chrome\Application\chrome.exe")
chrome = pytest.mark.skipif(not os.path.exists(_CHROME), reason="нет Chrome для замера геометрии")


def _read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def _modal_markup():
    """Блок #mbInserts из index.html — как есть (разметку не копируем, а вырезаем)."""
    html = _read(HTML)
    i = html.index('<div class="backdrop" id="mbInserts"')
    tail = html[i:]
    end = tail.index("<!-- ============ MODAL: camera layout")   # следующий блок страницы
    return tail[:tail.rindex("</div>", 0, end) + len("</div>")]


def _measure(width, height, cards, css_file=CSS, aemode=False):
    """Собрать страницу с настоящей модалкой и настоящим CSS, замерить её в Chrome.
    width/height — как у окна Chrome (--window-size), отдаются в _run_chrome; видимую
    высоту страница берёт у браузера, height — только фолбэк. Размеров странице не
    навязываем: при body 1440x1000 и вьюпорте 909 прокрутка страницы была бы всегда.
    aemode=True — та же модалка в режиме превью AE (шаг 3); css_file — какой CSS подставить
    (на нём же сверяется раскладка шага 3 с ревизией до cc632e3).
    """
    css_url = os.path.abspath(str(css_file)).replace("\\", "/")
    markup = _modal_markup()
    page = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><link rel="stylesheet" href="file:///%s">
<style>html,body{margin:0;padding:0;width:100%%;height:100%%}
/* Ширину и высоту страница НЕ навязывает: раньше тут стояло body 1440x1000 при вьюпорте
   1432x909, и прокрутку страницы давала арифметика самого стенда (sh=1000, sw=1440) —
   «модалка растянула страницу» краснело при любой геометрии окна. Вывод замеров — вне
   потока: длинная строка JSON не должна добавлять странице прокрутку. */
#geom{position:absolute;left:0;top:0;width:0;height:0;overflow:hidden}</style></head>
<body>
%s
<script>
var CARD="<div class='inscard' style='margin-top:10px'><div class='nm'>ins</div></div>";
var AE=%s;
window.addEventListener('load',function(){
  var host=document.getElementById('insHost');
  for(var i=0;i<%d;i++)host.insertAdjacentHTML('beforeend',CARD);
  document.getElementById('inspart_subs').style.display='none';
  document.getElementById('mbInserts').classList.add('on');
  if(AE){
    document.querySelector('#mbInserts .modal').classList.add('aemode');
    var w=document.getElementById('aewwords');
    for(var j=0;j<6;j++)w.insertAdjacentHTML('beforeend',"<span class='chip'>слово"+j+"</span>");
  }
  var vh=(document.documentElement.clientHeight||%d);
  var g=function(id){var el=document.getElementById(id);var r=el.getBoundingClientRect();
    return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,width:r.width,height:r.height,
            clientH:el.clientHeight,scrollH:el.scrollHeight};};
  var gq=function(s){var el=document.querySelector(s);var r=el.getBoundingClientRect();
    return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,width:r.width,height:r.height,
            clientH:el.clientHeight,scrollH:el.scrollHeight,display:getComputedStyle(el).display};};
  var res={vh:vh,innerH:window.innerHeight,modal:g('mbInserts'),tl:g('itl'),host:g('insHost'),
    side:g('inspart_inserts'),stage:g('ipvstage'),
    pv:gq('#mbInserts .inspv'),mid:gq('#aewpanel'),words:gq('#aewwords'),
    page:{sw:document.documentElement.scrollWidth,cw:document.documentElement.clientWidth,
          sh:document.documentElement.scrollHeight,ch:document.documentElement.clientHeight}};
  var pre=document.createElement('pre');pre.id='geom';pre.textContent=JSON.stringify(res);
  document.body.appendChild(pre);
});
</script>
</body></html>""" % (css_url, markup, "true" if aemode else "false", cards, height)
    return page


_SANDBOX_ERR = ("Access is denied", "crash server failed to launch", "platform_channel")


def _run_chrome(tmp_path, name, page, width, height):
    """Замер в безголовом Chrome; None — Chrome в этом окружении не запускается вовсе.

    В песочнице агента Windows-Chrome не может открыть свои mojo-каналы (named pipes):
    «FATAL: platform_channel.cc: Check failed: Access is denied». Это отказ среды, а не
    модалки, — тест обязан сказать об этом вслух (skip с причиной), а не краснеть зря.
    """
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


OLD_CSS_REV = "cc632e3^"   # ревизия ДО правки каркаса окна вставок — эталон вида шага 3


def _old_css(tmp_path):
    """CSS из ревизии до cc632e3: с ним сверяется раскладка шага 3 («ровно как было»).

    Сравнивать не с числами, вписанными в тест, а со стендом на том самом CSS: числа
    переживут правку соседних правил, а два стенда разойдутся. Ревизии в этом клоне нет
    (например, публичный срез без приватной истории) — возвращаем None, тест скажет вслух.
    """
    try:
        proc = subprocess.run(["git", "-C", ROOT, "show", OLD_CSS_REV + ":static/app.css"],
                              capture_output=True, text=True, encoding="utf-8", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or ".modal.aemode" not in (proc.stdout or ""):
        return None
    path = tmp_path / "app_before_cc632e3.css"
    path.write_text(proc.stdout, encoding="utf-8")
    return path


@chrome
@pytest.mark.xdist_group("chrome")
def test_aemode_preview_layout_is_the_same_as_before_cc632e3(tmp_path):
    """Шаг 3 (`.aemode`): раскладка колонок ровно как на CSS до cc632e3 (ширины ±2 px).

    Жалоба владельца (03.10.2026): «в превью AE средняя колонка «Вставки» пустая, карточек
    не видно, остальное съехало». Каркас шага 2 (#mbInserts .modal и потомки) перебивал
    .modal.aemode по специфичности id: в превью AE вылезала лишняя колонка карточек, плеер
    становился шире, панель слов ужималась.

    Колонка карточек в шаге 3 скрыта ЕЩЁ ДО cc632e3 (.modal.aemode .insside{display:none}):
    середина превью AE — панель слов, её список и проверяем (виден, высота > 200 px при 5+
    карточках вставок в стенде), а всю раскладку сверяем со стендом на прежнем CSS — там в
    сравнении и колонка карточек, и кадр: если они вернутся в раскладку шага 3, ширины
    разойдутся.

    Мутация: снять `:not(.aemode)` с каркаса шага 2 — у превью AE лишняя колонка карточек и
    широкий плеер, первое же сравнение краснеет.
    """
    old_css = _old_css(tmp_path)
    if old_css is None:
        pytest.skip("в клоне нет ревизии %s — сравнить раскладку шага 3 не с чем" % OLD_CSS_REV)
    before = _run_chrome(tmp_path, "ae_before.html",
                         _measure(1280, 900, 6, css_file=old_css, aemode=True), 1280, 900)
    now = _run_chrome(tmp_path, "ae_now.html", _measure(1280, 900, 6, aemode=True), 1280, 900)
    if before is None or now is None:
        pytest.skip("безголовый Chrome в песочнице не стартует (mojo/named pipes): "
                    "геометрию превью AE прогнать не удалось")

    # Список середины превью AE не сжат каркасом шага 2.
    assert now["words"]["height"] > 200, (
        "список средней колонки превью AE сжался: %s" % now["words"])

    boxes = (("modal", "окно"), ("pv", "колонка плеера"), ("stage", "кадр"),
             ("side", "колонка вставок"), ("host", "список карточек"),
             ("mid", "панель слов"), ("words", "список слов"), ("tl", "таймлайн"))
    for key, ru in boxes:
        for prop in ("left", "width", "height"):
            assert abs(now[key][prop] - before[key][prop]) <= 2, (
                "раскладка шага 3 разошлась с прежней: у %s %s = %s, было %s"
                % (ru, prop, now[key][prop], before[key][prop]))


@chrome
@pytest.mark.xdist_group("chrome")
def test_inserts_modal_height_is_constant_with_2_and_30_inserts(tmp_path):
    """Высота окна и положение таймлайна не зависят от числа карточек (1280x900)."""
    few = _run_chrome(tmp_path, "ins2.html", _measure(1280, 900, 2), 1280, 900)
    many = _run_chrome(tmp_path, "ins30.html", _measure(1280, 900, 30), 1280, 900)
    if few is None or many is None:
        pytest.skip("безголовый Chrome в песочнице не стартует (mojo/named pipes): "
                    "геометрию окна вставок прогнать не удалось")

    assert few["modal"]["height"] == many["modal"]["height"], (
        "окно вставок меняет высоту от числа вставок: %s против %s"
        % (few["modal"]["height"], many["modal"]["height"]))
    assert few["modal"]["height"] <= few["vh"], (
        "окно выше видимой области: %s при vh=%s" % (few["modal"], few["vh"]))
    assert few["modal"]["height"] >= 0.6 * few["vh"], (
        "окно не занимает видимый экран: %s при vh=%s" % (few["modal"], few["vh"]))
    assert few["tl"]["top"] == many["tl"]["top"] and few["tl"]["bottom"] == many["tl"]["bottom"], (
        "таймлайн съезжает от числа вставок: %s против %s" % (few["tl"], many["tl"]))
    assert many["tl"]["bottom"] <= many["modal"]["bottom"] + 1, (
        "таймлайн уехал за нижний край окна: tl=%s modal=%s" % (many["tl"], many["modal"]))

    # Прокручивается колонка карточек, а не всё окно: 30 карточек не влезают.
    assert many["host"]["scrollH"] > many["host"]["clientH"], (
        "список карточек не прокручивается внутри себя: %s" % many["host"])
    assert few["host"]["scrollH"] <= few["host"]["clientH"], (
        "две карточки не помещаются в список: %s" % few["host"])


@chrome
@pytest.mark.xdist_group("chrome")
def test_inserts_modal_full_height_and_no_page_scroll(tmp_path):
    """Окно занимает видимый экран (как другие полноэкранные модалки) и не даёт прокрутки страницы."""
    m = _run_chrome(tmp_path, "ins_full.html", _measure(1440, 1000, 30), 1440, 1000)
    if m is None:
        pytest.skip("безголовый Chrome в песочнице не стартует (mojo/named pipes): "
                    "геометрию окна вставок прогнать не удалось")
    assert m["modal"]["height"] >= 0.6 * m["vh"], (
        "окно не занимает видимый экран: %s при vh=%s" % (m["modal"], m["vh"]))
    assert m["modal"]["top"] >= 0, "верх окна за краем экрана: %s" % m["modal"]["top"]
    assert m["page"]["sh"] <= m["page"]["ch"] + 1, (
        "модалка растянула страницу по вертикали: %s" % m["page"])
    assert m["page"]["sw"] <= m["page"]["cw"] + 1, (
        "модалка растянула страницу по горизонтали: %s" % m["page"])


@chrome
@pytest.mark.xdist_group("chrome")
def test_inserts_modal_fills_1080p_and_video_frame_is_large(tmp_path):
    """1920x1080: окно вставок занимает экран, кадр видео — не меньше 500 px по высоте.

    Жалоба владельца (03.10.2026): «окно — маленький прямоугольник посреди экрана, видео
    крошечное». Причина — потолок min(720px,…) у #mbInserts .modal и базовый max-width:320px
    у .pvstage: модалка 720 при экране 1080, кадр 320x569.

    Мутация: вернуть в #mbInserts .modal высоту min(720px,calc(100vh - 80px)) — окно станет
    720 при innerHeight 1080, и первая же проверка краснеет.
    """
    m = _run_chrome(tmp_path, "ins_1920.html", _measure(1920, 1080, 30), 1920, 1080)
    if m is None:
        pytest.skip("безголовый Chrome в песочнице не стартует (mojo/named pipes): "
                    "геометрию окна вставок прогнать не удалось")
    vh = m["innerH"] or m["vh"]
    assert m["modal"]["height"] >= 0.9 * vh, (
        "окно вставок не занимает экран: %s при innerHeight=%s" % (m["modal"], vh))
    assert m["stage"]["height"] >= 500, (
        "кадр видео маленький (нужно >=500px по высоте): кадр=%s при окне %s"
        % (m["stage"], m["modal"]))
    assert m["modal"]["top"] >= 0 and m["modal"]["bottom"] <= vh + 1, (
        "окно не влезло в видимую область: %s при innerHeight=%s" % (m["modal"], vh))
    assert m["tl"]["bottom"] <= m["modal"]["bottom"] + 1, (
        "таймлайн уехал за нижний край окна: tl=%s modal=%s" % (m["tl"], m["modal"]))
    assert m["page"]["sh"] <= m["page"]["ch"] + 1, (
        "модалка растянула страницу по вертикали: %s" % m["page"])
    assert m["page"]["sw"] <= m["page"]["cw"] + 1, (
        "модалка растянула страницу по горизонтали: %s" % m["page"])


@chrome
@pytest.mark.xdist_group("chrome")
def test_inserts_modal_720p_keeps_timeline_in_window(tmp_path):
    """1280x720 — как было: таймлайн внутри окна и виден, прокрутки страницы нет.

    Каркас 94vh на низком окне вместе с отступами backdrop (40+40) чуть выше видимой
    области (94vh + 40 > vh при vh < 667) — это ок, backdrop прокручивается сам; важно,
    что таймлайн остаётся в окне, а страница не растёт.
    """
    m = _run_chrome(tmp_path, "ins_720.html", _measure(1280, 720, 30), 1280, 720)
    if m is None:
        pytest.skip("безголовый Chrome в песочнице не стартует (mojo/named pipes): "
                    "геометрию окна вставок прогнать не удалось")
    assert m["modal"]["top"] >= 0, "верх окна за краем экрана: %s" % m["modal"]["top"]
    assert m["modal"]["height"] <= m["vh"] + 1, (
        "окно выше видимой области: %s при vh=%s" % (m["modal"], m["vh"]))
    assert m["stage"]["height"] > 0, "кадр видео схлопнулся: %s" % m["stage"]
    assert m["tl"]["bottom"] <= m["modal"]["bottom"] + 1, (
        "таймлайн уехал за нижний край окна: tl=%s modal=%s" % (m["tl"], m["modal"]))
    assert m["tl"]["bottom"] <= m["vh"] + 1, (
        "таймлайн не виден на экране: tl=%s при vh=%s" % (m["tl"], m["vh"]))
    assert m["page"]["sh"] <= m["page"]["ch"] + 1, (
        "модалка растянула страницу по вертикали: %s" % m["page"])
    assert m["page"]["sw"] <= m["page"]["cw"] + 1, (
        "модалка растянула страницу по горизонтали: %s" % m["page"])


def test_inserts_modal_css_frame_is_fixed_by_height():
    """Каркас окна: модалка от окна браузера, прокрутка — только у списка карточек."""
    css = _read(CSS).replace(" ", "")
    m = re.search(r"#mbInserts\.modal:not\(\.aemode\)\{([^}]+)\}", css)
    assert m, "нет правила #mbInserts .modal:not(.aemode) (каркас постоянного размера)"
    rule = m.group(1)
    # Высота — как у превью шага 3 (94vh), без потолка в пикселях: с min(720px,…) на 1080p
    # окно ужималось вдвое (жалоба владельца 03.10.2026).
    assert "height:94vh" in rule and "max-height:94vh" in rule, (
        "высота окна не как у шага 3 (94vh): %s" % rule)
    assert "min(720px" not in rule, (
        "вернулся потолок 720px — на 1080p окно снова маленькое: %s" % rule)
    assert "overflow:hidden" in rule, "у модалки нет overflow:hidden: %s" % rule

    # Колонка плеера: ширина считается от высоты окна, потолок 480px больше кадр не держит.
    m = re.search(r"#mbInserts\.modal:not\(\.aemode\)\.inspv\{([^}]+)\}", css)
    assert m, "нет правила #mbInserts .modal:not(.aemode) .inspv (колонка плеера)"
    col = m.group(1)
    assert "94vh" in col, "ширина колонки плеера не считается от новой высоты модалки: %s" % col
    assert ",480px)" not in col, (
        "потолок ширины плеера остался 480px — на высоком экране кадр не растёт: %s" % col)
    assert "min-height:0" in col and "container-type:size" in col, (
        "кадр снова считался бы не от высоты своей колонки: %s" % col)

    # Кадр: базовый .pvstage{max-width:320px} держал его 320x569 на любой высоте окна.
    m = re.search(r"#mbInserts\.modal:not\(\.aemode\)\.inspv\.pvstage\{([^}]+)\}", css)
    assert m, "нет правила #mbInserts .modal:not(.aemode) .inspv .pvstage (кадр плеера)"
    stage = m.group(1)
    assert "max-width:none" in stage, (
        "базовый max-width:320px снова держит кадр маленьким: %s" % stage)
    assert "aspect-ratio:var(--stage-ar" in stage, "кадр потерял пропорцию плана: %s" % stage

    m = re.search(r"#mbInserts\.modal:not\(\.aemode\)\.inscols\{([^}]+)\}", css)
    assert m and "overflow:hidden" in m.group(1), "колонки не замкнуты по высоте"
    m = re.search(r"#mbInserts\.modal:not\(\.aemode\)\.inshostscroll\{([^}]+)\}", css)
    assert m, "нет правила #mbInserts .modal:not(.aemode) .inshostscroll"
    host = m.group(1)
    assert "flex:1" in host and "min-height:0" in host, (
        "список карточек не растягивается на свободную высоту модалки: %s" % host)
    assert "overflow-y:auto" in host, (
        "список карточек не прокручивается сам: %s" % host)
    assert "max-height:calc(88vh" not in host, (
        "вернулся max-height от высоты окна — размер снова поедет: %s" % host)

    # Цепочка .modal → .mbody → .inscols → .insside → колонка карточек → .inshostscroll
    # обязана быть замкнута на КАЖДОМ звене: одно звено обычным блоком вместо flex-контейнера
    # (так и было с #inspart_inserts) — и flex:1 у списка ничего не ограничивает, список
    # вырастает по содержимому, а окно с таймлайном уезжает за экран.
    m = re.search(r"#mbInserts\.modal:not\(\.aemode\)#inspart_inserts\{([^}]+)\}", css)
    assert m, "нет правила #mbInserts .modal:not(.aemode) #inspart_inserts — звено цепочки до списка карточек"
    link = m.group(1)
    assert "flex:1" in link and "min-height:0" in link and "display:flex" in link, (
        "колонка карточек не замкнута по высоте — список снова вырастет по содержимому: %s" % link)

    # Шаг 3 (aemode) открывает ту же модалку и держит СВОЙ каркас — 94vh. Каркас шага 2
    # помечен :not(.aemode) и до него не достаёт; страж селекторов — в
    # test_step2_frame_does_not_touch_step3, геометрия — в тесте aemode выше.
    assert re.search(r"\.modal\.aemode\{[^}]*height:94vh", css), (
        "у шага 3 отняли собственный каркас высоты")
    assert "#mbInserts.modal.aemode{" not in css, (
        "вернулся костыль #mbInserts .modal.aemode: каркас шага 2 снова метит в шаг 3")

    # Узкий экран: колонки друг под другом, но прокрутки страницы вбок быть не должно —
    # фиксированную ширину плеера (200px в basis) в этой ширине снимаем.
    start = css.index("@media(max-width:768px){")
    block = css[start:css.rindex("}", start, css.find("@media(", start + 1)
                                  if css.find("@media(", start + 1) >= 0 else len(css)) + 1]
    assert "#mbInserts.modal:not(.aemode).inspv{" in block, (
        "плеер не сжимается на узком экране: %s" % block)
    for decl in ("flex:none", "max-width:100%", "height:auto", "max-height:100%"):
        assert decl in block, "плееру на узком экране не хватает %s: %s" % (decl, block)
    assert "#mbInserts.modal:not(.aemode).insside{max-width:100%;overflow-x:hidden}" in block, (
        "колонка карточек на узком экране не замкнута по ширине: %s" % block)


def test_step2_frame_does_not_touch_step3():
    """Все правила каркаса окна вставок помечены `:not(.aemode)` — шага 3 не касаются.

    Шаг 3 открывает ТУ ЖЕ модалку (.modal.wide.aemode): правило по id
    (#mbInserts .modal …) перебивает .modal.aemode по специфичности, и превью AE уезжало за
    каркасом шага 2 — лишняя колонка карточек, широкий плеер, сжатая панель слов
    (жалоба владельца 03.10.2026). Здесь страж на сами селекторы: любому правилу с
    #mbInserts в селекторе положено `:not(.aemode)`.
    """
    css = _read(CSS)
    body = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)   # комментарии — не правила
    selectors = [s.strip() for s in re.findall(r"([^{}]*?)\{", body) if "#mbInserts" in s]
    assert selectors, "в app.css пропали правила окна вставок"
    for sel in selectors:
        assert ":not(.aemode)" in sel, (
            "правило каркаса окна вставок действует и на шаге 3: %s" % sel)

    # И сам каркас шага 3 — прежний: своя высота и прежние числа колонки плеера (до cc632e3).
    flat = css.replace(" ", "")
    m = re.search(r"\.modal\.aemode\{([^}]+)\}", flat)
    assert m and "height:94vh" in m.group(1), (
        "у шага 3 нет своей высоты 94vh: %s" % (m.group(1) if m else "правила нет"))
    m = re.search(r"\.modal\.aemode\.inspv\{([^}]+)\}", flat)
    assert m, "нет правила .modal.aemode .inspv (колонка плеера шага 3)"
    col = m.group(1)
    assert "480px" in col and "360px" in col, (
        "колонка плеера шага 3 ушла от прежних чисел (потолок 480px, вычет 360px): %s" % col)


def test_inserts_modal_width_is_the_same_as_step3():
    """Ширина шага 2 = ширина шага 3: обе ступени открывают ОДНУ модалку .modal.wide.

    Своего правила ширины у aemode нет — значит, требование «та же ширина» выполняется
    ровно тогда, когда модалка шага 2 остаётся .modal.wide. Проверка сторожит именно это:
    если шагу 3 заведут собственный max-width или у шага 2 отберут .wide, шаг 2 станет уже.
    """
    css = _read(CSS).replace(" ", "")
    m = re.search(r"\.modal\.wide\{([^}]+)\}", css)
    assert m, "нет правила .modal.wide — на нём держится ширина обеих ступеней"
    wide = m.group(1)
    assert "1560px" in wide and "97vw" in wide, "у .modal.wide отняли ширину: %s" % wide

    m = re.search(r"\.modal\.aemode\{([^}]+)\}", css)
    assert m, "нет правила .modal.aemode"
    assert "width" not in m.group(1), (
        "у шага 3 завелось своё правило ширины — шаг 2 обязан повторить его: %s" % m.group(1))

    html = _read(HTML)
    i = html.index('<div class="backdrop" id="mbInserts"')
    assert '<div class="modal wide"' in html[i:i + 400], (
        "модалка вставок потеряла .modal.wide — ширина шага 2 разошлась с шагом 3")
