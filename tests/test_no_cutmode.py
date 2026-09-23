# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сторож: режим нарезки cutmode полностью убран из UI и API."""
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault("REELSI_NO_BROWSER", "1")


def test_no_cutmode_in_templates_and_static():
    """Строки 'cutmode' нет ни в templates/index.html, ни в static/app/*.js."""
    index_html = os.path.join(ROOT, "templates", "index.html")
    assert os.path.isfile(index_html)
    content = open(index_html, encoding="utf-8").read()
    assert "cutmode" not in content, "В templates/index.html найдена устаревшая строка 'cutmode'"

    js_files = glob.glob(os.path.join(ROOT, "static", "app", "*.js"))
    assert len(js_files) > 0
    for js_path in js_files:
        js_text = open(js_path, encoding="utf-8").read()
        assert "cutmode" not in js_text, f"В {os.path.basename(js_path)} найдена устаревшая строка 'cutmode'"


def test_omnicut_run_does_not_depend_on_asr_field(monkeypatch, tmp_path):
    """api_omnicut_run не читает поле asr из тела запроса и всегда стартует gigaam."""
    from webui import app

    cam_file = tmp_path / "cam1.mp4"
    cam_file.write_bytes(b"fake")

    started_args = []

    def fake_run_omnicut_job(*args, **kwargs):
        started_args.append((args, kwargs))

    monkeypatch.setattr("api.jobs.run_omnicut_job", fake_run_omnicut_job)
    monkeypatch.setattr("api.jobs.job_start", lambda kind, label: True)

    client = app.test_client()
    resp = client.post(
        "/api/omnicut_run",
        json={
            "outdir": str(tmp_path),
            "pairs": [["cam1.mp4"]],
            "camdirs": [str(tmp_path)],
            "asr": "old",  # старое поле, если вдруг кто-то пришлёт
        }
    )
    assert resp.status_code == 200
    assert resp.get_json().get("ok") is True
    assert len(started_args) == 1
    args, _ = started_args[0]
    # 7-й позиционный аргумент run_omnicut_job — mode, он обязан быть "gigaam"
    mode_arg = args[6]
    assert mode_arg == "gigaam"


def test_api_ai_config_set_active_cut_asr(monkeypatch, tmp_path):
    """Эндпоинт /api/ai_config сохраняет active_cut_asr и отклоняет движки без cut."""
    from webui import app

    cfg_file = tmp_path / "ai_config.json"
    cfg_file.write_text('{"active": "LM", "profiles": {"LM": {}}}', encoding="utf-8")
    monkeypatch.setattr("core.aicut.config.AI_CONFIG_PATH", str(cfg_file))

    client = app.test_client()

    # GET возвращает default 'gigaam'
    r_get = client.get("/api/ai_config")
    assert r_get.status_code == 200
    assert r_get.get_json().get("active_cut_asr") == "gigaam"

    # Валидная установка
    r_post = client.post(
        "/api/ai_config",
        json={"action": "set_active_cut_asr", "name": "gigaam:multilingual_large_ctc"}
    )
    assert r_post.status_code == 200
    assert r_post.get_json().get("active_cut_asr") == "gigaam:multilingual_large_ctc"

    # Невалидный движок (whisper:large-v3 — cut=False)
    r_invalid = client.post(
        "/api/ai_config",
        json={"action": "set_active_cut_asr", "name": "whisper:large-v3"}
    )
    assert r_invalid.status_code == 200
    data = r_invalid.get_json()
    assert "error" in data
    assert "не годен для нарезки" in data.get("error", "")


def test_no_cut_mode_wording_in_ui():
    """Сторож формулировок: в templates/index.html (без комментариев) и static/app/*.js
    нет упоминаний «режим нарезки», а для активных ключей en.json — «cut(ting) mode».
    """
    index_html = os.path.join(ROOT, "templates", "index.html")
    assert os.path.isfile(index_html)
    raw_html = open(index_html, encoding="utf-8").read()
    clean_html = re.sub(r"<!--.*?-->", "", raw_html, flags=re.S)

    js_files = glob.glob(os.path.join(ROOT, "static", "app", "*.js"))
    assert len(js_files) > 0

    ru_re = re.compile(r"режим\w*\s+нарезки", re.I)
    en_re = re.compile(r"\bcut(ting)?\s+mode\b", re.I)

    ru_matches = []
    for m in ru_re.finditer(clean_html):
        start = max(0, m.start() - 40)
        end = min(len(clean_html), m.end() + 40)
        ru_matches.append(f"templates/index.html: ...{clean_html[start:end].strip()}...")

    for js_file in js_files:
        content = open(js_file, encoding="utf-8").read()
        for m in ru_re.finditer(content):
            start = max(0, m.start() - 40)
            end = min(len(content), m.end() + 40)
            ru_matches.append(f"{os.path.basename(js_file)}: ...{content[start:end].strip()}...")

    en_json_path = os.path.join(ROOT, "static", "i18n", "en.json")
    assert os.path.isfile(en_json_path)
    en_data = json.load(open(en_json_path, encoding="utf-8"))

    all_ui = [raw_html] + [open(f, encoding="utf-8").read() for f in js_files]

    en_matches = []
    for key, val in en_data.items():
        if any(key in src for src in all_ui):
            if en_re.search(val):
                en_matches.append(f"Ключ: {key!r} -> Перевод: {val!r}")

    errors = []
    if ru_matches:
        errors.append("Найдены упоминания «режима нарезки» в UI:\n  " + "\n  ".join(ru_matches))
    if en_matches:
        errors.append("Найдены «cut(ting) mode» в переводах активных ключей en.json:\n  " + "\n  ".join(en_matches))

    assert not errors, "\n\n".join(errors)


