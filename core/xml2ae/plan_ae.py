# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Готовые токены шаблона `plan["_ae"]` (остаток распила scene_plan).

Модуль забирает из `scene_plan` последний крупный кусок — сборку словаря `plan["_ae"]`:
готовые строки JS, которые `template.py` подставляет в AE_FULL. Это НЕ контракт плана:
предпросмотр `_ae` не читает вовсе, его читает `to_ae_full`.

Здесь ровно то, что `scene_plan` раскладывал по ключам словаря: подстановки камеры
(`cam1scale`/`cam1holds`/`cam1_ease`/`cam1_follow_decl|js`, якорь и позиция нула, позиции
и повороты рото, строки рамки кадра), интро (`intro_*`: раскладка, подстановки цветов,
теней, свечения и маршрутизации слоёв, затемнение под интро), звук (`voice_*`, ризер, поп,
глитч, whoosh, переход), субтитры (`subs`, циклы слов и функции эффектов), вставки,
Lumetri, дисклеймер, размытие на старте и ключи плашки с подписью.

Перенос ПОСТРОЧНЫЙ: поведение, числа, порядок ключей и ТЕКСТ подстановок не менялись ни на
байт (проверяется эталоном fixtures/golden_geometry.jsx и побайтовым сравнением .jsx/плана).
Имена локальных переменных оставлены как в scene_plan — тело перенесено дословно, а входы
распаковываются в преамбуле.

Вход — один неизменяемый `AeInputs`: готовые планы и подстановки соседних модулей
(`plan_camera`, `plan_intro`, `plan_intro_tpl`, `plan_audio`, `plan_decor`, `plan_subs`,
`plan_inserts`), структура стиля, сам план сцены и числа, которые считает только
`scene_plan` (громкость музыки, экспозиция клипа, ширина кадра, факт подложки). Выход —
`AePlan` с единственным полем `tokens`: сам словарь `plan["_ae"]`. Подложка фото-вставок,
которую `scene_plan` дописывал в словарь ПОСЛЕ его сборки, живёт здесь же —
`plate_tokens`.
"""
from dataclasses import dataclass, field
from typing import Any

from .jsutil import _fill_js, _jd, _js, _js_multiline
from .layout import (EASE_DEFAULT, HL_EASE_IN, HL_EASE_OUT, INS_MASK_R, INTRO_LINE_STEP,
                     hl_size_decl)
from .plan_audio import AudioPlan
from .plan_camera import CameraPlan
from .plan_decor import SH_SUB_DIR, SH_SUB_DIST, SH_SUB_OP, SH_SUB_SOFT, DecorPlan
from .plan_inserts import InsertsPlan
from .plan_intro import IntroPlan
from .plan_intro_tpl import IntroTpl
from .plan_style import StyleValues
from .plan_subs import SubsPlan
from .template import VOICE_CLIP_DECL, VOICE_SRC_DECL, VOICE_WAV_DECL


# Цвет камер через Lumetri: ключ плана -> matchName эффекта в AE и подпись
# для лога. Номера сняты архитектором с живого AE 26.2 по свойствам эффекта (ADBE Lumetri),
# диапазоны ползунков — в core/style_schema.py. Порядок = порядок setValue в .jsx.
LUMETRI_PARAMS = (
    ("exposure", "ADBE Lumetri-0011", "Exposure"),
    ("contrast", "ADBE Lumetri-0012", "Contrast"),
    ("highlights", "ADBE Lumetri-0013", "Highlights"),
    ("shadows", "ADBE Lumetri-0014", "Shadows"),
    ("whites", "ADBE Lumetri-0015", "Whites"),
    ("blacks", "ADBE Lumetri-0016", "Blacks"),
    ("temp", "ADBE Lumetri-0007", "Temperature"),
    ("tint", "ADBE Lumetri-0008", "Tint"),
    ("sat", "ADBE Lumetri-0020", "Saturation"),
)


def _fps_js(fps: float | int) -> str:
    """Частота для строки `FPS=` в шаблоне. В шаблоне стояло `%d`, и NTSC-частота
    29.97 усекалась до 29 — кадры XML делились бы на 29, то есть на 3.2% быстрее
    реального времени. Целая частота печатается ровно как раньше (`60`, эталон .jsx
    не меняется), дробная — числом с 6 знаками после запятой (`29.97003`)."""
    f = float(fps)
    return ("%d" % f) if f.is_integer() else str(round(f, 6))


def _lumetri_decl(lum: dict[str, float] | None, lum2: dict[str, float] | None = None) -> str:
    """Объявление LUMETRI / LUMETRI2 и функций applyLumetri / applyLumetri2 для .jsx.

    Один эффект ADBE Lumetri на слой, значения — по matchName из LUMETRI_PARAMS;
    ошибки уходят в _LOG (пустых catch нет: иначе неверный цвет ищут в AE вслепую).
    При выключенной галке стиля (lum = None, lum2 = None) подстановка пустая — .jsx побайтово
    прежний (golden держит).
    """
    if not lum and not lum2:
        return ""
    # Начинается без ведущего \n и кончается \n: подстановка стоит в НАЧАЛЕ строки
    # шаблона (перед `var ROTO=`) — при выключенной галке строка шаблона не меняется.
    js: list[str] = []
    if lum:
        js.extend([
            "    // Цвет камер через Lumetri: значения из стиля, exposure уже",
            "\n    // включает экспозицию клипа. Один эффект на слой — как в панели Lumetri в AE.",
            "\n    var LUMETRI = {%s};"
            % ", ".join('%s: %g' % (k, lum[k]) for k, _mn, _lb in LUMETRI_PARAMS),
            "\n    function applyLumetri(L){",
            "\n        try{",
            "\n            var lc = L.property(\"ADBE Effect Parade\").addProperty(\"ADBE Lumetri\");",
        ])
        for _key, _mn, _label in LUMETRI_PARAMS:
            js.append("\n            try{ lc.property(\"%s\").setValue(LUMETRI.%s); }"
                      "catch(e){ _LOG(\"Lumetri %s: \" + e); }" % (_mn, _key, _label))
        js.append("\n        }catch(e){ _LOG(\"Lumetri на слое: \" + e); }")
        js.append("\n    }\n")
    if lum2:
        prefix = "" if not lum else "    // Цвет Камеры 2 через Lumetri (разомкнутая цепочка связи).\n"
        js.extend([
            prefix + "    var LUMETRI2 = {%s};"
            % ", ".join('%s: %g' % (k, lum2[k]) for k, _mn, _lb in LUMETRI_PARAMS),
            "\n    function applyLumetri2(L){",
            "\n        try{",
            "\n            var lc = L.property(\"ADBE Effect Parade\").addProperty(\"ADBE Lumetri\");",
        ])
        for _key, _mn, _label in LUMETRI_PARAMS:
            js.append("\n            try{ lc.property(\"%s\").setValue(LUMETRI2.%s); }"
                      "catch(e){ _LOG(\"Lumetri2 %s: \" + e); }" % (_mn, _key, _label))
        js.append("\n        }catch(e){ _LOG(\"Lumetri2 на слое: \" + e); }")
        js.append("\n    }\n")
    return "".join(js)


# Клипы камер: покадровая экспозиция (EXPOSURE) ИЛИ весь Lumetri из стиля — ровно в том
# же месте шаблона, что и раньше. Значения по умолчанию — прежний текст .jsx байт в байт.
LUMETRI_CAM_OFF = (
    "if (EXPOSURE!=0){ try{ var lc=lay.property(\"ADBE Effect Parade\").addProperty(\"ADBE Lumetri\");  // яркость на все камеры\n"
    "                try{ lc.property(\"ADBE Lumetri-0011\").setValue(EXPOSURE); }catch(e){} }catch(e){} }")
LUMETRI_CAM_ON = "applyLumetri(lay);"
# Рото-копии камер — то же самое, но своей строкой шаблона (без внешнего try).
LUMETRI_ROTO_OFF = (
    "if (EXPOSURE!=0){ try{ var lc=cc.property(\"ADBE Effect Parade\").addProperty(\"ADBE Lumetri\");\n"
    "                lc.property(\"ADBE Lumetri-0011\").setValue(EXPOSURE); }catch(e){} }")
LUMETRI_ROTO_ON = "applyLumetri(cc);"


@dataclass(frozen=True)
class AeInputs:
    """Вход сборки токенов шаблона: всё, что `scene_plan` знает к моменту вызова.

    Поля названы как локальные переменные scene_plan. Готовые планы и подстановки
    приходят полями целиком (`cam`, `intro`, `itpl`, `au`, `decor`, `subs`, `ins`) —
    второй копии их чисел и текста здесь нет; `tpl` — результат `plan_intro_tpl` вместе
    с подстановками затемнения и открепления интро, посчитанными в нём же. `style` —
    структура стиля, прочитанная один раз (`plan_style.read_style`), `plan` — собранный
    словарь плана: `_ae` несёт те же числа, что уехали в план, и берёт их оттуда, а не
    считает заново.
    """
    meta: dict
    plan: dict
    cam: CameraPlan
    au: AudioPlan
    subs: SubsPlan
    ins: InsertsPlan
    intro: IntroPlan
    itpl: IntroTpl
    decor: DecorPlan
    style: StyleValues
    # Готовые строки и числа соседних блоков: камеры плана (CAM), подстановки субтитров
    # и интро (INTRO_GROUPS), ключи зума и типов интерполяции, геометрия полосы субтитров.
    cams_js: str
    subs_js: str
    sub_loop: str
    cam1scale_js: str
    cam1_ease_js: str
    cam1holds_js: str
    cam1_cx: Any
    cam1_cy: Any
    cam1_anchor: str
    cam2_js: str
    roto_pos_cc: str
    roto_pos_mk: str
    cam1_rot_decl: str
    cam1_rot_cam: str
    cam2_rot_decl: str
    cam2_rot_cam: str
    roto_rot_cc: str
    roto_rot_mk: str
    cam1_follow_decl: str
    cam1_follow_js: str
    cam2_follow_decl: str
    cam2_follow_js: str
    cam_frame_pos: str
    roto_frame_scale: str
    roto_frame_pos: str
    hl_row_decl: str
    hl_blur_decl: str
    hl_blur_fn: str
    hl_short_fn: str
    # Геометрия полосы субтитров (имена с подчёркиванием — как локальные в scene_plan).
    posy: Any
    hl_rise: Any
    hl_step: Any
    hl_dur: Any
    fsize: Any
    fsize_base: Any
    inserts_js: str
    # Числа, которые считает только scene_plan: кадр, fps, громкость музыки, тексты,
    # шрифты, имена и флаги блоков.
    fps: float
    music_db: Any
    # Путь запечённого голоса Камеры 1: None — голос не обработан (подстановки пустые).
    voice_wav: "str | None"
    voice_db: Any
    audio_fade: Any
    riser: Any
    pop: Any
    censor_js: str
    music_path: Any
    trans: str
    trans_sfx: str
    intro_font_ps: str
    intro_hl_font_ps: str
    intro_mode: str
    font_ps: str
    hl_font_ps: str
    disclaimer: Any
    disc_sec: float
    disc_end_on: bool
    lumetri: "dict[str, float] | None"
    lumetri2: "dict[str, float] | None"
    dg_on: bool
    accent_used: bool
    any_plate: bool
    # Подстановки звука из plan_audio: обрезка/точка удара/громкость и объявления голоса.
    pop_place: str = ""
    pop_tail: str = ""
    glitch_sfx: str = ""
    wsfx_place: str = ""
    wsfx_tail: str = ""
    riser_place: str = ""
    riser_tail: str = ""
    trans_place: str = ""
    trans_tail: str = ""


@dataclass(frozen=True)
class AePlan:
    """Выход: сам словарь `plan["_ae"]` (поле `tokens`).

    `default_factory=dict`: пустой словарь — не «нет токенов», а нормальный результат
    выключенных галок, и дописывать в него ключи вправе вызывающий (подложка — здесь же,
    `to_ae_full` копирует его себе перед подстановкой).
    """
    tokens: dict[str, Any] = field(default_factory=dict)


def plan_ae(inp: AeInputs) -> AePlan:
    """Собрать готовые токены шаблона: чистая функция от `AeInputs`.

    Ни стиль, ни план не читает: все числа и готовые строки приходят полями — второго
    чтения ключей стиля и второго подсчёта подстановок нет.
    """
    # ---- входы: имена ровно как в scene_plan (тело ниже перенесено дословно) ----
    meta = inp.meta
    plan = inp.plan
    stv = inp.style
    _cam, _au, _subs, _ip = inp.cam, inp.au, inp.subs, inp.ins
    _intro, _itpl, _decor = inp.intro, inp.itpl, inp.decor
    cams_js, subs_js, sub_loop = inp.cams_js, inp.subs_js, inp.sub_loop
    cam1scale_js, cam1_ease_js, cam1holds_js = inp.cam1scale_js, inp.cam1_ease_js, inp.cam1holds_js
    cam1_cx, cam1_cy, cam1_anchor = inp.cam1_cx, inp.cam1_cy, inp.cam1_anchor
    cam2_js = inp.cam2_js
    roto_pos_cc, roto_pos_mk = inp.roto_pos_cc, inp.roto_pos_mk
    cam1_rot_decl, cam1_rot_cam = inp.cam1_rot_decl, inp.cam1_rot_cam
    cam2_rot_decl, cam2_rot_cam = inp.cam2_rot_decl, inp.cam2_rot_cam
    roto_rot_cc, roto_rot_mk = inp.roto_rot_cc, inp.roto_rot_mk
    cam1_follow_decl, cam1_follow_js = inp.cam1_follow_decl, inp.cam1_follow_js
    cam2_follow_decl, cam2_follow_js = inp.cam2_follow_decl, inp.cam2_follow_js
    cam_frame_pos, roto_frame_scale, roto_frame_pos = (inp.cam_frame_pos,
                                                       inp.roto_frame_scale,
                                                       inp.roto_frame_pos)
    hl_row_decl, hl_blur_decl, hl_blur_fn = inp.hl_row_decl, inp.hl_blur_decl, inp.hl_blur_fn
    hl_short_fn = inp.hl_short_fn
    _posy, _hl_rise, _hl_step, _hl_dur = inp.posy, inp.hl_rise, inp.hl_step, inp.hl_dur
    _fsize, _fsize_base = inp.fsize, inp.fsize_base
    inserts_js = inp.inserts_js
    music_db, voice_wav, voice_db = inp.music_db, inp.voice_wav, inp.voice_db
    audio_fade, riser, pop, censor_js = inp.audio_fade, inp.riser, inp.pop, inp.censor_js
    music_path, trans, trans_sfx = inp.music_path, inp.trans, inp.trans_sfx
    intro_font_ps, intro_hl_font_ps = inp.intro_font_ps, inp.intro_hl_font_ps
    intro_mode, font_ps, hl_font_ps = inp.intro_mode, inp.font_ps, inp.hl_font_ps
    disclaimer, disc_sec, disc_end_on = inp.disclaimer, inp.disc_sec, inp.disc_end_on
    lumetri, lumetri2 = inp.lumetri, inp.lumetri2
    dg_on, _accent_used, any_plate = inp.dg_on, inp.accent_used, inp.any_plate
    pop_place, pop_tail, glitch_sfx = inp.pop_place, inp.pop_tail, inp.glitch_sfx
    wsfx_place, wsfx_tail = inp.wsfx_place, inp.wsfx_tail
    riser_place, riser_tail = inp.riser_place, inp.riser_tail
    trans_place, trans_tail = inp.trans_place, inp.trans_tail
    _intro_cam_decl, _intro_cam_cond = _itpl.cam_decl, _itpl.cam_cond
    _intro2_cam2_js, _intro_shade_js = _itpl.intro2_cam2_js, _itpl.shade_js

    tokens = dict(
    w=meta["w"], h=meta["h"], fps=_fps_js(meta["fps"]), dur=meta["dur"] / meta["fps"],
    name=_js(meta["name"]), cams=cams_js, subs=subs_js, cam1scale=cam1scale_js,
    cam1_ease=cam1_ease_js,
    cam1holds=cam1holds_js,
    # Слои клипа и рото кам1 заполняют кадр ровно: их прежний масштаб
    # переехал в ключи зума нула, иначе фит растил бы кадр вокруг СВОЕГО центра.
    cam1_fit=100.0,
    cam1_follow_decl=cam1_follow_decl,
    cam1_follow_js=cam1_follow_js,
    cam2_follow_decl=cam2_follow_decl,
    cam2_follow_js=cam2_follow_js,
    intro_scale=float(stv.intro_scale), intro_y=float(stv.intro_y),
    intro_y2=float(stv.intro_y2), intro_on2=_jd(_intro.on2),
    # Открепление интро от Камеры 1: объявление INTRO_CAM и добавка
    # «&& INTRO_CAM» к условию привязки. При дефолтном True обе подстановки пустые —
    # .jsx прежний байт в байт (golden).
    intro_cam_decl=_intro_cam_decl,
    intro_cam_cond=_intro_cam_cond,
    # Подъём интро над видеовставкой: все подстановки пустые, когда front выключен.
    intro_front_decl=_itpl.front_decl,
    intro_front_arr_decl=_itpl.front_arr_decl,
    intro_front_route=_itpl.front_route,
    intro_front_raise=_itpl.front_raise,
    # Подъём интро над рото по положению: подстановки непустые только
    # при галке стиля и группе в нижней половине кадра, иначе .jsx прежний (golden).
    intro_above_roto_decl=_itpl.above_roto_decl,
    intro_above_roto_arr_decl=_itpl.above_roto_arr_decl,
    intro_above_roto_route=_itpl.above_roto_route,
    intro_above_roto_raise=_itpl.above_roto_raise,
    # Y базовых линий строк интро: непусто при строках заднего плана
    # или якоре «first», иначе пусто — .jsx прежний байт в байт (golden).
    intro_ly_decl=_itpl.ly_decl,
    # Точка масштабирования прекомпа интро (intro_scale_anchor): при дефолтном "comp"
    # все три подстановки пустые — .jsx прежний байт в байт (golden). Числа (Y якоря и
    # компенсация Position) считает plan_intro, шаблон только применяет.
    intro_anchor_decl=_itpl.anchor_decl,
    intro_anchor_dy_js=_itpl.anchor_dy_js,
    intro_anchor_set=_itpl.anchor_set,
    # Большая строка: массивы INTRO_LX/INTRO_LK и куски шаблона для неё.
    # Нет большой строки ни в одной группе — все подстановки пустые (golden).
    intro_lx_decl=_itpl.lx_decl,
    intro_big_fn=_itpl.big_fn,
    intro_big_qi_vars=_itpl.big_qi_vars,
    intro_big_line_pos=_itpl.big_line_pos,
    intro_big_word_x=_itpl.big_word_x,
    sub_hide=_jd(_decor.sub_hide),
    sub_comp_name=_js(_decor.sub_comp_name),
    # готовые iDy каждой группы: шаблон больше не считает опускание
    # под INTRO_SAFE_TOP сам — берёт число, как берёт INS_C2_Y. Превью читает то же
    # из plan.intro[].y, поэтому база интро живёт в одном месте.
    intro_idy=_jd(_intro.idy),
    # Длительность фейд-аута прекомпов интро
    intro_fade=stv.intro_fade,
    # Межстрочный шаг строк интро в пикселях: 160 × intro_line_step/100.
    # При дефолтных 100% %g печатает ровно «160» — .jsx прежний байт в байт (golden).
    # Число строк и центровку блока считает Python (intro_line_ys) — второго шага нет.
    intro_line_step_px=INTRO_LINE_STEP * stv.line_step_k,
    # Окна фейд-аута прекомпов с глитчем (ПРАВКА 3/4): подстановки непустые только
    # при глитче в ролике, иначе .jsx прежний (golden).
    intro_fx_decl=_intro.fx_decl,
    intro_fx_out=_intro.fx_out,
    # Группы, гаснущие к появлению субтитра: окно [начало затухания,
    # конец слоя] на группу; пусто, когда таких групп нет — .jsx прежний (golden).
    intro_sub_fx_decl=_intro.sub_fx_decl,
    intro_sub_fx_out=_intro.sub_fx_out,
    # Множители длительности появления слов: массив INTRO_SQ и его
    # читалка introSQ. Нет сжатых слов — подстановки пусты (golden).
    intro_sq_decl=_intro.sq_decl,
    intro_sq_fn=_intro.sq_fn,
    # Затемнение под интро: непусто только при галке стиля, иначе .jsx
    # прежний байт в байт (golden). Слой создаётся сразу после камер — значит выше
    # клипов камер, а всё добавленное позже (вставки, интро, рото, субтитры, нулы)
    # встаёт выше него; блок LAYER_ORDER группы не трогает.
    intro_shade_js=_intro_shade_js,
    # Макет спикера: точка наезда Камеры 1 и точка покоя вставок Кам2,
    # сдвиг интро по X. Дефолты пустые подстановки — .jsx прежний (golden).
    # Камера 1: якорь и позиция нула считаются от точки наезда (cx/cy доли кадра).
    # При дефолте 0.5/0.5 это ровно то, что AE ставит сам, — кода нет вовсе.
    # Сами строки (якорь, позиции и повороты рото) собраны в plan_camera.py.
    cam1_cx=cam1_cx, cam1_cy=cam1_cy,
    cam1_anchor=cam1_anchor,
    cam2_js=cam2_js,
    intro2_cam2_js=_intro2_cam2_js,
    roto_pos_cc=roto_pos_cc,
    roto_pos_mk=roto_pos_mk,
    cam1_rot_decl=cam1_rot_decl,
    cam1_rot_cam=cam1_rot_cam,
    cam2_rot_decl=cam2_rot_decl,
    cam2_rot_cam=cam2_rot_cam,
    roto_rot_cc=roto_rot_cc,
    roto_rot_mk=roto_rot_mk,
    # Рамка кадра камеры: сдвиг слоя клипа, масштаб и сдвиг рото-копии с маской.
    # Без рамок ни в одной камере все три пустые — .jsx прежний (golden), кроме
    # строки масштаба клипов: она одна на все камеры (fitS × zoom рамки).
    cam_frame_pos=cam_frame_pos,
    roto_frame_scale=roto_frame_scale,
    roto_frame_pos=roto_frame_pos,
    # вставки Кам2: точка покоя по X и Y в px (в стиле insert_c2_x/y, долями кадра).
    # Дефолт 0.5/0.172 — X остаётся W/2, Y как INS_C2_Y_FR*H: объявление INS_C2_X
    # и подстановка в позицию пустые, .jsx прежний (golden).
    ins_c2x=plan["ins_c2x"], ins_c2y=plan["ins_c2y"],
    ins_c2x_decl=(", INS_C2_X=%d" % plan["ins_c2x"] if stv.insert_c2_x != 0.5 else ""),
    ins_c2x_pos=("INS_C2_X" if stv.insert_c2_x != 0.5 else "W/2"),
    # интро: сдвиг по X (px), дефолт 0 — подстановка «0» даёт прежнюю строку [0,INTRO_Y]
    intro_x_js=("%g" % float(stv.intro_x) if stv.intro_x else "0"),
    intro_x_p=("+%g" % float(stv.intro_x) if stv.intro_x else ""),
    music=_js(music_path) if music_path else '""', music_db=music_db,
    # Громкость голоса и микро-фейд клипов посчитаны в plan_audio.py:
    # те же числа уехали в plan["audio"], второй копии чтения стиля нет.
    voice_db=voice_db, audio_fade=audio_fade,
    # Обработанный голос камеры 1: объявление VOICE_WAV, импорт WAV и аудиослой
    # клипа; voice_lay — на кого ложатся громкость, фейды и цензура. Голос не
    # обработан — все четыре подстановки прежние (пусто и "lay"), .jsx байт в
    # байт прежний (golden).
    voice_wav=(VOICE_WAV_DECL % _js(voice_wav)) if voice_wav else "",
    voice_src=(VOICE_SRC_DECL if voice_wav else ""),
    voice_clip=(VOICE_CLIP_DECL if voice_wav else ""),
    voice_lay=("vl" if voice_wav else "lay"),
    riser=_js(riser) if riser else '""',
    pop=_js(pop) if pop else '""', censor=censor_js, intro_groups=_intro.groups_js,
    # Звуки с обрезкой/точкой удара/громкостью: дефолты = прежние
    # JS-строки, .jsx не меняется (golden). При заданных ключах — готовые фрагменты.
    pop_place=pop_place, pop_tail=pop_tail,
    glitch_sfx=glitch_sfx,
    wsfx_place=wsfx_place, wsfx_tail=wsfx_tail,
    riser_place=riser_place, riser_tail=riser_tail,
    trans_place=trans_place, trans_tail=trans_tail,
    intro_font=_js(intro_font_ps), intro_hl_font=_js(intro_hl_font_ps),
    intro_mode=_js(intro_mode or "word"),
    # Акцентный шрифт интро: если ни одна строка не отмечена галкой
    # или accent_font пуст — все три подстановки пустые и .jsx прежний (golden).
    accent_params=(",af" if _accent_used else ""),
    accent_font_pick=('(af||(col=="yellow"?INTRO_HL_FONT:INTRO_FONT))' if _accent_used
                      else '(col=="yellow"?INTRO_HL_FONT:INTRO_FONT)'),
    accent_call=(",ln.accent_font" if _accent_used else ""),
    # Цвета текста интро (новые ключи стиля): hl_fill3 (color=="accent"), свой
    # intro_fill/intro_hl_fill и цвет строки color=="custom" (fill_call, ln.fill).
    # Дефолты — все подстановки пустые/прежние, .jsx не меняется ни на байт (golden).
    hlfill3_decl=_itpl.hlfill3_decl,
    intro_fill_decl=_itpl.fill_decl,
    fill_params=_itpl.fill_params,
    fill_call=_itpl.fill_call,
    intro_fill_pick=_itpl.fill_pick,
    # Тень на каждом слове интро (intro_shadow): выключено — пустые подстановки.
    intro_shadow_decl=_itpl.shadow_decl,
    intro_word_shadow_fn=_itpl.word_shadow_fn,
    intro_word_shadow_line=_itpl.word_shadow_line,
    intro_word_shadow_word=_itpl.word_shadow_word,
    intro_anim_fx_fn=_itpl.anim_fx_fn,
    dg_on=dg_on,
    dg_report="",
    intro_hl_glow_fn=_itpl.hl_glow_fn,
    intro_group_flags=_itpl.group_flags,
    intro_line_anim=_itpl.line_anim,
    intro_word_anim=_itpl.word_anim,
    intro_comp_glow=_itpl.comp_glow,
    # Тень прекомпа интро: дефолты — ровно прежняя строка dropShadow(iL, 68)
    # и пустое объявление (golden); иначе — функция introCompShadow + вызов по камере.
    intro_comp_shadow=_itpl.comp_shadow,
    intro_comp_shadow_fn=_itpl.comp_shadow_fn,
    intro_line_layout=_itpl.line_layout,
    intro_back_scale_fn=_itpl.back_scale_fn,
    intro_back_scale_line=_itpl.back_scale_line,
    intro_back_scale_line_w=_itpl.back_scale_line_w,
    intro_back_scale_tmp=_itpl.back_scale_tmp,
    intro_back_scale_word=_itpl.back_scale_word,
    intro_back_scale_wpx=_itpl.back_scale_wpx,
    intro_glow=stv.intro_glow,
    # Яркости клипа (EXPOSURE) больше нет: ручка убрана из интерфейса, Lumetri задаётся
    # стилем. Значение жёстко 0 = «не вешать» — подстановка `if (EXPOSURE!=0)` в шаблоне
    # остаётся прежней и не срабатывает никогда, поэтому .jsx побайтово как раньше (golden).
    exposure=0.0, roto="[]",
    # Цвет камер через Lumetri: при выключенной галке подстановки несут
    # ровно прежний текст шаблона и пустое объявление — .jsx побайтово как раньше
    # (golden). При включённой: LUMETRI / LUMETRI2 + applyLumetri / applyLumetri2 вместо
    # покадровой экспозиции на клипах камер и их рото-копиях.
    lumetri_decl=_lumetri_decl(lumetri, lumetri2),
    lumetri_cam=(
        (LUMETRI_CAM_ON if lumetri else LUMETRI_CAM_OFF)
        if stv.lm2_link else (
            LUMETRI_CAM_OFF if (not lumetri and not lumetri2) else
            ("if (isSecond){\n"
             "                %s\n"
             "            }else{\n"
             "                %s\n"
             "            }" % ("applyLumetri2(lay);" if lumetri2 else LUMETRI_CAM_OFF,
                                LUMETRI_CAM_ON if lumetri else LUMETRI_CAM_OFF))
        )
    ),
    lumetri_roto=(
        (LUMETRI_ROTO_ON if lumetri else LUMETRI_ROTO_OFF)
        if stv.lm2_link else (
            LUMETRI_ROTO_OFF if (not lumetri and not lumetri2) else
            ("if (ci==1){\n"
             "                %s\n"
             "            }else{\n"
             "                %s\n"
             "            }" % ("applyLumetri2(cc);" if lumetri2 else LUMETRI_ROTO_OFF,
                                LUMETRI_ROTO_ON if lumetri else LUMETRI_ROTO_OFF))
        )
    ),
    inserts=inserts_js, trans=_js(trans) if trans else '""',
    trans_sfx=_js(trans_sfx) if trans_sfx else '""',
    # Сдвиги перехода: те же числа, что считают события звука в плане (plan_audio:
    # TR_IN/TR_SFX_LEAD). Подстановка печатает их %g — .jsx остаётся прежним байт
    # в байт (0.386/0.083), а число теперь одно на шаблон и на план.
    tr_in=_au.trans_in, tr_sfx_lead=_au.trans_sfx_lead,
    hl_rise=_hl_rise, hl_step=_hl_step, hl_dur=_hl_dur,
    hl_ease_out=HL_EASE_OUT, hl_ease_in=HL_EASE_IN,
    # Жёлтые в строке, блюр появления и длительность появления короткого
    # жёлтого: при дефолтах все подстановки пусты — .jsx прежний байт в
    # байт (golden).
    hl_row_decl=hl_row_decl, hl_blur_decl=hl_blur_decl, hl_blur_fn=hl_blur_fn,
    hl_short_fn=hl_short_fn,
    ease_default=EASE_DEFAULT,
    disclaimer=_js_multiline(disclaimer) if disclaimer else '""',
    # Кегль дисклеймера строкой: целое 47 печатается ровно «47» (было %d), ужатый под
    # ширину кадра кегль — «42.85». DISC_LEAD — только при зазоре строк в стиле.
    disc_end=disc_sec, disc_size=("%g" % _decor.disc_size),
    disc_lead_decl=_decor.disc_lead_decl, disc_lead_js=_decor.disc_lead_js,
    # Положение дисклеймера считает plan_decor (ручки disc_y/disc_dx): прежнее
    # int(H·0.764) при умолчаниях даёт ровно то же число, .jsx прежний байт в байт.
    disc_y=_decor.disc_y, disc_x_decl=_decor.disc_x_decl, disc_x_js=_decor.disc_x_js,
    # Размытие на старте: Adjustment Layer поверх всего + Gaussian Blur,
    # ключи start_blur -> 0 за start_blur_dur. Выключено (start_blur=0) — пусто.
    start_blur=stv.start_blur,
    blur_js=("" if stv.start_blur <= 0 else
             "\n    // размытие на старте: Adjustment Layer поверх всего,"
             "\n    // Gaussian Blur %(sb)g -> 0 за %(sd)g c" % {"sb": stv.start_blur,
                                                            "sd": stv.start_blur_dur}
             # addAdjustmentLayer в API After Effects НЕТ (есть add/addNull/addSolid/
             # addText/addCamera/addLight/addShape) — корректирующий слой это солид с
             # флагом adjustmentLayer. И matchName эффекта — «ADBE Gaussian Blur 2»,
             # с пробелом: он снят с живого проекта (sample1.inspect.json). Оба промаха
             # роняют сборку в AE, а node --check их не видит — синтаксис-то верный.
             + "\n    var sbl=main.layers.addSolid([1,1,1], \"Размытие на старте\", W, H, 1);"
               "\n    sbl.adjustmentLayer=true;"
               "\n    var sbe=sbl.property(\"ADBE Effect Parade\").addProperty(\"ADBE Gaussian Blur 2\");"
               # -0003 это Repeat Edge Pixels: в AE галка включена по умолчанию и портит края текста
               "\n    sbe.property(\"ADBE Gaussian Blur 2-0003\").setValue(0);   // Repeat Edge Pixels = 0"
               "\n    sbe.property(\"ADBE Gaussian Blur 2-0001\").setValueAtTime(0, %(sb)g);"
               "\n    sbe.property(\"ADBE Gaussian Blur 2-0001\").setValueAtTime(%(sd)g, 0);"
               "\n    try{ sbl.moveToBeginning(); }catch(e){}"
               % {"sb": stv.start_blur, "sd": stv.start_blur_dur}),
    # Хвостовой дисклеймер: копия головного на конец контента, держится
    # 1 с, гаснет за 0.35 — та же раскладка ключей, что у головного, со сдвигом.
    # Выключено (нет галки или текст пуст) — пусто; композиция не удлиняется.
    disc_end_js=("" if not disc_end_on else
                 "\n    // дисклеймер в конце: копия головного на конец контента"
                 "\n    var dle=main.layers.addText(DISCLAIMER);"
                 "\n    var dsp=dle.property(\"ADBE Text Properties\").property(\"ADBE Text Document\");"
                 "\n    var dd=dsp.value; dd.resetCharStyle(); dd.resetParagraphStyle(); dd.text=DISCLAIMER;"
                 "\n    try{setFont(dd, FONT);}catch(e){} dd.fontSize=DISC_SIZE; dd.fillColor=[1,1,1]; dd.applyFill=true;"
                 "\n    try{dd.justification=ParagraphJustification.CENTER_JUSTIFY;}catch(e){}"
                 + _decor.disc_lead_js_tail +
                 "\n    dsp.setValue(dd);"
                 "\n    dle.property(\"ADBE Transform Group\").property(\"ADBE Position\").setValue([%s, DISC_Y]);"
                 % _decor.disc_x_js
                 + "\n    dle.inPoint=DUR;"
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
    # Кегль текста интро в шаблоне (доработка ZL): при ужатых строках субтитров introDoc
    # обязан ставить СВОЙ кегль, а не FONT_SIZE. Кегли равны (режим по слову) —
    # подстановка ровно "FONT_SIZE", и .jsx прежний байт в байт (golden).
    intro_fsize_js=("FONT_SIZE" if _fsize_base == _fsize else str(_fsize_base)),
    posy=_posy,
    font=_js(font_ps), hl_font=_js(hl_font_ps), hlfill=_fill_js(stv.hl_fill),
    fill=_fill_js(stv.sub_fill if stv.sub_fill else [1, 1, 1]),
    hl_bold=("true" if stv.hl_bold else "false"),
    # Кегль жёлтого слова: множитель hl_size_k. При 1.0 подстановка пуста, а кегль
    # в циклах субтитров остаётся прежним FONT_SIZE — .jsx прежний байт в байт
    # (golden_geometry.jsx). Считает его layout: выражения циклов и объявление
    # берутся из одного места.
    hl_size_decl=hl_size_decl(stv.hl_size_k),
    # Тень слов субтитров: числа — из plan_decor (SH_SUB_*) и оттуда же в план
    # (plan["shadows"]["sub"]), поэтому у .jsx и превью одна и та же тень.
    sh_op=SH_SUB_OP, sh_dir=SH_SUB_DIR, sh_dist=SH_SUB_DIST, sh_soft=SH_SUB_SOFT,
    ins_fx=_js(stv.insert_fx),
    # Радиус скругления маски фотовставки — ОДНО число на .jsx и план (превью):
    # раньше оно стояло константой в шаблоне, и у превью радиуса не было вовсе.
    # При 60 подстановка даёт ровно прежний текст шаблона — .jsx прежний байт в байт
    # (golden_geometry.jsx).
    ins_mask_r=("%g" % INS_MASK_R),
    # Задание FC: «none»-вставки без анимации и без эффектов. Подстановки при
    # дефолтах (zoom/card/white) дают ровно прежний текст шаблона — .jsx не меняется
    # (golden); при none — пусто: ни вызова insFX, ни маски, ни wiggle.
    insfx_cam1=("insFX(L,\"cam1\");" if stv.insert_fx != "none" else ""),
    insfx_cam2=("insFX(L,\"cam2\");" if stv.insert_fx != "none" else ""),
    ins_wiggle=(
        "try{ L.property(\"ADBE Transform Group\").property(\"ADBE Position\").expression=\"wiggle(1,15)\"; }catch(e){}  // лёгкое дрожание"
        if stv.insert_anim != "none" else ""),
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
        if stv.insert_fx != "none" else ""),
    ins_c1on2_x=stv.insert_c1on2_x,
    ins_c1on2_y=stv.insert_c1on2_y,
    # Подложка фото-вставок: дефолты — ровно тот текст, что был в шаблоне,
    # поэтому без единой вставки с галкой .jsx побайтово прежний (golden). Вставка с
    # галкой переопределяет их ниже — там же и объяснение формул.
    ins_plate_decl="",
    ins_plate_layer="",
    ins_photo_pos="[W/2, H/2]",
    ins_photo_scale="[_f*100,_f*100]",
    sub_loop=sub_loop,
    # Появление БАЗОВЫХ слов (пресет sub_anim): объявление чисел и функция
    # subAnimKeys. У выключенного пресета обе подстановки пусты — .jsx прежний
    # байт в байт (golden). Считает их layout: ключи плана и .jsx берутся из
    # одного места, второй копии кривых нет.
    sa_decl=_subs.sub_anim_decl,
    sa_fn=_subs.sub_anim_fn,
    # Градиент текста и свечение (класс Б каталога): функции эффектов — ОДНИ на
    # сборку, в циклах слов стоят только их вызовы. Выключено — пусто (golden).
    sub_fx_fn=(_subs.sub_fill_fn + _subs.sub_glow_fn),
    # Подложка слова: создание ОДНОГО шейп-слоя до цикла слов и хвост (окна показа
    # и кривые) после него. Выключена галкой — обе подстановки пусты (golden).
    sub_wbg_js=_subs.sub_wbg_js,
    sub_wbg_tail=_subs.sub_wbg_tail,
    sub_shadow_js=_decor.sub_shadow_js,
    sub_bg_js=_decor.sub_bg_js,
    layer_order=_jd(list(stv.layer_order)),
    sub_bg_null_anchor=("    nullAnchor = bgLayer;\n" if _decor.sub_bg_on else ""),
    sub_scale_js=_decor.sub_scale_js,
    top_line_js=_decor.top_line_js,
    caption_js=_decor.caption_js)

    # Подложка фото-вставок: подстановки непустые ТОЛЬКО когда файл в стиле задан и хоть
    # у одной вставки есть галка, — иначе .jsx побайтово прежний (golden). Текст
    # подстановок живёт в plate_tokens: второй копии формул нет.
    if any_plate:
        tokens.update(plate_tokens(any_plate, stv.plate_path, tokens))
    return AePlan(tokens=tokens)


def plate_tokens(any_plate: bool, plate_path: Any, ae: dict[str, Any]) -> dict[str, Any]:
    """Подстановки подложки фото-вставок при галке «на подложке» (пусто без неё).

    Возвращает ровно те четыре ключа, которые `scene_plan` дописывал в `plan["_ae"]`
    ПОСЛЕ его сборки, — второго текста подстановок нет. `ae` нужен только ради
    маски-скругления: она построена в общем словаре, и на подложке её надо снять.
    """
    out: dict[str, Any] = {}
    if not any_plate:
        return out
    # Подложка фото-вставок. Подстановки непустые ТОЛЬКО когда файл в стиле
    # задан и хоть у одной вставки есть галка — иначе .jsx побайтово прежний (golden).
    # Путь подложки уезжает в .jsx ОДИН раз (INS_PLATE в шапке), а решение «этой вставке
    # подложку» шаблон принимает по полю ins.plate: у остальных вставок прекомп, маска и
    # формулы те же, что были. Слой плашки добавляется ПЕРВЫМ (фото встанет поверх неё),
    # один импорт на весь .jsx (imp дедуплицирует). Масштаб фото и сдвиг внутри прекомпа
    # посчитал Python (_ins_plate): в .jsx едут готовые ins.ps/px/py, своих формул
    # шаблон не держит.
    out["ins_plate_decl"] = (
        "    var INS_PLATE = %s;   // подложка вставок с галкой «на подложке»: путь или пусто\n"
        % _js(plate_path))
    out["ins_plate_layer"] = (
        "        // подложка: слой ПЕРВЫМ в прекомпе, только у вставок с галкой «на подложке»\n"
        "        if(ins.plate && INS_PLATE){ var plateItem=imp(INS_PLATE);\n"
        "            if(plateItem){ toBin(plateItem,\"Вставки\");\n"
        "                var plateL=pc.layers.add(plateItem);\n"
        "                try{ plateL.property(\"ADBE Transform Group\").property(\"ADBE Position\").setValue([W/2,H/2]);\n"
        "                    var plateW=plateItem.width; if(plateW){ var plateF=W/plateW;\n"
        "                        plateL.property(\"ADBE Transform Group\").property(\"ADBE Scale\").setValue([plateF*100,plateF*100]); } }catch(e){} } }\n"
        "        ")
    out["ins_photo_pos"] = (
        "(ins.plate&&INS_PLATE)?[W/2+ins.px, H/2+ins.py]:[W/2, H/2]")
    out["ins_photo_scale"] = (
        "(ins.plate&&INS_PLATE)?[(ins.ps||100),(ins.ps||100)]:[_f*100,_f*100]")
    # маска-скругление — только НЕ на подложке; у остальных вставок она остаётся
    if ae.get("ins_mask"):
        out["ins_mask"] = (
            "if (!(ins.plate && INS_PLATE)) {\n"
            + "\n".join(("    " + _ln) if _ln.strip() else _ln
                        for _ln in ae["ins_mask"].split("\n"))
            + "\n        }")
    return out
