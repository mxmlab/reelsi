# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Скачивание материала с гугл-диска по ссылке через rclone (задание G).

Своя качалка на requests тут невозможна: гугл на файлах крупнее ~100 МБ отдаёт
страницу-предупреждение о проверке на вирусы вместо файла, и наивная загрузка
молча сохранит HTML вместо видео. rclone это умеет, не тянет заново уже
скачанное и докачивает после обрыва.

Авторизацию настраивает ПОЛЬЗОВАТЕЛЬ (rclone config открывает браузер и входит в
аккаунт сам): этот модуль только читает его конфиг и зовёт уже настроенный
rclone. Секреты живут в конфиге rclone, в наши файлы ничего не пишется.
"""
import os, re, shutil, subprocess, threading, time
from flask import request, jsonify
from ._core import LOG_CAP, bp, umsg_err
from core.umsg import umsg

# Путь к конфигу rclone. По умолчанию — стандартное место rclone на платформе;
# REELSI_RCLONE_CONF (старое имя AUTOCUT_RCLONE_CONF) переопределяет — например,
# для изолированного тестового профиля на 5098.
CONF_ENV = "REELSI_RCLONE_CONF"
REMOTE_ENV = "REELSI_RCLONE_REMOTE"


def rclone_conf():
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


def parse_gdrive_link(url):
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


def rclone_remotes(conf=None):
    """Имена drive-remote из конфига rclone. Конфиг — простой INI: секции [имя]
    с ключами; нашим ремоутом секция становится, если в ней `type = drive`."""
    conf = conf or rclone_conf()
    if not os.path.isfile(conf):
        return []
    out, cur, is_drive = [], None, False
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


def rclone_remote(conf=None):
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


def rclone_cmd(remote, url, dest):
    """Аргументы rclone для ссылки. Файл — backend copyid (документированный
    доступ по ID), папка — copy с --drive-root-folder-id (то же по-другому:
    папка становится корнем remote). Список, без shell: путь из ссылки не может
    стать аргументом оболочки."""
    spec = parse_gdrive_link(url)
    if not spec:
        raise ValueError("не ссылка гугл-диска: " + str(url)[:80])
    # -v обязателен. rclone логирует статистику на уровне INFO (--stats-log-level,
    # по умолчанию INFO), а порог вывода по умолчанию — NOTICE, то есть с одним
    # --stats в лог не попадёт НИ ОДНОЙ строки прогресса и скачивание гигабайтов
    # выглядит как зависшее. С -v строки идут, заодно видно, что именно скачалось.
    # --stats 2s, а не 5s: статистика — единственный источник живого статуса на
    # странице, и раз в 5 секунд он выглядит подвисающим. Лог от этого не пухнет:
    # прогресс уходит в поля статуса, а в лог дублируется раз в 10%.
    args = ["rclone", "-v", "--config", rclone_conf(), "--stats", "2s"]
    if spec["kind"] == "file":
        d = dest.replace("\\", "/").rstrip("/") + "/"
        args += ["backend", "copyid", remote + ":", spec["id"], d]
    else:
        args += ["copy", "--drive-root-folder-id", spec["id"], remote + ":", dest]
        if spec["resource_key"]:
            args += ["--drive-resource-key", spec["resource_key"]]
    return args


_STATS = re.compile(r"Transferred:\s*(\d+(?:\.\d+)?\s?[A-Za-z]+)\s*/\s*(\d+(?:\.\d+)?\s?[A-Za-z]+),\s*(\d+)%")


def _progress_line(line):
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


def _is_noise(line):
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


def _stats_fields(line):
    """Строка статистики rclone → поля статуса для страницы, или None.

    Отдельно от `_progress_line`: пока прогресс жил только в логе джоба (а лог
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


# Дата и уровень в начале строки rclone: в узком логе страницы это половина
# ширины, а время там своё.
_TS = re.compile(r"^\d{4}/\d\d/\d\d \d\d:\d\d:\d\d\s+"
                 r"(?:DEBUG|INFO|NOTICE|WARNING|ERROR)\s*:\s*")


def _clean_line(line):
    return _TS.sub("", line.strip())


def _run_rclone(args, emit, stat=None):
    """rclone копией сабпроцессом. Статистика уходит в `stat` (живой статус
    страницы), события — в `emit` (лог джоба).

    В лог прогресс дублируется раз в 10%: лог — это след событий, по которому
    потом видно, докуда дошло, а не лента процентов (наружу отдаются последние
    строки, и при статистике раз в 2 секунды в них не осталось бы ничего).
    """
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace",
                         bufsize=1, creationflags=flags)
    logged = -10          # -10, а не 0: первый же блок (0%) отмечает в логе старт передачи
    for line in p.stdout:
        line = line.rstrip()
        if not line.strip():
            continue
        f = _stats_fields(line)
        if f:
            if stat:
                stat(f)
            pct = f.get("pct")
            if pct is not None and pct >= logged + 10:
                logged = pct - pct % 10
                emit(_progress_line(line))
            continue
        if not _is_noise(line):
            emit(_clean_line(line))
    return p.wait()


# failed: None пока идём, True/False по итогу — страница показывала «скачивание
# завершено» на любом исходе, и упавший rclone выглядел как успех. Имя не `ok`:
# ответ и так завёрнут в ok=True («запрос принят»), и два разных смысла у одного
# ключа — это 500 на ровном месте.
GDFRESH = {"cur": "запуск rclone…", "i": 0, "n": 0, "pct": None, "bytes": "", "total": "",
           "speed": "", "eta": "", "file": "", "file_pct": None}
GDJOB = dict(GDFRESH, running=False, done=False, failed=None, log=[], log_base=0,
             url="", started=0)
GDLOCK = threading.Lock()


def _gemit(line):
    with GDLOCK:
        GDJOB["log"].append(str(line))
        over = len(GDJOB["log"]) - LOG_CAP
        if over > 0:
            del GDJOB["log"][:over]
            GDJOB["log_base"] += over


def _gstat(fields):
    """Поля прогресса из статистики rclone — в состояние джоба."""
    with GDLOCK:
        GDJOB.update(fields)


def _download_job(cmd, url):
    ok = False
    try:
        _gemit(f"качаю {url}")
        code = _run_rclone(cmd, _gemit, _gstat)
        ok = code == 0
        cur = "скачивание завершено" if ok else f"rclone упал с кодом {code}"
        with GDLOCK:
            GDJOB["cur"] = cur
        _gemit(cur)          # чем кончилось — видно и в логе, не только в статусе
    except Exception as e:
        _gemit(f"ОШИБКА: {type(e).__name__}: {e}")
        with GDLOCK:
            GDJOB["cur"] = f"ошибка: {e}"
    finally:
        with GDLOCK:
            GDJOB.update(running=False, done=True, failed=not ok)


@bp.route("/api/gdrive_download", methods=["POST"])
def api_gdrive_download():
    """Скачать материал по ссылке гугл-диска. body: {url, dest}.
    Свой джоб (GDJOB), не общий JOB: нарезка не должна ждать гигабайт с диска —
    тот же образец, что у превью-прокси (PXJOB)."""
    d = request.get_json(silent=True) or {}
    url = (d.get("url") or "").strip()
    dest = (d.get("dest") or "").strip().strip('"')
    try:
        spec = parse_gdrive_link(url)
        if not spec:
            raise SystemExit(umsg("bad_link", "Не похоже на ссылку гугл-диска (нужна ссылка "
                                "на файл или папку)"))
        with GDLOCK:
            if GDJOB["running"]:
                raise SystemExit(umsg("download_busy", "Скачивание уже идёт — дождись конца"))
        if not shutil.which("rclone"):
            raise SystemExit(umsg("rclone_missing", "rclone не найден в PATH. Поставь rclone и один раз "
                                "настрой гугл-диск: rclone config"))
        try:
            remote = rclone_remote()
        except RuntimeError as e:
            raise SystemExit(umsg("rclone_not_configured", str(e), err=str(e)))
        try:
            os.makedirs(dest, exist_ok=True)
            if not os.path.isdir(dest):
                raise SystemExit(umsg("no_dest", f"Не получается создать папку загрузки: {dest}",
                                      path=dest))
            cmd = rclone_cmd(remote, url, dest)
        except OSError as e:
            raise SystemExit(umsg("dest_error", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
        with GDLOCK:
            GDJOB.update(GDFRESH, running=True, done=False, failed=None, log=[], log_base=0,
                         url=url, started=int(time.time()))
        threading.Thread(target=_download_job, args=(cmd, url), daemon=True).start()
        return jsonify(ok=True, remote=remote, kind=spec["kind"], dest=dest)
    except SystemExit as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/gdrive_status")
def api_gdrive_status():
    """Прогресс фонового скачивания с гугл-диска.

    `?since=` — сколько строк лога уже у клиента (тот же уговор, что у
    /api/video_status): раньше отдавались последние 40 строк без счётчика, и
    страница не могла понять, какие из них новые, — поэтому лог rclone до неё
    вообще не доходил.
    """
    try:
        since = int(request.args.get("since") or 0)
    except ValueError:
        since = 0
    with GDLOCK:
        start = max(0, since - GDJOB["log_base"])
        d = {k: v for k, v in GDJOB.items() if k not in ("log", "log_base")}
        d["log"] = GDJOB["log"][start:]
        d["log_total"] = GDJOB["log_base"] + len(GDJOB["log"])
        d["elapsed"] = int(time.time()) - GDJOB["started"] if GDJOB["started"] else 0
    return jsonify(ok=True, **d)
