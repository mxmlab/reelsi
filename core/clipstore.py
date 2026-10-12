# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Хранение снимков клипов рядом с XML и корзина для удалений.

Инвариант: всё, что пользователь сделал с клипом, лежит на диске рядом с его
XML в `<стем>.clip.json`. Браузер — только витрина.
Корзина `_reelsi_trash/` сохраняет удалённые нарезки с возможностью возврата.

Модуль чистый: без flask и GPU, полностью аннотирован.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
import shutil
from typing import Any

from core.applog import get_logger
from core.fileio import (atomic_json_dump, json_load_soft, move_file, quarantine_unreadable,
                         unique_path)
from core.umsg import ReelsiError, umsg

log = get_logger(__name__)
TRASH_DIR = "_reelsi_trash"


def clip_path(xml: str) -> str:
    """Путь к сайдкару снимка состояния клипа `<стем>.clip.json` рядом с XML."""
    stem, _ = os.path.splitext(xml)
    return stem + ".clip.json"


def save_clips(state: dict[str, Any]) -> int:
    """Для каждого клипа в state["CLIPS"], у которого файл xml существует:
    пишет {"v": 1, "saved_at": <ISO>, "clip": <объект клипа как есть>}
    через atomic_json_dump, только если объект clip изменился против файла на диске.
    Возвращает число записанных снимков.
    """
    if not isinstance(state, dict):
        return 0
    clips = state.get("CLIPS")
    if not isinstance(clips, list):
        return 0

    saved_count = 0
    for clip in clips:
        if not isinstance(clip, dict):
            continue
        xml = clip.get("xml")
        if not isinstance(xml, str) or not xml.strip():
            continue
        xml = xml.strip().strip('"')
        if not os.path.isfile(xml):
            continue

        cpath = clip_path(xml)
        existing = json_load_soft(cpath)
        if isinstance(existing, dict) and existing.get("clip") == clip:
            continue

        now_iso = datetime.now(timezone.utc).isoformat()
        payload = {
            "v": 1,
            "saved_at": now_iso,
            "clip": clip,
        }
        # Битый снимок (json_load_soft выше отдал None) откладываем, а не затираем: это
        # единственная копия клипа, из которой восстанавливаются вставки и спикер.
        bad = quarantine_unreadable(cpath, valid=lambda d: isinstance(d, dict))
        if bad:
            log.warning("снимок клипа не прочитан — отложен в %s", bad)
        atomic_json_dump(cpath, payload, indent=1)
        saved_count += 1

    return saved_count


def load_clip(xml: str) -> dict[str, Any] | None:
    """Объект clip из снимка или None (битый/нет файла -> None)."""
    if not xml or not isinstance(xml, str):
        return None
    cpath = clip_path(xml.strip().strip('"'))
    if not os.path.isfile(cpath):
        return None
    data = json_load_soft(cpath)
    if isinstance(data, dict) and isinstance(data.get("clip"), dict):
        return data["clip"]
    return None


def _check_trash_id(trash_id: str) -> str:
    """Проверяет trash_id: только имя подпапки без '..'/разделителей."""
    tid = str(trash_id).strip()
    if not tid or ".." in tid or "/" in tid or "\\" in tid:
        raise ReelsiError(umsg("bad_trash_id", f"Некорректный идентификатор корзины: {trash_id}", trash_id=trash_id))
    return tid


def _rm_empty(folder: str) -> None:
    """Убрать папку, если она пуста; непустую (чужие записи, файлы) — оставить как есть."""
    try:
        os.rmdir(folder)
    except OSError:
        pass  # непуста или уже убрана — трогать не надо


def _drop_empty_trash(entry_dir: str) -> None:
    """После возврата или очистки: пустую папку записи в `_reelsi_trash` и сам `_reelsi_trash` над ней."""
    _rm_empty(entry_dir)
    parent = os.path.dirname(os.path.normpath(entry_dir))
    if os.path.basename(parent) == TRASH_DIR:
        _rm_empty(parent)


def cleanup_failed_entry(entry_dir: str) -> None:
    """Запись корзины не создалась (перенос упал): её папку и пустую `_reelsi_trash` над ней убрать.
    Непустую `_reelsi_trash` (чужие записи) не трогаем."""
    shutil.rmtree(entry_dir, ignore_errors=True)
    _drop_empty_trash(entry_dir)


def new_trash_entry(root: str, stem: str) -> tuple[str, str]:
    """Id и путь новой записи корзины папки `root`: `<ГГГГммдд-ЧЧММСС>_<стем>`, занятое имя —
    `_2`, `_3`… Папки не создаёт: это делает вызывающий (клип и подкаст создают по-разному).
    Клипы и подкасты пишут один и тот же id — по нему возврат находит исходники в папках камер."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = unique_path(os.path.join(root, TRASH_DIR), f"{stamp}_{stem}", is_dir=True)
    return os.path.basename(path), path


def trash_move_in(src: str, entry_id: str) -> tuple[str, str]:
    """Внешний исходник уходит в `<его папка>/_reelsi_trash/<entry_id>/` под свободным именем.

    Возвращает (путь в корзине, абсолютный путь туда — его пишут в trash.json как stored).
    Сбой переноса — OSError наружу, но пустые папки, созданные ради этой попытки
    (`_reelsi_trash/<entry_id>` и сама `_reelsi_trash`), убираются: у исходника ничего не остаётся.
    """
    src_abs = os.path.abspath(src)
    src_trash = os.path.join(os.path.dirname(src_abs), TRASH_DIR, entry_id)
    try:
        os.makedirs(src_trash, exist_ok=True)
        dst = unique_path(src_trash, os.path.basename(src_abs))
        move_file(src_abs, dst)
    except OSError:
        _rm_empty(src_trash)
        _rm_empty(os.path.dirname(src_trash))
        raise
    return dst, os.path.abspath(dst)


def write_trash_meta(entry: str, meta: dict[str, Any]) -> None:
    """trash.json в папке записи корзины. Формат общий для клипов и подкастов (поле kind)."""
    atomic_json_dump(os.path.join(entry, "trash.json"), meta, indent=1)


def _in_trash(path: str) -> bool:
    """Лежит ли путь внутри папки, которая называется `_reelsi_trash` (по realpath, любой уровень).

    Нужна там, где путь из trash.json: запись `stored` вне корзины — подделка или ошибка, и
    такой файл ни возвращать, ни стирать нельзя.
    """
    cur = os.path.dirname(os.path.realpath(path))
    while True:
        if os.path.basename(cur).lower() == TRASH_DIR.lower():
            return True
        parent = os.path.dirname(cur)
        if parent == cur:
            return False
        cur = parent


def _is_reparse(path: str) -> bool:
    """Симлинк или junction Windows (точка повторной разборки). Python 3.10 при обходе и
    shutil.rmtree идёт по junction ВНУТРЬ — а junction ведёт, например, на личные данные.
    Такие места в корзине не обходим и не стираем."""
    try:
        st = os.lstat(path)
    except OSError:
        return True  # не прочитали атрибуты — считаем небезопасным
    attrs = getattr(st, "st_file_attributes", 0)
    return os.path.islink(path) or bool(attrs & 0x400)  # 0x400 = FILE_ATTRIBUTE_REPARSE_POINT


def _tree_bytes(root: str) -> tuple[int, bool]:
    """(суммарный размер файлов под root, есть ли внутри ссылка/junction). Внутрь ссылок не заходим."""
    total = 0
    linked = False
    stack = [root]
    while stack:
        cur = stack.pop()
        try:
            entries = list(os.scandir(cur))
        except OSError:
            continue  # папку не прочли — в объём не попадёт
        for e in entries:
            if _is_reparse(e.path):
                linked = True
                continue
            if e.is_dir(follow_symlinks=False):
                stack.append(e.path)
            elif e.is_file(follow_symlinks=False):
                try:
                    total += e.stat(follow_symlinks=False).st_size
                except OSError:
                    pass  # файл исчез между обходом и замером — в объём не попадёт
    return total, linked


def _stored_recs(meta: dict[str, Any]) -> list[dict[str, Any]]:
    """Записи trash.json, у которых файл лежит ВНЕ папки записи (поле stored — исходники камер и подкастов)."""
    recs = meta.get("files")
    if not isinstance(recs, list):
        return []
    return [r for r in recs if isinstance(r, dict) and isinstance(r.get("stored"), str) and r["stored"]]


def move_to_trash(xml: str, paths: list[str], sources: list[str] | None = None,
                  ) -> tuple[str, list[str], list[dict[str, str]]]:
    """Переносит файлы в <папка XML>/_reelsi_trash/<YYYYmmdd-HHMMSS>_<стем>/.

    sources — исходники камер клипа (решение владельца: удалил — всё в корзину). Каждый уходит
    в <папка исходника>/_reelsi_trash/<та же id записи>/<имя> на том же диске (переименование,
    а между дисками — копия через fileio.move_file). В trash.json такой файл пишется с полем
    stored; занятый или недоступный — в третьем элементе с причиной, остальное переносится.
    Общие с другими клипами исходники сюда не передают: отбор делает роут, здесь только перенос.

    Перенос ПОФАЙЛОВЫЙ: занятый файл (на Windows `.voice.wav` держит плеер превью)
    не роняет всю уборку — он остаётся на месте и возвращается в третьем элементе
    с причиной. Общий try вокруг цикла как раз и был дефектом: часть файлов уже в
    корзине, а trash.json не записан — запись не видна в корзине и не возвращается.
    Сам перенос — через core.fileio.move_file (os.replace, между дисками — копия).

    Пишет туда trash.json {"xml": <путь>, "deleted_at": ISO, "files": [{"from": исходный путь, "name": имя}]}
    по ФАКТИЧЕСКИ перенесённым файлам. Не перенесено ничего — папка корзины убирается.
    Возвращает (id папки, перенесённые пути, [{"path", "why"}]).
    """
    if not xml:
        raise ReelsiError(umsg("bad_xml_path", "Не указан XML", path=xml))
    xml_dir = os.path.dirname(os.path.abspath(xml))
    xml_name = os.path.basename(xml)
    stem, _ = os.path.splitext(xml_name)
    if not stem:
        stem = "clip"

    now_iso = datetime.now(timezone.utc).isoformat()

    trash_root = os.path.join(xml_dir, TRASH_DIR)
    os.makedirs(trash_root, exist_ok=True)

    folder_id, target_dir = new_trash_entry(xml_dir, stem)
    os.makedirs(target_dir, exist_ok=True)

    moved_paths: list[str] = []
    trash_records: list[dict[str, str]] = []
    not_moved: list[dict[str, str]] = []

    for p in paths:
        if not p or not os.path.isfile(p):
            continue
        # Если в корзине уже есть файл с таким именем (маловероятно в новой папке, но страхуем)
        dst = unique_path(target_dir, os.path.basename(p))
        fname = os.path.basename(dst)

        try:
            move_file(p, dst)
        except Exception as e:
            # Файл занят другим процессом (на Windows — плеер превью) или недоступен:
            # переносим остальные, а этот остаётся на месте и уходит в ответ причиной.
            not_moved.append({"path": p, "why": f"{type(e).__name__}: {e}"})
            continue

        moved_paths.append(p)
        trash_records.append({"from": os.path.abspath(p), "name": fname})

    # Исходники камер — рядом с самим исходником, под той же id записи: так возврат и
    # очистка находят их по одной записи trash.json. Занятый файл остаётся на месте.
    for src in sources or []:
        if not src or not os.path.isfile(src):
            continue
        src_abs = os.path.abspath(src)
        try:
            dst, stored = trash_move_in(src_abs, folder_id)
        except OSError as e:
            not_moved.append({"path": src, "why": f"{type(e).__name__}: {e}"})
            continue
        moved_paths.append(src)
        trash_records.append({"from": src_abs, "name": os.path.basename(dst), "stored": stored})

    # Ничего не перенесли — пустая папка корзины не нужна: без неё ни записи, ни мусора
    if not moved_paths:
        try:
            os.rmdir(target_dir)
        except OSError:
            pass  # папка уже убрана или непуста — оставляем как есть
        try:
            os.rmdir(trash_root)  # сама _reelsi_trash пустая — убираем и её
        except OSError:
            pass  # в корзине есть другие записи — не трогаем
        return ("", [], not_moved)

    meta = {
        "kind": "clip",   # корзина общая с подкастами: по полю видно, что это за запись
        "xml": os.path.abspath(xml),
        "deleted_at": now_iso,
        "files": trash_records,
    }
    write_trash_meta(target_dir, meta)

    return (folder_id, moved_paths, not_moved)


def list_trash(dir_: str) -> list[dict[str, Any]]:
    """Записи корзины одной папки (`<dir_>/_reelsi_trash/<запись>/trash.json`), новые первыми.

    Одна корзина на клипы и подкасты: поле `kind`. Записи без trash.json не показываются.
    `root` — папка, где лежит `_reelsi_trash`: по ней же возвращается запись.
    """
    if not dir_ or not os.path.isdir(dir_):
        return []
    trash_root = os.path.join(dir_, TRASH_DIR)
    if not os.path.isdir(trash_root):
        return []

    try:
        entries = os.listdir(trash_root)
    except OSError:
        return []

    results: list[dict[str, Any]] = []
    for entry in entries:
        folder_path = os.path.join(trash_root, entry)
        if not os.path.isdir(folder_path):
            continue

        meta_path = os.path.join(folder_path, "trash.json")
        meta = json_load_soft(meta_path)
        if not isinstance(meta, dict):
            continue

        deleted_at = str(meta.get("deleted_at") or "")
        files_rec = meta.get("files")
        if not isinstance(files_rec, list):
            files_rec = []

        # Старые записи клипов поля kind не имеют — это клипы
        kind = "podcast" if meta.get("kind") == "podcast" else "clip"
        xml_path = str(meta.get("xml") or "")
        stem = ""
        if xml_path:
            stem, _ = os.path.splitext(os.path.basename(xml_path))
        if not stem and "_" in entry:
            stem = entry.split("_", 1)[1]
        title = str(meta.get("title") or "") if kind == "podcast" else stem

        # Размер: вся папка записи (внутрь ссылок не заходим) + исходники по stored
        total_bytes = _tree_bytes(folder_path)[0]
        stored = _stored_recs(meta)
        for rec in stored:
            if _in_trash(rec["stored"]) and os.path.isfile(rec["stored"]):
                try:
                    total_bytes += os.path.getsize(rec["stored"])
                except OSError:
                    pass  # файл исчез между проверкой и замером — в объём не попадёт

        results.append({
            "id": entry,
            "kind": kind,
            "stem": stem if kind == "clip" else "",
            "title": title,
            "deleted_at": deleted_at,
            # у подкаста сама папка тоже файл записи; исходники по stored — отдельно
            "files": len(files_rec) + (1 if kind == "podcast" else 0),
            "bytes": total_bytes,
            "sources": len(stored),
            "root": dir_,
        })

    # Новые первыми (по deleted_at, если совпадает/пусто — по id)
    results.sort(key=lambda r: (r["deleted_at"], r["id"]), reverse=True)
    return results


def restore_trash(dir_: str, id_: str) -> dict[str, Any]:
    """Возвращает файлы на исходные места.

    Если на месте уже есть файл — НЕ перезаписывать, вернуть его в skipped с причиной.
    Имя файла из trash.json — только простое имя (name == basename(name), не пусто):
    запись вида "../../x" уводила чтение и запись за пределы папки корзины.
    Пустую папку корзины удалить.
    id_ проверять: только имя подпапки без '..'/разделителей.
    """
    if not dir_ or not os.path.isdir(dir_):
        raise ReelsiError(umsg("no_folder", f"Нет папки: {dir_}", path=dir_))
    tid = _check_trash_id(id_)

    trash_root = os.path.join(dir_, TRASH_DIR)
    folder_path = os.path.join(trash_root, tid)
    if not os.path.isdir(folder_path):
        raise ReelsiError(umsg("trash_not_found", f"Запись корзины не найдена: {tid}", id=tid))

    meta_path = os.path.join(folder_path, "trash.json")
    meta = json_load_soft(meta_path)
    if not isinstance(meta, dict) or not isinstance(meta.get("files"), list):
        raise ReelsiError(umsg("bad_trash_meta", f"Повреждены метаданные корзины в {tid}", id=tid))

    kind = str(meta.get("kind") or "clip")
    restored: list[str] = []
    skipped: list[dict[str, str]] = []

    if kind == "podcast":
        # Подкаст — сама папка: возвращаем её на старое место целиком. Место занято — отказ
        # ДО любого переноса: ни папку, ни её исходники не двигаем.
        orig_dir = str(meta.get("from") or "")
        dir_name = os.path.basename(os.path.normpath(orig_dir)) if orig_dir else ""
        src_dir = os.path.join(folder_path, dir_name) if dir_name else ""
        if not dir_name or not os.path.isdir(src_dir):
            raise ReelsiError(umsg("bad_trash_meta", f"Повреждены метаданные корзины в {tid}", id=tid))
        if os.path.lexists(orig_dir):
            raise ReelsiError(umsg("trash_restore_failed",
                                   f"Подкаст не возвращён: место занято ({orig_dir})", err=orig_dir))
        try:
            os.makedirs(os.path.dirname(os.path.abspath(orig_dir)), exist_ok=True)
            os.rename(src_dir, orig_dir)  # та же папка подкастов — переименование
        except OSError as e:
            raise ReelsiError(umsg("trash_restore_failed",
                                   f"Подкаст не возвращён: {e}", err=str(e))) from e
        restored.append(orig_dir)

    for rec in meta["files"]:
        if not isinstance(rec, dict):
            continue
        orig_from = rec.get("from")
        fname = rec.get("name")
        if not orig_from:
            skipped.append({"path": "", "why": "в записи корзины нет исходного пути"})
            continue
        if not isinstance(fname, str) or not fname.strip():
            continue
        # Имя из метаданных — только простое имя файла. Без этой проверки запись
        # name="../../x" читала os.path.join(folder_path, fname) ВНЕ корзины, и
        # восстановление уносило наружу чужой файл.
        if fname != os.path.basename(fname):
            skipped.append({"path": str(orig_from), "why": f"недопустимое имя в корзине: {fname}"})
            continue

        # stored — файл, который уехал к своему исходнику (камера, внешний файл подкаста):
        # лежит вне папки записи, и брать его надо оттуда. Вне _reelsi_trash — не трогаем.
        # Обычный файл записи stored не имеет (ключа нет) — берётся из папки записи. Ключ есть,
        # а путь пустой или не строка (null, число) — запись испорчена, пропускаем с причиной.
        stored = rec.get("stored")
        if stored is not None and not (isinstance(stored, str) and stored):
            skipped.append({"path": str(orig_from), "why": "в записи корзины путь к файлу испорчен"})
            continue
        if isinstance(stored, str):
            if not _in_trash(stored):
                skipped.append({"path": str(orig_from), "why": "путь в корзине вне папки _reelsi_trash — не трогаю"})
                continue
            src_file = os.path.abspath(stored)
            is_stored = True
        else:
            src_file = os.path.join(folder_path, fname)
            is_stored = False
        if not os.path.isfile(src_file):
            skipped.append({"path": orig_from, "why": "файл отсутствует в корзине"})
            continue

        if os.path.exists(orig_from):
            skipped.append({"path": orig_from, "why": "файл уже существует на исходном месте"})
            continue

        dst_dir = os.path.dirname(os.path.abspath(orig_from))
        if dst_dir:
            os.makedirs(dst_dir, exist_ok=True)

        try:
            move_file(src_file, orig_from)
            restored.append(orig_from)
            if is_stored:
                # папка `_reelsi_trash/<id>/` у исходника опустела — убираем её и `_reelsi_trash`
                _drop_empty_trash(os.path.dirname(src_file))
        except Exception as e:
            # не роняем восстановление остальных файлов: ошибка уходит в skipped и видна в ответе
            skipped.append({"path": orig_from, "why": f"ошибка восстановления: {e}"})

    # Запись уходит, только когда от неё ничего не осталось: ни файла в папке, ни исходника по
    # stored (его могли не вернуть — место занято, путь подозрительный). Иначе файл лишился бы записи.
    remaining_files = [f for f in os.listdir(folder_path) if f != "trash.json"]
    remaining_files += [str(r["stored"]) for r in _stored_recs(meta) if os.path.lexists(str(r["stored"]))]
    if not remaining_files:
        try:
            if os.path.isfile(meta_path):
                os.remove(meta_path)
            os.rmdir(folder_path)
        except OSError:
            pass  # папка не убралась (чужие файлы, права) — оставляем как есть
        # Если вся папка корзины опустела, можно убрать и _reelsi_trash
        try:
            os.rmdir(trash_root)
        except OSError:
            pass  # в корзине остались другие записи — не трогаем

    return {
        "ok": True,
        "restored": restored,
        "skipped": skipped,
    }


def list_trash_many(roots: list[str]) -> list[dict[str, Any]]:
    """Записи нескольких корзин (клипы папки и подкасты папки подкастов), новые первыми.

    Одна и та же папка, указанная дважды, даёт свои записи один раз.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in roots:
        key = os.path.normcase(os.path.abspath(root)) if root else ""
        if not key or key in seen:
            continue
        seen.add(key)
        out.extend(list_trash(root))
    out.sort(key=lambda r: (r["deleted_at"], r["id"]), reverse=True)
    return out


def empty_trash(roots: list[str], dry: bool = True) -> dict[str, Any]:
    """Очистка корзины: насовсем стирает ВСЕ записи корзин `roots` (с trash.json) вместе с файлами.

    dry → {entries, bytes} без удаления. Настоящий прогон → {removed, bytes, failed}.

    Защита: файл из записи (поле stored) стирается, только если он лежит внутри папки
    `_reelsi_trash` (по realpath). Иначе подделанный trash.json стёр бы чужой файл: такой
    путь — в failed, и вся запись с ним остаётся нетронутой. Запись со ссылкой или junction
    внутри тоже не трогаем (rmtree на junction ушёл бы в чужую папку).
    """
    entries: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        trash_root = os.path.join(root, TRASH_DIR)
        if not os.path.isdir(trash_root):
            continue
        try:
            names = sorted(os.listdir(trash_root))
        except OSError:
            continue  # корзину не прочли — записей нет
        for name in names:
            folder = os.path.join(trash_root, name)
            if not os.path.isdir(folder):
                continue
            meta = json_load_soft(os.path.join(folder, "trash.json"))
            if not isinstance(meta, dict):
                continue
            key = os.path.normcase(os.path.realpath(folder))
            if key in seen:
                continue
            seen.add(key)
            entries.append((folder, meta))

    if dry:
        total = 0
        for folder, meta in entries:
            total += _tree_bytes(folder)[0]
            for rec in _stored_recs(meta):
                if _in_trash(rec["stored"]) and os.path.isfile(rec["stored"]):
                    try:
                        total += os.path.getsize(rec["stored"])
                    except OSError:
                        pass  # файл исчез между обходом и замером — в объём не попадёт
        return {"ok": True, "entries": len(entries), "bytes": total}

    removed = 0
    freed = 0
    failed: list[dict[str, str]] = []
    for folder, meta in entries:
        bad = False
        if not _in_trash(folder):
            failed.append({"path": folder, "why": "запись вне папки _reelsi_trash — не трогаю"})
            continue
        stored = _stored_recs(meta)
        for rec in stored:
            if not _in_trash(rec["stored"]):
                failed.append({"path": rec["stored"],
                               "why": "путь вне папки _reelsi_trash — файл не тронут"})
                bad = True
        size, linked = _tree_bytes(folder)
        if linked:
            failed.append({"path": folder, "why": "внутри записи ссылка или junction — не стираю"})
            bad = True
        if bad:
            continue  # запись с подозрительным путём целиком остаётся в корзине
        try:
            for rec in stored:
                st = os.path.abspath(rec["stored"])
                if os.path.isfile(st):
                    freed += os.path.getsize(st)
                    os.remove(st)
                    _drop_empty_trash(os.path.dirname(st))
            shutil.rmtree(folder)
        except OSError as e:
            failed.append({"path": folder, "why": f"{type(e).__name__}: {e}"})
            continue
        freed += size
        removed += 1

    for root in roots:
        if root:
            _rm_empty(os.path.join(root, TRASH_DIR))
    return {"ok": True, "removed": removed, "bytes": freed, "failed": failed}
