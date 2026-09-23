# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Контракты шести роутов, которых не было ни в одном тесте.

Перечень собран механически (`grep -rn "@bp.route" api/` — 88 маршрутов): в tests/
не упоминались ровно эти шесть. Пишущие — `delspeaker`, `terms`, `ai_intro`,
`breaths` и `music_random` (POST по методу; по факту первые два пишут файлы,
остальные только читают), читающий — `files`.

Каждый роут проверяется с двух сторон, как в tests/test_destructive_routes.py:
плохой вход (нет поля, чужой тип, путь наружу) — ошибка ЭТОГО роута (200 + `err`),
а не HTTP 500 и не «тихо сделали не то»; успешный вызов делает ровно обещанное и
не трогает соседние файлы.

Изоляция: личные файлы и папки (`speakers/`, `terms.json`, `halluc_phrases.json`,
XML с сайдкарами) подменяются на tmp_path; ИИ и сеть не вызываются вовсе —
`aicut.cmd_intro`, `aicut.cmd_*` и `aicut.unload_ours` подменены заглушками
(образец — tests/test_routes_misc.py).

Запуск:  python -m pytest tests/test_api_contract.py -q
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

# Хост настоящего локального клиента: без него запрос отбивает защита от DNS
# rebinding (её тесты — в test_api_security.py), и до проверок дело не дойдёт.
H = {"Host": "127.0.0.1:5001"}


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def _post(client, url, payload):
    r = client.post(url, json=payload, headers=H)
    assert r.status_code == 200, f"{url}: {r.status_code} {r.data[:200]!r}"
    return r.get_json()


def _get(client, url, **kwargs):
    r = client.get(url, headers=H, **kwargs)
    assert r.status_code == 200, f"{url}: {r.status_code} {r.data[:200]!r}"
    return r.get_json()


def _stat(path):
    st = os.stat(path)
    return (st.st_size, st.st_mtime_ns)


# --------------------------------------------------------------------------- #
# POST /api/delspeaker (api/presets.py) — пишущий (удаляет файл профиля)
# --------------------------------------------------------------------------- #
@pytest.fixture
def speaker_dir(tmp_path, monkeypatch):
    """Своя папка спикеров: боевая `speakers/` — junction на личные профили."""
    from core import speakers
    d = tmp_path / "speakers"
    d.mkdir()
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(d))
    return d


def _real_speakers_state():
    """Слепок боевой папки спикеров (имена файлов) — её тест трогать не должен."""
    real = os.path.join(ROOT, "speakers")
    if not os.path.isdir(real):
        return None
    return sorted(os.listdir(real))


def test_delspeaker_удаляет_ровно_названный_профиль(client, speaker_dir):
    """Удаляется один файл по имени, соседний профиль цел побайтово."""
    doomed = speaker_dir / "A.json"
    neighbour = speaker_dir / "B.json"
    doomed.write_text('{"label": "A"}', encoding="utf-8")
    neighbour.write_text('{"label": "B", "cut": {"onset_db": 18}}', encoding="utf-8")
    before = neighbour.read_bytes()
    real_before = _real_speakers_state()

    assert _post(client, "/api/delspeaker", {"name": "A"}) == {"ok": True}

    assert not doomed.exists()
    assert neighbour.read_bytes() == before
    assert _real_speakers_state() == real_before, "тест тронул боевую папку speakers/"


def test_delspeaker_имя_с_пробелом_находит_свой_файл(client, speaker_dir):
    """`speakers.save` кладёт профиль под ключом из имени (пробел -> `_`), и удаление
    идёт по тому же ключу: профиль, сохранённый из UI, сносится тем же именем."""
    from core import speakers
    key, path = speakers.save("Спикер A", {"cut": {"onset_db": 15}})
    assert os.path.isfile(path)

    d = _post(client, "/api/delspeaker", {"name": "Спикер A"})
    assert d == {"ok": True}
    assert not os.path.exists(path), f"профиль {key} не удалён"
    assert os.listdir(str(speaker_dir)) == []


@pytest.mark.parametrize("payload", [{}, {"name": ""}, {"name": "   "},
                                     {"name": 123}, {"name": None}, {"name": ["A"]},
                                     {"name": {"x": "A"}}])
def test_delspeaker_без_имени_ничего_не_удаляет(client, speaker_dir, payload):
    """Нет имени (или оно не строка) — ошибка роута, оба профиля на месте."""
    a = speaker_dir / "A.json"
    b = speaker_dir / "B.json"
    a.write_text('{"label": "A"}', encoding="utf-8")
    b.write_text('{"label": "B"}', encoding="utf-8")

    d = _post(client, "/api/delspeaker", payload)
    assert d.get("ok") is not True and d["err"] == "speaker_name_missing", d
    assert a.is_file() and b.is_file()
    assert sorted(os.listdir(str(speaker_dir))) == ["A.json", "B.json"]


def test_delspeaker_нет_профиля(client, speaker_dir):
    """Нечего удалять — свой код ошибки, а не ok: true (иначе UI врёт про успех)."""
    d = _post(client, "/api/delspeaker", {"name": "НетТакого"})
    assert d.get("ok") is not True and d["err"] == "profile_not_found" and d["error"]
    assert os.listdir(str(speaker_dir)) == []


@pytest.mark.parametrize("name", ["../sentinel", "..\\sentinel", "/sentinel",
                                  "C:\\sentinel", "../../sentinel.json"])
def test_delspeaker_путь_наружу_не_удаляет(client, speaker_dir, tmp_path, name):
    """Имя — не путь: `speakers._key` чистит его до `[\\w-]`, поэтому чужой файл рядом
    с папкой профилей цел, а ответ — «профиль не найден»."""
    sentinel = tmp_path / "sentinel.json"
    sentinel.write_text('{"label": "sentinel"}', encoding="utf-8")
    (speaker_dir / "A.json").write_text('{"label": "A"}', encoding="utf-8")

    d = _post(client, "/api/delspeaker", {"name": name})
    assert d.get("ok") is not True and d["err"] == "profile_not_found", d
    assert sentinel.is_file()
    assert sentinel.read_text(encoding="utf-8") == '{"label": "sentinel"}'
    assert sorted(os.listdir(str(speaker_dir))) == ["A.json"]


# --------------------------------------------------------------------------- #
# GET/POST /api/terms (api/presets.py) — POST пишущий (перезапись словаря)
# --------------------------------------------------------------------------- #
@pytest.fixture
def terms_file(tmp_path, monkeypatch):
    """Свой файл словаря + чистый кэш: боевой terms.json (личный) не читается."""
    from core import terms
    p = tmp_path / "terms.json"
    monkeypatch.setattr(terms, "TERMS_PATH", str(p))
    monkeypatch.setattr(terms, "_CACHE", {"mtime": -1, "data": None})
    return p


def test_terms_get_пустой_словарь(client, terms_file):
    """Файла нет — пустой список, а не ошибка: словарь набирается в UI."""
    d = _get(client, "/api/terms")
    assert d == {"ok": True, "terms": []}
    assert not terms_file.exists(), "GET завёл файл словаря"


def test_terms_post_пишет_названия_и_сохраняет_варианты(client, terms_file, tmp_path):
    """POST перезаписывает список НАЗВАНИЙ: пустые и повторы отбрасываются, а
    накопленные обучением варианты термина остаются (terms.set_terms)."""
    neighbour = tmp_path / "чужой.json"
    neighbour.write_text("{}", encoding="utf-8")

    d = _post(client, "/api/terms", {"terms": [
        {"term": "RTX 5090", "variants": ["эр тэ икс"]}, "Reelsi", "   ", "reelsi"]})
    assert d["ok"] is True
    assert [t["term"] for t in d["terms"]] == ["RTX 5090", "Reelsi"]
    assert d["terms"][0]["variants"] == ["эр тэ икс"]

    # юзер правит только названия: вариант, выученный из правки слова, не теряется
    d = _post(client, "/api/terms", {"terms": ["RTX 5090"]})
    assert d["terms"] == [{"term": "RTX 5090", "variants": ["эр тэ икс"]}]

    on_disk = json.loads(terms_file.read_text(encoding="utf-8"))
    assert on_disk == {"terms": [{"term": "RTX 5090", "variants": ["эр тэ икс"]}]}
    assert neighbour.read_text(encoding="utf-8") == "{}", "правка словаря тронула соседний файл"

    # и словарь реально начал работать (правка доехала до применения)
    from core import terms
    words = [{"w": "эр", "start": 0.0, "end": 0.2},
             {"w": "тэ", "start": 0.2, "end": 0.4},
             {"w": "икс", "start": 0.4, "end": 0.6}]
    fixed = terms.fix_words(words)
    assert [w["w"] for w in fixed] == ["RTX 5090"]
    assert (fixed[0]["start"], fixed[0]["end"]) == (0.0, 0.6)   # тайминги склейки


def test_terms_post_число_в_теле_даёт_ошибку_роута(client, terms_file):
    """Не итерируемое значение — umsg, а не 500, и файла словаря не появляется."""
    d = _post(client, "/api/terms", {"terms": 123})
    assert d.get("ok") is not True and d["err"] == "terms_failed", d
    assert "TypeError" in d["error"]
    assert not terms_file.exists()


def test_terms_отсутствующее_поле_не_должно_стирать_словарь(client, terms_file):
    """POST без поля `terms` — не «очистить словарь», а ошибка: список названий
    приходит из UI целиком, и потерять его из-за пустого тела нельзя.
    Файл словаря при отказе не трогается вовсе."""
    _post(client, "/api/terms", {"terms": ["Reelsi", "RTX 5090"]})
    assert terms_file.is_file()

    for payload in ({}, {"terms": None}):
        d = _post(client, "/api/terms", payload)
        assert d.get("ok") is not True and d["err"], f"{payload}: словарь стёрт без ошибки"
        assert json.loads(terms_file.read_text(encoding="utf-8"))["terms"], \
            f"{payload}: список названий пропал"

    # а вот явный ПУСТОЙ СПИСОК — законное «удалить всё» из интерфейса (textarea пуст)
    d = _post(client, "/api/terms", {"terms": []})
    assert d == {"ok": True, "terms": []}, d
    assert json.loads(terms_file.read_text(encoding="utf-8")) == {"terms": []}


def test_terms_не_список_не_должен_писать_мусор(client, terms_file):
    """`terms` — список. Строка/словарь в теле раньше превращались в термины «a»,
    «b», «c» (итерация по символам/ключам) и оставались в словаре навсегда; теперь
    это отказ, и файла словаря не появляется."""
    for payload in ({"terms": "abc"}, {"terms": {"a": 1}}):
        d = _post(client, "/api/terms", payload)
        assert d.get("ok") is not True and d["err"], f"{payload}: тело принято как список"
    assert not terms_file.exists(), "отказ роута всё-таки записал мусор в словарь"


# --------------------------------------------------------------------------- #
# POST /api/music_random (api/files.py) — POST по методу, по факту только выбирает
# --------------------------------------------------------------------------- #
@pytest.fixture
def music_dir(tmp_path):
    d = tmp_path / "music"
    d.mkdir()
    (d / "b_track.mp3").write_bytes(b"B" * 100)
    (d / "a_track.m4a").write_bytes(b"A" * 200)
    (d / "cover.jpg").write_bytes(b"IMG")
    (d / "notes.txt").write_text("не музыка", encoding="utf-8")
    return d


def test_music_random_выбирает_аудио_из_папки(client, music_dir):
    """Путь — абсолютный, существует и лежит в папке; картинки и текст не выбираются;
    ни один файл папки не меняется."""
    before = {f.name: _stat(f) for f in music_dir.iterdir()}

    d = _post(client, "/api/music_random", {"dir": str(music_dir)})
    assert set(d) == {"path"}, d
    picked = d["path"]
    assert os.path.basename(picked) in ("a_track.m4a", "b_track.mp3"), picked
    assert os.path.isabs(picked) and os.path.isfile(picked)
    assert os.path.dirname(os.path.realpath(picked)) == os.path.realpath(str(music_dir))

    assert {f.name: _stat(f) for f in music_dir.iterdir()} == before


def test_music_random_по_seed_детерминирован(client, music_dir):
    """С тем же seed выбор тот же (превью и сборка должны показать один трек)."""
    first = _post(client, "/api/music_random", {"dir": str(music_dir), "seed": "clip_A"})
    second = _post(client, "/api/music_random", {"dir": str(music_dir), "seed": "clip_A"})
    assert first["path"] and first["path"] == second["path"]


@pytest.mark.parametrize("payload", [{}, {"dir": ""}, {"dir": None}, {"dir": 123},
                                     {"dir": ["/tmp"]}, {"dir": {"p": "/tmp"}},
                                     {"dir": "C:/нет-такой-папки"}])
def test_music_random_плохой_вход_пустой_путь(client, payload):
    """Нет папки (или она не строка) — пустой путь без падения: превью просто
    промолчит, а не покажет 500."""
    d = _post(client, "/api/music_random", payload)
    assert d == {"path": ""}, d


def test_music_random_файл_вместо_папки(client, tmp_path):
    f = tmp_path / "трек.mp3"
    f.write_bytes(b"x")
    assert _post(client, "/api/music_random", {"dir": str(f)}) == {"path": ""}


def test_music_random_пустая_папка(client, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _post(client, "/api/music_random", {"dir": str(empty)}) == {"path": ""}


# --------------------------------------------------------------------------- #
# POST /api/breaths (api/editor.py) — POST по методу, по факту читает сайдкар
# (цель проверяется is_reelsi_target, чужой путь сайдкар не отдаёт)
# --------------------------------------------------------------------------- #
def test_breaths_нет_сайдкара_пустой_список(client, tmp_path):
    """Нарезка могла идти без детектора — файла нет, ответ пустой, и роут его не создаёт."""
    xml = tmp_path / "clip.xml"
    xml.write_text("<xmeml/>", encoding="utf-8")

    d = _post(client, "/api/breaths", {"xml": str(xml)})
    assert d == {"ok": True, "marks": []}
    assert sorted(os.listdir(str(tmp_path))) == ["clip.xml"]


def test_breaths_отдаёт_метки_сайдкара(client, tmp_path):
    """Метки идут из `<stem>.breaths.json` как есть — редактор рисует их на таймлайне."""
    xml = tmp_path / "clip.xml"
    xml.write_text("<xmeml/>", encoding="utf-8")
    marks = [{"start": 1.5, "end": 2.0, "kind": "вздох", "cut": False},
             {"start": 7.0, "end": 7.3, "kind": "кхе", "cut": True}]
    (tmp_path / "clip.breaths.json").write_text(json.dumps(marks, ensure_ascii=False),
                                                encoding="utf-8")
    before = _stat(tmp_path / "clip.breaths.json")

    d = _post(client, "/api/breaths", {"xml": str(xml)})
    assert d == {"ok": True, "marks": marks}
    assert _stat(tmp_path / "clip.breaths.json") == before


def test_breaths_битый_сайдкар_это_ошибка_роута(client, tmp_path):
    """Обрезанный JSON (крах при записи) — umsg, а не 500 и не пустой список."""
    xml = tmp_path / "clip.xml"
    xml.write_text("<xmeml/>", encoding="utf-8")
    (tmp_path / "clip.breaths.json").write_text('[{"start": 1.0,', encoding="utf-8")

    d = _post(client, "/api/breaths", {"xml": str(xml)})
    assert d.get("ok") is not True and d["err"] == "breaths_failed", d
    assert d["error"]


@pytest.mark.parametrize("payload", [{}, {"xml": ""}, {"xml": 123}, {"xml": None},
                                     {"xml": ["clip.xml"]}, {"xml": {"path": "clip.xml"}}])
def test_breaths_плохой_вход_не_падает(client, payload):
    """`xml` не строка/не передан — отказ роута, а не 500: чужого пути нет, сайдкар
    не читается. До проверки цели такой вход молча давал пустой список,
    а путь «» искал `.breaths.json` в текущем каталоге сервера."""
    d = _post(client, "/api/breaths", payload)
    assert d.get("ok") is not True and d["err"] == "not_a_cut", d
    assert "marks" not in d


def test_breaths_чужой_путь_это_отказ(client, tmp_path):
    """Сайдкар читается только рядом со СВОЕЙ нарезкой: по телу с чужим путём
    `notes.breaths.json` не отдаётся. Чужой .xml без признаков нарезки (корень не
    xmeml, `project.json` рядом нет) — тоже отказ."""
    marks = [{"start": 1.5, "end": 2.0, "kind": "вздох", "cut": False}]
    notes = tmp_path / "notes.txt"
    notes.write_text("чужие заметки", encoding="utf-8")
    (tmp_path / "notes.breaths.json").write_text(json.dumps(marks, ensure_ascii=False),
                                                 encoding="utf-8")

    d = _post(client, "/api/breaths", {"xml": str(notes)})
    assert d.get("ok") is not True and d["err"] == "not_a_cut", d
    assert "marks" not in d, "по чужому пути отдан сайдкар нарезки"

    foreign_xml = tmp_path / "чужой.xml"
    foreign_xml.write_text("<notes><item/></notes>\n", encoding="utf-8")
    (tmp_path / "чужой.breaths.json").write_text(json.dumps(marks, ensure_ascii=False),
                                                 encoding="utf-8")

    d = _post(client, "/api/breaths", {"xml": str(foreign_xml)})
    assert d.get("ok") is not True and d["err"] == "not_a_cut", d
    assert "marks" not in d


def test_breaths_свою_нарезку_отдаёт_как_раньше(client, tmp_path):
    """Нарезка Reelsi (корень xmeml ЛИБО `project.json` рядом) — метки на месте:
    проверка цели не задела рабочий путь интерфейса."""
    xml = tmp_path / "clip.xml"
    xml.write_text("<xmeml/>", encoding="utf-8")
    marks = [{"start": 3.0, "end": 3.4, "kind": "кхе", "cut": True}]
    (tmp_path / "clip.breaths.json").write_text(json.dumps(marks, ensure_ascii=False),
                                                encoding="utf-8")

    assert _post(client, "/api/breaths", {"xml": str(xml)}) == {"ok": True, "marks": marks}

    # и вторая дверь признака нарезки: корень не xmeml, но есть .project.json
    other = tmp_path / "02_clip.xml"
    other.write_text("не XML вовсе\n", encoding="utf-8")
    (tmp_path / "02_clip.project.json").write_text("{}", encoding="utf-8")
    (tmp_path / "02_clip.breaths.json").write_text(json.dumps(marks, ensure_ascii=False),
                                                   encoding="utf-8")

    assert _post(client, "/api/breaths", {"xml": str(other)}) == {"ok": True, "marks": marks}


# --------------------------------------------------------------------------- #
# GET /api/files (api/files.py) — читающий
# --------------------------------------------------------------------------- #
def test_files_список_видео_папки(client, tmp_path):
    """Только видео (mp4/mov/mxf), по алфавиту, `name` — имя папки; файлы целы."""
    d = tmp_path / "Исходники"
    d.mkdir()
    for name in ("b.MP4", "a.mov", "c.mxf", "cover.jpg", "notes.txt", "sound.mp3"):
        (d / name).write_bytes(b"x")
    before = {f.name: _stat(f) for f in d.iterdir()}

    res = _get(client, "/api/files", query_string={"dir": str(d)})
    assert res["files"] == ["a.mov", "b.MP4", "c.mxf"]
    assert res["name"] == "Исходники"
    assert "err" not in res
    assert {f.name: _stat(f) for f in d.iterdir()} == before


def test_files_кавычки_в_пути(client, tmp_path):
    """Фронт присылает путь в кавычках — роут их снимает (как остальные файловые)."""
    d = tmp_path / "out"
    d.mkdir()
    (d / "clip.mp4").write_bytes(b"x")
    res = _get(client, "/api/files", query_string={"dir": f'"{d}"'})
    assert res["files"] == ["clip.mp4"]


@pytest.mark.parametrize("dir_arg", [None, "", "C:/нет-такой-папки", "   "])
def test_files_нет_папки(client, dir_arg):
    """Папки нет — пустой список РЯДОМ с ошибкой: фронт рисует пусто, а не падает."""
    query = {} if dir_arg is None else {"dir": dir_arg}
    res = _get(client, "/api/files", query_string=query)
    assert res["files"] == []
    assert res["err"] == "no_folder" and res["error"]


def test_files_файл_вместо_папки(client, tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"x")
    res = _get(client, "/api/files", query_string={"dir": str(f)})
    assert res["files"] == [] and res["err"] == "no_folder"


# --------------------------------------------------------------------------- #
# POST /api/ai_intro (api/ai.py) — пишущий (ИИ-разметка, .intro.json)
# --------------------------------------------------------------------------- #
@pytest.fixture
def xml_file(tmp_path):
    p = tmp_path / "Edited.xml"
    p.write_text("<xmeml/>", encoding="utf-8")
    return str(p)


@pytest.fixture
def no_model(monkeypatch):
    """LM Studio в тестах не трогаем: выгрузка модели — заглушка со счётчиком."""
    from core import aicut
    calls = []
    monkeypatch.setattr(aicut, "unload_ours", lambda *a, **k: calls.append(1))
    return calls


@pytest.fixture
def quiet_emit(monkeypatch):
    """Строки роута — в общий JOB-лог, его читают другие тесты: подменяем emit."""
    import api.ai as ai_route
    seen = []
    monkeypatch.setattr(ai_route, "emit", lambda line="", **v: seen.append((line, v)))
    return seen


def test_ai_intro_контракт(client, xml_file, monkeypatch, no_model, quiet_emit):
    """Ответ несёт строки интро и акценты; модель выгружается, счётчик ИИ-вызовов отпущен."""
    from core import aicut

    seen = {}
    rows = [{"text": "Хук", "start": 0.0, "end": 1.2}]
    mids = [{"text": "Акцент", "start": 12.0, "end": 13.0}]
    inserts = [{"type": "photo", "start_sec": 7.0, "query": "cat"}]

    def fake_intro(xml_path, model=None, emit=None, inserts=None):
        seen.update(xml_path=xml_path, model=model, inserts=inserts)
        emit("размечаю интро…")
        emit("акцентов: {count}", count=len(mids))
        return {"intro_rows": rows, "mid_groups": mids}

    monkeypatch.setattr(aicut, "cmd_intro", fake_intro)
    # проверка до вызова: чужой неотпущенный ИИ-вызов заставит _ai_begin ждать 25с
    assert api._core.AI_ACTIVE == 0, "предыдущий тест не отпустил AI_ACTIVE"

    d = _post(client, "/api/ai_intro", {"xml": xml_file, "model": "test-model",
                                        "inserts": inserts})

    assert d["ok"] is True, d
    assert d["intro_rows"] == rows and d["mid_groups"] == mids
    assert d["log"] == ["размечаю интро…", "акцентов: 1"]
    assert seen == {"xml_path": xml_file, "model": "test-model", "inserts": inserts}
    assert [line for line, _v in quiet_emit] == ["размечаю интро…", "акцентов: {count}"]
    assert quiet_emit[1][1] == {"count": 1}
    assert no_model == [1], "после успешного вызова модель обязана выгружаться"
    assert api._core.AI_ACTIVE == 0


def test_ai_intro_inserts_не_список_не_передаётся(client, xml_file, monkeypatch,
                                                  no_model, quiet_emit):
    """`inserts` из тела идёт в aicut только списком: иначе cmd_intro подхватит
    сайдкар `.inserts.json` (документированное поведение роута)."""
    from core import aicut
    seen = {}

    def fake_intro(xml_path, model=None, emit=None, inserts=None):
        seen["inserts"] = inserts
        return {"intro_rows": [], "mid_groups": []}

    monkeypatch.setattr(aicut, "cmd_intro", fake_intro)
    for payload in ("мусор", 123, {"a": 1}, None):
        _post(client, "/api/ai_intro", {"xml": xml_file, "inserts": payload})
        assert seen["inserts"] is None, payload
    assert api._core.AI_ACTIVE == 0


@pytest.mark.parametrize("payload", [{}, {"xml": ""}, {"xml": None}, {"xml": 123},
                                     {"xml": ["Edited.xml"]},
                                     {"xml": "C:/нет-такого.xml"}])
def test_ai_intro_нет_xml(client, payload, monkeypatch, no_model, quiet_emit):
    """Плохой/чужой `xml` — ошибка до всякого обращения к модели (её и не должно быть)."""
    from core import aicut

    def boom(*a, **k):
        raise AssertionError("роут пошёл к модели без файла XML")

    monkeypatch.setattr(aicut, "cmd_intro", boom)
    assert api._core.AI_ACTIVE == 0
    d = _post(client, "/api/ai_intro", payload)
    assert d.get("ok") is not True and d["err"] == "file_not_found", d
    assert no_model == [] and api._core.AI_ACTIVE == 0


def test_ai_intro_отказ_модели(client, xml_file, monkeypatch, no_model, quiet_emit):
    """Отказ/недоступность LM Studio — umsg модели в ответе, модель не выгружаем."""
    from core import aicut
    from core.umsg import umsg

    def boom(*a, **k):
        raise SystemExit(umsg("llm_down", "LM Studio недоступен"))

    monkeypatch.setattr(aicut, "cmd_intro", boom)
    assert api._core.AI_ACTIVE == 0
    d = _post(client, "/api/ai_intro", {"xml": xml_file})
    assert d.get("ok") is not True and d["err"] == "llm_down"
    assert d["error"] == "LM Studio недоступен"
    assert no_model == [] and api._core.AI_ACTIVE == 0


def test_ai_intro_чужая_ошибка(client, xml_file, monkeypatch, no_model, quiet_emit):
    """Любая другая ошибка внутри — тоже umsg (intro_failed), а не 500."""
    from core import aicut

    def boom(*a, **k):
        raise RuntimeError("провайдер вернул мусор")

    monkeypatch.setattr(aicut, "cmd_intro", boom)
    assert api._core.AI_ACTIVE == 0
    d = _post(client, "/api/ai_intro", {"xml": xml_file})
    assert d.get("ok") is not True and d["err"] == "intro_failed"
    assert "провайдер вернул мусор" in d["error"]
    assert no_model == [] and api._core.AI_ACTIVE == 0
