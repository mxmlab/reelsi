# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Единый список ступеней нарезки, нормализация контракта stages и построение opts.

Единственный источник правды для бэкенда и интерфейса.
Каждая ступень описывает:
  key      - уникальный ключ ступени;
  label    - русское название для UI;
  hint     - текст для «!»-тултипа;
  default  - значение по умолчанию;
  needs    - список зависимостей (ключи ступеней, которые эта ступень включает принудительно);
  branches - ветки ('gigaam', 'vad'), в которых ступень доступна.
"""
from typing import Any

STAGES: list[dict[str, Any]] = [
    {
        "key": "pauses",
        "label": "Паузы",
        "hint": "Вырезать паузы: по словам (speech), по энергии звука (loud) или отключить (off)",
        "default": "speech",
        "needs": [],
        "branches": ["gigaam", "vad"],
        "panel": True,
    },
    {
        "key": "asr",
        "label": "Распознавание речи",
        "hint": "Распознавание слов через ASR. Включается автоматически, если этого требуют другие ступени",
        "default": True,
        "needs": [],
        "branches": ["gigaam", "vad"],
        "panel": True,
    },
    {
        "key": "sense",
        "label": "Смысл (ИИ)",
        "hint": "ИИ-разметка смысловых кусков и удаление неудачных дублей через LLM",
        "default": True,
        "needs": ["asr"],
        "branches": ["gigaam"],
        "panel": True,
    },
    {
        "key": "dedupe",
        "label": "Правка нарезки кодом",
        "hint": "Код поверх решения ИИ: убирает повторы и оговорки, навязывает правило «оставить последний заход», возвращает вырезанное; нужен слабым моделям, с умной только портит (режет перечисления и ролевую речь)",
        "default": False,
        "needs": ["asr"],
        "branches": ["gigaam", "vad"],
        "panel": True,
    },
    {
        "key": "refine",
        "label": "Подгон резов",
        "hint": "Точная подгонка точек реза по огибающей звука и границам слов",
        "default": True,
        "needs": [],
        "branches": ["gigaam"],
        "panel": True,
    },
    {
        "key": "breath",
        "label": "Вздохи",
        "hint": "Детекция и вырезание вздохов перед фразами",
        "default": True,
        "needs": ["asr"],
        "branches": ["gigaam"],
        "panel": True,
    },
    {
        "key": "draft",
        "label": "Черновик mp4",
        "hint": "Рендер быстрого чернового видео .draft.mp4 для предпросмотра нарезки (включается автоматически вместе с Omni-ревью, отдельной галки в интерфейсе нет)",
        "default": False,
        "needs": [],
        "branches": ["gigaam", "vad"],
        "panel": False,
    },
]

DEFAULTS: dict[str, Any] = {
    s["key"]: s["default"] for s in STAGES if s["key"] != "asr"
}


def normalize(raw: dict[str, Any] | None) -> tuple[dict[str, Any], str]:
    """Нормализует словарь ступеней нарезки и возвращает (stages, branch).

    - pauses == 'loud' -> ветка 'vad', иначе 'gigaam';
    - asr вычисляется: True, если pauses == 'speech' или включена хоть одна
      ступень с 'asr' в needs;
    - ступени, недоступные в выбранной ветке, гасятся (не присутствуют в словаре);
    - нормализация идемпотентна.
    """
    if raw is None or not isinstance(raw, dict):
        raw = {}

    pauses_val = raw.get("pauses", DEFAULTS["pauses"])
    if pauses_val is False:
        pauses_val = "off"
    elif pauses_val is True:
        pauses_val = "speech"
    elif pauses_val not in ("off", "speech", "loud"):
        pauses_val = "speech"

    branch = "vad" if pauses_val == "loud" else "gigaam"

    # Собираем разрешённые ступени для текущей ветки
    result: dict[str, Any] = {}
    for stage in STAGES:
        k = stage["key"]
        if branch not in stage["branches"]:
            continue
        if k == "pauses":
            result[k] = pauses_val
        elif k == "asr":
            continue  # вычисляется ниже
        else:
            default_val = stage.get("default", False)
            result[k] = bool(raw.get(k, default_val))

    # Вычисление asr: включено, если pauses == 'speech' или любая активная ступень ветки требует asr
    needs_asr = any(
        result.get(s["key"])
        for s in STAGES
        if "asr" in s.get("needs", []) and s["key"] in result
    )
    asr = (pauses_val == "speech") or needs_asr

    # Вставляем asr в порядке STAGES
    ordered_result: dict[str, Any] = {}
    for stage in STAGES:
        k = stage["key"]
        if k not in result and k != "asr":
            continue
        if k == "asr":
            if branch in stage["branches"]:
                ordered_result[k] = asr
        else:
            ordered_result[k] = result[k]

    return ordered_result, branch


# Дефолтные пороги классической нарезки (ветка vad / reelsi.py).
# Значения обязаны совпадать с тем, что ранее было захардкожено в static/app/40-queue.js:371.
# Держатся здесь как единственный источник правды для бэкенда.
DEFAULT_THRESHOLDS: dict[str, Any] = {
    "model": "large-v3",
    "scale": 50.4,
    "vad_thresh": 18,
    "min_silence": 0.30,
    "pad": 0.08,
    "cam_return": 2,
}


def to_reelsi_opts(
    stages: dict[str, Any] | None = None,
    thresholds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Строит словарь opts для классического движка reelsi.py (ветка vad) на основе stages.

    ПОЧЕМУ субтитры выключены жёстко (subs=False, srt=False):
    Их делает отдельный шаг «Разметить всё» (/api/gen_subs) по готовому XML для любой ветки.
    Пока reelsi.py делал их сам, кастомная нарезка тянула Whisper даже для одних пауз
    по громкости, и быстрого прохода без GPU не получалось.

    ПОЧЕМУ Whisper поднимается только при dedupe=True:
    В reelsi.py условие загрузки модели — not (args.no_subs and args.no_dedup).
    При subs=False и dedupe=False оба флага no_* равны True, и Whisper не импортируется вовсе.
    """
    norm_stages, _ = normalize(stages)
    thresh = dict(DEFAULT_THRESHOLDS)
    if thresholds and isinstance(thresholds, dict):
        for k in DEFAULT_THRESHOLDS:
            if k in thresholds and thresholds[k] is not None:
                thresh[k] = thresholds[k]
    elif stages and isinstance(stages, dict):
        for k in DEFAULT_THRESHOLDS:
            if k in stages and stages[k] is not None:
                thresh[k] = stages[k]

    return {
        "subs": False,
        "srt": False,
        "no_cut": norm_stages.get("pauses") == "off",
        # Умолчание берём из описания ступени (False), а не литералом True:
        # как зафиксировано в подсказке ступени, с умной моделью dedupe только портит.
        "dedup": bool(norm_stages.get("dedupe", DEFAULTS["dedupe"])),
        "ae": False,
        "keep": "last",
        "aggressive": False,
        "ai_yellow": False,
        "model": thresh["model"],
        "scale": thresh["scale"],
        "vad_thresh": thresh["vad_thresh"],
        "min_silence": thresh["min_silence"],
        "pad": thresh["pad"],
        "cam_return": thresh["cam_return"],
    }
