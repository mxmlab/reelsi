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
# Широкий `except` (Exception / BaseException / голый) — не только `pass`
#
# `pass` — не единственный способ промолчать: `return ""`, `return None`, присвоение
# дефолта тоже глотают отказ. Поэтому у КАЖДОГО широкого обработчика должно быть одно
# из трёх: `raise` в теле, вызов журнала/вывода, комментарий, который объясняет выбор.
# --------------------------------------------------------------------------- #
_BROAD = {"Exception", "BaseException"}
# Журнал: модульные логгеры и stdlib-модули, по корню цепочки атрибутов.
_LOG_BASES = {"log", "logger", "logging", "warnings", "traceback", "_log"}
# Вывод пользователю: print, консольный и любой *emit-колбэк (`emit`, `_emit`,
# `_gemit`, `vemit`, `remit`, `console_emit`). Имя, оканчивающееся на emit, — это
# соглашение проекта для строк лога, поэтому такие обёртки не нужно перечислять.
_LOG_NAMES = {"print", "console_emit"}


def _is_broad(type_node):
    """Ловит ли обработчик всё: голый `except:`, `Exception`, `BaseException` или кортеж с ними."""
    if type_node is None:
        return True
    names = type_node.elts if isinstance(type_node, ast.Tuple) else [type_node]
    for n in names:
        name = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else ""
        if name in _BROAD:
            return True
    return False


def _root_name(node):
    """Самое левое имя цепочки атрибутов: `logging.getLogger().warning` -> `logging`."""
    while isinstance(node, (ast.Attribute, ast.Call)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node.id if isinstance(node, ast.Name) else ""


def _is_log_call(call):
    f = call.func
    if isinstance(f, ast.Name):
        return f.id in _LOG_NAMES or f.id.endswith("emit")
    if isinstance(f, ast.Attribute):
        return _root_name(f) in _LOG_BASES or f.attr.endswith("emit")
    return False


def _handler_explained(handler, comments):
    """Есть ли у обработчика журнал/вывод, `raise` или комментарий (на except, строкой выше, в теле)."""
    for node in handler.body:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Raise):
                return True
            if isinstance(sub, ast.Call) and _is_log_call(sub):
                return True
    end = handler.end_lineno or handler.lineno
    lines = set(range(handler.lineno - 1, end + 1))
    return bool(lines & comments)


def _unexplained_broad(src):
    """Номера строк широких `except`, которые глотают отказ без объяснения."""
    tree = ast.parse(src)
    comments = _comment_lines(src)
    return [node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler) and _is_broad(node.type)
            and not _handler_explained(node, comments)]


def test_широкий_except_объяснён_в_каждом_файле():
    """Сторож B2: широкий `except` без raise / журнала / комментария — падение со списком мест."""
    bad = []
    for path in _scanned_files():
        for line in _unexplained_broad(path.read_text(encoding="utf-8")):
            bad.append(f"{path.relative_to(ROOT).as_posix()}:{line}")
    assert not bad, ("широкий except глотает отказ без объяснения "
                     "(нужен raise, вызов журнала/вывода или комментарий):\n  " + "\n  ".join(bad))


def test_сторож_различает_объяснённое_и_немое():
    """Самопроверка сторожа: на синтетических обработчиках он должен быть прав в обе стороны."""
    немой = "try:\n    f()\nexcept Exception:\n    return ''\n"
    голый_pass = "try:\n    f()\nexcept:\n    pass\n"
    с_комментарием = "try:\n    f()\nexcept Exception:  # ответ — пустая строка\n    return ''\n"
    комментарий_выше = "try:\n    f()\n# почему молчим\nexcept Exception:\n    return ''\n"
    с_raise = "try:\n    f()\nexcept BaseException as e:\n    raise RuntimeError() from e\n"
    с_журналом = "try:\n    f()\nexcept Exception as e:\n    log.warning('x %s', e)\n"
    с_обёрткой = "try:\n    f()\nexcept Exception as e:\n    _gemit('сбой: {err}', err=e)\n"
    с_print = "try:\n    f()\nexcept (ValueError, Exception):\n    print('x')\n"
    узкий = "try:\n    f()\nexcept ValueError:\n    return ''\n"

    assert _unexplained_broad(немой) == [3]
    assert _unexplained_broad(голый_pass) == [3]
    assert _unexplained_broad(с_комментарием) == []
    assert _unexplained_broad(комментарий_выше) == []
    assert _unexplained_broad(с_raise) == []
    assert _unexplained_broad(с_журналом) == []
    assert _unexplained_broad(с_обёрткой) == []
    assert _unexplained_broad(с_print) == []
    assert _unexplained_broad(узкий) == [], "узкий except вне сторожа"


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


# --------------------------------------------------------------------------- #
# Остальные писатели, которые читают файл и пишут его обратно: индекс базы вставок,
# статистика рендера, журнал задач, снимок клипа, сайдкар проекта, трек головы, кэш
# плагинов, состояние интерфейса, сайдкар голоса. Битый файл уходит в `.bad-…` с
# прежними байтами, целый файл не откладывается ни разу.
# --------------------------------------------------------------------------- #
@pytest.fixture
def index(tmp_path, monkeypatch):
    """Индекс вставок — во временном каталоге: боевой insertlib.json личный."""
    from core import insertlib
    path = tmp_path / "insertlib.json"
    monkeypatch.setattr(insertlib, "INDEX_PATH", str(path))
    # Иначе пустой путь тихо скопировал бы боевой индекс (см. _seed_index).
    monkeypatch.setattr(insertlib, "_seed_index", lambda: None)
    monkeypatch.setattr(insertlib, "_CACHE", {"mtime": 0, "data": None, "mat": None,
                                              "mat_items": None, "df": None,
                                              "cand_words": None, "N": 0})
    return insertlib, path


@pytest.mark.parametrize("raw", [b'{"items": [{"path": "a.png"', b"[1, 2, 3]"],
                         ids=["обрезан", "не объект"])
def test_битый_индекс_вставок_откладывается_а_не_стирается(index, raw):
    """Личная база вставок: запись поверх битого индекса стёрла бы тысячи записей."""
    insertlib, path = index
    path.write_bytes(raw)
    before = path.read_bytes()

    insertlib._load()                        # как пишущие пути: сначала читают
    insertlib._save({"items": []})

    bad = _bad(path)
    assert len(bad) == 1 and bad[0].read_bytes() == before, "база вставок затёрта"
    assert json.loads(path.read_text(encoding="utf-8")) == {"items": []}


def test_целый_индекс_вставок_не_откладывается(index):
    insertlib, path = index
    path.write_text(json.dumps({"items": [{"path": "a.png"}]}), encoding="utf-8")

    d = insertlib._load()
    d["items"].append({"path": "b.png"})
    insertlib._save(d)

    assert _bad(path) == []
    assert len(json.loads(path.read_text(encoding="utf-8"))["items"]) == 2


@pytest.fixture
def render_stats(tmp_path, monkeypatch):
    """Статистика рендеров — во временном каталоге (REELSI_RENDER_STATS его и задаёт)."""
    from core import aerender
    path = tmp_path / "render_stats.json"
    monkeypatch.setattr(aerender, "get_stats_path", lambda: str(path))
    return aerender, path


def test_битая_статистика_рендера_откладывается(render_stats):
    """Одна новая запись поверх битой статистики стёрла бы историю прогонов."""
    aerender, path = render_stats
    path.write_bytes(b'{"runs": [{"n": 3, "jsx_sec": 1.0')
    before = path.read_bytes()

    aerender.save_render_stats(2, 1.0, 2.0, 3.0)

    bad = _bad(path)
    assert len(bad) == 1 and bad[0].read_bytes() == before, "история рендеров стёрта"
    runs = json.loads(path.read_text(encoding="utf-8"))["runs"]
    assert len(runs) == 1 and runs[0]["n"] == 2


def test_целая_статистика_рендера_не_откладывается(render_stats):
    aerender, path = render_stats
    aerender.save_render_stats(2, 1.0, 2.0, 3.0)
    aerender.save_render_stats(4, 1.0, 2.0, 3.0)

    assert _bad(path) == []
    assert len(json.loads(path.read_text(encoding="utf-8"))["runs"]) == 2


@pytest.fixture
def journal(tmp_path, monkeypatch):
    """Журнал задач — во временном каталоге: боевой job_state.json живой."""
    from core import jobstate
    path = tmp_path / "job_state.json"
    monkeypatch.setattr(jobstate, "JOB_STATE_PATH", str(path))
    return jobstate, path


def test_битый_журнал_задач_откладывается(journal):
    """Запись одного слота поверх битого журнала стёрла бы записи остальных слотов."""
    jobstate, path = journal
    path.write_bytes(b'{"version": 1, "jobs": {"render": {"slot": "render"')
    before = path.read_bytes()

    jobstate.journal_write("job", "cut", "ролик", "running")

    bad = _bad(path)
    assert len(bad) == 1 and bad[0].read_bytes() == before, "журнал задач затёрт"
    assert "job" in json.loads(path.read_text(encoding="utf-8"))["jobs"]


def test_целый_журнал_задач_не_откладывается(journal):
    jobstate, path = journal
    jobstate.journal_write("render", "render", "рендер", "running")
    jobstate.journal_write("job", "cut", "ролик", "running")

    assert _bad(path) == []
    assert set(json.loads(path.read_text(encoding="utf-8"))["jobs"]) == {"render", "job"}


def test_битый_снимок_клипа_откладывается(tmp_path):
    """Снимок клипа — единственная копия состояния клипа рядом с XML."""
    from core import clipstore
    xml = tmp_path / "01_clip.xml"
    xml.write_text("<xmeml/>", encoding="utf-8")
    cpath = Path(clipstore.clip_path(str(xml)))
    cpath.write_bytes(b'{"v": 1, "clip": {"xml"')
    before = cpath.read_bytes()
    clip = {"xml": str(xml), "name": "01_clip.xml", "job": {"introRows": []}}

    assert clipstore.save_clips({"CLIPS": [clip]}) == 1

    bad = _bad(cpath)
    assert len(bad) == 1 and bad[0].read_bytes() == before, "снимок клипа затёрт"
    assert clipstore.load_clip(str(xml)) == clip


def test_целый_снимок_клипа_не_откладывается(tmp_path):
    from core import clipstore
    xml = tmp_path / "01_clip.xml"
    xml.write_text("<xmeml/>", encoding="utf-8")
    clip = {"xml": str(xml), "name": "01_clip.xml"}

    clipstore.save_clips({"CLIPS": [clip]})
    clip2 = dict(clip, name="02_clip.xml")
    clipstore.save_clips({"CLIPS": [clip2]})

    assert _bad(Path(clipstore.clip_path(str(xml)))) == []
    assert clipstore.load_clip(str(xml)) == clip2


def test_битый_сайдкар_проекта_откладывается(tmp_path):
    """read_project отдал None, а запись заменила бы сайдкар новым словарём."""
    from core import project_file
    path = tmp_path / "01_clip.project.json"
    path.write_bytes(b'{"speaker": "ivan", "inserts"')
    before = path.read_bytes()

    project_file.write_project(path, {"speaker": "maxim"})

    bad = _bad(path)
    assert len(bad) == 1 and bad[0].read_bytes() == before, "сайдкар проекта затёрт"
    assert project_file.read_project(path)["speaker"] == "maxim"


def test_целый_сайдкар_проекта_не_откладывается(tmp_path):
    from core import project_file
    path = tmp_path / "01_clip.project.json"
    project_file.write_project(path, {"speaker": "maxim"})
    project_file.write_project(path, {"speaker": "ivan"})

    assert _bad(path) == []
    assert project_file.read_project(path)["speaker"] == "ivan"


def test_битый_трек_головы_откладывается(tmp_path, monkeypatch):
    """Трек головы, который load_cached не принял, перезаписывается — файл уходит в .bad-…"""
    from core import headtrack
    video = tmp_path / "cam1.mp4"
    video.write_bytes(b"0" * 16)
    xml = str(tmp_path / "timeline.xml")
    head = Path(headtrack.head_cache_path(xml, 1))
    head.write_bytes(b'{"v": 1, "video": "')
    before = head.read_bytes()
    monkeypatch.setattr(headtrack, "track",
                        lambda video, ranges, emit=None, cancel=None, fps=10: {"v": 1, "frames": []})

    headtrack.load_or_track(xml, str(video), [(0.0, 1.0)], cam=1)

    bad = _bad(head)
    assert len(bad) == 1 and bad[0].read_bytes() == before, "трек головы затёрт"
    assert json.loads(head.read_text(encoding="utf-8"))["frames"] == []


def test_целый_трек_головы_не_откладывается(tmp_path, monkeypatch):
    from core import headtrack
    video = tmp_path / "cam1.mp4"
    video.write_bytes(b"0" * 16)
    xml = str(tmp_path / "timeline.xml")
    head = Path(headtrack.head_cache_path(xml, 1))
    monkeypatch.setattr(headtrack, "track",
                        lambda video, ranges, emit=None, cancel=None, fps=10: {"v": 1, "frames": []})

    headtrack.load_or_track(xml, str(video), [(0.0, 1.0)], cam=1)
    headtrack.load_or_track(xml, str(video), [(0.0, 1.0)], cam=1)

    assert _bad(head) == []


def test_битый_кэш_плагинов_откладывается(tmp_path):
    """Родитель пишет свой кэш поверх битого: прошлые результаты сканирования пропали бы."""
    from core import voicefx_scan
    path = tmp_path / "vst3_cache.json"
    path.write_bytes(b'{"version": 1, "entries": {"a": ')
    before = path.read_bytes()

    voicefx_scan.write_cache(str(path), {"k": {"ok": True, "names": ["A"]}})

    bad = _bad(path)
    assert len(bad) == 1 and bad[0].read_bytes() == before, "кэш плагинов затёрт"
    assert voicefx_scan.read_cache(str(path)) == {"k": {"ok": True, "names": ["A"]}}


def test_целый_кэш_плагинов_не_откладывается(tmp_path):
    from core import voicefx_scan
    path = tmp_path / "vst3_cache.json"
    voicefx_scan.write_cache(str(path), {"a": {"ok": True, "names": []}})
    voicefx_scan.write_cache(str(path), {"b": {"ok": True, "names": []}})

    assert _bad(path) == []
    assert set(voicefx_scan.read_cache(str(path))) == {"b"}


def test_битое_состояние_интерфейса_откладывается(tmp_path, monkeypatch):
    """Состояние интерфейса: ревизия читается как 0, запись заменила бы файл целиком."""
    import api
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    client = app.test_client()
    path = tmp_path / "ui_state.json"
    monkeypatch.setattr(api.files, "UI_STATE_PATH", str(path))
    path.write_bytes(b'{"CLIPS": [{"xml": ')
    before = path.read_bytes()

    res = client.post("/api/ui_state", json={"state": {"CLIPS": []}},
                      headers={"Content-Type": "application/json"})

    assert res.status_code == 200 and res.get_json()["ok"] is True
    bad = _bad(path)
    assert len(bad) == 1 and bad[0].read_bytes() == before, "состояние интерфейса затёрто"


def test_целое_состояние_интерфейса_не_откладывается(tmp_path, monkeypatch):
    import api
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    client = app.test_client()
    path = tmp_path / "ui_state.json"
    monkeypatch.setattr(api.files, "UI_STATE_PATH", str(path))
    H = {"Content-Type": "application/json"}

    client.post("/api/ui_state", json={"state": {"CLIPS": []}}, headers=H)
    client.post("/api/ui_state", json={"state": {"CLIPS": [], "x": 1}}, headers=H)

    assert _bad(path) == []


def test_битый_сайдкар_голоса_откладывается(tmp_path, monkeypatch):
    """Сайдкар итогового голоса: final_voice_ready отдал «не готов» — пересборка пишет поверх."""
    from core import voicefx
    xml = tmp_path / "01_clip.xml"
    xml.write_text("<xmeml/>", encoding="utf-8")
    cache = tmp_path / "voice_cache.wav"
    cache.write_bytes(b"RIFF0000WAVEfmt ")
    monkeypatch.setattr(voicefx, "render_cached", lambda *a, **k: str(cache))
    monkeypatch.setattr(voicefx, "_sync_xml_voice", lambda *a, **k: None)
    meta = Path(voicefx._final_meta_path(str(xml)))
    meta.write_bytes(b'{"key": "stale"')
    before = meta.read_bytes()

    voicefx.ensure_final_voice(str(xml), "cam1.mp4", {}, emit=lambda *a, **k: None)

    bad = _bad(meta)
    assert len(bad) == 1 and bad[0].read_bytes() == before, "сайдкар голоса затёрт"
    saved = json.loads(meta.read_text(encoding="utf-8"))
    assert saved["key"] == voicefx.final_voice_key("cam1.mp4", {})


def test_целый_сайдкар_голоса_не_откладывается(tmp_path, monkeypatch):
    from core import voicefx
    xml = tmp_path / "01_clip.xml"
    xml.write_text("<xmeml/>", encoding="utf-8")
    cache = tmp_path / "voice_cache.wav"
    cache.write_bytes(b"RIFF0000WAVEfmt ")
    monkeypatch.setattr(voicefx, "render_cached", lambda *a, **k: str(cache))
    monkeypatch.setattr(voicefx, "_sync_xml_voice", lambda *a, **k: None)

    voicefx.ensure_final_voice(str(xml), "cam1.mp4", {}, emit=lambda *a, **k: None)
    voicefx.ensure_final_voice(str(xml), "cam1.mp4", {}, emit=lambda *a, **k: None)

    assert _bad(Path(voicefx._final_meta_path(str(xml)))) == []


def test_битый_сайдкар_силы_откладывается(tmp_path):
    """Сайдкар силы: read_emphasis отдал «не готов», расчёт пишет поверх — файл уходит в .bad-…"""
    from core import emphasis
    xml = str(tmp_path / "clip.xml")
    open(xml, "w", encoding="utf-8").close()
    side = Path(emphasis.emph_path(xml))
    side.write_bytes(b'{"key": [1, 2')
    before = side.read_bytes()
    words = [emphasis.WordRef(idx=0, text="слово", start=0.0, end=0.5)]

    emphasis.compute_emphasis(emphasis.EmphasisInputs(
        words=words, parsed=(None, [], [], None), xml_path=xml, idx=[0],
        emit=lambda *a, **k: None))

    bad = _bad(side)
    assert len(bad) == 1 and bad[0].read_bytes() == before, "сайдкар силы затёрт"
    assert "scores" in json.loads(side.read_text(encoding="utf-8"))


def test_целый_сайдкар_силы_не_откладывается(tmp_path):
    from core import emphasis
    xml = str(tmp_path / "clip.xml")
    open(xml, "w", encoding="utf-8").close()
    words = [emphasis.WordRef(idx=0, text="слово", start=0.0, end=0.5)]
    inp = emphasis.EmphasisInputs(words=words, parsed=(None, [], [], None), xml_path=xml,
                                  idx=[0], emit=lambda *a, **k: None)

    emphasis.compute_emphasis(inp)
    emphasis.compute_emphasis(inp)

    assert _bad(Path(emphasis.emph_path(xml))) == []
