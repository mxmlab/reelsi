# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сторож раскладки репозитория (TASKS.md).

Раскладка — не косметика. До неё 49 модулей движка лежали в корне вперемешку с
точками входа, а каждый модуль считал свою папку от `__file__` — то есть был
уверен, что лежит в корне репозитория. Переезд в `core/` сломал бы это МОЛЧА:
`ai_config.json`, `insertlib.json`, `terms.json`, `styles/`, `speakers/` ищутся
от той же папки, и пользователь просто «потерял» бы ключи, базу вставок и стили —
без единой ошибки в логе. Поэтому пути считает ровно один модуль (`core/paths.py`),
а раскладку держит этот тест.

Второй капкан — двойная загрузка: пока модуль импортируется и как `styles`, и как
`core.styles`, это ДВА модуля с разным состоянием (своими кэшами, своим `INDEX_PATH`),
и правка в одном не видна другому. Отсюда проверка «один модуль — одно имя».

Проверки «личные файлы в корне» и «данные в data/» запускают подпроцесс, который
печатает фактические пути модулей: спрашивать у кода, а не у списка в тесте —
единственный способ поймать молча переехавший путь.
"""
import json
import os
import re
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# 41 модуль, переехавший в core/ (TASKS.md, «Целевая раскладка»).
CORE_MODULES = [
    "align", "app_meta", "arrowfix", "asr_backends", "assets", "bootstrap", "breath",
    "censor", "ctc_asr", "cuda_env", "cutstages", "cutstate", "device", "draftrender",
    "drp", "falign", "falign_cli", "fileio", "fonts", "gigaam_subs", "insertlib",
    "omni_asr", "omni_cut", "omni_review", "roto", "selfcheck", "speakers", "ssm",
    "styles", "subs", "subtitle_blobs", "subtitle_xml", "sync", "terms", "transcribe",
    "umsg", "vad", "verify_jsx", "whisper_cpp", "xmlbuild", "ytmusic",
]
CORE_PACKAGES = ["aicut", "gigaam_cut", "xml2ae"]
ROOT_ENTRY_POINTS = {"webui.py", "reelsi.py", "doctor.py"}

# Ядро: тут `__file__` и sys.path не считает никто, кроме core/paths.py.
CORE_DIRS = ("core", "api")

_FILE_PATH_RE = re.compile(r"abspath\(__file__\)|dirname\(__file__\)|Path\(__file__\)")
_SYS_PATH_RE = re.compile(r"sys\.path\.(insert|append)")
_SUBPROC_PY_RE = re.compile(r"py_exec\(\),\s*os\.path\.join\(")

# Подпроцесс печатает одно и то же для трёх проверок: имена загруженных модулей и
# фактические пути. Импорт идёт в ЛЮБОЙ раскладке (core.X, иначе X) — сторож обязан
# запускаться и до переноса, и после, иначе его вывод до/после не с чем сравнить.
_PROBE = r'''
import importlib, json, os, sys
sys.path.insert(0, os.getcwd())


def mod(dotted):
    last = None
    for prefix in ("core.", ""):
        try:
            return importlib.import_module(prefix + dotted)
        except ImportError as e:
            last = e
    raise SystemExit("не импортируется %s: %s" % (dotted, last))


import webui  # noqa: F401  (тянет весь бэкенд и движок)

cfg = mod("aicut.config")
core = mod("api._core")
censor = mod("censor")
out = {
    "personal": {
        "AI_CONFIG_PATH": cfg.AI_CONFIG_PATH,
        "AI_LOG_PATH": cfg.AI_LOG_PATH,
        "JOB_LOCK_PATH": core.JOB_LOCK_PATH,
        "UI_STATE_PATH": core.UI_STATE_PATH,
        "INSERTLIB_INDEX": mod("insertlib").INDEX_PATH,
        "TERMS_PATH": mod("terms").TERMS_PATH,
        "BADWORDS_USER": censor.USER_PATHS["bad"],
        "OKWORDS_USER": censor.USER_PATHS["ok"],
        "HALLUC_PHRASES_PATH": mod("omni_cut").HALLUC_PHRASES_PATH,
        "VIDEO_DIR": mod("api.videogen").VIDEO_DIR,
        "STYLE_DIR": mod("styles").STYLE_DIR,
        "SPEAKER_DIR": mod("speakers").SPEAKER_DIR,
    },
    "data": {
        "ENGINES_JSON": mod("asr_backends").ENGINES_JSON,
        "BREATH_MODEL": mod("breath").MODEL_JSON,
        "BADWORDS_BASE": censor.BASE_PATHS["bad"],
        "OKWORDS_BASE": censor.BASE_PATHS["ok"],
        "DRP_TEMPLATE": mod("drp").TEMPLATE,
    },
    "base": mod("reelsi").DEFAULT_BASE,
    # Снимок sys.modules — ПОСЛЕ всех импортов выше: ядро (styles, insertlib, …)
    # подтягивается лениво, из обработчиков api/, а не на импорте webui.
    "modules": sorted(sys.modules),
}
print(json.dumps(out, ensure_ascii=False))
'''


def _tracked_files():
    """Отслеживаемые файлы, ОСТАВШИЕСЯ на диске.

    `git ls-files` показывает индекс, а он отстаёт: переезд файлов до коммита
    выглядит как «старый путь ещё отслеживается, нового нет». Старый путь при этом
    файла на диске не имеет — это артефакт индекса, а не нарушение раскладки;
    после `git add` оба списка совпадают.
    """
    out = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True, encoding="utf-8")
    return [f for f in out.splitlines() if f and os.path.exists(os.path.join(ROOT, f))]


def _core_py_files():
    """Все .py ядра (core/ и api/) — то, что обязано жить без `__file__` и sys.path."""
    found = []
    for d in CORE_DIRS:
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, d)):
            dirnames[:] = [x for x in dirnames if x != "__pycache__"]
            for f in sorted(filenames):
                if f.endswith(".py"):
                    found.append(os.path.relpath(os.path.join(dirpath, f), ROOT).replace("\\", "/"))
    return found


def _probe():
    """Пути и имена модулей из чистого окружения (без REELSI_*/AUTOCUT_*)."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("REELSI_", "AUTOCUT_"))}
    env["REELSI_NO_BROWSER"] = "1"
    r = subprocess.run([sys.executable, "-c", _PROBE], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600)
    assert r.returncode == 0, "подпроцесс раскладки упал:\n" + (r.stdout or "") + (r.stderr or "")
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
    assert lines, "подпроцесс раскладки ничего не напечатал"
    return json.loads(lines[-1])


# --------------------------------------------------------------------------- #
# Корень: только точки входа
# --------------------------------------------------------------------------- #
def test_root_has_only_entry_point_modules():
    """Отслеживаемые *.py в корне — ровно webui.py, reelsi.py, doctor.py."""
    root_py = {f for f in _tracked_files() if f.endswith(".py") and "/" not in f}
    assert root_py == ROOT_ENTRY_POINTS, (
        "в корне лишние модули: %s" % sorted(root_py - ROOT_ENTRY_POINTS))


# --------------------------------------------------------------------------- #
# Пути считает только core/paths.py
# --------------------------------------------------------------------------- #
def test_paths_come_from_paths_module_only():
    """В core/ и api/ нет `abspath(__file__)` / `dirname(__file__)` / `Path(__file__)`."""
    bad = []
    for rel in _core_py_files():
        if rel == "core/paths.py":
            continue
        text = open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace").read()
        for i, line in enumerate(text.splitlines(), 1):
            if _FILE_PATH_RE.search(line):
                bad.append("%s:%d: %s" % (rel, i, line.strip()))
    assert not bad, "папка модуля считается от __file__ вне core/paths.py:\n" + "\n".join(bad)


def test_no_sys_path_edits_in_core():
    """В core/ и api/ нет sys.path.insert/append — иначе модуль грузится дважды."""
    bad = []
    for rel in _core_py_files():
        text = open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace").read()
        for i, line in enumerate(text.splitlines(), 1):
            if _SYS_PATH_RE.search(line):
                bad.append("%s:%d: %s" % (rel, i, line.strip()))
    assert not bad, "правка sys.path в ядре:\n" + "\n".join(bad)


def test_subprocesses_launch_via_dash_m():
    """Модули ядра запускаются подпроцессом через `-m`, а не по пути к файлу."""
    bad = []
    rels = _core_py_files() + ["reelsi.py"]
    for rel in rels:
        text = open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace").read()
        for i, line in enumerate(text.splitlines(), 1):
            if _SUBPROC_PY_RE.search(line):
                bad.append("%s:%d: %s" % (rel, i, line.strip()))
    assert not bad, "запуск подпроцесса по пути к файлу вместо -m:\n" + "\n".join(bad)


# --------------------------------------------------------------------------- #
# Один модуль — одно имя
# --------------------------------------------------------------------------- #
def test_one_module_one_name():
    """Модули ядра не грузятся под верхнеуровневыми именами (styles, aicut, …)."""
    info = _probe()
    top = {n for n in info["modules"] if "." not in n}
    leaked = sorted(top & set(CORE_MODULES + CORE_PACKAGES))
    assert not leaked, "модули ядра загрузились как верхнеуровневые: %s" % leaked
    assert "core.styles" in info["modules"], (
        "core.styles не загрузился — ядро импортируется не как пакет")


# --------------------------------------------------------------------------- #
# Личные файлы — в корне, данные репозитория — в data/
# --------------------------------------------------------------------------- #
def test_personal_files_live_in_repo_root():
    """Каждый личный файл пользователя — прямо в корне репозитория.

    Личные файлы в git не попадают, и «где они» знает только код. Съехавший на
    core/ путь = интерфейс без ключей, с пустой базой вставок и без стилей.
    """
    personal = _probe()["personal"]
    bad = {k: v for k, v in personal.items()
           if os.path.dirname(os.path.abspath(v)) != ROOT}
    assert not bad, "личные файлы уехали из корня репозитория: %s" % bad


def test_repo_data_lives_in_data_dir():
    """Данные из репозитория — в data/ и на месте (папка, а не только путь)."""
    data = _probe()["data"]
    want = os.path.join(ROOT, "data")
    bad = {k: v for k, v in data.items() if os.path.dirname(os.path.abspath(v)) != want}
    assert not bad, "данные репозитория не в data/: %s" % bad
    missing = {k: v for k, v in data.items() if not os.path.exists(v)}
    assert not missing, "данных из data/ нет на диске: %s" % missing


def test_default_material_dir_is_above_repo():
    """Папка с материалом по умолчанию — уровнем выше репозитория."""
    base = _probe()["base"]
    assert os.path.abspath(base) == os.path.dirname(ROOT), (
        "папка материала по умолчанию: %s, ожидалась %s" % (base, os.path.dirname(ROOT)))


# --------------------------------------------------------------------------- #
# CLI запускаются
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("cmd", [
    ["-m", "core.aicut", "--help"],
    ["-m", "core.gigaam_cut", "--help"],
    ["-m", "core.xml2ae", "--help"],
    ["reelsi.py", "--help"],
    ["tools/verify_ae.py", "--help"],
    # Остальные CLI со справкой без загрузки моделей (проверено: 0.1–0.4 с)
    ["-m", "core.whisper_cpp", "--help"],
    ["-m", "core.ctc_asr", "--help"],
    ["-m", "core.omni_asr", "--help"],
    ["-m", "core.omni_review", "--help"],
    ["-m", "core.roto", "--help"],
    ["-m", "core.drp", "--help"],
    ["tools/train_breath.py", "--help"],
])
def test_cli_entry_points_work(cmd):
    """`--help` каждого CLI отвечает кодом 0 (модели при этом не грузятся)."""
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    # Справка CLI русская, а консоль подпроцесса на Windows — cp1251/cp1252:
    # без этого argparse падает UnicodeEncodeError на собственном --help.
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable] + cmd, cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=300)
    assert r.returncode == 0, "%s -> код %s\n%s\n%s" % (
        " ".join(cmd), r.returncode, r.stdout, r.stderr)
