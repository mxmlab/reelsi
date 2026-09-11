# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Атомарная запись JSON-файлов состояния (tmp + fsync + os.replace).

Прямой open(path, "w") усекает файл ДО сериализации: отбой питания или крах в
этот момент оставляет пустой/битый файл на месте живых данных (project.json,
индексы, термины, конфиги — ничего из них не пересобирается само). Один паттерн
на все места, где пишутся данные, а не пересоздаваемый кэш.
"""
import json
import os
import tempfile


def atomic_json_dump(path, obj, **kw):
    """Записать obj в path атомарно. Имя tmp уникально (mkstemp): два одновременных
    писателя в один файл не перемешают половины — кто последним сделал os.replace,
    того данные и остались, а битого файла не бывает."""
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".tmp.", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, **kw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def json_load_soft(path, default=None):
    """Прочитать JSON, битый файл — отдать default (не ронять вызывающего).

    Битый файл тут не лечится пересозданием: что с ним делать, знает только
    владелец (пересчитать, показать ошибку, удалить). Тут мы только не даём
    падению уползти в 500 без объяснения."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default
