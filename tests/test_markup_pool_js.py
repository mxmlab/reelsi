# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты пула параллельной разметки (runPool) на фронте и передачи batch."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
EDITOR_JS = ROOT / "static" / "app" / "70-editor.js"
INSERTS_JS = ROOT / "static" / "app" / "80-inserts.js"

node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")


def _extract_run_pool(src: str) -> str:
    """Извлечь текст функции runPool от объявления до сбалансированной закрывающей скобки."""
    match = re.search(r"async\s+function\s+runPool\(", src)
    assert match is not None, "async function runPool не найдена в 70-editor.js"
    start = match.start()
    depth = 0
    in_fn = False
    for i in range(start, len(src)):
        char = src[i]
        if char == "{":
            depth += 1
            in_fn = True
        elif char == "}":
            depth -= 1
            if in_fn and depth == 0:
                return src[start : i + 1]
    raise ValueError("Не найдена закрывающая фигурная скобка для runPool")


def _run_node_script(tmp_path: Path, script_name: str, body: str) -> Any:
    """Запустить проверочный JS-скрипт с боевой функцией runPool под node."""
    editor_src = EDITOR_JS.read_text(encoding="utf-8")
    run_pool_src = _extract_run_pool(editor_src)
    full_script = f"""
let UICANCEL = false;

{run_pool_src}

(async () => {{
{body}
}})().catch(err => {{
  console.error(err);
  process.exit(1);
}});
"""
    script_file = tmp_path / script_name
    script_file.write_text(full_script, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(script_file)],
        capture_output=True,
        text=True,
        encoding="utf-8-sig",
        errors="replace",
        timeout=30,
    )
    assert proc.returncode == 0, f"Node error: {proc.stderr or proc.stdout}"
    lines = [line.strip() for line in proc.stdout.strip().splitlines() if line.strip()]
    assert lines, "Node did not produce output"
    return json.loads(lines[-1])


@node
def test_run_pool_concurrency_and_start_order(tmp_path: Path) -> None:
    """1. 7 элементов, n=3, fn с задержкой 20мс -> максимум одновременных ровно 3,
    обработаны все 7, порядок старта 0..6."""
    body = """
  let cur = 0;
  let maxCur = 0;
  const started = [];
  const processed = [];
  const items = [0, 1, 2, 3, 4, 5, 6];

  await runPool(items, 3, async (it, i) => {
    started.push(it);
    cur++;
    if (cur > maxCur) maxCur = cur;
    await new Promise(r => setTimeout(r, 20));
    cur--;
    processed.push(it);
  });

  console.log(JSON.stringify({
    maxCur,
    started,
    processedCount: processed.length
  }));
"""
    res = _run_node_script(tmp_path, "test1.js", body)
    assert res["maxCur"] == 3, f"Ожидался maxCur=3, получено: {res['maxCur']}"
    assert res["processedCount"] == 7
    assert res["started"] == [0, 1, 2, 3, 4, 5, 6]


@node
def test_run_pool_n1_concurrency_and_order(tmp_path: Path) -> None:
    """2. n=1 -> максимум одновременных 1, порядок 0..6."""
    body = """
  let cur = 0;
  let maxCur = 0;
  const started = [];
  const processed = [];
  const items = [0, 1, 2, 3, 4, 5, 6];

  await runPool(items, 1, async (it, i) => {
    started.push(it);
    cur++;
    if (cur > maxCur) maxCur = cur;
    await new Promise(r => setTimeout(r, 10));
    cur--;
    processed.push(it);
  });

  console.log(JSON.stringify({
    maxCur,
    started,
    processedCount: processed.length
  }));
"""
    res = _run_node_script(tmp_path, "test2.js", body)
    assert res["maxCur"] == 1
    assert res["started"] == [0, 1, 2, 3, 4, 5, 6]
    assert res["processedCount"] == 7


@node
def test_run_pool_uicancel_abort(tmp_path: Path) -> None:
    """3. UICANCEL=true выставляется внутри fn на втором элементе при n=1 -> обработано ровно 2."""
    body = """
  let processed = 0;
  const items = [0, 1, 2, 3, 4, 5, 6];

  await runPool(items, 1, async (it, i) => {
    processed++;
    if (processed === 2) {
      UICANCEL = true;
    }
    await new Promise(r => setTimeout(r, 10));
  });

  console.log(JSON.stringify({
    processed
  }));
"""
    res = _run_node_script(tmp_path, "test3.js", body)
    assert res["processed"] == 2


@node
def test_run_pool_more_workers_than_items(tmp_path: Path) -> None:
    """4. n=10 на 3 элементах -> все 3, без падений."""
    body = """
  let processed = 0;
  const items = [0, 1, 2];

  await runPool(items, 10, async (it, i) => {
    processed++;
    await new Promise(r => setTimeout(r, 10));
  });

  console.log(JSON.stringify({
    processed
  }));
"""
    res = _run_node_script(tmp_path, "test4.js", body)
    assert res["processed"] == 3


def test_batch_in_markup_all_and_absent_in_clip_and_inserts() -> None:
    """5. Регуляркой по тексту 70-editor.js: в markupAllRun оба ИИ-вызова несут batch,
    и в markupClip оба тоже (параллельные жёлтые и вставки одного ролика — одна пачка,
    иначе begin_call вытесняет первый); в static/app/80-inserts.js batch нет (одиночные
    вызовы вставок идут не параллельно, отдельной пачки не требуют)."""
    editor_src = EDITOR_JS.read_text(encoding="utf-8")

    # Вытаскиваем markupAllRun и markupClip
    m_all = re.search(r"async\s+function\s+markupAllRun\b[\s\S]*?(?=async\s+function\s+markupClip\b)", editor_src)
    assert m_all is not None, "markupAllRun не найдена"
    all_src = m_all.group(0)

    m_clip = re.search(r"async\s+function\s+markupClip\b[\s\S]*?(?=function\s+sleep\b)", editor_src)
    assert m_clip is not None, "markupClip не найдена"
    clip_src = m_clip.group(0)

    # В markupAllRun оба ИИ-вызова несут batch
    y_all = re.search(r"aiPost\(\s*['\"]/api/ai_yellow['\"]\s*,\s*\{([^}]+)\}", all_src)
    assert y_all is not None, "aiPost(/api/ai_yellow) не найден в markupAllRun"
    assert re.search(r"\bbatch\b", y_all.group(1)) is not None, "batch отсутствует в вызове ai_yellow в markupAllRun"

    i_all = re.search(r"aiPost\(\s*['\"]/api/ai_inserts['\"]\s*,\s*\{([^}]+)\}", all_src)
    assert i_all is not None, "aiPost(/api/ai_inserts) не найден в markupAllRun"
    assert re.search(r"\bbatch\b", i_all.group(1)) is not None, "batch отсутствует в вызове ai_inserts в markupAllRun"

    # В markupClip оба вызова несут batch (одна пачка на запуск, см. docstring)
    y_clip = re.search(r"aiPost\(\s*['\"]/api/ai_yellow['\"]\s*,\s*\{([^}]+)\}", clip_src)
    assert y_clip is not None, "aiPost(/api/ai_yellow) не найден в markupClip"
    assert re.search(r"\bbatch\b", y_clip.group(1)) is not None, "batch отсутствует в вызове ai_yellow в markupClip"

    i_clip = re.search(r"aiPost\(\s*['\"]/api/ai_inserts['\"]\s*,\s*\{([^}]+)\}", clip_src)
    assert i_clip is not None, "aiPost(/api/ai_inserts) не найден в markupClip"
    assert re.search(r"\bbatch\b", i_clip.group(1)) is not None, "batch отсутствует в вызове ai_inserts в markupClip"

    # В 80-inserts.js batch нет в исполняемом коде (вне комментариев)
    inserts_src = INSERTS_JS.read_text(encoding="utf-8")
    inserts_no_comments = re.sub(r"//.*", "", inserts_src)
    inserts_no_comments = re.sub(r"/\*[\s\S]*?\*/", "", inserts_no_comments)
    assert re.search(r"\bbatch\b", inserts_no_comments) is None, "batch обнаружен в коде 80-inserts.js"
