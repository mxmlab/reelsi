# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""База вставок: скан, автоподбор, импорт, правка описаний.
"""
import os
from typing import Any
from flask import Response, jsonify, request
from ._core import bp, emit, umsg_err, jstr
from core import paths
from core.umsg import ReelsiError, umsg


def _insert_dest(d: dict[str, Any]) -> str:
    """Папка базы вставок: из тела запроса («Папка базы» в модалке 📚) или дефолт."""
    return (jstr(d, "dest").strip().strip('"')
            or os.path.join(os.path.dirname(paths.ROOT), "insert_library"))


def _convert_inserts(inserts: list[Any], emit: Any = None) -> dict[str, str]:
    """Нечитаемое для AE (webp/avif, CMYK-JPEG — в PNG; AV1-видео — в H.264)
    перекодируем в читаемое (см. insertlib.to_ae_media). Гоняем по КАЖДОМУ файлу,
    а не по списку расширений: и цветовая модель, и кодек видны только внутри
    файла, а importFile на таком роняет весь .jsx целиком. Перекодировка кладёт
    новый файл РЯДОМ с исходником, сам файл не трогает.
    -> {старый: новый} для перекодированных (исходник цел)."""
    from core import insertlib
    conv: dict[str, str] = {}
    for x in inserts:
        if not isinstance(x, dict):      # элемент списка не объект — пропуск
            continue
        m = jstr(x, "media").strip()
        if m and os.path.exists(m):
            new = insertlib.to_ae_media(m, emit=emit)
            if os.path.abspath(new) != os.path.abspath(m):
                conv[os.path.abspath(m)] = new
                x["media"] = new
    return conv


def _adopt_inserts(inserts: list[Any], dest: str, emit: Any = None) -> dict[str, str]:
    """Файлы вставок, уходящих в проект, переезжают из «Скаченного» в базу
    (<dest>/photos|videos) с описанием = запрос вставки. Пути в самих вставках
    подменяются ДО сборки, чтобы .jsx ссылался уже на новое место.
    -> {старый: новый} для переехавших (фронт чинит своё состояние)."""
    from core import insertlib
    # Перекодировка — ДО переезда в базу, чтобы и в проект, и в индекс попал
    # уже читаемый файл; вынесена в отдельную функцию, потому что рендер зовёт
    # только её (переезд в базу в рендере НЕ делаем — см. _run_render_job).
    conv = _convert_inserts(inserts, emit=emit)
    # Поля памяти картинки (маска, масштаб, точка покоя, «без фона») едут вместе с описанием:
    # сборка — единственный момент, когда точно известно, как картинка стояла в ролике. Иначе
    # первая сборка переносила бы файл в базу без настройки, и в следующих роликах её пришлось
    # бы подгонять заново. Пишет их insertlib.adopt теми же правилами, что правка (_memo_apply).
    items = [{"path": jstr(x, "media"), "desc": jstr(x, "query"),
              "ru": jstr(x, "prompt"),
              **{k: x.get(k) for k in insertlib.MEMO_KEYS}}
             for x in inserts if isinstance(x, dict) and jstr(x, "media").strip()]
    if not items:
        return conv
    try:
        moved = insertlib.adopt(items, dest, emit=emit)
    except ReelsiError: raise
    except Exception as e:                          # перенос НЕ должен ронять сборку
        (emit or (lambda *a: None))(f"⚠ прибрать в базу не вышло: {e}")
        return conv
    for x in inserts:
        if not isinstance(x, dict):      # элемент списка не объект — пропуск
            continue
        m = os.path.abspath(x["media"]) if jstr(x, "media").strip() else ""
        if m in moved:
            x["media"] = moved[m]
    # маппинг для фронта: старый webp -> его PNG -> куда PNG переехал в базе
    out = dict(moved)
    for old, png in conv.items():
        out[old] = moved.get(os.path.abspath(png), png)
    return out


@bp.route("/api/insertlib_info")
def api_insertlib_info() -> Response:
    try:
        try:
            from core import insertlib
            return jsonify(ok=True, **insertlib.info())
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("insertlib_info_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/insertlib_scan", methods=["POST"])
def api_insertlib_scan() -> Response:
    """Построить/обновить индекс базы вставок по списку папок (XML прошлых проектов +
    просто медиа). Векторы — эмбеддер сервиса моделей (EmbeddingGemma 2), иначе токенный матч."""
    d = request.get_json() or {}
    raw_dirs: Any = d.get("dirs") if isinstance(d.get("dirs"), list) else []
    dirs = [x.strip() for x in raw_dirs if isinstance(x, str) and x.strip()]
    log = []
    try:
        if not dirs:
            raise ReelsiError(umsg("need_folders", "Укажи хотя бы одну папку"))
        try:
            from core import insertlib
            res = insertlib.build_index(dirs, emit=lambda *a: log.append(" ".join(str(x) for x in a)))
            return jsonify(ok=True, log=log, **res)
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("insertlib_scan_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        r = umsg_err(e)
        r["log"] = log
        return jsonify(**r)


@bp.route("/api/insertlib_reject", methods=["POST"])
def api_insertlib_reject() -> Response:
    """«Не предлагать этот файл под этот запрос» — ставится, когда юзер перегенеривает
    поверх автоподбора/генерации. Файл остаётся в базе (руками через 📚 доступен)."""
    d = request.get_json() or {}
    path, query = jstr(d, "path").strip(), jstr(d, "query").strip()
    try:
        if not path or not query:
            raise ReelsiError(umsg("need_path_query", "Нужны path и query"))
        try:
            from core import insertlib
            return jsonify(ok=insertlib.reject(path, query, on=d.get("on", True) is not False))
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("insertlib_reject_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/insertlib_match", methods=["POST"])
def api_insertlib_match() -> Response:
    """Подбор файлов из базы: {queries:[{q,type}], k} -> {results:[[{path,name,score},..],..]}.
    Один batch-вызов эмбеддера на все запросы. type ('photo'|'video') — жёсткий фильтр:
    под фото-вставку видео не предлагаем (см. match_many)."""
    d = request.get_json() or {}
    qs = d.get("queries") if isinstance(d.get("queries"), list) else []
    if not qs:
        return jsonify(results=[])
    try:
        try:
            from core import insertlib
            # Стиль текущего спикера (поле look) резолвится ТОЛЬКО здесь: insertlib
            # про спикеров знать не должен. Пустой спикер/приписка -> None (поведение
            # прежнее — стиль в подборе не участвует).
            look = None
            if jstr(d, "speaker").strip():
                from core import aicut
                extra = aicut.resolve_image_prompt_cfg("a", speaker=jstr(d, "speaker"))["extra"]
                look = insertlib._norm_look(extra) or None
            # Индексы results/texts/hints обязаны совпадать с порядком qs, поэтому
            # не-объекты не выбрасываем, а читаем как пустые.
            texts = [jstr(x, "q") if isinstance(x, dict) else "" for x in qs]
            hints = [(x.get("type") or None) if isinstance(x, dict) else None for x in qs]
            k = int(d.get("k") or 5)
            # match_many батчит эмбеддинги, но type_hint у каждого свой — группируем по hint
            results = [None] * len(qs)
            for hint in set(hints):
                idx = [i for i, h in enumerate(hints) if h == hint]
                rr = insertlib.match_many([texts[i] for i in idx], k=k, type_hint=hint,
                                          look=look)
                for i, r in zip(idx, rr):
                    results[i] = r
            return jsonify(ok=True, results=results, emb=bool(insertlib.info().get("emb_model")))
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("insertlib_match_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))



@bp.route("/api/insertlib_import", methods=["POST"])
def api_insertlib_import() -> Response:
    """Перенести медиа из папок-источников в СВОЮ папку базы (photos/videos), с даты."""
    d = request.get_json() or {}
    raw_dirs: Any = d.get("dirs") if isinstance(d.get("dirs"), list) else []
    dirs = [x.strip() for x in raw_dirs if isinstance(x, str) and x.strip()]
    dest = jstr(d, "dest").strip().strip('"')
    log = []
    try:
        if not dirs or not dest:
            raise ReelsiError(umsg("need_src_and_db", "Нужны папки-источники и папка базы"))
        try:
            import datetime as _dt
            since = 0.0
            since_str = jstr(d, "since").strip()
            if since_str:
                since = _dt.datetime.strptime(since_str, "%Y-%m-%d").timestamp()
            from core import insertlib
            res = insertlib.import_media(dirs, dest, since_ts=since,
                                         emit=lambda *a: log.append(" ".join(str(x) for x in a)))
            return jsonify(ok=True, log=log, count=res["count"], dest=res["dest"],
                           log_file=res.get("log"))
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("insertlib_import_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        r = umsg_err(e)
        r["log"] = log
        return jsonify(**r)


@bp.route("/api/insertlib_desc", methods=["POST"])
def api_insertlib_desc() -> Response:
    d = request.get_json() or {}
    try:
        try:
            from core import insertlib
            return jsonify(**insertlib.set_desc(jstr(d, "path"), jstr(d, "desc")))
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("insertlib_desc_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/insertlib_items", methods=["POST"])
def api_insertlib_items() -> Response:
    d = request.get_json() or {}
    try:
        try:
            from core import insertlib
            return jsonify(ok=True, **insertlib.items_list(q=jstr(d, "q"),
                                                           offset=int(d.get("offset") or 0),
                                                           limit=int(d.get("limit") or 50)))
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("insertlib_items_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/insert_memo", methods=["GET", "POST"])
def api_insert_memo() -> Response:
    """Память картинки вставки за файлом базы: маска, масштаб, точка покоя, «без фона».

    GET ?path= -> {ok, memo}: что запомнено; файла нет в базе — memo пустой.
    POST {path, mw, mh, sc, x, y, nobg} -> {ok, saved}: saved=false, если файла нет в базе
    (картинка ещё не переехала в базу — это не ошибка, память придёт при сборке).
    Битые поля тела пропускаются молча (см. insertlib.remember_look).
    """
    try:
        try:
            from core import insertlib
            if request.method == "GET":
                return jsonify(ok=True, memo=insertlib.look_of(request.args.get("path") or ""))
            d = request.get_json() or {}
            path = jstr(d, "path").strip()
            return jsonify(ok=True, saved=bool(path) and insertlib.remember_look(path, d))
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("insert_memo_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


# ---- стоки (Pexels/Unsplash/Pixabay и др.) как источник вставок -----------
# Поиск и скачивание живут в core/stock.py: здесь только HTTP и проверка тела
# запроса. Скачанный файл ложится В БАЗУ (<dest>/stock/<провайдер>/) и в индекс —
# дальше это обычная вставка базы, автоподбор находит её сам.
@bp.route("/api/stock_search", methods=["POST"])
def api_stock_search() -> Response:
    """Кандидаты со стоков по запросу карточки: {query, type} -> {ok, results}.

    Выдача идёт по кругу от каждого подключённого стока: первый кадр Pexels, первый
    Unsplash, первый Pixabay, потом вторые и так далее (приоритет показа — порядок
    `core.stock.PROVIDERS`; см. `core.stock.search`), чтобы в подборе были все стоки,
    а не один. Кеш ответов — 24 ч
    (условие обоих стоков), поэтому повторный поиск по тому же запросу сети не
    касается. Скачивания тут нет: сначала юзер смотрит превью глазами.
    """
    d = request.get_json() or {}
    query, kind = jstr(d, "query").strip(), (jstr(d, "type").strip() or "photo")
    try:
        try:
            from core import stock
            if not query:
                raise ReelsiError(umsg("empty_query", "Пустой запрос — у вставки нет query"))
            if kind not in stock.KINDS:
                raise ReelsiError(umsg("stock_bad_kind", f"Неизвестный тип стока «{kind}»",
                                      kind=kind))
            # Ключей нет ни у одного стока — отказ ДО запроса в сеть и с подсказкой,
            # куда ключ вписать. Код и текст те же, что в core.stock (тот проверяет
            # ключи и сам): интерфейс переводит ошибку по коду ERR_stock_no_keys, а
            # сторож переводов (tests/test_i18n.py) видит umsg-коды только в api/,
            # core/aicut/ и core/xml2ae/ — без этой ветки перевод остался бы без кода,
            # которым он объявлен, и тест словаря упал бы на «сироте».
            if not stock.has_keys():
                raise ReelsiError(umsg("stock_no_keys", stock.NO_KEYS_TEXT))
            # Формат кадра ролика: от него ориентация поиска и целевой размер файла
            # (core/frame.py). Поля в теле нет — 9:16, как было: выбор формата в
            # интерфейсе — отдельный шаг, роут обязан работать и без него.
            fmt = jstr(d, "format").strip() or None
            return jsonify(ok=True, results=stock.search(query, kind, fmt=fmt))
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("stock_search_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/stock_pick", methods=["POST"])
def api_stock_pick() -> Response:
    """Скачать выбранного кандидата в базу: {candidate, dest?} -> {ok, path, thumb}.

    `dest` — папка базы (как у генерации вставок), по умолчанию insert_library рядом
    с репозиторием. Файл уезжает в <dest>/stock/<провайдер>/ с файлом лицензии и
    попадает в индекс базы, поэтому в следующих роликах находится автоподбором без
    единого запроса к стоку.
    """
    d = request.get_json() or {}
    cand = d.get("candidate")
    dest = _insert_dest(d)
    try:
        try:
            from core import insertlib, stock
            if not isinstance(cand, dict) or not cand:
                raise ReelsiError(umsg("stock_bad_candidate", stock.BAD_CANDIDATE_TEXT,
                                      provider=""))
            path = stock.download(cand, dest, emit=emit)
            return jsonify(ok=True, path=path, thumb=insertlib._thumb_b64(path))
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("stock_pick_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/ort_gpu")
def api_ort_gpu() -> Response:
    """Снятие фона на видеокарте: что стоит (своя папка onnxruntime-gpu) и ход установки.

    Ничего не запускает и не импортирует onnxruntime: только смотрит файлы и состояние
    процесса (см. core/ort_gpu.py). Панель настроек опрашивает его, пока идёт установка.
    """
    try:
        try:
            from core import ort_gpu
            return jsonify(ok=True, **ort_gpu.status(), job=ort_gpu.job_status())
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("ort_gpu_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/ort_gpu_install", methods=["POST"])
def api_ort_gpu_install() -> Response:
    """Поставить onnxruntime-gpu в свою папку (фоном) — кнопка «Скачать» в настройках.

    Ответ сразу: колесо ~250 МБ качается минутами. Ход работы панель читает `/api/ort_gpu`
    (`job` в ответе). Второй установки поверх первой не поднимаем — `started` равно false.
    Отказы без пакета onnxruntime или без видеокарты приходят ошибкой с понятным текстом.
    """
    try:
        try:
            from core import ort_gpu
            return jsonify(ok=True, started=ort_gpu.start_install())
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("ort_gpu_install_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))
