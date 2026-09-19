# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Файлы и папки: список камер, автоподбор камер по звуку, поиск ещё не нарезанных
дублей, нативные диалоги выбора, отдача медиа, ui_state.
"""
import os, re, sys, json, subprocess
from flask import request, jsonify, send_file
from core.fileio import atomic_json_dump, json_load_soft
import reelsi
from core import sync
from core import xmlbuild
from ._core import (DEFAULT_BASE, UI_STATE_PATH, _never_serve, app_out_dir, bp, jstr,
                    umsg_err)
from core.umsg import umsg


def _cams_response(base):
    """Ответ /api/cams: найденные папки камер (или ошибка «нет папок»).
    Отдельной функцией, чтобы его переиспользовал /api/cams_make — после создания
    папок ответ должен быть ровно тем же, что и при ручном выборе."""
    paths = reelsi.find_cam_dirs(base)
    if not paths:
        return jsonify(**umsg_err(SystemExit(umsg("no_cam_folders",
            f"В {base} нет папок камер ('камер*' / 'camera*') — создай или выбери вручную", base=base))))
    dirs = [{"name": os.path.basename(p), "path": p, "files": reelsi.list_videos(p)}
            for p in paths[:4]]
    return jsonify(base=base, outdir=app_out_dir(base), dirs=dirs)


@bp.route("/api/cams")
def api_cams():
    base = request.args.get("base", DEFAULT_BASE)
    try:
        return _cams_response(base)
    except Exception as e:
        return jsonify(**umsg_err(SystemExit(umsg("cams_failed", str(e)))))


@bp.route("/api/cams_make", methods=["POST"])
def api_cams_make():
    """Создать папки камер 1..N на чистой установке, где их нет вовсе.
    Только по явной кнопке в интерфейсе: молча создавать папки в чужой папке
    нельзя — пользователь мог указать не ту base."""
    d = request.get_json(silent=True) or {}
    base = jstr(d, "base") or DEFAULT_BASE
    n = max(1, min(4, int(d.get("n") or 1)))
    lang = jstr(d, "lang").lower() or "ru"
    prefix = "camera" if lang == "en" else "камера"
    try:
        for k in range(1, n + 1):
            os.makedirs(os.path.join(base, f"{prefix}{k}"), exist_ok=True)
        return _cams_response(base)
    except Exception as e:
        return jsonify(**umsg_err(SystemExit(umsg("cams_make_failed", str(e)))))


@bp.route("/api/cammatch", methods=["POST"])
def api_cammatch():
    """Автоподбор вторичных камер по звуку: видео камеры 1 (cam1) уже выбрано,
    в каждой папке dirs (камеры 2..N) ищем файл ТОГО ЖЕ дубля — его звук лучше
    всего коррелирует со звуком cam1 (имена файлов у камер могут не совпадать).
    На папку — лучший файл {name, offset, score} или name=null, если ни один не
    дотянул до sync.MATCH_MIN. Камеру 1 не подбираем: она задаёт дубль, с неё
    идёт звук нарезки.

    cam1dir + cam1 (папка и ИМЯ файла) склеиваются здесь, как это делает /api/run:
    фронт лепил разделитель сам («…dir» + '\\' + имя) и на Linux/macOS получал
    «/home/u/cam\\file.mp4» — файл не находился, кнопка всегда отвечала «нет видео
    камеры 1» (поймано 2026-08-11). Целый путь в cam1 тоже принимаем — так звали
    роут до этой правки."""
    d = request.get_json(silent=True) or {}
    cam1 = jstr(d, "cam1").strip().strip('"')
    cam1dir = jstr(d, "cam1dir").strip().strip('"')
    if cam1dir and cam1:
        cam1 = os.path.join(cam1dir, os.path.basename(cam1))
    dirs = [x.strip().strip('"') for x in (d.get("dirs") or [])
            if isinstance(x, str) and x.strip()]
    if not cam1 or not os.path.isfile(cam1):
        return jsonify(**umsg_err(SystemExit(umsg("cam1_not_found", "нет видео камеры 1"))))
    if not dirs:
        return jsonify(**umsg_err(SystemExit(umsg("no_dirs", "нет папок камер 2..N"))))
    try:
        ea, rate = sync.video_envelope(cam1)
        out = []
        for folder in dirs:
            best = None
            if os.path.isdir(folder):
                for f in reelsi.list_videos(folder):
                    full = os.path.join(folder, f)
                    if os.path.abspath(full).lower() == os.path.abspath(cam1).lower():
                        continue   # сам себя не подбираем
                    try:
                        eb, _ = sync.video_envelope(full)
                        off, sc = sync.match_score(ea, eb, rate)
                    except Exception:
                        continue   # без звука/битый — не кандидат
                    if best is None or sc > best["score"]:
                        best = {"name": f, "offset": round(off, 3), "score": round(sc, 3)}
            out.append({"dir": folder,
                        "name": best["name"] if best and best["score"] >= sync.MATCH_MIN else None,
                        "offset": best["offset"] if best else 0.0,
                        "score": best["score"] if best else 0.0})
        return jsonify(ok=True, cam1=os.path.basename(cam1), matches=out)
    except Exception as e:
        return jsonify(**umsg_err(SystemExit(umsg("cammatch_failed", f"{type(e).__name__}: {e}"))))


# Префикс очереди в имени результата: nn_<имя исходника>.xml (см. run_omnicut_job)
_QUEUE_PREFIX = re.compile(r"^\d+_")
_PATHURL = re.compile(r"<pathurl>([^<]+)</pathurl>")


def _basename(p):
    """Имя файла из пути ЛЮБОГО вида. os.path.basename на Linux не режет '\\', а в
    project.json пути лежат так, как их дал Windows ('D:/съёмка\\C1437.MP4')."""
    return str(p).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def _clip_cams(xml_path):
    """Список путей исходных камер клипа.

    Сначала пробует `<stem>.project.json`. Если сайдкара нет (XML добавлен
    руками) или cams пустой — читает теги `<pathurl>` из самого XML-файла.
    """
    proj = json_load_soft(os.path.splitext(xml_path)[0] + ".project.json") or {}
    cams = [c for c in (proj.get("cams") or []) if c and str(c).strip()]
    if cams:
        return cams
    if not os.path.isfile(xml_path):
        return []
    try:
        with open(xml_path, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except OSError:
        return []
    # Сюда попадут и вставки с музыкой — своих имён у них с дублями камеры не
    # бывает (C1437.MP4 против имени из «Скаченного»), а у нарезок Reelsi есть
    # сайдкар, и до этой ветки они не доходят вовсе.
    return [xmlbuild.unpathurl(u) for u in _PATHURL.findall(text) if u]


def _cut_sources(outdir):
    """По каким исходникам в папке результата УЖЕ есть нарезка: (имена, стемы).

    Три источника, от точного к грубому:
      - `<stem>.project.json` — там лежат пути камер прогона, файл крошечный;
      - сам XML (`<pathurl>`) — если сайдкар потеряли (2 МБ на файл, поэтому
        читаем только когда сайдкара нет);
      - имя самого XML со снятым префиксом очереди («03_C1432.xml» -> «C1432») —
        на случай XML, собранного руками или в другой программе.
    Всё в нижнем регистре: на Windows регистр в именах файлов не значит ничего.
    """
    names, stems = set(), set()
    for f in sorted(os.listdir(outdir)):
        if not f.lower().endswith(".xml"):
            continue                     # .xml.bak — копия прошлой сборки, не результат
        full = os.path.join(outdir, f)
        stems.add(_QUEUE_PREFIX.sub("", os.path.splitext(f)[0]).lower())
        cams = _clip_cams(full)
        names.update(_basename(c).lower() for c in cams)
    return names, stems


@bp.route("/api/newtakes", methods=["POST"])
def api_newtakes():
    """Какие дубли ещё НЕ нарезаны: файлы, для которых в папке результата нет XML.

    Ради этого и делается: папка камеры копится съёмками, а нарезать надо только
    то, что приехало с последней. Автоподбор камер по звуку без такого отсева
    гоняет корреляцию по ВСЕМ файлам и добавляет в очередь давно готовое.

    Вход: {outdir, files:[имена]} или {outdir, dir} (тогда список берём из папки).
    Выход: {new:[…], done:[…]} — исходный порядок сохраняется.
    Нет папки результата (первый прогон) — новые все, это не ошибка.
    """
    d = request.get_json(silent=True) or {}
    outdir = jstr(d, "outdir").strip().strip('"')
    files = [str(x) for x in (d.get("files") or []) if str(x).strip()]
    if not files:
        dir_ = jstr(d, "dir").strip().strip('"')
        if not os.path.isdir(dir_):
            return jsonify(**umsg_err(SystemExit(umsg("no_folder", f"Нет папки: {dir_}", path=dir_))))
        files = reelsi.list_videos(dir_)
    if not os.path.isdir(outdir):
        return jsonify(ok=True, new=files, done=[], no_outdir=True)
    try:
        names, stems = _cut_sources(outdir)
        new, done = [], []
        for f in files:
            base = _basename(f).lower()
            cut = base in names or os.path.splitext(base)[0] in stems
            (done if cut else new).append(f)
        return jsonify(ok=True, new=new, done=done)
    except Exception as e:
        return jsonify(**umsg_err(SystemExit(umsg("newtakes_failed", f"{type(e).__name__}: {e}"))))


@bp.route("/api/files")
def api_files():
    d = request.args.get("dir", "").strip().strip('"')
    if not os.path.isdir(d):
        # files=[] рядом с ошибкой: фронт рисует пустой список, а не падает
        return jsonify(files=[], **umsg_err(SystemExit(umsg("no_folder", "нет папки"))))
    return jsonify(files=reelsi.list_videos(d), name=os.path.basename(d))


@bp.route("/api/ui_state", methods=["GET", "POST"])
def api_ui_state():
    """Серверное зеркало состояния UI (клипы/очередь/стиль). localStorage остаётся
    основным и быстрым, файл — надёжная копия: переживает смену браузера, чистку
    и квоту localStorage. Пишется атомарно (tmp+replace)."""
    if request.method == "GET":
        try:
            if os.path.isfile(UI_STATE_PATH):
                with open(UI_STATE_PATH, encoding="utf-8") as f:
                    return jsonify(ok=True, state=json.load(f))
            return jsonify(ok=True, state=None)
        except Exception as e:
            return jsonify(**umsg_err(SystemExit(umsg("ui_state_load_failed", f"{type(e).__name__}: {e}"))))
    d = request.get_json(silent=True) or {}
    # тело без JSON давало d.get("state") is None, и зеркало перезаписывалось
    # значением null — состояние пользователя пропадало молча
    if not isinstance(d, dict) or d.get("state") is None:
        return jsonify(**umsg_err(SystemExit(umsg("ui_state_empty",
            "пустое или повреждённое тело запроса — состояние не перезаписано"))))
    try:
        # Общий tmp на два одновременных запроса (два таба) перемешивал половины:
        # каждый open(tmp,"w") усекал файл другого. mkstemp — свой tmp на запись.
        atomic_json_dump(UI_STATE_PATH, d.get("state"))
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(**umsg_err(SystemExit(umsg("ui_state_save_failed", f"{type(e).__name__}: {e}"))))


def _native_pick(dialog_call):
    """Run a Tk dialog in a subprocess (never touches the Flask thread) and return
    the chosen path. The app is local, so the dialog opens on the user's screen."""
    code = (
        "import sys, tkinter as tk\n"
        "from tkinter import filedialog\n"
        "r=tk.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
        f"p={dialog_call}\n"
        "sys.stdout.buffer.write((p or '').encode('utf-8'))\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=300)
    return r.stdout.decode("utf-8").strip()


@bp.route("/api/pickmedia")
def api_pickmedia():
    try:
        return jsonify(path=_native_pick(
            "filedialog.askopenfilename(title='Выбери фото/видео', "
            # webp/avif показываем: их полно в «Скаченном». After Effects их не читает,
            # поэтому на сборке они молча перекодируются в PNG (insertlib.to_ae_image)
            "filetypes=[('Медиа','*.png *.jpg *.jpeg *.webp *.avif *.gif "
            "*.mp4 *.mov *.m4v *.webm'),('Все файлы','*.*')])"))
    except Exception as e:
        return jsonify(**umsg_err(SystemExit(umsg("pickmedia_failed", str(e)))))


@bp.route("/api/fonts")
def api_fonts():
    """Установленные шрифты (PostScript-имя + семья) для автоподстановки в стиле."""
    try:
        from core import fonts
        return jsonify(ok=True, fonts=fonts.list_fonts())
    except Exception as e:
        return jsonify(ok=True, fonts=[], note=str(e))   # деградируем до свободного ввода


@bp.route("/api/fontfile/<path:ps_name>")
def api_fontfile(ps_name):
    """Отдать файл шрифта по PostScript-имени из таблицы шрифтов (задание DB).

    Только чтение, только файлы из list_fonts() — прямой путь из запроса не
    принимается (защита от чтения произвольных файлов).
    """
    try:
        from core import fonts
        rec = next((r for r in fonts.list_fonts() if r.get("ps") == ps_name), None)
        if not rec or not rec.get("file"):
            return jsonify(ok=False, error="font_not_found"), 404
        file_path = rec["file"]
        if _never_serve(file_path):
            return jsonify(ok=False, error="forbidden"), 403
        if not os.path.isfile(file_path):
            return jsonify(ok=False, error="file_not_found"), 404

        ext = os.path.splitext(file_path)[1].lower()
        mime = {
            ".ttf": "font/ttf",
            ".otf": "font/otf",
            ".woff": "font/woff",
            ".woff2": "font/woff2",
            ".ttc": "font/collection",
        }.get(ext, "application/octet-stream")

        return send_file(file_path, mimetype=mime, conditional=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500



@bp.route("/api/pickfiles")
def api_pickfiles():
    """Multi-select XML files (returns a list of paths)."""
    try:
        raw = _native_pick("'|'.join(filedialog.askopenfilenames(title='Выбери XML', "
                           "filetypes=[('XML','*.xml'),('Все файлы','*.*')]))")
        return jsonify(paths=[p for p in raw.split("|") if p.strip()])
    except Exception as e:
        return jsonify(**umsg_err(SystemExit(umsg("pickfiles_failed", str(e)))))


@bp.route("/api/pickone")
def api_pickone():
    """Один файл любого типа (для видео-перехода/звука/попа в кастом-стиле)."""
    try:
        p = _native_pick("filedialog.askopenfilename(title='Выбери файл')")
        return jsonify(path=p)
    except Exception as e:
        return jsonify(**umsg_err(SystemExit(umsg("pickone_failed", str(e)))))


@bp.route("/api/pickaudio")
def api_pickaudio():
    try:
        return jsonify(path=_native_pick(
            "filedialog.askopenfilename(title='Выбери аудио', "
            "filetypes=[('Аудио','*.m4a *.mp3 *.wav *.aac *.opus *.flac *.ogg'),('Все файлы','*.*')])"))
    except Exception as e:
        return jsonify(**umsg_err(SystemExit(umsg("pickaudio_failed", str(e)))))


@bp.route("/api/pickdir")
def api_pickdir():
    try:
        return jsonify(path=_native_pick("filedialog.askdirectory(title='Выбери папку')"))
    except Exception as e:
        return jsonify(**umsg_err(SystemExit(umsg("pickdir_failed", str(e)))))


# Allowlist расширений для /api/media: видео, картинки, звук (задание HU).
# Фронт использует /api/media для стриминга видео (в т.ч. прокси pv_*.mp4),
# показа картинок-вставок и воспроизведения музыки/SFX.
ALLOWED_MEDIA_EXTS = {
    # Видео (mpg/mpeg — проект считает их видео, см. core/verify_jsx.py VIDEO_EXT)
    "mp4", "mov", "m4v", "mkv", "webm", "avi", "mxf", "mts", "m2ts", "mpg", "mpeg",
    # Картинки
    "png", "jpg", "jpeg", "gif", "webp", "avif", "bmp", "tif", "tiff", "heic",
    # Звук
    "wav", "mp3", "m4a", "aac", "flac", "ogg", "opus", "aif", "aiff",
}

# Allowlist /api/waveform (задание MC): только звук и видео — то, откуда волна
# вообще берётся. Картинки из списка выше исключены нарочно: волны из них не
# выйдет, а кэш роут пишет РЯДОМ С ЦЕЛЬЮ (<путь>.peaks<pps>.json) — по просьбе
# страницы он создавал файл рядом с любым файлом на диске.
ALLOWED_WAVE_EXTS = {
    # Видео
    "mp4", "mov", "m4v", "mkv", "webm", "avi", "mxf", "mts", "m2ts", "mpg", "mpeg",
    # Звук
    "wav", "mp3", "m4a", "aac", "flac", "ogg", "opus", "aif", "aiff",
}


# Браузер держит не больше 6 соединений на хост, а <video>/<audio> просят
# `Range: bytes=0-`, дочитывают до заполнения буфера и замолкают, НЕ закрывая
# соединение. Превью открывает камеры, их дублёры, музыку, SFX и видеовставки —
# шесть таких элементов съедали все соединения, и обычный fetch (/api/style_schema)
# ждал 5.3 с вместо 0.3 с: «сервер встаёт при открытии превью» (задание MB).
# Поэтому открытый (или заведомо длинный) диапазон режем до MEDIA_CHUNK: браузер
# получает конец диапазона, дочитывает остаток сам и отпускает соединение.
MEDIA_CHUNK = 4 * 1024 * 1024

# Range на ОДИН диапазон: `bytes=S-` или `bytes=S-E`. Пробелы и регистр по RFC
# не значимы. Суффиксный (`bytes=-N`), список диапазонов и мусор сюда не попадают.
_RANGE_ONE_RE = re.compile(r"^\s*bytes\s*=\s*(\d+)\s*-\s*(\d*)\s*$", re.IGNORECASE)


def _narrow_media_range(header, size):
    """Сузить открытый/слишком длинный Range до MEDIA_CHUNK байт (задание MB).

    Возвращает новый заголовок Range либо None — «трогать нечего»: суффиксный
    диапазон, несколько диапазонов, битый заголовок, файл не больше куска,
    заявленный диапазон и так короче куска, начало за концом файла (там 416
    отдаёт сам werkzeug, как и раньше)."""
    m = _RANGE_ONE_RE.match(header or "")
    if not m:
        return None
    if not size or size <= MEDIA_CHUNK:
        return None
    start = int(m.group(1))
    if start >= size:
        return None
    end_raw = m.group(2)
    if end_raw and int(end_raw) - start + 1 <= MEDIA_CHUNK:
        return None
    return f"bytes={start}-{min(start + MEDIA_CHUNK, size) - 1}"


@bp.route("/api/media")
def api_media():
    """Serve a local media file with HTTP Range support so the browser <video> in
    the AI-cut preview can seek/stream. Local app — only serves existing files."""
    path = (request.args.get("path") or "").strip().strip('"')
    if not path:
        return ("not found", 404)
    # Порядок проверок — расширение, секрет, существование (задание IC, п. 8).
    # Раньше `isfile` стоял ПЕРВЫМ и отвечал 404/403 в зависимости от того, есть ли
    # файл на диске: посторонний клиент узнавал про существование любого файла, а
    # `_never_serve` для СВОИХ имён (`ai_config.json` без расширения из allowlist)
    # был недостижим — до него просто не доходили.
    # Расширение должно быть допустимым и у присланного пути, и у realpath (задание LB):
    # иначе симлинк clip.mp4 -> notes.txt позволяет читать немедийные файлы.
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext not in ALLOWED_MEDIA_EXTS:
        return ("forbidden", 403)
    try:
        real_ext = os.path.splitext(os.path.realpath(path))[1].lower().lstrip(".")
    except Exception:
        real_ext = ""
    if real_ext not in ALLOWED_MEDIA_EXTS:
        return ("forbidden", 403)
    if _never_serve(path):
        return ("forbidden", 403)
    # «без фона» (задание ZI): предпросмотр фото-вставки просит nobg=1 — отдаём тот же
    # кэш, что уедет в сборку (insertlib.nobg_path), а не исходник с фоном. Только картинки:
    # видео не трогаем. Фон снять не удалось — nobg_path вернёт исходный путь.
    if (request.args.get("nobg") or "").strip() not in ("", "0"):
        from core.insertlib import IMG_EXT, nobg_path
        if ("." + ext) in IMG_EXT:
            try:
                path = nobg_path(path)
            except Exception:
                pass                                     # нет rembg/модели — отдаём исходник
    if not os.path.isfile(path):
        return ("not found", 404)
    if request.args.get("dl"):
        return send_file(path, as_attachment=True, download_name=os.path.basename(path))
    # Сужение — только для отдачи на месте и ПОСЛЕ проверок безопасности (расширение,
    # realpath, _never_serve): подменяем заголовок в environ, чтобы 206 / Content-Range /
    # Content-Length посчитал сам werkzeug. dl=1 отдаёт файл целиком, как раньше.
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0                    # файла уже нет — сужать нечего
    narrowed = _narrow_media_range(request.environ.get("HTTP_RANGE"), size)
    if narrowed:
        request.environ["HTTP_RANGE"] = narrowed
    return send_file(path, conditional=True)


@bp.route("/api/music_random", methods=["POST"])
def api_music_random():
    """Случайный аудиофайл из папки музыки — ТОТ ЖЕ выбор, что на сборке
    (ytmusic.random_track в xml2ae/build.py). Превью так слушает ползунок «Музыка»
    в режиме «случайно»; сборка всё равно выберет трек заново, уровень тот же."""
    d = request.get_json(silent=True) or {}
    dir_ = jstr(d, "dir")
    seed = d.get("seed")
    try:
        from core import ytmusic
        return jsonify(path=ytmusic.random_track(dir_, seed=seed) or "")
    except Exception as e:
        return jsonify(path="", **umsg_err(SystemExit(umsg("music_random_failed", f"{type(e).__name__}: {e}"))))


@bp.route("/api/waveform")
def api_waveform():
    """Пики амплитуды исходника (для рисования волны на блоках). Кэш рядом с файлом."""
    path = (request.args.get("path") or "").strip().strip('"')
    try:
        pps = int(request.args.get("pps") or 80)
    except (TypeError, ValueError):
        pps = 80                     # ?pps=abc роняло роут в HTML-500 (задание HL)
    pps = max(10, min(1000, pps))    # ограничение [10, 1000] от раздувания кэша (задание HU)
    # Те же проверки, что у /api/media (задание MC). Это был единственный файловый
    # маршрут без них, а он не только читает файл, но и пишет кэш РЯДОМ с ним —
    # то есть по просьбе страницы создавал файл рядом с любым файлом на диске.
    # Расширение должно быть допустимым и у присланного пути, и у realpath: иначе
    # симлинк clip.wav -> notes.txt обходит allowlist.
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext not in ALLOWED_WAVE_EXTS:
        return ("forbidden", 403)
    try:
        real_ext = os.path.splitext(os.path.realpath(path))[1].lower().lstrip(".")
    except Exception:
        real_ext = ""
    if real_ext not in ALLOWED_WAVE_EXTS:
        return ("forbidden", 403)
    if _never_serve(path):
        return ("forbidden", 403)
    if not os.path.isfile(path):
        return jsonify(**umsg_err(SystemExit(umsg("no_file", "нет файла"))))
    cache = path + f".peaks{pps}.json"
    if os.path.isfile(cache):
        try:
            return jsonify(json.load(open(cache, encoding="utf-8")))
        except Exception:
            pass
    try:
        import numpy as np, librosa
        y, sr = librosa.load(path, sr=16000, mono=True)
        step = max(1, sr // pps)
        peaks = [round(float(np.abs(y[i:i+step]).max()), 3) for i in range(0, len(y), step)]
        res = {"ok": True, "dur": round(len(y)/sr, 3), "pps": pps, "peaks": peaks}
        try:
            json.dump(res, open(cache, "w", encoding="utf-8"))
        except Exception:
            pass
        return jsonify(res)
    except Exception as e:
        return jsonify(**umsg_err(SystemExit(umsg("waveform_failed", f"{type(e).__name__}: {e}"))))


@bp.route("/api/clip_delete", methods=["POST"])
def api_clip_delete():
    """Удалить нарезку клипа целиком: XML и все его сайдкары, НЕ трогая исходное видео.

    dry: true — только проверка и список файлов на удаление без реального удаления.
    """
    d = request.get_json(silent=True) or {}
    xml = jstr(d, "xml").strip().strip('"')
    if not xml:
        return jsonify(**umsg_err(SystemExit(umsg("no_xml", "не указан путь к XML"))))

    xml_dir = os.path.dirname(os.path.abspath(xml))
    xml_name = _basename(xml)
    stem = os.path.splitext(xml_name)[0]
    if not stem or not xml_dir:
        return jsonify(**umsg_err(SystemExit(umsg("bad_xml_path", f"Некорректный путь к XML: {xml}", path=xml))))
    if not os.path.isdir(xml_dir):
        return jsonify(**umsg_err(SystemExit(umsg("no_folder", f"Нет папки: {xml_dir}", path=xml_dir))))

    jsxdir = jstr(d, "jsxdir").strip().strip('"')
    dry = True if d.get("dry") is True or str(d.get("dry")).lower() in ("true", "1") else False

    # Читаем project.json или сам XML, чтобы узнать исходные камеры
    xml_path = os.path.join(xml_dir, xml_name)
    cams = _clip_cams(xml_path)
    cam_basenames = set()
    cam_realpaths = set()
    for c in cams:
        if c and str(c).strip():
            c_str = str(c).strip().strip('"')
            cam_basenames.add(_basename(c_str).lower())
            try:
                cam_realpaths.add(os.path.realpath(c_str).lower())
            except Exception:
                pass

    prefix = stem.lower() + "."
    files_to_delete = []
    skipped = []
    seen_paths = set()

    # 1. Сканируем папку XML (только файлы первого уровня, без рекурсии)
    try:
        entries = sorted(os.listdir(xml_dir))
    except Exception as e:
        return jsonify(**umsg_err(SystemExit(umsg("list_dir_failed", f"Не удалось прочитать папку: {e}", err=str(e)))))

    for fname in entries:
        if not fname.lower().startswith(prefix):
            continue
        fpath = os.path.join(xml_dir, fname)
        rp = os.path.realpath(fpath).lower()
        base = _basename(fname).lower()

        if os.path.isdir(fpath):
            skipped.append({"path": fpath, "why": "каталог"})
            continue
        if not os.path.isfile(fpath):
            continue
        if _never_serve(fpath):
            skipped.append({"path": fpath, "why": "защищённый системный файл"})
            continue
        if base in cam_basenames or rp in cam_realpaths:
            skipped.append({"path": fpath, "why": "исходник камеры"})
            continue

        seen_paths.add(rp)
        try:
            sz = os.path.getsize(fpath)
        except OSError:
            sz = 0
        files_to_delete.append({"path": fpath, "size": sz})

    # 2. Проверяем <stem>.jsx в jsxdir, если папка задана и отличается
    if jsxdir and os.path.isdir(jsxdir):
        try:
            same_dir = os.path.realpath(jsxdir).lower() == os.path.realpath(xml_dir).lower()
        except Exception:
            same_dir = False
        if not same_dir:
            jsx_file = os.path.join(jsxdir, stem + ".jsx")
            if os.path.exists(jsx_file):
                rp = os.path.realpath(jsx_file).lower()
                base = _basename(jsx_file).lower()
                if rp not in seen_paths:
                    if os.path.isdir(jsx_file):
                        skipped.append({"path": jsx_file, "why": "каталог"})
                    elif not os.path.isfile(jsx_file):
                        pass
                    elif _never_serve(jsx_file):
                        skipped.append({"path": jsx_file, "why": "защищённый системный файл"})
                    elif base in cam_basenames or rp in cam_realpaths:
                        skipped.append({"path": jsx_file, "why": "исходник камеры"})
                    else:
                        seen_paths.add(rp)
                        try:
                            sz = os.path.getsize(jsx_file)
                        except OSError:
                            sz = 0
                        files_to_delete.append({"path": jsx_file, "size": sz})

    total_bytes = sum(f["size"] for f in files_to_delete)

    if not dry:
        deleted = []
        for item in files_to_delete:
            p = item["path"]
            try:
                if os.path.isfile(p):
                    os.remove(p)
                deleted.append(item)
            except Exception as e:
                skipped.append({"path": p, "why": f"ошибка удаления: {e}"})
        files_to_delete = deleted
        total_bytes = sum(f["size"] for f in files_to_delete)

    seen_cams = set()
    cam_names = []
    for c in cams:
        if c and str(c).strip():
            b = _basename(c)
            if b.lower() not in seen_cams:
                seen_cams.add(b.lower())
                cam_names.append(b)
    return jsonify(ok=True, files=files_to_delete, bytes=total_bytes, skipped=skipped, cams=cam_names)

