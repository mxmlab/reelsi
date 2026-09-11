# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Пресеты стиля для сборки в After Effects (шрифт, цвет выделения, звуки, рото, музыка).

Пресет — это dict. Встроенные («Базовый» по умолчанию и «Geologica») + пользовательские
шаблоны как JSON-файлы в reelsi/styles/*.json (сохраняются из UI, не хардкодятся).

Поля пресета (все опциональны, чего нет — берётся из «Базового»):
  label          человекочитаемое имя
  font           PostScript-имя шрифта базового текста (белые слова)
  hl_font        шрифт выделенных слов (жирный вариант); None = как font
  hl_fill        цвет выделения [r,g,b] 0..1
  sub_fill       цвет базовых субтитров [r,g,b] 0..1 (дефолт [1,1,1] — белый)
  sub_case       регистр субтитров: upper (КАПСОМ, дефолт) | lower (строчными) | sentence (Как в предложении)
  transition     видео-переход для видеовставок: ключ assets.json | путь | None(=Quick 2.mov)
  transition_sfx звук перехода: ключ | путь | None(=whoosh)
  pop            звук на жёлтое слово: ключ | путь | None(=highlight_pop)
  pop_lead       на сколько кадров «поп» ставится ПОЗЖЕ жёлтого слова (дефолт 4)
  glitch         звук глитча интро: ключ | путь | None(=glitch)
  glitch_db      громкость звука глитча интро (dB), дефолт 0
  music_db       на сколько тише музыка (dB), дефолт −20
  voice_db       базовая громкость голоса (dB), дефолт 0; цензура ныряет voice_db→−100→voice_db
  disclaimer     текст дисклеймера в начале; None = дефолтный, "" = скрыть, строка = свой
  intro_riser    интро-SFX (ризер) в начале ролика: True/False
  intro_riser_file файл ризера: ключ | путь | None(=дефолт)
  roto           вешать ли ротоскоп по умолчанию
  roto_bottom    доля низа кадра в маску (стол), 0..1
  roto_device    'cuda' | 'cpu' | None(авто)
  intro_mode     'word' | 'line'
  cam1_fit       масштаб кадра Камеры 1, % заполнения композиции (100 = кадр заполнен ровно)
  intro_scale    общий масштаб интро, % (на нуле «интро»)
  intro_y        общий сдвиг интро по вертикали, px
  intro_y2       добавка к сдвигу для интро на перебивке, px (нул «интро на кам2»)
  accent_font    акцентный шрифт отдельных слов интро (PostScript-имя); пусто = выключено
  accent_case    регистр акцентных слов: title (Заглавная первая) | as-is | upper
  back_font      шрифт строк «на заднем плане» (PostScript-имя); пусто = выключено
  back_case      регистр строк «на заднем плане»: lower | as-is | upper
  back_step      шаг строки заднего плана, доля от обычного шага (дефолт 0.45, 1.0 = как все)
  back_scale     масштаб шрифта строк заднего плана (доля от FONT_SIZE, дефолт 0.69)
  back_gap       зазор между БУКВАМИ соседних строк интро, px: шаг до строки заднего плана
                 не меньше «хвост вниз верхней строки + высота букв нижней + зазор» (дефолт 4)
  intro_anchor   якорь блока интро на камере 1: center (по центру, как раньше) | first
                 (первая строка на месте, остальные ниже)
  intro_anchor2  то же для групп, попавших на перебивку (камера 2, свой нул)
  intro_roto_by_pos галка: группа интро камеры 1 из НИЖНЕЙ половины кадра встаёт над рото (дефолт False)
  intro_fx_fade       спад прекомпа с эффектами (глитч), с — дефолт 0.45 вместо общего 0.75
  intro_fx_fade_last  спад, если следом долго нет интро (последний прекомп либо пауза > 2 с), с — дефолт 0.35
  intro_fx_hold_add   добавка к полке прекомпа в том же случае, с — дефолт 0.3
  hl_fill3       третий цвет выделения интро [r,g,b] 0..1 (строка color=="accent")
  intro_fill     цвет обычного текста интро [r,g,b]; None = белый (как сегодня)
  intro_hl_fill  цвет выделенного (жёлтого) текста интро [r,g,b]; None = hl_fill
  intro_shadow   тень (Drop Shadow) на КАЖДОМ слове интро (пресет): True/False
  intro_shadow_op   ...opacity тени, 0..255
  intro_shadow_dir  ...направление тени, град
  intro_shadow_dist ...дистанция тени, px
  intro_shadow_soft ...мягкость тени, px
  back_shadow_op    ...прозрачность тени строк заднего плана, 0..255
  back_shadow_soft  ...мягкость тени строк заднего плана, px
  intro_comp_shadow_fill  цвет тени ПРЕКОМПА интро на камере 1 [r,g,b] 0..1
  intro_comp_shadow_op    ...непрозрачность этой тени, 0..255
  intro_comp_shadow2_fill цвет тени ПРЕКОМПА интро на камере 2 [r,g,b] 0..1
  intro_comp_shadow2_op   ...непрозрачность этой тени, 0..255
  start_blur     размытие на старте (сила Gaussian Blur, px); 0 = выключено
  start_blur_dur за сколько секунд размытие уходит в ноль
  disclaimer_end галка: копия головного дисклеймера в конце ролика
  disc_gap       зазор между БУКВАМИ соседних строк дисклеймера, px; None = интервал авто,
                 как сегодня. Шаг строк = «хвост вниз верхней строки + высота букв нижней
                 + зазор», высоту букв берут из файла шрифта (fonts.ink_extent), поэтому
                 после смены шрифта строки дисклеймера не наезжают друг на друга
  cam1_zoom      режим зума Камеры 1: 'pulse' (наезд с откатом) | 'jump' (скачки) | 'drift' (дрейф) | 'none' (нет зума)
  cam1_zoom_start наезд в первом кадре ролика: True (дефолт) | False
  cam1_zoom_cx   точка наезда Камеры 1 по X, доля кадра (0.5 = центр)
  cam1_zoom_cy   точка наезда Камеры 1 по Y, доля кадра (0.5 = центр)
  cam1_zoom_big  первый наезд Камеры 1, % (только pulse и drift; в jump не используется)
  cam1_zoom_lo   возвраты кам2→кам1: нижняя граница случайного пика, % (pulse и jump)
  cam1_zoom_hi   …верхняя граница % (возврат всегда в 100)
  insert_c2_x    точка покоя вставок Кам2 по X, доля кадра (0.5 = центр)
  insert_c2_y    точка покоя вставок Кам2 по Y, доля кадра (0.172 = сегодняшняя константа)
  insert_c1_x    общий сдвиг точки покоя вставок Кам1 по X, px (0 = как сегодня)
  insert_c1_y    …и по Y, px (0 = как сегодня)
  insert_anim    анимация фотовставок кам2: zoom (как сегодня: наезд от большего + блюр) | rise (выезд снизу + рост + фейд, субтитры уходят)
  intro_x        сдвиг всего интро по горизонтали, px (пара к intro_y)
  caption        подпись о ролике сверху (True | False)
  caption_font   шрифт подписи (PostScript-имя)
  caption_size   кегль подписи на экране, px (единственный размер, масштаб слоя всегда 100%)
  caption_fill   цвет текста подписи [r,g,b] 0..1
  caption_x      левый край блока подписи (плашки), px
  caption_y      базовая линия текста, px
  caption_bg     плашка под подписью (True | False)
  caption_bg_fill цвет плашки [r,g,b] 0..1
  caption_bg_op  прозрачность плашки, %
  caption_bg_round скругление плашки, px

Замена ассетов (transition/transition_sfx/pop): если None — дефолт; если абсолютный путь к
существующему файлу — он; иначе трактуем как ключ assets.json; если и там нет — дефолт.
"""
import os, json, copy

from core import paths

STYLE_DIR = paths.root("styles")

# Базовый пресет. Любой другой наследует отсюда недостающие поля.
BASE = {
    "label": "Базовый",
    "font": "SFPro-CondensedSemibold",
    "hl_font": None,                       # шрифт выделения (реальный жирный вариант); None = как база
    "hl_bold": False,                      # искусственный жирный (fauxBold) на выделенных
    "intro_font": None,                    # шрифт текста интро; None = как font (субтитры)
    "intro_hl_font": None,                 # шрифт выделения интро; None = как hl_font (субтитры)
    "hl_fill": [1, 0.9176, 0],             # жёлтый
    "transition": None,                    # Quick 2.mov (assets.json → transition)
    "transition_sfx": None,                # whoosh
    "pop": None,                           # highlight_pop
    "pop_db": 0.0,                         # смещение громкости от базовых −8 dB (highlight_pop)
    "glitch": None,                        # gltchgltch_24.wav / glitch
    "glitch_db": 0.0,                      # громкость звука глитча, dB (0 = как в файле)
    "music_db": -20.0,
    "voice_db": 0.0,                       # базовая громкость голоса (dB); цензура ныряет voice_db→−100→voice_db
    "audio_fades": True,                   # микро-фейд ~10мс на краях аудио-клипов камеры (щелчки на склейках)
    "disclaimer": None,                    # текст дисклеймера в начале; None = дефолтный, "" = скрыть, иначе свой
    "intro_riser": True,                   # интро-SFX (ризер) в начале ролика
    "intro_riser_file": None,              # файл ризера; None = ассет по умолчанию (дефолт совпадает с build.py)
    "pop_lead": 4,                         # на сколько кадров «поп» ПОЗЖЕ жёлтого слова (дефолт совпадает с build.py)
    "roto": True,                          # рото по умолчанию включён (фото-вставки + интро)
    "roto_bottom": 0.35,                   # низ маски (стол) 35%
    "roto_device": None,
    "roto_cam1_only": True,                # рото ТОЛЬКО на кусках Камеры 1 (на cam2 не делаем)
    "insert_style": "auto",                # стиль фотовставок: auto (по активной камере) | cam1 | cam2
    "insert_fx": "card",                   # эффекты фото: card (чёрная тень + скруглённая маска) | white (старые: белая тень + Simple Choker)
    "insert_c1on2_x": 0.0,                 # стиль «Кам 1», но вставка попала на перебивку: общий сдвиг точки покоя по X, px
    "insert_c1on2_y": 0.0,                 # …и по Y (такие вставки не привязаны к зуму Камеры 1 — у них своя раскладка)
    "insert_c1_x": 0.0,                    # общий сдвиг точки покоя вставок Кам1 по X, px (0 = как сегодня);
                                           # вставка кам1 в кадре Камеры 1 наследует зум нула камеры,
                                           # поэтому и сдвиг масштабируется при наезде (задание CB)
    "insert_c1_y": 0.0,                    # …и по Y
    "insert_snap_cut": True,               # True = вставку, ПЕРЕХОДЯЩУЮ на другую камеру, обрезаем в точке смены (без анимации выхода)
    "insert_anim": "zoom",                 # анимация фотовставок кам2: zoom (как сегодня: наезд от большего + блюр) | rise (выезд снизу + рост + фейд, субтитры уходят)
    "layer_order": ["subs", "video", "roto", "photo", "intro"], # порядок слоёв сверху вниз (задание FM)
    "cam1_zoom_start": True,               # наезд в первом кадре ролика (False = ролик начинается со 100%, без наезда)
    "cam1_zoom": "pulse",                  # режим зума Камеры 1 (pulse | jump | drift | none); дефолт совпадает с build.py
    "cam1_zoom_big": 182.0,                # ПЕРВЫЙ зум кам1 (наезд в начале), %
    "cam1_zoom_lo": 112.0,                 # последующие возвраты кам2→кам1: случайный пик, нижняя граница %
    "cam1_zoom_hi": 140.0,                 # …верхняя граница % (возврат всегда в 100)
    "cam1_drift_lo": 100.0,                # режим «drift»: нижняя граница случайного скейла %
    "cam1_drift_hi": 160.0,                # …верхняя граница % (между катами плавный дрейф)
    "cam1_fit": 100.0,                     # масштаб кадра камеры 1 в % ЗАПОЛНЕНИЯ композиции при зуме
                                           # нула 100% (100 = кадр заполнен ровно, 120 = врезка на 20%).
                                           # Считается от реального размера исходника, а не от масштаба
                                           # из Премьера — тот врёт на пережатых файлах. Зум нула поверх
    "intro_scale": 100.0,                  # общий масштаб всего интро, % (нул «интро»); 100 = как рисует
                                           # скрипт сам. Врезка кадра на интро не влияет — оно на своём нуле
    "intro_y": 0.0,                        # общий сдвиг всего интро по вертикали, px при зуме 100% (+ вниз)
    "intro_y2": 0.0,                       # ДОБАВКА к сдвигу для интро, попавшего на перебивку (нул «интро
                                           # на кам2»): на кам2 кадр другой и текст за спиной ставят ниже
    "intro_mode": "word",
    "intro_glow": 1.0,                     # Glow Intensity на интро-тексте (AE-дефолт 1.0)
    "accent_font": "",                     # акцентный шрифт отдельных слов интро (PostScript-имя);
                                           # пусто = выключено (акцент в разобранном проекте —
                                           # отдельный шрифт TeddyBear на 10 словах, регистр другой)
    "accent_case": "title",                # регистр акцентных слов: title (Заглавная первая) |
                                           # as-is | upper
    "back_font": "",                       # шрифт строк «на заднем плане» (PostScript-имя);
                                           # пусто = выключено. У Джаггера это Geologica-ExtraLight
    "back_case": "lower",                  # регистр этих строк: lower | as-is | upper
    "back_step": 0.45,                     # шаг строки заднего плана, доля от обычного шага (дефолт 0.45, 1.0 = как все)
    "back_scale": 0.69,                    # масштаб шрифта строк заднего плана (69% от FONT_SIZE)
    "back_gap": 4.0,                       # зазор между буквами соседних строк, px: шаг ДО строки
                                           # заднего плана = max(базовый, хвост верхней строки +
                                           # высота букв нижней + back_gap). Питоновский расчёт
                                           # (xml2ae/layout.intro_line_ys, fonts.ink_extent): шаг
                                           # больше не жёсткие пиксели и знает шрифт (задание A1)
    "intro_anchor": "center",              # якорь блока интро на камере 1: "center" (по центру,
                                           # как раньше) | "first" (первая строка стоит на месте,
                                           # добавленная строка опускает только нижние)
    "intro_anchor2": "center",             # то же для групп на перебивке (нул «интро на кам2»)
    "intro_roto_by_pos": False,            # галка «Интро над рото в нижней половине (камера 1)»:
                                           # группа, чей блок от центра кадра ниже центра (зона
                                           # субтитров), встаёт над рото; в верхней — под рото.
                                           # Группы на перебивке и на видеовставке не трогаются
    "intro_fx_fade": 0.45,                 # спад прекомпа с эффектами (глитч), с
    "intro_fx_fade_last": 0.35,            # спад, если следом долго нет интро, с
    "intro_fx_hold_add": 0.3,              # добавка к полке в том же случае, с
    "hl_fill3": [0.6863, 0.1216, 0.1216],  # третий цвет выделения интро (тёмно-красный);
                                           # берёт строка интро с color=="accent"
    "intro_fill": None,                    # цвет обычного текста интро [r,g,b]; None = белый
    "intro_hl_fill": None,                 # цвет выделенного текста интро; None = hl_fill
    "intro_shadow": False,                 # тень (Drop Shadow) на КАЖДОМ слове интро (пресет)
    "intro_shadow_op": 116.0,              # ...opacity тени, 0..255
    "intro_shadow_dir": 16.0,              # ...направление тени, град
    "intro_shadow_dist": 6.8,              # ...дистанция тени, px
    "intro_shadow_soft": 34.0,             # ...мягкость тени, px
    "back_shadow_op": 131.0,               # прозрачность тени строк заднего плана, 0..255
    "back_shadow_soft": 38.0,              # мягкость тени строк заднего плана, px
    # Тень ПРЕКОМПА интро (задание B): своя у камеры 1 и у камеры 2 (нул «интро на кам2»).
    # Дефолты = прежняя жёсткая белая тень dropShadow(iL, 68) — при них .jsx не меняется
    # ни на байт (golden); направление 135, дистанция 0 и мягкость 287 в шаблоне.
    "intro_comp_shadow_fill": [1, 1, 1],   # цвет тени прекомпа интро на камере 1 [r,g,b]
    "intro_comp_shadow_op": 68.0,          # ...непрозрачность, 0..255 (было 68)
    "intro_comp_shadow2_fill": [1, 1, 1],  # цвет тени прекомпа интро на камере 2 [r,g,b]
    "intro_comp_shadow2_op": 68.0,         # ...непрозрачность, 0..255
    "start_blur": 0.0,                     # размытие на старте: сила Gaussian Blur, px;
                                           # 0 = выключено (в разобранном проекте — 25→0 за 0.52с)
    "start_blur_dur": 0.52,                # за сколько секунд стартовое размытие уходит в ноль
    "disclaimer_end": False,               # копия головного дисклеймера в конце ролика (1с + фейд 0.35)
    "disc_gap": None,                      # зазор между буквами соседних строк дисклеймера, px;
                                           # None = интервал авто, как сегодня. Задан — .jsx
                                           # объявляет DISC_LEAD: шаг = max(desc верхней +
                                           # asc нижней) + disc_gap, высоты из файла шрифта
                                           # (fonts.ink_extent), кегль — ужатый под ширину
                                           # кадра (layout.DISC_FIT_W, задание E)
    "cam1_zoom_cx": 0.5,                   # точка наезда Камеры 1 по X, доли кадра; 0.5 = центр.
                                           # В AE якорь/позиция нула считаются от неё (задание Q):
                                           # при наезде неподвижна эта точка, а не центр кадра
    "cam1_zoom_cy": 0.5,                   # …и по Y
    "insert_c2_x": 0.5,                    # точка покоя вставок Кам2 по X, доли кадра (0.5 = центр)
    "insert_c2_y": 0.172,                  # …по Y (бывшая константа INS_C2_Y_FR; 0.172 ≈ 330px на 1920)
    "intro_x": 0.0,                        # сдвиг всего интро по горизонтали, px (пара к intro_y)
    "sub_y": 0.5964,                       # позиция субтитров: доля высоты кадра от ВЕРХА (0.5964 ≈ 40% снизу)
    "sub_scale": 100.0,                    # масштаб СЛОЯ прекомпа субтитров, % (100 = как сегодня);
                                           # раскладка внутри прекомпа не меняется, якорь/позиция
                                           # слоя при 100 не трогаются (задание FE)
    "sub_words_per_row": 1,                # слов в строке субтитров (целое, дефолт 1; 1 = по одному слову)
    "sub_rows_max": 1,                     # максимум строк субтитров при переносе: 1 (ужать кегль) или 2 (разбить на 2 строки)
    "sub_fill": [1.0, 1.0, 1.0],           # цвет базовых субтитров [r,g,b] 0..1 (белый, как сегодня)
    "sub_case": "upper",                   # регистр субтитров: upper (КАПСОМ, как сегодня) | lower | sentence
    "sub_bg": False,                       # плашка под субтитрами
    "sub_bg_fill": [1.0, 1.0, 1.0],        # цвет плашки
    "sub_bg_op": 72.0,                     # прозрачность плашки, %
    "sub_bg_h": 160.0,                     # высота плашки, px
    "sub_bg_round": 78.0,                  # скругление углов, px
    "sub_bg_pad": 18.0,                    # поля слева/справа, % от ширины строки
    "sub_bg_padmin": 70.0,                 # минимальное поле, px
    "sub_bg_dy": 0.0,                      # доводка по вертикали, px (+ вниз)
    "sub_bg_anim": 0.22,                   # за сколько секунд плашка переезжает на новую ширину
    "top_line": False,                     # верхняя строка-прогресс
    "top_line_y": 162.0,                   # высота линии в кадре, px от верха
    "top_line_w": 969.0,                   # длина линии, px
    "top_line_th": 12.5,                   # толщина, px
    "top_line_from": [0.984, 1.0, 0.541],  # цвет начала (#FBFF8A)
    "top_line_to": [1.0, 0.698, 0.988],    # цвет конца (#FFB2FC)
    "top_line_track_fill": [1.0, 1.0, 1.0],# цвет дорожки (белый)
    "top_line_track_op": 16.0,             # прозрачность дорожки, %
    "caption": False,                      # подпись о ролике сверху
    "caption_font": "SFPro-Bold",          # шрифт подписи (PostScript-имя)
    "caption_size": 26.0,                  # кегль текста на экране, px (задание DL-хвост: единственный размер, масштаб слоя 100%)
    "caption_case": "upper",               # регистр подписи: upper | as-is (задание DL: дефолт upper)
    "caption_fill": [1.0, 1.0, 1.0],       # цвет текста
    "caption_x": 55.5,                     # ЛЕВЫЙ КРАЙ блока подписи (плашки), px:
                                           # по началу верхней строки ((1080-969)/2)
    "caption_y": 228.0,                    # базовая линия текста, px
    "caption_bg": True,                    # плашка под подписью
    "caption_bg_fill": [0.345, 0.345, 0.345],
    "caption_bg_op": 45.0,                 # прозрачность плашки, %
    "caption_bg_round": 68.0,              # скругление, px (зажимается половиной высоты)
    "caption_kx": 1.718,                   # множитель ширины плашки от видимого текста (задание DL-хвост)
    "caption_ky": 2.484,                   # множитель высоты плашки от видимого текста (задание DL-хвост)
}

# Альтернативный пресет: Geologica, кремовое выделение, всегда ротоскоп.
# NB: видео-переход у этого стиля структурно другой (2 слоя transitions.mov + Luma Key +
# «Cinematic Woosh») — здесь заменяется только звук/файл на существующей механике; полная
# репликация дубль-перехода и 3-компового интро — отдельная доработка (см. ROADMAP).
GEOLOGICA = {
    "label": "Geologica (кремовый)",
    "font": "Geologica-Regular",
    "hl_font": "Geologica-SemiBold",       # реальный жирный вариант
    "hl_bold": False,
    "hl_fill": [0.9843, 0.8941, 0.7294],   # кремовый акцент
    "transition": "transitions",           # ключ assets.json, если положишь; иначе дефолт
    "transition_sfx": "cinematic_woosh",
    "pop": None,
    "music_db": -20.0,
    "roto": True,
    "roto_bottom": 0.25,
    "roto_device": None,
    "insert_style": "auto",
    "intro_mode": "line",
}

BUILTIN = {"base": BASE, "geologica": GEOLOGICA}

# Прежние ключи встроенных пресетов: остаются рабочими, чтобы сохранённый выбор в
# ui_state.json и старые сохранённые проекты не слетели после переименования.
# Читаются из styles/_aliases.json (gitignored) — в старых ключах были фамилии
# реальных людей, а этот файл наружу не уезжает. Нет файла = нет алиасов: встроенные
# base/geologica работают как обычно, а незнакомый ключ get() отдаёт базовым.
def _aliases():
    p = os.path.join(STYLE_DIR, "_aliases.json")
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}


ALIASES = _aliases()


DEFAULT_LAYER_ORDER = ["subs", "video", "roto", "photo", "intro"]
ALL_LAYER_IDS = ("subs", "intro", "photo", "video", "roto")


def migrate_style_dict(data):
    """Миграция старых ключей стиля в layer_order (задание FM).

    insert_above_subs=True -> photo встаёт выше subs
    insert_video_front=False -> video уходит ниже roto (за человеком)
    Старые ключи удаляются.
    """
    if not isinstance(data, dict):
        return data, False
    changed = False
    has_old_keys = ("insert_above_subs" in data) or ("insert_video_front" in data)

    if "layer_order" in data and isinstance(data["layer_order"], list):
        order = [x for x in data["layer_order"] if x in ALL_LAYER_IDS]
        for item in DEFAULT_LAYER_ORDER:
            if item not in order:
                order.append(item)
        order = order[:5]
    else:
        order = list(DEFAULT_LAYER_ORDER)
        if data.get("insert_video_front") is False:
            if "video" in order:
                order.remove("video")
            roto_idx = order.index("roto") if "roto" in order else len(order)
            order.insert(roto_idx + 1, "video")
            changed = True
        if data.get("insert_above_subs") is True:
            if "photo" in order:
                order.remove("photo")
            subs_idx = order.index("subs") if "subs" in order else 0
            order.insert(subs_idx, "photo")
            changed = True

    if has_old_keys:
        data.pop("insert_above_subs", None)
        data.pop("insert_video_front", None)
        changed = True

    if "layer_order" not in data or data["layer_order"] != order:
        data["layer_order"] = order
        changed = True

    if data.get("back_step") == 0.75:
        data["back_step"] = 0.45
        changed = True

    return data, changed


def resolve(style):
    """style: имя пресета (str) | dict | None -> полный dict с дефолтами базового."""
    base = copy.deepcopy(BASE)
    if style is None:
        return base
    data = get(style) if isinstance(style, str) else copy.deepcopy(dict(style))
    data, _ = migrate_style_dict(data)
    base.update({k: v for k, v in (data or {}).items() if v is not None or k in ("hl_font",)})
    # hl_font=None означает «как font» — оставляем None осознанно
    if data and "hl_font" in data:
        base["hl_font"] = data["hl_font"]
    if "layer_order" in data and isinstance(data["layer_order"], list):
        base["layer_order"] = list(data["layer_order"])
    return base


def _files():
    if not os.path.isdir(STYLE_DIR):
        return {}
    out = {}
    for f in os.listdir(STYLE_DIR):
        # Файлы с «_» в начале — служебные (например _aliases.json со старыми именами
        # пресетов), а не пользовательские шаблоны. Без этой проверки они попадали в
        # СПИСОК СТИЛЕЙ и показывались в селекторе как пресет «_aliases».
        if f.startswith("_"):
            continue
        if f.lower().endswith(".json"):
            p = os.path.join(STYLE_DIR, f)
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                if isinstance(raw, dict):
                    data, changed = migrate_style_dict(raw)
                    if changed:
                        try:
                            with open(p, "w", encoding="utf-8") as fh:
                                json.dump(data, fh, ensure_ascii=False, indent=1)
                        except Exception:
                            pass
                    out[os.path.splitext(f)[0]] = data
            except Exception:
                pass
    return out


def all_styles():
    """Все пресеты: встроенные + пользовательские шаблоны (файлы перекрывают встроенные)."""
    d = copy.deepcopy(BUILTIN)
    d.update(_files())
    return d


def get(name):
    d = all_styles()
    return d.get(name) or d.get(ALIASES.get(name)) or copy.deepcopy(BASE)


def save(name, data):
    """Сохранить пользовательский пресет как reelsi/styles/<name>.json.
    Подпись (label) принудительно = введённому имени, чтобы список не показывал
    унаследованное от пресета имя."""
    os.makedirs(STYLE_DIR, exist_ok=True)
    data = dict(data or {})
    data["label"] = name
    data, _ = migrate_style_dict(data)
    safe = "".join(c for c in (name or "custom") if c.isalnum() or c in "-_ ").strip() or "custom"
    fname = safe + ".json"
    json.dump(data, open(os.path.join(STYLE_DIR, fname), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    # ФС Windows регистронезависимая: файл мог сохранить прежний регистр имени —
    # вернуть РЕАЛЬНЫЙ стем с диска, чтобы ключ совпал с тем, что покажет all_styles().
    for f in os.listdir(STYLE_DIR):
        if f.lower() == fname.lower():
            return os.path.splitext(f)[0], os.path.join(STYLE_DIR, f)
    return safe, os.path.join(STYLE_DIR, fname)


def patch(name, patch_dict):
    """Точечно обновить ключи в reelsi/styles/<name>.json.

    Не трогает остальные поля. Встроенные шаблоны (base/geologica) не меняет.
    """
    if not name:
        raise ValueError("Не указано имя стиля")
    if not isinstance(patch_dict, dict):
        raise ValueError("patch должен быть словарём")
    target = None
    target_name = None
    if os.path.isdir(STYLE_DIR):
        for f in os.listdir(STYLE_DIR):
            if f.startswith("_"):
                continue
            if f.lower().endswith(".json") and os.path.splitext(f)[0].lower() == name.lower():
                target = os.path.join(STYLE_DIR, f)
                target_name = os.path.splitext(f)[0]
                break
    if not target:
        raise FileNotFoundError(f"Шаблон стиля «{name}» не найден")
    with open(target, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        data = {}
    data.update(patch_dict)
    data, _ = migrate_style_dict(data)
    with open(target, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return target_name, target

