# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Оркестрация безголового рендера и состояние его задания — без Flask.

Почему отдельным модулем. Раньше всё это лежало в `api/render.py` — HTTP-модуле на
1500 строк: движок рендера оттуда уже вынесли (`core/aerender.py`), а запуск AfterFX
и aerender, сторожа простоя, разбор очереди, мастер-проект и ETA по ходу оставались
рядом с роутами. Ни CLI, ни тесты не могли позвать оркестрацию, не подняв Flask.

Что здесь. Держатель состояния `RenderJob` (словарь состояния той же формы, что
прежний RJOB, замок, текущий процесс, вывод в лог) и вся оркестрация: запуск
процессов со сторожем простоя, разбор их вывода, живой прогресс, «Стоп». Имена —
БЕЗ ведущего подчёркивания: это интерфейс модуля, а не внутренности файла. `job`
передаётся первым аргументом: состояние у джоба одно, а экземпляр его живёт у
владельца (`api/render.py`).

Межпроцессный лок видеокарты (`_cross_lock_release`), очередь этапов
(`items_init`/`item_set`/`item_done`/`item_fail`) и журнал заданий берутся из
`core/jobstate.py` — то же, чем пользуются нарезка и сборка.

Лог рендера пишется функцией держателя (`job.emit`). В бою её подставляет
`api/render.py` своим `remit`: `core.app_meta.wrap_emit` пропускает колбэк как есть
ТОЛЬКО если его модуль начинается на «api.» — с writer'ом из ядра структурная
запись {"t", "v"} потерялась бы, а по ней фронт переводит строку.
"""
from __future__ import annotations
import copy, os, queue, re, subprocess, threading
import time as _time
from typing import Any, Callable, Sequence

from core.aerender import (AE_FAST_EXIT_SEC, AE_STALL_KILL_SEC, AE_STALL_WARN_SEC, AE_STALLED,
                           DEFAULT_AEP_SEC, DEFAULT_RENDER_SEC, ETA_WINDOW, PHASE_AEP_END,
                           PHASE_JSX_END, TC_DUR, TC_END, TC_START, aep_call_path,
                           aep_updated_by_run, ae_running, calc_phase_bounds, comp_frames,
                           comp_to_stem, eta_secs, find_ae,
                           finished_comp_name, get_baseline_phase_durations, jsx_call_path,
                           parse_frame_rate, parse_output_to, parse_progress, parse_timecode,
                           predict_aep_times, remove_stale_aelog, rendered_ok, save_render_stats)
from core.jobstate import (item_done, item_fail, item_set, items_init, journal_finish,
                           kill_tree, log_entry, sysexit_text, task_popen_kwargs,
                           _cross_lock_release)
from core.umsg import ReelsiError
from core.applog import get_logger

# Имя логгера оставлено прежним (reelsi.render): оркестрация переехала, а строки
# файла-лога менять незачем — по ним ищут причину сбоя рендера.
log = get_logger("reelsi.render")


def _no_convert(inserts: Any, emit: Any = None) -> dict[str, Any]:
    """Заглушка перекодировки вставок у держателя без владельца (тесты, CLI).

    В бою её подставляет `api/render.py` (см. `RenderJob.convert_inserts`): без
    перекодировки webp/avif-вставка дойдёт до AE и уронит предполёт."""
    return {}


class RenderJob(dict):
    """Состояние рендера и всё, чем оно управляется: словарь, замок, процесс, лог.

    Словарь — ТОЙ ЖЕ формы, что прежний RJOB: его читают /api/render_status, журнал
    заданий и интерфейс. Наследование от dict выбрано намеренно — владелец состояния
    (`api/render.py`) отдаёт наружу ровно те объекты, что и раньше (RJOB/RLOCK читают
    conftest.py и тесты), а оркестрация получает ОДИН объект, а не три глобальных
    имени, которые надо не забыть передать.

    emit — функция вывода в лог: подставляется владельцем (см. шапку модуля), по
    умолчанию пишет структурную запись сама. on_proc — хук смены текущего процесса:
    им `api/render.py` держит живым модульный `RPROC`, который читает
    `webui._cleanup_on_exit` (webui.py этим заданием не правится). convert_inserts —
    перекодировка вставок перед сборкой .jsx: её место в `api/inserts.py` (она зовёт
    разбор тела запроса из HTTP-слоя), а ядро api не импортирует — значит, функцию
    даёт владелец состояния, как и emit.
    """

    def __init__(self, state: dict[str, Any] | None = None,
                 emit: Callable[..., Any] | None = None,
                 lock: Any = None,
                 on_proc: Callable[[Any], None] | None = None,
                 convert_inserts: Callable[..., Any] | None = None) -> None:
        super().__init__(state or {})
        self.lock = lock if lock is not None else threading.Lock()
        self.proc: Any = None
        self.on_proc = on_proc
        self.emit = emit if emit is not None else self._log_line
        self.convert_inserts = convert_inserts if convert_inserts is not None else _no_convert

    def _log_line(self, line: str = "", **vars: Any) -> None:
        """Строка в лог рендера (свой лог, не общий JOB) — запись по умолчанию."""
        with self.lock:
            entry = log_entry(line, vars)
            self["log"].append(entry)
            if len(self["log"]) > 2000:
                del self["log"][:len(self["log"]) - 2000]

    def set_proc(self, p: Any) -> None:
        """Запомнить текущий процесс (None — кончился) и сказать об этом наружу."""
        self.proc = p
        if self.on_proc is not None:
            self.on_proc(p)

    def __deepcopy__(self, memo: dict[int, Any]) -> dict[str, Any]:
        """Копия — ПЛОСКИЙ словарь состояния.

        Замок и процесс копировать нельзя (да и незачем): снимок берут, чтобы вернуть
        СОСТОЯНИЕ джоба между тестами (tests/conftest.py)."""
        return copy.deepcopy(dict(self), memo)


def kill_proc(p: Any) -> None:
    """taskkill /T /F — дерево: aerender сам по себе не всегда держит детей,
    но после убийства не должен остаться ни один процесс рендера.

    Реализация одна на весь пакет — `core.jobstate.kill_tree`. Короткое имя
    `_kill_proc` осталось в `api/render.py` ради `webui._cleanup_on_exit`."""
    kill_tree(p)


def render_kill(job: RenderJob) -> None:
    """«Стоп» из интерфейса (/api/cancel зовёт): флаг джобу + реально убить
    текущий subprocess (AfterFX или aerender) с деревом."""
    with job.lock:
        job["cancel"] = True
        p = job.proc
    if p and p.poll() is None:
        kill_proc(p)


def pump_stdout(p: Any) -> queue.Queue[Any]:
    """Фоновый поток чтения stdout процесса в queue.Queue.
    Предотвращает переполнение OS-буфера трубы при обильном выводе процесса (напр. AfterFX -noui)
    и позволяет сторожу опрашивать активность с таймаутом без зависания в readline()."""
    q: queue.Queue[Any] = queue.Queue()

    def _pump() -> None:
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


def run_proc(job: RenderJob, cmd: Sequence[str] | list[str], total_frames: int | None = None, item_name: str | None = None, pct_base: float = 0.0, pct_span: float = 1.0) -> int:
    """Запустить процесс (aerender), стримить вывод в лог рендера со сторожем простоя.
    Возвращает код выхода или AE_STALLED. item_name — элемент очереди, которому
    дублируется доля рендера (aerender — единственный этап с честным процентом).
    pct_base и pct_span задают долю шкалы (например 0.35..1.0 на фазе рендера).
    Сторож простоя aerender: молчание AE_STALL_WARN_SEC — предупреждение,
    AE_STALL_KILL_SEC — снятие. Константы те же, что у AfterFX (5 и 20 мин): рендер даже
    тяжёлых кадров даёт строки прогресса чаще, а молчание означает зависание или окно ошибки."""
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                             **task_popen_kwargs())
    except FileNotFoundError as e:
        job.emit("не найден исполняемый файл: {err}", err=str(e))
        return -1
    with job.lock:
        job.set_proc(p)
    if job["cancel"]:
        kill_proc(p)
    q = pump_stdout(p)
    last_activity = _time.time()
    warned = False
    stalled = False
    hdr_dur, hdr_fps, hdr_start, hdr_end = None, None, None, None

    def _handle(raw: str) -> None:
        """Разбор одной строки aerender: лог, заголовок AE, прогресс,
        Output To. Обработчик ОДИН и на живой цикл, и на добирание хвоста после выхода
        процесса: во второй копии (только запись строк) у конца прогона терялся разбор
        «Finished composition» и «Total Time Elapsed», а по ним ставится 100% шкалы."""
        nonlocal total_frames, last_activity, hdr_dur, hdr_fps, hdr_start, hdr_end
        line = raw.rstrip("\r\n")
        if not line.strip():
            return
        last_activity = _time.time()
        job.emit(line)

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
            with job.lock:
                job["cur"] = out_to

        pr = parse_progress(line, total_frames) if total_frames else None
        if pr is not None:
            with job.lock:
                pct_calc = min(1.0, pct_base + pr * pct_span)
                job["pct"] = max(job.get("pct") or 0.0, pct_calc)
                if item_name:
                    for it in job.get("items", []):
                        if it.get("name") == item_name:
                            it["pct"] = pr          # доля и в очередь
                            break
        if "Finished composition" in line or "Total Time Elapsed" in line:
            with job.lock:
                pct_calc = min(1.0, pct_base + pct_span)
                job["pct"] = max(job.get("pct") or 0.0, pct_calc)

    def _drain(tail: bool = False) -> None:
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
                job.emit("⚠ aerender не отвечает {min} минут — прогон снят (сторож простоя)",
                      min=int(AE_STALL_KILL_SEC // 60))
                kill_proc(p)
                stalled = True
                break
            if not warned and now - last_activity >= AE_STALL_WARN_SEC:
                warned = True
                job.emit("⚠ aerender молчит {min} минут — обычно рендер столько "
                      "не длится; сниму процесс через {kill} минут простоя",
                      min=int(AE_STALL_WARN_SEC // 60), kill=int(AE_STALL_KILL_SEC // 60))
            if job["cancel"]:
                kill_proc(p)
                break
            _time.sleep(0.5)
            _drain()
    finally:
        rc = p.wait()
        _drain(tail=True)          # процесс умер — добираем и РАЗБИРАЕМ остаток stdout
        with job.lock:
            if job.proc is p:
                job.set_proc(None)
    if stalled and item_name:
        # Причина снятия — и в логе, и в failed, ровно там, где сработал сторож: элемент
        # очереди этому вызову уже передан (item_name), второй копии текста не заводим.
        item_fail(job, job.lock, item_name,
                  "aerender не отвечает %d минут — прогон снят" % (AE_STALL_KILL_SEC // 60),
                  bucket="failed")
    return AE_STALLED if stalled else rc


def run_proc_afx(job: RenderJob, cmd: Sequence[str] | list[str]) -> int:
    """AfterFX -noui -r одиночного ролика со сторожем простоя.
    Раньше здесь был `run_proc` с блокирующим чтением stdout: молчащий AfterFX вешал
    джоб навсегда. Теперь stdout читается фоном, а основной поток ждёт процесс и
    следит за активностью: процесс жив, но за AE_STALL_WARN_SEC не появилось ни одной
    новой строки stdout — предупреждение в лог; за AE_STALL_KILL_SEC — процесс снимается
    (`kill_proc`) и возвращается AE_STALLED (сообщение уже в логе). Отсчёт простоя —
    от последней НОВОЙ строки, не от старта."""
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                             **task_popen_kwargs())
    except FileNotFoundError as e:
        job.emit("не найден исполняемый файл: {err}", err=str(e))
        return -1
    with job.lock:
        job.set_proc(p)
    if job["cancel"]:
        kill_proc(p)
    # Построчное чтение в фон: readline() блокирует навсегда, а сторожу нужен таймаут.
    q = pump_stdout(p)
    last_activity = _time.time()
    warned = False
    stalled = False
    try:
        while True:
            if p.poll() is not None:
                break
            now = _time.time()
            if now - last_activity >= AE_STALL_KILL_SEC:
                job.emit("⚠ After Effects не отвечает {min} минут — прогон снят (сторож простоя)",
                      min=int(AE_STALL_KILL_SEC // 60))
                kill_proc(p)
                stalled = True
                break
            if not warned and now - last_activity >= AE_STALL_WARN_SEC:
                warned = True
                job.emit("⚠ After Effects молчит {min} минут — обычно сборка проекта столько "
                      "не длится; сниму процесс через {kill} минут простоя",
                      min=int(AE_STALL_WARN_SEC // 60), kill=int(AE_STALL_KILL_SEC // 60))
            if job["cancel"]:
                kill_proc(p)
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
                    job.emit(line)
    finally:
        rc = p.wait()
        # процесс умер — добираем остаток stdout (pump уже дошёл до EOF)
        while True:
            try:
                ln = q.get_nowait()
            except queue.Empty:
                break
            if ln is not None and ln.strip():
                job.emit(ln.rstrip("\r\n"))
        with job.lock:
            if job.proc is p:
                job.set_proc(None)
    return AE_STALLED if stalled else rc


def mark_stopped_waits(job: RenderJob) -> None:
    """«Стоп» по job["cancel"]: файлам, до которых работа не дошла (stage="wait"),
    проставить stage="stopped", чтобы очередь показывала их «остановлено», а не «в очереди».
    Своя копия, как `_mark_stopped_waits` в api/jobs.py, но под замком и джобом рендера."""
    with job.lock:
        for it in job.get("items", []):
            if it.get("stage") == "wait":
                it["stage"] = "stopped"


def run_render_single(job: RenderJob, norm: Sequence[dict[str, Any]], outdir: str | None, render_dir: str) -> None:
    """Одиночный рендер: безголовый .jsx (очередь+save+quit) -> verify_jsx ->
    AfterFX -noui -r (собрать .aep) -> aerender -project (рендер). Это же путь остаётся
    для набора из ОДНОГО ролика: поведение и .jsx ровно сегодняшние."""
    try:
        from core import verify_jsx
        from core import xml2ae
        job.emit("=== Рендер AE: {count} файл(ов), папка вывода: {dir} ===",
              count=len(norm), dir=render_dir)
        total_n = len(norm)
        t_jsx_base, t_aep_base, t_rnd_base, has_stats = get_baseline_phase_durations(total_n)
        p_jsx_end, p_aep_end = calc_phase_bounds(t_jsx_base, t_aep_base, t_rnd_base)
        t_jsx_start = _time.time()
        with job.lock:
            job["stage_label"] = "сборка таймлайнов"
            job["stage_done"] = 0
            job["stage_total"] = total_n
            job["pct"] = 0.0
            if has_stats:
                job["eta"] = t_jsx_base
                job["eta_phase"] = t_jsx_base
                job["eta_total"] = t_jsx_base + t_aep_base + t_rnd_base
                job["eta_preliminary"] = True
            else:
                job["eta"] = None
                job["eta_phase"] = None
                job["eta_total"] = None
                job["eta_preliminary"] = False
        # 1) построить БЕЗГОЛОВЫЙ .jsx (с render_dir): обычная сборка даёт ручной
        #    вариант без очереди/save/quit — его в -noui прогонять нечего
        jsx_list = []
        comp_names = {}   # стем .jsx -> имя главной композиции (.mov по нему)
        for i, j in enumerate(norm):
            if job["cancel"]:
                job.emit("⏹ Остановлено")
                mark_stopped_waits(job)
                return
            stem = os.path.splitext(os.path.basename(j["xml_path"]))[0]
            with job.lock:
                job["cur"] = stem
                job["stage_done"] = i
                pct_step = (i / total_n) * p_jsx_end
                job["pct"] = max(job.get("pct") or 0.0, min(p_jsx_end, pct_step))
            item_set(job, job.lock, stem, stage="jsx")   # этап 1: сборка безголового .jsx
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
            # а фронт узнаёт об этом через JOB["insmoved"], которого у job нет.
            job.convert_inserts(j.get("inserts") or [], emit=job.emit)
            kw = {k: v for k, v in j.items() if k not in ("xml_path", "outdir")}
            job.emit("  {stem}: сборка безголового .jsx…", stem=stem)
            try:
                comp_name_out: list[str] = []
                xml2ae.to_ae_full(j["xml_path"], jp, render_dir=render_dir,
                                  emit=job.emit, cancel=lambda: job["cancel"],
                                  comp_name_out=comp_name_out, **kw)
                # .mov пишется по ИМЕНИ КОМПОЗИЦИИ (om.file = main.name), а не по стему
                # .jsx — файл 01_C0233.xml может дать композицию C0233
                comp_name = comp_name_out[0] if comp_name_out else stem
                jsx_list.append(jp)
                comp_names[stem] = comp_name
                with job.lock:
                    job["stage_done"] = i + 1
                    pct_step = ((i + 1) / total_n) * p_jsx_end
                    job["pct"] = max(job.get("pct") or 0.0, min(p_jsx_end, pct_step))
                job.emit("  -> {path}", path=jp)
            except xml2ae.Cancelled:
                job.emit("⏹ Остановлено — рендер не запускался")
                return
            except (ReelsiError, SystemExit) as e:
                # Ошибка вида «рото не посчитано…» — это SystemExit (BaseException), и
                # раньше она проскакивала мимо обоих except Exception: ролик не попадал
                # ни в failed, ни в лог, а рендер продолжался с пустым набором.
                txt = sysexit_text(e)
                job.emit("  ОШИБКА сборки: {err}", err=txt)
                item_fail(job, job.lock, stem, txt, bucket="failed")
            except ReelsiError: raise
            except Exception as e:
                job.emit("  ОШИБКА сборки: {err}", err=str(e))
                # клип, у которого не собрался даже безголовый .jsx — item_fail
                item_fail(job, job.lock, stem, str(e), bucket="failed")
        if not jsx_list:
            job.emit("Ничего не собралось — рендер не запускался")
            return
        jsx_dur = _time.time() - t_jsx_start
        # 2) предполётная проверка — ДО AE. Пропажи: не рендерим вовсе.
        with job.lock:
            job["stage_label"] = "проверка файлов"
            job["pct"] = max(job.get("pct") or 0.0, p_jsx_end)
        bad = []
        for jp in jsx_list:
            stem = os.path.splitext(os.path.basename(jp))[0]
            item_set(job, job.lock, stem, stage="check")   # этап 2: предполётная проверка
            rep = verify_jsx.verify(jp)
            for e in rep.errors:  # type: ignore[misc]  # variable e reused outside except block
                bad.append((stem, e))
        if bad:
            job.emit("Предполётная проверка НЕ пройдена — рендер не запущен:")
            for name, e in bad:  # type: ignore[misc]  # variable e reused outside except block
                job.emit("  ✗ {name}: {err}", name=name, err=str(e))  # type: ignore[misc]  # variable e reused outside except block
            # это ПОКЛИПОВЫЕ падения (конкретные .jsx), не глобальные — item_fail
            for name, e in bad:  # type: ignore[misc]  # variable e reused outside except block
                item_fail(job, job.lock, name, e, bucket="failed")  # type: ignore[misc]  # variable e reused outside except block
            return
        job.emit("Проверка .jsx пройдена — файлов на диске хватает, запускаю AE")
        # 3) найти AE (самый свежий)
        ae = find_ae()
        if not ae:
            job.emit("After Effects не найден (искал в «C:\\Program Files\\Adobe\\Adobe After Effects *»). "
                  "Проверь установку и запусти снова.")
            # ГЛОБАЛЬНОЕ падение БЕЗ конкретного клипа (AE не найден для всего набора) —
            # прямой записью в failed, а не item_fail: в items писать нечего.
            with job.lock:
                job["failed"].append({"name": "AE", "reason": "After Effects не найден"})
            return
        afx, aer, aename = ae
        with job.lock:
            job["ae"] = aename
            job["out_dir"] = render_dir
            job["stage_label"] = "сборка проекта"
            job["stage_done"] = 0
            job["stage_total"] = len(jsx_list)
            job["pct"] = max(job.get("pct") or 0.0, p_jsx_end)
            if has_stats:
                job["eta"] = t_aep_base
                job["eta_phase"] = t_aep_base
                job["eta_total"] = t_aep_base + t_rnd_base
                job["eta_preliminary"] = True
            else:
                job["eta"] = None
                job["eta_phase"] = None
                job["eta_total"] = None
                job["eta_preliminary"] = False
        job.emit("AE: {name}", name=aename)
        job.emit("  AfterFX: {path}", path=afx)
        job.emit("  aerender: {path}", path=aer)
        t_aep_start = _time.time()
        aep_dur = 0.0
        render_dur = 0.0
        # 4) цепочка на каждый .jsx
        for i, jp in enumerate(jsx_list):
            if job["cancel"]:
                job.emit("⏹ Остановлено")
                mark_stopped_waits(job)
                break
            stem = os.path.splitext(os.path.basename(jp))[0]
            aep = re.sub(r"\.jsx$", ".aep", jp, flags=re.I)
            aelog = re.sub(r"\.aep$", ".aelog.txt", aep)
            jsx_call, why = jsx_call_path(jp)     # короткое имя: пробел рвёт -r
            if why:
                job.emit("  короткое имя для {name} не вышло (том без 8.3-имён) — выполняю копию {alt_name}",
                      name=os.path.basename(jp), alt_name=os.path.basename(jsx_call))
            job.emit("--- {stem}: сборка проекта (AfterFX -noui) ---", stem=stem)
            with job.lock:
                job["cur"] = stem
                job["stage_done"] = i
                pct_step = p_jsx_end + (i / len(jsx_list)) * (p_aep_end - p_jsx_end)
                job["pct"] = max(job.get("pct") or 0.0, min(p_aep_end, pct_step))
            item_set(job, job.lock, stem, stage="aep")      # этап 3: AfterFX -noui собирает .aep
            # Открытая копия AE перехватывает наш AfterFX -noui: он
            # возвращает код 0 за 0 секунд, а скрипт уезжает в НЕЁ — переписывает её
            # проект и закрывает без вопроса о сохранении. Такой запуск — стоп ДО старта.
            if ae_running():
                job.emit("  After Effects ОТКРЫТ — прогон этого ролика не начинаю. Закрой "
                      "After Effects и запусти рендер снова: иначе скрипт уйдёт в открытую "
                      "копию, перепишет её проект и закроет её.")
                item_fail(job, job.lock, stem,
                          "After Effects открыт — закрой его и запусти рендер снова",
                          bucket="failed")
                continue
            # Гигиена журнала: .aelog.txt прошлого прогона удаляем
            # ДО запуска AfterFX, иначе «нет файла = скрипт не запустился» снова врёт.
            # Не смогли удалить (файл занят) — ошибка прогона с текстом, не продолжение.
            rm_err = remove_stale_aelog(aelog)
            if rm_err:
                job.emit("  не удалить старый журнал {log}: {err} — файл занят? Рендер пропущен.",
                      log=aelog, err=rm_err)
                item_fail(job, job.lock, stem,
                          "не удалить старый .aelog.txt (файл занят?)", bucket="failed")
                continue
            # .aep НЕ стираем (человек мог доработать его руками) — запоминаем время
            # изменения ДО запуска, после прогона проверяем «обновлён этим прогоном»,
            # а не «существует».
            aep_mtime = os.path.getmtime(aep) if os.path.isfile(aep) else None
            t_afx = _time.time()
            rc = run_proc_afx(job, [afx, "-noui", "-r", jsx_call])
            afx_sec = _time.time() - t_afx
            if job["cancel"]:
                break
            if rc == AE_STALLED:
                # сторож снял зависший AfterFX — сообщение уже в логе, ролик помечаем здесь
                item_fail(job, job.lock, stem,
                          "After Effects не отвечает %d минут — прогон снят"
                          % (AE_STALL_KILL_SEC // 60), bucket="failed")
                continue
            if rc != 0:
                job.emit("  AfterFX завершился с кодом {code} — смотри вывод выше", code=rc)
            # Признаков два: нет .aelog.txt -> скрипт НЕ запустился (путь,
            # пробелы); лог есть, но .aep не обновлён этим прогоном -> скрипт шёл и
            # споткнулся, в логе видно где. Один признак (наличие .aep) путал «не
            # запустился» с «упал на середине».
            if not os.path.isfile(aelog):
                if afx_sec < AE_FAST_EXIT_SEC:
                    job.emit("  AfterFX вышел мгновенно (за {sec:.1f} с) — обычно это значит, "
                          "что скрипт ушёл в УЖЕ ОТКРЫТУЮ копию After Effects, а не поднял "
                          "свой экземпляр.", sec=afx_sec)
                job.emit("  НЕТ {log} — AE не выполнил скрипт. Проверь путь к .jsx: пробел "
                      "в имени рвёт аргумент -r, и скрипт не запускается вовсе. Рендер пропущен.",
                      log=aelog)
                item_fail(job, job.lock, stem,
                          "AE не выполнил скрипт (.aelog.txt не появился)", bucket="failed")
                continue
            with open(aelog, encoding="utf-8-sig", errors="replace") as fh:
                for line in fh:
                    line = line.rstrip("\r\n")
                    if line:
                        job.emit("  [aelog] " + line)
            if not aep_updated_by_run(aep, aep_mtime):
                if os.path.isfile(aep):
                    job.emit("  {path} остался от прошлого прогона — AfterFX не пересохранил "
                          "проект этим прогоном (см. строки [aelog] выше).", path=aep)
                job.emit("  НЕТ {path} — скрипт шёл, но споткнулся (см. строки [aelog] выше). "
                      "Рендер пропущен.", path=aep)
                item_fail(job, job.lock, stem,
                          "AfterFX не сохранил .aep (смотри .aelog.txt)", bucket="failed")
                continue
            with job.lock:
                job["stage_done"] = i + 1
                pct_step = p_jsx_end + ((i + 1) / len(jsx_list)) * (p_aep_end - p_jsx_end)
                job["pct"] = max(job.get("pct") or 0.0, min(p_aep_end, pct_step))
            aep_dur = _time.time() - t_aep_start
            total = comp_frames(open(jp, encoding="utf-8-sig").read())
            with job.lock:
                job["stage_label"] = "рендер"
                job["stage_done"] = 0
                job["stage_total"] = len(jsx_list)
                job["cur"] = os.path.basename(aep)
                job["pct"] = max(job.get("pct") or 0.0, p_aep_end)
                if has_stats:
                    job["eta"] = t_rnd_base
                    job["eta_phase"] = t_rnd_base
                    job["eta_total"] = t_rnd_base
                    job["eta_preliminary"] = True
                else:
                    job["eta"] = None
                    job["eta_phase"] = None
                    job["eta_total"] = None
                    job["eta_preliminary"] = False
            job.emit("--- {stem}: рендер (aerender), кадров: {frames} ---",
                  stem=stem, frames=total)
            item_set(job, job.lock, stem, stage="render")   # этап 4: aerender рендерит
            aep_call, aep_why = aep_call_path(aep)   # кириллица в -project рвёт aerender
            if aep_why:
                job.emit("  короткое имя для {name} не вышло (том без 8.3-имён) — рендерю копию {alt_name}",
                      name=os.path.basename(aep), alt_name=os.path.basename(aep_call))
            t_rnd_start = _time.time()
            rc = run_proc(job, [aer, "-project", aep_call], total_frames=total, item_name=stem,
                           pct_base=p_aep_end, pct_span=(1.0 - p_aep_end))
            render_dur = _time.time() - t_rnd_start
            if aep_why:
                try:
                    os.remove(aep_call)   # копия служебная — мусор в рабочей папке не оставляем
                except OSError:
                    pass  # служебную копию .aep уже убрали
            if job["cancel"]:
                break
            if rc != 0:
                if rc == AE_STALLED:
                    # снял сторож простоя: сообщение в логе и пометка ролика — внутри
                    # run_proc, второй раз то же самое не пишем
                    continue
                job.emit("  aerender завершился с кодом {code} — смотри вывод выше", code=rc)
                item_fail(job, job.lock, stem, f"aerender rc={rc}", bucket="failed")
                continue
            mov = os.path.join(render_dir, comp_names.get(stem, stem) + ".mov")
            if not rendered_ok(mov):
                # aerender на «Path is not valid» вернул 0 — коду возврата не верим, «Готово»
                # только по факту файла
                job.emit("  aerender вернул 0, но файла нет — смотри вывод aerender выше")
                item_fail(job, job.lock, stem,
                          "aerender вернул 0, но файла нет — смотри вывод aerender выше",
                          bucket="failed")
                continue
            # Одно место записи «готово»: item_done и кладёт путь в result,
            # и переводит элемент в done — вторым местом их не развести (живёт
            # здесь же: набор копит список, а не перезаписывает последний).
            item_done(job, job.lock, stem, mov, bucket="result")
            with job.lock:
                job["stage_done"] = 1
                job["pct"] = 1.0
                job["eta"] = None
                job["eta_phase"] = None
                job["eta_total"] = None
            job.emit("Готово: {path}", path=mov)
            save_render_stats(1, jsx_dur, aep_dur, render_dur)
    except (ReelsiError, SystemExit) as e:
        # Глобальное падение того же рода, что Exception ниже, но текст — из umsg:
        # traceback от «сними галку рото в стиле» человеку ничего не объясняет.
        txt = sysexit_text(e)
        log.error("Рендер прерван: %s", txt)
        job.emit("ОШИБКА: {err}", err=txt)
        with job.lock:
            job["failed"].append({"name": "рендер", "reason": txt})
    except ReelsiError: raise
    except Exception:
        log.exception("Сбой процесса рендера")
        import traceback
        job.emit("ОШИБКА:\n{tb}", tb=traceback.format_exc().strip().splitlines()[-1])
        # ГЛОБАЛЬНОЕ падение (внутренняя ошибка рендера) БЕЗ конкретного клипа — прямой
        # записью в failed, а не item_fail: связано ни с одним файлом набора.
        with job.lock:
            job["failed"].append({"name": "рендер", "reason": "внутренняя ошибка"})
    finally:
        with job.lock:
            job.update(running=False, done=True, cur="",
                        pct=(1.0 if job["result"] and not job["failed"] else (job["pct"] or 0)))


def run_render_combined(job: RenderJob, batch: Sequence[dict[str, Any]], outdir: str | None, render_dir: str) -> None:
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
    total_n = len(batch)
    t_jsx_base, t_aep_base, t_rnd_base, has_stats = get_baseline_phase_durations(total_n)
    p_jsx_end, p_aep_end = calc_phase_bounds(t_jsx_base, t_aep_base, t_rnd_base)
    with job.lock:
        job["stage_label"] = "сборка таймлайнов"
        job["stage_done"] = 0
        job["stage_total"] = total_n
        job["pct"] = 0.0
        if has_stats:
            job["eta"] = t_jsx_base
            job["eta_phase"] = t_jsx_base
            job["eta_total"] = t_jsx_base + t_aep_base + t_rnd_base
            job["eta_preliminary"] = True
        else:
            job["eta"] = None
            job["eta_phase"] = None
            job["eta_total"] = None
            job["eta_preliminary"] = False
    # Вставки конвертим ДО сборки (как _run_build_job): иначе
    # webp-вставка дошла бы до AE и уронила предполёт.
    for j in batch:
        job.convert_inserts(j.get("inserts") or [], emit=job.emit)
    stems = [os.path.splitext(os.path.basename(j["xml_path"]))[0] for j in batch]
    # Папка набора — та же, где в режиме «Отдельные .jsx» лежали бы клиповые .jsx
    od = outdir or os.path.dirname(batch[0]["xml_path"])
    os.makedirs(od, exist_ok=True)
    combined_jp = os.path.abspath(os.path.join(od, "Reelsi_all.jsx"))
    # outdir — параметр СБОРКИ, а не плана сцены: build_combined отдаёт словарь в
    # to_ae_full(**kw) -> scene_plan, и лишний ключ ронял бы общий .jsx (см. api/build.py)
    jobs = [{k: v for k, v in j.items() if k != "outdir"} for j in batch]
    comp_names_out: list[str] = []

    def _combined_progress(done: int, total: int) -> None:
        # build_combined зовёт progress ПЕРЕД сборкой done-го таймлайна — показываем
        # «собрано done-1», чтобы счётчик не забегал вперёд
        with job.lock:
            job["stage_done"] = max(0, done - 1)
            job["stage_total"] = total
            frac = (max(0, done - 1) / float(total)) * p_jsx_end
            job["pct"] = max(job.get("pct") or 0.0, min(p_jsx_end, frac))
        if 1 <= done <= total:
            item_set(job, job.lock, stems[done - 1], stage="jsx")

    t_jsx_start = _time.time()
    try:
        path, n = xml2ae.build_combined(jobs, combined_jp, emit=job.emit,
                                        cancel=lambda: job["cancel"],
                                        progress=_combined_progress,
                                        comps_global=True,
                                        comp_names_out=comp_names_out)
    except xml2ae.Cancelled:
        job.emit("⏹ Остановлено — рендер не запускался")
        return
    except (ReelsiError, SystemExit) as e:
        # Падение общее, а не поклиповое (.jsx один на весь набор), поэтому метим failed
        # КАЖДЫЙ ролик — с понятным текстом из umsg вместо str(e).
        txt = sysexit_text(e)
        job.emit("  ОШИБКА сборки: {err}", err=txt)
        for stem in stems:
            item_fail(job, job.lock, stem, txt, bucket="failed")
        return
    except ReelsiError: raise
    except Exception as e:
        job.emit("  ОШИБКА сборки: {err}", err=str(e))
        for stem in stems:
            item_fail(job, job.lock, stem, str(e), bucket="failed")
        return
    if job["cancel"]:
        job.emit("⏹ Остановлено")
        mark_stopped_waits(job)
        return
    if not comp_names_out:
        job.emit("Ничего не собралось — рендер не запускался")
        return
    jsx_dur = _time.time() - t_jsx_start
    job.emit("  -> {path}", path=combined_jp)
    with job.lock:
        job["stage_done"] = total_n
        job["stage_total"] = total_n
        job["pct"] = max(job.get("pct") or 0.0, p_jsx_end)
        job["eta"] = None
        job["eta_phase"] = None
        job["eta_total"] = None
    # 2) предполётная проверка — ОДИН раз по общему файлу (verify_jsx разбирает
    # «один .jsx на всё» по таймлайнам). Непрошедший ролик отсеять нельзя: ошибки
    # останавливают набор ЦЕЛИКОМ, и это сказано в логе прямо.
    with job.lock:
        job["cur"] = ""
        job["stage_label"] = "проверка файлов"
    for stem in stems:
        item_set(job, job.lock, stem, stage="check")
    rep = verify_jsx.verify(combined_jp)
    errs = [str(e) for e in rep.errors]
    if errs:
        job.emit("Предполётная проверка НЕ пройдена — рендер не запущен:")
        for e in errs:  # type: ignore[misc]  # variable e reused outside except block
            job.emit("  ✗ {err}", err=e)  # type: ignore[misc]  # variable e reused outside except block
        job.emit("Режим «Один на всё»: непрошедший предполёт ролик снимает ВЕСЬ набор — "
              ".jsx один на всех роликов. Исправь файл или убери ролик из набора и запусти снова.")
        for stem in stems:
            item_fail(job, job.lock, stem, errs[0], bucket="failed")
        return
    job.emit("Проверка .jsx пройдена — файлов на диске хватает, запускаю AE")
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
    job.emit("Мастер-скрипт: {path} ({n} роликов)", path=master_path, n=total_n)
    # 4) найти AE
    ae = find_ae()
    if not ae:
        job.emit("After Effects не найден (искал в «C:\\Program Files\\Adobe\\Adobe After Effects *»). "
              "Проверь установку и запусти снова.")
        with job.lock:
            job["failed"].append({"name": "AE", "reason": "After Effects не найден"})
        return
    afx, aer, aename = ae
    with job.lock:
        job["ae"] = aename
        job["out_dir"] = render_dir
        job["stage_label"] = "сборка проекта"
        job["stage_done"] = 0
        job["stage_total"] = total_n  # прогресс по таймлайнам набора
        job["pct"] = max(job.get("pct") or 0.0, p_jsx_end)
        if has_stats:
            job["eta"] = t_aep_base
            job["eta_phase"] = t_aep_base
            job["eta_total"] = t_aep_base + t_rnd_base
            job["eta_preliminary"] = True
        else:
            job["eta"] = None
            job["eta_phase"] = None
            job["eta_total"] = None
            job["eta_preliminary"] = False
        job["cur"] = ""
    job.emit("AE: {name}", name=aename)
    # 5) один AfterFX -noui -r мастера -> один .aep
    master_call, why = jsx_call_path(master_path)
    if why:
        job.emit("  короткое имя для {name} не вышло (том без 8.3-имён) — выполняю копию {alt_name}",
              name=os.path.basename(master_path), alt_name=os.path.basename(master_call))
    job.emit("--- сборка общего проекта (AfterFX -noui, мастер) ---")
    aelog = re.sub(r"\.aep$", ".aelog.txt", aep_path)
    # Открытая копия AE перехватывает наш AfterFX -noui — стоп
    if ae_running():
        job.emit("After Effects ОТКРЫТ — прогон набора не начинаю. Закрой After Effects и "
              "запусти рендер снова: иначе мастер уйдёт в открытую копию, перепишет её "
              "проект и закроет её без вопроса о сохранении.")
        for stem, _cn, _jp, _fr in good:
            item_fail(job, job.lock, stem,
                      "After Effects открыт — закрой его и запусти рендер снова",
                      bucket="failed")
        return
    # Гигиена журнала: .aelog.txt прошлого прогона удаляем ДО
    rm_err = remove_stale_aelog(aelog)
    if rm_err:
        job.emit("не удалить старый журнал {log}: {err} — файл занят? Прогон остановлен.",
              log=aelog, err=rm_err)
        for stem, _cn, _jp, _fr in good:
            item_fail(job, job.lock, stem,
                      "не удалить старый .aelog.txt (файл занят?)", bucket="failed")
        return
    # Стадии на время работы AfterFX: первый ролик собирается (aep),
    # остальные ждут очереди (wait); по мере готовности таймлайнов tail_master_log
    # переводит собранные в built, текущий в aep, остальные оставляет в wait.
    for i, (stem, _cn, _jp, _fr) in enumerate(good):
        item_set(job, job.lock, stem, stage="aep" if i == 0 else "wait")
    with job.lock:
        job["cur"] = good[0][0] if good else ""
    # .aep НЕ стираем (человек мог доработать его руками) — время ДО запуска мастера
    aep_mtime = os.path.getmtime(aep_path) if os.path.isfile(aep_path) else None
    t_aep_start = _time.time()
    rc = run_proc_master(job, afx, "-noui", "-r", master_call,
                          good=good, render_dir=render_dir, aelog_path=aelog,
                          p_jsx_end=p_jsx_end, p_aep_end=p_aep_end,
                          t_aep_base=t_aep_base, t_render_base=t_rnd_base,
                          has_stats=has_stats, whole_file=True)
    aep_dur = _time.time() - t_aep_start
    if job["cancel"]:
        return
    if not os.path.isfile(aelog):
        if aep_dur < AE_FAST_EXIT_SEC:
            job.emit("AfterFX вышел мгновенно (за {sec:.1f} с) — обычно это значит, что "
                  "мастер-скрипт ушёл в УЖЕ ОТКРЫТУЮ копию After Effects, а не поднял "
                  "свой экземпляр.", sec=aep_dur)
        job.emit("  НЕТ {log} — AE не выполнил мастер-скрипт. Проверь путь: пробел в имени рвёт "
              "аргумент -r, и скрипт не запускается вовсе. Рендер пропущен.", log=aelog)
        for stem, _cn, _jp, _fr in good:
            item_fail(job, job.lock, stem,
                      "AE не выполнил мастер-скрипт (.aelog.txt не появился)", bucket="failed")
        return
    with open(aelog, encoding="utf-8-sig", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\r\n")
            if line:
                job.emit("  [aelog] " + line)
    if rc != 0:
        job.emit("  AfterFX завершился с кодом {code} — смотри вывод выше", code=rc)
    if not aep_updated_by_run(aep_path, aep_mtime):
        if os.path.isfile(aep_path):
            job.emit("  {path} остался от прошлого прогона — мастер не пересохранил проект "
                  "этим прогоном (см. строки [aelog] выше).", path=aep_path)
        job.emit("  НЕТ {path} — мастер шёл, но споткнулся (см. строки [aelog] выше). "
              "Рендер пропущен.", path=aep_path)
        for stem, _cn, _jp, _fr in good:
            item_fail(job, job.lock, stem,
                      "AfterFX не сохранил .aep (смотри .aelog.txt)", bucket="failed")
        return
    # 6) один aerender -project: рендерит всю очередь; по мере Finished composition —
    #    соответствующий элемент в done по ИМЕНИ КОМПОЗИЦИИ
    with job.lock:
        job["cur"] = ""
        job["stage_label"] = "рендер"
        job["stage_done"] = 0
        job["stage_total"] = len(good)
        job["pct"] = max(job.get("pct") or 0.0, p_aep_end)
        for it in job.get("items", []):
            if it.get("stage") != "error":
                it["stage"] = "wait"
    job.emit("--- рендер набора (aerender -project), композиций: {n} ---", n=len(good))
    aep_call, aep_why = aep_call_path(aep_path)
    if aep_why:
        job.emit("  короткое имя для {name} не вышло (том без 8.3-имён) — рендерю копию {alt_name}",
              name=os.path.basename(aep_path), alt_name=os.path.basename(aep_call))
    t_rnd_start = _time.time()
    rc = run_proc_batch(job, aer, aep_call, [(s, cn, fr) for s, cn, _j, fr in good], render_dir,
                         p_aep_end=p_aep_end, t_render_base=t_rnd_base, has_stats=has_stats)
    render_dur = _time.time() - t_rnd_start
    if aep_why:
        try:
            os.remove(aep_call)
        except OSError:
            pass  # служебную копию .aep уже убрали
    if job["cancel"]:
        mark_stopped_waits(job)
        return
    if job["result"] and not job["failed"]:
        save_render_stats(len(good), jsx_dur, aep_dur, render_dur)


def run_proc_master(job: RenderJob, afx: str, *args: Any, good: Sequence[Any], render_dir: str, aelog_path: str,
                     p_jsx_end: float = PHASE_JSX_END, p_aep_end: float = PHASE_AEP_END,
                     t_aep_base: float = DEFAULT_AEP_SEC, t_render_base: float = DEFAULT_RENDER_SEC,
                     has_stats: bool = False, whole_file: bool = False) -> int:
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
        job.emit("не найден исполняемый файл: {err}", err=str(e))
        return -1
    with job.lock:
        job.set_proc(p)
        job["stage_label"] = "сборка проекта"
        job["stage_done"] = 0
        job["stage_total"] = len(good)
        job["pct"] = max(job.get("pct") or 0.0, p_jsx_end)
        if has_stats:
            job["eta"] = t_aep_base
            job["eta_phase"] = t_aep_base
            job["eta_total"] = t_aep_base + t_render_base
            job["eta_preliminary"] = True
        else:
            job["eta"] = None
            job["eta_phase"] = None
            job["eta_total"] = None
            job["eta_preliminary"] = False
    if job["cancel"]:
        kill_proc(p)
    # стем -> имя композиции и путь к его .jsx (для лога)
    by_jsx = {}
    for stem, _cn, jp, _fr in good:
        by_jsx[os.path.abspath(jp).replace("\\", "/")] = stem
    aelog = aelog_path
    timelines = {"stems": [stem for stem, _cn, _jp, _fr in good], "built": 0} if whole_file else None
    seen: set[str] = set()
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
    q = pump_stdout(p)
    try:
        while True:
            if p.poll() is not None:
                break
            now = _time.time()
            seen_n = len(seen)
            new_n = tail_master_log(job, aelog, seen, by_jsx, timelines=timelines)
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
                    job.emit(line)
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

                with job.lock:
                    job["stage_done"] = built_n
                    job["pct"] = max(job.get("pct") or 0.0, min(p_aep_end, pct_calc))
                    if built_n < len(good):
                        rem_phase = sum(weights[built_n:])
                        rem_total = rem_phase + t_render_base
                        if (now - t_build_start) >= 15.0 and len(built_times) >= 2:
                            job["eta"] = rem_phase
                            job["eta_phase"] = rem_phase
                            job["eta_total"] = rem_total
                            job["eta_preliminary"] = False
                            job["stage_label"] = "сборка проекта"
                        elif has_stats:
                            job["eta"] = rem_phase
                            job["eta_phase"] = rem_phase
                            job["eta_total"] = rem_total
                            job["eta_preliminary"] = True
                            job["stage_label"] = "сборка проекта"
                        else:
                            job["eta"] = None
                            job["eta_phase"] = None
                            job["eta_total"] = None
                            job["eta_preliminary"] = False
            # Сторож простоя: молчание дольше AE_STALL_KILL_SEC — снять процесс и
            # завершить прогон честной ошибкой, а не висеть вечно.
            if now - last_activity >= AE_STALL_KILL_SEC:
                job.emit("⚠ After Effects не отвечает {min} минут — прогон снят (сторож простоя)",
                      min=int(AE_STALL_KILL_SEC // 60))
                kill_proc(p)
                stalled = True
                break
            if not warn_sent and now - last_activity >= AE_STALL_WARN_SEC:
                warn_sent = True
                job.emit("⚠ After Effects молчит {min} минут — обычно сборка проекта столько "
                      "не длится; сниму процесс через {kill} минут простоя",
                      min=int(AE_STALL_WARN_SEC // 60), kill=int(AE_STALL_KILL_SEC // 60))
            if job["cancel"]:
                kill_proc(p)
                break
            _time.sleep(0.5)
        tail_master_log(job, aelog, seen, by_jsx, timelines=timelines)
    finally:
        rc = p.wait()
        while True:
            try:
                ln = q.get_nowait()
            except queue.Empty:
                break
            if ln is not None and ln.strip():
                job.emit(ln.rstrip("\r\n"))
        with job.lock:
            if job.proc is p:
                job.set_proc(None)
    with job.lock:
        job["cur"] = ""
        job["eta"] = None      # сборка кончилась — оценку гасим
        job["eta_phase"] = None
        job["eta_total"] = None
        job["stage_done"] = len(good)
        job["pct"] = max(job.get("pct") or 0.0, p_aep_end)
    # ролики, до которых мастер не дошёл (evalFile не случился) — item_fail
    fail_reason = ("мастер не выполнил evalFile этого ролика" if not stalled else
                   "After Effects не отвечает %d минут — прогон снят" % (AE_STALL_KILL_SEC // 60))
    for stem, _cn, jp, _fr in good:
        if os.path.abspath(jp).replace("\\", "/") not in seen:
            item_fail(job, job.lock, stem, fail_reason, bucket="failed")
    return rc


def tail_master_log(job: RenderJob, aelog: str, seen: set[str], by_jsx: dict[str, str], timelines: dict[str, Any] | None = None) -> int:
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
                    item_set(job, job.lock, stem, stage="built")
                elif idx == b:
                    item_set(job, job.lock, stem, stage="aep")
                else:
                    item_set(job, job.lock, stem, stage="wait")
            with job.lock:
                if b < len(stems):
                    job["cur"] = stems[b]
                else:
                    job["cur"] = ""
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
                    item_set(job, job.lock, stem, stage="aep")
                    with job.lock:
                        job["cur"] = stem
                    job.emit("собран: {stem}", stem=stem)
        elif "evalFile ОШИБКА: " in line:
            path = line.split("evalFile ОШИБКА: ", 1)[1].split(" — ")[0].strip()
            stem = by_jsx.get(path.replace("\\", "/"))
            if stem:
                item_fail(job, job.lock, stem,
                          "мастер не собрал ролик (evalFile ОШИБКА, см. лог мастера)",
                          bucket="failed")
        elif line.startswith("REELSI-MASTER") or line.startswith("comp ok: ") or line.startswith("таймлайн ok: "):
            job.emit("  [aelog] " + line)
    return new_tl if timelines is not None else new_ok


def run_proc_batch(job: RenderJob, aer: str, aep_call: str, comps: Sequence[Any], render_dir: str,
                    p_aep_end: float = PHASE_AEP_END, t_render_base: float = DEFAULT_RENDER_SEC,
                    has_stats: bool = False) -> int:
    """aerender для общего проекта: ДВА живых прогресса. comps —
    [(стем, имя_композиции, кадры), …]: стем адресует строку очереди, имя композиции —
    файл на диске, кадры — M для процента текущей композиции, когда aerender его
    не печатает, и для ETA.
    - имя текущей композиции: из строки «Output To:», запасной — по порядку;
    - кадры текущей композиции: из блока Start/End/Duration/Frame Rate,
      запасной — comp_frames из .jsx;
    - pct элемента: доля ТЕКУЩЕЙ композиции (parse_progress, кадры (N)/(N/M));
    - job["pct"]: общий по набору = (готовых + доля текущей) / всего — монотонно,
      без прыжка к 1.0 на «Finished composition» (это конец ОДНОЙ, а не всего);
    - job["cur"]: имя текущей композиции;
    - job["eta"]: ETA рендера в секундах (скользящая скорость за 30-60 с);
    - сторож простоя — те же константы и та же схема, что у одиночного `run_proc`:
    молчащий aerender снимается, причина идёт в лог и в failed."""
    try:
        p = subprocess.Popen([aer, "-project", aep_call],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                             **task_popen_kwargs())
    except FileNotFoundError as e:
        job.emit("не найден исполняемый файл: {err}", err=str(e))
        return -1
    with job.lock:
        job.set_proc(p)
        job["stage_label"] = "рендер"
        job["stage_done"] = 0
        job["stage_total"] = len(comps)
        job["pct"] = max(job.get("pct") or 0.0, p_aep_end)
        if has_stats:
            job["eta"] = t_render_base
            job["eta_phase"] = t_render_base
            job["eta_total"] = t_render_base
            job["eta_preliminary"] = True
        else:
            job["eta"] = None
            job["eta_phase"] = None
            job["eta_total"] = None
            job["eta_preliminary"] = False
    if job["cancel"]:
        kill_proc(p)
    done_names = []          # стемы, чей .mov уже готов
    next_idx = 0             # индекс следующей композиции по порядку очереди
    cur_stem = None          # стем текущей композиции
    comp_frames = {s: fr for s, _cn, fr in comps}  # стем -> кадры (запасные из .jsx)
    total_frames = sum(comp_frames.values()) or 0
    done_frames = 0          # кадры уже готовых композиций
    hdr_dur, hdr_fps, hdr_start, hdr_end = None, None, None, None
    t_start = _time.time()
    samples = []             # (сек от старта, суммарно отрендеренных кадров)
    # Вывод читает ФОНОВЫЙ поток (`pump_stdout`), а главный цикл сторожит простой
    # Пока главный поток сидел в `for line in p.stdout`, молчащий
    # aerender — окно ошибки, зависший плагин, недоступный сетевой диск — держал джоб
    # «рендер идёт» неограниченно долго: сторож был только у одиночного `run_proc`,
    # хотя CHANGELOG обещает его и `aerender` на наборе.
    q = pump_stdout(p)
    last_activity = _time.time()
    warned = False
    stalled = False

    def _handle(line: str) -> None:
        """Разбор одной строки aerender — ровно прежнее тело цикла. Обработчик ОДИН и
        на живой цикл, и на добирание хвоста после выхода/снятия процесса."""
        nonlocal total_frames, done_frames, cur_stem, next_idx, last_activity
        nonlocal hdr_dur, hdr_fps, hdr_start, hdr_end
        line = line.rstrip("\r\n")
        if not line.strip():
            return
        last_activity = _time.time()
        job.emit(line)

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
                with job.lock:
                    job["cur"] = comp_name
                item_set(job, job.lock, cur_stem, stage="render")
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
                item_done(job, job.lock, stem, mov, bucket="result")
                item_set(job, job.lock, stem, pct=1.0)
                job.emit("Готово: {path}", path=mov)
                # общий прогресс: готовых (включая только что) / всего — монотонно в диапазоне фазы 3
                with job.lock:
                    job["stage_done"] = len(done_names)
                    job["stage_total"] = len(comps)
                    render_frac = min(1.0, len(done_names) / float(len(comps)))
                    pct_step = p_aep_end + render_frac * (1.0 - p_aep_end)
                    job["pct"] = max(job.get("pct") or 0.0, min(1.0, pct_step))
                    job["stage_label"] = "рендер"
                    job["cur"] = ""
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
                with job.lock:
                    job["cur"] = comp_name
                item_set(job, job.lock, cur_stem, stage="render")

        if cur_stem:
            if ae_frames and comp_frames.get(cur_stem) != ae_frames:
                comp_frames[cur_stem] = ae_frames
                total_frames = sum(comp_frames.values()) or 0

            cur_frames = comp_frames.get(cur_stem)
            comp_name = next((_cn for _s, _cn, _fr in comps if _s == cur_stem), cur_stem)
            pr = parse_progress(line, cur_frames)
            if pr is not None:
                item_set(job, job.lock, cur_stem, pct=pr)
                with job.lock:
                    job["cur"] = comp_name
                    job["stage_done"] = len(done_names)
                    job["stage_total"] = len(comps)
                    render_frac = min(1.0, (len(done_names) + pr) / float(len(comps)))
                    pct_step = p_aep_end + render_frac * (1.0 - p_aep_end)
                    job["pct"] = max(job.get("pct") or 0.0, min(1.0, pct_step))
                    job["stage_label"] = "рендер"
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
                                job["eta"] = eta_calc
                                job["eta_phase"] = eta_calc
                                job["eta_total"] = eta_calc
                                job["eta_preliminary"] = False
                        elif has_stats:
                            rem_frac = max(0.0, 1.0 - render_frac)
                            job["eta"] = rem_frac * t_render_base
                            job["eta_phase"] = rem_frac * t_render_base
                            job["eta_total"] = rem_frac * t_render_base
                            job["eta_preliminary"] = True

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
                job.emit("⚠ aerender не отвечает {min} минут — прогон снят (сторож простоя)",
                      min=int(AE_STALL_KILL_SEC // 60))
                kill_proc(p)
                stalled = True
                break
            if not warned and now - last_activity >= AE_STALL_WARN_SEC:
                warned = True
                job.emit("⚠ aerender молчит {min} минут — обычно рендер столько "
                      "не длится; сниму процесс через {kill} минут простоя",
                      min=int(AE_STALL_WARN_SEC // 60), kill=int(AE_STALL_KILL_SEC // 60))
            if job["cancel"]:
                kill_proc(p)
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
        with job.lock:
            if job.proc is p:
                job.set_proc(None)
    with job.lock:
        job["eta"] = None      # рендер кончился — оценку гасим
        job["eta_phase"] = None
        job["eta_total"] = None
        job["stage_done"] = len(done_names)
        if done_names and len(done_names) == len(comps):
            job["pct"] = 1.0
    # композиции, чей .mov так и не появился — item_fail (aerender мог споткнуться)
    stall_reason = ("aerender не отвечает %d минут — прогон снят (сторож простоя)"
                    % (AE_STALL_KILL_SEC // 60))
    for stem, comp_name, _fr in comps:
        if stem not in done_names:
            mov = os.path.join(render_dir, comp_name + ".mov")
            if rendered_ok(mov):
                item_done(job, job.lock, stem, mov, bucket="result")
            else:
                item_fail(job, job.lock, stem,
                          stall_reason if stalled
                          else "aerender не отрендерил (см. вывод aerender выше)",
                          bucket="failed")
    return AE_STALLED if stalled else rc


def run_render_job(job: RenderJob, norm: Sequence[dict[str, Any]], outdir: str | None, render_dir: str) -> None:
    """Диспетчер рендера. Набор из ОДНОГО ролика — ровно прежний путь
    (_run_render_single: безголовый .jsx, AfterFX, aerender). Набор из нескольких —
    ВСЕГДА _run_render_combined: один проект AE и один общий Reelsi_all.jsx
    (решение пользователя 2026-09-11; радио multimode на рендер не влияет)."""
    try:
        if not norm:
            job.emit("Набор пуст")
            return
        items_init(job, job.lock,
                   [os.path.splitext(os.path.basename(j["xml_path"]))[0] for j in norm])
        if len(norm) <= 1:
            run_render_single(job, norm, outdir, render_dir)
            return
        job.emit("=== Рендер AE: {count} файл(ов) в общий проект, папка вывода: {dir} ===",
              count=len(norm), dir=render_dir)
        if job["cancel"]:
            mark_stopped_waits(job)
            return
        run_render_combined(job, norm, outdir, render_dir)
    except (ReelsiError, SystemExit) as e:
        # Последний рубеж диспетчера: тот же путь провала, что у Exception ниже, но в
        # лог и job["failed"] уходит понятный текст из umsg.
        txt = sysexit_text(e)
        log.error("Рендер прерван: %s", txt)
        job.emit("ОШИБКА: {err}", err=txt)
        with job.lock:
            job["failed"].append({"name": "рендер", "reason": txt})
    except ReelsiError: raise
    except Exception:
        log.exception("Сбой в потоке рендера")
        import traceback
        job.emit("ОШИБКА:\n{tb}", tb=traceback.format_exc().strip().splitlines()[-1])
        with job.lock:
            job["failed"].append({"name": "рендер", "reason": "внутренняя ошибка"})
    finally:
        with job.lock:
            job.update(running=False, done=True, cur="",
                        pct=(1.0 if job["result"] and not job["failed"] else (job["pct"] or 0)))
        # Журнал заданий: рендер закрыт. Раньше состояние жило только в
        # памяти, и после перезапуска сервера оборванный рендер выглядел как «не было».
        journal_finish(job)
        # Лок задач держим до самого конца рендера — включая «Стоп» и ошибку
        # (без него поверх рендера стартовала нарезка).
        _cross_lock_release()
