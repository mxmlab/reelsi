# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Черновой mp4 по виртуальному EDL финального XML (этап 2 «уроки video-use»).

Быстрый низкоразрешённый рендер смонтированного результата — «посмотреть глазами
без Premiere/AE»: trim+concat сегментов камер + звук с камеры 1 (с микро-фейдами
10мс на стыках) + слова-субтитры (.ass). Звук берётся из `<стем>.voice.wav` рядом с
XML, если он есть (обработанный голос, core/voicefx), — тайминг у файла тот же.
Кодек: аппаратный H.264 по настройке «Видеокодек» (⚙ → Инструменты), по умолчанию —
авто по железу (core.encoders); при недоступности / занятой VRAM — авто-фолбэк на
libx264 (CPU). Настройка читается при КАЖДОЙ сборке: сменил кодек — следующий черновик
и следующий прокси идут другим кодеком и другим декодом, без перезапуска сервера.

Через карту идёт всё, что реально можно, но ТОЛЬКО когда выбрано семейство NVIDIA:
720p-прокси камер собираются NVDEC-декодом и scale_cuda, итоговый черновик пишет NVENC.
При «Процессор» и «Intel (Quick Sync)» декод идёт процессором (у Intel — без
`-hwaccel qsv`: фильтр `crop` рамки кадра на кадрах QSV не работает, см. `_decode_tries`),
аппаратным остаётся только энкод. Прокси кэшируются в _tmp и переиспользуются
всеми следующими черновиками проекта — без них каждый черновик заново гонит через
декодер ВЕСЬ 4K-исходник (trim стоит после декодера), а черновик пересобирается на
каждой итерации самопроверки.

Временные файлы (прокси, filter-скрипт, .ass) — в <outdir>/_tmp/, итог — <stem>.draft.mp4
рядом с XML.

CLI:  python reelsi/draftrender.py "C:/.../01_C1295.xml" [--cpu] [--height 720] [--no-proxy]
"""
import os, re, subprocess, threading
# platform — РАДИ ТЕСТОВ: они подменяют `draftrender.platform.system`, проверяя порядок
# семейств на Mac/AMD (tests/test_encoder_choice.py). Выбор семейства живёт в
# core.encoders и смотрит на ТОТ ЖЕ модуль platform — подмена доходит и туда.
import platform                                             # noqa: F401
from typing import Any, Callable, Sequence
from core import encoders, frame, media
from core.app_meta import console_emit, wrap_emit
from core.gpulock import codec_gpu_lock
from core.umsg import ReelsiError, cli_error


# Рендер длинного ролика идёт минуты, сборка прокси — десятки секунд. Но ffmpeg
# умеет и висеть вечно (драйвер, битый файл, недодиск), а внутрипроцессный вызов
# без таймаута держал бы JOB навсегда: «Стоп» не имеет хэндла, лечится только
# рестартом сервера. Таймаут щедрый — это не лимит, а страховка от вечного висяка.
FFMPEG_TIMEOUT = 3600


class RenderCancelled(Exception):
    """Отменено пользователем — отдельное исключение, чтобы фронт показывал
    «прервано», а не «ОШИБКА»."""


def _run_ff(cmd: list[str], cancel: Callable[[], bool] | None = None, cwd: str | None = None, timeout: float = FFMPEG_TIMEOUT, on_progress: Callable[[str], None] | None = None) -> subprocess.CompletedProcess[str] | None:
    """subprocess.run для ffmpeg: с таймаутом И отменой.

    Возвращает CompletedProcess; None — отменено по `cancel()`; TimeoutExpired —
    процесс убит и выброшен (вызывающий решает, как донести «завис»). Процесс
    добивается в обоих случаях: без этого он сиротеет и держит входные файлы.

    `on_progress` — необязательный приём НАКОПЛЕННОГО stdout: ffmpeg с
    `-progress pipe:1` печатает туда ход сборки. С ним stdout читает отдельный поток
    построчно (копилка под замком) и зовёт приём на каждую строку; stderr читает
    второй поток — затем же, зачем его читал `communicate`: полный пайп останавливает
    сам ffmpeg. На таймаут, отмену и перебор заходов не влияет. Без `on_progress`
    путь прежний — `communicate`: разбирать нечего, и он не плодит потоки.

    Почему не `communicate(timeout=1.0)`, как было: на Windows CPython ветка Windows
    `Popen._communicate` при таймауте кладёт в `TimeoutExpired` output=None (на POSIX —
    уже прочитанное), и с боевым ffmpeg прогресс не доходил до приёма НИКОГДА. Тесты
    этого не видели: в них ffmpeg подменён, а подменённый отдаёт вывод как ему удобно."""
    import time as _t
    if cancel is not None and cancel():
        return None
    if on_progress is None:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding="utf-8", errors="replace", cwd=cwd)
        out, err = [], []
        t0 = _t.time()
        while True:
            try:
                o, e = p.communicate(timeout=1.0)
                out.append(o); err.append(e)
                return subprocess.CompletedProcess(cmd, p.returncode,
                                                   "".join(out), "".join(err))
            except subprocess.TimeoutExpired:
                if cancel is not None and cancel():
                    p.kill(); p.communicate()
                    return None
                if _t.time() - t0 > timeout:
                    p.kill(); p.communicate()
                    raise

    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, encoding="utf-8", errors="replace", cwd=cwd)
    out, err = [], []                     # накопленное: кладут читатели, отдают — тоже они
    lock = threading.Lock()
    broken = []                           # слом приёма прогресса: молча не глотаем

    def _reader(stream: Any, sink: list[str], report: bool) -> None:
        try:
            for line in stream:           # до EOF: иначе ffmpeg встанет на записи в пайп
                with lock:
                    sink.append(line)
                    text = "".join(sink) if report else None
                if text is not None:
                    on_progress(text)     # накопленным — как отдавал прежний communicate
        except ReelsiError: raise
        except Exception as ex:
            broken.append(ex)             # процесс добьёт основной цикл

    readers = [threading.Thread(target=_reader, args=(p.stdout, out, True), daemon=True),
               threading.Thread(target=_reader, args=(p.stderr, err, False), daemon=True)]
    for th in readers:
        th.start()
    cancelled = False
    t0 = _t.time()
    while True:
        if p.poll() is not None:
            break
        if broken:
            p.kill(); p.wait()
            break
        if cancel is not None and cancel():
            cancelled = True
            p.kill(); p.wait()
            break
        if _t.time() - t0 > timeout:
            p.kill(); p.wait()
            for th in readers:
                th.join()
            raise subprocess.TimeoutExpired(cmd, timeout)
        _t.sleep(0.1)                     # опрос отмены/таймаута: вывод читают потоки
    for th in readers:
        th.join()                         # хвост пайпа и строка без последнего \n
    with lock:
        if broken:
            raise broken[0]
        if cancelled:
            return None
        return subprocess.CompletedProcess(cmd, p.returncode,
                                           "".join(out), "".join(err))

FADE = 0.010          # сек, микро-фейд звука на краях сегментов (как в .jsx)
DRAFT_FPS = 30        # черновик — 30fps, хватает для оценки монтажа


# --------------------------------------------------------------------------- #
# Аппаратный кодировщик: какой брать на этом железе
# --------------------------------------------------------------------------- #
# Знание о кодеках переехало в общий core.encoders (он же обслуживает прожиг LUT в
# видео камер и настройку «Видеокодек» в ⚙). Здесь остались имена, которые зовут
# код и тесты: таблица кандидатов, таблица аргументов, проба и кэш ПРОБ.
#
# Декод на карте (-hwaccel + scale_cuda) оставлен только для NVIDIA: он там проверен
# годом работы. Остальным отдаём CPU-декод и аппаратный только ЭНКОД — это уже даёт
# основной выигрыш, а рисковать зависанием на чужом железе, которого у нас нет, незачем.
#
# _CANDIDATES — ПРОИЗВОДНАЯ от encoders.OS_ORDER: второй копии порядка быть не должно,
# разъехались бы молча. Имена аппаратных кандидатов без cpu: «нет аппаратного» в
# draftrender — это None, за который отвечает libx264 (см. hw_encoder).
_CANDIDATES = {os_name: [encoders.DRAFT_CODECS[f] for f in fams if f != "cpu"]
               for os_name, fams in encoders.OS_ORDER.items()}
# Аргументы кодеков черновика: у nvenc свой квантователь, остальным битрейт (флаги
# качества у amf/qsv/videotoolbox разъезжаются от версии ffmpeg к версии).
_ENC_ARGS = encoders.DRAFT_ARGS

# Кэш ПРОБ (имя кодека -> работает ли он на этой машине), не кэш выбора.
#
# Кэшировать итоговый выбор нельзя: раньше он лежал здесь и держал первый ответ до
# конца процесса сервера. Первый черновик после старта запоминал NVENC — и смена
# «Видеокодека» на «Intel Quick Sync» или «Процессор» не меняла НИЧЕГО: ни кодека,
# ни декода (`-hwaccel cuda` включается от выбранного семейства), пока сервер не
# перезапустят. Итог выбирается при КАЖДОМ вызове (encoders.pick читает текущую
# настройку), а помнится только то, что и правда стоит запуска ffmpeg, — работает
# ли семейство.
_PROBE_CACHE: dict[str, bool] = {}


def reset_cache() -> None:
    """Забыть пробы кодировщиков — их зовут при смене настройки «Видеокодек».

    Сами пробы от настройки не зависят (работает ли семейство на этой машине), но
    именно смена настройки — тот момент, когда пользователь ждёт другого кодека
    прямо сейчас: пробуем заново, а не отвечаем «как было при старте».
    """
    _PROBE_CACHE.clear()


def probe_family(family: str, prober: Callable[[str], bool] | None = None) -> bool:
    """Работает ли СЕМЕЙСТВО кодировщиков — с кэшем на процесс.

    `prober` — своя проба вызывающего (её подменяют тесты): получает ИМЯ кодека
    (`h264_nvenc`), а не семейство. Кэш — по имени кодека, поэтому «авто» и явный
    выбор одного и того же семейства не пробуют его дважды.
    """
    codec = encoders.codec_name(family, "draft")
    hit = _PROBE_CACHE.get(codec)
    if hit is not None:
        return hit
    ok = (prober or probe_encoder)(codec)
    _PROBE_CACHE[codec] = ok
    return ok


def probe_encoder(name: Any) -> bool:
    """Кодировщик реально работает ПРЯМО СЕЙЧАС? (драйвер, VRAM, лимит сессий)

    Проба общая (core.encoders), назначение — черновик: кадр 256x256, H.264 8 бит.
    Отвечает СВЕЖИМ результатом мимо кэша: сюда приходят ПОСЛЕ падения ffmpeg, чтобы
    понять, виноват кодировщик или вход, — закэшированное «работает» тут соврало бы.

    Имя оставлено ради тестов и лога: они подменяют и зовут именно его.
    """
    codec = str(name)
    return encoders.probe(encoders.family_of(codec) or codec, "draft", refresh=True)


def hw_encoder(refresh: bool = False) -> str | None:
    """Имя рабочего аппаратного H.264-кодировщика или None.

    Кодек выбирает общий core.encoders: он знает настройку «Видеокодек» (auto или
    семейство) и порядок семейств по ОС, и спрашивается ПРИ КАЖДОМ вызове — сменил
    настройку, и следующий вызов идёт другим кодеком, без перезапуска сервера.
    Помнится только проба (`probe_family`): её подменяют тесты, и она же отвечает
    «работает ли это прямо сейчас» после падения ffmpeg.

    `refresh=True` — «перепроверить»: карту могли занять или освободить (драйвер,
    VRAM), поэтому пробы забываются и идут заново.

    None означает «аппаратного нет» — вызывающие уходят на libx264 (он же выбор «cpu»)."""
    if refresh:
        reset_cache()                   # драйвер могли занять или освободить — спрашиваем заново
        encoders.reset_cache()
    choice = encoders.pick("draft", prober=probe_family)
    if choice.family == "cpu":
        return None
    return choice.args[1]               # аргументы начинаются с ["-c:v", имя, ...]


def _codec_args(name: str, q: int, br: str) -> list[str]:
    """Аргументы кодека по имени; неизвестному — битрейт (безопасный минимум)."""
    return encoders.codec_args(name, "draft", q=q, br=br)


def _codec_name(args: Sequence[Any] | None) -> str:
    """Имя кодека из аргументов ffmpeg (`-c:v <имя>`) или пусто.

    По имени решается, занимать ли видеокарту (`core.gpulock.codec_gpu_lock`): NVENC
    карту занимает, Quick Sync/AMF/x264 — нет. Пустые аргументы (аппаратного захода
    нет) — пустое имя: замок такому участку не нужен."""
    a = [str(x) for x in (args or [])]
    return a[a.index("-c:v") + 1] if "-c:v" in a else ""


def _codec_label(codec: Sequence[str], hw: str | None) -> str:
    """Кодек захода для лога: аппаратное имя или «libx264 (CPU)».

    Заход с именем аппаратного кодека — это аппаратный и есть, а x264 приходит и
    как CPU-фолбэк, и как выбор «Процессор»; в логе оба случая читаются одинаково.
    """
    name = _codec_name(codec)
    if not name:
        name = hw or "libx264"
    return name if name != "libx264" else "libx264 (CPU)"


def _decode_label(inp: Sequence[str]) -> str:
    """Декод захода для лога — по входным аргументам ffmpeg (источник правды).

    `-hwaccel cuda` — декод на карте NVIDIA (NVDEC), `qsv` — Quick Sync. Пусто —
    декод процессором: аппаратным остаётся только энкод (у Intel/AMD/Apple так и
    заведено нарочно, см. `_decode_tries`).
    """
    accel = str(inp[inp.index("-hwaccel") + 1]) if "-hwaccel" in inp else ""
    if not accel:
        return "процессор"
    return "NVDEC (CUDA)" if accel == "cuda" else accel + " (аппаратный)"


def tmp_dir(out_xml_or_dir: str) -> str:
    """<outdir>/_tmp — единая папка временных артефактов (черновики-скрипты, ass,
    склейки self-check). НЕ %TEMP%: рядом с проектом, чистится кнопкой/новой нарезкой."""
    d = out_xml_or_dir if os.path.isdir(out_xml_or_dir) else os.path.dirname(out_xml_or_dir)
    t = os.path.join(d, "_tmp")
    os.makedirs(t, exist_ok=True)
    return t


PROXY_GLOB = "pv_*.mp4"          # превью-прокси камер (build_preview_proxy)
# В _tmp живут два независимых кэша прокси:
# 1) `pv_*.mp4` — прокси кадра для плеера веба (build_preview_proxy): у превью длинный
#    GOP ради размера файла. Разновидность с ключевым КАЖДЫЙ кадр (`pv_r*.mp4`,
#    build_render_proxy) рендер без AE больше НЕ заказывает: кадры камер он вынимает
#    из исходников (core/webrender), а не играет прокси. Сборщик оставлен — им жил
#    рендер, и он же собирает прокси перехода по общей двери /api/preview_proxy;
# 2) `proxy_*.mp4` — 720p-прокси камер для быстрого рендера черновика (_proxy_path).
# У них разные имена/префиксы, но одинаковая суть: пересборка декода камеры стоит десятки
# секунд. Поэтому рутинная авто-очистка перед нарезкой (proxies=False) бережёт оба вида,
# а явная очистка места кнопкой (proxies=True) удаляет и считает оба.
PROXY_GLOBS = ("pv_*.mp4", "proxy_*.mp4")
# Имя файла субтитров черновика — ФИКСИРОВАННОЕ. В фильтрграфе имя идёт сырым
# (`subtitles=<имя>`), а фильтр разбирается по запятым, `[ ]`, `;`, `:` и кавычкам:
# стем ролика вида «C1,2[1];x'y» рвал `subtitles`, и черновик не собирался вовсе
# (GZ, п. I; перепроверено на ffmpeg 8.0). Файл живёт в _tmp своего ролика.
DRAFT_SUBS_NAME = "draft_subs.ass"


def proxy_size(outdir: str) -> int:
    """Сколько занимают прокси (превью и черновика) в <outdir>/_tmp, байт. Нужно, чтобы кнопка
    очистки показывала цену вопроса: удалил — следующее открытие предпросмотра/черновика ждёт пересборку."""
    import glob
    t = os.path.join(outdir, "_tmp")
    seen = set()
    total = 0
    for pat in PROXY_GLOBS:
        for f in glob.glob(os.path.join(t, pat)):
            p = os.path.abspath(f)
            if p in seen:
                continue
            seen.add(p)
            try:
                total += os.path.getsize(p)
            except OSError:
                pass  # файл исчез между обходом и замером — в объём не попадёт
    return total


def clean_tmp(outdir: str, emit: Any = console_emit, proxies: bool = False) -> int:
    """Очистить <outdir>/_tmp. Возвращает освобождённые байты.

    proxies=False (по умолчанию) — прокси (превью pv_*.mp4 и черновика proxy_*.mp4) НЕ трогаем.
    Они не мусор, а кэш по файлу камеры: пересборка стоит десятки секунд на файл, а зависят они от
    исходника, не от монтажа. Авто-очистка перед новой нарезкой ходит именно так — иначе каждая
    нарезка в этой папке обнуляла бы прокси всем клипам разом.
    proxies=True — явная уборка кнопкой, когда место нужно прямо сейчас (сносит и прокси)."""
    emit = wrap_emit(emit)
    import glob
    import shutil
    t = os.path.join(outdir, "_tmp")
    freed = 0
    if not os.path.isdir(t):
        return 0
    keep: set[str] = set()
    if not proxies:
        for pat in PROXY_GLOBS:
            keep.update(os.path.abspath(p) for p in glob.glob(os.path.join(t, pat)))
    for root, _dirs, files in os.walk(t):
        for f in files:
            p = os.path.join(root, f)
            if os.path.abspath(p) in keep:
                continue
            try:
                freed += os.path.getsize(p)
            except OSError:
                pass  # файл исчез между обходом и замером — в объём не попадёт
    if not keep:
        shutil.rmtree(t, ignore_errors=True)
    else:
        for root, dirs, files in os.walk(t, topdown=False):
            for f in files:
                p = os.path.join(root, f)
                if os.path.abspath(p) not in keep:
                    try:
                        os.remove(p)
                    except OSError:
                        pass  # файл уже удалён или занят другим процессом
            for dname in dirs:                       # пустые подпапки убираем, _tmp оставляем
                try:
                    os.rmdir(os.path.join(root, dname))
                except OSError:
                    pass  # каталог непустой или занят — оставляем как есть
    if proxies:
        emit("  _tmp очищен: {freed:.0f} МБ", freed=freed / 1e6)
    else:
        emit("  _tmp очищен: {freed:.0f} МБ (прокси оставлены)", freed=freed / 1e6)
    return freed


def _ass_time(t: float) -> str:
    h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _ass_subs(words: Sequence[dict[str, Any]], tw: int, th: int, path: str) -> str:
    """Слова -> простой .ass: по одному слову, крупно, ~40%% от низа (как в проекте)."""
    fs = max(24, int(tw * 0.13))
    margin_v = int(th * 0.40)                     # низ слова на ~40% от низа кадра
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {tw}\nPlayResY: {th}\nWrapStyle: 2\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, "
        "Bold, Outline, Shadow, Alignment, MarginL, MarginR, MarginV\n"
        f"Style: W,Arial,{fs},&H00FFFFFF,&H00000000,&H80000000,-1,2,1,2,10,10,{margin_v}\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Text\n")
    lines = []
    for w in words:
        txt = str(w["w"]).replace("\\", "").replace("{", "(").replace("}", ")")
        if w["e"] > w["s"] and txt:
            lines.append(f"Dialogue: 0,{_ass_time(w['s'])},{_ass_time(w['e'])},W,{txt}")
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(head + "\n".join(lines) + "\n")
    return path


def _proxy_path(src: str, tw: int, th: int, tdir: str, crop: str = "") -> str:
    """Имя прокси-файла камеры в _tmp. В ключ входят mtime/size исходника и размер
    кадра: переснял/перекодировал исходник или сменил height — прокси пересоберётся.
    `crop` — обрезка рамки кадра (core/frame.py): рамку правят в превью, и прокси,
    собранный по прежней, обязан пересобраться, а не показывать старый кадр."""
    import hashlib
    st = os.stat(src)
    key = (f"{os.path.abspath(src)}|{int(st.st_mtime)}|{st.st_size}|{tw}x{th}@{DRAFT_FPS}"
           + _rot_key(src) + ("|" + crop if crop else ""))
    return os.path.join(tdir, "proxy_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12] + ".mp4")


def _crop_filter(fr: Any, src: str, fw: int, fh: int) -> str:
    """Обрезка исходника по рамке камеры для ffmpeg: "crop=W:H:X:Y," или "".

    Рамки нет (или размеры исходника не прочитались) — пусто: фильтр-цепочка
    остаётся прежней, байт в байт. Стороны и позицию округляем до чётных: yuv420p
    не переносит нечётные. Размер кадра ролика (fw, fh) — тот же, по которому
    считает core/frame.py: кусок рамки всегда с пропорциями кадра.
    """
    if frame.is_frame_default(fr):
        return ""
    w, h, _rot = _display_dims(src)
    if not w or not h:
        return ""
    x, y, cw, ch = frame.frame_crop(fr, w, h, fw, fh)
    cw, ch = int(cw) // 2 * 2, int(ch) // 2 * 2
    if cw < 2 or ch < 2:
        return ""
    return "crop=%d:%d:%d:%d," % (cw, ch, int(x) // 2 * 2, int(y) // 2 * 2)


def _build_proxy(src: str, dst: str, tw: int, th: int, force_cpu: bool = False, emit: Any = console_emit,
                 cancel: Callable[[], bool] | None = None, crop: str = "",
                 allintra: bool = False) -> str | None:
    """Собрать 720p-прокси камеры: декод и масштаб на GPU (NVDEC + scale_cuda), кодек
    NVENC. Возвращает путь или None (тогда работаем по исходнику, как раньше).

    Зачем: `trim` стоит ПОСЛЕ декодера, поэтому каждый черновик прогоняет весь 4K-исходник
    целиком — а черновик пересобирается на каждой итерации самопроверки и после каждой
    правки. С прокси тяжёлый декод платится один раз, дальше сборка идёт по крошечному
    720p-файлу (замер: 3.8 с -> 0.8 с при 60 сегментах, на реальном 4K разрыв больше).

    `crop` — обрезка рамки кадра (пиксели ИСХОДНИКА): она вшивается в прокси, потому
    что кадр камеры дальше масштабируется целиком. С обрезкой GPU-заход не годится:
    `crop` — фильтр CPU, а на кадрах CUDA он не работает, поэтому идём CPU-декодом.

    `allintra` — ключевой кадр КАЖДЫЙ (`-g 1`, только x264): прокси рендера без AE, где
    перемотка <video> на каждый кадр не должна декодировать от далёкого ключевого."""
    emit = wrap_emit(emit)
    vf_gpu = f"fps={DRAFT_FPS},{crop}scale_cuda={tw}:{th}:format=yuv420p"
    vf_cpu = f"fps={DRAFT_FPS},{crop}scale={tw}:{th},format=yuv420p"
    x264 = _x264_args(q=RENDER_PROXY_Q if allintra else 28, allintra=allintra)
    # all-intra — только CPU-заход: ключевой каждый кадр это режим x264, у аппаратных
    # кодировщиков он зовётся иначе (intra-refresh) и на части драйверов не заводится
    # вовсе, а нам тут важна не скорость сборки прокси, а гарантия формата.
    hw = None if (force_cpu or allintra) else hw_encoder()
    tries = _decode_tries(src, vf_gpu, vf_cpu, hw,
                          _display_dims(src)[2], _codec_args(hw, 30, "3M") if hw else None, x264,
                          gpu=not crop)
    tmp = dst + ".part.mp4"
    for inp, vf, codec in tries:
        if cancel is not None and cancel():
            return None
        try:
            # Замок видеокарты — только под NVENC-заходом: Quick Sync, AMF и процессор
            # карту NVIDIA не занимают, и ждать им чужое запекание голоса нечего.
            with codec_gpu_lock(_codec_name(codec), "прокси камер", emit):
                r = _run_ff(["ffmpeg", "-y", "-v", "error"] + inp +
                            ["-an", "-vf", vf] + codec + [tmp], cancel=cancel)
        except subprocess.TimeoutExpired:
            emit("  ⚠ прокси: ffmpeg завис ({timeout} с) — следующая попытка", timeout=FFMPEG_TIMEOUT)
            continue
        if r is None:
            return None
        if r.returncode == 0 and os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
            # os.replace, а не core.fileio: файл пишет САМ ffmpeg (внешний процесс), в
            # памяти его нет — от общей инфраструктуры тут только публикация готового
            # .part после проверки размера. Недописанный .part не подхватится.
            os.replace(tmp, dst)
            # Каким кодеком и каким декодом собран файл — по ФАКТУ захода, а не по
            # выбору: заходы идут от быстрого к надёжному, и победил именно этот.
            emit("  кодек {codec}, декод {dec}", codec=_codec_label(codec, hw),
                 dec=_decode_label(inp))
            return dst
    try:
        os.remove(tmp)
    except OSError:
        pass  # недописанный .part уже убран
    emit("  ⚠ прокси не собрался для {name} — работаю по исходнику", name=os.path.basename(src))
    return None


def _x264_args(q: int, allintra: bool = False) -> list[str]:
    """Аргументы x264 для прокси: постоянное качество, без B-кадров.

    `allintra` — `-g 1 -bf 0 -keyint_min 1`: каждый кадр ключевой. Одна строка на оба
    вида прокси, чтобы «какой GOP у рендера» не разъезжалось с ключом кэша (он в
    RENDER_PROXY_VERSION)."""
    args = ["-c:v", "libx264", "-crf", str(q), "-preset", "veryfast"]
    return args + (["-g", "1", "-bf", "0", "-keyint_min", "1"] if allintra else [])


def build_render_proxy(src: str, dst: str, height: int = 720, emit: Any = console_emit,
                       cancel: Callable[[], bool] | None = None,
                       crop: str = "") -> str | None:
    """Прокси камеры с ключевым КАЖДЫЙ кадр: кадр как у превью, перемотка — одно
    декодирование вместо прогона от ближайшего ключевого.

    Рендер без AE эту разновидность больше не заказывает: кадры камер он вынимает из
    ИСХОДНИКОВ (core/webrender), потому что прокси короткой стороны кадра ролика зум
    клипа растягивал. Дверь оставлена для того, кому нужен именно all-intra прокси.

    Звук не нужен (`-an` внутри _build_proxy): рендер без AE собирает видео, звук к нему
    клеит ffmpeg из voice.wav/камеры 1. Кадр — короткая сторона как у превью."""
    tw, th, _rot = _short_side(src, height)
    return _build_proxy(src, dst, tw=tw, th=th, emit=emit, cancel=cancel,
                        crop=crop, allintra=True)


def _decode_tries(src: str, vf_gpu: str, vf_cpu: str, hw: str | None, rot: bool, hwc: list[str] | None,
                  x264: list[str], gpu: bool = True) -> list[tuple[list[str], str, Any]]:
    """Заходы сборки прокси: [(входные аргументы, видеофильтр, аргументы кодека)].
    Порядок — от быстрого к надёжному, побеждает первый успешный.

    ДЕКОД — по выбранному семейству, а не «раз есть аппаратный энкод, значит карта»:
    `-hwaccel cuda` + `scale_cuda` возможны только там, где это проверено годом
    работы, то есть на NVIDIA. Выбрал «Процессор» — `hw` приходит None и остаётся
    единственный заход с CPU-декодом; выбрал «Intel (Quick Sync)» — энкод `h264_qsv`,
    а декод процессором, в том числе потому, что на аппаратных кадрах не работает
    фильтр `crop` (обрезка рамки кадра, core/frame.py): цепочка рвётся ровно так же,
    как на CUDA-кадрах. Аппаратный ЭНКОД при этом остаётся — основной выигрыш
    (кодирование 4K) не теряется; аппаратный ДЕКОД Quick Sync — отдельная работа
    (`-hwaccel qsv`), и заводить его вслепую на железе, которого у нас нет, незачем.

    `gpu=False` — заход с `-hwaccel_output_format cuda` ЗАПРЕЩЁН: на кадрах CUDA
    фильтр CPU (обрезка рамки кадра) не работает, и цепочка рвётся.

    ПОВЁРНУТЫЙ кадр через NVDEC не гоняем ВОВСЕ. С `-hwaccel_output_format cuda`
    автоповорот ffmpeg не применяется: `scale_cuda` получает ещё не развёрнутый кадр
    (3840x2160), жмёт ландшафт в портрет, а матрица поворота 90° уезжает в выход как
    есть — прокси выходит и искажённым, и лежащим на боку. Коварство в том, что заход
    при этом УСПЕШЕН (на 4:2:0 NVDEC работает), поэтому в кэш попадал именно битый файл,
    а на CPU-заходе тот же исходник давал верный кадр — результат зависел от того, какой
    заход сработал. На CPU-декоде автоповорот отрабатывает, кадр верный. Реальные файлы
    с этим: `камера1/C1387-008.MP4`, `Камера2/C1385-004.MP4` (3840x2160, rotation=90)."""
    if not hw:
        return [(["-i", src], vf_cpu, x264)]
    if hw == "h264_nvenc" and not rot and gpu:
        # 4:2:2 10 бит (Sony/Canon) NVDEC до Blackwell не умеет — следующий заход спасает
        return [(["-hwaccel", "cuda", "-hwaccel_output_format", "cuda", "-i", src], vf_gpu, hwc),
                (["-i", src], vf_cpu, hwc),
                (["-i", src], vf_cpu, x264)]
    # Intel/AMD/Apple и любой повёрнутый кадр: декод на CPU, аппаратный только ЭНКОД
    return [(["-i", src], vf_cpu, hwc), (["-i", src], vf_cpu, x264)]


_DIMS_CACHE: dict[tuple[str, int, int] | None, tuple[int, int, bool]] = {}          # (abspath, mtime, size) -> (w, h, rot): ffprobe на каждый вызов
                          # заметен — _preview_proxy_plan зовётся на каждое открытие
                          # предпросмотра и на каждый опрос прогресса сборки


def _display_dims(src: str) -> tuple[int, int, bool]:
    """(w, h, повёрнут ли) КАК ПОКАЗЫВАЕТСЯ, а не как закодировано.

    Телефоны и часть камер пишут вертикаль как 3840x2160 с матрицей поворота 90° —
    файлы Djagger именно такие. Считать по закодированному кадру нельзя: прокси вышел
    бы 1280x720, то есть вертикаль, размазанная в горизонталь."""
    try:
        st = os.stat(src)
        ck = (os.path.abspath(src), int(st.st_mtime), st.st_size)
    except OSError:
        ck = None
    if ck in _DIMS_CACHE:
        return _DIMS_CACHE[ck]
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height:stream_side_data=rotation",
                            "-of", "default=nw=1:nk=1", src],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        vals = [x for x in (r.stdout or "").strip().splitlines() if x.strip()]
        w0, h0 = int(vals[0]), int(vals[1])
        rot = abs(int(float(vals[2]))) % 180 if len(vals) > 2 else 0
    except ReelsiError: raise
    except Exception:
        return 1080, 1920, False                 # не прочли — НЕ кэшируем: файл мог ещё писаться
    out = (h0, w0, True) if rot == 90 else (w0, h0, False)
    if ck:
        _DIMS_CACHE[ck] = out
    return out


def _short_side(src: str, height: int) -> tuple[int, int, bool]:
    """(w, h, повёрнут ли): короткая сторона = height, стороны чётные."""
    w0, h0, rot = _display_dims(src)
    if w0 <= h0:
        return height, int(round(h0 * height / w0 / 2)) * 2, rot
    return int(round(w0 * height / h0 / 2)) * 2, height, rot


_FPS_CACHE: dict[tuple[str, int, int], float] = {}           # (abspath, mtime, size) -> fps: ffprobe на каждый прокси
                          # не гоняем — один вызов на сборку, дальше из кэша


def _src_fps(src: str) -> float:
    """Кадровая частота исходника (для размера GOP превью-прокси)."""
    try:
        st = os.stat(src)
        ck = (os.path.abspath(src), int(st.st_mtime), st.st_size)
        if ck in _FPS_CACHE:
            return _FPS_CACHE[ck]
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=r_frame_rate",
                            "-of", "default=nw=1:nk=1", src],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
        num, _, den = (r.stdout or "").strip().partition("/")
        fps = float(num) / float(den) if den else float(num or 0)
        fps = fps if 1 < fps < 240 else 25.0        # не прочли — не кэшируем
        _FPS_CACHE[ck] = fps
        return fps
    except ReelsiError: raise
    except Exception:
        return 25.0


def _src_dur(src: str) -> float:
    """Длительность исходника, сек. 0.0 — не прочли (тогда процента не будет).

    Проба общая (core/media.py): там кэш по (путь, mtime, размер) и таймаут —
    ffprobe иначе гонялся бы на каждый кадр прогресса сборки."""
    return media.probe_duration(src) or 0.0


def ff_progress_us(chunk: str | None) -> int | None:
    """Последний `out_time_us=` из потока ffmpeg `-progress pipe:1`. None — прогресса нет.

    ffmpeg печатает блок полей раз в 0.5 с, то есть за сборку их набираются сотни:
    интересен последний — он и есть текущее положение. `N/A` на первых кадрах —
    не ошибка, просто пропускаем строку."""
    us = None
    for line in (chunk or "").splitlines():
        line = line.strip()
        if not line.startswith("out_time_us="):
            continue
        try:
            us = int(line.split("=", 1)[1])
        except ValueError:
            continue
    return us


def ff_progress_pct(chunk: str | None, dur: float) -> float | None:
    """Проценты готовности ТЕКУЩЕГО файла (0–100) по потоку `-progress pipe:1`.

    None — длительность исходника неизвестна или прогресса ещё нет. Выше 100 не бывает:
    out_time_us — уже записанный материал, а он не длиннее исходника."""
    if not dur or dur <= 0:
        return None
    us = ff_progress_us(chunk)
    if us is None or us < 0:
        return None
    return max(0.0, min(100.0, us / (dur * 1e6) * 100.0))


def _progress_hook(progress: Callable[[float], None] | None, dur: float) -> Callable[[str], None] | None:
    """Приём для _run_ff: накопленный stdout → проценты файла (None — приём не нужен)."""
    if progress is None:
        return None

    def hook(text: str) -> None:
        pct = ff_progress_pct(text, dur)
        if pct is not None:
            progress(pct)
    return hook


def _rot_key(src: str) -> str:
    """Довесок к ключу кэша для ПОВЁРНУТЫХ исходников.

    Прокси, собранные до фикса автоповорота, лежат искажёнными и лежащими на боку, но
    имя у них было бы тем же — и переиспользовались бы молча. Довесок ставим ТОЛЬКО
    повёрнутым: у нормальных файлов ключ не меняется, и они зря не пересобираются."""
    try:
        return "|rot" if _display_dims(src)[2] else ""
    except ReelsiError: raise
    except Exception:
        return ""


def _proxy_key(name: str, key: str, tdir: str) -> str:
    """Путь прокси в `_tmp`: префикс вида, имя — хеш ключа.

    Хеш берётся от СТРОКИ ключа: у каждого вида прокси ключ свой, и разойтись они не
    могут — в имя попадает ровно то, от чего прокси зависит.
    """
    import hashlib
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    return os.path.join(tdir, name + h + ".mp4")


def _src_version(src: str) -> str:
    """Общая часть ключа кэша: файл камеры по (путь, mtime, размер) и его поворот.

    Переснял/перекодировал исходник — прокси пересобирается; поворот в ключе — потому
    что прокси, собранные до фикса автоповорота, лежат на боку, а имя у них прежнее.
    """
    st = os.stat(src)
    return f"{os.path.abspath(src)}|{int(st.st_mtime)}|{st.st_size}" + _rot_key(src)


def preview_path(src: str, height: int, tdir: str) -> str:
    """Имя превью-прокси камеры в _tmp. Ключ — как у черновикового (mtime/size/размер),
    но префикс свой: тот собран БЕЗ звука и с fps=30, для превью не годится.

    В ключе есть версия формата (`g`): прокси до фикса короткого GOP несли ключевые
    кадры раз в 10 секунд (дефолтный GOP NVENC/X.264 = 250 фреймов), и каждый seek
    в браузере заставлял декодер прогонять до 10 секунд с последнего ключевого кадра.
    Сменили формат — старые прокси обязаны пересобраться, иначе кэш раздавал бы битые."""
    return _proxy_key("pv_", f"{_src_version(src)}|prev{height}:g", tdir)


# Имя прокси КАДРА РЕНДЕРА без AE (`pv_r…`). Разбирается по имени, а не по числу в
# списке: сборщику прокси (api/previewproxy) план приходит путями, и «какой это прокси»
# решается ровно там, где имя собрано.
RENDER_PROXY_RE = re.compile(r"^pv_r[0-9a-f]{12}\.mp4$")
# Версия формата прокси рендера. Ключевой кадр КАЖДЫЙ: перемотка <video> стоит одного
# декодированного кадра вместо прогона с далёкого ключевого (у превью GOP ~1 с — ради
# размера файла, у рендера важна скорость перемотки, а размер он платит свой).
RENDER_PROXY_VERSION = "allintra1"
# Качество прокси рендера: q=18 не нужен (кадр 1:1 всё равно ужмётся кодеком
# назначения), но и 28-й мылит кадр. Меняешь число — меняй версию выше: ключ кэша
# состоит из неё, и старые прокси обязаны пересобраться, а не подхватиться молча.
RENDER_PROXY_Q = 23


def render_proxy_path(src: str, height: int, tdir: str) -> str:
    """Имя прокси камеры ДЛЯ РЕНДЕРА БЕЗ AE: тот же кадр, что у превью, но все кадры
    ключевые (см. RENDER_PROXY_VERSION) и БЕЗ звука.

    Отдельный файл, а не тот же самый: превью играет длинным GOP и со звуком камеры 1,
    и подмена формата сломала бы ему перемотку и звук. Ключ кэша отличается версией
    формата, поэтому оба файла живут рядом и не подхватывают друг друга.
    """
    return _proxy_key("pv_r", f"{_src_version(src)}|rend{height}:{RENDER_PROXY_VERSION}", tdir)


def build_preview_proxy(src: str, dst: str, height: int = 720, force_cpu: bool = False, emit: Any = console_emit,
                        cancel: Callable[[], bool] | None = None, progress: Callable[[float], None] | None = None) -> str | None:
    """Прокси камеры ДЛЯ ПРЕДПРОСМОТРА В БРАУЗЕРЕ. Путь или None.

    Зачем отдельно от чернового прокси — три отличия, каждое обязательное:
      * СО ЗВУКОМ: камера 1 в предпросмотре — источник звука, `-an` её обнуляет;
      * fps исходника, без `fps=30`: по этому же <video> ходит плейхед редактора,
        и пересэмплированные кадры сдвинули бы покадровый шаг;
      * гарантированный yuv420p 8 бит.
    Третье — вся суть. Материал 4:2:2 10 бит (Sony/Canon; у Djagger именно он) браузер
    НЕ берёт на аппаратный декодер: mediaCapabilities отвечает powerEfficient=false, и
    4K софтом декодируется на проце. Каждый seek на стыке — сотни мс, отсюда «встаёт в
    разрезе». Тот же материал в 720p 4:2:0 8 бит powerEfficient=true и сеcится десятками мс.

    Прокси — от ИСХОДНИКА, не от монтажа: собирается один раз на файл камеры и живёт в
    кэше. Правки нарезки его не трогают (в отличие от черновика, который надо
    пересобирать после каждой правки).

    `progress` — необязательный приём процентов (0–100) готовности ЭТОГО файла: ffmpeg
    идёт с `-progress pipe:1 -nostats`, а знаменатель — длительность исходника из
    ffprobe. Значения приходят раз в секунду и могут повторяться."""
    emit = wrap_emit(emit)
    # height — КОРОТКАЯ сторона (как в render_draft): вертикаль 2160x3840 -> 720x1280,
    # горизонталь 3840x2160 -> 1280x720. Фиксированное «-2» по одной оси уронило бы
    # горизонтальный материал до 720x405.
    tw, th, rot = _short_side(src, height)
    vf_gpu = f"scale_cuda={tw}:{th}:format=yuv420p"
    vf_cpu = f"scale={tw}:{th},format=yuv420p"
    aac = ["-c:a", "aac", "-b:a", "128k"]
    # Короткий GOP: ключевой кадр раз в ~1 сек. Дефолтный GOP NVENC/X.264 = 250
    # фреймов — при 25 fps ключевые кадры раз в 10 секунд, и каждый seek в браузере
    # гоняет декодер от ближайшего ключевого кадра (до 10 сек видео) вперёд до цели.
    # Это и была тому ответственность за то, что передпросмотр «заикается» при
    # перемотке/стыках даже на готовых прокси. Краткий GOP: seek в пределах секунды,
    # чего для 720p декодера достаточно, чтобы стык/стык не заикался.
    src_fps = _src_fps(src)
    gop = max(12, int(round(src_fps or 25)))      # ~1 сек видео, но не короче 12 кадров
    g = ["-g", str(gop)]
    x264 = ["-c:v", "libx264", "-crf", "26", "-preset", "veryfast"] + g
    hw = None if force_cpu else hw_encoder()
    hwc = (_codec_args(hw, 28, "4M") + g) if hw else None
    tries = _decode_tries(src, vf_gpu, vf_cpu, hw, rot, hwc, x264)
    tmp = dst + ".part.mp4"
    on_prog = None if progress is None else _progress_hook(progress, _src_dur(src))
    for inp, vf, codec in tries:
        if cancel is not None and cancel():
            return None
        try:
            # -progress pipe:1 -nostats: ход сборки уходит в stdout (его разбирает
            # _progress_hook), а не в stderr — там по-прежнему только ошибки (-v error).
            # Замок видеокарты — только под NVENC-заходом (см. `_build_proxy`).
            with codec_gpu_lock(_codec_name(codec), "превью-прокси", emit):
                r = _run_ff(["ffmpeg", "-y", "-v", "error", "-nostats", "-progress", "pipe:1"] +
                            inp + ["-vf", vf] + codec + aac +
                            ["-movflags", "+faststart", tmp], cancel=cancel, on_progress=on_prog)
        except subprocess.TimeoutExpired:
            emit("  ⚠ превью-прокси: ffmpeg завис ({timeout} с) — следующая попытка", timeout=FFMPEG_TIMEOUT)
            continue
        if r is None:
            return None
        if r.returncode == 0 and os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
            # os.replace, а не core.fileio: см. _build_proxy — файл пишет ffmpeg, а не мы
            os.replace(tmp, dst)            # .part -> готово: недописанный не подхватится
            return dst
    try:
        os.remove(tmp)
    except OSError:
        pass  # недописанный .part уже убран
    emit("  ⚠ превью-прокси не собрался для {name} — играю исходник", name=os.path.basename(src))
    return None


def render_draft(xml_path: str, out_mp4: str | None = None, height: int = 720, force_cpu: bool = False, emit: Any = console_emit,

                 ncams: int | None = None, use_proxy: bool = True, cancel: Callable[[], bool] | None = None) -> str:
    """Собрать <stem>.draft.mp4 по EDL финального XML. Возвращает путь к mp4.
    height — размер КОРОТКОЙ стороны кадра (720 для вертикали = 720x1280).
    use_proxy — сначала собрать 720p-прокси камер в _tmp (кэш между черновиками).
    cancel — флаг отмены: проверяется между прокси и попытками кодека, а сам
    ffmpeg-процесс убивается сразу (иначе «Стоп» дожидался бы конца ролика)."""
    emit = wrap_emit(emit)
    from core import xml2ae
    edl = xml2ae.virtual_edl(xml_path, ncams=ncams)
    segs, audio, cams = edl["segs"], edl["audio"], edl["cams"]
    if not segs:
        raise RuntimeError("EDL пуст — в XML нет включённых клипов камер")
    if not (cams and cams[0].get("path") and os.path.isfile(cams[0]["path"])):
        raise RuntimeError("Не найден исходник камеры 1 (нужен для звука)")
    # целевой размер из пропорций секвенции; короткая сторона = height, чётные пиксели
    w0, h0 = edl["w"] or 1080, edl["h"] or 1920
    if w0 <= h0:
        tw, th = height, int(round(h0 * height / w0 / 2)) * 2
    else:
        tw, th = int(round(w0 * height / h0 / 2)) * 2, height
    out_mp4 = out_mp4 or (os.path.splitext(xml_path)[0] + ".draft.mp4")
    tdir = tmp_dir(xml_path)
    stem = os.path.splitext(os.path.basename(xml_path))[0]

    # входы: уникальные файлы камер, участвующие в EDL
    paths = []                                     # index -> path
    def _inp(p: str) -> int:
        if p not in paths:
            paths.append(p)
        return paths.index(p)
    used = sorted({s["ci"] for s in segs})
    for ci in used:
        if not (cams[ci].get("path") and os.path.isfile(cams[ci]["path"])):
            raise RuntimeError(f"Не найден исходник камеры {ci + 1}: {cams[ci].get('path')}")
    # Звук всегда с камеры 1 — но если обработка голоса включена (одно правило:
    # `voicefx.clip_voice_wav`), берём запечённый `<стем>.voice.wav`: тайминг у файла
    # тот же (он посчитан по звуку камеры 1 и той же длины), поэтому фейды и трим
    # остаются прежними, а черновик звучит так же, как уедет в AE. Выключено или файла
    # нет — всё как раньше.
    from core import voicefx
    voice = voicefx.clip_voice_wav(xml_path, emit=emit)   # ход запекания — в лог черновика
    a_idx = _inp(voice or cams[0]["path"])

    # 720p-прокси камер: тяжёлый 4K-декод один раз на все черновики этого проекта
    # Рамка кадра камеры (поле `frame` профиля спикера, core/frame.py) вшивается в
    # прокси: дальше кадр масштабируется целиком, и обрезать его уже негде.
    frames = frame.xml_frames(xml_path)
    crops: dict[int, str] = {}
    for ci in used:
        crops[ci] = _crop_filter(frame.frame_of(frames, ci + 1), cams[ci]["path"], w0, h0)
    proxy = {}
    if use_proxy:
        for ci in used:
            src = cams[ci]["path"]
            dst = _proxy_path(src, tw, th, tdir, crops[ci])
            if os.path.isfile(dst) and os.path.getsize(dst) > 0:
                proxy[src] = dst
                continue
            emit("  прокси камеры {cam}: {tw}x{th} (один раз, дальше черновики быстрые)",
                 cam=ci + 1, tw=tw, th=th)
            p = _build_proxy(src, dst, tw, th, force_cpu=force_cpu, emit=emit, cancel=cancel,
                             crop=crops[ci])
            if p is None and cancel is not None and cancel():
                raise RenderCancelled()
            if p:
                proxy[src] = p
                emit("    готово, {mb:.0f} МБ", mb=os.path.getsize(p) / 1e6)

    flt = []
    for k, s in enumerate(segs):
        src = cams[s["ci"]]["path"]
        # По исходнику (прокси нет) обрезку рамки делаем здесь; в прокси она уже вшита
        crop = "" if src in proxy else crops.get(s["ci"], "")
        i = _inp(proxy.get(src, src))
        d = s["te"] - s["ts"]
        flt.append(f"[{i}:v]trim=start={s['src']:.4f}:duration={d:.4f},"
                   f"setpts=PTS-STARTPTS,{crop}scale={tw}:{th},fps={DRAFT_FPS},"
                   f"format=yuv420p[v{k}]")
    for k, s in enumerate(audio):
        d = s["te"] - s["ts"]
        fo = max(0.0, d - FADE)
        flt.append(f"[{a_idx}:a]atrim=start={s['src']:.4f}:duration={d:.4f},"
                   f"asetpts=PTS-STARTPTS,afade=t=in:d={FADE},"
                   f"afade=t=out:st={fo:.4f}:d={FADE}[a{k}]")
    vcat = "".join(f"[v{k}]" for k in range(len(segs)))
    acat = "".join(f"[a{k}]" for k in range(len(audio)))
    flt.append(f"{vcat}concat=n={len(segs)}:v=1:a=0[vc]")
    if audio:
        flt.append(f"{acat}concat=n={len(audio)}:v=0:a=1[aout]")
    # субтитры: относительный путь + cwd=_tmp — никакого экранирования путей Windows.
    # Имя безопасное и постоянное (см. DRAFT_SUBS_NAME), а не стем ролика.
    ass_name = None
    if edl["words"]:
        ass_name = DRAFT_SUBS_NAME
        _ass_subs(edl["words"], tw, th, os.path.join(tdir, ass_name))
        flt.append(f"[vc]subtitles={ass_name}[vout]")
    else:
        flt.append("[vc]null[vout]")
    script = os.path.join(tdir, f"{stem}.draft_filters.txt")
    with open(script, "w", encoding="utf-8") as f:
        f.write(";\n".join(flt) + "\n")

    def _cmd(codec: Any) -> list[str]:
        c = ["ffmpeg", "-y", "-v", "error"]
        for p in paths:
            c += ["-i", p]
        # Опция графа — по версии ffmpeg: в 7.1 имя `-filter_complex_script` устарело
        # (см. encoders.filter_graph_args), а черновик и микс звука должны звать одну
        # и ту же сборку одинаково.
        c += encoders.filter_graph_args(script) + ["-map", "[vout]"]
        if audio:
            c += ["-map", "[aout]"]
        c += codec + ["-c:a", "aac", "-b:a", "128k", out_mp4]
        return c

    def _vram_used_mib() -> int | None:
        try:
            r = subprocess.run(["nvidia-smi", "--query-gpu=memory.used",
                                "--format=csv,noheader,nounits"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
            return int((r.stdout or "").strip().splitlines()[0])
        except ReelsiError: raise
        except Exception:
            return None

    hw = None if force_cpu else hw_encoder()
    hwc = _codec_args(hw, 28, "4M") if hw else None
    x264 = ["-c:v", "libx264", "-crf", "26", "-preset", "veryfast"]
    tries = [x264] if not hw else [hwc, x264]   # аппаратный может не влезть в VRAM рядом с LLM
    # Декод по кодекам заходов. Черновик декодирует ПРОКСИ (720p) — а прокси собраны
    # тем же выбором (`_build_proxy`), и декод им выпал тот же. Прокси не собрался
    # (или прокси отключены) — декодируется исходник, но это тот же CPU-путь.
    decode_of = {"libx264 (CPU)": _decode_tries("", "", "", None, False, None, x264)[0][0]}
    if hw:
        decode_of[hw] = _decode_tries("", "", "", hw, False, hwc, x264)[0][0]
    emit("  черновик: {segs} сегм. -> {tw}x{th}@{fps} {name}",
         segs=len(segs), tw=tw, th=th, fps=DRAFT_FPS, name=os.path.basename(out_mp4))
    last_err = ""
    for codec in tries:
        if cancel is not None and cancel():
            raise RenderCancelled()
        try:
            # Замок видеокарты — только под NVENC-заходом (`codec_gpu_lock`): черновик
            # с кодеком Quick Sync или «Процессор» карту NVIDIA не занимает и чужого
            # запекания голоса не ждёт.
            with codec_gpu_lock(_codec_name(codec), "черновик mp4", emit):
                r = _run_ff(_cmd(codec), cancel=cancel, cwd=tdir)
        except subprocess.TimeoutExpired:
            err = f"ffmpeg завис ({FFMPEG_TIMEOUT} с) — попытка прервана"
            emit("  ⚠ {err}", err=err)
            last_err = err
            if codec is hwc:
                continue                            # ещё есть CPU-фолбэк
            break
        if r is None:
            raise RenderCancelled()
        if r.returncode == 0 and os.path.isfile(out_mp4) and os.path.getsize(out_mp4) > 0:
            # «CPU x264» — прежняя подпись лога. Именно строкой: hw при CPU-заходе
            # пуст, и «кодек None» в строке о кодеке читалось бы как поломка.
            codec_label = hw if codec is hwc and hw else "CPU x264"
            # Чем собран файл и каким декодом — строкой в лог, ДО «готово». Кодек
            # выбирается по текущей настройке при каждом вызове, и эта строка и есть
            # ответ на «выбрал Quick Sync, а грузит NVIDIA»: в логе видно, что вышло.
            # Декод берётся из заходов того же сборщика: черновик декодирует ПРОКСИ
            # (или исходник, если прокси не собрался), а не входные файлы как есть.
            emit("  кодек {codec}, декод {dec}", codec=codec_label,
                 dec=_decode_label(decode_of[codec_label]))
            emit("  черновик готов ({codec}, {mb:.0f} МБ)",
                 codec=codec_label, mb=os.path.getsize(out_mp4) / 1e6)
            return out_mp4
        err = (r.stderr or "").strip()
        last_err = err.splitlines()[-1] if err else f"код {r.returncode}"
        if codec is hwc:
            # Раньше здесь всегда писали «не хватило VRAM» — и это врало, когда ffmpeg
            # падал по другой причине (битый вход, фильтр, путь). Проверяем кодировщик
            # отдельным микро-энкодом и говорим то, что есть.
            log: Any = os.path.join(tdir, os.path.basename(out_mp4) + ".hwenc_fail.log")
            try:
                with open(log, "w", encoding="utf-8") as f:
                    f.write(" ".join(_cmd(codec)) + "\n\n" + err + "\n")
            except OSError:
                log = None
            emit("  ⚠ {hw} не взлетел ({err})", hw=hw, err=last_err)
            for ln in err.splitlines()[-4:-1]:          # хвост stderr, а не одна строка
                emit("     {line}", line=ln)
            if probe_encoder(hw):
                emit("     сам {hw} рабочий — значит упало НЕ из-за кодировщика "
                     "(смотри ошибку выше: вход/фильтр/путь). CPU-фолбэк это замаскирует.",
                     hw=hw)
            elif hw == "h264_nvenc":
                emit("     h264_nvenc недоступен прямо сейчас; VRAM занято: "
                     "{vram} МиБ — выгрузи модели (мост/LM Studio) до рендера",
                     vram=_vram_used_mib() or '?')
            else:
                emit("     {hw} недоступен прямо сейчас (драйвер занят или лимит сессий)", hw=hw)
            if log:
                emit("     полный лог: {path}", path=log)
            emit("     падаю на CPU x264 — это МЕДЛЕННО")
    raise RuntimeError(f"ffmpeg не собрал черновик: {last_err}")


if __name__ == "__main__":
    try:
        import argparse
        ap = argparse.ArgumentParser(description="Черновой mp4 по финальному XML")
        ap.add_argument("xml")
        ap.add_argument("--out")
        ap.add_argument("--height", type=int, default=720, help="короткая сторона кадра")
        ap.add_argument("--cpu", action="store_true", help="без NVENC (не трогать VRAM)")
        ap.add_argument("--no-proxy", action="store_true", help="без 720p-прокси камер (по исходникам)")
        a = ap.parse_args()
        print(render_draft(a.xml, a.out, height=a.height, force_cpu=a.cpu,
                           use_proxy=not a.no_proxy))
    except ReelsiError as e:
        cli_error(e)
