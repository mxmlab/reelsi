# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""«!»-кружки: знак по центру кружка во всех местах, где он стоит (замер в настоящем Chrome).

Жалоба владельца (11.10.2026): «центрани восклицательные в кружочках — все, раздражает».
Причина: у `.i` line-height наследовался от страницы (1.55 → 17.05 px) при внутренней высоте
кружка 14 px, и глиф опускался на ~1 px. Правило `.i` теперь держит line-height равным
внутренней высоте кружка (14 px). `#stpanel .i` (13 px) замерен отдельно: там 12 px дают ровно
0 px, а 11 px (внутренняя высота) — −0.5 px, поэтому оставлен 12 px.

Критерий — центр прямоугольника глифа (Range.getBoundingClientRect по текстовому узлу «!»)
против центра кружка (border-box элемента `.i`): |Δx| ≤ 0.5 px и |Δy| ≤ 0.5 px.

Четыре места: шапка карточки (разметка из templates/index.html), строка настроек (тоже из
index.html), панель стиля (#stpanel — разметка повторяет 94-stylepanel.js: JS в стенде не
запускается), шапка таймлайна превью вставок (весь блок #mbInserts из index.html, как в
test_wv_ins_modal_geometry).

Стенд — безголовый Chrome на настоящем static/app.css; копии CSS в тесте нет.

Запуск: py -3.10 -m pytest tests/test_ui_geometry_info.py -q -p no:cacheprovider
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
CSS = os.path.join(ROOT, "static", "app.css")
HTML = os.path.join(ROOT, "templates", "index.html")

_CHROME = (shutil.which("chrome")
           or shutil.which("chrome.exe")
           or r"C:\Program Files\Google\Chrome\Application\chrome.exe")
chrome = pytest.mark.skipif(not os.path.exists(_CHROME), reason="нет Chrome для замера «!»")

TOL = 0.5   # px, допуск по каждой оси

_SANDBOX_ERR = ("Access is denied", "crash server failed to launch", "platform_channel")


def _read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def _card_markup(html):
    """Шапка карточки «Материал с гугл-диска» из index.html — с «!» (id приделан стенду)."""
    m = re.search(r'<h2><span class="k">◆</span>Материал с гугл-диска.*?</h2>', html)
    assert m, "нет шапки карточки «Материал с гугл-диска» в index.html"
    return m.group(0).replace('class="i"', 'class="i" id="geo_card"', 1)


def _setting_markup(html):
    """Строка настроек «Камер» из index.html — подпись с «!» (id приделан стенду)."""
    m = re.search(r'<label>Камер <span class="i"[^>]*>!</span></label>', html)
    assert m, "нет строки настроек «Камер» в index.html"
    return m.group(0).replace('class="i"', 'class="i" id="geo_setting"', 1)


# Панель стиля: JS (94-stylepanel.js) строит строку поля как .stfield-name + .i — повторяем
# именно эту структуру, иначе замер не отражает то, что видит пользователь.
STYLE_PANEL = ('<div id="stpanel" class="stpanel" style="width:420px">'
               '<div class="stfield"><div class="stfield-name"><label>Заголовок</label>'
               '<span class="i" id="geo_style" data-t="справка">!</span></div></div></div>')


def _timeline_markup(html):
    """Шапка таймлайна превью вставок — весь блок #mbInserts из index.html, как есть.

    Иконки JS рисует сам; в стенде пустые <span data-ic> заменяем svg-заглушкой того же
    размера, иначе строка меряла бы себя по подписи (см. test_wv_ins_modal_geometry)."""
    i = html.index('<div class="backdrop" id="mbInserts"')
    tail = html[i:]
    end = tail.index("<!-- ============ MODAL: camera layout")
    block = tail[:tail.rindex("</div>", 0, end) + len("</div>")]
    return re.sub(r'<span data-ic="[^"]*"[^>]*></span>',
                  '<svg class="ic" viewBox="0 0 24 24"></svg>', block)


# Что меряем: id элемента (или css-селектор) и подпись для сообщения об ошибке.
TARGETS = (
    ("#geo_card", "шапка карточки"),
    ("#geo_setting", "строка настроек"),
    ("#geo_style", "панель стиля"),
    ("#mbInserts .itlbar.tlhead .i", "шапка таймлайна превью вставок"),
)

_MEASURE_JS = """
window.addEventListener('load', function () {
  document.getElementById('mbInserts').classList.add('on');
  var targets = %s;
  var out = [];
  targets.forEach(function (tg) {
    var el = document.querySelector(tg[0]);
    if (!el) { out.push({name: tg[1], err: 'нет элемента ' + tg[0]}); return; }
    var box = el.getBoundingClientRect();
    var txt = null;
    for (var n = el.firstChild; n; n = n.nextSibling) {
      if (n.nodeType === 3 && n.textContent.trim() === '!') { txt = n; break; }
    }
    if (!txt) { out.push({name: tg[1], err: 'в .i нет текста «!»'}); return; }
    var rg = document.createRange();
    rg.selectNodeContents(txt);
    var g = rg.getBoundingClientRect();
    out.push({name: tg[1],
      circle: [box.width, box.height],
      dx: (g.left + g.width / 2) - (box.left + box.width / 2),
      dy: (g.top + g.height / 2) - (box.top + box.height / 2)});
  });
  var pre = document.createElement('pre');
  pre.id = 'geom';
  pre.textContent = JSON.stringify(out);
  document.body.appendChild(pre);
});
"""


def _page(css_url, html):
    """Страница стенда: настоящий CSS, четыре места с «!», модалка вставок открыта."""
    targets = json.dumps([[sel, name] for sel, name in TARGETS], ensure_ascii=False)
    return """<!DOCTYPE html>
<html><head><meta charset="utf-8"><link rel="stylesheet" href="%s">
<style>html,body{margin:0;padding:0}
/* Замер снимается без анимации появления: иначе кружки меряются в масштабе ~0.985 */
#mbInserts .modal{animation:none !important}
#geo_host{position:absolute;left:0;top:0;width:640px}</style></head><body>
<div id="geo_host">%s%s%s</div>
%s
<script>%s</script>
</body></html>""" % (css_url, _card_markup(html), _setting_markup(html), STYLE_PANEL,
                     _timeline_markup(html), _MEASURE_JS % targets)


def _run_chrome(tmp_path, page):
    """Замер в безголовом Chrome; None — Chrome в этом окружении не запускается вовсе."""
    p = tmp_path / "geo_info.html"
    p.write_text(page, encoding="utf-8")
    cmd = [_CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
           "--window-size=1280,900", "--virtual-time-budget=3000",
           "--dump-dom", p.resolve().as_uri()]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=90)
    if proc.returncode != 0:
        if any(e in proc.stderr for e in _SANDBOX_ERR):
            return None
        raise AssertionError("Chrome не отработал: %s" % proc.stderr[:400])
    m = re.search(r'<pre id="geom">(.*?)</pre>', proc.stdout, re.DOTALL)
    assert m, "нет замеров в выводе Chrome: %s" % proc.stdout[:400]
    return json.loads(m.group(1))


@chrome
@pytest.mark.xdist_group("chrome")
def test_info_mark_glyph_is_centered_in_its_circle(tmp_path):
    """Центр «!» совпадает с центром кружка в каждом из четырёх мест: |Δx| и |Δy| ≤ 0.5 px.

    Мутация: убрать `line-height:14px` из правила `.i` — Δy станет +1 px у всех кружков 16 px,
    и тест краснеет на первом же месте."""
    html = _read(HTML)
    css_url = os.path.abspath(CSS).replace("\\", "/")
    rows = _run_chrome(tmp_path, _page("file:///" + css_url, html))
    if rows is None:
        pytest.skip("Chrome в песочнице не запускается (platform_channel) — замер не сделан")
    report = "; ".join("%s: Δx=%.2f Δy=%.2f" % (r["name"], r["dx"], r["dy"]) for r in rows if "dx" in r)
    for r in rows:
        assert "err" not in r, "%s: %s" % (r["name"], r["err"])
    assert len(rows) == len(TARGETS), "замерено мест меньше четырёх: %s" % report
    for r in rows:
        assert abs(r["dx"]) <= TOL, "«!» не по центру по X — %s (%s)" % (r["name"], report)
        assert abs(r["dy"]) <= TOL, "«!» не по центру по Y — %s (%s)" % (r["name"], report)
