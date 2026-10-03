# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Прожиг LUT спикера в видео камер перед сборкой `.jsx` (core/lutbake.py).

ffmpeg здесь НЕ запускается: подменены и `subprocess.Popen` (сам прожиг), и
`encoders.pick` (проба кодека — это тоже запуск ffmpeg), и `media.probe_duration`
(ffprobe — знаменатель прогресса). Проверяются контракты:

* `baked_path` — имя кеша от исходника, таблицы и кодека: сменилось что-то из трёх
  — путь другой, ничего не менялось — тот же;
* `bake` — команда ffmpeg (`lut3d` с ЭКРАНИРОВАННЫМ путём к `.cube`, звук копией,
  аргументы мастера), готовый файл не пересобирается, отмена убивает процесс и
  убирает временный файл, сбой ffmpeg — `ReelsiError` с кодом `lut_bake_failed`;
* `baked_cams_for_build` — спикер и его `lut` из `<стем>.project.json`, битая
  таблица не роняет сборку (камера без LUT), сбой прожига роняет;
* сборка `.jsx` — прожжённый путь уезжает в `var CAM=`, а план превью
  (`plan["cams"]`, его читает `/api/scene`) остаётся на оригиналах; без LUT `.jsx`
  побайтово прежний (эталон tests/fixtures/golden_geometry.jsx).

Запуск:  python -m pytest tests/test_lutbake.py -q
"""
from __future__ import annotations

import gzip
import io
import json
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

import api  # noqa: E402  Flask-приложение (роут удаления нарезки)
from core import encoders  # noqa: E402
from core import lutbake, speakers  # noqa: E402
from core import media  # noqa: E402
from core import verify_jsx  # noqa: E402
from core.project_file import write_project  # noqa: E402
from core.umsg import ReelsiError  # noqa: E402
from core.xml2ae import build as xml2ae_build  # noqa: E402
from core.xml2ae.parse import Cancelled  # noqa: E402
from tests.test_geometry_python import _build as _golden_build  # noqa: E402
from tests.test_geometry_python import _mask_assets  # noqa: E402

GOLDEN = os.path.join(HERE, "fixtures", "golden_geometry.jsx")
# Аргументы «мастера» из core.encoders (hevc_nvenc 10 бит): в тестах проба кодека
# подменена, поэтому набор заведомо свой — он же и в ключе кеша.
MASTER = ["-c:v", "hevc_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "18",
          "-b:v", "0", "-pix_fmt", "p010le", "-profile:v", "main10", "-tag:v", "hvc1"]


def _noop(*_a, **_k):
    """emit-заглушка: строки лога в тестах не нужны."""
    return None


def log_to(lines):
    """emit, складывающий строки в список (проверяем предупреждения и этапы)."""
    def _emit(line="", **kw):
        lines.append(line)
    return _emit


# --------------------------------------------------------------------------- #
# Заглушки вместо внешних процессов
# --------------------------------------------------------------------------- #
class _Proc:
    """Процесс-заглушка для core.draftrender._run_ff (там subprocess.Popen).

    Прожиг идёт с `-progress pipe:1`, то есть stdout читает отдельный поток: отдаём
    ему строки прогресса. Файл-результат «пишет сам ffmpeg» — открываем путь из
    последнего аргумента команды; этим же путём проверяется уборка временного файла.
    """

    def __init__(self, cmd, rc, err, out, live):
        self.cmd = [str(c) for c in cmd]
        self._rc = rc
        self.returncode = None if live else rc
        self.stdout = io.StringIO(out)
        self.stderr = io.StringIO(err)
        self.killed = False
        self.created = False
        if rc == 0:
            path = self.cmd[-1]
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(b"FFMPEG-OUT")           # «готовое видео»
            self.created = True

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = 1

    def communicate(self, timeout=None):
        self.returncode = self._rc
        return self.stdout.read(), self.stderr.read()


class FakeFF:
    """Подменённый ffmpeg: команды копятся, файл-результат создаётся, код — свой.

    `live=True` — процесс «работает»: poll() не отдаёт код, пока его не убьют
    (так проверяется отмена).
    """

    def __init__(self, rc=0, err="", out="out_time_us=1000000\n", live=False):
        self.rc, self.err, self.out, self.live = rc, err, out, live
        self.procs: list[_Proc] = []

    def __call__(self, cmd, **_kw):
        p = _Proc(cmd, self.rc, self.err, self.out, self.live)
        self.procs.append(p)
        return p

    @property
    def cmds(self):
        return [p.cmd for p in self.procs]


class FakeNoFF(FakeFF):
    """ffmpeg, который НЕ должен запускаться вовсе."""

    def __call__(self, cmd, **_kw):
        raise AssertionError("ffmpeg запущен, хотя не должен: " + " ".join(str(c) for c in cmd))


@pytest.fixture(autouse=True)
def fake_codec(monkeypatch):
    """Проба кодека подменена: `encoders.pick` — тоже запуск ffmpeg.

    Возвращает функцию, которой тест может подменить набор аргументов (проверка
    «сменил кодек — сменился ключ кеша»).
    """
    def _set(args=None):
        choice = encoders.Choice(family="cpu", args=list(args or MASTER), label="Процессор")
        monkeypatch.setattr(lutbake.encoders, "pick", lambda *a, **k: choice)
        return choice
    _set()
    return _set


@pytest.fixture()
def ff(monkeypatch):
    """Подменённый ffmpeg (по умолчанию — успешный) вместо subprocess.Popen."""
    def _make(rc=0, err="", out="out_time_us=1000000\n", live=False):
        fake = FakeFF(rc=rc, err=err, out=out, live=live)
        monkeypatch.setattr(lutbake.subprocess, "Popen", fake)
        return fake
    return _make


@pytest.fixture()
def no_ffprobe(monkeypatch):
    """ffprobe не зовём: длительность исходника для прогресса — из заглушки."""
    monkeypatch.setattr(media, "probe_duration", lambda path: 3.0)


@pytest.fixture()
def speakers_dir(tmp_path, monkeypatch):
    """Профили спикеров — в tmp_path: в репозитории личные профили пользователя."""
    d = tmp_path / "speakers"
    d.mkdir()
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(d))
    return d


@pytest.fixture()
def xml_path(tmp_path):
    """XML нарезки: рядом с ним живёт сайдкар `.project.json`."""
    p = tmp_path / "clip.xml"
    p.write_text("<x/>", encoding="utf-8")
    return str(p)


@pytest.fixture()
def xml_subs(tmp_path):
    """Фикстура нарезки (2 камеры) — та же, что в golden-тестах геометрии."""
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


# --------------------------------------------------------------------------- #
# Таблицы .cube
# --------------------------------------------------------------------------- #
def _identity(size: int = 2) -> str:
    """Тождественный LUT текстом: цвет на выходе равен входу.

    Порядок строк — как требует формат .cube: красный меняется быстрее всех.
    """
    out = ['TITLE "identity"', "", "LUT_3D_SIZE %d" % size]
    step = 1.0 / (size - 1)
    for b in range(size):
        for g in range(size):
            for r in range(size):
                out.append("%.6f %.6f %.6f" % (r * step, g * step, b * step))
    return "\n".join(out) + "\n"


def _cube(tmp_path, name="cam1.cube", size=2, text=None) -> str:
    d = tmp_path / "LUT таблица"               # пробел и кириллица в пути — не случайно
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text(text if text is not None else _identity(size), encoding="utf-8")
    return str(p)


def _cam(tmp_path, name="cam1.mp4", data=b"fake camera file") -> str:
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def _speaker(key="Спикер", lut=None) -> dict:
    prof = {"label": key}
    if lut is not None:
        prof["lut"] = lut
    speakers.save(key, prof)
    return speakers.load(key) or {}


def _sidecar(xml_path, speaker=None, cams=None) -> None:
    """Сайдкар нарезки рядом с XML — так его пишет нарезка (core.project_file)."""
    proj = {"cams": cams or [], "offsets": [0.0], "fps": 60, "keep": [[0.0, 1.0]]}
    if speaker:
        proj["speaker"] = speaker
    write_project(os.path.splitext(xml_path)[0] + ".project.json", proj)


def _baked_of(tmp_path, src) -> str:
    """Путь, на который указывает прожжённый файл (создаём его: «кеш готов»)."""
    d = tmp_path / "_graded"
    d.mkdir(exist_ok=True)
    p = d / (os.path.splitext(os.path.basename(src))[0] + "-0123456789.mov")
    p.write_bytes(b"FFMPEG-OUT")
    return str(p)


# --------------------------------------------------------------------------- #
# 1. Ключ кеша: baked_path
# --------------------------------------------------------------------------- #
def test_baked_path_зависит_от_таблицы_времени_и_кодека(tmp_path, fake_codec):
    """Имя кеша: тот же набор — тот же путь; сменился LUT/mtime/кодек — новый."""
    src = _cam(tmp_path)
    cube_a = _cube(tmp_path, "a.cube", size=2)
    cube_b = _cube(tmp_path, "b.cube", size=3)
    out = str(tmp_path)

    first = lutbake.baked_path(src, cube_a, out)
    assert first == lutbake.baked_path(src, cube_a, out), "повтор дал другое имя — кеш не работает"
    assert os.path.dirname(first) == os.path.join(out, "_graded")
    assert os.path.basename(first).startswith("cam1-") and first.endswith(".mov"), first

    assert lutbake.baked_path(src, cube_b, out) != first, "смена таблицы не сменила ключ"

    mtime = os.stat(cube_a).st_mtime + 100
    os.utime(cube_a, (mtime, mtime))
    assert lutbake.baked_path(src, cube_a, out) != first, "правка .cube на диске не сменила ключ"

    fake_codec(["-c:v", "libx265", "-crf", "18"])
    assert lutbake.baked_path(src, cube_a, out) != first, "смена кодека не сменила ключ"


def test_baked_path_зависит_от_исходника(tmp_path):
    """Переснял клип (mtime/размер) — прожжённый файл пересобирается, а не переиспользуется."""
    src = _cam(tmp_path)
    cube = _cube(tmp_path)
    first = lutbake.baked_path(src, cube, str(tmp_path))

    with open(src, "ab") as f:
        f.write(b"more bytes")
    assert lutbake.baked_path(src, cube, str(tmp_path)) != first


# --------------------------------------------------------------------------- #
# 2. Команда прожига: bake
# --------------------------------------------------------------------------- #
def test_bake_команда_фильтр_звук_и_мастер(tmp_path, ff, no_ffprobe):
    """`lut3d` с экранированным путём (диск, пробел, кириллица), звук копией, мастер."""
    src = _cam(tmp_path)
    cube = _cube(tmp_path, "мой LUT.cube")
    dst = lutbake.baked_path(src, cube, str(tmp_path))
    fake = ff()

    assert lutbake.bake(src, cube, dst, emit=_noop) == dst

    cmd = fake.cmds[0]
    vf = cmd[cmd.index("-vf") + 1]
    escaped = "lut3d=file='%s':interp=tetrahedral" % lutbake.filter_path(cube)
    assert vf == lutbake.lut_filter(cube), vf
    assert escaped in vf, vf
    for tag in encoders.COLOR_TAGS:
        assert tag in cmd
    assert cmd[cmd.index("-i") + 1] == src
    # Двоеточие экранируется ВЕЗДЕ, где оно есть (`:` разделяет параметры фильтра), но
    # берётся оно из пути: «C:» есть только на Windows, на POSIX в tmp_path его нет —
    # там экранировать нечего, и требовать «\\:» значило бы проверять несуществующий путь.
    if ":" in cube:
        inner = escaped.split("':interp=", 1)[0]        # только путь внутри кавычек
        assert inner.count(":") == inner.count("\\:") == 1, \
            "двоеточие диска не экранировано: " + escaped
        assert "C\\:" in escaped, "диск не экранирован у буквы: " + escaped
    else:
        # Путь без двоеточия: в САМОМ пути экранировать нечего. Двоеточие в конце
        # строки — разделитель параметров фильтра (`:interp=`), он законен и не
        # экранируется: проверять «нет `:` во всём фильтре» значило бы ловить его.
        inner = escaped.split("':interp=", 1)[0]        # только путь внутри кавычек
        assert ":" not in inner, "в пути без двоеточия появилось экранирование: " + escaped
    assert "'" in escaped, "путь не завёрнут в кавычки фильтра: " + escaped
    assert cmd[cmd.index("-i") + 1] == src
    assert cmd[cmd.index("-map") + 1] == "0:v:0"
    assert cmd[cmd.index("-c:a") + 1] == "copy", "звук исходника перекодируется"
    assert "-map_metadata" in cmd
    # Аргументы мастера — ровно те, что отдал encoders.pick (HEVC 10 бит)
    joined = " ".join(cmd)
    for arg in MASTER:
        assert arg in joined, "в команде нет аргумента мастера: " + arg
    assert os.path.isfile(dst), "результат прожига не появился"
    assert not os.path.exists(dst + ".part.mov"), "временный файл остался"


def test_filter_path_экранирует_диск_и_кавычку():
    """Путь для фильтрграфа: прямые слеши, `\\:` у диска, `\\'` у апострофа."""
    assert lutbake.filter_path(r"C:\LUT\cam 1.cube") == "C\\:/LUT/cam 1.cube"
    assert lutbake.filter_path("/mnt/l'ut/x.cube") == "/mnt/l\\'ut/x.cube"


def test_bake_готовый_файл_не_пересобирается(tmp_path, monkeypatch, no_ffprobe):
    """Файл уже прожжён этим же набором (исходник + LUT + кодек) — ffmpeg не зовём."""
    src = _cam(tmp_path)
    cube = _cube(tmp_path)
    dst = lutbake.baked_path(src, cube, str(tmp_path))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "wb") as f:
        f.write(b"already baked")
    monkeypatch.setattr(lutbake.subprocess, "Popen", FakeNoFF())

    assert lutbake.bake(src, cube, dst, emit=_noop) == dst


def test_bake_сбой_ffmpeg_даёт_понятную_ошибку(tmp_path, ff, no_ffprobe):
    """ffmpeg упал: ReelsiError lut_bake_failed с хвостом stderr, временного файла нет."""
    src = _cam(tmp_path)
    cube = _cube(tmp_path)
    dst = lutbake.baked_path(src, cube, str(tmp_path))
    ff(rc=1, err="line one\nError opening input file clip.mp4\n")

    with pytest.raises(ReelsiError) as e:
        lutbake.bake(src, cube, dst, emit=_noop)

    assert e.value.code == "lut_bake_failed"
    assert "Error opening input file clip.mp4" in str(e.value), str(e.value)
    assert not os.path.exists(dst)
    assert not os.path.exists(dst + ".part.mov")


# --------------------------------------------------------------------------- #
# 6. Отмена: процесс убит, временный файл убран
# --------------------------------------------------------------------------- #
def test_bake_отмена_убивает_процесс_и_убирает_временный(tmp_path, ff, no_ffprobe):
    """«Стоп» во время прожига: ffmpeg убит, недописанного файла не осталось."""
    src = _cam(tmp_path)
    cube = _cube(tmp_path)
    dst = lutbake.baked_path(src, cube, str(tmp_path))
    fake = ff(live=True)
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 2          # первый опрос — до запуска, дальше «Стоп»

    with pytest.raises(Cancelled):
        lutbake.bake(src, cube, dst, emit=_noop, cancel=cancel)

    p = fake.procs[0]
    assert p.created, "ffmpeg даже не начал писать — тест проверяет не то"
    assert p.killed, "процесс ffmpeg не убит"
    assert not os.path.exists(dst + ".part.mov"), "временный файл остался"
    assert not os.path.exists(dst), "недописанный файл подменён на место готового"


# --------------------------------------------------------------------------- #
# 4. Кто прожигается: baked_cams_for_build
# --------------------------------------------------------------------------- #
def test_baked_cams_пусто_без_сайдкара_спикера_и_lut(xml_path, speakers_dir, tmp_path):
    """Нет сайдкара / нет спикера / нет `lut` — {} и ни одного запуска ffmpeg."""
    cams = [{"path": _cam(tmp_path)}]

    assert lutbake.baked_cams_for_build(xml_path, cams, emit=_noop, cancel=None) == {}

    _sidecar(xml_path, cams=[cams[0]["path"]])
    assert lutbake.baked_cams_for_build(xml_path, cams, emit=_noop, cancel=None) == {}

    _speaker()
    _sidecar(xml_path, speaker="Спикер", cams=[cams[0]["path"]])
    assert lutbake.baked_cams_for_build(xml_path, cams, emit=_noop, cancel=None) == {}


def test_baked_cams_только_камера_с_lut(xml_path, speakers_dir, tmp_path, ff, no_ffprobe):
    """LUT задан камере 2 — прожигается только она; список уходит в `.graded.json`."""
    cam1, cam2 = _cam(tmp_path, "cam1.mp4"), _cam(tmp_path, "cam2.mp4")
    cube2 = _cube(tmp_path, "для второй.cube")
    _speaker(lut={"2": cube2})
    _sidecar(xml_path, speaker="Спикер", cams=[cam1, cam2])
    fake = ff()

    mp = lutbake.baked_cams_for_build(xml_path, [{"path": cam1}, {"path": cam2}],
                                      emit=_noop, cancel=None)

    assert set(mp) == {cam2}, "прожглась не та камера: " + repr(mp)
    assert mp[cam2] == lutbake.baked_path(cam2, cube2, os.path.dirname(os.path.abspath(xml_path)))
    assert os.path.isfile(mp[cam2])
    assert len(fake.cmds) == 1, "ffmpeg позвали не один раз"
    assert lutbake.filter_path(cube2) in fake.cmds[0][fake.cmds[0].index("-vf") + 1]

    sidecar = json.loads(open(lutbake.graded_json_path(xml_path), encoding="utf-8").read())
    assert sidecar["paths"] == [mp[cam2]], "список прожжённых не записан рядом с XML"
    assert lutbake.graded_paths(xml_path) == [mp[cam2]]


def test_baked_cams_битая_таблица_не_роняет_сборку(xml_path, speakers_dir, tmp_path,
                                                   monkeypatch):
    """Битый или пропавший `.cube` — предупреждение в лог, камера без LUT."""
    cam1 = _cam(tmp_path)
    broken = _cube(tmp_path, "broken.cube", text="LUT_3D_SIZE 2\n0.0 0.0 0.0\n")
    missing = str(tmp_path / "нет-такого.cube")
    monkeypatch.setattr(lutbake.subprocess, "Popen", FakeNoFF())
    lines = []

    _speaker(lut={"1": broken})
    _sidecar(xml_path, speaker="Спикер", cams=[cam1])
    assert lutbake.baked_cams_for_build(xml_path, [{"path": cam1}], emit=log_to(lines),
                                        cancel=None) == {}
    assert any("не читается" in ln for ln in lines), lines

    _speaker("Второй", lut={"1": missing})
    _sidecar(xml_path, speaker="Второй", cams=[cam1])
    assert lutbake.baked_cams_for_build(xml_path, [{"path": cam1}], emit=log_to(lines),
                                        cancel=None) == {}
    assert any("не найден" in ln for ln in lines), lines


def test_baked_cams_сбой_прожига_роняет_сборку(xml_path, speakers_dir, tmp_path, ff,
                                               no_ffprobe):
    """Сбой ffmpeg не глотается: сборка обязана упасть, а не собраться без цвета."""
    cam1 = _cam(tmp_path)
    cube = _cube(tmp_path)
    _speaker(lut={"1": cube})
    _sidecar(xml_path, speaker="Спикер", cams=[cam1])
    ff(rc=1, err="Invalid data found when processing input\n")

    with pytest.raises(ReelsiError) as e:
        lutbake.baked_cams_for_build(xml_path, [{"path": cam1}], emit=_noop, cancel=None)

    assert e.value.code == "lut_bake_failed"


def test_baked_cams_кеш_не_пересобирается(xml_path, speakers_dir, tmp_path, monkeypatch,
                                          no_ffprobe):
    """Готовый файл того же набора — «из кеша»: ffmpeg не зовём, путь тот же."""
    cam1 = _cam(tmp_path)
    cube = _cube(tmp_path)
    _speaker(lut={"1": cube})
    _sidecar(xml_path, speaker="Спикер", cams=[cam1])
    dst = lutbake.baked_path(cam1, cube, os.path.dirname(os.path.abspath(xml_path)))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "wb") as f:
        f.write(b"already baked")
    monkeypatch.setattr(lutbake.subprocess, "Popen", FakeNoFF())
    lines = []

    mp = lutbake.baked_cams_for_build(xml_path, [{"path": cam1}], emit=log_to(lines),
                                      cancel=None)

    assert mp == {cam1: dst}
    assert any("из кеша" in ln for ln in lines), lines


# --------------------------------------------------------------------------- #
# 5. Сборка .jsx: подстановка только в CAM, план превью — оригиналы
# --------------------------------------------------------------------------- #
def _jsx_of(xml, tmp_path, name="out.jsx") -> str:
    path, _n, _s = xml2ae_build.to_ae_full(
        xml, jsx_path=str(tmp_path / name), style={}, inserts=[],
        disclaimer="", intro_riser=False, emit=_noop)
    return open(path, encoding="utf-8-sig").read()


def test_to_ae_full_подставляет_прожжённый_путь(xml_subs, tmp_path, monkeypatch):
    """Прожжённый путь уезжает в `var CAM=`, оригинал камеры остаётся в плане превью."""
    _meta, cams, _subs, _ins = xml2ae_build.parse_full(xml_subs)
    orig1, orig2 = cams[0]["path"], cams[1]["path"]
    baked1 = str(tmp_path / "_graded" / "CLIP-006-abcdef1234.mov")
    seen = {}

    def fake_bake(xml_path, plan_cams, emit=None, cancel=None):
        seen["cams"] = [c["path"] for c in plan_cams]
        return {orig1: baked1}

    monkeypatch.setattr(lutbake, "baked_cams_for_build", fake_bake)
    jsx = _jsx_of(xml_subs, tmp_path, "baked.jsx")

    cam_js = verify_jsx.extract_structs(jsx)["CAM"]
    assert cam_js[0]["path"] == baked1, "в .jsx уехал оригинал камеры 1"
    assert cam_js[1]["path"] == orig2, "камера без LUT переписана"
    assert [c["path"] for c in cam_js] == [baked1, orig2]
    assert cam_js[0]["clips"] == [list(cl) for cl in cams[0]["clips"]], \
        "клипы камеры поехали вместе с путём"
    assert seen["cams"] == [orig1, orig2], "прожигу отдали не оригиналы камер"

    plan = xml2ae_build.scene_plan(xml_subs, style={}, inserts=[], disclaimer="",
                                   intro_riser=False, emit=_noop)
    assert plan["cams"][0]["path"] == orig1, "план превью подменил путь камеры"


def test_scene_plan_не_прожигает(xml_subs, tmp_path, speakers_dir, monkeypatch):
    """Превью (`/api/scene` зовёт тот же scene_plan) прожигать не должен вовсе."""
    monkeypatch.setattr(lutbake, "bake",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("прожиг в плане")))
    _speaker(lut={"1": _cube(tmp_path)})
    _sidecar(xml_subs, speaker="Спикер")

    plan = xml2ae_build.scene_plan(xml_subs, style={}, inserts=[], disclaimer="",
                                   intro_riser=False, emit=_noop)

    assert plan["cams"][0]["path"] == xml2ae_build.parse_full(xml_subs)[1][0]["path"]
    assert not os.path.isdir(os.path.join(tmp_path, "_graded")), "план превью создал _graded"


def test_без_lut_jsx_побайтово_прежний(xml_subs, tmp_path, monkeypatch):
    """Прожигать нечего — .jsx совпадает с эталоном: подстановки камер нет вовсе."""
    monkeypatch.setattr(lutbake, "baked_cams_for_build", lambda *a, **k: {})
    jsx = _mask_assets(_golden_build(xml_subs, tmp_path))
    golden = _mask_assets(open(GOLDEN, encoding="utf-8-sig").read())
    assert jsx == golden, "сборка без LUT разошлась с эталоном"


def test_без_сайдкара_cam_прежний(xml_subs, tmp_path):
    """Обычная нарезка без сайдкара: сборка ничего не делает с путями камер."""
    _meta, cams, _subs, _ins = xml2ae_build.parse_full(xml_subs)
    cam_js = verify_jsx.extract_structs(_jsx_of(xml_subs, tmp_path))["CAM"]
    assert [c["path"] for c in cam_js] == [c["path"] for c in cams]


# --------------------------------------------------------------------------- #
# 3. Сторож .jsx: прожжённый .mov законен, AV1 — нет
# --------------------------------------------------------------------------- #
def test_verify_jsx_прожжённый_mov_законен(tmp_path, monkeypatch):
    """HEVC в `_graded/*.mov` — обычный файл камеры; AV1 по-прежнему роняет проверку."""
    gdir = tmp_path / "_graded"
    gdir.mkdir()
    mov = gdir / "CLIP-006-abcdef1234.mov"
    mov.write_bytes(b"\x00" * 64)
    cam = [{"path": str(mov), "name": "cam1", "clips": [[0, 60, 0, 60, True, 100]]}]

    monkeypatch.setattr(verify_jsx, "_codec_cached", lambda path: "hevc")
    rep = verify_jsx.Report("cam")
    verify_jsx.check_cam(cam, rep)
    assert rep.ok, "сторож ругается на прожжённый HEVC: %s" % rep.errors

    monkeypatch.setattr(verify_jsx, "_codec_cached", lambda path: "av1")
    rep = verify_jsx.Report("cam")
    verify_jsx.check_cam(cam, rep)
    assert any("AV1" in e for e in rep.errors), "AV1-видео прошло проверку: %s" % rep.errors


# --------------------------------------------------------------------------- #
# Уборка: прожжённые файлы удаляются вместе с нарезкой
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client():
    """Тестовый клиент Flask: приложение собирается из блюпринта (как в tests/test_lut.py)."""
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def test_уборка_нарезки_удаляет_прожжённые(tmp_path, client):
    """`/api/clip_delete` берёт прожжённые файлы из `<стем>.graded.json` и удаляет их.

    Путь в сайдкаре, ведущий ЗА пределы своей `_graded`, не трогаем: список — обычный
    JSON рядом с XML, и удалять по нему что угодно в другом месте нельзя.
    """
    outdir = tmp_path / "out"
    outdir.mkdir()
    xml = outdir / "01_clip.xml"
    xml.write_text("<xmeml/>", encoding="utf-8")
    write_project(str(outdir / "01_clip.project.json"), {"cams": [str(tmp_path / "cam1.mp4")]})
    gdir = outdir / "_graded"
    gdir.mkdir()
    baked = gdir / "cam1-0123456789.mov"
    baked.write_bytes(b"FFMPEG-OUT")
    alien = tmp_path / "чужой.mov"                 # НЕ в _graded: удалять нельзя
    alien.write_bytes(b"NOT OURS")
    (outdir / "01_clip.graded.json").write_text(
        json.dumps({"paths": [str(baked), str(alien)]}), encoding="utf-8")

    H = {"Host": "127.0.0.1:5001"}
    dry = client.post("/api/clip_delete", json={"xml": str(xml), "dry": True}, headers=H)
    assert dry.status_code == 200
    listed = [f["path"] for f in dry.get_json()["files"]]
    assert str(baked) in listed, "сухой прогон не показал прожжённый файл: %s" % listed
    assert str(alien) not in listed, "в список на удаление попал чужой путь"

    r = client.post("/api/clip_delete", json={"xml": str(xml), "dry": False}, headers=H)
    assert r.status_code == 200 and r.get_json()["ok"]
    assert not os.path.exists(baked), "прожжённый файл остался после удаления нарезки"
    assert not gdir.exists(), "пустая папка _graded осталась"
    assert alien.read_bytes() == b"NOT OURS", "тронуто что-то за пределами _graded"


def test_lut_discrepancy_within_one_percent(tmp_path, monkeypatch):
    """Расхождение цвета между превью и запечённым LUT <= 1% по всем RGB-каналам."""
    import subprocess
    import numpy as np

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not in PATH")

    # Кодек мастера задаём ЯВНО (`cpu`), а не «что выбрал авто»: аппаратный HEVC в
    # контейнере CI не поднимается (нет libcuda), а проба семейства там врёт — она
    # кодирует чёрный кадр lavfi и ошибку драйвера не видит. Тест про ЦВЕТ LUT, и
    # кодировщик ему безразличен; заодно число и команда перестают зависеть от машины.
    monkeypatch.setattr(encoders, "video_encoder_cfg", lambda: "cpu")
    cpu_codec = encoders.codec_name("cpu", "master")            # libx265
    if not encoders.probe("cpu", "master"):
        pytest.skip("нет кодировщика CPU (%s) — живой прожиг не на чем проверить"
                    % cpu_codec)

    cube_path = str(tmp_path / "test.cube")
    with open(cube_path, "w", encoding="utf-8") as f:
        f.write("TITLE \"Identity\"\nLUT_3D_SIZE 2\n")
        f.write("0.0 0.0 0.0\n1.0 0.0 0.0\n0.0 1.0 0.0\n1.0 1.0 0.0\n")
        f.write("0.0 0.0 1.0\n1.0 0.0 1.0\n0.0 1.0 1.0\n1.0 1.0 1.0\n")

    src_mp4 = str(tmp_path / "src.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=1280x720:d=1:r=25",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        src_mp4
    ], check=True, capture_output=True)

    baked_mov = str(tmp_path / "baked.mov")
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", src_mp4, "-map", "0:v:0", "-vf", lutbake.lut_filter(cube_path)]
    # Аргументы CPU-мастера берём из таблицы кодеков по ИМЕНИ, а не через
    # `lutbake.master_args()` (тот читает настройку из ai_config.json): тест обязан
    # кодировать процессором независимо от того, что выбрано на машине и откуда
    # взялась настройка. Иначе в контейнере без GPU команда уезжает на hevc_nvenc
    # и падает «Error while opening encoder» — тест про цвет, а не про железо.
    cmd += encoders.codec_args(cpu_codec, "master")
    cmd += encoders.COLOR_TAGS
    cmd += ["-map_metadata", "0", baked_mov]
    subprocess.run(cmd, check=True)

    lut = lutbake.lutlib.load_cube(cube_path)
    N = lut["size"]
    D = lut["data"]

    def sample(r, g, b):
        x, y, z = r * (N - 1), g * (N - 1), b * (N - 1)
        i0, j0, k0 = int(x), int(y), int(z)
        i1, j1, k1 = min(i0 + 1, N - 1), min(j0 + 1, N - 1), min(k0 + 1, N - 1)
        fx, fy, fz = x - i0, y - j0, z - k0
        out = [0.0, 0.0, 0.0]
        for ch in range(3):
            at = lambda i, j, k: D[((k * N + j) * N + i) * 3 + ch]
            a = at(i0, j0, k0) + (at(i1, j0, k0) - at(i0, j0, k0)) * fx
            b2 = at(i0, j1, k0) + (at(i1, j1, k0) - at(i0, j1, k0)) * fx
            c2 = at(i0, j0, k1) + (at(i1, j0, k1) - at(i0, j0, k1)) * fx
            d2 = at(i0, j1, k1) + (at(i1, j1, k1) - at(i0, j1, k1)) * fx
            e0 = a + (b2 - a) * fy
            e1 = c2 + (d2 - c2) * fy
            out[ch] = e0 + (e1 - e0) * fz
        return out

    p_src = subprocess.run(["ffmpeg", "-i", src_mp4, "-vframes", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], capture_output=True)
    p_dst = subprocess.run(["ffmpeg", "-i", baked_mov, "-vframes", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], capture_output=True)

    s_arr = np.frombuffer(p_src.stdout, dtype=np.uint8).reshape((-1, 3)).astype(float)[::10]
    d_arr = np.frombuffer(p_dst.stdout, dtype=np.uint8).reshape((-1, 3)).astype(float)[::10]

    expected = np.array([sample(r / 255.0, g / 255.0, b / 255.0) for r, g, b in s_arr]) * 255.0
    exp_mean = expected.mean(axis=0)
    dst_mean = d_arr.mean(axis=0)
    diff_pct = np.abs(dst_mean - exp_mean) / exp_mean * 100

    assert np.all(diff_pct <= 1.0), f"Discrepancy > 1%: {diff_pct}"
