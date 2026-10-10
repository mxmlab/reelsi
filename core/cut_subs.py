# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Субтитры нарезки по словам ИСХОДНИКА: одна дверь для первой нарезки и для правок блоков.

ПОЧЕМУ модуль есть. При включённом втором движке текста (`active_cut_text_asr`) слова
распознаются по ВСЕМУ исходнику, а в XML попадают слова только оставленных кусков.
Правка нарезки на шаге 1 (вернуть кусок, вырезать, сдвинуть границу) пересобирала XML по
субтитрам с ТЕКУЩЕГО таймлайна: слова вернувшегося куска нигде не лежали и пропадали.
Теперь слова исходника сохраняются рядом с нарезкой. Правка блоков (`subs_after_edit`):
слова оставшихся отрезков — из ТЕКУЩЕГО XML (с ручными правками и удалениями), слова
вернувшихся отрезков и расширенных границ — из слов исходника, вырезанное уходит.
Первая нарезка — `subs_for_keep`, та же дорога слов в кадры.

Сайдкары нарезки:
- `<stem>.srcwords.json` — слова исходника `{w, start, end}` в СЕКУНДАХ исходника
  (камера 1, по тому же звуку, что и `keep`). Полный список, не только оставленные куски.
- `project.json`, поле `text_subs` — нарезка сделана со вторым проходом, субтитры в XML
  взяты у исходника. Без флага субтитры прежние (шаг 2 или старый путь), правка их не трогает.
  Флаг — признак, а не текущая настройка: настройку могли выключить уже после нарезки.
- `<stem>.words.json` и `<stem>.srt` — субтитры по новому таймлайну (секунды), пишутся
  вместе с XML.
"""
import json
import os
from typing import Any, Callable, Sequence

from core import align
from core.applog import get_logger
from core.fileio import atomic_json_dump

log = get_logger(__name__)

SRC_WORDS_SUFFIX = ".srcwords.json"
# Жёлтое слово при пересборке ищем в пределах полсекунды от старого места (60 к/с).
YELLOW_TOL_FRAMES = 30


def _stem(xml_path: str) -> str:
    return os.path.splitext(xml_path)[0]


def src_words_path(xml_path: str) -> str:
    return _stem(xml_path) + SRC_WORDS_SUFFIX


def save_source_words(xml_path: str, words: Sequence[dict[str, Any]]) -> None:
    """Слова исходника рядом с нарезкой. Тайминги — как есть, без округления: первая
    нарезка считает субтитры по этим же числам, и пересборка должна дать тот же результат."""
    data = [{"w": str(w["w"]), "start": float(w["start"]), "end": float(w["end"])}
            for w in words]
    atomic_json_dump(src_words_path(xml_path), data, indent=1)


def load_source_words(xml_path: str) -> list[dict[str, Any]] | None:
    """Слова исходника или None: файла нет, он не читается или формат не тот."""
    p = src_words_path(xml_path)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError("не список")
        return [{"w": str(x["w"]), "start": float(x["start"]), "end": float(x["end"])}
                for x in data]
    except (OSError, ValueError, KeyError, TypeError) as ex:
        log.warning("слова исходника не прочитаны (%s): %s", p, ex)
        return None


def subs_for_keep(src_words: Sequence[dict[str, Any]] | None,
                  keep: Sequence[Sequence[float]]) -> list[dict[str, Any]] | None:
    """Слова исходника, попавшие в оставленные куски, на таймлайне (кадры xmlbuild.FPS).

    Единственная дорога от слов исходника к субтитрам XML: её зовёт и первая нарезка
    (core/gigaam_cut/pipeline.py), и каждая правка блоков (api/editor.py). Пустой вход —
    None: субтитров нет, как и до этого модуля."""
    if not src_words:
        return None
    return align.map_words(src_words, keep)


def write_sidecars(xml_path: str, sub_words: Sequence[dict[str, Any]] | None,
                   emit: Callable[..., Any] | None = None) -> None:
    """`<stem>.words.json` (секунды таймлайна) и `.srt` по субтитрам, которые уже в XML."""
    from core import xmlbuild
    side = [{"w": x["w"], "start": round(x["start"] / xmlbuild.FPS, 3),
             "end": round(x["end"] / xmlbuild.FPS, 3)} for x in sub_words or []]
    atomic_json_dump(_stem(xml_path) + ".words.json", side, indent=1)
    from core import xml2ae
    try:
        xml2ae.write_srt_for(xml_path)
    except Exception as ex:                  # srt вспомогательный: без него субтитры в XML есть
        if emit:
            emit("  srt не записан: {err}", err=str(ex))
        else:
            log.warning("srt не записан: %s", ex)


def forget_source(xml_path: str) -> None:
    """XML переписан нарезкой БЕЗ второго прохода: слова исходника прежней нарезки снимаем,
    флаг `text_subs` в сайдкаре тоже, иначе правка блоков вернула бы чужие слова."""
    from core.project_file import read_project, write_project
    try:
        os.remove(src_words_path(xml_path))
    except FileNotFoundError:
        # Слов исходника и не было — снимать нечего.
        pass
    proj_path = _stem(xml_path) + ".project.json"
    proj = read_project(proj_path)
    if proj and proj.get("text_subs"):
        proj.pop("text_subs", None)
        write_project(proj_path, proj)


def clear_sidecars(xml_path: str) -> None:
    """Снять субтитровые сайдкары, когда субтитров в XML больше нет (устаревшие)."""
    for suffix in (".words.json", ".srt"):
        p = _stem(xml_path) + suffix
        try:
            os.remove(p)
        except FileNotFoundError:
            # Файла не было — снимать нечего; это не ошибка, а нормальное состояние.
            pass


def _in(sec: float, keep: Sequence[Sequence[float]]) -> bool:
    return any(float(s) <= sec < float(e) for s, e in keep)


def _mid(w: dict[str, Any]) -> float:
    return (float(w["start"]) + float(w["end"])) / 2.0


def spans(keep: Sequence[Sequence[float]], fps: float) -> list[tuple[int, int, int]]:
    """[(начало_на_таймлайне, конец, начало_в_исходнике)] в кадрах СВОЕЙ частоты — курсором,
    как xmlbuild (куски короче кадра пропускаются, как и там)."""
    out: list[tuple[int, int, int]] = []
    tl = 0
    for s, e in keep:
        in0, out0 = round(float(s) * fps), round(float(e) * fps)
        if out0 - in0 <= 0:
            continue
        out.append((tl, tl + (out0 - in0), in0))
        tl += out0 - in0
    return out


def _src_sec(f: int, sp: Sequence[tuple[int, int, int]], fps: float, end: bool = False) -> float | None:
    """Кадр таймлайна -> секунда исходника.

    Начало слова ищем в полуинтервале [a, b), конец — в (a, b]: конец слова, совпавший с
    границей куска, должен остаться в ЭТОМ куске, а не уехать в начало следующего
    (иначе середина слова на стыке уходила бы за пределы своего куска)."""
    for a, b, in0 in sp:
        if (a < f <= b) if end else (a <= f < b):
            return (in0 + (f - a)) / fps
    return None


def transfer_xml_words(xml_words: Sequence[dict[str, Any]],
                       old_keep: Sequence[Sequence[float]],
                       new_keep: Sequence[Sequence[float]],
                       old_fps: float, new_fps: float) -> list[tuple[int, dict[str, Any], float]]:
    """Слова ТЕКУЩЕГО XML (кадры старого таймлайна) -> [(позиция_в_xml, слово, середина_в_исходнике)].

    Отбор и место — по середине слова в секундах исходника: середина в оставленном куске —
    слово остаётся. Слово на границе не режется по букве: его начало подрезается до начала
    своего куска. Перенос тот же, что у `_reproject_subs` (кадр -> секунда исходника -> кадр
    нового таймлайна), поэтому неправленые слова встают в те же кадры, что и раньше."""
    old_sp = spans(old_keep, old_fps)
    new_sp = spans(new_keep, new_fps)
    out: list[tuple[int, dict[str, Any], float]] = []
    for i, wd in enumerate(xml_words):
        f0, f1 = int(wd["start"]), int(wd["end"])
        s0 = _src_sec(f0, old_sp, old_fps)
        e0 = _src_sec(f1, old_sp, old_fps, end=True)
        if s0 is None or e0 is None:
            continue
        mid = (s0 + e0) / 2.0
        seg = None
        for a, b, in0 in new_sp:
            lo = in0 / new_fps
            hi = lo + (b - a) / new_fps
            if lo <= mid < hi:
                seg = (a, b, in0)
                break
        if seg is None:
            continue                                  # середина вырезана
        a, b, in0 = seg
        t = max(s0, in0 / new_fps)                    # не левее своего куска
        ns = a + (round(t * new_fps) - in0)
        ne = min(ns + max(1, round((f1 - f0) / old_fps * new_fps)), b)
        if ne <= ns:
            continue
        out.append((i, {"w": wd["w"], "start": ns, "end": ne}, mid))
    return out


def _restore_form(words: Sequence[tuple[int, dict[str, Any], float]],
                  src_words: Sequence[dict[str, Any]]) -> list[tuple[int, dict[str, Any], float]]:
    """Неправленое слово возвращаем в ИСХОДНОЙ записи (знаки, регистр), как у первой нарезки.

    Слово XML — это clean_sub_text исходника. Если его вид совпал с исходным словом, которое
    звучит в той же середине (допуск 0.1 с), это то же слово без правки владельца. Правленое
    слово (вид не совпал) остаётся как есть — с его текстом из XML."""
    from core import xmlbuild
    if not src_words:
        return list(words)

    def key(w: str) -> str:
        return xmlbuild.clean_sub_text(w, upper=False).lower()

    out: list[tuple[int, dict[str, Any], float]] = []
    for i, w, mid in words:
        best: tuple[float, str] | None = None
        k = key(w["w"])
        for s in src_words:
            if key(s["w"]) != k:
                continue
            d = abs(_mid(s) - mid)
            if d <= 0.1 and (best is None or d < best[0]):
                best = (d, s["w"])
        if best is not None:
            w = dict(w, w=best[1])
        out.append((i, w, mid))
    return out


def subs_after_edit(xml_words: Sequence[dict[str, Any]] | None, yellow_idx: Sequence[int],
                    old_keep: Sequence[Sequence[float]], new_keep: Sequence[Sequence[float]],
                    src_words: Sequence[dict[str, Any]], old_fps: float,
                    ) -> tuple[list[dict[str, Any]] | None, list[int]]:
    """Субтитры после правки блоков на шаге 1 (флаг text_subs). -> (слова|None, жёлтые позиции).

    Граница — по времени исходника, слово относится к куску по середине:
    (а) слово ТЕКУЩЕГО XML с серединой в оставленном куске, которое было и в старом монтаже,
        — со своими правками текста и без удалённых; переносится на новый таймлайн;
    (б) слово исходника с серединой в куске, которого в старом монтаже не было (вернувшийся
        кусок, расширенная граница), — из .srcwords.json, как у первой нарезки;
    (в) вырезанное — ничего.
    Удалённое владельцем слово внутри оставшегося куска не возвращается: оно не в (а) и не в (б)."""
    from core import xmlbuild
    tr = transfer_xml_words(xml_words or [], old_keep, new_keep, old_fps, xmlbuild.FPS)
    tr = _restore_form(tr, src_words)
    added = [w for w in src_words
             if _in(_mid(w), new_keep) and not _in(_mid(w), old_keep)]
    words: list[dict[str, Any]] = [w for _, w, _m in tr]
    if added:
        words += align.map_words(added, new_keep)
    words.sort(key=lambda x: int(x["start"]))
    if not words:
        return None, []
    ys = set(yellow_idx)
    targets = [w for i, w, _m in tr if i in ys]
    return words, carry_yellow(words, targets)


def carry_yellow(new_words: Sequence[dict[str, Any]],
                 targets: Sequence[dict[str, Any]]) -> list[int]:
    """Позиции жёлтых слов в новом списке: слово того же текста рядом по времени.

    `targets` — жёлтые слова СТАРОГО XML, уже перенесённые на новый таймлайн. Жёлтое
    живёт цветом на слове, поэтому после пересборки его надо найти заново, а не
    оставлять индекс: он указал бы на соседа.

    Сравниваем по виду, в каком слово попадает в XML (clean_sub_text): у слов исходника
    есть знаки и строчные, у XML — капс без знаков и цензура."""
    from core import xmlbuild

    def key(w: str) -> str:
        return xmlbuild.clean_sub_text(w, upper=False).lower()

    used: set[int] = set()
    out: list[int] = []
    for t in targets:
        best: tuple[int, int] | None = None
        for j, n in enumerate(new_words):
            if j in used or key(n["w"]) != key(t["w"]):
                continue
            d = abs(int(n["start"]) - int(t["start"]))
            if d <= YELLOW_TOL_FRAMES and (best is None or d < best[0]):
                best = (d, j)
        if best is not None:
            used.add(best[1])
            out.append(best[1])
    return sorted(out)
