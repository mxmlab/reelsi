# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сторож схемы стиля: каждая ручка стиля влияет на сборку или числится в исключениях.

Регрессия с roto_bottom показала, что настройки, заданные «один раз», руками не
проверяются: панель записала проценты 35 вместо доли 0.35, сборка зажала их в 1.0,
и маска накрыла весь кадр.

Здесь:
1. Эталон: стиль BASE даёт побайтно тот же .jsx, что эталон golden_geometry.jsx.
2. Каждая ручка схемы style_schema.py (все 118 ключей: key, key2, toggle)
   параметризованно проверяется на влияние на собранный .jsx.
   Все ручки подписи (13), интро (13), рото (4), видео (1) и звука (11)
   снабжаются нужными фикстурами и проверяются в деле.
3. Исключения (цель LI2 — <= 5, здесь 0) стерегутся: ни один ключ не теряется молча.
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
from core import insertlib, roto, style_schema, styles, xml2ae  # noqa: E402
from tests.test_geometry_python import _build, _mask_assets  # noqa: E402

T_CAM1, T_CAM2 = 1.0, 8.3

# Набор вставок с фото и видео: активирует transition и transition_sfx
RICH_INSERTS = [
    {"type": "photo", "style": "cam2", "media": "C:/x/a.png", "start_s": 1, "dur_s": 2},
    {"type": "photo", "style": "cam2", "media": "C:/x/cam2.png", "start_s": 8.0, "dur_s": 1.5},
    {"type": "photo", "style": "cam1", "media": "C:/x/b.png", "start_s": 5, "dur_s": 3},
    {"type": "video", "style": "cam2", "media": "C:/x/vid.mp4", "start_s": 4, "dur_s": 2},
]

# Разметка интро: группа на кам1 (нижняя половина кадра gy=600, акцент, back, глитч)
# и группа на кам2 (перебивка с 2 строками для проверки intro_anchor2)
RICH_INTRO = [
    {"words": ["ПЕРВОЕ"], "color": "white", "times": [T_CAM1], "gy": 600},
    {"words": ["АКЦЕНТ"], "color": "accent", "accent": True, "times": [T_CAM1 + 0.5]},
    {"words": ["ФОНОВОЕ"], "color": "white", "back": True, "times": [T_CAM1 + 1.0]},
    {"words": ["ГЛИТЧ"], "color": "white", "anim": "glitch", "times": [T_CAM1 + 1.5]},
    {"words": ["ВТОРАЯ", "КАМЕРА"], "color": "white", "times": [T_CAM2]},
    {"words": ["ВТОРАЯ", "СТРОКА"], "color": "white", "times": [T_CAM2 + 0.5]},
]
RICH_INTRO_SPLITS = [4]

# Ключи-исключения, не влияющие на сборку. В LI2 сокращены до 0:
# все 118 ручек схемы проверяются в сборке.
EXCEPTIONS = {}

# Известные мёртвые ключи (ключ есть в схеме, но нигде не читается бэкендом).
# В LI2 pop_db реабилитирован (_sfx_cfg конкатенирует prefix + "_db"), мёртвых ключей 0.
KNOWN_DEAD_KEYS = set()

# Префиксы и суффиксы для составного чтения ключей (_sfx_cfg)
SFX_PREFIXES = {"pop", "transition_sfx", "intro_riser", "transition"}
SFX_SUFFIXES = {"_in", "_out", "_at", "_db"}

# Ручки подложки (задание ZK): сами по себе сборку не двигают — подложку включает галка
# У ВСТАВКИ (ins.plate), поэтому этим ключам нужен контекст со вставкой на подложке.
PLATE_KNOBS = {"insert_plate_file", "insert_plate_scale"}


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
    """Детерминированные метрики шрифтов: формулы зазоров (back_gap, disc_gap)
    считаются независимо от наличия системных шрифтов в окружении."""
    from core import fonts

    def mock_ink_extent(ps_name, text, size_px):
        if not text or not str(text).strip():
            return (0.0, 0.0)
        k = float(size_px)
        return (round(0.7 * k, 2), round(0.2 * k, 2))

    monkeypatch.setattr(fonts, "ink_extent", mock_ink_extent)


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
            if "items" in x:
                walk(x["items"], cur_toggles, cur_shows)

    walk(style_schema.LAYERS)
    return items


SCHEMA_ITEMS = _collect_schema_items()
ALL_SCHEMA_KEYS = sorted(SCHEMA_ITEMS.keys())
TESTED_KEYS = [k for k in ALL_SCHEMA_KEYS if k not in EXCEPTIONS]


def _build_source(xml, style=None, inserts=None, music_path=None, caption="Спикер Иван", highlights=None):
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
    if k == "start_blur_dur":
        st_setup["start_blur"] = 50.0
    elif k == "sub_rows_max":
        st_setup["sub_words_per_row"] = 3
    elif k == "hl_row_anim":
        # Ручка работает только в режиме строк, и нужен жёлтый в строке (задание ZH)
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
    elif k in ("cam1_take_min", "cam1_take_lo", "cam1_take_hi", "cam1_take_hold", "cam1_take_yellow"):
        st_setup["cam1_take_zoom"] = True
        if k == "cam1_take_min":
            return st_setup, 7.0
        if k == "cam1_take_yellow":
            st_setup["cam1_take_min"] = 3.0
            return st_setup, True
    elif k in ("cam1_head_x", "cam1_head_smooth", "cam1_head_min"):
        st_setup["cam1_head_follow"] = True
        if k == "cam1_head_min":
            return st_setup, 150.0
    elif k == "layer_order":
        return st_setup, ["intro", "photo", "roto", "video", "subs"]
    elif k in ("cam1_zoom_cx", "cam1_zoom_cy"):
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
        # show_if у подложки больше нет (задание ZK): файл виден всегда, а подложку
        # включает галка У ВСТАВКИ — её ставит контекст сборки (см. PLATE_KNOBS ниже).
        # Картинка настоящая: по её размеру считается высота плашки в прекомпе.
        return {}, _knob_png(tmp_path, "plate_knob.png", 512, 512)
    elif k == "insert_plate_scale":
        # файл подложки в контексте, отличие — масштаб плашки
        return {"insert_plate_file": _knob_png(tmp_path, "plate_scale.png", 800, 800)}, 105.0

    if kind == "toggle" or ctl == "bool" or isinstance(base_val, bool):
        return st_setup, not bool(base_val)

    if ctl in ("num", "int", "angle"):
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
    """3. Список исключений не растёт молча: каждый ключ либо тестируется, либо в EXCEPTIONS (цель <= 5)."""
    all_keys_set = set(ALL_SCHEMA_KEYS)
    tested_set = set(TESTED_KEYS)
    exc_set = set(EXCEPTIONS.keys())

    assert len(exc_set) <= 5, f"Число исключений ({len(exc_set)}) превышает цель (<= 5)"
    assert tested_set | exc_set == all_keys_set, (
        "В схеме появились новые ключи без тестов и исключений: %s"
        % sorted(all_keys_set - (tested_set | exc_set))
    )
    assert tested_set.isdisjoint(exc_set), (
        "Ключи одновременно в проверенных и в исключениях: %s" % sorted(tested_set & exc_set)
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
    if knob_key.startswith("cam1_head_"):
        synthetic = {
            "v": 1,
            "fps": 10,
            "w": 2160,
            "h": 3840,
            "pts": [[i / 10, 0.5 + 0.15 * math.sin(i / 15)] for i in range(0, 3000)],
        }
        monkeypatch.setattr("core.headtrack.load_cached", lambda xml_path, video: synthetic)
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
                opts = ["pulse", "drift", "jump"]
                test_st[s_key] = [x for x in opts if x != s["ne"]][0]
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
                opts = ["pulse", "drift", "jump"]
                ref_st[s_key] = [x for x in opts if x != s["ne"]][0]
            elif "in" in s:
                ref_st[s_key] = s["in"][0]
    if knob_key not in st_setup:
        ref_st[knob_key] = base_val

    hl = [0, 1, 11] if knob_key == "cam1_take_yellow" else None
    ins = None
    if knob_key in PLATE_KNOBS:
        # Подложка включается галкой У ВСТАВКИ (задание ZK), а не галкой стиля: без неё
        # ручки стиля (файл и масштаб плашки) на сборку не влияют вовсе. Картинка вставки
        # настоящая (nobg_path читает файл с диска), rembg подменяем — onnx-модели на CPU
        # в тестах не место.
        ins = [{"type": "photo", "style": "cam2",
                "media": _knob_png(tmp_path, "plate_src.png", 320, 240),
                "start_s": 1, "dur_s": 2, "plate": True}]
        monkeypatch.setattr(insertlib, "remove_bg",
                            lambda data, trim=True, emit=None: _knob_png_bytes(320, 240))
    ref_jsx = _mask_assets(_build_source(xml_subs, style=ref_st, music_path=music_file,
                                         highlights=hl, inserts=ins))
    test_jsx = _mask_assets(_build_source(xml_subs, style=test_st, music_path=music_file,
                                          highlights=hl, inserts=ins))

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
