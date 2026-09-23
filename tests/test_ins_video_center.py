# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Видеовставка в превью растёт от ЦЕНТРА кадра, как в AE.

Дефект, пойманный владельцем: «Масштаб, %» у видеовставки увеличивал её от ЛЕВОГО
ВЕРХНЕГО угла. Замер в браузере: у `<video>` вставки computed `position:absolute;
left:0; top:0` — это правило `.pvstage video{position:absolute;inset:0;…}`, написанное
для КАМЕР; обёртка `.ipvins .ipvwrap` (flex, center/center) абсолютного ребёнка
центрировать не может, поэтому `translate(x·k, y·k)` из `ipvInsPlace` двигал от угла,
а не от центра: sc=70 — центр видео (95,169) при центре кадра (136,242), sc=130 —
(177,314), левый верх всегда (0,0).

Что стерегут тесты:
  * `.ipvins .ipvwrap video` снимает абсолютное позиционирование (`position:relative;
    inset:auto; flex:none`; фон прозрачный) — элемент снова флекс-ребёнок обёртки,
    и translate едет от центра;
  * `.pvstage video` НЕ изменено: камеры по-прежнему `absolute; inset:0`;
  * фото-вставка и подложка не задеты: правило маски/плашки то же, и новое правило их
    селектором не ловит;
  * боевой `ipvInsPlace` не ставит видео `left`/`top` — центровку отдаёт CSS
    (гоняется настоящий код на мини-DOM стенда ME, tests/test_ins_video_preview.py).

Запуск: python -m pytest tests/test_ins_video_center.py -q
"""
import importlib
import io
import os
import re
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

CSS = os.path.join(ROOT, "static", "app.css")
JS = os.path.join(ROOT, "static", "app", "85-inserts-view.js")

node = pytest.mark.skipif(not shutil.which("node"), reason="контракт фронта требует node в PATH")

# Камеры: <video> прямым ребёнком .pvstage — эталон, менять его нельзя
CAM_RULE = ".pvstage video{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;background:#000}"
# Фото-вставка: окно маски и фото внутри их не трогает
MASK_RULE = ".ipvins .insmask{position:relative;overflow:hidden;flex:none;"
MASK_IMG = ".ipvins .insmask img{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);"
# Видеовставка: флекс-ребёнок обёртки вместо абсолютной коробки кадра
INS_RULE = ".ipvins .ipvwrap video{position:relative;inset:auto;flex:none;background:transparent}"


def _css():
    return io.open(CSS, encoding="utf-8").read()


def _rules(css):
    """Правила файла списком (селектор, объявления); комментарии выброшены."""
    body = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", body):
        for sel in m.group(1).split(","):
            out.append((sel.strip(), m.group(2)))
    return out


_PART = re.compile(r"^([a-z][a-z0-9]*)?((?:\.[A-Za-z0-9_-]+)*)$")


def _parts(sel):
    """Селектор как цепочка потомков (тег + классы); None — не наш случай (`>` `:` и пр.)."""
    if re.search(r"[>+~\[\]:]", sel):
        return None
    parts = []
    for raw in sel.split():
        m = _PART.match(raw)
        if not m:
            return None
        classes = set(c[1:] for c in re.findall(r"\.[A-Za-z0-9_-]+", m.group(2) or ""))
        parts.append((m.group(1) or "", classes))
    return parts or None


def _matches(sel, path):
    """Ловит ли селектор элемент path[-1] в цепочке path (тег, классы) сверху вниз."""
    parts = _parts(sel)
    if not parts:
        return False
    i = 0
    for idx, (tag, classes) in enumerate(path):
        ptag, pcls = parts[i]
        if (not ptag or ptag == tag) and pcls <= classes:
            i += 1
            if i == len(parts):
                return idx == len(path) - 1
    return False


def _spec(sel, decl):
    """Специфичность (классы, теги) + приоритет !important — как считает браузер."""
    return (1 if "!important" in decl else 0,
            len(re.findall(r"\.[A-Za-z0-9_-]+", sel)),
            len(re.findall(r"(?:^|\s)[a-z][a-z0-9]*", sel)))


def _winner(decl_name, path):
    """Побеждающее объявление decl_name для элемента: (значение, селектор) или None."""
    best = None
    for sel, decls in _rules(_css()):
        if not _matches(sel, path):
            continue
        for chunk in decls.split(";"):
            if ":" not in chunk:
                continue
            name, val = chunk.split(":", 1)
            if name.strip().lower() != decl_name:
                continue
            spec = _spec(sel, chunk)
            if best is None or spec >= best[0]:
                best = (spec, val.replace("!important", "").strip(), sel)
    return None if best is None else (best[1], best[2])


# Кадр превью цепочкой (тег, классы) сверху вниз: .pvstage > .ipvins > .ipvwrap > video
STAGE = ("div", {"pvstage"})
INS_OVL = ("div", {"ipvins"})
IPV_WRAP = ("div", {"ipvwrap"})


def _ins_video_path(wrapper_classes=()):
    """Путь элемента видео-вставки: .pvstage > .ipvins > .ipvwrap > video."""
    return [STAGE, INS_OVL, ("div", {"ipvwrap"} | set(wrapper_classes)), ("video", set())]


def test_video_insert_is_a_flex_item_not_an_absolute_box():
    """Ядро: видео вставки снова флекс-ребёнок .ipvwrap, а не absolute 0/0.

    Пока `.pvstage video{position:absolute;inset:0}` оставалось победителем, обёртка с
    `align-items/justify-content:center` видео не центрировала (абсолютных детей флекс
    не раскладывает), и весь рост от «Масштаб, %» шёл от левого верхнего угла.
    """
    css = _css()
    assert INS_RULE in css, \
        "нет точечного правила .ipvins .ipvwrap video — абсолютное позиционирование камер не снято"

    pos = _winner("position", _ins_video_path())
    assert pos is not None, "у видео вставки нет побеждающего position — каскад не проверяем"
    assert pos[0] == "relative", \
        f"видео вставки позиционируется как {pos[0]} (правило {pos[1]}) — центр от flex не работает"
    ins = _winner("inset", _ins_video_path())
    assert ins and ins[0] in ("auto", "none"), f"inset не снят: {ins}"
    flex = _winner("flex", _ins_video_path())
    assert flex and flex[0] == "none", \
        f"видео осталось сжимаемым флексом ({flex}): коробка вставки уже кадра"
    bg = _winner("background", _ins_video_path())
    assert bg and bg[0] in ("transparent", "none") and "url(" not in bg[0], \
        f"за вставкой осталась чёрная подложка камер: {bg}"


def test_camera_video_rule_untouched():
    """Камеры не задеты: `.pvstage video` прежний, и новое правило до них не достаёт."""
    assert CAM_RULE in _css(), "правило камер .pvstage video изменено или пропало"
    cam = [STAGE, ("video", set())]
    pos = _winner("position", cam)
    assert pos and pos[0] == "absolute" and pos[1] == ".pvstage video", \
        f"у камеры побеждает не её правило: {pos}"
    ins = _winner("inset", cam)
    assert ins and ins[0] == "0" and ins[1] == ".pvstage video", \
        f"камера потеряла inset:0 — кадр камеры поехал: {ins}"
    assert not _matches(INS_RULE.split("{")[0], cam), \
        "правило вставки ловит и камеру (.pvstage video вне .ipvins)"


def test_photo_insert_and_plate_untouched():
    """Фото-вставка и подложка не задеты: правило видео их не ловит, их геометрия прежняя."""
    css = _css()
    assert MASK_RULE in css, "правило окна маски фото изменено"
    assert MASK_IMG in css, "правило фото внутри маски изменено"

    # селектор правила видео не должен матчить ни окно маски, ни фото, ни плашку подложки
    for tag, cls in (("div", {"insmask"}), ("div", {"insplate"}), ("img", set())):
        path = [STAGE, INS_OVL, IPV_WRAP, (tag, cls)]
        assert not _matches(INS_RULE.split("{")[0], path), \
            f"правило видео задело фото-элемент <{tag} {sorted(cls)}>"

    mask = [STAGE, INS_OVL, IPV_WRAP, ("div", {"insmask"})]
    pos = _winner("position", mask)
    assert pos and pos[0] == "relative", f"окно маски фото перестало быть флекс-элементом: {pos}"
    pos = _winner("position", mask + [("img", set())])
    assert pos and pos[0] == "absolute", f"фото внутри маски перестало центрироваться: {pos}"


def _me_stand():
    """Стенд ME (настоящие функции фронта на мини-DOM) — если он есть в tests/."""
    if not os.path.exists(os.path.join(HERE, "test_ins_video_preview.py")):
        return None
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    return importlib.import_module("test_ins_video_preview")


@node
def test_ipvins_place_leaves_centering_to_css():
    """`ipvInsPlace` не ставит видео left/top: центровку отдаёт CSS, translate едет от центра.

    Если left/top (или position) вернутся в боевой код, правило CSS перестанет решать:
    элемент снова окажется в углу, и «Масштаб, %» будет расти от левого верха.
    """
    me = _me_stand()
    if me is None:
        pytest.skip("node-стенд ME (tests/test_ins_video_preview.py) не найден")
    code = me._js() + me._DOM_JS + """
    var fw=%s, fh=%s;
    function look(sc,x,y){
      var v=build([mkItem(sc,{fitw:fw,fith:fh,x:x,y:y})]);
      return {w:v.style.width||'', tf:v.style.transform||'',
              left:(v.style.left==null?'':String(v.style.left)),
              top:(v.style.top==null?'':String(v.style.top)),
              pos:(v.style.position==null?'':String(v.style.position)),
              ins:(v.style.inset==null?'':String(v.style.inset)),
              wrap:(v.parentNode&&v.parentNode.className)||''};}
    console.log(JSON.stringify({sc70:look(70,0,0), sc130:look(130,0,0), pan:look(100,60,-40)}));
    """ % me._fill_box(1920, 1080)
    out = me._run_node(code)

    for key in ("sc70", "sc130", "pan"):
        got = out[key]
        assert got["w"], f"{key}: коробка вставки не выставлена — тест ничего не проверяет"
        assert got["left"] == "" and got["top"] == "", \
            f"{key}: ipvInsPlace прибил видео к {got['left']}/{got['top']} — рост пойдёт от угла"
        assert got["pos"] == "" and got["ins"] == "", \
            f"{key}: ipvInsPlace сам позиционирует видео (position: {got['pos']}, inset: {got['ins']})"
        assert "ipvwrap" in got["wrap"], \
            f"{key}: видео не ребёнок .ipvwrap — центрировать его нечему: {got['wrap']}"
    # масштаб по-прежнему из плана, а сдвиг — ровно x/y (от центра, без клампа)
    assert out["sc130"]["w"] != out["sc70"]["w"], "правка масштаба не меняет коробку вставки"
    assert out["pan"]["tf"] == "translate(60px,-40px)", \
        f"сдвиг x/y не доехал до transform: {out['pan']['tf']}"
