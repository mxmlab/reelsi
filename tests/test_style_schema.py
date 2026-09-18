# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты схемы панели стилей (Effect Controls) и эндпоинта /api/style_schema."""
import collections
import json
import os
import pathlib
import pytest

os.environ.setdefault("REELSI_NO_BROWSER", "1")

import api  # noqa: E402
from core import style_schema, styles  # noqa: E402

H = {"Host": "127.0.0.1:5001"}

VALID_CONVS = {"inv_pct", "pct_h", "pct_fx", "pct_fy", "frac_pct", "frac_pct_int"}


@pytest.fixture
def client():
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def _walk_schema(callback_group=None, callback_field=None):
    """Обход всех групп и полей схемы."""

    def _walk(items):
        for it in items:
            if it.get("type") == "group":
                if callback_group:
                    callback_group(it)
                _walk(it.get("items", []))
            elif it.get("type") == "field":
                if callback_field:
                    callback_field(it)

    for layer in style_schema.LAYERS:
        _walk(layer.get("items", []))


def _apply_conv(val, conv, w=1080, h=1920):
    """Применение conv из Таблицы 2 для проверки диапазонов UI."""
    if conv is None:
        return val
    if conv == "inv_pct":
        return round((1 - val) * 100)
    if conv == "pct_h":
        return round((val / h) * 100)
    if conv == "pct_fx":
        return round((val / w) * 100, 1)
    if conv == "pct_fy":
        return round((val / h) * 100, 1)
    if conv == "frac_pct":
        return round(val * 1000) / 10
    if conv == "frac_pct_int":
        return round(val * 100)
    raise ValueError(f"Неизвестный conv: {conv}")


def test_every_base_key_accounted_for():
    """styles.BASE имеет ровно 150 ключей; каждый в схеме или в EXTERNAL (без списков исключений)."""
    base_keys = set(styles.BASE.keys())
    assert len(base_keys) == 150, f"Ожидалось ровно 150 ключей в BASE, найдено {len(base_keys)}"

    # Внешние ключи
    ext_keys = set(style_schema.EXTERNAL.keys())
    assert ext_keys == {"label", "intro_mode"}

    field_keys = []
    toggle_keys = []

    for layer in style_schema.LAYERS:
        if layer.get("toggle"):
            toggle_keys.append(layer["toggle"])

    def on_group(g):
        if g.get("toggle"):
            toggle_keys.append(g["toggle"])

    def on_field(f):
        if "key" in f:
            field_keys.append(f["key"])
        if "key2" in f:
            field_keys.append(f["key2"])

    _walk_schema(callback_group=on_group, callback_field=on_field)

    # Все ключи полей уникальны между собой (138 полей, 139 ключей: cam1_zoom_cx/cy)
    field_counts = collections.Counter(field_keys)
    dup_fields = [k for k, c in field_counts.items() if c > 1]
    assert not dup_fields, f"Дубликаты ключей полей: {dup_fields}"
    assert len(field_keys) == 139

    # Все тумблеры уникальны между собой (10 тумблеров: 9 булевых + disclaimer)
    toggle_counts = collections.Counter(toggle_keys)
    dup_toggles = [k for k, c in toggle_counts.items() if c > 1]
    assert not dup_toggles, f"Дубликаты тумблеров: {dup_toggles}"
    assert len(toggle_keys) == 10

    # 9 булевых тумблеров не имеют полей в items
    bool_toggles = [t for t in toggle_keys if t != "disclaimer"]
    assert len(bool_toggles) == 9
    assert not (set(bool_toggles) & set(field_keys))

    # disclaimer — 3-позиционный тумблер слоя ('disc'), делящий ключ с textarea
    assert set(toggle_keys) & set(field_keys) == {"disclaimer"}

    # Схема + EXTERNAL строго покрывают BASE
    schema_keys = set(field_keys) | set(toggle_keys)
    assert len(schema_keys) == 148

    assert not (schema_keys & ext_keys), f"Пересечение схемы и EXTERNAL: {schema_keys & ext_keys}"
    assert schema_keys | ext_keys == base_keys
    assert not (base_keys - (schema_keys | ext_keys)), f"Пропущены ключи BASE: {base_keys - (schema_keys | ext_keys)}"
    assert not ((schema_keys | ext_keys) - base_keys), f"Лишние ключи в схеме: {(schema_keys | ext_keys) - base_keys}"


def test_select_defaults_in_options():
    """Для каждого ctl=='select' дефолт из BASE (если не None) присутствует в options."""
    select_fields = []

    def on_field(f):
        if f.get("ctl") == "select":
            select_fields.append(f)

    _walk_schema(callback_field=on_field)
    assert len(select_fields) > 0

    for f in select_fields:
        key = f["key"]
        base_val = styles.BASE[key]
        options = f.get("options", [])
        opt_vals = []
        for opt in options:
            if isinstance(opt, dict):
                opt_vals.append(opt.get("val"))
            elif isinstance(opt, (list, tuple)):
                opt_vals.append(opt[0])

        if base_val is None:
            assert f.get("nullable") is True, f"Поле {key} имеет дефолт None, но не nullable"
        else:
            assert base_val in opt_vals, f"Дефолт BASE[{key}]={base_val!r} отсутствует в options={opt_vals}"


def test_num_int_defaults_within_bounds():
    """Для num/int/angle дефолт из BASE лежит в [min, max] после conv; lim_min <= min и max <= lim_max."""
    num_fields = []

    def on_field(f):
        if f.get("ctl") in ("num", "int", "angle"):
            num_fields.append(f)

    _walk_schema(callback_field=on_field)
    assert len(num_fields) > 0

    for f in num_fields:
        key = f["key"]
        base_val = styles.BASE[key]
        if base_val is None:
            assert f.get("nullable") is True, f"Поле {key} имеет None в BASE, но не nullable"
            continue

        ui_val = _apply_conv(base_val, f.get("conv"))
        mi = f.get("min")
        ma = f.get("max")
        eps = 1e-6

        if mi is not None:
            assert ui_val >= mi - eps, f"Поле {key}: ui_val={ui_val} < min={mi}"
        if ma is not None:
            assert ui_val <= ma + eps, f"Поле {key}: ui_val={ui_val} > max={ma}"

        lim_mi = f.get("lim_min")
        lim_ma = f.get("lim_max")
        if lim_mi is not None:
            assert mi is not None and lim_mi <= mi + eps, f"Поле {key}: lim_min={lim_mi} > min={mi}"
        if lim_ma is not None:
            assert ma is not None and ma <= lim_ma + eps, f"Поле {key}: max={ma} > lim_max={lim_ma}"


def test_unique_ids_and_valid_convs():
    """Все id слоёв и групп уникальны; conv — только из таблицы п. 2; show_if.key — из BASE."""
    layer_ids = [l["id"] for l in style_schema.LAYERS]
    assert len(layer_ids) == len(set(layer_ids)), f"Дубликаты id слоёв: {layer_ids}"
    assert len(layer_ids) == 10

    group_ids = []

    def on_group(g):
        group_ids.append(g["id"])

    def on_field(f):
        c = f.get("conv")
        if c:
            assert c in VALID_CONVS, f"Недопустимый conv {c!r} у поля {f.get('key')}"
        if "show_if" in f:
            skey = f["show_if"].get("key")
            assert skey in styles.BASE, f"show_if.key={skey!r} поля {f.get('key')} отсутствует в BASE"

    _walk_schema(callback_group=on_group, callback_field=on_field)

    dup_groups = [k for k, c in collections.Counter(group_ids).items() if c > 1]
    assert not dup_groups, f"Дубликаты id групп: {dup_groups}"
    assert len(group_ids) == 38


def test_i18n_coverage_for_schema_strings():
    """Все русские строки (label, tip слоёв/групп/полей, label опций select) есть в en.json."""
    i18n_path = pathlib.Path(__file__).resolve().parent.parent / "static" / "i18n" / "en.json"
    en = json.loads(i18n_path.read_text(encoding="utf-8"))

    missing = []

    def check(s, ctx):
        if s and isinstance(s, str) and s not in en:
            missing.append(f"{ctx}: {s!r}")

    for layer in style_schema.LAYERS:
        check(layer.get("label"), f"layer {layer.get('id')} label")

    def on_group(g):
        check(g.get("label"), f"group {g.get('id')} label")
        check(g.get("tip"), f"group {g.get('id')} tip")

    def on_field(f):
        check(f.get("label"), f"field {f.get('key')} label")
        check(f.get("tip"), f"field {f.get('key')} tip")
        for opt in f.get("options", []):
            if isinstance(opt, dict):
                check(opt.get("label"), f"field {f.get('key')} option label")
            elif isinstance(opt, (list, tuple)) and len(opt) > 1:
                check(opt[1], f"field {f.get('key')} option label")

    _walk_schema(callback_group=on_group, callback_field=on_field)

    assert not missing, f"Отсутствуют переводы в en.json ({len(missing)} шт):\n" + "\n".join(missing)


def test_api_style_schema_endpoint(client):
    """GET /api/style_schema отдаёт ok: True, layers (10), external (2), base, 38 групп, 138 полей."""
    res = client.get("/api/style_schema", headers=H)
    assert res.status_code == 200
    data = res.get_json()

    assert data.get("ok") is True
    assert "base" in data
    assert len(data["base"]) == 150

    external = data.get("external")
    assert len(external) == 2

    layers = data.get("layers")
    assert isinstance(layers, list)
    assert len(layers) == 10

    groups_count = 0
    fields_count = 0

    def count_items(items):
        nonlocal groups_count, fields_count
        for it in items:
            if it.get("type") == "group":
                groups_count += 1
                count_items(it.get("items", []))
            elif it.get("type") == "field":
                fields_count += 1

    for layer in layers:
        count_items(layer.get("items", []))

    assert groups_count == 38
    assert fields_count == 138
