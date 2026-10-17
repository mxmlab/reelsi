# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Настройки плагинов голоса не теряются: окно, профиль спикера и превью сходятся.

Симптом владельца: в окне плагина выставили Mix 2 %, закрыли окно — «сохранено». Закрыли
превью, открыли снова — голос печётся с Mix 100 %. Пять причин, по одной на тест:

1. Страница держит копию профилей SPEAKERS, а сервер пишет состояние плагина в файл
   профиля — копия не обновлялась, и следующая запись откатывала состояние (П1).
2. Четыре записи профиля брали базу из той же устаревшей копии (П2).
3. Тост «сохранено» показывался по приходу состояния, а не по итогу записи (П3).
4. После закрытия окна превью выходил из пересчёта ранним return: трек остался под
   прежние плагины (П4).
5. Состояние окна приписывалось плагину, которого открывали последним, а не тому, чьё
   окно закрыли (П5).
6. Счётчик итогов записи (`save_seq`) — на нём держится тост «сохранено» (П5).

JS-тесты гоняют БОЕВЫЕ функции из static/app/*.js под node на мини-DOM и заглушках сети —
тем же стендом, что tests/test_voice_panel_js.py: тела функций берутся из файлов, копий в
тесте нет. Python-тесты — без настоящих плагинов: процесс окна подменён.

Каждый тест должен краснеть на коде до правки (проверено отдельно, см. отчёт).

Запуск:  py -3.10 -m pytest tests/test_voice_state_sync.py -q
"""
from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
# Стенд панели — тот же, что у test_voice_panel_js: мини-DOM и список боевых функций
# (pytest кладёт папку tests/ в sys.path, поэтому импорт по имени модуля работает).
from test_voice_panel_js import (  # noqa: E402
    DOM, PREVIEW_FUNCS, STATE, STYLES_JS, VOICE_FUNCS, _src, _src_any, _sync_consts,
)

from core import speakers, voicefx, voicefx_editor as ed  # noqa: E402

node = pytest.mark.skipif(not shutil.which("node"), reason="стенд требует node в PATH")

A = "C:/a.vst3"
B = "C:/b.vst3"


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


# --------------------------------------------------------------------------- #
# Стенд JS: сеть-заглушка и состояние модуля, которое живёт в файле
# --------------------------------------------------------------------------- #
# Сеть: ответы по префиксу адреса; каждый запрос запоминается (адрес, метод, тело).
NET = r"""
const NET=[];
let ROUTES={};
globalThis.fetch=(url,opts)=>{
  const u=String(url),method=(opts&&opts.method)||'GET',body=(opts&&opts.body)||'';
  NET.push({url:u,method:method,body:body});
  const key=Object.keys(ROUTES).find(k=>u.indexOf(k)===0);
  const resp=key?ROUTES[key](u,opts):{};
  return Promise.resolve({json:()=>Promise.resolve(resp)});};
function sleep(ms){return new Promise(r=>setTimeout(r,ms));}
// Клип без спикера для стенда: боевая clipSpeaker живёт в 40-queue.js, а профиль здесь не нужен.
function clipSpeaker(c){return (c&&c.job&&c.job.speaker)||'';}
"""

# Функции, которых нет в VOICE_FUNCS/PREVIEW_FUNCS, но до которых доходят пути стенда.
# voiceFxHostStop нужна настоящая: при живом хосте (VOICEFXLIVE взведён опросом) боевая
# voiceFxHostOn истинна, и vtPrep ждёт гашения через vtHostDown. Без неё — бесконечная
# рекурсия vtPrep в микрозадачах: опрос никогда не отдаёт управление таймерам.
EXTRA = ("voiceFxHostPoll", "voiceFxHostNotes", "voiceFxHostGone", "voiceFxHostSync",
         "voiceFxHostStop", "voiceFxHasVst", "voiceFxHostKey", "vtVoiceFail", "speakerFresh",
         "speakerRefresh", "saveSpeakerRenderdir")


def _let(pattern: str) -> str:
    """Строка состояния модуля из 95-styles.js — тем же приёмом, что у констант стенда."""
    m = re.search(pattern, STYLES_JS.read_text(encoding="utf-8"), re.M)
    assert m is not None, f"в 95-styles.js нет строки {pattern}"
    return m.group(0)


def _stand(body: str) -> str:
    """Собрать стенд: мини-DOM, константы, состояние, боевые функции и тело теста."""
    extra = "\n".join(_src_any(n) for n in EXTRA)
    return (DOM + _sync_consts() + "\n" + STATE + NET
            + _src(*(VOICE_FUNCS + PREVIEW_FUNCS)) + "\n" + extra + "\n"
            + _let(r"^let VFXHOST=.*$") + "\n" + _let(r"^let VOICEDB_SAVE_T=.*$") + "\n"
            + body)


def _node(tmp_path: Path, body: str) -> Any:
    """Прогнать стенд под node и вернуть JSON из последней строки вывода."""
    path = tmp_path / "state_stand.js"
    path.write_text(_stand(body), encoding="utf-8")
    proc = subprocess.run(["node", str(path)], capture_output=True, text=True,
                          encoding="utf-8-sig", errors="replace", timeout=60,
                          cwd=str(ROOT))
    assert proc.returncode == 0, f"node упал: {proc.stderr or proc.stdout}"
    lines = [line.strip() for line in proc.stdout.strip().splitlines() if line.strip()]
    assert lines, "стенд ничего не напечатал"
    return json.loads(lines[-1])


# --------------------------------------------------------------------------- #
# П1 — опрос с новым save_seq обновляет SPEAKERS с диска
# --------------------------------------------------------------------------- #
@node
def test_poll_refreshes_speakers_from_disk_after_a_saved_window(tmp_path: Path) -> None:
    """Окно сохранило состояние на сервере → страница берёт профиль с диска, не свою копию.

    Копия в SPEAKERS держит ОЛД-состояние; сервер записал НОВОЕ. Без обновления следующая
    запись (автосохранение панели) откатила бы плагин к старому.
    """
    body = r"""
(async()=>{
globalThis.__byId={pvvoice:new El('div')};
SPEAKERS={'Мясников':{label:'Мясников',voice_db:-3,voice_fx:{vst:[{path:'C:/a.vst3',state:'OLD'}]}}};
VOICEFXSPK='Мясников';
VOICEFXLIVE={sid:'77',running:true,window:true,track_ready:false,xml:'x.xml'};
ROUTES={
  '/api/voicefx_live':()=>({ok:true,running:true,sid:'77',window:true,track_ready:false,
                            save_seq:1,saved:true,error:''}),
  '/api/speakers':()=>({ok:true,speakers:{'Мясников':{label:'Мясников',voice_db:0,
      voice_fx:{vst:[{path:'C:/a.vst3',state:'NEW'}]}}}})};
voiceFxHostPoll();
await sleep(800);
clearTimeout(VFXHOST.timer);VFXHOST.timer=0;VOICEFXLIVE=null;
console.log(JSON.stringify({state:SPEAKERS['Мясников'].voice_fx.vst[0].state,
  toast:LOGS.indexOf('toast: Настройки плагина сохранены')>=0,
  posts:NET.filter(n=>n.method==='POST').length,
  dbLocal:SPEAKERS['Мясников'].voice_db}));
})().catch(e=>{console.error(e&&e.stack||e);process.exit(1);});
"""
    res = _node(tmp_path, body)
    assert res["state"] == "NEW", "копия профиля не обновилась с диска после сохранения окна"
    assert res["toast"] is True, "тост «сохранено» не показан по итогу записи"
    assert res["posts"] == 0, "опрос сам что-то записывает — записи тут быть не должно"
    assert res["dbLocal"] == -3, "speakerRefresh затёр несохранённую громкость с диска"


# --------------------------------------------------------------------------- #
# П2 — все записи профиля берут базу с диска, а не из копии SPEAKERS
# --------------------------------------------------------------------------- #
@node
def test_saves_start_from_fresh_profile_not_stale_copy(tmp_path: Path) -> None:
    """Громкость и папка рендера пишут в профиль СВЕЖЕЕ состояние плагина с диска.

    Копия SPEAKERS держит старое состояние, диск — новое. В POST уходит новое; свои поля
    (громкость, папка) — поверх; чужие поля профиля (камеры) сохраняются с диска.
    """
    body = r"""
(async()=>{
globalThis.__byId={};
SPEAKERS={'Мясников':{label:'Мясников',voice_db:-3,renderdir:'C:/old',
  voice_fx:{vst:[{path:'C:/a.vst3',state:'OLD'}]}}};
ROUTES={
  '/api/speakers':()=>({ok:true,speakers:{'Мясников':{label:'Мясников',voice_db:0,renderdir:'C:/old',
      camdirs:['C:/cam'],voice_fx:{vst:[{path:'C:/a.vst3',state:'NEW'}]}}}}),
  '/api/savespeaker':()=>({ok:true,key:'Мясников'})};
VOICEDB_PENDING['Мясников']=true;
await voiceDbSaveNow();
const b1=JSON.parse(NET.filter(n=>n.url==='/api/savespeaker').pop().body);
NET.length=0;
await saveSpeakerRenderdir(SPEAKERS['Мясников'],'D:/new');
const b2=JSON.parse(NET.filter(n=>n.url==='/api/savespeaker').pop().body);
console.log(JSON.stringify({
  dbName:b1.name, dbState:b1.data.voice_fx.vst[0].state, dbVoice:b1.data.voice_db,
  rdName:b2.name, rdState:b2.data.voice_fx.vst[0].state, rdDir:b2.data.renderdir,
  rdCam:b2.data.camdirs}));
})().catch(e=>{console.error(e&&e.stack||e);process.exit(1);});
"""
    res = _node(tmp_path, body)
    assert res["dbState"] == "NEW", "громкость откатила состояние плагина к старой копии"
    assert res["dbVoice"] == -3, "громкость не записалась (своё поле — поверх свежего)"
    assert res["dbName"] == "Мясников"
    assert res["rdState"] == "NEW", "папка рендера откатила состояние плагина"
    assert res["rdDir"] == "D:/new" and res["rdName"] == "Мясников"
    assert res["rdCam"] == ["C:/cam"], "чужие поля профиля (камеры) уехали не тем, что лежали"


# --------------------------------------------------------------------------- #
# П3 — тост по итогу записи: отказ не объявляется «сохранено»
# --------------------------------------------------------------------------- #
@node
def test_failed_write_is_not_announced_as_saved(tmp_path: Path) -> None:
    """Состояние пришло, а запись не вышла (saved:false) — тоста «сохранено» нет.

    Честный текст — в панель голоса и тостом: «Настройки плагина не сохранены: <причина>».
    """
    body = r"""
(async()=>{
const host=new El('div');host.innerHTML='<span data-vfx="status"></span>';
globalThis.__byId={pvvoice:host};
SPEAKERS={};
VOICEFXSPK='Мясников';
VOICEFXLIVE={sid:'78',running:true,window:true,track_ready:false,xml:'x.xml'};
ROUTES={'/api/voicefx_live':()=>({ok:true,running:true,sid:'78',window:true,track_ready:false,
  state:'NEWSTATE',save_seq:1,saved:false,error:'x'})};
voiceFxHostPoll();
await sleep(800);
clearTimeout(VFXHOST.timer);VFXHOST.timer=0;VOICEFXLIVE=null;
console.log(JSON.stringify({
  ok:LOGS.indexOf('toast: Настройки плагина сохранены')>=0,
  bad:LOGS.some(s=>s.indexOf('toast: Настройки плагина не сохранены: x')===0),
  status:host.querySelector('[data-vfx="status"]').textContent}));
})().catch(e=>{console.error(e&&e.stack||e);process.exit(1);});
"""
    res = _node(tmp_path, body)
    assert res["ok"] is False, "тост «сохранено» показан при неудачной записи"
    assert res["bad"] is True, "тост о неудачной записи не показан"
    assert "не сохранены" in res["status"], res["status"]


# --------------------------------------------------------------------------- #
# П4 — после закрытия окна превью пересчитывает голос, а не выходит рано
# --------------------------------------------------------------------------- #
@node
def test_closing_the_window_makes_preview_recompute_the_voice(tmp_path: Path) -> None:
    """Трек играет под прежние плагины; окно закрыли — vtPrep обязан заказать расчёт.

    Раньше сравнение «тот же трек» (st.on, st.dn, st.fin) и ранний return гасили пересчёт:
    голос оставался со старыми настройками до переоткрытия превью.
    """
    body = r"""
(async()=>{
const P={xml:'x.xml',cams:[{path:'cam1.mp4'}]};
const st=vtOf(P);
st.on=true;st.path='/voice.wav';st.fin=true;
st.dn=JSON.stringify(((vtFx(P))||{}).denoise||{});
st.wantDn=st.dn;
VOICEFXLIVE={sid:'79',running:true,window:true,track_ready:false,xml:'x.xml'};
ROUTES={
  '/api/voicefx_live':()=>({ok:true,running:true,sid:'79',window:false,track_ready:false}),
  '/api/voicefx_bake':()=>({error:'стенд: расчёт проверяем, не результат'})};
voiceFxHostPoll(P);
await sleep(800);
clearTimeout(VFXHOST.timer);VFXHOST.timer=0;VOICEFXLIVE=null;
console.log(JSON.stringify({bake:NET.some(n=>n.url==='/api/voicefx_bake')}));
})().catch(e=>{console.error(e&&e.stack||e);process.exit(1);});
"""
    res = _node(tmp_path, body)
    assert res["bake"] is True, "vtPrep вышел рано: голос после закрытия окна не пересчитан"


# --------------------------------------------------------------------------- #
# П5 — состояние уходит тому плагину, чьё окно закрыли (события хоста)
# --------------------------------------------------------------------------- #
class _Plugin:
    """Плагин цепочки: окно «показывает» и отдаёт то состояние, что человек накрутил."""

    def __init__(self, raw: bytes) -> None:
        self.raw_state = raw
        self.new = raw

    def show_editor(self) -> None:
        self.raw_state = self.new


@pytest.fixture
def speaker_dir(tmp_path, monkeypatch):
    """Профиль спикера — во временной папке: боевая `speakers/` личная, её не читаем."""
    d = tmp_path / "speakers"
    d.mkdir()
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(d))
    return d


def test_state_event_names_its_plugin_not_the_open_path(speaker_dir, tmp_path, monkeypatch):
    """Окно плагина A закрыли, а «Настроить» последним нажали у B — пишем в A.

    Сценарий целиком через события: хост открывает окно A (`_open_editor` пишет событие
    `state` с путём A), сервер читает файл событий и записывает профиль. Открытый путь
    сессии (B) в запись не попадает.
    """
    import api.voicefx as route

    key, path = speakers.save("Мясников", {"label": "Мясников", "voice_fx": {"vst": [
        {"path": A, "name": "", "state": "AQID", "on": True},
        {"path": B, "name": "", "state": "BBBB", "on": True}]}})
    events = tmp_path / "events.jsonl"
    monkeypatch.setattr(ed, "_EVENTS_FILE", str(events))
    plug_a = _Plugin(b"AQID")
    plug_a.new = b"\x07new-a"
    plug_b = _Plugin(b"BBBB")
    loaded = [({"path": A, "on": True}, plug_a), ({"path": B, "on": True}, plug_b)]
    state_out = tmp_path / "out.bin"

    ed._open_editor(loaded, 0, str(state_out), A)            # окно A открыто и закрыто

    proc = SimpleNamespace(pid=1, stdin=None, poll=lambda: 0)
    ses = voicefx._session_new(str(tmp_path / "w"), proc, state_out=str(state_out),
                               track_file="", chain=[{"path": A, "on": True},
                                                     {"path": B, "on": True}],
                               index=1, speaker=key, fx={}, device="", start=0.0,
                               src="", events_file=str(events))
    ses.open_path = B                                         # «Настроить» нажали у B
    voicefx.live_events(ses)
    assert ses.state_path == A, "состояние не привязано к плагину, чьё окно закрыли"

    route._save_live_done(ses, ses.state, None)

    after = speakers.load(key)["voice_fx"]["vst"]
    assert after[0]["state"] == _b64(b"\x07new-a"), "состояние A не записано в A"
    assert after[1]["state"] == "BBBB", "состояние записано в B — чужая настройка испорчена"


# --------------------------------------------------------------------------- #
# П5 — счётчик итогов записи растёт на каждую законченную попытку
# --------------------------------------------------------------------------- #
def test_save_seq_counts_every_finished_write(speaker_dir):
    """`save_seq` растёт на успех, на отказ и на ранний выход — страница ждёт именно его.

    По нему опрос понимает, что запись кончилась, и показывает её итог. Без счётчика тост
    «сохранено» показывали бы по самому состоянию — и он врал бы при отказе записи.
    """
    import api.voicefx as route

    key, _path = speakers.save("Мясников", {"label": "Мясников", "voice_fx": {"vst": [
        {"path": A, "name": "", "state": "AQID", "on": True}]}})
    proc = SimpleNamespace(pid=1, stdin=None, poll=lambda: 0)
    ses = voicefx._session_new(str(speaker_dir), proc, state_out="", track_file="",
                               chain=[{"path": A, "on": True}], index=0, speaker=key,
                               fx={}, device="", start=0.0, src="")
    assert ses.save_seq == 0

    route._save_live_done(ses, "", 1)                         # окно закрылось с кодом 1
    assert ses.save_seq == 1 and ses.saved is False and ses.error, "ветка ошибки не засчитана"

    route._save_live_done(ses, "", 0)                         # пустое состояние — ранний выход
    assert ses.save_seq == 2, "ранний выход не засчитан"

    route._save_live_done(ses, _b64(b"\x01ok"), 0)           # успех
    assert ses.save_seq == 3 and ses.saved is True and ses.error == "", "успех не засчитан"

    gone = voicefx._session_new(str(speaker_dir), proc, state_out="", track_file="",
                                chain=[{"path": A, "on": True}], index=0,
                                speaker="Нет такого профиля", fx={}, device="",
                                start=0.0, src="")
    route._save_live_done(gone, _b64(b"\x01x"), 0)           # профиля нет — отказ записи
    assert gone.save_seq == 1 and gone.saved is False and gone.error, "отказ записи не засчитан"
    # В ответе сессии счётчик виден странице
    assert route._live_payload(ses)["save_seq"] == 3
