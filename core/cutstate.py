# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Состояние клипа на диске между ступенями ИИ-нарезки.

Позволяет ступеням пайплайна работать в отдельных процессах без удержания
всего стека моделей в видеопамяти. Каждая ступень читает состояние клипа
с диска, дополняет его своими результатами и атомарно записывает обратно.
"""
from __future__ import annotations

import json
import os
from typing import Any

from core import fileio


VERSION = 1


def new(
    stem: str,
    out: str,
    cams: list[str],
    offsets: list[float],
    wav: str,
    *,
    scale: float,
    cam_return: int,
    speaker: str | None = None,
    model: str | None = None,
    engine: str | None = None,
    stages: dict[str, Any] | None = None,
    branch: str = "gigaam",
) -> dict[str, Any]:
    """Создать новый словарь состояния клипа со всеми полями схемы.

    Постоянные поля инициализируются переданными параметрами, результаты
    ступеней — None, список завершённых ступеней пуст, ошибка отсутствует.
    """
    return {
        "version": VERSION,
        "stem": stem,
        "out": out,
        "cams": cams,
        "offsets": offsets,
        "wav": wav,
        "scale": scale,
        "cam_return": cam_return,
        "speaker": speaker,
        "model": model,
        "engine": engine,
        "stages": stages,
        "branch": branch,
        # Результаты ступеней
        "full_text": None,
        "words": None,
        "silence_bounds": None,
        "kept": None,
        "drop": None,
        "rule": None,
        "cutlog": None,
        "keep": None,
        "assign": None,
        "breath_marks": None,
        # Служебные
        "done": [],
        "error": None,
    }


def _to_disk(state: dict[str, Any]) -> dict[str, Any]:
    """Подготовить словарь состояния к сериализации в JSON.

    Множества kept и drop превращаются в отсортированные списки, числовые ключи
    rule — в строки, а кортежи интервалов keep и silence_bounds — в списки.
    Исходный словарь вызывающего остаётся нетронутым.
    """
    disk = dict(state)
    if disk.get("kept") is not None:
        disk["kept"] = sorted(disk["kept"])
    if disk.get("drop") is not None:
        disk["drop"] = sorted(disk["drop"])
    if disk.get("rule") is not None:
        disk["rule"] = {str(k): v for k, v in disk["rule"].items()}
    if disk.get("keep") is not None:
        disk["keep"] = [list(item) for item in disk["keep"]]
    if disk.get("silence_bounds") is not None:
        disk["silence_bounds"] = [list(item) for item in disk["silence_bounds"]]
    return disk


def _from_disk(disk: dict[str, Any]) -> dict[str, Any]:
    """Восстановить типы Python из JSON-представления на диске.

    Списки индексов kept и drop превращаются в set, строковые ключи rule — в int,
    а интервалы keep и silence_bounds — в списки двухэлементных кортежей float.
    """
    state = dict(disk)
    if state.get("kept") is not None:
        state["kept"] = set(state["kept"])
    if state.get("drop") is not None:
        state["drop"] = set(state["drop"])
    if state.get("rule") is not None:
        state["rule"] = {int(k): v for k, v in state["rule"].items()}
    if state.get("keep") is not None:
        state["keep"] = [(float(item[0]), float(item[1])) for item in state["keep"]]
    if state.get("silence_bounds") is not None:
        state["silence_bounds"] = [(float(item[0]), float(item[1])) for item in state["silence_bounds"]]
    return state


def save(path: str | os.PathLike, state: dict[str, Any]) -> None:
    """Атомарно сохранить состояние клипа на диск через fileio.atomic_json_dump."""
    fileio.atomic_json_dump(str(path), _to_disk(state), indent=1)


def load(path: str | os.PathLike) -> dict[str, Any]:
    """Прочитать состояние клипа с диска и восстановить типы контракта.

    При отсутствии файла поднимает FileNotFoundError.
    """
    with open(path, "r", encoding="utf-8") as f:
        disk = json.load(f)
    return _from_disk(disk)


def update(path: str | os.PathLike, **fields: Any) -> dict[str, Any]:
    """Прочитать состояние, обновить указанные поля и атомарно сохранить.

    Версия схемы защищена от перезаписи извне. Неизвестные поля сохраняются.
    """
    state = load(path)
    fields.pop("version", None)
    if "kept" in fields and fields["kept"] is not None:
        fields["kept"] = set(fields["kept"])
    if "drop" in fields and fields["drop"] is not None:
        fields["drop"] = set(fields["drop"])
    if "rule" in fields and fields["rule"] is not None:
        fields["rule"] = {int(k): v for k, v in fields["rule"].items()}
    if "keep" in fields and fields["keep"] is not None:
        fields["keep"] = [(float(item[0]), float(item[1])) for item in fields["keep"]]
    if "silence_bounds" in fields and fields["silence_bounds"] is not None:
        fields["silence_bounds"] = [(float(item[0]), float(item[1])) for item in fields["silence_bounds"]]
    state.update(fields)
    save(path, state)
    return state


def mark_done(path: str | os.PathLike, stage: str) -> dict[str, Any]:
    """Дописать ключ ступени в done без дублирования с сохранением порядка."""
    state = load(path)
    done = state.setdefault("done", [])
    if stage not in done:
        done.append(stage)
    save(path, state)
    return state


def mark_error(path: str | os.PathLike, stage: str, exc: BaseException) -> dict[str, Any]:
    """Записать информацию об ошибке ступени и сохранить состояние."""
    state = load(path)
    state["error"] = {
        "stage": stage,
        "type": type(exc).__name__,
        "msg": str(exc),
    }
    save(path, state)
    return state


def path_for(work_dir: str | os.PathLike, idx: int, stem: str) -> str:
    """Сформировать путь к файлу состояния клипа: <work_dir>/<idx:02d>_<stem>.state.json."""
    return os.path.join(str(work_dir), f"{idx:02d}_{stem}.state.json")
