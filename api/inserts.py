# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""База вставок: скан, автоподбор, импорт, описания.
"""
import os, threading
from flask import request, jsonify
from ._core import bp, umsg_err
from core import paths
from core.umsg import umsg


def _insert_dest(d):
    """Папка базы вставок: из тела запроса («Папка базы» в модалке 📚) или дефолт."""
    return ((d.get("dest") or "").strip().strip('"')
            or os.path.join(os.path.dirname(paths.ROOT), "insert_library"))


def _convert_inserts(inserts, emit=None):
    """Нечитаемое для AE (webp/avif, CMYK-JPEG — в PNG; AV1-видео — в H.264)
    перекодируем в читаемое (см. insertlib.to_ae_media). Гоняем по КАЖДОМУ файлу,
    а не по списку расширений: и цветовая модель, и кодек видны только внутри
    файла, а importFile на таком роняет весь .jsx целиком. Перекодировка кладёт
    новый файл РЯДОМ с исходником, сам файл не трогает.
    -> {старый: новый} для перекодированных (исходник цел)."""
    from core import insertlib
    conv = {}
    for x in inserts:
        m = (x.get("media") or "").strip()
        if m and os.path.exists(m):
            new = insertlib.to_ae_media(m, emit=emit)
            if os.path.abspath(new) != os.path.abspath(m):
                conv[os.path.abspath(m)] = new
                x["media"] = new
    return conv


def _adopt_inserts(inserts, dest, emit=None):
    """Файлы вставок, уходящих в проект, переезжают из «Скаченного» в базу
    (<dest>/photos|videos) с описанием = запрос вставки. Пути в самих вставках
    подменяются ДО сборки, чтобы .jsx ссылался уже на новое место.
    -> {старый: новый} для переехавших (фронт чинит своё состояние)."""
    from core import insertlib
    # Перекодировка — ДО переезда в базу, чтобы и в проект, и в индекс попал
    # уже читаемый файл; вынесена в отдельную функцию, потому что рендер зовёт
    # только её (переезд в базу в рендере НЕ делаем — см. _run_render_job).
    conv = _convert_inserts(inserts, emit=emit)
    # mw/mh едут вместе с описанием: сборка — единственный момент, когда точно известна
    # форма маски, на которой юзер остановился. В следующих роликах match_many вернёт её
    # вместе с путём, и картинка приедет из базы уже с нужным кропом.
    items = [{"path": (x.get("media") or ""), "desc": (x.get("query") or ""),
              "ru": (x.get("prompt") or ""),
              "mw": x.get("mw"), "mh": x.get("mh")}
             for x in inserts if (x.get("media") or "").strip()]
    if not items:
        return conv
    try:
        moved = insertlib.adopt(items, dest, emit=emit)
    except Exception as e:                          # перенос НЕ должен ронять сборку
        (emit or (lambda *a: None))(f"⚠ прибрать в базу не вышло: {e}")
        return conv
    for x in inserts:
        m = os.path.abspath(x["media"]) if (x.get("media") or "").strip() else ""
        if m in moved:
            x["media"] = moved[m]
    # маппинг для фронта: старый webp -> его PNG -> куда PNG переехал в базе
    out = dict(moved)
    for old, png in conv.items():
        out[old] = moved.get(os.path.abspath(png), png)
    return out


@bp.route("/api/insertlib_info")
def api_insertlib_info():
    try:
        try:
            from core import insertlib
            return jsonify(ok=True, **insertlib.info())
        except Exception as e:
            raise SystemExit(umsg("insertlib_info_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except SystemExit as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/insertlib_scan", methods=["POST"])
def api_insertlib_scan():
    """Построить/обновить индекс базы вставок по списку папок (XML прошлых проектов +
    просто медиа). Эмбеддинги — LM Studio (если поднят), иначе токенный матч."""
    d = request.get_json() or {}
    dirs = [x for x in (d.get("dirs") or []) if (x or "").strip()]
    log = []
    try:
        if not dirs:
            raise SystemExit(umsg("need_folders", "Укажи хотя бы одну папку"))
        try:
            from core import insertlib
            res = insertlib.build_index(dirs, emit=lambda *a: log.append(" ".join(str(x) for x in a)))
            return jsonify(ok=True, log=log, **res)
        except Exception as e:
            raise SystemExit(umsg("insertlib_scan_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except SystemExit as e:
        r = umsg_err(e)
        r["log"] = log
        return jsonify(**r)


@bp.route("/api/insertlib_reject", methods=["POST"])
def api_insertlib_reject():
    """«Не предлагать этот файл под этот запрос» — ставится, когда юзер перегенеривает
    поверх автоподбора/генерации. Файл остаётся в базе (руками через 📚 доступен)."""
    d = request.get_json() or {}
    path, query = (d.get("path") or "").strip(), (d.get("query") or "").strip()
    try:
        if not path or not query:
            raise SystemExit(umsg("need_path_query", "Нужны path и query"))
        try:
            from core import insertlib
            return jsonify(ok=insertlib.reject(path, query, on=d.get("on", True) is not False))
        except Exception as e:
            raise SystemExit(umsg("insertlib_reject_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except SystemExit as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/insertlib_match", methods=["POST"])
def api_insertlib_match():
    """Подбор файлов из базы: {queries:[{q,type}], k} -> {results:[[{path,name,score},..],..]}.
    Один batch-вызов эмбеддера на все запросы. type ('photo'|'video') — жёсткий фильтр:
    под фото-вставку видео не предлагаем (см. match_many)."""
    d = request.get_json() or {}
    qs = d.get("queries") or []
    if not qs:
        return jsonify(results=[])
    try:
        try:
            from core import insertlib
            # Стиль текущего спикера (поле look) резолвится ТОЛЬКО здесь: insertlib
            # про спикеров знать не должен. Пустой спикер/приписка -> None (поведение
            # прежнее — стиль в подборе не участвует).
            look = None
            if (d.get("speaker") or "").strip():
                from core import aicut
                extra = aicut.resolve_image_prompt_cfg("a", speaker=d["speaker"])["extra"]
                look = insertlib._norm_look(extra) or None
            texts = [(x.get("q") or "") for x in qs]
            hints = [(x.get("type") or None) for x in qs]
            k = int(d.get("k") or 5)
            # match_many батчит эмбеддинги, но type_hint у каждого свой — группируем по hint
            results = [None] * len(qs)
            for hint in set(hints):
                idx = [i for i, h in enumerate(hints) if h == hint]
                rr = insertlib.match_many([texts[i] for i in idx], k=k, type_hint=hint,
                                          look=look)
                for i, r in zip(idx, rr):
                    results[i] = r
            return jsonify(ok=True, results=results, emb=bool(insertlib.info().get("emb_model")))
        except Exception as e:
            raise SystemExit(umsg("insertlib_match_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except SystemExit as e:
        return jsonify(**umsg_err(e))


ILL_JOB = {"running": False, "done": 0, "total": 0, "log": [], "error": ""}
ILL_LOCK = threading.Lock()


@bp.route("/api/insertlib_import", methods=["POST"])
def api_insertlib_import():
    """Перенести медиа из папок-источников в СВОЮ папку базы (photos/videos), с даты."""
    d = request.get_json() or {}
    dirs = [x for x in (d.get("dirs") or []) if (x or "").strip()]
    dest = (d.get("dest") or "").strip().strip('"')
    log = []
    try:
        if not dirs or not dest:
            raise SystemExit(umsg("need_src_and_db", "Нужны папки-источники и папка базы"))
        try:
            import datetime as _dt
            since = 0.0
            if (d.get("since") or "").strip():
                since = _dt.datetime.strptime(d["since"].strip(), "%Y-%m-%d").timestamp()
            from core import insertlib
            res = insertlib.import_media(dirs, dest, since_ts=since,
                                         emit=lambda *a: log.append(" ".join(str(x) for x in a)))
            return jsonify(ok=True, log=log, **res)
        except Exception as e:
            raise SystemExit(umsg("insertlib_import_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except SystemExit as e:
        r = umsg_err(e)
        r["log"] = log
        return jsonify(**r)


@bp.route("/api/insertlib_describe", methods=["POST"])
def api_insertlib_describe():
    """Vision-описания всех файлов базы (фоновый тред — vision по каждому файлу долгий)."""
    try:
        with ILL_LOCK:
            if ILL_JOB["running"]:
                raise SystemExit(umsg("describe_busy", "Описание уже идёт"))
            only_missing = bool((request.get_json() or {}).get("only_missing", True))
            ILL_JOB.update(running=True, done=0, total=0, log=[], error="")

        def _run():
            try:
                from core import insertlib

                def prog(done, total):
                    with ILL_LOCK:
                        ILL_JOB["done"], ILL_JOB["total"] = done, total

                def _emit(*a):
                    with ILL_LOCK:
                        ILL_JOB["log"].append(" ".join(str(x) for x in a))

                r = insertlib.auto_describe(emit=_emit,
                                            only_missing=only_missing, progress=prog)
                if r.get("error"):
                    with ILL_LOCK:
                        ILL_JOB["error"] = r["error"]
            except Exception as e:
                with ILL_LOCK:
                    ILL_JOB["error"] = f"{type(e).__name__}: {e}"
            finally:
                with ILL_LOCK:
                    ILL_JOB["running"] = False

        try:
            threading.Thread(target=_run, daemon=True).start()
        except Exception:
            with ILL_LOCK:
                ILL_JOB["running"] = False
            raise
        return jsonify(ok=True)
    except SystemExit as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/insertlib_describe_status")
def api_insertlib_describe_status():
    try:
        since = int(request.args.get("since") or 0)
    except (TypeError, ValueError):
        since = 0                    # ?since=abc роняло роут в HTML-500 (задание HL)
    with ILL_LOCK:
        return jsonify(running=ILL_JOB["running"], done=ILL_JOB["done"], total=ILL_JOB["total"],
                       error=ILL_JOB["error"], log=ILL_JOB["log"][since:], log_total=len(ILL_JOB["log"]))


@bp.route("/api/insertlib_desc", methods=["POST"])
def api_insertlib_desc():
    d = request.get_json() or {}
    try:
        try:
            from core import insertlib
            return jsonify(**insertlib.set_desc((d.get("path") or ""), d.get("desc") or ""))
        except Exception as e:
            raise SystemExit(umsg("insertlib_desc_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except SystemExit as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/insertlib_items", methods=["POST"])
def api_insertlib_items():
    d = request.get_json() or {}
    try:
        try:
            from core import insertlib
            return jsonify(ok=True, **insertlib.items_list(q=d.get("q") or "",
                                                           offset=int(d.get("offset") or 0),
                                                           limit=int(d.get("limit") or 50)))
        except Exception as e:
            raise SystemExit(umsg("insertlib_items_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except SystemExit as e:
        return jsonify(**umsg_err(e))
