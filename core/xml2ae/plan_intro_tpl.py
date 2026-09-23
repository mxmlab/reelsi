# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Подстановки шаблона интро (этап 5 распила scene_plan).

`scene_plan` был одной функцией на 3253 строки, и её блоки выносились по этапам: субтитры
(`plan_subs.py`), расчёт интро (`plan_intro.py`, MS), вставки (`plan_inserts.py`,
MT), звук. Этап 5 выносит сюда ТЕКСТОВЫЕ ПОДСТАНОВКИ шаблона интро — готовые строки JS,
которые `scene_plan` кладёт в `plan["_ae"]`, а `template.py` подставляет в AE_FULL:

* цвета текста интро — HL_FILL3, INTRO_FILL/INTRO_HL_FILL, аргумент `cf` и выбор заливки
  строки (`introDoc`);
* тень слов и строк интро — пресет стиля либо автотень у глитча и строк заднего плана;
* раскладку строк — Y базовых линий (INTRO_LY), «большое слева» (INTRO_LX/LK), ветки шага
  и масштаба заднего плана, якорь «первая строка»;
* маршрутизацию слоёв интро — подъём над видеовставкой (front) и над рото по положению;
* эффекты появления — функцию `introAnimFX` (глитч, Deep Glow 2, Tritone, свечение строки),
  её вызовы на строке и на слове, свечение жёлтого хайлайта и свечение прекомпа;
* тень прекомпа интро — своя у камеры 1 и камеры 2.

Перенос ПОСТРОЧНЫЙ: поведение, числа, порядок операций и ТЕКСТ подстановок не менялись ни
на байт (проверяется эталоном fixtures/golden_geometry.jsx и побайтовым сравнением
.jsx/плана). Имена локальных переменных оставлены как в scene_plan — поэтому тело перенесено
дословно, а входы распаковываются в преамбуле.

Вход — неизменяемый `IntroTplInputs`, выход — `IntroTpl` со всем, что `scene_plan` читает
дальше (имена локальных переменных после вызова — прежние). Общее с другими блоками остаётся
в `build.py` и приходит параметрами: флаги строк (`_any_glitch`/`_any_back`/`_any_big` — их же
читают ассет и звук глитча), готовые массивы раскладки из `plan_intro`
(INTRO_LY/LX/LK, ON2, FRONT, ABOVE_ROTO, ANCHOR), таблицы `INTRO_ANIMS` и `DEEP_GLOW2_GLITCH`,
правило счётчика `_has_valid_count`. Цвета, тени, свечение и геометрия заднего плана —
структурой `StyleValues` одним полем `style` (её читает один раз
`plan_style.read_style`): второго чтения ключей стиля здесь нет.
"""
import json
from dataclasses import dataclass
from typing import Callable

from .jsutil import _fill_js, _jd
from .plan_intro import IntroPlan
from .plan_style import StyleValues


@dataclass(frozen=True)
class IntroTplInputs:
    """Вход подстановок: всё, что `scene_plan` знает к моменту вызова.

    Поля названы как локальные переменные scene_plan, а `intro` — результат `plan_intro`:
    из него берутся готовые массивы раскладки и признак сжатия появления.
    """
    # Строки групп интро: уже разбиты по splits и отсортированы по таймингу. По ним
    # считаются флаги «есть строка с таким-то anim/fx/цветом» — разбора строк здесь нет.
    groups: list
    # Результат plan_intro: INTRO_LY/LX/LK, ON2, FRONT, ABOVE_ROTO, ANCHOR и sq_used.
    intro: IntroPlan
    # Точка масштабирования прекомпа (intro_scale_anchor): режим на всю сборку и готовые
    # числа на группу — Y якоря слоя (px прекомпа) и компенсация Position по Y (px слоя).
    scale_anchor: str
    anchor_y: list
    anchor_dy: list
    # Флаги строк, посчитанные в build.py: их читает не только этот блок (ассет и звук
    # глитча) — второй копии правила нет.
    any_glitch: bool
    any_back: bool
    any_big: bool
    # Строки со своим цветом: accent объявляет HL_FILL3, custom — аргумент cf у introDoc.
    accent_color_used: bool
    custom_color_used: bool
    # Тёмный ли жёлтый: решает, ставить ли Tritone и Deep Glow.
    yellow_dark: bool
    dg_on: bool
    # Резолвнутый и прочитанный стиль (plan_style.read_style): цвета текста,
    # тень слов/строк и прекомпа, свечение (все четыре двери — intro_glitch_glow,
    # intro_fx_glow и заведённые заданием «glowfix» intro_hl_glow/intro_comp_glow),
    # галка Deep Glow и геометрия заднего плана.
    style: StyleValues
    # Таблицы, общие с другими блоками: анимации интро и настройки Deep Glow 2.
    anims: dict
    deep_glow: list
    # Правило счётчика из build.py: есть ли в строке валидное число-счётчик.
    has_valid_count: Callable[[dict], bool]


@dataclass(frozen=True)
class IntroTpl:
    """Выход: ровно те подстановки, что `scene_plan` кладёт в `plan["_ae"]`.

    Порядок полей — порядок сборки строк в блоке. Пустая строка = подстановки нет: при
    выключенных ключах и дефолтах .jsx остаётся прежним байт в байт (golden).
    """
    # Цвета текста интро: HL_FILL3, INTRO_FILL/INTRO_HL_FILL, аргумент cf и выбор заливки.
    hlfill3_decl: str
    fill_decl: str
    fill_params: str
    fill_call: str
    fill_pick: str
    # Тень слов и строк: пресет стиля либо автотень глитча и заднего плана.
    shadow_decl: str
    word_shadow_fn: str
    word_shadow_line: str
    word_shadow_word: str
    # Раскладка строк: Y базовых линий, «большое слева», ветки шага и масштаба.
    ly_decl: str
    lx_decl: str
    # Точка масштабирования прекомпа (intro_scale_anchor): объявление INTRO_ANCHOR_Y/
    # INTRO_ANCHOR_DY, добавка компенсации к Position и установка Anchor Point.
    anchor_decl: str
    anchor_dy_js: str
    anchor_set: str
    big_fn: str
    big_qi_vars: str
    big_line_pos: str
    big_word_x: str
    line_layout: str
    back_scale_fn: str
    back_scale_line: str
    back_scale_line_w: str
    back_scale_tmp: str
    back_scale_word: str
    back_scale_wpx: str
    # Маршрутизация слоёв интро: над видеовставкой (front) и над рото по положению.
    front_decl: str
    front_arr_decl: str
    front_route: str
    front_raise: str
    above_roto_decl: str
    above_roto_arr_decl: str
    above_roto_route: str
    above_roto_raise: str
    # Эффекты появления: функция introAnimFX и её вызовы на строке и на слове.
    anim_fx_fn: str
    line_anim: str
    word_anim: str
    # Свечение жёлтого хайлайта, флаги группы в прекомпе и свечение прекомпа.
    hl_glow_fn: str
    group_flags: str
    comp_glow: str
    # Тень прекомпа: готовая строка вызова и (при не-дефолте) её функция.
    comp_shadow: str
    comp_shadow_fn: str


def plan_intro_tpl(inp: IntroTplInputs) -> IntroTpl:
    """Собрать подстановки шаблона интро: чистая функция от `IntroTplInputs`.

    Ни стиль, ни план не читает и не пишет: все числа и флаги приходят полями — второго
    чтения ключей стиля и второго подсчёта флагов нет.
    """
    # ---- входы: имена ровно как в scene_plan (тело ниже перенесено дословно) ----
    _intro_groups = inp.groups
    _intro = inp.intro
    _intro_ly, _intro_lx, _intro_lk = _intro.ly, _intro.lx, _intro.lk
    _intro_front, _intro_above_roto = _intro.front, _intro.above_roto
    _intro_anchor = _intro.anchor
    _scale_anchor = inp.scale_anchor
    _anchor_y, _anchor_dy = inp.anchor_y, inp.anchor_dy
    _sq_used = _intro.sq_used
    _any_glitch, _any_back, _any_big = inp.any_glitch, inp.any_back, inp.any_big
    _accent_color_used, _custom_color_used = inp.accent_color_used, inp.custom_color_used
    # Стиль — структурой, прочитанной один раз: цвета, тени, свечение
    # (все четыре двери — intro_glitch_glow/intro_fx_glow и двери «glowfix»
    # intro_hl_glow/intro_comp_glow), галка Deep Glow и геометрия заднего плана;
    # имена локальных переменных прежние.
    style = inp.style
    intro_fill, intro_hl_fill, hl_fill3 = style.intro_fill, style.intro_hl_fill, style.hl_fill3
    _yellow_dark, _dg_on, _dg_with_glow = inp.yellow_dark, inp.dg_on, style.dg_with_glow
    intro_shadow_on = bool(style.intro_shadow)
    intro_shadow_op, intro_shadow_dir = style.intro_shadow_op, style.intro_shadow_dir
    intro_shadow_dist, intro_shadow_soft = style.intro_shadow_dist, style.intro_shadow_soft
    back_shadow_op, back_shadow_soft = style.back_shadow_op, style.back_shadow_soft
    intro_glitch_shadow, intro_back_shadow = style.intro_glitch_shadow, style.intro_back_shadow
    intro_glitch_glow, intro_fx_glow = style.intro_glitch_glow, style.intro_fx_glow
    # Третья и четвёртая двери свечения (задание «glowfix»): галки свечения жёлтого
    # хайлайта и слоя прекомпа — рядом с соседними ключами свечения, из той же структуры.
    intro_hl_glow, intro_comp_glow = style.intro_hl_glow, style.intro_comp_glow
    intro_word_glow_thr, intro_word_glow_rad = style.intro_word_glow_thr, style.intro_word_glow_rad
    intro_word_glow_int = style.intro_word_glow_int
    intro_comp_shadow_fill = style.intro_comp_shadow_fill
    intro_comp_shadow_op = style.intro_comp_shadow_op
    intro_comp_shadow2_fill = style.intro_comp_shadow2_fill
    intro_comp_shadow2_op = style.intro_comp_shadow2_op
    intro_comp_shadow_dir, intro_comp_shadow_dist = (style.intro_comp_shadow_dir,
                                                     style.intro_comp_shadow_dist)
    intro_comp_shadow_soft = style.intro_comp_shadow_soft
    back_step, back_scale = style.back_step, style.back_scale
    INTRO_ANIMS, DEEP_GLOW2_GLITCH = inp.anims, inp.deep_glow
    _has_valid_count = inp.has_valid_count

    # Цвета и тень текста интро (новые ключи стиля). Всё выключено при дефолтах — ниже
    # собираются подстановки шаблона так, чтобы при выключенных ключах .jsx не менялся
    # ни на байт (golden), тем же приёмом, что уже применён для accent_font.
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
    # Ставить ли тритон на жёлтую строку — решено выше (_yellow_dark): цвет мидтонов у него
    # тот же, что уезжает в подстановку _yellow_expr, и считается он по переменным сборки,
    # а не по строке JS. Яркий цвет — тритон выбеливает букву, обе
    # подстановки пустые.
    _fill_inner = _white_expr
    if _custom_color_used:
        _fill_inner = '(col=="custom"?(cf||[1,1,1]):%s)' % _fill_inner
    if _accent_color_used:
        _fill_inner = '(col=="accent"?HL_FILL3:%s)' % _fill_inner
    _intro_fill_pick = '(col=="yellow"?%s:%s)' % (_yellow_expr, _fill_inner)
    # Эффекты появления строк интро (anim: glitch/reveal/left/right/up/count, fx: glow).
    # Пока anim == "" и fx == "", подстановки пустые либо равны прежнему тексту (golden).
    _any_reveal = any(x.get("anim") == "reveal" for g in _intro_groups for x in g)
    _any_left = any(x.get("anim") == "left" for g in _intro_groups for x in g)
    _any_right = any(x.get("anim") == "right" for g in _intro_groups for x in g)
    _any_up = any(x.get("anim") == "up" for g in _intro_groups for x in g)
    _any_count = any(_has_valid_count(x) for g in _intro_groups for x in g)
    _any_fx_glow = any(x.get("fx") == "glow" for g in _intro_groups for x in g)
    _any_intro_yellow = any(x.get("color") == "yellow" for g in _intro_groups for x in g)
    # Автотень — только у глитча и строк заднего плана, и только если её разрешает галка
    # этого вида строк. Свечение (fx=="glow") её по-прежнему НЕ приносит:
    # у строки со свечением на слое слова остаются ровно Glo2 и (для жёлтой) тритон,
    # иначе к свечению подмешивалась тень, которой пользователь не просил.
    _auto_glitch = _any_glitch and intro_glitch_shadow
    _auto_back = _any_back and intro_back_shadow
    _any_auto_shadow = _auto_glitch or _auto_back
    _shadow_needed = intro_shadow_on or _any_auto_shadow
    _anim_fx_used = (
        _any_glitch or _any_reveal or _any_fx_glow
        or _any_left or _any_right or _any_up
        or _any_count
    )

    # Тень (Drop Shadow) на КАЖДОМ слове/строке интро — пресет intro_shadow, либо
    # автоматически для строк с anim=="glitch" (галка intro_glitch_shadow) и back==True
    # (intro_back_shadow); fx=="glow" — без тени. Слово, которое и глитч, и строка заднего
    # плана, получает тень, если разрешена ХОТЬ ОДНА из двух галочек: иначе строка заднего
    # плана с глитчем теряла бы тень неожиданно. Обе галки на дефолте (True) дают ровно
    # прежнее условие `ln.anim=="glitch"||ln.back` — .jsx байт в байт как раньше (golden).
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
        # Условие собирается из ГАЛОК, а не из факта наличия таких строк в сборке: при обеих
        # включённых (дефолт) оно выходит ровно прежним `ln.anim=="glitch"||ln.back` в любом
        # ролике — .jsx байт в байт как раньше. Снятая галка убирает из условия свою ветку.
        _auto_shadow_cond = "||".join(
            (['ln.anim=="glitch"'] if intro_glitch_shadow else [])
            + (["ln.back"] if intro_back_shadow else []))
        _intro_word_shadow_line = ' if(%s) introWordShadow(Ll, ln.back);' % _auto_shadow_cond
        _intro_word_shadow_word = ' if(%s) introWordShadow(L2, ln.back);' % _auto_shadow_cond
    else:
        _intro_word_shadow_line = ""
        _intro_word_shadow_word = ""

    # Раскладка строк интро по вертикали: готовые Y базовых линий уезжают
    # в .jsx массивом INTRO_LY и берутся оттуда — шаг знает back_step и якорь блока,
    # в шаблоне этого не сосчитать. Массив нужен, если в ролике есть строки
    # заднего плана (там шаг уже не LINE_STEP) ИЛИ хоть одна группа с якорем «first»
    # (первая строка на месте), ИЛИ группа с большой строкой (Y большой
    # строки считает раскладка). Ничего из этого — .jsx прежний байт в байт (golden).
    _any_first = any(a == "first" for a in _intro_anchor)
    _intro_ly_decl = (
        "    var INTRO_LY=%s;    // [группа][строка] — Y базовой линии строки в прекомпе,"
        " считает Python: шаг знает back_step и якорь блока\n"
        % _jd(_intro_ly)
    ) if (_any_back or _any_first or _any_big) else ""
    # Большая строка: левый край каждой строки (px прекомпа от центра) и
    # множитель её кегля — массивами INTRO_LX/INTRO_LK, как INTRO_LY. У строк обычных
    # групп там null. Нет большой строки — объявления нет вовсе, .jsx прежний (golden).
    _intro_lx_decl = (
        "    var INTRO_LX=%s, INTRO_LK=%s;    // [группа][строка] — левый край строки"
        " (px прекомпа от центра) и множитель её кегля: строка с галкой «большое слева»"
        " встаёт слева крупно, остальные строки — стопкой справа, считает Python\n"
        % (_jd(_intro_lx), _jd(_intro_lk))
    ) if _any_big else ""

    # Точка масштабирования прекомпа интро (intro_scale_anchor): готовые числа на группу —
    # Y якоря слоя в прекомпе и компенсация Position по Y. Шаблон только применяет: Anchor
    # Point ставится в [IW/2, INTRO_ANCHOR_Y[gI]], к Position добавляется
    # INTRO_ANCHOR_DY[gI]. Всё это нужно, ТОЛЬКО когда режим не дефолтный: при "comp" (центр
    # композиции прекомпа) ни объявления, ни установки якоря, ни добавки в .jsx нет — файл
    # прежний байт в байт (golden), тем же приёмом собраны соседние подстановки.
    _any_scale_anchor = _scale_anchor != "comp"
    _intro_anchor_decl = (
        "    var INTRO_ANCHOR_Y=%s, INTRO_ANCHOR_DY=%s;    // [группа] — Y якоря слоя"
        " прекомпа и добавка к его Position по Y: точка масштабирования знает Y строк"
        " блока, считает Python (intro_scale_anchor)\n"
        % (_jd(_anchor_y), _jd(_anchor_dy))
    ) if _any_scale_anchor else ""
    _intro_anchor_dy_js = "+INTRO_ANCHOR_DY[gI]" if _any_scale_anchor else ""
    _intro_anchor_set = (
        "\n            // точка масштабирования прекомпа (intro_scale_anchor): якорь слоя —"
        " на текст, Position уже компенсирован (INTRO_ANCHOR_DY), поэтому картинка стоит"
        " на месте, а уменьшение идёт ОТ ТЕКСТА, а не от середины кадра\n"
        "            iL.property(\"ADBE Transform Group\").property(\"ADBE Anchor Point\")"
        ".setValue([IW/2, INTRO_ANCHOR_Y[gI]]);"
    ) if _any_scale_anchor else ""
    # Кусок «большая строка» для шаблона: функции чтения INTRO_LX/INTRO_LK,
    # скейл слоя большой строки и её ширина. Все подстановки непустые ТОЛЬКО когда в
    # ролике есть большая строка — без неё .jsx прежний байт в байт (golden). Большая
    # строка задний-план-скейл НЕ получает: lk его заменяет, поэтому условия back-скейла
    # и back-ширины дополнены проверкой «эта строка не большая» (_big_no).
    _big_no = " && introBigK(gI,qi)==null" if _any_big else ""
    _intro_big_fn = ""
    _intro_big_qi_vars = ""
    _intro_big_line = ""
    _intro_big_line_pos = ""
    _intro_big_word_x = ""
    if _any_big:
        _intro_big_fn = (
            '\n        function introBigK(gI,qi){ try{ var a=INTRO_LK[gI];'
            ' return (a&&a[qi]!=null)?a[qi]:null; }catch(e){ return null; } }'
            '\n        function introBigX(gI,qi){ try{ var a=INTRO_LX[gI];'
            ' return (a&&a[qi]!=null)?a[qi]:null; }catch(e){ return null; } }'
            '\n        function introBigScale(L,k){ try{ '
            'L.property("ADBE Transform Group").property("ADBE Scale")'
            '.setValue([Math.round(k*1000)/10, Math.round(k*1000)/10, 100]); '
            '}catch(e){} }'
        )
        # bigK/bigX — на строку, рядом с ln/wds/tms: дальше их читают и слова, и позиция
        _intro_big_qi_vars = " var bigK=introBigK(gI,qi), bigX=introBigX(gI,qi);"
        # построчно: текст выключен по центру, поэтому левый край = x − lineW/2
        _intro_big_line = " if(bigK!=null) introBigScale(Ll,bigK);"
        _intro_big_line_pos = (
            " if(bigX!=null){ Ll.property(\"ADBE Transform Group\").property(\"ADBE Position\")"
            ".setValue([IW/2+bigX+lineW/2, lineY]); }   // большое слева: левый край строки"
            " на IW/2+lx"
        )
        # пословно: старт строки — левый край блока, а не центр минус половина ширины
        _intro_big_word_x = " if(bigX!=null) x=IW/2+bigX;"

    if _any_back:
        _intro_line_layout = (
            "var nL=GRP.length, BACK_STEP=%g, BACK_SCALE=%g, maxLineW=0;\n"
            "            var lineSteps=[0], totH=0;\n"
            "            for(var si=1; si<nL; si++){\n"
            "                var stp = LINE_STEP * ((GRP[si].back || GRP[si-1].back) ? BACK_STEP : 1.0);\n"
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
        _intro_back_scale_line = (" if(ln.back%s) introBackScale(Ll);%s"
                                  % (_big_no, _intro_big_line))
        _intro_back_scale_line_w = (
            "var lineW=introW(Ll); if(ln.back%s) lineW*=BACK_SCALE;"
            " if(bigK!=null) lineW*=bigK; if(lineW>maxLineW) maxLineW=lineW;"
            % _big_no if _any_big else
            "var lineW=introW(Ll); if(ln.back) lineW*=BACK_SCALE; if(lineW>maxLineW) maxLineW=lineW;"
        )
        _intro_back_scale_tmp = (" if(ln.back%s) lineW*=BACK_SCALE; if(bigK!=null) lineW*=bigK;"
                                 % _big_no if _any_big else
                                 " if(ln.back) lineW*=BACK_SCALE;")
        _intro_back_scale_word = (" if(ln.back%s) introBackScale(L2);"
                                  " if(bigK!=null) introBigScale(L2,bigK);" % _big_no if _any_big else
                                  " if(ln.back) introBackScale(L2);")
        _intro_back_scale_wpx = (" if(ln.back%s) wpx*=BACK_SCALE; if(bigK!=null) wpx*=bigK;"
                                 % _big_no if _any_big else
                                 " if(ln.back) wpx*=BACK_SCALE;")
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
        _intro_back_scale_line = _intro_big_line
        _intro_back_scale_line_w = (
            "var lineW=introW(Ll); if(bigK!=null) lineW*=bigK; if(lineW>maxLineW) maxLineW=lineW;"
            if _any_big else "if(introW(Ll)>maxLineW) maxLineW=introW(Ll);")
        _intro_back_scale_tmp = (" if(bigK!=null) lineW*=bigK;" if _any_big else "")
        _intro_back_scale_word = (" if(bigK!=null) introBigScale(L2,bigK);" if _any_big else "")
        _intro_back_scale_wpx = (" if(bigK!=null) wpx*=bigK;" if _any_big else "")
    else:
        _intro_line_layout = (
            "var nL=GRP.length, cY=H/2 - (nL-1)/2*LINE_STEP, maxLineW=0;\n"
            "            for (var qi=0; qi<nL; qi++){\n"
            "                var ln=GRP[qi], wds=ln.words||[], tms=ln.times||[], lineY=cY+qi*LINE_STEP;"
            # Большая строка: INTRO_LY при ней объявлен всегда — Y строк
            # считает раскладка, из формулы cY+qi*LINE_STEP его не получить.
            + ("\n                if(INTRO_LY[gI]&&INTRO_LY[gI][qi]!=null) lineY=INTRO_LY[gI][qi];"
               if _any_big else "")
        )
        _intro_back_scale_fn = ""
        _intro_back_scale_line = _intro_big_line
        _intro_back_scale_line_w = (
            "var lineW=introW(Ll); if(bigK!=null) lineW*=bigK; if(lineW>maxLineW) maxLineW=lineW;"
            if _any_big else "if(introW(Ll)>maxLineW) maxLineW=introW(Ll);")
        _intro_back_scale_tmp = (" if(bigK!=null) lineW*=bigK;" if _any_big else "")
        _intro_back_scale_word = (" if(bigK!=null) introBigScale(L2,bigK);" if _any_big else "")
        _intro_back_scale_wpx = (" if(bigK!=null) wpx*=bigK;" if _any_big else "")

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

    # Подъём интро над РОТО по положению: группа Камеры 1, чей блок в нижней
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
        "    // Интро над рото по положению: каждый слой из introAboveRoto\n"
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
        # Сжатие появления: сжатые слова есть — все ключи анимации играют
        # от t0 с множителем SQ (его даёт introSQ на слово), и в вызовы добавляется
        # аргумент. Сжатых нет — ни множителя, ни аргумента: текст .jsx прежний (golden).
        _sq = "*SQ" if _sq_used else ""
        _sq_arg = ", SQ" if _sq_used else ""
        _sq_guard = ("            if(SQ==null || !(SQ>0)) SQ=1;   // слово успевает — множитель 1\n"
                     if _sq_used else "")
        _gl_op_lines = []
        for _kt, _kv in _g_an["op_keys"]:
            _t_str = "t0" if _kt == 0 else f"t0+{_kt:g}{_sq}"
            _gl_op_lines.append(f'                op.setValueAtTime({_t_str},{_kv:g});\n')
        _gl_op_jsx = "".join(_gl_op_lines)
        _sc_pct = int(round(_r_an["scale"] * 100))
        _sc3d_str = ",".join(f"{x:g}" if x == int(x) else str(x) for x in _r_an["scale_3d"])
        # Тритон на СЛОВЕ: у жёлтой строки со свечением/глитчем Midtones
        # красится в цвет заливки жёлтой строки — то же выражение, что _yellow_expr
        # (INTRO_HL_FILL, если он задан в стиле, иначе HL_FILL). Highlights/Shadows/
        # смешивание — дефолтные. Ставится ПОСЛЕ Glo2; строк без глитча и свечения,
        # как и белый/accent/custom цвет, он не касается. Нет таких строк в сборке —
        # подстановка пустая, .jsx прежний. Яркий цвет — тоже пустая:
        # свечение выбеливает букву, и тритон гонит её в Highlights вместо мидтонов.
        _tt_yellow = ""
        if (_any_glitch or _any_fx_glow) and _yellow_dark:
            _tt_yellow = (
                '            if((anim=="glitch"||fx=="glow") && col=="yellow"){\n'
                '                var tt=addFX(L,"ADBE Tritone"); setP(tt,"ADBE Tritone-0002",%s);\n'
                '            }\n' % _yellow_expr
            )
        # Числа Glo2 на словах: три ключа стиля, ОДНИ И ТЕ ЖЕ в обеих ветках
        # (глитч и fx=="glow"). %g печатает дефолты 149/77/0.62 ровно теми же литералами,
        # что стояли в шаблоне: при дефолтах .jsx байт в байт прежний (golden).
        _glow_set = ('var fxGl=addFX(L,"ADBE Glo2"); setP(fxGl,"ADBE Glo2-0002",%g);'
                     ' setP(fxGl,"ADBE Glo2-0003",%g); setP(fxGl,"ADBE Glo2-0004",%g);'
                     % (intro_word_glow_thr, intro_word_glow_rad, intro_word_glow_int))
        # Галка выключена — эффекта нет ВОВСЕ (а не «есть, но выключен»): строки Glo2 в
        # ветке не остаётся, а пустая ветка fx=="glow" в шаблон не едет — там ей нечего
        # делать. Gaussian Blur анимации глитча к свечению отношения не имеет и остаётся.
        _glow_glitch_inner = f'                    {_glow_set}\n' if intro_glitch_glow else ''
        _glow_glitch_line = f'                {_glow_set}\n' if intro_glitch_glow else ''
        _fx_glow_branch = (' else if(fx=="glow"){\n'
                           f'                {_glow_set}\n'
                           '            }') if intro_fx_glow else ''
        if _dg_on:
            _dg_set_lines = ['var fxDg=addFX(L,"PEDG2"); if(!fxDg) DG_MISS++;']
            for _mn, _val in DEEP_GLOW2_GLITCH:
                _dg_set_lines.append(f'setP(fxDg,"{_mn}",{json.dumps(_val)});')
            _dg_set_str = " ".join(_dg_set_lines)
            # Строка со свечением (fx=="glow") Deep Glow не берёт: вместо него
            # ей ставится ровно то же, что жёлтому глитчу в режиме «Встроенные» — ветка
            # else ниже (Gaussian Blur + Glo2). Условие в .jsx нужно и тогда, когда
            # подходящие слова в сборке есть не только такие: решение по КАЖДОЙ строке
            # принимает шаблон, как и раньше по col. Галка intro_dg_with_glow возвращает
            # прежнее условие байт в байт — .jsx у неё как до задания. Яркий цвет (задание
            # MK2/MK3) сюда не доходит вовсе: _dg_on уже выключен (_dg_bright), ветка else.
            _dg_yellow_cond = ('col=="yellow"' if _dg_with_glow
                               else 'col=="yellow" && fx!="glow"')
            _glitch_fx_code = (
                '            if(anim=="glitch"){\n'
                f'                if({_dg_yellow_cond}){{ {_dg_set_str} }} else {{\n'
                f'                    var fxGb=addFX(L,"ADBE Gaussian Blur 2"); setP(fxGb,"ADBE Gaussian Blur 2-0001",{_g_an["blur"]:g}); setP(fxGb,"ADBE Gaussian Blur 2-0003",0);\n'  # -0003: Repeat Edge Pixels, в AE включён по умолчанию и портит края текста
                + _glow_glitch_inner
                + '                }\n'
                '            }' + _fx_glow_branch + '\n'
            )
            _dg_miss_decl = 'var DG_MISS=0;\n        '
        else:
            _glitch_fx_code = (
                '            if(anim=="glitch"){\n'
                f'                var fxGb=addFX(L,"ADBE Gaussian Blur 2"); setP(fxGb,"ADBE Gaussian Blur 2-0001",{_g_an["blur"]:g}); setP(fxGb,"ADBE Gaussian Blur 2-0003",0);\n'  # -0003: Repeat Edge Pixels, в AE включён по умолчанию и портит края текста
                + _glow_glitch_line
                + '            }' + _fx_glow_branch + '\n'
            )
            _dg_miss_decl = ''
        # Масштаб появления — ОТ БАЗЫ слоя, а не в абсолютных 70→100.
        # База — Scale, выставленный ДО анимации: у большой строки introBigScale (lk*100),
        # у заднего плана introBackScale (BACK_SCALE*100), у обычной 100. Абсолютные ключи
        # перебивали статичное значение, и большое слово сжималось до 100 % (в превью
        # крупное, в AE обычного размера). Ветка только при _any_big: без большой строки
        # .jsx прежний байт в байт, как у остальных подстановок ZY (числа там те же —
        # обычная строка 100→70, задний план BACK_SCALE*100→×0.7, ключи от базы).
        _rev_scale_jsx = (
            '                    var sc=L.property("ADBE Transform Group").property("ADBE Scale");\n'
            '                    var base=(sc && sc.value && !isNaN(sc.value[0])) ? sc.value[0]\n'
            '                        : ((typeof isBack!=="undefined" && isBack'
            ' && typeof BACK_SCALE!=="undefined") ? BACK_SCALE*100 : 100);\n'
            f'                    var s1=Math.round(base*10)/10, s0=Math.round(base*{_r_an["scale"]:g}*10)/10;\n'
            f'                    sc.setValueAtTime(t0,[s0,s0]); sc.setValueAtTime(t0+F_DUR{_sq},[s1,s1]);\n'
        ) if _any_big else (
            '                    var sc=L.property("ADBE Transform Group").property("ADBE Scale");\n'
            '                    var isB=(typeof isBack!=="undefined"&&isBack)||(typeof BACK_SCALE!=="undefined"&&sc.value[0]<99);\n'
            '                    if(isB){\n'
            '                        var bSc=(typeof BACK_SCALE!=="undefined")?BACK_SCALE:0.69;\n'
            '                        var s1=Math.round(bSc*1000)/10;\n'
            f'                        var s0=Math.round(s1*{_r_an["scale"]:g}*10)/10;\n'
            f'                        sc.setValueAtTime(t0,[s0,s0]); sc.setValueAtTime(t0+F_DUR{_sq},[s1,s1]);\n'
            '                    }else{\n'
            f'                        sc.setValueAtTime(t0,[{_sc_pct},{_sc_pct}]); sc.setValueAtTime(t0+F_DUR{_sq},[100,100]);\n'
            '                    }\n'
        )
        _intro_anim_fx_fn = (
            '\n        ' + _dg_miss_decl
            + 'function introAnimFX(L, t0, anim, fx, w, target, expr, isBack, col%s){\n' % _sq_arg
            + _sq_guard
            + '            var hasCnt=(typeof target!=="undefined" && target!==null && !isNaN(target));\n'
            '            if(hasCnt){\n'
            '                try{\n'
            '                    var sl=addFX(L,"ADBE Slider Control");\n'
            '                    if(sl){\n'
            '                        var slP=sl.property("ADBE Slider Control-0001");\n'
            '                        if(slP){\n'
            '                            slP.setValueAtTime(t0,0);\n'
            f'                            slP.setValueAtTime(t0+HL_DUR{_sq},target);\n'
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
            + _glitch_fx_code
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
            f'                        pStart.setValueAtTime(t0,0); pStart.setValueAtTime(t0+{_g_an["dur"]:g}{_sq},100);\n'
            '                    }catch(e){}\n'
            '                    try{\n'
            '                        var pEnd=sel.property("ADBE Text Percent End");\n'
            f'                        pEnd.setValueAtTime(t0+{_g_an["end_keys"][0][0]:g}{_sq},{_g_an["end_keys"][0][1]:g}); pEnd.setValueAtTime(t0+{_g_an["end_keys"][1][0]:g}{_sq},{_g_an["end_keys"][1][1]:g}); pEnd.setValueAtTime(t0+{_g_an["end_keys"][2][0]:g}{_sq},{_g_an["end_keys"][2][1]:g});\n'
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
            f'                        pOff.setValueAtTime(t0,-100); pOff.setValueAtTime(t0+F_DUR{_sq},100);\n'
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
            '                    setP(gb,"ADBE Gaussian Blur 2-0003",0);\n'   # Repeat Edge Pixels: в AE включён по умолчанию и портит края текста
            '                    if(gb){\n'
            '                        var pBl=gb.property("ADBE Gaussian Blur 2-0001");\n'
            f'                        pBl.setValueAtTime(t0,{_r_an["blur"]:g}); pBl.setValueAtTime(t0+F_DUR{_sq},0);\n'
            '                    }\n'
            '                }catch(e){}\n'
            '                try{\n'
            + _rev_scale_jsx +
            '                }catch(e){}\n'
            '                var op=L.property("ADBE Transform Group").property("ADBE Opacity");\n'
            f'                op.setValueAtTime(t0,0); op.setValueAtTime(t0+F_DUR{_sq},100); easePair(op);\n'
            '            }else if(anim=="left"){\n'
            '                if(typeof w==="undefined" || w===null) w=introW(L);\n'
            '                var pos=L.property("ADBE Transform Group").property("ADBE Position");\n'
            '                var curP=pos.value, curX=curP[0], curY=curP[1];\n'
            '                pos.setValueAtTime(t0, [curX-w, curY]);\n'
            f'                pos.setValueAtTime(t0+F_DUR{_sq}, [curX, curY]);\n'
            '                easePair(pos);\n'
            '                var op=L.property("ADBE Transform Group").property("ADBE Opacity");\n'
            f'                op.setValueAtTime(t0,0); op.setValueAtTime(t0+F_DUR{_sq},100); easePair(op);\n'
            '            }else if(anim=="right"){\n'
            '                if(typeof w==="undefined" || w===null) w=introW(L);\n'
            '                var pos=L.property("ADBE Transform Group").property("ADBE Position");\n'
            '                var curP=pos.value, curX=curP[0], curY=curP[1];\n'
            '                pos.setValueAtTime(t0, [curX+w, curY]);\n'
            f'                pos.setValueAtTime(t0+F_DUR{_sq}, [curX, curY]);\n'
            '                easePair(pos);\n'
            '                var op=L.property("ADBE Transform Group").property("ADBE Opacity");\n'
            f'                op.setValueAtTime(t0,0); op.setValueAtTime(t0+F_DUR{_sq},100); easePair(op);\n'
            '            }else if(anim=="up"){\n'
            '                var pos=L.property("ADBE Transform Group").property("ADBE Position");\n'
            '                var curP=pos.value, curX=curP[0], curY=curP[1];\n'
            '                pos.setValueAtTime(t0, [curX, curY+HL_RISE]);\n'
            f'                pos.setValueAtTime(t0+F_DUR{_sq}, [curX, curY]);\n'
            '                easePair(pos);\n'
            '                var op=L.property("ADBE Transform Group").property("ADBE Opacity");\n'
            f'                op.setValueAtTime(t0,0); op.setValueAtTime(t0+F_DUR{_sq},100); easePair(op);\n'
            '            }else if(hasCnt){\n'
            '                var op=L.property("ADBE Transform Group").property("ADBE Opacity");\n'
            f'                op.setValueAtTime(t0,0); op.setValueAtTime(t0+HL_DUR{_sq},100); easePair(op);\n'
            '            }else{\n'
            '                var op=L.property("ADBE Transform Group").property("ADBE Opacity");\n'
            f'                op.setValueAtTime(t0,0); op.setValueAtTime(t0+F_DUR{_sq},100); easePair(op);\n'
            '            }\n'
            '        }'
        )
        # Коэффициент сжатия едет в вызов аргументом: слово — своё число из
        # INTRO_SQ, строка построчного режима — нулевое (её анимацию играет слой строки
        # от первого слова). Сжатых слов нет — аргумента нет, .jsx прежний (golden).
        _sq_li = ", introSQ(gI,qi,0)" if _sq_used else ""
        _sq_wi = ", introSQ(gI,qi,wj2)" if _sq_used else ""
        if _any_count:
            if _any_back:
                _intro_line_anim = ("introAnimFX(Ll, t0l, ln.anim, ln.fx, null, ln.cnt, ln.expr,"
                                    " ln.back, ln.color%s);" % _sq_li)
                _intro_word_anim = (
                    'var tw=(tms[wj2]!=null?tms[wj2]:0); if(tw<0)tw=0; '
                    'var cw=null; if(ln.cnts){for(var ci=0;ci<ln.cnts.length;ci++){if(ln.cnts[ci][0]===wj2){cw=ln.cnts[ci];break;}}}\n'
                    '                    introAnimFX(wl[wj2], tw, ln.anim, ln.fx, ww[wj2], cw?cw[1]:null, cw?cw[2]:null, ln.back, ln.color%s);' % _sq_wi
                )
            else:
                _intro_line_anim = ("introAnimFX(Ll, t0l, ln.anim, ln.fx, null, ln.cnt, ln.expr,"
                                    " null, ln.color%s);" % _sq_li)
                _intro_word_anim = (
                    'var tw=(tms[wj2]!=null?tms[wj2]:0); if(tw<0)tw=0; '
                    'var cw=null; if(ln.cnts){for(var ci=0;ci<ln.cnts.length;ci++){if(ln.cnts[ci][0]===wj2){cw=ln.cnts[ci];break;}}}\n'
                    '                    introAnimFX(wl[wj2], tw, ln.anim, ln.fx, ww[wj2], cw?cw[1]:null, cw?cw[2]:null, null, ln.color%s);' % _sq_wi
                )
        else:
            if _any_back:
                _intro_line_anim = ("introAnimFX(Ll, t0l, ln.anim, ln.fx, null, null, null,"
                                    " ln.back, ln.color%s);" % _sq_li)
                _intro_word_anim = (
                    'var tw=(tms[wj2]!=null?tms[wj2]:0); if(tw<0)tw=0;\n'
                    '                    introAnimFX(wl[wj2], tw, ln.anim, ln.fx, ww[wj2], null, null, ln.back, ln.color%s);' % _sq_wi
                )
            else:
                _intro_line_anim = ("introAnimFX(Ll, t0l, ln.anim, ln.fx, null, null, null,"
                                    " null, ln.color%s);" % _sq_li)
                _intro_word_anim = (
                    'var tw=(tms[wj2]!=null?tms[wj2]:0); if(tw<0)tw=0;\n'
                    '                    introAnimFX(wl[wj2], tw, ln.anim, ln.fx, ww[wj2], null, null, null, ln.color%s);' % _sq_wi
                )
    else:
        # Ветка вовсе без эффектов (обычный фейд): сжатым словам длительность
        # укорочена тем же множителем — своим у слова и нулевым у строки построчного режима.
        _intro_anim_fx_fn = ""
        _sq_li_p = "*introSQ(gI,qi,0)" if _sq_used else ""
        _sq_wi_v = ("var sq=introSQ(gI,qi,wj2); " if _sq_used else "")
        _sq_wi_p = "*sq" if _sq_used else ""
        _intro_line_anim = (
            'var opL=Ll.property("ADBE Transform Group").property("ADBE Opacity");\n'
            '                    opL.setValueAtTime(t0l,0); opL.setValueAtTime(t0l+F_DUR%s,100);'
            ' easePair(opL);' % _sq_li_p
        )
        _intro_word_anim = (
            'var op=wl[wj2].property("ADBE Transform Group").property("ADBE Opacity");\n'
            '                    var tw=(tms[wj2]!=null?tms[wj2]:0); if(tw<0)tw=0;\n'
            '                    %sop.setValueAtTime(tw, 0); op.setValueAtTime(tw+F_DUR%s, 100);'
            ' easePair(op);' % (_sq_wi_v, _sq_wi_p)
        )

    # Вызовы свечения жёлтого хайлайта: ставятся ПОСЛЕДНИМИ эффектами
    # слоя строки/слова, только если в сборке есть жёлтая строка интро.
    # Третья дверь свечения — галка intro_hl_glow (задание «glowfix»): раньше Glo2 на
    # жёлтом слове ставился мимо всех галок стиля. Тритон внутри функции к свечению
    # отношения не имеет (красит жёлтую букву на ярком цвете, _yellow_dark) — при снятой
    # галке он нужен и остаётся. Ни свечения, ни тритона — ни функции, ни вызовов: в .jsx
    # не остаётся ничего, чего в нём быть не должно.
    _hl_needed = _any_intro_yellow and (intro_hl_glow or _yellow_dark)
    _hl_call_line = ' if(ln.color=="yellow" && !grpGlitch && ln.fx!="glow") introHlGlow(Ll);' if _hl_needed else ""
    _hl_call_word = ' if(ln.color=="yellow" && !grpGlitch && ln.fx!="glow") introHlGlow(wl[wj2]);' if _hl_needed else ""
    _intro_line_anim += _hl_call_line
    _intro_word_anim += _hl_call_word

    # Числа Glo2 у жёлтого хайлайта — те же три ключа стиля, что у свечения слов
    # (intro_word_glow_thr/rad/int): при дефолтах 149/77/0.62 печатаются ровно прежними
    # литералами — .jsx не меняется ни на байт (golden).
    _hl_glow_set = ('var fxGl=addFX(L,"ADBE Glo2"); setP(fxGl,"ADBE Glo2-0002",%g);'
                    ' setP(fxGl,"ADBE Glo2-0003",%g); setP(fxGl,"ADBE Glo2-0004",%g);'
                    % (intro_word_glow_thr, intro_word_glow_rad, intro_word_glow_int))
    # Тритон в introHlGlow — вторая подстановка того же цвета; на ярком
    # цвете её нет, сама функция со свечением (Glo2) остаётся.
    _intro_hl_glow_fn = (
        '\n        function introHlGlow(L){\n'
        + (f'            {_hl_glow_set}\n' if intro_hl_glow else "")
        + (f'            var tt=addFX(L,"ADBE Tritone"); setP(tt,"ADBE Tritone-0002",{_yellow_expr});\n'
           if _yellow_dark else "")
        + '        }'
    ) if _hl_needed else ""

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
    # Четвёртая дверь свечения — галка intro_comp_glow (задание «glowfix»): Glo2 на слое
    # ПРЕКОМПА группы. Раньше эффект ставился всегда, а ключ intro_glow задавал только
    # Intensity, — снять свечение со всего блока пересборкой было нельзя: владелец гасил
    # Glo2 на слое слова, а светился прекомп. Галка снята — подстановка пустая, и Glo2 на
    # прекомпе не появляется НИ В ОДНОЙ из веток: ни мягкий 42/INTRO_GLOW, ни усиленный
    # 211/93/0.42 у группы с глитчем.
    if not intro_comp_glow:
        _intro_comp_glow = ""
    # Тень ПРЕКОМПА интро: своя у камеры 1 и камеры 2 — цвет и непрозрачность
    # у каждой свои, а направление/дистанция/мягкость общие (раньше стояли
    # жёстко 135/0/287). Все СЕМЬ ключей на дефолтах (белая, 68, 135/0/287) — подстановка
    # ровно прежняя строка dropShadow(iL, 68); иначе в шаблон едет объявление
    # introCompShadow и вызов с камерой группы (INTRO_ON2[gI]: 1 у группы на перебивке).
    # Дефолтный .jsx не меняется (golden).
    _ics_default = (intro_comp_shadow_fill == [1.0, 1.0, 1.0]
                    and intro_comp_shadow_op == 68.0
                    and intro_comp_shadow2_fill == [1.0, 1.0, 1.0]
                    and intro_comp_shadow2_op == 68.0
                    and float(intro_comp_shadow_dir) == 135.0
                    and float(intro_comp_shadow_dist) == 0.0
                    and float(intro_comp_shadow_soft) == 287.0)

    def _ics_rgb(fill: list[float]) -> str:
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
            " setP(ds,\"ADBE Drop Shadow-0003\",%g);"
            " setP(ds,\"ADBE Drop Shadow-0004\",%g);"
            " setP(ds,\"ADBE Drop Shadow-0005\",%g); }"
            % (_ics_rgb(intro_comp_shadow2_fill), _ics_rgb(intro_comp_shadow_fill),
               intro_comp_shadow2_op, intro_comp_shadow_op,
               intro_comp_shadow_dir, intro_comp_shadow_dist, intro_comp_shadow_soft)
        )

    return IntroTpl(
        hlfill3_decl=_hlfill3_decl,
        fill_decl=_intro_fill_decl,
        fill_params=_fill_params,
        fill_call=_fill_call,
        fill_pick=_intro_fill_pick,
        shadow_decl=_intro_shadow_decl,
        word_shadow_fn=_intro_word_shadow_fn,
        word_shadow_line=_intro_word_shadow_line,
        word_shadow_word=_intro_word_shadow_word,
        ly_decl=_intro_ly_decl,
        lx_decl=_intro_lx_decl,
        anchor_decl=_intro_anchor_decl,
        anchor_dy_js=_intro_anchor_dy_js,
        anchor_set=_intro_anchor_set,
        big_fn=_intro_big_fn,
        big_qi_vars=_intro_big_qi_vars,
        big_line_pos=_intro_big_line_pos,
        big_word_x=_intro_big_word_x,
        line_layout=_intro_line_layout,
        back_scale_fn=_intro_back_scale_fn,
        back_scale_line=_intro_back_scale_line,
        back_scale_line_w=_intro_back_scale_line_w,
        back_scale_tmp=_intro_back_scale_tmp,
        back_scale_word=_intro_back_scale_word,
        back_scale_wpx=_intro_back_scale_wpx,
        front_decl=_intro_front_decl,
        front_arr_decl=_intro_front_arr_decl,
        front_route=_intro_front_route,
        front_raise=_intro_front_raise,
        above_roto_decl=_intro_above_roto_decl,
        above_roto_arr_decl=_intro_above_roto_arr_decl,
        above_roto_route=_intro_above_roto_route,
        above_roto_raise=_intro_above_roto_raise,
        anim_fx_fn=_intro_anim_fx_fn,
        line_anim=_intro_line_anim,
        word_anim=_intro_word_anim,
        hl_glow_fn=_intro_hl_glow_fn,
        group_flags=_intro_group_flags,
        comp_glow=_intro_comp_glow,
        comp_shadow=_intro_comp_shadow,
        comp_shadow_fn=_intro_comp_shadow_fn,
    )
