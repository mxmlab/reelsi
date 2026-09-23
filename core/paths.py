# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Единственный источник путей: корень репозитория, данные репозитория, примеры.

ПОЧЕМУ этот модуль есть. Движок переехал в пакет `core/`, и
`os.path.dirname(os.path.abspath(__file__))` из любого его модуля стал указывать
на `core/`, а не на корень репозитория. А в корне лежат ЛИЧНЫЕ файлы
пользователя: `ai_config.json` с ключами провайдеров, `insertlib.json` — база
вставок на полторы тысячи файлов, `terms.json`, `styles/`, `speakers/`. Молча
переехавший путь — это «интерфейс открылся без ключей и со пустой базой» без
единой ошибки в логе. Поэтому папку модуля в ядре не считает никто, кроме
этого файла: всё остальное берёт `ROOT`, `DATA` и `EXAMPLES` отсюда.

Переопределения через `env(...)` (`REELSI_AI_CONFIG`, `REELSI_JOB_LOCK` и
прочие) остаются в самих модулях: это личные настройки профиля, а не раскладка.
"""
import os
import posixpath
from core.umsg import ReelsiError

# Корень репозитория — на уровень выше самого пакета core/.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Данные, которые лежат в репозитории (шаблоны, словари, эталоны, движки ASR).
DATA = os.path.join(ROOT, "data")
# Примеры личных файлов (*.example.json) — из них bootstrap заводит боевые.
EXAMPLES = os.path.join(ROOT, "examples")


def root(*parts: str) -> str:
    """Путь к личному файлу или папке пользователя — они живут в корне репозитория."""
    return os.path.join(ROOT, *parts)


def data(*parts: str) -> str:
    """Путь к данным, которые лежат в репозитории (data/)."""
    return os.path.join(DATA, *parts)


def require_source_tree() -> None:
    """Проверяет, что Reelsi запущен из клона репозитория (editable install).

    Обычный `pip install .` в site-packages не поддерживается: templates/, static/
    и личные файлы пользователя живут в корне клона.
    """
    missing = [d for d in ("templates", "static", "data") if not os.path.isdir(os.path.join(ROOT, d))]
    if missing:
        raise ReelsiError(
            f"Error: Reelsi must be run from a git clone repository (editable install).\n"
            f"Please run 'pip install -e .' from the repository root.\n"
            f"Missing required source directories in {ROOT}: {', '.join(missing)}."
        )


# Кэш результатов пробника регистронезависимости ФС: каталог-предок -> bool.
_PKEY_CACHE: dict[str, bool] = {}


def _pkey_cache_clear() -> None:
    """Сбрасывает кэш регистрозависимости файловых систем (для тестов)."""
    _PKEY_CACHE.clear()


def _is_fs_case_insensitive(path: str) -> bool:
    """Пробник файловой системы пути на нечувствительность к регистру.

    Ищет ближайший существующий предок пути (или сам путь), в имени которого
    есть буква. Если путь с swapcase() последнего компонента существует и
    указывает на тот же файл (os.path.samefile) — ФС не различает регистр.
    Результат кэшируется по каталогу-предку. При отсутствии предков с буквами
    или любых OSError считается регистрозависимой (False).
    """
    if not path:
        return False

    if os.name != "nt":
        path = path.replace("\\", "/")

    _path_mod = posixpath if os.name != "nt" else os.path

    # Быстрый поиск в кэше: проверяем сам путь и его каталоги-предки
    check = path
    while check:
        if check in _PKEY_CACHE:
            return _PKEY_CACHE[check]
        parent = _path_mod.dirname(check)
        if not parent or parent == check:
            break
        check = parent

    cur = path
    found = None
    visited: set[str] = set()

    while cur:
        visited.add(cur)
        try:
            if os.path.exists(cur):
                name = _path_mod.basename(cur)
                if any(c.isalpha() for c in name):
                    found = cur
                    break
        except OSError:
            return False

        parent = _path_mod.dirname(cur)
        if not parent or parent == cur:
            # Для относительного пути без найденного предка пробуем abspath
            if not _path_mod.isabs(cur):
                try:
                    abs_cur = os.path.abspath(cur)
                    if abs_cur not in visited and os.path.exists(abs_cur):
                        abs_name = _path_mod.basename(abs_cur)
                        if any(c.isalpha() for c in abs_name):
                            found = abs_cur
                            break
                        cur = _path_mod.dirname(abs_cur)
                        continue
                except OSError:
                    return False
            break
        cur = parent

    if found is None:
        _PKEY_CACHE[_path_mod.dirname(path) or path] = False
        return False

    parent_dir, name = _path_mod.split(found)
    swapped = _path_mod.join(parent_dir, name.swapcase())
    try:
        is_ci = bool(os.path.exists(swapped) and os.path.samefile(found, swapped))
    except OSError:
        is_ci = False

    # Кэшируем результат на каталог-предок (и на родительский каталог исходного пути)
    cache_key = parent_dir or found
    _PKEY_CACHE[cache_key] = is_ci
    pdir = _path_mod.dirname(path)
    if pdir:
        _PKEY_CACHE[pdir] = is_ci

    return is_ci


def pkey(path: str | os.PathLike) -> str:
    """Возвращает ключ пути для словарей и индексов с учётом файловой системы.

    ПОЧЕМУ эта функция есть. Стандартный `os.path.normcase` смотрит
    на платформу (ОС), а не на файловую систему. На Windows он приводит пути к
    нижнему регистру, а на Linux и macOS оставляет как есть. Но на macOS файловая
    система по умолчанию (APFS) нечувствительна к регистру (case-insensitive).
    В итоге `Foo.png` и `foo.png` на macOS — это один файл на диске, но два
    разных ключа в индексе вставок, истории видео и кэше масок ротоскопинга, что
    приводит к задвоению записей и ложным пометкам gone.

    Инвариант:
    - На Windows и на Linux с регистрозависимой ФС pkey(p) == os.path.normcase(p).
    - На ФС без учёта регистра вне Windows pkey(p) == os.path.normcase(p).lower().
    """
    if not path:
        return os.path.normcase(path or "")
    p = os.fspath(path)
    if os.name == "nt":
        return os.path.normcase(p)
    norm = posixpath.normcase(p)
    if _is_fs_case_insensitive(p):
        return norm.lower()
    return norm
