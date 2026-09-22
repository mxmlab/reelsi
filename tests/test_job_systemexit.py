# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание MX: понятные ошибки (SystemExit) из фоновых заданий доходят до пользователя.

`raise SystemExit(umsg("код", "текст"))` — принятый в проекте канал ошибок (около 249
мест). Синхронные роуты ловят его (`except SystemExit`), а потоки заданий ловили
только `Exception`, а SystemExit — BaseException: «рото не посчитано для 2 из 3
кусков, сними галку рото в стиле» уходило из потока мимо лога и списка failed.

Здесь зовём ровно те функции, что стоят в `threading.Thread`: сборку
(`_run_build_job`), рендер (`_run_render_single`, `_run_render_job`) и нарезку
(`run_job`). Внешний вызов, который бросает SystemExit, подменён. На main все тесты
падают: в failed пусто, текста про рото в логе задания нет.

Запуск:  python -m pytest tests/test_job_systemexit.py -q
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

from core.umsg import umsg  # noqa: E402

# Тот самый текст из core/xml2ae/build.py:1777 (roto_incomplete) — по нему и проверяем,
# что до пользователя доехала ПРИЧИНА, а не traceback.
ROTO = "рото не посчитано для 2 из 3 кусков (первая причина: маска не найдена)"


def _roto_stop():
    """Ровно та ошибка, из-за которой заведено задание: SystemExit с umsg."""
    return SystemExit(umsg("roto_incomplete", ROTO, n=2, m=3, err="маска не найдена"))


def _log_text(log_list):
    """Лог задания одной строкой: записи структурные ({t, v}), строки — как есть."""
    return "\n".join(e["t"].format(**e.get("v", {})) if isinstance(e, dict) and "t" in e
                     else str(e) for e in log_list)


def _call_worker(fn, *args):
    """Позвать потоковую функцию так, как её позвал бы threading.Thread.

    SystemExit, дошедший до верха, — это и есть дефект MX: в потоке он уносит работу
    молча. Ловим его здесь, чтобы падал один тест, а не вся сессия pytest, и
    возвращаем текст — тест проверяет, что наверх не вышло ничего.
    """
    try:
        fn(*args)
    except SystemExit as e:
        return str(e)
    return None


def _clear_job(job, **extra):
    """Пустое состояние джоба на входе теста (в конце его вернёт фикстура conftest)."""
    job.update(running=True, done=False, log=[], results=[], failed=[], cancel=False,
               log_base=0, items=[], **extra)
    job.setdefault("result", [])


def test_sysexit_text_one_parser_for_umsg_and_plain():
    """Разбор SystemExit ОДИН (api/_core.sysexit_text): текст из umsg — русская
    строка (как её показывают синхронные роуты), обычный SystemExit("…") — как есть.
    Второй копии разбора umsg в потоках быть не должно."""
    from api._core import sysexit_text

    assert sysexit_text(_roto_stop()) == ROTO
    assert sysexit_text(SystemExit("просто текст")) == "просто текст"
    assert sysexit_text(SystemExit()) == ""


def test_build_job_systemexit_goes_to_failed_and_log(tmp_path, monkeypatch):
    """1. Сборка: xml2ae.to_ae_full бросает SystemExit(umsg("roto_incomplete", …)).
    Элемент в failed с текстом про рото, та же строка в логе, задание не висит running."""
    from api import build as apibuild
    from api._core import JOB
    from core import xml2ae

    _clear_job(JOB)
    xml = tmp_path / "01_clip.xml"
    xml.write_text("<xml/>", encoding="utf-8")

    def boom(*a, **kw):
        raise _roto_stop()

    monkeypatch.setattr(xml2ae, "to_ae_full", boom)

    escaped = _call_worker(apibuild._run_build_job, [{"xml_path": str(xml)}],
                           "separate", str(tmp_path / "out"))

    assert escaped is None, "SystemExit вышел из потока сборки: %s" % escaped
    assert JOB["failed"], "падение сборки не попало в failed"
    assert JOB["failed"][0]["name"] == "01_clip"
    assert ROTO in JOB["failed"][0]["reason"], JOB["failed"]
    item = next(i for i in JOB["items"] if i["name"] == "01_clip")
    assert item["stage"] == "error", item
    assert ROTO in _log_text(JOB["log"]), "текста про рото нет в логе задания"
    assert not JOB["results"], "в results не должно быть ничего"
    assert JOB["running"] is False, "задание осталось висеть running=True"
    assert JOB["done"] is True


def test_build_job_global_systemexit_stops_done_line(tmp_path, monkeypatch):
    """Сборка падает ВНЕ поклипового try (тут — перенос вставок в базу): причина в
    лог, «сборка» в failed, и «Сборка завершена» НЕ печатается — ровно как при
    Exception (тот же путь провала, только текст из umsg)."""
    from api import build as apibuild
    from api._core import JOB

    _clear_job(JOB)
    xml = tmp_path / "01_clip.xml"
    xml.write_text("<xml/>", encoding="utf-8")

    def boom(*a, **kw):
        raise _roto_stop()

    monkeypatch.setattr(apibuild, "_adopt_inserts", boom)

    escaped = _call_worker(apibuild._run_build_job, [{"xml_path": str(xml)}],
                           "separate", str(tmp_path / "out"))

    assert escaped is None, "SystemExit вышел из потока сборки: %s" % escaped
    assert [f["name"] for f in JOB["failed"]] == ["сборка"], JOB["failed"]
    assert ROTO in JOB["failed"][0]["reason"]
    log = _log_text(JOB["log"])
    assert "сборка прервана" in log and ROTO in log, log
    assert "Сборка завершена" not in log, "задание написало «Сборка завершена», хотя упало"
    assert JOB["running"] is False


def test_render_single_systemexit_marks_clip_failed(tmp_path, monkeypatch):
    """2а. Одиночный рендер: SystemExit на сборке безголового .jsx — ролик в failed с
    понятным текстом, а не «Ничего не собралось» без причины."""
    from api import render
    from core import xml2ae

    _clear_job(render.RJOB, pct=None, cur="", ae="", out_dir="", eta=None, eta_phase=None,
               eta_total=None, eta_preliminary=False, stage_label=None, stage_done=0,
               stage_total=0)
    # Очередь этапов заводит диспетчер (_run_render_job) — заводим её сами, как он.
    render.items_init(render.RJOB, render.RLOCK, ["01_clip"])
    outdir = tmp_path / "jsx"
    xml = tmp_path / "01_clip.xml"
    xml.write_text("<xml/>", encoding="utf-8")

    def boom(*a, **kw):
        raise _roto_stop()

    monkeypatch.setattr(xml2ae, "to_ae_full", boom)

    escaped = _call_worker(render._run_render_single,
                           [{"xml_path": str(xml), "outdir": str(outdir)}],
                           str(outdir), str(tmp_path / "exp"))

    assert escaped is None, "SystemExit вышел из потока рендера: %s" % escaped
    assert render.RJOB["failed"], "падение ролика не попало в failed"
    assert render.RJOB["failed"][0]["name"] == "01_clip"
    assert ROTO in render.RJOB["failed"][0]["reason"], render.RJOB["failed"]
    item = next(i for i in render.RJOB["items"] if i["name"] == "01_clip")
    assert item["stage"] == "error", item
    assert ROTO in _log_text(render.RJOB["log"]), "текста про рото нет в логе рендера"
    assert render.RJOB["running"] is False, "рендер остался висеть running=True"


def test_render_job_systemexit_goes_to_failed(tmp_path, monkeypatch):
    """2б. Диспетчер рендера: SystemExit из одиночного пути (и из combined — тот же
    путь провала) не выходит из потока, а становится понятной записью в failed."""
    from api import render

    _clear_job(render.RJOB, pct=None, cur="", ae="", out_dir="", eta=None, eta_phase=None,
               eta_total=None, eta_preliminary=False, stage_label=None, stage_done=0,
               stage_total=0)

    def boom(*a, **kw):
        raise _roto_stop()

    monkeypatch.setattr(render, "_run_render_single", boom)

    escaped = _call_worker(render._run_render_job,
                           [{"xml_path": str(tmp_path / "clip.xml")}], "",
                           str(tmp_path))

    assert escaped is None, "SystemExit вышел из потока рендера: %s" % escaped
    assert render.RJOB["failed"], "падение рендера не попало в failed"
    assert ROTO in render.RJOB["failed"][0]["reason"], render.RJOB["failed"]
    assert ROTO in _log_text(render.RJOB["log"]), "текста про рото нет в логе рендера"
    assert render.RJOB["running"] is False, "рендер остался висеть running=True"


def test_cut_job_systemexit_marks_clip_failed(tmp_path, monkeypatch):
    """3. Нарезка: reelsi.process_pair бросает SystemExit(umsg(…)) — клип в failed с
    понятным текстом, та же строка в логе, задание завершено (не висит running)."""
    import reelsi
    from api import jobs
    from api._core import JOB

    _clear_job(JOB)

    def boom(cams, out_xml, args, model=None, emit=None):
        raise _roto_stop()

    monkeypatch.setattr(reelsi, "process_pair", boom)

    opts = {"subs": False, "dedup": False, "srt": False, "ae": False, "keep": True,
            "model": "small", "scale": 1.0, "vad_thresh": 0.5, "min_silence": 0.3,
            "pad": 0.1}
    pairs = [[str(tmp_path / "01_cam1.mp4")]]

    escaped = _call_worker(jobs.run_job, "", str(tmp_path / "out"), pairs, opts)

    assert escaped is None, "SystemExit вышел из потока нарезки: %s" % escaped
    assert JOB["failed"], "падение клипа не попало в failed"
    assert JOB["failed"][0]["name"] == "01_cam1"
    assert ROTO in JOB["failed"][0]["reason"], JOB["failed"]
    item = next(i for i in JOB["items"] if i["name"] == "01_cam1")
    assert item["stage"] == "error", item
    assert ROTO in _log_text(JOB["log"]), "текста про рото нет в логе нарезки"
    assert JOB["running"] is False, "нарезка осталась висеть running=True"
