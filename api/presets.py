# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Пресеты стиля, профили спикеров, словарь трудных терминов.

Всё это личные файлы пользователя (styles/*.json, speakers/*.json, terms.json —
в .gitignore), роуты только читают и пишут их.
"""
import os
from flask import Response, jsonify, request
from ._core import bp, umsg_err, jstr
from core.umsg import ReelsiError, umsg


@bp.route("/api/styles")
def api_styles() -> Response:
    """Все пресеты стиля (встроенные + пользовательские шаблоны) для селектора в UI."""
    try:
        try:
            from core import styles
            return jsonify(ok=True, styles=styles.all_styles())
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("styles_load_failed", str(e), err=str(e)))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/style_schema")
def api_style_schema() -> Response:
    """Схема слоёв, групп и полей стиля для панели настроек (Effect Controls)."""
    try:
        try:
            from core import style_schema
            return jsonify(ok=True, **style_schema.schema())
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("styles_load_failed", str(e), err=str(e)))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/savestyle", methods=["POST"])
def api_savestyle() -> Response:
    """Сохранить пользовательский пресет стиля как reelsi/styles/<name>.json."""
    d = request.get_json() or {}
    name = jstr(d, "name").strip()
    data = d.get("data") or {}
    try:
        if not name:
            raise ReelsiError(umsg("need_template_name", "Дай имя шаблону"))
        try:
            from core import styles
            key, path = styles.save(name, data)
            return jsonify(ok=True, key=key, path=path)
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("styles_save_failed", str(e), err=str(e)))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/style_patch", methods=["POST"])
def api_style_patch() -> Response:
    """Точечно обновить поля пользовательского пресета стиля reelsi/styles/<name>.json."""
    d = request.get_json() or {}
    name = jstr(d, "name").strip()
    patch = d.get("patch") or {}
    try:
        if not name:
            raise ReelsiError(umsg("style_name_missing", "Не указано имя стиля"))
        if not isinstance(patch, dict) or not patch:
            raise ReelsiError(umsg("empty_patch", "Пустой набор правок"))
        try:
            from core import styles
            key, path = styles.patch(name, patch)
            return jsonify(ok=True, key=key, path=path)
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("styles_patch_failed", str(e), err=str(e)))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))



@bp.route("/api/speakers")
def api_speakers() -> Response:
    """Профили спикеров для селектора: у каждого своя студия, микрофон и говор,
    а значит свои пороги нарезки (см. speakers.py). Отдаём вместе с дефолтами и
    подписями порогов — редактор профиля рисуется по ним."""
    try:
        try:
            from core import speakers
            return jsonify(ok=True, speakers=speakers.all_speakers(),
                           defaults=speakers.CUT_DEFAULTS, labels=speakers.CUT_LABELS)
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("speakers_load_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/savespeaker", methods=["POST"])
def api_savespeaker() -> Response:
    """Сохранить профиль спикера как reelsi/speakers/<name>.json."""
    d = request.get_json() or {}
    name = jstr(d, "name").strip()
    data = d.get("data") or {}
    try:
        if not name:
            raise ReelsiError(umsg("need_speaker_name", "Дай имя спикеру"))
        try:
            from core import speakers
            key, path = speakers.save(name, data)
            return jsonify(ok=True, key=key, path=path)
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("speakers_save_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/delspeaker", methods=["POST"])
def api_delspeaker() -> Response:
    """Удалить профиль спикера."""
    name = jstr(request.get_json() or {}, "name").strip()
    try:
        if not name:
            raise ReelsiError(umsg("speaker_name_missing", "Не указано имя спикера"))
        try:
            from core import speakers
            if not speakers.delete(name):
                raise ReelsiError(umsg("profile_not_found", "Профиль не найден"))
            return jsonify(ok=True)
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("speakers_del_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/terms", methods=["GET", "POST"])
def api_terms() -> Response:
    """Словарь трудных терминов (названий, которые ASR не знает).
    GET -> {terms:[{term,variants}]}. POST {terms:[...]} — перезаписать список названий
    (накопленные обучением варианты сохраняются, см. terms.set_terms).

    Поля `terms` нет (или оно `null`) и `terms` не список — ошибка `terms_failed`,
    `terms.json` не трогается: список названий приходит из интерфейса ЦЕЛИКОМ, и
    «поля нет» там неотличимо от «очистить всё». Пустой СПИСОК — по-прежнему законное
    «удалить всё» (дефект из отчёта NS)."""
    from core import terms
    if request.method == "GET":
        return jsonify(ok=True, **terms.load())
    d = request.get_json() or {}
    try:
        try:
            terms_in = d.get("terms")
            if terms_in is None:
                # Цена молчаливой записи тут — накопленный словарь трудных терминов.
                raise ReelsiError(umsg("terms_failed", "terms: поле не передано",
                                      err="terms: поле не передано"))
            if not isinstance(terms_in, list):
                # Строка раньше уезжала в словарь по символам, словарь — по ключам:
                # мусор («a», «b», «c») оставался у пользователя навсегда.
                raise TypeError(f"terms: ожидается список, пришло {type(terms_in).__name__}")
            out = terms.set_terms(terms_in)
            return jsonify(ok=True, terms=out)
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("terms_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/censor_words", methods=["GET", "POST"])
def api_censor_words() -> Response:
    """Списки цензуры субтитров. GET -> {lists:{bad:{text,custom,count}, ok:{…}}}.
    POST {bad:"…", ok:"…"} — сохранить свой список (текст как в файле, стем в строке);
    POST {reset:"bad"|"ok"|"all"} — вернуть поставочный. Ключ ответа `lists`, а не
    раскрытые bad/ok: у списка исключений имя `ok`, и оно столкнулось бы с общим
    флагом `ok=True`."""
    from core import censor
    if request.method == "GET":
        return jsonify(ok=True, lists=censor.info())
    d = request.get_json() or {}
    try:
        try:
            rst = jstr(d, "reset").strip()
            if rst:
                for k in (("bad", "ok") if rst == "all" else (rst,)):
                    if k not in ("bad", "ok"):
                        raise ReelsiError(umsg("unknown_list", f"неизвестный список: {k}", k=k))
                    censor.reset(k)
            else:
                for k in ("bad", "ok"):
                    if isinstance(d.get(k), str):
                        censor.write_text(k, d[k])
            return jsonify(ok=True, lists=censor.info())
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("censor_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/delstyle", methods=["POST"])
def api_delstyle() -> Response:
    """Удалить пользовательский пресет стиля (файл reelsi/styles/<name>.json).
    Встроенные (base/geologica — в коде styles.py) удалить нельзя."""
    name = jstr(request.get_json() or {}, "name").strip()
    try:
        if not name:
            raise ReelsiError(umsg("style_name_missing", "Не указано имя стиля"))
        try:
            from core import styles as _styles
            target = None
            if os.path.isdir(_styles.STYLE_DIR):
                for f in os.listdir(_styles.STYLE_DIR):
                    if f.lower().endswith(".json") and os.path.splitext(f)[0].lower() == name.lower():
                        target = os.path.join(_styles.STYLE_DIR, f)
                        break
            if not target:
                raise ReelsiError(umsg("builtin_style", "Это встроенный стиль — удалить нельзя"))
            os.remove(target)
            return jsonify(ok=True)
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("styles_del_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))
