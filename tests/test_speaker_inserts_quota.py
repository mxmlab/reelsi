# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты квот ИИ-вставок спикера: поле inserts {photo, video}, расчет целей и доборов.

Запуск:  py -3.10 -m pytest tests/test_speaker_inserts_quota.py -q
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from core.aicut import commands
from core import speakers
from core.umsg import ReelsiError


def test_ins_quota_resolution() -> None:
    """1. Разрешение квот: дефолты, оверрайды, битые значения, сумма 0."""
    assert commands.ins_quota(None) == (10, 3)
    assert commands.ins_quota({}) == (10, 3)
    assert commands.ins_quota({"inserts": {"photo": 6, "video": 0}}) == (6, 0)
    assert commands.ins_quota({"photo": 6}) == (6, 3)
    # Битые значения заменяются дефолтом по ключу
    assert commands.ins_quota({"photo": "x"}) == (10, 3)
    assert commands.ins_quota({"photo": -1}) == (10, 3)
    assert commands.ins_quota({"photo": True}) == (10, 3)
    assert commands.ins_quota({"photo": 99}) == (10, 3)
    assert commands.ins_quota({"inserts": {"photo": "x", "video": -1}}) == (10, 3)
    assert commands.ins_quota({"inserts": {"photo": True, "video": 99}}) == (10, 3)
    assert commands.ins_quota({"photo": 6, "video": "bad"}) == (6, 3)
    # Сумма 0 -> дефолт
    assert commands.ins_quota({"photo": 0, "video": 0}) == (10, 3)
    assert commands.ins_quota({"inserts": {"photo": 0, "video": 0}}) == (10, 3)


def test_ins_quota_reelsi_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """ReelsiError из speakers.load не глушится молча, а пробрасывается наверх."""
    def boom(key: str | None) -> dict[str, Any] | None:
        raise ReelsiError("spk_error")
    monkeypatch.setattr(speakers, "load", boom)
    with pytest.raises(ReelsiError):
        commands.ins_quota("some_speaker")

    # Обычный Exception (битый JSON, битый профиль) глушится и даёт дефолт (10, 3)
    def broken(key: str | None) -> dict[str, Any] | None:
        raise ValueError("broken json")
    monkeypatch.setattr(speakers, "load", broken)
    assert commands.ins_quota("some_speaker") == (10, 3)


def test_cmd_inserts_with_speaker_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """2. cmd_inserts с профилем 4 фото + 1 видео: итого 4+1, ins_target == 5,
    в промпте «НУЖНО РОВНО 5 вставок: 4 фото».
    """
    words = [(k, f"слово{k}", float(k * 5.0), float((k + 1) * 5.0)) for k in range(35)]
    monkeypatch.setattr(commands, "_words_from_xml", lambda p: list(words))

    # Ответ модели на 10 фото + 3 видео (тайминги без коллизий по INS_MIN_GAP)
    model_answer = {
        "analysis": "ок",
        "inserts": [
            {"phrase": f"слово{i}", "type": "photo", "start_sec": 6.0 + i * 5.0,
             "duration_sec": 2.5, "prompt": f"фото {i}", "query": f"photo_{i}"}
            for i in range(10)
        ] + [
            {"phrase": f"слово{20 + i}", "type": "video", "start_sec": 60.0 + i * 5.0,
             "duration_sec": 2.5, "prompt": f"видео {i}", "query": f"video_{i}"}
            for i in range(3)
        ],
    }

    recorded_calls: list[tuple[Any, ...]] = []

    def fake_ask_json(*args: Any, **kwargs: Any) -> dict[str, Any]:
        recorded_calls.append(args)
        return model_answer

    monkeypatch.setattr(commands, "_ask_json", fake_ask_json)

    xml_path = tmp_path / "clip.xml"
    profile = {"inserts": {"photo": 4, "video": 1}}
    res = commands.cmd_inserts(str(xml_path), speaker=profile, emit=lambda *a, **k: None)

    assert res["ins_target"] == 5
    photos = [x for x in res["inserts"] if x.get("type") != "video"]
    videos = [x for x in res["inserts"] if x.get("type") == "video"]
    assert len(photos) == 4
    assert len(videos) == 1

    assert len(recorded_calls) >= 1
    user_prompt = recorded_calls[0][1]
    assert "НУЖНО РОВНО 5 вставок: 4 фото" in user_prompt


def test_cmd_inserts_refill_with_speaker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """3. Добор с профилем: модель прислала 2 фото из 4 -> рекурсивный вызов
    получает count=2 и тот же speaker; во втором промпте «НУЖНО РОВНО 2 новых фото».
    """
    words = [(k, f"слово{k}", float(k * 5.0), float((k + 1) * 5.0)) for k in range(35)]
    monkeypatch.setattr(commands, "_words_from_xml", lambda p: list(words))

    recorded_prompts: list[str] = []

    def fake_ask_json(*args: Any, **kwargs: Any) -> dict[str, Any]:
        user_prompt = args[1]
        recorded_prompts.append(user_prompt)
        if len(recorded_prompts) == 1:
            # Первый ответ: только 2 фото и 1 видео (не хватает 2 фото до 4)
            return {
                "analysis": "ок",
                "inserts": [
                    {"phrase": "слово3", "type": "photo", "start_sec": 15.0,
                     "duration_sec": 2.5, "prompt": "фото 1", "query": "photo_1"},
                    {"phrase": "слово6", "type": "photo", "start_sec": 30.0,
                     "duration_sec": 2.5, "prompt": "фото 2", "query": "photo_2"},
                    {"phrase": "слово8", "type": "video", "start_sec": 40.0,
                     "duration_sec": 2.5, "prompt": "видео 1", "query": "video_1"},
                ],
            }
        else:
            # Ответ на добор: 2 новых фото
            return {
                "analysis": "ок",
                "inserts": [
                    {"phrase": "слово10", "type": "photo", "start_sec": 50.0,
                     "duration_sec": 2.5, "prompt": "добор 1", "query": "refill_1"},
                    {"phrase": "слово14", "type": "photo", "start_sec": 70.0,
                     "duration_sec": 2.5, "prompt": "добор 2", "query": "refill_2"},
                ],
            }

    monkeypatch.setattr(commands, "_ask_json", fake_ask_json)

    xml_path = tmp_path / "clip.xml"
    profile = {"inserts": {"photo": 4, "video": 1}}
    res = commands.cmd_inserts(str(xml_path), speaker=profile, emit=lambda *a, **k: None)

    assert len(recorded_prompts) >= 2
    assert "НУЖНО РОВНО 2 новых фото" in recorded_prompts[1]
    photos = [x for x in res["inserts"] if x.get("type") != "video"]
    videos = [x for x in res["inserts"] if x.get("type") == "video"]
    assert len(photos) == 4
    assert len(videos) == 1


def test_cmd_inserts_without_speaker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """4. Без спикера: 10 фото + 3 видео, ins_target == 13."""
    words = [(k, f"слово{k}", float(k * 5.0), float((k + 1) * 5.0)) for k in range(35)]
    monkeypatch.setattr(commands, "_words_from_xml", lambda p: list(words))

    model_answer = {
        "analysis": "ок",
        "inserts": [
            {"phrase": f"слово{i}", "type": "photo", "start_sec": 6.0 + i * 5.0,
             "duration_sec": 2.5, "prompt": f"фото {i}", "query": f"photo_{i}"}
            for i in range(10)
        ] + [
            {"phrase": f"слово{20 + i}", "type": "video", "start_sec": 60.0 + i * 5.0,
             "duration_sec": 2.5, "prompt": f"видео {i}", "query": f"video_{i}"}
            for i in range(3)
        ],
    }

    recorded_prompts: list[str] = []

    def fake_ask_json(*args: Any, **kwargs: Any) -> dict[str, Any]:
        recorded_prompts.append(args[1])
        return model_answer

    monkeypatch.setattr(commands, "_ask_json", fake_ask_json)

    xml_path = tmp_path / "clip.xml"
    res = commands.cmd_inserts(str(xml_path), speaker=None, emit=lambda *a, **k: None)

    assert res["ins_target"] == 13
    photos = [x for x in res["inserts"] if x.get("type") != "video"]
    videos = [x for x in res["inserts"] if x.get("type") == "video"]
    assert len(photos) == 10
    assert len(videos) == 3
    assert "НУЖНО РОВНО 13 вставок: 10 фото" in recorded_prompts[0]


def test_speakers_save_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """5. speakers.save с некорректными inserts -> ValueError, с валидным -> сохраняет."""
    spk_dir = tmp_path / "speakers"
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(spk_dir))

    for bad_ins in [{"photo": "a"}, {"foo": 1}, {"photo": 31}]:
        with pytest.raises(ValueError):
            speakers.save("bad_spk", {"inserts": bad_ins})

    key, path = speakers.save("good_spk", {"inserts": {"photo": 6, "video": 0}})
    assert os.path.isfile(path)
    loaded = speakers.load(key)
    assert loaded is not None
    assert loaded.get("inserts") == {"photo": 6, "video": 0}


def test_route_ai_inserts_passes_speaker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """6. Роут /api/ai_inserts передает speaker в cmd_inserts."""
    from flask import Flask
    import api
    from core import aicut

    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    client = app.test_client()

    xml_file = tmp_path / "sample.xml"
    xml_file.write_text("<xml></xml>", encoding="utf-8")

    captured_kwargs: dict[str, Any] = {}

    def fake_cmd_inserts(xml_path: str, **kwargs: Any) -> dict[str, Any]:
        captured_kwargs.update(kwargs)
        captured_kwargs["xml_path"] = xml_path
        return {"path": xml_path + ".inserts.json", "inserts": [], "ins_target": 13}

    monkeypatch.setattr(aicut, "cmd_inserts", fake_cmd_inserts)

    resp = client.post("/api/ai_inserts", json={"xml": str(xml_file), "speaker": "test_speaker"})
    assert resp.status_code == 200
    assert captured_kwargs.get("speaker") == "test_speaker"


def test_frontend_calls_and_target_fallback() -> None:
    """7. Во всех 4 вызовах /api/ai_inserts на фронте есть speaker:, и в 80-inserts.js
    нет устаревшего Math.max(c.insTarget||0,13).
    """
    ed_js = (ROOT / "static/app/70-editor.js").read_text(encoding="utf-8")
    ins_js = (ROOT / "static/app/80-inserts.js").read_text(encoding="utf-8")

    rx = re.compile(r"['\"]/api/ai_inserts['\"],\s*\{([^}]+)\}")
    matches_70 = rx.findall(ed_js)
    matches_80 = rx.findall(ins_js)

    assert len(matches_70) == 2, f"Ожидалось 2 вызова в 70-editor.js, найдено: {len(matches_70)}"
    assert len(matches_80) == 2, f"Ожидалось 2 вызова в 80-inserts.js, найдено: {len(matches_80)}"

    for body in matches_70 + matches_80:
        assert "speaker:" in body, f"Вызов без speaker: {body}"

    assert "Math.max(c.insTarget||0,13)" not in ins_js
