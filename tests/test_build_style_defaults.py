# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Запасные значения ключей стиля в сборке берутся из styles.BASE, а не из литерала (задание JC).

Дефолт ключа стиля жил в ДВУХ местах: в core/styles.py:BASE и числом-запасом рядом с
чтением в core/xml2ae/build.py — `st.get("sub_bg_op") if st.get("sub_bg_op") is not None
else 72.0`. Поменяешь дефолт в BASE — сборка при отсутствии ключа возьмёт СТАРОЕ число,
и .jsx соберётся не по стилю; увидеть это можно только в AE, а не в тестах.

Сторож разбирает build.py через ast и падает, если для ключа из styles.BASE осталась
форма со своим литералом:

    st.get("k") if st.get("k") is not None else <литерал>
    st.get("k") or <литерал>
    st.get("k", <литерал>)

Запас теперь берут обёртки `_sv(st, key)` (None = «не задано») и `_sv_or(st, key)`
(ноль и пустая строка тоже «не задано») — обе читают styles.BASE.
Ключи из списка ZAPAS_NE_BASE сторож пропускает: там запас НЕ равен BASE.

Запуск: python -m pytest tests -q
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import styles  # noqa: E402

BUILD_PY = os.path.join(ROOT, "core", "xml2ae", "build.py")

# запас ≠ BASE, решение владельца ждёт (задание JC, п. 2)
# intro_riser_file: в BASE None («файл не задан»), а в сборке `or ""` — это не дефолт
# файла, а подготовка строки к .strip(): None.strip() упал бы.
ZAPAS_NE_BASE = {"intro_riser_file"}


def _build_tree():
    with open(BUILD_PY, encoding="utf-8") as f:
        return ast.parse(f.read(), filename=BUILD_PY)


def _st_get_key(node):
    """`st.get("k")` -> "k"; переменный ключ и всё остальное -> None."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get" and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "st" and node.args):
        return None
    a = node.args[0]
    if isinstance(a, ast.Constant) and isinstance(a.value, str):
        return a.value
    return None


def _literal(node):
    """Сам ли узел литералом (число/строка/True/None/[1, 1, 1]) — то есть «запас в коде»."""
    try:
        ast.literal_eval(node)
        return True
    except Exception:
        return False


def _is_not_none_of(node, key):
    """`st.get(key) is not None` — тест формы «… if st.get(k) is not None else <запас>»."""
    if not isinstance(node, ast.Compare) or len(node.ops) != 1:
        return False
    if not isinstance(node.ops[0], ast.IsNot):
        return False
    if not all(isinstance(c, ast.Constant) and c.value is None for c in node.comparators):
        return False
    return any(_st_get_key(x) == key for x in ast.walk(node.left))


def _violations():
    """[(строка, ключ, форма)] — чтения ключей styles.BASE, оставшиеся со своим литералом."""
    tree = _build_tree()
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    out = []
    for node in ast.walk(tree):
        key = _st_get_key(node)
        if key is None or key not in styles.BASE or key in ZAPAS_NE_BASE:
            continue
        if len(node.args) >= 2 and _literal(node.args[1]):
            out.append((node.lineno, key, "st.get(k, <литерал>)"))
            continue
        par = parents.get(node)
        if (isinstance(par, ast.IfExp) and par.body is node and _literal(par.orelse)
                and _is_not_none_of(par.test, key)):
            out.append((node.lineno, key, "st.get(k) … else <литерал>"))
        elif (isinstance(par, ast.BoolOp) and isinstance(par.op, ast.Or)
                and par.values[0] is node and _literal(par.values[1])):
            out.append((node.lineno, key, "st.get(k) or <литерал>"))
    return sorted(out)


def test_build_reads_style_defaults_from_base():
    """В build.py нет чтений ключей styles.BASE со своим запасным литералом."""
    bad = _violations()
    assert not bad, (
        "в core/xml2ae/build.py запас ключа стиля снова стоит литералом, а не в styles.BASE "
        "(нужны обёртки _sv/_sv_or, задание JC):\n"
        + "\n".join("  строка %d: %s — %s" % (ln, key, form) for ln, key, form in bad)
    )


def test_sv_wrappers_take_default_from_base():
    """Обёртки _sv/_sv_or берут запас из styles.BASE и держат прежнюю семантику."""
    from core.xml2ae.build import _sv, _sv_or
    base_op = styles.BASE["sub_bg_op"]
    assert _sv({}, "sub_bg_op") == base_op                  # ключа нет — дефолт BASE
    assert _sv({"sub_bg_op": 0.0}, "sub_bg_op") == 0.0      # ноль для _sv — заданное значение
    assert _sv({"sub_bg_op": 12.5}, "sub_bg_op") == 12.5    # заданное значение не трогаем
    assert _sv_or({"sub_bg_op": 0.0}, "sub_bg_op") == base_op   # ноль для _sv_or — «не задано»
    assert _sv_or({"sub_bg_op": 12.5}, "sub_bg_op") == 12.5
