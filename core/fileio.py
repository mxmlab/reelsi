# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Атомарная запись JSON-, текстовых и бинарных файлов состояния (tmp + fsync + os.replace).

Прямой open(path, "w") усекает файл ДО сериализации: отбой питания или крах в
этот момент оставляет пустой/битый файл на месте живых данных (project.json,
индексы, термины, конфиги — ничего из них не пересобирается само). Один паттерн
на все места, где пишутся данные, а не пересоздаваемый кэш. То же и для XML
пользователя: усечённый XML из Премьеры не открывается вовсе.

Файл, который УЖЕ битый и вот-вот будет перезаписан, откладывается в сторону
(quarantine_unreadable): иначе первая же запись кладёт на его место одну новую
запись, и всё прежнее исчезает молча (история генераций видео, словарь терминов).

Запись идёт в ЦЕЛЬ ссылки (`os.path.realpath` до mkstemp): раньше os.replace
подменял саму ссылку обычным файлом, а цель оставалась со старыми данными — на
`styles/` и `speakers/`, подключённых junction'ом из рабочей копии сессии, это
означало «сохранил пресет, а его нигде нет». Права существующего файла при этом
переносятся на tmp: mkstemp создаёт его с 0600, и обычный файл пользователя
(0644/0664) после первой же атомарной записи становился «только для владельца».
"""
import json
import os
import stat
import tempfile
import time
from typing import IO, Any, Callable

from core.umsg import ReelsiError

# umask снимаем ОДИН раз при импорте: mkstemp всегда даёт 0600, а у нового файла
# права должны быть такие же, как у open(..., "w") — 0666 & ~umask.
_UMASK = os.umask(0)
os.umask(_UMASK)
_NEW_FILE_MODE = 0o666 & ~_UMASK


def _carry_mode(path: str, tmp: str) -> None:
    """Перенести на tmp права (и владельца на POSIX) уже существующего файла.

    Файла нет — ставим права нового файла по umask: у mkstemp-файла они 0600, и
    без этого свежесозданный конфиг не прочитал бы никто, кроме владельца."""
    try:
        st = os.stat(path)          # os.stat, а не lstat: path уже realpath
    except OSError:
        os.chmod(tmp, _NEW_FILE_MODE)
        return
    os.chmod(tmp, stat.S_IMODE(st.st_mode))
    if os.name == "posix":
        try:
            # getattr, а не прямой os.chown: в POSIX-сборках Python атрибут есть
            # всегда, а вот mypy под Windows его в типах не видит вовсе и считал бы
            # эту ветку ошибкой [attr-defined]. `# type: ignore` тут не годится —
            # на Linux он оказался бы лишним и упал бы на warn_unused_ignores.
            chown = getattr(os, "chown", None)
            if chown is not None:
                chown(tmp, st.st_uid, st.st_gid)
        except OSError:             # не владелец/нет прав — права уже перенесены
            pass  # не владелец/нет прав — права уже перенесены


def _atomic_write(path: str | os.PathLike[str], write: Callable[[IO[Any]], object],
                  mode: str = "w", encoding: str = "utf-8",
                  newline: str | None = None) -> None:
    """Одна точка записи для функций модуля: tmp рядом с целью + fsync + replace.

    Имя tmp уникально (mkstemp): два одновременных писателя в один файл не
    перемешают половины — кто последним сделал os.replace, того данные и остались.
    Целостность — всегда (старая или новая версия, не половина; битого файла не
    бывает). Долговечность переименования при отбое питания — на POSIX через fsync
    каталога, на Windows — журналом NTFS. mode="w" или "wb". Для текстового режима
    newline=None (как у open по умолчанию) — переводы строк не трогаем: вызывающий
    сам решает, нужен ли ему CRLF."""
    path = os.path.realpath(path)
    d = os.path.dirname(path) or "."
    # Цель только для чтения: прямой open(path, "w") отказал бы, а os.replace молча
    # подменил бы файл. Проверяем ДО mkstemp — tmp и дескриптор ещё не созданы.
    if os.path.exists(path) and not os.access(path, os.W_OK):
        raise PermissionError(13, "Permission denied", path)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".tmp.", dir=d)
    try:
        open_kw: dict[str, Any] = {"mode": mode}
        if "b" not in mode:
            open_kw["encoding"] = encoding
            open_kw["newline"] = newline
        with os.fdopen(fd, **open_kw) as f:
            write(f)
            f.flush()
            os.fsync(f.fileno())
        _carry_mode(path, tmp)
        os.replace(tmp, path)
    except ReelsiError: raise
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass  # временный файл уже убран — исходное исключение важнее
        raise

    if os.name == "posix":
        try:
            dfd = os.open(d, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except ReelsiError: raise
        except Exception:
            # Тип НЕ сужаем: сбой fsync каталога — не повод считать запись неудачной
            # (данные уже на диске), а прилететь тут может что угодно (см. R9, IY).
            pass  # fsync каталога не удался — данные уже записаны


def atomic_json_dump(path: str | os.PathLike[str], obj: Any, **kw: Any) -> None:
    """Записать obj в path атомарно (см. _atomic_write)."""
    _atomic_write(path, lambda f: json.dump(obj, f, ensure_ascii=False, **kw))


def atomic_text_write(path: str | os.PathLike[str], text: str, encoding: str = "utf-8",
                      newline: str | None = None) -> None:
    """Записать текст атомарно: tmp рядом + fsync + os.replace.

    Нужна там, где пишется XML/`.jsx`/SRT пользователя (core/xml2ae/highlights.py,
    сборка `.jsx`, субтитры): сбой или «Стоп» между усечением и записью оставлял
    пустой файл вместо живого. newline — как у open: профили спикеров пишутся с
    newline="\\r\\n", чтобы байты совпадали с прежней прямой записью."""
    _atomic_write(path, lambda f: f.write(text), mode="w", encoding=encoding, newline=newline)


def atomic_bytes_write(path: str | os.PathLike[str], data: bytes) -> None:
    """Записать байты атомарно: tmp рядом + fsync + os.replace.

    Нужна для бинарных файлов пользователя (DaVinci Resolve `.drp`), чтобы
    сбой питания или «Стоп» не оставляли пустой или недописанный архив."""
    _atomic_write(path, lambda f: f.write(data), mode="wb")


def json_load_soft(path: str | os.PathLike[str], default: Any = None) -> Any:
    """Прочитать JSON, битый файл — отдать default (не ронять вызывающего).

    Битый файл тут не лечится пересозданием: что с ним делать, знает только
    владелец (пересчитать, показать ошибку, удалить). Тут мы только не даём
    падению уползти в 500 без объяснения."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except ReelsiError: raise
    except Exception:
        return default


def quarantine_unreadable(path: str | os.PathLike[str],
                          valid: Callable[[Any], bool] | None = None) -> str | None:
    """Отложить в сторону файл, который не читается как JSON: `<path>.bad-<ГГГГММДД-ЧЧММСС>`.

    Зачем: читатель битого журнала отдаёт пустое значение (пустой список, пустой
    словарь), а следующая же запись кладёт по тому же пути одну новую запись — и всё,
    что в файле было, пропадает молча: так стиралась история ОПЛАЧЕННЫХ генераций
    видео и словарь терминов. Зовёт это ПИШУЩИЙ, перед записью, потому что читатель
    обязан сохранить свой контракт (пустое значение) и записывать не может.

    Битый файл не удаляется, а переименовывается рядом: что в нём было — видно руками.
    Имя занято (две поломки в одну секунду) — добавляется счётчик: чужой отложенный
    файл не затирается.

    valid(data) — проверка формата для файла, который читается как JSON, но не тем,
    чем должен быть (например, в журнале не список). Вернула False — тоже откладываем.
    -> новое имя файла; None — откладывать нечего (файла нет, он прочитался и прошёл valid).
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except Exception:
        pass  # не читается как JSON (мусор, обрыв записи, чужой формат) — откладываем
    else:
        if valid is None or valid(data):
            return None
    base = "%s.bad-%s" % (path, time.strftime("%Y%m%d-%H%M%S", time.localtime()))
    dst, n = base, 1
    while os.path.exists(dst):
        n += 1
        dst = "%s-%d" % (base, n)
    os.replace(path, dst)
    return dst
