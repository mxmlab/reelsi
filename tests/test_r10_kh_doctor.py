# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты к заданию KH:
- doctor видит whisper.cpp независимо от torch;
- doctor отличает наличие моделей whisper.cpp от их отсутствия;
- повторный запуск main() не накапливает ошибки;
- _whisper выгружает модель из VRAM в finally даже при сбое транскрипции.
"""
import subprocess
import pytest

import doctor
from core import asr_backends


@pytest.fixture(autouse=True)
def clean_rows():
    """Сброс модульного состояния doctor между тестами."""
    doctor._rows = []
    doctor._bad = 0
    yield
    doctor._rows = []
    doctor._bad = 0


@pytest.fixture
def mock_env(monkeypatch):
    """Изоляция внешних вызовов, чтобы тесты не ходили в сеть и не зависели от окружения."""
    monkeypatch.setattr(doctor, "_which",
                        lambda n: "/usr/bin/" + n if n in ("ffmpeg", "node") else None)

    # ffmpeg subprocess.run мокаем, чтобы не зависеть от наличия бинарника
    real_run = subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and cmd and cmd[0] == "ffmpeg":
            return subprocess.CompletedProcess(cmd, 0, stdout="ffmpeg version 6.0", stderr="")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(doctor.subprocess, "run", fake_run)


def test_doctor_checks_whisper_cpp_without_torch(capsys, mock_env, monkeypatch):
    """При отсутствии torch check_compute() делает return, но check_whisper_cpp()
    всё равно вызывается в main() и строка whisper.cpp попадает в отчёт."""
    orig_mod = doctor._mod

    def fake_mod(name):
        if name == "torch":
            return False, "нет"
        return orig_mod(name)

    monkeypatch.setattr(doctor, "_mod", fake_mod)

    from core import whisper_cpp
    monkeypatch.setattr(whisper_cpp, "whisper_cli_path", lambda: "/mock/whisper-cli")
    monkeypatch.setattr(whisper_cpp, "model_path", lambda size: "/mock/" + size)

    code = doctor.main()
    assert code == 1  # без torch код 1 (FAIL)

    out = capsys.readouterr().out
    assert "whisper.cpp" in out, "Строка whisper.cpp должна присутствовать в выводе даже без torch"
    lines = [line for line in out.splitlines() if "whisper.cpp" in line]
    assert len(lines) == 1
    assert "OK" in lines[0]


def test_whisper_cpp_models_status(monkeypatch):
    """Бинарник есть, но моделей нет → WARN с предупреждением о скачивании.
    Бинарник есть и модель найдена → OK со списком моделей."""
    from core import whisper_cpp
    monkeypatch.setattr(whisper_cpp, "whisper_cli_path", lambda: "/mock/whisper-cli")

    # Вариант 1: моделей нет
    monkeypatch.setattr(whisper_cpp, "model_path", lambda size: None)
    doctor._rows = []
    doctor.check_whisper_cpp()
    status, what, detail = doctor._rows[-1]
    assert status == doctor.WARN
    assert what == "whisper.cpp"
    assert detail == doctor.t("моделей нет — скачаются при первом выборе движка (large-v3 ≈ 3 ГБ)")

    # Вариант 2: найдена модель medium
    monkeypatch.setattr(whisper_cpp, "model_path",
                        lambda size: "/mock/ggml-medium.bin" if size == "medium" else None)
    doctor._rows = []
    doctor.check_whisper_cpp()
    status, what, detail = doctor._rows[-1]
    assert status == doctor.OK
    assert what == "whisper.cpp"
    assert "medium" in detail
    assert detail == doctor.t("бинарник найден, модели: {models}", models="medium")


def test_whisper_cpp_missing_binary(monkeypatch):
    """Бинарника нет → WARN без перечисления моделей."""
    from core import whisper_cpp
    monkeypatch.setattr(whisper_cpp, "whisper_cli_path", lambda: None)
    doctor._rows = []
    doctor.check_whisper_cpp()
    status, what, detail = doctor._rows[-1]
    assert status == doctor.WARN
    assert what == "whisper.cpp"
    expected = doctor.t("бинарника нет — на Mac/AMD быстрой транскрипции не будет. "
                        "Поставить по запросу: python -m core.whisper_cpp install (или brew/scoop)")
    assert detail == expected


def test_main_resets_bad_and_rows(capsys, mock_env, monkeypatch):
    """Два вызова main() подряд с ошибкой дают один и тот же результат (код 1
    и то же число проблем), а не накапливают _bad."""
    orig_mod = doctor._mod

    def fake_mod(name):
        if name == "flask":
            return False, "нет"
        return orig_mod(name)

    monkeypatch.setattr(doctor, "_mod", fake_mod)

    code1 = doctor.main()
    bad1 = doctor._bad
    out1 = capsys.readouterr().out

    code2 = doctor.main()
    bad2 = doctor._bad
    out2 = capsys.readouterr().out

    assert code1 == 1
    assert code2 == 1
    assert bad1 >= 1
    assert bad1 == bad2

    summary1 = [line for line in out1.splitlines() if any(k in line for k in ("НЕ ГОТОВО", "NOT READY"))]
    summary2 = [line for line in out2.splitlines() if any(k in line for k in ("НЕ ГОТОВО", "NOT READY"))]
    assert summary1 and summary2
    assert summary1 == summary2, f"Выводы различаются: {summary1} vs {summary2}"


def test_main_resets_bad_and_rows_without_torch(capsys, mock_env, monkeypatch):
    """Идемпотентность doctor.main() сохраняется и на машине без torch (несколько обязательных проблем)."""
    orig_mod = doctor._mod

    def fake_mod(name):
        if name in ("flask", "torch"):
            return False, "нет"
        return orig_mod(name)

    monkeypatch.setattr(doctor, "_mod", fake_mod)

    code1 = doctor.main()
    bad1 = doctor._bad
    out1 = capsys.readouterr().out

    code2 = doctor.main()
    bad2 = doctor._bad
    out2 = capsys.readouterr().out

    assert code1 == 1
    assert code2 == 1
    assert bad1 >= 2
    assert bad1 == bad2

    summary1 = [line for line in out1.splitlines() if any(k in line for k in ("НЕ ГОТОВО", "NOT READY"))]
    summary2 = [line for line in out2.splitlines() if any(k in line for k in ("НЕ ГОТОВО", "NOT READY"))]
    assert summary1 and summary2
    assert summary1 == summary2, f"Выводы различаются: {summary1} vs {summary2}"


def test_whisper_releases_model_on_exception(monkeypatch):
    """transcribe.transcribe падает → release_model всё равно вызывается в finally,
    а исключение пробрасывается наружу."""
    from core import aicut, transcribe

    monkeypatch.setattr(aicut, "unload_ours", lambda: None)
    monkeypatch.setattr(aicut, "warn_foreign_models", lambda: None)

    def boom_transcribe(*args, **kwargs):
        raise RuntimeError("simulated transcription failure")

    released = []
    monkeypatch.setattr(transcribe, "transcribe", boom_transcribe)
    monkeypatch.setattr(transcribe, "release_model", lambda: released.append(True))

    with pytest.raises(RuntimeError, match="simulated transcription failure"):
        asr_backends._whisper("test.wav")

    assert len(released) == 1, "release_model должен быть вызван в finally"


def test_whisper_translations():
    """Проверка переводов новых строк doctor."""
    from core.app_meta import t
    import core.app_meta as app_meta

    # Подменяем ui_lang на "en" для проверки перевода
    old_lang = app_meta.ui_lang
    try:
        app_meta.ui_lang = lambda: "en"
        tr_warn = t("моделей нет — скачаются при первом выборе движка (large-v3 ≈ 3 ГБ)")
        assert tr_warn == "no models — will download on first engine selection (large-v3 ≈ 3 GB)"

        tr_ok = t("бинарник найден, модели: {models}", models="medium")
        assert tr_ok == "binary found, models: medium"
    finally:
        app_meta.ui_lang = old_lang
