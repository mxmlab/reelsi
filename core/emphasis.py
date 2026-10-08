# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сила жёлтых слов: эмоция фразы (GigaAM-Emo) + ударение по звуку.

Зачем отдельный модуль. Жёлтых слов ИИ ставит десятки, и наезд на КАЖДОЕ превращал
ролик в непрерывный зум: «как только камера 2, сразу наезд и не уходит назад» (решение
владельца 02.10.2026). Поэтому сила слова считается здесь, а правило наезда берёт из неё
одно-два самых сильных (см. `core/xml2ae/layout.py`, `_take_zoom_segment_keys`).

Сила слова — две половины:

* **эмоция фразы** — модель `gigaam` с головой `emo` (`load_model("emo")`,
  `emotion_probs(окно)` -> angry/sad/neutral/positive; массив, а не файл — см. её
  докстринг), `emo = 1 - p(neutral)` в окне
  `EMO_WIN_S` с центром на слове. Замер владельца: окно 2.5 с — 65 мс на GPU,
  нейтральное 0.98-1.0, эмоциональное angry 0.77;
* **ударение по звуку** — громкость (RMS, дБ), высота тона (`librosa.yin`) и
  растянутость слога относительно СОСЕДНИХ слов (±`STRESS_NEIGHBORS`). Слоги считаются
  пиками огибающей громкости, поэтому длительность слова делится на их число.
  Тон и RMS считаются ОДИН раз на объединённое окно чтения (`_merge_windows`), а слово
  берёт из готовых массивов срез по своему времени: пословный `pyin` (с Витерби) считал
  то же самое заново на каждом слове и на клипе с полусотней жёлтых съедал 141 с из 145
  (замер архитектора), тогда как `yin` без Витерби — доли секунды на окно. В тон идут
  только ОЗВУЧЕННЫЕ кадры — громче опорного уровня окна на `VOICED_DROP_DB`: на паузах
  `yin` выдаёт случайную частоту, и без порога тон слова уезжал бы на шум паузы.

Звук — голос Камеры 1 в ИСХОДНОМ времени под словом. Сопоставление «слово монтажа ->
кусок исходника» взято один в один у дорожки голоса: `voice_segments` из
`core/xml2ae/plan_audio.py` склеивает клипы Камеры 1 тем же условием
(`|src + (te - ts) - in| < eps`). Куски считаются в КАДРАХ (в них живут клипы XML), а
монтажные времена слов — в секундах; кусок берётся тот, что накрывает СЕРЕДИНУ слова
(тот же выбор, что у `refine_keep`).

Кэш — сайдкар `<стем>.emph.json` рядом с XML. Ключ: исходник Камеры 1 и его mtime,
набор оценённых слов (индекс, время, текст), набор жёлтых, версия формулы. Нумер слов —
КАК У ПЛАНА (слова после вырезания интро плюс слова интро, продолжающие ряд): план
читает силы тем же нумером (`plan_camera._yellow_need`), и разойтись они не могут. Шаг
ИИ-жёлтых считает по исходному списку XML — на ролике с интро сайдкар после разметки
интро пересчитывается один раз (ключ другой), и это дешевле, чем путать индексы.

Сайдкар хранит ОБЕ составляющие по каждому слову (`emo` и `stress`), а какой из них
считать силой, решает ПЛАН по ключу стиля `hl_zoom_strength` («по голосу» / «по
эмоциям», см. `plan_camera`). Переключение способа поэтому ничего не пересчитывает:
числа уже лежат рядом, меняется только выбор в плане. Компонента, которой в сайдкаре
нет (в режиме «по голосу» модель эмоций не грузилась), читается как отсутствующая —
план честно вернётся к прежнему правилу вместо молчаливого нуля.

Звук исходника читается ОКНАМИ вокруг нужных слов (`audio_windows`): окно эмоции
`EMO_WIN_S` с центром на слове (и окно ударения на само слово — минус-один кадр с
каждой стороны). Раньше исходник читался целиком, и память со временем росли с его
длиной; на длинной записи это и было главной ценой расчёта.

Модель живёт в СЕРВИСЕ моделей (`core/model_service.py`): он держит голову `emo` в
памяти и считает окна в слотах, а расчёт шлёт ему окно и получает вероятности
(`ServiceEmo`). Так каждый расчёт перестал платить за чтение весов и `import torch`
— раньше `load_emo_model` шёл на каждый расчёт. Сервиса нет — прежний путь: модель
грузится здесь и сразу выгружается, потому что на Windows переполнение VRAM не даёт
честный OOM, оно вешает машину.

Когда что считается:

* в конце шага ИИ-жёлтых (`aicut.cmd_yellow`) — сразу после покраски XML;
* лениво при сборке (`core/xml2ae/precompute.py`, `emphasis_precompute`);
* план сцены (`/api/scene`) сайдкар только ЧИТАЕТ (`read_emphasis`): нет сайдкара —
  прежнее поведение и строка «сила жёлтых не посчитана» в лог.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from core import fileio
from core.applog import get_logger

log = get_logger(__name__)

# Версия формулы силы: меняешь веса или окна — поднимай, иначе старый сайдкар
# переживёт правку и наезды останутся посчитанными по прежнему правилу. Версия 2 —
# сайдкар хранит обе составляющие по слову (`emo` и `stress`), а не одну готовую силу.
# Версия 3 — тон считается `librosa.yin` по озвученным кадрам (`F0_MIN_HZ`…`F0_MAX_HZ`):
# числа f0 сменились, и сайдкар версии 2 обязан пересчитаться, а не доживать свой век.
EMPH_VERSION = 3

# Окно эмоции: центр — середина слова, ширина 2.5 с (замер владельца: 65 мс на GPU).
EMO_WIN_S = 2.5

# Частота, на которой обучена GigaAM-Emo (`gigaam.preprocess.SAMPLE_RATE`): звук проекта
# читается в своей частоте (22050), модель ждёт ровно эту.
EMO_SR = 16000

# Соседи по слову для z-оценки ударения: ±5 слов.
STRESS_NEIGHBORS = 5

# Способы оценки силы жёлтых (ключ стиля `hl_zoom_strength`):
#   emotion — только эмоция фразы (GigaAM-Emo);
#   voice   — только ударение по звуку: громкость, тон, растянутость.
# По умолчанию — эмоции (решение владельца 02.10.2026).
HL_MODE_EMOTION = "emotion"
HL_MODE_VOICE = "voice"
HL_MODE_DEFAULT = HL_MODE_EMOTION


def hl_mode(value: Any) -> str:
    """Способ оценки силы из ключа стиля: «voice» — по голосу, всё прочее — по эмоциям.

    Гейт один: ключа нет / мусор / старое значение — эмоции (дефолт владельца).
    """
    return HL_MODE_VOICE if str(value or "").strip().lower() == HL_MODE_VOICE else HL_MODE_DEFAULT


# Имя КОМПОНЕНТЫ сайдкара для способа оценки. Ключ стиля и имя компоненты — разные
# вещи: смена первого не должна менять формат сайдкара. Одна дверь на обе стороны
# (`compute_emphasis` пишет, `read_emphasis` читает) — иначе стороны разъедутся молча.
HL_COMPONENT = {HL_MODE_EMOTION: "emo", HL_MODE_VOICE: "stress"}


def component_name(mode: Any) -> str:
    """Имя компоненты сайдкара для способа оценки: `emo` (эмоции) или `stress` (голос)."""
    return HL_COMPONENT[hl_mode(mode)]

# Веса ИТОГОВОЙ силы (константы с комментарием, как просили в задании).
# Эмоция и акустика равноправны: сильное слово — это «фраза эмоциональна И слово
# ударили». Одна половина без второй в «сильные» не вытянет. Порог «слабого» слова в
# правиле наезда берётся из данных (медиана по клипу), а не отсюда.
W_EMO = 0.5
W_STRESS = 0.5

# Веса ВНУТРИ ударения: громкость, тон и длительность слога равноправны (по 1/3).
W_RMS = 1.0 / 3.0
W_F0 = 1.0 / 3.0
W_DUR = 1.0 / 3.0

# Хвост затухания z-оценки: z = 0 -> 0.5 (обычное слово), z = ±2 -> 0.88/0.12.
# Без сигмоиды z-оценка бывает до ±4, и `0.5·emo + 0.5·norm(...)` уезжала бы за [0,1],
# а по силе сравниваются слова РАЗНЫХ клипов (порог — медиана по клипу).
_Z_TAIL = 2.0

# Минимальная длительность слога: пики огибающей ближе 120 мс — один слог (быстрее
# человек слог не проговаривает; иначе дрожание огибающей давало бы «растянутость»).
SYL_MIN_S = 0.12

# Огибающая для слогов: 20 мс — тот же порядок, что у VAD и маски речи.
ENV_HOP_S = 0.02

# Диапазон тона: 65–500 Гц — человеческий голос с запасом вверх (женский/детский).
# Границы те же, что заданы для `librosa.yin` при замене pyin (02.10.2026): шире старого 70–400, а
# ниже 65 Гц `yin` на 1024 отсчётах всё равно не различит период.
F0_MIN_HZ = 65.0
F0_MAX_HZ = 500.0

# Шаг кадров тона: ~11.6 мс при 22050 Гц — тот же порядок, что у пиков слогов.
F0_HOP_S = 0.0116

# Окно `yin`: не меньше двух периодов самой низкой частоты диапазона (иначе функция
# честно возвращает NaN на всём) и не меньше кадра 1024 при 22050 Гц. Поднимаем до
# степени двойки — так быстрее FFT.
F0_FRAME_MIN = 1024

# Порог «озвученности» кадра: на `VOICED_DROP_DB` тише ОПОРНОГО уровня окна кадр в тон
# не идёт. `yin` шумит и на паузах выдаёт случайную частоту — без порога медиана тона
# слова уезжала бы на шум паузы, а не на голос.
VOICED_DROP_DB = 20.0

# Опорный уровень — процентиль RMS (дБ), а не медиана: окно чтения вокруг жёлтых бывает
# БОЛЬШЕЙ частью паузой (слово плюс ±5 соседей), и медиана такого окна — тишина: порог
# «медиана − 20 дБ» пропускал бы в тон шум паузы (проверено на 2 с синуса в 12 с окне).
# Процентиль же держится громких кадров речи и от доли паузы не зависит.
VOICED_REF_PCT = 90.0

# Точность сравнения кадров при склейке кусков голоса (доли кадра).
_EPS_F = 1e-6


@dataclass(frozen=True)
class WordRef:
    """Слово ролика: индекс в разметке, текст и времена (сек, монтажное время)."""
    idx: int
    text: str
    start: float
    end: float


@dataclass
class EmphasisView:
    """Что прочитано из сайдкара для текущего клипа.

    Проверить сайдкар мало — надо знать, ПОКРЫВАЕТ ли он нужные слова. Набор жёлтых
    правится руками (`/api/set_yellow`), и слова, добавленного после расчёта, в сайдкаре
    нет: тогда наезд обязан вернуться к прежнему поведению, а не молча счесть слово
    «слабым». `uncomputed` — индексы, для которых посчитанного нет.

    `scores` — сила, ВЫБРАННАЯ способом (`mode`): план отдаёт её правилу наезда.
    `components` — обе сырые составляющие по каждому слову ({индекс: {"emo", "stress"}}):
    по ним переключение способа ничего не пересчитывает. Компонента, которой в сайдкаре
    нет, в `scores` не попадает — и слово честно числится непосчитанным.
    """
    scores: dict[int, float]
    # Сайдкар есть, версия/исходник/слова/жёлтые совпали.
    valid: bool
    # Нужные слова, которых в сайдкаре нет (при valid=True).
    uncomputed: list[int] = field(default_factory=list)
    # Обе составляющие по каждому слову сайдкара: {индекс: {"emo": float|None, "stress": float|None}}.
    components: dict[int, dict[str, float | None]] = field(default_factory=dict)
    # Каким способом выбрана `scores`.
    mode: str = HL_MODE_DEFAULT


@dataclass
class EmphasisInputs:
    """Вход расчёта силы: слова ролика, разбор XML, путь XML и сами камеры.

    `parsed` — результат `core.xml2ae.parse_full` (meta, cams, subs, xml_inserts): из
    него берутся клипы Камеры 1 и путь её исходника. `words` — слова транскрипта монтажа
    в порядке `subs` (индекс = индекс слова в разметке).

    `audio_for` — ручка для тестов (заглушка звука): `(путь исходника) -> (samples, sr)`.
    В бою звук берётся из исходника Камеры 1 (`core.sync.extract_audio` + `librosa.load`).
    """
    words: Sequence[WordRef]
    parsed: tuple[Any, Any, Any, Any]
    xml_path: str
    # Какие слова оценивать: индексы жёлтых ролика. None — все слова (фолбэк).
    idx: Sequence[int] | None = None
    # Слова интро вне `words`: у них свой индексный ряд (len(words) + j) — ровно как их
    # собирает `_yellow_phrases` камеры. Времена — в КАДРАХ (как их отдаёт
    # `plan_intro.intro_hl_words`), в секунды переводятся при поиске куска исходника.
    intro_words: Sequence[tuple[float, float]] = ()
    # Способ оценки (ключ стиля `hl_zoom_strength`): "voice" — считать только ударение
    # и НЕ грузить модель эмоций; "emotion" — считать обе составляющие (переключение
    # способа потом ничего не пересчитывает).
    mode: str = HL_MODE_DEFAULT
    # Заглушка звука в тестах: (путь исходника) -> (моно float32, частота).
    audio_for: Callable[[str], tuple[Any, int]] | None = None
    # Заглушка чтения ОКНА звука: (путь исходника, начало, конец) -> (моно float32, частота).
    # Боевое чтение — `_read_window` (ffmpeg -ss/-t по отрезку), из-за чего длинный
    # исходник не читается целиком.
    audio_window: Callable[[str, float, float], tuple[Any, int]] | None = None
    sample_rate: int = 22050
    emit: Any = None
    cancel: Callable[[], bool] | None = None


# --------------------------------------------------------------------------- #
# Монтажное время -> исходное время Камеры 1
# --------------------------------------------------------------------------- #
def _voice_segments_frames(clips: Sequence[Any]) -> list[dict[str, float]]:
    """Куски голоса Камеры 1 в КАДРАХ: [{ts, te, src}] — то же правило, что
    `plan_audio.voice_segments` (клипы без звука и пустые пропускаются, соседние куски,
    стыкующиеся и в монтаже, и в исходнике, склеиваются).

    Числа те же, что у дорожки голоса, — это стережёт `tests/test_emphasis*.py`.
    """
    out: list[dict[str, float]] = []
    for c in clips or []:
        try:
            s, e, i = float(c[0]), float(c[1]), float(c[2])
        except (IndexError, TypeError, ValueError):
            continue
        if len(c) > 4 and not c[4]:
            continue
        if e <= s:
            continue
        if out and abs(out[-1]["te"] - s) < _EPS_F \
                and abs((out[-1]["src"] + (out[-1]["te"] - out[-1]["ts"])) - i) < _EPS_F:
            out[-1]["te"] = e
            continue
        out.append({"ts": s, "te": e, "src": i})
    out.sort(key=lambda x: x["ts"])
    return out


def word_refs(subs: Sequence[Any], fps: float) -> list[WordRef]:
    """Слова из `parse_full` (кадры) -> `WordRef` (секунды); индекс = порядок `subs`."""
    return [WordRef(idx=k, text=str(w), start=s / fps, end=e / fps)
            for k, (s, e, w) in enumerate(subs)]


def _time_in_source(segs: Sequence[dict[str, float]], start: float, end: float,
                    fps: float) -> tuple[float, float] | None:
    """Время фрагмента [start, end] (сек, монтаж) в исходнике Камеры 1 или None.

    Кусок ищется по СЕРЕДИНЕ фрагмента — тем же выбором, что у `refine_keep`: по
    касанию подтянулся бы соседний кусок, и слово получило бы чужой звук.
    """
    mid = (start + end) / 2.0
    for seg in segs:
        ts, te = seg["ts"] / fps, seg["te"] / fps
        if ts <= mid <= te:
            base = seg["src"] / fps
            return (base + (start - ts), base + (end - ts))
    return None


def _source_times(segs: Sequence[dict[str, float]], words: Sequence[WordRef],
                  fps: float) -> list[tuple[float, float] | None]:
    """Времена ВСЕХ слов в исходнике Камеры 1 (None — слова нет в монтаже камеры)."""
    return [_time_in_source(segs, w.start, w.end, fps) for w in words]


# --------------------------------------------------------------------------- #
# Окна звука: читаем не весь исходник, а отрезки вокруг нужных слов
# --------------------------------------------------------------------------- #
def _merge_windows(windows: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Склеить пересекающиеся/стыкующиеся окна: (начало, конец) по возрастанию.

    Без склейки соседние слова (и окно эмоции, накрывающее ударение) дали бы десятки
    чтений ffmpeg по одному и тому же куску. Пустое или вывернутое окно пропускается.
    """
    out: list[list[float]] = []
    for s, e in sorted((float(s), float(e)) for s, e in windows if float(e) > float(s)):
        if out and s <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def stress_spans(times: Sequence[tuple[float, float] | None], targets: Sequence[int]
                 ) -> dict[int, tuple[float, float]]:
    """Отрезки исходника для ударения: сам блок слов ±`STRESS_NEIGHBORS` соседей.

    Возвращает {индекс слова: (начало, конец)}. Слово без куска в монтаже камеры
    (и его сосед) в результат не попадает: читать там нечего.
    """
    out: dict[int, tuple[float, float]] = {}
    for i in targets:
        near = [t for t in times[max(0, i - STRESS_NEIGHBORS): i + STRESS_NEIGHBORS + 1]
                if t is not None]
        if not near:
            continue
        out[int(i)] = (min(t[0] for t in near), max(t[1] for t in near))
    return out


def audio_windows(spans: Sequence[tuple[float, float]], mode: str,
                  emo_spans: Sequence[tuple[float, float]] = ()
                  ) -> list[tuple[float, float]]:
    """Окна звука под расчёт: склеенные отрезки и окна эмоций с запасом `EMO_WIN_S`.

    Ударение считается по отрезку, который УЖЕ шире слова на соседей (`stress_spans`),
    поэтому по краям хватает одного кадра; окно эмоции — 2.5 с с центром на слове.
    Возвращаются отрезки ПОДРЯД: их число зависит от числа жёлтых, а не от длины
    исходника (это и стережёт `tests/test_emphasis.py`).
    """
    frames = 1.0 / 25.0                      # кадр запаса по краю отрезка ударения
    wins = [(float(s) - frames, float(e) + frames) for s, e in spans]
    if hl_mode(mode) != HL_MODE_VOICE:
        half = EMO_WIN_S / 2.0
        wins += [(float((s + e) / 2.0) - half, float((s + e) / 2.0) + half)
                 for s, e in emo_spans]
    return _merge_windows(wins)


def _read_window(source: str, start: float, end: float, sr: int) -> tuple[Any, int]:
    """Прочитать ОТРЕЗОК звука исходника: ffmpeg -ss/-t, декодируется только он.

    `-ss` стоит ДО `-i`: ffmpeg тогда прыгает по контейнеру, а не декодирует всё
    начало. Отдельного правила «как достать звук» здесь нет: файл готовит та же
    `core.sync` (ffmpeg -vn), читает `librosa.load`. Импорты ленивые: `core.sync`
    тянет numpy/scipy, а модуль импортирует сборка .jsx.
    """
    import librosa
    import tempfile

    from core import sync as _sync
    fd, tmp = tempfile.mkstemp(prefix="_emph_", suffix=".wav")
    os.close(fd)
    try:
        _sync.extract_audio(source, tmp, sr=sr, ss=max(0.0, float(start)),
                            duration=max(0.0, float(end) - float(start)))
        y, got = librosa.load(tmp, sr=sr, mono=True)
        return y, int(got or sr)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass  # временный wav уже убран — чистить нечего


# --------------------------------------------------------------------------- #
# Ударение по звуку: признаки и z-оценка
# --------------------------------------------------------------------------- #
def _sigmoid(z: float) -> float:
    """z-оценка -> [0,1] с хвостом `_Z_TAIL` (см. константу)."""
    import math
    try:
        return 1.0 / (1.0 + math.exp(-float(z) / _Z_TAIL))
    except (OverflowError, ValueError):
        return 0.5 if float(z) == 0 else (1.0 if float(z) > 0 else 0.0)


def _zscore(values: Sequence[float | None], i: int) -> float:
    """z-оценка значения `i` по соседям ±`STRESS_NEIGHBORS` (None пропускаются).

    Медиана и разброс (MAD) вместо среднего и сигмы: речь — не нормальное
    распределение, одно громкое слово сдвигало бы среднее и глушило соседей.
    MAD = 0 (все соседи равны) -> z = 0: слово обычное.
    """
    lo = max(0, i - STRESS_NEIGHBORS)
    window = [v for v in values[lo: i + STRESS_NEIGHBORS + 1] if v is not None]
    cur = values[i]
    if cur is None or len(window) < 2:
        return 0.0
    srt = sorted(window)
    n = len(srt)
    med = srt[n // 2] if n % 2 else (srt[n // 2 - 1] + srt[n // 2]) / 2.0
    mad = sorted(abs(v - med) for v in window)[n // 2]
    if mad <= 1e-9:
        return 0.0
    return (cur - med) / (1.4826 * mad)


def stress_norm(feat: Mapping[int, tuple[float, float, float]],
                idx: Sequence[int]) -> dict[int, float]:
    """Сырые признаки слов -> нормированное ударение [0,1].

    `feat[i]` = (rms_db, f0_hz|None, длительность_слога) — из `prosody`. Тон и
    длительность слога сравниваются с СОСЕДНИМИ словами: абсолютная высота голоса у
    разных людей разная, а «выше соседних слов» — одно и то же. Громкость (дБ) — сама по
    себе относительная величина, но z-оценка и ей не вредит. Слово без признаков (или без
    тона: голос в нём не различился) получает 0.5 — «обычное».
    """
    rms = [feat[i][0] if i in feat else None for i in idx]
    f0 = [feat[i][1] if i in feat else None for i in idx]
    dur = [feat[i][2] if i in feat else None for i in idx]
    out: dict[int, float] = {}
    for k, i in enumerate(idx):
        z = W_RMS * _zscore(rms, k) + W_F0 * _zscore(f0, k) + W_DUR * _zscore(dur, k)
        out[i] = _sigmoid(z)
    return out


def f0_frame_length(sr: int) -> int:
    """Окно `yin` под частоту дискретизации: два периода `F0_MIN_HZ`, не меньше `F0_FRAME_MIN`.

    Заданное в задании окно 1024 — это 46 мс при 22050 Гц (2.2 периода 65 Гц). На 48 кГц
    те же 1024 отсчёта дали бы 21 мс: `yin` на таком окне низкий голос не различает и
    молча отдаёт NaN. Поэтому окно считается от `sr`, а не берётся константой.
    """
    need = int(2.0 * sr / F0_MIN_HZ) + 1
    size = max(F0_FRAME_MIN, need)
    return 1 << (size - 1).bit_length()          # степень двойки — так быстрее FFT


@dataclass(frozen=True)
class ToneTrack:
    """Тон и громкость ОКНА звука, посчитанные один раз: кадры, f0 и RMS по кадрам.

    Массивы одной длины, кадр `k` — это время `k·hop/sr` секунд от начала окна. Работа
    на слово идёт СРЕЗАМИ этих массивов: `prosody` больше не гоняет `yin` по каждому
    слову (на клипе с полусотней жёлтых это стоило 141 с из 145).
    """
    sr: int
    hop: int
    f0: Any                    # float32, NaN — неозвученный/неразличимый кадр
    rms_db: Any                # float32, дБ по кадрам
    voiced: Any                # bool: кадр озвучен (f0 есть и он громче порога)


def tone_track(audio: Any, sr: int) -> ToneTrack:
    """Тон и RMS звука ОКНА: один вызов `librosa.yin` на весь массив.

    `yin` без Витерби (в отличие от `pyin`): на речи он даёт те же медианы тона за доли
    секунды, а Витерби стоил 109 с из 141 на клипе архитектора.

    Кадр считается озвученным, если `yin` различил частоту И его RMS не ниже ОПОРНОГО
    уровня окна минус `VOICED_DROP_DB` (`VOICED_REF_PCT`): на паузах `yin` выдаёт
    случайную частоту, и без порога медиана тона слова уезжала бы на шум паузы, а не на
    голос.
    """
    import numpy as np
    import librosa

    y = np.asarray(audio, dtype="float32")
    sr = int(sr or 22050)
    hop = max(1, int(F0_HOP_S * sr))
    frame_length = f0_frame_length(sr)
    n = int(len(y))
    try:
        f0 = np.asarray(librosa.yin(y, fmin=F0_MIN_HZ, fmax=F0_MAX_HZ, sr=sr,
                                    frame_length=frame_length, hop_length=hop),
                        dtype="float32")
        rms = np.asarray(librosa.feature.rms(y=y, frame_length=frame_length,
                                             hop_length=hop)[0], dtype="float32")
    except Exception as ex:
        # Звук короче окна `yin` или битый: тон не считается вовсе, признаки слов будут
        # без f0 (слово останется со громкостью и слогами). Ронять расчёт нельзя.
        log.debug("yin не посчитался на окне %d отсчётов: %s", n, ex)
        empty = np.zeros(0, dtype="float32")
        return ToneTrack(sr=sr, hop=hop, f0=empty, rms_db=empty,
                         voiced=np.zeros(0, dtype=bool))
    m = min(int(f0.size), int(rms.size))         # длины могут разойтись на кадр — берём общее
    f0, rms = f0[:m], rms[:m]
    db = 20.0 * np.log10(rms + 1e-9)
    if m:
        thr = float(np.percentile(db, VOICED_REF_PCT)) - VOICED_DROP_DB
        voiced = np.isfinite(f0) & (db > thr)
    else:
        voiced = np.zeros(0, dtype=bool)
    return ToneTrack(sr=sr, hop=hop, f0=f0, rms_db=db, voiced=voiced)


def _frame_index(sample: int, hop: int) -> int:
    """Отсчёт звука -> кадр тона: кадр, накрывающий этот отсчёт.

    `yin` центрирует кадр, поэтому время кадра — просто `frame·hop`; округление вниз
    оставляет кадр, чей центр не ПОЗЖЕ отсчёта (тот же выбор, что у среза массива).
    """
    return max(0, int(sample) // max(1, int(hop)))


def _word_f0(track: ToneTrack, a0: int, a1: int) -> float | None:
    """Тон слова: медиана f0 ОЗВУЧЕННЫХ кадров его среза; None — голоса в слове нет.

    None (а не 0.0): ноль — это «тон нулевой герц», и в z-оценке такое число тянуло бы
    вниз соседей. Слово без тона честно выпадает из признака `f0`.
    """
    import numpy as np

    if track.voiced.size == 0:
        return None
    k0 = _frame_index(a0, track.hop)
    k1 = _frame_index(max(a0, a1 - 1), track.hop) + 1     # правый край ВКЛЮЧИТЕЛЬНО
    k0 = min(k0, track.voiced.size)
    k1 = min(k1, track.voiced.size)
    if k1 <= k0:
        return None
    mask = track.voiced[k0:k1]
    vals = track.f0[k0:k1][mask]
    vals = vals[np.isfinite(vals)] if vals.size else vals
    if not vals.size:
        return None
    return float(np.median(vals))


def prosody(audio: Any, sr: int, spans: Sequence[tuple[int, float, float]]
            ) -> dict[int, tuple[float, float | None, float]]:
    """Признаки слов по звуку: {индекс: (rms_db, f0_hz|None, длительность_слога)}.

    `audio` — моно float32, `spans` — (индекс, начало, конец) в секундах ОТ НАЧАЛА
    массива. Слово без звука (за краем массива, короче 20 мс) в результат не попадает —
    вызывающий считает его нейтральным.

    Тон и RMS считаются ОДИН раз на весь переданный звук (`tone_track`), а слово берёт
    срез готовых массивов: окно чтения уже объединено `_merge_windows`, и пословный
    проход по нему не нужен. Слоги — пики огибающей громкости не ближе `SYL_MIN_S`.
    """
    import numpy as np
    import librosa
    from scipy.signal import find_peaks

    y = np.asarray(audio, dtype="float32")
    hop = max(1, int(ENV_HOP_S * sr))
    track = tone_track(y, sr)
    out: dict[int, tuple[float, float | None, float]] = {}
    for i, s, e in spans:
        a0 = max(0, min(int(round(s * sr)), len(y)))
        a1 = max(0, min(int(round(e * sr)), len(y)))
        dur = (a1 - a0) / float(sr)
        if dur < ENV_HOP_S:
            continue
        seg = y[a0:a1]
        rms = float(np.sqrt(np.mean(np.square(seg)))) if seg.size else 0.0
        db = 20.0 * float(np.log10(rms + 1e-9))
        env = np.abs(librosa.feature.rms(y=seg, hop_length=hop)[0])
        if env.size:
            peaks, _p = find_peaks(env, distance=max(1, int(SYL_MIN_S * sr / hop)))
            syl = max(1, len(peaks))
        else:
            syl = 1
        out[i] = (db, _word_f0(track, a0, a1), dur / float(syl))
    return out


# --------------------------------------------------------------------------- #
# Эмоция фразы (GigaAM-Emo)
# --------------------------------------------------------------------------- #
class ServiceEmo:
    """Голова `emo` в СЕРВИСЕ моделей: счёт идёт там, а не в этом процессе.

    Раньше каждая сборка читала веса заново (`load_emo_model`), считала и сразу
    выгружала (`release_emo`) — то есть на каждый расчёт платила за чтение головы.
    Сервис держит веса в памяти и считает их в слотах, а ролик не платит ни за
    чтение весов, ни за `import torch`; выгрузкой занят простой сервиса, поэтому
    `release` у модели из сервиса ничего не делает.

    Отказал сервис посреди расчёта (упал, не прочитал веса) — считаем здесь:
    `local()` поднимает модель этого процесса ровно один раз. Сила жёлтых — не
    повод ронять сборку, и «сервиса нет» уже случалось (фаза 1).
    """

    def __init__(self) -> None:
        self._local: Any = None

    def probs(self, window: Any, sr: int) -> Mapping[str, float]:
        """Вероятности эмоций окна: их считает сервис, иначе — своя модель."""
        from core import model_service
        try:
            return model_service.emotion_probs(window, sr)
        except model_service.ServiceUnavailable as ex:
            log.warning("сервис моделей отказал на эмоциях (%s) — считаю здесь", ex)
            return emotion_probs(window, self.local(), sr)

    def local(self) -> Any:
        """Модель ЭТОГО процесса — поднимается, только если сервис отказал."""
        if self._local is None:
            self._local = load_emo_model_local()
        return self._local

    def release(self) -> None:
        """Отпустить локальную модель, если она поднималась (веса сервиса — не наши)."""
        if self._local is not None:
            local, self._local = self._local, None
            release_emo(local)


def load_emo_model_local() -> Any:
    """Прочитать голову `emo` в ЭТОМ процессе — без кеша и без сервиса.

    Дверь двух путей: запасного (`load_emo_model`, когда сервиса нет) и самого
    сервиса моделей, который держит веса у себя. Импорт ленивый: без `gigaam`
    модуль всё равно импортируется. Отдельно проверяем наличие пакета:
    `ImportError` из `import gigaam` — понятная причина, а не «не посчиталось», и
    в логе она так и звучит.
    """
    import importlib.util
    if importlib.util.find_spec("gigaam") is None:
        raise RuntimeError("пакет gigaam не установлен — головы emo нет")
    import gigaam
    from core.gigaam_cache import gigaam_dir
    # download_root: головы GigaAM (emo в том числе) качаются в ту же папку, что и
    # нарезка — она одна на приложение, а не `~/.cache/gigaam` у пакета.
    return gigaam.load_model("emo", download_root=gigaam_dir())


def load_emo_model() -> Any:
    """Модель эмоций: голова `emo` в СЕРВИСЕ моделей, если он отвечает.

    Возвращает либо обёртку сервиса (`ServiceEmo`), либо настоящую модель этого
    процесса. Форма ответа одна: `emotion_probs` узнаёт обёртку и зовёт сервис,
    поэтому у вызывающего (`compute_emphasis`) ветки нет вовсе.

    Сервиса нет (не стартовал, весов у него нет) — прежний путь: веса здесь, и
    `release_emo` отпускает их сразу после расчёта.
    """
    from core import model_service
    if model_service.preload_emo():
        return ServiceEmo()
    log.info("сервис моделей недоступен — голова emo грузится в этом процессе")
    return load_emo_model_local()


def release_emo(model: Any) -> None:
    """Выгрузить модель эмфазы из видеопамяти.

    На Windows переполнение VRAM не даёт честный OOM — оно вешает машину, поэтому
    модель отпускается СРАЗУ после расчёта (как RVM в `core.roto.release`). Модель
    из сервиса отпускать нечем: её веса живут в сервисе, выгрузкой занят его
    простой; у обёртки отпускается только её собственная, запасная модель.
    """
    if isinstance(model, ServiceEmo):
        model.release()
        return
    try:
        del model
    except Exception:
        pass  # удалять нечего — это не повод падать
    from core.gigaam_cut.asr import _free_torch
    _free_torch()


def emotion_probs(window: Any, model: Any, sr: int) -> Mapping[str, float]:
    """Вероятности эмоций ОКНА ЗВУКА: {angry, sad, neutral, positive}.

    `model` — либо настоящая голова `emo`, либо `ServiceEmo` (веса живут в сервисе):
    во втором случае окно уезжает туда, а имена эмоций и формула остаются прежними —
    их считает этот же код на стороне сервиса.

    `get_probs` у GigaAM-Emo принимает ПУТЬ к файлу: внутри `prepare_wav` ->
    `load_audio` -> ffmpeg, а окно здесь — уже прочитанный массив. Отдать массив в
    `get_probs` нельзя, и в живом прогоне это и падало:
    `TypeError: expected str, bytes or os.PathLike object, not ndarray`. Гнать окно через
    временный файл и ffmpeg тоже нельзя: окно своё на КАЖДОЕ жёлтое слово. Поэтому шаги
    `get_probs` повторяются здесь по массиву: окно -> моно float32 -> `EMO_SR` (частота,
    на которой обучена модель) -> тензор на `_device`/`_dtype` модели -> `forward` ->
    `avg_pool1d` по времени -> `head` -> softmax -> `id2name`.

    `sr` — частота окна: звук читается `librosa.load(..., sr=...)` в частоте проекта
    (обычно 22050), а модель ждёт 16 кГц.
    """
    if isinstance(model, ServiceEmo):
        return model.probs(window, sr)
    import numpy as np
    import torch
    import torch.nn.functional as F

    wav = np.asarray(window, dtype="float32")
    if wav.ndim > 1:                      # многоканальный кусок -> моно
        wav = wav.mean(axis=-1, dtype="float32")
    wave = torch.from_numpy(np.ascontiguousarray(wav))
    if int(sr) != EMO_SR:
        import torchaudio               # зависимость gigaam; нужна только на ресемпле
        wave = torchaudio.functional.resample(wave, int(sr), EMO_SR)

    device = getattr(model, "_device", "cpu")
    wave = wave.to(device).to(getattr(model, "_dtype", torch.float32)).unsqueeze(0)
    length = torch.full([1], wave.shape[-1], device=device)
    with torch.inference_mode():
        encoded, _ = model.forward(wave, length)
        pooled = F.avg_pool1d(encoded, kernel_size=encoded.shape[-1]).squeeze(-1)
        logits = model.head(pooled)[0]
        probs = F.softmax(logits, dim=-1).detach().tolist()

    names = model.id2name
    return {names[i]: float(probs[i]) for i in range(len(names))}


def emotion_score(probs: Mapping[str, float]) -> float:
    """`emo = 1 - p(neutral)`: чем меньше нейтральности, тем сильнее фраза."""
    try:
        p = float(probs.get("neutral", 1.0) or 0.0)
    except (TypeError, ValueError):
        p = 1.0
    return max(0.0, min(1.0, 1.0 - p))


def _emo_window(audio: Any, sr: int, t: tuple[float, float], offset: float) -> Any | None:
    """Кусок `EMO_WIN_S` с центром на слове (слово — время, `offset` — начало аудио)."""
    half = EMO_WIN_S / 2.0
    center = (t[0] + t[1]) / 2.0
    a0 = int(round((center - half - offset) * sr))
    a1 = int(round((center + half - offset) * sr))
    a0, a1 = max(0, a0), min(len(audio), a1)
    if a1 - a0 < int(0.2 * sr):
        return None
    return audio[a0:a1]


def _emo_window_in_chunks(chunks: Sequence[tuple[float, float, Any]], sr: int,
                          t: tuple[float, float]) -> Any | None:
    """Окно эмоции слова в ОДНОМ из прочитанных отрезков (по времени исходника).

    Звук читается окнами, поэтому слова ищутся в своём отрезке: внутри него время
    считается от его начала. Слово, чьё окно эмоции не читали (режим «по голосу» или
    отрезок не достался), окна не получает — вызывающий считает эмоцию нейтральной.
    """
    center = (t[0] + t[1]) / 2.0
    for w0, w1, chunk in chunks:
        if w0 <= center <= w1:
            return _emo_window(chunk, sr, t, w0)
    return None


# --------------------------------------------------------------------------- #
# Ключ кэша и сайдкар
# --------------------------------------------------------------------------- #
def emph_path(xml_path: str) -> str:
    """Сайдкар силы жёлтых рядом с XML: `<стем>.emph.json`."""
    return os.path.splitext(xml_path)[0] + ".emph.json"


def _mtime(path: Any) -> float | None:
    """mtime файла или None (файла нет / не прочитался)."""
    try:
        return float(os.path.getmtime(str(path)))
    except (OSError, TypeError, ValueError):
        return None


def cache_key(words: Sequence[WordRef], intro_words: Sequence[tuple[float, float]],
              source: Any, highlights: Any) -> dict[str, Any]:
    """Ключ сайдкара: исходник и mtime, оценённые слова, набор жёлтых, версия формулы.

    Слова в ключе — (индекс, начало, конец, текст): правка ТЕКСТА слова
    (`/api/edit_word`) меняет эмоцию окна, правка времён — кусок исходника; и то и
    другое обязано пересчитать силу.
    """
    return {
        "v": EMPH_VERSION,
        "source": str(source or ""),
        "source_mtime": _mtime(source) if source else None,
        "words": [[int(w.idx), round(float(w.start), 4), round(float(w.end), 4), str(w.text)]
                  for w in words],
        "intro": [[round(float(s), 4), round(float(e), 4)] for s, e in intro_words],
        "yellow": sorted(int(x) for x in (highlights or [])),
    }


def _same_key(stored: Any, now: dict[str, Any]) -> bool:
    """Ключ сайдкара совпал с текущим (сравнение по значимым полям, не по форме)."""
    if not isinstance(stored, dict):
        return False
    for k in ("v", "source", "yellow", "intro"):
        if stored.get(k) != now.get(k):
            return False
    sm, nm = stored.get("source_mtime"), now.get("source_mtime")
    if (sm is None) != (nm is None):
        return False
    if sm is not None and abs(float(sm) - float(nm)) > 1e-6:
        return False
    sw, nw = stored.get("words"), now.get("words")
    if not isinstance(sw, list) or not isinstance(nw, list) or len(sw) != len(nw):
        return False
    for a, b in zip(sw, nw):
        if not isinstance(a, (list, tuple)) or len(a) != len(b):
            return False
        for x, y in zip(a, b):
            if x != y:
                return False
    return True


def read_emphasis(xml_path: str, words: Sequence[WordRef],
                  intro_words: Sequence[tuple[float, float]] = (),
                  highlights: Any = None, source: Any = None,
                  idx: Sequence[int] | None = None,
                  mode: Any = HL_MODE_DEFAULT) -> EmphasisView:
    """Прочитать сайдкар силы БЕЗ расчёта: эту дверь зовёт план сцены, она быстрая.

    Ключ разошёлся (другие слова, другой набор жёлтых, новый исходник, другая версия
    формулы) — `valid=False`, и правило наезда возвращается к прежнему поведению.

    `mode` — способ оценки (`hl_zoom_strength`): `voice` берёт `stress`, прочее — `emo`.
    Обе составляющие лежат в сайдкаре, поэтому смена способа НИЧЕГО не пересчитывает.
    """
    view_mode = hl_mode(mode)
    want = [int(i) for i in (idx if idx is not None else [w.idx for w in words])]
    path = emph_path(xml_path)
    data = fileio.json_load_soft(path) if os.path.isfile(path) else None
    if not isinstance(data, dict) or not _same_key(
            data.get("key"), cache_key(words, intro_words, source, highlights)):
        return EmphasisView(scores={}, valid=False, mode=view_mode)
    components = _parse_components(data.get("scores"))
    name = component_name(view_mode)
    scores: dict[int, float] = {}
    for i, comp in components.items():
        v = comp.get(name)
        if v is not None:
            scores[i] = float(v)
    return EmphasisView(scores=scores, valid=True, mode=view_mode,
                        components=components,
                        uncomputed=[i for i in want if i not in scores])


def _parse_components(raw: Any) -> dict[int, dict[str, float | None]]:
    """Обе составляющие сайдкара: {индекс: {"emo", "stress"}}; мусор пропускается.

    Формат версии 2 — обе половины по слову. Легаси-форма (одно число) читается как
    эмоция: так считала версия 1, но до сюда такой сайдкар не доходит — версия формулы
    стоит в ключе и разошлась бы раньше.
    """
    out: dict[int, dict[str, float | None]] = {}
    if not isinstance(raw, dict):
        return out
    names = (HL_COMPONENT[HL_MODE_EMOTION], HL_COMPONENT[HL_MODE_VOICE])
    for k, v in raw.items():
        try:
            i = int(k)
        except (TypeError, ValueError):
            continue
        comp: dict[str, float | None] = {}
        if isinstance(v, dict):
            for name in names:
                try:
                    comp[name] = float(v[name]) if v.get(name) is not None else None
                except (TypeError, ValueError):
                    comp[name] = None
        elif isinstance(v, (int, float)):
            # Легаси-форма одной силы: она и есть эмоция (так считала версия 1).
            comp[HL_COMPONENT[HL_MODE_EMOTION]] = float(v)
            comp[HL_COMPONENT[HL_MODE_VOICE]] = None
        else:
            continue
        out[i] = comp
    return out


# --------------------------------------------------------------------------- #
# Расчёт
# --------------------------------------------------------------------------- #
def _score(emo: float, stress: float) -> float:
    """Итоговая сила слова: `W_EMO·emo + W_STRESS·stress` (веса — константы выше)."""
    return max(0.0, min(1.0, W_EMO * float(emo) + W_STRESS * float(stress)))


def _load_audio(source: str, sr: int) -> tuple[Any, int]:
    """Звук исходника Камеры 1 ЦЕЛИКОМ: (моно float32, частота).

    Оставлено для тестов и разовых нужд: боевой расчёт читает ОКНА (`_read_window`,
    `audio_windows`), иначе память и время росли бы с длиной исходника.
    """
    import librosa
    import tempfile

    from core import sync as _sync
    fd, tmp = tempfile.mkstemp(prefix="_emph_", suffix=".wav")
    os.close(fd)
    try:
        _sync.extract_audio(source, tmp, sr=sr)
        y, got = librosa.load(tmp, sr=sr, mono=True)
        return y, int(got or sr)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass  # временный wav уже убран — чистить нечего


def compute_emphasis(inp: EmphasisInputs) -> dict[int, float]:
    """Посчитать силу выбранных слов клипа и записать сайдкар.

    В сайдкар уезжают составляющие по каждому слову (`emo`, `stress`) — по ним план
    выбирает способ (`hl_zoom_strength`) без единого пересчёта. Возвращается прежняя
    ИТОГОВАЯ сила (`_score`: `W_EMO·emo + W_STRESS·stress`, контракт WX) — план её не
    читает: он берёт из сайдкара компоненту выбранного способа (`read_emphasis`), и
    шкала у правила одна.

    Слова без куска исходника (нет звука в монтаже камеры) получают нейтральные
    признаки: эмоция 0, ударение 0.5 — «обычное слово»; случайно сильным такое слово
    не станет.

    Звук читается ОКНАМИ вокруг жёлтых слов (`audio_windows`), а не исходником целиком.
    Заглушки для тестов: `inp.audio_for` (звук целиком) и `inp.audio_window` (окно),
    `load_emo_model`/`emotion_probs` — модель эмоции, `prosody` — признаки. Модель
    берётся у сервиса (`load_emo_model` отдаёт `ServiceEmo`), и `release_emo` у неё
    ничего не отпускает — выгрузкой занят простой сервиса; если сервиса нет, модель
    грузится здесь и выгружается в finally, как раньше.

    Тон и RMS окна считаются ОДИН раз на всё объединённое окно (`_merge_windows` ->
    `prosody` -> `tone_track`), а не на каждое слово: пословный `yin` (а до него `pyin`)
    давал на клипе с полусотней жёлтых минуты вместо секунд (замер 02.10.2026).

    Режим «по голосу» модель эмоций не грузит ВООБЩЕ: нужна только акустика, и в
    сайдкар тогда уезжает лишь `stress`. Это и решение владельца, и бережность к
    видеопамяти: на Windows переполнение VRAM вешает машину.
    """
    words = list(inp.words)
    idx = sorted({int(i) for i in (inp.idx if inp.idx is not None else [w.idx for w in words])})
    idx = [i for i in idx if 0 <= i < len(words)]
    mode = hl_mode(inp.mode)
    meta, cams, _subs, _xi = inp.parsed
    fps = float((meta or {}).get("fps") or 60.0)
    source = ((cams[0].get("path") if cams else "") or "")
    segs = _voice_segments_frames(((cams[0].get("clips") if cams else []) or []))
    src = _source_times(segs, words, fps)

    # Слова, по которым считаем ударение: сами жёлтые плюс их соседи (±5 слов) — z-оценка
    # сравнивает слово с окружением, и соседний звук нужен ей как данные. Всё остальное
    # (сотни слов ролика) не считается вовсе: звук читается ТОЛЬКО вокруг жёлтых.
    target_idx = sorted({int(i) for i in (inp.idx if inp.idx is not None
                                          else [w.idx for w in words])} |
                        {len(words) + j for j in range(len(inp.intro_words or ()))})
    # Соседи считаются только по словам РОЛИКА: индекс слова интро продолжает ряд, и
    # окрестность вокруг него за краем списка не строится (его звук берётся из своего
    # куска, а ударение интро-слова считается по общей шкале).
    near = {j for i in target_idx if 0 <= i < len(words)
            for j in range(max(0, i - STRESS_NEIGHBORS), min(len(words), i + STRESS_NEIGHBORS + 1))}
    all_spans = stress_spans(src, sorted(near))
    stress_of = {i: sp for i, sp in all_spans.items()}
    stress_idx = sorted(stress_of)
    emo_spans = [src[i] for i in target_idx if 0 <= i < len(words) and src[i] is not None]

    sr = int(inp.sample_rate or 22050)
    chunks: list[tuple[float, float, Any]] = []   # прочитанные отрезки: (начало, конец, звук)
    feat: dict[int, tuple[float, float, float]] = {}

    def _reads() -> list[tuple[float, float]]:
        """Окна чтения звука: окрестности ударения плюс окна эмоции (без них — только окрестности)."""
        return audio_windows([stress_of[i] for i in stress_idx], mode, emo_spans)

    def _fill(chunk: Any, w0: float, w1: float, got_sr: int) -> None:
        """Признаки слов, чей отрезок целиком лёг в прочитанное окно.

        Время внутри окна считается от его начала: звук читается ОТРЕЗКАМИ, а `prosody`
        работает с массивом. Слово, чей отрезок не влез (окно обрезано краем исходника),
        остаётся без признаков — вызывающий сочтёт его нейтральным.

        `prosody` зовётся ОДИН раз на окно и сразу за все его слова: он считает тон
        (`yin`) и RMS по всему массиву сразу, и пословный вызов гонял бы `yin` заново
        на каждом слове — ровно та цена, из-за которой расчёт и переписывался.
        """
        inside = [(i, sp[0] - w0, sp[1] - w0) for i, sp in stress_of.items()
                  if sp[0] >= w0 - 1e-6 and sp[1] <= w1 + 1e-6]
        if inside:
            feat.update(prosody(chunk, got_sr, inside))

    have_audio = bool(source and stress_idx)
    if have_audio:
        if inp.audio_for is not None or inp.audio_window is not None:
            # Заглушки тестов: они сами решают, что за звук, — файла может и не быть.
            # `audio_for` отдаёт звук ЦЕЛИКОМ (легаси-заглушка: время в нём абсолютное),
            # `audio_window` — честный отрезок по запрошенному окну.
            if inp.audio_for is not None:
                chunk, sr2 = inp.audio_for(source)
                sr = int(sr2 or 22050)
                chunks.append((0.0, 1e12, chunk))    # «весь файл»: сдвига времени нет
                _fill(chunk, 0.0, 1e12, sr)
            else:
                for w0, w1 in _reads():
                    chunk, sr2 = inp.audio_window(source, w0, w1)
                    sr = int(sr2 or 22050)
                    chunks.append((float(w0), float(w1), chunk))
                    _fill(chunk, float(w0), float(w1), sr)
        elif os.path.isfile(source):
            for w0, w1 in _reads():
                try:
                    chunk, sr2 = _read_window(source, w0, w1, sr)
                except Exception as ex:
                    # Звук не достался (битый исходник, нет ffmpeg) — сила считается без
                    # акустики: хуже пустого сайдкара, но сборку это не роняет.
                    log.warning("звук для силы жёлтых не прочитан (%s): %s", source, ex)
                    break
                sr = int(sr2 or sr)
                chunks.append((float(w0), float(w1), chunk))
                _fill(chunk, float(w0), float(w1), sr)
        else:
            have_audio = False
    stress = stress_norm(feat, stress_idx)

    targets: list[tuple[int, tuple[float, float]]] = [(i, src[i]) for i in idx
                                                     if src[i] is not None]  # type: ignore[misc]
    for j, (s, e) in enumerate(inp.intro_words or ()):
        # Слова интро приходят в КАДРАХ (`plan_intro.intro_hl_words` читает их из subs) —
        # в исходное время переводим здесь: `_time_in_source` живёт в секундах.
        t = _time_in_source(segs, float(s) / fps, float(e) / fps, fps)
        if t is not None:
            targets.append((len(words) + j, t))

    emo: dict[int, float] = {}
    model: Any = None
    try:
        # Эмоция меряется ТОЛЬКО по звуку: звука нет (исходник пропал, нет ffmpeg) —
        # модель не грузим вовсе. И в режиме «по голосу» она не нужна вовсе: сила
        # считается по акустике. На Windows переполнение VRAM вешает машину, и грузить
        # модель впустую нельзя.
        if mode != HL_MODE_VOICE and targets and have_audio:
            from core.xml2ae.parse import Cancelled
            for _i, _t in targets:
                if inp.cancel is not None and inp.cancel():
                    raise Cancelled()
            model = load_emo_model()
            for i, t in targets:
                win = _emo_window_in_chunks(chunks, sr, t)
                if win is None:
                    continue
                emo[i] = emotion_score(emotion_probs(win, model, sr))
    finally:
        if model is not None:
            release_emo(model)

    n_emo = HL_COMPONENT[HL_MODE_EMOTION]
    n_stress = HL_COMPONENT[HL_MODE_VOICE]
    # Эмоция уезжает в сайдкар, только если её считали. В режиме «по голосу» модель не
    # грузится, и нуля на её месте быть не должно: ноль — это «фраза нейтральна», и
    # переключение способа на эмоции выбрало бы всем словам одну силу (первые по времени
    # наезжали бы вместо самых сильных). Без компоненты чтение честно скажет «не
    # посчитано», и правило вернётся к прежнему поведению. В режиме эмоций ноль остаётся
    # на месте несчитанного: это и есть определённое «нейтрально» (контракт WX), а звука
    # может не быть вовсе. Ударение есть всегда: без звука оно нейтрально (0.5).
    emo_done = mode != HL_MODE_VOICE
    components: dict[int, dict[str, float]] = {}
    order = set(idx) | {i for i, _t in targets}
    for i in sorted(order):
        comp: dict[str, float] = {n_stress: round(float(stress.get(i, 0.5)), 4)}
        if emo_done:
            comp[n_emo] = round(float(emo.get(i, 0.0)), 4)
        components[i] = comp
    scores = {i: _score(c.get(n_emo, 0.0), c[n_stress]) for i, c in components.items()}
    key = cache_key(words, inp.intro_words or (), source, idx)
    fileio.atomic_json_dump(emph_path(inp.xml_path), {
        "key": key,
        "scores": {str(k): components[k] for k in sorted(components)},
    })
    return scores
