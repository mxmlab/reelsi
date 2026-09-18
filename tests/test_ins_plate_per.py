# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Подложка — галка У ВСТАВКИ, а не у всего стиля; свои промпты подложки (задание ZK).

После ZI подложка и «без фона» включались галками стиля и работали на ВСЕ фото-вставки.
Здесь стережётся обратное: по умолчанию вставка как раньше (карточка, маска, обычный
промпт), а у отдельной вставки галка «на подложке» (поле plate) — тогда у неё подложка из
стиля (insert_plate_file), снятый фон (insertlib.nobg_path) и СВОЙ промпт (слоты pa/pb).
Ключей стиля insert_plate/insert_nobg больше нет.

Golden — тот же, что у геометрии: .jsx без единой вставки с plate обязан остаться
ПРЕЖНИМ побайтово, иначе подложка снова «на весь стиль».

Запуск: python -m pytest tests/test_ins_plate_per.py -q
"""
import gzip
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
sys.path.insert(0, HERE)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

from core import aicut, insertlib, style_schema, styles, xml2ae  # noqa: E402
from tests.test_geometry_python import _mask_assets  # noqa: E402

GOLDEN = os.path.join(HERE, "fixtures", "golden_geometry.jsx")
EN = os.path.join(ROOT, "static", "i18n", "en.json")
SETTINGS_JS = os.path.join(ROOT, "static", "app", "10-settings.js")

# Тот же набор, что у геометрии (test_geometry_python.INS): фото на кам1, фото на
# перебивке (cam2-ветка) и фото без выхода. Пути несуществующие — golden машино-независим.
INS = [
    {"type": "photo", "style": "cam2", "media": "C:/x/a.png", "start_s": 1, "dur_s": 2},
    {"type": "photo", "style": "cam2", "media": "C:/x/cam2.png", "start_s": 8.0, "dur_s": 1.5},
    {"type": "photo", "style": "cam1", "media": "C:/x/b.png", "start_s": 5, "dur_s": 3},
]

node = pytest.mark.skipif(not shutil.which("node"), reason="контракт фронта требует node в PATH")


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


@pytest.fixture(autouse=True)
def _isolate_censor(monkeypatch):
    """Детерминизм сборки: цензура читает поставочные списки, а не личные словари."""
    from core import censor
    monkeypatch.setattr(censor, "USER_PATHS", {"bad": "", "ok": ""})
    monkeypatch.setattr(censor, "_cache", {"bad": (None, None, censor.DEFAULT_BAD),
                                           "ok": (None, None, censor.DEFAULT_OK)})


@pytest.fixture(autouse=True)
def _no_rembg(monkeypatch):
    """rembg в тестах не зовём: onnx-модель на CPU тут ни к чему, а без пакета remove_bg
    падает SystemExit и роняет сборку целиком. Кому важно СНЯТИЕ фона — подменяет сам."""
    monkeypatch.setattr(insertlib, "remove_bg", lambda data, trim=True, emit=None: data)


@pytest.fixture()
def client():
    """Flask-клиент с блюпринтом api: /api/ai_genimage проверяется без сети."""
    import api
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def _png(path, w, h, color=(180, 40, 40, 255)):
    from PIL import Image
    Image.new("RGBA", (int(w), int(h)), color).save(str(path))
    return str(path)


def _png_bytes(w, h):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", (int(w), int(h)), (10, 200, 40, 255)).save(buf, "PNG")
    return buf.getvalue()


def _build(xml, tmp_path, style=None, inserts=None):
    st = dict(style or {})
    st["intro_riser"] = False                 # ризер тянет путь ассета с этой машины
    path, _, _ = xml2ae.to_ae_full(
        xml, jsx_path=str(tmp_path / "out.jsx"),
        inserts=[dict(x) for x in (inserts if inserts is not None else INS)],
        style=st, disclaimer="", intro_riser=False, emit=lambda *a, **k: None)
    return open(path, encoding="utf-8-sig").read()


def _ins_jsons(jsx):
    return json.loads(re.search(r"var INSERTS=(\[.*?\]);", jsx).group(1))


def _func(src, name):
    """Вырезать `[async] function name(...){...}` целиком по балансу скобок."""
    m = re.search(r"(?:async\s+)?function\s+%s\s*\(" % re.escape(name), src)
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


def _schema_fields():
    """Все поля схемы стиля по ключу."""
    found = {}

    def walk(items):
        for it in items:
            if it.get("type") == "field":
                found[it.get("key")] = it
            walk(it.get("items", []))

    for layer in style_schema.LAYERS:
        walk(layer.get("items", []))
    return found


def test_plate_only_for_the_checked_insert(xml_subs, tmp_path, monkeypatch):
    """1. Две фото-вставки, галка у одной: подложка, ps/px/py и снятый фон — только у неё,
    маска — только у второй."""
    plate = _png(tmp_path / "plate.png", 2048, 2048)
    on_plate = _png(tmp_path / "on.png", 800, 600)
    plain = _png(tmp_path / "off.png", 800, 600)
    calls = []

    def fake_remove(data, trim=True, emit=None):
        calls.append(1)
        return _png_bytes(800, 600)

    monkeypatch.setattr(insertlib, "remove_bg", fake_remove)
    ins = [{"type": "photo", "style": "cam2", "media": on_plate,
            "start_s": 1, "dur_s": 2, "plate": True},
           {"type": "photo", "style": "cam2", "media": plain, "start_s": 8.0, "dur_s": 1.5}]
    jsx = _build(xml_subs, tmp_path, style={"insert_plate_file": plate}, inserts=ins)
    on, off = _ins_jsons(jsx)

    assert on.get("plate") is True, "у вставки с галкой нет признака подложки"
    assert "plate" not in off, "признак подложки достался вставке без галки"
    for k in ("ps", "px", "py"):
        assert k in on, f"геометрии подложки нет у вставки с галкой: {k}"
        assert k not in off, f"геометрия подложки досталась вставке без галки: {k}"
    assert on["card"]["plate"] == plate, "плашка не уехала в карточку предпросмотра"
    assert "plate" not in off["card"] and "pw" in off["card"], \
        "у вставки без галки пропала обычная карточка (маска)"
    # «без фона» — только у той, что на подложке
    assert on["media"].endswith("on.nobg.png"), f"media подложки: {on['media']!r}"
    assert off["media"] == plain, f"у обычной вставки подменили файл: {off['media']!r}"
    assert calls == [1], f"фон снимали {len(calls)} раз вместо одного"
    # подложка в .jsx — под условием, а маска-скругление под обратным ему
    assert jsx.index("if(ins.plate && INS_PLATE){ var plateItem=imp(INS_PLATE);") \
        < jsx.index("pc.layers.add(pit)"), "слой подложки добавлен после фото"
    assert jsx.index("if (!(ins.plate && INS_PLATE)) {") < jsx.index("roundMask(L, (W-mw)/2"), \
        "маска-скругление вешается и на вставку с подложкой"


def test_plate_without_style_file_is_an_ordinary_insert(xml_subs, tmp_path, monkeypatch):
    """2. plate=True, а insert_plate_file пуст — вставка как обычная (ни подложки, ни nobg)."""
    photo = _png(tmp_path / "photo.png", 800, 600)
    calls = []

    def fake_remove(data, trim=True, emit=None):
        calls.append(1)
        return _png_bytes(800, 600)

    monkeypatch.setattr(insertlib, "remove_bg", fake_remove)
    ins = [{"type": "photo", "style": "cam2", "media": photo, "start_s": 8.0, "dur_s": 1.5,
            "plate": True}]
    plain = [dict(ins[0])]
    plain[0].pop("plate")

    assert _mask_assets(_build(xml_subs, tmp_path, style={}, inserts=ins)) == \
        _mask_assets(_build(xml_subs, tmp_path, style={}, inserts=plain)), \
        "галку вставки без файла подложки в стиле не видно — .jsx должен быть прежним"
    assert calls == [], "фон снимали, хотя подложки в стиле нет"
    jsx = _build(xml_subs, tmp_path, style={}, inserts=ins)
    assert "nobg.png" not in jsx and "INS_PLATE" not in jsx and "plate" not in _ins_jsons(jsx)[0]


def test_no_plate_anywhere_keeps_the_golden(xml_subs, tmp_path):
    """3. Ни у кого plate — .jsx побайтово как на main, даже с файлом подложки в стиле."""
    golden = _mask_assets(open(GOLDEN, encoding="utf-8-sig").read())
    plate = _png(tmp_path / "plate.png", 512, 512)
    assert _mask_assets(_build(xml_subs, tmp_path,
                               style={"insert_plate_file": plate}, inserts=INS)) == golden, \
        "файл подложки в стиле включил подложку всем вставкам"
    assert _mask_assets(_build(xml_subs, tmp_path, style={}, inserts=INS)) == golden, \
        "дефолтная сборка разошлась с эталоном"


def test_genimage_accepts_plate_slots(client, tmp_path, monkeypatch):
    """4. /api/ai_genimage принимает slot="pa": промпт берётся из image_prompts.pa спикера;
    неизвестный слот — по-прежнему ошибка, до генерации дело не доходит."""
    seen = []

    def fake_gen_image(prompt, prof=None, emit=None, retries=1):
        seen.append(prompt)
        return b"PNG-bytes"

    monkeypatch.setattr(aicut, "gen_image", fake_gen_image)
    monkeypatch.setattr(aicut, "image_rembg_on", lambda: False)
    out = tmp_path / "generated" / "x.png"
    monkeypatch.setattr(insertlib, "add_generated",
                        lambda png, query, dest, ru="", look="": str(out))
    monkeypatch.setattr(insertlib, "_thumb_b64", lambda p: "")

    from core import speakers
    monkeypatch.setattr(speakers, "load", lambda name: {
        "image_prompts": {"pa": {"extra": "on a plate", "pos": "suffix"},
                          "a": {"extra": "plain style", "pos": "suffix"}}})

    assert aicut.IMAGE_PROMPT_SLOTS == ("a", "b", "pa", "pb"), \
        "в наборе слотов нет подложки pa/pb"
    r = client.post("/api/ai_genimage",
                    json={"query": "mug", "slot": "pa", "speaker": "Тест", "dest": str(tmp_path)})
    body = r.get_json()
    assert body.get("ok") is True, body
    assert seen == ["mug on a plate"], f"промпт взят не из слота pa: {seen!r}"

    # неизвестный слот — ошибка, как и раньше; генерацию не запускаем
    seen.clear()
    r = client.post("/api/ai_genimage", json={"query": "mug", "slot": "zz", "dest": str(tmp_path)})
    body = r.get_json()
    assert body.get("err") == "unknown_prompt_slot", body
    assert "zz" in body.get("error", ""), body
    assert seen == [], "неизвестный слот всё-таки ушёл в генерацию"


@node
def test_node_insgencore_picks_plate_slots(tmp_path):
    """5. insGenCore: у вставки с галкой кнопки 1/2 шлют pa/pb, без галки — прежние a/b,
    и в логе видно, что ушёл промпт подложки."""
    with open(SETTINGS_JS, encoding="utf-8") as f:
        src = f.read()
    fn = _func(src, "insGenCore")
    script = """
const __sent=[], __logs=[], __keys=[];
function t(s,vars){__keys.push(s);
  return String(s).replace(/\\{(\\w+)\\}/g,(m,k)=>String((vars||{})[k]));}
function uiLog(m){__logs.push(String(m));}
function errText(e){return String(e);}
function val(){return '';}
const GEN_FETCH_MS=1000;
globalThis.fetch=async (u,o)=>{__sent.push(JSON.parse(o.body));
  return {json:async()=>({path:'C:/gen/x.png'})};};
""" + fn + """
(async()=>{
  await insGenCore({query:'q',plate:true},'a','spk');
  await insGenCore({query:'q',plate:true},'b','spk');
  await insGenCore({query:'q'},'a','spk');
  await insGenCore({query:'q'},'b','spk');
  console.log(JSON.stringify({slots:__sent.map(b=>b.slot),logs:__logs,
                              plate_key:__keys.indexOf(' \\u00b7 \\u043f\\u043e\\u0434\\u043b\\u043e\\u0436\\u043a\\u0430')>=0}));
})();
"""
    p = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=60)
    assert p.returncode == 0, p.stderr.strip()[:600]
    out = json.loads(p.stdout)

    assert out["slots"] == ["pa", "pb", "a", "b"], \
        f"кнопки 1/2 у подложки шлют не pa/pb: {out['slots']!r}"
    assert "подложка" in out["logs"][1], f"в логе нет пометки подложки: {out['logs'][1]!r}"
    assert "подложка" not in out["logs"][2], f"обычная вставка логирует подложку: {out['logs'][2]!r}"
    assert out["logs"][2].startswith("✨ сгенерено (промпт 1)"), out["logs"][2]
    assert out["plate_key"] is True, "лог подложки собран не ключом перевода"


def test_removed_style_keys_are_gone_but_knobs_stay():
    """6. Сторожа схемы/i18n без удалённых ключей: галок стиля нет ни в BASE, ни в схеме,
    а оставшиеся ручки подложки — без show_if и с новым тултипом."""
    fields = _schema_fields()
    for gone in ("insert_plate", "insert_nobg"):
        assert gone not in fields, f"в схеме осталась галка стиля {gone}"
        assert gone not in styles.BASE, f"в styles.BASE остался ключ {gone}"
    for kept in ("insert_plate_file", "insert_plate_scale"):
        assert kept in fields and kept in styles.BASE, f"пропала ручка {kept}"
        assert "show_if" not in fields[kept], f"{kept} всё ещё показывается по галке стиля"
    assert fields["insert_plate_file"]["tip"] == (
        "картинка-подложка для вставок с галкой «на подложке»; у остальных вставок её нет")

    en = json.load(open(EN, encoding="utf-8"))
    for gone in ("фото на фон", "без фона"):
        assert gone not in en, f"в en.json остался перевод удалённого ключа {gone!r}"
    assert en["на подложке"] == "on plate", "у галки вставки нет английского «on plate»"
