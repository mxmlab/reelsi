# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Контракты пяти роутов api/inserts.py — база вставок (круг 7):

* `GET  /api/insertlib_info`   — что в индексе (файл, папки, эмбеддер);
* `POST /api/insertlib_scan`   — построить/обновить индекс по папкам;
* `POST /api/insertlib_reject` — «не предлагать этот файл под этот запрос»;
* `POST /api/insertlib_import` — перенести медиа в папку базы (пишущий!);
* `POST /api/insertlib_items`  — список для модалки 📚.

Изоляция — жёсткая: боевой `insertlib.json` (личный файл, ~1500 записей) не читается и
не пишется. `insertlib.INDEX_PATH` подменяется на файл в tmp_path, кэш индекса — на
пустой, `_seed_index()` — на пустышку: иначе `_save()` затёр бы рабочий индекс, а
`_seed_index()` снял бы с него копию во временный. Эмбеддер (LM Studio) подменён на
«нет» — сети и моделей в тестах нет.

Запуск (только новые файлы): py -3.10 -m pytest tests/test_routes_*.py -q -p no:cacheprovider
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

import api  # noqa: E402

H = {"Host": "127.0.0.1:5001"}

EMPTY_CACHE = {"mtime": 0, "data": None, "mat": None, "mat_items": None,
               "df": None, "cand_words": None, "N": 0}


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def index(tmp_path, monkeypatch):
    """Свой индекс вставок + пустой кэш: боевой файл не читается и не пишется."""
    from core import insertlib
    monkeypatch.setattr(insertlib, "INDEX_PATH", str(tmp_path / "insertlib.json"))
    monkeypatch.setattr(insertlib, "_CACHE", dict(EMPTY_CACHE))
    # _seed_index копирует рабочий insertlib.json в ещё не созданный индекс профиля: в
    # чекауте владельца (там файл есть) тест «пустого индекса» видел 2576 записей, а в
    # свежей рабочей копии и в CI файла нет — и тест был зелёным. Приёмка круга 7.
    monkeypatch.setattr(insertlib, "_seed_index", lambda: None)
    monkeypatch.setattr(insertlib, "_emb_model", lambda: None)   # без LM Studio
    return str(tmp_path / "insertlib.json")


def _write_index(path, items, dirs=()):
    from core import insertlib
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"dirs": list(dirs), "emb_model": "", "emb_tag": insertlib.EMB_TAG,
                   "items": items}, f, ensure_ascii=False)


def _item(path, kind="photo", used=0, desc="", **extra):
    it = {"path": str(path), "name": os.path.basename(str(path)), "type": kind,
          "used": used, "desc": desc, "desc_src": "user" if desc else "name", "emb": None}
    it.update(extra)
    return it


def _read_index(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _get(client, url):
    r = client.get(url, headers=H)
    assert r.status_code == 200, f"{url}: {r.status_code} {r.data[:200]!r}"
    return r.get_json()


def _post(client, url, payload):
    r = client.post(url, json=payload, headers=H)
    assert r.status_code == 200, f"{url}: {r.status_code} {r.data[:200]!r}"
    return r.get_json()


# --------------------------------------------------------------------------- #
# /api/insertlib_info
# --------------------------------------------------------------------------- #
def test_insertlib_info_empty_and_filled(client, index, tmp_path):
    """Индекс пуст/собран/рваный — всегда JSON: count, dirs, emb_model, built."""
    fresh = _get(client, "/api/insertlib_info")
    assert fresh == {"ok": True, "count": 0, "dirs": [], "emb_model": "", "built": False}

    _write_index(index, [_item(tmp_path / "a.png"), _item(tmp_path / "b.mp4", "video")],
                 dirs=[str(tmp_path)])
    d = _get(client, "/api/insertlib_info")
    assert d["ok"] is True and d["count"] == 2 and d["built"] is True
    assert d["dirs"] == [str(tmp_path)] and d["emb_model"] == ""

    with open(index, "w", encoding="utf-8") as f:
        f.write("{рваный json")
    # Тест моделирует внешнюю запись (мимо _save); кэш индекса ключуется по mtime,
    # поэтому сдвигаем mtime вперёд, чтобы тест не зависел от тика таймера ФС.
    st = os.stat(index)
    os.utime(index, (st.st_atime, st.st_mtime + 2))
    d = _get(client, "/api/insertlib_info")              # не 500: база просто «не собрана»
    assert d["ok"] is True and d["built"] is False and d["count"] == 0


# --------------------------------------------------------------------------- #
# /api/insertlib_scan
# --------------------------------------------------------------------------- #
def test_insertlib_scan_builds_index_and_keeps_sources(client, index, tmp_path):
    """Скан пишет индекс по указанным папкам, исходники не трогает и не уносит."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "cat.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (src / "clip.mp4").write_bytes(b"\x00" * 32)
    (src / "notes.txt").write_text("не медиа", encoding="utf-8")
    other = tmp_path / "other"
    other.mkdir()
    (other / "сосед.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    d = _post(client, "/api/insertlib_scan", {"dirs": [str(src)]})
    assert d["ok"] is True, d
    assert d["count"] == 2 and d["emb"] is False
    assert isinstance(d["log"], list) and all(isinstance(x, str) for x in d["log"])
    assert any(str(src) in line for line in d["log"])

    saved = _read_index(index)
    assert saved["dirs"] == [str(src)]
    got = {it["name"]: it["type"] for it in saved["items"]}
    assert got == {"cat.png": "photo", "clip.mp4": "video"}   # .txt не медиа
    assert all(it["used"] == 0 for it in saved["items"])
    # соседнюю папку не сканировали, исходники на месте
    assert not any("сосед" in it["name"] for it in saved["items"])
    assert (src / "cat.png").is_file() and (src / "clip.mp4").is_file()


def test_insertlib_scan_keeps_previous_index_entries(client, index, tmp_path):
    """Скан не «съедает» прежние записи: чего нет на диске — помечается gone, не удаляется."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "cat.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    gone = tmp_path / "src" / "пропал.png"
    _write_index(index, [_item(src / "cat.png", used=3, desc="кошка"),
                         _item(gone, used=1, desc="был да сплыл")], dirs=[str(src)])

    d = _post(client, "/api/insertlib_scan", {"dirs": [str(src)]})
    assert d["ok"] is True and d["count"] == 2
    saved = {it["name"]: it for it in _read_index(index)["items"]}
    assert saved["cat.png"]["used"] == 3 and saved["cat.png"]["desc"] == "кошка"
    assert saved["пропал.png"].get("gone"), "пропавший файл обязан помечаться gone"


@pytest.mark.parametrize("payload", [{}, {"dirs": []}, {"dirs": [""]}, {"dirs": ["   "]}])
def test_insertlib_scan_needs_folders(client, index, payload):
    d = _post(client, "/api/insertlib_scan", payload)
    assert d.get("ok") is not True and d["err"] == "need_folders"
    assert d["log"] == []


def test_insertlib_scan_missing_folder_is_a_warning(client, index, tmp_path):
    """Папки нет — это предупреждение в логе, а не падение: остальные обрабатываются."""
    d = _post(client, "/api/insertlib_scan", {"dirs": [str(tmp_path / "нет-такой")]})
    assert d["ok"] is True and d["count"] == 0
    assert any("нет папки" in line for line in d["log"]), d["log"]


# --------------------------------------------------------------------------- #
# /api/insertlib_reject
# --------------------------------------------------------------------------- #
def test_insertlib_reject_marks_pair_and_untouches_files(client, index, tmp_path):
    """Брак ставится ПАРЕ файл+запрос; сам файл и соседняя запись не меняются."""
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    a.write_bytes(b"a-png")
    b.write_bytes(b"b-png")
    _write_index(index, [_item(a, used=2, desc="кошка"), _item(b, used=1, desc="пёс")])

    d = _post(client, "/api/insertlib_reject", {"path": str(a), "query": "  Кошка   на окне "})
    assert d["ok"] is True
    saved = {it["name"]: it for it in _read_index(index)["items"]}
    assert saved["a.png"]["rej"] == ["кошка на окне"]      # _norm_q: lowercase + пробелы
    assert "rej" not in saved["b.png"]
    assert a.read_bytes() == b"a-png"                      # файлы базы неприкосновенны

    # повторный вызов с on=False снимает брак
    assert _post(client, "/api/insertlib_reject",
                 {"path": str(a), "query": "кошка на окне", "on": False})["ok"] is True
    assert "rej" not in _read_index(index)["items"][0]
    # повтор брака не плодит дубликат
    _post(client, "/api/insertlib_reject", {"path": str(a), "query": "кошка на окне"})
    _post(client, "/api/insertlib_reject", {"path": str(a), "query": "кошка на окне"})
    saved = {it["name"]: it for it in _read_index(index)["items"]}
    assert saved["a.png"]["rej"] == ["кошка на окне"]


@pytest.mark.parametrize("payload", [{}, {"path": "a.png"}, {"query": "кошка"},
                                     {"path": "  ", "query": "кошка"}])
def test_insertlib_reject_bad_input(client, index, payload):
    d = _post(client, "/api/insertlib_reject", payload)
    assert d.get("ok") is not True and d["err"] == "need_path_query"


def test_insertlib_reject_unknown_path_is_false_not_error(client, index, tmp_path):
    """Файла нет в базе — `ok: false` без ошибки: это не сбой, а «браковать нечего»."""
    _write_index(index, [_item(tmp_path / "a.png")])
    r = client.post("/api/insertlib_reject",
                    json={"path": str(tmp_path / "чужой.png"), "query": "кошка"}, headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is False and "error" not in d


# --------------------------------------------------------------------------- #
# /api/insertlib_import
# --------------------------------------------------------------------------- #
def test_insertlib_import_reports_success(client, index, tmp_path):
    """Успешный перенос приходит как ok:true с count, dest и логом-списком строк. До HY
    ответ падал TypeError (ключ `log` из import_media спорил с логом роута) и клиент видел
    ошибку на уже выполненном переносе."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "cat.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    dest = tmp_path / "base"

    d = _post(client, "/api/insertlib_import", {"dirs": [str(src)], "dest": str(dest)})
    assert d.get("ok") is True, d
    assert d["count"] == 1 and d["dest"] == str(dest)
    assert isinstance(d["log"], list) and all(isinstance(x, str) for x in d["log"])
    assert "log_file" in d and os.path.isfile(d["log_file"])


def test_insertlib_import_moves_into_base_and_updates_index(client, index, tmp_path):
    """Побочные эффекты переноса (они выполняются ДО ответа, см. тест выше):
    файлы уезжают в photos/videos, лог переносов и пути в индексе обновлены."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "cat.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (src / "clip.mp4").write_bytes(b"\x00" * 16)
    dest = tmp_path / "base"
    _write_index(index, [_item(src / "cat.png", used=4, desc="кошка")], dirs=[str(src)])

    _post(client, "/api/insertlib_import", {"dirs": [str(src)], "dest": str(dest)})

    # перенесено, а не скопировано; исходники пусты
    assert (dest / "photos" / "cat.png").is_file()
    assert (dest / "videos" / "clip.mp4").is_file()
    assert not (src / "cat.png").exists() and not (src / "clip.mp4").exists()

    # лог переносов old -> new пишется рядом с базой
    log = json.load(open(dest / "_import_log.json", encoding="utf-8"))
    assert os.path.normcase(log[str(src / "cat.png")]) == \
        os.path.normcase(str(dest / "photos" / "cat.png"))

    # в индексе путь обновлён, а used/desc ПЕРЕЖИЛИ перенос (иначе база «обнулилась» бы)
    items = {it["name"]: it for it in _read_index(index)["items"]}
    assert os.path.normcase(items["cat.png"]["path"]) == \
        os.path.normcase(str(dest / "photos" / "cat.png"))
    assert items["cat.png"]["used"] == 4 and items["cat.png"]["desc"] == "кошка"


def test_insertlib_import_since_skips_old_files(client, index, tmp_path):
    """`since` (дата в будущем) — переноса нет, файлы остаются на месте."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "cat.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    dest = tmp_path / "base"

    _post(client, "/api/insertlib_import",
          {"dirs": [str(src)], "dest": str(dest), "since": "2099-01-01"})
    assert (src / "cat.png").is_file()
    assert not (dest / "photos" / "cat.png").exists()
    assert not (dest / "_import_log.json").exists()      # переносить было нечего


@pytest.mark.parametrize("payload", [{}, {"dirs": ["x"]}, {"dest": "x"},
                                     {"dirs": [], "dest": "x"}])
def test_insertlib_import_needs_both(client, index, payload):
    d = _post(client, "/api/insertlib_import", payload)
    assert d.get("ok") is not True and d["err"] == "need_src_and_db"
    assert d["log"] == []


def test_insertlib_import_bad_date_is_json_error(client, index, tmp_path):
    """Кривая дата — umsg с логом, а не 500 и не молчаливый перенос всего подряд."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "cat.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    d = _post(client, "/api/insertlib_import",
              {"dirs": [str(src)], "dest": str(tmp_path / "base"), "since": "вчера"})
    assert d.get("ok") is not True and d["err"] == "insertlib_import_failed"
    assert d["log"] == [] and (src / "cat.png").is_file()


# --------------------------------------------------------------------------- #
# /api/insertlib_items
# --------------------------------------------------------------------------- #
def test_insertlib_items_contract(client, index, tmp_path):
    """Список для модалки: сортировка по «использовано», фильтр и срез."""
    _write_index(index, [
        _item(tmp_path / "one.png", used=1, desc="кошка"),
        _item(tmp_path / "two.png", used=5, desc="собака"),
        _item(tmp_path / "three.mp4", "video", used=5, desc="город"),
    ])

    d = _post(client, "/api/insertlib_items", {})
    assert d["ok"] is True and d["total"] == 3
    assert [it["name"] for it in d["items"]] == ["three.mp4", "two.png", "one.png"]
    keys = {"path", "name", "type", "used", "desc", "desc_src", "ru", "vis", "look"}
    assert all(keys <= set(it) for it in d["items"])
    assert d["items"][0]["type"] == "video" and d["items"][0]["used"] == 5

    assert _post(client, "/api/insertlib_items", {"q": "кош"})["total"] == 1
    limited = _post(client, "/api/insertlib_items", {"limit": 1, "offset": 1})
    assert limited["total"] == 3 and [it["name"] for it in limited["items"]] == ["two.png"]
    assert _post(client, "/api/insertlib_items", {"q": "нет-такого"})["items"] == []


def test_insertlib_items_empty_index(client, index):
    d = _post(client, "/api/insertlib_items", {})
    assert d == {"ok": True, "total": 0, "items": []}


def test_insertlib_items_bad_type_is_json_error(client, index):
    """`offset`/`limit` не числа — umsg, а не 500 (как ?since= в HU)."""
    d = _post(client, "/api/insertlib_items", {"offset": "вчера"})
    assert d.get("ok") is not True and d["err"] == "insertlib_items_failed"
    d = _post(client, "/api/insertlib_items", {"limit": {"a": 1}})
    assert d.get("ok") is not True and d["err"] == "insertlib_items_failed"


# --------------------------------------------------------------------------- #
# Чужой тип поля
# --------------------------------------------------------------------------- #
def test_wrong_type_fields_in_inserts_routes(client, index):
    bad = []
    for url, payload in (("/api/insertlib_scan", {"dirs": [123]}),
                         ("/api/insertlib_import", {"dirs": [123], "dest": "x"}),
                         ("/api/insertlib_reject", {"path": 123, "query": "к"})):
        r = client.post(url, json=payload, headers=H)
        if r.status_code != 200 or r.get_json().get("ok") is True or not r.get_json().get("err"):
            bad.append((url, r.status_code, r.get_json()))
    assert bad == [], bad
