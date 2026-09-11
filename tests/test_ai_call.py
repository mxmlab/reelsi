# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Контракт ИИ-вызова: чем ограничиваем модель и как её останавливаем.

Решение принято после разбора C1355 (deepseek-v4-flash): max_tokens НЕ
останавливает генерацию — модель досчитывает, провайдер тарифицирует полный
ответ, а нам обрывается уже готовый результат. Поэтому потолок мы не шлём, а
ограничители у нас другие: таймер в логе (видно, что модель думает, а не висит)
и «Стоп», который рвёт соединение на ближайшем чанке.

Запуск:  python -m pytest reelsi/tests -q
"""
import json
import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import aicut  # noqa: E402


@pytest.fixture(autouse=True)
def _no_cancel():
    """Каждый тест стартует с чистым флагом «Стоп»."""
    aicut.clear_cancel()
    yield
    aicut.clear_cancel()


@pytest.fixture(autouse=True)
def _ai_log_tmp(tmp_path, monkeypatch):
    """Лог вызовов пишется во временную папку, а не в боевой ai_calls.jsonl."""
    monkeypatch.setattr(aicut.llm, "AI_LOG_PATH", str(tmp_path / "ai_calls.jsonl"))


class FakeResp:
    """Минимальный SSE-поток chat/completions."""

    def __init__(self, lines):
        self._lines = lines

    def __iter__(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def sse(**delta):
    return ("data: " + json.dumps({"choices": [{"delta": delta}]}) + "\n").encode()


DONE = b"data: [DONE]\n"


def test_stream_collects_content_and_reasoning():
    r = FakeResp([sse(reasoning="дум"), sse(content='{"a"'), sse(content=": 1}"), DONE])
    out = aicut._read_stream(r, emit=lambda *a, **k: None)
    assert out["choices"][0]["message"]["content"] == '{"a": 1}'


def test_stop_breaks_stream_on_next_chunk():
    """«Стоп» должен рвать генерацию сразу, а не ждать конца ответа."""
    aicut.cancel_call()
    try:
        with pytest.raises(SystemExit):
            aicut._read_stream(FakeResp([sse(content="x"), DONE]),
                               emit=lambda *a, **k: None)
    finally:
        aicut.clear_cancel()        # снять флаг, иначе поедут другие тесты


def test_upstream_5xx_in_200_body_is_retryable():
    """Free-эндпоинты OpenRouter кладут 5xx в тело 200-потока — это повод повторить,
    а не падать (иначе теряется вся нарезка)."""
    body = ("data: " + json.dumps({"error": {"code": 502, "message": "busy"}}) + "\n")
    with pytest.raises(aicut.UpstreamBusy):
        aicut._read_stream(FakeResp([body.encode()]), emit=lambda *a, **k: None)


def _keepalive():
    return b": OPENROUTER PROCESSING\n"


def test_keepalive_does_not_hide_a_dead_stream(monkeypatch):
    """Замолчавший апстрим обязан обрываться, даже пока идут кипэлайвы.

    Пойманный баг (2026-08-10, пакетная разметка интро): модель написала 505 симв.
    за 16с и замолчала навсегда, а OpenRouter продолжил слать ": OPENROUTER
    PROCESSING". Вызов висел 11+ минут и не отвалился бы никогда: единственный
    предохранитель, urlopen(timeout=600), меряет тишину В СОКЕТЕ, а кипэлайв её
    обнуляет. Пакетный прогон встал намертво на третьем файле.
    """
    monkeypatch.setattr(aicut.llm, "STALL_MID", 0.15)

    def lines():
        yield sse(content='{"a"')
        while True:                       # апстрим мёртв, кипэлайвы идут вечно
            time.sleep(0.02)
            yield _keepalive()

    with pytest.raises(aicut.StreamStalled):
        aicut._read_stream(FakeResp(lines()), emit=lambda *a, **k: None)


def test_slow_but_growing_stream_is_not_killed(monkeypatch):
    """Сторож меряет РОСТ ответа, а не общее время: медленная живая генерация
    (кипэлайв — чанк — кипэлайв) не должна обрываться, сколько бы ни шла."""
    monkeypatch.setattr(aicut.llm, "STALL_MID", 0.15)

    def lines():
        for _ in range(6):
            time.sleep(0.03)
            yield _keepalive()
            time.sleep(0.03)
            yield sse(content="x")
        yield DONE

    out = aicut._read_stream(FakeResp(lines()), emit=lambda *a, **k: None)
    assert out["choices"][0]["message"]["content"] == "xxxxxx"


def test_wider_window_until_the_first_chunk(monkeypatch):
    """Пока ни одного чанка не было, окно шире: очередь free-эндпоинта и загрузка
    локальной модели в VRAM легко съедают минуту, и это не поломка."""
    monkeypatch.setattr(aicut.llm, "STALL_MID", 0.05)
    monkeypatch.setattr(aicut.llm, "STALL_FIRST", 5.0)

    def lines():
        for _ in range(6):
            time.sleep(0.03)
            yield _keepalive()
        yield sse(content="ok")
        yield DONE

    out = aicut._read_stream(FakeResp(lines()), emit=lambda *a, **k: None)
    assert out["choices"][0]["message"]["content"] == "ok"


def test_tick_counts_keepalives_and_names_the_silence(monkeypatch):
    """Тик обязан считаться и на кипэлайвах. Раньше он стоял после `continue` для
    не-data строк: индикатор «модель жива» замирал ровно тогда, когда он и нужен —
    в логе последняя строка «… 16с», а вызов идёт одиннадцатую минуту."""
    monkeypatch.setattr(aicut.llm, "STALL_MID", 5.0)
    monkeypatch.setattr(aicut.llm, "STALL_NOTE", 0.05)
    log = []

    def lines():
        yield sse(content="x")
        for _ in range(8):
            time.sleep(0.05)
            yield _keepalive()
        yield DONE

    aicut._read_stream(FakeResp(lines()), emit=lambda line="", **k: log.append(line + " " + str(k)),
                       tick=0.1)
    assert any("тишина" in m for m in log), log


def test_openai_payload_has_no_max_tokens_when_off(monkeypatch):
    """При «уме» off своего потолка вывода провайдеру не ставим.

    max_tokens не останавливает модель — она досчитывает, а нам обрывает уже
    готовый (и оплаченный) результат. Этот контракт для случая off остаётся."""
    sent = {}

    def fake_urlopen(req, timeout=None):
        sent["payload"] = json.loads(req.data.decode("utf-8"))
        return FakeResp([sse(content='{"ok": true}'), DONE])

    monkeypatch.setattr(aicut.llm.urllib.request, "urlopen", fake_urlopen)
    prof = {"provider": "openrouter", "base_url": "https://x/v1", "api_key": "k",
            "model": "test/model", "reasoning": "off", "name": "t"}
    aicut._ask_openai(prof, "sys", "user", {"type": "object"},
                      max_tokens=1234, emit=lambda *a, **k: None)
    assert "max_tokens" not in sent["payload"]
    assert sent["payload"]["reasoning"] == {"enabled": False}


def test_openai_payload_sends_max_tokens_when_reasoning_on(monkeypatch):
    """При включённом «уме» потолок вывода обязателен (задание BW).

    OpenRouter считает reasoning.effort ПРОЦЕНТОМ от max_tokens запроса (low ≈ 20%):
    без него low на модели с выводом 393k — это 78 тысяч токенов раздумий, «забивает
    всё окно и не выводит вывод». Поэтому при ум != off шлём max_tokens (ответ +
    бюджет размышлений, его уже посчитал шаг)."""
    sent = {}

    def fake_urlopen(req, timeout=None):
        sent["payload"] = json.loads(req.data.decode("utf-8"))
        return FakeResp([sse(content='{"ok": true}'), DONE])

    monkeypatch.setattr(aicut.llm.urllib.request, "urlopen", fake_urlopen)
    prof = {"provider": "openrouter", "base_url": "https://x/v1", "api_key": "k",
            "model": "test/model", "reasoning": "medium", "name": "t"}
    aicut._ask_openai(prof, "sys", "user", {"type": "object"},
                      max_tokens=18000, emit=lambda *a, **k: None)
    assert sent["payload"]["max_tokens"] == 18000
    assert sent["payload"]["reasoning"] == {"effort": "medium"}


def test_unsupported_level_is_downgraded_to_nearest_lower(monkeypatch):
    """Невалидный уровень не слать: провайдер молча мапит его в максимум (BW).

    У deepseek-v4-flash в каталоге только low/high/max — medium провайдер бы
    смапил в default_effort=high и сжёг на размышления сотни тысяч токенов.
    Код обязан подменить на ближайший СНИЗУ (medium -> low) и написать в лог."""
    seen, log = [], []

    def fake_urlopen(req, timeout=None):
        seen.append(json.loads(req.data.decode("utf-8")))
        return FakeResp([sse(content='{"ok": true}'), DONE])

    monkeypatch.setattr(aicut.llm.urllib.request, "urlopen", fake_urlopen)
    # Каталог models.dev: deepseek-v4-flash умеет только low/high/max (задание BY).
    caps = {"reasoning": True, "reasoning_kind": "effort",
            "efforts": ["low", "high", "max"], "structured_output": True,
            "temperature": True, "out_limit": 393216, "ctx_limit": 1310720,
            "cache": True, "default": True,
            "cost": {"input": 0.14, "output": 0.28}}
    monkeypatch.setattr(aicut.llm.catalog, "caps",
                        lambda p, m, emit=None: caps)
    prof = {"provider": "openrouter", "base_url": "https://x/v1", "api_key": "k",
            "model": "deepseek/deepseek-v4-flash-0731", "reasoning": "medium", "name": "t"}
    aicut._ask_openai(prof, "sys", "user", {"type": "object"},
                      max_tokens=18000,
                      emit=lambda line="", **k: log.append(line + " " + str(k)))
    assert seen[0]["reasoning"] == {"effort": "low"}
    assert any("не поддерживает" in m for m in log), log


def test_level_is_downgraded_one_step_on_retry(monkeypatch):
    """Повтор после битого JSON не жжёт тот же бюджет размышлений второй раз (BW).

    Первый вызов утонул в размышлениях, второй утонул бы так же — и заплачено
    дважды. На повторе уровень понижается на ступень (high -> medium)."""
    seen, log = [], []
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        seen.append(json.loads(req.data.decode("utf-8")).get("reasoning"))
        if calls["n"] == 1:
            return FakeResp([sse(content='{битый'), DONE])     # битый JSON -> повтор
        return FakeResp([sse(content='{"ok": true}'), DONE])

    monkeypatch.setattr(aicut.llm.urllib.request, "urlopen", fake_urlopen)
    prof = {"provider": "openrouter", "base_url": "https://x/v1", "api_key": "k",
            "model": "test/model", "reasoning": "high", "name": "t"}
    aicut._ask_openai(prof, "sys", "user", {"type": "object"},
                      max_tokens=16000, retries=1,
                      emit=lambda line="", **k: log.append(line + " " + str(k)))
    assert seen[0] == {"effort": "high"}
    assert seen[1] == {"effort": "medium"}
    assert any("не жжём дважды" in m for m in log), log


def test_reasoning_level_is_not_downgraded_by_code(monkeypatch):
    """Уровень «ума» выставляет юзер — код его молча не меняет (без каталога)."""
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append(json.loads(req.data.decode("utf-8")).get("reasoning"))
        return FakeResp([sse(content='{"ok": true}'), DONE])

    monkeypatch.setattr(aicut.llm.urllib.request, "urlopen", fake_urlopen)
    prof = {"provider": "openrouter", "base_url": "https://x/v1", "api_key": "k",
            "model": "test/model", "reasoning": "high", "name": "t"}
    aicut._ask_openai(prof, "sys", "user", {"type": "object"},
                      emit=lambda *a, **k: None)
    assert seen == [{"effort": "high"}]


def test_temperature_not_sent_when_model_does_not_accept_it(monkeypatch):
    """Модель без temperature (в каталоге false, задание BY) — не шлём и пишем в лог.

    У gpt-5.6-luna в каталоге models.dev temperature=false: отправленный 0.8
    OpenRouter молча выбрасывал, а cmd_inserts думал, что работает. Теперь каталог
    знает заранее — без круга «400 -> повтор»."""
    sent, log = [], []

    def fake_urlopen(req, timeout=None):
        sent.append(json.loads(req.data.decode("utf-8")))
        return FakeResp([sse(content='{"ok": true}'), DONE])

    monkeypatch.setattr(aicut.llm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(aicut.llm.catalog, "caps", lambda p, m, emit=None: {
        "reasoning": True, "reasoning_kind": "effort", "efforts": ["low", "high"],
        "structured_output": True, "temperature": False, "out_limit": 128000,
        "ctx_limit": 1050000, "cache": True, "default": False,
        "cost": {"input": 0.2, "output": 1.2}})
    prof = {"provider": "openrouter", "base_url": "https://x/v1", "api_key": "k",
            "model": "openai/gpt-5.6-luna", "reasoning": "off", "name": "t"}
    aicut._ask_openai(prof, "sys", "user", {"type": "object"},
                      max_tokens=18000, temperature=0.8,
                      emit=lambda line="", **k: log.append(line + " " + str(k)))
    assert "temperature" not in sent[0]
    assert any("не принимает temperature" in m for m in log), log


def test_schema_goes_to_prompt_when_structured_output_unsupported(monkeypatch):
    """structured_output=false в каталоге: схема сразу в промпт, круга «400 ->
    повтор без response_format» в логе быть не должно (задание BY)."""
    sent, log = [], []

    def fake_urlopen(req, timeout=None):
        sent.append(json.loads(req.data.decode("utf-8")))
        return FakeResp([sse(content='{"ok": true}'), DONE])

    monkeypatch.setattr(aicut.llm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(aicut.llm.catalog, "caps", lambda p, m, emit=None: {
        "reasoning": True, "reasoning_kind": "effort", "efforts": None,
        "structured_output": False, "temperature": True, "out_limit": None,
        "ctx_limit": None, "cache": False, "default": False,
        "cost": None})
    prof = {"provider": "openrouter", "base_url": "https://x/v1", "api_key": "k",
            "model": "tencent/hy3-preview", "reasoning": "off", "name": "t"}
    aicut._ask_openai(prof, "sys", "user", {"type": "object"},
                      max_tokens=10000,
                      emit=lambda line="", **k: log.append(line + " " + str(k)))
    assert "response_format" not in sent[0]
    sysc = sent[0]["messages"][0]["content"]
    text = sysc if isinstance(sysc, str) else sysc[0]["text"]
    assert "JSON-схеме" in text
    assert not any("не принял structured outputs" in m for m in log), log


def test_max_tokens_capped_by_out_limit(monkeypatch):
    """Потолок вывода при включённом уме — не больше out_limit модели из каталога (BY)."""
    sent = []

    def fake_urlopen(req, timeout=None):
        sent.append(json.loads(req.data.decode("utf-8")))
        return FakeResp([sse(content='{"ok": true}'), DONE])

    monkeypatch.setattr(aicut.llm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(aicut.llm.catalog, "caps", lambda p, m, emit=None: {
        "reasoning": True, "reasoning_kind": "effort", "efforts": ["low", "high", "max"],
        "structured_output": True, "temperature": True, "out_limit": 2000,
        "ctx_limit": 1310720, "cache": True, "default": True,
        "cost": {"input": 0.14, "output": 0.28}})
    prof = {"provider": "openrouter", "base_url": "https://x/v1", "api_key": "k",
            "model": "deepseek/deepseek-v4-flash-0731", "reasoning": "high", "name": "t"}
    aicut._ask_openai(prof, "sys", "user", {"type": "object"},
                      max_tokens=18000, emit=lambda *a, **k: None)
    assert sent[0]["max_tokens"] == 2000    # бюджет 18000 > out_limit 2000


def _read_ai_log(path):
    return [json.loads(l) for l in open(path, encoding="utf-8")]


def test_ai_log_append_writes_entry(tmp_path, monkeypatch):
    """Каждый вызов пишется в ai_calls.jsonl: шаг, модель, reasoning, токены."""
    logp = str(tmp_path / "ai_calls.jsonl")
    monkeypatch.setattr(aicut.llm, "AI_LOG_PATH", logp)
    aicut.llm.ai_log_append("cut", {"model": "m/1", "provider": "openrouter",
                                    "reasoning": "low"}, ok=True,
                            in_t=100, out_t=50, rt=30, finish="stop", ms=1234)
    rows = _read_ai_log(logp)
    assert len(rows) == 1
    r = rows[0]
    assert r["step"] == "cut" and r["model"] == "m/1"
    assert r["reasoning"] == "low" and r["ok"] is True
    assert r["in"] == 100 and r["out"] == 50 and r["rt"] == 30
    assert r["ms"] == 1234 and r["err"] is None


def test_ai_log_prunes_to_cap(tmp_path, monkeypatch):
    """Авточистка: файл растёт не дальше AI_LOG_CAP строк (держим свежий хвост)."""
    logp = str(tmp_path / "ai_calls.jsonl")
    monkeypatch.setattr(aicut.llm, "AI_LOG_PATH", logp)
    monkeypatch.setattr(aicut.llm, "AI_LOG_MAX_MB", 0)      # порог 0 -> чистим сразу
    cap = 5
    monkeypatch.setattr(aicut.llm, "AI_LOG_CAP", cap)
    for i in range(cap + 10):
        aicut.llm.ai_log_append("cut", {"model": "m/1"}, ok=True, out_t=i)
    rows = _read_ai_log(logp)
    assert len(rows) == cap
    assert [r["out"] for r in rows] == list(range(cap + 10))[-cap:]   # свежайшие


def test_ai_log_error_and_length(tmp_path, monkeypatch):
    """Ошибка пишется ok=False с текстом; обрезка по length — тоже ошибка."""
    logp = str(tmp_path / "ai_calls.jsonl")
    monkeypatch.setattr(aicut.llm, "AI_LOG_PATH", logp)
    aicut.llm.ai_log_append("yellow", {"model": "m/1", "provider": "openrouter"},
                            ok=False, err="провайдер вернул 402")
    r = _read_ai_log(logp)[0]
    assert r["ok"] is False and r["err"] == "провайдер вернул 402"
    assert r["step"] == "yellow"
