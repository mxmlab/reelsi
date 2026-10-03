# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Какой видеокодек брать: автоопределение по железу и выбор в настройках.

Полная сборка ffmpeg знает БОЛЬШЕ кодировщиков, чем есть железа на машине: `h264_amf`
и `h264_qsv` лежат в ней и там, где нет ни AMD, ни Intel-графики. Честный признак один —
микро-энкод кадра (см. `probe`): он же ловит занятую VRAM и лимит сессий драйвера.
Всё, что дороже одного запуска ffmpeg, помнится на процесс.

Модуль общий, а не «для черновика». Назначений два:

* `draft` — H.264 8 бит, быстро: черновой рендер и прокси камер;
* `master` — HEVC 10 бит почти без потерь: прожиг LUT в видео камер перед AE.

Выбор пользователя живёт в `ai_config.json` полем `video_encoder` (`"auto"` или имя
семейства) и читается отсюда же: настройка МАШИННАЯ, а не спикера — она про железо,
а не про ролик. «Выбрано, но не работает» — не повод падать: предупреждение в лог и
авто (ровно так вёл себя черновик со своей таблицей `_CANDIDATES`).
"""
from __future__ import annotations

import platform
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable

from core.applog import get_logger
from core.umsg import ReelsiError

log = get_logger(__name__)

# Проба — это запуск ffmpeg и старт драйвера: секунды на семейство. 60 с хватает с
# запасом, а вечный висяк драйвера не должен держать HTTP-запрос.
PROBE_TIMEOUT = 60
# 256x256 — НЕ меньше: NVENC отвергает мелкие кадры («Frame Dimension less than the
# minimum supported value»), и проба врала бы «недоступен».
PROBE_FRAME = "color=black:s=256x256:d=0.1"

# Семейства кодеков: железо важнее имени кодека (у nvenc их два — h264 и hevc).
FAMILIES: tuple[str, ...] = ("nvidia", "intel", "amd", "apple", "cpu")
# Назначения: черновик и мастер (прожиг LUT).
PURPOSES: tuple[str, ...] = ("draft", "master")

# Порядок автоопределения по ОС — от быстрого к запасному (бывшая таблица _CANDIDATES
# черновика). Mac идёт своим путём: медиадвижок Apple есть always, NVENC — никогда.
OS_ORDER: dict[str, tuple[str, ...]] = {
    "Windows": ("nvidia", "amd", "intel", "cpu"),
    "Linux": ("nvidia", "amd", "intel", "cpu"),
    "Darwin": ("apple", "cpu"),
}
# Незнакомая ОС (FreeBSD и прочая экзотика): не падаем — идём по самому
# распространённому порядку, как делал черновик.
DEFAULT_ORDER: tuple[str, ...] = OS_ORDER["Windows"]

# Кодек семейства на каждое назначение. Черновик — H.264 8 бит (совместимость и
# скорость), мастер — HEVC 10 бит (p010le/main10): LUT в 8 битах теряет то, ради чего
# его жгут.
DRAFT_CODECS: dict[str, str] = {
    "nvidia": "h264_nvenc",
    "intel": "h264_qsv",
    "amd": "h264_amf",
    "apple": "h264_videotoolbox",
    "cpu": "libx264",
}
MASTER_CODECS: dict[str, str] = {
    "nvidia": "hevc_nvenc",
    "intel": "hevc_qsv",
    "amd": "hevc_amf",
    "apple": "hevc_videotoolbox",
    "cpu": "libx265",
}
CODECS: dict[str, dict[str, str]] = {"draft": DRAFT_CODECS, "master": MASTER_CODECS}

# Подписи для лога; интерфейс показывает свои строки и переводит их сам (app.js),
# но те же слова стоят в списке выбора — так лог и селект говорят об одном и том же.
LABELS: dict[str, str] = {
    "nvidia": "NVIDIA (NVENC)",
    "intel": "Intel (Quick Sync)",
    "amd": "AMD (AMF)",
    "apple": "Apple (VideoToolbox)",
    "cpu": "Процессор (x264/x265)",
}

# Допустимые значения настройки «Видеокодек» (поле video_encoder в ai_config.json).
SETTINGS: tuple[str, ...] = ("auto",) + FAMILIES

# Качество черновика по умолчанию — те же числа, что стояли в черновике
# (`_codec_args(hw, 28, "4M")`): 28-й квантователь nvenc / 4 Мбит/с остальным.
DRAFT_Q = 28
DRAFT_BR = "4M"

# Аргументы качества по кодеку. Для не-NVIDIA взят БИТРЕЙТ, а не квантователь: флаги
# качества у amf/qsv/videotoolbox разъезжаются от версии ffmpeg к версии, а `-b:v`
# понимают все и всегда. Для черновика этого достаточно.
DRAFT_ARGS: dict[str, Callable[[int, str], list[str]]] = {
    "h264_nvenc": lambda q, br: ["-preset", "p4", "-cq", str(q)],
    "h264_videotoolbox": lambda q, br: ["-b:v", br],          # [НЕПРОВЕРЕНО] — Apple
    "h264_amf": lambda q, br: ["-b:v", br],                   # [НЕПРОВЕРЕНО] — AMD
    "h264_qsv": lambda q, br: ["-b:v", br],
    "libx264": lambda q, br: ["-crf", "26", "-preset", "veryfast"],
}

# Мастер: HEVC 10 бит почти без потерь. Аргументы nvenc/qsv/cpu проверены на машине
# владельца микро-энкодом (h264_nvenc, hevc_nvenc 10 бит, h264_qsv, hevc_qsv работают;
# h264_amf — нет). Строки amf и apple — [НЕПРОВЕРЕНО]: ни AMD, ни Apple под рукой не
# было, от нерабочих спасает проба (см. pick) — она идёт ТЕМИ ЖЕ аргументами, что уйдут
# в дело, и нерабочее семейство просто выпадает из выбора.
MASTER_ARGS: dict[str, Callable[[int, str], list[str]]] = {
    "hevc_nvenc": lambda q, br: ["-preset", "p5", "-rc", "vbr", "-cq", "18", "-b:v", "0",
                                 "-pix_fmt", "p010le", "-profile:v", "main10",
                                 "-tag:v", "hvc1"],
    "hevc_qsv": lambda q, br: ["-global_quality", "20", "-pix_fmt", "p010le",
                               "-profile:v", "main10", "-tag:v", "hvc1"],
    # [НЕПРОВЕРЕНО] — AMD
    "hevc_amf": lambda q, br: ["-quality", "quality", "-rc", "cqp", "-qp_i", "18",
                               "-qp_p", "20", "-pix_fmt", "p010le", "-tag:v", "hvc1"],
    # [НЕПРОВЕРЕНО] — Apple
    "hevc_videotoolbox": lambda q, br: ["-q:v", "65", "-pix_fmt", "p010le",
                                        "-tag:v", "hvc1"],
    "libx265": lambda q, br: ["-crf", "18", "-preset", "medium", "-pix_fmt",
                              "yuv420p10le", "-tag:v", "hvc1"],
}
ARGS: dict[str, dict[str, Callable[[int, str], list[str]]]] = {
    "draft": DRAFT_ARGS, "master": MASTER_ARGS}

# --------------------------------------------------------------------------- #
# Цвет: видеодиапазон и матрица BT.709
# --------------------------------------------------------------------------- #
# Зачем это здесь, а не у вызывающего. Кадры рендера без AE приходят СНИМКОМ экрана:
# полный RGB (0…255) без всякой матрицы. Уходили они в кодировщик как есть, и файл
# выходил `color_range=pc, color_space=bt470bg` — то есть «полный диапазон, BT.601».
# Проигрыватель верит тегам: тени и полутени уезжали вверх (замер по владельцу:
# тени +19 %, средние тона +15 %, светлые +3 % — «пропали полутени»), а сам файл
# расходился с эталоном AE (`color_range=tv, bt709`).
#
# Поэтому у рендера кадров ДВА цветовых шага, и оба лежат здесь — одним источником:
#   * перевод пикселей RGB -> YUV в видеодиапазоне BT.709 делает фильтр `COLOR_FILTER`
#     (без него теги были бы только надписью на неверных числах);
#   * теги `COLOR_TAGS` говорят проигрывателю, что за числа внутри.
# Порядок аргументов держит вызывающий: фильтр — до кодеков (`-vf`), теги — после.
#
# Проверено замером на этой машине (ffmpeg 8.0, x264/x265 теги читаются из VUI
# битстрима): у libx264/libx265 четыре тега доходят ВСЕ, и для них добавлены
# `-x264-params`/`-x265-params` с теми же значениями: без них libx264 не пишет
# transfer/primaries вовсе (у ffmpeg 8.0 значения этих опций не доезжают до VUI).
# Строки NVENC/QSV/AMF/VideoToolbox — [НЕПРОВЕРЕНО] на своём железе: AMD и Apple под
# рукой нет, а NVENC чужие значения молча подменяет своими (замер: отдал `pc, gbr`).
# Нерабочие аргументы семейство не роняет — проба `probe` идёт ТЕМИ ЖЕ аргументами,
# что уйдут в дело, и семейство просто выпадает из выбора (см. pick).
COLOR_RANGE = "tv"                       # видеодиапазон: 16…235, как у AE и всего вещания
COLOR_MATRIX = "bt709"                   # матрица RGB -> YUV
COLOR_PRIMARIES = "bt709"                # первичные (цветность) — как у эталона AE
COLOR_TRC = "bt709"                      # передача (гамма) — как у эталона AE
COLOR_TAGS: list[str] = ["-color_range", COLOR_RANGE, "-colorspace", COLOR_MATRIX,
                         "-color_primaries", COLOR_PRIMARIES, "-color_trc", COLOR_TRC]
# Те же значения числами H.273 — их ждёт битстрим-фильтр VUI (см. VUI_BSF).
H273_PRIMARIES: dict[str, int] = {"bt709": 1}
H273_TRC: dict[str, int] = {"bt709": 1}
H273_MATRIX: dict[str, int] = {"bt709": 1, "bt470bg": 5, "smpte170m": 6}
# Фильтр перевода: RGB -> YUV в видеодиапазоне BT.709. `format` в конце — чтобы кадр
# пришёл кодировщику уже готовым: кодек сам решал бы, в каком диапазоне считать, и
# у разных семейств вышел бы разный результат.
COLOR_FILTER = "scale=out_range=%s:out_color_matrix=%s,format=%%s" % (COLOR_RANGE,
                                                                     COLOR_MATRIX)

# Теги в самом кодировщике (VUI битстрима) — там, где он их принимает. Ключ — имя
# кодека, значение — готовые аргументы; нет кодека в таблице — тегов в битстриме нет,
# остаются одни контейнерные (COLOR_TAGS).
VUI_ARGS: dict[str, list[str]] = {
    "libx264": ["-x264-params",
                "colorprim=%s:transfer=%s:colormatrix=%s:range=%s"
                % (COLOR_PRIMARIES, COLOR_TRC, COLOR_MATRIX, COLOR_RANGE)],
    "libx265": ["-x265-params",
                "log-level=none:colorprim=%s:transfer=%s:colormatrix=%s:range=limited"
                % (COLOR_PRIMARIES, COLOR_TRC, COLOR_MATRIX)],
}

# Второй путь к тому же VUI — битстрим-фильтр (`h264_metadata`/`hevc_metadata`): он
# правит VUI уже готового потока и кодеку ничего не диктует. Нужен там, где общие
# `-color_primaries/-color_trc` до битстрима НЕ доезжают: замер на этой машине —
# `hevc_nvenc` (мастер по умолчанию, см. MASTER_CODECS) отдаёт `tv, bt709` и молчит про
# передачу с первичными, отчего у владельца в готовом ролике было
# `color_range=tv, color_space=bt709, color_transfer=unknown, color_primaries=unknown`.
# Замер тем же ffmpeg 8.0: с фильтром те же аргументы дают все четыре тега
# (`tv, bt709, bt709, bt709`), remux `-c copy` их сохраняет — и склейка, и микс со
# звуком (оба идут `-c copy`) несут их дальше.
#
# Числа, а не имена: фильтр принимает значения H.273 (BT.709 = 1, полный диапазон = 1).
# Таблица — по СЕМЕЙСТВУ потока (h264/hevc), а не по кодеку: фильтр работает с готовым
# элементарным потоком, поэтому он одинаков для nvenc/qsv/amf/videotoolbox.
VUI_BSF: dict[str, list[str]] = {
    codec: ["-bsf:v", "%s_metadata=colour_primaries=%d:transfer_characteristics=%d"
                      ":matrix_coefficients=%d:video_full_range_flag=%d"
            % (family, H273_PRIMARIES[COLOR_PRIMARIES], H273_TRC[COLOR_TRC],
               H273_MATRIX[COLOR_MATRIX], 1 if COLOR_RANGE == "pc" else 0)]
    for family, codecs in (("hevc", ("hevc_nvenc", "hevc_qsv", "hevc_amf",
                                     "hevc_videotoolbox")),
                           ("h264", ("h264_nvenc", "h264_qsv", "h264_amf",
                                     "h264_videotoolbox")))
    for codec in codecs
}


def color_filter(pix_fmt: str) -> str:
    """Цепочка фильтров цвета для этого формата кадра: RGB -> YUV (BT.709, видеодиапазон).

    Формат называется здесь, а не отдельным `-pix_fmt`: фильтр обязан отдать кадр
    ровно в том виде, в каком его ждёт кодировщик, иначе swscale переведёт его второй
    раз — уже без матрицы, и цвет уедет молча."""
    return COLOR_FILTER % pix_fmt


def color_args(codec: str, pix_fmt: str = "yuv420p") -> list[str]:
    """Аргументы цвета для кодека: фильтр перевода (`-vf`) и теги.

    Первый аргумент — `-vf`, дальше теги: так их и надо ставить в командной строке
    (`-vf` относится к фильтрам вывода, теги — к параметрам кодека). Теги VUI
    (`VUI_ARGS`) добавляются кодеку, который их принимает, а `VUI_BSF` — битстрим-фильтр
    тем, у кого общие теги до VUI не доезжают (см. таблицу). Два пути к одному VUI
    нарочно: `-x264-params`/`-x265-params` уже проверены на этой машине, а фильтр —
    единственный работающий способ у железных кодеков; вместе они не встречаются ни на
    одном кодеке, поэтому второй раз одни и те же теги не пишутся.
    """
    return ["-vf", color_filter(pix_fmt), *COLOR_TAGS, *VUI_ARGS.get(codec, []),
            *VUI_BSF.get(codec, [])]


def _args_table(purpose: str) -> dict[str, Callable[[int, str], list[str]]]:
    """Таблица аргументов назначения; незнакомое назначение — ошибка вызова."""
    table = ARGS.get(purpose)
    if table is None:
        raise ValueError(f"неизвестное назначение кодека: {purpose!r}")
    return table


def codec_args(codec: str, purpose: str = "draft", q: int = DRAFT_Q,
               br: str = DRAFT_BR) -> list[str]:
    """Аргументы кодека: `["-c:v", имя]` + параметры качества для назначения.

    Неизвестному кодеку — битрейт: `-b:v` понимают все и всегда, и рендер не упадёт
    от отсутствия аргументов (кодек мог появиться в конфиге от другой версии)."""
    build = _args_table(purpose).get(codec)
    return ["-c:v", codec] + (build(q, br) if build else ["-b:v", br])


def family_of(codec: str) -> str | None:
    """Семейство по имени кодека (обратный поиск по таблицам) или None."""
    for table in CODECS.values():
        for family, name in table.items():
            if name == codec:
                return family
    return None


def uses_nvidia(codec: str) -> bool:
    """Занимает ли этот кодек видеокарту NVIDIA — по нему решается замок GPU.

    Карту занимает не «аппаратный кодек» вообще, а конкретно NVIDIA: у Quick Sync
    (Intel), AMF (AMD) и VideoToolbox (Apple) своё железо, а x264/x265 считает
    процессор — ни один из них чужой RoFormer или распознавание не задерживает.
    Замок видеокарты (`core.gpulock`) берут только NVENC-участки: он бережёт VRAM,
    а кончается она именно там.

    Принимается и имя кодека (`hevc_nvenc`), и имя семейства (`nvidia`): настройка
    «Видеокодек» хранит второе, а в командную строку ffmpeg уходит первое.
    """
    return codec == "nvidia" or family_of(codec) == "nvidia"


def codec_name(family: str, purpose: str) -> str:
    """Имя кодека семейства для назначения; незнакомое имя отдаётся как есть —
    так `probe` умеет проверять и кодек, о котором модуль ещё не знает."""
    return CODECS[purpose].get(family, family)


# --------------------------------------------------------------------------- #
# Проба: работает ли кодировщик ПРЯМО СЕЙЧАС
# --------------------------------------------------------------------------- #
# Наличие кодека в `ffmpeg -encoders` не значит ничего (см. шапку). Проба — микро-энкод
# одного кадра ТЕМИ ЖЕ аргументами, что пойдут в дело: у мастера это 10-битный p010le
# (проба в 8 битах врала бы в обе стороны), у черновика — его preset/bitrate.
_PROBE_CACHE: dict[tuple[str, str], bool] = {}
_AVAILABLE: dict[str, tuple[str, ...]] = {}
_LOCK = threading.Lock()


def _micro_encode(codec: str, purpose: str) -> bool:
    """Один запуск ffmpeg: закодировать чёрный кадр 256x256 и выбросить результат."""
    cmd = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", PROBE_FRAME] + \
        codec_args(codec, purpose) + ["-f", "null", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=PROBE_TIMEOUT)
        return r.returncode == 0
    except ReelsiError:
        raise
    except Exception:
        return False                    # ffmpeg не найден, драйвер отказал, таймаут — всё это «нет»


def probe(family: str, purpose: str = "draft", refresh: bool = False) -> bool:
    """Работает ли кодировщик этого семейства для НАЗНАЧЕНИЯ прямо сейчас?

    Кэш на процесс по (кодек, назначение): проба — запуск ffmpeg, гонять её на каждый
    черновик нельзя. `refresh=True` — спросить заново, мимо кэша: так спрашивает
    черновик, когда ffmpeg УЖЕ упал и надо понять, виноват кодировщик или вход.
    """
    codec = codec_name(family, purpose)
    key = (codec, purpose)
    if not refresh:
        with _LOCK:
            hit = _PROBE_CACHE.get(key)
        if hit is not None:
            return hit
    ok = _micro_encode(codec, purpose)
    with _LOCK:
        _PROBE_CACHE[key] = ok
    return ok


def auto_order() -> tuple[str, ...]:
    """Порядок перебора семейств для «авто» на ЭТОЙ машине."""
    return OS_ORDER.get(platform.system(), DEFAULT_ORDER)


def video_encoder_cfg() -> str:
    """Настройка «Видеокодек» из ai_config.json: `"auto"` или имя семейства.

    Незнакомое значение (конфиг от другой версии) читается как «auto»: настройка
    переживает смену железа, а рендер из-за неё падать не должен."""
    from core.aicut.config import load_ai_config     # ленивый: core не должен тянуть aicut при импорте
    val = load_ai_config().get("video_encoder")
    return val if isinstance(val, str) and val in SETTINGS else "auto"


@dataclass(frozen=True)
class Choice:
    """Что выбрали: семейство, готовые аргументы кодека и подпись для лога."""
    family: str
    args: list[str]
    label: str


def _choice(family: str, purpose: str) -> Choice:
    codec = codec_name(family, purpose)
    return Choice(family=family, args=codec_args(codec, purpose),
                  label=LABELS.get(family, family))


def _works(family: str, purpose: str, prober: Callable[[str], bool] | None) -> bool:
    """Проба семейства: своя (`prober` — по имени кодека) или общая с кэшем."""
    if prober is None:
        return probe(family, purpose)
    return prober(codec_name(family, purpose))


def pick(purpose: str, setting: str | None = None,
         prober: Callable[[str], bool] | None = None) -> Choice:
    """Кодек для назначения: семейство, аргументы, подпись.

    `setting` — значение настройки (`"auto"` или семейство); None — взять из ai_config.
    Выбрано семейство и проба прошла — оно. Выбрано, но не работает — предупреждение в
    лог и авто (настройка переживает смену железа, а рендер падать из-за неё не должен).
    `"auto"` — первое по порядку ОС, чья проба прошла. Ничего не работает — `cpu`:
    последнее слово всегда за процессором, иначе видео не собрать вовсе.

    `prober` — своя проба вызывающего: черновик зовёт со своей
    (`draftrender.probe_family`, а та — `draftrender.probe_encoder`) — у него свой кэш
    проб, и ему нужен свежий ответ после падения ffmpeg.
    """
    _args_table(purpose)                            # незнакомое назначение — ошибка вызова
    chosen = video_encoder_cfg() if setting is None else setting
    if chosen in FAMILIES:
        if chosen == "cpu" or _works(chosen, purpose, prober):
            return _choice(chosen, purpose)
        log.warning("видеокодек «%s» из настроек не прошёл пробу — беру авто (%s)",
                    LABELS.get(chosen, chosen), purpose)
    elif chosen != "auto":
        log.warning("неизвестный видеокодек «%s» в настройках — беру авто (%s)",
                    chosen, purpose)
    for family in auto_order():
        # «cpu» в порядке стоит последним и проверяется только как последнее слово:
        # отсутствие аппаратного — это и есть cpu, отдельная проба ничего не добавит.
        if family == "cpu" or family == chosen:
            continue
        if _works(family, purpose, prober):
            return _choice(family, purpose)
    return _choice("cpu", purpose)


def available(purpose: str = "master") -> list[str]:
    """Семейства, чья проба прошла, — в порядке предпочтения ЭТОЙ ОС (для интерфейса).

    Первое в списке и есть то, что выберет «авто» (см. pick). Считается один раз на
    процесс: проба — запуск ffmpeg, а интерфейс спрашивает этот список при каждом
    открытии настроек."""
    _args_table(purpose)
    with _LOCK:
        done = _AVAILABLE.get(purpose)
    if done is not None:
        return list(done)
    order = auto_order()
    order = order + tuple(f for f in FAMILIES if f not in order)
    out = tuple(f for f in order if probe(f, purpose))
    with _LOCK:
        _AVAILABLE[purpose] = out
    return list(out)


def cached_available(purpose: str = "master") -> list[str] | None:
    """Уже посчитанный список семейств или None (проб НЕ запускает).

    Нужен ответу GET /api/ai_config: он зовётся часто и обязан отвечать сразу, поэтому
    отдаёт список только когда цена уже заплачена (его посчитал /api/encoders), а иначе
    поля просто нет — фронт сходит за ним отдельным запросом."""
    with _LOCK:
        done = _AVAILABLE.get(purpose)
    return list(done) if done is not None else None


def auto_family(purpose: str = "master") -> str:
    """Семейство, которое выберет «авто»: первое доступное по порядку ОС, иначе `cpu`.

    Считается по уже посчитанному (см. cached_available) — новых запусков ffmpeg нет."""
    live = cached_available(purpose) or []
    for family in auto_order():
        if family in live:
            return family
    return "cpu"


def reset_cache() -> None:
    """Забыть пробы и списки доступного: сменилось железо/драйвер или так просят тесты."""
    with _LOCK:
        _PROBE_CACHE.clear()
        _AVAILABLE.clear()


__all__ = ["ARGS", "CODECS", "COLOR_FILTER", "COLOR_MATRIX", "COLOR_PRIMARIES",
           "COLOR_RANGE", "COLOR_TAGS", "COLOR_TRC", "DEFAULT_ORDER", "DRAFT_ARGS",
           "DRAFT_BR", "DRAFT_CODECS", "DRAFT_Q", "FAMILIES", "H273_MATRIX",
           "H273_PRIMARIES", "H273_TRC", "LABELS", "MASTER_ARGS",
           "MASTER_CODECS", "OS_ORDER", "PROBE_FRAME", "PROBE_TIMEOUT", "PURPOSES",
           "SETTINGS", "VUI_ARGS", "VUI_BSF", "Choice", "auto_family", "auto_order",
           "available", "cached_available", "codec_args", "codec_name", "color_args",
           "color_filter", "family_of", "pick", "probe", "reset_cache", "uses_nvidia"]
