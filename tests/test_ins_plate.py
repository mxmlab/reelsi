# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Подложка фото-вставок из стиля и «без фона» (задания ZI/ZK).

Плашка: у вставки с галкой «на подложке» (поле plate) снизу картинка из стиля
(insert_plate_file), фото ложится поверх неё. Масштаб и положение со страницы вставок
двигают только фото ВНУТРИ плашки, подложка стоит на месте, маска-скругление не вешается.
Решение — по ВСТАВКЕ (задание ZK): галок стиля insert_plate/insert_nobg больше нет, у
вставки без галки всё как раньше. «Без фона» — одна функция (insertlib.nobg_path) на
сборку и предпросмотр, кэш рядом с файлом.

Картинки синтетические (PIL, tmp_path): размеры подложки задают геометрию, размеры
фото — его коробку. rembg в тестах подменяется: onnx-модель на CPU тут ни к чему.

Запуск: python -m pytest tests/test_ins_plate.py -q
"""
import gzip
import io
import json
import os
import re
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from core import insertlib, style_schema, styles, xml2ae  # noqa: E402
from tests.test_geometry_python import _mask_assets  # noqa: E402

GOLDEN = os.path.join(HERE, "fixtures", "golden_geometry.jsx")
# Тот же набор, что у геометрии (test_geometry_python.INS): фото на кам1, фото на
# перебивке (cam2-ветка) и фото без выхода. Для golden-сборки пути несуществующие —
# так эталон машино-независим, для плашки ниже картинки настоящие.
INS = [
    {"type": "photo", "style": "cam2", "media": "C:/x/a.png", "start_s": 1, "dur_s": 2},
    {"type": "photo", "style": "cam2", "media": "C:/x/cam2.png", "start_s": 8.0, "dur_s": 1.5},
    {"type": "photo", "style": "cam1", "media": "C:/x/b.png", "start_s": 5, "dur_s": 3},
]


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
    """rembg в тестах не зовём: onnx-модель на CPU тут ни к чему, а без пакета remove_bg
    падает SystemExit и роняет сборку целиком. Кому важно СНЯТИЕ фона — подменяет сам."""
    monkeypatch.setattr(insertlib, "remove_bg", lambda data, trim=True, emit=None: data)


def _png(path, w, h, color=(180, 40, 40, 255)):
    """Синтетический PNG в tmp_path -> путь строкой."""
    from PIL import Image
    Image.new("RGBA", (int(w), int(h)), color).save(str(path))
    return str(path)


def _png_bytes(w, h):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", (int(w), int(h)), (10, 200, 40, 255)).save(buf, "PNG")
    return buf.getvalue()


def _plate_style(tmp_path, plate=None, **over):
    """Стиль с картинкой-подложкой: файл в стиле, галка — у самой вставки (plate).
    Без файла в стиле галка у вставки ничего не делает."""
    plate = plate or _png(tmp_path / "plate.png", 2048, 2048)
    st = {"insert_plate_file": plate}
    st.update(over)
    return st


def _build(xml, tmp_path, style=None, inserts=None):
    """Сборка .jsx без записи эталона: то же, что делает test_geometry_python._build."""
    st = dict(style or {})
    st["intro_riser"] = False
    path, _, _ = xml2ae.to_ae_full(
        xml, jsx_path=str(tmp_path / "out.jsx"),
        inserts=[dict(x) for x in (inserts if inserts is not None else INS)],
        style=st, disclaimer="", intro_riser=False, emit=lambda *a, **k: None)
    return open(path, encoding="utf-8-sig").read()


def _plan(xml, style=None, inserts=None):
    return xml2ae.scene_plan(xml,
                             inserts=[dict(x) for x in (inserts or [])],
                             style=dict(style or {}), disclaimer="", intro_riser=False,
                             emit=lambda *a, **k: None)


def _ins_jsons(jsx):
    return json.loads(re.search(r"var INSERTS=(\[.*?\]);", jsx).group(1))


def test_plate_off_jsx_is_the_main_one(xml_subs, tmp_path):
    """1. Ни у одной вставки галки — .jsx побайтово как на main: golden держит.

    Так же и с картинкой подложки, заданной в стиле: файл сам по себе ничего не включает.
    """
    golden = _mask_assets(open(GOLDEN, encoding="utf-8-sig").read())
    assert _mask_assets(_build(xml_subs, tmp_path, style={})) == golden, \
        "дефолтный стиль разошёлся с эталоном"
    off = {"insert_plate_file": None, "insert_plate_scale": 100}
    assert _mask_assets(_build(xml_subs, tmp_path, style=off)) == golden, \
        "пустые ручки подложки изменили .jsx — подстановки шаблона не пустые"
    assert _mask_assets(_build(xml_subs, tmp_path,
                               style={"insert_plate_file": _png(tmp_path / "p.png", 512, 512)})) == golden, \
        "файл подложки в стиле включил подложку всем вставкам"


def test_plate_layer_is_first_in_precomp_and_no_mask(xml_subs, tmp_path):
    """2. Вставка с галкой: INS_PLATE объявлен, слой подложки добавлен ПЕРВЫМ (до фото),
    roundMask зовётся только для вставок БЕЗ галки, фото берёт ps/px/py из плана."""
    plate = _png(tmp_path / "plate.png", 2048, 2048)
    photo = _png(tmp_path / "photo.png", 800, 600)
    ins = [{"type": "photo", "style": "cam2", "media": photo, "start_s": 8.0, "dur_s": 1.5,
            "plate": True}]
    jsx = _build(xml_subs, tmp_path, style=_plate_style(tmp_path, plate), inserts=ins)

    assert ("var INS_PLATE = " + json.dumps(plate)) in jsx, "путь подложки не уехал в .jsx"
    assert "if(ins.plate && INS_PLATE){ var plateItem=imp(INS_PLATE);" in jsx, \
        "слой подложки добавляется без проверки галки у вставки"
    # порядок в прекомпе: плашка добавлена раньше фото — значит лежит НИЖЕ него
    assert jsx.index("pc.layers.add(plateItem)") < jsx.index("pc.layers.add(pit)"), \
        "подложка добавлена после фото — фото окажется под плашкой"
    assert "if (!(ins.plate && INS_PLATE)) {" in jsx, \
        "маска-скругление не закрыта условием «не на подложке»"
    assert "roundMask(L, (W-mw)/2" in jsx, "маска у обычных вставок пропала вместе с подложкой"
    assert "(ins.plate&&INS_PLATE)?[W/2+ins.px, H/2+ins.py]:[W/2, H/2]" in jsx, \
        "позицию фото внутри прекомпа ставит не план"
    assert "(ins.plate&&INS_PLATE)?[(ins.ps||100),(ins.ps||100)]:[_f*100,_f*100]" in jsx, \
        "масштаб фото внутри прекомпа ставит не план"

    # те же числа, что в плане сцены (одна дверь: геометрию считает Python)
    plan = _plan(xml_subs, style=_plate_style(tmp_path, plate), inserts=ins)
    got = _ins_jsons(jsx)[0]
    want = plan["inserts"][0]
    for k in ("ps", "px", "py", "scale"):
        assert got[k] == want[k], f"ins.{k}: в .jsx {got[k]!r}, в плане {want[k]!r}"
    assert got["plate"] is True, "признак «на подложке» не уехал в данные вставки"
    card = want["card"]
    assert card["plate"] == plate
    assert card["w"] == pytest.approx(1080 * got["scale"] / 100, rel=1e-3)
    assert card["photo"]["w"] == pytest.approx(800 * got["ps"] / 100 * got["scale"] / 100,
                                               rel=1e-3)


def test_plate_geometry_does_not_move_with_manual_shift(xml_subs, tmp_path):
    """3. Ручной сдвиг и масштаб двигают ТОЛЬКО фото: у слоя те же ключи, что при 0/100,
    меняются только ps (×1.5) и px (экранные px, делённые на масштаб слоя)."""
    plate = _png(tmp_path / "plate.png", 1024, 1024)
    photo = _png(tmp_path / "photo.png", 800, 600)
    st = _plate_style(tmp_path, plate)
    base = {"type": "photo", "style": "cam2", "media": photo, "start_s": 8.0, "dur_s": 1.5,
            "plate": True}
    a = _plan(xml_subs, style=st, inserts=[dict(base)])["inserts"][0]
    b = _plan(xml_subs, style=st, inserts=[dict(base, x=40, sc=150)])["inserts"][0]

    assert b["anim"] == a["anim"], "сдвиг фото сдвинул анимацию слоя прекомпа"
    assert b["scale"] == a["scale"], "ручной масштаб фото изменил масштаб плашки"
    assert b["ps"] == pytest.approx(a["ps"] * 1.5, rel=1e-3), "sc не домножил масштаб фото"
    assert b["px"] == pytest.approx(40 / (a["scale"] / 100), rel=1e-3), \
        "px считается не делением экранных px на масштаб слоя"
    assert b["py"] == 0
    # точка покоя cam2 у плашки общая: ручные x/y в неё не уезжают
    assert a["x"] == b["x"] == 0 and a["y"] == b["y"] == 0 and b["sc"] == 100


def test_nobg_path_caches_and_falls_back(tmp_path, monkeypatch):
    """4. nobg_path: кэш новее исходника — rembg не зовём; исходник поправили — пересчёт;
    ошибка rembg и не-картинка — исходный путь."""
    src = _png(tmp_path / "pic.png", 40, 30)
    calls = []

    def fake_remove(data, trim=True, emit=None):
        calls.append(trim)
        return _png_bytes(12, 9)

    monkeypatch.setattr(insertlib, "remove_bg", fake_remove)
    p1 = insertlib.nobg_path(src, emit=lambda *a, **k: None)
    assert p1 == os.path.join(str(tmp_path), "pic.nobg.png"), p1
    assert len(calls) == 1 and calls[0] is True, "фон снимается с обрезкой прозрачных полей"
    assert insertlib.nobg_path(src, emit=lambda *a, **k: None) == p1
    assert len(calls) == 1, "кэш новее исходника, а rembg позвали второй раз"

    # исходник перегенерировали — старый кэш показывал бы прошлую картинку
    st = os.stat(p1).st_mtime
    os.utime(src, (st + 5, st + 5))
    assert insertlib.nobg_path(src, emit=lambda *a, **k: None) == p1
    assert len(calls) == 2, "устаревший кэш не пересчитали"

    # видео не картинка — как есть, без rembg
    vid = str(tmp_path / "clip.mp4")
    open(vid, "wb").write(b"\x00")
    assert insertlib.nobg_path(vid) == vid
    assert len(calls) == 2

    os.remove(p1)

    def boom(*a, **k):
        raise RuntimeError("нет модели")

    monkeypatch.setattr(insertlib, "remove_bg", boom)
    assert insertlib.nobg_path(src, emit=lambda *a, **k: None) == src, \
        "ошибка rembg обязана отдать исходный путь, а не пустоту"


def test_nobg_path_survives_system_exit_from_remove_bg(tmp_path, monkeypatch):
    """5. Без rembg/onnxruntime remove_bg кидает SystemExit, а не Exception: nobg_path
    обязан вернуть ИСХОДНЫЙ путь и сказать об этом в emit. До правки (задание ZK)
    SystemExit проходил насквозь и ронял всю сборку со вставкой «на подложке»."""
    src = _png(tmp_path / "pic.png", 40, 30)
    log = []

    def no_rembg(data, trim=True, emit=None):
        raise SystemExit("для снятия фона нужен пакет rembg")

    monkeypatch.setattr(insertlib, "remove_bg", no_rembg)
    got = insertlib.nobg_path(src, emit=log.append)
    assert got == src, "SystemExit от remove_bg обязан отдать исходник, а не пустоту"
    assert not os.path.exists(os.path.join(str(tmp_path), "pic.nobg.png")), \
        "кэш без фона появился, хотя фон не снимали"
    assert log, "о неснятом фоне в emit не сообщили"
    line = " ".join(log)
    assert "pic.png" in line and "rembg" in line, f"в сообщении нет имени файла и причины: {log!r}"


def test_to_ae_full_takes_nobg_media_for_the_plate_insert(xml_subs, tmp_path, monkeypatch):
    """6. У вставки с галкой «на подложке» в .jsx уехал путь кэша *.nobg.png."""
    plate = _png(tmp_path / "plate.png", 1024, 1024)
    photo = _png(tmp_path / "gen.png", 800, 600)
    calls = []

    def fake_remove(data, trim=True, emit=None):
        calls.append(1)
        return _png_bytes(800, 600)

    monkeypatch.setattr(insertlib, "remove_bg", fake_remove)
    ins = [{"type": "photo", "style": "cam2", "media": photo, "start_s": 8.0, "dur_s": 1.5,
            "plate": True}]
    jsx = _build(xml_subs, tmp_path, style=_plate_style(tmp_path, plate), inserts=ins)
    assert calls, "фон не снимали: галка вставки не доехала до сборки"
    assert json.dumps(os.path.join(str(tmp_path), "gen.nobg.png")) in jsx, \
        "в .jsx остался исходник вместо кэша без фона"
    assert json.dumps(photo) not in jsx, "исходный путь с фоном всё ещё в .jsx"
    assert _ins_jsons(jsx)[0]["media"].endswith("gen.nobg.png")


def test_plate_knobs_are_in_schema_and_base():
    """Ручки из таблицы задания: ключи, контролы, дефолты. Галок стиля больше нет,
    show_if у подложки тоже: решение принимает вставка, а не стиль."""
    found = {}

    def walk(items):
        for it in items:
            if it.get("type") == "field":
                found[it.get("key")] = it
            walk(it.get("items", []))

    for layer in style_schema.LAYERS:
        walk(layer.get("items", []))

    want = {"insert_plate_file": ("file", None), "insert_plate_scale": ("num", 100.0)}
    for k, (ctl, default) in want.items():
        assert k in found, f"в схеме нет ручки {k}"
        assert found[k]["ctl"] == ctl, f"{k}: контрол {found[k]['ctl']!r} вместо {ctl!r}"
        assert styles.BASE[k] == default, f"{k}: дефолт {styles.BASE[k]!r} вместо {default!r}"
        assert "show_if" not in found[k], f"{k}: ручка всё ещё показывается по галке стиля"
    for gone in ("insert_plate", "insert_nobg"):
        assert gone not in found, f"в схеме осталась галка стиля {gone}"
        assert gone not in styles.BASE, f"в styles.BASE остался ключ {gone}"
    assert found["insert_plate_file"].get("nullable") is True
    assert "sfx" not in found["insert_plate_file"], \
        "у подложки нет звука — карандаш редактора ей не положен"
    assert found["insert_plate_file"]["tip"] == (
        "картинка-подложка для вставок с галкой «на подложке»; у остальных вставок её нет")
    assert found["insert_plate_scale"]["min"] == 30 and found["insert_plate_scale"]["max"] == 300
    assert found["insert_plate_scale"]["lim_min"] == 10
    assert found["insert_plate_scale"]["lim_max"] == 500
