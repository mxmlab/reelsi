# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты конвейера разметки: готовность субтитров, параллелизм жёлтых и вставок, учёт локальности моделей."""
from __future__ import annotations

import io
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EDITOR_JS = ROOT / "static" / "app" / "70-editor.js"
QUEUE_JS = ROOT / "static" / "app" / "40-queue.js"

node = pytest.mark.skipif(not shutil.which("node"), reason="контракт фронта требует node в PATH")


def _func(src: str, name: str) -> str:
    """Вырезать функцию целиком по балансу фигурных скобок."""
    m = re.search(r"(?:async\s+)?function\s+%s\s*\(" % re.escape(name), src)
    assert m, f"в 70-editor.js не нашлась функция {name}"
    i = src.index("{", m.end() - 1)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start() : j + 1]
    raise AssertionError(f"не сошлись скобки у {name}")


def _clip_speaker() -> str:
    """Боевой `clipSpeaker` из 40-queue.js — им пользуется вызов /api/ai_inserts."""
    return _func(io.open(str(QUEUE_JS), encoding="utf-8").read(), "clipSpeaker")


def _extracted_js() -> str:
    """Извлечь боевые функции конвейера из 70-editor.js."""
    src = io.open(str(EDITOR_JS), encoding="utf-8").read()
    names = ["markupPlan", "markupAllRun", "markupClip", "qClipSum", "aiStepConc", "runPool"]
    return _clip_speaker() + "\n\n" + "\n\n".join(_func(src, n) for n in names)


def _run_pipeline_stand(scenario_cfg: dict[str, Any], fail_yellow_clip: str | None = None) -> dict[str, Any]:
    """Запустить 3 клипа через боевой markupAllRun под node и вернуть события и статусы."""
    extracted = _extracted_js()
    harness = f"""
let UICANCEL = false;
let curAE = -1;
let AICFG = {json.dumps(scenario_cfg)};
const FAIL_YELLOW_CLIP = {json.dumps(fail_yellow_clip)};

let seq = 0;
const events = [];
const localQStatus = {{}};

function localQStart(names) {{}}
function localQSet(name, stage, detail) {{
  localQStatus[name] = stage;
}}
function localQEnd() {{}}
function renderClips2() {{}}
function saveState() {{}}
function uiLog(msg) {{}}
function toast(msg) {{}}
function progQueue(title, k, n) {{}}
function progStep(title, pct) {{}}
function progDone(msg, err) {{}}
function t(s) {{ return s; }}
function engLabel(s) {{ return s; }}
function subSkipped(d) {{ return ''; }}
function errText(e) {{ return String(e); }}
function askConfirm(msg) {{ return Promise.resolve(false); }}
function clearHl(c) {{}}
function loadWordsFor(xml) {{}}
function val(id) {{ return ''; }}
function insLog(d) {{}}
function insAfterAI(c) {{ return Promise.resolve(null); }}

async function fetch(url, opts) {{
  if (url === '/api/xml_state') {{
    return {{
      json: async () => ({{ subs: 0, colored: 0, ncams: 2 }})
    }};
  }}
  if (url === '/api/gen_subs') {{
    const body = JSON.parse((opts && opts.body) || '{{}}');
    const startSeq = seq++;
    events.push({{ api: '/api/gen_subs', clip: body.xml, type: 'start', seq: startSeq }});
    await new Promise(r => setTimeout(r, 25));
    const endSeq = seq++;
    events.push({{ api: '/api/gen_subs', clip: body.xml, type: 'end', seq: endSeq }});
    return {{
      json: async () => ({{ subs: 10 }})
    }};
  }}
  return {{ json: async () => ({{}}) }};
}}

async function aiPost(url, body, title) {{
  const startSeq = seq++;
  events.push({{ api: url, clip: body.xml, type: 'start', seq: startSeq }});
  if (FAIL_YELLOW_CLIP && url === '/api/ai_yellow' && body.xml === FAIL_YELLOW_CLIP) {{
    await new Promise(r => setTimeout(r, 10));
    events.push({{ api: url, clip: body.xml, type: 'error', seq: seq++ }});
    throw new Error('yellow failure for ' + body.xml);
  }}
  await new Promise(r => setTimeout(r, 25));
  const endSeq = seq++;
  events.push({{ api: url, clip: body.xml, type: 'end', seq: endSeq }});
  if (url === '/api/ai_yellow') {{
    return {{ colored: [1, 2] }};
  }}
  if (url === '/api/ai_inserts') {{
    return {{ inserts: [{{ id: 1 }}], insTarget: 1 }};
  }}
  return {{}};
}}

{extracted}

(async () => {{
  const clips = [
    {{ xml: 'c1.xml', name: 'clip1', status: {{ subs: 0, colored: 0 }}, inserts: [] }},
    {{ xml: 'c2.xml', name: 'clip2', status: {{ subs: 0, colored: 0 }}, inserts: [] }},
    {{ xml: 'c3.xml', name: 'clip3', status: {{ subs: 0, colored: 0 }}, inserts: [] }},
  ];
  await markupAllRun('whisper', clips, ['subs', 'yellow', 'inserts']);
  console.log(JSON.stringify({{
    events: events,
    statuses: localQStatus,
    clips: clips.map(c => ({{ name: c.name, status: c.status, insertsCount: (c.inserts || []).length }}))
  }}));
}})().catch(err => {{
  console.error(err);
  process.exit(1);
}});
"""
    proc = subprocess.run(
        ["node", "-e", harness],
        capture_output=True,
        text=True,
        encoding="utf-8-sig",
        errors="replace",
        timeout=30,
    )
    assert proc.returncode == 0, f"Node process failed: {proc.stderr or proc.stdout}"
    lines = [line.strip() for line in proc.stdout.strip().splitlines() if line.strip()]
    assert lines, "Node did not produce output"
    res: dict[str, Any] = json.loads(lines[-1])
    return res


@node
def test_pipeline_both_cloud_starts_early() -> None:
    """1. Оба шага облачные: первый вызов /api/ai_yellow и /api/ai_inserts для клипа 1
    происходит ДО /api/gen_subs клипа 3."""
    cfg = {
        "step_local": {"yellow": False, "inserts": False},
        "step_profiles": {"yellow": "prof_cloud_y", "inserts": "prof_cloud_i"},
        "step_concurrency": {"yellow": 2, "inserts": 2},
    }
    data = _run_pipeline_stand(cfg)
    events: list[dict[str, Any]] = data["events"]

    y1_start = next(e["seq"] for e in events if e["api"] == "/api/ai_yellow" and e["clip"] == "c1.xml" and e["type"] == "start")
    i1_start = next(e["seq"] for e in events if e["api"] == "/api/ai_inserts" and e["clip"] == "c1.xml" and e["type"] == "start")
    s3_start = next(e["seq"] for e in events if e["api"] == "/api/gen_subs" and e["clip"] == "c3.xml" and e["type"] == "start")

    assert y1_start < s3_start, f"ai_yellow clip 1 ({y1_start}) стартовал не раньше gen_subs clip 3 ({s3_start})"
    assert i1_start < s3_start, f"ai_inserts clip 1 ({i1_start}) стартовал не раньше gen_subs clip 3 ({s3_start})"
    assert data["statuses"] == {"clip1": "done", "clip2": "done", "clip3": "done"}


@node
def test_pipeline_yellow_local_inserts_cloud() -> None:
    """2. Жёлтые локальные, вставки облачные: все ai_yellow — после последнего gen_subs,
    а ai_inserts клипа 1 — до него."""
    cfg = {
        "step_local": {"yellow": True, "inserts": False},
        "step_profiles": {"yellow": "prof_local_y", "inserts": "prof_cloud_i"},
        "step_concurrency": {"yellow": 2, "inserts": 2},
    }
    data = _run_pipeline_stand(cfg)
    events: list[dict[str, Any]] = data["events"]

    last_subs_end = max(e["seq"] for e in events if e["api"] == "/api/gen_subs" and e["type"] == "end")
    i1_start = next(e["seq"] for e in events if e["api"] == "/api/ai_inserts" and e["clip"] == "c1.xml" and e["type"] == "start")
    yellow_starts = [e["seq"] for e in events if e["api"] == "/api/ai_yellow" and e["type"] == "start"]

    assert i1_start < last_subs_end, f"ai_inserts clip 1 ({i1_start}) стартовал не до окончания субтитров ({last_subs_end})"
    for ys in yellow_starts:
        assert ys > last_subs_end, f"ai_yellow ({ys}) стартовал раньше окончания субтитров ({last_subs_end})"


@node
def test_pipeline_both_local_different_models() -> None:
    """3. Оба локальные, разные модели: все ai_inserts — после последнего ai_yellow."""
    cfg = {
        "step_local": {"yellow": True, "inserts": True},
        "step_profiles": {"yellow": "prof_local_y", "inserts": "prof_local_i"},
        "step_concurrency": {"yellow": 2, "inserts": 2},
    }
    data = _run_pipeline_stand(cfg)
    events: list[dict[str, Any]] = data["events"]

    last_yellow_end = max(e["seq"] for e in events if e["api"] == "/api/ai_yellow" and e["type"] == "end")
    inserts_starts = [e["seq"] for e in events if e["api"] == "/api/ai_inserts" and e["type"] == "start"]

    for is_ in inserts_starts:
        assert is_ > last_yellow_end, f"ai_inserts ({is_}) стартовал раньше окончания всех ai_yellow ({last_yellow_end})"


@node
def test_pipeline_both_local_same_model_overlap() -> None:
    """4. Оба локальные, одна модель: жёлтые и вставки клипа перекрываются."""
    cfg = {
        "step_local": {"yellow": True, "inserts": True},
        "step_profiles": {"yellow": "shared_local_prof", "inserts": "shared_local_prof"},
        "step_concurrency": {"yellow": 2, "inserts": 2},
    }
    data = _run_pipeline_stand(cfg)
    events: list[dict[str, Any]] = data["events"]

    # Проверяем, что есть перекрытие между yellow и inserts
    overlapped = False
    for clip in ("c1.xml", "c2.xml", "c3.xml"):
        y_start = next(e["seq"] for e in events if e["api"] == "/api/ai_yellow" and e["clip"] == clip and e["type"] == "start")
        y_end = next(e["seq"] for e in events if e["api"] == "/api/ai_yellow" and e["clip"] == clip and e["type"] == "end")
        i_start = next(e["seq"] for e in events if e["api"] == "/api/ai_inserts" and e["clip"] == clip and e["type"] == "start")
        i_end = next(e["seq"] for e in events if e["api"] == "/api/ai_inserts" and e["clip"] == clip and e["type"] == "end")
        if max(y_start, i_start) < min(y_end, i_end):
            overlapped = True
            break

    assert overlapped, "жёлтые и вставки ни у одного клипа не перекрылись во времени"


@node
def test_pipeline_yellow_error_continues_inserts() -> None:
    """5. Ошибка ai_yellow у клипа 2 — его вставки всё равно вызваны, статус клипа 2 — error."""
    cfg = {
        "step_local": {"yellow": False, "inserts": False},
        "step_profiles": {"yellow": "prof_cloud_y", "inserts": "prof_cloud_i"},
        "step_concurrency": {"yellow": 2, "inserts": 2},
    }
    data = _run_pipeline_stand(cfg, fail_yellow_clip="c2.xml")
    events: list[dict[str, Any]] = data["events"]

    inserts2_call = any(e["api"] == "/api/ai_inserts" and e["clip"] == "c2.xml" and e["type"] == "start" for e in events)
    assert inserts2_call, "ai_inserts для клипа 2 не был вызван после ошибки в ai_yellow"

    assert data["statuses"]["clip2"] == "error"
    assert data["statuses"]["clip1"] == "done"
    assert data["statuses"]["clip3"] == "done"
