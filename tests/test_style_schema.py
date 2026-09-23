# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты схемы панели стилей (Effect Controls) и эндпоинта /api/style_schema.

Чисел здесь нет намеренно: «ровно 159 ключей», «39 групп», «147 полей» —
это change-detector. Новая ручка требовала править один и тот же счётчик в нескольких
файлах, а настоящие дефекты проходили мимо: ключ в styles.BASE без поля схемы, поле без
перевода, дубль ключа. Вместо счётчиков проверяются инварианты связки
«BASE <-> схема <-> переводы <-> эндпоинт»: они краснеют ровно на дефекте и молчат,
когда ручку добавили правильно.

Запуск: python -m pytest tests/test_style_schema.py -q
"""
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

# Одна величина — две ручки: тумблер слоя «Дисклеймер» (значение "disc") и textarea его
# текста делят ключ disclaimer. Всякое другое повторение ключа в схеме — дубль-опечатка:
# панель ищет значение по ключу, и две ручки молча пишут в одно поле стиля.
SHARED_KEYS = {
    "disclaimer": "тумблер слоя disc и textarea текста дисклеймера",
}

# Настоящие пары key/key2: одна ручка-точка (ctl="point") с двумя координатами.
# Это исключение из правила «один ключ — одно поле», и оно описано явно: появилась
# новая пара — её сперва вписывают сюда.
POINT_PAIRS = {("cam1_zoom_cx", "cam1_zoom_cy")}


@pytest.fixture
def client():
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def _iter_nodes(layers=None):
    """Узлы дерева по порядку: [('layer'|'group'|'field', узел), ...].

    Обходит и схему (core.style_schema.LAYERS), и её же, приехавшую из эндпоинта:
    узлы там одной формы, поэтому проверки не приходится писать дважды.
    """
    out = []

    def _walk(items):
        for it in items:
            kind = it.get("type")
            if kind == "group":
                out.append(("group", it))
                _walk(it.get("items", []))
            elif kind == "field":
                out.append(("field", it))

    for layer in style_schema.LAYERS if layers is None else layers:
        out.append(("layer", layer))
        _walk(layer.get("items", []))
    return out


def _key_uses(nodes=None):
    """Каждое место, где схема называет ключ: [(ключ, 'key'|'key2'|'toggle'), ...]."""
    uses = []
    for _, node in _iter_nodes() if nodes is None else nodes:
        for slot in ("key", "key2"):
            if node.get(slot):
                uses.append((node[slot], slot))
        if node.get("toggle"):
            uses.append((node["toggle"], "toggle"))
    return uses


def _schema_keys():
    """Ключи, которые панель показывает: поля (key и key2) и тумблеры слоёв/групп."""
    return {key for key, _ in _key_uses()}


def _sets_of(layers):
    """Множества id слоёв, id групп, ключей полей и тумблеров — из любого дерева."""
    nodes = _iter_nodes(layers)
    uses = _key_uses(nodes)
    return (
        {node["id"] for kind, node in nodes if kind == "layer"},
        {node["id"] for kind, node in nodes if kind == "group"},
        {key for key, slot in uses if slot != "toggle"},
        {key for key, slot in uses if slot == "toggle"},
    )


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


def test_base_and_schema_cover_each_other():
    """Каждый ключ BASE — поле или тумблер схемы, либо внешний ключ; и наоборот.

    Ловит две тихие дыры: ключ живёт в styles.BASE, а ручки в схеме нет (из интерфейса
    его не поменять — так девять ключей и прожили без ручки раньше); ключ есть в
    схеме, а в BASE его нет (дефолт уезжает в build.py и расходится со стилем).
    """
    base = set(styles.BASE)
    external = set(style_schema.EXTERNAL)
    schema = _schema_keys()

    overlap = schema & external
    assert not overlap, f"ключ сразу и в схеме, и во внешних: {sorted(overlap)}"

    no_handle = sorted(base - schema - external)
    assert not no_handle, (
        "ключи styles.BASE без ручки в схеме (core/style_schema.py) и без EXTERNAL: "
        + ", ".join(no_handle)
    )

    no_default = sorted((schema | external) - base)
    assert not no_default, (
        "ключи схемы/EXTERNAL, которых нет в styles.BASE: " + ", ".join(no_default)
    )


def test_schema_keys_are_unique():
    """Один ключ — одна ручка: дубли только из SHARED_KEYS, пары — только из POINT_PAIRS.

    Дубль тихий: панель строит ручки по ключу и пишет значение в одно поле стиля, поэтому
    две ручки с одним ключом затирают друг друга — видно это только глазами в AE.
    """
    uses = _key_uses()
    counts = collections.Counter(key for key, _ in uses)
    duplicated = {key: c for key, c in counts.items() if c > 1}

    unexpected = sorted(set(duplicated) - set(SHARED_KEYS))
    assert not unexpected, (
        "ключ заведён у нескольких ручек схемы: "
        + ", ".join(f"{key} ({duplicated[key]} раза)" for key in unexpected)
    )

    stale = sorted(set(SHARED_KEYS) - set(duplicated))
    assert not stale, f"SHARED_KEYS описывает пары, которых в схеме больше нет: {stale}"

    # Общая пара — ровно «поле + тумблер»: два поля с одним ключом затрут друг друга.
    for key in SHARED_KEYS:
        kinds = sorted(slot for k, slot in uses if k == key)
        assert kinds == ["key", "toggle"], (
            f"{key}: ручки {kinds}, а пара — это поле и тумблер одной величины"
        )

    # key2 — вторая координата той же ручки: ключи внутри пары разные, пары описаны явно.
    pairs = set()
    for _, node in _iter_nodes():
        if not node.get("key2"):
            continue
        assert node.get("key"), f"key2={node['key2']!r} без key"
        assert node["key"] != node["key2"], f"поле {node['key']} ссылается само на себя"
        pairs.add((node["key"], node["key2"]))
    assert pairs == POINT_PAIRS, (
        "пары key/key2 разъехались с описанными в POINT_PAIRS: "
        f"в схеме {sorted(pairs)}, описаны {sorted(POINT_PAIRS)}"
    )


def test_schema_strings_are_labelled_and_translated():
    """У каждой ручки есть подпись, а подпись и подсказка переведены в static/i18n/en.json.

    У поля без перевода русская строка молча остаётся русской на английском интерфейсе —
    это видно только глазами и только после переключения языка. Пустой tip — это «подсказки
    нет», так можно; tip из пробелов — забытая строка.
    """
    i18n_path = pathlib.Path(__file__).resolve().parent.parent / "static" / "i18n" / "en.json"
    en = json.loads(i18n_path.read_text(encoding="utf-8"))

    missing = []
    unlabelled = []

    def check(s, ctx):
        if s and isinstance(s, str) and s not in en:
            missing.append(f"{ctx}: {s!r}")

    for kind, node in _iter_nodes():
        ctx = f"{kind} {node.get('id') or node.get('key')}"
        if not str(node.get("label") or "").strip():
            unlabelled.append(f"{ctx} label")
        if node.get("tip") is not None and not str(node["tip"]).strip():
            unlabelled.append(f"{ctx} tip")
        check(node.get("label"), f"{ctx} label")
        check(node.get("tip"), f"{ctx} tip")
        for opt in node.get("options", []):
            if isinstance(opt, dict):
                check(opt.get("label"), f"{ctx} option label")
            elif isinstance(opt, (list, tuple)) and len(opt) > 1:
                check(opt[1], f"{ctx} option label")

    assert not unlabelled, f"Ручки без подписи: {unlabelled}"
    assert not missing, f"Отсутствуют переводы в en.json ({len(missing)} шт):\n" + "\n".join(missing)


def test_select_defaults_in_options():
    """Для каждого ctl=='select' дефолт из BASE (если не None) присутствует в options."""
    select_fields = [node for kind, node in _iter_nodes()
                     if kind == "field" and node.get("ctl") == "select"]
    assert select_fields, "в схеме не осталось ни одного select — проверка ослепла"

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
    num_fields = [node for kind, node in _iter_nodes()
                  if kind == "field" and node.get("ctl") in ("num", "int", "angle")]
    assert num_fields, "в схеме не осталось числовых полей — проверка ослепла"

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
    layer_ids = [layer["id"] for layer in style_schema.LAYERS]
    assert len(layer_ids) == len(set(layer_ids)), f"Дубликаты id слоёв: {layer_ids}"

    group_ids = [node["id"] for kind, node in _iter_nodes() if kind == "group"]
    dup_groups = [k for k, c in collections.Counter(group_ids).items() if c > 1]
    assert not dup_groups, f"Дубликаты id групп: {dup_groups}"

    for kind, node in _iter_nodes():
        if kind != "field":
            continue
        c = node.get("conv")
        if c:
            assert c in VALID_CONVS, f"Недопустимый conv {c!r} у поля {node.get('key')}"
        if "show_if" in node:
            skey = node["show_if"].get("key")
            assert skey in styles.BASE, f"show_if.key={skey!r} поля {node.get('key')} отсутствует в BASE"


def test_api_style_schema_matches_schema(client):
    """GET /api/style_schema отдаёт ту же схему, что core.style_schema: сверка множеств.

    Считаются не слои, группы и поля, а их id и ключи: схема и эндпоинт разъезжаются и
    при том же числе (одно поле переименовали, другое потеряли) — число этого не видит.
    """
    res = client.get("/api/style_schema", headers=H)
    assert res.status_code == 200
    data = res.get_json()

    assert data.get("ok") is True
    assert data["base"] == styles.BASE, "эндпоинт отдал не дефолты styles.BASE"
    assert set(data["external"]) == set(style_schema.EXTERNAL), "внешние ключи разъехались"

    layers = data.get("layers")
    assert isinstance(layers, list)

    api_sets = _sets_of(layers)
    schema_sets = _sets_of(style_schema.LAYERS)
    for name, from_api, from_schema in zip(("слои", "группы", "ключи полей", "тумблеры"),
                                           api_sets, schema_sets):
        assert from_api == from_schema, (
            f"{name} разъехались: только в ответе {sorted(from_api - from_schema)}, "
            f"только в схеме {sorted(from_schema - from_api)}"
        )
