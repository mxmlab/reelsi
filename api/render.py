# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Безголовый рендер в After Effects (задание BD, шаг 3).

Свой джоб, как PXJOB у превью-прокси: рендер идёт минуты, и нарезка/сборка .jsx
не должны им блокироваться. Перед запуском AE — предполётная проверка verify_jsx:
пропавший файл в -noui пишется в $.writeln и уходит в никуда, а на выходе будет
.mov без куска камеры, о котором никто не узнает. Проверка ловит это за секунду
без всякого AE — есть пропажи, рендер не запускаем вовсе.
"""
import os, re, threading, queue, subprocess, shutil, hashlib, json, time as _time
from flask import request, jsonify
from core.fileio import atomic_json_dump
from ._core import (JOB, LOCK, bp, _cross_lock_acquire, _cross_lock_release,
                    item_done, item_fail, item_set, items_init, log_entry, umsg_err)
from core.umsg import umsg
from core import paths
from core.app_meta import env

RJOB = {"running": False, "done": False, "log": [], "pct": None, "cur": "",
        "ae": "", "out_dir": "", "result": [], "failed": [], "cancel": False,
        "eta": None, "eta_phase": None, "eta_total": None, "eta_preliminary": False,
        "stage_label": None, "stage_done": 0, "stage_total": 0}
RLOCK = threading.Lock()
RPROC = None          # текущий subprocess (AfterFX или aerender) — «Стоп» убивает дерево

# Дефолтные средние времена на 1 ролик при отсутствии накопленной статистики (задание FQ):
# сняты с реального прогона на 12 роликов (5 с сборка таймлайна, 36 с сборка в проект, 83 с рендер).
DEFAULT_JSX_SEC = 5.0
DEFAULT_AEP_SEC = 36.0
DEFAULT_RENDER_SEC = 83.0

# Сторож простоя AfterFX (задание AE-Hygiene). Отсчёт — от последней НОВОЙ строки
# активности (журнала/вывода), а не от старта: нормальная сборка тяжёлого ролика идёт
# минутами, рубить её нельзя. Простоял меньше warn — пишем в лог предупреждение,
# дольше kill — снимаем процесс и завершаем прогон честной ошибкой.
AE_STALL_WARN_SEC = 5 * 60      # предупреждение: AfterFX молчит N минут
AE_STALL_KILL_SEC = 20 * 60     # снятие: AfterFX не отвечает N минут
AE_FAST_EXIT_SEC = 5.0          # выход AfterFX быстрее — «мгновенный» (ушёл в открытую копию)
_AE_STALLED = -137              # rc _run_proc_afx: процесс снят сторожем простоя (не код выхода)

_DEF_TOT = DEFAULT_JSX_SEC + DEFAULT_AEP_SEC + DEFAULT_RENDER_SEC
_PHASE_JSX_END = DEFAULT_JSX_SEC / _DEF_TOT
_PHASE_AEP_END = (DEFAULT_JSX_SEC + DEFAULT_AEP_SEC) / _DEF_TOT

_REPO_ROOT = paths.ROOT


def _get_stats_path():
    """Путь к render_stats.json. REELSI_RENDER_STATS изолирует тестовый профиль (порт 5098)."""
    return env("RENDER_STATS") or os.path.join(_REPO_ROOT, "render_stats.json")


def _load_render_stats():
    """Загрузить накопленную статистику рендеров из render_stats.json.
    Возвращает (stats_dict, has_history_bool)."""
    p = _get_stats_path()
    if not os.path.isfile(p):
        return {"runs": []}, False
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        runs = data.get("runs", []) if isinstance(data, dict) else []
        return (data if isinstance(data, dict) else {"runs": []}), bool(runs)
    except Exception:
        return {"runs": []}, False


def _get_baseline_phase_durations(n_clips):
    """Ожидаемые длительности фаз (jsx, aep, render) в секундах для n_clips.
    Возвращает (t_jsx, t_aep, t_render, has_history)."""
    data, has_history = _load_render_stats()
    runs = data.get("runs", [])
    if has_history:
        tot_n = sum(r.get("n", 1) for r in runs if isinstance(r, dict) and r.get("n", 0) > 0)
        if tot_n > 0:
            tot_jsx = sum(r.get("jsx_sec", 0.0) for r in runs if isinstance(r, dict))
            tot_aep = sum(r.get("aep_sec", 0.0) for r in runs if isinstance(r, dict))
            tot_rnd = sum(r.get("render_sec", 0.0) for r in runs if isinstance(r, dict))
            per_jsx = (tot_jsx / tot_n) if tot_jsx > 0 else DEFAULT_JSX_SEC
            per_aep = (tot_aep / tot_n) if tot_aep > 0 else DEFAULT_AEP_SEC
            per_rnd = (tot_rnd / tot_n) if tot_rnd > 0 else DEFAULT_RENDER_SEC
            return per_jsx * n_clips, per_aep * n_clips, per_rnd * n_clips, True
    return DEFAULT_JSX_SEC * n_clips, DEFAULT_AEP_SEC * n_clips, DEFAULT_RENDER_SEC * n_clips, False


def _save_render_stats(n, jsx_sec, aep_sec, render_sec):
    """Сохранить фактические времена фаз завершенного прогона в render_stats.json."""
    if n <= 0:
        return
    try:
        p = _get_stats_path()
        data, _ = _load_render_stats()
        runs = data.setdefault("runs", [])
        runs.append({
            "ts": int(_time.time()),
            "n": int(n),
            "jsx_sec": round(float(jsx_sec), 2),
            "aep_sec": round(float(aep_sec), 2),
            "render_sec": round(float(render_sec), 2),
        })
        if len(runs) > 100:
            data["runs"] = runs[-100:]
        atomic_json_dump(p, data)
    except Exception:
        pass


def _calc_phase_bounds(t_jsx, t_aep, t_render):
    """Вычислить границы шкалы прогресса (0.0..1.0) для трех фаз по ожидаемым временам:
    возвращает (phase_jsx_end, phase_aep_end)."""
    t_tot = max(1.0, float(t_jsx + t_aep + t_render))
    p1 = max(0.01, min(0.5, float(t_jsx) / t_tot))
    p2 = max(p1 + 0.01, min(0.95, float(t_jsx + t_aep) / t_tot))
    return p1, p2


def _predict_aep_times(measured_durations, total_n, default_per_clip=DEFAULT_AEP_SEC):
    """Нелинейная модель сборки в проект (задание FQ): время n-го ролика t(n) = a + b*(n-1).
    measured_durations: список измеренных длительностей для первых k роликов [d_1, ..., d_k].
    total_n: общее число роликов в наборе N.
    default_per_clip: среднее время на ролик по умолчанию, если замеров еще нет.
    Возвращает список ожидаемых длительностей для всех N роликов [w_1, ..., w_N]."""
    k = len(measured_durations)
    if k == 0:
        return [max(0.5, float(default_per_clip))] * total_n

    if k == 1:
        avg = max(0.5, float(measured_durations[0]))
        return [avg] * total_n

    xs = list(range(k))
    ys = [float(y) for y in measured_durations]
    x_mean = (k - 1) / 2.0
    y_mean = sum(ys) / float(k)
    s_xx = sum((x - x_mean) ** 2 for x in xs)
    s_xy = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    if s_xx > 1e-9:
        b = s_xy / s_xx
        a = y_mean - b * x_mean
    else:
        a = y_mean
        b = 0.0

    weights = []
    for i in range(total_n):
        if i < k:
            weights.append(max(0.1, ys[i]))
        else:
            pred = a + b * i
            pred = max(0.5, pred)
            weights.append(pred)
    return weights


def remit(line, **vars):
    """Строка в лог рендера (свой лог, не общий JOB)."""
    with RLOCK:
        entry = log_entry(line, vars)
        RJOB["log"].append(entry)
        if len(RJOB["log"]) > 2000:
            del RJOB["log"][:len(RJOB["log"]) - 2000]


def default_render_dir():
    """Папка вывода по умолчанию — `exp` РЯДОМ с репозиторием (он публичный,
    чужим рендерам в нём не место), а не внутри. Нет папки — создадим при запуске."""
    repo = paths.ROOT
    return os.path.join(os.path.dirname(repo), "exp")


def _find_ae():
    """(AfterFX.exe, aerender.exe, имя) самой свежей установленной версии AE.
    Путь константой не зашиваем: AE 2023/2025/2026 стоят рядом, и первое же
    обновление Adobe сломало бы зашитый путь. Имя «Adobe After Effects 20xx»
    несёт год — сортируем по нему, берём самый свежий."""
    base = os.environ.get("ProgramFiles", r"C:\Program Files")
    adobe = os.path.join(base, "Adobe")
    try:
        names = os.listdir(adobe) if os.path.isdir(adobe) else []
    except OSError:
        names = []
    found = []
    for name in names:
        if not name.lower().startswith("adobe after effects"):
            continue
        m = re.search(r"(20\d{2})", name)
        ver = int(m.group(1)) if m else 0
        sf = os.path.join(adobe, name, "Support Files")
        afx, aer = os.path.join(sf, "AfterFX.exe"), os.path.join(sf, "aerender.exe")
        if os.path.isfile(afx) and os.path.isfile(aer):
            found.append((ver, afx, aer, name))
    if not found:
        return None
    found.sort(key=lambda x: x[0])
    _, afx, aer, name = found[-1]
    return afx, aer, name


def _ae_running():
    """Открыта ли уже копия After Effects (AfterFX.exe)? ЕДИНСТВЕННЫЙ источник этого
    знания для обоих путей рендера — маленькая и легко подменяется в тестах (задание
    AE-Hygiene). Проверка через tasklist: psutil в проекте нет. Открытая копия
    «съедает» наш AfterFX -noui: он возвращает код 0 за 0 секунд, а скрипт уезжает в
    НЕЁ — переписывает её проект и закрывает без вопроса о сохранении."""
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq AfterFX.exe", "/NH"],
                             capture_output=True, timeout=15)
    except Exception:
        return False        # не вышло проверить — прогон не блокируем
    return b"AfterFX.exe" in (out.stdout or b"")


def _short_path(p):
    """8.3-имя Windows-пути (короткое, без пробелов по построению). None — не вышло
    (том без коротких имён, генерация 8.3 отключена). Пробел в пути к .jsx рвёт
    аргумент `-r` AfterFX: скрипт не запускается вовсе, а процесс молча выходит с
    кодом 0 — см. задание CD."""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(512)
        n = ctypes.windll.kernel32.GetShortPathNameW(p, buf, 512)
        return buf.value if n else None
    except Exception:
        return None


def _jsx_call_path(jp):
    """Путь к .jsx для аргумента `-r`. Возвращает (путь, причина): причина непустая,
    если путь подменён. Сначала — короткое 8.3-имя (пробелов в нём нет). Не вышло
    (том без 8.3) — кладём копию под именем без пробелов рядом с целевым и зовём её,
    не молча: иначе рендер тихо не запустится. Задание CD."""
    s = _short_path(jp)
    if s:
        return s, ""
    stem = os.path.splitext(os.path.basename(jp))[0]
    alt = os.path.join(os.path.dirname(jp), re.sub(r"\s+", "", stem) + "_headless.jsx")
    shutil.copyfile(jp, alt)
    return alt, (f"  короткое имя для {os.path.basename(jp)} не вышло (том без 8.3-имён) — "
                 f"выполняю копию {os.path.basename(alt)}")


def _aep_call_path(aep):
    """Путь к .aep для аргумента `-project` aerender. Возвращает (путь, причина):
    причина непустая, когда путь подменён. aerender читает аргументы командной строки
    как ANSI, и на кодовой странице 1252 кириллица превращается в '????' — «Path is
    not valid», рендер не запускается (задание EV). Сначала короткое 8.3-имя; не
    вышло (том без 8.3) — копия рядом с целевым под ASCII-именем с хэшем стема, чтобы
    два кириллических имени не затёрли друг друга."""
    s = _short_path(aep)
    if s:
        return s, ""
    stem = os.path.splitext(os.path.basename(aep))[0]
    alt = os.path.join(os.path.dirname(aep),
                       re.sub(r"[^A-Za-z0-9_.-]+", "_", stem) + "_" +
                       hashlib.md5(stem.encode("utf-8")).hexdigest()[:6] + ".aep")
    shutil.copyfile(aep, alt)
    return alt, (f"  короткое имя для {os.path.basename(aep)} не вышло (том без 8.3-имён) — "
                 f"рендерю копию {os.path.basename(alt)}")


def _rendered_ok(path):
    """Файл на месте и непустой — единственный честный признак, что aerender реально
    отрендерил. Коду возврата не верить: на «Path is not valid» он вернул 0 (задание EV)."""
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def _remove_stale_aelog(aelog):
    """Перед запуском AfterFX удалить журнал ПРОШЛОГО прогона (задание AE-Hygiene).
    Иначе проверка «нет .aelog.txt = скрипт не запустился» проходит по старому файлу,
    а _tail_master_log читает старый лог и печатает «собран:» за ролики, которые ещё
    даже не строились. Возвращает None или текст ошибки (файл занят — это ошибка
    прогона с внятным текстом, а не молчаливое продолжение)."""
    try:
        if os.path.isfile(aelog):
            os.remove(aelog)
        return None
    except OSError as e:
        return str(e)


def _aep_updated_by_run(aep, mtime_before):
    """Отличить «.aep собран ЭТИМ прогоном» от «остался от прошлого» (задание
    AE-Hygiene). .aep стирать нельзя: человек мог доработать проект руками, поэтому
    вместо факта «файл существует» — факт «файл обновлён этим прогоном»: файла не
    было и он появился, либо время изменения выросло. mtime_before — os.path.getmtime
    ДО запуска AfterFX (None — файла не было)."""
    try:
        now = os.path.getmtime(aep) if os.path.isfile(aep) else None
    except OSError:
        return False
    return now is not None and (mtime_before is None or now > mtime_before)


def _comp_frames(jsx):
    """Общее число кадров композиции — честное, из плана (.jsx несёт FPS/DUR и
    удлинение под хвостовой дисклеймер). Рендер-процент = кадр / это число, без
    эвристик. Не прочиталось — None (тогда процент только по (N/M) из вывода)."""
    m = re.search(r"FPS=(\d+), DUR=([\d.]+)", jsx)
    if not m:
        return None
    fps, dur = int(m.group(1)), float(m.group(2))
    extra = re.search(r"Math\.max\(DUR,1\)(\+[\d.]+)?, FPS", jsx)
    add = float(extra.group(1)) if extra and extra.group(1) else 0.0
    total = round((max(dur, 1.0) + add) * fps)
    return total or None


_FRAME = re.compile(r"\((\d+)(?:/(\d+))?\)")
_END_MARK = ("Finished composition", "Total Time Elapsed")
# Имя композиции из маркера aerender. Реальный вывод: PROGRESS: 8/26/2026 12:55:08 AM:
# Finished composition "C0250". — имя В КАВЫЧКАХ и с точкой в конце; без кавычек тоже
# бывает (другая локаль). Сначала кавычки, потом запасной вариант (задание FK/FL).
_COMP_FIN = re.compile(r'Finished composition\s+"([^"]+)"')
_COMP_FIN_BARE = re.compile(r"Finished composition\s*[:：]?\s*([^\s.]+)")

# Задание FL: разбор заголовка текущей композиции из вывода aerender.
# Имя композиции — из строки Output To: (известно В НАЧАЛЕ её рендера).
_OUTPUT_TO = re.compile(r"Output To:\s*(.+)", re.I)
# Кадры композиции от AE — из блока Start / End / Duration / Frame Rate.
_TC_START = re.compile(r"Start:\s*(\d+:\d{2}:\d{2}:\d{2})", re.I)
_TC_END = re.compile(r"End:\s*(\d+:\d{2}:\d{2}:\d{2})", re.I)
_TC_DUR = re.compile(r"Duration:\s*(\d+:\d{2}:\d{2}:\d{2})", re.I)
_FRAME_RATE = re.compile(r"Frame Rate:\s*([\d.]+)", re.I)


def _parse_output_to(line):
    """Имя композиции из строки «Output To: ПУТЬ».
    Возвращает имя файла без пути и расширения (stem), или None (задание FL)."""
    m = _OUTPUT_TO.search(line)
    if not m:
        return None
    raw = m.group(1).strip().strip('"').strip("'")
    if not raw:
        return None
    norm = raw.replace("\\", "/")
    base = norm.rsplit("/", 1)[-1]
    stem, _ = os.path.splitext(base)
    return stem or None


def _parse_timecode(tc, fps):
    """Таймкод ч:мм:сс:кадры + fps -> общее число кадров (задание FL)."""
    if not tc or not fps:
        return None
    try:
        fps_val = float(fps)
        if fps_val <= 0:
            return None
    except (ValueError, TypeError):
        return None
    m = re.search(r"(\d+):(\d{2}):(\d{2}):(\d{2})", tc.strip())
    if not m:
        return None
    h, mn, s, f = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return round((h * 3600 + mn * 60 + s) * fps_val) + f


def _parse_frame_rate(line):
    """FPS из строки «Frame Rate: 60.00 (comp)». Возвращает float или None (задание FL)."""
    m = _FRAME_RATE.search(line)
    if not m:
        return None
    try:
        return float(m.group(1))
    except (ValueError, TypeError):
        return None


def _finished_comp_name(line):
    """Имя композиции из строки «Finished composition ...». Возвращает имя без кавычек
    и точки, или None, если маркер не тот."""
    m = _COMP_FIN.search(line)
    if m:
        return m.group(1).strip()
    m = _COMP_FIN_BARE.search(line)
    if m:
        return m.group(1).strip().strip('"').rstrip(".")
    return None


def _parse_progress(line, total):
    """(N) или (N/M) в строке вывода aerender -> доля. M в строке — авторитет;
    без неё берём total из плана. Finished composition / Total Time Elapsed —
    маркеры конца: 100%."""
    if any(m in line for m in _END_MARK):
        return 1.0
    m = _FRAME.search(line)
    if not m:
        return None
    frame = int(m.group(1))
    t = int(m.group(2)) if m.group(2) else total
    if not t or t <= 0:
        return None
    return min(1.0, frame / t)


def _kill_proc(p):
    """taskkill /T /F — дерево: aerender сам по себе не всегда держит детей,
    но после убийства не должен остаться ни один процесс рендера."""
    for _ in range(2):                       # taskkill /T бывает таймаутит — вторая попытка
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                           capture_output=True, timeout=15)
            if p.poll() is not None:
                break
        except Exception:
            pass
    else:
        try:
            p.kill()
        except Exception:
            pass


def render_kill():
    """«Стоп» из интерфейса (/api/cancel зовёт): флаг джобу + реально убить
    текущий subprocess (AfterFX или aerender) с деревом."""
    with RLOCK:
        RJOB["cancel"] = True
        p = RPROC
    if p and p.poll() is None:
        _kill_proc(p)


def _run_proc(cmd, total_frames=None, item_name=None, pct_base=0.0, pct_span=1.0):
    """Запустить процесс, стримить вывод в лог рендера, парсить прогресс.
    Возвращает код выхода. item_name — элемент очереди (задание FA), которому
    дублируется доля рендера (aerender — единственный этап с честным процентом).
    pct_base и pct_span задают долю шкалы (например 0.35..1.0 на фазе рендера)."""
    global RPROC
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except FileNotFoundError as e:
        remit("не найден исполняемый файл: {err}", err=str(e))
        return -1
    with RLOCK:
        RPROC = p
    if RJOB["cancel"]:
        _kill_proc(p)
    hdr_dur, hdr_fps, hdr_start, hdr_end = None, None, None, None
    try:
        for line in p.stdout:
            line = line.rstrip("\r\n")
            if not line.strip():
                continue
            remit(line)

            # Заголовок AE (задание FL): кадры композиции и имя из Output To:
            m_dur = _TC_DUR.search(line)
            if m_dur:
                hdr_dur = m_dur.group(1)
            fps_val = _parse_frame_rate(line)
            if fps_val is not None:
                hdr_fps = fps_val
            m_start = _TC_START.search(line)
            if m_start:
                hdr_start = m_start.group(1)
            m_end = _TC_END.search(line)
            if m_end:
                hdr_end = m_end.group(1)

            if hdr_dur and hdr_fps:
                ae_fr = _parse_timecode(hdr_dur, hdr_fps)
                if ae_fr:
                    total_frames = ae_fr
            elif hdr_start and hdr_end and hdr_fps:
                sf = _parse_timecode(hdr_start, hdr_fps)
                ef = _parse_timecode(hdr_end, hdr_fps)
                if sf is not None and ef is not None and ef >= sf:
                    total_frames = ef - sf + 1

            out_to = _parse_output_to(line)
            if out_to:
                with RLOCK:
                    RJOB["cur"] = out_to

            pr = _parse_progress(line, total_frames) if total_frames else None
            if pr is not None:
                with RLOCK:
                    pct_calc = min(1.0, pct_base + pr * pct_span)
                    RJOB["pct"] = max(RJOB.get("pct") or 0.0, pct_calc)
                    if item_name:
                        for it in RJOB.get("items", []):
                            if it.get("name") == item_name:
                                it["pct"] = pr          # доля и в очередь (задание FA)
                                break
            if "Finished composition" in line or "Total Time Elapsed" in line:
                with RLOCK:
                    pct_calc = min(1.0, pct_base + pct_span)
                    RJOB["pct"] = max(RJOB.get("pct") or 0.0, pct_calc)
    finally:
        rc = p.wait()
        with RLOCK:
            if RPROC is p:
                RPROC = None
    return rc


def _pump_stdout(p):
    """Фоновый поток чтения stdout процесса в queue.Queue (задания AE-Hygiene, GN).
    Предотвращает переполнение OS-буфера трубы при обильном выводе процесса (напр. AfterFX -noui)
    и позволяет сторожу опрашивать активность с таймаутом без зависания в readline()."""
    q = queue.Queue()

    def _pump():
        try:
            for line in p.stdout:
                q.put(line)
        except Exception:
            pass
        finally:
            q.put(None)          # EOF stdout

    threading.Thread(target=_pump, daemon=True).start()
    return q


def _run_proc_afx(cmd):
    """AfterFX -noui -r одиночного ролика со сторожем простоя (задание AE-Hygiene).
    Раньше здесь был _run_proc с блокирующим чтением stdout: молчащий AfterFX вешал
    джоб навсегда. Теперь stdout читается фоном, а основной поток ждёт процесс и
    следит за активностью: процесс жив, но за AE_STALL_WARN_SEC не появилось ни одной
    новой строки stdout — предупреждение в лог; за AE_STALL_KILL_SEC — процесс снимается
    (_kill_proc) и возвращается _AE_STALLED (сообщение уже в логе). Отсчёт простоя —
    от последней НОВОЙ строки, не от старта."""
    global RPROC
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
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
    return _AE_STALLED if stalled else rc


def _mark_stopped_waits():
    """«Стоп» по RJOB["cancel"]: файлам, до которых работа не дошла (stage="wait"),
    проставить stage="stopped", чтобы очередь показывала их «остановлено», а не «в очереди».
    Своя копия, как _mark_stopped_waits в jobs.py, но под RLOCK/RJOB рендера."""
    with RLOCK:
        for it in RJOB.get("items", []):
            if it.get("stage") == "wait":
                it["stage"] = "stopped"


def _run_render_single(norm, outdir, render_dir):
    """Одиночный рендер (задание BD): безголовый .jsx (очередь+save+quit) -> verify_jsx ->
    AfterFX -noui -r (собрать .aep) -> aerender -project (рендер). Это же путь остаётся
    для набора из ОДНОГО ролика (задание FH): поведение и .jsx ровно сегодняшние."""
    try:
        from core import verify_jsx
        from core import xml2ae
        from .inserts import _convert_inserts
        remit("=== Рендер AE: {count} файл(ов), папка вывода: {dir} ===",
              count=len(norm), dir=render_dir)
        total_n = len(norm)
        t_jsx_base, t_aep_base, t_rnd_base, has_stats = _get_baseline_phase_durations(total_n)
        p_jsx_end, p_aep_end = _calc_phase_bounds(t_jsx_base, t_aep_base, t_rnd_base)
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
        comp_names = {}   # стем .jsx -> имя главной композиции (задание FJ: .mov по нему)
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
            # строки webp-вставка дошла бы до AE и уронила предполёт (задание BS).
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
                # .jsx — файл 01_C0233.xml может дать композицию C0233 (задание FJ)
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
            except Exception as e:
                remit("  ОШИБКА сборки: {err}", err=str(e))
                # клип, у которого не собрался даже безголовый .jsx — item_fail (задание FA)
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
            # это ПОКЛИПОВЫЕ падения (конкретные .jsx), не глобальные — item_fail (задание FA)
            for name, e in bad:
                item_fail(RJOB, RLOCK, name, e, bucket="failed")
            return
        remit("Проверка .jsx пройдена — файлов на диске хватает, запускаю AE")
        # 3) найти AE (самый свежий)
        ae = _find_ae()
        if not ae:
            remit("After Effects не найден (искал в «C:\\Program Files\\Adobe\\Adobe After Effects *»). "
                  "Проверь установку и запусти снова.")
            # ГЛОБАЛЬНОЕ падение БЕЗ конкретного клипа (AE не найден для всего набора) —
            # прямой записью в failed, а не item_fail: в items писать нечего (задание FA).
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
            jsx_call, why = _jsx_call_path(jp)     # короткое имя: пробел рвёт -r (задание CD)
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
            # Открытая копия AE перехватывает наш AfterFX -noui (задание AE-Hygiene): он
            # возвращает код 0 за 0 секунд, а скрипт уезжает в НЕЁ — переписывает её
            # проект и закрывает без вопроса о сохранении. Такой запуск — стоп ДО старта.
            if _ae_running():
                remit("  After Effects ОТКРЫТ — прогон этого ролика не начинаю. Закрой "
                      "After Effects и запусти рендер снова: иначе скрипт уйдёт в открытую "
                      "копию, перепишет её проект и закроет её.")
                item_fail(RJOB, RLOCK, stem,
                          "After Effects открыт — закрой его и запусти рендер снова",
                          bucket="failed")
                continue
            # Гигиена журнала (задание AE-Hygiene): .aelog.txt прошлого прогона удаляем
            # ДО запуска AfterFX, иначе «нет файла = скрипт не запустился» снова врёт.
            # Не смогли удалить (файл занят) — ошибка прогона с текстом, не продолжение.
            rm_err = _remove_stale_aelog(aelog)
            if rm_err:
                remit("  не удалить старый журнал {log}: {err} — файл занят? Рендер пропущен.",
                      log=aelog, err=rm_err)
                item_fail(RJOB, RLOCK, stem,
                          "не удалить старый .aelog.txt (файл занят?)", bucket="failed")
                continue
            # .aep НЕ стираем (человек мог доработать его руками) — запоминаем время
            # изменения ДО запуска, после прогона проверяем «обновлён этим прогоном»,
            # а не «существует» (задание AE-Hygiene).
            aep_mtime = os.path.getmtime(aep) if os.path.isfile(aep) else None
            t_afx = _time.time()
            rc = _run_proc_afx([afx, "-noui", "-r", jsx_call])
            afx_sec = _time.time() - t_afx
            if RJOB["cancel"]:
                break
            if rc == _AE_STALLED:
                # сторож снял зависший AfterFX — сообщение уже в логе, ролик помечаем здесь
                item_fail(RJOB, RLOCK, stem,
                          "After Effects не отвечает %d минут — прогон снят"
                          % (AE_STALL_KILL_SEC // 60), bucket="failed")
                continue
            if rc != 0:
                remit("  AfterFX завершился с кодом {code} — смотри вывод выше", code=rc)
            # Признаков два (задание CD): нет .aelog.txt -> скрипт НЕ запустился (путь,
            # пробелы); лог есть, но .aep не обновлён этим прогоном -> скрипт шёл и
            # споткнулся, в логе видно где. Один признак (наличие .aep) путал «не
            # запустился» с «упал на середине» (задание AE-Hygiene).
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
            if not _aep_updated_by_run(aep, aep_mtime):
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
            total = _comp_frames(open(jp, encoding="utf-8-sig").read())
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
            aep_call, aep_why = _aep_call_path(aep)   # кириллица в -project рвёт aerender (задание EV)
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
                    pass
            if RJOB["cancel"]:
                break
            if rc != 0:
                remit("  aerender завершился с кодом {code} — смотри вывод выше", code=rc)
                item_fail(RJOB, RLOCK, stem, f"aerender rc={rc}", bucket="failed")
                continue
            mov = os.path.join(render_dir, comp_names.get(stem, stem) + ".mov")
            if not _rendered_ok(mov):
                # aerender на «Path is not valid» вернул 0 — коду возврата не верим, «Готово»
                # только по факту файла (задание EV)
                remit("  aerender вернул 0, но файла нет — смотри вывод aerender выше")
                item_fail(RJOB, RLOCK, stem,
                          "aerender вернул 0, но файла нет — смотри вывод aerender выше",
                          bucket="failed")
                continue
            # Одно место записи «готово» (задание FA): item_done и кладёт путь в result,
            # и переводит элемент в done — вторым местом их не развести (задание BI живёт
            # здесь же: набор копит список, а не перезаписывает последний).
            item_done(RJOB, RLOCK, stem, mov, bucket="result")
            with RLOCK:
                RJOB["stage_done"] = 1
                RJOB["pct"] = 1.0
                RJOB["eta"] = None
                RJOB["eta_phase"] = None
                RJOB["eta_total"] = None
            remit("Готово: {path}", path=mov)
            _save_render_stats(1, jsx_dur, aep_dur, render_dur)
    except Exception:
        import traceback
        remit("ОШИБКА:\n{tb}", tb=traceback.format_exc().strip().splitlines()[-1])
        # ГЛОБАЛЬНОЕ падение (внутренняя ошибка рендера) БЕЗ конкретного клипа — прямой
        # записью в failed, а не item_fail: связано ни с одним файлом набора (задание FA).
        with RLOCK:
            RJOB["failed"].append({"name": "рендер", "reason": "внутренняя ошибка"})
    finally:
        with RLOCK:
            RJOB.update(running=False, done=True, cur="",
                        pct=(1.0 if RJOB["result"] and not RJOB["failed"] else (RJOB["pct"] or 0)))


def _run_render_batch(batch, outdir, render_dir):
    """Набор роликов в ОДИН проект AE (задание FH): обычные .jsx (без безголового хвоста)
    + мастер-скрипт (evalFile каждого в своём try/catch, одна очередь, один .aep) ->
    verify_jsx по каждому -> один AfterFX -noui -r мастера -> один aerender -project."""
    from core import verify_jsx
    from core import xml2ae
    from .inserts import _convert_inserts
    total_n = len(batch)
    t_jsx_base, t_aep_base, t_rnd_base, has_stats = _get_baseline_phase_durations(total_n)
    p_jsx_end, p_aep_end = _calc_phase_bounds(t_jsx_base, t_aep_base, t_rnd_base)
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
    jsx_list = []
    comps = []   # (стем .jsx, имя главной композиции) — .mov ждём по имени (задание FJ)
    t_jsx_start = _time.time()
    jsx_times = []
    for i, j in enumerate(batch):
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
        item_set(RJOB, RLOCK, stem, stage="jsx")   # этап 1: сборка .jsx
        od = j.get("outdir") or outdir or os.path.dirname(j["xml_path"])
        os.makedirs(od, exist_ok=True)
        jp = os.path.abspath(os.path.join(od, stem + ".jsx"))
        _convert_inserts(j.get("inserts") or [], emit=remit)
        kw = {k: v for k, v in j.items() if k not in ("xml_path", "outdir")}
        remit("  {stem}: сборка .jsx…", stem=stem)
        try:
            comp_name_out = []
            xml2ae.to_ae_full(j["xml_path"], jp, binpfx=stem + " — ", comps_global=True,
                              emit=remit, cancel=lambda: RJOB["cancel"],
                              comp_name_out=comp_name_out, **kw)
            jsx_list.append(jp)
            comps.append((stem, comp_name_out[0] if comp_name_out else stem))
            now = _time.time()
            built_jsx_n = i + 1
            jsx_times.append((now - t_jsx_start, built_jsx_n))
            with RLOCK:
                RJOB["stage_done"] = built_jsx_n
                pct_step = (built_jsx_n / total_n) * p_jsx_end
                RJOB["pct"] = max(RJOB.get("pct") or 0.0, min(p_jsx_end, pct_step))
                # ETA на фазе 1 (задание FK, FQ):
                if built_jsx_n < total_n:
                    elapsed = now - t_jsx_start
                    if elapsed >= 15.0 and len(jsx_times) >= 2:
                        _dt = jsx_times[-1][0] - jsx_times[0][0]
                        _dn = jsx_times[-1][1] - jsx_times[0][1]
                        if _dt > 0 and _dn > 0:
                            per_clip = _dt / _dn
                            rem_phase = per_clip * (total_n - built_jsx_n)
                            RJOB["eta"] = rem_phase
                            RJOB["eta_phase"] = rem_phase
                            RJOB["eta_total"] = rem_phase + t_aep_base + t_rnd_base
                            RJOB["eta_preliminary"] = False
                            RJOB["stage_label"] = "сборка таймлайнов"
                    elif has_stats:
                        rem_phase = (t_jsx_base / total_n) * (total_n - built_jsx_n)
                        RJOB["eta"] = rem_phase
                        RJOB["eta_phase"] = rem_phase
                        RJOB["eta_total"] = rem_phase + t_aep_base + t_rnd_base
                        RJOB["eta_preliminary"] = True
                        RJOB["stage_label"] = "сборка таймлайнов"
                    else:
                        RJOB["eta"] = None
                        RJOB["eta_phase"] = None
                        RJOB["eta_total"] = None
                        RJOB["eta_preliminary"] = False
            remit("  -> {path}", path=jp)
        except xml2ae.Cancelled:
            remit("⏹ Остановлено — рендер не запускался")
            return
        except Exception as e:
            remit("  ОШИБКА сборки: {err}", err=str(e))
            item_fail(RJOB, RLOCK, stem, str(e), bucket="failed")
    if not jsx_list:
        remit("Ничего не собралось — рендер не запускался")
        return
    jsx_dur = _time.time() - t_jsx_start
    with RLOCK:
        RJOB["pct"] = max(RJOB.get("pct") or 0.0, p_jsx_end)
        RJOB["eta"] = None
        RJOB["eta_phase"] = None
        RJOB["eta_total"] = None
    # 2) предполётная проверка — ПОКЛИПОВО, до AE: ролик, не прошедший проверку,
    # в мастер-скрипт не попадает, остальные едут дальше (задание FH)
    with RLOCK:
        RJOB["cur"] = ""
        RJOB["stage_label"] = "проверка файлов"
    good, bad = [], []
    for stem, comp_name in comps:
        jp = os.path.join(os.path.dirname(jsx_list[0]), stem + ".jsx")
        item_set(RJOB, RLOCK, stem, stage="check")   # этап 2: предполётная проверка
        rep = verify_jsx.verify(jp)
        errs = [str(e) for e in rep.errors]
        if errs:
            bad.append((stem, errs))
            for e in errs:
                remit("  ✗ {name}: {err}", name=stem, err=e)
            item_fail(RJOB, RLOCK, stem, errs[0], bucket="failed")
        else:
            # кадры композиции — по её .jsx (задание FK): M для процента текущей
            # композиции, когда aerender его не печатает, и для ETA в кадрах
            frames = _comp_frames(open(jp, encoding="utf-8-sig").read())
            good.append((stem, comp_name, jp, frames))
    if bad:
        remit("Часть набора не прошла предполёт — эти ролики не поедут в общий проект")
    if not good:
        remit("Ни один ролик не прошёл предполёт — рендер не запущен")
        return

    # Пересчитываем границы, если часть роликов отсеялась
    if len(good) < total_n and len(good) > 0:
        t_aep_base = (t_aep_base / total_n) * len(good)
        t_rnd_base = (t_rnd_base / total_n) * len(good)
        p_jsx_end, p_aep_end = _calc_phase_bounds(jsx_dur, t_aep_base, t_rnd_base)

    # 3) мастер-скрипт из ПРОШЕДШИХ проверку .jsx. Мастер и .aep — В ПАПКУ НАБОРА
    # (где лежат .jsx роликов), а не в render_dir: в render_dir уезжает только готовое
    # видео (задание FK). .aelog.txt едет за .aep, как в одиночном пути.
    batch_dir = os.path.dirname(jsx_list[0])
    aep_path = os.path.join(batch_dir, "reelsi_batch.aep")
    master_path = os.path.join(batch_dir, "render_master.jsx")
    from core.xml2ae.build import _write_master
    # render_dir остаётся для om.file — видео в папке вывода рендера
    _write_master([g[2] for g in good], master_path, aep_path, render_dir)
    remit("Мастер-скрипт: {path} ({n} роликов)", path=master_path, n=len(good))
    # 4) найти AE
    ae = _find_ae()
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
        RJOB["stage_total"] = len(good)
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
    master_call, why = _jsx_call_path(master_path)
    if why:
        remit("  короткое имя для {name} не вышло (том без 8.3-имён) — выполняю копию {alt_name}",
              name=os.path.basename(master_path), alt_name=os.path.basename(master_call))
    remit("--- сборка общего проекта (AfterFX -noui, мастер) ---")
    aelog = re.sub(r"\.aep$", ".aelog.txt", aep_path)
    # Открытая копия AE перехватывает наш AfterFX -noui (задание AE-Hygiene): он
    # возвращает код 0 за 0 секунд, а мастер уезжает в НЕЁ — переписывает её проект
    # и закрывает её без вопроса о сохранении. Такой запуск — стоп ДО старта партии.
    if _ae_running():
        remit("After Effects ОТКРЫТ — прогон набора не начинаю. Закрой After Effects и "
              "запусти рендер снова: иначе мастер уйдёт в открытую копию, перепишет её "
              "проект и закроет её без вопроса о сохранении.")
        for stem, _cn, _jp, _fr in good:
            item_fail(RJOB, RLOCK, stem,
                      "After Effects открыт — закрой его и запусти рендер снова",
                      bucket="failed")
        return
    # Гигиена журнала (задание AE-Hygiene): .aelog.txt прошлого прогона удаляем ДО
    # запуска мастера — иначе «нет файла = скрипт не запустился» проходит по старому,
    # а _tail_master_log печатает «собран:» за ролики, которые ещё не строились.
    # Не смогли удалить (файл занят) — ошибка прогона с текстом, не продолжение.
    rm_err = _remove_stale_aelog(aelog)
    if rm_err:
        remit("не удалить старый журнал {log}: {err} — файл занят? Прогон остановлен.",
              log=aelog, err=rm_err)
        for stem, _cn, _jp, _fr in good:
            item_fail(RJOB, RLOCK, stem,
                      "не удалить старый .aelog.txt (файл занят?)", bucket="failed")
        return
    # стадия aep на время работы AfterFX (задание FJ): интерфейс показывает
    # «собираю проект», а не «0/12» без объяснения; в render переводятся только
    # перед aerender
    for stem, _cn, _jp, _fr in good:
        item_set(RJOB, RLOCK, stem, stage="aep")
    # .aep НЕ стираем (человек мог доработать его руками) — время ДО запуска мастера,
    # после прогона проверяем «обновлён этим прогоном» (задание AE-Hygiene).
    aep_mtime = os.path.getmtime(aep_path) if os.path.isfile(aep_path) else None
    t_aep_start = _time.time()
    rc = _run_proc_master(afx, "-noui", "-r", master_call,
                          good=good, render_dir=render_dir, aelog_path=aelog,
                          p_jsx_end=p_jsx_end, p_aep_end=p_aep_end,
                          t_aep_base=t_aep_base, t_render_base=t_rnd_base,
                          has_stats=has_stats)
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
    if not _aep_updated_by_run(aep_path, aep_mtime):
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
    #    соответствующий элемент в done по ИМЕНИ КОМПОЗИЦИИ (задание FH/FJ)
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
    aep_call, aep_why = _aep_call_path(aep_path)
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
            pass
    if RJOB["cancel"]:
        _mark_stopped_waits()
        return
    if RJOB["result"] and not RJOB["failed"]:
        _save_render_stats(len(good), jsx_dur, aep_dur, render_dur)


def _run_render_combined(batch, outdir, render_dir):
    """Рендер набора «Один на всё» (задание C, решение 2026-09-11): вместо N клиповых .jsx —
    ОДИН файл Reelsi_all.jsx (build_combined с comps_global=True и префиксами бинов), мастер
    выполняет ровно его. Предполёт verify_jsx — по общему файлу ОДИН раз (верификатор
    умеет разбирать «один .jsx на всё» по таймлайнам). Отсеять один непрошедший ролик
    нельзя — .jsx один на всех: ошибки предполёта останавливают весь набор (нужно
    исправить файл или убрать ролик из набора и запустить снова). Этап «сборка
    проекта» отслеживается по строкам «таймлайн ok:» журнала мастера (задание HC):
    stage_total = total_n, собранные таймлайны переходят в стадию built, текущий
    собираемый — в aep, остальные ждут в wait; в конце этапа переводим все ролики
    набора дальше по очереди этапов ровно так же, как это делает _run_render_batch."""
    from core import verify_jsx
    from core import xml2ae
    from .inserts import _convert_inserts
    total_n = len(batch)
    t_jsx_base, t_aep_base, t_rnd_base, has_stats = _get_baseline_phase_durations(total_n)
    p_jsx_end, p_aep_end = _calc_phase_bounds(t_jsx_base, t_aep_base, t_rnd_base)
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
    # Вставки конвертим ДО сборки (как _run_build_job и _run_render_batch): иначе
    # webp-вставка дошла бы до AE и уронила предполёт (задание BS).
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
        # «собрано done-1», чтобы счётчик не забегал вперёд (задание C)
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
    # останавливают набор ЦЕЛИКОМ, и это сказано в логе прямо (задание C).
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
    # набора; .mov ждём по ИМЕНИ КОМПОЗИЦИИ из comp_names_out, задание FJ/C)
    raw = open(combined_jp, encoding="utf-8-sig").read()
    blocks = verify_jsx.split_timelines(raw)
    frames_per = [_comp_frames(b) for b in blocks] if len(blocks) == total_n else [None] * total_n
    good = []
    for i, stem in enumerate(stems):
        cn = comp_names_out[i] if i < len(comp_names_out) else stem
        fr = frames_per[i] if i < len(frames_per) else None
        good.append((stem, cn, combined_jp, fr))
    # 3) мастер-скрипт из ОДНОГО общего файла. Мастер и .aep — в папку набора
    # (где лежит Reelsi_all.jsx), в render_dir уезжает только готовое видео (задание C)
    batch_dir = od
    aep_path = os.path.join(batch_dir, "reelsi_batch.aep")
    master_path = os.path.join(batch_dir, "render_master.jsx")
    from core.xml2ae.build import _write_master
    _write_master([combined_jp], master_path, aep_path, render_dir)
    remit("Мастер-скрипт: {path} ({n} роликов)", path=master_path, n=total_n)
    # 4) найти AE
    ae = _find_ae()
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
        RJOB["stage_total"] = total_n  # прогресс по таймлайнам набора (задания C, HC)
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
    master_call, why = _jsx_call_path(master_path)
    if why:
        remit("  короткое имя для {name} не вышло (том без 8.3-имён) — выполняю копию {alt_name}",
              name=os.path.basename(master_path), alt_name=os.path.basename(master_call))
    remit("--- сборка общего проекта (AfterFX -noui, мастер) ---")
    aelog = re.sub(r"\.aep$", ".aelog.txt", aep_path)
    # Открытая копия AE перехватывает наш AfterFX -noui (задание AE-Hygiene) — стоп
    if _ae_running():
        remit("After Effects ОТКРЫТ — прогон набора не начинаю. Закрой After Effects и "
              "запусти рендер снова: иначе мастер уйдёт в открытую копию, перепишет её "
              "проект и закроет её без вопроса о сохранении.")
        for stem, _cn, _jp, _fr in good:
            item_fail(RJOB, RLOCK, stem,
                      "After Effects открыт — закрой его и запусти рендер снова",
                      bucket="failed")
        return
    # Гигиена журнала (задание AE-Hygiene): .aelog.txt прошлого прогона удаляем ДО
    rm_err = _remove_stale_aelog(aelog)
    if rm_err:
        remit("не удалить старый журнал {log}: {err} — файл занят? Прогон остановлен.",
              log=aelog, err=rm_err)
        for stem, _cn, _jp, _fr in good:
            item_fail(RJOB, RLOCK, stem,
                      "не удалить старый .aelog.txt (файл занят?)", bucket="failed")
        return
    # Стадии на время работы AfterFX (задание HC): первый ролик собирается (aep),
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
    if not _aep_updated_by_run(aep_path, aep_mtime):
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
    #    соответствующий элемент в done по ИМЕНИ КОМПОЗИЦИИ (задание FH/FJ/C)
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
    aep_call, aep_why = _aep_call_path(aep_path)
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
            pass
    if RJOB["cancel"]:
        _mark_stopped_waits()
        return
    if RJOB["result"] and not RJOB["failed"]:
        _save_render_stats(len(good), jsx_dur, aep_dur, render_dur)


def _run_proc_master(afx, *args, good, render_dir, aelog_path,
                     p_jsx_end=_PHASE_JSX_END, p_aep_end=_PHASE_AEP_END,
                     t_aep_base=DEFAULT_AEP_SEC, t_render_base=DEFAULT_RENDER_SEC,
                     has_stats=False, whole_file=False):
    """AfterFX -noui -r мастера с ЖИВЫМ прогрессом и нелинейной оценкой (задания FJ, FQ). ExtendScript буферизует
    файл-лог до close(), поэтому мастер после КАЖДОГО ролика закрывает лог и открывает
    заново на дозапись — строки evalFile ok: появляются на диске сразу. Здесь читаем
    лог в цикле ожидания процесса и по каждой новой строке переводим ролик из aep в
    готовность к рендеру (stage=check — до предполёта уже пройден; ставим render позже)
    и пишем строку в общий лог интерфейса. evalFile ОШИБКА: — item_fail ролику.
    item_done остаётся один и только про рендер — второго источника «готово» нет.
    whole_file (задания C, HC) — мастер выполняет ОДИН .jsx на ВЕСЬ набор («Один на всё»):
    прогресс сборки отслеживается по строкам «таймлайн ok:» в журнале мастера (k-я строка —
    k-й ролик по порядку набора), stage_total = len(good), собранные ролики получают stage=built."""
    try:
        p = subprocess.Popen([afx] + list(args),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
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
    # Сторож простоя (задание AE-Hygiene): мастер жив и молчит — предупреждение на
    # AE_STALL_WARN_SEC, снятие на AE_STALL_KILL_SEC. Отсчёт — от последней НОВОЙ
    # строки .aelog.txt, а не от старта: тяжёлый ролик собирается минутами.
    last_activity = t_build_start
    warn_sent = False
    stalled = False
    # Построчное чтение stdout в фон (задание GN): AfterFX -noui пишет ошибки в stdout,
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

                weights = _predict_aep_times(clip_durations, len(good), default_per_clip=default_per)
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
            # завершить прогон честной ошибкой, а не висеть вечно (задание AE-Hygiene).
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
    этапа «AfterFX собирает проект» (задание FJ). seen — уже обработанные строки.
    timelines — словарь состояния {"stems": [...], "built": 0} для режима «Один на всё»
    (задание HC). Возвращает число новых таймлайнов (при timelines) либо новых «evalFile ok»
    (для ETA сборки, задание FK)."""
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


def _comp_to_stem(name, comps):
    """Имя композиции из маркера aerender («Output To: …» или «Finished composition: …») -> стем .jsx.
    ИМЯ — это meta["name"], а НЕ стем файла (задание FJ): файл 01_C0233.xml может дать
    композицию C0233. Нет совпадения — None (вызывающий берёт следующий по порядку)."""
    if not name:
        return None
    for _s, _cn, _fr in comps:
        if _cn == name or _s == name:
            return _s
    return None


# Скользящая скорость ETA (задание FK): окно 30-60 с, не среднее с начала прогона.
_ETA_WINDOW = 45.0


def _eta_secs(samples, total_frames, done_frames):
    """ETA в секундах по скользящей скорости кадров/с: (осталось кадров) / скорость.
    samples — [(t, frame), …] последних замеров «кадр отрендерен на секунду t».
    Пока замеров меньше 15-20 секунд — скорости нет, вернуть None (прочерк)."""
    if len(samples) < 2:
        return None
    t0 = samples[0][0]
    t1 = samples[-1][0]
    if t1 - t0 < 15.0:          # замеров меньше 15-20 с — честнее прочерк, чем выдуманное число
        return None
    f0 = samples[0][1]
    f1 = samples[-1][1]
    dt = t1 - t0
    if dt <= 0 or f1 <= f0:
        return None
    speed = (f1 - f0) / dt      # кадров/с за окно
    if speed <= 0:
        return None
    remaining = max(0, total_frames - done_frames)
    return remaining / speed


def _run_proc_batch(aer, aep_call, comps, render_dir,
                    p_aep_end=_PHASE_AEP_END, t_render_base=DEFAULT_RENDER_SEC,
                    has_stats=False):
    """aerender для общего проекта: ДВА живых прогресса (задание FK/FL). comps —
    [(стем, имя_композиции, кадры), …]: стем адресует строку очереди, имя композиции —
    файл на диске (FJ), кадры — M для процента текущей композиции, когда aerender его
    не печатает, и для ETA.
    - имя текущей композиции: из строки «Output To:» (задание FL), запасной — по порядку;
    - кадры текущей композиции: из блока Start/End/Duration/Frame Rate (задание FL),
      запасной — _comp_frames из .jsx;
    - pct элемента: доля ТЕКУЩЕЙ композиции (_parse_progress, кадры (N)/(N/M));
    - RJOB["pct"]: общий по набору = (готовых + доля текущей) / всего — монотонно,
      без прыжка к 1.0 на «Finished composition» (это конец ОДНОЙ, а не всего);
    - RJOB["cur"]: имя текущей композиции;
    - RJOB["eta"]: ETA рендера в секундах (скользящая скорость за 30-60 с)."""
    try:
        p = subprocess.Popen([aer, "-project", aep_call],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
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
    try:
        for line in p.stdout:
            line = line.rstrip("\r\n")
            if not line.strip():
                continue
            remit(line)

            # Блок параметров композиции от AE (задание FL)
            m_dur = _TC_DUR.search(line)
            if m_dur:
                hdr_dur = m_dur.group(1)
            fps_val = _parse_frame_rate(line)
            if fps_val is not None:
                hdr_fps = fps_val
            m_start = _TC_START.search(line)
            if m_start:
                hdr_start = m_start.group(1)
            m_end = _TC_END.search(line)
            if m_end:
                hdr_end = m_end.group(1)

            ae_frames = None
            if hdr_dur and hdr_fps:
                ae_frames = _parse_timecode(hdr_dur, hdr_fps)
            elif hdr_start and hdr_end and hdr_fps:
                sf = _parse_timecode(hdr_start, hdr_fps)
                ef = _parse_timecode(hdr_end, hdr_fps)
                if sf is not None and ef is not None and ef >= sf:
                    ae_frames = ef - sf + 1

            # Имя текущей композиции — из «Output To:» (задание FL)
            out_to = _parse_output_to(line)
            if out_to:
                stem = _comp_to_stem(out_to, comps)
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
                # имя композиции из маркера — в кавычках с точкой, см. _finished_comp_name
                # (задание FJ/FK/FL); ищем по нему ролик — это meta["name"], а НЕ стем .jsx
                name = _finished_comp_name(line)
                stem = _comp_to_stem(name, comps) if name else None
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
                    continue

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
                pr = _parse_progress(line, cur_frames)
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
                                while samples and (now - t_start) - samples[0][0] > _ETA_WINDOW:
                                    samples.pop(0)
                            if (now - t_start) >= 15.0 and len(samples) > 1:
                                eta_calc = _eta_secs(samples, total_frames, rendered)
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
    finally:
        rc = p.wait()
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
    for stem, comp_name, _fr in comps:
        if stem not in done_names:
            mov = os.path.join(render_dir, comp_name + ".mov")
            if _rendered_ok(mov):
                item_done(RJOB, RLOCK, stem, mov, bucket="result")
            else:
                item_fail(RJOB, RLOCK, stem,
                          "aerender не отрендерил (см. вывод aerender выше)", bucket="failed")
    return rc


def _run_render_job(jobs, outdir, render_dir):
    """Диспетчер рендера. Набор из ОДНОГО ролика — ровно прежний путь
    (_run_render_single: безголовый .jsx, AfterFX, aerender). Набор из нескольких —
    ВСЕГДА _run_render_combined: один проект AE и один общий Reelsi_all.jsx
    (решение пользователя 2026-09-11; радио multimode на рендер не влияет)."""
    try:
        from .build import _norm_build_jobs
        norm = _norm_build_jobs(jobs)
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
    except Exception:
        import traceback
        remit("ОШИБКА:\n{tb}", tb=traceback.format_exc().strip().splitlines()[-1])
        with RLOCK:
            RJOB["failed"].append({"name": "рендер", "reason": "внутренняя ошибка"})
    finally:
        with RLOCK:
            RJOB.update(running=False, done=True, cur="",
                        pct=(1.0 if RJOB["result"] and not RJOB["failed"] else (RJOB["pct"] or 0)))
        # Лок задач держим до самого конца рендера — включая «Стоп» и ошибку
        # (задание GZ, п. C: без него поверх рендера стартовала нарезка).
        _cross_lock_release()


@bp.route("/api/render_run", methods=["POST"])
def api_render_run():
    """Запустить безголовый рендер. body: {jobs, outdir?, render_dir}.
    Свой RJOB, но ОБЩИЙ лок задач с нарезкой и сборкой .jsx (задание GZ, п. C):
    две тяжёлые задачи на одной видеокарте одновременно не идут."""
    d = request.get_json() or {}
    jobs = d.get("jobs") or []
    try:
        if not jobs:
            raise SystemExit(umsg("set_empty", "Набор пуст"))
        render_dir = (d.get("render_dir") or "").strip().strip('"') or default_render_dir()
        try:
            os.makedirs(render_dir, exist_ok=True)
        except OSError as e:
            raise SystemExit(umsg("render_outdir",
                                  f"не создать папку вывода: {render_dir} — {e}",
                                  dir=render_dir, err=str(e)))
        with RLOCK:
            if RJOB["running"]:
                raise SystemExit(umsg("render_busy",
                                      "Рендер уже идёт — дождись конца или нажми «Остановить»"))
        # Общий лок задач (тот же, что берёт job_start у нарезки и сборки): без него
        # рендер стартовал поверх идущего джоба — две тяжёлые задачи на одной
        # видеокарте, да ещё обе пишут <outdir>/<stem>.jsx. JOB["running"] проверяем
        # отдельно: между «занял JOB» и «взял лок» у job_start есть окно.
        with LOCK:
            job_busy = JOB["running"]
        if job_busy or not _cross_lock_acquire():
            raise SystemExit(umsg("busy_wait",
                                  "Уже выполняется другая задача — дождись или смотри Логи"))
        try:
            with RLOCK:
                RJOB.update(running=True, done=False, log=[], pct=None, cur="", ae="",
                            out_dir=render_dir, result=[], failed=[], cancel=False,
                            items=[], eta=None, eta_phase=None, eta_total=None,
                            eta_preliminary=False, stage_label=None, stage_done=0,
                            stage_total=0)
            threading.Thread(target=_run_render_job,
                             args=(jobs, (d.get("outdir") or "").strip().strip('"'),
                                   render_dir),
                             daemon=True).start()
        except Exception:
            _cross_lock_release()          # поток не родился — лок не оставляем занятым
            raise
        return jsonify(ok=True)
    except SystemExit as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/render_status")
def api_render_status():
    """Состояние рендер-джоба: running/done/pct/cur/ae/out_dir/result/failed/eta + лог."""
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
                       log=RJOB["log"][-200:])
