# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Безголовый рендер в AE (задание BD, шаг 3, api/render.py).

Проверяется то, что можно без живого After Effects: разбор прогресса aerender
(честный процент = кадр / кадры из плана, маркеры конца), число кадров композиции
из плана .jsx (FPS/DUR + удлинение под дисклеймер), выбор самой свежей версии AE
из Program Files (путь константой не зашит, версия не подменяется), дефолт папки
вывода — exp рядом с репозиторием, а не внутри.

И предполётная проверка ДО AE (задание BJ): она обязана пропустить клип с интро и
вставками из интерфейса (их в сыром XML нет по построению — сверки с ним тут быть
не может), но валить рендер на пропавшем файле, как и раньше.

Запуск:  python -m pytest reelsi/tests -q
"""
import gzip
import os
import shutil
import sys
import urllib.parse

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault("REELSI_NO_BROWSER", "1")

import api.render as render  # noqa: E402


def test_progress_parses_frame_in_parens():
    """aerender пишет номер кадра в скобках; процент = кадр / кадры из плана."""
    assert render._parse_progress("(1/1600) frame", 1600) == pytest.approx(1 / 1600)
    assert render._parse_progress("(800/1600) frame", 1600) == pytest.approx(0.5)
    assert render._parse_progress("(10) frame", 1600) == pytest.approx(10 / 1600)
    # без total в строке — берём total из плана (второй аргумент)
    assert render._parse_progress("(640)", 1600) == pytest.approx(0.4)


def test_progress_marks_end():
    """Finished composition / Total Time Elapsed — маркеры конца: 100%."""
    assert render._parse_progress("Finished composition: MAIN", 1600) == 1.0
    assert render._parse_progress("Total Time Elapsed: 161.8 seconds", 1600) == 1.0


def test_progress_ignores_non_frame_lines():
    assert render._parse_progress("After Effects 26.2 - running...", 1600) is None
    assert render._parse_progress("", 1600) is None


def test_comp_frames_from_plan():
    """Общее число кадров — из плана .jsx: FPS * (DUR + удлинение дисклеймера)."""
    jsx = "var W=1080, H=1920, FPS=60, DUR=33.0000;"
    jsx += "\n    var main = app.project.items.addComp(name, W, H, 1.0, Math.max(DUR,1), FPS);"
    assert render._comp_frames(jsx) == 1980            # 33с * 60
    jsx2 = jsx.replace("Math.max(DUR,1)", "Math.max(DUR,1)+1.35")
    assert render._comp_frames(jsx2) == round((33.0 + 1.35) * 60)
    assert render._comp_frames("no fps here") is None


def test_default_render_dir_is_next_to_repo():
    """Папка вывода по умолчанию — exp РЯДОМ с репозиторием (он публичный), не внутри."""
    d = render.default_render_dir()
    assert os.path.basename(d) == "exp"
    assert os.path.dirname(d) == os.path.dirname(ROOT)


def _fake_ae_tree(tmp_path, version):
    """Каталог «Adobe/Adobe After Effects 20xx/Support Files» с aerender/AfterFX .exe."""
    sf = tmp_path / "Adobe" / ("Adobe After Effects " + version) / "Support Files"
    sf.mkdir(parents=True)
    (sf / "aerender.exe").write_bytes(b"")
    (sf / "AfterFX.exe").write_bytes(b"")
    return sf


def test_find_ae_picks_newest_version(tmp_path, monkeypatch):
    """Из установленных версий AE берётся самая свежая (2026), а не первая в списке:
    пресет «Untitled 1» лежит только в 26.2, и подменять версию нельзя. Папка без
    aerender.exe установкой не считается."""
    _fake_ae_tree(tmp_path, "2023")
    _fake_ae_tree(tmp_path, "2025")
    _fake_ae_tree(tmp_path, "2026")
    (tmp_path / "Adobe" / "Adobe After Effects 2024" / "Support Files").mkdir(parents=True)  # нет .exe
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    afx, aer, name = render._find_ae()
    assert "2026" in name and afx.endswith("AfterFX.exe") and aer.endswith("aerender.exe")
    assert os.path.dirname(afx) == str(tmp_path / "Adobe" / "Adobe After Effects 2026" / "Support Files")


def test_find_ae_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    assert render._find_ae() is None


def _fileurl(p):
    """file://localhost/… как в Premiere XML — для подмены эталонных путей фикстуры."""
    return "file://localhost/" + urllib.parse.quote(str(p).replace("\\", "/"), safe="/:")


def _preflight(jobs, tmp_path, monkeypatch):
    """Прогнать _run_render_job ровно до порога запуска AE (find_ae подменён, чтобы
    не искать After Effects на этой машине). Возвращает (find_ae_вызван, RJOB)."""
    render.RJOB.update(running=True, done=False, log=[], pct=None, cur="", ae="",
                       out_dir="", result=[], failed=[], cancel=False)
    reached = []
    monkeypatch.setattr(render, "_find_ae", lambda: reached.append(1) or None)
    render._run_render_job(jobs, "", str(tmp_path / "exp"))
    return bool(reached), render.RJOB


def _ui_job(tmp_path):
    """Джоб рендера как его собирает интерфейс: с интро и вставками, которых в сыром
    XML нет (intro_remove вынимает слова интро из дорожки, вставки живут только в UI).
    Пути камер фикстуры (C:/footage/…) подменены на реальные файлы — предполёт обязан
    проверять пропажи, а не спотыкаться об эталонные пути."""
    xml = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(xml, "wb") as f:
        shutil.copyfileobj(g, f)
    cam1 = tmp_path / "cam1.mp4"
    cam2 = tmp_path / "cam2.mp4"
    cam1.write_bytes(b"")
    cam2.write_bytes(b"")
    ins = tmp_path / "ins.png"
    ins.write_bytes(b"")
    src = open(xml, encoding="utf-8").read()
    src = src.replace("file://localhost/C%3a/footage/cam1/CLIP-006.MP4", _fileurl(cam1))
    src = src.replace("file://localhost/C%3a/footage/cam2/CLIP-006.MP4", _fileurl(cam2))
    open(xml, "w", encoding="utf-8").write(src)
    return [dict(xml=xml, outdir=str(tmp_path / "jsx"), music_random=False, censor=True,
                 inserts=[dict(type="photo", style="cam2", media=str(ins), start_s=1.0,
                               start_f=0, dur_s=2.0, dur_f=0, scale=44, mosaic=False,
                               x=0, y=0, sc=100, mw=100, mh=100, sin=0)],
                 intro=[dict(words=["ПЕРВОЕ"], color="white", times=[1.0])],
                 intro_remove=[0, 1], intro_splits=[], intro_mode="word", cams=2,
                 exposure=0, roto=False, roto_bottom=0.35, style=None)]


def test_preflight_passes_for_ui_intro_and_inserts(tmp_path, monkeypatch):
    """Клип с интро и вставками из интерфейса проходит предполёт (задание BJ): рендер
    собирает их поверх голого XML, и сверка числа слов/вставок с ним ВСЕГДА врёт —
    cross_check_xml валила бы тут «214 → 188» и «0 → 15», как на реальном прогоне
    2026-08-14. Пропажи файлов при этом по-прежнему проверяются (см. тест ниже)."""
    reached, rjob = _preflight(_ui_job(tmp_path), tmp_path, monkeypatch)
    # Лог в сообщение: без него падение на CI выглядело как «assert False» и не говорило
    # ни слова о причине (2026-08-14 — часовой разбор из-за одной непечатной строки).
    assert reached, ("предполёт не пропустил клип с интро и вставками из интерфейса\n"
                     + "\n".join(rjob["log"][-25:]))
    names = [f["name"] for f in rjob["failed"]]
    assert names == ["AE"], "предполёт нашёл ошибки кроме отсутствующего AE: %r" % names
    # Одиночный рендер: элементы очереди заведены РОВНО один раз (диспетчер) и со стемом
    # клипа. Дубль items_init внутри одиночного пути переинициализировал бы список —
    # это ловушка: появится работа между вызовами, состояние молча сотрётся (задание FH).
    items = rjob.get("items") or []
    assert len(items) == 1, "элементы очереди заведены не один раз: %r" % items
    assert items[0]["name"] == "timeline", "стем элемента не тот: %r" % items[0]["name"]
    assert items[0]["stage"] in ("wait", "check", "jsx"), "элемент не в рабочем этапе"


def test_preflight_blocks_missing_file(tmp_path, monkeypatch):
    """Пропавший с диска файл по-прежнему валит ренер до запуска AE — ради этого
    предполёт и существует (задание BD: -noui пишет пропажу в $.writeln и уходит в
    никуда, на выходе .mov без куска камеры)."""
    jobs = _ui_job(tmp_path)
    os.remove(str(tmp_path / "cam2.mp4"))     # после сборки .jsx файл «пропал»
    reached, rjob = _preflight(jobs, tmp_path, monkeypatch)
    assert not reached, "предполёт пропустил рендер с пропавшим файлом камеры"
    assert any("файла нет на диске" in f["reason"] for f in rjob["failed"]), (
        "нет ошибки о пропавшем файле: %r" % [f["reason"] for f in rjob["failed"]])


def test_short_path_has_no_spaces(tmp_path):
    """Короткое 8.3-имя — без пробелов по построению; на томе без 8.3 — None (задание CD)."""
    d = str(tmp_path / "папка с пробелом")
    os.makedirs(d)
    p = os.path.join(d, "01 РИЛС 12.jsx")
    open(p, "w").write("x")
    sp = render._short_path(p)
    assert sp is None or (" " not in sp), sp


def test_jsx_call_path_falls_back_to_spacefree_copy(tmp_path, monkeypatch):
    """8.3 отключён (None) — копия .jsx под именем без пробелов рядом с целевым, и об этом
    не молчим (задание CD): иначе рендер тихо не запустится. Путь к .aep при этом не
    меняется — он задан явной константой в хвосте."""
    monkeypatch.setattr(render, "_short_path", lambda p: None)
    d = str(tmp_path / "out")
    os.makedirs(d)
    jp = os.path.join(d, "01 РИЛС 12.jsx")
    open(jp, "w").write("var x=1;")
    call, why = render._jsx_call_path(jp)
    assert os.path.basename(call) == "01РИЛС12_headless.jsx", call
    assert " " not in call
    assert open(call, encoding="utf-8-sig").read() == "var x=1;"
    assert "не вышло" in why


def test_jsx_call_path_short_when_available(tmp_path, monkeypatch):
    """Есть короткое имя — зовём его, копию не создаём, причины нет."""
    d = str(tmp_path / "dir")
    os.makedirs(d)
    jp = os.path.join(d, "a b.jsx")
    open(jp, "w").write("x")
    if not render._short_path(jp):
        pytest.skip("том не поддерживает 8.3-имена (или не Windows)")
    call, why = render._jsx_call_path(jp)
    assert " " not in call
    assert not why
    assert not os.path.exists(os.path.join(d, "ab_headless.jsx"))


def test_aep_call_path_short_when_available(tmp_path, monkeypatch):
    """Есть короткое 8.3-имя — зовём его, копию не создаём, причины нет (задание EV)."""
    d = str(tmp_path / "dir")
    os.makedirs(d)
    aep = os.path.join(d, "03_РИЛС 3 22.08.aep")
    open(aep, "w").write("x")
    if not render._short_path(aep):
        pytest.skip("том не поддерживает 8.3-имена (или не Windows)")
    call, why = render._aep_call_path(aep)
    assert os.path.basename(call).isascii(), call
    assert " " not in call
    assert not why
    assert os.listdir(d) == ["03_РИЛС 3 22.08.aep"], os.listdir(d)


def test_aep_call_path_falls_back_to_ascii_copy(tmp_path, monkeypatch):
    """8.3 отключён (None) — копия .aep под ASCII-именем с хэшем рядом с целевым, причина
    непустая (задание EV): aerender читает -project как ANSI, и на кодовой странице 1252
    кириллица превращается в '????' — «Path is not valid», рендер не запускается."""
    monkeypatch.setattr(render, "_short_path", lambda p: None)
    d = str(tmp_path / "out")
    os.makedirs(d)
    aep = os.path.join(d, "03_РИЛС 3 22.08.aep")
    open(aep, "w").write("x")
    call, why = render._aep_call_path(aep)
    assert os.path.basename(call).isascii(), call
    assert call != aep
    assert os.path.isfile(call)
    assert "не вышло" in why


def test_aep_call_path_hashes_distinct_cyrillic_stems(tmp_path, monkeypatch):
    """Два разных кириллических стема дают РАЗНЫЕ имена копий — хэш обязателен: санитизация
    «РИЛС» и «РИЛТ» даёт один и тот же ASCII-префикс, и без хэша копии затёрли бы друг друга
    (задание EV)."""
    monkeypatch.setattr(render, "_short_path", lambda p: None)
    d = str(tmp_path / "out")
    os.makedirs(d)
    a1 = os.path.join(d, "03_РИЛС 3 22.08.aep")
    a2 = os.path.join(d, "03_РИЛТ 3 22.08.aep")
    open(a1, "w").write("x")
    open(a2, "w").write("x")
    c1, _ = render._aep_call_path(a1)
    c2, _ = render._aep_call_path(a2)
    assert c1 != c2


def test_rendered_ok_requires_real_file(tmp_path):
    """«Готово» только по факту файла: отсутствующий и нулевой — False, непустой — True.
    aerender вернул 0 на «Path is not valid», коду возврата не верим (задание EV)."""
    miss = str(tmp_path / "нет.mov")
    empty = str(tmp_path / "empty.mov")
    full = str(tmp_path / "full.mov")
    open(empty, "w").close()
    open(full, "w").write("x")
    assert not render._rendered_ok(miss)
    assert not render._rendered_ok(empty)
    assert render._rendered_ok(full)


def test_render_status_exposes_items():
    """/api/render_status отдаёт items наравне с остальным состоянием (задание FA)."""
    import api
    from flask import Flask

    render.RJOB.update(running=False, done=True, log=[], pct=1.0, cur="", ae="",
                       out_dir="exp", result=["exp/01.mov"], failed=[], cancel=False,
                       items=[{"name": "01", "stage": "done", "pct": None,
                               "path": "exp/01.mov", "reason": ""}])
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    r = app.test_client().get("/api/render_status", headers={"Host": "127.0.0.1:5001"})
    assert r.status_code == 200
    body = r.get_json()
    assert "items" in body
    assert body["items"] == render.RJOB["items"]


def test_parse_output_to_real_aerender_string():
    """Имя композиции из Output To: (задание FL). Имя выходного файла — факт о композиции
    В НАЧАЛЕ её рендера."""
    line = r"PROGRESS:  Output To: C:\путь\C0233.mov"
    assert render._parse_output_to(line) == "C0233"
    assert render._parse_output_to("PROGRESS:  Output To: C:/render/C0233.mov") == "C0233"
    assert render._parse_output_to(r'PROGRESS:  Output To: "C:\output\C0233.mov"') == "C0233"
    assert render._parse_output_to("PROGRESS: Launching After Effects...") is None


def test_parse_ae_header_frames_real_aerender_block():
    """Число кадров композиции — от AE, из блока Start/End/Duration/Frame Rate (задание FL)."""
    line_start = "PROGRESS:  Start: 0:00:00:00"
    line_end = "PROGRESS:  End: 0:00:00:02"
    line_dur = "PROGRESS:  Duration: 0:00:00:03"
    line_fps = "PROGRESS:  Frame Rate: 60.00 (comp)"

    fps = render._parse_frame_rate(line_fps)
    assert fps == 60.0

    m_dur = render._TC_DUR.search(line_dur)
    assert m_dur and m_dur.group(1) == "0:00:00:03"
    assert render._parse_timecode(m_dur.group(1), fps) == 3

    m_start = render._TC_START.search(line_start)
    m_end = render._TC_END.search(line_end)
    assert m_start and m_end
    sf = render._parse_timecode(m_start.group(1), fps)
    ef = render._parse_timecode(m_end.group(1), fps)
    assert sf == 0
    assert ef == 2
    assert ef - sf + 1 == 3


def test_finished_comp_name_real_aerender_string():
    """Finished composition из реального вывода: имя без кавычек, точки и даты (задание FL)."""
    line = 'PROGRESS:  8/26/2026 1:09:51 AM: Finished composition "C0233".'
    assert render._finished_comp_name(line) == "C0233"


def test_frame_number_starts_at_1():
    """Номер кадра в скобках начинается с 1 (0:00:00:00 (1)), 3 кадра -> 1/3, 2/3, 1.0 (задание FL)."""
    assert render._parse_progress("PROGRESS:  0:00:00:00 (1): 0 Seconds", 3) == pytest.approx(1 / 3)
    assert render._parse_progress("PROGRESS:  0:00:00:01 (2): 0 Seconds", 3) == pytest.approx(2 / 3)
    assert render._parse_progress("PROGRESS:  0:00:00:02 (3): 0 Seconds", 3) == pytest.approx(3 / 3)


def test_run_proc_batch_identifies_comp_from_output_to_and_header():
    """_run_proc_batch определяет имя из Output To: и кадры из блока AE на реальном выводе aerender (задание FL)."""
    raw_lines = [
        "PROGRESS: Launching After Effects...",
        "PROGRESS:  Start: 0:00:00:00",
        "PROGRESS:  End: 0:00:00:02",
        "PROGRESS:  Duration: 0:00:00:03",
        "PROGRESS:  Frame Rate: 60.00 (comp)",
        "PROGRESS:  Output Module: Untitled 1",
        r"PROGRESS:  Output To: C:\путь\C0233.mov",
        "PROGRESS:  Format: QuickTime",
        "PROGRESS:  0:00:00:00 (1): 0 Seconds",
        "PROGRESS:  0:00:00:01 (2): 0 Seconds",
        "PROGRESS:  0:00:00:02 (3): 0 Seconds",
        'PROGRESS:  8/26/2026 1:09:51 AM: Finished composition "C0233".',
        "PROGRESS:  Total Time Elapsed: 4 Seconds",
    ]

    comps = [("01_C0233", "C0233", 100)]  # 100 — запасное число кадров из .jsx

    class FakePopen:
        def __init__(self, *args, **kwargs):
            self.stdout = iter(raw_lines)
            self.pid = 12345

        def poll(self):
            return 0

        def wait(self):
            return 0

    render.RJOB.update(running=True, done=False, log=[], pct=None, cur="", ae="",
                       out_dir="", result=[], failed=[], cancel=False,
                       items=[{"name": "01_C0233", "stage": "render", "pct": None, "path": "", "reason": ""}])

    orig_popen = render.subprocess.Popen
    try:
        render.subprocess.Popen = FakePopen
        rc = render._run_proc_batch("fake_aerender", "fake_proj.aep", comps, "fake_out")
        assert rc == 0
        assert render.RJOB["pct"] == 1.0
        item = render.RJOB["items"][0]
        assert item["pct"] == 1.0
        assert item["stage"] == "done"
    finally:
        render.subprocess.Popen = orig_popen

