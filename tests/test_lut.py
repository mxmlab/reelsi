# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Таблицы LUT (.cube): разбор ядром, отдача браузеру и хранение в профиле спикера.

LUT на видео камеры — данные, а не настройка: цвет в превью считает шейдер браузера
(static/app/86-lut.js), а в сборку таблица уедет прожжённой в видео. Здесь стерегутся
три места, где файл превращается в эти данные: разбор (core/lut.py), роут /api/lut
(тот же путь и те же проверки, что у /api/media) и поле `lut` профиля спикера.
"""
from __future__ import annotations

import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

import api  # noqa: E402
from core import lut  # noqa: E402
from core import speakers  # noqa: E402
from core.umsg import ReelsiError  # noqa: E402

# Хост настоящего локального клиента: без него запрос отбивает защита от DNS rebinding
H = {"Host": "127.0.0.1:5001"}


def _identity(size: int = 2, dmin: tuple[float, float, float] = (0.0, 0.0, 0.0),
              dmax: tuple[float, float, float] = (1.0, 1.0, 1.0)) -> str:
    """Тождественный LUT текстом: цвет на выходе равен входу.

    Порядок строк — как требует формат .cube: красный меняется быстрее всех, то
    есть внутри блока идёт r, потом g, потом b.
    """
    out = ['TITLE "identity"', "# тождественная таблица", "", f"LUT_3D_SIZE {size}",
           f"DOMAIN_MIN {dmin[0]} {dmin[1]} {dmin[2]}",
           f"DOMAIN_MAX {dmax[0]} {dmax[1]} {dmax[2]}"]
    step = 1.0 / (size - 1)
    for b in range(size):
        for g in range(size):
            for r in range(size):
                out.append(f"{dmin[0] + r * step * (dmax[0] - dmin[0]):.6f} "
                           f"{dmin[1] + g * step * (dmax[1] - dmin[1]):.6f} "
                           f"{dmin[2] + b * step * (dmax[2] - dmin[2]):.6f}")
    return "\n".join(out) + "\n"


def _write(tmp_path, text: str, name: str = "cam1.cube") -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


# --------------------------------------------------------------------------- #
# Разбор .cube
# --------------------------------------------------------------------------- #
def test_тождественная_таблица_разбирается(tmp_path):
    """Размер, домен и точки — как в файле; красный меняется быстрее всех."""
    data = lut.load_cube(_write(tmp_path, _identity()))
    assert data["size"] == 2
    assert data["domain_min"] == [0.0, 0.0, 0.0]
    assert data["domain_max"] == [1.0, 1.0, 1.0]
    assert len(data["data"]) == 24
    # первые четыре точки по порядку: (0,0,0), (1,0,0), (0,1,0), (1,1,0)
    assert data["data"][0:12] == [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0]
    assert data["data"][12:15] == [0.0, 0.0, 1.0]


def test_домен_читается(tmp_path):
    """DOMAIN_MIN/MAX не 0..1 (лог-материал) — как заданы в файле."""
    data = lut.load_cube(_write(tmp_path, _identity(size=2, dmin=(-1.0, -1.0, -1.0),
                                                   dmax=(1.0, 1.0, 1.0))))
    assert data["domain_min"] == [-1.0, -1.0, -1.0]
    assert data["domain_max"] == [1.0, 1.0, 1.0]
    assert data["data"][0:3] == [-1.0, -1.0, -1.0]
    assert data["data"][-3:] == [1.0, 1.0, 1.0]


def test_таблица_1d_отклоняется(tmp_path):
    """1D-таблицу наложить на кадр нельзя — отдельный код, чтобы фронт объяснил."""
    path = _write(tmp_path, "LUT_1D_SIZE 32\n0.0 0.0 0.0\n", "one_d.cube")
    with pytest.raises(ReelsiError) as e:
        lut.load_cube(path)
    assert e.value.code == "lut_1d_unsupported"


@pytest.mark.parametrize("text", [
    "LUT_3D_SIZE 2\n0.0 0.0 0.0\n",                      # точек меньше, чем обещает размер
    "0.0 0.0 0.0\n1.0 1.0 1.0\n",                        # нет LUT_3D_SIZE
    "LUT_3D_SIZE 2\n0.0 0.0\n",                          # в строке два числа вместо трёх
    "LUT_3D_SIZE 2\n0.0 0.0 ноль\n",                     # не число
    "LUT_3D_SIZE два\n0.0 0.0 0.0\n",                    # размер не число
    "LUT_3D_SIZE 2\nКАКОЙ_ТО_КЛЮЧ 1 2 3\n",              # чужая строка-ключ
])
def test_битый_файл_отклоняется(tmp_path, text):
    path = _write(tmp_path, text, "broken.cube")
    with pytest.raises(ReelsiError) as e:
        lut.load_cube(path)
    assert e.value.code == "lut_bad"


def test_нет_файла_отклоняется(tmp_path):
    with pytest.raises(ReelsiError) as e:
        lut.load_cube(str(tmp_path / "нет-такого.cube"))
    assert e.value.code == "lut_bad"


def test_кеш_следит_за_mtime(tmp_path):
    """Кеш по (путь, mtime): тот же файл с новым mtime читается заново, а подмена
    содержимого без смены mtime — нет (иначе смысл ключа был бы не в mtime)."""
    path = tmp_path / "cam.cube"
    path.write_text(_identity(), encoding="utf-8")
    first = lut.load_cube(str(path))
    assert first["data"][3:6] == [1.0, 0.0, 0.0]

    stamp = os.stat(path).st_mtime_ns
    path.write_text(_identity().replace("1.000000 0.000000 0.000000",
                                        "0.500000 0.000000 0.000000"), encoding="utf-8")
    os.utime(path, ns=(stamp, stamp))                    # mtime тот же — кеш и должен отвечать
    assert lut.load_cube(str(path))["data"][3:6] == [1.0, 0.0, 0.0]

    later = stamp + 10_000_000_000                       # файл заменили — читаем заново
    os.utime(path, ns=(later, later))
    assert lut.load_cube(str(path))["data"][3:6] == [0.5, 0.0, 0.0]


# --------------------------------------------------------------------------- #
# Роут /api/lut
# --------------------------------------------------------------------------- #
def test_роут_отдаёт_таблицу(client, tmp_path):
    path = _write(tmp_path, _identity())
    r = client.get(f"/api/lut?path={path}", headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True
    assert d["size"] == 2 and d["domain_max"] == [1.0, 1.0, 1.0]
    assert len(d["data"]) == 24


def test_роут_отказывает_не_cube(client, tmp_path):
    """Расширение — первый рубеж: .cube не медиа и в ALLOWED_MEDIA_EXTS его нет."""
    path = _write(tmp_path, "не таблица", "notes.txt")
    r = client.get(f"/api/lut?path={path}", headers=H)
    assert r.status_code == 403


def test_роут_отказывает_пустому_пути(client):
    assert client.get("/api/lut?path=", headers=H).status_code == 403


def test_роут_отвечает_404_на_несуществующий(client, tmp_path):
    r = client.get(f"/api/lut?path={tmp_path / 'нет.cube'}", headers=H)
    assert r.status_code == 404


def test_роут_отдаёт_код_битого_файла(client, tmp_path):
    """Битый .cube — не 500 и не HTML: фронт показывает текст по коду ERR_lut_bad."""
    path = _write(tmp_path, "LUT_3D_SIZE 2\n0.0 0.0 0.0\n", "broken.cube")
    d = client.get(f"/api/lut?path={path}", headers=H).get_json()
    assert d["err"] == "lut_bad"
    assert d["err_vars"]["path"] == "broken.cube"


def test_роут_не_отдаёт_секреты(client, tmp_path, monkeypatch):
    """Список запрещённого — общий с /api/media (`_media_path_ok`)."""
    cfg = tmp_path / "ai_config.json"
    cfg.write_text('{"profiles": {"x": {"api_key": "sk-СЕКРЕТ"}}}', encoding="utf-8")
    r = client.get(f"/api/lut?path={cfg}", headers=H)
    assert r.status_code == 403
    assert b"sk-" not in r.data


# --------------------------------------------------------------------------- #
# Роут /api/pickcube
# --------------------------------------------------------------------------- #
def test_pickcube_отдаёт_выбранный_файл(client, monkeypatch):
    monkeypatch.setattr("api.files._native_pick", lambda call: "C:/lut/cam1.cube")
    r = client.get("/api/pickcube", headers=H)
    assert r.status_code == 200
    assert r.get_json()["path"] == "C:/lut/cam1.cube"


def test_pickcube_чужому_сайту_диалог_не_открывает(client, monkeypatch):
    """Диалог Tk — побочное действие: чужой странице оно достаться не должно."""
    calls: list[str] = []
    monkeypatch.setattr("api.files._native_pick", lambda call: calls.append(call) or "")
    r = client.get("/api/pickcube", headers={**H, "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    assert calls == []


# --------------------------------------------------------------------------- #
# Поле lut в профиле спикера
# --------------------------------------------------------------------------- #
def test_профиль_хранит_lut(tmp_path, monkeypatch):
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(tmp_path))
    speakers.save("Две камеры", {"lut": {"1": "C:/lut/cam1.cube", "2": "D:/LUT/CAM2.CUBE"}})
    assert speakers.load("Две камеры")["lut"] == {"1": "C:/lut/cam1.cube",
                                                  "2": "D:/LUT/CAM2.CUBE"}


def test_пустой_путь_в_профиль_не_пишется(tmp_path, monkeypatch):
    """Пустое поле редактора = «LUT не задан»: ключа в профиле быть не должно."""
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(tmp_path))
    key, _ = speakers.save("Пустой LUT", {"lut": {"1": "  ", "2": "C:/lut/cam2.cube"}})
    assert speakers.load(key)["lut"] == {"2": "C:/lut/cam2.cube"}
    key2, _ = speakers.save("Совсем пусто", {"lut": {"1": ""}})
    assert "lut" not in speakers.load(key2)


@pytest.mark.parametrize("bad", [
    "C:/lut/cam1.cube",                # не объект
    {"0": "C:/lut/cam1.cube"},         # камеры нумеруются с 1
    {"-1": "C:/lut/cam1.cube"},
    {"камера": "C:/lut/cam1.cube"},    # ключ — не номер
    {"1": "C:/lut/cam1.txt"},          # не таблица
    {"1": "C:/lut/cam1"},              # расширения нет
    {"1": 5},                          # путь не строка
    {"1": None},
])
def test_битый_lut_в_профиль_не_пишется(tmp_path, monkeypatch, bad):
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        speakers.save("Битый LUT", {"lut": bad})


# --------------------------------------------------------------------------- #
# Текстура LUT в WebGL2: флип кадра не должен ломать texImage3D
# --------------------------------------------------------------------------- #
JS_LUT = os.path.join(ROOT, "static", "app", "86-lut.js")


def _js_block(src: str, start: int) -> str:
    """Текст от скобки `src[start]` до парной закрывающей — вложенность по счёту."""
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError("не нашлась закрывающая скобка")


def _js_func(src: str, name: str) -> str:
    """Тело функции `name` из 86-lut.js — от объявления до парной скобки."""
    m = re.search(r"function\s+%s\s*\(" % re.escape(name), src)
    assert m is not None, f"в 86-lut.js не нашлась функция {name}"
    return _js_block(src, src.index("{", m.end()))


def test_загрузка_3d_текстуры_идёт_с_выключенным_флипом():
    """В lutTexImage флип выключается перед texImage3D и возвращается в finally.

    Дефект (проверен в WebGL2 браузера): `lutGL` ставит UNPACK_FLIP_Y_WEBGL=true ради
    кадра видео, а при нём WebGL2 отбивает загрузку 3D-текстуры из ArrayBufferView —
    INVALID_OPERATION на КАЖДЫЙ формат. `lutTexImage` возвращал 0, LUT-текстура
    оставалась пустой, и превью с LUT было чёрным. В браузере это не поймать, поэтому
    стережём порядок по тексту: выключить → texImage3D → вернуть (обязательно в
    finally, иначе исключение оставит флип выключенным, и кадр видео поедет
    перевёрнутым). Мутация «убрать выключение флипа» валит этот тест.
    """
    src = open(JS_LUT, encoding="utf-8").read()
    flat = re.sub(r"\s+", "", _js_func(src, "lutTexImage"))   # форматирование не в счёт

    off = re.search(r"gl\.pixelStorei\(gl\.UNPACK_FLIP_Y_WEBGL,false\)", flat)
    assert off, ("в lutTexImage не выключен UNPACK_FLIP_Y_WEBGL перед texImage3D: "
                 "WebGL2 отобьёт загрузку 3D-текстуры и LUT не поднимется")
    assert re.search(r"gl\.pixelStorei\(gl\.UNPACK_PREMULTIPLY_ALPHA_WEBGL,false\)", flat), \
        "рядом с флипом не выключен UNPACK_PREMULTIPLY_ALPHA_WEBGL"

    image = re.search(r"gl\.texImage3D\(", flat)
    assert image is not None, "в lutTexImage пропал вызов texImage3D"
    assert off.start() < image.start(), "флип выключается после texImage3D, а не перед ним"

    fin = re.search(r"finally\{", flat)
    assert fin is not None, ("флип возвращается не в finally: исключение оставит его "
                             "выключенным, и кадр видео уедет перевёрнутым")
    assert image.start() < fin.start(), "finally стоит до загрузки таблицы — возвращать нечего"
    assert re.search(r"gl\.pixelStorei\(gl\.UNPACK_FLIP_Y_WEBGL,true\)",
                     _js_block(flat, fin.end() - 1)), \
        "в finally нет возврата флипа — кадр видео будет грузиться неперевёрнутым"
