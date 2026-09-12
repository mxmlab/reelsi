# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Синтаксис ОТРЕНДЕРЕННОГО .jsx (шаблон AE_FULL с подстановками), а не только шаблона.

Синтаксис статического .jsx в CI проверяет джоба `jsx` (node --check), но сборка —
это ПОДСТАНОВКА: текст из XML, имена вставок, числа и пути вставляются в шаблон
регулярками, и рвануть может именно в собранном файле (кавычка в слове, неэкраниро-
ванный слеш в пути, сломавшийся литерал), при этом сам шаблон остаётся валидным.
Битый .jsx роняет импорт и весь проект AE, а узнать об этом иначе можно только
открыв Adobe — это разрыв цикла обратной связи, который CLAUDE.md и запрещает.

Запуск:  python -m pytest reelsi/tests -q
"""
import gzip
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault("REELSI_NO_BROWSER", "1")

from core import xml2ae  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("node"),
                                reason="node --check требует node в PATH (в CI он есть)")


@pytest.fixture()
def xml_subs(tmp_path):
    """Распакованная копия эталонного XML с субтитрами (как в test_contracts)."""
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


@pytest.fixture()
def xml_nosubs(tmp_path):
    dst = str(tmp_path / "timeline_nosubs.xml")
    shutil.copy(os.path.join(HERE, "fixtures", "timeline_nosubs.xml"), dst)
    return dst


def _check_syntax(jsx_path):
    """node --check не принимает расширение .jsx — копируем в .js (как CI-джоба jsx)."""
    tmp = jsx_path + ".check.js"
    try:
        shutil.copy(jsx_path, tmp)
        p = subprocess.run(["node", "--check", tmp],
                           capture_output=True, text=True, timeout=120)
        assert p.returncode == 0, f"отрендеренный .jsx не прошёл node --check:\n{p.stderr.strip()[:500]}"
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def test_rendered_jsx_with_subs_is_syntactically_valid(xml_subs, tmp_path):
    out = str(tmp_path / "subs.jsx")
    xml2ae.to_ae_full(xml_subs, out, emit=lambda *a: None)
    _check_syntax(out)


def test_rendered_jsx_without_subs_is_syntactically_valid(xml_nosubs, tmp_path):
    out = str(tmp_path / "nosubs.jsx")
    xml2ae.to_ae_full(xml_nosubs, out, emit=lambda *a: None)
    _check_syntax(out)


def test_headless_jsx_tail_contract(xml_nosubs, tmp_path):
    """Безголовый .jsx (задание BD): alert из шаблона убран, есть save и quit, очередь
    чистится до добавления, а папку вывода задаём ПОСЛЕ applyTemplate — пресет «Untitled 1»
    несёт свой путь и молча перебьёт заданный до него. Пропавший файл в imp() пишет в
    $.writeln, а не alert: в -noui модалка зависла бы навсегда."""
    out = str(tmp_path / "headless.jsx")
    xml2ae.to_ae_full(xml_nosubs, out, emit=lambda *a: None,
                      render_dir="C:/путь/exp")
    src = open(out, encoding="utf-8-sig").read()
    assert 'alert("Reelsi: собрано' not in src       # итоговый alert убран везде (задание BD)
    assert 'savePrefAsString' not in src             # галка «Allow Scripts…» тут ни при чём (задание CD)
    assert 'while (rq0.numItems > 0)' in src         # очередь чистим
    assert 'applyTemplate("Best Settings")' in src
    assert 'applyTemplate("Untitled 1")' in src      # ровно так, через пробел
    assert 'new File("C:/путь/exp" + "/" + main.name + ".mov")' in src
    # файл ПОСЛЕ applyTemplate: индекс om.file > om.applyTemplate
    assert src.index("om.file") > src.index('om.applyTemplate("Untitled 1")')
    # .aep сохраняется ДВАЖДЫ (задание BT): до очереди — чтобы появился, даже если
    # очередь не собралась, и после — чтобы в него попала очередь
    assert 'var f = new File("' in src and src.count("new File(") >= 3   # aelog + aep + om.file
    assert src.count("app.project.save(f)") == 2
    # лог хвоста — файлом (.aelog.txt), а не $.writeln: в -noui наши $.writeln не видны (задание CD)
    assert ".aelog.txt" in src and "$.writeln(\"REELSI" not in src
    assert src.count("_log.writeln") >= 5                 # каждый шаг хвоста пишет в файл-лог
    assert "}finally{" in src and "app.quit();" in src   # quit в finally: иначе AE повиснет процессом
    assert "main.openInViewer()" not in src          # в безголовом режиме бессмысленно
    assert '$.writeln("Не найден файл: "+p)' in src  # imp() при пропавшем файле — в лог, не модалка
    _check_syntax(out)


def test_headless_jsx_has_no_modal_calls(xml_nosubs, tmp_path):
    """Безголовый .jsx не должен содержать НИ ОДНОГО модального вызова. alert/confirm/prompt
    в -noui — окно, которое никто не закроет: процесс зависнет навсегда. Ручная сборка
    вправе их иметь (человек у экрана), безголовая — нет."""
    out = str(tmp_path / "headless.jsx")
    xml2ae.to_ae_full(xml_nosubs, out, emit=lambda *a: None,
                      render_dir="C:/путь/exp")
    src = open(out, encoding="utf-8-sig").read()
    for modal in ("alert(", "confirm(", "prompt("):
        assert modal not in src, f"модальный вызов {modal} остался в безголовом .jsx"
    _check_syntax(out)


def test_manual_jsx_has_no_alert_but_keeps_viewer(xml_nosubs, tmp_path):
    """Ручная сборка меняется только в одном: итогового alert больше нет (пользователь
    решил убрать его везде). openInViewer остаётся — человек открыл AE и видит композицию."""
    out = str(tmp_path / "manual.jsx")
    xml2ae.to_ae_full(xml_nosubs, out, emit=lambda *a: None)
    src = open(out, encoding="utf-8-sig").read()
    assert 'alert("Reelsi: собрано' not in src       # итоговый alert убран везде (задание BD)
    assert 'alert("Не найден файл' in src            # imp(): человек у экрана должен увидеть, чего нет
    assert "main.openInViewer()" in src
    assert "app.quit()" not in src                   # сохраняет пользователь сам
    assert "renderQueue" not in src


def test_rendered_jsx_riser_custom_db_zero_offset_syntax(xml_subs, tmp_path):
    """Стиль с ризером db != 0, но at == in: в сгенерированном тексте нет
    startTime=;, есть rl.startTime=0;, и отрендеренный .jsx проходит node --check."""
    out = str(tmp_path / "riser_db.jsx")
    style = {
        "intro_riser": True,
        "intro_riser_db": -4.0,
        "intro_riser_at": 0.0,
        "intro_riser_in": 0.0,
    }
    xml2ae.to_ae_full(xml_subs, out, style=style, emit=lambda *a: None)
    txt = open(out, encoding="utf-8-sig").read()
    assert "startTime=;" not in txt
    assert "rl.startTime=0;" in txt
    _check_syntax(out)

