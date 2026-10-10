# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Стенд настоящего Chrome для поведенческих проверок интерфейса (серия B3, порция 3).

Страница — та же, что отдаёт сервер (webui._page: index.html + static/app/*.js), но открывается
из file:// без сервера: ссылки /static/ переписываются на файлы проекта, а вызовы /api/ идут в
заглушку fetch, которую сценарий наполняет ответами (window.__api). Сценарий — список шагов,
которые драйвер _chrome_driver.js выполняет через DevTools Protocol: JS на странице, пауза,
события мыши и перетаскивание. Ввод — настоящий ввод браузера, а не вызов обработчиков.

Тесты, которые ходят сюда, помечаются xdist_group("chrome") (нужны -n auto --dist loadgroup).
"""
import json
import os
import pathlib
import shutil
import socket
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DRIVER = os.path.join(HERE, "_chrome_driver.js")
CHROME = (shutil.which("chrome") or shutil.which("chrome.exe")
          or r"C:\Program Files\Google\Chrome\Application\chrome.exe")
NODE = shutil.which("node")

chrome_stand = pytest.mark.skipif(
    not (os.path.exists(CHROME) and NODE),
    reason="поведенческий стенд Chrome требует Chrome и node")

# Заглушка fetch: /api/… идёт в обработчик из window.__api (по подстроке пути), иначе — пустой отказ.
API_STUB = r"""
window.__api = {};
// /api/… — точное совпадение пути (без строки запроса) с ключом window.__api
window.fetch = function (url, opts) {
  var path = String(url).split('?')[0], h = window.__api[path] || null;
  var d = h ? h(String(url), opts) : {ok: false, error: 'нет заглушки'};
  return Promise.resolve({ok: true, status: 200,
    json: function () { return Promise.resolve(d); },
    text: function () { return Promise.resolve(JSON.stringify(d)); }});
};
// Медиа: <video>/<audio> грузят /api/media?path=… — в стенде это тот же файл с диска (file://).
// Перехват только адреса источника; функции предпросмотра (pvSrc, bufMake, …) не трогаются.
(function () {
  var desc = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'src');
  Object.defineProperty(HTMLMediaElement.prototype, 'src', {
    configurable: true, enumerable: desc.enumerable,
    get: function () { return desc.get.call(this); },
    set: function (v) {
      v = String(v);
      var m = /^\/api\/media\?path=(.*)$/.exec(v);
      if (m) v = 'file:///' + encodeURI(decodeURIComponent(m[1]).split(String.fromCharCode(92)).join('/'));
      window.__srcLog = window.__srcLog || [];
      window.__srcLog.push([this, v, performance.now()]);
      desc.set.call(this, v);
    }});
  // перемотка: счётчик на самом элементе (стенд не меняет поведение, только считает)
  var ct = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'currentTime');
  Object.defineProperty(HTMLMediaElement.prototype, 'currentTime', {
    configurable: true, enumerable: ct.enumerable,
    get: function () { return ct.get.call(this); },
    set: function (t) { this.__seeks = (this.__seeks || 0) + 1; ct.set.call(this, t); }});
})();
"""


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def page_url(tmp_path) -> str:
    """Файл страницы в tmp_path: та же разметка, что у сервера, с файловыми ссылками на static/."""
    import webui  # импорт здесь: тесты без Chrome не тянут Flask-приложение

    html = webui._page()
    static_uri = pathlib.Path(ROOT, "static").as_uri()
    html = html.replace('"/static/', '"%s/' % static_uri)
    html = html.replace("<head>", "<head><script>" + API_STUB + "</script>", 1)
    path = pathlib.Path(tmp_path, "page.html")
    path.write_text(html, encoding="utf-8")
    return path.as_uri()


def run_steps(tmp_path, steps, *, w=1280, h=900, settle_ms=400):
    """Открыть страницу в настоящем Chrome, выполнить шаги, вернуть results (dict).

    Ошибка драйвера или JS на странице — AssertionError с текстом (тест покажет, что сломалось).
    """
    url = page_url(tmp_path)
    spec = {"chrome": CHROME, "port": _free_port(), "userdata": str(pathlib.Path(tmp_path, "profile")),
            "url": url, "w": w, "h": h, "settle_ms": settle_ms, "steps": steps}
    spec_path = pathlib.Path(tmp_path, "spec.json")
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run([NODE, DRIVER, str(spec_path)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)
    lines = [ln for ln in (proc.stdout or "").strip().splitlines() if ln.strip()]
    assert lines, "драйвер Chrome ничего не вывел: " + (proc.stderr or "")[:800]
    res = json.loads(lines[-1])
    assert "error" not in res, "стенд Chrome: " + res["error"]
    return res["results"]
