# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Словарь плана сцены `plan` (остаток распила scene_plan).

Модуль забирает из `scene_plan` сборку самого словаря плана: `plan["cams"]` с камерами,
зум Камеры 1, интро и затемнение под него, вставки и коробку их карточки, тень и свечение
субтитров и интро, слова, звук, дисклеймер, подпись, разметку рото, цвета, кегли, шрифты,
размытие на старте и точку покоя вставок Кам2. Поля-исключения (`sub_step`, `sub_bg`,
`sub_wbg`, градиент и свечение текста, `top_line`, `caption`, `disclaimer`) дописываются
ПОСЛЕ словаря — здесь же, тем же порядком, что был в `scene_plan`: их набор зависит от
галок и стиля, и при дефолтах ключа нет вовсе (.jsx и план прежние, golden).

Перенос ПОСТРОЧНЫЙ: поведение, числа, порядок ключей не менялись ни на байт
(проверяется эталоном fixtures/golden_geometry.jsx и побайтовым сравнением .jsx/плана).
Имена локальных переменных оставлены как в scene_plan — тело перенесено дословно, а входы
распаковываются в преамбуле.

Вход — один неизменяемый `SceneInputs`: готовые результаты соседних блоков (`plan_frame`,
`plan_inserts`, `plan_audio`, `plan_camera`, `plan_subs`, `plan_decor`, `plan_intro`) и
структура стиля, прочитанная один раз. Выход — один `ScenePlan` с ровно тем словарём,
который `scene_plan` возвращает вызывающим (`plan_ae` дописывает в него только `_ae`).
"""
from dataclasses import dataclass, field
from typing import Any

from core import voicefx  # noqa: F401  (контракт сборки: voicefx есть у build)

from .layout import (INTRO_F_DUR, SUB_GLITCH_BLUR, SUB_GLITCH_DUR,
                     SUB_GLITCH_END_KEYS, SUB_GLITCH_OP_KEYS, css_blur_px, _ins_box)
from .plan_audio import AudioPlan
from .plan_camera import CameraPlan
from .plan_decor import DecorPlan, shadows_plan
from .plan_frame import FramePlan
from .plan_inserts import InsertsPlan
from .plan_intro import IntroPlan
from .plan_style import StyleValues
from .plan_subs import SubsPlan


# Параметры анимаций интро (глитч, раскрытие): их читает превью браузера (план несёт
# `intro_anims`), а подстановки шаблона собирает plan_intro_tpl. Единый источник истины —
# вторая копия в JS не заводится. Таблица живёт здесь, потому что единственный её
# потребитель — словарь плана; сборка берёт её отсюда (импорт в build.py), это тот же объект.
INTRO_ANIMS: dict[str, Any] = {
    # f_dur — сколько играет появление обычной строки интро (F_DUR .jsx): фейд, масштаб,
    # up/left/right, РАСКРЫТИЕ и счётчик без своей анимации. Лежит здесь, потому что это
    # число той же таблицы: его читает превью (план несёт его как intro_anims.f_dur) —
    # своей копии у JS нет.
    "f_dur": INTRO_F_DUR,
    "glitch": {
        # Числа — из layout (SUB_GLITCH_*): ими же играет пресет появления субтитров
        # «глитч». Один источник на интро и на субтитры: вторая копия разошлась бы
        # молча, а разъехавшийся глитч виден только рендером.
        "dur": SUB_GLITCH_DUR,
        # сила Gaussian Blur на слое слова глитча (было 6.8, пользователь 2026-09-11: вдвое слабее)
        "blur": SUB_GLITCH_BLUR,
        "end_keys": [list(k) for k in SUB_GLITCH_END_KEYS],
        "op_keys": [list(k) for k in SUB_GLITCH_OP_KEYS],
    },
    "reveal": {
        # Длительность раскрытия — F_DUR .jsx (INTRO_F_DUR), а не длительность глитча:
        # ключи блюра, Scale слоя и Percent Offset селектора в introAnimFX стоят на
        # t0+F_DUR*SQ. Иначе поле читало бы превью и открывало слово позже AE.
        "dur": INTRO_F_DUR,
        "blur": 26.8,
        "scale": 0.7,
        "scale_3d": [11, 11, 91.66667],
        "shape": 2,
        "smoothness": 100,
        "ease": [10, 95],
    },
}


@dataclass(frozen=True)
class SceneInputs:
    """Вход сборки плана: всё, что `scene_plan` знает к моменту вызова.

    Поля названы как локальные переменные scene_plan. Готовые результаты соседних
    блоков приходят полями целиком (`frame`, `ins`, `au`, `cam`, `subs`, `decor`,
    `intro`) — второй копии их чисел здесь нет. `style` — структура стиля, прочитанная
    один раз (`plan_style.read_style`).
    """
    meta: dict
    frame: FramePlan
    ins: InsertsPlan
    au: AudioPlan
    cam: CameraPlan
    subs: SubsPlan
    decor: DecorPlan
    intro: IntroPlan
    style: StyleValues
    # Готовые числа соседних блоков и сборки: переход видеовставок и Lumetri камер
    # (его собирает scene_plan: стиль + экспозиция клипа) — модуль их не пересчитывает.
    trans_plan: Any
    lumetri: "dict[str, float] | None"
    fsize: Any
    fsize_base: Any
    sub_scale: Any
    font_ps: Any
    hl_font_ps: Any
    # Геометрия полосы субтитров и подстановки субтитров (имена как локальные в scene_plan).
    posy: Any
    hl_step: Any
    hl_rise: Any
    hl_dur: Any
    hl_blur_on: Any
    # Флаги стиля, посчитанные в scene_plan: тень и свечение слов интро, градиент и
    # свечение текста субтитров (по ним план несёт готовые числа для превью).
    intro_shadow_on: bool
    grad_on: bool
    glow_on: bool
    # Числа затемнения под интро (plan_shade.numbers; None — галка снята): ключ
    # `plan["shade"]` — тот же объект, что уехал в подстановку шаблона.
    shade_plan: Any
    # Разомкнута ли цепочка Lumetri камер (стиль lm2_link) и готовые значения Камеры 2:
    # ключ `plan["lumetri2"]` появляется ТОЛЬКО при разомкнутой — как было.
    lm2_link: Any
    lumetri2: "dict[str, float] | None"
    # Дописываемые после словаря поля: подложка слова, плашка, верхняя строка, подпись,
    # дисклеймер и режим строк — все они зависят от галок и стиля.
    sub_wbg_plan: Any
    sub_bg_plan: Any
    top_line_plan: Any
    caption_plan: Any
    disclaimer_plan: Any
    sub_step: Any
    sub_words_per_row: Any


@dataclass(frozen=True)
class ScenePlan:
    """Выход: сам словарь плана (поле `plan`).

    Поле объявлено с `field(default_factory=dict)` — `plan_ae` дописывает в него `_ae`,
    а `to_ae_full` копирует словарь себе; мутировать его дальше вправе вызывающий.
    """
    plan: dict[str, Any] = field(default_factory=dict)


def plan_scene(inp: SceneInputs) -> ScenePlan:
    """Собрать словарь плана сцены: чистая функция от `SceneInputs`.

    Ничего не читает с диска и не зовёт план_*: все числа и готовые строки приходят
    полями — второго чтения ключей стиля и второго подсчёта подстановок нет.
    """
    # ---- входы: имена ровно как в scene_plan (тело ниже перенесено дословно) ----
    meta = inp.meta
    stv = inp.style
    _frame_p, _ip, _au, _cam_p = inp.frame, inp.ins, inp.au, inp.cam
    _subs, _decor, _intro = inp.subs, inp.decor, inp.intro
    trans_plan, lumetri = inp.trans_plan, inp.lumetri
    _fsize, _fsize_base, sub_scale = inp.fsize, inp.fsize_base, inp.sub_scale
    font_ps, hl_font_ps = inp.font_ps, inp.hl_font_ps
    _posy, _hl_step, _hl_rise, _hl_dur = inp.posy, inp.hl_step, inp.hl_rise, inp.hl_dur
    hl_blur_on = inp.hl_blur_on
    intro_shadow_on = inp.intro_shadow_on
    _grad_on, _glow_on = inp.grad_on, inp.glow_on
    cams_plan = _frame_p.cams_plan
    inserts_plan, roto_plan = _ip.inserts, _cam_p.roto
    zoom_plan, audio = _cam_p.zoom, _au.audio
    shade_plan = inp.shade_plan
    subs_plan = _subs.subs
    lumetri2 = inp.lumetri2

    plan = {
    "fps": meta["fps"], "w": meta["w"], "h": meta["h"], "name": meta["name"],
    "dur": meta["dur"] / meta["fps"],
    "cams": cams_plan,
    # Камера 1: holds = тип интерполяции каждого ключа (1=HOLD, 0=BEZIER); keys = [кадр, %], опц. mode (drift);
    # ease = [in, out] на каждый ключ; fit = 100 — заполнение кадра уже
    # в ключах, поле оставлено ради превью: оно множит fit на ключ;
    # cx/cy — точка наезда в долях кадра: при наезде неподвижна она,
    # превью рисует её же как transformOrigin и центр масштабирования
    "zoom": zoom_plan,
    "intro": _intro.intro,
    # затемнение под интро: None при выключенной галке, иначе готовые
    # числа слоя-фигуры (x/y/scale/w/h/ox/oy/blur/op) — их же рисует предпросмотр
    "shade": shade_plan,
    # общий масштаб интро, в процентах как в стиле: превью множит на него
    # положение и размер блока; поля групп (dx/dy/ds/y) читает оно же — не переименовывать
    "intro_scale": float(stv.intro_scale),
    # интро едет с камерой: False — нулы интро и затемнение НЕ привязаны
    # к нулу Камеры 1. Числом из плана живёт предпросмотр (ipvIntroChild): при False
    # блок идёт в координатах кадра без зума/сдвига/слежения — второй копии правила нет.
    "intro_cam": stv.intro_cam,
    # то же для камеры 2: False — нул «интро на кам2» стоит в координатах кадра, и
    # превью (ipvIntroChild по on2) считает ту же ветку. Своя галка, не общая с
    # камерой 1: у стилей без ключа её значение дала миграция (styles.migrate_intro_cam2).
    "intro_cam2": stv.intro_cam2,
    # параметры анимаций интро: превью анимирует теми же числами,
    # что AE — вторая копия не заводится. f_dur — длительность появления строки
    # (F_DUR .jsx: ею играют ключи блюра, Scale слоя, Percent Offset селектора и
    # фейд): её читает превью, своей копии числа у него нет.
    "intro_anims": {
        "f_dur": INTRO_ANIMS["f_dur"],
        "glitch": {
            "dur": INTRO_ANIMS["glitch"]["dur"],
            "blur": INTRO_ANIMS["glitch"]["blur"],
            "end_keys": [list(k) for k in INTRO_ANIMS["glitch"]["end_keys"]],
            "op_keys": [list(k) for k in INTRO_ANIMS["glitch"]["op_keys"]],
        },
        "reveal": {
            "dur": INTRO_ANIMS["reveal"]["dur"],
            "blur": INTRO_ANIMS["reveal"]["blur"],
            # Раскрытие рисуется блюром CSS на слове, а число шаблона — «Blurriness»
            # Gaussian Blur в AE: превью нужна сигма того же размытия, иначе буквы
            # выходят вчетверо мягче собранных (число переводит layout.css_blur_px).
            "blur_css": css_blur_px(INTRO_ANIMS["reveal"]["blur"]),
            "scale": INTRO_ANIMS["reveal"]["scale"],
            "scale_3d": list(INTRO_ANIMS["reveal"]["scale_3d"]),
            "shape": INTRO_ANIMS["reveal"]["shape"],
            "smoothness": INTRO_ANIMS["reveal"]["smoothness"],
            "ease": list(INTRO_ANIMS["reveal"]["ease"]),
        },
    },
    "inserts": inserts_plan,
    # Переход видеовставок (Quick 2): файл, сдвиг TR_IN и размер исходника — те же
    # числа, что уехали в .jsx подстановками trans/tr_in. Превью рисует ИМИ слой
    # перехода: он начинается за TR_IN до стыка (tl.startTime=cut-TR_IN в шаблоне),
    # поэтому вход вставки виден РАНЬШЕ её start — как в AE. Нет видеовставок или
    # файла — поля нет вовсе, и превью не заводит ни элемента, ни правила.
    "trans": trans_plan,
    # Коробка карточки фотовставки (ширина, высоты Кам1/Кам2 и отношение сторон маски)
    # в пикселях кадра ролика: её читает превью — своей копии чисел (1030/528/2.2) у
    # него больше нет, в 4K-кадре она расходилась с собранной карточкой вдвое.
    # Считает layout._ins_box тем же правилом, что и геометрия вставок.
    "ins_box": _ins_box(meta["w"], meta["h"]),
    "layer_order": list(stv.layer_order),
    # Цвет камер через Lumetri: None при выключенной галке, иначе девять
    # значений стиля (exposure уже с экспозицией клипа). Их же читает превью —
    # второй копии правил нет: .jsx и предпросмотр берут один plan["lumetri"].
    "lumetri": lumetri,
    "subs": subs_plan,
    "sub_hide": _decor.sub_hide,
    # цвет базовых субтитров: [r,g,b] 0..1, превью красит тем же,
    # что AE — вторая копия не заводится. Жёлтые по-прежнему берут hl_fill.
    "sub_fill": list(stv.sub_fill) if stv.sub_fill else [1, 1, 1],
    # цвет выделения субтитров: [r,g,b] 0..1, превью красит тем же,
    # что AE — вторая копия не заводится.
    "hl_fill": list(stv.hl_fill) if stv.hl_fill else [1, 0.9176, 0],
    # цвета интро для предпросмотра:
    "hl_fill3": list(stv.hl_fill3) if stv.hl_fill3 else [0.6863, 0.1216, 0.1216],
    "intro_fill": list(stv.intro_fill) if stv.intro_fill else None,
    "intro_hl_fill": list(stv.intro_hl_fill) if stv.intro_hl_fill else None,
    # Тень и свечение слов интро для предпросмотра: те же галки и числа,
    # что уехали в подстановки шаблона, — превью рисует по ним (ipvIntro), второго
    # чтения ключей стиля во фронте нет, как у цветов выше. Цвет/прозрачность тени
    # прекомпа у каждой группы свои и лежат в plan.intro[].shadow, а общие для обеих
    # камер направление/дистанция/мягкость — здесь.
    "intro_word_fx": {
        "shadow_all": intro_shadow_on,
        "shadow_glitch": stv.intro_glitch_shadow, "shadow_back": stv.intro_back_shadow,
        "glow_glitch": stv.intro_glitch_glow, "glow_fx": stv.intro_fx_glow,
        # Свечение жёлтого хайлайта и слоя прекомпа (задание «glowfix») — те же галки,
        # что уехали в подстановки шаблона: превью гасит их по плану, второго чтения
        # ключей стиля во фронте нет.
        "glow_hl": stv.intro_hl_glow, "glow_comp": stv.intro_comp_glow,
        "glow_thr": stv.intro_word_glow_thr, "glow_rad": stv.intro_word_glow_rad,
        "glow_int": stv.intro_word_glow_int,
    },
    "intro_comp_shadow": {"dir": stv.intro_comp_shadow_dir,
                          "dist": stv.intro_comp_shadow_dist,
                          "soft": stv.intro_comp_shadow_soft},
    # Тени AE (Drop Shadow) для превью: субтитры, вставки и плашка под субтитрами.
    # Числа — те же, что уезжают подстановками в .jsx (считает plan_decor.shadows_plan):
    # превью переводит их в CSS своей единственной дверью aeShadowCss, своих чисел
    # тени у фронта нет. op255 — шкала AE 0..255 (в .jsx проценты ручки умножаются
    # на 255/100), color — [r,g,b] 0..1, как у остальных цветов плана.
    "shadows": shadows_plan(),
    "back_scale": stv.back_scale,
    "back_step": stv.back_step,
    # Шаг от заднего плана к обычной строке для предпросмотра: None —
    # ключа в стиле нет, раскладка взяла back_step (превью читает готовые ys).
    "back_step_after": stv.back_step_after,
    # Разметка рото: готовые фрагменты из plan_camera.py — маски по ним
    # делает to_ae_full, предпросмотр читает их же для полосы «здесь рото».
    "roto": roto_plan,
    # Звук плана: словарь собирается там же, где считаются его числа —
    # plan_audio.py. Голос, музыка, окна цензуры и события SFX — из одного места,
    # второй копии у .jsx и предпросмотра нет.
    "audio": audio,
    # стопка субтитров и кегль — для отрисовки в предпросмотре (тот же источник, что _ae)
    # intro_fsize — кегль интро (до ужатия строк, доработка ZL): превью рисует им
    # интро, fsize (ужатым) — субтитры; в режиме по слову числа равны.
    "posy": _posy, "hl_step": _hl_step, "hl_rise": _hl_rise, "fsize": _fsize,
    # Анимация жёлтых в строках для предпросмотра: время появления — у
    # самого слова (words[].t0), остальные числа — плоскими полями рядом с hl_rise/
    # hl_step: превью не заводит своей копии ни одного числа. hl_dur — то же 0.35 с,
    # что литералом HL_DUR в шаблоне, hl_row_anim — режим (word/row).
    "hl_dur": _hl_dur, "hl_row_anim": stv.hl_row_anim,
    "hl_blur": hl_blur_on, "hl_blur_amt": stv.hl_blur_amt,
    # Тот же блюр, переведённый в пиксели CSS: превью рисует размытие фильтром
    # браузера, а «Blurriness» AE и сигма blur() — разные числа (layout.css_blur_px).
    # Число считается ОДИН раз здесь: своей копии перевода у превью нет, а .jsx
    # по-прежнему получает само значение стиля — AE читает его как «Blurriness».
    "hl_blur_css": css_blur_px(stv.hl_blur_amt),
    # Кегль жёлтого слова: множитель базового (ручка hl_size_k). Превью рисует
    # жёлтый спан тем же кеглем, что .jsx ставит слово, — своей копии числа нет.
    "hl_size_k": _subs.hl_size_k,
    # Тонкое начертание пресета «начертание» (sub_anim_font): по нему превью
    # ступенит шрифт слова в той же середине появления, что .jsx (поле anim слова).
    "sub_anim_font": _subs.sub_anim_font,
    # Шрифты субтитров: те же PostScript-имена, что уезжают в .jsx (FONT/HL_FONT,
    # лесенка стиля — sub_font, выделение — hl_font). Превью берёт их ОТСЮДА, а не из
    # своей копии стиля: страница рендера получает тело сборки, где стиль может быть
    # и ИМЕНЕМ (строкой) — тогда CURSTYLE это строка, и субтитры рисовались запасным
    # шрифтом, хотя .jsx собрал заказанный (замер: «КУБИК» 344 px против 284 в AE).
    "sub_font": font_ps, "sub_hl_font": hl_font_ps,
    "intro_fsize": _fsize_base,
    # масштаб слоя прекомпа субтитров: превью рисует transform: scale()
    # с origin в posy — то же число, что уходит в Scale в .jsx
    "sub_scale": sub_scale,
    # тень субтитров: выключается при sub_bg
    "sub_shadow": _decor.sub_shadow,
    # размытие на старте: превью рисует CSS-фильтр с той же кривой;
    # 0 = выключено, план тогда несёт ноль и превью фильтр не вешает
    "start_blur": stv.start_blur, "start_blur_dur": stv.start_blur_dur,
    # точка покоя cam2-вставки (уезжает в стиль insert_c2_x/y, долями кадра)
    "ins_c2x": round(meta["w"] * stv.insert_c2_x),
    "ins_c2y": round(meta["h"] * stv.insert_c2_y),
    # сдвиг интро по горизонтали, px (пара к intro_y)
    "intro_x": round(float(stv.intro_x)),
}
    if not inp.lm2_link:
        plan["lumetri2"] = lumetri2
    if inp.sub_bg_plan:
        plan["sub_bg"] = inp.sub_bg_plan
    # Подложка слова (класс Б каталога): числа фигуры — превью ставит ею ОДИН элемент
    # за текущим словом; момент слова лежит в самом слове плана (поле wbg). Выключена —
    # поля нет вовсе, и превью не заводит ни элемента, ни правила.
    if inp.sub_wbg_plan:
        plan["sub_wbg"] = inp.sub_wbg_plan
    # Заливка текста градиентом: два цвета и угол — те же числа, что уехали в .jsx
    # (эффект ADBE Ramp на слое слова); превью рисует их background-clip:text.
    # Имя поля — sub_grad, а не sub_fill: sub_fill в плане уже занят цветом субтитров.
    if _grad_on:
        plan["sub_grad"] = {"mode": "gradient", "from": list(stv.sub_grad_from),
                            "to": list(stv.sub_grad_to), "angle": stv.sub_grad_angle}
    # Свечение текста: сила, радиус и цвет — те же числа, что у Glo2 в .jsx; превью
    # приближает свечение цветной тенью (CSS-аналога Glo2 нет).
    if _glow_on:
        plan["sub_glow"] = {"amt": stv.sub_glow_amt, "rad": stv.sub_glow_rad,
                            "fill": list(stv.sub_glow_fill), "yellow": bool(stv.sub_glow_yellow)}
    if inp.top_line_plan:
        plan["top_line"] = inp.top_line_plan
    if inp.caption_plan:
        plan["caption"] = inp.caption_plan
    # Дисклеймер: строки, шрифт, кегль, положение и время хвостовой копии — те же числа,
    # что уехали подстановками в .jsx, вторым чтением ключей стиля превью не живёт.
    # Текста нет (пустая строка = скрыт) — ключа нет вовсе.
    if inp.disclaimer_plan:
        plan["disclaimer"] = inp.disclaimer_plan
    if inp.sub_words_per_row > 1:
        plan["sub_step"] = inp.sub_step
    return ScenePlan(plan=plan)
