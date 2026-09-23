# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Слова, субтитры, редактор нарезки: всё, что правит XML и сайдкары.
"""
import os, json
from typing import Any, Sequence, cast
from flask import request, jsonify, Response
from core.fileio import atomic_json_dump
from core.project_file import ProjectFile, read_project, write_project
from ._core import bp, emit, is_reelsi_target, umsg_err, jstr
from core.umsg import ReelsiError, umsg
from core.applog import get_logger

log = get_logger(__name__)


def _sidecar_yellow(xml_path: str) -> list[int]:
    """Indices from <stem>.yellow.json next to the XML (e.g. written by aicut)."""
    p = os.path.splitext(xml_path)[0] + ".yellow.json"
    if not os.path.isfile(p):
        return []
    try:
        d = json.load(open(p, encoding="utf-8"))
    except ReelsiError: raise
    except Exception:
        return []
    # канонический формат (aicut/set_yellow) — {"yellow": [..]}; старый set_yellow писал
    # голый список [..] — принимаем оба, иначе существующие сайдкары молча отваливались
    if isinstance(d, list):
        return [int(i) for i in d]
    try:
        return [int(i) for i in (d.get("yellow") or [])]
    except ReelsiError: raise
    except Exception:
        return []


def _sidecar_caption(xml_path: str) -> str:
    """Text from <stem>.caption.json next to the XML."""
    p = os.path.splitext(xml_path)[0] + ".caption.json"
    if not os.path.isfile(p):
        return ""
    try:
        d = json.load(open(p, encoding="utf-8"))
    except ReelsiError: raise
    except Exception:
        return ""
    if isinstance(d, dict):
        return str(d.get("text") or "").strip()
    if isinstance(d, str):
        return d.strip()
    return ""


@bp.route("/api/caption", methods=["POST"])
def api_caption() -> Response:
    """Подпись о ролике (<stem>.caption.json рядом с XML): чтение и сохранение."""
    d = request.get_json() or {}
    xml_path = jstr(d, "xml").strip().strip('"')
    try:
        if not os.path.isfile(xml_path):
            raise ReelsiError(umsg("file_not_found", f"Файл не найден: {xml_path}",
                                  path=xml_path))
        try:
            if "text" in d:
                text = jstr(d, "text").strip()
                p = os.path.splitext(xml_path)[0] + ".caption.json"
                atomic_json_dump(p, {"text": text}, indent=1)
                return jsonify(ok=True, text=text)
            else:
                return jsonify(ok=True, text=_sidecar_caption(xml_path))
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("caption_failed", str(e), err=str(e)))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/words", methods=["POST"])
def api_words() -> Response:
    """Return the ordered subtitle words of an edited sequence XML so the UI can
    let the user click which to highlight (yellow). Indices match to_ae_full."""
    xml_path = jstr(request.get_json() or {}, "xml").strip().strip('"')
    try:
        if not os.path.isfile(xml_path):
            raise ReelsiError(umsg("file_not_found", f"Файл не найден: {xml_path}",
                                  path=xml_path))
        try:
            from core import xml2ae
            meta, _, subs, _ = xml2ae.parse_full(xml_path)
            fps = meta["fps"]
            auto = xml2ae.auto_highlights(xml_path)          # цветные слова из Премьера -> жёлтые + разделители
            yellow = [i for i in (auto["yellow"] or _sidecar_yellow(xml_path)) if 0 <= i < len(subs)]
            breaks = auto["breaks"] if auto["yellow"] else []
            return jsonify(ok=True, fps=fps, yellow=yellow, breaks=breaks,
                           words=[{"i": k, "w": w, "start": round(s / fps, 2)}
                                  for k, (s, e, w) in enumerate(subs)])
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("words_failed", str(e), err=str(e)))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/xml_state", methods=["POST"])
def api_xml_state() -> Response:
    """Что уже есть в XML — чтобы «Разметить всё» пропускало готовые шаги:
    subs = число слов-субтитров, colored = число НЕ-белых слов (цвет из Премьера,
    как их видит AE-парсер auto_highlights). НЕ учитывает сайдкар .yellow.json —
    только реальную разметку цветом в самом XML."""
    xml = jstr(request.get_json() or {}, "xml").strip().strip('"')
    try:
        if not os.path.isfile(xml):
            raise ReelsiError(umsg("file_not_found", f"Файл не найден: {xml}",
                                  path=xml))
        try:
            from core import xml2ae
            _, cam_tracks, subs, _ = xml2ae.parse_full(xml)
            colored = len(xml2ae.auto_highlights(xml).get("yellow") or [])
            return jsonify(ok=True, subs=len(subs), colored=colored,
                           ncams=max(1, len(cam_tracks)))
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("xml_state_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/omnicut_cuts", methods=["POST"])
def api_omnicut_cuts() -> Response:
    """Вернуть cut-log (<stem>.cuts.json рядом с XML) — что и почему вырезано Omni-нарезкой."""
    xml_path = jstr(request.get_json() or {}, "xml").strip().strip('"')
    p = os.path.splitext(xml_path)[0] + ".cuts.json"
    if not os.path.isfile(p):
        return jsonify(ok=True, cuts=[])
    try:
        try:
            return jsonify(ok=True, cuts=json.load(open(p, encoding="utf-8")))
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("omnicut_cuts_failed", str(e), err=str(e)))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/breaths", methods=["POST"])
def api_breaths() -> Response:
    """Метки вздохов/«кхе» (<stem>.breaths.json) — редактор рисует их на таймлайне.

    Уверенные детектор вырезал сам ещё в нарезке (в сайдкаре они помечены
    `вырезано`), здесь важны СПОРНЫЕ: юзер снимает их одним кликом. Нет файла —
    пустой список: нарезка могла идти без детектора (нет моделей или весов).

    Цель проверяем, как удаляющие роуты: сайдкар читается рядом с
    ПРИСЛАННЫМ путём, и без проверки тело с чужим именем отдавало бы
    `notes.breaths.json` из чужой папки. Интерфейс всегда шлёт путь нарезки —
    для него поведение не меняется."""
    xml_path = jstr(request.get_json() or {}, "xml").strip().strip('"')
    if not is_reelsi_target(xml_path, "cut"):
        return jsonify(**umsg_err(ReelsiError(umsg("not_a_cut",
                                                  f"Это не нарезка Reelsi: {xml_path}",
                                                  path=xml_path))))
    p = os.path.splitext(xml_path)[0] + ".breaths.json"
    if not os.path.isfile(p):
        return jsonify(ok=True, marks=[])
    try:
        try:
            return jsonify(ok=True, marks=json.load(open(p, encoding="utf-8")))
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("breaths_failed", str(e), err=str(e)))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/editor_load", methods=["POST"])
def api_editor_load() -> Response:
    """Блоки нарезки для редактора: оставленные куски исходника (камера 1) в секундах.
    Из сайдкара <stem>.project.json (есть offsets/cams для пересборки) или из XML."""
    xml = jstr(request.get_json() or {}, "xml").strip().strip('"')
    try:
        if not os.path.isfile(xml):
            raise ReelsiError(umsg("file_not_found", f"Файл не найден: {xml}",
                                  path=xml))
        try:
            p = _ensure_project(xml)                      # сайдкар или реконструкция из XML
            return jsonify(ok=True, fps=p.get("fps", 60), cam=p["cams"][0],
                           keep=p.get("keep", []), have_proj=True)
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("editor_load_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


def _reproject_subs(
    sub_words: list[dict[str, Any]] | None,
    yellow: Sequence[int],
    old_keep: Sequence[Sequence[float]],
    new_keep: Sequence[Sequence[float]],
    old_fps: float,
    new_fps: float | None = None,
) -> tuple[list[dict[str, Any]] | None, list[int]]:
    """Перенести слова-субтитры и жёлтые со СТАРОГО монтажа на новый. -> (sub_words|None, yellow)

    Слова живут в кадрах ТАЙМЛАЙНА, а таймлайн собирается курсором по списку кусков
    (xmlbuild.build): убрали кусок в начале — всё, что дальше, поехало влево. Поэтому
    переносить слова как есть нельзя. Регресс 2026-08-13: правка блоков отдавала в
    пересборку прежние sub_words, слова оставались на старых кадрах — рассинхрон
    ровно в длину удалённого куска, а слова из выброшенных кусков подписывали чужую
    речь. Переводим кадр таймлайна -> кадр ИСХОДНИКА по старому keep и обратно по
    новому; что попало в удалённые куски — выбрасываем.

    ЧАСТОТ ДВЕ, и стороны разные по природе: старый таймлайн — кадры входного XML, то
    есть частота проекта (25, 29.97 — что пришло из Премьера), новый — кадры НАШЕЙ
    секвенции, `xmlbuild.FPS` = 60, `build` всегда пишет 60. Одна частота на обе стороны
    уводила слово на чужое время: у 25-кадрового проекта — в 60/25 = 2.4 раза дальше,
    чем оно звучит. Общий язык сторон — СЕКУНДЫ исходника: кадр старого таймлайна ->
    секунда -> кадр нового. new_fps=None — прежние вызовы: частота одна на обе стороны.

    Жёлтые — позиции в списке слов (см. auto_highlights), после выброса они съезжают,
    поэтому пересчитываем их на новый порядок, иначе покрасились бы соседи.
    """
    if not sub_words:
        return None, []
    if new_fps is None:
        new_fps = old_fps
    same_keep = ([(round(s, 3), round(e, 3)) for s, e in old_keep] ==
                 [(round(s, 3), round(e, 3)) for s, e in new_keep])
    # Монтаж тот же (случай cams_save) — кадры не трогаем. Но это верно ТОЛЬКО при равных
    # частотах: у 25-кадрового проекта слова всё равно надо перевести в кадры 60.
    if not old_keep or (same_keep and old_fps == new_fps):
        return sub_words, list(yellow)

    def _spans(keep: Sequence[Sequence[float]], fps: float) -> list[tuple[int, int, int]]:
        """[(начало_на_таймлайне, конец, начало_в_исходнике)] в кадрах СВОЕЙ частоты —
        курсором, как xmlbuild."""
        out: list[tuple[int, int, int]]
        out, tl = [], 0
        for s, e in keep:
            in0, out0 = round(s * fps), round(e * fps)
            if out0 - in0 <= 0:                       # такие куски xmlbuild пропускает
                continue
            out.append((tl, tl + (out0 - in0), in0))
            tl += out0 - in0
        return out

    old_sp, new_sp = _spans(old_keep, old_fps), _spans(new_keep, new_fps)
    if not old_sp or not new_sp:
        return sub_words, list(yellow)

    words, kept_idx = [], []
    for i, wd in enumerate(sub_words):
        f0 = int(wd["start"])
        src = next((s0 + (f0 - a) for a, b, s0 in old_sp if a <= f0 < b), None)
        if src is None:
            continue                                  # слово вне старого монтажа — мусор
        t_src = src / old_fps                         # секунда исходника — общий язык сторон
        src_new = round(t_src * new_fps)
        seg = next(((a, b, s0) for a, b, s0 in new_sp if s0 <= src_new < s0 + (b - a)), None)
        if seg is None:
            continue                                  # этот кусок исходника удалили
        ns = seg[0] + (src_new - seg[2])
        # длину сохраняем, но за границу своего куска не пускаем: слово на стыке
        # иначе наехало бы на следующий кусок, где звучит уже другое слово
        ne = min(ns + max(1, round((int(wd["end"]) - f0) / old_fps * new_fps)), seg[1])
        if ne <= ns:
            continue
        words.append({"w": wd["w"], "start": ns, "end": ne})
        kept_idx.append(i)

    pos = {old: new for new, old in enumerate(kept_idx)}
    return (words or None), [pos[i] for i in yellow if i in pos]


@bp.route("/api/editor_save", methods=["POST"])
def api_editor_save() -> Response:
    """Пересобрать XML из отредактированных блоков (оставленные куски исходника)."""
    d = request.get_json() or {}
    xml = jstr(d, "xml").strip().strip('"')
    keep = d.get("keep") or []
    try:
        if not os.path.isfile(xml):
            raise ReelsiError(umsg("file_not_found", f"Файл не найден: {xml}",
                                  path=xml))
        try:
            proj_path = os.path.splitext(xml)[0] + ".project.json"
            from core import align
            from core import xmlbuild
            from core import xml2ae
            p = _ensure_project(xml)                      # сайдкар или реконструкция из XML
            cams = p["cams"]; offsets = p["offsets"]; fps = p.get("fps", 60); N = len(cams)
            segs = [(float(s), float(e)) for s, e in keep if float(e) - float(s) > 1.0/fps]
            segs.sort()
            # субтитры и жёлтые СОХРАНЯЕМ (как cams_save), но не «как есть»: блоки изменились,
            # значит таймлайн поехал — слова переносим через исходник (см. _reproject_subs).
            # Было sub_words=None — пересборка молча стирала субтитр-графику и жёлтые.
            old_keep = [(float(s), float(e)) for s, e in (p.get("keep") or [])]
            _meta, _pcams, subs, _ins = xml2ae.parse_full(xml)
            sub_words = ([{"w": w, "start": int(s), "end": int(e)} for (s, e, w) in subs]
                         if subs else None)
            yellow = xml2ae.auto_highlights(xml).get("yellow", [])
            # Частоты РАЗНЫЕ: старые кадры — частота проекта (входной XML), новые — 60
            # (столько пишет build). Одна частота уводила слова на чужое время.
            sub_words, yellow = _reproject_subs(sub_words, yellow, old_keep, segs, fps,
                                                xmlbuild.FPS)
            assign = align.assign_cameras(segs, N, return_every=p.get("cam_return", 2),
                                          big_chunk_sec=6.0) if N > 1 else None
            try:
                info = xmlbuild.build(cams, segs, offsets, xml, assign=assign,
                                      scale=p.get("scale", 50.4), sub_words=sub_words, music_path=None)
            except (ReelsiError, SystemExit) as e:
                # Пустой монтаж (убрали все блоки): build файл не тронул — отдаём отказ
                # роута с текстом гарда КАК ЕСТЬ, а не «SystemExit: …».
                raise ReelsiError(umsg("editor_save_failed", str(e), err=str(e)))
            from core import xml2ae
            xml2ae.write_srt_for(xml)
            if yellow:
                xml2ae.write_highlights(xml, yellow)     # вернуть жёлтые (цвет в XML)
            # память правок: что юзер ВЕРНУЛ (не было в прошлом keep) и что УДАЛИЛ — при
            # повторной нарезке LLM получает «не выкидывай похожее», куски защищаются жёстко
            if old_keep:
                try:
                    om = json.load(open(os.path.splitext(xml)[0] + ".omni.json", encoding="utf-8"))
                except ReelsiError: raise
                except Exception:
                    om = []

                def _mark(rng_list: Sequence[Sequence[float]]) -> list[dict[str, Any]]:
                    out: list[dict[str, Any]] = []
                    for (s, e) in rng_list:
                        if e - s < 0.3:                    # дрожание краёв — не правка
                            continue
                        txt = " ".join(t.get("text", "") for t in om
                                       if float(t.get("start", 0)) < e and float(t.get("end", 0)) > s)
                        out.append({"t0": round(s, 2), "t1": round(e, 2), "text": txt.strip()[:160]})
                    return out
                restored = _mark(align.subtract_ranges(segs, old_keep))
                deleted = _mark(align.subtract_ranges(old_keep, segs))
                if restored or deleted:
                    uo = p.get("user_overrides") or {}
                    seen = {(o.get("t0"), o.get("t1"))
                            for o in (uo.get("restored") or []) + (uo.get("deleted") or [])}
                    uo["restored"] = ((uo.get("restored") or [])
                                      + [o for o in restored if (o["t0"], o["t1"]) not in seen])[-50:]
                    uo["deleted"] = ((uo.get("deleted") or [])
                                     + [o for o in deleted if (o["t0"], o["t1"]) not in seen])[-50:]
                    p["user_overrides"] = uo
            p["keep"] = [[round(s, 3), round(e, 3)] for s, e in segs]
            p.pop("assign", None)                         # блоки изменились -> ручная раскладка камер устарела
            write_project(proj_path, p)
            return jsonify(ok=True, segs=len(segs), dur=round(info.get("total_s", 0), 1))
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("editor_save_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


def _project_from_xml(xml: str) -> ProjectFile:
    """Реконструировать проект (камеры, синхрон, оставленные куски) из готового XML —
    чтобы редактор/субтитры работали БЕЗ сайдкара. offsets выводим из клипов камер."""
    from core import xml2ae
    meta, cams, subs, ins = xml2ae.parse_full(xml)
    fps = meta["fps"]
    cam1 = sorted(cams[0]["clips"])
    keep = [[round(i/fps, 3), round(o/fps, 3)] for (s, e, i, o, en, *r) in cam1 if en and e > s]
    offsets = [0.0]
    for k in range(1, len(cams)):
        off = 0.0
        for (sk, ek, ink, ok, enk, *rk) in sorted(cams[k]["clips"]):
            if not enk:
                continue
            for (s1, e1, in1, o1, en1, *r1) in cam1:
                if s1 <= sk < e1:
                    off = round(((in1 + (sk - s1)) - ink) / fps, 3); break
            break
        offsets.append(off)
    return {"cams": [c["path"] for c in cams], "offsets": offsets, "fps": fps,
            "cam_return": 2, "scale": 50.4, "keep": keep}


def _ensure_project(xml: str) -> ProjectFile:
    """Проект из сайдкара, а если нет — реконструировать из XML и сохранить сайдкар."""
    p = os.path.splitext(xml)[0] + ".project.json"
    if os.path.isfile(p):
        # Рваный файл (крах/отбой в момент старой неатомарной записи) не должен
        # валить весь редактор: пересоберём проект заново и перепишем сайдкар.
        proj = read_project(p)
        if proj is not None:
            return proj
    proj = _project_from_xml(xml)
    try:
        write_project(p, proj)
    except ReelsiError: raise
    except Exception as ex:
        log.warning("не записал project.json (%s): %s", p, ex)
    return proj


@bp.route("/api/asr_engines")
def api_asr_engines() -> Response:
    """Список ASR-движков (кто слушает звук) — один источник истины для селекторов:
    самопроверка стыков на главной и «Движок субтитров» на шаге 2. Встроенные +
    пользовательские CTC-модели других языков из asr_engines.json (файл читается
    на каждый запрос — добавил язык, нажал F5, движок в списке)."""
    try:
        try:
            from core import asr_backends
            return jsonify(engines=asr_backends.engines())
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("asr_engines_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        r = umsg_err(e)
        r["engines"] = []
        return jsonify(**r)


@bp.route("/api/gen_subs", methods=["POST"])
def api_gen_subs() -> Response:
    """Субтитры С НУЛЯ: склеить аудио нарезки (камера 1 по keep) → Whisper → вписать
    субтитр-графику в XML. Нужен <stem>.project.json."""
    data = request.get_json() or {}
    xml = jstr(data, "xml").strip().strip('"')
    subengine = jstr(data, "subengine") or "whisper"
    try:
        if not os.path.isfile(xml):
            raise ReelsiError(umsg("file_not_found", f"Файл не найден: {xml}",
                                  path=xml))
        try:
            import numpy as np, librosa, soundfile as sf, tempfile
            from core import align
            from core import xmlbuild
            from core import asr_backends
            p = _ensure_project(xml)                      # сайдкар или реконструкция из XML
            cams = p["cams"]; offsets = p["offsets"]; N = len(cams)
            keep = [(float(s), float(e)) for s, e in p["keep"]]
            y, sr = librosa.load(cams[0], sr=16000, mono=True)
            parts = [y[int(s*16000):int(e*16000)] for s, e in keep]
            cut = np.concatenate(parts) if parts else y[:0]
            # Имя уникальное: Flask threaded=True, и два запроса субтитров (два окна,
            # «Разметить всё» + разметка клипа) писали в ОДИН _gensubs.wav — субтитры
            # одного клипа молча уезжали в XML другого. Файл убираем за собой.
            fd, tmp = tempfile.mkstemp(prefix="_gensubs_", suffix=".wav")
            os.close(fd)
            try:
                sf.write(tmp, cut, 16000, subtype="PCM_16")
                # Единая точка входа в плаггable ASR-бэкенды (каждый сам управляет VRAM
                # и выгружает свою модель после транскрипции).
                # emit — чтобы подмены по словарю терминов были видны в логе, а не молча
                words = asr_backends.transcribe_words(tmp, engine=subengine, emit=emit)  # [{w,start,end}] сек на cut-таймлайне
                words_path = os.path.splitext(xml)[0] + ".words.json"
                atomic_json_dump(words_path, words, indent=1)
            finally:
                try:
                    os.remove(tmp)
                except ReelsiError: raise
                except OSError:
                    pass  # временный файл уже удалён
            # Кадры слов — нашей секвенции (build пишет 60), а не проекта: у 25-кадрового
            # проекта слово уезжало в 2.4 раза дальше, чем звучит.
            sub_words = [{"w": w["w"], "start": round(w["start"]*xmlbuild.FPS),
                          "end": round(w["end"]*xmlbuild.FPS)}
                         for w in words]
            stored = p.get("assign")                      # ручная раскладка камер (если валидна по длине)
            if N > 1 and isinstance(stored, list) and len(stored) == len(keep):
                assign = [max(0, min(N - 1, int(x))) for x in stored]
            elif N > 1:
                assign = align.assign_cameras(keep, N, return_every=p.get("cam_return", 2),
                                              big_chunk_sec=6.0)
            else:
                assign = None
            try:
                info = xmlbuild.build(cams, keep, offsets, xml, assign=assign,
                                      scale=p.get("scale", 50.4), sub_words=sub_words, music_path=None)
            except (ReelsiError, SystemExit) as e:
                # Пустой монтаж: build файл не тронул — текст гарда отдаём как есть.
                raise ReelsiError(umsg("gen_subs_failed", str(e), err=str(e)))
            from core import xml2ae
            xml2ae.write_srt_for(xml)
            return jsonify(ok=True, subs=info.get("subtitles", len(sub_words)), words=len(words),
                           engine=subengine,
                           skipped=info.get("long_words") or [])   # слова, не влезшие в шаблон
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("gen_subs_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/aicut_preview", methods=["POST"])
def api_aicut_preview() -> Response:
    """Parse a produced timeline XML into a virtual timeline the browser can play
    straight from the source camera files (no rendering). Returns the ordered list
    of enabled cut segments (which camera + source time) plus subtitle words."""
    xml_path = jstr(request.get_json() or {}, "xml").strip().strip('"')
    try:
        if not os.path.isfile(xml_path):
            raise ReelsiError(umsg("file_not_found", f"Файл не найден: {xml_path}",
                                  path=xml_path))
        try:
            from core import xml2ae
            edl = xml2ae.virtual_edl(xml_path)      # общий EDL-парсер (реюз в draft-рендере)
            return jsonify(ok=True, **edl)
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("aicut_preview_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/scanxml", methods=["POST"])
def api_scanxml() -> Response:
    """Все .xml в папке (для «подхватить клипы из папки выхода» — список клипов живёт
    в localStorage и в другом браузере/после чистки пустой)."""
    d = request.get_json() or {}
    dir_ = jstr(d, "dir").strip().strip('"')
    try:
        if not os.path.isdir(dir_):
            raise ReelsiError(umsg("no_folder", f"Нет папки: {dir_}", path=dir_))
        try:
            files = sorted(os.path.join(dir_, f) for f in os.listdir(dir_)
                           if f.lower().endswith(".xml"))
            return jsonify(ok=True, paths=files)
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("scanxml_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/set_yellow", methods=["POST"])
def api_set_yellow() -> Response:
    """Явно задать набор жёлтых слов в XML (ручная разметка из предпросмотра):
    выбранные красим, ранее покрашенные но снятые — возвращаем в белый. Пишет и сайдкар."""
    d = request.get_json() or {}
    xml = jstr(d, "xml").strip().strip('"')
    try:
        if not os.path.isfile(xml):
            raise ReelsiError(umsg("file_not_found", f"Файл не найден: {xml}",
                                  path=xml))
        if "indices" not in d or not isinstance(d["indices"], list):
            raise ReelsiError(umsg("bad_indices", "Поле indices должно быть списком"))
        try:
            from core import xml2ae
            idx = [int(i) for i in d["indices"]]
            res = xml2ae.set_highlights(xml, idx)
            try:                                             # сайдкар .yellow.json — фолбэк для /api/words
                atomic_json_dump(os.path.splitext(xml)[0] + ".yellow.json",
                                  {"yellow": sorted(res.get("colored", []))})
            except ReelsiError: raise
            except Exception as ex:
                log.warning("сайдкар .yellow.json не записан (%s): %s",
                            os.path.splitext(xml)[0] + ".yellow.json", ex)
            return jsonify(ok=True, colored=res.get("colored", []),
                           skipped=[list(s) for s in res.get("skipped", [])])
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("set_yellow_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/clear_subs", methods=["POST"])
def api_clear_subs() -> Response:
    """Убрать из XML субтитр-графику целиком (крестик на теге «субтитры» на шаге 2).
    Пересобираем нарезку теми же keep/раскладкой, но без sub_words — то есть ровно то,
    что было до /api/gen_subs. Жёлтые уходят ВМЕСТЕ с субтитрами: они живут цветом на
    словах-титрах, без слов хранить их негде (и индексы всё равно указывали бы в пустоту).
    Сайдкар .yellow.json тоже сносим, иначе /api/words вернул бы жёлтые от старых слов."""
    from core import xmlbuild
    d = request.get_json() or {}
    xml = jstr(d, "xml").strip().strip('"')
    try:
        if not os.path.isfile(xml):
            raise ReelsiError(umsg("file_not_found", f"Файл не найден: {xml}",
                                  path=xml))
        try:
            p = _ensure_project(xml)
            cams = p["cams"]; offsets = p["offsets"]; N = len(cams)
            keep = [(float(s), float(e)) for s, e in p.get("keep", [])]
            stored = p.get("assign")
            assign = ([max(0, min(N - 1, int(x))) for x in stored]
                      if N > 1 and isinstance(stored, list) and len(stored) == len(keep) else None)
            if assign is None and N > 1:
                from core import align
                assign = align.assign_cameras(keep, N, return_every=p.get("cam_return", 2),
                                              big_chunk_sec=6.0)
            try:
                info = xmlbuild.build(cams, keep, offsets, xml, assign=assign,
                                      scale=p.get("scale", 50.4), sub_words=None, music_path=None)
            except (ReelsiError, SystemExit) as e:
                # Пустой монтаж: build файл не тронул — текст гарда отдаём как есть.
                raise ReelsiError(umsg("clear_subs_failed", str(e), err=str(e)))
            try:
                os.remove(os.path.splitext(xml)[0] + ".yellow.json")
            except OSError:
                pass  # сайдкара .yellow.json и не было — чистить нечего
            return jsonify(ok=True, segs=len(keep), dur=round(info.get("total_s", 0), 1))
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("clear_subs_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/edit_word", methods=["POST"])
def api_edit_word() -> Response:
    """Переписать текст слова-субтитра #index (порядок parse_full) прямо в XML.

    Правка «на известный термин» ЗАПОМИНАЕТСЯ: ослышка ASR уходит вариантом в
    terms.json, и в следующих роликах то же слово чинится само (см. terms.learn).
    Термин при этом должен уже быть в словаре — иначе туда поехали бы обычные опечатки."""
    d = request.get_json() or {}
    xml = jstr(d, "xml").strip().strip('"')
    try:
        if not os.path.isfile(xml):
            raise ReelsiError(umsg("file_not_found", f"Файл не найден: {xml}",
                                  path=xml))
        try:
            from core import xml2ae
            was = jstr(d, "was").strip()
            res = xml2ae.edit_word(xml, int(cast(Any, d.get("index"))), jstr(d, "text"))
            if res.get("error"):
                raise ReelsiError(umsg("edit_word_failed", res["error"], err=res["error"]))
            learned = None
            if was:
                try:
                    from core import terms
                    learned = terms.learn(was, res.get("word") or "")
                except ReelsiError: raise
                except Exception:
                    pass                                     # словарь не должен ломать правку
            # Ручная звёздочка в редакторе НИКУДА не запоминается. Раньше исходное слово
            # само уезжало в badwords.user.txt, список зарастал мусором выравнивания
            # («и», «из», «тет»), а сверка идёт по ПОДСТРОКЕ — одна основа «и» зацензурила
            # 106 слов из 203 в клипе. Список плохих слов правится только руками:
            # ⚙ → «Слова».
            return jsonify(ok=True, word=res.get("word"), learned=learned)
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("edit_word_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/delete_word", methods=["POST"])
def api_delete_word() -> Response:
    """Удалить слово-субтитр #index (порядок parse_full) прямо из XML."""
    d = request.get_json() or {}
    xml = jstr(d, "xml").strip().strip('"')
    try:
        if not os.path.isfile(xml):
            raise ReelsiError(umsg("file_not_found", f"Файл не найден: {xml}",
                                  path=xml))
        try:
            from core import xml2ae
            res = xml2ae.delete_word(xml, int(cast(Any, d.get("index"))))
            if res.get("error"):
                raise ReelsiError(umsg("delete_word_failed", res["error"], err=res["error"]))
            return jsonify(ok=True, index=res.get("index"), word=res.get("word"))
        except ReelsiError: raise
        except Exception as e:
            raise ReelsiError(umsg("delete_word_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))

