# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Audio extraction + two-camera sync by cross-correlation."""
import hashlib
import os
import subprocess
import tempfile
import time as _time
import numpy as np
from scipy.io import wavfile

SR = 16000  # analysis sample rate (mono)

# Порог автоподбора камер по звуку: нормированный пик корреляции огибающих ниже
# него — «не тот дубль». Замер на реальной съёмке (60 файлов камеры 1 против 50
# файлов камеры 2, 2026-08-11):
#   тот же дубль с другой камеры          0.82
#   клип, вырезанный из длинной записи    0.78-0.90
#   тот же файл                           1.00
#   другой дубль / файл без пары          <= 0.29
# Между 0.30 и 0.78 измерений НЕТ — порог ставим в середину пустой полосы. Раньше
# он стоял на 0.30, вплотную к шуму (максимум чужого — 0.293): достаточно чуть более
# похожего материала, и подбор молча подставил бы не тот ракурс. Промах дешевле
# ошибки: «похожего не нашлось» юзер видит сразу и выберет руками, а чужая камера
# в паре тихо испортит нарезку.
MATCH_MIN = 0.50

_ENV_CACHE = os.path.join(tempfile.gettempdir(), "reelsi_match_env")
# Кэш огибающих чистится по возрасту: файлы съёмочного дня уходят через неделю, и
# папка в %TEMP% не растёт вечно. Огибающая — ~48 КБ на минуту, так что это про
# порядок, а не про место.
ENV_CACHE_TTL_DAYS = 7
# Потолок на извлечение звука. Замер: 0.26 с на файл (ffmpeg -vn не декодирует
# видео), так что 300 с — это «процесс завис», а не «долгий файл». Без таймаута
# один битый файл вешал бы весь HTTP-запрос подбора навсегда: /api/cammatch
# гоняет ffmpeg по всей папке синхронно, отменить его нечем.
EXTRACT_TIMEOUT = 300


def extract_audio(video_path, wav_path, sr=SR, timeout=EXTRACT_TIMEOUT):
    """Decode the audio track to mono `sr` wav (fast: -vn, no video decode)."""
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", str(sr),
         "-c:a", "pcm_s16le", wav_path, "-loglevel", "error"],
        check=True, timeout=timeout)
    return wav_path


def _prune_env_cache(ttl_days=None):
    """Выбросить огибающие старше ttl_days. Тихо: чистка кэша не повод падать."""
    ttl = (ENV_CACHE_TTL_DAYS if ttl_days is None else ttl_days) * 86400
    try:
        now = _time.time()
        for name in os.listdir(_ENV_CACHE):
            p = os.path.join(_ENV_CACHE, name)
            try:
                if now - os.path.getmtime(p) > ttl:
                    os.unlink(p)
            except OSError:
                pass
    except OSError:
        pass


def _load(wav_path):
    sr, x = wavfile.read(wav_path)
    if x.ndim > 1:
        x = x.mean(axis=1)
    return sr, x.astype(np.float32)


def _envelope(x, sr, hop=160):
    """RMS energy envelope at sr/hop Hz (~100 Hz with hop=160)."""
    n = len(x) // hop
    e = np.sqrt(np.mean(x[:n * hop].reshape(n, hop) ** 2, axis=1) + 1e-9)
    e = e - e.mean()
    return e, sr / hop


def find_offset(wav_a, wav_b):
    """Return seconds to delay B so it aligns with A (positive => B starts later than A).
    Also returns a 0..1 confidence from peak sharpness."""
    from scipy.signal import correlate
    sr_a, a = _load(wav_a)
    sr_b, b = _load(wav_b)
    ea, r = _envelope(a, sr_a)
    eb, _ = _envelope(b, sr_b)
    corr = correlate(ea, eb, mode="full", method="fft")
    k = int(np.argmax(corr))
    lag = k - (len(eb) - 1)          # samples (env rate) that B trails A
    offset = lag / r                 # seconds
    peak = corr[k]
    conf = float((peak - np.median(corr)) / (np.max(np.abs(corr)) + 1e-9))
    return offset, conf


def wav_envelope(wav_path, hop=160):
    """RMS-огибающая wav (~100 Гц при 16 кГц) — то же, что считает find_offset."""
    sr, x = _load(wav_path)
    return _envelope(x, sr, hop)


MATCH_MIN_OVERLAP = 0.5      # доля КОРОТКОЙ огибающей, которая обязана перекрыться
# Сколько СЕКУНД общего звука нужно, чтобы вообще выносить решение. Чем короче
# перекрытие, тем выше забирается лучший случайный сдвиг — на чужом материале
# (замер 2026-08-11, потолок из 6 прогонов против файла на 300 с):
#   10 с -> 0.61   15 с -> 0.47   20 с -> 0.43   30 с -> 0.41   45 с -> 0.32   60 с -> 0.28
# Верный дубль даёт ~1.0 при любой длине, так что порог тут ни при чём: коротким
# кускам просто не хватает звука, чтобы отличить совпадение от везения. Ниже этого
# — честное «не нашлось», а не догадка (чужая камера в паре тихо испортит нарезку).
MATCH_MIN_OVERLAP_SEC = 40.0


def match_score(ea, eb, rate, min_overlap=None, min_overlap_sec=None):
    """Лучший сдвиг eb относительно ea (сек; >0 — eb начинается позже) и
    НОРМИРОВАННЫЙ пик корреляции огибающих: 1 = тот же звук, ~0 = другой материал.
    Нормировка обязательна: сырой пик корреляции растёт с длиной файла, и длинный
    чужой файл выглядел бы «похоже».

    Нормируем КАЖДЫЙ сдвиг по нормам ТОЛЬКО перекрывающихся кусков, а не по полным
    нормам файлов. С полными счёт падал как √(отношение длин): верный дубль в файле
    вдесятеро длиннее давал 0.290 и отсекался порогом 0.30 — «похожего не нашлось»
    там, где камера 2 писала смену одним куском (замер 2026-08-11). При таком
    делении короткое перекрытие даёт почти 1.0 на случайном шуме, поэтому сдвиги
    с перекрытием меньше min_overlap от короткой огибающей — и короче
    min_overlap_sec секунд — не рассматриваем вовсе (см. MATCH_MIN_OVERLAP_SEC).
    Сравнивать нечего -> (0.0, 0.0), то есть честное «не нашлось».
    """
    from scipy.signal import correlate
    # значения по умолчанию читаем ЗДЕСЬ, а не в сигнатуре: аргумент по умолчанию
    # связывается один раз при импорте, и подмена sync.MATCH_MIN_* (тесты, калибровка)
    # до функции бы не дошла — молча работали бы старые пороги
    if min_overlap is None:
        min_overlap = MATCH_MIN_OVERLAP
    if min_overlap_sec is None:
        min_overlap_sec = MATCH_MIN_OVERLAP_SEC
    na, nb = len(ea), len(eb)
    if not na or not nb:
        return 0.0, 0.0
    ea = np.asarray(ea, dtype=np.float64)
    eb = np.asarray(eb, dtype=np.float64)
    corr = correlate(ea, eb, mode="full", method="fft")
    # границы окна перекрытия для каждого сдвига lag: в ea это [a0, a1), в eb — то же
    # окно со сдвигом. Нормы окон берём из накопленных сумм квадратов — O(n) на все
    # сдвиги сразу, отдельный проход на каждый был бы O(n²).
    lag = np.arange(na + nb - 1) - (nb - 1)
    a0 = np.maximum(0, lag)
    a1 = np.minimum(na, nb + lag)
    ca = np.concatenate(([0.0], np.cumsum(ea ** 2)))
    cb = np.concatenate(([0.0], np.cumsum(eb ** 2)))
    denom = np.sqrt(np.maximum(ca[a1] - ca[a0], 0.0)
                    * np.maximum(cb[a1 - lag] - cb[a0 - lag], 0.0))
    need = max(int(min_overlap * min(na, nb)),
               int((min_overlap_sec or 0) * rate), 1)
    ok = (denom > 0) & ((a1 - a0) >= need)
    if not ok.any():
        return 0.0, 0.0
    ncc = np.where(ok, corr / (denom + 1e-9), -np.inf)
    k = int(np.argmax(ncc))
    return (k - (nb - 1)) / rate, float(np.clip(ncc[k], -1.0, 1.0))


def match_wavs(wav_a, wav_b):
    """То же, что match_score, по путям wav (тесты, CLI): (offset, score)."""
    ea, r = wav_envelope(wav_a)
    eb, _ = wav_envelope(wav_b)
    return match_score(ea, eb, r)


def video_envelope(video):
    """RMS-огибающая видео (~100 Гц) с кэшем в %TEMP%. Для автоподбора камер
    полные wav не храним: минута звука — 1.9 МБ, огибающая — 48 КБ. Ключ кэша —
    путь + mtime + размер: перезаписанный файл пересоберётся, остальное достаётся
    из кэша (30 файлов съёмочного дня — по одному извлечению на файл)."""
    st = os.stat(video)
    key = hashlib.md5((os.path.abspath(video) + "|%d|%d" % (st.st_mtime, st.st_size))
                      .encode("utf-8", "surrogateescape")).hexdigest()
    os.makedirs(_ENV_CACHE, exist_ok=True)
    p = os.path.join(_ENV_CACHE, key + ".npy")
    try:
        if os.path.isfile(p):
            return np.load(p), SR / 160.0
    except Exception:
        pass   # битый кэш — пересоберём
    fd, wav = tempfile.mkstemp(suffix=".wav", prefix="reelsi_env_")
    os.close(fd)
    try:
        extract_audio(video, wav)
        e, r = wav_envelope(wav)
    finally:
        try:
            os.unlink(wav)
        except OSError:
            pass
    with open(p + ".part", "wb") as fh:
        np.save(fh, e)
    os.replace(p + ".part", p)
    _prune_env_cache()   # чистим на записи: попадание в кэш остаётся без лишнего listdir
    return e, r
