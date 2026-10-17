# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Перечисления «1 — держите баланс солей»: номер крупно слева, суть пункта столбиком справа.

Владелец сам переделывал такие акценты: номер пункта + пара слов -> номер крупно (big, up), слова
пункта по одному на строку (right). Теперь это делает код: модель помечает пункт enum=true, а если
модель пункт пропустила — гарантия кодом (_place_mids). Служебный ключ enum из записи .intro.json
снимается. Лента в тестах строится так: слова одного пункта идут без пауз, между пунктами — пауза
(её считает _enum_span), иначе счёт слов после номера не воспроизводится.

Запуск: py -3.10 -m pytest tests/test_intro_enum.py -q
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core.aicut import commands  # noqa: E402


def _timed(*phrases):
    """Лента: слова фразы через 0.2 с (внутри пункта паузы нет), между фразами — пауза 0.8 с."""
    out, t = [], 0.0
    for ph in phrases:
        for w in ph.split():
            out.append((len(out), w, t, t + 0.15))
            t += 0.2
        t += 0.8
    return out


def _place(groups, words, busy=(), log=None, ends=None):
    """_place_mids с логом: строки подставляются как в console_emit (шаблон «{word}» раскрывается)."""
    def emit(msg="", /, **kw):
        if log is None:
            return
        try:
            log.append(str(msg).format(**kw))
        except Exception:
            log.append(str(msg))
    return commands._place_mids(groups, words, 0, busy, emit=emit, ends=ends)


def _group(mids, head):
    """Строки одной группы-акцента: от головной строки с from=head до следующей головы."""
    i = next(i for i, r in enumerate(mids) if r.get("break") and r.get("from") == head)
    j = i + 1
    while j < len(mids) and not mids[j].get("break"):
        j += 1
    return mids[i:j]


def _texts(rows, words, start):
    """Тексты строк группы по ленте слов: строка = count слов подряд, с позиции start."""
    out, k = [], start
    for r in rows:
        out.append(" ".join(w[1] for w in words[k:k + r["count"]]))
        k += r["count"]
    return out


# Типичная лента владельца: вступление и три пункта, между пунктами — паузы.
PHRASES = ("ПРИВЕТ", "1 ДЕРЖИТЕ БАЛАНС СОЛЕЙ", "2 ПОДКЛЮЧИТЕ ПЕПТИДЫ",
           "3 ИСПОЛЬЗУЙТЕ МИНИМАЛЬНЫЕ ДОЗЫ ДЕНЬ")
HEADS_IN_PHRASES = [1, 5, 8]


# ---- 1. номер пункта по слову ----

@pytest.mark.parametrize("word, want", [
    ("1", 1), ("2.", 2), ("2)", 2), ("1,", 1), ("20", 20),
    ("ПЕРВОЕ", 1), ("второе", 2), ("ТРЕТЬЕ", 3), ("ЧЕТВЕРТОЕ", 4), ("ЧЕТВЁРТОЕ", 4),
    ("ПЯТОЕ", 5), ("ШЕСТОЕ", 6), ("СЕДЬМОЕ", 7),
    ("ПЕРВЫЙ", 1), ("ВТОРОЙ", 2), ("ТРЕТИЙ", 3),
    ("ПЕРВАЯ", 1), ("ВТОРАЯ", 2), ("ТРЕТЬЯ", 3),
    ("во-первых", 1), ("ВО-ВТОРЫХ", 2), ("в-третьих", 3), ("В-ЧЕТВЕРТЫХ", 4),
])
def test_enum_number_распознаёт_номера(word, want):
    assert commands._enum_number(word) == want


@pytest.mark.parametrize("word", ["48", "0", "21", "ДВА", "РАЗ", "ЗА", "ДЕРЖИТЕ"])
def test_enum_number_чужое_не_номер(word):
    """48 — не пункт, «два» и «раз» словами — тоже (только цифры и порядковые из списка)."""
    assert commands._enum_number(word) is None


# ---- 2. какие номера образуют пункты ----

def test_enum_heads_три_пункта_по_порядку():
    """Номера 1, 2, 3 по возрастанию через 1 -> три головы ленты."""
    words = _timed(*PHRASES)
    assert commands._enum_heads(words, 0) == HEADS_IN_PHRASES


def test_enum_heads_одиночная_цифра_не_пункт():
    """«за 48 часов» и «2 раза» — не перечисление: одна цифра без продолжения."""
    words = _timed("ЗА 48 ЧАСОВ ВЫ ПОЛУЧИТЕ", "ОНИ 2 РАЗА В ДЕНЬ")
    assert commands._enum_heads(words, 0) == []


def test_enum_heads_не_по_возрастанию_не_пункты():
    """«1» и снова «1» — не последовательность: ни одна цифра пунктом не становится."""
    words = _timed("1 ДЕЛАЙТЕ", "ЧТО-ТО ЕЩЁ", "1 СНОВА")
    assert commands._enum_heads(words, 0) == []


def test_enum_heads_с_двойки():
    """Последовательность может начинаться не с 1: «2, 3» — тоже перечисление."""
    words = _timed("ПРИВЕТ", "2 ПОДКЛЮЧИТЕ ПЕПТИДЫ", "3 ИСПОЛЬЗУЙТЕ ДОЗЫ")
    assert commands._enum_heads(words, 0) == [1, 4]


# ---- 3. сколько слов в пункте после номера ----

def test_enum_span_до_паузы_или_следующего_пункта():
    """«1 ДЕРЖИТЕ БАЛАНС СОЛЕЙ» — номер и три слова; дальше идёт следующий пункт."""
    words = _timed(*PHRASES)
    assert commands._enum_span(1, words, set(HEADS_IN_PHRASES)) == 4


def test_enum_span_не_больше_четырёх_слов_после_номера():
    """«3 ИСПОЛЬЗУЙТЕ МИНИМАЛЬНЫЕ ДОЗЫ ДЕНЬ» — пять слов с номером, не больше."""
    words = _timed(*PHRASES)
    assert commands._enum_span(8, words, set(HEADS_IN_PHRASES)) == 5


def test_enum_span_обрывается_на_паузе():
    """Пауза ≥ 0.35 с перед словом кончает пункт; первое слово после номера берём всегда."""
    words = [(0, "2", 0.0, 0.15), (1, "ПОДКЛЮЧИТЕ", 0.2, 0.35), (2, "ПЕПТИДЫ", 1.5, 1.65)]
    assert commands._enum_span(0, words, {0}) == 2


# ---- 4. строки пункта ----

def test_enum_rows_номер_крупно_слова_столбиком():
    """Номер — big+up, каждое слово — своя строка right; служебное «И» склеивается со следующим."""
    words = _timed("3 КОНТРОЛЬ СНА И ЭСТРАДИОЛА")
    rows = commands._enum_rows(0, 5, words)
    assert _texts(rows, words, 0) == ["3", "КОНТРОЛЬ", "СНА", "И ЭСТРАДИОЛА"]
    head = rows[0]
    assert (head["big"], head["anim"], head["break"], head["from"]) == (True, "up", True, 0)
    assert all(r["anim"] == "right" and r["break"] is False and r["from"] is None for r in rows[1:])
    assert all(r["enum"] is True for r in rows)


# ---- 5. раскладка: модель, гарантия кодом, вставки ----

def test_place_mids_одиночная_двойка_становится_пунктом():
    """Модель прислала «2» одной цифрой (count 1, enum false): группа — «2 / ПОДКЛЮЧИТЕ / ПЕПТИДЫ»
    со строкой номера big+up и строками right."""
    words = _timed(*PHRASES)
    mids = _place([(5, 1, "white", False, False)], words)
    rows = _group(mids, 5)
    assert _texts(rows, words, 5) == ["2", "ПОДКЛЮЧИТЕ", "ПЕПТИДЫ"]
    assert rows[0]["big"] is True and rows[0]["anim"] == "up"
    assert all(r["anim"] == "right" for r in rows[1:])


def test_place_mids_пункт_от_модели_с_enum_и_числом_слов():
    """Модель прислала enum=true на «3 КОНТРОЛЬ СНА И ЭСТРАДИОЛА» (пять слов с номером) ->
    «3 / КОНТРОЛЬ / СНА / И ЭСТРАДИОЛА»."""
    words = _timed("ПРИВЕТ", "3 КОНТРОЛЬ СНА И ЭСТРАДИОЛА")
    mids = _place([(1, 5, "white", False, True)], words)
    rows = _group(mids, 1)
    assert _texts(rows, words, 1) == ["3", "КОНТРОЛЬ", "СНА", "И ЭСТРАДИОЛА"]


def test_place_mids_пункт_которого_модель_не_прислала_добавлен_кодом():
    """Модель не прислала ни одного пункта — все три ставит код."""
    words = _timed(*PHRASES)
    mids = _place([], words)
    assert [r["from"] for r in mids if r.get("big")] == HEADS_IN_PHRASES
    assert _texts(_group(mids, 8), words, 8) == [
        "3", "ИСПОЛЬЗУЙТЕ", "МИНИМАЛЬНЫЕ", "ДОЗЫ", "ДЕНЬ"]


def test_place_mids_обычный_акцент_на_времени_пункта_удаляется():
    """Обычный жёлтый акцент звучит внутри пункта «3» — пункт важнее, акцент уходит."""
    words = _timed(*PHRASES)
    log: list[str] = []
    mids = _place([(11, 1, "yellow", False, False)], words, log=log)
    assert not any(r.get("color") == "yellow" for r in mids)
    assert any("уступил месту пункта" in ln for ln in log), log
    assert 8 in [r["from"] for r in mids if r.get("big")]


def test_place_mids_пункт_под_вставкой_не_ставится():
    """Кадр занят вставкой на времени пункта «2» — пункт не ставим, в лог строка как у прочих."""
    words = _timed(*PHRASES)
    log: list[str] = []
    busy = [(words[5][2] - 0.05, words[7][3] + 0.05)]
    mids = _place([], words, busy=busy, log=log)
    assert 5 not in [r["from"] for r in mids if r.get("big")]
    assert [r["from"] for r in mids if r.get("big")] == [1, 8]
    assert any("акцент с «2»" in ln and "перекрыт вставкой" in ln for ln in log), log


# ---- 6. cmd_intro: оформление пункта и служебный ключ enum ----

def test_cmd_intro_оформляет_пункты_и_снимает_enum(tmp_path, monkeypatch):
    """В ответе модели: «2» одной цифрой и пункт «3» с enum=true; пункт «1» добавлен кодом.
    У строк номера big и up, у остальных right; ключа enum в .intro.json нет."""
    words = _timed(*PHRASES)
    monkeypatch.setattr(commands, "_words_from_xml", lambda _p: list(words))
    mid_groups = [
        {"from": 5, "count": 1, "color": "white", "back": False, "enum": False},
        {"from": 8, "count": 5, "color": "white", "back": False, "enum": True},
    ]
    monkeypatch.setattr(commands, "_ask_json",
                        lambda *a, **k: {"intro_rows": [], "mid_groups": mid_groups})
    res = commands.cmd_intro(str(tmp_path / "clip.xml"), emit=lambda *a, **k: None, inserts=[])
    mids = res["mid_groups"]
    assert [r["from"] for r in mids if r.get("big")] == HEADS_IN_PHRASES
    assert all("enum" not in r for r in mids)
    assert all(r["anim"] == "up" for r in mids if r.get("big"))
    assert all(r["anim"] == "right" for r in mids if not r.get("big") and r.get("from") is None)
    saved = json.loads((tmp_path / "clip.intro.json").read_text(encoding="utf-8"))
    assert all("enum" not in r for r in saved["mid_groups"])


# ---- 7. конец фразы из сайдкара: пункт кончается на точке, а не на паузе ----

OWNER_PHRASES = ("ПРИВЕТ ДРУЗЬЯ.", "1 ДЕРЖИТЕ БАЛАНС СОЛЕЙ. ПЕЙТЕ ВОДУ.",
                 "2 ТОЛЬКО ЦИТАТ. КОРОТКИЙ ТЕКСТ.", "3 КОНТРОЛЬ СНА И ЭСТРАДИОЛА. ЭТО ВАЖНО.")


def _with_sidecar(tmp_path, monkeypatch, *phrases):
    """Фразы со знаками — как в сайдкаре расшифровки; XML — те же слова без пунктуации.

    Тайминг общий, так что концы фраз сопоставляются по времени старта (как в проде)."""
    side = _timed(*phrases)
    plain = [(k, w.rstrip(".!?;»\"”)"), s, e) for k, w, s, e in side]
    monkeypatch.setattr(commands, "_words_from_xml", lambda _p: list(plain))
    data = [{"w": w, "start": s, "end": e} for _k, w, s, e in side]
    (tmp_path / "clip.words.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(tmp_path / "clip.xml"), plain


def test_четыре_примера_владельца_дают_его_группы(tmp_path, monkeypatch):
    """«1 ДЕРЖИТЕ БАЛАНС СОЛЕЙ.», «2 ТОЛЬКО ЦИТАТ.», «3 КОНТРОЛЬ СНА И ЭСТРАДИОЛА.» — пункт
    кончается на точке; «И» внутри пункта остаётся, в конце его не бывает."""
    xml, words = _with_sidecar(tmp_path, monkeypatch, *OWNER_PHRASES)
    ends = commands._sentence_ends(xml, words)
    mids = _place([], words, ends=ends)
    assert [r["from"] for r in mids if r.get("big")] == [2, 8, 13]
    assert _texts(_group(mids, 2), words, 2) == ["1", "ДЕРЖИТЕ", "БАЛАНС", "СОЛЕЙ"]
    assert _texts(_group(mids, 8), words, 8) == ["2", "ТОЛЬКО", "ЦИТАТ"]
    assert _texts(_group(mids, 13), words, 13) == ["3", "КОНТРОЛЬ", "СНА", "И ЭСТРАДИОЛА"]


def test_точка_входа_кончается_на_точке_а_не_на_паузе(tmp_path, monkeypatch):
    """«Точка входа. Не заходи…» — пункт «1 / ТОЧКА / ВХОДА», без «НЕ ЗАХОДИ»."""
    xml, words = _with_sidecar(tmp_path, monkeypatch,
                               "ПРИВЕТ.", "1 ТОЧКА ВХОДА. НЕ ЗАХОДИ СЮДА.", "2 ТОЛЬКО ЦИТАТ. КОРОТКИЙ.")
    ends = commands._sentence_ends(xml, words)
    mids = _place([], words, ends=ends)
    assert _texts(_group(mids, 1), words, 1) == ["1", "ТОЧКА", "ВХОДА"]
    assert _texts(_group(mids, 7), words, 7) == ["2", "ТОЛЬКО", "ЦИТАТ"]


def test_число_слов_от_модели_обрезается_точкой(tmp_path, monkeypatch):
    """Модель прислала пункт на шесть слов, а фраза кончилась на «солей.» — точка побеждает."""
    xml, words = _with_sidecar(tmp_path, monkeypatch, *OWNER_PHRASES)
    ends = commands._sentence_ends(xml, words)
    mids = _place([(2, 6, "white", False, True)], words, ends=ends)
    assert _texts(_group(mids, 2), words, 2) == ["1", "ДЕРЖИТЕ", "БАЛАНС", "СОЛЕЙ"]


def test_без_точек_пункт_четыре_слова_и_без_И_в_конце():
    """Без знаков конца фразы: номер и до четырёх слов, а служебное «И» в хвосте снимается.

    «2 ПОДКЛЮЧИТЕ ПЕПТИДЫ MOTS-C И SS-31» -> «2 / ПОДКЛЮЧИТЕ / ПЕПТИДЫ / MOTS-C» (четыре слова)."""
    words = _timed("1 ТЕСТ", "2 ПОДКЛЮЧИТЕ ПЕПТИДЫ MOTS-C И SS-31 ДЛЯ")
    mids = _place([], words)
    assert _texts(_group(mids, 2), words, 2) == ["2", "ПОДКЛЮЧИТЕ", "ПЕПТИДЫ", "MOTS-C"]


def test_sentence_ends_без_сайдкара_пусто(tmp_path):
    words = _timed("ПРИВЕТ ДРУЗЬЯ.")
    assert commands._sentence_ends(str(tmp_path / "clip.xml"), words) == set()


def test_sentence_ends_по_времени_с_допуском_и_кавычками(tmp_path):
    """Знак за закрывающей кавычкой — конец фразы; сдвиг старта больше 0.08 с — чужая запись."""
    words = [(0, "ПРИВЕТ", 0.0, 0.15), (1, "ДРУЗЬЯ", 0.2, 0.35),
             (2, "ТОЧКА", 1.0, 1.15), (3, "ВХОДА", 1.2, 1.35)]
    side = [{"w": "ПРИВЕТ", "start": 0.0, "end": 0.15},
            {"w": "ДРУЗЬЯ.»", "start": 0.25, "end": 0.35},
            {"w": "ТОЧКА;", "start": 1.3, "end": 1.4},
            {"w": "ВХОДА!", "start": 1.2, "end": 1.35}]
    (tmp_path / "clip.words.json").write_text(json.dumps(side, ensure_ascii=False), encoding="utf-8")
    assert commands._sentence_ends(str(tmp_path / "clip.xml"), words) == {1, 3}


def test_cmd_intro_берет_концы_фраз_из_сайдкара(tmp_path, monkeypatch):
    """cmd_intro сам читает сайдкар и передаёт концы фраз в раскладку пунктов."""
    xml, words = _with_sidecar(tmp_path, monkeypatch, *OWNER_PHRASES)
    monkeypatch.setattr(commands, "_ask_json", lambda *a, **k: {"intro_rows": [], "mid_groups": []})
    res = commands.cmd_intro(xml, emit=lambda *a, **k: None, inserts=[])
    mids = res["mid_groups"]
    assert _texts(_group(mids, 2), words, 2) == ["1", "ДЕРЖИТЕ", "БАЛАНС", "СОЛЕЙ"]
    assert _texts(_group(mids, 8), words, 8) == ["2", "ТОЛЬКО", "ЦИТАТ"]
    assert _texts(_group(mids, 13), words, 13) == ["3", "КОНТРОЛЬ", "СНА", "И ЭСТРАДИОЛА"]
