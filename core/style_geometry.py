# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Стиль задуман в кадре 1080×1920, а собирается в кадре формата ролика.

Стиль ОДИН на все форматы (9:16, 1:1, 4:5, 16:9), и числа в нём — пиксели
кадре 1080×1920: «Высота субтитров 40 % снизу», «Интро по вертикали, px»,
«Сдвиг фотовставок Кам1 по X, px». В квадратном кадре те же числа значили бы
другое: субтитры уезжали бы за нижний край, карточка вставки — за боковой.
Поэтому при сборке стиль пересчитывается под кадр формата — здесь объявлено,
КАК именно, и больше нигде:

  * ``x``    — положение или сдвиг по горизонтали, px → ``× W/1080``;
  * ``y``    — то же по вертикали → ``× H/1920``;
  * ``size`` — размер (кегль, обводка, разметка карточки, размытие) →
    ``× min(W, H)/1080``: мельче короткая сторона — мельче и размер;
  * ``frac`` — значение УЖЕ доля кадра (0.172) или доля от размера, который
    масштабируется сам (проценты, множители). Не меняется;
  * ключа в таблице нет — поле от кадра не зависит вовсе (секунды, dB,
    проценты, цвета, флаги) и перечислено в ``FRAME_INDEPENDENT``.

Почему ``frac`` не меняется, хотя это «положение»: ``sub_y`` (0.5964) — доля
ВЫСОТЫ кадра, а не пиксели, и в квадрате она честно даёт 644 px вместо 1145.
Ровно так же устроены точка наезда Камеры 1 и точка покоя вставок Кам2.

Отдельный вид у ``frac`` нужен не для арифметики, а для сторожа: поле без
пометки — это либо «забыли разметить» (тогда новый формат его не тронет, и
ролик соберётся «наполовину»), либо «от кадра не зависит». Разница видна
только человеку, поэтому она записана явно и проверяется тестом
(``tests/test_style_format.py``): числовой ключ ``styles.BASE`` обязан лежать
либо в таблице, либо в списке независимых.

Разметка сделана по СМЫСЛУ поля, а не по имени: у каждого размеченного поля
в таблице написано, где оно применяется. Сводка «поле → вид» уезжает фронту
полем ``geo`` схемы панели (``core/style_schema.py``) — вторая копия таблицы
на фронте разошлась бы с этой молча.
"""
from __future__ import annotations

from typing import Any, Mapping

# Кадр, в котором задуманы числа стиля. Формат по умолчанию (frame.DEFAULT)
# совпадает с ним, поэтому ролик 9:16 собирается РОВНО как раньше: пересчёт
# при 1080×1920 — тождество, и эталоны .jsx не меняются ни на байт.
BASE_W, BASE_H = 1080, 1920

# Виды полей. Порядок и имена — контракт: их читают и фронт (пересчёт превью),
# и тесты. Больше видов не бывает: всё прочее — «от кадра не зависит».
X, Y, SIZE, FRAC = "x", "y", "size", "frac"
KINDS = (X, Y, SIZE, FRAC)

# --------------------------------------------------------------------------- #
# Таблица разметки: поле стиля -> вид
# --------------------------------------------------------------------------- #
# Значения — те же, что в styles.BASE (ключ обязан там быть: сторож сверяет
# множества в обе стороны, лишняя строка тут — такая же ошибка, как пропуск).
FIELD_KIND: dict[str, str] = {
    # --- субтитры ---
    # Высота плашки под субтитрами, px композиции (layout.sub_bg_y: posy ± 0.27·кегль).
    "sub_bg_h": SIZE,
    # Скругление углов плашки, px: 78 при 160 высоты — доля высоты, а высота выше SIZE.
    "sub_bg_round": SIZE,
    # Минимальное поле плашки по бокам, px (проценты sub_bg_pad считаются от строки,
    # а этот минимум — от кадра).
    "sub_bg_padmin": SIZE,
    # Доводка плашки по вертикали, px (+ вниз).
    "sub_bg_dy": Y,
    # Сила появления субтитров (пресет sub_anim), px: подъём слова снизу. Задумана в
    # кадре 1080, как остальные размеры (sub_bg_h, top_line_w): в 4K-кадре подъём обязан
    # быть вдвое больше, иначе слово въезжает вдвое мельче задуманного.
    "sub_anim_amt": SIZE,
    # Подложка слова: высота, скругление и поле по бокам — px кадра 1080, как у плашки
    # под строкой (sub_bg_h/sub_bg_round/sub_bg_padmin): это размеры ФИГУРЫ, а не доли
    # слова. Доводка по вертикали — тот же вид, что sub_bg_dy.
    "sub_wbg_h": SIZE,
    "sub_wbg_round": SIZE,
    "sub_wbg_pad": SIZE,
    "sub_wbg_dy": Y,
    # Свечение текста: радиус Glo2 — px кадра (как intro_word_glow_rad у интро).
    "sub_glow_rad": SIZE,
    # --- верхняя строка-прогресс ---
    # Высота линии в кадре, px от верха.
    "top_line_y": Y,
    # Длина линии, px: 969 при кадре 1080 (запас по 55 px с каждой стороны).
    "top_line_w": SIZE,
    # Толщина линии, px.
    "top_line_th": SIZE,
    # --- подпись о ролике ---
    # Кегль подписи на экране, px (масштаб слоя всегда 100 %).
    "caption_size": SIZE,
    # Левый край блока подписи (плашки), px: (1080 − 969)/2.
    "caption_x": X,
    # Базовая линия текста подписи, px от верха.
    "caption_y": Y,
    # Скругление плашки подписи, px (зажимается половиной её высоты, а высота — от кегля).
    "caption_bg_round": SIZE,
    # --- дисклеймер ---
    # Сдвиг дисклеймера по горизонтали от центра кадра, px: «x»-поле, как сдвиги
    # интро/вставок — в 16:9 он обязан ехать вместе с шириной кадра.
    "disc_dx": X,
    # --- интро ---
    # Общий сдвиг всего интро по горизонтали, px (нул «интро»).
    "intro_x": X,
    # Общий сдвиг всего интро по вертикали, px (+ вниз).
    "intro_y": Y,
    # Положение интро на перебивке по вертикали, px (нул «интро на кам2»; само положение, не добавка).
    "intro_y2": Y,
    # Зазор между большим словом слева и стопкой строк справа, px.
    "intro_big_gap": SIZE,
    # Тень слов интро: дистанция и мягкость, px (мягкость 34 при кегле 140).
    "intro_shadow_dist": SIZE,
    "intro_shadow_soft": SIZE,
    # Мягкость тени строк заднего плана, px.
    "back_shadow_soft": SIZE,
    # Тень прекомпа интро: дистанция и мягкость, px (в шаблоне 135/0/287).
    "intro_comp_shadow_dist": SIZE,
    "intro_comp_shadow_soft": SIZE,
    # Glo2 Radius на словах, px (в шаблоне стояло жёстко 77).
    "intro_word_glow_rad": SIZE,
    # --- вставки ---
    # Общий сдвиг точки покоя вставок Кам1 по X, px (+ вправо).
    "insert_c1_x": X,
    # …и по Y (+ вниз).
    "insert_c1_y": Y,
    # То же для вставки стиля «Кам 1», попавшей на перебивку (своя раскладка).
    "insert_c1on2_x": X,
    "insert_c1on2_y": Y,
    # --- камера 1 ---
    # Сдвиг кадра Камеры 1 по X и Y, px.
    "cam1_pan_x": X,
    "cam1_pan_y": Y,
    # --- камера 2 ---
    # Сдвиг кадра Камеры 2 по X и Y, px.
    "cam2_pan_x": X,
    "cam2_pan_y": Y,
    # --- размытие на старте ---
    # Сила Gaussian Blur, px.
    "start_blur": SIZE,
}

# Числовые поля стиля, которые от размера кадра НЕ зависят. Список явный и
# полный: сюда попадает всё, что не в таблице, — и сторож требует, чтобы
# каждый числовой ключ BASE лежал в одном из двух мест.
#
# Группы (по смыслу, а не по имени):
#   * доли и проценты кадра/размера: sub_y (доля высоты кадра), insert_c2_x/y,
#     cam1_zoom_cx/cy, cam2_zoom_cx/cy, cam1_head_x (доли), cam1_fit, sub_scale,
#     intro_scale, intro_margin/intro_margin2 (процент с каждого края, доля ширины
#     из него — 1 − 2·margin/100), intro_line_step, intro_big_step, back_step,
#     back_step_after, back_scale, intro_big_over (проценты-множители);
#   * проценты зума камеры: cam1_zoom_big/lo/hi, cam1_drift_lo/hi, cam1_take_lo/hi,
#     cam1_head_min, insert_plate_scale;
#   * секунды и кадры: start_blur_dur, sub_bg_anim, intro_fade, intro_sub_fade,
#     intro_fx_hold_add, intro_last_hold, cam1_take_min, cam1_take_hold, pop_lead;
#   * dB и проценты прозрачности: glitch_db, music_db, voice_db, pop_db,
#     sub_bg_op, top_line_track_op, caption_bg_op, intro_shade_op;
#   * 0..255/0..10 у эффектов: intro_shadow_op, back_shadow_op,
#     intro_word_glow_thr, intro_word_glow_int, intro_glow;
#   * проценты непрозрачности тени прекомпа интро (в 0..255 их переводит план):
#     intro_comp_shadow_opacity, intro_comp_shadow2_opacity;
#   * цвет камер (Lumetri): экспозиция и ползунки цвета — безразмерные, как в AE;
#   * ротоскоп: roto_bottom — доля низа кадра под маску (0..1);
#   * углы: intro_shadow_dir, intro_comp_shadow_dir, cam1_rot;
#   * безразмерные множители: caption_kx, caption_ky, hl_blur_amt (сила блюра
#     появления задана относительно кегля субтитров, а не кадра), hl_size_k (кегль
#     жёлтого слова — множитель базового);
#   * правило силы жёлтых: hl_zoom_min_pct (порог силы в процентах процентиля),
#     hl_zoom_max_per_piece (счётчик наездов), hl_zoom_second_min_s (секунды);
#   * счётчики: sub_words_per_row, sub_rows_max, pop_lead (кадры), cam1_take_*.
FRAME_INDEPENDENT: frozenset[str] = frozenset({
    # служебная метка версии смысла intro_y2 — номер, не размер
    "intro_pos2_v",
    # субтитры
    "sub_y", "sub_scale", "sub_words_per_row", "sub_rows_max",
    "sub_bg_op", "sub_bg_pad", "sub_bg_anim",
    # субтитры: длительность появления слова — секунды, от кадра не зависит
    "sub_anim_dur",
    # субтитры: подложка слова — проценты и секунды. sub_wbg_op — прозрачность,
    # sub_wbg_sweep — время раскрытия маркера (с); сам цвет и вид фигуры от кадра
    # не зависят, а размеры (h/round/pad) размечены видом size выше.
    "sub_wbg_op", "sub_wbg_sweep",
    # субтитры: заливка текста — угол градиента (градусы, как в CSS) и признак режима
    "sub_grad_angle",
    # субтитры: свечение текста — интенсивность Glo2 (0..10) и галка «только жёлтые»
    "sub_glow_amt",
    # верхняя строка
    "top_line_track_op",
    # подпись
    "caption_bg_op", "caption_kx", "caption_ky",
    # дисклеймер: масштаб — множитель подобранного кегля (кегль уже размер кадра),
    # положение — доля ВЫСОТЫ кадра, как sub_y
    "disc_scale", "disc_y",
    # интро: множители, проценты и времена
    "intro_scale", "intro_margin", "intro_margin2", "intro_fit_max", "intro_fit_max2",
    "intro_line_step",
    "intro_big_step", "intro_big_over", "back_step",
    "back_scale", "intro_fade", "intro_fx_hold_add", "intro_last_hold",
    "intro_sub_fade", "intro_shadow_op", "intro_shadow_dir",
    "back_shadow_op", "intro_comp_shadow_opacity", "intro_comp_shadow2_opacity",
    "intro_comp_shadow_dir", "intro_word_glow_thr", "intro_word_glow_int",
    "intro_glow", "intro_shade_op",
    # вставки
    "insert_c2_x", "insert_c2_y", "insert_plate_scale",
    # камера 1
    "cam1_zoom_big", "cam1_zoom_lo", "cam1_zoom_hi", "cam1_drift_lo",
    "cam1_drift_hi", "cam1_take_min", "cam1_take_lo", "cam1_take_hi",
    "cam1_take_hold", "cam1_take_out", "cam1_fit", "cam1_zoom_cx", "cam1_zoom_cy",
    # камера 2: своя точка наезда и настройки зума — зеркально Камере 1
    "cam2_zoom_cx", "cam2_zoom_cy",
    "cam2_zoom_big", "cam2_zoom_lo", "cam2_zoom_hi", "cam2_drift_lo",
    "cam2_drift_hi", "cam2_take_min", "cam2_take_lo", "cam2_take_hi",
    "cam2_take_hold", "cam2_take_out", "cam2_fit", "cam2_rot",
    "cam2_head_x", "cam2_head_smooth", "cam2_head_min",
    "cam1_rot", "cam1_head_x", "cam1_head_smooth", "cam1_head_min",
    # правило силы жёлтых: проценты силы (порог), счётчик наездов и порог длины куска
    # в секундах — от кадра не зависят, это не геометрия
    "hl_zoom_min_pct", "hl_zoom_max_per_piece", "hl_zoom_second_min_s",
    # размытие на старте (длительность — секунды)
    "start_blur_dur",
    # субтитры: блюр появления задан долей кегля
    "hl_blur_amt",
    # субтитры: кегль жёлтого слова задан МНОЖИТЕЛЕМ базового — безразмерный,
    # от кадра не зависит (кегль субтитров уже пересчитан видом size)
    "hl_size_k",
    # ротоскоп: доля низа кадра под маску (0..1)
    "roto_bottom",
    # цвет камер (Lumetri): экспозиция и ползунки цвета — безразмерные, как в AE
    "lm_exposure", "lm_contrast", "lm_highlights", "lm_shadows", "lm_whites",
    "lm_blacks", "lm_temp", "lm_tint", "lm_sat",
    "lm2_exposure", "lm2_contrast", "lm2_highlights", "lm2_shadows", "lm2_whites",
    "lm2_blacks", "lm2_temp", "lm2_tint", "lm2_sat",
    # звук
    "pop_db", "glitch_db", "music_db", "voice_db", "pop_lead",
    # версии миграций
    "cam_zoom_v", "intro_comp_shadow_v",
})


def kind_of(key: str) -> str | None:
    """Вид поля стиля: ``x``/``y``/``size``/``frac`` или None — «не размечено»."""
    return FIELD_KIND.get(key)


# Поле-представитель каждого вида: по нему спрашивают МНОЖИТЕЛЬ вида, когда
# конкретного поля под рукой нет (драг превью переводит экранные пиксели в
# базовые единицы стиля — по оси). Копии правила «x -> W/1080» в интерфейсе нет:
# фронт спрашивает множитель по одному из этих ключей через ту же таблицу.
AXIS_KEYS: dict[str, str] = {"x": "intro_x", "y": "intro_y", "size": "top_line_w"}


def scale_for(kind: str | None, w: Any, h: Any) -> float:
    """Множитель пересчёта для вида поля в кадре ``w``×``h``.

    Неизвестный вид и неизвестный размер — 1.0: молча не пересчитать честнее,
    чем уронить сборку на чужом значении (профиль правят и руками).
    """
    try:
        fw, fh = float(w), float(h)
    except (TypeError, ValueError):
        return 1.0
    if fw <= 0 or fh <= 0:
        return 1.0
    if kind == X:
        return fw / BASE_W
    if kind == Y:
        return fh / BASE_H
    if kind == SIZE:
        return min(fw, fh) / BASE_W
    return 1.0


def _scaled(value: Any, k: float) -> Any:
    """Значение поля, умноженное на ``k``. Не число — как есть (мусор не наш)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    return round(float(value) * k, 2)


def scale_style(style: Mapping[str, Any], w: Any, h: Any) -> dict[str, Any]:
    """Стиль, пересчитанный под кадр ``w``×``h``; при 1080×1920 — он же.

    Вход не меняется (копия), ключи, которых нет в таблице, переносятся как
    есть: их либо не пересчитывают вовсе, либо читает кто-то другой. Поля из
    таблицы, которых в стиле НЕТ, подставляются из ``styles.BASE`` уже
    пересчитанными: иначе план взял бы по ним дефолт BASE (1080×1920) и
    разошёлся бы со стилем, где поле задано. Вызывается ТАМ, ГДЕ СТИЛЬ ВХОДИТ
    В ПЛАН (`core/xml2ae/plan_style.read_style`), с размером кадра плана: и
    .jsx, и превью, и черновик берут числа оттуда, второй копии формулы нет.
    """
    fw, fh = scale_for(X, w, h), scale_for(Y, w, h)
    fz = scale_for(SIZE, w, h)
    if fw == 1.0 and fh == 1.0 and fz == 1.0:
        return dict(style)          # тождество: числа стиля уже в этом кадре
    from core import styles as _styles          # импорт тут: styles зовёт этот модуль
    out = dict(style)
    for key, kind in FIELD_KIND.items():
        k = fw if kind == X else fh if kind == Y else fz if kind == SIZE else 1.0
        value = out[key] if key in out else _styles.BASE.get(key)
        if value is None:
            continue                # ключа нет и в BASE — подставлять нечего
        out[key] = _scaled(value, k)
    return out
