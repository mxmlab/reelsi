# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Выбор аппаратного кодировщика черновика: NVENC / VideoToolbox / AMF / QSV.

Главная ловушка, ради которой эти тесты и написаны: **наличие кодека в
`ffmpeg -encoders` ничего не значит.** Полные сборки содержат и h264_amf, и
h264_qsv на машине, где нет ни AMD, ни Intel-графики — проверено на рабочей
NVIDIA-машине. Единственный честный признак — микро-энкод.

Ветки для Mac и AMD на этом железе не выполняются никогда, поэтому проверяются
подделкой платформы и пробы.

Запуск:  python -m pytest reelsi/tests -q
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import draftrender as d  # noqa: E402


@pytest.fixture(autouse=True)
def reset_cache():
    """Кэш живёт на процесс — между тестами обнуляем, иначе они видят чужой выбор."""
    d._HW_CACHE = "unset"
    yield
    d._HW_CACHE = "unset"


def _fake(monkeypatch, system, working=()):
    """Платформа + набор кодеков, которые «проходят пробу»."""
    monkeypatch.setattr(d.platform, "system", lambda: system)
    monkeypatch.setattr(d, "_probe_encoder", lambda name: name in working)


def test_mac_picks_videotoolbox(monkeypatch):
    """На маке берём медиадвижок Apple, а не пытаемся в NVENC."""
    _fake(monkeypatch, "Darwin", working=("h264_videotoolbox", "h264_nvenc"))
    assert d.hw_encoder() == "h264_videotoolbox"


def test_windows_prefers_nvenc(monkeypatch):
    """Когда работают несколько — берём первый по списку приоритета."""
    _fake(monkeypatch, "Windows", working=("h264_nvenc", "h264_amf", "h264_qsv"))
    assert d.hw_encoder() == "h264_nvenc"


def test_amd_machine_falls_through_to_amf(monkeypatch):
    """NVENC в сборке есть, но пробу не проходит (карты нет) — уходим на AMF.
    Это и есть тот случай, ради которого проба существует."""
    _fake(monkeypatch, "Windows", working=("h264_amf", "h264_qsv"))
    assert d.hw_encoder() == "h264_amf"


def test_no_hardware_returns_none(monkeypatch):
    """Ничего не работает — None, и вызывающий уходит на libx264."""
    _fake(monkeypatch, "Linux", working=())
    assert d.hw_encoder() is None


def test_unknown_platform_still_tries_something(monkeypatch):
    """FreeBSD и прочая экзотика: не падаем, пробуем самый распространённый."""
    _fake(monkeypatch, "SunOS", working=("h264_nvenc",))
    assert d.hw_encoder() == "h264_nvenc"


def test_probe_runs_once_and_is_cached(monkeypatch):
    """Проба — это запуск ffmpeg. Гонять её на каждый черновик нельзя."""
    calls = []
    monkeypatch.setattr(d.platform, "system", lambda: "Windows")
    monkeypatch.setattr(d, "_probe_encoder", lambda n: calls.append(n) or (n == "h264_nvenc"))
    assert d.hw_encoder() == "h264_nvenc"
    assert d.hw_encoder() == "h264_nvenc"
    assert calls == ["h264_nvenc"], "проба запускалась повторно"


def test_refresh_forces_reprobe(monkeypatch):
    """...но по явной просьбе перепроверяем: карту могли занять или освободить."""
    _fake(monkeypatch, "Windows", working=("h264_nvenc",))
    assert d.hw_encoder() == "h264_nvenc"
    _fake(monkeypatch, "Windows", working=())
    assert d.hw_encoder(refresh=True) is None


# --------------------------------------------------------------------------- #
# Аргументы кодека
# --------------------------------------------------------------------------- #
def test_nvenc_args_unchanged():
    """NVENC-путь проверен годом работы — его параметры не должны поехать."""
    assert d._codec_args("h264_nvenc", 28, "4M") == [
        "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "28"]


def test_non_nvidia_uses_bitrate_not_quantizer():
    """У amf/qsv/videotoolbox флаги качества разъезжаются от версии ffmpeg к версии,
    а -b:v понимают все. Для черновика этого достаточно."""
    for name in ("h264_videotoolbox", "h264_amf", "h264_qsv"):
        args = d._codec_args(name, 28, "4M")
        assert args[:2] == ["-c:v", name]
        assert "-b:v" in args and "4M" in args
        assert "-cq" not in args, f"{name}: -cq не его флаг"


def test_unknown_codec_gets_safe_defaults():
    """Кодек, о котором мы не знаем, не должен ронять рендер отсутствием аргументов."""
    args = d._codec_args("h264_something_new", 28, "4M")
    assert args == ["-c:v", "h264_something_new", "-b:v", "4M"]


def test_every_candidate_has_args():
    """Каждый кандидат из таблицы платформ обязан иметь свои аргументы — иначе он
    молча уедет на битрейт-фолбэк, и никто не заметит."""
    for names in d._CANDIDATES.values():
        for name in names:
            assert name in d._ENC_ARGS, f"{name} в кандидатах, но без аргументов"


# --------------------------------------------------------------------------- #
# повёрнутый кадр мимо NVDEC (баг: прокси искажён и лежит на боку)
# --------------------------------------------------------------------------- #
def _tries(rot, hw="h264_nvenc"):
    from core import draftrender as d
    hwc = d._codec_args(hw, 30, "3M") if hw else None
    return d._decode_tries("src.mp4", "VF_GPU", "VF_CPU", hw, rot, hwc, ["-c:v", "libx264"])


def test_rotated_source_never_goes_through_nvdec():
    """Повёрнутый исходник декодируем ТОЛЬКО на CPU.

    С `-hwaccel_output_format cuda` автоповорот ffmpeg не применяется: scale_cuda
    получает ещё не развёрнутый кадр (3840x2160), жмёт ландшафт в портрет, а матрица
    поворота 90° уезжает в выход — прокси выходит искажённым и лежащим на боку.
    Заход при этом УСПЕШЕН (на 4:2:0 NVDEC работает), поэтому в кэш попадал битый
    файл, а на CPU-заходе тот же исходник давал верный кадр: результат зависел от
    того, какой заход сработал. Реальные файлы — C1387-008.MP4, C1385-004.MP4.
    """
    for inp, vf, _codec in _tries(rot=True):
        assert "-hwaccel" not in inp, "повёрнутый кадр снова уехал в NVDEC"
        assert vf == "VF_CPU", "к повёрнутому кадру применяется scale_cuda"


def test_unrotated_source_still_prefers_nvdec():
    """Обычный кадр как и раньше идёт первым заходом через карту — иначе теряем скорость."""
    first_inp, first_vf, _ = _tries(rot=False)[0]
    assert "-hwaccel" in first_inp and first_vf == "VF_GPU"
    # запасные заходы на месте: 4:2:2 10 бит NVDEC не умеет, он падает на CPU
    assert len(_tries(rot=False)) == 3


def test_rotated_source_keeps_hardware_encoder():
    """CPU только ДЕКОД — энкод остаётся на NVENC, иначе теряем весь выигрыш зря."""
    (_inp, _vf, codec) = _tries(rot=True)[0]
    assert "h264_nvenc" in codec


def test_no_hardware_encoder_means_one_plain_try():
    """Без аппаратного энкодера — один честный заход на libx264."""
    assert len(_tries(rot=False, hw=None)) == 1
    assert _tries(rot=True, hw=None) == _tries(rot=False, hw=None)


# --------------------------------------------------------------------------- #
# превью-прокси: короткий GOP — иначе браузерный seek гоняет декодер на 10 секунд
# --------------------------------------------------------------------------- #
def _preview_cmds(monkeypatch, tmp_path, fps="25/1", hw="h264_nvenc"):
    """Подменяем ffprobe (fps) и ffmpeg (запоминаем команды) в build_preview_proxy."""
    cmds = []

    class _R:
        returncode = 0
        stdout = ""
        stderr = ""

    class _Popen:                       # _run_ff ждёт процесс через Popen+communicate
        def __init__(self, cmd, **k):
            self._cmd = cmd or []
            self.returncode = 0
            self.stdout = ""
            self.stderr = ""
            if self._cmd and self._cmd[0].endswith("ffprobe"):
                self.stdout = fps
                return
            cmds.append(self._cmd)      # команду ffmpeg запоминаем для проверок
            for x in reversed(self._cmd):   # tmp = <dst>.part.mp4 — «ffmpeg» доделал
                if isinstance(x, str) and x.endswith(".part.mp4"):
                    with open(x, "wb") as f:
                        f.write(b"x")
                    break

        def communicate(self, timeout=None):
            return self.stdout, self.stderr

        def kill(self):
            pass

    def fake_run(*a, **k):
        args = a[-1] if a and isinstance(a[-1], list) else (a[1] if len(a) > 1 else None)
        if not args:
            args = k.get("args") or (a[0] if a else None)
        if args and args[0].endswith("ffprobe"):
            return _R() if fps == "" else type("_", (), {"stdout": fps, "returncode": 0,
                                                          "stderr": ""})()
        cmds.append(args)
        return _R()

    class _CP:
        def __init__(self, args, rc, out="", err=""):
            self.returncode = rc
            self.stdout = out
            self.stderr = err

    class _sp:
        PIPE = -1
        Popen = _Popen
        CompletedProcess = _CP
        TimeoutExpired = TimeoutError
        run = staticmethod(fake_run)

    monkeypatch.setattr(d, "subprocess", _sp)
    monkeypatch.setattr(d, "hw_encoder", lambda: hw)
    monkeypatch.setattr(d, "_display_dims", lambda s: (2160, 3840, False))
    src = str(tmp_path / "cam.mp4")
    open(src, "w").close()
    dst = str(tmp_path / "out.mp4")
    assert d.build_preview_proxy(src, dst, height=720, emit=lambda *a: None) == dst
    return cmds


def test_preview_proxy_uses_short_gop(monkeypatch, tmp_path):
    """Ключевой кадр раз в ~1 секунду, а не раз в 250 фреймов (10 сек при 25 fps).

    Дефолтный GOP NVENC и X.264 — 250 кадров. При 25 fps это ключевой кадр раз в
    10 секунд: каждый seek в браузере (стык, перемотка в редакторе) заставлял декодер
    прогонять до 10 секунд видео с последнего ключевого кадра — отсюда тормоза даже
    на готовых прокси. Короткий GOP лечит seek и стыки."""
    cmds = _preview_cmds(monkeypatch, tmp_path, fps="25/1")
    got = []
    for c in cmds:
        if "-c:v" in c:
            i = c.index("-c:v")
            got.append((c[i + 1], "-g" in c and int(c[c.index("-g") + 1])))
    assert any(codec == "h264_nvenc" and gop == 25 for codec, gop in got), \
        f"нет захода с GOP=25: {got}"


def test_preview_proxy_gop_scales_with_fps(monkeypatch, tmp_path):
    """60 fps — GOP 60 (тоже ~1 сек), а не захардкоженная 25."""
    cmds = _preview_cmds(monkeypatch, tmp_path, fps="60/1")
    assert any("-g" in c and int(c[c.index("-g") + 1]) == 60 for c in cmds), \
        "GOP не пересчитан под 60 fps"


def test_preview_proxy_gop_has_floor(monkeypatch, tmp_path):
    """Форматт с неизвестным fps не должен дать GOP короче 12 кадров."""
    cmds = _preview_cmds(monkeypatch, tmp_path, fps="")
    assert all("-g" not in c or int(c[c.index("-g") + 1]) >= 12 for c in cmds), \
        "GOP упал ниже пола в 12 кадров"
