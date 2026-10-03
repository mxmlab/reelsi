# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Предрасчёт для сборки и превью: трек головы, маски рото и сила жёлтых — ОДНИ функции.

Зачем отдельный модуль. Оба тяжёлых этапа до .jsx (слежение за головой и рото-маски)
раньше жили прямо в `to_ae_full`: чтобы увидеть их в превью, пришлось бы либо ждать
финальной сборки, либо завести ВТОРУЮ копию расчёта — а вторая копия рано или поздно
разъезжается с первой (и тогда превью показывает не то, что уедет в AE). Поэтому
расчёт вынесен сюда, а сборка и дверь превью зовут ОДНИ И ТЕ ЖЕ функции:

* `head_track` — трек головы по камерам стиля (`cam1_head_follow`/`cam2_head_follow`);
* `roto_masks` — рото-маски по разметке `plan["roto"]`;
* `emphasis_precompute` — сила жёлтых слов (`<стем>.emph.json`, `core/emphasis.py`):
  сайдкар силы для правила «наезд только на сильные жёлтые»;
* `precompute` — предстадии вместе: то, что зовёт превью;
* `cached_plan` — что УЖЕ посчитано, по кэшам и без GPU (быстрый ответ превью).

Кэши те же, что у сборки: `<стем>.head.json` (`core/headtrack.py`), `<стем>.emph.json`
(`core/emphasis.py`) и `<base>/roto/_cache/camN/roto_<хэш>.mp4` (`core/roto.py`).
Посчитанное в превью сборка берёт готовым, и наоборот — второго расчёта не бывает.
"""
import os
from typing import Any, Callable, Sequence

from core import emphasis as _emphasis
from core import frame as _frame
from core import headtrack as _headtrack
from core import roto as _roto
from core import styles as _styles
from core.app_meta import console_emit, wrap_emit
from core.applog import get_logger
from core.umsg import ReelsiError, UMsg, umsg

from .jsutil import _jd, _r
from .layout import _project_base
from .parse import Cancelled, parse_full
from .plan_intro import intro_hl_words
from .plan_style import read_style
from .plan_words import WordsInputs, plan_words

log = get_logger(__name__)

# Камеры слежения за головой: номер камеры -> ключ стиля. Порядок тот же, что был в
# `to_ae_full`: сначала Камера 1, потом Камера 2.
_FOLLOW_KEYS = ((1, "cam1_head_follow"), (2, "cam2_head_follow"))


class RotoIncomplete(ReelsiError):
    """Маски рото посчитаны не для всех кусков плана.

    Отдельный класс, а не общий ReelsiError: сборке это ошибка (в .jsx неполные маски
    писать нельзя), а превью — «ещё не готово»: маски лежат в кэше, ролик досчитается
    без них. Код перевода и переменные — те же, что печатала сборка (`roto_incomplete`).
    """

    def __init__(self, msg: str | UMsg, *, n: int, m: int, error: str) -> None:
        super().__init__(msg)
        self.n = n
        self.m = m
        self.error = error


def head_track(xml_path: str, style: Any = None, emit: Any = console_emit,
               cancel: Callable[[], bool] | None = None,
               ncams: Any = None) -> dict[str, Any]:
    """Трек головы по камерам, включённым в стиле: из кэша `<стем>.head.json` или расчёт.

    Возвращает {"cams": [номера посчитанных камер], "failed": {номер: причина}}.
    Сбой трека сборку НЕ роняет (как и раньше): камера, на которой он не удался, просто
    не попадёт в ключи слежения плана. Возврат нужен превью — по нему видно, что «трек
    готов», а что нет.

    «Стоп» (Cancelled) пробрасывается: это команда человека, а не «трек не вышел».
    """
    emit = wrap_emit(emit)
    st_pre = _styles.resolve(style)
    parsed: Any = None
    done: list[int] = []
    failed: dict[int, str] = {}
    for c_idx, follow_key in _FOLLOW_KEYS:
        if not bool(st_pre.get(follow_key)):
            continue
        try:
            if parsed is None:
                parsed = parse_full(xml_path, ncams=ncams)
            meta_pre, cams_pre, _, _ = parsed
            idx = c_idx - 1
            if len(cams_pre) > idx and cams_pre[idx].get("path") and os.path.isfile(cams_pre[idx]["path"]):
                ranges = _headtrack.cam1_ranges(cams_pre, meta_pre.get("fps"), cam=c_idx)
                if ranges:
                    _headtrack.load_or_track(xml_path, cams_pre[idx]["path"], ranges,
                                             emit=emit, cancel=cancel, fps=10, cam=c_idx)
                    done.append(c_idx)
        except Cancelled:
            raise
        except ReelsiError:
            raise
        except Exception as ex:
            failed[c_idx] = str(ex)
            emit("слежение за головой пропущено: {err}", err=ex)
    return {"cams": done, "failed": failed}


def emphasis_precompute(xml_path: str, style: Any = None, idx: Any = None,
                        emit: Any = console_emit,
                        cancel: Callable[[], bool] | None = None,
                        ncams: Any = None, intro: Any = None, intro_splits: Any = None,
                        intro_remove: Any = None) -> "_emphasis.EmphasisView":
    """Сила жёлтых слов: посчитать сайдкар `<стем>.emph.json`, если он не готов.

    Одна дверь на сборку и на кнопку превью — как у `head_track`: посчитанное здесь
    сборка читает готовым, и второго расчёта не бывает. Считается ТОЛЬКО при включённом
    «наезде на жёлтых» у камеры стиля: выключены оба — наездов по жёлтым нет вовсе, и
    грузить модель эмфазы не для чего.

    `intro`/`intro_splits`/`intro_remove` — строки интро из задания: выделенные цветом
    слова интро наезжают наравне с жёлтыми словами ролика, и их силы обязаны лежать в
    том же сайдкаре (слова берёт `plan_intro.intro_hl_words` — та же дверь, что у плана
    сцены, поэтому набор слов у расчёта и чтения совпадает).

    Модель эмфазы — GPU: держать её после расчёта нельзя (на Windows переполнение VRAM
    вешает машину), поэтому выгрузка — внутри `emphasis.compute_emphasis`. В режиме
    «по голосу» (`hl_zoom_strength=voice`) модель эмоций не грузится ВООБЩЕ: сила
    считается по акустике, и решения владельца «переключать способ без пересчёта» это
    не нарушает — в сайдкар уезжает только компонента ударения. Лок задач
    общий и берётся ВЫШЕ по стеку (джоб сборки, дверь превью): здесь своего замка нет.

    Возвращает прочитанный сайдкар (`EmphasisView`): `valid=False` — сила не посчитана
    (нет звука, нет модели), и правило наезда возвращается к прежнему.
    `style=None` — стиль вызывающему неизвестен (шаг ИИ-жёлтых): галка не проверяется,
    сила считается (лишний расчёт дешевле забытого сайдкара).
    """
    emit = wrap_emit(emit)
    # Способ оценки силы (`hl_zoom_strength`): «по голосу» — считать только ударение,
    # модель эмоций не грузить вовсе. Ключа нет / стиль неизвестен — способ по умолчанию
    # («по эмоциям»), и считаются обе составляющие: переключение потом ничего не пересчитает.
    mode = _emphasis.HL_MODE_DEFAULT
    if style is not None:
        stv = read_style(_styles.resolve(style), *_frame.output_frame_size(xml_path))
        mode = _emphasis.hl_mode(stv.hl_zoom_strength)
        if not (stv.cam1_yellow_zoom or stv.cam2_yellow_zoom):
            # Наезд на жёлтых выключен у обеих камер: наездов по жёлтым нет вовсе, и
            # грузить модель эмфазы не для чего.
            return _emphasis.read_emphasis(xml_path, [], ())
    meta, cams, subs, _xi = parse_full(xml_path, ncams=ncams)
    fps = meta.get("fps") or 60
    source = (cams[0].get("path") if cams else "") or ""
    # Нумер слов — как у ПЛАНА, а не как в XML: слова интро план вынимает из титров и
    # переиндексирует разметку (`plan_words`), поэтому слова и жёлтые считаем той же
    # дверью. Иначе сайдкар не совпал бы по ключу с тем, что читает `scene_plan`, и
    # правило силы молча выключилось бы на ролике с интро.
    _wp = plan_words(WordsInputs(
        subs=subs, highlights=idx, hl_breaks=None, hl_count=None, hl_joins=None,
        word_timings=None, xml_path=xml_path, intro_remove=intro_remove))
    hl = sorted(int(k) for k in _wp.hl)
    words = _emphasis.word_refs(_wp.subs, float(fps))
    # Времена слов интро — из ПОЛНОГО списка (`subs` до вырезания): `intro_remove` —
    # индексы исходного списка ролика.
    intro_words = intro_hl_words(intro, intro_splits, intro_remove, subs)
    need = list(hl) + [len(_wp.subs) + j for j in range(len(intro_words))]
    view = _emphasis.read_emphasis(xml_path, words, intro_words, hl, source, idx=need, mode=mode)
    if view.valid and not view.uncomputed:
        return view
    if not hl and not intro_words:
        return view
    try:
        _emphasis.compute_emphasis(_emphasis.EmphasisInputs(
            words=words, parsed=(meta, cams, subs, _xi), xml_path=xml_path,
            idx=hl, intro_words=intro_words, mode=mode, emit=emit, cancel=cancel))
    except Cancelled:
        raise
    except ReelsiError:
        raise
    except Exception as ex:
        # Сила — украшение наезда, а не сборка: не посчиталась (нет gigaam, нет звука) —
        # наезд идёт на каждую фразу, и об этом честно сказано в лог.
        log.warning("сила жёлтых не посчитана: %s", ex)
        emit("  ! сила жёлтых не посчитана ({err}) — наезд на каждую фразу хайлайта", err=ex)
    return _emphasis.read_emphasis(xml_path, words, intro_words, hl, source, idx=need, mode=mode)


def _release_roto(emit: Any) -> None:
    """Выгрузить RVM из видеопамяти после масок рото — ОДНА дверь на сборку и превью.

    Держать модель после расчёта нельзя: на Windows переполнение VRAM не даёт честный
    OOM, оно вешает машину. Раньше выгружала только сборка (`build._roto_js`), и превью
    после кнопки «Рассчитать рото и трекинг» держало ~2.4 ГБ до перезапуска сервера.
    Вызов ровно один: сборка своей выгрузки больше не делает.
    """
    try:
        _roto.release(emit=emit)
    except ReelsiError:
        raise
    except Exception as ex:
        # Не выгрузилась — модель держит видеопамять, и следующий расчёт упадёт по
        # памяти без видимой причины: говорим в лог и в вывод.
        log.warning("RVM не выгрузился после рото: %s", ex)
        emit("  ! модель рото не выгрузилась ({err}) — видеопамять занята "
             "до перезапуска сервера", err=ex)


def roto_masks(plan: Any, xml_path: str, kw: Any, emit: Any = console_emit,
               cancel: Callable[[], bool] | None = None,
               strict: bool = True) -> list[dict[str, Any]]:
    """Маски рото (GPU, самый долгий этап) по разметке `plan["roto"]`.

    Кусок плана с найденной маской превращается в запись контракта .jsx:
    {"ci","ts","te","cs","scale","mf","mask"} — ровно то, что уезжает в `var ROTO=`.
    Плюс два поля для превью, которых в .jsx нет, но которые нужны слою рото на экране:
    "src_start" (начало куска в исходнике) и "path" (исходник камеры куска). Кадр маски
    превью берёт по времени ВНУТРИ куска (t − ts): файл маски начинается с его начала, а
    в .jsx у слоя маски startTime=ts — `src_start` нужен только для `cs`.

    RVM выгружается из видеопамяти ЗДЕСЬ (в finally): и сборка, и кнопка превью считают
    одним и тем же вызовом, а значит одинаково отпускают память.

    `strict=True` (сборка): недосчитанные куски — ошибка `RotoIncomplete`, в .jsx
    неполные маски писать нельзя. `strict=False` (превью): отдаём то, что есть.
    """
    emit = wrap_emit(emit)
    if not kw.get("roto") or not plan.get("roto"):
        return []
    # Кадр ролика — тот же, что у плана: он нужен только для roto_cam1_only, но пусть
    # стиль читается по тому же кадру, что и у плана (как было в сборке).
    stv = read_style(_styles.resolve(kw.get("style")),
                     *_frame.output_frame_size(xml_path))
    cams = plan["cams"]
    items = [p for p in plan["roto"] if cams[p["ci"]].get("path")]   # нужен исходник камеры
    if stv.roto_cam1_only:             # рото только на кусках Камеры 1 (cam2 без рото)
        items = [p for p in items if p["ci"] == 0]
    if not items:
        return []
    # ОБЩИЙ кэш масок (имена по хэшу камера+фрагмент+низ) — реюз между пересборками
    # и XML: тот же камера+кусок не пересчитывается заново. Overwrite исключён (имена
    # уникальны по содержимому), поэтому одна папка на весь набор.
    base = kw.get("base") or _project_base(xml_path)
    roto_dir = os.path.join(base, "roto", "_cache")
    masks_by_cam: dict[int, Any] = {}    # маски делаем из ИСХОДНИКА своей камеры
    by_cam: dict[int, list[Any]] = {}
    for p in items:
        by_cam.setdefault(p["ci"], []).append(p)
    emit("  · рото: {chunks} кусков по {cams} камере(ам) — самый долгий этап сборки",
         chunks=len(items), cams=len(by_cam))
    failures: list[dict[str, Any]] = []
    try:
        for ci, ps in by_cam.items():
            masks_by_cam[ci] = _roto.alpha_for_ranges(
                cams[ci]["path"], [(p["src_start"], p["src_end"]) for p in ps],
                os.path.join(roto_dir, "cam%d" % (ci + 1)),
                bottom_pct=float(kw.get("roto_bottom") or 0), device=kw.get("roto_device"),
                emit=emit, cancel=cancel, failures=failures)
            # «Стоп» поймала alpha_for_ranges (проверяет флаг перед каждым куском): остальные
            # камеры не считаем, а готовое отдаём наружу — превью покажет посчитанное, сборку
            # прервёт вызывающий (в _roto_js «Стоп» проверяется сразу после этого вызова).
            if cancel is not None and cancel():
                break
    finally:
        # Выгрузка — в ОБЩЕЙ функции, а не у сборки: кнопку превью жмут на том же GPU,
        # и забытая в VRAM модель вешает машину на следующем расчёте.
        _release_roto(emit)
    ents: list[dict[str, Any]] = []
    missing: list[Any] = []
    for p in items:
        ms = masks_by_cam.get(p["ci"], [])
        m = next((mm for mm in ms if abs(mm["start"] - p["src_start"]) < 0.02), None)
        if m:
            ents.append({"ci": p["ci"], "ts": p["ts"], "te": p["te"],
                         "cs": _r(p["ts"] - p["src_start"]),
                         "scale": p["scale"], "mf": _r(m.get("f") or 1),
                         "mask": m["mask"], "path": cams[p["ci"]]["path"],
                         "src_start": _r(p["src_start"]), "src_end": _r(p["src_end"])})
        elif (p["src_end"] - p["src_start"]) >= _roto.MIN_SEG_SEC:
            missing.append(p)
    if missing and strict:
        non_micro = [p for p in items if (p["src_end"] - p["src_start"]) >= _roto.MIN_SEG_SEC]
        n = len(missing)
        m = len(non_micro)
        first_err = failures[0]["error"] if failures else "маска не найдена"
        msg = (f"рото не посчитано для {n} из {m} кусков "
               f"(первая причина: {first_err}). "
               f"Готовые маски в кэше — собери заново, или сними галку рото в стиле")
        raise RotoIncomplete(umsg("roto_incomplete", msg, n=n, m=m, err=first_err,
                                  error=first_err), n=n, m=m, error=first_err)
    return ents


def precompute(xml_path: str, style: Any = None, emit: Any = console_emit,
               cancel: Callable[[], bool] | None = None, *,
               plan: Any = None, strict: bool = True, ncams: Any = None,
               **kw: Any) -> dict[str, Any]:
    """Предстадии сборки одним вызовом: трек головы, сила жёлтых, маски рото.

    Ровно это зовёт превью по кнопке «Рассчитать рото и трекинг», и то же самое делает
    сборка (`to_ae_full`) — второй копии расчёта нет. `plan` уже посчитан вызывающим —
    второй `scene_plan` не гоняем (иначе разметка рото считалась бы дважды).

    Сила жёлтых — ДО `scene_plan`: план читает сайдкар и решает, на какие жёлтые ставить
    наезд, поэтому посчитанное после плана до наездов бы не дошло. Свой `plan` от
    вызывающего в этом случае уже содержит прежние наезды — это его ответственность
    (дверь превью строит план ПОСЛЕ этого вызова).

    Emph читает сам план (сайдкар к этому моменту уже на диске): второй двери чтения нет.
    """
    emit = wrap_emit(emit)
    if style is not None and not kw.get("style"):
        kw["style"] = style
    # Сила жёлтых — ДО плана: `scene_plan` читает её сайдкар и по ней решает, на какие
    # жёлтые ставить наезд. Посчитанное после плана до наездов бы не дошло.
    emph = emphasis_precompute(xml_path, kw.get("style"), idx=kw.get("highlights"),
                               emit=emit, cancel=cancel, ncams=ncams,
                               intro=kw.get("intro"), intro_splits=kw.get("intro_splits"),
                               intro_remove=kw.get("intro_remove"))
    head = head_track(xml_path, kw.get("style"), emit=emit, cancel=cancel, ncams=ncams)
    if plan is None:
        from .build import scene_plan
        if ncams is not None and kw.get("ncams") is None:
            kw["ncams"] = ncams           # план считается с тем же числом камер, что и трек
        plan = scene_plan(xml_path, emit=emit, cancel=cancel, **kw)
    roto = roto_masks(plan, xml_path, kw, emit=emit, cancel=cancel, strict=strict)
    return {"roto": roto, "head": head, "emph": emph}


def _mask_from_cache(video: str, s: float, e: float, bottom_pct: float,
                     mask_dir: str) -> str | None:
    """Путь готовой маски куска (s,e) в кэше или None.

    Имя маски считает `core/roto.py` (`_mask_key`), а лежит она в подпапке своей камеры
    (`<base>/roto/_cache/camN` — туда её кладёт `alpha_for_ranges`): второго правила
    «где лежит кэш» заводить нельзя — превью обязано найти ровно тот файл, который
    положит сборка.
    """
    div = _roto._mask_scale_div(_roto._probe(video)[1] or 0)
    mask = os.path.join(mask_dir, "roto_%s.mp4"
                        % _roto._mask_key(video, s, e, bottom_pct, div))
    return mask if _roto._has_frames(mask) else None


def _style_flag(style: Any, key: str, default: bool) -> bool:
    """Галка стиля из СЫРОГО словаря стиля (или из структуры `StyleValues`).

    Часть галок (`roto`, `roto_cam1_only`) живёт только в сыром стиле: их читает
    `api/build.py` и передаёт в план отдельными аргументами, поэтому в структуре их нет.
    Флага нет нигде — берём `default` (умолчание `core/styles.py BASE`).
    """
    st = _styles.resolve(style)
    if isinstance(st, dict):
        v = st.get(key)
        if v is not None:
            return bool(v)
        return default
    return bool(getattr(st, key, default))


def cached_plan(xml_path: str, style: Any = None, emit: Any = console_emit,
                plan: Any = None, **kw: Any) -> dict[str, Any]:
    """Что УЖЕ посчитано по кэшам — без GPU и без единого кадра расчёта.

    Отдельная быстрая дверь: превью спрашивает её при открытии и после расчёта, чтобы
    нарисовать слой рото и не жать долгую кнопку зря. Разметка берётся из плана сцены
    (`scene_plan` считает и он же отдаётся превью), а наличие масок и трека проверяется
    по диску: маски — тем же ключом кэша, что у `core/roto.py`, трек — `load_cached`.

    Аргументы плана — те же, что у двери `/api/scene` (её тело приходит сюда целиком:
    `inserts`, `include_xml_inserts`, `roto`, `roto_bottom`, `style`…). Иначе план вышел бы
    ДРУГОЙ — с XML-вставками и другим рото — и быстрая дверь не нашла бы маски, которые
    сборка только что положила. `base`/`roto_device` берутся из тех же аргументов или
    считаются по XML: второй копии правила «какие куски ротоить» не заводится.

    Возвращает {"roto": [{"ci","ts","te","src_start","mask","cs","scale","mf"}],
    "head": {"cams": [номера с готовым треком], "ready": bool}}.
    """
    from .build import scene_plan
    raw = _styles.resolve(style)
    # `roto` — параметр ПЛАНА (его достаёт нормализация входа, api/build.py): пришёл
    # готовым — берём его, иначе галка стиля (умолчание core/styles.py: BASE["roto"]).
    roto_on = bool(kw["roto"]) if kw.get("roto") is not None else _style_flag(raw, "roto", True)
    cam1_only = _style_flag(raw, "roto_cam1_only", True)
    if kw.get("roto_bottom") is not None:
        bottom = float(kw["roto_bottom"] or 0)
    else:
        bottom = float((raw.get("roto_bottom") if isinstance(raw, dict) else None) or 0)
    stv = read_style(raw, *_frame.output_frame_size(xml_path))
    if plan is None:
        plan = scene_plan(xml_path, style=style, emit=emit, **kw)
    base = _project_base(xml_path)
    roto_dir = os.path.join(base, "roto", "_cache")
    out: list[dict[str, Any]] = []
    if roto_on and plan.get("roto"):
        cams = plan["cams"]
        for p in plan["roto"]:
            ci = p["ci"]
            if cam1_only and ci != 0:      # рото только на кусках Камеры 1 (как в сборке)
                continue
            path = cams[ci].get("path")
            if not path or not os.path.isfile(path):
                continue
            if (p["src_end"] - p["src_start"]) < _roto.MIN_SEG_SEC:
                continue
            mask = _mask_from_cache(path, p["src_start"], p["src_end"], bottom,
                                    os.path.join(roto_dir, "cam%d" % (ci + 1)))
            if not mask:
                continue
            div = _roto._mask_scale_div(_roto._probe(path)[1] or 0)
            out.append({"ci": ci, "ts": _r(p["ts"]), "te": _r(p["te"]),
                        "cs": _r(p["ts"] - p["src_start"]),
                        "src_start": _r(p["src_start"]), "src_end": _r(p["src_end"]),
                        "scale": p["scale"], "mf": _r(div), "mask": mask})
    head = {"cams": [], "ready": False}
    for c_idx, follow_key in _FOLLOW_KEYS:
        if not bool(getattr(stv, follow_key, False)):
            continue
        cam = plan["cams"][c_idx - 1] if len(plan.get("cams") or []) > (c_idx - 1) else None
        path = cam.get("path") if cam else None
        if not path or not os.path.isfile(path):
            continue
        if _headtrack.load_cached(xml_path, path, cam=c_idx) is not None:
            head["cams"].append(c_idx)
    head["ready"] = bool(head["cams"])
    return {"roto": out, "head": head}


def js_roto(entries: Sequence[Any]) -> str:
    """Записи масок в виде `var ROTO=` для .jsx: только контрактные поля.

    Поля превью ("path", "src_start") в .jsx не уезжают: шаблон читает ровно свой
    набор, и лишний ключ там — молчаливая правка контракта.
    """
    return _jd([{k: v for k, v in e.items()
                 if k not in ("path", "src_start", "src_end")}
                for e in entries])
