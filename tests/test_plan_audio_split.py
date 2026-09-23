# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сторож распила scene_plan: звук (этап 4).

Звук уехал из `scene_plan` в `core/xml2ae/plan_audio.py` одной дверью `plan_audio`:
SFX (поп жёлтых, глитч по группам подряд идущих слов, whoosh и переход на катах, ризер),
их обрезка / точка удара / громкость (`<звук>_in/_out/_at/_db`), музыка, цензура голоса и
данные звука для шаблона.

Сторож держит СТЫК: дверь, вызванная НАПРЯМУЮ на фикстуре, обязана отдать ровно то, что
`scene_plan` кладёт в план (`plan["audio"]`) и в шаблон (CENSOR/RISER/POP/GLITCH/TRANS/
TRANS_SFX/VOICE_DB/MUSIC_DB/AUDIO_FADE, старт и хвосты слоёв звуков, блок слоёв глитча).
Разъедутся — предпросмотр сыграет не то, что соберёт AE, а увидеть это можно только в AE.

Входы собираются здесь ТАК ЖЕ, как их собирает `scene_plan` до вызова блока: разбор XML,
резолв стиля, группы интро, тайминги вставок (по ним звук решает, есть ли видеовставка),
индексы жёлтых, точки смены камеры и резолвер ассетов. Своей копии арифметики звука тут нет
намеренно — иначе сторож проверял бы копию правила. Общее с другими блоками не копируется:
`_sv`/`_sv_or` берутся из build, окна мьюта — из `layout._censor_windows`, точки смены
камеры — из `layout._cam_change_frames`.

Запуск: python -m pytest tests/test_plan_audio_split.py -q
"""
import gzip
import json
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import assets, styles, xml2ae  # noqa: E402
from core.xml2ae import build as build_mod  # noqa: E402
from core.xml2ae.build import read_style  # noqa: E402
from core.xml2ae.jsutil import _jd, _js, _r  # noqa: E402
from core.xml2ae.layout import _cam_change_frames, _project_base  # noqa: E402
from core.xml2ae.plan_audio import AudioInputs, plan_audio  # noqa: E402
from core.xml2ae.plan_inserts import (InsertTimingInputs,  # noqa: E402
                                      plan_insert_timings)
from core.xml2ae.plan_intro import _g_at  # noqa: E402

VIDEO = "C:/x/v.mp4"            # файла нет: тип вставки решает расширение, размеры не нужны

# Глитч-разметка: два слова ПОДРЯД (одна группа звука) и третье отдельно (своя группа)
GLITCH_INTRO = [
    {"words": ["ГЛИТЧ"], "color": "yellow", "anim": "glitch", "times": [1.5]},
    {"words": ["ЕЩЁ"], "color": "yellow", "anim": "glitch", "times": [1.62]},
    {"words": ["ПОЗЖЕ"], "color": "yellow", "anim": "glitch", "times": [9.0]},
    {"words": ["ОБЫЧНОЕ"], "color": "white", "times": [3.0]},
]
GLITCH_SPLITS = [3]

# Роли звуков в фикстуре: файлы кладём рядом с XML — резолвер ассетов берёт их оттуда
ROLES = {"highlight_pop": "pop.wav", "whoosh": "whoosh.wav", "transition": "trans.mov",
         "glitch": "glitch.wav", "intro_riser": "riser.wav"}


@pytest.fixture()
def xml_sub(tmp_path):
    """Ролик фикстуры (1-я секунда — кам1, 8-я — перебивка) и СВОИ ассеты рядом с ним.

    Ассеты кладём в `<папка XML>/assets/assets.json` — ровно туда, откуда их берёт
    `scene_plan`: без них роли whoosh/glitch разрешались бы в файлы репозитория, и сторож
    зависел бы от того, что лежит на машине (а на CI их нет вовсе).
    """
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    adir = tmp_path / "assets"
    adir.mkdir()
    for fn in set(ROLES.values()):
        (adir / fn).write_bytes(b"RIFF")
    (adir / "assets.json").write_text(json.dumps(ROLES), encoding="utf-8")
    return dst


@pytest.fixture(autouse=True)
def _isolate_censor(monkeypatch):
    """Детерминизм сборки: цензура читает поставочные списки, а не личный badwords.user.txt."""
    from core import censor
    monkeypatch.setattr(censor, "USER_PATHS", {"bad": "", "ok": ""})
    monkeypatch.setattr(censor, "_cache", {"bad": (None, None, censor.DEFAULT_BAD),
                                           "ok": (None, None, censor.DEFAULT_OK)})


def _media(tmp_path, name, data=b"RIFF"):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def _active_cam_at(cams, t_sec, fps):
    """Индекс показываемой камеры в момент t: то же правило, что `_active_cam_at`
    в scene_plan (и camAt в .jsx). Нужен подготовке вставок — их зовёт и звук."""
    f = t_sec * fps + 1e-4
    best = 0
    for ci in range(len(cams)):
        for cl in cams[ci]["clips"]:
            if cl[4] and cl[0] <= f < cl[1]:
                best = ci
                break
    return best


def _aset(xml):
    """Резолвер ассетов ровно как в scene_plan: папка проекта, иначе — папка установки."""
    base = _project_base(xml)
    asset_base = base
    if not os.path.isfile(os.path.join(base, "assets", "assets.json")):
        alt = os.path.dirname(build_mod.HERE)
        if os.path.isfile(os.path.join(alt, "assets", "assets.json")):
            asset_base = alt
    return assets.resolver(asset_base)


def _intro_groups(intro, splits):
    """Группы интро так же, как их собирает scene_plan (разбивка по splits и сортировка)."""
    lines = [x for x in (intro or []) if (x.get("words") or (x.get("text") or "").strip())]
    cuts = sorted(set(int(s) for s in (splits or []) if 0 < int(s) < len(lines)))
    bounds = [0] + cuts + [len(lines)]
    groups = [lines[bounds[k]:bounds[k + 1]] for k in range(len(bounds) - 1)]
    groups.sort(key=_g_at)
    return groups


def _call(style=None, **kw):
    """Аргументы сборки — одни и те же у scene_plan и у двери звука (свежие копии)."""
    args = dict(style=dict(style or {}), disclaimer="", emit=lambda *a, **k: None)
    args.update(kw)
    for k in ("inserts", "intro"):
        if args.get(k) is not None:
            args[k] = [dict(x) for x in args[k]]
    return args


def _door(xml, ckpt=None, **kw):
    """Дверь звука с входами ровно такими, какими их собрал бы scene_plan.

    Повторяет только ЧТЕНИЯ и подготовку scene_plan: разбор XML, резолв стиля и галки
    ризера, группы интро, тайминги вставок, индексы жёлтых, точки смены камеры, резолвер
    ассетов и цензор по ВСЕМ словам. Своей копии арифметики звука здесь нет намеренно.
    """
    meta, cams, subs, _xi = xml2ae.parse_full(xml)
    st = styles.resolve(dict(kw.get("style") or {}))
    fps = meta["fps"] or 60
    hl = set(int(x) for x in (kw.get("highlights") or []) if 0 <= int(x) < len(subs))
    style_values = read_style(st)
    ins = plan_insert_timings(InsertTimingInputs(
        inserts=[dict(x) for x in (kw.get("inserts") or [])], fps=fps,
        cam_change_sec=[f / fps for f in _cam_change_frames(cams)],
        active_cam_at=lambda t: _active_cam_at(cams, t, fps),
        style=style_values, emit=lambda *a, **k: None)).inserts
    # Галка ризера: стиль перебивает kwarg (правило scene_plan, до вызова блока).
    intro_riser = bool(kw.get("intro_riser", True))
    if style_values.intro_riser is not None:
        intro_riser = bool(style_values.intro_riser)
    groups = _intro_groups(kw.get("intro"), kw.get("intro_splits"))
    return plan_audio(AudioInputs(
        intro_groups=groups,
        any_glitch=any(x.get("anim") == "glitch" for g in groups for x in g),
        inserts=ins, subs=subs, hl=hl,
        cam_change_sec=[f / fps for f in _cam_change_frames(cams)], fps=fps,
        style=style_values, aset=_aset(xml), intro_riser=intro_riser,
        music=kw.get("music"), music_random=bool(kw.get("music_random")),
        music_dir=kw.get("music_dir"), base=_project_base(xml), xml_path=xml,
        censor_source=list(subs), censor_audio=bool(kw.get("censor_audio", True)),
        censor_fps=meta["fps"],
        voice_src=(cams[0].get("path") or "") if cams else "",
        music_db=kw.get("music_db", -20.0),
        ckpt=ckpt or (lambda stage: None), emit=lambda *a, **k: None))


def _jsx(xml, **kw):
    """Собранный .jsx (без записи файла) — теми же аргументами, что у плана."""
    src, _n, _s = xml2ae.to_ae_full(xml, return_source=True,
                                    **{k: v for k, v in kw.items() if k != "ckpt"})
    return src


def _check(xml, style=None, ckpt=None, **kw):
    """Сверить дверь звука с планом scene_plan; -> (plan, plan["audio"], AudioPlan)."""
    plan = xml2ae.scene_plan(xml, **_call(style, **kw))
    au = _door(xml, ckpt=ckpt, **_call(style, **kw))
    assert au.audio == plan["audio"], "звук плана разошёлся с дверью plan_audio"
    return plan, plan["audio"], au


def test_plan_audio_matches_plan_and_jsx(xml_sub):
    """Дефолтный стиль: все пять видов звуков, окна цензуры и подстановки шаблона —
    из одной двери (в .jsx уезжает ровно то, что читает предпросмотр)."""
    ins = [{"type": "video", "media": VIDEO, "start_s": 4.0, "dur_s": 2.0}]
    args = dict(highlights=[10, 20], inserts=ins, intro=GLITCH_INTRO,
                intro_splits=GLITCH_SPLITS)
    plan, audio, au = _check(xml_sub, None, **args)
    assert [s["kind"] for s in au.sfx_plan] == ["pop", "whoosh", "transition", "riser", "glitch"], \
        "фикстура: не все виды звуков собрались — сторож проверял бы пустоту"
    # файлы взялись из ролей ассетов рядом с XML, а не из репозитория
    for kind, fname in (("pop", "pop.wav"), ("whoosh", "whoosh.wav"),
                        ("transition", "trans.mov"), ("glitch", "glitch.wav"),
                        ("riser", "riser.wav")):
        media = [s for s in au.sfx_plan if s["kind"] == kind][0]["media"]
        assert os.path.basename(media) == fname, f"{kind}: ассет роли не доехал"
    assert audio["voice_src"] == plan["cams"][0]["path"]
    jsx = _jsx(xml_sub, **_call(None, **args))
    assert "var VOICE_DB=%g;" % au.voice_db in jsx
    assert "var AUDIO_FADE=%g;" % au.audio_fade in jsx
    assert "MUSIC_DB=%g;" % audio["music_db"] in jsx
    assert "var RISER=%s;" % _js(au.riser) in jsx
    assert "var TRANS=%s, TRANS_SFX=%s;" % (_js(au.trans), _js(au.trans_sfx)) in jsx
    assert "var CENSOR=%s;" % au.censor_js in jsx
    for token in (au.pop_place, au.pop_tail, au.wsfx_place, au.wsfx_tail,
                  au.riser_place, au.trans_place, au.glitch_sfx):
        assert token, "подстановка шаблона пуста — фикстура ничего не проверяет"
        assert token in jsx, "подстановка шаблона не доехала до .jsx"


def test_plan_audio_style_knobs_reach_jsx(xml_sub, tmp_path):
    """Ручки `<звук>_in/_out/_at/_db` и pop_lead: числа двери совпадают с планом и
    доезжают до .jsx — точка удара, обрезка и громкость база+db."""
    style = {"pop": _media(tmp_path, "pop.wav"), "pop_lead": 6, "pop_in": 0.05,
             "pop_at": 0.2, "pop_out": 0.45, "pop_db": -2.5,
             "transition_sfx": _media(tmp_path, "whoosh.wav"),
             "transition_sfx_in": 0.1, "transition_sfx_out": 0.9,
             "transition_sfx_at": 0.4, "transition_sfx_db": 3.0,
             "transition": _media(tmp_path, "trans.mov"),
             "intro_riser": True, "intro_riser_file": _media(tmp_path, "riser.wav"),
             "intro_riser_in": 0.2, "intro_riser_out": 2.5, "intro_riser_at": 0.4,
             "intro_riser_db": -3.0, "voice_db": -4.0, "audio_fades": False}
    ins = [{"type": "video", "media": VIDEO, "start_s": 4.0, "dur_s": 2.0}]
    args = dict(style=style, highlights=[10, 20], inserts=ins)
    plan, audio, au = _check(xml_sub, **args)
    sfx = {s["kind"]: s for s in au.sfx_plan}
    assert (sfx["pop"]["db"], sfx["pop"]["base"]) == (-2.5, -8.0)
    assert (sfx["whoosh"]["db"], sfx["whoosh"]["base"]) == (3.0, -10.0)
    assert (sfx["transition"]["db"], sfx["transition"]["base"]) == (0.0, 0.0)
    assert (sfx["riser"]["db"], sfx["riser"]["base"]) == (-3.0, 0.0)
    assert au.voice_db == -4.0 and plan["_ae"]["voice_db"] == -4.0
    assert au.audio_fade == 0.0, "снятая галка audio_fades не доехала до шаблона"
    jsx = _jsx(xml_sub, **_call(**args))
    # поп: старт (SUBS+6)/FPS − (0.2−0.05), обрезка out 0.45, громкость −8−2.5
    assert au.pop_place == "pl.startTime=(SUBS[pi][0]+6)/FPS-0.15;"
    assert "pl.outPoint=pl.startTime+0.45;" in jsx
    assert "setValue([-10.5,-10.5])" in jsx
    # whoosh: старт на −(0.4−0.1) от события, громкость −10+3
    assert "wl.startTime=cut-TR_IN-TR_SFX_LEAD-0.3;" in jsx
    assert "setValue([-7,-7])" in jsx
    # ризер: удар 0.4 при in 0.2 → старт −0.2 (удар ровно на нуле ролика), обрезка out 2.5
    assert au.riser_place == "rl.startTime=-0.2;"
    assert "rl.outPoint=rl.startTime+2.5;" in jsx
    for token in (au.pop_place, au.pop_tail, au.wsfx_place, au.wsfx_tail,
                  au.riser_place, au.riser_tail, au.trans_place, au.trans_tail):
        assert token in jsx


def test_plan_audio_glitch_groups_and_off(xml_sub):
    """Глитч: звук накрывает ГРУППУ подряд идущих слов (два слоя на три слова), события
    плана — по словам; без глитча слоёв нет вовсе."""
    args = dict(highlights=[10], intro=GLITCH_INTRO, intro_splits=GLITCH_SPLITS)
    _plan, _audio, au = _check(xml_sub, None, **args)
    assert au.glitch_sfx.count('gl.name="Глитч"') == 2, "группы глитч-слов не склеились"
    assert "gl.startTime=0.933;" in au.glitch_sfx      # 1.5 − 0.567 (PRE_S)
    assert "gl.startTime=8.433;" in au.glitch_sfx      # 9.0 − 0.567
    glitch = [s for s in au.sfx_plan if s["kind"] == "glitch"][0]
    assert [e["t"] for e in glitch["events"]] == [1.2, 1.32, 8.7], \
        "события плана — по словам (t = слово − 0.3), а не по группам"
    assert glitch["db"] == 0.0 and glitch["base"] == 0.0
    jsx = _jsx(xml_sub, **_call(None, **args))
    assert au.glitch_sfx in jsx, "блок слоёв глитча не доехал до .jsx"
    # без глитч-строк звука нет — и подстановка пустая (.jsx прежний)
    plain = [{"words": ["ОБЫЧНОЕ"], "color": "white", "times": [3.0]}]
    _p2, _a2, au2 = _check(xml_sub, None, highlights=[10], intro=plain)
    assert au2.glitch_sfx == "" and not [s for s in au2.sfx_plan if s["kind"] == "glitch"]
    assert au2.riser and not [s for s in au2.sfx_plan if s["kind"] in ("whoosh", "transition")], \
        "без видеовставки переход и whoosh ставиться не должны"


def test_plan_audio_music_and_checkpoint(xml_sub, tmp_path):
    """Музыка: путь и «случайная из папки» доезжают до плана и .jsx, этап «музыка» в логе
    ровно один; без музыки — ни пути, ни этапа."""
    track = _media(tmp_path, "track.mp3", b"ID3")
    mdir = tmp_path / "music"
    mdir.mkdir()
    shutil.copyfile(track, str(mdir / "track_1.mp3"))
    args = dict(music=track, music_db=-12.0)
    plan, audio, au = _check(xml_sub, None, **args)
    assert au.music_path == os.path.abspath(track) and audio["music_path"] == au.music_path
    assert audio["music_db"] == -12.0
    assert "var MUSIC=%s, MUSIC_DB=%g;" % (_js(au.music_path), -12.0) in _jsx(xml_sub, **_call(**args))
    calls = []
    _plan, _audio, au_r = _check(xml_sub, None, ckpt=calls.append, music_random=True,
                                 music_dir=str(mdir))
    assert au_r.music_path == os.path.abspath(str(mdir / "track_1.mp3")), \
        "случайная музыка не доехала из папки"
    assert calls == ["музыка"], "этап «музыка» в логе не совпал с обращением к музыке"
    # музыки нет — этапа нет и путь пуст
    calls2 = []
    _p3, audio3, au3 = _check(xml_sub, None, ckpt=calls2.append)
    assert au3.music_path == "" and audio3["music_path"] == "" and calls2 == []


def test_plan_audio_censor_windows(xml_sub):
    """Цензура звука: окна мьюта считаются по ВСЕМ словам фикстуры (со звёздочкой),
    галка censor_audio их выключает — и в плане, и в подстановке CENSOR."""
    subs = xml2ae.parse_full(xml_sub)[2]
    starred = [k for k, w in enumerate(subs) if "*" in (w[2] or "")]
    assert starred, "фикстура без цензурированных слов — сторож проверял бы пустоту"
    plan, audio, au = _check(xml_sub, None)
    assert len(au.censor_windows) == len(starred)
    assert audio["censor"] == [[_r(a), _r(b)] for a, b in au.censor_windows]
    assert au.censor_js == _jd([[_r(a), _r(b)] for a, b in au.censor_windows])
    assert "var CENSOR=%s;" % au.censor_js in _jsx(xml_sub, **_call(None))
    _p2, audio2, au2 = _check(xml_sub, None, censor_audio=False)
    assert au2.censor_windows == [] and audio2["censor"] == [] and au2.censor_js == "[]"
    assert "var CENSOR=[];" in _jsx(xml_sub, **_call(None, censor_audio=False))
    assert plan["audio"] == audio


def test_plan_audio_no_duplicates_and_no_cycle():
    """Дверь не заводит копий общего: обёрток стиля, ассетов, окон мьюта, JS-литералов —
    и не импортирует build.py (тот импортирует её сам, цикл сломал бы `import core.xml2ae`)."""
    import inspect

    from core.xml2ae import plan_audio as mod
    src = inspect.getsource(mod)
    for name in ("def _sv(", "def _sv_or(", "def _asset_or(", "def _censor_windows(",
                 "def _js(", "def _jd(", "def _r(", "def _media_dims("):
        assert name not in src, f"в plan_audio.py завелась копия {name}"
    for imp in ("from .build", "from core.xml2ae.build", "import build"):
        assert imp not in src, f"plan_audio.py тянет build.py: {imp}"
    # числа огибающей глитча остаются контрактом сборки: их берут снаружи из build
    assert build_mod.GLITCH_SFX_PRE_S == mod.GLITCH_SFX_PRE_S == 0.567
    assert build_mod.GLITCH_SFX_QUIET_DB == mod.GLITCH_SFX_QUIET_DB == -48.0
