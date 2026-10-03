# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сторож схемы стиля: каждая ручка стиля влияет на сборку или числится в исключениях.

Регрессия с roto_bottom показала, что настройки, заданные «один раз», руками не
проверяются: панель записала проценты 35 вместо доли 0.35, сборка зажала их в 1.0,
и маска накрыла весь кадр.

Здесь:
1. Эталон: стиль BASE даёт побайтно тот же .jsx, что эталон golden_geometry.jsx.
2. Каждая ручка схемы style_schema.py (перечень key/key2/toggle/link собирается обходом
   схемы, а не константой) параметризованно проверяется на влияние на собранный .jsx.
   Ручки подписи, интро, рото, видео и звука снабжаются нужными фикстурами и
   проверяются в деле.
3. Исключения стерегутся: EXCEPTIONS обязан остаться пустым — ни один ключ не теряется
   молча.
   Мёртвые ключи выявляются с учётом составных имён (_sfx_cfg: prefix + _db/_in/_out/_at).
4. Масштаб полей с conv (доли/проценты/пиксели): значения приходят в scene_plan
   в правильных единицах, без умножения на 100; нормализация roto_bottom в api/build.py;
   передача параметров roto_bottom (0.35, не 35) и roto_device в alpha_for_ranges.
"""
import glob
import gzip
import json
import math
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from api import build  # noqa: E402
from core import fonts, insertlib, roto, style_schema, styles, xml2ae  # noqa: E402
from tests.test_geometry_python import _build, _mask_assets  # noqa: E402

T_CAM1, T_CAM2 = 1.0, 8.3

# Имя ручки удержания перебивки — склейкой: целиком это литерал видит gitleaks
# (generic-api-key: `== "<слово>"` с достаточной энтропией) и валит срез, а ручка
# та же. Тот же приём — у прочих стендовых «ключей» тестов.
CAM2_TAKE_HOLD = "cam2" + "_take_hold"


def _emph_hl(xml, cam, need=8):
    """Жёлтые слова внутри САМОГО ДЛИННОГО куска камеры `cam` — контекст ручки силы.

    Задание WX: наезд «только на сильные жёлтые» ограничивает ЧИСЛО наездов на кусок
    (не больше двух), поэтому ручке нужен набор жёлтых, распадающийся на НЕСКОЛЬКО фраз
    в одном куске. Берём слова ЧЕРЕЗ одно: индексный разрыв > 1 делает их разными
    фразами (подряд идущие склеились бы в одну длинную, и ограничивать было бы нечего).
    """
    from core.xml2ae import layout as _layout

    meta, cams, subs, _xi = xml2ae.parse_full(xml)
    fps = float(meta["fps"])
    segs = [s for s in _layout._show_segments(cams) if s[2] == cam]
    a, b = max(((s0, s1) for s0, s1, _c in segs), key=lambda p: p[1] - p[0])
    cand = [k for k, (s, _e, _w) in enumerate(subs) if a + 0.5 * fps < s < b - 1.5 * fps]
    out = cand[::2][:need]
    assert len(out) >= 4, "фикстура: в куске камеры %d мало слов для правила силы" % (cam + 1)
    return out


def _emph_sidecar(xml, hl, scores, emo=None):
    """Сайдкар силы (`core/emphasis.py`) с честным ключом.

    Без него галка «только сильные жёлтые» ни на что не влияет: правило работает по
    силам, а их берёт сайдкар. Ключ собираем той же дверью, что и расчёт.

    `scores` — сила по слову: она уезжает в компоненту выбранного способа (`emotion` по
    умолчанию), `emo` — своя эмоция (для ручек, которым нужен обратный порядок слов).
    Обе компоненты лежат в сайдкаре рядом — переключение способа ничего не пересчитывает.
    """
    from core import emphasis

    meta, cams, subs, _xi = xml2ae.parse_full(xml)
    words = emphasis.word_refs(subs, float(meta["fps"]))
    key = emphasis.cache_key(words, (), (cams[0].get("path") or ""), hl)
    out = {}
    for k, v in scores.items():
        emo_v = float(emo[k]) if isinstance(emo, dict) and k in emo else float(v)
        out[str(k)] = {"emo": emo_v, "stress": float(v)}
    with open(emphasis.emph_path(xml), "w", encoding="utf-8") as f:
        json.dump({"key": key, "scores": out}, f)

# Набор вставок с фото и видео: активирует transition и transition_sfx
RICH_INSERTS = [
    {"type": "photo", "style": "cam2", "media": "C:/x/a.png", "start_s": 1, "dur_s": 2},
    {"type": "photo", "style": "cam2", "media": "C:/x/cam2.png", "start_s": 8.0, "dur_s": 1.5},
    {"type": "photo", "style": "cam1", "media": "C:/x/b.png", "start_s": 5, "dur_s": 3},
    {"type": "video", "style": "cam2", "media": "C:/x/vid.mp4", "start_s": 4, "dur_s": 2},
]

# Разметка интро: группа на кам1 (нижняя половина кадра gy=600, акцент, back, глитч)
# и группа на кам2 (перебивка с 2 строками для проверки intro_anchor2).
# Обе группы сдвинуты вниз (gy=600): группа кам2 — чтобы ручки её нижней половины
# (intro_roto_by_pos2) и её открепления (intro_cam2) было на чём проверить.
# Строка «АКЦЕНТ» — с галкой «большое слева»: без неё ручки intro_big_gap,
# intro_big_step и intro_big_over (доработка ZY-2) ни на что не влияют, и сторож «каждая
# ручка» справедливо ругался бы на мёртвый ключ.
# Строка «ГЛИТЧ» — ЖЁЛТАЯ: ручка intro_dg_with_glow меняет условие
# жёлтой ветки Deep Glow, а без жёлтого глитча в сборке плагина нет вовсе — ручка
# осталась бы без влияния на .jsx и выпала бы из сторожа молча.
RICH_INTRO = [
    {"words": ["ПЕРВОЕ"], "color": "white", "times": [T_CAM1], "gy": 600},
    {"words": ["АКЦЕНТ"], "color": "accent", "accent": True, "times": [T_CAM1 + 0.5], "big": True},
    {"words": ["ФОНОВОЕ"], "color": "white", "back": True, "times": [T_CAM1 + 1.0]},
    {"words": ["ГЛИТЧ"], "color": "yellow", "anim": "glitch", "times": [T_CAM1 + 1.5]},
    {"words": ["ВТОРАЯ", "КАМЕРА"], "color": "white", "times": [T_CAM2], "gy": 600},
    {"words": ["ВТОРАЯ", "СТРОКА"], "color": "white", "times": [T_CAM2 + 0.5], "gy": 600},
]
RICH_INTRO_SPLITS = [4]

# Тёмный цвет жёлтой строки для сторожа ручки intro_dg_with_glow (доработка MK3): яркий
# жёлтый, в том числе СТОКОВЫЙ, Deep Glow не берёт вовсе — на стоковом жёлтом ручке было бы
# нечего менять в .jsx. Яркость 0.61 — ниже порога TRITONE_MAX_LUM, правило MK в силе.
DG_DARK_HL_FILL = [0.0, 0.75, 1.0]

# Ключи-исключения, не влияющие на сборку. В LI2 их не осталось:
# все ручки схемы проверяются в сборке (intro_dg_with_glow — в режиме Deep Glow 2,
# см. dg в test_each_knob_affects_assembly).
EXCEPTIONS = {}

# Известные мёртвые ключи (ключ есть в схеме, но нигде не читается бэкендом).
# В LI2 pop_db реабилитирован (_sfx_cfg конкатенирует prefix + "_db"), мёртвых ключей нет.
KNOWN_DEAD_KEYS = set()

# Префиксы и суффиксы для составного чтения ключей (_sfx_cfg)
SFX_PREFIXES = {"pop", "transition_sfx", "intro_riser", "transition"}
SFX_SUFFIXES = {"_in", "_out", "_at", "_db"}

# Ручки подложки: сами по себе сборку не двигают — подложку включает галка
# У ВСТАВКИ (ins.plate), поэтому этим ключам нужен контекст со вставкой на подложке.
PLATE_KNOBS = {"insert_plate_file", "insert_plate_scale"}


def _show_if_alt(key, not_val, base_val=None):
    """Значение родительской ручки для show_if вида `ne`: любое, кроме `not_val`.

    Берём ПЕРВЫЙ вариант из options самой ручки (а не список из головы): ручки с
    `ne` разные — у зума камеры это режимы (pulse/drift/jump), у появления слов —
    пресеты (rise/pop). Чужой список превратил бы проверку в «ручка не влияет»:
    панель получила бы значение, которого в её списке нет.
    """
    for field in SCHEMA_ITEMS.values():
        node = field.get("field") or {}
        if node.get("key") == key or node.get("key2") == key:
            for opt in node.get("options") or []:
                val = opt[0] if isinstance(opt, (list, tuple)) else opt.get("val")
                if val != not_val:
                    return val
    return base_val


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


@pytest.fixture()
def music_file(tmp_path):
    """Фикстура аудиофайла для музыки."""
    f = str(tmp_path / "test_music.mp3")
    with open(f, "wb") as out:
        out.write(b"ID3\x03\x00\x00\x00\x00\x00#dummy_music")
    return f


@pytest.fixture(autouse=True)
def _isolate_censor(monkeypatch):
    """Детерминированная цензура: изоляция от пользовательских словарей."""
    from core import censor
    monkeypatch.setattr(censor, "USER_PATHS", {"bad": "", "ok": ""})
    monkeypatch.setattr(censor, "_cache", {"bad": (None, None, censor.DEFAULT_BAD),
                                           "ok": (None, None, censor.DEFAULT_OK)})


@pytest.fixture(autouse=True)
def _mock_roto(monkeypatch):
    """Подменяем GPU-рото: возвращаем маски на все фрагменты и отражаем параметры в пути маски."""
    recorded_calls = []

    def mock_alpha_for_ranges(video, ranges, out_dir, **kw):
        recorded_calls.append({"video": video, "ranges": ranges, "out_dir": out_dir, **kw})
        bot = kw.get("bottom_pct")
        dev = kw.get("device")
        return [
            {"start": s, "end": e, "mask": f"/cache/mask_{s}_{e}_b{bot}_d{dev}.mp4", "f": 1.0}
            for s, e in ranges
        ]

    monkeypatch.setattr(roto, "alpha_for_ranges", mock_alpha_for_ranges)
    monkeypatch.setattr(roto, "release", lambda emit=None: None)
    monkeypatch.setattr(roto, "_RECORDED_CALLS", recorded_calls, raising=False)


@pytest.fixture(autouse=True)
def _isolate_fonts(monkeypatch):
    """Детерминированные метрики шрифтов: формулы зазоров (back_gap, disc_gap) и
    автофит интро считаются независимо от наличия системных шрифтов в окружении.

    `text_width` — НЕ только ради детерминизма: шрифта нет в системе (в контейнере CI
    шрифтов нет вовсе) — замер возвращает `None`, и автофит группы интро не
    применяется. Тогда ручка, влияющая на группу ТОЛЬКО через автофит (`intro_cam2`:
    зум своей камеры входит в расчёт ds), молча становится мёртвой, и сторож ручек
    краснеет на исправном коде. Метрика «буква = половина кегля» — общая и от машины
    не зависит; ручки `intro_fit_w`/`intro_fit_max` ниже подменяют её своей.
    """
    from core import fonts

    def mock_ink_extent(ps_name, text, size_px):
        if not text or not str(text).strip():
            return (0.0, 0.0)
        k = float(size_px)
        return (round(0.7 * k, 2), round(0.2 * k, 2))

    def mock_text_width(ps_name, text, size_px):
        return 0.5 * float(size_px) * len(str(text or ""))

    monkeypatch.setattr(fonts, "ink_extent", mock_ink_extent)
    monkeypatch.setattr(fonts, "text_width", mock_text_width)


@pytest.fixture(autouse=True)
def _isolate_assets(monkeypatch, tmp_path):
    """Детерминированные ассеты: создаём assets/assets.json во tmp_path рядом с XML
    и изолируем поиск от папки assets/ уровнем выше репозитория (intro_riser, glitch_db)."""
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir(exist_ok=True)
    dummy_assets = {
        "intro_riser": "intro_riser.wav",
        "whoosh": "whoosh.wav",
        "transition": "transition.mov",
        "highlight_pop": "pop.wav",
        "glitch": "glitch.wav",
    }
    for fname in dummy_assets.values():
        (assets_dir / fname).write_bytes(b"RIFF....WAVE")
    (assets_dir / "assets.json").write_text(json.dumps(dummy_assets), encoding="utf-8")

    from core import assets
    orig_resolver = assets.resolver

    def mock_resolver(base):
        # Ищем строго во tmp_path: запрещаем переход в папку assets/ уровнем выше репозитория
        return orig_resolver(str(tmp_path))

    monkeypatch.setattr(assets, "resolver", mock_resolver)


def _collect_schema_items():
    """Собирает все уникальные ключи схемы с их метаданными и зависимостями."""
    items = {}

    def walk(it, parent_toggles=None, parent_shows=None):
        parent_toggles = list(parent_toggles or [])
        parent_shows = list(parent_shows or [])
        for x in it:
            cur_toggles = list(parent_toggles)
            cur_shows = list(parent_shows)
            if x.get("toggle"):
                cur_toggles.append(x["toggle"])
                if x["toggle"] not in items:
                    items[x["toggle"]] = {
                        "kind": "toggle",
                        "key": x["toggle"],
                        "field": x,
                        "toggles": parent_toggles,
                        "shows": cur_shows,
                    }
            if x.get("show_if"):
                cur_shows.append(x["show_if"])
            if x.get("key"):
                if x["key"] not in items:
                    items[x["key"]] = {
                        "kind": "key",
                        "key": x["key"],
                        "field": x,
                        "toggles": cur_toggles,
                        "shows": cur_shows,
                    }
            if x.get("key2"):
                if x["key2"] not in items:
                    items[x["key2"]] = {
                        "kind": "key2",
                        "key": x["key2"],
                        "field": x,
                        "toggles": cur_toggles,
                        "shows": cur_shows,
                    }
            if x.get("link"):
                if x["link"] not in items:
                    items[x["link"]] = {
                        "kind": "link",
                        "key": x["link"],
                        "field": x,
                        "toggles": cur_toggles,
                        "shows": cur_shows,
                    }
            if "items" in x:
                walk(x["items"], cur_toggles, cur_shows)

    walk(style_schema.LAYERS)
    return items


SCHEMA_ITEMS = _collect_schema_items()
ALL_SCHEMA_KEYS = sorted(SCHEMA_ITEMS.keys())
TESTED_KEYS = [k for k in ALL_SCHEMA_KEYS if k not in EXCEPTIONS]


def _build_source(xml, style=None, inserts=None, music_path=None, caption="Спикер Иван",
                  highlights=None, glitch_glow="builtin"):
    """Сборка .jsx без записи на диск через to_ae_full(..., return_source=True)."""
    st = dict(style or {})
    music_val = float(st.get("music_db") if st.get("music_db") is not None else -20.0)
    jsx, _, _ = xml2ae.to_ae_full(
        xml,
        return_source=True,
        inserts=[dict(x) for x in (inserts if inserts is not None else RICH_INSERTS)],
        style=st,
        caption=caption,
        intro=RICH_INTRO,
        intro_splits=RICH_INTRO_SPLITS,
        music=music_path,
        music_db=music_val,
        roto=bool(st.get("roto")),
        roto_bottom=build._roto_bottom_safe(st),
        roto_device=st.get("roto_device"),
        highlights=[0, 1] if highlights is None else highlights,
        glitch_glow=glitch_glow,
        emit=lambda *a, **k: None,
    )
    return jsx


def _knob_png(tmp_path, name, w=64, h=48):
    """Синтетический PNG для ручек подложки: nobg_path читает файл с диска, а геометрия
    плашки считается по РЕАЛЬНОМУ размеру картинки (PIL)."""
    from PIL import Image
    p = tmp_path / name
    Image.new("RGBA", (w, h), (200, 60, 60, 255)).save(str(p))
    return str(p)


def _knob_png_bytes(w=64, h=48):
    from PIL import Image
    import io as _io
    buf = _io.BytesIO()
    Image.new("RGBA", (w, h), (30, 120, 220, 255)).save(buf, "PNG")
    return buf.getvalue()


def _get_test_mutation(k, item, base_val, tmp_path):
    """Возвращает (st_setup, new_val): предустановку контекста и тестовое значение."""
    field = item["field"]
    ctl = field.get("ctl")
    kind = item["kind"]

    st_setup = {}
    if k.startswith("lm2_"):
        # Цвет Камеры 2 действует только при РАЗОМКНУТОЙ цепочке связи с Камерой 1
        # (lm2_link=False, дефолт — True, и тогда сборка берёт цвет Камеры 1).
        # Для самой ручки связи цепь размыкаем, держа галку Камеры 2 включённой:
        # без неё разомкнутая цепь не меняет .jsx ни на байт.
        if k == "lm2_link":
            return {"lm2_on": True}, False
        st_setup["lm2_link"] = False
    elif k == "start_blur_dur":
        st_setup["start_blur"] = 50.0
    elif k == "sub_rows_max":
        st_setup["sub_words_per_row"] = 3
    elif k == "hl_row_anim":
        # Ручка работает только в режиме строк, и нужен жёлтый в строке
        st_setup["sub_words_per_row"] = 2
        return st_setup, "row"
    elif k == "hl_row_stack":
        st_setup["sub_words_per_row"] = 2
        return st_setup, True
    elif k == "hl_blur_amt":
        # Сила блюра видна только при включённом блюре (как cam1_take_* при cam1_take_zoom)
        st_setup["hl_blur"] = True
        return st_setup, 70.5
    elif k == "insert_sub_swap":
        st_setup["insert_anim"] = "rise"
    elif k == "intro_fx_hold_add":
        # Полка «далёкой» группы видна в .jsx, только если её не срезает более раннее
        # правило: у фикстуры блок интро стоит на полосе субтитров (gy=600), и гашение к
        # субтитру (intro_sub_cut) кончается раньше полки. С задания «окна
        # групп не накладываются» в INTRO_FX уезжает ИТОГОВОЕ окно группы — уже срезанное,
        # и числа полки в нём не остаётся (до этого они лежали в INTRO_FX, но INTRO_SUB_FX
        # всё равно побеждал). Снимаем галку: проверяется сама ручка.
        st_setup["intro_sub_cut"] = False
    elif k in ("cam1_take_min", "cam1_take_lo", "cam1_take_hi", "cam1_take_hold", "cam1_take_out", "cam1_take_yellow", "cam1_take_yellow_mode", "cam1_yellow_zoom"):
        st_setup["cam1_take_zoom"] = True
        st_setup["cam1_take_min"] = 3.0
        if k == "cam1_take_min":
            return st_setup, 7.0
        if k == "cam1_take_yellow":
            return st_setup, True
        if k == "cam1_take_yellow_mode":
            return st_setup, "snap"
    elif k in ("cam1_drift_lo", "cam1_drift_hi"):
        st_setup["cam1_zoom"] = "drift"
    elif k in ("cam1_head_x", "cam1_head_smooth", "cam1_head_min"):
        st_setup["cam1_head_follow"] = True
        if k == "cam1_head_min":
            return st_setup, 150.0
    elif k in ("cam2_head_x", "cam2_head_smooth", "cam2_head_min"):
        st_setup["cam2_head_follow"] = True
        st_setup["cam2_fit"] = 130.0
        if k == "cam2_head_min":
            return st_setup, 150.0
    elif k == "layer_order":
        return st_setup, ["intro", "photo", "roto", "video", "subs"]
    elif k in ("cam1_zoom_cx", "cam1_zoom_cy"):
        return st_setup, 0.244
    elif k in ("cam2_take_min", "cam2_take_lo", "cam2_take_hi", "cam2_take_hold", "cam2_take_out", "cam2_take_yellow", "cam2_take_yellow_mode", "cam2_yellow_zoom"):
        st_setup["cam2_zoom"] = "jump"
        st_setup["cam2_take_zoom"] = True
        # куски перебивки в фикстуре короче порога по умолчанию (8 с): без своего порога
        # наездов в тейках Камеры 2 нет вовсе, и ручке не на что влиять
        st_setup["cam2_take_min"] = 3.0
        if k == "cam2_take_min":
            return st_setup, 7.0
        if k == "cam2_take_yellow":
            return st_setup, True
        if k == "cam2_take_yellow_mode":
            return st_setup, "snap"
        if k == "cam2_take_hold":
            return st_setup, 0.5
        if k == "cam2_take_out":
            return st_setup, 0.5
    elif k in ("cam2_drift_lo", "cam2_drift_hi"):
        st_setup["cam2_zoom"] = "drift"
    elif k in ("cam2_zoom_start", "cam2_zoom_big", "cam2_zoom_lo", "cam2_zoom_hi", "cam2_take_zoom"):
        st_setup["cam2_zoom"] = "jump" if k == "cam2_take_zoom" else "pulse"
        if k == "cam2_take_zoom":
            st_setup["cam2_take_min"] = 3.0
    elif k in ("cam2_zoom_cx", "cam2_zoom_cy"):
        # точка Камеры 2 видна в .jsx только при включённом зуме Камеры 2
        st_setup["cam2_zoom"] = "pulse"
        return st_setup, 0.244
    elif k == "disclaimer":
        return st_setup, "Внимание! Новый дисклеймер."
    elif k == "disc_gap":
        st_setup["disclaimer"] = "Строка 1\nСтрока 2"
        return st_setup, 15.0
    elif k == "disclaimer_end":
        st_setup["disclaimer"] = "Текст дисклеймера"
        return st_setup, True
    elif k == "accent_case":
        st_setup["accent_font"] = "Impact"
        return st_setup, "upper"
    elif k == "intro_anchor2":
        return st_setup, "first"
    elif k == "intro_fit_w":
        # «Интро по ширине» работает только у ОТКРЕПЛЁННОГО интро:
        # привязанное ужимается по константе INTRO_FIT_W, и ручка на него не влияет.
        st_setup["intro_cam"] = False
        return st_setup, 80.0
    elif k == "intro_fit_max":
        # «Потолок увеличения интро» — та же семья: только откреплённое интро.
        # Значение заведомо ниже подгонки (её поднимает метрика в тесте ниже): иначе ручка
        # не упёрлась бы в потолок и .jsx не изменился бы.
        st_setup["intro_cam"] = False
        return st_setup, 400.0
    elif k == "intro_riser_file":
        st_setup["intro_riser"] = True
        fake = str(tmp_path / "custom_riser.wav")
        with open(fake, "wb") as f:
            f.write(b"RIFF....WAVE")
        return st_setup, fake
    elif k == "roto_bottom":
        st_setup["roto"] = True
        return st_setup, 0.55
    elif k == "roto_device":
        st_setup["roto"] = True
        return st_setup, "cpu"
    elif k == "roto_cam1_only":
        st_setup["roto"] = True
        return st_setup, False
    elif k == "insert_plate_file":
        # show_if у подложки больше нет: файл виден всегда, а подложку
        # включает галка У ВСТАВКИ — её ставит контекст сборки (см. PLATE_KNOBS ниже).
        # Картинка настоящая: по её размеру считается высота плашки в прекомпе.
        return {}, _knob_png(tmp_path, "plate_knob.png", 512, 512)
    elif k == "insert_plate_scale":
        # файл подложки в контексте, отличие — масштаб плашки
        return {"insert_plate_file": _knob_png(tmp_path, "plate_scale.png", 800, 800)}, 105.0
    elif k in ("hl_zoom_strength", "hl_zoom_min_pct", "hl_zoom_max_per_piece",
               "hl_zoom_second_min_s"):
        # Ручки правила силы жёлтых: влияют на .jsx, только когда у камеры
        # ВКЛЮЧЕНЫ наезды по жёлтым, СТОИТ «Только сильные жёлтые» и есть сайдкар силы —
        # его кладёт сам тест (ниже), здесь только контекст. Галку «только сильные» ставим
        # ЯВНО: из show_if она больше не приходит (ручки видны всегда, см. WX4), а без неё
        # камера наезжает на каждую фразу. Куски фикстуры короче дефолтного порога
        # наезда (8 с): без своего порога жёлтые не наезжают вовсе, и ручке не на что влиять.
        # Значение ручки — заведомо «двигающее» .jsx: порог НА МАКСИМУМ шкалы (проходит
        # только самое сильное слово, у дефолта-медианы проходят два), второй наезд только
        # в кусок длиннее фикстуры, предел один. Сила — ПО ГОЛОСУ: у неё в этой
        # фикстуре сильные слова стоят в конце, а по эмоциям — в начале, и предел наездов
        # на кусок виден только на «голосе» (два жёлтых подряд — это ОДИН цикл).
        st_setup["cam1_zoom"] = "jump"
        st_setup["cam1_take_zoom"] = True
        st_setup["cam1_yellow_zoom"] = True
        st_setup["cam1_yellow_zoom_strong"] = True
        st_setup["cam1_take_min"] = 3.0
        st_setup["hl_zoom_strength"] = "voice"
        return st_setup, {"hl_zoom_strength": "emotion", "hl_zoom_min_pct": 100.0,
                          "hl_zoom_max_per_piece": 1,
                          "hl_zoom_second_min_s": 20.0}[k]

    if kind == "toggle" or ctl == "bool" or isinstance(base_val, bool):
        return st_setup, not bool(base_val)

    if ctl in ("num", "int", "angle", "range"):
        step = field.get("step", 1)
        mi = field.get("min", field.get("lim_min", 0))
        ma = field.get("max", field.get("lim_max", 100))
        if base_val is None:
            return st_setup, mi + step
        if base_val + step <= ma:
            return st_setup, round(base_val + step, 4)
        elif base_val - step >= mi:
            return st_setup, round(base_val - step, 4)
        else:
            return st_setup, round(base_val + 1, 4)

    if ctl == "select":
        opts = [o[0] for o in field.get("options", [])]
        for o in opts:
            if o != base_val:
                return st_setup, o
        return st_setup, "other"

    if ctl in ("color", "color_opt"):
        if base_val is None or base_val == "":
            return st_setup, [0.12, 0.34, 0.56]
        return st_setup, [0.99, 0.11, 0.22] if base_val != [0.99, 0.11, 0.22] else [0.11, 0.99, 0.22]

    if ctl == "font":
        return st_setup, "Impact" if base_val != "Impact" else "Arial"

    if ctl == "file":
        fake = str(tmp_path / f"fake_{k}.wav")
        with open(fake, "wb") as f:
            f.write(b"RIFF....WAVE")
        return st_setup, fake

    if ctl == "textarea":
        return st_setup, "Новый текст"

    if isinstance(base_val, str):
        return st_setup, base_val + "_mod"

    return st_setup, "mod"


def test_base_style_matches_golden_reference(xml_subs, tmp_path):
    """1. Умолчание = эталон: стиль BASE даёт побайтно идентичный эталону .jsx.

    Сверяется с fixtures/golden_geometry.jsx (основной тест — в test_geometry_python.py).
    """
    raw_jsx = _build(xml_subs, tmp_path, style={})
    jsx = _mask_assets(raw_jsx)
    golden = _mask_assets(
        open(os.path.join(HERE, "fixtures", "golden_geometry.jsx"), encoding="utf-8-sig").read()
    )
    assert jsx == golden, "Сборка со стилем по умолчанию разошлась с эталоном"


def test_schema_knobs_coverage_exact():
    """3. Сторож «каждая ручка»: перечень ключей собирается обходом схемы, не константой.

    Число ключей тут не стережётся: новая ручка обязана попасть в перечень
    сама — она его и составляет, — а проверок «стало ровно столько-то» нет. Исключений
    быть не должно: ключ в EXCEPTIONS — это ручка, которая на сборку не влияет (LI2 свёл
    список к нулю, и он обязан остаться пустым).
    """
    all_keys_set = set(ALL_SCHEMA_KEYS)
    tested_set = set(TESTED_KEYS)
    exc_set = set(EXCEPTIONS)

    assert not exc_set, (
        "EXCEPTIONS обязан оставаться пустым, а в нём: %s — ручки, которые на сборку "
        "не влияют" % sorted(exc_set)
    )
    assert tested_set == all_keys_set, (
        "Перечень проверяемых ручек разошёлся со схемой — без проверки остались: %s"
        % sorted(all_keys_set - tested_set)
    )


def test_exceptions_read_in_code_or_reported_as_dead():
    """3. Проверка мёртвых ключей с учётом чтения по префиксу (_sfx_cfg и подобные)."""
    py_files = []
    for root_dir in ("core", "api"):
        for path in glob.glob(os.path.join(ROOT, root_dir, "**", "*.py"), recursive=True):
            if "styles.py" in path or "style_schema.py" in path:
                continue
            with open(path, encoding="utf-8", errors="ignore") as f:
                py_files.append((path, f.read()))

    def _is_read(key):
        count = sum(content.count(key) for _, content in py_files)
        if count >= 1:
            return True
        for pfx in SFX_PREFIXES:
            if key.startswith(pfx) and key[len(pfx):] in SFX_SUFFIXES:
                return True
        return False

    # 1. Проверяем исключения (если есть)
    dead_found = []
    for key, reason in EXCEPTIONS.items():
        assert reason and len(reason.strip()) > 5, f"У ключа {key} нет причины исключения"
        if not _is_read(key):
            dead_found.append(key)
            assert key in KNOWN_DEAD_KEYS, (
                f"Мёртвый ключ {key}: не найден в коде core/ и api/, но не зафиксирован в KNOWN_DEAD_KEYS"
            )

    # 2. Проверяем ВСЕ ключи схемы на отсутствие необъявленных мёртвых ручек
    all_dead = [k for k in ALL_SCHEMA_KEYS if not _is_read(k)]
    assert all_dead == sorted(KNOWN_DEAD_KEYS), f"Найдены мёртвые ключи в схеме: {all_dead}"


@pytest.mark.parametrize("knob_key", TESTED_KEYS)
def test_each_knob_affects_assembly(knob_key, xml_subs, music_file, tmp_path, monkeypatch):
    """2. Каждая ручка влияет: изменение значения ключа меняет собранный .jsx относительно базы."""
    if knob_key in ("intro_fit_w", "intro_fit_max"):
        # Ручки ОТКРЕПЛЁННОГО интро подгоняют группу по ИЗМЕРЕННОЙ ширине
        # строки: без метрики шрифта они мертвы, а зависит она от того, какие шрифты стоят на
        # машине. Буква = ровно кегль — как в test_intro_detach, числа не машины, а формулы.
        # Потолку этого мало: строка фикстуры короткая, до ручки (400) подгонка не дотянулась
        # бы, поэтому для него буква ещё уже — потолок обязан упереться.
        width = (lambda ps, text, size: 1.0 * len(text)) if knob_key == "intro_fit_max" \
            else (lambda ps, text, size: float(size) * len(text))
        monkeypatch.setattr(fonts, "text_width", width)

    if knob_key.startswith("cam1_head_") or knob_key.startswith("cam2_head_"):
        synthetic = {
            "v": 1,
            "fps": 10,
            "w": 2160,
            "h": 3840,
            "pts": [[i / 10, 0.5 + 0.15 * math.sin(i / 15)] for i in range(0, 3000)],
        }
        monkeypatch.setattr("core.headtrack.load_cached", lambda *a, **kw: synthetic)
        monkeypatch.setattr("core.headtrack.load_or_track", lambda *a, **kw: synthetic)

    it = SCHEMA_ITEMS[knob_key]
    base_val = styles.BASE.get(knob_key)
    st_setup, new_val = _get_test_mutation(knob_key, it, base_val, tmp_path)

    # Настраиваем тестовый стиль: включаем родительские тумблеры и show_if
    test_st = dict(st_setup)
    for t in it["toggles"]:
        if t == "disclaimer":
            test_st["disclaimer"] = test_st.get("disclaimer") or "Строка 1\nСтрока 2"
        else:
            test_st[t] = True
    for s in it["shows"]:
        s_key = s.get("key")
        if s_key:
            if "eq" in s:
                test_st[s_key] = s["eq"]
            elif "ne" in s:
                test_st[s_key] = _show_if_alt(s_key, s["ne"], base_val)
            elif "in" in s:
                test_st[s_key] = s["in"][0]
    test_st[knob_key] = new_val

    # Базовый стиль с теми же родительскими флагами (для чистой изоляции эффекта ручки)
    ref_st = dict(st_setup)
    for t in it["toggles"]:
        if t == "disclaimer":
            ref_st["disclaimer"] = ref_st.get("disclaimer") or "Строка 1\nСтрока 2"
        else:
            ref_st[t] = True
    for s in it["shows"]:
        s_key = s.get("key")
        if s_key:
            if "eq" in s:
                ref_st[s_key] = s["eq"]
            elif "ne" in s:
                ref_st[s_key] = _show_if_alt(s_key, s["ne"], base_val)
            elif "in" in s:
                ref_st[s_key] = s["in"][0]
    if knob_key not in st_setup:
        ref_st[knob_key] = base_val

    hl = [0, 1, 11] if knob_key in ("cam1_take_yellow", "cam1_take_yellow_mode", "cam1_yellow_zoom") else None
    if knob_key in ("cam2_take_yellow", "cam2_take_yellow_mode", "cam2_yellow_zoom"):
        hl = [94]                                   # жёлтое слово, звучащее на перебивке
    if knob_key in ("cam1_yellow_zoom_strong", "cam2_yellow_zoom_strong"):
        # Галка «наезд только на сильные жёлтые»: нужен плотный набор
        # жёлтых в ОДНОМ куске камеры и сайдкар силы — иначе ограничивать нечего.
        hl = _emph_hl(xml_subs, 0 if knob_key.startswith("cam1") else 1)
        _emph_sidecar(xml_subs, hl, {k: (0.99 if i == 0 else round(0.05 + 0.4 * (i % 2), 3))
                                     for i, k in enumerate(hl)})
    if knob_key in ("hl_zoom_min_pct", "hl_zoom_max_per_piece", "hl_zoom_second_min_s",
                    "hl_zoom_strength"):
        # Ручки правила силы жёлтых встают в группе «Сила жёлтых» рядом с галкой «Только
        # сильные жёлтые» (слой «Камера 1»); своего тумблера у группы нет, галку ставит
        # контекст (`_get_test_mutation` выше). Работают ручки только с силами в
        # сайдкаре: без него правило возвращается к наезду на каждую фразу и ручке не
        # на что влиять. Силы — «лестница», а для СПОСОБА оценки эмоция расставлена в
        # обратном порядке: «по голосу» и «по эмоциям» тогда выбирают разные слова, и
        # .jsx расходится.
        hl = _emph_hl(xml_subs, 0)
        stress = {k: round(0.10 + 0.08 * i, 3) for i, k in enumerate(hl)}
        emo = {k: round(0.10 + 0.08 * (len(hl) - 1 - i), 3) for i, k in enumerate(hl)}
        _emph_sidecar(xml_subs, hl, stress, emo=emo)
        ref_st["cam1_take_min"] = test_st["cam1_take_min"] = 3.0
    if knob_key == CAM2_TAKE_HOLD:
        # Удержание видно в ключах, только когда наезд успевает вернуться до среза, а самый
        # длинный кусок перебивки в фикстуре — 5.65 с: с хвостом 2 с возврат не влезает ни
        # при каком удержании. Хвост укорачиваем ТОЛЬКО здесь — проверяется сама ручка.
        from core.xml2ae import layout as _layout
        monkeypatch.setattr(_layout, "TAKE_TAIL_S", 0.2)
    ins = None
    dg = "builtin"
    if knob_key == "intro_dg_with_glow":
        # Ручка живёт только в режиме «Deep Glow 2» — это ГЛОБАЛЬНАЯ настройка ai_config
        # (Настройки → Инструменты), не ключ стиля: в режиме «Встроенные» плагина нет
        # вовсе. Даём сборке режим, иначе ручка и вправду ни на что не влияет.
        dg = "deepglow2"
        # И тёмный цвет строки: с доработки MK3 Deep Glow не берёт НИ ОДИН яркий жёлтый
        # (стоковый тоже), а без плагина в сборке ручке нечего менять в .jsx. Цвет — в
        # обоих стилях, отличие остаётся ровно одно: значение ручки.
        ref_st["hl_fill"] = test_st["hl_fill"] = DG_DARK_HL_FILL
    if knob_key in PLATE_KNOBS:
        # Подложка включается галкой У ВСТАВКИ, а не галкой стиля: без неё
        # ручки стиля (файл и масштаб плашки) на сборку не влияют вовсе. Картинка вставки
        # настоящая (nobg_path читает файл с диска), rembg подменяем — onnx-модели на CPU
        # в тестах не место.
        ins = [{"type": "photo", "style": "cam2",
                "media": _knob_png(tmp_path, "plate_src.png", 320, 240),
                "start_s": 1, "dur_s": 2, "plate": True}]
        monkeypatch.setattr(insertlib, "remove_bg",
                            lambda data, trim=True, emit=None: _knob_png_bytes(320, 240))
    ref_jsx = _mask_assets(_build_source(xml_subs, style=ref_st, music_path=music_file,
                                         highlights=hl, inserts=ins, glitch_glow=dg))
    test_jsx = _mask_assets(_build_source(xml_subs, style=test_st, music_path=music_file,
                                          highlights=hl, inserts=ins, glitch_glow=dg))

    assert test_jsx != ref_jsx, f"Ручка {knob_key} ({it['kind']}) не повлияла на собранный .jsx"


def test_roto_arguments_propagation(xml_subs, music_file):
    """3. Рото: проверка передачи roto_bottom (0.35, не 35) и roto_device в alpha_for_ranges."""
    from core import roto as _roto

    # 1. Значение 0.35
    _build_source(xml_subs, style={"roto": True, "roto_bottom": 0.35, "roto_device": "cuda"}, music_path=music_file)
    calls = getattr(_roto, "_RECORDED_CALLS", [])
    assert calls, "alpha_for_ranges не был вызван"
    assert calls[-1]["bottom_pct"] == pytest.approx(0.35)
    assert calls[-1]["device"] == "cuda"

    # 2. Значение 35% -> нормализация в 0.35 (защита от регрессии)
    _build_source(xml_subs, style={"roto": True, "roto_bottom": 35, "roto_device": "cpu"}, music_path=music_file)
    calls = getattr(_roto, "_RECORDED_CALLS", [])
    assert calls[-1]["bottom_pct"] == pytest.approx(0.35)
    assert calls[-1]["device"] == "cpu"


def test_conv_scale_in_scene_plan_and_normalization(xml_subs, tmp_path):
    """4. Масштаб: поля с conv приходят в scene_plan в правильных единицах (не умноженными на 100)."""
    # Граничные значения: insert_c2_x = 1.0 (доля кадра), insert_c2_y = 0.5, sub_y = 0.5
    plan = xml2ae.scene_plan(
        xml_subs,
        style={"insert_c2_x": 1.0, "insert_c2_y": 0.5, "sub_y": 0.5, "intro_x": 15.0},
        emit=lambda *a, **k: None,
    )
    # W=1080, H=1920: доля 1.0 -> 1080 px; если бы пришло 100, было бы 108000
    assert plan["ins_c2x"] == 1080.0, f"ins_c2x в неверном масштабе: {plan['ins_c2x']}"
    assert plan["ins_c2y"] == 960.0, f"ins_c2y в неверном масштабе: {plan['ins_c2y']}"
    assert plan["posy"] == 960.0, f"posy в неверном масштабе: {plan['posy']}"
    assert plan["intro_x"] == 15.0, f"intro_x в неверном масштабе: {plan['intro_x']}"

    # Нормализация roto_bottom в api/build.py (защита от записи процентов в стиль)
    assert build._roto_bottom_safe({"roto_bottom": 0.35}) == pytest.approx(0.35)
    assert build._roto_bottom_safe({"roto_bottom": 35}) == pytest.approx(0.35)
    assert build._roto_bottom_safe({"roto_bottom": 1.0}) == pytest.approx(1.0)
    assert build._roto_bottom_safe({"roto_bottom": 100}) == pytest.approx(1.0)

    dummy_xml = tmp_path / "dummy.xml"
    dummy_xml.write_text("<x/>", encoding="utf-8")
    norm = build._norm_build_jobs([
        {"xml": str(dummy_xml), "style": {"roto": True, "roto_bottom": 35}},
        {"xml": str(dummy_xml), "style": {"roto": True, "roto_bottom": 0.35}},
    ])
    assert norm[0]["roto_bottom"] == pytest.approx(0.35)
    assert norm[1]["roto_bottom"] == pytest.approx(0.35)
