# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Единственный писатель и читатель `<stem>.project.json` — сайдкара редактора нарезки.

ПОЧЕМУ модуль есть (внешнее ревью 2026-09-22). Файл писали ПЯТЬ
независимых мест (`core/gigaam_cut/pipeline.py`, `core/omni_cut.py` — дважды,
`api/build.py` — дважды) и ещё два писали его «по случаю» (`api/editor.py`:
пересборка блоков и реконструкция проекта из XML). Поля каждый раз собирались
заново, своим словарём: `speaker` знал один писатель, `selfcheck` и
`user_overrides` — другой, и добавленное поле молча терялось на следующей
перезаписи. Ни версии формата, ни одного места для будущей миграции при этом не
было: понять, чем лежащий на диске файл отличается от того, что пишет текущий код,
можно было только чтением всех семи мест.

Теперь запись идёт через `write_project`, чтение — через `read_project`, и
`version` появляется у файла сам. Отсутствие `version` — старый файл (версия 0):
он читается ровно как раньше, а МЕСТО ДЛЯ МИГРАЦИИ ровно одно — `read_project`.
"""
import os
from typing import Any, TypedDict, cast

from core.fileio import atomic_json_dump, json_load_soft

# Версия формата сайдкара. 1 — текущая (пишется всегда).
PROJECT_VERSION = 1
# Версия файла БЕЗ поля version: всё, что нарезано раньше.
PROJECT_VERSION_LEGACY = 0


class ProjectFile(TypedDict, total=False):
    """Поля `<stem>.project.json` — общие и специфичные для отдельных писателей.

    `total=False`, потому что набор полей зависит от того, кто записал файл:
    нарезка кладёт `keep`/`cams`/`offsets`/`fps`/`cam_return`/`scale`, GigaAM-cut
    добавляет `speaker`, self-check — `selfcheck`, редактор правок —
    `user_overrides`, окно раскладки камер — `assign`. Читатели обязаны
    обращаться к полям через `.get(...)` с запасным значением, как и раньше.

    Что откуда (собрано по всем писателям):
      - `cams`, `offsets`, `fps`, `cam_return`, `scale`, `keep` — пишут ВСЕ
        писатели нарезки (gigaam_cut.pipeline, omni_cut ×2, build ×2) и
        реконструкция из XML (api.editor._project_from_xml);
      - `speaker` — нарезка GigaAM (pipeline) и omni_cut в режиме gigaam;
      - `selfcheck` — omni_cut (отчёт самопроверки стыков);
      - `user_overrides` — api.editor (память ручных правок) и omni_cut;
      - `assign` — api.build (ручная раскладка камер);
      - `version` — ставит сам `write_project`.
    """

    version: int
    cams: list[str]
    offsets: list[float]
    fps: float
    cam_return: int
    scale: float
    keep: list[list[float]]
    speaker: str
    selfcheck: dict[str, Any]
    user_overrides: dict[str, Any]
    assign: list[int]


def write_project(path: str | os.PathLike[str], data: ProjectFile) -> None:
    """Записать сайдкар атомарно, проставив `version` (core.fileio.atomic_json_dump).

    Отступ тот же, что был у всех прежних писателей (indent=1) — файл остаётся
    читаемым глазами, а диффы нарезок не превращаются в одну строку. Копия, а не
    сам словарь: `version` не протекает в состояние вызывающего (его ещё
    дочитывают и правят после записи).
    """
    out = dict(data)
    out["version"] = PROJECT_VERSION
    atomic_json_dump(path, out, indent=1)


def read_project(path: str | os.PathLike[str]) -> ProjectFile | None:
    """Прочитать сайдкар; None — файла нет или он не читается (как json_load_soft).

    ЕДИНСТВЕННОЕ место, где будет жить миграция формата: файл без `version` —
    это версия 0 (нарезки раньше), и поднимать его до текущей версии
    должен этот код, а не читатели. Пока версия 0 читается как есть — поля у неё
    те же самые, и поведение читателей не меняется.
    """
    data = json_load_soft(path)
    if not isinstance(data, dict):
        # Файла нет, JSON битый (крах в момент старой неатомарной записи) или это
        # вовсе не наш формат (список, строка) — наружу то же «не прочли», что и
        # раньше отдавал json_load_soft с default=None.
        return None
    data.setdefault("version", PROJECT_VERSION_LEGACY)
    return cast(ProjectFile, data)
