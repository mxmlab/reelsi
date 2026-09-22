# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Плейсхолдеры template.py против ключей, которые реально передаёт сборка.

Шаблон AE_FULL и его фрагменты (SUBS_LOOP_*) — это %-подстановки: сотни ключей,
десятки частичных подстановок, и ошибка в одном имени не видна ничем, кроме падения
сборки у пользователя. Ни один тест набора этого не сверял: `node --check` проверяет
уже СОБРАННЫЙ .jsx, а сам шаблон остаётся текстом, который никто не парсит.

Тест ловит три ошибки сразу:

1. В шаблоне есть `%(name)s`, а сборка такого ключа не кладёт -> KeyError при
   `AE_FULL % mapping` (пойман ещё и синтетической проверкой ниже — она повторяет
   подстановку на перехваченном словаре);
2. Ключ есть, а значения нет (None) -> `%s` молча напечатает «None» в .jsx;
3. В собранном .jsx осталось `%(` — значит фрагмент не подставился вовсе (так
   выглядит шаблон, у которого разошлись имя и ключ в ветке, включаемой редко).

Перехват идёт на исполнении, а не по тексту: сборка на фикстуре с включёнными
фичами кладёт подстановку в spy (`__mod__`), spy запоминает ключи и делегирует
подстановку настоящей строке. Поэтому проверяется ровно то, что уходит в AE,
вместе со всеми ветками, которые фича включает.

Запуск:  python -m pytest reelsi/tests -q
"""
import gzip
import os
import re
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault("REELSI_NO_BROWSER", "1")

from core.xml2ae import build  # noqa: E402
from core.xml2ae import plan_subs  # noqa: E402
from core.xml2ae import template  # noqa: E402

# Все фрагменты, которые сборка подставляет через % — и шаблон целиком, и части
# (plan_subs.py собирает SUBS_LOOP_WORDS/ROWS/STACK по частям).
TEMPLATES = ("AE_FULL", "SUBS_LOOP_WORDS", "SUBS_LOOP_WORDS_JOINED",
             "SUBS_LOOP_ROWS", "SUBS_LOOP_STACK", "SUBS_LOOP_STACK_JOINED")

PLACEHOLDER = re.compile(r"%\(([^)]*)\)")


class Spy(str):
    """Строка-шаблон, которая запоминает СВОЮ подстановку.

    Значение — настоящая строка шаблона (наследник str), поэтому сборка идёт как
    обычно; перехватывается только `%`. Записи складываются в общий список вместе
    с именем фрагмента: по ним проверяется и состав ключей, и остатки `%(`.
    """

    def __new__(cls, name, value, sink):
        self = super().__new__(cls, value)
        self.name = name
        self.sink = sink
        return self

    def __mod__(self, mapping):
        self.sink.append((self.name, dict(mapping)))
        return str.__mod__(self, mapping)

    def __getattr__(self, attr):          # .replace/.split/… — на настоящую строку
        return getattr(str(self), attr)


def _spy(sink):
    return {name: Spy(name, getattr(template, name), sink) for name in TEMPLATES}


def _fixture(tmp_path, name, gz=False):
    """Распаковать фикстуру в СВОЙ файл: два фикстуры-параметра одного теста делят
    tmp_path, и одинаковое имя (timeline.xml) затирало бы первую вторым XML."""
    dst = str(tmp_path / (os.path.splitext(name)[0] + ".xml"))
    src = os.path.join(HERE, "fixtures", name)
    if gz:
        with gzip.open(src, "rb") as g, open(dst, "wb") as f:
            shutil.copyfileobj(g, f)
    else:
        shutil.copy(src, dst)
    return dst


@pytest.fixture()
def xml_subs(tmp_path):
    return _fixture(tmp_path, "timeline_subs.xml.gz", gz=True)


@pytest.fixture()
def xml_nosubs(tmp_path):
    return _fixture(tmp_path, "timeline_nosubs.xml")


def _all_features_style():
    """Стиль, включающий ветки шаблона: у выключенной фичи подстановка пустая и её
    ветка в .jsx не попадает — тогда плейсхолдеры внутри неё не проверяются вовсе.

    Значения — НЕ дефолтные: у дефолта подстановка часто ровно «прежний текст», и
    подстановка с ключом не отличается от ветки без него. Здесь важно, чтобы ветка
    реально исполнилась, а не чтобы получилось красиво.
    """
    return {
        "hl_blur": True, "hl_blur_amt": 70.4, "hl_row_stack": True,
        "hl_row_anim": "row", "hl_bold": True,
        "intro_shadow": True, "intro_shade": True, "intro_roto_by_pos": True,
        "intro_cam": False, "intro_anchor": "first", "intro_anchor2": "first",
        "intro_fill": [1, 1, 1], "intro_hl_fill": [1, 0, 0],
        "accent_font": "Oswald-Bold", "back_font": "Oswald-Regular",
        "back_step_after": 0.5, "intro_big_step": 70.0,
        "sub_bg": True, "sub_scale": 90.0, "sub_words_per_row": 3,
        "top_line": True, "caption": True,
        "start_blur": 25.0, "disclaimer_end": True, "disc_gap": 8.0,
        "cam1_zoom": "jump", "cam1_zoom_cx": 0.4, "cam1_zoom_cy": 0.3,
        "cam1_pan_x": 20, "cam1_pan_y": -15, "cam1_rot": 1.5,
        "insert_style": "cam1", "insert_anim": "rise", "insert_fx": "white",
        "insert_c1on2_x": 10.0, "insert_c1on2_y": -10.0,
        "insert_c1_x": 12.0, "insert_c1_y": -8.0,
        "insert_c2_x": 0.4, "insert_c2_y": 0.25,
        "insert_plate_file": "C:/x/plate.png",
        "lm_on": True, "lm_exposure": 0.5,
        "intro_riser": False,          # ризер тянет путь ассета с этой машины
        "roto": False,
    }


# Интро со всеми ветками строк: большое слева, задний план, акцент, свой цвет,
# счётчик, жёлтое со свечением и глитч-группа (Deep Glow/Tritone). Раскладка строк
# разная, поэтому INTRO_LY/LX/LK и intro*-функции шаблона получают непустые подстановки.
INTRO_STYLED = [
    {"words": ["8"], "color": "white", "times": [1.0], "big": True},
    {"words": ["ФОН"], "color": "white", "times": [1.3], "back": True},
    {"words": ["АКЦЕНТ"], "color": "accent", "times": [1.6], "accent": True},
    {"words": ["ЦВЕТ"], "color": "custom", "times": [1.9], "fill": [0.2, 0.4, 0.9]},
    {"words": ["ЖЁЛТОЕ"], "color": "yellow", "times": [2.2], "fx": "glow"},
    {"words": ["ГЛИТЧ"], "color": "yellow", "times": [2.5], "anim": "glitch", "gy": 600},
    {"words": ["ДВЕНАДЦАТЬ"], "color": "custom", "times": [3.0], "is_count": True,
     "dec": 2, "fill": [0.1, 0.8, 0.3]},
    {"words": ["ПЕРВОЕ", "ВТОРОЕ"], "color": "yellow", "times": [5.4, 5.7]},
]
INTRO_SPLITS = [1, 4, 7]


def _build(xml, tmp_path, sink, style=None, name="out.jsx", **kw):
    """Собрать .jsx с перехватом подстановок. Фича-ветки включаем стилем."""
    st = _all_features_style()
    st.update(style or {})
    out = str(tmp_path / name)
    path, _nclips, _nsubs = build.to_ae_full(xml, jsx_path=out, style=st,
                                             emit=lambda *a: None, **kw)
    return path


def test_placeholders_match_build_keys(xml_subs, xml_nosubs, tmp_path, monkeypatch):
    """Набор `%(name)…` в каждом фрагменте = набор ключей подстановки; значений None нет,
    остатков `%(` в .jsx нет.

    Сборок несколько: бледная (одна камера, без фич) и полная (все ветки шаблона).
    Между ними покрываются все фрагменты и обе ветки каждого условия.
    """
    sink = []
    # Подменяем в КАЖДОМ модуле, который держит ссылку на фрагмент: build.py взял
    # AE_FULL, plan_subs.py — циклы субтитров (from .template import …); подмена
    # только в template.py их не перехватила бы.
    for mod in (build, plan_subs, template):
        for nm, val in _spy(sink).items():
            if hasattr(mod, nm):
                monkeypatch.setattr(mod, nm, val)

    ins = [{"type": "photo", "style": "cam1", "media": "C:/x/a.png", "start_s": 1, "dur_s": 2},
           {"type": "photo", "style": "cam2", "media": "C:/x/b.png", "start_s": 8.0, "dur_s": 1.5},
           {"type": "photo", "style": "cam2", "media": "C:/x/c.png", "start_s": 5.0, "dur_s": 3.0,
            "plate": True}]
    intro_style = {"accent_font": "TestInk-Regular", "back_font": "TestInk-Regular",
                   "intro_line_step": 200, "back_scale": 0.65}
    # Жёлтые фикстуры: 80/163/164/165 — идут подряд (стопка), 80 и 163 — слова-числа
    # («90», «5»), у 163 к тому же короткое появление (укороченный жёлтый).
    hl = [80, 163, 164, 165]
    # 1. Бледная сборка: одна камера, без интро, без вставок, дефолтные подстановки.
    _build(xml_nosubs, tmp_path, sink, name="plain.jsx",
           style={"insert_plate_file": None, "sub_words_per_row": 1},
           inserts=[], disclaimer="", intro=None, roto=False)
    # 2. Цикл субтитров ПО СЛОВУ: счётчик (sub_count_code) и укороченный жёлтый
    # (hl_dur_js/hl_blur_call) — эти подстановки живут только в ветке «по слову».
    _build(xml_subs, tmp_path, sink, name="words.jsx", inserts=[],
           disclaimer="", intro=None,
           style={"sub_words_per_row": 1, "hl_blur": True},
           highlights=hl, hl_count=[163], hl_joins=[164])
    # 3. Строки + стопка подряд жёлтых: sub_stack живёт только в ветке строк
    # (sub_words_per_row>1 при hl_row_stack). Своя сборка без интро: перенос
    # интро-слов сдвигает индексы жёлтых, и «подряд» перестаёт быть подряд.
    _build(xml_subs, tmp_path, sink, name="stack.jsx", inserts=[],
           disclaimer="", intro=None,
           style={"sub_words_per_row": 2, "hl_row_stack": True},
           highlights=[163, 164, 165, 166])
    # 4. Интро со всеми видами строк + вставки.
    _build(xml_subs, tmp_path, sink, name="full.jsx", inserts=ins,
           disclaimer="Подписка", intro=INTRO_STYLED, intro_splits=INTRO_SPLITS,
           intro_mode="word", style=dict(intro_style, hl_blur=True),
           highlights=hl, hl_joins=[164])
    # 5. То же интро построчно (ветка intro_mode=line: раскладка и анимации слоёв строк).
    _build(xml_subs, tmp_path, sink, name="line.jsx", inserts=ins,
           disclaimer="Подписка", intro=INTRO_STYLED, intro_splits=INTRO_SPLITS,
           intro_mode="line", style=dict(intro_style, hl_blur=True),
           highlights=hl, hl_joins=[164])
    # 6. Безголовый хвост рендера — своя ветка подстановок tail/imp_miss.
    _build(xml_subs, tmp_path, sink, name="render.jsx", inserts=ins,
           disclaimer="", intro=INTRO_STYLED, intro_splits=INTRO_SPLITS,
           render_dir=str(tmp_path / "exp"), highlights=hl, hl_count=[163])

    assert sink, "ни одна подстановка не перехвачена — сборка ушла мимо шаблона"

    # Наборы ключей у фрагментов пересекаются нарочно: циклы субтитров получают те же
    # данные, что и шаблон (их подставляет plan_subs тем же словарём). Поэтому сверяем
    # не «ключи фрагмента», а «каждый плейсхолдер ВСЕХ фрагментов кому-то передан»:
    # лишний ключ безвреден, а вот плейсхолдер без ключа — падение сборки.
    seen = {}
    for name, mapping in sink:
        seen.setdefault(name, set()).update(mapping)
    all_seen = set().union(*seen.values()) if seen else set()
    missing = []
    for name in TEMPLATES:
        ph = set(PLACEHOLDER.findall(str(getattr(template, name))))
        if ph - all_seen:
            missing.append("%s: %s" % (name, ", ".join(sorted(ph - all_seen))))
    assert not missing, ("в шаблоне есть плейсхолдеры, которых сборка не передала "
                         "(опечатка в имени ключа или ветка не покрыта фикстурой):\n"
                         + "\n".join(missing))
    # Каждый фрагмент обязан быть подставлен: набор ключей мог сойтись на другом, а
    # неподставленный фрагмент — та же ошибка, только молчаливая (шаблон уедет в .jsx
    # текстом). SUBS_LOOP_* выбираются парой (обычный/склеенный), поэтому проверяем
    # не «оба», а «хотя бы один из пары» — вторая половина отличается лишь данными.
    pairs = (("AE_FULL",),
             ("SUBS_LOOP_WORDS", "SUBS_LOOP_WORDS_JOINED"),
             ("SUBS_LOOP_ROWS",),
             ("SUBS_LOOP_STACK", "SUBS_LOOP_STACK_JOINED"))
    dead = ["/".join(p) for p in pairs if not any(n in seen for n in p)]
    assert not dead, ("фрагменты не подставились ни в одной из сборок — фикстура "
                      "перестала покрывать ветку: " + ", ".join(dead))

    # Синтетическая сверка: повторяем каждую перехваченную подстановку. Недостающий
    # ключ даёт KeyError с именем, значение None — видно в тексте, остаток `%(` —
    # не подставившийся фрагмент.
    for name, mapping in sink:
        bad_key = [k for k, v in mapping.items() if v is None]
        assert not bad_key, "%s: подстановка с None: %s" % (name, ", ".join(sorted(bad_key)))
        text = str(getattr(template, name)) % mapping
        assert "%(" not in text, ("%s: в подставленном тексте остались плейсхолдеры; "
                                  "подстановка не покрыла фрагмент" % name)


def test_no_percent_leftovers_in_rendered_jsx(xml_subs, tmp_path):
    """В собранном .jsx не остаётся `%(` — признак несделанной подстановки.

    Отдельно от перехвата: spy сравнивает ПОСЛЕ `%`, а тут проверяется то, что
    реально записано на диск, вместе с хвостом и обёрткой _dg_wrap.
    """
    out = _build(xml_subs, tmp_path, [], name="leftover.jsx",
                 inserts=[{"type": "photo", "style": "cam2", "media": "C:/x/a.png",
                           "start_s": 1, "dur_s": 2}],
                 disclaimer="Подписка", intro=INTRO_STYLED, intro_splits=INTRO_SPLITS)
    src = open(out, encoding="utf-8-sig").read()
    assert "%(" not in src, "в .jsx остались неразобранные плейсхолдеры шаблона"
    assert "%(name)" not in src
    assert "%(sub_loop)" not in src
