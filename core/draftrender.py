# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Черновой mp4 по виртуальному EDL финального XML (этап 2 «уроки video-use»).

Быстрый низкоразрешённый рендер смонтированного результата — «посмотреть глазами
без Premiere/AE»: trim+concat сегментов камер + звук с камеры 1 (с микро-фейдами
10мс на стыках) + слова-субтитры (.ass). Кодек: h264_nvenc, при недоступности /
занятой VRAM — авто-фолбэк на libx264 (CPU).

Через карту идёт всё, что реально можно: 720p-прокси камер собираются NVDEC-декодом
и scale_cuda, итоговый черновик пишет NVENC. Прокси кэшируются в _tmp и переиспользуются
всеми следующими черновиками проекта — без них каждый черновик заново гонит через
декодер ВЕСЬ 4K-исходник (trim стоит после декодера), а черновик пересобирается на
каждой итерации самопроверки.

Временные файлы (прокси, filter-скрипт, .ass) — в <outdir>/_tmp/, итог — <stem>.draft.mp4
рядом с XML.

CLI:  python reelsi/draftrender.py "C:/.../01_C1295.xml" [--cpu] [--height 720] [--no-proxy]
"""
import os, platform, subprocess
from core.app_meta import console_emit, wrap_emit


# Рендер длинного ролика идёт минуты, сборка прокси — десятки секунд. Но ffmpeg
# умеет и висеть вечно (драйвер, битый файл, недодиск), а внутрипроцессный вызов
# без таймаута держал бы JOB навсегда: «Стоп» не имеет хэндла, лечится только
# рестартом сервера. Таймаут щедрый — это не лимит, а страховка от вечного висяка.
FFMPEG_TIMEOUT = 3600


class RenderCancelled(Exception):
    """Отменено пользователем — отдельное исключение, чтобы фронт показывал
    «прервано», а не «ОШИБКА»."""


def _run_ff(cmd, cancel=None, cwd=None, timeout=FFMPEG_TIMEOUT):
    """subprocess.run для ffmpeg: с таймаутом И отменой.

    Возвращает CompletedProcess; None — отменено по `cancel()`; TimeoutExpired —
    процесс убит и выброшен (вызывающий решает, как донести «завис»). Процесс
    добивается в обоих случаях: без этого он сиротеет и держит входные файлы."""
    import time as _t
    if cancel is not None and cancel():
        return None
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

FADE = 0.010          # сек, микро-фейд звука на краях сегментов (как в .jsx)
DRAFT_FPS = 30        # черновик — 30fps, хватает для оценки монтажа


# --------------------------------------------------------------------------- #
# Аппаратный кодировщик: какой брать на этом железе
# --------------------------------------------------------------------------- #
# Наличие кодека в `ffmpeg -encoders` НИЧЕГО не значит: полные сборки содержат и
# h264_amf, и h264_qsv на машине, где нет ни AMD, ни Intel-графики. Единственный
# честный способ — микро-энкод. Пробуем один раз за процесс и запоминаем.
#
# Декод на карте (-hwaccel + scale_cuda) оставлен только для NVIDIA: он там проверен
# годом работы. Остальным отдаём CPU-декод и аппаратный только ЭНКОД — это уже даёт
# основной выигрыш, а рисковать зависанием на чужом железе, которого у нас нет, незачем.
_CANDIDATES = {
    "Darwin":  ["h264_videotoolbox"],                    # медиадвижок Apple Silicon
    "Windows": ["h264_nvenc", "h264_amf", "h264_qsv"],
    "Linux":   ["h264_nvenc", "h264_amf", "h264_qsv"],   # vaapi требует -vaapi_device, отдельно
}
# Параметры качества у каждого свои. Для не-NVIDIA взят битрейт, а не квантователь:
# флаги качества у amf/qsv/videotoolbox разъезжаются от версии к версии ffmpeg, а
# `-b:v` понимают все и всегда. Для ЧЕРНОВИКА этого достаточно.
_ENC_ARGS = {
    "h264_nvenc":        lambda q, br: ["-preset", "p4", "-cq", str(q)],
    "h264_videotoolbox": lambda q, br: ["-b:v", br],
    "h264_amf":          lambda q, br: ["-b:v", br],
    "h264_qsv":          lambda q, br: ["-b:v", br],
}
_HW_CACHE = "unset"          # None = аппаратного нет; строка = имя кодека


def _probe_encoder(name):
    """Кодировщик реально работает ПРЯМО СЕЙЧАС? (драйвер, VRAM, лимит сессий)

    256x256 — не меньше: NVENC отвергает мелкие кадры («Frame Dimension less than the
    minimum supported value»), и проба врала бы «недоступен»."""
    try:
        p = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                            "color=black:s=256x256:d=0.1", "-c:v", name,
                            "-f", "null", "-"], capture_output=True, timeout=60)
        return p.returncode == 0
    except Exception:
        return False


def hw_encoder(refresh=False):
    """Имя рабочего аппаратного H.264-кодировщика или None. Кэш на процесс."""
    global _HW_CACHE
    if _HW_CACHE != "unset" and not refresh:
        return _HW_CACHE
    _HW_CACHE = None
    for name in _CANDIDATES.get(platform.system(), ["h264_nvenc"]):
        if _probe_encoder(name):
            _HW_CACHE = name
            break
    return _HW_CACHE


def _codec_args(name, q, br):
    """Аргументы кодека по имени; неизвестному — битрейт (безопасный минимум)."""
    return [("-c:v"), name] + _ENC_ARGS.get(name, lambda q, b: ["-b:v", b])(q, br)


def tmp_dir(out_xml_or_dir):
    """<outdir>/_tmp — единая папка временных артефактов (черновики-скрипты, ass,
    склейки self-check). НЕ %TEMP%: рядом с проектом, чистится кнопкой/новой нарезкой."""
    d = out_xml_or_dir if os.path.isdir(out_xml_or_dir) else os.path.dirname(out_xml_or_dir)
    t = os.path.join(d, "_tmp")
    os.makedirs(t, exist_ok=True)
    return t


PROXY_GLOB = "pv_*.mp4"          # превью-прокси камер (build_preview_proxy)
# В _tmp живут два независимых кэша прокси:
# 1) `pv_*.mp4` — превью-прокси для плеера веба (build_preview_proxy);
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


def proxy_size(outdir):
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
                pass
    return total


def clean_tmp(outdir, emit=console_emit, proxies=False):
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
    keep = set()
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
                pass
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
                        pass
            for dname in dirs:                       # пустые подпапки убираем, _tmp оставляем
                try:
                    os.rmdir(os.path.join(root, dname))
                except OSError:
                    pass
    if proxies:
        emit("  _tmp очищен: {freed:.0f} МБ", freed=freed / 1e6)
    else:
        emit("  _tmp очищен: {freed:.0f} МБ (прокси оставлены)", freed=freed / 1e6)
    return freed


def _ass_time(t):
    h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _ass_subs(words, tw, th, path):
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


def _proxy_path(src, tw, th, tdir):
    """Имя прокси-файла камеры в _tmp. В ключ входят mtime/size исходника и размер
    кадра: переснял/перекодировал исходник или сменил height — прокси пересоберётся."""
    import hashlib
    st = os.stat(src)
    key = (f"{os.path.abspath(src)}|{int(st.st_mtime)}|{st.st_size}|{tw}x{th}@{DRAFT_FPS}"
           + _rot_key(src))
    return os.path.join(tdir, "proxy_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12] + ".mp4")


def _build_proxy(src, dst, tw, th, force_cpu=False, emit=console_emit, cancel=None):
    """Собрать 720p-прокси камеры: декод и масштаб на GPU (NVDEC + scale_cuda), кодек
    NVENC. Возвращает путь или None (тогда работаем по исходнику, как раньше).

    Зачем: `trim` стоит ПОСЛЕ декодера, поэтому каждый черновик прогоняет весь 4K-исходник
    целиком — а черновик пересобирается на каждой итерации самопроверки и после каждой
    правки. С прокси тяжёлый декод платится один раз, дальше сборка идёт по крошечному
    720p-файлу (замер: 3.8 с -> 0.8 с при 60 сегментах, на реальном 4K разрыв больше)."""
    emit = wrap_emit(emit)
    vf_gpu = f"fps={DRAFT_FPS},scale_cuda={tw}:{th}:format=yuv420p"
    vf_cpu = f"fps={DRAFT_FPS},scale={tw}:{th},format=yuv420p"
    x264 = ["-c:v", "libx264", "-crf", "28", "-preset", "veryfast"]
    hw = None if force_cpu else hw_encoder()
    tries = _decode_tries(src, vf_gpu, vf_cpu, hw,
                          _display_dims(src)[2], _codec_args(hw, 30, "3M") if hw else None, x264)
    tmp = dst + ".part.mp4"
    for inp, vf, codec in tries:
        if cancel is not None and cancel():
            return None
        try:
            r = _run_ff(["ffmpeg", "-y", "-v", "error"] + inp +
                        ["-an", "-vf", vf] + codec + [tmp], cancel=cancel)
        except subprocess.TimeoutExpired:
            emit("  ⚠ прокси: ffmpeg завис ({timeout} с) — следующая попытка", timeout=FFMPEG_TIMEOUT)
            continue
        if r is None:
            return None
        if r.returncode == 0 and os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
            os.replace(tmp, dst)                   # .part -> готово: недописанный не подхватится
            return dst
    try:
        os.remove(tmp)
    except OSError:
        pass
    emit("  ⚠ прокси не собрался для {name} — работаю по исходнику", name=os.path.basename(src))
    return None


def _decode_tries(src, vf_gpu, vf_cpu, hw, rot, hwc, x264):
    """Заходы сборки прокси: [(входные аргументы, видеофильтр, аргументы кодека)].
    Порядок — от быстрого к надёжному, побеждает первый успешный.

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
    if hw == "h264_nvenc" and not rot:
        # 4:2:2 10 бит (Sony/Canon) NVDEC до Blackwell не умеет — следующий заход спасает
        return [(["-hwaccel", "cuda", "-hwaccel_output_format", "cuda", "-i", src], vf_gpu, hwc),
                (["-i", src], vf_cpu, hwc),
                (["-i", src], vf_cpu, x264)]
    # Не-NVIDIA или повёрнутый кадр: декод на CPU, аппаратный только ЭНКОД
    return [(["-i", src], vf_cpu, hwc), (["-i", src], vf_cpu, x264)]


_DIMS_CACHE = {}          # (abspath, mtime, size) -> (w, h, rot): ffprobe на каждый вызов
                          # заметен — _preview_proxy_plan зовётся на каждое открытие
                          # предпросмотра и на каждый опрос прогресса сборки


def _display_dims(src):
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
    except Exception:
        return 1080, 1920, False                 # не прочли — НЕ кэшируем: файл мог ещё писаться
    out = (h0, w0, True) if rot == 90 else (w0, h0, False)
    if ck:
        _DIMS_CACHE[ck] = out
    return out


def _short_side(src, height):
    """(w, h, повёрнут ли): короткая сторона = height, стороны чётные."""
    w0, h0, rot = _display_dims(src)
    if w0 <= h0:
        return height, int(round(h0 * height / w0 / 2)) * 2, rot
    return int(round(w0 * height / h0 / 2)) * 2, height, rot


_FPS_CACHE = {}           # (abspath, mtime, size) -> fps: ffprobe на каждый прокси
                          # не гоняем — один вызов на сборку, дальше из кэша


def _src_fps(src):
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
    except Exception:
        return 25.0


def _rot_key(src):
    """Довесок к ключу кэша для ПОВЁРНУТЫХ исходников.

    Прокси, собранные до фикса автоповорота, лежат искажёнными и лежащими на боку, но
    имя у них было бы тем же — и переиспользовались бы молча. Довесок ставим ТОЛЬКО
    повёрнутым: у нормальных файлов ключ не меняется, и они зря не пересобираются."""
    try:
        return "|rot" if _display_dims(src)[2] else ""
    except Exception:
        return ""


def preview_path(src, height, tdir):
    """Имя превью-прокси камеры в _tmp. Ключ — как у черновикового (mtime/size/размер),
    но префикс свой: тот собран БЕЗ звука и с fps=30, для превью не годится.

    В ключе есть версия формата (`g`): прокси до фикса короткого GOP несли ключевые
    кадры раз в 10 секунд (дефолтный GOP NVENC/X.264 = 250 фреймов), и каждый seek
    в браузере заставлял декодер прогонять до 10 секунд с последнего ключевого кадра.
    Сменили формат — старые прокси обязаны пересобраться, иначе кэш раздавал бы битые."""
    import hashlib
    st = os.stat(src)
    key = f"{os.path.abspath(src)}|{int(st.st_mtime)}|{st.st_size}|prev{height}:g" + _rot_key(src)
    return os.path.join(tdir, "pv_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12] + ".mp4")


def build_preview_proxy(src, dst, height=720, force_cpu=False, emit=console_emit, cancel=None):
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
    пересобирать после каждой правки)."""
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
    for inp, vf, codec in tries:
        if cancel is not None and cancel():
            return None
        try:
            r = _run_ff(["ffmpeg", "-y", "-v", "error"] + inp + ["-vf", vf] + codec + aac +
                        ["-movflags", "+faststart", tmp], cancel=cancel)
        except subprocess.TimeoutExpired:
            emit("  ⚠ превью-прокси: ffmpeg завис ({timeout} с) — следующая попытка", timeout=FFMPEG_TIMEOUT)
            continue
        if r is None:
            return None
        if r.returncode == 0 and os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
            os.replace(tmp, dst)            # .part -> готово: недописанный не подхватится
            return dst
    try:
        os.remove(tmp)
    except OSError:
        pass
    emit("  ⚠ превью-прокси не собрался для {name} — играю исходник", name=os.path.basename(src))
    return None


def render_draft(xml_path, out_mp4=None, height=720, force_cpu=False, emit=console_emit,

                 ncams=None, use_proxy=True, cancel=None):
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
    def _inp(p):
        if p not in paths:
            paths.append(p)
        return paths.index(p)
    used = sorted({s["ci"] for s in segs})
    for ci in used:
        if not (cams[ci].get("path") and os.path.isfile(cams[ci]["path"])):
            raise RuntimeError(f"Не найден исходник камеры {ci + 1}: {cams[ci].get('path')}")
    a_idx = _inp(cams[0]["path"])                  # звук всегда с камеры 1 (из ОРИГИНАЛА)

    # 720p-прокси камер: тяжёлый 4K-декод один раз на все черновики этого проекта
    proxy = {}
    if use_proxy:
        for ci in used:
            src = cams[ci]["path"]
            dst = _proxy_path(src, tw, th, tdir)
            if os.path.isfile(dst) and os.path.getsize(dst) > 0:
                proxy[src] = dst
                continue
            emit("  прокси камеры {cam}: {tw}x{th} (один раз, дальше черновики быстрые)",
                 cam=ci + 1, tw=tw, th=th)
            p = _build_proxy(src, dst, tw, th, force_cpu=force_cpu, emit=emit, cancel=cancel)
            if p is None and cancel is not None and cancel():
                raise RenderCancelled()
            if p:
                proxy[src] = p
                emit("    готово, {mb:.0f} МБ", mb=os.path.getsize(p) / 1e6)

    flt = []
    for k, s in enumerate(segs):
        src = cams[s["ci"]]["path"]
        i = _inp(proxy.get(src, src))
        d = s["te"] - s["ts"]
        flt.append(f"[{i}:v]trim=start={s['src']:.4f}:duration={d:.4f},"
                   f"setpts=PTS-STARTPTS,scale={tw}:{th},fps={DRAFT_FPS},"
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

    def _cmd(codec):
        c = ["ffmpeg", "-y", "-v", "error"]
        for p in paths:
            c += ["-i", p]
        c += ["-filter_complex_script", script, "-map", "[vout]"]
        if audio:
            c += ["-map", "[aout]"]
        c += codec + ["-c:a", "aac", "-b:a", "128k", out_mp4]
        return c

    def _vram_used_mib():
        try:
            r = subprocess.run(["nvidia-smi", "--query-gpu=memory.used",
                                "--format=csv,noheader,nounits"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
            return int((r.stdout or "").strip().splitlines()[0])
        except Exception:
            return None

    hw = None if force_cpu else hw_encoder()
    hwc = _codec_args(hw, 28, "4M") if hw else None
    x264 = ["-c:v", "libx264", "-crf", "26", "-preset", "veryfast"]
    tries = [x264] if not hw else [hwc, x264]   # аппаратный может не влезть в VRAM рядом с LLM
    emit("  черновик: {segs} сегм. -> {tw}x{th}@{fps} {name}",
         segs=len(segs), tw=tw, th=th, fps=DRAFT_FPS, name=os.path.basename(out_mp4))
    last_err = ""
    for codec in tries:
        if cancel is not None and cancel():
            raise RenderCancelled()
        try:
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
            codec_label = hw if codec is hwc else "CPU x264"
            emit("  черновик готов ({codec}, {mb:.0f} МБ)",
                 codec=codec_label, mb=os.path.getsize(out_mp4) / 1e6)
            return out_mp4
        err = (r.stderr or "").strip()
        last_err = err.splitlines()[-1] if err else f"код {r.returncode}"
        if codec is hwc:
            # Раньше здесь всегда писали «не хватило VRAM» — и это врало, когда ffmpeg
            # падал по другой причине (битый вход, фильтр, путь). Проверяем кодировщик
            # отдельным микро-энкодом и говорим то, что есть.
            log = os.path.join(tdir, os.path.basename(out_mp4) + ".hwenc_fail.log")
            try:
                with open(log, "w", encoding="utf-8") as f:
                    f.write(" ".join(_cmd(codec)) + "\n\n" + err + "\n")
            except OSError:
                log = None
            emit("  ⚠ {hw} не взлетел ({err})", hw=hw, err=last_err)
            for ln in err.splitlines()[-4:-1]:          # хвост stderr, а не одна строка
                emit("     {line}", line=ln)
            if _probe_encoder(hw):
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
