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

Разбор тела запроса рендера тоже здесь — `prepare_render_task` (выбор движка, папка
вывода, адрес сервера, заголовок задания): роут принимает запрос и отдаёт ответ, а
«чем рендерить и куда» — знание о рендере, а не о HTTP.

Лог рендера пишется функцией держателя (`job.emit`). В бою её подставляет
`api/render.py` своим `remit`: `core.app_meta.wrap_emit` пропускает колбэк как есть
ТОЛЬКО если его модуль начинается на «api.» — с writer'ом из ядра структурная
запись {"t", "v"} потерялась бы, а по ней фронт переводит строку.
"""
from __future__ import annotations
import copy, os, queue, re, shutil, subprocess, threading
import time as _time
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from core.aerender import (AE_FAST_EXIT_SEC, AE_STALL_KILL_SEC, AE_STALL_WARN_SEC, AE_STALLED,
                           AERENDER_MEM_USAGE,
                           DEFAULT_AEP_SEC, DEFAULT_RENDER_SEC, ETA_WINDOW, PHASE_AEP_END,
                           PHASE_JSX_END, TC_DUR, TC_END, TC_START, aep_call_path,
                           aep_updated_by_run, ae_running, calc_phase_bounds, comp_frames,
                           comp_to_stem, default_render_dir, eta_secs, find_ae,
                           finished_comp_name, get_baseline_phase_durations, jsx_call_path,
                           parse_frame_rate, parse_output_to, parse_progress, parse_timecode,
                           predict_aep_times, remove_stale_aelog, rendered_ok, save_render_stats)
from core.jobstate import (item_done, item_fail, item_set, items_init, journal_finish,
                           kill_tree, log_entry, pump_stdout, sysexit_text,
                           task_popen_kwargs, _cross_lock_release)
from core.umsg import ReelsiError, umsg
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
        self.procs: list[Any] = []
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
    процессы рендера с деревом.

    Это И текущий процесс (AfterFX или aerender — одиночный путь), И весь список
    `job.procs`: у встроенного рендера без AE детей несколько (node-съёмщик со своим
    Chrome, ffmpeg куска, склейка), и регистрирует их он сам (`_child_registrar`).
    Гасим СВОИ процессы по PID — по имени убивать нельзя: на машине открыт браузер
    человека и может идти чужая сборка."""
    with job.lock:
        job["cancel"] = True
        p = job.proc
        procs = list(getattr(job, "procs", []))
    if p and p.poll() is None:
        kill_proc(p)
    for pr in procs:
        if pr and pr.poll() is None:
            kill_proc(pr)


# --------------------------------------------------------------------------- #
# Общие помощники запуска процессов
# --------------------------------------------------------------------------- #
# Четыре прогона (одиночный aerender, AfterFX -noui, мастер, набор) делают одно и
# то же: запускают процесс с той же трубой, следят за простоем, дочитывают хвост
# вывода и снимают с джоба текущий процесс. ЦИКЛЫ при этом НЕ слиты: у каждого свой
# порядок шагов (мастер сперва читает журнал и считает ETA, набор ждёт очередь с
# timeout=0.5, одиночный спит и потом дочитывает) — общими стали только куски.
def spawn_piped(job: RenderJob, cmd: Sequence[str] | list[str]) -> subprocess.Popen[str] | None:
    """Запустить процесс с объединённым выводом в трубу или вернуть None.

    None — исполняемого файла нет (FileNotFoundError): причина уже в логе, звать
    дальше нечего, вызывающий отдаёт -1. Аргументы Popen одни на все четыре прогона:
    utf-8 с replace (AE печатает в системной кодировке, падать на байтах незачем),
    CREATE_NO_WINDOW (безголовый запуск не должен мигать окном консоли) и
    task_popen_kwargs (на POSIX — своя группа процессов, её гасит «Стоп»)."""
    try:
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace",
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                **task_popen_kwargs())
    except FileNotFoundError as e:
        job.emit("не найден исполняемый файл: {err}", err=str(e))
        return None


# Тексты сторожа простоя — ЛИТЕРАЛЬНЫЕ шаблоны: строка целиком служит ключом
# перевода в static/i18n/en.json, поэтому из кусков она НЕ собирается (сборка
# развалила бы поиск ключа), а кто именно молчит — выбирается по `who`.
_STALL_NOT_RESPONDING: dict[str, str] = {
    "aerender": "⚠ aerender не отвечает {min} минут — прогон снят (сторож простоя)",
    "afx": "⚠ After Effects не отвечает {min} минут — прогон снят (сторож простоя)",
}
_STALL_SILENT: dict[str, str] = {
    "aerender": "⚠ aerender молчит {min} минут — обычно рендер столько не длится; сниму процесс через {kill} минут простоя",
    "afx": "⚠ After Effects молчит {min} минут — обычно сборка проекта столько не длится; сниму процесс через {kill} минут простоя",
}


class StallWatch:
    """Сторож простоя процесса рендера/сборки: политика в одном месте.

    Раньше три `if` (снятие по простою, предупреждение, «Стоп») были скопированы в
    каждом из четырёх циклов и могли разъехаться. Порядок проверок прежний и важен:
    сначала снятие (kill), потом предупреждение, потом отмена — предупреждение про
    молчание не должно подменять собой снятие. Тексты — из _STALL_* карт (who:
    "aerender" | "afx"), константы и kill_proc берутся из модуля на каждом вызове:
    их подменяют тесты.

    Отсчёт простоя — от последней НОВОЙ строки вывода (`touch`), а не от старта:
    тяжёлый ролик собирается и рендерится минутами."""

    def __init__(self, job: RenderJob, p: Any, who: str) -> None:
        self.job = job
        self.p = p
        self.who = who
        self.stalled = False
        self.warned = False
        self.last_activity = _time.time()

    def touch(self, now: float | None = None) -> None:
        """Была активность: строка вывода или новая строка журнала мастера."""
        self.last_activity = _time.time() if now is None else now

    def check(self, now: float) -> bool:
        """Проверить простой, предупреждение и «Стоп». True — выйти из цикла."""
        if now - self.last_activity >= AE_STALL_KILL_SEC:
            self.job.emit(_STALL_NOT_RESPONDING[self.who], min=int(AE_STALL_KILL_SEC // 60))
            kill_proc(self.p)
            self.stalled = True
            return True
        if not self.warned and now - self.last_activity >= AE_STALL_WARN_SEC:
            self.warned = True
            self.job.emit(_STALL_SILENT[self.who],
                          min=int(AE_STALL_WARN_SEC // 60), kill=int(AE_STALL_KILL_SEC // 60))
        if self.job["cancel"]:
            kill_proc(self.p)
            return True
        return False


def release_proc(job: RenderJob, p: Any) -> None:
    """Снять с джоба текущий процесс, если он всё ещё тот самый.

    Под замком и со сверкой `job.proc is p`: за время прогона процесс могли уже
    заменить («Стоп» + новый запуск), и затирать чужой хэндл нельзя."""
    with job.lock:
        if job.proc is p:
            job.set_proc(None)


def drain_emit(job: RenderJob, q: queue.Queue[str | None]) -> None:
    """Дочитать остаток очереди вывода и отдать непустые строки в лог.

    Хвост после выхода процесса: строки уже лежат в очереди насоса
    (`pump_stdout`), ждать их не нужно — берём до первой пустоты. None (EOF stdout)
    пропускаем молча."""
    while True:
        try:
            ln = q.get_nowait()
        except queue.Empty:
            break
        if ln is not None and ln.strip():
            job.emit(ln.rstrip("\r\n"))


def run_proc(job: RenderJob, cmd: Sequence[str] | list[str], total_frames: int | None = None, item_name: str | None = None, pct_base: float = 0.0, pct_span: float = 1.0) -> int:
    """Запустить процесс (aerender), стримить вывод в лог рендера со сторожем простоя.
    Возвращает код выхода или AE_STALLED. item_name — элемент очереди, которому
    дублируется доля рендера (aerender — единственный этап с честным процентом).
    pct_base и pct_span задают долю шкалы (например 0.35..1.0 на фазе рендера).
    Сторож простоя aerender: молчание AE_STALL_WARN_SEC — предупреждение,
    AE_STALL_KILL_SEC — снятие. Константы те же, что у AfterFX (5 и 20 мин): рендер даже
    тяжёлых кадров даёт строки прогресса чаще, а молчание означает зависание или окно ошибки."""
    p = spawn_piped(job, cmd)
    if p is None:
        return -1
    with job.lock:
        job.set_proc(p)
    if job["cancel"]:
        kill_proc(p)
    q = pump_stdout(p)
    watch = StallWatch(job, p, "aerender")
    hdr_dur, hdr_fps, hdr_start, hdr_end = None, None, None, None

    def _handle(raw: str) -> None:
        """Разбор одной строки aerender: лог, заголовок AE, прогресс,
        Output To. Обработчик ОДИН и на живой цикл, и на добирание хвоста после выхода
        процесса: во второй копии (только запись строк) у конца прогона терялся разбор
        «Finished composition» и «Total Time Elapsed», а по ним ставится 100% шкалы."""
        nonlocal total_frames, hdr_dur, hdr_fps, hdr_start, hdr_end
        line = raw.rstrip("\r\n")
        if not line.strip():
            return
        watch.touch()
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
            if watch.check(now):
                break
            _time.sleep(0.5)
            _drain()
    finally:
        rc = p.wait()
        _drain(tail=True)          # процесс умер — добираем и РАЗБИРАЕМ остаток stdout
        release_proc(job, p)
    if watch.stalled and item_name:
        # Причина снятия — и в логе, и в failed, ровно там, где сработал сторож: элемент
        # очереди этому вызову уже передан (item_name), второй копии текста не заводим.
        item_fail(job, job.lock, item_name,
                  "aerender не отвечает %d минут — прогон снят" % (AE_STALL_KILL_SEC // 60),
                  bucket="failed")
    return AE_STALLED if watch.stalled else rc


def run_proc_afx(job: RenderJob, cmd: Sequence[str] | list[str]) -> int:
    """AfterFX -noui -r одиночного ролика со сторожем простоя.
    Раньше здесь был `run_proc` с блокирующим чтением stdout: молчащий AfterFX вешал
    джоб навсегда. Теперь stdout читается фоном, а основной поток ждёт процесс и
    следит за активностью: процесс жив, но за AE_STALL_WARN_SEC не появилось ни одной
    новой строки stdout — предупреждение в лог; за AE_STALL_KILL_SEC — процесс снимается
    (`kill_proc`) и возвращается AE_STALLED (сообщение уже в логе). Отсчёт простоя —
    от последней НОВОЙ строки, не от старта."""
    p = spawn_piped(job, cmd)
    if p is None:
        return -1
    with job.lock:
        job.set_proc(p)
    if job["cancel"]:
        kill_proc(p)
    # Построчное чтение в фон: readline() блокирует навсегда, а сторожу нужен таймаут.
    q = pump_stdout(p)
    watch = StallWatch(job, p, "afx")
    try:
        while True:
            if p.poll() is not None:
                break
            now = _time.time()
            if watch.check(now):
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
                    watch.touch()
                    job.emit(line)
    finally:
        rc = p.wait()
        # процесс умер — добираем остаток stdout (pump уже дошёл до EOF)
        drain_emit(job, q)
        release_proc(job, p)
    return AE_STALLED if watch.stalled else rc


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
            rc = run_proc(job, [aer, "-project", aep_call,
                                "-mem_usage", str(AERENDER_MEM_USAGE[0]), str(AERENDER_MEM_USAGE[1])],
                          total_frames=total, item_name=stem,
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
    aep_mtime = os.path.getmtime(aep_path) if os.path.isfile(aep_path) else None

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

    workers = resolve_ae_build_workers(len(good))

    if workers <= 1:
        # 5) один AfterFX -noui -r мастера -> один .aep (N=1)
        master_path = os.path.join(batch_dir, "render_master.jsx")
        from core.xml2ae.build import _write_master
        _write_master([combined_jp], master_path, aep_path, render_dir)
        job.emit("Мастер-скрипт: {path} ({n} роликов)", path=master_path, n=total_n)
        master_call, why = jsx_call_path(master_path)
        if why:
            job.emit("  короткое имя для {name} не вышло (том без 8.3-имён) — выполняю копию {alt_name}",
                  name=os.path.basename(master_path), alt_name=os.path.basename(master_call))
        job.emit("--- сборка общего проекта (AfterFX -noui, мастер) ---")
        aelog = re.sub(r"\.aep$", ".aelog.txt", aep_path)
        rm_err = remove_stale_aelog(aelog)
        if rm_err:
            job.emit("не удалить старый журнал {log}: {err} — файл занят? Прогон остановлен.",
                  log=aelog, err=rm_err)
            for stem, _cn, _jp, _fr in good:
                item_fail(job, job.lock, stem,
                          "не удалить старый .aelog.txt (файл занят?)", bucket="failed")
            return
        for i, (stem, _cn, _jp, _fr) in enumerate(good):
            item_set(job, job.lock, stem, stage="aep" if i == 0 else "wait")
        with job.lock:
            job["cur"] = good[0][0] if good else ""
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
    else:
        # N>1: параллельная сборка по частям и слияние
        from core.xml2ae.build import _write_part_master, _write_merge_master
        parts_dir = os.path.join(batch_dir, "_parts")
        os.makedirs(parts_dir, exist_ok=True)
        durations = [float(fr) if fr else 100.0 for stem, cn, jp, fr in good]
        groups = partition_clips(durations, workers)
        job.emit("--- параллельная сборка проекта ({n} процессов AfterFX, {c} роликов) ---",
                 n=len(groups), c=len(good))
        parts_info: list[dict[str, Any]] = []
        for k, grp in enumerate(groups):
            part_master = os.path.join(parts_dir, f"render_master.part{k}.jsx")
            part_aep = os.path.join(parts_dir, f"reelsi_batch.part{k}.aep")
            part_aelog = os.path.join(parts_dir, f"reelsi_batch.part{k}.aelog.txt")
            remove_stale_aelog(part_aelog)
            _write_part_master(combined_jp, part_master, part_aep, part_aelog, grp)
            master_call, why = jsx_call_path(part_master)
            part_mtime = os.path.getmtime(part_aep) if os.path.isfile(part_aep) else None
            parts_info.append({
                "k": k,
                "master_path": part_master,
                "master_call": master_call,
                "aep_path": part_aep,
                "aelog_path": part_aelog,
                "aep_mtime": part_mtime,
                "indices": grp,
                "clips": [good[i] for i in grp],
            })
            for i_idx, c_idx in enumerate(grp):
                stem = good[c_idx][0]
                item_set(job, job.lock, stem, stage="aep" if i_idx == 0 else "wait")
        if good:
            with job.lock:
                job["cur"] = good[0][0]
        t_aep_start = _time.time()
        rcs = run_proc_parts(job, afx, parts_info, good=good, render_dir=render_dir,
                             p_jsx_end=p_jsx_end, p_aep_end=p_aep_end,
                             t_aep_base=t_aep_base, t_render_base=t_rnd_base,
                             has_stats=has_stats)
        aep_dur = _time.time() - t_aep_start
        if job["cancel"]:
            return

        successful_parts: list[dict[str, Any]] = []
        for part, rc in zip(parts_info, rcs):
            k = part["k"]
            part_aep = part["aep_path"]
            part_aelog = part["aelog_path"]
            part_ok = (rc == 0 and os.path.isfile(part_aelog) and
                       aep_updated_by_run(part_aep, part["aep_mtime"]))
            if part_ok:
                successful_parts.append(part)
                job.emit("  часть {k} готова ({n} роликов)", k=k, n=len(part["clips"]))
            else:
                job.emit("  ⚠ часть {k} не собралась (rc={rc}, aep={aep})",
                         k=k, rc=rc, aep=os.path.isfile(part_aep))
                for stem, _cn, _jp, _fr in part["clips"]:
                    item_fail(job, job.lock, stem,
                              f"часть {k} не собралась (AfterFX rc={rc})", bucket="failed")

        if not successful_parts:
            job.emit("Ни одна часть проекта не собралась — рендер остановлен.")
            return

        # Слияние готовых частей
        job.emit("--- слияние частей проекта ({n} частей) ---", n=len(successful_parts))
        merge_jsx = os.path.join(batch_dir, "render_merge.jsx")
        merge_aelog = os.path.join(batch_dir, "reelsi_batch.merge.aelog.txt")
        remove_stale_aelog(merge_aelog)
        successful_part_aeps = [p["aep_path"] for p in successful_parts]
        successful_good = [clip for p in successful_parts for clip in p["clips"]]
        successful_comp_names = [cn for _s, cn, _j, _fr in successful_good]
        _write_merge_master(merge_jsx, aep_path, merge_aelog, successful_part_aeps,
                            successful_comp_names, render_dir)
        merge_rc = run_proc_merge(job, afx, merge_jsx, merge_aelog, aep_path)
        if job["cancel"]:
            return
        if merge_rc != 0 or not aep_updated_by_run(aep_path, aep_mtime):
            if os.path.isfile(aep_path) and not aep_updated_by_run(aep_path, aep_mtime):
                job.emit("  {path} остался от прошлого прогона — слияние не пересохранило проект "
                         "этим прогоном (см. reelsi_batch.merge.aelog.txt).", path=aep_path)
            job.emit("  НЕТ {path} — слияние завершилось с ошибкой (rc={rc}). Рендер пропущен.",
                     path=aep_path, rc=merge_rc)
            for stem, _cn, _jp, _fr in successful_good:
                item_fail(job, job.lock, stem,
                          "слияние проектов не выполнилось (см. reelsi_batch.merge.aelog.txt)",
                          bucket="failed")
            return

        # Удачная сборка и слияние: чистим временную папку _parts/
        try:
            shutil.rmtree(parts_dir, ignore_errors=True)
        except Exception:
            pass  # временная папка _parts — сбой очистки не блокирует рендер
        good = successful_good
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

def get_free_ram_gb() -> float:
    """Свободная оперативная память машины в гигабайтах."""
    try:
        import psutil
        return float(psutil.virtual_memory().available) / (1024 ** 3)
    except Exception:
        pass  # psutil не установлен или недоступен — пробуем ctypes
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        windll = getattr(ctypes, "windll", None)
        if windll is not None and windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return float(stat.ullAvailPhys) / (1024 ** 3)
    except Exception:
        pass  # не-Windows или ошибка ctypes — откат на дефолтные 16 ГБ
    return 16.0


def resolve_ae_build_workers(n_clips: int, cfg_val: Any = None) -> int:
    """Определить число воркеров параллельной сборки AE (1..16).

    auto = min(3, n_clips, max(1, свободная RAM // 10 ГБ)).
    1 = прежний путь (один мастер-скрипт).
    """
    if n_clips <= 0:
        return 1
    if cfg_val is None:
        try:
            from core.aicut.config import ae_build_workers_cfg
            cfg_val = ae_build_workers_cfg()
        except Exception:
            cfg_val = "auto"
    val_str = str(cfg_val).strip().lower()
    if val_str == "auto":
        ram_gb = get_free_ram_gb()
        ram_workers = max(1, int(ram_gb // 10))
        return max(1, min(3, n_clips, ram_workers))
    try:
        w = int(val_str)
        return max(1, min(w, n_clips))
    except (ValueError, TypeError):
        return 1


def partition_clips(durations: Sequence[float], n_groups: int) -> list[list[int]]:
    """Разбить клипы на n_groups групп с выравниванием по длительности (LPT greedy).

    Клипы с наибольшей длительностью назначаются в группу с наименьшей текущей суммой.
    Возвращает список групп, где каждая группа содержит отсортированные 0-based индексы клипов.
    """
    if n_groups <= 1 or not durations:
        return [list(range(len(durations)))]
    n_groups = min(n_groups, len(durations))
    sorted_indices = sorted(range(len(durations)), key=lambda i: durations[i], reverse=True)
    groups: list[list[int]] = [[] for _ in range(n_groups)]
    loads: list[float] = [0.0] * n_groups
    for idx in sorted_indices:
        min_g = min(range(n_groups), key=lambda g: loads[g])
        groups[min_g].append(idx)
        loads[min_g] += float(durations[idx])
    for g in groups:
        g.sort()
    return groups


def run_proc_parts(job: RenderJob, afx: str, parts_info: list[dict[str, Any]],
                   good: Sequence[Any], render_dir: str,
                   p_jsx_end: float = PHASE_JSX_END, p_aep_end: float = PHASE_AEP_END,
                   t_aep_base: float = DEFAULT_AEP_SEC, t_render_base: float = DEFAULT_RENDER_SEC,
                   has_stats: bool = False) -> list[int]:
    """Параллельный запуск N процессов AfterFX -m -noui -r <part.jsx>.

    Adobe After Effects Command-Line Reference:
    Флаг -m ("Allows multiple instances of After Effects to run at the same time")
    запускает отдельный процесс AfterFX для каждой части набора.
    При этом проверка ae_running() остаётся в силе ДО старта воркеров:
    если пользователь уже работает в интерактивном After Effects с окном,
    запуск AfterFX может вмешаться в открытый сеанс или переписать несохранённый проект.
    """
    by_jsx: dict[str, str] = {}
    for stem, _cn, jp, _fr in good:
        by_jsx[os.path.abspath(jp).replace("\\", "/")] = stem

    for part in parts_info:
        p = spawn_piped(job, [afx, "-m", "-noui", "-r", part["master_call"]])
        part["proc"] = p
        part["q"] = pump_stdout(p) if p else None
        part["watch"] = StallWatch(job, p, "afx") if p else None
        part["seen"] = set()
        part["timelines"] = {"stems": [stem for stem, _cn, _jp, _fr in part["clips"]], "built": 0}

    with job.lock:
        job.procs = [part["proc"] for part in parts_info if part.get("proc")]
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

    t_build_start = _time.time()
    last_clip_time = t_build_start
    clip_durations: list[float] = []
    built_n = 0
    built_times: list[tuple[float, int]] = []
    default_per = (t_aep_base / len(good)) if len(good) > 0 else DEFAULT_AEP_SEC

    try:
        while True:
            alive = [part for part in parts_info if part.get("proc") and part["proc"].poll() is None]
            if not alive:
                break
            now = _time.time()
            total_new = 0
            for part in parts_info:
                p = part.get("proc")
                if not p:
                    continue
                q = part.get("q")
                watch = part.get("watch")
                seen = part["seen"]
                timelines = part["timelines"]
                aelog = part["aelog_path"]

                seen_n = len(seen)
                new_n = tail_master_log(job, aelog, seen, by_jsx, timelines=timelines)
                if len(seen) != seen_n or new_n:
                    if watch:
                        watch.touch(now)
                if new_n:
                    total_new += new_n

                if q:
                    while True:
                        try:
                            line = q.get_nowait()
                        except queue.Empty:
                            break
                        if line is None:
                            break
                        line = line.rstrip("\r\n")
                        if line.strip():
                            if watch:
                                watch.touch(now)
                            job.emit(line)

                if watch and watch.check(now):
                    pass

            if job["cancel"]:
                for part in parts_info:
                    if part.get("proc"):
                        kill_proc(part["proc"])
                break

            if total_new:
                built_n += total_new
                clip_dur = (now - last_clip_time) / float(total_new)
                for _ in range(total_new):
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
            _time.sleep(0.5)

        for part in parts_info:
            tail_master_log(job, part["aelog_path"], part["seen"], by_jsx, timelines=part["timelines"])
    finally:
        rcs = []
        for part in parts_info:
            p = part.get("proc")
            if p:
                rc = p.wait()
                rcs.append(rc)
                if part.get("q"):
                    drain_emit(job, part["q"])
            else:
                rcs.append(-1)
        with job.lock:
            job.procs = []
    return rcs


def run_proc_merge(job: RenderJob, afx: str, merge_jsx: str, merge_aelog: str, aep_path: str) -> int:
    """Запуск AfterFX -m -noui -r render_merge.jsx со сторожем простоя и разбором лога."""
    merge_call, _ = jsx_call_path(merge_jsx)
    p = spawn_piped(job, [afx, "-m", "-noui", "-r", merge_call])
    if p is None:
        return -1
    with job.lock:
        job.set_proc(p)
        job["stage_label"] = "слияние частей проекта"
    q = pump_stdout(p)
    watch = StallWatch(job, p, "afx")
    seen: set[str] = set()
    try:
        while True:
            if p.poll() is not None:
                break
            now = _time.time()
            if os.path.isfile(merge_aelog):
                try:
                    with open(merge_aelog, encoding="utf-8-sig", errors="replace") as fh:
                        for line in fh:
                            line = line.rstrip("\r\n")
                            if line and line not in seen:
                                seen.add(line)
                                watch.touch(now)
                                if line.startswith("REELSI-") or line.startswith("comp ok: ") or "ok" in line:
                                    job.emit("  [merge aelog] " + line)
                except OSError:
                    pass  # журнал пишется параллельно AfterFX — дочитаем на следующем тике
            while True:
                try:
                    q_line = q.get_nowait()
                except queue.Empty:
                    break
                if q_line is None:
                    break
                out_line = q_line.rstrip("\r\n")
                if out_line.strip():
                    watch.touch(now)
                    job.emit(out_line)
            if watch.check(now):
                break
            _time.sleep(0.5)
        if os.path.isfile(merge_aelog):
            try:
                with open(merge_aelog, encoding="utf-8-sig", errors="replace") as fh:
                    for line in fh:
                        line = line.rstrip("\r\n")
                        if line and line not in seen:
                            seen.add(line)
                            job.emit("  [merge aelog] " + line)
            except OSError:
                pass  # финальное дочитывание: файл мог быть удалён или заблокирован
    finally:
        rc = p.wait()
        drain_emit(job, q)
        release_proc(job, p)
    return rc


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
    p = spawn_piped(job, [afx] + list(args))
    if p is None:
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
    # Построчное чтение stdout в фон: AfterFX -noui пишет ошибки в stdout,
    # переполняя буфер трубы и блокируя процесс в WriteFile при отсутствии чтения.
    q = pump_stdout(p)
    watch = StallWatch(job, p, "afx")
    try:
        while True:
            if p.poll() is not None:
                break
            now = _time.time()
            seen_n = len(seen)
            new_n = tail_master_log(job, aelog, seen, by_jsx, timelines=timelines)
            if len(seen) != seen_n or new_n:
                watch.touch(now)          # любая новая строка лога — процесс жив
            while True:
                try:
                    line = q.get_nowait()
                except queue.Empty:
                    break
                if line is None:
                    break
                line = line.rstrip("\r\n")
                if line.strip():
                    watch.touch(now)
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
            if watch.check(now):
                break
            _time.sleep(0.5)
        tail_master_log(job, aelog, seen, by_jsx, timelines=timelines)
    finally:
        rc = p.wait()
        drain_emit(job, q)
        release_proc(job, p)
    with job.lock:
        job["cur"] = ""
        job["eta"] = None      # сборка кончилась — оценку гасим
        job["eta_phase"] = None
        job["eta_total"] = None
        job["stage_done"] = len(good)
        job["pct"] = max(job.get("pct") or 0.0, p_aep_end)
    # ролики, до которых мастер не дошёл (evalFile не случился) — item_fail
    fail_reason = ("мастер не выполнил evalFile этого ролика" if not watch.stalled else
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
    p = spawn_piped(job, [aer, "-project", aep_call,
                          "-mem_usage", str(AERENDER_MEM_USAGE[0]), str(AERENDER_MEM_USAGE[1])])
    if p is None:
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
    watch = StallWatch(job, p, "aerender")

    def _handle(line: str) -> None:
        """Разбор одной строки aerender — ровно прежнее тело цикла. Обработчик ОДИН и
        на живой цикл, и на добирание хвоста после выхода/снятия процесса."""
        nonlocal total_frames, done_frames, cur_stem, next_idx
        nonlocal hdr_dur, hdr_fps, hdr_start, hdr_end
        line = line.rstrip("\r\n")
        if not line.strip():
            return
        watch.touch()
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
            if watch.check(now):
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
        release_proc(job, p)
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
                          stall_reason if watch.stalled
                          else "aerender не отрендерил (см. вывод aerender выше)",
                          bucket="failed")
    return AE_STALLED if watch.stalled else rc


def _child_registrar(job: RenderJob) -> Callable[[Any], None]:
    """Дверь «запомни ребёнка встроенного рендера»: по этому списку работает «Стоп».

    Дети встроенного рендера — node-съёмщик (а с ним и его Chrome) и ffmpeg куска со
    склейкой. Список живёт в том же поле, что у AE-ветки (`job.procs`), и гасится тем
    же путём (`render_kill` -> `kill_proc`): до этого «Остановить» на встроенном
    рендере не гасило НИЧЕГО, потому что render_kill знал только AfterFX/aerender, а
    «Стоп» был виден рендеру лишь между кадрами.

    Копим под замком джоба: список читает чужой поток (`/api/cancel`), и без замка он
    мог прочитать его ровно посередине правки.
    """
    def add(proc: Any) -> None:
        with job.lock:
            job.procs.append(proc)
    return add


def run_render_builtin(job: RenderJob, norm: Sequence[dict[str, Any]], outdir: str | None,
                       render_dir: str, host: str = "") -> None:
    """Рендер ВСТРОЕННЫМ движком: кадры рисует наш же предпросмотр (`core/webrender`).

    Это второй движок «Собрать и отрендерить»: тот же набор клипов, та же папка вывода,
    та же очередь этапов и то же окно прогресса, что у AE, — отличается только движок.
    After Effects при этом не нужен вовсе (его и не ищем).

    Почему один клип за раз, хотя набор — это набор: у встроенного движка каждый ролик
    снимается своей страницей и своим Chrome, и параллелить их значило бы делить один
    GPU между несколькими съёмщиками (а на Windows переполнение VRAM вешает машину).
    Правило рендер-пути то же, что в AE-ветке: несколько клипов — ОДНА очередь.

    Замок видеокарты берётся по КОДЕКУ (`codec_gpu_lock`): NVENC карту занимает и его
    замок остаётся, а Quick Sync, AMF и процессор — нет, и такой рендер идёт рядом с
    чужим запеканием голоса, не дожидаясь его.

    `host` — адрес запущенного сервера (его берёт `request.host` в api/render.py):
    план сцены, тело сборки и прокси живут там, а зашивать адрес нельзя — у
    изолированного профиля и у копии на другом порту он свой.
    """
    from core import encoders, webrender
    from core.gpulock import codec_gpu_lock
    total_n = len(norm)
    host = host or os.environ.get("REELSI_HOST") or webrender.DEFAULT_HOST
    job.emit("=== Рендер без AE (встроенный): {count} файл(ов), папка вывода: {dir} ===",
             count=total_n, dir=render_dir)
    # Кодек мастера выбирается ЗДЕСЬ, а не внутри рендера: по нему решается, брать ли
    # замок видеокарты, и второй выбор кодека — это второй ответ «NVENC или нет».
    codec = encoders.pick("master")
    with job.lock:
        job["stage_label"] = "рендер без AE"
        job["stage_done"] = 0
        job["stage_total"] = total_n
        job["pct"] = 0.0
        job["ae"] = ""
        job["eta"] = None
        job["eta_phase"] = None
        job["eta_total"] = None
        job["eta_preliminary"] = False
    os.makedirs(render_dir, exist_ok=True)
    for i, j in enumerate(norm):
        if job["cancel"]:
            job.emit("⏹ Остановлено")
            mark_stopped_waits(job)
            return
        stem = os.path.splitext(os.path.basename(j["xml_path"]))[0]
        mov = os.path.join(render_dir, stem + ".mp4")
        with job.lock:
            job["cur"] = stem
            job["stage_done"] = i
            job["stage_total"] = total_n
            job["pct"] = max(0.0, min(1.0, i / float(total_n or 1)))
            # Дети ЭТОГО клипа: прошлый клип уже доснят, его процессы погашены, и в
            # списке «Стоп» им делать нечего.
            job.procs = []
        item_set(job, job.lock, stem, stage="render")
        job.emit("--- {stem}: рендер без AE -> {out} ---", stem=stem, out=mov)
        try:
            # Замок видеокарты берётся ТОЛЬКО под NVENC (`codec_gpu_lock`): Quick Sync,
            # AMF и процессор карту NVIDIA не занимают, и ждать им чужой RoFormer нечего —
            # ровно на этом владелец и спотыкался («выбран другой кодек, а он ждёт»).
            # Кадры рисует Chrome — это не CUDA-модель и не сессия кодека: на карте это
            # сотни мегабайт, а не гигабайты модели, и делить с ней нечего.
            with codec_gpu_lock(codec.args[1], "рендер без AE", emit=job.emit):
                webrender.render(
                    j["xml_path"], mov,
                    body={k: v for k, v in j.items() if k != "xml_path"},
                    host=host, cancel=lambda: bool(job["cancel"]),
                    emit=_builtin_progress(job, i), codec=codec,
                    on_child=_child_registrar(job))
        except ReelsiError:
            raise
        except (SystemExit,) as e:
            txt = sysexit_text(e)
            job.emit("  ОШИБКА рендера: {err}", err=txt)
            item_fail(job, job.lock, stem, txt, bucket="failed")
            continue
        except Exception as e:
            job.emit("  ОШИБКА рендера: {err}", err=str(e))
            item_fail(job, job.lock, stem, str(e), bucket="failed")
            continue
        finally:
            # Список «Стоп» — на один клип: у доснятого процесса гасить нечего, а
            # мёртвые объекты в списке только путали бы уборку.
            with job.lock:
                job.procs = []
        if job["cancel"]:
            item_fail(job, job.lock, stem, "остановлено", bucket="failed")
            job.emit("⏹ Остановлено")
            mark_stopped_waits(job)
            return
        # «Готово» — по факту файла, а не по коду возврата: код 0 при пустом выходе
        # уже ловился в AE-ветке (aerender врал так же).
        if not os.path.isfile(mov) or os.path.getsize(mov) == 0:
            job.emit("  файла нет — смотри лог выше")
            item_fail(job, job.lock, stem, "файл не собрался", bucket="failed")
            continue
        item_done(job, job.lock, stem, mov, bucket="result")
        with job.lock:
            job["stage_done"] = i + 1
            job["pct"] = (i + 1) / float(total_n or 1)
        job.emit("Готово: {path}", path=mov)


def _builtin_progress(job: RenderJob, index: int) -> Any:
    """Дверь лога для встроенного рендера: строки в лог джоба + процент по кадрам.

    Кадры рендер печатает строкой «кадр N/M» — по ней и считается прогресс клипа:
    `pct` джоба идёт от клипов (i/total), а внутри клипа очередь этапов показывает
    долю кадров. Номер кадра в строке — СУММА готовых кадров всех кусков рендера
    (core.webrender._Progress), поэтому доля монотонна и не скачет между
    экземплярами Chrome. Без этого полоса стояла бы на месте весь клип.

    Форму разбирает ТОЛЬКО эта дверь, и форма у неё одна: сырой счётчик куска
    съёмщик печатает иначе (`кусок N: кадр a/b`, capture.mjs). Раньше обе строки
    выглядели одинаково, и процент клипа ходил вверх-вниз: суммарная строка давала
    19 %, а следом пришедшая сырая от отставшего куска — 5 %.
    """
    def emit(line: str = "", /, **vars: Any) -> None:
        m = _FRAME_RE.match(line)
        if m:
            n, total = int(m.group(1)), int(m.group(2))
            if total > 0:
                with job.lock:
                    items = job.get("items") or []
                    if 0 <= index < len(items):
                        items[index]["pct"] = max(0.0, min(1.0, n / float(total)))
                # В лог — каждая сотая часть, а не каждый кадр: 60 строк в секунду
                # вытеснили бы из лога всё остальное.
                if n % max(1, total // 20) and n != total:
                    return
        job.emit(line, **vars)
    return emit


# Строка прогресса РОЛИКА («кадр 12/180») — формат в одном месте: печатает её
# core/webrender._Progress (сумма готовых кадров всех кусков), разбирает эта дверь.
# Сырой счётчик куска съёмщика (capture.mjs) печатается ДРУГОЙ формой нарочно
# («кусок 3: кадр 79/1590»): раньше он подходил под эту же регулярку и уводил
# процент клипа назад — отставший кусок возвращал полосу с 19 % на 5 %.
_FRAME_RE = re.compile(r"^кадр\s+(\d+)/(\d+)$")


# --------------------------------------------------------------------------- #
# Выбор движка и подготовка задания
# --------------------------------------------------------------------------- #
# Движки рендера: DEFAULT_ENGINE — прежний путь (AfterFX + aerender), BUILTIN_ENGINE —
# рендер без After Effects (кадры снимает наш же предпросмотр, см. run_render_builtin).
DEFAULT_ENGINE = "ae"
BUILTIN_ENGINE = "builtin"
RENDER_ENGINES: tuple[str, ...] = (DEFAULT_ENGINE, BUILTIN_ENGINE)
# Заголовок задания в журнале — следствие выбора движка, а не знание роута: обе строки
# лежат ключами в static/i18n/en.json, по ним фронт подписывает окно прогресса.
_ENGINE_TITLES: dict[str, str] = {DEFAULT_ENGINE: "Рендер AE", BUILTIN_ENGINE: "Рендер без AE"}


@dataclass(frozen=True)
class RenderTask:
    """Задание рендера, разобранное из тела запроса: чем рендерить, куда и по какому адресу.

    engine — выбранный движок; render_dir — папка вывода (уже создана); outdir — папка
    сборки .jsx (пусто — рядом с исходным .xml); host — адрес ЗАПУЩЕННОГО сервера: он
    нужен встроенному движку (план сцены, тело сборки и прокси живут на сервере); title —
    заголовок задания в журнале.
    """

    engine: str
    render_dir: str
    outdir: str
    host: str
    title: str


def prepare_render_task(engine: str = "", render_dir: str = "", outdir: str = "",
                        host: str = "") -> RenderTask:
    """Выбор движка и подготовка задания рендера — то, что нужно ДО запуска потока.

    Роут принимает запрос и отдаёт ответ: он только вынимает строки из тела (`jstr`),
    а «каким движком, в какую папку и по какому адресу» решается здесь, рядом с
    диспетчером (`run_render_job`), которого эти же значения и кормят.

    Пустой `engine` — прежний AE-путь; неизвестное значение — отказ
    (`render_engine_bad`), а не молчаливый переход на AE: иначе опечатка в теле
    запроса тихо рендерила бы другим движком.

    Папку вывода создаём сразу: пишут в неё оба движка, и `.jsx` вписывает её
    абсолютным путём константой — не создай мы её здесь, сборка упала бы уже в
    потоке, где ошибку некому вернуть в ответ.

    Встроенному движку нужен ЗАПУЩЕННЫЙ сервер: `host` берётся из самого запроса
    (`request.host`), а не зашивается — у изолированного профиля и у копии на другом
    порту он свой.
    """
    eng = (engine or "").strip().lower() or DEFAULT_ENGINE
    if eng not in RENDER_ENGINES:
        raise ReelsiError(umsg("render_engine_bad",
                               f"Неизвестный движок рендера: {eng}", engine=eng))
    folder = (render_dir or "").strip().strip('"') or default_render_dir()
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as e:
        raise ReelsiError(umsg("render_outdir",
                               f"не создать папку вывода: {folder} — {e}",
                               dir=folder, err=str(e)))
    return RenderTask(engine=eng, render_dir=folder, outdir=(outdir or "").strip().strip('"'),
                      host=host, title=_ENGINE_TITLES[eng])


def run_render_job(job: RenderJob, norm: Sequence[dict[str, Any]], outdir: str | None,
                   render_dir: str, engine: str = "ae", host: str = "") -> None:
    """Диспетчер рендера по ДВИЖКУ.

    `engine == "builtin"` (встроенный) — рендер без After Effects: кадры снимает наш же
    предпросмотр, звук сводится по плану сцены (см. `run_render_builtin`). Любое другое
    значение — прежний AE-путь, и он не меняется: набор из ОДНОГО ролика идёт ровно
    как раньше (`run_render_single`), набор из нескольких — ОДИН проект AE и один общий
    Reelsi_all.jsx (решение пользователя 2026-09-11; радио multimode на рендер не влияет).
    """
    try:
        if not norm:
            job.emit("Набор пуст")
            return
        items_init(job, job.lock,
                   [os.path.splitext(os.path.basename(j["xml_path"]))[0] for j in norm])
        builtin = str(engine or "").strip().lower() == BUILTIN_ENGINE
        if builtin:
            run_render_builtin(job, norm, outdir, render_dir, host=host)
            return
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
