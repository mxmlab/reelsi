# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание FM — порядок слоёв как перетаскиваемый список в стиле.

Проверяет:
1. Дефолтный layer_order: ["subs", "video", "roto", "photo", "intro"].
2. Модель стека: итоговый порядок слоёв в собранном .jsx совпадает со старым эталоном:
   субтитры -> переходы -> видео -> рото -> фото -> интро -> камеры.
3. Миграция старых ключей insert_above_subs=true и insert_video_front=false,
   включая проверку итогового положения слоёв в собранном .jsx.
4. План сцены несёт layer_order из стиля.
5. Сторож ключей стиля зелёный, старых ключей в BASE нет.
"""
import gzip
import json
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import styles  # noqa: E402
from core import xml2ae  # noqa: E402
from core.xml2ae.build import scene_plan  # noqa: E402


@pytest.fixture()
def xml_nosubs(tmp_path):
    dst = str(tmp_path / "timeline_nosubs.xml")
    shutil.copy(os.path.join(HERE, "fixtures", "timeline_nosubs.xml"), dst)
    return dst


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


@pytest.fixture(autouse=True)
def _isolate_censor(monkeypatch):
    from core import censor
    monkeypatch.setattr(censor, "USER_PATHS", {"bad": "", "ok": ""})
    monkeypatch.setattr(censor, "_cache", {"bad": (None, None, censor.DEFAULT_BAD),
                                           "ok": (None, None, censor.DEFAULT_OK)})


@pytest.fixture(autouse=True)
def _mock_roto_in_build(monkeypatch):
    """В тестах мокаем GPU-рото, чтобы генерировались реальные roto-слои в .jsx."""
    mock_data = json.dumps([
        {"ci": 0, "ts": 0.0, "te": 2.0, "cs": 0.0, "scale": 100, "mf": 1, "mask": "C:/x/mask.mp4"}
    ])
    monkeypatch.setattr(xml2ae.build, "_roto_js", lambda *a, **k: mock_data)


@pytest.fixture(autouse=True)
def _mock_assets_resolver(monkeypatch):
    """Мокаем resolver ассетов: изолируем тесты JSX от наличия личных файлов в ../assets."""
    from core import assets
    monkeypatch.setattr(assets, "resolver", lambda base: lambda role: "C:/mock/transition.mov" if role == "transition" else "")


def _ins_photo(t0, media="C:/x/a.png", style="cam2"):
    return {"type": "photo", "style": style, "media": media, "start_s": t0, "dur_s": 2.0}


def _ins_video(t0, media="C:/x/v.mp4"):
    return {"type": "video", "media": media, "start_s": t0, "dur_s": 3.0}


def _build(xml, out, style=None, inserts=None, roto=True, intro=None):
    return xml2ae.to_ae_full(xml, out, style=style or {},
                             inserts=inserts if inserts is not None else [],
                             roto=roto, intro=intro, intro_riser=False,
                             disclaimer="",
                             emit=lambda *a, **k: None)


def simulate_jsx_stack(jsx_path):
    """Исполняет .jsx в среде Node.js с эмуляцией ExtendScript After Effects.

    Возвращает список слоёв главной композиции сверху вниз:
    [{"name": ..., "category": ...}, ...]
    где category: 'subs' | 'transitions' | 'video' | 'roto' | 'photo' | 'intro' | 'cameras' | 'null' | 'audio' | 'disclaimer'
    """
    js_runner = r'''
const fs = require('fs');
let comps = [];
let mainComp = null;
function mockProp() {
  const p = {
    value: { resetCharStyle: ()=>{}, resetParagraphStyle: ()=>{} },
    numKeys: 0,
    setValue: ()=>{},
    setValueAtTime: ()=>{},
    addProperty: ()=>mockProp(),
    property: ()=>mockProp(),
    setInterpolationTypeAtKey: ()=>{}
  };
  return p;
}
class MockLayer {
  constructor(name, type, comp) {
    this.name = name || 'Layer';
    this.type = type || 'layer';
    this._comp = comp;
  }
  property() { return mockProp(); }
  remove() {
    const s = this._comp._stack;
    const idx = s.indexOf(this);
    if (idx >= 0) s.splice(idx, 1);
  }
  moveToBeginning() {
    const s = this._comp._stack;
    const idx = s.indexOf(this);
    if (idx >= 0) s.splice(idx, 1);
    s.unshift(this);
  }
  moveBefore(other) {
    const s = this._comp._stack;
    const idx = s.indexOf(this);
    if (idx >= 0) s.splice(idx, 1);
    const oidx = s.indexOf(other);
    if (oidx >= 0) s.splice(oidx, 0, this);
    else s.unshift(this);
  }
  moveAfter(other) {
    const s = this._comp._stack;
    const idx = s.indexOf(this);
    if (idx >= 0) s.splice(idx, 1);
    const oidx = s.indexOf(other);
    if (oidx >= 0) s.splice(oidx + 1, 0, this);
    else s.push(this);
  }
  setTrackMatte() {}
}
class MockComp {
  constructor(name) {
    this.name = name;
    this._stack = [];
    comps.push(this);
    const self = this;
    this.layers = {
      add: (item) => { const l = new MockLayer((item&&item.name)||'layer', 'layer', self); self._stack.unshift(l); return l; },
      addNull: () => { const l = new MockLayer('Null', 'null', self); l.nullLayer = true; self._stack.unshift(l); return l; },
      addShape: () => { const l = new MockLayer('Shape', 'shape', self); self._stack.unshift(l); return l; },
      addText: (txt) => { const l = new MockLayer('Text: ' + txt, 'text', self); self._stack.unshift(l); return l; },
      get length() { return self._stack.length; }
    };
  }
  layer(idx) { return this._stack[idx - 1]; }
  openInViewer() { mainComp = this; }
}
const app = {
  project: {
    items: {
      addComp: (n) => new MockComp(n),
      addFolder: () => ({})
    },
    importFile: (io) => ({ name: io.file ? io.file.name : 'imported', width: 1080, height: 1920, duration: 10 })
  },
  beginUndoGroup: () => {},
  endUndoGroup: () => {}
};
const File = function(p) { this.fsName = p; this.name = (p||'').split(/[\\\/]/).pop(); this.exists = true; };
const ImportOptions = function(f) { this.file = f; };
const TrackMatteType = { LUMA: 1 };
const BlendingMode = { ADD: 1 };
const KeyframeInterpolationType = { BEZIER: 1 };
const Shape = function() {};
const alert = () => {};

let jsx = fs.readFileSync(process.argv[1], 'utf8');
if (jsx.charCodeAt(0) === 0xFEFF) jsx = jsx.slice(1);
eval(jsx);
if (!mainComp) { console.log(JSON.stringify([])); process.exit(0); }

function cat(l) {
  if (l.nullLayer) return 'null';
  const n = l.name;
  if (n.startsWith('Субтитры')) return 'subs';
  if (n === 'Переход' || n.startsWith('Переход')) return 'transitions';
  if (n === 'Whoosh' || n === 'Музыка' || n.includes('SFX')) return 'audio';
  if (n.startsWith('Вставка: ') && (/\.(mp4|mov|avi|mkv|webm)$/i.test(n))) return 'video';
  if (n.startsWith('Рото')) return 'roto';
  if (n.startsWith('Вставка: ')) return 'photo';
  if (n.toLowerCase().includes('интро') || n.startsWith('Строка ') || n.startsWith('INT_')) return 'intro';
  if (n.startsWith('Дисклеймер') || n.startsWith('Text: МАТЕРИАЛ')) return 'disclaimer';
  return 'cameras';
}
const res = mainComp._stack.map((l, i) => ({ i: i+1, name: l.name, category: cat(l) }));
console.log(JSON.stringify(res));
'''
    res = subprocess.run(['node', '-e', js_runner, str(jsx_path)], capture_output=True, text=True, encoding='utf-8-sig', timeout=30)
    assert res.returncode == 0, f"Ошибка симуляции .jsx: {res.stderr}"
    return json.loads(res.stdout)


def _collapsed_categories(stack):
    """Схлопывает последовательные одинаковые категории в один список (без служебных null, disclaimer, audio)."""
    cats = []
    for item in stack:
        c = item["category"]
        if c in ("null", "disclaimer", "audio"):
            continue
        if not cats or cats[-1] != c:
            cats.append(c)
    return cats


def test_style_default_layer_order():
    """Дефолтный layer_order содержит 5 элементов в правильном базовом порядке."""
    expected = ["subs", "video", "roto", "photo", "intro"]
    assert styles.BASE.get("layer_order") == expected
    assert styles.resolve(None).get("layer_order") == expected
    assert styles.DEFAULT_LAYER_ORDER == expected
    # Старых ключей в BASE быть не должно
    assert "insert_above_subs" not in styles.BASE
    assert "insert_video_front" not in styles.BASE


def test_migration_insert_above_subs(xml_subs, tmp_path):
    """Стиль со старым insert_above_subs=True мигрирует в photo над subs и в .jsx."""
    data = {"insert_above_subs": True}
    migrated, changed = styles.migrate_style_dict(data)
    assert changed is True
    assert "insert_above_subs" not in migrated
    order = migrated["layer_order"]
    assert order.index("photo") < order.index("subs")
    assert set(order) == {"subs", "intro", "video", "roto", "photo"}
    assert len(order) == 5

    # Проверка собранного .jsx: photo реально выше subs
    ins = [_ins_photo(1.0, "C:/x/a.png"), _ins_video(4.0)]
    out = str(tmp_path / "migrated_above.jsx")
    _build(xml_subs, out, style=data, inserts=ins, roto=True)
    stack = simulate_jsx_stack(out)
    cats = _collapsed_categories(stack)
    assert cats.index("photo") < cats.index("subs"), f"Фото должно быть выше субтитров: {cats}"


def test_migration_insert_video_front_false(xml_subs, tmp_path):
    """Стиль со старым insert_video_front=False мигрирует в video под roto и в .jsx."""
    data = {"insert_video_front": False}
    migrated, changed = styles.migrate_style_dict(data)
    assert changed is True
    assert "insert_video_front" not in migrated
    order = migrated["layer_order"]
    assert order.index("roto") < order.index("video")
    assert set(order) == {"subs", "intro", "video", "roto", "photo"}
    assert len(order) == 5

    # Проверка собранного .jsx: video реально ниже roto
    ins = [_ins_photo(1.0, "C:/x/a.png"), _ins_video(4.0)]
    out = str(tmp_path / "migrated_video_back.jsx")
    _build(xml_subs, out, style=data, inserts=ins, roto=True)
    stack = simulate_jsx_stack(out)
    cats = _collapsed_categories(stack)
    assert cats.index("roto") < cats.index("video"), f"Видео должно быть ниже рото: {cats}"


def test_migration_both_old_keys():
    """Миграция стиля с обоими старыми ключами."""
    data = {"insert_above_subs": True, "insert_video_front": False}
    migrated, changed = styles.migrate_style_dict(data)
    assert changed is True
    assert "insert_above_subs" not in migrated
    assert "insert_video_front" not in migrated
    order = migrated["layer_order"]
    assert order.index("photo") < order.index("subs")
    assert order.index("roto") < order.index("video")


def test_stack_model_default_order(xml_subs, tmp_path):
    """Модель стека: итоговый порядок слоёв сверху вниз совпадает с эталоном.

    Эталонный порядок:
    субтитры -> переходы -> видео -> рото -> фото -> интро -> камеры
    """
    ins = [_ins_photo(1.0, "C:/x/a.png"), _ins_video(4.0, "C:/x/v.mp4")]
    intro = [{"words": ["ТЕСТ", "ИНТРО"], "times": [0.1, 0.4]}]
    out = str(tmp_path / "default_stack.jsx")
    _build(xml_subs, out, style={}, inserts=ins, intro=intro, roto=True)
    stack = simulate_jsx_stack(out)
    cats = _collapsed_categories(stack)

    expected_full = ["subs", "transitions", "video", "roto", "photo", "intro", "cameras"]
    assert cats == expected_full, f"Фактический стек {cats} не совпадает с эталоном {expected_full}"

    # 6 основных позиций (без служебных переходов)
    six_positions = [c for c in cats if c != "transitions"]
    assert six_positions == ["subs", "video", "roto", "photo", "intro", "cameras"]


def test_stack_model_fails_on_wrong_order(xml_subs, tmp_path):
    """Тест падает, если дефолтный порядок слоёв переставлен."""
    ins = [_ins_photo(1.0, "C:/x/a.png"), _ins_video(4.0, "C:/x/v.mp4")]
    intro = [{"words": ["ТЕСТ", "ИНТРО"], "times": [0.1, 0.4]}]
    out = str(tmp_path / "wrong_stack.jsx")
    # Переставим порядок: интро на самый верх
    wrong_order = ["intro", "subs", "video", "roto", "photo"]
    _build(xml_subs, out, style={"layer_order": wrong_order}, inserts=ins, intro=intro, roto=True)
    stack = simulate_jsx_stack(out)
    cats = _collapsed_categories(stack)
    expected_default = ["subs", "transitions", "video", "roto", "photo", "intro", "cameras"]
    assert cats != expected_default, "Кастомный порядок не должен совпадать с дефолтным"
    assert cats[0] == "intro", "Интро должно быть самым верхним в кастомном стеке"


def test_custom_layer_order_in_jsx(xml_subs, tmp_path):
    """Кастомный порядок слоёв отражается в фактическом порядке в .jsx."""
    ins = [_ins_photo(1.0, "C:/x/a.png"), _ins_video(4.0, "C:/x/v.mp4")]
    intro = [{"words": ["ТЕСТ", "ИНТРО"], "times": [0.1, 0.4]}]
    out = str(tmp_path / "custom.jsx")
    custom_order = ["photo", "subs", "roto", "video", "intro"]
    _build(xml_subs, out, style={"layer_order": custom_order}, inserts=ins, intro=intro, roto=True)
    txt = open(out, encoding="utf-8-sig").read()

    # В JSX передан массив LAYER_ORDER
    assert 'var LAYER_ORDER = ["photo","subs","roto","video","intro"];' in txt
    assert "var layerGroups = {" in txt
    assert '"subs": subLayers,' in txt
    assert '"photo": photoLayers,' in txt
    assert '"video": videoLayers,' in txt
    assert '"roto": rotoLayers' in txt

    # Симуляция стека подтверждает порядок
    stack = simulate_jsx_stack(out)
    cats = _collapsed_categories(stack)
    # photo выше subs, subs выше roto, roto выше video, video выше intro
    assert cats.index("photo") < cats.index("subs")
    assert cats.index("subs") < cats.index("roto")
    assert cats.index("roto") < cats.index("video")
    assert cats.index("video") < cats.index("intro")


def test_scene_plan_layer_order(xml_nosubs):
    """План сцены несёт layer_order из стиля."""
    custom_order = ["video", "photo", "subs", "intro", "roto"]
    p = scene_plan(xml_nosubs, inserts=[_ins_photo(1.0)], style={"layer_order": custom_order}, roto=False)
    assert p["layer_order"] == custom_order

    p_def = scene_plan(xml_nosubs, inserts=[_ins_photo(1.0)], style={}, roto=False)
    assert p_def["layer_order"] == ["subs", "video", "roto", "photo", "intro"]

