# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание IC: API и процессы — тело запроса, наборы, сторож rclone и aerender.

Файл бьёт по пунктам задания IC (о метках заданий — `.github/CONTRIBUTING.md`):

1. `/api/render_run` и `/api/build_run`: кривой набор — внятный `umsg` и НИКОГДА
   не 500; «файл не найден» — только про пропавший файл.
2. Сторож-тест: ЛЮБОЙ POST-роут `/api/*` (кроме `/api/pick*`) на тело из чисел
   вместо строк и тела не-объекты ([1, 2], "x", 5) отвечает не 500. `Thread.start` и `Popen`
   подменены — ни один процесс и ни один поток не запускается.
3. Отмена скачивания с гугл-диска: `failed=False`, `cancelled=True`.
4. Сторож rclone: активность — только ИЗМЕНЕНИЕ прогресса; строка, роняющая
   разбор, не убивает поток чтения.
5. Сторож простоя у набора aerender (`_run_proc_batch`), как у одиночного.
6. `kill_tree` — одна реализация на пакет для трёх мест.
7. `/api/swap_cam` с нецелым `cam` — `bad_cam`, а не «Камеру 1 менять нельзя».
8. `/api/media`: расширение → секрет → существование; `mpg`/`mpeg` отдаются.
9. `/api/export_xml`: только `.xml`, иначе 403.
10. `save_profile`: ключ-маска + смена адреса/провайдера — отказ, ключ не записан.

Запуск: python -m pytest tests/test_r8_ic_api.py -q
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import api  # noqa: E402
from api import _core, gdrive, render  # noqa: E402

H = {"Host": "127.0.0.1:5001"}


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture(autouse=True)
def _reset_jobs(tmp_path, monkeypatch):
    """Изоляция общих состояний и ЛИЧНЫХ файлов (термины, папка камер, индекс).

    RJOB/GDJOB/ILL_JOB — общие на процесс: их хвосты иначе приезжают в соседние
    файлы. `terms.json` и папка камер — данные пользователя, и сторож-тест ниже
    обходит ВСЕ POST-роуты: без подмены он записал бы пустой словарь терминов и
    создал «камера1» в рабочей папке пользователя.
    """
    from core import terms as _terms
    from api import files as _files, inserts as _inserts
    monkeypatch.setattr(_terms, "TERMS_PATH", str(tmp_path / "terms.json"))
    monkeypatch.setattr(_files, "DEFAULT_BASE", str(tmp_path / "медиа"))
    yield
    with gdrive.GDLOCK:
        gdrive.GDJOB.update(gdrive.GDFRESH, running=False, done=False, failed=None,
                            log=[], log_base=0, url="", started=0)
    with render.RLOCK:
        render.RJOB.update(running=False, done=False, log=[], pct=None, cur="", ae="",
                           out_dir="", result=[], failed=[], cancel=False, items=[])
    with _inserts.ILL_LOCK:
        _inserts.ILL_JOB.update(running=False, done=0, total=0, log=[], error="")


# --------------------------------------------------------------------------- #
# 1. Кривой набор в /api/render_run и /api/build_run — не 500 и внятный код
# --------------------------------------------------------------------------- #
@pytest.fixture
def xml_file(tmp_path):
    """Живой XML: без него `_norm_build_jobs` падает раньше проверки полей —
    «файл не найден» вместо разбора «exposure»/«style»."""
    p = tmp_path / "01_clip.xml"
    p.write_text("<xmeml version='4'><sequence></sequence></xmeml>", encoding="utf-8")
    return str(p)


@pytest.mark.parametrize("jobs", [
    [123],                                                   # элемент набора — не объект
    [{"xml": None, "style": 5}],                             # стиль — число
    [{"xml": None, "exposure": "abc"}],                       # Exposure — не число
])
def test_render_run_bad_set_is_not_500_and_not_file_not_found(client, monkeypatch,
                                                              xml_file, jobs):
    """Три тела из задания: `{"jobs":[123]}`, `"style": 5`, `"exposure": "abc"`."""
    jobs = [{**j, "xml": xml_file} if isinstance(j, dict) else j for j in jobs]
    monkeypatch.setattr("threading.Thread.start", lambda self: None)
    r = client.post("/api/render_run", json={"jobs": jobs}, headers=H)
    assert r.status_code == 200, r.status_code
    d = r.get_json()
    assert d.get("ok") is not True
    assert d.get("err") == "render_set_invalid", d
    assert not render.RJOB["running"]


def test_render_run_missing_file_is_file_not_found(client, monkeypatch):
    """Настоящая пропажа файла по-прежнему `file_not_found` с путём, а не набор."""
    monkeypatch.setattr("threading.Thread.start", lambda self: None)
    r = client.post("/api/render_run", json={"jobs": [{"xml": "нет-такого.xml"}]},
                    headers=H)
    d = r.get_json()
    assert d["err"] == "file_not_found" and d["err_vars"]["path"] == "нет-такого.xml", d


def test_render_run_exposure_error_names_the_field(client, monkeypatch, xml_file):
    """Сообщение обязано называть ПОЛЕ, а не «could not convert string to float»."""
    monkeypatch.setattr("threading.Thread.start", lambda self: None)
    d = client.post("/api/render_run",
                    json={"jobs": [{"xml": xml_file, "exposure": "abc"}]},
                    headers=H).get_json()
    assert "exposure" in d["error"], d
    assert "float" not in d["error"], d


def test_build_run_bad_set_is_not_file_not_found(client, monkeypatch, xml_file):
    """Тот же дефект был и у сборки .jsx: `_norm_build_jobs` — общая для двух роутов."""
    monkeypatch.setattr("threading.Thread.start", lambda self: None)
    d = client.post("/api/build_run",
                    json={"jobs": [{"xml": xml_file, "exposure": "abc"}]},
                    headers=H).get_json()
    assert d.get("err") == "build_set_invalid", d
    assert "exposure" in d["error"], d


@pytest.mark.parametrize("url", ["/api/render_run", "/api/build_run"])
def test_render_and_build_survive_non_object_body(client, monkeypatch, url):
    """Тело-массив (не объект) — 400 bad_body на границе Blueprint."""
    monkeypatch.setattr("threading.Thread.start", lambda self: None)
    r = client.post(url, json=[1, 2], headers=H)
    assert r.status_code == 400 and r.get_json().get("err") == "bad_body", r.get_json()


# --------------------------------------------------------------------------- #
# 2. Сторож: ни один POST-роут не отвечает 500 на числа и тела не-объекты
# --------------------------------------------------------------------------- #
NUMBERS = {"xml": 123, "name": 123, "path": 123, "dir": 123, "dest": 123, "url": 123,
           "query": 123, "outdir": 123, "text": 123, "model": 123}


def _post_routes():
    """Все POST-роуты /api/* из карты приложения (кроме /api/pick*)."""
    from webui import app
    out = []
    for rule in app.url_map.iter_rules():
        if not str(rule).startswith("/api/"):
            continue
        if str(rule).startswith("/api/pick"):
            continue                                  # нативные диалоги выбора файла
        if "POST" not in (rule.methods or ()):
            continue
        out.append(str(rule))
    return sorted(out)


def test_all_post_routes_survive_numeric_body(monkeypatch):
    """`{"xml": 123, …}` — ни одного 500: тело разбирается, а не падает.

    Ничего не запускаем и никуда не ходим: `Thread.start`, `Popen`/`run` и сеть
    подменены заглушками, профили ИИ/видео — пустые. Иначе сторож-тест сам стал бы
    живым вызовом провайдера на машине с настроенным `ai_config.json`.
    """
    import urllib.error
    import urllib.request
    from webui import app
    from core import aicut
    app.config["TESTING"] = True

    monkeypatch.setattr("threading.Thread.start", lambda self: None)
    monkeypatch.setattr("subprocess.Popen",
                        lambda *a, **k: pytest.fail("на кривом теле запущен процесс"))
    monkeypatch.setattr("subprocess.run", lambda *a, **k: None)
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(
                            urllib.error.URLError("сеть в тестах запрещена")))
    monkeypatch.setattr(aicut, "resolve_video_profile", lambda *a, **k: None)
    monkeypatch.setattr(aicut, "_ask_openai", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(aicut, "_ask_anthropic", lambda *a, **k: {"ok": True})

    routes = _post_routes()
    assert len(routes) >= 40, f"роутов подозрительно мало: {routes}"
    bad = []
    with app.test_client() as c:
        for url in routes:
            r = c.post(url, json=dict(NUMBERS), headers=H)
            if r.status_code >= 500:
                bad.append((url, r.status_code, r.get_data(as_text=True)[:200]))
    assert not bad, f"500 на числах в теле: {bad}"


@pytest.mark.parametrize("bad_body", [[1, 2], "x", 5])
def test_all_post_routes_survive_non_object_body(monkeypatch, bad_body):
    """Тело не объект (`[1, 2]`, `"x"`, `5`) — ни одного 500: граница Blueprint отдаёт 400."""
    import urllib.error
    import urllib.request
    from webui import app
    from core import aicut
    app.config["TESTING"] = True

    monkeypatch.setattr("threading.Thread.start", lambda self: None)
    monkeypatch.setattr("subprocess.Popen",
                        lambda *a, **k: pytest.fail("на кривом теле запущен процесс"))
    monkeypatch.setattr("subprocess.run", lambda *a, **k: None)
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(
                            urllib.error.URLError("сеть в тестах запрещена")))
    monkeypatch.setattr(aicut, "resolve_video_profile", lambda *a, **k: None)
    monkeypatch.setattr(aicut, "_ask_openai", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(aicut, "_ask_anthropic", lambda *a, **k: {"ok": True})

    routes = _post_routes()
    assert len(routes) >= 40, f"роутов подозрительно мало: {routes}"
    bad = []
    with app.test_client() as c:
        for url in routes:
            r = c.post(url, json=bad_body, headers=H)
            if r.status_code >= 500:
                bad.append((url, r.status_code, r.get_data(as_text=True)[:200]))
    assert not bad, f"500 на теле {bad_body!r}: {bad}"


# --------------------------------------------------------------------------- #
# 3. Отмена скачивания — не ошибка
# --------------------------------------------------------------------------- #
def test_gdrive_cancel_is_not_failure(monkeypatch):
    """«Стоп» ставит cancelled=True и failed=False: страница не рисует красный тост."""
    monkeypatch.setattr(gdrive, "_run_rclone", lambda *a, **k: 1)   # убитый rclone = код 1
    with gdrive.GDLOCK:
        gdrive.GDJOB.update(gdrive.GDFRESH, running=True, done=False, failed=None, log=[])
        gdrive.GDJOB["cancel"] = True
    gdrive._download_job(["rclone"], "https://drive.google.com/file/d/abcdefghijkl/view")
    assert gdrive.GDJOB["cur"] == "скачивание остановлено"
    assert gdrive.GDJOB["failed"] is False
    assert gdrive.GDJOB["cancelled"] is True


def test_gdrive_failure_still_failure(monkeypatch):
    """Обычное падение rclone остаётся ошибкой, и cancelled не выставляется."""
    monkeypatch.setattr(gdrive, "_run_rclone", lambda *a, **k: 3)
    with gdrive.GDLOCK:
        gdrive.GDJOB.update(gdrive.GDFRESH, running=True, done=False, failed=None, log=[])
    gdrive._download_job(["rclone"], "https://drive.google.com/file/d/abcdefghijkl/view")
    assert gdrive.GDJOB["failed"] is True
    assert gdrive.GDJOB["cancelled"] is False


def test_gdrive_status_exposes_cancelled(client):
    """Ключ `cancelled` обязан доехать до страницы — по нему выбирается статус."""
    with gdrive.GDLOCK:
        gdrive.GDJOB.update(gdrive.GDFRESH, running=False, done=True, failed=False,
                            cancelled=True, log=[])
    d = client.get("/api/gdrive_status", headers=H).get_json()
    assert d["cancelled"] is True and d["failed"] is False
    assert "cancelled" in gdrive.GDFRESH, "флаг не сбрасывается при новом скачивании"


# --------------------------------------------------------------------------- #
# 4. Сторож rclone: активность — изменение прогресса, а не любая строка
# --------------------------------------------------------------------------- #
class _Proc:
    """Поддельный rclone: строки кончаются — процесс «выходит»."""

    def __init__(self, lines, code=0):
        self._lines = list(lines)
        self._code = code
        self._done = False
        self.pid = 424242
        self.stdout = self._stream()

    def _stream(self):
        for ln in self._lines:
            yield ln
        self._done = True

    def poll(self):
        return self._code if self._done else None

    def wait(self):
        return self._code

    def kill(self):
        self._done = True


def _run_fake(lines, monkeypatch, kill_log, stall=0.5, code=0):
    """Прогнать _run_rclone на поддельном процессе. Возвращает (rc, лог)."""
    monkeypatch.setattr(gdrive, "RCLONE_STALL_SEC", stall)
    monkeypatch.setattr(gdrive.subprocess, "Popen", lambda *a, **k: _Proc(lines, code))
    monkeypatch.setattr(gdrive, "_kill_proc", lambda p: kill_log.append(p))
    log = []
    return gdrive._run_rclone(["rclone"], log.append), log


class _StalledProc(_Proc):
    """rclone, который печатает ОДИН И ТОТ ЖЕ блок статистики по таймеру:
    ровно то, ради чего сторож переделан (задание IC, п. 4).

    Строк конечное число и печатаются они медленно: на новом коде передача
    признаётся вставшей за RCLONE_STALL_SEC, на старом «активность» обновлялась
    каждой строкой, поток доигрывал до конца и сторож не срабатывал вовсе.
    """

    def __init__(self, line, count=40, period=0.05):
        self._line = line
        self._count = count
        self._period = period
        self._killed = False
        self._done = False
        self._code = 0
        self.pid = 424243
        self.stdout = self._stream()

    def _stream(self):
        import time
        for _ in range(self._count):
            if self._killed:
                break
            time.sleep(self._period)
            yield self._line
        self._done = True

    def poll(self):
        return 0 if self._done else None


def test_rclone_same_stats_is_a_stall(monkeypatch):
    """Блок статистики по таймеру с ТЕМИ ЖЕ байтами — не активность: снимаем."""
    line = "Transferred:   \t  512 MiB / 1.024 GiB, 50%, 0 B/s, ETA -"
    kill_log, out = [], []
    monkeypatch.setattr(gdrive, "RCLONE_STALL_SEC", 0.4)
    proc = _StalledProc(line)
    monkeypatch.setattr(gdrive.subprocess, "Popen", lambda *a, **k: proc)

    def _kill(p):
        kill_log.append(p)
        p._killed = True            # убитый процесс закрывает stdout — насос завершается

    monkeypatch.setattr(gdrive, "_kill_proc", _kill)
    rc = gdrive._run_rclone(["rclone"], out.append)
    assert rc == gdrive._RCLONE_STALLED
    assert kill_log, "вставшая передача должна быть снята сторожем"
    assert any("не отвечает" in ln for ln in out), out


def test_rclone_growing_bytes_keeps_download_alive(monkeypatch):
    """Растущие байты — передача идёт: сторож не срабатывает даже при малом пороге."""
    lines = [f"Transferred:   \t  {p} MiB / 1.024 GiB, {p}%, 10 MiB/s, ETA 1m"
             for p in range(0, 100, 5)]
    lines.append("2026/08/12 12:00:00 INFO  : IMG.MOV: Copied (new)")
    kill_log = []
    monkeypatch.setattr(gdrive, "RCLONE_STALL_SEC", 0.05)
    monkeypatch.setattr(gdrive.subprocess, "Popen", lambda *a, **k: _Proc(lines))
    monkeypatch.setattr(gdrive, "_kill_proc", lambda p: kill_log.append(p))
    rc = gdrive._run_rclone(["rclone"], lambda *_: None)
    assert rc == 0
    assert not kill_log, "растущая передача снята сторожем"


def test_rclone_broken_line_does_not_kill_the_reader(monkeypatch):
    """Строка, роняющая разбор, логируется — поток чтения жив, хвост дочитан.

    Раньше исключение вылетало из цикла чтения, поток умирал, активность замирала
    и сторож снимал ЗДОРОВОЕ скачивание через 10 минут.
    """
    class _Bad:
        def strip(self, *a):
            raise RuntimeError("строка, роняющая разбор")

        def startswith(self, *a):
            raise RuntimeError("строка, роняющая разбор")

    class _ProcBad(_Proc):
        def _stream(self):
            yield _Bad()
            yield "2026/08/12 12:00:00 INFO  : IMG.MOV: Copied (new)"
            self._done = True

    monkeypatch.setattr(gdrive, "RCLONE_STALL_SEC", 5.0)
    monkeypatch.setattr(gdrive.subprocess, "Popen", lambda *a, **k: _ProcBad([]))
    monkeypatch.setattr(gdrive, "_kill_proc", lambda p: pytest.fail("здоровое скачивание снято"))
    log = []
    rc = gdrive._run_rclone(["rclone"], log.append)
    assert rc == 0
    assert any("Copied (new)" in ln for ln in log), log   # поток дочитал до конца


def test_rclone_dead_reader_is_warned_once_and_not_killed(monkeypatch):
    """Поток чтения умер сам (труба порвалась) — предупреждение один раз, без снятия.

    Активность после этого взять неоткуда, и убивать процесс «по простою» нельзя:
    он может молча дописывать файл. Снимает его только «Стоп».
    """
    import time

    class _DeadReader:
        def __init__(self):
            self.pid = 999
            self._t0 = time.time()
            self.stdout = self._stream()

        def _stream(self):
            raise RuntimeError("труба порвалась")
            yield ""                         # генератор: исключение при первой итерации

        def poll(self):
            return 0 if time.time() - self._t0 > 0.4 else None

        def wait(self):
            return 0

    monkeypatch.setattr(gdrive, "RCLONE_STALL_SEC", 0.05)     # порог МЕНЬШЕ времени жизни
    monkeypatch.setattr(gdrive.subprocess, "Popen", lambda *a, **k: _DeadReader())
    monkeypatch.setattr(gdrive, "_kill_proc",
                        lambda p: pytest.fail("скачивание снято по простою без потока чтения"))
    log = []
    rc = gdrive._run_rclone(["rclone"], log.append)
    assert rc == 0
    warn = [ln for ln in log if "поток чтения" in ln]
    assert len(warn) == 1, log


# --------------------------------------------------------------------------- #
# 5. Сторож простоя у набора aerender
# --------------------------------------------------------------------------- #
class _SilentAerender:
    """Молчащий aerender: строки-то идут, но пустые (AE так и молчит), процесс
    сам не выходит. Конечное число строк — чтобы на старом коде (без сторожа)
    цикл чтения завершился, а не висел вечно."""

    def __init__(self, *a, **k):
        self.pid = 515151
        self.stdout = self._stream()
        self._killed = False

    def _stream(self):
        import time
        for _ in range(40):
            if self._killed:
                break
            time.sleep(0.05)
            yield ""

    def poll(self):
        return 1 if self._killed else None

    def wait(self):
        return 1

    def kill(self):
        self._killed = True


def test_run_proc_batch_stall_watchdog(monkeypatch, tmp_path):
    """Молчащий набор снимается сторожем, причина — в логе и в failed."""
    monkeypatch.setattr(render, "AE_STALL_KILL_SEC", 0.3)
    monkeypatch.setattr(render, "AE_STALL_WARN_SEC", 0.1)
    monkeypatch.setattr(render.subprocess, "Popen", _SilentAerender)
    monkeypatch.setattr(render, "_kill_proc",
                        lambda p: setattr(p, "_killed", True))

    comps = [("01_C0233", "C0233", 100)]
    with render.RLOCK:
        render.RJOB.update(running=True, done=False, log=[], pct=None, cur="", ae="",
                           out_dir="", result=[], failed=[], cancel=False, items=[], eta=None)
    render.items_init(render.RJOB, render.RLOCK, [s for s, _c, _f in comps])
    render.item_set(render.RJOB, render.RLOCK, "01_C0233", stage="render")

    rc = render._run_proc_batch("aerender.exe", "набор.aep", comps, str(tmp_path))
    assert rc == render._AE_STALLED
    assert any("не отвечает" in str(e) for e in render.RJOB["log"]), render.RJOB["log"]
    assert any("не отвечает" in f.get("reason", "") for f in render.RJOB["failed"])


def test_run_proc_batch_talks_and_finishes(monkeypatch, tmp_path):
    """Говорящий набор доигрывает до конца: сторож не мешает нормальному рендеру."""
    lines = [
        'PROGRESS:  8/26/2026 12:55:08 AM: Finished composition "C0233".',
    ]
    monkeypatch.setattr(render, "AE_STALL_KILL_SEC", 0.3)
    monkeypatch.setattr(render, "AE_STALL_WARN_SEC", 0.1)
    monkeypatch.setattr(render.subprocess, "Popen", lambda *a, **k: _Proc(lines))
    monkeypatch.setattr(render, "_kill_proc", lambda p: pytest.fail("снят живой рендер"))

    comps = [("01_C0233", "C0233", 100)]
    with render.RLOCK:
        render.RJOB.update(running=True, done=False, log=[], pct=None, cur="", ae="",
                           out_dir="", result=[], failed=[], cancel=False, items=[], eta=None)
    render.items_init(render.RJOB, render.RLOCK, [s for s, _c, _f in comps])

    rc = render._run_proc_batch("aerender.exe", "набор.aep", comps, str(tmp_path))
    assert rc == 0
    assert any(it.get("name") == "01_C0233" and it.get("stage") == "done"
               for it in render.RJOB["items"]), render.RJOB["items"]


# --------------------------------------------------------------------------- #
# 6. kill_tree — одна реализация
# --------------------------------------------------------------------------- #
def test_kill_tree_is_single_implementation():
    """Три места зовут одну функцию: своего `taskkill` в модулях роутов больше нет."""
    import ast
    import inspect
    import textwrap

    def calls_and_attrs(fn):
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        calls = {n.func.id for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        return calls, attrs

    from api import jobs
    for fn in (render._kill_proc, gdrive._kill_proc, jobs._kill_curproc):
        calls, attrs = calls_and_attrs(fn)
        assert "kill_tree" in calls or fn is jobs._kill_curproc, fn
        assert "run" not in attrs and "Popen" not in attrs, \
            f"в {fn.__module__}.{fn.__name__} осталась своя копия убийства дерева"
    calls, attrs = calls_and_attrs(_core.kill_tree)
    assert "run" in attrs, "kill_tree обязан звать taskkill через subprocess.run"


def test_kill_tree_kills_and_survives_errors(monkeypatch):
    """`kill_tree` доводит дело до `p.kill()`, даже если taskkill недоступен."""
    killed = []

    class _P:
        pid = 1

        def poll(self):
            return None

        def kill(self):
            killed.append(self)

    monkeypatch.setattr(_core.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("нет taskkill")))
    _core.kill_tree(_P())
    assert len(killed) == 1


# --------------------------------------------------------------------------- #
# 7. /api/swap_cam: нецелый номер камеры
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("cam", ["abc", 1.5, []])
def test_swap_cam_bad_cam(client, cam):
    """`cam: "abc"` раньше отвечал «Камеру 1 менять нельзя» — это про другое."""
    d = client.post("/api/swap_cam", json={"xml": "нет.xml", "cam": cam, "path": "нет.mp4"},
                    headers=H).get_json()
    assert d.get("err") == "bad_cam", d


def test_swap_cam_missing_xml_still_file_not_found(client):
    """Валидный номер, но нет XML — прежний file_not_found (порядок проверок цел)."""
    d = client.post("/api/swap_cam", json={"xml": "нет.xml", "cam": 1, "path": "нет.mp4"},
                    headers=H).get_json()
    assert d.get("err") == "file_not_found", d


# --------------------------------------------------------------------------- #
# 8. /api/media: порядок проверок и mpg/mpeg
# --------------------------------------------------------------------------- #
def test_media_serves_mpg(client, tmp_path):
    """.mpg/.mpeg — видео для проекта (core/verify_jsx.Video_EXT), отдаём их."""
    for ext in ("mpg", "mpeg"):
        f = tmp_path / f"clip.{ext}"
        f.write_bytes(b"fake-video")
        r = client.get(f"/api/media?path={f}", headers=H)
        assert r.status_code == 200, ext
        assert r.data == b"fake-video"


def test_media_missing_json_is_403_not_404(client, tmp_path):
    """Несуществующий .json — 403: 404 выдавал бы, что файла нет (оракул)."""
    r = client.get(f"/api/media?path={tmp_path / 'нет.json'}", headers=H)
    assert r.status_code == 403, r.status_code
    # и наоборот: существующий .json тоже 403, разницы в ответе нет
    f = tmp_path / "есть.json"
    f.write_text("{}", encoding="utf-8")
    assert client.get(f"/api/media?path={f}", headers=H).status_code == 403


def test_media_missing_media_is_404(client, tmp_path):
    """Разрешённое расширение, но файла нет — по-прежнему 404."""
    r = client.get(f"/api/media?path={tmp_path / 'нет.mp4'}", headers=H)
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# 9. /api/export_xml: только .xml
# --------------------------------------------------------------------------- #
def test_export_xml_refuses_non_xml(client, tmp_path):
    """Текст по пути больше не читается: секрет не уходит даже вложением."""
    secret = tmp_path / "secret.txt"
    secret.write_text("СЕКРЕТ", encoding="utf-8")
    r = client.get(f"/api/export_xml?path={secret}", headers=H)
    assert r.status_code == 403
    assert "СЕКРЕТ" not in r.get_data(as_text=True)


def test_export_xml_serves_xml(client, tmp_path, monkeypatch):
    """Настоящий .xml отдаётся как раньше — и в верхнем регистре расширения тоже."""
    from core import xmlbuild
    monkeypatch.setattr(xmlbuild, "fix_timecodes", lambda text: (text, 0))
    for name in ("clip.xml", "clip.XML"):
        f = tmp_path / name
        f.write_text("<xmeml/>", encoding="utf-8")
        r = client.get(f"/api/export_xml?path={f}", headers=H)
        assert r.status_code == 200, name
        assert r.get_data(as_text=True) == "<xmeml/>"


# --------------------------------------------------------------------------- #
# 10. save_profile: ключ-маска и смена адреса/провайдера
# --------------------------------------------------------------------------- #
@pytest.fixture
def cfg_file(tmp_path, monkeypatch):
    """Изолированный ai_config.json: боевой (с ключами) не читаем и не пишем."""
    from core.aicut import config
    p = tmp_path / "ai_config.json"
    p.write_text('{"active": "old", "profiles": {"old": '
                 '{"provider": "lmstudio", "base_url": "http://127.0.0.1:1234/v1", '
                 '"api_key": "sk-настоящий-ключ-1234", "model": "qwen"}}}',
                 encoding="utf-8")
    monkeypatch.setattr(config, "AI_CONFIG_PATH", str(p))
    return p


def _saved_key(path):
    import json
    return json.loads(path.read_text(encoding="utf-8"))["profiles"]["old"]["api_key"]


def test_save_profile_mask_other_address_refused(client, cfg_file):
    """Маска + новый адрес → отказ, ключ в конфиге не тронут."""
    r = client.post("/api/ai_config", json={
        "action": "save_profile", "name": "old", "old_name": "old",
        "profile": {"provider": "lmstudio", "base_url": "http://чужой-адрес.example/v1",
                    "api_key": "•••1234", "model": "qwen"}}, headers=H)
    d = r.get_json()
    assert d.get("err") == "key_mask_address_changed", d
    assert _saved_key(cfg_file) == "sk-настоящий-ключ-1234"
    assert "чужой-адрес" not in cfg_file.read_text(encoding="utf-8")


def test_save_profile_mask_other_provider_refused(client, cfg_file):
    """Маска + другой провайдер (URL совпал) → тоже отказ."""
    d = client.post("/api/ai_config", json={
        "action": "save_profile", "name": "old", "old_name": "old",
        "profile": {"provider": "openrouter", "base_url": "http://127.0.0.1:1234/v1",
                    "api_key": "•••1234", "model": "qwen"}}, headers=H).get_json()
    assert d.get("err") == "key_mask_address_changed", d
    assert _saved_key(cfg_file) == "sk-настоящий-ключ-1234"


def test_save_profile_mask_same_address_keeps_key(client, cfg_file):
    """Маска при том же адресе — прежнее поведение: ключ сохранённого профиля цел."""
    d = client.post("/api/ai_config", json={
        "action": "save_profile", "name": "old", "old_name": "old",
        "profile": {"provider": "lmstudio", "base_url": "http://127.0.0.1:1234/v1",
                    "api_key": "•••1234", "model": "qwen2"}}, headers=H).get_json()
    assert d.get("ok") is True, d
    assert _saved_key(cfg_file) == "sk-настоящий-ключ-1234"


def test_mask_warning_text_is_one_string_in_three_places(client, cfg_file):
    """Текст про маску ключа ОДИН: ответ сервера = ключ словаря = строка aiSetProv.

    Разъедутся — пользователь увидит русскую подсказку в английском интерфейсе
    (или не увидит вовсе): в i18n ключом служит сам русский текст.
    """
    import io
    import json
    from core import app_meta
    d = client.post("/api/ai_config", json={
        "action": "save_profile", "name": "old", "old_name": "old",
        "profile": {"provider": "openai", "base_url": "http://чужой.example/v1",
                    "api_key": "•••1234", "model": "qwen"}}, headers=H).get_json()
    en = json.load(io.open(os.path.join(ROOT, "static", "i18n", "en.json"),
                           encoding="utf-8"))
    assert d["error"] in en, f"текст ошибки не переводится: {d['error']!r}"
    assert d["error"] in app_meta.app_js_text(), "aiSetProv показывает другой текст"


def test_gdrive_cancel_status_is_translated(client):
    """«скачивание остановлено» — тот же текст у сервера и у страницы, и он переведён."""
    import io
    import json
    from core import app_meta
    with gdrive.GDLOCK:
        gdrive.GDJOB.update(gdrive.GDFRESH, running=False, done=True, cancelled=True,
                            failed=False, cur="скачивание остановлено", log=[])
    d = client.get("/api/gdrive_status", headers=H).get_json()
    en = json.load(io.open(os.path.join(ROOT, "static", "i18n", "en.json"),
                           encoding="utf-8"))
    assert d["cur"] in en, f"статус не переводится: {d['cur']!r}"
    assert "t('скачивание остановлено')" in app_meta.app_js_text(), \
        "страница берёт для отмены другой текст"
