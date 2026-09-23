# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Расчёт интро плана сцены (этап 2 распила scene_plan).

`scene_plan` был одной функцией на 3253 строки; этап 1 вынес блок субтитров
в `plan_subs.py`, этап 2 выносит сюда ВЕСЬ расчёт интро: окна групп, автофит и ширину
блока, безопасную зону, раскладку строк и «большое слева», затухание группы к субтитру
и сжатие появления INTRO_SQ, камеру группы, тень прекомпа, а также готовые
массивы и подстановки шаблона (INTRO_GROUPS, INTRO_LY/LX/LK/IDY/ON2/FRONT/ABOVE_ROTO,
INTRO_FX, INTRO_SUB_FX, INTRO_SQ).

Перенос ПОСТРОЧНЫЙ: поведение, числа и порядок операций не менялись ни на байт
(проверяется эталоном fixtures/golden_geometry.jsx и побайтовым сравнением .jsx/плана).
Имена локальных переменных оставлены как в scene_plan — поэтому тело перенесено дословно,
а входы распаковываются в преамбуле. В вызовах вынесенных helper'ов добавлены только их
явные аргументы.

Вход — один неизменяемый `IntroInputs`, выход — один `IntroPlan` со всем, что `scene_plan`
читает дальше (имена локальных переменных после вызова — те же). Данные субтитров для
правила MH (`_next_sub_after`/`_sub_row_at`) берутся из результата `plan_subs` — своей
копии элементов и геометрии полосы модуль не держит. Стиль приходит структурой
`StyleValues` одним полем `style` (её читает один раз `plan_style.read_style`),
а общие с другими блоками правила (`_accent_word`, `_parse_intro_count`, `_intro_line_font`,
`_intro_fit_ds`, `_intro_appear_dur`, `_intro_cnt_positions`, таблица `INTRO_ANIMS`)
остаются в `build.py` и приходят параметрами: второй копии нет.
"""
from dataclasses import dataclass
from typing import Any, Callable, Sequence, cast

from .jsutil import _jd, _r
from .layout import (INTRO_BASE_Y, INTRO_F_OUT, INTRO_SAFE_TOP, INTRO_SCALE,
                     _intro_group_window, _intro_i_dy, _show_segments, _zoom_max,
                     intro_big_layout, intro_block_span, intro_clamp_window,
                     intro_hits_subs, intro_line_sizes, intro_line_ys,
                     intro_sub_window)
from .plan_style import StyleValues
from .plan_subs import SubsPlan


@dataclass(frozen=True)
class IntroInputs:
    """Вход расчёта интро: всё, что `scene_plan` знает к моменту вызова.

    Поля названы как локальные переменные scene_plan (meta целиком — тело читает
    `meta["w"]/["h"]/["fps"]`), а `subs` — результат `plan_subs`: из него берутся
    элементы субтитров и геометрия полосы для правила MH. Все стилевые значения
    (геометрия строк, тайминг, автофит, точка масштабирования, затемнение, тень
    прекомпа) приходят структурой `style`, прочитанной один раз.
    """
    # Группы строк интро: уже разбиты по splits и отсортированы по времени (первая —
    # самая ранняя: JS считает её началом ролика и держит её с 0).
    groups: list
    # Камеры из parse_full: по ним считается, на каком нуле висит группа (кам2/перебивка).
    cams: list
    # Ролик: meta из parse_full (w/h/fps) и fps как _fps0 (meta["fps"] or 60) — им
    # пересчитываются окна показа камер в секунды.
    meta: dict
    fps: float
    active_cam_at: Callable[[float], int]
    # Видеовставки [(начало, конец)] в секундах: группа на такой вставке уезжает наверх.
    video_segs: list
    # Результат plan_subs: элементы субтитров и геометрия полосы.
    subs: SubsPlan
    # Шрифты: базовый (им меряется полоса субтитров), интро и жёлтый интро.
    font_ps: str
    intro_font_ps: str
    intro_hl_font_ps: str
    # Акцентная строка и строка заднего плана: шрифт и регистр каждой.
    accent_font_ps: str
    accent_case: str
    back_font_ps: str
    back_case: str
    # Строки заднего плана в ролике и глитч в ролике (ПРАВКИ 3/4, IK): от
    # первого зависят шаги строк, от второго — окна фейд-аута прекомпов.
    any_back: bool
    any_glitch: bool
    # Резолвнутый и прочитанный стиль (plan_style.read_style): геометрия строк,
    # тайминг, масштаб и автофит, точка масштабирования, затемнение и тень прекомпа.
    style: StyleValues
    # Правила, живущие в build.py: регистр слова, разбор числа-счётчика и позиции
    # счётчиков в строке, лесенка шрифта строки, автофит группы, длительность появления.
    accent_word: Callable[[str, str], str]
    parse_count: Callable[..., object]
    cnt_positions: Callable[[dict, int], list]
    line_font: Callable[..., str]
    fit_ds: Callable[..., float]
    appear_dur: Callable[..., float]
    # Таблица анимаций интро (build.INTRO_ANIMS): длительность глитча держит окно группы.
    anims: dict
    # Ключи зума Камеры 1 и тип интерполяции каждого: автофит и пересечение с полосой
    # субтитров считают по ним, пока интро привязано к камере.
    cam1_scale: list
    holds: list
    # Полка ПОСЛЕДНЕЙ группы интро после её последнего слова, с (ключ стиля
    # intro_last_hold). Читает его сборка (build.py, тем же _sv, что и остальные ключи
    # стиля) и отдаёт сюда ОДНИМ полем: по нему работают и формула окна
    # (layout._intro_group_window), и правило _far ниже — второй копии числа нет.
    intro_last_hold: float


@dataclass(frozen=True)
class IntroPlan:
    """Выход расчёта интро: ровно те имена, что `scene_plan` читает дальше.

    Поля intro/idy/on2/front/above_roto/anchor/ly/lx/lk/sq/sub_fx уезжают в план
    (предпросмотр) и в шаблон, decl/out/fn — готовые подстановки .jsx.
    """
    intro: list           # группы интро для плана: окна, ds, y, ys, sq, тень (plan["intro"])
    groups_js: str        # INTRO_GROUPS: строки групп (автофит уже в ds)
    idy: list             # INTRO_IDY: опускание блока под INTRO_SAFE_TOP на группу
    on2: list             # INTRO_ON2: группа висит на нуле «интро на кам2»
    front: list           # группа легла на видеовставку — прекомп поднимается над всем
    above_roto: list      # группа в нижней половине кадра — прекомп над рото
    anchor: list          # якорь блока на группу: center | first
    scale_anchor: str     # точка масштабирования прекомпа: comp | first | block
    anchor_y: list        # INTRO_ANCHOR_Y: Y якоря слоя прекомпа на группу (px прекомпа)
    anchor_dy: list       # INTRO_ANCHOR_DY: компенсация Position по Y на группу (px слоя)
    ly: list              # INTRO_LY: Y базовых линий строк на группу
    lx: list              # INTRO_LX: левый край строки (большая строка)
    lk: list              # INTRO_LK: множитель кегля строки (большая строка)
    sq: list              # INTRO_SQ: множитель длительности появления [группа][строка][слово]
    sub_fx: list          # INTRO_SUB_FX: окна групп, гаснущих к субтитру
    fx_decl: str          # INTRO_FX: окна выхода ВСЕХ групп (окна не накладываются)
    fx_out: str           # подстановка тех же окон в outStart/outEnd прекомпа
    sub_fx_decl: str      # INTRO_SUB_FX: объявление окон групп, гаснущих к субтитру
    sub_fx_out: str       # подстановка их в outStart/outEnd
    sq_decl: str          # INTRO_SQ и его читалка introSQ
    sq_fn: str
    sq_used: bool         # сжатые слова есть: шаблон зовёт introSQ(gI,qi,wi), .jsx прежний без них
    accent_used: bool     # хоть одна строка получила accent_font (подстановки af/cf)


def _g_at(g: list[dict[str, Any]]) -> float:
    """Момент первого слова группы (группы идут по таймингу; см. вызов в build.py)."""
    ts = [t for x in g for t in (x.get("times") or [])]
    return min(ts) if ts else 0.0


def _grp_big_i(g: list[dict[str, Any]]) -> int | None:
    """Индекс большой строки группы (первая с флагом big) или None.
    Группа из одной строки большой не считается: раскладывать её не с чем, и флаг
    остаётся без эффекта — .jsx такой группы прежний. Остальные строки с big в той
    же группе — обычные строки стопки (шаг занимают как все)."""
    if len(g) < 2:
        return None
    for _k, _x in enumerate(g):
        if _x.get("big"):
            return _k
    return None


def _scale_anchor_y(mode: str, ys: Sequence[float | None], h: float) -> float:
    """Y якоря масштабирования прекомпа в координатах прекомпа, px (intro_scale_anchor).

    "first" — Y первой строки блока, "block" — середина между первой и последней строкой,
    всё остальное ("comp" и незнакомое значение) — центр композиции прекомпа h/2, то есть
    прежнее поведение. Строки без Y (вырожденная раскладка «большое слева» отдаёт None) —
    тоже центр: якорь обязан быть числом. Округление до сотых — как у самих ys.
    """
    _ok = [v for v in ys if v is not None]
    if mode == "first" and _ok:
        return round(float(_ok[0]), 2)
    if mode == "block" and _ok:
        return round((float(_ok[0]) + float(_ok[-1])) / 2.0, 2)
    return h / 2.0


# ---------------------------------------------------------------------------
# Помощники, нужные только интро: явные аргументы вместо замыканий scene_plan.
# ---------------------------------------------------------------------------


# Группа, которая появляется на перебивке, вешается в JSX на отдельный нул «интро на кам2»
# (там другой кадр — текст за спиной ставят ниже). Камеру считаем по БОЛЬШИНСТВУ окна
# группы, а не по моменту появления первого слова: группа живёт gMax+0.3+1.0+0.75 секунд,
# и кат через 40 мс после первого слова уводил её на нул камеры 1, хотя почти всё время
# она висит над кадром камеры 2. Ничья (ровно 50/50) — ПОЗДНЕЙ камере: группа доигрывает
# на ней, и глаз запоминает конец. Показ камер по времени — тот же источник, что camAt в JSX.
def _intro_on2_at(ts: float, te: float, show_segs: list[Any], active_cam_at: Callable[[float], int]) -> int:
    win = te - ts
    if win <= 0:
        return 1 if active_cam_at(ts) != 0 else 0
    dur_c1 = 0.0
    last_ci = 0
    for a, b, ci in show_segs:
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


def _intro_front_at(ts: float, te: float, video_segs: list[Any]) -> int:
    for vs, ve in video_segs:
        if max(ts, vs) < min(te, ve):
            return 1
    return 0


def _next_sub_after(times: Sequence[float], sub_starts: Sequence[float]) -> float | None:
    """Момент появления первого субтитра ПОСЛЕ последнего слова группы, сек.
    После группы субтитров нет вовсе — None (окно группы остаётся прежним)."""
    if not times or not sub_starts:
        return None
    gmax = max(times)
    for s in sub_starts:
        if s > gmax + 1e-6:
            return s
    return None


def _sub_row_at(t: float, subs_plan: list[dict[str, Any]], hl_step: float, sub_step: float) -> tuple[int, float]:
    """(ряд, шаг) элементов субтитров, видимых в момент t: полоса
    субтитров — не только posy и кегль, но и высота набранного: у стопки жёлтых
    шаг свой (hl_step), у строк текста — шаг строк. Ряда нет — шаг не нужен."""
    row, stack = 0, False
    for it in subs_plan:
        if it["s"] - 1e-6 <= t < it.get("gend", it["e"]) + 1e-6:
            row = max(row, int(it.get("row") or 0))
            if it.get("stack"):
                stack = True
    if not row:
        return 0, 0.0
    return row, (hl_step if stack else sub_step)


def _intro_line_js(x: dict[str, Any], accent_font_ps: str, accent_case: str, back_font_ps: str, back_case: str,
                   cnt_positions: Callable[..., Any], parse_count: Callable[..., Any], accent_word: Callable[[str, str], str]) -> dict[str, Any]:
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
    # Акцентный шрифт: флаг живёт на СТРОКЕ рядом с color. Цвет строки
    # не меняется — акцент это ТРЕТЬЕ состояние слова (шрифт + регистр). Регистр
    # правится ЗДЕСЬ, в Python: и .jsx, и план получают готовый текст, второй
    # копии трансформации нет. Пустой accent_font = выключено: галка ничего не
    # делает (и это видно — строка остаётся как была).
    if x.get("accent"):
        if accent_font_ps:
            line["accent_font"] = accent_font_ps
            line["words"] = [accent_word(cast(str, wd), accent_case) for wd in line["words"]]
    elif x.get("back"):
        line["back"] = True
        if back_font_ps:
            line["accent_font"] = back_font_ps
        line["words"] = [accent_word(cast(str, wd), back_case) for wd in line["words"]]
    # «Большое слева»: флаг строки рядом с accent/back. Кладём ТОЛЬКО
    # при True — иначе .jsx меняется на пустом месте (golden). Раскладку по нему
    # считает intro_big_layout, в .jsx флаг нужен как признак строки (скейл и X
    # берутся из INTRO_LK/INTRO_LX).
    if x.get("big"):
        line["big"] = True
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
        for _p in cnt_positions(x, len(wds)):
            parsed = parse_count(wds[_p], x.get("dec"))
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
            parsed = parse_count(wd, x.get("dec"))
            if parsed is not None:
                target, dec, expr, _ = parsed
                cnts = [[wi, _r(target), expr, dec]]
                break
        if not cnts:
            parsed = parse_count(" ".join(cast(list[str], wds)).strip(), x.get("dec"))
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


def plan_intro(inp: IntroInputs) -> IntroPlan:
    """Интро плана сцены: группы -> окна -> раскладка -> автофит -> подстановки.

    Тело — дословный перенос блока из scene_plan (до распила — строки 1426-1797):
    имена локальных переменных оставлены прежними, поэтому ни одна строка не переписана.
    """
    _intro_groups = inp.groups
    cams = inp.cams
    meta = inp.meta
    _fps0 = inp.fps
    _active_cam_at = inp.active_cam_at
    _video_segs = inp.video_segs
    _intro_on2: list[Any] = []
    _intro_front: list[Any] = []
    _intro_above_roto: list[Any] = []
    _intro_anchor: list[str] = []
    _intro_anchor_y: list[Any] = []
    _intro_anchor_dy: list[Any] = []
    _intro_ly: list[Any] = []
    _intro_lx: list[Any] = []
    _intro_lk: list[Any] = []
    _intro_sq: list[Any] = []
    _intro_sub_fx: list[Any] = []
    # Элементы субтитров и геометрия полосы — из результата plan_subs:
    # своей копии данных субтитров модуль не заводит.
    subs_plan = inp.subs.subs
    _fsize_base = inp.subs.fsize_base
    _posy, _fsize = inp.subs.posy, inp.subs.fsize
    _hl_step, _sub_step = inp.subs.hl_step, inp.subs.sub_step
    font_ps = inp.font_ps
    intro_font_ps, intro_hl_font_ps = inp.intro_font_ps, inp.intro_hl_font_ps
    accent_font_ps, accent_case = inp.accent_font_ps, inp.accent_case
    back_font_ps, back_case = inp.back_font_ps, inp.back_case
    # any_glitch модулю больше не нужен: окно выхода считается по КАЖДОЙ группе (глитч-времена
    # своей группы лежат в _grp_stats), а не только у групп с глитчем — поле входа осталось
    # для тех, кто читает флаг сборки (ассет и звук глитча в build.py).
    _any_back = inp.any_back
    # Стиль — структурой, прочитанной один раз: имена локальных переменных
    # оставлены прежними, источник у них теперь поля структуры.
    style = inp.style
    # Точка масштабирования прекомпа (intro_scale_anchor): от неё слой интро уменьшается и
    # увеличивается. Ключ общий на все группы — читается ОДИН раз (второго чтения нет).
    _scale_anchor = style.intro_scale_anchor
    _accent_word, _parse_intro_count = inp.accent_word, inp.parse_count
    _intro_cnt_positions = inp.cnt_positions
    _intro_line_font, _intro_fit_ds = inp.line_font, inp.fit_ds
    _intro_appear_dur = inp.appear_dur
    anims = inp.anims
    back_step, back_step_after, back_scale = style.back_step, style.back_step_after, style.back_scale
    _line_step_k, _big_step_k = style.line_step_k, style.big_step_k
    intro_fade, intro_fx_hold_add = style.intro_fade, style.intro_fx_hold_add
    # Полка последней группы — ключ intro_last_hold: читается ОДИН раз (приехал полем
    # входа, см. IntroInputs), дальше работает и в окне группы (_intro_group_window),
    # и в правиле _far ниже. Второй копии числа нет.
    _intro_last_hold = float(inp.intro_last_hold)
    intro_sub_cut, intro_sub_fade = style.intro_sub_cut, style.intro_sub_fade
    _G, _fit_w, _fit_max = style.intro_scale_k, style.fit_w, style.fit_max
    _intro_cam = style.intro_cam
    cam1_scale, holds = inp.cam1_scale, inp.holds
    intro_comp_shadow_fill, intro_comp_shadow_op = style.intro_comp_shadow_fill, style.intro_comp_shadow_op
    intro_comp_shadow2_fill, intro_comp_shadow2_op = (style.intro_comp_shadow2_fill,
                                                      style.intro_comp_shadow2_op)
    # Показ камер по времени — тот же источник, что camAt в JSX (см. _intro_on2_at).
    _intro_show_segs = [(a / _fps0, b / _fps0, ci) for a, b, ci in _show_segments(cams)]
    # Кегль интро = кегль субтитров ДО ужатия строк (в AE это один FONT_SIZE): тот же
    # _fsize_base, что у стопки ниже, — автофит меряет ширину строки тем же размером
    # а ужимание строк его не касается (доработка ZL).
    # окна групп (ts/te) — здесь, в плане; превью их не считает. По тем же
    # округлённым times, что ушли в .jsx, — иначе план и AE разойдутся на сотых.
    intro_plan = []
    intro_idy = []                       # готовые iDy для шаблона
    # Статистика каждой группы по ГОТОВЫМ строкам (тем же, что уедут в .jsx): все моменты
    # слов и глитч-моменты. Окно выхода считаем здесь для КАЖДОЙ группы — план (превью) и
    # шаблон (INTRO_FX) получают одни и те же числа.
    _grp_stats = []
    for _grp in _intro_groups:
        _l = [_intro_line_js(x, accent_font_ps, accent_case, back_font_ps, back_case,
                             _intro_cnt_positions, _parse_intro_count, _accent_word)
              for x in _grp]
        _l_tms = [t for _x in _l for t in _x["times"]]
        _l_gl = [t for _x in _l if _x.get("anim") == "glitch" for t in _x["times"]]
        _grp_stats.append((_l, _l_tms, _l_gl))
    _fx_ts: list[Any] = [None] * len(_grp_stats)     # начало показа группы (inAt), с
    _fx_te: list[Any] = [None] * len(_grp_stats)     # конец слоя прекомпа: базовая формула + глитч, с
    _fx_fade: list[Any] = [None] * len(_grp_stats)   # спад группы (intro_fade либо пол окна), с
    _fx_win: list[Any] = [None] * len(_grp_stats)    # ИТОГОВОЕ окно группы: [начало затухания, конец], с
    for _g, (_l0, _l_tms, _l_gl) in enumerate(_grp_stats):
        # Базовая формула окна — та же функция, что отдаёт ts/te плану
        # (_intro_group_window): второй копии формулы нет ни здесь, ни в шаблоне. Полка
        # ПОСЛЕДНЕЙ группы (intro_last_hold) приезжает в неё параметром: при нуле
        # последняя считается ровно как непоследние.
        _ts_g, _te_g = _intro_group_window(_l_tms, _g, len(_grp_stats), _intro_last_hold)
        _fade_g = intro_fade if _l_gl else min(intro_fade, INTRO_F_OUT)
        if _l_gl:
            # ПРАВКА 3: полка прекомпа не наступает раньше конца анимации последнего
            # глитч-слова (момент слова + длительность анимации из INTRO_ANIMS). Формула
            # обычного окна — ровно та, что в AE_FULL (inAt/outStart), ПРАВКА её только
            # продлевает: прекомп без глитча не меняется ни на сотую.
            _gl_end = max(_l_gl) + anims["glitch"]["dur"]
            _out_s = max(_te_g - INTRO_F_OUT, _gl_end)
            # ПРАВКА 4 / IK: последний прекомп либо следующий начинается позже, чем через 2 с
            # после конца этого, — полка дополнительно держится на intro_fx_hold_add;
            # спад везде intro_fade.
            # Последняя группа при intro_last_hold == 0 надбавки НЕ получает: владелец просил,
            # чтобы последняя не задерживалась вовсе, а надбавка вернула бы ту же задержку с
            # другой стороны. У непоследних «далёких» групп надбавка остаётся как была.
            _far = _g == len(_grp_stats) - 1 and _intro_last_hold != 0
            if not _far and _g + 1 < len(_grp_stats):
                _nx_tms = _grp_stats[_g + 1][1]
                _nx_in = min(_nx_tms) if _nx_tms else 0.0
                if _nx_in - (_out_s + intro_fade) > 2.0:
                    _far = True
            if _far:
                _out_s += intro_fx_hold_add
            _te_g = _r(_out_s + _fade_g)
        _fx_ts[_g], _fx_te[_g], _fx_fade[_g] = _ts_g, _te_g, _fade_g

    # ---- Интро гаснет к субтитру, если стоит на его месте ----------------
    # Моменты появления элементов субтитров — из ТЕХ ЖЕ данных, что уходят в .jsx и план
    # (SUBS/SUB_ROWS/стопка): subs_plan собран выше и в режиме слов, и в режиме строк.
    _sub_starts = sorted({it["s"] for it in subs_plan})

    for _g, (_grp, (_lines, _tms, _l_gl)) in enumerate(zip(_intro_groups, _grp_stats)):
        # Окно группы посчитано выше (для КАЖДОЙ группы) — здесь оно только уточняется
        # правилом MH и подрезкой под старт следующей: второй копии базовой формулы нет.
        _ts, _te, _fade = _fx_ts[_g], _fx_te[_g], _fx_fade[_g]
        # окно (ts/te) уже посчитано — камера группы по большинству этого окна,
        # а не по первому слову: иначе кат сразу после старта оставлял нул камеры 1.
        _on2 = _intro_on2_at(_ts, _te, _intro_show_segs, _active_cam_at)
        _intro_on2.append(_on2)
        _front = _intro_front_at(_ts, _te, _video_segs)
        _intro_front.append(_front)
        # Якорь блока интро этой группы: на перебивке свой ключ стиля —
        # группа висит на другом нуле (кам2) и «первая строка» там своя. "first" —
        # первая строка стоит на месте, остальные ложатся ниже.
        _anchor = cast(str, style.intro_anchor2 if _on2 else style.intro_anchor)
        _intro_anchor.append(_anchor)
        # Смещение ГРУППЫ: живёт на головной строке (первой в группе) и
        # добавляется к позиции прекомпа в шаблоне. После разрезания/слияния групп
        # оно остаётся у той строки, которая стала головной, — новая группа с чистой
        # головы получает 0/0. Сюда же дублируем в plan (предпросмотр двигает мышью).
        _dx = float(_grp[0].get("gx") or 0) if _grp else 0.0
        _dy = float(_grp[0].get("gy") or 0) if _grp else 0.0
        _gs = float(_grp[0].get("gs") or 100) if _grp else 100.0
        _ds = _gs
        # Шрифт каждой строки считает Python (та же лесенка, что у автофита и .jsx) —
        # превью читает готовое и своей лесенки не держит. В сами строки (lines) поле не
        # кладём: они уезжают в .jsx как INTRO_GROUPS, и он обязан остаться прежним (golden).
        _line_fonts = [_intro_line_font(ln, intro_font_ps, intro_hl_font_ps) for ln in _lines]
        # Y базовых линий строк: шаги задают back_step (доля обычного)
        # и back_step_after (шаг от заднего плана к обычной строке под ним), а не жёсткие
        # пиксели шаблона, плюс якорь блока. Считает Python — тем же числам едут и .jsx
        # (INTRO_LY), и превью. В строки (lines) поле не кладём: INTRO_GROUPS
        # обязан остаться прежним (golden).
        # Группа с большой строкой: стопку раскладывает та же intro_line_ys,
        # но только по строкам СТОПКИ (большая шаг не занимает), а большую сажает на её
        # место intro_big_layout. Группа без большой — прежняя раскладка (lx/lk пустые).
        _big_i = _grp_big_i(_lines)
        _big_total = None
        _lx: Any
        _lk: Any
        _ys: Any
        _lx = _lk = None
        if _big_i is None:
            _ys = intro_line_ys(_lines, back_step, _any_back, _anchor, meta["h"],
                                step_k=_line_step_k, back_step_after=back_step_after)
            _n_stack = len(_lines)
        else:
            _stack = [_ln for _k, _ln in enumerate(_lines) if _k != _big_i]
            # Шаг СТОПКИ — свой (intro_big_step, доработка ZY-2), а не общий: большая
            # строка шаг не занимает, и её кегль подбирается под высоту стопки.
            _ys_stack = intro_line_ys(_stack, back_step, _any_back, _anchor, meta["h"],
                                      step_k=_big_step_k, back_step_after=back_step_after)
            _lx, _lk, _ys = intro_big_layout(_lines, _ys_stack, _fsize_base, _line_fonts,
                                             back_scale, style.intro_big_gap,
                                             style.intro_big_over)
            # По горизонтали у такой группы видно не строку, а весь блок; ширина блока —
            # из центровки: lx большой = −total/2 (intro_big_layout). Второй копии
            # формулы не заводим, автофит мерит то же, что считает раскладка.
            _big_total = -2.0 * float(cast(Any, _lx)[_big_i])
            _n_stack = len(_stack)
        _intro_ly.append(_ys)
        _intro_lx.append(_lx)
        _intro_lk.append(_lk)
        # Базовая позиция блока интро: невзведённая (без зума) позиция по
        # вертикали от ЦЕНТРА кадра = INTRO_Y(+INTRO_Y2) − INTRO_BASE_Y + iDy. gDy НЕ
        # включаем — он уже живёт отдельным полем dy (драг правит dy в кэше
        # плана), а превью сложит y + dy. iDy (опускание под INTRO_SAFE_TOP) считает
        # Python — шаблон берёт готовое число, CSS-позиция блока в превью уходит.
        # От НЕУЖАТОГО масштаба у ПРИВЯЗАННОГО интро (граница): автофит там
        # режет только Scale, и опускание от него не зависит. У ОТКРЕПЛЁННОГО — от
        # фактического ds: автофит его и увеличивает, а крупное интро без
        # этого вылезало за верх кадра. При якоре «first» блок по числу строк не
        # пересчитывается: первая строка на месте, значит и центр блока —
        # как у одной строки, добавленные строки свисают вниз и верх не поднимают.
        # Высота блока — по тому же межстрочному шагу, что у строк (step_k):
        # раздвинули строки — блок выше, и под SAFE_TOP его опускают сильнее.
        # Строк у группы с большой — по стопке: большая строка шаг не занимает, её кегль
        # подогнан под стопку и выше блока не выходит.
        # Автофит: применяется ТОЛЬКО если группу НЕ трогали руками
        # (_gs == 100). Если gs != 100 — пользователь явно задал масштаб рукой (рука
        # сильнее автофита), автофит не урезает его значение.
        # Зум Камеры 1 в автофит входит, только пока интро к ней привязано:
        # откреплённый текст её зумом не растёт — ключей нет, значит _zoom_max даёт 100.
        # Откреплённое интро подгоняется к ширине кадра в ОБЕ стороны:
        # увеличивать его зумом больше некому, поэтому доля ширины — из ручки intro_fit_w,
        # а потолок увеличения — из intro_fit_max.
        if _gs == 100:
            _ds = _intro_fit_ds(_lines, _ts, _te, _ds, meta["w"], _G,
                                cam1_scale if _intro_cam else [],
                                meta["fps"], back_scale, intro_font_ps, intro_hl_font_ps,
                                _fsize_base, holds=holds, big_w=_big_total,
                                fit_w=None if _intro_cam else _fit_w,
                                both_ways=not _intro_cam,
                                fit_max=None if _intro_cam else _fit_max)
        _idy = _intro_i_dy(meta["h"], 1 if _anchor == "first" else _n_stack,
                           _gs if _intro_cam else _ds, step_k=_line_step_k)
        # ds головной строки = готовое значение автофита: шаблон читает GRP[0].ds,
        # превью — plan.intro[].ds, второй копии расчёта нет.
        if _lines:
            if _ds != 100:
                _lines[0]["ds"] = _r(_ds)
            else:
                _lines[0].pop("ds", None)
        _y = round((style.intro_y) + _G * (-INTRO_BASE_Y + _idy), 2)
        if _on2:
            _y = round(_y + (style.intro_y2), 2)
        # Кегль каждой строки (back_scale/lk) и Y базовых линий — одни и те же числа нужны
        # и безопасной зоне ниже, и решению про полосу субтитров.
        _line_sizes = intro_line_sizes(_lines, _fsize_base, back_scale, _lk)
        # ---- Безопасная зона: сдвиг считается ПОСЛЕ автофита и по ФАКТИЧЕСКОМУ
        # верху блока, а не по половине межстрочного шага (_intro_i_dy). Причина замера:
        # откреплённое интро автофит УВЕЛИЧИВАЕТ, а верх блока держит капитель строки или
        # «большое слева», и у трёх групп стиля по умолчанию верх выходил 164–253 px при
        # INTRO_SAFE_TOP = 285 (ядро формулы _intro_i_dy — n/2·LINE_STEP — про капитель и lk
        # не знает). Верх считает та же функция, что решает про полосу субтитров
        # (layout.intro_block_span): второй копии формулы не заводится.
        # Привязанное интро сюда не заходит вовсе, как и группа, которой автофит не менял
        # масштаб (ручной gs и gs по умолчанию): их числа держит golden.
        if not _intro_cam and _ds != _gs and any(v is not None for v in _ys):
            _top, _ = intro_block_span(_ys, _line_sizes, meta["h"], ds=_ds, g=_G * 100.0,
                                       y=_y, dy=_dy, zoom=100.0, intro_cam=False,
                                       fonts=_line_fonts)
            if _top < INTRO_SAFE_TOP:
                # Сдвиг едет в тот же iDy/y, что уже уходят в .jsx (INTRO_IDY) и план (y):
                # превью покажет то же, второй копии сдвига нет. У откреплённого интро зум
                # не применяется (zk = 1), поэтому сдвиг кадра = G·ΔiDy — отсюда деление на G.
                _idy += (INTRO_SAFE_TOP - _top) / _G
                _y = round((style.intro_y) + _G * (-INTRO_BASE_Y + _idy), 2)
                if _on2:
                    _y = round(_y + (style.intro_y2), 2)
        # ---- Задание MH: группа стоит на полосе субтитров — гаснет к появлению
        # следующего. Решение — одна функция (layout.intro_hits_subs) на готовых числах
        # плана: Y базовых линий и кегли строк группы (back_scale/lk), масштаб прекомпа
        # (ds), позиция (y/dy), зум Камеры 1 в момент появления субтитра (пока интро к
        # ней привязано) и полоса субтитров (posy, кегль, ряд стопки). Копии формул
        # здесь нет: те же ys/кегли/ds/y читают превью и шаблон.
        # Окно режется ПОСЛЕ автофита: автофит считает по своему (более длинному) окну,
        # а полоса блока — по готовому ds. Глитч-группы (ПРАВКА 3/4) — то же правило
        # поверх их окна: короче, но никогда не позже него.
        # Последняя группа ролика под правило НЕ попадает: владелец держит её до конца
        # нарочно — у неё своё окно с HOLD (+1.0) и запас F_OUT, а субтитр, идущий после
        # интро, идёт уже по сценарию. Приёмка архитектора на 8 роликах: из 76 правленых
        # руками групп он не тронул НИ ОДНОЙ из 7 последних, а правило срезало их на
        # 1.5–1.9 с (C1459 гр.13, C1461-007 гр.14, C1462-004 гр.23). Вместе с окном
        # отпадает и сжатие появления (п.2): его включает только укороченное окно, а у
        # последней группы окно своё и длинное — анимации успевают до затухания с запасом.
        # Кегли строк (_line_sizes) посчитаны выше, вместе с безопасной зоной:
        # число одно на обе двери — второй копии формулы нет.
        _fstart = _te - _fade
        _sub_cut_win = None
        _ns = _next_sub_after(_tms, _sub_starts)
        # Последняя группа — та же, что держит HOLD в _intro_group_window (gi == n−1).
        _last_grp = _g == len(_intro_groups) - 1
        if intro_sub_cut and not _last_grp and _ns is not None and _ns < _te - 1e-6:
            _srow, _sstep = _sub_row_at(_ns, subs_plan, _hl_step, _sub_step)
            if intro_hits_subs(
                    _ys, _line_sizes, _posy, _fsize, h=meta["h"], ds=_ds, g=_G * 100.0,
                    y=_y, dy=_dy,
                    zoom=_zoom_max(cam1_scale if _intro_cam else [], meta["fps"],
                                   _ns, _ns, holds=holds),
                    intro_cam=_intro_cam, fonts=_line_fonts, sub_font=font_ps,
                    sub_row=_srow, sub_step=_sstep):
                _te, _fade, _fstart = intro_sub_window(_ts, _te, _ns, intro_sub_fade)
                if _fade > intro_fade:
                    # Общий фейд короче нового (intro_fade < intro_sub_fade): в шаблоне ключ
                    # «100» ставится в max(outStart, outEnd−F_FADE), то есть игру укоротит
                    # он, — и план обязан нести то же число, иначе превью покажет не то, что
                    # соберётся в AE.
                    _fade = intro_fade
                    _fstart = _te - _fade
                _sub_cut_win = [_r(_fstart), _r(_te)]
        _intro_sub_fx.append(_sub_cut_win)
        # ---- Окна групп не накладываются друг на друга: группа гаснет НЕ ПОЗЖЕ появления
        # следующей, иначе две группы висят в кадре разом (владелец разводил их руками).
        # Ограничение третье и последнее, поверх базовой формулы с продлением под глитч
        # (посчитана выше) и гашения к субтитру: из трёх берётся самое раннее.
        # t_last — последнее слово группы, anim_dur — длительность его появления (что не
        # влезает вместе с фейдом, то ужимается: обе не короче INTRO_MIN_PART). Сжатие
        # САМОЙ анимации живёт в INTRO_SQ ниже: начало затухания сдвинулось — слово играет
        # появление за остаток. Второго механизма сжатия нет.
        _gmax = max(_tms) if _tms else 0.0
        _anim_dur = 0.0
        for _ln in _lines:
            if _gmax in (_ln.get("times") or []):
                _anim_dur = max(_anim_dur, _intro_appear_dur(_ln.get("anim") or "",
                                                            bool(_ln.get("is_count"))))
        # Старт следующей группы — её же ts (in_at): у последней группы его нет вовсе, а
        # у группы без слов во времени (times пусты) появления тоже нет — подрезать не подо что.
        _next_tms = _grp_stats[_g + 1][1] if _g + 1 < len(_grp_stats) else None
        _next_in = _fx_ts[_g + 1] if _next_tms else None
        _fstart, _te, _fade = intro_clamp_window(_ts, _gmax, _fstart, _te, _fade,
                                                 _anim_dur, _next_in)
        # То же окно уезжает в .jsx (INTRO_FX) — ровно те числа, что несёт план (te/fade):
        # превью рисует план, AE собирает INTRO_FX, второй копии расчёта нет.
        _fx_win[_g] = [_r(_fstart), _r(_te)]
        # ---- Слова успевают доиграть появление до начала затухания. Слово,
        # чья анимация появления (всё, что ставит introAnimFX: глитч INTRO_ANIMS, фейд и
        # масштаб F_DUR, раскрытие, up/left/right, счётчик) заканчивается позже начала
        # затухания группы, играет её за d = max(0.1, начало затухания − момент слова) —
        # короче, но не короче 0.1 с. Коэффициент d/D на слово считает Python и отдаёт и
        # в .jsx (INTRO_SQ), и в план (превью анимирует тем же числом). Владелец ровно это
        # и правил руками: «глитч 0.27 → 0.17–0.19, фейд/масштаб 0.3 → 0.1–0.17 — чтобы
        # хотя бы увидеть текст».
        # В построчном режиме анимацию играет СЛОЙ строки от её первого слова — коэффициент
        # нулевого слова считается по нему (t0l в шаблоне = min(times)).
        _grp_sq = []
        for _li, _ln in enumerate(_lines):
            _dur = _intro_appear_dur(_ln.get("anim") or "", bool(_ln.get("is_count")))
            _t_first = min(_ln["times"]) if _ln.get("times") else 0.0
            _row_sq = []
            for _wi, _tw in enumerate(_ln.get("times") or []):
                _t_w = _t_first if _wi == 0 else float(_tw)
                _k = 1.0
                # Допуск 1 мс (1/16 кадра при 60 fps): времена плана округлены до
                # десятитысячных, и слово, чья анимация кончается на сотые доли
                # миллисекунды позже начала затухания, сжимать незачем — иначе .jsx
                # получал бы INTRO_SQ с множителем 0.9999 на ровном месте (golden).
                if _t_w + _dur > _fstart + 1e-3:
                    _k = min(1.0, max(0.1, _fstart - _t_w) / _dur)
                _row_sq.append(None if _k >= 1.0 else _r(_k))
            _grp_sq.append(_row_sq)
        _intro_sq.append(_grp_sq)
        # Галка «интро над рото по положению»: группу Камеры 1, чей блок
        # от центра кадра в НИЖНЕЙ половине (зона субтитров), в .jsx поднимают над
        # рото; блок в верхней половине остаётся под ним. Зум камеры не учитываем —
        # он множит позицию и сам блок одинаково, знак суммы (_y + _G*_dy) не меняется.
        # Группы на перебивке (свой нул) и на видеовставке (им и так наверх) не трогаем.
        _above_roto = bool(style.intro_roto_by_pos) and not _on2 and not _front \
            and (_y + _G * _dy) > 0
        _intro_above_roto.append(_above_roto)
        intro_idy.append(_idy)
        # ---- Точка масштабирования прекомпа (intro_scale_anchor) -------------------------
        # Якорь слоя прекомпа ставит шаблон (Anchor Point), а Position он получает уже
        # компенсированным: слой рисует точку источника Ya в Position + (Ya − H/2)·S, и
        # добавка (Ya − H/2)·S оставляет картинку ровно там, где она была, — меняется
        # ТОЛЬКО центр масштабирования. Поэтому текст ужимается от своей точки, а не
        # подтягивается к середине кадра (ручкой масштаба группы и Scale руками в AE).
        # S — масштаб прекомпа при базовом ds (INTRO_SCALE = 96.8 %), а не iSc = 96.8·ds/100:
        # с текущим ds компенсация гасила бы ровно то уменьшение, которое владелец и делает
        # ручкой масштаба (картинка при смене ds не менялась бы вовсе), и текст снова уезжал
        # бы к середине кадра. При ds = 100 числа совпадают, картинка не сдвигается ни на
        # пиксель. Числа готовые: ни в шаблоне, ни в превью формул нет.
        _ay = _scale_anchor_y(_scale_anchor, _ys, meta["h"])
        _ay_dy = _r((_ay - meta["h"] / 2.0) * (INTRO_SCALE / 100.0))
        _intro_anchor_y.append(_ay)
        _intro_anchor_dy.append(_ay_dy)
        intro_plan.append({"group": _g, "on2": bool(_on2),
                           "ts": _ts, "te": _te, "fade": _r(_fade), "lines": _lines,
                           "dx": _dx, "dy": _dy, "ds": _ds,
                           # База блока — от центра кадра. У не-дефолтного
                           # якоря масштаба в неё входит компенсация (G·(Ya − H/2)·S, кадровые
                           # px — та же добавка, что уезжает в Position прекомпа): превью
                           # зовёт это число базой, а центр масштабирования ставит
                           # transform-origin по anchor_y. Второй копии расчёта нет.
                           "y": round(_y + _G * _ay_dy, 2), "ys": _ys,
                           # Множитель длительности появления: [строка][слово],
                           # null — слово успевает (его анимация не сжата). Поля НЕТ, когда
                           # в группе сжимать нечего: превью читает отсутствие как 1, а
                           # .jsx получает массив INTRO_SQ только при сжатых словах (golden).
                           **({"sq": _grp_sq} if any(v is not None for _r_sq in _grp_sq
                                                      for v in _r_sq) else {}),
                           # Большая строка группы: левый край каждой строки
                           # (px прекомпа от центра) и множитель её кегля. Превью рисует
                           # готовые числа. У групп без большой строки полей НЕТ вовсе —
                           # в lines их тоже не кладём: INTRO_GROUPS обязан остаться
                           # прежним (golden).
                           **({"lx": _lx, "lk": _lk} if _big_i is not None else {}),
                           # Точка масштабирования группы (intro_scale_anchor): Y якоря слоя
                           # прекомпа в пикселях прекомпа — по нему превью ставит блоку
                           # transform-origin (второго чтения ключей стиля во фронте нет).
                           # У "comp" поля НЕТ вовсе: origin остаётся прежним — центр
                           # контейнера (как у блока без ключа), и .jsx прежний байт в байт.
                           **({"anchor_y": _ay} if _scale_anchor != "comp" else {}),
                           # Тень прекомпа этой группы: цвет и непрозрачность
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
                           # Галка «интро над рото в нижней половине»: блок
                           # группы ниже центра кадра на Камере 1 — прекомп в .jsx
                           # поднимается над рото (template.py, intro_above_roto_raise).
                           # В строки (lines) поле не кладём: они уезжают в .jsx как
                           # INTRO_GROUPS и обязаны остаться прежними (golden).
                           "above_roto": _above_roto,
                           # Шрифт каждой строки и Y базовых линий (готовые числа):
                           # в сами строки (lines) их не кладём — они уезжают в .jsx как
                           # INTRO_GROUPS, и он обязан остаться прежним (golden).
                           "fonts": _line_fonts})
    # Окна выхода групп для шаблона: Python посчитал их КАЖДОЙ группе (базовая формула +
    # продление под глитч ПРАВКИ 3/4 + гашение к субтитру MH + подрезка под старт следующей
    # группы), шаблон только подставляет — второй копии формулы в нём нет. Ни одной группы
    # со строками (ролик без интро) — подстановок нет, .jsx прежний (golden).
    _intro_fx_decl = ""
    _intro_fx_out = ""
    if any(_l for _l, _t, _g in _grp_stats):
        _fx_js = _jd(_fx_win)
        _intro_fx_decl = ("\n    var INTRO_FX=%s;    // [группа] = [начало фейд-аута, конец слоя]"
                          " прекомпа: окно выхода группы считает Python, окна групп не"
                          " накладываются" % _fx_js)
        _intro_fx_out = ("\n            if (INTRO_FX[gI]){ outStart=INTRO_FX[gI][0]; outEnd=INTRO_FX[gI][1]; }"
                         "  // окно выхода группы — из плана: вторая копия формулы не заводится")
    # Группы, гаснущие к появлению следующего субтитра: [группа] = [начало
    # затухания, конец слоя] — окно уже (next_sub) и спад короткий (intro_sub_fade). Правило
    # НЕ менялось, но подстановка идёт ПЕРЕД INTRO_FX: у INTRO_FX окно итоговое (в нём уже
    # учтено и гашение к субтитру, и подрезка под старт следующей группы), и применять его
    # надо последним — иначе поздняя запись вернула бы окно назад и группы наложились бы.
    # Ни одной такой группы — подстановки пусты, .jsx прежний (golden).
    _intro_sub_fx_decl = ""
    _intro_sub_fx_out = ""
    if any(w is not None for w in _intro_sub_fx):
        _sub_fx_js = _jd(_intro_sub_fx)
        _intro_sub_fx_decl = ("\n    var INTRO_SUB_FX=%s;    // [группа] = [начало фейд-аута,"
                              " конец слоя] группы, гаснущей к появлению субтитра: блок стоит на"
                              " полосе субтитров" % _sub_fx_js)
        _intro_sub_fx_out = ("\n            if (INTRO_SUB_FX[gI]){ outStart=INTRO_SUB_FX[gI][0];"
                             " outEnd=INTRO_SUB_FX[gI][1]; }  // гаснет к субтитру,"
                             " когда стоит на его полосе (окно не позже INTRO_FX)")
    # Множители длительности появления: [группа][строка][слово], null — слово
    # успевает доиграть до начала затухания. Объявляется только при сжатых словах: нет их —
    # ни массива, ни функции, ни лишнего аргумента в вызовах, .jsx прежний (golden).
    _intro_sq_decl = ""
    _intro_sq_fn = ""
    _sq_used = any(v is not None for _grp_sq in _intro_sq for _row_sq in _grp_sq for v in _row_sq)
    if _sq_used:
        _intro_sq_decl = ("\n    var INTRO_SQ=%s;    // [группа][строка][слово] — множитель"
                          " длительности появления: слово, чья анимация не успевала до начала"
                          " затухания группы, играет её короче" % _jd(_intro_sq))
        _intro_sq_fn = (
            '\n        function introSQ(gI,qi,wi){ try{ var a=INTRO_SQ[gI];'
            ' if(!a) return 1; a=a[qi]; if(!a) return 1; var v=a[wi];'
            ' return (v>0 && v<1)?v:1; }catch(e){ return 1; } }'
        )
    # .jsx-группы — из ГОТОВОГО плана (автофит уже в ds), чтобы .jsx и превью не
    # разошлись на одном и том же значении.
    intro_groups_js = _jd([p["lines"] for p in intro_plan])
    # Акцент или задний план: используются, только если хоть одна строка
    # реально получила accent_font. Иначе плейсхолдеры шаблона пусты и .jsx не меняется ни на байт (golden).
    _accent_used = any("accent_font" in ln for p in intro_plan for ln in p["lines"])

    return IntroPlan(
        intro=intro_plan, groups_js=intro_groups_js, idy=intro_idy,
        on2=_intro_on2, front=_intro_front, above_roto=_intro_above_roto,
        anchor=_intro_anchor, scale_anchor=_scale_anchor,
        anchor_y=_intro_anchor_y, anchor_dy=_intro_anchor_dy,
        ly=_intro_ly, lx=_intro_lx, lk=_intro_lk,
        sq=_intro_sq, sub_fx=_intro_sub_fx,
        fx_decl=_intro_fx_decl, fx_out=_intro_fx_out,
        sub_fx_decl=_intro_sub_fx_decl, sub_fx_out=_intro_sub_fx_out,
        sq_decl=_intro_sq_decl, sq_fn=_intro_sq_fn, sq_used=_sq_used,
        accent_used=_accent_used)
