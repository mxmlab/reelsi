# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Интро гаснет к субтитру, если стоит на его месте; появление успевает доиграть (задание MH).

Разбор `reelsi_batch.aep` владельца против собранного `Reelsi_all.jsx`: 61 из 76
правленых руками групп интро гаснут к моменту появления следующего субтитра (±0.1 с,
медиана отклонения 1 кадр), длина затухания почти постоянная — 0.15 с. Правки он делал
руками: группы, сдвинутые по Y (полосы субтитров не касаются), не трогал, а у 16 слов
сжимал появление — «чтобы хотя бы увидеть текст». Здесь это считает Python:

  * `intro_hits_subs` (`core/xml2ae/layout.py`) — стоит ли блок интро на полосе субтитров
    по вертикали: Y базовых линий и кегли строк (back_scale/lk), масштаб прекомпа
    INTRO_SCALE·ds/100, общий масштаб G, позиция (y/dy) и зум Камеры 1, пока интро к ней
    привязано; полоса — posy, кегль субтитров и высота стопки жёлтых;
  * группа с пересечением гаснет к `next_sub` (ручка `intro_sub_fade`, галка
    `intro_sub_cut`), окно уезжает в план (`te/fade`) и в .jsx (`INTRO_SUB_FX`);
  * слово, чья анимация появления не успевает до начала затухания, играет её короче
    (d = max(0.1, fade_start − t), ключи × d/D) — коэффициент на слово считает Python и
    отдаёт в .jsx (`INTRO_SQ`) и в план (`plan.intro[].sq`), превью анимирует тем же числом;
  * ПОСЛЕДНЯЯ группа ролика под правило не попадает (приёмка архитектора на 8 роликах
    владельца): её окно — прежнее, с HOLD, как на main. Ни одной из 7 последних групп он
    не правил, а правило срезало их на 1.5–1.9 с (C1459 гр.13, C1461-007 гр.14, C1462-004
    гр.23) — субтитр после интро идёт там уже по сценарию. Сжатие появления (п.2) у неё
    тоже не включается: его включает только укороченное окно, а окно не укоротилось.

Тесты:
  1. функция пересечения: блок на уровне субтитров → True, сдвиг на 400 px → False,
     зум Камеры 1 учитывается при `intro_cam=True` и не учитывается при False;
  2. `scene_plan` на фикстуре: следующий субтитр через 0.45 с после последнего слова →
     `te == next_sub`, затухание `intro_sub_fade`; сдвиг по Y и `intro_sub_cut=False` →
     окно прежнее; последняя группа ролика в тех же условиях — окно прежнее (HOLD, как на
     main), предпоследняя — укорочена;
  3. .jsx, исполненный в node с заглушками AE: ключи прозрачности прекомпа кончаются
     в `next_sub`, ключ «100» — в начале затухания;
  4. сжатие появления: глитч-слово, чья анимация не влезает до начала затухания,
     укладывает ключи в остаток, слово с запасом играет прежние 0.44 с;
  5. без пересекающихся групп .jsx побайтово прежний (INTRO_SUB_FX/INTRO_SQ не
     объявляются; эталон golden_geometry.jsx стережёт test_geometry_python);
  6. ручка `intro_fade` доезжает до превью: node-стенд `ipvIntro` — прозрачность на
     `te − 0.1` различается при `intro_fade=0.1` и `0.35` (это и есть проверка п. 3
     задания: план несёт fade, рефетч плана на правку ручки есть, ipvIntro его применяет);
  7. сторож ручек схемы: `intro_sub_fade`, `intro_sub_cut` — подписи, диапазоны,
     дефолты и перевод en.

Шрифт в сборках намеренно несуществующий (`TestInk-Regular`): метрики берутся из
запасной ветки раскладки, и числа не зависят от того, какие шрифты стоят на машине.
"""
import gzip
import json
import os
import re
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from core import style_schema, styles, xml2ae  # noqa: E402
from core.xml2ae.layout import (INTRO_BASE_Y, INTRO_F_DUR, INTRO_F_OUT,  # noqa: E402
                                INTRO_HOLD, intro_block_span, intro_hits_subs,
                                intro_sub_window, sub_span)
from tests.test_intro_big import (_NODE_STUB, _build_intro,  # noqa: E402
                                  _intro_region, _jsx_decls, _schema_field)
from tests.test_intro_preview_ys import (DOM_SIM, _ipvintro_region,  # noqa: E402
                                         _js_src, _run_node, node)

PS = "TestInk-Regular"          # шрифта с таким именем нет — см. докстринг
# Момент последнего слова группы и момент появления следующего субтитра фикстуры:
# 1.9167 + 0.45 = 2.3667 (между ними субтитров нет — «МЕТКОНСЕКТ» начинается в 1.9).
T_WORD, T_NEXT_SUB = 1.9167, 2.3666666666666667
# gy = 600 px тянет блок интро вниз, на полосу субтитров (posy = 1145 при H = 1920)
GY_ON_SUBS, GY_SHIFTED = 600, 1100
# Второй сценарий — ДВЕ группы: слова предпоследней и последней групп фикстуры и моменты
# субтитров сразу после них (обе группы стоят на полосе субтитров). Слово в 1.9 стоит
# РОВНО на субтитре — он «после слова» не считается (_next_sub_after: s > gmax), и группа
# гаснет к следующему, 2.3667.
PEN_WORD, NEXT_SUB_PEN = 1.9, 2.3666666666666667
LAST_WORD, NEXT_SUB_LAST = 5.4, 5.633333333333334


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


@pytest.fixture(autouse=True)
def _isolate_censor(monkeypatch):
    """Детерминизм сборки: цензура читает поставочные списки, а не личный badwords.user.txt."""
    from core import censor
    monkeypatch.setattr(censor, "USER_PATHS", {"bad": "", "ok": ""})
    monkeypatch.setattr(censor, "_cache", {"bad": (None, None, censor.DEFAULT_BAD),
                                           "ok": (None, None, censor.DEFAULT_OK)})


def _intro(gy=None, anim=None, words=None, times=None):
    """Группа из одной строки: с gy блок уезжает вниз, на полосу субтитров."""
    ln = {"words": words or ["ПЕРВОЕ"], "color": "white",
          "times": times or [T_WORD]}
    if gy is not None:
        ln["gy"] = gy
    if anim:
        ln["anim"] = anim
    return [ln]


def _cut_intro(gy=GY_ON_SUBS, anim=None, words=None, times=None):
    """Две группы: тестовая (предпоследняя — к ней правило и применяется) и хвост ролика.

    Правило «гаснет к субтитру» последнюю группу не трогает (HOLD), поэтому все проверки
    самого правила идут на предпоследней: хвост лишь делает её не последней. Слово хвоста —
    после всех субтитров сценария и вне полосы субтитров, на окно тестовой группы он не
    влияет. `_plan`/`_build_intro` для такой пары зовутся с `splits=[1]`.
    """
    return _intro(gy=gy, anim=anim, words=words, times=times) + \
        _intro(words=["ХВОСТ"], times=[LAST_WORD])


def _plan(xml, intro, style=None, splits=None):
    return xml2ae.scene_plan(xml, intro=intro, disclaimer="", intro_riser=False,
                             intro_splits=[] if splits is None else splits,
                             style=dict(style or {}, font=PS),
                             emit=lambda *a, **k: None)


def _win(plan, i=0):
    g = plan["intro"][i]
    return g["te"], g["fade"]


# Окно НЕпоследней группы (последнее слово + F_OUT — HOLD у неё не берётся). Именно его
# режет правило, и именно с ним сравниваются тесты правила. У ПОСЛЕДНЕЙ группы окно своё,
# с HOLD (gmax + F_DUR + HOLD + F_OUT) — как на main; его правило не трогает, и ожидание
# такого окна тест последней группы считает по её собственному слову.
MID_WINDOW = (T_WORD + INTRO_F_OUT, 0.35)


# ---- 1. функция пересечения ----------------------------------------------------------------

# Позиция блока по умолчанию: intro_y = 0, iDy = 0 (plan.intro[].y) — блок ВЫШЕ центра
# кадра, на полосу субтитров его увозит смещение группы dy.
Y_INTRO = -INTRO_BASE_Y
POSY, SUB_FSIZE = 1145, 140.0          # полоса субтитров фикстуры (posy и кегль из плана)


def _hits(dy=0.0, zoom=100.0, intro_cam=True, ys=(960.0,), fsize=SUB_FSIZE):
    """Стоит ли блок интро на полосе субтитров: те же числа, что у плана фикстуры."""
    return intro_hits_subs(list(ys), [fsize] * len(ys), POSY, SUB_FSIZE, h=1920.0,
                           ds=100.0, g=100.0, y=Y_INTRO, dy=dy, zoom=zoom,
                           intro_cam=intro_cam, sub_row=0, sub_step=0.0)


def test_overlap_block_on_subs_level():
    """Блок на уровне субтитров пересекается с полосой, сдвинутый на 400 px — нет."""
    assert _hits(dy=GY_ON_SUBS) is True, "блок на полосе субтитров не поймался"
    for dy in (GY_ON_SUBS - 400.0, GY_ON_SUBS + 400.0):
        assert _hits(dy=dy) is False, "блок, сдвинутый на 400 px, всё ещё на полосе субтитров"
    # граница: блок ровно НАД полосой (его низ на верхе полосы) — не пересечение
    sub_top = sub_span(POSY, SUB_FSIZE)[0]
    block_bottom = intro_block_span([960.0], [SUB_FSIZE], 1920.0, ds=100.0, g=100.0,
                                    y=Y_INTRO, dy=0.0)[1]
    dy_edge = sub_top - block_bottom
    assert _hits(dy=dy_edge) is False, "касание краями считается пересечением"
    assert _hits(dy=dy_edge + 1.0) is True, "блок, зашедший на полосу на 1 px, не поймался"


def test_overlap_zoom_only_when_intro_rides_camera():
    """Зум Камеры 1 учитывается, пока интро к ней привязано (intro_cam=True), и не
    учитывается у откреплённого интро (задание ZM)."""
    # блок по центру кадра (dy = база): при 100 % он до полосы не дотягивается,
    # при 300 % — дорастает до неё
    assert _hits(dy=INTRO_BASE_Y) is False
    assert _hits(dy=INTRO_BASE_Y, zoom=300.0, intro_cam=True) is True, (
        "зум Камеры 1 не приблизил блок к полосе субтитров — он не учитывается")
    assert _hits(dy=INTRO_BASE_Y, zoom=300.0, intro_cam=False) is False, (
        "зум учтён у откреплённого от камеры интро (задание ZM: там его нет)")


def test_sub_window_shorter_than_fade_in():
    """Затухание не начинается раньше конца фейд-ина группы: окна не хватает — спад короче."""
    te, fade, fstart = intro_sub_window(0.0, 3.5, 1.9, 0.15)
    assert (te, fstart) == (1.9, 1.75) and abs(fade - 0.15) < 1e-9
    # субтитр появляется на самом фейд-ине (0.3 с) — спад нулевой, срез жёсткий
    te, fade, fstart = intro_sub_window(0.0, 3.5, 0.2, 0.15)
    assert te == 0.2 and fade == 0.0 and abs(fstart - INTRO_F_DUR) < 1e-9


# ---- 2. scene_plan: группа на полосе гаснет к следующему субтитру ---------------------------

def test_plan_group_on_subs_fades_to_next_sub(xml_subs):
    """Следующий субтитр через 0.45 с после последнего слова: te == next_sub (±0.001),
    затухание — intro_sub_fade. Группа тестовая — ПРЕДпоследняя: у последней группы
    ролика своё правило (HOLD), его проверяет отдельный тест ниже."""
    plan = _plan(xml_subs, _cut_intro(), splits=[1])
    te, fade = _win(plan)
    assert abs((T_NEXT_SUB - T_WORD) - 0.45) < 1e-3, "фикстура: субтитр не через 0.45 с"
    assert abs(te - T_NEXT_SUB) < 1e-3, "группа не гаснет к появлению субтитра: te=%r" % te
    assert abs(fade - styles.BASE["intro_sub_fade"]) < 1e-9, (
        "затухание не равно intro_sub_fade: %r" % fade)
    assert te < MID_WINDOW[0], "окно не укоротилось"

    # ручка длины затухания работает: гаснет туда же, но медленнее
    plan2 = _plan(xml_subs, _cut_intro(), {"intro_sub_fade": 0.3}, splits=[1])
    assert abs(_win(plan2)[0] - T_NEXT_SUB) < 1e-3
    assert abs(_win(plan2)[1] - 0.3) < 1e-9, "intro_sub_fade не доехал до плана"

    # общий фейд короче нового: в AE спад укоротит он (ключ «100» в max(outStart,
    # outEnd−F_FADE)) — план обязан нести то же число, что соберёт шаблон
    plan3 = _plan(xml_subs, _cut_intro(), {"intro_sub_fade": 0.3, "intro_fade": 0.1},
                  splits=[1])
    assert abs(_win(plan3)[1] - 0.1) < 1e-9, (
        "затухание не укорочено общим фейдом: %r" % (_win(plan3)[1],))


def test_plan_shifted_group_keeps_window(xml_subs):
    """Та же группа, сдвинутая по Y на 500 px (полосы не касается), — окно прежнее."""
    te0, fade0 = _win(_plan(xml_subs, _cut_intro(gy=None), splits=[1]))
    assert abs(te0 - MID_WINDOW[0]) < 1e-6 and abs(fade0 - MID_WINDOW[1]) < 1e-9
    te_shift, fade_shift = _win(_plan(xml_subs, _cut_intro(gy=GY_SHIFTED), splits=[1]))
    assert (te_shift, fade_shift) == (te0, fade0), "сдвинутая по Y группа всё равно срезана"


def test_plan_knob_off_keeps_window(xml_subs):
    """intro_sub_cut=False — правило выключено целиком, окно прежнее."""
    assert _win(_plan(xml_subs, _cut_intro(), {"intro_sub_cut": False}, splits=[1])) == \
        (MID_WINDOW[0], MID_WINDOW[1])


def test_plan_last_group_keeps_hold_window(xml_subs, tmp_path):
    """Последняя группа ролика к субтитру НЕ гаснет: её окно — прежнее, как на main.

    Приёмка архитектора на 8 роликах владельца: 61 из 76 правленых групп гаснут к
    субтитру, но ни одной из 7 ПОСЛЕДНИХ групп он не трогал — последняя держится до
    конца ролика нарочно (HOLD), а правило срезало её на 1.5–1.9 с (C1459 гр.13,
    C1461-007 гр.14, C1462-004 гр.23). Здесь у обеих групп блок стоит на полосе
    субтитров и субтитр идёт после их последнего слова: предпоследняя гаснет к нему,
    последняя — нет; и появление её слов не сжимается (п.2) — окно не укоротилось.
    """
    intro = _intro(gy=GY_ON_SUBS, words=["ПЕРВОЕ"], times=[PEN_WORD]) \
        + _intro(gy=GY_ON_SUBS, anim="reveal", words=["ВТОРОЕ"], times=[LAST_WORD])
    plan = _plan(xml_subs, intro, splits=[1])
    assert len(plan["intro"]) == 2, "фикстура не собрала две группы"
    pen, last = plan["intro"]

    # предпоследняя: правило работает — гаснет к субтитру 2.3667 (окно без HOLD короче)
    assert NEXT_SUB_PEN < PEN_WORD + INTRO_F_OUT, "фикстура: субтитр позже окна группы"
    assert abs(pen["te"] - NEXT_SUB_PEN) < 1e-3, (
        "предпоследняя группа не гаснет к субтитру: te=%r" % pen["te"])
    assert abs(pen["fade"] - styles.BASE["intro_sub_fade"]) < 1e-9

    # последняя: субтитр 5.6333 идёт после её слова (сломанное правило срезало бы её
    # с 7.45 до 5.6333), но окно остаётся прежним — HOLD, как на main
    hold_te = LAST_WORD + INTRO_F_DUR + INTRO_HOLD + INTRO_F_OUT
    assert NEXT_SUB_LAST < hold_te, (
        "фикстура: субтитр идёт позже окна последней группы — правило и не сработало бы")
    assert abs(last["te"] - hold_te) < 1e-6, (
        "последняя группа всё ещё срезана к субтитру: te=%r" % last["te"])
    assert abs(last["fade"] - min(styles.BASE["intro_fade"], INTRO_F_OUT)) < 1e-9, (
        "у последней группы поехало затухание: %r" % last["fade"])
    # сжатие появления (п.2): его включает укороченное окно, а окна никто не укорачивал —
    # слово последней группы («ВТОРОЕ» в 5.4, анимация 0.3 с) играет все свои 0.3 с
    assert "sq" not in last, "последней группе сжали появление (окно не укорачивалось)"

    # то же и в .jsx: у последней группы окна в INTRO_SUB_FX нет (null), у предпоследней есть
    jsx = _build_intro(xml_subs, tmp_path, intro, splits=[1], name="last_hold.jsx")
    assert "INTRO_SQ" not in jsx, "сжатие объявлено там, где окна не укорачивали"
    decl = [ln for ln in jsx.splitlines() if "var INTRO_SUB_FX=" in ln]
    assert decl, "INTRO_SUB_FX не доехал до .jsx"
    fx = json.loads(decl[0].split("var INTRO_SUB_FX=", 1)[1].split(";", 1)[0])
    assert len(fx) == 2 and fx[1] is None, (
        "в .jsx последняя группа всё ещё срезана к субтитру: %r" % (fx,))
    assert fx[0] and abs(fx[0][1] - NEXT_SUB_PEN) < 1e-3, (
        "в .jsx предпоследняя группа не гаснет к субтитру: %r" % (fx,))


# ---- 3-4. .jsx в node: окно прекомпа и сжатие появления ------------------------------------

# Тот же стенд, что в test_intro_big (_NODE_STUB), только прекомп интро ещё и
# складывается в _compLayers: тесту нужны ключи прозрачности САМОГО прекомпа.
_MAIN_ADD = "var main = { layers: { add: function(ic){ return makeLayer(ic.name); } } };"
assert _MAIN_ADD in _NODE_STUB, "стенд test_intro_big изменился — правь _NODE_STUB_PRECOMP"
_NODE_STUB_PRECOMP = _NODE_STUB.replace(
    _MAIN_ADD,
    "var _compLayers = [];\n"
    "var main = { layers: { add: function(ic){ const L = makeLayer(ic.name);"
    " _compLayers.push(L); return L; } } };")


def _run_jsx(jsx, tmp_path, checks, name="stand.js"):
    """Исполнить блок интро собранного .jsx в node с заглушками AE."""
    script = (_NODE_STUB_PRECOMP + "\n" + _jsx_decls(jsx) + "\n"
              + _intro_region(jsx) + "\n" + checks)
    return _run_node(tmp_path, name, script)


@node
def test_jsx_precomp_fades_to_next_sub(xml_subs, tmp_path):
    """3. Ключи прозрачности прекомпа кончаются в next_sub: «0» — на конце слоя,
    «100» — в начале затухания (te − intro_sub_fade). Группа — предпоследняя: у
    последней группы ролика правило не работает (HOLD), см. test_plan_last_group…"""
    intro = _cut_intro()
    plan = _plan(xml_subs, intro, splits=[1])
    jsx = _build_intro(xml_subs, tmp_path, intro, splits=[1])
    assert "var INTRO_SUB_FX=" in jsx and "if (INTRO_SUB_FX[gI])" in jsx, (
        "INTRO_SUB_FX не доехал до .jsx")

    checks = """
const compL = _compLayers[0];
assert(compL, 'прекомп интро не создан');
const opKeys = compL.prop('ADBE Opacity').keys;
const kEnd = opKeys[opKeys.length - 1], kPrev = opKeys[opKeys.length - 2];
console.log(JSON.stringify({te: @TE@, fade: @FADE@, end: kEnd, start: kPrev,
                            finish: compL.outPoint, n: opKeys.length}));
""".replace("@TE@", json.dumps(plan["intro"][0]["te"])) \
   .replace("@FADE@", json.dumps(plan["intro"][0]["fade"]))
    res = _run_jsx(jsx, tmp_path, checks)
    assert res.returncode == 0, f"Node.js script failed: {res.stderr}\n{res.stdout}"
    d = json.loads(res.stdout.strip().splitlines()[-1])
    assert abs(d["end"][0] - d["te"]) < 1e-3, (
        "слой кончается не в next_sub: %r != %r" % (d["end"][0], d["te"]))
    assert d["end"][1] == 0, "последний ключ прозрачности не ноль"
    assert abs(d["start"][0] - (d["te"] - d["fade"])) < 1e-3, (
        "затухание начинается не в te − fade: %r" % (d["start"][0],))
    assert d["start"][1] == 100, "перед затуханием прозрачность не 100"
    assert abs(d["finish"] - d["te"]) < 1e-3, "outPoint слоя не в next_sub"


@node
def test_jsx_word_appearance_fits_before_fade(xml_subs, tmp_path):
    """4. Сжатие появления: глитч-слово, чья анимация не влезает до начала затухания,
    играет её в остаток (те же ключи INTRO_ANIMS, умноженные на коэффициент), а слово
    с запасом — прежние 0.44 с. Группа — предпоследняя (хвост делает её не последней):
    у последней окно своё, длинное, и сжимать там нечего."""
    # слова в группе: «ПЕРВОЕ» в 1.6667 (1.6667+0.44 = 2.107 < 2.2167 — успевает),
    # «ВТОРОЕ» в 1.9167 (2.357 > 2.2167 — не успевает, сжимается)
    words, times = ["ПЕРВОЕ", "ВТОРОЕ"], [1.6667, T_WORD]
    intro = _cut_intro(anim="glitch", words=words, times=times)
    plan = _plan(xml_subs, intro, splits=[1])
    fade_start = plan["intro"][0]["te"] - plan["intro"][0]["fade"]
    sq_fits, sq_cut = plan["intro"][0]["sq"][0]
    assert sq_fits is None, "слово с запасом получило множитель: %r" % (sq_fits,)
    assert 0 < sq_cut < 1, "питон не сжал появление последнего слова: %r" % (sq_cut,)
    assert abs(sq_cut - max(0.1, fade_start - T_WORD) / 0.44) < 1e-3, (
        "коэффициент не равен правилу d = max(0.1, fade_start − t)/D: %r" % (sq_cut,))

    jsx = _build_intro(xml_subs, tmp_path, intro, splits=[1], name="glitch.jsx")
    assert "var INTRO_SQ=" in jsx and "introSQ(gI,qi,wj2)" in jsx, "INTRO_SQ не доехал до .jsx"
    # группа НЕ на полосе субтитров: сжимать нечего — ни массива, ни читалки (golden)
    jsx_free = _build_intro(xml_subs, tmp_path,
                            _cut_intro(gy=None, anim="glitch", words=words, times=times),
                            splits=[1], name="free.jsx")
    assert "INTRO_SQ" not in jsx_free, "сжатие объявлено там, где слова успевают доиграть"

    checks = """
function wordKeys(text){
  const L = _textLayers.filter(function(L){ return !L._removed && L.text === text; })[0];
  assert(L, 'слой слова не создан: ' + text);
  return L.prop('ADBE Opacity').keys;
}
console.log(JSON.stringify({fits: wordKeys('ПЕРВОЕ'), cut: wordKeys('ВТОРОЕ')}));
"""
    res = _run_jsx(jsx, tmp_path, checks, name="stand_cut.js")
    assert res.returncode == 0, f"Node.js script failed: {res.stderr}\n{res.stdout}"
    d = json.loads(res.stdout.strip().splitlines()[-1])
    # семь ключей глитча (INTRO_ANIMS["glitch"]["op_keys"]) — у обоих слов
    assert len(d["cut"]) == 7 and len(d["fits"]) == 7, "у глитч-слова не семь ключей"
    # слово с запасом: времена ключей прежние
    assert abs(d["fits"][-1][0] - (times[0] + 0.2667)) < 1e-3, (
        "у слова с запасом ключи поехали: %r" % (d["fits"][-1],))
    # сжатое слово: последний ключ — в остатке до начала затухания, и он ≤ fade_start
    last_t = d["cut"][-1][0]
    assert abs(last_t - (T_WORD + 0.2667 * sq_cut)) < 1e-3, (
        "ключи не умножены на коэффициент: %r, ожидалось %r"
        % (last_t, T_WORD + 0.2667 * sq_cut))
    assert last_t <= fade_start + 1e-3, "появление не уложилось до начала затухания"
    # и все ключи держат порядок: сжатие не переставляет их
    ts = [k[0] for k in d["cut"]]
    assert ts == sorted(ts), "ключи сжатого слова идут не по порядку: %r" % (ts,)


# ---- 5. без пересекающихся групп .jsx прежний ----------------------------------------------

def test_jsx_unchanged_without_overlapping_groups(xml_subs, tmp_path):
    """Группы не на полосе субтитров: INTRO_SUB_FX и INTRO_SQ не объявляются, и ручки
    правила ничего не меняют — .jsx побайтово один и тот же (golden держит
    test_geometry_python: эталон fixtures/golden_geometry.jsx). Группы две: одна
    последняя (её правило и так не трогает) ничего не доказывала бы."""
    plain = _build_intro(xml_subs, tmp_path, _cut_intro(gy=None), splits=[1], name="plain.jsx")
    off = _build_intro(xml_subs, tmp_path, _cut_intro(gy=None), splits=[1],
                       style={"intro_sub_cut": False}, name="off.jsx")
    zero = _build_intro(xml_subs, tmp_path, _cut_intro(gy=None), splits=[1],
                        style={"intro_sub_fade": 0.0}, name="zero.jsx")
    assert "INTRO_SUB_FX" not in plain and "INTRO_SQ" not in plain, (
        "правило сработало там, где блок не на полосе субтитров")
    assert plain == off, "галку сняли, а .jsx изменился"
    assert plain == zero, "длительность затухания 0 изменила .jsx без сработавшего правила"


# ---- 6. ручка intro_fade доезжает до превью ------------------------------------------------

@node
def test_node_preview_applies_intro_fade(xml_subs, tmp_path):
    """6. Прозрачность блока в превью на `te − 0.1` различается при intro_fade = 0.1 и
    0.35 — значит, план несёт `fade`, рефетч плана на правку ручки доезжает, а ipvIntro
    его применяет (проверка п. 3 задания: своего дефекта в этой цепочке не нашлось)."""
    plans = {}
    for fade in (0.1, 0.35):
        p = _plan(xml_subs, _intro(), {"intro_fade": fade})
        plans[str(fade)] = {k: p[k] for k in ("w", "h", "fsize", "intro_fsize",
                                              "intro_scale", "intro_anims",
                                              "layer_order", "intro")}
        assert abs(p["intro"][0]["fade"] - fade) < 1e-9, "план не несёт fade ручки"
        assert "sq" not in p["intro"][0], "группа не на полосе — множителя быть не должно"

    sim = DOM_SIM.replace("@INTRO@", "[]")
    checks = """
const PL = @PLANS@;
function opacityAt(key){
  IPV.plan = PL[key];
  IPV.intro = ipvIntroGroups();
  IPV.introCur = -2;
  ioEl.style = { setProperty(){}, removeProperty(){} };
  const g = IPV.intro[0];
  assert(g, 'группа интро не доехала из плана');
  ipvIntro(g.outEnd - 0.1);
  return {op: parseFloat(ioEl.style.opacity), te: g.outEnd, fade: g.fade};
}
const slow = opacityAt('0.35'), fast = opacityAt('0.1');
assert.strictEqual(slow.fade, 0.35, 'план 0.35: fade не доехал: ' + slow.fade);
assert.strictEqual(fast.fade, 0.1, 'план 0.1: fade не доехал: ' + fast.fade);
assert(fast.op === 1, 'при fade=0.1 блок гаснет раньше времени: ' + fast.op);
assert(slow.op < 1, 'при fade=0.35 прозрачность на te − 0.1 не снижена: ' + slow.op);

console.log("OK: preview applies intro_fade");
""".replace("@PLANS@", json.dumps(plans, ensure_ascii=False))
    res = _run_node(tmp_path, "test_intro_sub_fade_preview.js",
                    sim + "\n" + _ipvintro_region(_js_src()) + "\n" + checks)
    assert res.returncode == 0, f"Node.js script failed: {res.stderr}\n{res.stdout}"
    assert "OK: preview applies intro_fade" in res.stdout


# ---- 7. ручки схемы ------------------------------------------------------------------------

def _i18n():
    path = os.path.join(ROOT, "static", "i18n", "en.json")
    return json.loads(open(path, encoding="utf-8").read())


def test_knobs_intro_sub_fade_and_cut():
    """Обе ручки в схеме стиля: подписи, диапазоны, дефолты и перевод en на месте
    (сторож «каждая ручка» — test_r11_li_every_knob — берёт их из схемы сам)."""
    f_cut = _schema_field("intro_sub_cut")
    assert f_cut, "в схеме стиля нет галки intro_sub_cut"
    assert f_cut["ctl"] == "bool" and f_cut["label"] == "Интро гаснет к субтитру"
    assert styles.BASE["intro_sub_cut"] is True, "дефолт галки intro_sub_cut уехал"

    f_fade = _schema_field("intro_sub_fade")
    assert f_fade, "в схеме стиля нет ручки intro_sub_fade"
    assert f_fade["ctl"] == "num" and f_fade["label"] == "Быстрый фейд перед субтитрами, с"
    assert (f_fade["min"], f_fade["max"], f_fade["step"]) == (0, 0.5, 0.01)
    assert styles.BASE["intro_sub_fade"] == 0.15, "дефолт ручки intro_sub_fade уехал"

    en = _i18n()
    for key in ("Интро гаснет к субтитру", f_cut["tip"], "Быстрый фейд перед субтитрами, с",
                f_fade["tip"]):
        assert key and key in en, "нет перевода en: %r" % (key,)
        assert not re.search(r"[А-Яа-яЁё]", en[key]), "в переводе остался русский текст: %r" % key


def test_schema_counters_updated():
    """Счётчики схемы (test_style_schema) знают про две новые ручки: ключи BASE, поля
    схемы и тумблеры не разъехались."""
    base = styles.BASE
    assert "intro_sub_cut" in base and "intro_sub_fade" in base
    fields = [k for k in ("intro_sub_cut", "intro_sub_fade") if _schema_field(k)]
    assert len(fields) == 2
    # группа «Тайминг» — та же, что у intro_fade (задание MH)
    def walk(items, path=()):
        for x in items:
            if x.get("key") == "intro_sub_fade":
                return path
            found = walk(x.get("items", []), path + (x.get("id") or x.get("key"),))
            if found:
                return found
        return None

    where = None
    for layer in style_schema.LAYERS:
        where = walk(layer.get("items", ()), (layer.get("id"),))
        if where:
            break
    assert where == ("intro", "intro.timing"), "ручка переехала из группы «Тайминг»: %r" % (where,)
