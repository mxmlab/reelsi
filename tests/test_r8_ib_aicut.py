# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание IB (круг 8), п. 4 и 5: ответ модели как ДАННЫЕ, а не гарантия.

п. 4 — `float()` пропускает `NaN`/`Infinity` из ответа модели: `nan or 2.5` даёт `nan`,
`min`/`max` его не режут, и `nan` уезжает и в `.jsx` (там `_r(nan)` падает), и в JSON
(`jsonify` пишет голый `NaN` — невалидный JSON для браузера).
п. 5 — на пути Anthropic обрезанный по `max_tokens` ответ только писал строку в лог
(OpenAI в этом случае отказывает), а обязательные поля схемы не проверялись вовсе.

Запуск: py -3.10 -m pytest tests/test_r8_ib_aicut.py -q -p no:cacheprovider
"""
import gzip
import json
import math
import shutil
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from core.aicut import commands, llm  # noqa: E402


class _Emit:
    """Сбор строк лога шага: emit(msg, **vars) — как console_emit."""

    def __init__(self):
        self.lines = []

    def __call__(self, msg, **vars):
        try:
            self.lines.append(str(msg).format(**vars))
        except Exception:
            self.lines.append(str(msg))


@pytest.fixture()
def xml_subs(tmp_path):
    dst = tmp_path / "timeline.xml"
    with gzip.open(HERE / "fixtures" / "timeline_subs.xml.gz", "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return str(dst)


def _answer(start_photo, start_video):
    """Ответ модели: пять вставок с нечисловым/не конечным стартом и две годных."""
    return {"inserts": [
        {"type": "photo", "start_sec": float("nan"), "query": "чашка на столе"},
        {"type": "photo", "start_sec": float("inf"), "query": "связка ключей"},
        {"type": "photo", "start_sec": float("-inf"), "query": "чёрный стол"},
        {"type": "photo", "start_sec": "abc", "query": "настольная лампа"},
        {"type": "photo", "start_sec": None, "query": "окно во двор"},
        {"type": "photo", "start_sec": start_photo, "duration_sec": float("nan"),
         "query": "ноутбук на столе"},
        {"type": "video", "start_sec": start_video, "duration_sec": "abc",
         "query": "город с дрона"},
    ]}


def test_вставки_с_nan_и_бесконечностью_отброшены(xml_subs, monkeypatch):
    """Вставка без числового `start_sec` выбрасывается со строкой в лог: поставить её
    «в начало таймлайна» — значит показать не тот кадр. Нечисловая длительность — 0,
    дальше штатный кламп (2.5с)."""
    words = commands._words_from_xml(xml_subs)
    dur = words[-1][3]
    start_photo, start_video = round(dur * 0.3, 2), round(dur * 0.6, 2)

    monkeypatch.setattr(commands, "_ask_json", lambda *a, **k: _answer(start_photo, start_video))
    em = _Emit()
    res = commands.cmd_inserts(xml_subs, count=1, emit=em)

    starts = [it["start_sec"] for it in res["inserts"]]
    assert starts == sorted(starts), f"порядок вставок сломан: {starts}"
    assert start_photo in starts and start_video in starts, \
        f"годные вставки потерялись: {res['inserts']}"
    assert len(res["inserts"]) == 2, f"прошли нечисловые вставки: {res['inserts']}"
    assert all(math.isfinite(float(it["start_sec"])) for it in res["inserts"])
    assert all(math.isfinite(float(it["duration_sec"])) for it in res["inserts"])
    durations = {it["type"]: it["duration_sec"] for it in res["inserts"]}
    assert durations["photo"] == 2.5 and durations["video"] == 2.5, durations
    # голый NaN в ответе браузеру — невалидный JSON
    json.dumps(res["inserts"], ensure_ascii=False, allow_nan=False)
    assert sum(1 for line in em.lines if "отброшена" in line) == 5, em.lines


def test_годный_ответ_не_теряет_вставок(xml_subs, monkeypatch):
    """Обратная сторона: числовые `start_sec`/`duration_sec` проходят как раньше."""
    words = commands._words_from_xml(xml_subs)
    dur = words[-1][3]
    answer = {"inserts": [{"type": "photo", "start_sec": round(dur * 0.3, 2),
                           "duration_sec": 3.0, "query": "ноутбук на столе"}]}
    monkeypatch.setattr(commands, "_ask_json", lambda *a, **k: answer)
    res = commands.cmd_inserts(xml_subs, count=1, emit=_Emit())
    assert len(res["inserts"]) == 1
    assert res["inserts"][0]["duration_sec"] == 3.0


# ---------------------------------------------------------------- Anthropic (п. 5)

class _Resp:
    def __init__(self, text, stop_reason="end_turn", out_tokens=10):
        self.stop_reason = stop_reason
        self.content = [types.SimpleNamespace(type="text", text=text)]
        self.usage = types.SimpleNamespace(input_tokens=5, output_tokens=out_tokens)


def _anthropic_stub(responses):
    """Поддельный пакет `anthropic` (в CI его нет) с заранее заданными ответами.

    Через sys.modules, а не импорт: настоящего пакета в CI не будет никогда."""
    mod = types.ModuleType("anthropic")
    mod.calls = []

    class _Err(Exception):
        pass

    for name in ("AuthenticationError", "PermissionDeniedError", "RateLimitError",
                 "APIConnectionError", "APIStatusError"):
        setattr(mod, name, type(name, (_Err,), {"status_code": 500}))

    class _Stream:
        def __init__(self, resp):
            self._resp = resp

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return self._resp

    class _Messages:
        def stream(self, **kwargs):
            mod.calls.append(kwargs)
            i = min(len(mod.calls) - 1, len(responses) - 1)
            return _Stream(responses[i])

    class _Client:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.messages = _Messages()

    mod.Anthropic = _Client
    return mod


PROF = {"api_key": "k", "model": "claude-3-7-sonnet", "reasoning": "off", "base_url": ""}
SCHEMA = {"required": ["inserts"]}


def test_обрезанный_ответ_claude_это_отказ_а_не_строка_в_лог(monkeypatch):
    """`stop_reason == "max_tokens"` — оборванный ответ: `_extract_json_obj` возьмёт из
    него последний ЦЕЛЫЙ элемент, `json.loads` пройдёт, и шаг молча отдаст пустой
    результат. У OpenAI тут отказ — теперь и у Claude (IB, п. 5)."""
    stub = _anthropic_stub([_Resp('{"inserts": [', stop_reason="max_tokens", out_tokens=42)])
    monkeypatch.setitem(sys.modules, "anthropic", stub)

    with pytest.raises(SystemExit) as e:
        llm._ask_anthropic(PROF, "sys", "user", SCHEMA, emit=_Emit())

    err = e.value.code
    assert getattr(err, "code", None) == "output_cut", f"не отказ провайдера: {err!r}"
    assert err.vars.get("tokens") == 42, err.vars


def test_нет_обязательного_поля_ответ_повторяется(monkeypatch):
    """Схема могла не примениться: без проверки `required` шаг возвращал `{}` как
    результат, и вызывающий видел «вставок: 0» вместо повтора (IB, п. 5)."""
    stub = _anthropic_stub([_Resp('{"other": 1}'), _Resp('{"inserts": [{"a": 1}]}')])
    monkeypatch.setitem(sys.modules, "anthropic", stub)

    data = llm._ask_anthropic(PROF, "sys", "user", SCHEMA, retries=1, emit=_Emit())

    assert data == {"inserts": [{"a": 1}]}, f"принят ответ без обязательного поля: {data}"
    assert len(stub.calls) == 2, "повтора не было"


def test_обязательных_полей_нет_во_всех_попытках_отказ(monkeypatch):
    stub = _anthropic_stub([_Resp('{"other": 1}'), _Resp('{"other": 2}')])
    monkeypatch.setitem(sys.modules, "anthropic", stub)

    with pytest.raises(SystemExit) as e:
        llm._ask_anthropic(PROF, "sys", "user", SCHEMA, retries=1, emit=_Emit())

    assert getattr(e.value.code, "code", None) == "bad_json", repr(e.value.code)
    assert len(stub.calls) == 2, "повтора не было"


def test_нормальный_ответ_claude_принимается(monkeypatch):
    stub = _anthropic_stub([_Resp('{"inserts": [{"start_sec": 1.5}]}')])
    monkeypatch.setitem(sys.modules, "anthropic", stub)

    data = llm._ask_anthropic(PROF, "sys", "user", SCHEMA, emit=_Emit())
    assert data == {"inserts": [{"start_sec": 1.5}]}
    assert len(stub.calls) == 1
