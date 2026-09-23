# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты — три мелочи из аудита.

1. `/api/waveform` (api/files.py) — единственный файловый маршрут без проверок.
   Теперь расширение проверяется и у присланного пути, и у realpath, затем
   `_never_serve` — и только потом чтение файла. Кэш пишется РЯДОМ С ЦЕЛЬЮ
   (`<путь>.peaks<pps>.json`), поэтому отказ обязан не создавать файл.
2. `core.aicut.catalog._save_disk` пишет через УНИКАЛЬНЫЙ временный файл
   (mkstemp + os.replace), а не через фиксированный `<path>.tmp`: иначе два
   одновременных «Обновить список» (вкладка ИИ и вкладка Видео) пишут в один
   файл и перемешивают содержимое кэша.
3. `core.whisper_cpp.ensure_model` качает ggml-веса с ЗАКРЕПЛЁННОЙ ревизии HF:
   веса разбирает нативный код, и «main на момент скачивания» — защита слабее,
   чем у самого whisper-cli (тот закреплён версией и ASSET_SHA256).

Сеть в тестах запрещена: librosa и huggingface_hub подменяются.
"""
import json
import os
import re
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import api  # noqa: E402
import core.aicut.config  # noqa: E402
from api._core import _never_serve  # noqa: E402
from api.files import ALLOWED_MEDIA_EXTS, ALLOWED_WAVE_EXTS  # noqa: E402

H = {"Host": "127.0.0.1:5001"}
SECRET = "sk-fake-secret-waveform-0123456789"


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def fake_librosa(monkeypatch):
    """Волна считается из поддельного librosa: тяжёлые пакеты и сеть не нужны.

    Возвращает список путей, которые реально дошли до загрузки — по нему видно,
    что отказ случился ДО работы, а не после."""
    import numpy as np

    called = []
    mod = types.ModuleType("librosa")

    def load(path, sr=16000, mono=True):
        called.append(path)
        return np.zeros(32000, dtype="float32"), 16000

    mod.load = load
    monkeypatch.setitem(sys.modules, "librosa", mod)
    return called


def _fake_hf(download):
    """Модуль-заглушка huggingface_hub с подменённым hf_hub_download."""
    mod = types.ModuleType("huggingface_hub")
    mod.hf_hub_download = download
    return mod


# --------------------------------------------------------------------------- #
# 1. /api/waveform: проверки перед работой
# --------------------------------------------------------------------------- #
def test_wave_allowed_exts_subset_of_media():
    """/api/waveform принимает только звук и видео из общего медиа-allowlist.

    Картинки исключены нарочно (волны из них не выйдет), а расширять сверх
    /api/media нечем — список не должен с ним разъезжаться."""
    assert ALLOWED_WAVE_EXTS <= ALLOWED_MEDIA_EXTS
    assert {"wav", "mp3", "m4a", "aac", "flac", "ogg", "opus", "aif", "aiff"} <= ALLOWED_WAVE_EXTS
    assert {"mp4", "mov", "m4v", "mkv", "webm", "avi"} <= ALLOWED_WAVE_EXTS
    assert not ({"png", "jpg", "jpeg", "gif", "webp", "avif"} & ALLOWED_WAVE_EXTS)


def test_waveform_audio_still_works(client, tmp_path, fake_librosa):
    """Обычный звуковой файл: прежний ответ, кэш рядом и повторный запрос из кэша."""
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFFfake")

    r = client.get(f"/api/waveform?path={audio}&pps=80", headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True and d["pps"] == 80 and d["dur"] == 2.0
    assert len(d["peaks"]) == 160              # 2 секунды по 80 пиков
    assert fake_librosa == [str(audio)]
    assert (tmp_path / "clip.wav.peaks80.json").is_file()

    r2 = client.get(f"/api/waveform?path={audio}&pps=80", headers=H)
    assert r2.status_code == 200 and r2.get_json()["ok"] is True
    assert fake_librosa == [str(audio)], "второй запрос обязан обойтись кэшем"


def test_waveform_video_still_works(client, tmp_path, fake_librosa):
    """Волна берётся и из видео: её зовёт редактор для блока камеры."""
    video = tmp_path / "C1437.MP4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    r = client.get(f"/api/waveform?path={video}", headers=H)
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


@pytest.mark.parametrize("name", ["notes.txt", "cover.png", "ai_config.json.bak", "noext"])
def test_waveform_refuses_other_extensions(client, tmp_path, fake_librosa, name):
    """Неразрешённое расширение — отказ, до librosa дело не дошло, кэш НЕ создан."""
    f = tmp_path / name
    f.write_text("не волна", encoding="utf-8")

    r = client.get(f"/api/waveform?path={f}", headers=H)
    assert r.status_code == 403
    assert fake_librosa == [], "до чтения файла дело доходить не должно"
    assert os.listdir(tmp_path) == [name], "рядом с файлом создан кэш <путь>.peaks*.json"


def test_waveform_refuses_symlink_with_audio_name(client, tmp_path, fake_librosa):
    """Симлинк clip.wav -> notes.txt: расширение присланного пути проходит, реальный
    путь — нет (та же пара проверок, что у /api/media)."""
    notes = tmp_path / "notes.txt"
    notes.write_text("не волна", encoding="utf-8")
    link = tmp_path / "clip.wav"
    try:
        os.symlink(notes, link)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"симлинки не поддерживаются: {e}")

    r = client.get(f"/api/waveform?path={link}", headers=H)
    assert r.status_code == 403
    assert fake_librosa == []
    assert sorted(os.listdir(tmp_path)) == ["clip.wav", "notes.txt"]


def test_waveform_refuses_ai_config(client, tmp_path, fake_librosa, monkeypatch):
    """Путь к секрету (AI_CONFIG_PATH) — отказ; ключ в ответ не попадает, кэша нет."""
    secret = tmp_path / "ai_config.json"
    secret.write_text(json.dumps({"profiles": {"x": {"api_key": SECRET}}}), encoding="utf-8")
    monkeypatch.setattr(core.aicut.config, "AI_CONFIG_PATH", str(secret))

    r = client.get(f"/api/waveform?path={secret}", headers=H)
    assert r.status_code == 403
    assert SECRET not in r.get_data(as_text=True)
    assert fake_librosa == []
    assert os.listdir(tmp_path) == ["ai_config.json"]


def test_waveform_refuses_hardlinked_secret_with_audio_name(client, tmp_path, fake_librosa, monkeypatch):
    """Секрет под безобидным именем clip.wav (жёсткая ссылка): расширение проходит,
    ловит `_never_serve`. Без него маршрут посчитал бы волну чужого файла и положил
    бы кэш рядом с ним."""
    secret = tmp_path / "real_ai_config.json"
    secret.write_text(json.dumps({"profiles": {"x": {"api_key": SECRET}}}), encoding="utf-8")
    monkeypatch.setattr(core.aicut.config, "AI_CONFIG_PATH", str(secret))
    link = tmp_path / "clip.wav"
    try:
        os.link(secret, link)
    except OSError as e:
        pytest.skip(f"жёсткие ссылки не поддерживаются: {e}")

    assert _never_serve(str(link)) is True

    r = client.get(f"/api/waveform?path={link}", headers=H)
    assert r.status_code == 403
    assert fake_librosa == []
    assert not os.path.exists(str(link) + ".peaks80.json")


def test_waveform_missing_file_is_no_file(client, tmp_path):
    """Протухший путь с разрешённым расширением — прежний ответ «нет файла», не 403."""
    missing = tmp_path / "уже_удалён.wav"
    r = client.get(f"/api/waveform?path={missing}", headers=H)
    assert r.status_code == 200
    assert r.get_json().get("err") == "no_file"


# --------------------------------------------------------------------------- #
# 2. Кэш каталога моделей: уникальный временный файл
# --------------------------------------------------------------------------- #
def test_save_disk_unique_tmp_no_garbage(tmp_path, monkeypatch):
    """Временный файл уникален на каждый вызов, мусора рядом нет, итог — последняя запись."""
    from core.aicut import catalog

    path = str(tmp_path / "models_dev.json")
    seen = []
    real_replace = os.replace

    def spy_replace(src, dst):
        seen.append((os.path.basename(src), os.path.basename(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(catalog.os, "replace", spy_replace)

    catalog._save_disk(path, {"v": 1})
    catalog._save_disk(path, {"v": 2})

    with open(path, encoding="utf-8") as fh:
        assert json.load(fh) == {"v": 2}
    assert [dst for _src, dst in seen] == ["models_dev.json", "models_dev.json"]
    # Фиксированного <path>.tmp нет: с ним два «Обновить список» пишут в один файл
    # и перемешивают содержимое кэша.
    assert all(src != "models_dev.json.tmp" for src, _dst in seen), seen
    assert seen[0][0] != seen[1][0], f"имя временного файла не уникально: {seen}"
    assert os.listdir(tmp_path) == ["models_dev.json"]


def test_save_disk_cleans_tmp_when_replace_fails(tmp_path, monkeypatch):
    """replace не состоялся — временный файл убран за собой, ошибка по-прежнему глушится."""
    from core.aicut import catalog

    path = str(tmp_path / "models_dev.json")

    def broken_replace(src, dst):
        raise OSError("занято")

    monkeypatch.setattr(catalog.os, "replace", broken_replace)

    catalog._save_disk(path, {"v": 1})          # исключение наружу не летит: кэш некритичен
    assert os.listdir(tmp_path) == []
    assert not os.path.exists(path)


# --------------------------------------------------------------------------- #
# 3. Веса whisper.cpp: закреплённая ревизия
# --------------------------------------------------------------------------- #
def test_ensure_model_pins_revision(tmp_path, monkeypatch):
    """hf_hub_download зовётся с непустым revision из константы, а не из main."""
    from core import whisper_cpp

    monkeypatch.setattr(whisper_cpp, "MODELS_DIR", str(tmp_path))
    calls = []

    def fake_download(**kwargs):
        calls.append(kwargs)
        return os.path.join(kwargs["local_dir"], kwargs["filename"])

    monkeypatch.setitem(sys.modules, "huggingface_hub", _fake_hf(fake_download))

    assert whisper_cpp.ensure_model("large-v3") == str(tmp_path / "ggml-large-v3.bin")
    assert len(calls) == 1
    assert calls[0]["repo_id"] == whisper_cpp.HF_REPO
    assert calls[0]["filename"] == "ggml-large-v3.bin"
    assert calls[0]["local_dir"] == str(tmp_path)
    revision = calls[0].get("revision")
    assert revision, "веса качаются без revision — то есть из main на момент скачивания"
    assert revision == whisper_cpp.HF_REVISION
    assert re.fullmatch(r"[0-9a-f]{40}", revision), f"ревизия не полный коммит-хеш: {revision}"


def test_ensure_model_local_file_goes_offline(tmp_path, monkeypatch):
    """Файл уже лежит в MODELS_DIR — в сеть не ходим вовсе (закрепление скачанных не ломает)."""
    from core import whisper_cpp

    monkeypatch.setattr(whisper_cpp, "MODELS_DIR", str(tmp_path))
    local = tmp_path / "ggml-tiny.bin"
    local.write_bytes(b"ggml-weights")

    def forbidden(**kwargs):
        raise AssertionError(f"сеть: hf_hub_download позвали при скачанной модели ({kwargs})")

    monkeypatch.setitem(sys.modules, "huggingface_hub", _fake_hf(forbidden))

    assert whisper_cpp.ensure_model("tiny") == str(local)
