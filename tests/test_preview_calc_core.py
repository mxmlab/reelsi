# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Пункт 1 задания: один расчёт для сборки и превью (рото и трекинг головы).

Раньше оба тяжёлых этапа жили прямо в `to_ae_full`: чтобы увидеть их в превью, надо
было либо ждать финального рендера, либо завести ВТОРУЮ копию расчёта. Теперь расчёт
вынесен в `core/xml2ae/precompute.py`, и сборка с превью зовут ОДНИ И ТЕ ЖЕ функции.

Проверяется без GPU (RVM/трекер подменены заглушками):

1. сборка СПРАШИВАЕТ предрасчёт (счётчик вызовов `precompute.head_track`), а не держит
   свою копию цикла по `cam1_head_follow`/`cam2_head_follow`;
2. трек, посчитанный предрасчётом, сборка НЕ пересчитывает: второй вызов с теми же
   аргументами берёт сайдкар `<стем>.head.json` из кэша;
3. маски, посчитанные предрасчётом, сборка не пересчитывает — кусок берётся готовым
   из `<base>/roto/_cache/camN`;
4. `precompute.cached_plan` (быстрая дверь превью) находит те же маски и тот же трек
   по кэшу — без расчёта;
5. в .jsx уезжает контракт масок (`ci/ts/te/cs/scale/mf/mask`), а поля превью
   (`path`/`src_start`) в него не протекают.
"""
import gzip
import json
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import headtrack as _headtrack  # noqa: E402
from core import roto as _roto  # noqa: E402
from core import xml2ae  # noqa: E402
from core.xml2ae import precompute  # noqa: E402
from core.xml2ae.build import _roto_js  # noqa: E402

STYLE = {"roto": True, "roto_bottom": 0.0, "cam1_head_follow": True}
# `roto` — параметр ПЛАНА (галку стиля достаёт нормализация входа, api/build.py),
# поэтому в вызовы он уезжает и отдельным аргументом, и в стиле: так делает боевой путь.
ROTO = dict(roto=True, roto_bottom=0.0)
XML_NAME = "timeline.xml"


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / XML_NAME)
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _plan(xml_subs, **kw):
    """План сцены с включённым рото и слежением — то же, что считает /api/scene."""
    return xml2ae.scene_plan(xml_subs, style=dict(STYLE), inserts=[],
                             include_xml_inserts=False,
                             emit=lambda *a, **k: None, **dict(ROTO, **kw))


@pytest.fixture()
def counters():
    """Счётчики вызовов заглушек: сколько раз реально считали (track/alpha)."""
    return {"track": 0, "alpha": 0, "alpha_cache": 0, "alpha_ranges": [], "head_track": 0}


# Пути камер обезличенной фикстуры: файлов с чужой машины на диске нет.
_CAM_PREFIX = "c:\\footage\\"
# Настоящий isfile: фикстура подменяет os.path.isfile, и «спросить у ФС» иначе не выйдет.
_REAL_ISFILE = os.path.isfile


def _isfile(path):
    """`os.path.isfile` для обезличенной фикстуры: камеры «существуют», остальное — правда.

    Камера фикстуры — путь с чужой машины; без этой подмены предрасчёт честно пропустил
    бы и трек, и рото, и проверять было бы нечего. Всё прочее (в том числе созданные
    тестом файлы масок и сайдкары) спрашиваем у настоящей ФС.
    """
    if str(path).replace("/", "\\").lower().startswith(_CAM_PREFIX):
        return True
    return _REAL_ISFILE(path)


@pytest.fixture(autouse=True)
def _no_gpu(monkeypatch, counters):
    """Заглушки GPU-расчёта: ни RVM, ни трекера.

    Проба `ffprobe` (её в тестах нет) отдаёт 1080p — этого довольно, чтобы ключ кэша
    масок совпал с тем, что посчитает `cached_plan`.
    """
    def fake_isfile(path):
        return _isfile(path)

    monkeypatch.setattr(os.path, "isfile", fake_isfile)
    monkeypatch.setattr(_roto, "_probe", lambda video: (1920, 1080, 60.0, "60/1"))
    # ffprobe («есть ли в маске кадры») в тестах не зовём: файл есть и непустой — этого
    # довольно, содержимое проверяет сам RVM, а его тут нет.
    monkeypatch.setattr(_roto, "_has_frames",
                        lambda path: _isfile(path) and os.path.getsize(path) > 0)

    def fake_track(video, ranges, emit=None, cancel=None, fps=10):
        counters["track"] += 1
        return {"v": 1, "fps": fps, "w": 1920, "h": 1080,
                "pts": [[0.0, 0.5], [10.0, 0.55]]}

    def fake_load_or_track(xml_path, video, ranges, emit=None, cancel=None, fps=10, cam=1):
        """Годный сайдкар — как у настоящего `load_or_track`: расчёта нет."""
        data = {"v": 1, "fps": fps, "w": 1920, "h": 1080, "pts": [[0.0, 0.5]],
                "video": os.path.abspath(video), "size": 1, "mtime": 0.0,
                "ranges": [list(r) for r in _headtrack.merge_ranges(ranges)]}
        if xml_path:
            path = _headtrack.head_cache_path(xml_path, cam)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        return data

    def fake_load_cached(xml_path, video, ranges=None, cam=1):
        if not xml_path or not os.path.isfile(_headtrack.head_cache_path(xml_path, cam)):
            return None
        return {"v": 1, "pts": [[0.0, 0.5]]}

    def fake_alpha_for_ranges(video, ranges, out_dir, bottom_pct=0.0, device=None,
                              seq_chunk=None, emit=None, cache_dir=None, cancel=None,
                              failures=None):
        """Маски как у настоящего `alpha_for_ranges`: имя — хэш камера+кусок, кэш — реюз.

        Второго расчёта по тому же куску не бывает: файл уже лежит в кэше, и заглушка
        отдаёт его БЕЗ счётчика. Счётчик `alpha` растёт только на настоящем расчёте —
        по нему и видно, что сборка не пересчитывает посчитанное предрасчётом.
        """
        os.makedirs(out_dir, exist_ok=True)
        res = []
        div = _roto._mask_scale_div(1080)
        counted = False
        for (s, e) in ranges:
            mask = os.path.join(out_dir, "roto_%s.mp4"
                                % _roto._mask_key(video, s, e, bottom_pct, div))
            if not os.path.isfile(mask):
                counted = True
                with open(mask, "wb") as f:
                    f.write(b"\x00\x00\x00\x18ftypmp42")     # «кадры есть»: _has_frames
            else:
                counters["alpha_cache"] += 1
            res.append({"start": float(s), "end": float(e), "mask": mask, "f": float(div)})
        if counted:
            counters["alpha"] += 1
            counters["alpha_ranges"].append(list(ranges))
        return res

    monkeypatch.setattr(_headtrack, "track", fake_track)
    monkeypatch.setattr(_headtrack, "load_or_track", fake_load_or_track)
    monkeypatch.setattr(_headtrack, "load_cached", fake_load_cached)
    monkeypatch.setattr(_roto, "alpha_for_ranges", fake_alpha_for_ranges)

    real_head_track = precompute.head_track

    def counting_head_track(*a, **kw):
        counters["head_track"] += 1
        return real_head_track(*a, **kw)

    monkeypatch.setattr(precompute, "head_track", counting_head_track)


def test_сборка_зовёт_предрасчёт_а_не_свою_копию(xml_subs, counters):
    """Пункт «одна функция»: у сборки нет своей копии цикла по камерам слежения.

    Считается вызов `precompute.head_track`: он обязан быть ровно один на сборку, и
    никакого второго расчёта внутри (счётчик заглушки `track` остаётся нулевым —
    сайдкар пишет `load_or_track` из кэша).
    """
    jsx = str(os.path.join(os.path.dirname(xml_subs), "out.jsx"))
    xml2ae.to_ae_full(xml_subs, jsx_path=jsx, inserts=[], style=dict(STYLE),
                      base=os.path.dirname(xml_subs), disclaimer="",
                      include_xml_inserts=False, emit=lambda *a, **k: None, **ROTO)

    assert counters["head_track"] == 1, (
        "сборка позвала предрасчёт %d раз — ждали ровно один" % counters["head_track"])
    assert counters["track"] == 0, "трек головы считался заново при готовом кэше"
    assert os.path.isfile(jsx), "сборка не записала .jsx"


def test_кэш_предрасчёта_сборка_не_пересчитывает(xml_subs, counters):
    """Трек и маски, посчитанные предрасчётом, сборка берёт готовыми.

    Первый проход считает и кладёт в кэш; второй проход обязан НЕ звать расчёт
    масок: файлы лежат в `<base>/roto/_cache/cam1`, и сборка находит их по тому же
    ключу. Это и есть «посчитанное в превью сборка берёт готовым».
    """
    base = os.path.dirname(xml_subs)
    out = os.path.join(base, "out.jsx")
    kw = dict(inserts=[], style=dict(STYLE), base=base, disclaimer="",
              include_xml_inserts=False, emit=lambda *a, **k: None, **ROTO)

    xml2ae.to_ae_full(xml_subs, jsx_path=out, **kw)
    first = counters["alpha"]
    assert first > 0, "первый проход не посчитал ни одной маски"
    assert counters["track"] == 0, "сайдкар трека уже готов — расчёта быть не должно"

    xml2ae.to_ae_full(xml_subs, jsx_path=out, **kw)
    assert counters["alpha"] == first, (
        "сборка пересчитала маски, хотя они лежат в общем кэше (было %d, стало %d)"
        % (first, counters["alpha"]))
    assert counters["head_track"] == 2, "предрасчёт зовётся на каждую сборку"


def test_маски_кэша_годны_для_md5_ключаниже(xml_subs, counters):
    """Имя маски сборки — хэш `roto._mask_key` от исходника и куска (один ключ на всех)."""
    plan = _plan(xml_subs)
    ents = precompute.roto_masks(plan, xml_subs, {"roto": True, "style": dict(STYLE),
                                                 "base": os.path.dirname(xml_subs)},
                                 emit=lambda *a, **k: None, strict=False)
    assert ents, "предрасчёт не отдал ни одной маски"
    e = ents[0]
    assert set(e) >= {"ci", "ts", "te", "cs", "scale", "mf", "mask"}
    assert os.path.isfile(e["mask"]), "маска предрасчёта не лежит на диске"
    assert os.path.basename(os.path.dirname(e["mask"])) == "cam1", e["mask"]


def test_js_контракт_масок_без_полей_превью(xml_subs, counters):
    """В .jsx уезжает только контракт: поля превью (`path`, `src_start`) не протекают."""
    plan = _plan(xml_subs)
    js = _roto_js(plan, xml_subs, {"roto": True, "style": dict(STYLE),
                                   "base": os.path.dirname(xml_subs)},
                  emit=lambda *a, **k: None, cancel=lambda: False)
    arr = json.loads(js)
    assert arr, "рото в .jsx пустое"
    for e in arr:
        assert set(e) == {"ci", "ts", "te", "cs", "scale", "mf", "mask"}, e
        assert os.path.isfile(e["mask"]), e["mask"]


def test_cached_plan_находит_готовое_без_расчёта(xml_subs, counters):
    """Быстрая дверь «что уже посчитано» отвечает по кэшам и ничего не считает.

    Аргументы плана — те же, что у сборки и у `/api/scene` (inserts=[], без XML-вставок):
    иначе план вышел бы ДРУГОЙ, и дверь не нашла бы только что посчитанные маски.
    """
    base = os.path.dirname(xml_subs)
    kw = dict(inserts=[], style=dict(STYLE), base=base, disclaimer="",
              include_xml_inserts=False, emit=lambda *a, **k: None, **ROTO)
    xml2ae.to_ae_full(xml_subs, jsx_path=os.path.join(base, "out.jsx"), **kw)
    before = dict(counters)

    ready = precompute.cached_plan(xml_subs, inserts=[], style=dict(STYLE),
                                   include_xml_inserts=False,
                                   roto=True, roto_bottom=0.0,
                                   emit=lambda *a, **k: None)
    assert ready["roto"], "cached_plan не нашёл посчитанные маски"
    assert ready["head"]["ready"] is True and ready["head"]["cams"] == [1], ready["head"]
    assert counters == before, "cached_plan полез считать (GPU-этап у быстрой двери)"


def test_предрасчёт_превью_и_сборка_одна_функция(xml_subs, counters):
    """`precompute.precompute` (его зовёт превью) делает ровно те же два этапа.

    Тот же план, тот же результат, что у сборки: `precompute` — это и есть тело
    предстадий `to_ae_full`, а не «похожий» расчёт.
    """
    base = os.path.dirname(xml_subs)
    plan = _plan(xml_subs)
    res = precompute.precompute(xml_subs, style=dict(STYLE), plan=plan,
                                emit=lambda *a, **k: None,
                                roto=True, base=base, roto_bottom=0.0)
    assert res["head"]["cams"] == [1], res["head"]
    assert res["roto"], "предрасчёт превью не отдал масок"
    assert counters["head_track"] == 1
    assert all(os.path.isfile(e["mask"]) for e in res["roto"])


def test_сборка_выгружает_rvm_ровно_один_раз(xml_subs, monkeypatch):
    """Пункт 5: выгрузка RVM — в общей функции предрасчёта, ровно один раз на сборку.

    Раньше модель выгружала только сборка (`build._roto_js`), и превью после кнопки
    «Рассчитать рото и трекинг» держало видеопамять до перезапуска сервера. Теперь
    выгрузка одна на оба пути: второй вызов в сборке был бы дублем.
    """
    calls = []
    monkeypatch.setattr(_roto, "release", lambda emit=None: calls.append(1) or True)
    base = os.path.dirname(xml_subs)
    xml2ae.to_ae_full(xml_subs, jsx_path=os.path.join(base, "out.jsx"), inserts=[],
                      style=dict(STYLE), base=base, disclaimer="",
                      include_xml_inserts=False, emit=lambda *a, **k: None, **ROTO)

    assert calls == [1], \
        "выгрузок RVM за сборку: %d (ждали ровно одну — в общей функции предрасчёта)" \
        % len(calls)
