# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Контракты роутов api/presets.py, которые до круга 7 не были покрыты:

* `POST /api/savestyle`    — сохранить пользовательский пресет стиля (пишущий!);
* `GET/POST /api/censor_words` — списки цензуры субтитров (пишущий!);
* `POST /api/delstyle`     — удалить пользовательский пресет (деструктивный!).

Изоляция: `styles/` и `speakers/` — папки БЕЗ переменной окружения, поэтому путь
подменяется в модуле-владельце (`core.styles.STYLE_DIR`) на папку в tmp_path; списки
цензуры — через `censor.USER_PATHS` (свои файлы в tmp_path). Боевые `styles/*.json`,
`data/badwords.txt`, `data/okwords.txt` и `*.user.txt` не читаются на запись и не
меняются — это проверяется прямо в тестах (mtime/размер/содержимое).

Отдельно стерегутся выходы из папки стилей: имя с `../`, абсолютным путём или «..»
обязано остаться ВНУТРИ `styles/` (имя санируется в `styles.save`).

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


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def style_dir(tmp_path, monkeypatch):
    """Своя папка стилей: боевые `styles/*.json` (личные) не читаются и не пишутся."""
    from core import styles
    d = tmp_path / "styles"
    d.mkdir()
    monkeypatch.setattr(styles, "STYLE_DIR", str(d))
    return d


@pytest.fixture
def censor_files(tmp_path, monkeypatch):
    """Свои файлы списков цензуры + чистый кэш: поставочные data/*.txt не правятся."""
    from core import censor
    bad, ok = tmp_path / "badwords.user.txt", tmp_path / "okwords.user.txt"
    monkeypatch.setitem(censor.USER_PATHS, "bad", str(bad))
    monkeypatch.setitem(censor.USER_PATHS, "ok", str(ok))
    monkeypatch.setattr(censor, "_cache", {"bad": (None, None, []), "ok": (None, None, [])})
    return bad, ok


def _post(client, url, payload):
    r = client.post(url, json=payload, headers=H)
    assert r.status_code == 200, f"{url}: {r.status_code} {r.data[:200]!r}"
    return r.get_json()


def _stat(path):
    st = os.stat(path)
    return (st.st_size, st.st_mtime_ns)


# --------------------------------------------------------------------------- #
# /api/savestyle
# --------------------------------------------------------------------------- #
def test_savestyle_writes_template_and_keeps_neighbours(client, style_dir):
    """Пресет ложится в папку стилей с label=имени; соседние файлы не переписаны."""
    from core import styles
    neighbour = style_dir / "Neighbor.json"
    neighbour.write_text('{"label": "Neighbor", "font": "X"}', encoding="utf-8")
    before = neighbour.read_bytes()

    d = _post(client, "/api/savestyle",
              {"name": "Мой стиль", "data": {"font": "Geologica-Regular", "roto": False}})
    assert d["ok"] is True, d
    assert d["key"] == "Мой стиль"
    assert os.path.realpath(d["path"]) == os.path.realpath(str(style_dir / "Мой стиль.json"))
    assert os.path.isfile(d["path"])

    saved = json.load(open(d["path"], encoding="utf-8"))
    assert saved["label"] == "Мой стиль"             # подпись = введённому имени
    assert saved["font"] == "Geologica-Regular" and saved["roto"] is False
    assert saved["layer_order"]                       # миграция дописала порядок слоёв

    # сосед цел побайтово, и правка видна в общем списке стилей
    assert neighbour.read_bytes() == before
    assert "Мой стиль" in styles.all_styles()


@pytest.mark.usefixtures("case_insensitive_fs")
def test_savestyle_overwrites_same_name(client, style_dir):
    """Повторное сохранение того же имени не плодит файлов: правка ложится в тот же файл.

    Регистр имени берётся с диска (ФС Windows регистронезависима), поэтому ключ ответа —
    реальный стем файла, а не тот, что прислал клиент: иначе пресет «пропал бы» из
    селектора, который строит ключи по файлам (`styles.all_styles`)."""
    first = _post(client, "/api/savestyle", {"name": "Draft", "data": {"font": "A"}})
    assert first["ok"] is True
    assert os.listdir(str(style_dir)) == ["Draft.json"]
    assert first["key"] == "Draft"

    second = _post(client, "/api/savestyle", {"name": "draft", "data": {"font": "B"}})
    assert second["ok"] is True
    names = os.listdir(str(style_dir))
    assert len(names) == 1 and names[0].lower() == "draft.json", names
    # ключ — реальный стем с диска, файл тот же самый, содержимое обновлено
    assert second["key"] == os.path.splitext(names[0])[0]
    assert os.path.normcase(second["path"]) == os.path.normcase(first["path"])
    assert json.load(open(second["path"], encoding="utf-8"))["font"] == "B"


@pytest.mark.parametrize("name", ["../evil", "..\\evil", "/evil", "C:\\evil", "..", "..."])
def test_savestyle_name_cannot_escape_styles_dir(client, style_dir, tmp_path, name):
    """Имя с `../`, абсолютным путём или «..» остаётся ВНУТРИ папки стилей.

    `styles.save` чистит имя до `[A-Za-z0-9-_ ]`, поэтому «../evil» становится
    «evil.json» в самой папке, а не файлом рядом с ней. Если санитайзер когда-нибудь
    ослабят, этот тест покраснеет: `os.path.join(styles, "/evil.json")` на Windows
    вернул бы `C:/evil.json` — то есть запись в корень диска.
    """
    d = _post(client, "/api/savestyle", {"name": name, "data": {"font": "X"}})
    assert d["ok"] is True, d
    root = os.path.realpath(str(style_dir))
    assert os.path.realpath(d["path"]).startswith(root + os.sep), \
        f"файл стиля ушёл за папку стилей: {d['path']}"
    assert os.path.isfile(d["path"])
    assert os.path.dirname(os.path.realpath(d["path"])) == root
    # ничего не появилось рядом с папкой стилей
    assert not (tmp_path / "evil.json").exists()


@pytest.mark.parametrize("payload", [{}, {"name": ""}, {"name": "   "}])
def test_savestyle_needs_name(client, style_dir, payload):
    d = _post(client, "/api/savestyle", payload)
    assert d.get("ok") is not True and d["err"] == "need_template_name" and d["error"]
    assert os.listdir(str(style_dir)) == []


@pytest.mark.parametrize("data", ["строка", [1, 2], 5])
def test_savestyle_bad_data_is_json_error(client, style_dir, data):
    """`data` не словарь — umsg, а не 500 и не файл-обрубок в папке стилей."""
    d = _post(client, "/api/savestyle", {"name": "Кривой", "data": data})
    assert d.get("ok") is not True and d["err"] == "styles_save_failed"
    assert os.listdir(str(style_dir)) == []


# --------------------------------------------------------------------------- #
# /api/censor_words
# --------------------------------------------------------------------------- #
def test_censor_words_get_contract(client, censor_files):
    """GET отдаёт оба списка ключом `lists` (имя `ok` не должно спорить с флагом ok)."""
    r = client.get("/api/censor_words", headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True and set(d["lists"]) == {"bad", "ok"}
    for kind in ("bad", "ok"):
        item = d["lists"][kind]
        assert set(item) == {"text", "custom", "count"}
        assert isinstance(item["text"], str) and item["count"] > 0
        assert item["custom"] is False               # свой список ещё не заведён
    # поставочный список плохих слов не пустой — иначе цензура просто не работает
    assert d["lists"]["bad"]["count"] == len(
        [ln for ln in d["lists"]["bad"]["text"].splitlines()
         if ln.split("#", 1)[0].strip()])


def test_censor_words_post_writes_own_list_only(client, censor_files, tmp_path):
    """POST пишет СВОЙ файл (`*.user.txt`), поставочные data/*.txt не трогает."""
    bad_user, ok_user = censor_files
    shipped = os.path.join(ROOT, "data", "badwords.txt")
    shipped_before = _stat(shipped)
    root_bad = os.path.join(ROOT, "badwords.user.txt")
    root_ok = os.path.join(ROOT, "okwords.user.txt")
    root_before = [(p, os.path.exists(p), _stat(p) if os.path.exists(p) else None)
                   for p in (root_bad, root_ok)]

    d = _post(client, "/api/censor_words", {"bad": "# комментарий\nубива\nНаркотик\n\n"})
    assert d["ok"] is True
    assert d["lists"]["bad"]["custom"] is True
    assert d["lists"]["bad"]["count"] == 2                  # комментарий и пустые строки — мимо
    assert d["lists"]["bad"]["text"] == "# комментарий\nубива\nНаркотик\n"
    assert bad_user.is_file() and "убива" in bad_user.read_text(encoding="utf-8")
    assert d["lists"]["ok"]["custom"] is False              # второй список не тронут
    assert not ok_user.exists()

    # цензура реально начала работать по новому списку, а поставочный файл цел
    from core import censor
    assert censor.is_bad("Убиваю") is True
    assert _stat(shipped) == shipped_before, "правка из UI уехала в поставочный data/badwords.txt"
    for p, existed, st in root_before:
        assert os.path.exists(p) == existed, f"тест завёл боевой {p}"
        if existed:
            assert _stat(p) == st, f"тест переписал боевой {p}"


def test_censor_words_empty_list_is_empty_not_reset(client, censor_files):
    """Пустой текст = «ничего не цензурим» (custom=True, count=0), а не «вернуть как было»."""
    d = _post(client, "/api/censor_words", {"bad": ""})
    assert d["ok"] is True
    assert d["lists"]["bad"]["custom"] is True
    assert d["lists"]["bad"]["count"] == 0
    assert d["lists"]["bad"]["text"].strip() == ""
    from core import censor
    assert censor.is_bad("убиваю") is False


def test_censor_words_reset_returns_shipped_list(client, censor_files):
    """reset=bad/all возвращает поставочный список, удаляя только свой файл."""
    bad_user, ok_user = censor_files
    _post(client, "/api/censor_words", {"bad": "своё\n", "ok": "своё\n"})
    assert bad_user.is_file() and ok_user.is_file()

    d = _post(client, "/api/censor_words", {"reset": "bad"})
    assert d["ok"] is True
    assert d["lists"]["bad"]["custom"] is False and d["lists"]["bad"]["count"] > 0
    assert not bad_user.exists() and ok_user.is_file(), "reset одного списка снёс соседний"

    d = _post(client, "/api/censor_words", {"reset": "all"})
    assert d["ok"] is True
    assert d["lists"]["bad"]["custom"] is False and d["lists"]["ok"]["custom"] is False
    assert not bad_user.exists() and not ok_user.exists()


def test_censor_words_unknown_reset(client, censor_files):
    d = _post(client, "/api/censor_words", {"reset": "злые"})
    assert d.get("ok") is not True and d["err"] == "unknown_list"
    assert d["err_vars"]["k"] == "злые"


@pytest.mark.parametrize("payload", [{"bad": 123}, {"ok": [1, 2]}, {"bad": None, "ok": {}}])
def test_censor_words_wrong_type_is_ignored_not_500(client, censor_files, payload):
    """Не-строка в теле — просто «не правка»: ответ ok, списки прежние, файлов нет."""
    d = _post(client, "/api/censor_words", payload)
    assert d["ok"] is True
    assert d["lists"]["bad"]["custom"] is False and d["lists"]["ok"]["custom"] is False
    assert not censor_files[0].exists() and not censor_files[1].exists()


# --------------------------------------------------------------------------- #
# /api/delstyle
# --------------------------------------------------------------------------- #
def test_delstyle_removes_only_named_template(client, style_dir):
    """Удаляется ровно один файл по имени (регистр не важен), сосед цел."""
    doomed = style_dir / "Doomed.json"
    neighbour = style_dir / "Neighbor.json"
    doomed.write_text('{"label": "Doomed"}', encoding="utf-8")
    neighbour.write_text('{"label": "Neighbor"}', encoding="utf-8")

    d = _post(client, "/api/delstyle", {"name": "doomed"})
    assert d == {"ok": True}
    assert not doomed.exists()
    assert neighbour.is_file() and json.load(open(neighbour, encoding="utf-8"))["label"] == "Neighbor"


@pytest.mark.parametrize("payload", [{}, {"name": ""}, {"name": "   "}])
def test_delstyle_needs_name(client, style_dir, payload):
    d = _post(client, "/api/delstyle", payload)
    assert d.get("ok") is not True and d["err"] == "style_name_missing"
    assert os.listdir(str(style_dir)) == []


@pytest.mark.parametrize("name", ["base", "geologica", "НетТакого"])
def test_delstyle_builtin_is_refused(client, style_dir, name):
    """Встроенные стили живут в коде styles.py — файла нет, значит удалять нечего."""
    d = _post(client, "/api/delstyle", {"name": name})
    assert d.get("ok") is not True and d["err"] == "builtin_style" and d["error"]


@pytest.mark.parametrize("name", ["../sentinel", "..\\sentinel", "/sentinel",
                                  "C:\\sentinel", "../../sentinel.json"])
def test_delstyle_name_cannot_reach_outside(client, style_dir, tmp_path, name):
    """Путь в имени не выходит из папки стилей: совпадение ищется ТОЛЬКО среди файлов папки."""
    sentinel = tmp_path / "sentinel.json"
    sentinel.write_text('{"label": "sentinel"}', encoding="utf-8")
    neighbour = style_dir / "Neighbor.json"
    neighbour.write_text('{"label": "Neighbor"}', encoding="utf-8")

    d = _post(client, "/api/delstyle", {"name": name})
    assert d.get("ok") is not True and d["err"] == "builtin_style", d
    assert sentinel.is_file() and sentinel.read_text(encoding="utf-8") == '{"label": "sentinel"}'
    assert neighbour.is_file()
    # и «почти совпадение» по стему тоже ничего не удаляет
    assert not (tmp_path / "sentinel").exists()


# --------------------------------------------------------------------------- #
# Чужой тип поля
# --------------------------------------------------------------------------- #
def test_wrong_type_name_in_style_routes(client, style_dir):
    bad = []
    for url in ("/api/savestyle", "/api/delstyle"):
        r = client.post(url, json={"name": 123, "data": {}}, headers=H)
        if r.status_code != 200 or r.get_json().get("ok") is True:
            bad.append((url, r.status_code, r.get_json()))
    assert bad == [], bad
