# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
r"""Golden-тесты самых рискованных контрактов: parse_full, set_highlights/edit_word
(пишут в XML пользователя!), нормализация джобов, /api/status, /api/ui_state.

Запуск:  python -m pytest reelsi/tests -q
Фикстуры: tests/fixtures/timeline_subs.xml.gz (xmeml с 253 словами-субтитрами, 2 камеры)
и timeline_nosubs.xml (мультикам без субтитров). Оба сняты с настоящего проекта Premiere,
но обезличены: текст слов заменён на нейтральный ПОБАЙТОВО РАВНОЙ длины (иначе поедут
смещения во FlatBuffer-блобах Source Text), пути к материалу — на C:\footage\cam1|cam2.
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
    """Распакованная копия эталонного XML с субтитрами (правки не трогают фикстуру)."""
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


@pytest.fixture()
def xml_nosubs(tmp_path):
    dst = str(tmp_path / "timeline_nosubs.xml")
    shutil.copy(os.path.join(HERE, "fixtures", "timeline_nosubs.xml"), dst)
    return dst


# ---------------- parse_full ----------------

def test_parse_full_subs(xml_subs):
    meta, cams, subs, ins = xml2ae.parse_full(xml_subs)
    assert meta["fps"] == 60
    assert len(cams) == 2                       # мультикам распознан
    assert len(subs) == 253                     # все слова-субтитры на месте
    assert ins == []
    # контракт parse_full: subs = [(start, end, word), ...]
    s = subs[0]
    assert isinstance(s[2], str) and s[2]
    assert s[0] < s[1]
    # сырые переводы строк в словах недопустимы (ломают .jsx)
    assert not any("\n" in w[2] or "\r" in w[2] for w in subs)


def test_parse_full_nosubs(xml_nosubs):
    meta, cams, subs, ins = xml2ae.parse_full(xml_nosubs)
    assert len(cams) == 2 and subs == [] and ins == []


def test_parse_full_ncams_upper_bound(xml_nosubs):
    # ncams — ВЕРХНЯЯ граница: ncams=2 на 2-камерном даёт 2, лишние треки не камеры
    meta, cams, subs, ins = xml2ae.parse_full(xml_nosubs, ncams=2)
    assert len(cams) == 2


# ---------------- to_ae_full: вставки ----------------

def test_insert_mask_reaches_jsx(xml_nosubs, tmp_path):
    """Форма маски (mw/mh, «Маска Ш/В» в карточке) доезжает до INSERTS в .jsx, а без
    правки уходит 100/100. Правка живёт только в UI-состоянии, и любой обрыв цепочки
    (карточка -> job.ins -> _ins_js) виден в AE только рендером."""
    import json
    import re
    out = str(tmp_path / "mask.jsx")
    xml2ae.to_ae_full(xml_nosubs, out, inserts=[
        {"type": "photo", "style": "cam2", "media": "C:/x/a.png",
         "start_s": 1, "dur_s": 2, "mw": 130, "mh": 60},
        {"type": "photo", "style": "cam2", "media": "C:/x/b.png", "start_s": 5, "dur_s": 2},
    ])
    txt = open(out, encoding="utf-8-sig").read()
    ins = json.loads(re.search(r"var INSERTS=(\[.*?\]);", txt).group(1))
    assert (ins[0]["mw"], ins[0]["mh"]) == (130, 60)
    assert (ins[1]["mw"], ins[1]["mh"]) == (100, 100)   # дефолт = авторасчёт как раньше


def test_insert_video_pan_and_sin_reach_jsx(xml_nosubs, tmp_path):
    """Панорама (x/y) и старт куска в файле (sin) доезжают до INSERTS у ВИДЕО-вставки:
    горизонтальный ролик почти всегда надо подвинуть, а нужный кусок редко в начале файла.
    Сам зажим по запасу кадра и по длине файла делает уже .jsx — там известны размеры."""
    import json
    import re
    out = str(tmp_path / "video.jsx")
    xml2ae.to_ae_full(xml_nosubs, out, inserts=[
        {"type": "video", "media": "C:/x/c.mp4", "start_s": 2, "dur_s": 3,
         "x": -420, "y": 0, "sin": 12.5},
    ])
    txt = open(out, encoding="utf-8-sig").read()
    ins = json.loads(re.search(r"var INSERTS=(\[.*?\]);", txt).group(1))
    assert ins[0]["t"] == "video"
    assert (ins[0]["x"], ins[0]["y"], ins[0]["sin"]) == (-420, 0, 12.5)


def test_insert_scale_reaches_jsx(xml_nosubs, tmp_path):
    """Ручной масштаб (sc, «масштаб %» в карточке) доезжает до INSERTS у ФОТО и у ВИДЕО,
    без правки уходит 100. Держится отдельно от scale: тот у фото пересчитывается по
    пропорциям картинки на каждой сборке и ручное число затёр бы."""
    import json
    import re
    out = str(tmp_path / "scale.jsx")
    xml2ae.to_ae_full(xml_nosubs, out, inserts=[
        {"type": "photo", "style": "cam2", "media": "C:/x/a.png",
         "start_s": 1, "dur_s": 2, "sc": 160},
        {"type": "video", "media": "C:/x/c.mp4", "start_s": 5, "dur_s": 3, "sc": 55},
        {"type": "photo", "style": "cam2", "media": "C:/x/b.png", "start_s": 9, "dur_s": 2},
    ])
    txt = open(out, encoding="utf-8-sig").read()
    ins = json.loads(re.search(r"var INSERTS=(\[.*?\]);", txt).group(1))
    assert [x["sc"] for x in ins] == [160, 55, 100]


def test_insert_jsx_scale_drives_animation(xml_nosubs, tmp_path):
    """Анимация считается ОТ масштаба, а не от констант: наезд кам2 берётся как
    PEAK/BASE от осевшего scale (иначе у крупной карточки пик оказывался меньше
    конечного размера), масштаб слоя = авторасчёт × sc, звук видеовставки выключен.
    Ключи анимаций считает Python (задание C) — проверяем их в плане сцены."""
    plan = xml2ae.scene_plan(xml_nosubs, inserts=[
        {"type": "photo", "media": "C:/x/a.png", "start_s": 80, "dur_s": 2},   # на перебивке → стиль cam2
    ])
    ins = plan["inserts"][0]
    assert ins["style"] == "cam2"
    S = (ins["scale"] or 44) * (ins["sc"] or 100) / 100
    keys = ins["anim"]["scale"]
    assert keys[0][1] == pytest.approx(S * 100 / 44)   # первый ключ — пик наезда PEAK/BASE
    assert keys[-1][1] == pytest.approx(S)             # последний — осевший масштаб
    out = str(tmp_path / "anim.jsx")
    xml2ae.to_ae_full(xml_nosubs, out, inserts=[
        {"type": "photo", "media": "C:/x/a.png", "start_s": 500, "dur_s": 2},
    ])
    txt = open(out, encoding="utf-8-sig").read()
    assert "if(ins.fit) try{ vl.property(\"ADBE Transform Group\").property(\"ADBE Scale\").setValue([ins.fit,ins.fit]); }catch(e){}" \
           in txt                                        # видео: масштаб заполнения кадра приходит из Python
    assert "vl.audioEnabled=false;" in txt                # звук вставки — с камеры 1


def test_cam1_fit_is_frame_fill_not_premiere_scale(xml_nosubs, tmp_path):
    """«Заполнение кадра» считается от РАЗМЕРА ИСХОДНИКА (его знает AE), а не от масштаба из
    Премьера. Тот описывает файл, который лежал в Премьере: после пережатия 4K->1080p из XML
    приезжает scale=50.4, и картинка встаёт вполовину кадра (vik.aep, 2026-08-01 — юзер
    компенсировал это +98% на зум-нуле). Рото-копия и её маска считаются от того же числа."""
    import re
    out = str(tmp_path / "fit.jsx")
    xml2ae.to_ae_full(xml_nosubs, out, style={"cam1_fit": 115})
    txt = open(out, encoding="utf-8-sig").read()
    assert re.search(r"var CAM1_FIT=115;", txt)
    assert "fitS = 100*Math.max(W/src.width, H/src.height)" in txt        # заполнение кадра
    assert "var csc = isSecond ? c[5] : fitS*CAM1_FIT/100;" in txt        # кам1 — от него, кам2 — как в Премьере
    assert "var rsc = (ci==0) ? rfit*CAM1_FIT/100 : rr.scale;" in txt     # рото-копия
    assert "var msc=rsc*(rr.mf||1);" in txt                               # и её маска

    plain = str(tmp_path / "plain.jsx")
    xml2ae.to_ae_full(xml_nosubs, plain)
    assert re.search(r"var CAM1_FIT=100;", open(plain, encoding="utf-8-sig").read())


def test_intro_placement_reaches_jsx(xml_nosubs, tmp_path):
    """Интро висит на своём нуле: общий масштаб и сдвиг едут туда, а не в прекомпы (внутри
    них раскладка слов посчитана в пикселях, и автофит длинных строк свой у каждого). Юзер
    правил это прямо в собранной композиции (vik.aep, 2026-08-01)."""
    import re
    out = str(tmp_path / "intro.jsx")
    xml2ae.to_ae_full(xml_nosubs, out, style={"intro_scale": 60, "intro_y": 75})
    txt = open(out, encoding="utf-8-sig").read()
    assert re.search(r"var INTRO_SCALE=60, INTRO_Y=75;", txt)
    assert 'setValue([0,INTRO_Y])' in txt
    assert 'setValue([INTRO_SCALE,INTRO_SCALE])' in txt

    plain = open(xml2ae.to_ae_full(xml_nosubs, str(tmp_path / "p2.jsx"))[0],
                 encoding="utf-8-sig").read()
    assert "var INTRO_SCALE=100, INTRO_Y=0;" in plain


# ---------------- set_highlights / edit_word ----------------

def test_set_highlights_roundtrip_and_backup(xml_subs):
    res = xml2ae.set_highlights(xml_subs, [1, 3, 5])
    assert sorted(res["colored"]) == [1, 3, 5]
    assert os.path.isfile(xml_subs + ".bak"), "первая правка обязана оставить .bak"
    # повторный вызов с другим набором: старые снимаются, новые красятся
    res2 = xml2ae.set_highlights(xml_subs, [2])
    assert sorted(res2["colored"]) == [2]
    assert xml2ae.auto_highlights(xml_subs)["yellow"] == [2]   # как видит AE-парсер
    meta, cams, subs, ins = xml2ae.parse_full(xml_subs)
    assert len(subs) == 253                     # ничего не потеряли
    # .bak = оригинал без жёлтых
    assert not xml2ae.auto_highlights(xml_subs + ".bak")["yellow"]


def test_edit_word_preserves_count_and_color(xml_subs):
    xml2ae.set_highlights(xml_subs, [4])
    r = xml2ae.edit_word(xml_subs, 4, "ПРОВЕРКА")
    assert r.get("ok") and r["word"] == "ПРОВЕРКА"
    meta, cams, subs, ins = xml2ae.parse_full(xml_subs)
    assert len(subs) == 253
    assert subs[4][2] == "ПРОВЕРКА"
    assert 4 in xml2ae.auto_highlights(xml_subs)["yellow"]   # цвет пережил правку текста
    # мусорный ввод чистится, переводы строк не проходят
    r2 = xml2ae.edit_word(xml_subs, 0, "  два\nслова ")
    assert r2.get("ok") and "\n" not in r2["word"]


def test_edit_word_bad_index(xml_subs):
    assert xml2ae.edit_word(xml_subs, 100500, "x").get("error")
    assert xml2ae.edit_word(xml_subs, 0, "").get("error")


def test_edit_word_never_touches_badwords(client, xml_subs, tmp_path, monkeypatch):
    """Ручная звёздочка в редакторе НЕ попадает в список плохих слов.

    Пойманный баг: слово само уезжало в `badwords.user.txt`, список зарастал мусором
    выравнивания («и», «из», «тет»), а censor сверяет по ПОДСТРОКЕ — основа «и»
    зацензурила 106 слов из 203 в клипе. Список правится только руками (⚙ → «Слова»)."""
    from core import censor
    bad = str(tmp_path / "badwords.user.txt")
    open(bad, "w", encoding="utf-8").write("убива\n")
    monkeypatch.setattr(censor, "USER_PATHS", dict(censor.USER_PATHS, bad=bad))
    monkeypatch.setattr(censor, "_cache", {"bad": (None, None, censor.DEFAULT_BAD),
                                           "ok": (None, None, censor.DEFAULT_OK)})
    d = client.post("/api/edit_word", json={
        "xml": xml_subs, "index": 0, "text": "ТАДА*ОФИЛ", "was": "ТАДАЛОФИЛ"}).get_json()
    assert d.get("ok")
    assert not d.get("censored")                         # поля больше нет вовсе
    assert open(bad, encoding="utf-8").read() == "убива\n"
    # обычная правка списки тоже не трогает
    d2 = client.post("/api/edit_word", json={
        "xml": xml_subs, "index": 1, "text": "ПРОВЕРКА", "was": "ЛОРЕМ"}).get_json()
    assert d2.get("ok")
    assert open(bad, encoding="utf-8").read() == "убива\n"


# ---------------- API-слой ----------------

@pytest.fixture(scope="module")
def client():
    import webui
    return webui.app.test_client()


def test_status_contract(client):
    d = client.get("/api/status").get_json()
    for k in ("running", "done", "log", "log_total", "results", "failed",
              "kind", "label", "progress"):
        assert k in d


def test_ui_state_roundtrip(client):
    import api
    old = None
    if os.path.isfile(api.UI_STATE_PATH):        # не затираем реальное состояние юзера
        old = open(api.UI_STATE_PATH, encoding="utf-8").read()
    try:
        st = {"CLIPS": [{"xml": "x.xml"}], "STEP": 2}
        assert client.post("/api/ui_state", json={"state": st}).get_json()["ok"]
        assert client.get("/api/ui_state").get_json()["state"] == st
    finally:
        if old is not None:
            open(api.UI_STATE_PATH, "w", encoding="utf-8").write(old)
        elif os.path.isfile(api.UI_STATE_PATH):
            os.remove(api.UI_STATE_PATH)


def test_set_yellow_endpoint(client, xml_subs):
    d = client.post("/api/set_yellow", json={"xml": xml_subs, "indices": [7]}).get_json()
    assert d.get("ok") and d["colored"] == [7]
    side = os.path.splitext(xml_subs)[0] + ".yellow.json"
    assert json.load(open(side, encoding="utf-8")) == {"yellow": [7]}   # канонический формат (aicut)


def test_ai_config_image_rembg_roundtrip(client):
    """Галка «убирать фон» ходит через тот же /api/ai_config и не сбивает active_image."""
    from core import aicut
    old = aicut.load_ai_config().get("image_rembg", True)
    try:
        for want in (False, True):
            d = client.post("/api/ai_config",
                            json={"action": "set_image_rembg", "value": want}).get_json()
            assert d.get("image_rembg") is want, d
            assert client.get("/api/ai_config").get_json()["image_rembg"] is want
    finally:
        client.post("/api/ai_config", json={"action": "set_image_rembg", "value": old})


def test_rembg_endpoint_guards(client, tmp_path):
    """/api/rembg: нет файла и видео — понятные ошибки, а не 500. Файл с альфой не
    трогаем (changed=False) — модель rembg тут не нужна, качать её тест не заставляет."""
    assert "error" in client.post("/api/rembg", json={"path": "Z:/nope.png"}).get_json()
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"\0")
    assert "фото" in client.post("/api/rembg", json={"path": str(vid)}).get_json()["error"]
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("нет Pillow")
    p = tmp_path / "already.png"
    Image.new("RGBA", (8, 8), (10, 20, 30, 0)).save(p)
    d = client.post("/api/rembg", json={"path": str(p)}).get_json()
    assert d.get("ok") and d["changed"] is False and d["path"] == str(p)


def test_adopt_moves_into_library(tmp_path, monkeypatch):
    """Вставки, ушедшие в проект, переезжают в базу: фото->photos, видео->videos,
    описание=запрос, коллизия имён не затирает, уже лежащее в базе не двигается."""
    from core import insertlib
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("нет Pillow")
    lib, dl = tmp_path / "lib", tmp_path / "dl"
    lib.mkdir()
    dl.mkdir()
    monkeypatch.setattr(insertlib, "INDEX_PATH", str(tmp_path / "idx.json"))
    json.dump({"items": [], "dirs": [str(lib)], "emb_model": ""},
              open(insertlib.INDEX_PATH, "w", encoding="utf-8"))
    insertlib._CACHE["data"] = None
    pic, vid = dl / "a.png", dl / "b.mp4"
    Image.new("RGB", (8, 8), "red").save(pic)
    vid.write_bytes(b"\0")
    moved = insertlib.adopt([{"path": str(pic), "desc": "liver 3d"},
                             {"path": str(vid), "desc": "city"}], str(lib))
    assert len(moved) == 2 and not os.listdir(dl)                  # источник опустел
    assert (lib / "photos" / "a.png").is_file() and (lib / "videos" / "b.mp4").is_file()
    idx = {i["name"]: i["desc"] for i in
           json.load(open(insertlib.INDEX_PATH, encoding="utf-8"))["items"]}
    assert idx == {"a.png": "liver 3d", "b.mp4": "city"}           # desc = запрос вставки
    Image.new("RGB", (8, 8), "blue").save(pic)                     # то же имя, другой файл
    m2 = insertlib.adopt([{"path": str(pic), "desc": "second"}], str(lib))
    assert os.path.basename(list(m2.values())[0]) == "a_2.png"     # не затёрли первый
    assert insertlib.adopt([{"path": str(lib / "photos" / "a.png"), "desc": "x"}],
                           str(lib)) == {}                         # уже в базе — не двигаем


def test_adopt_inserts_rewrites_media(tmp_path, monkeypatch):
    """_adopt_inserts подменяет media ДО сборки .jsx, а пустые/битые пути не трогает."""
    import api
    from core import insertlib
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("нет Pillow")
    lib, dl = tmp_path / "lib", tmp_path / "dl"
    lib.mkdir()
    dl.mkdir()
    monkeypatch.setattr(insertlib, "INDEX_PATH", str(tmp_path / "idx.json"))
    json.dump({"items": [], "dirs": [], "emb_model": ""},
              open(insertlib.INDEX_PATH, "w", encoding="utf-8"))
    insertlib._CACHE["data"] = None
    pic = dl / "x.png"
    Image.new("RGB", (8, 8), "red").save(pic)
    ins = [{"media": str(pic), "query": "liver"}, {"media": "", "query": "none"},
           {"media": str(tmp_path / "gone.png"), "query": "ghost"}]
    moved = api._adopt_inserts(ins, str(lib))
    assert len(moved) == 1
    assert ins[0]["media"] == str(lib / "photos" / "x.png")
    assert ins[1]["media"] == "" and ins[2]["media"].endswith("gone.png")


def _mini_index(tmp_path, monkeypatch, items):
    """Мини-индекс без эмбеддера: score считается токенами по desc.
    emb_tag ставим актуальный: иначе _ensure_emb_tag() в match_many идёт в ЖИВОЙ
    LM Studio на localhost:1234, и на машине с поднятым LM Studio тест внезапно
    считает настоящие косинусы вместо токенов."""
    from core import insertlib
    monkeypatch.setattr(insertlib, "INDEX_PATH", str(tmp_path / "idx.json"))
    for it in items:
        open(it["path"], "wb").write(b"x")
    json.dump({"items": items, "dirs": [], "emb_model": "", "emb_tag": insertlib.EMB_TAG},
              open(insertlib.INDEX_PATH, "w", encoding="utf-8"))
    insertlib._CACHE["data"] = None
    return insertlib


def test_match_prefers_newest_on_tie(tmp_path, monkeypatch):
    """Перегенерил тот же запрос -> в базе два файла с ОДИНАКОВЫМ desc и одинаковым
    score. Побеждать должен свежий: старый — это ровно тот, который юзер забраковал."""
    old, new = tmp_path / "a.png", tmp_path / "b.png"
    lib = _mini_index(tmp_path, monkeypatch, [
        {"path": str(old), "name": "a.png", "type": "photo", "used": 0,
         "desc": "liver 3d icon", "added": 100.0},
        {"path": str(new), "name": "b.png", "type": "photo", "used": 0,
         "desc": "liver 3d icon", "added": 200.0},
    ])
    assert lib.match("liver 3d icon", k=2)[0]["path"] == str(new)


def test_reject_excludes_only_that_query(tmp_path, monkeypatch):
    """Брак — это пара файл+запрос: под забракованный запрос файл не предлагается,
    под другую тему остаётся доступен."""
    p = tmp_path / "a.png"
    lib = _mini_index(tmp_path, monkeypatch, [
        {"path": str(p), "name": "a.png", "type": "photo", "used": 0,
         "desc": "liver kidney organ"},
    ])
    assert lib.match("liver organ", k=3)                       # до брака находится
    assert lib.reject(str(p), "liver organ") is True
    lib._CACHE["data"] = None
    assert lib.match("liver organ", k=3) == []                 # под этот запрос — нет
    assert lib.match("kidney organ", k=3)                      # под другой — да
    lib.reject(str(p), "liver organ", on=False)                # брак снимается
    lib._CACHE["data"] = None
    assert lib.match("liver organ", k=3)


def test_query_embedding_drops_style_words_and_prefixes(monkeypatch):
    """Что реально уезжает в эмбеддер: из ЗАПРОСА вырезан стиль («3d icon» — это
    инструкция генератору, а не предмет поиска), из описания — нет; для nomic обе
    стороны получают свой префикс, иначе асимметричный поиск плывёт."""
    from core import insertlib
    seen = []
    monkeypatch.setattr(insertlib, "_embed", lambda texts, model: seen.append(texts) or [[1.0]] * len(texts))
    insertlib._emb_queries(["broken eyeglasses 3d icon"], "text-embedding-nomic-embed-text-v2-moe")
    insertlib._emb_docs(["3D render of broken eyeglasses"], "text-embedding-nomic-embed-text-v2-moe")
    insertlib._emb_queries(["broken eyeglasses 3d icon"], "some-other-embedder")
    assert seen[0] == ["search_query: broken eyeglasses"]
    assert seen[1] == ["search_document: 3D render of broken eyeglasses"]
    assert seen[2] == ["broken eyeglasses"]                    # префикс — только nomic
    assert insertlib._q_text("3d icon render") == "3d icon render"   # пусто -> запрос как был


def test_stale_emb_tag_triggers_reembed(tmp_path, monkeypatch):
    """Индекс, собранный по старой схеме, пересчитывается сам (описания уже в индексе,
    рескан диска не нужен) — иначе запрос по новой схеме сравнивался бы со старыми
    векторами и подбор молча превратился бы в мусор."""
    p = tmp_path / "a.png"
    lib = _mini_index(tmp_path, monkeypatch, [
        {"path": str(p), "name": "a.png", "type": "photo", "used": 0,
         "desc": "liver 3d", "emb": [1.0, 0.0]},
    ])
    d = json.load(open(lib.INDEX_PATH, encoding="utf-8"))
    d["emb_model"], d["emb_tag"] = "nomic-x", "q1"             # старая схема
    json.dump(d, open(lib.INDEX_PATH, "w", encoding="utf-8"))
    lib._CACHE["data"] = None
    monkeypatch.setattr(lib, "_embed", lambda texts, model: [[0.0, 1.0]] * len(texts))
    assert lib._ensure_emb_tag() is True
    d = json.load(open(lib.INDEX_PATH, encoding="utf-8"))
    assert d["emb_tag"] == lib.EMB_TAG and d["items"][0]["emb"] == [0.0, 1.0]
    lib._CACHE["data"] = None
    assert lib._ensure_emb_tag() is False                      # второй раз — уже нечего


def test_match_type_hint_is_soft(tmp_path, monkeypatch):
    """type_hint — пожелание, а не фильтр: при прочих равных вперёд идёт заявленный тип,
    но заметно более подходящий файл другого типа побеждает (тип карточки потом чинится
    по расширению). Жёсткий фильтр тут пробовали 2026-07-30 и откатили — он резал и
    ручную выдачу 📚, в вариантах не оставалось ничего кроме одного типа."""
    ph, vd = tmp_path / "a.png", tmp_path / "b.mp4"
    lib = _mini_index(tmp_path, monkeypatch, [
        {"path": str(ph), "name": "a.png", "type": "photo", "used": 0, "desc": "liver organ"},
        {"path": str(vd), "name": "b.mp4", "type": "video", "used": 0, "desc": "liver organ"},
    ])
    assert lib.match("liver organ", k=5, type_hint="photo")[0]["path"] == str(ph)
    assert lib.match("liver organ", k=5, type_hint="video")[0]["path"] == str(vd)
    assert len(lib.match("liver organ", k=5, type_hint="photo")) == 2   # второй тип не выброшен
    # видео описано точнее -> едет видео, даже если просили фото
    d = json.load(open(lib.INDEX_PATH, encoding="utf-8"))
    d["items"][1]["desc"] = "liver organ 3d icon"
    json.dump(d, open(lib.INDEX_PATH, "w", encoding="utf-8"))
    lib._CACHE["data"] = None
    assert lib.match("liver organ 3d icon", k=5, type_hint="photo")[0]["path"] == str(vd)


def test_rescan_keeps_rejects(tmp_path, monkeypatch):
    """Полный рескан базы не должен терять брак и отметку свежести."""
    from core import insertlib
    d = tmp_path / "lib"
    d.mkdir()
    p = d / "a.png"
    lib = _mini_index(tmp_path, monkeypatch, [
        {"path": str(p), "name": "a.png", "type": "photo", "used": 0,
         "desc": "liver 3d", "rej": ["liver 3d"], "added": 123.0},
    ])
    lib.build_index([str(d)], use_emb=False)
    it = json.load(open(insertlib.INDEX_PATH, encoding="utf-8"))["items"][0]
    assert it.get("rej") == ["liver 3d"] and it.get("added") == 123.0


def test_blob_fits_word_longer_than_any_template():
    """Слово длиннее самого большого эталонного блоба не должно выпадать из субтитров:
    блоб удлиняется, текст читается обратно, «жёлтый» блоб остаётся жёлтым."""
    import base64
    from core import subtitle_blobs as sb
    for lib in (sb.library(), sb.colour_library()):
        for w in ("ГИПЕРЧУВСТВИТЕЛЬНОСТЬ", "ЭЛЕКТРОЭНЦЕФАЛОГРАФИЯ",
                  "АНТИДИСЕСТАБЛИШМЕНТАРИАНИЗМ"):
            assert len(w.encode("utf-8")) > lib.max_len
            b = base64.b64decode(lib.make(w))
            assert sb._read_text(b) == w
    assert sb.blob_is_coloured(
        base64.b64decode(sb.colour_library().make("ГИПЕРЧУВСТВИТЕЛЬНОСТЬ")))


def test_draft_proxy_key_tracks_source_and_geometry(tmp_path):
    """Ключ прокси-файла держит исходник и геометрию: перезаписал/переснял камеру или
    сменил height — прокси обязан пересобраться, иначе черновик соберётся по старому."""
    from core import draftrender
    src = tmp_path / "cam1.mp4"
    src.write_bytes(b"x" * 100)
    tdir = str(tmp_path)
    a = draftrender._proxy_path(str(src), 720, 1280, tdir)
    assert a == draftrender._proxy_path(str(src), 720, 1280, tdir)   # тот же файл -> кэш-хит
    assert a != draftrender._proxy_path(str(src), 1080, 1920, tdir)  # другой размер кадра
    src.write_bytes(b"x" * 200)
    os.utime(str(src), (1_700_000_000, 1_700_000_000))
    assert a != draftrender._proxy_path(str(src), 720, 1280, tdir)   # исходник изменился


def test_asr_engines_contract(client):
    """Реестр движков — общий источник истины для селекторов UI. Ключи должны быть
    у КАЖДОГО движка (фронт группирует по lang и фильтрует по selfcheck), а
    пользовательские CTC-модели из asr_engines.json — доезжать до списка."""
    d = client.get("/api/asr_engines").get_json()
    eng = d["engines"]
    assert {"whisper:large-v3", "gigaam", "omni"} <= {e["id"] for e in eng}
    for e in eng:
        assert {"id", "label", "lang", "kind", "prob", "subs", "selfcheck"} <= set(e)
    ctc = [e for e in eng if e["id"].startswith("ctc:")]
    assert ctc and all(e.get("model") for e in ctc)     # без model движок не запустить
    omni = next(e for e in eng if e["id"] == "omni")
    assert not omni["selfcheck"]                        # фразовые тайминги стыки не проверяют


def test_selfcheck_engine_aliases_and_straddle():
    """Старое значение селектора («large-v3») = размер Whisper, а не имя движка.
    И: рез, проходящий ПОСЕРЕДИНЕ слова, обязан находиться по исходной
    транскрипции — на этом держится самопроверка для CTC-движков."""
    from core import selfcheck
    assert selfcheck._norm_engine("large-v3") == "whisper:large-v3"
    assert selfcheck._norm_engine(None) == "whisper:large-v3"
    assert selfcheck._norm_engine("ctc:en") == "ctc:en"
    ref = [{"w": "раз", "start": 0.0, "end": 0.5},
           {"w": "купили", "start": 0.6, "end": 1.4},
           {"w": "два", "start": 2.0, "end": 2.4}]
    keep = [(0.0, 1.0), (2.0, 2.4)]                     # 1.0 — внутри «купили»
    bad = selfcheck.analyze_straddle(ref, keep)
    assert [(b["junction"], b["side"], b["w"]) for b in bad] == [(0, "end", "купили")]
    assert not selfcheck.analyze_straddle(ref, [(0.0, 1.6), (2.0, 2.4)])   # рез в паузе


def test_gigaam_heads_and_widen():
    """Головы GigaAM: CTC-и — для нарезки/самопроверки, RNN-T — только субтитры
    (эмиссия токена ≠ границы звучания). И `_widen` растягивает 0.04с-слова
    RNN-T до читаемых, не наезжая на соседей."""
    from core import asr_backends as ab
    heads = {e["id"]: e for e in ab.engines() if e.get("gigaam")}
    assert heads["gigaam"]["gigaam"] == "v3_ctc" and heads["gigaam"]["selfcheck"]
    assert not heads["gigaam:v3_rnnt"]["selfcheck"]
    assert not heads["gigaam:v3_e2e_rnnt"]["selfcheck"]
    assert all(h["subs"] for h in heads.values())
    w = ab._widen([{"w": "если", "start": 6.72, "end": 6.76},
                   {"w": "вы", "start": 7.04, "end": 7.08},
                   {"w": "купили", "start": 7.20, "end": 7.52}])
    assert all(x["end"] - x["start"] >= 0.13 for x in w)
    assert all(w[i]["end"] <= w[i + 1]["start"] for i in range(len(w) - 1))


def test_whisper_cpp_engines_contract():
    """whisper.cpp-движки: без пословной вероятности — в самопроверку стыков не
    пускать (как RNN-T-головы GigaAM), но в субтитры — да, и язык multi."""
    from core import asr_backends as ab
    cpp = {e["id"]: e for e in ab.engines() if e["kind"] == "whisper_cpp"}
    assert {"whisper.cpp:large-v3", "whisper.cpp:medium",
            "whisper.cpp:small"} == set(cpp)
    assert all(e["subs"] and not e["selfcheck"] and not e["prob"] for e in cpp.values())
    assert all(e["lang"] == "multi" for e in cpp.values())


def test_whisper_cpp_parse_json():
    """Разбор `-oj`: единицы таймкодов плавают между версиями — v1.9+ пишет
    миллисекунды и в словах, и в offsets (калибровка по timestamps), старые —
    слова в 10-мс тиках при секундных offsets. Без timestamps — исторический
    формат. Все пути отдают одинаковый контракт в секундах."""
    from core import whisper_cpp
    old = whisper_cpp.parse_words({"transcription": [
        {"words": [{"word": " привет", "start": 0, "end": 50},
                   {"word": "мир ", "start": 60, "end": 130}]}]})
    assert old == [{"w": "привет", "start": 0.0, "end": 0.5},
                   {"w": "мир", "start": 0.6, "end": 1.3}]
    segs = whisper_cpp.parse_words({"transcription": [
        {"text": "раз два", "offsets": {"from": 1.0, "to": 2.0}}]})
    assert segs == [{"w": "раз", "start": 1.0, "end": 1.5},
                    {"w": "два", "start": 1.5, "end": 2.0}]
    new = whisper_cpp.parse_words({"transcription": [
        {"timestamps": {"from": "00:00:00,000", "to": "00:00:10,500"},
         "offsets": {"from": 0, "to": 10500},
         "words": [{"word": "And", "start": 0, "end": 477}]}]})
    assert new == [{"w": "And", "start": 0.0, "end": 0.477}]
    new_segs = whisper_cpp.parse_words({"transcription": [
        {"timestamps": {"from": "00:00:00,000", "to": "00:00:10,500"},
         "offsets": {"from": 0, "to": 10500},
         "text": "раз два"}]})
    assert new_segs == [{"w": "раз", "start": 0.0, "end": 5.25},
                        {"w": "два", "start": 5.25, "end": 10.5}]


def test_whisper_cpp_cli_path(monkeypatch, tmp_path):
    """Поиск бинарника: явный путь > PATH > своя папка (в т.ч. рекурсивно:
    GitHub-архив распаковывается в bin/Release/). Не должен зависеть от машины,
    на которой запускаются тесты (whisper-cli может стоять локально)."""
    from core import whisper_cpp
    fake = tmp_path / "whisper-cli"
    fake.write_text("x")
    monkeypatch.setenv("REELSI_WHISPER_CLI", str(fake))
    assert whisper_cpp.whisper_cli_path() == str(fake)
    monkeypatch.delenv("REELSI_WHISPER_CLI")
    monkeypatch.setattr(whisper_cpp.shutil, "which", lambda n: None)
    empty = tmp_path / "empty"          # пустая папка: от REELSI_WHISPER_CLI
    empty.mkdir()                       # и от fake выше поиск уже не зависит
    monkeypatch.setattr(whisper_cpp, "BIN_DIR", str(empty))
    assert whisper_cpp.whisper_cli_path() is None
    nested = tmp_path / "nested" / "Release"   # регрессия: архив распаковывается
    nested.mkdir(parents=True)                 # в подпапку, искать надо рекурсивно
    (nested / whisper_cpp._BIN_NAME).write_text("x")
    monkeypatch.setattr(whisper_cpp, "BIN_DIR", str(nested.parent))
    assert whisper_cpp.whisper_cli_path() == str(nested / whisper_cpp._BIN_NAME)


def test_whisper_cpp_pick_asset():
    """Выбор ассета из GitHub API: точное имя из ASSET_PLAN под платформу,
    отсутствующего — честный None (дальше скажет «скачайте руками»)."""
    from core import whisper_cpp
    assets = [{"name": "whisper-bin-ubuntu-x64.tar.gz", "browser_download_url": "u"},
              {"name": "whisper-bin-Win32.zip", "browser_download_url": "w"},
              {"name": "whisper-cublas-12.4.0-bin-x64.zip", "browser_download_url": "c"}]
    assert whisper_cpp._pick_asset(assets, ("whisper-bin-Win32.zip",)) == "w"
    assert whisper_cpp._pick_asset(assets, ("whisper-bin-mac.zip",)) is None
    assert whisper_cpp._pick_asset(None, ("x",)) is None
    assert whisper_cpp.ASSET_PLAN.get(("win32", "amd64")) == "whisper-bin-Win32.zip"


def test_service_files_are_not_style_presets(tmp_path, monkeypatch):
    """`styles/_aliases.json` — служебный файл, а не пресет стиля.

    Регресс от 2026-08-06: старые имена встроенных пресетов вынесли в
    styles/_aliases.json (в нём были фамилии реальных людей), а `_files()` берёт из
    папки ВСЕ *.json — и служебный файл поехал в селектор стилей как пункт
    «_aliases». Всё, что начинается с подчёркивания, — не шаблон пользователя.
    """
    from core import styles as st
    monkeypatch.setattr(st, "STYLE_DIR", str(tmp_path))
    (tmp_path / "_aliases.json").write_text('{"test_alias": "base"}', encoding="utf-8")
    (tmp_path / "_черновик.json").write_text('{"label": "служебный"}', encoding="utf-8")
    (tmp_path / "Мой стиль.json").write_text('{"label": "Мой стиль"}', encoding="utf-8")

    names = st.all_styles().keys()
    assert "Мой стиль" in names, "пользовательский шаблон пропал"
    assert "_aliases" not in names and "_черновик" not in names, (
        "служебный файл показывается как пресет стиля")
    assert "base" in names and "geologica" in names, "встроенные пресеты пропали"


def test_backend_package_is_not_hidden_from_git():
    """Каждый .py пакета `api/` должен попадать в git.

    Регресс от 2026-08-06: бэкенд распилили на пакет, а в .gitignore лежало правило
    `_*.py` для черновиков в корне — без ведущего слэша оно съело `api/__init__.py`
    и `api/_core.py`. Локально всё работало (файлы на диске есть), а клонировавший
    получал пакет без точки входа. Правило сузили до `/_*.py`, но грабли общие:
    любой новый модуль с подчёркиванием наступит на них снова.
    """
    import subprocess
    pkg = os.path.join(ROOT, "api")
    mods = sorted(f for f in os.listdir(pkg) if f.endswith(".py"))
    assert "__init__.py" in mods and "_core.py" in mods, "пакет api/ разобран не тем местом"
    r = subprocess.run(["git", "check-ignore"] + [os.path.join("api", m) for m in mods],
                       cwd=ROOT, capture_output=True, text=True)
    if r.returncode == 128:
        pytest.skip("не git-репозиторий")
    hidden = [x for x in r.stdout.splitlines() if x.strip()]
    assert not hidden, "gitignore прячет модули бэкенда: " + ", ".join(hidden)

def test_live_catalogs_are_assigned_to_the_owning_module():
    """Каталоги OpenRouter обновляются присваиванием ИЗВНЕ — адресатом должен быть
    модуль-владелец, а не фасад `aicut`.

    Регресс от 2026-08-06 (распил aicut.py на пакет): присваивание каталога в
    /api/ai_models стало класть атрибут на пакет, а `video_caps` продолжает читать
    свои globals — кнопка «Обновить список моделей» тихо переставала работать.
    Поймать это иначе нечем: путь ходит в сеть и тестами не покрывается.

    (Строку-образец здесь не пишем: она попала бы под собственную же проверку.)
    """
    import re as _re
    pat = _re.compile(r"\baicut\.[A-Za-z_]+\s*=(?!=)")
    skip_dirs = {"aicut", ".git", "__pycache__", "docs", "_videogen", ".ruff_cache"}
    bad = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            for i, line in enumerate(open(p, encoding="utf-8", errors="replace"), 1):
                if pat.search(line):
                    bad.append("%s:%d: %s" % (os.path.relpath(p, ROOT), i, line.strip()))
    assert not bad, "присваивание в фасад aicut вместо модуля-владельца: " + " | ".join(bad)
