# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Музыка в стиле: переопределение у клипа, видимый случайный трек, «Другой трек».

Было: режим/ссылка/папка музыки — общие поля шага 3 (`musicmode`/`aemusic`/`aemusicdir`),
у клипа только `music`/`music_random`. Случайный трек выбирался сидом и в превью
(`/api/music_random`), и в сборке (`plan_audio` -> `ytmusic.random_track(seed=xml)`), но
нигде не был виден и сменить его было нельзя; ссылка YouTube скачивалась молча и только
в момент сборки.

Стало: `music_mode`/`music_dir`/`music_src` — ключи СТИЛЯ (рядом с `music_db`) и ручки
панели стиля (группа «Аудио → Музыка»); цензура аудио — тоже ключ стиля (`censor`).
У клипа `job.music_override` (null = как в стиле) и `job.music_pick` (закреплённый трек
режима «случайно»). Ручки музыки правят ЛИБО переопределение ОТКРЫТОГО клипа («только
этот ролик», по умолчанию), ЛИБО сам стиль («всем роликам со стилем») — второй выбор
только и помечает стиль изменённым. Сборка и превью играют `music_pick` как файл —
второго независимого выбора нет; «Другой трек» берёт новый сид и исключает текущий
(`exclude`); ссылку скачивает кнопка «Скачать» (`/api/music_fetch`). Яркости клипа
(`aeexposure`/`exposure`) больше нет вовсе, а цензура берётся из стиля клипа.

Тесты:

1. миграция стиля без ключей музыки: режим `random`, остальное пусто — прежнее поведение;
2. `/api/music_random` с `exclude` не возвращает исключённый трек (сеть не трогается:
   `core.ytmusic` берётся настоящий, файлы лежат в `tmp_path`);
3. `/api/music_fetch` зовёт `ytmusic.resolve` (он подменён — сеть НЕ трогается);
4. `plan_audio` с пришедшим файлом НЕ зовёт `random_track` (иначе сборка играла бы не то,
   что играло превью);
5. node: ручки музыки в области «только этот ролик» пишут переопределение клипа и НЕ
   трогают стиль (мутация: пишут в стиль — тест краснеет); «всем роликам со стилем» пишут
   в стиль и снимают переопределение; скачанный трек уходит туда же по выбору;
6. папка музыки (одна лестница `musicPickDir` на сборку и превью): переопределение клипа
   -> папка стиля -> прежнее общее `aemusicdir` -> `<папка проекта>\\music`; папка XML не
   участвует нигде (регрессия 2026-10-06: клип из подпапки нарезки собирался без музыки);
7. стиль без ключа `music_mode` = `random`, как на сервере (мутация: `||'off'` — красный);
8. цензура из стиля доезжает до сборки (`_norm_build_jobs`: style.censor -> censor_audio),
   а яркости клипа в задании сборки больше нет (`exposure` не принимает ни `scene_plan`,
   ни `jobForBuild`).

Запуск: python -m pytest tests/test_music_style.py -q
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

STYLES_JS = os.path.join(ROOT, "static", "app", "95-styles.js")
# effMusic определена в 95-styles.js, а музыка клипа (musicJobFields/musicPickDir) — в
# 90-ae.js рядом с jobForBuild: их зовут и сборка, и тело плана сцены. Файлы грузятся
# одним <script>-скоупом, и тест режет функции из ОБОИХ — копии правил в тесте нет.
AE_JS = os.path.join(ROOT, "static", "app", "90-ae.js")

os.environ.setdefault("REELSI_NO_BROWSER", "1")

from core import styles  # noqa: E402
from core.xml2ae.plan_audio import AudioInputs, plan_audio  # noqa: E402

node = pytest.mark.skipif(not shutil.which("node"),
                          reason="контракт фронта требует node в PATH")

MUSIC_KEYS = ("music_mode", "music_dir", "music_src")


# ---------------------------------------------------------------------------
# 1. Миграция стиля без ключей музыки
# ---------------------------------------------------------------------------

def test_style_without_music_keys_gets_old_behaviour():
    """Старый стиль (ключей музыки нет) -> random и пустые папка/ссылка.

    «Random» — это прежнее поведение: случайный трек из папки музыки. Другой дефолт
    молча менял бы музыку во всех уже сохранённых шаблонах.
    """
    data = {"label": "старый", "font": "SFPro-CondensedSemibold"}
    out = styles.migrate_music(data)
    assert out["music_mode"] == "random", "старый стиль перестал быть «случайно»"
    assert out["music_dir"] == "" and out["music_src"] == "", "пустые папка/ссылка не заведены"
    # Стиль целиком (через resolve) видит те же значения — вторые дефолты не заводятся.
    res = styles.resolve({"label": "старый"})
    for k in MUSIC_KEYS:
        assert k in res, "в резолвнутом стиле нет ключа %s" % k
        assert res[k] == styles.BASE[k], "стиль разошёлся с BASE по %s" % k


def test_broken_music_mode_falls_back_to_random():
    """Незнакомая строка режима (правленный руками JSON) -> random, а не в сборку как есть."""
    assert styles.migrate_music({"music_mode": "spotify"})["music_mode"] == "random"
    assert styles.migrate_music({"music_mode": None})["music_mode"] == "random"
    for good in ("off", "random", "file", "url"):
        assert styles.migrate_music({"music_mode": good})["music_mode"] == good, (
            "рабочий режим %s подменён миграцией" % good)


def test_base_defaults_do_not_change_the_jsx(tmp_path):
    """Ключи музыки стиля не уезжают в .jsx: BASE с ними и без них даёт один текст.

    Иначе «музыка в стиле» поменяла бы вид ВСЕХ уже собранных проектов — а ключи эти
    решают, ОТКУДА взять трек, и в подстановку уходит уже готовый путь.
    """
    import gzip
    xml = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(xml, "wb") as f:
        shutil.copyfileobj(g, f)
    music = tmp_path / "track.mp3"
    music.write_bytes(b"ID3\x03\x00\x00\x00\x00\x00#dummy")

    from core import xml2ae

    def src(style):
        jsx, _, _ = xml2ae.to_ae_full(xml, return_source=True, style=style,
                                      music=str(music), music_db=-20.0,
                                      highlights=[0, 1], emit=lambda *a, **k: None)
        return jsx

    base_keys = {k: styles.BASE[k] for k in MUSIC_KEYS}
    plain = dict(styles.BASE)
    for k in MUSIC_KEYS:
        plain.pop(k, None)
    assert src(base_keys) == src(plain), (
        "ключи музыки стиля попали в .jsx — вид проектов изменился бы")


# ---------------------------------------------------------------------------
# 1б. Ручки музыки и цензуры — в схеме панели стиля
# ---------------------------------------------------------------------------

def _schema_fields():
    """Поля схемы по ключу — как их обходит панель."""
    from core import style_schema

    out = {}

    def walk(items):
        for it in items:
            if it.get("type") == "group":
                walk(it.get("items", []))
            elif it.get("type") == "field":
                out[it["key"]] = it

    for layer in style_schema.LAYERS:
        walk(layer.get("items", []))
    return out


def test_music_and_censor_knobs_live_in_the_panel_schema():
    """Режим, папка и файл/ссылка музыки — обычные ручки схемы, помеченные `track`.

    Раньше их ручки жили внизу шага 3, а ключи числились внешними (`EXTERNAL`) — ровно
    чтобы сторож «каждая ручка влияет на .jsx» их не видел. Теперь ручки в панели стиля,
    а сторож отличает их по флагу `track`: ручка выбирает трек, а на текст .jsx не влияет.
    Цензура аудио — тоже ручка схемы (галка), и в BASE она включена.
    """
    from core import style_schema

    fields = _schema_fields()
    for key in MUSIC_KEYS:
        assert key in fields, "ручки %s нет в схеме панели стиля" % key
        assert fields[key].get("track") is True, (
            "у ручки %s нет флага track — сторож .jsx не отличит её от ручки текста" % key)
        assert key not in style_schema.EXTERNAL, "ключ %s снова числится внешним" % key
    assert fields["music_mode"]["ctl"] == "select", "режим музыки — не список"
    assert [o[0] for o in fields["music_mode"]["options"]] == ["off", "random", "file", "url"], (
        "варианты режима музыки разъехались с сервером")
    assert fields["censor"]["ctl"] == "bool", "цензура — не галка"
    assert styles.BASE["censor"] is True, "цензура по умолчанию выключена"
    assert styles.resolve({})["censor"] is True, "стиль без ключа остался без цензуры"


# ---------------------------------------------------------------------------
# 1в. Цензура из стиля доезжает до сборки; яркости клипа в сборке нет
# ---------------------------------------------------------------------------

def _timeline(tmp_path):
    import gzip
    xml = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(xml, "wb") as f:
        shutil.copyfileobj(g, f)
    return xml


def test_censor_of_the_style_reaches_the_build(tmp_path):
    """`_norm_build_jobs` берёт цензуру из СТИЛЯ, а поле клипа больше не читается.

    Миграция: у клипа, где когда-то стояло `censor:false`, цензура снова будет по стилю
    (по умолчанию — включена). Это осознанное решение: у клипов одного стиля цензура одна.
    """
    from api import build as build_mod

    xml = _timeline(tmp_path)

    def censor_audio(job):
        jobs = build_mod._norm_build_jobs([dict(job, xml=xml)])
        return jobs[0]["censor_audio"]

    assert censor_audio({"style": {"censor": False}}) is False
    assert censor_audio({"style": {"censor": True}}) is True
    assert censor_audio({"style": {}}) is True, "дефолт стиля потерялся"
    assert censor_audio({"style": {"label": "старый"}}) is True, "стиль без ключа остался без цензуры"
    assert censor_audio({"censor": False}) is True, (
        "поле клипа censor снова управляет цензурой")


def test_censor_flag_changes_the_assembled_jsx(tmp_path):
    """Цензура доезжает до .jsx: снятая галка стиля убирает окна мьюта (var CENSOR=[])."""
    from core import xml2ae

    xml = _timeline(tmp_path)

    def src(censor_audio):
        jsx, _, _ = xml2ae.to_ae_full(xml, return_source=True, style={},
                                      censor_audio=censor_audio, emit=lambda *a, **k: None)
        return jsx

    off, on = src(False), src(True)
    assert off != on, "цензура не влияет на собранный .jsx"
    assert "var CENSOR=[];" in off, "снятая цензура не убрала окна мьюта"
    assert "var CENSOR=[[" in on, "включённая цензура не дала окон мьюта"


def test_clip_brightness_is_gone_from_the_assembly(tmp_path):
    """Яркость клипа убрана совсем: ни `scene_plan`, ни `to_ae_full` её не принимают.

    Значение подстановки `EXPOSURE` жёстко 0 = «не вешать»: ветка `if (EXPOSURE!=0)` в
    шаблоне не срабатывает никогда, и .jsx от стиля `lm_exposure` не зависит — Lumetri
    настраивается стилем («Камера 1/2 → Цвет»).
    """
    import inspect
    from core import xml2ae

    assert "exposure" not in inspect.signature(xml2ae.scene_plan).parameters, (
        "у плана сцены снова есть экспозиция клипа")
    assert "exposure" not in inspect.signature(xml2ae.to_ae_full).parameters, (
        "у сборки .jsx снова есть экспозиция клипа")
    xml = _timeline(tmp_path)
    with pytest.raises(TypeError):
        xml2ae.scene_plan(xml, exposure=0.5, emit=lambda *a, **k: None)
    jsx, _, _ = xml2ae.to_ae_full(xml, return_source=True, style={},
                                  emit=lambda *a, **k: None)
    assert "var EXPOSURE=0;" in jsx, "подстановка яркости не обнулена — сборка поехала бы"
    assert "var LUMETRI" not in jsx, "стиль без галки Lumetri вдруг получил цвет"


# ---------------------------------------------------------------------------
# 2-3. Роуты музыки
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    from flask import Flask

    import api

    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


H = {"Host": "127.0.0.1:5001"}


def _tracks(tmp_path, n=3):
    """Несколько треков в папке — как у владельца в папке музыки."""
    d = tmp_path / "music"
    d.mkdir(exist_ok=True)
    for i in range(n):
        (d / ("track_%d.m4a" % i)).write_bytes(b"ID3\x03\x00\x00\x00\x00\x00#x")
    return str(d)


def test_music_random_with_exclude_never_returns_the_excluded(client, tmp_path):
    """`exclude` исключает текущий трек — иначе «Другой трек» возвращал бы тот же файл.

    Выбор детерминированный (сид), поэтому без исключения кнопка выглядела бы сломанной.
    Проверяются ВСЕ треки папки: каждый по очереди исключается, и ни один ответ не
    совпадает с исключённым.
    """
    d = _tracks(tmp_path, 3)
    names = sorted(os.listdir(d))
    for name in names:
        res = client.post("/api/music_random", headers=H,
                          json={"dir": d, "seed": "C:/cut/01.xml", "exclude": os.path.join(d, name)})
        assert res.status_code == 200
        got = res.get_json()["path"]
        assert got, "с исключением ответ пуст, хотя треков три"
        assert os.path.basename(got).lower() != name.lower(), (
            "исключённый трек %s вернулся снова" % name)
    # Без exclude тот же сид отдаёт тот же файл — выбор детерминированный.
    a = client.post("/api/music_random", headers=H,
                    json={"dir": d, "seed": "C:/cut/01.xml"}).get_json()["path"]
    b = client.post("/api/music_random", headers=H,
                    json={"dir": d, "seed": "C:/cut/01.xml"}).get_json()["path"]
    assert a == b, "выбор трека перестал быть детерминированным по сиду"


def test_music_random_with_one_track_keeps_it(client, tmp_path):
    """В папке один трек — исключать нечего: отдаём его, а не пустоту.

    Пустой ответ на «Другой трек» читался бы как «треков нет» и врал бы.
    """
    d = _tracks(tmp_path, 1)
    name = os.listdir(d)[0]
    got = client.post("/api/music_random", headers=H,
                      json={"dir": d, "seed": "s", "exclude": os.path.join(d, name)}).get_json()["path"]
    assert got and os.path.basename(got) == name


def test_music_fetch_calls_resolve_and_does_not_touch_the_network(client, tmp_path, monkeypatch):
    """`/api/music_fetch` скачивает трек через `ytmusic.resolve` (подменён — сети нет).

    Роут обязан отдать путь скачанного файла И положить его в присланную папку: этот
    файл тут же становится кандидатом режима «случайно».
    """
    from core import ytmusic

    calls = []

    def fake_resolve(url, outdir, emit=None):
        calls.append({"url": url, "dir": outdir})
        p = os.path.join(outdir, "track_1.m4a")
        os.makedirs(outdir, exist_ok=True)
        with open(p, "wb") as f:
            f.write(b"ID3\x03\x00\x00\x00\x00\x00#x")
        return p

    monkeypatch.setattr(ytmusic, "resolve", fake_resolve)
    d = str(tmp_path / "mymusic")
    res = client.post("/api/music_fetch", headers=H,
                      json={"url": "https://youtube.com/watch?v=abc", "dir": d})
    assert res.status_code == 200
    data = res.get_json()
    assert data.get("ok") is True and data.get("path"), "роут не вернул путь скачанного файла"
    assert calls == [{"url": "https://youtube.com/watch?v=abc", "dir": d}], (
        "ytmusic.resolve позван не с той ссылкой/папкой: %r" % calls)
    assert os.path.isfile(data["path"]), "скачанный файл не лёг в папку музыки"


def test_music_fetch_without_url_is_a_clear_error(client, monkeypatch):
    """Пустая ссылка — понятная ошибка, а не попытка скачать ничего."""
    from core import ytmusic

    called = []
    monkeypatch.setattr(ytmusic, "resolve", lambda *a, **k: called.append(a))
    res = client.post("/api/music_fetch", headers=H, json={"url": "  ", "dir": "C:/music"})
    assert not called, "роут полез скачивать пустую ссылку"
    body = res.get_json()
    assert body.get("error"), "пустая ссылка не дала ошибки"


# ---------------------------------------------------------------------------
# 4. plan_audio: пришедший файл — не повод выбирать случайный
# ---------------------------------------------------------------------------

def _audio_inputs(**kw):
    """Минимальные входы plan_audio: проверяется только выбор трека.

    Пустые интро/вставки/субтитры — так звуков SFX не заводится, и тест не зависит
    от ассетов и цензуры.
    """
    from core.xml2ae.plan_style import read_style

    base = dict(intro_groups=[], any_glitch=False, inserts=[], subs=[], hl=set(),
                cam_change_sec=[], fps=60.0, style=read_style({}), aset=lambda role: "",
                intro_riser=False, music=None, music_random=False, music_dir=None,
                base="", xml_path="C:/cut/01.xml", censor_source=[], censor_audio=False,
                censor_fps=60.0, voice_src="", voice_segments=[], music_db=-20.0,
                ckpt=lambda stage: None, emit=lambda *a, **k: None)
    base.update(kw)
    return AudioInputs(**base)


def test_plan_audio_with_a_file_never_picks_a_random_track(tmp_path, monkeypatch):
    """Пришёл файл (`music` непустой) — `random_track` НЕ зовётся.

    Цена ошибки: превью играет закреплённый трек, а сборка выбирает свой случайный —
    в проекте звучит не то, что человек слышал.
    """
    from core import ytmusic

    d = _tracks(tmp_path, 3)
    music = os.path.join(d, "track_1.m4a")
    calls = []
    monkeypatch.setattr(ytmusic, "random_track", lambda *a, **k: calls.append(a))

    out = plan_audio(_audio_inputs(music=music, music_random=False, music_dir=d))
    assert out.music_path == os.path.abspath(music), (
        "сборка взяла не присланный файл: %r" % out.music_path)
    assert not calls, "сборка выбрала случайный трек, хотя файл уже закреплён"


def test_plan_audio_random_without_a_pick_still_picks(tmp_path, monkeypatch):
    """Обратная сторона: режим «случайно» и трека нет — сборка выбирает сама (как раньше)."""
    from core import ytmusic

    d = _tracks(tmp_path, 3)
    picked = os.path.join(d, "track_2.m4a")
    calls = []

    def fake_random(outdir, emit=None, seed=None, exclude=None):
        calls.append({"dir": outdir, "seed": seed, "exclude": exclude})
        return picked

    monkeypatch.setattr(ytmusic, "random_track", fake_random)
    out = plan_audio(_audio_inputs(music="", music_random=True, music_dir=d))
    assert out.music_path == picked, "случайный трек не выбран вовсе"
    assert calls and calls[0]["dir"] == d, "папка музыки не доехала до выбора: %r" % calls


def test_plan_audio_never_invents_the_folder_from_the_xml(tmp_path, monkeypatch):
    """Папку музыки сервер берёт присланную, а не выдумывает из пути XML.

    `base` приходит сюда из `plan_assets` (`base or _project_base(xml_path)`), а тот
    отдаёт папку самого XML, если она не похожа на папку вывода: безусловный
    `os.path.join(base, "music")` искал треки в подпапке нарезки — клип из `repro_big`
    так и собрался без музыки (2026-10-06). Присланную папку (её считает интерфейс,
    musicPickDir) сервер обязан слушаться; папка XML не участвует ни в одном случае.
    """
    from core import ytmusic

    calls = []

    def fake_random(outdir, **kw):
        calls.append(outdir)
        return ""

    monkeypatch.setattr(ytmusic, "random_track", fake_random)
    d = _tracks(tmp_path, 2)
    plan_audio(_audio_inputs(music="", music_random=True, music_dir=d))
    assert calls == [d], "сервер искал треки не в присланной папке: %r" % calls
    # base — папка самого XML (боевая сборка: XML лёг в подпапку нарезки): дефолт по ней
    # повторял бы регрессию, поэтому папки нет вовсе — и падать на этом нечему.
    calls.clear()
    xml_dir = str(tmp_path / "repro_big")
    os.makedirs(xml_dir, exist_ok=True)
    out = plan_audio(_audio_inputs(music="", music_random=True, base=xml_dir,
                                   xml_path=os.path.join(xml_dir, "01.xml")))
    assert calls == [""], "сервер выдумал папку музыки рядом с XML: %r" % calls
    assert out.music_path == "", "музыки нет, а путь появился: %r" % out.music_path
    # Папка проекта (не папка XML) — законная запасная ступень.
    calls.clear()
    plan_audio(_audio_inputs(music="", music_random=True, base=str(tmp_path),
                             xml_path=os.path.join(xml_dir, "01.xml")))
    assert calls == [os.path.join(str(tmp_path), "music")], (
        "папка проекта не сработала запасной ступенью: %r" % calls)


def test_plan_audio_without_music_has_no_track_and_no_stage(tmp_path, monkeypatch):
    """Режим «без музыки» (ни файла, ни random) — ни пути, ни случайного выбора."""
    from core import ytmusic

    stages = []
    monkeypatch.setattr(ytmusic, "random_track", lambda *a, **k: pytest.fail("выбран трек"))
    monkeypatch.setattr(ytmusic, "resolve", lambda *a, **k: pytest.fail("скачан трек"))
    out = plan_audio(_audio_inputs(ckpt=stages.append))
    assert out.music_path == "", "музыки нет, а путь появился: %r" % out.music_path
    assert "музыка" not in stages, "этап «музыка» объявлен без музыки"


# ---------------------------------------------------------------------------
# 5. Node: блок музыки клипа
# ---------------------------------------------------------------------------

def _src():
    return io.open(STYLES_JS, encoding="utf-8").read()


def _func(src, name):
    m = re.search(r"(?:async\s+)?function\s+%s\s*\(" % re.escape(name), src)
    assert m, "в static/app не нашлась функция %s" % name
    i = src.index("{", m.end() - 1)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():j + 1]
    raise AssertionError("не сошлись скобки у %s" % name)


def _func_any(name):
    """Тело боевой функции из того файла, где она объявлена (95-styles или 90-ae)."""
    for path in (STYLES_JS, AE_JS):
        try:
            return _func(io.open(path, encoding="utf-8").read(), name)
        except AssertionError:
            continue
    raise AssertionError("функция %s не нашлась ни в 95-styles.js, ни в 90-ae.js" % name)


NODE_STUBS = r"""
const ELS={};
function $(id){return ELS[id]||(ELS[id]={id:id,value:'',textContent:'',
  style:{},disabled:false,innerHTML:''});}
function val(id){return ELS[id]?ELS[id].value:'';}
function t(s,vars){return String(s).replace(/\{(\w+)\}/g,(m,k)=>String((vars||{})[k]));}
function saveState(){}
function toast(m){TOASTS.push(String(m));}
function uiLog(m){}
function errText(d){return String((d&&(d.error||d))||'');}
function stEdit(){EDITS++;}
// Дверь спикера клипа живёт в 40-queue.js: здесь внешняя дверь вырезанных функций
// (jobForBuild/musicPickDir ходят за папками и стилем клипа).
function clipSpeaker(c){return (c&&c.job&&c.job.speaker)||'';}
function styleKeyFor(j){return (j&&j.styleKey)||null;}
function nCams(){return 2;}
const TOASTS=[];let EDITS=0;
"""


def _run_node(tmp_path, name, funcs, body):
    script = (NODE_STUBS + "\nconst CLIPS=[];let curAE=-1;let CURSTYLE={};\n"
              # Стили и их ключи музыки: у клипа стиль берётся У НЕГО (clipStyleObj),
              # а не из показанного в панели CURSTYLE.
              "const STYLES={};\n"
              "const SPEAKERS={};\n"
              # Область правки музыки и флаг занятости кнопки живут в 95-styles.js
              # (`let`-объявления, а не функции) — в стенде их объявляем сами.
              "let MUSIC_SCOPE='clip',MUSIC_REROLL_BUSY=false;\n"
              # Прежнее общее значение «папка музыки»: живёт в памяти (99-boot.js), поля
              # в разметке нет — боевая реализация читает его этой же переменной.
              "let AEMUSICDIR='';\n"
              + "\n".join(_func_any(f) for f in funcs) + "\n" + body)
    path = str(tmp_path / name)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(script)
    p = subprocess.run(["node", path], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=60)
    assert p.returncode == 0, (p.stderr or p.stdout).strip()[:900]
    return json.loads(p.stdout.strip().splitlines()[-1])


MUSIC_FUNCS = ["xmlDirOf", "clipStyleObj", "effMusic", "musicTrack", "musicTrackName",
               "musicFieldValue", "musicFieldEdit", "musicRevert", "musicScope", "musicClip",
               "clipCensor", "musicSync", "musicClipUI", "musicReroll", "musicDownload",
               "musicPickEnsure", "jobsMusicDir", "pickAudioInto", "effOutdir",
               "musicPickDir", "defJob", "styleForJob", "clipNcams", "musicJobFields",
               "jobForBuild"]


@node
def test_clip_music_override_wins_over_the_style(tmp_path):
    """`music_override=null` — режим и ссылка из стиля; с override — свои.

    Это и есть «у клипа можно поменять»: правка клипа не трогает стиль, а клип без
    правки живёт по стилю.
    """
    out = _run_node(tmp_path, "music_override.js", MUSIC_FUNCS, r"""
CURSTYLE={music_mode:'file',music_dir:'C:/stylemusic',music_src:'C:/stylemusic/a.m4a'};
CLIPS.push({xml:'C:/cut/01.xml',job:{}});curAE=0;
const st=musicJobFields(CLIPS[0]);
CLIPS[0].job.music_override={mode:'file',src:'C:/own/b.m4a'};
const own=musicJobFields(CLIPS[0]);
CLIPS[0].job.music_override={mode:'random',src:''};CLIPS[0].job.music_pick='C:/stylemusic/c.m4a';
const rnd=musicJobFields(CLIPS[0]);
CLIPS[0].job.music_override={mode:'off',src:''};
const off=musicJobFields(CLIPS[0]);
console.log(JSON.stringify({st,own,rnd,off}));
""")
    assert out["st"] == {"music": "C:/stylemusic/a.m4a", "music_random": False,
                         "music_dir": "C:/stylemusic"}, (
        "клип без переопределения не взял музыку стиля: %r" % out["st"])
    assert out["own"]["music"] == "C:/own/b.m4a" and out["own"]["music_random"] is False, (
        "свой трек клипа не уехал в сборку: %r" % out["own"])
    assert out["rnd"] == {"music": "C:/stylemusic/c.m4a", "music_random": False,
                          "music_dir": "C:/stylemusic"}, (
        "закреплённый случайный трек ушёл не как файл: %r" % out["rnd"])
    assert out["off"] == {"music": "", "music_random": False,
                          "music_dir": ""}, (
        "«без музыки» всё ещё выбирает трек: %r" % out["off"])


@node
def test_music_folder_ladder_clip_style_then_the_old_shared_value(tmp_path):
    """Папка музыки: переопределение клипа → стиль → прежнее общее `aemusicdir`.

    Стиль без `music_dir` (и без переопределения у клипа) обязан брать прежнее общее
    значение: у владельца в нём рабочая папка с 21 треком, и без этой ступени клип
    уходил бы за треками в другое место. Папка XML не участвует ни на одной ступени.
    """
    out = _run_node(tmp_path, "music_dir_ladder.js", MUSIC_FUNCS, r"""
$('base').value='C:/proj';
CURSTYLE={music_mode:'random',music_dir:'',music_src:''};   // папки в стиле нет
CLIPS.push({xml:'C:/proj/_tools_claude/repro_big/01.xml',job:{}});curAE=0;
AEMUSICDIR='C:/shared/music';
const legacy=musicPickDir(CLIPS[0]);
const legacyFields=musicJobFields(CLIPS[0]);
CLIPS[0].job.music_override={mode:'random',dir:'C:/own/dir'};
const own=musicPickDir(CLIPS[0]);
CLIPS[0].job.music_override=null;
CURSTYLE.music_dir='C:/style/music';
const style=musicPickDir(CLIPS[0]);
console.log(JSON.stringify({legacy,legacyDir:legacyFields.music_dir,own,style}));
""")
    assert out["legacy"] == "C:/shared/music", (
        "прежнее общее значение папки музыки потерялось: %r" % out["legacy"])
    assert out["legacyDir"] == "C:/shared/music", (
        "в сборку (music_dir) ушла не та папка, что зовёт лестница: %r" % out["legacyDir"])
    assert out["own"] == "C:/own/dir", (
        "папка переопределения клипа не старшая ступень: %r" % out["own"])
    assert out["style"] == "C:/style/music", (
        "папка стиля не подхватилась: %r" % out["style"])
    assert "repro_big" not in json.dumps(out), (
        "папка XML вернулась в лестницу: %r" % out)


@node
def test_music_folder_defaults_to_the_project_folder_not_the_xml(tmp_path):
    """Без папки в стиле и без `aemusicdir` папка музыки — `<папка проекта>\\music`.

    Регрессия 2026-10-06: дефолтом стала папка XML, и клип из подпапки нарезки
    (`repro_big`) искал треки в `repro_big/music` — сборка уходила БЕЗ музыки, хотя
    треки лежат в папке проекта. Папку XML не отдаёт ни одна ступень лестницы, и
    «Другой трек»/«Скачать» берут её той же дверью (jobsMusicDir), а не своей копией.
    """
    out = _run_node(tmp_path, "music_dir_project.js", MUSIC_FUNCS, r"""
$('base').value='C:/proj/';
CURSTYLE={music_mode:'random',music_dir:'',music_src:''};
CLIPS.push({xml:'C:/proj/_tools_claude/repro_big/01.xml',job:{}});curAE=0;
AEMUSICDIR='';
const project=musicPickDir(CLIPS[0]);
const projectDir=musicJobFields(CLIPS[0]).music_dir;
const shown=jobsMusicDir(CLIPS[0]);
$('base').value='';                       // папки проекта тоже нет
const noBase=musicPickDir(CLIPS[0]);
console.log(JSON.stringify({project,projectDir,shown,noBase}));
""")
    assert out["project"] == "C:/proj\\music", (
        "дефолт папки музыки — не папка проекта: %r" % out["project"])
    assert out["projectDir"] == "C:/proj\\music", (
        "в сборку (music_dir) ушла не папка проекта: %r" % out["projectDir"])
    assert out["shown"] == "C:/proj\\music", (
        "«Другой трек»/«Скачать» ищут треки не там, где сборка: %r" % out["shown"])
    assert out["noBase"] == "music", (
        "без папки проекта потерялся прежний относительный дефолт: %r" % out["noBase"])
    assert "repro_big" not in json.dumps(out), (
        "папка XML вернулась в лестницу: %r" % out)


@node
def test_random_mode_without_a_pick_lets_the_build_pick(tmp_path):
    """Режим «случайно» и закреплённого трека нет -> в сборку уходит music_random.

    Так собирались клипы ДО этого задания: выбор идёт сидом по пути XML, и музыка
    старых клипов от переезда не меняется.
    """
    out = _run_node(tmp_path, "music_random_flag.js", MUSIC_FUNCS, r"""
CURSTYLE={music_mode:'random',music_dir:'C:/stylemusic',music_src:''};
CLIPS.push({xml:'C:/cut/01.xml',job:{}});curAE=0;
const before=musicJobFields(CLIPS[0]);
CLIPS[0].job.music_pick='C:/stylemusic/picked.m4a';
const after=musicJobFields(CLIPS[0]);
console.log(JSON.stringify({before,after}));""")
    assert out["before"]["music_random"] is True and out["before"]["music"] == "", (
        "без закреплённого трека сборка не выберет случайный: %r" % out["before"])
    assert out["after"]["music_random"] is False and out["after"]["music"].endswith("picked.m4a"), (
        "закреплённый трек уехал не файлом: %r" % out["after"])


@node
def test_reroll_changes_the_pick(tmp_path):
    """«Другой трек»: запрос с новым сидом и исключением — `music_pick` меняется.

    Проверяется и сам запрос (сид, exclude, папка), и то, что новый трек записан в
    клип: иначе кнопка мигала бы, а играл прежний файл.
    """
    out = _run_node(tmp_path, "music_reroll.js", MUSIC_FUNCS, r"""
CURSTYLE={music_mode:'random',music_dir:'C:/m',music_src:''};
CLIPS.push({xml:'C:/cut/01.xml',job:{music_pick:'C:/m/one.m4a'}});curAE=0;
const CALLS=[];
globalThis.fetch=async (url,opt)=>{const body=JSON.parse(opt.body);
  CALLS.push({url,body});
  const same=body.seed==='C:/cut/01.xml';
  return {json:async()=>({path:same?'C:/m/one.m4a':'C:/m/two.m4a'})};};
(async()=>{
  await musicReroll();
  const pick=CLIPS[0].job.music_pick;
  console.log(JSON.stringify({pick,calls:CALLS.length,excluded:CALLS[0].body.exclude,
    lastExcluded:CALLS[CALLS.length-1].body.exclude,
    url:CALLS[0].url,saved:TOASTS,bytes:CLIPS[0].job.music_pick.length>0}));
})();
""")
    assert out["pick"].endswith("two.m4a"), (
        "«Другой трек» не сменил закреплённый трек: %r" % out["pick"])
    assert out["excluded"] == "C:/m/one.m4a", (
        "прежний трек не исключён из выбора: %r" % out["excluded"])
    assert out["url"] == "/api/music_random", "кнопка стучится не в тот роут: %r" % out["url"]
    assert out["calls"] >= 2, (
        "кнопка не перевыбрала трек, когда первый ответ вернул исключённый: %r" % out["calls"])


@node
def test_clip_scope_writes_the_override_and_leaves_the_style_alone(tmp_path):
    """«Только этот ролик» (по умолчанию) пишет переопределение клипа, а не стиль.

    Это и есть решение владельца: выбрал файл у ролика — стиль не меняется и не
    помечается изменённым, у остальных роликов стиля остаётся то, что в стиле (например,
    «случайно из папки»). Признак правки стиля — вызов stEdit (стиль помечается
    изменённым именно там): в области клипа его быть не должно, а сам CURSTYLE обязан
    остаться нетронутым.

    Мутация: начать писать в стиль (stEdit / запись в CURSTYLE) — тест краснеет.
    """
    out = _run_node(tmp_path, "music_clip_scope.js", MUSIC_FUNCS, r"""
CURSTYLE={music_mode:'random',music_dir:'C:/stylemusic',music_src:''};
STYLES.mak={music_mode:'random',music_dir:'C:/stylemusic',music_src:''};
CLIPS.push({xml:'C:/cut/01.xml',job:{styleKey:'mak'}});curAE=0;
$('st_music_mode').value='file';$('st_music_dir').value='C:/own';
$('st_music_src').value='C:/own/track.m4a';
MUSIC_SCOPE='clip';
musicFieldEdit();
const styleKeys=JSON.parse(JSON.stringify(CURSTYLE));
const ov=JSON.parse(JSON.stringify(CLIPS[0].job.music_override));
const fields={mode:musicFieldValue('music_mode'),src:musicFieldValue('music_src')};
const stJobs=musicJobFields(CLIPS[0]);
// Переопределение клипа важнее стиля — и в полях, и в том, что уходит в сборку.
console.log(JSON.stringify({styleKeys,ov,fields,stJobs,edits:EDITS,
  styleUntouched:JSON.stringify(CURSTYLE)===JSON.stringify(styleKeys)}));
""")
    assert out["ov"] == {"mode": "file", "src": "C:/own/track.m4a", "dir": "C:/own"}, (
        "правка «только этот ролик» не завела переопределение клипа: %r" % out["ov"])
    assert out["edits"] == 0, (
        "правка трека ролика позвала stEdit — стиль помечен изменённым: %r" % out["edits"])
    assert out["styleUntouched"] and out["styleKeys"]["music_mode"] == "random" \
        and out["styleKeys"]["music_src"] == "", (
        "стиль изменился от правки трека одного ролика: %r" % out["styleKeys"])
    assert out["fields"] == {"mode": "file", "src": "C:/own/track.m4a"}, (
        "поля панели не показывают трек ролика: %r" % out["fields"])
    assert out["stJobs"]["music"] == "C:/own/track.m4a", (
        "в сборку ушёл не трек ролика: %r" % out["stJobs"])


@node
def test_style_scope_writes_the_style_and_drops_the_clip_override(tmp_path):
    """«Всем роликам со стилем» пишет в стиль и снимает переопределение клипа.

    Иначе открытый клип продолжал бы играть своё, а человек думал бы, что сменил всем.
    """
    out = _run_node(tmp_path, "music_style_scope.js", MUSIC_FUNCS, r"""
CURSTYLE={music_mode:'random',music_dir:'C:/stylemusic',music_src:''};
STYLES.mak={music_mode:'random',music_dir:'C:/stylemusic',music_src:''};
CLIPS.push({xml:'C:/cut/01.xml',job:{styleKey:'mak',
  music_override:{mode:'file',src:'C:/own/old.m4a'}}});curAE=0;
$('st_music_mode').value='file';$('st_music_dir').value='C:/stylemusic';
$('st_music_src').value='C:/stylemusic/new.m4a';
MUSIC_SCOPE='style';
musicFieldEdit();
// captureAE (боевая дверь stEdit) унёс бы правленый стиль в задание клипа копией —
// здесь стенд его зовёт заглушкой, поэтому проверяем оба конца: сам стиль и то, что
// клип со стилем-копией берёт новый трек.
const stCopy=JSON.parse(JSON.stringify(CURSTYLE));
const jobs=musicJobFields({xml:'C:/cut/01.xml',job:{style:stCopy}});
console.log(JSON.stringify({mode:CURSTYLE.music_mode,src:CURSTYLE.music_src,
  dir:CURSTYLE.music_dir,ov:CLIPS[0].job.music_override,edits:EDITS,jobs:jobs}));
""")
    assert out["mode"] == "file" and out["src"] == "C:/stylemusic/new.m4a", (
        "«всем роликам со стилем» не записало трек в стиль: %r" % out)
    assert out["ov"] is None, (
        "переопределение клипа осталось и перекрыло бы стиль: %r" % out["ov"])
    assert out["edits"] == 1, "правка стиля не помечена изменённой (stEdit не позван)"
    assert out["jobs"]["music"] == "C:/stylemusic/new.m4a", (
        "клип не взял новый трек стиля: %r" % out["jobs"])


@node
def test_downloaded_track_goes_to_the_clip_or_to_the_style_by_scope(tmp_path):
    """Скачанный трек уходит в переопределение клипа или в стиль — по выбору области."""
    out = _run_node(tmp_path, "music_download.js", MUSIC_FUNCS, r"""
CURSTYLE={music_mode:'url',music_dir:'',music_src:'https://youtu.be/abc'};
STYLES.mak={music_mode:'url',music_dir:'',music_src:'https://youtu.be/abc'};
CLIPS.push({xml:'C:/cut/01.xml',job:{styleKey:'mak'}});curAE=0;
$('st_music_mode').value='url';$('st_music_src').value='https://youtu.be/abc';
globalThis.fetch=async (url,opt)=>({json:async()=>({ok:true,path:'C:/dl/track.m4a'})});
(async()=>{
  MUSIC_SCOPE='clip';
  await musicDownload();
  const clipOv=JSON.parse(JSON.stringify(CLIPS[0].job.music_override));
  const clipStyleSrc=CURSTYLE.music_src, editsAfterClip=EDITS;
  CLIPS.push({xml:'C:/cut/02.xml',job:{styleKey:'mak'}});curAE=1;
  MUSIC_SCOPE='style';
  await musicDownload();
  console.log(JSON.stringify({clipOv,clipStyleSrc,editsAfterClip,
    styleSrc:CURSTYLE.music_src,ov:CLIPS[1].job.music_override,edits:EDITS}));
})();
""")
    assert out["clipOv"]["src"] == "C:/dl/track.m4a" and out["clipOv"]["mode"] == "file", (
        "скачанный трек не ушёл в переопределение клипа: %r" % out["clipOv"])
    assert out["clipStyleSrc"] == "https://youtu.be/abc" and out["editsAfterClip"] == 0, (
        "скачивание в области «этот ролик» тронуло стиль: %r" % out)
    assert out["styleSrc"] == "C:/dl/track.m4a", (
        "скачанный трек не ушёл в стиль: %r" % out["styleSrc"])
    assert out["ov"] is None and out["edits"] == 1, out


@node
def test_style_without_mode_is_random_like_the_server(tmp_path):
    """Стиль без ключа `music_mode` = «случайно», как дефолт на сервере.

    Мутация: вернуть в effMusic `||'off'` — тест краснеет (владелец видел «рандома
    больше нет»: у стиля без ключа музыка выключалась, хотя на сервере было random).
    """
    out = _run_node(tmp_path, "music_default.js", MUSIC_FUNCS, r"""
STYLES.old={music_dir:'C:/m'};                    // ключа music_mode в стиле нет вовсе
CLIPS.push({xml:'C:/cut/01.xml',job:{styleKey:'old',music_pick:'C:/m/one.m4a'}});curAE=0;
const m=effMusic(CLIPS[0]);
const noStyle=effMusic({xml:'C:/cut/02.xml',job:{}});   // и вовсе без стиля
console.log(JSON.stringify({mode:m.mode,src:m.src,noStyle:noStyle.mode}));
""")
    assert out["mode"] == "random", (
        "стиль без ключа режима выключил музыку вместо «случайно»: %r" % out["mode"])
    assert out["src"] == "C:/m/one.m4a", out
    assert out["noStyle"] == "random", out


@node
def test_style_censor_of_the_clip_is_what_the_build_gets(tmp_path):
    """Цензура берётся у стиля КЛИПА: у двух клипов разных стилей она своя.

    Клип несёт только имя стиля; стиль задаёт и музыку, и цензуру. Второй клип того же
    набора не должен получить чужое решение.
    """
    out = _run_node(tmp_path, "music_censor.js", MUSIC_FUNCS, r"""
STYLES.loud={music_mode:'off',censor:false};
STYLES.quiet={music_mode:'off',censor:true};
CLIPS.push({xml:'C:/cut/01.xml',job:{styleKey:'loud',censor:true}});   // старьё клипа: не читаем
CLIPS.push({xml:'C:/cut/02.xml',job:{styleKey:'quiet'}});
console.log(JSON.stringify({a:clipCensor(CLIPS[0]),b:clipCensor(CLIPS[1]),
  none:clipCensor({xml:'C:/cut/03.xml',job:{}}),
  job:jobForBuild(CLIPS[1])}));
""")
    assert out["a"] is False and out["b"] is True, (
        "цензура взята не у стиля клипа: %r" % out)
    assert out["none"] is True, (
        "клип без стиля потерял цензуру по умолчанию (включена): %r" % out["none"])
    assert "censor" not in out["job"], (
        "цензура снова уезжает полем клипа в задание сборки: %r" % out["job"])
    assert "exposure" not in out["job"], (
        "яркость снова уезжает в задание сборки: %r" % out["job"])


@node
def test_build_job_has_no_exposure_and_no_clip_censor(tmp_path):
    """В задании сборки нет ни яркости, ни цензуры: и то, и другое решено стилем.

    Яркость убрана совсем (Lumetri настраивается стилем), цензура — ключ стиля и
    считается на сервере из стиля (`_norm_build_jobs`), а не полем клипа.
    """
    out = _run_node(tmp_path, "music_job.js", MUSIC_FUNCS, r"""
STYLES.mak={music_mode:'file',music_dir:'C:/m',music_src:'C:/m/a.m4a',censor:false};
CLIPS.push({xml:'C:/cut/01.xml',job:{styleKey:'mak'}});curAE=0;
const j=jobForBuild(CLIPS[0]);
const dj=defJob();
console.log(JSON.stringify({keys:Object.keys(j).sort(),
  defKeys:Object.keys(dj).sort(),job:j}));
""")
    assert "exposure" not in out["keys"], "яркость вернулась в задание сборки: %r" % out["keys"]
    assert "censor" not in out["keys"], "цензура вернулась полем клипа: %r" % out["keys"]
    assert "exposure" not in out["defKeys"] and "censor" not in out["defKeys"], (
        "поля яркости/цензуры вернулись в задание клипа: %r" % out["defKeys"])
    assert out["job"]["music"] == "C:/m/a.m4a", out


@node
def test_pinned_track_survives_reopening_the_clip(tmp_path):
    """Повторное открытие клипа не перевыбирает закреплённый трек.

    Подпись «режим|папка» ставит и показ блока, и добор трека — и ОДНОЙ лестницей
    (musicPickDir). Считай показ по одной ступени (папка стиля), а добор — по другой
    (<папка проекта>\\music), и они не совпали бы: трек сбрасывался бы и выбирался
    заново на каждом открытии клипа.
    """
    out = _run_node(tmp_path, "music_reopen.js", MUSIC_FUNCS, r"""
CURSTYLE={music_mode:'random',music_dir:'',music_src:''};  // папка стиля пуста — работает папка проекта
$('base').value='C:/cut';
CLIPS.push({xml:'C:/cut/01.xml',job:{music_pick:'C:/cut/music/one.m4a'}});curAE=0;
const CALLS=[];
globalThis.fetch=async (url,opt)=>{CALLS.push(JSON.parse(opt.body));
  return {json:async()=>({path:'C:/cut/music/two.m4a'})};};
(async()=>{
  musicSync(CLIPS[0]);musicClipUI();          // клип открыли — показ и подпись
  const pickAfterOpen=CLIPS[0].job.music_pick;
  musicClipUI();musicClipUI();                // перерисовали ещё дважды
  const pickAfterRerender=CLIPS[0].job.music_pick;
  await musicPickEnsure(CLIPS[0]);            // добор: трек уже есть — запроса быть не должно
  const pickAfterEnsure=CLIPS[0].job.music_pick;
  console.log(JSON.stringify({pickAfterOpen,pickAfterRerender,pickAfterEnsure,
    calls:CALLS.length,sig:CLIPS[0].job.music_sig}));
})();
""")
    assert out["pickAfterOpen"] == "C:/cut/music/one.m4a", "показ блока сбросил закреплённый трек"
    assert out["pickAfterRerender"] == "C:/cut/music/one.m4a", (
        "перерисовка блока сбросила закреплённый трек: %r" % out["pickAfterRerender"])
    assert out["pickAfterEnsure"] == "C:/cut/music/one.m4a", (
        "добор перевыбрал трек, хотя он уже закреплён: %r" % out["pickAfterEnsure"])
    assert out["calls"] == 0, "лишний запрос за треком: %r" % out["calls"]


@node
def test_pickers_write_where_the_scope_points(tmp_path):
    """Кнопки «Файл…» и «Выбрать…» доводят выбор туда же, куда ручной ввод.

    Выбор звукового файла и папки треков идёт нативными диалогами (`/api/pickaudio`,
    `/api/pickdir`), а значение пишет ОДНА дверь правки музыки (musicFieldEdit). В области
    «только этот ролик» (по умолчанию) это переопределение клипа, и стиль не трогается:
    иначе «выбрал файл — а изменился стиль всем».
    """
    out = _run_node(tmp_path, "music_pickers.js", MUSIC_FUNCS + ["pickdir"], r"""
CURSTYLE={music_mode:'random',music_dir:'C:/m1',music_src:''};
STYLES.mak={music_mode:'random',music_dir:'C:/m1',music_src:''};
CLIPS.push({xml:'C:/cut/01.xml',job:{styleKey:'mak'}});curAE=0;
MUSIC_SCOPE='clip';
const CALLS=[];
globalThis.fetch=async (url,opt)=>{
  CALLS.push(url);
  if(url==='/api/pickaudio')return {json:async()=>({path:'C:/own/pick.m4a'})};
  if(url==='/api/pickdir')return {json:async()=>({path:'C:/m3'})};
  return {json:async()=>({path:''})};
};
(async()=>{
  await pickAudioInto('st_music_src');
  const afterFile=JSON.parse(JSON.stringify(CLIPS[0].job.music_override));
  await pickdir('st_music_dir');
  console.log(JSON.stringify({afterFile,
    ov:JSON.parse(JSON.stringify(CLIPS[0].job.music_override)),
    styleDir:CURSTYLE.music_dir,edits:EDITS,calls:CALLS}));
})();
""")
    assert out["afterFile"]["src"] == "C:/own/pick.m4a", (
        "«Файл…» не записал путь трека в переопределение клипа: %r" % out["afterFile"])
    assert out["ov"]["dir"] == "C:/m3", (
        "«Выбрать…» у папки треков не довёл папку до переопределения: %r" % out["ov"])
    assert out["styleDir"] == "C:/m1" and out["edits"] == 0, (
        "выбор файла/папки в области «этот ролик» тронул стиль: %r" % out)
    assert "/api/pickaudio" in out["calls"] and "/api/pickdir" in out["calls"], (
        "кнопки зовут не нативные диалоги: %r" % out["calls"])
