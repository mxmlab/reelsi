# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Движок безголового рендера в After Effects — всё, что НЕ держит состояние задания.

Задание NN. До него движок жил внутри `api/render.py` (1912 строк, из которых в роутах
около 70), и оттого был недоступен ни CLI, ни `doctor.py` без импорта Flask-слоя:
`doctor.py` брал поиск AE прямо из `api.render` — инверсия слоёв.

Разрез — по признаку «есть ли у функции состояние задания». Здесь только чистое: поиск
и вызов AE, разбор вывода `aerender` (регулярки, таймкод, имя композиции, доля кадров),
ETA, статистика длительностей фаз, проверки результата. Ни одна функция отсюда не
трогает RJOB/RLOCK/RPROC, не зовёт `remit` и не запускает процессы задания — это
осталось в `api/render.py` вместе с оркестрацией и роутами.

Имена здесь — БЕЗ ведущего подчёркивания: это публичный интерфейс модуля, им пользуются
и `api/render.py`, и `doctor.py`, а не внутренности одного файла.
"""
import os, re, subprocess, shutil, hashlib, json, time as _time
from typing import Any, Iterable, Sequence

from core.fileio import atomic_json_dump
from core import paths
from core.app_meta import env
from core.umsg import ReelsiError
from core.applog import get_logger

log = get_logger(__name__)

# Дефолтные средние времена на 1 ролик при отсутствии накопленной статистики:
# сняты с реального прогона на 12 роликов (5 с сборка таймлайна, 36 с сборка в проект, 83 с рендер).
DEFAULT_JSX_SEC = 5.0
DEFAULT_AEP_SEC = 36.0
DEFAULT_RENDER_SEC = 83.0

# Сторож простоя AfterFX. Отсчёт — от последней НОВОЙ строки
# активности (журнала/вывода), а не от старта: нормальная сборка тяжёлого ролика идёт
# минутами, рубить её нельзя. Простоял меньше warn — пишем в лог предупреждение,
# дольше kill — снимаем процесс и завершаем прогон честной ошибкой.
AE_STALL_WARN_SEC = 5 * 60      # предупреждение: AfterFX молчит N минут
AE_STALL_KILL_SEC = 20 * 60     # снятие: AfterFX не отвечает N минут
AE_FAST_EXIT_SEC = 5.0          # выход AfterFX быстрее — «мгновенный» (ушёл в открытую копию)
AE_STALLED = -137               # rc _run_proc_afx: процесс снят сторожем простоя (не код выхода)

DEF_TOT = DEFAULT_JSX_SEC + DEFAULT_AEP_SEC + DEFAULT_RENDER_SEC
PHASE_JSX_END = DEFAULT_JSX_SEC / DEF_TOT
PHASE_AEP_END = (DEFAULT_JSX_SEC + DEFAULT_AEP_SEC) / DEF_TOT


def get_stats_path() -> str:
    """Путь к render_stats.json. REELSI_RENDER_STATS изолирует тестовый профиль (порт 5098)."""
    return env("RENDER_STATS") or os.path.join(paths.ROOT, "render_stats.json")


def load_render_stats() -> tuple[dict[str, Any], bool]:
    """Загрузить накопленную статистику рендеров из render_stats.json.
    Возвращает (stats_dict, has_history_bool)."""
    p = get_stats_path()
    if not os.path.isfile(p):
        return {"runs": []}, False
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        runs = data.get("runs", []) if isinstance(data, dict) else []
        return (data if isinstance(data, dict) else {"runs": []}), bool(runs)
    except ReelsiError: raise
    except Exception:
        return {"runs": []}, False


def get_baseline_phase_durations(n_clips: int) -> tuple[float, float, float, bool]:
    """Ожидаемые длительности фаз (jsx, aep, render) в секундах для n_clips.
    Возвращает (t_jsx, t_aep, t_render, has_history)."""
    data, has_history = load_render_stats()
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


def save_render_stats(n: int, jsx_sec: float, aep_sec: float, render_sec: float) -> None:
    """Сохранить фактические времена фаз завершенного прогона в render_stats.json."""
    if n <= 0:
        return
    try:
        p = get_stats_path()
        data, _ = load_render_stats()
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
    except ReelsiError: raise
    except Exception as ex:
        log.warning("статистика рендера не записалась: %s — "
                    "прогноз времени будет по умолчанию", ex)


def calc_phase_bounds(t_jsx: float, t_aep: float, t_render: float) -> tuple[float, float]:
    """Вычислить границы шкалы прогресса (0.0..1.0) для трех фаз по ожидаемым временам:
    возвращает (phase_jsx_end, phase_aep_end)."""
    t_tot = max(1.0, float(t_jsx + t_aep + t_render))
    p1 = max(0.01, min(0.5, float(t_jsx) / t_tot))
    p2 = max(p1 + 0.01, min(0.95, float(t_jsx + t_aep) / t_tot))
    return p1, p2


def predict_aep_times(measured_durations: Sequence[float], total_n: int,
                      default_per_clip: float = DEFAULT_AEP_SEC) -> list[float]:
    """Нелинейная модель сборки в проект: время n-го ролика t(n) = a + b*(n-1).
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

    weights: list[float] = []
    for i in range(total_n):
        if i < k:
            weights.append(max(0.1, ys[i]))
        else:
            pred = a + b * i
            pred = max(0.5, pred)
            weights.append(pred)
    return weights


def default_render_dir() -> str:
    """Папка вывода по умолчанию — `exp` РЯДОМ с репозиторием (он публичный,
    чужим рендерам в нём не место), а не внутри. Нет папки — создадим при запуске."""
    repo = paths.ROOT
    return os.path.join(os.path.dirname(repo), "exp")


def find_ae() -> tuple[str, str, str] | None:
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
    found: list[tuple[int, str, str, str]] = []
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


def ae_running() -> bool:
    """Открыта ли уже копия After Effects (AfterFX.exe)? ЕДИНСТВЕННЫЙ источник этого
    знания для обоих путей рендера — маленькая и легко подменяется в тестах. Проверка через tasklist: psutil в проекте нет. Открытая копия
    «съедает» наш AfterFX -noui: он возвращает код 0 за 0 секунд, а скрипт уезжает в
    НЕЁ — переписывает её проект и закрывает без вопроса о сохранении."""
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq AfterFX.exe", "/NH"],
                             capture_output=True, timeout=15)
    except ReelsiError: raise
    except Exception:
        return False        # не вышло проверить — прогон не блокируем
    return b"AfterFX.exe" in (out.stdout or b"")


def short_path(p: str) -> str | None:
    """8.3-имя Windows-пути (короткое, без пробелов по построению). None — не вышло
    (том без коротких имён, генерация 8.3 отключена). Пробел в пути к .jsx рвёт
    аргумент `-r` AfterFX: скрипт не запускается вовсе, а процесс молча выходит с
    кодом 0."""
    try:
        import ctypes
        # windll есть только в Windows-сборках ctypes; getattr — потому что mypy в
        # Linux-режиме (CI) этого атрибута в типах не видит, а `# type: ignore` под
        # Windows оказался бы лишним и упал бы на warn_unused_ignores.
        windll = getattr(ctypes, "windll", None)
        if windll is None:
            return None
        buf = ctypes.create_unicode_buffer(512)
        n = windll.kernel32.GetShortPathNameW(p, buf, 512)
        return buf.value if n else None
    except ReelsiError: raise
    except Exception:
        return None


def jsx_call_path(jp: str) -> tuple[str, str]:
    """Путь к .jsx для аргумента `-r`. Возвращает (путь, причина): причина непустая,
    если путь подменён. Сначала — короткое 8.3-имя (пробелов в нём нет). Не вышло
    (том без 8.3) — кладём копию под именем без пробелов рядом с целевым и зовём её,
    не молча: иначе рендер тихо не запустится. Задание CD."""
    s = short_path(jp)
    if s:
        return s, ""
    stem = os.path.splitext(os.path.basename(jp))[0]
    alt = os.path.join(os.path.dirname(jp), re.sub(r"\s+", "", stem) + "_headless.jsx")
    shutil.copyfile(jp, alt)
    return alt, (f"  короткое имя для {os.path.basename(jp)} не вышло (том без 8.3-имён) — "
                 f"выполняю копию {os.path.basename(alt)}")


def aep_call_path(aep: str) -> tuple[str, str]:
    """Путь к .aep для аргумента `-project` aerender. Возвращает (путь, причина):
    причина непустая, когда путь подменён. aerender читает аргументы командной строки
    как ANSI, и на кодовой странице 1252 кириллица превращается в '????' — «Path is
    not valid», рендер не запускается. Сначала короткое 8.3-имя; не
    вышло (том без 8.3) — копия рядом с целевым под ASCII-именем с хэшем стема, чтобы
    два кириллических имени не затёрли друг друга."""
    s = short_path(aep)
    if s:
        return s, ""
    stem = os.path.splitext(os.path.basename(aep))[0]
    alt = os.path.join(os.path.dirname(aep),
                       re.sub(r"[^A-Za-z0-9_.-]+", "_", stem) + "_" +
                       hashlib.md5(stem.encode("utf-8")).hexdigest()[:6] + ".aep")
    shutil.copyfile(aep, alt)
    return alt, (f"  короткое имя для {os.path.basename(aep)} не вышло (том без 8.3-имён) — "
                 f"рендерю копию {os.path.basename(alt)}")


def rendered_ok(path: str) -> bool:
    """Файл на месте и непустой — единственный честный признак, что aerender реально
    отрендерил. Коду возврата не верить: на «Path is not valid» он вернул 0."""
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def remove_stale_aelog(aelog: str) -> str | None:
    """Перед запуском AfterFX удалить журнал ПРОШЛОГО прогона.
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


def aep_updated_by_run(aep: str, mtime_before: float | None) -> bool:
    """Отличить «.aep собран ЭТИМ прогоном» от «остался от прошлого». .aep стирать нельзя: человек мог доработать проект руками, поэтому
    вместо факта «файл существует» — факт «файл обновлён этим прогоном»: файла не
    было и он появился, либо время изменения выросло. mtime_before — os.path.getmtime
    ДО запуска AfterFX (None — файла не было)."""
    try:
        now = os.path.getmtime(aep) if os.path.isfile(aep) else None
    except OSError:
        return False
    return now is not None and (mtime_before is None or now > mtime_before)


def comp_frames(jsx: str) -> int | None:
    """Общее число кадров композиции — честное, из плана (.jsx несёт FPS/DUR и
    удлинение под хвостовой дисклеймер). Рендер-процент = кадр / это число, без
    эвристик. Не прочиталось — None (тогда процент только по (N/M) из вывода)."""
    # FPS в .jsx бывает дробным: NTSC-секвенция 29.97 печатается как 29.97003
    # С `(\d+)` такой план не читался вовсе, и процент рендера уходил
    # на запасной путь «N из M» — без потери данных, но грубее.
    m = re.search(r"FPS=([\d.]+), DUR=([\d.]+)", jsx)
    if not m:
        return None
    fps, dur = float(m.group(1)), float(m.group(2))
    extra = re.search(r"Math\.max\(DUR,1\)(\+[\d.]+)?, FPS", jsx)
    add = float(extra.group(1)) if extra and extra.group(1) else 0.0
    total = round((max(dur, 1.0) + add) * fps)
    return total or None


FRAME = re.compile(r"\((\d+)(?:/(\d+))?\)")
END_MARK = ("Finished composition", "Total Time Elapsed")
# Имя композиции из маркера aerender. Реальный вывод: PROGRESS: 8/26/2026 12:55:08 AM:
# Finished composition "C0250". — имя В КАВЫЧКАХ и с точкой в конце; без кавычек тоже
# бывает (другая локаль). Сначала кавычки, потом запасной вариант.
COMP_FIN = re.compile(r'Finished composition\s+"([^"]+)"')
COMP_FIN_BARE = re.compile(r"Finished composition\s*[:：]?\s*([^\s.]+)")

# Задание FL: разбор заголовка текущей композиции из вывода aerender.
# Имя композиции — из строки Output To: (известно В НАЧАЛЕ её рендера).
OUTPUT_TO = re.compile(r"Output To:\s*(.+)", re.I)
# Кадры композиции от AE — из блока Start / End / Duration / Frame Rate.
TC_START = re.compile(r"Start:\s*(\d+:\d{2}:\d{2}:\d{2})", re.I)
TC_END = re.compile(r"End:\s*(\d+:\d{2}:\d{2}:\d{2})", re.I)
TC_DUR = re.compile(r"Duration:\s*(\d+:\d{2}:\d{2}:\d{2})", re.I)
FRAME_RATE = re.compile(r"Frame Rate:\s*([\d.]+)", re.I)


def parse_output_to(line: str) -> str | None:
    """Имя композиции из строки «Output To: ПУТЬ».
    Возвращает имя файла без пути и расширения (stem), или None."""
    m = OUTPUT_TO.search(line)
    if not m:
        return None
    raw = m.group(1).strip().strip('"').strip("'")
    if not raw:
        return None
    norm = raw.replace("\\", "/")
    base = norm.rsplit("/", 1)[-1]
    stem, _ = os.path.splitext(base)
    return stem or None


def parse_timecode(tc: str | None, fps: float | None) -> int | None:
    """Таймкод ч:мм:сс:кадры + fps -> общее число кадров."""
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


def parse_frame_rate(line: str) -> float | None:
    """FPS из строки «Frame Rate: 60.00 (comp)». Возвращает float или None."""
    m = FRAME_RATE.search(line)
    if not m:
        return None
    try:
        return float(m.group(1))
    except (ValueError, TypeError):
        return None


def finished_comp_name(line: str) -> str | None:
    """Имя композиции из строки «Finished composition ...». Возвращает имя без кавычек
    и точки, или None, если маркер не тот."""
    m = COMP_FIN.search(line)
    if m:
        return m.group(1).strip()
    m = COMP_FIN_BARE.search(line)
    if m:
        return m.group(1).strip().strip('"').rstrip(".")
    return None


def parse_progress(line: str, total: int | None) -> float | None:
    """(N) или (N/M) в строке вывода aerender -> доля. M в строке — авторитет;
    без неё берём total из плана. Finished composition / Total Time Elapsed —
    маркеры конца: 100%."""
    if any(m in line for m in END_MARK):
        return 1.0
    m = FRAME.search(line)
    if not m:
        return None
    frame = int(m.group(1))
    t = int(m.group(2)) if m.group(2) else total
    if not t or t <= 0:
        return None
    return min(1.0, frame / t)


def comp_to_stem(name: str | None, comps: Iterable[tuple[str, str, Any]]) -> str | None:
    """Имя композиции из маркера aerender («Output To: …» или «Finished composition: …») -> стем .jsx.
    ИМЯ — это meta["name"], а НЕ стем файла: файл 01_C0233.xml может дать
    композицию C0233. Нет совпадения — None (вызывающий берёт следующий по порядку)."""
    if not name:
        return None
    for _s, _cn, _fr in comps:
        if _cn == name or _s == name:
            return _s
    return None


# Скользящая скорость ETA: окно 30-60 с, не среднее с начала прогона.
ETA_WINDOW = 45.0


def eta_secs(samples: Sequence[tuple[float, int]], total_frames: int,
             done_frames: int) -> float | None:
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
