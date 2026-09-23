# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Геометрия кадра: карточки вставок, зум и дрейф камеры 1, раскладка, окна цензуры.

Числа здесь — не вкусовщина, а замеры: у каждой константы в комментарии написано,
какой кейс её задал.
"""
import heapq
import math
import os
from core import fonts as _fonts
from .jsutil import _r
from .parse import is_out_dir
from core.umsg import ReelsiError


# «Карточка» фотовставки на экране, px. Раньше масштаб был плоский (50 кам1 / 44 кам2) и не
# зависел от пропорций картинки: 16:9 выходило 295px высотой («мелкие все какие-то»), квадрат —
# 540. Теперь вписываем ВИДИМУЮ часть фото в коробку: при квадратной маске ширина равна высоте и
# упирается в CARD_H, у ультравайда (маску не режем) упирается в CARD_W.
INS_CARD_W, INS_CARD_H = 1030.0, 560.0
INS_CARD_H_CAM2 = 495.0            # кам2 садится мельче кам1 (сохраняем прежнее отношение 44/50)
INS_MASK_SQUARE_AR = 2.2           # должно совпадать с одноимённой константой в AE_FULL
# Сторона коробки, в которую вписано фото на подложке, долей стороны плашки:
# фото занимает 75% плашки, остальное — её поля.
INS_PLATE_INNER = 0.75
_IMG_SIZE_CACHE = {}


def _img_size(path):
    """(w, h) картинки или None, если не открылась (битый файл, видео, нет PIL)."""
    p = os.path.abspath(path or "")
    if p not in _IMG_SIZE_CACHE:
        try:
            from PIL import Image
            with Image.open(p) as im:
                _IMG_SIZE_CACHE[p] = im.size
        except ReelsiError: raise
        except Exception:
            _IMG_SIZE_CACHE[p] = None
    return _IMG_SIZE_CACHE[p]


def _ins_scale(media, style, comp_w=1080):
    """Масштаб фотовставки под «карточку». Фото в прекомпе тянется под ширину композа, значит
    видимая высота = ih*comp_w/iw. Не-ультравайд режется маской в квадрат — тогда видимая
    ширина равна видимой высоте. Размеры не прочитались -> старые дефолты."""
    wh = _img_size(media)
    if not wh or not wh[0] or not wh[1]:
        return 50 if style == "cam1" else 44
    iw, ih = wh
    vis_h = ih * (float(comp_w) / iw)                  # высота фото в прекомпе (тянуто под ширину)
    if vis_h <= 0:
        return 50 if style == "cam1" else 44
    vis_w = comp_w
    if comp_w / vis_h <= INS_MASK_SQUARE_AR:           # не ультравайд -> маска режет в квадрат
        vis_w = vis_h = min(comp_w, vis_h)
    card_h = INS_CARD_H if style == "cam1" else INS_CARD_H_CAM2
    return _r(min(INS_CARD_W / vis_w, card_h / vis_h) * 100, 1)


def _ins_card(media, style, mw, mh, sc, comp_w=1080, comp_h=1920):
    """Маска-карточка фотовставки в comp-координатах (px) в ОСЕВШЕМ масштабе:
    {w, h} — окно маски, {pw, ph} — само фото (тянуто под ширину композа, маска его режет).
    Раньше это считал предпросмотр (insPreviewBox) из размеров картинки — переехало в план
    сцены, чтобы JS не держал вторую копию. Размер не прочитался -> None
    (старое if(!iw||!ih) return)."""
    wh = _img_size(media)
    if not wh or not wh[0] or not wh[1]:
        return None
    iw, ih = wh
    photo_h = ih * (float(comp_w) / iw)                # высота фото в прекомпе (тянуто под ширину)
    vis_h, vis_w = photo_h, float(comp_w)
    if vis_h > 0 and comp_w / vis_h <= INS_MASK_SQUARE_AR:   # не ультравайд -> квадрат
        vis_w = vis_h = min(float(comp_w), vis_h)
    if vis_h <= 0 or vis_w <= 0:
        return None
    card_h = INS_CARD_H if style == "cam1" else INS_CARD_H_CAM2
    s = min(INS_CARD_W / vis_w, card_h / vis_h) * (float(sc or 100) / 100)
    vis_w = max(20, min(float(comp_w), vis_w * (float(mw or 100) / 100)))
    vis_h = max(20, min(photo_h, float(comp_h), vis_h * (float(mh or 100) / 100)))
    return {"w": _r(vis_w * s, 2), "h": _r(vis_h * s, 2),
            "pw": _r(comp_w * s, 2), "ph": _r(photo_h * s, 2)}


def _ins_plate(media, plate, style, sc, x, y, comp_w=1080, comp_h=1920, plate_scale=100.0):
    """Геометрия фото-вставки на подложке — плашка из стиля снизу,
    фото сверху, маски-скругления нет. Зовётся ТОЛЬКО для вставок с галкой «на подложке»
    (поле plate): у остальных прежний путь — карточка и маска.

    Подложка у всех таких вставок ОДНА и стоит одинаково: масштаб и положение со страницы
    вставок двигают только фото ВНУТРИ прекомпа, поэтому сдвиг уезжает не в точку покоя
    слоя (её считает шаблон), а в px/py прекомпа (экранные px делятся на масштаб слоя).
    Масштаб слоя прекомпа (scale) — плашка, вписанная в «карточку»: та же коробка, что
    у _ins_scale, только роль фото играет плашка.

    -> dict(scale, ps, px, py, card) или None, если подложки нет. Размеры плашки не
    прочитались — считаем её квадратной (plate_h_pc = comp_w): собрать вставку лучше,
    чем уронить её на битом файле. Размеров фото нет — ps=100 (не растягиваем).
    """
    if not plate:
        return None
    w = float(comp_w)
    pw, ph = (_img_size(plate) or (0, 0))
    plate_h_pc = (w * ph / pw) if (pw and ph) else w         # высота плашки в прекомпе
    card_h = INS_CARD_H if style == "cam1" else INS_CARD_H_CAM2
    s_l = min(INS_CARD_W / w, card_h / plate_h_pc) * 100 * (float(plate_scale or 100) / 100)
    k = s_l / 100.0
    box = INS_PLATE_INNER * min(w, plate_h_pc)               # коробка фото внутри плашки
    iw, ih = (_img_size(media) or (0, 0))
    ps = (min(box / iw, box / ih) * 100 * (float(sc or 100) / 100)) if (iw and ih) else 100.0
    # card — для предпросмотра: плашка и фото НА ЭКРАНЕ в осевшем масштабе, фото —
    # сдвигом от центра плашки (те самые ручные x/y со страницы вставок), px/ph не нужны
    return {"scale": _r(s_l, 2), "ps": _r(ps, 2),
            "px": _r(float(x or 0) / k, 2), "py": _r(float(y or 0) / k, 2),
            "card": {"w": _r(w * k, 2), "h": _r(plate_h_pc * k, 2), "plate": plate,
                     "photo": {"w": _r(iw * ps / 100 * k, 2), "h": _r(ih * ps / 100 * k, 2),
                               "x": _r(x or 0, 2), "y": _r(y or 0, 2)}}}


# Окна групп интро (ts/te) считает scene_plan — раньше это жило ДВУМЯ копиями:
# introGroupWindows в предпросмотре и inAt/outEnd в AE_FULL, и они уже разошлись
# (JS не учитывал max(gMax, inAt+F_DUR) для серединных групп). Константы — те же,
# что в template.py: без совпадения превью покажет не то окно.
# INTRO_F_OUT = 0.75 — длина ОКНА выхода: outEnd = outStart + 0.75. Сам фейд-аут
# короче (intro_fade, дефолт 0.35 с): по правкам пользователя в amdi1.aep момент
# полного исчезновения (outEnd) оставлен прежним, а начало фейда сдвинуто позже.
# Стоят ВЫШЕ _intro_group_window нарочно: INTRO_HOLD — её запасной аргумент (полка
# последней группы у прямых вызовов), а значение по умолчанию вычисляется при определении
# функции. Сборка всегда передаёт сюда ключ стиля intro_last_hold (дефолт его — та же 1.0).
INTRO_F_DUR, INTRO_HOLD, INTRO_F_OUT = 0.3, 1.0, 0.75


def _intro_group_window(times, gi, n_groups, intro_last_hold=INTRO_HOLD):
    """Окно группы интро (сек): (ts, te) — РОВНО формула inAt/outEnd из AE_FULL.
    times — моменты слов группы (сек, округлённые как в плане). gi — индекс группы,
    n_groups — их число.

    intro_last_hold — ключ стиля intro_last_hold (полка ПОСЛЕДНЕЙ группы после её
    последнего слова, с): >0 — последняя держится gMax + F_DUR + это число, как держалась
    на прежней константе INTRO_HOLD (дефолт 1.0 — .jsx прежний байт в байт); 0 — последняя
    считается РОВНО как непоследние: max(gMax, inAt + F_DUR). Отдельной ветки «последняя
    висит дольше» при нуле не остаётся — владелец: «Почему последнее интро всё ещё
    длинное» (подрезать последнюю под старт следующей не подо что, следующей нет).
    Формулу зовёт план (plan_intro) и прямые вызовы раскладки: второй копии нет, значение
    приезжает параметром."""
    gmin = min(times) if times else 0.0
    gmax = max(times) if times else 0.0
    in_at = 0.0 if (gi == 0 and gmin < 3) else gmin    # 1-я группа с 0 только если реально в начале
    hold = float(intro_last_hold) if gi == n_groups - 1 else 0.0
    out_start = (gmax + INTRO_F_DUR + hold) if hold > 0 \
        else max(gmax, in_at + INTRO_F_DUR)            # выход не раньше конца фейд-ина (см. шаблон)
    return _r(in_at), _r(out_start + INTRO_F_OUT)


INTRO_MIN_PART = 0.1    # пол ужатой анимации и ужатого фейда, с


def intro_clamp_window(in_at, t_last, out_start, out_end, fade, anim_dur, next_in):
    """Подрезать окно группы интро под старт следующей группы.

    Возвращает (out_start, out_end, fade). `next_in` — момент появления следующей
    группы (None у последней): позже него эта группа висеть не должна, иначе в кадре
    две группы разом. Не влезает анимация последнего слова вместе с фейдом — обе
    ужимаются одним множителем, каждая не короче INTRO_MIN_PART.
    """
    if next_in is None or out_end <= next_in:
        return _r(out_start), _r(out_end), _r(fade)
    avail = max(INTRO_MIN_PART * 2, next_in - t_last)
    need = anim_dur + fade
    if avail < need:
        k = avail / need
        fade = max(INTRO_MIN_PART, fade * k)
    out_end = next_in
    out_start = max(in_at + INTRO_F_DUR, out_end - fade)
    fade = max(INTRO_MIN_PART, out_end - out_start)
    return _r(out_start), _r(out_end), _r(fade)


# Геометрия вставок, раньше жила в AE_FULL (xml2ae/template.py) — переехала сюда, чтобы
# считаться в Python до сборки .jsx. Значения не менялись.
HL_EASE_OUT, HL_EASE_IN = 35, 90    # cubic-bezier(0.35,0.01,0.10,0.99)
EASE_DEFAULT = 33.3333              # Easy Ease по умолчанию (медленный откат большой←малый)
INS_ENTER, INS_EXIT = 0.38, 0.47    # вход/выход cam2-вставки, сек
# Появление жёлтого слова: подъём на HL_RISE, проявление и блюр играют за
# HL_DUR = 0.35 с, но короткое слово гаснет раньше, чем анимация доиграет (outPoint слоя
# = gend): в ролике владельца у 49 слов из 140 видимое время меньше 0.35 с, минимум
# 0.05 с — слово просто исчезало. Длительность d = min(HL_DUR, HL_FIT * видимое время):
# при HL_FIT = 0.6 слово стоит неподвижно хотя бы 40 % своей жизни. Число одно на всю
# сборку: шаблон получает HL_DUR подстановкой (template.py), план несёт его превью
# (hl_dur), а короткие слова — своё hd.
HL_DUR = 0.35
HL_FIT = 0.6


def hl_appear_dur(vis):
    """Длительность появления жёлтого слова, с: min(HL_DUR, HL_FIT*vis).
    vis — видимое время слова (outPoint − момент появления), с. Округление до десятых
    миллисекунды: столько же знаков, сколько у остальных чисел плана и .jsx."""
    return round(min(HL_DUR, HL_FIT * float(vis)), 4)
# Базовая позиция интро: превью ставит блок по plan.intro[i].y, шаблон
# берёт готовое iDy — позиция живёт в Python, вторая копия не заводится. Числа — из
# AE_FULL: iDy=0, iTop=H/2 - 520.7894 - (nL/2)*LINE_STEP*(iSc/100); if(iTop<SAFE_TOP)
# iDy=SAFE_TOP-iTop. iSc — базовый масштаб прекомпа 96.8 × gs/100.
INTRO_BASE_Y = 520.7894     # база позиции прекомпа интро, px (подъём от центра кадра)
INTRO_LINE_STEP = 160.0     # шаг строки внутри прекомпа, px (= 100% ключа intro_line_step)
INTRO_SAFE_TOP = 285.0      # верх блока не выше этой линии кадра — иначе опускаем
INTRO_SCALE = 96.8          # базовый масштаб прекомпа при gs=100, %%
# Запас автофита интро: строка не шире этой доли кадра даже на максимуме
# зума. Раньше константа жила в шаблоне (var INTRO_FIT_W), где считала от ширины
# прекомпа; с переездом автофита в scene_plan источник один — здесь.
INTRO_FIT_W = 0.92
# Затемнение под интро: числа сняты с amdi1.aep — пользователь в КАЖДОМ из
# четырёх роликов руками клал `Shape Layer 1` (мягкое чёрное затемнение снизу кадра, чтобы
# белый текст интро читался на светлой одежде). У всех четырёх одинаковы: прямоугольник
# 1416×1052, смещение прямоугольника внутри группы (−20, 610), чёрная заливка, обводки нет,
# Box Blur с радиусом 653. Разное — позиция слоя (−6…0, 441…606) и масштаб 87…103 %; здесь
# средние: SHADE_X = −4, SHADE_SCALE = 94. Позиция следовала за высотой интро (в среднем
# «y = INTRO_Y − 215», INTRO_Y — стиль intro_y), отсюда SHADE_DY. Слой висит на нуле
# «Камера 1» — координаты те же, что у нула «интро» (см. intro_y/intro_plan).
SHADE_REF_W = 1080                  # ширина композиций amdi1.aep, с которых сняты числа; остальные SHADE_* — пиксели кадра этой ширины
SHADE_W, SHADE_H = 1416, 1052       # размер прямоугольника, px
SHADE_OX, SHADE_OY = -20, 610       # смещение прямоугольника внутри группы, px
SHADE_BLUR = 653                    # радиус Box Blur, px
SHADE_X = -4                        # позиция слоя по X, px (среднее по роликам)
SHADE_DY = -215                     # позиция слоя: y = intro_y + SHADE_DY, px
SHADE_SCALE = 94                    # масштаб слоя, % (среднее по роликам)


def _intro_i_dy(h, n_lines, gs, step_k=1.0):
    """Опускание блока интро под INTRO_SAFE_TOP, px. Масштаб группы
    (96.8·gs/100) выбирает вызывающий: у ПРИВЯЗАННОГО интро это НЕужатый gs (граница
    — там автофит режет только Scale, и опускание от него не зависит), у
    ОТКРЕПЛЁННОГО — фактический ds, который автофит мог и увеличить:
    растянутое по ширине кадра интро обязано опуститься под SAFE_TOP, иначе вылезет
    за верх кадра. Точность как в старом
    шаблоне: без округления, чтобы .jsx не поехал на сотых.

    step_k — множитель межстрочного интервала (intro_line_step/100): высота
    блока растёт вместе с шагом, поэтому под SAFE_TOP его опускают по ТОМУ ЖЕ шагу, что
    стоит в раскладке строк. При 1.0 числа прежние."""
    i_sc = INTRO_SCALE * (float(gs) if gs else 100.0) / 100.0
    i_top = h / 2 - INTRO_BASE_Y - (n_lines / 2.0) * (INTRO_LINE_STEP * step_k) * (i_sc / 100.0)
    if i_top < INTRO_SAFE_TOP:
        return INTRO_SAFE_TOP - i_top
    return 0.0


def _cap(ps, size):
    """Высота заглавных (капитель) строки, px: верх «H» из контуров глифа —
    у заглавной нет ни хвоста, ни выносов, поэтому её верх и есть верх строки. Шрифта,
    файла или глифа нет — 0.72 кегля (та же запасная ветка, что была у чернил: раскладка
    обязана строиться и без файла шрифта). Вторая копия формулы не заводится — сюда
    смотрит и вертикаль большой строки, и верх блока."""
    ext = _fonts.ink_extent(ps, "H", size)
    if ext is None:
        return 0.72 * size
    return float(ext[0])


# Высота большого слова, % от высоты стопки (доработка ZY-2): дефолт ручки
# intro_big_over из styles.BASE. Сборка всегда передаёт число из стиля — здесь оно для
# прямых вызовов раскладки; совпадение с дефолтом ручки стережёт тест (test_intro_big.py),
# чтобы второе число не разъехалось с первым молча.
INTRO_BIG_OVER = 110.0


def intro_big_layout(lines, ys_stack, fsize, fonts, back_scale, gap,
                     over=INTRO_BIG_OVER):
    """Раскладка «большое слева»: ПЕРВАЯ строка группы с флагом big встаёт
    слева крупно, остальные строки группы — стопкой справа от неё, выровненные по левому
    краю. Возвращает (lx, lk, ys) — три списка ТОЙ ЖЕ длины, что lines:

    * ys — Y базовых линий в координатах прекомпа, по строке на элемент (большая строка
      садится на БАЗОВУЮ линию последней строки стопки, строки стопки — готовые из ys_stack);
    * lk — множитель кегля строки: у большой S/fsize, у строк стопки None;
    * lx — левый край строки, px прекомпа ОТ ЦЕНТРА: у всех строк группы с большой
      (и у большой, и у стопки — блок общий), у строк группы без большой None.

    Вертикаль — типографская, по ЗАГЛАВНЫМ и базовой линии (правка ZY), а не по чернилам:
    верх блока = y_first − cap(ps_first, size_first), низ = y_last — БАЗОВАЯ линия
    последней строки стопки. Кегль большой подобран под эту высоту с ручкой
    intro_big_over (доработка ZY-2): lk = over/100 · (y_last − верх)/cap(ps_big, fsize),
    и стоит она на той же базовой линии: y_big = y_last. При over=100 верх капители
    большой ровно совпадает с верхом блока, при дефолтных 110 — на 10 % выше стопки, как
    у эталона владельца. Чернила большой строки (asc/desc) в вертикали больше не
    участвуют: раньше низом считался низ ЧЕРНИЛ стопки, и хвост «Ц» в «ЗА МЕСЯЦ» (на
    7 px ниже базовой линии) утаскивал «8» вниз, а кратка «Й»/«Ё» так же портила верх.
    Кегль строки заднего плана — fsize·back_scale, как и прежде.

    Ширины — core.fonts.text_width (большая при S, стопка — каждая своим кеглем), блок
    центрирован: total = bigW + gap + max(stackW), lx_big = −total/2,
    lx стопки = −total/2 + bigW + gap.

    lines — строки группы ровно как уезжают в .jsx (поле big только у большой),
    ys_stack — Y базовых линий строк СТОПКИ (все, кроме большой: большая шаг не занимает,
    len(lines) − 1 значений). over — высота большого слова в % от высоты стопки (ручка
    intro_big_over, доработка ZY-2); дефолт INTRO_BIG_OVER держится равным
    styles.BASE["intro_big_over"] сторожем в тестах, сборка всегда передаёт число из стиля.
    Шрифт/глиф не найден (ink_extent/text_width дали None) —
    капитель 0.72 кегля, ширина 0.55 кегля на знак: раскладка строится и без файла шрифта
    (как автофит поступает с неизвестной шириной). Группа из одной строки большой не
    считается — флаг без эффекта, как и остальные big строки группы.
    """
    n = len(lines)
    if n <= 0:
        return [], [], []
    big_i = None
    for k in range(n):
        if lines[k].get("big"):
            big_i = k
            break
    if big_i is None or n < 2 or len(ys_stack) != n - 1:
        # Группа без большой строки (или раскладывать не с чем): флага нет ни у кого —
        # lx/lk пустые, ys отдаём как пришли (стопка целиком совпадает с lines).
        return ([None] * n, [None] * n,
                [round(float(y), 2) for y in ys_stack] if len(ys_stack) == n else [None] * n)

    def _text(i):
        return " ".join(str(w) for w in (lines[i].get("words") or []))

    def _size(i):
        """Кегль строки: задний план мельче, большая считается при fsize (её lk
        подбирается отдельно) — как в шаблоне, где back-скейл применяется к слою."""
        return fsize * back_scale if lines[i].get("back") else fsize

    def _width(i, size):
        wpx = _fonts.text_width(fonts[i], _text(i), size)
        if wpx is None:
            return 0.55 * size * len(_text(i))
        return float(wpx)

    stack_idx = [k for k in range(n) if k != big_i]
    y_first, y_last = float(ys_stack[0]), float(ys_stack[-1])
    # Верх блока — по капители ПЕРВОЙ строки стопки, низ — базовая линия ПОСЛЕДНЕЙ
    # Чернила тут не годятся: хвост «Ц» в «ЗА МЕСЯЦ» на 7 px ниже базовой
    # линии, и большая строка, выровненная по низу чернил, висела ниже строки.
    top = y_first - _cap(fonts[stack_idx[0]], _size(stack_idx[0]))
    cap_big = _cap(fonts[big_i], fsize)
    box_h = y_last - top
    # Кегль большой — от высоты стопки и ручки «Большое выше стопки» (доработка ZY-2):
    # over=100 — верх капители большой в верх блока, 110 (дефолт) — на десятую выше.
    lk = (float(over) / 100.0 * box_h / cap_big) if (box_h > 0 and cap_big > 0) else 1.0
    y_big = y_last                                  # та же базовая линия, что у стопки
    big_w = _width(big_i, fsize * lk)
    stack_w = [_width(k, _size(k)) for k in stack_idx]
    total = big_w + gap + (max(stack_w) if stack_w else 0.0)
    lx_big = -total / 2.0
    lx_stack = -total / 2.0 + big_w + gap

    lx, lks, ys = [None] * n, [None] * n, [None] * n
    lx[big_i], lks[big_i], ys[big_i] = lx_big, lk, y_big
    for j, k in enumerate(stack_idx):
        lx[k], lks[k], ys[k] = lx_stack, None, float(ys_stack[j])
    # Округление как у intro_line_ys: сотые — столько же знаков, сколько у остальных
    # чисел .jsx; lk — четыре знака (масштаб слоя в .jsx всё равно печатается десятыми
    # процента, k*100).
    return ([None if v is None else round(v, 2) for v in lx],
            [None if v is None else round(v, 4) for v in lks],
            [None if v is None else round(v, 2) for v in ys])


def intro_line_ys(lines, back_step, any_back_in_clip=True, anchor="center",
                  h=1920.0, step_k=1.0, back_step_after=None):
    """Y базовых линий строк интро в координатах прекомпа (высота h), по числу строк.

    Шаг ДО строки заднего плана и шаг МЕЖДУ строками заднего плана — line_step *
    back_step, шаг ПОСЛЕ блока заднего плана к обычной строке — line_step *
    back_step_after, где line_step = INTRO_LINE_STEP * step_k.
    Минимума по чернилам (fonts.ink_extent) и зазора back_gap здесь больше нет: минимум (73.5 px на дефолтном шрифте) перекрывал шаг на малых back_step, и
    ручка «не меняла ничего», а жёсткие 0.75 после строки заднего плана вообще не
    читались из стиля. Высоту букв из файла шрифта теперь не спрашивают вовсе — шаг
    целиком задаёт ручка.

    lines — строки группы ровно как уезжают в .jsx (поле back только у настоящей строки
    заднего плана: акцент его перебивает), back_step — шаг строк заднего плана долей от
    обычного шага, back_step_after — шаг от заднего плана к обычной строке под ним;
    None (ключа нет в стиле) — прежнее поведение: берётся back_step этого же стиля,
    старые стили выглядят как раньше байт в байт.

    any_back_in_clip: в ролике есть строки заднего плана. Нет их — все шаги LINE_STEP и
    базовая линия по центру: ветка шаблона без back другой раскладки не знает, шаг по
    back_step там смысла не имеет.

    anchor: "first" — первая строка стоит в h/2, добавленная строка опускает только
    нижние (блок не поднимается); "center" — как раньше: без back h/2 - (nL-1)/2*LINE_STEP,
    с back и головой не back — h/2 - (nL-1)*60*k, иначе h/2 - totH/2. Округление до сотых:
    столько же знаков, сколько у остальных чисел .jsx.

    step_k — множитель межстрочного интервала (intro_line_step/100): ОДИН на
    весь шаг строки — базовые 160 px, «60» центровки с back и шаг заднего плана.
    При 1.0 числа прежние байт в байт (golden).
    """
    n = len(lines)
    if n <= 0:
        return []
    line_step = INTRO_LINE_STEP * step_k

    def _back(i):
        return bool(lines[i].get("back"))

    steps = []
    for i in range(1, n):
        if not any_back_in_clip:
            steps.append(line_step)
            continue
        if _back(i) or _back(i - 1):
            # Шаг ПОСЛЕ блока заднего плана — своя ручка: видимые зазоры над
            # маленькой строкой и под ней разные (у владельца 53 и 23 px), а выровнять их
            # одним числом нельзя — высота букв и хвосты зависят от слов. Шаг ДО строки
            # заднего плана и между строками заднего плана остаётся общим back_step.
            step_frac = back_step
            if back_step_after is not None and _back(i - 1) and not _back(i):
                step_frac = back_step_after
            base = line_step * step_frac
        else:
            base = line_step
        steps.append(base)

    if anchor == "first":
        ys = [h / 2.0]
    elif not any_back_in_clip:
        ys = [h / 2.0 - (n - 1) / 2.0 * line_step]
    elif not _back(0) and n > 1:
        ys = [h / 2.0 - (n - 1) * 60.0 * step_k]
    else:
        ys = [h / 2.0 - sum(steps) / 2.0]
    for stp in steps:
        ys.append(ys[-1] + stp)
    return [round(y, 2) for y in ys]


# ---- Интро гаснет к субтитру, если стоит на его месте -------------------
# Разбор reelsi_batch.aep владельца: 69 из 76 правленых руками групп интро гаснут РОВНО
# в момент появления следующего субтитра (медиана отклонения 1 кадр), а длина затухания
# почти постоянная — 0.15 с. Сам он определил это на глаз и правил руками; здесь то же
# правило считает Python: группа, чей блок по вертикали лежит на полосе субтитров, гаснет
# к появлению следующего субтитра. Блок, уехавший по Y (полосы не касается), живёт как
# раньше — ровно это владелец и делал руками (группы, сдвинутые по Y, не трогал).
#
# Геометрия блока — те же числа, что у превью и шаблона: Y базовых линий (ys) и кегль
# строк в прекомпе, масштаб прекомпа INTRO_SCALE·ds/100, общий масштаб интро G, позиция
# группы (intro_y/intro_y2, iDy, dy) и зум Камеры 1 (пока интро к ней привязано).
# Второй копии формул здесь нет: sizes берёт правило «задний план/большая строка»
# (intro_line_sizes), верх строки — капитель (_cap), низ — выносные (_desc).
# Полоса субтитров — posy и кегль субтитров, у стопки жёлтых плюс её высота.


def intro_line_sizes(lines, fsize, back_scale=1.0, lk=None):
    """Кегль каждой строки интро, px: fsize у обычной строки,
    fsize·back_scale у строки заднего плана, fsize·lk у большой строки.

    Ровно это правило ставят шаблон (introBackScale/introBigScale) и превью
    (fontSize × bsc/lk) — своё здесь не заводится, иначе геометрия блока разошлась бы
    с тем, что видно на экране. Пустой/None список — пустой результат.
    """
    out = []
    for k, ln in enumerate(lines or []):
        k_lk = lk[k] if (lk and k < len(lk)) else None
        if k_lk is not None:
            out.append(float(fsize) * float(k_lk))
        elif (ln or {}).get("back"):
            out.append(float(fsize) * float(back_scale))
        else:
            out.append(float(fsize))
    return out


def _desc(ps, size):
    """Глубина выносных под базовой линией, px: низ «y» из контуров глифа.
    Файла шрифта или глифа нет — 0.24 кегля (та же запасная ветка, что у капители: блок
    обязан считаться и без файла шрифта — иначе группа молча перестала бы гаснуть)."""
    ext = _fonts.ink_extent(ps, "y", size)
    if ext is None:
        return 0.24 * size
    return float(ext[1])


def intro_block_span(ys, sizes, h, ds=100.0, g=100.0, y=0.0, dy=0.0,
                     zoom=100.0, intro_cam=True, fonts=None):
    """(top, bottom) — вертикальный габарит блока интро в кадре, px от верха кадра.
    Верх блока — по капители самой верхней строки, низ — по выносным
    нижней (вертикаль типографская, как в intro_big_layout).

    ys — Y базовых линий строк в координатах прекомпа высотой h; sizes — их кегли
    (intro_line_sizes); fonts — PostScript-имена строк (None/короче — запасная ветка).
    ds — масштаб группы (поле ds плана), g — общий масштаб интро (intro_scale),
    y — позиция блока от центра кадра (plan.intro[].y, уже с G·(−INTRO_BASE_Y+iDy)),
    dy — смещение группы (умножается на G, как в шаблоне и превью), zoom — зум Камеры 1
    в процентах на нужный момент; intro_cam=False (интро откреплено) — зум
    не применяется ни к размеру, ни к позиции. Пустые ys — (0, 0)."""
    if not ys:
        return 0.0, 0.0
    _sizes = list(sizes or [])
    _fonts = list(fonts or [])
    top_pc = bot_pc = None
    for i, yv in enumerate(ys):
        if yv is None:
            continue
        size = float(_sizes[i]) if i < len(_sizes) else 0.0
        ps = _fonts[i] if i < len(_fonts) else None
        t_i = float(yv) - (_cap(ps, size) if size else 0.0)
        b_i = float(yv) + (_desc(ps, size) if size else 0.0)
        top_pc = t_i if top_pc is None else min(top_pc, t_i)
        bot_pc = b_i if bot_pc is None else max(bot_pc, b_i)
    if top_pc is None:
        return 0.0, 0.0
    zk = (float(zoom) / 100.0) if intro_cam else 1.0
    center = h / 2.0 + zk * (float(y) + float(g) / 100.0 * float(dy))
    # Масштаб прекомпа: 96.8·ds/100 (то же iSc, что в шаблоне и в ipvIntroPos), × G
    # (масштаб нула «интро») и × зум Камеры 1, пока интро на ней.
    k = (INTRO_SCALE / 100.0) * (float(ds) / 100.0) * (float(g) / 100.0) * zk
    return (center + (top_pc - h / 2.0) * k, center + (bot_pc - h / 2.0) * k)


def sub_span(posy, size, font=None, row=0, step=0.0):
    """(top, bottom) — вертикальная полоса субтитров в кадре, px от верха кадра.
    posy — Y базовой линии строки (plan.posy), size — кегль субтитров, row/step — ряд
    стопки жёлтых (её слова идут ниже на шаг hl_step, как в preview ipvSubs); верх — по
    капители, низ — по выносным. size=0 — (posy, posy)."""
    if not size:
        return float(posy), float(posy)
    top = float(posy) - _cap(font, size)
    bot = float(posy) + float(row or 0) * float(step or 0.0) + _desc(font, size)
    return top, bot


def spans_hit(a, b):
    """Пересекаются ли две вертикальные полосы. Касание краями — не
    пересечение: блок, чей низ ровно на верхе полосы субтитров, на неё не заходит."""
    return a[0] < b[1] and b[0] < a[1]


def intro_hits_subs(ys, sizes, posy, sub_size, h=1920.0, ds=100.0, g=100.0,
                    y=0.0, dy=0.0, zoom=100.0, intro_cam=True, fonts=None,
                    sub_font=None, sub_row=0, sub_step=0.0):
    """Стоит ли блок интро на полосе субтитров, True/False.

    Одна дверь для решения «группа гаснет к появлению следующего субтитра»: сборка
    зовёт её на готовых числах плана (ys/кегли/ds/y/dy) и на полосе субтитров
    (posy/кегль/ряд стопки), превью и .jsx своей копии не держат вовсе."""
    return spans_hit(intro_block_span(ys, sizes, h, ds=ds, g=g, y=y, dy=dy,
                                      zoom=zoom, intro_cam=intro_cam, fonts=fonts),
                     sub_span(posy, sub_size, font=sub_font, row=sub_row, step=sub_step))


def intro_sub_window(in_at, te, next_sub, sub_fade, f_in=INTRO_F_DUR):
    """(te, fade, fade_start) — окно группы, гаснущей к появлению субтитра.

    Группа гаснет РОВНО к next_sub: te = next_sub, затухание sub_fade секунд, начало
    затухания next_sub − sub_fade, но не раньше конца фейд-ина группы (in_at + F_DUR) —
    иначе затухание начиналось бы раньше, чем блок успел появиться. Окна не хватает —
    затухание короче (вплоть до нуля: появление субтитра на самом фейд-ине — жёсткий
    срез). Числа те же, что у обычного окна: их читают и план (превью), и шаблон."""
    fstart = max(float(next_sub) - float(sub_fade), float(in_at) + float(f_in))
    return (float(next_sub), max(0.0, float(next_sub) - fstart), fstart)


# Анимации вставок, раньше жили константами в AE_FULL (правка .jsx руками влияла на
# сборку): переехали сюда вместе с расчётом ключей — у анимации один источник.
INS_BLUR = 41.0                     # Box Blur cam2-вставки, px (полный блюр на входе)
INS_RISE_DY, INS_RISE_S0, INS_RISE_ENTER = 132.0, 0.706, 0.5   # анимация «rise»: подъём px, доля целевого масштаба на старте, вход сек
INS_C2_PEAK, INS_C2_BASE = 100.0, 44.0  # cam2: пик наезда = осевший scale * PEAK/BASE
INS_C2_Y_FR = 0.172                 # cam2: Y точки покоя в долях H (≈330 при 1920)
INS_C1_UP, INS_C1_DN = 0.68, 0.80   # cam1 («из-за спины»): время подъёма над спиной и спуска
INS_C1_LOW, INS_C1_HIGH = 464.7, 567.0  # cam1: нижняя точка старта и высота вылета, px
# Тень плашки под субтитрами: чёрная, снята из adcut.aep
SUB_BG_SH_OP = 173.4                # Opacity (0..255 в AE, 68%)
SUB_BG_SH_DIR = 181.0               # Direction (градусы)
SUB_BG_SH_DIST = 5.0                # Distance (px)
SUB_BG_SH_SOFT = 44.0               # Softness (px)


def _media_dims(path):
    """(w, h) видео КАК ПОКАЗЫВАЕТСЯ (с учётом поворота — как item.width/height в AE)
    или None, если файла нет/размер не прочитался. Тогда вставку не трогаем вовсе —
    то же, что if(!iw||!ih) return в старом JS."""
    if not path or not os.path.isfile(path):
        return None
    try:
        from core import draftrender  # lazy: только для видео-вставок
        w, h, _rot = draftrender._display_dims(path)
        return (w, h) if w and h else None
    except ReelsiError: raise
    except Exception:
        return None


def _fit_scale(iw, ih, fill, comp_w, comp_h, k):
    """Масштаб видеовставки, %: fill=True — заполнение экрана (видео всегда так),
    иначе — ужать, если больше кадра. k = ручной масштаб (доля от авто).
    None, если размеров нет (старое if(!iw||!ih) return)."""
    if not iw or not ih:
        return None
    f = (max(comp_w / iw, comp_h / ih) if fill else min(comp_w / iw, comp_h / ih, 1)) * (k or 1)
    return f * 100


def _fill_slack(iw, ih, comp_w, comp_h, k):
    """На сколько px заполняющий кадр ролика вылезает за кадр композиции, в каждую сторону.
    16:9 ролик, вписанный по высоте, вылезает по ширине на ~2300 px (есть что панорамировать),
    а по высоте запаса нет вовсе; ужатая вставка (k<1) кадр не заполняет — вылет нулевой.
    -> (slack_x, slack_y); (0,0), если размеров нет.

    Это СПРАВКА (план несёт её полями slackx/slacky), а НЕ граница позиции: x/y
    видеовставки двигают свободно при любом масштабе — уехав за край
    ролика, вставка открывает кадр камеры, как и в AE. Раньше этим запасом сдвиг
    зажимали, и вертикальное 9:16 (вылета нет ни по одной оси) не двигалось совсем."""
    if not iw or not ih:
        return 0.0, 0.0
    f = max(comp_w / iw, comp_h / ih) * (k or 1)
    return max(0.0, (iw * f - comp_w) / 2), max(0.0, (ih * f - comp_h) / 2)


def _ins_enter_exit(t0, t1, noexit, fps, enter=INS_ENTER, exit_=INS_EXIT):
    """Окна входа/выхода cam2-вставки, сек. Окно короче анимации входа (ИИ округляет
    тайминги до 0.1с, _clip_end умеет подрезать фото под кат) — ключи выходили ИЗ
    ПОРЯДКА: ужимаем вход под факт. -> (en, ex); короче кадра — (0, win), показ без
    анимации (guard win<1/FPS остаётся в JS, он знает win)."""
    win = max(0.0, t1 - t0)
    if win < 1 / float(fps):
        return 0.0, win
    en = min(enter, win if noexit else win * 0.45)
    ex = min(exit_, max(0.0, win - en))
    return en, ex


def _anim_keys(t0, t1, va, vb, noexit, en, ex, fps):
    """Ключи анимации «вход va->vb, держим vb, выход vb->va» (то, что ставил JS-функцией
    fourKeys). Guard win<1/FPS и ветка noexit жили в ExtendScript — перенесены сюда
    (остаток): превью читает эти же ключи из плана сцены и не повторяет расчёт.
    Окно короче кадра — один ключ (показать vb без анимации); noexit — конец жёсткий.
    Числа округлены как в JSX (_r), чтобы превью и .jsx были биективны."""
    win = max(0.0, t1 - t0)
    if win < 1 / float(fps):
        return [[_r(t0), _r(vb)]]
    keys = [[_r(t0), _r(va)], [_r(t0 + en), _r(vb)]]
    if noexit:
        keys.append([_r(t1), _r(vb)])
    else:
        keys.append([_r(max(t0 + en, t1 - ex)), _r(vb)])
        keys.append([_r(t1), _r(va)])
    return keys


def _blur_keys(t0, t1, noexit):
    """Ключи Box Blur cam2-вставки: резкость входит за INS_ENTER, уходит за INS_EXIT.
    БЕЗ guard'а — в старом JS его здесь не было: на окне короче кадра ключи встают за
    концом слоя и не показываются, это безопасно. Повторяем старое поведение буквально."""
    keys = [[_r(t0), INS_BLUR], [_r(t0 + INS_ENTER), 0.0]]
    if noexit:
        keys.append([_r(t1), 0.0])
    else:
        keys.append([_r(max(t0 + INS_ENTER, t1 - INS_EXIT)), 0.0])
        keys.append([_r(t1), INS_BLUR])
    return keys


def _cam1_pos_keys(t0, t1, noexit, ix, iy, fps, cx=0.0, cy=0.0):
    """Ключи Position cam1-вставки «вылет из-за спины» (то, что ставил JS по INS_C1_*).
    Подъём/спуск сжимаются под окно: потолок 30 кадров на фазу — на коротком окне вылет
    не успевал начаться и фото не появлялось в кадре. cx/cy — точка покоя родительского
    нула (0,0 у «вставки кам1»/«вставки кам1 на кам2» — они существуют всегда).
    Старт dn БЕЗ ix/iy, как в JS: сдвиг точки покоя применяется уже над нулём."""
    iwin = max(0.0, t1 - t0)
    c1up = min(INS_C1_UP, 30.0 / float(fps), iwin if noexit else iwin * 0.45)
    c1dn = min(INS_C1_DN, 30.0 / float(fps), max(0.0, iwin - c1up))
    hs = t0 + c1up
    he = t1 if noexit else max(hs, t1 - c1dn)
    up = [_r(cx + ix), _r(cy - INS_C1_HIGH + iy)]
    dn = [_r(cx), _r(cy + INS_C1_LOW)]
    keys = [[_r(t0), dn], [_r(hs), up]]
    if noexit:
        keys.append([_r(t1), up])
    else:
        keys.append([_r(he), up])
        keys.append([_r(t1), dn])
    return keys


# Зум Null Камеры 1.
# • МУЛЬТИКАМ: авто — на каждом ВОЗВРАТЕ кам2→кам1 (конец включённой перебивки = кадр среза,
#   где кам1 снова показывается) «большой» кейфрейм ровно в срезе, через ZOOM_PUNCH кадров «малый»;
#   плюс ВСЕГДА такой импульс в самом начале (кадр 0). Между импульсами — медленный дрейф к большому.
# • ОДНА КАМЕРА: фиксированный паттерн DEFAULT_CAM1_SCALE (медленный наезд → сброс на кате),
#   правится вручную в AE под конкретное видео.
ZOOM_BIG, ZOOM_SMALL, ZOOM_PUNCH = 182.0, 100.0, 62   # 182%→100% за 62 кадра (bezier 0.35,0.01,0.10,0.99)
# Сняты с эталона C1456-011: заход 98 кадров, наезд 96, отъезд 88 при 60 fps
TAKE_LEAD_S = 1.6
TAKE_IN_S = 1.6
TAKE_OUT_S = 1.5
TAKE_TAIL_S = 2.0
# Подъезд не начинается раньше 0.3 с после ката — иначе он сливается с самим скачком
TAKE_YELLOW_MIN_LEAD_S = 0.3

DEFAULT_CAM1_SCALE = [
    (0, 100), (562, 110.9), (987.004, 125), (1074, 100),
    (2169, 139), (2231.01, 100), (2969, 139), (3292, 100),
    (4060, 139), (4383, 100), (4931.03, 125), (5018.03, 100), (5263.03, 116),
]


def _cam1_zoom_keys(cams, big=ZOOM_BIG, small=ZOOM_SMALL, punch=ZOOM_PUNCH,
                    lo=112.0, hi=140.0, fps=60.0, start=True):
    """Кейфреймы зума Null Камеры 1. Мультикам -> импульс-наезд на каждом ВОЗВРАТЕ кам2→кам1
    (кадр среза, где снова показывается кам1) + импульс в самом начале (кадр 0).
    ПЕРВЫЙ импульс = `big` (крупный наезд в начале), последующие возвраты = случайный пик
    в [lo,hi]% (не 182 каждый раз — юзер задавал интервал), всегда откат к `small` (100).
    Если start=False — в кадре 0 один ключ (0, 100) без наезда и отката.
    Одна камера (перебивок нет) -> фиксированный DEFAULT_CAM1_SCALE (кадры под 60fps,
    на другом fps пересчитываются). -> [(frame, percent), ...]."""
    returns = _cam1_return_frames(cams)              # срезы перебивка→кам1 (реальные смены кадра)
    if not returns:                                              # одна камера — фикс-паттерн
        k = float(fps) / 60.0
        return [(round(f * k, 2), v) for f, v in DEFAULT_CAM1_SCALE]
    import random as _rnd
    frames = sorted(set([0] + returns))
    rng = _rnd.Random(",".join(str(f) for f in frames))          # тот же таймлайн -> тот же разброс
    keys, prev = [], None
    for idx, t in enumerate(frames):
        if idx == 0:                                             # первый наезд — крупный
            if not start:
                keys.append((t, small))
                continue
            peak = big
        else:                                                    # возвраты — интервал lo..hi, соседи различимы
            for _ in range(8):
                peak = round(rng.uniform(lo, hi), 1)
                if prev is None or abs(peak - prev) >= 8:
                    break
            prev = peak
        keys.append((t, peak))                                   # пик ровно в срезе (возврат на кам1)
        keys.append((t + punch, small))                          # откат к 100 за punch кадров
    return keys


def _cam1_jump_keys(cams, lo=100.0, hi=140.0, min_diff=12.0, fps=60.0, start=True,
                    big=ZOOM_BIG, punch=ZOOM_PUNCH, take=None):
    """Джамп-кат зум кам1: на КАЖДОЙ смене показываемой камеры скейл ПРЫГАЕТ на случайное
    значение 100–140%% (HOLD-кейфреймы).
    Кадр 0 со start=True: плавный наезд big→100 за punch кадров (ease out 35 / in 90).
    Ноль — всегда 100 %: «сначала идёт зум, который я указываю, и он доходит ВСЕГДА до 100 %».
    Раньше первый сегмент получал случайное число из [lo, hi], и у стиля с big == lo == 100
    наезд в начале ролика ехал НАОБОРОТ (100 → lo..hi). Если big == 100 (наезжать некуда) —
    один ключ 100 %: второй был бы его дублем на punch кадре.
    Если start=False: в кадре 0 значение 100.0 без наезда.
    В длинных тейках (seg_end - f >= take['min_s']*fps) при переданном take:
    плавный подъезд к v*m, удержание take['hold_s'] с, отъезд обратно к v (если влезает до ката).
    Возвращает 4-элементные ключи: [(frame, pct, mode, hold), ...].
    Детерминировано (seed от кадров реза)."""
    import random as _rnd
    fps = int(round(float(fps) or 60))
    big = float(big if big is not None else ZOOM_BIG)
    small = 100.0                                 # то же, что ZOOM_SMALL у pulse: возврат всегда в 100 %
    punch = int(punch if punch is not None else ZOOM_PUNCH)
    frames = sorted(set([0] + _zoom_cut_frames(cams, fps=fps)))  # ТОЛЬКО реальные срезы кадра
    dur = max((cl[1] for c in cams for cl in c["clips"] if cl[4]), default=frames[-1] + fps)
    seg_ends = frames[1:] + [max(dur, frames[-1] + 1)]           # конец каждого интервала (=след. смена / конец видео)
    rng = _rnd.Random(",".join(str(f) for f in frames))          # тот же таймлайн -> тот же разброс
    trng = _rnd.Random("t," + ",".join(str(f) for f in frames)) if take else None
    keys, prev = [], None
    for idx, f in enumerate(frames):
        seg_end = int(round(seg_ends[idx]))
        if idx == 0:
            # Нулевой сегмент — исключение: он ВСЕГДА живёт на 100 % («сначала идёт зум,
            # который я указываю, и он доходит ВСЕГДА до 100 %»). Раньше здесь стояло
            # случайное число из [lo, hi], и у стиля с big = lo = 100 наезд в начале ролика
            # ехал НАОБОРОТ: камера отъезжала от 100 к lo..hi вместо наезда.
            v = small
        else:
            for _ in range(8):
                v = round(rng.uniform(lo, hi), 1)
                if prev is None or abs(v - prev) >= min_diff:
                    break
        prev = v

        if idx == 0:
            if not start or big == small:
                # start=False — наезда нет. big == small — наезжать некуда: второй ключ
                # (pe, small) был бы дублем первого, то есть в AE два одинаковых ключа
                # подряд с пустым разгоном между ними (стиль «мясников»: big = lo = 100).
                keys.append((f, small, 0, 1))
                pe = None
            else:
                pe = min(punch, max(1, seg_end - 2))
                keys.append((0, big, 1, 0))
                keys.append((pe, small, 2, 1))
        else:
            keys.append((f, v, 0, 1))
            pe = None

        # Наезд в тейке (только если take не None и seg_end - f >= take["min_s"]*fps)
        if take is not None and (seg_end - f) >= take["min_s"] * fps:
            m = 1.0 + trng.uniform(take["lo"], take["hi"]) / 100.0
            vm = round(v * m, 1)
            take_words = take.get("words")
            picked_w = None
            if take_words:
                min_lead = round(TAKE_YELLOW_MIN_LEAD_S * fps)
                in_frames = round(TAKE_IN_S * fps)
                for w in take_words:
                    if f < w < seg_end:
                        cand_b = w
                        cand_a = cand_b - in_frames
                        if cand_a >= f + min_lead and (idx != 0 or not start or pe is None or cand_a >= pe + 1) and cand_b <= seg_end - 2:
                            picked_w = w
                            a = cand_a
                            b = cand_b
                            break
            if picked_w is None:
                a = f + round(TAKE_LEAD_S * fps)
                if idx == 0 and start and pe is not None:
                    a = max(a, pe + 1)
                b = a + round(TAKE_IN_S * fps)
            if b <= seg_end - 2:
                keys.append((a, v, 1, 0))
                keys.append((b, vm, 2, 1))
                c = b + round(take["hold_s"] * fps)
                d = c + round(TAKE_OUT_S * fps)
                if d <= seg_end - round(TAKE_TAIL_S * fps):
                    keys.append((c, vm, 1, 0))
                    keys.append((d, v, 2, 1))
    return keys


# Автоужимание дисклеймера: самая длинная строка не шире этой доли ширины
# кадра. Число — замер, а не вкус: у SF Pro Condensed при кегле 47 (int(H·0.0245) при
# 1920) самая длинная строка занимала 1071 px при кадре 1080, то есть ровно 0.992.
# С Oswald-Bold та же строка при 47 давала 1194 px — за краем кадра; кегль считается
# под эту долю, поэтому после смены шрифта дисклеймер ужимается сам, а не вылезает.
DISC_FIT_W = 0.992


# \n = перенос строки (в AE станет \r); центрируется как в проекте
DEFAULT_DISCLAIMER = ("МАТЕРИАЛ НОСИТ ИСКЛЮЧИТЕЛЬНО ОБРАЗОВАТЕЛЬНЫЙ\n"
                      "ХАРАКТЕР. НЕ ЯВЛЯЕТСЯ МЕДИЦИНСКОЙ РЕКОМЕНДАЦИЕЙ,\n"
                      "ПРИМЕНЕНИЕ ПРЕПАРАТОВ ТОЛЬКО\n"
                      "ПО НАЗНАЧЕНИЮ ВРАЧА")


def _project_base(xml_path):
    p = os.path.dirname(os.path.abspath(xml_path))
    return os.path.dirname(p) if is_out_dir(os.path.basename(p)) else p


def _stack_layout(subs, hl, breaks=None, joins=None):
    """For highlighted words that are ADJACENT in reading order, assign a stack row
    (0,1,2,...) and a shared group-end frame (whole stack disappears together).
    `breaks` = set of word indices AFTER which the stack restarts (ручной разделитель
    «палочка» между жёлтыми — начать стопку заново).
    `joins` = set of word indices AFTER which the word is joined with next word
    in the same row (склейка в строку). Returns rows[], gend[]."""
    breaks = breaks or set()
    joins = (joins or set()) - breaks
    n = len(subs)
    rows = [0] * n
    gend = [e for (s, e, w) in subs]
    i = 0
    while i < n:
        if i in hl:
            j = i
            while j + 1 < n and (j + 1) in hl and j not in breaks:
                j += 1
            run_end = max(subs[k][1] for k in range(i, j + 1))
            cur_r = 0
            for k in range(i, j + 1):
                rows[k] = cur_r
                gend[k] = run_end
                if k not in joins:
                    cur_r += 1
            i = j + 1
        else:
            i += 1
    return rows, gend


def _censor_windows(subs, fps):
    """Audio-mute windows (sec) for the middle letter of each censored subtitle word.
    A word is censored if it carries a '*' (from censor.py or manual edit)."""
    wins = []
    for s, e, w in subs:
        n = len(w or "")
        if not n or "*" not in (w or ""):
            continue
        m = w.index("*")
        dur = e - s
        if dur <= 0:
            continue
        wins.append(((s + dur * m / n) / fps, (s + dur * (m + 1) / n) / fps))
    return wins


def _cam_overlaps(clips, sf, ef, fps, ci):
    """Все клипы камеры `ci`, пересекающиеся с окном [sf,ef] (кадры). Для каждого — участок
    ИСХОДНИКА, реально показанный там (учёт source in-point), и место на таймлайне.
    -> [dict(ci, tl_start, tl_end, src_start, src_end, scale)] (сек). Скрытые клипы cam2+ (не
    показываемые перебивки) пропускаются."""
    out = []
    for cl in clips:
        cs, ce, cin, en, sc = cl[0], cl[1], cl[2], cl[4], cl[5]
        if ci > 0 and not en:                      # скрытый клип 2-й+ камеры — не в кадре
            continue
        a = max(sf, cs); b = min(ef, ce)
        if b <= a:
            continue
        out.append(dict(ci=ci, tl_start=a / fps, tl_end=b / fps, scale=sc,
                        src_start=(cin + (a - cs)) / fps, src_end=(cin + (b - cs)) / fps))
    return out


def cover_sweep(raw):
    """Кто виден на каждом элементарном отрезке таймлайна: [(b0, b1, k), ...], где k —
    ИНДЕКС победившего клипа в raw. Побеждает верхняя дорожка (максимальный ci), при
    равном ci — тот, что раньше в raw (то есть раньше в XML).

    raw — [(start, end, ci, ...), ...]; поля после ci игнорируются, их разбирает
    вызывающий. Единица времени любая (кадры или секунды), лишь бы одна на весь список.

    Было O(R²): на каждую из ~2R границ пересматривался ВЕСЬ список клипов. На вертикалке
    в пару сотен клипов это незаметно, но час лекции с тысячами клипов давал секунды на
    КАЖДЫЙ вызов — а зовут это и раскладка, и предпросмотр, и черновик. Заметающая прямая
    даёт тот же ответ за O(R log R): границы — это все концы клипов, поэтому «клип
    покрывает [b0, b1]» (s <= b0 и e >= b1) равносильно «s <= b0 < e», а это обычный
    интервал активности.
    """
    if not raw:
        return []
    order = sorted(range(len(raw)), key=lambda k: raw[k][0])   # клипы по началу — для входа
    bounds = sorted({t for cl in raw for t in (cl[0], cl[1])})
    exp = []                    # куча (конец, индекс): когда клип перестаёт быть активным
    live = {}                   # ci -> множество индексов активных клипов этой камеры
    out, p = [], 0
    for b0, b1 in zip(bounds, bounds[1:]):
        while p < len(order) and raw[order[p]][0] <= b0:       # вошли в кадр
            k = order[p]
            p += 1
            heapq.heappush(exp, (raw[k][1], k))
            live.setdefault(raw[k][2], set()).add(k)
        while exp and exp[0][0] <= b0:                         # вышли (конец не включаем)
            _e, k = heapq.heappop(exp)
            live[raw[k][2]].discard(k)
        top = max((ci for ci, ks in live.items() if ks), default=None)
        if top is None:                                        # дырка в таймлайне
            continue
        out.append((b0, b1, min(live[top])))
    return out


def _show_segments(cams):
    """Склеенные сегменты ПОКАЗА (какая камера реально видна): [(start, end, ci), ...].
    Верхняя включённая дорожка побеждает; смежные куски одной камеры склеены (VAD-стыки
    внутри камеры — НЕ смена кадра)."""
    raw = []
    for ci, c in enumerate(cams):
        for cl in c["clips"]:
            if cl[4] and cl[1] > cl[0]:            # только включённые (реально видимые) клипы
                raw.append((cl[0], cl[1], ci))
    segs = []                                       # склеенные сегменты показа: (start, end, ci)
    for b0, b1, k in cover_sweep(raw):
        ci = raw[k][2]
        if segs and segs[-1][2] == ci and abs(segs[-1][1] - b0) < 1e-6:
            segs[-1] = (segs[-1][0], b1, ci)        # та же камера подряд — склеиваем
        else:
            segs.append((b0, b1, ci))
    return segs


def _cam_change_frames(cams):
    """Кадры, где меняется ПОКАЗЫВАЕМАЯ камера (срезы кадра). -> [frame, ...] по возрастанию."""
    segs = _show_segments(cams)
    return [segs[k][0] for k in range(1, len(segs)) if segs[k][2] != segs[k - 1][2]]


def _zoom_cut_frames(cams, fps=60.0, min_gap_sec=2.0):
    """Кадры срезов для зум-стилей: смены показываемой камеры ПЛЮС собственные склейки кам1
    (границы её клипов) там, где смен камеры нет. Однокамерный проект раньше уходил в случайные
    интервалы 4–7 c — кейфреймы вставали посреди кадра, а не на срезах. Слишком частые срезы
    прореживаем: между соседними ключами держим min_gap_sec, иначе зуму негде дрейфовать.
    -> [frame, ...] по возрастанию, без 0 (его добавляет вызывающий)."""
    fps = float(fps) or 60.0
    changes = set(_cam_change_frames(cams))          # смены камеры — берём ВСЕ, даже короткие перебивки
    glue = {cl[0] for cl in cams[0]["clips"]         # собственные склейки кам1 = тоже срезы кадра
            if cl[4] and cl[0] > 0} - changes
    gap, out, last = max(1.0, float(min_gap_sec)) * fps, [], 0
    for f in sorted(changes | glue):
        if f in changes or f - last >= gap:          # склейки кам1 прореживаем: зуму нужно где дрейфовать
            out.append(f); last = f
    return out


def _cam1_return_frames(cams):
    """Кадры, где показ ВОЗВРАЩАЕТСЯ на Камеру 1 (срез перебивка→кам1). Именно тут ставится
    импульс pulse-зума. Раньше брались концы ВСЕХ клипов cam2+ — при ручной раскладке смежные
    куски одной перебивки давали кейфрейм ПОСРЕДИ показа камеры, не на срезе."""
    segs = _show_segments(cams)
    return [segs[k][0] for k in range(1, len(segs)) if segs[k][2] == 0 and segs[k - 1][2] != 0]


def _cam1_drift_keys(cams, lo=100.0, hi=160.0, min_diff=10.0, fps=60.0,
                     big=None, punch=None, start=True):
    """Дрейф-зум кам1. ВСЕ ключи — ease (безье), никаких HOLD: «скачок» на кате получается
    из двух соседних ключей (цель дрейфа за 1 кадр до среза + новое значение в срезе) —
    интерполяция за 1 кадр читается как резкий скачок. Старт — как у pulse: big (182)
    в кадре 0 с фирменным ease-откатом к 100 за punch кадров, дальше между катами
    постоянный лёгкий дрейф к случайной цели (если start=False — старт со 100%, без наезда).
    -> [(frame, percent, mode), ...], mode: 1 = стартовый ключ big (out = ease как у pulse) ·
    2 = приход отката в 100 (in = ease как у pulse) · 0 = обычный Easy Ease.
    Кадры ключей — ТОЛЬКО реальные срезы (смены камеры + склейки кам1), см. _zoom_cut_frames.
    Детерминировано (seed от кадров реза)."""
    import random as _rnd
    fps = int(round(float(fps) or 60))
    big = float(big if big is not None else ZOOM_BIG)
    punch = int(punch if punch is not None else ZOOM_PUNCH)
    frames = sorted(set([0] + _zoom_cut_frames(cams, fps=fps)))  # ТОЛЬКО реальные срезы кадра
    dur = max((cl[1] for c in cams for cl in c["clips"] if cl[4]), default=frames[-1] + fps)
    seg_ends = frames[1:] + [max(dur, frames[-1] + 1)]           # конец каждого интервала (=след. смена / конец видео)
    rng = _rnd.Random("d," + ",".join(str(f) for f in frames))   # тот же таймлайн -> тот же разброс
    keys, prev = [], [None]

    def _pick():
        for _ in range(8):
            x = round(rng.uniform(lo, hi), 1)
            if prev[0] is None or abs(x - prev[0]) >= min_diff:
                break
        prev[0] = x
        return x

    for i, f in enumerate(frames):
        seg_end = int(round(seg_ends[i]))
        tgt = max(f + 1, seg_end - 1)                            # за 1 кадр до следующей смены
        if i == 0:                                               # старт: big -> фирменный ease-откат к 100 -> дрейф
            if not start:
                keys.append((f, 100.0, 0))
                prev[0] = 100.0
                if tgt > f:
                    keys.append((tgt, _pick(), 0))
                continue
            pe = min(f + punch, max(f + 1, tgt - 2))             # откат сжимается, если срез раньше punch
            keys.append((f, big, 1))                             # out = ease как у pulse (35)
            keys.append((pe, 100.0, 2))                          # in = ease как у pulse (90)
            prev[0] = 100.0
            if tgt > pe + 1:
                keys.append((tgt, _pick(), 0))                   # дрейф 100 -> w до кадра перед срезом
            continue
        v = _pick()
        keys.append((f, v, 0))                                   # новое значение в срезе: 1 кадр от цели = «скачок»
        if tgt > f:
            keys.append((tgt, _pick(), 0))                       # цель дрейфа перед следующим срезом
    return keys


def _zoom_key_eases(keys):
    """[[in, out], ...] — влияние ease на КАЖДЫЙ ключ зума Камеры 1 (для JS-цикла).
    pulse (2-элементные ключи): фирменная кривая только на участках большой→малый —
    out 35 у текущего, если следующий меньше; in 90, если предыдущий больше (как
    смотрел соседей JS). drift (3-элементные, mode): 1 = out 35 (старт big),
    2 = in 90 (приход в 100), 0 = Easy Ease. Длина совпадает с CAM1_SCALE."""
    out = []
    for i, k in enumerate(keys):
        if len(k) > 2:                              # drift: 3-й элемент = режим ключа
            md = int(k[2])
            out.append([HL_EASE_IN if md == 2 else EASE_DEFAULT,
                        HL_EASE_OUT if md == 1 else EASE_DEFAULT])
        else:
            prev = keys[i - 1][1] if i > 0 else None
            nxt = keys[i + 1][1] if i + 1 < len(keys) else None
            out.append([HL_EASE_IN if (prev is not None and prev > k[1]) else EASE_DEFAULT,
                        HL_EASE_OUT if (nxt is not None and nxt < k[1]) else EASE_DEFAULT])
    return out


def _zoom_key_holds(keys, legacy_hold=False):
    """[bool, ...] — тип интерполяции отрезка от КАЖДОГО ключа до следующего.
    True = HOLD (значение держится до следующего ключа); False = BEZIER (плавно).
    Для 4-элементных ключей (f, pct, mode, hold) берётся k[3];
    для остальных (ручной cam1_scale, старые данные) — fallback legacy_hold.
    Длина совпадает с len(keys)."""
    return [bool(k[3]) if len(k) >= 4 else bool(legacy_hold) for k in (keys or [])]


def _zoom_max(keys, fps, ts, te, holds=None, hold=None):
    """Максимум зума Камеры 1 (в %%, как в ключах) на окне [ts, te] сек.

    Группа живёт секунду с лишним, и наезд успевает случиться ВНУТРИ окна — брать зум
    в момент старта нельзя.
    Отрезок i с holds[i]==True вносит в максимум v_i, если [f_i, f_{i+1}) пересекает окно
    (последний ключ — до бесконечности);
    плавный отрезок (holds[i]==False) — линейные значения на концах пересечения с окном.
    До первого ключа — keys[0][1]. Ключей нет — 100.0 (зума нет).
    """
    raw_keys = [(k[0], float(k[1])) for k in (keys or []) if len(k) >= 2]
    if not raw_keys:
        return 100.0
    fps = float(fps) or 60.0
    n = len(raw_keys)
    if holds is None:
        if hold is not None:
            holds = [bool(hold)] * n
        else:
            holds = _zoom_key_holds(keys, legacy_hold=False)
    elif isinstance(holds, bool):
        holds = [holds] * n
    else:
        holds = [bool(h) for h in holds]

    pts = [[f / fps, v] for f, v in raw_keys]

    def _linear_at(t):
        if t <= pts[0][0]:
            return pts[0][1]
        if t >= pts[-1][0]:
            return pts[-1][1]
        for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
            if t0 <= t <= t1:
                return v0 if t1 <= t0 else v0 + (v1 - v0) * (t - t0) / (t1 - t0)
        return pts[-1][1]

    # Быстрый путь: если все HOLD (jump без наездов)
    if all(holds):
        best = 0.0
        for i, (f, v) in enumerate(raw_keys):
            f_end = raw_keys[i + 1][0] if i + 1 < n else float("inf")
            if f / fps <= te + 1e-9 and f_end / fps > ts - 1e-9:
                best = max(best, v)
        if best:
            return best
        return raw_keys[0][1]

    # Быстрый путь: если все плавные (pulse / drift)
    if not any(holds):
        best = max((v for t, v in pts if ts <= t <= te), default=0.0)
        return max(best, _linear_at(ts), _linear_at(te))

    # Смешанный режим
    best = 0.0
    if ts < pts[0][0] + 1e-9:
        best = max(best, pts[0][1])

    for i in range(n):
        t_i = pts[i][0]
        t_next = pts[i + 1][0] if i + 1 < n else float("inf")
        if t_i <= te + 1e-9 and t_next > ts - 1e-9:
            is_hold = holds[i] if i < len(holds) else False
            if is_hold or i == n - 1:
                best = max(best, pts[i][1])
            else:
                t_start_seg = max(ts, t_i)
                t_end_seg = min(te, t_next)
                best = max(best, _linear_at(t_start_seg), _linear_at(t_end_seg))

    if te > pts[-1][0] - 1e-9:
        best = max(best, pts[-1][1])

    return best if best > 0.0 else pts[0][1]


def _span_roto_plan(cams, sf, ef, fps):
    """Рото по ПОКАЗЫВАЕМОЙ камере в окне [sf,ef] (кадры): перебивки cam2+ там, где включён их
    клип, и Камера 1 во всех разрывах между ними. Так человек перед текстом/видеовставкой и на
    cam1, и на cam2. -> список энтри (без флага intro/vins — его ставит вызывающий)."""
    entries, cover = [], []
    for ci in range(1, len(cams)):                 # включённые клипы вторых камер = перебивки
        for e in _cam_overlaps(cams[ci]["clips"], sf, ef, fps, ci):
            entries.append(e); cover.append((e["tl_start"] * fps, e["tl_end"] * fps))
    cover.sort()
    cur, gaps = sf, []                             # разрывы окна, не покрытые перебивками -> cam1
    for a, b in cover:
        if a > cur:
            gaps.append((cur, a))
        cur = max(cur, b)
    if cur < ef:
        gaps.append((cur, ef))
    for gs, ge in gaps:
        entries += _cam_overlaps(cams[0]["clips"], int(gs), int(ge), fps, 0)
    return entries


HEAD_DEAD_RATIO = 0.03  # мёртвая зона слежения (3% ширины кадра): мелкие покачивания головы игнорируются


def _rdp(pts, eps=4.0):
    """Рамер–Дуглас–Пекер по (f, y) внутри клипа."""
    if len(pts) <= 2:
        return pts
    x1, y1 = pts[0]
    x2, y2 = pts[-1]
    dx = x2 - x1
    dy = y2 - y1
    denom = (dx * dx + dy * dy) ** 0.5
    max_d = -1.0
    idx = -1
    for i in range(1, len(pts) - 1):
        x0, y0 = pts[i]
        if denom == 0:
            d = ((x0 - x1) ** 2 + (y0 - y1) ** 2) ** 0.5
        else:
            d = abs(dy * x0 - dx * y0 + x2 * y1 - y2 * x1) / denom
        if d > max_d:
            max_d = d
            idx = i
    if max_d > eps:
        left = _rdp(pts[:idx + 1], eps)
        right = _rdp(pts[idx:], eps)
        return left[:-1] + right
    return [pts[0], pts[-1]]


def _cam1_follow_keys(cams, pts, w_src, h_src, zoom_keys, holds, fps=60.0,
                      W=1080, H=1920, cx=0.5, pan_x=0.0, cam1_fit=100.0,
                      target=0.5, smooth_s=0.6, min_scale=0.0):
    """Ключи слежения за головой по X для Камеры 1.

    Возвращает [(f, off), ...]. Все ключи Easy Ease (EASE_DEFAULT).
    """
    if not cams or not pts or not w_src or not h_src:
        return []
    from core import headtrack

    fps = float(fps or 60.0)
    W = float(W)
    H = float(H)
    cx = float(cx if cx is not None else 0.5)
    pan_x = float(pan_x or 0.0)
    cam1_fit = float(cam1_fit or 100.0)
    target = float(target if target is not None else 0.5)
    smooth_s = max(0.01, float(smooth_s if smooth_s is not None else 0.6))
    min_scale = float(min_scale or 0.0)
    head_dead = HEAD_DEAD_RATIO * W

    fit_w = w_src * max(W / w_src, H / h_src) * (cam1_fit / 100.0)
    Cx = cx * W

    raw_zoom = [(float(k[0]), float(k[1])) for k in (zoom_keys or []) if len(k) >= 2]
    n_zoom = len(raw_zoom)

    def _zoom_at(f):
        if not raw_zoom:
            return 1.0
        if f <= raw_zoom[0][0]:
            return raw_zoom[0][1] / 100.0
        if f >= raw_zoom[-1][0]:
            return raw_zoom[-1][1] / 100.0
        for i in range(n_zoom - 1):
            f0, v0 = raw_zoom[i]
            f1, v1 = raw_zoom[i + 1]
            if f0 <= f < f1:
                is_hold = holds[i] if holds and i < len(holds) else False
                if is_hold or f1 <= f0:
                    return v0 / 100.0
                return (v0 + (v1 - v0) * (f - f0) / (f1 - f0)) / 100.0
        return raw_zoom[-1][1] / 100.0

    def _clamp_off(off_val, s_val):
        if s_val * fit_w < W:
            return 0.0
        left_edge = Cx + s_val * (W / 2.0 - fit_w / 2.0 - Cx) + pan_x
        right_edge = Cx + s_val * (W / 2.0 + fit_w / 2.0 - Cx) + pan_x
        min_off = W - right_edge
        max_off = -left_edge
        if min_off > max_off:
            return 0.0
        return max(min_off, min(max_off, off_val))

    segs = _show_segments(cams)
    shown_clips = []
    for cl in cams[0].get("clips", []):
        if not cl[4] or cl[1] <= cl[0]:
            continue
        for seg_start, seg_end, ci in segs:
            if ci != 0:
                continue
            s_a = max(seg_start, cl[0])
            s_b = min(seg_end, cl[1])
            if s_b > s_a:
                shown_clips.append({
                    "start": s_a,
                    "end": s_b,
                    "cl_start": cl[0],
                    "cl_in": cl[2],
                })
    shown_clips.sort(key=lambda c: c["start"])
    if not shown_clips:
        return []

    step = max(1, int(round(fps / 10.0)))
    all_keys = []

    for clip in shown_clips:
        c_start = int(clip["start"])
        c_end = int(clip["end"])
        if c_end <= c_start:
            continue
        f_last = c_end - 1
        frames = list(range(c_start, c_end, step))
        if frames[-1] != f_last:
            frames.append(f_last)

        clip_samples = []
        y = 0.0
        prev_f = None

        for f in frames:
            t_src = (clip["cl_in"] + f - clip["cl_start"]) / fps
            hx = headtrack.head_at(pts, t_src)
            if hx is None:
                hx = 0.5
            s = _zoom_at(f)
            X0 = W / 2.0 + (hx - 0.5) * fit_w
            screen = Cx + s * (X0 - Cx) + pan_x
            err = target * W - screen
            active = (s * 100.0) >= (min_scale - 1e-6)
            desired = err if active else 0.0

            if prev_f is None:
                y = _clamp_off(desired, s)
            else:
                dt = (f - prev_f) / fps
                if active:
                    diff = err - y
                    if abs(diff) > head_dead:
                        sgn = 1.0 if diff > 0 else -1.0
                        y += (diff - sgn * head_dead) * (1.0 - math.exp(-dt / smooth_s))
                else:
                    y += (0.0 - y) * (1.0 - math.exp(-dt / smooth_s))
                    if abs(y) < 0.5:
                        y = 0.0
                y = _clamp_off(y, s)

            clip_samples.append((f, y))
            prev_f = f

        rdp_pts = _rdp(clip_samples, eps=4.0)
        all_keys.extend(rdp_pts)

    return [(int(f), _r(off, 2)) for f, off in all_keys]

