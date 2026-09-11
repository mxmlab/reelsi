# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Джобы нарезки: запуск, отмена, статус.

Здесь живёт CURPROC — хэндл подпроцесса omni_cut. Он тут, а не в _core, намеренно:
CURPROC ПЕРЕПРИСВАИВАЕТСЯ (global), а `from ._core import CURPROC` связал бы имя один
раз, и «Стоп» убивал бы None вместо процесса. Читают его только run_omnicut_job и
_kill_curproc — оба здесь.
"""
import os, threading, argparse, traceback, subprocess, shutil
from flask import request, jsonify
import reelsi
from core import cutstages
from ._core import (DEFAULT_BASE, JOB, LOCK, bp, emit, item_done, item_fail,
                    item_set, items_init, job_finish, job_start, set_progress, umsg_err)
from core.umsg import umsg
from core.app_meta import child_env, module_cmd
from .videogen import VJOB, VLOCK
CURPROC = None          # текущий subprocess задачи (omni_cut) — чтобы «Стоп» мог убить дерево
CURWORK = []            # рабочие каталоги задачи (omnicut_*, gigaamcut_*): omni_cut печатает
                        # их маркером WORK_DIR= в stdout, а _kill_curproc удаляет — иначе
                        # WAV камер (сотни МБ) переживают «Стоп» в %TEMP%


def build_pairs(camdirs, names_list):
    """Очередь (ИМЕНА файлов) + папки камер -> полные пути, с проверкой, что файлы есть.

    Очередь хранит только имена, папка приезжает отдельно — значит пара «очередь от
    одного спикера, папка от другого» склеивается в путь, которого нет, и это молча
    доезжало до ffmpeg. Тот падал кодом -2 (в отчёте — «код 1: CalledProcessError …
    4294967294»), и по логу нельзя было понять, что дело в папке (жалоба 2026-08-20:
    очередь Адилета, папка «камера1»). Проверяем ЗДЕСЬ и говорим прямым текстом."""
    if any(len(names) > len(camdirs) for names in names_list):
        raise SystemExit(umsg("queue_desync",
            "Рассинхрон очереди: файлов в паре больше, чем папок камер"))
    pairs = [[os.path.join(camdirs[k], names[k]) for k in range(len(names))]
             for names in names_list]
    if not pairs:
        raise SystemExit(umsg("queue_empty", "Очередь пуста"))
    missing = [p for cams in pairs for p in cams if p and not os.path.isfile(p)]
    if missing:
        lst = "; ".join(missing[:3]) + (f" (и ещё {len(missing) - 3})" if len(missing) > 3 else "")
        raise SystemExit(umsg("queue_file_missing",
            f"Файла из очереди нет в папке камеры: {lst}. "
            "Очередь хранит только имена — проверьте папки камер.", path=lst))
    return pairs


def _kill_curproc():
    """Убить текущий subprocess вместе с детьми (omni_cut порождает omni_asr — им VRAM),
    и убрать его рабочие каталоги (см. CURWORK)."""
    with LOCK:
        p = CURPROC
        works = list(CURWORK)
    if p and p.poll() is None:
        for _ in range(2):                   # taskkill /T бывает таймаутит — вторая попытка
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                               capture_output=True, timeout=15)
                if p.poll() is not None:
                    break
            except Exception:
                pass
        else:
            try:
                p.kill()                     # последний шанс: хотя бы родителя
            except Exception:
                pass
    for w in works:                          # temp-каталоги задачи (WAV камер и вырезок)
        shutil.rmtree(w, ignore_errors=True)


def _mark_stopped_waits():
    """«Стоп» по JOB["cancel"]: файлам, до которых работа не дошла (stage="wait"),
    проставить stage="stopped", чтобы очередь показывала их «остановлено», а не «в очереди»."""
    with LOCK:
        for it in JOB.get("items", []):
            if it.get("stage") == "wait":
                it["stage"] = "stopped"


def run_job(base, outdir, pairs, opts):
    try:
        os.makedirs(outdir, exist_ok=True)
        args = argparse.Namespace(
            no_subs=not opts["subs"], no_dedup=not opts["dedup"],
            no_srt=not opts["srt"], ae=opts["ae"], keep=opts["keep"],
            model=opts["model"], scale=float(opts["scale"]),
            vad_thresh=float(opts["vad_thresh"]),
            min_silence=float(opts["min_silence"]), pad=float(opts["pad"]),
            no_cut=bool(opts.get("no_cut")),
            aggressive=bool(opts.get("aggressive")),
            restarts=bool(opts.get("restarts")),        # ИИ-нарезка: детектор рестартов вместо точных повторов
            forced_align=bool(opts.get("forced_align")),  # точные тайминги (wav2vec2) для резов и субтитров
            cam_return=int(opts.get("cam_return") or 2),
            big_chunk=float(opts.get("big_chunk") or 6.0))
        model = None
        made = []                                   # (out_xml) успешно собранные — для фазы 2
        # Очередь этапов по стемам набора (задание FA): заводим ДО начала цикла, все — wait.
        items_init(JOB, LOCK, [os.path.splitext(os.path.basename(cams[0]))[0] for cams in pairs])
        # ФАЗА 1 — нарезка (Whisper в VRAM). Сначала освобождаем VRAM от LM Studio.
        if opts["subs"] or opts["dedup"]:
            try:
                from core import aicut
                aicut.unload_ours(emit=emit)        # чтобы Whisper влез (16 ГБ впритык)
                aicut.warn_foreign_models(emit=emit)  # предупредить, если висит чужая модель
            except Exception:
                pass
            emit("Гружу модель Whisper {model} (один раз)...", model=opts["model"])
            from core import transcribe
            model = transcribe.get_model(opts["model"])
        for i, cams in enumerate(pairs, 1):
            if JOB["cancel"]:
                emit("⏹ Остановлено пользователем.")
                break
            stem = os.path.splitext(os.path.basename(cams[0]))[0]
            out_xml = os.path.join(outdir, f"{i:02d}_{stem}.xml")
            emit("[{i}/{n}] {stem}", i=i, n=len(pairs), stem=stem)
            set_progress(i, len(pairs))
            item_set(JOB, LOCK, stem, stage="cut")
            try:
                reelsi.process_pair(cams, out_xml, args, model=model, emit=emit)
                made.append(out_xml)
                # Одно место записи «готово» (задание FA): item_done и кладёт путь в
                # results, и переводит элемент в done — вторым местом их не развести.
                item_done(JOB, LOCK, stem, os.path.basename(out_xml))
            except Exception:
                tb = traceback.format_exc()
                emit("  ОШИБКА:\n{tb}", tb=tb)
                item_fail(JOB, LOCK, stem, tb.strip().splitlines()[-1])
        # выгружаем Whisper ДО обращения к LM Studio (иначе OOM — VRAM впритык).
        # wav2vec2 forced-align крутится в отдельном процессе и освобождает VRAM сам.
        try:
            from core import transcribe
            if transcribe.release_model():
                emit("Модель Whisper выгружена, видеопамять освобождена.")
        except Exception:
            pass
        # ФАЗА 2 — ИИ-шаги на LM Studio (жёлтые). Whisper уже выгружен.
        if opts.get("ai_yellow") and opts["subs"] and made and not JOB["cancel"]:
            from core import aicut
            for out_xml in made:
                if JOB["cancel"]:
                    emit("⏹ Остановлено пользователем.")
                    break
                try:
                    res = aicut.cmd_yellow(out_xml, emit=emit)
                    emit("  🤖 ИИ жёлтые: {count}/{total} -> {path}",
                         count=len(res["yellow"]), total=res["total"],
                         path=os.path.basename(res["path"]))
                except SystemExit as e:
                    emit("  🤖 ИИ жёлтые пропущены: {reason}", reason=str(e))
                except Exception:
                    emit("  🤖 ИИ жёлтые — ОШИБКА:\n{tb}", tb=traceback.format_exc())
            try:
                aicut.unload_ours(emit=emit)         # освободить VRAM после ИИ-шагов
            except Exception:
                pass
        if JOB["cancel"]:
            _mark_stopped_waits()          # «Стоп»: до чего не дошло — «остановлено» (задание FA)
        fails = JOB["failed"]
        if fails:
            emit("\n⚠ Не собрались ({n}):", n=len(fails))
            for f in fails:
                emit("  ✗ {name}: {reason}", name=f["name"], reason=f["reason"])
        emit("\nГотово. Файлы в: {outdir}", outdir=outdir)
    except Exception:
        # падение ВНЕ пер-клипового try (makedirs на отвалившемся диске, OOM при
        # загрузке Whisper, битый opts) иначе убивало поток молча: finally честно
        # ставил done=True, и юзер видел «Готово (файлов нет)» с пустым логом.
        tb = traceback.format_exc()
        emit("ОШИБКА (нарезка прервана):\n{tb}", tb=tb)
        with LOCK:
            JOB["failed"].append({"name": "нарезка", "reason": tb.strip().splitlines()[-1]})
    finally:
        try:
            from core import transcribe
            if transcribe.release_model():
                emit("Модель Whisper выгружена, видеопамять освобождена.")
        except Exception:
            pass
        job_finish()


def run_omnicut_job(outdir, pairs, model=None, draft=True, selfcheck=False, review=False, mode="gigaam", selfcheck_model="whisper:large-v3", speaker=None, dedupe=None, stages=None):
    """ИИ-нарезка через omni_cut.py (Omni + LLM + SSM) — как subprocess, стримим лог.
    stages — словарь ступеней нарезки (задание GE); если задан, draft и dedupe берутся из него.
    draft — параметр сохранён для совместимости сигнатуры, в теле ни на что не влияет (черновик — производная Omni-ревью);
    review — Omni-ревью черновика (эксперимент, ВЫКЛ по умолчанию).
    mode — общий «Режим нарезки» (флоу): 'gigaam' (основной, цельный файл
    GigaAM + LLM решает рез) или 'old' (Старый/Qwen/VAD). НЕ выбирает движок слуха —
    движок всегда резолвится из active_omni (aicut.omni_local_engine()).
    selfcheck/selfcheck_model — самопроверка стыков; ВЫКЛ по умолчанию с 2026-08-11.
    В режиме gigaam (дефолт) omni_cut возвращается ДО блока селфчека, так что флаг
    там не делал ничего, а галочка в UI обещала работу, которой не было. Путь жив
    только у режима «Старый» и снаружи (CLI без --no-selfcheck); включить обратно —
    задача из ROADMAP, а не забытый флаг.
    speaker — профиль спикера (speakers/*.json): свои пороги нарезки под его
    студию и говор; применяется в режиме gigaam.
    dedupe — чистка дублей кодом (задание CA): None = не передавать флаг (работает профиль
    спикера); явный bool перекрывает профиль спикера (см. omni_cut --dedupe/--no-dedupe)."""
    global CURPROC, CURWORK       # CURWORK тоже ПЕРЕПРИСВАИВАЕТСЯ ниже: без global он
                                  # становился локальным на всю функцию, и WORK_DIR=
                                  # копился в списке, которого _kill_curproc не видит —
                                  # «Стоп» молча оставлял WAV камер в %TEMP%
    try:
        raw_stages = dict(stages) if stages is not None else {}
        if stages is None:
            raw_stages["draft"] = draft
            if dedupe is not None:
                raw_stages["dedupe"] = dedupe
        # черновик — производная Omni-ревью, отдельной галки в интерфейсе нет
        raw_stages["draft"] = bool(review)

        norm_stages, _ = cutstages.normalize(raw_stages)
        os.makedirs(outdir, exist_ok=True)
        try:
            from core import draftrender
            draftrender.clean_tmp(outdir, emit=emit)   # авто-очистка _tmp перед новой нарезкой
        except Exception:
            pass
        # Очередь этапов по стемам набора (задание FA): заводим ДО начала цикла, все — wait.
        items_init(JOB, LOCK, [os.path.splitext(os.path.basename(cams[0]))[0] for cams in pairs])
        for i, cams in enumerate(pairs, 1):
            if JOB["cancel"]:
                break
            stem = os.path.splitext(os.path.basename(cams[0]))[0]
            out_xml = os.path.join(outdir, f"{i:02d}_{stem}.xml")
            emit("[{i}/{n}] {stem} — ИИ-нарезка (Omni + LLM + SSM), ~5–10 мин",
                 i=i, n=len(pairs), stem=stem)
            set_progress(i, len(pairs))
            item_set(JOB, LOCK, stem, stage="cut")
            cmd = module_cmd("omni_cut", "--ssm", "--out", out_xml)
            if not norm_stages.get("draft", False):
                cmd += ["--no-draft"]
            if not selfcheck:
                cmd += ["--no-selfcheck"]
            if review:
                cmd += ["--omni-review"]
            if model:
                cmd += ["--model", model]
            # режим нарезки (mode) выбирает флоу: «old» — прежний (Qwen/VAD),
            # «gigaam» — цельный файл GigaAM + LLM решает рез.
            # движок, который СЛУШАЕТ звук, резолвится внутри omni_cut.py из active_omni.
            cmd += ["--mode", mode]
            # движок самопроверки стыков (Whisper / GigaAM / CTC другого языка)
            cmd += ["--selfcheck-model", selfcheck_model]
            if speaker:
                cmd += ["--speaker", speaker]
            # чистка дублей (задание CA): явный флаг только когда задан явно (дефект 2),
            # чтобы без флага действовал профиль спикера
            explicit_dedupe = raw_stages.get("dedupe")
            if explicit_dedupe is not None:
                cmd += ["--dedupe"] if bool(explicit_dedupe) else ["--no-dedupe"]
            # флаги пропуска ступеней нарезки (задание GE)
            if not norm_stages.get("sense", True):
                cmd += ["--no-sense"]
            if not norm_stages.get("refine", True):
                cmd += ["--no-refine"]
            if not norm_stages.get("breath", True):
                cmd += ["--no-breath"]
            if norm_stages.get("pauses") == "off":
                cmd += ["--no-pauses"]
            for c in cams:                       # аудио всегда с первой; assign_cameras сводит 2..N
                if c:
                    cmd += ["--cam", c]
            try:
                with LOCK:
                    CURWORK = []                # маркеры нового процесса ещё не печатались
                p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding="utf-8", errors="replace", bufsize=1,
                                     env=child_env())
                with LOCK:
                    CURPROC = p
                if JOB["cancel"]:            # «Стоп» успел проскочить между Popen и CURPROC=p
                    _kill_curproc()
                skip = ("Warning", "cudnn", "it/s", "casper", "Deprecat", "rope_scaling",
                        "Token2Wav", "weights_only", "SystemPrompt", "FutureWarning",
                        "torch.load", "Some weights", "newly init", "suggest you", "eager attention")
                tail = []                        # хвост сырого вывода — причина падения для отчёта
                key_err = None                   # содержательная строка ошибки (не просто хвост)
                ERRSIG = ("ImportError", "ModuleNotFoundError", "requires the following",
                          "No module named", "pip install", "CUDA out of memory",
                          "SystemExit", "Error:", "не принимает аудио", "баланс")
                for line in p.stdout:
                    line = line.rstrip()
                    if not line:
                        continue
                    if line.startswith("WORK_DIR="):
                        # omni_cut печатает свой temp-каталог: «Стоп» (taskkill /F,
                        # без атекситов) оставлял WAV камер в %TEMP% — чистим по
                        # этому пути в _kill_curproc. Строка служебная, в лог не идёт.
                        with LOCK:
                            CURWORK.append(line[len("WORK_DIR="):].strip())
                        continue
                    tail.append(line)
                    del tail[:-5]
                    if any(s in line for s in ERRSIG):
                        key_err = line.strip()   # последняя осмысленная строка ошибки
                    if not any(x in line for x in skip):
                        emit("  " + line)
                # stdout отдал EOF — процесс уже мёртв (или убит «Стопом»), так что
                # ждать тут нечего; таймаут — страховка от призрачных хендлов
                # (внук, унаследовавший пайп), из-за которых wait() умеет висеть.
                p.wait(timeout=30)
                if JOB["cancel"]:
                    emit("  ⏹ клип прерван")
                elif os.path.isfile(out_xml):
                    # Одно место записи «готово» (задание FA): item_done и кладёт путь
                    # в results, и переводит элемент в done — вторым местом их не развести.
                    item_done(JOB, LOCK, stem, os.path.basename(out_xml))
                else:
                    reason = key_err or (tail[-1] if tail else "процесс не дал вывода")
                    if p.returncode:
                        reason = f"код {p.returncode}: {reason}"
                    emit("  ⚠ XML не создан — {reason}", reason=reason)
                    item_fail(JOB, LOCK, stem, reason)
            except Exception:
                tb = traceback.format_exc()
                emit("  ОШИБКА:\n{tb}", tb=tb)
                item_fail(JOB, LOCK, stem, tb.strip().splitlines()[-1])
            finally:
                with LOCK:
                    CURPROC = None
        if JOB["cancel"]:
            _mark_stopped_waits()          # «Стоп»: до чего не дошло — «остановлено» (задание FA)
        fails = JOB["failed"]
        if fails:
            emit("\n⚠ Не собрались ({n}):", n=len(fails))
            for f in fails:
                emit("  ✗ {name}: {reason}", name=f["name"], reason=f["reason"])
        if JOB["cancel"]:
            emit("\n⏹ Остановлено. Что успело собраться — в списке.")
        else:
            emit("\nГотово. Файлы в: {outdir}", outdir=outdir)
    except Exception:
        # см. run_job: без except падение вне цикла по клипам выглядело как «Готово».
        tb = traceback.format_exc()
        emit("ОШИБКА (ИИ-нарезка прервана):\n{tb}", tb=tb)
        with LOCK:
            JOB["failed"].append({"name": "ИИ-нарезка", "reason": tb.strip().splitlines()[-1]})
    finally:
        with LOCK:
            CURPROC = None
        job_finish()


@bp.route("/api/omnicut_run", methods=["POST"])
def api_omnicut_run():
    d = request.get_json() or {}
    outdir = (d.get("outdir") or "").strip().strip('"')
    try:
        if not outdir:
            raise SystemExit(umsg("no_result_dir", "Не задана папка результата"))
        pairs = build_pairs(d.get("camdirs") or [], d.get("pairs") or [])
        if not job_start(kind="cut", label="ИИ-нарезка"):
            raise SystemExit(umsg("busy", "Уже выполняется"))
        raw_stages = d.get("stages")
        if raw_stages is None:
            raw_stages = {}
            if "draft" in d:
                raw_stages["draft"] = d.get("draft", False)
            if "dedupe" in d and d.get("dedupe") is not None:
                raw_stages["dedupe"] = d.get("dedupe")
        threading.Thread(target=run_omnicut_job,
                         args=(outdir, pairs, d.get("model"),
                               raw_stages.get("draft", False), d.get("selfcheck", False),
                               bool(d.get("review")), "gigaam",
                               d.get("selfcheck_model") or "whisper:large-v3",
                               (d.get("speaker") or "").strip() or None,
                               raw_stages.get("dedupe"),
                               raw_stages),
                         daemon=True).start()
        return jsonify(ok=True)
    except SystemExit as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/draft_render", methods=["POST"])
def api_draft_render():
    """Черновой .draft.mp4 по готовому XML (ручной перерендер после правок в редакторе).
    Фоновый JOB (рендер ~1-2 мин), лог в /api/status; NVENC с фолбэком на CPU."""
    d = request.get_json() or {}
    xml_path = (d.get("xml") or "").strip().strip('"')
    try:
        if not os.path.isfile(xml_path):
            raise SystemExit(umsg("file_not_found", f"Файл не найден: {xml_path}",
                                  path=xml_path))
        if not job_start(kind="draft", label="Черновик mp4"):
            raise SystemExit(umsg("busy_other", "Уже выполняется другая задача"))
    except SystemExit as e:
        return jsonify(**umsg_err(e))

    def _run():
        try:
            from core import draftrender
            emit("[1/1] Черновик mp4: {name}", name=os.path.basename(xml_path))
            set_progress(1, 1)
            p = draftrender.render_draft(xml_path, emit=emit, force_cpu=bool(d.get("cpu")),
                                         cancel=lambda: JOB["cancel"])
            with LOCK:
                JOB["results"].append(p)
        except draftrender.RenderCancelled:
            emit("⏹ Черновик прерван")
        except Exception:
            emit("ОШИБКА:\n{tb}", tb=traceback.format_exc())
        finally:
            job_finish()
    threading.Thread(target=_run, daemon=True).start()
    return jsonify(ok=True)


@bp.route("/api/clean_tmp", methods=["POST"])
def api_clean_tmp():
    """Очистить временные файлы: <outdir>/_tmp (черновики-скрипты, ass, склейки
    self-check) + черновые .draft.mp4; опционально кэш рото (roto=true — это КЭШ,
    удаление = пересчёт RVM) и превью-прокси камер (proxies=true — тоже КЭШ, удаление =
    пересборка по десятку секунд на файл камеры при следующем открытии предпросмотра)."""
    d = request.get_json() or {}
    outdir = (d.get("outdir") or "").strip().strip('"')
    try:
        if not os.path.isdir(outdir):
            raise SystemExit(umsg("no_folder", f"Нет папки: {outdir}", path=outdir))
        try:
            from core import draftrender
            import shutil, glob
            freed = 0
            freed += draftrender.clean_tmp(outdir, emit=lambda *a: None,
                                           proxies=bool(d.get("proxies")))
            if d.get("drafts"):
                for f in glob.glob(os.path.join(outdir, "*.draft.mp4")):
                    try:
                        freed += os.path.getsize(f)
                        os.remove(f)
                    except OSError:
                        pass
            if d.get("roto"):
                rd = os.path.join(os.path.dirname(outdir.rstrip("\\/")) or outdir, "roto", "_cache")
                for base in {rd, os.path.join(outdir, "roto", "_cache")}:
                    if os.path.isdir(base):
                        for root, _dirs, files in os.walk(base):
                            for f in files:
                                try:
                                    freed += os.path.getsize(os.path.join(root, f))
                                except OSError:
                                    pass
                        shutil.rmtree(base, ignore_errors=True)
            return jsonify(ok=True, freed_mb=round(freed / 1e6, 1))
        except Exception as e:
            raise SystemExit(umsg("clean_tmp_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except SystemExit as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/tmp_info")
def api_tmp_info():
    """Что и сколько лежит в <outdir>/_tmp — чтобы кнопка очистки спрашивала по делу,
    а не «удалить временные файлы?» вслепую."""
    outdir = (request.args.get("outdir") or "").strip().strip('"')
    try:
        if not os.path.isdir(outdir):
            raise SystemExit(umsg("no_folder", f"Нет папки: {outdir}", path=outdir))
        try:
            from core import draftrender
            t = os.path.join(outdir, "_tmp")
            total = 0
            for root, _dirs, files in os.walk(t):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
            prox = draftrender.proxy_size(outdir)
            return jsonify(ok=True, total_mb=round(total / 1e6, 1),
                           proxy_mb=round(prox / 1e6, 1),
                           other_mb=round((total - prox) / 1e6, 1))
        except Exception as e:
            raise SystemExit(umsg("tmp_info_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except SystemExit as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/cancel", methods=["POST"])
def api_cancel():
    """Остановить текущую задачу: subprocess (ИИ-нарезка) убивается сразу с детьми
    (omni_asr держит VRAM), внутрипроцессные шаги (Whisper/сборка) — после текущего клипа.
    Выполняется ВСЕГДА (даже без JOB["running"]): массовая разметка — это цепочка
    обычных POST-ов без джоба, ей тоже нужны aicut.CANCEL и выгрузка LM Studio."""
    with LOCK:
        JOB["cancel"] = True
    if JOB["running"]:
        emit("⏹ Останавливаю… (текущий шаг может дорабатывать несколько секунд)")
    # Рендер в AE живёт в своём RJOB и своим subprocess'ом — «Стоп» обязан убить
    # aerender с деревом (задание BD), иначе процесс досчитает минуты в фоне.
    from .render import render_kill
    render_kill()
    # Генерация видео живёт в своём VJOB, и раньше общая кнопка её не касалась: облачный
    # запрос молотил минуты и деньги, а остановить его было нечем вообще (F5 рвал только
    # опрос у клиента). Флаг ставим всегда — поток проверяет его между опросами статуса.
    with VLOCK:
        VJOB["cancel"] = True
    _kill_curproc()
    try:
        from core import aicut
        ep = aicut.cancel_call()   # CANCEL рвёт ретраи _ask_json (иначе он перезагрузит
                                   # выгруженную модель) + смена epoch убивает старый поток
        def _unload():             # не выгружать, если поверх уже стартовал новый ИИ-вызов
            if aicut.is_current(ep):
                aicut.unload_ours()
        threading.Thread(target=_unload, daemon=True).start()  # освободить VRAM LM Studio
    except Exception:
        pass
    return jsonify(ok=True)


@bp.route("/api/run", methods=["POST"])
def api_run():
    d = request.get_json(silent=True) or {}
    base = d.get("base") or DEFAULT_BASE
    try:
        if not (d.get("outdir") or "").strip():
            raise SystemExit(umsg("no_result_dir", "Не задана папка результата"))
        camdirs = d.get("camdirs") or []
        if not camdirs:
            camdirs = [p for p in reelsi.find_cam_dirs(base)]
        pairs = build_pairs(camdirs, d.get("pairs") or [])
        # Если пришли stages (задание GF), строим opts на сервере из единого контракта cutstages.
        # Старое поле opts остаётся как fallback для обратной совместимости.
        raw_stages = d.get("stages")
        if raw_stages is not None and isinstance(raw_stages, dict):
            thresholds = d.get("thresholds")
            opts = cutstages.to_reelsi_opts(raw_stages, thresholds=thresholds)
        else:
            opts = d.get("opts")
            if not isinstance(opts, dict):
                raise SystemExit(umsg("no_opts", "Нет параметров нарезки (opts)"))
        if not job_start(kind="cut", label="Классическая нарезка"):
            raise SystemExit(umsg("busy", "Уже выполняется"))
        threading.Thread(target=run_job, args=(base, d["outdir"], pairs, opts),
                         daemon=True).start()
        return jsonify(ok=True)
    except SystemExit as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/status")
def api_status():
    """?since=N — отдать только строки лога после абсолютного индекса N (иначе весь лог).
    log_total — абсолютный счётчик строк (включая срезанные кэпом): клиент шлёт его
    обратно как since; log_total < since у клиента = новый джоб, надо сбросить кэш."""
    since = request.args.get("since", type=int)
    with LOCK:
        base = JOB["log_base"]
        total = base + len(JOB["log"])
        if since is None:
            log = list(JOB["log"])
        else:
            log = list(JOB["log"][max(0, since - base):]) if since < total else []
        return jsonify(running=JOB["running"], done=JOB["done"], log=log, log_total=total,
                       results=JOB["results"], failed=JOB["failed"],
                       kind=JOB["kind"], label=JOB["label"], progress=JOB["progress"],
                       insmoved=JOB.get("insmoved") or {}, items=JOB.get("items") or [])


@bp.route("/api/cutstages")
def api_cutstages():
    """Отдать единый список ступеней нарезки и дефолты (задание GG).

    Интерфейс рисует ступени по ответу сервера, своей копии списка не держит.
    """
    return jsonify(
        stages=cutstages.STAGES,
        defaults=cutstages.DEFAULTS,
        threshold_defaults=cutstages.DEFAULT_THRESHOLDS,
        thresholds=cutstages.DEFAULT_THRESHOLDS,
    )
