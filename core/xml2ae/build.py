# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сборка .jsx: to_ae_full (один таймлайн) и build_combined (несколько в один файл).

Здесь же virtual_edl — тот же разбор, но для черновика ffmpeg, без AE.
"""
import json
import os
import re
from typing import Any, Sequence
from core.app_meta import console_emit, wrap_emit
from core.applog import get_logger
from core import fonts as _fonts
from core import frame as _frame
from core import lutbake
from core import voicefx
from core.fileio import atomic_text_write

from .jsutil import _jd, _js
from .layout import (DEFAULT_DISCLAIMER, HL_DUR, INTRO_F_DUR, INTRO_FIT_W, INTRO_SCALE,
                     cover_sweep,
                     _cam_change_frames,
                     _ins_scale,
                     _media_dims,
                     _px_k,
                     _zoom_max)
from . import precompute
from .parse import Cancelled, parse_full
# Числа огибающей звука глитча переехали в plan_audio.py (этап 4 распила scene_plan), но
# остаются контрактом сборки: их берут снаружи (tests/test_intro_anims_and_glitch_sound.py)
# по-прежнему из build — второй копии чисел нет, это тот же объект.
from .plan_assets import AssetsInputs, plan_assets
from .plan_audio import (GLITCH_SFX_ATTACK_S, GLITCH_SFX_HOLD_S,  # noqa: F401
                         GLITCH_SFX_PRE_S, GLITCH_SFX_QUIET_DB, GLITCH_SFX_RELEASE_S,
                         AudioInputs, plan_audio, voice_segments)
from .plan_camera import CameraInputs, plan_camera
# Выражения плашки и подписи переехали в plan_decor.py вместе с кодом, который их зовёт,
# но остаются контрактом сборки: их берут снаружи по-прежнему из build — второй копии нет,
# это те же объекты.
from .plan_decor import (SH_SUB_DIR, SH_SUB_DIST, SH_SUB_OP, SH_SUB_SOFT,  # noqa: F401
                         DecorInputs, _caption_bg_size_expr, _sub_bg_expr, plan_decor,
                         shadows_plan)
from .plan_emph import EmphInputs, plan_emph
from .plan_inserts import (InsertTimingInputs, InsertsInputs, plan_insert_timings,
                           plan_inserts)
from .plan_intro import IntroInputs, _g_at, _grp_big_i, plan_intro
from .plan_intro_tpl import IntroTplInputs, plan_intro_tpl
from .plan_lumetri import LumetriInputs, plan_lumetri
# LUMETRI_PARAMS переехала в plan_ae.py вместе с кодом, который её зовёт, но остаётся
# контрактом сборки: её берут снаружи (tests/test_lumetri.py) по-прежнему из build —
# второй копии таблицы нет, это тот же объект.
from .plan_ae import LUMETRI_PARAMS, AeInputs, plan_ae, plate_tokens  # noqa: F401
from .plan_frame import FrameInputs, apply_frame, plan_frame
from .plan_scene import INTRO_ANIMS, SceneInputs, plan_scene
from .plan_shade import ShadeInputs, plan_shade
# Стиль читается ОДИН раз: структура и её чтение уехали в plan_style.py.
# _sv/_sv_or остаются контрактом сборки: их берут снаружи (tests/test_build_style_defaults.py,
# сторожа распила) по-прежнему из build — второй копии обёрток нет, это те же объекты.
# TRITONE_MAX_LUM/_tritone_on переехали в plan_style.py вместе с кодом, который их
# зовёт, но остаются контрактом сборки: их берут снаружи (tests/test_deepglow_glow.py,
# tests/test_tritone_bright.py) по-прежнему из build — второй копии формулы нет, это те же
# объекты.
from .plan_style import (StyleValues, TRITONE_MAX_LUM, _sv, _sv_or,  # noqa: F401
                         _tritone_on, read_style)
from .plan_subs import SubsInputs, plan_subs
from .plan_words import WordsInputs, plan_words
from .template import AE_FULL
from core.umsg import ReelsiError

log = get_logger(__name__)


def _accent_word(w: Any, case: str) -> str:
    """Регистр слова — единая машинка для акцента интро и регистра субтитров
. Один источник трансформации: .jsx и план читают готовый текст, второй
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


def _intro_line_font(line: dict[str, Any], intro_font_ps: str, intro_hl_font_ps: str) -> str:
    """Шрифт строки интро — ЕДИНСТВЕННАЯ лесенка: акцентный перекрывает;
    жёлтая строка — intro_hl_font, иначе intro_font. Её же используют автофит и
    plan.intro[].fonts; превью своей лесенки не держит (жёлтые строки рисовались
    системным шрифтом и выходили на 41 % шире, чем в AE)."""
    af = (line.get("accent_font") or "").strip()
    if af:
        return af
    if line.get("color") == "yellow":
        return intro_hl_font_ps
    return intro_font_ps


def _intro_fit_ds(lines: list[dict[str, Any]], ts: float, te: float, ds: float, w: int, G: float,
                  cam_keys: Any, fps: float, back_scale: float, intro_font_ps: str,
                  intro_hl_font_ps: str, fsize: int, holds: Any = None, hold: Any = None, big_w: float | None = None,
                  fit_w: float | None = None, both_ways: bool = False, fit_max: float | None = None) -> float:
    """Автофит группы интро: строка видна как lineW·(iSc/100)·G·Z
    (iSc = INTRO_SCALE·ds/100 — масштаб прекомпа, G — общий масштаб интро, Z — зум
    Камеры 1), и ds подбирается так, чтобы эта ширина была ровно fit_w·W.
    Применяется, только когда gs == 100 (рука сильнее автофита — если
    gs != 100, группу масштабировали вручную).
    back_scale приходит готовым числом из структуры стиля (стиль читает
    read_style один раз); шрифты тоже готовыми (intro_font_ps/intro_hl_font_ps),
    свою лесенку автофит не заводит — иначе измерит не тот шрифт, что уйдёт в AE.
    Шрифт не найден — ширины нет, группу не трогаем: подгонять по неизвестной ширине
    хуже, чем не трогать.

    big_w — ширина ВСЕГО блока группы с большой строкой (total из intro_big_layout,
 ): у такой группы по горизонтали видно не самую длинную строку, а блок
    «большое слово + зазор + стопка», и автофит обязан мерить именно его. None (группа
    без большой строки) — прежний максимум по строкам.

    fit_w — доля ширины кадра (0…1), под которую подгоняем. Ручка у КАЖДОЙ камеры своя
    (`intro_margin`/`intro_margin2`, доля = 1 − 2·margin/100), поэтому сюда приходит уже
    выбранное по камере группы число: выбирает его plan_intro — там же, где выбирается
    камера группы, и второй копии правила нет. None — запасная доля INTRO_FIT_W (0.92):
    так зовут автофит прямые вызовы без стиля.

    both_ways — интро откреплено от камеры (intro_cam=False): увеличивать
    его некому (зум Камеры 1 в расчёт не входит, cam_keys пуст), поэтому группа садится
    на ширину в ОБЕ стороны — ds = fit, и короткая строка растягивается, как раньше её
    растягивал зум. Привязанное (both_ways=False) по-прежнему только ужимается: потолок
    ему задаёт ручной gs, а ширину — зум камеры.

    fit_max — «Масштаб интро, %», потолок увеличения ЭТОЙ камеры (intro_fit_max | intro_fit_max2),
    проценты (100…1000): ds = min(fit, fit_max) у откреплённого интро. Режется только
    УВЕЛИЧЕНИЕ: ужатие длинной строки потолком не ограничивается (fit < 100 при любом
    потолке ≥ 100). None — потолка нет: так зовут привязанное интро (ручку оно не читает
    вовсе), и без потолка одно короткое слово раздувалось до 667–819 % (≈780 px высотой)."""
    if not lines:
        return ds
    linew = 0.0
    if big_w is not None:
        linew = float(big_w)
    else:
        for ln in lines:
            ps = _intro_line_font(ln, intro_font_ps, intro_hl_font_ps)
            fs = round(fsize * back_scale) if ln.get("back") else fsize
            wpx = _fonts.text_width(ps, " ".join(ln.get("words") or []), fs)
            if wpx is None:
                return ds
            linew = max(linew, wpx)
    z = _zoom_max(cam_keys, fps, ts, te, holds=holds, hold=hold)
    _fw = INTRO_FIT_W if fit_w is None else float(fit_w)
    fit = 100.0 * w * _fw / (linew * (INTRO_SCALE / 100.0) * G * (z / 100.0))
    if both_ways:
        # Потолок увеличения: min(fit, потолок) — режет рост, ужатие нет.
        return fit if fit_max is None else min(fit, float(fit_max))
    return min(ds, fit)


def _parse_intro_count(text: Any, raw_dec: Any = None) -> tuple[int | float, int, str, str | None] | None:
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
            val: int | float = float(int_clean + "." + frac_part)
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

    # .value обязателен: effect(...)(...) отдаёт объект Property, а .toFixed — метод
    # числа. Без .value выражение падало в AE («toFixed is not a function»), и слой
    # показывал ошибку вместо числа — целые спасал только неявный привод в Math.round.
    if dec == 0:
        expr = 'Math.round(effect("Slider Control")("Slider").value)'
    else:
        if sep == ".":
            expr = f'effect("Slider Control")("Slider").value.toFixed({dec})'
        else:
            expr = f'effect("Slider Control")("Slider").value.toFixed({dec}).replace(".", ",")'

    target = int(val) if (isinstance(val, float) and val.is_integer() and dec == 0) else val
    return target, dec, expr, sep


def _intro_cnt_positions(x: dict[str, Any], nwords: int) -> list[int]:
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


def _has_valid_count(x: dict[str, Any]) -> bool:
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


# Настройки плагина Deep Glow 2 для слов жёлтого глитча интро (
# снято архитектором из MKnew13.aep пользователя, 2026-09-12, 30 потоков со значением,
# одинаковые во всех 28 копиях; порядок — как в слое AE, чтобы списки вроде
# пресета качества ставились раньше зависимых значений).
DEEP_GLOW2_GLITCH = [
    ("PEDG2-0017", 835), ("PEDG2-0018", 0.03), ("PEDG2-0107", 3), ("PEDG2-0118", 10),
    ("PEDG2-0119", 90), ("PEDG2-0042", [1, 0, 0, 1]), ("PEDG2-1048", 3), ("PEDG2-0049", 75),
    ("PEDG2-0050", 0.5), ("PEDG2-0051", 8), ("PEDG2-0053", 1), ("PEDG2-0155", 0),
    ("PEDG2-0131", 50), ("PEDG2-0136", 3), ("PEDG2-0132", 0), ("PEDG2-0057", 0),
    ("PEDG2-0114", 0), ("PEDG2-0115", 0), ("PEDG2-0137", 0), ("PEDG2-0058", 2),
    ("PEDG2-0102", 1), ("PEDG2-0109", 0), ("PEDG2-0143", 1), ("PEDG2-0146", 1),
    ("PEDG2-0059", 0), ("PEDG2-0060", 0), ("PEDG2-0061", 0), ("PEDG2-0062", 0),
    ("PEDG2-0027", 0), ("PEDG2-1107", 6),
]


# Параметры анимаций интро (глитч, раскрытие) уехали в plan_scene.py вместе со словарём
# плана (INTRO_ANIMS, там же и `SUB_GLITCH_*`), но остаются контрактом сборки: их берут
# снаружи (tests/test_intro_last_hold.py, tests/test_plan_intro_tpl_split.py) по-прежнему
# из build — второй копии таблицы нет, это тот же объект (импорт в начале модуля).


def _intro_appear_dur(anim: str | None, count: bool = False) -> float:
    """Сколько играет появление слова интро, с — по ветвям introAnimFX.

    Глитч — своя длительность INTRO_ANIMS (её же ставят селектору текста и ключам
    Opacity); счётчик — HL_DUR (слайдер крутится до него, появление длится F_DUR, берём
    большее); всё остальное (фейд, масштаб, раскрытие, up/left/right) — F_DUR. Числа
    здесь не свои: это ровно те подстановки, что уезжают в introAnimFX.
    """
    if anim == "glitch":
        return float(INTRO_ANIMS["glitch"]["dur"])
    if count:
        return float(HL_DUR)
    return float(INTRO_F_DUR)


def _speaker_frames(xml_path: str) -> dict[str, dict[str, float]]:
    """Поле `frame` профиля спикера ролика: спикер — в сайдкаре рядом с XML.

    Тем же путём его берут формат кадра (`core/frame.output_frame_size`), LUT
    (`core/lutbake`) и обработка голоса (`core/voicefx`). Геометрия рамки — в
    `core/frame.py` (`xml_frames`), здесь только имя, по которому её спрашивают.
    """
    return _frame.xml_frames(xml_path)


def _trans_plan(trans: str, tr_in: float) -> dict[str, Any] | None:
    """Поле плана `trans`: файл перехода видеовставок, сдвиг TR_IN и размер исходника.

    Переход (`Quick 2.mov` из стиля) — ProRes 4K, и браузер его не декодирует: превью
    играет ПРОКСИ, а прокси мельче исходника. «Во всю величину, как слой в AE» по прокси
    не посчитать — поэтому размер едет в плане (w/h читает `_media_dims`, та же дверь,
    что у видеовставок). Размер не прочитался — полей нет: превью возьмёт размер
    элемента у самого файла (переход в mp4 браузер играет исходником, и там он верный).
    """
    if not trans:
        return None
    out: dict[str, Any] = {"media": trans, "in": tr_in}
    wh = _media_dims(trans)
    if wh:
        out["w"], out["h"] = wh
    return out


def scene_plan(xml_path: str, cam1_scale: Any = None,   # None -> авто по сменам кам1→кам2
               music: Any = None, music_db: float = -20.0, music_dir: Any = None, base: Any = None,
               disclaimer: str = DEFAULT_DISCLAIMER, disc_sec: float = 1.35, intro_riser: bool = True,
               highlights: Any = None, hl_breaks: Any = None, hl_count: Any = None, hl_joins: Any = None, inserts: Any = None, censor_audio: bool = True, intro: Any = None,
               intro_remove: Any = None, intro_splits: Any = None, ncams: int | None = None, intro_mode: str = "word",
               roto: bool = False, roto_bottom: float = 0.0, roto_device: Any = None, style: Any = None,
               music_random: bool = False, emit: Any = None,
               include_xml_inserts: bool = True, cancel: Any = None, word_timings: Any = None,
               caption: Any = None, glitch_glow: str = "builtin") -> dict[str, Any]:
    """ПЛАН СЦЕНЫ: вся арифметика сборки, без записи .jsx и без GPU.
    to_ae_full рендерит из него шаблон после рото-масок; предпросмотр
    читает план напрямую. Поля — контракты JSX-структур (CAM/SUBS/INSERTS/INTRO_GROUPS)
    и производные: inserts[].anim (готовые ключи анимаций вставок — раньше их считал
    ExtendScript, остаток ), roto — разметка РОТО-фрагментов (маски делает
    to_ae_full, GPU), zoom.keys/ease — ключи Камеры 1. cancel — колбэк «нажали Стоп?»
    (см. Cancelled); свой emit — туда, где раньше печатали в консоль."""
    _stop = cancel or (lambda: False)
    emit = wrap_emit(emit)

    def _ckpt(stage: str) -> None:
        """Точка между этапами: показать, где мы, и проверить «Стоп»."""
        if stage:
            emit("  · {stage}", stage=stage)
        if _stop():
            raise Cancelled()

    _ckpt("разбор XML")
    # ---- Кадр ролика и диагностика камер вынесены в plan_frame.py (остаток распила) ----
    # Формат из профиля спикера главнее XML (core/frame.ensure_frame): XML пересобирается
    # в новый кадр ДО разбора, если разошлись пропорции. Сюда приходят ВСЕ сборки: .jsx,
    # превью шага 3 (/api/scene), черновик (virtual_edl) и набор. Ошибка «нет камер» —
    # ValueError, а не SystemExit (вызывающие ловят только Exception).
    _fw, _fh = _frame.ensure_frame(xml_path, emit=emit)
    meta, cams, subs, xml_inserts = parse_full(xml_path, ncams=ncams)
    apply_frame(_fw, _fh, meta, cams, emit)
    _fp = meta["fps"]
    # Множитель пиксельных констант раскладки под кадр ролика (layout._px_k: min(W, H)/1080,
    # то же правило, что у вида «size» стиля): им живут карточка вставки, её блюр и вылет,
    # затемнение под интро и коробка карточки для превью. Кадр здесь уже окончательный —
    # формат спикера главнее XML. Считается один раз: второй копии правила нет.
    _px = _px_k(meta["w"], meta["h"])
    inserts = [dict(x) for x in (inserts or [])]       # копии: не мутируем словари вызывающего
    if include_xml_inserts:
        inserts += [dict(
            type=xi["type"], style=xi.get("style") or "cam2", media=xi["media"],
            start_s=xi["start"] // _fp, start_f=xi["start"] % _fp,
            dur_s=(xi["end"] - xi["start"]) // _fp, dur_f=(xi["end"] - xi["start"]) % _fp,
            scale=_ins_scale(xi["media"], xi.get("style") or "cam2", meta["w"], _px), mosaic=False,
            sin=xi.get("sin", 0) / _fp)                # source in-point в секундах
            for xi in xml_inserts]
    from core import styles as _styles  # пресет стиля (шрифт/цвет/звуки/рото/вставки)
    st = _styles.resolve(style)
    # Стиль читается ОДИН раз: дальше scene_plan берёт значения из структуры,
    # а шесть модулей plan_* получают её одним полем style=. Сырой st нужен только на
    # резолв и на дисклеймер/ризер ниже — их же читает и структура.
    # Кадр стиля — кадр ролика (_fw/_fh: формат спикера, `core/frame.ensure_frame`).
    # Стиль задуман в 1080×1920, и в кадре другого формата его числа пересчитываются
    # РОВНО здесь (core/style_geometry.scale_style внутри read_style) — и .jsx, и
    # превью, и черновик берут готовые числа, второй копии правила нет.
    stv = read_style(st, _fw, _fh)
    # Градиент текста и свечение — галка/режим стиля: по ним в план уезжают числа для
    # превью (цвета, угол, сила, радиус). Сами эффекты ставит .jsx (subGrad/subGlow).
    _grad_on = str(stv.sub_fill_mode or "").strip().lower() == "gradient"
    _glow_on = bool(stv.sub_glow_on)
    if stv.disclaimer is not None:                     # стиль переопределяет дисклеймер ("" = скрыть)
        disclaimer = stv.disclaimer
    if stv.intro_riser is not None:                    # стиль может отключить интро-SFX (ризер)
        intro_riser = bool(stv.intro_riser)
    _fps0 = meta["fps"] or 60

    def _active_cam_at(t_sec: float) -> int:
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

    # Точки СМЕНЫ КАМЕРЫ (сек): где показываемая (верхняя включённая) камера меняется. Не каждый
    # VAD-стык внутри одной камеры, а именно переход кам1↔кам2. По ним режем/прижимаем вставки
    # (plan_inserts.py) и ставим звуки переходов (plan_audio.py) — точки
    # считаются здесь ОДИН раз, второй копии правила нет.
    _cam_change_sec = [f / _fps0 for f in _cam_change_frames(cams)]
    # ---- Вставки: тайминги вынесены в plan_inserts.py (этап 3 распила scene_plan) ----
    # Секунды старта/конца (сек+кадры и легаси-кадры), прижим старта к кату, срез окна катом
    # (SNAP_TOL/_clip_end), перенос схлопнувшегося окна в новый шот, тип по файлу и стиль
    # фото (cam1/cam2 по активной камере). Имена после вызова — прежние: `inserts` (его
    # читают `_any_plate` ниже и звук — `has_video` в plan_audio.py) и `_clip_end` — им
    # сборка данных режет окно.
    _ins_t = plan_insert_timings(InsertTimingInputs(
        inserts=inserts, fps=_fps0, cam_change_sec=_cam_change_sec,
        active_cam_at=_active_cam_at, style=stv, emit=emit, comp_w=meta["w"],
        comp_h=meta["h"]))
    inserts, _clip_end = _ins_t.inserts, _ins_t.clip_end
    # ---- Подготовка слов вынесена в plan_words.py (остаток распила scene_plan) ----
    # Разметка (жёлтые hl_raw, brk_raw, cnt_raw, joins_raw = ...) приводится к индексам
    # существующих слов, слова интро уходят из титров с переиндексацией разметки и пословных
    # таймингов, censor_source хранит ВСЕ слова (интро-слова звучат), а тайминги
    # дочитываются из сайдкара `<стем>.words.json`, если их не передали аргументом.
    # Имена ниже — ровно те, что читает остальной scene_plan: перенос построчный,
    # порядок операций не менялся:
    """
    brk_raw = hl_breaks
    cnt_raw = hl_count
    joins_raw = hl_joins
    """
    #
    # Интро собирается и в режиме строк. Прежний запрет («со строками эта
    # связь ещё не продумана») снят: связь как раз прямая — слова интро вынимаются из subs
    # здесь и раньше, чем строятся строки (raw_lines), поэтому строки собираются из
    # оставшихся слов, а интро от режима субтитров не зависит.
    _words = plan_words(WordsInputs(
        subs=subs, highlights=highlights, hl_breaks=hl_breaks, hl_count=hl_count,
        hl_joins=hl_joins, word_timings=word_timings, xml_path=xml_path,
        intro_remove=intro_remove))
    subs, hl, brk, cnt, joins = _words.subs, _words.hl, _words.brk, _words.cnt, _words.joins
    censor_source, word_timings = _words.censor_source, _words.word_timings
    eff_intro = intro or []
    eff_intro_splits = intro_splits or []
    # ---- Папка проекта и ассетов плюс шрифты вынесены в plan_assets.py (остаток распила) ----
    # base/asset_base/aset (assets.json рядом с XML или рядом с установкой) и лесенка
    # шрифтов «пусто = как базовый» с регистрами. Имена ниже — ровно те, что читает
    # остальной scene_plan: перенос построчный, порядок операций не менялся.
    _as = plan_assets(AssetsInputs(base=base, xml_path=xml_path, style=stv))
    base, aset = _as.base, _as.aset
    font_ps, hl_font_ps = _as.font_ps, _as.hl_font_ps
    intro_font_ps, intro_hl_font_ps = _as.intro_font_ps, _as.intro_hl_font_ps
    accent_font_ps, accent_case = _as.accent_font_ps, _as.accent_case
    back_font_ps, back_case = _as.back_font_ps, _as.back_case
    # Цвета и тень текста интро (новые ключи стиля, значения — в stv): третий цвет строки
    # (color=="accent"), свой цвет обычного/выделенного текста интро (intro_fill/
    # intro_hl_fill, None = как сегодня) и пресет тени на КАЖДОМ слове интро (intro_shadow).
    # Дефолты не меняют .jsx ни на байт (golden) — см. _intro_fill_pick/_accent_color_used/
    # _custom_color_used ниже. Тени и свечение слов интро (включая третью и четвёртую
    # двери свечения «glowfix» — intro_hl_glow/intro_comp_glow), тень ПРЕКОМПА, шаг строк
    # и фейд-аут группы — тоже в stv: их читают и план, и
    # plan_intro, и подстановки, второго чтения этих ключей в сборке нет;
    # почему у каждой ручки такой дефолт — в plan_style.py рядом с её полем.
    intro_shadow_on = bool(stv.intro_shadow)
    # Размытие на старте и хвостовой дисклеймер: дефолты = выключено,
    # при них плейсхолдеры шаблона пусты и .jsx не меняется ни на байт (golden).
    disc_end_on = bool(stv.disclaimer_end) and bool(disclaimer)
    # Макет спикера: точка покоя вставок Кам2 и сдвиг интро по X. Дефолты =
    # сегодняшнее поведение (0.5/0.172, 0), при них плейсхолдеры шаблона пусты и .jsx не
    # меняется ни на байт (golden). Точка наезда Камеры 1, сдвиг кадра (pan) и поворот
    # камеры читаются в plan_camera.py — там же и их подстановки. Числа
    # точки покоя вставок лежат в stv (insert_c2_x/y, insert_c1_x/y) — второй копии нет.
    # Подложка фото-вставок: картинку задаёт СТИЛЬ, а решение «эта вставка
    # на подложке» — галка у самой вставки (поле plate). Файла в стиле нет — подложки нет
    # ни у кого: подстановки шаблона пустые и .jsx побайтово прежний (golden).
    _any_plate = bool(stv.plate_path) and any(x.get("plate") for x in inserts)
    # ---- Цвет камер через Lumetri вынесен в plan_lumetri.py (остаток распила) ----
    # Девять значений стиля одной дверью: их читают и .jsx (LUMETRI / applyLumetri),
    # и превью (`plan["lumetri"]`), второй копии правил нет. Экспозиции клипа с шага AE
    # больше нет вовсе (яркость убрана, Lumetri настраивается стилем); ключи читаются
    # ЯВНО — сторож схемы (test_r11_li_every_knob) ищет ручку в коде по её имени.
    _lm = plan_lumetri(LumetriInputs(style=stv))
    lumetri, lumetri2 = _lm.lum, _lm.lum2
    # Цвет мидтонов жёлтой строки — тот самый, что уезжает в подстановку _yellow_expr:
    # своя подстановка intro_hl_fill перебивает hl_fill. Яркость у него ОДНА на двоих
    # по ней не ставится ни Tritone (выбеливает букву), ни Deep Glow
    # (к свечению строки добавляется второе свечение). Второй копии формулы нет — только
    # _tritone_on, читающая TRITONE_MAX_LUM (обе переехали в plan_style.py вместе с кодом,
    # который их зовёт). Цвета в стиле нет вовсе — _tritone_on(None) повторяет подстановку
    # _fill_js(None): тот же стоковый жёлтый styles.BASE (0.87).
    _yellow_dark = _tritone_on(stv.intro_hl_fill if stv.intro_hl_fill is not None
                               else stv.hl_fill)
    # ---- Затемнение под интро вынесено в plan_shade.py (остаток распила) ----
    # Числа слоя-фигуры (класс ключа intro_shade): их же читает предпросмотр
    # (`plan["shade"]`), а подстановку шаблона собирает plan_intro_tpl.shade_js по ЭТИМ
    # числам — второй копии формул и текста нет. Считается до plan_intro: числа затемнения
    # уезжают и в расчёт интро (подстановка шаблона), а от групп интро не зависят вовсе.
    # Выключенная галка — ключа в плане нет, подстановка пустая, .jsx прежний байт в байт
    # (golden).
    shade_plan = plan_shade(ShadeInputs(style=stv, px=_px)).numbers
    # Регистр и цвет базовых субтитров: регистр применяется в scene_plan к
    # ГОТОВОМУ тексту (и .jsx, и превью читают его — второй копии правила нет), цвет
    # уезжает в план для превью и в шаблон как параметр FILL. Дефолты upper/белый —
    # подстановки пустые, .jsx прежний (golden). Оба значения — в stv (sub_case/sub_fill).
    # интро разбиваем на прекомпы по splits (индексы строк-начал новых групп). Режим строк
    # (sub_words_per_row > 1) группы не отменяет: строки субтитров собираются из
    # слов БЕЗ интро, поэтому раскладка субтитров на группы интро не влияет.
    _intro_lines = [x for x in eff_intro if (x.get("words") or (x.get("text") or "").strip())]
    _splits = sorted(set(int(s) for s in eff_intro_splits if 0 < int(s) < len(_intro_lines)))
    _bounds = [0] + _splits + [len(_intro_lines)]
    _intro_groups = [_intro_lines[_bounds[k]:_bounds[k + 1]] for k in range(len(_bounds) - 1)]

    # группы идут по таймингу: первая = самая ранняя (JS считает её началом ролика и держит её с 0).
    # _g_at и _grp_big_i — расчёт интро и живут в plan_intro.py: копии здесь нет.
    _intro_groups.sort(key=_g_at)
    _any_glitch = any(x.get("anim") == "glitch" for g in _intro_groups for x in g)
    # Галка стиля «Deep Glow вместе со свечением строки»: по умолчанию строка
    # жёлтого глитча СО СВЕЧЕНИЕМ (fx=="glow") Deep Glow не берёт — на ней уже висит Glo2
    # свечения строки, и два свечения складывались (владелец гасил плагин руками у 7 слов
    # из 10). Включённая галка — прежнее поведение, слово в слово.
    # Яркий цвет жёлтого (доработка MK3): Deep Glow не берёт НИ ОДНО жёлтое слово
    # глитча — ни без свечения строки, ни с ним, и галка intro_dg_with_glow его не вернёт.
    # Причина та же, что у Tritone: на ярком цвете свечение выталкивает букву в белое, а
    # плагин кладёт сверху второе свечение. Правило одно на любой яркий жёлтый — заданный
    # в стиле (hl_fill/intro_hl_fill) или стоковый: у владельца стоковый жёлтый 0.87 в
    # большинстве стилей, и исключение «только заданный цвет» оставляло правило без дела
    # («уйдёт со всех ярких — не страшно, главное, чтобы на тёмных не уходил»). Тёмный
    # цвет (ниже порога) — правило MK: слово без свечения берёт плагин, со свечением нет.
    _dg_bright = not _yellow_dark
    # Deep Glow нужен, только если его есть куда ставить: жёлтый глитч БЕЗ свечения строки
    # или включённая галка. Иначе .jsx выходит ровно как в режиме «Встроенные» — ни PEDG2,
    # ни DG_MISS (у сборки без подходящих слов плагина нет вовсе, и ругаться не на что).
    _dg_on = glitch_glow == "deepglow2" and not _dg_bright and any(
        x.get("anim") == "glitch" and x.get("color") == "yellow"
        and (stv.dg_with_glow or x.get("fx") != "glow")
        for g in _intro_groups for x in g
    )
    # Строки «заднего плана» в ролике: от этого зависит и ветка раскладки
    # строк в шаблоне, и то, считает ли Python шаги по высоте букв. Акцентная строка
    # задним планом не считается — у неё свой шрифт и свой регистр (тот же приоритет,
    # что в _intro_line_js: accent перебивает back).
    _any_back = any(bool(x.get("back") and not x.get("accent")) for g in _intro_groups for x in g)
    _any_big = any(_grp_big_i(g) is not None for g in _intro_groups)
    # ---- Звук вынесен в plan_audio.py (этап 4 распила scene_plan) ----
    # Материалы и события SFX (поп жёлтых, глитч по группам слов, whoosh и переход на
    # катах, ризер), обрезка/точка удара/громкость (<звук>_in/_out/_at/_db), музыка,
    # цензура голоса, огибающая слоёв глитча и данные звука для шаблона. Имена ниже —
    # ровно те, что читает остальной scene_plan: перенос построчный, порядок операций и
    # подстановки не менялись.
    # Голос камеры 1 после обработки (core/voicefx.py, флаг voice_fx.final у спикера
    # нарезки): файл лежит рядом с XML, и его играют и превью, и проект AE. Пусто —
    # голос не обработан, звук идёт с камеры, как раньше.
    voice_wav = voicefx.final_voice_for_build(xml_path, (cams[0].get("path") or ""), emit=emit)
    # ---- Сила жёлтых вынесена в plan_emph.py (остаток распила scene_plan) ----
    # Сайдкар `<стем>.emph.json` (core/emphasis.py) читает ОДНА дверь: сборка .jsx,
    # превью (`/api/scene`) и черновик видят одни и те же числа. Нумерация слов — та же,
    # что у плана, и текст сообщений о непосчитанном не менялся.
    _emph = plan_emph(EmphInputs(
        cams=cams, subs=subs, hl=hl, intro=intro, intro_splits=intro_splits,
        intro_remove=intro_remove, censor_source=censor_source,
        xml_path=xml_path, meta=meta, style=stv, emit=emit)).emph
    _au = plan_audio(AudioInputs(
        intro_groups=_intro_groups, any_glitch=_any_glitch, inserts=inserts,
        subs=subs, hl=hl, cam_change_sec=_cam_change_sec, fps=_fps0,
        style=stv, aset=aset, intro_riser=intro_riser,
        music=music, music_random=music_random, music_dir=music_dir,
        base=base, xml_path=xml_path,
        # Цензор считаем по ВСЕМ словам (censor_source): интро-слова звучат.
        censor_source=censor_source, censor_audio=censor_audio, censor_fps=meta["fps"],
        # Словарь плана: путь голоса (Камера 1, обработанный — если он запечён) и
        # громкость музыки знает только scene_plan.
        voice_src=voice_wav or (cams[0].get("path") or ""), music_db=music_db,
        # Куски голоса по монтажному времени: клипы камеры 1 в СЕКУНДАХ — их режет
        # рендер без AE. Считает их plan_audio (там же, где окна цензуры и фейды),
        # а клипы камер есть только здесь.
        voice_segments=voice_segments(cams[0].get("clips") or [], _fps0),
        # Общее с другими блоками: точка «музыка» (этап в логе + проверка «Стоп») и лог.
        ckpt=_ckpt, emit=emit))
    riser, pop = _au.riser, _au.pop
    trans, trans_sfx = _au.trans, _au.trans_sfx
    trans_plan = _trans_plan(trans, _au.trans_in)
    music_path = _au.music_path
    censor_js = _au.censor_js
    glitch_sfx = _au.glitch_sfx
    pop_place, pop_tail = _au.pop_place, _au.pop_tail
    wsfx_place, wsfx_tail = _au.wsfx_place, _au.wsfx_tail
    riser_place, riser_tail = _au.riser_place, _au.riser_tail
    trans_place, trans_tail = _au.trans_place, _au.trans_tail
    voice_db, audio_fade = _au.voice_db, _au.audio_fade
    # ---- Камеры плана и рамка кадра вынесены в plan_frame.py (остаток распила) ----
    # Словари камер плана (`plan["cams"]`), готовые подстановки шаблона CAM и три строки
    # рамки кадра спикера. Клип, рото-копия и Basic Motion в Premiere берут ОДНИ и те же
    # числа рамки из одного места — второй копии формулы нет. Имена ниже — ровно те, что
    # читает остальной scene_plan: перенос построчный, порядок операций не менялся.
    # `_media_dims` — дверью из build (как у вставок): размеры исходника читает она, а
    # не своя ссылка модуля — подмена `build._media_dims` в тестах обязана работать.
    _fr = plan_frame(FrameInputs(cams=cams, meta=meta, xml_path=xml_path,
                                 media_dims=_media_dims))
    cams_plan = _fr.cams_plan
    cams_js = _fr.cams_js
    cam_frame_pos, roto_frame_scale, roto_frame_pos = (_fr.cam_frame_pos,
                                                       _fr.roto_frame_scale,
                                                       _fr.roto_frame_pos)

    # ---- Блок субтитров вынесен в plan_subs.py (этап 1 распила scene_plan) ----
    # Слова -> строки -> стопка подряд жёлтых -> появление жёлтых -> данные циклов
    # SUBS/SUB_ROWS/SUB_STACK. Имена ниже — ровно те, что читает остальной код scene_plan:
    # перенос построчный, поведение и подстановки шаблона не менялись.
    _subs = plan_subs(SubsInputs(
        subs=subs, hl=hl, brk=brk, cnt=cnt, joins=joins,
        font_ps=font_ps, hl_font_ps=hl_font_ps,
        width=meta["w"], height=meta["h"], fps=_fps0, cams=cams,
        word_timings=word_timings, style=stv,
        # Общие с другими блоками правила остаются в build.py:
        # регистр слова и разбор числа-счётчика — свои у каждого блока копии не заводятся.
        accent_word=_accent_word, parse_count=_parse_intro_count))
    subs_js, sub_loop = _subs.subs_js, _subs.sub_loop
    hl_row_decl, hl_blur_decl = _subs.hl_row_decl, _subs.hl_blur_decl
    hl_blur_fn, hl_short_fn, hl_blur_on = _subs.hl_blur_fn, _subs.hl_short_fn, _subs.hl_blur_on
    sub_scale, _hl_dur = _subs.sub_scale, _subs.hl_dur
    _posy, _hl_step, _hl_rise = _subs.posy, _subs.hl_step, _subs.hl_rise
    _fsize, _fsize_base, _sub_step = _subs.fsize, _subs.fsize_base, _subs.sub_step
    # ---- Камера вынесена в plan_camera.py (этап 6 распила scene_plan) ----
    # Параметры точки наезда/pan/поворота, ключи зума по режимам («заполнение кадра»
    # умножается на них РОВНО раз), holds, разметка рото и слежение за головой. Дверь
    # одна: зависимого кода между частями камеры нет, а ключи нужны всем — шаблону
    # (CAM1_SCALE), плану (предпросмотр) и автофиту интро (plan_intro.py). Имена ниже —
    # ровно те, что читает остальной scene_plan: перенос построчный, порядок операций
    # внутри камеры (ключи -> fit -> holds -> рото -> слежение) и подстановки не менялись.
    _cam = plan_camera(CameraInputs(
        cams=cams, meta=meta, fps=_fps0, subs=subs, hl=hl, style=stv,
        # Ключи зума из kwarg (None — режим стиля), галка ротоскопа и путь XML
        # (рядом с ним кэш трека головы) — камера решает по ним и разметку, и слежение.
        # Рамка камеры 1 — готовые числа из плана: слежение за головой зажимает сдвиг
        # по КРАЯМ слоя, а с рамкой слой смещён и увеличен (core/frame.frame_shift).
        cam1_scale=cam1_scale, roto=roto, xml_path=xml_path,
        cam1_frame=(cams_plan[0].get("frame") if cams_plan else None),
        # Слова интро, выделенные цветом (color yellow/accent), наезжают наравне с жёлтыми
        # словами ролика. Индексы этих слов берёт intro_remove — ТОТ ЖЕ список, по которому
        # plan_words вынимает слова интро из титров, — а времена начала и конца: subs_all
        # (censor_source, слова ДО вырезания интро). Второй копии сопоставления нет.
        intro_groups=_intro_groups, intro_remove=list(intro_remove or []),
        subs_all=censor_source, emph=_emph))
    cam1_scale, holds = _cam.cam1_scale, _cam.holds
    cam1scale_js, cam1_ease_js = _cam.cam1scale_js, _cam.cam1_ease_js
    cam1holds_js = _cam.cam1holds_js
    cam1_cx, cam1_cy = _cam.cam1_cx, _cam.cam1_cy
    cam1_anchor = _cam.cam1_anchor
    cam2_js = _cam.cam2_js
    cam1_follow_decl, cam1_follow_js = _cam.cam1_follow_decl, _cam.cam1_follow_js
    cam2_follow_decl, cam2_follow_js = _cam.cam2_follow_decl, _cam.cam2_follow_js
    roto_pos_cc, roto_pos_mk = _cam.roto_pos_cc, _cam.roto_pos_mk
    cam1_rot_decl, cam1_rot_cam = _cam.cam1_rot_decl, _cam.cam1_rot_cam
    cam2_rot_decl, cam2_rot_cam = _cam.cam2_rot_decl, _cam.cam2_rot_cam
    roto_rot_cc, roto_rot_mk = _cam.roto_rot_cc, _cam.roto_rot_mk
    # ---- Вставки: данные плана и подстановки вынесены в plan_inserts.py ----
    # Окна показа (_isec/_win), подложка и «без фона», масштабы видео, готовые ключи
    # анимаций (наезд, rise, none, вылет из-за спины) и строка INSERTS. Срез окна катом —
    # то же правило, что в таймингах выше (`_clip_end` из plan_insert_timings).
    _ip = plan_inserts(InsertsInputs(
        inserts=inserts, meta=meta, fps=_fps0, clip_end=_clip_end,
        media_dims=_media_dims, style=stv, emit=emit))
    inserts_plan, inserts_js = _ip.inserts, _ip.inserts_js
    _video_segs = _ip.video_segs

    # Цензура звука — окна мьюта голоса и их JS-литерал — считается в plan_audio.py
    # там же события звуков, с которыми она едет в план и в шаблон.
    # Разметка РОТО (`roto_plan`) — из plan_camera.py: там же правило «нужен исходник
    # камеры», по которому фрагмент не попадает в маски.

    # Новые цвета строки интро: считаем по СЫРЫМ данным групп, а не по _intro_line_js.
    # Используются, только если хоть одна строка в ЭТОЙ сборке реально просит accent/custom
    # (иначе плейсхолдеры шаблона пусты и .jsx не меняется ни на байт, golden).
    _accent_color_used = any(x.get("color") == "accent" for g in _intro_groups for x in g)
    _custom_color_used = any(x.get("color") == "custom" for g in _intro_groups for x in g)
    # ---- Расчёт интро вынесен в plan_intro.py (этап 2 распила scene_plan) ----
    # Окна групп, автофит и ширина блока, безопасная зона, раскладка строк и «большое
    # слева», затухание к субтитру, сжатие появления, камера группы, тень
    # прекомпа и подстановки шаблона. Имена ниже — ровно те, что читает остальной код
    # scene_plan: перенос построчный, поведение и подстановки не менялись.
    _intro = plan_intro(IntroInputs(
        groups=_intro_groups, cams=cams, meta=meta, fps=_fps0,
        active_cam_at=_active_cam_at, video_segs=_video_segs, subs=_subs,
        font_ps=font_ps, intro_font_ps=intro_font_ps, intro_hl_font_ps=intro_hl_font_ps,
        accent_font_ps=accent_font_ps, accent_case=accent_case,
        back_font_ps=back_font_ps, back_case=back_case,
        any_back=_any_back, any_glitch=_any_glitch,
        style=stv,
        # Полка последней группы интро (ключ intro_last_hold) — проброс: читается здесь тем
        # же _sv, что и остальные ключи стиля (запас — styles.BASE), а считает по нему одна
        # формула — layout._intro_group_window (зовут её и план, и подрезка окон).
        intro_last_hold=float(_sv(st, "intro_last_hold")),
        # Общие с другими блоками правила остаются в build.py.
        accent_word=_accent_word, parse_count=_parse_intro_count,
        cnt_positions=_intro_cnt_positions, line_font=_intro_line_font,
        fit_ds=_intro_fit_ds, appear_dur=_intro_appear_dur, anims=INTRO_ANIMS,
        cam1_scale=cam1_scale, holds=holds,
        # Свой зум Камеры 2 (пусто, если она неактивна): автофит интро, выпавшего на
        # перебивку, считает по нему, а не по зумам Камеры 1.
        cam2_scale=_cam.cam2_scale, cam2_holds=_cam.cam2_holds,
        # Камера 2 активна: по ней plan_intro_tpl решает, привязывать ли нул «интро на кам2»
        # к нулу Камеры 2 — второй копии правила нет.
        cam2_active=_cam.cam2_active,
        # Числа затемнения под интро (plan_shade) — по ним собирается подстановка шаблона.
        shade_plan=shade_plan))
    _accent_used = _intro.accent_used

    # ---- Оформление кадра вынесено в plan_decor.py (остаток распила scene_plan) ----
    # Уход субтитров на rise-вставках, имя композиции субтитров, тень и плашка субтитров
    # (с масштабом слоя прекомпа), верхняя строка-прогресс, подпись о ролике и дисклеймер
    # (кегль под ширину кадра и зазор строк). Имена полей результата — те же имена, что
    # были у локальных переменных: их читают план (предпросмотр) и подстановки шаблона.
    # Геометрия полосы субтитров (posy/шаг/подъём) приходит в модуль результатом plan_subs:
    # второй копии этих трёх формул в сборке больше нет.
    # Подпись кадра перенесена в plan_decor.py (capVal.resetCharStyle(); capVal.resetParagraphStyle();).
    _decor = plan_decor(DecorInputs(
        meta=meta, fps=_fps0, inserts=inserts_plan, subs=_subs,
        caption=caption, disclaimer=disclaimer, font_ps=font_ps, style=stv,
        # Хвостовая копия превью: галка уже вместе с «текст не пуст» — тем же условием
        # живёт концевой блок шаблона (disc_end_on), второй копии правила нет.
        disc_sec=disc_sec, disc_end_on=disc_end_on))
    # Слежение за головой (`follow_keys`) и `plan["zoom"]` — из plan_camera.py: кэш
    # `<стем>.head.json` читается там же одной дверью с ключами зума (fit уже внутри них).

    # ---- Сборка словаря плана вынесена в plan_scene.py (остаток распила) ----
    # Поля-исключения (sub_step, sub_bg, sub_wbg, градиент и свечение текста, top_line,
    # caption, disclaimer) модуль дописывает сам — тем же порядком и по тем же галкам,
    # что были здесь: при дефолтах ключа нет вовсе (.jsx и план прежние, golden).
    plan = plan_scene(SceneInputs(
        meta=meta, frame=_fr, ins=_ip, au=_au, cam=_cam, subs=_subs, decor=_decor,
        intro=_intro, style=stv, trans_plan=trans_plan, lumetri=lumetri,
        fsize=_fsize, fsize_base=_fsize_base, sub_scale=sub_scale,
        font_ps=font_ps, hl_font_ps=hl_font_ps,
        posy=_posy, hl_step=_hl_step, hl_rise=_hl_rise, hl_dur=_hl_dur,
        hl_blur_on=hl_blur_on, intro_shadow_on=intro_shadow_on,
        grad_on=_grad_on, glow_on=_glow_on, shade_plan=shade_plan,
        # Число Lumetri Камеры 2 кладётся в план только при разомкнутой связи камер
        # (lm2_link) — при связанной цепочке ключа нет вовсе, как и раньше.
        lm2_link=stv.lm2_link, lumetri2=lumetri2,
        sub_wbg_plan=_subs.sub_wbg_plan, sub_bg_plan=_decor.sub_bg_plan,
        top_line_plan=_decor.top_line_plan, caption_plan=_decor.caption_plan,
        disclaimer_plan=_decor.disclaimer_plan,
        sub_step=_sub_step, sub_words_per_row=stv.sub_words_per_row)).plan
    # ---- Подстановки шаблона интро вынесены в plan_intro_tpl.py (этап 5) ----
    # Готовые строки JS: цвета текста, тень слов/строк и прекомпа, раскладка строк (задний
    # план, якорь «first», «большое слева»), эффекты появления (глитч/Deep Glow/Tritone/
    # свечение) и маршрутизация слоёв над видеовставкой и рото. Имена ниже — ровно те, что
    # читает остальной код scene_plan: перенос построчный, текст подстановок не менялся.
    _itpl = plan_intro_tpl(IntroTplInputs(
        groups=_intro_groups, intro=_intro,
        # Точка масштабирования прекомпа (intro_scale_anchor): режим и готовые числа на
        # группу посчитал plan_intro — здесь только проброс ключей, второй копии нет.
        # Режимов два: у камеры 1 свой, у групп на перебивке свой (intro_scale_anchor2);
        # подстановки шаблона непустые, если не-дефолтен ХОТЬ ОДИН из них.
        scale_anchor=_intro.scale_anchor, scale_anchor2=_intro.scale_anchor2,
        anchor_y=_intro.anchor_y,
        anchor_dy=_intro.anchor_dy,
        any_glitch=_any_glitch, any_back=_any_back, any_big=_any_big,
        # Пресет появления субтитров «глитч» зовёт ту же introAnimFX: без неё подстановка
        # молча ушла бы в try/catch, и глитч не играл бы вовсе. Функция собирается, даже
        # когда строк интро с глитчем в ролике нет, — иначе её негде взять.
        subs_glitch=_subs.sub_anim_glitch,
        accent_color_used=_accent_color_used, custom_color_used=_custom_color_used,
        yellow_dark=_yellow_dark, dg_on=_dg_on,
        # Цвета, тени и свечение слов интро, тень прекомпа и геометрия заднего плана —
        # из структуры стиля: второй копии чтения ключей нет. Двери свечения
        # «glowfix» (intro_hl_glow/intro_comp_glow) читаются там же, в plan_style.py.
        style=stv,
        # Таблицы и правило счётчика остаются в build.py: второй копии нет.
        anims=INTRO_ANIMS, deep_glow=DEEP_GLOW2_GLITCH,
        has_valid_count=_has_valid_count,
        # Камера 2 активна и числа затемнения под интро: по ним собираются подстановки
        # шаблона (нул «интро на кам2» и слой затемнения) — своих чисел у модуля нет.
        cam2_active=_cam.cam2_active, shade=shade_plan))
    # ---- Готовые токены шаблона вынесены в plan_ae.py (остаток распила) ----
    # `plan["_ae"]` — не контракт плана: его читает to_ae_full (подстановки AE_FULL),
    # предпросмотр туда не заглядывает. Имена ключей и текст подстановок прежние.
    # Подложка фото-вставок дописывается ПОСЛЕ: её подстановок нет в общем словаре.
    plan["_ae"] = plan_ae(AeInputs(
        meta=meta, plan=plan,
        cam=_cam, au=_au, subs=_subs, ins=_ip, intro=_intro, itpl=_itpl, decor=_decor,
        style=stv, cams_js=cams_js, subs_js=subs_js, sub_loop=sub_loop,
        cam1scale_js=cam1scale_js, cam1_ease_js=cam1_ease_js, cam1holds_js=cam1holds_js,
        cam1_cx=cam1_cx, cam1_cy=cam1_cy, cam1_anchor=cam1_anchor, cam2_js=cam2_js,
        roto_pos_cc=roto_pos_cc, roto_pos_mk=roto_pos_mk,
        cam1_rot_decl=cam1_rot_decl, cam1_rot_cam=cam1_rot_cam,
        cam2_rot_decl=cam2_rot_decl, cam2_rot_cam=cam2_rot_cam,
        roto_rot_cc=roto_rot_cc, roto_rot_mk=roto_rot_mk,
        cam1_follow_decl=cam1_follow_decl, cam1_follow_js=cam1_follow_js,
        cam2_follow_decl=cam2_follow_decl, cam2_follow_js=cam2_follow_js,
        cam_frame_pos=cam_frame_pos, roto_frame_scale=roto_frame_scale,
        roto_frame_pos=roto_frame_pos,
        hl_row_decl=hl_row_decl, hl_blur_decl=hl_blur_decl, hl_blur_fn=hl_blur_fn,
        hl_short_fn=hl_short_fn,
        posy=_posy, hl_rise=_hl_rise, hl_step=_hl_step, hl_dur=_hl_dur,
        fsize=_fsize, fsize_base=_fsize_base, inserts_js=inserts_js,
        fps=_fps0, music_db=music_db, voice_wav=voice_wav,
        voice_db=voice_db, audio_fade=audio_fade, riser=riser, pop=pop,
        censor_js=censor_js, music_path=music_path, trans=trans, trans_sfx=trans_sfx,
        intro_font_ps=intro_font_ps, intro_hl_font_ps=intro_hl_font_ps,
        intro_mode=intro_mode, font_ps=font_ps, hl_font_ps=hl_font_ps,
        disclaimer=disclaimer, disc_sec=disc_sec, disc_end_on=disc_end_on,
        lumetri=lumetri, lumetri2=lumetri2, dg_on=_dg_on, accent_used=_accent_used,
        any_plate=_any_plate,
        pop_place=pop_place, pop_tail=pop_tail, glitch_sfx=glitch_sfx,
        wsfx_place=wsfx_place, wsfx_tail=wsfx_tail,
        riser_place=riser_place, riser_tail=riser_tail,
        trans_place=trans_place, trans_tail=trans_tail)).tokens
    if _any_plate:
        plan["_ae"].update(plate_tokens(_any_plate, stv.plate_path, plan["_ae"]))
    return plan


def _roto_js(plan: dict[str, Any], xml_path: str, kw: dict[str, Any], emit: Any, cancel: Any) -> str:
    """Рото-маски (GPU, самый долгий этап) по разметке plan.roto. `roto` выкл -> "[]".

    Сам расчёт живёт в `precompute.roto_masks` — той же функции, которой считает превью
    по кнопке «Рассчитать рото и трекинг»: второй копии правил «какие куски ротоить»,
    «где кэш масок» и «что делать со «Стопом»» быть не должно. Там же и выгрузка RVM из
    видеопамяти (одна на сборку и превью): своей выгрузки здесь нет — был бы второй
    вызов `roto.release`. Здесь остаётся только перевод разметки в .jsx-контракт.
    """
    if not kw.get("roto") or not plan.get("roto"):
        return "[]"
    try:
        ents = precompute.roto_masks(plan, xml_path, kw, emit=emit, cancel=cancel)
        # «Стоп» во время рото: `alpha_for_ranges` выходит из цикла и отдаёт то, что
        # успела (маски остались в кэше) — недосчитанные в .jsx писать нельзя, сборку
        # прерываем. Проверка ДО strict-ошибки: «Стоп» — это команда человека, а не
        # «рото не посчитано».
        if (cancel or (lambda: False))():
            raise Cancelled()
        return precompute.js_roto(ents)
    except (ReelsiError, Cancelled, SystemExit):
        raise                                    # «Стоп» — не «рото пропущен», SystemExit — пробрасывать
    except Exception as ex:
        from core.umsg import umsg
        msg = f"рото не удалось: {ex}. Сними галку рото в стиле или исправь причину"
        raise ReelsiError(umsg("roto_failed", msg, err=str(ex), error=str(ex))) from ex


def to_ae_full(xml_path: str, jsx_path: str | None = None, return_source: bool = False, emit: Any = console_emit, cancel: Any = None,
               render_dir: str | None = None, binpfx: str = "", comps_global: bool = False, comp_name_out: list[str] | None = None,
               hl_count: Any = None, hl_joins: Any = None, **kw: Any) -> tuple[str, int, int]:
    """Сборка .jsx. Вся арифметика — scene_plan (план сцены); здесь добавляются
    рото-маски (GPU) и рендер шаблона в файл. Остальные параметры — как в scene_plan.
    cancel — колбэк «нажали Стоп?»; проверяется между этапами и внутри рото (см. Cancelled).
    render_dir — папка вывода для безголового рендера: задана — .jsx сам
    ставит очередь рендера, сохраняет .aep и закрывает AE; пусто — ручная сборка как раньше.
    binpfx — префикс бинов панели проекта при сборке набора («стем — »),
    пусто при одиночной сборке (тогда имена бинов прежние — golden).
    comps_global — класть главную композицию в $.global.REELSI_COMPS для
    мастер-скрипта набора; при одиночной сборке False — строка пуста (golden).
    comp_name_out — список, в который кладётся ИМЯ ГЛАВНОЙ КОМПОЗИЦИИ
    (meta["name"], то самое, по которому om.file пишет .mov). Рендер ждёт файл по нему,
    а не по стему .jsx — на наборе это разные вещи (файл 01_C0233.xml → композиция C0233)."""
    emit = wrap_emit(emit)
    # Трек головы — предстадия сборки, общая с превью (`precompute.head_track`): кнопка
    # «Рассчитать рото и трекинг» считает ровно тем же кодом и кладёт ровно в тот же
    # кэш `<стем>.head.json`, поэтому посчитанное в превью сборка берёт готовым.
    precompute.head_track(xml_path, kw.get("style"), emit=emit, cancel=cancel,
                          ncams=kw.get("ncams"))
    # Сила жёлтых — вторая предстадия (core/emphasis.py): сайдкар `<стем>.emph.json`
    # нужен ДО `scene_plan` — план читает его и решает, на какие жёлтые ставить наезд.
    # Кэш тот же, что у кнопки превью (`precompute.emphasis_precompute`), поэтому
    # посчитанное там сборка берёт готовым; при выключенном «наезде на жёлтых» функция
    # сразу отдаёт пустой ответ и модель не грузит.
    precompute.emphasis_precompute(xml_path, kw.get("style"), idx=kw.get("highlights"),
                                   emit=emit, cancel=cancel, ncams=kw.get("ncams"),
                                   intro=kw.get("intro"), intro_splits=kw.get("intro_splits"),
                                   intro_remove=kw.get("intro_remove"))
    plan = scene_plan(xml_path, emit=emit, cancel=cancel, hl_count=hl_count, hl_joins=hl_joins, **kw)
    if comp_name_out is not None:
        comp_name_out.append(plan.get("name") or os.path.splitext(os.path.basename(xml_path))[0])
    # Прожиг LUT спикера — ЗДЕСЬ, а не в scene_plan: план зовёт и превью (/api/scene),
    # а ему прожигать нельзя (долго и не нужно — у превью своя покраска кадра). До рото:
    # сбой прожига обязан всплыть за секунды, а не после сорока минут масок. Пути
    # подменяются только в данных для .jsx (`plan["_ae"]["cams"]` — то, что уезжает в
    # `var CAM=`): `plan["cams"]` остаётся оригиналами, по ним считает рото (геометрия
    # от цвета не зависит) и по ним же рисует предпросмотр. Прожигать нечего (пусто) —
    # подстановки нет вовсе, .jsx прежний байт в байт (golden).
    cams_baked = lutbake.baked_cams_for_build(xml_path, plan["cams"], emit=emit, cancel=cancel)
    if cams_baked:
        plan["_ae"]["cams"] = _jd([{"path": cams_baked.get(c["path"], c["path"]),
                                    "name": c["name"], "clips": c["clips"],
                                    **({"frame": c["frame"]} if "frame" in c else {})}
                                   for c in plan["cams"]])
    # рото могло оборваться на середине (alpha_for_ranges выходит из цикла по
    # «Стопу») — недосчитанные маски в .jsx писать нельзя
    roto_js = _roto_js(plan, xml_path, kw, emit=emit, cancel=cancel)
    if (cancel or (lambda: False))():
        raise Cancelled()
    emit("  · сборка скрипта")                       # этап для логов (как раньше)
    # aep-путь задаёт Python, а не $.fileName: AfterFX зовётся по короткому
    # 8.3-имени .jsx, и вывод имени из $.fileName дал бы .aep с коротким именем
    aep_path = (re.sub(r"\.jsx$", ".aep", jsx_path, flags=re.I)
                if render_dir and jsx_path and not return_source else None)
    ae = dict(plan["_ae"])
    # Префикс бинов панели проекта: при сборке набора имена бинов получают
    # приставку стема ролика, иначе в общем проекте «Вставки»/«Субтитры» всех роликов —
    # каша. При одиночной сборке префикс пуст: decl пуст и toBin зовёт bin(n) как раньше —
    # .jsx прежний (golden).
    ae["binpfx_decl"] = ("var BIN_PFX = %s;\n    " % _js(binpfx)) if binpfx else ""
    ae["bin_name"] = "BIN_PFX+n" if binpfx else "n"
    ae["dg_report"] = _dg_report(render_dir, comps_global, ae)
    jsx = AE_FULL % dict(ae, roto=roto_js,
                         tail=_render_tail(render_dir, aep_path, comps_global=comps_global),
                         imp_miss=_imp_miss(render_dir))
    if render_dir is None and not comps_global and not return_source and ae.get("dg_on"):
        jsx = _dg_wrap(jsx)
    nclips = sum(len(c["clips"]) for c in plan["cams"])
    if return_source:                          # для мультифайла «один .jsx на всё»
        return jsx, nclips, len(plan["subs"])
    jsx_path = jsx_path or (os.path.splitext(xml_path)[0] + ".jsx")
    # utf-8-sig: ExtendScript без BOM может прочитать файл в системной кодировке (cp1251).
    # Атомарно: «Стоп»/сбой между усечением и записью оставлял пустой .jsx на месте
    # собранного — AE открывал ноль клипов, а пересобрать его было уже нечем.
    atomic_text_write(jsx_path, jsx, encoding="utf-8-sig")
    if not return_source and plan.get("subs"):
        from core.subs import write_srt
        srt_path = os.path.splitext(jsx_path)[0] + ".srt"
        write_srt(plan["subs"], srt_path)
        xml_srt = os.path.splitext(xml_path)[0] + ".srt"
        if os.path.abspath(xml_srt) != os.path.abspath(srt_path):
            try:
                write_srt(plan["subs"], xml_srt)
            except ReelsiError: raise
            except Exception as ex:
                # Копия рядом с .jsx уже записана выше, но пользователь ищет .srt рядом
                # с XML — молчание выглядело бы как «субтитров нет вовсе».
                log.warning("не записал .srt рядом с XML (%s): %s", xml_srt, ex)
                emit("  ! .srt рядом с XML не записан ({err}) — копия рядом с .jsx: {srt}",
                     err=ex, srt=srt_path)
    return jsx_path, nclips, len(plan["subs"])


def write_srt_for(xml_path: str, srt_path: str | None = None, **kw: Any) -> str | None:
    """Собрать план сцены для XML и записать .srt файл рядом с XML (или по указанному пути)."""
    srt_path = srt_path or (os.path.splitext(xml_path)[0] + ".srt")
    plan = scene_plan(xml_path, **kw)
    if plan.get("subs"):
        from core.subs import write_srt
        write_srt(plan["subs"], srt_path)
        return srt_path
    return None


def virtual_edl(xml_path: str, ncams: int | None = None) -> dict[str, Any]:
    """Виртуальный EDL финального XML: что реально видно/слышно на таймлайне.
    Возвращает dict: fps,w,h,dur(сек), cams=[{name,path}],
    segs=[{ci,ts,te,src}] (видео: верхняя включённая дорожка побеждает, сек),
    audio=[{ts,te,src}] (звук ВСЕГДА с камеры 1), words=[{s,e,w}] (сек).
    Используется предпросмотром (/api/aicut_preview) и draft-рендером."""
    # Кадр ролика — у спикера, а не у XML (core/frame.ensure_frame): черновик обязан
    # совпасть по формату с .jsx и Premiere, даже если формат сменили после нарезки.
    _fw, _fh = _frame.ensure_frame(xml_path, emit=wrap_emit(lambda *a, **k: None))
    meta, cams, subs, _ins = parse_full(xml_path, ncams=ncams)
    # Пересобрать XML не удалось — черновик всё равно режется в кадре спикера:
    # он для того и черновик, чтобы показать, как уедет сборка.
    meta["w"], meta["h"] = int(_fw), int(_fh)
    fps = meta["fps"] or 60
    raw: list[tuple[Any, ...]] = []
    for ci, c in enumerate(cams):
        if not c.get("path"):
            continue
        for (s, e, i, o, en, *rest) in c["clips"]:
            if en and e > s:
                raw.append((s, e, ci, i))       # ci третьим — этого ждёт cover_sweep
    segs: list[dict[str, Any]] = []
    for b0, b1, k in cover_sweep(raw):          # кто виден на отрезке; topmost track wins
        s, _e, ci, i = raw[k]
        src = (i + (b0 - s)) / fps
        if segs and segs[-1]["ci"] == ci and abs(
                segs[-1]["src"] + (segs[-1]["te"] - segs[-1]["ts"]) - src) < 1e-3:
            segs[-1]["te"] = b1 / fps                       # merge contiguous same-cam
        else:
            segs.append({"ci": ci, "ts": b0 / fps, "te": b1 / fps, "src": src})
    audio: list[dict[str, Any]] = []
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


def _dg_report(render_dir: str | None = None, comps_global: bool = False, ae: dict[str, Any] | None = None) -> str:
    """Сообщение внутри таймлайна при отсутствии Deep Glow 2 (matchName PEDG2).
    Только если в плане есть жёлтые слова глитча и выбран режим deepglow2 (ae.get('dg_on')).
    Три режима:
    - render_dir задан: _LOG('ОШИБКА: ' + ...) для слива в .aelog.txt;
    - comps_global: дозапись в $.global.REELSI_MASTER_LOG под мастером набора;
    - ручная сборка: суммирование в $.global.REELSI_DG_MISS (alert покажет _dg_wrap)."""
    if not ae or not ae.get("dg_on"):
        return ""
    msg = (
        '"Deep Glow 2 (PEDG2) не найден в After Effects: у " + DG_MISS + " слов глитча в «" '
        '+ main.name + "» нет свечения. Установите плагин или в Reelsi выберите: Настройки → Инструменты → Свечение глитча → Встроенные."'
    )
    if render_dir:
        return '    if(DG_MISS>0) _LOG("ОШИБКА: " + %s);\n' % msg
    if comps_global:
        return (
            "    if(DG_MISS>0 && $.global.REELSI_MASTER_LOG){\n"
            "        try{\n"
            "            var _dglog = new File($.global.REELSI_MASTER_LOG);\n"
            '            _dglog.encoding = "UTF-8";\n'
            '            _dglog.open("a");\n'
            '            _dglog.writeln("ОШИБКА: " + %s);\n'
            "            _dglog.close();\n"
            '        }catch(e){ _LOG("запись в REELSI_MASTER_LOG: " + e); }\n'
            "    }\n" % msg
        )
    return "    if(DG_MISS>0) $.global.REELSI_DG_MISS=($.global.REELSI_DG_MISS||0)+DG_MISS;\n"


def _dg_wrap(body: str) -> str:
    """Оборачивает .jsx ручной сборки в объявление и проверку счётчика DG_MISS.
    В начало — сброс $.global.REELSI_DG_MISS=0;, в конец — alert и повторный сброс."""
    alert_msg = (
        '"Deep Glow 2 (PEDG2) не найден в After Effects: у " + $.global.REELSI_DG_MISS + '
        '" слов глитча нет свечения. Установите плагин или в Reelsi выберите: Настройки → Инструменты → Свечение глитча → Встроенные."'
    )
    head = "$.global.REELSI_DG_MISS=0;\n"
    tail = "\nif($.global.REELSI_DG_MISS){\n    alert(%s);\n}\n$.global.REELSI_DG_MISS=0;\n" % alert_msg
    return head + body + tail


def _imp_miss(render_dir: str | None = None) -> str:
    """Что делает imp() при пропавшем файле. Ручная сборка — alert: человек
    у экрана видит, какой файл не нашёлся. Безголовый прогон (-noui) — alert это модалка,
    которую никто не закроет: процесс зависнет навсегда. Пишем в $.writeln (лог aerender /
    AfterFX) и продолжаем, вернув null — у всех вызовов imp() есть проверка `if(...)`."""
    if not render_dir:
        return 'alert("Не найден файл:\\n"+p);'
    return '$.writeln("Не найден файл: "+p);'


def _render_tail(render_dir: str | None = None, aep_path: str | None = None, comps_global: bool = False) -> str:
    """Хвост .jsx ПОСЛЕ app.endUndoGroup(). Обычный (ручной) режим — просто открыть
    композицию и ничего не сохранять: пользователь сохраняет сам, куда хочет.
    render_dir задан — безголовый рендер: очередь рендера ставит СКРИПТ, а не флаги
    aerender (-RStemplate/-OMtemplate; ошибка в них вылезет в середине рендера). Три
    вещи, на которых это ломается:
    - «Untitled 1» — пресет вывода пользователя, лежит ТОЛЬКО в AE 26.2; применяется
      по точной строке. Версия поэтому не подменяется, а ошибка применения оборачивается.
    - Пресет несёт СВОЙ путь вывода: om.file задаём ПОСЛЕ applyTemplate, иначе пресет
      молча перебьёт папку/имя и рендер уедет не туда.
    - Сборка живёт в памяти, а aerender работает по .aep — нужен app.project.save.
    Очередь перед добавлением чистим: иначе отрендерятся и старые элементы.
    aep_path — куда сохранять .aep: Python и так знает целевой путь, а
    $.fileName после запуска по короткому имени .jsx вернул бы короткое имя .aep.
    Лог хвоста — в <стем>.aelog.txt рядом с проектом: в -noui наши $.writeln не видны
    вовсе, файл же Python читает после возврата AfterFX. Создаётся ПЕРВЫМ
    делом — его отсутствие и есть признак «скрипт не запустился».
    comps_global — собрать композиции в $.global.REELSI_COMPS: мастер-скрипт
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
            "    // ---- безголовый рендер: очередь + save + quit ----\n"
            "    // Лог — ФАЙЛОМ, а не $.writeln: в -noui наши $.writeln не доезжают.\n"
            "    // Файл открываем ПЕРВЫМ делом: его отсутствие у Python = «скрипт не запустился».\n"
            "    var _log = new File(%s);\n"
            "    _log.encoding = \"UTF-8\";\n"
            "    _log.open(\"w\");\n"
            "    for (var _pi=0;_pi<_pending.length;_pi++) _log.writeln(_pending[_pi]);   // слив ранних ошибок сборки\n"
            "    _log.writeln(\"REELSI-TAIL: начат\");\n"
            "    try{\n"
            "        // .aep путь задаёт Python: короткое имя .jsx не должно\n"
            "        // сдвигать имя проекта. Сохраняем ДО очереди: .aep обязан появиться,\n"
            "        // даже если очередь не собралась — иначе «не сохранил проект» не\n"
            "        // отличить от «сломалось на пресетах».\n"
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


def build_combined(jobs: list[dict[str, Any]], out_jsx: str, emit: Any = None, cancel: Any = None, progress: Any = None,
                   comps_global: bool = False, comp_names_out: list[str] | None = None,
                   timeline_filter: bool | None = None) -> tuple[str, int]:
    """jobs: list of dicts, each = {"xml_path": ..., **to_ae_full kwargs}. Concatenate
    every file's build script into ONE .jsx that creates several comps in one AE project.

    emit/progress/cancel — чтобы «один .jsx на всё» был виден и останавливаем так же,
    как поштучная сборка: раньше эта ветка молчала весь прогон (в логе одна строка на
    старте и одна в конце) и «Стоп» не проверяла вовсе.

    comps_global — собрать таймлайны так, чтобы мастер-скрипт рендера мог
    выполнить этот ОДИН файл: каждый таймлайн получает comps_global=True (главная
    композиция кладётся в $.global.REELSI_COMPS — из чего мастер строит очередь) и
    binpfx="<стем> — " (иначе бины «Вставки»/«Субтитры» всех роликов в одном проекте —
    каша). False по умолчанию: кнопка «Собрать набор» собирает .jsx прежним (golden).
    comp_names_out — список, в который складываются ИМЕНА главных
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
        idx = i - 1
        if timeline_filter is True or (timeline_filter is None and comps_global):
            check_js = (
                "    // REELSI_ONLY: сборка только указанных таймлайнов\n"
                "    var _only = null;\n"
                "    try {\n"
                "        if (typeof $.global !== \"undefined\" && $.global && Object.prototype.toString.call($.global.REELSI_ONLY) === \"[object Array]\") {\n"
                "            _only = $.global.REELSI_ONLY;\n"
                "        } else {\n"
                "            var _g = (typeof window !== \"undefined\" ? window : (typeof global !== \"undefined\" ? global : this));\n"
                "            if (_g && Object.prototype.toString.call(_g[\"REELSI_ONLY\"]) === \"[object Array]\") {\n"
                "                _only = _g[\"REELSI_ONLY\"];\n"
                "            }\n"
                "        }\n"
                "    } catch (e) {}\n"
                "    if (_only) {\n"
                "        var _skip = true;\n"
                f"        for (var _oi = 0; _oi < _only.length; _oi++) {{ if (_only[_oi] === {idx}) {{ _skip = false; break; }} }}\n"
                "        if (_skip) return;\n"
                "    }\n"
            )
            src = src.replace("(function () {\n", "(function () {\n" + check_js, 1)
        parts.append(src)
    body = "\n\n// ===== следующий таймлайн =====\n\n".join(parts)
    if not comps_global and "REELSI_DG_MISS" in body:
        body = _dg_wrap(body)
    atomic_text_write(out_jsx, body, encoding="utf-8-sig")
    return out_jsx, len(parts)


def _write_master(jsx_list: Sequence[str], master_path: str, aep_path: str, render_dir: str) -> str:
    """Мастер-скрипт набора: лог первым делом, evalFile каждого ролика в своём try/catch,
    очередь из всех композиций, save ДО и ПОСЛЕ, quit в finally."""
    render_dir = render_dir.replace("\\", "/").rstrip("/")
    aep = aep_path.replace("\\", "/")
    aelog = re.sub(r"\.aep$", ".aelog.txt", aep)
    files = ",\n        ".join(_js(p.replace("\\", "/")) for p in jsx_list)
    jsx = (
        "// Reelsi -> мастер набора: N роликов в ОДИН проект AE, один aerender.\n"
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
        "    //    затрут строки друг друга.\n"
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
    atomic_text_write(master_path, jsx, encoding="utf-8-sig")
    return master_path


def _write_part_master(combined_jsx: str, master_path: str, part_aep: str,
                       part_aelog: str, part_indices: Sequence[int]) -> str:
    """Мастер-скрипт части набора: лог части, REELSI_ONLY, evalFile общего Reelsi_all.jsx,
    save reelsi_batch.part<k>.aep (без очереди рендера)."""
    combined = combined_jsx.replace("\\", "/")
    aep = part_aep.replace("\\", "/")
    aelog = part_aelog.replace("\\", "/")
    indices_js = json.dumps(list(part_indices))
    jsx = (
        "// Reelsi -> мастер части набора: параллельная сборка таймлайнов\n"
        "$.global.REELSI_MASTER_LOG = %s;\n"
        "var _log = new File($.global.REELSI_MASTER_LOG);\n"
        "    _log.encoding = \"UTF-8\";\n"
        "    _log.open(\"w\");\n"
        "    _log.writeln(\"REELSI-MASTER: начат\");\n"
        "try{\n"
        "    $.global.REELSI_ONLY = %s;\n"
        "    var REELSI_ONLY = $.global.REELSI_ONLY;\n"
        "    $.global.REELSI_COMPS = [];\n"
        "    _log.close();\n"
        "    var _evalErr = null;\n"
        "    try{\n"
        "        $.evalFile(new File(%s));\n"
        "    }catch(e){\n"
        "        _evalErr = e;\n"
        "    }\n"
        "    _log = new File($.global.REELSI_MASTER_LOG);\n"
        "    _log.encoding = \"UTF-8\";\n"
        "    _log.open(\"a\");\n"
        "    if (_evalErr){\n"
        "        _log.writeln(\"evalFile ОШИБКА: \" + %s + \" — \" + _evalErr);\n"
        "    } else {\n"
        "        _log.writeln(\"evalFile ok: \" + %s);\n"
        "    }\n"
        "    _log.close();\n"
        "    var f = new File(%s);\n"
        "    try{\n"
        "        app.project.save(f);\n"
        "        _log = new File($.global.REELSI_MASTER_LOG);\n"
        "        _log.encoding = \"UTF-8\";\n"
        "        _log.open(\"a\");\n"
        "        _log.writeln(\"save#1: ok\");\n"
        "    }catch(e){\n"
        "        _log = new File($.global.REELSI_MASTER_LOG);\n"
        "        _log.encoding = \"UTF-8\";\n"
        "        _log.open(\"a\");\n"
        "        _log.writeln(\"первый save не выполнился: \" + e);\n"
        "    }\n"
        "    _log.writeln(\"exists=\" + f.exists);\n"
        "    _log.writeln(\"REELSI-MASTER: готово\");\n"
        "}catch(e){\n"
        "    try{\n"
        "        _log = new File($.global.REELSI_MASTER_LOG);\n"
        "        _log.encoding = \"UTF-8\";\n"
        "        _log.open(\"a\");\n"
        "        _log.writeln(\"непредвиденная ошибка мастера части: \" + e);\n"
        "    }catch(e2){}\n"
        "}finally{\n"
        "    try{ _log.close(); }catch(e){}\n"
        "    $.global.REELSI_MASTER_LOG = null;\n"
        "    $.global.REELSI_ONLY = null;\n"
        "    app.quit();\n"
        "}\n" % (_js(aelog), indices_js, _js(combined), _js(combined), _js(combined), _js(aep))
    )
    atomic_text_write(master_path, jsx, encoding="utf-8-sig")
    return master_path


def _write_merge_master(merge_jsx_path: str, merge_aep_path: str, merge_aelog_path: str,
                        part_aeps: Sequence[str], comp_names: Sequence[str], render_dir: str) -> str:
    """Скрипт слияния частей проекта: импорт каждой part<k>.aep, consolidateFootage,
    очистка пустых папок, очередь рендера с Best Settings + Untitled 1, save reelsi_batch.aep."""
    render_dir = render_dir.replace("\\", "/").rstrip("/")
    aep = merge_aep_path.replace("\\", "/")
    aelog = merge_aelog_path.replace("\\", "/")
    parts_js = ",\n        ".join(_js(p.replace("\\", "/")) for p in part_aeps)
    comps_js = ",\n        ".join(_js(c) for c in comp_names)
    jsx = (
        "// Reelsi -> слияние частей набора в единый проект AE\n"
        "$.global.REELSI_MASTER_LOG = %s;\n"
        "var _log = new File($.global.REELSI_MASTER_LOG);\n"
        "    _log.encoding = \"UTF-8\";\n"
        "    _log.open(\"w\");\n"
        "    _log.writeln(\"REELSI-MASTER: начат\");\n"
        "    _log.writeln(\"REELSI-MERGE: начат\");\n"
        "try{\n"
        "    var _partFiles = [\n"
        "        %s\n"
        "    ];\n"
        "    var _compNames = [\n"
        "        %s\n"
        "    ];\n"
        "    // 1) импорт каждого проекта части\n"
        "    for (var _pi=0; _pi<_partFiles.length; _pi++){\n"
        "        var _pf = new File(_partFiles[_pi]);\n"
        "        if (!_pf.exists){\n"
        "            _log.writeln(\"part не найден: \" + _partFiles[_pi]);\n"
        "            continue;\n"
        "        }\n"
        "        try{\n"
        "            var _io = new ImportOptions(_pf);\n"
        "            _io.importAs = ImportAsType.PROJECT;\n"
        "            var _imported = app.project.importFile(_io);\n"
        "            if (_imported && _imported instanceof FolderItem){\n"
        "                while (_imported.numItems > 0){\n"
        "                    _imported.item(1).parentFolder = app.project.rootFolder;\n"
        "                }\n"
        "                try { _imported.remove(); } catch(e){}\n"
        "            }\n"
        "            _log.writeln(\"import ok: \" + _partFiles[_pi]);\n"
        "        }catch(e){\n"
        "            _log.writeln(\"import ОШИБКА: \" + _partFiles[_pi] + \" — \" + e);\n"
        "        }\n"
        "    }\n"
        "    // 2) слияние дублирующегося футажа\n"
        "    try{\n"
        "        app.project.consolidateFootage();\n"
        "        _log.writeln(\"consolidateFootage: ok\");\n"
        "    }catch(e){\n"
        "        _log.writeln(\"consolidateFootage: \" + e);\n"
        "    }\n"
        "    // 3) удаление пустых папок импорта\n"
        "    for (var _fi=app.project.numItems; _fi>=1; _fi--){\n"
        "        var _it = app.project.item(_fi);\n"
        "        if (_it instanceof FolderItem && _it.numItems === 0){\n"
        "            try { _it.remove(); } catch(e){}\n"
        "        }\n"
        "    }\n"
        "    // 4) сбор композиций по именам в исходном порядке\n"
        "    var _orderedComps = [];\n"
        "    for (var _cni=0; _cni<_compNames.length; _cni++){\n"
        "        var _tName = _compNames[_cni];\n"
        "        for (var _ii=1; _ii<=app.project.numItems; _ii++){\n"
        "            var _cItem = app.project.item(_ii);\n"
        "            if (_cItem instanceof CompItem && _cItem.name === _tName){\n"
        "                _orderedComps.push(_cItem);\n"
        "                break;\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "    // 5) очередь рендера: чистим, save ДО очереди\n"
        "    var rq0 = app.project.renderQueue;\n"
        "    while (rq0.numItems > 0) rq0.item(rq0.numItems).remove();\n"
        "    var f = new File(%s);\n"
        "    try{\n"
        "        app.project.save(f);\n"
        "        _log.writeln(\"save#1: ok\");\n"
        "    }catch(e){\n"
        "        _log.writeln(\"первый save не выполнился: \" + e);\n"
        "    }\n"
        "    // 6) композиции в очередь (Best Settings + Untitled 1)\n"
        "    for (var _ci=0; _ci<_orderedComps.length; _ci++){\n"
        "        var _c = _orderedComps[_ci];\n"
        "        try{\n"
        "            var rq = rq0.items.add(_c);\n"
        "            rq.applyTemplate(\"Best Settings\");\n"
        "            var om = rq.outputModule(1);\n"
        "            om.applyTemplate(\"Untitled 1\");\n"
        "            var _out = new File(%s + \"/\" + _c.name + \".mov\");\n"
        "            if(_out.exists){ _out.remove(); _log.writeln(\"перезаписан: \" + _c.name + \".mov\"); }\n"
        "            om.file = _out;\n"
        "            _log.writeln(\"comp ok: \" + _c.name);\n"
        "        }catch(e){\n"
        "            _log.writeln(\"композиция «\" + _c.name + \"» не встала в очередь: \" + e);\n"
        "        }\n"
        "        _log.close();\n"
        "        _log = new File($.global.REELSI_MASTER_LOG);\n"
        "        _log.encoding = \"UTF-8\";\n"
        "        _log.open(\"a\");\n"
        "    }\n"
        "    // 7) save ПОСЛЕ очереди\n"
        "    try{\n"
        "        app.project.save(f);\n"
        "        _log.writeln(\"save#2: ok\");\n"
        "    }catch(e){\n"
        "        _log.writeln(\"второй save не выполнился: \" + e);\n"
        "    }\n"
        "    _log.writeln(\"rq.numItems=\" + rq0.numItems);\n"
        "    _log.writeln(\"exists=\" + f.exists);\n"
        "    _log.writeln(\"REELSI-MASTER: готово\");\n"
        "}catch(e){\n"
        "    _log.writeln(\"непредвиденная ошибка мастера слияния: \" + e);\n"
        "}finally{\n"
        "    try{ _log.close(); }catch(e){}\n"
        "    $.global.REELSI_MASTER_LOG = null;\n"
        "    app.quit();\n"
        "}\n" % (_js(aelog), parts_js, comps_js, _js(aep), _js(render_dir))
    )
    atomic_text_write(merge_jsx_path, jsx, encoding="utf-8-sig")
    return merge_jsx_path