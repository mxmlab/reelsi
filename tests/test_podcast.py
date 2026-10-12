# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты core/podcast.py: имена папок, проект подкаста, дорожки и команды yt-dlp.

Без сети и без запуска yt-dlp: ответ `yt-dlp -J` берётся из обезличенной фикстуры
tests/fixtures/ytdlp_multiaudio.json (урезанный реальный ответ).
"""
import json
import os
import re
import sys

import pytest

from core import podcast
from core.umsg import ReelsiError

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "ytdlp_multiaudio.json")
URL = "https://www.youtube.com/watch?v=abcDEF12345"


@pytest.fixture
def ytinfo():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def _seg(sid, start, end):
    return {"id": sid, "start": start, "end": end, "title": "t", "why": "w", "reel": ""}


# --- slugify и проект ---

def test_slugify_keeps_letters_and_collapses_punct():
    assert podcast.slugify("Выпуск #12: Деньги/Время") == "Выпуск_12_Деньги_Время"


def test_slugify_latin_keeps_case():
    assert podcast.slugify("  Hello,   World!! ") == "Hello_World"


def test_slugify_empty_is_podcast():
    assert podcast.slugify("") == "podcast"
    assert podcast.slugify("!!! ###") == "podcast"


def test_slugify_length_limit():
    s = podcast.slugify("а" * 200)
    assert len(s) <= 60
    assert s == "а" * 60


def test_slugify_no_edge_underscore_after_cut():
    # обрезка на 60 не должна оставить «_» на конце
    s = podcast.slugify("a" * 59 + " bcd")
    assert len(s) <= 60
    assert not s.endswith("_")


def test_project_path_and_new_project_schema():
    assert podcast.project_path("x") == os.path.join("x", "podcast.json")
    p = podcast.new_project("Выпуск 1", URL, "C:/s/source.mkv", 5)
    assert p["v"] == 1
    assert p["status"] == "new"
    assert p["segments"] == []
    assert p["error"] == ""
    assert p["audio_track"] is None
    assert p["duration"] == 0.0
    assert p["asr_engine"] == "" and p["lang"] == ""
    assert p["slug"] == "Выпуск_1"
    assert p["reels_wanted"] == 5


def test_new_segment_id_is_6_hex():
    ids = {podcast.new_segment_id() for _ in range(50)}
    assert all(re.fullmatch(r"[0-9a-f]{6}", i) for i in ids)
    assert len(ids) > 1


def test_load_project_missing_and_bad(tmp_path):
    assert podcast.load_project(str(tmp_path)) is None
    (tmp_path / "podcast.json").write_text("[1, 2]", encoding="utf-8")
    assert podcast.load_project(str(tmp_path)) is None
    (tmp_path / "podcast.json").write_text('{"v": 2, "segments": []}', encoding="utf-8")
    assert podcast.load_project(str(tmp_path)) is None
    (tmp_path / "podcast.json").write_text("{битый", encoding="utf-8")
    assert podcast.load_project(str(tmp_path)) is None


def test_save_and_load_roundtrip(tmp_path):
    folder = str(tmp_path / "vypusk")
    proj = podcast.new_project("Выпуск", URL, "C:/s/source.mkv", 2)
    proj["duration"] = 100.0
    proj["segments"] = [_seg("a1b2c3", 10.0, 40.0), _seg("d4e5f6", 40.0, 70.0)]
    podcast.save_project(folder, proj)
    assert podcast.load_project(folder) == proj


def test_save_bad_segments_raises_and_writes_nothing(tmp_path):
    folder = str(tmp_path / "bad")
    proj = podcast.new_project("Плохой", URL, "x.mkv", 2)
    proj["duration"] = 100.0
    proj["segments"] = [_seg("a1", 10.0, 50.0), _seg("b2", 40.0, 60.0)]
    with pytest.raises(ReelsiError):
        podcast.save_project(folder, proj)
    assert not os.path.exists(podcast.project_path(folder))


# --- validate_segments ---

def test_validate_ok_touching_is_allowed():
    segs = [_seg("a1", 0.0, 30.0), _seg("b2", 30.0, 60.0)]
    assert podcast.validate_segments(segs, 100.0) == []


def test_validate_overlap():
    errs = podcast.validate_segments([_seg("a1", 0.0, 30.0), _seg("b2", 20.0, 50.0)], 100.0)
    assert any("пересекается" in e for e in errs)


def test_validate_overlap_hidden_inside_long_one():
    # короткий кусок целиком внутри длинного: соседний по сортировке не поможет
    segs = [_seg("a1", 0.0, 100.0), _seg("b2", 10.0, 20.0), _seg("c3", 50.0, 60.0)]
    errs = podcast.validate_segments(segs, 200.0)
    assert len([e for e in errs if "пересекается" in e]) >= 2


def test_validate_start_not_less_than_end():
    errs = podcast.validate_segments([_seg("a1", 30.0, 30.0)], 100.0)
    assert any("начало < конец" in e for e in errs)
    errs = podcast.validate_segments([_seg("a1", -1.0, 5.0)], 100.0)
    assert errs


def test_validate_end_beyond_duration():
    errs = podcast.validate_segments([_seg("a1", 10.0, 120.0)], 100.0)
    assert any("дальше длины" in e for e in errs)
    # длина неизвестна (0) — проверку конца не делаем
    assert podcast.validate_segments([_seg("a1", 10.0, 120.0)], 0.0) == []


def test_validate_duplicate_id():
    errs = podcast.validate_segments([_seg("dup", 0.0, 10.0), _seg("dup", 20.0, 30.0)], 100.0)
    assert any("повторяется" in e for e in errs)


def test_validate_missing_id_and_non_numbers():
    assert podcast.validate_segments([{"start": 0, "end": 5}], 100.0)
    assert podcast.validate_segments([{"id": "x", "start": "1", "end": 5}], 100.0)
    assert podcast.validate_segments("не список", 100.0)


# --- YouTube и команды yt-dlp ---

@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=abcDEF12345",
    "http://youtube.com/watch?v=abcDEF12345&t=10",
    "https://m.youtube.com/watch?v=abcDEF12345",
    "https://youtu.be/abcDEF12345",
    "https://www.youtube.com/shorts/abcDEF12345",
    "https://www.youtube.com/live/abcDEF12345",
])
def test_is_youtube_accepts(url):
    assert podcast.is_youtube(url)


@pytest.mark.parametrize("url", [
    "",
    "https://example.com/watch?v=abcDEF12345",
    "https://youtube.com.evil.com/watch?v=abcDEF12345",
    "https://www.youtube.com/watch?v=",
    "-https://youtu.be/abcDEF12345",
    "ftp://youtu.be/abcDEF12345",
])
def test_is_youtube_rejects(url):
    assert not podcast.is_youtube(url)


def test_probe_cmd_shape():
    cmd = podcast.probe_cmd(URL)
    assert cmd == [sys.executable, "-m", "yt_dlp", "-J", "--no-playlist",
                   "--js-runtimes", "node", "--", URL]


def test_probe_cmd_refuses_dash_and_non_youtube():
    with pytest.raises(ValueError):
        podcast.probe_cmd("-x")
    with pytest.raises(ValueError):
        podcast.probe_cmd("https://example.com/video")


def test_download_cmd_selector_with_format_id(tmp_path):
    cmd = podcast.download_cmd(URL, str(tmp_path), "251-23")
    i = cmd.index("-f")
    assert cmd[i + 1] == "bv*+ba[format_id=251-23]/bv*+ba/b"
    assert cmd[cmd.index("--merge-output-format") + 1] == "mkv"
    assert cmd[-2:] == ["--", URL]


def test_download_cmd_selector_without_format_id(tmp_path):
    cmd = podcast.download_cmd(URL, str(tmp_path), None)
    assert cmd[cmd.index("-f") + 1] == "bv*+ba/b"


def test_download_cmd_parallel_fragments_before_separator(tmp_path):
    """Скорость YouTube: `-N 8 --http-chunk-size 10M` есть и стоят до `--` (иначе yt-dlp
    примет их за адрес). Без `-N 8` звуковая дорожка идёт 90 КиБ/с вместо 13.5 МиБ/с."""
    cmd = podcast.download_cmd(URL, str(tmp_path), "140-1")
    sep = cmd.index("--")
    i = cmd.index("-N")
    assert cmd[i + 1] == "8" and i < sep
    j = cmd.index("--http-chunk-size")
    assert cmd[j + 1] == "10M" and j < sep


def test_download_cmd_refuses_dash_in_url_dest_format():
    with pytest.raises(ValueError):
        podcast.download_cmd("-x", "d", None)
    with pytest.raises(ValueError):
        podcast.download_cmd(URL, "-x", None)
    with pytest.raises(ValueError):
        podcast.download_cmd(URL, "d", "-x")


def test_download_cmd_refuses_unsafe_format_id_and_non_youtube():
    with pytest.raises(ValueError):
        podcast.download_cmd(URL, "d", "251;rm")
    with pytest.raises(ValueError):
        podcast.download_cmd(URL, "d", "251 x")
    with pytest.raises(ValueError):
        podcast.download_cmd("https://example.com/v", "d", None)


# --- дорожки звука ---

def test_audio_tracks_fixture(ytinfo):
    tracks = podcast.audio_tracks(ytinfo)
    assert [t["lang"] for t in tracks] == ["en-US", "fr", "ru"]
    en, fr, ru = tracks
    # лучший по abr в en-US: 140-23 (129.475) против 251-23 (125.304)
    assert en["format_id"] == "140-23"
    assert en["original"] is True
    assert en["label"] == "English (US) original (default)"
    assert fr["format_id"] == "140-12" and fr["original"] is False
    assert ru["format_id"] == "140-14" and ru["original"] is False
    assert ru["label"] == "Russian"
    assert podcast.default_track(tracks)["lang"] == "en-US"


def test_audio_tracks_skips_hls(ytinfo):
    # m3u8-дорожки в фикстуре есть только видео; добавим звуковой m3u8 с большим abr —
    # он не должен попасть в выбор
    ytinfo["formats"].append({"format_id": "233-9", "vcodec": "none", "acodec": "mp4a",
                              "protocol": "m3u8_native", "language": "ru", "abr": 999,
                              "format_note": "Russian, medium", "language_preference": -1})
    tracks = podcast.audio_tracks(ytinfo)
    assert all(t["format_id"] != "233-9" for t in tracks)
    assert [t["format_id"] for t in tracks if t["lang"] == "ru"] == ["140-14"]


def test_audio_tracks_missing_language_is_empty_group():
    info = {"formats": [{"format_id": "251-0", "vcodec": "none", "acodec": "opus",
                         "protocol": "https", "language": None, "abr": 120,
                         "format_note": "medium", "language_preference": None}]}
    tracks = podcast.audio_tracks(info)
    assert len(tracks) == 1
    assert tracks[0]["lang"] == ""
    assert tracks[0]["original"] is False
    assert tracks[0]["label"] == "medium"


def test_audio_tracks_original_by_note_word_only():
    info = {"formats": [{"format_id": "1", "vcodec": "none", "acodec": "opus",
                         "protocol": "https", "language": "de", "abr": 100,
                         "format_note": "German ORIGINAL, medium", "language_preference": -1}]}
    assert podcast.audio_tracks(info)[0]["original"] is True


def test_default_track_fallbacks():
    assert podcast.default_track([]) is None
    assert podcast.default_track([{"lang": "a", "original": False}])["lang"] == "a"


# --- прогресс yt-dlp ---

def test_progress_fields_with_size_and_eta():
    line = "[download]  12.3% of ~ 1.20GiB at  3.10MiB/s ETA 06:12 (frag 3/120)"
    assert podcast.progress_fields(line) == {
        "pct": 12.3, "size": "~1.20GiB", "speed": "3.10MiB/s", "eta": "06:12"}


def test_progress_fields_finished():
    line = "[download] 100% of  512.00MiB in 00:02:11 at 3.89MiB/s"
    assert podcast.progress_fields(line) == {
        "pct": 100.0, "size": "512.00MiB", "speed": "3.89MiB/s", "eta": ""}


def test_progress_fields_merge_stage():
    line = '[Merger] Merging formats into "C:/x/source.mkv"'
    assert podcast.progress_fields(line) == {"stage": "merge"}


def test_progress_fields_garbage_is_none():
    assert podcast.progress_fields("WARNING: что-то непонятное") is None
    assert podcast.progress_fields("") is None


# --- скачанный файл ---

def test_downloaded_source_ignores_partials(tmp_path):
    (tmp_path / "source.f137.mp4").write_bytes(b"x")
    (tmp_path / "source.mkv.part").write_bytes(b"x")
    (tmp_path / "source.mkv").write_bytes(b"x")
    assert podcast.downloaded_source(str(tmp_path)) == os.path.join(str(tmp_path), "source.mkv")


def test_downloaded_source_none_when_only_partials(tmp_path):
    (tmp_path / "source.f137.mp4").write_bytes(b"x")
    (tmp_path / "source.mkv.part").write_bytes(b"x")
    assert podcast.downloaded_source(str(tmp_path)) is None
    assert podcast.downloaded_source(str(tmp_path / "нет")) is None


# --- дубляж (П16): поле dub у дорожки ---

def test_audio_tracks_dub_flag_fixture(ytinfo):
    """Фикстура: оригинал en-US; fr и ru — автодубляж без слова dubbed, но оригинал рядом есть."""
    by = {t["lang"]: t for t in podcast.audio_tracks(ytinfo)}
    assert by["en-US"]["dub"] is False and by["en-US"]["original"] is True
    assert by["fr"]["dub"] is True and by["fr"]["original"] is False
    assert by["ru"]["dub"] is True and by["ru"]["original"] is False


def test_audio_tracks_dub_by_dubbed_word_without_original(ytinfo):
    """Нет ни одного оригинала, но в note стоит dubbed — дубляж всё равно помечен."""
    for f in ytinfo["formats"]:
        f["language_preference"] = -1
        f["format_note"] = (f.get("format_note") or "").replace("original (default)", "").strip()
    for f in ytinfo["formats"]:
        if f.get("format_id") == "140-14":
            f["format_note"] = "Russian, dubbed"
    tracks = podcast.audio_tracks(ytinfo)
    assert all(t["original"] is False for t in tracks)
    ru = [t for t in tracks if t["lang"] == "ru"]
    assert ru and ru[0]["dub"] is True


def test_audio_tracks_no_original_no_word_is_not_dub(ytinfo):
    """Ни оригинала, ни слова dubbed: про дубляж ничего не известно — dub = False."""
    for f in ytinfo["formats"]:
        f["language_preference"] = -1
        f["format_note"] = (f.get("format_note") or "").replace("original (default)", "").strip()
    tracks = podcast.audio_tracks(ytinfo)
    assert tracks and all(t["dub"] is False for t in tracks)
