# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сторожа свежей установки: откуда берётся звук субтитров.

Прогон на чистом Linux по README падал на шаге субтитров ДВУМЯ способами сразу, и оба
видны только на свежих версиях, которые ставит pip:

1. `librosa.load` читал ИСХОДНОЕ видео камеры. librosa 1.0 убрала запасной декодер
   audioread, а libsndfile видео не читает вовсе -> LibsndfileError на любом mp4/mov.
   У владельца стояли librosa 0.11 + audioread, поэтому дефект не был виден.
   Правильный путь — ffmpeg (`core.sync.extract_audio`) в wav, дальше `librosa.load`.
2. `model.transcribe(путь)` — faster-whisper декодировал путь своим PyAV, а PyAV 19
   убрал аргумент `metadata_errors`, который faster-whisper 1.2 ещё передаёт:
   `TypeError: open() got an unexpected keyword argument 'metadata_errors'`. С массивом
   float32 PyAV не участвует.

Плюс два храповика: место чтения звука по не-WAV пути так просто не появится
(`test_аудио_читается_только_по_wav`), и верхние границы версий в requirements.txt не
пропадут молча.

Опциональные пакеты (`faster_whisper`, `librosa`) в CI Linux отсутствуют, поэтому тесты
подменяют их через `sys.modules` и не требуют установки.

Запуск: python -m pytest tests/test_fresh_install_audio.py -q
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("REELSI_NO_BROWSER", "1")

import numpy as np  # noqa: E402

from core.fileio import atomic_json_dump  # noqa: E402

H = {"Host": "127.0.0.1:5001"}


# --------------------------------------------------------------------------- #
# Заглушки вместо тяжёлых и необязательных пакетов
# --------------------------------------------------------------------------- #
class _Word:
    def __init__(self, word: str, start: float, end: float, probability: float = 0.9) -> None:
        self.word = word
        self.start = start
        self.end = end
        self.probability = probability


class _Segment:
    """Минимальный сегмент faster-whisper: то, что читают transcribe/selfcheck."""

    def __init__(self) -> None:
        self.text = "привет мир"
        self.no_speech_prob = 0.0
        self.avg_logprob = -0.1
        self.words = [_Word(" привет", 0.0, 0.4), _Word(" мир", 0.4, 0.9)]


class _SpyModel:
    """Модель-шпион: записывает, ЧЕМ её позвали."""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    def transcribe(self, audio: Any, **kw: Any) -> tuple[list[_Segment], Any]:
        self.calls.append(audio)
        return [_Segment()], types.SimpleNamespace(language="ru")


def _stub_soundfile_read(monkeypatch: pytest.MonkeyPatch, samples: int = 16000) -> None:
    """Подменить только чтение: на CI Linux `soundfile` есть (ядро), но wav-файла нет."""
    import soundfile as sf

    monkeypatch.setattr(sf, "read",
                        lambda *a, **kw: (np.zeros((samples, 1), dtype="float32"), 16000))


# --------------------------------------------------------------------------- #
# (а) субтитры на ВИДЕО: до `librosa.load` звук доходит уже wav-ом
# --------------------------------------------------------------------------- #
# Пары кодеков на пробу: набор зависит от сборки ffmpeg (в CI Linux нет проприетарных
# кодеков). Проверяем не кодек, а то, что звук идёт через ffmpeg.
_VIDEO_CODECS = (("libx264", "aac"), ("mpeg4", "aac"), ("mpeg4", "pcm_s16le"))


def _make_test_mp4(path: Path) -> bool:
    """Синтетический mp4 со звуком (testsrc + sine). False — ни один набор не дался."""
    for vcodec, acodec in _VIDEO_CODECS:
        cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=64x64:rate=10:duration=2",
               "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
               "-c:v", vcodec, "-c:a", acodec, "-shortest", str(path), "-loglevel", "error"]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            return False
        if r.returncode == 0 and path.is_file():
            return True
    return False


def test_gen_subs_читает_видео_через_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """На видеовходе `librosa.load` получает wav-путь, а не сам mp4.

    Мутация: вернуть `librosa.load(cams[0], ...)` — тест краснеет (путь оканчивается
    на `.mp4`).
    """
    if not _make_test_mp4(tmp_path / "cam1.mp4"):
        pytest.skip("ffmpeg недоступен или не собрал тестовый mp4 (нет подходящих кодеков)")

    from api import editor
    from core import asr_backends, xml2ae, xmlbuild

    xml_file = tmp_path / "cut.xml"
    xml_file.write_text("<xmeml><sequence></sequence></xmeml>", encoding="utf-8")
    atomic_json_dump(str(tmp_path / "cut.project.json"),
                     {"version": 1, "cams": [str(tmp_path / "cam1.mp4")],
                      "keep": [[0.0, 1.0]], "offsets": [0.0]})

    # librosa — целиком подставной модуль (как в test_routes_behavior): важен не звук, а
    # ПУТЬ, которым его позвали. Настоящий librosa на CI Linux не поставлен, а здесь он
    # тянет numba и лезет писать свой кэш мимо рабочей папки.
    seen: list[str] = []

    def spy_load(path: Any, *a: Any, **kw: Any) -> tuple[Any, int]:
        seen.append(str(path))
        # wav обязан быть настоящим и непустым: так тест видит, что ffmpeg реально
        # отработал, а не что путь просто переименовали.
        assert os.path.getsize(path) > 1024, f"ffmpeg не наполнил {path}"
        return np.zeros(32000, dtype="float32"), 16000

    fake_librosa = types.ModuleType("librosa")
    fake_librosa.load = spy_load
    monkeypatch.setitem(sys.modules, "librosa", fake_librosa)
    # Модель ASR — фейк: тест про путь звука, а не про Whisper (его в CI нет).
    monkeypatch.setattr(asr_backends, "transcribe_words",
                        lambda *a, **kw: [{"w": "привет", "start": 0.1, "end": 0.5}])
    monkeypatch.setattr(xmlbuild, "build", lambda *a, **kw: {"subtitles": 1, "long_words": []})
    monkeypatch.setattr(xml2ae, "write_srt_for", lambda *a, **kw: None)

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(editor.bp)
    app.config["TESTING"] = True
    client = app.test_client()

    r = client.post("/api/gen_subs", json={"xml": str(xml_file), "subengine": "whisper"}, headers=H)
    assert r.status_code == 200

    assert seen, f"librosa.load не вызван вовсе (ответ: {r.get_json()})"
    assert len(seen) == 1, f"librosa.load позвали {len(seen)} раз: {seen}"
    assert seen[0].lower().endswith(".wav"), (
        f"librosa.load получил не-wav путь {seen[0]!r}: на свежей установке (librosa 1.0 "
        "без audioread) чтение видео падает LibsndfileError"
    )


# --------------------------------------------------------------------------- #
# (б) core.transcribe отдаёт модели МАССИВ
# --------------------------------------------------------------------------- #
def test_transcribe_отдаёт_модели_массив(monkeypatch: pytest.MonkeyPatch) -> None:
    """`model.transcribe` получает np.ndarray float32, а не строку-путь.

    Мутация: вернуть в вызов путь — тест краснеет.
    """
    from core import transcribe

    _stub_soundfile_read(monkeypatch, samples=16000)
    spy = _SpyModel()

    words = transcribe.transcribe("cut.wav", model=spy)

    assert spy.calls, "модель не позвали вовсе"
    audio = spy.calls[0]
    assert isinstance(audio, np.ndarray), (
        f"в модель ушёл {type(audio).__name__}, а не массив: с путём faster-whisper "
        "декодирует его PyAV'ом и падает TypeError на PyAV 19"
    )
    assert audio.dtype == np.float32
    assert audio.ndim == 1
    assert len(audio) == 16000
    assert [w["w"] for w in words] == ["привет", "мир"]


# --------------------------------------------------------------------------- #
# (в) то же в самопроверке стыков
# --------------------------------------------------------------------------- #
def test_selfcheck_отдаёт_модели_массив(monkeypatch: pytest.MonkeyPatch) -> None:
    """`selfcheck._transcribe_words` тоже передаёт массив: тот же PyAV-путь."""
    from core import selfcheck

    _stub_soundfile_read(monkeypatch, samples=8000)
    spy = _SpyModel()
    monkeypatch.setattr("core.transcribe.get_model", lambda *a, **kw: spy)

    words = selfcheck._transcribe_words("concat.wav")

    assert spy.calls, "модель не позвали вовсе"
    audio = spy.calls[0]
    assert isinstance(audio, np.ndarray), (
        f"в модель ушёл {type(audio).__name__}, а не массив (PyAV 19 + faster-whisper 1.2)"
    )
    assert audio.dtype == np.float32 and audio.ndim == 1
    assert words and words[0]["w"] == "привет" and "prob" in words[0]


# --------------------------------------------------------------------------- #
# (г) храповик: звук читается только по wav-пути
# --------------------------------------------------------------------------- #
READERS = ("librosa.load", "sf.read", "soundfile.read", "sf.info", "soundfile.info")

# Разрешённые места: путь не доказанно wav, но чтение безопасно по устройству места.
# Ключ — `<файл>:<функция>:<что зовут>`, имя функции берётся через `ast`. Номер строки
# ключом быть не может: он протухает от ЛЮБОЙ вставки выше по файлу (роут музыки сдвинул
# `librosa.load` в api/files.py), и храповик краснел «новым местом» и «протухшей записью»
# одновременно — из-за правки, к чтению звука отношения не имеющей. Новое место
# добавляется СЮДА ОСОЗНАННО: храповик разрешает, а не запрещает навсегда.
ALLOWED: dict[str, str] = {
    "api/files.py:api_waveform:librosa.load": (
        "путь уже проверен по ALLOWED_WAVE_EXTS и realpath — не-wav расширение до "
        "`librosa.load` не доходит (см. api/files.py, /api/waveform)"
    ),
    "core/omni_asr.py:main:sf.read": (
        "CLI-точка входа: wav задаёт пользователь аргументом "
        "(`python -m core.omni_asr <файл.wav>`), иначе это его явная ошибка"
    ),
}


def _dotted(node: ast.AST) -> str:
    """`librosa.load` из `Attribute(Name('librosa'), 'load')`."""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    return ""


def _is_wav_expr(node: ast.AST) -> bool:
    """В подвыражении пути есть литерал `.wav` — путь собирается как wav."""
    return any(isinstance(n, ast.Constant) and isinstance(n.value, str) and ".wav" in n.value.lower()
               for n in ast.walk(node))


def _wav_names(tree: ast.AST) -> set[str]:
    """Имена, которым присвоен wav-путь (`fd, wav = mkstemp(suffix=".wav")`,
    `wavs = [os.path.join(work, f"a{k}.wav") …]`), и параметры со словом `wav` в
    названии: контракт таких функций — «сюда приходит wav» (`wav_path`), и это ровно то,
    что видно в коде и в тестах."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_wav_expr(node.value):
            for tgt in node.targets:
                names.update(n.id for n in ast.walk(tgt) if isinstance(n, ast.Name))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            for a in list(args.args) + list(args.posonlyargs) + list(args.kwonlyargs):
                if "wav" in a.arg.lower():
                    names.add(a.arg)
    return names


def _trees() -> list[tuple[str, ast.AST]]:
    """Разобранное дерево каждого .py в api/ и core/ (файл, дерево)."""
    out: list[tuple[str, ast.AST]] = []
    for d in ("api", "core"):
        for path in sorted((ROOT / d).rglob("*.py")):
            try:
                out.append((path.relative_to(ROOT).as_posix(),
                            ast.parse(path.read_text(encoding="utf-8"))))
            except (OSError, SyntaxError):
                continue
    return out


def _wav_only_params(trees: list[tuple[str, ast.AST]]) -> set[tuple[str, str]]:
    """Параметры локальных функций, которые ВЕЗДЕ зовут только с wav-путём.

    Нужно из-за хелперов вроде `core/omni_cut._wav_duration(path)`: имя параметра
    ничего не говорит, но единственный вызов в проекте передаёт доказанно wav.
    Возвращает пары (имя функции, имя параметра): имя функции — ключ, а не гарантия,
    но одной этой эвристики хватает, и она не «протекает» в одноимённые функции
    других модулей (иначе `path` из `api/files.py` доказался бы чужой функцией).
    """
    params: dict[str, list[str]] = {}
    for _rel, tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                params[node.name] = [a.arg for a in
                                     list(node.args.posonlyargs) + list(node.args.args)]
    proven: dict[str, set[int]] = {name: set() for name in params}
    unproven: dict[str, set[int]] = {name: set() for name in params}
    for _rel, tree in trees:
        names = _wav_names(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id not in params:
                continue
            for i, arg in enumerate(node.args):
                (proven if _proven_wav(arg, names) else unproven)[node.func.id].add(i)
    # Индекс считается wav-параметром, только если он доказан и НИ РАЗУ не пришёл
    # не-wav-ом: иначе хелпер умеет и то, и другое, и доказывать нечего.
    out: set[tuple[str, str]] = set()
    for name, order in params.items():
        for i in proven[name] - unproven[name]:
            if i < len(order):
                out.add((name, order[i]))
    return out


def _proven_wav(arg: ast.AST, names: set[str]) -> bool:
    """Аргумент — wav по построению: литерал `.wav`, имя из `_wav_names` или `wavs[0]`."""
    if _is_wav_expr(arg):
        return True
    base = arg.value if isinstance(arg, ast.Subscript) else arg
    if isinstance(base, ast.Name) and base.id in names:
        return True
    # `wavs[k]` с переменным индексом ничего не доказывает: доказывает только имя
    # списка, элементы которого собраны как wav.
    if isinstance(arg, ast.Subscript):
        return _is_wav_expr(arg.slice)
    return False


class _ReadSites(ast.NodeVisitor):
    """Обход дерева с контекстом: имя функции, внутри которой стоит вызов.

    Ключ места — имя функции, а не номер строки: строка протухает от ЛЮБОЙ вставки
    выше по файлу, из-за чего храповик краснел на правке, к чтению звука отношения
    не имеющей.
    """

    def __init__(self, rel: str, names: set[str]) -> None:
        self.rel = rel
        self.names = names
        self.funcs: list[str] = []
        self.sites: list[tuple[str, str, str]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._enter(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._enter(node)

    def _enter(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.funcs.append(node.name)
        self.generic_visit(node)
        self.funcs.pop()

    def visit_Call(self, node: ast.Call) -> None:
        reader = _dotted(node.func)
        if reader in READERS and node.args:
            self.sites.append((self.rel, self.funcs[-1] if self.funcs else "",
                               "wav" if _proven_wav(node.args[0], self.names) else reader))
        self.generic_visit(node)


def _audio_read_sites() -> list[tuple[str, str, str]]:
    """Все места в `api/` и `core/`, где звук читается/проверяется ПО ПУТИ:
    (файл, имя функции, чем зовут либо `wav`, если путь доказанно wav)."""
    trees = _trees()
    wav_only = _wav_only_params(trees)
    sites: list[tuple[str, str, str]] = []
    for rel, tree in trees:
        names = _wav_names(tree)
        # Параметры, доказанные как wav-параметры, добавляем и в имена самого файла:
        # функция, объявленная здесь, читает свой параметр тем же вызовом.
        names |= {a.arg for n in ast.walk(tree)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                  for a in list(n.args.args) + list(n.args.posonlyargs)
                  if (n.name, a.arg) in wav_only}
        visitor = _ReadSites(rel, names)
        visitor.visit(tree)
        sites.extend(visitor.sites)
    return sites


def test_аудио_читается_только_по_wav() -> None:
    """Храповик: `librosa.load`/`sf.read` получают wav-путь либо стоят в списке ALLOWED.

    Причина дефекта именно здесь: чтение звука ИЗ ВИДЕО выглядело «естественным»
    вызовом `librosa.load(cams[0])` и падало на свежей установке. Место в списке —
    разрешение с причиной, а не забывчивость: ключ виден на ревью.
    """
    offenders = [f"{rel}:{func}:{reader}"
                 for rel, func, reader in _audio_read_sites()
                 if reader != "wav" and f"{rel}:{func}:{reader}" not in ALLOWED]
    assert not offenders, (
        "звук читается по пути без доказательства, что это wav (на свежей установке "
        "librosa 1.0 без audioread такое чтение падает на любом видео): "
        + ", ".join(offenders)
        + ". Либо проведи путь через core.sync.extract_audio (ffmpeg), либо добавь место "
          "в ALLOWED с причиной."
    )


def test_allow_list_не_протух() -> None:
    """Каждый ключ ALLOWED указывает на живое место чтения — иначе список чистится."""
    live = {f"{rel}:{func}:{reader}" for rel, func, reader in _audio_read_sites()
            if reader != "wav"}
    stale = [k for k in ALLOWED if k not in live]
    assert not stale, (
        "в ALLOWED есть ключи без живого места чтения (место переехало или правку "
        "откатили) — приведи список в порядок, чтобы он не разрешал неизвестно что: "
        + ", ".join(stale)
    )


# --------------------------------------------------------------------------- #
# (д) требования с верхней границей
# --------------------------------------------------------------------------- #
# Пакет -> полное требование: резолвер pip должен остаться в этом коридоре.
BOUNDED = {
    "librosa": ">=0.10,<1.0",
    "faster-whisper": ">=1.0,<2",
    "transformers": ">=5.10.1,<6",
    "numpy": ">=1.26,<3",
    "soundfile": ">=0.12,<1",
    "av": ">=12,<19",
}


def _requirement_line(name: str) -> str:
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    lines = [ln.strip() for ln in text.splitlines()
             if re.match(rf"^{re.escape(name)}\s*[<>=!~]", ln.strip())]
    assert lines, f"в requirements.txt нет строки требования для {name!r}"
    assert len(lines) == 1, f"{name!r} объявлен {len(lines)} раза: {lines}"
    return lines[0]


def test_requirements_имеют_верхнюю_границу() -> None:
    """У всех шести пакетов есть верхняя граница: без неё pip ставит ломающий мажор.

    PyAV (`av`) объявлен явно, хотя и приходит транзитивно: faster-whisper импортирует
    его всегда, а 19.0 несовместим с faster-whisper 1.2 (`metadata_errors`).
    """
    for name, bound in BOUNDED.items():
        line = _requirement_line(name)
        assert line.replace(" ", "") == f"{name}{bound}", (
            f"{name!r}: ожидалось {name}{bound}, а в файле {line!r}. Верхняя граница "
            "обязательна: свежий мажор ломает шаг субтитров на чистой установке"
        )
