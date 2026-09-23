# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Вставка на подложке: пропорции фото в превью и драг всей карточки.

Два дефекта владельца:

1. rembg обрезает пустые поля (trim=True), и пропорции меняются сильно: 1408×768 (1.83) ->
   176×451 (0.39), 1024×1024 (1.00) -> 594×699 (0.85). В .jsx уезжал обрезанный PNG (путь
   подменял _nobg_kw в to_ae_full ДО scene_plan), а план для превью (/api/scene) считал
   рамку карточки по ИСХОДНИКУ — CSS растягивал картинку в чужую рамку, фото на подложке
   было сплющено. Теперь путь подменяет САМ план сцены (одна дверь): и .jsx, и превью
   берут размеры рамки и файл из одного места, а rembg на файл зовётся один раз (кэш).
2. Драг вставки «на подложке» в превью двигал фото ВНУТРИ плашки. Теперь он двигает всю
   карточку: в _ins_js точка покоя и ключи слоя считаются по kx/ky, превью пишет kx/ky
   (у остальных вставок — по-прежнему x/y). Поля масштаба и положения на странице вставок
   двигают только фото — как было в ZI.

Картинки синтетические (PIL, tmp_path): размеры подложки задают геометрию, размеры фото —
его коробку. rembg подменяется: onnx-модель на CPU тут ни к чему.

Запуск: python -m pytest tests/test_plate_fix.py -q
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

from PIL import Image  # noqa: E402
from core import insertlib, xml2ae  # noqa: E402

JS = os.path.join(ROOT, "static", "app", "85-inserts-view.js")
node = pytest.mark.skipif(not shutil.which("node"), reason="контракт фронта требует node в PATH")


@pytest.fixture()
def xml_subs(tmp_path):
    """Фикстура таймлайна (та же, что у test_geometry_python)."""
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
    """rembg в тестах не зовём: кому важно снятие фона — подменяет remove_bg сам."""
    monkeypatch.setattr(insertlib, "remove_bg", lambda data, trim=True, emit=None: data)


def _png(path, w, h, color=(180, 40, 40, 255)):
    from PIL import Image
    Image.new("RGBA", (int(w), int(h)), color).save(str(path))
    return str(path)


def _png_bytes(w, h):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", (int(w), int(h)), (10, 200, 40, 255)).save(buf, "PNG")
    return buf.getvalue()


def _plan(xml, style=None, inserts=None):
    st = dict(style or {})
    st["intro_riser"] = False                          # ризер тянет путь ассета с этой машины
    return xml2ae.scene_plan(xml, inserts=[dict(x) for x in (inserts or [])],
                             style=st, disclaimer="", intro_riser=False,
                             emit=lambda *a, **k: None)


def _build(xml, tmp_path, style=None, inserts=None):
    st = dict(style or {})
    st["intro_riser"] = False
    path, _, _ = xml2ae.to_ae_full(
        xml, jsx_path=str(tmp_path / "out.jsx"),
        inserts=[dict(x) for x in (inserts if inserts is not None else [])],
        style=st, disclaimer="", intro_riser=False, emit=lambda *a, **k: None)
    return open(path, encoding="utf-8-sig").read()


def _ins_jsons(jsx):
    return json.loads(re.search(r"var INSERTS=(\[.*?\]);", jsx).group(1))


# ---- фронт: тела функций из static/app/85-inserts-view.js для прогона в node ----

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


def _drag_handler(src):
    """Обработчик `$('ipvins').addEventListener('pointerdown',e=>{...});` целиком.

    Драг вставок живёт не функцией, а стрелкой в addEventListener — берём по балансу
    скобок от её тела (в теле нет ни строк, ни шаблонов с фигурными скобками). Возвращаем
    готовое выражение: закрывающую `)` вызова дописываем сами — баланс скобок её не видит."""
    m = re.search(r"\$\('ipvins'\)\.addEventListener\('pointerdown',\s*e=>\{", src)
    assert m, "в исходнике не нашёлся обработчик драга вставок (#ipvins pointerdown)"
    i = m.end() - 1
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():j + 1] + ");"
    raise AssertionError("не сошлись скобки у обработчика драга вставок")


def _js(*names):
    with open(JS, "r", encoding="utf-8") as f:
        src = f.read()
    return "\n".join(_func(src, n) for n in names)


def _run_node(code):
    p = subprocess.run(["node", "-e", code], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


# ---- 1. пропорции фото в плане — по файлу, который показывается ----

def test_plan_photo_keeps_nobg_proportions(xml_subs, tmp_path, monkeypatch):
    """1. Снятый фон обрезает поля: рамка карточки в плане обязана считаться по .nobg.png.

    Заодно: путь в плане — кэш .nobg.png, а сборка (to_ae_full) фон НЕ пересчитывает —
    rembg на один файл зовётся ровно один раз (кэш рядом с исходником).
    """
    plate = _png(tmp_path / "plate.png", 1024, 1024)
    photo = _png(tmp_path / "photo.png", 1408, 768)    # 1.83 — как у генератора владельца
    calls = []

    def fake_remove(data, trim=True, emit=None):
        calls.append(trim)
        return _png_bytes(176, 451)                    # 0.39 — как отдаёт rembg с trim=True

    monkeypatch.setattr(insertlib, "remove_bg", fake_remove)
    ins = [{"type": "photo", "style": "cam2", "media": photo, "start_s": 8.0, "dur_s": 1.5,
            "plate": True}]
    plan = _plan(xml_subs, style={"insert_plate_file": plate}, inserts=ins)
    got = plan["inserts"][0]
    nobg = os.path.join(str(tmp_path), "photo.png.nobg.png")

    assert got["media"] == nobg and got["media"].endswith(".nobg.png"), \
        f"план несёт не кэш без фона: {got['media']!r}"
    assert calls == [True], f"фон снимали {len(calls)} раз вместо одного"

    with Image.open(nobg) as im:                       # размеры — из самого файла, не из чисел теста
        nw, nh = im.size
    assert (nw, nh) == (176, 451), f"кэш не того размера: {nw}x{nh}"
    card = got["card"]
    assert card["photo"]["w"] / card["photo"]["h"] == pytest.approx(nw / nh, rel=1e-3), \
        ("рамка фото в плане считается не по .nobg.png: "
         f"{card['photo']['w']}x{card['photo']['h']} против файла {nw}x{nh}")
    assert nw / nh != pytest.approx(1408 / 768, rel=0.05), "тест бессмыслен: пропорции совпали"

    # сборка: в .jsx уехал тот же кэш, и rembg второй раз не звали
    jsx = _build(xml_subs, tmp_path, style={"insert_plate_file": plate}, inserts=ins)
    assert calls == [True], f"to_ae_full пересчитал фон: rembg позвали {len(calls)} раз"
    assert json.dumps(nobg) in jsx, "в .jsx уехал не кэш без фона"
    assert _ins_jsons(jsx)[0]["media"] == nobg


# ---- 2. kx/ky двигают ВСЮ карточку, x/y — только фото внутри плашки ----

def _key_times(a_keys, b_keys):
    """Моменты ключей совпадают (сдвиг карточки не трогает тайминг вылета)."""
    assert [k[0] for k in a_keys] == [k[0] for k in b_keys], \
        f"моменты ключей разошлись: {a_keys} против {b_keys}"


def _key_shift(a_keys, b_keys, dx, dy):
    """Все ключи b сдвинуты относительно a ровно на (dx, dy)."""
    assert len(a_keys) == len(b_keys), f"число ключей разошлось: {a_keys} против {b_keys}"
    for ka, kb in zip(a_keys, b_keys):
        assert ka[0] == kb[0], f"момент ключа поехал: {ka} против {kb}"
        assert kb[1][0] - ka[1][0] == pytest.approx(dx, abs=1e-6), f"сдвиг по X: {ka} -> {kb}"
        assert kb[1][1] - ka[1][1] == pytest.approx(dy, abs=1e-6), f"сдвиг по Y: {ka} -> {kb}"


def _rest_shift(a_keys, b_keys, dx, dy):
    """Точка покоя (up) сдвинута на (dx, dy), точки dn (за кадром) — как были.

    dn в _cam1_pos_keys считается БЕЗ ручного сдвига («сдвиг точки покоя применяется уже
    над нулём»), поэтому сдвиг всей карточки обязан быть ровно в up-ключах.
    """
    _key_times(a_keys, b_keys)
    dn = a_keys[0][1]
    moved = 0
    for ka, kb in zip(a_keys, b_keys):
        if ka[1] == dn:
            assert kb[1] == dn, f"точка dn уехала вместе со сдвигом карточки: {ka} -> {kb}"
            continue
        assert kb[1][0] - ka[1][0] == pytest.approx(dx, abs=1e-6), f"сдвиг по X: {ka} -> {kb}"
        assert kb[1][1] - ka[1][1] == pytest.approx(dy, abs=1e-6), f"сдвиг по Y: {ka} -> {kb}"
        moved += 1
    assert moved, f"в ключах нет точки покоя (up): {a_keys}"


def test_kx_ky_move_the_whole_card(xml_subs, tmp_path):
    """2. kx/ky — сдвиг ВСЕЙ карточки: точка покоя слоя и его ключи анимации.

    Фото внутри плашки при этом не двигается вовсе: ps/px/py те же, что без kx/ky.
    """
    plate = _png(tmp_path / "plate.png", 1024, 1024)
    photo = _png(tmp_path / "photo.png", 800, 600)
    base = {"type": "photo", "style": "cam1", "media": photo, "start_s": 5, "dur_s": 3,
            "plate": True}

    # кам1: ключи вылета из-за спины считает Python — сдвиг карточки обязан быть в них
    st1 = {"insert_plate_file": plate}
    a = _plan(xml_subs, style=st1, inserts=[dict(base)])["inserts"][0]
    b = _plan(xml_subs, style=st1, inserts=[dict(base, kx=50, ky=-20)])["inserts"][0]
    assert (a["x"], a["y"]) == (0, 0) and (b["x"], b["y"]) == (50, -20), \
        f"точка покоя слоя не взяла kx/ky: {a['x']},{a['y']} -> {b['x']},{b['y']}"
    pa, pb = a["anim"].get("position"), b["anim"].get("position")
    assert pa and pb, "у кам1 пропали ключи вылета"
    _rest_shift(pa, pb, 50, -20)
    for k in ("ps", "px", "py", "scale"):
        assert b[k] == a[k], f"kx/ky тронули фото внутри плашки ({k}): {a[k]} -> {b[k]}"
    assert b["card"]["photo"]["x"] == a["card"]["photo"]["x"] == 0

    # кам2 (окно 8.0–9.5 фикстуры — вставка целиком на перебивке, стиль не перебьётся на кам1)
    # с выездом снизу: обе точки выезда считаются от точки покоя — сдвинуты обе
    st2 = {"insert_plate_file": plate, "insert_anim": "rise"}
    c2 = dict(base, style="cam2", start_s=8.0, dur_s=1.5)
    c = _plan(xml_subs, style=st2, inserts=[dict(c2)])["inserts"][0]
    d = _plan(xml_subs, style=st2, inserts=[dict(c2, kx=50, ky=-20)])["inserts"][0]
    assert c["style"] == d["style"] == "cam2", "вставка уехала на кам1 — тест не про кам2"
    assert (d["x"], d["y"]) == (50, -20) and (c["x"], c["y"]) == (0, 0)
    pc, pd = c["anim"]["position"], d["anim"]["position"]
    _key_times(pc, pd)
    _key_shift(pc, pd, 50, -20)
    assert d["anim"]["scale"] == c["anim"]["scale"], "kx/ky тронули масштаб слоя"
    for k in ("ps", "px", "py"):
        assert d[k] == c[k], f"kx/ky тронули фото внутри плашки ({k}): {c[k]} -> {d[k]}"

    # ручные x/y (поля страницы вставок) по-прежнему двигают ТОЛЬКО фото
    e = _plan(xml_subs, style=st1,
              inserts=[dict(base, kx=50, ky=-20, x=40, y=7)])["inserts"][0]
    assert (e["x"], e["y"]) == (b["x"], b["y"]), "ручные x/y поехали в точку покоя слоя"
    assert e["px"] != b["px"] and e["card"]["photo"]["x"] == 40, \
        "ручные x/y не доехали до фото внутри плашки"


# ---- 3. драг в превью: плашка пишет kx/ky, обычная вставка — x/y ----

_DRAG_STUBS = """
const HANDLERS={}, WINS={};
const window={addEventListener:(t,fn)=>{WINS[t]=fn;},removeEventListener:(t)=>{delete WINS[t];}};
function $(id){const el={addEventListener:(t,fn)=>{HANDLERS[id+':'+t]=fn;},clientWidth:1080};
  return el;}
let IPVMODE='ae';
const IPV={plan:null,insShift:null};
let curAE=0;
const CLIPS=[{inserts:[]}];
const INS=[];
function captureAE(){}
function ipvPlanSoon(){}
function ipvInsPlace(){}
function ipvIntroHitAt(){return null;}
function ipvIntroDragStart(){}
function ipvNow(){return 0;}
function ipvZoomAt(){return 1;}
function insDrag(x,media,dx,dy){
  IPV.plan={w:1080,inserts:[x]};
  INS.length=0;INS.push({media:media,x:0,y:0});
  CLIPS[0].inserts=[{media:media,x:0,y:0}];
  IPV.insShift=null;
  const wr={dataset:{ins:'0'},clientWidth:1080};
  const ev={clientX:500,clientY:500,preventDefault(){},stopPropagation(){},
            target:{closest:()=>({dataset:{ins:'0'},clientWidth:1080})}};
  HANDLERS['ipvins:pointerdown'](ev);
  WINS['pointermove']({clientX:500+dx,clientY:500+dy,shiftKey:false});
  WINS['pointerup']({clientX:500+dx,clientY:500+dy,shiftKey:false});
  return {ins:INS[0],card:CLIPS[0].inserts[0]};}
"""


@node
def test_preview_drag_writes_kx_ky_for_plate():
    """3. Драг: у вставки на подложке сдвиг уезжает в kx/ky (не в x/y), у обычной — в x/y."""
    with open(JS, "r", encoding="utf-8") as f:
        src = f.read()
    code = (_js("normInsPath", "axisLock", "cardToIns") + "\n"
            + _DRAG_STUBS + "\n" + _drag_handler(src) + """
    const plate={card:{w:400,h:300,plate:'C:/x/plate.png',photo:{w:100,h:80,x:5,y:6}},
                 style:'cam2',x:0,y:0,scale:44,sc:100,anim:{}};
    const plain={card:{w:400,h:300,pw:800,ph:600},style:'cam2',x:0,y:0,scale:44,sc:100,anim:{}};
    const a=insDrag(plate,'C:/x/photo.png',20,-10);
    const b=insDrag(plain,'C:/x/plain.png',20,-10);
    const card={media:'C:/x/p.png',start_sec:1,duration_sec:2,plate:true};
    console.log(JSON.stringify({
      plate_ins:a.ins, plate_card:a.card, plain_ins:b.ins, plain_card:b.card,
      kept:cardToIns(card,{kx:50,ky:-20}),
      card_wins:cardToIns({...card,kx:7,ky:8},{kx:50,ky:-20}),
      zero_is_a_value:cardToIns({...card,kx:0,ky:0},{kx:50,ky:-20})}));
    """)
    out = _run_node(code)

    # подложка: сдвиг в kx/ky и в данных шага 3, и в карточке шага 2
    assert out["plate_ins"]["kx"] == 20 and out["plate_ins"]["ky"] == -10, out["plate_ins"]
    assert out["plate_ins"]["x"] == 0 and out["plate_ins"]["y"] == 0, \
        f"драг подложки уехал в x/y фото: {out['plate_ins']}"
    assert out["plate_card"]["kx"] == 20 and out["plate_card"]["x"] == 0, out["plate_card"]

    # обычная вставка — как было: только x/y, никаких kx/ky
    assert out["plain_ins"]["x"] == 20 and out["plain_ins"]["y"] == -10, out["plain_ins"]
    assert "kx" not in out["plain_ins"] and "kx" not in out["plain_card"], \
        f"kx/ky завелись у обычной вставки: {out['plain_ins']} {out['plain_card']}"

    # перенос из разметки kx/ky не затирает: карточка важнее, ноль — тоже значение
    assert (out["kept"]["kx"], out["kept"]["ky"]) == (50, -20), out["kept"]
    assert (out["card_wins"]["kx"], out["card_wins"]["ky"]) == (7, 8), out["card_wins"]
    assert (out["zero_is_a_value"]["kx"], out["zero_is_a_value"]["ky"]) == (0, 0), \
        f"сдвиг, возвращённый в 0, подменяется старым: {out['zero_is_a_value']}"


@node
def test_preview_card_key_ignores_nobg_suffix():
    """3в. Карточка списка ищется по ИСХОДНИКУ: у вставки на подложке в плане путь кэша."""
    code = _js("normInsPath", "insCardKey") + """
    console.log(JSON.stringify({
      plate:insCardKey({media:'C:/x/p.png.nobg.png',plate:true}),
      plain:insCardKey({media:'C:/x/p.png.nobg.png'}),
      usual:insCardKey({media:'C:/x/p.png',plate:true})}));
    """
    out = _run_node(code)

    assert out["plate"] == "c:\\x\\p.png", \
        f"вставка на подложке не нашла бы свою карточку в списке: {out['plate']}"
    assert out["usual"] == "c:\\x\\p.png", out["usual"]
    assert out["plain"] == "c:\\x\\p.png.nobg.png", \
        f"у вставки без галки путь подменился: {out['plain']}"


@node
def test_preview_place_moves_whole_card_on_plate():
    """3б. Показ: сдвиг драга двигает у подложки ВСЮ карточку, а фото внутри — нет."""
    code = _js("keysAt", "ipvZoomAt", "ipvCamChild", "ipvInsPlace") + """
    function zoomPlan(){return {w:1080,h:1920,ins_c2x:540,ins_c2y:330,
      zoom:{cx:0.5,cy:0.5,rot:0,pan:[0,0],keys:[[0,100]],ease:[[33.3333,33.3333]]}};}
    const ipl={style:{}},iph={style:{}};
    const el={style:{},querySelector:(s)=>s==='img.iplate'?ipl:(s==='img.iphoto'?iph:null)};
    const wr={dataset:{ins:'0'},clientWidth:1080,firstChild:el};
    const IPV={fps:60,plan:null,insShift:{i:0,dx:30,dy:40}};
    const plate={card:{w:400,h:300,plate:'C:/x/plate.png',photo:{w:100,h:80,x:5,y:6}},
                 style:'cam2',x:0,y:0,scale:44,sc:100,anim:{}};
    const plain={card:{w:400,h:300,pw:800,ph:600},style:'cam2',x:0,y:0,scale:44,sc:100,anim:{}};
    IPV.plan=zoomPlan();
    ipvInsPlace(wr,plate,0);
    const plateCard=el.style.transform, platePhoto=iph.style.transform, plateImg=ipl.style.width;
    el.style.transform='';iph.style.transform='';
    ipvInsPlace(wr,plain,0);
    console.log(JSON.stringify({plate_card:plateCard,plate_photo:platePhoto,plate_img:plateImg,
                                plain_card:el.style.transform}));
    """
    out = _run_node(code)

    # Кам2: точка покоя слоя = ins_c2y от центра кадра (330−960=−630) плюс сдвиг драга
    assert out["plate_card"] == "translate(30px,-590px)", \
        f"сдвиг драга не двигает карточку на подложке: {out['plate_card']}"
    assert out["plate_photo"] == "translate(-50%,-50%) translate(5px,6px)", \
        f"сдвиг драга всё ещё двигает фото внутри плашки: {out['plate_photo']}"
    assert out["plate_img"] == "100%", "плашка не растянута на всю карточку"
    assert out["plain_card"] == "translate(30px,-590px)", \
        f"обычная вставка поехала не как раньше: {out['plain_card']}"
