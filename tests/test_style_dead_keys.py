# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты чистки мёртвых ключей стиля (DEAD_KEYS в core/styles.py).

Сборка больше не читает roto_video, caption_padx/caption_pady и intro_fx_fade/
intro_fx_fade_last, но в пользовательских styles/*.json они остались с прежних
версий. migrate_style_dict выкидывает их на любом входе — и на чтении файла,
и на resolve(), и на save()/patch()."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import styles  # noqa: E402

# Все пять ключей, которые сборка не читает (см. styles.DEAD_KEYS).
DEAD = ("roto_video", "caption_padx", "caption_pady", "intro_fx_fade", "intro_fx_fade_last")


def test_migrate_removes_dead_keys():
    """migrate_style_dict выкидывает все пять мёртвых ключей, живые не трогает."""
    data = {k: 1 for k in DEAD}
    data["intro_fade"] = 0.5
    data["font"] = "X"

    out, changed = styles.migrate_style_dict(data)

    assert changed is True
    for k in DEAD:
        assert k not in out
    assert out["intro_fade"] == 0.5
    assert out["font"] == "X"


def test_migrate_is_idempotent():
    """Повторный вызов на уже почищенном словаре ничего не меняет."""
    data = {k: 1 for k in DEAD}
    data["intro_fade"] = 0.5
    data["font"] = "X"

    first, changed_first = styles.migrate_style_dict(data)
    assert changed_first is True

    second, changed_second = styles.migrate_style_dict(first)

    assert changed_second is False
    assert second == first


def test_files_rewrites_file_without_dead_keys(tmp_path, monkeypatch):
    """_files() (через all_styles) перезаписывает файл пресета без мёртвых ключей."""
    styles_dir = tmp_path / "styles"
    styles_dir.mkdir()
    monkeypatch.setattr(styles, "STYLE_DIR", str(styles_dir))

    file_path = styles_dir / "t.json"
    sample = {"label": "T", "font": "X", "music_db": -20.0, "roto_video": False, "caption_padx": 56}
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(sample, f, ensure_ascii=False, indent=1)

    all_styles = styles.all_styles()
    assert "t" in all_styles

    with open(file_path, "r", encoding="utf-8") as f:
        on_disk = json.load(f)

    assert "roto_video" not in on_disk
    assert "caption_padx" not in on_disk
    # Прочие ключи шаблона сохранены (плюс добавленный layer_order от миграции).
    assert on_disk["label"] == "T"
    assert on_disk["font"] == "X"
    assert on_disk["music_db"] == -20.0


def test_resolve_drops_dead_keys():
    """resolve() не отдаёт мёртвые ключи наружу."""
    out = styles.resolve({"intro_fx_fade": 0.9})

    assert "intro_fx_fade" not in out
    for k in DEAD:
        assert k not in out
