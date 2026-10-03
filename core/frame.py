# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Формат кадра — единственный источник размера выходного кадра.

Раньше 9:16 (1080×1920) был зашит числом в полутора десятках мест: секвенция
Premiere, масштаб клипов камер, коробки вставок в превью, кегль субтитров в
Resolve, ориентация поиска на стоках. Поменять формат было нечем: правка одного
места оставляла остальные в прежнем размере, и ролик собирался «наполовину».

Здесь формат объявлен ОДИН раз, и отсюда его берут все:

  * `FORMATS` — что вообще бывает, ключ и есть имя формата;
  * `frame_size(fmt)` — размер кадра (w, h) в пикселях;
  * `cover_scale(src_w, src_h, W, H)` — масштаб клипа камеры в секвенции Premiere,
    при котором кадр заполнен целиком («Заполнить кадр» Премьера);
  * `orientation(fmt)` — ориентация кадра: её спрашивают поиски стоков.

Формат ролика лежит в профиле спикера (`speakers/*.json`, поле `format`), а сам
спикер нарезки — в сайдкаре `<стем>.project.json` рядом с XML: ровно так его
достают для LUT (`core/lutbake.py`) и обработки голоса (`core/voicefx.py`), и
ровно поэтому формат не приходится тащить через полсотни вызовов.

Чей кадр главнее — XML или профиль — решает ОДНО правило, и живёт оно в
`frame_by_format`: профиль приказывает, только если поле `format` задано в нём
ЯВНО. Профиля нет, поля нет — кадр задаёт сам XML (`output_frame_size`), и файл
не переписывается: чужой XML и профиль, заведённый до форматов, обязаны
работать как работали. Формат задан, а пропорции сошлись (XML 2160×3840 при
формате 9:16) — кадр тоже у XML: это тот же формат, просто крупнее, и стиль под
него пересчитает `scale_style`. Разошлись пропорции — XML пересобирается в кадр
формата (`ensure_frame`). Значение незнакомое — `DEFAULT`, прежнее поведение
9:16 байт в байт.

Размеры исходника — ПОСЛЕ поворота из метаданных (`display_size`): телефоны и
часть камер пишут вертикаль как 1280×720 с матрицей поворота 90°, и без поворота
масштаб «заполнить кадр» считался бы по горизонтальному кадру (266 % вместо 150 %).

Здесь же — РАМКА КАДРА КАМЕРЫ: какая часть исходника попадает в кадр ролика.
Лежит в профиле спикера (`speakers/*.json`, поле `frame`) рядом с форматом:

    {"1": {"x": 0.5, "y": 0.5, "zoom": 100}, "2": {...}}

`x`/`y` — точка ПОКАЗЫВАЕМОГО исходника (доли, после поворота), которая встаёт в
центр кадра ролика; `zoom` — проценты от «кадр заполнен ровно», не меньше 100.
Камеры в поле нет или значения дефолтные — поведение прежнее: обрезка по центру.

Сдвиг слоя зажимается так, чтобы кадр оставался заполнен (`frame_crop`): при
`zoom` 100 и совпадении пропорций сдвигаться некуда вовсе. Зажим живёт РОВНО в
одной функции — `frame_shift` считается из `frame_crop`, а не повторяет её
арифметику; остальные места зовут готовое число.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:                       # тип сайдкара — только для аннотаций
    from core.project_file import ProjectFile

# Что бывает. Значение — размер кадра в пикселях, ключ — имя формата: оно же
# уезжает в профиль спикера, в интерфейс и в отчёты.
FORMATS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
    "16:9": (1920, 1080),
}

# Формат по умолчанию: вертикаль, в которой снято и смонтировано всё до сих пор.
# Именно он держит совместимость — у профилей, заведённых раньше, поля `format`
# нет, и такой ролик обязан собираться ровно как раньше, байт в байт.
DEFAULT = "9:16"

# Рамка кадра камеры по умолчанию: центр, без увеличения — обрезка поровну, как было.
FRAME_DEFAULT: dict[str, float] = {"x": 0.5, "y": 0.5, "zoom": 100.0}
# Границы поля `zoom`: меньше 100 — кадр не заполнен (по краям открылась бы пустота,
# а «рамка» перестала бы быть рамкой), больше 400 — увеличение, за которым исходник
# уже мыло. Проверка на ЗАПИСИ профиля строгая, при чтении чужого файла — мягкая.
FRAME_ZOOM_MIN, FRAME_ZOOM_MAX = 100.0, 400.0


def frame_size(fmt: Any) -> tuple[int, int]:
    """Размер кадра (w, h) для формата; неизвестное значение или None — DEFAULT.

    Незнакомое значение — не ошибка: профиль правят руками, и опечатка в поле
    формата не должна ронять нарезку — ролик просто соберётся в формате по
    умолчанию. Проверка на входе (`speakers.save`) при этом строгая, так что
    неизвестное значение тут может прийти только из файла, правленного мимо UI.
    """
    size = FORMATS.get(fmt) if isinstance(fmt, str) else None
    return size if size is not None else FORMATS[DEFAULT]


def cover_scale(src_w: float, src_h: float, W: float, H: float) -> float:
    """Масштаб клипа камеры в секвенции Premiere, %: кадр заполнен целиком.

    `max(W/src_w, H/src_h) * 100` — это ровно «Заполнить кадр» (Scale to Frame
    Size) Премьера: у вертикального 4K-исходника в вертикальном кадре 50 %, у
    горизонтального в вертикальном — по ширине, у квадратного в квадратном — 100 %.

    Размеры исходника — КАК ПОКАЗЫВАЕТСЯ, то есть после поворота из метаданных
    (`display_size`): у съёмки 1280×720 с `rotation=90` кадр вертикальный.

    Округление до сотых — столько же знаков, сколько у остальных чисел XML.
    Размер не прочитался (нулевой) — 100 %: не масштабировать честнее, чем
    разделить на ноль и уронить сборку.
    """
    if not src_w or not src_h or not W or not H:
        return 100.0
    return round(max(W / float(src_w), H / float(src_h)) * 100, 2)


def orientation(fmt: Any) -> str:
    """Ориентация кадра: "portrait" | "landscape" | "square".

    Её спрашивают поиски стоков (`orientation=portrait` у Pexels): в вертикальный
    ролик нужен вертикальный кадр, иначе вставка приезжает обрезанной по краям.
    """
    w, h = frame_size(fmt)
    if w > h:
        return "landscape"
    if h > w:
        return "portrait"
    return "square"


def display_size(w: int, h: int, rotation: Any) -> tuple[int, int]:
    """(w, h) КАК ПОКАЗЫВАЕТСЯ: поворот из метаданных на четверть оборота меняет стороны.

    Телефоны и часть камер пишут вертикальный кадр горизонтальным, а поворот
    кладут матрицей отображения (`rotation` в side data потока, его читает
    `xmlbuild.probe`). Считать масштаб по закодированному кадру нельзя: 1280×720
    при повороте — это вертикаль 720×1280.
    """
    try:
        rot = abs(int(float(rotation or 0))) % 180
    except (TypeError, ValueError):
        rot = 0            # мусор в поле (строка из чужого дампа) — считаем «без поворота»
    return (h, w) if rot == 90 else (w, h)


def speaker_format_explicit(speaker: Any) -> str | None:
    """Ключ формата из профиля спикера, ЕСЛИ он задан там явно; иначе None.

    От `speaker_format` отличается ровно этим: тот отдаёт DEFAULT и на «поля
    нет», а тут «поля нет» видно отдельно — и это решает судьбу XML. Профиль без
    поля `format` заведён до форматов и приказывать не может: кадр такого ролика
    задаёт сам XML (`output_frame_size`), а не 1080×1920.
    """
    if isinstance(speaker, str) and speaker.strip():
        from core import speakers
        speaker = speakers.load(speaker)
    if not isinstance(speaker, dict):
        return None
    fmt = speaker.get("format")
    return fmt if isinstance(fmt, str) and fmt in FORMATS else None


def speaker_format(speaker: Any) -> str:
    """Ключ формата ролика по профилю спикера: имя/ключ профиля, dict или None.

    Нет профиля, нет поля `format`, значение не из FORMATS — DEFAULT. Профиль,
    заведённый до появления формата, обязан работать как раньше: это и есть
    условие совместимости шага. Спрашивают там, где формат ролика надо НАЗВАТЬ
    (новая нарезка, поиск стоков); кадр уже существующего XML считает
    `output_frame_size` — там явный формат главнее, а не DEFAULT.
    """
    return speaker_format_explicit(speaker) or DEFAULT


def _sidecar_speaker(xml_path: str) -> Any:
    """Спикер из сайдкара рядом с XML (`<стем>.project.json`); нет сайдкара — None.

    Тем же путём спикера достают LUT (`core/lutbake.baked_cams_for_build`) и
    обработку голоса (`core/voicefx.final_voice_for_build`).
    """
    from core.project_file import read_project
    proj = read_project(os.path.splitext(str(xml_path))[0] + ".project.json")
    return None if proj is None else proj.get("speaker")


def xml_frame_size(xml_path: str) -> tuple[int, int]:
    """(w, h) формата, который ЗАКАЗАН профилем спикера рядом с XML.

    Это «что ролик должен быть по профилю», а не «что лежит в файле»: сайдкара
    нет, спикера в нём нет, поля `format` нет — DEFAULT. Кадр УЖЕ существующего
    XML считает `output_frame_size`: у профиля без явного формата последнее
    слово за XML, и подменять его тут DEFAULT'ом нельзя — ровно на этом чужой
    4K ужимался в 1080×1920.
    """
    return frame_size(speaker_format_explicit(_sidecar_speaker(xml_path)))


def frame_by_format(file_size: tuple[int, int] | None, fmt: str | None) -> tuple[int, int]:
    """Кадр ролика по размеру секвенции файла и формату, заданному ЯВНО (`fmt`).

    Единственная копия правила «чей кадр главнее»:

      * формат не задан (`fmt is None`) — кадр задаёт сам XML: `file_size`, а не
        прочитался — DEFAULT. Чужой XML, субтитры-только и нарезка без профиля
        обязаны собираться как собирались: ужимать их нечем;
      * формат задан, пропорции сошлись (XML 2160×3840 при формате 9:16) — кадр
        равен файлу: это тот же формат, просто крупнее, и стиль под него
        пересчитывается сам (`scale_style`). Пиксели тут не сравниваем: «почти
        тот же» размер — это ровно тот же формат;
      * формат задан, пропорции разошлись — кадр формата: XML пересоберёт
        `ensure_frame`. Файла ещё нет (первая сборка) — тоже кадр формата.
    """
    if fmt is None:
        return file_size or frame_size(DEFAULT)
    want = frame_size(fmt)
    if file_size is None or not frame_proportions_match(file_size, want):
        return want
    return file_size


def output_frame_size(xml_path: str) -> tuple[int, int]:
    """(w, h) кадра ролика по существующему XML: правило — `frame_by_format`.

    Нужен там, где ролик уже нарезан и его кадр надо узнать, а не назначить:
    сборка .jsx, рото, пересборка XML из редактора, пересохранение. Профиль
    спикера тут только советует — распоряжается он лишь явным полем `format`.
    """
    return frame_by_format(seq_frame_size(xml_path), speaker_format_explicit(
        _sidecar_speaker(xml_path)))


def frame_proportions_match(xml_size: tuple[int, int], want: tuple[int, int]) -> bool:
    """Совпадают ли ПРОПОРЦИИ кадров (`w*H == h*W`), а не пиксели.

    Сравнение ровно по пикселям считало ролик 2160×3840 разошедшимся с форматом
    9:16 — и XML пересобирался в 1080×1920, то есть чужой 4K насильно ужимался.
    Один формат бывает разного размера, и это не расхождение.

    Нулевая сторона — «не кадр»: пропорций у него нет, совпадением не считаем
    (иначе 0×0 «совпал» бы с любым кадром).
    """
    if not (xml_size[0] and xml_size[1] and want[0] and want[1]):
        return False
    return int(xml_size[0]) * int(want[1]) == int(xml_size[1]) * int(want[0])


def seq_frame_size(xml_path: str) -> tuple[int, int] | None:
    """Размер кадра САМОЙ секвенции XML (width/height), None — не прочитался.

    Это НЕ `output_frame_size`: тот отдаёт кадр ролика (что ролик есть с учётом
    формата), а здесь — что реально лежит в файле. Разница и есть расхождение,
    из-за которого Premiere показывал бы один кадр, а After Effects собирал другой.

    Разбирается ровно один элемент (`samplecharacteristics` первой секвенции), без
    `xml2ae.parse_full`: проверка нужна там, где полный разбор ещё впереди, и он же
    кэширует разобранное дерево — второй тяжёлый проход по файлу ни к чему.
    """
    import xml.etree.ElementTree as ET

    try:
        fmt = ET.parse(str(xml_path)).getroot().find(
            ".//sequence//media/video/format/samplecharacteristics")
        if fmt is None:
            return None
        w, h = fmt.findtext("width"), fmt.findtext("height")
        if w is None or h is None:
            return None
        return int(str(w)), int(str(h))
    except Exception:                   # битый/чужой XML — «не прочитал», не падение
        return None


def ensure_frame(xml_path: str, emit: Any = None) -> tuple[int, int]:
    """Кадр ролика по XML: XML пересобирается, только если разошлись пропорции.

    Формат выбирают в профиле спикера, а XML пишется нарезкой — и между этими
    двумя событиями формат мог поменяться. Тогда XML остался бы в старом кадре:
    Premiere показал бы вертикаль, After Effects собрал квадрат, и «разъехались
    по формату» выглядело бы как сломанная сборка. Поэтому перед чтением XML
    (план, превью, черновик, .jsx) кадр файла сверяется с профилем и при
    РАСХОЖДЕНИИ ПРОПОРЦИЙ XML пересобирается — тем же путём, что «Сохранить» в
    редакторе нарезки (`api/editor.py`, `api_editor_save`): куски `keep`, камеры
    и субтитры из сайдкара, слова и жёлтые переносятся через `xmlbuild.build`.

    Чей кадр главнее, решает `frame_by_format`, и правило там одно на всех:
    профиль приказывает, только если поле `format` задано в нём ЯВНО. Профиля
    нет, поля нет — кадр задаёт сам XML, и файл не переписывается вовсе (чужой
    4K без явного формата раньше ужимался в 1080×1920). Формат задан, а
    пропорции те же (XML 2160×3840 при 9:16) — тоже не трогаем: это тот же
    формат, просто крупнее, и стиль под него пересчитает `scale_style`.

    Пересборка не удалась — тоже не падаем: кадр ролика остаётся форматом
    спикера, XML — прежним, а в лог уходит предупреждение (собрать ролик важнее,
    чем объяснить, почему он не той пропорции).
    """
    proj_path = os.path.splitext(str(xml_path))[0] + ".project.json"
    from core.project_file import read_project
    proj = read_project(proj_path)
    fmt = speaker_format_explicit(proj.get("speaker")) if proj is not None else None
    file_size = seq_frame_size(xml_path)
    if proj is None or fmt is None:
        return frame_by_format(file_size, None)      # формат не заказан — кадр из самого XML
    want = frame_size(fmt)
    if file_size is None or frame_proportions_match(file_size, want):
        return want if file_size is None else file_size
    if not _rebuild_frame(xml_path, proj_path, proj, want, _frame_log(emit)):
        return want                              # XML пересобрать не вышло — кадр спикера
    return seq_frame_size(xml_path) or want


def _silent(*args: Any, **vars: Any) -> None:
    """Заглушка лога: `emit` не передан — молчание, как и было у `ensure_frame`."""
    return None


def _frame_log(emit: Any) -> Callable[..., Any]:
    """Колбэк лога для пересборки кадра: общий образец `wrap_emit`.

    Шаблон и подстановки уходят в него как в остальных модулях — переводит и
    форматирует `wrap_emit`/`console_emit` (ключи шаблонов лежат в
    `static/i18n/en.json`), а не сам код пересборки. `emit` не передан —
    молчание: так `ensure_frame` вёл себя и раньше.
    """
    from core.app_meta import wrap_emit
    return _silent if emit is None else wrap_emit(emit)


def _rebuild_frame(xml_path: str, proj_path: str, proj: "ProjectFile",
                   want: tuple[int, int], log: Callable[..., Any]) -> bool:
    """Пересобрать XML в кадре `want`. True — файл перезаписан.

    Порядок тот же, что у пересохранения из редактора: сперва вычитать субтитры
    и жёлтые из ТЕКУЩЕГО XML (в `keep` они не лежат), потом собрать заново и
    вписать разметку обратно. Раскладка камер (`assign`) сохраняется: формат её
    не трогает. `log` — уже обёрнутый `wrap_emit` колбэк (`_frame_log`).
    """
    import traceback

    def _say(template: str, **vars: Any) -> None:
        """Строка в лог: шаблон с подстановками, как у остальных emit.

        Ронять сборку из-за лога нельзя: колбэк чужой, и его исключение
        выглядело бы как «XML пересобрать не удалось».
        """
        try:
            log(template, **vars)
        except Exception:
            pass                    # лог не повод не собрать ролик

    try:
        import shutil
        from core import align, xml2ae, xmlbuild
        from core.project_file import write_project
        cams = proj.get("cams") or []
        offsets = proj.get("offsets") or []
        if not cams or len(offsets) != len(cams):
            _say("⚠ формат кадра: в сайдкаре нет камер — XML оставлен как есть")
            return False
        keep = [(float(s), float(e)) for s, e in (proj.get("keep") or [])]
        _meta, _pcams, subs, _ins = xml2ae.parse_full(xml_path)
        sub_words = ([{"w": w, "start": int(s), "end": int(e)} for (s, e, w) in subs]
                     if subs else None)
        yellow = xml2ae.auto_highlights(xml_path).get("yellow", [])
        assign = align.assign_for_project(proj, keep, len(cams))
        _say("  · формат кадра: XML был {ow}×{oh}, у спикера {nw}×{nh} — пересобираю",
             ow=_meta["w"], oh=_meta["h"], nw=want[0], nh=want[1])
        stem = os.path.splitext(xml_path)[0]
        bak_path = f"{stem}.xml.format.bak"
        if os.path.isfile(xml_path):
            try:
                shutil.copy2(xml_path, bak_path)
            except OSError:
                pass  # бекап не критичен для сборки XML
        xmlbuild.build(cams, keep, offsets, xml_path,
                       assign=assign,
                       sub_words=sub_words, music_path=None)
        xml2ae.write_srt_for(xml_path)
        if yellow:
            xml2ae.write_highlights(xml_path, yellow)
        write_project(proj_path, proj)
        return True
    except Exception as ex:                        # noqa: BLE001 — сборка важнее формата
        size = seq_frame_size(xml_path)
        _say("⚠ формат кадра: XML пересобрать не удалось ({err}) — в файле остаётся "
             "{ow}×{oh}", err=ex, ow=(size or ("?", "?"))[0], oh=(size or ("?", "?"))[1])
        _say(traceback.format_exc())
        return False


# --------------------------------------------------------------------------- #
# Рамка кадра камеры
# --------------------------------------------------------------------------- #
def _num(raw: Any, default: float) -> float:
    """Число из чужого JSON: строка, bool и мусор — дефолт, а не падение.

    Профиль правят и руками, и правкой файла мимо интерфейса: опечатка в рамке не
    должна ронять нарезку. На ЗАПИСИ профиля проверка строгая (`check_frames`),
    так что сюда мусор приходит только из файла.
    """
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def frame_of(frames: Any, cam: int) -> dict[str, float]:
    """Рамка камеры `cam` (номер с 1) из поля `frame` профиля; нет — дефолт.

    Ключ — номер камеры строкой, как в поле `lut` («1», «2»): у камеры без рамки
    ключа нет вовсе, и такой ролик собирается как собирался.
    """
    if not isinstance(frames, dict):
        return dict(FRAME_DEFAULT)
    raw = frames.get(str(int(cam)))
    if not isinstance(raw, dict):
        return dict(FRAME_DEFAULT)
    fr = {"x": _num(raw.get("x"), 0.5), "y": _num(raw.get("y"), 0.5),
          "zoom": _num(raw.get("zoom"), 100.0)}
    return fr


def frames_of(speaker: Any) -> dict[str, dict[str, float]]:
    """Поле `frame` профиля спикера: имя/ключ профиля, dict или None.

    Читается тем же путём, что формат кадра (`speaker_format`): спикер нарезки
    лежит в сайдкаре рядом с XML, и тащить его через полсотни вызовов не нужно.
    Мусор в значениях смягчается до дефолта — рамка не повод не собрать ролик.
    """
    if isinstance(speaker, str) and speaker.strip():
        from core import speakers
        speaker = speakers.load(speaker)
    if not isinstance(speaker, dict):
        return {}
    raw = speaker.get("frame")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, float]] = {}
    for key in raw:
        cam = str(key).strip()
        if not cam.isdigit() or int(cam) < 1 or not isinstance(raw[key], dict):
            continue
        fr = frame_of(raw, int(cam))
        fr["zoom"] = max(FRAME_ZOOM_MIN, min(FRAME_ZOOM_MAX, fr["zoom"]))
        fr["x"] = max(0.0, min(1.0, fr["x"]))
        fr["y"] = max(0.0, min(1.0, fr["y"]))
        out[cam] = fr
    return out


def xml_frames(xml_path: str) -> dict[str, dict[str, float]]:
    """Рамки камер по XML: спикер — в сайдкаре рядом с ним (как в `output_frame_size`).

    Тем же путём спикера берут формат кадра, LUT (`core/lutbake`) и обработку
    голоса (`core/voicefx`). Сайдкара нет, он битый или спикера в нём нет — рамок
    нет: сборка без сайдкара обязана работать как раньше.
    """
    from core.project_file import read_project
    proj = read_project(os.path.splitext(str(xml_path))[0] + ".project.json")
    if proj is None:
        return {}
    return frames_of(proj.get("speaker"))


def is_frame_default(fr: Any) -> bool:
    """Рамка ничего не меняет: нет её или 0.5/0.5/100 — поведение прежнее.

    По этому признаку сборка решает, писать ли рамку в .jsx и в XML: дефолт не
    должен менять ни одной строки (эталоны .jsx, golden).
    """
    if not isinstance(fr, dict):
        return True
    return (float(fr.get("x", 0.5)) == 0.5 and float(fr.get("y", 0.5)) == 0.5
            and float(fr.get("zoom", 100.0)) == 100.0)


def check_frames(raw: Any) -> dict[str, dict[str, float]]:
    """Проверить поле `frame` перед записью профиля (иначе — ValueError).

    Строгая проверка тут, а не при использовании, — как у `lut` и `format`:
    профиль единственное место, где рамка вообще лежит, и молча записанный мусор
    (номер камеры строкой, сдвиг 1.5, зум 50) всплыл бы потом в сборке, уже без
    объяснения откуда. Значения, пришедшие ИЗ файла (правленного руками), при
    использовании смягчает `frames_of` — профиль из-за них не падает.

    Рамка на дефолте ключа не заводит: пустая правка не должна растить профиль.
    """
    if not isinstance(raw, dict):
        raise ValueError("поле frame должно быть объектом")
    out: dict[str, dict[str, float]] = {}
    for key, val in raw.items():
        cam = str(key).strip()
        if not cam.isdigit() or int(cam) < 1:
            raise ValueError(f"некорректный номер камеры в frame: {key!r} (нужно число от 1)")
        if not isinstance(val, dict):
            raise ValueError(f"frame.{cam} должен быть объектом")
        unknown = [k for k in val if k not in ("x", "y", "zoom")]
        if unknown:
            raise ValueError("неизвестные ключи frame.%s: " % cam
                             + ", ".join(sorted(str(k) for k in unknown)))
        fr: dict[str, float] = {}
        for axis in ("x", "y"):
            v = _num(val.get(axis), 0.5)
            if not 0.0 <= v <= 1.0:
                raise ValueError(f"frame.{cam}.{axis}: ожидается доля от 0 до 1, а не {v:g}")
            fr[axis] = round(v, 4)
        zoom = _num(val.get("zoom"), 100.0)
        if not FRAME_ZOOM_MIN <= zoom <= FRAME_ZOOM_MAX:
            raise ValueError(f"frame.{cam}.zoom: ожидается число от {FRAME_ZOOM_MIN:g} "
                             f"до {FRAME_ZOOM_MAX:g}, а не {zoom:g}")
        fr["zoom"] = round(zoom, 2)
        if not is_frame_default(fr):
            out[cam] = fr
    return out


def frame_crop(fr: Any, src_w: float, src_h: float, W: float, H: float) -> tuple[float, float, float, float]:
    """Какой кусок исходника попадает в кадр: (x, y, w, h) в пикселях исходника.

    Видимый размер — доля 1/zoom от «кадр заполнен ровно» и ровно с пропорциями
    кадра ролика (`cover_scale`). Центр куска — точка рамки (доли показываемого
    кадра); он ЗАЖИМАЕТСЯ в границы исходника: показать то, чего в исходнике нет,
    нельзя, и именно это зажимание и есть «кадр всегда заполнен» — при zoom 100 и
    совпадении пропорций сдвигаться некуда. Зажим живёт ТОЛЬКО здесь:
    `frame_shift` считается из этого куска, второй копии min/max в проекте нет.

    Размер исходника не прочитался (нулевой) — кусок пустой: масштабировать
    нечего, и делить на ноль нельзя.
    """
    if not src_w or not src_h or not W or not H:
        return 0.0, 0.0, 0.0, 0.0
    fr = fr if isinstance(fr, dict) else {}
    f = max(W / float(src_w), H / float(src_h))          # «заполнить кадр», px кадра на px исходника
    if is_frame_default(fr):
        # Рамки нет: обрезка поровну, ни сдвига, ни зажима. Считаем отдельной веткой,
        # а не общим путём: округление до сотых там сдвинуло бы центр на 0.01 px, и
        # «поведение прежнее» перестало бы быть побитовым.
        cw = min(float(src_w), W / f)
        ch = min(float(src_h), H / f)
        return (round((float(src_w) - cw) / 2.0, 2), round((float(src_h) - ch) / 2.0, 2),
                round(cw, 2), round(ch, 2))
    z = max(1.0, _num(fr.get("zoom"), 100.0) / 100.0)
    fx = min(1.0, max(0.0, _num(fr.get("x"), 0.5)))
    fy = min(1.0, max(0.0, _num(fr.get("y"), 0.5)))
    cw = min(float(src_w), W / (f * z))
    ch = min(float(src_h), H / (f * z))
    cx = min(max(fx * float(src_w), cw / 2.0), float(src_w) - cw / 2.0)
    cy = min(max(fy * float(src_h), ch / 2.0), float(src_h) - ch / 2.0)
    return (round(cx - cw / 2.0, 2), round(cy - ch / 2.0, 2),
            round(cw, 2), round(ch, 2))


def frame_shift(fr: Any, src_w: float, src_h: float, W: float, H: float) -> tuple[float, float]:
    """Сдвиг слоя камеры в пикселях кадра ролика: (0, 0) — кадр по центру.

    Это то же, что Basic Motion > Center в Premiere и Position слоя в After
    Effects: слой сдвигается на столько, на сколько центр видимого куска ушёл от
    центра исходника. Считается ИЗ зажима (`frame_crop`) — второй формулы сдвига
    в проекте нет.
    """
    if not src_w or not src_h or not W or not H:
        return 0.0, 0.0
    fr = fr if isinstance(fr, dict) else {}
    if is_frame_default(fr):
        return 0.0, 0.0          # кадр по центру — сдвига нет по определению
    z = max(1.0, _num(fr.get("zoom"), 100.0) / 100.0)
    f = max(W / float(src_w), H / float(src_h))
    x, y, cw, ch = frame_crop(fr, src_w, src_h, W, H)
    if not cw or not ch:
        return 0.0, 0.0
    return (round((float(src_w) / 2.0 - (x + cw / 2.0)) * f * z, 2),
            round((float(src_h) / 2.0 - (y + ch / 2.0)) * f * z, 2))


def frame_zoom(fr: Any) -> float:
    """Множитель масштаба слоя камеры от рамки: 1.0 — кадр заполнен ровно."""
    fr = fr if isinstance(fr, dict) else {}
    return max(1.0, _num(fr.get("zoom"), 100.0) / 100.0)

