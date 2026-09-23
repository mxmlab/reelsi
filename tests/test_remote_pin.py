# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты фиксации удалённого кода и безопасной распаковки."""
import hashlib
import io
import os
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
    assert sorted(p.name for p in bin_dir.iterdir()) == []

    # 2. Архив с symlink на /etc/passwd
    link_archive = tmp_path / "link.tar.gz"
    with tarfile.open(link_archive, "w:gz") as t:
        ti = tarfile.TarInfo(name="link_file")
        ti.type = tarfile.SYMTYPE
        ti.linkname = "/etc/passwd"
        t.addfile(ti)
    with tarfile.open(link_archive, "r:gz") as t:
        with pytest.raises(RuntimeError, match=r"небезопасная ссылка"):
            whisper_cpp._safe_extract_tar(t, str(bin_dir))
    assert sorted(p.name for p in bin_dir.iterdir()) == []

    # 3. Симлинк с linkname="../x"
    archive_dotdot = tmp_path / "symlink_dotdot.tar.gz"
    with tarfile.open(archive_dotdot, "w:gz") as t:
        ti = tarfile.TarInfo(name="sub/link")
        ti.type = tarfile.SYMTYPE
        ti.linkname = "../x"
        t.addfile(ti)
    with tarfile.open(archive_dotdot, "r:gz") as t:
        with pytest.raises(RuntimeError, match=r"небезопасная ссылка"):
            whisper_cpp._safe_extract_tar(t, str(bin_dir))
    assert sorted(p.name for p in bin_dir.iterdir()) == []

    # 4. Симлинк с linkname="a/../b" (компонент .. запрещён целиком)
    archive_norm_dotdot = tmp_path / "symlink_norm_dotdot.tar.gz"
    with tarfile.open(archive_norm_dotdot, "w:gz") as t:
        ti = tarfile.TarInfo(name="link")
        ti.type = tarfile.SYMTYPE
        ti.linkname = "a/../b"
        t.addfile(ti)
    with tarfile.open(archive_norm_dotdot, "r:gz") as t:
        with pytest.raises(RuntimeError, match=r"небезопасная ссылка"):
            whisper_cpp._safe_extract_tar(t, str(bin_dir))
    assert sorted(p.name for p in bin_dir.iterdir()) == []

    # 5. Симлинк с диском Windows (C:\x)
    archive_win_abs = tmp_path / "symlink_win_abs.tar.gz"
    with tarfile.open(archive_win_abs, "w:gz") as t:
        ti = tarfile.TarInfo(name="link")
        ti.type = tarfile.SYMTYPE
        ti.linkname = "C:\\x"
        t.addfile(ti)
    with tarfile.open(archive_win_abs, "r:gz") as t:
        with pytest.raises(RuntimeError, match=r"небезопасная ссылка"):
            whisper_cpp._safe_extract_tar(t, str(bin_dir))
    assert sorted(p.name for p in bin_dir.iterdir()) == []

    # 6. Хардлинк на несуществующий в архиве член
    archive_bad_hardlink = tmp_path / "bad_hardlink.tar.gz"
    with tarfile.open(archive_bad_hardlink, "w:gz") as t:
        ti = tarfile.TarInfo(name="link_hard")
        ti.type = tarfile.LNKTYPE
        ti.linkname = "nonexistent_file"
        t.addfile(ti)
    with tarfile.open(archive_bad_hardlink, "r:gz") as t:
        with pytest.raises(RuntimeError, match=r"небезопасная ссылка"):
            whisper_cpp._safe_extract_tar(t, str(bin_dir))
    assert sorted(p.name for p in bin_dir.iterdir()) == []

    # 7. Хардлинк на ../x
    archive_dotdot_hardlink = tmp_path / "dotdot_hardlink.tar.gz"
    with tarfile.open(archive_dotdot_hardlink, "w:gz") as t:
        ti = tarfile.TarInfo(name="link_hard")
        ti.type = tarfile.LNKTYPE
        ti.linkname = "../x"
        t.addfile(ti)
    with tarfile.open(archive_dotdot_hardlink, "r:gz") as t:
        with pytest.raises(RuntimeError, match=r"небезопасная ссылка"):
            whisper_cpp._safe_extract_tar(t, str(bin_dir))
    assert sorted(p.name for p in bin_dir.iterdir()) == []

    # 8. Запись сквозь ссылку (lib -> sub + файл lib/evil)
    archive_through_symlink = tmp_path / "through_symlink.tar.gz"
    with tarfile.open(archive_through_symlink, "w:gz") as t:
        ti_sym = tarfile.TarInfo(name="lib")
        ti_sym.type = tarfile.SYMTYPE
        ti_sym.linkname = "sub"
        t.addfile(ti_sym)
        ti_file = tarfile.TarInfo(name="lib/evil")
        ti_file.size = 4
        t.addfile(ti_file, io.BytesIO(b"evil"))
    with tarfile.open(archive_through_symlink, "r:gz") as t:
        with pytest.raises(RuntimeError, match=r"небезопасная ссылка"):
            whisper_cpp._safe_extract_tar(t, str(bin_dir))
    assert sorted(p.name for p in bin_dir.iterdir()) == []

    # 9. Архив с устройством (на 3.10 у tarfile нет filter= — проверяем руками)
    dev_archive = tmp_path / "dev.tar.gz"
    with tarfile.open(dev_archive, "w:gz") as t:
        ti = tarfile.TarInfo(name="dev_null")
        ti.type = tarfile.CHRTYPE
        ti.devmajor, ti.devminor = 1, 3
        t.addfile(ti)
    with tarfile.open(dev_archive, "r:gz") as t:
        with pytest.raises(RuntimeError, match=r"спецфайлы"):
            whisper_cpp._safe_extract_tar(t, str(bin_dir))
    assert sorted(p.name for p in bin_dir.iterdir()) == []

    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path / "evil").exists()
    assert sorted(p.name for p in bin_dir.iterdir()) == []


def _can_create_symlinks(tmp_path):
    """Проверить, разрешено ли создание симлинков в текущей ОС/окружении."""
    src = tmp_path / "_test_symlink_src"
    dst = tmp_path / "_test_symlink_dst"
    src.write_text("test")
    try:
        os.symlink(src, dst)
        return True
    except OSError:
        return False
    finally:
        try:
            dst.unlink(missing_ok=True)
            src.unlink(missing_ok=True)
        except Exception:
            pass


def _create_release_like_tar(archive_path):
    """Собрать tar-архив, аналогичный официальному Linux-релизу whisper.cpp.

    Содержит каталог build/bin/, бинарник whisper-cli, базовые библиотеки и
    8 цепочек внутренних относительных симлинков.
    """
    files = {
        "build/bin/whisper-cli": b"elf-payload-cli",
        "build/bin/libwhisper.so.1.9.2": b"elf-payload-whisper",
        "build/bin/libggml.so.0.9.0": b"elf-payload-ggml",
        "build/bin/libggml-base.so.0.9.0": b"elf-payload-ggml-base",
        "build/bin/libggml-cpu.so.0.9.0": b"elf-payload-ggml-cpu",
    }
    symlinks = {
        "build/bin/libwhisper.so.1": "libwhisper.so.1.9.2",
        "build/bin/libwhisper.so": "libwhisper.so.1",
        "build/bin/libggml.so.0": "libggml.so.0.9.0",
        "build/bin/libggml.so": "libggml.so.0",
        "build/bin/libggml-base.so.0": "libggml-base.so.0.9.0",
        "build/bin/libggml-base.so": "libggml-base.so.0",
        "build/bin/libggml-cpu.so.0": "libggml-cpu.so.0.9.0",
        "build/bin/libggml-cpu.so": "libggml-cpu.so.0",
    }
    with tarfile.open(archive_path, "w:gz") as t:
        for name, content in files.items():
            ti = tarfile.TarInfo(name=name)
            ti.size = len(content)
            ti.type = tarfile.REGTYPE
            t.addfile(ti, io.BytesIO(content))
        for name, linkname in symlinks.items():
            ti = tarfile.TarInfo(name=name)
            ti.type = tarfile.SYMTYPE
            ti.linkname = linkname
            t.addfile(ti)
    return files, symlinks


def test_whisper_cpp_release_tar_validation_passes(tmp_path, monkeypatch):
    """Валидация архива с цепочками внутренних симлинков проходит без извлечения."""
    archive = tmp_path / "release_validate.tar.gz"
    _create_release_like_tar(archive)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    # Подменяем extractall на заглушку: тест разбора и валидации должен
    # выполняться всегда, даже если у текущего пользователя нет прав на symlink.
    monkeypatch.setattr(tarfile.TarFile, "extractall", lambda *a, **kw: None)

    with tarfile.open(archive, "r:gz") as t:
        whisper_cpp._safe_extract_tar(t, str(bin_dir))


def test_whisper_cpp_extracts_release_like_tar(tmp_path):
    """Архив релиза с 8 цепочечными симлинками успешно распаковывается."""
    if not _can_create_symlinks(tmp_path):
        pytest.skip("Создание симлинков требует повышенных привилегий на Windows")

    archive = tmp_path / "release.tar.gz"
    files, symlinks = _create_release_like_tar(archive)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    with tarfile.open(archive, "r:gz") as t:
        whisper_cpp._safe_extract_tar(t, str(bin_dir))

    # Все файлы на месте
    for rel_path in files:
        target = bin_dir / rel_path
        assert target.is_file()
        assert target.read_bytes() == files[rel_path]

    # Все 8 симлинков на месте, читаются и указывают внутрь bin_dir
    real_bin_dir = os.path.realpath(str(bin_dir))
    for rel_path, expected_linkname in symlinks.items():
        link_path = bin_dir / rel_path
        assert os.path.islink(link_path)
        assert os.readlink(link_path) == expected_linkname
        real_link = os.path.realpath(str(link_path))
        assert real_link == real_bin_dir or real_link.startswith(real_bin_dir + os.sep)


def test_whisper_cpp_install_cli_linux_tar(tmp_path, monkeypatch):
    """install_cli на платформе Linux скачивает и распаковывает release-архив с whisper-cli."""
    if not _can_create_symlinks(tmp_path):
        pytest.skip("Создание симлинков требует повышенных привилегий на Windows")

    bin_dir = tmp_path / "bin"
    archive = tmp_path / "downloaded.tar.gz"
    _create_release_like_tar(archive)
    archive_bytes = archive.read_bytes()
    expected_sha = hashlib.sha256(archive_bytes).hexdigest()

    asset_name = "whisper-bin-ubuntu-x64.tar.gz"
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(whisper_cpp, "BIN_DIR", str(bin_dir))
    monkeypatch.setitem(whisper_cpp.ASSET_SHA256, asset_name, expected_sha)
    monkeypatch.delenv("REELSI_WHISPER_CLI", raising=False)
    monkeypatch.setattr(whisper_cpp.shutil, "which", lambda _name: None)

    mock_resp = MagicMock()
    mock_resp.read.return_value = archive_bytes
    monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout=None: mock_resp)

    path = whisper_cpp.install_cli(emit=lambda *a, **kw: None)
    expected_cli = str(bin_dir / "build" / "bin" / "whisper-cli")
    assert path == expected_cli
    assert os.path.isfile(path)


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
