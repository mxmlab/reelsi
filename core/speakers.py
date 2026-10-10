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
    inserts
            квота вставок на шаг 2: {"photo": <int>, "video": <int>}, диапазон 0..30,
            сумма >= 1. Пусто = 10 фото и 3 видео.
    named_inserts
            вставки по названиям: слово из личного словаря `named_inserts.json` (корень
            репозитория) получает фото-вставку с картинкой из базы вставок (детерминированный
            проход после ответа модели, `core/aicut/commands.py`). Здесь только ВЫКЛючатель:
            поля нет — включено, `false` — не ставить. Названия и формы — в словаре, а не в профиле.
            Старое имя поля `drug_inserts` при сохранении переезжает сюда.
    lut
            LUT (.cube) на видео камеры: {"<номер камеры с 1>": "путь"}. В превью
            накладывается на лету, в сборке прожигается в видео — AE плохо
            работает с цветом. Пусто — цвет как есть.
    breath_model
            свой `breath_model.*.json`. Пусто = общий. Обучается
            `train_breath.py --speaker <имя>`, НО на текущих объёмах личные
            модели общей проигрывают (21.8% против 25% при равной точности):
            данных на одного мало. Поле на будущее
    format
            формат кадра ролика: один из ключей `frame.FORMATS` ("9:16", "1:1",
            "4:5", "16:9"). Отсюда его берут секвенция Premiere, масштаб клипов
            камер, превью, черновик, .drp и поиск стоков (core/frame.py —
            единственный источник). Пусто = 9:16, как снималось до сих пор
    frame
            рамка кадра КАЖДОЙ камеры: {"<номер камеры с 1>": {"x", "y", "zoom"}}.
            Какая часть исходника попадает в кадр: x/y — точка исходника (доли
            ПОКАЗЫВАЕМОГО кадра, после поворота), встающая в центр кадра ролика,
            zoom — проценты от «кадр заполнен ровно», 100..400. Задаётся мышкой в
            превью шага 3, применяется везде, где собирается картинка (.jsx,
            превью, XML, черновик). Камеры в поле нет или значения 0.5/0.5/100 —
            обрезка по центру, как было (core/frame.py — единственный источник
            геометрии рамки)
    voice_fx
            обработка голоса камеры 1 — ИИ-шумодав и цепочка VST3
            ({"denoise": {"on", "engine", "atten_db", "mix"},
            "vst": [{"path", "name", "state", "on"}], "cut", "final"}).
            Движок (`engine`) — `deepfilter` (предел подавления в дБ, `atten_db`),
            `roformer` или `roformer_aggr` (доля обработанного в смеси, `mix`, %);
            поля нет — прежний `deepfilter`, как у профилей, записанных до
            появления RoFormer. Форма и дефолты — `core/voicefx.py:normalize_fx`,
            редактор — блок «Голос» в превью нарезки. Запекается в WAV до
            After Effects; пусто = голос как есть

Профиль без `cut` = текущее поведение один в один: дефолты здесь и константы в
`gigaam_cut.tune` — одни и те же числа, за этим следит `tests/test_speakers.py`.
"""
from __future__ import annotations
import os, json, re, copy
from typing import Any

from core import frame
from core import paths
from core.applog import get_logger
from core.fileio import atomic_text_write
from core.umsg import ReelsiError

log = get_logger(__name__)

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
    из-за одного файла не должен пропадать весь список в UI; в журнал пишем тип ошибки
    (имя файла — это имя человека, в лог его не кладём)."""
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
        except Exception as e:
            # Один битый профиль не прячет весь список: пропускаем его, но в журнале должно быть видно почему.
            log.warning("профиль спикера не прочитан (%s), пропущен", type(e).__name__)
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


def _check_voice_fx(raw: Any) -> None:
    """Проверить поле `voice_fx` перед записью профиля (иначе — ValueError).

    Строгая проверка тут, а не при использовании: профиль — единственное место,
    где настройки обработки голоса вообще лежат, и молча записанный мусор (строка
    вместо дБ, чужой ключ) всплыл бы потом в нарезке, уже без объяснения откуда.
    Битые значения, пришедшие ИЗ файла (правленного руками), при использовании
    смягчает core.voicefx.normalize_fx — профиль из-за них не падает.
    """
    if not isinstance(raw, dict):
        raise ValueError("поле voice_fx должно быть объектом")
    unknown = [k for k in raw if k not in ("denoise", "vst", "cut", "final")]
    if unknown:
        raise ValueError("неизвестные ключи voice_fx: " + ", ".join(sorted(str(k) for k in unknown)))
    dn = raw.get("denoise")
    if dn is not None:
        if not isinstance(dn, dict):
            raise ValueError("поле voice_fx.denoise должно быть объектом")
        bad_dn = [k for k in dn if k not in ("on", "engine", "atten_db", "mix")]
        if bad_dn:
            raise ValueError("неизвестные ключи voice_fx.denoise: "
                             + ", ".join(sorted(str(k) for k in bad_dn)))
        if "on" in dn and not isinstance(dn["on"], bool):
            raise ValueError("voice_fx.denoise.on должен быть true/false")
        if "engine" in dn:
            # Импорт внутри функции: core.voicefx сам импортирует speakers на
            # уровне модуля, и встречный импорт сверху был бы кольцом. Список
            # движков живёт там же, где нормализация поля, — второй копии нет.
            from core import voicefx
            if dn["engine"] not in voicefx.DENOISE_ENGINES:
                raise ValueError("некорректное значение voice_fx.denoise.engine: "
                                 "ожидается одно из "
                                 + ", ".join(voicefx.DENOISE_ENGINES))
        if "atten_db" in dn:
            att = dn["atten_db"]
            if not isinstance(att, int) or isinstance(att, bool) or att < 0 or att > 100:
                raise ValueError("некорректное значение voice_fx.denoise.atten_db: "
                                 "ожидается число от 0 до 100")
        if "mix" in dn:
            mix = dn["mix"]
            if not isinstance(mix, int) or isinstance(mix, bool) or mix < 0 or mix > 100:
                raise ValueError("некорректное значение voice_fx.denoise.mix: "
                                 "ожидается число от 0 до 100")
    vst = raw.get("vst")
    if vst is not None:
        if not isinstance(vst, list):
            raise ValueError("поле voice_fx.vst должно быть списком")
        for i, item in enumerate(vst):
            if not isinstance(item, dict):
                raise ValueError(f"элемент voice_fx.vst[{i}] должен быть объектом")
            bad = [k for k in item if k not in ("path", "name", "state", "on")]
            if bad:
                raise ValueError(f"неизвестные ключи voice_fx.vst[{i}]: "
                                 + ", ".join(sorted(str(k) for k in bad)))
            if not isinstance(item.get("path"), str) or not item["path"].strip():
                raise ValueError(f"voice_fx.vst[{i}].path: нужен путь к плагину")
            for key in ("name", "state"):
                if key in item and not isinstance(item[key], str):
                    raise ValueError(f"voice_fx.vst[{i}].{key} должен быть строкой")
            if "on" in item and not isinstance(item["on"], bool):
                raise ValueError(f"voice_fx.vst[{i}].on должен быть true/false")
    for key in ("cut", "final"):
        if key in raw and not isinstance(raw[key], bool):
            raise ValueError(f"voice_fx.{key} должен быть true/false")


def _check_lut(raw: Any) -> dict[str, str]:
    """Проверить поле `lut` перед записью профиля (иначе — ValueError).

    Строгая проверка тут, а не при использовании, — по той же причине, что у
    voice_fx: профиль единственное место, где путь к таблице вообще лежит, и молча
    записанный мусор (номер камеры строкой, путь к чему угодно) всплыл бы потом в
    превью, уже без объяснения откуда.

    Пустой путь — ключ не пишется: пустое поле в редакторе значит «LUT не задан»,
    и профиль без правок не должен обзаводиться пустой таблицей.
    """
    if not isinstance(raw, dict):
        raise ValueError("поле lut должно быть объектом")
    out: dict[str, str] = {}
    for k, v in raw.items():
        cam = str(k).strip()
        if not cam.isdigit() or int(cam) < 1:
            raise ValueError(f"некорректный номер камеры в lut: {k!r} (нужно число от 1)")
        if not isinstance(v, str):
            raise ValueError(f"lut.{cam}: нужен путь к файлу .cube")
        path = v.strip()
        if not path:
            continue
        if not path.lower().endswith(".cube"):
            raise ValueError(f"lut.{cam}: путь должен оканчиваться на .cube")
        out[cam] = path
    return out


def _check_format(raw: Any) -> str:
    """Проверить поле `format` перед записью профиля (иначе — ValueError).

    Строгая проверка тут, а не при использовании, — по той же причине, что у
    voice_fx и lut: формат ролика выбирают в профиле, и молча записанное чужое
    значение («916», «вертикаль») всплыло бы потом в нарезке, уже без объяснения
    откуда. Незнакомое значение при ЧТЕНИИ файла, правленного руками, смягчает
    core.frame.speaker_format — профиль из-за него не падает, ролик собирается 9:16.

    Пустое значение — ключ не пишется: пустое поле в редакторе значит «формат по
    умолчанию», и профиль без правок не должен обзаводиться записью про формат.
    """
    if not isinstance(raw, str):
        raise ValueError("поле format должно быть строкой")
    fmt = raw.strip()
    if not fmt:
        return ""
    if fmt not in frame.FORMATS:
        raise ValueError(f"неизвестный формат кадра: {raw!r} (нужно одно из "
                         + ", ".join(sorted(frame.FORMATS)))
    return fmt


def _check_frame(raw: Any) -> dict[str, dict[str, float]]:
    """Проверить поле `frame` перед записью профиля (иначе — ValueError).

    Обёртка над `core.frame.check_frames`: рамка — геометрия кадра, и правило у
    неё одно на всех, кто её применяет (.jsx, XML, черновик, превью). Вторая
    копия проверки здесь разошлась бы с той, по которой считают.

    Рамка на дефолте (0.5/0.5/100) ключа не заводит: камера без правок не должна
    появляться в профиле, как не появляется пустой LUT и пустой формат.
    """
    return frame.check_frames(raw)


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
    if "inserts" in d:
        ins = d["inserts"]
        if not isinstance(ins, dict):
            raise ValueError("поле inserts должно быть объектом")
        unknown_ins = [k for k in ins if k not in ("photo", "video")]
        if unknown_ins:
            raise ValueError("неизвестные ключи inserts: " + ", ".join(sorted(str(k) for k in unknown_ins)))
        for k, v in ins.items():
            if not isinstance(v, int) or isinstance(v, bool) or v < 0 or v > 30:
                raise ValueError(f"некорректное значение inserts.{k}: ожидается число от 0 до 30")
        if sum(ins.values()) < 1:
            raise ValueError("сумма вставок (photo + video) должна быть не меньше 1")
    # Старое имя выключателя (до «вставок по названиям») переезжает на новое при сохранении:
    # иначе профиль с drug_inserts:false жил бы вечно рядом с галкой, которую уже не снять.
    if "drug_inserts" in d:
        legacy = d.pop("drug_inserts")
        if "named_inserts" not in d:
            d["named_inserts"] = legacy
    if "named_inserts" in d and not isinstance(d["named_inserts"], bool):
        raise ValueError("поле named_inserts должно быть true/false")
    if "voice_fx" in d:
        _check_voice_fx(d["voice_fx"])
    if "lut" in d:
        luts = _check_lut(d["lut"])
        if luts:
            d["lut"] = luts
        else:
            del d["lut"]          # одни пустые пути — поля нет, как у inserts и voice_fx
    if "format" in d:
        fmt = _check_format(d["format"])
        if fmt:
            d["format"] = fmt
        else:
            del d["format"]       # пусто = формат по умолчанию (9:16), поля нет
    if "frame" in d:
        fr = _check_frame(d["frame"])
        if fr:
            d["frame"] = fr
        else:
            del d["frame"]        # все камеры на дефолте — поля нет, как у lut
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
