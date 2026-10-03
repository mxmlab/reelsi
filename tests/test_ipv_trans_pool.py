# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты превью: пул видео перехода и освобождение медиа-элементов.

Проверяет три контракта из pending_VP_task.md:
1. План с 12 событиями перехода одного файла -> в #ipvtrans <= 2 <video>; на времени внутри
   6-го перехода один из них показывает кадр с currentTime ≈ tm - t0_6;
2. Повторный ipvOpen 5 раз -> число <video> в документе не растёт (константа), у удалённых
   элементов снят src (removeAttribute('src') и load());
3. Смена плана переходов (новая подпись) -> старые элементы освобождены (removeAttribute('src'), load()).
"""
import json
import os
import re
import shutil
import subprocess
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
JS = os.path.join(ROOT, "static", "app", "85-inserts-view.js")
CORE_JS = os.path.join(ROOT, "static", "app", "00-core.js")

node = pytest.mark.skipif(not shutil.which("node"), reason="стенд требует node в PATH")


def _run_node(code: str, tmp_path, name: str = "test.js") -> str:
    path = tmp_path / name
    path.write_text(code, encoding="utf-8")
    p = subprocess.run(["node", str(path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError(f"node {name} упал (rc={p.returncode}):\n{p.stderr}")
    return p.stdout.strip()


def _fn(src: str, name: str) -> str:
    m = re.search(r"(?:async\s+)?function\s+%s\s*\(" % re.escape(name), src)
    if not m:
        raise AssertionError(f"в исходнике не нашлась функция {name}")
    i = src.index("{", m.end() - 1)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():j + 1]
    raise AssertionError(f"не закрыта функция {name}")


_MOCK_ENV = r"""
const assert = require('assert');
function El(tag){
  this.tagName = String(tag).toUpperCase();
  this.style = {};
  this.children = [];
  this.dataset = {};
  this._attrs = {};
  this._ev = {};
  this._removedAttrs = [];
  this._loads = 0;
  this._pauses = 0;
  this.paused = true;
  this.currentTime = 0;
  this.videoWidth = 3840;
  this.videoHeight = 2160;
  this.duration = 1.6266;
  this.muted = false;
  this.volume = 1;
}
El.prototype.appendChild = function(c){
  c.parentNode = this;
  this.children.push(c);
  return c;
};
El.prototype.insertBefore = function(c, ref){
  c.parentNode = this;
  const idx = ref ? this.children.indexOf(ref) : -1;
  if(idx >= 0) this.children.splice(idx, 0, c);
  else this.children.push(c);
  return c;
};
El.prototype.removeChild = function(c){
  const idx = this.children.indexOf(c);
  if(idx >= 0){
    this.children.splice(idx, 1);
    c.parentNode = null;
  }
  return c;
};
El.prototype.remove = function(){
  if(this.parentNode){
    const idx = this.parentNode.children.indexOf(this);
    if(idx >= 0) this.parentNode.children.splice(idx, 1);
    this.parentNode = null;
  }
};
El.prototype.addEventListener = function(n, f){
  (this._ev[n] = this._ev[n] || []).push(f);
};
El.prototype.removeEventListener = function(n, f){
  if(this._ev[n]){
    const i = this._ev[n].indexOf(f);
    if(i >= 0) this._ev[n].splice(i, 1);
  }
};
El.prototype.setAttribute = function(n, v){
  this._attrs[n] = String(v);
};
El.prototype.getAttribute = function(n){
  return (n in this._attrs) ? this._attrs[n] : null;
};
El.prototype.removeAttribute = function(n){
  delete this._attrs[n];
  this._removedAttrs.push(n);
};
El.prototype.load = function(){
  this._loads++;
};
El.prototype.pause = function(){
  this.paused = true;
  this._pauses++;
};
El.prototype.play = function(){
  this.paused = false;
  return { catch(){} };
};
El.prototype.querySelectorAll = function(sel){
  const want = String(sel || '').replace(/^\./, '');
  const out = [];
  (function scan(el){
    for(const ch of el.children){
      if(want === 'video' ? ch.tagName === 'VIDEO' : (ch._cls && ch._cls.has(want))) out.push(ch);
      scan(ch);
    }
  })(this);
  return out;
};
El.prototype.querySelector = function(sel){
  return this.querySelectorAll(sel)[0] || null;
};
Object.defineProperty(El.prototype, 'id', {
  get(){ return this._id || ''; },
  set(v){ this._id = String(v); }
});
Object.defineProperty(El.prototype, 'className', {
  get(){ return [...(this._cls || [])].join(' '); },
  set(v){ this._cls = new Set(String(v || '').split(/\s+/).filter(Boolean)); }
});
Object.defineProperty(El.prototype, 'innerHTML', {
  get(){ return this._html || ''; },
  set(v){
    this._html = String(v);
    if(!v) this.children = [];
  }
});
El.prototype.classList = {
  add(){}, remove(){}, toggle(){}, contains(){ return false; }
};
const ALL_VIDEOS = [];
const document = {
  createElement(tag){
    const el = new El(tag);
    if(String(tag).toLowerCase() === 'video') ALL_VIDEOS.push(el);
    return el;
  }
};
const STAGE = new El('div');
STAGE._id = 'ipvstage';
const ELEMENTS_MAP = {
  ipvstage: STAGE,
  ipvins: new El('div'),
  ipvsub: new El('div'),
  ipvintro: new El('div'),
  ipvtime: new El('span'),
  ipvseek: new El('input'),
};
ELEMENTS_MAP.ipvins._id = 'ipvins';
ELEMENTS_MAP.ipvsub._id = 'ipvsub';
ELEMENTS_MAP.ipvintro._id = 'ipvintro';
ELEMENTS_MAP.ipvtime._id = 'ipvtime';
ELEMENTS_MAP.ipvseek._id = 'ipvseek';
ELEMENTS_MAP.ipvsub.style = { removeProperty(){} };
STAGE.appendChild(ELEMENTS_MAP.ipvintro);
STAGE.appendChild(ELEMENTS_MAP.ipvins);
STAGE.appendChild(ELEMENTS_MAP.ipvsub);

function $(id){
  if(ELEMENTS_MAP[id]) return ELEMENTS_MAP[id];
  let found = null;
  (function scan(el){
    if(found) return;
    if(el._id === id || el.id === id){ found = el; return; }
    for(const ch of el.children){ scan(ch); }
  })(STAGE);
  return found;
}
function t(s){ return s; }
function uiLog(){}
function pvSrc(p){ return '/api/media?path=' + encodeURIComponent(p); }
function vidSeekTol(){ return 0.4; }
function ipvNow(){ return 0; }
function ipvUI(){}
const CSS = { supports: () => true };
const MEDIA_VOL = 1;
"""


@node
def test_trans_pool_size_and_seek_inside_6th_transition(tmp_path):
    """План с 12 событиями перехода одного файла: в #ipvtrans <= 2 <video>, на 6-м переходе точный кадр."""
    core_src = open(CORE_JS, encoding="utf-8").read()
    src = open(JS, encoding="utf-8").read()
    media_free_fn = _fn(core_src, "mediaFree")
    trans_plan_fn = _fn(src, "ipvTransPlan")
    trans_fn = _fn(src, "ipvTrans")

    # 12 событий перехода с шагом 2.0 с
    evs = [round(1.0 + i * 2.0, 3) for i in range(12)]
    # 6-й переход: индекс 5
    t0_6 = evs[5]
    tm = t0_6 + 0.5  # внутри 6-го перехода

    js = f"""
    {_MOCK_ENV}
    {media_free_fn}
    {trans_plan_fn}
    {trans_fn}

    const IPV = {{ fps: 60, plan: null, playing: false, vids: [1], transSig: '', transAsked: '' }};
    IPV.plan = {{
      trans: {{ media: 'Quick 2.mov', in: 0, w: 3840, h: 2160 }},
      inserts: {json.dumps([{"t": "video", "media": "v.mp4", "start": e, "end": e + 1.8, "noexit": True} for e in evs])}
    }};

    ipvTrans({tm});

    const box = $('ipvtrans');
    assert(box, 'коробка #ipvtrans не создана');
    const vs = box.querySelectorAll('video');
    const ctList = vs.map(v => +(v.currentTime || 0));
    const dispList = vs.map(v => v.style.display || '');
    const starts = vs.map(v => +(v.dataset.t0 || 0));

    console.log(JSON.stringify({{
      n: vs.length,
      ct: ctList,
      disp: dispList,
      starts: starts,
      tm: {tm},
      t0_6: {t0_6}
    }}));
    """
    out = json.loads(_run_node(js, tmp_path, "pool_12.js"))
    assert out["n"] <= 2, f"в #ipvtrans больше 2 <video>: {out['n']}"
    # Один из них должен показывать кадр с currentTime ≈ tm - t0_6
    expected_ct = tm - t0_6
    matching = [i for i, ct in enumerate(out["ct"])
                if abs(ct - expected_ct) < 0.05 and out["disp"][i] != "none"]
    assert matching, (f"ни один из элементов не показывает 6-й переход (ct ≈ {expected_ct}): "
                      f"ct={out['ct']}, disp={out['disp']}, starts={out['starts']}")


@node
def test_ipv_open_frees_videos_repeated_calls(tmp_path):
    """Повторный ipvOpen того же/другого клипа 5 раз: число <video> константно, удалённые освобождены."""
    core_src = open(CORE_JS, encoding="utf-8").read()
    src = open(JS, encoding="utf-8").read()
    media_free_fn = _fn(core_src, "mediaFree")
    ins_free_all_fn = _fn(src, "insVidFreeAll")
    ipv_open_fn = _fn(src, "ipvOpen")

    js = f"""
    {_MOCK_ENV}
    let IPV = {{ vids: [], bufs: [], stats: {{}}, audio: [], segs: [], words: [] }};
    const PVPX = {{ xml: '', building: false }};
    const ITL = {{ pps: 0 }};
    // Слой рото — состояние верхнего уровня 85-inserts-view.js: ipvOpen сбрасывает его
    // на каждом открытии клипа (чужие маски прошлого клипа не должны остаться в кадре).
    // Стенду оно нужно рядом с вырезанной функцией, как PVPX/ITL выше.
    let IPV_ROTO = '', IPV_ROTOACC = -1, IPV_ROTODRAWN = -1;
    const IPV_ROTOSEQ = [];
    function vtStop(){{}}
    function ipvPause(){{ IPV.playing = false; }}
    function pvApplyStageAspect(){{}}
    function ipvMarks(){{}}
    function voiceWiring(){{}}
    function bufMake(P, st, io){{
      const b = document.createElement('video');
      st.insertBefore(b, io);
      P.bufs.push({{ el: b }});
    }}
    function camDeltas(){{ return [0, 0]; }}
    function camBufs(){{}}
    function itlFit(){{}}
    function ipvSeekTo(){{}}
    function ipvPlanFetch(){{}}
    async function pvProxyLoad(){{ return null; }}
    function pvProxyMerge(){{}}
    function ipvRenderPage(){{ return false; }}

    const fetch = async () => ({{
      json: async () => ({{
        cams: [{{ path: 'c1.mp4', name: 'c1' }}, {{ path: 'c2.mp4', name: 'c2' }}],
        segs: [{{ ci: 0, ts: 0, te: 10, src: 0 }}],
        audio: [{{ ci: 0, ts: 0, te: 10, src: 0 }}],
        words: [],
        fps: 60,
        dur: 10
      }})
    }});

    {media_free_fn}
    {ins_free_all_fn}
    {ipv_open_fn}

    async function run(){{
      const counts = [];
      for(let i = 0; i < 5; i++){{
        await ipvOpen('clip' + i + '.xml');
        counts.push(STAGE.querySelectorAll('video').length);
      }}
      // Проверяем все созданные видео: те, что уже не в STAGE (удалены), обязаны быть освобождены
      const currentInStage = new Set(STAGE.querySelectorAll('video'));
      const removedVideos = ALL_VIDEOS.filter(v => !currentInStage.has(v));
      const freedProperly = removedVideos.filter(v =>
        v._removedAttrs.includes('src') && v._loads > 0
      );
      console.log(JSON.stringify({{
        counts: counts,
        totalCreated: ALL_VIDEOS.length,
        removedCount: removedVideos.length,
        freedCount: freedProperly.length
      }}));
    }}
    run().catch(e => {{ console.error(e); process.exit(1); }});
    """
    out = json.loads(_run_node(js, tmp_path, "ipv_open_5.js"))
    # Число видео на сцене должно быть постоянным (не расти)
    assert len(set(out["counts"])) == 1, f"число видео в STAGE росло: {out['counts']}"
    assert out["removedCount"] > 0, "не было удалённых видео при повторном ipvOpen"
    assert out["freedCount"] == out["removedCount"], (
        f"не все удалённые видео были освобождены (src снят, load вызван): "
        f"{out['freedCount']} из {out['removedCount']}"
    )


@node
def test_trans_plan_change_frees_old_videos(tmp_path):
    """Смена плана переходов (новая подпись): старые элементы освобождены (removeAttribute('src'), load())."""
    core_src = open(CORE_JS, encoding="utf-8").read()
    src = open(JS, encoding="utf-8").read()
    media_free_fn = _fn(core_src, "mediaFree")
    trans_plan_fn = _fn(src, "ipvTransPlan")
    trans_fn = _fn(src, "ipvTrans")

    js = f"""
    {_MOCK_ENV}
    {media_free_fn}
    {trans_plan_fn}
    {trans_fn}

    const IPV = {{ fps: 60, plan: null, playing: false, vids: [1], transSig: '', transAsked: '' }};
    // План 1
    IPV.plan = {{
      trans: {{ media: 'Quick 1.mov', in: 0, w: 3840, h: 2160 }},
      inserts: [{{ t: 'video', media: 'v1.mp4', start: 1.0, end: 3.0, noexit: true }}]
    }};
    ipvTrans(1.5);
    const box = $('ipvtrans');
    assert(box, 'нет коробки #ipvtrans');
    const firstVideos = box.querySelectorAll('video').slice();
    assert(firstVideos.length > 0, 'нет видео в первом плане');

    // План 2 (другой файл перехода)
    IPV.plan = {{
      trans: {{ media: 'Quick 2.mov', in: 0, w: 3840, h: 2160 }},
      inserts: [{{ t: 'video', media: 'v2.mp4', start: 5.0, end: 7.0, noexit: true }}]
    }};
    ipvTrans(5.5);

    const freedCount = firstVideos.filter(v =>
      v._removedAttrs.includes('src') && v._loads > 0
    ).length;

    console.log(JSON.stringify({{
      firstCount: firstVideos.length,
      freedCount: freedCount
    }}));
    """
    out = json.loads(_run_node(js, tmp_path, "plan_change.js"))
    assert out["freedCount"] == out["firstCount"], (
        f"при смене плана старые видео не были освобождены: "
        f"{out['freedCount']} из {out['firstCount']}"
    )
