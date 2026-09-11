# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Раскладка акцентов «текст за спиной» (_place_mids).

Баг, ради которого тест: страховка «не ближе 6 секунд ПО СТАРТУ» выбрасывала 40%
разметки. Сверка с ручным эталоном юзера (9 роликов, 148 акцентов в собранных .jsx,
июль 2026) дала медианный разрыв 4.2с, p10 = 1.2с, минимум 0.4с; порог 6с оставлял
53% эталона, правило «не наезжать на конец предыдущей группы» — 93%. Смысл страховки —
не дать двум прекомпам висеть одновременно, а не задавать ритм.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import aicut  # noqa: E402


def _words(n=40, step=0.5):
    """Лента слов: слово каждые step секунд, длительность слова 0.4с."""
    return [(k, f"СЛОВО{k}", k * step, k * step + 0.4) for k in range(n)]


def _place(groups, words=None, intro_len=0, busy=()):
    return aicut._place_mids(groups, words or _words(), intro_len, busy,
                             emit=lambda *a, **k: None)


def _heads(mids, words=None):
    """Головы групп -> (индекс первого слова, цвет)."""
    return [(m["from"], m["color"]) for m in mids if m["break"]]


def test_соседние_акценты_через_две_секунды_остаются():
    """Ровно то, что резал порог 6с: у юзера медиана разрыва 4.2с."""
    mids = _place([(10, 1, "yellow"), (14, 1, "yellow"), (18, 1, "yellow")])
    assert _heads(mids) == [(10, "yellow"), (14, "yellow"), (18, "yellow")]


def test_наезд_на_предыдущую_группу_отбрасывается():
    """Слово 10 звучит 5.0–5.4с, слово 11 — 5.5–5.9с: второй прекомп встал бы поверх."""
    mids = _place([(10, 1, "white"), (11, 1, "white")])
    assert _heads(mids) == [(10, "white")]


def test_акцент_под_вставкой_отбрасывается():
    """Кадр занят вставкой — текста за спиной там не видно."""
    mids = _place([(10, 1, "white"), (20, 1, "white")], busy=[(4.8, 7.5)])
    assert _heads(mids) == [(20, "white")]


def test_акцент_внутри_интро_отбрасывается():
    mids = _place([(3, 1, "white"), (20, 1, "white")], intro_len=8)
    assert _heads(mids) == [(20, "white")]


def test_группа_за_краем_ленты_отбрасывается():
    mids = _place([(38, 5, "white")], words=_words(40))
    assert mids == []


def test_длинная_группа_рвётся_на_строки_одного_прекомпа():
    """Голова несёт from+break, продолжения — from=None, break=False."""
    mids = _place([(10, 3, "yellow")])
    assert len(mids) > 1
    assert mids[0]["from"] == 10 and mids[0]["break"] is True
    assert all(m["from"] is None and m["break"] is False for m in mids[1:])
    assert sum(m["count"] for m in mids) == 3


def test_группы_идут_по_возрастанию_таймингов():
    mids = _place([(30, 1, "white"), (10, 1, "white"), (20, 1, "white")])
    assert _heads(mids) == [(10, "white"), (20, "white"), (30, "white")]


# ---- карта свободных окон: смысл разметки — занять места, где вставок НЕТ ----

def _ins(t, dur=2.5):
    return {"start_sec": t, "duration_sec": dur}


def test_свободные_окна_это_дырки_между_вставками():
    words = _words(60)                                  # ролик 0–30с
    free = aicut._free_windows(words, [_ins(5), _ins(12), _ins(20)], after=0.0)
    assert [(round(a, 1), round(b, 1)) for a, b in free] == [
        (0.0, 5.0), (7.5, 12.0), (14.5, 20.0), (22.5, 29.9)]


def test_окно_короче_порога_не_окно():
    """2 секунды между вставками — акцент туда не влезает."""
    free = aicut._free_windows(_words(60), [_ins(5), _ins(9.5)], after=0.0)
    assert all(b - a >= aicut.INTRO_FREE_MIN for a, b in free)
    assert not any(round(a, 1) == 7.5 for a, b in free)


def test_интро_из_свободных_окон_исключено():
    free = aicut._free_windows(_words(60), [_ins(20)], after=8.0)
    assert round(free[0][0], 1) == 8.0


def test_квота_растёт_с_длиной_окна_и_не_бывает_нулевой():
    """Пустое окно и есть то место, ради которого всё затевается — там всегда ≥ 1."""
    assert aicut._free_quota(0, 3) == 1
    assert aicut._free_quota(0, 18) > aicut._free_quota(0, 8) > 1


def test_карта_показывает_окна_с_квотой():
    words = _words(60)
    free = aicut._free_windows(words, [_ins(10)], after=0.0)
    hint = aicut._intro_free_hint(words, free)
    assert "СЮДА И СТАВЬ АКЦЕНТЫ" in hint
    assert "0–10с → " in hint
    assert "ЗАНЯТО вставками" in hint
