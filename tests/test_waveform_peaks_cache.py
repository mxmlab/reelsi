# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Кэш пиков волны (/api/waveform) — служебная папка и версия исходника.

Прежде кэш писался `<файл>.peaks<pps>.json` РЯДОМ с медиа и не знал версии файла:
изменил исходник — волна рисовалась по старым пикам, а кэш-файлы мусорили в папках
владельца и оставались после удаления клипа. Правило теперь такое же, как у прокси:
ключ — версия исходника (путь, mtime, размер) + pps, кэш — в REELSI_PEAKS_CACHE.

Сеть и librosa не нужны: librosa подменён, длина сигнала зависит от размера файла.
"""
import os
import sys
import time
import types

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import api  # noqa: E402
import api.files as files_api  # noqa: E402
import core.draftrender as draftrender  # noqa: E402

H = {"Host": "127.0.0.1:5001"}
DAY = 86400


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def librosa_calls(monkeypatch):
    """Поддельный librosa: длина сигнала = размер файла * 100 сэмплов (sr 16 кГц).
    Список — пути, которые дошли до пересчёта; по нему видно, был ли кэш."""
    calls = []
    mod = types.ModuleType("librosa")

    def load(path, sr=16000, mono=True):
        calls.append(path)
        return np.zeros(os.path.getsize(path) * 100, dtype="float32"), 16000

    mod.load = load
    monkeypatch.setitem(sys.modules, "librosa", mod)
    return calls


def _wave(client, path, pps=80):
    r = client.get("/api/waveform", query_string={"path": str(path), "pps": pps}, headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True
    return d


def _write(path, nbytes, mtime=None):
    path.write_bytes(b"x" * nbytes)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


@pytest.fixture
def media(tmp_path):
    d = tmp_path / "media"
    d.mkdir()
    return d


def test_peaks_cache_goes_to_service_dir_not_next_to_media(client, tmp_path, media, librosa_calls):
    """Кэш пиков — в служебной папке; рядом с медиа ничего не появляется."""
    audio = media / "clip.wav"
    _write(audio, 10)

    d = _wave(client, audio)
    assert d["pps"] == 80 and d["dur"] == round(10 * 100 / 16000, 3)

    cache = files_api.peaks_cache_path(str(audio), 80)
    assert os.path.isfile(cache)
    assert os.path.dirname(os.path.abspath(cache)) == os.path.abspath(str(tmp_path / "_peaks"))
    assert os.listdir(media) == ["clip.wav"], "рядом с медиа появился кэш"


def test_second_request_is_served_from_cache(client, media, librosa_calls):
    audio = media / "clip.wav"
    _write(audio, 10)
    _wave(client, audio)
    _wave(client, audio)
    assert len(librosa_calls) == 1, "второй запрос обязан обойтись кэшем"


@pytest.mark.parametrize("change", ["size", "mtime"])
def test_changed_source_is_recomputed(client, media, librosa_calls, change):
    """Изменил исходник — волна пересчитывается. Размер и дата — два независимых признака:
    каждый по отдельности обязан дать новый ключ (тот же правило, что у прокси)."""
    audio = media / "clip.wav"
    t0 = time.time() - 1000
    _write(audio, 10, mtime=t0)
    d1 = _wave(client, audio)
    assert len(librosa_calls) == 1

    if change == "size":
        _write(audio, 30, mtime=t0)          # та же дата, другой размер
    else:
        _write(audio, 10, mtime=t0 + 500)    # тот же размер, другая дата
    d2 = _wave(client, audio)

    assert len(librosa_calls) == 2, "изменённый файл обязан пересчитаться, а не отдать старые пики"
    if change == "size":
        assert d2["dur"] != d1["dur"], "новые пики должны идти от нового содержимого"


def test_legacy_peaks_next_to_source_are_removed(client, media, librosa_calls):
    """Старые `<имя>.peaks<pps>.json` рядом с этим файлом — удаляются при обращении.
    Соседние файлы с другим именем не трогаем."""
    audio = media / "clip.wav"
    _write(audio, 10)
    (media / "clip.wav.peaks80.json").write_text('{"peaks": [9.0]}', encoding="utf-8")
    (media / "clip.wav.peaks10.json").write_text('{"peaks": [9.0]}', encoding="utf-8")
    (media / "other.wav.peaks80.json").write_text('{"peaks": [1.0]}', encoding="utf-8")

    d = _wave(client, audio)

    assert d["peaks"] != [9.0], "отдали старый кэш рядом с файлом"
    assert sorted(os.listdir(media)) == ["clip.wav", "other.wav.peaks80.json"]


def test_two_pps_are_two_caches(client, tmp_path, media, librosa_calls):
    audio = media / "clip.wav"
    _write(audio, 10)
    _wave(client, audio, pps=80)
    _wave(client, audio, pps=100)
    _wave(client, audio, pps=80)
    _wave(client, audio, pps=100)

    assert files_api.peaks_cache_path(str(audio), 80) != files_api.peaks_cache_path(str(audio), 100)
    assert len(os.listdir(tmp_path / "_peaks")) == 2
    assert len(librosa_calls) == 2, "каждый pps считается один раз и дальше идёт из кэша"


def test_stale_entries_are_pruned_on_write(client, tmp_path, media, librosa_calls):
    """Записи старше 60 дней уходят при записи новой; свежие и чужие файлы — нет."""
    cache_dir = tmp_path / "_peaks"
    cache_dir.mkdir()
    old = cache_dir / "peaks_0123456789abcdef.json"
    fresh = cache_dir / "peaks_fedcba9876543210.json"
    foreign = cache_dir / "notes.json"
    for f in (old, fresh, foreign):
        f.write_text("{}", encoding="utf-8")
    now = time.time()
    os.utime(old, (now - 61 * DAY, now - 61 * DAY))
    os.utime(fresh, (now - 1 * DAY, now - 1 * DAY))
    os.utime(foreign, (now - 61 * DAY, now - 61 * DAY))

    audio = media / "clip.wav"
    _write(audio, 10)
    _wave(client, audio)

    assert not old.exists(), "запись старше 60 дней не удалена"
    assert fresh.exists()
    assert foreign.exists(), "чужой файл в служебной папке не наш — трогать нельзя"


def test_proxy_and_waveform_share_one_source_version(monkeypatch, media):
    """Одно правило версии исходника: ключ прокси = версия файла + поворот, без второй копии."""
    monkeypatch.setattr(draftrender, "_rot_key", lambda src: "|ROT")
    audio = media / "clip.wav"
    _write(audio, 10)
    assert draftrender._src_version(str(audio)) == draftrender.src_file_version(str(audio)) + "|ROT"
