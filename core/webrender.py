# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Рендер ролика БЕЗ After Effects: кадры рисует наш же предпросмотр.

Ролик собирается из того же кода, что играет предпросмотр шага 3: страница
`/render` (её отдаёт webui) грузит те же `static/app/*.js` и по команде
`ipvRenderAt(t)` выставляет сцену на момент `t`. Снимает кадры Chrome без окна
(`core/webrender/capture.mjs`, протокол DevTools), кодирует ffmpeg кодеком
назначения `master` (`core/encoders.py`).

Почему так, а не вторым набором формул в Python: план сцены — единственный
источник чисел, а отрисовка живёт в JS. Любая копия анимации в Python разошлась
бы с превью молча, и «рендер не совпал с предпросмотром» искали бы заново.

Что модуль делает сам: спрашивает у запущенного webui план сцены (из него —
fps, размер кадра и длительность), кладёт тело сборки в `_tmp` клипа, вынимает
ffmpeg'ом кадры камер, запускает съёмщик и ffmpeg, читает кадры из stdout съёмщика
и пишет их в stdin ffmpeg. Кадр едет в JPEG (`CAPTURE_FORMAT`), а не в PNG:
кодирование снимка 1080x1920 и base64 через протокол DevTools стоят на кадр больше,
чем сам рендер. Отмена гасит СВОЙ Chrome по PID (не по имени) — на машине может быть
открыт браузер человека.

КАДРЫ КАМЕР КАРТИНКАМИ — ИЗ ИСХОДНИКОВ. Замер владельца: перемотка `<video>` — 90 мс
из 225 на кадр (`#timing`), самая большая доля. Поэтому перед съёмкой куска модуль
вынимает ffmpeg'ом из ИСХОДНИКА камеры РОВНО ТЕ исходные кадры, что видны в кадрах
куска (`frames_for_chunk`), а страница (`static/app/85-inserts-view.js`) рисует камеру
из картинки по номеру кадра — той же отрисовкой, другим источником пикселей.
Соответствие «время монтажа → исходный кадр» считает ОДНО правило (`src_frame_at`),
и его же повторяет страница (`ipvSrcFrameAt`): расхождение на кадр — это ДРУГАЯ
картинка, а увидеть её было бы негде.

ПОЧЕМУ ИСХОДНИК, А НЕ ПРОКСИ РЕНДЕРА (`pv_r…`, был такой). Прокси собирался короткой
стороной кадра ролика (1080), а зум клипа (до 182 %) растягивал его при отрисовке:
AE сжимает 4K сразу в итоговый масштаб, мы сжимали 4K в 1080 и растягивали. Замер
C1476: стена на 20-й секунде 356 против 2004 у AE (дисперсия лапласиана). Размер
вынутого кадра считается по МАКСИМАЛЬНОМУ зуму кадров куска (`frame_size`): короткая
сторона не меньше min(W,H)×зум, но не крупнее исходника, сжатие lanczos. Цвет перевода
YUV→RGB задан явно (`in_range=tv`, матрица bt709) — swscale по умолчанию берёт bt601,
и кожа уезжает на 5–9 единиц. Прокси ПРЕВЬЮ (`pv_…`, шаг 3) этой правкой не тронут:
он для плеера, а не для рендера.

Кадр, который должен был вынуться и не вынулся, — ОШИБКА рендера с именем файла и
номером кадра: подставить прокси или чёрное значило бы молча отдать брак. Перемоткой
`<video>` идут только те камеры, для которых вынимать не из чего вовсе (нет исходника,
не пришёл EDL). Папка картинок убирается вместе с куском.

Вместе с папкой страница получает ГРАНИЦЫ куска (`_chunk_url`: `fstart`/`fcount`) — по
ним она не просит картинку за последним своим кадром. Просьба о таком кадре — это 404 на
ровном месте (кадр принадлежит соседнему куску, и в этой папке его нет), а 404 уводил
камеру на перемотку `<video>` до конца куска: одиночная пропажа картинки не должна
стоить куска целиком (та же причина — у порога пропаж подряд на странице).

Съёмщик печатает в stderr разбивку времени кадра (`#timing seek=… paint=… shot=…
write=…`): по ней видно, что именно тормозит. Формат строки — в одном файле на
обе стороны (`core/webrender/timing.mjs`), разбор — здесь (`_timing_line`).

Скорость. Кадры ролика делятся на куски, и каждый кусок снимает СВОЙ экземпляр Chrome
(у каждого свой профиль): экземпляров по умолчанию половина ядер, но не больше 4
(`_default_instances`). Куски стартуют РАЗОМ и кодируют КАЖДЫЙ СВОЙ сегмент во временный
файл — тем же кодеком и теми же параметрами (`core/encoders`, мастер). Почему не «кадры
всех кусков в один ffmpeg по порядку»: один поток требует, чтобы кадры кусков встречались
в нём по порядку ролика, а это значит держать готовые кадры в памяти до своей очереди —
на трёхминутном ролике это гигабайты. В конце сегменты склеиваются БЕЗ перекодирования
(`ffmpeg -f concat -safe 0 -i list.txt -c copy`, см. `_concat_cmd`): кодек и параметры у
сегментов одни и те же, поэтому лишний ключевой кадр на стыке не виден, а качество на
стыке не отличается от остальных кадров. Звук сводится по плану сцены и накладывается на
готовую картинку (`core/webrender_audio`), видео при этом копируется как есть.

Прогресс — по СУММЕ готовых кадров всех кусков (`_Progress`): куски идут разом, и номер
последнего пришедшего кадра скакал бы между экземплярами («15, 16, 15, 16, 15, 17…»),
а на отставшем куске уезжал назад — полоса прогресса врала бы о ходе рендера.

Живой прогон, ради которого это переделано: 300 кадров, 4 экземпляра, ~0.23 с на кадр —
итог 59 с, ровно как у ОДНОГО экземпляра: второй Chrome стартовал только на кадре 210,
потому что цикл запускал кусок и тут же ждал его конца (кадры читались в том же потоке).

    py -3.10 -m core.webrender клип.xml --out клип.mp4 [--start 5 --dur 3]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Sequence, cast

from core import encoders as enc
from core import paths
from core.app_meta import console_emit, http_req, wrap_emit
from core.encoders import pick as pick_codec
from core.jobstate import kill_tree, task_popen_kwargs
from core.umsg import ReelsiError, cli_error, umsg
from core.xml2ae.layout import HL_EASE_IN, HL_EASE_OUT   # эталон кривой — одно место (layout)

# Съёмщик кадров — рядом, в своей папке: он не часть Python-ядра, а отдельная
# программа на node без единой зависимости (см. шапку capture.mjs). Путь считается
# от корня репозитория (core.paths), а не от папки модуля: свою папку в ядре не
# считает никто, кроме paths.py (tests/test_layout.py).
CAPTURE = os.path.join(paths.ROOT, "core", "webrender", "capture.mjs")
HDR = struct.Struct(">I")            # префикс длины кадра: 4 байта, старший вперёд
DEFAULT_HOST = "127.0.0.1:5001"
# ЕДИНИЦЫ. У времени, которое уезжает съёмщику в командной строке, единица стоит и в
# имени константы, и в имени ключа (`--ready-timeout-ms`): раньше здесь были секунды
# под ключом без единицы, а capture.mjs читал те же числа как миллисекунды — страница
# объявлялась неготовой через 180 мс («не готова за 0 с»). Единственное время у
# съёмщика — миллисекунды; остальные числа там кадры и кадры в секунду.
READY_TIMEOUT_MS = 180000            # сколько ждёт страница рендера (план, шрифты, прокси)
PROXY_TIMEOUT = 3600.0               # сборка прокси камер: 4K-файл — это минуты
HTTP_TIMEOUT = 600.0
# Кадр едет от съёмщика в JPEG, а не в PNG: снимок 1080x1920 в PNG — это мегабайты, и
# почти всё это время уходит на кодирование в браузере и base64 через протокол
# DevTools. JPEG качества 95 отдаёт тот же кадр куском в разы меньшим, а ffmpeg читает
# его потоком как mjpeg. Формат и качество называются ОДНОЙ парой констант: их знают
# обе стороны — съёмщик берёт их из командной строки, приёмник из строки входа ffmpeg.
CAPTURE_FORMAT = "jpeg"
CAPTURE_QUALITY = 95
# Строка съёмщика: `#timing seek=0.4 paint=0.3 shot=0.9 write=0.1 (мс, среднее)`.
# Обе стороны держат её формат в одном месте: съёмщик печатает (webrender/timing.mjs),
# здесь она разбирается, и разойтись молча не может — тест сверяет разбор с печатью.
TIMING_RE = re.compile(
    r"#timing\s+seek=(?P<seek>[0-9.]+)\s+paint=(?P<paint>[0-9.]+)\s+"
    r"shot=(?P<shot>[0-9.]+)\s+write=(?P<write>[0-9.]+)")

# Формат картинок, которые вынимаются из ИСХОДНИКА ДО съёмки (см. шапку). JPEG, а не PNG:
# замер на синтетическом материале 1080x1920 — 150 кадров вынимаются одинаково быстро
# (0.44 с JPEG против 0.47 с PNG), а весят 19.2 МБ против 44.5 МБ. Лишний вес PNG это
# только лишний разбор в браузере и лишний трафик; потерь качества, которых не было,
# он не убирает: снимок кадра и так уходит в JPEG (`CAPTURE_FORMAT`).
FRAME_FORMAT = "jpeg"
FRAME_EXT = ".jpg"
FRAME_Q = "2"                        # -q:v для mjpeg: 2 — почти без потерь (шкала квантования)
# Окно поиска кадра в источнике — четверть кадра источника: кадр ищется по СВОЕЙ метке
# времени, и окно должно быть уже половины кадра, иначе в выборку попадут оба соседа.
FRAME_TOL = 0.25
# Разрыв, на котором выемка режется на отдельные команды ffmpeg: 0.5 с источника. Внутри
# отрезка кадры идут подряд, и декодируется ровно нужное; на разрыве (склейка переставила
# материал) одна команда декодировала бы всё между крайними кадрами — минуты на ровном месте.
FRAME_RUN_GAP = 0.5
FRAME_TIMEOUT = 1800.0               # выемка картинок куска: ffmpeg с `-v error`, но живой
# Сколько читать источник после последнего нужного кадра отрезка. `-t` у ВХОДА (не `-to`
# у выхода): он ограничивает чтение файла, а не кадры после фильтра. Без него `select`
# разбирает источник до САМОГО КОНЦА — замер на 4K-исходнике: три кадра на 8-й секунде
# вынимались 17.8 с вместо 1.0 с. Полсекунды — запас на точность перемотки (`-ss` без
# `-noaccurate_seek` начинает с кадра ДО просимого времени).
FRAME_READ_TAIL = 0.5
# Запас перемотки назад: `-ss` перед `-i` ищет ближайший ключевой кадр ДО времени, и
# разница между ним и первым нужным кадром бывает в целый GOP. Полсекунды покрывают
# обычные 0.5–2 с, а лишнее чтение всё равно ограничено `-t`.
FRAME_SEEK_BACK = 0.5
# Масштабирование вынутого кадра — lanczos: единственный из фильтров ffmpeg, который на
# сжатии 4K->1080 не теряет мелкую деталь (замер: дисперсия лапласиана у bicubic ниже).
FRAME_SCALE_FLAGS = "lanczos"
# Цвет исходника переводится в RGB ЯВНО: диапазон tv (студийный), матрица bt709 — ровно
# то, что записано в файлах камер (`yuv422p10le`, цветовые теги bt709/tv). Отдать это
# swscale «по умолчанию» нельзя: по умолчанию он берёт bt601 (по высоте кадра), и кожа
# уезжает на 5-9 единиц. Опции принадлежат ФИЛЬТРУ scale, а не графу: отдельным звеном
# `in_range=tv` ffmpeg не разбирает вовсе («parsing a filter description»).
# `out_range=full` — не украшение: JPEG не умеет студийный диапазон, и mjpeg-кодировщик
# отказывается работать с tv-кадром («Non full-range YUV is non-standard»).
FRAME_COLOR_FILTER = "in_range=tv:in_color_matrix=bt709:out_range=full:out_color_matrix=bt709"
# Единица, ниже которой сжатие картинки на холсте страницы мылит: у Chrome с
# `imageSmoothingQuality='high'` однократное уменьшение больше чем вдвое берёт не всё,
# что попало в пиксель. Вынутый кадр по правилу зума даёт масштаб около 1:1, и если он
# вдруг станет мельче — это видно здесь, а не глазами по готовому ролику.
FRAME_MIN_DRAW_SCALE = 0.5
FRAMES_PARAM = "frames"              # имя параметра адреса страницы: папка картинок куска
# ДИАПАЗОН кадров куска — в том же адресе, рядом с папкой. Страница грузит кадр ЗАРАНЕЕ
# (`ipvFramePrime`), и без границ куска она просила на его последнем кадре картинку
# СЛЕДУЮЩЕГО куска: файла такого в этой папке нет, сервер отвечает 404, и камера уходила
# на перемотку `<video>` до конца куска (живой прогон: 149 кадров по 94 мс вместо 22).
FRAME_START_PARAM = "fstart"         # первый кадр куска: номер кадра ролика
FRAME_COUNT_PARAM = "fcount"         # сколько кадров в куске
# РАСШИРЕНИЕ картинок куска — в том же адресе: имя файла (`c0_1979.png`) собирает страница,
# и расширение обязано совпасть с тем, что записал ffmpeg. Своей копии «.jpg» у страницы
# больше нет: разойдись они — камера просила бы несуществующие файлы (404), и кусок целиком
# уходил бы на перемотку `<video>` вместо картинок.
FRAME_EXT_PARAM = "fext"             # расширение картинок куска без точки


class _Cancelled(Exception):
    """Отмена рендера: свой Chrome и ffmpeg надо погасить, клип не выйдет."""


class _Progress:
    """Готовые кадры всех кусков: общий процент ролика идёт по их СУММЕ.

    Куски рендерятся РАЗОМ (см. render), и кадры идут вперемешку: каждый кусок
    отсчитывает СВОИ кадры, а в лог уходит СУММА отсчитанного всеми. Раньше в строку
    попадал номер последнего пришедшего кадра, и с четырьмя экземплярами Chrome
    процент скакал: «15, 16, 15, 16, 15, 17, 18, 17…», а на отставшем куске уезжал
    назад («от 50 до 45») — полоса прогресса врала о ходе рендера.

    Сколько кадров у каждого куска — считает САМ кусок и отдаёт готовое число
    (`add(k, n)`), а не «плюс один»: счётчик в том же потоке, что и кадры, и
    задвоить или потерять кадр тут нечем. Из готовых чисел берётся максимум по
    куску (кадры одного куска приходят по порядку, а память о прошлом максимуме
    держит сумму неубывающей).

    Строку печатаем ВНУТРИ замка: иначе порядок строк в логе задавал бы планировщик,
    а не номера кадров. И только на КРУПНЫХ приростах (двадцатая часть ролика):
    строк на каждый кадр лог не выдержит (60 в секунду вытеснили бы из него всё).
    """

    def __init__(self, total: int, emit: Callable[..., Any]) -> None:
        self._total = max(1, int(total))
        self._emit = emit
        self._lock = threading.Lock()
        self._per_chunk: dict[int, int] = {}
        self._done = 0
        self._said = 0                  # последнее, что ушло в лог: строки не повторяются

    def add(self, chunk: int, n: int = 1) -> int:
        """Кусок `chunk` отсчитал n кадров (всего): вернуть готовые кадры ролика.

        Формат строки («кадр N/M») — контракт с дверью прогресса рендера
        (core/render_job._FRAME_RE): по ней считается процент клипа.
        """
        with self._lock:
            self._per_chunk[chunk] = max(self._per_chunk.get(chunk, 0), int(n))
            done = min(self._total, sum(self._per_chunk.values()))
            # Монотонность держит САМ счётчик, а не порядок вызовов: строка назад —
            # это процент назад у двери прогресса, и полоса у человека уезжает.
            done = max(done, self._done)
            self._done = done
            step = max(1, self._total // 20)
            if done == self._total or (done >= self._said + step and done > self._said):
                self._said = done
                self._emit("кадр {n}/{total}", n=done, total=self._total)
            return done

    @property
    def done(self) -> int:
        """Сколько кадров уже ушло в сегменты: по нему — «кадров пришло N из M»."""
        with self._lock:
            return self._done


@dataclass
class _ChunkJob:
    """Запущенный кусок рендера: свой съёмщик, свой кодировщик сегмента, свой поток кадров.

    Куски стартуют разом, поэтому состояние куска живёт в одном месте: по нему и
    уборка (кого гасить), и разбор кодов возврата (чей кусок упал).
    """

    first: int                            # первый кадр куска в ролике (номер кадра)
    count: int                            # сколько кадров снимает кусок
    seg: str                              # временный файл сегмента (склейка — в конце)
    cap: subprocess.Popen[bytes]          # съёмщик кадров (node + СВОЙ Chrome)
    ff: subprocess.Popen[bytes]           # кодировщик сегмента (ffmpeg, кодек мастера)
    cap_err: threading.Thread             # stderr съёмщика: прогресс, PID Chrome, ошибки
    ff_err: threading.Thread              # stderr кодировщика: причина его падения
    pump: threading.Thread | None = None  # кадры съёмщика -> stdin кодировщика
    frames: int = 0                       # сколько кадров доехало до кодировщика
    frame_dir: str = ""                   # папка картинок куска (её убирает поток куска)


@dataclass
class _Ctx:
    """Что нужно КАЖДОМУ куску: страница, размер кадра, кодек, папки и общие двери."""

    url: str                              # страница рендера (одна на все куски)
    w: int
    h: int
    fps: float
    work: str                             # рабочая папка: сегменты кусков и профили Chrome
    codec_args: list[str]                 # аргументы кодека мастера — одни на все сегменты
    color_args: list[str]                 # фильтр перевода цвета и теги — те же на все сегменты
    chrome: str | None
    node: str
    cancel: Callable[[], bool] | None
    state: dict[str, Any]                 # общий: PID-ы своих Chrome и хвост диагностики
    emit: Callable[..., Any]
    progress: _Progress
    abort: threading.Event                # «какой-то кусок оборвался — свои не доснимаем»
    ff_err: list[str]                     # копилка stderr всех кодировщиков и склейки
    frame_dir: str = ""                   # папка картинок ТЕКУЩЕГО куска (ставит render)


# --------------------------------------------------------------------------- #
# Связь с запущенным webui
# --------------------------------------------------------------------------- #
def _request(host: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET/POST JSON к запущенному серверу. План, прокси и тело сборки живут там."""
    url = f"http://{host}{path}"
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data is not None else None
    try:
        with urllib.request.urlopen(http_req(url, data=data, headers=headers),
                                    timeout=HTTP_TIMEOUT) as r:
            out = json.loads(r.read().decode("utf-8"))
    except ReelsiError:
        raise
    except Exception as e:
        # url и err — переменные перевода (ERR_webrender_server): в английском
        # интерфейсе адрес и причину подставляет словарь, а не эта строка.
        raise ReelsiError(umsg("webrender_server", f"Сервер не ответил ({url}): {e}",
                               url=url, err=f"{e}"))
    if not isinstance(out, dict):
        # err здесь — «чем ответил сервер» для английского текста (ERR_webrender_server):
        # у кода два разных повода, и перевод один, поэтому переменные у обоих вызовов
        # одни и те же (иначе в английском осталось бы литеральное `{err}`).
        raise ReelsiError(umsg("webrender_server", f"Сервер ответил не JSON ({url})",
                               url=url, err="not JSON"))
    return out


def _scene_plan(host: str, body: dict[str, Any]) -> dict[str, Any]:
    """План сцены из `/api/scene` — тот же роут, что зовёт предпросмотр шага 3.

    Из плана берём ровно три вещи: fps (кадры рендера идут по нему), размер кадра
    (натуральный размер сцены) и длительность (куда по умолчанию рендерить).
    """
    d = _request(host, "/api/scene", body)
    plan = d.get("plan")
    if not d.get("ok") or not isinstance(plan, dict):
        why = str(d.get("error") or "?")
        raise ReelsiError(umsg("webrender_plan", f"План сцены не получен: {why}", why=why))
    return plan


def _save_body(host: str, body: dict[str, Any]) -> str:
    """Положить тело сборки в `_tmp` клипа и вернуть id для адреса страницы."""
    d = _request(host, "/api/render_body", body)
    if not d.get("ok") or not d.get("id"):
        why = str(d.get("error") or "?")
        raise ReelsiError(umsg("webrender_body", f"Тело сборки не сохранилось: {why}", why=why))
    return str(d["id"])


def _camera_sources(plan: dict[str, Any], edl: dict[str, Any],
                    emit: Callable[..., Any]) -> dict[int, str]:
    """{камера: ИСХОДНИК} для выемки кадров. Пустой словарь — вынимать не из чего.

    Кадры камер рендер берёт из ИСХОДНИКА, а не из прокси (`pv_r…`): прокси собран
    короткой стороной кадра ролика, и зум клипа растягивал его — стена на 20-й секунде
    выходила 356 против 2004 у AE (дисперсия лапласиана). Прокси превью (шаг 3) этой
    правкой не трогается: он для плеера, а не для рендера.

    ПУТЬ берётся у плана (`cams[].path`), а не у EDL: план — тот же разбор XML, что
    уезжает странице рендера, и путь камеры в нём есть всегда. У EDL список камер
    короче (камеры без пути там пропускаются), а `ci` в его кусках нумеруются по
    ПОЛНОМУ списку — брать путь по месту в списке EDL значило бы перепутать камеры.
    У EDL берётся другое: есть ли вообще куски монтажа (нет — вынимать нечего).

    Файла нет на диске — камеры в словаре не будет: её кадры пойдут перемоткой
    `<video>` (как раньше), потому что «исходник уехал» это не повод не рендерить.
    """
    if not (edl.get("segs") or []):
        return {}
    out: dict[int, str] = {}
    for ci, c in enumerate(plan.get("cams") or []):
        path = str((c or {}).get("path") or "")
        if not path:
            continue
        if not os.path.isfile(path):
            emit("⚠ камера {cam}: исходник не найден ({path}) — её кадры идут перемоткой",
                 cam=ci + 1, path=path)
            continue
        out[ci] = path
    return out


def _preview_edl(host: str, xml: str) -> dict[str, Any]:
    """EDL клипа — тот же роут, что наполняет предпросмотр (`IPV.segs`): /api/aicut_preview.

    Из него берётся «что видно в кадре»: по нему и выемка картинок, и страница. Второго
    разбора XML тут быть не должно — разойдись они, разошлись бы и кадры.
    """
    d = _request(host, "/api/aicut_preview", {"xml": xml})
    segs = d.get("segs")
    if not isinstance(segs, list) or not segs:
        raise ReelsiError(umsg("webrender_edl", "Куски монтажа не пришли — рендер идёт перемоткой"))
    return d


# --------------------------------------------------------------------------- #
# Кадры камер картинками: правило «время → кадр» и выемка
# --------------------------------------------------------------------------- #
def _seg_at(segs: Sequence[dict[str, Any]], tm: float) -> dict[str, Any] | None:
    """Кусок EDL, видимый в момент tm — зеркало `pvSegAt` (static/app/60-preview.js).

    Берётся ПЕРВЫЙ кусок, кончающийся позже tm (допуск тот же, 1e-3), иначе последний:
    куски EDL идут подряд и покрывают ролик целиком.
    """
    for s in segs:
        if tm < float(s.get("te") or 0) - 1e-3:
            return s
    return segs[-1] if segs else None


def _cam_time_at(segs: Sequence[dict[str, Any]], ci: int, tm: float) -> float | None:
    """Исходное время камеры ci на момент tm — зеркало `ipvCamTimeAt` (85-inserts-view.js).

    Ищется её собственный кусок, в который попал tm; не попал ни в один — последний её
    кусок (так же, как на странице): за концом ролика камера показывает свой хвост.
    """
    mine = [s for s in segs if int(s.get("ci") or 0) == ci]
    if not mine:
        return None
    for s in mine:
        if float(s.get("ts") or 0) <= tm < float(s.get("te") or 0):
            break
    else:
        s = mine[-1]
    return float(s.get("src") or 0) + max(0.0, tm - float(s.get("ts") or 0))


def src_frame_at(segs: Sequence[dict[str, Any]], fps: float,
                 tm: float) -> tuple[int, int] | None:
    """Какая камера видна в кадре ролика tm и какой ИСХОДНЫЙ кадр ей нужен.

    ОДНО правило на выемку картинок (`frames_for_chunk`) и на страницу рендера
    (`ipvSrcFrameAt` в static/app/85-inserts-view.js): время источника округляется к
    кадру ролика. Расхождение на кадр тут не мелочь — на страницу приезжает ДРУГАЯ
    картинка, и увидеть это можно только глазами по готовому ролику. Возвращает
    (камера, номер исходного кадра) или None: пустой EDL, нулевая частота.
    """
    if fps <= 0:
        return None
    s = _seg_at(segs, tm)
    if s is None:
        return None
    ci = int(s.get("ci") or 0)
    t = _cam_time_at(segs, ci, tm)
    if t is None:
        return None
    # floor(x + 0.5), а не round: у Python round(2.5) округляет к чётному, у JS Math.round —
    # вверх, и ровно на половине кадра стороны разъехались бы на кадр.
    return ci, int(math.floor(t * fps + 0.5))


def frames_for_chunk(segs: Sequence[dict[str, Any]], fps: float, first: int,
                     count: int) -> dict[int, list[int]]:
    """{камера: номера исходных кадров} для кадров куска [first, first+count).

    Кадры куска идут подряд, поэтому собираются разом и без повторов: второй такой же
    номер — это тот же файл, и просить его у страницы дважды незачем.
    """
    out: dict[int, list[int]] = {}
    for k in range(first, first + count):
        r = src_frame_at(segs, fps, k / fps)
        if r is None:
            continue
        out.setdefault(r[0], []).append(r[1])
    return {ci: sorted(set(fs)) for ci, fs in out.items()}


def _file_fps(path: str) -> float | None:
    """Кадров в секунду файла-источника (ffprobe) или None — не прочлось.

    Прокси рендера пересобирается в DRAFT_FPS (core.draftrender), но полагаться на
    число из чужого модуля нельзя: промах по частоте — это кадр, уехавший на один, а
    он виден только глазами. Не прочлось — картинок не будет, рендер идёт перемоткой.
    """
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=r_frame_rate",
                            "-of", "default=nw=1:nk=1", path],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
        num, _, den = (r.stdout or "").strip().partition("/")
        fps = float(num) / float(den) if den else float(num or 0)
    except ReelsiError:
        raise
    except Exception:
        return None
    return fps if 1.0 < fps < 240.0 else None


def _src_index(f: int, fps: float, src_fps: float) -> int:
    """Номер кадра ИСТОЧНИКА, который показывает исходный кадр f ролика.

    Браузер на перемотке `<video>` показывает кадр, НАКРЫВАЮЩИЙ момент (`pts <= t`), —
    значит и вынимать надо тот же самый: floor(t × src_fps). Прибавка 1e-6 — против
    двоичной дроби: 6/60 × 30 в double это 2.9999999999999996, и без неё кадр уехал бы
    на один ровно на границе кадра.
    """
    return int(math.floor((f / fps) * src_fps + 1e-6))


# --------------------------------------------------------------------------- #
# Размер картинки камеры: по зуму клипа, а не «короткая сторона ролика»
# --------------------------------------------------------------------------- #
def _bezier_t(p1x: float, p2x: float, x: float) -> float:
    """Параметр кривой при заданном x — тот же Ньютон, что `bezierT` в 85-inserts-view.js.

    Копия здесь не «похожая формула», а буквальный перенос: расхождение сдвинуло бы
    момент, в который зум максимален, и кадр вынулся бы чуть мельче нужного.
    """
    q = x
    for _ in range(8):
        u = 1.0 - q
        xt = 3 * u * u * q * p1x + 3 * u * q * q * p2x + q * q * q
        dx = 3 * u * u * p1x + 6 * u * q * (p2x - p1x) + 3 * q * q * (1 - p2x)
        if abs(xt - x) < 1e-6 or dx < 1e-6:
            break
        q -= (xt - x) / dx
        q = 0.0 if q < 0 else (1.0 if q > 1 else q)
    return q


def _bezier_y(p1x: float, p2x: float, q: float) -> float:
    """Значение кривой в параметре q — как `bezierY` рядом с ней."""
    u = 1.0 - q
    return 3 * u * q * q + q * q * q


def zoom_at(zoom: Any, tm: float) -> float:
    """Масштаб клипа (доли, 1.0 = 100 %) в кадре ролика номер tm — зеркало `keysAt`.

    Ключи плана — в КАДРАХ ролика, поэтому сюда приходит номер кадра, а не время: время
    на странице умножается на частоту (`ipvZoomAt`), и обе стороны попадают в один и тот
    же ключ. Второй копии интерполятора зума в проекте быть не должно: разойдись она с
    `keysAt`, картинка вынулась бы не того размера.
    """
    z = (zoom or {}) if isinstance(zoom, dict) else {}
    keys = z.get("keys") or []
    if not len(keys):
        return 1.0
    try:
        val = float(keys[0][1])
        last_f = float(keys[-1][0])
        last_v = float(keys[-1][1])
    except (TypeError, ValueError, IndexError):
        return 1.0
    if tm >= last_f:
        return last_v / 100.0      # за последним ключом — его значение (как у keysAt)
    for i in range(len(keys) - 1):
        try:
            f0, v0 = float(keys[i][0]), float(keys[i][1])
            f1, v1 = float(keys[i + 1][0]), float(keys[i + 1][1])
        except (TypeError, ValueError, IndexError):
            return val / 100.0
        if tm < f0:
            break                  # раньше первого ключа — его значение
        if tm >= f1:
            continue
        holds = z.get("holds")
        if isinstance(holds, list) and i < len(holds) and holds[i]:
            val = v0               # джамп-кат: значение держится до следующего ключа
            break
        span = f1 - f0
        if span <= 0:
            val = v1
            break
        u = (tm - f0) / span
        eases = z.get("ease")
        # Нет готового ease у ключа — ЭТАЛОННАЯ пара [in, out] из layout (35/90), как у
        # keysAt страницы. Здесь стояли свои литералы [35, 90] в ЧУЖОМ порядке (in/out
        # наоборот): без ease рендер и превью считали разные кривые.
        e0 = (eases[i] if isinstance(eases, list) and i < len(eases) else None) or [HL_EASE_IN, HL_EASE_OUT]
        e1 = (eases[i + 1] if isinstance(eases, list) and i + 1 < len(eases) else None) or [HL_EASE_IN, HL_EASE_OUT]
        try:
            be = (float(e0[1]) / 100.0, 1.0 - float(e1[0]) / 100.0)
        except (TypeError, ValueError, IndexError):
            be = (HL_EASE_OUT / 100.0, 1.0 - HL_EASE_IN / 100.0)   # тот же эталон 35/90
        q = _bezier_y(be[0], be[1], _bezier_t(be[0], be[1], u))
        val = v0 + (v1 - v0) * q
        return val / 100.0
    return val / 100.0


def _frame_max_zoom(plan: dict[str, Any], first: int, count: int,
                    frame_zoom: float = 1.0) -> float:
    """Максимальный масштаб клипа на кадрах куска [first, first+count) — множитель картинки.

    Считается по КАДРАМ, а не «по ключам»: кривая между ключами не монотонна у всех
    режимов зума, и «взять максимум ключей» дало бы то больше, то меньше нужного —
    а меньше нужного это ровно то мыло, ради которого правило и заведено.

    `frame_zoom` — рамка кадра камеры (`core/frame.py`, поле `frame.zoom` в плане): слой
    увеличен ещё и ею, и в кадре пиксель исходника растянут в `frame_zoom` раз сильнее.
    Считается у вызывающего (по камерам куска), здесь только умножается.
    """
    z = plan.get("zoom") or {}
    if not len(z.get("keys") or []):
        return 1.0
    top = 1.0
    for f in range(int(first), int(first) + max(0, int(count))):
        top = max(top, zoom_at(z, f))
    return max(1.0, top * max(1.0, float(frame_zoom)))


def frame_size(w: int, h: int, zoom: float, src: tuple[int, int]) -> tuple[int, int]:
    """Размер вынимаемого кадра: короткая сторона ≥ min(W,H)×зум, но не больше исходника.

    Зачем правило. AE сжимает 4K сразу в итоговый масштаб — и зум клипа (до 182 %) растит
    УЖЕ сжатый кадр. Мы сжимали 4K в короткую сторону кадра ролика (1080) и растягивали
    на зум: стена на 20-й секунде выходила 356 против 2004 у AE. Вынутый по этому правилу
    кадр зум не растягивает: масштаб его отрисовки на холсте ≈ 1.

    `src` — размеры исходника КАК ПОКАЗЫВАЕТСЯ (поворот из метаданных уже учтён,
    core.draftrender._display_dims): ffmpeg поворачивает кадр сам, и масштабировать надо
    по тому же кадру, который увидит страница.

    Пропорции держит один множитель, а не два независимых ограничения: кадр камеры
    вписывается в кадр ролика целиком, и «короткая сторона» тут — та же величина, что
    у исходника. Стороны чётные: нечётную сторону не переносит ни yuv420p у mjpeg, ни
    сам масштаб 4:2:0.
    """
    sw, sh = int(src[0]), int(src[1])
    if sw < 2 or sh < 2:
        return sw, sh
    need = max(1.0, float(zoom)) * min(int(w), int(h))
    short = min(sw, sh)
    k = min(need / short, 1.0)          # больше исходника не просим: растягивать нечем
    out_w = max(2, int(round(sw * k))) // 2 * 2
    out_h = max(2, int(round(sh * k))) // 2 * 2
    return out_w, out_h


def _src_runs(idx: Sequence[int], gap: int) -> list[list[int]]:
    """Разбить номера кадров источника на ОТРЕЗКИ без больших дыр (см. FRAME_RUN_GAP).

    Внутри отрезка кадры идут подряд — `select` берёт ровно их; на разрыве одна команда
    декодировала бы всё между крайними кадрами, а это на ровном месте минуты.
    """
    out: list[list[int]] = []
    for n in idx:
        if out and n - out[-1][-1] <= gap:
            out[-1].append(n)
        else:
            out.append([n])
    return out


def _quality_args() -> list[str]:
    """Аргументы качества картинки: у JPEG это шкала квантования, у PNG — уровень сжатия."""
    return ["-q:v", FRAME_Q] if FRAME_FORMAT == "jpeg" else ["-compression_level", "2"]


def _extract_cmd(ci: int, src: str, src_fps: float, run: Sequence[int],
                 out_dir: str, size: tuple[int, int]) -> list[str]:
    """Команда выемки одного отрезка кадров ИСХОДНИКА.

    Кадры ищутся по СВОЕЙ метке времени (`select` по t), а не по счёту: счёт после
    `-ss` начинается с нуля, и «кадр 5» означал бы «пятый после перемотки» — а какой
    это кадр источника, пришлось бы считать отдельно. Имена ffmpeg ставит по метке
    кадра (`-frame_pts 1`), и по ним ВИДНО, что вынулось: чужой метке времени (файл не
    с нуля) соответствует другое имя, такой кадр просто не найдётся и уйдёт перемоткой.

    `-ss` перед `-i` — быстрая перемотка: декодируется от ближайшего ключевого, а не
    весь файл. `-copyts` оставляет метки исходника: без него они сдвинулись бы к нулю и
    `-frame_pts` назвал бы файлы номерами от начала отрезка. `-t` У ВХОДА (не у выхода)
    обрывает чтение за последним нужным кадром — иначе `select` разбирает файл до конца
    (см. FRAME_READ_TAIL). Цвет и поворот — тоже здесь: `in_range=tv` с матрицей bt709
    заданы ЯВНО (см. FRAME_COLOR_FILTER), а поворот ffmpeg применяет сам по метаданным,
    поэтому и `scale` считает по КАДРУ КАК ПОКАЗЫВАЕТСЯ (`frame_size`).

    `size` — размер вынутого кадра: он посчитан по зуму клипа (`frame_size`), чтобы
    страница не растягивала картинку. Сжатие — lanczos.
    """
    tol = FRAME_TOL / src_fps
    first, last = run[0], run[-1]
    if last - first + 1 == len(run):
        expr = "between(t,%.6f,%.6f)" % (first / src_fps - tol, last / src_fps + tol)
    else:
        expr = "+".join("lt(abs(t-%.6f),%.6f)" % (n / src_fps, tol) for n in run)
    scale = "scale=%d:%d:flags=%s:%s" % (int(size[0]), int(size[1]),
                                         FRAME_SCALE_FLAGS, FRAME_COLOR_FILTER)
    return ["ffmpeg", "-y", "-v", "error",
            "-ss", "%.6f" % max(0.0, first / src_fps - FRAME_SEEK_BACK),
            "-t", "%.6f" % ((last - first) / src_fps + FRAME_SEEK_BACK + FRAME_READ_TAIL),
            "-copyts", "-i", src, "-vf", "select='%s',%s" % (expr, scale),
            "-fps_mode", "passthrough", "-frame_pts", "1", "-an", *_quality_args(),
            os.path.join(out_dir, "s%d_%%07d%s" % (ci, FRAME_EXT))]


def _produced_frames(out_dir: str, ci: int) -> dict[int, str]:
    """Что вынулось: {номер кадра источника: путь}. Номер стоит в ИМЕНИ файла.

    Проверка честная: файл, названный не тем номером, который просили (у источника
    ненулевая метка времени), в словарь не попадёт — и кадр уйдёт перемоткой, а не
    встанет в ролик чужим.
    """
    pref = "s%d_" % ci
    out: dict[int, str] = {}
    try:
        names = os.listdir(out_dir)
    except OSError:
        return out           # папки нет — вынимать было некуда
    for name in names:
        if not name.startswith(pref) or not name.endswith(FRAME_EXT):
            continue
        try:
            n = int(name[len(pref):-len(FRAME_EXT)])
        except ValueError:
            continue         # чужое имя в папке — не наш кадр
        out[n] = os.path.join(out_dir, name)
    return out


def _extract_runs(ci: int, src: str, src_fps: float, idx: Sequence[int], out_dir: str,
                  size: tuple[int, int], emit: Callable[..., Any],
                  cancel: Callable[[], bool] | None) -> dict[int, str]:
    """Вынуть кадры источника отрезками: {номер кадра источника: путь к файлу}.

    Отмена прекращает выемку: съёмки уже не будет. Ошибка ffmpeg НЕ глотается: кадр,
    который не вынулся, в рендере нечем заменить — подставить прокси или чёрное значило
    бы молча отдать брак (см. capture_frames).
    """
    gap = max(1, int(round(src_fps * FRAME_RUN_GAP)))
    out: dict[int, str] = {}
    for run in _src_runs(idx, gap):
        if cancel is not None and cancel():
            break
        try:
            r = subprocess.run(_extract_cmd(ci, src, src_fps, run, out_dir, size),
                               capture_output=True, timeout=FRAME_TIMEOUT)
        except ReelsiError:
            raise
        except Exception as e:
            raise ReelsiError(umsg("webrender_frames",
                                   f"Кадры камеры {ci + 1} не вынулись из {src} "
                                   f"(кадр источника {run[0]}): {e}",
                                   why=str(e), src=src, frame=run[0], cam=ci + 1)) from e
        if r.returncode != 0:
            why = _drain_stderr(r.stderr)
            raise ReelsiError(umsg("webrender_frames",
                                   f"Кадры камеры {ci + 1} не вынулись из {src} "
                                   f"(кадр источника {run[0]}, ffmpeg код {r.returncode}): {why}",
                                   why=why, src=src, frame=run[0], cam=ci + 1))
        out.update(_produced_frames(out_dir, ci))
    return out


def _drain_stderr(raw: bytes | None) -> str:
    """Хвост stderr процесса — для сообщения об ошибке."""
    return (raw or b"").decode("utf-8", "replace").strip()[-400:]


def _frame_path(out_dir: str, ci: int, f: int) -> str:
    """Путь картинки ИСХОДНОГО кадра — ровно это имя просит страница (`ipvFrameURL`)."""
    return os.path.join(out_dir, "c%d_%d%s" % (ci, f, FRAME_EXT))


def _frame_link(src: str, dst: str) -> bool:
    """Ссылка на уже вынутый кадр (копия — если ссылок файловая система не умеет).

    Один кадр источника показывается НЕСКОЛЬКИМИ кадрами ролика (частота исходника
    ниже частоты ролика): копировать его значило бы писать те же мегабайты второй раз.
    """
    try:
        os.link(src, dst)
        return True
    except ReelsiError:
        raise
    except Exception:
        pass                 # ссылок нет (чужая ФС) — пишем копию
    try:
        shutil.copyfile(src, dst)
        return True
    except ReelsiError:
        raise
    except Exception:
        return False


def capture_frames(frames: dict[int, list[int]], sources: dict[int, str], fps: float,
                   size: tuple[int, int], out_dir: str, emit: Any = None,
                   cancel: Callable[[], bool] | None = None) -> str | None:
    """Вынуть картинки кадров куска из ИСХОДНИКОВ камер в out_dir; вернуть папку или None.

    `frames` — что нужно (`frames_for_chunk`), `sources` — {камера: файл-исходник}
    (`_camera_sources`), `size` — размер вынутого кадра по зуму клипа (`frame_size`).
    Камера без исходника пропускается: её кадры страница возьмёт перемоткой, как раньше.

    Имя файла — номер ИСХОДНОГО кадра ролика (`c<камера>_<кадр>.jpg`): ровно его считает
    страница, и второго правила «какой кадр показать» нигде нет. Ничего не вынулось —
    None, и в адрес страницы папка не попадёт вовсе: перематывать она будет молча.

    Кадр, который ДОЛЖЕН был вынуться и не вынулся, — ошибка рендера, а не повод
    перемотать: подставить прокси или чёрное значило бы отдать ролик с чужим кадром и
    увидеть это только глазами. Ошибку поднимает `_extract_runs` — с именем файла и
    номером кадра; здесь ловится случай «ffmpeg отработал, а кадра в папке нет».
    """
    em = wrap_emit(emit)
    if not frames or not sources or fps <= 0 or (cancel is not None and cancel()):
        return None
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as e:
        em("⚠ кадры картинками: папка не создалась ({err}) — рендер идёт перемоткой", err=e)
        return None
    total = 0
    for ci in sorted(frames):
        want, src = frames[ci], sources.get(ci)
        if not want or not src:
            continue
        src_fps = _file_fps(src)
        if not src_fps:
            em("⚠ кадры камеры {cam}: частота источника не прочлась — её кадры идут перемоткой",
               cam=ci + 1)
            continue
        # Кадры источника — по одному на группу кадров ролика: частота источника ниже
        # частоты ролика, и один его кадр показывается несколькими кадрами ролика.
        idx: list[int] = []
        by_idx: list[list[int]] = []
        for f in want:
            n = _src_index(f, fps, src_fps)
            if idx and n == idx[-1]:
                by_idx[-1].append(f)
            else:
                idx.append(n)
                by_idx.append([f])
        got = _extract_runs(ci, src, src_fps, idx, out_dir, size, em, cancel)
        for n, group in zip(idx, by_idx):
            path = got.get(n)
            if path is None:
                raise ReelsiError(umsg("webrender_frames",
                                       f"Кадры камеры {ci + 1} не вынулись из {src}: "
                                       f"кадра источника {n} нет "
                                       f"(кадры ролика {group[0]}–{group[-1]})",
                                       why=f"кадра {n} нет", src=src, frame=n, cam=ci + 1))
            for f in group:
                if not _frame_link(path, _frame_path(out_dir, ci, f)):
                    raise ReelsiError(umsg("webrender_frames",
                                           "Кадр камеры {cam} не лёг в папку куска: {path}",
                                           why="кадр не записался", path=_frame_path(out_dir, ci, f),
                                           src=src, frame=n, cam=ci + 1))
                total += 1
    if not total:
        return None
    em("кадры картинками: вынуто {n} из исходников, размер {w}x{h}",
       n=total, w=int(size[0]), h=int(size[1]))
    return out_dir


def _render_edl(host: str, xml: str, fps: float, emit: Callable[..., Any]) -> dict[str, Any] | None:
    """EDL клипа для выемки картинок; None — не пришёл, рендер идёт перемоткой.

    Ошибка тут не ошибка рендера: без монтажа нечего вынимать, и ролик собирается
    перемоткой `<video>`. Частота сверяется с той, по которой рендер нумерует кадры:
    страница берёт частоту из ЭТОГО ЖЕ EDL, и разойдись они — одно правило «время → кадр»
    посчитало бы разные номера (второго правила нет, есть одно на обе стороны).
    """
    try:
        d = _preview_edl(host, xml)
    except _Cancelled:
        raise
    except ReelsiError as e:
        emit("⚠ кадры картинками: монтаж не прочитался ({err}) — рендер идёт перемоткой",
             err=str(e))
        return None
    efps = float(d.get("fps") or 0)
    if abs(efps - fps) > 1e-6:
        emit("⚠ кадры картинками: частота монтажа {a:g} не равна частоте рендера {b:g} — "
             "рендер идёт перемоткой", a=efps, b=fps)
        return None
    return d


def source_dims(path: str) -> tuple[int, int]:
    """Размер исходника КАК ПОКАЗЫВАЕТСЯ (поворот из метаданных уже учтён).

    Своей двери к ffprobe тут не заводится: размеры и поворот читает
    `core.draftrender._display_dims` — тот же разбор, которым живёт прокси превью, и
    расхождение с ним означало бы прокси в одной ориентации, а картинки рендера в
    другой. Не прочлось — (0, 0): кадр такого источника не масштабируется, а берётся
    как есть (см. `frame_size`).
    """
    from core.draftrender import _display_dims
    w, h, _rot = _display_dims(path)
    return int(w), int(h)


def _chunk_size(plan: dict[str, Any], edl: dict[str, Any], first: int, count: int,
                w: int, h: int) -> tuple[int, int] | None:
    """Размер картинок куска [first, first+count): короткая сторона по макс. зуму клипа.

    Камеры куска считаются РАЗОМ, а не по одной: зум клипа один на весь кадр ролика
    (вторая камера масштабируется им же), поэтому у картинок куска один размер — и
    странице не приходится держать в голове, что кадр камеры 2 крупнее кадра камеры 1.

    Показанная камера берётся у EDL (какой камерой снят кадр ролика), её исходник — у
    плана (`cams[].path`): «какая камера сейчас в кадре» — то же правило, что у выемки
    и у страницы. Ни одного исходника в куске — None: вынимать не из чего.
    """
    need = set(frames_for_chunk(edl.get("segs") or [], float(plan.get("fps") or 60),
                                first, count))
    if not need:
        return None
    cams = plan.get("cams") or []
    top, src_size = 1.0, (0, 0)
    for ci in sorted(need):
        cam = cams[ci] if 0 <= ci < len(cams) else {}
        path = str(cam.get("path") or "")
        if not path:
            continue
        w0, h0 = source_dims(path)
        # Рамка кадра камеры (`core/frame.py`): слой увеличен ещё и ею — в кадре
        # пиксель исходника растянут в `frame.zoom` раз сильнее (поле есть только у
        # камеры с правками, у остальных 100 %).
        fz = float((cam.get("frame") or {}).get("zoom") or 100.0) / 100.0
        top = max(top, _frame_max_zoom(plan, first, count, fz))
        src_size = (max(src_size[0], w0), max(src_size[1], h0))
    if not src_size[0]:
        return None
    return frame_size(w, h, top, src_size)


def _chunk_frames(edl: dict[str, Any] | None, sources: dict[int, str], size: tuple[int, int] | None,
                  work: str, k: int, first: int, count: int, emit: Callable[..., Any],
                  cancel: Callable[[], bool] | None) -> str:
    """Папка картинок куска k — или пустая строка: картинок нет, кадры идут перемоткой.

    Пустая строка осталась ровно для случая «вынимать не из чего»: монтаж не пришёл,
    исходников камер нет, размер посчитать не по чему. Всё остальное — ошибка рендера
    (её поднимает `capture_frames`): кадр, который должен был вынуться, заменить нечем.
    """
    if edl is None or not sources or size is None:
        return ""
    em = wrap_emit(emit)
    try:
        efps = float(edl.get("fps") or 0)
        need = frames_for_chunk(edl.get("segs") or [], efps, first, count)
        return capture_frames(need, sources, efps, size, os.path.join(work, "frames_%d" % k),
                              emit, cancel) or ""
    except _Cancelled:
        raise
    except ReelsiError:
        raise                    # кадра нет — это не «перемотаем», а ошибка (см. capture_frames)
    except Exception as e:
        # Сюда попадает только неожиданное (ошибку выемки `capture_frames` поднимает
        # сама): разбирать кадры нечем — в лог причину, кадры идут перемоткой.
        em("⚠ кадры картинками: выемка не удалась ({err}) — рендер идёт перемоткой", err=e)
        return ""


# --------------------------------------------------------------------------- #
# Рендер
# --------------------------------------------------------------------------- #
def _read_exact(stream: Any, n: int) -> bytes | None:
    """Ровно n байт из потока или None, если поток кончился (кадр не доехал)."""
    buf = b""
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def _kill_pid(pid: int) -> None:
    """Погасить процесс ПО PID — свой Chrome, а не «все chrome.exe».

    По имени убивать нельзя: на машине открыт браузер человека, и `taskkill /IM`
    снёс бы его вместе с рендером.
    """
    if pid <= 0:
        return
    if os.name == "nt":
        for _ in range(2):
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                               capture_output=True, timeout=15)
            except ReelsiError:
                raise
            except Exception:
                pass  # процесса уже нет — гасить нечего
        return
    try:
        os.kill(pid, 9)          # свой PID: чужой сюда не попадает
    except ReelsiError:
        raise
    except Exception:
        pass  # процесса уже нет (или он чужой для этого пользователя) — гасить нечего


def _capture_cmd(node: str, url: str, w: int, h: int, fps: float, first: int, count: int,
                 profile: str, chrome: str | None) -> list[str]:
    """Командная строка съёмщика. Одна на запуск и на проверку единиц.

    Единицы и типы здесь и только здесь: кадр (`--start`), кадры (`--frames`), кадры в
    секунду (`--fps`), МИЛЛИСЕКУНДЫ у единственного времени (`--ready-timeout-ms`) и
    формат кадра с качеством (`--format`/`--jpeg-quality`). Расхождение единиц между
    Python и capture.mjs ловится тестом на РЕАЛЬНОМ разборе съёмщика (`--parse-only`) —
    см. tests/test_webrender.py.

    Экземпляров съёмщика на рендер может быть несколько (см. `_frame_chunks`): у
    каждого свой Chrome со своим профилем (одному браузеру профиль занят), но командная
    строка у всех одна и та же — отличается только куском кадров. Поэтому и проверка
    единиц одна на все.
    """
    cmd = [node, CAPTURE, "--url", url, "--w", str(w), "--h", str(h),
           "--fps", "%g" % fps, "--start", str(first), "--frames", str(count),
           "--profile", profile, "--ready-timeout-ms", str(READY_TIMEOUT_MS),
           "--format", CAPTURE_FORMAT, "--jpeg-quality", str(CAPTURE_QUALITY)]
    if chrome:
        cmd += ["--chrome", chrome]
    return cmd


def _default_instances() -> int:
    """Сколько экземпляров Chrome поднимать по умолчанию: половина ядер, но не больше 4.

    Потолок не про экономию, а про память: каждый экземпляр — свой Chrome со своей
    копией страницы (десять вкладок 1080×1920 на слабой машине кладут её в своп).
    """
    cpus = os.cpu_count() or 1
    return max(1, min(4, cpus // 2))


def _frame_chunks(first: int, count: int, instances: int) -> list[tuple[int, int]]:
    """Куски кадров для экземпляров съёмщика: [(первый кадр, сколько кадров)].

    Куски идут подряд и покрывают ровно `count` кадров — сумма равна целому, разница
    между кусками не больше кадра. Нарезка равными долями, а не «по времени»: кадры
    нумеруются целыми, и дробить их иначе нечем.
    """
    n = max(1, min(int(instances), max(1, count)))
    base, rest = divmod(count, n)
    out: list[tuple[int, int]] = []
    pos = first
    for k in range(n):
        take = base + (1 if k < rest else 0)
        if take <= 0:
            continue
        out.append((pos, take))
        pos += take
    return out


def _codec_pix_fmt(codec_args: Sequence[str], default: str = "yuv420p") -> str:
    """Формат кадра из аргументов кодека (`-pix_fmt …`) или запасной.

    Нужен цветовому фильтру: он обязан отдать кадр ровно в том формате, в каком его
    ждёт кодировщик (у мастера это 10 бит), иначе swscale переведёт кадр второй раз —
    уже без матрицы, и цвет уедет молча. Формат называется ОДИН раз: в аргументах
    кодека (core/encoders), а не второй копией здесь.
    """
    args = list(codec_args)
    for i, a in enumerate(args[:-1]):
        if a == "-pix_fmt":
            return args[i + 1]
    return default


def render(xml: str, out: str, *, start: float | None = None, dur: float | None = None,
           body: dict[str, Any] | None = None, host: str = DEFAULT_HOST,
           emit: Any = None, cancel: Callable[[], bool] | None = None,
           chrome: str | None = None, node: str = "node",
           keep_profile: bool = False, instances: int = 0,
           audio: bool = True, codec: enc.Choice | None = None) -> dict[str, Any]:
    """Собрать ролик в `out` без After Effects.

    `start`/`dur` — кусок ролика в секундах (для проверок); без них рендерится всё.
    `body` — тело сборки, ровно то, что страница шага 3 шлёт на `/api/scene`
    (стиль, музыка, вставки, интро). Без него план соберётся по умолчаниям клипа:
    камеры, субтитры и жёлтые из сайдкаров — но ни вставок, ни интро.
    `cancel` — «останови»: зовётся между кадрами, гасит все свои Chrome и ffmpeg.
    `instances` — сколько экземпляров Chrome поднять на ролик (0 — по умолчанию:
    половина ядер, не больше 4). Кадры делятся на куски, все куски стартуют РАЗОМ (у
    каждого свой Chrome и свой кодировщик сегмента), а в конце сегменты склеиваются
    без перекодирования (`_concat_cmd`).
    `audio` — сводить ли звук по плану (`core/webrender_audio`): False оставляет ролик
    без звуковой дорожки (нужно там, где картинку проверяют отдельно).
    `codec` — кодек мастера, выбранный ВЫЗЫВАЮЩИМ (None — выбрать здесь, как раньше).
    Нужен тому, кто по кодеку решает, брать ли замок видеокарты (`core.gpulock`):
    второго выбора кодека — а значит и второго ответа «NVENC или нет» — быть не должно.
    Возвращает словарь: ok, out, frames, fps, w, h, cancelled, audio.
    """
    em = wrap_emit(emit)
    xml = os.path.abspath(xml)
    if not os.path.isfile(xml):
        raise ReelsiError(umsg("file_not_found", f"Файл не найден: {xml}", path=xml))
    if not os.path.isfile(CAPTURE):
        raise ReelsiError(umsg("webrender_capture", f"Нет съёмщика кадров: {CAPTURE}",
                               path=CAPTURE))
    # Папка выходного файла — ДО запуска ffmpeg: на несуществующей папке он выходил
    # сразу, съёмщик падал на закрытой трубе, и пользователь видел «съёмщик кадров упал»,
    # то есть причину, которой он не создавал.
    out = os.path.abspath(out)
    outdir = os.path.dirname(out)
    if outdir:
        os.makedirs(outdir, exist_ok=True)
    # Картинка пишется во ВРЕМЕННЫЙ файл, а не сразу в `out`: звук сводится по плану и
    # склеивается с готовым видео одним проходом (`core/webrender_audio.mix`, видео там
    # копируется без перекодирования). Пишем в `out` — и склейка читала бы то, что сама
    # же перезаписывает.
    video = _video_only_path(out) if audio else out
    payload = dict(body or {})
    payload["xml"] = xml

    plan = _scene_plan(host, payload)
    fps = float(plan.get("fps") or 60)
    w = int(plan.get("w") or 1080)
    h = int(plan.get("h") or 1920)
    total = float(plan.get("dur") or 0)
    t0 = max(0.0, float(start or 0))
    span = float(dur) if dur else max(0.0, total - t0)
    first = int(round(t0 * fps))
    count = int(round(span * fps))
    if count <= 0:
        raise ReelsiError(umsg("webrender_empty", "Нечего рендерить: пустой интервал"))
    body_id = _save_body(host, payload)
    # EDL и частота — для выемки картинок: страница считает кадр по ТОМУ ЖЕ EDL
    # (`ipvSrcFrameAt`), и второго «что видно в кадре» тут быть не должно.
    edl = _render_edl(host, xml, fps, em)
    # ИСХОДНИКИ камер, а не прокси рендера: из прокси (короткая сторона кадра ролика)
    # кадр выходил мыльным — зум клипа растягивал его (см. _camera_sources, frame_size).
    sources = _camera_sources(plan, edl or {}, em) if edl is not None else {}

    if codec is None:
        # Кодек выбирает вызывающий (для замка видеокарты ему надо знать, NVENC это или
        # нет), а здесь выбор делается только тогда, когда его не передали: CLI и тесты.
        codec = pick_codec("master")
    # Цвет: снимок Chrome — полный RGB, а в файл он должен уйти YUV в видеодиапазоне
    # BT.709 (иначе тени и полутени уезжают вверх, см. core/encoders.COLOR_FILTER).
    # Формат кадра берётся у САМОГО кодека (`-pix_fmt` в его аргументах): второй копии
    # «сколько бит у мастера» тут быть не должно — разошлась бы с кодеком молча.
    color_args = enc.color_args(codec.args[1], _codec_pix_fmt(codec.args))
    url = ("http://%s/render?xml=%s&body=%s&pxh=%d"
           % (host, urllib.parse.quote(xml), urllib.parse.quote(body_id), min(w, h)))
    # Рабочая папка рендера: сегменты кусков, список склейки и профили Chrome. Профиль
    # свой у КАЖДОГО экземпляра (одному браузеру профиль занят), а имя папки с PID и
    # временем — чтобы повторный рендер не ждал освобождения чужой.
    work = _work_dir(xml)
    os.makedirs(work, exist_ok=True)
    chunks = _frame_chunks(first, count, instances if instances > 0 else _default_instances())
    segs = [_segment_path(work, k) for k in range(len(chunks))]
    list_path = _concat_list_path(work)
    # Размер картинок куска — по МАКСИМАЛЬНОМУ зуму ЕГО кадров (frame_size): у куска без
    # наезда картинка мельче, и место на диске тратится по факту, а не «на всякий случай».
    sizes: list[tuple[int, int] | None] = [
        _chunk_size(plan, edl, cfirst, ccount, w, h) if edl is not None and sources else None
        for cfirst, ccount in chunks]
    em("рендер без AE: {w}x{h}, {fps:g} к/с, кадров {n}, кодек {codec}, снимок {fmt} q{q}, "
       "экземпляров {k}", w=w, h=h, fps=fps, n=count, codec=codec.label,
       fmt=CAPTURE_FORMAT, q=CAPTURE_QUALITY, k=len(chunks))
    # Цвет — в лог одной строкой: «тени уехали» ищут именно здесь, а по готовому файлу
    # видно только теги (и то не всегда: у части кодеков их пишет сам битстрим).
    em("цвет: {range} диапазон, матрица {matrix}, первичные и передача {trc}",
       range=enc.COLOR_RANGE, matrix=enc.COLOR_MATRIX, trc=enc.COLOR_TRC)
    state: dict[str, Any] = {"chrome_pids": [], "tail": []}
    # stderr ВСЕХ кодировщиков и склейки читается потоком: полная труба встала бы, а
    # причина падения ищется в её хвосте.
    ff_err: list[str] = []
    jobs: list[_ChunkJob] = []
    concat: subprocess.Popen[bytes] | None = None
    concat_err: threading.Thread | None = None
    concat_started = False        # ffmpeg склейки уже пишет итог: половину файла убираем
    abort = threading.Event()     # «какой-то кусок оборвался» — свои не доснимаем
    progress = _Progress(count, em)
    ctx = _Ctx(url=url, w=w, h=h, fps=fps, work=work, codec_args=list(codec.args),
               color_args=list(color_args), chrome=chrome, node=node, cancel=cancel,
               state=state, emit=em, progress=progress, abort=abort, ff_err=ff_err)
    written = 0
    cancelled = False
    failed: BaseException | None = None
    built = False                 # склейка дошла до конца: итоговая картинка собрана
    try:
        # ВСЕ куски стартуют разом, и кадры каждого читает СВОЙ поток: ждать кусок целиком
        # здесь нельзя — экземпляры пойдут по очереди, и рендер займёт столько же, сколько
        # один экземпляр (живой прогон: 300 кадров, 4 экземпляра — 59 с, второй Chrome
        # стартовал только на кадре 210).
        for k, (cfirst, ccount) in enumerate(chunks):
            if cancel is not None and cancel():
                raise _Cancelled()
            # Картинки кадров ИМЕННО ЭТОГО куска — до его запуска: страница просит их с
            # первого кадра. Папка куска уезжает в адрес страницы (`_start_chunk`) и
            # убирается вместе с куском (поток кадров, см. _pump_chunk).
            ctx.frame_dir = _chunk_frames(edl, sources, sizes[k], work, k, cfirst, ccount,
                                          em, cancel)
            jobs.append(_start_chunk(ctx, k, cfirst, ccount, segs[k]))
        # Ждём кадры и СЛУШАЕМ отмену: потоки кадров ошибку наружу не отдают (её разбирает
        # разбор кодов возврата ниже), а «Стоп» иначе заметился бы только на последнем куске.
        while any(job.pump is not None and job.pump.is_alive() for job in jobs):
            if cancel is not None and cancel():
                raise _Cancelled()
            time.sleep(0.1)
        for job in jobs:
            if job.pump is not None:
                job.pump.join()
        # Отмену проверяем ПОСЛЕ потоков, а не только в ожидании: кусок мог честно
        # добежать до «Стоп» и выйти сам, и тогда разбор кодов возврата принял бы отмену
        # за падение съёмщика («кадров пришло N из M» вместо «рендер отменён»).
        if cancel is not None and cancel():
            raise _Cancelled()
        written = progress.done
        # Коды возврата — по ВСЕМ кускам: упал любой — ролик не полный.
        rc_ff = max((job.ff.wait() for job in jobs), default=0)
        rc_cap = max((job.cap.wait() for job in jobs), default=0)
        # ПОРЯДОК РАЗБОРА: сначала кодировщик, потом съёмщик. ffmpeg падает первым (нет
        # папки, нет места, не понял вход) и закрывает трубу — съёмщик после этого падает на
        # записи в неё, и его «код 1» ничего не объясняет: пользователь видел «съёмщик
        # кадров упал» там, где причина — ffmpeg. Его stderr читался потоком (ff_err).
        if rc_ff != 0:
            tail = _tail_text(ff_err)
            raise ReelsiError(umsg("webrender_ffmpeg",
                                   f"ffmpeg куска упал (код {rc_ff}): {tail}",
                                   code=rc_ff, err=tail))
        if rc_cap != 0:
            tail = _tail_text(state["tail"])
            raise ReelsiError(umsg("webrender_capture_failed",
                                   f"Съёмщик кадров упал (код {rc_cap}): {tail}",
                                   code=rc_cap, err=tail))
        if written != count:
            raise ReelsiError(umsg("webrender_short", f"Кадров пришло {written} из {count}",
                                   n=written, total=count))
        # ---- склейка: сегменты -> итоговая картинка, без перекодирования ----
        if cancel is not None and cancel():
            raise _Cancelled()
        _write_concat_list(list_path, segs)
        concat = subprocess.Popen(_concat_cmd(list_path, video),
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        concat_started = True
        concat_err = threading.Thread(target=_read_lines, args=(concat.stderr, ff_err),
                                      daemon=True)
        concat_err.start()
        rc_concat = concat.wait()
        if rc_concat != 0:
            tail = _tail_text(ff_err)
            # Код тот же, что у кодировщика куска: падает-то ffmpeg, и причина — в его хвосте.
            raise ReelsiError(umsg("webrender_ffmpeg",
                                   f"склейка кусков: ffmpeg упал (код {rc_concat}): {tail}",
                                   code=rc_concat, err=tail))
        built = True
    except _Cancelled:
        cancelled = True
        em("рендер отменён")
    except BaseException as e:
        # Ошибку разбираем ПОСЛЕ уборки: сообщение о причине должно уйти наружу целиком
        # (см. порядок разбора выше), а не потеряться на гашении процессов.
        failed = e
    finally:
        # Гасим ВСЕ свои процессы: у рендера несколько кусков, и каждый поднимает СВОЙ
        # Chrome и СВОЙ кодировщик сегмента. Брошенный Chrome остался бы висеть (со своим
        # профилем и своей страницей), а брошенный кодировщик держал бы сегмент открытым.
        # По имени не убиваем никогда — на машине открыт браузер человека.
        for job in jobs:
            if job.cap.poll() is None:
                kill_tree(job.cap)
            if job.ff.poll() is None:
                kill_tree(job.ff)
        if concat is not None and concat.poll() is None:
            kill_tree(concat)
        # Chrome — по PID и все: экземпляров на рендер несколько, и каждый поднимает свой
        # Chrome (PID приходит строкой `#chrome-pid` от своего съёмщика).
        for pid in list(state["chrome_pids"]):
            _kill_pid(int(pid))
        for job in jobs:
            # Хвост stderr съёмщика читает поток, а в сообщение об ошибке идёт он же:
            # без ожидания последняя строка (та самая причина) могла не успеть доехать.
            job.cap_err.join(timeout=5.0)
            job.ff_err.join(timeout=5.0)
            if job.pump is not None:
                job.pump.join(timeout=5.0)
            _close_pipe(job.ff.stdin)
        if concat is not None:
            if concat_err is not None:
                concat_err.join(timeout=5.0)
            _close_pipe(concat.stdin)
        # Кадры считает общий счётчик кусков: он же и на отмене показывает, докуда дошло.
        written = progress.done
        # Половина итогового файла хуже отсутствия файла: убираем то, что успел написать
        # ffmpeg склейки (в `out` он пишет только при рендере без звука, и только если
        # склейка вообще началась — иначе на месте `out` мог лежать прежний готовый ролик).
        if not built and (video != out or concat_started):
            _remove_file(video)
        # Сегменты — это гигабайты (по куску на экземпляр): убираем всегда, и на ошибке тоже.
        _cleanup_work(work, segs, list_path, keep_profile)

    if cancelled:
        return {"ok": False, "cancelled": True, "out": out, "frames": written, "fps": fps,
                "w": w, "h": h, "error": "рендер отменён"}
    if failed is not None:
        raise failed
    if not jobs:
        raise ReelsiError(umsg("webrender_failed", "Рендер не запустился"))
    if not os.path.isfile(video) or os.path.getsize(video) == 0:
        raise ReelsiError(umsg("webrender_no_file", f"Файл не собрался: {video}", path=video))
    # ---- звук: по плану, поверх готовой картинки ----
    # Отдельным шагом, а не в том же ffmpeg: картинка уже закодирована, и миксу остаётся
    # скопировать её (`-c:v copy`) и добавить дорожку. Ошибка микса — ошибка рендера:
    # отдать ролик без звука значит молча показать брак (см. webrender_audio.mix).
    if audio:
        from core import webrender_audio
        try:
            webrender_audio.mix(plan, video, out, start=t0, dur=span, emit=em)
        finally:
            try:
                os.remove(video)
            except OSError:
                pass       # временный файл уже убран — мусор в папке вывода не оставляем
    em("готово: {out} — кадров {n}", out=out, n=written)
    return {"ok": True, "cancelled": False, "out": out, "frames": written, "fps": fps,
            "w": w, "h": h, "audio": bool(audio)}


def _video_only_path(out: str) -> str:
    """Временный файл картинки рядом с итоговым (его убирает `render`).

    Имя в папке вывода начинается с точки: уборка `_tmp` клипа такие файлы не трогает,
    а «глазами» они не мешают — и всё же это временный файл, и рендер его за собой убирает.
    """
    return os.path.join(os.path.dirname(os.path.abspath(out)),
                        ".reelsi_video_" + os.path.basename(out))


def _tmp_dir(xml: str) -> str:
    """`_tmp` клипа — там же, где прокси и черновики (core.draftrender.tmp_dir)."""
    from core.draftrender import tmp_dir
    return tmp_dir(xml)


def _work_dir(xml: str) -> str:
    """Рабочая папка рендера в `_tmp` клипа: сегменты кусков, список склейки, профили.

    Имя с PID и временем — как было у профиля: повторный рендер того же клипа не ждёт
    освобождения папки и не путает свои файлы с чужими. Сегменты — ГИГАБАЙТЫ (по куску
    на экземпляр), поэтому папка убирается за рендером всегда (`_cleanup_work`).
    """
    return os.path.join(_tmp_dir(xml), "webrender_%d_%d" % (os.getpid(), int(time.time())))


def _segment_path(work: str, k: int) -> str:
    """Временный файл сегмента куска — в рабочей папке рендера.

    Контейнер тот же mp4, что у итогового ролика: сегменты пишутся ОДНИМ кодеком и
    одними параметрами (`core.encoders`, мастер), поэтому склейка идёт `-c copy`, и
    лишний ключевой кадр на стыке не виден.
    """
    return os.path.join(work, "seg_%d.mp4" % k)


def _concat_list_path(work: str) -> str:
    """Файл списка для склейки (`ffmpeg -f concat`) — рядом с сегментами."""
    return os.path.join(work, "concat.txt")


def _concat_list_text(segs: Sequence[str]) -> str:
    """Тело списка склейки: по строке `file '<путь>'` на сегмент, в порядке ролика.

    Путь берётся в кавычки и экранируется так, как это понимает разборщик списка у
    ffmpeg (av_get_token): одиночная кавычка — это `'\\''`. Порядок строк — порядок
    кусков, то есть порядок ролика: пропущенный или переставленный сегмент сдвинул бы
    весь ролик после него.
    """
    return "".join("file '%s'\n" % p.replace("'", "'\\''") for p in segs)


def _concat_cmd(list_path: str, dst: str) -> list[str]:
    """Склейка сегментов БЕЗ перекодирования: `-f concat -safe 0 -i список -c copy`.

    `-safe 0` — пути в списке абсолютные (по умолчанию демультиплексор их отвергает).
    `-c copy` — кодек и параметры у сегментов одни и те же, второй раз кодировать
    нечего: склейка выходит побитовой сборкой потоков, а не новым качеством.
    """
    return ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
            "-i", list_path, "-c", "copy", dst]


def _write_concat_list(path: str, segs: Sequence[str]) -> None:
    """Записать список склейки: UTF-8 БЕЗ BOM и с LF на конце строки.

    BOM демультиплексор не срезает и приклеил бы его к первому пути (съёмка идёт в
    папку с кириллицей, а не в ASCII), а `\\r` в конце строки попал бы в имя файла.
    Файл временный, поэтому запись обычная: убирает его `_cleanup_work`.
    """
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(_concat_list_text(segs))


def _close_pipe(pipe: Any) -> None:
    """Закрыть трубу процесса: EOF кодировщику, иначе он ждёт кадров, которых не будет."""
    if pipe is None:
        return
    try:
        pipe.close()
    except Exception:
        pass  # труба уже закрыта — процесс сам завершится


def _remove_file(path: str) -> None:
    """Убрать файл, которого может и не быть (своё временное рендер убирает сам)."""
    try:
        os.remove(path)
    except OSError:
        pass  # файла нет — убирать нечего


def _cleanup_work(work: str, segs: Sequence[str], list_path: str, keep_profile: bool) -> None:
    """Убрать за рендером: сегменты, список склейки и профили Chrome.

    Сегменты убираются ВСЕГДА, и на ошибке тоже: брошенные, они молча заняли бы диск
    (по куску на экземпляр, то есть гигабайты). `keep_profile` (--keep-profile) — дверь
    для разбора: профили Chrome остаются на диске, а сегменты всё равно убираются.
    """
    for path in [*segs, list_path]:
        _remove_file(path)
    if not keep_profile:
        shutil.rmtree(work, ignore_errors=True)


def _timing_line(line: str) -> dict[str, float] | None:
    """Разбор строки `#timing …` съёмщика в словарь чисел (мс) или None.

    Разбивка времени — единственный способ понять, что именно тормозит рендер: ждать
    кадр <video>, рисовать, снимать или отдавать байты. Строку печатает съёмщик
    (`webrender/timing.mjs`), а разбор живёт здесь — и он же в тесте, поэтому формат
    не может разойтись молча: съёмщик печатает ЕГО ЖЕ функцией.
    """
    m = TIMING_RE.match(line)
    if not m:
        return None
    try:
        return {k: float(v) for k, v in m.groupdict().items()}
    except ValueError:
        return None


def _pump_stderr(proc: subprocess.Popen[bytes], state: dict[str, Any],
                 emit: Callable[..., Any]) -> None:
    """Читать stderr съёмщика: оттуда приходят прогресс и PID своего Chrome.

    Отдельным потоком: полная труба stderr остановила бы съёмщик на середине
    рендера (и это выглядело бы как «завис»).

    `#timing` — в лог как есть (по ней видно, на что уходит время), но не в хвост
    диагностики: она приходит на КАЖДЫЕ 30 кадров и вытеснила бы из хвоста причину
    падения.
    """
    pid_mark = "#chrome-pid "
    if proc.stderr is None:
        return
    for raw in proc.stderr:
        line = raw.decode("utf-8", "replace").rstrip()
        if not line:
            continue
        if line.startswith(pid_mark):
            try:
                # PID-ов столько же, сколько экземпляров съёмщика: копим их все, а не
                # держим последний — иначе уборка погасила бы только один Chrome.
                state["chrome_pids"].append(int(line[len(pid_mark):].strip()))
            except ValueError:
                pass  # не наш формат — гасим тогда только съёмщик
            continue
        if line.startswith("#ready ") or line.startswith("#done "):
            continue                       # служебные метки съёмщика — не в лог
        if line.startswith("#gpu "):
            # Чем рисует браузер (аппаратно или запасным путём) — строка СТАРТА, не
            # кадра: к первому кадру она уже не нужна, а в хвосте диагностики заняла бы
            # место причины падения.
            continue
        if _timing_line(line) is not None:
            emit(line)                     # разбивка времени — в лог, но не в хвост ошибки
            continue
        # `#ошибка …` и `#консоль …` — это ДИАГНОСТИКА (падение страницы рендера):
        # их глотать нельзя, иначе «съёмщик упал» приходит без причины вовсе.
        tail = state["tail"]
        tail.append(line)
        if len(tail) > 40:
            del tail[0]
        emit(line)


def _read_lines(stream: Any, sink: list[str]) -> None:
    """Читать stderr процесса построчно в копилку — своим потоком.

    Полная труба stderr останавливает сам процесс (ffmpeg встал бы на записи ошибки и
    «завис»), а `communicate` тут не годится: он читает только после конца процесса, и
    причина падения пришла бы с опозданием на весь рендер.
    """
    if stream is None:
        return
    try:
        for raw in stream:
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                sink.append(line)
                if len(sink) > 40:
                    del sink[0]
    except Exception:
        pass  # труба закрылась вместе с процессом — прочитанное уже в копилке


def _tail_text(lines: list[str], limit: int = 8) -> str:
    """Хвост накопленных строк — для сообщения об ошибке."""
    return "\n".join(lines[-limit:])


def _segment_cmd(seg: str, codec_args: list[str], color_args: list[str],
                 fps: float) -> list[str]:
    """Командная строка кодировщика ОДНОГО куска: кадры съёмщика -> сегмент.

    Порядок здесь — часть контракта: `-f image2pipe -c:v mjpeg` описывают ВХОД
    (кадры едут JPEG'ом, см. CAPTURE_FORMAT), дальше идёт цветовой фильтр перевода
    (`-vf`: RGB снимка -> YUV в видеодиапазоне BT.709, см. core/encoders), и только
    потом параметры кодека. Фильтр ПОСЛЕ входа и ДО кодека: он относится к выводу, а
    не к вводу, и, поставленный раньше, он просто не нашёлся бы (ffmpeg читает его
    как опцию входа и падает на «Option vf not found»).
    """
    return ["ffmpeg", "-y", "-v", "error", "-f", "image2pipe",
            "-framerate", "%g" % fps, "-c:v", "mjpeg", "-i", "-",
            *color_args, *codec_args, "-an", seg]


def _start_chunk(ctx: _Ctx, k: int, first: int, count: int, seg: str) -> _ChunkJob:
    """Запустить кусок: свой Chrome, свой кодировщик сегмента и свой поток кадров.

    Возвращается СРАЗУ после запуска: кадры читает отдельный поток, а цикл обязан
    успеть стартовать остальные куски. Иначе экземпляры идут по очереди и рендер
    занимает столько же, сколько один экземпляр (живой прогон: 300 кадров, 4
    экземпляра — 59 с, второй Chrome стартовал только на кадре 210).

    Профиль Chrome — свой у каждого куска: одному браузеру профиль занят, и два
    экземпляра с одним профилем не поднялись бы вовсе.

    Папка картинок этого куска (`ctx.frame_dir`) уезжает в адрес страницы: без неё
    страница перематывает `<video>`, с ней — рисует камеру из картинки (см. шапку).
    Вместе с папкой уезжает ДИАПАЗОН кадров куска: по нему страница не просит картинку
    за концом куска (её там нет — там уже другой кусок), см. `_chunk_url`.
    """
    profile = os.path.join(ctx.work, "chrome_%d" % k)
    url = _chunk_url(ctx.url, ctx.frame_dir, first, count)
    cap = subprocess.Popen(
        _capture_cmd(ctx.node, url, ctx.w, ctx.h, ctx.fps, first, count, profile,
                     ctx.chrome),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, **task_popen_kwargs())
    cap_err = threading.Thread(target=_pump_stderr, args=(cap, ctx.state, ctx.emit),
                               daemon=True)
    cap_err.start()
    # Кадры съёмщика — JPEG, а кодировщик и параметры — те же, что у остальных кусков:
    # сегменты обязаны сойтись при склейке. Цветовой фильтр и теги у ВСЕХ кусков одни
    # и те же (`ctx.color_args`): склейка идёт `-c copy`, и разойдись они — цвет менялся
    # бы на стыке кусков.
    ff = subprocess.Popen(_segment_cmd(seg, ctx.codec_args, ctx.color_args, ctx.fps),
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE)
    ff_err = threading.Thread(target=_read_lines, args=(ff.stderr, ctx.ff_err), daemon=True)
    ff_err.start()
    job = _ChunkJob(first=first, count=count, seg=seg, cap=cap, ff=ff,
                    cap_err=cap_err, ff_err=ff_err, frame_dir=ctx.frame_dir)
    job.pump = threading.Thread(target=_pump_chunk, args=(job, ctx, k), daemon=True)
    job.pump.start()
    return job


def _url_path(path: str) -> str:
    """Путь для адреса страницы: прямые слэши.

    Папку картинок собирает `os.path.join` (на Windows это «\\»), а страница клеит к ней
    имя файла через «/» — и путь с обратными слэшами дал бы «C:\\…\\frames_0/c0_1.jpg».
    Роут `/api/media` принимает оба вида, но разнобой в путях — лишний повод для
    расхождения, поэтому слэши приводит тот, кто отдаёт путь.
    """
    return os.path.abspath(path).replace("\\", "/")


def _chunk_url(url: str, frame_dir: str, first: int, count: int) -> str:
    """Адрес страницы куска: папка картинок и ГРАНИЦЫ его кадров (см. FRAMES_PARAM).

    Границы нужны странице для примерки кадра (`ipvFramePrime` в static/app/85-inserts-view.js):
    кадр грузится на один вперёд, и на последнем кадре куска примерка уходила в СЛЕДУЮЩИЙ
    кусок — картинки для него в этой папке нет (её вынимает другой экземпляр), сервер
    отвечает 404, а пропажа помечала камеру «без картинок» до конца куска: на куске в
    300 кадров это 149 кадров перемотки `<video>` вместо картинок (seek 94 мс против 22).

    Папки нет — адрес не трогаем вовсе: картинок нет, и подставлять пустые значения нечего.
    Оба числа — номера кадров ролика: те же, что считает `_frame_chunks`. Рядом едет
    РАСШИРЕНИЕ картинок (`FRAME_EXT_PARAM`): имя файла собирает страница, и своей копии
    «.jpg» у неё нет — иначе после смены формата она просила бы несуществующие файлы.
    """
    if not frame_dir:
        return url
    return "%s&%s=%s&%s=%d&%s=%d&%s=%s" % (url, FRAMES_PARAM,
                                           urllib.parse.quote(_url_path(frame_dir)),
                                           FRAME_START_PARAM, int(first),
                                           FRAME_COUNT_PARAM, int(count),
                                           FRAME_EXT_PARAM, FRAME_EXT.lstrip("."))


def _pump_chunk(job: _ChunkJob, ctx: _Ctx, k: int) -> None:
    """Поток куска: кадры съёмщика — в СВОЙ кодировщик, со счётом в общий прогресс.

    `k` — номер куска: по нему общий счётчик кадров знает, чьи кадры пришли (см.
    _Progress), и в лог уходит сумма готового по всем кускам, а не последний кадр.

    Кусок не доснял — поднимаем `abort`: ролик всё равно не полный, и доигрывать
    остальные куски значило бы ждать впустую. Причину разбирает `render` по кодам
    возврата и счёту кадров: поток ошибку наружу не отдаёт (её бы никто не поймал).

    Картинки куска убираются ЗДЕСЬ: кадры доехали до кодировщика, и страница своё уже
    отсняла. Держать их до конца ролика незачем — это сотни килобайт на кадр, по папке
    на каждый экземпляр Chrome. Не убралось (файл ещё читает сервер) — папку доберёт
    общая уборка рабочей папки.
    """
    try:
        n = _pump_frames(job.cap, job.ff, job.count, ctx.progress, k, ctx.cancel, ctx.abort)
        job.frames = n
        if n < job.count:
            ctx.abort.set()
    finally:
        if job.frame_dir:
            shutil.rmtree(job.frame_dir, ignore_errors=True)


def _pump_frames(proc: subprocess.Popen[bytes], ff: subprocess.Popen[bytes], count: int,
                 progress: _Progress, chunk: int, cancel: Callable[[], bool] | None,
                 abort: threading.Event) -> int:
    """Кадры из stdout съёмщика — в stdin ЕГО кодировщика. Кадр = 4 байта длины + данные.

    Пишем по мере прихода: ffmpeg кодирует параллельно со съёмкой, а в памяти не
    держится ни один кадр (1080x1920 JPEG — сотни килобайт на каждый). Кусков
    несколько, и у КАЖДОГО своя пара «съёмщик — кодировщик»: кадры разных кусков
    встречаются только в конце, на склейке (`_concat_cmd`).

    Трубу закрываем сами (finally): без EOF кодировщик ждал бы кадров, которых уже не
    будет, и рендер встал бы навсегда.

    `progress` — счётчик кадров ВСЕГО ролика, общий на все куски (см. _Progress), а
    `chunk` — номер ЭТОГО куска: по нему счётчик знает, сколько кадров ролика готово,
    а не «сколько пришло последним» — иначе процент скакал бы между экземплярами.

    `abort` — «другой кусок уже оборвался»: свой тогда бросаем, не досняв.

    Отмена и оборванный съёмщик — это `break`, а не исключение: поток, бросивший
    исключение, некому поймать (ошибку разбирает render по кодам возврата и счёту
    кадров), а «Стоп» главный поток видит по `cancel` сразу после ожидания потоков.

    Оборванная запись — не выход: причина придёт из кода возврата ffmpeg, и её разбор
    (после съёмщика, см. render) покажет пользователю именно её.
    """
    assert proc.stdout is not None and ff.stdin is not None
    out = ff.stdin
    n = 0
    try:
        while n < count:
            if cancel is not None and cancel():
                break       # «Стоп»: кадры куска больше не нужны, коды разберёт render
            if abort.is_set():
                break       # чужой кусок оборвался: причина придёт из его кода возврата
            head = _read_exact(proc.stdout, HDR.size)
            if head is None:
                break
            (size,) = HDR.unpack(head)
            data = _read_exact(proc.stdout, size)
            if data is None:
                break
            try:
                out.write(data)
            except OSError:
                break       # кодировщик закрыл трубу: причина придёт из его кода возврата
            n += 1
            progress.add(chunk, n)
    finally:
        _close_pipe(out)
    return n


def _drain(stream: Any) -> str:
    """Хвост вывода процесса — для сообщения об ошибке."""
    if stream is None:
        return ""
    try:
        return stream.read().decode("utf-8", "replace").strip()[-600:]
    except Exception:
        return ""


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _parse(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="python -m core.webrender",
        description="Рендер ролика без After Effects: кадры рисует предпросмотр, снимает Chrome")
    ap.add_argument("xml", help="XML клипа (нарезка)")
    ap.add_argument("--out", required=True, help="куда писать mp4")
    ap.add_argument("--start", type=float, default=None, help="с какой секунды (по умолчанию с начала)")
    ap.add_argument("--dur", type=float, default=None, help="сколько секунд (по умолчанию до конца)")
    ap.add_argument("--body", default=None,
                    help="JSON-файл с телом сборки (то же, что шаг 3 шлёт на /api/scene)")
    ap.add_argument("--host", default=DEFAULT_HOST, help=f"адрес запущенного webui (по умолчанию {DEFAULT_HOST})")
    ap.add_argument("--chrome", default=None, help="путь к chrome.exe (иначе ищется сам)")
    ap.add_argument("--node", default="node", help="интерпретатор node")
    ap.add_argument("--keep-profile", action="store_true",
                    help="не убирать временный профиль Chrome (для разбора)")
    ap.add_argument("--instances", type=int, default=0,
                    help="экземпляров Chrome на ролик (0 — по умолчанию: половина ядер, максимум 4)")
    ap.add_argument("--no-audio", action="store_true",
                    help="не сводить звук: ролик без звуковой дорожки (проверка картинки)")
    return ap.parse_args(argv)


def _console_utf8() -> None:
    """Русский текст в консоль: у Python на Windows stdout — cp1252/cp1251, и `print`
    падал `UnicodeEncodeError: 'charmap' codec can't encode characters` на первой же
    строке лога. Та же починка, что у остальных CLI проекта (core/omni_cut.py,
    core/verify_jsx.py, core/umsg.cli_error): переключаем кодировку потока на UTF-8.
    """
    try:
        # В типах sys.stdout — TextIO, а reconfigure есть только у TextIOWrapper:
        # на практике это он и есть, но проверку типов это не устраивает.
        cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")
    except ReelsiError:
        raise
    except Exception:
        pass  # поток без reconfigure (перенаправлен) — печатаем как есть


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    # ДО первой строки лога: emit по умолчанию (console_emit) печатает в sys.stdout, и
    # без переключения кодировки русский лог валит CLI на cp1252-консоли.
    _console_utf8()
    body: dict[str, Any] | None = None
    if args.body:
        try:
            with open(args.body, encoding="utf-8") as f:
                body = json.load(f)
        except Exception as e:
            raise ReelsiError(umsg("webrender_body_file", f"Тело сборки не прочиталось: {e}", err=str(e)))
        if not isinstance(body, dict):
            raise ReelsiError(umsg("webrender_body_file", "Тело сборки должно быть объектом JSON"))
    # emit — общий путь логов проекта: он переводит шаблон по словарю и печатает в stdout.
    res = render(args.xml, args.out, start=args.start, dur=args.dur, body=body,
                 host=args.host, emit=console_emit, chrome=args.chrome,
                 node=args.node, keep_profile=args.keep_profile,
                 instances=args.instances, audio=not args.no_audio)
    # Хвост лога — размер кадра и частота, а не «{fps}x{w}»: «60x1080» читалось как
    # «60 на 1080» и не говорило ни разрешения, ни кадров в секунду (порядок был чужой
    # обоим). Числа — те же, что в первой строке рендера (w×h и fps из плана).
    console_emit("готово: {out} — кадров {n}, {w}x{h}, {fps:g} к/с", out=res["out"],
                 n=res["frames"], w=res["w"], h=res["h"], fps=res["fps"])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ReelsiError as e:
        cli_error(e)
