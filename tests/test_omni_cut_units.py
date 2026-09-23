# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты решающих функций `core/omni_cut.py`, которых не было ни в одном тесте.

Перечень собран механически (def-ы модуля против упоминаний в tests/): 17 функций
из 26 не упоминались. Здесь те, что ПРИНИМАЮТ РЕШЕНИЕ или пишут файл — от них
зависит, что останется в ролике, и ошибка тут дороже всего:

* `halluc_drop` + `_load_halluc` + `_learn_halluc` — три слоя отбраковки
  галлюцинаций Omni и самообучение словарика (`halluc_phrases.json`);
* `is_nonspeech` / `_defective` — не-речь и NG-пересъёмка (обходят гард длинных);
* `voiced_ratio` — акустика (кашель/вздох без голосового тона);
* `_snap_silence` — можно ли резать в этой точке (нет тишины рядом — не режем);
* `_pair_dup` / `_tail_retake` / `_dup_of_neighbor` — дубль соседа или перезаход хвоста;
* `_tail_cut_by_words` — где именно в конце длинного интервала начинается старый хвост;
* `_count_key` — сколько раз фраза прозвучала в склейке (речек резов);
* `_full_pass` — кэш/запись `.full.json` (окна сплошного анализа);
* `_free_vram_for_render` — выгрузка моделей перед NVENC-рендером.

Личные файлы не трогаются: `halluc_phrases.json` подменяется на tmp_path (autouse —
иначе самообучение писало бы в боевой файл), `%TEMP%`, сеть и модели не вызываются,
`transcribe`/`subprocess.Popen`/`aicut` подменены заглушками.

Запуск:  python -m pytest tests/test_omni_cut_units.py -q
"""
import json
import os
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import omni_cut  # noqa: E402


@pytest.fixture(autouse=True)
def halluc_path(tmp_path, monkeypatch):
    """Словарик галлюцинаций — в tmp_path: боевой `halluc_phrases.json` личный
    (в .gitignore), и самообучение `_learn_halluc` писало бы прямо в него."""
    p = tmp_path / "halluc_phrases.json"
    monkeypatch.setattr(omni_cut, "HALLUC_PHRASES_PATH", str(p))
    return p


@pytest.fixture
def emitted():
    """Сборщик строк emit: `emit(шаблон, **переменные)` — как console_emit.

    Сигнатура `*args, **vars`: omni_cut зовёт `emit("  {line}", line=…)` — с
    позиционным шаблоном И именем `line` в переменных, и жёсткий параметр `line`
    здесь был бы конфликтом имён.
    """
    lines = []

    def _emit(*args, **vars):
        lines.append(((args[0] if args else vars.pop("line", "")), vars))

    _emit.lines = lines
    return _emit


def _iv(start, end, text):
    return {"start": float(start), "end": float(end), "text": text}


# --------------------------------------------------------------------------- #
# Словарик галлюцинаций: _load_halluc / _learn_halluc
# --------------------------------------------------------------------------- #
def test_load_halluc_без_файла_отдаёт_сид(halluc_path):
    assert not halluc_path.exists()
    assert omni_cut._load_halluc() == set(omni_cut.HALLUC_SEED)


def test_load_halluc_добавляет_выученное_к_сиду(halluc_path):
    halluc_path.write_text(json.dumps(["моя фраза"]), encoding="utf-8")
    loaded = omni_cut._load_halluc()
    assert "моя фраза" in loaded
    assert set(omni_cut.HALLUC_SEED) <= loaded


def test_load_halluc_битый_файл_не_роняет(halluc_path):
    """Обрезанный JSON (крах при записи) — работаем на сиде, а не падаем."""
    halluc_path.write_text('["моя фра', encoding="utf-8")
    assert omni_cut._load_halluc() == set(omni_cut.HALLUC_SEED)


def test_learn_halluc_пишет_только_новое(halluc_path):
    """Фразы сида в файл не едут (они и так всегда в словаре), новая — едет."""
    omni_cut._learn_halluc([])
    assert not halluc_path.exists(), "пустой список создал файл"

    omni_cut._learn_halluc(["это что за шум"])          # фраза сида
    assert not halluc_path.exists(), "фраза сида записана в личный файл"

    omni_cut._learn_halluc(["новая фраза"])
    assert json.loads(halluc_path.read_text(encoding="utf-8")) == ["новая фраза"]


def test_learn_halluc_дописывает_к_прежним(halluc_path):
    halluc_path.write_text(json.dumps(["первая"]), encoding="utf-8")
    omni_cut._learn_halluc(["вторая"])
    assert json.loads(halluc_path.read_text(encoding="utf-8")) == ["вторая", "первая"]
    assert {"вторая", "первая"} <= omni_cut._load_halluc()


# --------------------------------------------------------------------------- #
# halluc_drop: три слоя отбраковки галлюцинаций
# --------------------------------------------------------------------------- #
def test_halluc_drop_повтор_шаблона_учит_фразу(halluc_path, emitted):
    """Одинаковый длинный огрызок на ДВУХ коротких интервалах — шаблон чат-приоров:
    оба в drop, фраза уходит в словарик. Длинный интервал (>=3с) не трогаем."""
    texts = [_iv(0, 2, "Это что за шум опять"),
             _iv(5, 7, "Это что за шум опять"),
             _iv(9, 15, "Это что за шум опять")]

    drops = omni_cut.halluc_drop(texts, emit=emitted)

    assert drops == {0, 1}
    assert json.loads(halluc_path.read_text(encoding="utf-8")) == ["это что за шум опять"]
    assert [v["why"] for _l, v in emitted.lines] == ["повтор-шаблон", "повтор-шаблон"]


def test_halluc_drop_короткий_повтор_не_шаблон(halluc_path, emitted):
    """«Ага» на двух интервалах — не шаблон: у слоя 2 порог 8 символов, иначе под
    нож шли бы обычные короткие слова живой речи."""
    drops = omni_cut.halluc_drop([_iv(0, 1, "Ага"), _iv(2, 3, "Ага")], emit=emitted)
    assert drops == set()
    assert not halluc_path.exists()


def test_halluc_drop_словарик_ловит_одиночную_фразу(halluc_path, emitted):
    """Один короткий интервал с известной фразой-галлюцинацией — drop без обучения."""
    drops = omni_cut.halluc_drop([_iv(0, 1.5, "Продолжение следует")], emit=emitted)
    assert drops == {0}
    assert [v["why"] for _l, v in emitted.lines] == ["известная галлюцинация"]
    assert not halluc_path.exists()


def test_halluc_drop_акустика_только_для_коротких(halluc_path, emitted):
    """Интервал без голосового тона (кашель) — drop; длинный интервал акустика не
    трогает: там всегда есть речь, и один провал тона её не отменяет."""
    texts = [_iv(0, 1.5, "Кхм"), _iv(4, 12, "Длинная осмысленная фраза про монтаж")]
    drops = omni_cut.halluc_drop(texts, unvoiced={0, 1}, emit=emitted)
    assert drops == {0}
    assert [v["why"] for _l, v in emitted.lines] == ["нет голосового тона (кашель/вздох)"]


def test_halluc_drop_пустой_текст_не_попадает_в_словарик(halluc_path, emitted):
    """Пустой/пунктуация-только текст не нормализуется в фразу — учить нечего."""
    drops = omni_cut.halluc_drop([_iv(0, 1, "…"), _iv(2, 3, "—")], emit=emitted)
    assert drops == set()
    assert not halluc_path.exists()


# --------------------------------------------------------------------------- #
# is_nonspeech: не-речь без всякой модели
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,dur", [
    ("", None), ("   ", None),
    ("[смех]", None), ("(вздох)", 1.0), ("<noise>", None),
    ("А", None), ("Ааааа", None),
    ("Ох. Эх", 1.0),                       # короткий интервал целиком из филлеров
    ("Ну вот", 1.0),                       # то же, но длиннее четырёх букв
    ("кто", 5.0),                          # огрызок: <=4 букв при известной длительности
    ("Это.", 1.0),                         # вздох, на который Omni налепила огрызок
    ("Это что за шум?", 0.4),              # 37 букв/с — физически не речь
    ("Извините, но я не могу продолжать этот разговор", None),   # боилерплейт отказа
    ("извините я не могу выполнить эту просьбу", None),
])
def test_is_nonspeech_ловит_мусор(text, dur):
    assert omni_cut.is_nonspeech(text, dur) is True


@pytest.mark.parametrize("text,dur", [
    ("Ну вот", 3.0),                       # длинный интервал из филлеров — уже речь
    ("кто", None),                         # без длительности правило огрызка не работает
    ("кхе а вот здесь мы продолжаем говорить", 3.0),   # одно «кхе» не убивает фразу
    ("Это нормальная длинная фраза про монтаж видео", 3.0),
    ("давайте сначала разберёмся с этим вопросом", 3.0),
    ("не то чтобы я был против", 3.0),
])
def test_is_nonspeech_не_трогает_живую_речь(text, dur):
    assert omni_cut.is_nonspeech(text, dur) is False


# --------------------------------------------------------------------------- #
# _defective: брак, который можно выкинуть даже длинным
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", [
    "Стоп, давай заново", "давайте по новой", "блин, забыл", "стоп стоп",
    "стоп, давай", "снято, заново", "Извините, но я не могу продолжать",
])
def test_defective_ловит_ng_маркеры(text):
    assert omni_cut._defective(_iv(0, 8, text)) is True


@pytest.mark.parametrize("text", [
    "давайте сначала разберёмся с этим вопросом",
    "это не то чтобы важно, но послушай",
    "заново я бы это не делал",
    "мы продолжим этот разговор позже",
])
def test_defective_не_ловит_обычные_слова(text):
    """Одиночные «заново»/«сначала»/«не то» — живая речь: на них гард длинных
    интервалов снимать нельзя."""
    assert omni_cut._defective(_iv(0, 8, text)) is False


def test_defective_не_речь_тоже_брак():
    assert omni_cut._defective(_iv(0, 8, "[смех]")) is True


# --------------------------------------------------------------------------- #
# voiced_ratio: акустика (нужен numpy)
# --------------------------------------------------------------------------- #
def test_voiced_ratio_тишина_и_тон():
    np = pytest.importorskip("numpy")
    sr = 16000
    silence = np.zeros(sr, dtype="int16")
    assert omni_cut.voiced_ratio(silence, sr) == 0.0

    short = np.ones(200, dtype="int16")               # короче кадра (40мс)
    assert omni_cut.voiced_ratio(short, sr) == 0.0

    t = np.arange(sr, dtype="float32") / sr
    tone = (3000 * np.sin(2 * np.pi * 120 * t)).astype("int16")   # 120 Гц — в диапазоне F0
    ratio = omni_cut.voiced_ratio(tone, sr)
    assert ratio > 0.5, f"тон 120 Гц не распознан как голосовой: {ratio}"

    # шум без периодичности — не голос
    rng = np.random.default_rng(0)
    noise = (rng.normal(0, 3000, sr)).astype("int16")
    assert omni_cut.voiced_ratio(noise, sr) < 0.5


# --------------------------------------------------------------------------- #
# _snap_silence: можно ли резать в этой точке
# --------------------------------------------------------------------------- #
def _env_with_dip(dip_at=0.5):
    np = pytest.importorskip("numpy")
    t = np.arange(0, 1.0, 0.01)
    rms = np.ones_like(t)
    rms[np.searchsorted(t, dip_at)] = 0.01
    return t, rms


def test_snap_silence_находит_локальный_минимум():
    t, rms = _env_with_dip(0.5)
    got = omni_cut._snap_silence(t, rms, 0.52, win=0.4)
    assert got == pytest.approx(0.5)


def test_snap_silence_без_тишины_не_режет():
    """Ровный громкий участок — резать негде: None, а не «ближайший кадр»."""
    np = pytest.importorskip("numpy")
    t = np.arange(0, 1.0, 0.01)
    rms = np.ones_like(t)
    assert omni_cut._snap_silence(t, rms, 0.5, win=0.4) is None


def test_snap_silence_вне_окна_и_нулевая_огибающая():
    """Окно за пределами аудио и полностью нулевая огибающая — тоже None."""
    np = pytest.importorskip("numpy")
    t = np.arange(0, 1.0, 0.01)
    rms = np.ones_like(t)
    assert omni_cut._snap_silence(t, rms, 100.0, win=0.4) is None

    zeros = np.zeros_like(t)
    assert omni_cut._snap_silence(t, zeros, 0.5, win=0.4) is None


# --------------------------------------------------------------------------- #
# _pair_dup / _tail_retake / _dup_of_neighbor: дубль или перезаход хвоста
# --------------------------------------------------------------------------- #
def test_pair_dup_ловит_дубль_и_подмножество():
    assert omni_cut._pair_dup("раз два три четыре", "раз два три четыре") is True
    assert omni_cut._pair_dup("раз два", "раз два три четыре пять") is True   # подмножество
    assert omni_cut._pair_dup("кот сидел на окне", "пёс бежал по двору") is False
    assert omni_cut._pair_dup("", "раз два три") is False
    assert omni_cut._pair_dup("раз два три", "") is False


def test_pair_dup_слабого_пересечения_мало():
    """Общие два слова из семи — не дубль (иначе уникальные интервалы гибнут)."""
    assert omni_cut._pair_dup("раз два три четыре пять шесть семь",
                              "раз два восемь девять") is False


def test_tail_retake_ловит_только_короткий_хвост():
    long_text = "хотите расти в зале и вот это то что вам нужно"
    assert omni_cut._tail_retake(long_text, "это то что вам нужно") is True
    # равные по размеру пересъёмки — это дубль пары, а не перезаход хвоста
    assert omni_cut._tail_retake("раз два три четыре", "раз два три четыре") is False
    assert omni_cut._tail_retake("раз два три", "") is False
    # короткий хвост, которого в длинном тексте нет, — тоже не перезаход
    assert omni_cut._tail_retake(long_text, "пёс бежал по двору") is False


def test_dup_of_neighbor_пустой_текст_считается_дублем():
    texts = [_iv(0, 3, "осмысленная фраза про монтаж"), _iv(4, 6, "   ")]
    assert omni_cut._dup_of_neighbor(texts, 1) is True


def test_dup_of_neighbor_ищет_в_окне_span():
    texts = [_iv(0, 3, "раз два три четыре пять"),
             _iv(3, 6, "совсем другой текст здесь"),
             _iv(6, 9, "раз два три четыре пять")]
    assert omni_cut._dup_of_neighbor(texts, 2, span=4) is True
    assert omni_cut._dup_of_neighbor(texts, 2, span=1) is False   # сосед вне окна


def test_dup_of_neighbor_перезаход_хвоста_не_дубль():
    """Длинный интервал с контентом и короткий перезаход его хвоста: длинный
    дублем НЕ считается (иначе вступление гибнет из-за пересъёма хвоста)."""
    texts = [_iv(0, 8, "хотите расти в зале и вот это то что вам нужно"),
             _iv(9, 11, "это то что вам нужно")]
    assert omni_cut._dup_of_neighbor(texts, 0, span=4) is False
    # а сам короткий перезаход — дубль: он и должен уйти
    assert omni_cut._dup_of_neighbor(texts, 1, span=4) is True


# --------------------------------------------------------------------------- #
# _tail_cut_by_words: где в конце длинного интервала начинается старый хвост
# --------------------------------------------------------------------------- #
@pytest.fixture
def fake_transcribe(monkeypatch):
    """Заглушка пословных таймингов: Whisper в тестах не запускается."""
    from core import transcribe
    calls = {}

    def _fake(wav_path, intervals=None, **kw):
        calls["wav_path"] = wav_path
        calls["intervals"] = intervals
        return [dict(w) for w in calls["words"]]

    monkeypatch.setattr(transcribe, "transcribe_segments", _fake)
    return calls


TAIL_TEXT = "раз два три четыре"


def test_tail_cut_by_words_берёт_последнее_вхождение(emitted, fake_transcribe):
    """Два вхождения хвоста в интервале — режем по ПОСЛЕДНЕМУ (раннее может быть
    частью живой фразы), с отступом 60мс от начала слова."""
    fake_transcribe["words"] = [
        {"w": "раз", "start": 10.6, "end": 10.8}, {"w": "два", "start": 10.8, "end": 11.0},
        {"w": "три", "start": 11.0, "end": 11.2}, {"w": "и", "start": 11.2, "end": 11.3},
        {"w": "ещё", "start": 11.3, "end": 11.6}, {"w": "слово", "start": 11.6, "end": 12.0},
        {"w": "раз", "start": 16.0, "end": 16.2}, {"w": "два", "start": 16.2, "end": 16.4},
        {"w": "три", "start": 16.4, "end": 16.6}, {"w": "четыре", "start": 16.6, "end": 17.0},
    ]

    got = omni_cut._tail_cut_by_words("dummy.wav", (10.0, 20.0), TAIL_TEXT, emit=emitted)

    assert got == (pytest.approx(15.94), 20.0)
    # окно распознавания — конец интервала, а не весь интервал
    (win0, win_e), = fake_transcribe["intervals"]
    assert win_e == 20.0 and win0 > 10.0
    assert emitted.lines == []


def test_tail_cut_by_words_короткий_хвост_не_режем(emitted, fake_transcribe):
    """Меньше трёх слов в хвосте — по чему резать, непонятно: None без распознавания."""
    fake_transcribe["words"] = []
    assert omni_cut._tail_cut_by_words("dummy.wav", (0.0, 10.0), "два слова", emit=emitted) is None
    assert fake_transcribe.get("intervals") is None, "Whisper запущен на огрызке хвоста"


def test_tail_cut_by_words_хвост_не_найден(emitted, fake_transcribe):
    """Whisper не расслышал хвост — None и строка в лог (наверху будет фолбэк)."""
    fake_transcribe["words"] = [{"w": "совсем", "start": 12.0, "end": 12.4},
                                {"w": "другое", "start": 12.4, "end": 12.8}]
    assert omni_cut._tail_cut_by_words("dummy.wav", (10.0, 20.0), TAIL_TEXT, emit=emitted) is None
    assert "не нашёл" in emitted.lines[0][0]


@pytest.mark.parametrize("start", [10.2, 19.95])
def test_tail_cut_by_words_у_края_не_верим(emitted, fake_transcribe, start):
    """Совпадение в самом начале интервала или у самого его конца — не верим:
    срез короче 0.2с или без запаса смысла только рвёт фразу."""
    fake_transcribe["words"] = [{"w": "раз", "start": start, "end": start + 0.2},
                                {"w": "два", "start": start + 0.2, "end": start + 0.4},
                                {"w": "три", "start": start + 0.4, "end": start + 0.6}]
    assert omni_cut._tail_cut_by_words("dummy.wav", (10.0, 20.0), TAIL_TEXT, emit=emitted) is None


# --------------------------------------------------------------------------- #
# _joint_snippet: какой звук вокруг стыка уходит на речек
# --------------------------------------------------------------------------- #
def _ramp(seconds=6, sr=16000):
    """Аудио-«линейка»: по значению сэмпла видно, из какого места куска он взят."""
    np = pytest.importorskip("numpy")
    return np.arange(int(seconds * sr), dtype="int16")


def test_joint_snippet_берёт_хвост_до_и_голову_после():
    """Ровно span секунд слева от стыка и span справа — тот же звук, что уйдёт в
    черновик: по нему речек и проверяет, уцелел ли старый хвост. Слева берётся
    ХВОСТ куска (последние 2с из трёх), справа — голова следующего."""
    sr = 16000
    a16 = _ramp(8, sr)

    snip = omni_cut._joint_snippet(a16, [(0.0, 3.0), (4.0, 7.0)], 4.0, span=2.0, sr=sr)

    assert list(snip) == list(a16[1 * sr:3 * sr]) + list(a16[4 * sr:6 * sr])


def test_joint_snippet_правую_часть_режет_по_стыку():
    """Правый кусок начинается ДО стыка — берём только то, что после него (иначе в
    речек попадал бы уже вырезанный звук)."""
    sr = 16000
    a16 = _ramp(6, sr)

    snip = omni_cut._joint_snippet(a16, [(4.0, 6.0)], 4.5, span=1.0, sr=sr)

    assert list(snip) == list(a16[int(4.5 * sr):int(5.5 * sr)])


def test_joint_snippet_без_кусков_ничего():
    """Резать нечего (keep пуст) — None, а не пустой массив: речек такие стыки
    пропускает, а не «слышит тишину»."""
    assert omni_cut._joint_snippet(_ramp(2), [], 1.0, sr=16000) is None


# --------------------------------------------------------------------------- #
# _count_key: сколько раз фраза прозвучала (речек склейки)
# --------------------------------------------------------------------------- #
def test_count_key_считает_вхождения():
    assert omni_cut._count_key("раз два три и ещё раз два три", ["раз", "два", "три"]) == 2
    assert omni_cut._count_key("совсем другой текст", ["раз", "два", "три"]) == 0
    assert omni_cut._count_key("раз два три", []) == 0


# --------------------------------------------------------------------------- #
# _full_pass: окна сплошного анализа и кэш .full.json
# --------------------------------------------------------------------------- #
class _FakeProc:
    """Подменённый `omni_asr`: строки в stdout и код возврата, без процесса."""

    def __init__(self, lines):
        self.stdout = iter(lines)

    def wait(self):
        return 0


def _make_wav(path, seconds=3.0, sr=16000):
    np = pytest.importorskip("numpy")
    import soundfile as sf
    sf.write(str(path), np.zeros(int(seconds * sr), dtype="float32"), sr)


def test_full_pass_пишет_окна_и_отдаёт_разбор(tmp_path, monkeypatch, emitted):
    """Свежий прогон: окна по full_window уходят в omni_asr через iv.json, строки
    его прогресса — в emit, результат отдаётся разбором."""
    import subprocess
    wav = tmp_path / "a1.wav"
    _make_wav(wav, 65.0)
    work = tmp_path / "work"
    work.mkdir()
    out = tmp_path / "out.xml"
    a = types.SimpleNamespace(out=str(out), full_window=30.0)
    captured = {}

    def fake_popen(cmd, **kw):
        captured["cmd"] = cmd
        captured["kwargs"] = kw
        dst = cmd[cmd.index("--out") + 1]
        json.dump([{"start": 0, "end": 30, "text": "речь"}], open(dst, "w", encoding="utf-8"))
        return _FakeProc(["окно 1\n", "\n", "окно 2\n"])

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    res = omni_cut._full_pass(str(wav), a, str(work), emit=emitted)

    assert res == [{"start": 0, "end": 30, "text": "речь"}]
    assert (tmp_path / "out.full.json").is_file()
    iv = json.load(open(os.path.join(str(work), "iv.json"), encoding="utf-8"))
    assert iv == [[0.0, 30.0], [30.0, 60.0], [60.0, 65.0]]   # сплошные окна по 30с
    assert str(wav) in captured["cmd"] and "core.omni_asr" in captured["cmd"]
    assert [line for line, _v in emitted.lines if line.strip()] == \
        ["  full-анализ: {count} окон по {step:.0f}с (тишина не вырезается)",
         "  {line}", "  {line}"]
    assert emitted.lines[1][1] == {"line": "окно 1"}


def test_full_pass_берёт_готовый_кэш(tmp_path, monkeypatch, emitted):
    """`.full.json` уже есть — повторный анализ не запускается (и это видно в логе)."""
    import subprocess
    wav = tmp_path / "a1.wav"
    _make_wav(wav, 3.0)
    out = tmp_path / "out.xml"
    (tmp_path / "out.full.json").write_text(json.dumps([{"start": 0, "end": 3, "text": "кэш"}]),
                                            encoding="utf-8")
    a = types.SimpleNamespace(out=str(out), full_window=30.0)

    def boom(*a_, **k):
        raise AssertionError("анализ запущен при готовом кэше")

    monkeypatch.setattr(subprocess, "Popen", boom)

    res = omni_cut._full_pass(str(wav), a, str(tmp_path), emit=emitted)
    assert res == [{"start": 0, "end": 3, "text": "кэш"}]
    assert "full-анализ из кэша" in emitted.lines[0][0]


def test_full_pass_сбой_не_роняет_нарезку(tmp_path, monkeypatch, emitted):
    """omni_asr не оставил результат — возвращаем None и говорим об этом: нарезка
    продолжается без канвы смысла, а не падает."""
    import subprocess
    wav = tmp_path / "a1.wav"
    _make_wav(wav, 3.0)
    a = types.SimpleNamespace(out=str(tmp_path / "out.xml"), full_window=30.0)
    monkeypatch.setattr(subprocess, "Popen", lambda *a_, **k: _FakeProc([]))

    assert omni_cut._full_pass(str(wav), a, str(tmp_path), emit=emitted) is None
    assert "не удался" in emitted.lines[-1][0]


# --------------------------------------------------------------------------- #
# _free_vram_for_render: перед NVENC выгружаем всё наше
# --------------------------------------------------------------------------- #
def test_free_vram_for_render_выгружает_модели(monkeypatch, emitted):
    from core import aicut, transcribe
    unloaded, released = [], []
    monkeypatch.setattr(aicut, "unload_ours", lambda *a, **k: unloaded.append(1))
    monkeypatch.setattr(transcribe, "release_model", lambda: (released.append(1), True)[1])

    omni_cut._free_vram_for_render(emit=emitted)

    assert unloaded == [1] and released == [1]
    assert [line for line, _v in emitted.lines] == ["  Whisper выгружен"]


def test_free_vram_for_render_терпит_сбой_whisper(monkeypatch, emitted):
    """Whisper уже выгружен self-check'ом — падение release_model не должно ломать
    подготовку к рендеру (наши модели LM Studio всё равно выгружаем)."""
    from core import aicut, transcribe
    unloaded = []
    monkeypatch.setattr(aicut, "unload_ours", lambda *a, **k: unloaded.append(1))

    def boom():
        raise RuntimeError("нет весов")

    monkeypatch.setattr(transcribe, "release_model", boom)

    omni_cut._free_vram_for_render(emit=emitted)      # не должно бросить

    assert unloaded == [1]
    assert emitted.lines == []
