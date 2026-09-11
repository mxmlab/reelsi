# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Геометрия кадра: карточки вставок, зум и дрейф камеры 1, раскладка, окна цензуры.

Числа здесь — не вкусовщина, а замеры: у каждой константы в комментарии написано,
какой кейс её задал.
"""
import heapq
import os
from core import fonts as _fonts
from .jsutil import _r
from .parse import is_out_dir


# «Карточка» фотовставки на экране, px. Раньше масштаб был плоский (50 кам1 / 44 кам2) и не
# зависел от пропорций картинки: 16:9 выходило 295px высотой («мелкие все какие-то»), квадрат —
# 540. Теперь вписываем ВИДИМУЮ часть фото в коробку: при квадратной маске ширина равна высоте и
# упирается в CARD_H, у ультравайда (маску не режем) упирается в CARD_W.
INS_CARD_W, INS_CARD_H = 1030.0, 560.0
INS_CARD_H_CAM2 = 495.0            # кам2 садится мельче кам1 (сохраняем прежнее отношение 44/50)
INS_MASK_SQUARE_AR = 2.2           # должно совпадать с одноимённой константой в AE_FULL
_IMG_SIZE_CACHE = {}


def _img_size(path):
    """(w, h) картинки или None, если не открылась (битый файл, видео, нет PIL)."""
    p = os.path.abspath(path or "")
    if p not in _IMG_SIZE_CACHE:
        try:
            from PIL import Image
            with Image.open(p) as im:
                _IMG_SIZE_CACHE[p] = im.size
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
    сцены (задание D), чтобы JS не держал вторую копию. Размер не прочитался -> None
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


def _intro_group_window(times, gi, n_groups):
    """Окно группы интро (сек): (ts, te) — РОВНО формула inAt/outEnd из AE_FULL.
    times — моменты слов группы (сек, округлённые как в плане). gi — индекс группы,
    n_groups — их число (последняя держит HOLD и фейдится последней)."""
    gmin = min(times) if times else 0.0
    gmax = max(times) if times else 0.0
    in_at = 0.0 if (gi == 0 and gmin < 3) else gmin    # 1-я группа с 0 только если реально в начале
    out_start = (gmax + INTRO_F_DUR + INTRO_HOLD) if gi == n_groups - 1 \
        else max(gmax, in_at + INTRO_F_DUR)            # выход не раньше конца фейд-ина (см. шаблон)
    return _r(in_at), _r(out_start + INTRO_F_OUT)


# Геометрия вставок, раньше жила в AE_FULL (xml2ae/template.py) — переехала сюда, чтобы
# считаться в Python до сборки .jsx (задание B из TASKS.md). Значения не менялись.
HL_EASE_OUT, HL_EASE_IN = 35, 90    # cubic-bezier(0.35,0.01,0.10,0.99)
EASE_DEFAULT = 33.3333              # Easy Ease по умолчанию (медленный откат большой←малый)
INS_ENTER, INS_EXIT = 0.38, 0.47    # вход/выход cam2-вставки, сек
# Окна групп интро (ts/te) считает scene_plan — раньше это жило ДВУМЯ копиями:
# introGroupWindows в предпросмотре и inAt/outEnd в AE_FULL, и они уже разошлись
# (JS не учитывал max(gMax, inAt+F_DUR) для серединных групп). Константы — те же,
# что в template.py: без совпадения превью покажет не то окно.
INTRO_F_DUR, INTRO_HOLD, INTRO_F_OUT = 0.3, 1.0, 0.75
# Базовая позиция интро (задание Q2): превью ставит блок по plan.intro[i].y, шаблон
# берёт готовое iDy — позиция живёт в Python, вторая копия не заводится. Числа — из
# AE_FULL: iDy=0, iTop=H/2 - 520.7894 - (nL/2)*LINE_STEP*(iSc/100); if(iTop<SAFE_TOP)
# iDy=SAFE_TOP-iTop. iSc — базовый масштаб прекомпа 96.8 × gs/100.
INTRO_BASE_Y = 520.7894     # база позиции прекомпа интро, px (подъём от центра кадра)
INTRO_LINE_STEP = 160.0     # шаг строки внутри прекомпа, px
INTRO_SAFE_TOP = 285.0      # верх блока не выше этой линии кадра — иначе опускаем
INTRO_SCALE = 96.8          # базовый масштаб прекомпа при gs=100, %%
# Запас автофита интро (задание BP): строка не шире этой доли кадра даже на максимуме
# зума. Раньше константа жила в шаблоне (var INTRO_FIT_W), где считала от ширины
# прекомпа; с переездом автофита в scene_plan источник один — здесь.
INTRO_FIT_W = 0.92


def _intro_i_dy(h, n_lines, gs):
    """Опускание блока интро под INTRO_SAFE_TOP, px (задание Q2). От неужатого масштаба
    (96.8·gs/100) — автофит длинных строк (INTRO_FIT_W) знает только AE, а его редкий
    случай осознанно отдаём: превью сходится с AE всегда, очень длинная строка получает
    чуть другое опускание, чем сегодня (записано в TASKS.md). Точность как в старом
    шаблоне: без округления, чтобы .jsx не поехал на сотых."""
    i_sc = INTRO_SCALE * (float(gs) if gs else 100.0) / 100.0
    i_top = h / 2 - INTRO_BASE_Y - (n_lines / 2.0) * INTRO_LINE_STEP * (i_sc / 100.0)
    if i_top < INTRO_SAFE_TOP:
        return INTRO_SAFE_TOP - i_top
    return 0.0


def _line_ink(ps_name, line, size_px):
    """(asc, desc) чернил строки интро, px, или None (шрифта/глифа нет) — тонкая обёртка
    над fonts.ink_extent: строка интро в .jsx несёт слова, а не готовую строку."""
    return _fonts.ink_extent(ps_name, " ".join(line.get("words") or []), size_px)


def intro_line_ys(lines, fonts, fsize, back_scale, back_step, back_gap,
                  any_back_in_clip, anchor, h):
    """Y базовых линий строк интро в координатах прекомпа (высота h), по числу строк
    (задание A1). Раньше шаг был жёсткими пикселями ТОЛЬКО в шаблоне (LINE_STEP=160,
    160*back_step до строки заднего плана) и о шрифте не знал: после смены шрифта малые
    строки наезжали на строку над ними, и пользователь раздвигал их руками в AE. Его шаг
    и совпал с «хвост вниз верхней строки + высота букв нижней + ~4 px» — теперь это и
    есть формула: шаг до строки заднего плана не меньше зазора между ЧЕРНИЛАМИ соседних
    строк (fonts.ink_extent), а не между базовыми линиями.

    lines — строки группы ровно как уезжают в .jsx (поле back только у настоящей строки
    заднего плана: акцент его перебивает), fonts — шрифт каждой строки (plan.intro[].fonts),
    fsize — кегль не-back строки, back_scale/back_step — кегль и шаг строк заднего плана
    долями от fsize/LINE_STEP.

    any_back_in_clip: в ролике есть строки заднего плана. Нет их — все шаги LINE_STEP и
    базовая линия по центру: ветка шаблона без back другой раскладки не знает, шаги по
    back_step/back_gap там смысла не имеют. back_gap None — зазор не считаем вовсе.

    anchor: "first" — первая строка стоит в h/2, добавленная строка опускает только
    нижние (блок не поднимается); "center" — как раньше: без back h/2 - (nL-1)/2*LINE_STEP,
    с back и головой не back — h/2 - (nL-1)*60, иначе h/2 - totH/2. Округление до сотых:
    столько же знаков, сколько у остальных чисел .jsx.
    """
    n = len(lines)
    if n <= 0:
        return []

    def _back(i):
        return bool(lines[i].get("back"))

    def _fsz(i):
        return fsize * back_scale if _back(i) else fsize

    steps = []
    for i in range(1, n):
        if not any_back_in_clip:
            steps.append(INTRO_LINE_STEP)
            continue
        if _back(i):
            base = INTRO_LINE_STEP * back_step
        elif _back(i - 1):
            base = INTRO_LINE_STEP * 0.75
        else:
            base = INTRO_LINE_STEP
        # Зазор между буквами: хвост вниз ПРЕДЫДУЩЕЙ строки плюс высота букв этой.
        # Высоту не знаем (шрифта нет) — базовый шаг, как в шаблоне.
        if _back(i) and back_gap is not None:
            prev = _line_ink(fonts[i - 1] if i - 1 < len(fonts) else None, lines[i - 1], _fsz(i - 1))
            cur = _line_ink(fonts[i] if i < len(fonts) else None, lines[i], _fsz(i))
            if prev is not None and cur is not None:
                base = max(base, prev[1] + cur[0] + float(back_gap))
        steps.append(base)

    if anchor == "first":
        ys = [h / 2.0]
    elif not any_back_in_clip:
        ys = [h / 2.0 - (n - 1) / 2.0 * INTRO_LINE_STEP]
    elif not _back(0) and n > 1:
        ys = [h / 2.0 - (n - 1) * 60.0]
    else:
        ys = [h / 2.0 - sum(steps) / 2.0]
    for stp in steps:
        ys.append(ys[-1] + stp)
    return [round(y, 2) for y in ys]


# Анимации вставок, раньше жили константами в AE_FULL (правка .jsx руками влияла на
# сборку): переехали сюда вместе с расчётом ключей — у анимации один источник (задание C).
INS_BLUR = 41.0                     # Box Blur cam2-вставки, px (полный блюр на входе)
INS_RISE_DY, INS_RISE_S0, INS_RISE_ENTER = 132.0, 0.706, 0.5   # анимация «rise»: подъём px, доля целевого масштаба на старте, вход сек
INS_C2_PEAK, INS_C2_BASE = 100.0, 44.0  # cam2: пик наезда = осевший scale * PEAK/BASE
INS_C2_Y_FR = 0.172                 # cam2: Y точки покоя в долях H (≈330 при 1920)
INS_C1_UP, INS_C1_DN = 0.68, 0.80   # cam1 («из-за спины»): время подъёма над спиной и спуска
INS_C1_LOW, INS_C1_HIGH = 464.7, 567.0  # cam1: нижняя точка старта и высота вылета, px
# Тень плашки под субтитрами (задание DE): чёрная, снята из adcut.aep
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
    """Сколько ПОЛЕЗНО двигать заполняющий экран кадр, px в каждую сторону.
    16:9 ролик, вписанный по высоте, вылезает по ширине на ~2300 px (есть что
    панорамировать), а по высоте запаса нет вовсе — уехав за эту границу, вставка
    показывает пустоту. Ужатая вставка (k<1) кадр не заполняет — запас нулевой.
    -> (slack_x, slack_y); (0,0), если размеров нет."""
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
    (остаток задания B): превью читает эти же ключи из плана сцены и не повторяет расчёт.
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


def _cam1_jump_keys(cams, lo=100.0, hi=140.0, min_diff=12.0, fps=60.0, start=True):
    """Джамп-кат зум кам1: на КАЖДОЙ смене показываемой камеры (и в кадре 0) скейл ПРЫГАЕТ на
    случайное значение 100–140%% без анимации (HOLD-кейфреймы ставит JSX по CAM1_HOLD).
    Если start=False — в кадре 0 значение 100 вместо случайного.
    Одна камера — прыжки на собственных склейках кам1 (см. _zoom_cut_frames), не по таймеру.
    Детерминировано (seed от кадров реза), соседние значения отличаются минимум на
    min_diff, чтобы скачок был заметен."""
    import random as _rnd
    fps = int(round(float(fps) or 60))
    frames = sorted(set([0] + _zoom_cut_frames(cams, fps=fps)))  # ТОЛЬКО реальные срезы кадра
    rng = _rnd.Random(",".join(str(f) for f in frames))          # тот же таймлайн -> тот же разброс
    keys, prev = [], None
    for idx, f in enumerate(frames):
        if idx == 0 and not start:
            v = 100.0
        else:
            for _ in range(8):
                v = round(rng.uniform(lo, hi), 1)
                if prev is None or abs(v - prev) >= min_diff:
                    break
        keys.append((f, v)); prev = v
    return keys


# Автоужимание дисклеймера (задание E): самая длинная строка не шире этой доли ширины
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


def _zoom_max(keys, fps, ts, te, hold=False):
    """Максимум зума Камеры 1 (в %%, как в ключах) на окне [ts, te] сек (задание BP).

    Группа живёт секунду с лишним, и наезд успевает случиться ВНУТРИ окна — брать зум
    в момент старта нельзя. Для плавных (pulse/drift) значения между ключами у
    стандартных ease-кривых не выходят за концы отрезка, поэтому достаточно ключей
    внутри окна и его границ (линейная интерполяция). Для джамп-ката (hold) значение
    ДЕРЖИТСЯ от своего ключа до следующего — берём максимум ключей, чей интервал
    [frame, next_frame) пересекает окно (линейная интерполяция тут занизила бы пик).
    Ключей нет — 100 (зума нет)."""
    keys = [(k[0], float(k[1])) for k in (keys or []) if len(k) >= 2]
    if not keys:
        return 100.0
    fps = float(fps) or 60.0
    if hold:
        # джамп-кат: значение держится от СВОЕГО ключа до следующего, последний — навсегда.
        # Активен ключ, чей интервал [f, next_f) пересекает окно.
        n = len(keys)
        best = 0.0
        for i, (f, v) in enumerate(keys):
            f_end = keys[i + 1][0] if i + 1 < n else float("inf")
            if f / fps <= te + 1e-9 and f_end / fps > ts - 1e-9:
                best = max(best, v)
        if best:
            return best
        return keys[0][1]            # окно до первого ключа — зум статичен, как до ключей
    pts = [[f / fps, v] for f, v in keys]

    def _at(t):
        if t <= pts[0][0]:
            return pts[0][1]
        if t >= pts[-1][0]:
            return pts[-1][1]
        for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
            if t0 <= t <= t1:
                return v0 if t1 <= t0 else v0 + (v1 - v0) * (t - t0) / (t1 - t0)
        return pts[-1][1]

    best = max((v for t, v in pts if ts <= t <= te), default=0.0)
    return max(best, _at(ts), _at(te))


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
