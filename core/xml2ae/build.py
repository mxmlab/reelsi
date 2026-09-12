# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сборка .jsx: to_ae_full (один таймлайн) и build_combined (несколько в один файл).

Здесь же virtual_edl — тот же разбор, но для черновика ffmpeg, без AE.
"""
import os
import re
from core import paths
from core.app_meta import console_emit, wrap_emit
from core import fonts as _fonts

from .jsutil import _asset_or, _fill_js, _jd, _js, _js_multiline, _r
from .layout import (DEFAULT_DISCLAIMER, DISC_FIT_W, EASE_DEFAULT, HL_EASE_IN, HL_EASE_OUT,
                     INS_C1_HIGH, INS_C2_BASE, INS_C2_PEAK, INS_C2_Y_FR, INS_EXIT,
                     INS_RISE_DY, INS_RISE_S0, INS_RISE_ENTER, INTRO_BASE_Y,
                     INTRO_F_DUR, INTRO_F_OUT, INTRO_FIT_W, INTRO_HOLD, INTRO_SCALE,
                     SUB_BG_SH_DIR, SUB_BG_SH_DIST,
                     SUB_BG_SH_OP, SUB_BG_SH_SOFT, ZOOM_BIG,
                     cover_sweep,
                     _anim_keys,
                     _blur_keys, _cam1_drift_keys, _cam1_jump_keys, _cam1_pos_keys,
                     _cam1_zoom_keys, _cam_change_frames, _censor_windows,
                     _fill_slack, _fit_scale, _ins_card, _ins_enter_exit, _ins_scale,
                     _intro_group_window, _intro_i_dy, _media_dims, _project_base,
                     _show_segments, _span_roto_plan, _stack_layout,
                     _zoom_key_eases, _zoom_max,
                     intro_line_ys)
from .parse import Cancelled, HERE, _is_image, parse_full
from .template import AE_FULL, SUBS_LOOP_WORDS, SUBS_LOOP_ROWS, SUBS_LOOP_WORDS_JOINED


def _sub_bg_expr(st, sub_layer_name="Субтитры (текст)"):
    """Выражение на размер плашки субтитров (задание DE, обновлено DL).

    Текст берётся целиком из refs/sub_bg_size.js, подставляются 4 константы из стиля
    и имя слоя субтитров в главном композе.
    """
    h = float(st.get("sub_bg_h") if st.get("sub_bg_h") is not None else 160.0)
    pad = float(st.get("sub_bg_pad") if st.get("sub_bg_pad") is not None else 18.0) / 100.0
    padmin = float(st.get("sub_bg_padmin") if st.get("sub_bg_padmin") is not None else 70.0)
    anim = float(st.get("sub_bg_anim") if st.get("sub_bg_anim") is not None else 0.22)
    ref_path = paths.data("refs", "sub_bg_size.js")
    with open(ref_path, "r", encoding="utf-8") as f:
        src = f.read()
    marker = "// --- НАСТРОЙКИ ---"
    idx = src.find(marker)
    if idx != -1:
        src = src[idx:]
    src = re.sub(r"const\s+fixedHeight\s*=\s*[^;]+;", f"const fixedHeight  = {h:g};", src)
    src = re.sub(r"const\s+padPercent\s*=\s*[^;]+;", f"const padPercent   = {pad:g};", src)
    src = re.sub(r"const\s+minPadX\s*=\s*[^;]+;", f"const minPadX      = {padmin:g};", src)
    src = re.sub(r"const\s+animDuration\s*=\s*[^;]+;", f"const animDuration = {anim:g};", src)
    src = src.replace('const precompLayer = thisComp.layer("Субтитры (текст)");', f'const precompLayer = thisComp.layer({_js(sub_layer_name)});')
    return src


def _caption_bg_size_expr(kx, ky):
    """Выражение на «Размер прямоугольника» плашки под подписью (задание DG, обновлено DL)."""
    ref_path = paths.data("refs", "caption_bg_size.js")
    with open(ref_path, "r", encoding="utf-8") as f:
        src = f.read()
    lines = [ln for ln in src.splitlines() if not ln.startswith("//")]
    src = "\n".join(lines).strip()
    src = src.replace('targetLayerName = "textlayer1";', 'targetLayerName = "Подпись";')
    src = re.sub(
        r'\[r\.width\s*\*\s*[\d.]+\s*,\s*r\.height\s*\*\s*[\d.]+\];[^\n]*',
        f'[r.width * {kx:g}, r.height * {ky:g}];',
        src,
    )
    return src


def _caption_pos_expr(cap_x, cap_y, kx):
    """Выражение на позицию ТЕКСТА подписи: caption_x — ЛЕВЫЙ КРАЙ блока (задание DL).

    Текст берётся из refs/caption_pos.js, подставляются три константы из стиля.
    Ширину плашки AE знает только при отрисовке, поэтому центр надписи считается
    выражением: левый край + половина ширины плашки.
    """
    ref_path = paths.data("refs", "caption_pos.js")
    with open(ref_path, "r", encoding="utf-8") as f:
        src = f.read()
    lines = [ln for ln in src.splitlines() if not ln.startswith("//")]
    src = "\n".join(lines).strip()
    return re.sub(r"const CAP_X = [^;]+;",
                  f"const CAP_X = {cap_x:g}, CAP_Y = {cap_y:g}, KX = {kx:g};", src)


def _caption_bg_pos_expr():
    """Выражение на позицию плашки под подписью (задание DG) — центрирование по тексту."""
    ref_path = paths.data("refs", "caption_bg_pos.js")
    with open(ref_path, "r", encoding="utf-8") as f:
        src = f.read()
    lines = [ln for ln in src.splitlines() if not ln.startswith("//")]
    return "\n".join(lines).strip()


def _accent_word(w, case):
    """Регистр слова — единая машинка для акцента интро (задание R) и регистра субтитров
    (задание CO). Один источник трансформации: .jsx и план читают готовый текст, второй
    копии правила не заводится.
    title/sentence — Заглавная первая, остальные строчные («Сдо*нуть» из «СДО*НУТЬ»);
    as-is — не трогаем; upper — всё заглавное; lower — всё строчное."""
    s = str(w)
    if case == "upper":
        return s.upper()
    if case == "lower":
        return s.lower()
    if case == "as-is":
        return s
    return s[:1].upper() + s[1:].lower() if s else s


def _intro_line_font(line, intro_font_ps, intro_hl_font_ps):
    """Шрифт строки интро — ЕДИНСТВЕННАЯ лесенка (задание BP): акцентный перекрывает;
    жёлтая строка — intro_hl_font, иначе intro_font. Её же используют автофит и
    plan.intro[].fonts; превью своей лесенки не держит (жёлтые строки рисовались
    системным шрифтом и выходили на 41 % шире, чем в AE)."""
    af = (line.get("accent_font") or "").strip()
    if af:
        return af
    if line.get("color") == "yellow":
        return intro_hl_font_ps
    return intro_font_ps


def _intro_fit_ds(lines, ts, te, ds, w, G, cam_keys, fps, st, intro_font_ps,
                  intro_hl_font_ps, fsize, hold=False):
    """Автофит группы интро (задание BP): широкая строка видна как lineW·(iSc/100)·G·Z
    (iSc = INTRO_SCALE·ds/100 — масштаб прекомпа, G — общий масштаб интро, Z — зум
    Камеры 1), и если с МАКСИМАЛЬНЫМ зумом на окне группы [ts, te] она шире 0.92·W,
    ужимаем ds ровно до равенства. Применяется, только когда gs == 100 (задание CF:
    рука сильнее автофита — если gs != 100, группу масштабировали вручную).
    st нужен для back_scale; шрифты приходят готовыми (intro_font_ps/intro_hl_font_ps),
    свою лесенку автофит не заводит — иначе измерит не тот шрифт, что уйдёт в AE.
    Шрифт не найден — ширины нет, группу не трогаем: ужать по неизвестной ширине хуже, чем не ужать."""
    if not lines:
        return ds
    linew = 0.0
    for ln in lines:
        ps = _intro_line_font(ln, intro_font_ps, intro_hl_font_ps)
        fs = round(fsize * float(st.get("back_scale") if st.get("back_scale") is not None else 0.69)) if ln.get("back") else fsize
        wpx = _fonts.text_width(ps, " ".join(ln.get("words") or []), fs)
        if wpx is None:
            return ds
        linew = max(linew, wpx)
    z = _zoom_max(cam_keys, fps, ts, te, hold=hold)
    fit = 100.0 * w * INTRO_FIT_W / (linew * (INTRO_SCALE / 100.0) * G * (z / 100.0))
    return min(ds, fit)


def _parse_intro_count(text, raw_dec=None):
    """Разбор целевого числа для anim=='count' в интро.
    Числом считается текст, состоящий из цифр, возможно с ОДНИМ разделителем
    дробной части — запятой или точкой, и возможно с пробелами-разделителями тысяч.
    Возвращает (target, dec, expr, sep) или None, если текст не число.
    """
    if not text:
        return None
    s = re.sub(r"\s+", " ", str(text)).strip()
    if not s:
        return None
    dots = s.count(".")
    commas = s.count(",")
    if dots + commas > 1:
        return None
    sep = "." if dots == 1 else ("," if commas == 1 else None)
    if sep:
        int_part, frac_part = s.split(sep)
        int_clean = int_part.replace(" ", "")
        if (
            not int_clean
            or not int_clean.isascii()
            or not int_clean.isdigit()
            or not frac_part
            or not frac_part.isascii()
            or not frac_part.isdigit()
        ):
            return None
        auto_dec = len(frac_part)
        try:
            val = float(int_clean + "." + frac_part)
        except ValueError:
            return None
    else:
        int_clean = s.replace(" ", "")
        if not int_clean or not int_clean.isascii() or not int_clean.isdigit():
            return None
        auto_dec = 0
        try:
            val = int(int_clean)
        except ValueError:
            return None

    # Автоподсчёт знаков после запятой по числу: целое -> 0, 2.5 или 2,5 -> 1 знак
    dec = auto_dec

    if dec == 0:
        expr = 'Math.round(effect("Slider Control")("Slider"))'
    else:
        if sep == ".":
            expr = f'(effect("Slider Control")("Slider")).toFixed({dec})'
        else:
            expr = f'(effect("Slider Control")("Slider")).toFixed({dec}).replace(".", ",")'

    target = int(val) if (isinstance(val, float) and val.is_integer() and dec == 0) else val
    return target, dec, expr, sep


def _intro_cnt_positions(x, nwords):
    """Позиции слов строки, на которых стоит счётчик (cnt_words), по возрастанию.

    cnt_words — новый формат (список позиций внутри строки). Мусор, дроби и выход за
    границы строки молча пропускаем: строка без единого разобранного числа остаётся
    без счётчика. Пустой список — пустой результат (счётчиков нет).
    """
    cw = x.get("cnt_words")
    if not isinstance(cw, list):
        return []
    out = set()
    for v in cw:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        try:
            p = int(v)
        except (ValueError, OverflowError):    # NaN/бесконечность из чужого JSON — не позиция
            continue
        if p != v or not (0 <= p < nwords):
            continue
        out.add(p)
    return sorted(out)


def _has_valid_count(x):
    if isinstance(x.get("cnt_words"), list):
        wds = [str(wd) for wd in (x.get("words") or (x.get("text") or "").split())]
        for p in _intro_cnt_positions(x, len(wds)):
            if _parse_intro_count(wds[p], x.get("dec")) is not None:
                return True
        return False
    if not (x.get("is_count") or x.get("anim") == "count"):
        return False
    wds = [str(wd) for wd in (x.get("words") or (x.get("text") or "").split())]
    for wd in wds:
        if _parse_intro_count(wd, x.get("dec")) is not None:
            return True
    return _parse_intro_count(" ".join(wds).strip(), x.get("dec")) is not None


# Параметры анимаций интро (глитч, раскрытие): сборка .jsx берёт числа
# из этого словаря, а план сцены (scene_plan) передаёт их в превью браузера.
# Единый источник истины — вторая копия в JS не заводится.
INTRO_ANIMS = {
    "glitch": {
        "dur": 0.44,
        # сила Gaussian Blur на слое слова глитча (было 6.8, пользователь 2026-09-11: вдвое слабее)
        "blur": 3.4,
        "end_keys": [
            [0.017, 100],
            [0.205, 5],
            [0.392, 100],
        ],
        "op_keys": [
            [0.0, 0],
            [0.05, 100],
            [0.1, 100],
            [0.1417, 0],
            [0.1833, 93],
            [0.225, 0],
            [0.2667, 100],
        ],
    },
    "reveal": {
        "dur": 0.44,
        "blur": 26.8,
        "scale": 0.7,
        "scale_3d": [11, 11, 91.66667],
        "shape": 2,
        "smoothness": 100,
        "ease": [10, 95],
    },
}

# Звук глитча в секундах (не зависит от fps ролика): огибающая слоя на ГРУППУ глитч-слов
# (ПРАВКА 1/2). Слой стартует за GLITCH_SFX_PRE_S до первого слова группы — это смещение
# снято с эталона (17 кадров при 30 fps) и его не менять. До первого слова — тишина,
# нарастание до glitch_db за GLITCH_SFX_ATTACK_S, полка, затем спад за
# GLITCH_SFX_RELEASE_S до GLITCH_SFX_QUIET_DB; слой кончается через один кадр после
# конца спада (в эталоне зазор 0.013–0.036 с).
GLITCH_SFX_PRE_S = 0.567      # старт слоя за это время до первого слова группы, с
GLITCH_SFX_ATTACK_S = 0.08    # нарастание от тишины до glitch_db у первого слова, с
GLITCH_SFX_HOLD_S = 0.45      # полка звука после последнего слова группы, с
GLITCH_SFX_RELEASE_S = 0.12   # спад до тишины, с
GLITCH_SFX_QUIET_DB = -48.0   # уровень «тихо» (как у микро-фейдов клипов камеры), dB


def scene_plan(xml_path, cam1_scale=None,   # None -> авто по сменам кам1→кам2
               music=None, music_db=-20.0, music_dir=None, base=None,
               disclaimer=DEFAULT_DISCLAIMER, disc_sec=1.35, intro_riser=True,
               highlights=None, hl_breaks=None, hl_count=None, hl_joins=None, inserts=None, censor_audio=True, intro=None,
               intro_remove=None, intro_splits=None, ncams=None, exposure=0.0, intro_mode="word",
               roto=False, roto_bottom=0.0, roto_device=None, style=None,
               music_random=False, emit=None,
               include_xml_inserts=True, cancel=None, word_timings=None,
               caption=None):
    """ПЛАН СЦЕНЫ (задание C): вся арифметика сборки, без записи .jsx и без GPU.
    to_ae_full рендерит из него шаблон после рото-масок; предпросмотр (задание D)
    читает план напрямую. Поля — контракты JSX-структур (CAM/SUBS/INSERTS/INTRO_GROUPS)
    и производные: inserts[].anim (готовые ключи анимаций вставок — раньше их считал
    ExtendScript, остаток задания B), roto — разметка РОТО-фрагментов (маски делает
    to_ae_full, GPU), zoom.keys/ease — ключи Камеры 1. cancel — колбэк «нажали Стоп?»
    (см. Cancelled); свой emit — туда, где раньше печатали в консоль."""
    _stop = cancel or (lambda: False)
    emit = wrap_emit(emit)

    def _ckpt(stage):
        """Точка между этапами: показать, где мы, и проверить «Стоп»."""
        if stage:
            emit("  · {stage}", stage=stage)
        if _stop():
            raise Cancelled()

    _ckpt("разбор XML")
    meta, cams, subs, xml_inserts = parse_full(xml_path, ncams=ncams)
    if not cams:
        # ValueError, а НЕ SystemExit: вызывающие ловят только Exception, поэтому
        # SystemExit пролетал сквозь них — /api/to_ae отдавал 500-HTML вместо {error},
        # а фоновая сборка молча писала «Сборка завершена» с пустым results.
        raise ValueError("Не нашёл видеодорожки с камерами в XML.")
    for _ci, _c in enumerate(cams):                 # диагностика 1-кам «нет названий нулов»: пустой путь
        if not (_c.get("path") or "").strip():
            emit("⚠ Камера {cam} без пути к файлу (нул создастся пустым — проверь XML).", cam=_ci + 1)
    _fp = meta["fps"]
    inserts = [dict(x) for x in (inserts or [])]       # копии: не мутируем словари вызывающего
    if include_xml_inserts:
        inserts += [dict(
            type=xi["type"], style=xi.get("style") or "cam2", media=xi["media"],
            start_s=xi["start"] // _fp, start_f=xi["start"] % _fp,
            dur_s=(xi["end"] - xi["start"]) // _fp, dur_f=(xi["end"] - xi["start"]) % _fp,
            scale=_ins_scale(xi["media"], xi.get("style") or "cam2"), mosaic=False,
            sin=xi.get("sin", 0) / _fp)                # source in-point в секундах
            for xi in xml_inserts]
    from core import styles as _styles  # пресет стиля (шрифт/цвет/звуки/рото/вставки)
    st = _styles.resolve(style)
    if st.get("disclaimer") is not None:               # стиль переопределяет дисклеймер ("" = скрыть)
        disclaimer = st["disclaimer"]
    if st.get("intro_riser") is not None:              # стиль может отключить интро-SFX (ризер)
        intro_riser = bool(st["intro_riser"])
    _fps0 = meta["fps"] or 60

    def _active_cam_at(t_sec):
        """Индекс камеры, реально показываемой в момент t (сек): верхняя включённая
        перебивка, иначе Камера 1. Как camAt в JSX."""
        f = t_sec * _fps0 + 1e-4
        best = 0
        for ci in range(len(cams)):
            for cl in cams[ci]["clips"]:
                if cl[4] and cl[0] <= f < cl[1]:
                    best = ci
                    break
        return best

    def _ins_t0(x):                                    # старт вставки в секундах (сек+кадры или легаси-кадры)
        s, fr = x.get("start_s"), x.get("start_f")
        if s is not None or fr is not None:
            return float(s or 0) + float(fr or 0) / _fps0
        return float(x.get("start") or 0) / _fps0

    def _ins_t1(x):                                    # конец вставки в секундах (старт+длительность или явный конец)
        if x.get("dur_s") is not None or x.get("dur_f") is not None:
            return _ins_t0(x) + float(x.get("dur_s") or 0) + float(x.get("dur_f") or 0) / _fps0
        s, fr = x.get("end_s"), x.get("end_f")
        if s is not None or fr is not None:
            return float(s or 0) + float(fr or 0) / _fps0
        return float(x.get("end") or 0) / _fps0

    # Точки СМЕНЫ КАМЕРЫ (сек): где показываемая (верхняя включённая) камера меняется. Не каждый
    # VAD-стык внутри одной камеры, а именно переход кам1↔кам2. По ним режем/прижимаем вставки.
    _cam_change_sec = [f / _fps0 for f in _cam_change_frames(cams)]
    _snap = bool(st.get("insert_snap_cut", True))
    # Вставка, стартующая ВПРИТЫК перед катом, доигрывала бы вход на уходящем кадре и обрывалась
    # срезом. Прижимаем старт ровно к кату: вставка начинается уже на следующем кадре, вся анимация
    # входа идёт по нему (и стиль фото авто-выбирается по НОВОЙ камере). Конец не двигаем.
    SNAP_START_TOL = float(st.get("insert_snap_start", 0.35))   # сек до ката

    def _snap_start(x):
        if not _snap or SNAP_START_TOL <= 0:
            return
        t0, t1 = _ins_t0(x), _ins_t1(x)
        for cp in _cam_change_sec:
            if t0 < cp <= t0 + SNAP_START_TOL and cp < t1 - 1.5 / _fps0:
                x["start_s"], x["start_f"] = cp, 0.0        # старт = кат
                x["dur_s"], x["dur_f"] = t1 - cp, 0.0       # конец на месте
                x.pop("start", None)
                x.pop("end", None)
                x.pop("end_s", None)
                x.pop("end_f", None)
                break

    for x in inserts:
        _snap_start(x)

    # тип — ПО ФАЙЛУ, а не по тому, что просили у базы/ИИ: автоподбор мягкий (type_hint даёт
    # +0.05 к score, а не фильтрует), поэтому под «фото» прилетает mp4, а под «видео» — jpg.
    # А тип решает всё: фото = стоп-кадр с наездом, видео = футаж с переходом и whoosh.
    for x in inserts:
        if x.get("media"):
            x["type"] = "photo" if _is_image(x["media"]) else "video"

    # Вставок длиной в пару кадров в природе не бывает: старт прижимается к кату ещё на
    # создании, и `_snap_start` выше делает то же самое здесь. Если после среза катом окно
    # ВСЁ РАВНО схлопнулось — это не «короткая вставка», а НЕВЕРНЫЙ СТАРТ: её задумывали
    # в новом шоте, а поставили за миг до ката. Переносим старт на кат и играем задуманную
    # длительность там, где ей место. Делаем это ДО выбора стиля — иначе стиль фото
    # («из-за спины» / «наезд») считался бы по старой, уходящей камере.
    # ТОЛЬКО ФОТО-вставку (b-roll над головой), которая ПЕРЕХОДИТ на другую камеру, обрезаем ровно
    # в точке смены и без анимации выхода (не тянется через смену ракурса). ВИДЕО НЕ режем посреди
    # окна никогда — это полноэкранная вставка, играет весь свой хрон. Если стиль выключил snap —
    # и фото не режем. ОТДЕЛЬНО: вставка (фото И видео), чей конец лежит на точке смены камеры
    # (±SNAP_TOL — ИИ округляет тайминги до 0.1 c), считается СРЕЗАННОЙ катом: конец прижимаем к
    # кату, выхода нет (у видео это же убирает выходной переход+whoosh).
    SNAP_TOL = 0.12                                    # сек: конец «на кате» с учётом округления ИИ

    def _clip_end(x, t0, t1):                          # -> (end_sec, noexit)
        if _snap:
            for cp in _cam_change_sec:                 # конец вставки на самом кате -> жёсткий срез
                if abs(t1 - cp) <= SNAP_TOL and cp > t0 + 1.5 / _fps0:
                    return min(t1, cp), True           # не переползаем смену ракурса
            if (x.get("type") or "photo") == "photo":
                for cp in _cam_change_sec:             # первая смена камеры ВНУТРИ окна фото
                    if t0 + 1.5 / _fps0 < cp < t1 - 1.5 / _fps0:
                        return cp, True                # обрезать в точке смены, жёсткий срез
        return t1, False

    MIN_INS_SEC = 0.85                                 # вход 0.38 + выход 0.47: короче анимацию не уложить
    if _snap:
        for x in inserts:
            t0, t1raw = _ins_t0(x), _ins_t1(x)
            cut, _ne = _clip_end(x, t0, t1raw)
            if cut - t0 >= MIN_INS_SEC or t1raw - t0 < MIN_INS_SEC:
                continue                               # окно нормальное либо вставка и была короткой
            want = t1raw - t0                          # задуманная длительность
            nxt = [cp for cp in _cam_change_sec if cp > cut + 1.5 / _fps0]
            end = min(cut + want, nxt[0]) if nxt else cut + want
            if end - cut < MIN_INS_SEC:
                continue                               # и в новом шоте не помещается — оставляем как есть
            emit("  вставка {name}: старт {t0:.2f}с срезался катом до {cut_diff:.2f}с — перенёс на кат {cut:.2f}с (похоже, неверный тайминг начала — проверь на шаге разметки)",
                 name=os.path.basename(x.get('media') or '?'), t0=t0, cut_diff=cut - t0, cut=cut)
            x["start_s"], x["start_f"] = cut, 0.0
            x["dur_s"], x["dur_f"] = end - cut, 0.0
            for k in ("start", "end", "end_s", "end_f"):
                x.pop(k, None)

    _instyle = (st.get("insert_style") or "auto")      # стиль фотовставок: авто | cam1 | cam2
    for x in inserts:                                  # стиль фото: force cam1/cam2 или АВТО по активной камере
        if (x.get("type") or "photo") != "photo":
            continue
        _act = _active_cam_at(_ins_t0(x))
        if _instyle in ("cam1", "cam2"):
            sstyle = _instyle
        else:                                          # auto: над кам1 → «из-за спины», над перебивкой → cam2
            sstyle = "cam1" if _act == 0 else "cam2"
        x["style"] = sstyle
        # стиль «Кам 1» выставлен принудительно, а в кадре перебивка: в JSX такая вставка вешается
        # на отдельный нул (без зума Камеры 1, которой в кадре нет) и сдвигается общими INS_C1_ON2_X/Y
        x["oncam2"] = bool(sstyle == "cam1" and _act != 0)
        # масштаб считаем по пропорциям картинки (см. _ins_scale), но РУЧНОЙ уже проставленный
        # scale не затираем — иначе правка из webui умирала на каждой пересборке
        if not x.get("scale_manual"):
            x["scale"] = _ins_scale(x.get("media"), sstyle)
    hl_raw = set(int(x) for x in (highlights or []) if 0 <= int(x) < len(subs))
    brk_raw = set(int(x) for x in (hl_breaks or []) if 0 <= int(x) < len(subs))
    cnt_raw = set(int(x) for x in (hl_count or []) if 0 <= int(x) < len(subs))
    joins_raw = set(int(x) for x in (hl_joins or []) if 0 <= int(x) < len(subs))
    joins_raw = joins_raw - brk_raw
    sub_words_per_row = max(1, int(st.get("sub_words_per_row") or 1))
    sub_rows_max = max(1, int(st.get("sub_rows_max") or 1))
    eff_intro = [] if sub_words_per_row > 1 else (intro or [])
    eff_intro_remove = [] if sub_words_per_row > 1 else (intro_remove or [])
    eff_intro_splits = [] if sub_words_per_row > 1 else (intro_splits or [])
    remove = set(int(i) for i in eff_intro_remove if 0 <= int(i) < len(subs))
    censor_source = list(subs)                    # цензор считаем по ВСЕМ словам (интро-слова звучат)
    if remove:                                   # слова интро убираем из титров, хайлайты переиндексируем
        keep = [k for k in range(len(subs)) if k not in remove]
        remap = {old: new for new, old in enumerate(keep)}
        subs = [subs[k] for k in keep]
        hl = set(remap[k] for k in hl_raw if k in remap)
        brk = set(remap[k] for k in brk_raw if k in remap)
        cnt = set(remap[k] for k in cnt_raw if k in remap)
        joins = set(remap[k] for k in joins_raw if k in remap)
    else:
        hl = hl_raw
        brk = brk_raw
        cnt = cnt_raw
        joins = joins_raw
    rows, gend = _stack_layout(subs, hl, brk, joins)
    base = base or _project_base(xml_path)
    from core import assets as _assets
    # assets live next to the XML's project OR in assets/ next to the Reelsi install.
    # This matters when the edited sequence is exported to some other folder.
    asset_base = base
    if not os.path.isfile(os.path.join(base, "assets", "assets.json")):
        alt = os.path.dirname(HERE)
        if os.path.isfile(os.path.join(alt, "assets", "assets.json")):
            asset_base = alt
    aset = _assets.resolver(asset_base)
    font_ps = st.get("font") or "SFPro-CondensedSemibold"  # st уже резолвнут выше
    hl_font_ps = st.get("hl_font") or font_ps
    intro_font_ps = st.get("intro_font") or font_ps        # шрифты интро: пусто = как субтитры
    intro_hl_font_ps = st.get("intro_hl_font") or hl_font_ps
    # Акцентный шрифт строк интро (задание R): PostScript-имя; пусто = выключено.
    # Значение живой в стиле, в шаблон и план едет через данные строки (accent_font).
    accent_font_ps = (st.get("accent_font") or "").strip()
    accent_case = (st.get("accent_case") or "title").strip()
    back_font_ps = (st.get("back_font") or "").strip()
    back_case = (st.get("back_case") or "lower").strip()
    # Цвета и тень текста интро (новые ключи стиля): третий цвет строки (color=="accent"),
    # свой цвет обычного/выделенного текста интро (intro_fill/intro_hl_fill, None = как
    # сегодня) и пресет тени на КАЖДОМ слове интро (intro_shadow). Дефолты не меняют .jsx
    # ни на байт (golden) — см. _intro_fill_pick/_accent_color_used/_custom_color_used ниже.
    hl_fill3 = st.get("hl_fill3")
    intro_fill = st.get("intro_fill")
    intro_hl_fill = st.get("intro_hl_fill")
    intro_shadow_on = bool(st.get("intro_shadow"))
    intro_shadow_op = float(st.get("intro_shadow_op") if st.get("intro_shadow_op") is not None else 116.0)
    intro_shadow_dir = float(st.get("intro_shadow_dir") if st.get("intro_shadow_dir") is not None else 16.0)
    intro_shadow_dist = float(st.get("intro_shadow_dist") if st.get("intro_shadow_dist") is not None else 6.8)
    intro_shadow_soft = float(st.get("intro_shadow_soft") if st.get("intro_shadow_soft") is not None else 34.0)
    back_shadow_op = float(st.get("back_shadow_op") if st.get("back_shadow_op") is not None else 131.0)
    back_shadow_soft = float(st.get("back_shadow_soft") if st.get("back_shadow_soft") is not None else 38.0)
    # Тень ПРЕКОМПА интро (задание B): у камеры 1 и камеры 2 свои цвет/непрозрачность
    # (ключи стиля intro_comp_shadow*). Дефолты — прежняя белая тень dropShadow(iL, 68):
    # при всех четырёх дефолтах .jsx остаётся прежним байт в байт (golden).
    intro_comp_shadow_fill = [float(v) for v in (st.get("intro_comp_shadow_fill") or [1, 1, 1])]
    intro_comp_shadow_op = float(
        st.get("intro_comp_shadow_op") if st.get("intro_comp_shadow_op") is not None else 68.0)
    intro_comp_shadow2_fill = [float(v) for v in (st.get("intro_comp_shadow2_fill") or [1, 1, 1])]
    intro_comp_shadow2_op = float(
        st.get("intro_comp_shadow2_op") if st.get("intro_comp_shadow2_op") is not None else 68.0)
    back_step = float(st.get("back_step") if st.get("back_step") is not None else 0.45)
    if abs(back_step - 0.75) < 1e-4:
        back_step = 0.45
    back_scale = float(st.get("back_scale") if st.get("back_scale") is not None else 0.69)
    # Зазор между буквами соседних строк интро (задание A1): шаг ДО строки заднего плана
    # не меньше «хвост вниз верхней строки + высота букв нижней + back_gap». Числа даёт
    # fonts.ink_extent; зазор стиля — дефолт 4 px, как раздвинул строки пользователь в AE.
    back_gap = float(st.get("back_gap") if st.get("back_gap") is not None else 4.0)
    # Спад и полка прекомпов интро с эффектами (ПРАВКА 4): ключи стиля рядом с back_step.
    # Дефолты совпадают с эталоном 1421 — встроенные стили без правок их не меняют.
    intro_fx_fade = float(st.get("intro_fx_fade") if st.get("intro_fx_fade") is not None else 0.45)
    intro_fx_fade_last = float(st.get("intro_fx_fade_last") if st.get("intro_fx_fade_last") is not None else 0.35)
    intro_fx_hold_add = float(st.get("intro_fx_hold_add") if st.get("intro_fx_hold_add") is not None else 0.3)
    # Размытие на старте и хвостовой дисклеймер (задание S): дефолты = выключено,
    # при них плейсхолдеры шаблона пусты и .jsx не меняется ни на байт (golden).
    start_blur = float(st.get("start_blur") or 0)
    start_blur_dur = float(st.get("start_blur_dur") or 0.52)
    disc_end_on = bool(st.get("disclaimer_end")) and bool(disclaimer)
    # Макет спикера (задание Q): точка наезда камеры, точка покоя вставок Кам2 и сдвиг
    # интро по X. Дефолты = сегодняшнее поведение (0.5/0.5, 0.5/0.172, 0), при них
    # плейсхолдеры шаблона пусты и .jsx не меняется ни на байт (golden).
    cam1_cx = float(st.get("cam1_zoom_cx") if st.get("cam1_zoom_cx") is not None else 0.5)
    cam1_cy = float(st.get("cam1_zoom_cy") if st.get("cam1_zoom_cy") is not None else 0.5)
    ins_c2x = float(st.get("insert_c2_x") if st.get("insert_c2_x") is not None else 0.5)
    ins_c2y = float(st.get("insert_c2_y") if st.get("insert_c2_y") is not None else INS_C2_Y_FR)
    # Общий сдвиг точки покоя вставок кам1 (задание CB), px. Дефолт 0/0 = как сегодня.
    ins_c1x = float(st.get("insert_c1_x") or 0)
    ins_c1y = float(st.get("insert_c1_y") or 0)
    intro_x_px = float(st.get("intro_x") or 0)
    hl_fill = st.get("hl_fill")
    # Регистр и цвет базовых субтитров (задание CO): регистр применяется в scene_plan к
    # ГОТОВОМУ тексту (и .jsx, и превью читают его — второй копии правила нет), цвет
    # уезжает в план для превью и в шаблон как параметр FILL. Дефолты upper/белый —
    # подстановки пустые, .jsx прежний (golden).
    sub_case = (st.get("sub_case") or "upper").strip()
    sub_fill = st.get("sub_fill")
    # интро разбиваем на прекомпы по splits (индексы строк-начал новых групп)
    _intro_lines = [x for x in eff_intro if (x.get("words") or (x.get("text") or "").strip())]
    if sub_words_per_row > 1:
        _intro_groups = []
    else:
        _splits = sorted(set(int(s) for s in eff_intro_splits if 0 < int(s) < len(_intro_lines)))
        _bounds = [0] + _splits + [len(_intro_lines)]
        _intro_groups = [_intro_lines[_bounds[k]:_bounds[k + 1]] for k in range(len(_bounds) - 1)]

    # группы идут по таймингу: первая = самая ранняя (JS считает её началом ролика и держит её с 0)
    def _g_at(g):
        ts = [t for x in g for t in (x.get("times") or [])]
        return min(ts) if ts else 0.0
    _intro_groups.sort(key=_g_at)
    _any_glitch = any(x.get("anim") == "glitch" for g in _intro_groups for x in g)
    # Строки «заднего плана» в ролике (задание A1): от этого зависит и ветка раскладки
    # строк в шаблоне, и то, считает ли Python шаги по высоте букв. Акцентная строка
    # задним планом не считается — у неё свой шрифт и свой регистр (тот же приоритет,
    # что в _intro_line_js: accent перебивает back).
    _any_back = any(bool(x.get("back") and not x.get("accent")) for g in _intro_groups for x in g)
    _glitch_word_times = []
    if _any_glitch:
        for _grp in _intro_groups:
            for _ln in _grp:
                if _ln.get("anim") == "glitch":
                    _wds = [str(_w) for _w in (_ln.get("words") or (_ln.get("text") or "").split())]
                    _tms = list(_ln.get("times") or [])
                    for _wi in range(len(_wds)):
                        _wt = float(_tms[_wi]) if _wi < len(_tms) and _tms[_wi] is not None else 0.0
                        if _wt < 0:
                            _wt = 0.0
                        _glitch_word_times.append(_wt)
    # Звук глитча ставится на ГРУППУ слов, а не на слово (ПРАВКА 1): подряд идущие
    # глитч-слова накрыты ОДНИМ растянутым звуком, своя группа начинается там, где
    # следующее слово начинается НЕ раньше конца звука текущей группы (последнее слово
    # группы + полка + спад + кадр). Границы прекомпов при группировке не учитываются —
    # только время: идём по глитч-словам в порядке возрастания.
    _glitch_sound_groups = []
    if _glitch_word_times:
        _frame = 1.0 / _fps0
        for _t in sorted(float(t) for t in _glitch_word_times):
            if (_glitch_sound_groups
                    and _t < _glitch_sound_groups[-1][-1] + GLITCH_SFX_HOLD_S
                    + GLITCH_SFX_RELEASE_S + _frame):
                _glitch_sound_groups[-1].append(_t)
            else:
                _glitch_sound_groups.append([_t])
    # Группа, которая появляется на перебивке, вешается в JSX на отдельный нул «интро на кам2»
    # (там другой кадр — текст за спиной ставят ниже). Камеру считаем по БОЛЬШИНСТВУ окна
    # группы, а не по моменту появления первого слова: группа живёт gMax+0.3+1.0+0.75 секунд,
    # и кат через 40 мс после первого слова уводил её на нул камеры 1, хотя почти всё время
    # она висит над кадром камеры 2. Ничья (ровно 50/50) — ПОЗДНЕЙ камере: группа доигрывает
    # на ней, и глаз запоминает конец. Показ камер по времени — тот же источник, что camAt в JSX.
    _intro_show_segs = [(a / _fps0, b / _fps0, ci) for a, b, ci in _show_segments(cams)]

    def _intro_on2_at(ts, te):
        win = te - ts
        if win <= 0:
            return 1 if _active_cam_at(ts) != 0 else 0
        dur_c1 = 0.0
        last_ci = 0
        for a, b, ci in _intro_show_segs:
            lo, hi = max(a, ts), min(b, te)
            if lo < hi:
                if ci == 0:
                    dur_c1 += hi - lo
                last_ci = ci
        non_c1 = win - dur_c1
        if non_c1 > win / 2:
            return 1
        if non_c1 < win / 2:
            return 0
        return 1 if last_ci != 0 else 0
    _intro_on2 = []
    _intro_front = []
    _intro_above_roto = []               # галка «интро над рото по положению» (задание C)
    _intro_anchor = []                   # якорь блока на группу: center | first (задание A1)
    _intro_ly = []                       # Y базовых линий строк на группу — в .jsx как INTRO_LY
    riser = aset("intro_riser") if intro_riser else ""
    # Свой файл ризера (задание AA): строка в стиле перекрывает ассет-дефолт.
    _riser_file = (st.get("intro_riser_file") or "").strip()
    if _riser_file and intro_riser:
        riser = _riser_file
    pop = (_asset_or(st.get("pop"), "highlight_pop", aset)) if hl else ""
    glitch_asset = (_asset_or(st.get("glitch"), "glitch", aset)) if _any_glitch else ""
    glitch_db = float(st.get("glitch_db") if st.get("glitch_db") is not None else 0.0)
    has_video = any((x.get("type") or "photo") == "video" for x in inserts)
    trans = _asset_or(st.get("transition"), "transition", aset) if has_video else ""
    trans_sfx = _asset_or(st.get("transition_sfx"), "whoosh", aset) if has_video else ""
    # Звуки с обрезкой / точкой удара / громкостью (задание AA). Плоские ключи стиля:
    # <звук>_in/_out/_at/_db (+ pop_lead в кадрах для «попа»). Дефолты = прежнее
    # поведение: .jsx не меняется ни на байт (golden). Формула: слой ставится так,
    # чтобы точка `at` файла попала на момент события (жёлтое слово / кат / старт).
    def _g(v, d):
        return v if v not in (None, "") else d
    def _sfx_cfg(prefix, base_db, def_out):
        return dict(in_s=float(_g(st.get(prefix + "_in"), 0)),
                    out_s=(float(st[prefix + "_out"]) if st.get(prefix + "_out")
                           not in (None, "") else None),
                    at_s=float(_g(st.get(prefix + "_at"), 0)),
                    db=float(_g(st.get(prefix + "_db"), 0)),
                    base=base_db, def_out=def_out)
    pop_cfg = _sfx_cfg("pop", -8.0, 0.1)          # поп по умолчанию обрезан до ~0.1с
    wsfx_cfg = _sfx_cfg("transition_sfx", -10.0, None)
    riser_cfg = _sfx_cfg("intro_riser", 0.0, None)
    trans_cfg = _sfx_cfg("transition", 0.0, None)
    pop_lead = int(_g(st.get("pop_lead"), 4)) or 0
    # Звук задан «как вчера» (все ключи дефолтные) — шаблон работает прежним кодом.
    def _plain(cfg, lead):
        return (cfg["in_s"] == 0 and cfg["at_s"] == 0 and cfg["db"] == 0
                and cfg["out_s"] is None and lead == 4)
    pop_plain = _plain(pop_cfg, pop_lead)
    wsfx_plain = _plain(wsfx_cfg, 4)
    riser_plain = _plain(riser_cfg, 4)
    trans_plain = _plain(trans_cfg, 4)
    def _sfx_off(cfg):
        """Сдвиг старта: момент_события − (at − in), как "+0.2" / "-0.3" / "". """
        off = -(cfg["at_s"] - cfg["in_s"])
        return "%+g" % off if abs(off) > 1e-9 else ""
    def _sfx_tail(cfg, var):
        """JS-хвост после startTime: inPoint / outPoint / громкость. var — имя слоя.
        out не задан — базовая обрезка звука (def_out), как было (поп 0.1)."""
        tail = ""
        if abs(cfg["in_s"]) > 1e-9:
            tail += "\n            try{ %s.inPoint=%s.startTime+%g; }catch(e){}" % (var, var, cfg["in_s"])
        if cfg["out_s"] is not None:
            tail += "\n            try{ %s.outPoint=%s.startTime+%g; }catch(e){}" % (var, var, cfg["out_s"])
        elif cfg["def_out"] is not None:
            tail += "\n            try{ %s.outPoint=%s.startTime+%g; }catch(e){}" % (var, var, cfg["def_out"])
        if abs(cfg["db"]) > 1e-9:
            lv = cfg["base"] + cfg["db"]
            tail += ("\n            try{ %s.property(\"ADBE Audio Group\").property(\"ADBE Audio Levels\")"
                     ".setValue([%g,%g]); }catch(e){}") % (var, lv, lv)
        return tail
    # Токены для шаблона: при дефолтах — прежние JS-строки (.jsx не меняется).
    pop_place = ("pl.startTime=(SUBS[pi][0]+%d)/FPS%s;" % (pop_lead, _sfx_off(pop_cfg))
                 if not pop_plain
                 else "pl.startTime=(SUBS[pi][0]+4)/FPS;  // +4 кадра — звук лучше ложится")
    pop_tail = (_sfx_tail(pop_cfg, "pl") if not pop_plain
                else "try{ pl.outPoint=pl.startTime+0.1; }catch(e){}          // поп обрезан до ~0.1с\n            "
                     "try{ pl.property(\"ADBE Audio Group\").property(\"ADBE Audio Levels\").setValue([-8,-8]); }catch(e){}")
    wsfx_place = ("wl.startTime=cut-TR_IN-TR_SFX_LEAD%s;" % _sfx_off(wsfx_cfg)
                  if not wsfx_plain else "wl.startTime=cut-TR_IN-TR_SFX_LEAD;")
    wsfx_tail = (_sfx_tail(wsfx_cfg, "wl") if not wsfx_plain
                 else "try{ wl.property(\"ADBE Audio Group\").property(\"ADBE Audio Levels\").setValue([-10,-10]); }catch(e){}")
    riser_place = ("rl.startTime=%s;" % (_sfx_off(riser_cfg).lstrip("+") or "0") if not riser_plain else "rl.startTime=0;")
    riser_tail = _sfx_tail(riser_cfg, "rl") if not riser_plain else ""
    trans_place = ("tl.startTime=cut-TR_IN%s;" % _sfx_off(trans_cfg)
                   if not trans_plain else "tl.startTime=cut-TR_IN;")
    trans_tail = _sfx_tail(trans_cfg, "tl") if not trans_plain else ""
    # Звуки в ПЛАН СЦЕНЫ (превью читает их, задание AB): события с ГОТОВЫМ стартом —
    # t (монтажное время, когда звук начинает играть = ev − at + in), файловые in/out.
    # JS старт не пересчитывает (задание AB): берёт числа из плана.
    def _sfx_ev(ev, cfg):
        return {"t": round(ev - cfg["at_s"] + cfg["in_s"], 3),
                "in": cfg["in_s"], "out": cfg["out_s"]}
    _hl_events = [s / _fps0 for k, (s, e, w) in enumerate(subs) if k in hl] if hl else []
    sfx_plan = []
    if pop and _hl_events:
        sfx_plan.append({"kind": "pop", "media": pop,
                         "events": [_sfx_ev(t + pop_lead / _fps0, pop_cfg)
                                    for t in _hl_events],
                         "db": pop_cfg["db"], "base": pop_cfg["base"]})
    if trans_sfx:
        sfx_plan.append({"kind": "whoosh", "media": trans_sfx,
                         "events": [_sfx_ev(c - 0.386 - 0.083, wsfx_cfg)
                                    for c in _cam_change_sec],
                         "db": wsfx_cfg["db"], "base": wsfx_cfg["base"]})
    if trans:
        sfx_plan.append({"kind": "transition", "media": trans,
                         "events": [_sfx_ev(c - 0.386, trans_cfg)
                                    for c in _cam_change_sec],
                         "db": trans_cfg["db"], "base": trans_cfg["base"]})
    if riser:
        sfx_plan.append({"kind": "riser", "media": riser,
                         "events": [_sfx_ev(0.0, riser_cfg)],
                         "db": riser_cfg["db"], "base": riser_cfg["base"]})
    if glitch_asset and _glitch_word_times:
        sfx_plan.append({"kind": "glitch", "media": glitch_asset,
                         "events": [{"t": round(max(0.0, t - 0.3), 3),
                                     "in": 0.0, "out": 1.05}
                                    for t in _glitch_word_times],
                         "db": glitch_db, "base": 0.0})
    music_path = ""
    _mdir = music_dir or os.path.join(base, "music")   # папка музыки (по умолчанию рядом с XML)
    if music_random or music:
        _ckpt("музыка")
    if music_random:                                   # случайно из уже скачанных
        from core import ytmusic
        music_path = ytmusic.random_track(_mdir, emit=emit, seed=xml_path) or ""
    elif music:                                        # ссылка YouTube (скачать) или путь к файлу
        from core import ytmusic
        music_path = ytmusic.resolve(music, _mdir, emit=emit)
    cams_plan = []
    for ci, c in enumerate(cams):
        cams_plan.append({"ci": ci, "path": c["path"] or "", "name": c["name"],
                          "clips": [[s, e, i, o, bool(en), _r(sc)]
                                    for s, e, i, o, en, sc in c["clips"]]})
    cams_js = _jd([{"path": c["path"], "name": c["name"], "clips": c["clips"]} for c in cams_plan])
    # обрезаем конец слова по началу следующего, чтобы соседние (особ. мелкие «и/в») не накладывались
    def _endc(k):
        s, e, w = subs[k]
        ns = subs[k + 1][0] if k + 1 < len(subs) else None
        return min(e, ns) if (ns is not None and ns > s) else e

    _posy = int(meta["h"] * float(st.get("sub_y") or 0.5964))
    _hl_step = round(meta["h"] * 0.06224, 2)
    _hl_rise = round(meta["h"] * 0.06406, 2)
    _fsize = max(60, int(meta["w"] * 0.13))
    _sub_step = round(_fsize * 1.18, 2)
    # Масштаб СЛОЯ прекомпа субтитров (задание FE), %: кегль/раскладка не трогаются,
    # 100 = как сегодня. При 100 подстановка в шаблон пуста — .jsx прежний (golden).
    sub_scale = float(st.get("sub_scale") if st.get("sub_scale") is not None else 100.0)
    from core.subs import build_sub_rows
    from core import fonts as _fonts

    if sub_words_per_row <= 1:
        rows, gend = _stack_layout(subs, hl, brk, joins)
        max_line_w = 0.92 * meta["w"]
        # Регистр субтитров (задание CO): применяем к ГОТОВОМУ тексту в scene_plan — .jsx
        # и превью читают преобразованное, второй копии правила нет. upper (дефолт) —
        # слова из XML уже капсом, upper() их не меняет, .jsx прежний (golden). В
        # покадровом режиме каждое слово — своя реплика, sentence = Заглавная на каждом.
        def _sub_w(w):
            return _accent_word(w, "title" if sub_case == "sentence" else sub_case)
        subs_plan = []
        cnt_items = []
        any_sub_count = False
        for k, (s, e, w) in enumerate(subs):
            item_cnt = None
            if k in cnt:
                parsed = _parse_intro_count(_sub_w(w))
                if parsed is not None:
                    target, dec, expr, _ = parsed
                    item_cnt = [target, expr]
                    any_sub_count = True
            cnt_items.append(item_cnt)
            wd = _sub_w(w)
            ps = hl_font_ps if k in hl else font_ps
            w_px = _fonts.text_width(ps, w, _fsize)
            item = {
                "s": s / _fps0,
                "e": _endc(k) / _fps0,
                "w": wd,
                "color": "yellow" if k in hl else "white",
                "row": rows[k],
                "gend": gend[k] / _fps0,
                "repl": k,
            }
            if item_cnt is not None:
                item["cnt"] = item_cnt[0]
                item["expr"] = item_cnt[1]
            if w_px is not None and w_px > max_line_w:
                shrunk_fs = max(40, int(_fsize * max_line_w / w_px))
                if shrunk_fs < _fsize:
                    item["fsize"] = shrunk_fs
            subs_plan.append(item)

        any_joins = bool(joins)
        sub_tpl = SUBS_LOOP_WORDS_JOINED if any_joins else SUBS_LOOP_WORDS
        if any_sub_count:
            subs_js = _jd([[s, _endc(k), _sub_w(w), 1 if k in hl else 0, rows[k], gend[k], cnt_items[k]]
                           for k, (s, e, w) in enumerate(subs)])
            sub_count_code = (
                '\n        var cnt = sw[6];\n'
                '        if (cnt){\n'
                '            try{\n'
                '                var sl = addFX(L, "ADBE Slider Control");\n'
                '                if (sl){\n'
                '                    var slP = sl.property("ADBE Slider Control-0001");\n'
                '                    if (slP){\n'
                '                        slP.setValueAtTime(t0, 0);\n'
                '                        slP.setValueAtTime(t0 + HL_DUR, cnt[0]);\n'
                '                    }\n'
                '                }\n'
                '            }catch(e){}\n'
                '            try{\n'
                '                if (sp && cnt[1]){\n'
                '                    sp.expression = cnt[1];\n'
                '                }\n'
                '            }catch(e){}\n'
                '        }'
            )
            sub_loop = sub_tpl % dict(sub_count_code=sub_count_code)
        else:
            subs_js = _jd([[s, _endc(k), _sub_w(w), 1 if k in hl else 0, rows[k], gend[k]]
                           for k, (s, e, w) in enumerate(subs)])
            sub_loop = sub_tpl % dict(sub_count_code="")
        sub_rows_js = "[]"
    else:
        cut_bounds = set()
        for ci_cam in cams:
            for cl in ci_cam.get("clips", []):
                cut_bounds.add(int(cl[0]))
                cut_bounds.add(int(cl[1]))
        if word_timings is None and xml_path:
            from core.fileio import json_load_soft
            _w_sidecar = os.path.splitext(xml_path)[0] + ".words.json"
            if os.path.isfile(_w_sidecar):
                word_timings = json_load_soft(_w_sidecar)
        raw_lines = build_sub_rows(subs, per_row=sub_words_per_row, max_rows=sub_rows_max,
                                   cut_bounds=cut_bounds, word_timings=word_timings)
        max_line_w = 0.92 * meta["w"]

        # Подбор единого кегля на весь ролик (задание CK):
        # ширина строки = сумма ширин слов каждым своим шрифтом (базовый / hl_font) + пробелы
        reqs = []
        for line in raw_lines:
            words = line.get("words") or []
            if not words:
                continue
            spc = _fonts.text_width(font_ps, " ", _fsize)
            tot_w = 0.0
            meas_ok = True
            for wd in words:
                ps = hl_font_ps if wd.get("idx") in hl else font_ps
                ww = _fonts.text_width(ps, wd.get("w") or "", _fsize)
                if ww is None:
                    meas_ok = False
                    break
                tot_w += ww
            if meas_ok and len(words) > 1 and spc is not None:
                tot_w += (len(words) - 1) * spc
            if not meas_ok:
                req_fs = _fsize
            elif tot_w > max_line_w:
                req_fs = max(40, int(_fsize * max_line_w / tot_w))
            else:
                req_fs = _fsize
            reqs.append(req_fs)

        if reqs:
            _fsize = min(reqs)
        _sub_step = round(_fsize * 1.18, 2)

        # Регистр субтитров (задание CO): применяем к готовым словам — и план, и .jsx
        # строятся из преобразованного текста, второй копии правила нет. sentence —
        # «Как в предложении»: первое слово РЕПЛИКИ с заглавной, остальные строчные.
        # upper (дефолт) слова из XML не меняет, .jsx прежний (golden).
        repl_first = set()
        _first_r = set()
        for ln in raw_lines:
            r = ln["repl"]
            if r not in _first_r and ln.get("row") == 0 and ln.get("words"):
                _first_r.add(r)
                repl_first.add(ln["words"][0]["idx"])
        def _sub_w(w, idx):
            if sub_case == "sentence":
                return _accent_word(w, "title" if idx in repl_first else "lower")
            return _accent_word(w, sub_case)
        subs_plan = []
        for line in raw_lines:
            l_words = line["words"]
            t_words = [{"w": _sub_w(x["w"], x["idx"]),
                        "color": "yellow" if x["idx"] in hl else "white",
                        "s": x["start"] / _fps0, "e": x["end"] / _fps0} for x in l_words]
            all_hl = all(x["idx"] in hl for x in l_words)
            it = {
                "s": line["start"] / _fps0,
                "e": line["end"] / _fps0,
                "w": " ".join(x["w"] for x in t_words),
                "color": "yellow" if all_hl else "white",
                "row": line["row"],
                "gend": line["end"] / _fps0,
                "repl": line["repl"],
                "words": t_words,
            }
            subs_plan.append(it)

        subs_js = _jd([[s, _endc(k), _sub_w(w, k), 1 if k in hl else 0, 0, _endc(k)]
                       for k, (s, e, w) in enumerate(subs)])
        sub_rows_data = [
            [
                line["start"],
                line["end"],
                line["row"],
                0,
                [[x["start"], _sub_w(x["w"], x["idx"]), 1 if x["idx"] in hl else 0]
                 for x in line["words"]]
            ]
            for line in raw_lines
        ]
        sub_rows_js = _jd(sub_rows_data)
        sub_loop = SUBS_LOOP_ROWS % dict(sub_rows=sub_rows_js, sub_step=_sub_step)
    _c1zoom = (st.get("cam1_zoom") or "pulse")         # pulse = наезд с откатом | jump = резкие скачки | drift = скачок+плавный дрейф 100–160% | none = нет зума
    if cam1_scale is None:                             # авто-зум по сменам кам1→кам2
        if _c1zoom == "none":
            cam1_scale = [(0, 100.0)]
        else:
            _zstart = (st.get("cam1_zoom_start") is not False)
            _zbig = float(st.get("cam1_zoom_big") or ZOOM_BIG)
            _zlo = float(st.get("cam1_zoom_lo") or 112.0)
            _zhi = float(st.get("cam1_zoom_hi") or 140.0)
            if _c1zoom == "drift":
                _zdlo = float(st.get("cam1_drift_lo") or 100.0)
                _zdhi = float(st.get("cam1_drift_hi") or 160.0)
                cam1_scale = _cam1_drift_keys(cams, lo=_zdlo, hi=_zdhi, fps=meta["fps"], big=_zbig, start=_zstart)
            elif _c1zoom == "jump":
                cam1_scale = _cam1_jump_keys(cams, lo=_zlo, hi=_zhi, fps=meta["fps"], start=_zstart)
            else:
                cam1_scale = _cam1_zoom_keys(cams, big=_zbig, lo=_zlo, hi=_zhi, fps=meta["fps"], start=_zstart)
    # 3-й элемент (mode: 1=HOLD, 0=BEZIER) эмитим только если он есть (drift); 2-элементные — легаси
    cam1scale_js = _jd([([_r(f), _r(v)] + ([int(rest[0])] if rest else []))
                        for f, v, *rest in (cam1_scale or [])])
    # ease на каждый ключ зума: JS больше не смотрит соседей/режимы, а берёт готовые
    # [in, out] влияния из данных (задание B)
    cam1_ease_js = _jd(_zoom_key_eases(cam1_scale or []))
    _fps = meta["fps"]

    def _isec(x, k):                                   # поле «сек+кадры» -> секунды
        s, f = x.get(k + "_s"), x.get(k + "_f")
        if s is not None or f is not None:
            return float(s or 0) + float(f or 0) / _fps
        return float(x.get(k) or 0) / _fps             # легаси: целые кадры

    def _win(x):                                       # -> (start_sec, end_sec)
        st = _isec(x, "start")
        if x.get("dur_s") is not None or x.get("dur_f") is not None:  # старт + длительность
            return st, st + float(x.get("dur_s") or 0) + float(x.get("dur_f") or 0) / _fps
        return st, _isec(x, "end")                     # легаси: явный конец

    # _cam_change_sec и _snap посчитаны выше (там же прижимаем старт вставки к кату).
    # ТОЛЬКО ФОТО-вставку (b-roll над головой), которая ПЕРЕХОДИТ на другую камеру, обрезаем ровно
    # в точке смены и без анимации выхода (не тянется через смену ракурса). ВИДЕО НЕ режем посреди
    # окна никогда — это полноэкранная вставка, играет весь свой хрон. Если стиль выключил snap —
    # и фото не режем. ОТДЕЛЬНО: вставка (фото И видео), чей конец лежит на точке смены камеры
    # (±SNAP_TOL — ИИ округляет тайминги до 0.1 c), считается СРЕЗАННОЙ катом: конец прижимаем к
    # кату, выхода нет (у видео это же убирает выходной переход+whoosh).
    # SNAP_TOL и _clip_end определены ВЫШЕ (до выбора стиля): по ним же чинится
    # схлопнувшееся окно, а стиль должен считаться уже по исправленному старту.

    # видеовставка: перед человеком (фул на весь кадр, дефолт) или за ним (рото сверху)
    _vfront = bool(st.get("insert_video_front", True))

    def _front(x):
        return _vfront if x.get("front") is None else bool(x.get("front"))

    _insert_anim = (st.get("insert_anim") or "zoom").strip()

    def _ins_js(x):
        t0, t1raw = _win(x)
        t1, noexit = _clip_end(x, t0, t1raw)
        out = {"t": x.get("type") or "photo", "style": x.get("style") or "cam2",
               "media": x.get("media") or "", "start": _r(t0), "end": _r(t1),
               "scale": _r(x.get("scale") or 44), "mosaic": bool(x.get("mosaic")),
               "x": _r(x.get("x") or 0), "y": _r(x.get("y") or 0),
               # ручной масштаб, % от авто (фото — от карточки, видео — от заполнения кадра);
               # держим отдельно от scale, потому что scale у фото пересчитывается по картинке
               "sc": _r(x.get("sc") or 100),
               # форма маски-карточки, % от авторасчёта (100 = как считает JSX сам)
               "mw": _r(x.get("mw") or 100), "mh": _r(x.get("mh") or 100),
               "sin": _r(x.get("sin") or 0), "noexit": bool(noexit), "front": bool(_front(x)),
               "oncam2": bool(x.get("oncam2"))}
        # геометрия из Python (задание B): видео — масштаб заполнения и запас панорамы
        # (fit/slack — те же числа, что AE считал из item.width/height), фото — окна
        # входа/выхода cam2-анимации. Размеры не прочитались -> полей нет: вставку
        # не трогаем (старое if(!iw||!ih) return).
        if (x.get("type") or "photo") == "video":
            wh = _media_dims(x.get("media"))
            if wh:
                k = _r(x.get("sc") or 100) / 100               # округлённый sc: как увидит JSX
                iw, ih = wh
                out["fit"] = _r(_fit_scale(iw, ih, True, meta["w"], meta["h"], k))
                sx, sy = _fill_slack(iw, ih, meta["w"], meta["h"], k)
                out["slackx"], out["slacky"] = _r(sx), _r(sy)
                # коробка заполнения при sc=100 (px в comp): ужатому видео (sc<100) предпросмотр
                # рисует её × sc/100, не читая размеры файла (задание D)
                f0 = _fit_scale(iw, ih, True, meta["w"], meta["h"], 1.0) / 100
                out["fitw"], out["fith"] = _r(iw * f0, 2), _r(ih * f0, 2)
                if k >= 1:                          # кламп панорамы из JS: только у заполняющей вставки
                    out["x"] = _r(max(-sx, min(sx, float(x.get("x") or 0))))
                    out["y"] = _r(max(-sy, min(sy, float(x.get("y") or 0))))
        else:
            # маска-карточка в comp-координатах (осевший масштаб): её масштабирует anim.scale
            # (как Scale слоя в AE), и предпросмотру не нужны размеры картинки (задание D)
            _card = _ins_card(x.get("media"), out["style"], out.get("mw"), out.get("mh"),
                              out.get("sc"), meta["w"], meta["h"])
            if _card:
                out["card"] = _card
            # анимации вставок: готовые ключи вместо досчёта в ExtendScript (остаток задания B).
            # Те же округлённые t0/t1 и en/ex, что ушли в JSX, — предпросмотр интерполирует их же.
            t0r, t1r = _r(t0), _r(t1)
            if out["style"] == "cam2":
                S = float(out["scale"] or 44) * float(out["sc"] or 100) / 100
                if _insert_anim == "rise":           # задание DD: выезд снизу + рост + фейд, без блюра
                    en, ex = _ins_enter_exit(t0r, t1r, noexit, _fps0, enter=INS_RISE_ENTER, exit_=INS_EXIT)
                    out["en"], out["ex"] = _r(en), _r(ex)
                    enr, exr = _r(en), _r(ex)
                    win = max(0.0, t1r - t0r)
                    if win < 1 / float(_fps0):
                        sc_keys = [[t0r, _r(S)]]
                    else:
                        sc_keys = [[t0r, _r(S * INS_RISE_S0)], [_r(t0r + enr), _r(S)]]
                    ix, iy = float(out["x"] or 0), float(out["y"] or 0)
                    rest_x = round(meta["w"] * ins_c2x) + ix
                    rest_y = round(meta["h"] * ins_c2y) + iy
                    pos_start = [_r(rest_x), _r(rest_y + INS_RISE_DY)]
                    pos_end = [_r(rest_x), _r(rest_y)]
                    if win < 1 / float(_fps0):
                        pos_keys = [[t0r, pos_end]]
                    else:
                        pos_keys = [[t0r, pos_start], [_r(t0r + enr), pos_end]]
                    out["anim"] = {
                        "scale": sc_keys,
                        "position": pos_keys,
                        "opacity": _anim_keys(t0r, t1r, 0.0, 100.0, noexit, enr, exr, _fps0),
                    }
                elif _insert_anim == "none":         # задание FC: без анимации — слой просто есть
                    # один ключ на свойство: слой стоит от t0 в осевшем масштабе S и полной
                    # непрозрачности, вход/выход жёсткие; блюра и позиции нет вовсе
                    out["anim"] = {"scale": [[t0r, _r(S)]], "opacity": [[t0r, 100.0]]}
                else:                                # наезд от осевшего масштаба + opacity + блюр
                    en, ex = _ins_enter_exit(t0r, t1r, noexit, _fps0)  # от округлённых t0/t1, как JSX
                    out["en"], out["ex"] = _r(en), _r(ex)
                    enr, exr = _r(en), _r(ex)
                    pk = S * INS_C2_PEAK / INS_C2_BASE
                    out["anim"] = {"scale": _anim_keys(t0r, t1r, pk, S, noexit, enr, exr, _fps0),
                                   "opacity": _anim_keys(t0r, t1r, 0.0, 100.0, noexit, enr, exr, _fps0),
                                   "blur": _blur_keys(t0r, t1r, noexit)}
            else:                                    # cam1: вылет из-за спины (локально к нулу)
                en, ex = _ins_enter_exit(t0r, t1r, noexit, _fps0)
                out["en"], out["ex"] = _r(en), _r(ex)
                ix, iy = float(out["x"] or 0), float(out["y"] or 0)
                if out["oncam2"]:                    # общий сдвиг всех cam1-на-перебивке (INS_C1_ON2_X/Y)
                    ix += float(st.get("insert_c1on2_x") or 0)
                    iy += float(st.get("insert_c1on2_y") or 0)
                if _insert_anim == "none":           # задание FC: без анимации — сразу точка покоя
                    # up — точка ПОКОЯ из _cam1_pos_keys (layout.py), нижняя точка dn
                    # (за спиной) не строится вовсе: слой просто стоит на месте
                    out["anim"] = {"position": [[t0r, [_r(ins_c1x + ix),
                                                       _r(ins_c1y - INS_C1_HIGH + iy)]]]}
                else:                                # обычный вылет: подъём dn→up и спуск
                    # общий сдвиг точки покоя вставок кам1 (задание CB): парный к insert_c2_x/y,
                    # cx/cy _cam1_pos_keys и есть точка покоя — сдвиг считается здесь, в плане,
                    # и превью рисует готовое (правило одного источника)
                    out["anim"] = {"position": _cam1_pos_keys(t0r, t1r, noexit, ix, iy, _fps0,
                                                              cx=ins_c1x, cy=ins_c1y)}
        return out

    inserts_plan = [_ins_js(x) for x in inserts]
    inserts_js = _jd(inserts_plan)
    _video_segs = [(ins["start"], ins["end"]) for ins in inserts_plan if ins.get("t") == "video"]

    def _intro_front_at(ts, te):
        for vs, ve in _video_segs:
            if max(ts, vs) < min(te, ve):
                return 1
        return 0
    censor_windows = _censor_windows(censor_source, meta["fps"]) if censor_audio else []
    censor_js = _jd([[_r(a), _r(b)] for a, b in censor_windows])
    # разметка РОТО (дешёвое, без масок — их делает to_ae_full на GPU): сплошная копия
    # персонажа по видимой камере (EDL). Превью может опираться на те же фрагменты.
    # Выключенный ротоскоп — пустая разметка. Флаг тут не спрашивали, и полоса «здесь
    # рото» в предпросмотре оставалась гореть после «Авто-ротоскоп» выкл (жалоба
    # 2026-08-12). Соседняя строка про цензуру флаг спрашивает — здесь его забыли.
    roto_plan = [] if not roto else [
        p for p in _span_roto_plan(cams, 0, int(meta["dur"]), meta["fps"])
                 if cams[p["ci"]].get("path")]       # нужен исходник камеры
    def _intro_line_js(x):
        line = {"color": x.get("color") or "white",
                "words": [str(wd) for wd in (x.get("words") or (x.get("text") or "").split())],
                "times": [_r(t) for t in (x.get("times") or [])]}
        # Геометрия ГРУППЫ живёт на головной строке. В .jsx проносим только
        # ненулевое смещение и отличный от 100% масштаб: дефолт не меняет данные.
        _dx, _dy = float(x.get("gx") or 0), float(x.get("gy") or 0)
        if _dx or _dy:
            line["dx"] = _r(_dx)
            line["dy"] = _r(_dy)
        _gs = float(x.get("gs") or 100)
        if _gs != 100:
            line["ds"] = _r(_gs)
        # Акцентный шрифт (задание R): флаг живёт на СТРОКЕ рядом с color. Цвет строки
        # не меняется — акцент это ТРЕТЬЕ состояние слова (шрифт + регистр). Регистр
        # правится ЗДЕСЬ, в Python: и .jsx, и план получают готовый текст, второй
        # копии трансформации нет. Пустой accent_font = выключено: галка ничего не
        # делает (и это видно — строка остаётся как была).
        if x.get("accent"):
            if accent_font_ps:
                line["accent_font"] = accent_font_ps
                line["words"] = [_accent_word(wd, accent_case) for wd in line["words"]]
        elif x.get("back"):
            line["back"] = True
            if back_font_ps:
                line["accent_font"] = back_font_ps
            line["words"] = [_accent_word(wd, back_case) for wd in line["words"]]
        # Цвет интро (новые ключи стиля): color=="custom" несёт СВОЙ цвет строки в поле
        # fill [r,g,b] 0..1. Без fill строка color=="custom" рисуется как white (см.
        # _intro_fill_pick) — здесь просто ничего не кладём, JS сам подставит дефолт.
        if line["color"] == "custom":
            _cf = x.get("fill")
            if _cf:
                line["fill"] = [_r(v) for v in list(_cf)[:3]]
        if x.get("anim"):
            line["anim"] = str(x["anim"])
        if x.get("fx"):
            line["fx"] = str(x["fx"])
        if isinstance(x.get("cnt_words"), list):
            # Новый формат: счётчик на КАЖДОЕ слово-число, позиции слов — в cnt_words.
            # Скаляры cnt/expr/dec/cnt_idx остаются и равны ПЕРВОМУ счётчику: на них
            # стоит строчный режим (один текстовый слой = один счётчик) и старые тесты.
            wds = line["words"]
            cnts = []
            for _p in _intro_cnt_positions(x, len(wds)):
                parsed = _parse_intro_count(wds[_p], x.get("dec"))
                if parsed is not None:
                    target, dec, expr, _ = parsed
                    cnts.append([_p, _r(target), expr, dec])
            if cnts:
                line["cnts"] = cnts
                line["cnt"] = cnts[0][1]
                line["expr"] = cnts[0][2]
                line["dec"] = cnts[0][3]
                line["cnt_idx"] = cnts[0][0]
                line["is_count"] = True
            elif x.get("dec") is not None:
                line["dec"] = int(x["dec"])
        elif x.get("is_count") or x.get("anim") == "count":
            # Легаси: флаг на строку без позиций — счётчик на первом числе строки
            # (включая разбор всей строки целиком, когда отдельных чисел нет).
            # cnts даёт один элемент — он же скаляры ниже.
            wds = line["words"]
            cnts = []
            for wi, wd in enumerate(wds):
                parsed = _parse_intro_count(wd, x.get("dec"))
                if parsed is not None:
                    target, dec, expr, _ = parsed
                    cnts = [[wi, _r(target), expr, dec]]
                    break
            if not cnts:
                parsed = _parse_intro_count(" ".join(wds).strip(), x.get("dec"))
                if parsed is not None:
                    target, dec, expr, _ = parsed
                    cnts = [[0, _r(target), expr, dec]]
            if cnts:
                line["is_count"] = True
                line["cnts"] = cnts
                line["cnt"] = cnts[0][1]
                line["expr"] = cnts[0][2]
                line["dec"] = cnts[0][3]
                line["cnt_idx"] = cnts[0][0]
            elif x.get("dec") is not None:
                line["dec"] = int(x["dec"])
        elif x.get("dec") or (x.get("dec") is not None and x.get("dec") != ""):
            line["dec"] = int(x["dec"])
        return line
    # Новые цвета строки интро: считаем по СЫРЫМ данным групп, а не по _intro_line_js.
    # Используются, только если хоть одна строка в ЭТОЙ сборке реально просит accent/custom
    # (иначе плейсхолдеры шаблона пусты и .jsx не меняется ни на байт, golden).
    _accent_color_used = any(x.get("color") == "accent" for g in _intro_groups for x in g)
    _custom_color_used = any(x.get("color") == "custom" for g in _intro_groups for x in g)
    # Общий масштаб интро (intro_scale, задание BG): в AE он висит на нуле «интро»
    # (родителе прекомпа) и множит СМЕЩЕНИЕ ребёнка и его размер, а собственный сдвиг
    # нула (intro_y/intro_y2) не трогает. Поэтому G входит в базу (-INTRO_BASE_Y+iDy),
    # а intro_y/intro_y2 — плоским слагаемым. 100% = дефолт: y не меняется ни на сотую.
    _G = float(st.get("intro_scale") or 100) / 100
    # Кегль интро = кегль субтитров (в AE это один FONT_SIZE): тот же _fsize, что у
    # стопки ниже, — автофит меряет ширину строки тем же размером (задание BP).
    # окна групп (ts/te) — здесь, в плане; превью их не считает (задание D). По тем же
    # округлённым times, что ушли в .jsx, — иначе план и AE разойдутся на сотых.
    intro_plan = []
    intro_idy = []                       # готовые iDy для шаблона (задание Q2)
    # Статистика каждой группы по ГОТОВЫМ строкам (тем же, что уедут в .jsx): все моменты
    # слов и глитч-моменты. Окна фейд-аута прекомпов с глитчем (ПРАВКА 3/4) считаем один
    # раз — план (превью) и шаблон (INTRO_FX) получают одни и те же числа.
    _grp_stats = []
    for _grp in _intro_groups:
        _l = [_intro_line_js(x) for x in _grp]
        _l_tms = [t for _x in _l for t in _x["times"]]
        _l_gl = [t for _x in _l if _x.get("anim") == "glitch" for t in _x["times"]]
        _grp_stats.append((_l, _l_tms, _l_gl))
    _fx_fade = [None] * len(_grp_stats)   # спад прекомпа с глитчем (0.45/0.35), с
    _fx_os = [None] * len(_grp_stats)     # момент начала фейд-аута (полка кончилась), с
    _fx_oe = [None] * len(_grp_stats)     # конец спада = конец слоя прекомпа, с
    for _g, (_l0, _l_tms, _l_gl) in enumerate(_grp_stats):
        if not _l_gl:
            continue
        # ПРАВКА 3: полка прекомпа не наступает раньше конца анимации последнего
        # глитч-слова (момент слова + длительность анимации из INTRO_ANIMS). Формула
        # обычного окна — ровно та, что в AE_FULL (inAt/outStart), ПРАВКА её только
        # продлевает: прекомп без глитча не меняется ни на сотую.
        _gl_end = max(_l_gl) + INTRO_ANIMS["glitch"]["dur"]
        _gmin = min(_l_tms) if _l_tms else 0.0
        _gmax = max(_l_tms) if _l_tms else 0.0
        _in_at = 0.0 if (_g == 0 and _gmin < 3) else _gmin
        _ng = len(_grp_stats)
        _out_js = (_gmax + INTRO_F_DUR + INTRO_HOLD) if _g == _ng - 1 \
            else max(_gmax, _in_at + INTRO_F_DUR)
        _out_s = max(_out_js, _gl_end)
        # ПРАВКА 4: последний прекомп либо следующий начинается позже, чем через 2 с
        # после конца этого, — полка дополнительно держится на intro_fx_hold_add,
        # а спад ещё короче (intro_fx_fade_last вместо intro_fx_fade).
        _far = _g == _ng - 1
        if not _far and _g + 1 < _ng:
            _nx_tms = _grp_stats[_g + 1][1]
            _nx_in = min(_nx_tms) if _nx_tms else 0.0
            if _nx_in - (_out_s + intro_fx_fade) > 2.0:
                _far = True
        if _far:
            _out_s += intro_fx_hold_add
            _fx_fade[_g] = intro_fx_fade_last
        else:
            _fx_fade[_g] = intro_fx_fade
        _fx_os[_g] = _r(_out_s)
        _fx_oe[_g] = _r(_out_s + _fx_fade[_g])

    for _g, (_grp, (_lines, _tms, _l_gl)) in enumerate(zip(_intro_groups, _grp_stats)):
        _ts, _te = _intro_group_window(_tms, _g, len(_intro_groups))
        _fade = INTRO_F_OUT
        if _fx_fade[_g] is not None:
            _fade, _te = _fx_fade[_g], _fx_oe[_g]
        # окно (ts/te) уже посчитано — камера группы по большинству этого окна (BR),
        # а не по первому слову: иначе кат сразу после старта оставлял нул камеры 1.
        _on2 = _intro_on2_at(_ts, _te)
        _intro_on2.append(_on2)
        _front = _intro_front_at(_ts, _te)
        _intro_front.append(_front)
        # Якорь блока интро этой группы (задание A1): на перебивке свой ключ стиля —
        # группа висит на другом нуле (кам2) и «первая строка» там своя. "first" —
        # первая строка стоит на месте, остальные ложатся ниже.
        _anchor = str((st.get("intro_anchor2") if _on2 else st.get("intro_anchor")) or "center")
        _intro_anchor.append(_anchor)
        # Смещение ГРУППЫ (задание E): живёт на головной строке (первой в группе) и
        # добавляется к позиции прекомпа в шаблоне. После разрезания/слияния групп
        # оно остаётся у той строки, которая стала головной, — новая группа с чистой
        # головы получает 0/0. Сюда же дублируем в plan (предпросмотр двигает мышью).
        _dx = float(_grp[0].get("gx") or 0) if _grp else 0.0
        _dy = float(_grp[0].get("gy") or 0) if _grp else 0.0
        _gs = float(_grp[0].get("gs") or 100) if _grp else 100.0
        _ds = _gs
        # Базовая позиция блока интро (задание Q2): невзведённая (без зума) позиция по
        # вертикали от ЦЕНТРА кадра = INTRO_Y(+INTRO_Y2) − INTRO_BASE_Y + iDy. gDy НЕ
        # включаем — он уже живёт отдельным полем dy (задание E: драг правит dy в кэше
        # плана), а превью сложит y + dy. iDy (опускание под INTRO_SAFE_TOP) считает
        # Python — шаблон берёт готовое число, CSS-позиция блока в превью уходит.
        # От НЕУЖАТОГО масштаба (граница задания BP): автофит ниже режет только Scale,
        # а опускание блока от него не зависит. При якоре «first» блок по числу строк не
        # пересчитывается (задание A1): первая строка на месте, значит и центр блока —
        # как у одной строки, добавленные строки свисают вниз и верх не поднимают.
        _idy = _intro_i_dy(meta["h"], 1 if _anchor == "first" else len(_grp), _ds)
        # Автофит (задание BP / CF): применяется ТОЛЬКО если группу НЕ трогали руками
        # (_gs == 100). Если gs != 100 — пользователь явно задал масштаб рукой (рука
        # сильнее автофита), автофит не урезает его значение.
        if _gs == 100:
            _ds = _intro_fit_ds(_lines, _ts, _te, _ds, meta["w"], _G, cam1_scale,
                                meta["fps"], st, intro_font_ps, intro_hl_font_ps,
                                _fsize, hold=(_c1zoom == "jump"))
        # ds головной строки = готовое значение автофита: шаблон читает GRP[0].ds,
        # превью — plan.intro[].ds, второй копии расчёта нет.
        if _lines:
            if _ds != 100:
                _lines[0]["ds"] = _r(_ds)
            else:
                _lines[0].pop("ds", None)
        _y = round((st.get("intro_y") or 0) + _G * (-INTRO_BASE_Y + _idy), 2)
        if _on2:
            _y = round(_y + (st.get("intro_y2") or 0), 2)
        # Галка «интро над рото по положению» (задание C): группу Камеры 1, чей блок
        # от центра кадра в НИЖНЕЙ половине (зона субтитров), в .jsx поднимают над
        # рото; блок в верхней половине остаётся под ним. Зум камеры не учитываем —
        # он множит позицию и сам блок одинаково, знак суммы (_y + _G*_dy) не меняется.
        # Группы на перебивке (свой нул) и на видеовставке (им и так наверх) не трогаем.
        _above_roto = bool(st.get("intro_roto_by_pos")) and not _on2 and not _front \
            and (_y + _G * _dy) > 0
        _intro_above_roto.append(_above_roto)
        intro_idy.append(_idy)
        # Шрифт каждой строки считает Python (та же лесенка, что у автофита и .jsx) —
        # превью читает готовое и своей лесенки не держит. В сами строки (lines) поле не
        # кладём: они уезжают в .jsx как INTRO_GROUPS, и он обязан остаться прежним (golden).
        _line_fonts = [_intro_line_font(ln, intro_font_ps, intro_hl_font_ps) for ln in _lines]
        # Y базовых линий строк (задание A1): шаг знает высоту букв шрифта, а не только
        # жёсткие пиксели, и якорь блока. Считает Python — тем же числам едут и .jsx
        # (INTRO_LY), и превью. В строки (lines) поле не кладём: INTRO_GROUPS обязан
        # остаться прежним (golden).
        _ys = intro_line_ys(_lines, _line_fonts, _fsize, back_scale, back_step, back_gap,
                            _any_back, _anchor, meta["h"])
        _intro_ly.append(_ys)
        intro_plan.append({"group": _g, "on2": bool(_on2),
                           "ts": _ts, "te": _te, "fade": _r(_fade), "lines": _lines,
                           "dx": _dx, "dy": _dy, "ds": _ds, "y": _y, "ys": _ys,
                           # Тень прекомпа этой группы (задание B): цвет и непрозрачность
                           # ТОЙ камеры, на которой группа (_on2). Тем же числом живёт
                           # превью (filter: drop-shadow), второй копии выбора камеры нет.
                           "shadow": {"fill": (intro_comp_shadow2_fill if _on2
                                               else intro_comp_shadow_fill),
                                      "op": (intro_comp_shadow2_op if _on2
                                             else intro_comp_shadow_op)},
                           # Группа легла по времени на видеовставку: в AE её прекомп после
                           # раскладки уносит moveToBeginning НАД всем (template.py,
                           # intro_front_raise). Признак нужен и превью — без него слой интро
                           # оставался под видео: текст был закрыт картинкой и не хватался
                           # мышью, хотя в AE лежал сверху.
                           # В строки (lines) поле не кладём: они уезжают в .jsx как
                           # INTRO_GROUPS, и он обязан остаться прежним (golden).
                           "front": bool(_front),
                           # Галка «интро над рото в нижней половине» (задание C): блок
                           # группы ниже центра кадра на Камере 1 — прекомп в .jsx
                           # поднимается над рото (template.py, intro_above_roto_raise).
                           # В строки (lines) поле не кладём: они уезжают в .jsx как
                           # INTRO_GROUPS и обязаны остаться прежними (golden).
                           "above_roto": _above_roto,
                           # Шрифт каждой строки и Y базовых линий (готовые числа, задание A1):
                           # в сами строки (lines) их не кладём — они уезжают в .jsx как
                           # INTRO_GROUPS, и он обязан остаться прежним (golden).
                           "fonts": _line_fonts})
    # Готовые окна фейд-аута прекомпов с глитчем для шаблона (ПРАВКА 3/4): JS берёт
    # числа из плана, второй копии расчёта не заводится. Без глитча подстановка пустая —
    # .jsx прежний (golden).
    _intro_fx_decl = ""
    _intro_fx_out = ""
    if _any_glitch:
        _fx_js = _jd([None if _fx_os[_g] is None else [_fx_os[_g], _fx_oe[_g]]
                      for _g in range(len(_grp_stats))])
        _intro_fx_decl = ("\n    var INTRO_FX=%s;    // [группа] = [начало фейд-аута, конец слоя] прекомпа"
                          " с глитчем: числа из плана (ПРАВКА 3/4)" % _fx_js)
        _intro_fx_out = ("\n            if (INTRO_FX[gI]){ outStart=INTRO_FX[gI][0]; outEnd=INTRO_FX[gI][1]; }"
                         "  // ПРАВКА 3/4: прекомп с глитчем держит полку и гасится своими числами")
    # .jsx-группы — из ГОТОВОГО плана (автофит уже в ds), чтобы .jsx и превью не
    # разошлись на одном и том же значении.
    intro_groups_js = _jd([p["lines"] for p in intro_plan])
    # Акцент (задание R) или задний план: используются, только если хоть одна строка
    # реально получила accent_font. Иначе плейсхолдеры шаблона пусты и .jsx не меняется ни на байт (golden).
    _accent_used = any("accent_font" in ln for p in intro_plan for ln in p["lines"])
    # геометрия субтитров для предпросмотра: стопка живёт в comp-координатах, и JS не должен
    # досчитывать формулы из h (posy = sub_y*h, шаг = 0.06224*h — это контракт _ae ниже)
    _posy = int(meta["h"] * float(st.get("sub_y") or 0.5964))
    _hl_step = round(meta["h"] * 0.06224, 2)
    _hl_rise = round(meta["h"] * 0.06406, 2)
    # уход субтитров на вставках rise (задание DD): для каждой rise-вставки
    # субтитры скрываются [[t0,100],[t0+en,0],[t1-ex,0],[t1,100]]; окна внахлёст объединяются
    sub_hide = []
    if _insert_anim == "rise":
        rise_windows = []
        for xi in inserts_plan:
            if (xi.get("t") or "photo") == "photo" and xi.get("style") == "cam2":
                st_t = float(xi.get("start") or 0)
                en_t = float(xi.get("end") or 0)
                if en_t > st_t:
                    rise_windows.append((st_t, en_t, bool(xi.get("noexit"))))
        if rise_windows:
            merged_wins = []
            for st_t, en_t, ne in sorted(rise_windows, key=lambda w: (w[0], w[1])):
                if not merged_wins:
                    merged_wins.append([st_t, en_t])
                else:
                    if st_t <= merged_wins[-1][1]:
                        merged_wins[-1][1] = max(merged_wins[-1][1], en_t)
                    else:
                        merged_wins.append([st_t, en_t])
            for mw_s, mw_e in merged_wins:
                mw_noexit = any(ne for st_t, en_t, ne in rise_windows
                                if abs(en_t - mw_e) < 1e-5 and mw_s <= st_t < mw_e)
                if mw_noexit:
                    en_m, _ = _ins_enter_exit(mw_s, mw_e, True, _fps0, enter=INS_RISE_ENTER, exit_=INS_EXIT)
                    sub_hide.append([_r(mw_s), 100.0])
                    sub_hide.append([_r(mw_s + en_m), 0.0])
                    sub_hide.append([_r(mw_e), 0.0])
                    sub_hide.append([_r(mw_e + 1.0 / _fps0), 100.0])
                else:
                    en_m, ex_m = _ins_enter_exit(mw_s, mw_e, False, _fps0, enter=INS_RISE_ENTER, exit_=INS_EXIT)
                    sub_hide.append([_r(mw_s), 100.0])
                    sub_hide.append([_r(mw_s + en_m), 0.0])
                    sub_hide.append([_r(max(mw_s + en_m, mw_e - ex_m)), 0.0])
                    sub_hide.append([_r(mw_e), 100.0])
    # фон (плашка) под субтитрами (задание DE)
    # фон (плашка) под субтитрами (задание DE, обновлено DL)
    sub_comp_name = f"Субтитры ({meta['name']})" if meta.get("name") else "Субтитры (текст)"
    sub_bg_on = bool(st.get("sub_bg"))
    # Тень субтитров (задание DK): при включённой плашке собственная тень текста снимается
    sub_shadow = not sub_bg_on
    sub_shadow_js = (
        '    var ds = subLayer.property("ADBE Effect Parade").addProperty("ADBE Drop Shadow");\n'
        '    ds.property("ADBE Drop Shadow-0002").setValue(SH_OPACITY/100*255);  // Opacity (percent in UI -> 0..255)\n'
        '    ds.property("ADBE Drop Shadow-0003").setValue(SH_DIR);      // Direction\n'
        '    ds.property("ADBE Drop Shadow-0004").setValue(SH_DIST);     // Distance\n'
        '    ds.property("ADBE Drop Shadow-0005").setValue(SH_SOFT);     // Softness\n'
    ) if sub_shadow else ""
    sub_bg_js = ""
    sub_bg_plan = None
    if sub_bg_on:
        sub_bg_fill = list(st.get("sub_bg_fill") if st.get("sub_bg_fill") is not None else [1.0, 1.0, 1.0])
        sub_bg_op = float(st.get("sub_bg_op") if st.get("sub_bg_op") is not None else 72.0)
        sub_bg_h = float(st.get("sub_bg_h") if st.get("sub_bg_h") is not None else 160.0)
        sub_bg_round = float(st.get("sub_bg_round") if st.get("sub_bg_round") is not None else 78.0)
        sub_bg_pad = float(st.get("sub_bg_pad") if st.get("sub_bg_pad") is not None else 18.0)
        sub_bg_padmin = float(st.get("sub_bg_padmin") if st.get("sub_bg_padmin") is not None else 70.0)
        sub_bg_dy = float(st.get("sub_bg_dy") if st.get("sub_bg_dy") is not None else 0.0)
        sub_bg_anim = float(st.get("sub_bg_anim") if st.get("sub_bg_anim") is not None else 0.22)
        # центр = posy + (строк - 1) * sub_step / 2 - 0.27 * fsize + sub_bg_dy (задание DI)
        sub_bg_y = round(_posy + (sub_rows_max - 1) * _sub_step / 2.0 - 0.27 * _fsize + sub_bg_dy, 2)
        sub_bg_plan = {
            "fill": sub_bg_fill,
            "op": sub_bg_op,
            "h": sub_bg_h,
            "round": sub_bg_round,
            "pad": sub_bg_pad,
            "padmin": sub_bg_padmin,
            "dy": sub_bg_dy,
            "y": sub_bg_y,
            "anim": sub_bg_anim,
            "layer": sub_comp_name,
        }
        sub_bg_expr_code = _sub_bg_expr(st, sub_layer_name=sub_comp_name)
        sub_bg_hide = [[k[0], (sub_bg_op if k[1] > 0 else 0.0)] for k in sub_hide]
        sub_bg_js = (
            "\n    // ---- фон (плашка) под субтитрами (задание DE) ----\n"
            "    var bgLayer = main.layers.addShape();\n"
            '    bgLayer.name = "Фон субтитров";\n'
            '    var bgContents = bgLayer.property("ADBE Root Vectors Group");\n'
            '    var bgRect = bgContents.addProperty("ADBE Vector Shape - Rect");\n'
            f'    bgRect.property("ADBE Vector Rect Roundness").setValue({sub_bg_round:g});\n'
            f'    bgRect.property("ADBE Vector Rect Size").expression = {_jd(sub_bg_expr_code)};\n'
            '    var bgFill = bgContents.addProperty("ADBE Vector Graphic - Fill");\n'
            f'    bgFill.property("ADBE Vector Fill Color").setValue({_fill_js(sub_bg_fill)});\n'
            f'    bgLayer.property("ADBE Transform Group").property("ADBE Position").setValue([W/2, {sub_bg_y:g}]);\n'
            f'    bgLayer.property("ADBE Transform Group").property("ADBE Opacity").setValue({sub_bg_op:g});\n'
            '    var bgDs = bgLayer.property("ADBE Effect Parade").addProperty("ADBE Drop Shadow");\n'
            '    bgDs.property("ADBE Drop Shadow-0001").setValue([0,0,0]);\n'
            f'    bgDs.property("ADBE Drop Shadow-0002").setValue({SUB_BG_SH_OP:g});\n'
            f'    bgDs.property("ADBE Drop Shadow-0003").setValue({SUB_BG_SH_DIR:g});\n'
            f'    bgDs.property("ADBE Drop Shadow-0004").setValue({SUB_BG_SH_DIST:g});\n'
            f'    bgDs.property("ADBE Drop Shadow-0005").setValue({SUB_BG_SH_SOFT:g});\n'
            + (f'    var SUB_BG_HIDE = {_jd(sub_bg_hide)};\n'
               '    if (SUB_BG_HIDE.length){\n'
               '        applyKeyframes(bgLayer.property("ADBE Transform Group").property("ADBE Opacity"), SUB_BG_HIDE);\n'
               '    }\n' if sub_bg_hide else '')
            + '    subLayers = [bgLayer, subLayer];\n'
        )
    # верхняя строка-прогресс (задание DF)
    top_line_on = bool(st.get("top_line"))
    top_line_js = ""
    top_line_plan = None
    if top_line_on:
        top_line_y = float(st.get("top_line_y") if st.get("top_line_y") is not None else 162.0)
        top_line_w = float(st.get("top_line_w") if st.get("top_line_w") is not None else 969.0)
        top_line_th = float(st.get("top_line_th") if st.get("top_line_th") is not None else 12.5)
        top_line_from = list(st.get("top_line_from") if st.get("top_line_from") is not None else [0.984, 1.0, 0.541])
        top_line_to = list(st.get("top_line_to") if st.get("top_line_to") is not None else [1.0, 0.698, 0.988])
        top_line_track_fill = list(st.get("top_line_track_fill") if st.get("top_line_track_fill") is not None else [1.0, 1.0, 1.0])
        top_line_track_op = float(st.get("top_line_track_op") if st.get("top_line_track_op") is not None else 16.0)
        top_line_plan = {
            "y": top_line_y,
            "w": top_line_w,
            "th": top_line_th,
            "from": top_line_from,
            "to": top_line_to,
            "track_fill": top_line_track_fill,
            "track_op": top_line_track_op,
            "dur": meta["dur"] / meta["fps"],
        }
        top_line_r = top_line_th / 2.0
        tl_w_int = max(1, int(round(top_line_w)))
        tl_th_int = max(1, int(round(top_line_th)))
        tl_mask_r = tl_th_int / 2.0
        top_line_js = (
            "\n    // ---- верхняя строка-прогресс (задание DF) ----\n"
            "    function _tlShape(x0, y0, x1, y1, r){\n"
            "        r=Math.min(r,(x1-x0)/2,(y1-y0)/2); var k=r*0.5523;\n"
            "        var sh=new Shape(); sh.closed=true;\n"
            "        sh.vertices   =[[x0+r,y0],[x1-r,y0],[x1,y0+r],[x1,y1-r],[x1-r,y1],[x0+r,y1],[x0,y1-r],[x0,y0+r]];\n"
            "        sh.inTangents =[[-k,0],[0,0],[0,-k],[0,0],[k,0],[0,0],[0,k],[0,0]];\n"
            "        sh.outTangents=[[0,0],[k,0],[0,0],[0,k],[0,0],[-k,0],[0,0],[0,-k]];\n"
            "        return sh;\n"
            "    }\n"
            "    var tlTrack = main.layers.addShape();\n"
            '    tlTrack.name = "Строка (дорожка)";\n'
            '    var tlTrackContents = tlTrack.property("ADBE Root Vectors Group");\n'
            '    var tlTrackRect = tlTrackContents.addProperty("ADBE Vector Shape - Rect");\n'
            f'    tlTrackRect.property("ADBE Vector Rect Size").setValue([{top_line_w:g}, {top_line_th:g}]);\n'
            f'    tlTrackRect.property("ADBE Vector Rect Roundness").setValue({top_line_r:g});\n'
            '    var tlTrackFill = tlTrackContents.addProperty("ADBE Vector Graphic - Fill");\n'
            f'    tlTrackFill.property("ADBE Vector Fill Color").setValue({_fill_js(top_line_track_fill)});\n'
            f'    tlTrack.property("ADBE Transform Group").property("ADBE Position").setValue([W/2, {top_line_y:g}]);\n'
            f'    tlTrack.property("ADBE Transform Group").property("ADBE Opacity").setValue({top_line_track_op:g});\n'
            f'    var tlProg = main.layers.addSolid([1,1,1], "Строка (прогресс)", {tl_w_int}, {tl_th_int}, 1);\n'
            f'    tlProg.property("ADBE Transform Group").property("ADBE Position").setValue([W/2, {top_line_y:g}]);\n'
            '    var tlRamp = tlProg.property("ADBE Effect Parade").addProperty("ADBE Ramp");\n'
            f'    tlRamp.property("ADBE Ramp-0001").setValue([0, {tl_th_int / 2.0:g}]);\n'
            f'    tlRamp.property("ADBE Ramp-0002").setValue({_fill_js(top_line_from)});\n'
            f'    tlRamp.property("ADBE Ramp-0003").setValue([{tl_w_int:g}, {tl_th_int / 2.0:g}]);\n'
            f'    tlRamp.property("ADBE Ramp-0004").setValue({_fill_js(top_line_to)});\n'
            '    var tlMask = tlProg.property("ADBE Mask Parade").addProperty("ADBE Mask Atom");\n'
            '    tlMask.name = "Раскрытие";\n'
            '    var tlMaskProp = tlMask.property("ADBE Mask Shape");\n'
            f'    tlMaskProp.setValueAtTime(0, _tlShape(0, 0, {tl_th_int}, {tl_th_int}, {tl_mask_r:g}));\n'
            f'    tlMaskProp.setValueAtTime(DUR, _tlShape(0, 0, {tl_w_int}, {tl_th_int}, {tl_mask_r:g}));\n'
            '    try{ tlTrack.moveToBeginning(); }catch(e){}\n'
            '    try{ tlProg.moveToBeginning(); }catch(e){}\n'
        )
    # подпись о ролике (задание DG, обновлено DL)
    caption_on = bool(st.get("caption"))
    caption_text_raw = str(caption or "").strip()
    caption_case = st.get("caption_case") or "upper"
    caption_text = caption_text_raw.upper() if caption_case == "upper" else caption_text_raw
    caption_js = ""
    caption_plan = None
    if caption_on:
        caption_font = st.get("caption_font") or "SFPro-Bold"
        caption_size = float(st.get("caption_size") if st.get("caption_size") is not None else 26.0)
        caption_fill = list(st.get("caption_fill") if st.get("caption_fill") is not None else [1.0, 1.0, 1.0])
        caption_x = float(st.get("caption_x") if st.get("caption_x") is not None else 55.5)
        caption_y = float(st.get("caption_y") if st.get("caption_y") is not None else 228.0)
        caption_bg = bool(st.get("caption_bg", True))
        caption_bg_fill = list(st.get("caption_bg_fill") if st.get("caption_bg_fill") is not None else [0.345, 0.345, 0.345])
        caption_bg_op = float(st.get("caption_bg_op") if st.get("caption_bg_op") is not None else 45.0)
        caption_bg_round = float(st.get("caption_bg_round") if st.get("caption_bg_round") is not None else 68.0)
        # множители плашки считаются от ВИДИМОГО текста (масштаб слоя всегда 100%):
        # в эталоне 181.4/105.6 по ширине и 88.2/35.5 по высоте (задание DL-хвост)
        caption_kx = float(st.get("caption_kx") if st.get("caption_kx") is not None else 1.718)
        caption_ky = float(st.get("caption_ky") if st.get("caption_ky") is not None else 2.484)
        cap_pos_expr = _caption_pos_expr(caption_x, caption_y, caption_kx)
        caption_plan = {
            "text": caption_text,
            "font": caption_font,
            "size": caption_size,
            "fill": caption_fill,
            "x": caption_x,
            "y": caption_y,
            "case": caption_case,
            "bg": caption_bg,
            "bg_fill": caption_bg_fill,
            "bg_op": caption_bg_op,
            "bg_round": caption_bg_round,
            "kx": caption_kx,
            "ky": caption_ky,
        }
        if caption_text:
            bg_block = ""
            if caption_bg:
                size_expr = _caption_bg_size_expr(caption_kx, caption_ky)
                pos_expr = _caption_bg_pos_expr()
                bg_block = (
                    '    var capBg = main.layers.addShape();\n'
                    '    capBg.name = "Подпись (фон)";\n'
                    '    var capBgContents = capBg.property("ADBE Root Vectors Group");\n'
                    '    var capBgRect = capBgContents.addProperty("ADBE Vector Shape - Rect");\n'
                    f'    capBgRect.property("ADBE Vector Rect Roundness").setValue({caption_bg_round:g});\n'
                    f'    capBgRect.property("ADBE Vector Rect Size").expression = {_jd(size_expr)};\n'
                    '    var capBgFill = capBgContents.addProperty("ADBE Vector Graphic - Fill");\n'
                    f'    capBgFill.property("ADBE Vector Fill Color").setValue({_fill_js(caption_bg_fill)});\n'
                    f'    capBg.property("ADBE Transform Group").property("ADBE Position").expression = {_jd(pos_expr)};\n'
                    f'    capBg.property("ADBE Transform Group").property("ADBE Opacity").setValue({caption_bg_op:g});\n'
                )
            caption_js = (
                "\n    // ---- подпись о ролике (задание DG, обновлено DL) ----\n"
                + bg_block
                + f'    var capLayer = main.layers.addText({_jd(caption_text)});\n'
                '    capLayer.name = "Подпись";\n'
                '    var capDoc = capLayer.property("ADBE Text Properties").property("ADBE Text Document");\n'
                f'    var capVal = capDoc.value; capVal.resetCharStyle(); capVal.resetParagraphStyle(); capVal.text = {_jd(caption_text)};\n'
                f'    try{{ capVal.font = {_js(caption_font)}; }}catch(e){{}}\n'
                f'    capVal.fontSize = {caption_size:g};\n'
                f'    capVal.fillColor = {_fill_js(caption_fill)};\n'
                '    capVal.applyFill = true;\n'
                # caption_x — ЛЕВЫЙ КРАЙ блока подписи. Плашка центрируется на тексте
                # (своим выражением), поэтому её левый край садится на caption_x только
                # если центр надписи = caption_x + ширина плашки / 2 — это и считает
                # выражение на Position текста. Без плашки центрировать не от чего:
                # тогда текст просто выключен влево и начинается на caption_x.
                + ('    try{ capVal.justification = ParagraphJustification.CENTER_JUSTIFY; }catch(e){}\n'
                   if caption_bg else
                   '    try{ capVal.justification = ParagraphJustification.LEFT_JUSTIFY; }catch(e){}\n')
                + '    capDoc.setValue(capVal);\n'
                + f'    capLayer.property("ADBE Transform Group").property("ADBE Position").setValue([{caption_x:g}, {caption_y:g}]);\n'
                + (f'    capLayer.property("ADBE Transform Group").property("ADBE Position").expression = {_jd(cap_pos_expr)};\n'
                   if caption_bg else '')
                + ('    try{ capBg.moveToBeginning(); }catch(e){}\n' if caption_bg else '')
                + '    try{ capLayer.moveToBeginning(); }catch(e){}\n'
            )
    plan = {
        "fps": meta["fps"], "w": meta["w"], "h": meta["h"], "name": meta["name"],
        "dur": meta["dur"] / meta["fps"],
        "cams": cams_plan,
        # Камера 1: hold = резкие скачки без отката; keys = [кадр, %], опц. mode (drift);
        # ease = [in, out] на каждый ключ (задание B); fit = постоянный масштаб-страховка;
        # cx/cy — точка наезда в долях кадра (задание Q): при наезде неподвижна она,
        # превью рисует её же как transformOrigin и центр масштабирования
        "zoom": {"hold": _c1zoom == "jump", "fit": float(st.get("cam1_fit") or 100),
                 "cx": cam1_cx, "cy": cam1_cy,
                 "keys": cam1_scale or [], "ease": _zoom_key_eases(cam1_scale or [])},
        "intro": intro_plan,
        # общий масштаб интро, в процентах как в стиле (задание BG): превью множит на него
        # положение и размер блока; поля групп (dx/dy/ds/y) читает оно же — не переименовывать
        "intro_scale": float(st.get("intro_scale") or 100),
        # параметры анимаций интро: превью анимирует теми же числами,
        # что AE — вторая копия не заводится.
        "intro_anims": {
            "glitch": {
                "dur": INTRO_ANIMS["glitch"]["dur"],
                "blur": INTRO_ANIMS["glitch"]["blur"],
                "end_keys": [list(k) for k in INTRO_ANIMS["glitch"]["end_keys"]],
                "op_keys": [list(k) for k in INTRO_ANIMS["glitch"]["op_keys"]],
            },
            "reveal": {
                "dur": INTRO_ANIMS["reveal"]["dur"],
                "blur": INTRO_ANIMS["reveal"]["blur"],
                "scale": INTRO_ANIMS["reveal"]["scale"],
                "scale_3d": list(INTRO_ANIMS["reveal"]["scale_3d"]),
                "shape": INTRO_ANIMS["reveal"]["shape"],
                "smoothness": INTRO_ANIMS["reveal"]["smoothness"],
                "ease": list(INTRO_ANIMS["reveal"]["ease"]),
            },
        },
        "inserts": inserts_plan,
        "layer_order": list(st.get("layer_order") or ["subs", "video", "roto", "photo", "intro"]),
        "subs": subs_plan,
        "sub_hide": sub_hide,
        # цвет базовых субтитров (задание CO): [r,g,b] 0..1, превью красит тем же,
        # что AE — вторая копия не заводится. Жёлтые по-прежнему берут hl_fill.
        "sub_fill": list(sub_fill) if sub_fill else [1, 1, 1],
        # цвет выделения субтитров (задание CV): [r,g,b] 0..1, превью красит тем же,
        # что AE — вторая копия не заводится.
        "hl_fill": list(hl_fill) if hl_fill else [1, 0.9176, 0],
        # цвета интро для предпросмотра:
        "hl_fill3": list(hl_fill3) if hl_fill3 else [0.6863, 0.1216, 0.1216],
        "intro_fill": list(intro_fill) if intro_fill else None,
        "intro_hl_fill": list(intro_hl_fill) if intro_hl_fill else None,
        "back_scale": back_scale,
        "back_step": back_step,
        "roto": [{"ci": p["ci"], "ts": _r(p["tl_start"]), "te": _r(p["tl_end"]),
                  "src_start": _r(p["src_start"]), "src_end": _r(p["src_end"]),
                  "scale": _r(p["scale"])} for p in roto_plan],
        "audio": {"voice_src": (cams[0].get("path") or "") if cams else "",
                  "voice_db": float(st.get("voice_db") or 0),
                  "music_path": music_path, "music_db": music_db,
                  "censor": [[_r(a), _r(b)] for a, b in censor_windows],
                  "sfx": sfx_plan},
        # стопка субтитров и кегль — для отрисовки в предпросмотре (тот же источник, что _ae)
        "posy": _posy, "hl_step": _hl_step, "hl_rise": _hl_rise, "fsize": _fsize,
        # масштаб слоя прекомпа субтитров (задание FE): превью рисует transform: scale()
        # с origin в posy — то же число, что уходит в Scale в .jsx
        "sub_scale": sub_scale,
        # тень субтитров (задание DK): выключается при sub_bg
        "sub_shadow": sub_shadow,
        # размытие на старте (задание S): превью рисует CSS-фильтр с той же кривой;
        # 0 = выключено, план тогда несёт ноль и превью фильтр не вешает
        "start_blur": start_blur, "start_blur_dur": start_blur_dur,
        # точка покоя cam2-вставки (задание Q: уезжает в стиль insert_c2_x/y, долями кадра)
        "ins_c2x": round(meta["w"] * ins_c2x), "ins_c2y": round(meta["h"] * ins_c2y),
        # сдвиг интро по горизонтали, px (пара к intro_y)
        "intro_x": round(intro_x_px),
    }
    if sub_bg_plan:
        plan["sub_bg"] = sub_bg_plan
    if top_line_plan:
        plan["top_line"] = top_line_plan
    if caption_plan:
        plan["caption"] = caption_plan
    # Цвета и тень текста интро (новые ключи стиля). Всё выключено при дефолтах — ниже
    # собираются подстановки шаблона так, чтобы при выключенных ключах .jsx не менялся
    # ни на байт (golden), тем же приёмом, что уже применён для accent_font (задание R).
    # HL_FILL3 объявляется, только если строка color=="accent" реально есть в сборке.
    _hlfill3_decl = (", HL_FILL3=%s" % _fill_js(hl_fill3)) if _accent_color_used else ""
    # INTRO_FILL/INTRO_HL_FILL переопределяют белый/жёлтый ТОЛЬКО текста интро — от
    # стиля, не от данных строки; None (дефолт) = подстановка пустая.
    _intro_fill_decl = ((", INTRO_FILL=%s" % _fill_js(intro_fill)) if intro_fill is not None else "") \
        + ((", INTRO_HL_FILL=%s" % _fill_js(intro_hl_fill)) if intro_hl_fill is not None else "")
    # cf — аргумент introDoc с готовым fill строки color=="custom" (см. _intro_line_js);
    # добавляется, только если такая строка есть в ЭТОЙ сборке.
    _fill_params = ",cf" if _custom_color_used else ""
    _fill_call = ",ln.fill" if _custom_color_used else ""
    # Порядок цветов совпадает с приоритетом в данных: yellow (жёлтые субтитро-слова
    # интро) первым, как и было; accent/custom дописываются ВНУТРЬ ветки "иначе", только
    # если реально нужны — при их отсутствии выражение побайтово прежнее.
    _white_expr = "INTRO_FILL" if intro_fill is not None else "[1,1,1]"
    _yellow_expr = "INTRO_HL_FILL" if intro_hl_fill is not None else "HL_FILL"
    _fill_inner = _white_expr
    if _custom_color_used:
        _fill_inner = '(col=="custom"?(cf||[1,1,1]):%s)' % _fill_inner
    if _accent_color_used:
        _fill_inner = '(col=="accent"?HL_FILL3:%s)' % _fill_inner
    _intro_fill_pick = '(col=="yellow"?%s:%s)' % (_yellow_expr, _fill_inner)
    # Эффекты появления строк интро (anim: glitch/reveal/left/right/up/count, fx: glow).
    # Пока anim == "" и fx == "", подстановки пустые либо равны прежнему тексту (golden).
    _any_glitch = any(x.get("anim") == "glitch" for g in _intro_groups for x in g)
    _any_reveal = any(x.get("anim") == "reveal" for g in _intro_groups for x in g)
    _any_left = any(x.get("anim") == "left" for g in _intro_groups for x in g)
    _any_right = any(x.get("anim") == "right" for g in _intro_groups for x in g)
    _any_up = any(x.get("anim") == "up" for g in _intro_groups for x in g)
    _any_count = any(_has_valid_count(x) for g in _intro_groups for x in g)
    _any_fx_glow = any(x.get("fx") == "glow" for g in _intro_groups for x in g)
    _any_intro_yellow = any(x.get("color") == "yellow" for g in _intro_groups for x in g)
    # Автотень — только у глитча и строк заднего плана. Свечение (fx=="glow") её больше
    # НЕ приносит: у строки со свечением на слое слова остаются ровно Glo2 и (для жёлтой)
    # тритон, иначе к свечению подмешивалась тень, которой пользователь не просил.
    _any_auto_shadow = _any_glitch or _any_back
    _shadow_needed = intro_shadow_on or _any_auto_shadow
    _anim_fx_used = (
        _any_glitch or _any_reveal or _any_fx_glow
        or _any_left or _any_right or _any_up
        or _any_count
    )

    # Тень (Drop Shadow) на КАЖДОМ слове/строке интро — пресет intro_shadow, либо
    # автоматически для строк с anim=="glitch" и back==True (fx=="glow" — без тени).
    _intro_shadow_decl = (
        "\n    var INTRO_SHADOW_OP=%g, INTRO_SHADOW_DIR=%g, INTRO_SHADOW_DIST=%g, INTRO_SHADOW_SOFT=%g;"
        "\n    var BACK_SHADOW_OP=%g, BACK_SHADOW_SOFT=%g;"
        % (intro_shadow_op, intro_shadow_dir, intro_shadow_dist, intro_shadow_soft,
           back_shadow_op, back_shadow_soft)
    ) if _shadow_needed else ""
    _intro_word_shadow_fn = (
        "\n        function introWordShadow(L, isBack){ var ds=addFX(L,\"ADBE Drop Shadow\");"
        " setP(ds,\"ADBE Drop Shadow-0002\",isBack?BACK_SHADOW_OP:INTRO_SHADOW_OP);"
        " setP(ds,\"ADBE Drop Shadow-0003\",INTRO_SHADOW_DIR);"
        " setP(ds,\"ADBE Drop Shadow-0004\",INTRO_SHADOW_DIST);"
        " setP(ds,\"ADBE Drop Shadow-0005\",isBack?BACK_SHADOW_SOFT:INTRO_SHADOW_SOFT); }"
    ) if _shadow_needed else ""
    if intro_shadow_on:
        _intro_word_shadow_line = " introWordShadow(Ll, ln.back);"
        _intro_word_shadow_word = " introWordShadow(L2, ln.back);"
    elif _any_auto_shadow:
        _intro_word_shadow_line = ' if(ln.anim=="glitch"||ln.back) introWordShadow(Ll, ln.back);'
        _intro_word_shadow_word = ' if(ln.anim=="glitch"||ln.back) introWordShadow(L2, ln.back);'
    else:
        _intro_word_shadow_line = ""
        _intro_word_shadow_word = ""

    # Раскладка строк интро по вертикали (задание A1): готовые Y базовых линий уезжают
    # в .jsx массивом INTRO_LY и берутся оттуда — шаг знает высоту букв шрифта (back_gap)
    # и якорь блока, в шаблоне этого не сосчитать. Массив нужен, если в ролике есть строки
    # заднего плана (там шаг уже не LINE_STEP) ИЛИ хоть одна группа с якорем «first»
    # (первая строка на месте). Ни того, ни другого — .jsx прежний байт в байт (golden).
    _any_first = any(a == "first" for a in _intro_anchor)
    _intro_ly_decl = (
        "    var INTRO_LY=%s;    // [группа][строка] — Y базовой линии строки в прекомпе,"
        " считает Python (задание A1): шаг знает высоту букв шрифта и якорь блока\n"
        % _jd(_intro_ly)
    ) if (_any_back or _any_first) else ""

    if _any_back:
        _intro_line_layout = (
            "var nL=GRP.length, BACK_STEP=%g, BACK_SCALE=%g, maxLineW=0;\n"
            "            var lineSteps=[0], totH=0;\n"
            "            for(var si=1; si<nL; si++){\n"
            "                var stp = LINE_STEP * (GRP[si].back ? BACK_STEP : (GRP[si-1].back ? 0.75 : 1.0));\n"
            "                totH += stp;\n"
            "                lineSteps.push(totH);\n"
            "            }\n"
            "            var cY = (!GRP[0].back && nL>1) ? (H/2 - (nL-1)*60) : (H/2 - totH/2);\n"
            "            for (var qi=0; qi<nL; qi++){\n"
            "                var ln=GRP[qi], wds=ln.words||[], tms=ln.times||[];\n"
            "                var lineY=cY+lineSteps[qi];\n"
            "                if(INTRO_LY[gI]&&INTRO_LY[gI][qi]!=null) lineY=INTRO_LY[gI][qi];"
            % (back_step, back_scale)
        )
        _intro_back_scale_fn = (
            '\n        function introBackScale(L){ try{ '
            'L.property("ADBE Transform Group").property("ADBE Scale").setValue([Math.round(BACK_SCALE*1000)/10, Math.round(BACK_SCALE*1000)/10, 100]); '
            '}catch(e){} }'
        )
        _intro_back_scale_line = " if(ln.back) introBackScale(Ll);"
        _intro_back_scale_line_w = "var lineW=introW(Ll); if(ln.back) lineW*=BACK_SCALE; if(lineW>maxLineW) maxLineW=lineW;"
        _intro_back_scale_tmp = " if(ln.back) lineW*=BACK_SCALE;"
        _intro_back_scale_word = " if(ln.back) introBackScale(L2);"
        _intro_back_scale_wpx = " if(ln.back) wpx*=BACK_SCALE;"
    elif _any_first:
        # Якорь «первая строка» без строк заднего плана: шаги — прежние LINE_STEP, но
        # отсчёт не от центра блока, а от первой строки, и добавленная строка верх не
        # поднимает — числа даёт Python.
        _intro_line_layout = (
            "var nL=GRP.length, maxLineW=0;\n"
            "            for (var qi=0; qi<nL; qi++){\n"
            "                var ln=GRP[qi], wds=ln.words||[], tms=ln.times||[], lineY=INTRO_LY[gI][qi];"
        )
        _intro_back_scale_fn = ""
        _intro_back_scale_line = ""
        _intro_back_scale_line_w = "if(introW(Ll)>maxLineW) maxLineW=introW(Ll);"
        _intro_back_scale_tmp = ""
        _intro_back_scale_word = ""
        _intro_back_scale_wpx = ""
    else:
        _intro_line_layout = (
            "var nL=GRP.length, cY=H/2 - (nL-1)/2*LINE_STEP, maxLineW=0;\n"
            "            for (var qi=0; qi<nL; qi++){\n"
            "                var ln=GRP[qi], wds=ln.words||[], tms=ln.times||[], lineY=cY+qi*LINE_STEP;"
        )
        _intro_back_scale_fn = ""
        _intro_back_scale_line = ""
        _intro_back_scale_line_w = "if(introW(Ll)>maxLineW) maxLineW=introW(Ll);"
        _intro_back_scale_tmp = ""
        _intro_back_scale_word = ""
        _intro_back_scale_wpx = ""

    # Подъём интро над видеовставкой (признак front на группу): хотя бы одна группа
    # попадает на видеовставку — в .jsx появляются массив INTRO_FRONT, introFrontLayers
    # и маршрутизация push/raise. Ни одной — все четыре подстановки пустые, .jsx прежний (golden).
    _any_front = any(f for f in _intro_front)
    _intro_front_decl = (
        "    var INTRO_FRONT=%s;    // [0|1 на группу] — группа попадает на видеовставку:"
        " поднимается над рото и видео\n" % _jd(_intro_front)
    ) if _any_front else ""
    _intro_front_arr_decl = "\n    var introFrontLayers = [];" if _any_front else ""
    _intro_front_route = (
        "if (INTRO_FRONT[gI]) introFrontLayers.push(iL);\n"
        "            else introLayers.push(iL);"
    ) if _any_front else "introLayers.push(iL);"
    _intro_front_raise = (
        "    for (var fi = 0; fi < introFrontLayers.length; fi++){\n"
        "        try{ introFrontLayers[fi].moveToBeginning(); }catch(e){}\n"
        "    }\n"
    ) if _any_front else ""

    # Подъём интро над РОТО по положению (задание C): группа Камеры 1, чей блок в нижней
    # половине кадра, после раскладки по layer_order переносится под самый верхний
    # рото-слой (moveBefore), то есть встаёт сразу над рото. Ни одной такой группы —
    # все четыре подстановки пустые, .jsx прежний (golden).
    _any_above_roto = any(_intro_above_roto)
    _intro_above_roto_decl = (
        "    var INTRO_ABOVE_ROTO=%s;    // [0|1 на группу] — группа Камеры 1 в НИЖНЕЙ"
        " половине кадра: поднимается над рото\n" % _jd(_intro_above_roto)
    ) if _any_above_roto else ""
    _intro_above_roto_arr_decl = "\n    var introAboveRoto = [];" if _any_above_roto else ""
    _intro_above_roto_route = (
        "\n            if (INTRO_ABOVE_ROTO[gI]) introAboveRoto.push(iL);"
    ) if _any_above_roto else ""
    _intro_above_roto_raise = (
        "    // Интро над рото по положению (задание C): каждый слой из introAboveRoto\n"
        "    // переносим ПЕРЕД самым верхним рото-слоем. Рото-слоёв нет — делать нечего.\n"
        "    var topRoto = null;\n"
        "    for (var ri3 = 0; ri3 < rotoLayers.length; ri3++){\n"
        "        try{ if (!topRoto || rotoLayers[ri3].index < topRoto.index) topRoto = rotoLayers[ri3]; }catch(e){}\n"
        "    }\n"
        "    if (topRoto){\n"
        "        for (var ai = 0; ai < introAboveRoto.length; ai++){\n"
        "            try{ introAboveRoto[ai].moveBefore(topRoto); }catch(e){}\n"
        "        }\n"
        "    }\n"
    ) if _any_above_roto else ""

    if _anim_fx_used:
        _g_an = INTRO_ANIMS["glitch"]
        _r_an = INTRO_ANIMS["reveal"]
        _gl_op_lines = []
        for _kt, _kv in _g_an["op_keys"]:
            _t_str = "t0" if _kt == 0 else f"t0+{_kt:g}"
            _gl_op_lines.append(f'                op.setValueAtTime({_t_str},{_kv:g});\n')
        _gl_op_jsx = "".join(_gl_op_lines)
        _sc_pct = int(round(_r_an["scale"] * 100))
        _sc3d_str = ",".join(f"{x:g}" if x == int(x) else str(x) for x in _r_an["scale_3d"])
        # Тритон на СЛОВЕ (задание G): у жёлтой строки со свечением/глитчем Midtones
        # красится в цвет заливки жёлтой строки — то же выражение, что _yellow_expr
        # (INTRO_HL_FILL, если он задан в стиле, иначе HL_FILL). Highlights/Shadows/
        # смешивание — дефолтные. Ставится ПОСЛЕ Glo2; строк без глитча и свечения,
        # как и белый/accent/custom цвет, он не касается. Нет таких строк в сборке —
        # подстановка пустая, .jsx прежний.
        _tt_yellow = ""
        if _any_glitch or _any_fx_glow:
            _tt_yellow = (
                '            if((anim=="glitch"||fx=="glow") && col=="yellow"){\n'
                '                var tt=addFX(L,"ADBE Tritone"); setP(tt,"ADBE Tritone-0002",%s);\n'
                '            }\n' % _yellow_expr
            )
        _intro_anim_fx_fn = (
            '\n        function introAnimFX(L, t0, anim, fx, w, target, expr, isBack, col){\n'
            '            var hasCnt=(typeof target!=="undefined" && target!==null && !isNaN(target));\n'
            '            if(hasCnt){\n'
            '                try{\n'
            '                    var sl=addFX(L,"ADBE Slider Control");\n'
            '                    if(sl){\n'
            '                        var slP=sl.property("ADBE Slider Control-0001");\n'
            '                        if(slP){\n'
            '                            slP.setValueAtTime(t0,0);\n'
            '                            slP.setValueAtTime(t0+HL_DUR,target);\n'
            '                        }\n'
            '                    }\n'
            '                }catch(e){}\n'
            '                try{\n'
            '                    var sp=L.property("ADBE Text Properties").property("ADBE Text Document");\n'
            '                    if(sp && expr){\n'
            '                        sp.expression=expr;\n'
            '                    }\n'
            '                }catch(e){}\n'
            '            }\n'
            '            if(anim=="glitch"){\n'
            f'                var fxGb=addFX(L,"ADBE Gaussian Blur 2"); setP(fxGb,"ADBE Gaussian Blur 2-0001",{_g_an["blur"]:g});\n'
            '                var fxGl=addFX(L,"ADBE Glo2"); setP(fxGl,"ADBE Glo2-0002",149); setP(fxGl,"ADBE Glo2-0003",77); setP(fxGl,"ADBE Glo2-0004",0.62);\n'
            '            } else if(fx=="glow"){\n'
            '                var fxGl=addFX(L,"ADBE Glo2"); setP(fxGl,"ADBE Glo2-0002",149); setP(fxGl,"ADBE Glo2-0003",77); setP(fxGl,"ADBE Glo2-0004",0.62);\n'
            '            }\n'
            + _tt_yellow +
            '            if(anim=="glitch"){\n'
            '                try{\n'
            '                    var tp=L.property("ADBE Text Properties");\n'
            '                    var anims=(tp?tp.property("ADBE Text Animators"):null)||L.property("ADBE Text Animators");\n'
            '                    var tanim=anims.addProperty("ADBE Text Animator");\n'
            '                    var sels=tanim.property("ADBE Text Selectors");\n'
            '                    var sel=sels.addProperty("ADBE Text Selector");\n'
            '                    try{\n'
            '                        var pStart=sel.property("ADBE Text Percent Start");\n'
            f'                        pStart.setValueAtTime(t0,0); pStart.setValueAtTime(t0+{_g_an["dur"]:g},100);\n'
            '                    }catch(e){}\n'
            '                    try{\n'
            '                        var pEnd=sel.property("ADBE Text Percent End");\n'
            f'                        pEnd.setValueAtTime(t0+{_g_an["end_keys"][0][0]:g},{_g_an["end_keys"][0][1]:g}); pEnd.setValueAtTime(t0+{_g_an["end_keys"][1][0]:g},{_g_an["end_keys"][1][1]:g}); pEnd.setValueAtTime(t0+{_g_an["end_keys"][2][0]:g},{_g_an["end_keys"][2][1]:g});\n'
            '                    }catch(e){}\n'
            '                    try{\n'
            '                        var adv=sel.property("ADBE Text Range Advanced");\n'
            '                        adv.property("ADBE Text Randomize Order").setValue(1);\n'
            '                        adv.property("ADBE Text Random Seed").setValue(10);\n'
            '                    }catch(e){}\n'
            '                    try{\n'
            '                        var aProps=tanim.property("ADBE Text Animator Properties");\n'
            '                        var aOp=aProps.addProperty("ADBE Text Opacity");\n'
            '                        aOp.setValue(0);\n'
            '                    }catch(e){}\n'
            '                }catch(e){}\n'
            '                var op=L.property("ADBE Transform Group").property("ADBE Opacity");\n'
            f'{_gl_op_jsx}'
            '            }else if(anim=="reveal"){\n'
            '                try{\n'
            '                    var tp=L.property("ADBE Text Properties");\n'
            '                    var anims=(tp?tp.property("ADBE Text Animators"):null)||L.property("ADBE Text Animators");\n'
            '                    var tanim=anims.addProperty("ADBE Text Animator");\n'
            '                    var sels=tanim.property("ADBE Text Selectors");\n'
            '                    var sel=sels.addProperty("ADBE Text Selector");\n'
            '                    try{\n'
            '                        var pOff=sel.property("ADBE Text Percent Offset");\n'
            '                        pOff.setValueAtTime(t0,-100); pOff.setValueAtTime(t0+F_DUR,100);\n'
            '                    }catch(e){}\n'
            '                    try{\n'
            '                        var adv=sel.property("ADBE Text Range Advanced");\n'
            f'                        adv.property("ADBE Text Range Shape").setValue({_r_an["shape"]:d});\n'
            f'                        adv.property("ADBE Text Selector Smoothness").setValue({_r_an["smoothness"]:d});\n'
            f'                        adv.property("ADBE Text Levels Max Ease").setValue({_r_an["ease"][0]:d});\n'
            f'                        adv.property("ADBE Text Levels Min Ease").setValue({_r_an["ease"][1]:d});\n'
            '                    }catch(e){}\n'
            '                    try{\n'
            '                        var aProps=tanim.property("ADBE Text Animator Properties");\n'
            '                        var aSc=aProps.addProperty("ADBE Text Scale 3D");\n'
            f'                        aSc.setValue([{_sc3d_str}]);\n'
            '                    }catch(e){}\n'
            '                }catch(e){}\n'
            '                try{\n'
            '                    var gb=addFX(L,"ADBE Gaussian Blur 2");\n'
            '                    if(gb){\n'
            '                        var pBl=gb.property("ADBE Gaussian Blur 2-0001");\n'
            f'                        pBl.setValueAtTime(t0,{_r_an["blur"]:g}); pBl.setValueAtTime(t0+F_DUR,0);\n'
            '                    }\n'
            '                }catch(e){}\n'
            '                try{\n'
            '                    var sc=L.property("ADBE Transform Group").property("ADBE Scale");\n'
            '                    var isB=(typeof isBack!=="undefined"&&isBack)||(typeof BACK_SCALE!=="undefined"&&sc.value[0]<99);\n'
            '                    if(isB){\n'
            '                        var bSc=(typeof BACK_SCALE!=="undefined")?BACK_SCALE:0.69;\n'
            '                        var s1=Math.round(bSc*1000)/10;\n'
            f'                        var s0=Math.round(s1*{_r_an["scale"]:g}*10)/10;\n'
            '                        sc.setValueAtTime(t0,[s0,s0]); sc.setValueAtTime(t0+F_DUR,[s1,s1]);\n'
            '                    }else{\n'
            f'                        sc.setValueAtTime(t0,[{_sc_pct},{_sc_pct}]); sc.setValueAtTime(t0+F_DUR,[100,100]);\n'
            '                    }\n'
            '                }catch(e){}\n'
            '                var op=L.property("ADBE Transform Group").property("ADBE Opacity");\n'
            '                op.setValueAtTime(t0,0); op.setValueAtTime(t0+F_DUR,100); easePair(op);\n'
            '            }else if(anim=="left"){\n'
            '                if(typeof w==="undefined" || w===null) w=introW(L);\n'
            '                var pos=L.property("ADBE Transform Group").property("ADBE Position");\n'
            '                var curP=pos.value, curX=curP[0], curY=curP[1];\n'
            '                pos.setValueAtTime(t0, [curX-w, curY]);\n'
            '                pos.setValueAtTime(t0+F_DUR, [curX, curY]);\n'
            '                easePair(pos);\n'
            '                var op=L.property("ADBE Transform Group").property("ADBE Opacity");\n'
            '                op.setValueAtTime(t0,0); op.setValueAtTime(t0+F_DUR,100); easePair(op);\n'
            '            }else if(anim=="right"){\n'
            '                if(typeof w==="undefined" || w===null) w=introW(L);\n'
            '                var pos=L.property("ADBE Transform Group").property("ADBE Position");\n'
            '                var curP=pos.value, curX=curP[0], curY=curP[1];\n'
            '                pos.setValueAtTime(t0, [curX+w, curY]);\n'
            '                pos.setValueAtTime(t0+F_DUR, [curX, curY]);\n'
            '                easePair(pos);\n'
            '                var op=L.property("ADBE Transform Group").property("ADBE Opacity");\n'
            '                op.setValueAtTime(t0,0); op.setValueAtTime(t0+F_DUR,100); easePair(op);\n'
            '            }else if(anim=="up"){\n'
            '                var pos=L.property("ADBE Transform Group").property("ADBE Position");\n'
            '                var curP=pos.value, curX=curP[0], curY=curP[1];\n'
            '                pos.setValueAtTime(t0, [curX, curY+HL_RISE]);\n'
            '                pos.setValueAtTime(t0+F_DUR, [curX, curY]);\n'
            '                easePair(pos);\n'
            '                var op=L.property("ADBE Transform Group").property("ADBE Opacity");\n'
            '                op.setValueAtTime(t0,0); op.setValueAtTime(t0+F_DUR,100); easePair(op);\n'
            '            }else if(hasCnt){\n'
            '                var op=L.property("ADBE Transform Group").property("ADBE Opacity");\n'
            '                op.setValueAtTime(t0,0); op.setValueAtTime(t0+HL_DUR,100); easePair(op);\n'
            '            }else{\n'
            '                var op=L.property("ADBE Transform Group").property("ADBE Opacity");\n'
            '                op.setValueAtTime(t0,0); op.setValueAtTime(t0+F_DUR,100); easePair(op);\n'
            '            }\n'
            '        }'
        )
        if _any_count:
            if _any_back:
                _intro_line_anim = "introAnimFX(Ll, t0l, ln.anim, ln.fx, null, ln.cnt, ln.expr, ln.back, ln.color);"
                _intro_word_anim = (
                    'var tw=(tms[wj2]!=null?tms[wj2]:0); if(tw<0)tw=0; '
                    'var cw=null; if(ln.cnts){for(var ci=0;ci<ln.cnts.length;ci++){if(ln.cnts[ci][0]===wj2){cw=ln.cnts[ci];break;}}}\n'
                    '                    introAnimFX(wl[wj2], tw, ln.anim, ln.fx, ww[wj2], cw?cw[1]:null, cw?cw[2]:null, ln.back, ln.color);'
                )
            else:
                _intro_line_anim = "introAnimFX(Ll, t0l, ln.anim, ln.fx, null, ln.cnt, ln.expr, null, ln.color);"
                _intro_word_anim = (
                    'var tw=(tms[wj2]!=null?tms[wj2]:0); if(tw<0)tw=0; '
                    'var cw=null; if(ln.cnts){for(var ci=0;ci<ln.cnts.length;ci++){if(ln.cnts[ci][0]===wj2){cw=ln.cnts[ci];break;}}}\n'
                    '                    introAnimFX(wl[wj2], tw, ln.anim, ln.fx, ww[wj2], cw?cw[1]:null, cw?cw[2]:null, null, ln.color);'
                )
        else:
            if _any_back:
                _intro_line_anim = "introAnimFX(Ll, t0l, ln.anim, ln.fx, null, null, null, ln.back, ln.color);"
                _intro_word_anim = (
                    'var tw=(tms[wj2]!=null?tms[wj2]:0); if(tw<0)tw=0;\n'
                    '                    introAnimFX(wl[wj2], tw, ln.anim, ln.fx, ww[wj2], null, null, ln.back, ln.color);'
                )
            else:
                _intro_line_anim = "introAnimFX(Ll, t0l, ln.anim, ln.fx, null, null, null, null, ln.color);"
                _intro_word_anim = (
                    'var tw=(tms[wj2]!=null?tms[wj2]:0); if(tw<0)tw=0;\n'
                    '                    introAnimFX(wl[wj2], tw, ln.anim, ln.fx, ww[wj2], null, null, null, ln.color);'
                )
    else:
        _intro_anim_fx_fn = ""
        _intro_line_anim = (
            'var opL=Ll.property("ADBE Transform Group").property("ADBE Opacity");\n'
            '                    opL.setValueAtTime(t0l,0); opL.setValueAtTime(t0l+F_DUR,100); easePair(opL);'
        )
        _intro_word_anim = (
            'var op=wl[wj2].property("ADBE Transform Group").property("ADBE Opacity");\n'
            '                    var tw=(tms[wj2]!=null?tms[wj2]:0); if(tw<0)tw=0;\n'
            '                    op.setValueAtTime(tw, 0); op.setValueAtTime(tw+F_DUR, 100); easePair(op);'
        )

    # Вызовы свечения жёлтого хайлайта (задание GP): ставятся ПОСЛЕДНИМИ эффектами
    # слоя строки/слова, только если в сборке есть жёлтая строка интро.
    _hl_call_line = ' if(ln.color=="yellow" && !grpGlitch && ln.fx!="glow") introHlGlow(Ll);' if _any_intro_yellow else ""
    _hl_call_word = ' if(ln.color=="yellow" && !grpGlitch && ln.fx!="glow") introHlGlow(wl[wj2]);' if _any_intro_yellow else ""
    _intro_line_anim += _hl_call_line
    _intro_word_anim += _hl_call_word

    _intro_hl_glow_fn = (
        '\n        function introHlGlow(L){\n'
        '            var fxGl=addFX(L,"ADBE Glo2"); setP(fxGl,"ADBE Glo2-0002",149); setP(fxGl,"ADBE Glo2-0003",77); setP(fxGl,"ADBE Glo2-0004",0.62);\n'
        f'            var tt=addFX(L,"ADBE Tritone"); setP(tt,"ADBE Tritone-0002",{_yellow_expr});\n'
        '        }'
    ) if _any_intro_yellow else ""

    if _any_intro_yellow:
        _intro_group_flags = (
            "\n            var grpGlitch=false, grpGlow=false, grpYellow=false;\n"
            "            for(var gck=0; gck<GRP.length; gck++){\n"
            '                if(GRP[gck].anim=="glitch") grpGlitch=true;\n'
            '                if(GRP[gck].fx=="glow") grpGlow=true;\n'
            '                if(GRP[gck].color=="yellow") grpYellow=true;\n'
            "            }"
        )
        if _any_glitch or _any_fx_glow:
            _intro_comp_glow = (
                'if(grpGlitch || (!grpGlow && !grpYellow)){\n'
                '                try{ var igl=iL.property("ADBE Effect Parade").addProperty("ADBE Glo2");\n'
                '                     if(grpGlitch){\n'
                '                         setP(igl,"ADBE Glo2-0002",211); setP(igl,"ADBE Glo2-0003",93); setP(igl,"ADBE Glo2-0004",0.42);\n'
                '                     } else {\n'
                '                         try{ igl.property("Glow Radius").setValue(42); }catch(e){}\n'
                '                         try{ igl.property("Glow Intensity").setValue(INTRO_GLOW); }catch(e){}\n'
                '                     }\n'
                '                }catch(e){}\n'
                '            }'
            )
        else:
            _intro_comp_glow = (
                'if(!grpYellow){\n'
                '                try{ var igl=iL.property("ADBE Effect Parade").addProperty("ADBE Glo2");\n'
                '                     try{ igl.property("Glow Radius").setValue(42); }catch(e){}\n'
                '                     try{ igl.property("Glow Intensity").setValue(INTRO_GLOW); }catch(e){} }catch(e){}\n'
                '            }'
            )
    else:
        if _any_glitch or _any_fx_glow:
            _intro_group_flags = (
                "\n            var grpGlitch=false, grpGlow=false;\n"
                "            for(var gck=0; gck<GRP.length; gck++){\n"
                '                if(GRP[gck].anim=="glitch") grpGlitch=true;\n'
                '                if(GRP[gck].fx=="glow") grpGlow=true;\n'
                "            }"
            )
            _intro_comp_glow = (
                'if(grpGlitch || !grpGlow){\n'
                '                try{ var igl=iL.property("ADBE Effect Parade").addProperty("ADBE Glo2");\n'
                '                     if(grpGlitch){\n'
                '                         setP(igl,"ADBE Glo2-0002",211); setP(igl,"ADBE Glo2-0003",93); setP(igl,"ADBE Glo2-0004",0.42);\n'
                '                     } else {\n'
                '                         try{ igl.property("Glow Radius").setValue(42); }catch(e){}\n'
                '                         try{ igl.property("Glow Intensity").setValue(INTRO_GLOW); }catch(e){}\n'
                '                     }\n'
                '                }catch(e){}\n'
                '            }'
            )
        else:
            _intro_group_flags = ""
            _intro_comp_glow = (
                'try{ var igl=iL.property("ADBE Effect Parade").addProperty("ADBE Glo2");\n'
                '                 try{ igl.property("Glow Radius").setValue(42); }catch(e){}\n'
                '                 try{ igl.property("Glow Intensity").setValue(INTRO_GLOW); }catch(e){} }catch(e){}'
            )
    # Тень ПРЕКОМПА интро (задание B): своя у камеры 1 и камеры 2 — направление/дистанция/
    # мягкость те же, что у dropShadow (135/0/287), меняются только цвет и непрозрачность.
    # Все четыре ключа на дефолтах (белая, 68) — подстановка ровно прежняя строка
    # dropShadow(iL, 68); иначе в шаблон едет объявление introCompShadow и вызов с камерой
    # группы (INTRO_ON2[gI]: 1 у группы на перебивке). Дефолтный .jsx не меняется (golden).
    _ics_default = (intro_comp_shadow_fill == [1.0, 1.0, 1.0]
                    and intro_comp_shadow_op == 68.0
                    and intro_comp_shadow2_fill == [1.0, 1.0, 1.0]
                    and intro_comp_shadow2_op == 68.0)

    def _ics_rgb(fill):
        return "%g,%g,%g" % (fill[0], fill[1], fill[2])

    if _ics_default:
        _intro_comp_shadow = "dropShadow(iL, 68);"
        _intro_comp_shadow_fn = ""
    else:
        _intro_comp_shadow = "introCompShadow(iL, INTRO_ON2[gI]);"
        _intro_comp_shadow_fn = (
            "\n    function introCompShadow(L, on2){"
            " var ds=addFX(L,\"ADBE Drop Shadow\");"
            " setP(ds,\"ADBE Drop Shadow-0001\", on2?[%s]:[%s]);"
            " setP(ds,\"ADBE Drop Shadow-0002\", on2?%g:%g);"
            " setP(ds,\"ADBE Drop Shadow-0003\",135);"
            " setP(ds,\"ADBE Drop Shadow-0004\",0);"
            " setP(ds,\"ADBE Drop Shadow-0005\",287); }"
            % (_ics_rgb(intro_comp_shadow2_fill), _ics_rgb(intro_comp_shadow_fill),
               intro_comp_shadow2_op, intro_comp_shadow_op)
        )
    if _any_glitch and glitch_asset and _glitch_word_times:
        gl_blocks = []
        eff_db = 0.0 + glitch_db
        _r4 = lambda v: round(v * 10000) / 10000
        for _grp_t in _glitch_sound_groups:
            # Один слой звука на ГРУППУ глитч-слов (ПРАВКА 1): startTime за 0.567 до
            # ПЕРВОГО слова группы, дальше огибающая — тишина до первого слова, полка
            # на glitch_db, спад за кадр до конца слоя (ПРАВКА 2).
            _t_first, _t_last = _grp_t[0], _grp_t[-1]
            st_val = _r4(_t_first - GLITCH_SFX_PRE_S)
            in_val = _r4(_t_first)
            out_val = _r4(_t_last + GLITCH_SFX_HOLD_S + GLITCH_SFX_RELEASE_S + 1.0 / _fps0)
            atk_val = _r4(_t_first + GLITCH_SFX_ATTACK_S)
            rel_s_val = _r4(_t_last + GLITCH_SFX_HOLD_S)
            rel_e_val = _r4(_t_last + GLITCH_SFX_HOLD_S + GLITCH_SFX_RELEASE_S)
            lines = [
                '            var gl=main.layers.add(glitchItem); gl.name="Глитч";',
                '            gl.startTime=%g; gl.inPoint=%g; gl.outPoint=%g;' % (st_val, in_val, out_val),
                '            try{ var glAlv=gl.property("ADBE Audio Group").property("ADBE Audio Levels");',
                '                 glAlv.setValue([%g, %g]);' % (eff_db, eff_db),
                '                 // звук покрывает ВСЮ группу глитч-слов (ПРАВКА 1): тишина до',
                '                 // первого слова, нарастание за 0.08, полка, спад до тишины',
                '                 // за кадр до конца слоя',
                '                 glAlv.setValueAtTime(%g, [%g, %g]);' % (in_val, GLITCH_SFX_QUIET_DB, GLITCH_SFX_QUIET_DB),
                '                 glAlv.setValueAtTime(%g, [%g, %g]);' % (atk_val, eff_db, eff_db),
                '                 glAlv.setValueAtTime(%g, [%g, %g]);' % (rel_s_val, eff_db, eff_db),
                '                 glAlv.setValueAtTime(%g, [%g, %g]); }catch(e){}' % (rel_e_val, GLITCH_SFX_QUIET_DB, GLITCH_SFX_QUIET_DB),
            ]
            gl_blocks.append("\n".join(lines))
        gl_body = "\n".join(gl_blocks)
        glitch_sfx = (
            '\n\n    // ---- звук глитча: один слой на ГРУППУ глитч-слов (ПРАВКА 1) ----\n'
            f'    var GLITCH={_js(glitch_asset)};\n'
            '    if (GLITCH){ var glitchItem=imp(GLITCH);\n'
            '        if (glitchItem){\n'
            '            toBin(glitchItem,"Интро");\n'
            f'{gl_body}\n'
            '        }\n'
            '    }'
        )
    else:
        glitch_sfx = ""
    # Дисклеймер подстраивается под шрифт (задание E). Кегль: база int(H·0.0245), но самая
    # длинная строка не должна вылезать за DISC_FIT_W ширины кадра — у SF Pro Condensed при
    # 47 она давала ровно 0.992·W, у Oswald-Bold 1194 px (за краем кадра 1080) и пользователь
    # ужимал слой руками. Кегль только УМЕНЬШАЕТСЯ: узкий шрифт дисклеймер не раздувает.
    # Ширины нет (шрифта/глифа нет в системе) — прежняя база, как сегодня.
    _disc_base = int(meta["h"] * 0.0245)
    _disc_lines = (disclaimer or "").split("\n")
    _disc_w = [_fonts.text_width(font_ps, _ln, _disc_base) for _ln in _disc_lines]
    if any(_w is None for _w in _disc_w):
        disc_size = _disc_base
    else:
        _disc_w_max = max(_disc_w)
        disc_size = (round(_disc_base * DISC_FIT_W * meta["w"] / _disc_w_max, 2)
                     if _disc_w_max > DISC_FIT_W * meta["w"] else _disc_base)
    # Интервал дисклеймера: шаг строк = «хвост вниз верхней строки + высота букв нижней +
    # disc_gap» при УЖЕ подобранном кегле (высоты даёт fonts.ink_extent по контурам глифов).
    # Зазор не задан (None = интервал авто) или высот нет — DISC_LEAD не объявляется вовсе.
    disc_lead = None
    if st.get("disc_gap") is not None and len(_disc_lines) > 1:
        _disc_ink = [_fonts.ink_extent(font_ps, _ln, disc_size) for _ln in _disc_lines]
        if all(_x is not None for _x in _disc_ink):
            disc_lead = round(max(_disc_ink[_i][1] + _disc_ink[_i + 1][0]
                                  for _i in range(len(_disc_ink) - 1))
                              + float(st["disc_gap"]), 2)
    # Применение интервала к текстовому документу — в ОБОИХ блоках дисклеймера (головной в
    # template.py, концевой ниже). При дефолтах подстановка пустая: .jsx прежний байт в байт
    # (golden). typeof-guard: DISC_LEAD объявляется только вместе с зазором стиля.
    _disc_lead_code = ('if (typeof DISC_LEAD!=="undefined"){ try{ dd.autoLeading=false;'
                       ' dd.leading=DISC_LEAD; }catch(e){} }')
    disc_lead_decl = (", DISC_LEAD=%g" % disc_lead) if disc_lead is not None else ""
    disc_lead_js = ("" if disc_lead is None else _disc_lead_code + "\n        ")
    disc_lead_js_tail = ("" if disc_lead is None else "\n    " + _disc_lead_code)
    # служебное для сборки: готовые токены шаблона (не входят в контракт плана)
    plan["_ae"] = dict(
        w=meta["w"], h=meta["h"], fps=meta["fps"], dur=meta["dur"] / meta["fps"],
        name=_js(meta["name"]), cams=cams_js, subs=subs_js, cam1scale=cam1scale_js,
        cam1_ease=cam1_ease_js,
        cam1hold="true" if _c1zoom == "jump" else "false",
        cam1_fit=float(st.get("cam1_fit") or 100),
        intro_scale=float(st.get("intro_scale") or 100), intro_y=float(st.get("intro_y") or 0),
        intro_y2=float(st.get("intro_y2") or 0), intro_on2=_jd(_intro_on2),
        # Подъём интро над видеовставкой: все подстановки пустые, когда front выключен.
        intro_front_decl=_intro_front_decl,
        intro_front_arr_decl=_intro_front_arr_decl,
        intro_front_route=_intro_front_route,
        intro_front_raise=_intro_front_raise,
        # Подъём интро над рото по положению (задание C): подстановки непустые только
        # при галке стиля и группе в нижней половине кадра, иначе .jsx прежний (golden).
        intro_above_roto_decl=_intro_above_roto_decl,
        intro_above_roto_arr_decl=_intro_above_roto_arr_decl,
        intro_above_roto_route=_intro_above_roto_route,
        intro_above_roto_raise=_intro_above_roto_raise,
        # Y базовых линий строк интро (задание A1): непусто при строках заднего плана
        # или якоре «first», иначе пусто — .jsx прежний байт в байт (golden).
        intro_ly_decl=_intro_ly_decl,
        sub_hide=_jd(sub_hide),
        sub_comp_name=_js(sub_comp_name),
        # готовые iDy каждой группы (задание Q2): шаблон больше не считает опускание
        # под INTRO_SAFE_TOP сам — берёт число, как берёт INS_C2_Y. Превью читает то же
        # из plan.intro[].y, поэтому база интро живёт в одном месте.
        intro_idy=_jd(intro_idy),
        # Окна фейд-аута прекомпов с глитчем (ПРАВКА 3/4): подстановки непустые только
        # при глитче в ролике, иначе .jsx прежний (golden).
        intro_fx_decl=_intro_fx_decl,
        intro_fx_out=_intro_fx_out,
        # Макет спикера (задание Q): точка наезда Камеры 1 и точка покоя вставок Кам2,
        # сдвиг интро по X. Дефолты пустые подстановки — .jsx прежний (golden).
        # Камера 1: якорь и позиция нула считаются от точки наезда (cx/cy доли кадра).
        # При дефолте 0.5/0.5 это ровно то, что AE ставит сам, — кода нет вовсе.
        cam1_cx=cam1_cx, cam1_cy=cam1_cy,
        cam1_anchor=("" if (cam1_cx == 0.5 and cam1_cy == 0.5) else
                     ("\n    // точка наезда камеры (задание Q): anchor+position от неё, "
                      "неподвижна именно она\n"
                      "    if(cam1null){ cam1null.property(\"ADBE Transform Group\").property(\"ADBE Anchor Point\").setValue([%g,%g]);"
                      " cam1null.property(\"ADBE Transform Group\").property(\"ADBE Position\").setValue([%g,%g]); }"
                      % (cam1_cx * meta["w"] - meta["w"] / 2, cam1_cy * meta["h"] - meta["h"] / 2,
                         cam1_cx * meta["w"], cam1_cy * meta["h"]))),
        # Рото привязывается к нулу ПОСЛЕ того, как нул получил якорь точки наезда и
        # ключи зума (задание BK). AE при присвоении parent сохраняет мировое положение
        # слоя и пересчитывает локальную Position ребёнка под трансформ нула на ТЕКУЩИЙ
        # момент — без принудительной позиции рото уезжает на смещение точки наезда от
        # центра кадра (Scale рядом уже перезадаётся по той же причине). При дефолтной
        # точке 0.5/0.5 смещения нет — подстановки пустые, .jsx прежний (golden).
        roto_pos_cc=("" if (cam1_cx == 0.5 and cam1_cy == 0.5) else
                     ("\n            // AE компенсирует позицию при привязке по трансформу нула на"
                      "\n            // текущий момент, а нул уже несёт якорь точки наезда и ключи зума"
                      "\n            try{ cc.property(\"ADBE Transform Group\").property(\"ADBE Position\").setValue([0,0]); }catch(e){}")),
        roto_pos_mk=("" if (cam1_cx == 0.5 and cam1_cy == 0.5) else
                     ("\n            try{ mk.property(\"ADBE Transform Group\").property(\"ADBE Position\").setValue([0,0]); }catch(e){}")),
        # вставки Кам2: точка покоя по X и Y в px (в стиле insert_c2_x/y, долями кадра).
        # Дефолт 0.5/0.172 — X остаётся W/2, Y как INS_C2_Y_FR*H: объявление INS_C2_X
        # и подстановка в позицию пустые, .jsx прежний (golden).
        ins_c2x=plan["ins_c2x"], ins_c2y=plan["ins_c2y"],
        ins_c2x_decl=(", INS_C2_X=%d" % plan["ins_c2x"] if ins_c2x != 0.5 else ""),
        ins_c2x_pos=("INS_C2_X" if ins_c2x != 0.5 else "W/2"),
        # интро: сдвиг по X (px), дефолт 0 — подстановка «0» даёт прежнюю строку [0,INTRO_Y]
        intro_x_js=("%g" % intro_x_px if intro_x_px else "0"),
        intro_x_p=("+%g" % intro_x_px if intro_x_px else ""),
        music=_js(music_path) if music_path else '""', music_db=music_db,
        voice_db=float(st.get("voice_db") or 0),
        audio_fade=(0.010 if st.get("audio_fades", True) else 0.0),
        riser=_js(riser) if riser else '""',
        pop=_js(pop) if pop else '""', censor=censor_js, intro_groups=intro_groups_js,
        # Звуки с обрезкой/точкой удара/громкостью (задание AA): дефолты = прежние
        # JS-строки, .jsx не меняется (golden). При заданных ключах — готовые фрагменты.
        pop_place=pop_place, pop_tail=pop_tail,
        glitch_sfx=glitch_sfx,
        wsfx_place=wsfx_place, wsfx_tail=wsfx_tail,
        riser_place=riser_place, riser_tail=riser_tail,
        trans_place=trans_place, trans_tail=trans_tail,
        intro_font=_js(intro_font_ps), intro_hl_font=_js(intro_hl_font_ps),
        intro_mode=_js(intro_mode or "word"),
        # Акцентный шрифт интро (задание R): если ни одна строка не отмечена галкой
        # или accent_font пуст — все три подстановки пустые и .jsx прежний (golden).
        accent_params=(",af" if _accent_used else ""),
        accent_font_pick=('(af||(col=="yellow"?INTRO_HL_FONT:INTRO_FONT))' if _accent_used
                          else '(col=="yellow"?INTRO_HL_FONT:INTRO_FONT)'),
        accent_call=(",ln.accent_font" if _accent_used else ""),
        # Цвета текста интро (новые ключи стиля): hl_fill3 (color=="accent"), свой
        # intro_fill/intro_hl_fill и цвет строки color=="custom" (fill_call, ln.fill).
        # Дефолты — все подстановки пустые/прежние, .jsx не меняется ни на байт (golden).
        hlfill3_decl=_hlfill3_decl,
        intro_fill_decl=_intro_fill_decl,
        fill_params=_fill_params,
        fill_call=_fill_call,
        intro_fill_pick=_intro_fill_pick,
        # Тень на каждом слове интро (intro_shadow): выключено — пустые подстановки.
        intro_shadow_decl=_intro_shadow_decl,
        intro_word_shadow_fn=_intro_word_shadow_fn,
        intro_word_shadow_line=_intro_word_shadow_line,
        intro_word_shadow_word=_intro_word_shadow_word,
        intro_anim_fx_fn=_intro_anim_fx_fn,
        intro_hl_glow_fn=_intro_hl_glow_fn,
        intro_group_flags=_intro_group_flags,
        intro_line_anim=_intro_line_anim,
        intro_word_anim=_intro_word_anim,
        intro_comp_glow=_intro_comp_glow,
        # Тень прекомпа интро (задание B): дефолты — ровно прежняя строка dropShadow(iL, 68)
        # и пустое объявление (golden); иначе — функция introCompShadow + вызов по камере.
        intro_comp_shadow=_intro_comp_shadow,
        intro_comp_shadow_fn=_intro_comp_shadow_fn,
        intro_line_layout=_intro_line_layout,
        intro_back_scale_fn=_intro_back_scale_fn,
        intro_back_scale_line=_intro_back_scale_line,
        intro_back_scale_line_w=_intro_back_scale_line_w,
        intro_back_scale_tmp=_intro_back_scale_tmp,
        intro_back_scale_word=_intro_back_scale_word,
        intro_back_scale_wpx=_intro_back_scale_wpx,
        intro_glow=float(st.get("intro_glow") if st.get("intro_glow") is not None else 1.0),
        exposure=float(exposure or 0), roto="[]",
        inserts=inserts_js, trans=_js(trans) if trans else '""',
        trans_sfx=_js(trans_sfx) if trans_sfx else '""',
        hl_rise=_hl_rise, hl_step=_hl_step,
        hl_ease_out=HL_EASE_OUT, hl_ease_in=HL_EASE_IN,
        ease_default=EASE_DEFAULT,
        disclaimer=_js_multiline(disclaimer) if disclaimer else '""',
        # Кегль дисклеймера строкой: целое 47 печатается ровно «47» (было %d), ужатый под
        # ширину кадра кегль — «42.85». DISC_LEAD — только при зазоре строк в стиле.
        disc_end=disc_sec, disc_size=("%g" % disc_size),
        disc_lead_decl=disc_lead_decl, disc_lead_js=disc_lead_js,
        disc_y=int(meta["h"] * 0.764),
        # Размытие на старте (задание S): Adjustment Layer поверх всего + Gaussian Blur,
        # ключи start_blur -> 0 за start_blur_dur. Выключено (start_blur=0) — пусто.
        start_blur=start_blur,
        blur_js=("" if start_blur <= 0 else
                 "\n    // размытие на старте (задание S): Adjustment Layer поверх всего,"
                 "\n    // Gaussian Blur %(sb)g -> 0 за %(sd)g c" % {"sb": start_blur, "sd": start_blur_dur}
                 # addAdjustmentLayer в API After Effects НЕТ (есть add/addNull/addSolid/
                 # addText/addCamera/addLight/addShape) — корректирующий слой это солид с
                 # флагом adjustmentLayer. И matchName эффекта — «ADBE Gaussian Blur 2»,
                 # с пробелом: он снят с живого проекта (sample1.inspect.json). Оба промаха
                 # роняют сборку в AE, а node --check их не видит — синтаксис-то верный.
                 + "\n    var sbl=main.layers.addSolid([1,1,1], \"Размытие на старте\", W, H, 1);"
                   "\n    sbl.adjustmentLayer=true;"
                   "\n    var sbe=sbl.property(\"ADBE Effect Parade\").addProperty(\"ADBE Gaussian Blur 2\");"
                   "\n    sbe.property(\"ADBE Gaussian Blur 2-0001\").setValueAtTime(0, %(sb)g);"
                   "\n    sbe.property(\"ADBE Gaussian Blur 2-0001\").setValueAtTime(%(sd)g, 0);"
                   "\n    try{ sbl.moveToBeginning(); }catch(e){}"
                   % {"sb": start_blur, "sd": start_blur_dur}),
        # Хвостовой дисклеймер (задание S): копия головного на конец контента, держится
        # 1 с, гаснет за 0.35 — та же раскладка ключей, что у головного, со сдвигом.
        # Выключено (нет галки или текст пуст) — пусто; композиция не удлиняется.
        disc_end_js=("" if not disc_end_on else
                     "\n    // дисклеймер в конце (задание S): копия головного на конец контента"
                     "\n    var dle=main.layers.addText(DISCLAIMER);"
                     "\n    var dsp=dle.property(\"ADBE Text Properties\").property(\"ADBE Text Document\");"
                     "\n    var dd=dsp.value; dd.resetCharStyle(); dd.resetParagraphStyle(); dd.text=DISCLAIMER;"
                     "\n    try{dd.font=FONT;}catch(e){} dd.fontSize=DISC_SIZE; dd.fillColor=[1,1,1]; dd.applyFill=true;"
                     "\n    try{dd.justification=ParagraphJustification.CENTER_JUSTIFY;}catch(e){}"
                     + disc_lead_js_tail +
                     "\n    dsp.setValue(dd);"
                     "\n    dle.property(\"ADBE Transform Group\").property(\"ADBE Position\").setValue([W/2, DISC_Y]);"
                     "\n    dle.inPoint=DUR;"
                     "\n    var dop=dle.property(\"ADBE Transform Group\").property(\"ADBE Opacity\");"
                     "\n    dop.setValueAtTime(DUR+DISC_END-0.35, 100); dop.setValueAtTime(DUR+DISC_END, 0);"
                     "\n    dle.outPoint=DUR+DISC_END;"
                     "\n    try{ var gg=dle.property(\"ADBE Effect Parade\").addProperty(\"ADBE Glo2\");"
                     "\n         try{gg.property(\"Glow Radius\").setValue(42);}catch(e){} }catch(e){}"
                     "\n    try{ dle.moveToBeginning(); }catch(e){}"),
        # композицию удлиняем ровно на длительность хвостового дисклеймера, иначе слой
        # окажется за краем и человек его не увидит; выключено — пустая подстановка
        comp_dur=("+%.4g" % disc_sec) if disc_end_on else "",
        fsize=_fsize,
        posy=_posy,
        font=_js(font_ps), hl_font=_js(hl_font_ps), hlfill=_fill_js(hl_fill),
        fill=_fill_js(sub_fill if sub_fill else [1, 1, 1]),
        hl_bold=("true" if st.get("hl_bold") else "false"),
        sh_op=68, sh_dir=181, sh_dist=5, sh_soft=44,
        ins_fx=_js(st.get("insert_fx") or "card"),
        # Задание FC: «none»-вставки без анимации и без эффектов. Подстановки при
        # дефолтах (zoom/card/white) дают ровно прежний текст шаблона — .jsx не меняется
        # (golden); при none — пусто: ни вызова insFX, ни маски, ни wiggle.
        insfx_cam1=("insFX(L,\"cam1\");" if (st.get("insert_fx") or "card") != "none" else ""),
        insfx_cam2=("insFX(L,\"cam2\");" if (st.get("insert_fx") or "card") != "none" else ""),
        ins_wiggle=(
            "try{ L.property(\"ADBE Transform Group\").property(\"ADBE Position\").expression=\"wiggle(1,15)\"; }catch(e){}  // лёгкое дрожание"
            if _insert_anim != "none" else ""),
        ins_mask=(
            "if (INS_FX!=\"white\"){                          // маска-скругление только у нового вида\n"
            "            var ph=H; try{ if(pit.width&&pit.height) ph=pit.height*(W/pit.width); }catch(e){}   // высота фото в прекомпе (тянуто под ширину)\n"
            "            var mh=ph, mw=W;\n"
            "            // квадратная карточка: режем по меньшей стороне. Ультравайд (артерия, схемы) в квадрат\n"
            "            // не лезет — теряется смысл картинки, такие оставляем целиком по ширине.\n"
            "            if (mh>0 && W/mh <= INS_MASK_SQUARE_AR){ var side=Math.min(W, mh); mw=side; mh=side; }\n"
            "            // ручная правка формы карточки (поля «Маска Ш/В» в UI, % от авто): авторасчёт\n"
            "            // квадратит всё подряд, а у половины картинок предмет в квадрат не помещается.\n"
            "            // Больше самого фото маску не растягиваем — за его краем в прекомпе пусто.\n"
            "            mw = Math.max(20, Math.min(W,  mw*(ins.mw||100)/100));\n"
            "            mh = Math.max(20, Math.min(ph, mh*(ins.mh||100)/100));\n"
            "            roundMask(L, (W-mw)/2, Math.max(0,(H-mh)/2), (W+mw)/2, Math.min(H,(H+mh)/2), INS_MASK_R); }"
            if (st.get("insert_fx") or "card") != "none" else ""),
        ins_c1on2_x=float(st.get("insert_c1on2_x") or 0),
        ins_c1on2_y=float(st.get("insert_c1on2_y") or 0),
        sub_loop=sub_loop,
        sub_shadow_js=sub_shadow_js,
        sub_bg_js=sub_bg_js,
        layer_order=_jd(list(st.get("layer_order") or ["subs", "video", "roto", "photo", "intro"])),
        sub_bg_null_anchor=("    nullAnchor = bgLayer;\n" if sub_bg_on else ""),
        # Масштаб слоя прекомпа субтитров (задание FE). При 100 — пусто, .jsx прежний
        # (golden). При другом значении: якорь и позицию слоя прекомпа переносим в точку
        # строки [W/2, POSY] (иначе масштаб от центра кадра утащит строку к середине и
        # sub_y начнёт врать), Scale = sub_scale. Плашка (bgLayer) — ОТДЕЛЬНЫЙ shape-слой:
        # её прямоугольник нарисован вокруг ЛОКАЛЬНОГО (0,0), поэтому якорь — локальная
        # координата точки масштабирования [0, POSY-BG_Y] (BG_Y = исходная Position.y
        # плашки), позиция — в ту же экранную точку [W/2, POSY]. Формула экрана
        # Position+(P_local-Anchor)*Scale при s=1 даёт ровно BG_Y (ничего не сдвинулось),
        # при s<1 плашка подтягивается к строке пропорционально — как в превью.
        sub_scale_js=(
            ("\n    // масштаб субтитров (задание FE): якорь и позиция — в точку строки, "
             "иначе масштаб от центра кадра утащит строку к середине и sub_y начнёт врать\n"
             "    subLayer.property(\"ADBE Transform Group\").property(\"ADBE Anchor Point\").setValue([SW/2, POSY]);\n"
             "    subLayer.property(\"ADBE Transform Group\").property(\"ADBE Position\").setValue([W/2, POSY]);\n"
             "    subLayer.property(\"ADBE Transform Group\").property(\"ADBE Scale\").setValue([SUB_SCALE,SUB_SCALE]);\n"
             "    // плашка — shape-слой: прямоугольник вокруг локального (0,0), якорь — её "
             "локальная точка масштабирования\n"
             "    try{ bgLayer.property(\"ADBE Transform Group\").property(\"ADBE Anchor Point\").setValue([0, POSY-BG_Y]); }catch(e){}\n"
             "    try{ bgLayer.property(\"ADBE Transform Group\").property(\"ADBE Position\").setValue([W/2, POSY]); }catch(e){}\n"
             "    try{ bgLayer.property(\"ADBE Transform Group\").property(\"ADBE Scale\").setValue([SUB_SCALE,SUB_SCALE]); }catch(e){}"
             ).replace("SUB_SCALE", "%g" % sub_scale).replace("POSY", "%d" % _posy)
             .replace("BG_Y", "%g" % (sub_bg_y if sub_bg_on else 0.0))
            if sub_scale != 100.0 else ""),
        top_line_js=top_line_js,
        caption_js=caption_js)
    if sub_words_per_row > 1:
        plan["sub_step"] = _sub_step
    return plan


def _roto_js(plan, xml_path, kw, emit, cancel):
    """Рото-маски (GPU, самый долгий этап) по разметке plan.roto. `roto` выкл -> "[]".
    Превью масок не делает — оно читает ту же разметку из плана сцены (задание C/D)."""
    if not kw.get("roto") or not plan.get("roto"):
        return "[]"
    from core import styles as _styles
    st = _styles.resolve(kw.get("style"))
    try:
        from core import roto as _roto
        cams = plan["cams"]
        _plan = [p for p in plan["roto"] if cams[p["ci"]].get("path")]   # нужен исходник камеры
        if st.get("roto_cam1_only", True):     # рото только на кусках Камеры 1 (cam2 без рото)
            _plan = [p for p in _plan if p["ci"] == 0]
        if not _plan:
            return "[]"
        # ОБЩИЙ кэш масок (имена по хэшу камера+фрагмент+низ) — реюз между пересборками
        # и XML: тот же камера+кусок не пересчитывается заново. Overwrite исключён (имена
        # уникальны по содержимому), поэтому одна папка на весь набор.
        base = kw.get("base") or _project_base(xml_path)
        roto_dir = os.path.join(base, "roto", "_cache")
        masks_by_cam = {}                    # маски делаем из ИСХОДНИКА своей камеры
        by_cam = {}
        for p in _plan:
            by_cam.setdefault(p["ci"], []).append(p)
        emit("  · рото: {chunks} кусков по {cams} камере(ам) — самый долгий этап сборки",
             chunks=len(_plan), cams=len(by_cam))
        for ci, ps in by_cam.items():
            masks_by_cam[ci] = _roto.alpha_for_ranges(
                cams[ci]["path"], [(p["src_start"], p["src_end"]) for p in ps],
                os.path.join(roto_dir, "cam%d" % (ci + 1)),
                bottom_pct=float(kw.get("roto_bottom") or 0), device=kw.get("roto_device"),
                emit=emit, cancel=cancel)
        ents = []
        for p in _plan:
            ms = masks_by_cam.get(p["ci"], [])
            m = next((mm for mm in ms if abs(mm["start"] - p["src_start"]) < 0.02), None)
            if m:
                ents.append({"ci": p["ci"], "ts": p["ts"], "te": p["te"],
                             "cs": _r(p["ts"] - p["src_start"]),
                             "scale": p["scale"], "mf": _r(m.get("f") or 1),
                             "mask": m["mask"]})
        return _jd(ents)
    except Cancelled:
        raise                                    # «Стоп» — не «рото пропущен»
    except Exception as ex:
        emit("рото пропущен: {err}", err=str(ex))
        return "[]"
    finally:
        try:
            _roto.release(emit=emit)             # выгрузить RVM из VRAM после сборки
        except Exception:
            pass


def to_ae_full(xml_path, jsx_path=None, return_source=False, emit=console_emit, cancel=None,
               render_dir=None, binpfx="", comps_global=False, comp_name_out=None,
               hl_count=None, hl_joins=None, **kw):
    """Сборка .jsx. Вся арифметика — scene_plan (план сцены, задание C); здесь добавляются
    рото-маски (GPU) и рендер шаблона в файл. Остальные параметры — как в scene_plan.
    cancel — колбэк «нажали Стоп?»; проверяется между этапами и внутри рото (см. Cancelled).
    render_dir — папка вывода для безголового рендера (задание BD): задана — .jsx сам
    ставит очередь рендера, сохраняет .aep и закрывает AE; пусто — ручная сборка как раньше.
    binpfx (задание FH) — префикс бинов панели проекта при сборке набора («стем — »),
    пусто при одиночной сборке (тогда имена бинов прежние — golden).
    comps_global (задание FH) — класть главную композицию в $.global.REELSI_COMPS для
    мастер-скрипта набора; при одиночной сборке False — строка пуста (golden).
    comp_name_out (задание FJ) — список, в который кладётся ИМЯ ГЛАВНОЙ КОМПОЗИЦИИ
    (meta["name"], то самое, по которому om.file пишет .mov). Рендер ждёт файл по нему,
    а не по стему .jsx — на наборе это разные вещи (файл 01_C0233.xml → композиция C0233)."""
    emit = wrap_emit(emit)
    plan = scene_plan(xml_path, emit=emit, cancel=cancel, hl_count=hl_count, hl_joins=hl_joins, **kw)
    if comp_name_out is not None:
        comp_name_out.append(plan.get("name") or os.path.splitext(os.path.basename(xml_path))[0])
    # рото могло оборваться на середине (alpha_for_ranges выходит из цикла по
    # «Стопу») — недосчитанные маски в .jsx писать нельзя
    roto_js = _roto_js(plan, xml_path, kw, emit=emit, cancel=cancel)
    if (cancel or (lambda: False))():
        raise Cancelled()
    emit("  · сборка скрипта")                       # этап для логов (как раньше)
    # aep-путь задаёт Python, а не $.fileName (задание CD): AfterFX зовётся по короткому
    # 8.3-имени .jsx, и вывод имени из $.fileName дал бы .aep с коротким именем
    aep_path = (re.sub(r"\.jsx$", ".aep", jsx_path, flags=re.I)
                if render_dir and jsx_path and not return_source else None)
    ae = dict(plan["_ae"])
    # Префикс бинов панели проекта (задание FH): при сборке набора имена бинов получают
    # приставку стема ролика, иначе в общем проекте «Вставки»/«Субтитры» всех роликов —
    # каша. При одиночной сборке префикс пуст: decl пуст и toBin зовёт bin(n) как раньше —
    # .jsx прежний (golden).
    ae["binpfx_decl"] = ("var BIN_PFX = %s;\n    " % _js(binpfx)) if binpfx else ""
    ae["bin_name"] = "BIN_PFX+n" if binpfx else "n"
    jsx = AE_FULL % dict(ae, roto=roto_js,
                         tail=_render_tail(render_dir, aep_path, comps_global=comps_global),
                         imp_miss=_imp_miss(render_dir))
    nclips = sum(len(c["clips"]) for c in plan["cams"])
    if return_source:                          # для мультифайла «один .jsx на всё»
        return jsx, nclips, len(plan["subs"])
    jsx_path = jsx_path or (os.path.splitext(xml_path)[0] + ".jsx")
    # utf-8-sig: ExtendScript без BOM может прочитать файл в системной кодировке (cp1251)
    open(jsx_path, "w", encoding="utf-8-sig").write(jsx)
    if not return_source and plan.get("subs"):
        from core.subs import write_srt
        srt_path = os.path.splitext(jsx_path)[0] + ".srt"
        write_srt(plan["subs"], srt_path)
        xml_srt = os.path.splitext(xml_path)[0] + ".srt"
        if os.path.abspath(xml_srt) != os.path.abspath(srt_path):
            try:
                write_srt(plan["subs"], xml_srt)
            except Exception:
                pass
    return jsx_path, nclips, len(plan["subs"])


def write_srt_for(xml_path, srt_path=None, **kw):
    """Собрать план сцены для XML и записать .srt файл рядом с XML (или по указанному пути)."""
    srt_path = srt_path or (os.path.splitext(xml_path)[0] + ".srt")
    plan = scene_plan(xml_path, **kw)
    if plan.get("subs"):
        from core.subs import write_srt
        write_srt(plan["subs"], srt_path)
        return srt_path
    return None


def virtual_edl(xml_path, ncams=None):
    """Виртуальный EDL финального XML: что реально видно/слышно на таймлайне.
    Возвращает dict: fps,w,h,dur(сек), cams=[{name,path}],
    segs=[{ci,ts,te,src}] (видео: верхняя включённая дорожка побеждает, сек),
    audio=[{ts,te,src}] (звук ВСЕГДА с камеры 1), words=[{s,e,w}] (сек).
    Используется предпросмотром (/api/aicut_preview) и draft-рендером."""
    meta, cams, subs, _ins = parse_full(xml_path, ncams=ncams)
    fps = meta["fps"] or 60
    raw = []
    for ci, c in enumerate(cams):
        if not c.get("path"):
            continue
        for (s, e, i, o, en, *rest) in c["clips"]:
            if en and e > s:
                raw.append((s, e, ci, i))       # ci третьим — этого ждёт cover_sweep
    segs = []
    for b0, b1, k in cover_sweep(raw):          # кто виден на отрезке; topmost track wins
        s, _e, ci, i = raw[k]
        src = (i + (b0 - s)) / fps
        if segs and segs[-1]["ci"] == ci and abs(
                segs[-1]["src"] + (segs[-1]["te"] - segs[-1]["ts"]) - src) < 1e-3:
            segs[-1]["te"] = b1 / fps                       # merge contiguous same-cam
        else:
            segs.append({"ci": ci, "ts": b0 / fps, "te": b1 / fps, "src": src})
    audio = []
    if cams and cams[0].get("path"):
        for (s, e, i, o, en, *rest) in sorted(cams[0]["clips"]):
            if en and e > s:
                src = i / fps
                if audio and abs(audio[-1]["src"] + (audio[-1]["te"] - audio[-1]["ts"]) - src) < 1e-3:
                    audio[-1]["te"] = e / fps
                else:
                    audio.append({"ts": s / fps, "te": e / fps, "src": src})
    dur = (meta.get("dur") or 0) / fps or (segs[-1]["te"] if segs else 0)
    return {"fps": fps, "w": meta.get("w"), "h": meta.get("h"), "dur": dur,
            "cams": [{"name": c.get("name"), "path": c.get("path")} for c in cams],
            "segs": segs, "audio": audio,
            "words": [{"s": s / fps, "e": e / fps, "w": w} for (s, e, w) in subs]}


def _imp_miss(render_dir=None):
    """Что делает imp() при пропавшем файле (задание BD). Ручная сборка — alert: человек
    у экрана видит, какой файл не нашёлся. Безголовый прогон (-noui) — alert это модалка,
    которую никто не закроет: процесс зависнет навсегда. Пишем в $.writeln (лог aerender /
    AfterFX) и продолжаем, вернув null — у всех вызовов imp() есть проверка `if(...)`."""
    if not render_dir:
        return 'alert("Не найден файл:\\n"+p);'
    return '$.writeln("Не найден файл: "+p);'


def _render_tail(render_dir=None, aep_path=None, comps_global=False):
    """Хвост .jsx ПОСЛЕ app.endUndoGroup(). Обычный (ручной) режим — просто открыть
    композицию и ничего не сохранять: пользователь сохраняет сам, куда хочет (задание BD).
    render_dir задан — безголовый рендер: очередь рендера ставит СКРИПТ, а не флаги
    aerender (-RStemplate/-OMtemplate; ошибка в них вылезет в середине рендера). Три
    вещи, на которых это ломается:
    - «Untitled 1» — пресет вывода пользователя, лежит ТОЛЬКО в AE 26.2; применяется
      по точной строке. Версия поэтому не подменяется, а ошибка применения оборачивается.
    - Пресет несёт СВОЙ путь вывода: om.file задаём ПОСЛЕ applyTemplate, иначе пресет
      молча перебьёт папку/имя и рендер уедет не туда.
    - Сборка живёт в памяти, а aerender работает по .aep — нужен app.project.save.
    Очередь перед добавлением чистим: иначе отрендерятся и старые элементы.
    aep_path — куда сохранять .aep (задание CD): Python и так знает целевой путь, а
    $.fileName после запуска по короткому имени .jsx вернул бы короткое имя .aep.
    Лог хвоста — в <стем>.aelog.txt рядом с проектом: в -noui наши $.writeln не видны
    вовсе (задание CD), файл же Python читает после возврата AfterFX. Создаётся ПЕРВЫМ
    делом — его отсутствие и есть признак «скрипт не запустился».
    comps_global (задание FH) — собрать композиции в $.global.REELSI_COMPS: мастер-скрипт
    набора evalFile'ит ролики и собирает их главные композиции из этого массива. При
    обычной ручной сборке (False) строка пуста — .jsx прежний (golden)."""
    if not render_dir:
        if comps_global:
            comps_push = (
                "if(!$.global.REELSI_COMPS)$.global.REELSI_COMPS=[];"
                "$.global.REELSI_COMPS.push(main);\n"
                "    if($.global.REELSI_MASTER_LOG){\n"
                "        try{\n"
                "            var _mlog = new File($.global.REELSI_MASTER_LOG);\n"
                "            _mlog.encoding = \"UTF-8\";\n"
                "            _mlog.open(\"a\");\n"
                "            _mlog.writeln(\"таймлайн ok: \" + main.name);\n"
                "            _mlog.close();\n"
                "        }catch(e){ _LOG(\"запись в REELSI_MASTER_LOG: \" + e); }\n"
                "    }\n    "
            )
            return "    %smain.openInViewer();\n    app.endUndoGroup();\n" % comps_push
        return "    main.openInViewer();\n    app.endUndoGroup();\n"
    # File в AE ест и смешанные разделители, но косые — канон; Python-путь копируется в JS-литерал
    render_dir = render_dir.replace("\\", "/").rstrip("/")
    aep = (aep_path or "").replace("\\", "/")
    aelog = re.sub(r"\.aep$", ".aelog.txt", aep)
    return ("    app.endUndoGroup();\n"
            "    // ---- безголовый рендер (задание BD): очередь + save + quit ----\n"
            "    // Лог — ФАЙЛОМ, а не $.writeln: в -noui наши $.writeln не доезжают (задание CD).\n"
            "    // Файл открываем ПЕРВЫМ делом: его отсутствие у Python = «скрипт не запустился».\n"
            "    var _log = new File(%s);\n"
            "    _log.encoding = \"UTF-8\";\n"
            "    _log.open(\"w\");\n"
            "    for (var _pi=0;_pi<_pending.length;_pi++) _log.writeln(_pending[_pi]);   // слив ранних ошибок сборки (задание CE)\n"
            "    _log.writeln(\"REELSI-TAIL: начат\");\n"
            "    try{\n"
            "        // .aep путь задаёт Python (задание CD): короткое имя .jsx не должно\n"
            "        // сдвигать имя проекта. Сохраняем ДО очереди: .aep обязан появиться,\n"
            "        // даже если очередь не собралась — иначе «не сохранил проект» не\n"
            "        // отличить от «сломалось на пресетах» (задание BT).\n"
            "        var f = new File(%s);\n"
            "        try{\n"
            "            app.project.save(f);\n"
            "            _log.writeln(\"save#1: ok\");\n"
            "        }catch(e){\n"
            "            _log.writeln(\"первый save не выполнился: \" + e);\n"
            "        }\n"
            "        var rq0 = app.project.renderQueue;\n"
            "        while (rq0.numItems > 0) rq0.item(rq0.numItems).remove();   // очередь чистим: aerender рендерит всё, что в ней\n"
            "        var rq = rq0.items.add(main);\n"
            "        try{\n"
            "            rq.applyTemplate(\"Best Settings\");\n"
            "            _log.writeln(\"applyTemplate('Best Settings'): ok\");\n"
            "        }catch(e){\n"
            "            _log.writeln(\"applyTemplate('Best Settings') не применился: \" + e);\n"
            "        }\n"
            "        var om = rq.outputModule(1);\n"
            "        try{\n"
            "            om.applyTemplate(\"Untitled 1\");\n"
            "            _log.writeln(\"applyTemplate('Untitled 1'): ok\");\n"
            "        }catch(e){\n"
            "            _log.writeln(\"applyTemplate('Untitled 1') не применился: \" + e);\n"
            "        }\n"
            "        try{\n"
            "            var _out = new File(%s + \"/\" + main.name + \".mov\");   // ПОСЛЕ applyTemplate: пресет несёт свой путь\n"
            "            if(_out.exists){ _out.remove(); _log.writeln(\"перезаписан: \" + main.name + \".mov\"); }\n"
            "            om.file = _out;\n"
            "            _log.writeln(\"om.file: ok\");\n"
            "        }catch(e){\n"
            "            _log.writeln(\"установка om.file не вышла: \" + e);\n"
            "        }\n"
            "        try{\n"
            "            app.project.save(f);   // второй раз: в .aep должна попасть очередь, иначе aerender отрендерит пустоту\n"
            "            _log.writeln(\"save#2: ok\");\n"
            "        }catch(e){\n"
            "            _log.writeln(\"второй save не выполнился: \" + e);\n"
            "        }\n"
            "        _log.writeln(\"rq.numItems=\" + rq0.numItems);\n"
            "        _log.writeln(\"exists=\" + f.exists);\n"
            "    }catch(e){\n"
            "        _log.writeln(\"непредвиденная ошибка хвоста: \" + e);\n"
            "    }finally{\n"
            "        _log.close();   // сбросить буфер ДО app.quit, иначе Python прочитает пустой файл\n"
            "        app.quit();   // в finally: иначе исключение оставит AE висеть процессом\n"
            "    }\n") % (_js(aelog), _js(aep), _js(render_dir))


def build_combined(jobs, out_jsx, emit=None, cancel=None, progress=None,
                   comps_global=False, comp_names_out=None):
    """jobs: list of dicts, each = {"xml_path": ..., **to_ae_full kwargs}. Concatenate
    every file's build script into ONE .jsx that creates several comps in one AE project.

    emit/progress/cancel — чтобы «один .jsx на всё» был виден и останавливаем так же,
    как поштучная сборка: раньше эта ветка молчала весь прогон (в логе одна строка на
    старте и одна в конце) и «Стоп» не проверяла вовсе.

    comps_global (задание C) — собрать таймлайны так, чтобы мастер-скрипт рендера мог
    выполнить этот ОДИН файл: каждый таймлайн получает comps_global=True (главная
    композиция кладётся в $.global.REELSI_COMPS — из чего мастер строит очередь) и
    binpfx="<стем> — " (иначе бины «Вставки»/«Субтитры» всех роликов в одном проекте —
    каша). False по умолчанию: кнопка «Собрать набор» собирает .jsx прежним (golden).
    comp_names_out (задание C) — список, в который складываются ИМЕНА главных
    композиций по порядку таймлайнов (как в to_ae_full через свой comp_name_out: имя
    композиции ≠ стем файла, а .mov рендер ждёт именно по имени)."""
    emit = wrap_emit(emit)
    cancel = cancel or (lambda: False)
    parts = []
    for i, j in enumerate(jobs, 1):
        if cancel():
            raise Cancelled()
        stem = os.path.splitext(os.path.basename(j["xml_path"]))[0]
        emit("[{cur}/{total}] {stem} — сборка таймлайна…", cur=i, total=len(jobs), stem=stem)
        if progress:
            progress(i, len(jobs))
        kw = {k: v for k, v in j.items() if k != "xml_path"}
        kw.setdefault("emit", emit)
        kw["cancel"] = cancel
        if comps_global:
            kw["binpfx"] = stem + " — "
            kw["comps_global"] = True
        if comp_names_out is not None:
            kw["comp_name_out"] = comp_names_out
        src, nc, ns = to_ae_full(j["xml_path"], return_source=True, **kw)
        if j.get("xml_path"):
            # .srt рядом с XML пишет отдельный scene_plan: to_ae_full при
            # return_source=True его не пишет. Без фильтра сюда же уехали бы
            # binpfx/comps_global/comp_name_out — их scene_plan не принимает.
            plan_j = scene_plan(j["xml_path"], **{k: v for k, v in kw.items()
                                                  if k not in ("binpfx", "comps_global",
                                                               "comp_name_out")})
            if plan_j.get("subs"):
                from core.subs import write_srt
                write_srt(plan_j["subs"], os.path.splitext(j["xml_path"])[0] + ".srt")
        emit("  готово: {clips} клипов, {subs} субтитров", clips=nc, subs=ns)
        parts.append(src)
    body = "\n\n// ===== следующий таймлайн =====\n\n".join(parts)
    open(out_jsx, "w", encoding="utf-8-sig").write(body)
    return out_jsx, len(parts)


def build_render_batch(jobs, outdir, render_dir, emit=None, cancel=None):
    """Сборка НАБОРА для одного проекта AE (задание FH): N обычных .jsx (без безголового
    хвоста) + один мастер-скрипт, который evalFile'ит каждый ролик в своём try/catch,
    собирает главные композиции из $.global.REELSI_COMPS в одну очередь рендера и
    сохраняет один .aep. Возвращает (jsx_list, master_path, aep_path).

    Каждый ролик собирается с binpfx="<стем> — " (имена бинов в общем проекте не
    перемешиваются) и comps_global=True (main кладётся в $.global.REELSI_COMPS).
    Одиночная сборка идёт мимо этой функции — там binpfx пуст и .jsx прежний (golden).

    Мастер-скрипт повторяет правила сегодняшнего безголового хвоста дословно:
    лог файлом ПЕРВЫМ делом, om.file ПОСЛЕ applyTemplate (пресет несёт свой путь),
    save проекта ДО очереди и ещё раз ПОСЛЕ (иначе aerender рендерит пустоту)."""
    emit = wrap_emit(emit)
    cancel = cancel or (lambda: False)
    jsx_list = []
    for i, j in enumerate(jobs, 1):
        if cancel():
            raise Cancelled()
        stem = os.path.splitext(os.path.basename(j["xml_path"]))[0]
        emit("[{cur}/{total}] {stem} — сборка таймлайна…", cur=i, total=len(jobs), stem=stem)
        kw = {k: v for k, v in j.items() if k != "xml_path"}
        kw.setdefault("emit", emit)
        kw["cancel"] = cancel
        # обычный .jsx (БЕЗ render_dir — безголовый хвост не нужен, его заменит мастер),
        # с префиксом бинов и сборкой композиций в глобальный массив
        jp = os.path.join(outdir, stem + ".jsx")
        p, nc, ns = to_ae_full(j["xml_path"], jp, binpfx=stem + " — ",
                               comps_global=True, **kw)
        emit("  готово: {clips} клипов, {subs} субтитров", clips=nc, subs=ns)
        jsx_list.append(p)
    aep_path = os.path.join(render_dir, "reelsi_batch.aep")
    master_path = os.path.join(render_dir, "render_master.jsx")
    _write_master(jsx_list, master_path, aep_path, render_dir)
    return jsx_list, master_path, aep_path


def _write_master(jsx_list, master_path, aep_path, render_dir):
    """Мастер-скрипт набора: лог первым делом, evalFile каждого ролика в своём try/catch,
    очередь из всех композиций, save ДО и ПОСЛЕ, quit в finally."""
    render_dir = render_dir.replace("\\", "/").rstrip("/")
    aep = aep_path.replace("\\", "/")
    aelog = re.sub(r"\.aep$", ".aelog.txt", aep)
    files = ",\n        ".join(_js(p.replace("\\", "/")) for p in jsx_list)
    jsx = (
        "// Reelsi -> мастер набора (задание FH): N роликов в ОДИН проект AE, один aerender.\n"
        "// Лог — файлом ПЕРВЫМ делом: его отсутствие у Python = «скрипт не запустился».\n"
        "$.global.REELSI_MASTER_LOG = %s;\n"
        "var _log = new File($.global.REELSI_MASTER_LOG);\n"
        "    _log.encoding = \"UTF-8\";\n"
        "    _log.open(\"w\");\n"
        "    _log.writeln(\"REELSI-MASTER: начат\");\n"
        "try{\n"
        "    $.global.REELSI_COMPS = [];\n"
        "    var _files = [\n"
        "        %s\n"
        "    ];\n"
        "    // 1) каждый ролик — evalFile в СВОЁМ try/catch: упавший не мешает остальным.\n"
        "    //    ExtendScript буферизует файл-лог до close(), поэтому лог мастера ЗАКРЫВАЕМ\n"
        "    //    перед каждым $.evalFile (чтобы дочерний скрипт мог дописать «таймлайн ok:»)\n"
        "    //    и открываем заново на дозапись ПОСЛЕ: два открытых дескриптора одного файла\n"
        "    //    затрут строки друг друга (задания FJ, GN).\n"
        "    for (var _fi=0; _fi<_files.length; _fi++){\n"
        "        _log.close();\n"
        "        var _evalErr = null;\n"
        "        try{\n"
        "            $.evalFile(new File(_files[_fi]));\n"
        "        }catch(e){\n"
        "            _evalErr = e;\n"
        "        }\n"
        "        _log = new File($.global.REELSI_MASTER_LOG);\n"
        "        _log.encoding = \"UTF-8\";\n"
        "        _log.open(\"a\");\n"
        "        if (_evalErr){\n"
        "            _log.writeln(\"evalFile ОШИБКА: \" + _files[_fi] + \" — \" + _evalErr);\n"
        "        } else {\n"
        "            _log.writeln(\"evalFile ok: \" + _files[_fi]);\n"
        "        }\n"
        "        _log.close();\n"
        "        _log = new File($.global.REELSI_MASTER_LOG);\n"
        "        _log.encoding = \"UTF-8\";\n"
        "        _log.open(\"a\");\n"
        "    }\n"
        "    // 2) очередь: чистим (aerender рендерит всё, что в ней), save ДО очереди —\n"
        "    // .aep обязан появиться, даже если очередь не собралась\n"
        "    var rq0 = app.project.renderQueue;\n"
        "    while (rq0.numItems > 0) rq0.item(rq0.numItems).remove();\n"
        "    var f = new File(%s);\n"
        "    try{\n"
        "        app.project.save(f);\n"
        "        _log.writeln(\"save#1: ok\");\n"
        "    }catch(e){\n"
        "        _log.writeln(\"первый save не выполнился: \" + e);\n"
        "    }\n"
        "    // 3) все собранные композиции — в очередь, каждой Best Settings + Untitled 1;\n"
        "    // om.file ПОСЛЕ applyTemplate: пресет несёт свой путь и молча перебьёт папку\n"
        "    for (var _ci=0; _ci<$.global.REELSI_COMPS.length; _ci++){\n"
        "        var _c = $.global.REELSI_COMPS[_ci];\n"
        "        try{\n"
        "            var rq = rq0.items.add(_c);\n"
        "            rq.applyTemplate(\"Best Settings\");\n"
        "            var om = rq.outputModule(1);\n"
        "            om.applyTemplate(\"Untitled 1\");\n"
        "            var _out = new File(%s + \"/\" + _c.name + \".mov\");\n"
        "            if(_out.exists){ _out.remove(); _log.writeln(\"перезаписан: \" + _c.name + \".mov\"); }\n"
        "            om.file = _out;   // ПОСЛЕ applyTemplate: пресет несёт свой путь\n"
        "            _log.writeln(\"comp ok: \" + _c.name);\n"
        "        }catch(e){\n"
        "            _log.writeln(\"композиция «\" + _c.name + \"» не встала в очередь: \" + e);\n"
        "        }\n"
        "        // тот же приём: сбросить буфер — по comp ok видно, что сборка кончилась\n"
        "        _log.close();\n"
        "        _log = new File($.global.REELSI_MASTER_LOG);\n"
        "        _log.encoding = \"UTF-8\";\n"
        "        _log.open(\"a\");\n"
        "    }\n"
        "    // 4) save ПОСЛЕ очереди: в .aep должна попасть очередь, иначе aerender отрендерит пустоту\n"
        "    try{\n"
        "        app.project.save(f);\n"
        "        _log.writeln(\"save#2: ok\");\n"
        "    }catch(e){\n"
        "        _log.writeln(\"второй save не выполнился: \" + e);\n"
        "    }\n"
        "    _log.writeln(\"rq.numItems=\" + rq0.numItems);\n"
        "    _log.writeln(\"exists=\" + f.exists);\n"
        "}catch(e){\n"
        "    _log.writeln(\"непредвиденная ошибка мастера: \" + e);\n"
        "}finally{\n"
        "    try{ _log.close(); }catch(e){}\n"
        "    $.global.REELSI_MASTER_LOG = null;\n"
        "    app.quit();\n"
        "}\n" % (_js(aelog), files, _js(aep), _js(render_dir))
    )
    open(master_path, "w", encoding="utf-8-sig").write(jsx)
    return master_path
