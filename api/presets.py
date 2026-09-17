# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Пресеты стиля, профили спикеров, словарь трудных терминов.

Всё это личные файлы пользователя (styles/*.json, speakers/*.json, terms.json —
в .gitignore), роуты только читают и пишут их.
"""
import os
from flask import request, jsonify
from ._core import bp, umsg_err, jstr
from core.umsg import umsg


@bp.route("/api/styles")
def api_styles():
    """Все пресеты стиля (встроенные + пользовательские шаблоны) для селектора в UI."""
    try:
        try:
            from core import styles
            return jsonify(ok=True, styles=styles.all_styles())
        except Exception as e:
            raise SystemExit(umsg("styles_load_failed", str(e), err=str(e)))
    except SystemExit as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/style_schema")
def api_style_schema():
    """Схема слоёв, групп и полей стиля для панели настроек (Effect Controls)."""
    try:
        try:
            from core import style_schema
            return jsonify(ok=True, **style_schema.schema())
        except Exception as e:
            raise SystemExit(umsg("styles_load_failed", str(e), err=str(e)))
    except SystemExit as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/savestyle", methods=["POST"])
def api_savestyle():
    """Сохранить пользовательский пресет стиля как reelsi/styles/<name>.json."""
    d = request.get_json() or {}
    name = jstr(d, "name").strip()
    data = d.get("data") or {}
    try:
        if not name:
            raise SystemExit(umsg("need_template_name", "Дай имя шаблону"))
        try:
            from core import styles
            key, path = styles.save(name, data)
            return jsonify(ok=True, key=key, path=path)
        except Exception as e:
            raise SystemExit(umsg("styles_save_failed", str(e), err=str(e)))
    except SystemExit as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/style_patch", methods=["POST"])
def api_style_patch():
    """Точечно обновить поля пользовательского пресета стиля reelsi/styles/<name>.json."""
    d = request.get_json() or {}
    name = jstr(d, "name").strip()
    patch = d.get("patch") or {}
    try:
        if not name:
            raise SystemExit(umsg("style_name_missing", "Не указано имя стиля"))
        if not isinstance(patch, dict) or not patch:
            raise SystemExit(umsg("empty_patch", "Пустой набор правок"))
        try:
            from core import styles
            key, path = styles.patch(name, patch)
            return jsonify(ok=True, key=key, path=path)
        except Exception as e:
            raise SystemExit(umsg("styles_patch_failed", str(e), err=str(e)))
    except SystemExit as e:
        return jsonify(**umsg_err(e))



@bp.route("/api/speakers")
def api_speakers():
    """Профили спикеров для селектора: у каждого своя студия, микрофон и говор,
    а значит свои пороги нарезки (см. speakers.py). Отдаём вместе с дефолтами и
    подписями порогов — редактор профиля рисуется по ним."""
    try:
        try:
            from core import speakers
            return jsonify(ok=True, speakers=speakers.all_speakers(),
                           defaults=speakers.CUT_DEFAULTS, labels=speakers.CUT_LABELS)
        except Exception as e:
            raise SystemExit(umsg("speakers_load_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except SystemExit as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/savespeaker", methods=["POST"])
def api_savespeaker():
    """Сохранить профиль спикера как reelsi/speakers/<name>.json."""
    d = request.get_json() or {}
    name = jstr(d, "name").strip()
    data = d.get("data") or {}
    try:
        if not name:
            raise SystemExit(umsg("need_speaker_name", "Дай имя спикеру"))
        try:
            from core import speakers
            key, path = speakers.save(name, data)
            return jsonify(ok=True, key=key, path=path)
        except Exception as e:
            raise SystemExit(umsg("speakers_save_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except SystemExit as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/delspeaker", methods=["POST"])
def api_delspeaker():
    """Удалить профиль спикера."""
    name = jstr(request.get_json() or {}, "name").strip()
    try:
        if not name:
            raise SystemExit(umsg("speaker_name_missing", "Не указано имя спикера"))
        try:
            from core import speakers
            if not speakers.delete(name):
                raise SystemExit(umsg("profile_not_found", "Профиль не найден"))
            return jsonify(ok=True)
        except Exception as e:
            raise SystemExit(umsg("speakers_del_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except SystemExit as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/terms", methods=["GET", "POST"])
def api_terms():
    """Словарь трудных терминов (названий, которые ASR не знает).
    GET -> {terms:[{term,variants}]}. POST {terms:[...]} — перезаписать список названий
    (накопленные обучением варианты сохраняются, см. terms.set_terms)."""
    from core import terms
    if request.method == "GET":
        return jsonify(ok=True, **terms.load())
    d = request.get_json() or {}
    try:
        try:
            out = terms.set_terms(d.get("terms") or [])
            return jsonify(ok=True, terms=out)
        except Exception as e:
            raise SystemExit(umsg("terms_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except SystemExit as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/censor_words", methods=["GET", "POST"])
def api_censor_words():
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
                        raise SystemExit(umsg("unknown_list", f"неизвестный список: {k}", k=k))
                    censor.reset(k)
            else:
                for k in ("bad", "ok"):
                    if isinstance(d.get(k), str):
                        censor.write_text(k, d[k])
            return jsonify(ok=True, lists=censor.info())
        except Exception as e:
            raise SystemExit(umsg("censor_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except SystemExit as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/delstyle", methods=["POST"])
def api_delstyle():
    """Удалить пользовательский пресет стиля (файл reelsi/styles/<name>.json).
    Встроенные (base/geologica — в коде styles.py) удалить нельзя."""
    name = jstr(request.get_json() or {}, "name").strip()
    try:
        if not name:
            raise SystemExit(umsg("style_name_missing", "Не указано имя стиля"))
        try:
            from core import styles as _styles
            target = None
            if os.path.isdir(_styles.STYLE_DIR):
                for f in os.listdir(_styles.STYLE_DIR):
                    if f.lower().endswith(".json") and os.path.splitext(f)[0].lower() == name.lower():
                        target = os.path.join(_styles.STYLE_DIR, f)
                        break
            if not target:
                raise SystemExit(umsg("builtin_style", "Это встроенный стиль — удалить нельзя"))
            os.remove(target)
            return jsonify(ok=True)
        except Exception as e:
            raise SystemExit(umsg("styles_del_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except SystemExit as e:
        return jsonify(**umsg_err(e))
