# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты на обрыв ответа провайдером по лимиту вывода.

Проверяет, что:
1. При finish_reason == "length" (OpenAI/OpenRouter) вызов падает с ReelsiError(output_cut),
   в сообщении есть общее число токенов, число reasoning_tokens (если есть) и рекомендация
   понизить «ум» или взять модель с большим выводом.
2. Обрезанный ответ (оборванный JSON) НЕ разбирается: _extract_json_obj не вытаскивает
   из него уцелевшие под-объекты, и результат шага не возвращается пользователю.
3. Ветка Anthropic при stop_reason == "max_tokens" аналогично падает с ReelsiError(output_cut).
"""
import json
import sys
import types

import pytest

from core import aicut
from core.aicut import llm
from core.umsg import ReelsiError


@pytest.fixture(autouse=True)
def _clean_state():
    """Сброс флага отмены перед каждым тестом."""
    aicut.clear_cancel()
    yield
    aicut.clear_cancel()


class FakeResp:
    """Имитация HTTP SSE-потока для urllib.request.urlopen."""

    def __init__(self, lines):
        self._lines = lines

    def __iter__(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _sse(obj):
    return ("data: " + json.dumps(obj) + "\n").encode("utf-8")


_DONE = b"data: [DONE]\n"


class _FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeUsage:
    def __init__(self, in_tok, out_tok):
        self.input_tokens = in_tok
        self.output_tokens = out_tok


class _FakeAnthropicMessage:
    def __init__(self, text, stop_reason="max_tokens", in_tok=120, out_tok=4096):
        self.content = [_FakeBlock(text)]
        self.stop_reason = stop_reason
        self.usage = _FakeUsage(in_tok, out_tok)


class _FakeStreamContext:
    def __init__(self, msg):
        self._msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self._msg


class _FakeMessages:
    def __init__(self, msg):
        self._msg = msg

    def stream(self, **kwargs):
        return _FakeStreamContext(self._msg)


class _FakeAnthropicClient:
    def __init__(self, msg):
        self.messages = _FakeMessages(msg)


def _make_anthropic_stub(msg):
    """Поддельный модуль anthropic для тестов без установленного пакета (в CI его нет)."""
    mod = types.ModuleType("anthropic")

    class _Err(Exception):
        pass

    for name in ("AuthenticationError", "PermissionDeniedError", "RateLimitError",
                 "APIConnectionError", "APIStatusError"):
        setattr(mod, name, type(name, (_Err,), {"status_code": 500}))

    mod.Anthropic = lambda **kw: _FakeAnthropicClient(msg)
    return mod


def test_openai_finish_reason_length_with_reasoning_tokens(monkeypatch):
    """Обрыв ответа OpenAI по length с reasoning_tokens -> ReelsiError(output_cut) с токенами."""
    # Обрывок содержит валидный объект {"id": 1}, который _extract_json_obj мог бы вытащить
    raw_chunk = '{"items": [{"id": 1, "text": "первый"}], "incomplete_tail": '
    stream_lines = [
        _sse({"choices": [{"delta": {"content": raw_chunk}}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "length"}]}),
        _sse({
            "usage": {
                "prompt_tokens": 150,
                "completion_tokens": 4096,
                "completion_tokens_details": {"reasoning_tokens": 1024},
            }
        }),
        _DONE,
    ]

    monkeypatch.setattr(
        llm.urllib.request,
        "urlopen",
        lambda req, timeout=None: FakeResp(stream_lines),
    )

    prof = {
        "provider": "openrouter",
        "base_url": "https://api.openrouter.ai/v1",
        "api_key": "dummy_key",
        "model": "test/model",
        "reasoning": "medium",
        "name": "test_profile",
    }
    schema = {
        "type": "object",
        "properties": {"id": {"type": "integer"}},
        "required": ["id"],
    }

    step_returned = False
    with pytest.raises(ReelsiError) as exc_info:
        res = llm._ask_openai(prof, "system prompt", "user prompt", schema, emit=lambda *a, **k: None)
        step_returned = True  # Не должно выполниться
        assert res is not None

    assert not step_returned, "Результат шага не должен возвращаться при обрыве"

    err = exc_info.value.umsg
    assert getattr(err, "code", None) == "output_cut", f"Ожидался код output_cut, получено: {err!r}"
    assert err.vars.get("tokens") == 4096

    msg = str(exc_info.value)
    assert "4096 токенов" in msg
    assert "1024 на размышления" in msg
    assert "лимиту вывода" in msg
    assert "Понизь уровень «ума»" in msg
    assert "с большим выводом" in msg


def test_openai_finish_reason_length_without_reasoning_tokens(monkeypatch):
    """Обрыв ответа OpenAI по length без reasoning_tokens -> ReelsiError(output_cut) без упоминания ума."""
    raw_chunk = '{"items": [{"id": 2}], "cut": '
    stream_lines = [
        _sse({"choices": [{"delta": {"content": raw_chunk}}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "length"}]}),
        _sse({
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 2048,
            }
        }),
        _DONE,
    ]

    monkeypatch.setattr(
        llm.urllib.request,
        "urlopen",
        lambda req, timeout=None: FakeResp(stream_lines),
    )

    prof = {
        "provider": "openai",
        "base_url": "https://api.openai.com/v1",
        "api_key": "dummy_key",
        "model": "gpt-4o",
        "reasoning": "off",
        "name": "test_openai",
    }
    schema = {"type": "object", "properties": {"items": {"type": "array"}}}

    step_returned = False
    with pytest.raises(ReelsiError) as exc_info:
        res = llm._ask_openai(prof, "sys", "user", schema, emit=lambda *a, **k: None)
        step_returned = True
        assert res is not None

    assert not step_returned, "Результат шага не должен возвращаться при обрыве"

    err = exc_info.value.umsg
    assert getattr(err, "code", None) == "output_cut"
    assert err.vars.get("tokens") == 2048

    msg = str(exc_info.value)
    assert "2048 токенов" in msg
    assert "на размышления" not in msg
    assert "лимиту вывода" in msg
    assert "Понизь уровень «ума»" in msg
    assert "с большим выводом" in msg


def test_openai_obryvok_is_not_parsed_into_partial_result(monkeypatch):
    """Обрывок не разбирается: если ответ оборван, валидный под-объект не спасает от output_cut."""
    # Этот кусок содержит валидный завершённый объект {"valid": "data"},
    # который json.loads(_extract_json_obj(raw)) бы успешно прочитал при отсутствии проверки finish_reason
    raw_chunk = '{"valid": "data"}, "unclosed": ['
    stream_lines = [
        _sse({"choices": [{"delta": {"content": raw_chunk}}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "length"}]}),
        _sse({"usage": {"completion_tokens": 512}}),
        _DONE,
    ]

    monkeypatch.setattr(
        llm.urllib.request,
        "urlopen",
        lambda req, timeout=None: FakeResp(stream_lines),
    )

    prof = {
        "provider": "openrouter",
        "base_url": "https://api.openrouter.ai/v1",
        "api_key": "k",
        "model": "m",
        "name": "p",
    }
    schema = {"type": "object", "required": ["valid"]}

    with pytest.raises(ReelsiError) as exc_info:
        llm._ask_openai(prof, "sys", "user", schema, emit=lambda *a, **k: None)

    assert exc_info.value.umsg.code == "output_cut"


def test_anthropic_stop_reason_max_tokens_raises_output_cut(monkeypatch):
    """Обрыв ответа Anthropic по stop_reason == 'max_tokens' -> ReelsiError(output_cut)."""
    truncated_text = '{"items": [{"id": 42}], "broken": '
    fake_msg = _FakeAnthropicMessage(
        text=truncated_text,
        stop_reason="max_tokens",
        in_tok=80,
        out_tok=8192,
    )

    # Подставляем stub-модуль anthropic в sys.modules
    stub = _make_anthropic_stub(fake_msg)
    monkeypatch.setitem(sys.modules, "anthropic", stub)

    prof = {
        "provider": "anthropic",
        "base_url": "https://api.anthropic.com",
        "api_key": "sk-ant-test",
        "model": "claude-3-7-sonnet",
        "reasoning": "off",
        "name": "test_claude",
    }
    schema = {"type": "object", "required": ["items"]}

    step_returned = False
    with pytest.raises(ReelsiError) as exc_info:
        res = llm._ask_anthropic(prof, "sys", "user", schema, emit=lambda *a, **k: None)
        step_returned = True
        assert res is not None

    assert not step_returned, "Результат шага не должен возвращаться при обрыве"

    err = exc_info.value.umsg
    assert getattr(err, "code", None) == "output_cut", f"Ожидался output_cut, получено: {err!r}"
    assert err.vars.get("tokens") == 8192

    msg = str(exc_info.value)
    assert "8192 токенов" in msg
    assert "лимиту вывода" in msg
    assert "Понизь уровень «ума»" in msg
    assert "с большим выводом" in msg


def test_ask_json_dispatches_and_guards_length(monkeypatch):
    """_ask_json диспетчеризует оба провайдера и при обрыве поднимает ReelsiError(output_cut)."""
    # 1. OpenAI через _ask_json
    raw_chunk = '{"status": "cut'
    stream_lines = [
        _sse({"choices": [{"delta": {"content": raw_chunk}}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "length"}]}),
        _sse({"usage": {"completion_tokens": 1000}}),
        _DONE,
    ]
    monkeypatch.setattr(
        llm.urllib.request,
        "urlopen",
        lambda req, timeout=None: FakeResp(stream_lines),
    )

    openai_prof = {
        "provider": "openrouter",
        "base_url": "https://api.openrouter.ai/v1",
        "api_key": "k",
        "model": "m",
    }
    monkeypatch.setattr(llm, "resolve_profile", lambda *a, **k: openai_prof)

    with pytest.raises(ReelsiError) as exc1:
        llm._ask_json("sys", "user", {"type": "object"}, emit=lambda *a, **k: None)
    assert exc1.value.umsg.code == "output_cut"

    # 2. Anthropic через _ask_json
    fake_msg = _FakeAnthropicMessage(text='{"status": "cut', stop_reason="max_tokens", out_tok=2000)
    stub = _make_anthropic_stub(fake_msg)
    monkeypatch.setitem(sys.modules, "anthropic", stub)

    anthropic_prof = {
        "provider": "anthropic",
        "base_url": "https://api.anthropic.com",
        "api_key": "k",
        "model": "claude-3-7-sonnet",
    }
    monkeypatch.setattr(llm, "resolve_profile", lambda *a, **k: anthropic_prof)

    with pytest.raises(ReelsiError) as exc2:
        llm._ask_json("sys", "user", {"type": "object"}, emit=lambda *a, **k: None)
    assert exc2.value.umsg.code == "output_cut"
