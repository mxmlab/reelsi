# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Субтитры нарезки по словам исходника: правка блоков на шаге 1 не теряет слова.

ПОЧЕМУ эти тесты существуют. При включённом втором движке текста субтитры пишутся в XML
сразу при нарезке — только для оставленных кусков. Правка блоков пересобирала XML по
субтитрам с текущего таймлайна: вернувшийся кусок приходил без слов (слова исходника
нигде не сохранялись). Теперь слова исходника лежат в `<stem>.srcwords.json`, и каждая
пересборка субтитров идёт через `core.cut_subs.subs_for_keep` — ровно как первая нарезка.

Фикстуры обезличены: слова придуманы, медиа-файлов нет (ffprobe подменён).
Запуск: py -3.10 -m pytest tests/test_cut_subs_source.py -q -p no:cacheprovider
"""
import json
import os
import re

import pytest

from core import cut_subs
from core import xml2ae
from core import xmlbuild
from core.gigaam_cut import pipeline
from core.gigaam_cut import textpass
from core.project_file import write_project
from core.umsg import ReelsiError

H = {"Host": "127.0.0.1:5001"}

# Слова ИСХОДНИКА (секунды камеры 1). Все шесть звучат в ролике, нарезка оставляет не всё.
SRC = [
    {"w": "Раз,", "start": 0.10, "end": 0.50},
    {"w": "два.", "start": 0.60, "end": 1.00},
    {"w": "три", "start": 2.00, "end": 2.40},
    {"w": "четыре,", "start": 2.50, "end": 2.90},
    {"w": "пять.", "start": 4.00, "end": 4.40},
    {"w": "шесть", "start": 4.50, "end": 4.90},
]
KEEP_FULL = [(0.0, 1.2), (1.9, 3.2), (3.9, 5.1)]   # первая нарезка: все три куска
KEEP_CUT = [(0.0, 1.2), (3.9, 5.1)]                 # вырезали средний кусок
# В XML слова капсом без знаков (clean_sub_text), поэтому ожидания — в виде букв.
TEXTS_FULL = ["раз", "два", "три", "четыре", "пять", "шесть"]
TEXTS_CUT = ["раз", "два", "пять", "шесть"]


def _n(words):
    """Вид слова в XML: капс без знаков — сравниваем по буквам, не по записи."""
    return [re.sub(r"\W", "", w).lower() for w in words]


@pytest.fixture
def fake_probe(monkeypatch):
    monkeypatch.setattr(xmlbuild, "probe", lambda p, **k: {
        "dur_s": 60.0, "width": 1920, "height": 1080, "timecode": "01:00:00:00"})


@pytest.fixture
def client():
    from flask import Flask
    import api
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def _first_cut(tmp_path, *, text_subs=True):
    """Нарезка как её пишет пайплайн: XML + .words.json + .srt + .srcwords.json + project."""
    cam = tmp_path / "cam1.mp4"
    cam.write_bytes(b"video")
    xml = str(tmp_path / "01_cam1.xml")
    subs = cut_subs.subs_for_keep(SRC, KEEP_FULL)
    xmlbuild.build([str(cam)], KEEP_FULL, [0.0], xml, sub_words=subs, music_path=None)
    if text_subs:
        cut_subs.write_sidecars(xml, subs)
        cut_subs.save_source_words(xml, SRC)
    proj = {"cams": [str(cam)], "offsets": [0.0], "fps": 60, "cam_return": 2,
            "scale": 50.4, "keep": [[s, e] for s, e in KEEP_FULL]}
    if text_subs:
        proj["text_subs"] = True
    write_project(os.path.splitext(xml)[0] + ".project.json", proj)
    return xml


def _xml_texts(xml):
    return _n([w for (_s, _e, w) in xml2ae.parse_full(xml)[2]])


def _side(xml):
    with open(os.path.splitext(xml)[0] + ".words.json", encoding="utf-8") as f:
        return json.load(f)


def _srt(xml):
    with open(os.path.splitext(xml)[0] + ".srt", encoding="utf-8") as f:
        return f.read()


def _save(client, xml, keep):
    r = client.post("/api/editor_save", json={"xml": xml, "keep": keep}, headers=H)
    d = r.get_json()
    assert d and d.get("ok"), d
    return d


def _subs_count(client, xml):
    r = client.post("/api/xml_state", json={"xml": xml}, headers=H)
    return r.get_json()["subs"]


# --------------------------------------------------------------------------- #
# 1. Вернуть кусок: его слова снова в субтитрах XML, .words.json и .srt
# --------------------------------------------------------------------------- #
def test_вернуть_кусок_возвращает_его_слова(tmp_path, client, fake_probe):
    xml = _first_cut(tmp_path)
    first_side = _side(xml)
    first_srt = _srt(xml)
    assert _xml_texts(xml) == TEXTS_FULL

    _save(client, xml, [list(k) for k in KEEP_CUT])
    assert _xml_texts(xml) == TEXTS_CUT

    d = _save(client, xml, [list(k) for k in KEEP_FULL])
    assert d["note"] == ""

    # слова вернувшегося куска — снова в XML, как при первой нарезке
    assert _xml_texts(xml) == TEXTS_FULL
    # .words.json и .srt — по новому таймлайну: тот же результат, что у первой нарезки
    assert _side(xml) == first_side
    assert _srt(xml) == first_srt
    assert _subs_count(client, xml) == len(TEXTS_FULL)


# --------------------------------------------------------------------------- #
# 2. Вырезать кусок: его слова пропали из субтитров, .words.json и .srt
# --------------------------------------------------------------------------- #
def test_вырезать_кусок_убирает_его_слова(tmp_path, client, fake_probe):
    xml = _first_cut(tmp_path)

    _save(client, xml, [list(k) for k in KEEP_CUT])

    assert _xml_texts(xml) == TEXTS_CUT
    assert _n([x["w"] for x in _side(xml)]) == TEXTS_CUT
    srt = _srt(xml)
    assert "три" not in srt.lower() and "четыре" not in srt.lower()
    assert "пять" in srt.lower() and "шесть" in srt.lower()


def test_сдвиг_границы_не_теряет_слова_исходника(tmp_path, client, fake_probe):
    """Граница уехала внутрь слова «три»: слово остаётся, потому что его середина в куске."""
    xml = _first_cut(tmp_path)
    shifted = [[0.0, 1.2], [2.1, 3.2], [3.9, 5.1]]   # начало «три» (2.0) срезано, середина 2.2 в куске

    _save(client, xml, shifted)

    texts = _xml_texts(xml)
    assert "три" in texts and "четыре" in texts


# --------------------------------------------------------------------------- #
# 3. Слов исходника нет: субтитры помечены устаревшими, шаг 2 их сделает с нуля
# --------------------------------------------------------------------------- #
def test_нет_слов_исходника_вырезание_держит_xml_а_добавление_устаревает(tmp_path, client, fake_probe):
    """Без .srcwords вырезание держит субтитры XML (их слова уже в XML). А вот вернуть кусок
    без слов исходника не из чего: субтитры помечаются устаревшими, шаг 2 делает их заново."""
    xml = _first_cut(tmp_path)
    os.remove(cut_subs.src_words_path(xml))

    d = _save(client, xml, [list(k) for k in KEEP_CUT])
    assert d["note"] == ""
    assert _xml_texts(xml) == TEXTS_CUT
    assert _subs_count(client, xml) == len(TEXTS_CUT)

    d = _save(client, xml, [list(k) for k in KEEP_FULL])
    assert _subs_count(client, xml) == 0
    assert _xml_texts(xml) == []
    assert not os.path.exists(os.path.splitext(xml)[0] + ".words.json")
    assert not os.path.exists(os.path.splitext(xml)[0] + ".srt")
    assert "устарев" in d["note"]
    # флаг снят: следующая правка не пытается вернуть субтитры из исходника
    d2 = _save(client, xml, [list(k) for k in KEEP_FULL])
    assert d2["note"] == ""
    assert _subs_count(client, xml) == 0


# --------------------------------------------------------------------------- #
# 3б. Ручные правки слов владельца переживают правку блоков
# --------------------------------------------------------------------------- #
def test_правка_текста_переживает_возврат_другого_куска(tmp_path, client, fake_probe):
    """Поправили «два» -> вырезали средний кусок -> вернули его: «двойка» на месте,
    «три» и «четыре» пришли из исходника."""
    xml = _first_cut(tmp_path)
    r = client.post("/api/edit_word", json={"xml": xml, "index": TEXTS_FULL.index("два"),
                                            "text": "двойка"}, headers=H)
    assert r.get_json().get("ok"), r.get_json()

    _save(client, xml, [list(k) for k in KEEP_CUT])
    _save(client, xml, [list(k) for k in KEEP_FULL])

    assert _xml_texts(xml) == ["раз", "двойка", "три", "четыре", "пять", "шесть"]


def test_удалённое_слово_не_возвращается(tmp_path, client, fake_probe):
    """Удалили «два» -> вырезали средний кусок -> вернули его: «два» не вернулось
    (оно внутри оставшегося куска, владелец его убрал); «три» и «четыре» — из исходника."""
    xml = _first_cut(tmp_path)
    r = client.post("/api/delete_word", json={"xml": xml, "index": TEXTS_FULL.index("два")},
                    headers=H)
    assert r.get_json().get("ok"), r.get_json()

    _save(client, xml, [list(k) for k in KEEP_CUT])
    _save(client, xml, [list(k) for k in KEEP_FULL])

    assert _xml_texts(xml) == ["раз", "три", "четыре", "пять", "шесть"]


def test_сдвиг_границы_наружу_старые_с_правками_новые_из_исходника(tmp_path, client, fake_probe):
    """Поправили «раз», вырезали средний кусок, растянули первый кусок наружу до 2.5 с:
    «разок» (правка) остался, «два» остался, «три» (середина 2.2 внутри растяжения) — из
    исходника, «четыре» (середина 2.7 за границей) не вошло."""
    xml = _first_cut(tmp_path)
    r = client.post("/api/edit_word", json={"xml": xml, "index": 0, "text": "разок"}, headers=H)
    assert r.get_json().get("ok"), r.get_json()
    _save(client, xml, [list(k) for k in KEEP_CUT])

    _save(client, xml, [[0.0, 2.5], [3.9, 5.1]])

    assert _xml_texts(xml) == ["разок", "два", "три", "пять", "шесть"]


# --------------------------------------------------------------------------- #
# 4. Нарезка без второго прохода: поведение прежнее, субтитры не трогаем
# --------------------------------------------------------------------------- #
def test_без_флага_правка_переносит_субтитры_как_раньше(tmp_path, client, fake_probe):
    xml = _first_cut(tmp_path, text_subs=False)

    d = _save(client, xml, [list(k) for k in KEEP_CUT])

    assert d["note"] == ""
    assert _xml_texts(xml) == TEXTS_CUT
    assert not os.path.exists(cut_subs.src_words_path(xml))
    # старый путь не пишет .words.json: это по-прежнему дело шага 2
    assert not os.path.exists(os.path.splitext(xml)[0] + ".words.json")


# --------------------------------------------------------------------------- #
# 5. Ручное снятие субтитров: правка блоков их не возвращает
# --------------------------------------------------------------------------- #
def test_снятые_вручную_субтитры_не_возвращаются(tmp_path, client, fake_probe):
    xml = _first_cut(tmp_path)
    r = client.post("/api/clear_subs", json={"xml": xml}, headers=H)
    assert r.get_json().get("ok"), r.get_json()

    _save(client, xml, [list(k) for k in KEEP_FULL])

    assert _subs_count(client, xml) == 0


def test_классическая_перенарезка_снимает_слова_исходника(tmp_path, client, fake_probe):
    """cutjob переписывает XML без второго прохода: прежние слова исходника и флаг
    снимаются, и правка блоков не возвращает чужие субтитры."""
    xml = _first_cut(tmp_path)
    cut_subs.forget_source(xml)
    assert not os.path.exists(cut_subs.src_words_path(xml))

    d = _save(client, xml, [list(k) for k in KEEP_CUT])

    assert d["note"] == ""
    assert _xml_texts(xml) == TEXTS_CUT   # старый перенос с таймлайна, как без флага


# --------------------------------------------------------------------------- #
# 6. Жёлтое слово переживает вырезание соседа и возврат куска
# --------------------------------------------------------------------------- #
def test_жёлтое_слово_переезжает_с_пересборкой(tmp_path, client, fake_probe):
    """«пять» живёт в третьем куске, который остаётся в обоих монтажах: его позиция
    в списке слов меняется (4 -> 2 -> 4), жёлтое должно ехать за словом, а не за индексом."""
    xml = _first_cut(tmp_path)
    xml2ae.write_highlights(xml, [TEXTS_FULL.index("пять")])
    assert xml2ae.auto_highlights(xml)["yellow"] == [4]

    _save(client, xml, [list(k) for k in KEEP_CUT])
    assert xml2ae.auto_highlights(xml)["yellow"] == [TEXTS_CUT.index("пять")]

    _save(client, xml, [list(k) for k in KEEP_FULL])
    assert xml2ae.auto_highlights(xml)["yellow"] == [TEXTS_FULL.index("пять")]


# --------------------------------------------------------------------------- #
# 7. Пайплайн: слова исходника сохраняются и флаг ставится только при втором проходе
# --------------------------------------------------------------------------- #
@pytest.fixture
def pipe(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline.aicut, "unload_ours", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.aicut, "warn_foreign_models", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.draftrender, "clean_tmp", lambda *a, **k: None)
    monkeypatch.setattr(xml2ae, "write_srt_for", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.xmlbuild, "build", lambda cams, keep, offsets, out, **kw: {
        "total_s": 3.0, "segments": len(keep)})
    ctc_words = [
        {"w": "первый", "start": 0.0, "end": 1.0, "prob": 0.99, "space_before": False},
        {"w": "второй", "start": 1.0, "end": 2.0, "prob": 0.99, "space_before": True},
        {"w": "третий", "start": 2.0, "end": 3.0, "prob": 0.99, "space_before": True},
    ]
    text = " ".join(w["w"] for w in ctc_words)
    monkeypatch.setattr(pipeline, "transcribe_words_whole", lambda *a, **k: (text, ctc_words))
    monkeypatch.setattr(pipeline, "decide_markup", lambda *a, **k: (set(range(3)), set(), "", []))
    monkeypatch.setattr(pipeline, "_silence_bounds", lambda *a, **k: [])
    stages = {"pauses": "speech", "sense": False, "dedupe": False,
              "refine": False, "breath": False, "draft": False}
    out = str(tmp_path / "out.xml")

    def run():
        pipeline.run("dummy.wav", ["cam1.mp4"], [0.0], out, 50.4,
                     stages=stages, emit=lambda *a, **k: None)
    return {"run": run, "out": out}


SUB_SRC = [
    {"w": "Первый,", "start": 0.0, "end": 1.0},
    {"w": "Второй.", "start": 1.0, "end": 2.0},
    {"w": "Третий", "start": 2.0, "end": 3.0},
]


def test_пайплайн_сохраняет_слова_исходника_и_флаг(monkeypatch, pipe):
    monkeypatch.setattr(pipeline.aicut, "cut_text_asr_engine", lambda *a, **k: "whisper:large-v3-turbo")
    monkeypatch.setattr(textpass, "text_pass",
                        lambda wav, words, engine, emit: (words, SUB_SRC))
    pipe["run"]()

    with open(cut_subs.src_words_path(pipe["out"]), encoding="utf-8") as f:
        assert json.load(f) == SUB_SRC
    with open(os.path.splitext(pipe["out"])[0] + ".project.json", encoding="utf-8") as f:
        assert json.load(f).get("text_subs") is True


def test_пайплайн_без_второго_прохода_ни_слов_ни_флага(monkeypatch, pipe):
    monkeypatch.setattr(pipeline.aicut, "cut_text_asr_engine", lambda *a, **k: "")
    pipe["run"]()

    assert not os.path.exists(cut_subs.src_words_path(pipe["out"]))
    with open(os.path.splitext(pipe["out"])[0] + ".project.json", encoding="utf-8") as f:
        assert "text_subs" not in json.load(f)


def test_сбой_сборки_не_оставляет_чужих_слов(monkeypatch, pipe):
    """Сборка упала — слова исходника НЕ записаны: прошлая нарезка не получит чужие слова."""
    monkeypatch.setattr(pipeline.aicut, "cut_text_asr_engine", lambda *a, **k: "whisper:large-v3-turbo")
    monkeypatch.setattr(textpass, "text_pass", lambda wav, words, engine, emit: (words, SUB_SRC))

    def boom(*a, **k):
        raise ReelsiError("сборка не удалась")
    monkeypatch.setattr(pipeline.xmlbuild, "build", boom)

    with pytest.raises(ReelsiError):
        pipe["run"]()
    assert not os.path.exists(cut_subs.src_words_path(pipe["out"]))


# --------------------------------------------------------------------------- #
# 8. Модуль: битые слова исходника — None, а не падение правки
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("content", ["не json", "{\"a\": 1}", "[{\"w\": \"x\"}]"])
def test_битые_слова_исходника_дают_none(tmp_path, content):
    xml = str(tmp_path / "c.xml")
    with open(cut_subs.src_words_path(xml), "w", encoding="utf-8") as f:
        f.write(content)
    assert cut_subs.load_source_words(xml) is None


def test_карта_слов_исходника_по_кускам():
    subs = cut_subs.subs_for_keep(SRC, KEEP_CUT)
    assert _n([x["w"] for x in subs]) == TEXTS_CUT
    assert cut_subs.subs_for_keep([], KEEP_CUT) is None
