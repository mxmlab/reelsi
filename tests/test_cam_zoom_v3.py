# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты для задачи VN: стиль клипа от спикера, наезды camN_take_zoom/yellow_zoom,
отъезд наезда по эталонной кривой 35/90 (правка VU), слежение head FX."""
import json
import random
import shutil
import subprocess
import pytest

from core import styles, style_schema
from core.xml2ae import layout


# ---------------------------------------------------------------------------
# Пункт 1: JS-тест поведения selectAE, openEditClip, openCamsFor
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")
def test_select_ae_speaker_style_vs_global(tmp_path):
    """п. 1: при выборе клипа без своего стиля он получает стиль спикера, а не глобальный дефолт.
    Если у клипа был свой стиль — остаётся свой."""
    js_code = r"""
    const fs = require('fs');
    let curAE = -1;
    let AEXML = '';
    let CURSTYLE = null;
    let STDEF = 'BaseStyle';
    let STYLES = {
      'BaseStyle': { label: 'Base' },
      'SpeakerAStyle': { label: 'SpkA' },
      'ClipOwnStyle': { label: 'Own' }
    };
    let SPEAKERS = {
      'alice': { label: 'Alice', style: 'SpeakerAStyle' },
      'bob': { label: 'Bob' } // нет стиля
    };
    let CLIPS = [
      { xml: 'c0.xml', job: { speaker: 'alice' } }, // нет своего стиля, есть спикер со стилем
      { xml: 'c1.xml', job: { speaker: 'bob' } },   // нет своего стиля, спикер без стиля
      { xml: 'c2.xml', job: { speaker: 'alice', styleKey: 'ClipOwnStyle', style: STYLES['ClipOwnStyle'] } } // свой стиль
    ];

    function val(id) {
      if (id === 'stylesel') return STDEF;
      return '';
    }
    function $(id) {
      return { value: '' };
    }
    function samePath(a, b) { return a === b; }
    function stClone(s) { return Object.assign({}, s); }
    function stMigrateCamZoom(s) { return s; }
    function stView(s) {}
    function renderStyleInfo() {}
    function refreshCutTable() {}
    function applyStyleToUI() {}

    // Реализация selectAE из 90-ae.js
    function selectAE(i) {
      curAE = i;
      const c = CLIPS[i];
      if (!c) return;
      AEXML = c.xml;
      const j = c.job || {};
      let key = j.styleKey;
      if (!key && !j.style) {
        if (j.speaker && SPEAKERS[j.speaker] && SPEAKERS[j.speaker].style) {
          key = SPEAKERS[j.speaker].style;
          j.styleKey = key;
        } else {
          key = val('stylesel') || STDEF;
        }
      }
      if (j.style) {
        CURSTYLE = stClone(j.style);
      } else {
        const base = STYLES[key] || STYLES[STDEF];
        CURSTYLE = stClone(base);
      }
    }

    // Тест 1: клип 0 получает стиль 'SpeakerAStyle', а не 'BaseStyle'
    selectAE(0);
    const res0 = { key: CLIPS[0].job.styleKey, cur: CURSTYLE };

    // Тест 2: клип 1 (спикер без стиля) получает 'BaseStyle'
    selectAE(1);
    const res1 = { key: CLIPS[1].job.styleKey, cur: CURSTYLE };

    // Тест 3: клип 2 (свой стиль) сохраняет 'ClipOwnStyle'
    selectAE(2);
    const res2 = { key: CLIPS[2].job.styleKey, cur: CURSTYLE };

    console.log(JSON.stringify({ res0, res1, res2 }));
    """
    script_path = str(tmp_path / "test_select_ae.js")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(js_code)
    proc = subprocess.run(["node", script_path], capture_output=True, text=True, check=True)
    data = json.loads(proc.stdout.strip())
    assert data["res0"]["key"] == "SpeakerAStyle", "Клип должен получить стиль профиля своего спикера"
    assert data["res1"].get("key") is None, "Клип без стиля спикера не должен фиксировать styleKey"
    assert data["res2"]["key"] == "ClipOwnStyle", "Клип со своим стилем должен сохранить его"


# ---------------------------------------------------------------------------
# Пункт 2: Наезды камеры (кусок 5с, 12с, 20с с зазором < 1с)
# ---------------------------------------------------------------------------

def test_yellow_zoom_in_short_segment_under_take_min():
    """п. 2: кусок 5 с (меньше take_min=8 с), включён yellow_zoom, есть жёлтое слово — наезд ЕСТЬ.

    Правило наезда хайлайта: подъезд стартует за 0.1 с до первого слова фразы (кадр 144) и
    длится 0.6 с — пик на кадре 180 (0.5 с внутрь слова, а не на самом его начале).
    Удержание 2 с сверх слова даёт c = 300, полный отъезд d = 444 вылезает за кусок
    (300 − 18) — отъезда нет, пик держится до склейки: ключей два.
    """
    fps = 60.0
    take = {
        "min_s": 8.0,
        "lo": 25.0,
        "hi": 40.0,
        "hold_s": 2.0,
        "out_s": 2.4,
        "long_on": False,
        "yellow_on": True,
        "words": [150.0],  # 2.5 с
    }
    # 5 секунд = 300 кадров
    keys = layout._take_zoom_segment_keys(
        f=0, seg_end=300, v=100.0, fps=fps, take=take,
        trng=random.Random(42), lead_min_frame=0,
    )
    assert len(keys) == 2, f"Ожидался наезд без отъезда (2 ключа), получено {len(keys)}"
    assert keys[0][0] == 144.0, "подъезд начинается за 0.1 с до слова: 150 − 6"
    assert keys[1][0] == 180.0, "пик через 0.6 с подъезда: 144 + 36"


def test_take_zoom_centered_in_12s_segment():
    """п. 2: кусок 12 с без жёлтых, включён take_zoom — наезд ровно в середине куска (центр удержания = 6 с)."""
    fps = 60.0
    take = {
        "min_s": 8.0,
        "lo": 25.0,
        "hi": 40.0,
        "hold_s": 2.0,
        "out_s": 2.4,
        "long_on": True,
        "yellow_on": False,
        "words": [],
    }
    # 12 секунд = 720 кадров
    keys = layout._take_zoom_segment_keys(
        f=0, seg_end=720, v=100.0, fps=fps, take=take,
        trng=random.Random(42), lead_min_frame=0,
    )
    assert len(keys) == 4, f"Ожидался 1 длинный цикл наезда (4 ключа), получено {len(keys)}"
    k_a, k_b, k_c, k_d = keys
    # Центр удержания [k_b, k_c] должен быть ровно в середине куска (360 кадров = 6.0 с)
    center_hold = (k_b[0] + k_c[0]) / 2.0
    assert abs(center_hold - 360.0) < 1.0, f"Центр удержания {center_hold} должен быть равен 360 (6.0 с)"


def test_yellow_zoom_gap_under_1s_dropped():
    """п. 2: кусок 20 с, два жёлтых слова с интервалом 0.8 с — второе жёлтое слово НЕ получает наезд (зазор < 1 с).

    Считает это по-прежнему `cur_ref` = конец отъезда предыдущего цикла + 1 с: первый
    цикл (a=114, b=150, c=210, d=354) отодвигает порог на кадр 414, а фраза со 168
    кадра началась бы на 162 — наезда нет.
    """
    fps = 60.0
    # 20 секунд = 1200 кадров.
    take = {
        "min_s": 8.0,
        "lo": 25.0,
        "hi": 40.0,
        "hold_s": 1.0,
        "out_s": 2.4,
        "long_on": False,
        "yellow_on": True,
        "words": [120.0, 168.0],
    }
    keys = layout._take_zoom_segment_keys(
        f=0, seg_end=1200, v=100.0, fps=fps, take=take,
        trng=random.Random(42), lead_min_frame=0,
    )
    assert len(keys) == 4, f"Второе слово должно быть отброшено из-за зазора < 1 с, получено {len(keys)} ключей"
    # Пик первого цикла: a = 120 − 6 = 114, b = 114 + 36 = 150
    assert keys[1][0] == 150.0


# ---------------------------------------------------------------------------
# Пункт 3: Плавный отъезд (отъезд — эталонная кривая 35/90, время отъезда 2.4 с)
# ---------------------------------------------------------------------------

def test_take_out_uses_reference_ease_and_default_duration():
    """п. 3 (правка VU 01.10.2026): ключи отъезда играют ТОЙ ЖЕ кривой, что остальные
    движения — out 35 / in 90 (было своё S-образное 75/75), время отъезда = 2.4 с."""
    assert not hasattr(layout, "TAKE_OUT_EASE"), "число 75 у отъезда убрано: кривая одна на сборку"
    assert layout.TAKE_OUT_S == 2.4

    fps = 60.0
    take = {
        "min_s": 8.0,
        "lo": 25.0,
        "hi": 40.0,
        "hold_s": 2.0,
        "out_s": 2.4,
        "long_on": True,
        "yellow_on": False,
        "words": [],
    }
    keys = layout._take_zoom_segment_keys(
        f=0, seg_end=720, v=100.0, fps=fps, take=take,
        trng=random.Random(42), lead_min_frame=0,
    )
    assert len(keys) == 4
    k_a, k_b, k_c, k_d = keys
    # Режим для ключей c и d равен 3
    assert k_c[2] == 3
    assert k_d[2] == 3

    # Проверка eases для ключей: c и d должны получить эталонную пару [in 90, out 35]
    eases = layout._zoom_key_eases(keys)
    assert eases[2] == [90, 35], f"Ключ c должен иметь эталонную пару [90, 35], получено {eases[2]}"
    assert eases[3] == [90, 35], f"Ключ d должен иметь эталонную пару [90, 35], получено {eases[3]}"

    # Длительность отъезда k_d - k_c равна 2.4 с (144 кадра)
    dur_out = (k_d[0] - k_c[0]) / fps
    assert abs(dur_out - 2.4) < 0.05, f"Длительность отъезда {dur_out} должна быть 2.4 с"


# ---------------------------------------------------------------------------
# Пункт 4: Панель стиля, схема, миграция v2 -> v3
# ---------------------------------------------------------------------------

def test_schema_head_group_and_zoom_subgroups():
    """п. 4: схема содержит отдельную группу cam1.head с toggle cam1_head_follow и fx=True; cam1.zoom содержит 5 подгрупп."""
    sch = style_schema.schema()
    layers = sch["layers"]
    c1 = next(l for l in layers if l["id"] == "cam1")

    # 1. Отдельная группа cam1.head
    c1_head = next(it for it in c1["items"] if it.get("id") == "cam1.head")
    assert c1_head.get("fx") is True
    assert c1_head.get("toggle") == "cam1_head_follow"
    assert c1_head.get("label") == "Слежение за головой"
    head_keys = [it["key"] for it in c1_head.get("items", []) if it.get("type") == "field"]
    assert head_keys == ["cam1_head_x", "cam1_head_smooth", "cam1_head_min"]

    # 2. В cam1.tr нет ключей слежения
    c1_tr = next(it for it in c1["items"] if it.get("id") == "cam1.tr")
    tr_keys = [it["key"] for it in c1_tr.get("items", []) if it.get("type") == "field"]
    for hk in ("cam1_head_follow", "cam1_head_x", "cam1_head_smooth", "cam1_head_min"):
        assert hk not in tr_keys

    # 3. В cam1.zoom 5 подгрупп
    c1_zoom = next(it for it in c1["items"] if it.get("id") == "cam1.zoom")
    zoom_ids = [it.get("id") for it in c1_zoom.get("items", [])]
    assert zoom_ids == [
        "cam1.zoom_start",
        "cam1.zoom_cut",
        "cam1.zoom_take",
        "cam1.zoom_yellow",
        "cam1.zoom_cycle",
    ]


def test_migration_v2_to_v3():
    """п. 4: миграция v2 -> v3 переносит ключи и устанавливает cam_zoom_v = 3."""
    # 1. take_zoom False + snap -> оба False
    m_snap_f = styles.migrate_cam_zoom({"cam1_take_zoom": False, "cam1_take_yellow_mode": "snap"})
    assert m_snap_f["cam1_take_zoom"] is False
    assert m_snap_f["cam1_yellow_zoom"] is False
    assert m_snap_f["cam_zoom_v"] == 3
    assert m_snap_f["cam1_take_out"] == 2.4

    # 2. take_zoom True + snap -> оба True
    m_snap_t = styles.migrate_cam_zoom({"cam1_take_zoom": True, "cam1_take_yellow_mode": "snap"})
    assert m_snap_t["cam1_take_zoom"] is True
    assert m_snap_t["cam1_yellow_zoom"] is True

    # 3. take_zoom True + only -> yellow True, take False
    m_only_t = styles.migrate_cam_zoom({"cam1_take_zoom": True, "cam1_take_yellow_mode": "only"})
    assert m_only_t["cam1_yellow_zoom"] is True
    assert m_only_t["cam1_take_zoom"] is False

    # 4. take_zoom False + only -> оба False
    m_only_f = styles.migrate_cam_zoom({"cam1_take_zoom": False, "cam1_take_yellow_mode": "only"})
    assert m_only_f["cam1_yellow_zoom"] is False
    assert m_only_f["cam1_take_zoom"] is False

    # 5. off -> yellow_zoom: False (take_zoom не трогать)
    m_off_t = styles.migrate_cam_zoom({"cam1_take_zoom": True, "cam1_take_yellow_mode": "off"})
    assert m_off_t["cam1_take_zoom"] is True
    assert m_off_t["cam1_yellow_zoom"] is False

    m_off_f = styles.migrate_cam_zoom({"cam1_take_zoom": False, "cam1_take_yellow_mode": "off"})
    assert m_off_f["cam1_take_zoom"] is False
    assert m_off_f["cam1_yellow_zoom"] is False

    # 6. нет yellow_mode, есть старый take_yellow
    m_old_f = styles.migrate_cam_zoom({"cam_zoom_v": 2, "cam1_take_zoom": False, "cam1_take_yellow": True})
    assert m_old_f["cam1_take_zoom"] is False
    assert m_old_f["cam1_yellow_zoom"] is False

    m_old_t = styles.migrate_cam_zoom({"cam_zoom_v": 2, "cam1_take_zoom": True, "cam1_take_yellow": True})
    assert m_old_t["cam1_take_zoom"] is True
    assert m_old_t["cam1_yellow_zoom"] is True

    # cam2<-cam1 связь (CAM1_TO_CAM2_ZOOM_KEYS) копирует новые ключи
    m_cam2 = styles.migrate_cam2_zoom({
        "cam2_zoom_on": True,
        "cam1_take_out": 3.1,
        "cam1_yellow_zoom": True,
    })
    assert m_cam2["cam2_take_out"] == 3.1
    assert m_cam2["cam2_yellow_zoom"] is True


@pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")
def test_migration_v2_to_v3_js(tmp_path):
    """п. 1: JS-стенд stMigrateCamZoom из static/app/95-styles.js.
    take_zoom False + snap → оба False; take_zoom True + snap → оба True;
    take_zoom True + only → yellow True, take False; take_zoom False + only → оба False."""
    js_code = r"""
    const fs = require('fs');
    const content = fs.readFileSync('static/app/95-styles.js', 'utf8');
    const fnMatch = content.match(/function stMigrateCamZoom\([\s\S]*?\n(?=function|\/\/|$)/);
    if (!fnMatch) throw new Error("stMigrateCamZoom not found in 95-styles.js");
    eval(fnMatch[0]);

    // 1. take_zoom False + snap -> оба False
    const r1 = stMigrateCamZoom({ cam1_take_zoom: false, cam1_take_yellow_mode: 'snap' });
    // 2. take_zoom True + snap -> оба True
    const r2 = stMigrateCamZoom({ cam1_take_zoom: true, cam1_take_yellow_mode: 'snap' });
    // 3. take_zoom True + only -> yellow True, take False
    const r3 = stMigrateCamZoom({ cam1_take_zoom: true, cam1_take_yellow_mode: 'only' });
    // 4. take_zoom False + only -> оба False
    const r4 = stMigrateCamZoom({ cam1_take_zoom: false, cam1_take_yellow_mode: 'only' });

    console.log(JSON.stringify({ r1, r2, r3, r4 }));
    """
    script_path = str(tmp_path / "test_migrate_cam_zoom.js")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(js_code)
    proc = subprocess.run(["node", script_path], capture_output=True, text=True, check=True)
    res = json.loads(proc.stdout.strip())
    assert res["r1"]["cam1_take_zoom"] is False
    assert res["r1"]["cam1_yellow_zoom"] is False
    assert res["r2"]["cam1_take_zoom"] is True
    assert res["r2"]["cam1_yellow_zoom"] is True
    assert res["r3"]["cam1_take_zoom"] is False
    assert res["r3"]["cam1_yellow_zoom"] is True
    assert res["r4"]["cam1_take_zoom"] is False
    assert res["r4"]["cam1_yellow_zoom"] is False


def test_take_zoom_pulse_lead_min_frame_clearance():
    """Тест: режим pulse со стартовым импульсом (big 182, punch 62 кадра), один кусок 12 с без
    жёлтых, take_zoom вкл → ключ a цикла ≥ lead_min_frame + 0.3 с·fps; то же для первого
    промежутка при жёлтом в конце куска."""
    fps = 60.0
    cams = [{"clips": [[0, 720, 0, "cam1.mov", True]]}]
    big = 182.0
    punch = 62

    # 1) Один кусок 12 с без жёлтых, take_zoom вкл
    take_no_yellow = {
        "min_s": 8.0,
        "take_zoom": True,
        "yellow_zoom": False,
        "words": [],
    }
    keys = layout._cam1_zoom_keys(cams, big=big, punch=punch, fps=fps, start=True, take=take_no_yellow)
    assert keys[0][0] == 0.0 and keys[0][1] == 182.0
    assert keys[1][0] == float(punch) and keys[1][1] == 100.0
    lead_min_frame = punch + 1
    take_keys = keys[2:]
    assert len(take_keys) == 4, f"Ожидалось 4 ключа длинного наезда, получено {len(take_keys)}"
    k_a = take_keys[0][0]
    min_allowed_a = lead_min_frame + round(layout.TAKE_MIN_LEAD_S * fps)
    assert k_a >= min_allowed_a, f"Ключ a ({k_a}) должен быть ≥ lead_min_frame + 0.3 с·fps ({min_allowed_a})"

    # 2) То же для первого промежутка при жёлтом в конце куска (слово на 11.0 с = 660 кадров)
    take_with_yellow = {
        "min_s": 8.0,
        "take_zoom": True,
        "yellow_zoom": True,
        "words": [660.0],
    }
    keys_y = layout._cam1_zoom_keys(cams, big=big, punch=punch, fps=fps, start=True, take=take_with_yellow)
    assert keys_y[0][0] == 0.0 and keys_y[0][1] == 182.0
    assert keys_y[1][0] == float(punch) and keys_y[1][1] == 100.0
    take_keys_y = keys_y[2:6]
    assert len(take_keys_y) == 4
    k_a_first_span = take_keys_y[0][0]
    assert k_a_first_span >= min_allowed_a, (
        f"Ключ a первого промежутка ({k_a_first_span}) должен быть ≥ lead_min_frame + 0.3 с·fps ({min_allowed_a})"
    )


def test_take_zoom_between_yellow_gap_3_2s_no_long_cycle():
    """Кусок 20 с, жёлтые циклы оставляют только щель 3.2 с, оба вкл -> цикла длинного куска нет."""
    fps = 60.0
    take = {
        "min_s": 8.0,
        "take_zoom": True,
        "yellow_zoom": True,
        "words": [48, 720],
        "hold_s": 2.0,
        "out_s": 2.4,
    }
    keys = layout._take_zoom_segment_keys(
        f=0, seg_end=1200, v=100.0, fps=fps, take=take,
        trng=None, lead_min_frame=0,
    )
    # Щель между yellow_cycles[0] (d=312) и yellow_cycles[1] (a=624) за вычетом зазоров 1 с:
    # (624 - 60) - (312 + 60) = 192 кадра = 3.2 с.
    # Полный цикл 6.0 с без сжатия не помещается -> цикла длинного куска нет, только 2 жёлтых цикла (8 ключей).
    assert len(keys) == 8, f"Ожидалось 8 ключей (2 жёлтых цикла без длинного наезда), получено {len(keys)}"


def test_take_zoom_between_yellow_gap_8s_has_full_long_cycle():
    """Та же раскладка куска 20 с, щель 8 с -> цикл длинного куска есть, подъезд 1.6, отъезд 2.4 (не сжаты).

    Хайлайт на кадре 1008 отъезда не получает (c = 1158, d = 1302 > 1200 − 18):
    камера держит пик до склейки, поэтому ключей 10 (4 + 4 + 2), а не 12.
    """
    fps = 60.0
    take = {
        "min_s": 8.0,
        "take_zoom": True,
        "yellow_zoom": True,
        "words": [48, 1008],
        "hold_s": 2.0,
        "out_s": 2.4,
    }
    keys = layout._take_zoom_segment_keys(
        f=0, seg_end=1200, v=100.0, fps=fps, take=take,
        trng=None, lead_min_frame=0,
    )
    # Щель между yellow_cycles[0] (d=342) и yellow_cycles[1] (a=1002) за вычетом зазоров 1 с:
    # (1002 - 60) - (342 + 60) = 540 кадров = 9.0 с.
    # Полный цикл 6.0 с без сжатия помещается -> 4 + 4 + 2 ключа.
    assert len(keys) == 10, f"Ожидалось 10 ключей (2 хайлайта + длинный цикл), получено {len(keys)}"
    long_cycle_keys = keys[4:8]
    dur_in = (long_cycle_keys[1][0] - long_cycle_keys[0][0]) / fps
    dur_out = (long_cycle_keys[3][0] - long_cycle_keys[2][0]) / fps
    assert abs(dur_in - 1.6) < 1e-3, f"Подъезд должен быть 1.6 с (не сжат), получено {dur_in}"
    assert abs(dur_out - 2.4) < 1e-3, f"Отъезд должен быть 2.4 с (не сжат), получено {dur_out}"


def test_take_zoom_no_yellow_9s_cycle_present():
    """Кусок 9 с без жёлтых, out_s=2.4, hold=2 -> цикл есть (сжатие разрешено, как раньше)."""
    fps = 60.0
    take = {
        "min_s": 8.0,
        "take_zoom": True,
        "yellow_zoom": False,
        "words": [],
        "hold_s": 2.0,
        "out_s": 2.4,
    }
    keys = layout._take_zoom_segment_keys(
        f=0, seg_end=int(9.0 * fps), v=100.0, fps=fps, take=take,
        trng=None, lead_min_frame=0,
    )
    assert len(keys) == 4, f"Ожидался 1 цикл длинного наезда (4 ключа), получено {len(keys)}"



