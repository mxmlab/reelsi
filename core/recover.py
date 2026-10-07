# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Восстановление вставок клипа с диска.

Состояние клипов (включая вставки) живёт в браузере (localStorage) и в зеркале
`ui_state.json`; кнопка «Из папки результата» подхватывает только сами XML, а
вставки при этом терялись — вернуть их после потери списка было нечем. Здесь
собираем вставки обратно из того, что осталось на диске:

- `<stem>.inserts.json` — ИИ-разметка: фраза, тип, тайминг, запрос (`query`) и
  подпись (`prompt`); выбранный файл-медиа сюда НЕ попадает;
- `<stem>.jsx` — собранный проект AE: `var INSERTS=[…]` уже с выбранными файлами
  и геометрией (x/y, крупность, маска), но там нет `phrase`/`query`.

Слияние по времени старта даёт объект для карточки шага 2: разметку берём из
сайдкара, файл и геометрию — из `.jsx`. Тип (`type`) — общий для обоих.

Модуль чистый: только чтение файлов и разбор, без flask и GPU.
"""
from __future__ import annotations

import json
import os
from typing import Any


def _load_json(path: str) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _load_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


def _extract_array(s: str, marker: str) -> str | None:
    """Подстрока сбалансированного массива сразу после `marker` (имя переменной).

    Регэкспом `\\[.*?\\]` не обойтись: внутри могут быть вложенные массивы и
    скобки в строках. Идём по символам и следим за вложенностью и строкой.
    """
    i = s.find(marker)
    if i < 0:
        return None
    i = s.find("[", i)
    if i < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for j in range(i, len(s)):
        ch = s[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return s[i:j + 1]
    return None


def read_sidecar_inserts(xml_path: str) -> list[dict[str, Any]]:
    """ИИ-разметка вставок из `<stem>.inserts.json` (пусто, если сайдкара нет)."""
    d = _load_json(os.path.splitext(xml_path)[0] + ".inserts.json")
    ins = d.get("inserts") if isinstance(d, dict) else None
    return [x for x in ins if isinstance(x, dict)] if isinstance(ins, list) else []


def read_jsx_inserts(xml_path: str) -> list[dict[str, Any]]:
    """Собранные вставки из `<stem>.jsx` (`var INSERTS=[…]`) — с файлами и геометрией."""
    s = _load_text(os.path.splitext(xml_path)[0] + ".jsx")
    raw = _extract_array(s, "INSERTS") if s else None
    if not raw:
        return []
    try:
        arr = json.loads(raw)
    except Exception:
        return []
    return [x for x in arr if isinstance(x, dict)] if isinstance(arr, list) else []


def read_project_speaker(xml_path: str) -> str:
    """Спикер прогона из `<stem>.project.json` (нужен, чтобы клип собрался тем же стилем)."""
    d = _load_json(os.path.splitext(xml_path)[0] + ".project.json")
    return str(d.get("speaker") or "") if isinstance(d, dict) else ""


def _num(x: Any, default: float) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _s(x: Any, key: str, default: Any = "") -> Any:
    v = x.get(key)
    return default if v is None else v


def _merge_one(side: dict[str, Any] | None, jsx: dict[str, Any]) -> dict[str, Any]:
    base = side or {}
    start = _num(jsx.get("start"), 0.0)
    end = _num(jsx.get("end"), start + 2.5)
    dur = round(end - start, 3)
    if dur <= 0:
        dur = _num(base.get("duration_sec"), 2.5) or 2.5
    return {
        "type": jsx.get("t") or base.get("type") or "photo",
        "start_sec": round(start, 3),
        "duration_sec": dur,
        "media": str(jsx.get("media") or ""),
        "x": _num(jsx.get("x"), 0.0),
        "y": _num(jsx.get("y"), 0.0),
        "sc": _num(jsx.get("sc"), 100.0) or 100.0,
        "sin": _num(jsx.get("sin"), 0.0),
        "mw": _num(jsx.get("mw"), _num(base.get("mw"), 100.0)),
        "mh": _num(jsx.get("mh"), _num(base.get("mh"), 100.0)),
        "mosaic": bool(jsx.get("mosaic") or base.get("mosaic")),
        "phrase": str(_s(base, "phrase")),
        "query": str(_s(base, "query")),
        "prompt": str(_s(base, "prompt")),
    }


def _bare_one(side: dict[str, Any]) -> dict[str, Any]:
    """Разметка без `.jsx`: файла нет, но карточку и тайминг вернуть можно."""
    return {
        "type": side.get("type") or "photo",
        "start_sec": _num(side.get("start_sec"), 0.0),
        "duration_sec": _num(side.get("duration_sec"), 2.5) or 2.5,
        "media": "",
        "x": 0.0,
        "y": 0.0,
        "sc": 100.0,
        "sin": 0.0,
        "mw": _num(side.get("mw"), 100.0),
        "mh": _num(side.get("mh"), 100.0),
        "mosaic": bool(side.get("mosaic")),
        "phrase": str(_s(side, "phrase")),
        "query": str(_s(side, "query")),
        "prompt": str(_s(side, "prompt")),
    }


# Насколько по времени может разойтись разметка сайдкара и вставка из .jsx, чтобы
# считаться одним и тем же местом. Сборка округляет тайминг, поэтому не точно ноль.
MATCH_TOL = 0.35


def merge_inserts(side: list[dict[str, Any]], jsx: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Слить разметку (.json) и сборку (.jsx) в объекты карточек шага 2."""
    out: list[dict[str, Any]] = []
    used: set[int] = set()
    for j in jsx:
        start = _num(j.get("start"), 0.0)
        best, best_d = -1, MATCH_TOL
        for i, sd in enumerate(side):
            if i in used:
                continue
            d = abs(_num(sd.get("start_sec"), -99.0) - start)
            if d < best_d:
                best, best_d = i, d
        if best >= 0:
            used.add(best)
        out.append(_merge_one(side[best] if best >= 0 else None, j))
    for i, sd in enumerate(side):
        if i not in used:
            out.append(_bare_one(sd))
    out.sort(key=lambda t: t["start_sec"])
    return out


def recover_inserts(xml_path: str) -> list[dict[str, Any]]:
    """Вставки клипа, собранные с диска: разметка из сайдкара + файлы из `.jsx`."""
    return merge_inserts(read_sidecar_inserts(xml_path), read_jsx_inserts(xml_path))
