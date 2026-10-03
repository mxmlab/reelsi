# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Дверь превью: расчёт рото и трекинга по кнопке, «что уже посчитано».

Зачем своя дверь. Рото и слежение за головой — единственные два этапа, которые видно
только в финальном рендере: до этой кнопки превью показывало полосу «здесь рото» на
таймлайне и ничего в кадре. Считать их на лету в `/api/scene` нельзя — это GPU-часы, и
роут обязан оставаться быстрым (его зовут на каждую правку вставок).

Что здесь и почему так:

* **свой джоб** (`PCJOB`/`PCLOCK`), как PXJOB у превью-прокси и RJOB у рендера: идти
  расчёт может минуты, и открытое превью не должно блокировать нарезку/сборку .jsx;
* **две двери в одной**: `build=false` (превью спрашивает при открытии) — быстрый ответ
  «что уже посчитано» по кэшам, GPU не трогается; `build=true` (кнопка) — то же плюс
  фоновый расчёт;
* **общий лок задач** (`_cross_lock_acquire`, тот же, что у job_start) — тяжёлый
  GPU-этап: нарезка, сборка, рендер и расчёт превью одновременно на одной видеокарте не
  идут (на Windows переполнение VRAM не даёт честный OOM, оно вешает машину);
* **тот же расчёт, что у сборки**: `core/xml2ae/precompute.py`. Кэши те же
  (`<стем>.head.json`, `<base>/roto/_cache/camN`), поэтому посчитанное здесь сборка
  берёт готовым — второго расчёта не бывает;
* **тело — как у `/api/scene`**, и нормализуется той же дверью (`api/build.py`,
  `_norm_build_jobs`): план сцены обязан совпасть с тем, по которому превью рисует кадр,
  иначе маски искались бы не по тем кускам.
* **кадр превью** несёт прогресс на каждом запросе статуса: брать его из `jobstate` не
  нужно, кнопка живёт внутри окна «Вставки».

Маски в ответе отдаются путями по кэшу: кадр показывает их через существующую раздачу
медиа (`/api/media`), своей раздачи у этой двери нет.
"""
import os
import threading
from typing import Any

from flask import Response, jsonify, request

from core.umsg import ReelsiError, umsg

from ._core import (bp, jstr, log_entry, sysexit_text, umsg_err,
                    _cross_lock_acquire, _cross_lock_release)

# Состояние джоба: этап, проценты и хвост лога. Замок свой — JOB у расчёта превью не
# занимается нарочно: иначе открытая кнопка гасила бы нарезку, которая идёт в фоне.
PCJOB: dict[str, Any] = {"running": False, "done": False, "cancel": False,
                         "stage": "", "pct": 0, "i": 0, "n": 0, "cur": "",
                         "roto": [], "head": {"cams": [], "ready": False},
                         # Сила жёлтых (core/emphasis.py): посчитан ли сайдкар и сколько
                         # в нём слов — тем же ответом, что прогресс этапов.
                         "emph": {"valid": False, "n": 0}, "log": []}
PCLOCK = threading.Lock()

# Сколько строк лога держим: тот же порядок величины, что у остальных дверей (PXJOB).
_LOG_TAIL = 200


def _emit(line: str, /, **vars: Any) -> None:
    """Строка в лог джоба. Если сборщик сообщает о начале куска рото — двигаем прогресс.

    Счёт «i из n» берём из самой строки `core/roto.py` про запуск RVM: у сборщика масок
    нет колбэка процентов, а лишнюю ручку в GPU-модуль ради полосы на экране не заводим.

    Знаменатель — из строки предрасчёта «рото: N кусков» (`chunks`): это ровно то число
    кусков, которое уходит на RVM. Свой счёт по `plan["roto"]` врал (2 при 14 кусках):
    куски отбирает `roto_masks` (нужен исходник камеры, `roto_cam1_only`), а микро-куски
    отсеивает `roto.alpha_for_ranges` — второго чтения этих правил в двери превью нет.
    """
    with PCLOCK:
        PCJOB["log"].append(log_entry(line, vars))
        if len(PCJOB["log"]) > _LOG_TAIL:
            del PCJOB["log"][:-_LOG_TAIL]
        if vars.get("name"):
            PCJOB["cur"] = str(vars["name"])
        # Строка «рото: {chunks} кусков по {cams} камере(ам)» — единственная с обоими
        # полями: по ней и узнаём знаменатель, до первого куска RVM.
        if vars.get("chunks") is not None and vars.get("cams") is not None:
            PCJOB["n"] = int(vars["chunks"])
            PCJOB["i"] = 0
        if line.startswith("RVM ("):
            PCJOB["i"] = int(PCJOB["i"]) + 1
            n = int(PCJOB["n"])
            if n:
                PCJOB["pct"] = int(max(0, min(100, round(PCJOB["i"] * 100.0 / n))))


def _stage(name: str, i: int = 0, n: int = 0) -> None:
    """Сменить этап джоба (то же поле рисует подпись под полосой на кадре)."""
    with PCLOCK:
        PCJOB.update(stage=name, i=i, n=n, pct=0)


def _cancel() -> bool:
    """«Нажали Стоп?» — колбэк для предрасчёта (`roto.alpha_for_ranges`)."""
    with PCLOCK:
        return bool(PCJOB["cancel"])


def _run_preview_calc(job: dict[str, Any]) -> None:
    """Фоновый расчёт: трек головы и маски рото — тем же кодом, что сборка.

    `job` — нормализованное тело (та же дверь, что у `/api/scene`): план сцены считается
    ровно с теми же аргументами, что у превью, иначе разметка рото была бы другой.

    Завершившееся с ошибкой задание не роняет сервер: причина уходит в лог джоба и в
    `done`, а кнопка на кадре показывает «готово»/ошибку. Недосчитанное рото — не
    ошибка двери: маски, которые успели посчитаться, уже лежат в кэше, и превью их
    покажет (сборка на них же и упадёт, если чего-то не хватит).
    """
    from core import xml2ae
    xml_path = job.get("xml_path") or ""
    style = job.get("style")
    try:
        _stage("plan")
        # Сила жёлтых — ДО плана: `scene_plan` читает её сайдкар и решает, на какие
        # жёлтые ставить наезд (правило «наезд только на сильные»), поэтому посчитанное
        # после плана до наездов бы не дошло. Кэш тот же, что у сборки.
        _stage("yellow")
        emph = xml2ae.precompute.emphasis_precompute(
            xml_path, style, idx=job.get("highlights"), emit=_emit, cancel=_cancel,
            ncams=job.get("ncams"), intro=job.get("intro"),
            intro_splits=job.get("intro_splits"), intro_remove=job.get("intro_remove"))
        with PCLOCK:
            PCJOB["emph"] = {"valid": bool(getattr(emph, "valid", False)),
                             "n": len(getattr(emph, "scores", {}) or {})}
        plan = xml2ae.scene_plan(emit=_emit, **job)
        # Знаменатель прогресса рото ставит сам предрасчёт — строкой «рото: N кусков»
        # (см. `_emit`): сколько кусков реально уйдёт на RVM, знает только `roto_masks`.
        _stage("head", 0, 2)
        head = xml2ae.precompute.head_track(xml_path, style, emit=_emit, cancel=_cancel,
                                            ncams=job.get("ncams"))
        with PCLOCK:
            PCJOB["head"] = {"cams": list(head.get("cams") or []),
                             "ready": bool(head.get("cams"))}
        # n здесь ещё 0: знаменатель придёт строкой «рото: N кусков» из roto_masks.
        _stage("roto", 0, int(PCJOB["n"]))
        roto = xml2ae.precompute.roto_masks(plan, xml_path, job,
                                            emit=_emit, cancel=_cancel, strict=False)
        with PCLOCK:
            PCJOB["roto"] = list(roto)
            PCJOB["pct"] = 100
    except ReelsiError as e:
        with PCLOCK:
            PCJOB["log"].append(sysexit_text(e))
    except Exception:
        import traceback
        with PCLOCK:
            PCJOB["log"].append(traceback.format_exc().strip().splitlines()[-1])
    finally:
        try:
            _cross_lock_release()
        finally:
            with PCLOCK:
                PCJOB.update(running=False, done=True, cur="", stage="")


@bp.route("/api/preview_calc", methods=["POST"])
def api_preview_calc() -> Response:
    """Расчёт рото и трекинга для превью. body: как у /api/scene + `build`.

    `build` не задан/false — быстрая дверь «что уже посчитано»: ответ по кэшам, без GPU
    (превью зовёт её при открытии). `build=true` — плюс фоновый расчёт под общим локом
    GPU: кнопка «Рассчитать рото и трекинг».

    Возвращает «что уже посчитано» сразу (по кэшам, без GPU) и `building` — идёт ли
    расчёт. Второй расчёт поверх идущего не запускается: два потока RVM на одной
    видеокарте — это переполнение VRAM, а не ускорение (ответ `building=true`).
    """
    d = request.get_json() or {}
    xml = jstr(d, "xml").strip().strip('"')
    try:
        if not os.path.isfile(xml):
            raise ReelsiError(umsg("file_not_found", f"Файл не найден: {xml}", path=xml))
        try:
            # Тело нормализует ТА ЖЕ дверь, что /api/scene: иначе план сцены вышел бы
            # другим (XML-вставки, свой roto) и маски искались бы не по тем кускам.
            from .build import _norm_build_jobs
            from core import xml2ae
            job = _norm_build_jobs([d])[0]
            job.pop("outdir", None)          # папка .jsx — только для сборки, в план не идёт
            ready = xml2ae.precompute.cached_plan(**job)
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("scene_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
        start = False
        with PCLOCK:
            busy = bool(PCJOB["running"])
            # Быстрая дверь НИЧЕГО не запускает: она спрашивается при каждом открытии
            # превью, а GPU-часы тратит только кнопка (build=true). Уже идёт — второй
            # RVM поверх первого не запускаем: видеокарта одна.
            if d.get("build") and not busy:
                if not _cross_lock_acquire():
                    raise ReelsiError(umsg("busy", "Уже выполняется"))
                PCJOB.update(running=True, done=False, cancel=False, stage="plan", pct=0,
                             i=0, n=0, cur="", roto=[],
                             head={"cams": [], "ready": False}, emph={"valid": False, "n": 0},
                             log=[])
                busy = True
                start = True
        if start:
            try:
                threading.Thread(target=_run_preview_calc,
                                 args=(job,), daemon=True).start()
            except ReelsiError: raise
            except Exception:
                # Поток не родился — отпускаем ровно то, что занял: иначе лок висел бы
                # до перезапуска сервера, и не запускалось бы ничего (образец — api/render.py).
                _cross_lock_release()
                with PCLOCK:
                    PCJOB["running"] = False
                raise
        return jsonify(ok=True, building=busy, **ready)
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/preview_calc_status")
def api_preview_calc_status() -> Response:
    """Прогресс расчёта превью: этап, процент, i/n текущего этапа, хвост лога и результат.

    Результат (`roto`, `head`) отдаётся здесь же, а не отдельным запросом: полоса
    прогресса и готовые маски приезжают одним ответом, и превью не гоняет вторую дверь.
    """
    with PCLOCK:
        head = PCJOB["head"] if isinstance(PCJOB["head"], dict) else {"cams": [], "ready": False}
        emph = PCJOB.get("emph") if isinstance(PCJOB.get("emph"), dict) else {"valid": False, "n": 0}
        return jsonify(ok=True, running=PCJOB["running"], done=PCJOB["done"],
                       stage=PCJOB["stage"], pct=PCJOB["pct"], i=PCJOB["i"],
                       n=PCJOB["n"], cur=PCJOB["cur"], roto=list(PCJOB["roto"]),
                       head={"cams": list(head.get("cams") or []),
                             "ready": bool(head.get("ready"))},
                       emph={"valid": bool(emph.get("valid")), "n": int(emph.get("n") or 0)},
                       log=PCJOB["log"][-40:])


@bp.route("/api/preview_calc_cancel", methods=["POST"])
def api_preview_calc_cancel() -> Response:
    """«Стоп» расчёта превью: флаг джобу, текущий кусок рото досчитывается.

    Общий `/api/cancel` тут не годится: он гасит нарезку, рендер и голос заодно, а
    кнопка в окне превью обязана останавливать только своё. Готовые маски остаются в
    кэше — следующая сборка (или вторая попытка) их не пересчитывает.
    """
    with PCLOCK:
        PCJOB["cancel"] = True
    return jsonify(ok=True)


def status_snapshot() -> dict[str, Any]:
    """Снимок состояния джоба — для тестов и соседних дверей (без Flask-ответа)."""
    with PCLOCK:
        return {k: (list(v) if isinstance(v, list) else v) for k, v in PCJOB.items()}


def reset_state() -> None:
    """Сброс состояния джоба (тесты и повторный старт): как PXJOB у прокси."""
    with PCLOCK:
        PCJOB.update(running=False, done=False, cancel=False, stage="", pct=0,
                     i=0, n=0, cur="", roto=[], head={"cams": [], "ready": False},
                     emph={"valid": False, "n": 0}, log=[])
