# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Стиль, прочитанный ОДИН раз: `read_style(st)` -> `StyleValues`.

До этого задания `scene_plan` читал ключи стиля 79 локальными присваиваниями и потом
перечислял те же значения поимённо в аргументах шести модулей `plan_*`: каждая новая
ручка правилась в трёх местах (`styles.BASE`, чтение в `scene_plan`, аргумент модуля).
Теперь чтение одно: `scene_plan` зовёт `read_style(st)` и дальше берёт значения из
неизменяемой структуры, а модули получают её ОДНИМ полем `style=`.

Правила, по которым собран модуль:

* Читаем ровно теми же обёртками, что читал `scene_plan`, — `_sv` (None = «не задано»)
  и `_sv_or` (ноль и пустая строка тоже «не задано»); запас обоих — `styles.BASE`,
  своих литералов здесь нет (сторож «дефолты только в BASE» — `test_build_style_defaults`).
  Ключи, которые `scene_plan` читал сырым `st.get`, читаются сырым `st.get`: у них своя
  семантика («None = стиль молчит»), и подмена её на `_sv` поменяла бы поведение.
* Имена полей — имена локальных переменных `scene_plan` (так видно, что перенос
  построчный). Ведущее подчёркивание локальной переменной снято: это была пометка
  «служебное» у переменной внутри функции, а поле структуры публичное — и у половины
  из них ровно под этим именем (без подчёркивания) они и уезжали в модули
  (`plate_path`, `insert_anim`, `fit_w`, ...). Единственное исключение — `_G`:
  в `intro_scale_k` она называется так, как её звал `plan_intro`.
* Производные (приведение типа, деление на 100, `strip`, `max(1, ...)`) считаются
  ЗДЕСЬ, если зависят только от стиля: они считались один раз и раньше, а теперь их
  читают и `scene_plan`, и модули — второй копии формулы быть не должно.
  Производные, которым нужны другие входы (`_posy` — высота кадра, `lumetri` —
  экспозиция клипа, `_yellow_dark` — правило `_tritone_on` из build.py), остались в
  `scene_plan`.
"""
from dataclasses import dataclass
from typing import Any, Mapping

from core import style_geometry
from core import styles as _styles


def _sv(st: Mapping[str, Any], key: str) -> Any:
    """Значение ключа стиля; ключа нет (None) — дефолт из styles.BASE.

    Раньше запасное число стояло рядом с КАЖДЫМ чтением (`st.get("sub_bg_op") if
    st.get("sub_bg_op") is not None else 72.0`), и правка дефолта в styles.BASE до
    сборки не доезжала: в BASE новое число, в .jsx старое. Источник дефолтов один —
    styles.BASE. Форма с `is not None`: ноль и пустая строка — ЗАДАННЫЕ значения.
    """
    v = st.get(key)
    return _styles.BASE[key] if v is None else v


def _sv_or(st: Mapping[str, Any], key: str) -> Any:
    """Значение ключа стиля; пусто/ноль — дефолт из styles.BASE.

    Форма `st.get(k) or <число/строка>`: ноль, пустая строка и None означают «не
    задано» ровно как раньше, но запас берётся из styles.BASE, а не из литерала
    рядом с чтением.
    """
    return st.get(key) or _styles.BASE[key]


@dataclass(frozen=True)
class StyleValues:
    """Все значения стиля, которые нужны сборке, — прочитанные один раз.

    Поля сгруппированы по блокам `scene_plan` (общее, субтитры, интро, вставки,
    камера, звук, палитра камер) — в том же порядке, в каком ключи читались в его
    теле. Тип `object` там, где значение уезжает в план/шаблон как есть (None —
    «ключа нет, подстановка пустая») и приведение живёт у потребителя, как жило.
    """
    # --- общее: дисклеймер и ризер интро (стиль перебивает kwarg сборки) ---
    disclaimer: Any          # st.get: None — стиль молчит, "" — скрыть дисклеймер
    disclaimer_end: Any      # галка «копия дисклеймера в конце ролика»
    disc_gap: Any            # зазор строк дисклеймера, px; None — интервал авто
    disc_scale: float        # масштаб дисклеймера, % (множитель подобранного кегля)
    disc_y: float            # положение блока по высоте, % кадра (базовая линия 1-й строки)
    disc_dx: float           # сдвиг дисклеймера по горизонтали от центра кадра, px
    intro_riser: Any         # st.get: None — галка как в kwarg, иначе стиль сильнее
    intro_riser_file: Any    # свой файл ризера; None = ассет по умолчанию
    # --- шрифты и регистры (сырые значения: лесенку «пусто = как базовый» собирает
    # scene_plan — шрифты остаются отдельными входами модулей) ---
    font: Any
    hl_font: Any             # None = как font
    intro_font: Any          # None = как font (субтитры)
    intro_hl_font: Any       # None = как hl_font
    accent_font: Any
    accent_case: Any
    back_font: Any
    back_case: Any
    # --- субтитры ---
    sub_case: str                          # strip: регистр субтитров
    sub_y: Any                             # доля высоты кадра; px считает план
    sub_scale: float                       # масштаб слоя прекомпа субтитров, %
    sub_words_per_row: int                 # max(1, …): слов в строке
    sub_rows_max: int                      # max(1, …): строк в реплике
    sub_fill: Any                          # цвет базовых субтитров; None = белый
    # Появление БАЗОВЫХ (белых) слов: пресет и его две ручки. Ключи по ним считает
    # layout (единственный источник кривых), .jsx и превью читают готовые ключи плана.
    # sub_anim_dur = 0 и sub_anim_amt = None значат «как задумано пресетом».
    sub_anim: str
    sub_anim_dur: float
    sub_anim_amt: Any                      # None = сила пресета; иначе px (rise) / % (pop)
    sub_anim_font: Any                     # тонкое начертание пресета weight; None = только прозрачность
    hl_fill: Any                           # цвет жёлтого; None = стоковый жёлтый
    hl_fill3: Any                          # третий цвет (color=="accent"); None = дефолт
    hl_bold: Any                           # fauxBold на выделенных словах
    hl_size_k: float                       # кегль жёлтого слова множителем базового: 1.0 = как сегодня
    hl_row_anim: Any                       # появление жёлтых в строке: word | row
    hl_blur: bool
    hl_blur_amt: float
    hl_row_stack: bool                     # подряд жёлтые — стопкой
    # плашка под субтитрами: ключи читаются всегда, ставятся по галке sub_bg
    sub_bg: Any
    sub_bg_fill: list
    sub_bg_op: float
    sub_bg_h: float
    sub_bg_round: float
    sub_bg_pad: float
    sub_bg_padmin: float
    sub_bg_dy: float
    sub_bg_anim: float
    # Подложка слова, заливка текста и свечение (класс Б каталога): фигура и эффекты
    # на словах субтитров. Все ключи читаются всегда, ставятся по своим галкам/режимам.
    sub_wbg_on: Any                        # галка «Подложка слова»
    sub_wbg_kind: str                      # highlight (маркер раскрывается) | pill (таблетка)
    sub_wbg_fill: list
    sub_wbg_op: float
    sub_wbg_h: float
    sub_wbg_round: float
    sub_wbg_pad: float
    sub_wbg_dy: float
    sub_wbg_sweep: float
    sub_fill_mode: str                     # solid (цвет субтитров) | gradient
    sub_grad_from: list
    sub_grad_to: list
    sub_grad_angle: float
    sub_glow_on: Any                       # галка «Свечение текста»
    sub_glow_yellow: bool                  # свечение только на жёлтых словах
    sub_glow_amt: float
    sub_glow_rad: float
    sub_glow_fill: list
    # верхняя строка-прогресс
    top_line: Any
    top_line_y: float
    top_line_w: float
    top_line_th: float
    top_line_from: list
    top_line_to: list
    top_line_track_fill: list
    top_line_track_op: float
    # подпись о ролике
    caption: Any
    caption_case: Any
    caption_font: Any
    caption_size: float
    caption_fill: list
    caption_x: float
    caption_y: float
    caption_bg: bool
    caption_bg_fill: list
    caption_bg_op: float
    caption_bg_round: float
    caption_kx: float
    caption_ky: float
    # --- интро: положение, масштаб, раскладка ---
    intro_scale: Any
    intro_scale_k: float                   # бывшее _G: intro_scale / 100
    intro_x: Any                           # сдвиг интро по X, px
    intro_y: Any
    intro_y2: Any
    intro_cam: bool                        # «интро едет с камерой» (камера 1)
    intro_cam2: bool                       # то же для нула «интро на кам2» (камера 2)
    fit_w: float                           # камера 1: 1 − 2·intro_margin/100 (доля ширины кадра)
    fit_w2: float                          # камера 2: то же из intro_margin2
    fit_max: float                         # камера 1: потолок увеличения (intro_fit_max), %
    fit_max2: float                        # камера 2: свой потолок (intro_fit_max2), %
    # Межстрочный интервал интро: ОДИН множитель k на оба места — шаги строк
    # и центровку блока в Python (intro_line_ys, _intro_i_dy) и var LINE_STEP в шаблоне.
    # 100 = прежние 160 px: подстановка печатает ровно «160», .jsx прежний (golden).
    line_step_k: float                     # бывшее _line_step_k: межстрочный, доля
    # Межстрочный СТОПКИ группы с большой строкой (доработка ZY-2): свой множитель шага,
    # % от тех же 160 px. От общей ручки не зависит: у эталона владельца стопка плотнее,
    # а общий межстрочный двигает остальные группы.
    big_step_k: float                      # бывшее _big_step_k: шаг стопки большой строки
    back_step: float
    # Шаг ПОСЛЕ блока заднего плана: своя ручка, ключа в стиле может не быть
    # вовсе — тогда None, и раскладка берёт back_step (старые стили прежние байт в байт).
    # Форма с `is not None`: ноль — ЗАДАННОЕ значение, как у прочих чтений через _sv.
    back_step_after: Any                   # None — ключа нет, раскладка берёт back_step
    back_scale: float                      # кегль строки заднего плана, доля
    # Фейд-аут прекомпа интро: единый ключ стиля intro_fade (дефолт 0.35).
    intro_fade: float
    intro_fx_hold_add: float
    # Интро гаснет к субтитру, если стоит на его месте: галка и длительность
    # этого затухания. Дефолт галки True — так собраны ролики владельца (69 из 76 групп
    # гаснут ровно в момент появления следующего субтитра). Галка снята или полосы
    # субтитров блок не касается — окно группы прежнее, .jsx прежний байт в байт (golden).
    intro_sub_cut: bool                    # гаснет к появлению субтитра
    intro_sub_fade: float
    intro_scale_anchor: str                # comp | first (точка масштабирования прекомпа)
    # То же для интро, попавшего на перебивку: у кадра камеры 2 своя раскладка, и точку
    # масштабирования его прекомпов держат отдельно от камеры 1.
    intro_scale_anchor2: str
    intro_anchor: Any                      # center | first
    intro_anchor2: Any                     # то же для групп на перебивке
    intro_big_gap: float                   # зазор «большое слева»
    intro_big_over: float
    intro_roto_by_pos: Any                 # интро над рото по положению (камера 1)
    intro_roto_by_pos2: Any                # …и для групп на перебивке (камера 2)
    intro_shade: Any                       # затемнение под интро
    intro_shade_op: float
    intro_glow: float
    # цвета текста интро (None = сегодняшнее поведение, подстановка пустая)
    intro_fill: Any
    intro_hl_fill: Any
    # Тень и свечение слов интро — отдельные рычаги. Раньше решение «ставить
    # эффект» было жёстким: глитч и строки заднего плана — всегда с тенью, глитч и строка
    # с fx=="glow" — всегда с Glo2. Дефолты (галки True, числа 149/77/0.62) не меняют .jsx
    # ни на байт: подстановки шаблона печатают ровно прежний текст (golden).
    intro_shadow: Any
    intro_shadow_op: float
    intro_shadow_dir: float
    intro_shadow_dist: float
    intro_shadow_soft: float
    back_shadow_op: float
    back_shadow_soft: float
    intro_glitch_shadow: bool
    intro_back_shadow: bool
    intro_glitch_glow: bool
    intro_fx_glow: bool
    # Свечение accent-строк: единая галка стиля — свечение ставят ВСЕ accent-строки
    # (или ни одна). Решает её план в _intro_line_js (plan_intro.py); поле fx строки
    # не читается вовсе: у отдельной строки свечение больше не выбирается, а «снято
    # руками» и «не задано» в нём неразличимы (решение владельца 02.10.2026).
    intro_accent_glow: bool
    # Ещё две двери свечения интро (задание «glowfix»): свечение Glo2 на жёлтом слове
    # хайлайта (introHlGlow) и на слое прекомпа группы. Галок у них не было вовсе —
    # свечение ставилось мимо стиля, и снять его пересборкой было нельзя. Читаются здесь
    # же, где остальные ключи свечения; дефолты True: .jsx без этих ключей собирается
    # байт в байт как раньше (golden).
    intro_hl_glow: bool
    intro_comp_glow: bool
    intro_word_glow_thr: float
    intro_word_glow_rad: float
    intro_word_glow_int: float
    dg_with_glow: bool                     # бывшее _dg_with_glow: Deep Glow вместе со свечением
    # Тень ПРЕКОМПА интро: у камеры 1 и камеры 2 свои цвет/непрозрачность
    # (ключи стиля intro_comp_shadow*). Направление/дистанция/мягкость — общие у обеих
    # камер. Непрозрачность приходит из стиля В ПРОЦЕНТАХ (0..100) и переводится здесь
    # в сырые 0..255 AE — это ЕДИНСТВЕННОЕ место пересчёта, второй копии формулы нет
    # ни в шаблоне, ни в превью (превью читает готовое число из плана). Дефолт 26.7 %
    # даёт 68.1 из 255: шаблон считает это прежней подстановкой dropShadow(iL, 68)
    # по допуску (см. plan_intro_tpl), и .jsx остаётся прежним байт в байт (golden).
    intro_comp_shadow_fill: list
    intro_comp_shadow_op: float            # …непрозрачность в 0..255, посчитана из процентов
    intro_comp_shadow2_fill: list
    intro_comp_shadow2_op: float           # …то же для камеры 2
    intro_comp_shadow_dir: float
    intro_comp_shadow_dist: float
    intro_comp_shadow_soft: float
    # --- вставки ---
    insert_anim: str                       # strip: zoom | rise | none
    insert_style: Any                      # auto | cam1 | cam2
    insert_snap_cut: bool
    insert_snap_start: float               # st.get(…, 0.35): ключа нет в BASE, форма прежняя
    insert_sub_swap: bool                  # субтитры уходят на rise-вставках
    insert_fx: Any                         # card | white | none
    insert_video_front: Any                # ст. ключ, мигрирует в layer_order; форма прежняя
    insert_c1_x: float
    insert_c1_y: float
    insert_c2_x: float
    insert_c2_y: float
    insert_c1on2_x: float
    insert_c1on2_y: float
    plate_path: str                        # бывшее _plate_path: insert_plate_file, strip
    plate_scale: float                     # бывшее _plate_scale: 0 -> 100.0
    # --- камера (читает plan_camera, scene_plan ключей не знает) ---
    cam1_zoom: Any                         # pulse | jump | drift | none
    cam1_zoom_start: Any
    cam1_zoom_big: float
    cam1_zoom_lo: float
    cam1_zoom_hi: float
    cam1_drift_lo: float
    cam1_drift_hi: float
    cam1_take_zoom: bool
    cam1_take_min: float
    cam1_take_lo: float
    cam1_take_hi: float
    cam1_take_hold: float
    cam1_take_out: float
    cam1_take_yellow_mode: str
    cam1_take_yellow: bool
    cam1_yellow_zoom: bool
    # Наезд только на САМЫЕ СИЛЬНЫЕ жёлтые (core/emphasis.py): сила слова — эмоция
    # фразы + ударение по звуку. Дефолт True: ключа в старом стиле нет, а правило
    # владелец просил включённым (решение 02.10.2026).
    cam1_yellow_zoom_strong: bool
    # Ручки правила силы жёлтых (общие для обеих камер): способ оценки, порог силы,
    # предел наездов на кусок и порог длины для второго наезда. Читает plan_camera.
    hl_zoom_strength: str
    hl_zoom_min_pct: float
    hl_zoom_max_per_piece: int
    hl_zoom_second_min_s: float
    cam1_fit: float
    cam1_zoom_cx: float
    cam1_zoom_cy: float
    # Зум Камеры 2: свой независимый набор настроек зума и своя точка наезда
    cam2_zoom: Any                         # pulse | jump | drift | none
    cam2_zoom_start: Any
    cam2_zoom_big: float
    cam2_zoom_lo: float
    cam2_zoom_hi: float
    cam2_drift_lo: float
    cam2_drift_hi: float
    cam2_take_zoom: bool
    cam2_take_min: float
    cam2_take_lo: float
    cam2_take_hi: float
    cam2_take_hold: float
    cam2_take_out: float
    cam2_take_yellow_mode: str
    cam2_take_yellow: bool
    cam2_yellow_zoom: bool
    cam2_yellow_zoom_strong: bool
    cam2_fit: float
    cam2_zoom_cx: float
    cam2_zoom_cy: float
    cam2_pan_x: float
    cam2_pan_y: float
    cam2_rot: float
    cam2_head_follow: Any                  # слежение за головой
    cam2_head_x: float
    cam2_head_smooth: float
    cam2_head_min: float
    cam1_pan_x: float
    cam1_pan_y: float
    cam1_rot: float
    cam1_head_follow: Any                  # слежение за головой
    cam1_head_x: float
    cam1_head_smooth: float
    cam1_head_min: float
    roto_cam1_only: Any                    # рото только на кусках Камеры 1
    # --- звук ---
    glitch_db: float
    voice_db: float
    audio_fades: bool
    pop: Any                               # файл/ключ ассета «попа»; None = ассет
    glitch: Any
    transition: Any
    transition_sfx: Any
    # Обрезка/точка удара/громкость звуков (<звук>_in/_out/_at/_db): ключи читаются
    # СЫРЫМИ, как читал их _sfx_cfg: правило «не задано -> 0/None» и базовые громкости
    # (base/def_out) — арифметика звука, она осталась в plan_audio.py.
    pop_in: Any
    pop_out: Any
    pop_at: Any
    pop_db: Any
    transition_sfx_in: Any
    transition_sfx_out: Any
    transition_sfx_at: Any
    transition_sfx_db: Any
    intro_riser_in: Any
    intro_riser_out: Any
    intro_riser_at: Any
    intro_riser_db: Any
    transition_in: Any
    transition_out: Any
    transition_at: Any
    transition_db: Any
    pop_lead: Any
    # --- палитра камер через Lumetri: галка и девять чисел ---
    lm_on: Any
    lm_exposure: float
    lm_contrast: float
    lm_highlights: float
    lm_shadows: float
    lm_whites: float
    lm_blacks: float
    lm_temp: float
    lm_tint: float
    lm_sat: float
    # --- палитра Камеры 2 через Lumetri: цепочка связи, галка и девять чисел ---
    lm2_link: bool
    lm2_on: Any
    lm2_exposure: float
    lm2_contrast: float
    lm2_highlights: float
    lm2_shadows: float
    lm2_whites: float
    lm2_blacks: float
    lm2_temp: float
    lm2_tint: float
    lm2_sat: float
    # --- порядок слоёв и размытие на старте ---
    layer_order: Any
    start_blur: float
    start_blur_dur: float


def read_style(st: Mapping[str, Any], w: Any = None, h: Any = None) -> StyleValues:
    """Прочитать стиль один раз и отдать структурой.

    `st` — уже резолвнутый стиль (`styles.resolve(style)`): резолв остаётся у вызывающего,
    потому что им же перебиваются kwarg-и сборки (дисклеймер, ризер). Порядок чтений —
    порядок блоков `scene_plan`: так диф переноса читается построчно, а порядок значений
    в структуре совпадает с порядком полей.

    `w`/`h` — кадр ролика (`core/frame.ensure_frame`). Стиль задуман
    в кадре 1080×1920, поэтому здесь он ОДИН раз пересчитывается под кадр плана
    (`core/style_geometry.scale_style`): дальше и .jsx, и превью, и черновик берут
    готовые числа, второй копии правила нет. Кадр не задан или равен базовому —
    стиль читается как есть (эталоны .jsx байт в байт).
    """
    if w is not None and h is not None:
        st = style_geometry.scale_style(st, w, h)
    _back_step_after = _sv(st, "back_step_after")
    # Доля ширины кадра для автофита интро: ручки «Отступ от краёв» у КАЖДОЙ камеры
    # (intro_margin/intro_margin2, % с каждой стороны), доля = 1 − 2·margin/100.
    # Одно правило на оба режима: и привязанное (только ужатие), и откреплённое
    # (в обе стороны). Сторож диапазона — сама формула: margin 30 % даёт 40 % ширины,
    # margin 0 — всю ширину кадра.
    _fit1 = 1.0 - 2.0 * float(_sv(st, "intro_margin")) / 100.0
    _fit2 = 1.0 - 2.0 * float(_sv(st, "intro_margin2")) / 100.0
    _plate_path = str(_sv_or(st, "insert_plate_file") or "").strip()
    _plate_scale = float(_sv_or(st, "insert_plate_scale")) or 100.0
    _insert_anim = (_sv_or(st, "insert_anim")).strip()
    _intro_scale = _sv_or(st, "intro_scale")
    return StyleValues(
        # --- общее ---
        disclaimer=st.get("disclaimer"),
        disclaimer_end=st.get("disclaimer_end"),
        disc_gap=st.get("disc_gap"),
        # Ручки дисклеймера: пусто/ноль — «не задано», запас из styles.BASE. Ноль у
        # disc_dx и disc_scale — ЗАДАННЫЕ значения, но оба совпадают с запасом BASE
        # (0 и 100), поэтому _sv_or здесь ничего не меняет; у disc_y ноль осмыслен
        # (прижать к верху кадра) — значит _sv, а не _sv_or.
        disc_scale=float(_sv_or(st, "disc_scale")),
        disc_y=float(_sv(st, "disc_y")),
        disc_dx=float(_sv_or(st, "disc_dx")),
        intro_riser=st.get("intro_riser"),
        intro_riser_file=st.get("intro_riser_file"),
        # --- шрифты и регистры (сырые: лесенку собирает scene_plan) ---
        font=_sv_or(st, "font"),
        hl_font=st.get("hl_font"),
        intro_font=st.get("intro_font"),
        intro_hl_font=st.get("intro_hl_font"),
        accent_font=_sv_or(st, "accent_font"),
        accent_case=_sv_or(st, "accent_case"),
        back_font=_sv_or(st, "back_font"),
        back_case=_sv_or(st, "back_case"),
        # --- субтитры ---
        sub_case=(_sv_or(st, "sub_case")).strip(),
        sub_y=_sv_or(st, "sub_y"),
        sub_scale=float(_sv(st, "sub_scale")),
        sub_words_per_row=max(1, int(_sv_or(st, "sub_words_per_row"))),
        sub_rows_max=max(1, int(_sv_or(st, "sub_rows_max"))),
        sub_fill=st.get("sub_fill"),
        # Пресет появления базовых слов и его ручки: строка пресета — как hl_row_anim
        # (strip: в стиле её правят руками), амплитуда читается СЫРОЙ — None значит
        # «как задумано пресетом», и это не то же самое, что ноль.
        sub_anim=str(_sv(st, "sub_anim")).strip(),
        sub_anim_dur=float(_sv(st, "sub_anim_dur")),
        sub_anim_amt=st.get("sub_anim_amt"),
        # Тонкое начертание читается СЫРЫМ st.get, как hl_font: None — «своего нет»,
        # и это не то же самое, что пустая строка. Ручку читает только пресет weight.
        sub_anim_font=st.get("sub_anim_font"),
        hl_fill=st.get("hl_fill"),
        hl_fill3=st.get("hl_fill3"),
        hl_bold=st.get("hl_bold"),
        # Множитель кегля жёлтого слова: ноль/пусто — «не задано» (кегль базовый),
        # мусор в стиле тоже даёт 1.0 — решает layout.hl_size_factor.
        hl_size_k=float(_sv_or(st, "hl_size_k")),
        hl_row_anim=_sv(st, "hl_row_anim"),
        hl_blur=bool(_sv(st, "hl_blur")),
        hl_blur_amt=float(_sv(st, "hl_blur_amt")),
        hl_row_stack=bool(_sv(st, "hl_row_stack")),
        sub_bg=st.get("sub_bg"),
        sub_bg_fill=list(_sv(st, "sub_bg_fill")),
        sub_bg_op=float(_sv(st, "sub_bg_op")),
        sub_bg_h=float(_sv(st, "sub_bg_h")),
        sub_bg_round=float(_sv(st, "sub_bg_round")),
        sub_bg_pad=float(_sv(st, "sub_bg_pad")),
        sub_bg_padmin=float(_sv(st, "sub_bg_padmin")),
        sub_bg_dy=float(_sv(st, "sub_bg_dy")),
        sub_bg_anim=float(_sv(st, "sub_bg_anim")),
        # Подложка слова: вид фигуры — строка как hl_row_anim (strip: стиль правят руками),
        # остальное — числа. Галка читается СЫРОЙ, как sub_bg: «не задано» = выключено.
        sub_wbg_on=st.get("sub_wbg_on"),
        sub_wbg_kind=str(_sv_or(st, "sub_wbg_kind")).strip().lower(),
        sub_wbg_fill=list(_sv(st, "sub_wbg_fill")),
        sub_wbg_op=float(_sv(st, "sub_wbg_op")),
        sub_wbg_h=float(_sv(st, "sub_wbg_h")),
        sub_wbg_round=float(_sv(st, "sub_wbg_round")),
        sub_wbg_pad=float(_sv(st, "sub_wbg_pad")),
        sub_wbg_dy=float(_sv(st, "sub_wbg_dy")),
        sub_wbg_sweep=float(_sv(st, "sub_wbg_sweep")),
        # Заливка текста и свечение: режим — та же строка со strip, цвета и числа как есть.
        sub_fill_mode=str(_sv_or(st, "sub_fill_mode")).strip().lower(),
        sub_grad_from=list(_sv(st, "sub_grad_from")),
        sub_grad_to=list(_sv(st, "sub_grad_to")),
        sub_grad_angle=float(_sv(st, "sub_grad_angle")),
        sub_glow_on=st.get("sub_glow_on"),
        sub_glow_yellow=bool(_sv(st, "sub_glow_yellow")),
        sub_glow_amt=float(_sv(st, "sub_glow_amt")),
        sub_glow_rad=float(_sv(st, "sub_glow_rad")),
        sub_glow_fill=list(_sv(st, "sub_glow_fill")),
        top_line=st.get("top_line"),
        top_line_y=float(_sv(st, "top_line_y")),
        top_line_w=float(_sv(st, "top_line_w")),
        top_line_th=float(_sv(st, "top_line_th")),
        top_line_from=list(_sv(st, "top_line_from")),
        top_line_to=list(_sv(st, "top_line_to")),
        top_line_track_fill=list(_sv(st, "top_line_track_fill")),
        top_line_track_op=float(_sv(st, "top_line_track_op")),
        caption=st.get("caption"),
        caption_case=_sv_or(st, "caption_case"),
        caption_font=_sv_or(st, "caption_font"),
        caption_size=float(_sv(st, "caption_size")),
        caption_fill=list(_sv(st, "caption_fill")),
        caption_x=float(_sv(st, "caption_x")),
        caption_y=float(_sv(st, "caption_y")),
        caption_bg=bool(_sv(st, "caption_bg")),
        caption_bg_fill=list(_sv(st, "caption_bg_fill")),
        caption_bg_op=float(_sv(st, "caption_bg_op")),
        caption_bg_round=float(_sv(st, "caption_bg_round")),
        caption_kx=float(_sv(st, "caption_kx")),
        caption_ky=float(_sv(st, "caption_ky")),
        # --- интро ---
        intro_scale=_intro_scale,
        intro_scale_k=float(_intro_scale) / 100,
        intro_x=_sv_or(st, "intro_x"),
        intro_y=_sv_or(st, "intro_y"),
        intro_y2=_sv_or(st, "intro_y2"),
        intro_cam=bool(_sv(st, "intro_cam")),
        # Ручка камеры 2: чтение своё (ключ мог прийти из старого стиля только миграцией,
        # см. styles.migrate_intro_cam2) — второго чтения intro_cam на неё нет.
        intro_cam2=bool(_sv(st, "intro_cam2")),
        fit_w=_fit1,
        fit_w2=_fit2,
        fit_max=float(_sv_or(st, "intro_fit_max")),
        fit_max2=float(_sv_or(st, "intro_fit_max2")),
        line_step_k=float(_sv(st, "intro_line_step")) / 100.0,
        big_step_k=float(_sv(st, "intro_big_step")) / 100.0,
        back_step=float(_sv(st, "back_step")),
        back_step_after=(None if _back_step_after is None else float(_back_step_after)),
        back_scale=float(_sv(st, "back_scale")),
        intro_fade=float(_sv(st, "intro_fade")),
        intro_fx_hold_add=float(_sv(st, "intro_fx_hold_add")),
        intro_sub_cut=bool(_sv(st, "intro_sub_cut")),
        intro_sub_fade=float(_sv(st, "intro_sub_fade")),
        intro_scale_anchor=str(_sv_or(st, "intro_scale_anchor")),
        intro_scale_anchor2=str(_sv_or(st, "intro_scale_anchor2")),
        intro_anchor=_sv_or(st, "intro_anchor"),
        intro_anchor2=_sv_or(st, "intro_anchor2"),
        intro_big_gap=float(_sv(st, "intro_big_gap")),
        intro_big_over=float(_sv(st, "intro_big_over")),
        intro_roto_by_pos=st.get("intro_roto_by_pos"),
        intro_roto_by_pos2=st.get("intro_roto_by_pos2"),
        intro_shade=st.get("intro_shade"),
        intro_shade_op=float(_sv(st, "intro_shade_op")),
        intro_glow=float(_sv(st, "intro_glow")),
        intro_fill=st.get("intro_fill"),
        intro_hl_fill=st.get("intro_hl_fill"),
        intro_shadow=st.get("intro_shadow"),
        intro_shadow_op=float(_sv(st, "intro_shadow_op")),
        intro_shadow_dir=float(_sv(st, "intro_shadow_dir")),
        intro_shadow_dist=float(_sv(st, "intro_shadow_dist")),
        intro_shadow_soft=float(_sv(st, "intro_shadow_soft")),
        back_shadow_op=float(_sv(st, "back_shadow_op")),
        back_shadow_soft=float(_sv(st, "back_shadow_soft")),
        intro_glitch_shadow=bool(_sv(st, "intro_glitch_shadow")),
        intro_back_shadow=bool(_sv(st, "intro_back_shadow")),
        intro_glitch_glow=bool(_sv(st, "intro_glitch_glow")),
        intro_fx_glow=bool(_sv(st, "intro_fx_glow")),
        intro_accent_glow=bool(_sv(st, "intro_accent_glow")),
        # Третья и четвёртая двери свечения интро (задание «glowfix»): жёлтое слово
        # хайлайта и слой прекомпа группы — читаются той же обёрткой, что соседи выше.
        intro_hl_glow=bool(_sv(st, "intro_hl_glow")),
        intro_comp_glow=bool(_sv(st, "intro_comp_glow")),
        intro_word_glow_thr=float(_sv(st, "intro_word_glow_thr")),
        intro_word_glow_rad=float(_sv(st, "intro_word_glow_rad")),
        intro_word_glow_int=float(_sv(st, "intro_word_glow_int")),
        dg_with_glow=bool(_sv(st, "intro_dg_with_glow")),
        intro_comp_shadow_fill=[float(v) for v in (_sv_or(st, "intro_comp_shadow_fill"))],
        # round до десятых — та же точность, что у ручки (миграция считает проценты так же):
        # 68/255*100 = 26.666… -> 26.7, а обратно 26.7*255/100 = 68.085, и это ровно тот
        # дефолт, который шаблон узнаёт по допуску 0.05.
        intro_comp_shadow_op=round(float(_sv(st, "intro_comp_shadow_opacity")) * 255 / 100, 1),
        intro_comp_shadow2_fill=[float(v) for v in (_sv_or(st, "intro_comp_shadow2_fill"))],
        intro_comp_shadow2_op=round(float(_sv(st, "intro_comp_shadow2_opacity")) * 255 / 100, 1),
        intro_comp_shadow_dir=float(_sv(st, "intro_comp_shadow_dir")),
        intro_comp_shadow_dist=float(_sv(st, "intro_comp_shadow_dist")),
        intro_comp_shadow_soft=float(_sv(st, "intro_comp_shadow_soft")),
        # --- вставки ---
        insert_anim=_insert_anim,
        insert_style=_sv_or(st, "insert_style"),
        insert_snap_cut=bool(_sv(st, "insert_snap_cut")),
        insert_snap_start=float(st.get("insert_snap_start", 0.35)),
        insert_sub_swap=bool(_sv(st, "insert_sub_swap")),
        insert_fx=_sv_or(st, "insert_fx"),
        insert_video_front=st.get("insert_video_front", True),
        insert_c1_x=float(_sv_or(st, "insert_c1_x")),
        insert_c1_y=float(_sv_or(st, "insert_c1_y")),
        insert_c2_x=float(_sv(st, "insert_c2_x")),
        insert_c2_y=float(_sv(st, "insert_c2_y")),
        insert_c1on2_x=float(_sv_or(st, "insert_c1on2_x")),
        insert_c1on2_y=float(_sv_or(st, "insert_c1on2_y")),
        plate_path=_plate_path,
        plate_scale=_plate_scale,
        # --- камера ---
        cam1_zoom=_sv_or(st, "cam1_zoom"),
        cam1_zoom_start=_sv(st, "cam1_zoom_start"),
        cam1_zoom_big=float(_sv_or(st, "cam1_zoom_big")),
        cam1_zoom_lo=float(_sv_or(st, "cam1_zoom_lo")),
        cam1_zoom_hi=float(_sv_or(st, "cam1_zoom_hi")),
        cam1_drift_lo=float(_sv_or(st, "cam1_drift_lo")),
        cam1_drift_hi=float(_sv_or(st, "cam1_drift_hi")),
        cam1_take_zoom=bool(_sv(st, "cam1_take_zoom")),
        cam1_take_min=float(_sv_or(st, "cam1_take_min")),
        cam1_take_lo=float(_sv_or(st, "cam1_take_lo")),
        cam1_take_hi=float(_sv_or(st, "cam1_take_hi")),
        cam1_take_hold=float(_sv_or(st, "cam1_take_hold")),
        cam1_take_out=float(_sv_or(st, "cam1_take_out")),
        cam1_take_yellow_mode=str(_sv_or(st, "cam1_take_yellow_mode") or "off"),
        cam1_take_yellow=bool(_sv(st, "cam1_take_yellow")),
        cam1_yellow_zoom=bool(_sv(st, "cam1_yellow_zoom")),
        cam1_yellow_zoom_strong=bool(_sv(st, "cam1_yellow_zoom_strong")),
        hl_zoom_strength=str(_sv_or(st, "hl_zoom_strength") or "emotion"),
        hl_zoom_min_pct=float(_sv(st, "hl_zoom_min_pct")),
        hl_zoom_max_per_piece=int(float(_sv(st, "hl_zoom_max_per_piece"))),
        hl_zoom_second_min_s=float(_sv(st, "hl_zoom_second_min_s")),
        cam1_fit=float(_sv_or(st, "cam1_fit")),
        cam1_zoom_cx=float(_sv(st, "cam1_zoom_cx")),
        cam1_zoom_cy=float(_sv(st, "cam1_zoom_cy")),
        cam2_zoom=_sv_or(st, "cam2_zoom"),
        cam2_zoom_start=_sv(st, "cam2_zoom_start"),
        cam2_zoom_big=float(_sv_or(st, "cam2_zoom_big")),
        cam2_zoom_lo=float(_sv_or(st, "cam2_zoom_lo")),
        cam2_zoom_hi=float(_sv_or(st, "cam2_zoom_hi")),
        cam2_drift_lo=float(_sv_or(st, "cam2_drift_lo")),
        cam2_drift_hi=float(_sv_or(st, "cam2_drift_hi")),
        cam2_take_zoom=bool(_sv(st, "cam2_take_zoom")),
        cam2_take_min=float(_sv_or(st, "cam2_take_min")),
        cam2_take_lo=float(_sv_or(st, "cam2_take_lo")),
        cam2_take_hi=float(_sv_or(st, "cam2_take_hi")),
        cam2_take_hold=float(_sv_or(st, "cam2_take_hold")),
        cam2_take_out=float(_sv_or(st, "cam2_take_out")),
        cam2_take_yellow_mode=str(_sv_or(st, "cam2_take_yellow_mode") or "off"),
        cam2_take_yellow=bool(_sv(st, "cam2_take_yellow")),
        cam2_yellow_zoom=bool(_sv(st, "cam2_yellow_zoom")),
        cam2_yellow_zoom_strong=bool(_sv(st, "cam2_yellow_zoom_strong")),
        cam2_fit=float(_sv_or(st, "cam2_fit")),
        cam2_zoom_cx=float(_sv(st, "cam2_zoom_cx")),
        cam2_zoom_cy=float(_sv(st, "cam2_zoom_cy")),
        cam2_pan_x=float(_sv_or(st, "cam2_pan_x")),
        cam2_pan_y=float(_sv_or(st, "cam2_pan_y")),
        cam2_rot=float(_sv_or(st, "cam2_rot")),
        cam2_head_follow=st.get("cam2_head_follow"),
        cam2_head_x=float(_sv(st, "cam2_head_x")),
        cam2_head_smooth=float(_sv(st, "cam2_head_smooth")),
        cam2_head_min=float(_sv(st, "cam2_head_min")),
        cam1_pan_x=float(_sv_or(st, "cam1_pan_x")),
        cam1_pan_y=float(_sv_or(st, "cam1_pan_y")),
        cam1_rot=float(_sv_or(st, "cam1_rot")),
        cam1_head_follow=st.get("cam1_head_follow"),
        cam1_head_x=float(_sv(st, "cam1_head_x")),
        cam1_head_smooth=float(_sv(st, "cam1_head_smooth")),
        cam1_head_min=float(_sv(st, "cam1_head_min")),
        roto_cam1_only=_sv(st, "roto_cam1_only"),
        # --- звук (ключи <звук>_in/_out/_at/_db — сырые, арифметика в plan_audio.py) ---
        glitch_db=float(_sv(st, "glitch_db")),
        voice_db=float(_sv_or(st, "voice_db")),
        audio_fades=bool(_sv(st, "audio_fades")),
        pop=st.get("pop"),
        glitch=st.get("glitch"),
        transition=st.get("transition"),
        transition_sfx=st.get("transition_sfx"),
        pop_in=st.get("pop_in"),
        pop_out=st.get("pop_out"),
        pop_at=st.get("pop_at"),
        pop_db=st.get("pop_db"),
        transition_sfx_in=st.get("transition_sfx_in"),
        transition_sfx_out=st.get("transition_sfx_out"),
        transition_sfx_at=st.get("transition_sfx_at"),
        transition_sfx_db=st.get("transition_sfx_db"),
        intro_riser_in=st.get("intro_riser_in"),
        intro_riser_out=st.get("intro_riser_out"),
        intro_riser_at=st.get("intro_riser_at"),
        intro_riser_db=st.get("intro_riser_db"),
        transition_in=st.get("transition_in"),
        transition_out=st.get("transition_out"),
        transition_at=st.get("transition_at"),
        transition_db=st.get("transition_db"),
        pop_lead=st.get("pop_lead"),
        # --- палитра камер через Lumetri ---
        lm_on=_sv(st, "lm_on"),
        lm_exposure=float(_sv(st, "lm_exposure")),
        lm_contrast=float(_sv(st, "lm_contrast")),
        lm_highlights=float(_sv(st, "lm_highlights")),
        lm_shadows=float(_sv(st, "lm_shadows")),
        lm_whites=float(_sv(st, "lm_whites")),
        lm_blacks=float(_sv(st, "lm_blacks")),
        lm_temp=float(_sv(st, "lm_temp")),
        lm_tint=float(_sv(st, "lm_tint")),
        lm_sat=float(_sv(st, "lm_sat")),
        # --- палитра Камеры 2 через Lumetri ---
        lm2_link=bool(_sv(st, "lm2_link")),
        lm2_on=_sv(st, "lm2_on"),
        lm2_exposure=float(_sv(st, "lm2_exposure")),
        lm2_contrast=float(_sv(st, "lm2_contrast")),
        lm2_highlights=float(_sv(st, "lm2_highlights")),
        lm2_shadows=float(_sv(st, "lm2_shadows")),
        lm2_whites=float(_sv(st, "lm2_whites")),
        lm2_blacks=float(_sv(st, "lm2_blacks")),
        lm2_temp=float(_sv(st, "lm2_temp")),
        lm2_tint=float(_sv(st, "lm2_tint")),
        lm2_sat=float(_sv(st, "lm2_sat")),
        # --- порядок слоёв и размытие на старте ---
        layer_order=_sv_or(st, "layer_order"),
        start_blur=float(_sv_or(st, "start_blur")),
        start_blur_dur=float(_sv_or(st, "start_blur_dur")),
    )
