# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Ссылка на папку гугл-диска с подпапками: список подпапок и раскладка по камерам.

Что стережём:
1. top_folders — группировка по первой части пути, файлы корня отдельно, вложенность
   глубже одного уровня, естественный порядок имён («Камера 2» раньше «Камера 10»).
2. rclone_cmd — подпапка (источник remote:имя) и корень (--max-depth 1) с сохранённым `--`;
   опасные имена подпапки отказываются до запуска rclone.
3. rclone_list_cmd — листинг только для папки, для файла — отказ.
4. Роуты /api/gdrive_list и /api/gdrive_download с map. rclone подменён, сети нет.
5. Раскладка в джобе: команды по очереди, «Стоп» после первой — вторая не запускается,
   упавшая папка не отменяет остальные, итог — failed.

Запуск: py -3.10 -m pytest tests/test_gdrive_folders.py -q
"""
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

import api.gdrive as gdrive  # noqa: E402
from core.rclone import rclone_cmd, rclone_list_cmd, top_folders  # noqa: E402

FILE = "https://drive.google.com/file/d/1AbC_dEfGhI1234567890/view?usp=sharing"
FOLDER = "https://drive.google.com/drive/folders/1XyfxxxxxxxxxxxxxxxxxxxxxxxxxKHCh"

# Записанный ответ `rclone lsjson -R --files-only` на папку с двумя камерами,
# файлом в корне и файлом документа без размера (-1)
LSJSON = [
    {"Path": "Камера 2/IMG_0002.MOV", "Name": "IMG_0002.MOV", "Size": 2000, "IsDir": False},
    {"Path": "Камера 10/IMG_0010.MOV", "Name": "IMG_0010.MOV", "Size": 500, "IsDir": False},
    {"Path": "Камера 2/sub/deep.MOV", "Name": "deep.MOV", "Size": 300, "IsDir": False},
    {"Path": "Камера 2/IMG_0003.MOV", "Name": "IMG_0003.MOV", "Size": 1000, "IsDir": False},
    {"Path": "Камера 1/IMG_0001.MOV", "Name": "IMG_0001.MOV", "Size": 4000, "IsDir": False},
    {"Path": "общий.txt", "Name": "общий.txt", "Size": 10, "IsDir": False},
    {"Path": "Документ", "Name": "Документ", "Size": -1, "IsDir": False},
]


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    conf = tmp_path / "rclone.conf"
    conf.write_text("[gdrive]\ntype = drive\n", encoding="utf-8")
    monkeypatch.setenv("REELSI_RCLONE_CONF", str(conf))
    monkeypatch.setenv("REELSI_RCLONE_REMOTE", "gdrive")
    monkeypatch.delenv("AUTOCUT_RCLONE_REMOTE", raising=False)


@pytest.fixture
def client():
    from flask import Flask
    import api
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


# --------------------------------------------------------------------------- #
# 1. top_folders — разбор lsjson
# --------------------------------------------------------------------------- #
def test_top_folders_группирует_по_первой_части_пути():
    r = top_folders(LSJSON)
    assert [d["name"] for d in r["dirs"]] == ["Камера 1", "Камера 2", "Камера 10"]
    by = {d["name"]: d for d in r["dirs"]}
    # файл глубже одного уровня (sub/deep.MOV) считается в свою верхнюю папку
    assert by["Камера 2"] == {"name": "Камера 2", "files": 3, "bytes": 3300}
    assert by["Камера 1"] == {"name": "Камера 1", "files": 1, "bytes": 4000}
    assert by["Камера 10"] == {"name": "Камера 10", "files": 1, "bytes": 500}


def test_top_folders_файлы_корня_отдельно():
    """Файлы самой ссылки не уезжают в папку камеры: они в root_files/root_bytes.
    Размер -1 (документ Google) — ноль, а не минус."""
    r = top_folders(LSJSON)
    assert r["root_files"] == 2
    assert r["root_bytes"] == 10
    assert sum(d["files"] for d in r["dirs"]) == 5


def test_top_folders_пустой_ответ_и_папки_без_файлов():
    assert top_folders([]) == {"dirs": [], "root_files": 0, "root_bytes": 0}
    assert top_folders([{"Path": "Пусто", "IsDir": True}]) == \
        {"dirs": [], "root_files": 0, "root_bytes": 0}


# --------------------------------------------------------------------------- #
# 2. rclone_cmd — подпапка и корень
# --------------------------------------------------------------------------- #
def test_подпапка_качается_из_своего_источника(tmp_path):
    args = rclone_cmd("gdrive", FOLDER, str(tmp_path), sub="Камера 1")
    i = args.index("--")
    assert args[i + 1] == "gdrive:Камера 1"
    assert args[i + 2] == str(tmp_path)
    assert "--max-depth" not in args
    assert args[args.index("--drive-root-folder-id") + 1] == "1XyfxxxxxxxxxxxxxxxxxxxxxxxxxKHCh"


def test_корень_ссылки_только_файлы_верхнего_уровня(tmp_path):
    args = rclone_cmd("gdrive", FOLDER, str(tmp_path), root_only=True)
    assert args[args.index("--max-depth") + 1] == "1"
    assert args.index("--max-depth") < args.index("--")     # опция до разделителя
    i = args.index("--")
    assert args[i + 1] == "gdrive:"


def test_без_подпапки_поведение_прежнее(tmp_path):
    args = rclone_cmd("gdrive", FOLDER, str(tmp_path))
    i = args.index("--")
    assert args[i + 1] == "gdrive:" and args[i + 2] == str(tmp_path)


@pytest.mark.parametrize("bad", ["-x", "../x", "..y", "a/b", "a\\b", ""])
def test_опасное_имя_подпапки_отказ_до_rclone(tmp_path, bad):
    with pytest.raises(ValueError):
        rclone_cmd("gdrive", FOLDER, str(tmp_path), sub=bad)


def test_подпапка_и_корень_у_ссылки_на_файл_отказ(tmp_path):
    with pytest.raises(ValueError):
        rclone_cmd("gdrive", FILE, str(tmp_path), sub="Камера 1")
    with pytest.raises(ValueError):
        rclone_cmd("gdrive", FILE, str(tmp_path), root_only=True)


# --------------------------------------------------------------------------- #
# 3. rclone_list_cmd — листинг
# --------------------------------------------------------------------------- #
def test_листинг_папки_рекурсивный_только_файлы():
    args = rclone_list_cmd("gdrive", FOLDER)
    assert args[0] == "rclone" and "lsjson" in args
    assert "-R" in args and "--files-only" in args
    assert args[args.index("--drive-root-folder-id") + 1] == "1XyfxxxxxxxxxxxxxxxxxxxxxxxxxKHCh"
    assert args[-2:] == ["--", "gdrive:"]
    assert "--drive-resource-key" not in args


def test_листинг_с_resourcekey():
    rk = FOLDER + "?resourcekey=0-ABCDEFGHIXJQpIGqBJq3MC"
    args = rclone_list_cmd("gdrive", rk)
    assert args[args.index("--drive-resource-key") + 1] == "0-ABCDEFGHIXJQpIGqBJq3MC"


def test_листинг_для_файла_отказ():
    with pytest.raises(ValueError):
        rclone_list_cmd("gdrive", FILE)


# --------------------------------------------------------------------------- #
# 4. Роуты и джоб — rclone подменён
# --------------------------------------------------------------------------- #
class _Proc:
    """Подставной rclone: отдаёт строки и код возврата. cancel_after — поднимает флаг
    «Стоп» уже после вывода, как если бы человек нажал его во время первой папки."""

    def __init__(self, code=0, lines=(), cancel_after=False):
        self.pid = 4242
        self._code = code
        self._done = False
        self._killed = False
        self._lines = list(lines)
        self._cancel_after = cancel_after
        self.stdout = self._stream()

    def _stream(self):
        for ln in self._lines:
            yield ln
        if self._cancel_after:
            gdrive.GDJOB["cancel"] = True
        self._done = True

    def poll(self):
        return self._code if self._done else None

    def wait(self):
        return self._code

    def kill(self):
        self._killed = True


@pytest.fixture
def job_env(monkeypatch):
    """Поток джоба не запускаем: Thread исполняет цель синхронно, и ответ роута
    приходит уже после конца скачивания. Убийство — не настоящий taskkill."""
    class _SyncThread:
        def __init__(self, target, args=(), daemon=None):
            self._target, self._args = target, args

        def start(self):
            self._target(*self._args)

    class _Threads:
        Thread = _SyncThread

    monkeypatch.setattr(gdrive, "threading", _Threads)
    monkeypatch.setattr(gdrive, "_kill_proc", lambda p: p.kill())
    monkeypatch.setattr(gdrive.shutil, "which", lambda name, *a, **k: "C:/fake/rclone")
    reset = dict(running=False, done=False, failed=None, cancel=False, cancelled=False,
                 log=[], log_base=0, url="", started=0)
    with gdrive.GDLOCK:
        gdrive.GDJOB.update(gdrive.GDFRESH, **reset)
    yield
    with gdrive.GDLOCK:
        gdrive.GDJOB.update(gdrive.GDFRESH, **reset)


def _fake_list(monkeypatch, stdout="", returncode=0, stderr="", raise_timeout=False):
    calls = []

    def fake_run(args, **kw):
        calls.append(list(args))
        if raise_timeout:
            raise subprocess.TimeoutExpired(args, kw.get("timeout"))
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(gdrive.subprocess, "run", fake_run)
    # rclone в PATH есть не везде (в Linux-образе CI его нет): без подмены роут отвечает
    # «rclone не найден» раньше подменённого вызова — как и в фикстуре job_env
    monkeypatch.setattr(gdrive.shutil, "which", lambda name, *a, **k: "C:/fake/rclone")
    return calls


def _fake_popen(monkeypatch, procs):
    """Popen отдаёт по одному подставному процессу на каждую команду, по порядку."""
    calls = []
    queue = list(procs)

    def fake_popen(cmd, *a, **k):
        calls.append(list(cmd))
        return queue.pop(0)

    monkeypatch.setattr(gdrive.subprocess, "Popen", fake_popen)
    return calls


def _gd_state():
    with gdrive.GDLOCK:
        return dict(gdrive.GDJOB)


def test_gdrive_list_папка_отдаёт_подпапки(client, monkeypatch):
    calls = _fake_list(monkeypatch, stdout=json.dumps(LSJSON))
    r = client.post("/api/gdrive_list", json={"url": FOLDER}).get_json()
    assert r["kind"] == "folder"
    assert [d["name"] for d in r["dirs"]] == ["Камера 1", "Камера 2", "Камера 10"]
    assert r["root_files"] == 2 and r["root_bytes"] == 10
    assert len(calls) == 1 and "lsjson" in calls[0]


def test_gdrive_list_файл_без_rclone(client, monkeypatch):
    calls = _fake_list(monkeypatch, stdout="[]")
    r = client.post("/api/gdrive_list", json={"url": FILE}).get_json()
    assert r == {"ok": True, "kind": "file"}
    assert calls == []


def test_gdrive_list_ошибка_rclone_и_таймаут(client, monkeypatch):
    _fake_list(monkeypatch, returncode=1, stderr="x\n2026/10/17 ERROR : token expired\n")
    r = client.post("/api/gdrive_list", json={"url": FOLDER}).get_json()
    assert r["err"] == "gdrive_list_failed"
    assert r["err_vars"]["err"].endswith("token expired")

    _fake_list(monkeypatch, raise_timeout=True)
    r = client.post("/api/gdrive_list", json={"url": FOLDER}).get_json()
    assert r["err"] == "gdrive_list_timeout"
    assert r["err_vars"]["sec"] == gdrive.RCLONE_LIST_TIMEOUT_SEC


def test_gdrive_list_мусорная_ссылка(client, monkeypatch):
    calls = _fake_list(monkeypatch, stdout="[]")
    r = client.post("/api/gdrive_list", json={"url": "https://yandex.ru/disk"}).get_json()
    assert r["err"] == "bad_link"
    assert calls == []


def test_download_map_две_папки_две_команды_по_очереди(client, tmp_path, monkeypatch, job_env):
    c1, c2 = tmp_path / "c1", tmp_path / "c2"
    calls = _fake_popen(monkeypatch, [_Proc(), _Proc()])
    body = {"url": FOLDER, "dest": str(tmp_path / "dl"),
            "map": [{"dir": "Камера 1", "dest": str(c1)}, {"dir": "Камера 2", "dest": str(c2)}]}
    r = client.post("/api/gdrive_download", json=body).get_json()
    assert r["ok"] is True and r["parts"] == 2 and "error" not in r
    assert len(calls) == 2
    for cmd, name, to in ((calls[0], "Камера 1", c1), (calls[1], "Камера 2", c2)):
        i = cmd.index("--")
        assert cmd[i + 1] == "gdrive:" + name and cmd[i + 2] == str(to)
    assert c1.is_dir() and c2.is_dir()
    st = _gd_state()
    assert st["done"] is True and st["failed"] is False and st["parts"] == 2
    assert any("Камера 2" in str(x) for x in st["log"])      # строка «папка «…» → …»


def test_download_map_пустой_dest_пропускается(client, tmp_path, monkeypatch, job_env):
    c2 = tmp_path / "c2"
    calls = _fake_popen(monkeypatch, [_Proc()])
    body = {"url": FOLDER, "dest": str(tmp_path / "dl"),
            "map": [{"dir": "Камера 1", "dest": ""}, {"dir": "Камера 2", "dest": str(c2)}]}
    r = client.post("/api/gdrive_download", json=body).get_json()
    assert r["parts"] == 1
    assert len(calls) == 1 and "gdrive:Камера 2" in calls[0]


def test_download_map_файлы_корня_одной_командой(client, tmp_path, monkeypatch, job_env):
    root = tmp_path / "root"
    calls = _fake_popen(monkeypatch, [_Proc()])
    body = {"url": FOLDER, "dest": str(tmp_path / "dl"), "map": [{"dir": "", "dest": str(root)}]}
    client.post("/api/gdrive_download", json=body)
    cmd = calls[0]
    assert "--max-depth" in cmd and cmd[cmd.index("--max-depth") + 1] == "1"
    assert cmd[cmd.index("--") + 1] == "gdrive:"


def test_download_map_стоп_после_первой_вторая_не_запускается(client, tmp_path, monkeypatch, job_env):
    calls = _fake_popen(monkeypatch, [_Proc(cancel_after=True), _Proc()])
    body = {"url": FOLDER, "dest": str(tmp_path / "dl"),
            "map": [{"dir": "Камера 1", "dest": str(tmp_path / "c1")},
                    {"dir": "Камера 2", "dest": str(tmp_path / "c2")}]}
    client.post("/api/gdrive_download", json=body)
    assert len(calls) == 1
    st = _gd_state()
    assert st["cancelled"] is True and st["failed"] is False and st["done"] is True


def test_download_map_упавшая_папка_не_отменяет_остальные(client, tmp_path, monkeypatch, job_env):
    calls = _fake_popen(monkeypatch, [_Proc(code=3), _Proc(code=0)])
    body = {"url": FOLDER, "dest": str(tmp_path / "dl"),
            "map": [{"dir": "Камера 1", "dest": str(tmp_path / "c1")},
                    {"dir": "Камера 2", "dest": str(tmp_path / "c2")}]}
    client.post("/api/gdrive_download", json=body)
    assert len(calls) == 2                       # вторая папка качается и после ошибки первой
    st = _gd_state()
    assert st["failed"] is True and st["done"] is True
    assert "3" in st["cur"]


@pytest.mark.parametrize("bad_map", [
    "Камера 1",                                                   # не список
    [{"dir": "../x", "dest": "C:/x"}],                            # недопустимое имя подпапки
    [{"dir": "Камера 1", "dest": "-config"}],                     # dest с дефиса — опция rclone
    [{"dir": "Камера 1", "dest": ""}],                            # всё «не качать»
    [7],                                                          # элемент — не объект
])
def test_download_map_битая_форма_внятная_ошибка(client, tmp_path, monkeypatch, job_env, bad_map):
    calls = _fake_popen(monkeypatch, [])
    r = client.post("/api/gdrive_download",
                    json={"url": FOLDER, "dest": str(tmp_path / "dl"), "map": bad_map}).get_json()
    assert r["err"] == "gdrive_map_bad"
    assert calls == []


def test_download_без_map_одна_команда_как_раньше(client, tmp_path, monkeypatch, job_env):
    calls = _fake_popen(monkeypatch, [_Proc()])
    r = client.post("/api/gdrive_download",
                    json={"url": FILE, "dest": str(tmp_path / "dl")}).get_json()
    assert r["ok"] is True and r["parts"] == 1
    assert len(calls) == 1 and "copyid" in calls[0]
