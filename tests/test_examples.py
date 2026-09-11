# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Пример-файлы для первого запуска (insertlib.example.json, styles.example.json,
speakers.example.json).

Рабочие данные (insertlib.json, styles/*.json, speakers/*.json) в гит не попадают —
они личные и тяжёлые. В репозитории вместо них лежат примеры, которые новый
пользователь копирует себе под правильным именем. Тест стережёт ровно то, что
пример обязан оставаться ВАЛИДНЫМ для загрузчиков: скопированный файл не должен
падать там, где это предусмотрено (иначе «запустилось на примере» молча врёт).

Запуск:  python -m pytest reelsi/tests -q
"""
import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import insertlib  # noqa: E402
from core import speakers  # noqa: E402
from core import styles  # noqa: E402

EXAMPLES = {
    "insertlib": os.path.join(ROOT, "examples", "insertlib.example.json"),
    "styles": os.path.join(ROOT, "examples", "styles.example.json"),
    "speakers": os.path.join(ROOT, "examples", "speakers.example.json"),
}


def _read(name):
    path = EXAMPLES[name]
    assert os.path.isfile(path), f"нет {os.path.basename(path)} в examples/ репозитория"
    return json.load(open(path, encoding="utf-8"))


def test_examples_are_valid_json():
    for name in EXAMPLES:
        _read(name)


def test_insertlib_example_loads_as_empty_index(tmp_path, monkeypatch):
    """Скопированный пример должен читаться загрузчиком как пустая собранная база
    (built=True, count=0) — UI покажет «в базе 0 файлов» и предложит скан, а не
    «база не построена»."""
    idx = tmp_path / "insertlib.json"
    idx.write_bytes(open(EXAMPLES["insertlib"], "rb").read())
    monkeypatch.setattr(insertlib, "INDEX_PATH", str(idx))
    insertlib._CACHE["data"] = None
    d = insertlib._load()
    assert d is not None
    assert isinstance(d.get("items"), list) and not d["items"]
    assert isinstance(d.get("dirs"), list)
    assert d.get("emb_tag") == insertlib.EMB_TAG, (
        "схема эмбеддинга в примере устарела — первый матч молча пересчитает "
        "весь индекс")
    info = insertlib.info()
    assert info["built"] and info["count"] == 0


def test_style_example_resolves_with_defaults():
    """Скопированный пресет обязан пройти resolve() — недостающие поля дотянутся
    из «Базового»."""
    d = _read("styles")
    assert d.get("label"), "у пресета нет label — в UI он потеряет имя"
    res = styles.resolve(copy.deepcopy(d))
    assert res["label"] == d["label"]
    assert res["font"] == d.get("font", styles.BASE["font"])
    assert "hl_fill" in res and "music_db" in res


def test_speaker_example_saves_and_loads(tmp_path, monkeypatch):
    """Профиль проходит собственные правила speakers: неизвестных ключей в cut нет
    (иначе save() бросит), после копирования в speakers/ его видит all_speakers()."""
    d = _read("speakers")
    assert d.get("label")
    unknown = [k for k in (d.get("cut") or {}) if k not in speakers.CUT_DEFAULTS]
    assert not unknown, "неизвестные пороги в примере: " + ", ".join(sorted(unknown))
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(tmp_path / "speakers"))
    key, path = speakers.save(d["label"], copy.deepcopy(d))
    assert os.path.isfile(path)
    loaded = speakers.all_speakers()
    assert key in loaded and loaded[key]["label"] == d["label"]
