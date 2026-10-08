# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Чужой плагин — только в дочернем процессе: сторожа и процессы голоса.

Живые VST3 здесь НЕ запускаются и не читаются НИКОГДА. Настоящий плагин — чужая
DLL: один падает обращением к памяти, другой (u-he Satin) показывает своё окно
ошибки, и оно висит на экране, держа процессы. Поэтому у каждого теста каталоги
плагинов — временная папка, а дочерний процесс или подменён заглушкой, или
запускается с фальшивым `pedalboard`. Живой замер с настоящими плагинами делает
владелец руками, не тесты.

Что стерегут эти тесты:

* `list_vst3`, `load_plugin`, `get_plugin_names_for_file` не зовутся в модулях
  сервера и ядра — только в процессах `core/voicefx_scan|render|editor` (AST по
  дереву, а не grep: имена встречаются и в комментариях);
* имя плагина читает дочерний процесс, и повторный список его НЕ поднимает (кеш на
  диске), а изменённый плагин досканируется один;
* упавший плагин помечается «не грузится» с ключом файла, не теряет остальные и
  повторно не грузится, пока файл не изменился;
* цепочка уезжает в `core/voicefx_render` заданием файлом (состояние — base64),
  а «Стоп» снимает процесс по PID;
* системные окна ошибок Windows гасятся ДО импорта pedalboard в каждом процессе.

Запуск:  python -m pytest tests/test_voicefx_proc.py -q
"""
import ast
import base64
import ctypes
import json
import os
import queue
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from core import paths, voicefx  # noqa: E402
from core.umsg import ReelsiError  # noqa: E402

# Функции, которые ОТКРЫВАЮТ DLL плагина: имена файлов внутри (scan), загрузка
# плагина (render, editor). Разрешены только в процессах-потомках, которые
# уходят вместе со своими потоками.
PLUGIN_CALLS = ("load_plugin", "get_plugin_names_for_file", "VST3Plugin")
# Процессы, которым это можно (каждый — своя точка входа `python -m core.…`).
CHILD_MODULES = ("core/voicefx_scan.py", "core/voicefx_render.py",
                 "core/voicefx_audio.py", "core/voicefx_editor.py")
# Импорт самого пакета: он тянет JUCE в процесс, а нужен только потомкам.
PLUGIN_IMPORTS = ("pedalboard",)
CODE_DIRS = ("core", "api")
ROOT_FILES = ("webui.py", "reelsi.py", "doctor.py")

PROC_MODULES = ("voicefx_scan", "voicefx_render", "voicefx_audio", "voicefx_editor")


def _modules() -> list[Path]:
    """Модули сервера и ядра под сторожем (без процессов-потомков)."""
    skip = {ROOT / name for name in CHILD_MODULES}
    files = [p for d in CODE_DIRS for p in (ROOT / d).rglob("*.py")
             if "__pycache__" not in p.parts]
    files += [ROOT / name for name in ROOT_FILES if (ROOT / name).is_file()]
    return sorted(p for p in files if p not in skip)


def _forbidden_calls(tree: ast.AST) -> list[str]:
    """Вызовы, загружающие плагин: `load_plugin(...)`, `pb.VST3Plugin…` и прочие."""
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in PLUGIN_CALLS:
                found.append(f"строка {node.lineno}: {func.id}()")
            elif isinstance(func, ast.Attribute) and func.attr in PLUGIN_CALLS:
                found.append(f"строка {node.lineno}: .{func.attr}()")
        elif isinstance(node, ast.Name) and node.id == "VST3Plugin":
            found.append(f"строка {node.lineno}: VST3Plugin")
    return found


def _forbidden_imports(tree: ast.AST, top_level: bool = False) -> list[str]:
    """Импорты pedalboard в модуле сервера/ядра: JUCE в живом процессе.

    `top_level=True` — только импорты уровня модуля: именно они выполняются ДО
    тела точки входа, а ленивый `import pedalboard` внутри функции это ровно то,
    чего мы хотим (плагин грузится после гашения окон ошибок).
    """
    found: list[str] = []
    nodes = tree.body if top_level else list(ast.walk(tree))
    for node in nodes:
        names: list[tuple[str, int]] = []
        if isinstance(node, ast.Import):
            names = [(a.name.split(".")[0], node.lineno) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [(node.module.split(".")[0], node.lineno)]
        for name, lineno in names:
            if name in PLUGIN_IMPORTS:
                found.append(f"строка {lineno}: import {name}")
    return found


def _guard_violations(src: str | None = None) -> list[str]:
    """Нарушения сторожа по дереву. `src` — подмена исходника (мутация сторожа)."""
    bad: list[str] = []
    for path in _modules():
        rel = path.relative_to(ROOT).as_posix()
        text = src if src is not None else path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError as e:                 # pragma: no cover — исходник сломан
            bad.append(f"{rel}: не разобран ({e})")
            continue
        for what in _forbidden_calls(tree) + _forbidden_imports(tree):
            bad.append(f"{rel}: {what}")
    return bad


def test_no_plugin_loading_outside_child_processes():
    """Плагин грузится только в процессах-потомках: в сервере и ядре — нигде.

    Не grep: имена (`load_plugin`, `VST3Plugin`) стоят и в комментариях — «так
    делать нельзя», — и сторож на них краснел бы вечно. Разбор AST смотрит на
    вызовы и импорты.
    """
    bad = _guard_violations()
    assert not bad, ("чужой плагин грузится в процессе сервера (JUCE оставит его "
                     "потоки жить до перезапуска):\n  " + "\n  ".join(bad))


def test_guard_catches_the_returned_call():
    """Мутация сторожа: вернули вызов в исходник — сторож обязан покраснеть.

    Механическая проверка того, что сторож видит нарушение, а не «просто зелёный»:
    тот же исходник `core/voicefx.py` с одной добавленной строкой о загрузке
    плагина. Своя проверка сторожа нужна потому, что «ноль нарушений» одинаково
    выглядит и у сломанного сторожа.
    """
    path = ROOT / "core" / "voicefx.py"
    text = path.read_text(encoding="utf-8")
    assert not _guard_violations(text), "сторож краснеет на чистом файле"
    mutated = text + "\n\n\ndef _mutation():\n    return load_plugin('x.vst3')\n"
    bad = _guard_violations(mutated)
    assert any("load_plugin" in b for b in bad), bad


# --------------------------------------------------------------------------- #
# Фальшивый процесс сканирования: файл результатов пишет он, кеш — родитель
# --------------------------------------------------------------------------- #
def _scan_job(cmd: list[str]) -> tuple[dict[str, Any], str]:
    """Задание и путь файла результатов из команды процесса."""
    with open(cmd[cmd.index("--job") + 1], encoding="utf-8") as f:
        return json.load(f), cmd[cmd.index("--results") + 1]


def _write_names(results: str, done: dict[str, Any], item: dict[str, str],
                 names: list[str]) -> None:
    """Ответ процесса по одному плагину — как его пишет core/voicefx_scan."""
    done[item["key"]] = {"ok": True, "names": names}
    with open(results, "w", encoding="utf-8") as f:
        json.dump(done, f)


def _ok_child(calls: list[Any], names_for: Callable[[str], list[str]]) -> Callable[..., Any]:
    """Заглушка процесса сканирования: читает задание, пишет имена, выходит с 0.

    `--check` (проверка пакета) в задание не заглядывает: у неё работы нет.
    """
    def fake(cmd, what, timeout, emit=voicefx.console_emit, cancelled=None):
        cmd = [str(c) for c in cmd]
        calls.append((cmd, None))
        if "--job" not in cmd:
            return voicefx._ChildRun([], [], 0)
        job, results = _scan_job(cmd)
        calls[-1] = (cmd, job)
        done: dict[str, Any] = {}
        for item in job["paths"]:
            _write_names(results, done, item, names_for(item["path"]))
        return voicefx._ChildRun([{"scanned": len(job["paths"])}], [], 0)
    return fake


def _crash_child(calls: list[Any], crash_on: str) -> Callable[..., Any]:
    """Заглушка процесса, который ПАДАЕТ на плагине с именем `crash_on`.

    По имени файла, а не по номеру в задании: порядок обхода папки плагинов не
    задан, и тест на «упал на втором» зависел бы от файловой системы.

    Пишет то же, что и настоящий сканер: `ok: null` перед загрузкой плагина и
    ответ после неё — и уходит с кодом, как нативный сбой чужой DLL.
    """
    def fake(cmd, what, timeout, emit=voicefx.console_emit, cancelled=None):
        cmd = [str(c) for c in cmd]
        job, results = _scan_job(cmd)
        calls.append((cmd, job))
        done: dict[str, Any] = {}
        for item in job["paths"]:
            done[item["key"]] = {"ok": None, "names": []}     # «начали грузить»
            with open(results, "w", encoding="utf-8") as f:
                json.dump(done, f)
            if os.path.basename(item["path"]) == crash_on:
                return voicefx._ChildRun([], [], -1073741819)  # 0xC0000005
            done[item["key"]] = {"ok": True, "names": [os.path.basename(item["path"])]}
            with open(results, "w", encoding="utf-8") as f:
                json.dump(done, f)
        return voicefx._ChildRun([{"scanned": len(job["paths"])}], [], 0)
    return fake


def _found(root) -> list[str]:
    """Найденные *.vst3 в каталоге теста — то же, что делает обход папок.

    Подменяет `voicefx._found_vst3`, а не переменную окружения: переменная лишь
    ДОБАВЛЯЕТ каталоги к системным, и тест, оставив `REELSI_VST3_DIRS`, всё равно
    упирался бы в общий каталог плагинов машины (пусть и с пустой заглушкой от
    сторожа tests/conftest.py). Здесь список путей задан тестом целиком, и в
    задание сканера не попадает ничего, кроме своих файлов.

    Внутрь бандла (`X.vst3/…`) не заходим: как и настоящий обход, плагином
    считаем саму папку с этим расширением.
    """
    out: list[str] = []
    for base, dirs, files in os.walk(root):
        for name in sorted(files) + sorted(dirs):
            full = os.path.join(base, name)
            if name.lower().endswith(".vst3"):
                out.append(full)
            elif os.path.isdir(full):
                out += _found(full)
    return sorted(set(out))


def _plugin(tmp_path, name: str):
    """Файл-плагин во временной папке (папка-бандл, как настоящий .vst3)."""
    path = tmp_path / name
    path.mkdir()
    (path / "plugin.bin").write_bytes(b"juce")
    return path


def _stem(path: str) -> list[str]:
    """Имя плагина, как его отдаёт настоящий сканер одиночке: имя файла без .vst3.

    Оболочка (WaveShell) отдаёт несколько имён — тогда элементов списка столько
    же; здесь у нас одиночки, и заголовок в списке — имя без расширения.
    """
    return [os.path.splitext(os.path.basename(path))[0]]


def _scan_calls(calls: list[Any]) -> list[tuple[list[str], dict[str, Any]]]:
    """Только вызовы сканирования, без `--check` (у проверки пакета задания нет)."""
    return [(cmd, job) for cmd, job in calls if job is not None]


@pytest.fixture(autouse=True)
def own_voicefx(tmp_path, monkeypatch):
    """Своя папка запечённых треков и свой кеш имён на каждый тест.

    Без этого тест писал бы в боевой `_voicefx/` (сторож изоляции tests/conftest.py
    валит за это сессию), а кеш имён досканировался бы в боевой файл профиля.
    """
    monkeypatch.setattr(voicefx, "VOICEFX_DIR", str(tmp_path / "_voicefx"))
    monkeypatch.setattr(voicefx, "_VST3_CACHE", None)
    monkeypatch.setenv("REELSI_VST3_SCAN", str(tmp_path / "vst3_scan.json"))


# --------------------------------------------------------------------------- #
# Список: дочерний процесс, кеш на диске, досканирование
# --------------------------------------------------------------------------- #
def test_cache_path_is_its_own_state_file(tmp_path, monkeypatch):
    """`REELSI_VST3_SCAN` задаёт файл кэша: изолированный профиль не правит боевой."""
    from core import voicefx_scan
    own = tmp_path / "own.json"
    monkeypatch.setenv("REELSI_VST3_SCAN", str(own))
    assert voicefx_scan.cache_path() == str(own)
    monkeypatch.delenv("REELSI_VST3_SCAN")
    monkeypatch.setattr(voicefx, "VOICEFX_DIR", str(tmp_path / "_voicefx"))
    assert voicefx_scan.cache_path() == str(tmp_path / "_voicefx" / "vst3_scan.json")


def test_list_vst3_sends_plugins_to_child_process(tmp_path, monkeypatch):
    """Имена читает дочерний процесс: список и пути уезжают заданием файлом."""
    (tmp_path / "a.vst3").mkdir()
    (tmp_path / "Shell.vst3").mkdir()
    monkeypatch.setenv("REELSI_VST3_DIRS", str(tmp_path))
    monkeypatch.setattr(voicefx, "_found_vst3", lambda: _found(tmp_path))
    calls: list[Any] = []
    monkeypatch.setattr(voicefx, "_run_child",
                        _ok_child(calls, lambda p: ["One", "Two"]
                                  if "Shell" in p else ["a"]))

    got = voicefx.list_vst3()

    assert got, "список пуст: ответ процесса не разобран"
    assert [p["title"] for p in got] == ["a", "One", "Two"], got
    scans = _scan_calls(calls)
    assert len(scans) == 1, calls
    cmd, job = scans[0]
    assert cmd[1:4] == ["-m", "core.voicefx_scan", "--job"], cmd
    assert "--results" in cmd and job["paths"], "задание уехало без путей"
    paths = [it["path"] for it in job["paths"]]
    assert sorted(paths) == sorted([str(tmp_path / "a.vst3"), str(tmp_path / "Shell.vst3")])


def test_short_8_3_plugin_dir_is_not_thrown_away(tmp_path, monkeypatch):
    """Короткая 8.3-форма каталога плагинов не выкидывает его из списка.

    `tempfile.gettempdir()` на сборочном сервере Windows отдаёт короткую форму
    (`C:\\Users\\RUNNER~1\\…`), а каталог теста приходит длинной
    (`C:\\Users\\runneradmin\\…`). Это один каталог, но `commonpath` на такой паре
    говорит «не внутри», и сторож подменял его заведомо пустым: список плагинов
    выходил ПУСТЫМ, хотя плагин на месте. Проверяется сторож (tests/conftest.py) и
    ключ кеша (`core/voicefx_scan.file_key` зовёт `realpath`): оба обязаны
    сравнивать пути в одной форме.

    Каталог создаётся СВОЙ, в системном временном, и подделывается только ответ
    `gettempdir` — настоящие каталоги плагинов не читаются (дочерний процесс
    подменён заглушкой, как у соседних тестов).
    """
    if os.name != "nt":                            # 8.3 — только Windows
        pytest.skip("короткие имена 8.3 есть только на Windows")
    long_dir = os.path.join(paths.real(tempfile.gettempdir()), "reelsi_8_3_plugin_dir")
    os.makedirs(long_dir, exist_ok=True)
    try:
        (Path(long_dir) / "a.vst3").write_bytes(b"bundle")
        calls: list[Any] = []
        monkeypatch.setattr(voicefx, "_run_child", _ok_child(calls, lambda path: ["a"]))
        monkeypatch.setenv("REELSI_VST3_DIRS", long_dir)
        monkeypatch.setattr(tempfile, "gettempdir", lambda: _short_path(long_dir))

        found = voicefx.list_vst3()

        mine = [p for p in found if paths.real(p["path"]) == paths.real(
            os.path.join(long_dir, "a.vst3"))]
        # Непустой список и есть «сторож не ругается»: он подменил бы каталог
        # заведомо пустым, и плагина в ответе не было бы вовсе.
        assert mine, ("сторож выкинул каталог в короткой 8.3-форме: список пуст", found)
        assert os.path.basename(os.path.dirname(mine[0]["path"])) == \
            os.path.basename(long_dir), mine
    finally:
        shutil.rmtree(long_dir, ignore_errors=True)


def _short_path(path: str) -> str:
    """Короткая 8.3-форма пути (`GetShortPathNameW`); нет короткого имени — как есть."""
    buf = ctypes.create_unicode_buffer(32768)
    n = ctypes.windll.kernel32.GetShortPathNameW(path, buf, 32768)
    return buf.value if n else path


def test_real_plugin_dirs_are_never_touched(tmp_path, monkeypatch):
    """Сторож каталогов: настоящие VST3 в тестах не ищутся и не читаются.

    Сторож из tests/conftest.py (isolate_vst3_dirs) подменяет системные каталоги
    заведомо пустыми временными — здесь проверяется он сам: без своей папки
    плагинов список пуст, а дочерний процесс не поднимается вовсе.
    """
    monkeypatch.delenv("REELSI_VST3_DIRS", raising=False)
    monkeypatch.delenv("AUTOCUT_VST3_DIRS", raising=False)
    tmp = paths.real(tempfile.gettempdir())
    for d in voicefx.vst3_dirs():
        assert os.path.commonpath([paths.real(d), tmp]) == tmp, \
            f"сторож отдал настоящий каталог плагинов: {d}"
    calls: list[Any] = []

    def only_check(cmd, what, timeout, emit=voicefx.console_emit, cancelled=None):
        """Разрешена только проверка пакета: плагинов для чтения нет вовсе."""
        calls.append([str(c) for c in cmd])
        return voicefx._ChildRun([], [], 0)
    monkeypatch.setattr(voicefx, "_run_child", only_check)

    assert voicefx.list_vst3() == []
    assert len(calls) == 1 and calls[0][1:4] == ["-m", "core.voicefx_scan", "--check"], calls


def test_second_list_does_not_start_child_process(tmp_path, monkeypatch):
    """Повторный список берётся из кеша на диске: плагины не читаются вовсе."""
    (tmp_path / "a.vst3").mkdir()
    monkeypatch.setenv("REELSI_VST3_DIRS", str(tmp_path))
    monkeypatch.setattr(voicefx, "_found_vst3", lambda: _found(tmp_path))
    calls: list[Any] = []
    monkeypatch.setattr(voicefx, "_run_child", _ok_child(calls, _stem))

    first = voicefx.list_vst3()
    assert len(_scan_calls(calls)) == 1 and first

    monkeypatch.setattr(voicefx, "_VST3_CACHE", None)   # кеш в памяти сброшен: остаётся диск
    second = voicefx.list_vst3()
    assert second == first, "список из кеша на диске разошёлся с первым"
    assert len(_scan_calls(calls)) == 1, "второй список снова читал плагины вместо кеша" 


def test_changed_plugin_is_the_only_one_rescanned(tmp_path, monkeypatch):
    """Изменился один плагин — в процесс уезжает только он."""
    _plugin(tmp_path, "old.vst3")
    new = _plugin(tmp_path, "new.vst3")
    monkeypatch.setenv("REELSI_VST3_DIRS", str(tmp_path))
    monkeypatch.setattr(voicefx, "_found_vst3", lambda: _found(tmp_path))
    calls: list[Any] = []
    monkeypatch.setattr(voicefx, "_run_child", _ok_child(calls, _stem))

    voicefx.list_vst3()
    first = _scan_calls(calls)
    assert len(first) == 1 and len(first[0][1]["paths"]) == 2, calls

    (new / "plugin.bin").write_bytes(b"updated!")       # размер и mtime другие
    monkeypatch.setattr(voicefx, "_VST3_CACHE", None)
    got = voicefx.list_vst3()

    scans = _scan_calls(calls)
    assert len(scans) == 2, "изменение плагина не привело к досканированию: %r" % (scans,)
    assert [it["path"] for it in scans[1][1]["paths"]] == [str(new)], \
        "досканировали не только изменившийся плагин"
    assert {p["title"] for p in got} == {"old", "new"}


# --------------------------------------------------------------------------- #
# Падение и зависание: остальные плагины не теряются
# --------------------------------------------------------------------------- #
def test_crashed_plugin_is_marked_and_the_rest_are_read(tmp_path, monkeypatch):
    """Плагин, уронивший процесс, помечается «не грузится» и пропускается.

    Две вещи разом: упавший в кеш записан с ключом файла (значит, пока файл не
    изменился, второй раз его не грузят), а следующий за ним — прочитан. Иначе
    один битый плагин отменял бы список целиком.
    """
    _plugin(tmp_path, "ok-first.vst3")
    _plugin(tmp_path, "bad.vst3")
    _plugin(tmp_path, "ok-last.vst3")
    monkeypatch.setenv("REELSI_VST3_DIRS", str(tmp_path))
    monkeypatch.setattr(voicefx, "_found_vst3", lambda: _found(tmp_path))
    calls: list[Any] = []
    monkeypatch.setattr(voicefx, "_run_child", _crash_child(calls, crash_on="bad.vst3"))
    log: list[str] = []
    monkeypatch.setattr(voicefx, "console_emit",
                        lambda line="", **vars: log.append(line.format(**vars) if vars else line))

    got = voicefx.list_vst3()

    titles = {p["title"] for p in got}
    assert titles == {"ok-first", "ok-last"}, titles
    scans = _scan_calls(calls)
    assert len(scans) == 2, "после падения процесс не подняли заново"
    second = [it["path"] for it in scans[1][1]["paths"]]
    assert str(tmp_path / "bad.vst3") not in second, "упавший плагин грузят второй раз"

    from core import voicefx_scan
    entries = voicefx_scan.read_cache(voicefx_scan.cache_path())
    bad_key = voicefx_scan.file_key(str(tmp_path / "bad.vst3"))
    assert entries[bad_key] == {"ok": False, "names": []}, \
        "упавший плагин не записан в кеш как «не грузится»"
    assert any("bad.vst3" in line for line in log), log


def test_crashed_plugin_is_not_loaded_again(tmp_path, monkeypatch):
    """Тот же файл во второй раз не грузится: в процессе ни одного плагина."""
    _plugin(tmp_path, "bad.vst3")
    monkeypatch.setenv("REELSI_VST3_DIRS", str(tmp_path))
    monkeypatch.setattr(voicefx, "_found_vst3", lambda: _found(tmp_path))
    calls: list[Any] = []
    monkeypatch.setattr(voicefx, "_run_child", _crash_child(calls, crash_on="bad.vst3"))
    voicefx.list_vst3()
    
    monkeypatch.setattr(voicefx, "_VST3_CACHE", None)
    started: list[Any] = []

    def boom(cmd, *a, **kw):
        cmd = [str(c) for c in cmd]
        started.append(cmd)
        assert "--job" not in cmd, "упавший плагин грузят повторно"
        return voicefx._ChildRun([], [], 0)      # проверке пакета делать нечего
    monkeypatch.setattr(voicefx, "_run_child", boom)

    assert voicefx.list_vst3() == [], "упавший плагин попал в список"
    assert all("--job" not in cmd for cmd in started), started


def test_changed_crashed_plugin_is_tried_again(tmp_path, monkeypatch):
    """Плагин обновили — его пробуют снова: ключ кеша изменился."""
    bad = _plugin(tmp_path, "bad.vst3")
    monkeypatch.setenv("REELSI_VST3_DIRS", str(tmp_path))
    monkeypatch.setattr(voicefx, "_found_vst3", lambda: _found(tmp_path))
    calls: list[Any] = []
    monkeypatch.setattr(voicefx, "_run_child", _crash_child(calls, crash_on="bad.vst3"))
    assert voicefx.list_vst3() == []

    (bad / "plugin.bin").write_bytes(b"updated!")
    monkeypatch.setattr(voicefx, "_VST3_CACHE", None)
    monkeypatch.setattr(voicefx, "_run_child", _ok_child(calls, lambda p: ["Bad"]))
    got = voicefx.list_vst3()
    # Обновлённый файл прочитан заново, а не взят из записи «не грузится»
    assert [p["title"] for p in got] == ["bad"], got


def test_hung_child_is_killed_by_pid(tmp_path, monkeypatch):
    """Зависший процесс снимается по PID, а не ждёт вечно."""
    _plugin(tmp_path, "hang.vst3")
    monkeypatch.setenv("REELSI_VST3_DIRS", str(tmp_path))
    monkeypatch.setattr(voicefx, "_found_vst3", lambda: _found(tmp_path))
    killed: list[int] = []

    class _Child:
        pid = 424242
        returncode = 0

        def poll(self):
            return None                       # «плагин завис»: процесс жив, вывода нет

        def wait(self, timeout=None):
            return 0                          # «снялся»: ждать после kill нечего

    monkeypatch.setattr(voicefx.subprocess, "Popen", lambda cmd, **kw: _Child())
    monkeypatch.setattr(voicefx, "kill_tree", lambda p: killed.append(p.pid))
    monkeypatch.setattr(voicefx, "VST_SCAN_TIMEOUT", 0.2)

    # Список отдаётся БЕЗ зависшего плагина, а не падает: один битый файл не
    # отменяет остальные (человек открыл блок настроек голоса и ждёт список).
    assert voicefx.list_vst3() == []
    assert killed == [424242], "зависший процесс не снят по PID"
    from core import voicefx_scan
    key = voicefx_scan.file_key(str(tmp_path / "hang.vst3"))
    assert voicefx_scan.read_cache(voicefx_scan.cache_path())[key] == \
        {"ok": False, "names": []}, "зависший плагин не помечен «не грузится»"


def test_hung_plugin_does_not_lose_the_rest(tmp_path, monkeypatch):
    """Завис на одном — остальные в списке: виновник помечен, список отдан."""
    _plugin(tmp_path, "good.vst3")
    _plugin(tmp_path, "hang.vst3")
    monkeypatch.setenv("REELSI_VST3_DIRS", str(tmp_path))
    monkeypatch.setattr(voicefx, "_found_vst3", lambda: _found(tmp_path))

    def fake_child(cmd, what, timeout, emit=voicefx.console_emit, cancelled=None):
        cmd = [str(c) for c in cmd]
        if "--job" not in cmd:
            return voicefx._ChildRun([], [], 0)
        with open(cmd[cmd.index("--job") + 1], encoding="utf-8") as f:
            job = json.load(f)
        results = cmd[cmd.index("--results") + 1]
        done: dict[str, Any] = {}
        for item in job["paths"]:
            if os.path.basename(item["path"]).startswith("hang"):
                # «Завис»: пометка «начали» осталась в файле, ответа нет —
                # ровно то, что видит родитель после снятия процесса по PID.
                done[item["key"]] = {"ok": None, "names": []}
                with open(results, "w", encoding="utf-8") as f:
                    json.dump(done, f)
                raise ReelsiError(_timeout_msg())
            done[item["key"]] = {"ok": True, "names": _stem(item["path"])}
            with open(results, "w", encoding="utf-8") as f:
                json.dump(done, f)
        return voicefx._ChildRun([{"scanned": len(job["paths"])}], [], 0)
    monkeypatch.setattr(voicefx, "_run_child", fake_child)
    monkeypatch.setattr(voicefx, "VST_SCAN_TIMEOUT", 0.2)

    got = voicefx.list_vst3()

    assert [p["title"] for p in got] == ["good"], got
    from core import voicefx_scan
    entries = voicefx_scan.read_cache(voicefx_scan.cache_path())
    assert entries[voicefx_scan.file_key(str(tmp_path / "hang.vst3"))]["ok"] is False
    assert entries[voicefx_scan.file_key(str(tmp_path / "good.vst3"))]["ok"] is True


def _timeout_msg():
    """Та же ошибка таймаута, что поднимает `_run_child` (по `err` её и узнают)."""
    from core.umsg import umsg
    return umsg("voicefx_render_failed", "VST3: превышен таймаут (0.2 с)", err="timeout")


def test_scan_without_pedalboard_is_clear_error(tmp_path, monkeypatch):
    """Нет пакета pedalboard — ошибка про пакет, а не пустой список.

    Досканировать есть что (`a.vst3`), а пакета нет: ошибка рождается в дочернем
    процессе (`core/voicefx_scan`), а родитель отдаёт её наружу ТЕМ ЖЕ кодом — по
    нему фронт берёт перевод. Процесс здесь настоящий, и в PYTHONPATH первым едет
    поддельный `pedalboard`, который на импорте бросает ImportError: настоящий
    пакет (а с ним JUCE) в тест не попадает ни при каком PYTHONPATH.
    """
    _plugin(tmp_path, "a.vst3")
    monkeypatch.setenv("REELSI_VST3_DIRS", str(tmp_path))
    monkeypatch.setattr(voicefx, "_found_vst3", lambda: _found(tmp_path))
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "pedalboard.py").write_text(
        "raise ImportError('pedalboard нет: подделка для теста')\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(stub), str(ROOT)]))
    monkeypatch.setattr(voicefx, "_VST3_CACHE", None)

    with pytest.raises(ReelsiError) as e:
        voicefx.list_vst3()
    assert e.value.code == "vst_unavailable", str(e.value)


# --------------------------------------------------------------------------- #
# Применение цепочки: задание и отмена
# --------------------------------------------------------------------------- #
def _wav(path, seconds: float = 0.5, rate: int = 48000) -> None:
    """Настоящий WAV на входе рендера: извлечение идёт через подменённый ffmpeg."""
    import wave
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))


def _extract_stub(calls: list[list[str]]):
    """Заглушка `subprocess.run`: вместо ffmpeg делает пустой WAV на месте выхода."""
    def fake_run(cmd, **kw):
        cmd = [str(c) for c in cmd]
        calls.append(cmd)
        if cmd and cmd[0] == "ffmpeg":
            _wav(cmd[-1])
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    return fake_run


def test_render_chain_goes_to_child_process(tmp_path, monkeypatch):
    """Цепочка уезжает в core/voicefx_render заданием: порядок, имена, состояние."""
    src = tmp_path / "cam1.wav"
    _wav(src)
    out = tmp_path / "out.wav"
    monkeypatch.setattr(voicefx.subprocess, "run", _extract_stub([]))
    calls: list[Any] = []

    def fake_child(cmd, what, timeout, emit=voicefx.console_emit, cancelled=None):
        cmd = [str(c) for c in cmd]
        calls.append((cmd, what, timeout))
        with open(cmd[cmd.index("--job") + 1], encoding="utf-8") as f:
            job = json.load(f)
        calls[-1] = (cmd, what, timeout, job)
        _wav(job["out"])
        return voicefx._ChildRun([{"done": True, "frames": 10}], [], 0)
    monkeypatch.setattr(voicefx, "_run_child", fake_child)

    state = base64.b64encode(b"\x01\x02state").decode("ascii")
    fx = {"vst": [
        {"path": "C:/first.vst3", "name": "", "state": state, "on": True},
        {"path": "C:/off.vst3", "on": False},
        {"path": "C:/shell.vst3", "name": "Inner", "on": True}]}
    voicefx.render(str(src), fx, str(out))

    cmd, what, timeout, job = calls[0]
    assert cmd[1:4] == ["-m", "core.voicefx_render", "--job"], cmd
    assert what == "VST-цепочка" and timeout == voicefx.VST_RENDER_TIMEOUT
    chain = job["chain"]
    assert [c["path"] for c in chain] == ["C:/first.vst3", "C:/shell.vst3"], \
        "порядок цепочки или пропуск выключенного плагина сломались"
    assert chain[1]["name"] == "Inner", "имя внутри оболочки не доехало до процесса"
    assert chain[0]["state_b64"] == state, "состояние плагина не доехало до процесса"
    assert job["out"] == str(out), "результат процесса кладётся не туда"
    assert out.is_file() and out.stat().st_size > 0


def test_cancel_kills_child_process(tmp_path, monkeypatch):
    """«Стоп» снимает процесс цепочки по PID, а не ждёт конца блока."""
    src = tmp_path / "cam1.wav"
    _wav(src)
    monkeypatch.setattr(voicefx.subprocess, "run", _extract_stub([]))

    class _Child:
        pid = 777001
        stdout: Any = []
        returncode = 0

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0                          # «снялся»: ждать после kill нечего

    monkeypatch.setattr(voicefx.subprocess, "Popen", lambda cmd, **kw: _Child())
    monkeypatch.setattr(voicefx, "pump_stdout", lambda p: queue.Queue())
    killed: list[int] = []
    monkeypatch.setattr(voicefx, "kill_tree", lambda p: killed.append(p.pid))

    fx = {"vst": [{"path": "C:/p.vst3", "on": True}]}
    with pytest.raises(ReelsiError) as e:
        voicefx.render(str(src), fx, str(tmp_path / "out.wav"),
                       cancelled=lambda: True)
    assert e.value.code == "voicefx_cancelled", str(e.value)
    assert killed == [777001], "отменённый процесс не снят по PID"


def test_child_error_keeps_translation_code(tmp_path, monkeypatch):
    """Ошибка процесса уезжает наверх ТЕМ ЖЕ кодом: по нему фронт берёт перевод."""
    src = tmp_path / "cam1.wav"
    _wav(src)
    monkeypatch.setattr(voicefx.subprocess, "run", _extract_stub([]))

    def fake_child(cmd, what, timeout, emit=voicefx.console_emit, cancelled=None):
        return voicefx._ChildRun([], [ReelsiError(_umsg_vst())], 1)
    monkeypatch.setattr(voicefx, "_run_child", fake_child)

    with pytest.raises(ReelsiError) as e:
        voicefx.render(str(src), {"vst": [{"path": "C:/p.vst3", "on": True}]},
                       str(tmp_path / "out.wav"))
    assert e.value.code == "vst_unavailable"


def _umsg_vst():
    """Ошибка «нет пакета» тем же кодом, каким её поднимает процесс."""
    from core.umsg import umsg
    return umsg("vst_unavailable", "Нет пакета pedalboard — VST-плагины недоступны")


# --------------------------------------------------------------------------- #
# Системные окна ошибок Windows: гасятся ДО чужого плагина
# --------------------------------------------------------------------------- #
def _main_block(module: str) -> list[ast.stmt]:
    """Тела `if __name__ == "__main__"` модуля процесса."""
    path = ROOT / "core" / ("%s.py" % module)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    blocks = [n for n in tree.body
              if isinstance(n, ast.If) and any(isinstance(c, ast.Constant)
                                               and c.value == "__main__"
                                               for c in ast.walk(n.test))]
    assert blocks, f"{module}: нет точки входа __main__"
    return list(blocks[0].body)


def _call_name(node: ast.stmt) -> str:
    """Имя вызванной функции в операторе-вызове или пустая строка."""
    call = node.value if isinstance(node, ast.Expr) else None
    if call is None or not isinstance(call, ast.Call):
        return ""
    return call.func.attr if isinstance(call.func, ast.Attribute) else ""


@pytest.mark.parametrize("module", PROC_MODULES)
def test_child_calls_no_error_windows_first(module):
    """Первое действие точки входа процесса — гашение окон ошибок Windows.

    Статически и по порядку операторов: окно «Application Error» поднимает
    система в момент падения чужой DLL, и до этого момента режим уже должен
    стоять. Гарантия тут может быть только одна — вызов раньше всего остального,
    что вообще может тронуть чужой код (и раньше перенастройки UTF-8: ошибка
    печати не должна помешать гашению).

    И ни одного `import pedalboard` на уровне модуля: JUCE попал бы в процесс
    ещё до тела точки входа (импорт идёт лениво, из функций).
    """
    body = _main_block(module)
    names = [_call_name(node) for node in body]
    assert names and names[0] == "no_error_windows", (
        f"{module}: точка входа начинается не с гашения окон ошибок, а с {names[:2]}")
    assert "utf8_stdout" in names[:2], f"{module}: UTF-8 перенастраивается не сразу: {names[:3]}"
    tree = ast.parse((ROOT / "core" / ("%s.py" % module)).read_text(encoding="utf-8"))
    assert not _forbidden_imports(tree, top_level=True), (
        f"{module}: pedalboard импортируется на уровне модуля — JUCE попадёт в "
        "процесс раньше, чем будут погашены окна ошибок")


def test_child_starts_plugins_only_after_error_mode(tmp_path, monkeypatch):
    """Живая проверка порядка: процесс, дошедший до плагина, окна уже погасил.

    Настоящий `core/voicefx_scan` со всем его путём — от разбора задания до
    чтения имён — и подменённым `pedalboard` (настоящий JUCE в тест не попадает).
    Окна ошибок и загрузка плагина пишутся в один список: если гашение когда-то
    переедет вниз, плагин окажется в списке первым.

    Точка входа здесь — не `main()`, а тело `__main__` (в нём и живёт гашение),
    потому что `cli_error` и `SystemExit` посреди теста не нужны.
    """
    import core.voicefx_proc as vp
    from core import voicefx_scan
    order: list[str] = []
    monkeypatch.setattr(vp, "_kernel32", lambda: SimpleNamespace(
        SetErrorMode=lambda mask: order.append("no_error_windows")))
    monkeypatch.setattr(vp.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "pedalboard", _fake_pedalboard(order, tmp_path))
    work = tmp_path / "work"
    work.mkdir()
    job = work / "job.json"
    job.write_text(json.dumps({"paths": [{"path": "C:/p.vst3", "key": "k"}]}),
                   encoding="utf-8")

    # Тело точки входа: ровно те же два вызова, что и при запуске процесса.
    vp.no_error_windows()
    vp.utf8_stdout()
    voicefx_scan.main(["--job", str(job), "--results", str(work / "res.json")])

    assert order == ["no_error_windows", "plugin"], order


def _fake_pedalboard(order: list[str], tmp_path) -> Any:
    """Поддельный пакет pedalboard: настоящий JUCE в тест не попадает.

    Через `sys.modules`, а не подменой атрибута: процессы импортируют пакет
    ЛЕНИВО, внутри своих функций (`import pedalboard`), и подмена атрибута модуля
    на такой импорт не влияет вовсе. Зато каждый вызов, загружающий плагин,
    записывается в `order` — на этом и стоит проверка порядка.
    """
    import types
    import wave

    class _AudioFile:
        """Читает/пишет настоящий WAV: достаточно, чтобы рендер дошёл до плагина."""

        def __init__(self, path, mode="r", samplerate=0, num_channels=0, format=None):
            self._w = wave.open(
                str(path), mode,
                None if mode == "r" else (num_channels, 2, int(samplerate)))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._w.close()
            return False

        @property
        def samplerate(self):
            return self._w.getframerate()

        @property
        def num_channels(self):
            return self._w.getnchannels()

        @property
        def frames(self):
            return self._w.getnframes()

        def tell(self):
            return self._w.tell()

        def read(self, n):
            import numpy as np
            raw = self._w.readframes(n)
            data = np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0
            return data.reshape(1, -1)

        def write(self, data):
            import numpy as np
            arr = np.asarray(data, dtype="float32")
            self._w.writeframes((np.clip(arr, -1.0, 1.0) * 32767).astype("<i2").tobytes())

    class _VST3Plugin:
        @staticmethod
        def get_plugin_names_for_file(path):
            order.append("plugin")
            return ["Fake"]

    class _Plugin:
        raw_state = b""

        def show_editor(self):
            order.append("plugin")          # окно плагина — тоже его загрузка

    class _Device:                          # pragma: no cover — запас, если позовут
        pass

    class _AudioStream:
        output_device_names: list[str] = []
        default_output_device_name = ""

        def __init__(self, **kw):
            self.running = False

        def __enter__(self):
            self.running = True
            return self

        def __exit__(self, *exc):
            self.running = False
            return False

        def write(self, audio, sample_rate):
            pass

        def close(self):
            pass

    pb = types.ModuleType("pedalboard")
    pb.VST3Plugin = _VST3Plugin                                       # type: ignore[attr-defined]
    pb.load_plugin = lambda path, plugin_name=None: (order.append("plugin"), _Plugin())[1]  # type: ignore[attr-defined]
    pb.Pedalboard = lambda plugins: (lambda data, sr, reset=True: data)  # type: ignore[attr-defined]
    pb.io = SimpleNamespace(AudioFile=_AudioFile, AudioStream=_AudioStream)  # type: ignore[attr-defined]
    pb.__dict__["_Device"] = _Device
    return pb


class _SilentChild:
    """Процесс без вывода: ничего не печатает и сразу «вышел»."""

    pid = 1
    returncode = 0
    stdout: Any = []

    def poll(self):
        return 0


def test_error_mode_asks_windows_for_the_three_dialogs_off(tmp_path, monkeypatch):
    """Маска SetErrorMode — ровно те три флага, что убирают модальные окна.

    Значения из winbase.h: SEM_FAILCRITICALERRORS (0x1), SEM_NOGPFAULTERRORBOX
    (0x2, само «Application Error»), SEM_NOOPENFILEERRORBOX (0x8000). Ошибка в
    числе — окно снова всплывёт и будет держать процесс до нажатия кнопки.
    """
    from core import voicefx_proc as vp
    assert (vp.SEM_FAILCRITICALERRORS | vp.SEM_NOGPFAULTERRORBOX
            | vp.SEM_NOOPENFILEERRORBOX) == 0x8003
    seen: list[int] = []
    monkeypatch.setattr(vp, "_kernel32",
                        lambda: SimpleNamespace(SetErrorMode=seen.append))
    monkeypatch.setattr(vp.sys, "platform", "win32")
    vp.no_error_windows()
    assert seen == [0x8003], "SetErrorMode вызван не той маской: %r" % (seen,)


def test_error_windows_is_noop_off_windows(monkeypatch):
    """На не-Windows ничего не делаем: SetErrorMode там нет вовсе."""
    from core import voicefx_proc as vp
    monkeypatch.setattr(vp.sys, "platform", "linux")
    monkeypatch.setattr(vp, "_kernel32", lambda: pytest.fail(
        "на не-Windows kernel32 не трогаем"))
    vp.no_error_windows()


def test_scan_results_file_protocol(tmp_path):
    """Протокол результатов процесса: `ok: null` — «начали и не закончили».

    По этой записи родитель и находит плагин, уронивший процесс. Читается она
    БЕЗ приведения к bool (в отличие от кеша), иначе «упал» и «прочитан» стали бы
    одним и тем же, и битый плагин грузился бы при каждом открытии блока.
    """
    from core import voicefx_scan
    path = tmp_path / "results.json"
    path.write_text(json.dumps({
        "a": {"ok": True, "names": ["A"]},
        "b": {"ok": None, "names": []},
        "c": {"ok": False, "names": []}}), encoding="utf-8")
    done = voicefx_scan.read_results(str(path))
    assert done["a"]["ok"] is True and done["a"]["names"] == ["A"]
    assert done["b"]["ok"] is None, "«начали и не закончили» потерялось"
    assert done["c"]["ok"] is False
    assert voicefx_scan.read_results(str(tmp_path / "нет.json")) == {}


def test_scan_cache_protocol_is_versioned(tmp_path):
    """Кеш читается только своей версии: чужой/битый файл — пусто, а не мусор."""
    from core import voicefx_scan
    path = tmp_path / "vst3_scan.json"
    voicefx_scan.write_cache(str(path), {"k": {"ok": True, "names": ["A"]}})
    assert voicefx_scan.read_cache(str(path)) == {"k": {"ok": True, "names": ["A"]}}
    path.write_text(json.dumps({"version": 999, "entries": {"k": {"ok": True}}}),
                    encoding="utf-8")
    assert voicefx_scan.read_cache(str(path)) == {}
    path.write_text("{битый", encoding="utf-8")
    assert voicefx_scan.read_cache(str(path)) == {}
