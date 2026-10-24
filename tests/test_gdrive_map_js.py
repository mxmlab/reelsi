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


# --------------------------------------------------------------------------- #
# Окно «Куда что качать» и итог — настоящие функции 40-queue.js в node.
# DOM, fetch и модалка подставные: проверяем строки, раскладку на отправку,
# отмену и итог, а не разметку (разметку окна держат test_gdrive_map_layout и
# test_ui_podcast).
# --------------------------------------------------------------------------- #
CORE_JS = os.path.join(ROOT, "static", "app", "00-core.js")
EN_JSON = os.path.join(ROOT, "static", "i18n", "en.json")

_STAND_HEAD = r"""
let N = 2, CAMDIRS = ['D:\\cam1', 'D:\\cam2'];
let GDN = 0, GDLOGN = 0, GDLAST = null, GDMAP = null;
let BOX_ON = false, MO_CB = null, FETCH_ANSWER = null;
const OPENED = [], CALLS = [];
const ELS = {};
function mkEl(id) {
  return { id, value: '', textContent: '', innerHTML: '', disabled: false, className: '',
    onclick: null, onchange: null, kids: [],
    classList: { contains: () => BOX_ON },
    appendChild(c) { this.kids.push(c); },
    querySelector() { return this._dl || (this._dl = { textContent: '' }); } };
}
function $(id) { return ELS[id] || (ELS[id] = mkEl(id)); }
const document = { createElement: () => mkEl('row'), addEventListener() {} };
class MutationObserver {
  constructor(cb) { this.cb = cb; }
  observe() { MO_CB = this.cb; }
  disconnect() { if (MO_CB === this.cb) MO_CB = null; }
}
function nCams() { return N; }
function t(s, v) { return v ? s.replace(/\{(\w+)\}/g, (m, k) => (k in v ? String(v[k]) : m)) : s; }
function esc(s) { return String(s); }
function val(id) { return $(id).value; }
function openModal(id) { BOX_ON = true; OPENED.push(id); }
function closeModal(id) { BOX_ON = false; OPENED.push('close:' + id); if (MO_CB) MO_CB(); }
function toast() {}
function uiLog() {}
function errText(d) { return d.error; }
function pickdir() { return Promise.resolve(); }
function gdPoll() {}
function fetch(url, opts) {
  CALLS.push({ url, body: opts && opts.body ? JSON.parse(opts.body) : null });
  const data = url.indexOf('/api/gdrive_list') >= 0 ? FETCH_ANSWER : { ok: true };
  return Promise.resolve({ json: () => Promise.resolve(data) });
}
"""


def _window_stand(tmp_path, body):
    src40 = open(QUEUE_JS, encoding="utf-8").read()
    src00 = open(CORE_JS, encoding="utf-8").read()
    fns = [_function_source(src00, "plur")] + [
        _function_source(src40, n) for n in (
            "gdSize", "gdMapDefaults", "gdCountLine", "gdDlPath", "gdMapAsk",
            "gdMapRelabel", "gdSummary", "gdDownload", "gdStart")]
    script = (_STAND_HEAD + "\n".join(fns) +
              "\n(async () => {\nconst out = {};\n"
              "const sleep = () => new Promise(r => setImmediate(r));\n" + body +
              "\nprocess.stdout.write(JSON.stringify(out));\n})();\n")
    return _run_node(script, tmp_path)


@node
def test_окно_строки_умолчания_и_раскладка_на_отправку(tmp_path):
    got = _window_stand(tmp_path, """
$('gdrive_dest').value = 'D:\\\\dl';
const info = {kind: 'folder',
  dirs: [{name: 'Камера 1', files: 3, bytes: 3e9}, {name: 'Камера 2', files: 2, bytes: 1e9}],
  groups: [{prefix: 'C', files: ['C1653.MP4', 'c1654.mp4'], bytes: 150},
           {prefix: '', files: ['001.mov'], bytes: 7}]};
ELS.gdMapRows = mkEl('gdMapRows');
const p1 = gdMapAsk(info);
out.rowsHTML = ELS.gdMapRows.kids.map(r => r.innerHTML);
out.defaults = [0, 1, 2, 3].map(i => $('gdMapSel' + i).value);
$('gdMapSel2').value = 'cam:1';                       // группа C — в папку камеры 2
$('gdrive_dest').value = 'E:\\\\new';
gdMapRelabel();
out.relabel = $('gdMapSel0')._dl.textContent;
$('gdrive_dest').value = 'D:\\\\dl';                  // вернули папку загрузки до «Скачать»
$('gdMapOk').onclick();
out.layout = await p1;
const p2 = gdMapAsk({kind: 'folder', dirs: [{name: 'A', files: 1, bytes: 1}],
  groups: [{prefix: 'C', files: ['C1.mp4'], bytes: 1}, {prefix: 'IMG_', files: ['IMG_2.mov'], bytes: 1}]});
out.defaults2 = [0, 1, 2].map(i => $('gdMapSel' + i).value);
$('gdMapCancel').onclick();
out.cancelled = (await p2) === null;
""")
    assert got["defaults"] == ["cam:0", "cam:1", "dl", "dl"]          # подпапки по номеру, остальное — в папку загрузки
    assert got["defaults2"] == ["cam:0", "cam:1", "dl"]              # подпапка и группа — на свободные камеры, нехватка — загрузка
    assert got["cancelled"] is True
    assert got["rowsHTML"][0].count("Камера 1") >= 1
    assert "C… — 2 файла · 0.0 МБ" in got["rowsHTML"][2]              # группа: приставка, число, размер
    assert "C1653.MP4, c1654.mp4" in got["rowsHTML"][2]                # под ней первые имена
    assert "Прочие файлы — 1 файл · 0.0 МБ" in got["rowsHTML"][3]     # пустая приставка
    assert got["relabel"] == "В папку загрузки (E:\\new\\Камера 1)"    # смена папки переписывает подписи
    lay = got["layout"]
    assert lay["file"] is False and lay["dest"] == "D:\\dl"
    assert lay["map"] == [
        {"dir": "Камера 1", "dest": "D:\\cam1"},
        {"dir": "Камера 2", "dest": "D:\\cam2"},
        {"files": ["C1653.MP4", "c1654.mp4"], "prefix": "C", "dest": "D:\\cam2"},
        {"files": ["001.mov"], "prefix": "", "dest": "D:\\dl"},
    ]
    assert lay["rows"][2] == {"where": "cam:1", "files": 2, "bytes": 150}


@node
def test_окно_открывается_для_плоской_папки_и_для_файла(tmp_path):
    got = _window_stand(tmp_path, """
$('gdrive_dest').value = 'D:\\\\dl';
const FOLDER = 'https://drive.google.com/drive/folders/1XyfxxxxxxxxxxxxxxxxxxxxxxxxxKHCh';
const FILE = 'https://drive.google.com/file/d/1AbC_dEfGhI1234567890/view?usp=sharing';
// плоская папка — без подпапок, одна группа
$('gdrive_url').value = FOLDER;
FETCH_ANSWER = {kind: 'folder', dirs: [], groups: [{prefix: 'IMG_', files: ['IMG_1.mov'], bytes: 5}]};
const d1 = gdDownload();
await sleep(); await sleep();
out.flatOpened = OPENED.includes('mbGdMap');
$('gdMapOk').onclick();
await d1;
out.flatBody = CALLS.filter(c => c.url.includes('gdrive_download')).map(c => c.body);
// ссылка на файл — одна строка, без «Не качать», без map
OPENED.length = 0; CALLS.length = 0;
ELS.gdMapRows.kids = [];                              // заглушка не чистит строки при innerHTML = ''
$('gdrive_url').value = FILE;
FETCH_ANSWER = {kind: 'file'};
const d2 = gdDownload();
await sleep(); await sleep();
out.fileOpened = OPENED.includes('mbGdMap');
out.fileDefault = $('gdMapSel0').value;
out.fileHasNone = $('gdMapRows') && ELS.gdMapRows.kids.length === 1 &&
  !ELS.gdMapRows.kids[0].innerHTML.includes('value="none"');
$('gdMapSel0').value = 'dl';
$('gdMapOk').onclick();
await d2;
out.fileBody = CALLS.filter(c => c.url.includes('gdrive_download')).map(c => c.body);
out.fileSummary = gdSummary();
// отмена окна — статус «скачивание отменено», на сервер ничего не уходит
OPENED.length = 0; CALLS.length = 0;
$('gdrive_url').value = FOLDER;
FETCH_ANSWER = {kind: 'folder', dirs: [{name: 'Камера 1', files: 1, bytes: 1}], groups: []};
const d3 = gdDownload();
await sleep(); await sleep();
$('gdMapCancel').onclick();
await d3;
out.cancelStatus = $('gdrive_status').textContent;
out.cancelPosts = CALLS.filter(c => c.url.includes('gdrive_download')).length;
// пустая ссылка — окна нет, в статусе сказано почему
OPENED.length = 0;
FETCH_ANSWER = {kind: 'folder', dirs: [], groups: []};
await gdDownload();
// (ссылка та же — FOLDER)
out.emptyStatus = $('gdrive_status').textContent;
out.emptyOpened = OPENED.includes('mbGdMap');
""")
    assert got["flatOpened"] is True
    assert got["flatBody"] == [{
        "url": "https://drive.google.com/drive/folders/1XyfxxxxxxxxxxxxxxxxxxxxxxxxxKHCh",
        "dest": "D:\\dl",
        "map": [{"files": ["IMG_1.mov"], "prefix": "IMG_", "dest": "D:\\cam1"}]}]
    assert got["fileOpened"] is True and got["fileDefault"] == "cam:0"
    assert got["fileHasNone"] is True
    assert got["fileBody"] == [{"url": "https://drive.google.com/file/d/1AbC_dEfGhI1234567890/view?usp=sharing",
                                "dest": "D:\\dl"}]
    assert got["fileSummary"] == "файл скачан: папка загрузки"
    assert got["cancelStatus"] == "скачивание отменено"
    assert got["cancelPosts"] == 0
    assert got["emptyStatus"] == "в ссылке нет файлов" and got["emptyOpened"] is False


@node
def test_итог_после_скачивания_по_раскладке(tmp_path):
    got = _window_stand(tmp_path, """
GDLAST = {file: false, rows: [{where: 'cam:1', files: 3, bytes: 1.5 * 2 ** 30},
  {where: 'cam:0', files: 4, bytes: 2.1 * 2 ** 30}, {where: 'dl', files: 1, bytes: 0},
  {where: 'none', files: 9, bytes: 9}]};
out.summary = gdSummary();
GDLAST = {file: true, rows: [{where: 'cam:0', files: 1, bytes: 5}]};
out.fileSummary = gdSummary();
GDLAST = null;
out.noSummary = gdSummary();
""")
    assert got["summary"] == ("Камера 1 — 4 файла · 2.10 ГБ; Камера 2 — 3 файла · 1.50 ГБ; "
                              "папка загрузки — 1 файл · 0.0 МБ")
    assert got["fileSummary"] == "файл скачан: Камера 1"
    assert got["noSummary"] == ""                  # после F5 раскладки в памяти нет — итога нет


# Число файлов склоняется ФРАЗОЙ целиком («{n} файл», «{n} файла», «{n} файлов»): английский
# словарь переводит фразу, а не слово «файла» отдельно (в нём «файла» — «file» из других мест).
_T_STAND = r"""
const I18N = __EN__;
let GDLAST = null;
function t(s, vars) {
  let out = (s && I18N[s]) || s;
  if (vars) for (const k in vars) out = out.split('{' + k + '}').join(String(vars[k]));
  return out;
}
function nCams() { return 2; }
"""


def _count_stand(tmp_path, en, body):
    src00 = open(CORE_JS, encoding="utf-8").read()
    src40 = open(QUEUE_JS, encoding="utf-8").read()
    head = _T_STAND.replace("__EN__", json.dumps(en, ensure_ascii=False))
    fns = [_function_source(src00, "plur")] + [
        _function_source(src40, n) for n in ("gdSize", "gdCountLine", "gdSummary")]
    script = head + "\n".join(fns) + "\n(() => {\nconst out = {};\n" + body + \
        "\nprocess.stdout.write(JSON.stringify(out));\n})();\n"
    return _run_node(script, tmp_path)


_COUNT_BODY = """
for (const n of [1, 2, 3, 5, 11, 22]) out[n] = gdCountLine(n, 0).split(' · ')[0];
GDLAST = {file: false, rows: [{where: 'cam:0', files: 1, bytes: 0},
  {where: 'cam:1', files: 3, bytes: 0}, {where: 'dl', files: 5, bytes: 0}]};
out.summary = gdSummary();
"""


@node
def test_число_файлов_склоняется_фразой_по_английски(tmp_path):
    en = json.load(open(EN_JSON, encoding="utf-8"))
    got = _count_stand(tmp_path, en, _COUNT_BODY)
    assert got["1"] == "1 file" and got["3"] == "3 files" and got["5"] == "5 files"
    assert got["2"] == "2 files" and got["11"] == "11 files" and got["22"] == "22 files"
    assert got["summary"] == ("Camera 1 — 1 file · 0.0 MB; Camera 2 — 3 files · 0.0 MB; "
                              "download folder — 5 files · 0.0 MB")


@node
def test_число_файлов_склоняется_фразой_по_русски(tmp_path):
    got = _count_stand(tmp_path, {}, _COUNT_BODY)
    assert got["1"] == "1 файл" and got["2"] == "2 файла" and got["3"] == "3 файла"
    assert got["5"] == "5 файлов" and got["11"] == "11 файлов" and got["22"] == "22 файла"
    assert got["summary"] == ("Камера 1 — 1 файл · 0.0 МБ; Камера 2 — 3 файла · 0.0 МБ; "
                              "папка загрузки — 5 файлов · 0.0 МБ")
