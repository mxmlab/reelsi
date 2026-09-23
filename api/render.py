# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Безголовый рендер в After Effects (шаг 3).

Свой джоб, как PXJOB у превью-прокси: рендер идёт минуты, и нарезка/сборка .jsx
не должны им блокироваться. Перед запуском AE — предполётная проверка verify_jsx:
пропавший файл в -noui пишется в $.writeln и уходит в никуда, а на выходе будет
.mov без куска камеры, о котором никто не узнает. Проверка ловит это за секунду
без всякого AE — есть пропажи, рендер не запускаем вовсе.

Движок (поиск AE, разбор вывода `aerender`, ETA, статистика фаз, проверки результата)
живёт отдельно — `core/aerender.py`: у него нет состояния задания, и он
доступен CLI и `doctor.py` без импорта Flask-слоя. Здесь остаётся то, у чего это
состояние есть: RJOB/RLOCK/RPROC, запуск процессов и роуты.
"""
import os, re, threading, queue, subprocess, time as _time
from flask import request, jsonify
from core.aerender import (AE_FAST_EXIT_SEC, AE_STALL_KILL_SEC, AE_STALL_WARN_SEC, AE_STALLED,
                           DEFAULT_AEP_SEC, DEFAULT_RENDER_SEC, ETA_WINDOW, PHASE_AEP_END,
                           PHASE_JSX_END, TC_DUR, TC_END, TC_START, aep_call_path,
                           aep_updated_by_run, ae_running, calc_phase_bounds, comp_frames,
                           comp_to_stem, default_render_dir, eta_secs, find_ae,
                           finished_comp_name, get_baseline_phase_durations, jsx_call_path,
                           parse_frame_rate, parse_output_to, parse_progress, parse_timecode,
                           predict_aep_times, remove_stale_aelog, rendered_ok, save_render_stats)
from ._core import (JOB, LOCK, bp, _cross_lock_acquire, _cross_lock_release,
                    item_done, item_fail, item_set, items_init, jstr, journal_bind,
                    journal_finish, journal_interrupted, kill_tree,
                    log_entry, task_popen_kwargs, umsg_err, sysexit_text)
from core.umsg import ReelsiError, umsg
from core.applog import get_logger

log = get_logger("reelsi.render")

RJOB = {"running": False, "done": False, "log": [], "pct": None, "cur": "",
        "ae": "", "out_dir": "", "result": [], "failed": [], "cancel": False,
        "eta": None, "eta_phase": None, "eta_total": None, "eta_preliminary": False,
        "stage_label": None, "stage_done": 0, "stage_total": 0}
RLOCK = threading.Lock()
RPROC = None          # текущий subprocess (AfterFX или aerender) — «Стоп» убивает дерево


def remit(line, **vars):
    """Строка в лог рендера (свой лог, не общий JOB)."""
    with RLOCK:
        entry = log_entry(line, vars)
        RJOB["log"].append(entry)
        if len(RJOB["log"]) > 2000:
            del RJOB["log"][:len(RJOB["log"]) - 2000]


def _kill_proc(p):
    """taskkill /T /F — дерево: aerender сам по себе не всегда держит детей,
    но после убийства не должен остаться ни один процесс рендера.

    Имя оставлено ради тестов и `webui._cleanup_on_exit`: реализация теперь одна
    на весь пакет — `_core.kill_tree`."""
    kill_tree(p)


def render_kill():
    """«Стоп» из интерфейса (/api/cancel зовёт): флаг джобу + реально убить
    текущий subprocess (AfterFX или aerender) с деревом."""
    with RLOCK:
        RJOB["cancel"] = True
        p = RPROC
    if p and p.poll() is None:
        _kill_proc(p)


def _pump_stdout(p):
    """Фоновый поток чтения stdout процесса в queue.Queue.
    Предотвращает переполнение OS-буфера трубы при обильном выводе процесса (напр. AfterFX -noui)
    и позволяет сторожу опрашивать активность с таймаутом без зависания в readline()."""
    q = queue.Queue()

    def _pump():
        try:
            for line in p.stdout:
                q.put(line)
        except ReelsiError: raise
        except Exception:
            pass  # поток вывода оборвался (процесс умер) — EOF отдаём в finally
        finally:
            q.put(None)          # EOF stdout

    threading.Thread(target=_pump, daemon=True).start()
    return q


def _run_proc(cmd, total_frames=None, item_name=None, pct_base=0.0, pct_span=1.0):
    """Запустить процесс (aerender), стримить вывод в лог рендера со сторожем простоя.
    Возвращает код выхода или AE_STALLED. item_name — элемент очереди, которому
    дублируется доля рендера (aerender — единственный этап с честным процентом).
    pct_base и pct_span задают долю шкалы (например 0.35..1.0 на фазе рендера).
    Сторож простоя aerender: молчание AE_STALL_WARN_SEC — предупреждение,
    AE_STALL_KILL_SEC — снятие. Константы те же, что у AfterFX (5 и 20 мин): рендер даже
    тяжёлых кадров даёт строки прогресса чаще, а молчание означает зависание или окно ошибки."""
    global RPROC
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                             **task_popen_kwargs())
    except FileNotFoundError as e:
        remit("не найден исполняемый файл: {err}", err=str(e))
        return -1
    with RLOCK:
        RPROC = p
    if RJOB["cancel"]:
        _kill_proc(p)
    q = _pump_stdout(p)
    last_activity = _time.time()
    warned = False
    stalled = False
    hdr_dur, hdr_fps, hdr_start, hdr_end = None, None, None, None

    def _handle(raw):
        """Разбор одной строки aerender: лог, заголовок AE, прогресс,
        Output To. Обработчик ОДИН и на живой цикл, и на добирание хвоста после выхода
        процесса: во второй копии (только remit) у конца прогона терялся разбор
        «Finished composition» и «Total Time Elapsed», а по ним ставится 100% шкалы."""
        nonlocal total_frames, last_activity, hdr_dur, hdr_fps, hdr_start, hdr_end
        line = raw.rstrip("\r\n")
        if not line.strip():
            return
        last_activity = _time.time()
        remit(line)

        # Заголовок AE: кадры композиции и имя из Output To:
        m_dur = TC_DUR.search(line)
        if m_dur:
            hdr_dur = m_dur.group(1)
        fps_val = parse_frame_rate(line)
        if fps_val is not None:
            hdr_fps = fps_val
        m_start = TC_START.search(line)
        if m_start:
            hdr_start = m_start.group(1)
        m_end = TC_END.search(line)
        if m_end:
            hdr_end = m_end.group(1)

        if hdr_dur and hdr_fps:
            ae_fr = parse_timecode(hdr_dur, hdr_fps)
            if ae_fr:
                total_frames = ae_fr
        elif hdr_start and hdr_end and hdr_fps:
            sf = parse_timecode(hdr_start, hdr_fps)
            ef = parse_timecode(hdr_end, hdr_fps)
            if sf is not None and ef is not None and ef >= sf:
                total_frames = ef - sf + 1

        out_to = parse_output_to(line)
        if out_to:
            with RLOCK:
                RJOB["cur"] = out_to

        pr = parse_progress(line, total_frames) if total_frames else None
        if pr is not None:
            with RLOCK:
                pct_calc = min(1.0, pct_base + pr * pct_span)
                RJOB["pct"] = max(RJOB.get("pct") or 0.0, pct_calc)
                if item_name:
                    for it in RJOB.get("items", []):
                        if it.get("name") == item_name:
                            it["pct"] = pr          # доля и в очередь
                            break
        if "Finished composition" in line or "Total Time Elapsed" in line:
            with RLOCK:
                pct_calc = min(1.0, pct_base + pct_span)
                RJOB["pct"] = max(RJOB.get("pct") or 0.0, pct_calc)

    def _drain(tail=False):
        """Дочитать то, что насос уже положил в очередь. tail=True — после выхода
        процесса подождать метку конца stdout (хвост ещё в трубе), но не дольше двух
        секунд: застрявший на закрытии трубы насос-демон не должен держать джоб."""
        deadline = _time.time() + 2.0
        while True:
            try:
                ln = q.get(timeout=0.1) if tail and _time.time() < deadline else q.get_nowait()
            except queue.Empty:
                return
            if ln is None:
                tail = False            # stdout закрыт — дальше только остатки очереди
                continue
            _handle(ln)

    try:
        while True:
            if p.poll() is not None:
                break
            now = _time.time()
            if now - last_activity >= AE_STALL_KILL_SEC:
                remit("⚠ aerender не отвечает {min} минут — прогон снят (сторож простоя)",
                      min=int(AE_STALL_KILL_SEC // 60))
                _kill_proc(p)
                stalled = True
                break
            if not warned and now - last_activity >= AE_STALL_WARN_SEC:
                warned = True
                remit("⚠ aerender молчит {min} минут — обычно рендер столько "
                      "не длится; сниму процесс через {kill} минут простоя",
                      min=int(AE_STALL_WARN_SEC // 60), kill=int(AE_STALL_KILL_SEC // 60))
            if RJOB["cancel"]:
                _kill_proc(p)
                break
            _time.sleep(0.5)
            _drain()
    finally:
        rc = p.wait()
        _drain(tail=True)          # процесс умер — добираем и РАЗБИРАЕМ остаток stdout
        with RLOCK:
            if RPROC is p:
                RPROC = None
    if stalled and item_name:
        # Причина снятия — и в логе, и в failed, ровно там, где сработал сторож: элемент
        # очереди этому вызову уже передан (item_name), второй копии текста не заводим.
        item_fail(RJOB, RLOCK, item_name,
                  "aerender не отвечает %d минут — прогон снят" % (AE_STALL_KILL_SEC // 60),
                  bucket="failed")
    return AE_STALLED if stalled else rc


def _run_proc_afx(cmd):
    """AfterFX -noui -r одиночного ролика со сторожем простоя.
    Раньше здесь был _run_proc с блокирующим чтением stdout: молчащий AfterFX вешал
    джоб навсегда. Теперь stdout читается фоном, а основной поток ждёт процесс и
    следит за активностью: процесс жив, но за AE_STALL_WARN_SEC не появилось ни одной
    новой строки stdout — предупреждение в лог; за AE_STALL_KILL_SEC — процесс снимается
    (_kill_proc) и возвращается AE_STALLED (сообщение уже в логе). Отсчёт простоя —
    от последней НОВОЙ строки, не от старта."""
    global RPROC
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                             **task_popen_kwargs())
    except FileNotFoundError as e:
        remit("не найден исполняемый файл: {err}", err=str(e))
        return -1
    with RLOCK:
        RPROC = p
    if RJOB["cancel"]:
        _kill_proc(p)
    # Построчное чтение в фон: readline() блокирует навсегда, а сторожу нужен таймаут.
    q = _pump_stdout(p)
    last_activity = _time.time()
    warned = False
    stalled = False
    try:
        while True:
            if p.poll() is not None:
                break
            now = _time.time()
            if now - last_activity >= AE_STALL_KILL_SEC:
                remit("⚠ After Effects не отвечает {min} минут — прогон снят (сторож простоя)",
                      min=int(AE_STALL_KILL_SEC // 60))
                _kill_proc(p)
                stalled = True
                break
            if not warned and now - last_activity >= AE_STALL_WARN_SEC:
                warned = True
                remit("⚠ After Effects молчит {min} минут — обычно сборка проекта столько "
                      "не длится; сниму процесс через {kill} минут простоя",
                      min=int(AE_STALL_WARN_SEC // 60), kill=int(AE_STALL_KILL_SEC // 60))
            if RJOB["cancel"]:
                _kill_proc(p)
                break
            _time.sleep(0.5)
            # дочитать всё, что появилось в stdout с прошлого тика
            while True:
                try:
                    line = q.get_nowait()
                except queue.Empty:
                    break
                if line is None:
                    break        # stdout закрыт — дальше ждём только выхода процесса
                line = line.rstrip("\r\n")
                if line.strip():
                    last_activity = _time.time()
                    remit(line)
    finally:
        rc = p.wait()
        # процесс умер — добираем остаток stdout (pump уже дошёл до EOF)
        while True:
            try:
                ln = q.get_nowait()
            except queue.Empty:
                break
            if ln is not None and ln.strip():
                remit(ln.rstrip("\r\n"))
        with RLOCK:
            if RPROC is p:
                RPROC = None
    return AE_STALLED if stalled else rc


def _mark_stopped_waits():
    """«Стоп» по RJOB["cancel"]: файлам, до которых работа не дошла (stage="wait"),
    проставить stage="stopped", чтобы очередь показывала их «остановлено», а не «в очереди».
    Своя копия, как _mark_stopped_waits в jobs.py, но под RLOCK/RJOB рендера."""
    with RLOCK:
        for it in RJOB.get("items", []):
            if it.get("stage") == "wait":
                it["stage"] = "stopped"


def _run_render_single(norm, outdir, render_dir):
    """Одиночный рендер: безголовый .jsx (очередь+save+quit) -> verify_jsx ->
    AfterFX -noui -r (собрать .aep) -> aerender -project (рендер). Это же путь остаётся
    для набора из ОДНОГО ролика: поведение и .jsx ровно сегодняшние."""
    try:
        from core import verify_jsx
        from core import xml2ae
        from .inserts import _convert_inserts
        remit("=== Рендер AE: {count} файл(ов), папка вывода: {dir} ===",
              count=len(norm), dir=render_dir)
        total_n = len(norm)
        t_jsx_base, t_aep_base, t_rnd_base, has_stats = get_baseline_phase_durations(total_n)
        p_jsx_end, p_aep_end = calc_phase_bounds(t_jsx_base, t_aep_base, t_rnd_base)
        t_jsx_start = _time.time()
        with RLOCK:
            RJOB["stage_label"] = "сборка таймлайнов"
            RJOB["stage_done"] = 0
            RJOB["stage_total"] = total_n
            RJOB["pct"] = 0.0
            if has_stats:
                RJOB["eta"] = t_jsx_base
                RJOB["eta_phase"] = t_jsx_base
                RJOB["eta_total"] = t_jsx_base + t_aep_base + t_rnd_base
                RJOB["eta_preliminary"] = True
            else:
                RJOB["eta"] = None
                RJOB["eta_phase"] = None
                RJOB["eta_total"] = None
                RJOB["eta_preliminary"] = False
        # 1) построить БЕЗГОЛОВЫЙ .jsx (с render_dir): обычная сборка даёт ручной
        #    вариант без очереди/save/quit — его в -noui прогонять нечего
        jsx_list = []
        comp_names = {}   # стем .jsx -> имя главной композиции (.mov по нему)
        for i, j in enumerate(norm):
            if RJOB["cancel"]:
                remit("⏹ Остановлено")
                _mark_stopped_waits()
                return
            stem = os.path.splitext(os.path.basename(j["xml_path"]))[0]
            with RLOCK:
                RJOB["cur"] = stem
                RJOB["stage_done"] = i
                pct_step = (i / total_n) * p_jsx_end
                RJOB["pct"] = max(RJOB.get("pct") or 0.0, min(p_jsx_end, pct_step))
            item_set(RJOB, RLOCK, stem, stage="jsx")   # этап 1: сборка безголового .jsx
            od = j.get("outdir") or outdir or os.path.dirname(j["xml_path"])
            os.makedirs(od, exist_ok=True)
            # ТОЛЬКО абсолютный: путь .aep и путь своего лога Python вписывает в .jsx
            # константами, а ExtendScript разрешает относительный File() от папки AE,
            # а не от нашей — проект тогда сохраняется неизвестно куда (поймано на
            # приёмке CD).
            jp = os.path.abspath(os.path.join(od, stem + ".jsx"))
            # Вставки готовим ДО сборки, как это делает _run_build_job через
            # _adopt_inserts: .jsx строится напрямую через to_ae_full, и без этой
            # строки webp-вставка дошла бы до AE и уронила предполёт.
            # Переезд в базу (adopt) тут НЕ делаем намеренно: он перемещает файлы,
            # а фронт узнаёт об этом через JOB["insmoved"], которого у RJOB нет.
            _convert_inserts(j.get("inserts") or [], emit=remit)
            kw = {k: v for k, v in j.items() if k not in ("xml_path", "outdir")}
            remit("  {stem}: сборка безголового .jsx…", stem=stem)
            try:
                comp_name_out = []
                xml2ae.to_ae_full(j["xml_path"], jp, render_dir=render_dir,
                                  emit=remit, cancel=lambda: RJOB["cancel"],
                                  comp_name_out=comp_name_out, **kw)
                # .mov пишется по ИМЕНИ КОМПОЗИЦИИ (om.file = main.name), а не по стему
                # .jsx — файл 01_C0233.xml может дать композицию C0233
                comp_name = comp_name_out[0] if comp_name_out else stem
                jsx_list.append(jp)
                comp_names[stem] = comp_name
                with RLOCK:
                    RJOB["stage_done"] = i + 1
                    pct_step = ((i + 1) / total_n) * p_jsx_end
                    RJOB["pct"] = max(RJOB.get("pct") or 0.0, min(p_jsx_end, pct_step))
                remit("  -> {path}", path=jp)
            except xml2ae.Cancelled:
                remit("⏹ Остановлено — рендер не запускался")
                return
            except (ReelsiError, SystemExit) as e:
                # Ошибка вида «рото не посчитано…» — это SystemExit (BaseException), и
                # раньше она проскакивала мимо обоих except Exception: ролик не попадал
                # ни в failed, ни в лог, а рендер продолжался с пустым набором.
                txt = sysexit_text(e)
                remit("  ОШИБКА сборки: {err}", err=txt)
                item_fail(RJOB, RLOCK, stem, txt, bucket="failed")
            except ReelsiError: raise
            except Exception as e:
                remit("  ОШИБКА сборки: {err}", err=str(e))
                # клип, у которого не собрался даже безголовый .jsx — item_fail
                item_fail(RJOB, RLOCK, stem, str(e), bucket="failed")
        if not jsx_list:
            remit("Ничего не собралось — рендер не запускался")
            return
        jsx_dur = _time.time() - t_jsx_start
        # 2) предполётная проверка — ДО AE. Пропажи: не рендерим вовсе.
        with RLOCK:
            RJOB["stage_label"] = "проверка файлов"
            RJOB["pct"] = max(RJOB.get("pct") or 0.0, p_jsx_end)
        bad = []
        for jp in jsx_list:
            stem = os.path.splitext(os.path.basename(jp))[0]
            item_set(RJOB, RLOCK, stem, stage="check")   # этап 2: предполётная проверка
            rep = verify_jsx.verify(jp)
            for e in rep.errors:
                bad.append((stem, e))
        if bad:
            remit("Предполётная проверка НЕ пройдена — рендер не запущен:")
            for name, e in bad:
                remit("  ✗ {name}: {err}", name=name, err=str(e))
            # это ПОКЛИПОВЫЕ падения (конкретные .jsx), не глобальные — item_fail
            for name, e in bad:
                item_fail(RJOB, RLOCK, name, e, bucket="failed")
            return
        remit("Проверка .jsx пройдена — файлов на диске хватает, запускаю AE")
        # 3) найти AE (самый свежий)
        ae = find_ae()
        if not ae:
            remit("After Effects не найден (искал в «C:\\Program Files\\Adobe\\Adobe After Effects *»). "
                  "Проверь установку и запусти снова.")
            # ГЛОБАЛЬНОЕ падение БЕЗ конкретного клипа (AE не найден для всего набора) —
            # прямой записью в failed, а не item_fail: в items писать нечего.
            with RLOCK:
                RJOB["failed"].append({"name": "AE", "reason": "After Effects не найден"})
            return
        afx, aer, aename = ae
        with RLOCK:
            RJOB["ae"] = aename
            RJOB["out_dir"] = render_dir
            RJOB["stage_label"] = "сборка проекта"
            RJOB["stage_done"] = 0
            RJOB["stage_total"] = len(jsx_list)
            RJOB["pct"] = max(RJOB.get("pct") or 0.0, p_jsx_end)
            if has_stats:
                RJOB["eta"] = t_aep_base
                RJOB["eta_phase"] = t_aep_base
                RJOB["eta_total"] = t_aep_base + t_rnd_base
                RJOB["eta_preliminary"] = True
            else:
                RJOB["eta"] = None
                RJOB["eta_phase"] = None
                RJOB["eta_total"] = None
                RJOB["eta_preliminary"] = False
        remit("AE: {name}", name=aename)
        remit("  AfterFX: {path}", path=afx)
        remit("  aerender: {path}", path=aer)
        t_aep_start = _time.time()
        aep_dur = 0.0
        render_dur = 0.0
        # 4) цепочка на каждый .jsx
        for i, jp in enumerate(jsx_list):
            if RJOB["cancel"]:
                remit("⏹ Остановлено")
                _mark_stopped_waits()
                break
            stem = os.path.splitext(os.path.basename(jp))[0]
            aep = re.sub(r"\.jsx$", ".aep", jp, flags=re.I)
            aelog = re.sub(r"\.aep$", ".aelog.txt", aep)
            jsx_call, why = jsx_call_path(jp)     # короткое имя: пробел рвёт -r
            if why:
                remit("  короткое имя для {name} не вышло (том без 8.3-имён) — выполняю копию {alt_name}",
                      name=os.path.basename(jp), alt_name=os.path.basename(jsx_call))
            remit("--- {stem}: сборка проекта (AfterFX -noui) ---", stem=stem)
            with RLOCK:
                RJOB["cur"] = stem
                RJOB["stage_done"] = i
                pct_step = p_jsx_end + (i / len(jsx_list)) * (p_aep_end - p_jsx_end)
                RJOB["pct"] = max(RJOB.get("pct") or 0.0, min(p_aep_end, pct_step))
            item_set(RJOB, RLOCK, stem, stage="aep")      # этап 3: AfterFX -noui собирает .aep
            # Открытая копия AE перехватывает наш AfterFX -noui: он
            # возвращает код 0 за 0 секунд, а скрипт уезжает в НЕЁ — переписывает её
            # проект и закрывает без вопроса о сохранении. Такой запуск — стоп ДО старта.
            if ae_running():
                remit("  After Effects ОТКРЫТ — прогон этого ролика не начинаю. Закрой "
                      "After Effects и запусти рендер снова: иначе скрипт уйдёт в открытую "
                      "копию, перепишет её проект и закроет её.")
                item_fail(RJOB, RLOCK, stem,
                          "After Effects открыт — закрой его и запусти рендер снова",
                          bucket="failed")
                continue
            # Гигиена журнала: .aelog.txt прошлого прогона удаляем
            # ДО запуска AfterFX, иначе «нет файла = скрипт не запустился» снова врёт.
            # Не смогли удалить (файл занят) — ошибка прогона с текстом, не продолжение.
            rm_err = remove_stale_aelog(aelog)
            if rm_err:
                remit("  не удалить старый журнал {log}: {err} — файл занят? Рендер пропущен.",
                      log=aelog, err=rm_err)
                item_fail(RJOB, RLOCK, stem,
                          "не удалить старый .aelog.txt (файл занят?)", bucket="failed")
                continue
            # .aep НЕ стираем (человек мог доработать его руками) — запоминаем время
            # изменения ДО запуска, после прогона проверяем «обновлён этим прогоном»,
            # а не «существует».
            aep_mtime = os.path.getmtime(aep) if os.path.isfile(aep) else None
            t_afx = _time.time()
            rc = _run_proc_afx([afx, "-noui", "-r", jsx_call])
            afx_sec = _time.time() - t_afx
            if RJOB["cancel"]:
                break
            if rc == AE_STALLED:
                # сторож снял зависший AfterFX — сообщение уже в логе, ролик помечаем здесь
                item_fail(RJOB, RLOCK, stem,
                          "After Effects не отвечает %d минут — прогон снят"
                          % (AE_STALL_KILL_SEC // 60), bucket="failed")
                continue
            if rc != 0:
                remit("  AfterFX завершился с кодом {code} — смотри вывод выше", code=rc)
            # Признаков два: нет .aelog.txt -> скрипт НЕ запустился (путь,
            # пробелы); лог есть, но .aep не обновлён этим прогоном -> скрипт шёл и
            # споткнулся, в логе видно где. Один признак (наличие .aep) путал «не
            # запустился» с «упал на середине».
            if not os.path.isfile(aelog):
                if afx_sec < AE_FAST_EXIT_SEC:
                    remit("  AfterFX вышел мгновенно (за {sec:.1f} с) — обычно это значит, "
                          "что скрипт ушёл в УЖЕ ОТКРЫТУЮ копию After Effects, а не поднял "
                          "свой экземпляр.", sec=afx_sec)
                remit("  НЕТ {log} — AE не выполнил скрипт. Проверь путь к .jsx: пробел "
                      "в имени рвёт аргумент -r, и скрипт не запускается вовсе. Рендер пропущен.",
                      log=aelog)
                item_fail(RJOB, RLOCK, stem,
                          "AE не выполнил скрипт (.aelog.txt не появился)", bucket="failed")
                continue
            with open(aelog, encoding="utf-8-sig", errors="replace") as fh:
                for line in fh:
                    line = line.rstrip("\r\n")
                    if line:
                        remit("  [aelog] " + line)
            if not aep_updated_by_run(aep, aep_mtime):
                if os.path.isfile(aep):
                    remit("  {path} остался от прошлого прогона — AfterFX не пересохранил "
                          "проект этим прогоном (см. строки [aelog] выше).", path=aep)
                remit("  НЕТ {path} — скрипт шёл, но споткнулся (см. строки [aelog] выше). "
                      "Рендер пропущен.", path=aep)
                item_fail(RJOB, RLOCK, stem,
                          "AfterFX не сохранил .aep (смотри .aelog.txt)", bucket="failed")
                continue
            with RLOCK:
                RJOB["stage_done"] = i + 1
                pct_step = p_jsx_end + ((i + 1) / len(jsx_list)) * (p_aep_end - p_jsx_end)
                RJOB["pct"] = max(RJOB.get("pct") or 0.0, min(p_aep_end, pct_step))
            aep_dur = _time.time() - t_aep_start
            total = comp_frames(open(jp, encoding="utf-8-sig").read())
            with RLOCK:
                RJOB["stage_label"] = "рендер"
                RJOB["stage_done"] = 0
                RJOB["stage_total"] = len(jsx_list)
                RJOB["cur"] = os.path.basename(aep)
                RJOB["pct"] = max(RJOB.get("pct") or 0.0, p_aep_end)
                if has_stats:
                    RJOB["eta"] = t_rnd_base
                    RJOB["eta_phase"] = t_rnd_base
                    RJOB["eta_total"] = t_rnd_base
                    RJOB["eta_preliminary"] = True
                else:
                    RJOB["eta"] = None
                    RJOB["eta_phase"] = None
                    RJOB["eta_total"] = None
                    RJOB["eta_preliminary"] = False
            remit("--- {stem}: рендер (aerender), кадров: {frames} ---",
                  stem=stem, frames=total)
            item_set(RJOB, RLOCK, stem, stage="render")   # этап 4: aerender рендерит
            aep_call, aep_why = aep_call_path(aep)   # кириллица в -project рвёт aerender
            if aep_why:
                remit("  короткое имя для {name} не вышло (том без 8.3-имён) — рендерю копию {alt_name}",
                      name=os.path.basename(aep), alt_name=os.path.basename(aep_call))
            t_rnd_start = _time.time()
            rc = _run_proc([aer, "-project", aep_call], total_frames=total, item_name=stem,
                           pct_base=p_aep_end, pct_span=(1.0 - p_aep_end))
            render_dur = _time.time() - t_rnd_start
            if aep_why:
                try:
                    os.remove(aep_call)   # копия служебная — мусор в рабочей папке не оставляем
                except OSError:
                    pass  # служебную копию .aep уже убрали
            if RJOB["cancel"]:
                break
            if rc != 0:
                if rc == AE_STALLED:
                    # снял сторож простоя: сообщение в логе и пометка ролика — внутри
                    # _run_proc, второй раз то же самое не пишем
                    continue
                remit("  aerender завершился с кодом {code} — смотри вывод выше", code=rc)
                item_fail(RJOB, RLOCK, stem, f"aerender rc={rc}", bucket="failed")
                continue
            mov = os.path.join(render_dir, comp_names.get(stem, stem) + ".mov")
            if not rendered_ok(mov):
                # aerender на «Path is not valid» вернул 0 — коду возврата не верим, «Готово»
                # только по факту файла
                remit("  aerender вернул 0, но файла нет — смотри вывод aerender выше")
                item_fail(RJOB, RLOCK, stem,
                          "aerender вернул 0, но файла нет — смотри вывод aerender выше",
                          bucket="failed")
                continue
            # Одно место записи «готово»: item_done и кладёт путь в result,
            # и переводит элемент в done — вторым местом их не развести (живёт
            # здесь же: набор копит список, а не перезаписывает последний).
            item_done(RJOB, RLOCK, stem, mov, bucket="result")
            with RLOCK:
                RJOB["stage_done"] = 1
                RJOB["pct"] = 1.0
                RJOB["eta"] = None
                RJOB["eta_phase"] = None
                RJOB["eta_total"] = None
            remit("Готово: {path}", path=mov)
            save_render_stats(1, jsx_dur, aep_dur, render_dur)
    except (ReelsiError, SystemExit) as e:
        # Глобальное падение того же рода, что Exception ниже, но текст — из umsg:
        # traceback от «сними галку рото в стиле» человеку ничего не объясняет.
        txt = sysexit_text(e)
        log.error("Рендер прерван: %s", txt)
        remit("ОШИБКА: {err}", err=txt)
        with RLOCK:
            RJOB["failed"].append({"name": "рендер", "reason": txt})
    except ReelsiError: raise
    except Exception:
        log.exception("Сбой процесса рендера")
        import traceback
        remit("ОШИБКА:\n{tb}", tb=traceback.format_exc().strip().splitlines()[-1])
        # ГЛОБАЛЬНОЕ падение (внутренняя ошибка рендера) БЕЗ конкретного клипа — прямой
        # записью в failed, а не item_fail: связано ни с одним файлом набора.
        with RLOCK:
            RJOB["failed"].append({"name": "рендер", "reason": "внутренняя ошибка"})
    finally:
        with RLOCK:
            RJOB.update(running=False, done=True, cur="",
                        pct=(1.0 if RJOB["result"] and not RJOB["failed"] else (RJOB["pct"] or 0)))


def _run_render_combined(batch, outdir, render_dir):
    """Рендер набора «Один на всё» (решение 2026-09-11): вместо N клиповых .jsx —
    ОДИН файл Reelsi_all.jsx (build_combined с comps_global=True и префиксами бинов), мастер
    выполняет ровно его. Предполёт verify_jsx — по общему файлу ОДИН раз (верификатор
    умеет разбирать «один .jsx на всё» по таймлайнам). Отсеять один непрошедший ролик
    нельзя — .jsx один на всех: ошибки предполёта останавливают весь набор (нужно
    исправить файл или убрать ролик из набора и запустить снова). Этап «сборка
    проекта» отслеживается по строкам «таймлайн ok:» журнала мастера:
    stage_total = total_n, собранные таймлайны переходят в стадию built, текущий
    собираемый — в aep, остальные ждут в wait; в конце этапа переводим все ролики
    набора дальше по очереди этапов."""
    from core import verify_jsx
    from core import xml2ae
    from .inserts import _convert_inserts
    total_n = len(batch)
    t_jsx_base, t_aep_base, t_rnd_base, has_stats = get_baseline_phase_durations(total_n)
    p_jsx_end, p_aep_end = calc_phase_bounds(t_jsx_base, t_aep_base, t_rnd_base)
    with RLOCK:
        RJOB["stage_label"] = "сборка таймлайнов"
        RJOB["stage_done"] = 0
        RJOB["stage_total"] = total_n
        RJOB["pct"] = 0.0
        if has_stats:
            RJOB["eta"] = t_jsx_base
            RJOB["eta_phase"] = t_jsx_base
            RJOB["eta_total"] = t_jsx_base + t_aep_base + t_rnd_base
            RJOB["eta_preliminary"] = True
        else:
            RJOB["eta"] = None
            RJOB["eta_phase"] = None
            RJOB["eta_total"] = None
            RJOB["eta_preliminary"] = False
    # Вставки конвертим ДО сборки (как _run_build_job): иначе
    # webp-вставка дошла бы до AE и уронила предполёт.
    for j in batch:
        _convert_inserts(j.get("inserts") or [], emit=remit)
    stems = [os.path.splitext(os.path.basename(j["xml_path"]))[0] for j in batch]
    # Папка набора — та же, где в режиме «Отдельные .jsx» лежали бы клиповые .jsx
    od = outdir or os.path.dirname(batch[0]["xml_path"])
    os.makedirs(od, exist_ok=True)
    combined_jp = os.path.abspath(os.path.join(od, "Reelsi_all.jsx"))
    # outdir — параметр СБОРКИ, а не плана сцены: build_combined отдаёт словарь в
    # to_ae_full(**kw) -> scene_plan, и лишний ключ ронял бы общий .jsx (см. api/build.py)
    jobs = [{k: v for k, v in j.items() if k != "outdir"} for j in batch]
    comp_names_out = []

    def _combined_progress(done, total):
        # build_combined зовёт progress ПЕРЕД сборкой done-го таймлайна — показываем
        # «собрано done-1», чтобы счётчик не забегал вперёд
        with RLOCK:
            RJOB["stage_done"] = max(0, done - 1)
            RJOB["stage_total"] = total
            frac = (max(0, done - 1) / float(total)) * p_jsx_end
            RJOB["pct"] = max(RJOB.get("pct") or 0.0, min(p_jsx_end, frac))
        if 1 <= done <= total:
            item_set(RJOB, RLOCK, stems[done - 1], stage="jsx")

    t_jsx_start = _time.time()
    try:
        path, n = xml2ae.build_combined(jobs, combined_jp, emit=remit,
                                        cancel=lambda: RJOB["cancel"],
                                        progress=_combined_progress,
                                        comps_global=True,
                                        comp_names_out=comp_names_out)
    except xml2ae.Cancelled:
        remit("⏹ Остановлено — рендер не запускался")
        return
    except (ReelsiError, SystemExit) as e:
        # Падение общее, а не поклиповое (.jsx один на весь набор), поэтому метим failed
        # КАЖДЫЙ ролик — с понятным текстом из umsg вместо str(e).
        txt = sysexit_text(e)
        remit("  ОШИБКА сборки: {err}", err=txt)
        for stem in stems:
            item_fail(RJOB, RLOCK, stem, txt, bucket="failed")
        return
    except ReelsiError: raise
    except Exception as e:
        remit("  ОШИБКА сборки: {err}", err=str(e))
        for stem in stems:
            item_fail(RJOB, RLOCK, stem, str(e), bucket="failed")
        return
    if RJOB["cancel"]:
        remit("⏹ Остановлено")
        _mark_stopped_waits()
        return
    if not comp_names_out:
        remit("Ничего не собралось — рендер не запускался")
        return
    jsx_dur = _time.time() - t_jsx_start
    remit("  -> {path}", path=combined_jp)
    with RLOCK:
        RJOB["stage_done"] = total_n
        RJOB["stage_total"] = total_n
        RJOB["pct"] = max(RJOB.get("pct") or 0.0, p_jsx_end)
        RJOB["eta"] = None
        RJOB["eta_phase"] = None
        RJOB["eta_total"] = None
    # 2) предполётная проверка — ОДИН раз по общему файлу (verify_jsx разбирает
    # «один .jsx на всё» по таймлайнам). Непрошедший ролик отсеять нельзя: ошибки
    # останавливают набор ЦЕЛИКОМ, и это сказано в логе прямо.
    with RLOCK:
        RJOB["cur"] = ""
        RJOB["stage_label"] = "проверка файлов"
    for stem in stems:
        item_set(RJOB, RLOCK, stem, stage="check")
    rep = verify_jsx.verify(combined_jp)
    errs = [str(e) for e in rep.errors]
    if errs:
        remit("Предполётная проверка НЕ пройдена — рендер не запущен:")
        for e in errs:
            remit("  ✗ {err}", err=e)
        remit("Режим «Один на всё»: непрошедший предполёт ролик снимает ВЕСЬ набор — "
              ".jsx один на всех роликов. Исправь файл или убери ролик из набора и запусти снова.")
        for stem in stems:
            item_fail(RJOB, RLOCK, stem, errs[0], bucket="failed")
        return
    remit("Проверка .jsx пройдена — файлов на диске хватает, запускаю AE")
    # Кадры композиций — по таймлайнам общего файла (порядок таймлайнов = порядок
    # набора; .mov ждём по ИМЕНИ КОМПОЗИЦИИ из comp_names_out)
    raw = open(combined_jp, encoding="utf-8-sig").read()
    blocks = verify_jsx.split_timelines(raw)
    frames_per = [comp_frames(b) for b in blocks] if len(blocks) == total_n else [None] * total_n
    good = []
    for i, stem in enumerate(stems):
        cn = comp_names_out[i] if i < len(comp_names_out) else stem
        fr = frames_per[i] if i < len(frames_per) else None
        good.append((stem, cn, combined_jp, fr))
    # 3) мастер-скрипт из ОДНОГО общего файла. Мастер и .aep — в папку набора
    # (где лежит Reelsi_all.jsx), в render_dir уезжает только готовое видео
    batch_dir = od
    aep_path = os.path.join(batch_dir, "reelsi_batch.aep")
    master_path = os.path.join(batch_dir, "render_master.jsx")
    from core.xml2ae.build import _write_master
    _write_master([combined_jp], master_path, aep_path, render_dir)
    remit("Мастер-скрипт: {path} ({n} роликов)", path=master_path, n=total_n)
    # 4) найти AE
    ae = find_ae()
    if not ae:
        remit("After Effects не найден (искал в «C:\\Program Files\\Adobe\\Adobe After Effects *»). "
              "Проверь установку и запусти снова.")
        with RLOCK:
            RJOB["failed"].append({"name": "AE", "reason": "After Effects не найден"})
        return
    afx, aer, aename = ae
    with RLOCK:
        RJOB["ae"] = aename
        RJOB["out_dir"] = render_dir
        RJOB["stage_label"] = "сборка проекта"
        RJOB["stage_done"] = 0
        RJOB["stage_total"] = total_n  # прогресс по таймлайнам набора
        RJOB["pct"] = max(RJOB.get("pct") or 0.0, p_jsx_end)
        if has_stats:
            RJOB["eta"] = t_aep_base
            RJOB["eta_phase"] = t_aep_base
            RJOB["eta_total"] = t_aep_base + t_rnd_base
            RJOB["eta_preliminary"] = True
        else:
            RJOB["eta"] = None
            RJOB["eta_phase"] = None
            RJOB["eta_total"] = None
            RJOB["eta_preliminary"] = False
        RJOB["cur"] = ""
    remit("AE: {name}", name=aename)
    # 5) один AfterFX -noui -r мастера -> один .aep
    master_call, why = jsx_call_path(master_path)
    if why:
        remit("  короткое имя для {name} не вышло (том без 8.3-имён) — выполняю копию {alt_name}",
              name=os.path.basename(master_path), alt_name=os.path.basename(master_call))
    remit("--- сборка общего проекта (AfterFX -noui, мастер) ---")
    aelog = re.sub(r"\.aep$", ".aelog.txt", aep_path)
    # Открытая копия AE перехватывает наш AfterFX -noui — стоп
    if ae_running():
        remit("After Effects ОТКРЫТ — прогон набора не начинаю. Закрой After Effects и "
              "запусти рендер снова: иначе мастер уйдёт в открытую копию, перепишет её "
              "проект и закроет её без вопроса о сохранении.")
        for stem, _cn, _jp, _fr in good:
            item_fail(RJOB, RLOCK, stem,
                      "After Effects открыт — закрой его и запусти рендер снова",
                      bucket="failed")
        return
    # Гигиена журнала: .aelog.txt прошлого прогона удаляем ДО
    rm_err = remove_stale_aelog(aelog)
    if rm_err:
        remit("не удалить старый журнал {log}: {err} — файл занят? Прогон остановлен.",
              log=aelog, err=rm_err)
        for stem, _cn, _jp, _fr in good:
            item_fail(RJOB, RLOCK, stem,
                      "не удалить старый .aelog.txt (файл занят?)", bucket="failed")
        return
    # Стадии на время работы AfterFX: первый ролик собирается (aep),
    # остальные ждут очереди (wait); по мере готовности таймлайнов _tail_master_log
    # переводит собранные в built, текущий в aep, остальные оставляет в wait.
    for i, (stem, _cn, _jp, _fr) in enumerate(good):
        item_set(RJOB, RLOCK, stem, stage="aep" if i == 0 else "wait")
    with RLOCK:
        RJOB["cur"] = good[0][0] if good else ""
    # .aep НЕ стираем (человек мог доработать его руками) — время ДО запуска мастера
    aep_mtime = os.path.getmtime(aep_path) if os.path.isfile(aep_path) else None
    t_aep_start = _time.time()
    rc = _run_proc_master(afx, "-noui", "-r", master_call,
                          good=good, render_dir=render_dir, aelog_path=aelog,
                          p_jsx_end=p_jsx_end, p_aep_end=p_aep_end,
                          t_aep_base=t_aep_base, t_render_base=t_rnd_base,
                          has_stats=has_stats, whole_file=True)
    aep_dur = _time.time() - t_aep_start
    if RJOB["cancel"]:
        return
    if not os.path.isfile(aelog):
        if aep_dur < AE_FAST_EXIT_SEC:
            remit("AfterFX вышел мгновенно (за {sec:.1f} с) — обычно это значит, что "
                  "мастер-скрипт ушёл в УЖЕ ОТКРЫТУЮ копию After Effects, а не поднял "
                  "свой экземпляр.", sec=aep_dur)
        remit("  НЕТ {log} — AE не выполнил мастер-скрипт. Проверь путь: пробел в имени рвёт "
              "аргумент -r, и скрипт не запускается вовсе. Рендер пропущен.", log=aelog)
        for stem, _cn, _jp, _fr in good:
            item_fail(RJOB, RLOCK, stem,
                      "AE не выполнил мастер-скрипт (.aelog.txt не появился)", bucket="failed")
        return
    with open(aelog, encoding="utf-8-sig", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\r\n")
            if line:
                remit("  [aelog] " + line)
    if rc != 0:
        remit("  AfterFX завершился с кодом {code} — смотри вывод выше", code=rc)
    if not aep_updated_by_run(aep_path, aep_mtime):
        if os.path.isfile(aep_path):
            remit("  {path} остался от прошлого прогона — мастер не пересохранил проект "
                  "этим прогоном (см. строки [aelog] выше).", path=aep_path)
        remit("  НЕТ {path} — мастер шёл, но споткнулся (см. строки [aelog] выше). "
              "Рендер пропущен.", path=aep_path)
        for stem, _cn, _jp, _fr in good:
            item_fail(RJOB, RLOCK, stem,
                      "AfterFX не сохранил .aep (смотри .aelog.txt)", bucket="failed")
        return
    # 6) один aerender -project: рендерит всю очередь; по мере Finished composition —
    #    соответствующий элемент в done по ИМЕНИ КОМПОЗИЦИИ
    with RLOCK:
        RJOB["cur"] = ""
        RJOB["stage_label"] = "рендер"
        RJOB["stage_done"] = 0
        RJOB["stage_total"] = len(good)
        RJOB["pct"] = max(RJOB.get("pct") or 0.0, p_aep_end)
        for it in RJOB.get("items", []):
            if it.get("stage") != "error":
                it["stage"] = "wait"
    remit("--- рендер набора (aerender -project), композиций: {n} ---", n=len(good))
    aep_call, aep_why = aep_call_path(aep_path)
    if aep_why:
        remit("  короткое имя для {name} не вышло (том без 8.3-имён) — рендерю копию {alt_name}",
              name=os.path.basename(aep_path), alt_name=os.path.basename(aep_call))
    t_rnd_start = _time.time()
    rc = _run_proc_batch(aer, aep_call, [(s, cn, fr) for s, cn, _j, fr in good], render_dir,
                         p_aep_end=p_aep_end, t_render_base=t_rnd_base, has_stats=has_stats)
    render_dur = _time.time() - t_rnd_start
    if aep_why:
        try:
            os.remove(aep_call)
        except OSError:
            pass  # служебную копию .aep уже убрали
    if RJOB["cancel"]:
        _mark_stopped_waits()
        return
    if RJOB["result"] and not RJOB["failed"]:
        save_render_stats(len(good), jsx_dur, aep_dur, render_dur)


def _run_proc_master(afx, *args, good, render_dir, aelog_path,
                     p_jsx_end=PHASE_JSX_END, p_aep_end=PHASE_AEP_END,
                     t_aep_base=DEFAULT_AEP_SEC, t_render_base=DEFAULT_RENDER_SEC,
                     has_stats=False, whole_file=False):
    """AfterFX -noui -r мастера с ЖИВЫМ прогрессом и нелинейной оценкой. ExtendScript буферизует
    файл-лог до close(), поэтому мастер после КАЖДОГО ролика закрывает лог и открывает
    заново на дозапись — строки evalFile ok: появляются на диске сразу. Здесь читаем
    лог в цикле ожидания процесса и по каждой новой строке переводим ролик из aep в
    готовность к рендеру (stage=check — до предполёта уже пройден; ставим render позже)
    и пишем строку в общий лог интерфейса. evalFile ОШИБКА: — item_fail ролику.
    item_done остаётся один и только про рендер — второго источника «готово» нет.
    whole_file — мастер выполняет ОДИН .jsx на ВЕСЬ набор («Один на всё»):
    прогресс сборки отслеживается по строкам «таймлайн ok:» в журнале мастера (k-я строка —
    k-й ролик по порядку набора), stage_total = len(good), собранные ролики получают stage=built."""
    try:
        p = subprocess.Popen([afx] + list(args),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                             **task_popen_kwargs())
    except FileNotFoundError as e:
        remit("не найден исполняемый файл: {err}", err=str(e))
        return -1
    with RLOCK:
        global RPROC
        RPROC = p
        RJOB["stage_label"] = "сборка проекта"
        RJOB["stage_done"] = 0
        RJOB["stage_total"] = len(good)
        RJOB["pct"] = max(RJOB.get("pct") or 0.0, p_jsx_end)
        if has_stats:
            RJOB["eta"] = t_aep_base
            RJOB["eta_phase"] = t_aep_base
            RJOB["eta_total"] = t_aep_base + t_render_base
            RJOB["eta_preliminary"] = True
        else:
            RJOB["eta"] = None
            RJOB["eta_phase"] = None
            RJOB["eta_total"] = None
            RJOB["eta_preliminary"] = False
    if RJOB["cancel"]:
        _kill_proc(p)
    # стем -> имя композиции и путь к его .jsx (для лога)
    by_jsx = {}
    for stem, _cn, jp, _fr in good:
        by_jsx[os.path.abspath(jp).replace("\\", "/")] = stem
    aelog = aelog_path
    timelines = {"stems": [stem for stem, _cn, _jp, _fr in good], "built": 0} if whole_file else None
    seen = set()
    t_build_start = _time.time()
    last_clip_time = t_build_start
    clip_durations = []
    built_n = 0              # сколько роликов мастер уже собрал (evalFile ok)
    built_times = []         # (сек от старта, built_n) — для скользящей скорости сборки
    default_per = (t_aep_base / len(good)) if len(good) > 0 else DEFAULT_AEP_SEC
    # Сторож простоя: мастер жив и молчит — предупреждение на
    # AE_STALL_WARN_SEC, снятие на AE_STALL_KILL_SEC. Отсчёт — от последней НОВОЙ
    # строки .aelog.txt, а не от старта: тяжёлый ролик собирается минутами.
    last_activity = t_build_start
    warn_sent = False
    stalled = False
    # Построчное чтение stdout в фон: AfterFX -noui пишет ошибки в stdout,
    # переполняя буфер трубы и блокируя процесс в WriteFile при отсутствии чтения.
    q = _pump_stdout(p)
    try:
        while True:
            if p.poll() is not None:
                break
            now = _time.time()
            seen_n = len(seen)
            new_n = _tail_master_log(aelog, seen, by_jsx, timelines=timelines)
            if len(seen) != seen_n or new_n:
                last_activity = now       # любая новая строка лога — процесс жив
            while True:
                try:
                    line = q.get_nowait()
                except queue.Empty:
                    break
                if line is None:
                    break
                line = line.rstrip("\r\n")
                if line.strip():
                    last_activity = now
                    remit(line)
            if new_n:
                built_n += new_n
                clip_dur = (now - last_clip_time) / float(new_n)
                for _ in range(new_n):
                    clip_durations.append(max(0.1, clip_dur))
                last_clip_time = now
                built_times.append((now - t_build_start, built_n))

                weights = predict_aep_times(clip_durations, len(good), default_per_clip=default_per)
                sum_done = sum(weights[:built_n])
                sum_tot = sum(weights)
                aep_frac = min(1.0, sum_done / sum_tot) if sum_tot > 0 else (built_n / float(len(good)))
                pct_calc = p_jsx_end + aep_frac * (p_aep_end - p_jsx_end)

                with RLOCK:
                    RJOB["stage_done"] = built_n
                    RJOB["pct"] = max(RJOB.get("pct") or 0.0, min(p_aep_end, pct_calc))
                    if built_n < len(good):
                        rem_phase = sum(weights[built_n:])
                        rem_total = rem_phase + t_render_base
                        if (now - t_build_start) >= 15.0 and len(built_times) >= 2:
                            RJOB["eta"] = rem_phase
                            RJOB["eta_phase"] = rem_phase
                            RJOB["eta_total"] = rem_total
                            RJOB["eta_preliminary"] = False
                            RJOB["stage_label"] = "сборка проекта"
                        elif has_stats:
                            RJOB["eta"] = rem_phase
                            RJOB["eta_phase"] = rem_phase
                            RJOB["eta_total"] = rem_total
                            RJOB["eta_preliminary"] = True
                            RJOB["stage_label"] = "сборка проекта"
                        else:
                            RJOB["eta"] = None
                            RJOB["eta_phase"] = None
                            RJOB["eta_total"] = None
                            RJOB["eta_preliminary"] = False
            # Сторож простоя: молчание дольше AE_STALL_KILL_SEC — снять процесс и
            # завершить прогон честной ошибкой, а не висеть вечно.
            if now - last_activity >= AE_STALL_KILL_SEC:
                remit("⚠ After Effects не отвечает {min} минут — прогон снят (сторож простоя)",
                      min=int(AE_STALL_KILL_SEC // 60))
                _kill_proc(p)
                stalled = True
                break
            if not warn_sent and now - last_activity >= AE_STALL_WARN_SEC:
                warn_sent = True
                remit("⚠ After Effects молчит {min} минут — обычно сборка проекта столько "
                      "не длится; сниму процесс через {kill} минут простоя",
                      min=int(AE_STALL_WARN_SEC // 60), kill=int(AE_STALL_KILL_SEC // 60))
            if RJOB["cancel"]:
                _kill_proc(p)
                break
            _time.sleep(0.5)
        _tail_master_log(aelog, seen, by_jsx, timelines=timelines)
    finally:
        rc = p.wait()
        while True:
            try:
                ln = q.get_nowait()
            except queue.Empty:
                break
            if ln is not None and ln.strip():
                remit(ln.rstrip("\r\n"))
        with RLOCK:
            if RPROC is p:
                RPROC = None
    with RLOCK:
        RJOB["cur"] = ""
        RJOB["eta"] = None      # сборка кончилась — оценку гасим
        RJOB["eta_phase"] = None
        RJOB["eta_total"] = None
        RJOB["stage_done"] = len(good)
        RJOB["pct"] = max(RJOB.get("pct") or 0.0, p_aep_end)
    # ролики, до которых мастер не дошёл (evalFile не случился) — item_fail
    fail_reason = ("мастер не выполнил evalFile этого ролика" if not stalled else
                   "After Effects не отвечает %d минут — прогон снят" % (AE_STALL_KILL_SEC // 60))
    for stem, _cn, jp, _fr in good:
        if os.path.abspath(jp).replace("\\", "/") not in seen:
            item_fail(RJOB, RLOCK, stem, fail_reason, bucket="failed")
    return rc


def _tail_master_log(aelog, seen, by_jsx, timelines=None):
    """Дочитать файл-лог мастера с последнего места: evalFile ok/ОШИБКА -> живой прогресс
    этапа «AfterFX собирает проект». seen — уже обработанные строки.
    timelines — словарь состояния {"stems": [...], "built": 0} для режима «Один на всё»
. Возвращает число новых таймлайнов (при timelines) либо новых «evalFile ok»
    (для ETA сборки)."""
    try:
        if not os.path.isfile(aelog):
            return 0
        with open(aelog, encoding="utf-8-sig", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return 0
    new_ok = 0
    new_tl = 0
    if timelines is not None:
        stems = timelines.get("stems", [])
        total_tl = sum(1 for ln in lines if ln.rstrip("\r\n").startswith("таймлайн ok: "))
        prev_tl = timelines.get("built", 0)
        new_tl = max(0, total_tl - prev_tl)
        if new_tl:
            timelines["built"] = total_tl
            b = total_tl
            for idx, stem in enumerate(stems):
                if idx < b:
                    item_set(RJOB, RLOCK, stem, stage="built")
                elif idx == b:
                    item_set(RJOB, RLOCK, stem, stage="aep")
                else:
                    item_set(RJOB, RLOCK, stem, stage="wait")
            with RLOCK:
                if b < len(stems):
                    RJOB["cur"] = stems[b]
                else:
                    RJOB["cur"] = ""
    for line in lines:
        line = line.rstrip("\r\n")
        if not line or line in seen:
            continue
        seen.add(line)
        if "evalFile ok: " in line:
            path = line.split("evalFile ok: ", 1)[1].strip()
            norm_p = os.path.abspath(path).replace("\\", "/")
            seen.add(norm_p)
            if timelines is None:
                new_ok += 1
                stem = by_jsx.get(norm_p) or by_jsx.get(path.replace("\\", "/"))
                if stem:
                    # ролик собран мастером — готов к рендеру; до aerender ставим aep
                    item_set(RJOB, RLOCK, stem, stage="aep")
                    with RLOCK:
                        RJOB["cur"] = stem
                    remit("собран: {stem}", stem=stem)
        elif "evalFile ОШИБКА: " in line:
            path = line.split("evalFile ОШИБКА: ", 1)[1].split(" — ")[0].strip()
            stem = by_jsx.get(path.replace("\\", "/"))
            if stem:
                item_fail(RJOB, RLOCK, stem,
                          "мастер не собрал ролик (evalFile ОШИБКА, см. лог мастера)",
                          bucket="failed")
        elif line.startswith("REELSI-MASTER") or line.startswith("comp ok: ") or line.startswith("таймлайн ok: "):
            remit("  [aelog] " + line)
    return new_tl if timelines is not None else new_ok


def _run_proc_batch(aer, aep_call, comps, render_dir,
                    p_aep_end=PHASE_AEP_END, t_render_base=DEFAULT_RENDER_SEC,
                    has_stats=False):
    """aerender для общего проекта: ДВА живых прогресса. comps —
    [(стем, имя_композиции, кадры), …]: стем адресует строку очереди, имя композиции —
    файл на диске, кадры — M для процента текущей композиции, когда aerender его
    не печатает, и для ETA.
    - имя текущей композиции: из строки «Output To:», запасной — по порядку;
    - кадры текущей композиции: из блока Start/End/Duration/Frame Rate,
      запасной — comp_frames из .jsx;
    - pct элемента: доля ТЕКУЩЕЙ композиции (parse_progress, кадры (N)/(N/M));
    - RJOB["pct"]: общий по набору = (готовых + доля текущей) / всего — монотонно,
      без прыжка к 1.0 на «Finished composition» (это конец ОДНОЙ, а не всего);
    - RJOB["cur"]: имя текущей композиции;
    - RJOB["eta"]: ETA рендера в секундах (скользящая скорость за 30-60 с);
    - сторож простоя — те же константы и та же схема, что у одиночного `_run_proc`:
    молчащий aerender снимается, причина идёт в лог и в failed."""
    try:
        p = subprocess.Popen([aer, "-project", aep_call],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                             **task_popen_kwargs())
    except FileNotFoundError as e:
        remit("не найден исполняемый файл: {err}", err=str(e))
        return -1
    with RLOCK:
        global RPROC
        RPROC = p
        RJOB["stage_label"] = "рендер"
        RJOB["stage_done"] = 0
        RJOB["stage_total"] = len(comps)
        RJOB["pct"] = max(RJOB.get("pct") or 0.0, p_aep_end)
        if has_stats:
            RJOB["eta"] = t_render_base
            RJOB["eta_phase"] = t_render_base
            RJOB["eta_total"] = t_render_base
            RJOB["eta_preliminary"] = True
        else:
            RJOB["eta"] = None
            RJOB["eta_phase"] = None
            RJOB["eta_total"] = None
            RJOB["eta_preliminary"] = False
    if RJOB["cancel"]:
        _kill_proc(p)
    done_names = []          # стемы, чей .mov уже готов
    next_idx = 0             # индекс следующей композиции по порядку очереди
    cur_stem = None          # стем текущей композиции
    comp_frames = {s: fr for s, _cn, fr in comps}  # стем -> кадры (запасные из .jsx)
    total_frames = sum(comp_frames.values()) or 0
    done_frames = 0          # кадры уже готовых композиций
    hdr_dur, hdr_fps, hdr_start, hdr_end = None, None, None, None
    t_start = _time.time()
    samples = []             # (сек от старта, суммарно отрендеренных кадров)
    # Вывод читает ФОНОВЫЙ поток (`_pump_stdout`), а главный цикл сторожит простой
    # Пока главный поток сидел в `for line in p.stdout`, молчащий
    # aerender — окно ошибки, зависший плагин, недоступный сетевой диск — держал джоб
    # «рендер идёт» неограниченно долго: сторож был только у одиночного `_run_proc`,
    # хотя CHANGELOG обещает его и `aerender` на наборе.
    q = _pump_stdout(p)
    last_activity = _time.time()
    warned = False
    stalled = False

    def _handle(line):
        """Разбор одной строки aerender — ровно прежнее тело цикла. Обработчик ОДИН и
        на живой цикл, и на добирание хвоста после выхода/снятия процесса."""
        nonlocal total_frames, done_frames, cur_stem, next_idx, last_activity
        nonlocal hdr_dur, hdr_fps, hdr_start, hdr_end
        line = line.rstrip("\r\n")
        if not line.strip():
            return
        last_activity = _time.time()
        remit(line)

        # Блок параметров композиции от AE
        m_dur = TC_DUR.search(line)
        if m_dur:
            hdr_dur = m_dur.group(1)
        fps_val = parse_frame_rate(line)
        if fps_val is not None:
            hdr_fps = fps_val
        m_start = TC_START.search(line)
        if m_start:
            hdr_start = m_start.group(1)
        m_end = TC_END.search(line)
        if m_end:
            hdr_end = m_end.group(1)

        ae_frames = None
        if hdr_dur and hdr_fps:
            ae_frames = parse_timecode(hdr_dur, hdr_fps)
        elif hdr_start and hdr_end and hdr_fps:
            sf = parse_timecode(hdr_start, hdr_fps)
            ef = parse_timecode(hdr_end, hdr_fps)
            if sf is not None and ef is not None and ef >= sf:
                ae_frames = ef - sf + 1

        # Имя текущей композиции — из «Output To:»
        out_to = parse_output_to(line)
        if out_to:
            stem = comp_to_stem(out_to, comps)
            if stem is not None and stem not in done_names:
                cur_stem = stem
                comp_name = next((_cn for _s, _cn, _fr in comps if _s == cur_stem), cur_stem)
                with RLOCK:
                    RJOB["cur"] = comp_name
                item_set(RJOB, RLOCK, cur_stem, stage="render")
                if ae_frames:
                    comp_frames[cur_stem] = ae_frames
                    total_frames = sum(comp_frames.values()) or 0

        if "Finished composition" in line or "Total Time Elapsed" in line:
            # имя композиции из маркера — в кавычках с точкой, см. finished_comp_name
            # ищем по нему ролик — это meta["name"], а НЕ стем .jsx
            name = finished_comp_name(line)
            stem = comp_to_stem(name, comps) if name else None
            if stem is None:
                stem = cur_stem
            if stem is None:
                # иначе — следующий по порядку очереди
                if next_idx < len(comps):
                    stem = comps[next_idx][0]
                    next_idx += 1
            if stem:
                comp_name = next((_cn for _s, _cn, _fr in comps if _s == stem), stem)
                frames = comp_frames.get(stem, 0)
                done_names.append(stem)
                done_frames += frames or 0
                mov = os.path.join(render_dir, comp_name + ".mov")
                item_done(RJOB, RLOCK, stem, mov, bucket="result")
                item_set(RJOB, RLOCK, stem, pct=1.0)
                remit("Готово: {path}", path=mov)
                # общий прогресс: готовых (включая только что) / всего — монотонно в диапазоне фазы 3
                with RLOCK:
                    RJOB["stage_done"] = len(done_names)
                    RJOB["stage_total"] = len(comps)
                    render_frac = min(1.0, len(done_names) / float(len(comps)))
                    pct_step = p_aep_end + render_frac * (1.0 - p_aep_end)
                    RJOB["pct"] = max(RJOB.get("pct") or 0.0, min(1.0, pct_step))
                    RJOB["stage_label"] = "рендер"
                    RJOB["cur"] = ""
                cur_stem = None
                hdr_dur, hdr_fps, hdr_start, hdr_end = None, None, None, None
                return

        # Если cur_stem еще не определен по Output To: — запасной вариант по порядку
        if cur_stem is None:
            for _s, _cn, _fr in comps:
                if _s not in done_names:
                    cur_stem = _s
                    break
            if cur_stem:
                comp_name = next((_cn for _s, _cn, _fr in comps if _s == cur_stem), cur_stem)
                with RLOCK:
                    RJOB["cur"] = comp_name
                item_set(RJOB, RLOCK, cur_stem, stage="render")

        if cur_stem:
            if ae_frames and comp_frames.get(cur_stem) != ae_frames:
                comp_frames[cur_stem] = ae_frames
                total_frames = sum(comp_frames.values()) or 0

            cur_frames = comp_frames.get(cur_stem)
            comp_name = next((_cn for _s, _cn, _fr in comps if _s == cur_stem), cur_stem)
            pr = parse_progress(line, cur_frames)
            if pr is not None:
                item_set(RJOB, RLOCK, cur_stem, pct=pr)
                with RLOCK:
                    RJOB["cur"] = comp_name
                    RJOB["stage_done"] = len(done_names)
                    RJOB["stage_total"] = len(comps)
                    render_frac = min(1.0, (len(done_names) + pr) / float(len(comps)))
                    pct_step = p_aep_end + render_frac * (1.0 - p_aep_end)
                    RJOB["pct"] = max(RJOB.get("pct") or 0.0, min(1.0, pct_step))
                    RJOB["stage_label"] = "рендер"
                    # ETA: скользящая скорость кадров/с за последние 30-60 с
                    if cur_frames:
                        now = _time.time()
                        rendered = done_frames + int(round((pr or 0) * cur_frames))
                        samples.append((now - t_start, rendered))
                        if len(samples) > 1:
                            while samples and (now - t_start) - samples[0][0] > ETA_WINDOW:
                                samples.pop(0)
                        if (now - t_start) >= 15.0 and len(samples) > 1:
                            eta_calc = eta_secs(samples, total_frames, rendered)
                            if eta_calc is not None:
                                RJOB["eta"] = eta_calc
                                RJOB["eta_phase"] = eta_calc
                                RJOB["eta_total"] = eta_calc
                                RJOB["eta_preliminary"] = False
                        elif has_stats:
                            rem_frac = max(0.0, 1.0 - render_frac)
                            RJOB["eta"] = rem_frac * t_render_base
                            RJOB["eta_phase"] = rem_frac * t_render_base
                            RJOB["eta_total"] = rem_frac * t_render_base
                            RJOB["eta_preliminary"] = True

    try:
        while True:
            got = True
            try:
                ln = q.get(timeout=0.5)
            except queue.Empty:
                got = False          # тишина в очереди — простой проверяем ниже, не выходим
                ln = None
            now = _time.time()
            if now - last_activity >= AE_STALL_KILL_SEC:
                remit("⚠ aerender не отвечает {min} минут — прогон снят (сторож простоя)",
                      min=int(AE_STALL_KILL_SEC // 60))
                _kill_proc(p)
                stalled = True
                break
            if not warned and now - last_activity >= AE_STALL_WARN_SEC:
                warned = True
                remit("⚠ aerender молчит {min} минут — обычно рендер столько "
                      "не длится; сниму процесс через {kill} минут простоя",
                      min=int(AE_STALL_WARN_SEC // 60), kill=int(AE_STALL_KILL_SEC // 60))
            if RJOB["cancel"]:
                _kill_proc(p)
                break
            if not got:
                continue             # строк не было — простой уже проверен
            if ln is None:
                break                # метка конца stdout: процесс отработал — ждём выход
            _handle(ln)
    finally:
        rc = p.wait()
        # Хвост вывода: после снятия по простою или «Стопу» последние строки aerender
        # (в том числе «Finished composition») ещё лежат в трубе и в очереди насоса.
        # Ждём метку конца stdout, но не дольше двух секунд: насос-демон не должен
        # держать джоб.
        deadline = _time.time() + 2.0
        while True:
            try:
                ln = q.get(timeout=0.1) if _time.time() < deadline else q.get_nowait()
            except queue.Empty:
                break
            if ln is None:
                deadline = 0.0      # stdout закрыт — дальше только остаток очереди
                continue
            _handle(ln)
        with RLOCK:
            if RPROC is p:
                RPROC = None
    with RLOCK:
        RJOB["eta"] = None      # рендер кончился — оценку гасим
        RJOB["eta_phase"] = None
        RJOB["eta_total"] = None
        RJOB["stage_done"] = len(done_names)
        if done_names and len(done_names) == len(comps):
            RJOB["pct"] = 1.0
    # композиции, чей .mov так и не появился — item_fail (aerender мог споткнуться)
    stall_reason = ("aerender не отвечает %d минут — прогон снят (сторож простоя)"
                    % (AE_STALL_KILL_SEC // 60))
    for stem, comp_name, _fr in comps:
        if stem not in done_names:
            mov = os.path.join(render_dir, comp_name + ".mov")
            if rendered_ok(mov):
                item_done(RJOB, RLOCK, stem, mov, bucket="result")
            else:
                item_fail(RJOB, RLOCK, stem,
                          stall_reason if stalled
                          else "aerender не отрендерил (см. вывод aerender выше)",
                          bucket="failed")
    return AE_STALLED if stalled else rc


def _run_render_job(norm, outdir, render_dir):
    """Диспетчер рендера. Набор из ОДНОГО ролика — ровно прежний путь
    (_run_render_single: безголовый .jsx, AfterFX, aerender). Набор из нескольких —
    ВСЕГДА _run_render_combined: один проект AE и один общий Reelsi_all.jsx
    (решение пользователя 2026-09-11; радио multimode на рендер не влияет)."""
    try:
        if not norm:
            remit("Набор пуст")
            return
        items_init(RJOB, RLOCK,
                   [os.path.splitext(os.path.basename(j["xml_path"]))[0] for j in norm])
        if len(norm) <= 1:
            _run_render_single(norm, outdir, render_dir)
            return
        remit("=== Рендер AE: {count} файл(ов) в общий проект, папка вывода: {dir} ===",
              count=len(norm), dir=render_dir)
        if RJOB["cancel"]:
            _mark_stopped_waits()
            return
        _run_render_combined(norm, outdir, render_dir)
    except (ReelsiError, SystemExit) as e:
        # Последний рубеж диспетчера: тот же путь провала, что у Exception ниже, но в
        # лог и RJOB["failed"] уходит понятный текст из umsg.
        txt = sysexit_text(e)
        log.error("Рендер прерван: %s", txt)
        remit("ОШИБКА: {err}", err=txt)
        with RLOCK:
            RJOB["failed"].append({"name": "рендер", "reason": txt})
    except ReelsiError: raise
    except Exception:
        log.exception("Сбой в потоке рендера")
        import traceback
        remit("ОШИБКА:\n{tb}", tb=traceback.format_exc().strip().splitlines()[-1])
        with RLOCK:
            RJOB["failed"].append({"name": "рендер", "reason": "внутренняя ошибка"})
    finally:
        with RLOCK:
            RJOB.update(running=False, done=True, cur="",
                        pct=(1.0 if RJOB["result"] and not RJOB["failed"] else (RJOB["pct"] or 0)))
        # Журнал заданий: рендер закрыт. Раньше состояние жило только в
        # памяти, и после перезапуска сервера оборванный рендер выглядел как «не было».
        journal_finish(RJOB)
        # Лок задач держим до самого конца рендера — включая «Стоп» и ошибку
        # (без него поверх рендера стартовала нарезка).
        _cross_lock_release()


@bp.route("/api/render_run", methods=["POST"])
def api_render_run():
    """Запустить безголовый рендер. body: {jobs, outdir?, render_dir}.
    Свой RJOB, но ОБЩИЙ лок задач с нарезкой и сборкой .jsx:
    две тяжёлые задачи на одной видеокарте одновременно не идут."""
    d = request.get_json() or {}
    try:
        from .build import _norm_or_error
        norm = _norm_or_error(d.get("jobs") or [], "render_set_invalid")
        if not norm:
            raise ReelsiError(umsg("set_empty", "Набор пуст"))
        render_dir = jstr(d, "render_dir").strip().strip('"') or default_render_dir()
        try:
            os.makedirs(render_dir, exist_ok=True)
        except OSError as e:
            raise ReelsiError(umsg("render_outdir",
                                  f"не создать папку вывода: {render_dir} — {e}",
                                  dir=render_dir, err=str(e)))
        with RLOCK:
            if RJOB["running"]:
                raise ReelsiError(umsg("render_busy",
                                      "Рендер уже идёт — дождись конца или нажми «Остановить»"))
        # Общий лок задач (тот же, что берёт job_start у нарезки и сборки): без него
        # рендер стартовал поверх идущего джоба — две тяжёлые задачи на одной
        # видеокарте, да ещё обе пишут <outdir>/<stem>.jsx. JOB["running"] проверяем
        # отдельно: между «занял JOB» и «взял лок» у job_start есть окно.
        with LOCK:
            job_busy = JOB["running"]
        if job_busy or not _cross_lock_acquire():
            raise ReelsiError(umsg("busy_wait",
                                  "Уже выполняется другая задача — дождись или смотри Логи"))
        try:
            with RLOCK:
                RJOB.update(running=True, done=False, log=[], pct=None, cur="", ae="",
                            out_dir=render_dir, result=[], failed=[], cancel=False,
                            items=[], eta=None, eta_phase=None, eta_total=None,
                            eta_preliminary=False, stage_label=None, stage_done=0,
                            stage_total=0)
            # Журнал заданий: после перезапуска сервера /api/render_status
            # отдаёт рендер как interrupted — с клипом, на котором он оборвался.
            journal_bind(RJOB, "render", "Рендер AE", "pct")
            threading.Thread(target=_run_render_job,
                             args=(norm, jstr(d, "outdir").strip().strip('"'),
                                   render_dir),
                             daemon=True).start()
        except ReelsiError: raise
        except Exception:
            with RLOCK:
                RJOB["running"] = False
            _cross_lock_release()          # поток не родился — лок не оставляем занятым
            raise
        return jsonify(ok=True)
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/render_status")
def api_render_status():
    """Состояние рендер-джоба: running/done/pct/cur/ae/out_dir/result/failed/eta + лог.
    interrupted — рендер, оборванный перезапуском сервера (журнал заданий)."""
    with RLOCK:
        return jsonify(ok=True, running=RJOB["running"], done=RJOB["done"],
                       pct=RJOB["pct"], cur=RJOB["cur"], ae=RJOB["ae"],
                       out_dir=RJOB["out_dir"], result=RJOB["result"],
                       failed=list(RJOB["failed"]), items=RJOB.get("items") or [],
                       eta=RJOB.get("eta"), eta_phase=RJOB.get("eta_phase"),
                       eta_total=RJOB.get("eta_total"),
                       eta_preliminary=RJOB.get("eta_preliminary", False),
                       stage_label=RJOB.get("stage_label"),
                       stage_done=RJOB.get("stage_done", 0),
                       stage_total=RJOB.get("stage_total", 0),
                       default_dir=default_render_dir(),
                       interrupted=journal_interrupted("render"),
                       log=RJOB["log"][-200:])
