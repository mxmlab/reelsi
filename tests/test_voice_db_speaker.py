# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Громкость голоса — свойство СПИКЕРА, а не клипа (баг 2026-10-12).

Раньше громкость жила в трёх местах: копия стиля в клипе (`job.style.voice_db`) для
ползунков и превью, стиль спикера для нарезки и стиль клипа для сборки. Импорт давал
всем клипам −3 дБ, а правка одного клипа не доходила до остальных клипов спикера.

Теперь правило одно — `core/speakers.voice_db_of` (своё значение профиля → громкость
стиля спикера → 0), и его берут нарезка (`voicefx.apply_cut_fx`), сборка и встроенный
рендер (`xml2ae.scene_plan`, поле `speaker`). Фронт читает и пишет её одной дверью
(`voiceDbOf` / `setVoiceDb`). Файлы тут проверяются так:

1. правило `voice_db_of` и проверка `save` (строка и −100 отвергаются);
2. нарезка берёт громкость профиля спикера, а не его стиля;
3. сборка: два клипа одного спикера с разными стилями получают одно значение;
   клип без спикера — громкость своего стиля;
4. фронт (стенд в node, боевые функции из `static/app/95-styles.js`): ползунок одного
   клипа меняет громкость всех клипов спикера, сохранение — одним запросом после серии,
   клип без спикера меняет только громкость стиля, точка «стиль изменён» не горит;
5. сторож по тексту: `voice_db` в `static/app/*.js` читается и пишется только внутри
   `voiceDbOf`, `setVoiceDb` и `setStyleDb` (путь без спикера).

Запуск:  py -3.10 -m pytest tests/test_voice_db_speaker.py -q
"""
from __future__ import annotations

import gzip
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from core import speakers  # noqa: E402
from core import styles  # noqa: E402
from core import voicefx  # noqa: E402

APP = ROOT / "static" / "app"
STYLES_JS = APP / "95-styles.js"

node = pytest.mark.skipif(not shutil.which("node"), reason="стенд требует node в PATH")


@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    """Профили и стили — во временных папках, боевые speakers/ и styles/ не трогаем."""
    spk = tmp_path / "speakers"
    spk.mkdir()
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(spk))
    st = tmp_path / "styles"
    st.mkdir()
    monkeypatch.setattr(styles, "STYLE_DIR", str(st))
    # Два стиля с разной громкостью голоса: «Громко» −7 дБ, «Тихо» −9 дБ.
    (st / "Громко.json").write_text(json.dumps({"label": "Громко", "voice_db": -7.0}),
                                   encoding="utf-8")
    (st / "Тихо.json").write_text(json.dumps({"label": "Тихо", "voice_db": -9.0}),
                                  encoding="utf-8")
    return spk


# --------------------------------------------------------------------------- #
# 1. Правило громкости и проверка профиля
# --------------------------------------------------------------------------- #
def test_voice_db_of_own_value_wins(dirs):
    """Своё значение профиля важнее стиля спикера."""
    speakers.save("Иван", {"style": "Громко", "voice_db": -3.0})
    assert speakers.voice_db_of(speakers.load("Иван")) == -3.0


def test_voice_db_of_falls_back_to_speaker_style(dirs):
    """Нет своего значения → громкость стиля спикера (не стиля клипа)."""
    speakers.save("Пётр", {"style": "Громко"})
    assert speakers.voice_db_of(speakers.load("Пётр")) == -7.0


def test_voice_db_of_without_style_is_zero(dirs):
    """Нет своего значения и стиля → 0; не профиль → 0."""
    speakers.save("Пусто", {})
    assert speakers.voice_db_of(speakers.load("Пусто")) == 0.0
    assert speakers.voice_db_of(None) == 0.0


@pytest.mark.parametrize("bad", ["-3", -100, 13, True, math.nan, math.inf])
def test_save_rejects_bad_voice_db(dirs, bad):
    """Строка, вне ползунка (−100 — это цензура, не громкость) и не число — отказ."""
    with pytest.raises(ValueError):
        speakers.save("Тест", {"voice_db": bad})


def test_save_keeps_value_and_drops_none(dirs):
    """Валидное значение сохраняется; None — поля нет (громкость из стиля)."""
    key, _ = speakers.save("Тест", {"voice_db": -4.5})
    assert speakers.load(key)["voice_db"] == -4.5
    key, _ = speakers.save("Тест", {"voice_db": None})
    assert "voice_db" not in speakers.load(key)


# --------------------------------------------------------------------------- #
# 2. Нарезка: громкость из профиля спикера
# --------------------------------------------------------------------------- #
def _cut_gain(monkeypatch, tmp_path, prof: dict[str, Any]) -> float:
    """Громкость, с которой apply_cut_fx зовёт анализ нарезки (печь подменена)."""
    seen: dict[str, float] = {}
    cam1 = tmp_path / "cam1.mp4"
    cam1.write_bytes(b"fake")
    wav0 = tmp_path / "a0.wav"
    wav0.write_bytes(b"raw")
    full = tmp_path / "full.wav"
    full.write_bytes(b"full")

    def fake_render(src, fx, emit=None, **kw):
        return str(full)

    def fake_analysis(full_path, dst, gain_db=0.0, emit=None):
        seen["gain"] = gain_db
        return dst

    monkeypatch.setattr(voicefx, "render_cached", fake_render)
    monkeypatch.setattr(voicefx, "analysis_wav", fake_analysis)
    assert voicefx.apply_cut_fx(str(wav0), str(cam1), prof, emit=lambda *a, **k: None) is True
    return seen["gain"]


def test_cut_gain_comes_from_speaker_profile(dirs, monkeypatch, tmp_path):
    """Нарезка слушает громкость ПРОФИЛЯ (−3), а не стиля спикера (−7)."""
    speakers.save("Иван", {"style": "Громко", "voice_db": -3.0,
                           "voice_fx": {"denoise": {"on": True}}})
    assert _cut_gain(monkeypatch, tmp_path, speakers.load("Иван")) == -3.0


def test_cut_gain_falls_back_to_speaker_style(dirs, monkeypatch, tmp_path):
    """Своего значения нет → громкость стиля спикера (−7), как было до переезда."""
    speakers.save("Пётр", {"style": "Громко", "voice_fx": {"denoise": {"on": True}}})
    assert _cut_gain(monkeypatch, tmp_path, speakers.load("Пётр")) == -7.0


# --------------------------------------------------------------------------- #
# 3. Сборка: план сцены (AE VOICE_DB и встроенный рендер — один источник)
# --------------------------------------------------------------------------- #
@pytest.fixture()
def xml_subs(tmp_path):
    """Тот же клип с субтитрами, что у соседних тестов плана сцены."""
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(str(HERE / "fixtures" / "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _voice_db_in_plan(xml: str, **kw: Any) -> tuple[float, float]:
    """(громкость в _ae — для .jsx, в audio — для встроенного рендера) плана сцены."""
    from core import xml2ae
    plan = xml2ae.scene_plan(xml, emit=lambda *a, **k: None, **kw)
    return float(plan["_ae"]["voice_db"]), float(plan["audio"]["voice_db"])


def test_two_clips_of_one_speaker_get_the_speaker_voice_db(dirs, xml_subs):
    """Два клипа одного спикера с РАЗНЫМИ громкостями стиля → у обоих громкость спикера."""
    speakers.save("Иван", {"style": "Громко", "voice_db": -3.0})
    a = _voice_db_in_plan(xml_subs, style={"voice_db": -10.0}, speaker="Иван")
    b = _voice_db_in_plan(xml_subs, style={"voice_db": -1.0}, speaker="Иван")
    assert a == (-3.0, -3.0), a
    assert b == (-3.0, -3.0), b


def test_speaker_without_own_value_uses_its_style_not_the_clip_style(dirs, xml_subs):
    """Спикер без своего значения: громкость стиля СПИКЕРА (−7), а не стиля клипа (−10)."""
    speakers.save("Пётр", {"style": "Громко"})
    assert _voice_db_in_plan(xml_subs, style={"voice_db": -10.0}, speaker="Пётр") == (-7.0, -7.0)


def test_clip_without_speaker_keeps_its_own_style_voice_db(dirs, xml_subs):
    """Клип без спикера (и без профиля с таким тегом) — громкость его стиля, как раньше."""
    assert _voice_db_in_plan(xml_subs, style={"voice_db": -10.0}) == (-10.0, -10.0)
    assert _voice_db_in_plan(xml_subs, style={"voice_db": -10.0}, speaker="Нет такого") \
        == (-10.0, -10.0)


def test_norm_passes_the_speaker_tag_into_the_job(dirs, xml_subs):
    """Тег спикера клипа доходит до плана сцены: через нормализацию сборки (`api/build`)."""
    from api.build import _norm_build_jobs
    job = _norm_build_jobs([{"xml": xml_subs, "speaker": "Иван", "style": {"voice_db": -10}}])[0]
    assert job["speaker"] == "Иван"
    job = _norm_build_jobs([{"xml": xml_subs, "style": {"voice_db": -10}}])[0]
    assert job["speaker"] is None


# --------------------------------------------------------------------------- #
# 4. Фронт: боевые функции под node
# --------------------------------------------------------------------------- #
def _func_src(src: str, name: str) -> str:
    """Текст функции `name` от объявления до сбалансированной закрывающей скобки."""
    m = re.search(r"(?<![\w$])(?:async\s+)?function\s+%s\s*\(" % re.escape(name), src)
    assert m is not None, f"не нашлась функция {name} в 95-styles.js"
    depth = 0
    for i in range(m.start(), len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():i + 1]
    raise AssertionError(f"не нашлась закрывающая скобка функции {name}")


STAND = r"""
// ---- заглушки двери интерфейса: только то, что звали боевые функции ----
const DOM={speaker:{}};const VAL={speaker:''};   // селектор шага 1 есть на странице всегда
const $=id=>DOM[id]||null;
const val=id=>VAL[id]||'';
function clipSpeaker(c){return (c&&c.job&&c.job.speaker)||'';}
function t(s){return s;}
function toast(){} function uiLog(){} function errText(d){return (d&&d.error)||'';}
let CLIPS=[];let curAE=-1;
let SPEAKERS={};
let STYLES={base:{voice_db:0,music_db:-20},'Громко':{voice_db:-7},'Тихо':{voice_db:-9}};
let CURSTYLE={voice_db:0,music_db:-20};
let TOUCH=0,APPLY=0,CAPTURE=0,REFRESH=[];
function styleTouched(){TOUCH++;} function captureAE(){CAPTURE++;}
function stRefresh(k){REFRESH.push(k);} function updateStyleDiffDots(){}
function applyDbGains(){APPLY++;}
function stRefreshSpeakerFields(){REFRESH.push('<speaker-fields>');}
// Таймеры и сеть — подменяем: сохранение должно уйти ОДНИМ запросом после паузы.
let TIMERS=[];let NEXT=1;const SAVES=[];
globalThis.setTimeout=(fn,ms)=>{const id=NEXT++;TIMERS.push({id,fn});return id;};
globalThis.clearTimeout=(id)=>{TIMERS=TIMERS.filter(x=>x.id!==id);};
globalThis.fetch=async(url,opts)=>{if(url==='/api/savespeaker')SAVES.push(JSON.parse(opts.body));
  // Свежий профиль «с диска» — тот же, что в памяти: громкость берёт базу отсюда (speakerFresh)
  if(url==='/api/speakers')return {json:async()=>({ok:true,speakers:JSON.parse(JSON.stringify(SPEAKERS))})};
  return {json:async()=>({ok:true,key:'Ivan'})};};
async function flush(){const tm=TIMERS.splice(0);for(const x of tm)await x.fn();}
// ---- боевой код ----
__CODE__
// ---- сценарий ----
(async()=>{
  const out={};
  SPEAKERS={Ivan:{label:'Иван',style:'Громко',voice_db:-3},Pet:{label:'Пётр',style:'Громко'}};
  CLIPS=[{job:{speaker:'Ivan'}},{job:{speaker:'Ivan'}},{job:{speaker:''}},{job:{speaker:'Pet'}}];
  curAE=0;
  out.readBefore=[voiceDbOf(CLIPS[0]),voiceDbOf(CLIPS[1]),voiceDbOf(CLIPS[3])];
  // Серия правок одного клипа: последний выигрывает, в профиль уходит ОДИН запрос.
  for(let i=0;i<10;i++)setVoiceDb(-i,CLIPS[0]);
  setVoiceDb(0,CLIPS[0]);
  out.timersBeforeFlush=TIMERS.length;
  out.savesBeforeFlush=SAVES.length;
  await flush();
  out.savesAfterFlush=SAVES.length;
  out.savedVoiceDb=SAVES.length?SAVES[0].data.voice_db:null;
  out.savedName=SAVES.length?SAVES[0].name:null;
  out.readSecond=voiceDbOf(CLIPS[1]);        // второй клип того же спикера → 0
  out.readPet=voiceDbOf(CLIPS[3]);           // без своего значения — из стиля спикера (−7)
  out.styleUntouched={voice:CURSTYLE.voice_db,touch:TOUCH};
  // Клип без спикера: меняется только громкость стиля.
  setVoiceDb(-6,CLIPS[2]);
  out.noSpeaker={styleVoice:CURSTYLE.voice_db,touch:TOUCH,speakerSaves:SAVES.length,
                 read:voiceDbOf(CLIPS[2])};
  // Панель шага 1 без открытого клипа — спикер верхнего селектора.
  curAE=-1;VAL.speaker='Ivan';
  out.step1=voiceDbOf();
  setVoiceDb(-5);
  out.step1After=[SPEAKERS.Ivan.voice_db,CURSTYLE.voice_db];
  await flush();
  out.step1Saves=SAVES.length;
  console.log('RESULT '+JSON.stringify(out));
})().catch(e=>{console.log('ERROR '+(e&&e.stack||e));process.exit(1);});
"""


def _run_stand(tmp_path: Path) -> dict[str, Any]:
    """Боевой код (функции из 95-styles.js) + заглушки → node → итог сценария."""
    src = STYLES_JS.read_text(encoding="utf-8")
    # Константы сохранения — из того же файла, рядом с функциями.
    m = re.search(r"const VOICEDB_SAVE_MS[\s\S]*?(?=function voiceDbSaveSoon)", src)
    assert m is not None, "в 95-styles.js нет блока отложенного сохранения громкости"
    parts = [m.group(0)]
    # voiceFxPanelHosts/vfxEl — ползунки обеих панелей «Голос» (превью и профиль): в стенде
    # панелей нет, поэтому список пуст и до разметки дело не доходит
    for name in ("voiceDbSpeakerKey", "voiceDbBySpeaker", "voiceDbOf", "setVoiceDb",
                 "voiceDbSaveSoon", "voiceDbSaveNow", "speakerFresh", "setStyleDb", "syncDbSliders",
                 "voiceFxPanelHosts", "vfxEl"):
        parts.append(_func_src(src, name))
    script = tmp_path / "voice_db_stand.js"
    script.write_text(STAND.replace("__CODE__", "\n".join(parts)), encoding="utf-8")
    proc = subprocess.run(["node", str(script)], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
    assert line, f"стенд не отработал:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(line[-1][len("RESULT "):])


_STAND_OUT: dict[str, Any] = {}


def _stand(tmp_path_factory) -> dict[str, Any]:
    """Итог сценария стенда: один прогон node на модуль, проверок несколько."""
    if not _STAND_OUT:
        _STAND_OUT.update(_run_stand(tmp_path_factory.mktemp("voice_db_stand")))
    return _STAND_OUT


@node
def test_front_one_slider_moves_every_clip_of_the_speaker(tmp_path_factory):
    """Ползунок клипа: громкость спикера у ВСЕХ его клипов, одна отложенная запись."""
    out = _stand(tmp_path_factory)
    assert out["readBefore"] == [-3.0, -3.0, -7.0], out
    assert out["timersBeforeFlush"] == 1, "серия правок должна ждать паузы, а не слать на каждый вызов"
    assert out["savesBeforeFlush"] == 0, out
    assert out["savesAfterFlush"] == 1, f"ушло запросов: {out['savesAfterFlush']}"
    # Пишем под КЛЮЧОМ профиля (файл speakers/<ключ>.json), а не под подписью: подпись
    # «Иван» дала бы второй файл рядом с «Ivan» (speakers.save берёт имя файла из имени)
    assert out["savedVoiceDb"] == 0.0 and out["savedName"] == "Ivan", out
    assert out["readSecond"] == 0.0, out
    assert out["readPet"] == -7.0, "у спикера без своего значения громкость — из его стиля"


@node
def test_front_clip_without_speaker_touches_only_style_voice(tmp_path_factory):
    """Клип без спикера: меняется громкость СТИЛЯ, профиль спикера не трогается."""
    out = _stand(tmp_path_factory)
    assert out["noSpeaker"]["styleVoice"] == -6.0, out
    assert out["noSpeaker"]["read"] == -6.0, out
    assert out["noSpeaker"]["speakerSaves"] == 1, "без спикера запрос профиля не нужен"
    assert out["noSpeaker"]["touch"] == 1, "путь без спикера — правка стиля, точка «изменён» горит"


@node
def test_front_speaker_edit_does_not_mark_style_changed(tmp_path_factory):
    """Правка громкости спикера не помечает стиль изменённым и не трогает CURSTYLE."""
    out = _stand(tmp_path_factory)
    assert out["styleUntouched"] == {"voice": 0, "touch": 0}, out["styleUntouched"]


@node
def test_front_step1_without_open_clip_uses_the_top_speaker(tmp_path_factory):
    """Шаг 1 без открытого клипа: громкость спикера верхнего селектора."""
    out = _stand(tmp_path_factory)
    assert out["step1"] == 0.0, out["step1"]   # спикер шага 1 — Иван, его громкость уже 0
    assert out["step1After"] == [-5.0, -6.0], out["step1After"]
    assert out["step1Saves"] == 2, out


# --------------------------------------------------------------------------- #
# 5. Сторож по тексту: voice_db читается и пишется только в белом списке
# --------------------------------------------------------------------------- #
# voiceDbSaveNow — запись громкости в профиль от свежей базы с диска: она и есть дверь
# записи voice_db (поверх свежего профиля пишется только громкость).
ALLOWED_VOICE_DB_FUNCS = {"voiceDbOf", "setVoiceDb", "setStyleDb", "voiceDbSaveNow"}


def _enclosing_function(src: str, pos: int) -> str:
    """Имя самой внутренней именованной функции, в теле которой стоит позиция."""
    best = None
    for m in re.finditer(r"(?<![\w$])(?:async\s+)?function\s+([\w$]+)\s*\(", src):
        i = src.find("{", m.end())
        if i < 0:
            continue
        depth = 0
        for j in range(i, len(src)):
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
                if depth == 0:
                    if m.start() <= pos <= j and (best is None or m.start() > best[0]):
                        best = (m.start(), m.group(1))
                    break
    return best[1] if best else "<top-level>"


def test_voice_db_is_read_and_written_only_in_the_whitelist():
    """Сторож: литерал `voice_db` в static/app/*.js — только в voiceDbOf/setVoiceDb/setStyleDb.

    Иначе снова появится третий источник громкости, и правка одного клипа опять
    не дойдёт до остальных клипов спикера.
    """
    bad = []
    seen = 0
    for path in sorted(APP.glob("*.js")):
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r"voice_db", src):
            seen += 1
            fn = _enclosing_function(src, m.start())
            if fn not in ALLOWED_VOICE_DB_FUNCS:
                line = src.count("\n", 0, m.start()) + 1
                bad.append(f"{path.name}:{line} в {fn}")
    assert seen, "сторож не нашёл ни одного voice_db — разбор сломан"
    assert not bad, "voice_db вне белого списка: " + "; ".join(bad)
