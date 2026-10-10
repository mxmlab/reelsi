# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""ИИ-интро, 2026-10-10: лимит строки хука берётся из стиля (метка <ROW_MAX> в промпте),
конец хука — на конце мысли (промпт), слово-призыв в конце ролика — последний акцент
(гарантия в коде, _place_call_word), и замер призыва на фикстуре (tools/intro_eval.py).

Баги, ради которых тесты: число 14 стояло в промпте, и ручка стиля intro_row_max до модели
не доходила; призыв «напишите мне слово «…»» уступал хвост акцентов или перекрывался
вставкой, и зритель не видел слово, которое должен написать.
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import aicut  # noqa: E402
from core.aicut import commands  # noqa: E402
from core.aicut.prompts import INTRO_SYSTEM  # noqa: E402
import tools.intro_eval as ev  # noqa: E402

TAIL = ["НАПИШИТЕ", "МНЕ", "СЛОВО", "«КОНСУЛЬТАЦИЯ»", "В", "ЛИЧНЫЕ", "СООБЩЕНИЯ."]


def _words(texts, step=0.5):
    """Лента (индекс, слово, начало, конец): слово каждые step секунд, длина 0.4 с."""
    return [(k, t, k * step, k * step + 0.4) for k, t in enumerate(texts)]


def _ролик(n=40, tail=TAIL):
    """n слов: в конце — фраза (по умолчанию с призывом), перед ней филлер СЛОВО<k>.
    Призыв «КОНСУЛЬТАЦИЯ» стоит на индексе 36 (t0 = 18.0 с), хвост ролика — с индекса 10."""
    head = [f"СЛОВО{k}" for k in range(n - len(tail))]
    return _words(head + tail)


def _mid(f, cnt=1, color="white", back=False):
    return {"from": f, "count": cnt, "color": color, "break": True, "back": back}


class Log(list):
    """Сборщик логов: emit('шаблон {word}', word=...) -> строка с подставленным словом."""

    def __call__(self, line="", **vars):
        self.append(line.format(**vars) if vars else line)


def _dump(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)


# ---- 1. лимит строки хука: из стиля клипа, через метку в промпте ----

def _system_seen(monkeypatch, tmp_path, style):
    """Перехват системного сообщения модели: cmd_intro ходит в заглушку, сеть не трогаем."""
    seen = {}

    def fake(system, user, schema, **kw):
        seen["system"] = system
        return {"intro_rows": [], "mid_groups": []}

    monkeypatch.setattr(commands, "_ask_json", fake)
    monkeypatch.setattr(commands, "_words_from_xml", lambda path: _ролик())
    aicut.cmd_intro(str(tmp_path / "clip.xml"), emit=lambda *a, **k: None, style=style)
    return seen["system"]


@pytest.mark.parametrize("style, num", [({"intro_row_max": 9}, "9"),
                                        ({"intro_row_max": 20}, "20"),
                                        (None, "20")])
def test_лимит_строки_в_промпте_берётся_из_стиля(monkeypatch, tmp_path, style, num):
    """Стиль с ручкой 9 — в system-сообщении модели «9 символов», не «14» и не метка."""
    system = _system_seen(monkeypatch, tmp_path, style)
    assert "<ROW_MAX>" not in system
    assert f"{num} символов вместе с пробелом" in system


def test_промпт_без_числа_14_в_лимите_строки():
    assert "14 символов вместе с пробелом" not in INTRO_SYSTEM
    assert "<ROW_MAX> символов вместе с пробелом" in INTRO_SYSTEM


# ---- 2. D3: хук кончается на конце мысли (только промпт) ----

def test_промпт_хук_кончается_на_конце_мысли():
    assert "Хук кончается на конце фразы, а не на союзе" not in INTRO_SYSTEM
    assert "законченная мысль" in INTRO_SYSTEM
    assert "«…ТРОМБА ИЛИ»" in INTRO_SYSTEM
    assert "«…БАЗУ. ЕСТЬ»" in INTRO_SYSTEM


# ---- 3. D5: слово-призыв — последний акцент ----

def test_промпт_призыв_последний_акцент():
    assert "напишите мне слово «консультация»" in INTRO_SYSTEM
    assert "после него акцентов не ставь" in INTRO_SYSTEM


def test_кавычки_разных_видов_и_на_соседних_словах():
    ws = _words(["а", "„ТРИ", "СЛОВА“", "б", '"ASCII"', "«внутри»"])
    assert commands._quote_spans(ws) == [(1, 2), (4, 4), (5, 5)]


def test_призыв_становится_последним_акцентом_хвост_удаляется():
    """«…напишите мне слово «консультация» в личные сообщения.» — последний акцент = слово
    «консультация»; группа после него (ЛИЧНЫЕ СООБЩЕНИЯ) удалена, ранняя группа цела."""
    log = Log()
    out = commands._place_call_word([_mid(20), _mid(38, 2, "yellow")], _ролик(),
                                    intro_len=0, busy=[], emit=log)
    assert [r["from"] for r in out if r["break"]] == [20, 36]
    assert out[-1] == {"from": 36, "count": 1, "color": "yellow", "break": True, "back": False}
    assert any("КОНСУЛЬТАЦИЯ" in line and "последний акцент" in line for line in log)


def test_призыв_уже_поставлен_ии_хвост_после_него_удаляется():
    """ИИ поставил «СЛОВО «КОНСУЛЬТАЦИЯ»» — группа сохраняется как есть, всё после неё уходит.
    Продолжение группы (from=None) тоже сохраняется."""
    mids = [_mid(35, 2, "yellow"),
            {"from": None, "count": 1, "color": "yellow", "break": False, "back": False},
            _mid(38)]
    log = Log()
    out = commands._place_call_word(list(mids), _ролик(), intro_len=0, busy=[], emit=log)
    assert out == mids[:2]
    assert any("последний акцент" in line for line in log)


def test_призыв_перекрыт_вставкой_ничего_не_добавлено():
    mids = [_mid(38)]
    log = Log()
    out = commands._place_call_word(list(mids), _ролик(), intro_len=0,
                                    busy=[(17.5, 19.0)], emit=log)
    assert out == mids
    assert any("КОНСУЛЬТАЦИЯ" in line and "перекрыт вставкой" in line for line in log)


def test_кавычек_нет_без_изменений():
    tail = ["НАПИШИТЕ", "МНЕ", "СЛОВО", "КОНСУЛЬТАЦИЯ", "В", "ЛИЧНЫЕ", "СООБЩЕНИЯ."]
    mids = [_mid(20), _mid(38)]
    log = Log()
    out = commands._place_call_word(mids, _ролик(tail=tail), intro_len=0, busy=[], emit=log)
    assert out is mids
    assert log == []


def test_кавычки_в_начале_ролика_не_трогаются():
    """Кавычки не в последних 15 % слов (здесь — индекс 0 из 40) — решает промпт."""
    words = _words(["«ПРИВЕТ»"] + [f"СЛОВО{k}" for k in range(39)])
    mids = [_mid(38)]
    log = Log()
    out = commands._place_call_word(mids, words, intro_len=0, busy=[], emit=log)
    assert out is mids
    assert log == []


def test_кавычки_длиннее_трёх_слов_не_призыв():
    tail = ["НАПИШИТЕ", "МНЕ", "«ПОДПИШИТЕСЬ", "НА", "ЭТОТ", "КАНАЛ", "СЕЙЧАС»."]
    mids = [_mid(20)]
    out = commands._place_call_word(mids, _ролик(tail=tail), intro_len=0, busy=[],
                                    emit=lambda *a, **k: None)
    assert out is mids


def test_призыв_внутри_интро_не_ставится():
    mids = [_mid(20)]
    log = Log()
    out = commands._place_call_word(mids, _ролик(), intro_len=40, busy=[], emit=log)
    assert out is mids
    assert any("в интро" in line for line in log)


def test_призыв_через_cmd_intro_последний_акцент_и_оформление(tmp_path, monkeypatch):
    """Сквозной путь: ответ модели -> cmd_intro -> .intro.json. Последний акцент — призыв,
    у него есть оформление (anim) — значит функция стоит ДО _intro_look."""
    monkeypatch.setattr(commands, "_words_from_xml", lambda path: _ролик())
    monkeypatch.setattr(commands, "_ask_json", lambda *a, **k: {
        "intro_rows": [{"count": 3, "color": "white", "break": True, "back": False}],
        "mid_groups": [{"from": 20, "count": 1, "color": "white", "back": False},
                       {"from": 38, "count": 2, "color": "yellow", "back": False}]})
    xml = str(tmp_path / "clip.xml")
    res = aicut.cmd_intro(xml, emit=lambda *a, **k: None)
    last = res["mid_groups"][-1]
    assert (last["from"], last["count"], last["color"], last["break"]) == (36, 1, "yellow", True)
    assert "anim" in last and "fx" in last
    saved = json.load(open(str(tmp_path / "clip.intro.json"), encoding="utf-8"))
    assert saved["mid_groups"][-1]["from"] == 36


# ---- 4. замер призыва на фикстуре (tools/intro_eval.py) ----

def test_замер_призыва_на_фикстуре(tmp_path, capsys):
    stem = str(tmp_path / "clip")
    _dump(stem + ".words.json", [{"w": t, "start": s, "end": e} for _, t, s, e in _ролик()])
    _dump(stem + ".intro.json", {
        "intro_rows": [{"count": 3, "color": "white", "break": True, "back": False}],
        "mid_groups": [_mid(20), _mid(38, 2, "yellow")]})
    rep = ev.call_report(stem + ".intro.json")
    assert rep["call"] is True and rep["call_timed"] is True
    assert rep["call_before"] is False          # в ответе ИИ призыв не последний
    assert rep["call_after"] is True            # по новым правилам — последний
    ev.call_summary([rep])
    out = capsys.readouterr().out
    assert "было (ИИ как есть) 0, стало (новые правила) 1" in out


def test_замер_призыва_без_таймингов_не_мерит(tmp_path, capsys):
    stem = str(tmp_path / "clip")
    _dump(stem + ".words.json", [{"w": t} for t in [w[1] for w in _ролик()]])
    _dump(stem + ".intro.json", {"intro_rows": [], "mid_groups": [_mid(20)]})
    rep = ev.call_report(stem + ".intro.json")
    assert rep["call"] is True and rep["call_timed"] is False
    ev.call_summary([rep])
    assert "таймингов в .words.json нет" in capsys.readouterr().out
