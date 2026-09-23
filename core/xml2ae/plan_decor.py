# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Оформление кадра плана сцены (остаток распила scene_plan).

Модуль забирает из `scene_plan` всё, что рисуется ПОВЕРХ кадра и не зависит от
арифметики интро, вставок и звука:

* уход субтитров на rise-вставках (`sub_hide`) — окна скрытия полосы на фото кам2;
* имя композиции субтитров и их тень (`sub_comp_name`, `sub_shadow`, `sub_shadow_js`);
* плашка под субтитрами (`sub_bg_*`): план для превью, готовый JS слоя и масштаб слоя
  прекомпа субтитров (`sub_scale_js`, он же держит якорь плашки);
* верхняя строка-прогресс (`top_line_*`);
* подпись о ролике (`caption_*`);
* дисклеймер: кегль под ширину кадра и межстрочный зазор (`_disc_*`, `disc_lead`,
  `_disc_lead_code`) — им живут и головной, и хвостовой блоки шаблона.

Перенос ПОСТРОЧНЫЙ: поведение, числа и текст подстановок не менялись ни на байт
(проверяется эталоном fixtures/golden_geometry.jsx и побайтовым сравнением .jsx/плана).
Имена локальных переменных оставлены как в scene_plan — поэтому тело перенесено дословно,
а входы распаковываются в преамбуле. Дисклеймер считается ЗДЕСЬ же, хотя в scene_plan он
стоял ниже по тексту: это чистая функция от текста, шрифта и стиля, и порядок вызовов
на неё не влияет.

Вход — один неизменяемый `DecorInputs`, выход — один `DecorPlan` со всем, что `scene_plan`
читает дальше. Стиль приходит структурой `StyleValues` одним полем `style`, данные
субтитров (posy/кегль/шаг/масштаб) — результатом `plan_subs` одним полем `subs`: своей
копии ни тех, ни других чисел модуль не держит. Четыре функции-выражения AE
(`_sub_bg_expr`, `_caption_bg_size_expr`, `_caption_pos_expr`, `_caption_bg_pos_expr`)
переехали сюда вместе с кодом, который их зовёт; `_sub_bg_expr` и `_caption_bg_size_expr`
остаются контрактом сборки — их берут снаружи по-прежнему из `build` (это те же объекты).
"""
import re
from dataclasses import dataclass
from typing import Any, Mapping, cast

from core import fonts as _fonts
from core import paths

from .jsutil import _fill_js, _jd, _js, _r
from .layout import (DISC_FIT_W, INS_EXIT, INS_RISE_ENTER, SUB_BG_SH_DIR, SUB_BG_SH_DIST,
                     SUB_BG_SH_OP, SUB_BG_SH_SOFT, _ins_enter_exit)
from .plan_style import StyleValues, read_style
from .plan_subs import SubsPlan


@dataclass(frozen=True)
class DecorInputs:
    """Вход оформления кадра: всё, что `scene_plan` знает к моменту вызова.

    Поля названы как локальные переменные scene_plan: `meta` — ролик (тело читает
    `meta["h"]/["w"]/["name"]/["dur"]/["fps"]`), `inserts` — готовый план вставок
    (`plan_inserts`: по нему считаются окна ухода субтитров), `subs` — результат
    `plan_subs` (геометрия полосы и масштаб слоя субтитров), `caption`/`disclaimer` —
    тексты из kwargs сборки, `font_ps` — базовый шрифт (им меряется ширина строк
    дисклеймера), `style` — структура стиля, прочитанная ОДИН раз.
    """
    meta: dict
    fps: float
    inserts: list
    subs: SubsPlan
    caption: Any
    disclaimer: Any
    font_ps: Any
    style: StyleValues


@dataclass(frozen=True)
class DecorPlan:
    """Выход оформления кадра: ровно те имена, что `scene_plan` читает дальше.

    `sub_hide`/`sub_shadow`/`sub_bg_plan`/`top_line_plan`/`caption_plan` уезжают в план
    (предпросмотр), остальное — готовые подстановки .jsx; `disc_size` печатает сборка
    (`%g`) на месте подстановки DISC_SIZE.
    """
    sub_hide: list           # окна скрытия субтитров на rise-вставках (plan["sub_hide"])
    sub_comp_name: str       # имя композиции субтитров в главном композе
    sub_shadow: bool         # тень текста субтитров: при плашке снимается
    sub_shadow_js: str       # JS слоя тени субтитров (пусто при плашке)
    sub_bg_on: bool          # галка плашки: по ней якорь нула субтитров
    sub_bg_plan: object      # плашка для превью (None при выключенной галке)
    sub_bg_js: str           # JS слоя плашки (пусто при выключенной галке)
    sub_scale_js: str        # масштаб слоя прекомпа субтитров (пусто при 100%)
    top_line_plan: object    # строка-прогресс для превью (None при выключенной галке)
    top_line_js: str         # JS слоя строки-прогресса
    caption_plan: object     # подпись о ролике для превью (None при выключенной галке)
    caption_js: str          # JS слоя подписи
    disc_size: float | int   # кегль дисклеймера под ширину кадра
    disc_lead_decl: str      # объявление DISC_LEAD ("" — зазора нет)
    disc_lead_js: str        # применение зазора в головном блоке дисклеймера
    disc_lead_js_tail: str   # применение зазора в хвостовом блоке дисклеймера


def _sub_bg_expr(style: object, sub_layer_name: str = "Субтитры (текст)") -> str:
    """Выражение на размер плашки субтитров (обновлено DL).

    Текст берётся целиком из refs/sub_bg_size.js, подставляются 4 константы из стиля
    и имя слоя субтитров в главном композе. Стиль приходит структурой StyleValues;
    сырой словарь тоже принимается — им helper зовёт сторож стыка
    (tests/test_sub_bg.py), и это ровно тот же вход, что у read_style.
    """
    if isinstance(style, StyleValues):
        stv = style
    elif isinstance(style, Mapping):
        stv = read_style(style)
    else:
        stv = read_style({})
    h = stv.sub_bg_h
    pad = stv.sub_bg_pad / 100.0
    padmin = stv.sub_bg_padmin
    anim = stv.sub_bg_anim
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


def _caption_bg_size_expr(kx: float, ky: float) -> str:
    """Выражение на «Размер прямоугольника» плашки под подписью (обновлено DL)."""
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


def _caption_pos_expr(cap_x: float, cap_y: float, kx: float) -> str:
    """Выражение на позицию ТЕКСТА подписи: caption_x — ЛЕВЫЙ КРАЙ блока.

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


def _caption_bg_pos_expr() -> str:
    """Выражение на позицию плашки под подписью — центрирование по тексту."""
    ref_path = paths.data("refs", "caption_bg_pos.js")
    with open(ref_path, "r", encoding="utf-8") as f:
        src = f.read()
    lines = [ln for ln in src.splitlines() if not ln.startswith("//")]
    return "\n".join(lines).strip()


def plan_decor(inp: DecorInputs) -> DecorPlan:
    """Оформление кадра: уход субтитров, плашка, строка-прогресс, подпись, дисклеймер.

    Тело — дословный перенос блоков из scene_plan (до распила — строки 853-1075
    и 1312-1343, а также подстановка масштаба субтитров из 1620-1633): имена локальных
    переменных оставлены прежними, поэтому ни одна строка не переписана.
    """
    meta = inp.meta
    _fps0 = inp.fps
    inserts_plan = inp.inserts
    # Геометрия полосы субтитров и масштаб слоя — из результата plan_subs: своей копии
    # чисел модуль не держит (в scene_plan эти три строки считались второй раз теми же
    # формулами — копия убрана вместе с переносом).
    _posy, _fsize, _sub_step = inp.subs.posy, inp.subs.fsize, inp.subs.sub_step
    sub_scale = inp.subs.sub_scale
    caption, disclaimer = inp.caption, inp.disclaimer
    font_ps = inp.font_ps
    # Стиль — структурой, прочитанной один раз: имена локальных переменных оставлены
    # прежними (stv), источник у них теперь поле структуры.
    stv = inp.style
    # уход субтитров на вставках rise: для каждой rise-вставки
    # субтитры скрываются [[t0,100],[t0+en,0],[t1-ex,0],[t1,100]]; окна внахлёст объединяются
    sub_hide = []
    if stv.insert_anim == "rise" and stv.insert_sub_swap:
        rise_windows = []
        for xi in inserts_plan:
            if (xi.get("t") or "photo") == "photo" and xi.get("style") == "cam2":
                st_t = float(xi.get("start") or 0)
                en_t = float(xi.get("end") or 0)
                if en_t > st_t:
                    rise_windows.append((st_t, en_t, bool(xi.get("noexit"))))
        if rise_windows:
            merged_wins: list[list[float]] = []
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
    # фон (плашка) под субтитрами
    # фон (плашка) под субтитрами (обновлено DL)
    sub_comp_name = f"Субтитры ({meta['name']})" if meta.get("name") else "Субтитры (текст)"
    sub_bg_on = bool(stv.sub_bg)
    # Тень субтитров: при включённой плашке собственная тень текста снимается
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
        # центр = posy + (строк - 1) * sub_step / 2 - 0.27 * fsize + sub_bg_dy
        sub_bg_y = round(_posy + (stv.sub_rows_max - 1) * _sub_step / 2.0 - 0.27 * _fsize
                         + stv.sub_bg_dy, 2)
        sub_bg_plan = {
            "fill": stv.sub_bg_fill,
            "op": stv.sub_bg_op,
            "h": stv.sub_bg_h,
            "round": stv.sub_bg_round,
            "pad": stv.sub_bg_pad,
            "padmin": stv.sub_bg_padmin,
            "dy": stv.sub_bg_dy,
            "y": sub_bg_y,
            "anim": stv.sub_bg_anim,
            "layer": sub_comp_name,
        }
        # Плашка читает те же четыре константы стиля, что уехали в план (второго чтения
        # ключей нет): выражение собирается из структуры, как и всё остальное здесь.
        sub_bg_expr_code = _sub_bg_expr(stv, sub_layer_name=sub_comp_name)
        sub_bg_hide = [[k[0], (stv.sub_bg_op if k[1] > 0 else 0.0)] for k in sub_hide]
        sub_bg_js = (
            "\n    // ---- фон (плашка) под субтитрами ----\n"
            "    var bgLayer = main.layers.addShape();\n"
            '    bgLayer.name = "Фон субтитров";\n'
            '    var bgContents = bgLayer.property("ADBE Root Vectors Group");\n'
            '    var bgRect = bgContents.addProperty("ADBE Vector Shape - Rect");\n'
            f'    bgRect.property("ADBE Vector Rect Roundness").setValue({stv.sub_bg_round:g});\n'
            f'    bgRect.property("ADBE Vector Rect Size").expression = {_jd(sub_bg_expr_code)};\n'
            '    var bgFill = bgContents.addProperty("ADBE Vector Graphic - Fill");\n'
            f'    bgFill.property("ADBE Vector Fill Color").setValue({_fill_js(stv.sub_bg_fill)});\n'
            f'    bgLayer.property("ADBE Transform Group").property("ADBE Position").setValue([W/2, {sub_bg_y:g}]);\n'
            f'    bgLayer.property("ADBE Transform Group").property("ADBE Opacity").setValue({stv.sub_bg_op:g});\n'
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
    # Масштаб слоя прекомпа субтитров. При 100 — пусто, .jsx прежний
    # (golden). При другом значении: якорь и позицию слоя прекомпа переносим в точку
    # строки [W/2, POSY] (иначе масштаб от центра кадра утащит строку к середине и
    # sub_y начнёт врать), Scale = sub_scale. Плашка (bgLayer) — ОТДЕЛЬНЫЙ shape-слой:
    # её прямоугольник нарисован вокруг ЛОКАЛЬНОГО (0,0), поэтому якорь — локальная
    # координата точки масштабирования [0, POSY-BG_Y] (BG_Y = исходная Position.y
    # плашки), позиция — в ту же экранную точку [W/2, POSY]. Формула экрана
    # Position+(P_local-Anchor)*Scale при s=1 даёт ровно BG_Y (ничего не сдвинулось),
    # при s<1 плашка подтягивается к строке пропорционально — как в превью.
    sub_scale_js = (
        ("\n    // масштаб субтитров: якорь и позиция — в точку строки, "
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
        if sub_scale != 100.0 else "")
    # верхняя строка-прогресс
    top_line_on = bool(stv.top_line)
    top_line_js = ""
    top_line_plan = None
    if top_line_on:
        top_line_plan = {
            "y": stv.top_line_y,
            "w": stv.top_line_w,
            "th": stv.top_line_th,
            "from": stv.top_line_from,
            "to": stv.top_line_to,
            "track_fill": stv.top_line_track_fill,
            "track_op": stv.top_line_track_op,
            "dur": meta["dur"] / meta["fps"],
        }
        top_line_r = stv.top_line_th / 2.0
        tl_w_int = max(1, int(round(stv.top_line_w)))
        tl_th_int = max(1, int(round(stv.top_line_th)))
        tl_mask_r = tl_th_int / 2.0
        top_line_js = (
            "\n    // ---- верхняя строка-прогресс ----\n"
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
            f'    tlTrackRect.property("ADBE Vector Rect Size").setValue([{stv.top_line_w:g}, {stv.top_line_th:g}]);\n'
            f'    tlTrackRect.property("ADBE Vector Rect Roundness").setValue({top_line_r:g});\n'
            '    var tlTrackFill = tlTrackContents.addProperty("ADBE Vector Graphic - Fill");\n'
            f'    tlTrackFill.property("ADBE Vector Fill Color").setValue({_fill_js(stv.top_line_track_fill)});\n'
            f'    tlTrack.property("ADBE Transform Group").property("ADBE Position").setValue([W/2, {stv.top_line_y:g}]);\n'
            f'    tlTrack.property("ADBE Transform Group").property("ADBE Opacity").setValue({stv.top_line_track_op:g});\n'
            f'    var tlProg = main.layers.addSolid([1,1,1], "Строка (прогресс)", {tl_w_int}, {tl_th_int}, 1);\n'
            f'    tlProg.property("ADBE Transform Group").property("ADBE Position").setValue([W/2, {stv.top_line_y:g}]);\n'
            '    var tlRamp = tlProg.property("ADBE Effect Parade").addProperty("ADBE Ramp");\n'
            f'    tlRamp.property("ADBE Ramp-0001").setValue([0, {tl_th_int / 2.0:g}]);\n'
            f'    tlRamp.property("ADBE Ramp-0002").setValue({_fill_js(stv.top_line_from)});\n'
            f'    tlRamp.property("ADBE Ramp-0003").setValue([{tl_w_int:g}, {tl_th_int / 2.0:g}]);\n'
            f'    tlRamp.property("ADBE Ramp-0004").setValue({_fill_js(stv.top_line_to)});\n'
            '    var tlMask = tlProg.property("ADBE Mask Parade").addProperty("ADBE Mask Atom");\n'
            '    tlMask.name = "Раскрытие";\n'
            '    var tlMaskProp = tlMask.property("ADBE Mask Shape");\n'
            f'    tlMaskProp.setValueAtTime(0, _tlShape(0, 0, {tl_th_int}, {tl_th_int}, {tl_mask_r:g}));\n'
            f'    tlMaskProp.setValueAtTime(DUR, _tlShape(0, 0, {tl_w_int}, {tl_th_int}, {tl_mask_r:g}));\n'
            '    try{ tlTrack.moveToBeginning(); }catch(e){}\n'
            '    try{ tlProg.moveToBeginning(); }catch(e){}\n'
        )
    # подпись о ролике (обновлено DL)
    caption_on = bool(stv.caption)
    caption_text_raw = str(caption or "").strip()
    caption_text = (caption_text_raw.upper() if stv.caption_case == "upper"
                    else caption_text_raw)
    caption_js = ""
    caption_plan = None
    if caption_on:
        # множители плашки считаются от ВИДИМОГО текста (масштаб слоя всегда 100%):
        # в эталоне 181.4/105.6 по ширине и 88.2/35.5 по высоте ()
        cap_pos_expr = _caption_pos_expr(stv.caption_x, stv.caption_y, stv.caption_kx)
        caption_plan = {
            "text": caption_text,
            "font": stv.caption_font,
            "size": stv.caption_size,
            "fill": stv.caption_fill,
            "x": stv.caption_x,
            "y": stv.caption_y,
            "case": stv.caption_case,
            "bg": stv.caption_bg,
            "bg_fill": stv.caption_bg_fill,
            "bg_op": stv.caption_bg_op,
            "bg_round": stv.caption_bg_round,
            "kx": stv.caption_kx,
            "ky": stv.caption_ky,
        }
        if caption_text:
            bg_block = ""
            if stv.caption_bg:
                size_expr = _caption_bg_size_expr(stv.caption_kx, stv.caption_ky)
                pos_expr = _caption_bg_pos_expr()
                bg_block = (
                    '    var capBg = main.layers.addShape();\n'
                    '    capBg.name = "Подпись (фон)";\n'
                    '    var capBgContents = capBg.property("ADBE Root Vectors Group");\n'
                    '    var capBgRect = capBgContents.addProperty("ADBE Vector Shape - Rect");\n'
                    f'    capBgRect.property("ADBE Vector Rect Roundness").setValue({stv.caption_bg_round:g});\n'
                    f'    capBgRect.property("ADBE Vector Rect Size").expression = {_jd(size_expr)};\n'
                    '    var capBgFill = capBgContents.addProperty("ADBE Vector Graphic - Fill");\n'
                    f'    capBgFill.property("ADBE Vector Fill Color").setValue({_fill_js(stv.caption_bg_fill)});\n'
                    f'    capBg.property("ADBE Transform Group").property("ADBE Position").expression = {_jd(pos_expr)};\n'
                    f'    capBg.property("ADBE Transform Group").property("ADBE Opacity").setValue({stv.caption_bg_op:g});\n'
                )
            caption_js = (
                "\n    // ---- подпись о ролике ----\n"
                + bg_block
                + f'    var capLayer = main.layers.addText({_jd(caption_text)});\n'
                '    capLayer.name = "Подпись";\n'
                '    var capDoc = capLayer.property("ADBE Text Properties").property("ADBE Text Document");\n'
                f'    var capVal = capDoc.value; capVal.resetCharStyle(); capVal.resetParagraphStyle(); capVal.text = {_jd(caption_text)};\n'
                f'    try{{ setFont(capVal, {_js(stv.caption_font)}); }}catch(e){{}}\n'
                f'    capVal.fontSize = {stv.caption_size:g};\n'
                f'    capVal.fillColor = {_fill_js(stv.caption_fill)};\n'
                '    capVal.applyFill = true;\n'
                # caption_x — ЛЕВЫЙ КРАЙ блока подписи. Плашка центрируется на тексте
                # (своим выражением), поэтому её левый край садится на caption_x только
                # если центр надписи = caption_x + ширина плашки / 2 — это и считает
                # выражение на Position текста. Без плашки центрировать не от чего:
                # тогда текст просто выключен влево и начинается на caption_x.
                + ('    try{ capVal.justification = ParagraphJustification.CENTER_JUSTIFY; }catch(e){}\n'
                   if stv.caption_bg else
                   '    try{ capVal.justification = ParagraphJustification.LEFT_JUSTIFY; }catch(e){}\n')
                + '    capDoc.setValue(capVal);\n'
                + f'    capLayer.property("ADBE Transform Group").property("ADBE Position").setValue([{stv.caption_x:g}, {stv.caption_y:g}]);\n'
                + (f'    capLayer.property("ADBE Transform Group").property("ADBE Position").expression = {_jd(cap_pos_expr)};\n'
                   if stv.caption_bg else '')
                + ('    try{ capBg.moveToBeginning(); }catch(e){}\n' if stv.caption_bg else '')
                + '    try{ capLayer.moveToBeginning(); }catch(e){}\n'
            )
    # Дисклеймер подстраивается под шрифт. Кегль: база int(H·0.0245), но самая
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
    if stv.disc_gap is not None and len(_disc_lines) > 1:
        _disc_ink = [_fonts.ink_extent(font_ps, _ln, disc_size) for _ln in _disc_lines]
        if all(_x is not None for _x in _disc_ink):
            disc_lead = round(max(_disc_ink[_i][1] + _disc_ink[_i + 1][0]
                                  for _i in range(len(_disc_ink) - 1))
                              + float(cast(Any, stv.disc_gap)), 2)
    # Применение интервала к текстовому документу — в ОБОИХ блоках дисклеймера (головной в
    # template.py, концевой ниже). При дефолтах подстановка пустая: .jsx прежний байт в байт
    # (golden). typeof-guard: DISC_LEAD объявляется только вместе с зазором стиля.
    _disc_lead_code = ('if (typeof DISC_LEAD!=="undefined"){ try{ dd.autoLeading=false;'
                       ' dd.leading=DISC_LEAD; }catch(e){} }')
    disc_lead_decl = (", DISC_LEAD=%g" % disc_lead) if disc_lead is not None else ""
    disc_lead_js = ("" if disc_lead is None else _disc_lead_code + "\n        ")
    disc_lead_js_tail = ("" if disc_lead is None else "\n    " + _disc_lead_code)
    return DecorPlan(
        sub_hide=sub_hide, sub_comp_name=sub_comp_name, sub_shadow=sub_shadow,
        sub_shadow_js=sub_shadow_js, sub_bg_on=sub_bg_on, sub_bg_plan=sub_bg_plan,
        sub_bg_js=sub_bg_js, sub_scale_js=sub_scale_js,
        top_line_plan=top_line_plan, top_line_js=top_line_js,
        caption_plan=caption_plan, caption_js=caption_js,
        disc_size=disc_size, disc_lead_decl=disc_lead_decl,
        disc_lead_js=disc_lead_js, disc_lead_js_tail=disc_lead_js_tail)
