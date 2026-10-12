# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Роуты и джоб подкаста (api/podcast.py): проверка дорожек, создание, конвейер, правка кусков.

Без сети, GPU и ffmpeg: `subprocess.run` (yt-dlp -J), `podcast_ai.transcribe`/`choose` и
общий лок задач подменяются по модулю api.podcast. Конвейер зовём напрямую `_pipeline(dir)`,
а не через поток, — так тест детерминирован.

Запуск:  py -3.10 -m pytest tests/test_api_podcast.py -q
"""
import os
import re
import sys
import threading
from types import SimpleNamespace

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

import api.podcast as podcast_api  # noqa: E402
from core import fileio, podcast  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "ytdlp_multiaudio.json")
YT = "https://www.youtube.com/watch?v=abcDEF12345"


def _client():
    from flask import Flask
    import api
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def _reset_pd():
    with podcast_api.PDLOCK:
        podcast_api.PDJOB.update(podcast_api.PDFRESH, running=False, done=False, failed=None,
                                 log=[], log_base=0, dir="", started=0)


@pytest.fixture(autouse=True)
def _pd_clean():
    _reset_pd()
    yield
    _reset_pd()


def _file_project(tmp_path, status="downloaded", title="Выпуск"):
    """Проект подкаста из локального файла: папка podcasts/<title>, источник — пустой mp4."""
    media = tmp_path / "media"
    media.mkdir(exist_ok=True)
    src = media / "lecture.mp4"
    src.write_bytes(b"x")
    folder = str(tmp_path / "podcasts" / "Выпуск")
    proj = podcast.new_project(title, "", os.path.abspath(str(src)), 5)
    proj["status"] = status
    podcast.save_project(folder, proj)
    return folder


def _fake_cams(monkeypatch, lock=None):
    """Подмена core.podcast_cams.analyze: без RVM и ffmpeg. Возвращает список вызовов:
    (папка, снимок лока в момент вызова) — лок нужен, чтобы проверить «под локом»."""
    from core import podcast_cams
    calls = []

    def fake(proj_dir, proj, emit=None, cancel=None):
        calls.append((proj_dir, dict(lock) if lock is not None else None))
        return {}

    monkeypatch.setattr(podcast_cams, "analyze", fake)
    return calls


def _fake_lock(monkeypatch, ok=True):
    """Общий лок задач: считает, сколько раз взяли и отпустили. ok=False — лок занят."""
    st = {"taken": 0, "released": 0}

    def acquire():
        if ok:
            st["taken"] += 1
        return ok

    def release():
        st["released"] += 1

    monkeypatch.setattr(podcast_api, "_cross_lock_acquire", acquire)
    monkeypatch.setattr(podcast_api, "_cross_lock_release", release)
    return st


# --- проверка дорожек ---

def test_проверка_дорожек_по_фикстуре(monkeypatch):
    raw = open(FIXTURE, encoding="utf-8").read()
    monkeypatch.setattr(podcast_api.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout=raw, stderr=""))
    c = _client()
    d = c.post("/api/podcast_probe", json={"url": YT}).get_json()
    assert d["ok"] is True
    assert d["title"] == "Тестовый выпуск"
    assert len(d["tracks"]) == 3
    # по умолчанию — оригинал, а не первый попавшийся дубляж
    assert d["default"] == "140-23"


def test_проверка_не_youtube_даёт_ошибку(monkeypatch):
    def no_run(*a, **k):
        raise AssertionError("до yt-dlp дело не должно доходить")
    monkeypatch.setattr(podcast_api.subprocess, "run", no_run)
    d = _client().post("/api/podcast_probe", json={"url": "https://disk.yandex.ru/d/abc"}).get_json()
    assert not d.get("ok")
    assert d.get("error")


# --- создание ---

def test_создание_из_файла_папка_статус_и_суффикс(tmp_path):
    root = tmp_path / "podcasts"
    root.mkdir()
    media = tmp_path / "media"
    media.mkdir()
    f = media / "Интервью.mp4"
    f.write_bytes(b"x")
    c = _client()

    d = c.post("/api/podcast_create", json={"root": str(root), "file": str(f), "reels": 5}).get_json()
    assert d["ok"] is True
    assert os.path.basename(d["dir"]) == "Интервью"
    proj = podcast.load_project(d["dir"])
    assert proj["status"] == "downloaded"
    assert proj["source"] == os.path.abspath(str(f))

    again = c.post("/api/podcast_create", json={"root": str(root), "file": str(f), "reels": 5}).get_json()
    assert again["ok"] is True
    assert os.path.basename(again["dir"]) == "Интервью_2"


@pytest.mark.parametrize("reels", [0, 31])
def test_создание_рилсов_вне_диапазона_отказ(tmp_path, reels):
    root = tmp_path / "podcasts"
    root.mkdir()
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    d = _client().post("/api/podcast_create",
                       json={"root": str(root), "file": str(f), "reels": reels}).get_json()
    assert not d.get("ok")
    assert d.get("error")


def test_создание_и_ссылка_и_файл_отказ(tmp_path):
    root = tmp_path / "podcasts"
    root.mkdir()
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    d = _client().post("/api/podcast_create",
                       json={"root": str(root), "url": YT, "file": str(f), "reels": 3}).get_json()
    assert not d.get("ok")
    assert d.get("error")
    # ни папки, ни проекта быть не должно: отказ до записи
    assert os.listdir(root) == []


# --- конвейер ---

def test_конвейер_файла_расшифровка_и_выбор_по_порядку(tmp_path, monkeypatch):
    folder = _file_project(tmp_path)
    lock = _fake_lock(monkeypatch)
    calls = []

    def fake_transcribe(f, proj, emit=None, cancel=None):
        calls.append("transcribe")
        proj["duration"] = 100.0
        podcast.save_project(f, proj)
        return [{"w": "а", "start": 0.0, "end": 1.0}]

    def fake_choose(f, proj, emit=None):
        calls.append("choose")
        proj["segments"] = []
        proj["status"] = "ready"       # статус «ready» ставит choose — здесь его имитируем
        podcast.save_project(f, proj)
        return []

    monkeypatch.setattr(podcast_api.podcast_ai, "transcribe", fake_transcribe)
    monkeypatch.setattr(podcast_api.podcast_ai, "choose", fake_choose)
    _fake_cams(monkeypatch)

    podcast_api._pipeline(folder)

    assert calls == ["transcribe", "choose"]
    assert lock == {"taken": 1, "released": 1}
    assert podcast.load_project(folder)["status"] == "ready"
    assert podcast_api.PDJOB["failed"] is False and podcast_api.PDJOB["done"] is True


def test_лок_не_взялся_статус_error_и_расшифровки_нет(tmp_path, monkeypatch):
    folder = _file_project(tmp_path)
    lock = _fake_lock(monkeypatch, ok=False)
    called = []
    monkeypatch.setattr(podcast_api.podcast_ai, "transcribe",
                        lambda *a, **k: called.append(1) or [])

    podcast_api._pipeline(folder)

    proj = podcast.load_project(folder)
    assert called == []
    assert lock["released"] == 0
    assert proj["status"] == "error"
    assert "тяжёлая" in proj["error"]
    assert podcast_api.PDJOB["failed"] is True


def test_расшифровка_упала_лок_всё_равно_отпущен(tmp_path, monkeypatch):
    folder = _file_project(tmp_path)
    lock = _fake_lock(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("модель не загрузилась")

    monkeypatch.setattr(podcast_api.podcast_ai, "transcribe", boom)

    podcast_api._pipeline(folder)

    proj = podcast.load_project(folder)
    assert lock == {"taken": 1, "released": 1}
    assert proj["status"] == "error"
    assert proj["error"] and "модель не загрузилась" in proj["error"]
    assert podcast_api.PDJOB["failed"] is True


def _cams_cancel_pipeline(tmp_path, monkeypatch, fake_analyze):
    """Конвейер до разбора камер (расшифровка подменена), разбор — fake_analyze."""
    from core import podcast_cams
    folder = _file_project(tmp_path)
    _fake_lock(monkeypatch)

    def fake_transcribe(f, proj, emit=None, cancel=None):
        proj["duration"] = 100.0
        podcast.save_project(f, proj)
        return [{"w": "а", "start": 0.0, "end": 1.0}]

    monkeypatch.setattr(podcast_api.podcast_ai, "transcribe", fake_transcribe)
    monkeypatch.setattr(podcast_cams, "analyze", fake_analyze)
    podcast_api._pipeline(folder)
    return folder


def test_стоп_в_разборе_камер_отмена_а_не_ошибка(tmp_path, monkeypatch):
    """Cancelled из podcast_cams при нажатом «Стоп» — отмена: статус назад на расшифровку,
    джоб не failed, в логе «остановлено» (раньше тип был другой, но флаг спасал)."""
    from core.xml2ae.parse import raise_if_cancelled

    def fake_analyze(proj_dir, proj, emit=None, cancel=None):
        podcast_api.PDJOB["cancel"] = True          # человек нажал «Стоп» во время разбора
        raise_if_cancelled(cancel)

    folder = _cams_cancel_pipeline(tmp_path, monkeypatch, fake_analyze)
    assert podcast_api.PDJOB["cancelled"] is True and podcast_api.PDJOB["failed"] is False
    assert podcast_api.PDJOB["cur"] == "остановлено"
    assert podcast.load_project(folder)["status"] == "transcribed"


def test_cancelled_без_флага_тоже_отмена(tmp_path, monkeypatch):
    """Cancelled без выставленного флага — тоже отмена, а не ошибка: тип один."""
    from core.xml2ae.parse import Cancelled

    def fake_analyze(proj_dir, proj, emit=None, cancel=None):
        raise Cancelled()

    folder = _cams_cancel_pipeline(tmp_path, monkeypatch, fake_analyze)
    assert podcast_api.PDJOB["cancelled"] is True and podcast_api.PDJOB["failed"] is False
    assert podcast.load_project(folder)["status"] == "transcribed"


# --- правка кусков ---

def test_правка_кусков_пересечение_отказ_и_новые_id(tmp_path):
    folder = _file_project(tmp_path, status="transcribed")
    proj = podcast.load_project(folder)
    proj["duration"] = 100.0
    proj["status"] = "ready"
    podcast.save_project(folder, proj)
    before = open(podcast.project_path(folder), "rb").read()
    c = _client()

    bad = c.post("/api/podcast_save", json={"dir": folder, "segments": [
        {"start": 10, "end": 40, "title": "а"},
        {"start": 30, "end": 60, "title": "б"},
    ]}).get_json()
    assert not bad.get("ok")
    assert open(podcast.project_path(folder), "rb").read() == before

    good = c.post("/api/podcast_save", json={"dir": folder, "segments": [
        {"id": "keep01", "start": 10, "end": 40, "title": "а"},
        {"start": 40, "end": 60, "title": "б"},          # касание концом — можно
    ]}).get_json()
    assert good["ok"] is True
    segs = podcast.load_project(folder)["segments"]
    assert segs[0]["id"] == "keep01"
    assert re.fullmatch(r"[0-9a-f]{6}", segs[1]["id"])


# --- список и карточка ---

def test_список_берёт_только_папки_с_проектом(tmp_path):
    root = tmp_path / "root"
    for name in ("first", "second"):
        podcast.save_project(str(root / name), podcast.new_project(name, "", "", 3))
    (root / "empty").mkdir()
    d = _client().get("/api/podcast_list", query_string={"root": str(root)}).get_json()
    assert d["ok"] is True
    assert len(d["items"]) == 2
    assert {i["title"] for i in d["items"]} == {"first", "second"}


def test_карточка_без_проекта_отказ(tmp_path):
    d = _client().get("/api/podcast_get", query_string={"dir": str(tmp_path)}).get_json()
    assert not d.get("ok")
    assert d.get("error")


# --- папка подкастов создаётся сама (Д.3) ---

def test_создание_создаёт_несуществующую_папку_подкастов(tmp_path):
    root = tmp_path / "нет" / "podcasts"   # ни одного из уровней ещё нет
    media = tmp_path / "media"
    media.mkdir()
    f = media / "Выпуск.mp4"
    f.write_bytes(b"x")
    d = _client().post("/api/podcast_create",
                       json={"root": str(root), "file": str(f), "reels": 5}).get_json()
    assert d["ok"] is True
    assert root.is_dir()
    assert os.path.isfile(podcast.project_path(d["dir"]))


@pytest.mark.parametrize("root", ["-evil", "relative/podcasts"])
def test_создание_папка_с_минусом_или_относительная_отказ(tmp_path, root):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    d = _client().post("/api/podcast_create",
                       json={"root": root, "file": str(f), "reels": 5}).get_json()
    assert not d.get("ok")
    assert d.get("error")
    assert not os.path.exists(root)


# --- лог джоба: записи, а не готовый текст (Д.5) ---

def test_лог_джоба_формат_числа_доходит_до_текста(monkeypatch):
    """`{el:.0f}` остаётся шаблоном в записи; текст из неё даёт тот же t(), что у главного
    джоба (серверный аналог клиентского t() — core.app_meta.t)."""
    podcast_api._pemit("{el:.0f}с · ждём первый чанк", el=3.7)
    rec = podcast_api.PDJOB["log"][-1]
    assert isinstance(rec, dict) and rec["v"] == {"el": 3.7}
    from core import app_meta
    assert app_meta.t(rec["t"], **rec["v"]) == "4с · ждём первый чанк"
    podcast_api._pemit("обычная строка")
    assert podcast_api.PDJOB["log"][-1] == "обычная строка"


# --- прокси для браузера (часть А) ---

@pytest.fixture(autouse=True)
def _pp_clean():
    with podcast_api.PPLOCK:
        podcast_api.PPJOB.update(running=False, pct=None, error="", dir="")
    yield
    with podcast_api.PPLOCK:
        podcast_api.PPJOB.update(running=False, pct=None, error="", dir="")


class _ProxyStub:
    """build_preview_proxy подменён счётчиком: ждёт `gate`, пишет dst и возвращает его."""

    def __init__(self):
        import threading
        self.calls = []
        self.gate = threading.Event()

    def __len__(self):
        return len(self.calls)

    def __getitem__(self, i):
        return self.calls[i]

    def __eq__(self, other):
        return self.calls == other


@pytest.fixture
def proxy_build(monkeypatch):
    from core import draftrender
    stub = _ProxyStub()

    def fake(src, dst, height=720, **kw):
        stub.calls.append((src, dst, height))
        stub.gate.wait(10)
        with open(dst, "wb") as f:
            f.write(b"proxy")
        return dst

    monkeypatch.setattr(draftrender, "build_preview_proxy", fake)
    yield stub
    stub.gate.set()     # не оставляем поток висеть, если тест упал до открытия


def _wait_proxy_idle(timeout=10.0):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        with podcast_api.PPLOCK:
            if not podcast_api.PPJOB["running"]:
                return
        time.sleep(0.02)
    raise AssertionError("сборка прокси не кончилась за {} с".format(timeout))


def test_прокси_свежее_источника_готово_без_сборки(tmp_path, proxy_build):
    folder = _file_project(tmp_path)
    proxy = os.path.join(folder, podcast_api.PROXY_NAME)
    with open(proxy, "wb") as f:
        f.write(b"p")
    src = podcast.load_project(folder)["source"]
    os.utime(src, (1000, 1000))
    os.utime(proxy, (2000, 2000))
    d = _client().post("/api/podcast_proxy", json={"dir": folder}).get_json()
    assert d["ok"] is True and d["ready"] is True and d["path"] == proxy
    assert proxy_build == []


def test_прокси_нет_запускает_сборку_и_статус_доходит_до_готово(tmp_path, proxy_build):
    folder = _file_project(tmp_path)
    c = _client()
    d = c.post("/api/podcast_proxy", json={"dir": folder}).get_json()
    assert d["ok"] is True and d["ready"] is False and d["building"] is True
    proxy_build.gate.set()
    _wait_proxy_idle()
    assert len(proxy_build) == 1
    assert proxy_build[0][2] == 720
    assert os.path.isfile(os.path.join(folder, podcast_api.PROXY_NAME))
    # временный файл сборки не остаётся рядом с итогом
    assert not os.path.exists(os.path.join(folder, podcast_api.PROXY_BUILD_NAME))
    st = c.get("/api/podcast_proxy_status", query_string={"dir": folder}).get_json()
    assert st["ok"] is True and st["running"] is False
    assert st["ready"] is True and st["error"] == ""


def test_прокси_повторный_запрос_не_запускает_вторую_сборку(tmp_path, proxy_build):
    folder = _file_project(tmp_path)
    c = _client()
    c.post("/api/podcast_proxy", json={"dir": folder})
    # сборка стоит у ворот: второй запрос застаёт её в работе и второй не запускает
    again = c.post("/api/podcast_proxy", json={"dir": folder}).get_json()
    assert again["ok"] is True and again["building"] is True
    proxy_build.gate.set()
    _wait_proxy_idle()
    assert len(proxy_build) == 1


def test_прокси_папка_без_проекта_отказ(tmp_path, proxy_build):
    d = _client().post("/api/podcast_proxy", json={"dir": str(tmp_path)}).get_json()
    assert not d.get("ok")      # ошибка — без поля ok, как у всех роутов
    assert d.get("error")
    assert proxy_build == []


# --- П7: перевод кусков в рилсы (_convert) ---

def _ready_project(tmp_path, status="ready"):
    """Проект с двумя непересекающимися кусками: 10–40 с и 50–80 с, длина записи 100 с."""
    folder = _file_project(tmp_path, status=status)
    proj = podcast.load_project(folder)
    proj["duration"] = 100.0
    proj["segments"] = [
        {"id": "aaa111", "start": 10.0, "end": 40.0, "title": "первый", "why": "", "reel": ""},
        {"id": "bbb222", "start": 50.0, "end": 80.0, "title": "второй", "why": "", "reel": ""},
    ]
    podcast.save_project(folder, proj)
    return folder


def _fake_convert(monkeypatch, fail_on=None, cancel_on=None, crash_on=None):
    """Подмена core.podcast_reframe.convert_segment: пишет пустой файл, как настоящий конвейер.

    crash_on — KeyboardInterrupt (не Exception): конвейер его не ловит, статус не пишет —
    так проверяется, что готовый рилс уже лежит в podcast.json, а не спасён записью статуса."""
    from core import podcast_reframe
    calls = []

    def fake(proj_dir, proj, seg, index, emit=None, cancel=None, reels_dir=None):
        calls.append(index)
        if crash_on is not None and index == crash_on:
            raise KeyboardInterrupt()
        if cancel_on is not None and index == cancel_on:
            podcast_api.PDJOB["cancel"] = True
            from core.xml2ae.parse import Cancelled
            raise Cancelled()
        if fail_on is not None and index == fail_on:
            raise RuntimeError("ffmpeg упал на куске")
        out = os.path.join(reels_dir or os.path.join(proj_dir, "reels"),
                           f"{proj.get('slug') or 'podcast'}_{index + 1:02d}.mp4")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as fh:
            fh.write(b"mp4")
        seg["reel"] = out
        return out

    monkeypatch.setattr(podcast_reframe, "convert_segment", fake)
    _fake_cams(monkeypatch)
    return calls


def test_перевод_успех_лок_взят_и_отпущен_статус_converted(tmp_path, monkeypatch):
    folder = _ready_project(tmp_path)
    lock = _fake_lock(monkeypatch)
    calls = _fake_convert(monkeypatch)

    podcast_api._convert(folder, None)

    proj = podcast.load_project(folder)
    assert lock == {"taken": 1, "released": 1}
    assert calls == [0, 1]
    assert proj["status"] == "converted"
    assert podcast_api.PDJOB["reels"] == [s["reel"] for s in proj["segments"]]
    assert len(podcast_api.PDJOB["reels"]) == 2
    assert podcast_api.PDJOB["failed"] is False and podcast_api.PDJOB["done"] is True


def test_перевод_падение_второго_куска_первый_рилс_сохранён(tmp_path, monkeypatch):
    folder = _ready_project(tmp_path)
    lock = _fake_lock(monkeypatch)
    _fake_convert(monkeypatch, fail_on=1)

    podcast_api._convert(folder, None)

    proj = podcast.load_project(folder)
    assert lock == {"taken": 1, "released": 1}          # лок отпущен и при ошибке
    assert proj["status"] == "error"
    assert proj["error"] and "ffmpeg упал" in proj["error"]
    assert proj["segments"][0]["reel"].endswith("_01.mp4")   # готовый рилс пережил падение
    assert proj["segments"][1]["reel"] == ""
    assert podcast_api.PDJOB["failed"] is True


def test_перевод_отмена_статус_назад_в_ready_готовые_остаются(tmp_path, monkeypatch):
    folder = _ready_project(tmp_path)
    lock = _fake_lock(monkeypatch)
    _fake_convert(monkeypatch, cancel_on=1)

    podcast_api._convert(folder, None)

    proj = podcast.load_project(folder)
    assert lock == {"taken": 1, "released": 1}
    assert proj["status"] == "ready"
    assert proj["segments"][0]["reel"].endswith("_01.mp4")
    assert podcast_api.PDJOB["cancelled"] is True and podcast_api.PDJOB["failed"] is False


def test_перевод_выбранный_кусок_сохраняет_номер_по_всем_кускам(tmp_path, monkeypatch):
    folder = _ready_project(tmp_path)
    _fake_lock(monkeypatch)
    calls = _fake_convert(monkeypatch)

    podcast_api._convert(folder, ["bbb222"])

    assert calls == [1]                                  # второй кусок — номер 02, не 01
    proj = podcast.load_project(folder)
    assert proj["segments"][0]["reel"] == ""
    assert proj["segments"][1]["reel"].endswith("_02.mp4")


def test_перевод_статус_new_ошибка_без_лока_и_без_смены_статуса(tmp_path, monkeypatch):
    folder = _ready_project(tmp_path, status="new")
    lock = _fake_lock(monkeypatch)
    calls = _fake_convert(monkeypatch)

    podcast_api._convert(folder, None)

    assert calls == [] and lock == {"taken": 0, "released": 0}
    assert podcast.load_project(folder)["status"] == "new"
    assert podcast_api.PDJOB["failed"] is True


def test_перевод_лок_занят_ошибка_и_статус_не_меняется(tmp_path, monkeypatch):
    folder = _ready_project(tmp_path)
    lock = _fake_lock(monkeypatch, ok=False)
    calls = _fake_convert(monkeypatch)

    podcast_api._convert(folder, None)

    assert calls == [] and lock["released"] == 0
    assert podcast.load_project(folder)["status"] == "ready"
    assert podcast_api.PDJOB["failed"] is True


def test_перевод_рилс_на_диске_до_падения_следующего_куска(tmp_path, monkeypatch):
    # Жёсткое падение (не Exception): статус не записывается — первый рилс должен быть
    # в podcast.json уже по своей собственной записи проекта после куска.
    folder = _ready_project(tmp_path)
    _fake_lock(monkeypatch)
    _fake_convert(monkeypatch, crash_on=1)

    with pytest.raises(KeyboardInterrupt):
        podcast_api._convert(folder, None)

    proj = podcast.load_project(folder)
    assert proj["segments"][0]["reel"].endswith("_01.mp4")
    assert proj["segments"][1]["reel"] == ""


# П8: перевод стартует из error и converting (после падения или рестарта), если есть куски

@pytest.mark.parametrize("status", ["error", "converting"])
def test_перевод_из_error_и_converting_с_кусками_идёт(tmp_path, monkeypatch, status):
    folder = _ready_project(tmp_path, status=status)
    lock = _fake_lock(monkeypatch)
    calls = _fake_convert(monkeypatch)

    podcast_api._convert(folder, None)

    proj = podcast.load_project(folder)
    assert calls == [0, 1]
    assert lock == {"taken": 1, "released": 1}
    assert proj["status"] == "converted"
    assert podcast_api.PDJOB["failed"] is False


@pytest.mark.parametrize("status", ["error", "converting"])
def test_перевод_из_error_и_converting_без_кусков_отказ(tmp_path, monkeypatch, status):
    folder = _file_project(tmp_path, status=status)
    lock = _fake_lock(monkeypatch)
    calls = _fake_convert(monkeypatch)

    podcast_api._convert(folder, None)

    assert calls == [] and lock == {"taken": 0, "released": 0}
    assert podcast.load_project(folder)["status"] == status     # отказ не меняет статус
    assert podcast_api.PDJOB["failed"] is True


# --- роуты run / convert / status / cancel: сторож покрытия роутов (test_route_coverage) ---

def _no_thread(monkeypatch):
    """Фоновый поток не стартует: проверяем ответ роута и то, с каким телом поток бы запустился.

    Подмена — по модулю api.podcast (его `threading`), глобальный threading не трогаем."""
    started = []

    class _FakeThread:
        def __init__(self, target=None, args=(), daemon=None):
            self._target, self._args = target, args

        def start(self):
            started.append((self._target, self._args))

    monkeypatch.setattr(podcast_api, "threading",
                        SimpleNamespace(Thread=_FakeThread, Lock=threading.Lock))
    return started


def test_роут_run_запускает_конвейер_и_занимает_джоб(tmp_path, monkeypatch):
    folder = _file_project(tmp_path)
    started = _no_thread(monkeypatch)
    d = _client().post("/api/podcast_run", json={"dir": folder}).get_json()
    assert d["ok"] is True
    assert started == [(podcast_api._pipeline, (folder,))]
    assert podcast_api.PDJOB["running"] is True


def test_роут_convert_запускает_перевод_с_выбранными_кусками(tmp_path, monkeypatch):
    folder = _ready_project(tmp_path)
    started = _no_thread(monkeypatch)
    d = _client().post("/api/podcast_convert",
                       json={"dir": folder, "ids": ["bbb222"]}).get_json()
    assert d["ok"] is True
    assert started == [(podcast_api._convert, (folder, ["bbb222"], ""))]
    assert podcast_api.PDJOB["running"] is True


def test_роут_status_отдаёт_поля_джоба_и_хвост_лога(monkeypatch):
    podcast_api._pemit("первая строка")
    podcast_api._pemit("вторая строка")
    podcast_api._set_pd(running=True, cur="скачивание", pct=12.5)
    c = _client()
    d = c.get("/api/podcast_status").get_json()
    assert d["ok"] is True
    assert d["running"] is True and d["cur"] == "скачивание"
    assert d["log_total"] == 2 and len(d["log"]) == 2
    tail = c.get("/api/podcast_status", query_string={"since": 1}).get_json()
    assert len(tail["log"]) == 1 and tail["log_total"] == 2


def test_роут_cancel_ставит_флаг_джобу(monkeypatch):
    from core import aicut
    calls = []

    def fake_cancel_call():
        # Настоящий cancel_call ставит процессный флаг CANCEL, который утёк бы в другие тесты.
        calls.append(1)
        return 1

    monkeypatch.setattr(aicut, "cancel_call", fake_cancel_call)
    monkeypatch.setattr(podcast_api, "PDPROC", None)
    d = _client().post("/api/podcast_cancel").get_json()
    assert d["ok"] is True
    assert podcast_api.PDJOB["cancel"] is True
    assert calls == [1]


# --- П11: разбор камер (cams.json) в конвейере и при переводе ---

def test_разбор_камер_после_расшифровки_под_локом(tmp_path, monkeypatch):
    from core import podcast_cams
    folder = _file_project(tmp_path)
    lock = _fake_lock(monkeypatch)
    order = []

    def fake_transcribe(f, proj, emit=None, cancel=None):
        order.append("transcribe")
        proj["duration"] = 100.0
        podcast.save_project(f, proj)
        return [{"w": "а", "start": 0.0, "end": 1.0}]

    def fake_analyze(proj_dir, proj, emit=None, cancel=None):
        order.append(("analyze", dict(lock)))
        return {}

    def fake_choose(f, proj, emit=None):
        order.append("choose")
        proj["segments"] = []
        proj["status"] = "ready"
        podcast.save_project(f, proj)
        return []

    monkeypatch.setattr(podcast_api.podcast_ai, "transcribe", fake_transcribe)
    monkeypatch.setattr(podcast_api.podcast_ai, "choose", fake_choose)
    monkeypatch.setattr(podcast_cams, "analyze", fake_analyze)

    podcast_api._pipeline(folder)

    assert order == ["transcribe", ("analyze", {"taken": 1, "released": 0}), "choose"]
    assert lock == {"taken": 1, "released": 1}
    assert podcast.load_project(folder)["status"] == "ready"


def test_разбор_камер_сорвался_статус_error_и_лок_отпущен(tmp_path, monkeypatch):
    from core import podcast_cams
    folder = _file_project(tmp_path)
    lock = _fake_lock(monkeypatch)

    def fake_transcribe(f, proj, emit=None, cancel=None):
        proj["duration"] = 100.0
        podcast.save_project(f, proj)
        return [{"w": "а", "start": 0.0, "end": 1.0}]

    def boom(*a, **k):
        raise RuntimeError("трек не загрузился")

    monkeypatch.setattr(podcast_api.podcast_ai, "transcribe", fake_transcribe)
    monkeypatch.setattr(podcast_cams, "analyze", boom)

    podcast_api._pipeline(folder)

    proj = podcast.load_project(folder)
    assert lock == {"taken": 1, "released": 1}
    assert proj["status"] == "error"
    assert "трек не загрузился" in proj["error"]


def test_продолжить_без_cams_делает_только_разбор(tmp_path, monkeypatch):
    from core import podcast_cams
    folder = _file_project(tmp_path, status="ready")
    proj = podcast.load_project(folder)
    proj["duration"] = 100.0
    podcast.save_project(folder, proj)
    fileio.atomic_json_dump(os.path.join(folder, podcast_api.podcast_ai.WORDS_FILE),
                            [{"w": "а", "start": 0.0, "end": 1.0}])
    lock = _fake_lock(monkeypatch)
    order = []
    monkeypatch.setattr(podcast_api.podcast_ai, "transcribe",
                        lambda *a, **k: order.append("transcribe") or [])
    monkeypatch.setattr(podcast_api.podcast_ai, "choose",
                        lambda *a, **k: order.append("choose") or [])
    monkeypatch.setattr(podcast_cams, "analyze",
                        lambda *a, **k: order.append(("analyze", dict(lock))) or {})

    podcast_api._pipeline(folder)

    assert order == [("analyze", {"taken": 1, "released": 0})]
    assert lock == {"taken": 1, "released": 1}
    assert podcast_api.PDJOB["failed"] is False


def test_продолжить_из_transcribed_сначала_разбор_потом_выбор(tmp_path, monkeypatch):
    from core import podcast_cams
    folder = _file_project(tmp_path, status="transcribed")
    fileio.atomic_json_dump(os.path.join(folder, podcast_api.podcast_ai.WORDS_FILE),
                            [{"w": "а", "start": 0.0, "end": 1.0}])
    _fake_lock(monkeypatch)
    order = []

    def fake_choose(f, proj, emit=None):
        order.append("choose")
        proj["segments"] = []
        proj["status"] = "ready"
        podcast.save_project(f, proj)
        return []

    monkeypatch.setattr(podcast_api.podcast_ai, "transcribe",
                        lambda *a, **k: order.append("transcribe") or [])
    monkeypatch.setattr(podcast_api.podcast_ai, "choose", fake_choose)
    monkeypatch.setattr(podcast_cams, "analyze", lambda *a, **k: order.append("analyze") or {})

    podcast_api._pipeline(folder)

    assert order == ["analyze", "choose"]


def test_перевод_без_cams_сначала_разбор_под_локом(tmp_path, monkeypatch):
    from core import podcast_cams, podcast_reframe
    folder = _ready_project(tmp_path)
    lock = _fake_lock(monkeypatch)
    order = []

    def fake_analyze(proj_dir, proj, emit=None, cancel=None):
        order.append(("analyze", dict(lock)))
        return {}

    def fake_convert(proj_dir, proj, seg, index, emit=None, cancel=None, reels_dir=None):
        order.append(("convert", index))
        out = os.path.join(proj_dir, "reels", f"podcast_{index + 1:02d}.mp4")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as fh:
            fh.write(b"mp4")
        seg["reel"] = out
        return out

    monkeypatch.setattr(podcast_cams, "analyze", fake_analyze)
    monkeypatch.setattr(podcast_reframe, "convert_segment", fake_convert)

    podcast_api._convert(folder, None)

    assert order == [("analyze", {"taken": 1, "released": 0}), ("convert", 0), ("convert", 1)]
    assert lock == {"taken": 1, "released": 1}
    assert podcast.load_project(folder)["status"] == "converted"


def test_перевод_с_годным_cams_разбор_не_зовётся(tmp_path, monkeypatch):
    from core import podcast_cams
    folder = _ready_project(tmp_path)
    proj = podcast.load_project(folder)
    st = os.stat(proj["source"])
    fileio.atomic_json_dump(os.path.join(folder, "cams.json"), {
        "v": 1, "src": os.path.abspath(proj["source"]), "size": st.st_size, "mtime": st.st_mtime,
        "src_w": 1920, "src_h": 1080, "scene_thr": 0.1, "shots": [], "angles": [],
        "angle_info": {}, "shot_heads": {}})
    _fake_lock(monkeypatch)
    _fake_convert(monkeypatch)

    def no_analyze(*a, **k):
        raise AssertionError("годный cams.json — разбор не нужен")

    monkeypatch.setattr(podcast_cams, "analyze", no_analyze)

    podcast_api._convert(folder, None)

    assert podcast.load_project(folder)["status"] == "converted"


def test_emit_разбора_камер_отдаёт_шаблон_а_не_текст():
    # wrap_emit узнаёт колбэк по имени emit/remit/_emit: только так шаблон с переменными
    # доходит до клиента и переводится по en.json, а не печатается русским текстом.
    from core.app_meta import wrap_emit
    assert wrap_emit(podcast_api._emit) is podcast_api._emit
    podcast_api.PDJOB["log"] = []
    wrap_emit(podcast_api._emit)("склейки: {n}", n=3)
    entry = podcast_api.PDJOB["log"][-1]
    assert entry == {"t": "склейки: {n}", "v": {"n": 3}}


# --- П16: дорожка проекта по ссылке — оригинал выбирает сервер, а не yt-dlp ---

def _patch_probe(monkeypatch, rc=0, err=""):
    """yt-dlp -J подменён: отдаёт фикстуру (rc=0) или код ошибки. Возвращает список вызовов."""
    raw = open(FIXTURE, encoding="utf-8").read()
    calls = []

    def fake(cmd, *a, **k):
        calls.append(cmd)
        return SimpleNamespace(returncode=rc, stdout=raw if rc == 0 else "", stderr=err)

    monkeypatch.setattr(podcast_api.subprocess, "run", fake)
    return calls


def _create_yt(root, **extra):
    body = {"root": str(root), "url": YT, "title": "Ссылка", "reels": 5}
    body.update(extra)
    return _client().post("/api/podcast_create", json=body).get_json()


def test_создание_по_ссылке_без_дорожки_берёт_оригинал(tmp_path, monkeypatch):
    """Без format_id сервер сам проверяет дорожки и кладёт оригинал — не отдаёт выбор yt-dlp."""
    calls = _patch_probe(monkeypatch)
    d = _create_yt(tmp_path / "podcasts")
    assert d["ok"] is True
    assert d["project"]["audio_track"] == {"format_id": "140-23", "lang": "en-US", "original": True}
    assert len(calls) == 1


def test_создание_проверка_упала_проект_не_создан(tmp_path, monkeypatch):
    """Проверка упала — отказ с причиной, папка проекта не появилась."""
    _patch_probe(monkeypatch, rc=1, err="ERROR: Video unavailable")
    root = tmp_path / "podcasts"
    d = _create_yt(root)
    assert not d.get("ok")      # ошибка — без поля ok, как у всех роутов
    assert d.get("error")
    assert os.listdir(str(root)) == []


def test_создание_с_format_id_дубляжа_дорожка_не_оригинал(tmp_path, monkeypatch):
    """Клиент прислал id дубляжа — в проекте он с языком и без пометки оригинала."""
    _patch_probe(monkeypatch)
    d = _create_yt(tmp_path / "podcasts", format_id="140-14")
    assert d["ok"] is True
    assert d["project"]["audio_track"] == {"format_id": "140-14", "lang": "ru", "original": False}


def test_создание_format_id_которого_нет_у_видео_отказ(tmp_path, monkeypatch):
    """Чужой id не уходит в проект молча: тихо скачался бы звук наугад."""
    _patch_probe(monkeypatch)
    root = tmp_path / "podcasts"
    d = _create_yt(root, format_id="999-9")
    assert not d.get("ok")      # ошибка — без поля ok, как у всех роутов
    assert os.listdir(str(root)) == []


def test_дорожка_проекта_доходит_до_селектора_скачивания(tmp_path, monkeypatch):
    """Конвейер скачивает по format_id проекта: в селекторе ba[format_id=…], не голый bv*+ba/b."""
    _patch_probe(monkeypatch)
    d = _create_yt(tmp_path / "podcasts")
    folder = d["dir"]
    captured = []

    def fake_run(cmd, emit, stat=None):
        captured.append(cmd)
        return 1   # скачивание «не удалось» — нам нужна только команда

    monkeypatch.setattr(podcast_api, "_run_ytdlp", fake_run)
    podcast_api._pipeline(folder)
    assert captured, "yt-dlp не вызван"
    sel = captured[0][captured[0].index("-f") + 1]
    assert sel == "bv*+ba[format_id=140-23]/bv*+ba/b"


def test_список_дорожка_у_ссылки_и_не_у_файла(tmp_path, monkeypatch):
    """На карточке — дорожка подкаста по ссылке; у файла с диска её нет."""
    _patch_probe(monkeypatch)
    root = tmp_path / "podcasts"
    _create_yt(root)
    _file_project(tmp_path, title="Файл")
    items = _client().get("/api/podcast_list", query_string={"root": str(root)}).get_json()["items"]
    by = {it["title"]: it for it in items}
    assert by["Ссылка"]["audio_track"] == {"format_id": "140-23", "lang": "en-US", "original": True}
    assert by["Файл"]["audio_track"] is None


def test_проект_без_дорожки_качает_оригинал_и_записывает_его(tmp_path, monkeypatch):
    """П17 (хвост П16): проект по ссылке, созданный до П16, без audio_track. Конвейер сам берёт
    оригинал, пишет его в проект и качает с ним: в селекторе ba[format_id=<оригинал>], не голый bv*+ba/b."""
    _patch_probe(monkeypatch)
    folder = str(tmp_path / "podcasts" / "Старый")
    proj = podcast.new_project("Старый", YT, "", 5)
    proj["status"] = "new"
    podcast.save_project(folder, proj)
    captured = []

    def fake_run(cmd, emit, stat=None):
        captured.append(cmd)
        return 1   # скачивание «не удалось» — нам нужна только команда

    monkeypatch.setattr(podcast_api, "_run_ytdlp", fake_run)
    podcast_api._pipeline(folder)
    assert captured, "yt-dlp не вызван"
    sel = captured[0][captured[0].index("-f") + 1]
    assert sel == "bv*+ba[format_id=140-23]/bv*+ba/b"
    saved = podcast.load_project(folder)
    assert saved["audio_track"] == {"format_id": "140-23", "lang": "en-US", "original": True}


# --- П-новое (2026-10-11): корзина, «Все рилсы в очередь», папка рилсов спикера ---

def _speaker(monkeypatch, tmp_path, key, cam1):
    """Профиль спикера в во временной папке speakers: camdirs[0] = cam1 (пусто — без папки)."""
    import json
    from core import speakers
    d = tmp_path / "speakers"
    d.mkdir(exist_ok=True)
    camdirs = [cam1] if cam1 else []
    (d / f"{key}.json").write_text(json.dumps({"label": key, "camdirs": camdirs}, ensure_ascii=False),
                                   encoding="utf-8")
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(d))
    return key


def _ready_folder(root, title, segs):
    """Проект «ready» с готовыми кусками в root/title (без файла на диске)."""
    folder = os.path.join(str(root), title)
    proj = podcast.new_project(title, "", "", 5)
    proj["status"] = "ready"
    proj["duration"] = 100.0
    proj["segments"] = segs
    podcast.save_project(folder, proj)
    return folder


def _seg(sid, start, end, reel=""):
    return {"id": sid, "start": float(start), "end": float(end), "title": "", "why": "", "reel": reel}


def _fake_convert_dirs(monkeypatch, fail_title=None):
    """Фейк convert_segment, который видит папку рилсов: пишет `<папка>/<slug>_NN.mp4`.

    Возвращает список (папка рилсов) по каждому вызову. fail_title — падение куска
    подкаста с таким названием (первый кусок)."""
    from core import podcast_reframe
    seen = []

    def fake(proj_dir, proj, seg, index, emit=None, cancel=None, reels_dir=None):
        seen.append(reels_dir)
        if fail_title is not None and proj.get("title") == fail_title and index == 0:
            raise RuntimeError("ffmpeg упал на куске")
        d = reels_dir or os.path.join(proj_dir, "reels")
        out = os.path.join(d, f"{proj.get('slug') or 'podcast'}_{index + 1:02d}.mp4")
        os.makedirs(d, exist_ok=True)
        with open(out, "wb") as fh:
            fh.write(b"mp4")
        seg["reel"] = out
        return out

    monkeypatch.setattr(podcast_reframe, "convert_segment", fake)
    _fake_cams(monkeypatch)
    return seen


# --- корзина ---

def test_удаление_переносит_папку_в_корзину_а_не_стирает(tmp_path):
    folder = _file_project(tmp_path)
    root = os.path.dirname(folder)
    c = _client()
    d = c.post("/api/podcast_delete", json={"dir": folder}).get_json()
    assert d["ok"] is True
    assert not os.path.exists(folder)
    # trash — id записи; сама запись: _reelsi_trash/<id>/<имя подкаста>/ + trash.json
    assert re.match(r"\d{8}-\d{6}_", d["trash"])
    entry = os.path.join(root, "_reelsi_trash", d["trash"])
    assert os.path.isfile(os.path.join(entry, os.path.basename(folder), "podcast.json"))   # проект цел
    assert fileio.json_load_soft(os.path.join(entry, "trash.json"))["kind"] == "podcast"


def test_удаление_подкаста_с_идущим_джобом_отказ(tmp_path):
    folder = _file_project(tmp_path)
    podcast_api._set_pd(running=True, dir=folder)
    d = _client().post("/api/podcast_delete", json={"dir": folder}).get_json()
    assert d.get("err") == "podcast_busy"
    assert "дождись" in d["error"]
    assert os.path.isfile(podcast.project_path(folder))            # папка на месте


def test_удаление_подкаста_из_пачки_все_рилсы_отказ(tmp_path):
    folder = _ready_project(tmp_path)
    root = os.path.dirname(folder)
    podcast_api._set_pd(running=True, dir=root, folders=[folder])
    d = _client().post("/api/podcast_delete", json={"dir": folder}).get_json()
    assert d.get("err") == "podcast_busy"
    assert os.path.isfile(podcast.project_path(folder))


def test_удаление_другого_подкаста_при_идущем_джобе_разрешено(tmp_path):
    folder = _file_project(tmp_path)
    other = _ready_folder(os.path.dirname(folder), "Другой", [])
    podcast_api._set_pd(running=True, dir=other)
    d = _client().post("/api/podcast_delete", json={"dir": folder}).get_json()
    assert d["ok"] is True
    assert not os.path.exists(folder)


def test_список_не_показывает_корзину(tmp_path):
    root = tmp_path / "podcasts"
    podcast.save_project(str(root / "first"), podcast.new_project("first", "", "", 3))
    podcast.save_project(str(root / "_reelsi_trash" / "20260101-000000_old"),
                         podcast.new_project("old", "", "", 3))
    d = _client().get("/api/podcast_list", query_string={"root": str(root)}).get_json()
    assert [i["title"] for i in d["items"]] == ["first"]


# --- папка рилсов: камера 1 спикера или папка подкаста ---

def test_перевод_со_спикером_рилсы_в_камеру_1(tmp_path, monkeypatch):
    folder = _ready_project(tmp_path)
    cam1 = tmp_path / "cam1"
    cam1.mkdir()
    key = _speaker(monkeypatch, tmp_path, "Иван", str(cam1))
    started = _no_thread(monkeypatch)
    d = _client().post("/api/podcast_convert", json={"dir": folder, "ids": [], "speaker": key}).get_json()
    assert d["ok"] is True and d["fallback"] is False
    assert started[0][1][2] == os.path.abspath(str(cam1))


def test_перевод_без_спикера_рилсы_в_папке_подкаста_с_флагом(tmp_path, monkeypatch):
    folder = _ready_project(tmp_path)
    started = _no_thread(monkeypatch)
    d = _client().post("/api/podcast_convert", json={"dir": folder, "ids": []}).get_json()
    assert d["ok"] is True and d["fallback"] is True
    assert started[0][1][2] == ""                      # пусто — `<подкаст>/reels`


def test_спикер_без_папки_камеры_1_fallback(tmp_path, monkeypatch):
    folder = _ready_project(tmp_path)
    key = _speaker(monkeypatch, tmp_path, "Пётр", str(tmp_path / "нет_такой_папки"))
    started = _no_thread(monkeypatch)
    d = _client().post("/api/podcast_convert", json={"dir": folder, "ids": [], "speaker": key}).get_json()
    assert d["fallback"] is True
    assert started[0][1][2] == ""


def test_перевод_передаёт_папку_рилсов_в_конвейер_куска(tmp_path, monkeypatch):
    folder = _ready_project(tmp_path)
    cam1 = str(tmp_path / "cam1")
    seen = _fake_convert_dirs(monkeypatch)
    _lock = _fake_lock(monkeypatch)
    podcast_api._convert(folder, None, cam1)
    assert seen == [cam1, cam1]
    assert all(os.path.dirname(r) == cam1 for r in podcast_api.PDJOB["reels"])


# --- «Все рилсы в очередь» ---

def test_все_рилсы_один_лок_готовые_не_переводятся_порядок_по_списку(tmp_path, monkeypatch):
    root = tmp_path / "podcasts"
    first = _ready_folder(root, "Первый", [_seg("a1", 10, 40), _seg("a2", 50, 80)])
    # первый кусок уже с рилсом на диске — переводим только второй
    done = os.path.join(first, "reels", "Первый_01.mp4")
    os.makedirs(os.path.dirname(done), exist_ok=True)
    open(done, "wb").write(b"mp4")
    proj = podcast.load_project(first)
    proj["segments"][0]["reel"] = done
    podcast.save_project(first, proj)
    second = _ready_folder(root, "Второй", [_seg("b1", 5, 25)])
    lock = _fake_lock(monkeypatch)
    seen = _fake_convert_dirs(monkeypatch)

    podcast_api._convert_all(str(root), [first, second], "")

    assert lock == {"taken": 1, "released": 1}           # один захват лока на весь пакет
    assert len(seen) == 2                                # Первый: один кусок; Второй: один кусок
    reels = podcast_api.PDJOB["reels"]
    assert reels[0] == done and len(reels) == 3          # все рилсы, по порядку подкастов
    assert [os.path.basename(r) for r in reels] == ["Первый_01.mp4", "Первый_02.mp4", "Второй_01.mp4"]
    assert podcast.load_project(first)["status"] == "converted"
    assert podcast.load_project(second)["status"] == "converted"
    assert podcast_api.PDJOB["failed"] is False and podcast_api.PDJOB["done"] is True


def test_все_рилсы_ошибка_одного_подкаста_не_останавливает_остальные(tmp_path, monkeypatch):
    root = tmp_path / "podcasts"
    bad = _ready_folder(root, "Первый", [_seg("a1", 10, 40)])
    good = _ready_folder(root, "Второй", [_seg("b1", 5, 25)])
    _fake_lock(monkeypatch)
    _fake_convert_dirs(monkeypatch, fail_title="Первый")

    podcast_api._convert_all(str(root), [bad, good], "")

    assert podcast.load_project(bad)["status"] == "error"
    assert podcast.load_project(good)["status"] == "converted"
    assert podcast_api.PDJOB["failed"] is True           # джоб в целом не удался, но второй подкаст сделан
    assert len(podcast_api.PDJOB["reels"]) == 1


def test_все_рилсы_лок_занят_статусы_не_меняются(tmp_path, monkeypatch):
    root = tmp_path / "podcasts"
    one = _ready_folder(root, "Первый", [_seg("a1", 10, 40)])
    _fake_lock(monkeypatch, ok=False)
    _fake_convert_dirs(monkeypatch)
    podcast_api._convert_all(str(root), [one], "")
    assert podcast.load_project(one)["status"] == "ready"
    assert podcast_api.PDJOB["failed"] is True


def test_роут_все_рилсы_запускает_пачку_и_отдаёт_fallback(tmp_path, monkeypatch):
    root = tmp_path / "podcasts"
    _ready_folder(root, "Первый", [_seg("a1", 10, 40)])
    _ready_folder(root, "Второй", [_seg("b1", 5, 25)])
    started = _no_thread(monkeypatch)
    d = _client().post("/api/podcast_convert_all", json={"root": str(root), "speaker": ""}).get_json()
    assert d["ok"] is True and d["count"] == 2 and d["fallback"] is True
    target, args = started[0]
    assert target is podcast_api._convert_all
    assert sorted(args[1]) == sorted([os.path.join(str(root), "Первый"), os.path.join(str(root), "Второй")])
    assert args[2] == ""


def test_роут_все_рилсы_без_готовых_подкастов_отказ(tmp_path):
    root = tmp_path / "podcasts"
    podcast.save_project(str(root / "Новый"), podcast.new_project("Новый", "", "", 3))  # статус new
    d = _client().post("/api/podcast_convert_all", json={"root": str(root)}).get_json()
    assert d.get("err") == "podcast_none_ready"


def test_роут_все_рилсы_не_стартует_при_идущем_джобе(tmp_path):
    root = tmp_path / "podcasts"
    _ready_folder(root, "Первый", [_seg("a1", 10, 40)])
    podcast_api._set_pd(running=True, dir=str(root / "Первый"))
    d = _client().post("/api/podcast_convert_all", json={"root": str(root)}).get_json()
    assert d.get("err") == "podcast_busy"
