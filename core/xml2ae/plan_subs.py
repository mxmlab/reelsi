# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Блок субтитров плана сцены (задание MR, этап 1 распила scene_plan).

`scene_plan` был одной функцией на 3253 строки с 24 вложенными функциями, делившими
состояние замыканиями; ревью назвало это главным долгом. Первый этап распила вынес сюда
ВЕСЬ блок субтитров: обрезку конца слова по соседу (`_endc`), кегль и геометрию полосы
(posy/шаг/подъём), стопку подряд жёлтых, появление жёлтых — в том числе коротких
(задания MA/MN), — данные циклов SUBS/SUB_ROWS/SUB_STACK и подстановки шаблона
(HL_ROW_WORD, HL_BLUR, hlDur/hlRowDur/hlBlur).

Перенос ПОСТРОЧНЫЙ: поведение, числа, порядок операций и текст подстановок не менялись
ни на байт (проверяется эталоном fixtures/golden_geometry.jsx и побайтовым сравнением
.jsx/плана). Имена локальных переменных оставлены как в scene_plan — поэтому тело
перенесено дословно, а входы распаковываются в преамбуле.

Вход — один неизменяемый `SubsInputs`, выход — один `SubsPlan` со всем, что `scene_plan`
читает дальше. Общие с другими блоками обёртки (`_sv`/`_sv_or` — дефолт ключа стиля из
styles.BASE) и правила (`_accent_word` — регистр слова, `_parse_intro_count` — разбор
числа-счётчика) остаются в `build.py` и приходят параметрами: второй копии нет.
"""
from dataclasses import dataclass
from typing import Callable

from .jsutil import _jd
from .layout import HL_DUR, _stack_layout, hl_appear_dur
from .template import (SUBS_LOOP_ROWS, SUBS_LOOP_STACK, SUBS_LOOP_STACK_JOINED,
                       SUBS_LOOP_WORDS, SUBS_LOOP_WORDS_JOINED)


@dataclass(frozen=True)
class SubsInputs:
    """Вход блока субтитров: всё, что `scene_plan` знает к моменту вызова.

    Поля названы как локальные переменные scene_plan, а `width`/`height` — это
    `meta["w"]`/`meta["h"]`: тело переноса читает те же имена. `sv`, `sv_or`,
    `accent_word`, `parse_count` — функции, которые живут в build.py и общие с другими
    блоками (свой стиль и своё правило регистра в модуль не заводятся).
    """
    # Слова транскрипта [(начало, конец, слово)] в кадрах и разметка по ним: жёлтые,
    # ручные разделители серий, слова со счётчиком, склейки в строку.
    subs: list
    hl: set
    brk: set
    cnt: set
    joins: set
    # Шрифты (PostScript-имена): базовый и жёлтых; регистр субтитров (задание CO).
    font_ps: str
    hl_font_ps: str
    sub_case: str
    # Режим субтитров: слов в строке и строк в реплике (задание CJ).
    sub_words_per_row: int
    sub_rows_max: int
    # Кадр (meta["w"]/meta["h"]) и частота: в кадрах считает XML, в секунды переводит план.
    width: int
    height: int
    fps: float
    # Клипы камер — только ради границ катов: строка субтитров не тянется через кат.
    cams: list
    # Пословные тайминги из сайдкара (None — их нет): ими уточняются строки.
    word_timings: object
    # Резолвнутый стиль и обёртки чтения его ключей.
    st: dict
    sv: Callable[[dict, str], object]
    sv_or: Callable[[dict, str], object]
    accent_word: Callable[[str, str], str]
    parse_count: Callable[..., object]


@dataclass(frozen=True)
class SubsPlan:
    """Выход блока субтитров: ровно те имена, что scene_plan читает дальше.

    Первые восемь полей уезжают в .jsx подстановками шаблона, остальные — геометрия
    полосы субтитров: её читают и план (предпросмотр), и шаблон, и окна интро (задание MH).
    """
    subs: list            # элементы субтитров для плана/превью (plan["subs"])
    subs_js: str          # данные SUBS — строки цикла слов
    sub_loop: str         # цикл субтитров: SUBS_LOOP_WORDS/SUB_ROWS (+ цикл стопки)
    hl_row_decl: str      # объявление HL_ROW_WORD (жёлтые в строке, задание ZH)
    hl_blur_decl: str     # объявление HL_BLUR (блюр появления жёлтых)
    hl_blur_fn: str       # функция hlBlur(L, t0) — сигнатура-контракт задания ZH
    hl_short_fn: str      # hlDur/hlRowDur и HL_HD для коротких жёлтых (задания MA/MN)
    hl_blur_on: bool      # галка блюра — уезжает в план (превью)
    sub_scale: float      # масштаб слоя прекомпа субтитров, % (задание FE)
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
    sub_case = inp.sub_case
    sub_words_per_row, sub_rows_max = inp.sub_words_per_row, inp.sub_rows_max
    width, height = inp.width, inp.height
    _fps0 = inp.fps
    cams, word_timings, st = inp.cams, inp.word_timings, inp.st
    # Общие с другими блоками обёртки и правила — по-прежнему в build.py, сюда приходят
    # параметрами: своей копии _sv/_sv_or/_accent_word/_parse_intro_count в модуле нет.
    _sv, _sv_or = inp.sv, inp.sv_or
    _accent_word = inp.accent_word
    _parse_intro_count = inp.parse_count
    # обрезаем конец слова по началу следующего, чтобы соседние (особ. мелкие «и/в») не накладывались
    def _endc(k):
        s, e, w = subs[k]
        ns = subs[k + 1][0] if k + 1 < len(subs) else None
        return min(e, ns) if (ns is not None and ns > s) else e

    _posy = int(height * float(_sv_or(st, "sub_y")))
    _hl_step = round(height * 0.06224, 2)
    _hl_rise = round(height * 0.06406, 2)
    # Длительность подъёма/проявления жёлтых, с (задания ZU/MA): ОДНО число на всю сборку —
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
    # Масштаб СЛОЯ прекомпа субтитров (задание FE), %: кегль/раскладка не трогаются,
    # 100 = как сегодня. При 100 подстановка в шаблон пуста — .jsx прежний (golden).
    sub_scale = float(_sv(st, "sub_scale"))
    # Жёлтые в режиме строк (задание ZH): HL_ROW_WORD нужен только циклу строк — в режиме
    # «по слову» объявления нет вовсе, и .jsx остаётся прежним байт в байт (golden).
    hl_row_decl = ("" if sub_words_per_row <= 1 else
                   ("\n    var HL_ROW_WORD = %s;   // жёлтые в строке (hl_row_anim): true — въезжает,"
                    " когда слово произнесено; false — вместе со строкой"
                    % ("true" if _sv(st, "hl_row_anim") == "word" else "false")))
    # Блюр появления жёлтых (задание ZH): выключен — ни объявления, ни функции, ни вызовов,
    # все три подстановки пусты и .jsx прежний байт в байт (golden). Сами hl_blur_fn и
    # hl_blur_call собираются НИЖЕ: у короткого жёлтого (задание MA) и блюр играет свою
    # длительность, а её до расчёта циклов ещё не знают.
    hl_blur_on = bool(_sv(st, "hl_blur"))
    hl_blur_call = " hlBlur(L, t0);" if hl_blur_on else ""      # цикл строк — как было
    hl_blur_decl = hl_blur_fn = ""
    if hl_blur_on:
        hl_blur_decl = ("\n    var HL_BLUR = %g;   // сила блюра появления жёлтых, px"
                        " (Gaussian Blur, повтор краёв выключен)" % float(_sv(st, "hl_blur_amt")))
    # Короткое жёлтое слово (задание MA): подъём, проявление и блюр играли общие HL_DUR =
    # 0.35 с, а слово с видимым временем меньше 0.35 с гасло (outPoint = gend) посреди
    # анимации — «просто исчезало». Длительность d = min(HL_DUR, HL_FIT * видимое время)
    # считает Python (layout.hl_appear_dur) для КАЖДОГО такого слова и кладёт её полем 7
    # строки данных SUBS/SUB_STACK ([.., gend, cnt, hd] — сразу за полем счётчика: поле 6
    # занято cnt_items, его не трогаем). Нет ни одного укороченного жёлтого — нет ни полей,
    # ни функции hlDur, ни новых подстановок: .jsx побайтово как на main (golden).
    _hl_hd = {}                     # индекс жёлтого слова -> своя длительность появления, с
    # Короткая СТРОКА (задание MN): цикл строк зажимал момент появления окном
    # (r_t1 - HL_DUR), но если строка короче HL_DUR, момент оставался в её начале, и подъём с
    # проявлением обрывались на конце строки. Длительность d = hl_appear_dur(r_t1 - w_t0) —
    # ТА ЖЕ функция, что у MA; видимое время считается до конца строки. Python кладёт её
    # полем 3 слова данных SUB_ROWS ([начало, текст, hl, hd]) и полем hd слова в плане
    # (превью берёт готовое). Нет ни одной такой строки — нет ни поля, ни функции hlRowDur,
    # ни подстановок: .jsx побайтово прежний (golden).
    _hl_row_hd = {}                 # индекс жёлтого слова СТРОКИ -> своя длительность, с
    hl_short_fn = ""

    def _hl_loop(elem, **kw):
        """Подстановки цикла субтитров для элемента `elem` (имя переменной строки данных):
        длительность появления в ключах подъёма/проявления и вызов блюра. Пока укороченных
        жёлтых нет — ровно прежний текст: HL_DUR и hlBlur(L, t0). У укороченного длительность
        едет в блюр через HL_HD (сигнатура hlBlur(L, t0) — контракт задания ZH)."""
        kw["hl_dur_js"] = ("hlDur(%s)" % elem) if _hl_hd else "HL_DUR"
        if hl_blur_on:
            if _hl_hd:
                kw["hl_blur_call"] = " HL_HD = hlDur(%s); hlBlur(L, t0);" % elem
            elif _hl_row_hd:
                # HL_HD завели короткие СТРОКИ (задание MN), а этому циклу укороченных не
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

    if sub_words_per_row <= 1:
        rows, gend = _stack_layout(subs, hl, brk, joins)
        max_line_w = 0.92 * width
        # Регистр субтитров (задание CO): применяем к ГОТОВОМУ тексту в scene_plan — .jsx
        # и превью читают преобразованное, второй копии правила нет. upper (дефолт) —
        # слова из XML уже капсом, upper() их не меняет, .jsx прежний (golden). В
        # покадровом режиме каждое слово — своя реплика, sentence = Заглавная на каждом.
        def _sub_w(w):
            return _accent_word(w, "title" if sub_case == "sentence" else sub_case)
        subs_plan = []
        cnt_items = []
        any_sub_count = False
        # Короткие жёлтые (задание MA): видимое время слова — от его появления до общего
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
            if k in _hl_hd:
                item["hd"] = _hl_hd[k]
            if w_px is not None and w_px > max_line_w:
                shrunk_fs = max(40, int(_fsize * max_line_w / w_px))
                if shrunk_fs < _fsize:
                    item["fsize"] = shrunk_fs
            subs_plan.append(item)

        def _sub_row(k, end, wd, hl_v):
            """Строка данных цикла слов: [начало, конец, слово, hl, ряд, gend] плюс поле
            счётчика (индекс 6) и — у укороченного жёлтого (задание MA) — поле длительности
            появления (индекс 7). Пока укороченных нет, полей ровно шесть: .jsx прежний."""
            r = [subs[k][0], end, wd, hl_v, rows[k], gend[k]]
            if any_sub_count:
                r.append(cnt_items[k])
            if _hl_hd:
                while len(r) < 7:
                    r.append(None)              # поле счётчика: счётчиков в ролике нет
                r.append(_hl_hd.get(k, _hl_dur))
            return r

        any_joins = bool(joins)
        sub_tpl = SUBS_LOOP_WORDS_JOINED if any_joins else SUBS_LOOP_WORDS
        subs_js = _jd([_sub_row(k, _endc(k), _sub_w(w), 1 if k in hl else 0)
                       for k, (s, e, w) in enumerate(subs)])
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
            sub_loop = sub_tpl % _hl_loop("sw", sub_count_code=sub_count_code)
        else:
            sub_loop = sub_tpl % _hl_loop("sw", sub_count_code="")
        sub_rows_js = "[]"
    else:
        hl_row_stack = bool(_sv(st, "hl_row_stack"))
        # Стопка подряд жёлтых (задание ZU) раскладывается ТЕМ ЖЕ правилом, что работает в
        # режиме «по слову»: _stack_layout даёт row/gend на каждое слово (серии с учётом
        # склеек joins и ручных разделителей brk). Своей копии разбора серий здесь нет —
        # иначе режимы разъехались бы. Серия — слова с ОДНИМ gend: он у всей серии общий
        # (конец последнего слова), у одиночного жёлтого — свой собственный.
        rows = gend = None
        stacked_indices = set()
        if hl_row_stack:
            rows, gend = _stack_layout(subs, hl, brk, joins)
            _run_words = {}
            for _k in hl:
                _run_words[gend[_k]] = _run_words.get(gend[_k], 0) + 1
            stacked_indices = {_k for _k in hl if _run_words[gend[_k]] >= 2}
            # Короткие жёлтые СТОПКИ (задание MA): стопка играет тем же циклом, что режим
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
        hl_anim_mode = _sv(st, "hl_row_anim")
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
                if is_hl:
                    # Время появления жёлтого в строке (задание ZH, то же правило, что в
                    # цикле строк шаблона): при "word" — время слова, зажатое в окно строки
                    # (не раньше её начала и не позже, чем остаётся место на подъём), при
                    # "row" — начало строки. Считает Python: превью берёт готовое t0.
                    if hl_anim_mode == "word":
                        w_t0 = round(min(max(w_s, r_s), max(r_s, r_e - _hl_dur)), 4)
                    else:
                        w_t0 = round(r_s, 4)
                    tw["t0"] = w_t0
                    # Длительность появления (задание MN): видимое время — до конца строки
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
                w_px = _fonts.text_width(ps, w, _fsize)
                item = {
                    "s": s / _fps0,
                    "e": _endc(k) / _fps0,
                    "w": wd,
                    "color": "yellow",
                    "row": rows[k],
                    "gend": gend[k] / _fps0,
                    "repl": k,
                    "stack": True,
                }
                if k in _hl_hd:
                    item["hd"] = _hl_hd[k]
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
            # У укороченного жёлтого (задание MA) в конец строки уезжает его длительность
            # появления: поле счётчика (6) в стопке пустое, длительность — поле 7.
            sub_stack_data = []
            for k in sorted(stacked_indices):
                _sw = [subs[k][0], _endc(k), _sub_w(subs[k][2], k), 1, rows[k], gend[k]]
                if _hl_hd:
                    _sw.append(None)
                    _sw.append(_hl_hd.get(k, _hl_dur))
                sub_stack_data.append(_sw)
            sub_stack_js = _jd(sub_stack_data)
            stack_tpl = SUBS_LOOP_STACK_JOINED if any_joins else SUBS_LOOP_STACK
            # В склейке второй проход цикла идёт по r_words, и строка данных там — `rsw`.
            sub_stack_loop = stack_tpl % _hl_loop("rsw" if any_joins else "sw",
                                                  sub_stack=sub_stack_js)

        # В SUBS (её читает только поп-SFX по индексу начала) у слов серии — их ряд стопки и
        # общий конец; у остальных слов поля прежние. Галка выключена — stacked_indices пуст,
        # ветки не вычисляются, и SUBS побайтово прежний (golden).
        subs_js = _jd([[s, _endc(k), _sub_w(w, k), 1 if k in hl else 0,
                        rows[k] if k in stacked_indices else 0,
                        gend[k] if k in stacked_indices else _endc(k)]
                       for k, (s, e, w) in enumerate(subs)])

        def _row_word(x):
            """Слово строки для цикла: [начало, текст, hl] и — у коротких строк (задание
            MN) — четвёртым полем длительность появления (у длинных жёлтых и белых —
            общая HL_DUR, как поле 7 у цикла слов). Пока коротких нет, полей ровно три:
            .jsx прежний байт в байт (golden)."""
            wd = [x["start"], _sub_w(x["w"], x["idx"]), 1 if x["idx"] in hl else 0]
            if _hl_row_hd:
                wd.append(_hl_row_hd.get(x["idx"], _hl_dur))
            return wd

        sub_rows_data = [
            [
                line["start"],
                line["end"],
                line["row"],
                0,
                [_row_word(x) for x in line["words"]]
            ]
            for line in raw_lines
        ]
        sub_rows_js = _jd(sub_rows_data)
        # Цикл строк (задание MN): короткому жёлтому длительность даёт Python — подстановка
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
                                         hl_blur_call=rows_blur_call) + sub_stack_loop
    # Циклы субтитров собраны, укороченные жёлтые известны — теперь функции шаблона. Обе
    # пусты, пока в ролике нет ни одного такого слова: .jsx прежний побайтово (golden).
    if _hl_hd or _hl_row_hd:
        # Комментарий — под то, что реально объявлено: текст MA (ролики с укороченными
        # словами/стопкой остаются как были), а ролику без стопки — свой: упоминание
        # SUB_STACK в нём ловит сторож tests/test_rows_yellow (токен "SUB_STACK" в .jsx).
        _hl_comment = (
            "\n    // Короткое жёлтое слово (задание MA): появление не успевало доиграть до"
            "\n    // outPoint — длительность кладёт Python полем 7 строки данных SUBS/SUB_STACK"
            "\n    // ([start,end,word,hl,row,gend,cnt,hd]), и только словам, кому общей HL_DUR"
            "\n    // не хватает." if _hl_hd else
            "\n    // Короткое жёлтое в строке (задание MN): появление не успевало доиграть до"
            "\n    // outPoint строки — длительность кладёт Python полем 3 слова данных SUB_ROWS"
            "\n    // ([начало,текст,hl,hd]), и только тому, кому общей HL_DUR не хватает.")
        hl_short_fn = (
            _hl_comment
            # Сигнатура hlBlur(L, t0) — контракт задания ZH (её стережёт test_hl_anim),
            # поэтому длительность блюра едет через HL_HD: цикл ставит переменную прямо
            # перед вызовом.
            + ("\n    // Блюр берёт её из HL_HD — переменную ставит цикл ПЕРЕД вызовом."
               "\n    var HL_HD = HL_DUR;" if hl_blur_on else "")
            + ("\n    function hlDur(sw){ return sw[7]; }" if _hl_hd else "")
            # Цикл строк зовёт hlRowDur(r_words[wi]) — иначе функция не нужна и её нет.
            + ("\n    function hlRowDur(wd){ return wd[3]; }" if _hl_row_hd else ""))
    if hl_blur_on:
        hl_blur_fn = (
            "\n    // Блюр появления жёлтого (задание ZH): Gaussian Blur HL_BLUR -> 0 на ТЕХ ЖЕ"
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
        hl_short_fn=hl_short_fn, hl_blur_on=hl_blur_on, sub_scale=sub_scale,
        posy=_posy, hl_step=_hl_step, hl_rise=_hl_rise, hl_dur=_hl_dur,
        fsize=_fsize, fsize_base=_fsize_base, sub_step=_sub_step)
