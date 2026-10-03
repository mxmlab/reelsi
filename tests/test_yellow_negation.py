# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Частица «не»/«ни» перед жёлтым словом тоже жёлтая (03.10.2026).

Симптом владельца: «на жёлтых выбираются тоже без приставок типа "не", упускаются».
Замер по 236 клипам: жёлтое слово с белым «НЕ» перед ним — 135 случаев, «НИ» — 3.
Предлоги перед жёлтым владелец добавлял руками 5 раз — они сюда НЕ входят.

Здесь шаг ``_yellow_fix_negation`` (дополнение набора индексов), его нормализация
(запятая, регистр, ё), отсутствие дублей и выхода за границы, и контракт групп:
два соседних жёлтых без зазора остаются ОДНОЙ стопкой (разделителей нет).
"""
import gzip
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import aicut, xml2ae  # noqa: E402
from core.aicut.commands import (  # noqa: E402
    YELLOW_NEGATION_PARTICLES, _norm_particle, _yellow_fix_negation)


def _words(*names):
    """Лента слов без зазоров: слово 0.5 с, начало следующего = конец предыдущего.

    Те же тайминги, что у боевого транскрипта внутри фразы, — иначе проверка
    «частица встала вплотную» мерила бы зазор тестовой ленты, а не прави́ло.
    """
    return [(i, w, i * 0.5, i * 0.5 + 0.5) for i, w in enumerate(names)]


# ---- «не»/«ни» перед выбранным словом добавляется ----

def test_не_помогает_добавляет_частицу():
    """«ЭТО НЕ ПОМОГАЕТ», модель выбрала ПОМОГАЕТ -> жёлтые {НЕ, ПОМОГАЕТ}."""
    words = _words("ЭТО", "НЕ", "ПОМОГАЕТ")
    assert _yellow_fix_negation([2], words) == [1, 2]


def test_cmd_yellow_не_помогает_целиком(monkeypatch):
    """Тот же «НЕ ПОМОГАЕТ» через боевой cmd_yellow: набор {НЕ, ПОМОГАЕТ} и строка
    «частица «не» к жёлтым: +1» в emit. Убрать вызов _yellow_fix_negation — тест
    краснеет и по набору, и по логу (проверка мутацией). ``_ask_json`` — заглушка
    (LLM не запускаем), запись файлов подменена."""
    import core.xml2ae as _xml2ae
    from core.aicut import commands as _cmd

    words = _words("ЭТО", "НЕ", "ПОМОГАЕТ")
    monkeypatch.setattr(_cmd, "_words_from_xml", lambda xml_path: words)
    monkeypatch.setattr(_cmd, "_ask_json", lambda *a, **k: {"yellow": [2]})
    monkeypatch.setattr(_xml2ae, "write_highlights",
                        lambda xml_path, idx: {"colored": list(idx), "skipped": []})
    monkeypatch.setattr(_cmd, "atomic_json_dump", lambda path, data: None)

    seen = []
    out = aicut.cmd_yellow("нет-такого.xml", emit=lambda line, **vars: seen.append((line, vars)))
    assert out["yellow"] == [1, 2], "«НЕ» не добавлено к жёлтым: %r" % (out["yellow"],)
    particle = [s for s in seen if s[0] == "  частица «не» к жёлтым: +{n}"]
    assert particle and particle[0][1] == {"n": 1}, \
        "в логе нет сообщения о добавленной частице: %r" % (seen,)


def test_запятая_и_заглавная_не_мешают():
    """«не,» с запятой и «Не» с заглавной — та же частица после нормализации."""
    assert _yellow_fix_negation([2], _words("ЭТО", "не,", "ПОМОГАЕТ")) == [1, 2]
    assert _yellow_fix_negation([2], _words("ЭТО", "Не", "ПОМОГАЕТ")) == [1, 2]


def test_ни_перед_словом_тоже_жёлтое():
    """«НИ» — вторая частица из замера (3 случая), поведение то же."""
    assert _yellow_fix_negation([2], _words("ЭТО", "НИ", "ПОМОГАЕТ")) == [1, 2]


def test_нормализация_регистр_пунктуация_ё():
    """_norm_particle: «не,» -> «НЕ», «Ё» -> «Е», краевые кавычки/тире снимаются."""
    assert _norm_particle("не,") == "НЕ"
    assert _norm_particle("  Ни! ") == "НИ"
    assert _norm_particle("«НЕ»") == "НЕ"
    assert _norm_particle("Ё") == "Е"


def test_уже_жёлтая_частица_без_дублей():
    """«НЕ» уже в наборе — второй раз не добавляется, набор не растёт."""
    words = _words("ЭТО", "НЕ", "ПОМОГАЕТ")
    assert _yellow_fix_negation([1, 2], words) == [1, 2]
    assert sorted(_yellow_fix_negation([2, 1], words)) == [1, 2]
    assert len(_yellow_fix_negation([2, 1], words)) == 2


def test_слово_ноль_не_выходит_за_границы():
    """Жёлтое слово 0 — предыдущего нет: ни исключения, ни отрицательного индекса."""
    words = _words("ПОМОГАЕТ", "НЕ", "ДРУГОЕ")
    assert _yellow_fix_negation([0], words) == [0]
    assert _yellow_fix_negation([0, 1], words) == [0, 1]


def test_предлог_перед_жёлтым_не_добавляется():
    """Предлоги («В», «НА», «ЗА»…) в набор частиц НЕ входят: белыми и остаются."""
    assert _yellow_fix_negation([2], _words("МЫ", "В", "ТРЕНИРОВКЕ")) == [2]
    assert _yellow_fix_negation([2], _words("МЫ", "НА", "ТРЕНИРОВКЕ")) == [2]
    assert _yellow_fix_negation([2], _words("МЫ", "ЗА", "ТРЕНИРОВКЕ")) == [2]
    assert "В" not in YELLOW_NEGATION_PARTICLES
    assert "НА" not in YELLOW_NEGATION_PARTICLES


def test_после_добавления_частицы_зазор_нулевой():
    """Частица встаёт вплотную: конец i−1 совпадает со стартом i (значит, одна группа)."""
    words = _words("ЭТО", "НЕ", "ПОМОГАЕТ")
    idx = _yellow_fix_negation([2], words)
    for a, b in zip(idx, idx[1:]):
        assert words[a][3] == pytest.approx(words[b][2]), \
            "между соседними жёлтыми появился зазор — группа развалится"


# ---- контракт групп: соседние жёлтые без зазора не разрываются разделителем ----

@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def test_не_и_слово_остаются_одной_группой(xml_subs):
    """«НЕ»+слово в XML: зазора нет -> auto_highlights не ставит разделитель между ними.

    Фикстура: слова 0 «ЛОРЕМ» (кадры 0-24) и 1 «СУ» (24-35) идут впритык — ровно тот
    случай, что даёт дополненный набор жёлтых. Разделителей быть не должно: это одна
    стопка, как и до правки.
    """
    res = xml2ae.set_highlights(xml_subs, [0, 1])
    assert sorted(res["colored"]) == [0, 1], "слова не покрасились — тест бессмысленен"
    auto = xml2ae.auto_highlights(xml_subs)
    assert auto["yellow"] == [0, 1]
    assert auto["breaks"] == [], \
        "соседние жёлтые без зазора получили разделитель (разные группы): %r" % (auto["breaks"],)
