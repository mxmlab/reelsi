# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты решающих функций `core/omni_asr.py`, которых не было ни в одном тесте.

Перечень собран механически (def-ы модуля против упоминаний в tests/): 9 функций из 11
не упоминались. Распознавание речи — вход всей нарезки, и ошибка тут дороже всего:
глухой эндпоинт уезжает в транскрипт репликами, «мысли» reasoning-модели — в субтитры,
не тот движок — в тайминги.

* `group_chunks` — где кончается кусок ~24с;
* `_looks_deaf` / `_deaf_guard` — признак «модель оглохла» и что сторож делает на третьем
  глухом чанке подряд (понятная ошибка вместо пустых реплик);
* `_hf_cache_bytes` / `ensure_weights` — «веса есть / качать / отказ»: объём кэша,
  повторы после обрыва и итоговая ошибка (без реального скачивания);
* `_asr_transcribe` / `transcribe_clip_cloud` — отказы провайдера, у каждого свой текст
  (401 ключ, 402 кредиты, 429 лимит, 404/400 «не принимает аудио»), переключение на
  /audio/transcriptions для чистых ASR-моделей и то, что «мысли» модели не попадают в
  расшифровку;
* `transcribe_clip` / `transcribe_clip_gigaam` — сборка результата на заглушке модели:
  какой движок позвали и что осталось на диске.

Ничего внешнего: torch, qwen_omni_utils, huggingface_hub и gigaam — заглушки в sys.modules
(в тестовом окружении их нет), сеть — заглушка `urllib.request.urlopen` вместо транспорта,
HF-кэш и временные wav — tmp_path, паузы повторов (20с на 429, 5с между попытками
скачивания) — в список, а не в реальное время. Сети, моделей и GPU тесты не трогают.

Запуск:  python -m pytest tests/test_omni_asr_units.py -q
"""
import base64
import io
import json
import os
import sys
import tempfile
import threading
import time
import types
import urllib.error
import urllib.request

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import omni_asr  # noqa: E402
from core.umsg import ReelsiError  # noqa: E402


PROF = {"name": "test_omni", "provider": "openrouter",
        "base_url": "https://api.example.test/v1", "api_key": "test-key",
        "model": "google/gemini-2.5-flash"}


def _prof(**over):
    p = dict(PROF)
    p.update(over)
    return p


def _clip_samples(n):
    """Кусок аудио 16 кГц int16 — ровно то, что omni_cut вырезает из дорожки."""
    np = pytest.importorskip("numpy")
    return np.arange(n, dtype="int16")


def _clip(seconds=0.1):
    return _clip_samples(int(omni_asr.SR * seconds))


# --------------------------------------------------------------------------- #
# Общая обвязка: тишина повторов, временный каталог, модульные глобалы
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Паузы повторов (20с на 429, 5с между попытками скачивания) — в список.

    Иначе один тест 429 ждал бы две минуты реального времени.
    """
    waits = []
    monkeypatch.setattr(time, "sleep", lambda s: waits.append(s))
    return waits


@pytest.fixture(autouse=True)
def tmp_tempdir(tmp_path, monkeypatch):
    """Временные wav — в tmp_path: боевой %TEMP% тестам не принадлежит.

    `transcribe_clip` пишет `_omni_clip_<pid>.wav`, `transcribe_clip_gigaam` —
    `_omni_gigaam_*.wav`; оба смотрят в `tempfile.gettempdir()`.
    """
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def clean_globals():
    """Модульные глобалы omni_asr — состояние между тестами.

    `_deaf_hits` (сколько глухих ответов подряд) и `_ASR_MODELS` (какие модели уже
    оказались чистыми ASR) живут в модуле: без сброса тесты влияли бы друг на друга.
    """
    omni_asr._deaf_hits[0] = 0
    omni_asr._ASR_MODELS.clear()
    yield
    omni_asr._deaf_hits[0] = 0
    omni_asr._ASR_MODELS.clear()


@pytest.fixture
def emitted():
    """Сборщик строк emit — как console_emit: `emit(шаблон, **переменные)`."""
    lines = []

    def _emit(*args, **vars):
        lines.append(((args[0] if args else vars.get("line", "")), vars))

    _emit.lines = lines
    return _emit


# --------------------------------------------------------------------------- #
# group_chunks: где кончается кусок ~24с
# --------------------------------------------------------------------------- #
def test_group_chunks_пустой_вход():
    assert omni_asr.group_chunks([]) == []


def test_group_chunks_один_интервал():
    assert omni_asr.group_chunks([(1.0, 2.5)]) == [(1.0, 2.5)]


def test_group_chunks_склеивает_пока_влезает_в_лимит():
    """Пауза между репликами кусок не разрывает, и конец куска — конец ПОСЛЕДНЕЙ
    реплики, а не начало следующей: в кусок входит и тишина между ними."""
    intervals = [(0.0, 4.0), (5.0, 9.0), (10.0, 15.0)]
    assert omni_asr.group_chunks(intervals, max_len=24.0) == [(0.0, 15.0)]


def test_group_chunks_режет_по_переходу_через_лимит():
    """Разрыв — перед репликой, которая вывела бы кусок за лимит, а не с её конца."""
    intervals = [(0.0, 10.0), (11.0, 20.0), (21.0, 30.0), (31.0, 40.0)]
    assert omni_asr.group_chunks(intervals, max_len=24.0) == [(0.0, 20.0), (21.0, 40.0)]


def test_group_chunks_ровно_лимит_не_разрывает():
    """Порог строгий (`>`): ровно max_len — ещё один кусок, на 0.1с длиннее — уже два."""
    assert omni_asr.group_chunks([(0.0, 10.0), (10.0, 24.0)], max_len=24.0) == [(0.0, 24.0)]
    assert omni_asr.group_chunks([(0.0, 10.0), (10.0, 24.1)], max_len=24.0) == \
        [(0.0, 10.0), (10.0, 24.1)]


def test_group_chunks_длинный_интервал_не_делится():
    """Один интервал длиннее лимита отдаётся целиком: дробить его здесь нечем —
    резать по словам будет нарезка, а обрубок без начала только потерял бы речь."""
    assert omni_asr.group_chunks([(5.0, 95.0)], max_len=24.0) == [(5.0, 95.0)]


# --------------------------------------------------------------------------- #
# _looks_deaf: глухой ответ эндпоинта
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("txt", [
    "Аудио не предоставлено.",
    "Аудио не был предоставлен в запросе",
    "Я не получил аудио, пришлите аудио ещё раз",
    "Пожалуйста, предоставьте аудио для транскрипции",
    "Загрузите аудио, и я его расшифрую",
    "I didn't receive any audio in your message",
    "No audio was provided",
])
def test_looks_deaf_узнаёт_глухой_ответ(txt):
    assert omni_asr._looks_deaf(txt) is True


@pytest.mark.parametrize("txt", [
    "", None, "   ",
    "Привет, сегодня разберём три упражнения на пресс.",
    "Мы записали звук и проверили его в редакторе.",
])
def test_looks_deaf_не_трогает_живую_расшифровку(txt):
    assert omni_asr._looks_deaf(txt) is False


def test_looks_deaf_длинный_текст_не_глухой():
    """Порог в 400 символов: настоящая расшифровка может упомянуть «аудио не
    предоставлено» (цитата, инструкция) — длинный ответ глухим не считается."""
    txt = "Сегодня разберём, как это работает: " + "слово " * 70 + "аудио не предоставлено"
    assert len(txt) >= 400
    assert omni_asr._looks_deaf(txt) is False


# --------------------------------------------------------------------------- #
# _deaf_guard: что сторож делает с глухим ответом
# --------------------------------------------------------------------------- #
def test_deaf_guard_пропускает_нормальный_ответ_и_сбрасывает_счётчик():
    omni_asr._deaf_hits[0] = 2
    assert omni_asr._deaf_guard(PROF, "обычная расшифровка") == "обычная расшифровка"
    assert omni_asr._deaf_hits[0] == 0


def test_deaf_guard_глухой_ответ_отдаёт_пусто():
    """Пустая строка, а не текст отказа: он уехал бы в транскрипт репликой."""
    assert omni_asr._deaf_guard(PROF, "Аудио не предоставлено.") == ""
    assert omni_asr._deaf_hits[0] == 1


def test_deaf_guard_нормальный_ответ_после_двух_глухих_спасает():
    """Счётчик считает именно ПОДРЯД идущие глухие чанки: живой чанк его обнуляет."""
    omni_asr._deaf_guard(PROF, "аудио не предоставлено")
    omni_asr._deaf_guard(PROF, "no audio provided")
    assert omni_asr._deaf_guard(PROF, "нормальная расшифровка") == "нормальная расшифровка"
    assert omni_asr._deaf_hits[0] == 0


def test_deaf_guard_третий_глухой_чанк_роняет_с_понятной_ошибкой():
    """Три глухих подряд — эндпоинт не принимает звук: дальше гонять чанки бессмысленно,
    и пользователю говорят про модель и что выбрать вместо неё."""
    omni_asr._deaf_guard(PROF, "Аудио не предоставлено.")
    omni_asr._deaf_guard(PROF, "I didn't receive any audio")
    with pytest.raises(ReelsiError) as err:
        omni_asr._deaf_guard(PROF, "no audio provided")

    text = str(err.value)
    assert "НЕ СЛЫШИТ аудио" in text
    assert PROF["model"] in text
    assert "Локально" in text


# --------------------------------------------------------------------------- #
# _hf_cache_bytes: сколько весов уже лежит в HF-кэше
# --------------------------------------------------------------------------- #
def _blobs_dir(home, repo="Qwen/Qwen2.5-Omni-7B"):
    return home / ".cache" / "huggingface" / "hub" / ("models--" + repo.replace("/", "--")) / "blobs"


@pytest.fixture
def hf_home(tmp_path, monkeypatch):
    """Домашний каталог — в tmp_path: боевой HF-кэш (~/.cache/huggingface) не трогаем."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))    # Windows: expanduser смотрит сюда
    return home


def test_hf_cache_bytes_без_каталога_ноль(hf_home):
    assert omni_asr._hf_cache_bytes("Qwen/Qwen2.5-Omni-7B") == 0


def test_hf_cache_bytes_суммирует_недокачанное(hf_home):
    """`.incomplete` считаются наравне с готовыми файлами: по ним и видно прогресс
    докачки, ради которого объём вообще считается."""
    blobs = _blobs_dir(hf_home)
    blobs.mkdir(parents=True)
    (blobs / "model.safetensors").write_bytes(b"a" * 2000)
    (blobs / "model.safetensors.incomplete").write_bytes(b"b" * 1000)
    assert omni_asr._hf_cache_bytes("Qwen/Qwen2.5-Omni-7B") == 3000


def test_hf_cache_bytes_считает_только_своё_репо(hf_home):
    """Имя репо разворачивается в каталог `models--Org--Name`: чужой кэш в объём
    докачки попасть не должен."""
    mine = _blobs_dir(hf_home, "Qwen/Qwen2.5-Omni-7B")
    other = _blobs_dir(hf_home, "Qwen/Qwen2.5-Omni-3B")
    mine.mkdir(parents=True)
    other.mkdir(parents=True)
    (mine / "a.bin").write_bytes(b"a" * 100)
    (other / "b.bin").write_bytes(b"b" * 9999)
    assert omni_asr._hf_cache_bytes("Qwen/Qwen2.5-Omni-7B") == 100


def test_hf_cache_bytes_пропускает_lock(hf_home):
    """`.lock` — не веса: в объёме скачанного ему делать нечего."""
    blobs = _blobs_dir(hf_home)
    blobs.mkdir(parents=True)
    (blobs / "0.0.0.lock").write_bytes(b"l" * 4096)
    (blobs / "a.incomplete").write_bytes(b"a" * 10)
    assert omni_asr._hf_cache_bytes("Qwen/Qwen2.5-Omni-7B") == 10


def test_hf_cache_bytes_терпит_исчезнувший_файл(hf_home, monkeypatch):
    """Файл исчез между обходом каталога и замером — он просто не попадает в объём,
    а не роняет подсчёт (иначе прогресс докачки падал бы с OSError)."""
    blobs = _blobs_dir(hf_home)
    blobs.mkdir(parents=True)
    (blobs / "gone.incomplete").write_bytes(b"g" * 500)
    (blobs / "here.incomplete").write_bytes(b"h" * 200)
    real = os.path.getsize

    def flaky(path):
        if str(path).endswith("gone.incomplete"):
            raise OSError("файл исчез между обходом и замером")
        return real(path)

    monkeypatch.setattr(os.path, "getsize", flaky)
    assert omni_asr._hf_cache_bytes("Qwen/Qwen2.5-Omni-7B") == 200


# --------------------------------------------------------------------------- #
# ensure_weights: «веса есть / качать / отказ»
# --------------------------------------------------------------------------- #
@pytest.fixture
def hf(monkeypatch):
    """Заглушка huggingface_hub: ни метаданных, ни скачивания из сети.

    `errors` — очередь исходов скачивания (исключение или None = успех), `delay` —
    сколько «качать»: с ним успевает отработать цикл прогресса.
    """
    calls = {"download": [], "api": 0}
    state = {"result": "/weights/Qwen2.5-Omni-7B", "errors": [], "siblings": [],
             "api_exc": None, "delay": 0.0, "cache_bytes": 0}

    class _HfApi:
        def model_info(self, repo, files_metadata=False):
            calls["api"] += 1
            if state["api_exc"] is not None:
                raise state["api_exc"]
            return types.SimpleNamespace(siblings=state["siblings"])

    def _snapshot_download(repo, max_workers=4):
        calls["download"].append({"repo": repo, "max_workers": max_workers})
        if state["delay"]:
            threading.Event().wait(state["delay"])      # не time.sleep: его тесты гасят
        if state["errors"]:
            err = state["errors"].pop(0)
            if err is not None:
                raise err
        return state["result"]

    mod = types.ModuleType("huggingface_hub")
    mod.HfApi = _HfApi
    mod.snapshot_download = _snapshot_download
    monkeypatch.setitem(sys.modules, "huggingface_hub", mod)
    # объём кэша берём из состояния: реальный HF-кэш машины тестам не принадлежит
    monkeypatch.setattr(omni_asr, "_hf_cache_bytes", lambda repo: state["cache_bytes"])
    return calls, state


def test_ensure_weights_готовые_веса_отдаёт_путь(hf, emitted):
    calls, state = hf
    assert omni_asr.ensure_weights("Qwen/Qwen2.5-Omni-7B", emit=emitted) == state["result"]
    assert len(calls["download"]) == 1                  # повтор не понадобился
    assert [line for line, _v in emitted.lines][-1] == "  веса на месте"


def test_ensure_weights_повтор_после_обрыва(hf, emitted, no_sleep):
    """Обрыв — не отказ: snapshot_download докачивает с места, попыток retries+1,
    а в лог уходит вид ошибки и номер попытки."""
    calls, state = hf
    state["errors"] = [OSError("соединение сброшено"), None]

    assert omni_asr.ensure_weights("repo", emit=emitted, retries=2) == state["result"]

    assert len(calls["download"]) == 2
    broken = [line for line, _v in emitted.lines if "обрыв скачивания" in line]
    assert len(broken) == 1
    assert "OSError" in broken[0] and "(1/2)" in broken[0]
    assert no_sleep == [5]                              # пауза перед докачкой


def test_ensure_weights_отказ_после_всех_попыток(hf, emitted, no_sleep):
    calls, state = hf
    state["errors"] = [RuntimeError("boom")] * 3

    with pytest.raises(ReelsiError) as err:
        omni_asr.ensure_weights("repo", emit=emitted, retries=2)

    assert "не удалось скачать веса после 3 попыток" in str(err.value)
    assert "boom" in str(err.value)
    assert len(calls["download"]) == 3


def test_ensure_weights_прогресс_знает_размер_весов(hf, emitted):
    """Размер берётся из метаданных и только по весам (.safetensors/.bin): прогресс
    идёт в «скачано/всего»."""
    calls, state = hf
    state["siblings"] = [types.SimpleNamespace(rfilename="model-00001.safetensors",
                                               size=2_000_000_000),
                         types.SimpleNamespace(rfilename="config.json", size=1000),
                         types.SimpleNamespace(rfilename="model-00002.bin",
                                               size=1_000_000_000)]
    state["cache_bytes"] = 500_000_000
    state["delay"] = 0.05

    omni_asr.ensure_weights("repo", emit=emitted)

    progress = [line for line, _v in emitted.lines if line.startswith("  скачивание весов")]
    assert progress == ["  скачивание весов: 0.5/3.0 ГБ (16%)"]


def test_ensure_weights_без_метаданных_качает_дальше(hf, emitted):
    """HuggingFace недоступен, размер весов не узнать — это не повод не качать:
    в логе остаётся только скачанное."""
    calls, state = hf
    state["api_exc"] = RuntimeError("503 Service Unavailable")
    state["cache_bytes"] = 1_500_000_000
    state["delay"] = 0.05

    omni_asr.ensure_weights("repo", emit=emitted)

    progress = [line for line, _v in emitted.lines if line.startswith("  скачивание весов")]
    assert progress == ["  скачивание весов: 1.5 ГБ"]


# --------------------------------------------------------------------------- #
# HTTP-заглушка: вместо сети — заготовленные ответы по очереди
# --------------------------------------------------------------------------- #
class _Resp:
    """Ответ транспорта: `json.load(r)` читает его как файл.

    Тело одноразовое (как у настоящего ответа), поэтому на каждый запрос нужен свой
    объект — список `[...] * n` тут обманывает: второй повтор прочитал бы пустоту.
    """

    def __init__(self, payload=None, raw=None):
        body = raw if raw is not None else \
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._buf = io.BytesIO(body)

    def read(self, amt=None):
        return self._buf.read() if amt is None else self._buf.read(amt)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _json_resp(payload):
    return _Resp(payload)


def _http_error(code, detail=""):
    """HTTPError с телом ответа — так провайдер и отвечает на плохой ключ или лимит."""
    return urllib.error.HTTPError("https://api.example.test/v1", code, "err", {},
                                  io.BytesIO(detail.encode("utf-8")))


@pytest.fixture
def http(monkeypatch):
    """Заглушка urllib: ответы кончились — тест падает.

    Лишний запрос — это в том числе лишние деньги у провайдера, поэтому «пошёл
    повторять там, где не ждали» должно быть видно, а не проглатываться.
    """
    state = {"answers": [], "calls": []}

    def _urlopen(req, timeout=None):
        state["calls"].append(req)
        if not state["answers"]:
            raise AssertionError(f"лишний запрос: {req.full_url}")
        ans = state["answers"].pop(0)
        if isinstance(ans, Exception):
            raise ans
        return ans

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    return state


def _body(req):
    return json.loads(req.data.decode("utf-8"))


# --------------------------------------------------------------------------- #
# _asr_transcribe: OpenAI-совместимый /audio/transcriptions
# --------------------------------------------------------------------------- #
def test_asr_transcribe_шлёт_wav_в_multipart_и_отдаёт_текст(http):
    http["answers"].append(_json_resp({"text": "  привет мир  "}))

    assert omni_asr._asr_transcribe(PROF, _clip()) == "привет мир"

    req, = http["calls"]
    assert req.full_url == "https://api.example.test/v1/audio/transcriptions"
    assert req.get_header("Authorization") == "Bearer test-key"
    assert req.get_header("Content-type").startswith("multipart/form-data; boundary=")
    body = req.data
    assert b'name="model"' in body and PROF["model"].encode() in body
    assert b'name="response_format"' in body and b"json" in body
    assert b'filename="audio.wav"' in body and b"audio/wav" in body
    assert b"RIFF" in body                              # сам wav уехал в запрос


def test_asr_transcribe_собирает_текст_из_results(http):
    """Часть провайдеров отвечает не `text`, а списком `results` — склеиваем."""
    http["answers"].append(_json_resp({"results": [{"text": "раз"}, {"text": "два"}]}))
    assert omni_asr._asr_transcribe(PROF, _clip()) == "раз два"


def test_asr_transcribe_пустой_ответ_это_пустая_строка(http):
    http["answers"].append(_json_resp({"text": None}))
    assert omni_asr._asr_transcribe(PROF, _clip()) == ""


@pytest.mark.parametrize("code", [401, 403])
def test_asr_transcribe_ключ_не_принят_без_повторов(http, code):
    """Тот же ключ повторять нечего: отказ сразу и с именем профиля."""
    http["answers"].append(_http_error(code, '{"error":"invalid api key"}'))

    with pytest.raises(ReelsiError) as err:
        omni_asr._asr_transcribe(PROF, _clip())

    text = str(err.value)
    assert "API-ключ не принят" in text and PROF["name"] in text and str(code) in text
    assert len(http["calls"]) == 1


def test_asr_transcribe_402_кредиты(http):
    http["answers"].append(_http_error(402, '{"error":"insufficient credits"}'))

    with pytest.raises(ReelsiError) as err:
        omni_asr._asr_transcribe(PROF, _clip())

    assert "кончились кредиты" in str(err.value) and "402" in str(err.value)


def test_asr_transcribe_429_ждёт_и_потом_сдаётся(http, no_sleep):
    """Лимит запросов — пауза и повтор, НЕ тратя попытки; но бесконечно ждать нельзя."""
    http["answers"] += [_http_error(429, '{"error":"rate limited"}') for _ in range(6)]

    with pytest.raises(ReelsiError) as err:
        omni_asr._asr_transcribe(PROF, _clip())

    assert "лимит запросов провайдера (429) не отпускает" in str(err.value)
    assert len(http["calls"]) == 6                      # 5 пауз и шестой отказ
    assert no_sleep == [20] * 5


def test_asr_transcribe_сбой_сети_повторяется(http, no_sleep):
    http["answers"] += [urllib.error.URLError("нет сети"), _json_resp({"text": "ок"})]

    assert omni_asr._asr_transcribe(PROF, _clip()) == "ок"
    assert len(http["calls"]) == 2


def test_asr_transcribe_отказ_после_всех_попыток(http):
    """Прочие коды (500 и т.п.) — повтор, и только потом понятная ошибка."""
    http["answers"] += [_http_error(500, "internal error") for _ in range(3)]

    with pytest.raises(ReelsiError) as err:
        omni_asr._asr_transcribe(PROF, _clip(), retries=2)

    assert "ASR-транскрипция упала после 3 попыток" in str(err.value)
    assert "500" in str(err.value)
    assert len(http["calls"]) == 3


def test_asr_transcribe_не_json_ответ_это_сбой_чанка_а_не_крах(http):
    """Не-JSON тело ответа (страница шлюза, обрезанный ответ) обрабатывается как сбой
    чанка: повторы по той же схеме и итоговый ReelsiError, а не сырой JSONDecodeError."""
    http["answers"] += [_Resp(raw=b"<html>502 Bad Gateway</html>") for _ in range(3)]

    with pytest.raises(ReelsiError) as err:
        omni_asr._asr_transcribe(PROF, _clip(), retries=2)

    assert "не-JSON" in str(err.value)
    assert len(http["calls"]) == 3


# --------------------------------------------------------------------------- #
# transcribe_clip_cloud: /chat/completions и переключение на ASR
# --------------------------------------------------------------------------- #
def test_cloud_успех_шлёт_аудио_и_разбирает_ответ(http):
    http["answers"].append(_json_resp({"choices": [{"message": {"content": " привет "}}]}))

    assert omni_asr.transcribe_clip_cloud(PROF, _clip()) == "привет"

    req, = http["calls"]
    assert req.full_url == "https://api.example.test/v1/chat/completions"
    assert req.get_header("Authorization") == "Bearer test-key"
    body = _body(req)
    assert body["model"] == PROF["model"]
    assert body["temperature"] == 0.0
    assert body["max_tokens"] == 1600                   # с запасом на «размышления»
    assert body["messages"][0] == {"role": "system", "content": omni_asr.SYS}
    audio = body["messages"][1]["content"][0]
    assert audio["type"] == "input_audio" and audio["input_audio"]["format"] == "wav"
    assert base64.b64decode(audio["input_audio"]["data"]).startswith(b"RIFF")
    assert body["reasoning"] == {"enabled": False}      # думать вслух тут нечего


def test_cloud_у_локального_провайдера_нет_параметра_reasoning(http):
    """reasoning шлётся только OpenRouter/OpenAI: LM Studio на незнакомый параметр
    отвечает 400, и каждый чанк стоил бы лишнего круга."""
    prof = _prof(provider="lmstudio", base_url="http://127.0.0.1:1234/v1")
    http["answers"].append(_json_resp({"choices": [{"message": {"content": "ок"}}]}))

    assert omni_asr.transcribe_clip_cloud(prof, _clip()) == "ок"
    assert "reasoning" not in _body(http["calls"][0])


def test_cloud_400_про_reasoning_повторяет_без_него(http):
    """Провайдер не понял параметр — повтор без него и БЕЗ траты попытки."""
    http["answers"] += [_http_error(400, '{"error":"unknown parameter: reasoning"}'),
                        _json_resp({"choices": [{"message": {"content": "ок"}}]})]

    assert omni_asr.transcribe_clip_cloud(PROF, _clip()) == "ок"

    assert len(http["calls"]) == 2
    assert "reasoning" in _body(http["calls"][0])
    assert "reasoning" not in _body(http["calls"][1])


def test_cloud_400_с_reasoning_у_локального_провайдера_не_особый_случай(http):
    """Параметр ему и не слали — значит 400 про него обычная ошибка и повтор."""
    prof = _prof(provider="lmstudio", base_url="http://127.0.0.1:1234/v1")
    http["answers"] += [_http_error(400, '{"error":"unknown parameter: reasoning"}'),
                        _json_resp({"choices": [{"message": {"content": "ок"}}]})]

    assert omni_asr.transcribe_clip_cloud(prof, _clip()) == "ок"
    assert len(http["calls"]) == 2
    assert "reasoning" not in _body(http["calls"][1])


@pytest.mark.parametrize("code,detail", [
    (404, '{"error":"No endpoints found for this model"}'),
    (400, '{"error":"model does not support audio modality"}'),
])
def test_cloud_модель_без_аудио_отказ_сразу(http, code, detail):
    """Модель заявлена мультимодальной, а звук не принимает: повтор не поможет —
    ошибка сразу и с советом, какую модель взять."""
    http["answers"].append(_http_error(code, detail))

    with pytest.raises(ReelsiError) as err:
        omni_asr.transcribe_clip_cloud(PROF, _clip())

    text = str(err.value)
    assert "не принимает аудио" in text and PROF["model"] in text
    assert len(http["calls"]) == 1


@pytest.mark.parametrize("code", [401, 403])
def test_cloud_ключ_не_принят_без_повторов(http, code):
    http["answers"].append(_http_error(code, '{"error":"invalid api key"}'))

    with pytest.raises(ReelsiError) as err:
        omni_asr.transcribe_clip_cloud(PROF, _clip())

    assert "API-ключ не принят" in str(err.value)
    assert len(http["calls"]) == 1


def test_cloud_402_кредиты(http):
    http["answers"].append(_http_error(402, '{"error":"insufficient credits"}'))

    with pytest.raises(ReelsiError) as err:
        omni_asr.transcribe_clip_cloud(PROF, _clip())

    assert "кончились кредиты" in str(err.value)


def test_cloud_402_баланс_для_аудио_объясняется_отдельно(http):
    """У OpenRouter на аудио своё требование к балансу — про него и говорим, иначе
    пользователь ищет причину в ключе."""
    http["answers"].append(_http_error(
        402, '{"error":"You need at least $0.50 balance for audio requests"}'))

    with pytest.raises(ReelsiError) as err:
        omni_asr.transcribe_clip_cloud(PROF, _clip())

    text = str(err.value)
    assert "$0.50" in text and "openrouter.ai/credits" in text


def test_cloud_429_ждёт_и_потом_сдаётся(http, no_sleep):
    http["answers"] += [_http_error(429, '{"error":"rate limited"}') for _ in range(6)]

    with pytest.raises(ReelsiError) as err:
        omni_asr.transcribe_clip_cloud(PROF, _clip())

    assert "429" in str(err.value)
    assert len(http["calls"]) == 6
    assert no_sleep == [20] * 5


def test_cloud_переключается_на_asr_эндпоинт_и_запоминает_модель(http):
    """Провайдер говорит «это транскрибационная модель» — уходим на /audio/transcriptions
    и запоминаем: следующие чанки идут туда сразу, без лишнего круга."""
    http["answers"] += [
        _http_error(400, '{"error":"This is a transcription model, use /audio/transcriptions"}'),
        _json_resp({"text": " расшифровка "})]

    assert omni_asr.transcribe_clip_cloud(PROF, _clip()) == "расшифровка"

    assert PROF["model"] in omni_asr._ASR_MODELS
    assert http["calls"][1].full_url.endswith("/audio/transcriptions")

    http["answers"].append(_json_resp({"text": "второй чанк"}))
    assert omni_asr.transcribe_clip_cloud(PROF, _clip()) == "второй чанк"
    assert http["calls"][2].full_url.endswith("/audio/transcriptions")


def test_cloud_известная_asr_модель_идёт_сразу_на_asr(http):
    omni_asr._ASR_MODELS.add(PROF["model"])
    http["answers"].append(_json_resp({"text": "  расшифровка  "}))

    assert omni_asr.transcribe_clip_cloud(PROF, _clip()) == "расшифровка"
    assert http["calls"][0].full_url.endswith("/audio/transcriptions")


def test_cloud_мысли_модели_не_уезжают_в_транскрипт(http):
    """`reasoning_content` — размышления, а не расшифровка. Пустой `content` при них
    это сбой чанка (повтор), а не текст для субтитров."""
    for _ in range(3):
        http["answers"].append(_json_resp({"choices": [{
            "message": {"content": "", "reasoning_content": "думаю про монтаж и тишину"},
            "finish_reason": "stop"}]}))

    with pytest.raises(ReelsiError) as err:
        omni_asr.transcribe_clip_cloud(PROF, _clip(), retries=2)

    text = str(err.value)
    assert "думаю про монтаж" not in text
    assert "расшифровка чанка не получена" in text
    assert len(http["calls"]) == 3


def test_cloud_обрезанный_по_токенам_ответ_повторяется(http):
    """`finish_reason: length` — думающая модель съела max_tokens: это тоже сбой,
    и в ошибке должно быть сказано, что именно случилось."""
    http["answers"] += [_json_resp({"choices": [{"message": {"content": ""},
                                                 "finish_reason": "length"}]})
                        for _ in range(3)]

    with pytest.raises(ReelsiError) as err:
        omni_asr.transcribe_clip_cloud(PROF, _clip(), retries=2)

    assert "ответ обрезан по max_tokens" in str(err.value)


def test_cloud_ответ_без_choices_повторяется(http):
    http["answers"] += [_json_resp({"error": "upstream is down"}) for _ in range(3)]

    with pytest.raises(ReelsiError) as err:
        omni_asr.transcribe_clip_cloud(PROF, _clip(), retries=2)

    assert "провайдер не вернул ответ" in str(err.value)


def test_cloud_не_json_ответ_это_сбой_чанка_а_не_крах(http):
    """Не-JSON ответ провайдера (страница шлюза, обрезанное тело) обрабатывается как сбой
    чанка: повторы по той же схеме и итоговый ReelsiError, а не сырой JSONDecodeError."""
    http["answers"] += [_Resp(raw=b"<html>502 Bad Gateway</html>") for _ in range(3)]

    with pytest.raises(ReelsiError) as err:
        omni_asr.transcribe_clip_cloud(PROF, _clip(), retries=2)

    assert "не-JSON" in str(err.value)
    assert len(http["calls"]) == 3


def test_cloud_глухой_ответ_отдаёт_пустую_строку(http):
    """Сторож глухого эндпоинта работает и на облачном пути: в транскрипт не уезжает
    «аудио не предоставлено»."""
    http["answers"].append(_json_resp({"choices": [{"message": {
        "content": "Аудио не предоставлено."}}]}))

    assert omni_asr.transcribe_clip_cloud(PROF, _clip()) == ""
    assert omni_asr._deaf_hits[0] == 1


# --------------------------------------------------------------------------- #
# transcribe_clip: локальный Qwen2.5-Omni
# --------------------------------------------------------------------------- #
class _Ids:
    def __init__(self, prompt_len):
        self.shape = (1, prompt_len)


class _Inputs(dict):
    """Входы процессора: `.to` зовётся цепочкой (device, потом dtype)."""

    def __init__(self, prompt_len):
        super().__init__({"input_ids": _Ids(prompt_len)})
        self.moves = []

    def to(self, target):
        self.moves.append(target)
        return self


class _GenIds:
    """Фейковый ответ модели: помнит срез — по нему видно, что декодируется."""

    def __init__(self):
        self.sliced = None

    def __getitem__(self, item):
        self.sliced = item
        return "GENERATED"


class _Proc:
    def __init__(self, reply="  расшифровка  "):
        self.reply = reply
        self.conv = self.template_kw = None
        self.inputs = self.call_kw = None
        self.decoded = self.decode_kw = None
        self.audio_data = self.audio_sr = None

    def apply_chat_template(self, conv, **kw):
        self.conv, self.template_kw = conv, kw
        return "PROMPT"

    def __call__(self, **kw):
        import soundfile as sf
        self.call_kw = kw
        if kw.get("audio"):
            self.audio_data, self.audio_sr = sf.read(kw["audio"][0], dtype="int16")
        self.inputs = _Inputs(7)
        return self.inputs

    def batch_decode(self, gen, **kw):
        self.decoded, self.decode_kw = gen, kw
        return [self.reply]


class _Model:
    device = "cuda"
    dtype = "float16"

    def __init__(self):
        self.gen_kw = None
        self.ids = _GenIds()

    def generate(self, **kw):
        self.gen_kw = kw
        return self.ids


@pytest.fixture
def fake_torch(monkeypatch):
    """torch (и вместе с ним GPU) в тестах не нужен: нужен только inference_mode."""
    class _Inference:
        def __enter__(self):
            return None

        def __exit__(self, *exc):
            return False

    mod = types.ModuleType("torch")
    mod.inference_mode = lambda: _Inference()
    monkeypatch.setitem(sys.modules, "torch", mod)
    return mod


@pytest.fixture
def mm_info(monkeypatch):
    """Заглушка `qwen_omni_utils.process_mm_info`: отдаёт путь к аудио из разговора."""
    seen = {}

    def process_mm_info(conv, **kw):
        seen["conv"], seen["kw"] = conv, kw
        return [conv[1]["content"][0]["audio"]], [], []

    mod = types.ModuleType("qwen_omni_utils")
    mod.process_mm_info = process_mm_info
    monkeypatch.setitem(sys.modules, "qwen_omni_utils", mod)
    return seen


def test_transcribe_clip_декодирует_только_новые_токены(fake_torch, mm_info, tmp_tempdir):
    """Срез по длине промпта: без него в расшифровку уехали бы ещё и системная
    инструкция с разговором."""
    proc, model = _Proc(), _Model()
    clip = _clip(0.1)

    assert omni_asr.transcribe_clip(proc, model, clip) == "расшифровка"

    assert proc.decoded == "GENERATED"
    assert model.ids.sliced == (slice(None), slice(7, None))
    assert model.gen_kw["return_audio"] is False
    assert model.gen_kw["max_new_tokens"] == 420
    assert list(proc.inputs.moves) == ["cuda", "float16"]
    assert proc.decode_kw == {"skip_special_tokens": True,
                              "clean_up_tokenization_spaces": False}


def test_transcribe_clip_кладёт_звук_и_системный_промпт_в_разговор(fake_torch, mm_info,
                                                                   tmp_tempdir):
    """Модели уходит файл с ТЕМ ЖЕ звуком (16 кГц, PCM16) и инструкция «дословно»:
    иначе повторы и оговорки, ради которых всё и делается, потеряются."""
    proc, model = _Proc(), _Model()
    clip = _clip(0.1)

    omni_asr.transcribe_clip(proc, model, clip)

    assert proc.conv[0]["content"][0]["text"] == omni_asr.SYS
    assert proc.conv[1]["role"] == "user"
    audio = proc.conv[1]["content"][0]["audio"]
    assert os.path.dirname(audio) == str(tmp_tempdir)
    assert os.path.basename(audio).startswith("_omni_clip_")
    assert proc.audio_sr == omni_asr.SR
    assert list(proc.audio_data) == list(clip)
    assert mm_info["kw"] == {"use_audio_in_video": False}
    assert proc.call_kw["padding"] is True
    assert proc.call_kw["audio"] == [audio]
    assert proc.template_kw == {"add_generation_prompt": True, "tokenize": False}


def test_transcribe_clip_убирает_временный_wav(fake_torch, mm_info, tmp_tempdir):
    """Временный wav Qwen-пути убирается за собой в finally (как у gigaam-пути)."""
    omni_asr.transcribe_clip(_Proc(), _Model(), _clip(0.1))
    assert list(tmp_tempdir.glob("_omni_clip_*.wav")) == []


# --------------------------------------------------------------------------- #
# transcribe_clip_gigaam: локальный GigaAM
# --------------------------------------------------------------------------- #
class _Res:
    """TranscriptionResult: текст отдаётся через `.text`."""

    def __init__(self, text):
        self.text = text


class _GigaAM:
    """Заглушка GigaAM: помнит, каким методом её позвали и что ей дали на вход."""

    def __init__(self, res=None, fail=None):
        self.res, self.fail, self.calls = res, fail, []

    def _run(self, method, path):
        import soundfile as sf
        data, sr = sf.read(path, dtype="int16")     # модель читает файл, пока он есть
        self.calls.append({"method": method, "path": path, "sr": sr, "data": data})
        if self.fail is not None:
            raise self.fail
        return self.res

    def transcribe(self, path):
        return self._run("transcribe", path)

    def transcribe_longform(self, path):
        return self._run("transcribe_longform", path)


def test_gigaam_короткий_чанк_идёт_в_transcribe(tmp_tempdir):
    model = _GigaAM(_Res("короткая расшифровка"))
    clip = _clip(0.5)

    assert omni_asr.transcribe_clip_gigaam(model, clip) == "короткая расшифровка"

    call, = model.calls
    assert call["method"] == "transcribe"
    assert call["sr"] == omni_asr.SR
    assert list(call["data"]) == list(clip)
    assert list(tmp_tempdir.glob("_omni_gigaam_*.wav")) == []   # за собой убрано


def test_gigaam_ровно_25с_ещё_transcribe():
    """Порог строгий (`>`): ровно 25с — короткий чанк. Длиннее — longform, иначе
    GigaAM бросает ValueError «Too long wav file» и чанк теряется."""
    model = _GigaAM(_Res("ок"))
    omni_asr.transcribe_clip_gigaam(model, _clip_samples(omni_asr.SR * 25))
    assert [c["method"] for c in model.calls] == ["transcribe"]


def test_gigaam_длинный_чанк_идёт_в_longform():
    model = _GigaAM(_Res("ок"))
    omni_asr.transcribe_clip_gigaam(model, _clip_samples(omni_asr.SR * 25 + 1))
    assert [c["method"] for c in model.calls] == ["transcribe_longform"]


def test_gigaam_результат_без_атрибута_text():
    """Модель отдала просто строку — берём её, а не падаем на `.text`."""
    model = _GigaAM("просто строка")
    assert omni_asr.transcribe_clip_gigaam(model, _clip(0.1)) == "просто строка"


def test_gigaam_ошибка_инференса_не_оставляет_временный_wav(tmp_tempdir):
    """Падение модели не должно копить временные wav: файл убирается в finally."""
    model = _GigaAM(fail=RuntimeError("CUDA out of memory"))

    with pytest.raises(RuntimeError, match="CUDA out of memory"):
        omni_asr.transcribe_clip_gigaam(model, _clip(0.1))

    assert list(tmp_tempdir.glob("_omni_gigaam_*.wav")) == []


def test_gigaam_ошибка_longform_тоже_убирает_файл(tmp_tempdir):
    model = _GigaAM(fail=ValueError("Too long wav file"))

    with pytest.raises(ValueError, match="Too long wav file"):
        omni_asr.transcribe_clip_gigaam(model, _clip_samples(omni_asr.SR * 25 + 1))

    assert list(tmp_tempdir.glob("_omni_gigaam_*.wav")) == []
