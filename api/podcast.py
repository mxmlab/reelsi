# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Подкаст → рилсы: роуты, джоб и конвейер (порция П3).

Чистая часть — core/podcast.py (папки, проект, команды yt-dlp), расшифровка и ИИ-выбор —
core/podcast_ai.py. Здесь то, что правда про HTTP и процессы: состояние джоба (PDJOB),
запуск yt-dlp с чтением вывода в фоне и сторожем простоя, конвейер «скачать → расшифровать
→ выбрать» и отмена.

ПОЧЕМУ статус хранится в podcast.json, а не только в памяти джоба: расшифровка идёт
минуты, и перезапуск сервера не должен оставить подкаст в вечном «идёт». По статусу
конвейер понимает, с какого шага продолжить.

Джоб свой (PDJOB), как GDJOB: он не занимает общий JOB нарезки. Тяжёлые шаги
(расшифровка) берут общий межпроцессный лок задач, выбор кусков идёт в облаке без него.
"""
import json
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from flask import Response, jsonify, request

from ._core import (_cross_lock_acquire, _cross_lock_release, bp, job_log_add, job_log_since,
                    jstr, kill_tree, umsg_err)
from core import fileio, podcast, podcast_ai, speakers
from core._pathguard import is_within
from core.jobproc import STALLED_RC, run_watched
from core.applog import get_logger
from core.clipstore import TRASH_DIR as POD_TRASH_DIR
from core.clipstore import cleanup_failed_entry, new_trash_entry, trash_move_in, write_trash_meta
from core.umsg import ReelsiError, umsg
from core.xml2ae.parse import Cancelled

log = get_logger("reelsi.podcast")

# Сторож простоя yt-dlp: 10 минут без смены прогресса -> процесс снимаем деревом.
PD_STALL_SEC = 10 * 60
_PD_STALLED = STALLED_RC           # код «снято сторожем» — один на все задания (core.jobproc)
PROBE_TIMEOUT_SEC = 120
REELS_MIN, REELS_MAX = 1, 30

# Куда откатываем статус при отмене: на последний достигнутый шаг, а не в ошибку.
_PD_BACK = {"download": "new", "transcribe": "downloaded", "choose": "transcribed"}

# Поля, которые сбрасываются в начале каждого запуска (лог и хвост — отдельно).
PDFRESH: dict[str, Any] = {"cur": "", "stage": "", "pct": None, "size": "", "speed": "",
                           "eta": "", "cancel": False, "cancelled": False, "pi": 0, "pn": 0}
# failed: None пока идём, True/False по итогу. cancelled — «Стоп» нажал человек: не ошибка.
# folders — подкасты, которые пишет джоб «Все рилсы» (dir там — сама папка подкастов): их не удаляем.
PDJOB: dict[str, Any] = dict(PDFRESH, running=False, done=False, failed=None, log=[],
                             log_base=0, dir="", started=0, reels=[], i=0, n=0, folders=[])
# reels / i / n — перевод в рилсы (П7). Списки в PDFRESH не кладём: `PDJOB.update(PDFRESH, …)`
# отдал бы всем запускам один и тот же список, и `reels` одного прогона попал бы в следующий.
PDLOCK = threading.Lock()
PDPROC: Any = None  # текущий процесс yt-dlp — для отмены и сторожа простоя


# Шаг остановлен кнопкой «Стоп»: конвейер откатывает статус, а не пишет ошибку. Тип один с
# разбором камер и рефреймом (core.xml2ae.parse.Cancelled): раньше у подкаста был свой, и
# Cancelled из модулей ловился как ошибка, а не как отмена.
_PodcastCancelled = Cancelled


def _pcancelled() -> bool:
    with PDLOCK:
        return bool(PDJOB.get("cancel"))


def _set_pd(**fields: Any) -> None:
    with PDLOCK:
        PDJOB.update(fields)


def _pemit(line: Any = "", /, **vars: Any) -> None:
    """Строка лога джоба. Понимает emit('текст') и emit('Шаблон {x}', x=…) как console_emit.

    ПОЧЕМУ запись, а не готовый текст: строки ИИ приходят шаблоном с форматом
    (`{el:.0f}с`), и если подставить переменные здесь, формат на строке уже не сработает —
    выходило «3.7с» вместо «4с». Главный джоб пишет `log_entry` (api/_core.py), клиент
    превращает запись в текст тем же `fmtLog` → `t()`, что и главный прогресс, — и перевод,
    и формат чисел работают одинаково.
    """
    job_log_add(PDJOB, PDLOCK, line, vars)


def _emit(line: Any = "", /, **vars: Any) -> None:
    """То же, что _pemit, под именем `_emit`: core.app_meta.wrap_emit отдаёт такой колбэк как
    есть, и шаблон с переменными доходит до клиента целым — строка переводится по en.json, а не
    печатается уже подставленным русским текстом (как у `_pemit`, которого wrap_emit не узнаёт)."""
    _pemit(line, **vars)


def _pstat(fields: dict[str, Any]) -> None:
    """Поля прогресса yt-dlp в состояние джоба. Склейка — не отдельный шаг, а подпись."""
    f = dict(fields)
    if f.pop("stage", None) == "merge":
        f["cur"] = "склейка видео и звука"
    _set_pd(**f)


def podcast_kill() -> None:
    """«Стоп» из интерфейса: флаг джобу, убить текущий yt-dlp деревом, снять ИИ-вызов."""
    with PDLOCK:
        PDJOB["cancel"] = True
        p = PDPROC
    if p and p.poll() is None:
        kill_tree(p)
    # Ленивый импорт: aicut тяжёлый, а без подкаста он не нужен.
    from core import aicut
    aicut.cancel_call()  # рвёт ретраи ИИ-вызова выбора кусков


def _set_pdproc(p: subprocess.Popen[str] | None) -> None:
    global PDPROC
    with PDLOCK:
        PDPROC = p


def _kill_pd(p: Any) -> None:
    """Снять процесс yt-dlp деревом. Отдельной функцией — чтобы подмена в тестах действовала
    на сторожа так же, как для rclone (`api.gdrive._kill_proc`)."""
    kill_tree(p)


def _run_ytdlp(cmd: list[str], emit: Callable[..., Any],
               stat: Callable[..., Any] | None = None) -> int:
    """yt-dlp сабпроцессом: вывод разбираем здесь, запуск и сторож — `core.jobproc.run_watched`.

    Поля прогресса уходят в `stat` (статус страницы), остальные строки — в лог; прогресс
    в лог — раз в 10 % (как у rclone). Простой считается по ИЗМЕНЕНИЮ прогресса: вставшая
    передача не должна выглядеть живой — поэтому строка прогресса, не сдвинувшая процент,
    активностью не считается.
    """
    logged = -10                       # -10, а не 0: первый блок (0%) отмечает старт в логе
    last_pct: list[float | None] = [None]

    def _handle(raw: str) -> bool:
        nonlocal logged
        line = raw.rstrip()
        if not line.strip():
            return False
        f = podcast.progress_fields(line)
        if f is None:
            emit(line)                 # не прогресс — живое событие yt-dlp
            return True
        if stat:
            stat(f)
        if "stage" in f:               # [Merger]: склейка видео и звука в mkv
            emit("склейка видео и звука в mkv")
            return True
        pct = f.get("pct")
        if pct is None:
            return False
        active = False
        if pct != last_pct[0]:
            active = True
            # Процент упал — пошёл второй поток (звук после видео): лог снова с нуля.
            if last_pct[0] is not None and pct < last_pct[0]:
                logged = -10
            last_pct[0] = pct
        if pct >= logged + 10:
            logged = pct - pct % 10
            emit(line)
        return active

    return run_watched(
        cmd, _handle, set_proc=_set_pdproc, cancelled=_pcancelled, stall_sec=PD_STALL_SEC,
        on_stall=lambda: emit(f"⚠ yt-dlp молчит {PD_STALL_SEC // 60} минут — скачивание снято "
                              f"(сторож простоя)"),
        on_dead=lambda: emit("⚠ поток чтения вывода yt-dlp умер — активность не отслеживается, "
                             "скачивание не снимаю по простою (снять — «Стоп»)"),
        kill=_kill_pd)


# --------------------------------------------------------------------------- #
# Конвейер: скачать → расшифровать → выбрать
# --------------------------------------------------------------------------- #

def _err_text(e: BaseException) -> str:
    txt = str(umsg_err(e).get("error") or "").strip()
    return txt or type(e).__name__


def _set_status(folder: str, proj: dict[str, Any], status: str,
                error: str | None = None) -> None:
    """Статус (и ошибку) — в проект и на диск. Пишем сразу: статус переживает перезапуск."""
    proj["status"] = status
    if error is not None:
        proj["error"] = error
    podcast.save_project(folder, proj)


def _resume_step(folder: str, proj: dict[str, Any]) -> str:
    """С какого шага продолжить по статусу проекта: download / transcribe / choose / ''."""
    st = str(proj.get("status") or "")
    if not proj.get("source") and st in ("new", "downloading", "error"):
        if not proj.get("url"):
            raise ReelsiError(umsg("podcast_no_source",
                                   "Нет ни файла, ни ссылки — обрабатывать нечего"))
        return "download"
    if st in ("downloaded", "transcribing", "downloading"):
        return "transcribe"
    if st in ("transcribed", "choosing"):
        return "choose"
    if st == "error":  # ошибка после скачивания: расшифровка есть — выбираем, нет — заново
        return "choose" if podcast_ai.load_words(folder) else "transcribe"
    return ""          # ready и прочие: делать нечего


def _load(folder: str) -> dict[str, Any]:
    proj = podcast.load_project(folder)
    if proj is None:
        raise ReelsiError(umsg("podcast_bad_project",
                               "Файл проекта подкаста повреждён или от другой версии"))
    return proj


def _cams_step(folder: str, proj: dict[str, Any]) -> None:
    """Стадия «разбор камер» (П11): cams.json один раз на источник. ВЫЗЫВАТЬ ПОД ОБЩИМ ЛОКОМ."""
    from core import podcast_cams  # тяжёлый (numpy, трек): грузим только когда разбираем камеры
    _set_pd(stage="cams", cur="разбор камер", pct=None)
    podcast_cams.analyze(folder, proj, _emit, _pcancelled)


def _cams_missing(folder: str, proj: dict[str, Any]) -> bool:
    """Разбор камер ещё не сделан: есть источник и расшифровка, а годного cams.json нет."""
    from core import podcast_cams
    return bool(proj.get("source")) and bool(podcast_ai.load_words(folder)) \
        and podcast_cams.load(folder, proj) is None


def _pipeline(folder: str) -> None:
    """Конвейер подкаста по статусам: идёт с последнего достигнутого шага.

    Ошибка → статус error и текст в `error`. Отмена → статус назад, `cancelled`, не `failed`.
    Вызывается потоком из `/api/podcast_run`, а тесты зовут его напрямую.
    """
    _set_pd(running=True, done=False, failed=None, dir=folder, started=int(time.time()),
            cancel=False, cancelled=False, stage="", cur="запуск")
    ok = False
    cancelled = False
    step = ""
    proj: dict[str, Any] | None = None
    try:
        proj = _load(folder)
        step = _resume_step(folder, proj)
        if step == "download":
            _set_pd(stage="download", cur="скачиваю видео", pct=0)
            _set_status(folder, proj, "downloading", error="")
            # Проект, созданный до П16, дорожки не знает: без неё yt-dlp выбрал бы звук сам.
            # Берём оригинал сейчас и записываем в проект, чтобы карточка показала, что скачано.
            if proj.get("url") and not (proj.get("audio_track") or {}).get("format_id"):
                proj["audio_track"] = _pick_track(proj["url"], None)
                podcast.save_project(folder, proj)
            fid = (proj.get("audio_track") or {}).get("format_id")
            code = _run_ytdlp(podcast.download_cmd(proj["url"], folder, fid), _pemit, _pstat)
            if code != 0:
                if _pcancelled():
                    raise _PodcastCancelled()
                raise ReelsiError(umsg("podcast_download_failed",
                                       "yt-dlp не скачал видео (код {code}) — смотри лог",
                                       code=code))
            src = podcast.downloaded_source(folder)
            if src is None:
                raise ReelsiError(umsg("podcast_no_source_file",
                                       "После скачивания нет файла source.* — смотри лог"))
            proj["source"] = os.path.abspath(src)
            _set_status(folder, proj, "downloaded", error="")
            _pemit("видео скачано: {name}", name=os.path.basename(src))
            step = "transcribe"
        if step == "transcribe":
            if not _cross_lock_acquire():
                raise ReelsiError(umsg("podcast_lock_busy",
                                       "Уже выполняется другая тяжёлая задача"))
            try:
                _set_pd(stage="transcribe", cur="расшифровка", pct=None)
                _set_status(folder, proj, "transcribing", error="")
                podcast_ai.transcribe(folder, proj, _pemit, _pcancelled)
                # Расшифровка уже на диске: отмена разбора камер откатит статус на «transcribed»,
                # а не на «downloaded» (тогда расшифровка пошла бы заново).
                step = "choose"
                # Разбор камер — сразу после расшифровки, под тем же локом (RVM и ffmpeg).
                _cams_step(folder, proj)
            finally:
                # Лок отпускаем при ЛЮБОМ исходе: иначе после ошибки весь подкаст и нарезка
                # до перезапуска сервера ждали бы «Уже выполняется».
                _cross_lock_release()
            _set_status(folder, proj, "transcribed", error="")
            step = "choose"
        # Проект с расшифровкой, но без cams.json (старый проект, отмена разбора): «Продолжить»
        # делает разбор камер и дальше выбор кусков, если он нужен; готовые куски — только разбор.
        ran_cams = False
        if step in ("choose", "") and _cams_missing(folder, proj):
            if not _cross_lock_acquire():
                raise ReelsiError(umsg("podcast_lock_busy",
                                       "Уже выполняется другая тяжёлая задача"))
            try:
                _cams_step(folder, proj)
            finally:
                _cross_lock_release()
            ran_cams = True
        if step == "choose":
            _set_pd(stage="choose", cur="ИИ выбирает куски")
            _set_status(folder, proj, "choosing", error="")
            warns = podcast_ai.choose(folder, proj, _pemit)
            for w in warns:
                _pemit("⚠ {w}", w=w)
        if step == "" and not ran_cams:
            _pemit("подкаст не требует действий: статус {st}", st=str(proj.get("status")))
        ok = True
        _set_pd(cur="готово")
    except (Exception, SystemExit) as e:
        if isinstance(e, _PodcastCancelled) or _pcancelled():
            cancelled = True
            _pemit("остановлено")
            _set_pd(cur="остановлено")
            back = _PD_BACK.get(step, "")
            if proj is not None and back:
                try:
                    _set_status(folder, proj, back, error="")
                except Exception:
                    log.warning("подкаст: не удалось откатить статус", exc_info=True)
        else:
            txt = _err_text(e)
            if not isinstance(e, ReelsiError):
                log.warning("подкаст %s: %s", folder, txt, exc_info=True)
            _pemit("ОШИБКА: {txt}", txt=txt)
            _set_pd(cur=f"ошибка: {txt}")
            if proj is not None:
                try:
                    _set_status(folder, proj, "error", error=txt)
                except Exception:
                    log.warning("подкаст %s: не удалось записать ошибку", folder, exc_info=True)
    finally:
        _set_pd(running=False, done=True, cancelled=cancelled,
                failed=(not ok) and not cancelled, pct=None)


def _has_reel(seg: dict[str, Any]) -> bool:
    """У куска есть рилс на диске. Запись `reel` без файла (стёрли руками) — рилса нет."""
    r = str(seg.get("reel") or "")
    return bool(r) and os.path.isfile(r)


def _plan_convert(folder: str, ids: list[str] | None) -> tuple[dict[str, Any], list[tuple[int, dict[str, Any]]]]:
    """Проверки перевода БЕЗ лока и без смены статуса: статус, куски, выбор. Отказ — ReelsiError.

    Возвращает проект и пары (index, кусок): index — позиция куска среди ВСЕХ кусков по `start`,
    номер в имени рилса от выбора не зависит.
    """
    proj = _load(folder)
    # error — после падения перевода, converting — после рестарта посреди перевода.
    # Куски есть → перевод идёт; их нет → отказ (тот же путь, что и для new).
    if proj.get("status") not in ("ready", "converted", "error", "converting"):
        raise ReelsiError(umsg("podcast_not_ready",
                               "Сначала дождись кусков: статус проекта «{st}»",
                               st=str(proj.get("status") or "")))
    segs = sorted(proj.get("segments") or [], key=lambda s: float(s["start"]))
    if not segs:
        raise ReelsiError(umsg("podcast_no_segments", "В подкасте нет кусков для рилсов"))
    todo = [(k, s) for k, s in enumerate(segs) if not ids or s["id"] in ids]
    if not todo:
        raise ReelsiError(umsg("podcast_no_segments", "Ни одного выбранного куска нет в проекте"))
    return proj, todo


def _roll_status(folder: str, proj: dict[str, Any], status: str, error: str | None = None) -> None:
    """Статус при отмене/ошибке перевода: запись не должна затереть саму причину (её пишет лог)."""
    try:
        _set_status(folder, proj, status, error=error)
    except Exception:
        log.warning("подкаст %s: не удалось записать статус %s", folder, status, exc_info=True)


def _convert_locked(folder: str, proj: dict[str, Any], todo: list[tuple[int, dict[str, Any]]],
                    cam1: str, reels: list[str]) -> None:
    """Перевод кусков ОДНОГО подкаста. ВЫЗЫВАТЬ ПОД ОБЩИМ ЛОКОМ.

    Статус: converting на время перевода, converted — в конце. Отмена → назад в ready (готовые
    рилсы остаются), ошибка → error с текстом. Оба случая поднимаются наверх: решает вызывающий.
    Папка рилсов — папка камеры 1 спикера (cam1), без неё — `<подкаст>/reels`.
    `reels` — сюда дописываются готовые рилсы этого прогона.
    """
    from core import podcast_reframe, podcast_cams
    reels_dir = cam1 or os.path.join(folder, "reels")
    _set_status(folder, proj, "converting", error="")
    try:
        # Разбор камер — раз на источник: без годного cams.json считаем здесь же, под локом.
        if podcast_cams.load(folder, proj) is None:
            _cams_step(folder, proj)
        for n_done, (index, seg) in enumerate(todo, 1):
            _set_pd(stage="convert", cur="перевод в рилсы", i=n_done, n=len(todo))
            podcast_reframe.convert_segment(folder, proj, seg, index, emit=_emit,
                                            cancel=_pcancelled, reels_dir=reels_dir)
            # Сразу на диск: reel у куска уже стоит (convert_segment его проставил).
            podcast.save_project(folder, proj)
            reels.append(str(seg["reel"]))
            _set_pd(reels=list(reels))
    except (Exception, SystemExit) as e:
        if isinstance(e, _PodcastCancelled) or _pcancelled():
            _roll_status(folder, proj, "ready", error="")
            raise _PodcastCancelled() from e
        _roll_status(folder, proj, "error", error=_err_text(e))
        raise
    _set_status(folder, proj, "converted", error="")


def _convert(folder: str, ids: list[str] | None, cam1: str = "") -> None:
    """«Перевести в рилсы» (П7): куски проекта → вертикальные mp4 под общим локом.

    ids пусто → все куски. Статусы: ready/converted → converting → converted; отмена → назад в
    ready (готовые reel остаются); ошибка → error. Стартовать можно и из error, и из converting:
    иначе после падения или рестарта сервера перевод стоял бы в тупике. Без кусков — отказ; чужой
    лок — отказ. Отказ — БЕЗ смены статуса: проект остаётся как был. cam1 — папка рилсов спикера
    (пусто — `<подкаст>/reels`).
    """
    _set_pd(running=True, done=False, failed=None, dir=folder, started=int(time.time()),
            cancel=False, cancelled=False, stage="", cur="запуск", reels=[], i=0, n=0, pi=0, pn=0)
    ok = False
    cancelled = False
    try:
        proj, todo = _plan_convert(folder, ids)
        if not _cross_lock_acquire():
            raise ReelsiError(umsg("podcast_lock_busy",
                                   "Уже выполняется другая тяжёлая задача"))
        reels: list[str] = []
        try:
            _convert_locked(folder, proj, todo, cam1, reels)
        finally:
            # Лок отпускаем при ЛЮБОМ исходе — иначе RVM-трекинг и нарезка встанут до перезапуска.
            _cross_lock_release()
        ok = True
        _set_pd(cur="готово", i=len(todo), n=len(todo))
    except (Exception, SystemExit) as e:
        if isinstance(e, _PodcastCancelled) or _pcancelled():
            cancelled = True
            _pemit("остановлено")
            _set_pd(cur="остановлено")
        else:
            txt = _err_text(e)
            if not isinstance(e, ReelsiError):
                log.warning("подкаст %s: перевод в рилсы: %s", folder, txt, exc_info=True)
            _pemit("ОШИБКА: {txt}", txt=txt)
            _set_pd(cur=f"ошибка: {txt}")
    finally:
        _set_pd(running=False, done=True, cancelled=cancelled,
                failed=(not ok) and not cancelled, pct=None)


def _all_reels(folders: list[str]) -> list[str]:
    """Все рилсы подкастов по порядку: подкасты — как в списке, куски — по времени; только файлы на диске."""
    out: list[str] = []
    for folder in folders:
        proj = podcast.load_project(folder) or {}
        segs = [s for s in (proj.get("segments") or []) if isinstance(s, dict)]
        for s in sorted(segs, key=lambda s: float(s.get("start") or 0)):
            if _has_reel(s):
                out.append(str(s["reel"]))
    return out


def _convert_all(root: str, folders: list[str], cam1: str) -> None:
    """«Все рилсы в очередь»: кусков без рилса всех подкастов папки — одним джобом.

    Все подкасты идут под ОДНИМ захватом общего лока, каждый — тем же `_convert_locked`, что и
    одиночный перевод. Кусок с рилсом на диске не переводим заново. Ошибка одного подкаста не
    останавливает остальные: его статус — error, причина в логе, джоб в конце — с ошибкой.
    Отмена останавливает всё. В конце `reels` — ВСЕ рилсы подкастов (клиент добавит в очередь
    те, которых там ещё нет), даже если перевод прервали.
    """
    _set_pd(running=True, done=False, failed=None, dir=root, started=int(time.time()),
            cancel=False, cancelled=False, stage="", cur="запуск", reels=[], i=0, n=0,
            pi=0, pn=len(folders))
    ok = False
    cancelled = False
    any_failed = False
    try:
        if not _cross_lock_acquire():
            raise ReelsiError(umsg("podcast_lock_busy",
                                   "Уже выполняется другая тяжёлая задача"))
        try:
            for pi, folder in enumerate(folders, 1):
                if _pcancelled():
                    raise _PodcastCancelled()
                _set_pd(pi=pi)
                try:
                    proj = _load(folder)
                    segs = sorted(proj.get("segments") or [], key=lambda s: float(s["start"]))
                    todo = [(k, s) for k, s in enumerate(segs) if not _has_reel(s)]
                    if not todo:
                        continue                   # все рилсы уже на диске — этот подкаст пропускаем
                    _set_pd(i=0, n=len(todo))
                    _convert_locked(folder, proj, todo, cam1, [])
                except _PodcastCancelled:
                    raise
                except (Exception, SystemExit) as e:
                    if _pcancelled():
                        raise _PodcastCancelled() from e
                    any_failed = True
                    txt = _err_text(e)
                    if not isinstance(e, ReelsiError):
                        log.warning("подкаст %s: перевод в рилсы: %s", folder, txt, exc_info=True)
                    _pemit("ОШИБКА: {txt}", txt=txt)
                    _set_pd(cur=f"ошибка: {txt}")
        finally:
            # Лок отпускаем при ЛЮБОМ исходе — см. _convert.
            _cross_lock_release()
        ok = not any_failed
        _set_pd(cur="готово" if ok else "часть подкастов не переведена — смотри лог")
    except (Exception, SystemExit) as e:
        if isinstance(e, _PodcastCancelled) or _pcancelled():
            cancelled = True
            _pemit("остановлено")
            _set_pd(cur="остановлено")
        else:
            txt = _err_text(e)
            _pemit("ОШИБКА: {txt}", txt=txt)
            _set_pd(cur=f"ошибка: {txt}")
    finally:
        _set_pd(reels=_all_reels(folders), running=False, done=True, cancelled=cancelled,
                failed=(not ok) and not cancelled, pct=None)


# --------------------------------------------------------------------------- #
# Роуты
# --------------------------------------------------------------------------- #

def _reels(v: Any) -> int:
    """Число рилсов: целое 1..30. Строка из цифр тоже годится (поле формы)."""
    if isinstance(v, str) and v.strip().isdigit():
        v = int(v.strip())
    if isinstance(v, bool) or not isinstance(v, int) or not REELS_MIN <= v <= REELS_MAX:
        raise ReelsiError(umsg("podcast_bad_reels", "Рилсов — целое число от 1 до 30"))
    return v


def _last_line(text: str | None) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1] if lines else "нет текста ошибки"


def _project_dir(d: Any) -> str:
    """Папка подкаста из запроса: существует и в ней есть podcast.json, иначе ошибка."""
    raw = d.strip().strip('"') if isinstance(d, str) else ""
    folder = os.path.abspath(raw) if raw else ""
    if not folder or not os.path.isdir(folder) \
            or not os.path.isfile(podcast.project_path(folder)):
        raise ReelsiError(umsg("podcast_not_found", "Папка подкаста не найдена: {path}",
                               path=raw))
    return folder


def _new_folder(root: str, title: str) -> str:
    """root/slug, а если там уже чей-то podcast.json — root/slug_2, slug_3…"""
    # занятость — по podcast.json, а не по самой папке: пустая папка без проекта не мешает
    return fileio.unique_path(root, podcast.slugify(title), is_dir=True,
                              busy=lambda p: os.path.exists(podcast.project_path(p)))


def _probe(url: str) -> dict[str, Any]:
    """Проверка ссылки (yt-dlp -J, без скачивания): дорожки, оригинал по умолчанию, заголовок.

    Одна функция и для «Проверить», и для создания: создание без выбранной дорожки берёт
    оригинал отсюда, а не отдаёт выбор yt-dlp. Ошибки — ReelsiError с кодами podcast_probe_*.
    """
    try:
        cmd = podcast.probe_cmd(url)
    except ValueError as e:
        raise ReelsiError(umsg("podcast_bad_url", str(e)))
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        res = subprocess.run(cmd, timeout=PROBE_TIMEOUT_SEC, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", env=env)
    except subprocess.TimeoutExpired:
        raise ReelsiError(umsg("podcast_probe_timeout",
                               "yt-dlp не ответил за {sec} с — проверь ссылку и сеть",
                               sec=PROBE_TIMEOUT_SEC))
    if res.returncode != 0:
        raise ReelsiError(umsg("podcast_probe_failed",
                               "yt-dlp не открыл ссылку: {why}",
                               why=_last_line(res.stderr)))
    try:
        info = json.loads(res.stdout or "")
    except ValueError:
        raise ReelsiError(umsg("podcast_probe_bad", "yt-dlp вернул не JSON"))
    if not isinstance(info, dict):
        raise ReelsiError(umsg("podcast_probe_bad", "yt-dlp вернул не объект"))
    tracks = podcast.audio_tracks(info)
    return {"title": str(info.get("title") or ""), "duration": info.get("duration") or 0,
            "tracks": tracks, "default": podcast.default_track(tracks)}


def _pick_track(url: str, fmt: str) -> dict[str, Any]:
    """Дорожка проекта по ссылке: {format_id, lang, original} из той же проверки.

    fmt пуст — оригинал (default_track). fmt задан — ищем его среди дорожек проверки: без
    этого в проект ушёл бы id без языка, а на карточке не из чего показать, что скачано.
    Проверка упала или дорожки нет — ReelsiError: проект не создаётся, а не качается наугад.
    """
    tracks = _probe(url)["tracks"]
    if fmt:
        chosen = next((t for t in tracks if t["format_id"] == fmt), None)
        if chosen is None:
            raise ReelsiError(umsg("podcast_track_missing",
                                   "У видео нет звуковой дорожки {fid} — проверь ссылку заново",
                                   fid=fmt))
    else:
        chosen = podcast.default_track(tracks)
        if chosen is None:
            raise ReelsiError(umsg("podcast_no_tracks",
                                   "YouTube не отдал ни одной звуковой дорожки"))
    return {"format_id": chosen["format_id"], "lang": chosen["lang"],
            "original": chosen["original"]}


@bp.route("/api/podcast_probe", methods=["POST"])
def api_podcast_probe() -> Response:
    """Проверка ссылки: звуковые дорожки и заголовок, без скачивания (yt-dlp -J)."""
    d = request.get_json(silent=True) or {}
    url = jstr(d, "url").strip()
    try:
        info = _probe(url)
        best = info["default"]
        return jsonify(ok=True, title=info["title"], duration=info["duration"],
                       tracks=info["tracks"], default=best["format_id"] if best else "")
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/podcast_create", methods=["POST"])
def api_podcast_create() -> Response:
    """Новый подкаст: папка root/slug и podcast.json. Файл — сразу «downloaded», ссылка — «new»."""
    d = request.get_json(silent=True) or {}
    try:
        raw_root = jstr(d, "root").strip().strip('"')
        url = jstr(d, "url").strip()
        file = jstr(d, "file").strip().strip('"')
        title = jstr(d, "title").strip()
        fmt = jstr(d, "format_id").strip()
        # Папка подкастов: абсолютный путь, и не похожий на опцию («-…» уйдёт в команды
        # как флаг). Если её ещё нет — создаём: первый запуск не должен требовать ручной
        # подготовки папки. Не создалась (нет прав, это файл) — отказ, а не тихий пропуск.
        if not raw_root or raw_root.startswith("-") or not os.path.isabs(raw_root):
            raise ReelsiError(umsg("podcast_bad_root",
                                   "Папка подкастов не годится или её не создать: {path}",
                                   path=raw_root))
        root = os.path.abspath(raw_root)
        try:
            os.makedirs(root, exist_ok=True)
        except OSError as e:
            raise ReelsiError(umsg("podcast_bad_root",
                                   "Папка подкастов не годится или её не создать: {path}",
                                   path=raw_root)) from e
        # Ровно одно из двух: и ссылка, и файл — непонятно, что брать; ни одного — нечего делать.
        if bool(url) == bool(file):
            raise ReelsiError(umsg("podcast_one_source",
                                   "Нужна ровно одна вещь: ссылка YouTube или файл"))
        reels = _reels(d.get("reels"))
        track: dict[str, Any] | None = None
        if url:
            if not podcast.is_youtube(url):
                raise ReelsiError(umsg("podcast_bad_url", "Это не ссылка YouTube"))
            # дорожку берём ДО папки проекта: проверка упала — проект не создан
            track = _pick_track(url, fmt)
            source, status, default_title = "", "new", "podcast"
        else:
            if not os.path.isfile(file):
                raise ReelsiError(umsg("podcast_no_file", "Файл не найден: {path}", path=file))
            source = os.path.abspath(file)
            status = "downloaded"
            default_title = os.path.splitext(os.path.basename(file))[0]
        title = title or default_title
        folder = _new_folder(root, title)
        proj = podcast.new_project(title, url, source, reels)
        proj["status"] = status
        if track is not None:
            proj["audio_track"] = track
        elif fmt:
            proj["audio_track"] = {"format_id": fmt}
        podcast.save_project(folder, proj)
        return jsonify(ok=True, dir=folder, project=proj)
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/podcast_run", methods=["POST"])
def api_podcast_run() -> Response:
    """Запустить конвейер подкаста фоновым потоком; второй запуск, пока идёт первый, — нет."""
    d = request.get_json(silent=True) or {}
    try:
        folder = _project_dir(jstr(d, "dir"))
        with PDLOCK:
            if PDJOB["running"]:
                raise ReelsiError(umsg("podcast_busy",
                                       "Подкаст уже обрабатывается — дождись конца"))
            PDJOB.update(PDFRESH, running=True, done=False, failed=None, log=[], log_base=0,
                         dir=folder, started=int(time.time()), folders=[])
        try:
            threading.Thread(target=_pipeline, args=(folder,), daemon=True).start()
        except Exception:
            with PDLOCK:
                PDJOB["running"] = False   # поток не родился — не оставляем джоб занятым
            raise
        return jsonify(ok=True)
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


def _cam1_dir(speaker: str) -> str:
    """Папка камеры 1 выбранного спикера, если она есть на диске; иначе ''.

    Рилсы пишутся туда (владелец, 2026-10-11: папка рилсов — где спикер и его камера 1).
    Пусто — рилсы в папке подкаста, и ответ роута несёт fallback.
    """
    name = (speaker or "").strip()
    if not name:
        return ""
    prof = speakers.load(name) or {}
    cds = prof.get("camdirs")
    cam1 = str(cds[0] if isinstance(cds, list) and cds else "").strip().strip('"')
    return os.path.abspath(cam1) if cam1 and os.path.isdir(cam1) else ""


@bp.route("/api/podcast_convert", methods=["POST"])
def api_podcast_convert() -> Response:
    """Перевод кусков в рилсы фоновым потоком на том же PDJOB; body: {dir, ids?, speaker?}.

    ids пусто — все куски. Готовые рилсы клиент забирает из `reels` в статусе джоба.
    fallback: true — рилсы легли в папку подкаста (у спикера нет папки камеры 1).
    """
    d = request.get_json(silent=True) or {}
    try:
        folder = _project_dir(jstr(d, "dir"))
        raw_ids = d.get("ids")
        ids = [x for x in raw_ids if isinstance(x, str) and x] if isinstance(raw_ids, list) else []
        cam1 = _cam1_dir(jstr(d, "speaker"))
        with PDLOCK:
            if PDJOB["running"]:
                raise ReelsiError(umsg("podcast_busy",
                                       "Подкаст уже обрабатывается — дождись конца"))
            PDJOB.update(PDFRESH, running=True, done=False, failed=None, log=[], log_base=0,
                         dir=folder, started=int(time.time()), reels=[], i=0, n=0, folders=[])
        try:
            threading.Thread(target=_convert, args=(folder, ids or None, cam1),
                             daemon=True).start()
        except Exception:
            with PDLOCK:
                PDJOB["running"] = False   # поток не родился — не оставляем джоб занятым
            raise
        return jsonify(ok=True, fallback=not cam1)
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/podcast_convert_all", methods=["POST"])
def api_podcast_convert_all() -> Response:
    """«Все рилсы в очередь»: кусков без рилса всех подкастов папки — одним джобом.

    body: {root, speaker?}. В работу берём готовые (ready/converted/error) с кусками — в порядке
    списка. Ответ: count — сколько подкастов в работе, fallback — как у одиночного перевода.
    """
    d = request.get_json(silent=True) or {}
    try:
        raw = jstr(d, "root").strip().strip('"')
        if not raw or not os.path.isdir(raw):
            raise ReelsiError(umsg("podcast_bad_root",
                                   "Папка подкастов не годится или её не создать: {path}",
                                   path=raw))
        root = os.path.abspath(raw)
        cam1 = _cam1_dir(jstr(d, "speaker"))
        folders = [folder for folder, proj in _podcast_rows(root)
                   if proj.get("status") in ("ready", "converted", "error")
                   and isinstance(proj.get("segments"), list) and proj["segments"]]
        if not folders:
            raise ReelsiError(umsg("podcast_none_ready",
                                   "Нет подкастов с готовыми кусками — переводить нечего"))
        with PDLOCK:
            if PDJOB["running"]:
                raise ReelsiError(umsg("podcast_busy",
                                       "Подкаст уже обрабатывается — дождись конца"))
            PDJOB.update(PDFRESH, running=True, done=False, failed=None, log=[], log_base=0,
                         dir=root, started=int(time.time()), reels=[], i=0, n=0,
                         folders=folders)
        try:
            threading.Thread(target=_convert_all, args=(root, folders, cam1),
                             daemon=True).start()
        except Exception:
            with PDLOCK:
                PDJOB["running"] = False   # поток не родился — не оставляем джоб занятым
            raise
        return jsonify(ok=True, count=len(folders), fallback=not cam1)
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


def _norm_dir(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


def _busy_folders() -> set[str]:
    """Папки подкастов, которые сейчас пишет джоб: перевод/разбор (вместе с «Все рилсы») и сборка прокси."""
    names: list[str] = []
    with PDLOCK:
        if PDJOB["running"]:
            names = [str(PDJOB["dir"] or "")] + [str(x) for x in (PDJOB.get("folders") or [])]
    with PPLOCK:
        if PPJOB["running"] and PPJOB["dir"]:
            names.append(str(PPJOB["dir"]))
    return {_norm_dir(x) for x in names if x}


def _same_file(a: Any, b: str) -> bool:
    """Один и тот же файл: пути сверяем по realpath (регистр и разделители на Windows не значат)."""
    if not a or not isinstance(a, str) or not b:
        return False  # пустой путь realpath принял бы за текущую папку — сравнивать нельзя
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _to_trash(folder: str, sources: bool) -> tuple[str, list[dict[str, str]]]:
    """Подкаст → корзина: `<папка подкастов>/_reelsi_trash/<YYYYmmdd-HHMMSS>_<имя>/`.

    Внутрь записи переезжает сама папка подкаста под своим именем, рядом пишется trash.json
    {kind: "podcast", title, from, deleted_at, files}. Перенос внутри одной папки подкастов —
    переименование, на том же диске. Занятая папка (плеер держит прокси) — отказ, всё на месте.

    Внешний исходник (файл, выбранный с диска) уходит при sources: true в
    `<его папка>/_reelsi_trash/<id записи>/` и пишется в files с полем stored. Если тот же файл
    у другого подкаста этой папки — не переносим, причина в skipped. Скачанный source.* лежит
    внутри папки подкаста и уезжает вместе с ней всегда.

    Возвращает (id записи, skipped).
    """
    root = os.path.dirname(folder)
    base = os.path.basename(folder)
    entry_id, entry = new_trash_entry(root, base)

    proj = podcast.load_project(folder) or {}
    src = str(proj.get("source") or "").strip()
    # Кто ещё держит тот же внешний файл — смотрим, пока папка подкаста ещё на месте в списке
    sharers: list[str] = []
    if sources and src and not is_within(src, folder):
        sharers = [str(p.get("title") or os.path.basename(f))
                   for f, p in _podcast_rows(root)
                   if os.path.normcase(os.path.realpath(f)) != os.path.normcase(os.path.realpath(folder))
                   and _same_file(p.get("source"), src)]

    try:
        os.makedirs(entry)
        os.rename(folder, os.path.join(entry, base))  # та же папка подкастов — переименование
    except OSError as e:
        cleanup_failed_entry(entry)  # запись не создалась — пустые папки записи и корзины не оставляем
        raise ReelsiError(umsg("podcast_delete_failed",
                               "Подкаст не убран в корзину — файл занят или нет прав: {why}",
                               why=f"{type(e).__name__}: {e}")) from e

    files: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    if sources and src and not is_within(src, folder):
        src_abs = os.path.abspath(src)
        if sharers:
            skipped.append({"path": src_abs, "why": "нужен ещё подкасту: " + ", ".join(sharers)})
        else:
            try:
                dst, stored = trash_move_in(src_abs, entry_id)
                files.append({"from": src_abs, "name": os.path.basename(dst), "stored": stored})
            except OSError as e:
                skipped.append({"path": src_abs, "why": f"не перенесён: {type(e).__name__}: {e}"})

    write_trash_meta(entry, {
        "kind": "podcast",
        "title": str(proj.get("title") or base),
        "from": folder,
        "deleted_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
    })
    return entry_id, skipped


@bp.route("/api/podcast_delete", methods=["POST"])
def api_podcast_delete() -> Response:
    """Убрать подкаст в корзину; body: {dir, sources?}. Рилсы, уже стоящие в очереди, остаются.

    sources: true — унести и внешний исходник, если его не держит другой подкаст этой папки.
    Подкаст, который сейчас пишет джоб, не трогаем — отказ «дождись конца».
    """
    d = request.get_json(silent=True) or {}
    try:
        folder = _project_dir(jstr(d, "dir"))
        if _norm_dir(folder) in _busy_folders():
            raise ReelsiError(umsg("podcast_busy",
                                   "Подкаст уже обрабатывается — дождись конца"))
        entry_id, skipped = _to_trash(folder, _flag_on(d.get("sources")))
        return jsonify(ok=True, trash=entry_id, skipped=skipped)
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


def _flag_on(v: Any) -> bool:
    """Флаг из тела запроса: true / "true" / "1" — да."""
    return v is True or str(v).lower() in ("true", "1")


@bp.route("/api/podcast_list")
def api_podcast_list() -> Response:
    """Подкасты в папке root (один уровень): где есть проект, новые сверху; корзину не показываем."""
    root = (request.args.get("root") or "").strip().strip('"')
    items: list[dict[str, Any]] = []
    for folder, proj in _podcast_rows(root):
        segs = proj.get("segments")
        items.append({
            "dir": folder,
            "title": proj.get("title", ""),
            "status": proj.get("status", ""),
            "n_segments": len(segs) if isinstance(segs, list) else 0,
            # готовые рилсы (П7): карточка «рилсы готовы: N»
            "reels_ready": sum(1 for s in segs if isinstance(s, dict) and s.get("reel"))
                           if isinstance(segs, list) else 0,
            "reels_wanted": proj.get("reels_wanted", 0),
            "url": proj.get("url", ""),
            # дорожка (П16) — только у подкаста по ссылке: у файла с диска её нет
            "audio_track": (proj.get("audio_track") or None) if proj.get("url") else None,
            "error": proj.get("error", ""),
        })
    return jsonify(ok=True, items=items)


def _podcast_rows(root: str) -> list[tuple[str, dict[str, Any]]]:
    """Подкасты папки root (один уровень) с проектом, новые сверху: [(папка, проект)].

    Корзина `_reelsi_trash` — не подкаст: в ней папки уже убранных подкастов.
    """
    rows: list[tuple[float, str, dict[str, Any]]] = []
    if root and os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            if name == POD_TRASH_DIR:
                continue
            folder = os.path.join(root, name)
            if not os.path.isdir(folder):
                continue
            proj = podcast.load_project(folder)
            if proj is None:
                continue
            try:
                mtime = os.path.getmtime(podcast.project_path(folder))
            except OSError:
                mtime = 0.0
            rows.append((mtime, folder, proj))
    rows.sort(key=lambda r: r[0], reverse=True)
    return [(folder, proj) for _, folder, proj in rows]


@bp.route("/api/podcast_status")
def api_podcast_status() -> Response:
    """Прогресс конвейера. `?since=` — сколько строк лога уже у клиента (как у gdrive_status)."""
    try:
        since = int(request.args.get("since") or 0)
    except ValueError:
        since = 0
    with PDLOCK:
        log, total = job_log_since(PDJOB, since)
        d = {k: v for k, v in PDJOB.items() if k not in ("log", "log_base")}
        d["log"] = log
        d["log_total"] = total
        d["elapsed"] = int(time.time()) - PDJOB["started"] if PDJOB["started"] else 0
    return jsonify(ok=True, **d)


@bp.route("/api/podcast_cancel", methods=["POST"])
def api_podcast_cancel() -> Response:
    podcast_kill()
    return jsonify(ok=True)


@bp.route("/api/podcast_get")
def api_podcast_get() -> Response:
    """Проект и слова расшифровки (для окна правки кусков)."""
    try:
        folder = _project_dir(request.args.get("dir"))
        proj = _load(folder)
        return jsonify(ok=True, project=proj, words=podcast_ai.load_words(folder))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/podcast_save", methods=["POST"])
def api_podcast_save() -> Response:
    """Заменить куски проекта. Пересечения и выход за длину ловит save_project: файл не тронут."""
    d = request.get_json(silent=True) or {}
    try:
        folder = _project_dir(jstr(d, "dir"))
        proj = _load(folder)
        raw = d.get("segments")
        bad = "Куски подкаста не сходятся: {problems}"
        if not isinstance(raw, list):
            raise ReelsiError(umsg("podcast_bad_segments", bad, problems="куски должны быть списком"))
        segs: list[dict[str, Any]] = []
        for x in raw:
            if not isinstance(x, dict):
                raise ReelsiError(umsg("podcast_bad_segments", bad, problems="кусок не объект"))
            sid = x.get("id")
            segs.append({
                "id": sid if isinstance(sid, str) and sid else podcast.new_segment_id(),
                "start": x.get("start"),
                "end": x.get("end"),
                "title": str(x.get("title") or "")[:podcast_ai.TITLE_MAX],
                "why": str(x.get("why") or "")[:podcast_ai.WHY_MAX],
                "reel": str(x.get("reel") or ""),
            })
        if "reels_wanted" in d:
            proj["reels_wanted"] = _reels(d.get("reels_wanted"))
        proj["segments"] = segs
        podcast.save_project(folder, proj)
        return jsonify(ok=True, project=proj)
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


# --------------------------------------------------------------------------- #
# Прокси для браузера (окно правки кусков, П5)
# --------------------------------------------------------------------------- #
# Исходник — mkv 4K VP9/AV1: браузер тянет его тяжело, поэтому окно играет 720p-прокси.
# Сборка — ffmpeg, а не GPU-модель, поэтому общий межпроцессный лок задач здесь НЕ берём
# (так же делает /api/preview_proxy). Свой джоб PPJOB: одна сборка за раз.
PROXY_NAME = "proxy720.mp4"              # итог: окно играет его, когда он свежее источника
PROXY_BUILD_NAME = "proxy720.build.mp4"  # куда пишет сборка; потом os.replace в итог
PPJOB: dict[str, Any] = {"running": False, "pct": None, "error": "", "dir": ""}
PPLOCK = threading.Lock()


def _pp_pct(value: float) -> None:
    """Процент сборки прокси (0–100) — для строки «прокси 37 %» в окне."""
    with PPLOCK:
        PPJOB["pct"] = int(max(0, min(100, round(value))))


def _proxy_ready(folder: str) -> bool:
    """Прокси есть и не старше источника (или источника на диске нет — тогда хватит самого файла)."""
    final = os.path.join(folder, PROXY_NAME)
    if not os.path.isfile(final):
        return False
    proj = podcast.load_project(folder) or {}
    src = str(proj.get("source") or "")
    if not src or not os.path.isfile(src):
        return True
    return os.path.getmtime(final) >= os.path.getmtime(src)


def _proxy_build(folder: str, src: str) -> None:
    """Фоновая сборка прокси: ffmpeg во временный файл, потом os.replace в итог."""
    from core import draftrender
    tmp = os.path.join(folder, PROXY_BUILD_NAME)
    final = os.path.join(folder, PROXY_NAME)
    err = ""
    try:
        got = draftrender.build_preview_proxy(src, tmp, 720, progress=_pp_pct)
        if got and os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
            fileio.move_file(tmp, final)
        else:
            err = "прокси не собрался — окно играет исходник"
    except (ReelsiError, SystemExit) as e:
        err = _err_text(e)
    except Exception as e:
        log.warning("подкаст %s: прокси для браузера: %s", folder, e, exc_info=True)
        err = f"{type(e).__name__}: {e}"
    finally:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)      # недописанный прокси не должен лежать рядом с итогом
        except OSError:
            pass  # недописанный прокси: следующая сборка перезапишет тот же tmp-файл
        with PPLOCK:
            PPJOB.update(running=False, error=err)


@bp.route("/api/podcast_proxy", methods=["POST"])
def api_podcast_proxy() -> Response:
    """Прокси подкаста для окна правки. body: {dir}.

    Готов и свежее источника — `ready: true`, сборка не запускается. Нет — фоновая сборка
    (если уже идёт именно для этого подкаста, второй не запускаем). Пока прокси нет, окно
    играет исходник через /api/media.
    """
    d = request.get_json(silent=True) or {}
    try:
        folder = _project_dir(jstr(d, "dir"))
        proj = _load(folder)
        src = str(proj.get("source") or "")
        if not src or not os.path.isfile(src):
            raise ReelsiError(umsg("podcast_proxy_no_source",
                                   "Исходника подкаста нет на диске: {path}", path=src))
        final = os.path.join(folder, PROXY_NAME)
        if _proxy_ready(folder):
            return jsonify(ok=True, ready=True, path=final)
        with PPLOCK:
            if PPJOB["running"]:
                # Чужая сборка (другой подкаст) не наша: окно этого подкаста играет исходник.
                mine = PPJOB["dir"] == folder
                return jsonify(ok=True, ready=False, building=mine)
            PPJOB.update(running=True, pct=0, error="", dir=folder)
        try:
            threading.Thread(target=_proxy_build, args=(folder, src), daemon=True).start()
        except Exception:
            with PPLOCK:
                PPJOB["running"] = False   # поток не родился — не оставляем сборку занятой
            raise
        return jsonify(ok=True, ready=False, building=True)
    except (ReelsiError, SystemExit) as e:
        return jsonify(ok=False, **umsg_err(e))


@bp.route("/api/podcast_proxy_status")
def api_podcast_proxy_status() -> Response:
    """Ход сборки прокси для подкаста `?dir=`: идёт ли, процент, готов ли, ошибка сборки."""
    raw = (request.args.get("dir") or "").strip().strip('"')
    folder = os.path.abspath(raw) if raw else ""
    with PPLOCK:
        mine = bool(PPJOB["running"]) and bool(folder) and PPJOB["dir"] == folder
        pct = PPJOB["pct"]
        err = PPJOB["error"] if folder and PPJOB["dir"] == folder else ""
    ready = bool(folder) and os.path.isdir(folder) and _proxy_ready(folder)
    return jsonify(ok=True, running=mine, pct=pct, ready=ready, error=err)
