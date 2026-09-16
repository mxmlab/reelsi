# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание IB (круг 8): pathurl на суррогатах (п. 3), мёртвый `xml_attr` (п. 8),
текст титра в `.drp` (п. 6), роль ассета за папкой `assets/` (п. 9).

Запуск: py -3.10 -m pytest tests/test_r8_ib_artifacts.py -q -p no:cacheprovider
"""
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from core import assets, drp, xmlbuild, xmltext  # noqa: E402


def test_pathurl_круговой_рейс_кириллицы(tmp_path):
    """Путь с кириллицей и пробелами едет в XML и возвращается байт в байт."""
    p = str(tmp_path / "ролик камера 1.mp4")
    url = xmlbuild.pathurl(p)
    assert url.startswith("file://localhost/")
    assert "%" in url, "кириллица должна быть процентно экранирована"
    # Регистр смотрим только у ИМЕНИ файла: в самом пути tmp_path уже есть «%XX»
    # (pytest экранирует русское имя теста), а нам важен регистр своей записи.
    tail = url.rsplit("/", 1)[-1]
    escs = re.findall(r"%[0-9a-fA-F]{2}", tail)
    assert escs, f"имя не экранировано: {tail}"
    assert all(e == e.lower() for e in escs), \
        f"верхний регистр %xx — эталонные XML изменятся: {tail}"
    assert xmlbuild.unpathurl(url) == p


def test_pathurl_не_падает_на_одиночном_суррогате(tmp_path):
    """Имя с нечитаемым байтом (`os.listdir` на Linux отдаёт его суррогатом
    `\\udcff`): `quote(str)` падал UnicodeEncodeError — и падала вся сборка XML.
    Обратный разбор обязан вернуть ТО ЖЕ имя, а не «\\ufffd»."""
    p = str(tmp_path) + os.sep + "cam\udcff1.mp4"
    url = xmlbuild.pathurl(p)                       # до правки: UnicodeEncodeError
    assert "\udcff" not in url, "суррогат уехал в XML сырым"
    assert xmlbuild.unpathurl(url) == p, "имя с нечитаемым байтом не восстановилось"


def test_xml_attr_удалён():
    """`xml_attr` — ни одного потребителя: экранирование атрибутов делает
    `xml_text(..., attr=True)`, а вторая точка входа только расходилась с первой."""
    assert not hasattr(xmltext, "xml_attr"), "мёртвая функция вернулась"
    assert "&quot;" in xmltext.xml_text('a"b', attr=True)


def test_set_text_экранирует_обратный_слеш_и_перевод_строки():
    """Значение StyledText в Fusion — литерал Lua, а не XML-строка: обратный слеш
    начинает escape-последовательность, голый перевод строки закрывает литерал —
    композиция титра ломается и `.drp` не открывается."""
    nodes = 'StyledText = Input { Value = "старое" }'
    out = drp.set_text(nodes, 'C:\\new\nвторая "цитата"')
    assert out == 'StyledText = Input { Value = "C:\\\\new\\nвторая \'цитата\'" }'
    # кавычка по-прежнему апостроф: сам литерал не рвётся
    assert out.count('"') == 2
    # табуляция внутри строки Lua допустима — её не трогаем
    assert drp.set_text(nodes, "a\tb") == 'StyledText = Input { Value = "a\tb" }'


def test_set_text_без_спецсимволов_не_меняется():
    nodes = 'StyledText = Input { Value = "старое" }'
    assert drp.set_text(nodes, "новый титр") == 'StyledText = Input { Value = "новый титр" }'


class _LogRec:
    """Запись вызовов log.warning: базовый логгер «reelsi» не пробрасывает записи
    наверх (applog ставит propagate=False), штатный caplog их не видит."""

    def __init__(self):
        self.msgs = []

    def warning(self, fmt, *a):
        self.msgs.append(fmt % a)


def test_роль_ассета_за_папкой_предупреждает_один_раз(tmp_path, monkeypatch):
    """`../` в значении роли молча пропускался: «звук пропал», а в логе ни строчки.
    Предупреждение — с ролью и значением и по разу на роль, а не на каждый вызов."""
    d = tmp_path / "assets"
    d.mkdir()
    (d / "assets.json").write_text(json.dumps({"whoosh": "../секрет.mp3"}),
                                   encoding="utf-8")
    (tmp_path / "секрет.mp3").write_bytes(b"x")
    rec = _LogRec()
    monkeypatch.setattr(assets, "log", rec)

    resolve = assets.resolver(str(tmp_path))
    assert resolve("whoosh") == "", "файл за папкой assets/ всё ещё отдаётся"
    assert resolve("whoosh") == ""
    assert len(rec.msgs) == 1, f"предупреждение не один раз на роль: {rec.msgs}"
    assert "whoosh" in rec.msgs[0] and "../секрет.mp3" in rec.msgs[0], rec.msgs[0]


def test_обычная_роль_не_предупреждает(tmp_path, monkeypatch):
    d = tmp_path / "assets"
    d.mkdir()
    (d / "whoosh.mp3").write_bytes(b"x")
    (d / "assets.json").write_text(json.dumps({"whoosh": "whoosh.mp3"}), encoding="utf-8")
    rec = _LogRec()
    monkeypatch.setattr(assets, "log", rec)

    assert assets.resolver(str(tmp_path))("whoosh") == str(d / "whoosh.mp3")
    assert rec.msgs == []
