# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Камера плана сцены (задание MW, этап 6 распила scene_plan).

`scene_plan` был одной функцией на 3253 строки с 24 вложенными функциями, делившими
состояние замыканиями. Этапы 1–5 (задания MR/MS/MT/MU/MV) вынесли блок субтитров, расчёт
интро, вставки, звук и подстановки шаблона интро. Последний этап выносит сюда ВСЮ камеру:

* параметры Камеры 1 из стиля — точка наезда (`cam1_zoom_cx/cy`), сдвиг кадра (pan) и
  поворот: при дефолтах 0.5/0.5, 0/0 и 0 подстановки шаблона пусты и .jsx не меняется
  ни на байт (golden);
* ключи зума по режимам (`cam1_zoom`: pulse, jump, drift, none), наезды в тейках
  (`cam1_take_*`, в том числе по жёлтым словам) и «заполнение кадра» (`cam1_fit`) —
  оно умножается на ключи РОВНО ОДИН РАЗ, поэтому слежение за головой и автофит интро
  берут ключи уже с fit;
* тип интерполяции ключей (`holds`) и готовые подстановки шаблона CAM1_SCALE, CAM1_HOLDS,
  CAM1_EASE;
* разметку рото (`roto_plan`) и данные `plan["roto"]` — маски по ней делает `to_ae_full`
  на GPU, предпросмотр опирается на те же фрагменты;
* слежение за головой (`cam1_head_*`): чтение кэша `<стем>.head.json`, ключи CAM1_FOLLOW
  и их темпоральный ease;
* данные плана для предпросмотра — `plan["zoom"]` целиком (holds/fit/cx/cy/pan/rot/keys/
  ease/follow) и подстановки якоря точки наезда, позиций и поворотов рото-копий.

Перенос ПОСТРОЧНЫЙ: поведение, числа, порядок операций и ТЕКСТ подстановок .jsx не менялись
ни на байт (проверяется эталоном fixtures/golden_geometry.jsx и побайтовым сравнением
.jsx/плана). Имена локальных переменных оставлены как в scene_plan — поэтому тело перенесено
дословно, а входы распаковываются в преамбуле.

Почему дверь одна. Части камеры стояли в трёх местах scene_plan (параметры стиля — рядом с
макетом спикера, ключи зума — сразу после субтитров, рото — после данных вставок, слежение —
после затемнения интро), но зависимого кода между ними нет: всё, что камере нужно (слова
субтитров и индексы жёлтых — для наездов в тейках, флаг `roto` — для разметки, путь XML —
для кэша головы), готово к первой же строке блока. Порядок операций ВНУТРИ камеры сохранён
как был: ключи зума -> fit -> holds -> рото -> слежение -> данные плана -> подстановки.
Вторая дверь завела бы вторую копию ключей зума: их читают и подстановки шаблона, и
`plan["zoom"]` предпросмотра, и автофит интро (plan_intro.py).

Общее с другими блоками остаётся в `build.py` и приходит параметрами: `_sv`/`_sv_or`
(дефолты ключей стиля). Таблицы камеры живут в `layout.py` для всех блоков сразу:
`_cam1_zoom_keys`/`_cam1_jump_keys`/`_cam1_drift_keys`, `_zoom_key_holds`/`_zoom_key_eases`,
`_span_roto_plan`, `_cam1_follow_keys`. `_active_cam_at` камера не зовёт — он общий у
вставок и интро и остаётся в build.py. `to_ae_full` (трекинг головы ПЕРЕД сборкой) не
тронут: здесь только чтение уже готового кэша.
"""
from dataclasses import dataclass
from typing import Callable

from .jsutil import _jd, _r
from .layout import (EASE_DEFAULT, _cam1_drift_keys, _cam1_follow_keys, _cam1_jump_keys,
                     _cam1_zoom_keys, _span_roto_plan, _zoom_key_eases, _zoom_key_holds)


@dataclass(frozen=True)
class CameraInputs:
    """Вход камеры: всё, что `scene_plan` знает к моменту вызова.

    Поля названы как локальные переменные scene_plan. `cam1_scale` — kwarg сборки: None
    означает «посчитать по режиму стиля», готовый список ключей приходит из тестов и
    предпросмотра и режим перебивает. `subs`/`hl` нужны только ветке «наезды по жёлтым»
    (`cam1_take_yellow`): по временам жёлтых слов ставятся ключи.
    """
    # Камеры из разбора XML: по их клипам считаются и ключи зума, и разметка рото.
    cams: list
    # Ролик из разбора XML: ширина/высота/длительность в кадрах и fps без запаса.
    meta: dict
    # Частота кадров как _fps0 (meta["fps"] or 60) — ею считаются ключи и участки головы.
    fps: float
    # Слова субтитров (уже без вырезанных слов интро) и индексы жёлтых — для наездов
    # в тейках по жёлтым.
    subs: list
    hl: set
    # Резолвнутый стиль и обёртки чтения его ключей (общие с другими блоками).
    st: dict
    sv: Callable[[dict, str], object]
    sv_or: Callable[[dict, str], object]
    # Явные ключи зума (kwarg scene_plan) или None — тогда режим из стиля.
    cam1_scale: object
    # Галка «Авто-ротоскоп»: выключена — разметка пустая, `_roto_js` не позовёт GPU.
    roto: bool
    # Путь XML: рядом с ним лежит кэш трека головы `<стем>.head.json`.
    xml_path: str


@dataclass(frozen=True)
class CameraPlan:
    """Выход камеры: ровно те имена, что `scene_plan` читает дальше.

    Ключи зума отдаются уже с «заполнением кадра»: их читают подстановки шаблона
    (`cam1scale_js`), `plan["zoom"]`, автофит интро (plan_intro.py) и слежение. `roto` —
    готовый `plan["roto"]` (данные масок), `zoom` — готовый `plan["zoom"]` (предпросмотр).
    `*_js`/`*_decl` — подстановки шаблона: при выключенных ручках они пустые или прежние,
    и .jsx остаётся байт в байт (golden).
    """
    cam1_scale: list        # [[кадр, %], ...] (+ режим drift третьим элементом), уже с fit
    holds: list             # тип интерполяции каждого ключа: 1=HOLD, 0=BEZIER
    cam1scale_js: str       # подстановка CAM1_SCALE
    cam1_ease_js: str       # подстановка CAM1_EASE: [in, out] влияния на каждый ключ
    cam1holds_js: str       # подстановка CAM1_HOLDS (то же, что zoom["holds"])
    roto: list              # plan["roto"]: фрагменты масок (ci/ts/te/src/scale)
    zoom: dict              # plan["zoom"]: holds/fit/cx/cy/pan/rot/keys/ease (+follow)
    cam1_cx: float          # точка наезда Камеры 1, доли кадра (подстановка cam1_cx)
    cam1_cy: float
    cam1_moved: bool        # камера сдвинута/повёрнута/следит: иначе подстановки пустые
    cam1_follow_decl: str   # объявление var CAM1_FOLLOW
    cam1_follow_js: str     # код слежения: ключи на X-координату нула Камеры 1
    cam1_anchor: str        # якорь и позиция нула от точки наезда
    roto_pos_cc: str        # компенсация позиции рото-копии после привязки к нулу
    roto_pos_mk: str        # то же для маски
    cam1_rot_decl: str      # var CAM1_ROT
    cam1_rot_cam: str       # поворот слоя Камеры 1
    roto_rot_cc: str        # поворот рото-копии Камеры 1
    roto_rot_mk: str        # поворот маски Камеры 1


def plan_camera(inp: CameraInputs) -> CameraPlan:
    """Камера плана сцены: параметры из стиля, ключи зума, рото, слежение, подстановки.

    Тело — дословный перенос блоков из scene_plan (до распила — строки 657-664, 803-845,
    861-868, 1252-1288, 1467-1486 и 1559-1582): имена локальных переменных оставлены
    прежними, поэтому ни одна строка не переписана. Порядок операций тот же, и он важен:
    «заполнение кадра» умножается на ключи ДО holds и ДО слежения — иначе слежение
    сравнивало бы порог с ключами без fit, а `plan["zoom"]` разошёлся бы с .jsx.
    """
    cams = inp.cams
    meta = inp.meta
    _fps0 = inp.fps
    subs, hl = inp.subs, inp.hl
    st = inp.st
    # Общие с другими блоками обёртки — по-прежнему в build.py, сюда приходят параметрами.
    _sv, _sv_or = inp.sv, inp.sv_or
    cam1_scale = inp.cam1_scale
    xml_path = inp.xml_path
    # Флаг ротоскопа: в scene_plan он звался `roto`, но здесь именем `roto` называется и
    # готовый список плана — разводим их, чтобы не читать флаг после его использования.
    _roto_on = inp.roto

    # Макет спикера (задание Q): точка наезда камеры, сдвиг кадра (pan) и поворот. Дефолты
    # 0.5/0.5, 0/0 и 0 — ровно то, что AE ставит сам: подстановки шаблона при них пустые,
    # .jsx не меняется ни на байт (golden).
    cam1_cx = float(_sv(st, "cam1_zoom_cx"))
    cam1_cy = float(_sv(st, "cam1_zoom_cy"))
    pan_x = float(_sv_or(st, "cam1_pan_x"))
    pan_y = float(_sv_or(st, "cam1_pan_y"))
    rot = float(_sv_or(st, "cam1_rot"))
    _c1zoom = (_sv_or(st, "cam1_zoom"))         # pulse = наезд с откатом | jump = резкие скачки | drift = скачок+плавный дрейф 100–160% | none = нет зума
    if cam1_scale is None:                             # авто-зум по сменам кам1→кам2
        if _c1zoom == "none":
            cam1_scale = [(0, 100.0)]
        else:
            _zstart = _sv(st, "cam1_zoom_start")
            _zbig = float(_sv_or(st, "cam1_zoom_big"))
            _zlo = float(_sv_or(st, "cam1_zoom_lo"))
            _zhi = float(_sv_or(st, "cam1_zoom_hi"))
            if _c1zoom == "drift":
                _zdlo = float(_sv_or(st, "cam1_drift_lo"))
                _zdhi = float(_sv_or(st, "cam1_drift_hi"))
                cam1_scale = _cam1_drift_keys(cams, lo=_zdlo, hi=_zdhi, fps=meta["fps"], big=_zbig, start=_zstart)
            elif _c1zoom == "jump":
                _ztake = None
                if _sv(st, "cam1_take_zoom"):
                    _ztake = {
                        "min_s": float(_sv_or(st, "cam1_take_min")),
                        "lo": float(_sv_or(st, "cam1_take_lo")),
                        "hi": float(_sv_or(st, "cam1_take_hi")),
                        "hold_s": float(_sv_or(st, "cam1_take_hold")),
                    }
                    if _sv(st, "cam1_take_yellow"):
                        _ztake["words"] = sorted(subs[k][0] for k in hl)
                cam1_scale = _cam1_jump_keys(cams, lo=_zlo, hi=_zhi, fps=meta["fps"], start=_zstart, big=_zbig, take=_ztake)
            else:
                cam1_scale = _cam1_zoom_keys(cams, big=_zbig, lo=_zlo, hi=_zhi, fps=meta["fps"], start=_zstart)
    # «Заполнение кадра» (задание ZE) — общий множитель зума Камеры 1, и умножается он РОВНО
    # ЗДЕСЬ, один раз. Раньше fit сидел в Scale слоёв клипа и рото, и кадр рос вокруг своего
    # центра, а вставки кам1 с интро не росли вовсе — в превью кадр хороший, в AE уезжает на
    # 240–335 px (ipvZoomAt множит fit на ключи и масштабирует ВСЁ вокруг точки наезда).
    # Дальше ключи уже с fit берут все: CAM1_SCALE, автофит интро (_zoom_max), слежение
    # (_cam1_follow_keys) и план. Второй копии умножения не заводить.
    _fit_k = float(_sv_or(st, "cam1_fit")) / 100.0
    if _fit_k != 1.0:
        cam1_scale = [(f, round(v * _fit_k, 2), *rest) for f, v, *rest in (cam1_scale or [])]
    holds = _zoom_key_holds(cam1_scale or [], legacy_hold=(_c1zoom == "jump"))
    # 3-й элемент (mode: 1=HOLD, 0=BEZIER) эмитим только если он есть (drift); 2-элементные — легаси
    cam1scale_js = _jd([([_r(f), _r(v)] + ([int(rest[0])] if rest else []))
                        for f, v, *rest in (cam1_scale or [])])
    # ease на каждый ключ зума: JS больше не смотрит соседей/режимы, а берёт готовые
    # [in, out] влияния из данных (задание B)
    cam1_ease_js = _jd(_zoom_key_eases(cam1_scale or []))
    cam1holds_js = _jd([1 if h else 0 for h in holds])
    # разметка РОТО (дешёвое, без масок — их делает to_ae_full на GPU): сплошная копия
    # персонажа по видимой камере (EDL). Превью может опираться на те же фрагменты.
    # Выключенный ротоскоп — пустая разметка. Флаг тут не спрашивали, и полоса «здесь
    # рото» в предпросмотре оставалась гореть после «Авто-ротоскоп» выкл (жалоба
    # 2026-08-12). Соседняя строка про цензуру флаг спрашивает — здесь его забыли.
    roto_plan = [] if not _roto_on else [
        p for p in _span_roto_plan(cams, 0, int(meta["dur"]), meta["fps"])
                 if cams[p["ci"]].get("path")]       # нужен исходник камеры
    follow_keys = []
    if bool(st.get("cam1_head_follow")) and xml_path and cams and cams[0].get("path"):
        from core import headtrack
        ranges = headtrack.cam1_ranges(cams, _fps0)
        try:
            hdata = headtrack.load_cached(xml_path, cams[0]["path"], ranges=ranges)
        except TypeError:
            hdata = headtrack.load_cached(xml_path, cams[0]["path"])
        if hdata is not None:
            w_src = hdata.get("w") or meta["w"]
            h_src = hdata.get("h") or meta["h"]
            target = float(_sv(st, "cam1_head_x"))
            smooth_s = float(_sv(st, "cam1_head_smooth"))
            # Заполнение уже сидит в ключах зума (ZE): сюда 100 — слои клипа и рото кам1
            # заполняют кадр ровно, а fit растит нул вместе с детьми. Порог слежения —
            # в числах пользователя («150 — точка отсчёта для всего, пересчитывать в уме
            # нельзя»), а сравнивается он с ключами, которые уже ×k, — значит и порог ×k.
            min_scale = float(_sv(st, "cam1_head_min")) * _fit_k
            follow_keys = _cam1_follow_keys(
                cams=cams, pts=hdata.get("pts", []),
                w_src=w_src, h_src=h_src,
                zoom_keys=cam1_scale, holds=holds,
                fps=_fps0, W=meta["w"], H=meta["h"],
                cx=cam1_cx, pan_x=pan_x, cam1_fit=100.0,
                target=target, smooth_s=smooth_s,
                min_scale=min_scale,
            )

    # fit = 100 (ZE): заполнение живёт в ключах зума выше, а превью считает ровно так же —
    # (fit/100)·(ключ/100). Вторая копия умножения развела бы превью и AE.
    zoom_plan = {"holds": [1 if h else 0 for h in holds], "fit": 100.0,
                 "cx": cam1_cx, "cy": cam1_cy,
                 "pan": [pan_x, pan_y], "rot": rot,
                 "keys": cam1_scale or [], "ease": _zoom_key_eases(cam1_scale or [])}
    if follow_keys:
        zoom_plan["follow"] = {"keys": [list(k) for k in follow_keys],
                               "ease": [[EASE_DEFAULT, EASE_DEFAULT] for _ in follow_keys]}
    # Данные масок для плана: ключи разметки -> имена полей контракта (маски по ним делает
    # to_ae_full, предпросмотр рисует полосу «здесь рото»). Собираются здесь, а не в
    # build.py: иначе у разметки было бы два читателя одной формулы.
    roto = [{"ci": p["ci"], "ts": _r(p["tl_start"]), "te": _r(p["tl_end"]),
             "src_start": _r(p["src_start"]), "src_end": _r(p["src_end"]),
             "scale": _r(p["scale"])} for p in roto_plan]

    # Подстановки шаблона: собираются ТОЛЬКО под фактическое состояние камеры — при
    # дефолтах стиля каждая пустая (или прежняя строка) и .jsx остаётся байт в байт (golden).
    cam1_moved = (cam1_cx != 0.5 or cam1_cy != 0.5 or pan_x != 0 or pan_y != 0 or bool(follow_keys))
    cam1_anchor = ("" if not cam1_moved else
                   ("\n    // точка наезда камеры (задание Q): anchor+position от неё, "
                    "неподвижна именно она\n"
                    "    if(cam1null){ cam1null.property(\"ADBE Transform Group\").property(\"ADBE Anchor Point\").setValue([%g,%g]);"
                    " cam1null.property(\"ADBE Transform Group\").property(\"ADBE Position\").setValue([%g,%g]); }"
                    % (cam1_cx * meta["w"] - meta["w"] / 2, cam1_cy * meta["h"] - meta["h"] / 2,
                       cam1_cx * meta["w"] + pan_x, cam1_cy * meta["h"] + pan_y)))
    # Рото привязывается к нулу ПОСЛЕ того, как нул получил якорь точки наезда и ключи зума
    # (задание BK). AE при присвоении parent сохраняет мировое положение слоя и пересчитывает
    # локальную Position ребёнка под трансформ нула на ТЕКУЩИЙ момент — без принудительной
    # позиции рото уезжает на смещение точки наезда от центра кадра (Scale рядом уже
    # перезадаётся по той же причине). При дефолтной точке 0.5/0.5 смещения нет —
    # подстановки пустые, .jsx прежний (golden).
    roto_pos_cc = ("" if not cam1_moved else
                   ("\n            // AE компенсирует позицию при привязке по трансформу нула на"
                    "\n            // текущий момент, а нул уже несёт якорь точки наезда и ключи зума"
                    "\n            try{ cc.property(\"ADBE Transform Group\").property(\"ADBE Position\").setValue([0,0]); }catch(e){}"))
    roto_pos_mk = ("" if not cam1_moved else
                   ("\n            try{ mk.property(\"ADBE Transform Group\").property(\"ADBE Position\").setValue([0,0]); }catch(e){}"))
    cam1_rot_decl = ("\n    var CAM1_ROT=%g;" % rot if rot != 0 else "")
    cam1_rot_cam = (' if(!isSecond){ try{ lay.property("ADBE Transform Group").property("ADBE Rotate Z").setValue(CAM1_ROT); }catch(e){} }' if rot != 0 else "")
    roto_rot_cc = ('\n            if(ci==0){ try{ cc.property("ADBE Transform Group").property("ADBE Rotate Z").setValue(CAM1_ROT); }catch(e){} }' if rot != 0 else "")
    roto_rot_mk = ('\n            if(ci==0){ try{ mk.property("ADBE Transform Group").property("ADBE Rotate Z").setValue(CAM1_ROT); }catch(e){} }' if rot != 0 else "")
    cam1_follow_decl = ("\n    var CAM1_FOLLOW=%s;" % _jd([list(k) for k in follow_keys])) if follow_keys else ""
    cam1_follow_js = (
        "\n    // слежение за головой по X (задание ZC): ключи на X-координату нула Камеры 1\n"
        "    if (cam1null && typeof CAM1_FOLLOW !== \"undefined\" && CAM1_FOLLOW.length){\n"
        "        var pos = cam1null.property(\"ADBE Transform Group\").property(\"ADBE Position\");\n"
        "        pos.dimensionsSeparated = true;\n"
        "        var posX = cam1null.property(\"ADBE Transform Group\").property(\"ADBE Position_0\");\n"
        "        var base = posX.value;\n"
        "        for (var fi = 0; fi < CAM1_FOLLOW.length; fi++)\n"
        "            posX.setValueAtTime(CAM1_FOLLOW[fi][0] / FPS, base + CAM1_FOLLOW[fi][1]);\n"
        "        for (var ki = 1; ki <= posX.numKeys; ki++)\n"
        "            posX.setInterpolationTypeAtKey(ki, KeyframeInterpolationType.BEZIER, KeyframeInterpolationType.BEZIER);\n"
        "        var eIns = [], eOuts = [];\n"
        "        for (var ki2 = 0; ki2 < posX.numKeys; ki2++){\n"
        "            eIns.push(%(ease_default)g); eOuts.push(%(ease_default)g);\n"
        "        }\n"
        "        temporalEase(posX, eIns, eOuts);\n"
        "    }\n"
    ) % {"ease_default": EASE_DEFAULT} if follow_keys else ""

    return CameraPlan(
        cam1_scale=cam1_scale or [], holds=holds,
        cam1scale_js=cam1scale_js, cam1_ease_js=cam1_ease_js, cam1holds_js=cam1holds_js,
        roto=roto, zoom=zoom_plan,
        cam1_cx=cam1_cx, cam1_cy=cam1_cy, cam1_moved=cam1_moved,
        cam1_follow_decl=cam1_follow_decl, cam1_follow_js=cam1_follow_js,
        cam1_anchor=cam1_anchor, roto_pos_cc=roto_pos_cc, roto_pos_mk=roto_pos_mk,
        cam1_rot_decl=cam1_rot_decl, cam1_rot_cam=cam1_rot_cam,
        roto_rot_cc=roto_rot_cc, roto_rot_mk=roto_rot_mk)
