# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Геометрия переехала из AE_FULL в layout.py (задание B): .jsx несёт готовые
числа (fit/slackx/slacky у видео, en/ex у фото, CAM1_EASE у зума), шаблон только
применяет. Здесь:

- golden: собранный .jsx не меняется побайтово (эталон fixtures/golden_geometry.jsx —
  обновлять только осознанно, при реальной правке геометрии);
- юниты самих функций: окна входа/выхода, запас панорамы, масштаб, ease по соседям.

Задание C: ключи анимаций вставок тоже переехали в layout.py (_anim_keys/_blur_keys/
_cam1_pos_keys) — их же читает из плана сцены предпросмотр.

Фикстура timeline_subs.xml.gz: Камера 2 выключена на первом клипе (кадры 0..443) и
включена на втором (443..651 @60fps): 1-я секунда — кам1, 8-я — перебивка.
"""
import gzip
import json
import os
import re
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import xml2ae  # noqa: E402
from core.xml2ae.layout import (_anim_keys, _blur_keys, _cam1_pos_keys,  # noqa: E402
                           _fill_slack, _fit_scale, _ins_enter_exit,
                           _zoom_key_eases)

INS = [  # фото на кам1, фото на перебивке (cam2-ветка), фото без выхода.
    # Видео-инсерта здесь НАМЕРЕННО нет: has_video тянет в сборку переход/whoosh
    # из assets/ (пути машины) — golden обязан быть машино-независимым
    {"type": "photo", "style": "cam2", "media": "C:/x/a.png", "start_s": 1, "dur_s": 2},
    {"type": "photo", "style": "cam2", "media": "C:/x/cam2.png", "start_s": 8.0, "dur_s": 1.5},
    {"type": "photo", "style": "cam1", "media": "C:/x/b.png", "start_s": 5, "dur_s": 3},
]

# Абсолютные пути до папки assets/ (ризер, переход, whoosh) подставляются от места
# установки Reelsi — на другой машине они другие, а на чистом клоне ассетов нет вовсе.
ASSET_PATH = re.compile(r"[A-Za-z]:[\\/][^\r\n\"]*?[\\/]assets[\\/]")


def _mask_assets(txt):
    """Абсолютные пути ассетов -> маска: эталон и сборка сравниваются независимо от
    раскладки папок машины, где запущен тест.

    Также нормализует:
    - CRLF -> LF: .gitattributes объявляет *.jsx text eol=crlf, поэтому эталон на
      любой ОС читается с \r\n, а сборка пишет open(..., 'w') без newline= (на Windows
      даёт \r\n, на Linux \n). Сравниваем содержимое, а не переносы строк;
    - экранированный '\\' -> '/': разделитель пути в .jsx задаёт ОС сборки (os.sep в
      _decode_pathurl, см. xml2ae/parse.py), и на Linux он законно прямой слеш вместо
      обратного на Windows. По этой же причине _parent_name в parse.py нормализует
      пути posixpath'ом — эталон снят на Windows, а golden стережёт геометрию и
      структуру, а не системный разделитель путей."""
    txt = txt.replace("\r\n", "\n")
    txt = txt.replace("\\\\", "/")
    return ASSET_PATH.sub(r"##ASSETS##/", txt)


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


@pytest.fixture(autouse=True)
def _isolate_censor(monkeypatch):
    """Golden-тест детерминирован: цензура читает поставочные списки, а не личный badwords.user.txt."""
    from core import censor
    monkeypatch.setattr(censor, "USER_PATHS", {"bad": "", "ok": ""})
    monkeypatch.setattr(censor, "_cache", {"bad": (None, None, censor.DEFAULT_BAD),
                                           "ok": (None, None, censor.DEFAULT_OK)})


def _build(xml, tmp_path, style=None, inserts=None):
    st = dict(style or {})
    st["intro_riser"] = False   # golden детерминирован: стиль по умолчанию включает ризер,
                                # а он подставляет путь к ассету с этой машины
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / "out.jsx"),
                                   inserts=[dict(x) for x in (inserts if inserts is not None else INS)],
                                   style=st, disclaimer="",
                                   intro_riser=False, emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read()


def _ins_jsons(jsx):
    return json.loads(re.search(r"var INSERTS=(\[.*?\]);", jsx).group(1))


def test_golden_jsx(xml_subs, tmp_path):
    """Собранный .jsx побайтово совпадает с эталоном. Эталон обновлять только
    осознанно — любые изменения геометрии/структуры INSERTS/CAM1_EASE всплывут здесь.
    Эталон НЕ перегенерируется тестом автоматически: пока
    core/xml2ae/build.py:scene_plan не распилен, это единственная страховка на
    анимацию, интро и раскладку вставок — ослаблять и перегенерировать без ручной
    сверки нельзя.
    Абсолютные пути ассетов (ризер/переход — от места установки) замаскированы в обеих
    сторонах: golden проходит на машине без assets/ рядом с репозиторием."""
    raw_jsx = _build(xml_subs, tmp_path)
    assert '"name":"cam1"' in raw_jsx, "имя камеры 1 потеряно при сборке (пустой name)"
    assert '"name":"cam2"' in raw_jsx, "имя камеры 2 потеряно при сборке (пустой name)"
    jsx = _mask_assets(raw_jsx)
    golden = _mask_assets(open(os.path.join(HERE, "fixtures", "golden_geometry.jsx"),
                               encoding="utf-8-sig").read())
    assert jsx == golden


def test_roto_position_reset_after_parent(xml_subs, tmp_path):
    """Задание BK: рото привязывается к нулу ПОСЛЕ того, как нул получил якорь точки
    наезда и ключи зума. AE при присвоении parent сохраняет мировое положение слоя и
    пересчитывает локальную Position ребёнка на текущий момент — без принудительной
    позиции рото уезжает на смещение точки наезда (Scale рядом уже перезадаётся по той
    же причине). При дефолтной точке 0.5/0.5 смещения нет вовсе — строк в .jsx нет,
    golden (test_golden_jsx) не меняется."""
    shift = _build(xml_subs, tmp_path,
                   style={"cam1_zoom_cx": 0.5, "cam1_zoom_cy": 0.244})
    assert 'cc.property("ADBE Transform Group").property("ADBE Position").setValue([0,0])' in shift, \
        "рото-копия не получает явную позицию после привязки к смещённому нулу"
    assert 'mk.property("ADBE Transform Group").property("ADBE Position").setValue([0,0])' in shift, \
        "рото-маска не получает явную позицию после привязки к смещённому нулу"
    default = _build(xml_subs, tmp_path)
    assert 'cc.property("ADBE Transform Group").property("ADBE Position").setValue([0,0])' not in default
    assert 'mk.property("ADBE Transform Group").property("ADBE Position").setValue([0,0])' not in default


def test_zoom_eases_pulse():
    """Направленное правило (как JS смотрел соседей): out 35 у ключа, чей следующий
    меньше; in 90, если предыдущий больше; иначе Easy Ease 33.3333."""
    keys = [(0, 182), (62, 100), (856, 136.9), (918, 100)]
    assert _zoom_key_eases(keys) == [[33.3333, 35], [90, 33.3333],
                                     [33.3333, 35], [90, 33.3333]]


def test_zoom_eases_drift_modes():
    """Drift: режим ключа решает (1 = out 35, 2 = in 90, 0 = Easy Ease)."""
    keys = [(0, 182, 1), (62, 100, 2), (100, 120, 0), (160, 110, 0)]
    assert _zoom_key_eases(keys) == [[33.3333, 35], [90, 33.3333],
                                     [33.3333, 33.3333], [33.3333, 33.3333]]


def test_ins_enter_exit_windows():
    """Полное окно: вход 0.38, выход 0.47. Окно короче входа — вход ужимается (иначе
    ключи шли бы из порядка: t0->va, t1->va, t0+0.38->vb). noexit — только вход."""
    assert _ins_enter_exit(0.0, 2.0, False, 60) == (0.38, 0.47)
    en, ex = _ins_enter_exit(0.0, 0.5, False, 60)
    assert (en, ex) == (0.225, 0.275)
    assert _ins_enter_exit(0.0, 0.2, True, 60) == (0.2, 0.0)
    assert _ins_enter_exit(0.0, 0.01, False, 60) == (0.0, 0.01)  # короче кадра: показ без анимации


def test_anim_keys():
    """Четыре ключа va->vb->vb->va по en/ex (то, что ставил JS-функцией fourKeys).
    Окно короче кадра — один ключ vb (guard); noexit — жёсткий конец без выхода."""
    keys = _anim_keys(1.0, 3.0, 100.0, 44.0, False, 0.38, 0.47, 60)
    assert keys == [[1.0, 100.0], [1.38, 44.0], [2.53, 44.0], [3.0, 100.0]]
    keys = _anim_keys(1.0, 3.0, 100.0, 44.0, True, 0.38, 0.47, 60)   # noexit: без выхода
    assert keys == [[1.0, 100.0], [1.38, 44.0], [3.0, 44.0]]
    assert _anim_keys(0.0, 0.01, 100.0, 44.0, False, 0.38, 0.47, 60) == [[0.0, 44.0]]


def test_blur_keys():
    """Блюр: резкость входит за 0.38, уходит за 0.47. БЕЗ guard'а, как в старом JS
    (на окне короче кадра ключи встают за концом слоя и не показываются)."""
    keys = _blur_keys(8.0, 9.5, False)
    assert keys == [[8.0, 41.0], [8.38, 0.0], [9.03, 0.0], [9.5, 41.0]]
    keys = _blur_keys(8.0, 10.0, True)
    assert keys == [[8.0, 41.0], [8.38, 0.0], [10.0, 0.0]]


def test_cam1_pos_keys():
    """Вылет из-за спины: старт внизу [0, 464.7] (сдвиг точки покоя x/y применяется
    уже НАД нулём — в старте его нет, как в JS), подъём max(0.68, 30 кадров) до
    [x, -567+y]; noexit — оставляет фото наверху до конца шота."""
    keys = _cam1_pos_keys(1.0, 3.0, False, 0.0, 0.0, 60)
    assert keys == [[1.0, [0.0, 464.7]], [1.5, [0.0, -567.0]],
                    [2.5, [0.0, -567.0]], [3.0, [0.0, 464.7]]]   # потолок 30 кадров вместо 0.68
    keys = _cam1_pos_keys(5.0, 7.3833, True, 20.0, -10.0, 60)
    assert keys == [[5.0, [0.0, 464.7]], [5.5, [20.0, -577.0]], [7.3833, [20.0, -577.0]]]


def test_fit_scale_math():
    """Заполнение кадра: 16:9 в вертикальном кадре -> ~178%, ровно по кадру -> 100%,
    ужатая вставка (sc=55) -> ~98%. Без размеров — None (старое if(!iw||!ih) return)."""
    f = _fit_scale(1920, 1080, True, 1080, 1920, 1.0)
    assert 177 < f < 178
    assert _fit_scale(1080, 1920, True, 1080, 1920, 1.0) == 100.0
    assert 97 < _fit_scale(1920, 1080, True, 1080, 1920, 0.55) < 98
    assert _fit_scale(1920, 1080, False, 1080, 1920, 1.0) == 56.25   # не-fill: ужать под кадр
    assert _fit_scale(0, 0, True, 1080, 1920, 1.0) is None


def test_fill_slack_math():
    """Запас панорамы: 16:9 по ширине ~1166 px (и по высоте ноль — дальше пустота),
    ровно по кадру — ноль. Ужатая (sc<100) кадр не заполняет — двигать нечего."""
    sx, sy = _fill_slack(1920, 1080, 1080, 1920, 1.0)
    assert sx > 1000 and sy == 0
    assert _fill_slack(1080, 1920, 1080, 1920, 1.0) == (0.0, 0.0)
    sx, sy = _fill_slack(1920, 1080, 1080, 1920, 0.55)
    assert sx > 0 and sy == 0
    assert _fill_slack(0, 0, 1080, 1920, 1.0) == (0.0, 0.0)


def test_photo_has_en_ex(xml_subs, tmp_path):
    """У фото-вставок .jsx несёт окна входа/выхода (их считает Python)."""
    ins = _ins_jsons(_build(xml_subs, tmp_path))
    photos = [x for x in ins if x["t"] == "photo"]
    assert photos and all("en" in x and "ex" in x for x in photos)
    assert photos[0]["en"] == 0.38 and photos[0]["ex"] == 0.47


def test_video_without_dims_has_no_fit(xml_subs, tmp_path):
    """Размеры файла не прочитались — полей fit/slack нет: шаблон не трогает вставку
    (старое if(!iw||!ih) return). Видео C:/x/c.mp4 не существует."""
    ins = _ins_jsons(_build(xml_subs, tmp_path, inserts=[
        {"type": "video", "media": "C:/x/c.mp4", "start_s": 10, "dur_s": 3,
         "x": 150, "y": -80, "sc": 55}]))
    vid = [x for x in ins if x["t"] == "video"][0]
    assert "fit" not in vid and "slackx" not in vid and "slacky" not in vid
