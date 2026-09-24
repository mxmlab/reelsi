# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Генерация видео (вкладка «Видео»).

СВОЙ фоновый поток и состояние VJOB, JOB нарезки/сборки не трогает: облачная генерация
идёт минутами и не должна занимать локальную очередь.
"""
import os, json, threading, time
from typing import Any, cast
import urllib.request
from flask import Response, jsonify, request
from ._core import (APP_NAME, APP_REFERER, LOG_CAP, bp, env, jstr, journal_interrupted,
                    journal_write, umsg_err)
from core import paths
from core.fileio import atomic_json_dump, quarantine_unreadable
from core.umsg import ReelsiError, umsg
from core.app_meta import http_req
from core.applog import get_logger

log = get_logger(__name__)




# ============================================================================
# Генерация видео (Seedance 2 и др. на OpenRouter Video API) — отдельная вкладка.
# СВОЙ фоновый поток и состояние VJOB, НЕ трогает JOB нарезки/сборки: генерация
# видео идёт минутами и не должна лочить пайплайн (и наоборот). Клиент опрашивает
# /api/video_status. Референсы — ГОТОВЫЕ https-ссылки (OpenRouter качает файл по URL;
# локальные файлы/data-URI он отвергает). Готовый mp4 падает в _videogen/out и
# отдаётся через /api/media.
# ============================================================================
VIDEO_DIR = env("VIDEO_DIR") or paths.root("_videogen")
VIDEO_OUT = os.path.join(VIDEO_DIR, "out")
VJOB: dict[str, Any] = {"running": False, "done": False, "cancel": False, "log": [], "log_base": 0,
        "result": None, "error": None, "err": None, "err_vars": None, "started": 0,
        "key": None, "context": ""}       # ключ истории + opaque-контекст UI карточки
VLOCK = threading.Lock()

# История генераций — на СЕРВЕРЕ, а не в состоянии страницы: она про то, что сервер
# посчитал и что лежит у него в _videogen/out. Страница живёт F5 и вкладками, и без
# такого файла всё, кроме последнего результата, пропадало вместе с перезагрузкой:
# готовые ролики оставались на диске безымянными, а оборванная задача — незаметной.
# REELSI_VIDEO_HISTORY/REELSI_VIDEO_DIR — как REELSI_UI_STATE: без своих переменных
# тестовый профиль правил бы боевую историю.
VIDEO_HIST_PATH = env("VIDEO_HISTORY") or os.path.join(VIDEO_DIR, "history.json")
VHIST_CAP = 300
VHIST_LOCK = threading.Lock()


def _vhist_read() -> list[dict[str, Any]]:
    """Записи истории (старые в начале). Битый/отсутствующий файл — пустой список:
    вкладка не должна падать из-за журнала."""
    try:
        with open(VIDEO_HIST_PATH, encoding="utf-8") as f:
            items = json.load(f)
        return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []
    except ReelsiError: raise
    except Exception as ex:
        # Контракт (пустой список) сохраняем, но молчать нельзя: следующая же запись
        # статуса завела бы журнал с нуля и стёрла историю задач.
        if os.path.exists(VIDEO_HIST_PATH):
            log.warning("журнал генераций не прочитан (%s): %s — при следующей записи будет "
                        "отложен в %s.bad-…", VIDEO_HIST_PATH, ex, VIDEO_HIST_PATH)
        return []


def _vhist_valid(data: Any) -> bool:
    """Формат журнала — СПИСОК записей: объект или строка в файле — такая же поломка,
    как обрыв записи, и история из него не собирается."""
    return isinstance(data, list)


def _vhist_write(items: list[dict[str, Any]]) -> None:
    """Атомарно (core.fileio.atomic_json_dump), как ui_state: рестарт посреди записи
    не оставит огрызок. Битый журнал ПЕРЕД записью откладывается в сторону
    (core.fileio.quarantine_unreadable): без этого первая же запись статуса затирала
    файл одной записью и стирала историю оплаченных генераций."""
    try:
        os.makedirs(os.path.dirname(VIDEO_HIST_PATH), exist_ok=True)
        bad = quarantine_unreadable(VIDEO_HIST_PATH, valid=_vhist_valid)
        if bad:
            log.warning("журнал генераций не прочитан (%s) — отложен в %s, история начата "
                        "заново", VIDEO_HIST_PATH, bad)
        atomic_json_dump(VIDEO_HIST_PATH, items[-VHIST_CAP:], indent=1)
    except ReelsiError: raise
    except Exception as e:
        print("video history:", e)


def vhist_put(key: str | None, **fields: Any) -> None:
    """Создать/обновить запись задачи. Пишем на КАЖДОМ переходе статуса, а не в конце:
    если сервер убьют посреди генерации, запись останется — по ней видно, что задача
    не закончилась (и за что провайдер мог списать деньги)."""
    if not key:
        return
    with VHIST_LOCK:
        items = _vhist_read()
        for it in items:
            if it.get("key") == key:
                it.update(fields)
                break
        else:
            it = {"key": key}
            it.update(fields)
            items.append(it)
        _vhist_write(items)


def vhist_boot() -> None:
    """Рестарт сервера обрывает поток генерации, а запись остаётся «идёт» — вкладка
    показывала бы вечную задачу. Метим такие как прерванные, один раз на старте."""
    items = _vhist_read()
    lost = [it for it in items if it.get("status") == "running"]
    for it in lost:
        it["status"] = "lost"
        it["error"] = (it.get("error") or "прервано перезапуском сервера — задача могла "
                                          "досчитаться у провайдера и списаться")
    if lost:
        _vhist_write(items)


vhist_boot()


def vhist_scan_files(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ролики, о которых история не знает: сгенерированы до её появления или файл
    положили в папку руками. Импортируем как готовые задачи — иначе «что ещё можно
    скачать» показывало бы только генерации последних дней."""
    known = {paths.pkey(os.path.abspath(it["path"]))
             for it in items if it.get("path")}
    try:
        names = sorted(os.listdir(VIDEO_OUT))
    except OSError:
        return items
    new = []
    for fn in names:
        if not fn.lower().endswith((".mp4", ".webm", ".mov")):
            continue
        p = os.path.join(VIDEO_OUT, fn)
        if paths.pkey(os.path.abspath(p)) in known:
            continue
        try:
            ts = int(os.path.getmtime(p))
        except OSError:
            continue
        new.append({"key": "file:" + fn, "ts": ts, "status": "done", "path": p,
                    "prompt": "", "model": "", "found": True})
    if not new:
        return items
    items = sorted(items + new, key=lambda x: x.get("ts") or 0)
    _vhist_write(items)
    return items


def vemit(line: Any, /, **vars: Any) -> None:
    """Строка в лог генерации видео (усечение как у основного лога).

    `aicut.gen_video` пишет структурно, как основной JOB. В VJOB хранится готовая
    строка: история извлекает из неё id принятой провайдером задачи.
    """
    key: Any
    line = str(line)
    if vars:
        try:
            line = line.format(**vars)
        except ReelsiError: raise
        except Exception:
            for key, value in vars.items():
                line = line.replace("{" + str(key) + "}", str(value))
    with VLOCK:
        VJOB["log"].append(line)
        over = len(VJOB["log"]) - LOG_CAP
        if over > 0:
            del VJOB["log"][:over]
            VJOB["log_base"] += over
        key = VJOB.get("key")
    # id задачи у провайдера снимаем с лога: другого способа узнать его снаружи
    # gen_video не даёт, а если сервер убьют посреди генерации — только по нему и
    # найдёшь оплаченный ролик (он лежит у провайдера ~48ч)
    if key and " задача " in line and " принята" in line:
        task = line.split(" задача ", 1)[1].split(" принята")[0].strip()
        if task and task != "—":
            vhist_put(key, task=task)


def _video_worker(prompt: str, refs: list[dict[str, Any]], opts: dict[str, Any], key: str | None = None) -> None:
    from core import aicut
    import traceback as _tb
    try:
        res = aicut.gen_video(prompt, refs=refs, opts=opts, out_dir=VIDEO_OUT,
                              emit=vemit, should_cancel=lambda: VJOB["cancel"])
        with VLOCK:
            VJOB["result"] = {"path": res["path"], "cost": res.get("cost"),
                              "id": res.get("id"), "ms": res.get("ms"),
                              "url": "/api/media?path=" + cast(Any, urllib.request).quote(res["path"]),
                              "name": os.path.basename(res["path"])}
        vhist_put(key, status="done", path=res["path"], cost=res.get("cost"),
                  task=res.get("id") or "", ms=res.get("ms"), error="",
                  err=None, err_vars=None)
    except (ReelsiError, SystemExit) as e:
        ue = umsg_err(e)
        with VLOCK:
            VJOB["error"] = ue["error"]
            VJOB["err"] = ue.get("err")
            VJOB["err_vars"] = ue.get("err_vars")
            stopped = VJOB["cancel"]
        vemit("✗ " + ue["error"])
        # отменённое и упавшее — разные вещи: в тексте отмены сказано, приняли ли её
        # у провайдера, и это единственный след задачи, за которую могли списать
        vhist_put(key, status="cancelled" if stopped else "error", error=ue["error"],
                  err=ue.get("err"), err_vars=ue.get("err_vars"))
    except ReelsiError: raise
    except Exception:
        tb = _tb.format_exc()
        with VLOCK:
            VJOB["error"] = tb.strip().splitlines()[-1]
            VJOB["err"] = None
            VJOB["err_vars"] = None
        vemit("✗ " + tb.strip().splitlines()[-1])
        vhist_put(key, status="error", error=tb.strip().splitlines()[-1])
    finally:
        # stopped живёт только в ветке SystemExit — на обычном Exception его нет,
        # и выражение ниже падало бы NameError ВНУТРИ finally (вторая запись истории
        # терялась). Читаем всё состояние под локом, потом одна запись истории.
        with VLOCK:
            stopped = VJOB["cancel"]
            VJOB["running"] = False
            VJOB["done"] = True
            res_ok = bool(VJOB["result"])
            err = VJOB["error"]
            err_c = VJOB.get("err")
            err_v = VJOB.get("err_vars")
            started = VJOB["started"]
        # Журнал заданий: генерация — свой слот, вложение в нарезку не
        # мешает. После перезапуска сервера оборванная генерация видна как interrupted.
        journal_write("video", "video", "Генерация видео", "done", started=started)
        vhist_put(key, status="done" if res_ok else ("cancelled" if stopped else "error"),
                  error="" if res_ok or stopped else err,
                  err=err_c if not res_ok else None,
                  err_vars=err_v if not res_ok else None)


@bp.route("/api/video_gen", methods=["POST"])
def api_video_gen() -> Response:
    """Запустить генерацию видео в фоне.

    Raw-режим вкладки принимает прежние `{prompt, duration, ...}`. Карточка
    вставки передаёт только `{query, slot, speaker, insert_duration}`: личную
    приписку и подходящую длительность считает сервер.
    """
    from core import aicut
    d = request.get_json() or {}
    try:
        prof = aicut.resolve_video_profile()
        if prof is None:
            raise ReelsiError(umsg("video_disabled",
                "Генерация видео выключена — выбери профиль «Видео» в ⚙ → «Разметка и AE»"))
        # Модель и каталог провайдера нужны ДО нормализации: video_insert_duration/
        # video_resolution_cfg/video_auto_aspect/video_auto_shape считают по caps модели,
        # и раньше каталог тянул только worker — для live-only модели (её нет во
        # встроенном VIDEO_MODELS) вставка выбирала длину по пустому каталогу (3),
        # а worker потом отвергал её по supported_durations.
        model = aicut.video_model_cfg()
        aicut.ensure_video_catalog(prof)
        insert_mode = "query" in d
        if insert_mode:
            query = jstr(d, "query").strip()
            slot = jstr(d, "slot") or "a"
            if not query:
                raise ReelsiError(umsg("empty_query", "Пустой запрос — у вставки нет query"))
            if slot not in aicut.VIDEO_PROMPT_SLOTS:
                raise ReelsiError(umsg("unknown_prompt_slot", f"Неизвестный слот промпта «{slot}»",
                                      slot=slot))
            prompt = aicut.build_video_prompt(query, slot=slot, speaker=jstr(d, "speaker") or None)
            duration = aicut.video_insert_duration(d.get("insert_duration"), model)
            refs_in: list[Any] = []
            xml = jstr(d, "xml").strip().strip('"')
            if not xml or not os.path.isfile(xml):
                raise ReelsiError(umsg("file_not_found", f"Файл XML не найден: {xml}", path=xml))
            try:
                from core import xml2ae
                meta, _, _, _ = xml2ae.parse_full(xml)
                aspect = aicut.video_auto_aspect(meta.get("w"), meta.get("h"), model)
            except ReelsiError: raise
            except Exception as e:
                raise ReelsiError(umsg("video_xml_failed", f"Не удалось прочитать XML: {e}",
                                      err=str(e)))
            opts = {"model": model, "duration": duration,
                    "resolution": aicut.video_resolution_cfg(model),
                    "aspect_ratio": aspect or None}
        else:
            prompt = jstr(d, "prompt").strip()
            refs_in = d.get("refs") or []
            # И у raw-вкладки модель и разрешение ОБЩИЕ из ai_config: тело запроса
            # может прийти из устаревшей страницы, но не должно тихо запустить другую
            # модель/разрешение. duration/aspect считает сервер по референсам ниже.
            opts = {"model": model, "duration": None,
                    "resolution": aicut.video_resolution_cfg(model),
                    "aspect_ratio": None,
                    "size": d.get("size"), "seed": d.get("seed"),
                    "audio": bool(d.get("audio"))}
        # промпт обязателен у ВСЕХ моделей: запрос из одних референсов провайдер отвергает
        if not prompt:
            raise ReelsiError(umsg("empty_prompt",
                "Пустой запрос — напиши, что снять (одних референсов мало)"))
        refs = []
        for r in refs_in:
            if not isinstance(r, dict):        # элемент списка не объект — пропуск
                continue
            url = jstr(r, "url").strip()
            if not url:
                continue                                   # пустую строку-референс просто пропускаем
            if not url.lower().startswith("https://"):
                raise ReelsiError(umsg("ref_not_https",
                    f"Референс должен быть HTTPS-ссылкой (OpenRouter качает "
                    f"файл по URL, локальные не принимает): {url[:80]}", url=url[:80]))
            role = r.get("role") if r.get("role") in aicut.VIDEO_ROLES else "reference"
            # kind ставит ffprobe при вставке ссылки (расширения у ссылок часто нет);
            # не проверили — падаем на расширение
            kind = jstr(r, "kind")
            if kind not in ("video", "image", "audio", "page"):
                kind = "video" if aicut.is_video_url(url) else "image"
            # kind/duration от клиента — только ПОДСКАЗКА для мгновенной проверки ниже;
            # перед самой отправкой gen_video досматривает каждую ссылку сам (resolve_refs)
            refs.append({"url": url, "role": role, "caption": jstr(r, "caption"),
                         "kind": kind, "duration": r.get("duration") or 0,
                         "w": r.get("w") or r.get("width") or 0,
                         "h": r.get("h") or r.get("height") or 0})
        if not insert_mode:
            shape = aicut.video_auto_shape(refs, model)
            opts["duration"] = shape["duration"]
            opts["aspect_ratio"] = shape["aspect_ratio"] or None
        # Быстрая проверка правил модели (без сети) — ответом на нажатие, а не через
        # полминуты 400-м от провайдера. Длинную (ffprobe по ссылкам) делает gen_video.
        bad = aicut.video_check(model, opts, refs, prompt=prompt)
        if bad:
            raise ReelsiError(umsg("bad_opts", "; ".join(bad), list="; ".join(bad)))
        now = int(time.time())
        key = "v%d" % int(time.time() * 1000)
        # Карточка передаёт непрозрачный токен только в заголовке. Он не попадает к
        # провайдеру, но переживает F5 вместе с VJOB и связывает готовый result с
        # ИМЕННО той вставкой, а не с текущим индексом/открытой вкладкой.
        context = str(request.headers.get("X-Reelsi-Video-Context") or "").strip()[:200]
        # Проверка «уже идёт» и пометка running — ОДИН критический отрезок: между ними
        # валютная работа (генерация оплачивается), и два одновременных запроса обязаны
        # не пройти оба. Валидация выше — только чтение, ей лок не нужен.
        with VLOCK:
            if VJOB["running"]:
                raise ReelsiError(umsg("video_busy", "Генерация видео уже идёт"))
            VJOB.update(running=True, done=False, cancel=False, log=[], log_base=0,
                        result=None, error=None, started=now, key=key, context=context)
        # Журнал заданий: генерация идёт минутами и стоит денег — после
        # перезапуска сервера по журналу видно, что задача была и не закрылась.
        journal_write("video", "video", "Генерация видео", "running", started=now)
        # запись заводим ДО старта потока: задача, оборванная на первой же минуте, тоже
        # должна остаться видимой во вкладке — вместе с запросом и референсами
        vhist_put(key, ts=now, status="running", model=model, prompt=prompt,
                  opts=opts, refs=refs, path="", cost=None, ms=None, error="", task="")
        try:
            threading.Thread(target=_video_worker, args=(prompt, refs, opts, key),
                             daemon=True).start()
        except ReelsiError: raise
        except Exception:
            with VLOCK:
                VJOB["running"] = False
            raise
        return jsonify(ok=True, model=model, key=key, prompt=prompt,
                       duration=opts.get("duration"),
                       aspect_ratio=opts.get("aspect_ratio"),
                       resolution=opts.get("resolution"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/video_status")
def api_video_status() -> Response:
    """Опрос генерации видео. ?since= — сколько строк лога уже у клиента.
    interrupted — генерация, оборванная перезапуском сервера (журнал заданий, NC)."""
    try:
        since = int(request.args.get("since") or 0)
    except ValueError:
        since = 0
    with VLOCK:
        start = max(0, since - VJOB["log_base"])
        return jsonify(running=VJOB["running"], done=VJOB["done"],
                       log=VJOB["log"][start:], log_total=VJOB["log_base"] + len(VJOB["log"]),
                       result=VJOB["result"], error=VJOB["error"],
                       err=VJOB.get("err"), err_vars=VJOB.get("err_vars"),
                       key=VJOB.get("key"), context=VJOB.get("context") or "",
                       interrupted=journal_interrupted("video"),
                       elapsed=(int(time.time()) - VJOB["started"]) if VJOB["started"] else 0)


@bp.route("/api/video_history", methods=["GET", "POST"])
def api_video_history() -> Response:
    """Задачи генерации, которые помнит СЕРВЕР: готовые ролики (их ещё можно скачать),
    ошибки, отменённые и оборванные рестартом. Страница берёт список отсюда, а не из
    своей памяти, — иначе F5 стирал бы всё, кроме последнего результата.

    POST {action:"delete", key} — убрать запись. Файл удаляется ВМЕСТЕ с ней: иначе
    он вернётся в список следующим же обходом папки (vhist_scan_files), и «убрать»
    выглядело бы сломанной кнопкой."""
    if request.method == "POST":
        d = request.get_json(silent=True) or {}
        key = jstr(d, "key")
        try:
            if (jstr(d, "action") or "delete") != "delete" or not key:
                raise ReelsiError(umsg("need_delete_key", "нужен action=delete и key"))
            with VLOCK:
                busy = VJOB["running"] and VJOB.get("key") == key
            if busy:
                raise ReelsiError(umsg("video_running",
                    "Эта генерация ещё идёт — сначала останови её"))
            with VHIST_LOCK:
                items = _vhist_read()
                gone = [it for it in items if it.get("key") == key]
                _vhist_write([it for it in items if it.get("key") != key])
            removed = 0
            for it in gone:
                p = it.get("path") or ""
                # только из своей папки: путь пришёл из истории, но удалять что-то
                # вне _videogen/out по ключу из запроса всё равно нельзя
                if not (p and os.path.isfile(p)):
                    continue
                if paths.pkey(os.path.abspath(os.path.dirname(p))) != \
                   paths.pkey(os.path.abspath(VIDEO_OUT)):
                    continue
                try:
                    os.remove(p)
                    removed += 1
                except OSError as e:
                    raise ReelsiError(umsg("del_failed", f"файл не удалился: {e}", err=str(e)))
            return jsonify(ok=True, removed=removed)
        except (ReelsiError, SystemExit) as e:
            return jsonify(**umsg_err(e))
    with VHIST_LOCK:
        items = vhist_scan_files(_vhist_read())
    out: list[dict[str, Any]] = []
    for it in items:
        e = dict(it)  # type: ignore[misc]  # e переиспользован после except
        p = e.get("path") or ""  # type: ignore[misc]  # чтение e
        if p:
            # exists проверяем на КАЖДЫЙ запрос: ролики чистят руками, а «Скачать»
            # на исчезнувший файл — это 404 вместо честного «файла больше нет»
            ok = os.path.isfile(p)
            e["exists"] = ok  # type: ignore[misc]  # запись e
            e["size"] = os.path.getsize(p) if ok else 0  # type: ignore[misc]  # запись e
            e["name"] = os.path.basename(p)  # type: ignore[misc]  # запись e
            e["url"] = ("/api/media?path=" + cast(Any, urllib.request).quote(p)) if ok else ""  # type: ignore[misc]  # запись e
        out.append(e)  # type: ignore[misc]  # чтение e
    out.sort(key=lambda x: x.get("ts") or 0, reverse=True)
    with VLOCK:
        cur = VJOB.get("key") if VJOB["running"] else None
    return jsonify(ok=True, items=out, running_key=cur, out_dir=VIDEO_OUT)


@bp.route("/api/video_cancel", methods=["POST"])
def api_video_cancel() -> Response:
    """Остановить генерацию видео (поток проверяет флаг между опросами статуса)."""
    with VLOCK:
        VJOB["cancel"] = True
    return jsonify(ok=True)


@bp.route("/api/video_models", methods=["POST"])
def api_video_models() -> Response:
    """Каталог видео-моделей провайдера: GET {base}/videos/models. БЕСПЛАТНЫЙ GET,
    НЕ генерация. Запоминает supported_parameters/типы входов в aicut, чтобы gen_video
    слал только заявленное, и возвращает факты по выбранной модели (что принимает).
    body: {model?} — по какой модели выделить caps (иначе из активного видео-профиля)."""
    from core import aicut
    d = request.get_json() or {}
    try:
        prof = aicut.resolve_video_profile()
        if prof is None:
            raise ReelsiError(umsg("video_profile_missing",
                "Сначала выбери профиль «Видео» (OpenRouter-ключ и модель)"))
        model = jstr(d, "model").strip() or aicut.video_model_cfg()
        base = prof["base_url"].rstrip("/")
        headers = {"Authorization": "Bearer " + (prof.get("api_key") or ""),
                   "HTTP-Referer": APP_REFERER, "X-Title": APP_NAME}
        headers = aicut.apply_profile_headers(headers, prof)
        try:
            req = http_req(base + "/videos/models", headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except ReelsiError: raise
            except Exception:
                detail = ""
            if e.code in (401, 403):
                raise ReelsiError(umsg("key_rejected",
                    f"Ключ не принят ({e.code}) — проверь профиль «{prof['name']}»",
                    code=e.code, name=prof['name']))
            raise ReelsiError(umsg("video_catalog_failed",
                f"Каталог видео-моделей не отдался: {e.code} {detail}",
                code=e.code, detail=detail))
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("video_models_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))
    entries = data.get("data") or data.get("models") or []
    key = aicut.video._catalog_key(prof)
    aicut.video.set_video_catalog(key, entries)
    ids = sorted(aicut.video.VIDEO_MODEL_CAPS.keys())
    caps = aicut.video_caps(model, prof=prof)
    out = {"ok": True, "models": ids, "count": len(ids), "model": model,
           # выпадашка обновляется тем же ответом: встроенные + всё, что есть у провайдера
           "list": aicut.video_model_list(prof=prof),
           "found": bool(caps and caps.get("listed")),
           # живой каталог мог измениться после сохранения: переоцениваем и сбрасываем
           # устаревшее серверное значение, фронт применит итог — скрытого local state нет
           "video_resolution": aicut.video_resolution_sync(model, prof=prof)}
    if caps:
        # raw наружу не тащим (в нём длинный description) — только то, что рисует UI
        out["caps"] = {k: v for k, v in caps.items() if k != "raw"}
    return jsonify(**out)


@bp.route("/api/video_probe", methods=["POST"])
def api_video_probe() -> Response:
    """Что за файл лежит по ссылке-референсу: {kind, duration, width, height}.
    Зачем: тип нельзя брать из расширения (у ссылок его часто нет, а фото и видео
    уходят в РАЗНЫЕ поля запроса), а длину видео надо знать заранее — Seedance режет
    r2v по СУММАРНОЙ длине видео-референсов и отвечает 400. Ошибка ffprobe не
    блокирует: {ok:false} — просто гадаем по расширению, как раньше."""
    from core import aicut
    d = request.get_json() or {}
    url = jstr(d, "url").strip()
    if not url.lower().startswith("https://"):
        return jsonify(ok=False, error="нужна https-ссылка")
    info = aicut.probe_media(url)
    if not info:
        return jsonify(ok=False, kind="video" if aicut.is_video_url(url) else "image",
                       error="не удалось прочитать файл по ссылке")
    return jsonify(ok=True, **info)
