// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Maxim Si
// профили ИИ-провайдеров, словарь терминов, списки цензуры
//
// Часть интерфейса Reelsi. Файлы static/app/ грузятся ПО ПОРЯДКУ ИМЁН обычными
// <script>-тегами (не модулями): один общий скоуп, как было в едином app.js.
// Порядок важен — объявления функций поднимаются в пределах своего файла.

// ================= ИИ-профили (LM Studio / Anthropic / OpenRouter / OpenAI-совм.) =================
// Активный профиль живёт на СЕРВЕРЕ (reelsi/ai_config.json) — эндпоинты /api/ai_*
// резолвят его сами, поле model в запросы больше не кладём.
let AICFG=null;      // {active, profiles:{name:{provider,base_url,api_key(маска),model,reasoning}}, presets, reasoning_models}
let AIEDIT=null;     // имя профиля в форме настроек (null = создаём новый)
let AISNAMES=[];     // порядок имён для onclick по индексу (имена бывают с кавычками)
let REASONING_IDS=new Set();  // точные id моделей с reasoning (из /api/ai_models, OpenRouter supported_parameters)
// Уровни усилий reasoning, которые поддерживает КАЖДАЯ модель: {id:["low","high",...]}
// из /api/ai_models (OpenRouter reasoning.supported_efforts). По ним селект «Ум»
// показывает только то, что модель реально умеет: у deepseek-v4-flash 0423 нет
// low/medium, и выбранный low молча мапился в максимум, сжигая сотни тысяч токенов.
let MODEL_EFFORTS={};  // {model_id_lower: [efforts]}
// Возможности модели из каталога models.dev (задание BY): помимо уровней ещё
// контекст, потолок вывода и цена — их показываем рядом с моделью.
let MODEL_CAPS={};     // {model_id_lower: {efforts, ctx_limit, out_limit, cost, default, ...}}
// Модели, думающие ПО УМОЛЧАНИЮ (из каталога: toggle в reasoning_options). Для них
// «off» — это активное действие, и селект «Ум» обязан это показать.
let MODEL_DEFAULTS={};  // {model_id_lower: true|false}
// Уровни, которые понимает наша система (REASONING_LEVELS на сервере).
const ALL_LEVELS=['off','minimal','low','medium','high','xhigh','max'];
// Уровни «Ум» для модели: off + уровни из каталога (efforts).
// 1. Для моделей с известными efforts — ТОЛЬКО поддерживаемые ею уровни (DeepSeek: low/high/max, Gemini: minimal..high и т.д.).
// 2. Для известных моделей без reasoning — только ['off'].
// 3. Для неизвестных кастомных моделей (свой сервер, кастомный URL) — ALL_LEVELS.
function modelReasoningLevels(m){
  m=(m||'').toLowerCase(); if(!m)return null;
  const e=MODEL_EFFORTS[m];
  if(e&&e.length){
    const lv=['off'].concat(e.filter(l=>l!=='off'&&l!=='none'));
    return lv.length?lv:['off'];
  }
  const cap=MODEL_CAPS[m];
  if(cap&&cap.reasoning===false){
    return ['off'];
  }
  if(REASONING_IDS.size>0&&!modelSupportsReasoning(m)){
    return ['off'];
  }
  return ALL_LEVELS;}
// модель поддерживает reasoning? точное совпадение по списку провайдера ИЛИ
// подстрока из AICFG.reasoning_models (фолбэк, когда список не подтянут).
function modelSupportsReasoning(m){
  m=(m||'').toLowerCase(); if(!m)return false;
  if(REASONING_IDS.has(m))return true;
  return (AICFG&&AICFG.reasoning_models||[]).some(s=>m.indexOf(s.toLowerCase())>=0);
}
// Уровень «ума» больше НЕ живёт в профиле: он задаётся на каждый шаг отдельно
// (см. setStepReasoning). Здесь под полем «Модель» — что выбранная модель реально
// умеет (из каталога models.dev): уровни reasoning, контекст, потолок вывода и цена.
function aiSetModelInput(){
  const el=$('ais_model'); if(!el)return;
  const m=(el.value||'').trim();
  const hint=$('ais_reas'); if(!hint)return;
  const e=modelReasoningLevels(m);
  if(!m){hint.textContent='';return;}
  const cap=MODEL_CAPS[m.toLowerCase()]||{};
  // Цена и лимиты — рядом с моделью (задание BY): каталог знает, что у модели
  // внутри, показывать не догадку, а его.
  const ctxt=cap.ctx_limit?fmtN(cap.ctx_limit):null;
  const cout=cap.out_limit?fmtN(cap.out_limit):null;
  const cost=(cap.cost&&cap.cost.input!=null)?('$'+cap.cost.input+'/'+(cap.cost.output!=null?cap.cost.output:'?')+'M'):null;
  const lim=(ctxt||cout)?' · '+t('контекст')+' '+(ctxt||'?')+(cout?', '+t('вывод')+' '+cout:'')+(cost?' · '+cost:''):'';
  if(!e||(e.length===1&&e[0]==='off')){
    const knownNoReas=(cap&&cap.reasoning===false)||(REASONING_IDS.size>0&&!modelSupportsReasoning(m));
    hint.textContent=knownNoReas
      ?t('модель без reasoning — доступен только Off')
      :t('уровни ума: настраиваются свободно (off · minimal · low · medium · high · xhigh · max)');
    hint.textContent+=lim;
    return;}
  hint.textContent=t('уровни ума: ')+e.join(' · ')+lim;
  if(e.length===1&&e[0]==='off')hint.textContent+=t(' (модель без reasoning)');}
function fmtN(n){n=Number(n);if(!isFinite(n))return '?';
  return n>=1000000?(n/1000000)+'M':n>=1000?(n/1000)+'k':String(n);}

async function loadAIProfiles(){
  try{AICFG=await (await fetch('/api/ai_config')).json();}catch(e){return;}
  MODEL_DEFAULTS=(AICFG&&AICFG.reasoning_defaults)||{};
  // Каталог уже на сервере (aicut.catalog) — уровни и лимиты доступны сразу,
  // «Обновить список» нужен только для списка моделей провайдера.
  MODEL_CAPS=(AICFG&&AICFG.model_caps)||{};
  MODEL_EFFORTS={};
  for(const [mid,c] of Object.entries(MODEL_CAPS)){ if(c&&c.efforts)MODEL_EFFORTS[mid]=c.efforts; }
  fillAIProfileSelects();}
// «Ум» шага: селекты <select data-reas="cut|yellow|inserts|intro"> на страницах.
// Уровень хранится на сервере в ai_config.reasoning_steps и применяется только к
// своему шагу — нарезке думать надо, жёлтым/вставкам/интро нет (см. aicut.py).
const REAS_TITLES={off:'Off — без раздумий',minimal:'Minimal — чуть подумать',
  low:'Low — коротко подумать',medium:'Medium — подумать',high:'High — думать долго',
  xhigh:'X-High — думать очень долго',max:'Max — думать максимально'};
function fillStepReasoning(){if(!AICFG)return;
  const steps=AICFG.reasoning_steps||{}, lv=AICFG.reasoning_levels||ALL_LEVELS;
  // effective — что РЕАЛЬНО уйдёт в API: сервер понизил невалидный для модели
  // уровень по каталогу (medium -> low у deepseek-v4-flash). Показываем его,
  // иначе селект и сводка снова врут (три разных ответа на один вопрос).
  const eff=AICFG.reasoning_effective||{};
  document.querySelectorAll('[data-reas]').forEach(sel=>{
    const step=sel.dataset.reas, cur=eff[step]||steps[step]||'off';
    // Модель шага — из привязки шага или активного профиля; если её уровни
    // известны из каталога, показываем только их: выбор невалидного уровня для
    // модели раньше молча мапился провайдером в максимум и жёг сотни тысяч токенов.
    const pn=(AICFG.step_profiles||{})[step]||AICFG.active;
    const pm=(AICFG.profiles||{})[pn]||{};
    const eff2=modelReasoningLevels(pm.model);
    // Уровни из каталога (efforts) НЕ режем нашим фиксированным списком: там могут
    // быть max/xhigh/minimal, которых в ALL_LEVELS нет, но которые модель умеет.
    const lvs=eff2||lv;
    // Модель думает сама по умолчанию (deepseek-v4-flash и пр.) — «off» у неё это
    // активное выключение, а не «ничего не делать». Подпись это доносит (BW).
    const thinks=!!(MODEL_DEFAULTS&&MODEL_DEFAULTS[(pm.model||'').toLowerCase()]);
    const noReasoning=(lvs.length===1&&lvs[0]==='off');
    sel.innerHTML=lvs.map(l=>{
      let cap=REAS_TITLES[l]||l;
      if(l==='off'&&thinks)cap='Off — думает сама, выключаем явно';
      return '<option value="'+l+'"'+(l===cur?' selected':'')+'>'
        +esc(t(cap))+'</option>';
    }).join('');
    // Если текущий уровень модель не поддерживает — не оставлять «невидимое
    // значение»: селект встанет на первый доступный (off). Эффективный уровень
    // по определению есть в списке модели, это чистая страховка.
    sel.value=(lvs.indexOf(cur)>=0)?cur:lvs[0];
    sel.disabled=noReasoning;
    // Хранимое и эффективное разошлись (модель не умеет выбранный уровень) —
    // честно говорим об этом под селектом, как styleinfo: «модель не умеет
    // medium — идёт low». Без этого юзер снова не узнал бы, чем реально режет.
    const hint=$('reas_hint_'+step);
    if(hint){
      const stored=steps[step];
      if(stored!=null&&stored!==cur){
        hint.textContent=t('{model} не умеет {stored} — идёт {effective}',
          {model:pm.model||pn,stored:stored,effective:cur});
        hint.style.display='';
      }else{hint.textContent='';hint.style.display='none';}
    }});}
async function setStepReasoning(sel){
  const step=sel.dataset.reas, level=sel.value;
  // Сервер недоступен (рестарт бэкенда) — молча терять настройку нельзя: селект
  // показывал новое, сервер хранил старое. Ловим, тостим и возвращаем контрол
  // к сохранённому состоянию (задание по UI-состояниям).
  let d;
  try{d=await (await fetch('/api/ai_config',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'set_reasoning_step',step,level})})).json();}
  catch(e){toast(t('Сервер не ответил: ')+e);fillStepReasoning();return;}
  if(d.error){toast('⚠ '+errText(d));fillStepReasoning();return;}
  AICFG.reasoning_steps=d.reasoning_steps||AICFG.reasoning_steps;
  AICFG.reasoning_effective=d.reasoning_effective||AICFG.reasoning_effective;
  fillStepReasoning();cutSummary();markupSummary();
  toast(t('ум · ')+(step==='cut'?t('нарезка'):step)+': '+level);}
// «Модель» шага: селекты <select data-prof="cut|yellow|inserts|intro">. Профиль
// (какая МОДЕЛЬ думает) выбирается ОТДЕЛЬНО на каждый шаг — сервер хранит в
// ai_config.step_profiles, пусто = общий active (см. aicut.step_profile).
function fillStepProfiles(){if(!AICFG)return;
  const sp=AICFG.step_profiles||{}, names=Object.keys(AICFG.profiles||{});
  document.querySelectorAll('[data-prof]').forEach(sel=>{
    const step=sel.dataset.prof, cur=sp[step]||AICFG.active;
    sel.innerHTML=names.map(n=>'<option'+(n===cur?' selected':'')+'>'+esc(n)+'</option>').join('');
    sel.value=cur;});}
async function setStepProfile(sel){
  const step=sel.dataset.prof, name=sel.value;
  // Сервер недоступен — молча терять смену модели шага нельзя (селект показал бы
  // новое, сервер хранил бы старое): тост и возврат контрола к сохранённому.
  let d;
  try{d=await (await fetch('/api/ai_config',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'set_step_profile',step,name})})).json();}
  catch(e){toast(t('Сервер не ответил: ')+e);fillStepProfiles();return;}
  if(d.error){toast('⚠ '+errText(d));fillStepProfiles();return;}
  AICFG.step_profiles=d.step_profiles||AICFG.step_profiles;
  AICFG.reasoning_effective=d.reasoning_effective||AICFG.reasoning_effective;
  fillStepProfiles();cutSummary();markupSummary();
  toast(t('модель · ')+(step==='cut'?t('нарезка'):step)+': '+name);}
async function setCutAsr(name){
  let d;
  try{d=await (await fetch('/api/ai_config',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'set_active_cut_asr',name:name||'gigaam'})})).json();}
  catch(e){toast(t('Сервер не ответил: ')+e);fillCutAsr();return;}
  if(d.error){toast('⚠ '+errText(d));fillCutAsr();return;}
  AICFG.active_cut_asr=d.active_cut_asr||name;
  fillCutAsr();cutSummary();
  toast(t('движок нарезки: ')+engLabel(AICFG.active_cut_asr));}
function fillCutAsr(){
  const sel=$('cut_asr');if(!sel)return;
  const list=(typeof ASR_ENGINES!=='undefined'?ASR_ENGINES:[]).filter(e=>e&&e.cut);
  const cur=(AICFG&&AICFG.active_cut_asr)||'gigaam';
  if(!list.length){
    sel.innerHTML='<option value="gigaam">'+t('GigaAM-v3 (RU, CTC)')+'</option>';
    sel.value='gigaam';
    return;
  }
  sel.innerHTML='';
  [...new Set(list.map(e=>e.lang))].forEach(l=>{
    const g=document.createElement('optgroup');g.label=LANGNAME[l]||l;
    list.filter(e=>e.lang===l).forEach(e=>{
      const o=document.createElement('option');o.value=e.id;o.textContent=t(e.label);g.appendChild(o);});
    sel.appendChild(g);});
  const exists=list.some(e=>e.id===cur);
  sel.value=exists?cur:(list[0]?list[0].id:'gigaam');
}
// ================= Ступени нарезки (задание GG) =================
// Панель ступеней рисуется ПО ДАННЫМ С СЕРВЕРА (/api/cutstages).
// Своей копии списка ступеней в JS нет — единственный источник правды cutstages.py.
let CUT_STAGES_META=null;
let CUT_DEFAULTS={};
let CUT_THRESH_DEFAULTS={};
let CUT_STAGES={};
let CUT_THRESHOLDS={};
let ASR_HOLD_TIMER=null;

async function loadCutStages(){
  if(CUT_STAGES_META){renderCutStagesUI();return;}
  try{
    const d=await (await fetch('/api/cutstages')).json();
    if(d&&d.stages){
      CUT_STAGES_META=d.stages;
      CUT_DEFAULTS=d.defaults||{};
      CUT_THRESH_DEFAULTS=d.threshold_defaults||d.thresholds||{};
      if(!Object.keys(CUT_STAGES).length){
        CUT_STAGES=Object.assign({},CUT_DEFAULTS);
      }
      if(!Object.keys(CUT_THRESHOLDS).length){
        CUT_THRESHOLDS=Object.assign({},CUT_THRESH_DEFAULTS);
      }
      renderCutStagesUI();
      cutSummary();
    }
  }catch(e){
    uiLog(t('⚠ Не удалось загрузить ступени нарезки: ')+e);
  }
}

function renderCutStagesUI(){
  const listEl=$('cut_stages_list');
  if(!listEl||!CUT_STAGES_META)return;

  const pausesVal=(CUT_STAGES&&CUT_STAGES.pauses)||(CUT_DEFAULTS&&CUT_DEFAULTS.pauses)||'speech';
  const branch=(pausesVal==='loud')?'vad':'gigaam';

  const needsAsr=CUT_STAGES_META.some(s=>s.key!=='asr'&&s.needs&&s.needs.includes('asr')
    &&s.branches&&s.branches.includes(branch)&&CUT_STAGES[s.key]);
  const asrVal=(pausesVal==='speech')||needsAsr;

  if(!listEl.children.length){
    listEl.innerHTML='';
    CUT_STAGES_META.forEach(s=>{
      if(s.panel===false)return;
      const k=s.key;
      const row=document.createElement('div');
      row.className='setrow';
      row.id='stage_row_'+k;
      row.style.minHeight='var(--h-sm)';
      row.style.alignItems='center';
      row.style.justifyContent='space-between';

      if(k==='pauses'){
        row.innerHTML='<label>'+t(s.label)+' <span class="i" data-t="'+esc(t(s.hint))+'">!</span></label>'
          +'<div class="seg" id="stage_seg_pauses">'
          +'<label><input type="radio" name="stage_pauses" value="off" onchange="setStagePauses(this.value)"> '+t('не резать')+'</label>'
          +'<label><input type="radio" name="stage_pauses" value="speech" onchange="setStagePauses(this.value)"> '+t('по речи')+'</label>'
          +'<label><input type="radio" name="stage_pauses" value="loud" onchange="setStagePauses(this.value)"> '+t('по громкости')+'</label>'
          +'</div>';
      }else if(k==='asr'){
        row.innerHTML='<div style="display:flex;align-items:center;gap:7px;flex-wrap:wrap">'
          +'<label class="chk" id="stage_chklabel_'+k+'" style="margin:0"><input type="checkbox" id="stage_chk_'+k+'"> '+t(s.label)+'</label>'
          +'<span class="i" data-t="'+esc(t(s.hint))+'">!</span>'
          +'<span class="hint" id="asr_holders_hint" style="display:none;color:var(--mut);font-size:12px"></span>'
          +'</div>';
      }else{
        row.innerHTML='<div style="display:flex;align-items:center;gap:7px">'
          +'<label class="chk" id="stage_chklabel_'+k+'" style="margin:0"><input type="checkbox" id="stage_chk_'+k+'" onchange="setStageValue(\''+k+'\',this.checked)"> '+t(s.label)+'</label>'
          +'<span class="i" data-t="'+esc(t(s.hint))+'">!</span>'
          +'</div>';
      }
      listEl.appendChild(row);
    });
    tipArm(listEl);
  }

  CUT_STAGES_META.forEach(s=>{
    if(s.panel===false)return;
    const k=s.key;
    const inBranch=s.branches&&s.branches.includes(branch);
    const row=$('stage_row_'+k);

    if(k==='pauses'){
      document.querySelectorAll('input[name="stage_pauses"]').forEach(r=>{
        r.checked=(r.value===pausesVal);
      });
      segUI();
    }else if(k==='asr'){
      const chk=$('stage_chk_asr');
      if(chk){
        chk.checked=asrVal;
        chk.disabled=!inBranch;
        chk.onclick=onAsrClick;
      }
    }else{
      const chk=$('stage_chk_'+k);
      if(chk){
        chk.disabled=!inBranch;
        const curVal=(CUT_STAGES[k]!==undefined)?CUT_STAGES[k]:s.default;
        chk.checked=!!curVal;
      }
    }
    if(row)row.style.opacity=inBranch?'1':'0.4';
  });

  const vadBox=$('vad_thresholds_box');
  if(vadBox){
    vadBox.style.display=(branch==='vad')?'flex':'none';
    const vt=$('vad_thresh'),vms=$('vad_min_silence'),vp=$('vad_pad');
    const th=Object.assign({},CUT_THRESH_DEFAULTS,CUT_THRESHOLDS);
    if(vt&&th.vad_thresh!==undefined)vt.value=th.vad_thresh;
    if(vms&&th.min_silence!==undefined)vms.value=th.min_silence;
    if(vp&&th.pad!==undefined)vp.value=th.pad;
  }
}

function onAsrClick(e){
  const pausesVal=(CUT_STAGES&&CUT_STAGES.pauses)||(CUT_DEFAULTS&&CUT_DEFAULTS.pauses)||'speech';
  const branch=(pausesVal==='loud')?'vad':'gigaam';

  const holders=[];
  if(pausesVal==='speech'){
    holders.push({key:'pauses',label:t('паузы')});
  }
  if(CUT_STAGES_META){
    CUT_STAGES_META.forEach(s=>{
      if(s.key!=='asr'&&s.needs&&s.needs.includes('asr')&&s.branches&&s.branches.includes(branch)&&CUT_STAGES[s.key]){
        holders.push({key:s.key,label:t(s.label).toLowerCase()});
      }
    });
  }

  const chk=$('stage_chk_asr');
  if(holders.length>0){
    if(chk)chk.checked=true;
    if(e)e.preventDefault();
    highlightHolders(holders);
    const hintEl=$('asr_holders_hint');
    if(hintEl){
      const names=holders.map(h=>h.label).join(', ');
      hintEl.textContent=t('держат: {names} — сними их, чтобы выключить',{names:names});
      hintEl.style.display='';
    }
    clearTimeout(ASR_HOLD_TIMER);
    ASR_HOLD_TIMER=setTimeout(()=>{
      clearHolderHighlights();
      const hEl=$('asr_holders_hint');
      if(hEl)hEl.style.display='none';
    },3500);
  }else{
    if(chk)chk.checked=false;
  }
}

function highlightHolders(holders){
  clearHolderHighlights();
  holders.forEach(h=>{
    const row=$('stage_row_'+h.key);
    if(row){
      row.classList.add('stage-holder-highlight');
      row.style.outline='1px solid var(--iron)';
      row.style.borderRadius='var(--r)';
    }
  });
}

function clearHolderHighlights(){
  document.querySelectorAll('.stage-holder-highlight').forEach(el=>{
    el.classList.remove('stage-holder-highlight');
    el.style.outline='';
    el.style.borderRadius='';
  });
}

function setStagePauses(val){
  CUT_STAGES.pauses=val;
  renderCutStagesUI();
  saveState();
  cutSummary();
}

function setStageValue(key,val){
  CUT_STAGES[key]=!!val;
  if(val&&CUT_STAGES_META){
    const meta=CUT_STAGES_META.find(s=>s.key===key);
    if(meta&&meta.needs){
      meta.needs.forEach(dep=>{if(dep!=='asr')CUT_STAGES[dep]=true;});
    }
  }
  renderCutStagesUI();
  saveState();
  cutSummary();
}

function setVadThreshold(key,val){
  const num=parseFloat(val);
  if(!isNaN(num)){
    CUT_THRESHOLDS[key]=num;
    saveState();
  }
}
function fillAIProfileSelects(){if(!AICFG)return;
  fillStepReasoning();fillStepProfiles();fillCutAsr();
  const om=$('omniprofile');
  if(om){const cur=AICFG.active_omni||'__local__';
    const LOCALS=['__local__','__gigaam__'];
    om.innerHTML='<option value="__local__">'+t('Локально: Qwen2.5-Omni')+'</option>'
      +'<option value="__gigaam__">'+t('Локально: GigaAM-v3 (RU)')+'</option>'
      +Object.keys(AICFG.profiles).map(n=>'<option value="'+esc(n)+'"'+(n===cur?' selected':'')+'>'+esc(n)+'</option>').join('');
    om.value=(LOCALS.includes(cur)||AICFG.profiles[cur])?cur:'__local__';}
  const im=$('ais_image');
  if(im){const cur=AICFG.active_image||'__off__';
    im.innerHTML='<option value="__off__">'+t('выключено')+'</option>'
      +Object.keys(AICFG.profiles).map(n=>'<option value="'+esc(n)+'"'+(n===cur?' selected':'')+'>'+esc(n)+'</option>').join('');
    im.value=(cur!=='__off__'&&AICFG.profiles[cur])?cur:'__off__';}
  const rb=$('ais_rembg');
  if(rb){rb.checked=(AICFG.image_rembg!==false);
    // снятие фона и «генерить при разметке» имеют смысл только при включённой генерации
    $('ais_rembgRow').style.display=imgGenOn()?'flex':'none';}
  const gg=$('glitchglow');
  if(gg)gg.value=AICFG.glitch_glow||'builtin';
  const gr=$('illgenRow');if(gr)gr.style.display=imgGenOn()?'flex':'none';
  fillVideoControls();
  cutSummary();markupSummary();
}
// Общие настройки видео: профиль (провайдер+ключ) и модель живут в ⚙, но отдельная
// вкладка перестраивает свои ручные поля по тому же единственному выбору модели.
function fillVideoControls(){if(!AICFG)return;
  const vs=$('ais_video');
  if(vs){const cur=AICFG.active_video||'__off__';
    vs.innerHTML='<option value="__off__">'+t('выключено')+'</option>'
      +Object.keys(AICFG.profiles).map(n=>'<option value="'+esc(n)+'"'+(n===cur?' selected':'')+'>'+esc(n)+'</option>').join('');
    vs.value=(cur!=='__off__'&&AICFG.profiles[cur])?cur:'__off__';}
  const ms=$('vid_model'),list=AICFG.video_models||[];
  if(ms){const cur=AICFG.video_model||(list[0]||{}).id||'';
    const known=list.some(m=>m.id===cur);
    ms.innerHTML=list.map(m=>'<option value="'+esc(m.id)+'"'+(m.id===cur?' selected':'')+'>'
        +esc(t(m.label||m.id))+'</option>').join('')
      // модель из профиля/каталога может быть не из списка — не терять её молча
      +(known||!cur?'':'<option value="'+esc(cur)+'" selected>'+esc(cur)+t(' (из профиля)')+'</option>');
    ms.value=cur;}
  vidApplyModel();
  vidRestoreForm();      // после vidApplyModel: списки длин/разрешений наполняет он
  vidProfSummary();
}
// Возможности ВЫБРАННОЙ модели: встроенный каталог (приходит с /api/ai_config, есть
// сразу) поверх которого ложится ответ «Свериться с провайдером», если он про неё же.
function vidModelId(){const s=$('vid_model');return (s&&s.value)||(AICFG&&AICFG.video_model)||'';}
function vidCaps(){const id=vidModelId();
  if(VIDCAPS&&VIDCAPS.caps&&VIDCAPS.model===id)return VIDCAPS.caps;
  const m=((AICFG&&AICFG.video_models)||[]).find(x=>x.id===id);
  return m?m.caps:null;}
async function setVideoModel(id){
  const d=await (await fetch('/api/ai_config',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'set_video_model',model:id})})).json();
  if(d.error){toast('⚠ '+errText(d));return;}
  AICFG.video_model=d.video_model||id;
  // сервер атомарно сбросил устаревшее разрешение (несовместимое с новой моделью)
  AICFG.video_resolution=d.video_resolution||'';
  vidApplyModel();vidProfSummary();}
// Разрешение генерации видео — ОБЩАЯ настройка в ⚙. Список — только поддерживаемые
// моделью + «по умолчанию» (провайдер сам). Значение живёт на сервере
// (ai_config.video_resolution), резолвится против caps модели.
function vidResList(){
  const c=vidCaps();
  return (c&&c.resolutions&&c.resolutions.length)?c.resolutions
    :(AICFG&&AICFG.video_resolutions)||['480p','720p','1080p'];
}
function vidResValue(){return (AICFG&&AICFG.video_resolution)||'';}
function vidSupportsRes(res){
  if(!res)return false;
  const list=vidResList();
  return list.some(r=>String(r).toLowerCase()===String(res).toLowerCase());
}
function vidFillResolution(){
  const sel=$('vidres'),inp=$('vid_res'),cur=vidResValue();
  if(sel){
    const known=vidSupportsRes(cur);
    sel.innerHTML='<option value="">'+esc(t('по умолч.'))+'</option>'
      +vidResList().map(r=>'<option value="'+esc(r)+'"'
        +(String(r).toLowerCase()===String(cur).toLowerCase()?' selected':'')+'>'+esc(r)+'</option>').join('');
    sel.value=known?cur:'';   // устаревшее — показываем «по умолчанию», скрытого local state нет
  }
  if(inp)inp.value=cur||'';
}
async function setVideoResolution(res){
  const d=await (await fetch('/api/ai_config',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'set_video_resolution',resolution:res||''})})).json();
  if(d.error){toast('⚠ '+errText(d));vidFillResolution();return;}
  AICFG.video_resolution=d.video_resolution||'';
  vidFillResolution();vidProfSummary();}
// Перестроить поля под модель: в списках только то, что она принимает; чего не умеет
// (seed, звук) — выключаем, чтобы не отправлять заведомо лишнее.
function vidApplyModel(){
  const c=vidCaps();
  vidFillResolution();
  vidRefreshAutoShape();
  const sd=$('vid_seed');
  if(sd){const on=!c||c.seed!==false;sd.disabled=!on;sd.placeholder=on?t('авто'):t('не поддерживает');
    if(!on)sd.value='';}
  const au=$('vid_audio'),ab=$('vid_audiobox');
  if(au){const on=!c||c.audio!==false;au.disabled=!on;if(!on)au.checked=false;
    if(ab)ab.style.opacity=on?'':'0.5';}
  renderVidRefs();
}
function vidRefreshAutoShape(){
  const c=vidCaps()||{},durations=(c.durations||[]).map(Number).filter(Number.isFinite).sort((a,b)=>a-b);
  const refs=(typeof VREFS==='undefined'?[]:VREFS).filter(r=>(r.url||'').trim());
  const video=refs.find(r=>((r.kind||vidUrlKind(r.url))==='video'));
  const image=refs.find(r=>((r.kind||vidUrlKind(r.url))==='image'));
  const source=(video&&video.w&&video.h)?video:(image&&image.w&&image.h)?image:null;
  let target=video&&Number(video.duration)>0?Math.ceil(Number(video.duration)):null;
  let duration=durations.length?(target==null?durations[0]:(durations.find(x=>x>=target)||durations[durations.length-1])):4;
  const aspects=c.aspect_ratios||['9:16','16:9','1:1'];
  const parse=s=>{const p=String(s).split(':').map(Number);return p.length===2&&p[0]>0&&p[1]>0?p[0]/p[1]:0;};
  let aspect=aspects[0]||'';
  if(source){const ratio=Number(source.w)/Number(source.h),scores=aspects.map((a,i)=>({a,i,v:parse(a)})).filter(x=>x.v>0);
    if(ratio>0&&scores.length)aspect=scores.sort((a,b)=>Math.abs(Math.log(ratio/a.v))-Math.abs(Math.log(ratio/b.v))||a.i-b.i)[0].a;}
  const d=$('vid_dur'),a=$('vid_aspect');if(d)d.value=duration?String(duration):'';if(a)a.value=aspect;
  const hint=$('vidshape');if(hint)hint.textContent=source?(video&&source===video?t('по видео-референсу'):t('по фото-референсу')):t('fallback — добавь проверенный референс');
}
function vidGenOn(){return !!(AICFG&&AICFG.active_video&&AICFG.active_video!=='__off__');}
function vidProfSummary(){const el=$('vidprofsum');if(!el||!AICFG)return;
  const name=AICFG.active_video||'__off__';
  if(name==='__off__'){el.textContent=t('профиль не выбран');el.dataset.t=t('Выбери профиль видео в настройках');}
  else{const p=AICFG.profiles[name]||{};el.textContent=name+' · '+(p.provider||'')
      +' · '+(AICFG.video_model||'');
    el.dataset.t=name+' — '+(p.base_url||'')+t(' · модель ')+(AICFG.video_model||'');}
  const hint=$('vidhint');
  if(hint&&!vidGenOn())hint.textContent=t('Выбери профиль «Видео» (нужен OpenRouter-ключ).');
  else if(hint)hint.textContent='';}
async function setVideoProfile(name){
  const d=await (await fetch('/api/ai_config',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'set_active_video',name})})).json();
  if(d.error)toast('⚠ '+errText(d));
  else AICFG.active_video=d.active_video;
  fillVideoControls();renderInsHost();
  vidSyncCaps(true);}              // другой профиль — другой ключ и другой каталог
// Вкладки настроек сгруппированы по задачам: подключения, шаги, генерация и инструменты. Всё, что здесь
// выбрано, живёт на сервере (ai_config.json) или в состоянии UI — открыл ⚙, увидел то же.
function aiSetTab(tab){
  const allowed=['models','cut','markup','generation','words','tools'];
  if(tab==='common')tab='tools';
  if(!allowed.includes(tab))tab='models';
  document.querySelectorAll('#aisTabs .tab').forEach(b=>{const on=(b.dataset.tab===tab);
    b.classList.toggle('on',on);b.setAttribute('aria-selected',on?'true':'false');});
  allowed.forEach(k=>{const el=$('aistab_'+k);if(el){const on=k===tab;el.style.display=on?'':'none';el.setAttribute('aria-hidden',on?'false':'true');}});
}
// Короткая сводка «что сейчас выбрано» прямо на странице — настройки уехали в ⚙,
// и без неё было не видно, какой моделью считается нарезка/разметка.
function stepProf(step){return ((AICFG&&AICFG.step_profiles)||{})[step]||(AICFG&&AICFG.active)||'—';}
function cutSummary(){const el=$('cutsum');if(!el||!AICFG)return;
  // Тот же эффективный уровень, что и в селекте «Ум»: сервер понижает невалидный
  // для модели (medium -> low у deepseek-v4-flash), и сводка обязана показывать
  // именно итог, а не хранимое — иначе снова «показываем одно, шлём другое».
  const lv=((AICFG.reasoning_effective||{}).cut)||((AICFG.reasoning_steps||{}).cut)||'off';
  const cutAsr=(AICFG&&AICFG.active_cut_asr)||'gigaam';
  const cutL=engLabel(cutAsr);
  const sp=(typeof SPEAKERS!=='undefined')?SPEAKERS[val('speaker')]:null;
  const n=sp?Object.keys(sp.cut||{}).length:0;

  let offStr='';
  if(CUT_STAGES_META){
    const pausesVal=(CUT_STAGES&&CUT_STAGES.pauses)||(CUT_DEFAULTS&&CUT_DEFAULTS.pauses)||'speech';
    const branch=(pausesVal==='loud')?'vad':'gigaam';
    const offList=[];
    if(pausesVal==='off')offList.push(t('паузы'));
    CUT_STAGES_META.forEach(s=>{
      if(s.key!=='pauses'&&s.key!=='asr'&&s.key!=='draft'&&s.branches&&s.branches.includes(branch)){
        const v=(CUT_STAGES[s.key]!==undefined)?CUT_STAGES[s.key]:s.default;
        if(!v)offList.push(t(s.label).toLowerCase());
      }
    });
    if(branch==='vad'){
      offStr=t(' · VAD (по громкости)');
      if(offList.length)offStr+=t(' · выкл: ')+offList.join(', ');
    }else if(offList.length){
      offStr+=t(' · выкл: ')+offList.join(', ');
    }
  }

  el.textContent=t('ИИ: ')+stepProf('cut')+t(' · ум: ')+lv+t(' · движок: ')+cutL
    +offStr
    +(sp?(t(' · спикер: ')+(sp.label||'')
        +(n?t(' ({n} порог. изменено)',{n:n}):t(' (пороги по умолчанию)'))
        +(sp.breath_p_cut?t(' + вздохи с {p}',{p:sp.breath_p_cut}):'')
        +((sp.hint||'').trim()?t(' + своя поправка ИИ'):'')
        // стиль AE зависит от спикера, но живёт на шаге 3 — на первом экране его иначе не видно
        +(sp.style?t(' · стиль AE: {s}',{s:(stylesMap()[sp.style]||{}).label||sp.style}):'')):'');}
// STYLES объявлен ниже по файлу — на момент разбора cutSummary его ещё нет
function stylesMap(){return (typeof STYLES!=='undefined'&&STYLES)||{};}
// В шапке разметки строка живёт рядом с кнопками — длинной она распирала ряд.
// Показываем коротко (модель жёлтых, обрезанную до 18 символов), всё — в тултипе.
function markupSummary(){const el=$('mkupsum');if(!el||!AICFG)return;
  const st=AICFG.reasoning_steps||{}, ef=AICFG.reasoning_effective||{};
  // Уровни — эффективные (как в селектах «Ум»): сервер понизил невалидный для
  // модели, и тултип обязан показывать итог, а не хранимое.
  const p=String(stepProf('yellow'));
  el.textContent=p.length>18?(p.slice(0,17)+'…'):p;
  el.dataset.t=t('Модель — жёлтые: {yellow}, вставки: {inserts}, интро: {intro}',
      {yellow:stepProf('yellow'),inserts:stepProf('inserts'),intro:stepProf('intro')})
    +'\n'+t('Субтитры: {s}',{s:engLabel(val('subengine')||'whisper')})
    +'\n'+t('Ум — жёлтые: {y}, вставки: {i}, интро: {n}',
      {y:ef.yellow||st.yellow||'off',i:ef.inserts||st.inserts||'off',n:ef.intro||st.intro||'off'})
    +'\n'+t('Генерация картинок: {v}',{v:imgGenOn()?t('включена'):t('выключена')});}
// ---- словарь терминов: список названий в ⚙ → «Слова», варианты копятся сами ----
// В textarea только НАЗВАНИЯ. Запомненные ослышки туда не выводим и при сохранении не
// теряем (сервер их сохраняет сам): их десятки, и редактировать их руками незачем.
let TERMS=null;
async function loadTerms(){const el=$('terms_list');if(!el)return;
  try{const d=await (await fetch('/api/terms')).json();
    if(d.error)return;
    TERMS=d.terms||[];
    if(document.activeElement!==el)el.value=TERMS.map(x=>x.term).join('\n');
    termsSummary();}catch(e){}}
function termsSummary(){const el=$('terms_res');if(!el)return;
  const n=(TERMS||[]).length,v=(TERMS||[]).reduce((a,x)=>a+((x.variants||[]).length),0);
  el.textContent=n?t('{n} назв.',{n:n})+(v?t(' · запомнено ослышек: {v}',{v:v}):'')
    :t('пусто — ASR ничего не правит');
  renderTermVariants();}
// Запомненные ослышки — ЧИПАМИ под названием: learn будет ошибаться и дальше, и без
// крестика чинить это пришлось бы в блокноте. Крестик шлёт тот же /api/terms полным
// списком: set_terms сохраняет переданные варианты, так что удаление доезжает.
function renderTermVariants(){const el=$('terms_var');if(!el)return;
  const items=(TERMS||[]).filter(it=>(it.variants||[]).length);
  el.style.display=items.length?'':'none';
  el.innerHTML=items.map((it,ti)=>'<div style="display:flex;gap:6px;align-items:flex-start;flex-wrap:wrap">'
    +'<span class="muted" style="font-size:12px;min-width:96px;line-height:20px;text-transform:uppercase">'+esc(it.term)+'</span>'
    +(it.variants||[]).map((v,vi)=>'<span class="tag" style="text-transform:none;letter-spacing:0">'+esc(v)
      +'<span class="tagx" tabindex="0" role="button" aria-label="'+t('Убрать вариант')+'" data-t="'+t('Убрать запомненный вариант «{v}»',{v:v})+'" onclick="event.stopPropagation();delTermVariant('+ti+','+vi+')">'+ico('x')+'</span></span>').join('')
    +'</div>').join('');}
async function delTermVariant(ti,vi){const it=(TERMS||[])[ti];if(!it||!(it.variants||[])[vi])return;
  const terms=TERMS.map(x=>({term:x.term,variants:(x.variants||[]).slice()}));
  terms[ti].variants.splice(vi,1);
  try{const d=await (await fetch('/api/terms',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({terms})})).json();
    if(d.error){toast('⚠ '+errText(d));return;}
    TERMS=d.terms||[];termsSummary();uiLog(t('вариант «{v}» убран',{v:it.variants[vi]}));
  }catch(e){toast(t('⚠ сервер не ответил: ')+e);}}
async function saveTerms(){const el=$('terms_list');if(!el)return;
  const list=(el.value||'').split('\n').map(s=>s.trim()).filter(Boolean);
  try{const d=await (await fetch('/api/terms',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({terms:list})})).json();
    if(d.error){toast('⚠ '+errText(d));return;}
    TERMS=d.terms||[];termsSummary();uiLog(t('словарь терминов: {n} названий',{n:TERMS.length}));
  }catch(e){toast(t('⚠ сервер не ответил: ')+e);}}
// ---- цензура (censor.py): стоп-слова и исключения правятся в ⚙ → «Слова» ----
// Списки в поставке ОБЩИЕ, правка ложится в свой файл (badwords.user.txt/okwords.user.txt).
// Поэтому у каждого списка есть «По умолчанию» — вернуть поставочный, а не очистить:
// пустой список это «не цензурить ничего», отдельный осмысленный выбор.
let CENSOR=null;
// Кнопка «По умолчанию» отбирает фокус у textarea, и её onchange успевает уйти на сервер
// перед сбросом. Держим запросы в очереди: иначе сброс мог прийти первым, а сохранение
// следом — и список «возвращался» к тому, что только что стёрли.
let CENSOR_Q=Promise.resolve();
function censorQ(fn){CENSOR_Q=CENSOR_Q.then(fn,fn);return CENSOR_Q;}
function censorFill(lists){if(!lists)return;CENSOR=lists;
  [['bad','bad_list','bad_res'],['ok','ok_list','ok_res']].forEach(([k,ta,res])=>{
    const el=$(ta),d=lists[k];if(!el||!d)return;
    if(document.activeElement!==el)el.value=d.text||'';
    const r=$(res);
    if(r)r.textContent=t('слов: {n} · {src}',{n:d.count||0,src:d.custom?t('свой список'):t('список из поставки')});});}
async function loadCensor(){if(!$('bad_list'))return;
  try{const d=await (await fetch('/api/censor_words')).json();
    if(!d.error)censorFill(d.lists);}catch(e){}}
function saveCensor(kind){const el=$(kind==='ok'?'ok_list':'bad_list');if(!el)return;
  const body={};body[kind]=el.value||'';
  return censorQ(async()=>{
    try{const d=await (await fetch('/api/censor_words',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify(body)})).json();
      if(d.error){toast('⚠ '+errText(d));return;}
      censorFill(d.lists);
      uiLog(t('цензура: ')+(kind==='ok'?t('исключений'):t('стоп-слов'))+' — '+((d.lists[kind]||{}).count||0));
    }catch(e){toast(t('⚠ сервер не ответил: ')+e);}});}
function resetCensor(kind){
  return censorQ(async()=>{
    try{const d=await (await fetch('/api/censor_words',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({reset:kind})})).json();
      if(d.error){toast('⚠ '+errText(d));return;}
      censorFill(d.lists);toast(t('Вернул список из поставки'));
    }catch(e){toast(t('⚠ сервер не ответил: ')+e);}});}
// Приписки к промпту: профиль спикера -> пусто (задание CS).
function imgPrompts(spkKey){
  const spk=(typeof SPEAKERS!=='undefined'&&spkKey)?SPEAKERS[spkKey]:null;
  const spkP=(spk&&spk.image_prompts)||{};
  return {
    a:(spkP.a&&spkP.a.extra)?spkP.a:{extra:'',pos:'suffix'},
    b:(spkP.b&&spkP.b.extra)?spkP.b:{extra:'',pos:'suffix'},
  };
}
// Приписки к видео независимы от image_prompts: одинаковые две кнопки на карточке
// не означают общий стиль, у моделей картинки и видео разные инструкции.
function videoPrompts(spkKey){
  const spk=(typeof SPEAKERS!=='undefined'&&spkKey)?SPEAKERS[spkKey]:null;
  const spkP=(spk&&spk.video_prompts)||{};
  return {
    a:(spkP.a&&spkP.a.extra)?spkP.a:{extra:'',pos:'suffix'},
    b:(spkP.b&&spkP.b.extra)?spkP.b:{extra:'',pos:'suffix'},
  };
}
function imgGenOn(){return !!(AICFG&&AICFG.active_image&&AICFG.active_image!=='__off__');}
async function setImageProfile(name){
  // Сервер недоступен — молча терять смену профиля картинок нельзя: тост и
  // возврат селекта к сохранённому состоянию (задание по UI-состояниям).
  let d;
  try{d=await (await fetch('/api/ai_config',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'set_active_image',name})})).json();}
  catch(e){toast(t('Сервер не ответил: ')+e);fillAIProfileSelects();return;}
  if(d.error)toast('⚠ '+errText(d));
  else{AICFG.active_image=d.active_image;AICFG.image_rembg=d.image_rembg;}
  fillAIProfileSelects();renderInsHost();}
async function setImageRembg(on){
  // Сервер недоступен — молча терять настройку нельзя: тост и возврат галки
  // к сохранённому состоянию (задание по UI-состояниям).
  let d;
  try{d=await (await fetch('/api/ai_config',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'set_image_rembg',value:!!on})})).json();}
  catch(e){toast(t('Сервер не ответил: ')+e);fillAIProfileSelects();return;}
  if(d.error)toast('⚠ '+errText(d));else AICFG.image_rembg=d.image_rembg;}
async function setGlitchGlow(v){
  let d;
  try{d=await (await fetch('/api/ai_config',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'set_glitch_glow',value:v})})).json();}
  catch(e){toast(t('Сервер не ответил: ')+e);fillAIProfileSelects();return;}
  if(d.error){toast('⚠ '+errText(d));fillAIProfileSelects();}
  else AICFG.glitch_glow=d.glitch_glow;}
// «Убрать фон» у уже выбранного файла (кнопка на карточке) — рядом ляжет <имя>-nobg.png
async function insRembg(i){if(curIns<0)return;const x=CLIPS[curIns].inserts[i];if(!x||!x.media)return;
  if(x.genBusy)return;
  x.genBusy=true;renderInsHost();
  const ac=new AbortController();
  const timer=setTimeout(()=>ac.abort(),GEN_FETCH_MS);
  try{const d=await (await fetch('/api/rembg',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({path:x.media,query:x.query||'',prompt:(x.prompt||''),dest:(val('illdest')||'').trim()}),signal:ac.signal})).json();
    if(d.error){toast('⚠ '+errText(d));uiLog(t('фон: ОШИБКА — ')+d.error);}
    else if(!d.changed){toast(t('У файла уже есть прозрачность'));}
    else{x.media=d.path;uiLog(t('фон убран: ')+d.path.replace(/^.*[\\\/]/,''));}}
  catch(e){toast(t('⚠ сервер не ответил: ')+e);}
  finally{clearTimeout(timer);}
  x.genBusy=false;renderInsHost();syncClipLists();saveState();}
// Время, за которое генерация картинки обязана уложиться. Серверный urlopen ждёт
// ответа 300с и ретраит один раз; зависший между шагами запрос (rembg, запись в
// базу) таймаута не имеет вообще. Без лимита кнопка «…» виснет навсегда: гвардия
// двойного клика (x.genBusy) снимается только по возврату fetch, и зависший сервер
// запирает карточку до перезагрузки страницы (поймано 2026-08-10).
const GEN_FETCH_MS=330000;   // 5.5 мин: одна попытка 300с + запас на rembg/сохранение
// ядро генерации — по вставке любого клипа (не только открытого), без UI-зависимостей.
// -> true если файл появился; ошибку кидаем наверх, чтобы пачка не молотила N раз
// подряд в один и тот же отвал (нет ключа / 402 / 429).
async function insGenCore(x,slot,speaker){
  const ac=new AbortController();
  const timer=setTimeout(()=>ac.abort(),GEN_FETCH_MS);
  let d;
  try{
    const body={query:x.query,prompt:(x.prompt||''),slot:(slot==='b'?'b':'a'),dest:(val('illdest')||'').trim()};
    if(speaker) body.speaker=speaker;
    d=await (await fetch('/api/ai_genimage',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body),signal:ac.signal})).json();
  }finally{clearTimeout(timer);}
  if(d.error)throw errText(d);
  x.media=d.path;x.genAuto=true;x.libAuto=false;x.libOpts=null;x.noAuto=false;
  uiLog(t('✨ сгенерено (промпт {p})',{p:(slot==='b'?'2':'1')})+': '+d.path.replace(/^.*[\\\/]/,'')
    +(d.nobg?t(' · фон убран'):'')+t(' (в базе)')+(d.warn?' ⚠ '+d.warn:''));
  return true;}
async function insGenOne(i,slot){if(curIns<0)return;const x=CLIPS[curIns].inserts[i];if(!x)return;
  if(!(x.query||'').trim()){toast(t('У вставки нет запроса — нечего генерить'));return;}
  if(x.genBusy)return;
  x.genBusy=true;renderInsHost();
  // Генерим ПОВЕРХ картинки, которую подставили автоматом (база/прошлая генерация) =
  // «эта не та». Бракуем её под этот запрос, иначе следующий автоподбор вернёт ровно её:
  // у сгенерённого desc == запрос, score ~1.0, и она перебьёт свежую.
  // Флаг genBusy ставим ДО await: без него второй клик проскакивал гвардию, пока шёл
  // reject, и стартовала вторая ПЛАТНАЯ генерация (аудит, гонка двойного клика).
  if(x.media&&(x.libAuto||x.genAuto))await insRejectMedia(x.media,x.query);
  const c=CLIPS[curIns];
  const spkKey=(c&&c.job&&c.job.speaker)||(val('speaker')||'').trim()||undefined;
  try{await insGenCore(x,slot,spkKey);}
  catch(e){toast('⚠ '+e);uiLog(t('✨ генерация: ОШИБКА — ')+e);}
  x.genBusy=false;renderInsHost();syncClipLists();saveState();}
// фото-вставки без файла (после автоподбора из базы) -> сгенерить. ask=false — молча,
// для авто-режима при разметке; при первой же ошибке останавливаемся.
async function insGenBatch(c,ask){
  // noAuto — картинку тут сняли руками. В АВТО-режиме при разметке (ask=false) молча
  // генерить новую за деньги нельзя — снял, значит не надо, вернуть юзер может сам (✨).
  // Явная кнопка с подтверждением цены (ask=true) — осознанный запрос: генерим и снятое.
  const need=(c.inserts||[]).filter(x=>x.type!=='video'&&!x.media&&(ask||!x.noAuto)&&(x.query||'').trim());
  if(!need.length){if(ask)toast(t('Все фото-вставки уже с файлами'));return 0;}
  if(ask&&!await askConfirm(t('Сгенерить {n} картинок (~{cost})?',{n:need.length,cost:'$'+(need.length*0.04).toFixed(2)})+'\n'
    +t('Каждая упадёт в базу вставок и переиспользуется в следующих роликах.')))return 0;
  uiLog(t('✨ генерация недостающих ({name}): {n} шт…',{name:c.name,n:need.length}));
  let n=0;const t0=performance.now();
  const spkKey=(c&&c.job&&c.job.speaker)||(val('speaker')||'').trim()||undefined;
  for(const x of need){                            // последовательно — не ловить 429
    if(typeof UICANCEL!=='undefined'&&UICANCEL)break;
    const ti=performance.now();
    try{await insGenCore(x,'a',spkKey);n++;}
    catch(e){toast(t('⚠ генерация: ')+e);uiLog(t('✨ генерация: ОШИБКА — ')+e+t(' — дальше не идём'));break;}
    uiLog('  ✨ ['+n+'/'+need.length+'] '+(performance.now()-ti).toFixed(0)+t(' мс'));}
  uiLog(t('✨ сгенерено картинок: {n} из {m}',{n:n,m:need.length})+t(' · всего ')+(performance.now()-t0).toFixed(0)+t(' мс'));
  return n;}
async function insGenMissing(){if(curIns<0)return;
  await insGenBatch(CLIPS[curIns],true);
  renderInsHost();syncClipLists();saveState();}
// после ИИ-разметки вставок: сперва база (бесплатно), потом — если юзер включил
// галку «и сразу генерить» — генерация остатка. -> строчка-хвост для статуса.
async function insAfterAI(c){
  let auto=0,gen=0;
  if(illCfg().auto!==false)auto=await insLibAuto(c);
  if(illCfg().gen&&imgGenOn())gen=await insGenBatch(c,false);
  return (auto?t(' · из базы: ')+auto:'')+(gen?t(' · сгенерено: ')+gen:'');}
async function setOmniProfile(name){
  const LOCALS=['__local__','__gigaam__'];
  if(!LOCALS.includes(name)&&AICFG.profiles[name]&&AICFG.profiles[name].provider==='anthropic'){
    toast(t('Claude API не принимает аудио — для Omni нужна аудио-модель (Gemini на OpenRouter) или локальная'));
    fillAIProfileSelects();return;}
  // Сервер недоступен — молча терять смену профиля слуха нельзя: тост и возврат
  // селекта к сохранённому состоянию (задание по UI-состояниям).
  let d;
  try{d=await (await fetch('/api/ai_config',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'set_active_omni',name})})).json();}
  catch(e){toast(t('Сервер не ответил: ')+e);fillAIProfileSelects();return;}
  if(d.error)toast('⚠ '+errText(d));else AICFG.active_omni=d.active_omni;
  fillAIProfileSelects();}
async function openAISettings(tab){
  if(!AICFG)await loadAIProfiles();
  if(!AICFG){toast(t('Сервер не ответил — /api/ai_config'));return;}
  loadCutStages();
  renderCutStagesUI();
  aiSetPick(AICFG.active in AICFG.profiles?AICFG.active:Object.keys(AICFG.profiles)[0]);
  aiSetTab(tab||'models');   // со страницы приходим сразу на её вкладку
  loadTerms();               // словарь и списки цензуры живут на сервере (файлами рядом
  loadCensor();              // с censor.py) — перечитываем на каждом заходе в настройки
  openModal('mbAISettings');
  setTimeout(()=>vidSyncCaps(),0);
  aiStatsLoad();             // сводка вызовов — всегда свежая при открытии ⚙
}
// Сводка ИИ-вызовов (/api/ai_stats): медианы токенов и времени по (модель, шаг, ум).
// Показывает, кто реально думает и во что это обходится — без неё догадка «кто
// сколько думает» была перевёрнутой (см. задание BW).
let AISTATS=[];
async function aiStatsLoad(){
  const el=$('aistats_host');if(!el)return;
  try{
    const d=await (await fetch('/api/ai_stats')).json();
    if(d.error){el.textContent='';return;}
    AISTATS=d.groups||[];
    const rows=AISTATS.map(g=>'<tr><td>'+esc(g.model)+'</td><td>'+esc(g.step)+'</td><td>'+esc(g.lvl)
      +'</td><td>'+g.n+'</td><td>'+g['in']+'</td><td>'+g.out+'</td><td>'+g.rt+'</td><td>'+g.rtpct
      +'%</td><td>'+g.sec.toFixed(1)+'</td><td>'+g.err+'</td></tr>').join('');
    el.innerHTML='<table><thead><tr><th>'+t('модель')+'</th><th>'+t('шаг')+'</th><th>'+t('ум')+'</th>'
      +'<th>n</th><th>in</th><th>out</th><th>rt</th><th>rt%</th><th>'+t('сек')+'</th><th>'+t('ошибки')+'</th></tr></thead>'
      +'<tbody>'+(rows||'<tr><td colspan="10" class="muted">'+t('пока нет вызовов')+'</td></tr>')+'</tbody></table>';
  }catch(e){el.textContent='';}}
function aiSetList(){AISNAMES=Object.keys(AICFG.profiles);
  $('aisList').innerHTML=AISNAMES.map((n,i)=>
      '<div class="aisItem'+(n===AIEDIT?' on':'')+'" tabindex="0" role="button" onclick="aiSetPick(AISNAMES['+i+'])" data-t="'+esc(n)+'">'
      +(n===AICFG.active?'<span class="dot">●</span>':'')+esc(n)+'</div>').join('')
    +'<div class="aisItem" style="color:var(--mut)" tabindex="0" role="button" onclick="aiSetNew()">'+esc(t('＋ Новый профиль'))+'</div>';}
function aiSyncKeyType(val){
  const el=$('ais_key');
  if(!el)return;
  el.type=(typeof val==='string'&&val.startsWith('env:'))?'text':'password';
}
function aiSetPick(name){AIEDIT=name;const p=AICFG.profiles[name]||{};
  $('ais_name').value=name||'';
  $('ais_provider').value=p.provider||'lmstudio';
  $('ais_url').value=p.base_url||'';
  $('ais_key').value=p.api_key||'';       // сервер отдаёт маску •••…; не тронешь — не изменится
  aiSyncKeyType(p.api_key);
  const hdrs=p.headers||{};
  $('ais_headers').value=(typeof hdrs==='object'&&!Array.isArray(hdrs))
    ?Object.entries(hdrs).map(([k,v])=>k+': '+v).join('\n')
    :'';
  $('ais_model').value=p.model||'';
  $('aisDel').style.display='';
  const cl=$('aisClone');if(cl)cl.style.display='';
  const ab=$('aisActive');if(ab)ab.style.display=(name&&AICFG&&name===AICFG.active)?'none':'';
  const kw=$('ais_key_warn');
  if(kw){
    if(p.key_env_ok===false){
      const envName=(p.api_key||'').replace(/^env:/,'').trim();
      kw.textContent=t('переменная окружения {n} не задана',{n:envName});
      kw.style.display='';
    }else{
      kw.textContent='';kw.style.display='none';
    }
  }
  aiSetHints();aiSetModelInput();aiSetStatus('');aiSetList();}
function aiSetNew(){AIEDIT=null;
  $('ais_name').value='';$('ais_provider').value='lmstudio';
  $('ais_url').value=((AICFG.presets||{}).lmstudio||{}).base_url||'';
  $('ais_key').value='';
  aiSyncKeyType('');
  $('ais_headers').value='';
  $('ais_model').value='';
  const kw=$('ais_key_warn');if(kw){kw.textContent='';kw.style.display='none';}
  $('aisDel').style.display='none';
  const cl=$('aisClone');if(cl)cl.style.display='none';
  const ab=$('aisActive');if(ab)ab.style.display='none';
  aiSetHints();aiSetModelInput();aiSetStatus('');aiSetList();}
function aiSetProv(){const pre=(AICFG.presets||{})[$('ais_provider').value]||{};
  $('ais_url').value=pre.base_url||'';    // автозаполнение по провайдеру, поле редактируемое
  $('ais_model').value='';aiSetHints();aiSetModelInput();}
function aiSetHints(models){const pre=(AICFG.presets||{})[$('ais_provider').value]||{};
  let ms=models||pre.models||[];
  // OpenRouter без подтянутого списка: сразу покажем известные reasoning-модели,
  // чтобы юзер видел, какие модели умеют extended thinking, ещё до «Обновить список».
  if(!ms.length && $('ais_provider').value==='openrouter' && AICFG.reasoning_examples){
    ms=AICFG.reasoning_examples.slice();
  }
  // Рядом с моделью — контекст, потолок вывода и цена (задание BY): каталог
  // models.dev знает их для каждой модели, показываем их, а не «reasoning».
  $('ais_models').innerHTML=ms.map(m=>{
    const c=MODEL_CAPS[(m||'').toLowerCase()]||{};
    let tail='';
    const ctx=fmtN(c.ctx_limit), out=fmtN(c.out_limit);
    const cost=(c.cost&&c.cost.input!=null)?(' · $'+c.cost.input+'/M'):'';
    if(ctx||out)tail=' · '+t('контекст')+' '+(ctx||'?')+(out?', '+t('вывод')+' '+out:'')+cost;
    return '<option value="'+esc(m)+'"'+(tail?' data-t="'+esc(m+tail)+'"':'')+'>';}).join('');
  // помечаем reasoning-модели прямо в подсказках дата-листа (точный список
  // провайдера ИЛИ фолбэк-подстроки из AICFG.reasoning_models)
  const dl=$('ais_models'); if(dl)for(const o of dl.options){
    const m=o.value;
    const c=MODEL_CAPS[m.toLowerCase()]||{};
    let tag=modelSupportsReasoning(m)?' · reasoning':'';
    if(c.default)tag+=' · '+t('думает по умолч.');
    o.textContent=m+tag+(o.dataset.t?o.dataset.t:'');}
}
function aiSetStatus(msg,cls){const el=$('aisStatus');el.className=cls||'muted';el.textContent=msg||'';}
function aiSetForm(){return {provider:$('ais_provider').value,base_url:val('ais_url').trim(),
  api_key:$('ais_key').value,headers_text:val('ais_headers'),model:val('ais_model').trim()};}
async function aiSetSave(){
  const name=val('ais_name').trim();if(!name){aiSetStatus(t('⚠ дай имя профилю'),'err');return;}
  const d=await (await fetch('/api/ai_config',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'save_profile',name,old_name:AIEDIT,profile:aiSetForm()})})).json();
  if(d.error){aiSetStatus('⚠ '+errText(d),'err');return;}
  AICFG.active=d.active;AICFG.profiles=d.profiles;AICFG.active_omni=d.active_omni;AICFG.active_image=d.active_image;AICFG.image_rembg=d.image_rembg;AICFG.glitch_glow=d.glitch_glow;
  fillAIProfileSelects();aiSetPick(name);aiSetStatus(t('сохранено'),'ok');}
async function aiSetMakeActive(){if(!AIEDIT)return;
  const d=await (await fetch('/api/ai_config',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'set_active',name:AIEDIT})})).json();
  if(d.error){aiSetStatus('⚠ '+errText(d),'err');return;}
  AICFG.active=d.active;AICFG.profiles=d.profiles;AICFG.active_omni=d.active_omni;AICFG.active_image=d.active_image;AICFG.image_rembg=d.image_rembg;AICFG.glitch_glow=d.glitch_glow;
  fillAIProfileSelects();aiSetPick(AIEDIT);aiSetStatus(t('активный профиль: {n}',{n:AIEDIT}),'ok');}
async function aiSetClone(){if(!AIEDIT)return;
  const baseName=AIEDIT;
  const existing=new Set(Object.keys(AICFG.profiles||{}));
  let newName=baseName+' '+t('копия');
  if(existing.has(newName)){
    let i=2;
    while(existing.has(baseName+' '+t('копия')+' '+i)){
      i++;
    }
    newName=baseName+' '+t('копия')+' '+i;
  }
  const d=await (await fetch('/api/ai_config',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'clone_profile',name:baseName,new_name:newName})})).json();
  if(d.error){aiSetStatus('⚠ '+errText(d),'err');return;}
  AICFG.active=d.active;AICFG.profiles=d.profiles;AICFG.active_omni=d.active_omni;AICFG.active_image=d.active_image;AICFG.image_rembg=d.image_rembg;AICFG.glitch_glow=d.glitch_glow;
  fillAIProfileSelects();aiSetPick(newName);aiSetStatus(t('профиль продублирован'),'ok');}
async function aiSetDelete(){if(!AIEDIT)return;
  if(!await askConfirm(t('Удалить профиль «{n}»?',{n:AIEDIT})))return;
  const d=await (await fetch('/api/ai_config',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'delete_profile',name:AIEDIT})})).json();
  if(d.error){aiSetStatus('⚠ '+errText(d),'err');return;}
  AICFG.active=d.active;AICFG.profiles=d.profiles;AICFG.active_omni=d.active_omni;AICFG.active_image=d.active_image;AICFG.image_rembg=d.image_rembg;AICFG.glitch_glow=d.glitch_glow;
  fillAIProfileSelects();aiSetPick(d.active);}
async function aiSetTest(){aiSetStatus(t('проверяю…'));
  try{const d=await (await fetch('/api/ai_test',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:AIEDIT,profile:aiSetForm()})})).json();
    if(d.error)aiSetStatus('✗ '+errText(d),'err');
    else aiSetStatus(t('работает · {ms} мс',{ms:d.ms}),'ok');}
  catch(e){aiSetStatus(t('✗ сервер не ответил: ')+e,'err');}}
async function aiSetModels(){aiSetStatus(t('запрашиваю список моделей…'));
  try{const d=await (await fetch('/api/ai_models',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:AIEDIT,profile:aiSetForm()})})).json();
    if(d.error){aiSetStatus('✗ '+errText(d),'err');return;}
    REASONING_IDS=new Set((d.reasoning_models||[]).map(x=>x.toLowerCase()));
    MODEL_EFFORTS=d.efforts||{};
    MODEL_DEFAULTS=d.default_enabled||{};
    MODEL_CAPS=Object.assign(MODEL_CAPS||{},d.caps||{});
    let urlNote='';
    const curUrl=val('ais_url').trim();
    if(d.base_url&&d.base_url!==curUrl){
      $('ais_url').value=d.base_url;
      urlNote=t(' · Base URL обновлён на {u}',{u:d.base_url});
    }
    aiSetHints(d.models);
    aiSetModelInput();   // пересчитать доступность reasoning под уже введённую модель
    fillStepReasoning(); // селекты «Ум» на страницах — под уровни выбранных моделей
    aiSetStatus(t('моделей: {n}',{n:d.models.length})+urlNote+(REASONING_IDS.size?t(' · reasoning: {n}',{n:REASONING_IDS.size}):'')
      +((d.image_models||[]).length?t(' · картинки: {n}',{n:d.image_models.length}):'')
      +t(' — открой подсказки в поле «Модель»'),'ok');}
  catch(e){aiSetStatus('✗ '+e,'err');}}

