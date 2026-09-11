# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сборка .jsx для After Effects и раскладка камер.
"""
import os, threading, traceback, urllib.parse
from flask import request, jsonify, send_file, Response
from core.fileio import atomic_json_dump
from ._core import (JOB, LOCK, bp, emit, item_done, item_set, items_init, job_finish,
                    job_start, set_progress, _never_serve, umsg_err)
from core.umsg import umsg
from .editor import _ensure_project, _sidecar_yellow, _sidecar_caption
from .inserts import _adopt_inserts, _insert_dest


# ==========================================================================
# Роуты, добавленные в webui (2026-07-13..17): сборка .jsx фоном, жёлтые/правка
# слов в XML, база вставок, раскладка камер. Перенесены сюда 1:1.
# ==========================================================================

def _norm_build_jobs(jobs_in):
    """Нормализация набора клипов для фонового джоба сборки (/api/build_run)."""
    from core import styles  # локальный импорт, как в соседних модулях api/
    norm = []
    for j in jobs_in:
        xml = (j.get("xml") or "").strip().strip('"')
        if not os.path.isfile(xml):
            raise ValueError(f"Файл не найден: {xml}")
        # Рото и громкость музыки — параметры СТИЛЯ, а не клипа (решение 2026-08-22):
        # клип хранит ИМЯ стиля, стиль живёт отдельно. Клиповые roto/roto_bottom
        # раньше всегда перебивали правку стиля, поэтому стиль резолвим ОДИН раз
        # на клип и из результата берём всё, что стилю принадлежит.
        st = styles.resolve(j.get("style"))
        norm.append(dict(
            xml_path=xml,
            # Папка для .jsx ИМЕННО этого клипа (задание N): тег спикера определяет
            # её у клипа, и каждый собирается в свою. Пусто = глобальное поле.
            outdir=(j.get("outdir") or "").strip().strip('"') or None,
            music=(j.get("music") or "").strip() or None,
            music_dir=(j.get("music_dir") or "").strip().strip('"') or None,
            highlights=(j.get("highlights") or _sidecar_yellow(xml)),
            caption=(j.get("caption") or _sidecar_caption(xml)),
            hl_breaks=j.get("hl_breaks") or [],
            hl_count=j.get("hl_count") or [],
            hl_joins=j.get("hl_joins") or [],
            inserts=j.get("inserts") or [],
            intro=j.get("intro") or [],
            intro_remove=j.get("intro_remove") or [],
            intro_splits=j.get("intro_splits") or [],
            ncams=j.get("cams") or None,
            exposure=float(j.get("exposure") or 0),
            intro_mode=j.get("intro_mode") or "word",
            roto=bool(st.get("roto")), roto_bottom=float(st.get("roto_bottom") or 0),
            roto_device=(j.get("roto_device") or "").strip().lower() or None,
            style=j.get("style") or None,
            music_db=float(st.get("music_db") if st.get("music_db") is not None else -20.0),
            music_random=bool(j.get("music_random")),
            censor_audio=bool(j.get("censor", True)),
            include_xml_inserts=False))
    return norm


def _run_build_job(norm, mode, outdir):
    """Фоновая сборка .jsx со стримом лога в общий JOB (как у нарезки)."""
    try:
        from core import xml2ae  # внутри try: ошибка импорта иначе оставляла JOB running=True навсегда
        emit("=== Сборка .jsx: {count} файл(ов), режим {mode} ===", count=len(norm), mode=mode)
        moved = {}                       # вставки уходят в проект -> прибрать в базу
        for j in norm:
            moved.update(_adopt_inserts(j.get("inserts") or [], j.pop("insdest", None)
                                        or _insert_dest({}), emit=emit))
        if moved:
            with LOCK:
                JOB["insmoved"] = moved  # фронт починит пути у себя, когда джоб добежит
        # Очередь этапов по стемам набора (задание FA): элементы заводятся по клипам
        # даже в combined-режиме — видно, какие клипы вошли в общий .jsx.
        items_init(JOB, LOCK, [os.path.splitext(os.path.basename(j["xml_path"]))[0] for j in norm])
        stopped = lambda: bool(JOB["cancel"])   # «Стоп» проверяется ВНУТРИ файла (рото), не только между
        # Куда уедет .jsx — В ЛОГ, до начала работы. Раньше папка не печаталась нигде, а
        # НЕПУСТОЕ, но несуществующее значение молча подменялось на папку XML: человек
        # правил поле, жал сборку и искал файл там, где его нет (жалоба 2026-08-11).
        # Сначала это стало честной ошибкой, теперь (жалоба 2026-08-12) папка создаётся
        # сама — молчаливая подмена пути недопустима в любом случае: файл ляжет ровно
        # в указанную папку, а не в соседнюю.
        def ensure_dir(od):
            if od and not os.path.isdir(od):
                try:
                    os.makedirs(od, exist_ok=True)
                    emit("Папка создана: {dir}", dir=od)
                except OSError as e:
                    emit("ОШИБКА: не удалось создать папку для .jsx — {dir}", dir=od)
                    emit("  {err}", err=str(e.strerror or e))
                    emit("Ничего не собрано.")
                    with LOCK:
                        JOB["failed"].append({"name": "сборка", "reason": f"не создать папку: {od}"})
                    return False
            if od:
                emit("Папка для .jsx: {dir}", dir=od)
            else:
                emit("Папка для .jsx не задана — каждый файл ляжет рядом со своим XML")
            return True
        if mode == "combined":
            od = outdir or os.path.dirname(norm[0]["xml_path"])
            if not ensure_dir(od):
                return
            # outdir — параметр СБОРКИ, а не плана сцены: build_combined отдаёт весь
            # словарь в to_ae_full(**kw) -> scene_plan, и лишний ключ ронял общий .jsx
            # (TypeError: scene_plan() got an unexpected keyword argument 'outdir')
            # с тех пор, как у клипа появилась своя папка по тегу спикера (задание N).
            # В поштучной ветке и в рендере он снимается так же.
            jobs = [{k: v for k, v in j.items() if k != "outdir"} for j in norm]
            for j in jobs:
                j["emit"] = emit          # build_combined -> to_ae_full(**kw)
            try:
                path, n = xml2ae.build_combined(jobs, os.path.join(od, "Reelsi_all.jsx"),
                                                emit=emit, cancel=stopped,
                                                progress=set_progress)
                # Результат ОДИН на весь набор, а элементы — по клипам (задание FA):
                # при успехе все они получают done с путём общего .jsx. Пишем под тем же
                # локом, где появляется result, — threading.Lock нереентерабелен, поэтому
                # помощник item_done (он сам берёт лок и КЛАДЁТ в bucket) тут не зовём:
                # результат-то один, и дублировать его в results по разу на клип нельзя.
                with LOCK:
                    JOB["results"].append(path)
                    for it in JOB.get("items", []):
                        if it.get("stage") == "wait":
                            it.update(stage="done", path=str(path), pct=None)
                emit("-> {path} ({count} комп.)", path=path, count=n)
            except xml2ae.Cancelled:
                emit("⏹ Остановлено пользователем — общий .jsx не записан "
                     "(готовые маски рото остались в кэше).")
            except Exception:
                emit("ОШИБКА:\n{tb}", tb=traceback.format_exc())
        else:
            printed = set()               # у клипов одного спикера папка одна — в лог один раз
            for i, j in enumerate(norm, 1):
                if JOB["cancel"]:
                    emit("⏹ Остановлено пользователем.")
                    break
                stem = os.path.splitext(os.path.basename(j["xml_path"]))[0]
                emit("[{i}/{n}] {stem} — сборка .jsx…", i=i, n=len(norm), stem=stem)
                set_progress(i, len(norm))
                item_set(JOB, LOCK, stem, stage="jsx")   # очередь этапов (задание FA)
                # Папка клипа — из тега спикера (задание N); глобальное поле — запасной
                # путь. Пусто у обоих = рядом со своим XML, как всегда.
                od = j.get("outdir") or outdir or os.path.dirname(j["xml_path"])
                if od not in printed:
                    if not ensure_dir(od):
                        return
                    printed.add(od)
                kw = {k: v for k, v in j.items() if k not in ("xml_path", "outdir")}
                try:
                    p, nc, ns = xml2ae.to_ae_full(j["xml_path"], os.path.join(od, stem + ".jsx"),
                                                  emit=emit, cancel=stopped, **kw)
                    # Одно место записи «готово» (задание FA): item_done и кладёт путь
                    # в results, и переводит элемент в done — вторым местом их не развести.
                    item_done(JOB, LOCK, stem, p)
                    # Полный путь, а не одно имя: «куда положил» — первый вопрос,
                    # который задают логу, и раньше ответа в нём не было.
                    emit("  -> {path} ({clips} клипов, {subs} субтитров)",
                         path=p, clips=nc, subs=ns)
                except xml2ae.Cancelled:
                    emit("  ⏹ Остановлено пользователем — файл не записан.")
                    break
                except Exception:
                    emit("  ОШИБКА:\n{tb}", tb=traceback.format_exc())
        emit("\nСборка завершена.")
    except Exception:
        tb = traceback.format_exc()
        emit("ОШИБКА (сборка прервана):\n{tb}", tb=tb)
        with LOCK:
            JOB["failed"].append({"name": "сборка", "reason": tb.strip().splitlines()[-1]})
    finally:
        job_finish()


@bp.route("/api/build_run", methods=["POST"])
def api_build_run():
    """Асинхронная сборка .jsx (набор или один файл): лог стримится в /api/status,
    результат — пути .jsx в results. Общий JOB с нарезкой (VRAM всё равно один)."""
    d = request.get_json() or {}
    try:
        try:
            norm = _norm_build_jobs(d.get("jobs") or [])
        except ValueError as e:
            msg = str(e)
            path = msg.split("Файл не найден: ", 1)[-1] if msg.startswith("Файл не найден: ") else msg
            raise SystemExit(umsg("file_not_found", msg, path=path, err=msg))
        if not norm:
            raise SystemExit(umsg("set_empty", "Набор пуст"))
        for j in norm:
            j["insdest"] = _insert_dest(d)     # куда прибирать вставки (снимается в _run_build_job)
        if not job_start(kind="build", label="Сборка .jsx"):
            raise SystemExit(umsg("busy_wait", "Уже выполняется другая задача — дождись или смотри Логи"))
        threading.Thread(target=_run_build_job,
                         args=(norm, d.get("mode") or "separate",
                               (d.get("outdir") or "").strip().strip('"')),
                         daemon=True).start()
        return jsonify(ok=True)
    except SystemExit as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/cams_load", methods=["POST"])
def api_cams_load():
    """Раскладка камер по сегментам для отдельного окна-редактора: сегменты (тайминги +
    длительность на монтажной ленте) и текущая активная камера каждого (ручная из
    project.json['assign'] если валидна, иначе авто assign_cameras). Аудио всегда cam1."""
    from core import align
    d = request.get_json() or {}
    xml = (d.get("xml") or "").strip().strip('"')
    try:
        if not os.path.isfile(xml):
            raise SystemExit(umsg("file_not_found", f"Файл не найден: {xml}",
                                  path=xml))
        try:
            p = _ensure_project(xml)
            cams = p["cams"]; N = len(cams)
            keep = [(float(s), float(e)) for s, e in p.get("keep", [])]
            segs, tl = [], 0.0
            for s, e in keep:
                dur = e - s
                segs.append({"start": round(s, 3), "end": round(e, 3),
                             "dur": round(dur, 3), "tl": round(tl, 3)})
                tl += dur
            stored = None if d.get("auto") else p.get("assign")
            if isinstance(stored, list) and len(stored) == len(keep):
                assign = [max(0, min(N - 1, int(x))) for x in stored]
            elif N > 1:
                assign = align.assign_cameras(keep, N, return_every=p.get("cam_return", 2),
                                              big_chunk_sec=6.0)
            else:
                assign = [0] * len(keep)
            return jsonify(ok=True, n=N, fps=p.get("fps", 60), total=round(tl, 1),
                           names=[os.path.basename(c) for c in cams], segs=segs,
                           assign=[int(x) for x in assign], manual=isinstance(stored, list))
        except Exception as e:
            raise SystemExit(umsg("cams_load_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except SystemExit as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/cams_save", methods=["POST"])
def api_cams_save():
    """Пересобрать XML с РУЧНОЙ раскладкой камер (список индексов на каждый сегмент) и
    сохранить её в project.json['assign'] (переживёт генерацию субтитров).

    Субтитры и жёлтые СОХРАНЯЮТСЯ (2026-07-22). Раскладка меняет только то, ЧЬЯ картинка
    видна на куске: keep-интервалы, их длины и звук (всегда камера 1) те же, значит слова
    и их тайминги не меняются ни на кадр — стирать разметку было незачем. Делаем как в
    swap_cam: вычитываем субтитр-графику и жёлтые ДО пересборки и вписываем обратно."""
    from core import xmlbuild
    from core import xml2ae
    d = request.get_json() or {}
    xml = (d.get("xml") or "").strip().strip('"')
    assign_in = d.get("assign") or []
    try:
        if not os.path.isfile(xml):
            raise SystemExit(umsg("file_not_found", f"Файл не найден: {xml}",
                                  path=xml))
        try:
            p = _ensure_project(xml)
            cams = p["cams"]; offsets = p["offsets"]; N = len(cams)
            keep = [(float(s), float(e)) for s, e in p.get("keep", [])]
            if len(assign_in) != len(keep):
                raise SystemExit(umsg("sync_mismatch",
                    f"Рассинхрон: сегментов {len(keep)}, камер {len(assign_in)} — перезагрузи окно",
                    segs=len(keep), cams=len(assign_in)))
            assign = [max(0, min(N - 1, int(x))) for x in assign_in]
            _meta, _pcams, subs, _ins = xml2ae.parse_full(xml)
            sub_words = ([{"w": w, "start": int(s), "end": int(e)} for (s, e, w) in subs]
                         if subs else None)
            yellow = xml2ae.auto_highlights(xml).get("yellow", [])
            info = xmlbuild.build(cams, keep, offsets, xml,
                                  assign=(assign if N > 1 else None),
                                  scale=p.get("scale", 50.4), sub_words=sub_words, music_path=None)
            from core import xml2ae
            xml2ae.write_srt_for(xml)
            colored = 0
            if yellow:
                colored = len(xml2ae.write_highlights(xml, yellow).get("colored", []))
            p["assign"] = assign
            atomic_json_dump(os.path.splitext(xml)[0] + ".project.json", p, indent=1)
            return jsonify(ok=True, segs=len(keep), dur=round(info.get("total_s", 0), 1),
                           subs=(len(sub_words) if sub_words else 0), yellow=colored)
        except Exception as e:
            raise SystemExit(umsg("cams_save_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except SystemExit as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/swap_cam", methods=["POST"])
def api_swap_cam():
    """Заменить файл камеры (обычно вторую) на другой и заново свести под Камеру 1:
    пересчитываем синхрон, пересобираем XML с ТЕМИ ЖЕ keep/раскладкой/субтитрами/жёлтыми.
    Аудио всегда с cam1 → слова/тайминги/вставки не трогаются, весь дальнейший флоу цел.
    Тело: {xml, cam (индекс 0-based, >=1), path (новый файл камеры)}."""
    import tempfile, shutil
    from core import sync
    from core import xmlbuild
    from core import xml2ae
    from core import align
    d = request.get_json() or {}
    xml = (d.get("xml") or "").strip().strip('"')
    k = int(d.get("cam") if d.get("cam") is not None else 1)   # 0 — валидный индекс (guard ниже отклонит)
    new_path = (d.get("path") or "").strip().strip('"')
    try:
        if not os.path.isfile(xml):
            raise SystemExit(umsg("file_not_found", f"Файл не найден: {xml}",
                                  path=xml))
        if not os.path.isfile(new_path):
            raise SystemExit(umsg("cam_file_not_found", f"Новый файл камеры не найден: {new_path}",
                                  path=new_path))
        try:
            p = _ensure_project(xml)
            cams = list(p["cams"]); offsets = list(p.get("offsets") or [0.0] * len(cams))
            N = len(cams)
            if k < 1 or k >= N:
                raise SystemExit(umsg("cam1_immutable",
                    f"Камеру 1 (звук) менять нельзя; допустимо 2..{N}" if N > 1
                    else "У клипа одна камера — менять вторую нечего", n=N))
            keep = [(float(s), float(e)) for s, e in p.get("keep", [])]
            # сохранить субтитры и жёлтые ДО пересборки (они с аудио cam1 — не меняются)
            meta, pcams, subs, _ins = xml2ae.parse_full(xml)
            sub_words =([{"w": w, "start": int(s), "end": int(e)} for (s, e, w) in subs]
                         if subs else None)
            yellow = xml2ae.auto_highlights(xml).get("yellow", [])
            # заново свести новую камеру k с cam1 (аудио)
            # temp-папка удаляется: раньше каждая замена камеры оставляла в %TEMP% два
            # несжатых WAV на весь хронометраж, и кнопка 🧹 их не видела (она чистит _tmp)
            work = tempfile.mkdtemp(prefix="swapcam_")
            try:
                wa = os.path.join(work, "a0.wav"); wb = os.path.join(work, f"a{k}.wav")
                sync.extract_audio(cams[0], wa)
                sync.extract_audio(new_path, wb)
                off, conf = sync.find_offset(wa, wb)
            finally:
                shutil.rmtree(work, ignore_errors=True)
            cams[k] = new_path
            offsets[k] = round(float(off), 3)
            # раскладка камер: ручная из проекта (если валидна) иначе авто — та же, что была
            stored = p.get("assign")
            if N > 1 and isinstance(stored, list) and len(stored) == len(keep):
                assign = [max(0, min(N - 1, int(x))) for x in stored]
            elif N > 1:
                assign = align.assign_cameras(keep, N, return_every=p.get("cam_return", 2),
                                              big_chunk_sec=6.0)
            else:
                assign = None
            xmlbuild.build(cams, keep, offsets, xml, assign=assign,
                           scale=p.get("scale", 50.4), sub_words=sub_words, music_path=None)
            colored = 0
            if yellow:                                       # вернуть жёлтые (в XML) — цвет с аудио cam1
                colored = len(xml2ae.write_highlights(xml, yellow).get("colored", []))
            p["cams"] = cams; p["offsets"] = offsets
            atomic_json_dump(os.path.splitext(xml)[0] + ".project.json", p, indent=1)
            return jsonify(ok=True, offset=round(float(off), 3), conf=round(float(conf), 2),
                           low_conf=(conf < 0.30), subs=(len(sub_words) if sub_words else 0),
                           yellow=colored, name=os.path.basename(new_path))
        except Exception as e:
            raise SystemExit(umsg("swap_cam_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except SystemExit as e:
        return jsonify(**umsg_err(e))


# ==========================================================================
# Скачивание таймлайна: XML с настоящими таймкодами + .drp (DaVinci Resolve).
# ==========================================================================

@bp.route("/api/export_xml")
def api_export_xml():
    """Скачать таймлайн: на лету проставляем настоящие таймкоды исходников.

    Файл на диске рабочий, его читают редактор, субтитры и `xml2ae` — поэтому чиним
    только копию на выходе. Нарезки, сделанные до правки `probe()` (2026-08-06),
    содержат `00;00;00;00` вместо настоящего таймкода камеры; Премьеру всё равно,
    а DaVinci Resolve позиционирует клипы по таймкоду и раскладывает нарезку со
    сдвигом в часы. С правильным таймкодом XML открывается одинаково в обоих —
    сверено с экспортом самого Премьера, отличий больше нет ни одного.
    """
    path = (request.args.get("path") or "").strip().strip('"')
    if not path or not os.path.isfile(path):
        return ("not found", 404)
    if _never_serve(path):
        return ("forbidden", 403)
    from core import xmlbuild
    try:
        text = open(path, encoding="utf-8", newline="").read()
        text, n = xmlbuild.fix_timecodes(text)
    except Exception:                       # не смогли починить — отдаём как есть
        return send_file(path, as_attachment=True, download_name=os.path.basename(path))
    name = urllib.parse.quote(os.path.basename(path))   # кириллица в имени -> RFC 5987
    return Response(text, mimetype="application/xml", headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{name}",
        "X-Reelsi-Timecodes-Fixed": str(n)})


@bp.route("/api/export_drp", methods=["POST"])
def api_export_drp():
    """Скачать таймлайн в `.drp` (DaVinci Resolve): камеры, нарезка, синхрон,
    раскладка, субтитры-графика и вставки — всё, что умеет `drp.build()`.

    Вход — тот же XML, что у `/api/export_xml` (+ список вставок с фронта; в
    XML их нет, они живут только в состоянии UI). Данные сборки берём из сайдкара
    `<stem>.project.json`, как при пересборке XML: кто не нарезан там — вставки
    без media в сборку не уйдут.
    """
    d = request.get_json() or {}
    xml = (d.get("xml") or "").strip().strip('"')
    try:
        if not os.path.isfile(xml):
            raise SystemExit(umsg("file_not_found", f"Файл не найден: {xml}",
                                  path=xml))
        if _never_serve(xml):
            return ("forbidden", 403)
        try:
            import tempfile
            from core import drp
            p = _ensure_project(xml)
            cams = p.get("cams") or []
            if not cams:
                raise SystemExit(umsg("no_cams_sidebar", "В сайдбаре нет камер — нарезку не собрать"))
            fps = int(p.get("fps") or 60)
            keep = [(float(s), float(e)) for s, e in (p.get("keep") or [])]
            offsets = [float(x) for x in (p.get("offsets") or [0.0] * len(cams))]
            if len(cams) != len(offsets):
                offsets = [0.0] * len(cams)
            stored = p.get("assign")
            assign = ([max(0, min(len(cams) - 1, int(x))) for x in stored]
                      if len(cams) > 1 and isinstance(stored, list)
                      and len(stored) == len(keep) else None)
            if assign is None and len(cams) > 1 and keep:
                from core import align
                assign = align.assign_cameras(keep, len(cams),
                                              return_every=p.get("cam_return", 2),
                                              big_chunk_sec=6.0)

            from core import xml2ae
            _m, _c, subs, _i = xml2ae.parse_full(xml)
            yellow = _sidecar_yellow(xml)
            inserts = []
            for x in (d.get("inserts") or []):
                media = (x.get("media") or "").strip().strip('"')
                if not media or not os.path.isfile(media):
                    continue
                st = float(x.get("start_sec") or 0)
                dur = float(x.get("duration_sec") or 2)
                inserts.append(dict(type="photo" if x.get("type") == "photo" else "video",
                                    media=media, start=int(round(st * fps)),
                                    end=int(round((st + dur) * fps))))

            stem = os.path.splitext(os.path.basename(xml))[0]
            fd, tmp = tempfile.mkstemp(suffix=".drp"); os.close(fd)
            try:
                drp.build(tmp, cams, keep, offsets, assign=assign, sub_words=subs,
                          yellow=yellow, inserts=inserts, name=stem, fps=fps)
                data = open(tmp, "rb").read()
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            if not data:
                raise SystemExit(umsg("drp_empty", "Сборка .drp вернула пустой файл"))
            fname = urllib.parse.quote(stem + ".drp")
            return Response(data, mimetype="application/octet-stream", headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{fname}"})
        except Exception as e:
            raise SystemExit(umsg("export_drp_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except SystemExit as e:
        return jsonify(**umsg_err(e))


# ==========================================================================
# План сцены (задание C): вся арифметика сборки без .jsx и рото-масок.
# ==========================================================================

@bp.route("/api/scene", methods=["POST"])
def api_scene():
    """План сцены: тайминги вставок/зума, готовые ключи анимаций (ins.anim),
    субтитры, интро, цензор. Быстрый роут без GPU — фронт и предпросмотр зовут
    на лету; это тот же расчёт, что уходит в .jsx, только без записи.
    Вход — те же поля, что у фоновой сборки (/api/build_run), напр. {"xml": ...,
    "inserts": [...]}; «roto» принято, но маски не строятся (это этап to_ae_full)."""
    d = request.get_json() or {}
    xml = (d.get("xml") or "").strip().strip('"')
    try:
        if not os.path.isfile(xml):
            raise SystemExit(umsg("file_not_found", f"Файл не найден: {xml}",
                                  path=xml))
        try:
            jobs = _norm_build_jobs([d])
            from core import xml2ae
            job = jobs[0]
            job.pop("outdir", None)          # папка .jsx — только для сборки, в план не идёт
            plan = xml2ae.scene_plan(**job)
            plan.pop("_ae", None)          # служебное для сборки .jsx — не контракт плана
            return jsonify(ok=True, plan=plan)
        except Exception as e:
            raise SystemExit(umsg("scene_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except SystemExit as e:
        return jsonify(**umsg_err(e))
