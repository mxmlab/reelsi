# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Защита прокси черновика и превью от авто-уборки _tmp.

В _tmp сосуществуют два кэша:
- pv_*.mp4 — превью-прокси плеера (build_preview_proxy);
- proxy_<hash>.mp4 — 720p-прокси черновика (_proxy_path).
Авто-очистка clean_tmp(outdir, proxies=False) перед новой нарезкой обязана беречь оба кэша,
иначе каждая нарезка сбрасывала бы кэш черновика и замедляла последующие рендеры на десятки секунд.
При ручной уборке clean_tmp(outdir, proxies=True) удаляются оба кэша.
Функция proxy_size(outdir) должна суммировать размер обоих видов прокси.
"""
from core import draftrender


def test_clean_tmp_keeps_both_proxy_types(tmp_path):
    """По умолчанию clean_tmp оставляет и pv_*.mp4, и proxy_*.mp4, удаляя остальной мусор."""
    tdir = tmp_path / "_tmp"
    tdir.mkdir()

    pv = tdir / "pv_a.mp4"
    pv.write_bytes(b"preview_data_123")  # 16 bytes

    draft = tdir / "proxy_0123456789ab.mp4"
    draft.write_bytes(b"draft_proxy_data_456")  # 20 bytes

    junk = tdir / "junk.wav"
    junk.write_bytes(b"junk_audio_bytes")  # 16 bytes

    freed = draftrender.clean_tmp(str(tmp_path), emit=lambda *a, **k: None, proxies=False)

    assert pv.is_file(), "pv_a.mp4 должен сохраниться"
    assert draft.is_file(), "proxy_*.mp4 должен сохраниться при авто-уборке"
    assert not junk.exists(), "junk.wav должен быть удалён"
    assert freed == 16


def test_clean_tmp_removes_all_proxies_when_proxies_true(tmp_path):
    """При явной уборке (proxies=True) удаляются оба вида прокси и весь _tmp."""
    tdir = tmp_path / "_tmp"
    tdir.mkdir()

    pv = tdir / "pv_a.mp4"
    pv.write_bytes(b"preview_data_123")  # 16 bytes

    draft = tdir / "proxy_0123456789ab.mp4"
    draft.write_bytes(b"draft_proxy_data_456")  # 20 bytes

    junk = tdir / "junk.wav"
    junk.write_bytes(b"junk_audio_bytes")  # 16 bytes

    freed = draftrender.clean_tmp(str(tmp_path), emit=lambda *a, **k: None, proxies=True)

    assert not pv.exists(), "pv_*.mp4 должен быть удалён при proxies=True"
    assert not draft.exists(), "proxy_*.mp4 должен быть удалён при proxies=True"
    assert not junk.exists(), "junk.wav должен быть удалён"
    assert not tdir.exists(), "папка _tmp должна быть очищена полностью"
    assert freed == 16 + 20 + 16


def test_proxy_size_counts_both_proxy_types(tmp_path):
    """proxy_size должен учитывать и pv_*.mp4, и proxy_*.mp4, но не сторонние файлы."""
    tdir = tmp_path / "_tmp"
    tdir.mkdir()

    pv = tdir / "pv_a.mp4"
    pv.write_bytes(b"a" * 100)

    draft = tdir / "proxy_0123456789ab.mp4"
    draft.write_bytes(b"b" * 250)

    junk = tdir / "junk.wav"
    junk.write_bytes(b"c" * 500)

    size = draftrender.proxy_size(str(tmp_path))
    assert size == 350, f"proxy_size должен вернуть 350 (100+250), а вернул {size}"
