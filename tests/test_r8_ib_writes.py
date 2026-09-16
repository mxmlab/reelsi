# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание IB (круг 8), п. 2: данные и файлы пользователя пишутся атомарно.

`open(..., "w")` усекает файл ДО записи: «Стоп», крах или отбой питания в этот
момент оставляют пустой (или обрезанный) файл на месте живого, а пресеты стилей,
профили спикеров, `.jsx`/`.srt`/XML пользователя ниоткуда не пересобираются.
Проверяем поведением: сбой на подмене файла (`os.replace`) — старый файл цел;
и сторожем по исходникам — прямых записей в перечисленных местах не осталось.

Запуск: py -3.10 -m pytest tests/test_r8_ib_writes.py -q -p no:cacheprovider
"""
import gzip
import json
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from core import align, fileio, omni_cut, speakers, styles, subs, subtitle_xml  # noqa: E402

# Прямые записи, которые задание IB переводит на core.fileio (файл: запрещённый текст).
FORBIDDEN_WRITES = {
    "core/styles.py": ['open(p, "w", encoding="utf-8")', 'open(target, "w", encoding="utf-8")'],
    "core/speakers.py": ['open(path, "w", encoding="utf-8", newline="\\r\\n")'],
    "core/omni_cut.py": ['open(HALLUC_PHRASES_PATH, "w", encoding="utf-8")'],
    "core/xml2ae/build.py": ['open(jsx_path, "w", encoding="utf-8-sig")',
                             'open(out_jsx, "w", encoding="utf-8-sig")',
                             'open(master_path, "w", encoding="utf-8-sig")'],
    "core/subtitle_xml.py": ['open(out_xml, "w", encoding="UTF-8")'],
    "core/subs.py": ['open(srt_path, "w", encoding="utf-8")'],
    "core/align.py": ['open(path, "w", encoding="utf-8")'],
}


def _no_emit(*a, **k):
    pass


def _boom_replace(monkeypatch):
    """Сбой на подмене файла: ровно та точка, где усечённый файл становится виден."""
    def boom(*a, **k):
        raise OSError("сбой на подмене файла")
    monkeypatch.setattr(fileio.os, "replace", boom)


def _leftovers(d):
    return [p.name for p in Path(d).iterdir() if ".tmp." in p.name]


def test_прямых_записей_в_перечисленных_местах_не_осталось():
    """Сторож класса: данные пользователя в этих модулях идут только через core.fileio.
    Новое прямое `open(..., "w")` здесь — падение (как в test_gz_atomic_writes)."""
    bad = []
    for rel, needles in FORBIDDEN_WRITES.items():
        src = (ROOT / rel).read_text(encoding="utf-8")
        for needle in needles:
            if needle in src:
                bad.append(f"{rel}: {needle}")
    assert not bad, "прямая запись данных пользователя:\n  " + "\n  ".join(bad)


def test_миграция_пресета_стиля_не_усекает_файл(tmp_path, monkeypatch):
    """`_files()` переписывает старый пресет под layer_order. Запись под try/except —
    сбой на подмене файла раньше оставлял пресет усечённым МОЛЧА."""
    monkeypatch.setattr(styles, "STYLE_DIR", str(tmp_path))
    p = tmp_path / "старый.json"
    old = {"insert_above_subs": True, "label": "старый"}
    p.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")

    _boom_replace(monkeypatch)
    styles._files()
    assert json.loads(p.read_text(encoding="utf-8")) == old, "пресет пользователя усечён"
    assert not _leftovers(tmp_path), "временный файл остался"


def test_точечная_правка_пресета_не_усекает_файл(tmp_path, monkeypatch):
    """`patch()` (правка стиля из UI) писал пресет напрямую."""
    monkeypatch.setattr(styles, "STYLE_DIR", str(tmp_path))
    _, path = styles.save("мой", {"hl_on": True})
    before = Path(path).read_bytes()

    _boom_replace(monkeypatch)
    with pytest.raises(OSError):
        styles.patch("мой", {"hl_on": False})
    assert Path(path).read_bytes() == before, "пресет пользователя усечён"
    assert not _leftovers(tmp_path), "временный файл остался"


def test_профиль_спикера_пишется_crlf_и_не_усекается(tmp_path, monkeypatch):
    """Профиль спикера — тот же файл пользователя, но с переводами строк CRLF:
    атомарная запись обязана сохранить и содержимое, и байты переводов строк."""
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(tmp_path))
    _, path = speakers.save("Студия", {"cut": {}, "folder": "/x"})
    before = Path(path).read_bytes()
    assert b"\r\n" in before, "переводы строк перестали быть CRLF"
    assert b"\n" not in before.replace(b"\r\n", b""), "появился голый LF"
    assert json.loads(before.decode("utf-8"))["folder"] == "/x"

    _boom_replace(monkeypatch)
    with pytest.raises(OSError):
        speakers.save("Студия", {"cut": {}, "folder": "/y"})
    assert Path(path).read_bytes() == before, "профиль спикера усечён"


def test_выученные_фразы_не_усекаются(tmp_path, monkeypatch):
    """`_learn_halluc` дописывает фразы-галлюцинации в файл рядом с копией."""
    p = tmp_path / "halluc_phrases.json"
    p.write_text('["старая фраза"]', encoding="utf-8")
    monkeypatch.setattr(omni_cut, "HALLUC_PHRASES_PATH", str(p))

    _boom_replace(monkeypatch)
    with pytest.raises(OSError):
        omni_cut._learn_halluc(["новая фраза"])
    assert json.loads(p.read_text(encoding="utf-8")) == ["старая фраза"]
    assert not _leftovers(tmp_path), "временный файл остался"


def test_srt_плана_сцены_не_усекается(tmp_path, monkeypatch):
    """`subs.write_srt` — .srt рядом с `.jsx` (план сцены AE)."""
    p = tmp_path / "a.srt"
    p.write_text("СТАРОЕ\n", encoding="utf-8")

    _boom_replace(monkeypatch)
    with pytest.raises(OSError):
        subs.write_srt([{"s": 0.0, "e": 1.0, "w": "привет"}], str(p))
    assert p.read_text(encoding="utf-8") == "СТАРОЕ\n"


def test_srt_премьеры_не_усекается(tmp_path, monkeypatch):
    """`align.make_srt` — .srt для дорожки субтитров Premiere."""
    p = tmp_path / "b.srt"
    p.write_text("СТАРОЕ\n", encoding="utf-8")

    _boom_replace(monkeypatch)
    with pytest.raises(OSError):
        align.make_srt([{"w": "привет", "start": 0, "end": 30}], str(p))
    assert p.read_text(encoding="utf-8") == "СТАРОЕ\n"


def test_xml_с_дорожкой_субтитров_не_усекается(tmp_path, monkeypatch):
    """`subtitle_xml.add_subtitles` пишет НОВЫЙ XML — самый дорогой артефакт шага."""
    from core import transcribe

    monkeypatch.setattr(transcribe, "load_words_cache",
                        lambda path: [{"w": "привет", "start": 2.9, "end": 3.1}])
    monkeypatch.setattr(transcribe, "words_cache_path", lambda src, **kw: "нет-кэша.json")
    xml = tmp_path / "edited.xml"
    shutil.copyfile(HERE / "fixtures" / "timeline_nosubs.xml", xml)
    out = tmp_path / "out.xml"
    out.write_text("СТАРОЕ", encoding="utf-8")

    _boom_replace(monkeypatch)
    with pytest.raises(OSError):
        subtitle_xml.add_subtitles(str(xml), str(out), emit=_no_emit)
    assert out.read_text(encoding="utf-8") == "СТАРОЕ", "готовый XML усечён"


def test_мастер_скрипт_набора_не_усекается(tmp_path, monkeypatch):
    """`_write_master` — .jsx мастера рендера набора (utf-8-sig, BOM обязателен)."""
    from core.xml2ae.build import _write_master

    master = tmp_path / "render_master.jsx"
    master.write_text("СТАРОЕ", encoding="utf-8")

    _boom_replace(monkeypatch)
    with pytest.raises(OSError):
        _write_master([str(tmp_path / "a.jsx")], str(master), str(tmp_path / "p.aep"),
                      str(tmp_path / "exp"))
    assert master.read_text(encoding="utf-8") == "СТАРОЕ"


@pytest.fixture()
def xml_subs(tmp_path):
    dst = tmp_path / "timeline.xml"
    with gzip.open(HERE / "fixtures" / "timeline_subs.xml.gz", "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return str(dst)


def test_сборка_jsx_не_усекается(xml_subs, tmp_path, monkeypatch):
    """`to_ae_full` — одиночный `.jsx` таймлайна."""
    from core import xml2ae

    out = tmp_path / "out.jsx"
    out.write_text("СТАРОЕ", encoding="utf-8")

    _boom_replace(monkeypatch)
    with pytest.raises(OSError):
        xml2ae.to_ae_full(xml_subs, jsx_path=str(out), inserts=[],
                          style={"intro_riser": False}, disclaimer="", emit=_no_emit)
    assert out.read_text(encoding="utf-8") == "СТАРОЕ", "собранный .jsx усечён"


def test_сборка_одного_jsx_на_всё_не_усекается(xml_subs, tmp_path, monkeypatch):
    """`build_combined` — «один .jsx на всё»."""
    from core import xml2ae

    out = tmp_path / "combined.jsx"
    out.write_text("СТАРОЕ", encoding="utf-8")

    _boom_replace(monkeypatch)
    with pytest.raises(OSError):
        xml2ae.build_combined([{"xml_path": xml_subs, "inserts": [],
                                "style": {"intro_riser": False}, "disclaimer": ""},
                               {"xml_path": xml_subs, "inserts": [],
                                "style": {"intro_riser": False}, "disclaimer": ""}],
                              str(out), emit=_no_emit)
    assert out.read_text(encoding="utf-8") == "СТАРОЕ", "склейка усечена"
