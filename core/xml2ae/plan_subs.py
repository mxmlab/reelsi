# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Блок субтитров плана сцены (этап 1 распила scene_plan).

`scene_plan` был одной функцией на 3253 строки с 24 вложенными функциями, делившими
состояние замыканиями; ревью назвало это главным долгом. Первый этап распила вынес сюда
ВЕСЬ блок субтитров: обрезку конца слова по соседу (`_endc`), кегль и геометрию полосы
(posy/шаг/подъём), стопку подряд жёлтых, появление жёлтых — в том числе коротких
, — данные циклов SUBS/SUB_ROWS/SUB_STACK и подстановки шаблона
(HL_ROW_WORD, HL_BLUR, hlDur/hlRowDur/hlBlur).

Перенос ПОСТРОЧНЫЙ: поведение, числа, порядок операций и текст подстановок не менялись
ни на байт (проверяется эталоном fixtures/golden_geometry.jsx и побайтовым сравнением
.jsx/плана). Имена локальных переменных оставлены как в scene_plan — поэтому тело
перенесено дословно, а входы распаковываются в преамбуле.

Вход — один неизменяемый `SubsInputs`, выход — один `SubsPlan` со всем, что `scene_plan`
читает дальше. Стиль приходит структурой `StyleValues` одним полем `style` (
её читает один раз `plan_style.read_style`), а общие с другими блоками правила
(`_accent_word` — регистр слова, `_parse_intro_count` — разбор числа-счётчика) остаются
в `build.py` и приходят параметрами: второй копии нет.
"""
from dataclasses import dataclass
from typing import Any, Callable, cast

from .jsutil import _jd
from .layout import (HL_DUR, SubAnim, _stack_layout, hl_appear_dur, hl_size_expr,
                     hl_size_factor, sub_anim_js, sub_anim_join_call, sub_anim_keys,
                     sub_anim_preset, sub_fill_call, sub_fill_js, sub_glow_call,
                     sub_glow_js, sub_wbg, sub_wbg_js, sub_wbg_moment, sub_wbg_plan)
from .plan_style import StyleValues
from .template import (SUBS_LOOP_ROWS, SUBS_LOOP_STACK, SUBS_LOOP_STACK_JOINED,
                       SUBS_LOOP_WORDS, SUBS_LOOP_WORDS_JOINED)


@dataclass(frozen=True)
class SubsInputs:
    """Вход блока субтитров: всё, что `scene_plan` знает к моменту вызова.

    Поля названы как локальные переменные scene_plan, а `width`/`height` — это
    `meta["w"]`/`meta["h"]`: тело переноса читает те же имена. `style` — структура
    стиля, прочитанная ОДИН раз (`plan_style.read_style`); нестилевые
    входы остаются отдельными полями, а `accent_word`/`parse_count` — функции,
    которые живут в build.py и общие с другими блоками (своего правила регистра
    и своего разбора числа-счётчика в модуль не заводится).
    """
    # Слова транскрипта [(начало, конец, слово)] в кадрах и разметка по ним: жёлтые,
    # ручные разделители серий, слова со счётчиком, склейки в строку.
    subs: list[Any]
    hl: set[Any]
    brk: set[Any]
    cnt: set[Any]
    joins: set[Any]
    # Шрифты (PostScript-имена): базовый и жёлтых.
    font_ps: str
    hl_font_ps: str
    # Кадр (meta["w"]/meta["h"]) и частота: в кадрах считает XML, в секунды переводит план.
    width: int
    height: int
    fps: float
    # Клипы камер — только ради границ катов: строка субтитров не тянется через кат.
    cams: list[dict[str, Any]]
    # Пословные тайминги из сайдкара (None — их нет): ими уточняются строки.
    word_timings: Any
    # Резолвнутый и прочитанный стиль: регистр, геометрия полосы, жёлтые в строке.
    style: StyleValues
    accent_word: Callable[[str, str], str]
    parse_count: Callable[..., Any]


@dataclass(frozen=True)
class SubsPlan:
    """Выход блока субтитров: ровно те имена, что scene_plan читает дальше.

    Первые поля уезжают в .jsx подстановками шаблона, остальные — геометрия полосы
    субтитров: её читают и план (предпросмотр), и шаблон, и окна интро.
    """
    subs: list[dict[str, Any]]            # элементы субтитров для плана/превью (plan["subs"])
    subs_js: str          # данные SUBS — строки цикла слов
    sub_loop: str         # цикл субтитров: SUBS_LOOP_WORDS/SUB_ROWS (+ цикл стопки)
    hl_row_decl: str      # объявление HL_ROW_WORD (жёлтые в строке)
    hl_blur_decl: str     # объявление HL_BLUR (блюр появления жёлтых)
    hl_blur_fn: str       # функция hlBlur(L, t0) — сигнатура-контракт
    hl_short_fn: str      # hlDur/hlRowDur и HL_HD для коротких жёлтых
    sub_anim_decl: str    # объявление чисел пресета появления слов (пусто при sub_anim="none")
    sub_anim_fn: str      # функция subAnimKeys — те же ключи, что в плане (пусто при "none")
    sub_anim_font: str    # тонкое начертание пресета weight ("" — ступеньки нет)
    sub_anim_glitch: bool # пресет появления — глитч: сборке нужна функция глитча интро
    # Подложка слова, заливка текста и свечение (класс Б каталога): числа для превью
    # и подстановки .jsx. Выключено — пусто, .jsx прежний байт в байт (golden).
    sub_wbg_js: str       # создание шейп-слоя подложки и функция wbgKeys
    sub_wbg_tail: str     # окна показа подложки и кривые — после цикла слов
    sub_wbg_plan: dict[str, Any] | None   # числа подложки для превью (None — выключена)
    sub_fill_fn: str      # функция subGrad (пусто при сплошной заливке)
    sub_glow_fn: str      # функция subGlow (пусто при выключенном свечении)
    hl_size_k: float      # кегль жёлтого слова множителем базового: 1.0 = как сегодня
    hl_blur_on: bool      # галка блюра — уезжает в план (превью)
    sub_scale: float      # масштаб слоя прекомпа субтитров, %
    posy: int             # Y полосы субтитров, px (sub_y * высота кадра)
    hl_step: float        # шаг строк стопки, px
    hl_rise: float        # подъём появления жёлтого, px
    hl_dur: float         # длительность появления жёлтого, с (layout.HL_DUR)
    fsize: int            # кегль субтитров (в режиме строк — ужатый под ширину кадра)
    fsize_base: int       # кегль ДО ужатия строк: им рисуется интро (доработка ZL)
    sub_step: float       # шаг строк субтитров, px (1.18 * fsize)


def plan_subs(inp: SubsInputs) -> SubsPlan:
    """Субтитры плана сцены: слова -> строки -> стопка -> данные циклов -> подстановки.

    Тело — дословный перенос блока из scene_plan (до распила — строки 1051-1494):
    имена локальных переменных оставлены прежними, поэтому ни одна строка не переписана.
    """
    subs, hl, brk, cnt, joins = inp.subs, inp.hl, inp.brk, inp.cnt, inp.joins
    font_ps, hl_font_ps = inp.font_ps, inp.hl_font_ps
    width, height = inp.width, inp.height
    _fps0 = inp.fps
    cams, word_timings = inp.cams, inp.word_timings
    # Стиль приходит структурой, прочитанной один раз: sub_case, геометрия
    # полосы, жёлтые в строке и блюр — оттуда; своей копии чтения ключей в модуле нет.
    style = inp.style
    sub_case = style.sub_case
    sub_words_per_row, sub_rows_max = style.sub_words_per_row, style.sub_rows_max
    # Общие с другими блоками правила — по-прежнему в build.py, сюда приходят параметрами:
    # своей копии _accent_word/_parse_intro_count в модуле нет.
    _accent_word = inp.accent_word
    _parse_intro_count = inp.parse_count
    # обрезаем конец слова по началу следующего, чтобы соседние (особ. мелкие «и/в») не накладывались
    def _endc(k: int) -> int:
        s, e, w = subs[k]
        ns = subs[k + 1][0] if k + 1 < len(subs) else None
        return min(e, ns) if (ns is not None and ns > s) else e

    _posy = int(height * float(style.sub_y))
    _hl_step = round(height * 0.06224, 2)
    _hl_rise = round(height * 0.06406, 2)
    # Длительность подъёма/проявления жёлтых, с: ОДНО число на всю сборку —
    # константа layout.HL_DUR. Шаблон получает его подстановкой (template.py), план несёт
    # предпросмотру (hl_dur): своей копии числа в JS не заводится, как и у остальной
    # геометрии субтитров.
    _hl_dur = HL_DUR
    _fsize = max(60, int(width * 0.13))
    # Кегль интро = кегль ДО ужатия строк (доработка ZL). В режиме строк автофит ужимает
    # _fsize под самую длинную строку, но интро — не строка субтитров: раньше оно брало
    # ужатый кегль и выходило в 2.4 раза мельче, чем в режиме по слову. Запоминаем
    # неужатый здесь, ДО ветки строк; в режиме по слову _fsize_base == _fsize и .jsx
    # остаётся прежним байт в байт (golden).
    _fsize_base = _fsize
    _sub_step = round(_fsize * 1.18, 2)
    # Масштаб СЛОЯ прекомпа субтитров, %: кегль/раскладка не трогаются,
    # 100 = как сегодня. При 100 подстановка в шаблон пуста — .jsx прежний (golden).
    sub_scale = float(style.sub_scale)
    # Появление БАЗОВОГО (белого) слова: пресет стиля sub_anim. Ключи считает
    # layout (единственный источник кривых), а план несёт их каждому белому слову
    # полем anim; .jsx ставит ТЕ ЖЕ ключи на слой, превью — на спан слова.
    # Жёлтые пресет не трогает: у них своя анимация появления (подъём/проявление/блюр),
    # и две анимации на одном слое складывались бы дважды. Слово жёлтое — пресет молчит.
    _anim: SubAnim | None = sub_anim_preset(style.sub_anim, style.sub_anim_dur,
                                            style.sub_anim_amt, style.sub_anim_font)
    _anim_sub = sub_anim_js(_anim)
    # Тонкое начертание пресета weight уезжает и в план: превью обязано ступенить
    # начертание в ТОЙ ЖЕ середине dur, что .jsx, иначе картинки разъедутся.
    _anim_font = _anim.font if _anim is not None else ""
    # Пресет-глитч: сборке нужна функция глитча интро (introAnimFX) — свой вызов в
    # subAnimKeys её зовёт, и без неё подстановка упала бы в try/catch и промолчала.
    _anim_glitch = _anim is not None and _anim.name == "glitch"
    # Подложка слова (класс Б каталога): числа и подстановки .jsx. Выключена — пусто,
    # .jsx со стилем по умолчанию остаётся прежним байт в байт (golden_geometry.jsx).
    _wbg = sub_wbg(style)
    _wbg_sub = sub_wbg_js(_wbg) if _wbg is not None else {}
    # Заливка текста градиентом и свечение: эффекты на слоях слов. Функции — одни на
    # сборку (в циклах только вызовы), выключено — пусто.
    _grad_fn = sub_fill_js(style)
    _grad_call = sub_fill_call(style)
    _glow_fn = sub_glow_js(style)
    _glow_call_hl = sub_glow_call(style, "hl")
    _glow_call_rows = sub_glow_call(style, "w_hl")
    # В циклах стопки признака «жёлтое слово» нет вовсе — там ВСЕ слова жёлтые:
    # подстановка идёт литералом, а не именем переменной (undefined уронил бы сборку).
    _glow_call_stack = sub_glow_call(style, "true")
    # Кегль жёлтого слова (ручка hl_size_k) в циклах субтитров: выражение у каждого
    # цикла своё — строка кегля общая с белыми словами только в цикле «по слову»
    # и в цикле строк. При 1.0 каждое выражение — ровно прежний текст FONT_SIZE/cur_fsz,
    # и .jsx со стилем по умолчанию остаётся прежним байт в байт (golden_geometry.jsx).
    _hl_k = hl_size_factor(style.hl_size_k)
    _hl_fsz_words = hl_size_expr(_hl_k, "FONT_SIZE", "hl")    # «по слову» и склейки
    _hl_fsz_rows = hl_size_expr(_hl_k, "cur_fsz", "w_hl")     # цикл строк
    _hl_fsz_all = hl_size_expr(_hl_k, "FONT_SIZE")            # стопка: слова только жёлтые

    def _w_fs(is_hl: bool) -> float:
        """Кегль, которым слово реально нарисуется, — для ЗАМЕРА ширины (автофит).

        У жёлтого кегль умножен на hl_size_k: мерить общим FONT_SIZE значило бы ужать
        (или не ужать) слово не тем кеглем, каким его ставит .jsx. При 1.0 множитель
        ничего не меняет — ширина та же, что была.
        """
        return _fsize * _hl_k if is_hl else _fsize
    # Жёлтые в режиме строк: HL_ROW_WORD нужен только циклу строк — в режиме
    # «по слову» объявления нет вовсе, и .jsx остаётся прежним байт в байт (golden).
    hl_row_decl = ("" if sub_words_per_row <= 1 else
                   ("\n    var HL_ROW_WORD = %s;   // жёлтые в строке (hl_row_anim): true — въезжает,"
                    " когда слово произнесено; false — вместе со строкой"
                    % ("true" if style.hl_row_anim == "word" else "false")))
    # Блюр появления жёлтых: выключен — ни объявления, ни функции, ни вызовов,
    # все три подстановки пусты и .jsx прежний байт в байт (golden). Сами hl_blur_fn и
    # hl_blur_call собираются НИЖЕ: у короткого жёлтого и блюр играет свою
    # длительность, а её до расчёта циклов ещё не знают.
    hl_blur_on = bool(style.hl_blur)
    hl_blur_call = " hlBlur(L, t0);" if hl_blur_on else ""      # цикл строк — как было
    hl_blur_decl = hl_blur_fn = ""
    if hl_blur_on:
        hl_blur_decl = ("\n    var HL_BLUR = %g;   // сила блюра появления жёлтых, px"
                        " (Gaussian Blur, повтор краёв выключен)" % float(style.hl_blur_amt))
    # Короткое жёлтое слово: подъём, проявление и блюр играли общие HL_DUR =
    # 0.35 с, а слово с видимым временем меньше 0.35 с гасло (outPoint = gend) посреди
    # анимации — «просто исчезало». Длительность d = min(HL_DUR, HL_FIT * видимое время)
    # считает Python (layout.hl_appear_dur) для КАЖДОГО такого слова и кладёт её полем 7
    # строки данных SUBS/SUB_STACK ([.., gend, cnt, hd] — сразу за полем счётчика: поле 6
    # занято cnt_items, его не трогаем). Нет ни одного укороченного жёлтого — нет ни полей,
    # ни функции hlDur, ни новых подстановок: .jsx побайтово как на main (golden).
    _hl_hd: dict[int, float] = {}                     # индекс жёлтого слова -> своя длительность появления, с
    # Короткая СТРОКА: цикл строк зажимал момент появления окном
    # (r_t1 - HL_DUR), но если строка короче HL_DUR, момент оставался в её начале, и подъём с
    # проявлением обрывались на конце строки. Длительность d = hl_appear_dur(r_t1 - w_t0) —
    # ТА ЖЕ функция, что у MA; видимое время считается до конца строки. Python кладёт её
    # полем 3 слова данных SUB_ROWS ([начало, текст, hl, hd]) и полем hd слова в плане
    # (превью берёт готовое). Нет ни одной такой строки — нет ни поля, ни функции hlRowDur,
    # ни подстановок: .jsx побайтово прежний (golden).
    _hl_row_hd: dict[int, float] = {}                 # индекс жёлтого слова СТРОКИ -> своя длительность, с
    hl_short_fn = ""

    def _hl_loop(elem: str, t_expr: str = "sw[0]/FPS", wbg_key: str = "call_hl",
                 glow: str = "hl", **kw: Any) -> dict[str, Any]:
        """Подстановки цикла субтитров для элемента `elem` (имя переменной строки данных):
        длительность появления в ключах подъёма/проявления и вызов блюра. Пока укороченных
        жёлтых нет — ровно прежний текст: HL_DUR и hlBlur(L, t0). У укороченного длительность
        едет в блюр через HL_HD (сигнатура hlBlur(L, t0) — контракт ).

        `t_expr` — выражение времени слова в ЭТОМ цикле: градиент текста ставится по
        прямоугольнику слова, а он у слоя с ключевым текстом (счётчик) свой на каждом
        моменте — «на глаз» брать нулевой кадр нельзя. `wbg_key` — какой вызов подложки
        слова кладёт цикл в своё место (`sw`/`rsw`), `glow` — имя признака «слово жёлтое»
        (в стопке признака нет вовсе — там литерал `true`).
        """
        # Появление базового слова — общая часть всех циклов слов: у выключенного
        # пресета подстановки пустые, и .jsx остаётся прежним байт в байт (golden).
        # Цикл СО СКЛЕЙКАМИ кладёт свою строку вызова поверх (sub_anim_join_call).
        kw.update(_anim_sub)
        # Подложка, градиент и свечение — тоже общая часть: выключены ручки — пустые
        # подстановки, и .jsx прежний (golden). Своей копии вызовов в шаблоне нет.
        kw["wbg_base"] = _wbg_sub.get("call_base", "")
        kw["wbg_hl"] = _wbg_sub.get(wbg_key, "")
        kw["wbg_row"] = ""
        kw["grad_call"] = sub_fill_call(style, t_expr)
        # Цикл СО СКЛЕЙКАМИ рисует жёлтые слова вторым проходом по своим данным (kw):
        # время слова у него своё, поэтому и выражение для градиента отдельное.
        kw["grad_call_kw"] = sub_fill_call(style, "kw[0]/FPS")
        kw["glow_call"] = sub_glow_call(style, glow)
        kw["hl_dur_js"] = ("hlDur(%s)" % elem) if _hl_hd else "HL_DUR"
        if hl_blur_on:
            if _hl_hd:
                kw["hl_blur_call"] = " HL_HD = hlDur(%s); hlBlur(L, t0);" % elem
            elif _hl_row_hd:
                # HL_HD завели короткие СТРОКИ, а этому циклу укороченных не
                # досталось: переменную обязательно вернуть к общей — иначе блюр возьмёт
                # длительность последнего жёлтого строки, что осталась в ней с прошлого цикла.
                kw["hl_blur_call"] = " HL_HD = HL_DUR; hlBlur(L, t0);"
            else:
                kw["hl_blur_call"] = hl_blur_call
        else:
            kw["hl_blur_call"] = ""
        return kw
    from core.subs import build_sub_rows
    from core import fonts as _fonts

    rows: list[int] | None
    gend: list[float] | None
    _sub_w: Any

    if sub_words_per_row <= 1:
        rows, gend = _stack_layout(subs, hl, brk, joins)
        max_line_w = 0.92 * width
        # Регистр субтитров: применяем к ГОТОВОМУ тексту в scene_plan — .jsx
        # и превью читают преобразованное, второй копии правила нет. upper (дефолт) —
        # слова из XML уже капсом, upper() их не меняет, .jsx прежний (golden). В
        # покадровом режиме каждое слово — своя реплика, sentence = Заглавная на каждом.
        def _sub_w(w: str) -> str:
            return _accent_word(w, "title" if sub_case == "sentence" else sub_case)
        subs_plan = []
        cnt_items = []
        any_sub_count = False
        # Короткие жёлтые: видимое время слова — от его появления до общего
        # конца связки (outPoint слоя = gend). Кому общей HL_DUR не хватает — своя
        # длительность: она уезжает и в данные цикла (поле 7), и в план (hd — превью).
        for k in sorted(hl):
            _d = hl_appear_dur((gend[k] - subs[k][0]) / _fps0)
            if _d < _hl_dur:
                _hl_hd[k] = _d
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
            w_px = _fonts.text_width(ps, w, _w_fs(k in hl))
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
            if k in _hl_hd:
                item["hd"] = _hl_hd[k]
            # Появление по пресету — только белому слову (см. _anim выше): у жёлтого
            # своя анимация, и вторая на том же слое сложилась бы с первой.
            if _anim is not None and k not in hl:
                item["anim"] = sub_anim_keys(_anim, s / _fps0)
            # Подложка слова: момент, с которого слово считается текущим. В режиме
            # «по слову» это время самого слова — оно и есть своя реплика.
            if _wbg is not None:
                item["wbg"] = {"t": sub_wbg_moment(s / _fps0)}
            if w_px is not None and w_px > max_line_w:
                shrunk_fs = max(40, int(_fsize * max_line_w / w_px))
                if shrunk_fs < _fsize:
                    item["fsize"] = shrunk_fs
            subs_plan.append(item)

        def _sub_row(k: int, end: int, wd: str, hl_v: int) -> list[Any]:
            """Строка данных цикла слов: [начало, конец, слово, hl, ряд, gend] плюс поле
            счётчика (индекс 6), поле длительности появления укороченного жёлтого
            (индекс 7) и — у пресета масштаба — поле стартового размера роста (тоже
            индекс 7: два поля вместе не встречаются, у роста нет укороченных жёлтых).
            Поля добавляются, только если сборке есть что в них положить: без укороченных
            жёлтых и пресета .jsx прежний байт в байт (golden)."""
            r = [subs[k][0], end, wd, hl_v, cast(list[int], rows)[k], cast(list[float], gend)[k]]
            if any_sub_count:
                r.append(cnt_items[k])
            if _hl_hd:
                while len(r) < 7:
                    r.append(None)              # поле счётчика: счётчиков в ролике нет
                r.append(_hl_hd.get(k, _hl_dur))
            if _anim is not None and _anim.name == "pop":
                # Стартовый размер роста едет полем 7 — цикл СО СКЛЕЙКАМИ читает его
                # оттуда (в обычном цикле то же число стоит в HL_W_SC подстановкой).
                while len(r) < 7:
                    r.append(None)
                r.append(_anim.scale)
            return r

        any_joins = bool(joins)
        sub_tpl = SUBS_LOOP_WORDS_JOINED if any_joins else SUBS_LOOP_WORDS
        subs_js = _jd([_sub_row(k, _endc(k), _sub_w(w), 1 if k in hl else 0)
                       for k, (s, e, w) in enumerate(subs)])
        # Склейки — своя строка вызова появления базового слова: у второго прохода
        # позиция это finalY, а пик масштаба берётся из данных (sub_anim_join_call).
        _anim_call = (sub_anim_join_call(_anim) if (any_joins and _anim is not None)
                      else _anim_sub["call"])
        if any_sub_count:
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
            sub_loop = sub_tpl % _hl_loop("sw", base_anim=_anim_call, sub_count_code=sub_count_code,
                                          hl_fsz=_hl_fsz_words)
        else:
            sub_loop = sub_tpl % _hl_loop("sw", base_anim=_anim_call, sub_count_code="",
                                          hl_fsz=_hl_fsz_words)
        sub_rows_js = "[]"
    else:
        hl_row_stack = bool(style.hl_row_stack)
        # Стопка подряд жёлтых раскладывается ТЕМ ЖЕ правилом, что работает в
        # режиме «по слову»: _stack_layout даёт row/gend на каждое слово (серии с учётом
        # склеек joins и ручных разделителей brk). Своей копии разбора серий здесь нет —
        # иначе режимы разъехались бы. Серия — слова с ОДНИМ gend: он у всей серии общий
        # (конец последнего слова), у одиночного жёлтого — свой собственный.
        rows = gend = None
        stacked_indices = set()
        if hl_row_stack:
            rows, gend = _stack_layout(subs, hl, brk, joins)
            _run_words: dict[int, int] = {}
            for _k in hl:
                _run_words[gend[_k]] = _run_words.get(gend[_k], 0) + 1
            stacked_indices = {_k for _k in hl if _run_words[gend[_k]] >= 2}
            # Короткие жёлтые СТОПКИ: стопка играет тем же циклом, что режим
            # «по слову» (выезд на HL_RISE, проявление, блюр, общий конец), поэтому и
            # длительность считается так же — от появления слова до gend стопки. Цикл
            # СТРОК не трогаем: там момент появления уже зажат так, что анимация успевает.
            for _k in sorted(stacked_indices):
                _d = hl_appear_dur((gend[_k] - subs[_k][0]) / _fps0)
                if _d < _hl_dur:
                    _hl_hd[_k] = _d

        cut_bounds = set()
        for ci_cam in cams:
            for cl in ci_cam.get("clips", []):
                cut_bounds.add(int(cl[0]))
                cut_bounds.add(int(cl[1]))
        if stacked_indices:
            words_for_rows = [
                {"start": s, "end": e, "w": w, "idx": k}
                for k, (s, e, w) in enumerate(subs)
                if k not in stacked_indices
            ]
        else:
            words_for_rows = subs
        raw_lines = build_sub_rows(words_for_rows, per_row=sub_words_per_row, max_rows=sub_rows_max,
                                   cut_bounds=cut_bounds, word_timings=word_timings)
        max_line_w = 0.92 * width

        # Подбор единого кегля на весь ролик:
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
                ww = _fonts.text_width(ps, wd.get("w") or "", _w_fs(wd.get("idx") in hl))
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

        # Регистр субтитров: применяем к готовым словам — и план, и .jsx
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
        def _sub_w(w: str, idx: int) -> str:
            if sub_case == "sentence":
                return _accent_word(w, "title" if idx in repl_first else "lower")
            return _accent_word(w, sub_case)
        subs_plan = []
        hl_anim_mode = style.hl_row_anim
        for line in raw_lines:
            l_words = line["words"]
            r_s = line["start"] / _fps0
            r_e = line["end"] / _fps0
            t_words = []
            for x in l_words:
                is_hl = x["idx"] in hl
                w_s = x["start"] / _fps0
                w_e = x["end"] / _fps0
                tw = {
                    "w": _sub_w(x["w"], x["idx"]),
                    "color": "yellow" if is_hl else "white",
                    "s": w_s,
                    "e": w_e,
                }
                # Белое слово: появление по пресету стиля. Ключи кладёт layout, здесь —
                # только выбор: жёлтое пресет не трогает (у него своя анимация).
                if not is_hl and _anim is not None:
                    tw["anim"] = sub_anim_keys(_anim, w_s)
                # Подложка слова: в строке слово стоит с её начала, а звучит в свой
                # момент — берём время слова, зажатое в окно строки (та же формула,
                # что у появления жёлтого). Считает Python, .jsx берёт поле данных.
                if _wbg is not None:
                    tw["wbg"] = {"t": sub_wbg_moment(w_s, r_s, r_e)}
                if is_hl:
                    # Время появления жёлтого в строке (то же правило, что в
                    # цикле строк шаблона): при "word" — время слова, зажатое в окно строки
                    # (не раньше её начала и не позже, чем остаётся место на подъём), при
                    # "row" — начало строки. Считает Python: превью берёт готовое t0.
                    if hl_anim_mode == "word":
                        w_t0 = round(min(max(w_s, r_s), max(r_s, r_e - _hl_dur)), 4)
                    else:
                        w_t0 = round(r_s, 4)
                    tw["t0"] = w_t0
                    # Длительность появления: видимое время — до конца строки
                    # r_t1, формула — та же, что у MA (layout.hl_appear_dur). Короткому
                    # жёлтому общей HL_DUR не хватает: строка короче 0.35 с или момент зажат
                    # к её концу. Тогда своя длительность едет и в .jsx (поле 3 слова), и в
                    # план (hd — превью анимирует по нему), длинному — общая.
                    _d = hl_appear_dur(r_e - w_t0)
                    if _d < _hl_dur:
                        tw["hd"] = _d
                        _hl_row_hd[x["idx"]] = _d
                t_words.append(tw)
            all_hl = all(x["idx"] in hl for x in l_words)
            it = {
                "s": r_s,
                "e": r_e,
                "w": " ".join(x["w"] for x in t_words),
                "color": "yellow" if all_hl else "white",
                "row": line["row"],
                "gend": r_e,
                "repl": line["repl"],
                "words": t_words,
            }
            subs_plan.append(it)

        if stacked_indices:
            # Элементы стопки — ровно как элементы режима «по слову» (тот же состав полей и
            # те же row/gend из _stack_layout), только с пометкой stack: по ней превью
            # кладёт их отдельными строками по шагу HL_STEP, а не в строку текста.
            for k in sorted(stacked_indices):
                s, e, w = subs[k]
                wd = _sub_w(w, k)
                ps = hl_font_ps if k in hl else font_ps
                w_px = _fonts.text_width(ps, w, _w_fs(k in hl))
                item = {
                    "s": s / _fps0,
                    "e": _endc(k) / _fps0,
                    "w": wd,
                    "color": "yellow",
                    "row": cast(list[int], rows)[k],
                    "gend": cast(list[float], gend)[k] / _fps0,
                    "repl": k,
                    "stack": True,
                }
                if k in _hl_hd:
                    item["hd"] = _hl_hd[k]
                # Подложка слова: слово стопки — своя реплика, момент — его начало.
                if _wbg is not None:
                    item["wbg"] = {"t": sub_wbg_moment(s / _fps0)}
                if w_px is not None and w_px > max_line_w:
                    shrunk_fs = max(40, int(_fsize * max_line_w / w_px))
                    if shrunk_fs < _fsize:
                        item["fsize"] = shrunk_fs
                subs_plan.append(item)
            subs_plan.sort(key=lambda item: (item["s"], item.get("row", 0)))

        sub_stack_loop = ""
        if stacked_indices:
            any_joins = bool(joins)
            # Данные цикла стопки — как SUBS в режиме «по слову»: [начало, конец, слово,
            # hl=1, ряд стопки, общий конец]. Слова серии рисует цикл SUBS_LOOP_STACK:
            # выезд на HL_RISE, проявление и общий конец стопки — второй копии анимации нет.
            # У укороченного жёлтого в конец строки уезжает его длительность
            # появления: поле счётчика (6) в стопке пустое, длительность — поле 7.
            sub_stack_data = []
            for k in sorted(stacked_indices):
                _sw = [subs[k][0], _endc(k), _sub_w(subs[k][2], k), 1, cast(list[int], rows)[k], cast(list[float], gend)[k]]
                if _hl_hd:
                    _sw.append(None)
                    _sw.append(_hl_hd.get(k, _hl_dur))
                sub_stack_data.append(_sw)
            sub_stack_js = _jd(sub_stack_data)
            stack_tpl = SUBS_LOOP_STACK_JOINED if any_joins else SUBS_LOOP_STACK
            # В склейке второй проход цикла идёт по r_words, и строка данных там — `rsw`:
            # своё и имя переменной в вызове подложки, и время слова для градиента (kw).
            sub_stack_loop = stack_tpl % _hl_loop(
                "rsw" if any_joins else "sw",
                t_expr=("kw[0]/FPS" if any_joins else "sw[0]/FPS"),
                wbg_key=("call_hl_joined" if any_joins else "call_hl"),
                glow="true",
                sub_stack=sub_stack_js,
                hl_fsz=_hl_fsz_all)

        # В SUBS (её читает только поп-SFX по индексу начала) у слов серии — их ряд стопки и
        # общий конец; у остальных слов поля прежние. Галка выключена — stacked_indices пуст,
        # ветки не вычисляются, и SUBS побайтово прежний (golden).
        subs_js = _jd([[s, _endc(k), _sub_w(w, k), 1 if k in hl else 0,
                        cast(list[int], rows)[k] if k in stacked_indices else 0,
                        cast(list[float], gend)[k] if k in stacked_indices else _endc(k)]
                       for k, (s, e, w) in enumerate(subs)])

        def _row_word(x: dict[str, Any], mt: float | None) -> list[Any]:
            """Слово строки для цикла: [начало, текст, hl] и — у коротких строк — четвёртым полем длительность появления (у длинных жёлтых и белых —
            общая HL_DUR, как поле 7 у цикла слов). Пока коротких нет, полей ровно три:
            .jsx прежний байт в байт (golden).

            Подложка слова читает ПЯТОЕ поле (индекс 4): момент, с которого слово
            считается текущим. Поле всегда на своём месте — при коротких строках
            пропуск заполняется None, иначе индекс уехал бы и подложка прыгала бы
            не на то слово.
            """
            wd = [x["start"], _sub_w(x["w"], x["idx"]), 1 if x["idx"] in hl else 0]
            if _hl_row_hd:
                wd.append(_hl_row_hd.get(x["idx"], _hl_dur))
            if mt is not None:
                while len(wd) < 4:
                    wd.append(None)
                wd.append(mt)
            return wd

        sub_rows_data = [
            [
                line["start"],
                line["end"],
                line["row"],
                0,
                [_row_word(x, (sub_wbg_moment(x["start"] / _fps0, line["start"] / _fps0,
                                              line["end"] / _fps0)
                               if _wbg is not None else None))
                 for x in line["words"]]
            ]
            for line in raw_lines
        ]
        sub_rows_js = _jd(sub_rows_data)
        # Цикл строк: короткому жёлтому длительность даёт Python — подстановка
        # hl_dur_js берёт её из поля 3 слова (hlRowDur), остальным оставляет общую HL_DUR.
        # Блюр играет ту же длительность через HL_HD (сигнатура hlBlur(L, t0) — контракт
        # ZH); цикл стопки, что идёт следом, ставит HL_HD сам.
        rows_dur_js = "hlRowDur(r_words[wi])" if _hl_row_hd else "HL_DUR"
        if hl_blur_on and _hl_row_hd:
            rows_blur_call = " HL_HD = hlRowDur(r_words[wi]); hlBlur(L, t0);"
        elif hl_blur_on and _hl_hd:
            rows_blur_call = " HL_HD = HL_DUR; hlBlur(L, t0);"
        else:
            rows_blur_call = hl_blur_call
        # Цикл стопки дописывается ПОСЛЕ цикла строк (в AE слои стопки встают поверх строк),
        # а не подстановкой внутрь SUBS_LOOP_ROWS: шаблон строк остаётся отдельным блоком, и
        # его можно подставлять по-старому (tests/test_template_sub_wide.py).
        sub_loop = SUBS_LOOP_ROWS % dict(sub_rows=sub_rows_js, sub_step=_sub_step,
                                         hl_dur_js=rows_dur_js,
                                         hl_blur_call=rows_blur_call,
                                         base_anim=_anim_sub["call"],
                                         # Подложка слова в строке: вызов свой — момент
                                         # слова лежит в данных (поле 4), а не в t0 слоя.
                                         wbg_base="", wbg_hl="",
                                         wbg_row=_wbg_sub.get("call_row", ""),
                                         grad_call=sub_fill_call(style, "wd[0]/FPS"),
                                         glow_call=sub_glow_call(style, "w_hl"),
                                         hl_fsz=_hl_fsz_rows) + sub_stack_loop
    # Циклы субтитров собраны, укороченные жёлтые известны — теперь функции шаблона. Обе
    # пусты, пока в ролике нет ни одного такого слова: .jsx прежний побайтово (golden).
    if _hl_hd or _hl_row_hd:
        # Комментарий — под то, что реально объявлено: текст MA (ролики с укороченными
        # словами/стопкой остаются как были), а ролику без стопки — свой: упоминание
        # SUB_STACK в нём ловит сторож tests/test_rows_yellow (токен "SUB_STACK" в .jsx).
        _hl_comment = (
            "\n    // Короткое жёлтое слово: появление не успевало доиграть до"
            "\n    // outPoint — длительность кладёт Python полем 7 строки данных SUBS/SUB_STACK"
            "\n    // ([start,end,word,hl,row,gend,cnt,hd]), и только словам, кому общей HL_DUR"
            "\n    // не хватает." if _hl_hd else
            "\n    // Короткое жёлтое в строке: появление не успевало доиграть до"
            "\n    // outPoint строки — длительность кладёт Python полем 3 слова данных SUB_ROWS"
            "\n    // ([начало,текст,hl,hd]), и только тому, кому общей HL_DUR не хватает.")
        hl_short_fn = (
            _hl_comment
            # Сигнатура hlBlur(L, t0) — контракт (её стережёт test_hl_anim),
            # поэтому длительность блюра едет через HL_HD: цикл ставит переменную прямо
            # перед вызовом.
            + ("\n    // Блюр берёт её из HL_HD — переменную ставит цикл ПЕРЕД вызовом."
               "\n    var HL_HD = HL_DUR;" if hl_blur_on else "")
            + ("\n    function hlDur(sw){ return sw[7]; }" if _hl_hd else "")
            # Цикл строк зовёт hlRowDur(r_words[wi]) — иначе функция не нужна и её нет.
            + ("\n    function hlRowDur(wd){ return wd[3]; }" if _hl_row_hd else ""))
    if hl_blur_on:
        hl_blur_fn = (
            "\n    // Блюр появления жёлтого: Gaussian Blur HL_BLUR -> 0 на ТЕХ ЖЕ"
            "\n    // ключах, что подъём и проявление. Повтор краёв выключен — иначе размытие"
            "\n    // подтягивало бы в кадр края текстового слоя."
            "\n    function hlBlur(L, t0){"
            "\n        try{"
            "\n            var bl = L.property(\"ADBE Effect Parade\").addProperty(\"ADBE Gaussian Blur 2\");"
            "\n            bl.property(\"ADBE Gaussian Blur 2-0003\").setValue(0);   // Repeat Edge Pixels = 0"
            "\n            var bp = bl.property(\"ADBE Gaussian Blur 2-0001\");"
            "\n            bp.setValueAtTime(t0, HL_BLUR); bp.setValueAtTime(t0+%(blur_dur)s, 0);"
            "\n            easePair(bp);"
            "\n        }catch(e){ _LOG(\"блюр появления жёлтого: \" + e); }"
            "\n    }" % {"blur_dur": "HL_HD" if (_hl_hd or _hl_row_hd) else "HL_DUR"})

    return SubsPlan(
        subs=subs_plan, subs_js=subs_js, sub_loop=sub_loop,
        hl_row_decl=hl_row_decl, hl_blur_decl=hl_blur_decl, hl_blur_fn=hl_blur_fn,
        hl_short_fn=hl_short_fn, sub_anim_decl=_anim_sub["decl"],
        sub_anim_fn=_anim_sub["fn"], sub_anim_font=_anim_font,
        sub_anim_glitch=_anim_glitch,
        sub_wbg_js=_wbg_sub.get("js", ""), sub_wbg_tail=_wbg_sub.get("tail", ""),
        sub_wbg_plan=(sub_wbg_plan(_wbg) if _wbg is not None else None),
        sub_fill_fn=_grad_fn, sub_glow_fn=_glow_fn,
        hl_size_k=_hl_k, hl_blur_on=hl_blur_on, sub_scale=sub_scale,
        posy=_posy, hl_step=_hl_step, hl_rise=_hl_rise, hl_dur=_hl_dur,
        fsize=_fsize, fsize_base=_fsize_base, sub_step=_sub_step)
