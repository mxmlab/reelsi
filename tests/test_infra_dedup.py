# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание NB: одна инфраструктура вместо копий — запись, длительность, таймауты.

Копии одного и того же приёма разъезжаются молча, и это не теория: «tmp + os.replace»
жил в девяти файлах (в одном комментарии даже утверждалось, что атомарной записи
ТЕКСТА в `core/fileio.py` нет — она там есть с ), длительность ffprobe
считалась пятью кусками кода с разным поведением при ошибке (0.0 против исключения),
а `subprocess.run` без таймаута мог зависнуть навсегда вместе с потоком джоба.

Три сторожа класса по исходникам `core/` и `api/`:
  * `os.replace(` — только `core/fileio.py`;
  * `format=duration` — только `core/media.py`;
  * `subprocess.run(`/`check_output(` — только с `timeout=`.
Исключения перечислены ПОИМЁННО и с причиной у каждого, а список проверяется на
свежесть: место починили — строку из списка убери (иначе список тихо врёт).

Плюс поведение общей пробы `core.media.probe_duration`: нет файла, битый файл,
зависший/отсутствующий ffprobe — `None` без исключения наружу; на настоящем медиа —
число; повторный вопрос берётся из кэша. И прежний контракт вызывающих, которые
ждали число: 0.0 при «не прочли» (в него упирается и прогресс сборки, и кадр для
vision), а не None.

Запуск:  python -m pytest tests/test_infra_dedup.py -q
"""
import ast
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

CORE_DIRS = ("core", "api")


def _py_files():
    """Все .py ядра — то, где копии инфраструктуры и разъезжались."""
    out = []
    for base in CORE_DIRS:
        out += sorted((ROOT / base).rglob("*.py"))
    return out


def _rel(path):
    return path.relative_to(ROOT).as_posix()


def _lines(path):
    return path.read_text(encoding="utf-8").splitlines()


# --------------------------------------------------------------------------- #
# 1. Атомарная запись — только core/fileio.py
# --------------------------------------------------------------------------- #
# Места, где `os.replace` остаётся НАРОЧНО: общей атомарной записи тут применить
# нечего — файла нет в питоновском процессе (его пишет внешняя программа или поток
# в сеть), и подменить остаётся только готовый результат. Ключ — путь от корня.
REPLACE_EXCEPTIONS = {
    "core/draftrender.py": (
        "прокси и превью-прокси пишет САМ ffmpeg (внешний процесс) прямо в <dst>.part.mp4, "
        "в памяти файла нет: os.replace публикует его только после проверки кода возврата "
        "и размера"),
    "core/aicut/video.py": (
        "скачанный ролик лежит во временном файле потока и публикуется лишь после проверок "
        "(Content-Type, Content-Length, сигнатура контейнера): они же решают, идти ли к "
        "следующему URL, — внутрь колбэка core.fileio это не переносится"),
}


def test_атомарная_запись_только_в_fileio():
    """`os.replace` мимо `core/fileio.py` — это чья-то своя копия атомарной записи.
    Такие копии и разъезжались: у одной не было fsync, у другой — уникального имени
    tmp (два параллельных писателя перемешивали файл)."""
    bad = []
    for p in _py_files():
        rel = _rel(p)
        if rel == "core/fileio.py" or rel in REPLACE_EXCEPTIONS:
            continue
        for i, line in enumerate(_lines(p), 1):
            if "os.replace(" in line:
                bad.append(f"{rel}:{i}: {line.strip()}")
    assert not bad, (
        "своя копия атомарной записи — переведи на core.fileio"
        " (atomic_json_dump/atomic_text_write/atomic_bytes_write):\n  " + "\n  ".join(bad))


# --------------------------------------------------------------------------- #
# 2. Длительность через ffprobe — только core/media.py
# --------------------------------------------------------------------------- #
# Пробы, где `format=duration` берётся ВМЕСТЕ с другими полями (размер кадра, кодек,
# таймкод): там свой вызов оправдан — второй заход ffprobe был бы вторым процессом на
# тот же файл или вторым сетевым запросом по той же ссылке.
DURATION_EXCEPTIONS = {
    "core/xmlbuild.py": (
        "проба совмещённая: длительность идёт вместе с размером кадра, кодеком и "
        "таймкодом — один ffprobe на каждую камеру сборки, см. probe()"),
    "core/aicut/video.py": (
        "проба ссылки: по длительности, размеру и кодеку решается тип референса, "
        "повторный заход по тому же URL — лишний сетевой запрос, см. probe_media()"),
}

DURATION_NEEDLE = "format=duration"


def test_длительность_считает_только_media():
    bad = []
    for p in _py_files():
        rel = _rel(p)
        if rel == "core/media.py" or rel in DURATION_EXCEPTIONS:
            continue
        for i, line in enumerate(_lines(p), 1):
            if DURATION_NEEDLE in line:
                bad.append(f"{rel}:{i}: {line.strip()}")
    assert not bad, (
        "длительность считается своей копией пробы — зови core.media.probe_duration:\n  "
        + "\n  ".join(bad))


def test_копии_длительности_зовут_общую_пробу():
    """Места, которым длительность нужна числом, спрашивают ОБЩУЮ функцию. Иначе
    поведение при ошибке разъедется снова (было: 0.0 здесь, исключение там)."""
    for rel in ("core/draftrender.py", "core/insertlib.py",
                "core/omni_review.py", "core/xmlbuild.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "probe_duration" in src, f"{rel} не зовёт общую пробу"


# --------------------------------------------------------------------------- #
# 3. subprocess — всегда с таймаутом
# --------------------------------------------------------------------------- #
# Файлы, где вызов без таймаута оправдан (список пуст — и это правильно): Popen с
# собственным контролем процесса (нарезка, рендер, ffmpeg в draftrender) сюда НЕ
# попадает вовсе — сторож смотрит только run/check_output.
SUBPROCESS_TIMEOUT_EXCEPTIONS = {}


def _subprocess_calls(tree):
    """Вызовы `subprocess.run`/`check_output` в дереве, с учётом псевдонимов импорта.

    Регуляркой такое не проверить: имена берутся из импорта (`import subprocess as sp`),
    а сам вызов бывает многострочным — виден только по узлу дерева.
    """
    mods, funcs = {"subprocess"}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "subprocess":
                    mods.add(a.asname or "subprocess")
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for a in node.names:
                funcs.add(a.asname or a.name)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
                and fn.value.id in mods):
            name = fn.attr
        elif isinstance(fn, ast.Name) and fn.id in funcs:
            name = fn.id
        else:
            continue
        if name in ("run", "check_output"):
            out.append(node)
    return out


def test_каждый_subprocess_с_таймаутом():
    """Зависший ffprobe/ffmpeg/git/node держит HTTP-запрос, поток джоба и GPU —
    отменять его нечем. Таймаут обязателен всем run/check_output."""
    bad = []
    for p in _py_files():
        rel = _rel(p)
        src = p.read_text(encoding="utf-8")
        for node in _subprocess_calls(ast.parse(src, filename=rel)):
            if any(kw.arg == "timeout" for kw in node.keywords):
                continue
            if rel in SUBPROCESS_TIMEOUT_EXCEPTIONS:
                continue
            bad.append(f"{rel}:{node.lineno}: {src.splitlines()[node.lineno - 1].strip()}")
    assert not bad, (
        "subprocess без timeout — процесс может зависнуть навсегда:\n  " + "\n  ".join(bad))


def test_списки_исключений_не_стареют():
    """Исключение без причины — молчаливая дыра, а исключение для починенного места —
    тихая ложь: сторож перестаёт ловить НОВЫЕ копии в том же файле."""
    stale = []
    for needle, table in ((DURATION_NEEDLE, DURATION_EXCEPTIONS),
                          ("os.replace(", REPLACE_EXCEPTIONS)):
        for rel, why in table.items():
            assert why.strip(), f"{rel}: исключение без причины"
            p = ROOT / rel
            if not p.is_file() or needle not in p.read_text(encoding="utf-8"):
                stale.append(f"{needle!r}: {rel}")
    for rel, why in SUBPROCESS_TIMEOUT_EXCEPTIONS.items():
        assert why.strip(), f"{rel}: исключение без причины"
    assert not stale, ("место починено или уехало — убери его из исключений:\n  "
                       + "\n  ".join(stale))


# --------------------------------------------------------------------------- #
# 4. Поведение общей пробы
# --------------------------------------------------------------------------- #
def test_нет_файла_это_none(tmp_path):
    """Несуществующий файл — None без исключения: «не прочли» это нормальный ответ
    (файл могли ещё писать, камеру удалили), а не повод валить джоб."""
    from core import media

    assert media.probe_duration(str(tmp_path / "нет-такого.mp4")) is None


def test_битый_файл_это_none(tmp_path):
    """ffprobe на мусоре ругается и выходит с ошибкой — наружу всё равно None."""
    from core import media

    p = tmp_path / "битый.mp4"
    p.write_bytes(b"\x00\x01\x02 not a video")
    assert media.probe_duration(str(p)) is None


def test_ошибки_пробы_не_летят_наружу(tmp_path, monkeypatch):
    """Зависший ffprobe (TimeoutExpired) и отсутствующий бинарник (OSError) —
    та же ошибка операции, что и «файл не открылся»: None, а не исключение."""
    from core import media

    p = tmp_path / "cam.mp4"
    p.write_bytes(b"x")

    def timeout(*a, **k):
        raise subprocess.TimeoutExpired(a[0] if a else "ffprobe", k.get("timeout"))

    monkeypatch.setattr(media.subprocess, "run", timeout)
    assert media.probe_duration(str(p)) is None

    def no_ffprobe(*a, **k):
        raise FileNotFoundError("ffprobe не найден")

    monkeypatch.setattr(media.subprocess, "run", no_ffprobe)
    assert media.probe_duration(str(p)) is None


def test_длительность_кэшируется(tmp_path, monkeypatch):
    """ffprobe — процесс на вызов, а спрашивают длительность в цикле (прогресс
    сборки черновика — на каждый кадр). Один вопрос на неизменённый файл."""
    from core import media

    p = tmp_path / "cam.mp4"
    p.write_bytes(b"x" * 64)
    calls = []

    class _R:
        stdout = "12.5\n"

    def fake_run(*a, **k):
        calls.append(a)
        return _R()

    monkeypatch.setattr(media.subprocess, "run", fake_run)
    assert media.probe_duration(str(p)) == 12.5
    assert media.probe_duration(str(p)) == 12.5
    assert len(calls) == 1, "ffprobe вызван повторно — кэш не работает"

    # Файл перезаписали — ключ кэша сменился, спрашиваем заново
    p.write_bytes(b"y" * 128)
    assert media.probe_duration(str(p)) == 12.5
    assert len(calls) == 2, "перезаписанный файл взят из старого кэша"


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="в PATH нет ffprobe")
def test_длительность_настоящего_медиа(tmp_path):
    """На настоящем файле — число. Видео в tests/fixtures нет (только XML, шрифты и
    JSON), поэтому медиа синтезируется stdlib-ом: WAV из модуля wave ffprobe читает
    так же, как любой контейнер, и длительность у него честная."""
    sys.path.insert(0, str(ROOT))
    from core import media

    p = tmp_path / "секунда.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00" * 16000)          # 8000 кадров по 2 байта = ровно 1 с
    dur = media.probe_duration(str(p))
    assert dur == pytest.approx(1.0, abs=0.05), f"не длина файла: {dur!r}"


def test_вызывающие_по_прежнему_видят_0_0(monkeypatch):
    """Контракт вызывающих не меняется: у них длительность — ЧИСЛО (0.0 = не прочли).
    None уехал бы в прогресс сборки и в середину ролика для vision."""
    from core import draftrender, media, omni_review, xmlbuild

    monkeypatch.setattr(media, "probe_duration", lambda path: None)
    assert draftrender._src_dur("нет.mp4") == 0.0
    assert omni_review._dur("нет.mp4") == 0.0
    assert xmlbuild.probe_audio_dur("нет.mp3") == 0.0

    monkeypatch.setattr(media, "probe_duration", lambda path: 12.0)
    assert draftrender._src_dur("нет.mp4") == 12.0
    assert omni_review._dur("нет.mp4") == 12.0
    assert xmlbuild.probe_audio_dur("нет.mp3") == 12.0
