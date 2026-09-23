# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание NP: проглоченная ошибка обязана быть объяснена.

Сторож статический: в `core/`, `api/` и корневых `*.py` у каждого обработчика, чьё
тело — один `pass`, есть комментарий на той же строке или строкой выше. Комментарий
и есть ответ на вопрос «почему тут правильно ничего не делать»: уборка временного
файла, закрытие ресурса, проба необязательной возможности, сторож падения процесса.
Обработчик, который прячет отказ (запись, сеть, видеопамять, следующий шаг), вместо
этого пишет в лог — такие места перечислены в отчёте задания, `pass` в них нет.

Плюс три поведенческие проверки мест, где молчание теряло данные или прятало отказ:

* `_learn_halluc` — битый/не тот файл фраз НЕ перезаписывается (перезапись стирала
  всё выученное: файл ниоткуда не восстанавливается);
* `.srt` рядом с XML — сбой записи виден строкой в выводе сборки, сборка не падает;
* выгрузка RVM после рото — сбой виден строкой в выводе (иначе следующая сборка
  падает по видеопамяти без видимой причины).

И то же самое для двух журналов, которые дописываются, а не пересоздаются:
битый `history.json` генераций видео и битый `terms.json` перед записью откладываются
рядом (`<файл>.bad-…`), а не затираются одной новой записью.

Запуск:  python -m pytest tests/test_no_silent_except.py -q
"""
import ast
import gzip
import io
import json
import os
import shutil
import sys
import time
import tokenize
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import omni_cut  # noqa: E402

# Файлы-исключения: {путь от корня: причина}. Список пуст намеренно — комментарий
# можно поставить везде, включая core/crashtrace.py (там он особенно нужен: во время
# падения процесса бросать нельзя, но и молчать без объяснения нехорошо).
EXCEPTIONS = {}


def _scanned_files():
    """Всё, что под сторожем: корневые *.py, core/**/*.py, api/**/*.py."""
    files = sorted(ROOT.glob("*.py"))
    files += sorted((ROOT / "core").rglob("*.py"))
    files += sorted((ROOT / "api").rglob("*.py"))
    return [p for p in files if str(p.relative_to(ROOT)).replace("\\", "/") not in EXCEPTIONS]


def _comment_lines(src):
    """Номера строк, на которых есть комментарий (по токенам, а не по «#» в тексте)."""
    lines = set()
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            lines.add(tok.start[0])
    return lines


def _silent_handlers(src):
    """Строки `pass` обработчиков, чьё тело — ровно один `pass`."""
    out = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.ExceptHandler):
            continue
        body = [st for st in node.body
                if not (isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant)
                        and isinstance(st.value.value, str))]
        if len(body) == 1 and isinstance(body[0], ast.Pass):
            out.append(body[0].lineno)
    return out


def test_у_каждого_проглоченного_исключения_есть_объяснение():
    """Сторож: `except …: pass` без комментария — падение."""
    bad = []
    for path in _scanned_files():
        src = path.read_text(encoding="utf-8")
        comments = _comment_lines(src)
        for line in _silent_handlers(src):
            if line not in comments and (line - 1) not in comments:
                bad.append(f"{path.relative_to(ROOT)}:{line}")
    assert not bad, ("`pass` без объяснения (комментарий на той же строке или строкой выше):\n  "
                     + "\n  ".join(bad))


def test_исключения_перечислены_с_причиной():
    """Файл-исключение без причины — та же тишина, только в другом месте."""
    assert all(reason.strip() for reason in EXCEPTIONS.values()), EXCEPTIONS


# --------------------------------------------------------------------------- #
# Поведение: три места, где молчание дороже всего
# --------------------------------------------------------------------------- #
@pytest.fixture
def halluc_path(tmp_path, monkeypatch):
    """Словарик фраз — во временном каталоге: боевой `halluc_phrases.json` личный."""
    p = tmp_path / "halluc_phrases.json"
    monkeypatch.setattr(omni_cut, "HALLUC_PHRASES_PATH", str(p))
    return p


def test_битый_файл_фраз_не_перезаписывается(halluc_path):
    """Файл есть, но обрезан (крах при записи) — обучение пропускаем, файл цел."""
    halluc_path.write_bytes('["моя фра'.encode("utf-8"))
    before = halluc_path.read_bytes()

    omni_cut._learn_halluc(["новая фраза"])

    assert halluc_path.read_bytes() == before, "выученные фразы стёрты перезаписью"


def test_файл_фраз_не_список_не_перезаписывается(halluc_path):
    """В файле не список (руками поправили, чужая версия) — то же самое."""
    halluc_path.write_text('{"фраза": 1}', encoding="utf-8")
    before = halluc_path.read_bytes()

    omni_cut._learn_halluc(["новая фраза"])

    assert halluc_path.read_bytes() == before, "файл фраз перезаписан чужим форматом"


def test_без_файла_фразы_по_прежнему_учатся(halluc_path):
    """Успешный путь не изменился: файла нет — начинаем с пустого и записываем."""
    omni_cut._learn_halluc(["новая фраза"])

    assert json.loads(halluc_path.read_text(encoding="utf-8")) == ["новая фраза"]


@pytest.fixture
def xml_subs(tmp_path):
    """XML с дорожкой субтитров (та же фикстура, что у сборки .jsx)."""
    dst = tmp_path / "timeline.xml"
    with gzip.open(HERE / "fixtures" / "timeline_subs.xml.gz", "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return str(dst)


def test_сбой_записи_srt_рядом_с_xml_виден_в_выводе(xml_subs, tmp_path, monkeypatch):
    """`.srt` рядом с `.jsx` записан, рядом с XML — нет: об этом есть строка."""
    from core import subs, xml2ae

    real_write_srt = subs.write_srt

    def fake_write_srt(rows, path):
        if os.path.basename(path).startswith("timeline"):     # цель рядом с XML
            raise OSError("диск полон")
        return real_write_srt(rows, path)

    monkeypatch.setattr(subs, "write_srt", fake_write_srt)

    lines = []
    jsx = str(tmp_path / "out.jsx")
    xml2ae.to_ae_full(xml_subs, jsx_path=jsx, inserts=[], style={"intro_riser": False},
                      disclaimer="", emit=lambda *a, **k: lines.append(a[0] if a else ""))

    assert os.path.isfile(jsx), "сборка упала из-за .srt"
    assert os.path.isfile(str(tmp_path / "out.srt")), "копия рядом с .jsx не записана"
    assert any(".srt" in line and "XML" in line for line in lines), lines


def test_сбой_выгрузки_рото_виден_в_выводе(tmp_path, monkeypatch):
    """RVM не выгрузился — сборка не падает, но в выводе есть строка про видеопамять."""
    from core import roto as _roto
    from core.xml2ae.build import _roto_js

    plan = {
        "cams": [{"path": str(tmp_path / "cam1.mp4")}],
        "roto": [{"ci": 0, "src_start": 0.0, "src_end": 2.0, "ts": 0.0, "te": 2.0, "scale": 100}],
    }

    def mock_alpha_for_ranges(video, ranges, out_dir, **kw):
        return [{"start": 0.0, "end": 2.0, "mask": "/cache/m1.mp4", "f": 2.0}]

    def broken_release(emit=None):
        raise RuntimeError("CUDA driver error")

    monkeypatch.setattr(_roto, "alpha_for_ranges", mock_alpha_for_ranges)
    monkeypatch.setattr(_roto, "release", broken_release)

    lines = []
    res = _roto_js(plan, str(tmp_path / "test.xml"), {"roto": True, "base": str(tmp_path)},
                   emit=lambda *a, **k: lines.append(a[0] if a else ""), cancel=lambda: False)

    assert json.loads(res), "маски потерялись из-за сбоя выгрузки"
    assert any("не выгрузилась" in line for line in lines), lines


# --------------------------------------------------------------------------- #
# Битый журнал не затирается, а откладывается рядом
#
# Тот же шаблон, что у файла фраз выше: читатель битого файла отдаёт пустое значение,
# и следующая запись кладёт по тому же пути одну новую запись. История ОПЛАЧЕННЫХ
# генераций видео и словарь терминов пропадали так молча. Пропустить запись здесь
# нельзя (новый ролик остался бы без записи, правка словаря — без сохранения),
# поэтому пишущий откладывает битый файл в сторону и пишет заново.
# --------------------------------------------------------------------------- #
@pytest.fixture
def vhist(tmp_path, monkeypatch):
    """Журнал генераций — во временном каталоге: боевой history.json личный."""
    from api import videogen
    path = tmp_path / "history.json"
    monkeypatch.setattr(videogen, "VIDEO_HIST_PATH", str(path))
    return videogen, path


@pytest.fixture
def terms(tmp_path, monkeypatch):
    """Словарь терминов — во временном каталоге: боевой terms.json личный."""
    from core import terms as _terms
    path = tmp_path / "terms.json"
    monkeypatch.setattr(_terms, "TERMS_PATH", str(path))
    monkeypatch.setattr(_terms, "_CACHE", {"mtime": -1, "data": None})
    return _terms, path


def _bad(path):
    """Отложенные рядом копии: `<имя>.bad-*`."""
    return sorted(path.parent.glob(path.name + ".bad-*"))


def test_битый_журнал_генераций_откладывается_а_не_стирается(vhist):
    """Мусор вместо JSON: запись статуса завела бы журнал с нуля и стёрла историю
    оплаченных задач — вместо этого файл остаётся рядом целым."""
    videogen, path = vhist
    path.write_bytes(b"\x00\x01{not json")
    before = path.read_bytes()

    videogen.vhist_put("v1", ts=1, status="running", prompt="кот")

    bad = _bad(path)
    assert len(bad) == 1 and bad[0].read_bytes() == before, "битый журнал пропал"
    items = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(items, list) and items[0]["key"] == "v1"


def test_журнал_генераций_не_список_откладывается(vhist):
    """JSON прочитался, но это не список записей — истории из него не собрать."""
    videogen, path = vhist
    path.write_text('{"a": 1}', encoding="utf-8")
    before = path.read_bytes()

    videogen.vhist_put("v1", ts=1, status="done")

    bad = _bad(path)
    assert len(bad) == 1 and bad[0].read_bytes() == before, "чужой формат потерян"
    assert isinstance(json.loads(path.read_text(encoding="utf-8")), list)


def test_битый_словарь_терминов_откладывается_а_не_стирается(terms):
    """Обрезанный terms.json: правка из UI записала бы словарь из одного термина."""
    mod, path = terms
    path.write_bytes(b'{"terms": [{"term": "\xd0\xbe\xd0\xb1\xd1\x80')
    before = path.read_bytes()

    mod.set_terms([{"term": "MOTS-C", "variants": []}])

    bad = _bad(path)
    assert len(bad) == 1 and bad[0].read_bytes() == before, "старый словарь пропал"
    assert json.loads(path.read_text(encoding="utf-8"))["terms"][0]["term"] == "MOTS-C"
    assert [t["term"] for t in mod.load()["terms"]] == ["MOTS-C"]


def test_целые_файлы_не_откладываются(vhist, terms):
    """Обычная запись поверх читаемого файла: никаких .bad-* и ничего не потеряно."""
    videogen, hist_path = vhist
    mod, terms_path = terms
    videogen.vhist_put("v1", ts=1, status="running")
    videogen.vhist_put("v1", ts=2, status="done")
    mod.set_terms(["RTX5090"])
    mod.set_terms(["RTX5090", "Wi-Fi"])

    assert _bad(hist_path) == [] and _bad(terms_path) == []
    hist = json.loads(hist_path.read_text(encoding="utf-8"))
    assert len(hist) == 1 and hist[0]["status"] == "done"
    assert [t["term"] for t in mod.load()["terms"]] == ["RTX5090", "Wi-Fi"]


def test_занятое_имя_отложенного_файла_не_затирается(vhist):
    """Вторая поломка в ту же секунду: имя .bad-… занято — берём следующее, а чужой
    отложенный файл остаётся как был (иначе потерялись бы оба)."""
    videogen, path = vhist
    now = time.time()
    taken = [path.with_name(path.name + ".bad-" + time.strftime("%Y%m%d-%H%M%S",
                                                               time.localtime(now + d)))
             for d in (0, 1)]
    for t in taken:
        t.write_text("занято", encoding="utf-8")
    path.write_bytes(b"garbage, not json")
    before = path.read_bytes()

    videogen.vhist_put("v1", ts=1, status="done")

    for t in taken:
        assert t.read_text(encoding="utf-8") == "занято", "занятое имя .bad-* затёрто"
    saved = [b for b in _bad(path) if b.read_bytes() == before]
    assert len(saved) == 1, "битый журнал не отложен под свободным именем"
    assert isinstance(json.loads(path.read_text(encoding="utf-8")), list)
