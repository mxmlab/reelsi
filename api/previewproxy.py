# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Превью-прокси камер: чтобы предпросмотр не замирал на стыках.

Материал 4:2:2 10 бит (Sony/Canon) браузер НЕ берёт на аппаратный декодер —
mediaCapabilities отвечает powerEfficient=false, и 4K декодируется софтом на проце.
Каждый seek на стыке стоит сотни мс, отсюда «встаёт в разрезе». Тот же материал в
720p 4:2:0 8 бит аппаратный и секается десятками мс.

Прокси — ОТ ИСХОДНИКА, а не от монтажа: один раз на файл камеры, дальше из кэша.
Правки нарезки его не трогают — это принципиально другой зверь, чем черновик,
который надо пересобирать после каждой правки.
"""
import os, threading
from flask import request, jsonify
from ._core import bp, umsg_err
from core.umsg import umsg

PXJOB = {"running": False, "done": False, "log": [], "cur": "", "i": 0, "n": 0}
PXLOCK = threading.Lock()


def _preview_proxy_plan(xml_path, height=720):
    """[(src, proxy_path, готов ли)] по камерам XML + папка кэша."""
    from core import draftrender
    from core import xml2ae
    edl = xml2ae.virtual_edl(xml_path)
    tdir = draftrender.tmp_dir(xml_path)
    out = []
    for c in edl.get("cams") or []:
        src = c.get("path")
        if not (src and os.path.isfile(src)):
            continue
        dst = draftrender.preview_path(src, height, tdir)
        out.append((src, dst, os.path.isfile(dst) and os.path.getsize(dst) > 0))
    return out, tdir


def _run_preview_proxy(plan, height):
    """Фоновая сборка недостающих прокси. Свой джоб, а не общий JOB: нарезка\сборка
    .jsx не должны блокироваться тем, что юзер открыл предпросмотр."""
    from core import draftrender
    todo = [(s, d) for (s, d, ok) in plan if not ok]
    try:
        with PXLOCK:
            PXJOB.update(running=True, done=False, log=[], i=0, n=len(todo), cur="")
        for k, (src, dst) in enumerate(todo, 1):
            with PXLOCK:
                PXJOB.update(i=k, cur=os.path.basename(src))
            def _emit(line, **vars):
                with PXLOCK:
                    entry = {"t": str(line), "v": vars} if vars else str(line)
                    PXJOB["log"].append(entry)
            _emit("превью-прокси {cur}/{total}: {name}",
                  cur=k, total=len(todo), name=os.path.basename(src))
            draftrender.build_preview_proxy(src, dst, height=height, emit=_emit)
    except Exception:
        import traceback
        with PXLOCK:
            PXJOB["log"].append(traceback.format_exc().strip().splitlines()[-1])
    finally:
        with PXLOCK:
            PXJOB.update(running=False, done=True, cur="")


@bp.route("/api/preview_proxy", methods=["POST"])
def api_preview_proxy():
    """Прокси камер для предпросмотра. body: {xml, build?: bool}.

    Возвращает по каждой камере путь к прокси и готов ли он. build=true — запустить
    фоновую сборку недостающих. Пока прокси нет, интерфейс играет исходник (как раньше)."""
    d = request.get_json() or {}
    xml_path = (d.get("xml") or "").strip().strip('"')
    try:
        if not os.path.isfile(xml_path):
            raise SystemExit(umsg("file_not_found", f"Файл не найден: {xml_path}",
                                  path=xml_path))
        height = int(d.get("height") or 720)
        try:
            plan, tdir = _preview_proxy_plan(xml_path, height)
        except Exception as e:
            raise SystemExit(umsg("preview_plan_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except SystemExit as e:
        return jsonify(**umsg_err(e))
    # Два одновременных запроса (два таба) не должны запустить ДВА сборщика в один
    # детерминированный dst (pv_<sha1>.part.mp4): перемешанные потоки кадров уехали
    # бы в кэш насовсем. Проверка И пометка «running» — под одним PXLOCK, поток — после.
    start = False
    with PXLOCK:
        busy = PXJOB["running"]
        if d.get("build") and not busy and any(not ok for (_s, _d, ok) in plan):
            PXJOB.update(running=True, done=False, log=[], i=0, n=0, cur="")
            busy = True
            start = True
    if start:
        threading.Thread(target=_run_preview_proxy, args=(plan, height), daemon=True).start()
    return jsonify(ok=True, dir=tdir, building=busy,
                   cams=[{"path": s, "proxy": p, "ready": ok} for (s, p, ok) in plan])


@bp.route("/api/preview_proxy_status")
def api_preview_proxy_status():
    """Прогресс фоновой сборки превью-прокси."""
    with PXLOCK:
        return jsonify(ok=True, **{k: v for k, v in PXJOB.items() if k != "log"},
                       log=PXJOB["log"][-40:])
