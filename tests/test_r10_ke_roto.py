# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты контрактов авто-ротоскопа: передача failures, точность ключа кэша и ReelsiError при сбоях."""

import json
import os
import pytest

from core import roto
from core.xml2ae import Cancelled
from core.xml2ae.build import _roto_js
from core.umsg import ReelsiError


@pytest.fixture
def mock_roto_deps(monkeypatch):
    """Мокаем видео-зависимости (ffmpeg, GPU-модель) для изоляции модульных тестов."""
    monkeypatch.setattr(roto, "_probe", lambda video: (1920, 1080, 25.0, 100.0))
    monkeypatch.setattr(roto, "_pick_device", lambda force=None: "cuda")
    monkeypatch.setattr(roto, "_has_frames", lambda mask: False)
    monkeypatch.setattr(roto, "release", lambda emit=None: None)


def test_alpha_for_ranges_segment_error(tmp_path, monkeypatch, mock_roto_deps):
    """Ошибка сегмента (не OOM) — этот кусок пишется в failures, цикл идёт дальше."""
    video = str(tmp_path / "cam1.mp4")
    with open(video, "wb") as f:
        f.write(b"video_dummy")

    def mock_alpha_for_video(vid, out_mask, **kwargs):
        s = kwargs.get("start", 0.0)
        if abs(s - 2.0) < 0.01:
            raise RuntimeError("boom")
        return (out_mask, 2.0)

    monkeypatch.setattr(roto, "alpha_for_video", mock_alpha_for_video)

    failures = []
    ranges = [(0.0, 2.0), (2.0, 4.0), (4.0, 6.0)]
    out_dir = str(tmp_path / "out")

    res = roto.alpha_for_ranges(video, ranges, out_dir, failures=failures)

    assert len(res) == 2
    assert [m["start"] for m in res] == [0.0, 4.0]
    assert len(failures) == 1
    assert failures[0]["start"] == 2.0
    assert failures[0]["end"] == 4.0
    assert "boom" in failures[0]["error"]


def test_alpha_for_ranges_oom_with_retry_failure(tmp_path, monkeypatch, mock_roto_deps):
    """OOM на втором куске и OOM на ретрае — в failures второй и все следующие (кроме микро), выход из цикла."""
    video = str(tmp_path / "cam1.mp4")
    with open(video, "wb") as f:
        f.write(b"video_dummy")

    def mock_alpha_for_video(vid, out_mask, **kwargs):
        s = kwargs.get("start", 0.0)
        if s >= 2.0:
            raise RuntimeError("CUDA out of memory")
        return (out_mask, 2.0)

    monkeypatch.setattr(roto, "alpha_for_video", mock_alpha_for_video)

    failures = []
    # Второй кусок (2.0-4.0), третий кусок микро (4.0-4.05 < 0.15), четвёртый (4.05-6.0)
    ranges = [(0.0, 2.0), (2.0, 4.0), (4.0, 4.05), (4.05, 6.0)]
    out_dir = str(tmp_path / "out")

    res = roto.alpha_for_ranges(video, ranges, out_dir, failures=failures)

    assert len(res) == 1
    assert res[0]["start"] == 0.0
    # Второй и четвёртый куски попали в failures; микро-кусок исключён
    assert len(failures) == 2
    assert failures[0]["start"] == 2.0
    assert failures[0]["end"] == 4.0
    assert failures[0]["error"] == "не хватает видеопамяти"
    assert failures[1]["start"] == 4.05
    assert failures[1]["end"] == 6.0
    assert failures[1]["error"] == "не хватает видеопамяти"


def test_alpha_for_ranges_empty_mask(tmp_path, monkeypatch, mock_roto_deps):
    """alpha_for_video вернул None (кадров нет) — кусок попадает в failures с 'маска пустая'."""
    video = str(tmp_path / "cam1.mp4")
    with open(video, "wb") as f:
        f.write(b"video_dummy")

    monkeypatch.setattr(roto, "alpha_for_video", lambda *a, **k: None)

    failures = []
    ranges = [(0.0, 2.0)]
    out_dir = str(tmp_path / "out")

    res = roto.alpha_for_ranges(video, ranges, out_dir, failures=failures)

    assert len(res) == 0
    assert len(failures) == 1
    assert failures[0]["start"] == 0.0
    assert failures[0]["end"] == 2.0
    assert failures[0]["error"] == "маска пустая"


def test_alpha_for_ranges_cancel(tmp_path, monkeypatch, mock_roto_deps):
    """«Стоп» (cancel()) на втором куске — failures пуст, цикл прерван."""
    video = str(tmp_path / "cam1.mp4")
    with open(video, "wb") as f:
        f.write(b"video_dummy")

    monkeypatch.setattr(roto, "alpha_for_video", lambda vid, mask, **kw: (mask, 2.0))

    call_count = [0]

    def cancel():
        call_count[0] += 1
        # На втором вызове (перед вторым куском) сигнализируем отмену
        return call_count[0] >= 2

    failures = []
    ranges = [(0.0, 2.0), (2.0, 4.0), (4.0, 6.0)]
    out_dir = str(tmp_path / "out")

    res = roto.alpha_for_ranges(video, ranges, out_dir, cancel=cancel, failures=failures)

    assert len(res) == 1
    assert failures == []


def test_mask_key_precision_and_size(tmp_path):
    """Два файла с одинаковым путём и секундой mtime, но разным st_mtime_ns или размером -> разные ключи."""
    f = tmp_path / "video.mp4"
    f.write_bytes(b"sample_content_v1")

    base_sec = 1_700_000_000
    ns1 = base_sec * 1_000_000_000 + 100_000
    os.utime(str(f), ns=(ns1, ns1))
    k1 = roto._mask_key(str(f), 0.0, 2.0, 0.0, 2.0)

    # Меняем только наносекунды (та же целая секунда mtime)
    ns2 = base_sec * 1_000_000_000 + 500_000
    os.utime(str(f), ns=(ns2, ns2))
    k2 = roto._mask_key(str(f), 0.0, 2.0, 0.0, 2.0)
    assert k1 != k2, "Разные наносекунды mtime должны давать разные ключи маски"

    # Меняем размер файла при тех же наносекундах mtime
    f.write_bytes(b"sample_content_v1_extended_size")
    os.utime(str(f), ns=(ns1, ns1))
    k3 = roto._mask_key(str(f), 0.0, 2.0, 0.0, 2.0)
    assert k1 != k3, "Разный размер файла должен давать разные ключи маски"


def test_mask_key_posix_case_sensitivity():
    """На POSIX (os.name != 'nt') пути A.mp4 и a.mp4 дают разные ключи."""
    if os.name == "nt":
        pytest.skip("На Windows normcase приводит путь к нижнему регистру (case-insensitive)")
    k_upper = roto._mask_key("/tmp/A.mp4", 0.0, 2.0, 0.0, 2.0)
    k_lower = roto._mask_key("/tmp/a.mp4", 0.0, 2.0, 0.0, 2.0)
    assert k_upper != k_lower


def test_roto_js_missing_mask_raises(tmp_path, monkeypatch):
    """_roto_js: одна маска из двух кусков -> ReelsiError, текст содержит «1 из 2»."""
    from core import roto as _roto

    plan = {
        "cams": [{"path": str(tmp_path / "cam1.mp4")}],
        "roto": [
            {"ci": 0, "src_start": 0.0, "src_end": 2.0, "ts": 0.0, "te": 2.0, "scale": 100},
            {"ci": 0, "src_start": 3.0, "src_end": 5.0, "ts": 3.0, "te": 5.0, "scale": 100},
        ],
    }

    def mock_alpha_for_ranges(video, ranges, out_dir, **kw):
        fail_list = kw.get("failures")
        if fail_list is not None:
            fail_list.append({"start": 3.0, "end": 5.0, "error": "маска пустая"})
        return [{"start": 0.0, "end": 2.0, "mask": "/cache/m1.mp4", "f": 2.0}]

    monkeypatch.setattr(_roto, "alpha_for_ranges", mock_alpha_for_ranges)
    monkeypatch.setattr(_roto, "release", lambda emit=None: None)

    kw = {"roto": True, "base": str(tmp_path)}
    with pytest.raises(ReelsiError) as exc:
        _roto_js(plan, str(tmp_path / "test.xml"), kw, emit=lambda *a, **k: None, cancel=lambda: False)

    assert "1 из 2" in str(exc.value)
    assert "маска пустая" in str(exc.value)


def test_roto_js_all_masks_present(tmp_path, monkeypatch):
    """_roto_js: все маски есть -> валидный JSON с двумя записями."""
    from core import roto as _roto

    plan = {
        "cams": [{"path": str(tmp_path / "cam1.mp4")}],
        "roto": [
            {"ci": 0, "src_start": 0.0, "src_end": 2.0, "ts": 0.0, "te": 2.0, "scale": 100},
            {"ci": 0, "src_start": 3.0, "src_end": 5.0, "ts": 3.0, "te": 5.0, "scale": 100},
        ],
    }

    def mock_alpha_for_ranges(video, ranges, out_dir, **kw):
        return [
            {"start": 0.0, "end": 2.0, "mask": "/cache/m1.mp4", "f": 2.0},
            {"start": 3.0, "end": 5.0, "mask": "/cache/m2.mp4", "f": 2.0},
        ]

    monkeypatch.setattr(_roto, "alpha_for_ranges", mock_alpha_for_ranges)
    monkeypatch.setattr(_roto, "release", lambda emit=None: None)

    kw = {"roto": True, "base": str(tmp_path)}
    res = _roto_js(plan, str(tmp_path / "test.xml"), kw, emit=lambda *a, **k: None, cancel=lambda: False)

    data = json.loads(res)
    assert len(data) == 2
    assert data[0]["mask"] == "/cache/m1.mp4"
    assert data[1]["mask"] == "/cache/m2.mp4"


def test_roto_js_micro_chunk_skipped_without_error(tmp_path, monkeypatch):
    """_roto_js: микро-кусок (< MIN_SEG_SEC) без маски -> ошибки нет."""
    from core import roto as _roto

    plan = {
        "cams": [{"path": str(tmp_path / "cam1.mp4")}],
        "roto": [
            {"ci": 0, "src_start": 0.0, "src_end": 2.0, "ts": 0.0, "te": 2.0, "scale": 100},
            {"ci": 0, "src_start": 2.5, "src_end": 2.55, "ts": 2.5, "te": 2.55, "scale": 100},
        ],
    }

    def mock_alpha_for_ranges(video, ranges, out_dir, **kw):
        # Возвращаем маску только для полноразмерного куска
        return [{"start": 0.0, "end": 2.0, "mask": "/cache/m1.mp4", "f": 2.0}]

    monkeypatch.setattr(_roto, "alpha_for_ranges", mock_alpha_for_ranges)
    monkeypatch.setattr(_roto, "release", lambda emit=None: None)

    kw = {"roto": True, "base": str(tmp_path)}
    res = _roto_js(plan, str(tmp_path / "test.xml"), kw, emit=lambda *a, **k: None, cancel=lambda: False)

    data = json.loads(res)
    assert len(data) == 1
    assert data[0]["mask"] == "/cache/m1.mp4"


def test_roto_js_import_error_raises_roto_failed(tmp_path, monkeypatch):
    """_roto_js: alpha_for_ranges бросает исключение -> ReelsiError с «рото не удалось»."""
    from core import roto as _roto

    plan = {
        "cams": [{"path": str(tmp_path / "cam1.mp4")}],
        "roto": [
            {"ci": 0, "src_start": 0.0, "src_end": 2.0, "ts": 0.0, "te": 2.0, "scale": 100},
        ],
    }

    def mock_alpha_for_ranges(video, ranges, out_dir, **kw):
        raise ImportError("No module named torch")

    monkeypatch.setattr(_roto, "alpha_for_ranges", mock_alpha_for_ranges)
    monkeypatch.setattr(_roto, "release", lambda emit=None: None)

    kw = {"roto": True, "base": str(tmp_path)}
    with pytest.raises(ReelsiError) as exc:
        _roto_js(plan, str(tmp_path / "test.xml"), kw, emit=lambda *a, **k: None, cancel=lambda: False)

    assert "рото не удалось" in str(exc.value)


def test_roto_js_cancelled_propagates(tmp_path, monkeypatch):
    """_roto_js: Cancelled пробрасывается напрямую."""
    from core import roto as _roto

    plan = {
        "cams": [{"path": str(tmp_path / "cam1.mp4")}],
        "roto": [
            {"ci": 0, "src_start": 0.0, "src_end": 2.0, "ts": 0.0, "te": 2.0, "scale": 100},
        ],
    }

    def mock_alpha_for_ranges(video, ranges, out_dir, **kw):
        raise Cancelled("Пользователь нажал Стоп")

    monkeypatch.setattr(_roto, "alpha_for_ranges", mock_alpha_for_ranges)
    monkeypatch.setattr(_roto, "release", lambda emit=None: None)

    kw = {"roto": True, "base": str(tmp_path)}
    with pytest.raises(Cancelled):
        _roto_js(plan, str(tmp_path / "test.xml"), kw, emit=lambda *a, **k: None, cancel=lambda: False)
