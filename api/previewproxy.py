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

Звук прокси — ВСЕГДА звук камеры: обработанный голос клипа в видео не вшивается.
Голос идёт отдельной дорожкой (`<audio>`) в плеерах превью (`vt*` в
static/app/60-preview.js) и печётся своей дверью `/api/voicefx_bake` — той же, что у
панели «Голос». Отсюда два следствия, ради которых это и разделено: настройки голоса
прокси не пересобирают (сменил долю шумодава — пересчитался только голос, а прокси
файла камеры на десятки секунд остался на месте), а «голосового» прокси со своим
именем и своим хешем больше нет вовсе.
"""
import os, threading
from typing import Any
from flask import Response, jsonify, request
from ._core import (bp, emit as general_emit, jstr, lock_owner_text, log_entry, umsg_err,
                    _cross_lock_acquire, _cross_lock_release, cross_lock_task,
                    sysexit_text)
from core.umsg import ReelsiError, umsg

PXJOB: dict[str, Any] = {"running": False, "done": False, "log": [], "cur": "", "i": 0, "n": 0, "pct": 0}
PXLOCK = threading.Lock()

# Имя задачи для отказа «уже идёт» (см. _core.lock_owner_text): по нему человек
# понимает, что видеокарту держит сборка прокси, а не нарезка, и где смотреть прогресс.
PROXY_TASK = "сборка прокси превью"


def _emit(line: str, /, **vars: Any) -> None:
    with PXLOCK:
        entry = log_entry(line, vars)
        PXJOB["log"].append(entry)
    # Та же строка — в общий лог задания (его читает /api/status и панель «Логи»):
    # сообщение отказа посылает человека именно туда, а раньше сборщик писал только
    # в свой PXJOB, и в «Логах» было пусто. Сборка прокси идёт под межзадачным
    # локом, то есть одновременно с нарезкой/сборкой не бывает — перетирать нечего.
    general_emit(line, **vars)


def _proxy_hint() -> str:
    """Строка прогресса сборки для ЧУЖОГО отказа: «камера 2 из 2, 40 % — …».

    Данные уже есть в PXJOB (i/n текущего файла и процент сборки): по ним отказ
    нарезки говорит, чего именно ждать."""
    with PXLOCK:
        i, n, pct = PXJOB["i"], PXJOB["n"], PXJOB["pct"]
    if not n:
        return "готовлю план камер — прогресс в окне превью и в Логах"
    return f"камера {i} из {n}, {pct} % — прогресс в окне превью и в Логах"


def _pct(value: float) -> None:
    """Процент сборки ТЕКУЩЕГО файла.

    Приходит из `build_preview_proxy` (ffmpeg идёт с `-progress pipe:1`, см.
    `draftrender.ff_progress_pct`) — фронт рисует по нему полосу прогресса.
    Округляем до целого: в интерфейсе показываются проценты, а не доли."""
    with PXLOCK:
        PXJOB["pct"] = int(max(0, min(100, round(value))))


def _preview_proxy_plan(xml_path: str, height: int = 720,
                        allintra: bool = False) -> tuple[list[tuple[str, str, bool]], str]:
    """[(src, proxy_path, готов ли)] по камерам XML + папка кэша.

    Путь у КАЖДОЙ камеры один и тот же — обычный превью-прокси со звуком камеры:
    «голосового» прокси (звук камеры 1 заменён обработанным голосом) здесь больше нет.
    Голос играет отдельной дорожкой плеера (`vt*`, static/app/60-preview.js), поэтому
    настроек голоса в плане нет вовсе — и смена ручки шумодава не пересобирает прокси
    файла камеры.

    `allintra` — прокси ДЛЯ РЕНДЕРА БЕЗ AE: ключевой кадр каждый (см.
    `draftrender.render_proxy_path`). Файл и ключ кэша другие, превью их не видит.
    """
    from core import draftrender
    from core import xml2ae
    edl = xml2ae.virtual_edl(xml_path)
    tdir = draftrender.tmp_dir(xml_path)
    out: list[tuple[str, str, bool]] = []
    for c in edl.get("cams") or []:
        src = c.get("path")
        if not (src and os.path.isfile(src)):
            continue
        dst = (draftrender.render_proxy_path(src, height, tdir) if allintra
               else draftrender.preview_path(src, height, tdir))
        out.append((src, dst, os.path.isfile(dst) and os.path.getsize(dst) > 0))
    return out, tdir


def _extra_proxy_plan(paths: Any, height: int, tdir: str,
                      allintra: bool) -> list[tuple[str, str, bool]]:
    """Прокси файлов, которым он нужен НЕ из-за камер: слой перехода видеовставки.

    Переход (`Quick 2.mov` из стиля) — ProRes 4K: браузер его не декодирует вовсе, и без
    прокси перехода в превью не видно совсем, а он горит на входе и выходе каждой
    видеовставки. Собирается тем же сборщиком и теми же именами кэша, что прокси камер
    (`preview_path`/`render_proxy_path`): второй двери «чем играть нечитаемый файл» не
    заводится. Файла нет или путь пуст — записи нет: заказывать нечего.

    Дубли путей выбрасываются по ИМЕНИ прокси, а не по исходнику: два разных пути могут
    дать один и тот же кэш (жёсткая ссылка, копия) — собирать его дважды незачем.
    """
    from core import draftrender
    out: list[tuple[str, str, bool]] = []
    seen: set[str] = set()
    for raw in (paths or []):
        src = str(raw or "").strip()
        if not src or not os.path.isfile(src):
            continue
        dst = (draftrender.render_proxy_path(src, height, tdir) if allintra
               else draftrender.preview_path(src, height, tdir))
        if dst in seen:
            continue
        seen.add(dst)
        out.append((src, dst, os.path.isfile(dst) and os.path.getsize(dst) > 0))
    return out


def _run_preview_proxy(plan: list[tuple[str, str, bool]], height: int) -> None:
    """Фоновая сборка недостающих прокси. Свой джоб, а не общий JOB: нарезка\сборка
    .jsx не должны блокироваться тем, что юзер открыл предпросмотр.

    Голоса здесь нет: обработанный голос клипа играет отдельной дорожкой плеера
    (`vt*`, static/app/60-preview.js) и печётся своей дверью `/api/voicefx_bake`.
    Отсюда и счётчик: «превью-прокси 1/1 · 0 %» висело ровно на шаге запекания —
    у шумодава процента сборки прокси не бывает по определению, а плеер показывал
    именно его. Теперь в этом счётчике только сборка файла, у неё процент настоящий.

    Прокси рендера без AE (`draftrender.RENDER_PROXY_RE`) собираются СВОИМ сборщиком:
    ключевой кадр каждый, без звука. Какой это прокси — видно по имени файла: план
    приходит путями, и второй копии решения тут быть не должно.
    """
    from core import draftrender
    todo = [(s, d) for (s, d, ok) in plan if not ok]
    try:
        with PXLOCK:
            PXJOB.update(running=True, done=False, log=[], i=0, n=len(todo), cur="", pct=0)
        for k, (src, dst) in enumerate(todo, 1):
            with PXLOCK:
                PXJOB.update(i=k, cur=os.path.basename(src), pct=0)
            _emit("превью-прокси {cur}/{total}: {name}",
                  cur=k, total=len(todo), name=os.path.basename(src))
            if draftrender.RENDER_PROXY_RE.search(os.path.basename(dst)):
                draftrender.build_render_proxy(src, dst, height=height, emit=_emit)
                continue
            draftrender.build_preview_proxy(src, dst, height=height, emit=_emit,
                                            progress=_pct)
    except (ReelsiError, SystemExit) as e:
        # SystemExit (umsg) — BaseException: без ветки сборка прокси вставала с пустым
        # логом, и в статусе не было причины.
        with PXLOCK:
            PXJOB["log"].append(sysexit_text(e))
    except ReelsiError: raise
    except Exception:
        import traceback
        with PXLOCK:
            PXJOB["log"].append(traceback.format_exc().strip().splitlines()[-1])
    finally:
        try:
            _cross_lock_release()
        finally:
            with PXLOCK:
                PXJOB.update(running=False, done=True, cur="")


@bp.route("/api/preview_proxy", methods=["POST"])
def api_preview_proxy() -> Response:
    """Прокси камер для предпросмотра. body: {xml, build?: bool, height?: int, allintra?: bool}.

    Возвращает по каждой камере путь к прокси и готов ли он. build=true — запустить
    фоновую сборку недостающих. Пока прокси нет, интерфейс играет исходник (как раньше).

    Прокси всегда со звуком камеры: обработанный голос клипа — отдельная дорожка плеера
    и своя дверь (`/api/voicefx_bake`), настроек голоса этот роут не знает и знать не
    должен — иначе смена ручки шумодава пересобирала бы прокси файла камеры.

    allintra=true — прокси ДЛЯ РЕНДЕРА БЕЗ AE: ключевой кадр каждый (перемотка <video>
    на каждый кадр не декодирует от далёкого ключевого). Отдельные файлы и отдельный
    ключ кэша: превью свой прокси не теряет.

    extra — пути файлов, которым прокси нужен НЕ из-за камер (слой перехода
    видеовставки, см. `_extra_proxy_plan`): собирает их тот же сборщик, а в ответе они
    едут отдельным списком."""
    d = request.get_json() or {}
    xml_path = jstr(d, "xml").strip().strip('"')
    start = False
    try:
        if not os.path.isfile(xml_path):
            raise ReelsiError(umsg("file_not_found", f"Файл не найден: {xml_path}",
                                  path=xml_path))
        height = int(d.get("height") or 720)
        allintra = bool(d.get("allintra"))
        try:
            plan, tdir = _preview_proxy_plan(xml_path, height, allintra)
            extra = _extra_proxy_plan(d.get("extra"), height, tdir, allintra)
            # Сборщик и признак «есть что собирать» работают по ОБОЕДИНЁННОМУ списку:
            # переходу видеовставки прокси нужен так же, как камерам, а в ответе он
            # едет своим списком (extra).
            todo = plan + extra
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("preview_plan_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))

        # Два одновременных запроса (два таба) не должны запустить ДВА сборщика в один
        # детерминированный dst (pv_<sha1>.part.mp4): перемешанные потоки кадров уехали
        # бы в кэш насовсем. Проверка И пометка «running» — под одним PXLOCK, поток — после.
        want_build = False
        with PXLOCK:
            busy = PXJOB["running"]
            if d.get("build") and not busy and any(not ok for (_s, _d, ok) in todo):
                want_build = True
                # Имя и подсказка прогресса — ДО захвата: отказ соседней задачи
                # (нарезка/сборка) назовёт сборку прокси и её «камера i из n, pct %».
                cross_lock_task(PROXY_TASK, _proxy_hint)
                if _cross_lock_acquire():
                    PXJOB.update(running=True, done=False, log=[], i=0, n=0, cur="", pct=0)
                    busy = True
                    start = True
        # Отказ строим ВНЕ PXLOCK: lock_owner_text спрашивает подсказку прогресса,
        # а та берёт PXLOCK сама — под ним это был бы само-дедлок.
        if want_build and not start:
            raise ReelsiError(umsg("busy", lock_owner_text("Уже выполняется")))
        if start:
            try:
                threading.Thread(target=_run_preview_proxy,
                                 args=(todo, height), daemon=True).start()
            except ReelsiError: raise
            except Exception:
                _cross_lock_release()
                with PXLOCK:
                    PXJOB["running"] = False
                raise
        return jsonify(ok=True, dir=tdir, building=busy,
                       cams=[{"path": s, "proxy": p, "ready": ok} for (s, p, ok) in plan],
                       extra=[{"path": s, "proxy": p, "ready": ok} for (s, p, ok) in extra])
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


# Один замок на файл звука: два запроса подряд (открыли клип, тут же сохранили правку —
# openPreview зовётся снова) не должны писать один и тот же .part разом.
PALOCKS: dict[str, threading.Lock] = {}


@bp.route("/api/preview_audio", methods=["POST"])
def api_preview_audio() -> Response:
    """Звук камеры 1 клипа одним WAV — для звука редактора шага 1. body: {xml}.

    Редактор играет звук буфером Web Audio (static/app/60-preview.js, блок `ea*`), а не
    элементом <video>: так стыки идут с точностью до сэмпла, без перемотки и подгонки
    скоростью. Камера 1 — та же, что у `/api/aicut_preview` (`virtual_edl`, первая
    камера): по ней же печётся обработанный голос, и время у них общее. Вынимается
    один раз на файл камеры, дальше из кэша `_tmp` (`pa_*.wav`)."""
    d = request.get_json() or {}
    xml_path = jstr(d, "xml").strip().strip('"')
    try:
        if not os.path.isfile(xml_path):
            raise ReelsiError(umsg("file_not_found", f"Файл не найден: {xml_path}",
                                  path=xml_path))
        from core import draftrender
        from core import xml2ae
        cams = xml2ae.virtual_edl(xml_path).get("cams") or []
        src = (cams[0].get("path") if cams else "") or ""
        if not (src and os.path.isfile(src)):
            raise ReelsiError(umsg("file_not_found", f"Файл не найден: {src}", path=src))
        dst = draftrender.preview_audio_path(src, draftrender.tmp_dir(xml_path))
        with PXLOCK:
            lock = PALOCKS.setdefault(dst, threading.Lock())
        with lock:
            if not (os.path.isfile(dst) and os.path.getsize(dst) > 0):
                if not draftrender.build_preview_audio(src, dst):
                    raise ReelsiError(umsg("preview_audio_failed",
                                           f"Звук камеры не вынулся: {os.path.basename(src)}",
                                           name=os.path.basename(src)))
        return jsonify(ok=True, path=dst, src=src)
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/preview_proxy_status")
def api_preview_proxy_status() -> Response:
    """Прогресс фоновой сборки превью-прокси: i/n текущего файла, его имя (cur),
    процент готовности (pct, 0–100) и хвост лога."""
    with PXLOCK:
        return jsonify(ok=True, **{k: v for k, v in PXJOB.items() if k != "log"},
                       log=PXJOB["log"][-40:])
