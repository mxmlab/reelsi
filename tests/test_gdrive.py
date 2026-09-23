# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Скачивание с гугл-диска по ссылке (api/gdrive.py).

Проверяется то, что можно проверить без живого rclone: разбор ссылки (самое
место, где молча скачаешь не то — перепутанные id/тип/кусок чужого URL), выбор
drive-remote из конфига и сборка аргументов команды без shell (путь из ссылки
уходит в список аргументов, а не в оболочку).

Запуск:  python -m pytest reelsi/tests -q
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

import api.gdrive as gdrive  # noqa: E402
from api.gdrive import _run_rclone  # noqa: E402
from core.rclone import (  # noqa: E402
    clean_line, parse_gdrive_link, progress_line,
    rclone_cmd, rclone_conf, rclone_remote, rclone_remotes, stats_fields,
)

FILE = "https://drive.google.com/file/d/1AbC_dEfGhI1234567890/view?usp=sharing"
FOLDER = "https://drive.google.com/drive/folders/1XyfxxxxxxxxxxxxxxxxxxxxxxxxxKHCh"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for v in ("REELSI_RCLONE_REMOTE", "AUTOCUT_RCLONE_REMOTE", "REELSI_RCLONE_CONF"):
        monkeypatch.delenv(v, raising=False)


def write_conf(tmp_path, body):
    p = tmp_path / "rclone.conf"
    p.write_text(body, encoding="utf-8")
    return str(p)


def test_файловая_ссылка_любого_вида():
    assert parse_gdrive_link(FILE) == {"kind": "file", "id": "1AbC_dEfGhI1234567890",
                                       "resource_key": None}
    assert parse_gdrive_link("https://drive.google.com/open?id=1XyfAAAAAAAAAAAAA") == {
        "kind": "file", "id": "1XyfAAAAAAAAAAAAA", "resource_key": None}
    assert parse_gdrive_link("https://drive.google.com/uc?export=download&id=1XyfBBBBBBBBBBB") == {
        "kind": "file", "id": "1XyfBBBBBBBBBBB", "resource_key": None}


def test_папочная_ссылка_и_resourcekey():
    assert parse_gdrive_link(FOLDER) == {"kind": "folder", "id": "1XyfxxxxxxxxxxxxxxxxxxxxxxxxxKHCh",
                                         "resource_key": None}
    assert parse_gdrive_link("https://drive.google.com/drive/u/0/folders/1XyfYYYYYYY?resourcekey=0-ABCDEFGHIXJQpIGqBJq3MC&usp=sharing") == {
        "kind": "folder", "id": "1XyfYYYYYYY", "resource_key": "0-ABCDEFGHIXJQpIGqBJq3MC"}


def test_мусорная_ссылка_и_пусто():
    assert parse_gdrive_link("") is None
    assert parse_gdrive_link("https://yandex.ru/disk") is None
    assert parse_gdrive_link("https://drive.google.com/") is None
    assert parse_gdrive_link(None) is None


def test_конфиг_читает_drive_remote(tmp_path):
    conf = write_conf(tmp_path, """
[gdrive]
type = drive
token = {"access_token":"x"}
[other]
type = webdav
""")
    assert rclone_remotes(conf) == ["gdrive"]
    assert rclone_remote(conf) == "gdrive"


def test_конфига_нет_или_гугл_диска_нет(tmp_path):
    assert rclone_remotes(str(tmp_path / "nope.conf")) == []
    conf = write_conf(tmp_path, "[ftp]\ntype = ftp\n")
    assert rclone_remotes(conf) == []
    with pytest.raises(RuntimeError):
        rclone_remote(conf)


def test_несколько_drive_remote_просят_явное_имя(tmp_path):
    conf = write_conf(tmp_path, "[a]\ntype = drive\n[b]\ntype = drive\n")
    with pytest.raises(RuntimeError):
        rclone_remote(conf)
    os.environ["REELSI_RCLONE_REMOTE"] = "b"
    assert rclone_remote(conf) == "b"


def test_явная_переменная_пути_к_конфигу(monkeypatch, tmp_path):
    p = write_conf(tmp_path, "[gdrive]\ntype = drive\n")
    monkeypatch.setenv("REELSI_RCLONE_CONF", p)
    assert rclone_conf() == p
    assert rclone_remotes() == ["gdrive"]


def test_команда_файла_это_copyid_без_shell(tmp_path):
    os.environ["REELSI_RCLONE_CONF"] = write_conf(tmp_path, "[gdrive]\ntype = drive\n")
    args = rclone_cmd("gdrive", FILE, str(tmp_path))
    assert args[0] == "rclone"
    assert "--config" in args and "shell" not in args
    i = args.index("copyid")
    assert args[i + 1] == "gdrive:"
    assert args[i + 2] == "1AbC_dEfGhI1234567890"
    assert args[i + 3].endswith("/")
    assert "&&" not in args and ";" not in args


def test_команда_папки_ставит_root_folder_id(tmp_path):
    os.environ["REELSI_RCLONE_CONF"] = write_conf(tmp_path, "[gdrive]\ntype = drive\n")
    args = rclone_cmd("gdrive", FOLDER, str(tmp_path))
    assert "copy" in args and "backend" not in args
    assert "--drive-root-folder-id" in args
    assert args[args.index("--drive-root-folder-id") + 1] == "1XyfxxxxxxxxxxxxxxxxxxxxxxxxxKHCh"
    assert "--drive-resource-key" not in args

    rk = "https://drive.google.com/drive/folders/1XyfxxxxxxxxxxxxxxxxxxxxxxxxxKHCh?resourcekey=0-ABCDEFGHIXJQpIGqBJq3MC&usp=sharing"
    args = rclone_cmd("gdrive", rk, str(tmp_path))
    assert args[args.index("--drive-resource-key") + 1] == "0-ABCDEFGHIXJQpIGqBJq3MC"


def test_прогресс_rclone_разбирается_в_лог():
    """Строка прогресса rclone (--stats) → «скачивание: X из Y (N%)», всё прочее — как есть."""
    assert progress_line("Transferred:   	  0.512 GiB / 1.234 GiB, 41%, 12.5 MiB/s, ETA 0s") == \
        "скачивание: 0.512 GiB из 1.234 GiB (41%)"
    assert progress_line("Transferred:   	  1.234 GiB / 1.234 GiB, 100%, 8.1 MiB/s, ETA 0s") == \
        "скачивание: 1.234 GiB из 1.234 GiB (100%)"
    assert progress_line("2026/08/12 12:00:00 ERROR : file.mp4: failed") is None
    assert progress_line("") is None


def test_прогресс_вообще_печатается_только_с_v(tmp_path):
    """`-v` в команде обязателен, и это не украшение.

    rclone логирует статистику на уровне INFO (`--stats-log-level`, дефолт INFO),
    а порог вывода по умолчанию — NOTICE. С одним `--stats` в лог не попадёт ни
    одной строки прогресса, и скачивание гигабайта выглядит как зависшее. Ловушка
    незаметная: на подставном rclone из теста строки печатаются в любом случае.
    """
    os.environ["REELSI_RCLONE_CONF"] = write_conf(tmp_path, "[gdrive]\ntype = drive\n")
    for url in (FILE, FOLDER):
        args = rclone_cmd("gdrive", url, str(tmp_path))
        assert "-v" in args, "без -v прогресс rclone в лог не попадёт"


def test_блок_статистики_не_забивает_лог():
    """Из блока статистики в лог уходит одна строка прогресса, остальные — мимо.

    Блок печатается целиком каждые --stats секунд, а наружу отдаются последние 40
    строк лога: без фильтра в них не осталось бы ничего, кроме статистики.
    """
    from core.rclone import is_noise
    for noise in ("Transferred:            0 / 1, 0%",
                  "Checks:                 2 / 2, 100%",
                  "Elapsed time:        45.0s",
                  "Transferring:",
                  " *  IMG_6753.MOV: 41% /1.234Gi, 12.345Mi/s, 1m2s"):
        assert is_noise(noise), noise
    for keep in ("2026/08/12 12:00:00 ERROR : file.mp4: failed to copy",
                 "2026/08/12 12:00:00 INFO  : IMG_6753.MOV: Copied (new)"):
        assert not is_noise(keep), keep
    # строка прогресса сама по себе шумом не считается — её разбирает progress_line
    assert progress_line("Transferred:   	  0.5 GiB / 1 GiB, 50%, 8 MiB/s, ETA 1m")


def test_статистика_разбирается_по_полям():
    """Блок статистики → поля живого статуса: сколько, как быстро, сколько осталось.

    Три строки блока различаются только формой (у счётчика файлов нет единиц),
    и перепутать их легко: тогда «файл 2 из 5» уехало бы в проценты байтов.
    """
    assert stats_fields("Transferred:   \t  0.512 GiB / 1.234 GiB, 41%, 12.5 MiB/s, ETA 1m2s") == {
        "bytes": "0.512 GiB", "total": "1.234 GiB", "pct": 41,
        "speed": "12.5 MiB/s", "eta": "1m2s"}
    assert stats_fields("Transferred:            2 / 5, 40%") == {"i": 2, "n": 5}
    assert stats_fields(" *  IMG_6753.MOV: 41% /1.234Gi, 12.345Mi/s, 1m2s") == {
        "file": "IMG_6753.MOV", "file_pct": 41}
    # без скорости и ETA (первый блок) — всё равно проценты, а не None
    assert stats_fields("Transferred:        0 B / 1.234 GiB, 0%")["pct"] == 0
    assert stats_fields("2026/08/12 12:00:00 INFO  : IMG.MOV: Copied (new)") is None
    assert stats_fields("Checks:                 2 / 2, 100%") is None


def test_дата_и_уровень_из_строки_убираются():
    """В логе страницы своё время, а ширина узкая: дата+уровень съедали полстроки."""
    assert clean_line("2026/08/12 12:00:00 INFO  : IMG.MOV: Copied (new)") == \
        "IMG.MOV: Copied (new)"
    assert clean_line("2026/08/12 12:00:00 ERROR : IMG.MOV: failed to copy") == \
        "IMG.MOV: failed to copy"
    assert clean_line("  rclone: не про даты  ") == "rclone: не про даты"


class _FakeProc:
    """Подставной rclone: отдаёт заготовленные строки и код возврата.

    `poll()` обязателен: сторож простоя опрашивает процесс, и без
    него `_run_rclone` падал AttributeError. Код возврата показывается ТОЛЬКО
    когда строки кончились — иначе цикл вышел бы, не прочитав ни одной."""

    def __init__(self, lines, code=0):
        self._lines = list(lines)
        self._code = code
        self._done = False
        self.pid = 4242
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


def test_прогресс_в_статус_каждый_блок_а_в_лог_раз_в_10_процентов(monkeypatch):
    """Куда что уходит из вывода rclone.

    Статус обновляется на КАЖДОМ блоке статистики (иначе страница показывает
    «запуск rclone…» до самого конца и выглядит зависшей), а в лог прогресс
    дублируется раз в 10%: лог отдаётся хвостом, и при статистике раз в 2
    секунды лента процентов вытеснила бы из него события.
    """
    lines = ["2026/08/12 12:00:00 INFO  : IMG.MOV: Copied (new)"]
    lines += [f"Transferred:   \t  {p} MiB / 100 MiB, {p}%, 10 MiB/s, ETA 1m"
              for p in (0, 3, 7, 11, 25, 100)]
    lines += ["Checks:                 2 / 2, 100%", "Elapsed time:        45.0s"]
    monkeypatch.setattr(gdrive.subprocess, "Popen", lambda *a, **k: _FakeProc(lines))
    log, stats = [], []
    assert _run_rclone(["rclone"], log.append, stats.append) == 0
    assert [s["pct"] for s in stats] == [0, 3, 7, 11, 25, 100]
    prog = [l for l in log if l.startswith("скачивание:")]
    assert len(prog) == 4, prog          # 0, 11, 25, 100 — но не каждые 2 секунды
    assert prog[-1] == "скачивание: 100 MiB из 100 MiB (100%)"
    assert log[0] == "IMG.MOV: Copied (new)"          # событие — без даты и уровня
    assert not [l for l in log if l.startswith(("Checks", "Elapsed"))]


def test_упавший_rclone_не_считается_успехом(monkeypatch):
    """`failed` в статусе — по коду возврата. Раньше страница писала «скачивание
    завершено» на любом исходе, и обрыв на середине выглядел как успех."""
    monkeypatch.setattr(gdrive.subprocess, "Popen",
                        lambda *a, **k: _FakeProc(["2026/08/12 12:00:00 ERROR : failed"], 3))
    gdrive.GDJOB.update(running=True, done=False, failed=None, log=[], log_base=0)
    gdrive._download_job(["rclone"], "https://drive.google.com/file/d/x/view")
    assert gdrive.GDJOB["failed"] is True and gdrive.GDJOB["done"] is True
    assert "3" in gdrive.GDJOB["cur"]
    assert any("failed" in l for l in gdrive.GDJOB["log"])


def test_статус_отдаётся_целиком_и_с_инкрементальным_логом():
    """Ответ /api/gdrive_status собирается через **GDJOB — и на этом уже падало
    500-й: ключ состояния совпал с `ok` конверта («запрос принят»). Заодно
    стережём поля, на которых держится статус, и уговор `?since=`."""
    from flask import Flask
    import api
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    c = app.test_client()

    gdrive.GDJOB.update(gdrive.GDFRESH, running=True, done=False, failed=None,
                        log=["раз", "два"], log_base=0, url="u", started=0)
    d = c.get("/api/gdrive_status?since=0").get_json()
    assert d["ok"] is True                       # конверт, а не итог скачивания
    for k in ("running", "done", "failed", "cur", "pct", "i", "n", "speed", "eta",
              "file", "bytes", "total", "elapsed", "log_total"):
        assert k in d, k
    assert d["log"] == ["раз", "два"] and d["log_total"] == 2
    assert c.get("/api/gdrive_status?since=2").get_json()["log"] == []
    assert c.get("/api/gdrive_status?since=дичь").get_json()["log_total"] == 2
    gdrive.GDJOB.update(running=False, done=False, log=[])


def test_мусорная_ссылка_не_доходит_до_команды(tmp_path):
    """rclone_cmd — публичная функция; на не-ссылке она обязана падать внятно,
    а не с TypeError по None где-то внутри."""
    os.environ["REELSI_RCLONE_CONF"] = write_conf(tmp_path, "[gdrive]\ntype = drive\n")
    with pytest.raises(ValueError):
        rclone_cmd("gdrive", "https://disk.yandex.ru/d/abcdefgh", str(tmp_path))
