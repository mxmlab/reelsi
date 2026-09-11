# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""whisper.cpp как ASR-бэкенд: бинарник `whisper-cli` + ggml-модели.

Зачем: CTranslate2 (faster-whisper) из коробки умеет только CUDA и CPU — на Apple
Silicon и Radeon транскрипция всегда идёт на CPU. whisper.cpp понимает Metal (Mac)
и Vulkan (AMD и любое другое железо) — единственный способ дать не-NVIDIA железу
транскрипцию быстрее CPU (см. docs/PLATFORMS.md).

Всё здесь опционально: нет бинарника или модели — движок просто не выбирается,
остальные движки работают как работали. Диспетчер и реестр движков —
в asr_backends.py.
"""
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from core.app_meta import console_emit

# Своя папка, а не ~/.cache/huggingface: пользователю должно быть видно, сколько
# места занимают модели (large-v3 — 3 ГБ), и их можно удалить руками.
# REELSI_WHISPER_CPP — для изолированного профиля (webui_test.py): тестовая
# транскрипция не должна тянуть 3 ГБ в боевую папку.
WHISPER_CPP_DIR = os.environ.get("REELSI_WHISPER_CPP") or os.path.join(
    os.path.expanduser("~"), ".reelsi", "whisper_cpp")
BIN_DIR = os.path.join(WHISPER_CPP_DIR, "bin")
MODELS_DIR = os.path.join(WHISPER_CPP_DIR, "models")

# ggml-веса с Hugging Face. Имена файлов стабильны и совпадают с размерами,
# которые объявлены в _BUILTIN у asr_backends.py. Репо — ggerganov/whisper.cpp
# (ggml-org/whisper.cpp на HF НЕ существует — проверено вживую 2026-08-09).
HF_REPO = "ggerganov/whisper.cpp"
MODEL_FILES = {
    "large-v3": "ggml-large-v3.bin",
    "medium": "ggml-medium.bin",
    "small": "ggml-small.bin",
    "base": "ggml-base.bin",
    "tiny": "ggml-tiny.bin",
}

_BIN_NAME = "whisper-cli.exe" if sys.platform == "win32" else "whisper-cli"


def whisper_cli_path():
    """Где взять бинарник: явный путь (REELSI_WHISPER_CLI), PATH (brew/scoop/apt),
    своя папка BIN_DIR. Возвращает путь или None."""
    explicit = os.environ.get("REELSI_WHISPER_CLI")
    if explicit and os.path.isfile(explicit):
        return explicit
    on_path = shutil.which("whisper-cli")
    if on_path:
        return on_path
    # Прямой путь И рекурсивно: GitHub-архив распаковывается в bin/Release/,
    # и заставлять пользователя переносить файлы вручную — лишнее действие.
    local = os.path.join(BIN_DIR, _BIN_NAME)
    if os.path.isfile(local):
        return local
    for root, _dirs, files in os.walk(BIN_DIR):
        for f in files:
            if f == _BIN_NAME:
                return os.path.join(root, f)
    return None


def _model_file(size):
    f = MODEL_FILES.get(size)
    if not f:
        raise RuntimeError("whisper.cpp: неизвестный размер модели '%s'" % size)
    return f


def model_path(size):
    """Путь к модели, если она уже скачана в свою папку, иначе None."""
    path = os.path.join(MODELS_DIR, _model_file(size))
    return path if os.path.isfile(path) else None


def ensure_model(size):
    """Скачать ggml-модель (если ещё нет) и вернуть путь к файлу.

    Докачка с места обрыва — встроенная в huggingface_hub (файлы .incomplete),
    тот же приём, что у omni_asr.py; xet-транспорт выключен: он застревает
    на пустой сети, классический HTTP доезжает."""
    path = model_path(size)
    if path:
        return path
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        raise RuntimeError("whisper.cpp: нет huggingface_hub — установите: "
                           "pip install huggingface-hub")
    try:
        return hf_hub_download(repo_id=HF_REPO, filename=_model_file(size),
                               local_dir=MODELS_DIR)
    except Exception as e:
        raise RuntimeError("whisper.cpp: не удалось скачать модель %s (%s: %s)"
                           % (_model_file(size), type(e).__name__, e))


def _hms_to_sec(s):
    """'00:00:00,000' / '00:00:00.000' -> секунды (float)."""
    s = (s or "").replace(",", ".").strip()
    parts = s.split(":")
    if len(parts) != 3:
        return None
    try:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError:
        return None


def _scales(data):
    """Делители таймкодов -> (для слов, для offsets сегментов).

    Форматы плавают между версиями: v1.9+ пишет МИЛЛИСЕКУНДЫ и в словах, и в
    offsets (проверено вживую 2026-08-09); старые версии — слова в 10-мс тиках,
    а offsets в СЕКУНДАХ. Калибруем по timestamps-строкам (всегда hh:mm:ss):
    отношение длительности в offsets к ней даёт единиц в секунде (1000 или 1)."""
    for seg in (data.get("transcription") or []):
        off = seg.get("offsets") or {}
        ts = seg.get("timestamps") or {}
        try:
            s, e = _hms_to_sec(ts.get("from")), _hms_to_sec(ts.get("to"))
            dur = float(off.get("to", 0)) - float(off.get("from", 0))
            if s is not None and e is not None and dur > 0 and e - s > 0:
                ratio = dur / (e - s)
                if 900 <= ratio <= 1100:       # миллисекунды во всём (v1.9+)
                    return 1000, 1000
                if 0.9 <= ratio <= 1.1:        # offsets в секундах (старые)
                    return 100, 1
        except (TypeError, ValueError):
            continue
    return 100, 1                              # исторический формат без timestamps


def parse_words(data):
    """JSON из `whisper-cli -oj` -> [{"w","start","end"}] (секунды).

    Версии, у которых слова есть, — берём их; у остальных натягиваем слова на
    сегмент интерполяцией (как omni-фразы). Единицы таймкодов калибруются
    автоматически (см. _scales)."""
    ws, os_ = _scales(data)
    words = []
    for seg in (data.get("transcription") or []):
        seg_words = seg.get("words")
        if seg_words:
            for w in seg_words:
                t = (w.get("word") or "").strip()
                if t:
                    words.append({"w": t,
                                  "start": round(float(w["start"]) / ws, 3),
                                  "end": round(float(w["end"]) / ws, 3)})
        else:
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            off = seg.get("offsets") or {}
            s, e = (float(off.get("from", 0.0)) / os_,
                    float(off.get("to", 0.0)) / os_)
            toks = text.split()
            n = len(toks)
            for i, t in enumerate(toks):
                words.append({"w": t,
                              "start": round(s + (e - s) * (i / n), 3),
                              "end": round(s + (e - s) * ((i + 1) / n), 3)})
    return words


def transcribe(wav_path, size="large-v3"):
    """Прогнать whisper-cli по файлу и вернуть [{"w","start","end"}] (сек).

    Отдельным процессом (как GigaAM/CTC в asr_backends.py): нативный краш или
    зависание GPU-кода не должны убить Flask. JSON при `-oj` пишется в ФАЙЛ,
    в stdout уходит только текст — поэтому результат читаем из `<out>.json` во
    временной папке; имя уникальное, чтобы два параллельных прогона не
    перетёрли друг друга (та же ловушка, что у _ctc)."""
    cli = whisper_cli_path()
    if not cli:
        raise RuntimeError("whisper.cpp: бинарник whisper-cli не найден. Поставить: "
                           "brew install whisper-cpp (mac) | scoop install whisper-cpp "
                           "(win) | apt install whisper-cpp (linux), либо положить "
                           "бинарник в %s" % BIN_DIR)
    model = ensure_model(size)
    fd, out_base = tempfile.mkstemp(prefix="_whisper_cpp_")
    os.close(fd)
    os.remove(out_base)                       # whisper-cli создаст файл сам
    cmd = [cli, "-m", model, "-f", wav_path, "-of", out_base, "-oj", "-l", "auto"]
    out_path = out_base + ".json"
    try:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=1800)
        except subprocess.TimeoutExpired:
            raise RuntimeError("whisper.cpp: превышен таймаут (30 мин)")
        if r.returncode != 0:
            raw = (r.stderr or "").strip()
            err = [l for l in raw.splitlines() if l.strip()]
            raise RuntimeError(raw if "error" in raw.lower() else
                               (err[-1] if err else "whisper.cpp: бинарник "
                                                    "завершился с ошибкой"))
        if not os.path.isfile(out_path):
            raise RuntimeError("whisper.cpp: движок не записал JSON-результат")
        try:
            with open(out_path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            raise RuntimeError("whisper.cpp: не удалось разобрать JSON-результат")
        return parse_words(data)
    finally:
        for p in (out_base, out_path):
            try:
                os.remove(p)
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# Установка бинарника ПО ЯВНОМУ ЗАПРОСУ (python -m core.whisper_cpp install)
# --------------------------------------------------------------------------- #
# Ничего не качается само собой: пользователь может жить на GigaAM и не знать
# о whisper.cpp вовсе. Даже моделей — они тянутся только при первом прогоне
# движка (ensure_model), т.е. когда человек САМ выбрал whisper.cpp в селекторе.
# Имена ассетов — из официальных релизов ggerganov/whisper.cpp (v1.9.2):
# для macOS CLI-сборок в релизах нет вовсе — там brew install whisper-cpp.
ASSET_PLAN = {
    ("win32", "amd64"): "whisper-bin-Win32.zip",
    ("linux", "x86_64"): "whisper-bin-ubuntu-x64.tar.gz",
    ("linux", "aarch64"): "whisper-bin-ubuntu-arm64.tar.gz",
}
RELEASES_URL = "https://github.com/ggerganov/whisper.cpp/releases"
GH_API = "https://api.github.com/repos/ggerganov/whisper.cpp/releases/latest"


def _pick_asset(assets, names):
    """Выбрать URL ассета по имени из JSON-ответа GitHub API. Чистая функция:
    в тестах вместо сети подкладывается готовый список."""
    for a in assets or []:
        if (a.get("name") or "") in names:
            return a.get("browser_download_url")
    return None


def install_cli(emit=console_emit):
    """Скачать whisper-cli под платформу и распаковать в BIN_DIR.

    Только по явному запросу (CLI). При недоступном GitHub API не выдумывает
    URL, а честно говорит, что скачать руками, — репо/версия могут разъехаться."""
    existing = whisper_cli_path()
    if existing:
        emit("whisper.cpp: бинарник уже установлен: {path}", path=existing)
        return existing
    key = (sys.platform, platform.machine().lower())
    asset = ASSET_PLAN.get(key)
    if asset is None:
        raise RuntimeError("whisper.cpp: официальных CLI-сборок для %s/%s в "
                           "релизах нет. macOS: brew install whisper-cpp; "
                           "остальное — сборка из исходников (README whisper.cpp)"
                           % key)
    try:
        d = json.load(urllib.request.urlopen(GH_API, timeout=30))
        url = _pick_asset(d.get("assets") or [], (asset,))
    except Exception:
        raise RuntimeError("whisper.cpp: не удалось достучаться до GitHub API — "
                           "скачайте %s вручную: %s" % (asset, RELEASES_URL))
    if not url:
        raise RuntimeError("whisper.cpp: ассет %s не найден в последнем релизе — "
                           "проверьте: %s" % (asset, RELEASES_URL))
    emit("whisper.cpp: скачиваю {name} ...", name=url.split("/")[-1])
    data = urllib.request.urlopen(url, timeout=600).read()
    os.makedirs(BIN_DIR, exist_ok=True)
    if asset.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.extractall(BIN_DIR)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
            t.extractall(BIN_DIR)
    path = whisper_cli_path()
    if not path:
        raise RuntimeError("whisper.cpp: архив распакован, но whisper-cli внутри "
                           "не найден — содержимое: %s" % os.listdir(BIN_DIR))
    emit("whisper.cpp: установлен: {path}", path=path)
    return path


if __name__ == "__main__":
    # Без консоли stdout у Python на Windows — cp1251/cp1252, и русский текст
    # роняет печать UnicodeEncodeError (та же починка, что в doctor.py/webui.py).
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if len(sys.argv) > 1 and sys.argv[1] == "install":
        try:
            install_cli()
        except RuntimeError as e:
            print(e)
            sys.exit(1)
    else:
        print("usage: python -m core.whisper_cpp install   # установить whisper-cli "
              "(по запросу; модели скачаются при первом выборе движка)")
