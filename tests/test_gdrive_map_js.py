# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Фронт гугл-диска: выбор по умолчанию в окне раскладки и перечитка камер после скачивания.

Из static/app/40-queue.js вырезаются две функции — gdMapDefaults (выбор папки по умолчанию)
и camsRefreshKeep (перечитка камер с сохранением выбора) — и гоняются в node с подставными
$, nCams, loadCams. Само окно держится на DOM и промисе закрытия: без браузера его не
проверить, поэтому здесь только логика выбора и сохранение селектов.

Запуск: py -3.10 -m pytest tests/test_gdrive_map_js.py -q
"""
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import pytest  # noqa: E402

QUEUE_JS = os.path.join(ROOT, "static", "app", "40-queue.js")
node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")


def _function_source(src, name):
    """Текст функции `name` из исходника: от объявления до парной закрывающей скобки.

    Скобки в телах этих двух функций только кодовые (ни одной в строках и регулярках),
    поэтому простой подсчёт глубины точен.
    """
    m = re.search(r"^(?:async\s+)?function\s+" + re.escape(name) + r"\s*\(", src, re.M)
    assert m, f"в 40-queue.js нет функции {name}"
    depth = 0
    i = src.index("{", m.end())
    while True:
        ch = src[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():i + 1]
        i += 1


def _run_node(script, tmp_path):
    path = tmp_path / "stand.js"
    path.write_text(script, encoding="utf-8")
    res = subprocess.run(["node", str(path)], capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


@node
def test_выбор_по_умолчанию_в_окне_раскладки(tmp_path):
    """Номер камеры в имени — в свою камеру; без номера — по порядку на свободные камеры;
    не хватило камер — -1 («В папку загрузки»). Камера без папки в выбор не попадает."""
    fn = _function_source(open(QUEUE_JS, encoding="utf-8").read(), "gdMapDefaults")
    cases = [
        {"names": ["Камера 1", "Камера 2"], "cams": [0, 1], "want": [0, 1]},
        {"names": ["A", "B"], "cams": [0, 1], "want": [0, 1]},
        {"names": ["A", "B", "C"], "cams": [0, 1], "want": [0, 1, -1]},
        {"names": ["1", "2", "3"], "cams": [0, 1], "want": [0, 1, -1]},
        {"names": ["камера 2", "CAM 1"], "cams": [0, 1], "want": [1, 0]},
        {"names": ["Камера 2", "Камера 1"], "cams": [1], "want": [1, -1]},
        {"names": ["Камера 10", "A"], "cams": [0, 1], "want": [0, 1]},
        {"names": ["IMG 2024", "Камера 2"], "cams": [0, 1], "want": [0, 1]},
    ]
    script = (fn + "\nconst cases = " + json.dumps(cases, ensure_ascii=False) + ";\n"
              "process.stdout.write(JSON.stringify(cases.map(c => gdMapDefaults(c.names, c.cams))));\n")
    got = _run_node(script, tmp_path)
    assert got == [c["want"] for c in cases]


@node
def test_камеры_после_скачивания_сохраняют_выбор(tmp_path):
    """camsRefreshKeep перечитывает камеры (loadCams пересобирает селекты) и возвращает
    выбранное там, где оно ещё есть. Пропавшее из списка не восстанавливается."""
    fn = _function_source(open(QUEUE_JS, encoding="utf-8").read(), "camsRefreshKeep")
    script = """
const STORE = {};
function mkSel(opts) {
  const s = { options: [], _v: '' };
  s.fill = (arr) => { s.options = arr.map(v => ({ value: v })); s._v = arr[0] || ''; };
  Object.defineProperty(s, 'value', {
    get() { return this._v; },
    set(v) { this._v = s.options.some(o => o.value === v) ? v : ''; },
  });
  s.fill(opts);
  return s;
}
function $(id) { return STORE[id] || null; }
function nCams() { return 2; }
// loadCams как после скачивания: buildCamRows пересобирает селекты с первого пункта
async function loadCams() {
  STORE.sel0.fill(['A', 'B', 'C']);
  STORE.sel1.fill(['X']);
}
""" + fn + """
(async () => {
  STORE.sel0 = mkSel(['A', 'B']); STORE.sel0.value = 'B';
  STORE.sel1 = mkSel(['X', 'Y']); STORE.sel1.value = 'Y';
  await camsRefreshKeep();
  process.stdout.write(JSON.stringify({ sel0: STORE.sel0.value, sel1: STORE.sel1.value }));
})();
"""
    got = _run_node(script, tmp_path)
    assert got == {"sel0": "B", "sel1": "X"}
