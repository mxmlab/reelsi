# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Бюджет видеопамяти: потолок параллельной нарезки, замок на субтитрах, запасной путь Whisper.

Три поломки, снятые с живого прогона на карте 4 ГБ (все три — «тихая»: причина
пользователю не называлась):

1. **Потолок параллельной нарезки.** Десять роликов разом — это не десять моделей
   на карте, а десять CUDA-контекстов по ~570 МиБ КАЖДЫЙ (замер: четыре процесса =
   2307 из 4096 МиБ). Заданное число роликов с картой не сверялось: подпроцесс падал
   `OutOfMemoryError`, а в интерфейсе было «код 1» без причины.
2. **Замок на субтитрах.** `POST /api/gen_subs` поднимал ASR в процессе СЕРВЕРА и
   не брал `gpu_lock` — субтитры поверх идущей нарезки это два ASR на карте разом.
3. **Запасной путь Whisper.** `large-v3` в float16 на карте 3.7 ГБ не влезает
   (~3 ГБ весов + контекст), а пользователь видел сырое
   `CUDA failed with error out of memory` — без «что поставить».

Ни один тест здесь не трогает железо и модели: VRAM подделывается числами, модель —
классом-пустышкой, замок — шпионом. Мутации, которые обязаны красить эти тесты:
убрать потолок из `cut_parallel_width` и убрать `gpu_lock` из `gen_subs`.
"""
from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path
from typing import Any, Iterator

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

from api import editor  # noqa: E402
from core import device, transcribe  # noqa: E402
from core.aicut import config as acfg  # noqa: E402
from core.umsg import ReelsiError, umsg  # noqa: E402

H = {"Host": "127.0.0.1:5001"}

OOM_TEXT = "CUDA failed with error out of memory"


# --------------------------------------------------------------------------- #
# 1. Потолок параллельной нарезки по свободной VRAM
# --------------------------------------------------------------------------- #
def test_свободной_карты_нет_потолка_нет(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без CUDA мерить нечего: `None` значит «потолка нет», а не «ноль памяти».

    Иначе на CI и на маке любой параллелизм схлопнулся бы в один ролик.
    """
    monkeypatch.setattr(device, "free_vram_mib", lambda: None)
    assert device.parallel_width_budget() is None


def test_потолок_по_фейковой_времени(monkeypatch: pytest.MonkeyPatch) -> None:
    """4096 МиБ свободно → шесть роликов: 4096 // 600. Замер: 4 × 568 = 2307 МиБ."""
    monkeypatch.setattr(device, "free_vram_mib", lambda: 4096)
    assert device.parallel_width_budget() == 4096 // device.CUT_ROLE_VRAM_MIB
    assert device.parallel_width_budget() == 6


def test_потолок_никогда_не_ноль(monkeypatch: pytest.MonkeyPatch) -> None:
    """Карта забита чужой моделью — всё равно один ролик: «0» непонятнее честного OOM."""
    monkeypatch.setattr(device, "free_vram_mib", lambda: 120)
    assert device.parallel_width_budget() == 1


def test_просили_10_получили_6_и_строку_в_лог(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Просили 10 роликов, карта тянет 6 → 6 и строка в лог с причиной и числами.

    Мутация: убрать сверку с `parallel_width_budget` — тест краснеет (вернётся 10).
    """
    monkeypatch.setattr(acfg, "step_concurrency", lambda step: 10)
    monkeypatch.setattr(acfg, "step_is_local", lambda step: False)
    monkeypatch.setattr(device, "parallel_width_budget", lambda free_mib=None: 6)
    monkeypatch.setattr(device, "vram_total_mib", lambda: 4096)

    lines: list[str] = []

    class _Log:
        def info(self, msg: str, *args: Any) -> None:
            lines.append(msg % args if args else msg)

    monkeypatch.setattr(acfg, "log", _Log())

    assert acfg.cut_parallel_width("gigaam", False, 10) == 6
    assert lines, "в лог задания не попало ни строки про потолок карты"
    text = lines[0]
    assert "4.0" in text, f"в строке нет размера карты: {text!r}"
    assert "600" in text, f"в строке нет цены ролика: {text!r}"
    assert "не больше 6" in text and "просили 10" in text, text


def test_просили_меньше_потолка_не_режем(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ниже потолка настройку не трогаем: 3 ролика при потолке 6 остаются тремя."""
    monkeypatch.setattr(acfg, "step_concurrency", lambda step: 3)
    monkeypatch.setattr(acfg, "step_is_local", lambda step: False)
    monkeypatch.setattr(device, "parallel_width_budget", lambda free_mib=None: 6)
    assert acfg.cut_parallel_width("gigaam", False, 10) == 3


def test_без_карты_как_просили(monkeypatch: pytest.MonkeyPatch) -> None:
    """Карты нет (None) → поведение прежнее: ровно min(настройка, роликов)."""
    monkeypatch.setattr(acfg, "step_concurrency", lambda step: 10)
    monkeypatch.setattr(acfg, "step_is_local", lambda step: False)
    monkeypatch.setattr(device, "parallel_width_budget", lambda free_mib=None: None)
    assert acfg.cut_parallel_width("gigaam", False, 10) == 10


def test_сообщение_доктора_и_решение_смотрят_на_одну_меру(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Оценка влезания считает контекст: 3775 МиБ свободно — float16 не влезает,
    int8 влезает; мера неизвестна — не гадаем и не подменяем модель."""
    assert device.whisper_fits("large-v3", "float16", free_mib=3775) is False
    assert device.whisper_fits("large-v3", "int8_float16", free_mib=3775) is True
    assert device.whisper_fits("medium", "float16", free_mib=3775) is True
    assert device.whisper_fits("large-v3", "float16", free_mib=None) is True


# --------------------------------------------------------------------------- #
# 2. Субтитры берут замок видеокарты
# --------------------------------------------------------------------------- #
@pytest.fixture
def lock_spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Подменяет `gpu_lock` шпионом: видно, взят ли замок вокруг распознавания.

    Подменяем в САМОМ `core.gpulock` (там же, откуда имя берут и `api.editor`, и
    нарезка): `editor` импортирует замок локально внутри роута, поэтому правка
    атрибута `editor` до него бы не дошла — и тест был бы зелёным при убранном
    `with gpu_lock(...)`, то есть не сторожил бы ничего.
    """
    import contextlib

    from core import gpulock

    events: list[str] = []

    @contextlib.contextmanager
    def spy(label: str = "", emit: Any = None) -> Iterator[None]:
        events.append(f"enter:{label}")
        try:
            yield
        finally:
            events.append("exit")

    monkeypatch.setattr(gpulock, "gpu_lock", spy)
    return events


def _gen_subs_client() -> Any:
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(editor.bp)
    app.config["TESTING"] = True
    return app.test_client()


def test_gen_subs_берёт_замок_видеокарты(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lock_spy: list[str]
) -> None:
    """Распознавание субтитров обязано идти под `gpu_lock`.

    Мутация: убрать `with gpu_lock(...)` — тест краснеет (`lock_spy` пуст и внутри
    распознавания замок не взят), а на карте окажутся два ASR разом.
    """
    import numpy as np
    import soundfile as sf
    from core import asr_backends, sync as _sync, xml2ae, xmlbuild
    from core.fileio import atomic_json_dump

    xml_file = tmp_path / "cut.xml"
    xml_file.write_text("<xmeml><sequence></sequence></xmeml>", encoding="utf-8")
    atomic_json_dump(str(tmp_path / "cut.project.json"),
                     {"version": 1, "cams": [str(tmp_path / "cam1.mp4")],
                      "keep": [[0.0, 1.0]], "offsets": [0.0]})
    (tmp_path / "cam1.mp4").write_bytes(b"\x00" * 100)

    monkeypatch.setattr(_sync, "extract_audio", lambda src, dst, **kw: dst)
    fake_librosa = types.ModuleType("librosa")
    fake_librosa.load = lambda path, sr=16000, mono=True: (np.zeros(16000), 16000)
    monkeypatch.setitem(sys.modules, "librosa", fake_librosa)
    monkeypatch.setattr(sf, "write", lambda *a, **k: None)

    inside: list[bool] = []

    def spy_transcribe(*a: Any, **kw: Any) -> list[dict[str, Any]]:
        inside.append(bool(lock_spy) and lock_spy[-1] == "enter:субтитры")
        return [{"w": "привет", "start": 0.1, "end": 0.5}]

    monkeypatch.setattr(asr_backends, "transcribe_words", spy_transcribe)
    monkeypatch.setattr(xmlbuild, "build", lambda *a, **k: {"subtitles": 1, "long_words": []})
    monkeypatch.setattr(xml2ae, "write_srt_for", lambda *a, **k: None)

    r = _gen_subs_client().post("/api/gen_subs",
                                json={"xml": str(xml_file), "subengine": "whisper"}, headers=H)
    assert r.status_code == 200
    assert r.get_json().get("ok") is True, r.get_json()
    assert inside == [True], f"распознавание шло БЕЗ замка видеокарты: {lock_spy}"
    assert lock_spy == ["enter:субтитры", "exit"], lock_spy


def test_gen_subs_отдаёт_причину_нехватки_памяти(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lock_spy: list[str]
) -> None:
    """Исчерпание памяти в субтитрах доезжает до фронта кодом, а не «кодом 1».

    Раньше `ReelsiError` из распознавания попадал в общий `except Exception` и
    превращался в `gen_subs_failed` с `RuntimeError: ...` на экране.
    """
    import numpy as np
    import soundfile as sf
    from core import asr_backends, sync as _sync, xml2ae, xmlbuild
    from core.fileio import atomic_json_dump

    xml_file = tmp_path / "cut.xml"
    xml_file.write_text("<xmeml><sequence></sequence></xmeml>", encoding="utf-8")
    atomic_json_dump(str(tmp_path / "cut.project.json"),
                     {"version": 1, "cams": [str(tmp_path / "cam1.mp4")],
                      "keep": [[0.0, 1.0]], "offsets": [0.0]})
    (tmp_path / "cam1.mp4").write_bytes(b"\x00" * 100)

    monkeypatch.setattr(_sync, "extract_audio", lambda src, dst, **kw: dst)
    fake_librosa = types.ModuleType("librosa")
    fake_librosa.load = lambda path, sr=16000, mono=True: (np.zeros(16000), 16000)
    monkeypatch.setitem(sys.modules, "librosa", fake_librosa)
    monkeypatch.setattr(sf, "write", lambda *a, **k: None)
    monkeypatch.setattr(xmlbuild, "build", lambda *a, **k: {"subtitles": 0, "long_words": []})
    monkeypatch.setattr(xml2ae, "write_srt_for", lambda *a, **k: None)

    def boom(*a: Any, **kw: Any) -> list[dict[str, Any]]:
        raise ReelsiError(umsg(
            "whisper_gpu_fallback", "Карта 3.7 ГБ: не влезла ни одна ступень Whisper",
            where="large-v3", tried="large-v3/float16", card="3.7", err=OOM_TEXT))

    monkeypatch.setattr(asr_backends, "transcribe_words", boom)

    r = _gen_subs_client().post("/api/gen_subs",
                                json={"xml": str(xml_file), "subengine": "whisper"}, headers=H)
    d = r.get_json()
    assert d.get("err") == "whisper_gpu_fallback", f"код потерялся: {d}"
    assert "не влезла" in d.get("error", ""), d
    assert lock_spy == ["enter:субтитры", "exit"], lock_spy


# --------------------------------------------------------------------------- #
# 3. Запасной путь Whisper при нехватке памяти
# --------------------------------------------------------------------------- #
class _FakeWhisper:
    """WhisperModel-пустышка: пишет, что у неё просили, и падает OOM по сценарию.

    Сценарий — множество пар «размер/точность», на которых НАДО упасть. Живой модели
    в тестах нет и быть не должно: проверяем логику выбора, а не CUDA.
    """

    calls: list[tuple[str, str]] = []
    oom_on: set[tuple[str, str]] = set()

    def __init__(self, model_size: str, device: str = "cuda", compute_type: str = "float16") -> None:
        # Именно список КЛАССА: у экземпляра своего нет, и `self.calls.append` завёл бы
        # теневой атрибут, а тест читал бы пустой class-атрибут и «ничего не проверял».
        type(self).calls.append((model_size, compute_type))
        if (model_size, compute_type) in type(self).oom_on:
            raise RuntimeError(OOM_TEXT)

    def transcribe(self, audio: Any, **kw: Any) -> tuple[list[Any], Any]:
        return [], None


@pytest.fixture
def fake_whisper(monkeypatch: pytest.MonkeyPatch) -> type[_FakeWhisper]:
    """Поддельная faster-whisper + поддельная мера VRAM.

    Патчим `transcribe.free_vram_mib`, а не `device.free_vram_mib`: имена втянуты в
    `transcribe` через `from ... import`, и правка исходного модуля поведение не
    изменила бы (тест «проходил» бы на живой карте тестовой машины).
    """
    _FakeWhisper.calls = []
    _FakeWhisper.oom_on = set()
    fake_mod = types.ModuleType("faster_whisper")
    fake_mod.WhisperModel = _FakeWhisper
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_mod)
    # `ct2_device()` решает по ЖИВОЙ карте: на машине без CUDA он перевёл бы пару
    # («cuda», «float16») в («cpu», «int8»), и проверка «повтор в int8» смотрела бы
    # не на запасной путь, а на выбор устройства. Тест обязан быть одинаковым на
    # любой машине — устройство тоже подделываем.
    monkeypatch.setattr(transcribe, "ct2_device", lambda *a, **k: ("cuda", "float16"))
    monkeypatch.setattr(transcribe, "read_mono16k", lambda path: None)
    monkeypatch.setattr(transcribe, "release_model", lambda: None)
    monkeypatch.setattr(transcribe, "_MODEL", None)
    monkeypatch.setattr(transcribe, "is_oom_error", lambda e: OOM_TEXT in str(e))
    monkeypatch.setattr(transcribe, "vram_total_mib", lambda: 4096)
    return _FakeWhisper


def _collect_emit() -> tuple[list[str], Any]:
    lines: list[str] = []

    def emit(line: str = "", /, **vars: Any) -> None:
        lines.append(line.format(**vars) if vars else line)

    return lines, emit


def test_oom_повтор_на_int8_со_строкой_причины(
    fake_whisper: type[_FakeWhisper], monkeypatch: pytest.MonkeyPatch
) -> None:
    """OOM на первой попытке → повтор в int8_float16, и в лог — строка с картой.

    Карта 4 ГБ со свободными 3775 МиБ: `large-v3` в float16 не влезает и по оценке —
    она всё равно пробуется первой (заказанную ступень мы не подменяем молча), падает,
    и запасной путь берёт ту же модель в int8.
    """
    monkeypatch.setattr(transcribe, "free_vram_mib", lambda: 3775)
    lines, emit = _collect_emit()
    _FakeWhisper.oom_on = {("large-v3", "float16")}

    words = transcribe.transcribe("cut.wav", model_size="large-v3", emit=emit)

    assert words == []
    assert fake_whisper.calls == [("large-v3", "float16"),
                                  ("large-v3", "int8_float16")], fake_whisper.calls
    note = [ln for ln in lines if "не влезла" in ln]
    assert note, f"нет строки с причиной: {lines}"
    assert "4.0 ГБ" in note[0], note[0]
    assert "large-v3" in note[0] and "int8_float16" in note[0], note[0]


def test_заказ_не_влезает_всё_равно_пробуем_его_первым(
    fake_whisper: type[_FakeWhisper], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Заказанную пользователем ступень пробуем ПЕРВОЙ, даже если карта ей мала.

    Иначе `large-v3` на 4 ГБ молча подменялся бы на `small`, и человек не знал бы,
    почему субтитры стали хуже. Дальше запасной путь идёт вниз по ступеням
    (int8 той же модели, затем размер меньше) и на первой влезающей останавливается.
    """
    monkeypatch.setattr(transcribe, "free_vram_mib", lambda: 3775)
    lines, emit = _collect_emit()
    # Заказанная ступень падает по памяти: значит, до запасных дело доходит.
    _FakeWhisper.oom_on = {("large-v3", "float16"), ("large-v3", "int8_float16")}

    transcribe.transcribe("cut.wav", model_size="large-v3", emit=emit)

    assert fake_whisper.calls == [("large-v3", "float16"),
                                  ("large-v3", "int8_float16"),
                                  ("medium", "float16")], fake_whisper.calls
    notes = [ln for ln in lines if "не влезла" in ln]
    assert len(notes) == 2, lines
    assert "int8_float16" in notes[0] and "medium/float16" in notes[1], notes


def test_падение_не_по_памяти_наверх_сразу(
    fake_whisper: type[_FakeWhisper], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Нет файла модели / битые веса — не крутим три размера, а падаем сразу."""
    monkeypatch.setattr(transcribe, "free_vram_mib", lambda: 3775)

    def bad_init(self: Any, model_size: str, device: str = "cuda",
                 compute_type: str = "float16") -> None:
        raise ValueError("нет файла модели large-v3")

    monkeypatch.setattr(_FakeWhisper, "__init__", bad_init)

    with pytest.raises(ValueError) as exc:
        transcribe.transcribe("cut.wav", model_size="large-v3")
    assert "нет файла модели" in str(exc.value)


def test_все_ступени_упали_ошибка_с_кодом_и_переводом(
    fake_whisper: type[_FakeWhisper], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Не помогло ничего → `ReelsiError` с кодом `whisper_gpu_fallback`.

    Код нужен ровно для того, чтобы английский интерфейс показал перевод, а не
    русскую строку с сырым текстом CUDA.
    """
    monkeypatch.setattr(transcribe, "free_vram_mib", lambda: 3775)
    # Карта забита под завязку: не влезает НИ ОДНА ступень, включая самую скромную.
    _FakeWhisper.oom_on = {("large-v3", "int8_float16"), ("large-v3", "float16"),
                           ("medium", "int8_float16"), ("medium", "float16"),
                           ("small", "int8_float16"), ("small", "float16")}

    with pytest.raises(ReelsiError) as exc:
        transcribe.transcribe("cut.wav", model_size="large-v3")

    err = exc.value
    assert err.code == "whisper_gpu_fallback", err.code
    assert err.vars.get("card") == "4.0", err.vars
    assert "large-v3/float16" in err.vars.get("tried", ""), err.vars
    assert OOM_TEXT in str(err), str(err)

    # Перевод обязан быть в словаре: иначе на английском останется русский текст.
    en = json.loads((Path(ROOT) / "static" / "i18n" / "en.json").read_text(encoding="utf-8"))
    assert "ERR_whisper_gpu_fallback" in en
