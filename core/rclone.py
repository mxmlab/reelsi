# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""rclone: конфиг, разбор ссылок гугл-диска, сборка команды и разбор её вывода.

ПОЧЕМУ модуль есть (инверсия слоёв). Всё это жило в `api/gdrive.py` —
модуле HTTP-слоя, хотя состояния тут нет вовсе: чистые функции от строки к строке.
Теперь они в ядре, а в `api/gdrive.py` остались джоб (GDJOB), процесс rclone,
его сторож простоя и роуты.

Своя качалка на requests тут невозможна: гугл на файлах крупнее ~100 МБ отдаёт
страницу-предупреждение о проверке на вирусы вместо файла, и наивная загрузка
молча сохранит HTML вместо видео. rclone это умеет, не тянет заново уже
скачанное и докачивает после обрыва.

Авторизацию настраивает ПОЛЬЗОВАТЕЛЬ (rclone config открывает браузер и входит в
аккаунт сам): этот модуль только читает его конфиг и собирает уже настроенный
rclone. Секреты живут в конфиге rclone, в наши файлы ничего не пишется.
"""
import os
import re
from typing import Any

# Путь к конфигу rclone. По умолчанию — стандартное место rclone на платформе;
# REELSI_RCLONE_CONF (старое имя AUTOCUT_RCLONE_CONF) переопределяет — например,
# для изолированного тестового профиля на 5098.
CONF_ENV = "REELSI_RCLONE_CONF"
REMOTE_ENV = "REELSI_RCLONE_REMOTE"


def rclone_conf() -> str:
    p = os.environ.get(CONF_ENV) or os.environ.get("AUTOCUT_RCLONE_CONF")
    if p:
        return p
    if os.name == "nt":
        return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                            "rclone", "rclone.conf")
    return os.path.expanduser("~/.config/rclone/rclone.conf")


# Ссылки гугл-диска бывают трёх видов: /file/d/<id>, /open?id=<id> и
# /drive/folders/<id> (со старыми ссылками-шерингом ещё и ?resourcekey=…).
_FOLDER_RE = re.compile(r"drive\.google\.com/drive/(?:u/\d+/)?folders/([A-Za-z0-9_-]{8,})")
_FILE_RE = re.compile(r"drive\.google\.com/(?:file/d/|(?:open|uc)\?(?:[^&#]*&)*id=)([A-Za-z0-9_-]{8,})")
_RESKEY_RE = re.compile(r"resourcekey=([A-Za-z0-9_-]+)")


def parse_gdrive_link(url: str) -> dict[str, Any] | None:
    """Ссылка гугл-диска → {kind:'file'|'folder', id, resource_key} или None.

    Не похоже на ссылку гугл-диска — вернуть None и дать внятную ошибку: молча
    тащить непонятное через rclone нельзя, там путь из ссылки уходит в аргументы
    процесса.
    """
    if not url:
        return None
    for kind, pat in (("folder", _FOLDER_RE), ("file", _FILE_RE)):
        m = pat.search(url)
        if m:
            rk = _RESKEY_RE.search(url)
            return {"kind": kind, "id": m.group(1),
                    "resource_key": rk.group(1) if rk else None}
    return None


def rclone_remotes(conf: str | None = None) -> list[str]:
    """Имена drive-remote из конфига rclone. Конфиг — простой INI: секции [имя]
    с ключами; нашим ремоутом секция становится, если в ней `type = drive`."""
    conf = conf or rclone_conf()
    if not os.path.isfile(conf):
        return []
    out: list[str] = []
    cur: str | None = None
    is_drive = False
    with open(conf, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            m = re.match(r"\[(.+)\]$", line)
            if m:
                if cur and is_drive:
                    out.append(cur)
                cur, is_drive = m.group(1), False
            elif cur and re.match(r"type\s*=\s*drive$", line, re.I):
                is_drive = True
    if cur and is_drive:
        out.append(cur)
    return out


def rclone_remote(conf: str | None = None) -> str:
    """Имя drive-remote для скачивания. Явное (REELSI_RCLONE_REMOTE) приоритетнее,
    иначе — единственный drive-remote в конфиге. Ноль или несколько — внятная
    ошибка с тем, что делать."""
    name = os.environ.get(REMOTE_ENV) or os.environ.get("AUTOCUT_RCLONE_REMOTE")
    if name:
        return name
    remotes = rclone_remotes(conf)
    if len(remotes) == 1:
        return remotes[0]
    if not remotes:
        raise RuntimeError("rclone не настроен на гугл-диск: в конфиге нет "
                           "remote с type=drive. Запусти rclone config и добавь")
    raise RuntimeError(f"в rclone-конфиге несколько гугл-дисков "
                       f"({', '.join(remotes)}) — задай имя через {REMOTE_ENV}")


def rclone_cmd(remote: str, url: str, dest: str) -> list[str]:
    """Аргументы rclone для ссылки. Файл — backend copyid (документированный
    доступ по ID), папка — copy с --drive-root-folder-id (то же по-другому:
    папка становится корнем remote). Список, без shell: путь из ссылки не может
    стать аргументом оболочки."""
    spec = parse_gdrive_link(url)
    if not spec:
        raise ValueError("не ссылка гугл-диска: " + str(url)[:80])
    dest = str(dest)
    # Путь назначения приходит из запроса, а значение, начинающееся с «-», rclone
    # разберёт как ОПЦИЮ: `--config=<чужой конфиг>` увёл бы скачивание на чужие
    # токены, `--dry-run` сделал бы вид, что скачали. Отказываем до всякого rclone.
    if dest.startswith("-"):
        raise ValueError("путь назначения не может начинаться с «-»: " + dest[:80])
    # -v обязателен. rclone логирует статистику на уровне INFO (--stats-log-level,
    # по умолчанию INFO), а порог вывода по умолчанию — NOTICE, то есть с одним
    # --stats в лог не попадёт НИ ОДНОЙ строки прогресса и скачивание гигабайтов
    # выглядит как зависшее. С -v строки идут, заодно видно, что именно скачалось.
    # --stats 2s, а не 5s: статистика — единственный источник живого статуса на
    # странице, и раз в 5 секунд он выглядит подвисающим. Лог от этого не пухнет:
    # прогресс уходит в поля статуса, а в лог дублируется раз в 10%.
    args = ["rclone", "-v", "--config", rclone_conf(), "--stats", "2s"]
    # `--` перед первым позиционным аргументом: после него для
    # rclone всё — значения, а не опции. Без него id из ссылки (маска допускает
    # ведущий дефис) и путь назначения управляли бы разбором аргументов.
    # У file-ветки первый позиционный — подкоманда `copyid`; у folder `--` идёт
    # после опций, иначе --drive-root-folder-id сам стал бы позиционным и папка
    # скачалась бы в корень remote, а не по id.
    if spec["kind"] == "file":
        d = dest.replace("\\", "/").rstrip("/") + "/"
        args += ["backend", "--", "copyid", remote + ":", spec["id"], d]
    else:
        args += ["copy", "--drive-root-folder-id", spec["id"]]
        if spec["resource_key"]:
            args += ["--drive-resource-key", spec["resource_key"]]
        args += ["--", remote + ":", dest]
    return args


_STATS = re.compile(r"Transferred:\s*(\d+(?:\.\d+)?\s?[A-Za-z]+)\s*/\s*(\d+(?:\.\d+)?\s?[A-Za-z]+),\s*(\d+)%")


def progress_line(line: str) -> str | None:
    """Строка rclone → прогресс для лога или None, если это не прогресс.
    Вынесено отдельно, чтобы тесты стерегли разбор без живого rclone."""
    m = _STATS.search(line)
    return f"скачивание: {m.group(1)} из {m.group(2)} ({m.group(3)}%)" if m else None


# Остальные строки блока статистики. Блок печатается КАЖДЫЕ --stats секунд целиком
# (счётчик файлов, проверки, время, список текущих передач), первую строку мы уже
# превратили в прогресс — остальные забивают лог, а наружу отдаются последние 40
# строк, и в них не осталось бы ничего, кроме статистики.
_NOISE = re.compile(r"^(Checks|Deleted|Renamed|Transferred|Elapsed time|Errors|"
                    r"Server Side (?:Copies|Moves)|Transferring|\*\s)", re.I)


def is_noise(line: str) -> bool:
    return bool(_NOISE.match(line.strip()))


# Тот же блок статистики, но разобранный по полям — для живого статуса на странице.
# Нужные строки различаются только формой:
#   Transferred:   0.512 GiB / 1.234 GiB, 41%, 12.5 MiB/s, ETA 1m2s   ← байты
#   Transferred:            2 / 5, 40%                                ← счётчик файлов
#    *  IMG_6753.MOV: 41% /1.234Gi, 12.345Mi/s, 1m2s                  ← что качается сейчас
_FILES = re.compile(r"^Transferred:\s*(\d+)\s*/\s*(\d+),\s*\d+%\s*$")
_BYTES = re.compile(r"^Transferred:\s*(\d+(?:\.\d+)?\s?[A-Za-z]+)\s*/\s*"
                    r"(\d+(?:\.\d+)?\s?[A-Za-z]+),\s*(\d+)%"
                    r"(?:,\s*(\d+(?:\.\d+)?\s?[A-Za-z/]+))?(?:,\s*ETA\s*(\S+))?")
_CURFILE = re.compile(r"^\*\s+(.+?):\s*(\d+)%\s*/")


def stats_fields(line: str) -> dict[str, Any] | None:
    """Строка статистики rclone → поля статуса для страницы, или None.

    Отдельно от `progress_line`: пока прогресс жил только в логе джоба (а лог
    наружу не отдавался вовсе), в статусе до самого конца висело «запуск
    rclone…» — по нему нельзя отличить работу от повисшего процесса, и на
    многогигабайтном файле это выглядит как «ничего не происходит». Забираем
    всё, что rclone печатает: сколько скачано, скорость, остаток времени,
    счётчик файлов и имя текущего файла.
    """
    s = line.strip()
    m = _FILES.match(s)          # счётчик файлов проверяем первым: у него нет единиц
    if m:
        return {"i": int(m.group(1)), "n": int(m.group(2))}
    m = _BYTES.match(s)
    if m:
        return {"bytes": m.group(1), "total": m.group(2), "pct": int(m.group(3)),
                "speed": m.group(4) or "", "eta": m.group(5) or ""}
    m = _CURFILE.match(s)
    if m:
        return {"file": m.group(1), "file_pct": int(m.group(2))}
    return None


_CHECKS = re.compile(r"^Checks:\s*(\d+)\s*/\s*(\d+)", re.I)


def checks_fields(line: str) -> dict[str, Any] | None:
    """Строка проверок rclone (Checks: i / n) → поля прогресса или None.

    В статус страницы (stat) идти не обязан, но считается живым прогрессом
    для сторожа простоя: когда байты дошли до 100%, rclone
    проверяет хеши файлов, и здоровая проверка не должна сниматься сторожем.
    """
    s = line.strip()
    m = _CHECKS.match(s)
    if m:
        return {"ci": int(m.group(1)), "cn": int(m.group(2))}
    return None


# Дата и уровень в начале строки rclone: в узком логе страницы это половина
# ширины, а время там своё.
_TS = re.compile(r"^\d{4}/\d\d/\d\d \d\d:\d\d:\d\d\s+"
                 r"(?:DEBUG|INFO|NOTICE|WARNING|ERROR)\s*:\s*")


def clean_line(line: str) -> str:
    return _TS.sub("", line.strip())
