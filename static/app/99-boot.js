// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Maxim Si
// сохранение состояния и запуск интерфейса
//
// Часть интерфейса Reelsi. Файлы static/app/ грузятся ПО ПОРЯДКУ ИМЁН обычными
// <script>-тегами (не модулями): один общий скоуп, как было в едином app.js.
// Порядок важен — объявления функций поднимаются в пределах своего файла.

// ================= persistence =================
const LSKEY='reelsi_state';
let LSWARNED=false;             // не спамить тостом: квота localStorage лечится только вручную
let SRVST_T=null,SRVST_LAST='';  // дебаунс серверного зеркала + последняя отправленная строка
const SRVST_DELAY=1200;   // задержка отправки; ОБЯЗАНА быть меньше периода flushSave
                          // (2500 мс), иначе каждый тик отодвигает таймер и зеркало
                          // не пишется никогда — поймано 2026-09-08
const SRVST_MAX_WAIT=20000;  // потолок ожидания: если с последней УСПЕШНОЙ отправки
                             // прошло столько, шлём немедленно
let SRVST_OK_T=Date.now(); // с нулём потолок ожидания срабатывает сразу и первые правки уходят в обход дебаунса пачкой; отсчёт должен начинаться с загрузки страницы
let SRVST_ERR=false;       // «об этой серии провалов уже сообщили» — не спамить uiLog
function stateObj(){return {
  base:val('base'),ai_outdir:val('ai_outdir'),aeoutdir:AEGLOBAL,aerender:AERENDER,aemusicdir:val('aemusicdir'),
  cams:nCams(),CAMDIRS,CAMFILES,CAMFROM,QUEUE,newonly:$('newonly').checked,
  dedupe:(CUT_STAGES&&CUT_STAGES.dedupe!==undefined)?!!CUT_STAGES.dedupe:false,
  cut_stages:CUT_STAGES,
  cut_thresholds:CUT_THRESHOLDS,
  // genBusy — мгновенный флаг «идёт генерация», в состояние НЕ пишется: иначе
  // flushSave успевает сохранить залипшую «…», и она переживает F5 навсегда —
  // гвардия двойного клика глотает нажатия, а снять некому (поймано 2026-08-10).
  CLIPS:CLIPS.map(c=>{const cc={...c};cc.inserts=(c.inserts||[]).map(x=>{const xx={...x};delete xx.genBusy;return xx;});return cc;}),
  curAE,styleSel:val('style'),CURSTYLE,STEP,subengine:val('subengine'),speaker:val('speaker'),
  VID:vidStateObj()};}
function saveState(){const s=JSON.stringify(stateObj());
  try{localStorage.setItem(LSKEY,s);}
  catch(e){if(!LSWARNED){LSWARNED=true;toast(t('⚠ localStorage переполнен — состояние хранится только на сервере (это ок)'));
    uiLog(t('⚠ saveState: ')+e);}}
  srvStateSave(s);}
function srvStateSave(s){ // зеркало на сервер: дебаунс SRVST_DELAY, не шлём неизменившееся
  if(s===SRVST_LAST)return;
  // потолок ожидания: если с последней УСПЕШНОЙ отправки прошло слишком много — сразу
  if(Date.now()-SRVST_OK_T>SRVST_MAX_WAIT){srvStatePost(s);return;}
  clearTimeout(SRVST_T);
  SRVST_T=setTimeout(()=>srvStatePost(s),SRVST_DELAY);}
function srvStatePost(s){ // собственно отправка зеркала; звать из srvStateSave и beforeunload
  fetch('/api/ui_state',{method:'POST',headers:{'Content-Type':'application/json'},
    body:'{"state":'+s+'}'}).then(r=>{if(!r.ok)throw new Error('HTTP '+r.status);
    return r.json().then(d=>{if(d.error)throw new Error(d.error);
      SRVST_LAST=s;SRVST_OK_T=Date.now();SRVST_ERR=false;});})
    .catch(e=>{if(!SRVST_ERR){SRVST_ERR=true;uiLog(t('⚠ ui_state: ')+e);}});}
function applyState(s){try{
  ['base','ai_outdir','aeoutdir','aemusicdir'].forEach(k=>{const el=$(k);if(el&&s[k]!=null)el.value=s[k];});
  // Глобальная папка .jsx (клипы без тега спикера) — из сохранённого состояния;
  // поле показывает её или папку тега открытого клипа (см. renderAeDirField).
  if(s.aeoutdir!=null)AEGLOBAL=s.aeoutdir;
  if(s.aerender!=null)AERENDER=s.aerender;
  // s.aimodel (старый ключ) больше не читаем — выбор модели переехал в серверный ai_config.json
  if(s.cams){const rb=document.querySelector('input[name=cams][value="'+s.cams+'"]');if(rb)rb.checked=true;}
  if(Array.isArray(s.CAMDIRS))CAMDIRS=s.CAMDIRS;if(Array.isArray(s.CAMFILES))CAMFILES=s.CAMFILES;
  if(Array.isArray(s.CAMFROM))CAMFROM=s.CAMFROM;
  // s.CAMCUSTOM — ключ состояния, сохранённого ДО 2026-08-20 (булев «выбрана руками»):
  // читаем его, чтобы выбранная папка камеры пережила обновление и её не затёр автоподбор.
  else if(Array.isArray(s.CAMCUSTOM))CAMFROM=s.CAMCUSTOM.map(x=>x?'user':'');
  if(Array.isArray(s.QUEUE))QUEUE=s.QUEUE;
  if(s.newonly!=null)$('newonly').checked=!!s.newonly;   // «только новые» — рабочая настройка, а не разовая галка
  if(s.cut_stages&&typeof s.cut_stages==='object')CUT_STAGES=Object.assign({},s.cut_stages);
  if(s.dedupe!=null&&(CUT_STAGES.dedupe===undefined||!s.cut_stages))CUT_STAGES.dedupe=!!s.dedupe;   // «чистка дублей» (задание CA) — переживает F5, как соседние галки
  if(s.cut_thresholds&&typeof s.cut_thresholds==='object')CUT_THRESHOLDS=Object.assign({},s.cut_thresholds);
  if(Array.isArray(s.CLIPS)){CLIPS=s.CLIPS;
    // Старое состояние могло сохранить залипшую «…» (genBusy в JSON, поймано
    // 2026-08-10) — после восстановления кнопки генерации обязаны быть живыми.
    (CLIPS||[]).forEach(c=>(c.inserts||[]).forEach(x=>{delete x.genBusy;}));}
  if(typeof s.curAE==='number')curAE=s.curAE;
  if(curAE>=CLIPS.length)curAE=-1;      // состояние могло сохраниться с индексом длиннее списка
  if(s.CURSTYLE)CURSTYLE=s.CURSTYLE;if(s.styleSel)STYLESAVED=s.styleSel;
  if(s.subengine!=null){SUBWANT=asrMigrate(s.subengine);const se=$('subengine');if(se&&se.options.length)se.value=SUBWANT;}
  // s.selfcheck_model и устаревшие ключи в старом состоянии просто игнорируем
  // Список спикеров грузится асинхронно — запоминаем выбор, ставит его loadSpeakers
  if(s.speaker!=null){SPKSAVED=s.speaker;const sp=$('speaker');if(sp&&sp.options.length>1)sp.value=s.speaker;spkEditUI();}
  // Вкладка «Видео»: запрос и референсы ставим сразу, остальное — когда fillVideoControls
  // наполнит списки под модель (они приходят с /api/ai_config, асинхронно)
  if(s.VID){VIDSAVED=s.VID;
    if(Array.isArray(s.VID.refs))VREFS=s.VID.refs;
    const tp=$('vid_prompt');if(tp&&s.VID.prompt!=null)tp.value=s.VID.prompt;}
  buildCamRows();renderQueue();renderAeDirField();renderCutStagesUI();cutSummary();
}catch(e){
  uiLog(t('⚠ состояние интерфейса не восстановлено: ')+e);
  console.error(e);
}}
function restoreState(){let s=null;
  try{s=JSON.parse(localStorage.getItem(LSKEY)||'null');}catch(e){}
  if(s){applyState(s);return;}
  // localStorage пуст (новый браузер / чистка / квота) — тянем серверную копию
  fetch('/api/ui_state').then(r=>r.json()).then(d=>{
    if(!d||!d.state)return;
    applyState(d.state);SRVST_LAST=JSON.stringify(d.state);
    goStep(CLIPS.length?(d.state.STEP||1):1);
    if(CLIPS.length)refreshStatuses();
    toast(t('Состояние восстановлено с сервера'));
  }).catch(()=>{});}

// ================= boot =================
document.addEventListener('DOMContentLoaded',()=>{setTimeout(edBind,300);});
window.addEventListener('resize',()=>{if($('mbPreview').classList.contains('on')){edResize();edDraw();}});
$('base').value=__BASE__;
document.querySelectorAll('[data-ic]').forEach(el=>{el.outerHTML=ico(el.dataset.ic,el.dataset.cls||'');});  // статичные иконки
// доступность: «!»-тултипы открываются с клавиатуры, кликабельные div-ы работают по Enter/Space
tipArm();
document.addEventListener('keydown',e=>{
  if((e.key==='Enter'||e.key===' ')&&e.target&&e.target.matches
     &&e.target.matches('.stepitem,.lnk,.aisItem,[role=button]')){e.preventDefault();e.target.click();}});
segUI();musicUI();syncVolUI();
restoreState();
LASTCAMS=nCams();      // база для отката радио, если юзер откажется чистить очередь
loadCutStages();       // загрузка ступеней нарезки с сервера (задание GG)
loadASREngines();      // после restoreState: он кладёт выбранный движок в ASRWANT
loadCams();
loadAIProfiles();
// спикеры — после стилей: выбор спикера подставляет стиль, а его надо знать
loadStyles().then(loadSpeakers);loadFonts();
edBind();
let bootStep=1,bootRaw='';try{bootRaw=localStorage.getItem('reelsi_step')||'';bootStep=parseInt(bootRaw)||1;}catch(e){}
if(bootRaw==='video')openVideo(); else goStep(CLIPS.length?bootStep:1);
// видео-джоб живёт своим потоком — подхватываем его отдельно от нарезки/сборки (F5 в процессе)
(async()=>{try{const d=await (await fetch('/api/video_status')).json();
  const ctx=videoContextForStatus(d);
  if(d.running){if(!ctx&&bootRaw!=='video')openVideo();
    logReset();progShow(t('Генерация видео'),t('возобновляю после перезагрузки…'));
    vidBusy(true);            // F5 во время генерации — «Остановить» должна вернуться вместе с прогрессом
    VIDCANCEL=false;VIDCTX=ctx;VIDPOLL=true;VIDRETRY=0;pollVideo();}
  else if(d.done){if(ctx){VIDCTX=null;vidBusy(false);videoFinish(d,ctx);}
    else{insVideoForgetPending();if(d.result&&bootRaw==='video')vidShowResult(d.result);}}
  else insVideoForgetPending();
}catch(e){}})();
if(CLIPS.length)refreshStatuses();   // подтянуть ncams/статусы для восстановленных клипов (влияет на кнопку раскладки камер шага 1)
illHdrPoll();                        // если описание базы уже идёт (запущено до F5) — показать прогресс в шапке
// если на сервере уже крутится задача (F5 посреди сборки/нарезки) — подхватываем её индикацию
(async()=>{try{const d=await (await fetch('/api/status')).json();
  queueRender(d);   // очередь этапов (задание FA): после F5 виден и итог уже закончившейся
  if(d.running){
    // kind/label приходят структурно из JOB (сниффинг лога — только фолбэк для старого сервера)
    const kind=d.kind||((d.log||[]).some(l=>fmtLog(l).indexOf('=== Сборка')===0)?'build':'cut');
    const label=d.label||(/Omni|27b/.test((d.log||[]).map(fmtLog).join('\n'))?t('ИИ-нарезка'):t('Нарезка'));
    progShow(label,t('возобновляю после перезагрузки…'));
    if(kind==='build')pollBuild();
    else if(kind==='draft')pollDraft();
    else{CUTLABEL=label;cutBusy(true);pollAI();}}}catch(e){}})();
// рендер живёт в своём RJOB — после F5 подхватываем его отдельно
(async()=>{try{const d=await (await fetch('/api/render_status')).json();
  queueRender(d);   // очередь этапов (задание FA): рендер — своя дверь, до проверки running
  // дефолт папки вывода рендера — один источник на сервере. Кладём в AERENDER,
  // а не только в поле: загрузка спикеров (applySpeakerDirs -> renderRenderDirField)
  // идёт позже и перезаписала бы значение, оставленное только в DOM.
  if(d.default_dir&&!AERENDER){AERENDER=d.default_dir;renderRenderDirField();}
  if(d.running){logReset();progShow(t('Рендер AE'),t('возобновляю после перезагрузки…'));uiBusySet(true);pollRender();}}catch(e){}})();
// bfcache возвращает страницу целиком — вместе с застрявшим в «…» genBusy старого
// сеанса: fetch, ушедший в зависший сервер, живёт ровно столько, сколько жила вкладка,
// а гвардия двойного клика глотает все нажатия. Ручная перезагрузка чистит сама,
// здесь подстраховываем переходы назад/вперёд — кнопки генерации не помнят чужой сеанс.
window.addEventListener('pageshow',e=>{if(!e.persisted)return;
  (CLIPS||[]).forEach(c=>(c.inserts||[]).forEach(x=>{x.genBusy=false;}));});
// сохранение состояния: захватываем DOM панели AE в джоб перед записью, чтобы правки не терялись
function flushSave(){if(STEP===3&&curAE>=0)captureAE();else saveState();}
setInterval(flushSave,2500);
window.addEventListener('beforeunload',()=>{flushSave();
  // sendBeacon гарантированно уходит при закрытии вкладки; обычный fetch не успевает
  const s=JSON.stringify(stateObj());if(s===SRVST_LAST)return;
  if(navigator.sendBeacon){navigator.sendBeacon('/api/ui_state',
    new Blob(['{"state":'+s+'}'],{type:'application/json'}));}
  else{fetch('/api/ui_state',{method:'POST',headers:{'Content-Type':'application/json'},
    body:'{"state":'+s+'}'}).catch(()=>{});}});
document.addEventListener('visibilitychange',()=>{if(document.hidden)flushSave();});
