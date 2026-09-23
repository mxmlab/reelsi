# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Контракты на СТЫКАХ модулей: api/ <-> xml2ae/ <-> xmlbuild.py <-> insertlib.py.

Внутри модулей всё покрыто своими тестами; ломается обычно между ними — там, где
один модуль молча предполагает что-то про другой. Здесь только такие места, и
каждое — из реального бага (см. «Подводные камни» в ARCHITECTURE.md).

Тяжёлое (`xmlbuild.build`, ffmpeg, GPU) подменяется: проверяем НЕ то, что сборка
работает, а то, что на неё уходит.

Запуск: python -m pytest reelsi/tests -q
"""
import gzip
import json
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault("REELSI_NO_BROWSER", "1")

from core import xml2ae  # noqa: E402


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


@pytest.fixture()
def client():
    import webui
    webui.app.config["TESTING"] = True
    return webui.app.test_client()


# ------------------------------------------- раскладка камер не трогает разметку

def test_cams_save_carries_subs_and_yellow_into_rebuild(client, xml_subs, monkeypatch):
    """Раскладка камер меняет только то, ЧЬЯ картинка видна: слова и тайминги те же.

    Регрессия 2026-07-22: пересборка шла БЕЗ `sub_words`, и сохранение раскладки
    стирало субтитры вместе с жёлтыми. Контракт: субтитр-графика и жёлтые
    вычитываются ДО пересборки и вписываются обратно.
    """
    import api
    from core import xmlbuild

    # жёлтые, которые обязаны пережить пересборку
    xml2ae.set_highlights(xml_subs, [4, 9])
    _m, _c, subs_before, _i = xml2ae.parse_full(xml_subs)

    seen = {}

    def fake_build(cams, keep, offsets, out, **kw):
        seen["sub_words"] = kw.get("sub_words")
        seen["assign"] = kw.get("assign")
        return {"total_s": 12.3}

    monkeypatch.setattr(xmlbuild, "build", fake_build)

    proj = api._ensure_project(xml_subs)
    nseg = len(proj.get("keep") or [])
    assert nseg, "проект из XML не реконструировался — тест бессмысленен"

    d = client.post("/api/cams_save",
                    json={"xml": xml_subs,
                          "assign": [i % max(len(proj["cams"]), 1) for i in range(nseg)]}).get_json()
    assert d.get("ok"), d

    # главное: слова уехали в пересборку, а не потерялись
    assert seen["sub_words"], "sub_words не переданы в build — субтитры будут стёрты"
    assert len(seen["sub_words"]) == len(subs_before)
    assert seen["sub_words"][0]["w"] == subs_before[0][2]
    # и жёлтые вписаны обратно
    assert d["yellow"] == 2, d
    assert sorted(xml2ae.auto_highlights(xml_subs).get("yellow") or []) == [4, 9]


def test_editor_save_carries_subs_and_yellow_into_rebuild(client, xml_subs, monkeypatch):
    """Правка блоков нарезки не стирает субтитр-графику и жёлтые.

    Регрессия 2026-08-13 (аудит): api_editor_save пересобирал XML с sub_words=None,
    и правка блоков ПОСЛЕ генерации субтитров молча стирала субтитры вместе с
    жёлтыми. Контракт как у cams_save: слова/жёлтые вычитываются ДО пересборки.
    """
    import api
    from core import xmlbuild

    xml2ae.set_highlights(xml_subs, [4, 9])
    _m, _c, subs_before, _i = xml2ae.parse_full(xml_subs)

    seen = {}

    def fake_build(cams, keep, offsets, out, **kw):
        seen["sub_words"] = kw.get("sub_words")
        seen["assign"] = kw.get("assign")
        return {"total_s": 12.3}

    monkeypatch.setattr(xmlbuild, "build", fake_build)

    proj = api._ensure_project(xml_subs)
    nseg = len(proj.get("keep") or [])
    assert nseg, "проект из XML не реконструировался — тест бессмысленен"

    d = client.post("/api/editor_save",
                    json={"xml": xml_subs, "keep": proj["keep"]}).get_json()
    assert d.get("ok"), d

    # главное: слова уехали в пересборку, а не потерялись
    assert seen["sub_words"], "sub_words не переданы в build — субтитры будут стёрты"
    assert len(seen["sub_words"]) == len(subs_before)
    assert seen["sub_words"][0]["w"] == subs_before[0][2]
    # жёлтые не тронуты
    assert sorted(xml2ae.auto_highlights(xml_subs).get("yellow") or []) == [4, 9]


def test_editor_save_reprojects_subs_when_blocks_change(client, xml_subs, monkeypatch):
    """Удалили блок — слова едут вместе с картинкой, а не остаются на старых кадрах.

    Регрессия 2026-08-13 (продолжение): слова-субтитры лежат в кадрах ТАЙМЛАЙНА, а
    таймлайн собирается курсором по кускам. Перенос прежних sub_words «как есть» давал
    рассинхрон ровно в длину удалённого блока и оставлял слова из выброшенных кусков
    подписями к чужой речи. Контракт: кадр таймлайна -> исходник -> новый таймлайн.
    """
    import api
    from core import xmlbuild
    FPS = xmlbuild.FPS

    _m, _c, subs_before, _i = xml2ae.parse_full(xml_subs)
    proj = api._ensure_project(xml_subs)
    keep = [(float(s), float(e)) for s, e in proj["keep"]]
    assert len(keep) >= 2 and subs_before, "фикстура не годится для этой проверки"

    dropped = round(keep[0][1] * FPS) - round(keep[0][0] * FPS)   # длина блока в кадрах
    in_drop = [r for r in subs_before if r[0] < dropped]
    rest = [r for r in subs_before if r[0] >= dropped]
    assert in_drop and rest, "в первом блоке нет слов — проверка ничего не докажет"

    seen = {}

    def fake_build(cams, keep_arg, offsets, out, **kw):
        seen["sub_words"] = kw.get("sub_words")
        return {"total_s": 12.3}

    monkeypatch.setattr(xmlbuild, "build", fake_build)

    d = client.post("/api/editor_save",
                    json={"xml": xml_subs, "keep": [list(k) for k in keep[1:]]}).get_json()
    assert d.get("ok"), d
    got = seen["sub_words"]
    assert got, "sub_words не переданы в build"

    # слова удалённого блока выброшены, остальные съехали ровно на его длину
    assert len(got) == len(rest), f"ожидалось {len(rest)} слов, пришло {len(got)}"
    for old, new in zip(rest, got):
        assert new["w"] == old[2]
        assert new["start"] == old[0] - dropped, (
            f"«{old[2]}» стоит на {new['start']}, а картинка на {old[0] - dropped}")
    # и ничего не вылезло за укоротившийся таймлайн
    total = sum(round(e * FPS) - round(s * FPS) for s, e in keep[1:])
    assert max(w["end"] for w in got) <= total


def test_reproject_subs_remaps_yellow_indices():
    """Жёлтые — позиции в списке слов: выбросили слова — индексы обязаны съехать."""
    from api.editor import _reproject_subs
    FPS = 60
    old_keep = [(0.0, 1.0), (10.0, 11.0)]              # два блока по 60 кадров
    new_keep = [(10.0, 11.0)]                          # первый удалён
    words = [{"w": "первое", "start": 5, "end": 20},    # в удалённом блоке
             {"w": "второе", "start": 65, "end": 80},   # во втором: 60 + 5
             {"w": "третье", "start": 85, "end": 100}]
    got, yellow = _reproject_subs(words, [0, 2], old_keep, new_keep, FPS)
    assert [w["w"] for w in got] == ["второе", "третье"]
    assert got[0]["start"] == 5 and got[0]["end"] == 20   # уехало в начало таймлайна
    assert yellow == [1], "жёлтое слово 2 стало словом 1, слово 0 удалено вместе с блоком"


def test_cams_save_rejects_desync(client, xml_subs, monkeypatch):
    """Раскладка не той длины — понятная ошибка, а не молчаливая пересборка мусора."""
    import api
    from core import xmlbuild
    monkeypatch.setattr(xmlbuild, "build", lambda *a, **k: {"total_s": 0})
    api._ensure_project(xml_subs)
    d = client.post("/api/cams_save", json={"xml": xml_subs, "assign": [0]}).get_json()
    assert "error" in d and "Рассинхрон" in d["error"]


# ------------------------------------------------- webp/avif до сборки .jsx

def _webp(tmp_path, name="pic.webp", alpha=True):
    Image = pytest.importorskip("PIL.Image", reason="нет Pillow")
    p = tmp_path / name
    Image.new("RGBA" if alpha else "RGB", (12, 12), (255, 0, 0, 128 if alpha else 255)).save(p)
    return str(p)


def test_webp_insert_is_converted_before_it_reaches_ae(tmp_path, monkeypatch):
    """AE не импортирует webp вовсе: .jsx собирается, а слоя в композиции нет.
    Поэтому конвертация стоит в `_adopt_inserts` — на ВСЕХ трёх путях сборки."""
    import api
    from core import insertlib
    lib = tmp_path / "lib"
    lib.mkdir()
    monkeypatch.setattr(insertlib, "INDEX_PATH", str(tmp_path / "idx.json"))
    json.dump({"items": [], "dirs": [], "emb_model": ""},
              open(insertlib.INDEX_PATH, "w", encoding="utf-8"))
    insertlib._CACHE["data"] = None

    ins = [{"media": _webp(tmp_path), "query": "broken eyeglasses"}]
    api._adopt_inserts(ins, str(lib))

    assert ins[0]["media"].lower().endswith(".png"), ins[0]["media"]
    assert os.path.exists(ins[0]["media"])


def test_webp_insert_is_converted_in_render_prep(tmp_path):
    """Рендер строит .jsx напрямую через to_ae_full, минуя _adopt_inserts: без
    `_convert_inserts` в _run_render_job webp-вставка дошла бы до AE и уронила
    предполёт. Перекодировка кладёт .png РЯДОМ — исходник цел."""
    from api.inserts import _convert_inserts

    ins = [{"media": _webp(tmp_path), "query": "broken eyeglasses"}]
    _convert_inserts(ins)

    assert ins[0]["media"].lower().endswith(".png"), ins[0]["media"]
    assert os.path.exists(ins[0]["media"])


def test_webp_alpha_survives_conversion(tmp_path):
    """Прозрачность — смысл вставки. Потерять её при перекодировке = белый прямоугольник."""
    from core import insertlib
    Image = pytest.importorskip("PIL.Image", reason="нет Pillow")
    png = insertlib.to_ae_image(_webp(tmp_path))
    with Image.open(png) as im:
        assert im.mode == "RGBA"


def test_supported_formats_are_not_touched(tmp_path):
    """Конвертация не бесплатна — png/jpg возвращаются как есть, без лишней копии."""
    from core import insertlib
    Image = pytest.importorskip("PIL.Image", reason="нет Pillow")
    p = tmp_path / "ok.png"
    Image.new("RGB", (8, 8), "blue").save(p)
    assert insertlib.to_ae_image(str(p)) == os.path.abspath(str(p))


def test_jpeg_named_as_png_is_reencoded_to_png(tmp_path):
    """JPEG под именем .png (2026-09-11, restore-energy-2b968533.png): After Effects
    выбирает импортёр по расширению и падает (5027 :: 12). to_ae_image перекодирует в
    <имя>-png.png, исходник остаётся нетронутым."""
    from core import insertlib
    Image = pytest.importorskip("PIL.Image", reason="нет Pillow")
    src = tmp_path / "x.png"
    Image.new("RGB", (10, 10), "red").save(str(src), "JPEG")
    src_bytes = src.read_bytes()
    assert src_bytes.startswith(b"\xff\xd8\xff")

    dst = insertlib.to_ae_image(str(src))
    assert dst.endswith("-png.png")
    assert os.path.exists(dst)
    with open(dst, "rb") as f:
        assert f.read().startswith(b"\x89PNG")
    assert src.read_bytes() == src_bytes

    ok_png = tmp_path / "real.png"
    Image.new("RGB", (10, 10), "green").save(str(ok_png), "PNG")
    assert insertlib.to_ae_image(str(ok_png)) == os.path.abspath(str(ok_png))


def _cmyk_jpg(tmp_path, name="stock.jpg"):
    Image = pytest.importorskip("PIL.Image", reason="нет Pillow")
    p = tmp_path / name
    Image.new("CMYK", (12, 12), (0, 40, 80, 10)).save(p)
    return str(p)


def test_cmyk_jpeg_insert_is_converted_before_it_reaches_ae(tmp_path, monkeypatch):
    """CMYK-JPEG со стока (2026-07-27, 09_ng18.jsx): расширение обычное, а importFile
    падает с «Unsupported video bit depth» и роняет ВЕСЬ .jsx на первом же импорте —
    не один слой, а весь проект. Значит смотреть надо в файл, а не на расширение."""
    import api
    from core import insertlib
    lib = tmp_path / "lib"
    lib.mkdir()
    monkeypatch.setattr(insertlib, "INDEX_PATH", str(tmp_path / "idx.json"))
    json.dump({"items": [], "dirs": [], "emb_model": ""},
              open(insertlib.INDEX_PATH, "w", encoding="utf-8"))
    insertlib._CACHE["data"] = None

    ins = [{"media": _cmyk_jpg(tmp_path), "query": "stage I melanoma"}]
    api._adopt_inserts(ins, str(lib))

    Image = pytest.importorskip("PIL.Image", reason="нет Pillow")
    assert os.path.exists(ins[0]["media"]), ins[0]["media"]
    with Image.open(ins[0]["media"]) as im:
        assert im.mode in ("RGB", "RGBA"), im.mode


def test_verifier_catches_cmyk_insert(tmp_path):
    """Тот же CMYK, но со стороны verify_jsx: ловим до AE, а не после 40 минут рото."""
    from core import verify_jsx
    rep = verify_jsx.Report("t.jsx")
    verify_jsx._media_problem(_cmyk_jpg(tmp_path), rep, "INSERTS[0]")
    assert rep.errors and "CMYK" in rep.errors[0]


def _vid(tmp_path, name, encoder):
    """Крошечный ролик заданным энкодером. Нет ffmpeg (или энкодера) — тест пропускаем."""
    import shutil, subprocess
    if not shutil.which("ffmpeg"):
        pytest.skip("нет ffmpeg")
    p = tmp_path / name
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "testsrc=size=64x64:rate=10:duration=0.5",
                        "-c:v", encoder, "-pix_fmt", "yuv420p", str(p)],
                       capture_output=True, text=True, timeout=300)
    if r.returncode or not p.exists():
        pytest.skip("ffmpeg не собрал %s: %s" % (encoder, (r.stderr or "")[:120]))
    return str(p)


def test_av1_insert_is_converted_before_it_reaches_ae(tmp_path, monkeypatch):
    """AV1 (типовой mp4 «videoplayback» с YouTube) роняет importFile с «The source
    compression type is not supported» и обрывает ВЕСЬ .jsx — как CMYK-JPEG у картинок.
    Контейнер обычный, значит смотреть надо в поток (2026-08-01, AutoCut_all.jsx)."""
    import api
    from core import insertlib
    src = _vid(tmp_path, "videoplayback (12).mp4", "libsvtav1")
    lib = tmp_path / "lib"
    lib.mkdir()
    monkeypatch.setattr(insertlib, "INDEX_PATH", str(tmp_path / "idx.json"))
    json.dump({"items": [], "dirs": [], "emb_model": ""},
              open(insertlib.INDEX_PATH, "w", encoding="utf-8"))
    insertlib._CACHE["data"] = None

    ins = [{"media": src, "query": "mugs on table"}]
    api._adopt_inserts(ins, str(lib))

    assert os.path.exists(ins[0]["media"]), ins[0]["media"]
    assert insertlib._vcodec(ins[0]["media"]) == "h264"


def test_h264_video_is_not_touched(tmp_path):
    """Перекодировка видео дорогая — читаемое AE возвращаем как есть, без копии."""
    from core import insertlib
    p = _vid(tmp_path, "ok.mp4", "libx264")
    assert insertlib.to_ae_video(p) == os.path.abspath(p)


def test_verifier_catches_av1_insert(tmp_path):
    """Тот же AV1, но со стороны verify_jsx: ловим до AE, а не диалогом на 686-й строке."""
    from core import verify_jsx
    src = _vid(tmp_path, "videoplayback (13).mp4", "libsvtav1")
    rep = verify_jsx.Report("t.jsx")
    verify_jsx._media_problem(src, rep, "INSERTS[0]")
    assert rep.errors and "AV1" in rep.errors[0]


# --------------------------------------------- .jsx и XML не должны расходиться

def test_verifier_agrees_with_parse_full(xml_subs):
    """Сверка .jsx с исходником опирается на parse_full — держим контракт явным:
    `subs` = [(start, end, word)], и число слов = число слоёв в компе субтитров."""
    _meta, cams, subs, ins = xml2ae.parse_full(xml_subs)
    assert len(cams) == 2 and len(subs) == 253 and ins == []
    assert all(len(s) == 3 and s[0] < s[1] and isinstance(s[2], str) for s in subs)
