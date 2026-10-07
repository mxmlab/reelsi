# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Папка весов GigaAM: одна на приложение, а не домашняя у пакета.

Пакет `gigaam` качает веса в `~/.cache/gigaam`, если `load_model` не дали
`download_root`, — и на машине с нерабочей или доступной только на чтение
домашней папкой (контейнер, сервисная учётка, CI) нарезка падала чужим
`PermissionError`. Выбор папки живёт в `core/gigaam_cache.py`:

1. `REELSI_GIGAAM_CACHE` задана — она: явный выбор человека главнее и готового
   кэша в домашней папке, и самой домашней папки;
2. `~/.cache/gigaam` уже есть — берём её: существующие установки не качают
   гигабайт заново, а веса остаются там, где их ищет и сам пакет;
3. домашняя папка пишется — `~/.cache/gigaam`, она и создаётся;
4. домашняя папка недоступна на запись или её нет — запасная папка ВНУТРИ папки
   приложения, `_model_cache/gigaam` (корень приложения подменяется на `tmp_path`:
   в боевом дереве эта папка лежит в `.gitignore`);
5. ни туда, ни туда — `ReelsiError` с причиной по-русски, а не `PermissionError`
   из недр пакета.

Сеть и настоящие веса тесты НЕ трогают: пакет `gigaam` подменён заглушкой,
домашняя папка — `tmp_path`. Сторож в конце обходит `core/` и `api/` по AST и
требует `download_root` у КАЖДОГО вызова `gigaam.load_model(` — без него веса
снова уезжают в домашнюю папку, и это видно только на живом прогоне.
"""
import ast
import os
import sys
from pathlib import Path
from typing import Any

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import gigaam_cache  # noqa: E402
from core import paths  # noqa: E402
from core.umsg import ReelsiError  # noqa: E402

# Каталоги ядра, где ищутся вызовы пакета GigaAM. `tools/` сюда не входит:
# нарезка и распознавание живут в ядре, а не в утилитах.
CORE_DIRS = ("core", "api")


def _fake_home(monkeypatch: Any, tmp_path: Path, *, with_cache: bool) -> Path:
    """Домашняя папка в `tmp_path` (веса — по флагу), без записи в боевую.

    Подменяются обе переменные: на POSIX `expanduser("~")` читает `HOME`, на
    Windows — `USERPROFILE`. Иначе тест зависел бы от платформы, а на Windows
    ещё и попал бы в боевую домашнюю папку владельца.
    """
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    if with_cache:
        (home / ".cache" / "gigaam").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("HOMEDRIVE", raising=False)
    monkeypatch.delenv("HOMEPATH", raising=False)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("REELSI_GIGAAM_CACHE", raising=False)
    return home


def _patch_root(monkeypatch: Any, tmp_path: Path) -> None:
    """Корень приложения — в `tmp_path`: запасная папка ищется внутри него.

    `paths.root()` читает `ROOT` при каждом вызове, поэтому подмены модульной
    переменной достаточно — иначе тест писал бы в боевое дерево репозитория.
    """
    monkeypatch.setattr(paths, "ROOT", str(tmp_path))


def _no_write(monkeypatch: Any) -> None:
    """Папки не принимают запись: `os.access(..., W_OK)` отвечает «нет».

    Подменяется именно проверка записи (как и просится в задании): сделать
    каталог только для чтения на Windows нельзя — атрибут `ReadOnly` у папки
    запись не запрещает, а `chmod` под суперпользователем не действует.
    """
    monkeypatch.setattr(os, "access", lambda path, mode, **kw: False)


def test_env_wins_over_existing_home_cache(monkeypatch: Any, tmp_path: Path) -> None:
    """Явная переменная главнее всего: даже готовая `~/.cache/gigaam` её не перебивает."""
    _fake_home(monkeypatch, tmp_path, with_cache=True)
    own = tmp_path / "own_gigaam"
    monkeypatch.setenv("REELSI_GIGAAM_CACHE", str(own))
    assert gigaam_cache.gigaam_dir() == str(own)
    assert os.path.isdir(str(own)), "папку из переменной не завели"


def test_existing_home_cache_wins(monkeypatch: Any, tmp_path: Path) -> None:
    """Есть `~/.cache/gigaam`, переменной нет — берём её: она выбрана ДО создания папок."""
    home = _fake_home(monkeypatch, tmp_path, with_cache=True)
    _no_write(monkeypatch)                     # даже без прав: папка уже есть
    assert gigaam_cache.gigaam_dir() == str(home / ".cache" / "gigaam")


def test_home_without_cache_is_used_and_created(monkeypatch: Any, tmp_path: Path) -> None:
    """Домашняя папка на запись, кэша ещё нет — папка та же, и она создаётся."""
    home = _fake_home(monkeypatch, tmp_path, with_cache=False)
    got = gigaam_cache.gigaam_dir()
    assert got == str(home / ".cache" / "gigaam")
    assert os.path.isdir(got), "папку весов не завели — пакет упадёт на os.makedirs"


def test_readonly_home_falls_back_to_model_cache(monkeypatch: Any, tmp_path: Path) -> None:
    """Домашняя папка только на чтение — запасная `_model_cache/gigaam`, и она создаётся."""
    home = _fake_home(monkeypatch, tmp_path, with_cache=False)
    _patch_root(monkeypatch, tmp_path)
    expected = str(tmp_path / "_model_cache" / "gigaam")

    real_access = os.access

    def no_home_write(path: Any, mode: int, **kw: Any) -> bool:
        if os.path.abspath(str(path)) == os.path.abspath(str(home)):
            return False
        return real_access(path, mode, **kw)

    monkeypatch.setattr(os, "access", no_home_write)
    got = gigaam_cache.gigaam_dir()
    assert got == expected
    assert os.path.isdir(expected), "запасную папку внутри приложения не завели"


def test_missing_home_falls_back_to_model_cache(monkeypatch: Any, tmp_path: Path) -> None:
    """Домашней папки нет, и создать её нельзя (как `/home/reelsi` в контейнере).

    Запасная папка при этом обязана быть ВНУТРИ папки приложения: домашняя
    папка тут как раз и есть то, чего на машине нет.

    Проверка записи спрашивает ближайшего существующего предка: у несуществующей
    домашней папки это `/home`, и «нельзя писать предку» — ровно тот контейнер,
    где `/home` принадлежит root, а uid пользователя не может создать там ничего.
    """
    parent = tmp_path / "root_home"          # роль `/home`: чужой и только на чтение
    parent.mkdir()
    home = parent / "no_such_home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("HOMEDRIVE", raising=False)
    monkeypatch.delenv("HOMEPATH", raising=False)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("REELSI_GIGAAM_CACHE", raising=False)
    _patch_root(monkeypatch, tmp_path)
    blocked = os.path.abspath(str(parent))
    expected = str(tmp_path / "_model_cache" / "gigaam")
    assert not os.path.exists(str(home)), "домашняя папка не должна существовать"

    def parent_is_read_only(path: Any, mode: int, **kw: Any) -> bool:
        # Предку домашней папки и всему под ним писать нельзя; папка приложения
        # живёт в другом месте и создаётся.
        del mode, kw                        # подпись `os.access` целиком
        p = os.path.abspath(str(path))
        return p != blocked and not p.startswith(blocked + os.sep)

    monkeypatch.setattr(os, "access", parent_is_read_only)
    assert gigaam_cache.gigaam_dir() == expected
    assert os.path.isdir(expected), "запасную папку внутри приложения не завели"


def test_env_dir_unwritable_raises_reelsi_error(monkeypatch: Any, tmp_path: Path) -> None:
    """Переменную задали, а папка не принимает запись — ошибка про эту папку.

    Молча уехать в домашнюю папку нельзя: человек выбрал место сам, и подмена
    его выбора — это веса не там, где он их ждёт.
    """
    _fake_home(monkeypatch, tmp_path, with_cache=False)
    own = tmp_path / "own_gigaam"
    monkeypatch.setenv("REELSI_GIGAAM_CACHE", str(own))
    _no_write(monkeypatch)

    with pytest.raises(ReelsiError) as exc:
        gigaam_cache.gigaam_dir()
    assert exc.value.code == "gigaam_env_no_dir", "у ошибки нет кода для перевода"
    assert exc.value.vars == {"path": str(own)}
    assert "REELSI_GIGAAM_CACHE" in str(exc.value)


def test_both_dirs_unwritable_raises_reelsi_error(monkeypatch: Any, tmp_path: Path) -> None:
    """Ни домашняя, ни запасная — `ReelsiError` с причиной (код + переменные)."""
    home = _fake_home(monkeypatch, tmp_path, with_cache=False)
    _patch_root(monkeypatch, tmp_path)
    _no_write(monkeypatch)

    with pytest.raises(ReelsiError) as exc:
        gigaam_cache.gigaam_dir()
    # Текст ошибки — шаблон с {home}/{app}: подстановка живёт в словаре (ERR_<код>),
    # поэтому пути и причину проверяем по коду и переменным, а не по строке.
    assert "нет папки для весов распознавания" in str(exc.value)
    assert exc.value.code == "gigaam_no_dir", "у ошибки нет кода для перевода"
    assert exc.value.vars == {
        "home": str(home / ".cache" / "gigaam"),
        "app": str(tmp_path / "_model_cache" / "gigaam"),
    }


def test_app_cache_dir_is_inside_app_root(monkeypatch: Any, tmp_path: Path) -> None:
    """Запасная папка — внутри папки приложения, а не в домашней (та и отвалилась)."""
    home = _fake_home(monkeypatch, tmp_path, with_cache=False)
    _patch_root(monkeypatch, tmp_path)
    got = gigaam_cache.app_cache_dir()
    assert got == str(tmp_path / "_model_cache" / "gigaam")
    assert not got.startswith(os.path.abspath(str(home))), "запасная папка снова в домашней"


def test_app_cache_dir_honours_env(monkeypatch: Any, tmp_path: Path) -> None:
    """Своя переменная `REELSI_GIGAAM_CACHE` — как у прочих личных папок."""
    own = tmp_path / "own_gigaam"
    monkeypatch.setenv("REELSI_GIGAAM_CACHE", str(own))
    assert gigaam_cache.app_cache_dir() == str(own)


# --------------------------------------------------------------------------- #
# Сторож: веса не уезжают в домашнюю папку мимо `download_root`
# --------------------------------------------------------------------------- #
def _load_model_calls() -> list[tuple[str, int, bool]]:
    """Вызовы `gigaam.load_model(...)` в `core/` и `api/`: (файл, строка, есть ли download_root)."""
    out: list[tuple[str, int, bool]] = []
    for rel_dir in CORE_DIRS:
        for path in sorted((Path(ROOT) / rel_dir).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not (isinstance(func, ast.Attribute) and func.attr == "load_model"):
                    continue
                # Ищем только вызовы пакета gigaam: `load_model` есть и у своих
                # модулей (например, Qwen2.5-Omni в этом же файле), и у них
                # `download_root` не при чём.
                base = func.value
                name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")
                if name != "gigaam":
                    continue
                has_root = any(kw.arg == "download_root" for kw in node.keywords)
                out.append((os.path.relpath(str(path), ROOT).replace("\\", "/"),
                            node.lineno, has_root))
    return out


def test_every_gigaam_load_model_passes_download_root() -> None:
    """Каждый вызов `gigaam.load_model(` передаёт `download_root`."""
    calls = _load_model_calls()
    assert calls, "в core/ и api/ не нашлось ни одного вызова gigaam.load_model"
    without = [f"{f}:{line}" for f, line, has in calls if not has]
    assert not without, (
        "вызовы gigaam.load_model без download_root — веса уедут в ~/.cache/gigaam "
        "и на машине без домашней папки на запись нарезка упадёт: " + ", ".join(without))


def test_stub_catches_call_without_download_root(monkeypatch: Any, tmp_path: Path) -> None:
    """Сторож не выродился: вызов без `download_root` он видит."""
    sample = tmp_path / "core_sample.py"
    sample.write_text("import gigaam\nm = gigaam.load_model('v3_ctc')\n", encoding="utf-8")
    tree = ast.parse(sample.read_text(encoding="utf-8"))
    bad = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
           and isinstance(n.func, ast.Attribute) and n.func.attr == "load_model"
           and not any(kw.arg == "download_root" for kw in n.keywords)]
    assert bad, "разбор вызовов не видит вызов без download_root"


def test_omni_asr_gigaam_path_passes_download_root(monkeypatch: Any, tmp_path: Path) -> None:
    """Живой путь `core.omni_asr --engine gigaam`: заглушке достаётся download_root.

    Пакета gigaam в CI может не быть, веса — гигабайты: подменяем и пакет, и
    инференс, и проверяем ровно то, что проверяем, — аргумент загрузки.
    """
    soundfile = pytest.importorskip("soundfile", reason="звук читается soundfile")
    np = pytest.importorskip("numpy", reason="звук собирается массивом")
    from core import omni_asr

    wav = tmp_path / "clip.wav"
    soundfile.write(str(wav), np.zeros(16000, dtype="int16"), 16000, subtype="PCM_16")
    intervals = tmp_path / "iv.json"
    intervals.write_text("[[0.0, 1.0]]", encoding="utf-8")
    out = tmp_path / "out.json"
    # Домашняя папка — своя, пустая: на машине владельца в боевой уже лежат веса
    # GigaAM, и без подмены тест зависел бы от того, что стоит на этой машине.
    _fake_home(monkeypatch, tmp_path, with_cache=False)
    model_dir = tmp_path / "weights"
    monkeypatch.setenv("REELSI_GIGAAM_CACHE", str(model_dir))

    seen: dict[str, Any] = {}

    class _GigaAMStub:
        def load_model(self, name: str, **kw: Any) -> str:
            seen["name"] = name
            seen.update(kw)
            return "model"

    fake = _GigaAMStub()
    monkeypatch.setitem(sys.modules, "gigaam", fake)
    monkeypatch.setattr(omni_asr, "transcribe_clip_gigaam", lambda model, clip: "привет")

    omni_asr.main([str(wav), "--engine", "gigaam", "--intervals", str(intervals),
                   "--out", str(out)])

    assert seen.get("name") == "v3_ctc"
    assert seen.get("download_root") == gigaam_cache.gigaam_dir(), (
        "загрузка модели ушла без download_root — веса поедут в домашнюю папку")
