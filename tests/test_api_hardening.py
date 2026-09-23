# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Дешёвая защита API: пять дверей — по тесту на каждую.

1. `POST /api/clip_delete` без `dry` — сухой прогон: файлы нарезки остаются на диске,
   в ответе список того, что БЫЛО БЫ удалено. Удаление — только при явном `dry: false`
   (до правки отсутствие параметра означало «удалить»).
2. `ai_config.json` после сохранения — режим 0600 (POSIX): в файле ключи провайдеров,
   а SECURITY.md обещает защиту правами ОС. На Windows тест пропускается: прав
   user/group там нет, `os.chmod` управляет только битом «только чтение».
3. rclone: путь назначения, начинающийся с «-», — отказ; в команде `--` стоит перед
   позиционными путями. Иначе значение из запроса управляло бы разбором аргументов,
   а ведущий дефис маска id из ссылки допускает.
4. `/api/media?nobg=1`: путь, вернувшийся из `insertlib.nobg_path`, проходит ТЕ ЖЕ
   проверки (расширение по realpath, `_never_serve`, isfile), что и присланный.
5. Побочные GET (`/api/pick*`, `/api/waveform`) закрыты чужому origin так же, как POST.
   Вариант с `Sec-Fetch-Site: cross-site` проходил и до правки (этот заголовок
   проверялся у любого метода) — на main падает вариант с одним `Origin`.

Запуск:  python -m pytest tests/test_api_hardening.py -q
"""
import os
import stat
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

import api  # noqa: E402

# Хост настоящего локального клиента: без него запрос отбивает защита от DNS rebinding
# (её тесты — в test_api_security.py), и до проверок этого файла дело не дойдёт.
H = {"Host": "127.0.0.1:5001"}

FILE_URL = "https://drive.google.com/file/d/1AbC_dEfGhI1234567890/view?usp=sharing"
# Маска id в parse_gdrive_link ([A-Za-z0-9_-]{8,}) ведущий дефис допускает: без `--`
# такой id rclone разобрал бы как набор опций.
DASH_ID_URL = "https://drive.google.com/file/d/-AbC_dEfGhI1234567890/view"
FOLDER_URL = "https://drive.google.com/drive/folders/1XyfxxxxxxxxxxxxxxxxxxxxxxxxxKHCh"

# GET-роуты с побочным действием (диалог Tk или запись кэша волны рядом с файлом).
SIDE_EFFECT_GETS = ("/api/pickdir", "/api/pickmedia", "/api/pickfiles",
                    "/api/pickone", "/api/pickaudio", "/api/waveform?path=x")


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def no_dialog(monkeypatch):
    """Диалог выбора файла — заглушка. Проверяется хук ДО роута, а не Tk: если бы
    запрос доходил до /api/pickdir, здесь было бы видно, что диалог открылся."""
    calls = []
    monkeypatch.setattr("api.files._native_pick",
                        lambda call: calls.append(call) or "")
    return calls


# --------------------------------------------------------------------------- #
# 1. clip_delete: без dry ничего не удаляется
# --------------------------------------------------------------------------- #
def _clip(tmp_path, stem="01_clip"):
    """Клип как на диске: XML, project.json и пара сайдкаров."""
    import json
    out = tmp_path / "out"
    out.mkdir()
    xml = out / f"{stem}.xml"
    xml.write_text("<xml/>", encoding="utf-8")
    (out / f"{stem}.project.json").write_text(
        json.dumps({"cams": [str(tmp_path / "cam" / "C1437.MP4")]}), encoding="utf-8")
    (out / f"{stem}.cuts.json").write_text("{}", encoding="utf-8")
    (out / f"{stem}.srt").write_text("1\n", encoding="utf-8")
    return xml, sorted(out.glob(f"{stem}.*"))


def test_clip_delete_без_dry_только_показывает_список(client, tmp_path):
    """Запрос без `dry` — сухой прогон: и файлы на месте, и список в ответе."""
    xml, files = _clip(tmp_path)
    r = client.post("/api/clip_delete", json={"xml": str(xml)}, headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True
    assert {os.path.basename(f["path"]) for f in d["files"]} == {f.name for f in files}
    assert d["bytes"] == sum(f.stat().st_size for f in files)
    for f in files:
        assert f.exists(), f"{f.name} удалён без явного dry: false"


def test_clip_delete_с_явным_dry_false_удаляет(client, tmp_path):
    """Интерфейс удаление не потерял: он передаёт dry: false явно
    (static/app/40-queue.js) и обязан получать прежнее поведение."""
    xml, files = _clip(tmp_path)
    r = client.post("/api/clip_delete", json={"xml": str(xml), "dry": False}, headers=H)
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
    for f in files:
        assert not f.exists(), f"{f.name} не удалён при dry: false"


# --------------------------------------------------------------------------- #
# 2. ai_config.json: ключи читает только владелец
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(os.name != "posix",
                    reason="на Windows прав user/group нет: os.chmod меняет только "
                           "бит «только чтение», режим 0600 не выставляется")
def test_ai_config_после_сохранения_0600(client):
    from core.aicut import config as aicut_config

    r = client.post("/api/ai_config", json={"action": "set_active", "name": "LM Studio"},
                    headers=H)
    assert r.status_code == 200 and r.get_json()["ok"] is True
    path = aicut_config.AI_CONFIG_PATH
    assert os.path.isfile(path), "сохранение не создало конфиг"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600, \
        f"конфиг с ключами доступен всем: {oct(stat.S_IMODE(os.stat(path).st_mode))}"


# --------------------------------------------------------------------------- #
# 3. rclone: значение из запроса не становится опцией
# --------------------------------------------------------------------------- #
@pytest.fixture
def rclone_conf(tmp_path, monkeypatch):
    conf = tmp_path / "rclone.conf"
    conf.write_text("[gdrive]\ntype = drive\n", encoding="utf-8")
    monkeypatch.setenv("REELSI_RCLONE_CONF", str(conf))
    return str(conf)


@pytest.mark.parametrize("dest", ["--config=/tmp/чужой.conf", "--dry-run", "-x"])
def test_rclone_назначение_с_дефисом_это_отказ(rclone_conf, dest):
    """Путь назначения приходит из тела запроса: `--config=…` увёл бы скачивание на
    чужие токены, `--dry-run` сделал бы вид, что скачали."""
    from core.rclone import rclone_cmd
    with pytest.raises(ValueError):
        rclone_cmd("gdrive", FILE_URL, dest)


def test_rclone_позиционные_пути_идут_после_двойного_дефиса(rclone_conf, tmp_path):
    """`--` перед первым позиционным аргументом: после него для rclone всё — значения.
    Проверяем хвост команды целиком, включая id с ведущим дефисом (маска его допускает),
    и путь назначения."""
    from core.rclone import rclone_cmd

    dest = str(tmp_path)
    os_dest = dest.replace("\\", "/").rstrip("/")
    cases = [
        (FILE_URL, ["copyid", "gdrive:", "1AbC_dEfGhI1234567890", os_dest + "/"]),
        (DASH_ID_URL, ["copyid", "gdrive:", "-AbC_dEfGhI1234567890", os_dest + "/"]),
        (FOLDER_URL, ["gdrive:", dest]),
    ]
    for url, tail in cases:
        args = rclone_cmd("gdrive", url, dest)
        assert args[0] == "rclone"
        assert args.count("--") == 1, f"{url}: ожидался ровно один разделитель --"
        dash = args.index("--")
        assert args[dash + 1:] == tail, f"{url}: позиционные пути должны идти после --"


# --------------------------------------------------------------------------- #
# 4. /api/media: подмена nobg проверяется как исходный путь
# --------------------------------------------------------------------------- #
def test_media_nobg_подмена_проходит_те_же_проверки(client, tmp_path, monkeypatch):
    """`nobg_path` возвращает кэш рядом с исходником, и до правки он отдавался вообще
    без проверок: расширение, realpath и `_never_serve` относились только к присланному
    пути. Теперь итоговый путь проверяется тем же набором, что и присланный."""
    from core import insertlib

    png = tmp_path / "photo.png"
    png.write_bytes(b"\x89PNG-original")
    txt = tmp_path / "notes.txt"
    txt.write_text("секрет", encoding="utf-8")
    secret = tmp_path / "ai_config.json"
    secret.write_text('{"profiles": {"x": {"api_key": "sk-СЕКРЕТ"}}}', encoding="utf-8")

    monkeypatch.setattr(insertlib, "nobg_path", lambda media, emit=None: str(txt))
    r = client.get(f"/api/media?path={png}&nobg=1", headers=H)
    assert r.status_code == 403, "подмена на недопустимое расширение отдана"

    monkeypatch.setattr(insertlib, "nobg_path", lambda media, emit=None: str(secret))
    r = client.get(f"/api/media?path={png}&nobg=1", headers=H)
    assert r.status_code == 403, "подмена на файл с ключами отдана"
    assert b"sk-" not in r.data

    # Обычный случай (кэш рядом с исходником) не сломан
    cache = tmp_path / "photo.png.nobg.png"
    cache.write_bytes(b"\x89PNG-nobg")
    monkeypatch.setattr(insertlib, "nobg_path", lambda media, emit=None: str(cache))
    r = client.get(f"/api/media?path={png}&nobg=1", headers=H)
    assert r.status_code == 200
    assert r.data == b"\x89PNG-nobg"


# --------------------------------------------------------------------------- #
# 5. Побочные GET: чужой origin — 403, как у POST
# --------------------------------------------------------------------------- #
def test_побочные_get_с_чужого_сайта_закрыты(client, no_dialog):
    for path in SIDE_EFFECT_GETS:
        r = client.get(path, headers={**H, "Sec-Fetch-Site": "cross-site"})
        assert r.status_code == 403, f"{path} доступен чужому сайту"
        assert (r.get_json(silent=True) or {}).get("err") == "forbidden_origin"

        r = client.get(path, headers={**H, "Origin": "http://evil.example"})
        assert r.status_code == 403, f"{path} доступен чужому origin без Sec-Fetch-Site"
        assert (r.get_json(silent=True) or {}).get("err") == "forbidden_origin"
    assert no_dialog == [], "диалог выбора файла успел открыться до проверки"


def test_побочные_get_со_своего_сайта_работают(client, no_dialog):
    """Наш интерфейс (same-origin), закладка (none) и свой Origin — как раньше."""
    for headers in ({"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Site": "none"},
                    {"Origin": "http://127.0.0.1:5001"}):
        r = client.get("/api/pickdir", headers={**H, **headers})
        assert r.status_code == 200, f"свой клиент заблокирован: {headers}"
    assert len(no_dialog) == 3, "диалог не дошёл до заглушки"


def test_обычный_get_чужому_origin_по_прежнему_не_запрещён(client):
    """Проверка добавлена ТОЛЬКО побочным GET: статусы и списки чужой странице ничего
    не дают (ответ ей не прочитать), а сломать их легко."""
    r = client.get("/api/status", headers={**H, "Origin": "http://evil.example"})
    assert r.status_code != 403
