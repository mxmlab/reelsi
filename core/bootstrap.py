# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Первый запуск: создание пользовательских файлов из примеров (*.example.json).

Создаёт только отсутствующие файлы:
- insertlib.json из insertlib.example.json
- styles/<name>.json из styles.example.json (если папка пуста или отсутствует)
- speakers/<name>.json из speakers.example.json (если папка пуста или отсутствует)

Ничего не перезаписывает и не дописывает в существующие файлы.
Личные списки (badwords.user.txt, terms.json) не трогает.
"""
import json
import os
import shutil
import sys

from core import paths
from core.app_meta import t
from core.umsg import ReelsiError, cli_error


def _example_name(example_path, default="Example"):
    """Имя пресета/профиля из JSON примера (поле name или label)."""
    try:
        with open(example_path, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            val = d.get("name") or d.get("label")
            if val and isinstance(val, str) and val.strip():
                return val.strip()
    except ReelsiError: raise
    except Exception:
        pass  # файл примера не прочитался — вернём имя по умолчанию
    return default


def _write_example(src_path, target_path, name):
    """Скопировать пример с подменой имени внутри (label/name)."""
    try:
        with open(src_path, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            if "label" in d:
                d["label"] = name
            elif "name" in d:
                d["name"] = name
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
            f.write("\n")
    except ReelsiError: raise
    except Exception:
        shutil.copy2(src_path, target_path)


def ensure_user_files():
    """Создать недостающие пользовательские файлы из *.example.json.

    Возвращает список строк сообщений для лога.
    """
    from core import speakers
    from core import styles

    created = []

    # 1. База вставок: боевой файл установки
    insertlib_target = paths.root("insertlib.json")
    insertlib_src = os.path.join(paths.EXAMPLES, "insertlib.example.json")
    if not os.path.exists(insertlib_target) and os.path.isfile(insertlib_src):
        shutil.copy2(insertlib_src, insertlib_target)
        created.append(t("заведён insertlib.json из примера — правь в интерфейсе"))

    # 2. Стили: если папки нет или в ней нет пользовательских json-файлов
    style_dir = styles.STYLE_DIR
    style_src = os.path.join(paths.EXAMPLES, "styles.example.json")
    has_styles = os.path.isdir(style_dir) and any(
        f.lower().endswith(".json") and not f.startswith(".") and not f.startswith("_")
        for f in os.listdir(style_dir)
    )
    if not has_styles and os.path.isfile(style_src):
        os.makedirs(style_dir, exist_ok=True)
        raw_name = _example_name(style_src, default="Мой стиль")
        name = t(raw_name)
        target = os.path.join(style_dir, f"{name}.json")
        if not os.path.exists(target):
            _write_example(style_src, target, name)
            created.append(t("заведён styles/{name}.json из примера — правь в интерфейсе", name=name))

    # 3. Спикеры: если папки нет или в ней нет пользовательских json-файлов
    speaker_dir = speakers.SPEAKER_DIR
    speaker_src = os.path.join(paths.EXAMPLES, "speakers.example.json")
    has_speakers = os.path.isdir(speaker_dir) and any(
        f.lower().endswith(".json") and not f.startswith(".")
        for f in os.listdir(speaker_dir)
    )
    if not has_speakers and os.path.isfile(speaker_src):
        os.makedirs(speaker_dir, exist_ok=True)
        raw_name = _example_name(speaker_src, default="Пример профиля")
        name = t(raw_name)
        target = os.path.join(speaker_dir, f"{name}.json")
        if not os.path.exists(target):
            _write_example(speaker_src, target, name)
            created.append(t("заведён speakers/{name}.json из примера — правь в интерфейсе", name=name))

    return created


if __name__ == "__main__":
    try:
        for _s in (sys.stdout, sys.stderr):
            try:
                _s.reconfigure(encoding="utf-8", errors="replace")
            except ReelsiError: raise
            except Exception:
                pass  # поток без reconfigure (перенаправлен) — служебная печать не критична
        for msg in ensure_user_files():
            print(f"  {msg}")
    except ReelsiError as e:
        cli_error(e)
