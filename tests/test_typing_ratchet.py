# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Храповик строгой типизации на границе ядра (внешнее ревью 2026-09-22).

Три сторожа — по одному на каждую часть решения:

1. **Список строгих модулей не сжимается.** Тест держит НИЖНИЙ список: `[tool.mypy]
   files` обязан содержать все эти модули, а `[[tool.mypy.overrides]]` — держать для
   них `disallow_untyped_defs = true`. Граница может расти, уменьшаться — нет.
2. **`mypy` по этому списку не находит ошибок.** Нет `mypy` — тест пропускается с
   причиной: в CI он есть (ставится рядом с ruff), локально может не стоять.
3. **`.project.json` пишет и читает ровно один модуль.** `write_project` ставит
   `version`, `read_project` читает файл БЕЗ `version` (старый = версия 0), и прямых
   `atomic_json_dump(... ".project.json" ...)` вне `core/project_file.py` нет —
   иначе поле, добавленное одним писателем, молча теряется при следующей записи
   другим (так и жили пять независимых писателей раньше).
"""
import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from core.project_file import PROJECT_VERSION, PROJECT_VERSION_LEGACY, read_project, write_project  # noqa: E402

# Нижний список строгого контура: модули, которые зовут все остальные (пути,
# файловый ввод-вывод, медиа, метаданные приложения, рендер, камеры, rclone,
# .project.json) — граница ядра. Свой список, а не чтение чужого: pyproject можно
# расширить свободно, а выкинуть из него модуль тест не даст.
LOWER_BOUND = (
    "core/umsg.py",
    "core/fileio.py",
    "core/media.py",
    "core/paths.py",
    "core/app_meta.py",
    "core/aerender.py",
    "core/cams.py",
    "core/rclone.py",
    "core/xml2ae/__init__.py",
    "core/xml2ae/__main__.py",
    "core/xml2ae/build.py",
    "core/xml2ae/highlights.py",
    "core/xml2ae/jsutil.py",
    "core/xml2ae/layout.py",
    "core/xml2ae/parse.py",
    "core/xml2ae/plan_assets.py",
    "core/xml2ae/plan_audio.py",
    "core/xml2ae/plan_camera.py",
    "core/xml2ae/plan_decor.py",
    "core/xml2ae/plan_inserts.py",
    "core/xml2ae/plan_intro.py",
    "core/xml2ae/plan_intro_tpl.py",
    "core/xml2ae/plan_style.py",
    "core/xml2ae/plan_subs.py",
    "core/xml2ae/plan_words.py",
    "core/xml2ae/template.py",
    "core/aicut/__init__.py",
    "core/aicut/__main__.py",
    "core/aicut/catalog.py",
    "core/aicut/commands.py",
    "core/aicut/config.py",
    "core/aicut/config_actions.py",
    "core/aicut/images.py",
    "core/aicut/llm.py",
    "core/aicut/prompts.py",
    "core/aicut/video.py",
    "core/project_file.py",
    "core/cutjob.py",
    "core/draftrender.py",
    "core/drp.py",
    "core/insertlib.py",
    "core/omni_cut.py",
    "core/verify_jsx.py",
    "api/__init__.py",
    "api/_core.py",
    "api/ai.py",
    "api/build.py",
    "api/editor.py",
    "api/files.py",
    "api/gdrive.py",
    "api/inserts.py",
    "api/jobs.py",
    "api/presets.py",
    "api/previewproxy.py",
    "api/render.py",
    "api/videogen.py",
    "core/align.py",
    "core/asr_backends.py",
    "core/censor.py",
    "core/crashtrace.py",
    "core/omni_asr.py",
    "core/render_job.py",
    "core/roto.py",
    "core/subs.py",
    "core/subtitle_blobs.py",
    "core/terms.py",
    "core/transcribe.py",
    "core/whisper_cpp.py",
    "core/xmlbuild.py",
    "core/__init__.py",
    "core/applog.py",
    "core/arrowfix.py",
    "core/assets.py",
    "core/bootstrap.py",
    "core/breath.py",
    "core/ctc_asr.py",
    "core/cuda_env.py",
    "core/cutstages.py",
    "core/cutstate.py",
    "core/device.py",
    "core/falign.py",
    "core/falign_cli.py",
    "core/fonts.py",
    "core/gigaam_cut/__init__.py",
    "core/gigaam_cut/__main__.py",
    "core/gigaam_cut/asr.py",
    "core/gigaam_cut/decide.py",
    "core/gigaam_cut/pipeline.py",
    "core/gigaam_cut/takes.py",
    "core/gigaam_cut/tune.py",
    "core/gigaam_subs.py",
    "core/headtrack.py",
    "core/jobstate.py",
    "core/omni_review.py",
    "core/selfcheck.py",
    "core/speakers.py",
    "core/ssm.py",
    "core/style_schema.py",
    "core/styles.py",
    "core/subtitle_xml.py",
    "core/sync.py",
    "core/vad.py",
    "core/xmltext.py",
    "core/ytmusic.py",
    "doctor.py",
    "reelsi.py",
    "webui.py",
    "tools/analyze_blobs.py",
    "tools/ast_same.py",
    "tools/bench_vision.py",
    "tools/check_eol.py",
    "tools/harvest_good.py",
    "tools/i18n_extract.py",
    "tools/i18n_js_keys.py",
    "tools/i18n_merge.py",
    "tools/intro_hook_check.py",
    "tools/intro_hook_rules.py",
    "tools/intro_rules.py",
    "tools/mine_edits.py",
    "tools/public_slice.py",
    "tools/slice_check.py",
    "tools/train_breath.py",
    "tools/verify_ae.py",
    "tools/webui_test.py",
    "tools/route_coverage.py",
)

# Каталоги, которые сканирует сторож писателей .project.json: код, а не тесты
# (тесты вправе готовить файл руками — им и проверяем чтение старого формата).
CODE_DIRS = ("core", "api", "tools")


def _pyproject() -> dict[str, Any]:
    """pyproject.toml словарём: tomllib (3.12+), tomli из pip или пропуск."""
    path = ROOT / "pyproject.toml"
    try:
        import tomllib
    except ImportError:
        try:
            from pip._vendor import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            pytest.skip("нет ни tomllib, ни tomli — pyproject.toml не разобрать")
    with open(path, "rb") as f:
        return tomllib.load(f)


def test_pyproject_keeps_strict_module_list():
    """`[tool.mypy] files` содержит строгий список целиком (граница не сжалась)."""
    cfg = _pyproject().get("tool", {}).get("mypy", {})
    files = cfg.get("files")
    assert isinstance(files, list) and files, "[tool.mypy] files пуст или отсутствует"
    missing = [m for m in LOWER_BOUND if m not in files]
    assert not missing, (
        "из строгого списка [tool.mypy] files пропали модули: " + ", ".join(missing)
    )
    for key in ("python_version", "ignore_missing_imports", "warn_unused_ignores",
                "warn_redundant_casts"):
        assert key in cfg, f"в [tool.mypy] нет ключа {key}"


def test_pyproject_strict_modules_require_annotations():
    """Для строгих модулей включён `disallow_untyped_defs` (иначе список ничего не значит)."""
    cfg = _pyproject().get("tool", {}).get("mypy", {})
    strict = None
    for override in cfg.get("overrides", []):
        if override.get("disallow_untyped_defs"):
            strict = set(override.get("module", []))
            break
    assert strict, "нет [[tool.mypy.overrides]] с disallow_untyped_defs = true"
    expected = {m[:-3].replace("/", ".") for m in LOWER_BOUND}
    missing = sorted(expected - strict)
    assert not missing, (
        "disallow_untyped_defs не распространён на модули: " + ", ".join(missing)
    )


def test_mypy_reports_no_errors():
    """`mypy` по строгому списку — 0 ошибок под win32 и linux (нет mypy — пропуск с причиной)."""
    if importlib.util.find_spec("mypy") is None:
        pytest.skip("mypy не установлен (в CI ставится рядом с ruff: pip install -r "
                    "requirements-dev.txt)")
    # --no-incremental: без него mypy заводит .mypy_cache в корне репозитория, а тесты
    # не должны оставлять следов в дереве (сторож изоляции в conftest.py).
    # Проверяем обе платформы явно (win32 и linux), независимо от ОС хоста.
    for platform in ("win32", "linux"):
        r = subprocess.run([sys.executable, "-m", "mypy", "--no-incremental", "--platform", platform],
                           cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=900)
        assert r.returncode == 0, (f"mypy нашёл ошибки в строгом списке под платформой {platform}:\n"
                                   + (r.stdout or "") + (r.stderr or ""))


def test_write_project_stamps_version(tmp_path):
    """`write_project` пишет `version` и остальные поля (тем же отступом, indent=1)."""
    path = tmp_path / "01_clip.project.json"
    write_project(str(path), {"cams": ["a.mp4"], "keep": [[0.0, 1.5]]})
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data["version"] == PROJECT_VERSION == 1
    assert data["cams"] == ["a.mp4"] and data["keep"] == [[0.0, 1.5]]
    assert "\n " in raw, "сайдкар записан не с отступом indent=1"


def test_read_project_reads_file_without_version(tmp_path):
    """Файл без `version` — старый (версия 0): читается как раньше, поля на месте."""
    path = tmp_path / "old.project.json"
    path.write_text(json.dumps({"cams": ["a.mp4"], "keep": [[0.0, 2.0]]}),
                    encoding="utf-8")
    proj = read_project(str(path))
    assert proj is not None
    assert proj["cams"] == ["a.mp4"] and proj["keep"] == [[0.0, 2.0]]
    assert proj["version"] == PROJECT_VERSION_LEGACY == 0


def test_read_project_is_soft_on_missing_and_broken(tmp_path):
    """Нет файла / рваный JSON / не наш формат — None, а не исключение (как было)."""
    assert read_project(str(tmp_path / "нет-такого.project.json")) is None
    broken = tmp_path / "broken.project.json"
    broken.write_text('{"cams": [', encoding="utf-8")
    assert read_project(str(broken)) is None
    not_ours = tmp_path / "list.project.json"
    not_ours.write_text("[1, 2]", encoding="utf-8")
    assert read_project(str(not_ours)) is None


def _project_json_dump_calls() -> list[str]:
    """Вызовы atomic_json_dump с литералом «.project.json» вне core/project_file.py."""
    found = []
    files = [p for d in CODE_DIRS for p in (ROOT / d).rglob("*.py")]
    files += sorted(ROOT.glob("*.py"))
    for path in files:
        if path.name == "project_file.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # Имя как есть (`atomic_json_dump(...)`) и через модуль
            # (`fileio.atomic_json_dump(...)`) — обе формы записи в проекте есть.
            named = (isinstance(func, ast.Name) and func.id == "atomic_json_dump")
            attr = (isinstance(func, ast.Attribute) and func.attr == "atomic_json_dump")
            if not (named or attr):
                continue
            for arg in node.args:
                literals = [n.value for n in ast.walk(arg) if isinstance(n, ast.Constant)
                            and isinstance(n.value, str)]
                if any(".project.json" in s for s in literals):
                    found.append(f"{path.relative_to(ROOT)}:{node.lineno}")
                    break
    return found


def test_project_json_has_single_writer():
    """Ни один модуль, кроме core/project_file.py, не пишет .project.json напрямую."""
    offenders = _project_json_dump_calls()
    assert not offenders, (
        "прямая запись .project.json мимо core.project_file.write_project: "
        + ", ".join(offenders)
    )


def test_project_file_module_keeps_layers():
    """`core/project_file.py` — ядро: ни Flask, ни api, ни импорта из CLI."""
    path = ROOT / "core" / "project_file.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "flask" not in imported, "ядро не имеет права зависеть от HTTP-слоя"
    assert "api" not in imported, "api импортирует core, а не наоборот"
