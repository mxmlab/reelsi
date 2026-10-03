# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Список VST3 — ОТДЕЛЬНЫМ процессом (запускает core/voicefx.py).

Зачем отдельный процесс. Имена плагинов внутри файла читает `pedalboard`, а он
работает поверх JUCE: `VST3Plugin.get_plugin_names_for_file` открывает DLL
плагина. Владелец плагина (Ozone, Waves, Valhalla) поднимает в этом же процессе
свои фоновые потоки и не останавливает их никогда — DLL из процесса не
выгружается. Замер на живом сервере: `GET /api/voicefx_vst_list` отвечал 13 с,
после чего процесс висел на ~1500 % процессора, а число потоков выросло на 23 и
не падало до перезапуска сервера. Поэтому список собирается ЗДЕСЬ: этот процесс
печатает JSON и выходит вместе со всеми потоками чужих плагинов.

    python -m core.voicefx_scan --job job.json --results results.json

Задание и результаты — ФАЙЛАМИ, а не аргументами и не ответом в stdout:

* путь плагина — длинная строка с пробелами, а плагинов бывает сотня: в
  командной строке Windows это её предел;
* ЧУЖОЙ ПЛАГИН МОЖЕТ УРОНИТЬ ПРОЦЕСС (обращение к памяти — живой случай), и
  тогда stdout с ответом теряется целиком. Результаты поэтому пишутся на диск
  ПОСЛЕ КАЖДОГО плагина, атомарно (`core/fileio.atomic_json_dump`): родитель
  после падения читает файл и продолжает с того плагина, на котором упали, —
  остальные из списка не теряются.

Формат результатов:

    {"bad.vst3": {"ok": true,  "names": ["Bad"]},     # прочитан
     "ugly.vst3": {"ok": false, "names": []},         # не грузится (упал/завис)
     "hang.vst3": {"ok": null,  "names": []}}         # НАЧАЛИ и не закончили

`ok: null` пишется ПЕРЕД загрузкой плагина и заменяется ответом сразу после:
родитель по этой записи и узнаёт, на ком именно упал процесс.

Кэш имён — файл состояния (`REELSI_VST3_SCAN`, по умолчанию `vst3_scan.json` в
папке обработки голоса): ключ записи — путь + mtime + размер файла плагина, так
что новый или обновлённый плагин досканируется, а остальные не открываются вовсе.
Кэш пишет ТОЛЬКО родитель (core/voicefx.py): у него одного в руках и результаты
упавших процессов, и предыдущие записи — второму писателю здесь нечего делать.

Ход работы — строки JSON в stdout: общий протокол процессов VST описан в
core/voicefx_proc.py.
"""
from __future__ import annotations
import json
import os
import sys
from typing import Any, NoReturn, Sequence

from core import voicefx_proc
from core.app_meta import env
from core.fileio import atomic_json_dump, json_load_soft
from core.umsg import ReelsiError, umsg

# Версия формата кэша. Чужой/старый файл (версия не наша) читается как пустой:
# пересобрать список дешевле, чем разбираться, что в нём лежало.
VST3_CACHE_VERSION = 1

# Файл состояния кэша имён. В папке обработки голоса, а не в корне репозитория:
# это кэш, он пересобирается сам (и у изолированного профиля он свой).
DEFAULT_CACHE_NAME = "vst3_scan.json"

# Потолки обхода папки-бандла при сборке ключа (см. file_key): плагин — это
# единицы файлов, а глубже и шире начинаются чужие копии и примеры.
MAX_BUNDLE_DEPTH = 4
MAX_BUNDLE_DIRS = 16
MAX_BUNDLE_FILES = 64


def cache_path() -> str:
    """Путь кэша имён: `REELSI_VST3_SCAN` или файл в папке обработки голоса.

    Читается на КАЖДЫЙ вызов, а не в константу при импорте: изолированный
    профиль (`tools/webui_test.py`) ставит переменную уже после импорта, и
    привязанный к боевому файлу кэш он бы не переопределил.

    Импорт `core.voicefx` — ВНУТРИ функции: тот при импорте читает переменные
    окружения и тянет за собой профили спикеров и стили, а этот модуль нужен
    ещё и как самостоятельная точка входа (`python -m core.voicefx_scan`).
    """
    from core import voicefx
    explicit = env("VST3_SCAN")
    if explicit:
        return explicit
    return os.path.join(voicefx.VOICEFX_DIR, DEFAULT_CACHE_NAME)


def write_cache(path: str, entries: dict[str, dict[str, Any]]) -> None:
    """Записи кэша на диск атомарно (tmp + fsync + os.replace, core/fileio).

    Зовёт РОДИТЕЛЬ (core/voicefx.py), а не этот процесс: у него в руках и
    результаты упавших процессов, и прежние записи. Второй писатель (процесс
    сканирования) затирал бы то, что родитель уже свёл, — а исход упавшего
    плагина знает только родитель.
    """
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    atomic_json_dump(path, {"version": VST3_CACHE_VERSION, "entries": entries}, indent=1)


def file_key(path: str) -> str:
    """Ключ записи кэша: `realpath|mtime|size` — по СОДЕРЖИМОМУ плагина.

    Содержимое не хешируем (файл весит десятки мегабайт), но и одного `stat` по
    пути мало: на Windows `.vst3` — это ПАПКА-бандл, и её собственный mtime
    меняется не при каждом обновлении плагина (а на некоторых сборках не меняется
    вовсе: у нас в прогоне папка отдавала size=0 и один и тот же mtime при
    перезаписанном содержимом). Обновлённый плагин тогда навсегда остался бы в
    кэше «не грузится» — и не грузился бы больше никогда.

    Поэтому бандл обходим и берём самое позднее время правки и суммарный размер
    его файлов. Плагины ставят сотнями, а обход бандла — это единицы файлов;
    глубина ограничена: у гигантских сборок внутри бывают свои копии.
    """
    real = os.path.realpath(path)
    try:
        st = os.stat(real)
    except OSError:
        return real + "|0|0"          # файла нет — ключ всё равно нужен
    mtime, size = st.st_mtime, st.st_size
    if os.path.isdir(real):
        mtime, size = _dir_stamp(real, mtime, size)
    return "%s|%s|%d" % (real, repr(mtime), size)


def _dir_stamp(root: str, mtime: float, size: int) -> tuple[float, int]:
    """Время правки и суммарный размер файлов внутри папки-бандла.

    Ошибки обхода (нет прав, файл исчез между вызовами) не валят ключ: то, что
    успели прочитать, уже отличает обновлённый плагин от прежнего.
    """
    total = size
    newest = mtime
    try:
        for base, dirs, files in os.walk(root):
            dirs[:] = dirs[:MAX_BUNDLE_DIRS]
            if base.count(os.sep) - root.count(os.sep) >= MAX_BUNDLE_DEPTH:
                dirs[:] = []          # глубже не ходим: там уже не сам плагин
                continue
            for name in files[:MAX_BUNDLE_FILES]:
                try:
                    st = os.stat(os.path.join(base, name))
                except OSError:
                    continue
                total += st.st_size
                newest = max(newest, st.st_mtime)
    except OSError:
        pass  # папка исчезла между вызовами — ключ собран из того, что успели прочитать
    return newest, total


def read_cache(path: str) -> dict[str, dict[str, Any]]:
    """Записи кэша из файла: {ключ: {"ok", "names"}}. Битый/чужой файл — пусто.

    `ok` — строго bool: запись с `ok: null` (её пишет процесс перед загрузкой
    плагина) читается как «не грузится», а не как «прочитан» — иначе подвисший
    навсегда плагин сканировался бы при каждом открытии блока.
    """
    data = json_load_soft(path)
    if not isinstance(data, dict) or data.get("version") != VST3_CACHE_VERSION:
        return {}
    entries = data.get("entries")
    if not isinstance(entries, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, item in entries.items():
        if not isinstance(item, dict):
            continue
        names = item.get("names")
        out[str(key)] = {
            "ok": item.get("ok") is True,
            "names": [str(n) for n in names] if isinstance(names, list) else [],
        }
    return out


def read_results(path: str) -> dict[str, dict[str, Any]]:
    """Ответы процесса из файла результатов — БЕЗ приведения `ok` к bool.

    Отличие от `read_cache` принципиальное: здесь `ok: null` значит «этот плагин
    начали и не закончили» — по нему родитель находит виновника падения.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, item in data.items():
        if not isinstance(item, dict):
            continue
        out[str(key)] = {"ok": item.get("ok"), "names": item.get("names") or []}
    return out


def pedalboard() -> Any:
    """Ленивый импорт pedalboard: без пакета VST недоступны вовсе.

    Проверяются оба вида «пакета нет»: ImportError и `sys.modules[…] = None`
    (так отсутствие пакета подменяют тесты — `import` в этом случае тоже бросает
    ImportError, но полагаться на одну ветку нельзя: у `None` нет ни одного
    атрибута, и обращение к нему упало бы уже в чужом коде). Найденный модуль
    кладём в `found`, а не в `pb`: mypy сузил бы тип имени `pb` до модуля и
    ругался бы на `None` в ветке ImportError.
    """
    try:
        import pedalboard as pb
    except ImportError:
        # Так отсутствие пакета подменяют тесты (`sys.modules[...] = None`):
        # импорт в этом случае тоже бросает ImportError, но у `None` нет ни одного
        # атрибута, и обращение к нему упало бы уже в чужом коде.
        found = getattr(sys.modules, "pedalboard", None)
    else:
        found = pb
    if found is None:
        raise ReelsiError(umsg("vst_unavailable",
                               "Нет пакета pedalboard — VST-плагины недоступны: "
                               "pip install pedalboard"))
    return found


def plugin_names(path: str, pb: Any) -> list[str]:
    """Имена плагинов ВНУТРИ файла (пусто — прочитать не вышло).

    Оболочка (WaveShell) отдаёт несколько имён: сам файл её не грузит, грузит
    конкретное имя внутри. Чужой файл, который просто не читается (не VST3, битая
    сборка), не роняет список: причина уезжает строкой в лог родителя, а плагин
    получает запись «имён нет».
    """
    try:
        return [str(n) for n in pb.VST3Plugin.get_plugin_names_for_file(path)]
    except ReelsiError:
        raise
    except Exception as e:                    # noqa: BLE001 — чужой файл, а не наш сбой
        voicefx_proc.emit("VST3 не читается ({path}): {err}", path=path, err=e)
        return []


def read_job(path: str) -> list[dict[str, str]]:
    """Элементы задания {path, key}: битый файл — понятная ошибка.

    Задание файлом, а не аргументами: путь плагина — длинная строка с пробелами,
    а плагинов бывает сотня; в командной строке Windows это упирается в её предел.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise ReelsiError(f"Файл задания не прочитан ({path}): {e}")
    raw = data.get("paths") if isinstance(data, dict) else None
    items: list[dict[str, str]] = []
    for item in (raw if isinstance(raw, list) else []):
        if not isinstance(item, dict):
            continue
        p = item.get("path")
        key = item.get("key")
        if isinstance(p, str) and p and isinstance(key, str) and key:
            items.append({"path": p, "key": key})
    return items


def scan(paths: list[dict[str, str]], results: str) -> int:
    """Прочитать имена перечисленных плагинов; вернуть число прочитанных.

    Пакет проверяется ПЕРВЫМ делом, даже когда список пуст: родитель зовёт этот
    процесс и с пустым заданием — чтобы узнать, что pedalboard на месте (без него
    список бесполезен: грузить плагины нечем, и человеку нужна ошибка про пакет,
    а не пустой выпадающий список).

    Результаты пишутся ПОСЛЕ КАЖДОГО плагина, а перед загрузкой на его месте
    ставится `ok: null`. Плагин, уронивший процесс, не уносит с собой работу по
    остальным: родитель читает файл, помечает виновника «не грузится» и
    запускает процесс снова — с теми, кого ещё не смотрели.

    Кэш здесь не читается и не пишется: какие плагины сканировать, решил родитель
    (он же собирает записи в кэш) — второму писателю тут нечего делать.
    """
    pb = pedalboard()                          # нет пакета — понятная ошибка, не пустой список
    done = read_results(results)
    scanned = 0
    for item in paths:
        key = item["key"]
        if key in done:
            continue                           # уже смотрели: после падения процесс поднимают заново
        done[key] = {"ok": None, "names": []}  # «начали»: по этой записи родитель найдёт упавшего
        atomic_json_dump(results, done, indent=1)
        names = plugin_names(item["path"], pb)
        done[key] = {"ok": True, "names": names}
        atomic_json_dump(results, done, indent=1)
        scanned += 1
    return scanned


def _parse_args(argv: Sequence[str]) -> dict[str, str]:
    """Разобрать аргументы в словарь. Своими руками, без argparse.

    Разбор простой (три ключа), а argparse тянет за собой интерактив и тексты
    справки — ровно то, от чего избавлен api/ (tests/test_api_no_cli.py).
    """
    out = {"job": "", "results": "", "check": ""}
    i = 0
    while i < len(argv):
        key = argv[i].lstrip("-")
        if key not in out:
            raise ReelsiError(f"Неизвестный аргумент: {argv[i]}")
        if key == "check":
            out[key] = "1"                     # флаг без значения
            i += 1
            continue
        if i + 1 >= len(argv):
            raise ReelsiError(f"У аргумента {argv[i]} нет значения")
        out[key] = argv[i + 1]
        i += 2
    return out


def main(argv: Sequence[str] | None = None) -> int:
    """Точка входа процесса: прочитать имена плагинов из задания.

    `--check` — пустая работа: проверить, что пакет на месте. Родитель зовёт это,
    когда имена уже лежат в кэше, а ответ нужен сейчас: без pedalboard список
    бесполезен, и человеку нужна ошибка про пакет, а не выпадающий список,
    который молча ничего не делает.
    """
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    if args["check"]:
        pedalboard()
        return 0
    if not args["job"]:
        raise ReelsiError("Не указан файл задания (--job)")
    if not args["results"]:
        raise ReelsiError("Не указан файл результатов (--results)")
    scanned = scan(read_job(args["job"]), args["results"])
    # Ответ — строкой JSON: сколько плагинов прочитано, решает родитель (он же
    # решает, писать ли об этом в лог: при повторном открытии блока читать нечего).
    print(json.dumps({"scanned": scanned}), flush=True)
    return 0


def _fail(e: BaseException) -> NoReturn:
    """Ошибка процесса наружу: JSON-объектом с кодом, а не строкой в stderr.

    Не `core.umsg.cli_error`: тот печатает текст в stderr и теряет КОД перевода —
    родитель ждёт его из stdout (`voicefx_proc.fail`). Контракт тот же:
    `except ReelsiError` на точке входа, сообщение пользователю и код 1.
    """
    voicefx_proc.fail(e)


if __name__ == "__main__":
    voicefx_proc.no_error_windows()     # окна ошибок Windows — до чужого кода
    voicefx_proc.utf8_stdout()
    try:
        sys.exit(main())
    except ReelsiError as e:
        _fail(e)
