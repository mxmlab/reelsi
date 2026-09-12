# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание GZ, пункт B: `emit` в `core/omni_cut.py` понимает именованные аргументы.

`_full_pass` и речек склеек зовут лог шаблоном: `emit("  full-анализ из кэша: {path}",
path=dst)`. Колбэк, который им передавал `main` (`lambda *x: print(*x, flush=True)` и
`lambda m: print(m, flush=True)`), таких аргументов не принимает — TypeError. В
`--full-audio` прогон падал целиком, а речек склеек в `--mode old` не работал никогда
(исключение глоталось на 901/908).

Тест берёт выражение `emit` ПРЯМО ИЗ ИСХОДНИКА `main` (ast) — своя лямбда в тесте
проверяла бы тест, а не код.

Запуск:  python -m pytest tests -q
"""
import ast
import json
import sys
import types
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

SRC = ROOT / "core" / "omni_cut.py"


def _emit_expr(call_name):
    """Выражение emit из `main`: `emit=` у вызова call_name либо присваивание `_emit`.

    Речек получает не литерал, а локальную переменную (`emit=_emit`) — её и разворачиваем.
    """
    assigned, call_expr = None, None
    for node in ast.walk(ast.parse(SRC.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_emit" for t in node.targets):
            assigned = ast.unparse(node.value)
        elif isinstance(node, ast.Call) and getattr(node.func, "id", None) == call_name:
            for kw in node.keywords:
                if kw.arg == "emit":
                    call_expr = ast.unparse(kw.value)
    expr = assigned if call_expr in (None, "_emit") else call_expr
    assert expr, f"в main не нашлось emit для {call_name}"
    return expr


def _emit_из_исходника(call_name):
    """Собрать тот же emit, что отдаёт main, и записать его строки в список."""
    from core.app_meta import console_emit, wrap_emit

    lines = []
    env = {"wrap_emit": wrap_emit,
           "console_emit": console_emit,
           "print": lambda *a, **k: lines.append(" ".join(str(x) for x in a))}
    expr = _emit_expr(call_name)
    return eval(expr, env), lines, expr                 # noqa: S307 — выражение из своего исходника


def test_full_pass_зовётся_emit_который_понимает_kwargs(tmp_path):
    """Кэш-ветка `_full_pass` — самая дешёвая: файл `.full.json` уже есть, тяжёлые
    части (звук, omni_asr) не нужны. На старом emit это TypeError."""
    from core import omni_cut

    emit, lines, expr = _emit_из_исходника("_full_pass")
    (tmp_path / "clip.full.json").write_text(
        json.dumps([{"start": 0.0, "end": 4.0, "text": "привет"}]), encoding="utf-8")
    a = types.SimpleNamespace(out=str(tmp_path / "clip.xml"))

    got = omni_cut._full_pass("нет-такого.wav", a, str(tmp_path), emit=emit)

    assert got and got[0]["text"] == "привет"
    assert any("clip.full.json" in line for line in lines), (
        f"emit={expr} не подставил {{path}} в строку: {lines}")


def _речек_parts(tmp_path, monkeypatch):
    """Общая обвязка речека: тяжёлые части (сниппет, subprocess) подменены."""
    from core import omni_cut

    monkeypatch.setattr(omni_cut, "_joint_snippet",
                        lambda a16, keep, t: np.full(32000, 1000, dtype="int16"))
    monkeypatch.setattr(omni_cut.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(stdout="", stderr="",
                                                              returncode=0))
    texts = [{"text": "не то"}, {"text": "привет мир"}]
    tail_list = [((0.0, 1.0), 0, 1)]
    return omni_cut, texts, tail_list


def test_речек_omni_зовётся_emit_который_понимает_kwargs(tmp_path, monkeypatch):
    """Речек склеек слухом Omni: `_emit` из main (`lambda m: print(m)`) не принимал
    i/j/cnt — исключение глоталось, и проверка склеек молча не работала."""
    omni_cut, texts, tail_list = _речек_parts(tmp_path, monkeypatch)
    emit, lines, expr = _emit_из_исходника("_emit")
    (tmp_path / "_splice_omni.json").write_text(
        json.dumps([{"text": "привет мир"}]), encoding="utf-8")

    bad = omni_cut._splice_recheck_omni(np.zeros(10, dtype="int16"), [(0.0, 1.0)],
                                        tail_list, texts, str(tmp_path), emit=emit)

    assert bad == []
    assert any("речек (Omni) склейки [0]→[1]" in line for line in lines), (
        f"emit={expr} не подставил i/j: {lines}")


def test_речек_whisper_зовётся_emit_который_понимает_kwargs(tmp_path, monkeypatch):
    """Тот же `_emit` уходит и в Whisper-фолбэк речека (строка 905)."""
    omni_cut, texts, tail_list = _речек_parts(tmp_path, monkeypatch)
    emit, lines, _ = _emit_из_исходника("_emit")
    from core import transcribe
    monkeypatch.setattr(transcribe, "transcribe",
                        lambda w: [{"w": "привет"}, {"w": "мир"}])

    bad = omni_cut._splice_recheck(np.zeros(10, dtype="int16"), [(0.0, 1.0)],
                                   tail_list, texts, str(tmp_path), emit=emit)

    assert bad == []
    assert any("речек склейки [0]→[1]" in line for line in lines), lines


@pytest.mark.parametrize("name", ["_full_pass", "_splice_recheck_omni", "_splice_recheck"])
def test_колбэк_из_main_сам_разбирает_шаблон(name):
    """Прямая проверка того же emit: строка с `{var}` и именованным аргументом должна
    печататься подставленной, а не падать и не показывать `{var}` как есть."""
    emit, lines, _ = _emit_из_исходника(name)
    emit("  шаблон {path} и {n}", path="C:/clip.full.json", n=3)
    assert lines == ["  шаблон C:/clip.full.json и 3"], lines
