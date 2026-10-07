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
import io
import json
import os
import sys
import time
import urllib.error

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import aicut  # noqa: E402
from core.umsg import ReelsiError


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
        with pytest.raises(ReelsiError):
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
    """При включённом «уме» потолок вывода обязателен.

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
    """Невалидный уровень не слать: провайдер молча мапит его в максимум.

    У deepseek-v4-flash в каталоге только low/high/max — medium провайдер бы
    смапил в default_effort=high и сжёг на размышления сотни тысяч токенов.
    Код обязан подменить на ближайший СНИЗУ (medium -> low) и написать в лог."""
    seen, log = [], []

    def fake_urlopen(req, timeout=None):
        seen.append(json.loads(req.data.decode("utf-8")))
        return FakeResp([sse(content='{"ok": true}'), DONE])

    monkeypatch.setattr(aicut.llm.urllib.request, "urlopen", fake_urlopen)
    # Каталог models.dev: deepseek-v4-flash умеет только low/high/max.
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
    """Повтор после битого JSON не жжёт тот же бюджет размышлений второй раз.

    Первый вызов утонул в размышлениях, второй утонул бы так же — и заплачено
    дважды. На повторе уровень понижается на ступень (high -> medium).

    Возможности модели берутся из каталога, и подменённый `urlopen` ловит ЕГО запрос
    тоже (каталог зовёт тот же `urllib.request.urlopen`): при заполненном кэше каталога
    в воркере счёт вызовов сдвигался, и «битый JSON» отдавался уже второму вызову —
    повтора не наступало, тест падал на `seen[1]` (`-n auto`, порядок тестов в воркере).
    Поэтому каталог здесь назван явно — как в соседних тестах файла: «возможностей нет»,
    и первый вызов `urlopen` — ровно запрос модели.
    """
    seen, log = [], []
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        seen.append(json.loads(req.data.decode("utf-8")).get("reasoning"))
        if calls["n"] == 1:
            return FakeResp([sse(content='{битый'), DONE])     # битый JSON -> повтор
        return FakeResp([sse(content='{"ok": true}'), DONE])

    monkeypatch.setattr(aicut.llm.catalog, "caps", lambda p, m, emit=None: {
        "reasoning": None, "reasoning_kind": None, "efforts": None,
        "structured_output": None, "temperature": None, "out_limit": None,
        "ctx_limit": None, "cache": None, "default": None, "cost": None,
        "catalog_provider": None})
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
    """Модель без temperature (в каталоге false) — не шлём и пишем в лог.

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
    повтор без response_format» в логе быть не должно."""
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
    """Потолок вывода при включённом уме — не больше out_limit модели из каталога."""
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


def test_max_completion_tokens_retry_and_success(monkeypatch):
    """Провайдер отвечает 400 на max_tokens, требуя max_completion_tokens:
    второй запрос несёт max_completion_tokens и не несёт max_tokens, итог успешен."""
    calls = []

    def fake_urlopen(req, timeout=None):
        payload = json.loads(req.data.decode("utf-8"))
        calls.append(payload)
        if len(calls) == 1:
            err_body = b'{"error": {"message": "Unsupported parameter: max_tokens. Please use max_completion_tokens instead."}}'
            raise urllib.error.HTTPError(req.full_url if hasattr(req, "full_url") else str(req),
                                         400, "Bad Request", {}, io.BytesIO(err_body))
        return FakeResp([sse(content='{"ok": true}'), DONE])

    monkeypatch.setattr(aicut.llm.urllib.request, "urlopen", fake_urlopen)
    prof = {"provider": "openrouter", "base_url": "https://x/v1", "api_key": "k",
            "model": "test/model", "reasoning": "medium", "name": "t"}
    res = aicut._ask_openai(prof, "sys", "user", {"type": "object"},
                            max_tokens=1000, emit=lambda *a, **k: None)
    assert res == {"ok": True}
    assert len(calls) == 2
    assert "max_tokens" in calls[0] and "max_completion_tokens" not in calls[0]
    assert "max_completion_tokens" in calls[1] and "max_tokens" not in calls[1]
    assert calls[1]["max_completion_tokens"] == 1000


def test_stream_options_rejection_does_not_infinite_loop(monkeypatch):
    """Провайдер ВСЕГДА отвечает 400 про stream_options:
    функция завершается (ReelsiError по контракту), число вызовов urlopen <= retries + 3."""
    calls = []

    def fake_urlopen(req, timeout=None):
        payload = json.loads(req.data.decode("utf-8"))
        calls.append(payload)
        if len(calls) > 10:
            raise RuntimeError("бесконечный цикл: число вызовов urlopen превысило лимит")
        err_body = b'{"error": {"message": "stream_options is not supported by this model"}}'
        raise urllib.error.HTTPError(req.full_url if hasattr(req, "full_url") else str(req),
                                     400, "Bad Request", {}, io.BytesIO(err_body))

    monkeypatch.setattr(aicut.llm.urllib.request, "urlopen", fake_urlopen)
    prof = {"provider": "openrouter", "base_url": "https://x/v1", "api_key": "k",
            "model": "test/model", "reasoning": "off", "name": "t"}
    with pytest.raises(ReelsiError):
        aicut._ask_openai(prof, "sys", "user", {"type": "object"},
                          retries=1, emit=lambda *a, **k: None)
    assert len(calls) <= 1 + 3  # retries + 3


def test_reasoning_level_ladder_on_retry(monkeypatch):
    """Модель поддерживает high/medium/low, ответ не разбирается на первых двух
    попытках, retries=2 -> уровни трёх запросов high, medium, low."""
    monkeypatch.setattr(aicut.llm.catalog, "caps", lambda prov, m, emit=None: {
        "reasoning": True, "efforts": ["low", "medium", "high"],
        "reasoning_kind": "effort", "structured_output": True, "temperature": True,
        "out_limit": 2000, "ctx_limit": 1310720, "cache": True, "default": True,
    })
    calls = []

    def fake_urlopen(req, timeout=None):
        payload = json.loads(req.data.decode("utf-8"))
        calls.append(payload)
        if len(calls) < 3:
            return FakeResp([sse(content="broken json"), DONE])
        return FakeResp([sse(content='{"ok": true}'), DONE])

    monkeypatch.setattr(aicut.llm.urllib.request, "urlopen", fake_urlopen)
    prof = {"provider": "openrouter", "base_url": "https://x/v1", "api_key": "k",
            "model": "test/model", "reasoning": "high", "name": "t"}
    res = aicut._ask_openai(prof, "sys", "user", {"type": "object"},
                            retries=2, emit=lambda *a, **k: None)
    assert res == {"ok": True}
    assert len(calls) == 3
    efforts = [c["reasoning"]["effort"] for c in calls]
    assert efforts == ["high", "medium", "low"]


# ---- «Родной» effort: потолок самой модели, а не наш бюджет (баг 02.10.2026) ----
# Разметка вставок на openai/gpt-6-luna через OpenRouter: 6 вызовов подряд
# finish=length ровно по 26000 токенов (ответ 10000 + REASONING_BUDGET["high"]=16000),
# из них 25682–26000 на размышления. У openai/* effort — родной параметр, наш
# потолок размышления не ограничивает, а обрывает уже посчитанный ответ: таким
# моделям шлём out_limit из каталога.
NATIVE_CAPS = {"reasoning": True, "reasoning_kind": "effort",
               "efforts": ["low", "medium", "high"], "structured_output": True,
               "temperature": True, "out_limit": 128000, "ctx_limit": 1050000,
               "cache": True, "default": False,
               "cost": {"input": 0.2, "output": 1.2}}


def _native_prof(model="openai/gpt-5.6-luna", lvl="high"):
    return {"provider": "openrouter", "base_url": "https://x/v1", "api_key": "k",
            "model": model, "reasoning": lvl, "name": "t"}


def _sse_usage(**kw):
    return ("data: " + json.dumps({"usage": kw}) + "\n").encode()


def _sse_finish(reason):
    return ("data: " + json.dumps({"choices": [{"delta": {},
                                                "finish_reason": reason}]}) + "\n").encode()


def test_openai_family_gets_model_out_limit(monkeypatch):
    """openai/* + «ум» high: max_tokens = out_limit модели из каталога, не наш бюджет."""
    sent = []

    def fake_urlopen(req, timeout=None):
        sent.append(json.loads(req.data.decode("utf-8")))
        return FakeResp([sse(content='{"ok": true}'), DONE])

    monkeypatch.setattr(aicut.llm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(aicut.llm.catalog, "caps", lambda p, m, emit=None: dict(NATIVE_CAPS))
    aicut._ask_openai(_native_prof(), "sys", "user", {"type": "object"},
                      max_tokens=26000, emit=lambda *a, **k: None)
    assert sent[0]["max_tokens"] == 128000      # out_limit модели, а не 26000
    assert sent[0]["reasoning"] == {"effort": "high"}


def test_other_families_keep_answer_plus_budget(monkeypatch):
    """Другие семейства не трогаем: у anthropic/* потолок — доля effort, шлём бюджет."""
    sent = []

    def fake_urlopen(req, timeout=None):
        sent.append(json.loads(req.data.decode("utf-8")))
        return FakeResp([sse(content='{"ok": true}'), DONE])

    monkeypatch.setattr(aicut.llm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(aicut.llm.catalog, "caps",
                        lambda p, m, emit=None: dict(NATIVE_CAPS, out_limit=64000))
    aicut._ask_openai(_native_prof("anthropic/claude-sonnet-4-20250514"), "sys", "user",
                      {"type": "object"}, max_tokens=26000, emit=lambda *a, **k: None)
    assert sent[0]["max_tokens"] == 26000       # ответ + бюджет, как раньше


def test_native_effort_without_out_limit_keeps_budget(monkeypatch):
    """Нет out_limit в каталоге — поведение как раньше (наш бюджет)."""
    sent = []

    def fake_urlopen(req, timeout=None):
        sent.append(json.loads(req.data.decode("utf-8")))
        return FakeResp([sse(content='{"ok": true}'), DONE])

    monkeypatch.setattr(aicut.llm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(aicut.llm.catalog, "caps",
                        lambda p, m, emit=None: dict(NATIVE_CAPS, out_limit=None))
    aicut._ask_openai(_native_prof(), "sys", "user", {"type": "object"},
                      max_tokens=26000, emit=lambda *a, **k: None)
    assert sent[0]["max_tokens"] == 26000


def test_native_effort_off_sends_no_max_tokens(monkeypatch):
    """Ум off — потолок не шлём и для openai/* (как раньше)."""
    sent = []

    def fake_urlopen(req, timeout=None):
        sent.append(json.loads(req.data.decode("utf-8")))
        return FakeResp([sse(content='{"ok": true}'), DONE])

    monkeypatch.setattr(aicut.llm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(aicut.llm.catalog, "caps", lambda p, m, emit=None: dict(NATIVE_CAPS))
    aicut._ask_openai(_native_prof(lvl="off"), "sys", "user", {"type": "object"},
                      max_tokens=26000, emit=lambda *a, **k: None)
    assert "max_tokens" not in sent[0]
    assert sent[0]["reasoning"] == {"enabled": False}


def test_finish_length_at_our_ceiling_names_our_ceiling(monkeypatch):
    """completion_tokens == отправленному потолку -> это НАШ потолок, и числа в тексте."""
    def fake_urlopen(req, timeout=None):
        return FakeResp([sse(content='{"a": 1}'),
                         _sse_usage(prompt_tokens=10, completion_tokens=128000,
                                    completion_tokens_details={"reasoning_tokens": 125000}),
                         _sse_finish("length"), DONE])

    monkeypatch.setattr(aicut.llm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(aicut.llm.catalog, "caps", lambda p, m, emit=None: dict(NATIVE_CAPS))
    with pytest.raises(ReelsiError) as ei:
        aicut._ask_openai(_native_prof(), "sys", "user", {"type": "object"},
                          max_tokens=26000, emit=lambda *a, **k: None)
    e = ei.value
    assert e.code == "output_cut_ours"
    assert "потолок вывода 128000 токенов" in str(e)
    assert "125000 на размышления" in str(e)
    # В журнал уходит и отправленный потолок — по нему видно, кто обрезал ответ.
    rows = _read_ai_log(aicut.llm.AI_LOG_PATH)
    assert rows[-1]["ok"] is False and rows[-1]["mt"] == 128000


def test_finish_length_below_our_ceiling_names_provider_limit(monkeypatch):
    """completion_tokens меньше отправленного потолка -> лимит провайдера/модели."""
    def fake_urlopen(req, timeout=None):
        return FakeResp([sse(content='{"a": 1}'),
                         _sse_usage(prompt_tokens=10, completion_tokens=5000),
                         _sse_finish("length"), DONE])

    monkeypatch.setattr(aicut.llm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(aicut.llm.catalog, "caps", lambda p, m, emit=None: dict(NATIVE_CAPS))
    with pytest.raises(ReelsiError) as ei:
        aicut._ask_openai(_native_prof(), "sys", "user", {"type": "object"},
                          max_tokens=26000, emit=lambda *a, **k: None)
    e = ei.value
    assert e.code == "output_cut"               # НЕ наш потолок
    assert "по своему лимиту вывода" in str(e)


def test_ai_log_records_sent_max_tokens(monkeypatch):
    """В ai_calls.jsonl попадает ФАКТИЧЕСКИ отправленный max_tokens (поле mt)."""
    def fake_urlopen(req, timeout=None):
        return FakeResp([sse(content='{"ok": true}'), DONE])

    monkeypatch.setattr(aicut.llm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(aicut.llm.catalog, "caps", lambda p, m, emit=None: dict(NATIVE_CAPS))
    aicut._ask_openai(_native_prof(), "sys", "user", {"type": "object"},
                      max_tokens=26000, emit=lambda *a, **k: None)
    rows = _read_ai_log(aicut.llm.AI_LOG_PATH)
    assert rows[-1]["ok"] is True
    assert rows[-1]["mt"] == 128000

