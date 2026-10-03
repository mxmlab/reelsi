# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Прожиг LUT спикера в видео камер — только на финальной сборке `.jsx`.

Зачем: у спикера на каждую камеру может быть задана таблица цвета
(`speakers/*.json` → `lut: {"<номер камеры с 1>": "путь .cube"}`, разбор —
`core/lut.py`). В превью она накладывается на лету шейдером браузера, а в проект
After Effects камера обязана уходить с УЖЕ прожжённым цветом: AE плохо работает с
цветом, а таблица, оставленная «на потом», тянула бы за собой ручки Lumetri, и
превью разъезжалось бы с рендером.

Где живёт прожиг: ТОЛЬКО в `to_ae_full` — план сцены (`scene_plan`) зовёт и
превью (`/api/scene`), и прожигать там нельзя (долго и не нужно: у превью своя
покраска кадра). Рото-маски по-прежнему считаются по ОРИГИНАЛУ камеры
(`plan["cams"]`): геометрия от цвета не зависит.

Спикер клипа — из `<стем>.project.json` рядом с XML (поле `speaker`), ровно как у
голоса (`core/voicefx.final_voice_for_build`).

Кеш: файл лежит в `<папка результата>/_graded`, а имя — хеш от исходника (путь +
mtime + размер), таблицы (путь + mtime) и аргументов кодека мастера. Сменил LUT,
переснял клип, сменил кодек в настройках — файл пересоберётся; ничего не менял —
берётся готовый.

Папку `_graded` кнопка «Временные файлы» НЕ чистит нарочно: проект AE на эти файлы
ссылается. Убираются они вместе с нарезкой клипа (api/files.py) — по списку
`<стем>.graded.json` рядом с XML.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time
from typing import IO, Any, Callable, Mapping, NoReturn, Sequence

from core import draftrender
from core import encoders
from core import lut as lutlib
from core import media
from core import speakers
from core.app_meta import console_emit, wrap_emit
from core.fileio import atomic_json_dump, atomic_stream_write, json_load_soft
from core.project_file import read_project
from core.umsg import ReelsiError, umsg

# Папка прожжённых файлов рядом с результатом. НЕ `_tmp`: кнопка «Временные файлы»
# (draftrender.clean_tmp) чистит `_tmp` целиком, а на эти файлы ссылается собранный
# проект AE — они не мусор.
GRADED_DIR = "_graded"

# Ход прожига печатаем не чаще, чем раз в 10%: клип бывает на час, и строка на
# каждый процент превратила бы лог сборки в простыню.
PROGRESS_STEP = 10

# Потолок на ffmpeg — не лимит, а страховка от вечного висяка. Мастер это HEVC
# 10 бит почти без потерь (core.encoders), и час 4K кодируется долго: час, как у
# черновика (720p H.264), тут был бы уже пределом работы, а не зависанием.
BAKE_TIMEOUT = 6 * 3600


def graded_dir(outdir: str) -> str:
    """Папка прожжённых файлов рядом с результатом (одна на все клипы папки)."""
    return os.path.join(outdir, GRADED_DIR)


def graded_json_path(xml_path: str) -> str:
    """`<стем XML>.graded.json` — список прожжённых файлов ЭТОЙ нарезки.

    Нужен уборке (api/files.py): файлы лежат в подпапке `_graded`, и обычный обход
    «<стем>.*» рядом с XML их не видит. Список — то, что реально просит эта нарезка;
    тот же файл камеры с той же таблицей у соседней нарезки будет тем же самым
    (имя посчитано от исходника и LUT) — тогда он уедет вместе с первой.
    """
    return os.path.splitext(xml_path)[0] + ".graded.json"


def graded_paths(xml_path: str) -> list[str]:
    """Пути прожжённых файлов из сайдкара; [] — списка нет или он битый.

    Читается мягко (core.fileio.json_load_soft): уборка нарезки не должна падать
    из-за испорченного JSON — файлы нарезки удаляются и без него.
    """
    data = json_load_soft(graded_json_path(xml_path))
    if not isinstance(data, dict):
        return []
    raw = data.get("paths")
    if not isinstance(raw, list):
        return []
    return [str(p) for p in raw if str(p).strip()]


def master_args(setting: str | None = None) -> list[str]:
    """Аргументы кодека мастера (HEVC 10 бит) — те же, что уедут в ffmpeg.

    `setting` — значение настройки «Видеокодек»; None — взять из `ai_config.json`
    (это делает `encoders.pick`). Те же аргументы входят в ключ кеша: сменил
    кодировщик — прожжённый файл пересобирается, а не переиспользуется молча.
    """
    return encoders.pick("master", setting).args


def _stat(path: str) -> tuple[float, int]:
    """(mtime, размер) файла; файла нет — (0.0, 0): ключ кеша всё равно нужен."""
    try:
        st = os.stat(path)
    except OSError:
        return 0.0, 0
    return st.st_mtime, st.st_size


def _digest(src: str, cube: str) -> str:
    """Ключ кеша прожига: исходник, таблица и кодек — по 10 знаков sha1."""
    mtime, size = _stat(src)
    cube_mtime, _cube_size = _stat(cube)
    parts = [os.path.realpath(src), repr(mtime), str(size),
             os.path.realpath(cube), repr(cube_mtime), " ".join(master_args()),
             lut_filter(cube), " ".join(encoders.COLOR_TAGS)]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:10]


def baked_path(src: str, cube: str, outdir: str) -> str:
    """Путь прожжённого файла: `<outdir>/_graded/<стем исходника>-<хеш>.mov`.

    `.mov`, а не `.mp4`: контейнер держит и HEVC, и звук исходника как есть
    (`-c:a copy`) — перекодировать звук в AAC ради mp4 значило бы менять то, что
    звучит в проекте AE.
    """
    stem = os.path.splitext(os.path.basename(src))[0]
    return os.path.join(graded_dir(outdir), "%s-%s.mov" % (stem, _digest(src, cube)))


def filter_path(path: str) -> str:
    """Путь к `.cube` для фильтрграфа ffmpeg: экранированный и с прямыми слешами.

    В фильтре `:` разделяет параметры (`lut3d=file=…:interp=…`), а `\\` — сам
    экранирующий символ: Windows-путь `C:\\LUT\\cam1.cube` без экранирования
    развалил бы `lut3d` на чужие параметры. Поэтому обратные слеши — прямыми
    (ffmpeg на Windows их понимает), двоеточие диска — `\\:`, апостроф — `\\'`
    (он закрыл бы кавычку, в которую путь завёрнут в команде). Пробел и кириллица
    экранирования не требуют: ffmpeg получает аргумент одним элементом списка,
    а не через оболочку.
    """
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def lut_filter(cube: str) -> str:
    """Цепочка фильтров прожига LUT: явная матрица BT.709, tetrahedral интерполяция и tv диапазон."""
    return (
        "scale=out_color_matrix=bt709:out_range=full,"
        "format=gbrp10le,"
        "lut3d=file='%s':interp=tetrahedral,"
        "scale=in_color_matrix=bt709:in_range=full:out_color_matrix=bt709:out_range=tv"
        % filter_path(cube)
    )



def _tail(text: str, n: int = 3) -> str:
    """Последние непустые строки вывода — в них и причина сбоя ffmpeg."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return " / ".join(lines[-n:])


def _remove(path: str) -> None:
    """Убрать временный файл; его может не быть — это не ошибка."""
    try:
        os.remove(path)
    except OSError:
        pass  # ffmpeg не дошёл до записи (или файл уже убран) — убирать нечего


def _raise_cancelled() -> NoReturn:
    """Отмена пользователем: то же исключение, что у остальной сборки.

    Импорт ленивый: `core.xml2ae.parse` тянет за собой пакет `core.xml2ae`, а его
    `__init__` импортирует build — то есть этот модуль. На уровне модуля вышел бы
    круговой импорт.
    """
    from core.xml2ae.parse import Cancelled
    raise Cancelled()


def _progress_logger(emit: Any) -> Callable[[float], None]:
    """Приём процентов прожига: печатает не чаще, чем раз в PROGRESS_STEP."""
    last = -1

    def _on(pct: float) -> None:
        nonlocal last
        step = int(pct // PROGRESS_STEP)
        if step > last:
            last = step
            if step > 0:
                emit("    LUT: {pct:.0f}%", pct=pct)
    return _on


def bake(src: str, cube: str, dst: str, emit: Any = console_emit,
         cancel: Callable[[], bool] | None = None) -> str:
    """Прожечь LUT в видео камеры и положить результат в `dst`; вернуть `dst`.

    `dst` уже есть — не пересобираем: имя посчитано от исходника, таблицы и кодека
    (см. `baked_path`), то есть это кеш, а не «файл с тем же именем».

    ffmpeg: `lut3d` (интерполяция tetrahedral — в 8-битной трилинейной LUT теряет
    ровно то, ради чего его жгут), звук исходника копией (`-c:a copy`), метаданные
    источника (`-map_metadata 0`), кодек — мастер (HEVC 10 бит, `core.encoders`).
    Файл пишет САМ ffmpeg, поэтому подмена `dst` идёт через атомарную запись ядра из
    временного файла рядом (образец — `core.voicefx.analysis_wav`): обрыв или «Стоп»
    не оставляет на месте готового файла половину.

    `cancel()` — «нажали Стоп»: процесс убивается, временный файл убирается, наружу
    уходит `Cancelled` (как у остальных этапов сборки). Сбой ffmpeg — `ReelsiError`
    `lut_bake_failed` с хвостом stderr: молча собрать проект без LUT нельзя.
    """
    emit = wrap_emit(emit)
    if os.path.isfile(dst) and os.path.getsize(dst) > 0:
        return dst                        # уже прожжён этим же набором (исходник + LUT + кодек)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    # `.mov` последним расширением: по нему ffmpeg выбирает муксер, а `.part` он не знает.
    tmp = dst + ".part.mov"
    try:
        cmd = ["ffmpeg", "-y", "-v", "error", "-nostats", "-progress", "pipe:1",
               "-i", src, "-map", "0:v:0", "-map", "0:a?",
               "-vf", lut_filter(cube)]
        cmd += master_args()
        cmd += encoders.COLOR_TAGS
        cmd += ["-c:a", "copy", "-map_metadata", "0", tmp]
        # Прогресс — тот же механизм, что у прокси превью: `-progress pipe:1`
        # построчно, проценты от длительности исходника (draftrender._progress_hook).
        dur = media.probe_duration(src) or 0.0
        hook = draftrender._progress_hook(_progress_logger(emit), dur) if dur > 0 else None
        try:
            r = draftrender._run_ff(cmd, cancel=cancel, timeout=BAKE_TIMEOUT, on_progress=hook)
        except subprocess.TimeoutExpired:
            emit("LUT: ffmpeg не уложился в {sec} с — снят", sec=BAKE_TIMEOUT)
            raise ReelsiError(umsg("lut_bake_failed",
                                   "LUT: ffmpeg не уложился в %d с" % BAKE_TIMEOUT,
                                   path=src, err="timeout"))
        except OSError as e:
            raise ReelsiError(umsg("lut_bake_failed", "LUT: ffmpeg не запустился (%s)" % e,
                                   path=src, err=str(e)))
        if r is None:
            _raise_cancelled()            # «Стоп»: процесс убит, дальше только уборка
        if r.returncode != 0:
            tail = _tail(r.stderr or r.stdout) or "код возврата %d" % r.returncode
            emit("LUT: ffmpeg не отработал — {err}", err=tail)
            raise ReelsiError(umsg("lut_bake_failed",
                                   "LUT не прожёгся (%s): %s" % (os.path.basename(src), tail),
                                   path=src, err=tail))

        def _write(f: IO[Any]) -> None:
            with open(tmp, "rb") as r2:
                shutil.copyfileobj(r2, f, 1024 * 1024)
        atomic_stream_write(dst, _write)
    finally:
        _remove(tmp)
    return dst


def baked_cams_for_build(xml_path: str, cams: Sequence[Mapping[str, Any]],
                         emit: Any = console_emit,
                         cancel: Callable[[], bool] | None = None) -> dict[str, str]:
    """{оригинал камеры: прожжённый файл} для сборки `.jsx`; {} — прожигать нечего.

    Спикер нарезки — в сайдкаре `<стем>.project.json` (поле `speaker`), таблицы — в
    его профиле (`lut` по номеру камеры С ОДИНАРКИ). Нет сайдкара, нет спикера, нет
    `lut` — пусто: сборка идёт как раньше, по оригиналам.

    Битый `.cube` или пропавший файл таблицы — предупреждение в лог и эта камера без
    LUT: из-за чужой таблицы проект AE не должен оставаться несобранным. Сбой самого
    прожига — ПАДЕНИЕ сборки (`ReelsiError` из `bake`): молча отдать проект без
    цвета, который владелец заказал, нельзя.

    Готовые пути пишутся в `<стем>.graded.json` рядом с XML — по нему уборка нарезки
    (api/files.py) знает, что удалять вместе с ней.
    """
    emit = wrap_emit(emit)
    proj = read_project(os.path.splitext(xml_path)[0] + ".project.json")
    if not proj:
        return {}
    prof = speakers.load(proj.get("speaker"))
    if not isinstance(prof, dict):
        return {}
    table = prof.get("lut")
    if not isinstance(table, dict):
        return {}
    outdir = os.path.dirname(os.path.abspath(xml_path))
    mp: dict[str, str] = {}
    for ci, cam in enumerate(cams or []):
        src = str((cam or {}).get("path") or "").strip()
        cube = table.get(str(ci + 1))
        if not src or not isinstance(cube, str) or not cube.strip():
            continue                      # у камеры нет файла или LUT не задан
        cube = cube.strip()
        if not os.path.isfile(cube):
            emit("! LUT камеры {cam} не найден ({path}) — камера идёт без LUT",
                 cam=ci + 1, path=cube)
            continue
        try:
            lutlib.load_cube(cube)        # битую таблицу ловим здесь, а не в ffmpeg
        except ReelsiError as e:
            emit("! LUT камеры {cam} не читается ({err}) — камера идёт без LUT",
                 cam=ci + 1, err=e)
            continue
        if not os.path.isfile(src):
            emit("! LUT камеры {cam} не прожжён: нет файла камеры ({path})",
                 cam=ci + 1, path=src)
            continue
        dst = baked_path(src, cube, outdir)
        name = os.path.basename(cube)
        if os.path.isfile(dst) and os.path.getsize(dst) > 0:
            emit("LUT: камера {cam} — из кеша ({name})", cam=ci + 1, name=name)
        else:
            emit("LUT: прожигаю камеру {cam} ({name})…", cam=ci + 1, name=name)
            t0 = time.monotonic()
            bake(src, cube, dst, emit=emit, cancel=cancel)
            emit("LUT: камера {cam} — готово за {sec:.1f} с", cam=ci + 1,
                 sec=time.monotonic() - t0)
        mp[src] = dst
    if mp:
        atomic_json_dump(graded_json_path(xml_path), {"paths": sorted(set(mp.values()))},
                         indent=1)
    return mp
