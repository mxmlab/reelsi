# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Профили спикеров — «кто в кадре» как настройка нарезки.

Каждый спикер снимает у себя: своя комната, свой микрофон, свой говор. Пороги
в `gigaam_cut.tune` откалиброваны на спикере A — стерильная студия, речь на 39-40 дБ
над шумом комнаты. На чужом материале те же цифры врут.

(Спикеры в комментариях зовутся буквами: это реальные люди, и их акустика —
данные о них. Соответствие — локально в `docs/speakers-calibration.md`.)

Замер по реальным клипам (8 спикера C, 7 спикера A, `_envelope` из gigaam_cut.tune):

    Спикер A   речь над полом 39.5 дБ (36.7-40.8, разброс 4 дБ)
    Спикер C   речь над полом 28.8 дБ (21.0-34.6, разброс 14 дБ)

Отсюда всё и едет. `ONSET_DB = 20` («голос = громче 20 дБ над полом») у спикера A
съедает половину динамики, у спикера C — три четверти, а на одном клипе (запас
21 дБ) порог проходит ВЫШЕ медианы его речи. Видно по огибающей: непрерывные куски
«выше порога голоса» у спикера A медианой 0.39с, у спикера C — 0.12с, и 90% короче
0.35с. Речь для кода рассыпается на осколки, дальше `refine_keep` режет «дыры»
между ними, а `drop_micro_keeps` выкидывает то, что осталось.

Ровно это юзер и возвращал руками: из 18 восстановленных кусков у спикера C шесть
длиной 0.32-0.50с вообще не значатся в `.cuts.json` — их убрал не LLM, а пороги
`MIN_KEEP=0.6` / `MIN_ISLAND=0.35`. Восстановлений на клип: спикер A 0.74,
спикер B 1.0, спикер C 2.25.

Профиль = JSON в `reelsi/speakers/<имя>.json`:

    label   человекочитаемое имя
    style   имя пресета AE (`styles/*.json`) — подставляется при выборе спикера
    outdir  папка результата: юзер забывает её менять, и клипы валятся в чужую
    jsxdir  папка для .jsx (сборка AE) — по той же причине: у каждого спикера
            свой проект After Effects, и скрипт не должен уезжать в чужой
    ref     референсные кадры (для будущего автоопределения по лицу)
    cut     оверрайды порогов нарезки (см. CUT_DEFAULTS), чего нет — дефолт
    hint    добавка к промпту LLM («выкидывай разговоры с оператором»)
    breath_p_cut / breath_p_mark
            пороги детектора вздохов лично для него (общие — `breath.P_CUT` /
            `P_MARK`). Личным должен быть именно порог: замер leave-one-clip-out
            на 44 клипах показал, что общий 0.95 у спикера A берёт 18 вздохов из
            151, а его же порог 0.88 — 31 при той же точности 75%
    image_prompts
            стилевые приписки к промпту генерации картинок (слоты a и b):
            {"a": {"extra": "...", "pos": "..."}, "b": {"extra": "...", "pos": "..."}}
    video_prompts
            личные приписки к промпту генерации видео-вставок (слоты a и b),
            с той же структурой {extra, pos}. Отсутствие = чистый запрос карточки.
    breath_model
            свой `breath_model.*.json`. Пусто = общий. Обучается
            `train_breath.py --speaker <имя>`, НО на текущих объёмах личные
            модели общей проигрывают (21.8% против 25% при равной точности):
            данных на одного мало. Поле на будущее

Профиль без `cut` = текущее поведение один в один: дефолты здесь и константы в
`gigaam_cut.tune` — одни и те же числа, за этим следит `tests/test_speakers.py`.
"""
from __future__ import annotations
import os, json, re, copy
from typing import Any

from core import paths
from core.fileio import atomic_text_write
from core.umsg import ReelsiError

SPEAKER_DIR = paths.root("speakers")

# Пороги, которые профиль может переопределить. Значения ДОЛЖНЫ совпадать с
# константами gigaam_cut.tune — иначе «профиль без правок» молча поменяет нарезку.
# Сверяет тест; менять здесь и там одновременно.
CUT_DEFAULTS = {
    # --- пороги громкости ---
    "db_auto":   False,   # считать snap_db/onset_db от РЕАЛЬНОГО запаса речи в клипе
    "snap_frac":  0.30,   # доля запаса (речь−пол) под «тихо» при db_auto
    "onset_frac": 0.50,   # доля запаса под «это уже голос» при db_auto
    "snap_db":   12.0,    # дБ над полом: ниже — тихо (жёсткий порог, db_auto=false)
    "onset_db":  20.0,    # дБ над полом: ниже — ещё не голос (вдох, шум, стук)
    "onset_fall": 30.0,   # дБ ниже пика слова: глухой согласный тише вокала
    # --- как читаем огибающую ---
    "quiet_run":  0.08,   # сек: столько тихо подряд = затишье, а не провал в волне
    "edge_in_max":  0.15, # сек: максимум добора назад (атака согласного)
    "edge_out_max": 0.35, # сек: максимум добора вперёд (хвост слова)
    "word_pad":   0.08,   # сек: запас вокруг слова — CTC подрезает хвосты
    # --- что считаем мусором ---
    "hole_min":   0.15,   # сек: дыра без речи длиннее — вырезаем
    "min_keep":   0.60,   # сек: однословный островок короче — выкидываем
    "min_island": 0.35,   # сек: кусок короче — мусор при любом числе слов
    # --- ритм речи ---
    "silence_sec": 0.80,  # сек: пауза дольше — «полное молчание», режется всегда
    # --- чистка дублей кодом ---
    # Галка «чистка дублей» на шаге 1 перекрывает профиль на этот прогон —
    # как остальные поля шага 1. Здесь живёт дефолт для CLI/без галочки.
    # Умолчание False: в подсказке cutstages зафиксировано,
    # что умной модели чистка дублей только вредит (режет перечисления и роли).
    "dedupe":     False,
}

# Человекочитаемые подписи для UI (порядок = порядок полей в модалке)
CUT_LABELS = [
    ("db_auto",     "Пороги от запаса речи"),
    ("snap_frac",   "Доля запаса: тишина"),
    ("onset_frac",  "Доля запаса: голос"),
    ("snap_db",     "Тишина, дБ над полом"),
    ("onset_db",    "Голос, дБ над полом"),
    ("onset_fall",  "Спад от пика слова, дБ"),
    ("quiet_run",   "Затишье, с"),
    ("edge_in_max", "Добор назад, с"),
    ("edge_out_max", "Добор вперёд, с"),
    ("word_pad",    "Запас вокруг слова, с"),
    ("hole_min",    "Дыра без речи, с"),
    ("min_keep",    "Островок в одно слово, с"),
    ("min_island",  "Минимальный кусок, с"),
    ("silence_sec", "Полное молчание, с"),
]


def _key(name: str | None) -> str:
    """Имя файла из имени спикера: пробелы и слэши в файловой системе ни к чему."""
    k = re.sub(r"[^\w\-]+", "_", (name or "").strip(), flags=re.UNICODE).strip("_")
    return k or "speaker"


def all_speakers() -> dict[str, dict[str, Any]]:
    """{ключ: профиль} из speakers/*.json. Битый JSON пропускаем молча —
    из-за одного файла не должен пропадать весь список в UI."""
    out: dict[str, dict[str, Any]] = {}
    if not os.path.isdir(SPEAKER_DIR):
        return out
    for f in sorted(os.listdir(SPEAKER_DIR)):
        if not f.lower().endswith(".json"):
            continue
        try:
            with open(os.path.join(SPEAKER_DIR, f), encoding="utf-8") as fh:
                d = json.load(fh)
        except ReelsiError: raise
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        k = os.path.splitext(f)[0]
        d.setdefault("label", k)
        out[k] = d
    return out


def load(key: str | None) -> dict[str, Any] | None:
    """Профиль по ключу или по label (в UI выбирают человекочитаемое имя)."""
    if not key:
        return None
    sp = all_speakers()
    if key in sp:
        return sp[key]
    for d in sp.values():
        if d.get("label") == key:
            return d
    return None


def save(name: str | None, data: Any) -> tuple[str, str]:
    """Записать профиль. Возвращает (ключ, путь)."""
    if not isinstance(data, dict):
        raise ValueError("профиль должен быть объектом")
    d = copy.deepcopy(data)
    d["label"] = (d.get("label") or name or "").strip() or name
    cut = d.get("cut") or {}
    unknown = [k for k in cut if k not in CUT_DEFAULTS]
    if unknown:
        raise ValueError("неизвестные пороги: " + ", ".join(sorted(unknown)))
    os.makedirs(SPEAKER_DIR, exist_ok=True)
    key = _key(name or d["label"])
    path = os.path.join(SPEAKER_DIR, key + ".json")
    # newline="\r\n" — как в прежней прямой записи: профиль читается и на Windows,
    # и в git-диффе; перевод строки тут часть формата, а не оформление
    atomic_text_write(path, json.dumps(d, ensure_ascii=False, indent=1, sort_keys=True),
                      newline="\r\n")
    return key, path


def delete(key: str | None) -> bool:
    """Удалить профиль. True — файл был и удалён."""
    path = os.path.join(SPEAKER_DIR, _key(key) + ".json")
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False


def resolve_cut(prof: Any) -> dict[str, Any]:
    """Пороги нарезки для профиля: дефолты + оверрайды. prof = dict, ключ, или
    None (= дефолты). Чужие ключи игнорируются, типы приводятся к дефолтным —
    в JSON легко положить строку, а на ней потом падает сравнение с float."""
    out = dict(CUT_DEFAULTS)
    if isinstance(prof, str):
        prof = load(prof)
    if not isinstance(prof, dict):
        return out
    for k, v in (prof.get("cut") or {}).items():
        if k not in CUT_DEFAULTS or v is None:
            continue
        try:
            out[k] = bool(v) if isinstance(CUT_DEFAULTS[k], bool) else float(v)
        except (TypeError, ValueError):
            continue
    return out
