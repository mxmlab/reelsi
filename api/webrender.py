# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Рендер без After Effects: тело сборки для страницы /render.

Кадры рисует наш же предпросмотр шага 3 (страница `/render` грузит те же
`static/app/*.js`), а параметры сборки — стиль, музыка, вставки, интро — лежат в
состоянии браузера, куда серверу доступа нет. Поэтому тело сборки (РОВНО то, что
страница шага 3 шлёт на `/api/scene`) приезжает сюда запросом и кладётся в
`_tmp` своего клипа: id уезжает в адрес страницы, страница забирает тело обратно
и считает план из тех же полей.

Второй сборки этого тела на стороне Python здесь нет и не должно быть: любая
копия `ipvPlanBody` разошлась бы с фронтом молча, а разошедшийся рендер хуже
отсутствующего. Модуль занимается только доставкой и уборкой.
"""
from __future__ import annotations

import os
import re
import uuid
from typing import Any

from flask import Response, jsonify, request

from core.umsg import ReelsiError, umsg
from ._core import bp, jstr, umsg_err

# Имя файла тела: `<id>.json` в _tmp клипа. id — шестнадцатеричный, поэтому имя
# нельзя подсунуть чужое (в запросе id приходит от клиента).
BODY_PREFIX = "render_body_"
BODY_ID_RE = re.compile(r"^[0-9a-f]{8,32}$")


def body_path(xml: str, body_id: str) -> str:
    """Путь файла тела в `_tmp` клипа. Только для проверенного id (см. BODY_ID_RE).

    `tmp_dir` — та же папка временных артефактов клипа, что у прокси и черновика
    (core.draftrender): живёт рядом с проектом и убирается его же уборкой.
    """
    from core.draftrender import tmp_dir
    return os.path.join(tmp_dir(xml), BODY_PREFIX + body_id + ".json")


def speaker_of(xml: str) -> str:
    """Спикер клипа из сайдкара `<стем>.project.json` — или пустая строка.

    Тем же путём спикера берут формат кадра, LUT и обработка голоса
    (core.frame.output_frame_size): состояние браузера тут ни при чём, и
    странице рендера оно недоступно.
    """
    from core import frame as _frame
    spk = _frame._sidecar_speaker(xml)
    return spk.strip() if isinstance(spk, str) else ""


def _save_body(xml: str, body: dict[str, Any]) -> str:
    """Записать тело сборки в _tmp клипа, вернуть id. Пишем через fileio (атомарно)."""
    from core.fileio import atomic_json_dump
    body_id = uuid.uuid4().hex[:16]
    atomic_json_dump(body_path(xml, body_id), body, indent=1)
    return body_id


def _load_body(xml: str, body_id: str) -> dict[str, Any] | None:
    """Прочитать тело по id; None — файла нет или он не разобрался."""
    from core.fileio import json_load_soft
    data = json_load_soft(body_path(xml, body_id))
    return data if isinstance(data, dict) else None


@bp.route("/api/render_body", methods=["POST"])
def api_render_body_save() -> Response:
    """Сохранить тело сборки для страницы рендера. body: то же, что у /api/scene.

    Отвечает `{ok, id, speaker}`: id уезжает в адрес `/render`, speaker нужен
    странице, чтобы взять LUT камер, рамку и формат из профиля своего клипа.
    """
    d = request.get_json(silent=True) or {}
    xml = jstr(d, "xml").strip().strip('"')
    try:
        if not isinstance(d, dict):
            raise ReelsiError(umsg("render_body_bad", "Тело запроса должно быть объектом"))
        if not xml or not os.path.isfile(xml):
            raise ReelsiError(umsg("file_not_found", f"Файл не найден: {xml}", path=xml))
        body = dict(d)
        body["xml"] = xml
        body_id = _save_body(xml, body)
        return jsonify(ok=True, id=body_id, speaker=speaker_of(xml))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/render_body", methods=["GET"])
def api_render_body_get() -> Response:
    """Отдать тело сборки странице рендера. Параметры: `xml`, `id`."""
    xml = (request.args.get("xml") or "").strip().strip('"')
    body_id = (request.args.get("id") or "").strip()
    try:
        if not xml or not os.path.isfile(xml):
            raise ReelsiError(umsg("file_not_found", f"Файл не найден: {xml}", path=xml))
        if not BODY_ID_RE.match(body_id):
            raise ReelsiError(umsg("render_body_bad_id", "Тело сборки не найдено: неверный id",
                                   id=body_id))
        body = _load_body(xml, body_id)
        if body is None:
            raise ReelsiError(umsg("render_body_missing",
                                   "Тело сборки не найдено — сохрани его заново",
                                   id=body_id))
        return jsonify(ok=True, body=body, speaker=speaker_of(xml))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))
