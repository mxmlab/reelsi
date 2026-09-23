# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Вставки плана сцены (этап 3 распила scene_plan).

`scene_plan` был одной функцией на 3253 строки с 24 вложенными функциями, делившими
состояние замыканиями. Этапы 1–2 вынесли блок субтитров в `plan_subs.py`
и расчёт интро в `plan_intro.py`; этап 3 выносит сюда ВСЕ вставки:

* разбор таймингов и привязка к монтажу — секунды старта/конца (сек+кадры и легаси-кадры),
  прижим старта к кату (`_snap_start`), срез окна катом (`SNAP_TOL`/`_clip_end`), перенос
  схлопнувшегося окна в новый шот (`MIN_INS_SEC`), тип по файлу и стиль фото (`cam1`/`cam2`
  по активной камере, флаг `oncam2`);
* данные вставок для плана и шаблона — окна показа (`_isec`/`_win`), подложка и «без фона»,
  масштабы видео, готовые ключи анимаций (наезд, `rise`, `none`, вылет из-за спины) и сама
  строка INSERTS.

Перенос ПОСТРОЧНЫЙ: поведение, числа и порядок операций не менялись ни на байт
(проверяется эталоном fixtures/golden_geometry.jsx и побайтовым сравнением .jsx/плана).
Имена локальных переменных оставлены как в scene_plan — поэтому тело перенесено дословно,
а входы распаковываются в преамбуле. В вызовах вынесенных helper'ов добавлены только их
явные аргументы.

Почему дверей две. Подготовка таймингов и сборка данных РАЗНЕСЕНЫ по scene_plan, и между
ними лежит зависимый код: звук считает `has_video` по УЖЕ проставленному типу вставки и
ставит whoosh/переход по точкам смены камеры. Одна дверь заставила бы тащить туда звук, а
он в этом этапе не трогается. `plan_insert_timings` правит копии словарей вставок (их
делает scene_plan) и отдаёт `clip_end` — замыкание прижима/среза окна: сборка данных
пользуется им же, второй копии правила среза нет.

Общее с другими блоками остаётся в `build.py` и приходит параметрами: `_active_cam_at`
(его зовут и камера, и интро), `_cam_change_sec` (по тем же точкам ставятся звуки
переходов) и `_media_dims` (его подменяют в модуле build — tests/test_insert_video_pan.py).
Стиль приходит структурой `StyleValues` одним полем `style` (её читает один
раз `plan_style.read_style`). Геометрия вставок — из `layout.py` (`_fit_scale`,
`_ins_card`, `_cam1_pos_keys`, `_anim_keys`, …), признак картинки — `_is_image` из `parse.py`.
"""
import os
from dataclasses import dataclass
from typing import Callable

from .jsutil import _jd, _r
from .layout import (INS_C2_BASE, INS_C2_PEAK, INS_C1_HIGH, INS_EXIT, INS_RISE_DY,
                     INS_RISE_ENTER, INS_RISE_S0, _anim_keys, _blur_keys,
                     _cam1_pos_keys, _fill_slack, _fit_scale, _ins_card,
                     _ins_enter_exit, _ins_plate, _ins_scale)
from .parse import _is_image
from .plan_style import StyleValues


@dataclass(frozen=True)
class InsertTimingInputs:
    """Вход подготовки таймингов: всё, что `scene_plan` знает к моменту вызова.

    Поля названы как локальные переменные scene_plan. `inserts` — копии словарей
    (scene_plan делает их сам): модуль правит их ПО МЕСТУ, как правил блок в scene_plan.
    `cam_change_sec` считается в build.py не здесь: по тем же точкам ставятся звуки
    переходов — второй копии точек смены камеры не заводится. `style` — структура
    стиля, прочитанная один раз.
    """
    # Вставки: свои из UI плюс разобранные из XML (сек+кадры или легаси-кадры).
    inserts: list
    # Частота кадров как _fps0 (meta["fps"] or 60) — ею тайминги переводятся в секунды.
    fps: float
    # Точки смены показываемой камеры, сек (_cam_change_sec из build.py).
    cam_change_sec: list
    # Индекс камеры в момент t: правило общее с камерой и интро (build.py).
    active_cam_at: Callable[[float], int]
    # Резолвнутый и прочитанный стиль (plan_style.read_style).
    style: StyleValues
    # Лог: сюда уходит сообщение о переносе вставки, срезанной катом.
    emit: Callable[..., object]


@dataclass(frozen=True)
class InsertTimings:
    """Выход подготовки: вставки, готовые к монтажу, и правило среза окна.

    `clip_end` — замыкание `_clip_end` над `_snap`, `_cam_change_sec` и SNAP_TOL: им
    сборка данных режет окно (и снимает выход). Отдаётся наружу, потому что между двумя
    дверями лежит звук, читающий тот же подготовленный список вставок.
    """
    inserts: list
    clip_end: Callable[..., tuple]


@dataclass(frozen=True)
class InsertsInputs:
    """Вход сборки данных вставок: всё, что `scene_plan` знает к моменту вызова.

    Поля названы как локальные переменные scene_plan (`meta` — ролик: тело читает
    `meta["w"]`/`["h"]`/`["fps"]`). `clip_end` — результат `plan_insert_timings`:
    срез окна катом остаётся ОДНИМ правилом. Стилевые значения (подложка, анимация,
    точки покоя кам1/кам2, эффекты, «видео за человеком») приходят структурой `style`,
    прочитанной один раз: поимённого перечисления ключей больше нет.
    """
    # Подготовленные вставки (тайминги, тип, стиль) — из plan_insert_timings.
    inserts: list
    meta: dict
    # Частота кадров как _fps0 — ею считаются окна входа/выхода и ключи анимаций.
    fps: float
    clip_end: Callable[..., tuple]
    # Размеры файла: build.py отдаёт свой _media_dims (тесты подменяют его в build).
    media_dims: Callable[[str], object]
    # Резолвнутый и прочитанный стиль (plan_style.read_style).
    style: StyleValues
    # Лог: сюда уходит сообщение о снятом фоне (nobg_path).
    emit: Callable[..., object]


@dataclass(frozen=True)
class InsertsPlan:
    """Выход сборки данных: ровно те имена, что `scene_plan` читает дальше.

    inserts уезжает в план (предпросмотр) как plan["inserts"], inserts_js — в шаблон
    подстановкой INSERTS, video_segs — окна видеовставок: по ним группа интро уезжает
    наверх (INTRO_FRONT).
    """
    inserts: list
    inserts_js: str
    video_segs: list


def plan_insert_timings(inp: InsertTimingInputs) -> InsertTimings:
    """Тайминги вставок: секунды, прижим к катам, срез окна, тип и стиль фото.

    Тело — дословный перенос блока из scene_plan (до распила — строки 537-644): имена
    локальных переменных оставлены прежними, поэтому ни одна строка не переписана.
    Порядок операций тот же, и он важен: стиль фото считается ПОСЛЕ починки
    схлопнувшегося окна — иначе «из-за спины»/«наезд» выбирались бы по уходящей камере.
    """
    inserts = inp.inserts
    _fps0 = inp.fps
    _cam_change_sec = inp.cam_change_sec
    _active_cam_at = inp.active_cam_at
    # Стиль — структурой, прочитанной один раз: обёрток _sv/_sv_or нет.
    style = inp.style
    emit = inp.emit

    def _ins_t0(x):                                    # старт вставки в секундах (сек+кадры или легаси-кадры)
        s, fr = x.get("start_s"), x.get("start_f")
        if s is not None or fr is not None:
            return float(s or 0) + float(fr or 0) / _fps0
        return float(x.get("start") or 0) / _fps0

    def _ins_t1(x):                                    # конец вставки в секундах (старт+длительность или явный конец)
        if x.get("dur_s") is not None or x.get("dur_f") is not None:
            return _ins_t0(x) + float(x.get("dur_s") or 0) + float(x.get("dur_f") or 0) / _fps0
        s, fr = x.get("end_s"), x.get("end_f")
        if s is not None or fr is not None:
            return float(s or 0) + float(fr or 0) / _fps0
        return float(x.get("end") or 0) / _fps0

    _snap = style.insert_snap_cut
    # Вставка, стартующая ВПРИТЫК перед катом, доигрывала бы вход на уходящем кадре и обрывалась
    # срезом. Прижимаем старт ровно к кату: вставка начинается уже на следующем кадре, вся анимация
    # входа идёт по нему (и стиль фото авто-выбирается по НОВОЙ камере). Конец не двигаем.
    SNAP_START_TOL = style.insert_snap_start   # сек до ката

    def _snap_start(x):
        if not _snap or SNAP_START_TOL <= 0:
            return
        t0, t1 = _ins_t0(x), _ins_t1(x)
        for cp in _cam_change_sec:
            if t0 < cp <= t0 + SNAP_START_TOL and cp < t1 - 1.5 / _fps0:
                x["start_s"], x["start_f"] = cp, 0.0        # старт = кат
                x["dur_s"], x["dur_f"] = t1 - cp, 0.0       # конец на месте
                x.pop("start", None)
                x.pop("end", None)
                x.pop("end_s", None)
                x.pop("end_f", None)
                break

    for x in inserts:
        _snap_start(x)

    # тип — ПО ФАЙЛУ, а не по тому, что просили у базы/ИИ: автоподбор мягкий (type_hint даёт
    # +0.05 к score, а не фильтрует), поэтому под «фото» прилетает mp4, а под «видео» — jpg.
    # А тип решает всё: фото = стоп-кадр с наездом, видео = футаж с переходом и whoosh.
    for x in inserts:
        if x.get("media"):
            x["type"] = "photo" if _is_image(x["media"]) else "video"

    # Вставок длиной в пару кадров в природе не бывает: старт прижимается к кату ещё на
    # создании, и `_snap_start` выше делает то же самое здесь. Если после среза катом окно
    # ВСЁ РАВНО схлопнулось — это не «короткая вставка», а НЕВЕРНЫЙ СТАРТ: её задумывали
    # в новом шоте, а поставили за миг до ката. Переносим старт на кат и играем задуманную
    # длительность там, где ей место. Делаем это ДО выбора стиля — иначе стиль фото
    # («из-за спины» / «наезд») считался бы по старой, уходящей камере.
    # ТОЛЬКО ФОТО-вставку (b-roll над головой), которая ПЕРЕХОДИТ на другую камеру, обрезаем ровно
    # в точке смены и без анимации выхода (не тянется через смену ракурса). ВИДЕО НЕ режем посреди
    # окна никогда — это полноэкранная вставка, играет весь свой хрон. Если стиль выключил snap —
    # и фото не режем. ОТДЕЛЬНО: вставка (фото И видео), чей конец лежит на точке смены камеры
    # (±SNAP_TOL — ИИ округляет тайминги до 0.1 c), считается СРЕЗАННОЙ катом: конец прижимаем к
    # кату, выхода нет (у видео это же убирает выходной переход+whoosh).
    SNAP_TOL = 0.12                                    # сек: конец «на кате» с учётом округления ИИ

    def _clip_end(x, t0, t1):                          # -> (end_sec, noexit)
        if _snap:
            for cp in _cam_change_sec:                 # конец вставки на самом кате -> жёсткий срез
                if abs(t1 - cp) <= SNAP_TOL and cp > t0 + 1.5 / _fps0:
                    return min(t1, cp), True           # не переползаем смену ракурса
            if (x.get("type") or "photo") == "photo":
                for cp in _cam_change_sec:             # первая смена камеры ВНУТРИ окна фото
                    if t0 + 1.5 / _fps0 < cp < t1 - 1.5 / _fps0:
                        return cp, True                # обрезать в точке смены, жёсткий срез
        return t1, False

    MIN_INS_SEC = 0.85                                 # вход 0.38 + выход 0.47: короче анимацию не уложить
    if _snap:
        for x in inserts:
            t0, t1raw = _ins_t0(x), _ins_t1(x)
            cut, _ne = _clip_end(x, t0, t1raw)
            if cut - t0 >= MIN_INS_SEC or t1raw - t0 < MIN_INS_SEC:
                continue                               # окно нормальное либо вставка и была короткой
            want = t1raw - t0                          # задуманная длительность
            nxt = [cp for cp in _cam_change_sec if cp > cut + 1.5 / _fps0]
            end = min(cut + want, nxt[0]) if nxt else cut + want
            if end - cut < MIN_INS_SEC:
                continue                               # и в новом шоте не помещается — оставляем как есть
            emit("  вставка {name}: старт {t0:.2f}с срезался катом до {cut_diff:.2f}с — перенёс на кат {cut:.2f}с (похоже, неверный тайминг начала — проверь на шаге разметки)",
                 name=os.path.basename(x.get('media') or '?'), t0=t0, cut_diff=cut - t0, cut=cut)
            x["start_s"], x["start_f"] = cut, 0.0
            x["dur_s"], x["dur_f"] = end - cut, 0.0
            for k in ("start", "end", "end_s", "end_f"):
                x.pop(k, None)

    _instyle = style.insert_style      # стиль фотовставок: авто | cam1 | cam2
    for x in inserts:                                  # стиль фото: force cam1/cam2 или АВТО по активной камере
        if (x.get("type") or "photo") != "photo":
            continue
        _act = _active_cam_at(_ins_t0(x))
        if _instyle in ("cam1", "cam2"):
            sstyle = _instyle
        else:                                          # auto: над кам1 → «из-за спины», над перебивкой → cam2
            sstyle = "cam1" if _act == 0 else "cam2"
        x["style"] = sstyle
        # стиль «Кам 1» выставлен принудительно, а в кадре перебивка: в JSX такая вставка вешается
        # на отдельный нул (без зума Камеры 1, которой в кадре нет) и сдвигается общими INS_C1_ON2_X/Y
        x["oncam2"] = bool(sstyle == "cam1" and _act != 0)
        # масштаб считаем по пропорциям картинки (см. _ins_scale), но РУЧНОЙ уже проставленный
        # scale не затираем — иначе правка из webui умирала на каждой пересборке
        if not x.get("scale_manual"):
            x["scale"] = _ins_scale(x.get("media"), sstyle)
    return InsertTimings(inserts=inserts, clip_end=_clip_end)


def plan_inserts(inp: InsertsInputs) -> InsertsPlan:
    """Данные вставок для плана и шаблона: окна, подложка, масштабы, ключи, INSERTS.

    Тело — дословный перенос блока из scene_plan (до распила — строки 1063-1232): имена
    локальных переменных оставлены прежними, поэтому ни одна строка не переписана.
    """
    inserts = inp.inserts
    meta = inp.meta
    _fps0 = inp.fps
    _clip_end = inp.clip_end
    _media_dims = inp.media_dims
    # Стиль — структурой, прочитанной один раз: подложка, анимация, точки
    # покоя кам1/кам2, эффекты и «видео за человеком» берутся оттуда, своих чтений нет.
    style = inp.style
    _plate_path, _plate_scale = style.plate_path, style.plate_scale
    _insert_anim = style.insert_anim
    ins_c1x, ins_c1y = style.insert_c1_x, style.insert_c1_y
    ins_c2x, ins_c2y = style.insert_c2_x, style.insert_c2_y
    emit = inp.emit
    _fps = meta["fps"]

    def _isec(x, k):                                   # поле «сек+кадры» -> секунды
        s, f = x.get(k + "_s"), x.get(k + "_f")
        if s is not None or f is not None:
            return float(s or 0) + float(f or 0) / _fps
        return float(x.get(k) or 0) / _fps             # легаси: целые кадры

    def _win(x):                                       # -> (start_sec, end_sec)
        st = _isec(x, "start")
        if x.get("dur_s") is not None or x.get("dur_f") is not None:  # старт + длительность
            return st, st + float(x.get("dur_s") or 0) + float(x.get("dur_f") or 0) / _fps
        return st, _isec(x, "end")                     # легаси: явный конец

    # Срез окна катом (SNAP_TOL и _clip_end) посчитан в таймингах вставок (plan_insert_timings):
    # там же прижимается старт к кату, и стиль фото выбран уже по исправленному старту.

    # видеовставка: перед человеком (фул на весь кадр, дефолт) или за ним (рото сверху)
    _vfront = bool(style.insert_video_front)

    def _front(x):
        return _vfront if x.get("front") is None else bool(x.get("front"))

    def _ins_js(x):
        t0, t1raw = _win(x)
        t1, noexit = _clip_end(x, t0, t1raw)
        # «Без фона»: путь фото у вставки с галкой «на подложке» меняется
        # ЗДЕСЬ, в плане сцены, — до _ins_plate и до всей остальной геометрии (у nobg_path
        # свой кэш: второй раз на тот же файл rembg не зовётся). Раньше подмену делал
        # _nobg_kw в to_ae_full, ДО scene_plan: в .jsx уезжал обрезанный PNG, а план для
        # превью (/api/scene) считался по ИСХОДНИКУ — рамка карточки была по одним
        # пропорциям, картинка по другим, и фото на подложке в превью сплющивалось
        # (1408×768 -> 176×451). Теперь и .jsx, и превью читают ОДИН план: второй копии
        # подмены в сборке не осталось.
        media_src = x.get("media") or ""
        media = media_src
        if x.get("plate") and _plate_path and _is_image(media_src):
            from core.insertlib import nobg_path
            media = nobg_path(media_src, emit=emit)
        out = {"t": x.get("type") or "photo", "style": x.get("style") or "cam2",
               "media": media, "start": _r(t0), "end": _r(t1),
               "scale": _r(x.get("scale") or 44), "mosaic": bool(x.get("mosaic")),
               "x": _r(x.get("x") or 0), "y": _r(x.get("y") or 0),
               # ручной масштаб, % от авто (фото — от карточки, видео — от заполнения кадра);
               # держим отдельно от scale, потому что scale у фото пересчитывается по картинке
               "sc": _r(x.get("sc") or 100),
               # форма маски-карточки, % от авторасчёта (100 = как считает JSX сам)
               "mw": _r(x.get("mw") or 100), "mh": _r(x.get("mh") or 100),
               "sin": _r(x.get("sin") or 0), "noexit": bool(noexit), "front": bool(_front(x)),
               "oncam2": bool(x.get("oncam2"))}
        # геометрия из Python: видео — масштаб заполнения и запас панорамы
        # (fit/slack — те же числа, что AE считал из item.width/height), фото — окна
        # входа/выхода cam2-анимации. Размеры не прочитались -> полей нет: вставку
        # не трогаем (старое if(!iw||!ih) return).
        if (x.get("type") or "photo") == "video":
            wh = _media_dims(media)
            if wh:
                k = _r(x.get("sc") or 100) / 100               # округлённый sc: как увидит JSX
                iw, ih = wh
                out["fit"] = _r(_fit_scale(iw, ih, True, meta["w"], meta["h"], k))
                # запас вылета ролика за кадр — СПРАВКА для превью, а не граница позиции
                # x/y уходят в план и .jsx ровно такими, какими их задал
                # пользователь. Раньше позиция зажималась этим запасом (`if k >= 1`), и у
                # вертикального 9:16 при sc=100 запаса нет вовсе — X и Y обнулялись, видео
                # не двигалось. Уехав за край, вставка открывает кадр камеры — как в AE.
                sx, sy = _fill_slack(iw, ih, meta["w"], meta["h"], k)
                out["slackx"], out["slacky"] = _r(sx), _r(sy)
                # коробка заполнения при sc=100 (px в comp): ужатому видео (sc<100) предпросмотр
                # рисует её × sc/100, не читая размеры файла
                f0 = _fit_scale(iw, ih, True, meta["w"], meta["h"], 1.0) / 100
                out["fitw"], out["fith"] = _r(iw * f0, 2), _r(ih * f0, 2)
        else:
            # маска-карточка в comp-координатах (осевший масштаб): её масштабирует anim.scale
            # (как Scale слоя в AE), и предпросмотру не нужны размеры картинки
            # Подложка — решение ВСТАВКИ, не стиля: у кого галки нет, тот идёт
            # прежним путём (карточка, маска) даже при заданном в стиле файле подложки.
            _plate = _ins_plate(media, _plate_path, out["style"], out.get("sc"),
                                out.get("x"), out.get("y"), meta["w"], meta["h"],
                                _plate_scale) if (x.get("plate") and _plate_path) else None
            if _plate:
                # Подложка: масштаб слоя прекомпа — плашка под карточку, фото
                # вписано в неё, ручные сдвиг/масштаб уехали в px/py/ps ВНУТРЬ прекомпа.
                # Анимация слоя (вылет кам1, выезд кам2, точка покоя) считается по
                # нейтральному sc=100: подложка у всех таких вставок одного размера.
                # plate в данных — признак для шаблона: слой подложки и отказ от маски
                # достаются РОВНО этим вставкам.
                out.update(_plate)
                out["plate"] = True
                # Сдвиг ВСЕЙ карточки — kx/ky: точка покоя слоя и его
                # ключи анимации считаются по ним, поэтому драг в превью двигает плашку
                # вместе с фото. Ручные x/y со страницы вставок уехали в px/py (_ins_plate)
                # и по-прежнему двигают только фото ВНУТРИ подложки.
                out["x"] = _r(x.get("kx") or 0)
                out["y"] = _r(x.get("ky") or 0)
                out["sc"] = 100
            else:
                _card = _ins_card(media, out["style"], out.get("mw"), out.get("mh"),
                                  out.get("sc"), meta["w"], meta["h"])
                if _card:
                    out["card"] = _card
            # анимации вставок: готовые ключи вместо досчёта в ExtendScript (остаток).
            # Те же округлённые t0/t1 и en/ex, что ушли в JSX, — предпросмотр интерполирует их же.
            t0r, t1r = _r(t0), _r(t1)
            if out["style"] == "cam2":
                S = float(out["scale"] or 44) * float(out["sc"] or 100) / 100
                if _insert_anim == "rise":           # выезд снизу + рост + фейд, без блюра
                    en, ex = _ins_enter_exit(t0r, t1r, noexit, _fps0, enter=INS_RISE_ENTER, exit_=INS_EXIT)
                    out["en"], out["ex"] = _r(en), _r(ex)
                    enr, exr = _r(en), _r(ex)
                    win = max(0.0, t1r - t0r)
                    if win < 1 / float(_fps0):
                        sc_keys = [[t0r, _r(S)]]
                    else:
                        sc_keys = [[t0r, _r(S * INS_RISE_S0)], [_r(t0r + enr), _r(S)]]
                    ix, iy = float(out["x"] or 0), float(out["y"] or 0)
                    rest_x = round(meta["w"] * ins_c2x) + ix
                    rest_y = round(meta["h"] * ins_c2y) + iy
                    pos_start = [_r(rest_x), _r(rest_y + INS_RISE_DY)]
                    pos_end = [_r(rest_x), _r(rest_y)]
                    if win < 1 / float(_fps0):
                        pos_keys = [[t0r, pos_end]]
                    else:
                        pos_keys = [[t0r, pos_start], [_r(t0r + enr), pos_end]]
                    out["anim"] = {
                        "scale": sc_keys,
                        "position": pos_keys,
                        "opacity": _anim_keys(t0r, t1r, 0.0, 100.0, noexit, enr, exr, _fps0),
                    }
                elif _insert_anim == "none":         # без анимации — слой просто есть
                    # один ключ на свойство: слой стоит от t0 в осевшем масштабе S и полной
                    # непрозрачности, вход/выход жёсткие; блюра и позиции нет вовсе
                    out["anim"] = {"scale": [[t0r, _r(S)]], "opacity": [[t0r, 100.0]]}
                else:                                # наезд от осевшего масштаба + opacity + блюр
                    en, ex = _ins_enter_exit(t0r, t1r, noexit, _fps0)  # от округлённых t0/t1, как JSX
                    out["en"], out["ex"] = _r(en), _r(ex)
                    enr, exr = _r(en), _r(ex)
                    pk = S * INS_C2_PEAK / INS_C2_BASE
                    out["anim"] = {"scale": _anim_keys(t0r, t1r, pk, S, noexit, enr, exr, _fps0),
                                   "opacity": _anim_keys(t0r, t1r, 0.0, 100.0, noexit, enr, exr, _fps0),
                                   "blur": _blur_keys(t0r, t1r, noexit)}
            else:                                    # cam1: вылет из-за спины (локально к нулу)
                en, ex = _ins_enter_exit(t0r, t1r, noexit, _fps0)
                out["en"], out["ex"] = _r(en), _r(ex)
                ix, iy = float(out["x"] or 0), float(out["y"] or 0)
                if out["oncam2"]:                    # общий сдвиг всех cam1-на-перебивке (INS_C1_ON2_X/Y)
                    ix += style.insert_c1on2_x
                    iy += style.insert_c1on2_y
                if _insert_anim == "none":           # без анимации — сразу точка покоя
                    # up — точка ПОКОЯ из _cam1_pos_keys (layout.py), нижняя точка dn
                    # (за спиной) не строится вовсе: слой просто стоит на месте
                    out["anim"] = {"position": [[t0r, [_r(ins_c1x + ix),
                                                       _r(ins_c1y - INS_C1_HIGH + iy)]]]}
                else:                                # обычный вылет: подъём dn→up и спуск
                    # общий сдвиг точки покоя вставок кам1: парный к insert_c2_x/y,
                    # cx/cy _cam1_pos_keys и есть точка покоя — сдвиг считается здесь, в плане,
                    # и превью рисует готовое (правило одного источника)
                    out["anim"] = {"position": _cam1_pos_keys(t0r, t1r, noexit, ix, iy, _fps0,
                                                              cx=ins_c1x, cy=ins_c1y)}
        return out

    inserts_plan = [_ins_js(x) for x in inserts]
    inserts_js = _jd(inserts_plan)
    _video_segs = [(ins["start"], ins["end"]) for ins in inserts_plan if ins.get("t") == "video"]
    return InsertsPlan(inserts=inserts_plan, inserts_js=inserts_js, video_segs=_video_segs)
