# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тест-сторож интернационализации логов.

Проверяет:
1. В переведённых модулях в emit() / remit() не осталось голых f-строк с кириллицей.
2. Все русские ключи-шаблоны emit() из переведённых модулей присутствуют в en.json.
3. emit() и remit() корректно упаковывают шаблон и переменные в {"t": ..., "v": ...}.
4. Старая форма emit("готовый текст") сохраняет обратную совместимость.
"""
import ast
import io
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

EN_PATH = os.path.join(ROOT, "static", "i18n", "en.json")

# Список модулей, переведённых на структурированный emit(template, **vars).
# Пополняется по мере конверсии остальных модулей (aicut, insertlib, etc.).
I18N_CONVERTED_LOG_MODULES = [
    "api/jobs.py",
    "api/build.py",
    "api/render.py",
    "api/_core.py",
    "api/previewproxy.py",
    "core/aicut/commands.py",
    "core/aicut/llm.py",
    "core/aicut/video.py",
    "core/aicut/images.py",
    "core/aicut/catalog.py",
    "core/omni_cut.py",
    "core/omni_asr.py",
    "core/gigaam_cut/pipeline.py",
    "core/gigaam_cut/takes.py",
    "core/gigaam_cut/tune.py",
    "core/gigaam_cut/asr.py",
    "core/gigaam_cut/decide.py",
    "core/asr_backends.py",
    "core/insertlib.py",
    "core/roto.py",
    "core/draftrender.py",
    "core/xml2ae/build.py",
    "reelsi.py",
    "core/breath.py",
    "core/falign.py",
    "core/selfcheck.py",
    "core/whisper_cpp.py",
    "core/ytmusic.py",
    "core/subtitle_xml.py",
    "core/ctc_asr.py",
    "core/terms.py",
]

CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")


@pytest.fixture(scope="module")
def en_dict():
    with io.open(EN_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_no_bare_fstrings_with_cyrillic_in_converted_modules():
    """В переведённых модулях все emit/remit с переменными должны использовать
    структурную форму emit('Шаблон {var}', var=val), а не голые f-строки.
    Голая f-строка собирается в Python до emit и теряет связь со словарём."""
    violations = []
    for rel_path in I18N_CONVERTED_LOG_MODULES:
        full_path = os.path.join(ROOT, rel_path)
        assert os.path.isfile(full_path), f"Модуль не найден: {rel_path}"
        code = io.open(full_path, encoding="utf-8").read()
        tree = ast.parse(code, filename=rel_path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = None
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                if name in ("emit", "remit") and node.args:
                    first_arg = node.args[0]
                    # Проверяем JoinedStr (f-string)
                    if isinstance(first_arg, ast.JoinedStr):
                        # Проверяем, есть ли кириллица
                        for val in first_arg.values:
                            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                                if CYRILLIC_RE.search(val.value):
                                    violations.append(f"{rel_path}:{node.lineno}: f-string in {name}()")
                                    break
    assert not violations, f"Найдены f-строки с кириллицей в emit/remit: {violations}"


def test_all_emit_keys_in_converted_modules_exist_in_en_json(en_dict):
    """Все строковые литералы с кириллицей или переменными, передаваемые в emit/remit
    в переведённых модулях, обязаны присутствовать в словаре static/i18n/en.json."""
    missing = []
    for rel_path in I18N_CONVERTED_LOG_MODULES:
        full_path = os.path.join(ROOT, rel_path)
        code = io.open(full_path, encoding="utf-8").read()
        tree = ast.parse(code, filename=rel_path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = None
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                if name in ("emit", "remit") and node.args:
                    first_arg = node.args[0]
                    if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                        s = first_arg.value
                        if CYRILLIC_RE.search(s) or ("{" in s and "}" in s):
                            if s not in en_dict:
                                missing.append((rel_path, node.lineno, s))
    assert not missing, f"Отсутствуют ключи в en.json ({len(missing)}): {missing[:5]}"


def test_emit_and_remit_packaging():
    """emit() и remit() упаковывают параметры в dict {'t': ..., 'v': ...} при наличии vars,
    и оставляют строку без изменений при отсутствии vars."""
    from api._core import JOB, emit
    from api.render import RJOB, remit

    # emit с переменными
    emit("Камера {n}", n=2)
    last_job_entry = JOB["log"][-1]
    assert isinstance(last_job_entry, dict)
    assert last_job_entry == {"t": "Камера {n}", "v": {"n": 2}}

    # emit без переменных (обратная совместимость)
    emit("Простая строка")
    assert JOB["log"][-1] == "Простая строка"

    # remit с переменными
    remit("AE: {name}", name="Adobe After Effects 2026")
    last_rjob_entry = RJOB["log"][-1]
    assert isinstance(last_rjob_entry, dict)
    assert last_rjob_entry == {"t": "AE: {name}", "v": {"name": "Adobe After Effects 2026"}}

    # remit без переменных
    remit("Готовая строка")
    assert RJOB["log"][-1] == "Готовая строка"


def test_t_function_translates_structured_log_keys(en_dict):
    """app_meta.t() корректно подставляет переменные в переведённые ключи лога."""
    from core import app_meta
    from core.app_meta import t

    app_meta._UI_LANG_CACHED = None
    app_meta._I18N_DICT = None
    os.environ["REELSI_LANG"] = "en"
    try:
        translated = t("=== Сборка .jsx: {count} файл(ов), режим {mode} ===", count=3, mode="separate")
        assert translated == "=== Build .jsx: 3 file(s), mode separate ==="

        translated_done = t("Готово: {path}", path="/path/to/clip.mov")
        assert translated_done == "Done: /path/to/clip.mov"
    finally:
        os.environ.pop("REELSI_LANG", None)
        app_meta._UI_LANG_CACHED = None
        app_meta._I18N_DICT = None


node_required = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")


@node_required
def test_boot_polling_handles_mixed_object_and_string_logs():
    """Бут-поллинг в 99-boot.js корректно определяет kind='build' и label='ИИ-нарезка'
    на смешанном логе (объекты {"t": ..., "v": ...} + строки).

    Без fmtLog:
    1. l.indexOf('=== Сборка') падает с TypeError: l.indexOf is not a function (первая запись - объект).
    2. .join('\\n') даёт [object Object] и регулярка /Omni|27b/ не находит модель.
    """
    core_js_path = os.path.join(ROOT, "static", "app", "00-core.js")
    boot_js_path = os.path.join(ROOT, "static", "app", "99-boot.js")
    core_js = io.open(core_js_path, encoding="utf-8").read()
    boot_js = io.open(boot_js_path, encoding="utf-8").read()

    # Извлекаем логику определения kind и label из 99-boot.js
    m_kind = re.search(r"const kind=([^;]+);", boot_js)
    m_label = re.search(r"const label=([^;]+);", boot_js)
    assert m_kind, "Не найдена строка kind в 99-boot.js"
    assert m_label, "Не найдена строка label в 99-boot.js"

    kind_expr = m_kind.group(1)
    label_expr = m_label.group(1)

    node_script = f"""
const localStorage = {{ getItem: () => null, setItem: () => {{}} }};
const document = {{ addEventListener: () => {{}}, querySelector: () => null }};
const window = {{}};
const NodeFilter = {{ SHOW_TEXT: 4 }};

{core_js}

// 1. Тест kind='build' со структурированным логом
const d_build = {{
    log: [
        {{"t": "=== Сборка .jsx: {{count}} файл(ов), режим {{mode}} ===", "v": {{"count": 3, "mode": "separate"}}}},
        "Готово: /path/to/clip.mov"
    ]
}};
let d = d_build;
const kind_build = {kind_expr};

// 2. Тест label='ИИ-нарезка' со структурированным логом Omni
const d_omni = {{
    log: [
        "Инициализация...",
        {{"t": "ИИ-нарезка ({{model}})", "v": {{"model": "Qwen2.5-Omni-7B"}}}}
    ]
}};
d = d_omni;
const label_omni = {label_expr};

// 3. Тест label='Нарезка' для обычной нарезки
const d_cut = {{
    log: [
        "Нарезка по тишине...",
        "Готово"
    ]
}};
d = d_cut;
const label_cut = {label_expr};

console.log(JSON.stringify({{
    kind_build: kind_build,
    label_omni: label_omni,
    label_cut: label_cut
}}));
"""
    p = subprocess.run(
        ["node", "-e", node_script],
        capture_output=True,
        text=True,
        encoding="utf-8-sig",
        errors="replace",
        timeout=60,
    )
    assert p.returncode == 0, f"Ошибка выполнения node скрипта:\n{p.stderr}\n{p.stdout}"
    res = json.loads(p.stdout)

    assert res["kind_build"] == "build", f"Ожидался kind='build', получено: {res['kind_build']}"
    assert res["label_omni"] == "ИИ-нарезка", f"Ожидался label='ИИ-нарезка', получено: {res['label_omni']}"
    assert res["label_cut"] == "Нарезка", f"Ожидался label='Нарезка', получено: {res['label_cut']}"

