# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Словарь перевода интерфейса.

Ключ словаря — сам русский текст (как в gettext), поэтому «сломаться» перевод может
ровно двумя способами, и оба тихие: строка в разметке поменялась и потеряла свой
ключ, либо в переводе разъехались плейсхолдеры `{n}`. Ни то, ни другое не видно,
пока не переключишь язык и не посмотришь глазами именно на это место.

Запуск:  python -m pytest reelsi/tests -q
"""
import io
import json
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

EN = os.path.join(ROOT, "static", "i18n", "en.json")
# см. комментарий в test_ui_static: интерфейс распилен на static/app/*.js
from core import app_meta  # noqa: E402
HTML = os.path.join(ROOT, "templates", "index.html")
PLACEHOLDER = re.compile(r"\{(\w+)\}")


@pytest.fixture(scope="module")
def en():
    return json.load(io.open(EN, encoding="utf-8"))


def test_dictionary_is_valid_and_complete(en):
    assert en, "словарь пуст"
    empty = [k for k, v in en.items() if not str(v).strip()]
    assert not empty, f"без перевода: {empty[:5]}"


def test_dictionary_keys_are_sorted(en):
    """Ключи словаря обязаны быть отсортированы по алфавиту (как пишут i18n_extract/merge)."""
    keys = list(en.keys())
    assert keys == sorted(keys), "ключи в en.json не отсортированы"


def test_no_russian_left_in_values(en):
    """Значение с кириллицей — забытая строка: на английском она так и покажется."""
    cyr = re.compile(r"[А-Яа-яЁё]")
    bad = [k for k, v in en.items() if cyr.search(str(v))]
    # «ё» в объяснении про GigaAM — часть примера, а не забытый перевод
    bad = [k for k in bad if "«ё»" not in en[k]]
    assert not bad, f"перевод остался русским: {bad[:5]}"


def test_placeholders_match(en):
    """`{n}` обязан быть и в ключе, и в переводе. Пропал — пользователь увидит
    «Camera folder» без номера; появился лишний — «{n}» прямо на экране.
    ERR_* — исключение: это бэкенд-ошибки с кодом (umsg.py), переменные туда
    приходят из err_vars ответа, а не из ключа словаря."""
    for k, v in en.items():
        if k.startswith("ERR_"):
            assert str(v).strip(), f"перевод ERR_{k[4:]} пуст"
            continue
        assert set(PLACEHOLDER.findall(k)) == set(PLACEHOLDER.findall(str(v))), \
            f"плейсхолдеры разъехались: {k!r} -> {v!r}"


def test_every_markup_string_is_covered(en):
    """Строка появилась в разметке, а в словаре её нет — она молча останется
    русской на английском интерфейсе. Экстрактор смотрит на то же, что и глаз."""
    from i18n_extract import strings
    missing = [s for s in strings() if s not in en]
    assert not missing, ("нет перевода для строк разметки "
                         f"({len(missing)}): {missing[:5]}")


def _js_unescape(s):
    """Ключи t('…') в исходнике — с экранированием (\n, \'), а t() ищет по
    runtime-строке (реальные переводы строк). Без разэкранирования ключ с \n
    «не находится» в словаре — и перевод такого места молча не работает."""
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t", "r": "\r",
                                       "\\": "\\", "'": "'", '"': '"',
                                       "0": "\0"}.get(m.group(1), m.group(1)), s)


def test_js_keys_exist_in_dictionary(en):
    """Строки, обёрнутые в t('…') в app.js, тоже обязаны быть в словаре."""
    src = app_meta.app_js_text()
    keys = [_js_unescape(k) for k in re.findall(r"\bt\('((?:[^'\\]|\\.)+)'", src)]
    russian = [k for k in keys if re.search(r"[А-Яа-яЁё]", k)]
    assert russian, "в app.js не осталось ни одного t('…') с русским текстом"
    missing = [k for k in russian if k not in en]
    assert not missing, f"t('…') без перевода: {missing[:5]}"


def test_dictionary_is_embedded_not_fetched():
    """Словарь встраивается в страницу. Через fetch была гонка: строки камер и
    статуса успевали отрисоваться раньше загрузки и оставались русскими, а обход
    DOM их уже не догоняет — «Камера 1» собрана из шаблона «Камера {n}»."""
    html = io.open(HTML, encoding="utf-8").read()
    js = app_meta.app_js_text()
    assert "__I18N_EN__" in html, "плейсхолдер словаря пропал из шаблона"
    assert "REELSI_I18N" in js, "app.js больше не читает встроенный словарь"
    assert "fetch('/static/i18n/" not in js, "словарь снова грузится запросом — вернётся гонка"


def test_placeholder_is_not_a_substring_of_the_variable_name():
    """`__I18N_EN__` подставляется заменой подстроки. Если так назвать и переменную,
    замена съест её тоже и объявление превратится в битую деструктуризацию —
    ровно это и случилось при первой попытке."""
    html = io.open(HTML, encoding="utf-8").read()
    line = next(x for x in html.splitlines() if "=__I18N_EN__" in x)
    name = line.split("=__I18N_EN__")[0].split()[-1]
    assert "__I18N_EN__" not in name, f"имя переменной {name!r} содержит плейсхолдер"


def test_russian_needs_no_dictionary():
    """Главное свойство схемы: на русском словарь не нужен вообще. Значит правка
    перевода физически не может сломать рабочий интерфейс."""
    js = app_meta.app_js_text()
    body = js[js.index("function t(s, vars)"):js.index("function applyI18n")]
    assert "I18N[s]) || s" in body, "t() больше не возвращает ключ как запасной вариант"
    init = js[js.index("function initLang"):js.index("// ================= helpers")]
    assert "if(target !== 'en')" in init, (
        "на русском выполняется лишняя работа — раньше выход был сразу")


def _backend_err_codes():
    import glob
    codes = set()
    for f in glob.glob(os.path.join(ROOT, "api", "*.py")) + \
            glob.glob(os.path.join(ROOT, "core", "aicut", "*.py")) + \
            glob.glob(os.path.join(ROOT, "core", "xml2ae", "*.py")):
        src = io.open(f, encoding="utf-8").read()
        for m in re.finditer(r'umsg\("([a-z0-9_]+)"', src):
            codes.add(m.group(1))
    return codes


def test_every_backend_error_code_has_translation(en):
    """Каждый umsg-код бэкенда обязан иметь ERR_<код> в словаре: иначе в
    английском интерфейсе пользователь увидит русский текст с кодом."""
    codes = _backend_err_codes()
    assert codes, "бэкенд не использует ни одного umsg-кода"
    missing = [c for c in sorted(codes) if "ERR_" + c not in en]
    assert not missing, f"нет перевода ERR_{missing[0]}"


def test_backend_error_code_is_never_orphan(en):
    """Код, для которого перестали писать ошибки, можно убрать из словаря —
    лишний ключ не сломал бы, но молча стареет."""
    codes = _backend_err_codes()
    orphan = [k for k in en if k.startswith("ERR_") and k[4:] not in codes]
    assert not orphan, f"в словаре висят без дела: {orphan[:5]}"


def test_no_cyrillic_jsonify_errors_in_backend():
    """Слепое пятно схемы umsg: `test_every_backend_error_code_has_translation`
    проверяет, что у каждого вызова umsg() есть ключ, но место, где umsg() НЕ
    позвали вовсе (jsonify(error='русский') на месте), не видит никто — русский
    текст оставался на английском интерфейсе. Скан api/**.py: любой кириллический
    литерал в jsonify(error=) обязан быть переведён кодом umsg."""
    import glob
    cyr = re.compile(r"[А-Яа-яЁё]")
    pat = re.compile(r"jsonify\(error=([^,)]*)")
    bad = []
    for f in sorted(glob.glob(os.path.join(ROOT, "api", "*.py"))):
        src = io.open(f, encoding="utf-8").read()
        for i, line in enumerate(src.splitlines(), 1):
            for m in pat.finditer(line):
                if cyr.search(m.group(1)):
                    bad.append(f"{os.path.basename(f)}:{i}")
    assert not bad, f"jsonify(error=русский) без umsg: {bad[:10]}"


def test_umsg_err_unpacks_code_and_vars():
    """umsg_err(SystemExit) даёт фронту {error, err, err_vars}; обычный
    SystemExit идёт без кода (старые сообщения, CLI-ошибки) — фронт покажет
    текст как есть."""
    from core.umsg import UMsg, umsg
    from api._core import umsg_err

    u = umsg("key_rejected", "API-ключ не принят (401) — проверь ключ в настройках ⚙",
             code=401, name="Anthropic")
    assert isinstance(u, UMsg) and str(u).startswith("API-ключ")
    d = umsg_err(SystemExit(u))
    assert d["error"].startswith("API-ключ")
    assert d["err"] == "key_rejected"
    assert d["err_vars"] == {"code": 401, "name": "Anthropic"}

    d = umsg_err(SystemExit("просто текст"))
    assert d["error"] == "просто текст"
    assert d["err"] is None and d["err_vars"] is None
