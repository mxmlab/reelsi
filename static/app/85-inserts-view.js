// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Maxim Si
// предпросмотр вставок: виртуальный плеер и таймлайн
//
// Часть интерфейса Reelsi. Файлы static/app/ грузятся ПО ПОРЯДКУ ИМЁН обычными
// <script>-тегами (не модулями): один общий скоуп, как было в едином app.js.
// Порядок важен — объявления функций поднимаются в пределах своего файла.

// ---- предпросмотр вставок: виртуальный плеер в окне «Вставки» ----
// МУЛЬТИКАМ как в PV: звук всегда камера 1 (vids[0], ведущий плейхед), картинка — активный
// ракурс по EDL segs (opacity, не display — иначе застывший кадр). В нужный момент поверх
// кадра — вставка: файл (фото/видео) или заглушка с query. Два режима (IPVMODE):
//   'clips' — вставки шага 2 (CLIPS[curIns].inserts, поля start_sec/duration_sec);
//   'ae'    — AE-вставки шага 3 (INS, поля start_s+start_f/dur_s+dur_f) + оверлей ИНТРО.
let IPV={vids:[],bufs:[],scrubbing:false,scrubT:0,
  segs:[],audio:[],words:[],dur:0,fps:60,aidx:0,vidx:-1,primed:-1,curCi:-1,rollCi:-1,playing:false,raf:0,xml:'',cur:-1,intro:[],introCur:-1,plan:null,insShift:null,insVids:new Map(),dims:new Map()};
let IPVMODE='clips';
// нормализация пути вставки (Windows: слеши и регистр) — ОДИН источник для ensureJobs,
// applyInsMoved, сопоставления плана и драга в предпросмотре
function normInsPath(s){return (s||'').replace(/\//g,'\\').toLowerCase();}
// Ключ сопоставления вставки плана с карточкой списка. У вставки «на подложке» план несёт
// путь КЭША <стем>.<расш>.nobg.png (подмену делает план сцены), а карточка хранит
// исходник — сравниваем без суффикса кэша, иначе подсветка играющей карточки гасла бы.
// Файл для показа берётся из ПЛАНА как есть: второй копии правила «где лежит кэш» нет.
function insCardKey(x){
  const p=normInsPath(x&&x.media);
  return (x&&x.plate)?p.replace(/\.nobg\.png$/,''):p;}
// URL картинки-вставки для предпросмотра. У вставки с галкой «на подложке» просим тот же
// кэш <стем>.nobg.png, что уедет в сборку (core/insertlib.nobg_path) — снятие фона одно на
// превью и AE, второго расчёта в JS нет. Нужен он только там, где на руках
// ИСХОДНЫЙ путь (карточки шага 2 и показ без плана): в плане сцены media у такой вставки
// уже .nobg.png, и nobg=1 поверх кэша завёл бы второй файл .nobg.nobg.png.
// Подложке (её картинке) nobg НЕ просим никогда: у неё своя прозрачность, rembg её только испортит.
function insImgURL(p,onPlate){
  return '/api/media?path='+encodeURIComponent(p)+(onPlate?'&nobg=1':'');}
// перевод карточки шага 2 в контракт вставки сборки/плана (start_s, dur_s, scale, геометрия)
// ОДИН источник для ensureJobs (шаг 3) и ipvPlanBody (шаг 2)
function cardToIns(x,was){
  was=was||{};
  if(x.sin==null&&was.sin)x.sin=was.sin;
  // Сдвиг ВСЕЙ карточки на подложке (kx/ky) переносим как x/y: сначала берём
  // у карточки (её правит драг в предпросмотре), иначе из прошлой записи задания — иначе
  // пересборка списка шага 3 обнуляла бы сдвиг. Ноль проверяем на null: 0 — тоже значение.
  const kx=(x.kx!=null?x.kx:(was.kx!=null?was.kx:0));
  const ky=(x.ky!=null?x.ky:(was.ky!=null?was.ky:0));
  return {type:x.type==='video'?'video':'photo',style:was.style||'cam2',media:x.media,src2:true,
    start_s:Math.round((x.start_sec||0)*100)/100,start_f:0,
    dur_s:Math.round((x.duration_sec||2)*100)/100,dur_f:0,
    scale:(was.scale!=null?was.scale:44),mosaic:!!x.mosaic,plate:!!x.plate,
    x:x.x||0,y:x.y||0,kx:kx,ky:ky,sc:x.sc||100,mw:x.mw||100,mh:x.mh||100,sin:x.sin||0,
    ...(was.noexit?{noexit:was.noexit}:{})};}
// ---- план сцены: предпросмотр РИСУЕТ то, что прислал /api/scene ----
// Никаких вторых расчётов: окна групп интро, анимации вставок, зум, стопку субтитров и
// полосы рото берём из плана. Ошибка плана предпросмотр не ломает — без него играет как раньше.
function ipvPlanBody(){
  const c=(curAE>=0&&CLIPS[curAE])?CLIPS[curAE]:(curIns>=0&&CLIPS[curIns]?CLIPS[curIns]:null);
  const j=c&&c.job;const xml=IPV.xml;
  let ir;try{ir=introResolve();}catch(e){ir={lines:[],remove:[],splits:[]};}
  const insList=(IPVMODE==='ae')
    ?INS.filter(r=>(r.media||'').trim())
    :((c&&c.inserts)?c.inserts.filter(r=>(r.media||'').trim()).map(x=>cardToIns(x)):[]);
  return {xml:xml,music:j&&j.music_random?'':(j&&j.music||''),music_random:!!(j&&j.music_random),
    music_dir:val('aemusicdir').trim(),
    highlights:(HLXML===xml)?[...HL]:[],hl_breaks:(HLXML===xml)?[...BRK]:[],
    hl_count:(HLXML===xml)?[...CNT]:[],
    hl_joins:(HLXML===xml)?[...JNS]:[],
    inserts:insList,
    intro:ir.lines,intro_remove:ir.remove,intro_splits:ir.splits,
    intro_mode:val('intromode')||'word',censor:$('censor')?$('censor').checked:true,
    cams:clipNcams(c),exposure:parseFloat(val('aeexposure'))||0,
    roto: (CURSTYLE && CURSTYLE.roto != null) ? !!CURSTYLE.roto : (typeof STSCHEMA !== 'undefined' && STSCHEMA && STSCHEMA.base ? !!STSCHEMA.base.roto : false),
    roto_bottom: (CURSTYLE && CURSTYLE.roto_bottom != null) ? CURSTYLE.roto_bottom : (typeof STSCHEMA !== 'undefined' && STSCHEMA && STSCHEMA.base ? STSCHEMA.base.roto_bottom : 0),
    style:CURSTYLE};}
let IPVPLAN_T=0;
function ipvPlanSoon(){clearTimeout(IPVPLAN_T);IPVPLAN_T=setTimeout(ipvPlanFetch,300);}   // правки идут пачкой — рефетчим по затишью
async function ipvPlanFetch(){
  const xml=IPV.xml;if(!xml)return;
  let d;try{d=await (await fetch('/api/scene',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(ipvPlanBody())})).json();}
  catch(e){return;}                       // план не критичен: плеер играет как раньше
  if(IPV.xml!==xml)return;                // модалку успели переоткрыть на другом клипе
  if(d.ok&&d.plan){IPV.plan=d.plan;IPV.insShift=null;   // свежий план сам несёт сдвиги — временный сброс не нужен
    ipvSubsInvalidate();        // новый шрифт/положение — показать субтитры заново даже на паузе
    IPV.intro=ipvIntroGroups();
    sfxEnsure(d.plan);          // SFX: элементы под план
    IPV.cur=-2;IPV.introCur=-2;
    if(typeof renderSubRowsList==='function')renderSubRowsList();
    if(typeof aewUpdateCaptionUI==='function')aewUpdateCaptionUI();
    if(IPV.vids.length){itlDraw();ipvUI(ipvNow());}}
  else if(d.error){uiLog(t('план сцены: ')+(d.error||''));}}
// ---- SFX в предпросмотре: те же числа, что в AE ----
// План несёт audio.sfx с ГОТОВЫМ стартом каждого события (t = ev − at + in, файловые
// in/out) — JS ничего не пересчитывает, только ставит элемент на позицию. Каждый звук —
// свой <audio> (как музыка), громкость = dbToGain(база + db) × MEDIA_VOL (общий множитель
// прослушивания, в .jsx не уезжает). Видеопереход в превью не звучит: он и не рисуется.
let SFX_ELS={};
function sfxEnsure(plan){
  const sfx=(plan&&plan.audio&&plan.audio.sfx)||[];
  for(const k in SFX_ELS){if(!sfx.find(s=>s.kind===k)){SFX_ELS[k].el.remove();delete SFX_ELS[k];}}
  sfx.forEach(s=>{
    if(s.kind==='transition'||!s.media||!s.events||!s.events.length)return;
    let st=SFX_ELS[s.kind];
    if(!st){const el=document.createElement('audio');el.preload='auto';
      el.src='/api/media?path='+encodeURIComponent(s.media);document.body.appendChild(el);
      st=SFX_ELS[s.kind]={el,vol:1,path:s.media};}
    if(st&&st.path!==s.media){st.el.src='/api/media?path='+encodeURIComponent(s.media);st.path=s.media;}
    st.vol=dbToGain((s.base||0)+(s.db||0));});
  sfxSyncApply();}
function sfxSyncApply(){for(const k in SFX_ELS){try{SFX_ELS[k].el.volume=SFX_ELS[k].vol*MEDIA_VOL;}catch(e){}}}
function sfxPause(){for(const k in SFX_ELS){try{SFX_ELS[k].el.pause();}catch(e){}}}
function sfxSync(tm){
  if(!IPV.playing){sfxPause();return;}
  const sfx=((IPV.plan&&IPV.plan.audio)||{}).sfx||[];
  for(const s of sfx){
    const st=SFX_ELS[s.kind];if(!st)continue;
    const ev=s.events.find(e=>{
      const dur=(e.out!=null)?(e.out-e.in):(s.kind==='pop'?0.1:6);
      return tm>=e.t&&tm<e.t+Math.max(0.04,dur);
    });
    if(!ev){if(!st.el.paused)st.el.pause();continue;}
    const fpos=ev.in+(tm-ev.t);
    if(Math.abs(st.el.currentTime-fpos)>0.08)try{st.el.currentTime=fpos;}catch(e){}
    if(st.el.paused)st.el.play().catch(()=>{});}
  sfxSyncApply();}
// ---- один интерполятор ключей на весь предпросмотр ----
// AE-ease задаётся парой влияний (speed всегда 0), перевод в CSS-кривую точный:
// cubic-bezier(out/100, 0, 1-in/100, 1). Проверка: 35/90 -> (0.35, 0, 0.10, 1).
function aeEase(out,inp){return [out/100,0,1-inp/100,1];}
function bezierY(p1x,p2x,q){const u=1-q;return 3*u*q*q+q*q*q;}
// параметр кривой при заданном x (Ньютон); x(q)=3(1-q)^2 q p1x + 3(1-q) q^2 p2x + q^3
function bezierT(p1x,p2x,x){let q=x;
  for(let i=0;i<8;i++){const u=1-q;
    const xt=3*u*u*q*p1x+3*u*q*q*p2x+q*q*q;
    const dx=3*u*u*p1x+6*u*q*(p2x-p1x)+3*q*q*(1-p2x);
    if(Math.abs(xt-x)<1e-6||dx<1e-6)break;
    q-=(xt-x)/dx;if(q<0)q=0;else if(q>1)q=1;}
  return q;}
// значение ключей в момент tm (сек). keys=[[tm,v],...], ease=[[in,out],...] по ключу; между
// соседними ключами — кривая из out уходящего и in приходящего (нет ease — дефолт 35/90,
// тот же bez(), что шаблон вешает на ключи вставок). v может быть массивом (Position).
// hold может быть булевым (джамп-кат: значение держится) или массивом 0/1 по отрезкам.
function keysAt(keys,ease,tm,hold){
  if(!keys||!keys.length)return 0;
  if(hold===true){let v=keys[0][1];for(const k of keys){if(k[0]<=tm)v=k[1];}return v;}
  if(tm<=keys[0][0])return keys[0][1];
  const last=keys[keys.length-1];if(tm>=last[0])return last[1];
  const isArr=Array.isArray(hold);
  for(let i=0;i<keys.length-1;i++){const a=keys[i],b=keys[i+1];
    if(tm>=a[0]&&tm<b[0]){
      if(isArr&&hold[i])return a[1];
      const span=b[0]-a[0];if(span<=0)return b[1];
      const u=(tm-a[0])/span;
      const o=ease&&ease[i]?ease[i][1]:35;        // out уходящего ключа
      const inn=ease&&ease[i+1]?ease[i+1][0]:90;  // in приходящего
      const be=aeEase(o,inn);
      const p=bezierT(be[0],be[2],u);
      const q=bezierY(be[0],be[2],p);
      return Array.isArray(a[1])?a[1].map((cv,j)=>cv+(b[1][j]-cv)*q):a[1]+(b[1]-a[1])*q;}
  }
  return last[1];}
function ipvIns(){return IPVMODE==='ae'?INS:((CLIPS[curIns]&&CLIPS[curIns].inserts)||[]);}
function insSD(x){if(IPVMODE==='ae'){const f=IPV.fps||60;
    const d0=(+x.dur_s||0)+(+x.dur_f||0)/f;
    return {s:(+x.start_s||0)+(+x.start_f||0)/f,d:d0>0?d0:2};}
  return {s:+x.start_sec||0,d:+x.duration_sec||2};}
function insSetSD(x,s,d){if(IPVMODE==='ae'){x.start_s=s;x.start_f=0;x.dur_s=d;x.dur_f=0;}
  else{x.start_sec=s;x.duration_sec=d;}}
function insLbl(x){return IPVMODE==='ae'?(((x.media||'').replace(/^.*[\\\/]/,''))||t('файл не выбран')):(x.query||'');}
function ipvAfterEdit(){if(IPVMODE==='ae'){renderIns();captureAE();itlDraw();ipvRefresh();}
  else{saveState();renderInsHost();syncClipLists();}}   // renderInsHost сам дёргает itlDraw+ipvRefresh
async function ipvOpen(xml){
  ipvPause();insVidFreeAll();
  IPV={vids:[],bufs:[],scrubbing:false,scrubT:0,
    segs:[],audio:[],words:[],dur:0,fps:60,aidx:0,vidx:-1,primed:-1,curCi:-1,rollCi:-1,defAt:0,stats:{styk:0,swap:0,seek:0,cam:0,stale:0,back:0},playing:false,raf:0,xml:xml||'',cur:-1,intro:[],introCur:-1,plan:null,insShift:null};
  const stage=$('ipvstage');[...stage.querySelectorAll('video')].forEach(v=>v.remove());
  const oldCv=$('ipvcam');if(oldCv)oldCv.remove();   // старый canvas кадра мог остаться от прошлого клипа
  const oldSh=$('ipvshade');if(oldSh)oldSh.remove(); // затемнение под интро — тоже от прошлого клипа
  const ov=$('ipvins');ov.className='ipvins';ov.innerHTML='';
  const sb=$('ipvsub');sb.textContent='';sb.classList.remove('plan');
  sb.style.opacity='';sb.style.removeProperty('--subfs');sb.style.removeProperty('--subfc');sb.style.removeProperty('--subhl');sb.style.removeProperty('--subsh');
  const io=$('ipvintro');io.innerHTML='';io.style.display='none';
  $('ipvtime').textContent='0:00 / 0:00';$('ipvseek').value=0;ITL.pps=0;ipvMarks();
  if(!xml)return;
  let d;try{d=await (await fetch('/api/aicut_preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({xml})})).json();}
  catch(e){d={error:''+e};}
  if(IPV.xml!==xml)return;   // модалку успели переоткрыть на другом клипе
  if(d.error||!(d.cams||[]).length||!d.cams[0].path){uiLog(t('предпросмотр вставок: ')+(d.error||t('в XML не нашлись камеры')));return;}
  IPV.segs=d.segs||[];IPV.audio=(d.audio&&d.audio.length?d.audio:d.segs)||[];
  IPV.words=d.words||[];IPV.fps=d.fps||60;
  IPV.dur=d.dur||(IPV.audio.length?IPV.audio[IPV.audio.length-1].te:0);
  IPV.cams=d.cams;   // дублёру нужны пути камер, чтобы переезжать на прокси
  const px=await pvProxyLoad(xml,true);pvProxyMerge(px);   // прокси камер: без него 4:2:2 10 бит встаёт на каждом стыке
  if(IPV.xml!==xml)return;
  IPV.vids=d.cams.map((c,ix)=>{const v=document.createElement('video');
    v.src=pvSrc(c.path);v.preload='auto';v.muted=(ix!==0);v.playsInline=true;
    v.volume=MEDIA_VOL;v.style.zIndex=(ix===0)?'2':'1';   // ракурс переключается порядком слоёв, см. camVisual
    if(ix===0)v.onerror=()=>uiLog(t('предпросмотр: не открылся исходник камеры 1 — ')+c.path);
    stage.insertBefore(v,io);voiceWiring(v);return v;});
  // каждая камера — непрерывная дорожка со своим оффсетом и дублёром на склейки (camApply)
  IPV.bufs=[];bufMake(IPV,stage,io,0,0);
  IPV.delta=camDeltas(IPV);camBufs(IPV,stage,io);
  // кадр Камеры рисует canvas поверх видео (см. ipvCamPaint): CSS-зум видео дрожал, канвас нет
  const cv=document.createElement('canvas');cv.id='ipvcam';
  cv.style.position='absolute';cv.style.inset='0';cv.style.width='100%';cv.style.height='100%';
  cv.style.zIndex='3';cv.style.pointerEvents='none';stage.insertBefore(cv,io);   // над камерами (0-2), под рото (4) и субтитрами (5)
  // видео прячем видимостью, а не display:none: кадр для canvas должен продолжать декодироваться
  // показ кадра теперь на canvas: когда декодер отдал кадр (loadeddata/seeked) — перерисовываем сами
  IPV.vids.forEach(v=>{
    v.style.visibility='hidden';
    v.addEventListener('loadeddata',()=>ipvCamPaint(ipvZoomAt(ipvNow())));
    v.addEventListener('seeked',()=>ipvCamPaint(ipvZoomAt(ipvNow())));});
  IPV.bufs.forEach(b=>{
    b.el.style.visibility='hidden';
    b.el.addEventListener('loadeddata',()=>ipvCamPaint(ipvZoomAt(ipvNow())));
    b.el.addEventListener('seeked',()=>ipvCamPaint(ipvZoomAt(ipvNow())));});
  itlFit();ipvSeekTo(0);
  if(typeof zoomPickMark==='function')zoomPickMark();   // маркер точки наезда
  ipvPlanFetch();
  if(px&&px.building){PVPX.xml=xml;pvProxyWatch('ipvstage');}   // прокси готовятся — догнать их на переезде
}
// картинка активного ракурса — общая машина всех плееров (camApply в 60-preview.js):
// разбег входящей камеры перед стыком, показ по готовности, дрейф гасится скоростью.
function ipvApplyVisual(tm,play){camApply(IPV,tm,play);}
function ipvSeekTo(tm){if(!IPV.vids.length||!IPV.audio.length)return;tm=Math.max(0,Math.min(tm,IPV.dur));
  IPV.aidx=pvSegAt(IPV.audio,tm);IPV.vidx=-1;IPV.primed=-1;const a=IPV.audio[IPV.aidx];
  try{IPV.vids[0].currentTime=a.src+Math.max(0,tm-a.ts);}catch(e){}
  spareIdle(IPV);camIdle(IPV);ipvApplyVisual(tm,false);ipvUI(tm);itlEnsure(tm);musicSync();}
function ipvNow(){const a=IPV.audio[IPV.aidx];return (a&&IPV.vids.length)?a.ts+(IPV.vids[0].currentTime-a.src):0;}
function ipvStep(){if(!IPV.playing)return;
  const st=pvStep(IPV);if(!st)return;
  if(st.end){ipvPause();ipvSeekTo(0);return;}
  if(st.adv)IPV.stats.styk++;
  if(st.swap)IPV.stats.swap++;
  if(st.seeked)IPV.stats.seek++;
  ipvApplyVisual(st.tm,true);
  spareRollAt(IPV,st.tm);
  ipvUI(st.tm);}
function ipvTick(){if(!IPV.playing)return;ipvStep();IPV.raf=requestAnimationFrame(ipvTick);}
function ipvPlay(){if(!IPV.vids.length)return;IPV.playing=true;$('ipvplay').innerHTML=ico('pause');
  IPV.vids[0].muted=false;IPV.vids[0].play().catch(()=>{});sparePrime(IPV);ipvApplyVisual(ipvNow(),true);
  IPV.raf=requestAnimationFrame(ipvTick);
  clearInterval(IPV.itv);IPV.itv=setInterval(ipvStep,120);musicSync();}   // страховка: rAF молчит в фоновой вкладке
function ipvPause(){IPV.playing=false;const b=$('ipvplay');if(b)b.innerHTML=ico('play');
  cancelAnimationFrame(IPV.raf);clearInterval(IPV.itv);IPV.vids.forEach(v=>v.pause());spareStop(IPV);camIdle(IPV);
  const ov=$('ipvins');if(ov){const iv=ov.querySelector('video');if(iv)iv.pause();}musicSync();sfxPause();}
function ipvToggle(){IPV.playing?ipvPause():ipvPlay();}
// ---- музыка превью: как в рендере (уровень MUSIC_DB), синхронно с плеером ----
// Отдельный <audio> на весь предпросмотр: старт/пауза/перемотка по IPV, позиция = позиция
// монтажа (в AE музыка — слой под всем роликом, так и звучит). Источник — по режиму:
// file = путь из поля, random = любой трек из папки музыки (ТОТ ЖЕ выбор, что на сборке —
// /api/music_random поверх ytmusic.random_track; сборка выберет заново, уровень тот же),
// url = трек ещё не скачан, играть нечего — ползунок остаётся рабочим (тишина).
let MUSIC_EL=null,MUSIC_KEY='';
function musicKey(){const m=val('musicmode');
  const clipId=(IPV&&IPV.xml)?IPV.xml:(typeof curAE!=='undefined'?curAE:'');
  return m==='file'?('f:'+val('aemusic').trim()):(m==='random'?('r:'+val('aemusicdir').trim()+':'+clipId):'u');}
function musicEnsure(){audioGraph();if(!AUDIO||!MG)return false;
  if(MUSIC_EL)return true;
  MUSIC_EL=document.createElement('audio');MUSIC_EL.preload='auto';
  MUSIC_EL.volume=MEDIA_VOL;   // тот же множитель прослушивания, что у голоса (см. applyMediaVol)
  try{AUDIO.createMediaElementSource(MUSIC_EL).connect(MG);}catch(e){MUSIC_EL=null;return false;}
  document.body.appendChild(MUSIC_EL);return true;}
async function musicPick(){
  if(!musicEnsure())return;
  const m=val('musicmode');let p='';
  if(m==='file')p=val('aemusic').trim();
  else if(m==='random'){try{
    const clipId=(IPV&&IPV.xml)?IPV.xml:(typeof curAE!=='undefined'?curAE:'');
    const d=await (await fetch('/api/music_random',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({dir:val('aemusicdir').trim(),seed:clipId})})).json();
    p=(d&&d.path)||'';}catch(e){}}
  if(p)MUSIC_EL.src='/api/media?path='+encodeURIComponent(p);
  else MUSIC_EL.removeAttribute('src');
  if(IPV.playing)musicSync();}   // трек сменился во время игры — подхватить позицию и играть
function musicSync(){if(!musicEnsure())return;
  if(AUDIO&&AUDIO.state==='suspended')AUDIO.resume().catch(()=>{});
  const k=musicKey();
  if(k!==MUSIC_KEY){MUSIC_KEY=k;MUSIC_EL.removeAttribute('src');musicPick();return;}   // режим/трек сменились — пусть подберётся
  if(!MUSIC_EL.src)return;                        // url-режим или трек ещё подбирается
  try{MUSIC_EL.currentTime=ipvNow();}catch(e){}
  if(IPV.playing)MUSIC_EL.play().catch(()=>{});else MUSIC_EL.pause();}
// разбег готовим один раз, когда протяжка улеглась (см. pvScrub — та же причина)
function ipvScrub(x){if(!IPV.vids.length)return;const tm=x/1000*IPV.dur;const was=IPV.playing;
  IPV.scrubbing=true;clearTimeout(IPV.scrubT);ipvPause();ipvSeekTo(tm);if(was)ipvPlay();
  IPV.scrubT=setTimeout(()=>{IPV.scrubbing=false;if(IPV.playing)sparePrime(IPV);},150);}
function ipvJump(i){const x=ipvIns()[i];if(!x||!IPV.vids.length)return;
  ipvSeekTo(Math.max(0,insSD(x).s-1));ipvPlay();}
// ---- наезд и дрейф Камеры 1: ключи и кривые из плана (zoom.keys/ease/hold/fit) ----
// Масштаб кадра от центра композиции; hold — джамп-кат без интерполяции. Преломление
// кадра в кадр и дублёру: на стыке подмена меняет элементы местами, и вышедший в эфир
// дублёр должен нести тот же зум, иначе кадр «прыгает» в масштабе.
// Масштаб в момент tm — ОДИН на всех (кадр и вставки кам1): второй интерполятор
// разъехался бы с первым, и превью снова разошлось бы с AE.
function ipvZoomAt(tm){const z=IPV.plan&&IPV.plan.zoom;if(!z||!z.keys||!z.keys.length)return 1;
  const fps=IPV.fps||60;
  // квантование времени к кадру композиции убирает дрожание рендера между кадрами
  const tq=Math.round(tm*fps)/fps;
  const pct=keysAt((z.keys||[]).map(k=>[k[0]/fps,k[1]]),z.ease,tq,z.holds!=null?z.holds:z.hold);
  return ((z.fit==null?100:z.fit)/100)*(pct/100);}
function ipvZoom(tm){const s=ipvZoomAt(tm);
  const pl=IPV.plan;
  // точка наезда камеры: масштабируем кадр от неё, а не от центра — в AE
  // нул Камеры 1 имеет anchor/position от точки наезда, и неподвижна именно она
  const cx=((pl&&pl.zoom&&pl.zoom.cx)!=null)?pl.zoom.cx:0.5;
  const cy=((pl&&pl.zoom&&pl.zoom.cy)!=null)?pl.zoom.cy:0.5;
  ipvRotoMaskZoom(s,cx,cy);      // подсказка «низ маски рото» едет вместе с кадром
  ipvCamPaint(s);}               // кадр рисует canvas: CSS-масштаб видео дрожал, вырезка — нет
function ipvCamShift(tm){
  const pl=IPV.plan;
  const pan=(pl&&pl.zoom&&pl.zoom.pan)||[0,0];
  let off=0;
  const fol=pl&&pl.zoom&&pl.zoom.follow;
  if(fol&&fol.keys&&fol.keys.length){
    const fps=IPV.fps||60;
    const tt=(tm!=null)?tm:((typeof ipvNow==='function')?ipvNow():0);
    const tq=Math.round(tt*fps)/fps;
    const keys=fol.keys.map(k=>[k[0]/fps,k[1]]);
    off=keysAt(keys,fol.ease,tq,false)||0;
  }
  return [(pan[0]||0)+off, pan[1]||0];
}
// ---- одна матрица кадра Камеры 1 ----
// Точка ИСХОДНИКА (px композиции от его центра при заполнении кадра) -> экран (px композиции
// от левого верхнего угла), матрица 2D (a,b,c,d,e,f). Модель как в AE после ZE:
//   экран = C + S·(R·p − C_c) + T,
// где C — точка наезда (cx*W, cy*H от левого верхнего угла), C_c — она же от центра кадра,
// S = ipvZoomAt(tm) (заполнение уже внутри ключей), R — горизонт `rot` вокруг центра
// ИСХОДНИКА, T = ipvCamShift(tm) = pan + слежение.
// Проверка модели: при S=1, rot=0, T=0 центр исходника встаёт в центр кадра, а точка наезда
// неподвижна при любом S. −C_c стоит ПОСЛЕ R, поэтому поворот центра исходника не двигает
// (в AE поворачивается слой камеры вокруг своего якоря, а не нул). Второй копии правила не
// заводить: кадр и подсказка рото обязаны считать одно и то же.
function ipvCamMatrix(tm){
  const pl=IPV.plan,W=pl?pl.w:1080,H=pl?pl.h:1920;
  const s=ipvZoomAt(tm);
  const cx=((pl&&pl.zoom&&pl.zoom.cx)!=null)?pl.zoom.cx:0.5;
  const cy=((pl&&pl.zoom&&pl.zoom.cy)!=null)?pl.zoom.cy:0.5;
  const rad=((pl&&pl.zoom&&pl.zoom.rot)||0)*Math.PI/180;
  const co=Math.cos(rad),si=Math.sin(rad);
  const shift=(typeof ipvCamShift==='function')?ipvCamShift(tm):((pl&&pl.zoom&&pl.zoom.pan)||[0,0]);
  const dx=(cx-0.5)*W,dy=(cy-0.5)*H;                          // точка наезда от центра кадра (C_c)
  return [s*co, s*si, -s*si, s*co,
          cx*W+(shift[0]||0)-s*dx,
          cy*H+(shift[1]||0)-s*dy];}
// ---- Lumetri в превью ----
// ПРИБЛИЖЕНИЕ: настоящие формулы Lumetri закрыты (плагин), здесь те же шаги в том же
// порядке, что в панели AE: экспозиция -> контраст -> тона -> баланс -> насыщенность.
// Все константы приближения собраны ЗДЕСЬ, чтобы правка была в одном месте.
const IPV_LM_HL=0.25, IPV_LM_SH=0.25, IPV_LM_WH=0.15, IPV_LM_BL=0.15;  // вес тонов
const IPV_LM_BAL=0.2;              // баланс: ±20% каналу при ±100 (температура/оттенок)
const IPV_LM_N=64;                 // точек в таблице кривой (feComponentTransfer)
function ipvLmSmooth(a,b,x){       // smoothstep: 0 ниже a, 1 выше b, между — плавно
  if(b<=a)return x>=b?1:0;
  const u=Math.max(0,Math.min(1,(x-a)/(b-a)));
  return u*u*(3-2*u);}
// Кривая тона: яркость x (0…1) -> яркость y (0…1); значения — из plan.lumetri.
function ipvLumetriTone(x,lm){
  const l=lm||{};
  const ex=+l.exposure||0,ct=+l.contrast||0,hl=+l.highlights||0,
        sh=+l.shadows||0,wh=+l.whites||0,bl=+l.blacks||0;
  let y=x*Math.pow(2,ex);                                   // экспозиция
  y=0.5+(y-0.5)*(1+ct/100);                                 // контраст вокруг середины
  y+=IPV_LM_HL*(hl/100)*ipvLmSmooth(0.5,1,y);               // светлые
  y+=IPV_LM_SH*(sh/100)*(1-ipvLmSmooth(0,0.5,y));           // тени
  y+=IPV_LM_WH*(wh/100)*ipvLmSmooth(0.75,1,y);              // белые
  y+=IPV_LM_BL*(bl/100)*(1-ipvLmSmooth(0,0.25,y));          // тёмные
  return Math.max(0,Math.min(1,y));}
// Таблица IPV_LM_N точек — одна кривая на все три канала (feFuncR/G/B).
function ipvLumetriTable(lm){
  const out=[];
  for(let i=0;i<IPV_LM_N;i++)out.push(ipvLumetriTone(i/(IPV_LM_N-1),lm).toFixed(5));
  return out.join(' ');}
// Фильтр превью: скрытый <svg> с фильтром id="ipvLumetri". Пересобирается ТОЛЬКО при
// смене plan.lumetri (подпись): иначе строка таблицы собиралась бы на каждом кадре.
let IPV_LM_SIG=null;
function ipvLumetriFilter(){
  const lm=(typeof IPV!=='undefined'&&IPV.plan)?IPV.plan.lumetri:null;
  if(!lm){IPV_LM_SIG=null;return 'none';}          // галка стиля снята — цвет как был
  const sig=JSON.stringify(lm);
  if(sig===IPV_LM_SIG&&$('ipvLumetriSvg'))return 'url(#ipvLumetri)';
  let svg=$('ipvLumetriSvg');
  if(!svg){
    const host=$('ipvstage')||document.body;
    svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    svg.setAttribute('id','ipvLumetriSvg');svg.setAttribute('aria-hidden','true');
    // именно нулевой размер, а не display:none: у скрытого фильтра браузер не считает
    // результат, и canvas нарисовал бы кадр без цвета
    svg.style.position='absolute';svg.style.width='0';svg.style.height='0';
    svg.style.overflow='hidden';svg.style.pointerEvents='none';
    host.appendChild(svg);}
  const temp=+lm.temp||0,tint=+lm.tint||0,sat=(lm.sat==null?100:+lm.sat);
  const kr=1+IPV_LM_BAL*temp/100, kb=1-IPV_LM_BAL*temp/100, kg=1-IPV_LM_BAL*tint/100;
  // color-interpolation-filters=sRGB: иначе браузер считает кривую в linearRGB и
  // приближение уезжает от картинки AE
  svg.innerHTML='<filter id="ipvLumetri" x="0" y="0" width="100%" height="100%" '
    +'color-interpolation-filters="sRGB">'
    +'<feComponentTransfer>'
    +'<feFuncR type="table" tableValues="'+ipvLumetriTable(lm)+'"/>'
    +'<feFuncG type="table" tableValues="'+ipvLumetriTable(lm)+'"/>'
    +'<feFuncB type="table" tableValues="'+ipvLumetriTable(lm)+'"/>'
    +'</feComponentTransfer>'
    +'<feColorMatrix type="matrix" values="'
    +[kr,0,0,0,0, 0,kg,0,0,0, 0,0,kb,0,0, 0,0,0,1,0].join(' ')+'"/>'
    +'<feColorMatrix type="saturate" values="'+(sat/100)+'"/>'
    +'</filter>';
  IPV_LM_SIG=sig;
  return 'url(#ipvLumetri)';}
// Кадр камеры рисуется НА canvas (выбор стенда): субпиксельные координаты стабильны, а
// CSS-трансформ на <video> дрожал по горизонтали при зуме. Видео спрятаны видимостью
// (см. ipvOpen), поэтому canvas рисуется КАЖДЫЙ кадр — и при s==1 тоже.
function ipvCamPaint(s){const cv=$('ipvcam');if(!cv)return;   // canvas нет — рисовать нечем
  const ci=(IPV.curCi>=0)?IPV.curCi:0;                        // активная камера (не выбрана — камера 0)
  const v=IPV.vids[ci];
  if(!v||v.readyState<2||!v.videoWidth){                      // кадр ещё не декодирован — чистим холст
    const c0=cv._c||(cv.getContext&&cv.getContext('2d'));
    if(c0){c0.setTransform(1,0,0,1,0,0);c0.clearRect(0,0,cv.width,cv.height);}return;}
  const st=$('ipvstage');if(!st)return;
  const r=st.getBoundingClientRect();
  const W=Math.round(r.width),H=Math.round(r.height);         // CSS-размер сцены
  if(W<1||H<1)return;
  const dpr=window.devicePixelRatio||1;
  const pw=Math.round(W*dpr),ph=Math.round(H*dpr);
  let c=cv._c;                                                // контекст и размеры кэшируем на canvas
  if(!c||cv.width!==pw||cv.height!==ph){
    cv.width=pw;cv.height=ph;cv.style.width=W+'px';cv.style.height=H+'px';
    c=cv.getContext('2d');
    c.imageSmoothingEnabled=true;c.imageSmoothingQuality='high';
    cv._c=c;}
  // Чистим ПРИ ЕДИНИЧНОЙ матрице: в контексте осталась матрица прошлого кадра, и clearRect
  // в ней вычистил бы только угол — оттуда мазня по краям кадра.
  c.setTransform(1,0,0,1,0,0);c.clearRect(0,0,cv.width,cv.height);
  const vw=v.videoWidth,vh=v.videoHeight;
  if(ci!==0){                                                 // перебивка: как было — вырезка без зума, сдвига и поворота
    s=1;                                                      // зум — только Камера 1 (как был на видео)
    const pl=IPV.plan;
    const cx=((pl&&pl.zoom&&pl.zoom.cx)!=null)?pl.zoom.cx:0.5;  // точка наезда из плана
    const cy=((pl&&pl.zoom&&pl.zoom.cy)!=null)?pl.zoom.cy:0.5;
    const f=Math.max(W/vw,H/vh)*s;                            // заполнение кадра
    const sw=W/f,sh=H/f;
    // Lumetri из стиля висит на ВСЕХ клипах камер (в AE — на клипах и их рото-копиях),
    // поэтому фильтр превью надевается и на перебивку
    c.filter=ipvLumetriFilter();
    c.drawImage(v,cx*(vw-sw),cy*(vh-sh),sw,sh,0,0,W,H);       // вырезка от точки наезда, без округления
    c.filter='none';                                          // холст один на кадр — состояние не копим
    return;}
  // Камера 1: рисуем ВЕСЬ исходник через матрицу. Раньше из видео вырезался ровно кадр
  // экрана и сдвигался уже вырезанным куском — по краям открывались полосы и мазня,
  // которых в AE нет: там двигается весь исходник. Матрица живёт в px
  // КОМПОЗИЦИИ, поэтому и заполнение f считается по кадру композиции, а не по стойке:
  // k переводит px композиции в px сцены (в перебивке вырезка рисуется прямо в px сцены).
  const pl=IPV.plan;
  const Wc=(pl&&pl.w)||1080, Hc=(pl&&pl.h)||1920;
  const k=W/Wc;                                               // px сцены на px композиции
  const f=Math.max(Wc/vw,Hc/vh);                              // заполнение кадра в px композиции
  const m=ipvCamMatrix((typeof ipvNow==='function')?ipvNow():0);
  c.setTransform(dpr*k*m[0],dpr*k*m[1],dpr*k*m[2],dpr*k*m[3],dpr*k*m[4],dpr*k*m[5]);
  c.filter=ipvLumetriFilter();                                // цвет камер из плана
  c.drawImage(v,0,0,vw,vh,-vw*f/2,-vh*f/2,vw*f,vh*f);
  c.filter='none';}                                           // состояние холста не копим
// Полоса «Низ маски %» (подсказка ротоскопа, rotoMask в 95-styles.js) обязана ехать
// ВМЕСТЕ с кадром: RVM заливает низ ИСХОДНИКА камеры, в AE рото-слой висит на нуле
// Камеры 1 и едет с её наездом, а подсказка была прибита к низу стойки — при зуме 160%
// красным закрашивалось не то место, и «низ маски» правился вслепую (жалоба 2026-08-14).
// Тот же transform, что у видео: рамка во весь кадр, полоса внутри неё.
// s/cx/cy приходят из ipvZoom — второго интерполятора зума заводить нельзя.
function ipvRotoMaskZoom(s,cx,cy){const st=$('ipvstage');const m=st&&st.querySelector('.rotomask');
  if(!m)return;
  const pl=IPV.plan;
  if(s==null)s=IPV.vids.length?ipvZoomAt(ipvNow()):1;      // зовут из панели стиля на паузе
  if(cx==null)cx=((pl&&pl.zoom&&pl.zoom.cx)!=null)?pl.zoom.cx:0.5;
  if(cy==null)cy=((pl&&pl.zoom&&pl.zoom.cy)!=null)?pl.zoom.cy:0.5;
  // композитный слой подсказки ротоскопа без перерастрирования
  if(!m._zmInit){m.style.willChange='transform';m.style.backfaceVisibility='hidden';m._zmInit=true;}
  const rot=(pl&&pl.zoom&&pl.zoom.rot)||0;
  const r=st.getBoundingClientRect();
  const W=r.width||(pl&&pl.w)||1080;
  const k=W/((pl&&pl.w)||1080);
  const shift=(typeof ipvCamShift==='function')?ipvCamShift():((pl&&pl.zoom&&pl.zoom.pan)||[0,0]);
  const panX=shift[0]*k, panY=shift[1]*k;
  if(!rot&&!panX&&!panY){
    m.style.transform='scale3d('+s+','+s+',1)';
    m.style.transformOrigin=(cx*100)+'% '+(cy*100)+'%';
  }else{
    const cc=ipvCamChild(0,0,s);
    const origX=W/2+cc[0]*k;
    const H=r.height||(pl&&pl.h)||1920;
    const origY=H/2+cc[1]*k;
    const px=origX-cx*W, py=origY-cy*H;
    m.style.transformOrigin=(cx*100)+'% '+(cy*100)+'%';
    m.style.transform='translate('+px+'px,'+py+'px) rotate('+rot+'deg) translate('+(-px)+'px,'+(-py)+'px) translate('+panX+'px,'+panY+'px) scale3d('+s+','+s+',1)';
  }}
// Экран = C + s*(p − C): положение ребёнка нула Камеры 1 (вставки кам1, интро)
// при зуме s. C — точка наезда, p — положение без зума; всё от ЦЕНТРА кадра в px композиции.
// ЕДИНСТВЕННАЯ функция на это правило — зовётся из вставок кам1 и из интро, второй копии нет.
function ipvCamChild(px, py, s, tm){
  const pl=IPV.plan,W=pl?pl.w:1080,H=pl?pl.h:1920;
  const cx=((pl&&pl.zoom&&pl.zoom.cx)!=null)?pl.zoom.cx:0.5;
  const cy=((pl&&pl.zoom&&pl.zoom.cy)!=null)?pl.zoom.cy:0.5;
  const shift=(typeof ipvCamShift==='function')?ipvCamShift(tm):((pl&&pl.zoom&&pl.zoom.pan)||[0,0]);
  const dx=(cx-0.5)*W, dy=(cy-0.5)*H;   // точка наезда от центра кадра
  return [s*px+(1-s)*dx+shift[0], s*py+(1-s)*dy+shift[1]];}
// Размытие на старте: в AE — Adjustment Layer с Gaussian Blur start_blur->0
// за start_blur_dur от начала ролика. Здесь тот же фильтр CSS-ом на кадре: linear интерполяция
// от start_blur до 0 (в AE ключи линейные, ease не ставится). Выключено — план несёт ноль.
// Задание DQ: clip-path режет результат фильтра по рамке кадра, иначе размытый край лезет на модалку.
function ipvStartBlur(tm){const pl=IPV.plan,b=pl&&pl.start_blur||0,d=pl&&pl.start_blur_dur||0.52;
  const st=$('ipvstage');if(!st)return;
  if(!b||tm>=d||tm<0){
    if(st.style.filter)st.style.filter='';
    if(st.style.clipPath)st.style.clipPath='';
    return;
  }
  const v=b*(1-tm/d);
  st.style.filter='blur('+v.toFixed(1)+'px)';
  st.style.clipPath='inset(0 round var(--r))';}
// ---- верхняя строка-прогресс по плану ----
function ipvTopLine(tm){
  const el=$('ipvtopline');if(!el)return;
  const pl=IPV.plan;const tl=pl&&pl.top_line;
  if(!tl){el.style.display='none';return;}
  const w=pl.w||1080;
  const dur=tl.dur||pl.dur||1;
  const progFr=Math.max(0,Math.min(1,tm/dur));
  const progW=tl.th + (tl.w - tl.th) * progFr;
  const cFrom=rgb2hex(tl.from||[0.984,1,0.541]);
  const cTo=rgb2hex(tl.to||[1,0.698,0.988]);
  const cTrack=rgb2hex(tl.track_fill||[1,1,1]);
  const trOp=((tl.track_op!=null?tl.track_op:16)/100);
  const rCqw=(tl.th/2/w*100).toFixed(3)+'cqw';
  const fullWCqw=(tl.w/w*100).toFixed(3)+'cqw';
  const hCqw=(tl.th/w*100).toFixed(3)+'cqw';
  const topCqw=(tl.y/w*100).toFixed(3)+'cqw';

  el.style.display='';
  el.style.width=fullWCqw;
  el.style.height=hCqw;
  el.style.top=topCqw;
  el.style.borderRadius=rCqw;

  const track=el.querySelector('.pvtl_track');
  if(track){
    track.style.background=cTrack;
    track.style.opacity=trOp;
    track.style.borderRadius=rCqw;
  }
  const prog=el.querySelector('.pvtl_prog');
  if(prog){
    prog.style.width=(progW/w*100).toFixed(3)+'cqw';
    prog.style.borderRadius=rCqw;
    prog.style.background='linear-gradient(to right, '+cFrom+', '+cTo+')';
    prog.style.backgroundSize=fullWCqw+' 100%';
    prog.style.backgroundRepeat='no-repeat';
  }
}
// ---- подпись о ролике по плану (обновлено DL) ----
function ipvCaption(tm){
  const el=$('ipvcaption');if(!el)return;
  const pl=IPV.plan;const cap=pl&&pl.caption;
  if(!cap||!cap.text){el.style.display='none';return;}
  const w=pl.w||1080;
  const fontPs=cap.font||'SFPro-Bold';
  const fv=ipvFontFor(fontPs);
  const size=cap.size!=null?cap.size:26;   // кегль экранный: масштаб слоя убран ()
  const kx=cap.kx!=null?cap.kx:1.718;
  const ky=cap.ky!=null?cap.ky:2.484;
  // В AE плашка меряется от sourceRectAtTime текста: высота рамки = 1.4125 кегля, а её
  // центр — на 0.146 кегля ВЫШЕ базовой линии (снято из adcut.aep: при кегле 48 рамка
  // идёт -40.91…+26.9 от базовой). Тех же пропорций держимся здесь, иначе превью и
  // .jsx разъезжаются. Саму базовую линию ставим замером (см. ниже), а не формулой:
  // у браузера свои метрики шрифта и полулидинг, и подпись уезжала по вертикали.
  const RECT_H=1.4125, RECT_MID=0.146;
  const boxH=size*RECT_H;

  const inner=el.querySelector('.pvcap_inner');
  const txtEl=el.querySelector('.pvcap_text');
  if(!inner||!txtEl)return;

  el.style.display='';
  el.style.left=(cap.x/w*100).toFixed(3)+'cqw';   // точка отсчёта; текст центрируется по ней ниже
  el.style.top=((cap.y-size*0.852)/w*100).toFixed(3)+'cqw';   // грубо, дальше доводим замером
  el.style.fontSize=(size/w*100).toFixed(3)+'cqw';
  el.style.color=rgb2hex(cap.fill||[1,1,1]);
  if(fv){
    el.style.fontFamily="'"+fv.family+"'";
    if(fv.var)el.style.fontVariationSettings=Object.entries(fv.var).map(([a,v])=>'"'+a+'" '+v).join(',');
    else el.style.fontVariationSettings='';
  }else{
    el.style.fontFamily='';
    el.style.fontVariationSettings='';
  }
  txtEl.textContent=cap.text;

  const stage=el.parentElement;
  const stageW=(stage&&stage.clientWidth)||0;
  const k=stageW>0?(w/stageW):0;
  let textW=0, baseOff=0;
  if(k){
    const er=el.getBoundingClientRect();
    const rng=document.createRange();rng.selectNodeContents(txtEl);
    textW=rng.getBoundingClientRect().width*k;
    // опора базовой линии: пустой inline-block нулевой высоты стоит НА базовой линии
    const strut=document.createElement('i');
    strut.className='pvcap_strut';
    txtEl.appendChild(strut);
    baseOff=(strut.getBoundingClientRect().bottom-er.top)*k;   // базовая линия от верха блока
    strut.remove();
  }
  if(!textW&&el.dataset.lastTextW)textW=parseFloat(el.dataset.lastTextW)||0;
  if(!baseOff&&el.dataset.lastBaseOff)baseOff=parseFloat(el.dataset.lastBaseOff)||0;
  if(textW)el.dataset.lastTextW=textW;
  if(baseOff)el.dataset.lastBaseOff=baseOff;
  // базовая линия текста обязана лечь ровно на cap.y — как Position слоя в AE
  if(baseOff)el.style.top=((cap.y-baseOff)/w*100).toFixed(3)+'cqw';
  // caption_x — ЛЕВЫЙ КРАЙ блока подписи: плашка начинается ровно на нём, а текст
  // стоит по её центру (в .jsx то же самое считает выражение на Position текста).
  // Без плашки центрировать не от чего — текст просто начинается на caption_x.
  if(textW)txtEl.style.left=(cap.bg?((textW*(kx-1)/2)/w*100).toFixed(3):'0')+(cap.bg?'cqw':'');

  if(cap.bg&&textW){
    const bf=cap.bg_fill||[0.345,0.345,0.345];
    const op=((cap.bg_op!=null?cap.bg_op:45)/100);
    const rr=Math.round((bf[0]||0)*255),gg=Math.round((bf[1]||0)*255),bb=Math.round((bf[2]||0)*255);
    const plateW=textW*kx, plateH=boxH*ky;
    inner.style.display='';
    inner.style.width=(plateW/w*100).toFixed(3)+'cqw';
    inner.style.height=(plateH/w*100).toFixed(3)+'cqw';
    inner.style.left='0';                                                // левый край плашки — на caption_x
    inner.style.top=((baseOff-size*RECT_MID-plateH/2)/w*100).toFixed(3)+'cqw';
    inner.style.background='rgba('+rr+','+gg+','+bb+','+op+')';
    inner.style.borderRadius=((cap.bg_round!=null?cap.bg_round:68)/w*100).toFixed(3)+'cqw';
  }else{
    inner.style.display='none';
  }
}
// ---- затемнение под интро по плану ----
// Мягкое чёрное затемнение снизу кадра под текстом интро — пользователь клал его руками в
// каждом ролике (Shape Layer в amdi1.aep). Числа (позиция, размер, размытие, прозрачность)
// считает Python в плане сцены: здесь только отрисовка, второй копии формул нет, как у тени
// прекомпа (plan.intro[].shadow). Слой висит на нуле Камеры 1 — значит едет и масштабируется
// вместе с её зумом (ipvIntroChild, та же функция-выбор, что у блока интро), но лежит НИЖЕ
// интро и вставок: затемнение обязано гасить кадр камеры, а не текст поверх него. План без
// ключа (галка выключена) — элемента нет вовсе.
function ipvShade(){
  const pl=IPV.plan,sh=pl&&pl.shade,st=$('ipvstage');
  let el=$('ipvshade');
  if(!sh||!st){if(el)el.remove();return;}
  if(!el){
    el=document.createElement('div');el.id='ipvshade';el.className='ipvshade';
    el.dataset.noi18n='1';
    const io=$('ipvintro');                    // в DOM перед интро: при равном z-index ниже его
    if(io)st.insertBefore(el,io);else st.appendChild(el);
  }
  const w=pl.w||1080;
  const k=(st.clientWidth||w)/w;               // пиксели превью на пиксель кадра
  const sc=(sh.scale!=null?sh.scale:100)/100;
  const bw=sh.w*k, bh=sh.h*k;
  // Центр прямоугольника в системе нула Камеры 1: Position слоя + масштаб × смещение
  // фигуры внутри группы. margin'ы сдвигают коробку её центром в центр кадра — дальше
  // работает та же функция-выбор, что у интро (ipvIntroChild): привязанное затемнение
  // едет за камерой, откреплённое стоит в координатах кадра.
  const cc=ipvIntroChild((sh.x||0)+(sh.ox||0)*sc,(sh.y||0)+(sh.oy||0)*sc,ipvNow());
  const s=cc[2];                               // зум камеры, а у откреплённого — 1
  el.style.width=bw+'px';el.style.height=bh+'px';
  el.style.marginLeft=(-bw/2)+'px';el.style.marginTop=(-bh/2)+'px';
  el.style.transform='translate('+(cc[0]*k)+'px,'+(cc[1]*k)+'px) scale('+(sc*s)+')';
  // размытие — в пикселях превью тем же масштабом кадр→превью, что R у тени интро; transform
  // идёт ПОСЛЕ фильтра, как в AE (эффект на источнике, потом Scale слоя)
  el.style.filter='blur('+((sh.blur||0)*k).toFixed(1)+'px)';
  el.style.opacity=String((sh.op!=null?sh.op:100)/100);
}
// ---- координаты интро и затемнения: одна функция-выбор ----
// Пока галка «интро едет с камерой» включена (plan.intro_cam !== false), нулы «интро»,
// «интро на кам2» и слой затемнения висят на нуле Камеры 1 — точка считается общей
// машиной ipvCamChild, как у вставок кам1. Галку сняли (intro_cam=false):
// в .jsx эти нулы идут по ветке else — координаты кадра, — значит ни зума, ни сдвига
// `pan`, ни слежения за головой: точка как есть, зум 1, как у свободных вставок кам2.
// Второй копии выбора нет: обе точки входа (ipvShade и ipvIntroPos) берут тут и точку,
// и зум — иначе затемнение и текст разъехались бы на откреплённом интро.
function ipvIntroChild(px,py,tm){
  const pl=IPV.plan,free=!!(pl&&pl.intro_cam===false);
  const s=free?1:ipvZoomAt(tm);
  const cc=free?[px,py]:ipvCamChild(px,py,s);
  return [cc[0],cc[1],s];
}
// ---- субтитры по плану: стопка по row, цвет по color, исчезновение группы по gend ----
// План пришёл заново, а слова на паузе те же: ключ показа (visKey ниже) не меняется, и
// ipvSubs оставлял старый DOM — правка шрифта или высоты в панели стиля не была видна,
// пока не сменится слово. Ключ СНИМАЕМ (а не пишем пустую строку): пустое значение
// совпало бы с ключом плана, у которого в этот момент нет видимых слов, и старые строки
// остались бы на экране. Так следующая отрисовка перестраивает строки всегда —
// ipvPlanFetch сам зовёт ipvUI, поэтому работает и на паузе.
function ipvSubsInvalidate(){const el=$('ipvsub');const host=el&&el.querySelector('.pvsubs_host');
  if(host)delete host.dataset.visKey;}
function ipvSubs(tm){const el=$('ipvsub');if(!el)return;
  const pl=IPV.plan;const subs=(pl&&pl.subs)||[];
  el.classList.toggle('plan',!!(pl&&subs.length));
  // styleSubPos ставит на ЭТОТ ЖЕ элемент инлайновый bottom (старый режим — одна строка
  // над низом кадра). Инлайн бьёт любое правило, поэтому в режиме плана контейнер
  // оставался высотой 60% кадра вместо 100%, и проценты слов считались от него —
  // стопка уезжала вверх («субтитры очень высоко», сверка с AE 2026-08-11).
  if(el.classList.contains('plan'))el.style.bottom='';
  if(pl&&pl.sub_hide&&pl.sub_hide.length){
    const subOp=keysAt(pl.sub_hide,null,tm)/100;
    el.style.opacity=subOp;
  }else{
    el.style.opacity='';
  }
  if(!subs.length){el.innerHTML='';return;}
  // Снимаем всё чужое из #ipvsub — текстовые узлы и посторонние элементы
  Array.from(el.childNodes).forEach(node=>{
    if(node.nodeType===3||(node.nodeType===1&&!node.classList.contains('pvsub_bg')&&!node.classList.contains('pvsubs_host'))){
      node.remove();
    }
  });
  const posy=pl.posy||0,step=pl.hl_step||0,h=pl.h||1920,w=pl.w||1080;
  // Порядок слоёв из плана сцены
  const defOrder=['subs','video','roto','photo','intro'];
  const order=(pl&&pl.layer_order)||defOrder;
  const getZ=k=>{const i=order.indexOf(k);return i>=0?(10-i):0;};
  el.style.zIndex=getZ('subs');
  const ovI=$('ipvins');if(ovI)ovI.style.zIndex=Math.max(getZ('photo'),getZ('video'));
  // Слой интро здесь НЕ трогаем: его zIndex ставит ipvIntro на каждом кадре — там же,
  // где живёт признак front (группа поверх видео, как moveToBeginning в AE).
  // Масштаб слоя прекомпа субтитров: то же число, что уходит в Scale в .jsx.
  // В AE якорь слоя в [W/2, POSY] — точка строки; в превью transform-origin в той же точке
  // (проценты от высоты контейнера = posy/H), иначе масштаб от центра утащит строку.
  const subScale=pl.sub_scale!=null?pl.sub_scale:100;
  if(subScale!==100){
    el.style.transform='scale('+(subScale/100)+')';
    el.style.transformOrigin='50% '+((posy/h)*100).toFixed(3)+'%';
  }else{
    el.style.transform='';el.style.transformOrigin='';
  }
  // кегль из плана: fsize задан в пикселях композиции, стойка её ширины — 100cqw.
  // План поля не дал — переменную СНИМАЕМ: иначе на новом плане остался бы кегль
  // прошлого (правка стиля шла бы мимо превью).
  if(pl.fsize)el.style.setProperty('--subfs',(pl.fsize/w*100).toFixed(3)+'cqw');
  else el.style.removeProperty('--subfs');
  // цвет базовых субтитров из плана: превью красит тем же, что AE.
  if(pl.sub_fill)el.style.setProperty('--subfc',rgb2hex(pl.sub_fill));
  else el.style.removeProperty('--subfc');
  // цвет выделения из плана: превью красит тем же, что AE (--subhl).
  if(pl.hl_fill)el.style.setProperty('--subhl',rgb2hex(pl.hl_fill));
  else el.style.removeProperty('--subhl');
  // тень субтитров из плана: при включённой плашке собственная тень текста снимается
  if(pl.sub_shadow===false){
    el.style.setProperty('--subsh','none');
  }else{
    el.style.removeProperty('--subsh');
  }
  const s=(typeof CURSTYLE!=='undefined'&&CURSTYLE)?CURSTYLE:{};
  const basePs=s.font||'SFPro-CondensedSemibold';
  const hlPs=s.hl_font||basePs;
  const baseFv=ipvFontFor(basePs);
  const hlFv=ipvFontFor(hlPs);
  // Кавычки ТОЛЬКО одинарные: строка уезжает в атрибут style="…", и двойная кавычка
  // внутри обрывает атрибут на себе — браузер получал `font-family:` без значения и
  // рисовал субтитры шрифтом страницы (жалоба «в превью не тот шрифт», третий раз).
  // CSS одинарные кавычки принимает и у семейства, и у осей вариативного шрифта.
  function fvCss(fv){
    if(!fv)return 'font-weight:800;';
    let cs=fv.family?('font-family:\''+fv.family+'\';'):'';
    if(fv.var){
      cs+='font-variation-settings:'+Object.entries(fv.var).map(([a,v])=>"'"+a+"' "+v).join(',')+';';
    }
    return cs;
  }
  const baseFvCss=fvCss(baseFv);
  const hlFvCss=fvCss(hlFv||baseFv);
  const vis=subs.filter(sub=>tm>=sub.s&&tm<sub.gend);
  const sbg=pl.sub_bg;
  let bgEl=el.querySelector('.pvsub_bg');
  if(sbg){
    if(!bgEl){
      bgEl=document.createElement('div');
      bgEl.className='pvsub_bg';
      el.appendChild(bgEl);
    }
    const bgFill=rgb2hex(sbg.fill||[1,1,1]);
    const bgOp=((sbg.op!=null?sbg.op:72)/100);
    const bgH=(sbg.h/w*100).toFixed(3)+'cqw';
    const bgR=(sbg.round/w*100).toFixed(3)+'cqw';
    const bgBot=((h-sbg.y)/h*100).toFixed(3)+'%';
    const bgAnim=(sbg.anim!=null?sbg.anim:0.22)+'s';
    bgEl.style.background=bgFill;
    bgEl.style.opacity=bgOp;
    bgEl.style.height=bgH;
    bgEl.style.borderRadius=bgR;
    bgEl.style.bottom=bgBot;
    bgEl.style.transition='width '+bgAnim+' cubic-bezier(.165,.84,.44,1)';
  }else if(bgEl){
    bgEl.remove();
    bgEl=null;
  }
  let host=el.querySelector('.pvsubs_host');
  if(!host){
    host=document.createElement('div');
    host.className='pvsubs_host';
    el.appendChild(host);
  }
  const visKey=vis.map(sub=>(sub.stack?'s':'r')+(sub.s)+':'+(sub.gend)+':'+(sub.w||'')+':'+(sub.row||0)).join('|');
  if(host.dataset.visKey!==visKey){
    host.dataset.visKey=visKey;
    // Элементы стопки подряд жёлтых помечены в плане stack: они вышли из строк
    // и рисуются каждый своей строкой, ровно как в режиме «по слову». Шаг у них HL_STEP
    // (план.hl_step), а не SUB_STEP строк — общий rowMap склеил бы стопку со строкой текста
    // в один ряд и поставил бы её по чужому шагу.
    const rowMap=new Map(), stackMap=new Map();
    vis.forEach(sub=>{
      const m=sub.stack?stackMap:rowMap;
      const r=sub.row||0;
      if(!m.has(r))m.set(r,[]);
      m.get(r).push(sub);
    });
    const lineHtml=(rowSubs,rowStep,r)=>{
      const bot=((h-(posy+r*rowStep))/h*100).toFixed(2);
      let minFs=Infinity;
      rowSubs.forEach(s=>{if(s.fsize&&s.fsize<minFs)minFs=s.fsize;});
      const fsStyle=(minFs<Infinity)?('font-size:'+(minFs/w*100).toFixed(3)+'cqw;'):'';

      const wordsHtml=rowSubs.map(sub=>{
        if(sub.words&&sub.words.length>1){
          return sub.words.map(wd=>{
            const isY=(wd.color==='yellow');
            const wCss=isY?hlFvCss:baseFvCss;
            // время появления жёлтого — из плана (t0): по нему ниже идут подъём,
            // проявление и блюр. Своей формулы «когда слово произнесено» в превью нет.
            const t0=(isY&&wd.t0!=null)?(' data-hl0="'+wd.t0+'"'):'';
            // своя длительность появления у укороченного слова (hd): в AE её
            // играет цикл стопки/слов, а не этот — превью берёт готовое число из плана.
            const hd=(isY&&wd.hd!=null)?(' data-hld="'+wd.hd+'"'):'';
            return '<span class="pvsubw_wd'+(isY?' yel':'')+'"'+t0+hd+' style="'+wCss+'">'+esc(wd.w)+'</span>';
          }).join(' ');
        }else{
          const isY=(sub.color==='yellow');
          const wCss=isY?hlFvCss:baseFvCss;
          // Жёлтое слово режима «по слову» и стопки въезжает так же, как его слой в AE
          // момент — начало слова (s плана = inPoint слоя), у строки из одного
          // слова — момент ZH из плана (words[0].t0). Длительность — hd плана, если план её
          // знает: у стопки/слова это поле элемента, у строки из ОДНОГО слова — поле слова
          // нет её — превью берёт общую hl_dur, как и раньше.
          const w0=(sub.words&&sub.words[0])||null;
          const t0=isY?((w0&&w0.t0!=null)?w0.t0:sub.s):null;
          const hl0=(isY&&t0!=null)?(' data-hl0="'+t0+'"'):'';
          const wHd=(w0&&w0.hd!=null)?w0.hd:sub.hd;
          const hd=(isY&&wHd!=null)?(' data-hld="'+wHd+'"'):'';
          return '<span class="pvsubw_wd'+(isY?' yel':'')+'"'+hl0+hd+' style="'+wCss+'">'+esc(sub.w)+'</span>';
        }
      }).join(' ');

      const allYel=rowSubs.every(s=>s.color==='yellow');
      return '<span class="pvsubw'+(allYel?' yel':'')+'" style="bottom:'+bot+'%;'+fsStyle+'">'+wordsHtml+'</span>';
    };
    let html='';
    rowMap.forEach((rowSubs,r)=>html+=lineHtml(rowSubs,rowSubs[0].sub_step||pl.sub_step||step,r));
    stackMap.forEach((stSubs,r)=>html+=lineHtml(stSubs,step,r));
    host.innerHTML=html;
  }
  // Появление жёлтого в строке: до своего момента слово невидимо, за HL_DUR
  // поднимается на hl_rise и проявляется — та же кривая, что easePair в AE (keysAt с
  // дефолтными 35/90 = aeEase). Числа и время появления — из плана, своей копии нет.
  // Короткое жёлтое играет СВОЮ длительность hd из плана: общей HL_DUR слову
  // с малым видимым временем не хватало, и анимация обрывалась его исчезновением.
  // Подъём — position:relative + top, а НЕ transform: .pvsubw_wd — обычный inline-span,
  // а к inline-боксу transform не применяется вовсе (сдвиг просто пропал бы). relative
  // раскладку строки не трогает — в отличие от inline-block, который ломает кернинг.
  const rise=pl.hl_rise||0, hDur=(pl.hl_dur!=null?pl.hl_dur:0.35);
  const blAmt=pl.hl_blur?(pl.hl_blur_amt||0):0;
  const kpx=(el.clientWidth||w)/w;               // пиксели превью на пиксель кадра
  host.querySelectorAll('.pvsubw_wd').forEach(wsp=>{
    const t0=parseFloat(wsp.dataset&&wsp.dataset.hl0);
    if(!(t0>=0))return;                          // у слова своей анимации нет — как было
    const hd=parseFloat(wsp.dataset&&wsp.dataset.hld);   // своя длительность — если план дал
    const dur=(hd>0)?hd:hDur;                    // нет поля: общая hl_dur, как раньше
    const rem=(tm<t0+dur)?keysAt([[t0,1],[t0+dur,0]],null,tm):0;   // 1 -> 0 по кривой
    wsp.style.position=rem>0?'relative':'';
    wsp.style.top=rem>0?(rise*rem*kpx).toFixed(2)+'px':'';
    wsp.style.opacity=rem>0?String(1-rem):'';
    wsp.style.filter=(blAmt&&rem>0)?('blur('+(blAmt*rem*kpx).toFixed(2)+'px)'):'';
  });
  if(sbg&&bgEl){
    let maxW_px=0;
    const textSpans=host.querySelectorAll('.pvsubw');
    textSpans.forEach(sp=>{
      let lineW=0;
      const wds=sp.querySelectorAll('.pvsubw_wd');
      if(wds&&wds.length>0){
        let minX=Infinity,maxX=-Infinity;
        wds.forEach(wsp=>{
          const r=wsp.getBoundingClientRect();
          if(r.width>0){
            minX=Math.min(minX,r.left);
            maxX=Math.max(maxX,r.right);
          }
        });
        if(minX<Infinity)lineW=maxX-minX;
      }
      if(!lineW){
        const range=document.createRange();
        range.selectNodeContents(sp);
        lineW=range.getBoundingClientRect().width;
      }
      maxW_px=Math.max(maxW_px,lineW);
    });
    const stageW=el.clientWidth||1080;
    const k=w/stageW;
    const rawW=maxW_px*k;
    if(rawW>0){
      const pad=sbg.pad!=null?sbg.pad:18.0;
      const padmin=sbg.padmin!=null?sbg.padmin:70.0;
      const fullW=rawW+Math.max(2*padmin,rawW*2*pad/100);
      const fullCqw=(fullW/w*100).toFixed(3)+'cqw';
      bgEl.style.width=fullCqw;
      bgEl.dataset.lastWidth=fullCqw;
    }else if(bgEl.dataset.lastWidth){
      bgEl.style.width=bgEl.dataset.lastWidth;
    }
  }
}
// tm, а не t: имя t занято функцией перевода, а тут в теле есть t('стык ') — параметр
// перекрывал её, и ipvUI падал «t is not a function» на КАЖДОМ изменении счётчиков.
// Из-за этого не работали протяжка плейхеда, ползунок перемотки и кнопка генерации
// (см. ipvOverlay и insAdd — та же ловушка, тест test_time_var_never_shadows_t).
function ipvUI(tm){const seek=$('ipvseek');if(seek&&document.activeElement!==seek)seek.value=IPV.dur?Math.round(tm/IPV.dur*1000):0;
  $('ipvtime').textContent=fmtT(tm)+' / '+fmtT(IPV.dur);
  vgDuck(tm,IPV.plan);
  let cur='',yel=false;
  for(let k=0;k<IPV.words.length;k++){const w=IPV.words[k];
    if(tm>=w.s&&tm<w.e){cur=w.w;yel=(IPVMODE==='ae'&&typeof HL!=='undefined'&&HL.has(k));break;}}
  const sb=$('ipvsub');
  if(IPV.plan){
    ipvSubs(tm);       // стопка субтитров по плану
    ipvTopLine(tm);    // верхняя строка-прогресс по плану
    ipvCaption(tm);    // подпись о ролике по плану
  }
  else{
    const tle=$('ipvtopline');if(tle)tle.style.display='none';
    const cape=$('ipvcaption');if(cape)cape.style.display='none';
    sb.style.opacity='';
    sb.style.removeProperty('--subfs');
    sb.style.removeProperty('--subfc');
    sb.style.removeProperty('--subhl');
    sb.style.removeProperty('--subsh');
    sb.textContent=cur;sb.style.color=yel?'var(--subhl,var(--yel))':'';sb.classList.remove('plan');
  }
  ipvZoom(tm);                                    // наезд/дрейф Камеры 1 по плану
  ipvShade();                                     // затемнение под интро — из плана
  ipvStartBlur(tm);                               // размытие на старте — CSS-фильтр на кадре
  itlPh(tm);ipvIntro(tm);ipvOverlay(tm);aewHighlight(tm);
  if(typeof subrowHighlight==='function')subrowHighlight(tm);
  sfxSync(tm);                                    // SFX по плану
  // Счётчики честности показа: «стык/своп/сидк» — про склейку (сколько прошло подменой
  // дублёра, а сколько сорвалось в seek), «кам/замер» — про ракурсы: сколько было
  // переключений и на скольких кадрах входящая камера ещё доигрывала seek (кадр её же,
  // но чуть прежний). Чужой ракурс не показывается вовсе, поэтому счётчика на него нет.
  // Хрому они не нужны (съедали место у ползунков — жалоба 2026-08-12): в консоль.
  const st=IPV.stats,stt=st.styk+'/'+st.swap+'/'+st.seek+'/'+st.cam+'/'+st.stale+'/'+st.back;
  if(stt!==IPV._stt){IPV._stt=stt;console.log(t('стык ')+st.styk+t(' · своп ')+st.swap+t(' · сидк ')+st.seek
    +t(' · кам ')+st.cam+t(' · замер ')+st.stale+t(' · откат ')+st.back);}}
// размер карточки фотовставки НА ЭКРANE (кадр 1080×1920) — тот же расчёт, что _ins_scale
// в xml2ae.py: вписываем видимую часть в коробку, не-ультравайд режется маской в квадрат.
// BH — среднее cam1/cam2 (560/495): стиль (какая камера активна) в UI ещё неизвестен.
// mw/mh — ручная форма маски в % (см. scrubMask): растягивают/сужают ВИДИМУЮ часть, а
// масштаб карточки остаётся авторасчётным — те же клампы, что в JSX (за краем фото пусто).
// Возвращает и окно маски (w/h), и размер САМОГО фото в тех же px (pw/ph): в прекомпе фото
// тянется под ширину композа и маска его обрезает, а не ужимает — предпросмотру нужны оба.
// sc — ручной масштаб (% от авто): множит ВСЮ карточку вместе с маской, как scale слоя в AE.
function insPreviewBox(iw,ih,mw,mh,sc){const W=1080,H=1920,BW=1030,BH=528,SQ=2.2;
  const photoH=ih*W/iw;
  let visH=photoH,visW=W;
  if(visH>0&&W/visH<=SQ){visW=visH=Math.min(W,visH);}
  const s=Math.min(BW/visW,BH/visH)*(sc==null?100:sc)/100;
  visW=Math.max(20,Math.min(W,visW*(mw==null?100:mw)/100));
  // выше H маска не растёт даже у фото длиннее композа: в JSX её углы клампятся по кадру
  visH=Math.max(20,Math.min(photoH,H,visH*(mh==null?100:mh)/100));
  return {w:visW*s,h:visH*s,pw:W*s,ph:photoH*s};}
// размер видеовставки на экране: заполнение кадра × sc (зеркало fitInto в xml2ae).
// Пока sc=100, коробка = кадр и предпросмотр рисует видео как раньше, через object-fit:cover.
function insVideoFill(vw,vh,sc){const W=1080,H=1920;
  const f=Math.max(W/vw,H/vh)*((sc==null?100:sc)/100);
  return {w:vw*f,h:vh*f};}
// панорама полноэкранной видеовставки: позиция = x/y как их задал пользователь (px кадра),
// ничем не зажата. Раньше сдвиг упирался в запас вылета ролика за кадр
// (зеркало fillSlack в xml2ae): у вертикального 9:16 при sc=100 запаса нет вовсе — видео
// не двигалось совсем, а 16:9 по высоте не двигалось никогда. sx/sy остаются в ответе
// СПРАВКОЙ (сколько ролик вылезает за кадр), позицию они не режут.
function insVideoPan(vw,vh,x,y){const W=1080,H=1920;
  if(!vw||!vh)return {x:x||0,y:y||0,sx:0,sy:0};
  const f=Math.max(W/vw,H/vh),sx=Math.max(0,(vw*f-W)/2),sy=Math.max(0,(vh*f-H)/2);
  return {x:x||0,y:y||0,sx,sy};}
// ---- видеовставки: ОДИН <video> на файл и размеры из плана ----
// Моргание чёрным: перестройка оверлея (новый план после каждой правки, ipvRefresh сбрасывает
// IPV.cur) чистила #ipvins и создавала НОВЫЙ <video> с тем же src — элемент показывает чёрное,
// пока не загрузит первый кадр. Поэтому элементы живут в кэше по пути файла и переезжают из
// обёртки в обёртку; src у живого элемента не переприсваивается вовсе.
function insVidCache(){if(!IPV.insVids)IPV.insVids=new Map();return IPV.insVids;}
function insVidDimsMap(){if(!IPV.dims)IPV.dims=new Map();return IPV.dims;}
// ключ кэша: нормализованный путь файла; «#N» — N-й ОДНОВРЕМЕННЫЙ показ того же файла
// (элемент нельзя вставить в два места DOM, а в кадре один и тот же ролик бывает дважды)
function insVidKey(media,n){const k=normInsPath(media);return n?k+'#'+n:k;}
// живой <video> этого файла. used — ключи, занятые текущей перестройкой: переиспользуем
// готовый элемент (кадр уже декодирован), новый заводим только когда такого ещё нет
function insVideoEl(media,used){
  used=used||new Set();
  for(let n=0;;n++){
    const key=insVidKey(media,n),have=insVidCache().get(key);
    if(have){if(!used.has(key)){used.add(key);return have;}continue;}   // занят другой вставкой в этом же кадре
    const v=document.createElement('video');
    v.src='/api/media?path='+encodeURIComponent(media);
    v.muted=true;v.playsInline=true;v.preload='auto';
    // Размеры файла приходят только с метаданными: запоминаем их и перерисовываем кадр, если
    // план их не несёт (fitw/fith). Иначе вставка осталась бы вовсе без масштаба — и «Масштаб, %»
    // не на что было бы умножать.
    v.addEventListener('loadedmetadata',()=>{
      insVidDimsPut(media,v.videoWidth,v.videoHeight);
      if(IPV.plan&&!insVidPlanDims(media)&&typeof ipvUI==='function')ipvUI(ipvNow());});
    insVidCache().set(key,v);used.add(key);return v;}
}
// освободить элемент: пауза, снятый src (файл перестаёт держать соединение)
// и load() — им браузер отпускает ресурс
function insVidFree(v){if(!v)return;
  try{v.pause();}catch(e){}
  try{v.removeAttribute('src');}catch(e){}
  try{v.load();}catch(e){}
  if(v.parentNode&&v.parentNode.removeChild)try{v.parentNode.removeChild(v);}catch(e){}}
// размеры файла (как _media_dims в Python): из кэша размеров, иначе с живого элемента
function insVidDimsGet(media){
  const m=insVidDimsMap().get(normInsPath(media));
  if(m&&m.w&&m.h)return m;
  const v=insVidCache().get(insVidKey(media,0));
  return (v&&v.videoWidth&&v.videoHeight)?{w:v.videoWidth,h:v.videoHeight}:null;}
function insVidDimsPut(media,w,h){w=+w||0;h=+h||0;
  if(w&&h)insVidDimsMap().set(normInsPath(media),{w:w,h:h});}
// несёт ли план размеры файла (fitw/fith): если да, перерисовка по метаданным не нужна
function insVidPlanDims(media){const k=normInsPath(media);
  const arr=(IPV.plan&&IPV.plan.inserts)||[];
  return arr.some(x=>normInsPath(x.media)===k&&x.fitw&&x.fith);}
// коробка заполнения видеовставки в px КОМПОЗИЦИИ: fitw/fith из плана (их считает Python из
// размеров файла), иначе — по закэшированным размерам того же файла. null — размеров нет вовсе,
// тогда размер поставит первый loadedmetadata (молча без масштаба не остаёмся)
function ipvInsDims(x){
  if(x.fitw&&x.fith)return {w:x.fitw,h:x.fith};
  const d=insVidDimsGet(x.media);if(!d)return null;
  const b=insVideoFill(d.w,d.h,100);return {w:b.w,h:b.h};}
// ключи кэша, чьи вставки ещё есть в текущем клипе/плане: их элементы не освобождаем
function insVidKeep(){
  const out=[];
  const add=media=>{if(!media)return;const base=normInsPath(media);let k=base,n=0;
    while(out.indexOf(k)>=0){n++;k=base+'#'+n;}out.push(k);};
  const arr=(IPV.plan&&IPV.plan.inserts)||null;
  if(arr){for(const x of arr)if((x.t==='video'||x.type==='video')&&x.media)add(x.media);}
  else{for(const x of ipvIns())if(x.type==='video'&&x.media)add(x.media);}   // плана нет — карточки/INS
  return out;}
// чего нет в клипе/плане — освободить (файл больше не держит соединение); что есть, но сейчас
// не в кадре — на паузу: элемент живёт в кэше для повторного показа, а играть втихую не должен
function insVidSweep(used){
  const keep=insVidKeep();
  insVidCache().forEach((v,key)=>{
    if(keep.indexOf(key)<0){insVidFree(v);insVidCache().delete(key);return;}
    if(!(used&&used.has(key))&&!v.paused)try{v.pause();}catch(e){}});}
// ipvOpen на другом клипе: все элементы прошлого клипа освобождаем
function insVidFreeAll(){if(!IPV.insVids)return;
  IPV.insVids.forEach(v=>insVidFree(v));IPV.insVids.clear();}
// tm, а не t: тело зовёт t('ФОТО') для заглушки «файл не выбран», и параметр-время
// перекрывал функцию перевода. Падало ровно там, где перекрытие и надо было увидеть:
// первая вставка успевала отрисоваться, вторая — нет, а классы .cur/.playing (по ним
// таймлайн и карточки понимают, на какой вставке стоит плейхед) не проставлялись вовсе.
function ipvOverlay(tm){const ov=$('ipvins');if(!ov)return;
  // Есть план сцены — рисуем по плану: позиция/масштаб/появление из
  // готовых ключей, ничего не досчитываем. Без плана (ошибка запроса) — прежняя ветка-фолбэк.
  if(IPV.plan)return ipvOverlayPlan(tm);
  const arr=ipvIns();
  // ВСЕ вставки, активные в момент tm: в AE они лежат друг на друге (позже добавленная — выше),
  // поэтому и здесь показываем все разом, а не только верхнюю — перекрытие должно быть видно.
  let act=[];
  for(let i=0;i<arr.length;i++){const sd=insSD(arr[i]);if(tm>=sd.s&&tm<sd.s+sd.d)act.push(i);}
  const top=act.length?act[act.length-1]:-1;
  const sig=act.join(',');
  if(sig!==IPV.cur){IPV.cur=sig;ov.innerHTML='';ov.className='ipvins'+(act.length?' on':'');
    const usedVid=new Set();                       // ключи кэша, занятые этой перестройкой
    for(let a=0;a<act.length;a++){const i=act[a],x=arr[i];const vid=x.type==='video';
      // «full» (object-fit:cover по кадру) годится, только пока видео кадр ЗАПОЛНЯЕТ:
      // ужатому (sc<100) размер ставим руками, иначе cover врал бы — показывал обрезанный
      // кадр вместо всего ролика, который в AE виден целиком
      const vidCard=vid&&x.media&&(x.sc==null?100:x.sc)!==100;
      // обёртка на вставку: каждая ложится ПОВЕРХ предыдущих (позже в DOM = выше в AE-стеке),
      // а внутри — тот же элемент, что раньше (маска-фото / видео / заглушка)
      const defOrder=['subs','video','roto','photo','intro'];
      const order=(IPV.plan&&IPV.plan.layer_order)||defOrder;
      const wr=document.createElement('div');
      wr.className='ipvwrap'+(vid&&x.media&&!vidCard?' full':'')+(x.mosaic?' mosaic':'');
      wr.style.zIndex=vid?(10-order.indexOf('video')):(10-order.indexOf('photo'));
      wr.dataset.ins=i;
      if(x.media){
        if(isPhotoPath(x.media)){
          // фото живёт В МАСКЕ: обёртка = окно маски (overflow:hidden), картинка внутри —
          // всегда во всю ширину композа. Без обёртки фото просто ужималось до размера
          // окна (object-fit:cover), и у горизонтальных снимков уменьшение «В» уменьшало
          // ВСЮ картинку вместо того, чтобы срезать верх/низ, как маска в AE.
          const mk=document.createElement('div');mk.className='insmask';
          const im=document.createElement('img');im.src=insImgURL(x.media,x.plate);
          mk.appendChild(im);wr.appendChild(mk);}
        else{const iv=insVideoEl(x.media,usedVid);wr.appendChild(iv);}}
      else{const ph=document.createElement('div');ph.className='ph';
        ph.innerHTML='<b>'+(vid?t('ВИДЕО'):t('ФОТО'))+(x.mosaic?' · MOSAIC':'')+'</b>'
          +esc(IPVMODE==='ae'?t('файл не выбран'):(x.query||t('(без запроса)')));
        wr.appendChild(ph);}
      ov.appendChild(wr);
      // ---- ставим вставку ТУДА, ГДЕ ОНА БУДЕТ в AE, чтобы по превью целиться x/y ----
      // Фото садится в верхнюю треть (cam2 Y=330, cam1 ~393 — берём 330; стиль в UI неизвестен),
      // видео — во весь кадр (center). x/y двигают ЭТУ точку покоя. Стойка 9:16 = кадр 1080×1920
      // один-в-один, поэтому масштаб k один на обе оси.
      const el=wr.firstChild;const k=(ov.clientWidth||1080)/1080;
      const fullVid=vid&&x.media&&!vidCard;            // видео во весь экран (object-fit:cover)
      const isImg=x.media&&isPhotoPath(x.media);
      if(el&&el.style){
        const baseY=vid?960:330;                       // видео = центр (весь кадр), фото = верхняя треть
        const setPos=()=>{el.style.transform='translate('+((x.x||0)*k)+'px,'+(((baseY-960)+(x.y||0))*k)+'px)';};
        if(isImg){                                     // размер как в AE (та же коробка, что _ins_scale)
          const im=el.querySelector('img');
          const fit=()=>{const iw=im.naturalWidth,ih=im.naturalHeight;
            if(iw&&ih){const b=insPreviewBox(iw,ih,x.mw,x.mh,x.sc);
              el.style.width=(b.w*k)+'px';el.style.height=(b.h*k)+'px';      // окно маски
              im.style.width=(b.pw*k)+'px';im.style.height=(b.ph*k)+'px';}   // фото — под ширину композа
            setPos();};
          if(im.complete&&im.naturalWidth)fit();else im.addEventListener('load',fit,{once:true});
          setPos();
        }
        else if(vidCard){                              // ужатое видео: размер = заполнение × sc, ролик виден целиком
          const fit=()=>{const vw=el.videoWidth,vh=el.videoHeight;
            if(vw&&vh){const b=insVideoFill(vw,vh,x.sc);
              el.style.maxWidth='none';el.style.maxHeight='none';el.style.objectFit='fill';
              el.style.width=(b.w*k)+'px';el.style.height=(b.h*k)+'px';}
            setPos();};
          if(el.videoWidth)fit();else el.addEventListener('loadedmetadata',fit,{once:true});
          setPos();                                    // ужатая вставка ездит по x/y свободно — как в .jsx
        }
        // Полноэкранное видео двигаем КАРТИНКОЙ ВНУТРИ коробки (object-position), а не
        // transform'ом обёртки: обёртка тут и есть кадр, и сдвиг её целиком выглядел бы как
        // поехавший монтаж. Сдвиг — ровно x/y пользователя (px кадра × k), без клампа
        // уехав за край, ролик открывает то, что под ним, — кадр камеры.
        else if(fullVid){const pan=()=>{const p=insVideoPan(el.videoWidth,el.videoHeight,x.x,x.y);
            // k — коробка предпросмотра относительно кадра 1080: сдвиг тоже в её пикселях
            el.style.objectPosition='calc(50% + '+(p.x*k)+'px) calc(50% + '+(p.y*k)+'px)';};
          if(el.videoWidth)pan();else el.addEventListener('loadedmetadata',pan,{once:true});}
        else setPos();                                 // заглушка «файл не выбран» — как раньше
      }}
    insVidSweep(usedVid);                              // лишнее — на паузу, чужое — освободить
    const cardSel=(IPVMODE==='ae')?'#inslist .inscard':'#insHost .inscard';
    document.querySelectorAll(cardSel).forEach((c,i)=>c.classList.toggle('playing',act.includes(i)));
    document.querySelectorAll('#itlblocks .itlblk:not(.intro):not(.roto)').forEach((b,i)=>b.classList.toggle('cur',act.includes(i)));
    if(top>=0&&IPVMODE!=='ae'){const c=document.querySelectorAll('#insHost .inscard')[top];
      if(c)c.scrollIntoView({block:'nearest',behavior:'smooth'});}}
  for(let a=0;a<act.length;a++){const i=act[a],x=arr[i];
    const v=ov.querySelector('[data-ins="'+i+'"] video');
    if(!v||!x.media)continue;                        // видеовставка идёт от своего sin вместе с монтажом
    const want=Math.max(0,(x.sin||0)+tm-insSD(x).s);
    if(Math.abs((v.currentTime||0)-want)>0.4){try{v.currentTime=want;}catch(e){}}
    if(IPV.playing&&v.paused)v.play().catch(()=>{});
    if(!IPV.playing&&!v.paused)v.pause();}}
// ---- вставки ПО ПЛАНУ: всё из plan.inserts, геометрия — готовая, не считается ----
function ipvOverlayPlan(tm){const ov=$('ipvins');const arr=(IPV.plan&&IPV.plan.inserts)||[];
  const allCards=ipvIns();
  const act=[];
  for(let i=0;i<arr.length;i++){const x=arr[i];
    if(tm>=+x.start&&tm<+x.end)act.push({isPlan:true,i:i,x:x,start:+x.start});}   // окна из плана (снап/срезы уже учтены)
  if(IPVMODE==='clips'){
    for(let ci=0;ci<allCards.length;ci++){const x=allCards[ci];
      if(!(x.media||'').trim()){const sd=insSD(x);                                // карточки без файла (): окно из карточки
        if(tm>=sd.s&&tm<sd.s+sd.d)act.push({isPlan:false,ci:ci,x:x,start:sd.s});}}}
  act.sort((a,b)=>a.start-b.start);                                              // позже начавшаяся — выше в DOM/AE-стеке
  const sig=act.map(a=>a.isPlan?'p'+a.i:'c'+a.ci).join(',');
  if(sig!==IPV.cur){IPV.cur=sig;ov.innerHTML='';ov.className='ipvins ae'+(act.length?' on':'');
    const usedVid=new Set();                       // ключи кэша, занятые этой перестройкой
    for(let a=0;a<act.length;a++){const item=act[a],x=item.x;const vid=(x.t==='video'||x.type==='video');
      const vidCard=vid&&x.media&&(x.sc==null?100:x.sc)!==100;
      const defOrder=['subs','video','roto','photo','intro'];
      const order=(IPV.plan&&IPV.plan.layer_order)||defOrder;
      const wr=document.createElement('div');
      wr.className='ipvwrap'+(vid&&x.media&&!vidCard?' full':'')+(x.mosaic?' mosaic':'');
      wr.style.zIndex=vid?(10-order.indexOf('video')):(10-order.indexOf('photo'));
      wr.dataset.ins=item.isPlan?item.i:('c'+item.ci);
      if(x.media){
        if(isPhotoPath(x.media)){
          const card=x.card||null;
          // фото на подложке: ДВА img — плашка на весь card.w×card.h и фото
          // поверх со сдвигом от её центра. Окна маски нет вовсе: обрезки в этом режиме нет.
          if(card&&card.plate){
            const pl=document.createElement('div');pl.className='insplate';
            pl.style.position='relative';pl.style.flex='none';
            pl.style.filter='drop-shadow(0 6px 18px rgba(0,0,0,.6))';   // тот же insFX-вид, что у маски
            const ip=document.createElement('img');ip.className='iplate';
            // подложке nobg НЕ просим: фон снимается у ФОТО вставки с галкой «на подложке»,
            // а у плашки своя прозрачность — rembg её только испортит
            ip.src='/api/media?path='+encodeURIComponent(card.plate);
            ip.style.position='absolute';ip.style.left='50%';ip.style.top='50%';
            ip.style.transform='translate(-50%,-50%)';
            ip.style.maxWidth='none';ip.style.maxHeight='none';ip.style.objectFit='fill';ip.style.filter='none';
            const ph2=document.createElement('img');ph2.className='iphoto';
            // Путь берём из ПЛАНА как есть: у вставки «на подложке» это уже кэш .nobg.png
            // (подмену делает план сцены) — nobg=1 поверх кэша завёл бы второй
            // файл. Размеры рамки и файл — из одного места, плана.
            ph2.src=insImgURL(x.media);
            ph2.style.position='absolute';ph2.style.left='50%';ph2.style.top='50%';
            ph2.style.maxWidth='none';ph2.style.maxHeight='none';ph2.style.objectFit='fill';ph2.style.filter='none';
            pl.appendChild(ip);pl.appendChild(ph2);wr.appendChild(pl);
          }else{
            const mk=document.createElement('div');mk.className='insmask';
            const im=document.createElement('img');im.src=insImgURL(x.media);
            mk.appendChild(im);wr.appendChild(mk);}}
        else{const iv=insVideoEl(x.media,usedVid);wr.appendChild(iv);}}
      else{const ph=document.createElement('div');ph.className='ph';
        ph.innerHTML='<b>'+(vid?t('ВИДЕО'):t('ФОТО'))+(x.mosaic?' · MOSAIC':'')+'</b>'
          +esc(IPVMODE==='ae'?t('файл не выбран'):(x.query||t('(без запроса)')));
        wr.appendChild(ph);}
      ov.appendChild(wr);}
    insVidSweep(usedVid);                          // лишнее — на паузу, чужое — освободить
    const cardActs=[];
    for(let a=0;a<act.length;a++){const item=act[a];
      if(item.isPlan){
        const nm=insCardKey(item.x);
        const ci=allCards.findIndex(z=>nm&&nm===normInsPath(z.media));
        if(ci>=0&&!cardActs.includes(ci))cardActs.push(ci);
      }else{
        if(item.ci>=0&&!cardActs.includes(item.ci))cardActs.push(item.ci);
      }
    }
    const cardSel=(IPVMODE==='ae')?'#inslist .inscard':'#insHost .inscard';
    document.querySelectorAll(cardSel).forEach((c,i)=>c.classList.toggle('playing',cardActs.includes(i)));
    document.querySelectorAll('#itlblocks .itlblk:not(.intro):not(.roto)').forEach((b,i)=>b.classList.toggle('cur',cardActs.includes(i)));
    const topCard=cardActs.length?cardActs[cardActs.length-1]:-1;
    if(topCard>=0&&IPVMODE!=='ae'){const c=document.querySelectorAll('#insHost .inscard')[topCard];
      if(c)c.scrollIntoView({block:'nearest',behavior:'smooth'});}
  }
  for(let a=0;a<act.length;a++){const item=act[a];
    const key=item.isPlan?item.i:('c'+item.ci);
    const wr=ov.querySelector('[data-ins="'+key+'"]');if(wr)ipvInsPlace(wr,item.x,tm);}   // покадровая геометрия
  for(let a=0;a<act.length;a++){const item=act[a],x=item.x;
    if(!item.isPlan||!x.media)continue;                        // видеовставка идёт от своего sin вместе с монтажом
    const v=ov.querySelector('[data-ins="'+item.i+'"] video');
    if(!v)continue;
    const want=Math.max(0,(x.sin||0)+tm-(+x.start));
    if(Math.abs((v.currentTime||0)-want)>0.4){try{v.currentTime=want;}catch(e){}}
    if(IPV.playing&&v.paused)v.play().catch(()=>{});
    if(!IPV.playing&&!v.paused)v.pause();}}
// покадровая позиция/масштаб/прозрачность вставки из плана. Карточка фото (x.card) — в
// comp-координатах осевшего масштаба; anim.scale — Scale слоя в AE, делится на осевший S.
function ipvInsPlace(wr,x,tm){
  const el=wr.firstChild;if(!el)return;
  const pl=IPV.plan;const W=pl?pl.w:1080,H=pl?pl.h:1920,k=(wr.clientWidth||W)/W;
  // Временный сдвиг во время/после драга: тянем вставку — она едет с пальцем
  // до пересчёта плана (план кэш, свежий несёт сдвиги сам). В данные пишется по отпусканию,
  // здесь сдвиг только показывается. Плана нет — ноль, обычный показ.
  const sh=(IPV.insShift&&IPV.insShift.i===+wr.dataset.ins)?IPV.insShift:null;
  const sx=sh?sh.dx:0,sy=sh?sh.dy:0;
  const anim=x.anim;
  if((x.t==='video'||x.type==='video')&&x.media){     // видео: коробка заполнения × sc, панорама x/y из плана
    const sc=(x.sc==null?100:x.sc);
    // Коробка — ОДНА на все sc: заполнение кадра при sc=100 (fitw/fith) × sc/100. Раньше ветка
    // sc>=100 жёстко ставила width/height 100% с object-fit:cover и sc не читала вовсе: «Масштаб, %»
    // больше 100 не менял на экране ничего, а на sc≠100 обёртка теряла класс .full и элемент
    // попадал под правило .ipvins video{max-width:72%;max-height:46%} — ролик рисовался МАЛЕНЬКОЙ
    // обрезанной карточкой вместо кадра. Инлайновые max-width/max-height это правило и снимают.
    // Размеры — из плана, иначе из кэша того же файла; нет ни там, ни там — поставит
    // loadedmetadata (ipvInsDims не молчит: кадр перерисуется, а не останется без масштаба).
    const d=ipvInsDims(x);
    el.style.maxWidth='none';el.style.maxHeight='none';el.style.objectFit='fill';el.style.objectPosition='';
    if(d){el.style.width=(d.w*sc/100*k)+'px';el.style.height=(d.h*sc/100*k)+'px';}
    // Позиция — ровно x/y из плана + сдвиг драга, при ЛЮБОМ масштабе: клампа
    // запасом вылета нет (план его и не зажимает — slackx/slacky там справка), поэтому
    // вертикальное 9:16 при sc=100 тоже двигается. Коробка уезжает за край кадра — в
    // проёме видно то, что под вставкой (кадр камеры), и в AE ровно так же.
    const px2=(x.x||0)+sx,py2=(x.y||0)+sy;
    el.style.transform='translate('+(px2*k)+'px,'+(py2*k)+'px)';
    return;}
  if(x.card){                                        // фото: окно маски из плана, масштаб — anim.scale
    const style=x.style||'cam2';
    let m=1,op=1,px=(x.x||0),py=(x.y||0);
    if(style==='cam2'&&anim){
      const S=(x.scale||44)*(x.sc||100)/100;         // осевший Scale слоя (см. шаблон: Ss=scale*sc/100)
      if(S>0&&anim.scale)m=keysAt(anim.scale,null,tm)/S;         // наезд: pk/S -> 1 или rise: S0/S -> 1
      if(anim.opacity)op=keysAt(anim.opacity,null,tm)/100;
      if(anim.position){
        const p=keysAt(anim.position,null,tm);
        if(Array.isArray(p)){px=p[0]-W/2;py=p[1]-H/2;}
      }else{
        py=((pl&&pl.ins_c2y||0)-H/2)+(x.y||0);px=((pl&&pl.ins_c2x||W/2)-W/2)+(x.x||0);   // точка покоя cam2 — из стиля
      }
    }else if(anim&&anim.position){                   // cam1 «из-за спины»: ключи ОТ ЦЕНТРА кадра
      // Ключи _cam1_pos_keys посчитаны в ЛОКАЛЬНЫХ координатах нула «вставки кам1»
      // (cx=cy=0), а нул стоит в центре кадра. Значит это УЖЕ смещение от центра, и
      // вычитать W/2 и H/2 нельзя: так фото уезжало ровно на пол-кадра влево и вверх,
      // за край экрана (первая сверка с AE, 2026-08-11).
      const p=keysAt(anim.position,null,tm);
      if(Array.isArray(p)){px=p[0];py=p[1];}
    }
    // cam1-вставка в кадре кам1 наследует зум нула Камеры 1 (в AE
    // insNull1.parent=cam1null) — размер умножается на зум, а СМЕЩЕНИЕ от центра
    // считается правилом экран = C + s*(p−C) (ipvCamChild). oncam2 (кам1
    // на перебивке) и кам2 сидят на СВОБОДНЫХ нулах — им зум не положен.
    const z=(style==='cam1'&&!x.oncam2)?ipvZoomAt(tm):1;
    const c=x.card;
    el.style.width=(c.w*m*k*z)+'px';el.style.height=(c.h*m*k*z)+'px';
    if(c.plate){                                     // фото на подложке
      // Плашка — на весь элемент (card.w×card.h), фото поверх со сдвигом от её центра.
      // Числа — из плана (card.photo), своих расчётов здесь нет. Ручные x/y двигают
      // ТОЛЬКО фото (поля страницы вставок), а драг в кадре (IPV.insShift) — ВСЮ карточку:
      // он уходит в точку покоя слоя, и в данные пишется kx/ky. Раньше сдвиг
      // драга уезжал в фото — оно вылезало из плашки.
      const ipl=el.querySelector('img.iplate'),iph=el.querySelector('img.iphoto');
      if(ipl){ipl.style.width='100%';ipl.style.height='100%';}
      if(iph){const pho=c.photo||{};
        iph.style.width=((pho.w||0)*m*k*z)+'px';iph.style.height=((pho.h||0)*m*k*z)+'px';
        iph.style.transform='translate(-50%,-50%) translate('+((pho.x||0)*m*k*z)+'px,'
                                                           +((pho.y||0)*m*k*z)+'px)';}
      el.style.opacity=op;
      const cp=(style==='cam1'&&!x.oncam2)?ipvCamChild(px+sx,py+sy,z):[px+sx,py+sy];
      el.style.transform='translate('+(cp[0]*k)+'px,'+(cp[1]*k)+'px)';
      return;}
    const im=el.querySelector('img');
    if(im){im.style.width=(c.pw*m*k*z)+'px';im.style.height=(c.ph*m*k*z)+'px';}
    el.style.opacity=op;
    // Задание ZG: свободные вставки (кам2 и «кам1 на кам2») идут БЕЗ ipvCamChild — у них
    // ни зума, ни сдвига, ни слежения Камеры 1 (в AE нулы «вставки кам2» и «вставки кам1
    // на кам2» не привязаны к её нулу). Через ipvCamChild они получали сдвиг `pan` и
    // слежение и уезжали вместе с кадром, хотя кадр в этот момент другой.
    const cc=(style==='cam1'&&!x.oncam2)?ipvCamChild(px+sx, py+sy, z):[px+sx, py+sy];
    el.style.transform='translate('+(cc[0]*k)+'px,'+(cc[1]*k)+'px)';
    return;}
  // размеров не прочиталось либо заглушка «файл не выбран» — точка покоя cam2/центр как раньше
  const isVid=(x.t==='video'||x.type==='video');
  const baseY=isVid?0:((pl&&pl.ins_c2y!=null?pl.ins_c2y:330)-H/2);
  const baseX=isVid?0:((pl&&pl.ins_c2x!=null?pl.ins_c2x:W/2)-W/2);
  el.style.transform='translate('+(((baseX+(x.x||0))+sx)*k)+'px,'+(((baseY+(x.y||0))+sy)*k)+'px)';}
function ipvMarks(){itlDraw();}   // после правок карточек перерисовываем таймлайн вставок
function ipvRefresh(){IPV.cur=-2;if(IPV.vids.length)ipvUI(ipvNow());   // перерисовать оверлей после правок карточек
  ipvPlanSoon();}   // правки вставок на шагах 2 и 3 видны после пересчёта плана

// ---- интро в предпросмотре (только ae-режим): окна групп из ПЛАНА ----
// ts/te считает scene_plan (xml2ae/build.py), превью их не досчитывает. introGroupWindows
// осталась прослойкой: панель шага 2/редактора плана не имеет и вызывает её с группами без
// ts/te — такие просто не дают окон (историческая копия формулы там отмирает сама).
function ipvIntroGroups(){const pl=IPV.plan;
  return introGroupWindows(pl&&pl.intro||[]);}
function introGroupWindows(ir){
  const groups=Array.isArray(ir)?ir:((ir&&ir.lines)||[]);   // панель шага 2 шлёт {lines,splits}
  return groups.filter(g=>g.ts!=null&&g.te!=null)
    .map(g=>({lines:g.lines,inAt:g.ts,outEnd:g.te,fade:g.fade,front:!!g.front,shadow:g.shadow,ys:g.ys,fonts:g.fonts,
      // Большое слева: левый край и множитель кегля каждой строки — те же
      // готовые числа, что уехали в .jsx (INTRO_LX/INTRO_LK). Это дверь: забытый тут
      // ключ — и превью рисует большую строку по-старому, хотя план её уже посчитал.
      lx:g.lx,lk:g.lk,
      // Множитель длительности появления на слово: [строка][слово], null —
      // слово успевает доиграть до начала затухания. Числа считает Python, превью своей
      // копии правила «когда слово успевает» не держит.
      sq:g.sq,
      // Точка масштабирования блока (intro_scale_anchor): Y якоря слоя прекомпа в пикселях
      // прекомпа — то же готовое число, что уехало в .jsx (INTRO_ANCHOR_Y). Это дверь:
      // забытый тут ключ — и блок в превью уменьшается от середины кадра, хотя в AE он уже
      // ужимается от текста (ключей стиля JS не читает вовсе). Поля нет (дефолт «центр
      // композиции») — origin снимаем, остаётся центр контейнера, как было.
      anchor_y:g.anchor_y}));}
// пересчёт интро на лету: правки в панели уходят в план (окна считает бэкенд), по затишью
// перезапрашиваем — полоски на таймлайне и оверлей в кадре догоняют за ~0.4с
function ipvIntroRefresh(){if(IPVMODE!=='ae')return;
  ipvPlanSoon();}
// PostScript-имя шрифта -> {family, var} с подключением файла шрифта через @font-face.
// В AE шрифт задаётся PostScript-именем. В браузере подключаем ровно тот же файл через
// @font-face (font-family:'reelsi-<PS>') и /api/fontfile/<PS>, чтобы начертание (Bold, Regular,
// Condensed) совпадало с AE побайтово. Вариативным шрифтам поверх задаются оси font-variation-settings.
// Обычным шрифтам вес и ширина не нужны — файл уже содержит правильное начертание.
// Шрифта нет в системе — текущий вид без изменений (font-weight:800), одна строка в лог.
const SFXFONTLOG={};
const IPV_FONT_FACES={};
function ensureFontFace(ps){
  if(!ps||IPV_FONT_FACES[ps])return;
  IPV_FONT_FACES[ps]=true;
  let st=document.getElementById('ipv-font-faces');
  if(!st){
    st=document.createElement('style');
    st.id='ipv-font-faces';
    document.head.appendChild(st);
  }
  const fam='reelsi-'+ps;
  const url='/api/fontfile/'+encodeURIComponent(ps);
  st.appendChild(document.createTextNode("@font-face { font-family: '"+fam.replace(/'/g,"\\'")+"'; src: url('"+url+"'); }\n"));
}
function ipvFontFor(ps){if(!ps)return null;
  const f=FONTS.find(f=>f.ps===ps);
  if(f&&(f.family||f.file)){
    ensureFontFace(ps);
    return {family:'reelsi-'+ps,var:f.var,ps:ps};
  }
  if(!SFXFONTLOG[ps]){SFXFONTLOG[ps]=1;if(typeof uiLog==='function')uiLog(t('шрифт не найден в системе: ')+ps);}
  return null;}
function ipvEase(u){
  if(u<=0)return 0;if(u>=1)return 1;
  const be=aeEase(35,90);
  const p=bezierT(be[0],be[2],u);
  return bezierY(be[0],be[2],p);
}
function ipvToHex(c,fb){
  if(typeof c==='string')return c;
  if(Array.isArray(c)&&c.length>=3){
    const h=x=>('0'+Math.round(Math.max(0,Math.min(1,x||0))*255).toString(16)).slice(-2);
    return '#'+h(c[0])+h(c[1])+h(c[2]);
  }
  return fb||'#ffffff';
}
function ipvParseCount(w,decHint){
  if(!w)return null;
  const str=String(w).trim();
  const m=str.match(/^([^\d]*?)(\d[\d\s]*(?:[\.,]\d+)?)([^\d]*)$/);
  if(!m)return null;
  const pfx=m[1]||'',numRaw=m[2]||'',sfx=m[3]||'';
  const hasComma=numRaw.includes(','),hasDot=numRaw.includes('.');
  if(hasComma&&hasDot)return null;
  const hasSpaces=numRaw.includes(' ');
  const clean=numRaw.replace(/\s+/g,'').replace(',','.');
  const val=parseFloat(clean);
  if(isNaN(val))return null;
  let dec=decHint;
  if(dec==null||isNaN(dec)){
    if(hasComma||hasDot){const frac=clean.split('.')[1]||'';dec=frac.length;}
    else{dec=0;}
  }
  return {val:val,dec:dec,hasComma:hasComma,hasSpaces:hasSpaces,pfx:pfx,sfx:sfx};
}
function ipvIntroDefAnims(){
  return {
    glitch:{
      dur:44/100,
      op_keys:[
        [0,0],
        [0.05,1],
        [0.1,1],
        [1417/10000,0],
        [0.1833,93/100],
        [0.225,0],
        [2667/10000,1]
      ]
    },
    reveal:{
      dur:44/100,
      blur:268/10,
      scale:0.7
    }
  };
}
function ipvIntroAnimParams(){
  const pl=(typeof IPV!=='undefined'&&IPV.plan)||null;
  const a=(pl&&pl.intro_anims)||{};
  const g=a.glitch||{};
  const r=a.reveal||{};
  const d=ipvIntroDefAnims();
  return {
    glitch:{
      dur:(g.dur!=null)?g.dur:d.glitch.dur,
      op_keys:g.op_keys||d.glitch.op_keys
    },
    reveal:{
      dur:(r.dur!=null)?r.dur:d.reveal.dur,
      blur:(r.blur!=null)?r.blur:d.reveal.blur,
      scale:(r.scale!=null)?r.scale:d.reveal.scale
    }
  };
}
function ipvGlitchOp(dt,kIn){
  let k=kIn;
  if(!k){
    if(typeof ipvIntroAnimParams==='function'){
      k=ipvIntroAnimParams().glitch.op_keys;
    }else if(typeof ipvIntroDefAnims==='function'){
      k=ipvIntroDefAnims().glitch.op_keys;
    }
  }
  if(!k||!k.length)return (dt<=0?0:1);
  const lastT=k[k.length-1][0];
  const lastV=k[k.length-1][1]>1?k[k.length-1][1]/100:k[k.length-1][1];
  const firstT=k[0][0];
  const firstV=k[0][1]>1?k[0][1]/100:k[0][1];
  if(dt>=lastT)return lastV;
  if(dt<=firstT)return firstV;
  for(let i=0;i<k.length-1;i++){
    if(dt>=k[i][0]&&dt<=k[i+1][0]){
      const v0=k[i][1]>1?k[i][1]/100:k[i][1];
      const v1=k[i+1][1]>1?k[i+1][1]/100:k[i+1][1];
      const u=(dt-k[i][0])/(k[i+1][0]-k[i][0]);
      return v0+(v1-v0)*u;
    }
  }
  return 1;
}
function ipvRenderChars(sp,text,fn,disp){
  if(sp._chText!==text){
    sp._chText=text;
    sp.innerHTML=text.split('').map((ch,ci)=>{
      const isSpace=(ch===' ');
      return '<span class="ich" style="display:'+(disp||'inline-block')+';'+(isSpace?'white-space:pre;':'')+'">'+(isSpace?' ':(typeof esc==='function'?esc(ch):ch))+'</span>';
    }).join('');
  }
  const chs=sp.children;
  for(let i=0;i<chs.length;i++){
    fn(chs[i],i,chs.length);
  }
}
function ipvIntro(tm){const io=$('ipvintro');if(!io)return;
  if(IPVMODE!=='ae'||!IPV.intro.length){io.style.display='none';IPV.introCur=-1;introMarkPlaying(-1);return;}
  let gi=-1;
  for(let k=0;k<IPV.intro.length;k++){const g=IPV.intro[k];if(tm>=g.inAt&&tm<g.outEnd)gi=k;}
  introMarkPlaying(gi);   // та же группа подсвечивается в панелях интро
  if(gi!==IPV.introCur){IPV.introCur=gi;io.innerHTML='';
    // 'flex', а не '': сброс инлайнового стиля возвращает правило .ipvintro{display:none}
    // из CSS, и интро не показывалось НИКОГДА (первая сверка с AE, 2026-08-11)
    io.style.display=(gi<0)?'none':'flex';
    if(gi>=0){
      // кегль ИЗ ПЛАНА, как у субтитров: в AE интро и субтитры — один FONT_SIZE.
      // Но при нескольких словах в строке автофит ужимает FONT_SIZE субтитров, а интро
      // остаётся неужатым (доработка ZL) — берём intro_fsize; старого поля нет (превью
      // со старым бэкендом) — падаем на fsize, как было.
      const pl=IPV.plan;
      const ifs=pl&&(pl.intro_fsize||pl.fsize);
      if(ifs)io.style.setProperty('--introsfs',(ifs/(pl.w||1080)*100).toFixed(3)+'cqw');
      const s=(typeof CURSTYLE!=='undefined'&&CURSTYLE)?CURSTYLE:{};
      const lines=IPV.intro[gi].lines||[];
      // Строки группы сажаем базовой линией по готовым y из плана: шаг строк
      // зависит от шрифта и якоря, CSS-потоком (flex + line-height + ручные marginTop) его
      // не повторить — превью врало о высоте. ys той же длины, что строки — раскладываем
      // абсолютно; нет ys (старый бэкенд без перезапуска) — сегодняшний поток как есть.
      const yl=IPV.intro[gi].ys;
      const ys=(Array.isArray(yl)&&yl.length===lines.length)?yl:null;
      // Большое слева: левый край строки и множитель её кегля — готовые
      // числа плана (lx/lk группы). Своих чисел превью не считает: раскладку знает
      // Python. Группа с lx раскладывается абсолютно ВСЕГДА — по центру потока такую
      // строку не поставить.
      const xl=IPV.intro[gi].lx;
      const lxs=(Array.isArray(xl)&&xl.length===lines.length)?xl:null;
      const kll=IPV.intro[gi].lk;
      const kls=(Array.isArray(kll)&&kll.length===lines.length)?kll:null;
      // Множитель длительности появления: готовые числа плана
      // ([строка][слово]); null/нет поля — слово успевает доиграть, множитель 1.
      const sql=IPV.intro[gi].sq;
      const sqs=(Array.isArray(sql)&&sql.length===lines.length)?sql:null;
      // Глитч в группе — то же условие, что grpGlitch в .jsx: жёлтое слово группы с
      // глитчем свечения хайлайта (introHlGlow) не получает. Считается один раз на
      // перестроение группы, а не на строку.
      const grpGlitch=lines.some(x=>x.anim==='glitch');
      lines.forEach((l,li)=>{const dv=document.createElement('div');
      // Цвет строки: yellow -> intro_hl_fill / hl_fill, accent -> hl_fill3, custom -> l.fill, white -> intro_fill
      let col='#ffffff';
      if(l.color==='yellow'){
        col=ipvToHex((pl&&pl.intro_hl_fill)||(s&&s.intro_hl_fill)||(pl&&pl.hl_fill)||(s&&s.hl_fill),'var(--introhl,var(--subhl,var(--yel)))');
      }else if(l.color==='accent'){
        col=ipvToHex((pl&&pl.hl_fill3)||(s&&s.hl_fill3),'var(--subhl3,#af1f1f)');
      }else if(l.color==='custom'&&l.fill){
        col=ipvToHex(l.fill,'#ffffff');
      }else{
        col=ipvToHex((pl&&pl.intro_fill)||(s&&s.intro_fill),'#ffffff');
      }
      const lx=(lxs&&lxs[li]!=null)?lxs[li]:null;
      const lk=(kls&&kls[li]!=null)?kls[li]:null;
      dv.className='iline'+(l.color==='yellow'?' yel':'')+(l.back?' back':'')+(l.color==='accent'?' accent':'')+((ys||lxs)?' abs':'');
      dv.style.color=col;
      // Свечение и тень СЛОВА: галки и числа — из плана (plan.intro_word_fx),
      // второго чтения ключей стиля во фронте нет. Свечение: глитч-строке — по галке
      // «свечение слов с глитчем», строке fx=='glow' — по «свечение слов со свечением
      // строки», жёлтому слову хайлайта — по «свечению жёлтого хайлайта» (та же третья
      // дверь, что introHlGlow в .jsx: у группы с глитчем и у строки со свечением её нет).
      // Тень (Drop Shadow) — те же условия, что в .jsx: галка «тень на всех
      // словах» либо своя галка глитча/заднего плана.
      const wfx=(pl&&pl.intro_word_fx)||{};
      const glowOn=(l.anim==='glitch')?(wfx.glow_glitch!==false)
                  :((l.fx==='glow')?(wfx.glow_fx!==false)
                  :((l.color==='yellow'&&!grpGlitch)?(wfx.glow_hl!==false):false));
      // Общее свечение блока — аналог Glo2 на слое ПРЕКОМПА группы (четвёртая дверь):
      // светится весь блок целиком, а не отдельное слово.
      const compGlowOn=(wfx.glow_comp!==false);
      const wshOn=(wfx.shadow_all===true)
                 ||(l.anim==='glitch'&&wfx.shadow_glitch!==false)
                 ||(!!l.back&&wfx.shadow_back!==false);
      const tsh=[];
      if(glowOn){
        // Приближение Glo2 двумя гало, как и было (12 и 24 px при дефолтных числах).
        // Множитель k: Glo2 Radius и Intensity растят свечение, Threshold (порог, выше
        // которого буква светится) — уменьшает; CSS-аналога порогу нет, поэтому он здесь
        // так. При дефолтах 77/0.62/149 k=1 — превью выглядит как раньше.
        const gThr=(wfx.glow_thr!=null?+wfx.glow_thr:149);
        const gRad=(wfx.glow_rad!=null?+wfx.glow_rad:77);
        const gInt=(wfx.glow_int!=null?+wfx.glow_int:0.62);
        const gk=Math.max(0,(gRad/77)*(gInt/0.62)*((255-gThr)/106));
        if(gk>0){
          const b1=Math.round(12*gk*10)/10, b2=Math.round(24*gk*10)/10;
          tsh.push('0 0 '+b1+'px '+col,'0 0 '+b2+'px '+col);
        }
      }
      // Общее свечение блока (Glo2 на прекомпе, радиус 42): мягкая широкая тень ЦВЕТОМ
      // строки — CSS-аналог свечения всего блока, а не буквы.
      if(compGlowOn)tsh.push('0 0 21px '+col);
      if(wshOn)tsh.push('0 2px 10px rgba(0,0,0,'+(glowOn?'.85':'.75')+')');
      dv.style.textShadow=tsh.join(', ');
      // Масштаб и межстрочный интервал для мелкого текста (back). В ветке ys
      // ручные marginTop не нужны: вертикаль строки целиком задаёт y из плана.
      // Большая строка кегль берёт из плана (lk) — back-скейл её не касается,
      // lk его заменяет, как и в .jsx.
      if(lk!=null){
        dv.style.fontSize='calc(var(--introsfs,8.4cqw) * '+lk+')';
      }else if(l.back){
        const bsc=(pl&&pl.back_scale!=null)?pl.back_scale:(s.back_scale!=null?s.back_scale:0.69);
        dv.style.fontSize='calc(var(--introsfs,8.4cqw) * '+bsc+')';
        dv.style.lineHeight='1.15';
        if(!ys&&li>0&&!lines[li-1].back){dv.style.marginTop='-0.3em';}
      }else if(!ys&&li>0&&lines[li-1].back){
        dv.style.marginTop='-0.15em';
      }
      // Шрифт строки — из ПЛАНА: plan.intro[].fonts считает Python
      // ровно той же лесенкой, что уходит в .jsx (accent_font -> жёлтая intro_hl_font ->
      // intro_font). Своей лесенки превью не держит: жёлтые строки без intro_hl_font/
      // intro_font оставались без шрифта и рисовались системным с fontWeight 800 —
      // на 41 % шире, чем в AE. Запасная ветка — только для бэкенда, который ещё не
      // перезапущен после обновления (в плане нет fonts); она повторяет build.py:473-476.
      const gf=IPV.intro[gi].fonts;
      const ps=(gf&&gf[li])||l.accent_font||(l.color==='yellow'?(s.intro_hl_font||s.hl_font||s.font):(s.intro_font||s.font))||'SFPro-CondensedSemibold';
      const fv=ps?ipvFontFor(ps):null;
      if(fv){dv.style.fontFamily="'"+fv.family+"'";
        if(fv.var)dv.style.fontVariationSettings=Object.entries(fv.var)
          .map(([a,v])=>'"'+a+'" '+v).join(',');
        else{
          dv.style.fontVariationSettings='';
        }}
      else{dv.style.fontWeight='800';}
      (l.words||[]).forEach((w,wi)=>{const sp=document.createElement('span');sp.className='iword';sp.textContent=w;
        sp.dataset.origWord=w;
        sp.dataset.t=(l.times&&l.times[wi]!=null)?l.times[wi]:0;
        sp.dataset.anim=l.anim||'';
        sp.dataset.fx=l.fx||'';
        // Сжатие появления: множитель длительности у ЭТОГО слова — из плана.
        // Больше нуля и меньше единицы — слово играет появление короче (числа те же, что
        // уехали в INTRO_SQ для AE); нет множителя — анимация прежняя.
        const sqw=(sqs&&sqs[li]&&sqs[li][wi]!=null)?sqs[li][wi]:0;
        if(sqw>0&&sqw<1)sp.dataset.sq=sqw;
        // Счётчиков в строке может быть НЕСКОЛЬКО (cnts: [[позиция, цель, выражение, dec], ...]):
        // каждое слово считает от своего sp.dataset.t. Нет cnts — старое поведение по
        // is_count/cnt_idx (бэкенд, который ещё не перезапущен после обновления).
        let isCw=false,pwc=null,cwc=null;
        if(Array.isArray(l.cnts)){
          for(let ci=0;ci<l.cnts.length;ci++){if(l.cnts[ci][0]===wi){cwc=l.cnts[ci];break;}}
          isCw=!!cwc;
          if(cwc)pwc=ipvParseCount(w,cwc[3]);
        }else if(l.is_count){
          if(l.cnt_idx!=null){if(wi===l.cnt_idx)isCw=true;}
          else{pwc=ipvParseCount(w,l.dec);if(pwc)isCw=true;}
        }
        if(isCw){
          if(!pwc)pwc=ipvParseCount(w,l.dec);
          sp.dataset.isCount='1';
          sp.dataset.cntTarget=(cwc&&cwc[1]!=null)?cwc[1]:((l.cnt!=null)?l.cnt:(pwc?pwc.val:0));
          sp.dataset.cntDec=(cwc&&cwc[3]!=null)?cwc[3]:((l.dec!=null)?l.dec:(pwc?pwc.dec:0));
          sp.dataset.cntComma=(pwc&&pwc.hasComma)?'1':'0';
          sp.dataset.cntSpaces=(pwc&&pwc.hasSpaces)?'1':'0';
          sp.dataset.cntPrefix=(pwc&&pwc.pfx)||'';
          sp.dataset.cntSuffix=(pwc&&pwc.sfx)||'';
        }
        if(wi>0)dv.appendChild(document.createTextNode(' '));   // межсловный отступ — настоящий пробел шрифта, как в AE
        dv.appendChild(sp);});
      if(gi>=0&&IPV.intro[gi].lines[IPV.intro[gi].lines.length-1]===l){
        const h=document.createElement('i');h.className='intro-scale-handle';h.dataset.t=t('Изменить масштаб интро (двойной клик — вернуть авто)');dv.appendChild(h);}
      io.appendChild(dv);});
      // Посадка строк по y из плана: базовая линия строки — на её y из плана,
      // пересчитанном от центра контейнера (y из плана отсчитан от центра прекомпа высотой
      // plan.h). k — CSS-пиксели на пиксель прекомпа, тот же, что у прочих пересчётов превью.
      // Замер смещения базовой линии — ОДИН раз на построение группы (меняется IPV.introCur),
      // а не на каждом кадре: замер гонит layout, в покадровом рендере ему не место.
      // Приём тот же, что у подписи в ipvCaption: пустой inline-block нулевой высоты стоит
      // НА базовой линии (стили — .ipvintro .iline.abs .pvcap_strut в app.css).
      // Смещение берём из вёрстки (offsetTop/offsetHeight), а НЕ из getBoundingClientRect:
      // у #ipvintro есть transform: scale(...) (масштаб группы × зум камеры, ставит
      // ipvIntroPos), прямоугольник приходит в ЭКРАННЫХ пикселях, а style.top — в
      // нетрансформированных. baseOff ошибался ровно в «масштаб блока» раз, у каждой группы
      // своим (снято замером: до −51 px на группе с 0.941), да ещё от transform ПРЕДЫДУЩЕЙ
      // группы при построении — ошибка «прыгала». offsetTop опоры от transform не зависит:
      // её offsetParent — сама строка (.iline.abs — position:absolute).
      if(ys||lxs){
        const pw=(pl&&pl.w)||1080,ph=(pl&&pl.h)||1920;
        const k=(io.clientWidth||pw)/pw;
        const cy=(io.clientHeight||0)/2;
        const rows=io.querySelectorAll('.iline');
        for(let li=0;li<rows.length;li++){
          const dv=rows[li];
          // Левый край строки большого блока: центр контейнера + lx в px
          // превью (тот же коэффициент k, что у вертикали ниже). Текст идёт вправо от
          // этого края, поэтому translateX(-50%) из .iline.abs снимаем.
          if(lxs&&lxs[li]!=null){
            dv.style.left=(((io.clientWidth||pw)/2+lxs[li]*k)).toFixed(2)+'px';
            dv.style.transform='none';
          }
          if(!ys||li>=ys.length)continue;
          const strut=document.createElement('i');
          strut.className='pvcap_strut';
          dv.appendChild(strut);
          const baseOff=strut.offsetTop+strut.offsetHeight;
          strut.remove();
          dv.style.top=(cy+(ys[li]-ph/2)*k-baseOff).toFixed(2)+'px';
        }
      }}}
  if(gi>=0){
    const g=IPV.intro[gi];
    // Слой интро. Обычно — та же формула, что getZ в ipvSubs (порядок из плана).
    // Группа, попавшая по времени на видеовставку (front), поднимается на 11: это выше любого
    // слоя layer_order (максимум там 10) — ровно то, что делает AE, унося такой прекомп
    // moveToBeginning (template.py, intro_front_raise). Без признака превью всегда ставило
    // интро по layer_order и держало его ПОД видео: текст был закрыт картинкой и не хватался
    // мышью, хотя в AE лежал сверху.
    const pl=IPV.plan;
    const order=(pl&&pl.layer_order)||['subs','video','roto','photo','intro'];
    const i=order.indexOf('intro');
    const zIntro=i>=0?(10-i):0;
    io.style.zIndex=g.front?'11':String(zIntro);
    // Тень прекомпа интро: цвет и непрозрачность — из плана, у группы своя
    // камера (plan.intro[].shadow). Направление, дистанция и мягкость — числа
    // плана, общие у обеих камер (plan.intro_comp_shadow), их же печатает introCompShadow
    // в .jsx. Приближение AE Drop Shadow: смещение X = dist·cos(dir), Y = dist·sin(dir)
    // (Y в кадре вниз), мягкость -> CSS blur = soft/2, как и было при 0/287 по умолчанию.
    // Фильтр ставится ДО transform блока, поэтому масштаб группы (ds, зум камеры)
    // учитывается сам. Нет shadow (старый бэкенд без перезапуска) — фильтр пустой.
    const sh=g.shadow;
    if(sh&&sh.fill){
      const k=(io.clientWidth||(pl?pl.w:1080))/(pl?pl.w:1080);
      const cs=(pl&&pl.intro_comp_shadow)||{};
      const cSoft=(cs.soft!=null?+cs.soft:287), cDist=(cs.dist!=null?+cs.dist:0);
      const cRad=(cs.dir!=null?+cs.dir:135)*Math.PI/180;
      const R=(cSoft*k*0.5).toFixed(1);
      const dx=(cDist*Math.cos(cRad)*k).toFixed(1), dy=(cDist*Math.sin(cRad)*k).toFixed(1);
      const c=sh.fill.map(v=>Math.round(Math.max(0,Math.min(1,v||0))*255));
      io.style.filter='drop-shadow('+dx+'px '+dy+'px '+R+'px rgba('+c[0]+','+c[1]+','+c[2]+','+((sh.op||0)/255).toFixed(3)+'))';
    }else{io.style.filter='';}
    // Общий фейд-аут группы интро: длительность спада (fade) приходит в плане из
    // scene_plan — у прекомпов с глитчем она короче (0.45/0.35 вместо обычной 0.75),
    // вторая копия чисел в JS не заводится.
    const dtOut=(g&&g.outEnd!=null)?(g.outEnd-tm):1;
    const fadeS=(g&&g.fade!=null)?g.fade:0.75;
    if(dtOut<fadeS&&dtOut>=0){io.style.opacity=ipvEase(Math.max(0,dtOut/fadeS)).toFixed(3);}
    else if(dtOut<0){io.style.opacity='0';}
    else{io.style.opacity='1';}
    // Покадровая анимация каждого слова:
    io.querySelectorAll('.iline > span').forEach((sp,spi)=>{
      const tw=+sp.dataset.t;
      const dt=tm-tw;
      const anim=sp.dataset.anim||'';
      const isCount=(sp.dataset.isCount==='1');
      // Множитель сжатия появления этого слова: длительность анимации
      // множится на него — ровно так же, как ключи в AE (INTRO_SQ в .jsx).
      const sq=(+sp.dataset.sq>0&&+sp.dataset.sq<1)?+sp.dataset.sq:1;
      if(dt<0){
        sp._chText=null;
        sp.classList.remove('on');
        sp.style.opacity='0';
        if(anim==='up')sp.style.transform='translateY(100%)';
        else if(anim==='left')sp.style.transform='translateX(-100%)';
        else if(anim==='right')sp.style.transform='translateX(100%)';
        else if(anim==='reveal'){
          const rP=ipvIntroAnimParams().reveal;
          const rSc=(rP.scale!=null)?rP.scale:0.7;
          const rBl=(rP.blur!=null)?rP.blur:0;
          sp.style.transform='scale('+rSc+')';
          sp.style.filter=(rBl>0)?('blur('+rBl+'px)'):'';
        }
        else{sp.style.transform='';sp.style.filter='';}
        if(isCount){
          const dec=+sp.dataset.cntDec||0;
          let s0=(0).toFixed(dec);
          if(sp.dataset.cntComma==='1')s0=s0.replace('.',',');
          sp.textContent=(sp.dataset.cntPrefix||'')+s0+(sp.dataset.cntSuffix||'');
        }else{sp.textContent=sp.dataset.origWord;}
        return;
      }
      sp.classList.add('on');
      // Расчёт числового значения счётчика (isCount) от 0 до cntTarget за HL_DUR=1.5с:
      let curText=sp.dataset.origWord;
      if(isCount){
        const uCnt=Math.min(1,Math.max(0,dt/(1.5*sq)));
        const qCnt=ipvEase(uCnt);
        const curVal=(+sp.dataset.cntTarget)*qCnt;
        const dec=+sp.dataset.cntDec||0;
        let sNum=curVal.toFixed(dec);
        if(sp.dataset.cntComma==='1')sNum=sNum.replace('.',',');
        if(sp.dataset.cntSpaces==='1'){
          const p=sNum.split(sp.dataset.cntComma==='1'?',':'.');
          p[0]=p[0].replace(/\B(?=(\d{3})+(?!\d))/g,' ');
          sNum=p.join(sp.dataset.cntComma==='1'?',':'.');
        }
        curText=(sp.dataset.cntPrefix||'')+sNum+(sp.dataset.cntSuffix||'');
        if(uCnt>=1&&sp.dataset.origWord){curText=sp.dataset.origWord;}
      }
      // Эффекты появления слова:
      const anP=ipvIntroAnimParams();
      if(anim==='glitch'){
        const gP=anP.glitch||{};
        const gDur=(gP.dur!=null)?gP.dur:0;
        if(gDur>0&&dt<gDur*sq){
          sp.style.opacity=ipvGlitchOp(dt/sq).toFixed(3);
          const jx=(Math.sin(dt*150+spi)*2.0).toFixed(1);
          const jy=(Math.cos(dt*200+spi)*1.0).toFixed(1);
          sp.style.transform='translate('+jx+'px,'+jy+'px)';
          sp.style.filter='';
          const fTick=Math.floor(dt*30);
          ipvRenderChars(sp,curText,(chEl,ci)=>{
            const hash=Math.abs(Math.sin(fTick*12.9898+ci*78.233+10)*43758.5453)%1;
            const pVis=Math.min(1,Math.max(0.2,dt/(gDur*sq)));
            chEl.style.opacity=(hash<pVis)?'1':'0';
          },'inline');
        }else{
          sp._chText=null;
          sp.style.opacity='1';sp.style.transform='';sp.style.filter='';sp.textContent=curText;
        }
      }else if(anim==='reveal'){
        const rP=anP.reveal||{};
        const rDur=(rP.dur!=null)?rP.dur:0;
        if(rDur>0&&dt<rDur*sq){
          const uL=Math.min(1,Math.max(0,dt/(rDur*sq)));
          const qL=ipvEase(uL);
          sp.style.opacity=qL.toFixed(3);
          const rBl=(rP.blur!=null)?rP.blur:0;
          const bl=((1-qL)*rBl).toFixed(1);
          sp.style.filter=(1-qL>0.01&&rBl>0)?('blur('+bl+'px)'):'';
          const rSc=(rP.scale!=null)?rP.scale:0.7;
          sp.style.transform='scale('+(rSc+(1-rSc)*qL).toFixed(3)+')';
          ipvRenderChars(sp,curText,(chEl,ci,n)=>{
            const p=(n>1)?(ci/(n-1)):0.5;
            const tStart=p*0.55;
            const uC=Math.min(1,Math.max(0,(uL-tStart)/0.45));
            const qC=ipvEase(uC);
            chEl.style.transform='scale('+(0.1+0.9*qC).toFixed(3)+')';
            chEl.style.opacity=Math.max(0.15,qC).toFixed(3);
          });
        }else{
          sp._chText=null;
          sp.style.opacity='1';sp.style.transform='';sp.style.filter='';sp.textContent=curText;
        }
      }else if(anim==='up'){
        sp._chText=null;
        const u=Math.min(1,Math.max(0,dt/(0.3*sq)));
        const q=ipvEase(u);
        sp.textContent=curText;
        sp.style.transform='translateY('+((1-q)*100).toFixed(1)+'%)';
        sp.style.opacity=q.toFixed(3);
        sp.style.filter='';
      }else if(anim==='left'){
        sp._chText=null;
        const u=Math.min(1,Math.max(0,dt/(0.3*sq)));
        const q=ipvEase(u);
        sp.textContent=curText;
        sp.style.transform='translateX('+((q-1)*100).toFixed(1)+'%)';
        sp.style.opacity=q.toFixed(3);
        sp.style.filter='';
      }else if(anim==='right'){
        sp._chText=null;
        const u=Math.min(1,Math.max(0,dt/(0.3*sq)));
        const q=ipvEase(u);
        sp.textContent=curText;
        sp.style.transform='translateX('+((1-q)*100).toFixed(1)+'%)';
        sp.style.opacity=q.toFixed(3);
        sp.style.filter='';
      }else{
        sp._chText=null;
        sp.textContent=curText;
        const dur=(isCount?1.5:0.3)*sq;
        const u=Math.min(1,Math.max(0,dt/dur));
        const q=ipvEase(u);
        sp.style.opacity=q.toFixed(3);
        sp.style.transform='';
        sp.style.filter='';
      }
    });
  }
  if(gi>=0)ipvIntroPos(gi,tm);}   // позиция блока из плана (dx/dy группы) — и после рефетча плана
// позиция интро-блока в кадре из плана: dx/dy группы — comp-пиксели, на экран
// через k (стойка/plan.w). Общий сдвиг нула и INTRO_Y в AE «вшиты» в CSS-позицию блока.
function ipvIntroPos(gi,tm){const io=$('ipvintro');if(!io)return;
  const pl=IPV.plan,g=pl&&pl.intro[gi];
  const k=(io.clientWidth||(pl?pl.w:1080))/(pl?pl.w:1080);
  // Интро висит на нуле «интро», привязанном к нулу Камеры 1: в AE оно наследует зум —
  // едет и МАСШТАБИРУЕТСЯ на s. Базовая позиция блока живёт
  // В ПЛАНЕ (plan.intro[].y, от центра кадра), а не в CSS: превью рисует её из плана,
  // и она идёт в ipvCamChild как часть p — база едет и масштабируется вместе со всем.
  // Смещение группы g.dy складывается поверх (его правит драг в кэше плана).
  // Общий масштаб интро: в AE он на нуле «интро», родителе прекомпа, поэтому
  // множит и СМЕЩЕНИЕ группы (dx/dy), и размер (ds) — а база y уже включает G (считает
  // scene_plan). Поля в плане нет (старый ответ) = 100%, как сегодня.
  // Галка «интро едет с камерой» снята: зум и сдвиг камеры к блоку не
  // применяются вовсе — точка и зум приходят из ipvIntroChild (там же затемнение).
  const G=((pl&&pl.intro_scale!=null)?pl.intro_scale:100)/100;
  const cc=ipvIntroChild(G*(g?g.dx||0:0), (g?g.y||0:0)+G*(g?g.dy||0:0), tm!=null?tm:ipvNow());
  // Точка масштабирования блока (intro_scale_anchor): в AE якорь слоя прекомпа стоит на Y
  // строки блока (INTRO_ANCHOR_Y), а Position приезжает уже компенсированным — блок
  // уменьшается ОТ СВОЕГО ТЕКСТА, а не подтягивается к середине кадра. В превью то же
  // делает transform-origin в той же точке: проценты от высоты контейнера (#ipvintro
  // растянут на кадр, как posy/H у субтитров), k тут не нужен. Число — из плана, второго
  // чтения ключей стиля во фронте нет; поля нет (дефолт «центр композиции») — origin
  // снимаем, и блок масштабируется от центра контейнера, как раньше.
  const ay=(g&&g.anchor_y!=null)?g.anchor_y:null;
  io.style.transformOrigin=(ay!=null)?('50% '+(ay/((pl&&pl.h)||1920)*100).toFixed(3)+'%'):'';
  io.style.transform='translate('+(cc[0]*k)+'px,'+(cc[1]*k)+'px) scale('+(0.968*G*((g&&g.ds!=null?g.ds:100)/100)*cc[2])+')';}
// головная строка gi-й группы (в том же порядке, что группы плана: обе по таймингу).
// Считаем как introWalk: головой с count=0 группа НЕ начинается (в план такая не попала),
// иначе индекс разъезжался бы с группами плана на пустых строках.
function introHeadIdx(gi){let off=0,c=0;for(let i=0;i<INTRO.length;i++){
  const r=INTRO[i];if(r.from!=null&&r.from>=0&&r.from<WORDS.length)off=r.from;
  const head=(i===0||r.break||r.from!=null);
  if(head&&(r.count|0)>0&&off<WORDS.length){if(c===gi)return i;c++;}
  off=Math.min(WORDS.length,off+Math.max(0,r.count|0));}
  return -1;}

// ---- таймлайн вставок (как редактор нарезки): блоки двигаются, края тянутся, колесо = зум ----
let ITL={pps:0};
function itlFitPps(){const el=$('itl');return (el&&IPV.dur)?Math.max(1,el.clientWidth-2)/IPV.dur:10;}
function itlFit(){ITL.pps=itlFitPps();itlDraw();const el=$('itl');if(el)el.scrollLeft=0;}
function itlZoom(f,px){const el=$('itl');if(!el||!IPV.dur)return;
  if(!ITL.pps)ITL.pps=itlFitPps();
  const cx=(px!=null)?px:el.clientWidth/2;
  const tm=(el.scrollLeft+cx)/ITL.pps;                       // время под курсором держим на месте
  ITL.pps=Math.max(itlFitPps(),Math.min(80,ITL.pps*f));
  itlDraw();el.scrollLeft=Math.max(0,tm*ITL.pps-cx);}
function itlDraw(){const el=$('itl'),inn=$('itlin');if(!el||!inn)return;
  if(!IPV.dur){inn.style.width='100%';$('itlblocks').innerHTML='';$('itlruler').innerHTML='';itlPh(0);return;}
  if(!ITL.pps)ITL.pps=itlFitPps();
  inn.style.width=Math.ceil(IPV.dur*ITL.pps)+'px';
  const steps=[1,2,5,10,15,30,60,120];const st=steps.find(s=>s*ITL.pps>=70)||120;
  let R='';for(let tm=0;tm<=IPV.dur;tm+=st)R+='<i style="left:'+(tm*ITL.pps)+'px">'+fmtT(tm)+'</i>';
  $('itlruler').innerHTML=R;
  const bh=$('itlblocks');bh.innerHTML='';
  const ae=(IPVMODE==='ae');
  if(ae&&IPV.intro.length)IPV.intro.forEach((g,k)=>{   // интро-полоски (не таскаются) — видно перекрытия
    const b=document.createElement('div');b.className='itlblk intro';
    b.style.left=(g.inAt*ITL.pps)+'px';b.style.width=Math.max(8,(g.outEnd-g.inAt)*ITL.pps)+'px';
    b.dataset.t=t('интро {n}: {a} — {b}',{n:k+1,a:fmtIns(g.inAt),b:fmtIns(g.outEnd)});
    b.innerHTML='<span class="lbl">'+t('интро ')+(k+1)+'</span>';bh.appendChild(b);});
  if(ae&&IPV.plan&&(IPV.plan.roto||[]).length)   // полоса «здесь рото включено» по плану (без масок)
    IPV.plan.roto.forEach((p,ri)=>{const b=document.createElement('div');b.className='itlblk roto';
      b.style.left=(p.ts*ITL.pps)+'px';b.style.width=Math.max(8,(p.te-p.ts)*ITL.pps)+'px';
      b.dataset.t=t('рото {n}: {a} — {b}',{n:ri+1,a:fmtIns(p.ts),b:fmtIns(p.te)});
      // СМЫСЛ полосы — справка: без подписи оранжевая полоска непонятна (жалоба
      // 2026-08-12). data-t показывает #tipbox по наведению.
      b.dataset.t=t('оранжевая полоса — здесь включён ротоскоп: человек отделяется от фона маской');bh.appendChild(b);});
  ipvIns().forEach((x,i)=>{const sd=insSD(x);const s=sd.s,d=sd.d;
    const b=document.createElement('div');b.id='itlb'+i;
    // зелёный блок = файл выбран осознанно (как и зелёная карточка): автоподбор
    // и генерация остаются в цвете своего типа — фото синий, видео янтарный
    const picked=x.media&&!x.libAuto&&!x.genAuto;
    b.className='itlblk '+(ae?(x.type==='video'?'video':'photo'):(picked?'chosen':(x.type==='video'?'video':'photo')))+(ae?' lo':'');
    b.style.left=(s*ITL.pps)+'px';b.style.width=Math.max(8,d*ITL.pps)+'px';
    b.dataset.t=fmtIns(s)+' — '+fmtIns(s+d)+' ('+(Math.round(d*10)/10)+t('с')+') · '+insLbl(x);
    b.innerHTML='<span class="eh l"></span><span class="lbl">'+esc(insLbl(x)||(x.type==='video'?t('видео'):t('фото')))+'</span><span class="eh r"></span>';
    b.addEventListener('pointerdown',e=>{const eh=e.target.classList&&e.target.classList.contains('eh');
      itlBlockDown(e,i,eh?(e.target.classList.contains('l')?'l':'r'):'move');});
    b.addEventListener('dblclick',e=>{e.preventDefault();ipvJump(i);});
    bh.appendChild(b);});
  itlPh(IPV.vids.length?ipvNow():0);}
function itlPh(tm){const p=$('itlph');if(p)p.style.left=((tm||0)*ITL.pps)+'px';}
function itlEnsure(tm){const el=$('itl');if(!el||!IPV.dur||ITL.scrub)return;const x=tm*ITL.pps;
  if(x<el.scrollLeft+10||x>el.scrollLeft+el.clientWidth-10)el.scrollLeft=Math.max(0,x-el.clientWidth/3);}
function itlBlockDown(e,i,mode){const x=ipvIns()[i];if(!x)return;
  e.preventDefault();e.stopPropagation();
  const sd0=insSD(x);const st={x0:e.clientX,s:sd0.s,d:sd0.d};
  let moved=false;
  // блок ищем на каждом кадре, а не держим ссылку: снятие фокуса с поля «что искать»
  // (см. глобальный pointerdown) может дёрнуть перерисовку списка прямо посреди перетаскивания
  const move=ev=>{const blk=$('itlb'+i);if(!blk)return;
    const dt=(ev.clientX-st.x0)/ITL.pps;if(Math.abs(ev.clientX-st.x0)>2)moved=true;
    let s=st.s,d=st.d;
    if(mode==='move')s=Math.min(Math.max(0,st.s+dt),Math.max(0,IPV.dur-st.d));
    else if(mode==='l'){const end=st.s+st.d;s=Math.min(Math.max(0,st.s+dt),end-0.5);d=end-s;}
    else d=Math.min(Math.max(0.5,st.d+dt),Math.max(0.5,IPV.dur-st.s));
    s=Math.round(s*10)/10;d=Math.round(d*10)/10;
    insSetSD(x,s,d);
    blk.style.left=(s*ITL.pps)+'px';blk.style.width=Math.max(8,d*ITL.pps)+'px';
    blk.dataset.t=fmtIns(s)+' — '+fmtIns(s+d)+' ('+d+t('с')+') · '+insLbl(x);};
  const up=()=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);
    ipvAfterEdit();
    if(moved&&IPV.vids.length&&!IPV.playing)ipvSeekTo(insSD(x).s+0.01);};   // показать, куда легло
  window.addEventListener('pointermove',move);window.addEventListener('pointerup',up);}
// клик/протяжка по фону таймлайна = перемотка за курсором (плейхед таскается); колесо = зум (Shift = прокрутка)
// .roto исключён из «занимаемых блоков»: полоса рото теперь hoverable (для тултипа), но
// клик по ней должен перематывать, как по фону — иначе тонкая полоса съест перемотку.
$('itlin').addEventListener('pointerdown',e=>{if(e.target.closest('.itlblk:not(.intro):not(.roto)'))return;
  if(!IPV.vids.length||!ITL.pps)return;e.preventDefault();
  const was=IPV.playing;ipvPause();ITL.scrub=true;
  const to=ev=>{const r=$('itlin').getBoundingClientRect();ipvSeekTo((ev.clientX-r.left)/ITL.pps);};
  to(e);
  const up=()=>{window.removeEventListener('pointermove',to);window.removeEventListener('pointerup',up);
    ITL.scrub=false;if(was)ipvPlay();};
  window.addEventListener('pointermove',to);window.addEventListener('pointerup',up);});
$('itl').addEventListener('wheel',e=>{e.preventDefault();const el=$('itl');
  if(e.shiftKey){el.scrollLeft+=(e.deltaY||e.deltaX);return;}
  const r=el.getBoundingClientRect();itlZoom(e.deltaY<0?1.35:1/1.35,e.clientX-r.left);},{passive:false});
// Кнопок «±0.5с» и «посмотреть это место» в карточке больше нет: тайминг двигается
// протяжкой блока по таймлайну вставок, туда же кликом ставится плейхед.
// Клик мимо текстового поля = поле отпускает фокус. Таймлайн вставок и ползунок
// перемотки зовут preventDefault() на pointerdown (иначе тащить блок нельзя), а он
// заодно отменяет и смену фокуса: поле «что искать» оставалось активным, и пробел
// уходил В ПРОМПТ вместо play/pause. Гасим фокус сами, до их обработчиков (capture).
document.addEventListener('pointerdown',e=>{
  const a=document.activeElement;
  if(!a||!/^(INPUT|TEXTAREA)$/.test(a.tagName))return;
  if(a.type==='checkbox'||a.type==='radio'||a.type==='range')return;
  if(e.target===a||(e.target.closest&&e.target.closest('input,textarea')===a))return;
  a.blur();                                        // onchange поля успевает отработать
},true);
document.addEventListener('keydown',e=>{if(!$('mbInserts').classList.contains('on'))return;
  // пробел = ВСЕГДА play/pause, кроме реального набора текста: фокус на чекбоксе (mosaic)
  // или на ползунке перемотки его перехватывать не должен
  if(e.key===' '&&spaceOnControl(e))return;                 // пробел на кнопке/чипе — его
  const el=e.target||{},tg=(el.tagName||'').toLowerCase();
  const typing=(tg==='textarea')||(tg==='input'&&!['checkbox','radio','range','button'].includes(el.type));
  if(e.key===' '&&!typing&&(tg==='input'||tg==='select')){e.preventDefault();el.blur&&el.blur();ipvToggle();return;}
  if(typing||tg==='select')return;
  if(e.key===' '){e.preventDefault();ipvToggle();}});
document.addEventListener('keydown',e=>{if(!$('mbCams').classList.contains('on'))return;
  if(e.key===' '&&spaceOnControl(e))return;                 // строки раскладки — role=button
  const tg=(e.target.tagName||'').toLowerCase();if(tg==='input'||tg==='textarea'||tg==='select')return;
  if(e.key===' '){e.preventDefault();cpvToggle();}});

// ---- перетаскивание в кадре (шаг 2): вставки / интро / субтитры ----
// Общая механика как на полосе вставок: pointer events, элемент едет локально сразу, в данные
// значение пишется по ОТПУСКАНИЮ, план догоняет тем же дебаунсом (ipvPlanSoon), плеер не
// перезапускается. Главная ловушка — пересчёт координат: экранные px делятся на k (ширина
// стойки / plan.w), а для вставки style=cam1 на Камере 1 ещё и на текущий зум (ipvZoomAt):
// она отрисована увеличенной вместе с кадром, и без деления палец на наезде 182% сдвинул бы
// её почти вдвое дальше, чем просил.
// Shift-драг — движение по ОДНОЙ оси, как в графических редакторах. Ось выбирается по
// БОЛЬШЕМУ по модулю смещению от точки старта и переоценивается на каждом pointermove
// (развернул движение — ось сменилась). Лок живёт в st.lock, и pointerup применяет
// ИМЕННО его, а не ev.shiftKey: Shift можно отпустить за миг до кнопки мыши, и в данные
// уехало бы не то, что нарисовано. Обнуляется СМЕЩЕНИЕ (dx/dy), а не координата —
// объект остаётся на своей второй оси, а не прыгает в ноль.
function axisLock(st,dx,dy,shift){
  st.lock=shift?(Math.abs(dx)>=Math.abs(dy)?'x':'y'):null;
  if(st.lock==='x')dy=0;else if(st.lock==='y')dx=0;
  return [dx,dy];}
$('ipvins').addEventListener('pointerdown',e=>{
  // Вставка лежит выше интро и перехватывала драг текста. Порядок слоёв на картинке не
  // меняем (он повторяет AE) — меняем только, кому достаётся нажатие: проверяем интро
  // РАНЬШЕ поиска .ipvwrap и отдаём нажатие ему.
  const ih=ipvIntroHitAt(e.clientX,e.clientY);if(ih){ipvIntroDragStart(e,ih.handle);return;}
  if(IPVMODE!=='ae'||!IPV.plan)return;
  const wr=e.target.closest('.ipvwrap');if(!wr)return;
  const i=+wr.dataset.ins;if(!(i>=0))return;
  const x=IPV.plan.inserts[i];if(!x)return;
  const real=INS.indexOf(INS.filter(r=>(r.media||'').trim())[i]);if(real<0)return;
  e.preventDefault();e.stopPropagation();
  const pl=IPV.plan,W=pl.w||1080;
  const k=(wr.clientWidth||W)/W;
  // зум делим ТОЛЬКО там, где placement его умножает (фото кам1 на кам1 — печёные ключи)
  const z=(x.card&&x.style==='cam1'&&!x.oncam2)?ipvZoomAt(ipvNow()):1;
  // Вставка «на подложке»: драг двигает ВСЮ карточку — плашку с фото,
  // поэтому старт и запись идут по kx/ky; у остальных вставок — по x/y, как раньше.
  // Поля положения/масштаба на странице вставок по-прежнему правят только фото.
  const onPlate=!!(x.card&&x.card.plate);
  const st={x0:e.clientX,y0:e.clientY,x:onPlate?(INS[real].kx||0):(INS[real].x||0),
            y:onPlate?(INS[real].ky||0):(INS[real].y||0),lock:null};
  // на старте мог висеть сдвиг прошлого драга (рефетч плана ещё не пришёл) — накопляем от него
  const psh=(IPV.insShift&&IPV.insShift.i===i)?IPV.insShift:{dx:0,dy:0};
  const move=ev=>{let dx=(ev.clientX-st.x0)/(k*z),dy=(ev.clientY-st.y0)/(k*z);
    [dx,dy]=axisLock(st,dx,dy,ev.shiftKey);
    IPV.insShift={i:i,dx:psh.dx+dx,dy:psh.dy+dy};
    ipvInsPlace(wr,x,ipvNow());};
  const up=ev=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);
    let dx=(ev.clientX-st.x0)/(k*z),dy=(ev.clientY-st.y0)/(k*z);
    [dx,dy]=axisLock(st,dx,dy,st.lock!==null);
    const nx=Math.round((st.x+dx)*10)/10,ny=Math.round((st.y+dy)*10)/10;
    if(onPlate){INS[real].kx=nx;INS[real].ky=ny;}else{INS[real].x=nx;INS[real].y=ny;}
    // сдвиг пишем и в карточку шага 2 — она источник x/y: драг правил только
    // INS, а ensureJobs при следующем открытии предпросмотра пересобирает список из карточек
    // и возвращал ноль. Ищем ту же карточку тем же norm-сравнением пути (правило как у маски).
    // kx/ky уезжают туда же: у вставки на подложке карточка — источник сдвига ВСЕЙ карточки.
    const cl=CLIPS[curAE];
    if(cl&&Array.isArray(cl.inserts)){
      const nm=normInsPath(INS[real].media);
      const ic=cl.inserts.findIndex(z=>nm&&nm===normInsPath(z.media));
      if(ic>=0){if(onPlate){cl.inserts[ic].kx=nx;cl.inserts[ic].ky=ny;}
                else{cl.inserts[ic].x=nx;cl.inserts[ic].y=ny;}}
    }
    captureAE();ipvPlanSoon();
    if(x.card&&x.style==='cam1'&&!x.oncam2){
      // кам1 рисуется из ПЕЧЁНЫХ ключей anim.position (старых x/y) — до прихода нового плана
      // держим сдвиг поверх них, иначе она отпрыгнет назад на ~0.7с пока план пересчитается
      IPV.insShift={i:i,dx:psh.dx+dx,dy:psh.dy+dy};
    }else{
      // кам2/видео читают x/y напрямую — правим кэш плана, сдвиг больше не нужен
      x.x=nx;x.y=ny;if(IPV.insShift&&IPV.insShift.i===i)IPV.insShift=null;}};
  window.addEventListener('pointermove',move);window.addEventListener('pointerup',up);});
// двойной клик по ручке масштаба, когда её закрывает слой вставок: #ipvintro события не
// получит — пробиваем стек тем же ipvIntroHitAt. Двойной клик по блокам таймлайна (itlblk)
// — отдельный обработчик, его не трогаем.
$('ipvins').addEventListener('dblclick',e=>{
  const ih=ipvIntroHitAt(e.clientX,e.clientY);
  if(ih&&ih.handle)ipvIntroScaleReset(e);});
// интро: тянется ГРУППА (прекомп) целиком; данные — gx/gy/gs на головной строке
$('ipvintro').addEventListener('pointerdown',e=>{
  const handle=e.target.closest('.intro-scale-handle');
  if(!handle&&!e.target.closest('.iline'))return;
  ipvIntroDragStart(e,handle);});
// интро: двойной клик по ручке масштаба возвращает автофит (gs=100)
$('ipvintro').addEventListener('dblclick',e=>{
  if(!e.target.closest('.intro-scale-handle'))return;
  ipvIntroScaleReset(e);});
// Тела драга и сброса интро — ОДНИ на обработчики #ipvintro и #ipvins: второй копии
// логики быть не должно (на разъехавшихся копиях уже горели). Объявлены
// выражениями в const, а не function-декларациями, намеренно: сторож
// tests/test_ui_static.py режет обработчик интро до следующего `\nfunction ` и иначе
// потерял бы из среза расчёт оси и запись gx/gy/gs.
const ipvIntroDragStart=function ipvIntroDragStart(e,handle){
  if(IPVMODE!=='ae'||!IPV.plan)return;
  const pl=IPV.plan;const gi=IPV.introCur;if(gi<0)return;
  introReorder();                                  // головы — в том же порядке, что группы плана
  const h=introHeadIdx(gi);if(h<0)return;
  e.preventDefault();e.stopPropagation();
  const io=$('ipvintro'),W=pl.w||1080;
  const k=(io.clientWidth||W)/W;
  // Общий масштаб интро: блок отрисован с множителем G (см. ipvIntroPos),
  // и экранные px переводятся в пространство группы делением на k*G — иначе при 60%
  // блок уезжает из-под пальца, а уголок тянет вдвое сильнее, чем просили.
  const G=((pl&&pl.intro_scale!=null)?pl.intro_scale:100)/100;
  const st={x0:e.clientX,y0:e.clientY,gx:INTRO[h].gx||0,gy:INTRO[h].gy||0,gs:INTRO[h].gs||100,lock:null};
  if(handle){e.preventDefault();e.stopPropagation();}
  const move=ev=>{let dx=(ev.clientX-st.x0)/(k*G),dy=(ev.clientY-st.y0)/(k*G);
    if(handle)pl.intro[gi].ds=Math.max(20,Math.min(300,st.gs+dx));
    else{[dx,dy]=axisLock(st,dx,dy,ev.shiftKey);pl.intro[gi].dx=st.gx+dx;pl.intro[gi].dy=st.gy+dy;}   // кэш плана — блок едет за пальцем
    ipvIntroPos(gi);};
  const up=ev=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);
    let dx=(ev.clientX-st.x0)/(k*G),dy=(ev.clientY-st.y0)/(k*G);
    if(handle)INTRO[h].gs=Math.round(Math.max(20,Math.min(300,st.gs+dx))*10)/10;
    else{[dx,dy]=axisLock(st,dx,dy,st.lock!==null);INTRO[h].gx=Math.round((st.gx+dx)*10)/10;INTRO[h].gy=Math.round((st.gy+dy)*10)/10;}   // данные
    captureAE();ipvPlanSoon();};
  window.addEventListener('pointermove',move);window.addEventListener('pointerup',up);};
const ipvIntroScaleReset=function ipvIntroScaleReset(e){
  if(IPVMODE!=='ae'||!IPV.plan)return;
  const pl=IPV.plan;const gi=IPV.introCur;if(gi<0)return;
  introReorder();
  const h=introHeadIdx(gi);if(h<0)return;
  e.preventDefault();e.stopPropagation();
  INTRO[h].gs=100;
  captureAE();ipvPlanSoon();};
// Кому достаётся нажатие над текстом интро. Слой вставок #ipvins лежит ВЫШЕ слоя интро
// #ipvintro (z-index из layer_order: фото 7, видео 9, интро 6), а .ipvwrap принимает
// указатель — pointerdown над строкой интро, закрытой вставкой, уходил в обработчик
// вставок и тащил ВСТАВКУ. Порядок слоёв на картинке НЕ меняем (он повторяет AE) —
// меняем только, кому достаётся нажатие: пробиваем стек сами. elementsFromPoint
// пропускает элементы с pointer-events:none, а .iline и ручка масштаба — auto.
const ipvIntroHitAt=function ipvIntroHitAt(x,y){
  if(IPVMODE!=='ae'||!IPV.plan||IPV.introCur<0)return null;
  for(const el of document.elementsFromPoint(x,y)){
    if(!el.closest||!el.closest('#ipvintro'))continue;
    const h=el.closest('.intro-scale-handle');
    if(h)return {handle:h};
    if(el.closest('.iline'))return {handle:null};
    return null;
  }
  return null;};
// Субтитры в кадре мышью НЕ таскаются: высота правится ползунком стиля
// («Высота субтитров, % снизу»), а перехваченный клик по строке мешал работе с кадром.
// Прежний драг правил CURSTYLE.sub_y через posy плана — от него остался только путь
// через поле стиля (stEdit → ipvPlanSoon), он и есть единственный.

