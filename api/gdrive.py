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
from ._core import LOG_CAP, bp, jstr, kill_tree, umsg_err, sysexit_text
from core.applog import get_logger
from core.umsg import umsg

log = get_logger("reelsi.gdrive")

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
    dest = str(dest)
    # Путь назначения приходит из запроса, а значение, начинающееся с «-», rclone
    # разберёт как ОПЦИЮ: `--config=<чужой конфиг>` увёл бы скачивание на чужие
    # токены, `--dry-run` сделал бы вид, что скачали. Отказываем до всякого rclone
    # (задание MZ, п. 3).
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
    # `--` перед первым позиционным аргументом (задание MZ, п. 3): после него для
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


_CHECKS = re.compile(r"^Checks:\s*(\d+)\s*/\s*(\d+)", re.I)


def _checks_fields(line):
    """Строка проверок rclone (Checks: i / n) → поля прогресса или None.

    В статус страницы (stat) идти не обязан, но считается живым прогрессом
    для сторожа простоя (задание IX): когда байты дошли до 100%, rclone
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


# Сторож простоя rclone: 10 минут тишины в stdout -> принудительное завершение (задание HU)
RCLONE_STALL_SEC = 10 * 60
_RCLONE_STALLED = -137
# Поля разобранного прогресса, по изменению которых видно, что передача идёт
# (задание IC, п. 4; задание IX). `speed`/`eta` сюда не входят намеренно: они меняются
# и у блока статистики, напечатанного по таймеру `--stats 2s`, когда передача уже встала.
_PROGRESS_KEYS = ("bytes", "pct", "file", "file_pct", "i", "n", "ci", "cn")

GDPROC = None  # Текущий процесс rclone — для отмены через /api/cancel и сторожа простоя


def _kill_proc(p):
    """taskkill /T /F — дерево: rclone с дочерними процессами снимается полностью.

    Имя оставлено ради тестов: реализация теперь одна на пакет — `_core.kill_tree`
    (задание IC, п. 6)."""
    kill_tree(p)


def gdrive_kill():
    """«Стоп» из интерфейса (/api/cancel зовёт): флаг джобу + реально убить
    текущий subprocess rclone с деревом."""
    with GDLOCK:
        GDJOB["cancel"] = True
        p = GDPROC
    if p and p.poll() is None:
        _kill_proc(p)


def _clean_line(line):
    return _TS.sub("", line.strip())


def _run_rclone(args, emit, stat=None):
    """rclone копией сабпроцессом со сторожем простоя и поддержкой отмены.
    Статистика уходит в `stat` (живой статус страницы), события — в `emit` (лог джоба).

    В лог прогресс дублируется раз в 10%: лог — это след событий, по которому
    потом видно, докуда дошло, а не лента процентов (наружу отдаются последние
    строки, и при статистике раз в 2 секунды в них не осталось бы ничего).

    Вывод читает ФОНОВЫЙ поток (образец — `_run_proc_afx` в api/render.py): пока
    главный поток сидит в `for line in p.stdout`, он не может ни заметить простой,
    ни снять зависший процесс — а зависшая сеть держала «Скачивание уже идёт»
    до перезапуска сервера. Все строки разбирает ОДИН обработчик `_handle`: если
    разбирать хвост вывода второй копией (после выхода процесса), прогресс уедет
    только в статус, а из лога конец передачи пропадёт.

    Простой считается по ИЗМЕНЕНИЮ разобранного прогресса, а не по факту строки
    (задание IC, п. 4): `--stats 2s` печатает блок статистики по таймеру, и раньше
    вставшая передача выглядела живой. Исключение на строке ловится на САМОЙ строке —
    поток чтения из-за одной непонятной строки больше не умирает.
    """
    global GDPROC
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace",
                         bufsize=1, creationflags=flags)
    with GDLOCK:
        GDPROC = p
    if GDJOB.get("cancel"):
        _kill_proc(p)

    logged = -10          # -10, а не 0: первый же блок (0%) отмечает в логе старт передачи
    # Время последней РЕАЛЬНОЙ активности: от него, а не от старта, считаем простой —
    # большая папка качается минутами, и рубить её нельзя.
    activity = [time.time()]
    # Последний разобранный прогресс по полям: активность — изменение значения
    # ЛЮБОГО поля прогресса относительно его прежнего значения (словарь last_stat[k]),
    # а не кортежа строки (задание IX). Строки блока статистики (--stats 2s) имеют
    # разную форму и чередуются; раньше кортеж строки «менялся» на каждой строке
    # при полностью замороженных значениях, и вставшая передача не снималась.
    last_stat = {}

    def _handle(raw):
        nonlocal logged
        line = raw.rstrip()
        if not line.strip():
            return
        f = _stats_fields(line)
        chk = None if f else _checks_fields(line)
        prog = f or chk
        if prog:
            if f and stat:
                stat(f)
            # Прогресс изменился (байты/проценты/имя файла, счётчик файлов, проверки) —
            # передача жива. Скорость и ETA в ключ НЕ входят: они меняются у блока статистики
            # сами по себе и «оживили» бы вставшую передачу.
            changed = False
            for k in _PROGRESS_KEYS:
                if k in prog:
                    if k not in last_stat or last_stat[k] != prog[k]:
                        last_stat[k] = prog[k]
                        changed = True
            if changed:
                activity[0] = time.time()
            if f:
                pct = f.get("pct")
                if pct is not None and pct >= logged + 10:
                    logged = pct - pct % 10
                    emit(_progress_line(line))
            return
        if _is_noise(line):
            return                       # остальные строки блока статистики — не активность
        activity[0] = time.time()        # не статистика — живое событие rclone
        emit(_clean_line(line))

    # Исключение ловится НА СТРОКУ: раньше `except Exception` стоял вокруг всего цикла,
    # поток чтения умирал на первой же неожиданной строке, активность замирала — и
    # сторож снимал ЗДОРОВОЕ скачивание через 10 минут (задание IC, п. 4).
    died = [None]

    def _pump():
        try:
            for line in p.stdout:
                try:
                    _handle(line)
                except Exception as e:
                    log.warning("rclone: строка вывода не разобрана: %s: %s",
                                type(e).__name__, e)
        except Exception as e:               # умерло само чтение потока, не разбор строки
            died[0] = f"{type(e).__name__}: {e}"
            log.warning("rclone: поток чтения вывода упал: %s", died[0])

    pump = threading.Thread(target=_pump, daemon=True)
    pump.start()

    stalled = False
    warned_dead = False
    try:
        while p.poll() is None:
            now = time.time()
            if died[0] is not None and not warned_dead:
                # Активность больше неоткуда взять: по простою НЕ убиваем — процесс
                # жив и может дописывать файл молча (задание IC, п. 4).
                warned_dead = True
                emit("⚠ поток чтения вывода rclone умер — активность не отслеживается, "
                     "скачивание не снимаю по простою (снять — «Стоп»)")
            if not warned_dead and now - activity[0] >= RCLONE_STALL_SEC:
                emit(f"⚠ rclone не отвечает {int(RCLONE_STALL_SEC // 60)} минут — "
                     f"скачивание снято (сторож простоя)")
                _kill_proc(p)
                stalled = True
                break
            if GDJOB.get("cancel"):
                _kill_proc(p)
                break
            time.sleep(0.5)
        rc = p.wait()
        # Хвост вывода (последние строки rclone) читается ещё мгновение после выхода;
        # ждём его, но не бесконечно: насос — демон и не должен держать джоб.
        pump.join(timeout=10.0)
    finally:
        with GDLOCK:
            if GDPROC is p:
                GDPROC = None
    return _RCLONE_STALLED if stalled else rc


# failed: None пока идём, True/False по итогу — страница показывала «скачивание
# завершено» на любом исходе, и упавший rclone выглядел как успех. Имя не `ok`:
# ответ и так завёрнут в ok=True («запрос принят»), и два разных смысла у одного
# ключа — это 500 на ровном месте.
# cancelled: «Стоп» нажал человек — это НЕ ошибка (задание IC, п. 3): раньше отмена
# приезжала на страницу как failed=True и рисовалась красным тостом «не удалось».
GDFRESH = {"cur": "запуск rclone…", "i": 0, "n": 0, "pct": None, "bytes": "", "total": "",
           "speed": "", "eta": "", "file": "", "file_pct": None, "cancel": False,
           "cancelled": False}
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
        if GDJOB.get("cancel"):
            cur = "скачивание остановлено"
        elif code == _RCLONE_STALLED:
            cur = f"нет данных {int(RCLONE_STALL_SEC // 60)} минут"
        elif ok:
            cur = "скачивание завершено"
        else:
            cur = f"rclone упал с кодом {code}"
        with GDLOCK:
            GDJOB["cur"] = cur
        _gemit(cur)          # чем кончилось — видно и в логе, не только в статусе
    except SystemExit as e:
        # SystemExit (umsg) — BaseException: без ветки скачивание отмечалось упавшим,
        # но БЕЗ причины — ни в логе, ни в GDJOB["cur"] её не было (задание MX).
        txt = sysexit_text(e)
        _gemit(f"ОШИБКА: {txt}")
        with GDLOCK:
            GDJOB["cur"] = f"ошибка: {txt}"
    except Exception as e:
        _gemit(f"ОШИБКА: {type(e).__name__}: {e}")
        with GDLOCK:
            GDJOB["cur"] = f"ошибка: {e}"
    finally:
        # Отмена — не ошибка: failed=False + cancelled=True, страница покажет
        # нейтральное «скачивание остановлено» без красного тоста (задание IC, п. 3).
        cancelled = bool(GDJOB.get("cancel"))
        with GDLOCK:
            GDJOB.update(running=False, done=True, cancelled=cancelled,
                         failed=(not ok) and not cancelled)


@bp.route("/api/gdrive_download", methods=["POST"])
def api_gdrive_download():
    """Скачать материал по ссылке гугл-диска. body: {url, dest}.
    Свой джоб (GDJOB), не общий JOB: нарезка не должна ждать гигабайт с диска —
    тот же образец, что у превью-прокси (PXJOB)."""
    d = request.get_json(silent=True) or {}
    url = jstr(d, "url").strip()
    dest = jstr(d, "dest").strip().strip('"')
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
        try:
            threading.Thread(target=_download_job, args=(cmd, url), daemon=True).start()
        except Exception:
            with GDLOCK:
                GDJOB["running"] = False
            raise
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
