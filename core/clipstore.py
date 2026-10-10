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
from typing import Any

from core.applog import get_logger
from core.fileio import atomic_json_dump, json_load_soft, move_file, quarantine_unreadable
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


def move_to_trash(xml: str, paths: list[str]) -> tuple[str, list[str], list[dict[str, str]]]:
    """Переносит файлы в <папка XML>/_reelsi_trash/<YYYYmmdd-HHMMSS>_<стем>/.

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

    now = datetime.now()
    now_str = now.strftime("%Y%m%d-%H%M%S")
    now_iso = datetime.now(timezone.utc).isoformat()

    trash_root = os.path.join(xml_dir, TRASH_DIR)
    os.makedirs(trash_root, exist_ok=True)

    folder_id = f"{now_str}_{stem}"
    target_dir = os.path.join(trash_root, folder_id)
    counter = 1
    while os.path.exists(target_dir):
        counter += 1
        folder_id = f"{now_str}_{stem}_{counter}"
        target_dir = os.path.join(trash_root, folder_id)

    os.makedirs(target_dir, exist_ok=True)

    moved_paths: list[str] = []
    trash_records: list[dict[str, str]] = []
    not_moved: list[dict[str, str]] = []

    for p in paths:
        if not p or not os.path.isfile(p):
            continue
        fname = os.path.basename(p)
        dst = os.path.join(target_dir, fname)
        # Если в корзине уже есть файл с таким именем (маловероятно в новой папке, но страхуем)
        if os.path.exists(dst):
            c = 1
            root_fn, ext_fn = os.path.splitext(fname)
            while os.path.exists(dst):
                c += 1
                fname = f"{root_fn}_{c}{ext_fn}"
                dst = os.path.join(target_dir, fname)

        try:
            move_file(p, dst)
        except Exception as e:
            # Файл занят другим процессом (на Windows — плеер превью) или недоступен:
            # переносим остальные, а этот остаётся на месте и уходит в ответ причиной.
            not_moved.append({"path": p, "why": f"{type(e).__name__}: {e}"})
            continue

        moved_paths.append(p)
        trash_records.append({"from": os.path.abspath(p), "name": fname})

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
        "xml": os.path.abspath(xml),
        "deleted_at": now_iso,
        "files": trash_records,
    }
    atomic_json_dump(os.path.join(target_dir, "trash.json"), meta, indent=1)

    return (folder_id, moved_paths, not_moved)


def list_trash(dir_: str) -> list[dict[str, Any]]:
    """Список записей в корзине: [{id, stem, deleted_at, files, bytes}], новые первыми."""
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

        xml_path = str(meta.get("xml") or "")
        stem = ""
        if xml_path:
            stem, _ = os.path.splitext(os.path.basename(xml_path))
        if not stem and "_" in entry:
            stem = entry.split("_", 1)[1]

        total_bytes = 0
        for root_sub, _, fnames in os.walk(folder_path):
            for fn in fnames:
                fp = os.path.join(root_sub, fn)
                try:
                    total_bytes += os.path.getsize(fp)
                except OSError:
                    pass  # файл исчез между обходом и вопросом о размере — размер не считаем

        results.append({
            "id": entry,
            "stem": stem,
            "deleted_at": deleted_at,
            "files": len(files_rec),
            "bytes": total_bytes,
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

    restored: list[str] = []
    skipped: list[dict[str, str]] = []

    for rec in meta["files"]:
        if not isinstance(rec, dict):
            continue
        orig_from = rec.get("from")
        fname = rec.get("name")
        if not orig_from or not isinstance(fname, str) or not fname.strip():
            continue
        # Имя из метаданных — только простое имя файла. Без этой проверки запись
        # name="../../x" читала os.path.join(folder_path, fname) ВНЕ корзины, и
        # восстановление уносило наружу чужой файл.
        if fname != os.path.basename(fname):
            skipped.append({"path": str(orig_from), "why": f"недопустимое имя в корзине: {fname}"})
            continue

        src_file = os.path.join(folder_path, fname)
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
        except Exception as e:
            # не роняем восстановление остальных файлов: ошибка уходит в skipped и видна в ответе
            skipped.append({"path": orig_from, "why": f"ошибка восстановления: {e}"})

    # Если все файлы восстановлены (остался только trash.json или пусто) — удаляем папку корзины
    remaining_files = [f for f in os.listdir(folder_path) if f != "trash.json"]
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
