# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты фиксации удалённого кода и безопасной распаковки (задание HN)."""
import hashlib
import io
import platform
import sys
import tarfile
import zipfile
from unittest.mock import MagicMock

import numpy as np
import pytest

from core import breath, roto, whisper_cpp


def _asset_key():
    """Ключ ASSET_PLAN для текущей платформы — чтобы тест не зависел от архитектуры."""
    return (sys.platform, platform.machine().lower())


# ---------------------------------------------------------------------------
# 1. whisper.cpp: хеши, URL, безопасная распаковка
# ---------------------------------------------------------------------------

def test_whisper_cpp_constants():
    """Константы репозитория, версии и хешей для whisper.cpp."""
    assert getattr(whisper_cpp, "WHISPER_CPP_VERSION", None) == "v1.9.2"
    assert "ggml-org/whisper.cpp" in whisper_cpp.RELEASES_URL
    assert not hasattr(whisper_cpp, "GH_API")  # запрос к releases/latest удалён

    expected_hashes = {
        "whisper-bin-Win32.zip": "de170719aebcb4794d695d449e179002db1fe03b862f21f5c34b2909a7cf8f22",
        "whisper-bin-ubuntu-x64.tar.gz": "46811a3ecf584307480a220b9ef5ff81b7b22dc41577cbc274ce3afc61f753b1",
        "whisper-bin-ubuntu-arm64.tar.gz": "7e26fa6a36d9174d5c0bf033ccbc026c3b5e569e2ee787058241346ef5392719",
    }
    asset_sha = getattr(whisper_cpp, "ASSET_SHA256", {})
    for asset, h in expected_hashes.items():
        assert asset_sha.get(asset) == h


def test_whisper_cpp_rejects_wrong_hash(tmp_path, monkeypatch):
    """Неверный хеш скачанного файла вызывает ошибку до распаковки."""
    bin_dir = tmp_path / "bin"
    monkeypatch.setattr(whisper_cpp, "BIN_DIR", str(bin_dir))
    monkeypatch.setattr(whisper_cpp, "whisper_cli_path", lambda: None)
    monkeypatch.setattr(whisper_cpp, "ASSET_PLAN",
                        {_asset_key(): "whisper-bin-test.zip"})
    monkeypatch.setattr(whisper_cpp, "ASSET_SHA256",
                        {"whisper-bin-test.zip": "0" * 64})

    # Скачиваем ПРАВИЛЬНЫЙ архив, но с хешем, которого нет в ASSET_SHA256:
    # так проверяется именно сверка, а не разбор мусора.
    archive = tmp_path / "asset.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(whisper_cpp._BIN_NAME, b"real payload")
    data = archive.read_bytes()

    mock_resp = MagicMock()
    mock_resp.read.return_value = data
    monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout=None: mock_resp)

    with pytest.raises(RuntimeError, match=r"контрольная сумма.*не совпала"):
        whisper_cpp.install_cli(emit=lambda *a, **kw: None)

    # До распаковки дело не дошло: BIN_DIR даже не создан
    assert not bin_dir.exists()


def test_whisper_cpp_rejects_unsafe_zip(tmp_path):
    """Архив zip с выходом наружу (../), абсолютным путём или диском Windows отвергается."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)

    bad_names = ["../evil.txt", "/root/evil.txt", "C:/Windows/evil.txt",
                 "..\\evil.txt", "release/../../evil.txt"]
    for i, bad in enumerate(bad_names):
        archive = tmp_path / f"bad{i}.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr(bad, b"malicious")
            z.writestr("whisper-cli.exe", b"bin")
        with zipfile.ZipFile(archive) as z:
            with pytest.raises(RuntimeError, match=r"небезопасный путь"):
                whisper_cpp._safe_extract_zip(z, str(bin_dir))

    # Ни один зловредный файл не появился ни внутри, ни рядом
    assert not (tmp_path / "evil.txt").exists()
    assert sorted(p.name for p in bin_dir.iterdir()) == []
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "bad0.zip", "bad1.zip", "bad2.zip", "bad3.zip", "bad4.zip", "bin"]


def test_whisper_cpp_rejects_unsafe_tar(tmp_path):
    """Архив tar с выходом наружу, symlink или спецфайлом отвергается."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)

    # 1. Архив с выходом наверх
    archive = tmp_path / "escape.tar.gz"
    with tarfile.open(archive, "w:gz") as t:
        ti = tarfile.TarInfo(name="../escape.txt")
        ti.size = 4
        t.addfile(ti, io.BytesIO(b"test"))
    with tarfile.open(archive, "r:gz") as t:
        with pytest.raises(RuntimeError, match=r"небезопасный путь"):
            whisper_cpp._safe_extract_tar(t, str(bin_dir))

    # 2. Архив с symlink
    link_archive = tmp_path / "link.tar.gz"
    with tarfile.open(link_archive, "w:gz") as t:
        ti = tarfile.TarInfo(name="link_file")
        ti.type = tarfile.SYMTYPE
        ti.linkname = "/etc/passwd"
        t.addfile(ti)
    with tarfile.open(link_archive, "r:gz") as t:
        with pytest.raises(RuntimeError, match=r"ссылки запрещены"):
            whisper_cpp._safe_extract_tar(t, str(bin_dir))

    # 3. Архив с устройством (на 3.10 у tarfile нет filter= — проверяем руками)
    dev_archive = tmp_path / "dev.tar.gz"
    with tarfile.open(dev_archive, "w:gz") as t:
        ti = tarfile.TarInfo(name="dev_null")
        ti.type = tarfile.CHRTYPE
        ti.devmajor, ti.devminor = 1, 3
        t.addfile(ti)
    with tarfile.open(dev_archive, "r:gz") as t:
        with pytest.raises(RuntimeError, match=r"спецфайлы"):
            whisper_cpp._safe_extract_tar(t, str(bin_dir))

    assert not (tmp_path / "escape.txt").exists()
    assert sorted(p.name for p in bin_dir.iterdir()) == []


def test_whisper_cpp_installs_valid_archive(tmp_path, monkeypatch):
    """Корректный архив с верным хешем распаковывается и бинарник находится."""
    bin_name = whisper_cpp._BIN_NAME
    bin_dir = tmp_path / "bin"
    monkeypatch.setattr(whisper_cpp, "BIN_DIR", str(bin_dir))

    # Собираем валидный zip на диске и берём его sha256 как «эталон релиза»
    archive = tmp_path / "asset.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("Release/" + bin_name, b"dummy binary payload")
    data = archive.read_bytes()
    valid_sha = hashlib.sha256(data).hexdigest()

    monkeypatch.setattr(whisper_cpp, "ASSET_PLAN",
                        {_asset_key(): "whisper-bin-test.zip"})
    monkeypatch.setattr(whisper_cpp, "ASSET_SHA256", {
        "whisper-bin-test.zip": valid_sha,
    })

    mock_resp = MagicMock()
    mock_resp.read.return_value = data
    urls_called = []

    def mock_urlopen(url, timeout=None):
        urls_called.append(url)
        return mock_resp

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    # Бинарник ищется рекурсивно настоящей whisper_cli_path (архив релиза кладёт
    # его в Release/), но PATH и REELSI_WHISPER_CLI отвязаны от машины.
    monkeypatch.delenv("REELSI_WHISPER_CLI", raising=False)
    monkeypatch.setattr(whisper_cpp.shutil, "which", lambda _name: None)

    res = whisper_cpp.install_cli(emit=lambda *a, **kw: None)
    assert res == str(bin_dir / "Release" / bin_name)
    assert (bin_dir / "Release" / bin_name).exists()
    assert urls_called[0] == "https://github.com/ggml-org/whisper.cpp/releases/download/v1.9.2/whisper-bin-test.zip"


# ---------------------------------------------------------------------------
# 2. RobustVideoMatting: коммит SHA и skip_validation
# ---------------------------------------------------------------------------

def test_rvm_pinned_commit_and_skip_validation(monkeypatch):
    """RVM загружается из строго закреплённого коммита с skip_validation=True."""
    pytest.importorskip("torch")
    monkeypatch.setattr(roto, "_MODEL", None)
    expected_repo = "PeterL1n/RobustVideoMatting:53d74c6826735f01f4406b5ca9075eee27bec094"

    calls = []

    def mock_hub_load(repo_or_dir, model, *args, **kwargs):
        calls.append((repo_or_dir, model, args, kwargs))
        m = MagicMock()
        m.to.return_value.eval.return_value = m
        return m

    import torch.hub
    monkeypatch.setattr(torch.hub, "load", mock_hub_load)
    monkeypatch.setattr(roto, "_pick_device", lambda force=None: "cpu")

    roto._load()

    assert len(calls) == 1
    repo_arg, model_arg, _, kwargs_arg = calls[0]
    assert repo_arg == expected_repo
    assert model_arg == roto._VARIANT
    assert kwargs_arg.get("skip_validation") is True


# ---------------------------------------------------------------------------
# 3. ced-tiny: ревизия коммита
# ---------------------------------------------------------------------------

def test_breath_ced_pinned_revision(monkeypatch):
    """ced-tiny загружается со строго закреплённой ревизией."""
    pytest.importorskip("torch")
    monkeypatch.setattr(breath, "_CED", None)
    expected_rev = "ace276d29dd0bb3f3517b0fa8cf300738c409019"

    fe_calls = []
    m_calls = []

    class MockFE:
        @classmethod
        def from_pretrained(cls, pretrained_model_name_or_path, *args, **kwargs):
            fe_calls.append((pretrained_model_name_or_path, args, kwargs))
            fe = MagicMock()
            fe.return_value = {"input_values": MagicMock()}
            return fe

    class MockModel:
        @classmethod
        def from_pretrained(cls, pretrained_model_name_or_path, *args, **kwargs):
            m_calls.append((pretrained_model_name_or_path, args, kwargs))
            m = MagicMock()
            m.config.num_labels = 2
            logits_mock = MagicMock()
            logits_mock.float.return_value.cpu.return_value.numpy.return_value = np.zeros((1, 2), dtype="float32")
            m.return_value.logits = logits_mock
            m.to.return_value.eval.return_value = m
            return m

    import types
    fake_transformers = types.ModuleType("transformers")
    fake_transformers.AutoFeatureExtractor = MockFE
    fake_transformers.AutoModelForAudioClassification = MockModel
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)
    monkeypatch.setattr(breath, "pick_device", lambda: "cpu")

    y = np.zeros(16000, dtype="float32")
    spans = [(0.1, 0.5)]

    # Вызываем _ced
    breath._ced(y, spans, batch=1)

    assert getattr(breath, "CED_REVISION", None) == expected_rev
    assert len(fe_calls) == 1
    assert len(m_calls) == 1
    assert fe_calls[0][0] == breath.CED_ID
    assert fe_calls[0][2].get("revision") == expected_rev
    assert fe_calls[0][2].get("trust_remote_code") is True
    assert m_calls[0][0] == breath.CED_ID
    assert m_calls[0][2].get("revision") == expected_rev
    assert m_calls[0][2].get("trust_remote_code") is True
