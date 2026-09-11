# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Профили спикеров: дефолты, применение к порогам gigaam_cut, автопороги.

Главное здесь — первый тест. Дефолты в speakers.CUT_DEFAULTS продублированы с
констант gigaam_cut, и если они разъедутся, то «спикер без правок» молча
поменяет нарезку — ровно та тихая поломка, которую руками не заметить.

Запуск:  python -m pytest reelsi/tests -q
"""
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import speakers  # noqa: E402
from core import gigaam_cut as gc  # noqa: E402
# Пороги берём у модуля-владельца: на фасаде пакета их нет намеренно —
# `from .tune import SNAP_DB` заморозил бы значение по умолчанию, и профиль
# спикера молча перестал бы действовать. Функции при этом остаются на фасаде:
# они читают globals своего модуля, и он у них тот же самый.
from core.gigaam_cut import tune as gt  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_globals():
    """Тесты меняют пороги в gigaam_cut.tune — вернуть как было, иначе поедут
    соседние тесты (порядок в pytest не гарантирован)."""
    saved = {g: getattr(gt, g) for g in gt._CUT_GLOBALS.values()}
    saved["SPEAKER"] = gt.SPEAKER
    saved["_DB_TUNED"] = gt._DB_TUNED
    yield
    for k, v in saved.items():
        setattr(gt, k, v)


def test_defaults_match_gigaam_constants():
    """Дефолт профиля == константа модуля. Расхождение = тихая смена нарезки."""
    for key, gname in gt._CUT_GLOBALS.items():
        assert key in speakers.CUT_DEFAULTS, f"{key} нет в CUT_DEFAULTS"
        assert speakers.CUT_DEFAULTS[key] == pytest.approx(getattr(gt, gname)), (
            f"{key}: профиль {speakers.CUT_DEFAULTS[key]}, "
            f"gigaam_cut.tune.{gname} {getattr(gt, gname)}")


def test_every_default_is_wired():
    """Каждый порог из CUT_DEFAULTS реально доезжает до константы модуля —
    иначе поле в UI есть, а эффекта нет."""
    assert set(speakers.CUT_DEFAULTS) == set(gt._CUT_GLOBALS)


def test_resolve_cut_ignores_junk():
    prof = {"cut": {"min_keep": "0.3", "onset_db": None, "нет_такого": 1}}
    cut = speakers.resolve_cut(prof)
    assert cut["min_keep"] == 0.3                      # строка приведена к float
    assert cut["onset_db"] == speakers.CUT_DEFAULTS["onset_db"]   # None -> дефолт
    assert "нет_такого" not in cut


def test_resolve_cut_none_is_defaults():
    assert speakers.resolve_cut(None) == speakers.CUT_DEFAULTS


def test_save_rejects_unknown_threshold(tmp_path, monkeypatch):
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        speakers.save("Тест", {"cut": {"onset_dB": 12}})   # опечатка в регистре


def test_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(tmp_path))
    key, path = speakers.save("Тест Тестов", {"style": "base", "cut": {"min_keep": 0.3}})
    assert os.path.isfile(path)
    assert " " not in key                              # имя файла без пробелов
    assert speakers.load(key)["cut"]["min_keep"] == 0.3
    assert speakers.load("Тест Тестов")["style"] == "base"   # ищется и по label
    assert speakers.delete(key) is True
    assert speakers.load(key) is None


def test_camdirs_roundtrip(tmp_path, monkeypatch):
    """Папки камер живут в профиле, как outdir/jsxdir (задание U): сохраняются и
    возвращаются как есть; отсутствие camdirs — автоподбор, не «пустые папки»."""
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(tmp_path))
    speakers.save("Два пульта", {"camdirs": ["C:/камера1", "C:/камера2"]})
    assert speakers.load("Два пульта")["camdirs"] == ["C:/камера1", "C:/камера2"]
    key, _ = speakers.save("Без папок", {})
    assert "camdirs" not in speakers.load(key)


def test_image_prompts_roundtrip(tmp_path, monkeypatch):
    """Приписки к промптам генерации картинок живут в профиле (задание CQ)."""
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(tmp_path))
    prompts = {
        "a": {"extra": "3d icon", "pos": "suffix"},
        "b": {"extra": "photorealistic", "pos": "prefix"},
    }
    speakers.save("Иконщик", {"image_prompts": prompts})
    loaded = speakers.load("Иконщик")
    assert loaded["image_prompts"] == prompts
    assert loaded["image_prompts"]["a"]["extra"] == "3d icon"
    assert loaded["image_prompts"]["b"]["pos"] == "prefix"


def test_video_prompts_roundtrip(tmp_path, monkeypatch):
    """Приписки видео-вставок независимы от image_prompts (задание EZ)."""
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(tmp_path))
    prompts = {
        "a": {"extra": "cinematic lighting", "pos": "suffix"},
        "b": {"extra": "close-up", "pos": "prefix"},
    }
    speakers.save("Видеограф", {"video_prompts": prompts})
    loaded = speakers.load("Видеограф")
    assert loaded["video_prompts"] == prompts
    assert "image_prompts" not in loaded


def test_apply_speaker_unknown_keeps_defaults():
    before = gt.MIN_KEEP
    assert gc.apply_speaker("нет-такого-спикера", emit=lambda *a, **k: None) is None
    assert gt.MIN_KEEP == before


def test_apply_speaker_none_is_noop():
    before = {g: getattr(gt, g) for g in gt._CUT_GLOBALS.values()}
    assert gc.apply_speaker(None, emit=lambda *a, **k: None) is None
    assert {g: getattr(gt, g) for g in gt._CUT_GLOBALS.values()} == before


def test_apply_speaker_sets_globals(tmp_path, monkeypatch):
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(tmp_path))
    speakers.save("Тихий", {"cut": {"min_keep": 0.3, "db_auto": True}})
    prof = gc.apply_speaker("Тихий", emit=lambda *a, **k: None)
    assert prof is not None
    assert gt.MIN_KEEP == 0.3 and gt.DB_AUTO is True
    assert gt.MIN_ISLAND == speakers.CUT_DEFAULTS["min_island"]   # не тронуто


def test_defaults_reach_functions_after_apply(tmp_path, monkeypatch):
    """Пороги, которые раньше были дефолтами аргументов: они связывались при
    импорте, и профиль до них не доставал. Проверяем сквозь вызов функции."""
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(tmp_path))
    speakers.save("Пауза", {"cut": {"silence_sec": 0.3}})
    words = [{"w": "раз", "start": 0.0, "end": 0.3},
             {"w": "два", "start": 0.8, "end": 1.1}]      # пауза 0.5с
    assert gc._silence_bounds(words) == set()             # 0.5 < 0.8 по умолчанию
    gc.apply_speaker("Пауза", emit=lambda *a, **k: None)
    assert gc._silence_bounds(words) == {0}               # 0.5 > 0.3 из профиля


def test_autotune_reproduces_baseline_thresholds():
    """Формула автопорогов на базовом запасе спикера A (речь 39.5 дБ над полом)
    обязана вернуть примерно текущие 20/12 — иначе она не совместима с тем, на
    чём калибровались все остальные числа."""
    floor = -55.0
    db = np.concatenate([np.full(400, floor), np.full(600, floor + 39.5)])
    gt.DB_AUTO, gt._DB_TUNED = True, False
    gt.SNAP_DB, gt.ONSET_DB = 12.0, 20.0
    gc._autotune_db(db, floor, emit=lambda *a, **k: None)
    assert gt.SNAP_DB == pytest.approx(11.85, abs=0.3)
    assert gt.ONSET_DB == pytest.approx(19.75, abs=0.3)


def test_autotune_lowers_thresholds_for_quiet_speaker():
    """Запас спикера C (≈29 дБ) — пороги должны опуститься, иначе «голос»
    проходит выше половины его слов."""
    floor = -49.0
    db = np.concatenate([np.full(400, floor), np.full(600, floor + 29.0)])
    gt.DB_AUTO, gt._DB_TUNED = True, False
    gt.SNAP_DB, gt.ONSET_DB = 12.0, 20.0
    gc._autotune_db(db, floor, emit=lambda *a, **k: None)
    assert gt.ONSET_DB < 16.0 and gt.SNAP_DB < 10.0


def test_autotune_off_by_default():
    floor = -49.0
    db = np.concatenate([np.full(400, floor), np.full(600, floor + 29.0)])
    gt.DB_AUTO, gt._DB_TUNED = False, False
    gt.SNAP_DB, gt.ONSET_DB = 12.0, 20.0
    gc._autotune_db(db, floor, emit=lambda *a, **k: None)
    assert (gt.SNAP_DB, gt.ONSET_DB) == (12.0, 20.0)


def test_autotune_survives_silent_clip():
    """Тишина/битый звук: медианы речи нет — оставляем жёсткие пороги, а не
    считаем от мусора."""
    floor = -60.0
    db = np.full(1000, floor)
    gt.DB_AUTO, gt._DB_TUNED = True, False
    gt.SNAP_DB, gt.ONSET_DB = 12.0, 20.0
    gc._autotune_db(db, floor, emit=lambda *a, **k: None)
    assert (gt.SNAP_DB, gt.ONSET_DB) == (12.0, 20.0)


def test_hint_reaches_decide_prompt(tmp_path, monkeypatch):
    """Поправка спикера (`hint`) должна доезжать до системного промпта решения —
    для спикера B это единственный рычаг, который подтверждается его данными."""
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(tmp_path))
    speakers.save("Болтун", {"hint": "выкидывай реплики оператору"})
    assert "выкидывай" not in gc._sys(gc.DECIDE_MARKUP_SYS)
    gc.apply_speaker("Болтун", emit=lambda *a, **k: None)
    for base in (gc.DECIDE_MARKUP_SYS,):
        out = gc._sys(base)
        assert out.startswith(base) and "выкидывай реплики оператору" in out


def test_hint_absent_leaves_prompt_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(tmp_path))
    speakers.save("Молчун", {"cut": {"min_keep": 0.4}})
    gc.apply_speaker("Молчун", emit=lambda *a, **k: None)
    assert gc._sys(gc.DECIDE_MARKUP_SYS) == gc.DECIDE_MARKUP_SYS


def test_breath_thresholds_are_personal(tmp_path, monkeypatch):
    """Порог детектора вздохов берётся из профиля. Личным должен быть именно он:
    общий 0.95 у спикера A берёт 18 вздохов из 151, его 0.88 — 31 при той же
    точности."""
    from core import breath
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(tmp_path))
    speakers.save("Дышит", {"breath_p_cut": 0.88, "breath_p_mark": 0.4})
    gc.apply_speaker("Дышит", emit=lambda *a, **k: None)
    assert float(gt.SPEAKER["breath_p_cut"]) == 0.88 != breath.P_CUT
    speakers.save("Обычный", {})
    gc.apply_speaker("Обычный", emit=lambda *a, **k: None)
    assert float((gt.SPEAKER or {}).get("breath_p_cut") or breath.P_CUT) == breath.P_CUT


def test_breath_model_path_falls_back(tmp_path, monkeypatch):
    """Личной модели вздохов нет — работаем на общей, а не молча без детектора."""
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(tmp_path))
    speakers.save("Безмодельный", {"breath_model": "нет_такого_файла.json"})
    gc.apply_speaker("Безмодельный", emit=lambda *a, **k: None)
    assert gc.breath_model_path(emit=lambda *a, **k: None) is None
    speakers.save("Пустой", {"breath_model": ""})
    gc.apply_speaker("Пустой", emit=lambda *a, **k: None)
    assert gc.breath_model_path(emit=lambda *a, **k: None) is None


def test_shipped_profiles_are_valid():
    """Профили в репозитории: только известные пороги, стиль существует."""
    from core import styles
    known = set(styles.all_styles())
    for key, prof in speakers.all_speakers().items():
        unknown = set(prof.get("cut") or {}) - set(speakers.CUT_DEFAULTS)
        assert not unknown, f"{key}: неизвестные пороги {unknown}"
        if prof.get("style"):
            assert prof["style"] in known, f"{key}: нет стиля {prof['style']}"
