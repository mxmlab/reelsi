# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Где лежат веса GigaAM: одна папка на всё приложение.

ПОЧЕМУ этот модуль есть. Пакет `gigaam` качает веса в `~/.cache/gigaam`
(его `_CACHE_DIR = os.path.expanduser("~/.cache/gigaam")`), если `load_model` не
дали `download_root`. Reelsi его не давал, поэтому 421 МБ весов уезжали мимо всех
остальных кэшей приложения — человек их не видел и перенести не мог, а на машине
с нерабочей или доступной только на чтение домашней папкой (контейнер,
сервисная учётка, CI) нарезка падала `PermissionError: '/home/reelsi'` без
понятной причины. Проверено живьём: в контейнере `HOME` был `/home/reelsi`,
которого не существует, и все 34 ролика упали одним и тем же `PermissionError`.

Правило выбора папки — здесь и только здесь (`gigaam_dir()`), все вызовы
`gigaam.load_model(...)` обязаны передавать `download_root=gigaam_dir()`:

1. `REELSI_GIGAAM_CACHE` задана — она, и только она. Это явный выбор человека, и
   он главнее и готового кэша в домашней папке, и самой домашней папки: иначе
   веса уедут не туда, куда их положили.
2. `~/.cache/gigaam` уже есть — она. Существующие установки (в том числе с
   уже скачанными весами) не качают гигабайт заново, а веса остаются там, где
   их ищет и сам пакет: одна папка на оба способа запуска.
3. Домашняя папка пишется — `~/.cache/gigaam`, она и создаётся.
4. Домашняя папка недоступна на запись или её нет (контейнер, сервисная учётка,
   CI) — запасная папка ВНУТРИ папки приложения, `_model_cache/gigaam` рядом с
   его кодом (`paths.root("_model_cache", "gigaam")`): домашняя папка тут уже ни
   при чём, а место видно и удаляется руками. В git она не едет (`.gitignore`).
5. Ни туда, ни туда записать нельзя — `ReelsiError` с причиной по-русски
   (код `gigaam_no_dir` для английского интерфейса) вместо чужого
   `PermissionError` из недр пакета.

Сторож `tests/test_gigaam_dir.py` обходит `core/` и `api/` по AST и падает,
если хоть один вызов `gigaam.load_model(` остался без `download_root`.
"""
from __future__ import annotations

import os

from core import paths
from core.app_meta import env
from core.umsg import ReelsiError, umsg

# Имя папки внутри домашней: ровно то же, что у самого пакета gigaam
# (`os.path.join(".cache", "gigaam")`) — иначе веса уедут от уже установленных.
HOME_CACHE_DIRNAME = os.path.join(".cache", "gigaam")
# Запасная папка — внутри папки приложения, а не в домашней: недоступность
# домашней и есть причина запаса. Имя с подчёркиванием — в `.gitignore`.
MODEL_CACHE_DIRNAME = "_model_cache"


def home_cache_dir() -> str:
    """Папка, куда веса кладёт сам пакет gigaam: `~/.cache/gigaam`."""
    return os.path.join(os.path.expanduser("~"), HOME_CACHE_DIRNAME)


def app_cache_dir() -> str:
    """Запасная папка весов: явная переменная или `_model_cache/gigaam` в приложении."""
    return env("GIGAAM_CACHE") or paths.root(MODEL_CACHE_DIRNAME, "gigaam")


def _writable_dir(path: str) -> bool:
    """Можно ли писать в папку (саму или в её ближайшего существующего предка).

    Проверяется именно предок: у несуществующего `HOME` (`/home/reelsi` в
    контейнере, где пользователя завести не удалось) тестировать нечего, а
    создать его нельзя — родитель `home` чужой и только на чтение. Поэтому
    спрашиваем разрешение у того каталога, в котором пришлось бы создавать.
    """
    probe = os.path.abspath(path)
    while probe and not os.path.isdir(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break                      # дошли до корня — существующих предков больше нет
        probe = parent
    return os.path.isdir(probe) and os.access(probe, os.W_OK)


def _ensure_dir(path: str) -> bool:
    """Завести папку весов. False — создать не удалось (права, путь занят файлом).

    Проверка записи и само создание — две разные вещи: `os.access` разрешает
    запись по правам, а `makedirs` может упасть и на гонке (папку успели
    завести файлом). Второе падение — уже не «нет прав», а отказ, и причину
    называет вызывающий.
    """
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        return False
    return True


def _choose_dir() -> str:
    """Выбор папки весов: явная переменная, готовый кэш, домашняя, запасная в приложении."""
    own = env("GIGAAM_CACHE")
    if own:
        # Явный выбор человека не перебивает ничто: ни готовый кэш в домашней
        # папке, ни сама домашняя папка — иначе веса уедут не туда, куда их
        # положили, и человек этого даже не увидит.
        if _writable_dir(own) and _ensure_dir(own):
            return own
        msg = ("папка весов распознавания {path}, указанная в REELSI_GIGAAM_CACHE, "
               "недоступна на запись")
        raise ReelsiError(umsg("gigaam_env_no_dir", msg, path=own))
    home_cache = home_cache_dir()
    if os.path.isdir(home_cache):
        # Веса уже тут: установка старая, и качать гигабайт заново нельзя.
        return home_cache
    if _writable_dir(os.path.expanduser("~")) and _ensure_dir(home_cache):
        return home_cache
    app_cache = app_cache_dir()
    if _writable_dir(app_cache) and _ensure_dir(app_cache):
        return app_cache
    # Ошибка с кодом (umsg): на английском интерфейсе текст берётся из словаря
    # (ERR_gigaam_no_dir), а пути подставляются из err_vars ответа.
    msg = ("нет папки для весов распознавания: домашняя папка {home} недоступна на запись, "
           "запасная папка приложения {app} тоже — укажи свою в REELSI_GIGAAM_CACHE")
    raise ReelsiError(umsg("gigaam_no_dir", msg, home=home_cache, app=app_cache))


def gigaam_dir() -> str:
    """Папка весов GigaAM для `gigaam.load_model(..., download_root=...)`.

    Единственное место выбора: `REELSI_GIGAAM_CACHE` (явный выбор человека),
    затем существующая `~/.cache/gigaam`, затем `~/.cache/gigaam` при
    записываемой домашней папке, затем запасная `_model_cache/gigaam` внутри
    папки приложения.
    """
    return _choose_dir()
