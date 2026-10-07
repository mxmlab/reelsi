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
  segs:[],audio:[],words:[],dur:0,contentDur:0,fps:60,aidx:0,vidx:-1,primed:-1,curCi:-1,rollCi:-1,playing:false,raf:0,xml:'',cur:-1,intro:[],introCur:-1,plan:null,insShift:null,insVids:new Map(),dims:new Map(),roto:[]};
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
    // cid — стабильный id карточки шага 2: связь «вставка шага 3 ↔ карточка», которую путь
    // файла не несёт (дубли одного фото). Так же её читают insCardFor и ensureJobs.
    cid:x.uid||'',
    start_s:Math.round((x.start_sec||0)*100)/100,start_f:0,
    dur_s:Math.round((x.duration_sec||2)*100)/100,dur_f:0,
    scale:(was.scale!=null?was.scale:44),mosaic:!!x.mosaic,plate:!!x.plate,
    x:x.x||0,y:x.y||0,kx:kx,ky:ky,sc:x.sc||100,mw:x.mw||100,mh:x.mh||100,sin:x.sin||0,
    ...(was.noexit?{noexit:was.noexit}:{})};}
// ---- план сцены: предпросмотр РИСУЕТ то, что прислал /api/scene ----
// Никаких вторых расчётов: окна групп интро, анимации вставок, зум, стопку субтитров и
// полосы рото берём из плана. Ошибка плана предпросмотр не ломает — без него играет как раньше.
// Формат кадра клипа (w, h): его спрашивают там, где плана ещё нет (первый кадр,
// ошибка /api/scene, стенды node). Есть план — он и главнее: числа в нём уже посчитаны
// по кадру ролика. Кадр ролика — формат спикера ЭТОГО клипа (camFrameWH, core/frame.py);
// профиля нет или функции нет (стенд node гоняет функции по одной) — 9:16, как раньше.
function ipvPlanWH(){
  const p=(typeof IPV!=='undefined'&&IPV.plan)||null;
  const wh=(typeof camFrameWH==='function')?camFrameWH():null;
  return {w:(p&&p.w)||(wh&&wh[0])||1080,h:(p&&p.h)||(wh&&wh[1])||1920};}
function ipvPlanBody(){
  // Страница рендера без AE присылает тело сборки ГОТОВЫМ: своего интерфейса у неё нет,
  // а второй сборки тех же полей (уже в Python) быть не должно — план обязан считаться
  // из одного места. Дверь читает только эта страница, у остальных IPV_BODY нет.
  if(typeof IPV_BODY!=='undefined'&&IPV_BODY)return IPV_BODY;
  const c=(curAE>=0&&CLIPS[curAE])?CLIPS[curAE]:(curIns>=0&&CLIPS[curIns]?CLIPS[curIns]:null);
  const j=c&&c.job;const xml=IPV.xml;
  let ir;try{ir=introResolve();}catch(e){ir={lines:[],remove:[],splits:[]};}
  const insList=(IPVMODE==='ae')
    ?INS.filter(r=>(r.media||'').trim())
    :((c&&c.inserts)?c.inserts.filter(r=>(r.media||'').trim()).map(x=>cardToIns(x)):[]);
  // Музыка — та же тройка полей, что уходит в сборку (jobForBuild): режим и папка из
  // стиля (с переопределением у клипа), трек — закреплённый job.music_pick. Считает
  // это ОДНА функция плана музыки, а не вторая копия правил здесь.
  const _mu=(typeof musicJobFields==='function'&&c)
    ?musicJobFields(c)
    :{music:'',music_random:false,music_dir:''};
  return {xml:xml,music:_mu.music,music_random:_mu.music_random,music_dir:_mu.music_dir,
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
function pvApplyStageAspect(plan, stageOrId){
  const el = (typeof stageOrId === 'string' ? $(stageOrId) : stageOrId) || $('ipvstage') || $('pvstage');
  const w = (plan && plan.w) ? Number(plan.w) : 1080;
  const h = (plan && plan.h) ? Number(plan.h) : 1920;
  const ar = (w > 0 && h > 0) ? (w / h) : (9 / 16);
  const arStr = (w > 0 && h > 0) ? `${w} / ${h}` : '9 / 16';
  if (el) {
    el.style.setProperty('--stage-ar', arStr);
    el.style.aspectRatio = arStr;
  }
  const root = (el && el.closest) ? el.closest('.inspv') : null;
  if (root) {
    root.style.setProperty('--stage-ar', `${ar}`);
  }
  return { w, h, ar, arStr };
}
let IPVPLAN_T=0;
function ipvPlanSoon(){clearTimeout(IPVPLAN_T);IPVPLAN_T=setTimeout(ipvPlanFetch,300);}   // правки идут пачкой — рефетчим по затишью
async function ipvPlanFetch(){
  const xml=IPV.xml;if(!xml)return;
  let d;try{d=await (await fetch('/api/scene',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(ipvPlanBody())})).json();}
  catch(e){return;}                       // план не критичен: плеер играет как раньше
  if(IPV.xml!==xml)return;                // модалку успели переоткрыть на другом клипе
  if(d.ok&&d.plan){IPV.plan=d.plan;IPV.insShift=null;   // свежий план сам несёт сдвиги — временный сброс не нужен
    // Длина КОНТЕНТА: хвост дисклеймера живёт после ролика и продлевает только ползунок.
    if(!IPV.contentDur)IPV.contentDur=IPV.dur;
    const _tail=d.plan.disclaimer&&d.plan.disclaimer.end_copy;
    if(_tail&&_tail.t1>IPV.dur){IPV.dur=_tail.t1;}
    if(typeof pvApplyStageAspect==='function')pvApplyStageAspect(d.plan, $('ipvstage'));
    ipvSubsInvalidate();        // новый шрифт/положение — показать субтитры заново даже на паузе
    IPV.intro=ipvIntroGroups();
    sfxEnsure(d.plan);          // SFX: элементы под план
    // Переходу видеовставок нужен прокси (ProRes браузер не играет). Проверка typeof —
    // как у соседних дверей: стенды node вырезают ipvPlanFetch по одной функции, и
    // вызов без неё ронял бы стенд, а не боевую отрисовку.
    if(typeof ipvTransAsk==='function')ipvTransAsk(d.plan);
    IPV.cur=-2;IPV.introCur=-2;
    if(typeof renderSubRowsList==='function')renderSubRowsList();
    if(typeof aewUpdateCaptionUI==='function')aewUpdateCaptionUI();
    // Рото в кадре: масок у плана нет (их считает кнопка «Рассчитать рото и трекинг»),
    // берём готовые из кэша — быстрая дверь без GPU. Ждём её ДО первого кадра, иначе
    // на паузе слой появился бы только после перемотки.
    if(typeof ipvCalcApply==='function')await ipvCalcApply();
    if(IPV.vids.length){if(_tail)itlFit();itlDraw();ipvUI(ipvNow());}}
  else if(d.error){uiLog(t('план сцены: ')+(d.error||''));}}
// ---- SFX в предпросмотре: те же числа, что в AE ----
// План несёт audio.sfx с ГОТОВЫМ стартом каждого события (t = ev − at + in, файловые
// in/out) — JS ничего не пересчитывает, только ставит элемент на позицию. Каждый звук —
// свой <audio> (как музыка), громкость = dbToGain(база + db) × MEDIA_VOL (общий множитель
// прослушивания, в .jsx не уезжает). Видеопереход в превью не звучит: он и не рисуется.
let SFX_ELS={};
function sfxEnsure(plan){
  const sfx=(plan&&plan.audio&&plan.audio.sfx)||[];
  for(const k in SFX_ELS){if(!sfx.find(s=>s.kind===k)){mediaFree(SFX_ELS[k].el);delete SFX_ELS[k];}}
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
// Числа ЭТАЛОННОЙ кривой живут ТОЛЬКО здесь (умолчания параметров): keysAt без готового
// ease и ipvEase берут их отсюда, своей пары 35/90 у превью нет. В .jsx те же два числа
// приезжают подстановкой HL_EASE_OUT/HL_EASE_IN (core/xml2ae/layout.py) — одна кривая
// на сборку и на предпросмотр.
function aeEase(out,inp){
  if(out==null)out=35;if(inp==null)inp=90;
  return [out/100,0,1-inp/100,1];}
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
      if(ease==='linear'||ease===false){
        return Array.isArray(a[1])?a[1].map((cv,j)=>cv+(b[1][j]-cv)*u):a[1]+(b[1]-a[1])*u;}
      const o=ease&&ease[i]?ease[i][1]:null;        // out уходящего ключа (нет ease — эталон aeEase)
      const inn=ease&&ease[i+1]?ease[i+1][0]:null;  // in приходящего
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
// Допуск перемотки <video>: в игре он грубый (0.4 с — иначе декодер дёргался бы на
// каждом кадре), в рендере — половина кадра: снимок обязан быть ровно тем кадром, который
// просит номер. Одно правило на ВСЕ места постановки времени — видеовставки
// (ipvOverlay/ipvOverlayPlan) и камеру кадра (vFrameAt).
function vidSeekTol(){return ipvRenderMode()?(0.5/(IPV.fps||60)):0.4;}
function insSetSD(x,s,d){if(IPVMODE==='ae'){x.start_s=s;x.start_f=0;x.dur_s=d;x.dur_f=0;}
  else{x.start_sec=s;x.duration_sec=d;}}
// uid карточки шага 2 — СТАБИЛЬНЫЙ id внутри клипа. Путь файла не различает дубли: три
// вставки с одним и тем же фото — три карточки с одинаковым media, и «первая по пути»
// получала правку, тайминг, подстройки и подсветку всех трёх (вставки «исчезали» и
// «двоились»). Ставится ЛЕНИВО: сохранённое состояние приезжает без uid, а появиться он
// обязан ровно один раз на карточку — зовут insEnsureUids ensureJobs и открытие окна вставок.
let INSUIDSEQ=0;
function insEnsureUids(c){
  if(!c||!Array.isArray(c.inserts))return c;
  c.inserts.forEach(x=>{if(x&&!x.uid)x.uid=(typeof crypto!=='undefined'&&crypto&&crypto.randomUUID)
    ?crypto.randomUUID():((Date.now().toString(36))+'_'+(++INSUIDSEQ));});
  return c;}
// Карточка шага 2, из которой собрана вставка шага 3. Ищем по стабильному id (cid у вставки,
// uid у карточки): у дублей одного файла путь не различает, кто есть кто. Путь — ТОЛЬКО
// запасной ключ для ЛЕГАСИ-вставок без cid: берём первую карточку с этим путём, ещё НЕ
// ЗАНЯТУЮ другой вставкой, и запоминаем связь в самой вставке (дальше это уже cid).
// null = вставка добавлена руками на шаге 3 (карточки у неё нет, пересборка её не тронет).
function insCardFor(x){if(!x)return null;
  const id=x.cid||x.uid;                        // вставка шага 3 (cid) или сама карточка (uid)
  if(id)for(const v of CLIPS){if(!v||!Array.isArray(v.inserts))continue;
    const own=v.inserts.find(z=>z&&z.uid===id);if(own)return own;}
  const cl=CLIPS[curAE];
  if(!cl||!Array.isArray(cl.inserts))return null;
  insEnsureUids(cl);
  const nm=normInsPath(x.media);
  if(!nm)return null;
  const claimed={};
  ((typeof INS!=='undefined'&&Array.isArray(INS))?INS:[]).forEach(r=>{if(r&&r!==x&&r.cid)claimed[r.cid]=1;});
  const ic=cl.inserts.findIndex(z=>nm===normInsPath(z.media)&&!(z&&claimed[z.uid]));
  if(ic<0)return null;
  const card=cl.inserts[ic];
  if(!x.uid)x.cid=card.uid;                     // легаси нашла свою карточку — связь запоминаем
  return card;}
// Тайминг/длительность правки на шаге 3 (драг блока, ручки краёв на таймлайне) пишем И в
// карточку шага 2: она — источник истины для ensureJobs, и без этой записи правка молча
// пропадала при следующем открытии превью (INS пересобирался из карточки со старым стартом).
// Карточка — по стабильному id вставки (insCardFor): у дублей одного файла путь не различает.
function insSetSDCard(x,s,d){const c=insCardFor(x);if(!c)return;
  c.start_sec=s;c.duration_sec=d;}
function insLbl(x){return IPVMODE==='ae'?(((x.media||'').replace(/^.*[\\\/]/,''))||t('файл не выбран')):(x.query||'');}
function ipvAfterEdit(){if(IPVMODE==='ae'){renderIns();captureAE();itlDraw();ipvRefresh();}
  else{saveState();renderInsHost();syncClipLists();}}   // renderInsHost сам дёргает itlDraw+ipvRefresh
// Страница рендера без AE (templates/render.html) грузит эти же файлы и открывает
// предпросмотр той же дверью (ipvOpen): своей она объявляет тело сборки (IPV_BODY).
// Дорожка обработанного голоса ей не нужна — звук рендер собирает сам
// (core/webrender_audio), а заказ запекания на каждый кусок дрался бы за GPU со съёмкой.
function ipvRenderPage(){return typeof IPV_BODY!=='undefined'&&!!IPV_BODY;}
async function ipvOpen(xml){
  const tra=IPV.vt;vtStop(IPV);
  ipvPause();insVidFreeAll();
  IPV={vids:[],bufs:[],scrubbing:false,scrubT:0,
    segs:[],audio:[],words:[],dur:0,fps:60,aidx:0,vidx:-1,primed:-1,curCi:-1,rollCi:-1,defAt:0,stats:{styk:0,swap:0,seek:0,cam:0,stale:0,back:0},playing:false,raf:0,xml:xml||'',cur:-1,intro:[],introCur:-1,plan:null,insShift:null};
  IPV.vt=tra;
  const stage=$('ipvstage');
  pvApplyStageAspect(null, stage);
  [...stage.querySelectorAll('video')].forEach(v=>mediaFree(v));
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
  // Рото — как слои перехода: чужой кэш масок сбрасываем, холст и элементы прошлого
  // клипа отпускаем (новый клип начинает с чистого листа).
  const oldRc=$('ipvroto');if(oldRc)oldRc.remove();
  IPV_ROTOSEQ.forEach(el=>{try{el.pause();}catch(e){}});
  IPV_ROTOSEQ.length=0;IPV_ROTO='';IPV_ROTOACC=-1;IPV_ROTODRAWN=-1;
  // Слои перехода — от прошлого клипа (их заводят по плану): коробку убираем, заказ
  // прокси перехода сбрасываем. Новый клип начинает с чистого листа.
  const oldTr=$('ipvtrans');if(oldTr){oldTr.querySelectorAll('video').forEach(mediaFree);oldTr.remove();}
  IPV.transSig='';IPV.transAsked='';
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
  if(typeof pvAudioLimit==='function')pvAudioLimit(stage,IPV);   // строка про звук Firefox: причину называет сам элемент
  // Обработанный голос клипа — ТОЙ ЖЕ дорожкой и той же дверью, что шаг 1 (vtPrep):
  // панели «Голос» у этого превью нет, поэтому настройки берутся из профиля спикера
  // клипа (vtFx) — ровно те, по которым соберётся проект. Страница рендера без AE сюда
  // не ходит: см. ipvRenderPage. Элемент дорожки переживает переоткрытие клипа
  // (`tra` выше): он не в DOM, а второй createMediaElementSource на тот же звук завёл
  // бы вторую дорожку мимо регулятора громкости.
  if(!ipvRenderPage()&&typeof vtPrep==='function')vtPrep(IPV);
}
// Закрытие превью шага 3: освободить видео камер, буферов, переходов и вставок
function ipvClose(){
  ipvPause();insVidFreeAll();
  const stage=$('ipvstage');
  if(stage)[...stage.querySelectorAll('video')].forEach(mediaFree);
  const oldTr=$('ipvtrans');if(oldTr){oldTr.querySelectorAll('video').forEach(mediaFree);oldTr.remove();}
  IPV.transSig='';IPV.vids=[];IPV.bufs=[];}
// картинка активного ракурса — общая машина всех плееров (camApply в 60-preview.js):
// разбег входящей камеры перед стыком, показ по готовности, дрейф гасится скоростью.
function ipvApplyVisual(tm,play){camApply(IPV,tm,play);}
// Время монтажа. В рендере без AE часы — НОМЕР КАДРА (IPV.renderT), а не currentTime
// <video>: seek округляет время к кадру исходника, и одна и та же t давала бы разные
// кадры. Живое превью (renderT пуст) считает как раньше — от ведущей камеры.
function ipvNow(){return ipvNowFrame(IPV);}
function ipvNowFrame(P){if(P.renderT!=null)return P.renderT;
  const a=P.audio[P.aidx];return (a&&P.vids.length)?a.ts+(P.vids[0].currentTime-a.src):0;}
// Выставить сцену на момент tm и — в живом превью — перемотать ведущую камеру.
// Дверь ОДНА на оба пути, и это не экономия строк: дверей было две, и вторая
// (ipvSeekSet, «сцена без перемотки») оказалась копией первой — правку одной из них
// вторая не видела. Здесь один код сцены и одно решение о перемотке:
//   * живое превью (протяжка, клик по таймлайну, пауза) ставит сцену и время <video>;
//   * рендер кадров (ipvRenderAt) в режиме рендера перемотку НЕ делает: её ведёт
//     vFrameAt по НОМЕРУ кадра — у активной камеры своё исходное время (ipvCamTimeAt),
//     и лишний `currentTime=` на ведущей дёргал бы декодер за камеру, которой в кадре
//     может и не быть.
// Сцена выставляется на КАЖДОМ кадре: субтитры, вставки и наезд считаются на tm —
// «кадр уже на месте» не должно значить «кадр не считается».
function ipvSeekTo(tm){if(!IPV.vids.length||!IPV.audio.length)return;tm=Math.max(0,Math.min(tm,IPV.dur));
  IPV.aidx=pvSegAt(IPV.audio,tm);IPV.vidx=-1;IPV.primed=-1;
  spareIdle(IPV);camIdle(IPV);ipvApplyVisual(tm,false);ipvUI(tm);itlEnsure(tm);musicElSync();
  if(typeof vtTick==='function')vtTick(IPV,tm);   // дорожка обработанного голоса стоит на паузе — подводим к новому месту
  if(!ipvRenderMode())ipvVideoTo(IPV.vids[0],ipvLeadTime(tm));}
// Исходное время ведущей камеры на момент tm — то, куда её ставить. Одна формула на
// живую протяжку и на рендер: второй копии «что играет» быть не должно.
function ipvLeadTime(tm){const a=IPV.audio[IPV.aidx];return a?a.src+Math.max(0,tm-a.ts):null;}
// Поставить <video> на время, если оно уже не стоит. Порог — одна миллисекунда с
// хвостиком: у играющего <video> время меняется каждый кадр, и «ставить всегда» значило
// бы сбрасывать декодер 60 раз в секунду. Дробное сравнение тут НЕ про кадр исходника:
// «кадр уже на месте» решает вызывающий (см. vFrameAt), а здесь только «не дёргать
// декодер зря».
function ipvVideoTo(v,want){if(!v||want==null)return;
  if(Math.abs((+v.currentTime||0)-want)<=1e-4)return;
  try{v.currentTime=want;}catch(e){}}
function ipvStep(){if(!IPV.playing)return;
  const st=pvStep(IPV);if(!st)return;
  if(st.end){ipvPause();ipvSeekTo(0);return;}
  if(st.adv)IPV.stats.styk++;
  if(st.swap)IPV.stats.swap++;
  if(st.seeked)IPV.stats.seek++;
  ipvApplyVisual(st.tm,true);
  spareRollAt(IPV,st.tm);
  if(typeof vtTick==='function')vtTick(IPV,st.tm);   // дорожка обработанного голоса идёт за монтажом (и глушит звук камеры)
  if(typeof pvAudioLimit==='function')pvAudioLimit($('ipvstage'),IPV);   // строка про звук Firefox — по ходу игры
  ipvUI(st.tm);}
function ipvTick(){if(!IPV.playing)return;ipvStep();IPV.raf=requestAnimationFrame(ipvTick);}
function ipvPlay(){if(!IPV.vids.length)return;IPV.playing=true;$('ipvplay').innerHTML=ico('pause');
  // Граф Web Audio будится ОДНОЙ дверью на все плееры (audioWake) — и из обработчика
  // нажатия, а не побочно через музыку (`musicElSync` в конце): звук камеры идёт только
  // через граф, и приостановленный AudioContext глушил бы шаг 3 молча.
  if(typeof audioWake==='function')audioWake();
  // Звук камеры глушим, если играет обработанный голос (IPV.voiceMute): ту же галку
  // ставит и снимает vtTick — здесь она важна в первый кадр, до тика.
  IPV.vids[0].muted=!!IPV.voiceMute;IPV.vids[0].play().catch(()=>{});sparePrime(IPV);ipvApplyVisual(ipvNow(),true);
  if(typeof vtTick==='function')vtTick(IPV,ipvNow());
  IPV.raf=requestAnimationFrame(ipvTick);
  clearInterval(IPV.itv);IPV.itv=setInterval(ipvStep,120);musicElSync();}   // страховка: rAF молчит в фоновой вкладке
function ipvPause(){IPV.playing=false;const b=$('ipvplay');if(b)b.innerHTML=ico('play');
  cancelAnimationFrame(IPV.raf);clearInterval(IPV.itv);IPV.vids.forEach(v=>v.pause());spareStop(IPV);camIdle(IPV);
  const ov=$('ipvins');if(ov){const iv=ov.querySelector('video');if(iv)iv.pause();}musicElSync();sfxPause();vtPause(IPV);}
function ipvToggle(){IPV.playing?ipvPause():ipvPlay();}

// ---- рендер без After Effects: кадр по НОМЕРУ, а не по реальному времени ----
// Зовёт страница /render: она грузит ЭТИ ЖЕ файлы интерфейса и просит кадр за кадром.
// Второй копии отрисовки здесь нет и быть не должно — рендер идёт через ipvSeekTo и
// ipvUI, то есть через тот же код, что играет и что тянется ползунком. Отличий ровно
// пять, и все пять — здесь:
//   1) время берётся из IPV.renderT, а не из currentTime <video> (см. ipvNow);
//   2) на корне висит класс render-mode — CSS-переходы и анимации выключены (app.css);
//   3) величины, которые в живом превью везёт переход (ширина плашки субтитров),
//      считаются на t — ipvSubsBgAt;
//   4) кадр сдаётся в два приёма: ipvRenderAt выставляет сцену, ipvRenderPaint сводит
//      её к снимку. Так съёмщик мерит seek и paint по отдельности (у владельца на кадр
//      уходило 1.3 с, и без разбивки не видно, на что именно);
//   5) «показанный» кадр <video> (requestVideoFrameCallback) НЕ ждётся, а перемотка не
//      повторяется там, где кадр уже стоит на месте (см. vFrameShown и vFrameAt).
const VFRAME_MS=1500;      // потолок ожидания кадра <video>: не открывшийся файл не вешает рендер
// Разбивка времени съёмки кадра: СЕКУНДЫ (съёмщик переводит их в миллисекунды, см.
// readPaint в core/webrender/capture.mjs). Живой прогон показал 1.3 с на кадр — по этим
// числам видно, на что именно уходит время: ждать кадр <video> (seek), рисовать (paint)
// или снимать протоколом. Обнуляет их съёмщик, прочитав: кадр мерится по отдельности.
const IPV_RT={seek:0,paint:0,frames:0};
function ipvRenderMode(){return IPV.renderT!=null;}
function ipvRenderModeOn(){
  const r=(typeof document!=='undefined')?(document.documentElement||document.body):null;
  if(r&&r.classList&&!r.classList.contains('render-mode'))r.classList.add('render-mode');}
// Исходное время камеры ci на момент tm: её кусок EDL, в который попал tm. Кусок —
// тот же IPV.segs, по которому camApply выбирает ракурс: второй копии «что играет» нет.
function ipvCamTimeAt(ci,tm){
  const segs=(IPV.segs||[]).filter(s=>+s.ci===+ci);
  let s=null;for(const g of segs){if(tm>=g.ts&&tm<g.te){s=g;break;}}
  if(!s)s=segs.length?segs[segs.length-1]:null;
  return s?s.src+Math.max(0,tm-s.ts):null;}
// Ждём, что элемент отыграл seek: кадр после currentTime= приходит не сразу.
function vSeekDone(v){return new Promise(res=>{
  if(!v||(!v.seeking&&v.readyState>=2))return res();
  let done=false;
  const fin=()=>{if(done)return;done=true;clearTimeout(T);
    v.removeEventListener('seeked',fin);v.removeEventListener('loadeddata',fin);
    v.removeEventListener('error',fin);res();};
  const T=setTimeout(fin,VFRAME_MS);
  v.addEventListener('seeked',fin);v.addEventListener('loadeddata',fin);v.addEventListener('error',fin);});}
// ...и что кадр уже отдан на отрисовку: requestVideoFrameCallback (Chrome). Нет его —
// не ждём вовсе, не пришёл за таймаут — снимаем то, что успело показаться.
// В РЕНДЕРЕ этого ожидания НЕТ (ms=0) — и это не «оптимизация на глаз»: у <video> на
// паузе в Chrome без окна колбэк не приходит НИКОГДА, поэтому каждый кадр упирался в
// полный таймаут (живой замер владельца: seek 1.5–2.2 с на кадр при VFRAME_MS=1500).
// Ждать и нечего: после `seeked` кадр уже отдан декодером и годится для drawImage, а
// камеру рисует холст (ipvCamPaint), а не сам <video>: «показанный» кадр нужен показу на
// экране, а не снимку. Ожидание оставлено на случай, если дверь позовут вне рендера —
// там <video> виден сам, и показанный кадр как раз то, чего ждут.
function vFrameShown(v){return new Promise(res=>{
  const ms=ipvRenderMode()?0:VFRAME_MS;
  if(!v||ms<=0||typeof v.requestVideoFrameCallback!=='function')return res();
  let done=false;
  const fin=()=>{if(done)return;done=true;clearTimeout(T);res();};
  const T=setTimeout(fin,ms);
  try{v.requestVideoFrameCallback(fin);}catch(e){clearTimeout(T);res();}});}
// Поставить <video> на нужное исходное время и дождаться кадра. want=null — время уже
// поставлено (видеовставка ставит его себе сама, по своему sin): ждём только кадр.
async function vFrameAt(v,want){
  if(!v)return;
  // Кадр уже СТОИТ на просимом времени (разница меньше половины кадра, vidSeekTol): это
  // ровно тот кадр исходника, который просит номер, — ни `currentTime=`, ни ожидания.
  // Перемотка стоит прогона от ближайшего ключевого, а ждать нечего: кадр на месте.
  // «Стоит» — значит и не едет (seeking): начатая перемотка означает, что кадра ещё нет.
  if(want!=null&&!v.seeking&&Math.abs((+v.currentTime||0)-want)<=vidSeekTol())return;
  ipvVideoTo(v,want);        // 1e-4: стоим на этом времени — декодер не дёргаем зря
  await vSeekDone(v);
  await vFrameShown(v);}
// Шрифты и картинки — ДО первого кадра: иначе он снимется подменённым шрифтом или
// пустым местом фото. fonts.ready сам по себе ничего не грузит (он про уже запрошенные),
// поэтому сначала просим браузер загрузить семейства, которые реально стоят в кадре.
async function ipvRenderAssets(){
  try{
    if(document.fonts){
      const fams=new Set(),root=$('ipvstage');
      const els=(root&&root.querySelectorAll)?root.querySelectorAll('*'):[];
      for(const el of els){const fm=el.style&&el.style.fontFamily;if(fm)fams.add(fm);}
      await Promise.all([...fams].map(f=>{try{return document.fonts.load('16px '+f);}catch(e){return null;}}));
      await document.fonts.ready;}
  }catch(e){}
  const root=$('ipvstage');if(!root||!root.querySelectorAll)return;
  await Promise.all([...root.querySelectorAll('img')].map(im=>{
    if(im.decode)return im.decode().catch(()=>{});
    return new Promise(res=>{if(im.complete)return res();
      im.addEventListener('load',res,{once:true});im.addEventListener('error',res,{once:true});});}));}
// Кадр отдан браузеру: два rAF. Снимок снимает сведённый слой, а не дерево DOM.
function ipvRenderPaintFrame(){return new Promise(res=>{
  if(typeof requestAnimationFrame!=='function')return res();
  requestAnimationFrame(()=>requestAnimationFrame(()=>res()));});}
// ---- рендер без AE: кадры камер ГОТОВЫМИ КАРТИНКАМИ ----
// Замер владельца: перемотка <video> — 90 мс из 225 на кадр, самая большая доля. Поэтому
// ПЕРЕД съёмкой куска core/webrender.py вынимает ffmpeg'ом ровно те исходные кадры, что
// видны в кадрах куска, и кладёт их в папку куска (её отдаёт страница /render дверью
// IPV_FRAMES). Камеру рисует ТА ЖЕ ipvCamPaint: меняется только источник пикселей
// (ipvCamSrc) — рамка спикера, LUT, зум и наезд остаются как были.
// Картинки нет (не вынулась, папка не приехала, живое превью) — кадр идёт перемоткой
// <video>, как раньше: выемка это ускорение, а не условие рендера.
let IPV_FRAMES='';          // папка картинок куска; '' — кадры идут перемоткой
let IPV_FEXT='jpg';         // расширение картинок куска: его назвал сборщик (fext в адресе)
let IPV_FRANGE=null;        // [первый кадр куска, сколько их]: границы примерки кадра
let IPV_FRAME=null;         // {ci,f,img} — источник пикселей ТЕКУЩЕГО кадра рендера
const IPV_FIMGS=new Map();  // 'ci:f' -> <img>: окно загруженных картинок
const IPV_FDEAD=new Set();  // камеры, картинок которых нет ВООБЩЕ: их кадры идут перемоткой
const IPV_FMISSN=new Map(); // ci -> сколько картинок камеры не пришло ПОДРЯД (успех обнуляет)
const IPV_FLOG=new Set();   // про пропажу уже сказано (строка на камеру, а не на кадр)
const IPV_FMAX=6;           // сколько картинок держим: 1080x1920 в памяти — 8 МБ на кадр
// Размер блока мозаики в px КАДРА ролика — как у эффекта Mosaic в AE: шаблон
// (core/xml2ae/template.py, addMosaic) ставит 64x64 и включает «sharp colors». Второй
// копии числа не заводить: разойдись они — мозаика превью и рендера будет разной
// крупности на одном и том же кадре.
const MOSAIC_BLOCK=64;
// С какой пропажи ПОДРЯД камера уходит на перемотку до конца куска. Единица тут стоила
// куска целиком: на живом прогоне ПЕРВАЯ же картинка камеры, сменившейся на 150-м кадре
// пятисекундного куска, не доехала — и 149 кадров (весь остаток куска) пошли перемоткой
// `<video>` по 94 мс вместо 22. Порог в три кадра оставляет одиночному сбою цену одного
// кадра, а пропавшей папке — три запроса вместо трёхсот. Считаются при этом только
// пропажи у камер, которые в куске показываются (ipvFrameFail): картинка камеры вне
// монтажа куска — не пропажа куска.
const IPV_FMISS_MAX=3;
// Какая камера видна в кадре ролика tm и какой ИСХОДНЫЙ кадр ей нужен. ОДНО правило с
// выемкой (core.webrender.src_frame_at): время источника округляется к кадру ролика.
// Расхождение на кадр — это ДРУГАЯ картинка, поэтому формула одна на обе стороны, а не
// «похожие» вычисления. Камеру берём у того же куска EDL, по которому показывает
// camApply, а время — тем же ipvCamTimeAt: второй копии «что играет» здесь нет.
function ipvSrcFrameAt(tm){
  const segs=IPV.segs||[];if(!segs.length)return null;
  const s=segs[pvSegAt(segs,tm)];if(!s)return null;
  // Имя tsrc, а не t: `t` в проекте — функция перевода (tests/test_ui_static.py).
  const tsrc=ipvCamTimeAt(s.ci,tm);if(tsrc==null)return null;
  // floor(x+0.5), а не Math.round: у Python round(2.5) округляет к чётному, у JS — вверх,
  // и ровно на половине кадра стороны разъехались бы на кадр (core.webrender.src_frame_at).
  return {ci:(+s.ci||0),f:Math.floor(tsrc*(IPV.fps||60)+0.5)};}
// Картинка исходного кадра: файл c<камера>_<кадр>.<расширение> в папке куска. Имя —
// номер ИСХОДНОГО кадра (тот же, что считает ipvSrcFrameAt), а не «пятый с начала
// куска»: второй нумерации, которая могла бы разойтись с выемкой, тут не заводится.
// РАСШИРЕНИЕ приходит в адресе страницы (IPV_FEXT) и совпадает с тем, чем вынимал
// ffmpeg: своей копии «.jpg» у страницы нет — формат выемки меняется в core/webrender.py,
// и разойдись они, камера просила бы несуществующие файлы (404 → перемотка `<video>`).
function ipvFrameURL(ci,f){
  return '/api/media?path='+encodeURIComponent(IPV_FRAMES+'/c'+ci+'_'+f+'.'+IPV_FEXT);}
// Картинка кадра: из окна или новая (начинает грузиться сразу). Не загрузилась — кадр
// уйдёт перемоткой, и об этом ОДНА строка в лог (по строке на кадр лог бы затопило).
function ipvFrameImg(ci,f){
  if(!IPV_FRAMES||IPV_FDEAD.has(ci))return null;
  const k=ci+':'+f,hit=IPV_FIMGS.get(k);
  if(hit)return hit._bad?null:hit;
  const im=document.createElement('img');
  im._bad=false;
  // Адрес запоминаем: у события `error` статуса нет, а причину пропажи надо назвать
  // (ipvFrameWhy спрашивает её у сервера по этому же адресу).
  const url=ipvFrameURL(ci,f);
  im.addEventListener('error',()=>{im._bad=true;ipvFrameFail(im,ci,url);});
  im.src=url;
  IPV_FIMGS.set(k,im);
  ipvFrameTrim();
  return im;}
// Картинка не пришла — ЭТОТ кадр уходит перемоткой. Считается пропажа ОДИН раз на
// картинку (`_counted`): в браузере на 404 приходят и событие `error`, и отказ `decode()`,
// а это одна пропажа, а не две. Камера уходит на перемотку до конца куска только после
// IPV_FMISS_MAX пропаж ПОДРЯД (см. IPV_FMISS_MAX): одиночный сбой не должен стоить куска.
// И считаются пропажи только у камеры, которая в ЭТОМ КУСКЕ показывается: картинок
// камеры вне монтажа кусок не рисует, а счётчик помечал её «без картинок» и уводил кусок
// на перемотку `<video>` целиком (живой прогон: 404 у камеры вне монтажа стоил 149
// кадров по 94 мс вместо 22).
function ipvFrameFail(im,ci,url){
  if(im._counted)return;
  im._counted=true;
  // Показывается ли эта камера в куске. Границы куска приезжают в адресе (IPV_FRANGE),
  // а нет их только у живого превью — там «куска» нет вовсе, и считаем как раньше.
  let mine=!IPV_FRANGE;
  if(!mine){
    const f0=IPV_FRANGE[0],f1=f0+IPV_FRANGE[1],fps=IPV.fps||60,segs=IPV.segs||[];
    for(let i=0;i<segs.length;i++){const g=segs[i];
      if(+g.ci!==+ci)continue;
      // Куски EDL режем по границам кадра ролика — теми же номерами, что и кусок.
      if(Math.round(+g.te*fps)>f0&&Math.round(+g.ts*fps)<f1){mine=true;break;}}}
  if(mine){
    IPV_FMISSN.set(ci,(IPV_FMISSN.get(ci)||0)+1);
    if((IPV_FMISSN.get(ci)||0)>=IPV_FMISS_MAX)IPV_FDEAD.add(ci);}
  if(IPV_FLOG.has(ci))return;      // про эту камеру уже сказано — причину не спрашиваем
  IPV_FLOG.add(ci);
  ipvFrameWhy(url,(why)=>ipvFrameMiss(ci,url,why,mine));}
// Почему картинка не приехала. У <img> причину не спросить: событие `error` статуса не
// несёт, а без причины строка «картинками не пришли» не отвечает на вопрос, ради которого
// её читают, — это папка не та, файла нет или сервер не отдал. Поэтому ОДИН раз на камеру
// спрашиваем сервер (HEAD по тому же адресу): 404 — файла нет, 200 — файл есть, а до
// браузера не доехал.
function ipvFrameWhy(url,done){
  if(typeof fetch!=='function')return done('картинка не загрузилась');
  fetch(url,{method:'HEAD'}).then((r)=>done(r.status===200
    ?'файл сервер отдаёт (200), а браузер его не получил':'сервер ответил '+r.status))
    .catch((e)=>done('запрос не прошёл: '+(e&&e.message?e.message:e)));}
function ipvFrameMiss(ci,url,why,mine){
  // В консоль, а не в uiLog: у страницы рендера нет ни панелей, ни лога, зато её
  // console.error забирает съёмщик и печатает строкой `#консоль` в лог рендера
  // (core/webrender/capture.mjs) — там её и увидит человек. ПРИЧИНА в строке обязательна:
  // без неё «картинками не пришли» не отличить от «папки нет» и разбирать нечем.
  // АДРЕС первого отсутствующего файла — тоже: счётчик и статус не говорят, ЧТО именно
  // просили, а по адресу видно и папку куска, и камеру, и номер кадра.
  if(typeof console!=='undefined'&&console.error)
    console.error('рендер: картинка камеры '+(ci+1)+' не пришла: '+url+' ('+why+') — '+
      (mine?'этот кадр идёт перемоткой <video>'
           :'камера в этом куске не показывается, пропажа не считается'));}
// Окно загруженных картинок: 1080x1920 в памяти — это 8 МБ на кадр, и весь кусок
// (десятки кадров) держать нельзя. Лишние отпускаем: src снимаем атрибутом (пустая
// строка — это запрос на саму страницу), элемент уходит из окна.
function ipvFrameTrim(){
  while(IPV_FIMGS.size>IPV_FMAX){
    const k=IPV_FIMGS.keys().next().value,im=IPV_FIMGS.get(k);
    if(IPV_FRAME&&IPV_FRAME.img===im)break;          // текущий кадр не отпускаем
    IPV_FIMGS.delete(k);
    try{im.removeAttribute('src');}catch(e){}}}
// Картинка готова к рисованию: загружена и раскодирована (drawImage до готовности
// рисует пустоту). Нет картинки — null: кадр рисуется перемоткой.
async function ipvFrameReady(ci,f){
  const im=ipvFrameImg(ci,f);if(!im)return null;
  try{await im.decode();}catch(e){im._bad=true;ipvFrameFail(im,ci,ipvFrameURL(ci,f));return null;}
  if(im._bad)return null;
  IPV_FMISSN.delete(ci);        // картинка доехала — счёт пропаж подряд с нуля
  return im;}
// Источник пикселей кадра: поставить готовую картинку исходного кадра (её читает
// ipvCamSrc). Возвращает картинку или null — «этот кадр рисуй перемоткой».
async function ipvFrameAt(sf){
  IPV_FRAME=null;
  if(!sf)return null;
  const im=await ipvFrameReady(sf.ci,sf.f);
  if(im)IPV_FRAME={ci:sf.ci,f:sf.f,img:im};
  return im;}
// Источник пикселей камеры ci: картинка вынутого кадра или <video>. ОДНА дверь на всю
// отрисовку кадра — второго решения «откуда пиксели» в файле нет.
function ipvCamSrc(ci){
  const fr=IPV_FRAME;
  if(fr&&fr.ci===ci&&fr.img)return fr.img;
  return (IPV.vids&&IPV.vids[ci])||null;}
// Следующий кадр грузим заранее: пока браузер сводит и снимает текущий, картинка
// следующего уже едет. Ошибка тут та же, что у кадра: камера пометится, и её кадры
// пойдут перемоткой (ipvFrameFail считает пропажи подряд).
// ГРАНИЦЫ КУСКА: за его последним кадром примерка не идёт — там кадр СОСЕДНЕГО куска, и
// картинки для него в этой папке нет вовсе (ipvFrameInChunk). Просьба о ней — 404 на
// каждом последнем кадре куска, а пропажа картинки уводила камеру на перемотку до конца
// куска: на куске в 300 кадров это стоило 149 кадров.
function ipvFramePrime(tm){
  if(!IPV_FRAMES||!ipvRenderMode())return;
  if(!ipvFrameInChunk(Math.round(tm*(IPV.fps||60))))return;
  const sf=ipvSrcFrameAt(tm);if(!sf)return;
  ipvFrameImg(sf.ci,sf.f);}
// Кадр ролика внутри куска? Границы приезжают в адресе страницы (fstart/fcount,
// core/webrender._chunk_url). Не пришли — границ нет: страница рендерит ролик целиком
// (живое превью), и примерка ничем не ограничена.
function ipvFrameInChunk(k){
  return !IPV_FRANGE||(k>=IPV_FRANGE[0]&&k<IPV_FRANGE[0]+IPV_FRANGE[1]);}
// Отрисовать кадр на момент tm и дождаться, что он готов к съёмке. Возвращает время
// кадра. Одна и та же tm дважды подряд даёт одинаковую картинку: ни текущего времени
// <video>, ни CSS-переходов, ни анимаций в расчёте не участвует.
// Параметр — tm, а не t: имя t занято функцией перевода (tests/test_ui_static.py).
async function ipvRenderAt(tm){
  if(!IPV.vids.length)return null;
  const tq=Math.max(0,Math.min(+tm||0,IPV.dur||(+tm||0)));
  IPV_RT.seek=0;IPV_RT.paint=0;IPV_RT.frames=0;
  const t0=performance.now();
  // Камера кадра и ЕЁ исходный кадр — по тому же правилу, что и выемка картинок: иначе
  // на страницу приехала бы чужая картинка (ipvSrcFrameAt, core.webrender.src_frame_at).
  const sf=ipvSrcFrameAt(tq);
  const ci=sf?sf.ci:((IPV.curCi>=0)?IPV.curCi:0);
  IPV.renderT=tq;                       // с этого места режим рендера включён (ipvRenderMode)
  ipvRenderModeOn();                    // переходы и анимации выключены классом на корне
  // Сцена выставляется на КАЖДОМ кадре: субтитры, вставки и наезд считаются на tm, и
  // «кадр уже на месте» не должно значить «кадр не считается». Перемотка ведущей камеры
  // здесь при этом НЕ делается — ipvSeekTo в режиме рендера её пропускает.
  ipvSeekTo(tq);                        // тот же «выставить всё на момент», что у протяжки ползунка
  // Кадр камеры — готовой картинкой, если её вынули до съёмки (ipvFrameAt): тогда
  // перематывать нечего вовсе. Нет картинки — кадр идёт перемоткой <video>, как раньше
  // (vFrameAt ставит камере её исходное время и ждёт кадр).
  const shot=await ipvFrameAt(sf);
  // Кадры всех <video> ждутся РАЗОМ: активная камера и видеовставки — разные декодеры, и
  // по очереди это была бы их СУММА (у владельца кадр ждал камеру, потом каждую вставку).
  // Перематывать больше нечего: время вставкам поставил ipvOverlayPlan внутри ipvSeekTo.
  // Ждать нечего, когда таких <video> нет: пустой querySelectorAll — это и есть «в кадре
  // их не видно». Видеовставки картинками не вынимаются: их время считает своя анимация.
  const ov=$('ipvins');
  const ivs=(ov&&ov.querySelectorAll)?[...ov.querySelectorAll('video')]:[];
  // Слои перехода — свои <video> в отдельной коробке (не в оверлее вставок: тот
  // перестраивается целиком): их кадры ждутся наравне с вставками, иначе снимок снял бы
  // переход с прошлого кадра или вовсе до метаданных.
  const tb=$('ipvtrans');
  const tv=(tb&&tb.querySelectorAll)?[...tb.querySelectorAll('video')].filter(v=>!v._dead):[];
  await Promise.all([shot?null:vFrameAt(IPV.vids[ci],sf?ipvCamTimeAt(ci,tq):null),
    ...ivs.map(v=>vFrameAt(v,null)), ...tv.map(v=>vFrameAt(v,null))]);
  // Мозаика вставок — ПОСЛЕ ожидания их кадров и с перерисовкой (`force`): время вставке
  // ставит ipvOverlayPlan ещё до ожидания, и по кэшу холст остался бы с прошлым кадром.
  ipvInsMosaics(true);
  ipvCamPaint(ipvZoomAt(tq));           // холст — ПОСЛЕ кадров: пока декодер молчит, рисовать нечего
  IPV_RT.seek+=(performance.now()-t0)/1000;
  IPV_RT.frames++;
  ipvFramePrime(tq+1/(IPV.fps||60));    // следующий кадр — заранее, пока снимают этот
  return tq;}
// Кадр отдан браузеру — снимать можно. Отдельной дверью, а не внутри ipvRenderAt:
// съёмщик зовёт её сам и мерит ею время рисования (paint), а seek считает ipvRenderAt.
async function ipvRenderPaint(){
  const t0=performance.now();
  await ipvRenderAssets();
  await ipvRenderPaintFrame();
  IPV_RT.paint+=(performance.now()-t0)/1000;
  return {seek:IPV_RT.seek,paint:IPV_RT.paint,frames:IPV_RT.frames};}
// Съёмщик зовёт ЭТУ дверь сам (capture.mjs), поэтому под именем обязана лежать ФУНКЦИЯ
// отрисовки. Объект статистики здесь уже стоял — и живой прогон падал на первом кадре:
// «TypeError: window.reelsiRenderPaint is not a function». Саму разбивку съёмщик берёт
// возвратом вызова, отдельным именем её выставлять не надо.
// Проверка `typeof window!=='undefined'`: этот же файл грузят node-стенды предпросмотра
// (tests/test_intro_*.py и соседние) — браузера там нет, и обращение к window роняло их
// на загрузке файла («ReferenceError: window is not defined»), хотя проверяют они
// отрисовку интро, а не двери рендера.
if(typeof window!=='undefined')window.reelsiRenderPaint=ipvRenderPaint;

// ---- музыка превью: как в рендере (уровень MUSIC_DB), синхронно с плеером ----
// Отдельный <audio> на весь предпросмотр: старт/пауза/перемотка по IPV, позиция = позиция
// монтажа (в AE музыка — слой под всем роликом, так и звучит). Источник берётся у КЛИПА
// (`musicTrack`), а не из общих полей шага 3: режим и папка музыки теперь у стиля,
// переопределение — у клипа, а выбранный при «случайно» трек закреплён в job.music_pick.
// Поэтому превью и сборка играют ОДИН И ТОТ ЖЕ файл, а не два независимых случайных.
// url-режим (ссылка) сами не скачиваем: трек качается кнопкой «Скачать» в блоке музыки,
// до неё играть нечего — ползунок громкости остаётся рабочим (тишина).
let MUSIC_EL=null,MUSIC_KEY='';
// Тройка полей музыки для сборки/плана — ОДНА дверь на оба запроса (пишет их
// 90-ae.js: musicJobFields/musicPickDir там же, где jobForBuild). Превью зовёт её,
// когда собирает тело /api/scene: разъехавшись, превью и .jsx взяли бы разные треки.
function musicKey(){const c=(typeof curAE!=='undefined'&&curAE>=0)?CLIPS[curAE]:null;
  const m=effMusic(c);
  return m.mode==='off'?'off':(m.mode+'|'+m.src+'|'+musicPickDir(c));}
function musicEnsure(){audioGraph();if(!AUDIO||!MG)return false;
  if(MUSIC_EL)return true;
  MUSIC_EL=document.createElement('audio');MUSIC_EL.preload='auto';
  MUSIC_EL.volume=MEDIA_VOL;   // тот же множитель прослушивания, что у голоса (см. applyMediaVol)
  try{AUDIO.createMediaElementSource(MUSIC_EL).connect(MG);}catch(e){MUSIC_EL=null;return false;}
  document.body.appendChild(MUSIC_EL);return true;}
async function musicPick(){
  if(!musicEnsure())return;
  const c=(typeof curAE!=='undefined'&&curAE>=0)?CLIPS[curAE]:null;
  const m=effMusic(c);let p='';
  if(m.mode==='file'||m.mode==='random')p=m.src||'';
  else if(m.mode!=='off'){   // url: трек ещё не скачан — подберём путь, если он уже в папке
    p='';}
  if(p)MUSIC_EL.src='/api/media?path='+encodeURIComponent(p);
  else MUSIC_EL.removeAttribute('src');
  if(IPV.playing)musicElSync();}   // трек сменился во время игры — подхватить позицию и играть
// Элемент музыки ведёт ОТДЕЛЬНАЯ дверь `musicElSync`: имя `musicSync` занято в
// 95-styles.js (сброс закреплённого трека при смене папки/режима клипа), а объявления
// функций в общем скоупе перекрывают друг друга — победил бы последний файл, и плеер
// звал бы чужую функцию (или она возвращалась бы сразу, не дойдя до звука).
function musicElSync(){if(!musicEnsure())return;
  if(AUDIO&&AUDIO.state==='suspended')AUDIO.resume().catch(()=>{});
  const k=musicKey();
  if(k!==MUSIC_KEY){MUSIC_KEY=k;MUSIC_EL.removeAttribute('src');musicPick();return;}   // режим/трек сменились — пусть подберётся
  if(!MUSIC_EL.src)return;                        // url-режим или трек ещё не выбран
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
function ipvZoomAt(tm,target){
  let z=IPV.plan&&IPV.plan.zoom;
  if(target==='cam2'||target===1)z=z&&z.cam2;
  else if(target&&typeof target==='object')z=target;
  if(!z||!z.keys||!z.keys.length)return 1;
  const fps=IPV.fps||60;
  // квантование времени к кадру композиции убирает дрожание рендера между кадрами
  const tq=Math.round(tm*fps)/fps;
  const pct=keysAt((z.keys||[]).map(k=>[k[0]/fps,k[1]]),z.ease,tq,z.holds!=null?z.holds:z.hold);
  return ((z.fit==null?100:z.fit)/100)*(pct/100);}
// Точка наезда Камеры 2 из плана ([cx,cy] в долях кадра) или null, когда Камера 2
// неактивна: план несёт `zoom.cam2` только при активности (зум, fit, pan, rot, cx/cy).
function ipvCam2Point(){const z=IPV.plan&&IPV.plan.zoom;const c=z&&z.cam2;
  if(!c)return null;
  return [c.cx!=null?c.cx:0.5,c.cy!=null?c.cy:0.5];}
function ipvZoom(tm){const s=ipvZoomAt(tm);
  const pl=IPV.plan;
  // точка наезда камеры: масштабируем кадр от неё, а не от центра — в AE
  // нул Камеры 1 имеет anchor/position от точки наезда, и неподвижна именно она
  const cx=((pl&&pl.zoom&&pl.zoom.cx)!=null)?pl.zoom.cx:0.5;
  const cy=((pl&&pl.zoom&&pl.zoom.cy)!=null)?pl.zoom.cy:0.5;
  ipvRotoMaskZoom(s,cx,cy);      // подсказка «низ маски рото» едет вместе с кадром
  ipvCamPaint(s);}               // кадр рисует canvas: CSS-масштаб видео дрожал, вырезка — нет
function ipvCamShift(tm, target){
  const pl=IPV.plan;
  const isCam2=(target==='cam2'||target===1);
  const z=isCam2?(pl&&pl.zoom&&pl.zoom.cam2):(pl&&pl.zoom);
  const pan=(z&&z.pan)||[0,0];
  let off=0;
  const fol=z&&z.follow;
  if(fol&&fol.keys&&fol.keys.length){
    const fps=IPV.fps||60;
    const tt=(tm!=null)?tm:((typeof ipvNow==='function')?ipvNow():0);
    const tq=Math.round(tt*fps)/fps;
    const keys=fol.keys.map(k=>[k[0]/fps,k[1]]);
    off=keysAt(keys,(fol.ease&&!fol.linear)?fol.ease:'linear',tq,false)||0;
  }
  return [(pan[0]||0)+off, pan[1]||0];
}
// ---- одна матрица кадра Камеры 1 / Камеры 2 ----
// Точка ИСХОДНИКА (px композиции от его центра при заполнении кадра) -> экран (px композиции
// от левого верхнего угла), матрица 2D (a,b,c,d,e,f). Модель как в AE после ZE:
//   экран = C + S·(R·p − C_c) + T,
// где C — точка наезда (cx*W, cy*H от левого верхнего угла), C_c — она же от центра кадра,
// S = ipvZoomAt(tm, target) (заполнение уже внутри ключей), R — горизонт `rot` вокруг центра
// ИСХОДНИКА, T = ipvCamShift(tm, target) = pan + слежение.
// Проверка модели: при S=1, rot=0, T=0 центр исходника встаёт в центр кадра, а точка наезда
// неподвижна при любом S. −C_c стоит ПОСЛЕ R, поэтому поворот центра исходника не двигает
// (в AE поворачивается слой камеры вокруг своего якоря, а не нул). Второй копии правила не
// заводить: кадр и подсказка рото обязаны считать одно и то же.
function ipvCamMatrix(tm, target){
  const pl=IPV.plan,W=pl?pl.w:1080,H=pl?pl.h:1920;
  const isCam2=(target==='cam2'||target===1);
  const z=isCam2?(pl&&pl.zoom&&pl.zoom.cam2):(pl&&pl.zoom);
  const s=isCam2?ipvZoomAt(tm,'cam2'):ipvZoomAt(tm);
  const cx=((z&&z.cx)!=null)?z.cx:0.5;
  const cy=((z&&z.cy)!=null)?z.cy:0.5;
  const rad=((z&&z.rot)||0)*Math.PI/180;
  const co=Math.cos(rad),si=Math.sin(rad);
  const shift=(typeof ipvCamShift==='function')?ipvCamShift(tm, target):((z&&z.pan)||[0,0]);
  const dx=(cx-0.5)*W,dy=(cy-0.5)*H;                          // точка наезда от центра кадра (C_c)
  return [s*co, s*si, -s*si, s*co,
          cx*W+(shift[0]||0)-s*dx,
          cy*H+(shift[1]||0)-s*dy];}
// ---- Lumetri в превью ----
// ПРИБЛИЖЕНИЕ: настоящие формулы Lumetri закрыты (плагин), поэтому модель снята
// ЗАМЕРОМ эталона AE: кадр нашего рендера без цвета (lm_on=false) и тот же кадр из
// exp/C1476.mov, пары пиксель-в-пиксель, медиана по корзинам яркости. Что показал замер:
//   * экспозиция — СТОПЫ в линейном свете (как было): 2^ex множит линейную яркость.
//     Здесь стояло `x * 2^ex` прямо по гамме, и превью выходило на 19-22 % светлее AE;
//   * тона (светлые/тени/белые/тёмные) у AE — МНОЖИТЕЛЬ к линейной яркости, а не
//     добавка к гамма-значению: добавка темнила тени и не поднимала середину — отсюда
//     «наш кадр темнее AE» и средняя яркость на 5.5 % ниже эталона;
//   * перекрёстной зависимости каналов нет (при одном входе канала выход один и тот же
//     при любой яркости соседей), но каналы идут РАЗНЫМИ кривыми: наш вход против
//     импорта AE даёт R ниже, B выше. Отсюда поканальная поправка входа;
//   * в глубоких тенях наш кадр и импорт AE сходятся, расхождение каналов растёт к свету.
// Все константы приближения собраны ЗДЕСЬ, чтобы правка была в одном месте.
const IPV_LM_HL=0.48, IPV_LM_SH=1.05, IPV_LM_WH=0.15, IPV_LM_BL=0.15;  // амплитуды тонов
const IPV_LM_HL_LO=0.004, IPV_LM_HL_HI=0.256, IPV_LM_HL_TOP=0.85;      // полоса светлых
const IPV_LM_SH_LO=0.035, IPV_LM_SH_HI=0.09, IPV_LM_SH_TOP=0.455;      // полоса теней
const IPV_LM_GAIN_R=1.04, IPV_LM_GAIN_G=1, IPV_LM_GAIN_B=0.94;         // поправка входа
const IPV_LM_GAIN_LO=0.027, IPV_LM_GAIN_HI=0.055;                      // она растёт от чёрного
const IPV_LM_GAIN_FADE=0.6, IPV_LM_GAIN_END=0.9;                       // и сходит у белого
const IPV_LM_BAL=0.2;              // баланс: ±20% каналу при ±100 (температура/оттенок)
const IPV_LM_N=256;                // точек в таблице кривой (feComponentTransfer)
// Перевод sRGB <-> линейный свет: экспозиция Lumetri — это СТОПЫ, то есть множитель
// 2^ex к линейной яркости, а не к гамма-значению.
function ipvLmToLin(c){return (c<=0.04045)?(c/12.92):Math.pow((c+0.055)/1.055,2.4);}
function ipvLmToSrgb(c){return (c<=0.0031308)?(c*12.92):(1.055*Math.pow(c,1/2.4)-0.055);}
function ipvLmSmooth(a,b,x){       // smoothstep: 0 ниже a, 1 выше b, между — плавно
  if(b<=a)return x>=b?1:0;
  const u=Math.max(0,Math.min(1,(x-a)/(b-a)));
  return u*u*(3-2*u);}
// Вес ручки по уровню: 0 слева и справа, 1 в середине (полоса).
function ipvLmBand(lo,mid,hi,p){return ipvLmSmooth(lo,mid,p)*(1-ipvLmSmooth(mid,hi,p));}
// Кривая тона: яркость x (0…1) -> яркость y (0…1); значения — из plan.lumetri.
// ch (0/1/2) — номер канала: включает поканальную поправку входа. Её видят только
// таблицы фильтра; сама кривая при ch=undefined остаётся чистым Lumetri, поэтому
// при нулевых ручках она тождество, а экспозиция — ровно стопы.
function ipvLumetriTone(x,lm,ch){
  const l=lm||{};
  const ex=+l.exposure||0,ct=+l.contrast||0,hl=+l.highlights||0,
        sh=+l.shadows||0,wh=+l.whites||0,bl=+l.blacks||0;
  const gain=(ch==null)?1:(ch===0?IPV_LM_GAIN_R:(ch===2?IPV_LM_GAIN_B:IPV_LM_GAIN_G));
  // Поправка входа включается от уровня: в глубоких тенях каналы нашего кадра и
  // импорта AE совпадают, к свету расходятся, у белого снова сходятся.
  const w=(ch==null)?0:ipvLmSmooth(IPV_LM_GAIN_LO,IPV_LM_GAIN_HI,x)
    *(1-ipvLmSmooth(IPV_LM_GAIN_FADE,IPV_LM_GAIN_END,x));
  let u,y;
  if(ex!==0||w>0){                  // шаг есть — считаем в линейном свете
    u=ipvLmToLin(x)*(1+(gain-1)*w);
    if(ex!==0)u*=Math.pow(2,ex);
    y=ipvLmToSrgb(u);
  }else{y=x;}                       // ни шага, ни поправки — числа кадра не меняются
  // Тона — множитель к ЛИНЕЙНОЙ яркости (по замеру AE), а не добавка к гамма-значению.
  const k=1+(hl/100)*IPV_LM_HL*ipvLmBand(IPV_LM_HL_LO,IPV_LM_HL_HI,IPV_LM_HL_TOP,y)
          +(sh/100)*IPV_LM_SH*ipvLmBand(IPV_LM_SH_LO,IPV_LM_SH_HI,IPV_LM_SH_TOP,y)
          +(wh/100)*IPV_LM_WH*ipvLmSmooth(0.75,1,y)
          +(bl/100)*IPV_LM_BL*(1-ipvLmSmooth(0,0.25,y));
  if(k!==1)y=ipvLmToSrgb(ipvLmToLin(y)*k);
  if(ct!==0)y=0.5+(y-0.5)*(1+ct/100);                       // контраст вокруг середины
  return Math.max(0,Math.min(1,y));}
// Таблица IPV_LM_N точек — своя кривая на каждый канал (feFuncR/G/B): у каналов
// разные поканальные поправки входа.
function ipvLumetriTable(lm,ch){
  const out=[];
  for(let i=0;i<IPV_LM_N;i++)out.push(ipvLumetriTone(i/(IPV_LM_N-1),lm,ch).toFixed(5));
  return out.join(' ');}
// Фильтр превью: скрытый <svg> с фильтром id="ipvLumetri" (или "ipvLumetri2"). Пересобирается ТОЛЬКО при
// смене plan.lumetri / plan.lumetri2 (подпись): иначе строка таблицы собиралась бы на каждом кадре.
let IPV_LM_SIG=null;
let IPV_LM2_SIG=null;
function ipvLumetriFilter(ci){
  const pl=(typeof IPV!=='undefined'&&IPV.plan)?IPV.plan:null;
  const isCam2=(ci===1);
  const hasCam2Color=(isCam2 && pl && ('lumetri2' in pl));
  const lm=hasCam2Color ? pl.lumetri2 : (pl ? pl.lumetri : null);
  const fid=hasCam2Color ? 'ipvLumetri2' : 'ipvLumetri';
  const svgId=hasCam2Color ? 'ipvLumetri2Svg' : 'ipvLumetriSvg';
  if(!lm){
    if(hasCam2Color)IPV_LM2_SIG=null; else IPV_LM_SIG=null;
    return 'none';
  }
  const sig=JSON.stringify(lm);
  const curSig=hasCam2Color ? IPV_LM2_SIG : IPV_LM_SIG;
  if(sig===curSig&&$(svgId))return 'url(#'+fid+')';
  let svg=$(svgId);
  if(!svg){
    const host=$('ipvstage')||document.body;
    svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    svg.setAttribute('id',svgId);svg.setAttribute('aria-hidden','true');
    // именно нулевой размер, а не display:none: у скрытого фильтра браузер не считает
    // результат, и canvas нарисовал бы кадр без цвета
    svg.style.position='absolute';svg.style.width='0';svg.style.height='0';
    svg.style.overflow='hidden';svg.style.pointerEvents='none';
    host.appendChild(svg);}
  const temp=+lm.temp||0,tint=+lm.tint||0,sat=(lm.sat==null?100:+lm.sat);
  const kr=1+IPV_LM_BAL*temp/100, kb=1-IPV_LM_BAL*temp/100, kg=1-IPV_LM_BAL*tint/100;
  // color-interpolation-filters=sRGB: иначе браузер считает кривую в linearRGB и
  // приближение уезжает от картинки AE
  svg.innerHTML='<filter id="'+fid+'" x="0" y="0" width="100%" height="100%" '
    +'color-interpolation-filters="sRGB">'
    +'<feComponentTransfer>'
    +'<feFuncR type="table" tableValues="'+ipvLumetriTable(lm,0)+'"/>'
    +'<feFuncG type="table" tableValues="'+ipvLumetriTable(lm,1)+'"/>'
    +'<feFuncB type="table" tableValues="'+ipvLumetriTable(lm,2)+'"/>'
    +'</feComponentTransfer>'
    +'<feColorMatrix type="matrix" values="'
    +[kr,0,0,0,0, 0,kg,0,0,0, 0,0,kb,0,0, 0,0,0,1,0].join(' ')+'"/>'
    +'<feColorMatrix type="saturate" values="'+(sat/100)+'"/>'
    +'</filter>';
  if(hasCam2Color)IPV_LM2_SIG=sig; else IPV_LM_SIG=sig;
  return 'url(#'+fid+')';}
// Во сколько раз холст сжимает картинку кадра (доля единицы) — по ней видно, платит ли
// кадр за масштабирование деталью. Правило зума (core.webrender.frame_size) держит эту
// долю не ниже 1/зум: при 100 % картинка ровно во столько раз крупнее холста, во сколько
// максимальный зум больше единицы, а на самом зуме ложится пиксель в пиксель. Отдельной
// дверью, а не «на глаз»: этой же формулой проверка сверяет размер, который просит выемка.
function ipvCamDrawScale(imgShort,outShort){
  if(!imgShort||!outShort)return 0;
  return outShort/imgShort;}
// Кадр камеры рисуется НА canvas (выбор стенда): субпиксельные координаты стабильны, а
// CSS-трансформ на <video> дрожал по горизонтали при зуме. Видео спрятаны видимостью
// (см. ipvOpen), поэтому canvas рисуется КАЖДЫЙ кадр — и при s==1 тоже.
function ipvCamPaint(s){const cv=$('ipvcam');if(!cv)return;   // canvas нет — рисовать нечем
  const ci=(IPV.curCi>=0)?IPV.curCi:0;                        // активная камера (не выбрана — камера 0)
  // Пиксели кадра — картинка вынутого исходного кадра (режим рендера) или <video>:
  // решает ipvCamSrc, и это единственное место, где источник выбирается. Проверка
  // `typeof`: файл грузят и node-стенды предпросмотра, где этой двери нет.
  const v=(typeof ipvCamSrc==='function')?ipvCamSrc(ci):IPV.vids[ci];
  // Размер кадра у источника разный: у <video> это videoWidth, у картинки — naturalWidth.
  const vw=(v&&(v.videoWidth||v.naturalWidth))||0,vh=(v&&(v.videoHeight||v.naturalHeight))||0;
  if(!v||!vw||!vh||(v.readyState!=null&&v.readyState<2)){     // кадр ещё не готов — чистим холст
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
  // LUT камеры из профиля спикера накладывается на лету (lutApply, 86-lut.js).
  // Его canvas меньше кадра (не больше 1920 по большей стороне), поэтому координаты
  // вырезки пересчитываем в его пиксели: иначе зум и сдвиг поехали бы по картинке.
  const ks=lutKS(ci,v),skx=ks[0],sky=ks[1];
  ipvDrawFrame(c,ci,v,vw,vh,skx,sky,W,H,dpr);
  // Маркер точки наезда едет вместе с кадром: перекрестие рисуется по ТОЙ ЖЕ матрице,
  // что сейчас на холсте (см. zoomPickMark в 95-styles.js), а кадр перерисовывается
  // каждый тик проигрывания и каждую перемотку. Зовём здесь, а не в таймере: второго
  // места, где кадр меняется, нет; вне правки точки функция выходит по display.
  if(typeof zoomPickMark==='function')zoomPickMark();}
// ОДНА отрисовка кадра камеры: вырезка, матрица, цвет. Зовётся из ipvCamPaint (кадр
// превью) и из ipvRotoPaint (копия кадра ПОД маской рото). Второй копии правил
// «заполнение, матрица, LUT» быть не должно: рото-фигура обязана лежать на кадре
// пиксель-в-пиксель, и разойдись эти две двери — человек разъехался бы со своим кадром.
// `vw/vh` уже посчитаны вызывающим (у <video> это videoWidth, у картинки — naturalWidth).
// Геометрия одного кадра камеры: матрица холста, доля исходника (uv) и прямоугольник
// назначения (dst). Возвращается наружу, потому что ею же кладётся маска рото: она в mf
// раз мельче исходника, и её полный кадр обязан лечь ровно на тот же прямоугольник.
// Доли (uv), а не пиксели: у источника может быть свой размер (LUT-canvas, маска).
function ipvFrameGeom(ci,vw,vh,W,H,dpr){
  const pl=IPV.plan;
  if(ci>1){                                                   // перебивка 3+ камер: вырезка без сдвига и поворота
    const cx=((pl&&pl.zoom&&pl.zoom.cx)!=null)?pl.zoom.cx:0.5;  // точка наезда из плана
    const cy=((pl&&pl.zoom&&pl.zoom.cy)!=null)?pl.zoom.cy:0.5;
    const f=Math.max(W/vw,H/vh);                              // заполнение кадра
    const sw=W/f,sh=H/f;
    const _fr=(typeof camFrameOf==='function')?camFrameOf(ci):null;
    const _on=!!(_fr&&typeof camFrameDefault==='function'&&!camFrameDefault(_fr));
    const _d=_on?camFrameDim():{W:W,H:H};
    const _c=_on?camFrameCrop(vw,vh,_d.W,_d.H,_fr):[cx*(vw-sw),cy*(vh-sh),sw,sh];
    return {m:[1,0,0,1,0,0],uv:[_c[0]/vw,_c[1]/vh,_c[2]/vw,_c[3]/vh],dst:[0,0,W,H]};}
  // Камеры 1 и 2: рисуем ВЕСЬ исходник через единую матрицу кадра (ipvCamMatrix с ci).
  // Матрица живёт в px КОМПОЗИЦИИ, заполнение f считается по кадру композиции,
  // k переводит px композиции в px сцены.
  const Wc=(pl&&pl.w)||1080, Hc=(pl&&pl.h)||1920;
  const k=W/Wc;                                               // px сцены на px композиции
  const f=Math.max(Wc/vw,Hc/vh);                              // заполнение кадра в px композиции
  const _fr=(typeof camFrameOf==='function')?camFrameOf(ci):null;
  const _fz=(typeof camFrameZoom==='function')?camFrameZoom(_fr):1;
  const _sh=(typeof camFrameShift==='function')?camFrameShift(vw,vh,Wc,Hc,_fr):[0,0];
  const m=ipvCamMatrix((typeof ipvNow==='function')?ipvNow():0, ci);
  return {m:[dpr*k*m[0],dpr*k*m[1],dpr*k*m[2],dpr*k*m[3],dpr*k*m[4],dpr*k*m[5]],
          uv:[0,0,1,1],dst:[-vw*f*_fz/2+_sh[0],-vh*f*_fz/2+_sh[1],vw*f*_fz,vh*f*_fz]};}
function ipvDrawFrame(c,ci,v,vw,vh,skx,sky,W,H,dpr){
  const g=ipvFrameGeom(ci,vw,vh,W,H,dpr);
  const img=lutApply(ci,v);
  // Размер картинки — её собственный (LUT-canvas округляет пиксели): по нему считается
  // доля вырезки. Источника нет в пикселях (кадр не декодирован) — исходные размеры.
  const iw=(img&&img.width)||vw*skx, ih=(img&&img.height)||vh*sky;
  c.setTransform(g.m[0],g.m[1],g.m[2],g.m[3],g.m[4],g.m[5]);
  c.filter=ipvLumetriFilter(ci);                                // цвет камер из плана
  c.drawImage(img,g.uv[0]*iw,g.uv[1]*ih,g.uv[2]*iw,g.uv[3]*ih,
    g.dst[0],g.dst[1],g.dst[2],g.dst[3]);
  c.filter='none';                                              // состояние холста не копим
  return g;}
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
  // Рамка кадра Камеры 1 (core/frame.py): слой увеличен и смещён. Смещение входит в тот
  // же transform ДО масштаба нула (в AE оно внутри слоя, то есть множится на зум), а
  // полоса «низ маски» встаёт по низу ИСХОДНИКА, а не стойки — иначе она врёт тем
  // сильнее, чем крупнее рамка.
  const v1=IPV.vids[0];
  const fr1=(typeof camFrameOf==='function')?camFrameOf(0):null;
  const frOn=!!(v1&&v1.videoWidth&&fr1&&typeof camFrameDefault==='function'&&!camFrameDefault(fr1));
  const frSh=frOn?camFrameShift(v1.videoWidth,v1.videoHeight,(pl&&pl.w)||1080,(pl&&pl.h)||1920,fr1):[0,0];
  const panX=(shift[0]+(frOn?frSh[0]*s:0))*k, panY=(shift[1]+(frOn?frSh[1]*s:0))*k;
  const band=m.querySelector('.rmband');
  if(band){
    const pct=+(m._pct!=null?m._pct:0);
    if(frOn){
      const Wc=(pl&&pl.w)||1080, Hc=(pl&&pl.h)||1920, kk=W/Wc;
      const f1=Math.max(Wc/v1.videoWidth,Hc/v1.videoHeight)*camFrameZoom(fr1);
      const srcH=v1.videoHeight*f1*kk;                   // высота исходника на стойке, px
      band.style.height=(pct/100*srcH)+'px';
      band.style.bottom=(r.height/2-frSh[1]*kk-srcH/2)+'px';
    }else{
      band.style.height=pct+'%';                         // рамки нет — полоса как была
      band.style.bottom='';
    }
  }
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
function ipvCamChild(px, py, s, tm, which){
  const pl=IPV.plan,W=pl?pl.w:1080,H=pl?pl.h:1920;
  const isCam2=(which==='cam2'||which===1);
  const z=isCam2?(pl&&pl.zoom&&pl.zoom.cam2):(pl&&pl.zoom);
  const cx=((z&&z.cx)!=null)?z.cx:0.5;
  const cy=((z&&z.cy)!=null)?z.cy:0.5;
  const shift=(typeof ipvCamShift==='function')?ipvCamShift(tm, which):((z&&z.pan)||[0,0]);
  const dx=(cx-0.5)*W, dy=(cy-0.5)*H;   // точка наезда от центра кадра
  return [s*px+(1-s)*dx+(shift[0]||0), s*py+(1-s)*dy+(shift[1]||0)];}

// ---- РОТО в превью: вырезанная фигура спикера поверх слоя по layer_order ----
// До этой правки рото было видно только в финальном рендере: на таймлайне стояла полоса
// «здесь рото», а в кадре — ничего. Считает маски кнопка «Рассчитать рото и трекинг»
// (её дверь кладёт готовые .mp4 в тот же кэш, что сборка), превью только ПОКАЗЫВАЕТ.
//
// Композит: кадр СВОЕЙ камеры куска × luma-маска = фигура с альфой. Ровно то же делает
// .jsx: слой-копия камеры с Track Matte LUMA (template.py, addRoto). Кадр маски берётся
// по времени ВНУТРИ куска (t − ts): файл маски начинается с начала куска, и в AE у слоя
// маски startTime=ts (template.py, `mk.startTime=rr.ts`) — превью обязано брать тот же
// кадр. А камера — общим ipvDrawFrame (та же матрица, заполнение, LUT и рамка кадра),
// иначе фигура разъехалась бы со своим кадром.
//
// Холст ТРОЙНОЙ: на ДАННЫЙ холст кладётся готовый композит, РАБОЧИЙ держит кадр камеры,
// АЛЬФА — маску с яркостью в альфе (SVG-фильтр ipvLumaAlphaFilter: canvas 2D сам так не
// умеет, а multiply дал бы непрозрачный чёрный фон вместо выреза). Кадр камеры остаётся
// там, где маска светлая, — `destination-in` по альфе маски.
let IPV_ROTO='';      // xml, для которого посчитан кэш масок (открыт другой клип — сброс)
const IPV_ROTOSEQ=[]; // <video> масок по кускам: элемент на кусок, src не переставляем
let IPV_ROTOACC=-1;   // время, на котором холст уже сведён (работает и на паузе)
let IPV_ROTODRAWN=-1; // тот же сброс для слоя (кадр мог не смениться, а куски — да)
// Путь куска в момент tm или null: куски не пересекаются, но ищем ПЕРВЫЙ подходящий —
// так же, как camApply выбирает ракурс по IPV.segs.
function ipvRotoAt(tm){
  const arr=IPV.roto||[];
  for(const p of arr)if(tm>=p.ts&&tm<+p.te)return p;
  return null;}
// Кадр маски куска: t − ts — время ВНУТРИ куска. Файл маски начинается с его начала
// (проверено ffprobe: кусок ts=0..te=4.567 → маска 4.571 с), и в AE у слоя маски
// startTime=ts — значит время кадра в исходнике маски и есть t − ts. Прибавка src_start
// (как было) уводила время в конец файла: всё время показывался последний кадр маски.
// Секунды округляются к номеру кадра (как ipvNowFrame), иначе на паузе дрожал бы выбор
// кадра.
function ipvRotoMaskTime(p,tm,fps){
  const f=Math.max(1,fps||60);
  return Math.max(0,Math.round((tm-(+p.ts||0))*f)/f);}
// «Уже на кадре?» — тот же допуск, что у всех перемоток превью (vidSeekTol): половина
// кадра в рендере, 0.4 с в игре. Второй копии допуска быть не должно.
function ipvRotoFrameStands(v,p,tm,fps){
  if(!v||v.seeking)return false;
  return Math.abs((+v.currentTime||0)-ipvRotoMaskTime(p,tm,fps))<=vidSeekTol();}
// Достать <video> маски куска (или null, если ссылки на файл нет): элементы живут по
// номеру куска, `src` ставится один раз. `muted`/`playsInline` — чтобы браузер не
// открывал звуковую дорожку и не просил жест на воспроизведение.
function ipvRotoVideo(ri,p){
  const arr=IPV.roto||[];
  const old=IPV_ROTOSEQ[ri];
  if(old&&old.dataset&&old.dataset.mask===p.mask)return old;
  if(old){try{old.pause();}catch(e){}}
  const el=document.createElement('video');
  el.className='ipvroto';
  el.muted=true;el.playsInline=true;el.preload='auto';
  el.dataset.mask=p.mask||'';
  el.src='/api/media?path='+encodeURIComponent(p.mask);
  // Кадр маски приехал (`loadeddata`) или перемотка доехала (`seeked`) — сводим слой
  // САМИ. Без этих подписок холст оставался пустым до следующего внешнего вызова
  // (пауза: сцена не пересчитывается, и подписок на кадре больше неоткуда взять).
  // Подписка одна на элемент: он живёт по номеру куска, `src` не переставляется.
  const again=()=>{IPV_ROTOACC=-1;IPV_ROTODRAWN=-1;ipvRoto(ipvNow());};
  el.addEventListener('loadeddata',again);
  el.addEventListener('seeked',again);
  IPV_ROTOSEQ[ri]=el;
  return el;}
// Холст слоя: создаётся ОДИН раз, поверх видео камер и под интро/вставками (z-index —
// по layer_order, как у всех слоёв превью).
function ipvRotoCanvas(){
  const cv=$('ipvroto');if(cv)return cv;
  const st=$('ipvstage');if(!st||!st.insertBefore)return null;
  const io=$('ipvintro');
  const box=document.createElement('canvas');
  box.id='ipvroto';box.className='ipvroto';
  box.style.position='absolute';box.style.inset='0';
  box.style.width='100%';box.style.height='100%';
  box.style.pointerEvents='none';
  if(io)st.insertBefore(box,io);else st.appendChild(box);
  return box;}
// Показать слой: z-index из плана. Слой рото — ОБЫЧНЫЙ слой layer_order (дефолт
// ['subs','video','roto','photo','intro']: рото над кадром и фото, под видео и интро).
// Группа интро на видеовставке поднимается на 11 (ipvIntro) и уходит выше рото — как
// moveToBeginning в AE.
function ipvRotoShow(cv){
  const pl=IPV.plan;
  const order=(pl&&pl.layer_order)||['subs','video','roto','photo','intro'];
  const i=order.indexOf('roto');
  // display именно 'block': у canvas.ipvroto в CSS стоит display:none, и пустая строка
  // вернула бы это правило — холст остался бы невидимым.
  cv.style.display='block';cv.style.zIndex=String(i>=0?(10-i):8);}
function ipvRotoClear(){
  const cv=$('ipvroto');
  if(cv){const c=cv._c||(cv.getContext&&cv.getContext('2d'));
    if(c)c.clearRect(0,0,cv.width,cv.height);cv.style.display='none';}
  IPV_ROTOACC=-1;IPV_ROTODRAWN=-1;}
// Кадр камеры куска ещё не декодирован — вырезать нечего: сведение вернуло бы ПУСТОЙ
// холст, а время уже было бы помечено сведённым (держали 0 непрозрачных пикселей до
// следующей перемотки). Ждём кадр камеры её же событиями — подписка одна на элемент.
function ipvRotoWaitCam(v){
  if(!v||!v.addEventListener||v._rotoCamBind)return;
  v._rotoCamBind=true;
  const again=()=>ipvRoto(ipvNow());
  v.addEventListener('loadeddata',again);
  v.addEventListener('seeked',again);}
// Слой рото на момент tm: без масок — слой снят; куска нет — холст очищен (в кадре не
// должно остаться фигуры прошлого куска). Тяжёлое сведение холста не гоняем, пока звучит
// то же время (пауза, повторный ipvUI на том же кадре).
function ipvRoto(tm){
  tm=+tm||0;
  if(IPV_ROTO&&IPV.xml&&IPV_ROTO!==IPV.xml){IPV_ROTO='';IPV.roto=[];IPV_ROTOSEQ.length=0;}
  const arr=IPV.roto||[];
  if(!arr.length){ipvRotoClear();return;}
  const p=ipvRotoAt(tm);
  if(!p){ipvRotoClear();return;}
  const cv=ipvRotoCanvas();if(!cv)return;
  ipvRotoShow(cv);
  const ri=arr.indexOf(p);
  const el=ipvRotoVideo(ri,p);          // подписки на loadeddata/seeked — там же
  const fps=IPV.fps||60;
  const key=Math.round(tm*fps)/fps;     // кадр композиции: тот же квант, что у зума
  // Метаданных маски ещё нет («сведённым» этот кадр не помечаем — сведёт `loadeddata`).
  if(!el||!el.videoWidth){
    const c0=cv._c||(cv.getContext&&cv.getContext('2d'));
    if(c0)c0.clearRect(0,0,cv.width,cv.height);
    return;}
  if(key===IPV_ROTOACC&&key===IPV_ROTODRAWN)return;
  // Кадр маски — по времени куска; не стоит — перематываем (кадр доедет и сведётся сам,
  // см. `seeked` в ipvRotoVideo), а сейчас сводим тем, что есть: пустой слой хуже кадра
  // прошлого места, который через мгновение сменится.
  if(!ipvRotoFrameStands(el,p,tm,fps)){
    try{el.currentTime=ipvRotoMaskTime(p,tm,fps);}catch(e){}}
  if(ipvRotoPaint(cv,el,p)){IPV_ROTOACC=key;IPV_ROTODRAWN=key;}
  else{IPV_ROTOACC=-1;IPV_ROTODRAWN=-1;}}   // не свелось — кадр НЕ помечаем сведённым
// Фильтр «яркость маски -> альфа» для слоя рото. Canvas 2D не умеет брать альфу из
// яркости: multiply дал бы НЕПРОЗРАЧНЫЙ чёрный фон вместо выреза, а у grayscale-маски
// альфа непрозрачна везде. Матрица кладёт яркость в альфу (RGB — в белый, чтобы
// последующее умножение на кадр не темнило). Цвет считаем в sRGB: RVM кладёт яркость
// маски как есть, и luma-матте в AE берёт её из того же grayscale-кадра.
const IPV_LUMA_A='0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0.2126 0.7152 0.0722 0 0';
function ipvLumaAlphaFilter(){
  const id='ipvLumaAlpha';
  if(!$('ipvLumaAlphaSvg')){
    const host=$('ipvstage')||document.body;
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    svg.setAttribute('id','ipvLumaAlphaSvg');svg.setAttribute('aria-hidden','true');
    // нулевой размер, а не display:none: у скрытого фильтра браузер не считает результат
    svg.style.position='absolute';svg.style.width='0';svg.style.height='0';
    svg.style.overflow='hidden';svg.style.pointerEvents='none';
    svg.innerHTML='<filter id="'+id+'" x="0" y="0" width="100%" height="100%" '
      +'color-interpolation-filters="sRGB"><feColorMatrix type="matrix" values="'
      +IPV_LUMA_A+'"/></filter>';
    host.appendChild(svg);}
  return 'url(#'+id+')';}
// Свести кадр: кадр камеры куска на РАБОЧИЙ холст (той же ipvDrawFrame), затем вырезать
// его маской (яркость -> альфа) и положить готовое на холст слоя. Ровно то же делает
// .jsx: копия кадра камеры с Track Matte LUMA.
// Возвращает true, только если фигура действительно сведена: вызывающий (ipvRoto) по
// этому помечает кадр сведённым, а несведённый обязан пересвестись сам.
function ipvRotoPaint(cv,el,p){
  const st=$('ipvstage');if(!st)return false;
  const r=st.getBoundingClientRect();
  const W=Math.round(r.width),H=Math.round(r.height);
  if(W<1||H<1)return false;
  const dpr=window.devicePixelRatio||1;
  const pw=Math.round(W*dpr),ph=Math.round(H*dpr);
  let c=cv._c;
  if(!c||cv.width!==pw||cv.height!==ph){
    cv.width=pw;cv.height=ph;cv.style.width=W+'px';cv.style.height=H+'px';
    c=cv.getContext('2d');
    c.imageSmoothingEnabled=true;c.imageSmoothingQuality='high';
    cv._c=c;}
  if(!cv._work){
    const wc=document.createElement('canvas');
    wc.width=pw;wc.height=ph;
    cv._work=wc;cv._wc=wc.getContext('2d');}
  if(!cv._alpha){
    const ac=document.createElement('canvas');
    ac.width=pw;ac.height=ph;
    cv._alpha=ac;cv._ac=ac.getContext('2d');}
  const wc=cv._work,w=cv._wc,ac=cv._alpha,a=cv._ac;
  if(wc.width!==pw||wc.height!==ph){wc.width=pw;wc.height=ph;}
  if(ac.width!==pw||ac.height!==ph){ac.width=pw;ac.height=ph;}
  c.setTransform(1,0,0,1,0,0);c.clearRect(0,0,cv.width,cv.height);
  w.setTransform(1,0,0,1,0,0);w.clearRect(0,0,wc.width,wc.height);
  const ci=(p.ci!=null)?p.ci:0;
  const v=(typeof ipvCamSrc==='function')?ipvCamSrc(ci):(IPV.vids&&IPV.vids[ci]);
  const vw=(v&&(v.videoWidth||v.naturalWidth))||0,vh=(v&&(v.videoHeight||v.naturalHeight))||0;
  // Кадра камеры нет — вырезать нечего. Возврат false: кадр НЕ помечается сведённым, и
  // слой дорисуется сам, когда камера отдаст кадр (подписка в ipvRotoWaitCam).
  if(!v||!vw||!vh||(v.readyState!=null&&v.readyState<2)){ipvRotoWaitCam(v);return false;}
  const ks=(typeof lutKS==='function')?lutKS(ci,v):[1,1];
  // Кадр камеры — в РАБОЧИЙ холст, той же вырезкой и матрицей, что показаны на экране
  // (ipvDrawFrame — ОДНА на оба места): фигура обязана лежать на кадре пиксель-в-пиксель.
  // `g` — геометрия кадра: ею же кладётся маска, иначе зум/сдвиг/слежение разъехались бы.
  const g=ipvDrawFrame(w,ci,v,vw,vh,ks[0],ks[1],W,H,dpr);
  const mw=el.videoWidth||0,mh=el.videoHeight||0;
  let drawn=false;
  if(mw&&mh&&g){
    // Маска мельче исходника (mf), но её ПОЛНЫЙ кадр — это тот же кадр камеры: кладём
    // её на тот же прямоугольник (g.uv — доля исходника, g.dst — куда), яркость уходит
    // в альфу SVG-фильтром. Кадр маски берётся по времени куска (ipvRotoMaskTime).
    a.setTransform(1,0,0,1,0,0);a.clearRect(0,0,ac.width,ac.height);
    a.filter=ipvLumaAlphaFilter();
    a.setTransform(g.m[0],g.m[1],g.m[2],g.m[3],g.m[4],g.m[5]);
    a.drawImage(el,g.uv[0]*mw,g.uv[1]*mh,g.uv[2]*mw,g.uv[3]*mh,
      g.dst[0],g.dst[1],g.dst[2],g.dst[3]);
    a.filter='none';a.setTransform(1,0,0,1,0,0);
    // destination-in: остаётся кадр камеры ровно там, где маска светлая (альфа готового
    // слоя = яркость маски) — это и есть вырезанная фигура спикера.
    w.setTransform(1,0,0,1,0,0);
    w.globalCompositeOperation='destination-in';
    w.drawImage(ac,0,0);
    w.globalCompositeOperation='source-over';
    drawn=true;}
  c.drawImage(wc,0,0);
  return drawn;}
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
  // Длительность строки — длина КОНТЕНТА (tl.dur = plan.dur), а не всего превью:
  // хвостовой дисклеймер удлиняет предпросмотр (IPV.dur), но полоса обязана дойти
  // до конца ровно к концу ролика, а не тянуться по копии дисклеймера.
  const dur=tl.dur||(pl.disclaimer&&pl.dur)||IPV.dur||1;
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
// ---- дисклеймер по плану: ровно как в AE ----
// В AE дисклеймер — один текстовый слой: центрированный абзац, Position = [центр кадра, DISC_Y],
// прозрачность 100 до DISC_END−0.35 и линейно к 0 за 0.35 с, эффект Glo2 радиусом 42.
// Числа плана — те же подстановки, что уехали в .jsx (plan_decor): кегль, положение x/y,
// шаг строк и время. Своей копии формул здесь нет — иначе превью разъезжалось бы с .jsx.
// Якорь AE — базовая линия ПЕРВОЙ строки, а не центр блока: вертикаль — по `asc` плана
// (подъём строки над базовой линией), а не подгонкой замером — см. ниже.
// Хвостовая копия (end_copy) — второй показ на конце ролика: плана нет — элемента нет.
function ipvDisc(tm){
  const el=$('ipvdisc');if(!el)return;
  const pl=IPV.plan;const d=pl&&pl.disclaimer;
  const txt=el.querySelector('.pvdis_text');
  // Скрывая слой, гасим и прозрачность: иначе в свойстве остался бы прошлый кадр
  // (в браузере это не видно, но состояние слоя врало бы — и тест, и глаз по devtools).
  const hide=()=>{el.style.display='none';el.style.opacity='0';};
  if(!d||!txt){hide();return;}
  const w=pl.w||1080;
  const size=d.size!=null?d.size:47;         // кегль УЖЕ подобран Python'ом: второй копии нет
  // Окно показа: головной блок до t_end, хвостовая копия — своё окно на конце ролика.
  const ec=d.end_copy;
  let tStart=0,tEnd=d.t_end!=null?d.t_end:1.35;
  if(ec&&tm>=ec.t0){tStart=ec.t0;tEnd=ec.t1;}
  const fade=d.fade!=null?d.fade:0.35;       // те же 0.35, что у ключей Opacity в .jsx
  if(tm<tStart||tm>tEnd){hide();return;}
  el.style.display='';
  // Кегль и шаг строк — в cqw (проценты ширины кадра): те же единицы, что у подписи.
  el.style.fontSize=(size/w*100).toFixed(3)+'cqw';
  const lead=d.lead!=null?d.lead:null;
  if(lead!=null)el.style.lineHeight=(lead/size).toFixed(4);
  else el.style.lineHeight='';
  // Горизонталь: блок шириной в кадр (правило .ipvdisc), левый край = x − W/2, значит центр
  // блока лёг ровно на Position.x, а строки центрирует text-align:center того же правила.
  // Никакого transform: сдвиг уже сидит в left, и translateX(-50%) уводил блок ещё на
  // полкадра влево — строки занимали −540…526 px кадра и резались левым краем.
  el.style.left=(((d.x!=null?d.x:w/2)/w-0.5)*100).toFixed(3)+'cqw';
  // Вертикаль — числом плана, без подгонки замером: Position слоя в AE — базовая линия
  // ПЕРВОЙ строки, а `asc` в плане и есть её подъём над верхом блока при этом кегле.
  // Верх строки = (y − asc), как считает Python (fonts.ink_extent, тот же замер, что у
  // disc_gap). Своего числа превью не берёт, а браузерный замер полулидинга дал бы
  // другой верх — превью разъезжалось бы с .jsx (так и было: слой уезжал по вертикали).
  el.style.top=((d.y-(d.asc!=null?d.asc:0))/w*100).toFixed(3)+'cqw';
  // Glo2 Radius 42 px кадра: у CSS-тени нет интенсивности, поэтому свечение приближаем
  // белой тенью того же радиуса — как у текста интро (своей копии числа 42 тут нет).
  el.style.textShadow='0 0 '+((d.glow!=null?d.glow:42)/w*100).toFixed(3)+'cqw #fff';
  el.style.opacity=String(Math.max(0,Math.min(1,(tEnd-tm)/fade)));
  const fv=ipvFontFor(d.font||'');
  if(fv){
    el.style.fontFamily="'"+fv.family+"'";
    if(fv.var)el.style.fontVariationSettings=Object.entries(fv.var).map(([a,v])=>'"'+a+'" '+v).join(',');
    else el.style.fontVariationSettings='';
  }else{
    el.style.fontFamily='';
    el.style.fontVariationSettings='';
  }
  txt.textContent=(d.lines||[]).join('\n');
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
// Пока галка «интро едет с камерой» включена (plan.intro_cam !== false), нул «интро»
// и слой затемнения висят на нуле Камеры 1 — точка считается общей машиной ipvCamChild,
// как у вставок кам1. Галку сняли (intro_cam=false): в .jsx эти нулы идут по ветке
// else — координаты кадра, — значит ни зума, ни сдвига `pan`, ни слежения за головой:
// точка как есть, зум 1, как у свободных вставок кам2.
// Второй копии выбора нет: обе точки входа (ipvShade и ipvIntroPos) берут тут и точку,
// и зум — иначе затемнение и текст разъехались бы на откреплённом интро.
// Группа на перебивке (on2) едет НЕ за Камерой 1: в .jsx нул «интро на кам2» — ребёнок нула
// КАМЕРЫ 2, если её зум включён И включена СВОЯ галка камеры 2 (plan.intro_cam2), и стоит в
// координатах кадра, если зума камеры 2 нет или галка снята. Зум спрятанной Камеры 1 на неё
// не влияет, и галка камеры 1 (intro_cam) её больше не трогает.
function ipvIntroChild(px,py,tm,on2){
  const pl=IPV.plan;
  if(on2){
    const free=!!(pl&&pl.intro_cam2===false);
    const p2=free?null:ipvCam2Point();
    if(!p2)return [px,py,1];
    const s2=ipvZoomAt(tm,'cam2'),W=pl?pl.w:1080,H=pl?pl.h:1920;
    // Сдвиг нула камеры 2 — та же дверь, что у кадра (ipvCamShift): pan + слежение за
    // головой. Своя сборка из pan здесь теряла слежение — в AE интро на нуле камеры 2
    // едет за головой, а в превью стояло бы на месте.
    const pan=(typeof ipvCamShift==='function')?ipvCamShift(tm,'cam2'):((pl&&pl.zoom&&pl.zoom.cam2&&pl.zoom.cam2.pan)||[0,0]);
    return [s2*px+(1-s2)*(p2[0]-0.5)*W+(pan[0]||0), s2*py+(1-s2)*(p2[1]-0.5)*H+(pan[1]||0), s2];
  }
  const free=!!(pl&&pl.intro_cam===false);
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
  if(host)delete host.dataset.visKey;
  SUBW_CACHE.clear();}   // новый план — другие шрифты и кегли: мереные ширины больше не годятся
// ---- плашка субтитров: ширина на момент tm ----
// В живом превью ширину везёт CSS-переход (transition ставит ipvSubs), в рендере перехода
// нет — величину считает ipvSubsBgAt ТОЙ ЖЕ кривой, что выражение в .jsx
// (data/refs/sub_bg_size.js: ease out quart за anim от последней смены сырой ширины).
// Сырая ширина строки — то, что в AE даёт sourceRectAtTime: меряем ЖИВОЙ DOM, вторых
// формул «сколько занимает строка» нет. Ключ показа (какие слова видны) и саму ширину
// кладёт в кэш ipvSubs — правило «что видно на tm» живёт ТОЛЬКО там, второй копии нет.
const SUBW_CACHE=new Map();   // ключ показа -> сырая ширина строки, px кадра
let SUBW_MEASURE=false;       // идёт замер: ipvSubs рисует строки, ширину плашки не считает
let SUBW_TOUCHED=false;       // замер уводил строки в прошлое — кадр надо вернуть на tm
// Сырая ширина строки в px КАДРА по уже нарисованному DOM: максимум строки стопки,
// как getRawWidth в выражении AE (там — sourceRectAtTime по видимым слоям).
function ipvSubRawWidth(host,el,frameW){
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
  if(!maxW_px)return 0;
  // Перевод в px кадра — ровно тот же, что был тут до выноса: стойка превью шириной
  // `clientWidth` показывает кадр шириной plan.w, ширина меряется в её пикселях.
  const stageW=el.clientWidth||1080;
  return maxW_px*((frameW||1080)/stageW);}
// Полная ширина плашки (то, что уезжает в «Размер прямоугольника»): поля по бокам —
// проценты от строки, но не меньше padmin. Числа — из плана, копии правила нет.
function ipvSubFullW(rawW,sbg){
  if(!(rawW>0))return 0;
  const pad=(sbg&&sbg.pad!=null)?sbg.pad:18.0;
  const padmin=(sbg&&sbg.padmin!=null)?sbg.padmin:70.0;
  return rawW+Math.max(2*padmin,rawW*2*pad/100);}
// Сырая ширина на ПРОИЗВОЛЬНЫЙ момент: строки этого момента рисует тот же ipvSubs
// (он же кладёт в кэш ключ показа и ширину), дальше читаем кэш. Своего правила «что
// видно на tm» здесь нет — иначе оно разошлось бы с отрисовкой.
function ipvSubRawWAt(tm){
  if(SUBW_MEASURE)return null;      // уже меряем: второй заход был бы рекурсией
  SUBW_MEASURE=true;
  try{ipvSubs(tm);}finally{SUBW_MEASURE=false;}
  SUBW_TOUCHED=true;
  const el=$('ipvsub');
  const host=el&&el.querySelector('.pvsubs_host');
  const key=(host&&host.dataset.visKey)||'';
  return (key&&SUBW_CACHE.has(key))?SUBW_CACHE.get(key):null;}
// Ширина плашки на момент tm (px кадра): назад ищем последнюю смену сырой ширины
// (порог 2 px — как в выражении AE) и доезжаем до неё кривой за anim.
function ipvSubsBgAt(tm,curRaw,sbg){
  const anim=(sbg&&sbg.anim!=null)?+sbg.anim:0.22;
  let full=ipvSubFullW(curRaw,sbg);
  if(anim>0&&curRaw>0){
    const fps=IPV.fps||60,dt=1/fps,maxFrames=Math.ceil(anim/dt)+1;
    let prev=curRaw,change=tm;
    for(let f=1;f<=maxFrames;f++){
      const tc=tm-f*dt;if(tc<0)break;
      const wPrev=ipvSubRawWAt(tc);
      if(wPrev==null)break;
      if(Math.abs(wPrev-curRaw)>2){prev=wPrev;change=tc+dt;break;}}
    const u=Math.max(0,Math.min(1,(tm-change)/anim));
    const e=1-Math.pow(1-u,4);      // ease out quart — ровно easeVal из sub_bg_size.js
    full=ipvSubFullW(prev+(curRaw-prev)*e,sbg);}
  // Замеры прошлых моментов РИСОВАЛИ строки того момента: без возврата в кадр попал бы
  // чужой текст (ширина верная, слова — прошлые).
  if(SUBW_TOUCHED){SUBW_TOUCHED=false;SUBW_MEASURE=true;
    try{ipvSubs(tm);}finally{SUBW_MEASURE=false;}}
  return full;}
// ---- появление базового слова: те же ключи, что у слоя в AE --------------------
// Ключи приходят В ПЛАНЕ полем слова `anim` (подъём dy, прозрачность op, масштаб sc,
// блюр bl, доля открытия wp, ступенька начертания ft) — их посчитал Python, второй
// копии кривых нет ни здесь, ни в .jsx.
// Значение на момент tm — интерполяция keysAt (кривая easePair из AE: 35/90).
// До первого ключа слова ещё нет и в кадре: в AE слой в этот момент до inPoint, то есть
// не виден вовсе, поэтому спан скрыт, а не растянут по краям строки.
// Масштаб — от центра по горизонтали и от БАЗОВОЙ ЛИНИИ по вертикали: там же, где
// стоит слово, — рост «из-под себя», как у слоя с якорем под текстом.
function ipvSubWordAnim(wsp,tm){
  let a=null;
  // Ключи едут в атрибуте закодированными (encodeURIComponent): сам JSON полон
  // кавычек, и в HTML-атрибуте он обрывал бы разметку на первой же из них.
  try{a=JSON.parse(decodeURIComponent(wsp.dataset.wa||''));}catch(e){a=null;}
  if(!a||!a.op||!a.op.length)return;
  const t0=a.op[0][0], t1=a.op[a.op.length-1][0];
  if(!(t0>=0))return;
  const hidden=(tm<t0);                             // ключей ещё нет — слово не показывалось
  wsp.style.visibility=hidden?'hidden':'';
  // Глитч — своя ветка: мерцание играет по ЛИНЕЙНЫМ ключам плана (теми же, что ставит
  // introAnimFX в .jsx), а не по кривой easePair, как остальные пресеты.
  if(a.name==='glitch'){ ipvSubGlitch(wsp,a,tm,t1,hidden); return; }
  const op=keysAt(a.op,null,tm)/100;
  const dy=a.dy?keysAt(a.dy,null,tm):0;
  const sc=a.sc?keysAt(a.sc,null,tm)/100:1;
  const bl=a.bl?keysAt(a.bl,null,tm):0;
  // Открытие слова: доля открытия из ключей плана (0 — закрыто, 100 — открыто целиком).
  // clip-path режет слово по правому краю — ровно то, что в AE делает маска-прямоугольник,
  // правый край которой едет от левого края текста к правому.
  const wp=a.wp?keysAt(a.wp,null,tm):100;
  const on=(tm<t1+0.001);                           // анимация идёт или ещё не началась
  wsp.style.position=(dy!==0)?'relative':'';
  wsp.style.top=(dy!==0)?dy.toFixed(2)+'px':'';
  wsp.style.opacity=op.toFixed(4);
  wsp.style.filter=(bl>0)?('blur('+bl.toFixed(2)+'px)'):'';
  wsp.style.transform=(sc!==1)?('scale('+sc.toFixed(4)+')'):'';
  wsp.style.transformOrigin=(sc!==1)?'50% 100%':'';
  wsp.style.clipPath=(wp<100)?('inset(0 '+(100-wp).toFixed(4)+'% 0 0)'):'';
  // Начертание — СТУПЕНЬКОЙ (1 -> тонкое, 0 -> основное), как держащие ключи Source Text
  // в AE: оси вариативного шрифта After Effects не анимирует.
  ipvSubWordFont(wsp, a.ft?(keysAt(a.ft,null,tm,true)>=0.5):false);
  if(!on&&!hidden){                                 // анимация отыграна — следов не остаётся
    wsp.style.position='';
    wsp.style.top='';
    wsp.style.opacity='';
    wsp.style.filter='';
    wsp.style.transform='';
    wsp.style.transformOrigin='';
    wsp.style.clipPath='';
    ipvSubWordFont(wsp,false);
  }
}
// ---- глитч появления: тот же механизм, что у строк интро ------------------------
// Прозрачность — по ключам плана ТЕМ ЖЕ ipvGlitchOp (линейно), буквы проступают
// случайно — тем же ipvRenderChars. Своего глитча превью не заводит: в .jsx это ровно
// та же introAnimFX, что играет строки интро.
function ipvSubGlitch(wsp,a,tm,t1,hidden){
  if(hidden)return;                                 // до появления слова его нет и в кадре
  if(tm>t1+0.001){                                  // глитч отыгран — следов не остаётся
    if(wsp._chText!=null){ wsp.textContent=wsp.textContent; wsp._chText=null; }
    wsp.style.opacity='';
    return;
  }
  const t0=a.op[0][0], dt=tm-t0;
  // Ключи плана — в АБСОЛЮТНОМ времени слова: ipvGlitchOp сравнивает время с ключами
  // как есть (он не предполагает, что первый ключ на нуле), поэтому ему идёт tm, а не dt.
  wsp.style.opacity=ipvGlitchOp(tm,a.op).toFixed(3);
  const dur=(a.dur>0)?a.dur:(t1-t0||1);
  const tick=Math.floor(dt*30);
  const pVis=Math.min(1,Math.max(0.2,dt/dur));
  ipvRenderChars(wsp,wsp.textContent,(ch,ci)=>{
    const hash=Math.abs(Math.sin(tick*12.9898+ci*78.233+10)*43758.5453)%1;
    ch.style.opacity=(hash<pVis)?'1':'0';
  },'inline');
}
// ---- подложка слова: ОДИН элемент за ТЕКУЩИМ словом ------------------------------
// Момент, с которого слово считается текущим, лежит в плане у каждого слова
// (data-wbg); фигура встаёт за словом с САМЫМ ПОЗДНИМ моментом среди видимых — это и
// есть произносимое сейчас слово. В строке из четырёх слов фигура перескакивает
// четыре раза, а слой в .jsx для этого один: сотни слов — не сотни слоёв.
// Геометрия — по прямоугольнику выбранного слова (в AE то же даёт sourceRectAtTime),
// числа (высота, скругление, поле, цвета, раскрытие) — из плана: своих у превью нет.
function ipvSubWbg(tm){
  const el=$('ipvsub');if(!el)return;
  const pl=IPV.plan||{},wb=pl.sub_wbg;
  const host=el.querySelector('.pvsubs_host');if(!host)return;
  let mk=host.querySelector('.pvsub_wbg');
  const drop=()=>{if(mk){mk.remove();mk=null;}};
  if(!wb){drop();return;}
  let cur=null,curT=-1;
  host.querySelectorAll('.pvsubw_wd').forEach(sp=>{
    // Имя wt, а не t: t — переводчик интерфейса, и локальная переменная с таким именем
    // роняет панель («t is not a function») — на этом уже обжигались в ipvUI.
    const wt=parseFloat(sp.dataset&&sp.dataset.wbg);
    if(!(wt>=0))return;
    if(wt>curT){curT=wt;cur=sp;}
  });
  if(!cur||!(tm>=curT)){drop();return;}             // слово ещё не звучало — подложки нет
  const w=pl.w||1080,hh=pl.h||1920;
  const stageW=el.clientWidth||w;
  const k=w/stageW;                                 // экранные px -> px кадра
  const box=cur.getBoundingClientRect(),hb=host.getBoundingClientRect();
  const pad=(wb.pad!=null)?+wb.pad:0, mh=(wb.h!=null)?+wb.h:0;
  const full=box.width*k+2*pad;                     // слово плюс поле по бокам
  // Раскрытие маркера — по ключам плана (0 -> 100 % за sweep) и от ЦЕНТРА слова:
  // ровно так же растёт ширина фигуры у слоя в .jsx.
  const frac=(wb.kind==='highlight'&&wb.sweep>0)
    ?(keysAt([[curT,0],[curT+wb.sweep,100]],null,tm)/100):1;
  const wpx=full*frac;
  const leftPx=(box.left-hb.left)*k+(full-wpx)/2;
  const cyPx=((box.top+box.height/2)-hb.top)*k+((wb.dy!=null)?+wb.dy:0);
  const cqw=v=>((v/w*100).toFixed(3)+'cqw');
  if(!mk){mk=document.createElement('div');mk.className='pvsub_wbg';host.appendChild(mk);}
  mk.style.left=cqw(leftPx);
  mk.style.width=cqw(wpx);
  mk.style.height=cqw(mh);
  mk.style.borderRadius=cqw((wb.round!=null)?+wb.round:0);
  mk.style.bottom=(((hh-(cyPx+mh/2))/hh*100).toFixed(3)+'%');
  mk.style.background=rgb2hex(wb.fill||[1,1,1]);
  mk.style.opacity=String(((wb.op!=null)?+wb.op:100)/100);
  // За каким словом стоит фигура — в разметку: по этому стенд и проверяет, что
  // подложка идёт за ТЕКУЩИМ словом, а не за предыдущим.
  mk.dataset.w=cur.textContent||'';
  mk.dataset.t=String(curT);
}
// Смена начертания слова: у пресета «начертание» слово приходит тонким шрифтом и в
// середине появления становится основным. Имена обоих шрифтов лежат в разметке слова
// (data-wf — тонкое, data-wb — основное), а объявления из них делает та же дверь,
// что и у остальных шрифтов превью (ipvFontDecls): второй копии правила нет.
// Тонкого шрифта в системе нет — шага тоже нет, как в AE («шрифт не найден -> база»).
function ipvSubWordFont(wsp,thin){
  if(wsp.dataset.wf===undefined)return;
  const d=ipvFontDecls(ipvFontFor(thin?wsp.dataset.wf:(wsp.dataset.wb||'')));
  wsp.style.fontFamily=d.family;
  wsp.style.fontVariationSettings=d.vars;
}
// Объявления шрифта из записи FONTS: семейство и оси вариативного шрифта. Одна дверь
// на весь предпросмотр — строку для разметки собирает она же (fvCss в ipvSubs).
function ipvFontDecls(fv){
  if(!fv)return {family:'',vars:''};
  return {family:fv.family?("'"+fv.family+"'"):'',
          vars:fv.var?Object.entries(fv.var).map(([a,v])=>"'"+a+"' "+v).join(','):''};
}
// ---- тень AE (Drop Shadow) в CSS: одна дверь на всё превью ----
// Замер по рендеру AE 2026 (06.10.2026): белый квадрат в прекомпе, чёрная тень,
// Opacity 255, Distance 0 — Softness 50/150/287 дали гауссову сигму 9.5/28.0/53.5 px
// (подгонка erf-профиля края, rmse < 0.004), то есть σ = 0.187·Softness. CSS
// drop-shadow(dx dy r c) размывает гауссом с σ = r/2, отсюда r = 2·0.187·Softness:
// множитель 0.374 ниже. Пока превью брало половину мягкости, тень выходила шире
// собранной в AE в 1.34 раза.
// sh — тень ИЗ ПЛАНА (plan.shadows.<кто>: {op255, dir, dist, soft, color}), k — пиксели
// превью на пиксель кадра: и смещение, и мягкость заданы в пикселях кадра, как Drop
// Shadow в AE. Смещение AE: dx = Dist·cos(Dir), dy = Dist·sin(Dir) (Y в кадре вниз).
// Непрозрачность приходит уже в шкале AE 0..255 — в альфу CSS переходит один-к-одному.
// Своих чисел тени у превью нет: второе место разошлось бы с .jsx молча.
function aeShadowCss(sh,k){
  const R_K=0.374;                             // r = 0.374·Softness, см. замер выше
  if(!sh)return '';
  k=(k==null)?1:+k;
  const op=Math.max(0,Math.min(255,+(sh.op255!=null?sh.op255:255)))/255;
  const rad=(+((sh.dir!=null?sh.dir:0)))*Math.PI/180;
  const dx=((sh.dist||0)*k*Math.cos(rad)).toFixed(1);
  const dy=((sh.dist||0)*k*Math.sin(rad)).toFixed(1);
  const r=(R_K*(sh.soft||0)*k).toFixed(1);
  const c=(sh.color||[0,0,0]).map(v=>Math.round(Math.max(0,Math.min(1,v||0))*255));
  return 'drop-shadow('+dx+'px '+dy+'px '+r+'px rgba('+c[0]+','+c[1]+','+c[2]+','+op.toFixed(3)+'))';
}
function ipvSubs(tm){const el=$('ipvsub');if(!el)return;
  // ---- заливка текста градиентом и свечение: числа из плана --------------------
  // Обе — ВНУТРИ ipvSubs, а не отдельными функциями файла: стенды отдельных тестов
  // вырезают из 85-inserts-view.js ровно ipvSubs (и lineHtml в нём), и вызов наружу
  // ронял бы такой стенд целиком («ipvSubGradCss is not defined»).
  // Градиент по буквам — то, что CSS умеет через background-clip:text, а AE — эффектом
  // Gradient Ramp на слое слова: цвета и угол приходят в плане, своих чисел нет.
  // text-shadow:none — не украшение: тень градиентного слова рисуется ОТДЕЛЬНЫМ слоем
  // под ним (fx слова ниже). У градиента буквы прозрачны для заливки, и тень, нарисованная
  // на самих буквах, ложится ПОД них и просвечивает сквозь них чёрным — слово выходило
  // тёмным, хотя в AE под слоем с Ramp лежит тень слоя, а буквы остаются градиентом.
  function gradCssFor(g){
    if(!g)return '';
    const from=rgb2hex(g.from||[1,1,1]), to=rgb2hex(g.to||[1,1,1]);
    const ang=(g.angle!=null)?+g.angle:90;
    return 'background-image:linear-gradient('+ang+'deg,'+from+','+to+');'
          +'-webkit-background-clip:text;background-clip:text;color:transparent;'
          +'-webkit-text-fill-color:transparent;text-shadow:none;';
  }
  // Свечение — Glo2 в AE, приближение двумя размытыми копиями букв здесь: у CSS нет ни
  // порога, ни интенсивности, поэтому радиус и сила множат два размытия — тем же приёмом,
  // что уже живёт в превью интро (12 и 24 px при радиусе 77 и силе 0.62, то есть при
  // дефолтах интро множитель 1). Перевод «радиус Glo2 -> пиксели CSS» — ОДНО место на
  // превью: вторая копия формулы у слов субтитров и у слов интро разошлась бы молча.
  // Тени возвращаются списком в форме text-shadow: у сплошной заливки свечение играет на
  // буквах, у градиента — функциями drop-shadow на слое ПОД словом (см. fxFor ниже).
  function glowTsh(g,isY){
    if(!g||(g.yellow&&!isY))return [];       // «только жёлтые»: белым свечения нет
    const rad=(g.rad!=null)?+g.rad:40, amt=(g.amt!=null)?+g.amt:1;
    const gk=(rad/77)*(amt/0.62);
    if(!(gk>0))return [];
    const col=rgb2hex(g.fill||[1,1,1]);
    return [Math.round(12*gk*10)/10,Math.round(24*gk*10)/10].map(b=>'0 0 '+b+'px '+col);
  }
  // Тень субтитров — та же, что в .jsx (Drop Shadow на слое прекомпа): рисуется
  // ОДНОЙ дверью aeShadowCss фильтром на контейнере слов (ниже, у host). Своих чисел
  // тени у превью нет вовсе, поэтому и списка теней в форме text-shadow здесь больше нет.
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
  // Пиксели превью на пиксель кадра: тени AE заданы в пикселях кадра (Drop Shadow в AE),
  // и на экране они обязаны расти вместе с ним. Тем же множителем мерятся подъём и блюр
  // жёлтого слова ниже (kpx) — мера одна на всю функцию.
  const kpx=(el.clientWidth||w)/w;
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
  // CSS-тень из app.css снимается ВСЕГДА: тень AE рисуется фильтром по числам плана
  // (aeShadowCss, контейнер слов ниже), и вторая, «своя» тень из CSS была бы лишней.
  // Снимается и при выключенной галке (плашка): sub_shadow===false гасит фильтр там же.
  el.style.setProperty('--subsh','none');
  const s=(typeof CURSTYLE!=='undefined'&&CURSTYLE)?CURSTYLE:{};
  // Шрифты субтитров — ИЗ ПЛАНА: их посчитал Python тем же стилем, что уехал в .jsx
  // (FONT/HL_FONT), и второй копии правила «какой шрифт у субтитров» тут быть не должно.
  // Своей копией был стиль страницы (CURSTYLE), а он на странице рендера может не
  // доехать: тело сборки принимает и стиль-ИМЯ (строкой, так строит CLI) — тогда
  // CURSTYLE это строка, s.font пуст, и субтитры рисовались запасным
  // SFPro-CondensedSemibold, хотя .jsx собрал BebasNeue-Bold. Замер по кадру владельца
  // (30 с): «КУБИК» 344 px против 284 в AE, «СУСТ*НОНА» 610 против 498 — и кегль при
  // этом совпадал (98 против 100), то есть расходился ИМЕННО шрифт.
  // Поля нет (старый бэкенд без перезапуска) — падаем на стиль, как было.
  const basePs=pl.sub_font||s.font||'SFPro-CondensedSemibold';
  const hlPs=pl.sub_hl_font||s.hl_font||basePs;
  const baseFv=ipvFontFor(basePs);
  const hlFv=ipvFontFor(hlPs);
  // Тонкое начертание пресета «начертание»: имя приходит в плане (его посчитала
  // сборка стиля), разрешается здесь тем же поиском шрифтов, что и остальные.
  // Не нашлось в системе — шага начертания не будет вовсе, как и в AE.
  const thinPs=pl.sub_anim_font||'';
  const thinFv=thinPs?ipvFontFor(thinPs):null;
  // Кавычки ТОЛЬКО одинарные: строка уезжает в атрибут style="…", и двойная кавычка
  // внутри обрывает атрибут на себе — браузер получал `font-family:` без значения и
  // рисовал субтитры шрифтом страницы (жалоба «в превью не тот шрифт», третий раз).
  // CSS одинарные кавычки принимает и у семейства, и у осей вариативного шрифта.
  // Семейство и оси собирает ipvFontDecls — та же дверь, что у смены начертания.
  function fvCss(fv){
    if(!fv)return 'font-weight:800;';
    const d=ipvFontDecls(fv);
    let cs=d.family?('font-family:'+d.family+';'):'';
    if(d.vars)cs+='font-variation-settings:'+d.vars+';';
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
    const bgAnim=(sbg.anim!=null?sbg.anim:0.22);
    bgEl.style.background=bgFill;
    bgEl.style.opacity=bgOp;
    bgEl.style.height=bgH;
    bgEl.style.borderRadius=bgR;
    bgEl.style.bottom=bgBot;
    // В рендере перехода НЕТ: ширину на момент t считает ipvSubsBgAt (ниже), а переход
    // тянул бы её по реальному времени — снимок зависел бы от того, как быстро снимают,
    // а не от номера кадра. Класс render-mode глушит переход и сам, но инлайновое
    // значение ему не подчиняется — поэтому его тут и не ставим.
    bgEl.style.transition=ipvRenderMode()?'none':('width '+bgAnim+'s cubic-bezier(.165,.84,.44,1)');
    // Тень плашки — свои числа AE (SUB_BG_SH_* в .jsx): в главном композе плашка лежит
    // ОТДЕЛЬНЫМ слоем со своим Drop Shadow, поэтому фильтр ставится ей, а не контейнеру
    // слов (иначе тень текста легла бы и на плашку — вторая). Раньше превью её не
    // рисовало вовсе: кадр расходился с собранным в AE.
    // typeof — стенды отдельных тестов вырезают ipvSubs без соседних функций файла.
    bgEl.style.filter=(typeof aeShadowCss==='function')
      ?aeShadowCss(pl.shadows&&pl.shadows.sub_bg,kpx):'';
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
  // Тень субтитров AE — Drop Shadow на слое прекомпа: фильтром на контейнере слов,
  // чтобы тень легла под ВСЁ содержимое прекомпа разом (слова и подложку слова), как в
  // AE. Галка «плашка» снимает тень текста (sub_shadow=false) — тогда фильтра нет.
  // typeof — стенды отдельных тестов вырезают ipvSubs без соседних функций файла.
  host.style.filter=(pl.sub_shadow===false||typeof aeShadowCss!=='function')
    ?'':aeShadowCss(pl.shadows&&pl.shadows.sub,kpx);
  // Ключ показа: по нему решается, перерисовывать ли слова. Появление по пресету
  // (поле anim) входит в ключ: у одного и того же слова ключи могут появиться/исчезнуть
  // между планами (в панели сменили пресет), и тогда разметку обязательно перестроить.
  // Кегль жёлтых (hl_size_k) и имя тонкого начертания — тоже: и то и другое стоит
  // в разметке слова, и без них правка стиля не доехала бы до уже нарисованных строк.
  // Готовность шрифтов — тоже: посадку строк по базовой линии (ниже) мерит вёрстка, а
  // до загрузки файла шрифта метрики другие (запасной шрифт) — по смене статуса строки
  // перестраиваются и мерятся заново.
  const visKey=vis.map(sub=>(sub.stack?'s':'r')+(sub.s)+':'+(sub.gend)+':'+(sub.w||'')+':'+(sub.row||0)
    +(sub.anim?'a':((sub.words||[]).some(wd=>wd.anim)?'a':''))).join('|')
    +'|k'+(pl.hl_size_k!=null?pl.hl_size_k:1)+'|f'+(pl.sub_anim_font||'')
    // Градиент и свечение — тоже в ключе: их числа стоят в РАЗМЕТКЕ слова
    // (text-shadow буквы либо слой эффектов с drop-shadow под ней), и без них правка
    // стиля не доехала бы до уже нарисованных строк — как было с hl_size_k. Тень AE
    // в разметке не стоит (фильтр контейнера слов), но галка «плашка» меняет вид кадра
    // разом — её признак в ключе остаётся.
    +'|s'+(pl.sub_shadow===false?0:1)+'|g'+JSON.stringify(pl.sub_grad||0)
    +'|x'+JSON.stringify(pl.sub_glow||0)
    +'|d'+((typeof document!=='undefined'&&document.fonts&&document.fonts.status)||'');
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
      // Кегль жёлтого слова: множитель стиля (hl_size_k) к кеглю ЭТОЙ строки. Считается
      // здесь же, где кегль строки: у ужатой строки он свой, и второй копии правила
      // «от чего считать» быть не должно. При 1.0 (как сегодня) стиля нет вовсе.
      const baseFs=(minFs<Infinity)?minFs:(pl.fsize||0);
      const yK=(pl.hl_size_k!=null?pl.hl_size_k:1);
      const yFs=(yK!==1&&baseFs>0)?('font-size:'+(baseFs*yK/w*100).toFixed(3)+'cqw;'):'';
      // Заливка градиентом (sub_fill_mode) и свечение (sub_glow): те же числа, что у
      // .jsx. «Только жёлтые» — свечение лишь жёлтым словам, как ветка GLOW_YEL там.
      // Поле градиента — sub_grad: sub_fill в плане занят цветом субтитров (--subfc).
      const gradCss=gradCssFor(pl.sub_grad);
      const gradOn=!!pl.sub_grad;
      // Свечение слова сплошной заливки — объявлением на буквах: буквы непрозрачны,
      // тени буквам не нужны вовсе — тень слоя прекомпа рисует aeShadowCss фильтром на
      // контейнере слов. Свечения нет — объявления нет (в CSS остаётся --subsh:none).
      const tshFor=(isY)=>{
        if(gradOn)return '';     // у градиента свечение играет слой под словом
        const gl=glowTsh(pl.sub_glow,isY);
        if(!gl.length)return '';
        return 'text-shadow:'+gl.join(',')+';';
      };
      // Слой эффектов слова: обёртка ПОД словом, у которой свечение нарисовано
      // drop-shadow: он берёт уже нарисованные буквы и кладёт свет под них — ровно то,
      // что в AE делает Glo2 на слое слова под слоем с Ramp. Нужен только там, где
      // эффекту негде больше жить: у градиента (свой text-shadow лёг бы под прозрачные
      // буквы), поэтому у остальных слов лишнего элемента и лишнего filter нет.
      const fxFor=(isY)=>{
        if(!gradOn)return '';
        const f=glowTsh(pl.sub_glow,isY).map(s=>'drop-shadow('+s+')');
        return f.length?('filter:'+f.join(' ')+';'):'';
      };
      const wrapFx=(html,isY)=>{const fx=fxFor(isY);
        return fx?('<span class="pvsubw_fx" style="'+fx+'">'+html+'</span>'):html;};
      // Имена шрифтов слова у пресета «начертание»: тонкое и основное. Пишутся только
      // тому слову, у которого есть ключи появления, — остальным ступенить нечего.
      const wFonts=(a)=>((a&&thinFv)?(' data-wf="'+esc(thinPs)+'" data-wb="'+esc(basePs)+'"'):'');
      // Подложка слова: момент, с которого слово текущее, — число из плана (wd.wbg.t).
      // Превью по нему выбирает, за каким словом стоят фигуру, и своей формулы «когда
      // слово звучит» не держит.
      const wbgAt=(wd)=>((wd&&wd.wbg&&wd.wbg.t!=null)?(' data-wbg="'+wd.wbg.t+'"'):'');

      const wordsHtml=rowSubs.map(sub=>{
        if(sub.words&&sub.words.length>1){
          return sub.words.map(wd=>{
            const isY=(wd.color==='yellow');
            const wCss=(isY?hlFvCss:baseFvCss)+(isY?yFs:'')+gradCss+tshFor(isY);
            // время появления жёлтого — из плана (t0): по нему ниже идут подъём,
            // проявление и блюр. Своей формулы «когда слово произнесено» в превью нет.
            const t0=(isY&&wd.t0!=null)?(' data-hl0="'+wd.t0+'"'):'';
            // своя длительность появления у укороченного слова (hd): в AE её
            // играет цикл стопки/слов, а не этот — превью берёт готовое число из плана.
            const hd=(isY&&wd.hd!=null)?(' data-hld="'+wd.hd+'"'):'';
            // Появление БЕЛОГО слова по пресету стиля: ключи посчитал Python и положил
            // в план — превью их только интерполирует (ipvSubWordAnim ниже). Своих кривых нет.
            const wa=(!isY&&wd.anim)?(' data-wa="'+esc(encodeURIComponent(JSON.stringify(wd.anim)))+'"'):'';
            const wf=(!isY&&wd.anim)?wFonts(wd.anim):'';
            return wrapFx('<span class="pvsubw_wd'+(isY?' yel':'')+'"'+t0+hd+wa+wf+wbgAt(wd)+' style="'+wCss+'">'+esc(wd.w)+'</span>',isY);
          }).join(' ');
        }else{
          const isY=(sub.color==='yellow');
          const wCss=(isY?hlFvCss:baseFvCss)+(isY?yFs:'')+gradCss+tshFor(isY);
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
          const wa=(!isY&&sub.anim)?(' data-wa="'+esc(encodeURIComponent(JSON.stringify(sub.anim)))+'"'):'';
          const wf=(!isY&&sub.anim)?wFonts(sub.anim):'';
          // Подложка у одиночного слова/строки из одного слова: момент — из элемента
          // плана, а у строки-однословника он лежит в её единственном слове.
          const wbg=wbgAt(sub)||wbgAt(w0);
          return wrapFx('<span class="pvsubw_wd'+(isY?' yel':'')+'"'+hl0+hd+wa+wf+wbg+' style="'+wCss+'">'+esc(sub.w)+'</span>',isY);
        }
      }).join(' ');

      const allYel=rowSubs.every(s=>s.color==='yellow');
      return '<span class="pvsubw'+(allYel?' yel':'')+'" style="bottom:'+bot+'%;'+fsStyle+'">'+wordsHtml+'</span>';
    };
    let html='';
    rowMap.forEach((rowSubs,r)=>html+=lineHtml(rowSubs,rowSubs[0].sub_step||pl.sub_step||step,r));
    stackMap.forEach((stSubs,r)=>html+=lineHtml(stSubs,step,r));
    host.innerHTML=html;
    // Посадка строк по БАЗОВОЙ ЛИНИИ, а не по низу коробки. В AE POSY — базовая линия
    // текстового слоя (Position точечного текста), а CSS `bottom` ставит нижний край
    // КОРОБКИ строки: глифы встают выше AE на спуск шрифта. Замер по кадру владельца
    // (30 с, posy 1132): низ глифов 1102 у нас против 1130 в AE — ровно спуск
    // BebasNeue-Bold при 140 px (winDescent 300/1000 → 0.5·(1−0.9+0.3)·140 = 28 px).
    // Спуск мерится в вёрстке: пустой inline-block нулевой высоты стоит НА базовой
    // линии (тот же приём, что у интро и подписи, см. .pvcap_strut) — числа шрифта
    // в превью не заводятся, шрифт может быть любой.
    host.querySelectorAll('.pvsubw').forEach(row=>{
      const strut=document.createElement('i');
      strut.className='pvcap_strut';
      row.appendChild(strut);
      const baseOff=strut.offsetTop+strut.offsetHeight;   // от верха коробки до базовой линии
      // Убираем опору: в кадре ей делать нечего. Две двери — потому что node-стенды
      // превью (tests/test_intro_preview_anim.py и соседние) гоняют эту же функцию на
      // урезанном DOM: без `remove` падал бы не стенд, а боевая отрисовка субтитров.
      if(strut.remove)strut.remove();else if(row.removeChild)row.removeChild(strut);
      const boxH=row.offsetHeight||0;
      const bot=parseFloat(row.style.bottom||'');
      if(baseOff>0&&boxH>baseOff&&bot===bot)
        row.style.bottom=(bot-(boxH-baseOff)/h*100).toFixed(3)+'%';
    });
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
  // Блюр — поле плана hl_blur_css: это УЖЕ пиксели CSS (сигма гауссианы). Число стиля
  // (hl_blur_amt) — «Blurriness» Gaussian Blur в AE, и подставлять его в blur() нельзя:
  // выходило вчетверо размытее собранного в AE (перевод — core/xml2ae/layout.css_blur_px,
  // один на всю сборку). Старый бэкенд без перезапуска поля не шлёт — тогда как было.
  const blAmt=pl.hl_blur?((pl.hl_blur_css!=null)?pl.hl_blur_css:(pl.hl_blur_amt||0)):0;
  // Один список слов на две анимации: у слова бывает либо анимация жёлтого (ключи
  // выделения), либо появление по пресету стиля — обход дерева один на кадр, а не два.
  const wSpans=host.querySelectorAll('.pvsubw_wd');
  wSpans.forEach(wsp=>{
    // Появление БЕЛОГО слова по пресету стиля (sub_anim): ключи лежат В ПЛАНЕ — их
    // посчитал Python (core/xml2ae/layout.py), превью кривых не считает вовсе, только
    // интерполирует (keysAt, та же кривая, что easePair в AE). Анимация — функция
    // времени: в режиме рендера (ipvRenderAt) ни одного CSS-перехода не заводится.
    if(wsp.dataset&&wsp.dataset.wa){ ipvSubWordAnim(wsp,tm); return; }
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
  // Подложка слова — ПОСЛЕ слов: она выбирает текущее слово по их моменту из плана
  // (data-wbg) и встаёт по его прямоугольнику. Один элемент на кадр, как один слой в AE.
  // Проверка typeof: стенды отдельных тестов вырезают из файла ТОЛЬКО ipvSubs (без
  // соседних функций), и без неё вызов ронял бы такой стенд целиком.
  if(typeof ipvSubWbg==='function') ipvSubWbg(tm);
  if(sbg&&bgEl){
    const rawW=ipvSubRawWidth(host,el,w);
    if(rawW>0){bgEl.dataset.lastRaw=String(rawW);SUBW_CACHE.set(visKey,rawW);}
    if(SUBW_MEASURE)return;      // идёт замер другого момента: ширину поставит внешний вызов
    const cur=(rawW>0)?rawW:(parseFloat(bgEl.dataset.lastRaw)||0);
    // Живое превью — ширина строки, дальше её везёт CSS-переход. Рендер — ширина на t.
    const full=ipvRenderMode()?ipvSubsBgAt(tm,cur,sbg):ipvSubFullW(cur,sbg);
    if(full>0){
      const fullCqw=(full/w*100).toFixed(3)+'cqw';
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
    ipvDisc(tm);       // дисклеймер по плану — те же числа, что в .jsx
  }
  else{
    const tle=$('ipvtopline');if(tle)tle.style.display='none';
    const cape=$('ipvcaption');if(cape)cape.style.display='none';
    const dce=$('ipvdisc');if(dce)dce.style.display='none';
    sb.style.opacity='';
    sb.style.removeProperty('--subfs');
    sb.style.removeProperty('--subfc');
    sb.style.removeProperty('--subhl');
    sb.style.removeProperty('--subsh');
    sb.textContent=cur;sb.style.color=yel?'var(--subhl,var(--yel))':'';sb.classList.remove('plan');
  }
  ipvZoom(tm);                                    // наезд/дрейф Камеры 1 по плану
  ipvShade();                                     // затемнение под интро — из плана
  if(typeof ipvTrans==='function')ipvTrans(tm);   // переход на входе/выходе видеовставки
  ipvStartBlur(tm);                               // размытие на старте — CSS-фильтр на кадре
  itlPh(tm);ipvIntro(tm);ipvOverlay(tm);aewHighlight(tm);
  // Слой рото — после кадра и вставок: куски по времени меняются, и на новом куске
  // холст обязан сменить картинку, а не остаться с фигурой прошлого.
  ipvRoto(tm);
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
// размер карточки фотовставки НА ЭКРАНЕ (кадр ролика — из плана) — тот же расчёт, что
// _ins_scale в xml2ae.py: вписываем видимую часть в коробку, не-ультравайд режется маской
// в квадрат. W/H — размер кадра: у плана он свой (9:16, 1:1, 4:5, 16:9), без плана —
// прежняя вертикаль. Явные W/H — для стендов (node гоняет эти функции без плана).
// Коробка (BW/BH/SQ) — из плана, поля ins_box: Python (layout._ins_box) отдаёт её уже
// умноженной под кадр ролика тем же правилом, что размеры стиля (min(W,H)/1080).
// Своей копии чисел у превью нет: в 4K-кадре 2160×3840 копия расходилась с собранной
// карточкой вдвое. BH — среднее cam1/cam2 (у плана обе высоты, стиль — какая камера
// активна — в UI ещё неизвестен). Плана нет (первый кадр, ошибка /api/scene, стенды) —
// прежние числа, умноженные на кадр по тому же правилу.
// mw/mh — ручная форма маски в % (см. scrubMask): растягивают/сужают ВИДИМУЮ часть, а
// масштаб карточки остаётся авторасчётным — те же клампы, что в JSX (за краем фото пусто).
// Возвращает и окно маски (w/h), и размер САМОГО фото в тех же px (pw/ph): в прекомпе фото
// тянется под ширину композа и маска его обрезает, а не ужимает — предпросмотру нужны оба.
// sc — ручной масштаб (% от авто): множит ВСЮ карточку вместе с маской, как scale слоя в AE.
function insPreviewBox(iw,ih,mw,mh,sc,W,H){
  if(W==null||H==null){const f=ipvPlanWH();W=f.w;H=f.h;}
  const p=(typeof IPV!=='undefined'&&IPV.plan)||null,ib=p&&p.ins_box;
  let BW,BH,SQ;
  if(ib&&ib.w){BW=+ib.w;BH=Math.round((+ib.h_cam1+ +ib.h_cam2)/2);SQ=+ib.sq||2.2;}
  else{const k=Math.min(W,H)/1080;BW=1030*k;BH=Math.round((560+495)/2*k);SQ=2.2;}
  const photoH=ih*W/iw;
  let visH=photoH,visW=W;
  if(visH>0&&W/visH<=SQ){visW=visH=Math.min(W,visH);}
  const s=Math.min(BW/visW,BH/visH)*(sc==null?100:sc)/100;
  visW=Math.max(20,Math.min(W,visW*(mw==null?100:mw)/100));
  // выше H маска не растёт даже у фото длиннее композа: в JSX её углы клампятся по кадру
  visH=Math.max(20,Math.min(photoH,H,visH*(mh==null?100:mh)/100));
  return {w:visW*s,h:visH*s,pw:W*s,ph:photoH*s};}
// размер видеовставки на экране: заполнение кадра × sc (зеркало fitInto в xml2ae).
// Кадр — из плана (W/H), плана нет — формат спикера клипа (ipvPlanWH), как было.
// Пока sc=100, коробка = кадр и предпросмотр рисует видео как раньше, через object-fit:cover.
function insVideoFill(vw,vh,sc,W,H){
  if(W==null||H==null){const f=ipvPlanWH();W=f.w;H=f.h;}
  const f=Math.max(W/vw,H/vh)*((sc==null?100:sc)/100);
  return {w:vw*f,h:vh*f};}
// панорама полноэкранной видеовставки: позиция = x/y как их задал пользователь (px кадра),
// ничем не зажата. Раньше сдвиг упирался в запас вылета ролика за кадр
// (зеркало fillSlack в xml2ae): у вертикального 9:16 при sc=100 запаса нет вовсе — видео
// не двигалось совсем, а 16:9 по высоте не двигалось никогда. sx/sy остаются в ответе
// СПРАВКОЙ (сколько ролик вылезает за кадр), позицию они не режут.
function insVideoPan(vw,vh,x,y,W,H){
  if(W==null||H==null){const f=ipvPlanWH();W=f.w;H=f.h;}
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
    if(have){if(!used.has(key)){used.add(key);have.style.visibility='';return have;}continue;}   // занят другой вставкой в этом же кадре
    const v=document.createElement('video');
    v.src='/api/media?path='+encodeURIComponent(media);
    v.muted=true;v.playsInline=true;v.preload='auto';
    // Размеры файла приходят только с метаданными: запоминаем их и перерисовываем кадр, если
    // план их не несёт (fitw/fith). Иначе вставка осталась бы вовсе без масштаба — и «Масштаб, %»
    // не на что было бы умножать.
    v.addEventListener('loadedmetadata',()=>{
      insVidDimsPut(media,v.videoWidth,v.videoHeight);
      if(IPV.plan&&!insVidPlanDims(media)&&typeof ipvUI==='function')ipvUI(ipvNow());});
    const onMosFrame=()=>{
      const wr=(v.closest&&v.closest('.ipvwrap.mosaic'))||(v.parentNode&&v.parentNode.classList&&v.parentNode.classList.contains('mosaic')?v.parentNode:null);
      if(wr)ipvPixelate(wr,v,true);};
    v.addEventListener('loadeddata',onMosFrame);
    v.addEventListener('seeked',onMosFrame);
    insVidCache().set(key,v);used.add(key);return v;}
}
function insVidFree(v){if(typeof mediaFree==='function')return mediaFree(v);
  if(!v)return;try{v.pause();}catch(e){}try{v.removeAttribute('src');}catch(e){}try{v.load();}catch(e){}if(v.parentNode&&v.parentNode.removeChild)try{v.parentNode.removeChild(v);}catch(e){}}
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
    if(Math.abs((v.currentTime||0)-want)>vidSeekTol()){try{v.currentTime=want;}catch(e){}}
    if(IPV.playing&&v.paused)v.play().catch(()=>{});
    if(!IPV.playing&&!v.paused)v.pause();}
  ipvInsMosaics();}   // мозаика — ПОСЛЕ перемотки вставок: холст обязан нести их кадр
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
            // фильтр (тень insFX и блюр) ставит ipvInsPlace на каждом кадре — из плана:
            // своих чисел тени здесь нет, как и у маски
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
    // В AE-плане cid не приезжает с сервера (план собирается из вставок панели, поле ему
    // не нужно) — берём его у ИСХОДНОЙ вставки по индексу: plan.inserts идёт ровно по
    // INS.filter(media) один-в-один. Путь файла — запасной ключ (старый план/легаси).
    const planCards=(IPVMODE==='ae')?INS.filter(r=>(r.media||'').trim()):null;
    for(let a=0;a<act.length;a++){const item=act[a];
      let ci=-1;
      if(item.isPlan){
        const src=planCards?planCards[item.i]:item.x;
        if(src&&src.cid)ci=allCards.findIndex(z=>z&&z.uid===src.cid);
        if(ci<0){const nm=insCardKey(item.x);
          ci=allCards.findIndex(z=>nm&&nm===normInsPath(z.media));}
      }else{
        ci=item.ci;
      }
      if(ci>=0&&!cardActs.includes(ci))cardActs.push(ci);
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
    const wr=ov.querySelector('[data-ins="'+key+'"]');if(wr)ipvInsDraw(wr,item.x,tm);}   // покадровая геометрия
  for(let a=0;a<act.length;a++){const item=act[a],x=item.x;
    if(!item.isPlan||!x.media)continue;                        // видеовставка идёт от своего sin вместе с монтажом
    const v=ov.querySelector('[data-ins="'+item.i+'"] video');
    if(!v)continue;
    const want=Math.max(0,(x.sin||0)+tm-(+x.start));
    if(Math.abs((v.currentTime||0)-want)>vidSeekTol()){try{v.currentTime=want;}catch(e){}}
    if(IPV.playing&&v.paused)v.play().catch(()=>{});
    if(!IPV.playing&&!v.paused)v.pause();}
  ipvInsMosaics();}   // мозаика — ПОСЛЕ перемотки вставок: холст обязан нести их кадр
// покадровая позиция/масштаб/прозрачность вставки из плана. Карточка фото (x.card) — в
// comp-координатах осевшего масштаба; anim.scale — Scale слоя в AE, делится на осевший S.
function ipvInsPlace(wr,x,tm){
  const el=wr.firstChild;if(!el)return;
  const pl=IPV.plan;const W=pl?pl.w:1080,H=pl?pl.h:1920,k=(wr.clientWidth||W)/W;
  // Фильтр карточки фото: тень AE (insFX в .jsx вешает Drop Shadow на слой вставки) и
  // блюр входа/выхода (Box Blur2). Порядок как в AE: блюр добавлен к слою ПОСЛЕ тени, а
  // эффекты AE считаются снизу вверх — значит блюр применяется первым, и в CSS он тоже
  // идёт первым. Числа тени — из плана (plan.shadows.ins), своих 0 6px 18px у превью нет.
  // Ни блюра, ни тени — фильтра нет вовсе, как и было (вставка резкая).
  const fxFilter=(bl)=>{
    const blur=(bl>0)?('blur('+(bl*k).toFixed(2)+'px)'):'';
    const sh=(typeof aeShadowCss==='function')?aeShadowCss(pl.shadows&&pl.shadows.ins,k):'';
    return ((blur?blur+' ':'')+sh).trim();
  };
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
    // Размытие входа/выхода cam2-вставки: в .jsx на слой вешается Box Blur с ключами
    // ins.anim.blur (INS_BLUR 41 px -> 0 за INS_ENTER, обратно за INS_EXIT), и БЕЗ него
    // кадр входа расходился с AE: у нас вставка «проявлялась прозрачностью» резкой, в AE
    // приходила из размытия. Ключи — из плана, своей формулы у превью нет.
    let bl=0;
    if(anim&&anim.blur)bl=keysAt(anim.blur,null,tm);
    // Осевший Scale слоя (см. шаблон: Ss=scale*sc/100) — нужен и наезду cam2 (m — доля
    // от него), и радиусу скругления маски: он задан в px ПРЕКОМПА и растёт вместе со
    // слоем. Считается ОДИН раз на кадр: вторая копия формулы разошлась бы с первой.
    const S=(x.scale||44)*(x.sc||100)/100;
    if(style==='cam2'&&anim){
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
      el.style.filter=fxFilter(bl);
      const cp=(style==='cam1'&&!x.oncam2)?ipvCamChild(px+sx,py+sy,z):[px+sx,py+sy];
      el.style.transform='translate('+(cp[0]*k)+'px,'+(cp[1]*k)+'px)';
      return;}
    const im=el.querySelector('img');
    if(im){im.style.width=(c.pw*m*k*z)+'px';im.style.height=(c.ph*m*k*z)+'px';}
    // Скругление окна маски: в .jsx маска-«Скругление» лежит на слое прекомпа, и радиус
    // (INS_MASK_R, px прекомпа) приходит на экран умноженным на масштаб слоя. Число —
    // ИЗ ПЛАНА (ins.mask_r): своей копии 60 у превью нет, и правило «у кого маски нет»
    // (вид white/none, вставка на подложке) решает тоже план — как шаблон.
    const mr=(x.mask_r!=null)?+x.mask_r:0;
    el.style.borderRadius=(mr>0)?((mr*(S/100)*m*z*k).toFixed(2)+'px'):'';
    el.style.opacity=op;
    el.style.filter=fxFilter(bl);   // фильтр на всей карточке: тень и блюр, маска и фото разом, как эффекты на слое в AE
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
// ---- слой ПЕРЕХОДА на входе и выходе видеовставки (Quick 2) ------------------------
// В .jsx на каждой видеовставке стоит слой перехода (`addTransAt`): tl.startTime =
// cut − TR_IN, то есть слой начинается ЗА TR_IN до стыка, играет файл от нуля до его
// конца, лежит наложением Add и поверх всего (transLayers.moveToBeginning). Вход
// вставки поэтому виден РАНЬШЕ её start — ровно то, из-за чего кадр перед вставкой
// расходился с AE (владелец: на 29 с в AE уже синеватое размытие, у нас кадр камеры).
// Файл и сдвиг — из плана (plan.trans: media + in), своей копии TR_IN у превью нет.
function ipvTransPlan(){
  const pl=IPV.plan,tr=pl&&pl.trans;
  if(!tr||!tr.media)return null;
  const shift=+tr.in||0;
  const evs=[];
  for(const x of (pl.inserts||[])){
    if((x.t||x.type)!=='video'||!x.media)continue;      // переход — только у видеовставок
    evs.push(Math.round((+x.start-shift)*1e4)/1e4);     // вход: слой начинается ДО старта
    if(!x.noexit)evs.push(Math.round((+x.end-shift)*1e4)/1e4);}   // выход срезан катом — слоя нет
  return evs.length?{media:tr.media,evs:evs,w:+tr.w||0,h:+tr.h||0}:null;}
// Переходу нужен ПРОКСИ: исходник — ProRes 4K, браузер его не декодирует (та же беда,
// что у камер). Заказываем его той же дверью (/api/preview_proxy) и один раз на файл:
// пока прокси не готов, элемент играет исходник, а на Windows не играет вовсе.
function ipvTransAsk(pl){
  const tr=pl&&pl.trans;
  if(!tr||!tr.media||typeof pvProxyLoad!=='function')return;
  if((typeof PVPX!=='undefined'&&PVPX.map&&PVPX.map[tr.media])||IPV.transAsked===tr.media)return;
  IPV.transAsked=tr.media;
  // Разновидность прокси — обычная, как у превью: allintra-прокси был нужен рендеру
  // без AE, когда камеры игрались прокси и каждый кадр перематывался. Теперь рендер
  // берёт кадры камер из ИСХОДНИКОВ (core/webrender.py), и переходу ключевой каждый
  // кадр не нужен: он тот же прокси превью, что и в живом плеере.
  pvProxyLoad(IPV.xml,true,[tr.media],false).then(px=>{if(!px)return;
    pvProxyMerge(px);
    if(PVPX.map[tr.media])ipvTransRefresh();
    else if(typeof pvProxyWatch==='function')pvProxyWatch($('ipvstage'));}).catch(()=>{});}
// Прокси перехода доехал (pvProxyRefresh) — элементу нужен новый src: у ProRes-исходника
// кадра не было вовсе, и без этой двери переход остался бы невидимым до перезахода в клип.
// Заказа не было вовсе (сборщик был занят прокси камер) — просим ещё раз: сборщик один
// на все прокси, и «занято» — не отказ.
function ipvTransRefresh(){
  const tp=ipvTransPlan();if(!tp)return;
  if(typeof PVPX!=='undefined'&&PVPX.map&&!PVPX.map[tp.media]){
    IPV.transAsked='';
    if(typeof ipvTransAsk==='function')ipvTransAsk(IPV.plan);}
  const box=$('ipvtrans');if(!box||!box.querySelectorAll)return;
  const want=pvSrc(tp.media);
  box.querySelectorAll('video').forEach(v=>{if(v.getAttribute('src')!==want){
    v._dead=false;                                   // новый источник — старая немощь не в счёт
    try{v.setAttribute('src',want);v.load();}catch(e){}}});
  IPV.cur=-2;if(IPV.vids.length)ipvUI(ipvNow());}
// Покадрово: у каждого слоя своё время файла (tm − старт слоя). Вне окна файла слой не
// виден — в AE за концом исходника слой тоже пуст (последний кадр не держится).
// Пул из 2 элементов на файл перехода вместо элемента на событие: два — чтобы два близких
// перехода не дрались за один элемент.
function ipvTrans(tm){
  const st=$('ipvstage');if(!st)return;
  const tp=ipvTransPlan();
  let box=$('ipvtrans');
  if(!tp){
    if(box){box.querySelectorAll('video').forEach(mediaFree);box.remove();}
    IPV.transSig='';return;}
  if(!box){box=document.createElement('div');box.id='ipvtrans';box.className='ipvtrans';
    box.dataset.noi18n='1';
    // Add в AE = сложение каналов: у CSS это plus-lighter. Нет его (старый браузер) —
    // screen: та же световая накладка, но с «выгоранием» на ярком.
    box.style.mixBlendMode=(typeof CSS!=='undefined'&&CSS.supports&&
      CSS.supports('mix-blend-mode','plus-lighter'))?'plus-lighter':'screen';
    st.appendChild(box);}
  const sig=tp.media+'|'+tp.evs.join(',');
  if(sig!==IPV.transSig){
    IPV.transSig=sig;
    box.querySelectorAll('video').forEach(mediaFree);
    box.innerHTML='';
    const poolSize=Math.min(2,tp.evs.length);
    for(let i=0;i<poolSize;i++){
      const v=document.createElement('video');
      v.className='ipvtransv';v.muted=true;v.volume=0;v.playsInline=true;v.preload='auto';
      // Файл браузеру не по зубам (ProRes без прокси) — помечаем: по такому <video>
      // нечего ждать кадр в рендере, а vSeekDone ждал бы полный таймаут на КАЖДОМ
      // кадре окна перехода (событие `error` приходит один раз).
      v.addEventListener('error',()=>{v._dead=true;});
      v.setAttribute('src',pvSrc(tp.media));
      box.appendChild(v);}}
  const k=(st.clientWidth||1080)/1080;
  const vids=box.querySelectorAll('video');
  let dur=0;
  vids.forEach(v=>{if(isFinite(v.duration)&&v.duration>0)dur=v.duration;});
  const evs=(tp.evs||[]).slice().sort((a,b)=>a-b);
  vids.forEach((v,ix)=>{
    const subEvs=evs.filter((_,i)=>i%vids.length===ix);
    if(!subEvs.length){
      if(v.style.display!=='none'){v.style.display='none';try{v.pause();}catch(e){}}
      return;}
    const active=subEvs.find(t0=>{const c=tm-t0;return c>=0&&(dur>0?c<dur:c<2.0);});
    let t0=active;
    if(t0==null){
      t0=subEvs[0];let minD=Math.abs(tm-t0);
      for(let i=1;i<subEvs.length;i++){
        const d=Math.abs(tm-subEvs[i]);
        if(d<minD){minD=d;t0=subEvs[i];}}}
    v.dataset.t0=String(t0);
    const ct=tm-t0;
    const curDur=(isFinite(v.duration)&&v.duration>0)?v.duration:dur;
    // Размер — ВЕЛИЧИНА ИСХОДНИКА из плана (в AE слой лежит 1:1 в кадре, и в кадре
    // видна его центральная часть). Играем прокси — он мельче исходника, и «по
    // videoWidth» кадр обрезался бы иначе: у 720p-прокси в кадр влезла бы половина
    // ширины вместо четверти. Размера в плане нет — берём размер самого файла.
    const vw=tp.w||v.videoWidth, vh=tp.h||v.videoHeight;
    if(ct<0||(curDur>0&&ct>=curDur)||!(vw>0)){                 // до метаданных рисовать нечего
      if(v.style.display!=='none'){v.style.display='none';try{v.pause();}catch(e){}}
      return;}
    if(v.style.display==='none')v.style.display='';
    v.style.width=(vw*k)+'px';v.style.height=(vh*k)+'px';
    // Время ставим ТОЧНО на первом кадре слоя: у перехода весь смысл в моменте стыка
    // (вспышка горит на нём), а грубый допуск живой протяжки (0.4 с у длинных вставок)
    // здесь оставил бы слой на нуле файла и вспышка не совпала бы с катом. Дальше —
    // общий допуск: каждый кадр дёргать декодер незачем.
    const first=(v._at==null||v._at!==v.dataset.t0);
    if(first||Math.abs((+v.currentTime||0)-ct)>vidSeekTol()){
      v._at=v.dataset.t0;try{v.currentTime=ct;}catch(e){}}
    if(IPV.playing&&v.paused)v.play().catch(()=>{});
    if(!IPV.playing&&!v.paused)v.pause();});}
// Покадровая отрисовка вставки: только ГЕОМЕТРИЯ (ipvInsPlace). Мозаика — отдельным
// проходом (ipvInsMosaics): она обязана идти ПОСЛЕ того, как <video> вставки встало на
// своё время, а внутри ipvInsDraw она попадала в тот же кадр, что и перемотка, и
// рисовала ПРЕДЫДУЩИЙ кадр ролика (в рендере — до `await` кадров).
function ipvInsDraw(wr,x,tm){
  ipvInsPlace(wr,x,tm);}
// Источник пикселей вставки: у фото это <img> (в маске или на подложке), у ВИДЕО —
// сам <video>: он и есть первый ребёнок обёртки, внутри него искать нечего. Здесь
// стоял `el.querySelector('img,video')`, и у видеовставки он возвращал null —
// мозаика не рисовалась вовсе (владелец: «видеовставка чистая, в AE мозаика»).
// Фото НА ПОДЛОЖКЕ: мозаика — на самом фото (`.iphoto`), а не на плашке: в AE эффект
// Mosaic стоит на слое вставки, плашка идёт отдельным слоем.
function ipvInsSrc(el){
  if(!el)return null;
  if(el.tagName==='IMG'||el.tagName==='VIDEO')return el;
  if(!el.querySelector)return null;
  return el.querySelector('img.iphoto')||el.querySelector('img,video');}
// Мозаика ВСЕХ видимых вставок кадра — одним проходом по обёрткам. `force` — перерисовать
// даже тот кадр <video>, что уже нарисован: в рендере время вставке ставят ДО ожидания
// кадра, и по кэшу (`cv._t`) холст остался бы с прежним кадром ролика.
function ipvInsMosaics(force){
  const ov=$('ipvins');if(!ov||!ov.querySelectorAll)return;
  ov.querySelectorAll('.ipvwrap.mosaic').forEach(wr=>{
    const el=wr.firstChild;if(!el)return;
    const src=ipvInsSrc(el);if(src)ipvPixelate(wr,src,force);});}
// Мозаика вставки: размер блока — как у эффекта Mosaic в AE (template.py: addMosaic
// ставит 64x64). Кадр сводится к блокам MOSAIC_BLOCK и растягивается обратно БЕЗ
// сглаживания: получается мозаика, а не размытие (здесь было `filter:blur(10px)`).
// Холст живёт В ОБЁРТКЕ рядом с источником: холст в браузере один на всех, а элементов
// на кадре бывает несколько.
function ipvPixelate(wr,src,force){
  if(!wr||!src||typeof document==='undefined')return null;
  const sw=(src.videoWidth||src.naturalWidth||0),sh=(src.videoHeight||src.naturalHeight||0);
  if(!sw||!sh){
    if(src.addEventListener&&!src._mosWait){
      src._mosWait=true;
      const retry=()=>{src._mosWait=false;ipvPixelate(wr,src,true);};
      src.addEventListener('loadeddata',retry,{once:true});
      src.addEventListener('seeked',retry,{once:true});}
    return null;}
  // Размер холста — CSS-коробка элемента, а не его собственные пиксели: коробку ставит
  // ipvInsPlace, и она же видна в кадре. Ролик 4K, сжатый в карточку, иначе рисовался бы
  // за краями своей коробки.
  let w=0,h=0;
  if(src.getBoundingClientRect){const r=src.getBoundingClientRect();w=Math.round(r.width);h=Math.round(r.height);}
  if(!w||!h){w=src.clientWidth||0;h=src.clientHeight||0;}
  if(!w||!h)return null;
  let cv=(wr.querySelector&&wr.querySelector('canvas.ipvmosaic'))||null;
  if(!cv){cv=document.createElement('canvas');cv.className='ipvmosaic';
    src.parentNode.insertBefore(cv,src.nextSibling);}
  if(cv.width!==w||cv.height!==h){cv.width=w;cv.height=h;}
  cv.style.width=w+'px';cv.style.height=h+'px';
  // Позицию и масштаб холст берёт у ИСТОЧНИКА: ту же коробку, тот же translate и ту же
  // прозрачность ставит ipvInsPlace/photos — второго правила «где вставка» тут нет.
  // Холст абсолютный и центрирован, как источник: фото центрирует CSS (left/top 50% +
  // translate), видео — флекс обёртки. Раньше холст был обычным флекс-элементом и
  // вставал РЯДОМ с видео (обёртка — flex-строка), а не поверх него.
  cv.style.transform='translate(-50%,-50%) '+(src.style.transform||'');
  cv.style.opacity=src.style.opacity||'';
  src.style.visibility='hidden';                // источник остаётся в DOM и рисует кадр
  // Ролик перерисовывается, когда <video> сменил кадр: на паузе превью это новый кадр
  // монтажа, при игре — каждый кадр. Картинке хватает одной отрисовки. `force` — про
  // рендер: время вставке ставят ДО ожидания кадра, и по кэшу холст остался бы с прежним.
  const isVid=(src.tagName==='VIDEO');
  if(isVid&&src.addEventListener&&!src._mosBound){
    src._mosBound=true;
    const onSeek=()=>{
      const pWr=(src.closest?src.closest('.ipvwrap.mosaic'):null)||wr;
      if(pWr)ipvPixelate(pWr,src,true);};
    src.addEventListener('seeked',onSeek);
    src.addEventListener('loadeddata',onSeek);}
  const at=isVid?(+src.currentTime||0):-1;
  if(force||!isVid||cv._t!==at){
    cv._t=at;
    drawMosaic(cv,src,sw,sh);
  }
  return cv;}
// Один проход мозаики: уменьшить кадр до блоков и растянуть обратно БЕЗ сглаживания.
// Вынесено отдельной дверью — её зовёт тест (canvas в браузере не подделать целиком, а
// числа блоков проверить надо).
//
// Блок — MOSAIC_BLOCK px ИСХОДНИКА, как у эффекта Mosaic в AE: эффект стоит на слое ДО
// его Scale, поэтому в кадре блок выходит 64·ks, где ks — масштаб показа ролика. У 4K
// 2160×4096, заполняющего кадр 1080×1920, ks = 0.5 → блок 32 px. Замер по кадру
// владельца (rb_30): период мозаики в AE 34 px (дрожание wiggle(1,15) размывает
// среднее), то есть «64 px кадра» — неверно вдвое.
function drawMosaic(cv,src,sw,sh){
  const ctx=cv.getContext&&cv.getContext('2d');if(!ctx)return null;
  const k=(IPV.plan&&IPV.plan.w)?(cv.width/IPV.plan.w):1;   // px превью на px кадра
  // Сводим ИСХОДНИК к блокам m x n, поэтому блок в кадре = cv.width/cols = 64·cv.width/sw.
  const bw=Math.max(1,Math.round(MOSAIC_BLOCK*k)),bh=Math.max(1,Math.round(MOSAIC_BLOCK*k));
  const cols=Math.max(1,Math.round(sw*k/bw)),rows=Math.max(1,Math.round(sh*k/bh));
  if(!cv._small){cv._small=document.createElement('canvas');}
  const sm=cv._small;
  if(sm.width!==cols||sm.height!==rows){sm.width=cols;sm.height=rows;}
  const sctx=sm.getContext&&sm.getContext('2d');if(!sctx)return null;
  sctx.imageSmoothingEnabled=true;
  sctx.clearRect(0,0,cols,rows);
  sctx.drawImage(src,0,0,cols,rows);            // сводим кадр к блокам m x n
  ctx.imageSmoothingEnabled=false;              // растягиваем БЕЗ сглаживания — мозаика
  ctx.clearRect(0,0,cv.width,cv.height);
  ctx.drawImage(sm,0,0,cols,rows,0,0,cv.width,cv.height);
  return {cols:cols,rows:rows,bw:bw,bh:bh};}
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
      // Привязана ли группа к камере (intro_cam у камеры 1, intro_cam2 у групп на
      // перебивке): поле плана cam — из него ipvIntroPos решает, множит ли общий
      // масштаб интро смещение группы. Это дверь: забытый тут ключ — и откреплённая
      // группа кам2 в превью поедет от чужого масштаба, хотя в .jsx стоит на месте.
      cam:g.cam,
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
  const be=aeEase();          // эталон 35/90 — из умолчаний aeEase, своей пары чисел нет
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
    // «Раскрытие» играет F_DUR = 0.3 с — тем же временем, что фейд, масштаб, up/left/right
    // и счётчик (F_DUR в .jsx, core/xml2ae/template.py:743). Здесь стояла длительность
    // ГЛИТЧА (сорок четыре сотых): по ней буквы открывались позже, и на 3.0 с последняя
    // ещё не показывалась — в AE уже «КУДА», у нас «КУД».
    reveal:{
      dur:3/10,
      blur:268/10,
      scale:0.7,
      // Масштаб буквы у НЕоткрытой буквы — Scale 3D аниматора в .jsx (11 %). Это
      // единственное, чем аниматор раскрытия двигает буквы: прозрачность у них не
      // анимируется вовсе (её ведёт Opacity слоя, общая на слово).
      char_scale:11/100
    }
  };
}
// Длительность появления слова интро: у глитча своя (INTRO_ANIMS), у остальных —
// F_DUR (.jsx). Числа — те же, что уезжают в INTRO_FX; поле плана (intro_anims.f_dur)
// главнее любой копии: план несёт ровно то число, которым играют ключи .jsx.
function ipvIntroAppearDur(a){
  const pl=(typeof IPV!=='undefined'&&IPV.plan)||null;
  const p=pl&&pl.intro_anims;
  const v=(p&&p.f_dur!=null)?+p.f_dur:0;
  return (v>0)?v:((a&&a.dur!=null)?+a.dur:0);}
// Длительность счётчика интро и появления слова-счётчика — HL_DUR (.jsx,
// core/xml2ae/layout.py: HL_DUR = 0.35 с). Ею играют ОБА ключа слайдера счётчика
// (`slP.setValueAtTime(t0,0); slP.setValueAtTime(t0+HL_DUR*SQ,target)` в
// core/xml2ae/plan_intro_tpl.py) и фейд слова со счётчиком без своей анимации
// (ветка hasCnt в introAnimFX). Здесь стояло 1.5 с по кривой: на кадре владельца
// 2.5 с (слово «19» — на 2.1 с) в AE уже 19, а превью показывало 10.
// Поле плана главнее: если бэкенд отдаст HL_DUR сам, превью возьмёт его, а не копию.
function ipvIntroHlDur(){
  const pl=(typeof IPV!=='undefined'&&IPV.plan)||null;
  const v=(pl&&pl.hl_dur!=null)?+pl.hl_dur:0;
  return (v>0)?v:35/100;}
// Параметры появления слова интро. «Раскрытие» играет столько же, сколько фейд и
// масштаб, — F_DUR .jsx (ключи блюра, Scale слоя и Percent Offset селектора стоят на
// t0+F_DUR*SQ). Число приезжает ИЗ ПЛАНА (`intro_anims.f_dur`, он же reveal.dur) —
// своей копии у превью нет; значение по умолчанию осталось только для сборок без
// блока параметров (живой замер владельца: с длительностью ГЛИТЧА буквы открывались
// позже AE — на 3.0 с последняя ещё не показывалась, в AE уже «КУДА», у нас «КУД»).
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
      dur:ipvIntroAppearDur(d.reveal),
      // Блюр — поле плана blur_css: пиксели CSS, а не «Blurriness» AE (см. hl_blur_css
      // у субтитров: перевод один — core/xml2ae/layout.css_blur_px). Поля нет (старый
      // бэкенд) — берём число как было, чтобы раскрытие не осталось вовсе без размытия.
      blur:(r.blur_css!=null)?r.blur_css:((r.blur!=null)?r.blur:d.reveal.blur),
      scale:(r.scale!=null)?r.scale:d.reveal.scale,
      // Масштаб неоткрытой буквы: его несёт план (scale_3d аниматора из INTRO_ANIMS),
      // значение по умолчанию — та же константа шаблона (11 %).
      char_scale:((r.scale_3d&&r.scale_3d.length)?(+r.scale_3d[0]/100):d.reveal.char_scale)
    }
  };
}
// Открытие буквы в анимации «reveal»: доля открытия буквы ci из n при общем ходе u (0..1).
// Это Percent Offset селектора в AE, едущий от −100 до 100 за F_DUR, с формой «Ramp Up»:
// выбранная часть текста едет по слову, и буква на своём месте в слове попадает в РАМПУ.
// Рампа занимает всю ширину слова, поэтому буква открывается не мгновенно на своём шаге,
// а плавно: доля буквы = clamp(2u − p), где p — её середина в долях слова.
//
// Почему не «ступенькой»: раньше буква открывалась на отрезке [i/n, (i+1)/n], и на кадре
// владельца (3.0 с, u = 0.77) последняя буква «КУДА» получала 0.07 — то есть гасла
// (её вела ПРОЗРАЧНОСТЬ), хотя в AE она уже растёт масштабом и видна. Замер по кадру AE
// (rb_3): четыре буквы, последняя в анимации; у нас было три.
function ipvRevealU(u,ci,n){
  const total=Math.max(1,+n||1);
  const p=((+ci||0)+0.5)/total;                  // середина буквы в долях ширины слова
  return Math.min(1,Math.max(0,2*(+u||0)-p));}
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
      // fx=='glow' здесь — поле ПЛАНА, а не строки клипа: его ставит план по единой
      // галке стиля intro_accent_glow (accent-строкам при включённой), поэтому превью
      // показывает ровно то же свечение, что сборка, и поле fx строки не читает.
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
      if(gi>=0&&!ipvRenderMode()&&IPV.intro[gi].lines[IPV.intro[gi].lines.length-1]===l){
        // Ручка масштаба — элемент ПРАВКИ, а не картинки: в режиме рендера её нет
        // вовсе. Живой случай: черта с ручкой попала в готовый ролик под текстом
        // интро (кадр снимается с этой же страницы). CSS её тоже прячет
        // (.render-mode .intro-scale-handle) — здесь она и не создаётся: нечего
        // показывать и нечего тащить в снимок.
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
    // в .jsx. Непрозрачность приходит СЫРЫМ значением AE 0..255: проценты ручки
    // пересчитаны на бэке (plan_style.read_style), и здесь второго пересчёта нет.
    // Перевод в CSS — ОДНА дверь на всё превью (aeShadowCss, тот же, что у субтитров,
    // вставок и плашки): своих чисел мягкости и смещения здесь больше нет.
    // Фильтр ставится ДО transform блока, поэтому масштаб группы (ds, зум камеры)
    // учитывается сам. Нет shadow (старый бэкенд без перезапуска) — фильтр пустой.
    // typeof — стенды отдельных тестов вырезают ipvIntro без соседних функций файла.
    const sh=g.shadow;
    if(sh&&sh.fill&&typeof aeShadowCss==='function'){
      const k=(io.clientWidth||(pl?pl.w:1080))/(pl?pl.w:1080);
      const cs=(pl&&pl.intro_comp_shadow)||{};
      io.style.filter=aeShadowCss({op255:sh.op, dir:(cs.dir!=null?+cs.dir:135),
        dist:(cs.dist!=null?+cs.dist:0), soft:(cs.soft!=null?+cs.soft:287),
        color:sh.fill},k);
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
      // Число счётчика (isCount): в .jsx это Slider Control с ДВУМЯ ЛИНЕЙНЫМИ ключами —
      // 0 на моменте своего слова и цель на t0+HL_DUR*SQ (plan_intro_tpl.py). Ни кривой
      // (easePair у слайдера нет), ни второго числа длительности тут быть не должно:
      // на кадре владельца 2.5 с (слово «19» — на 2.1 с, SQ = 0.8482) в AE уже 19,
      // а превью считало 1.5 с по кривой и показывало 10.
      let curText=sp.dataset.origWord;
      if(isCount){
        const uCnt=Math.min(1,Math.max(0,dt/(ipvIntroHlDur()*sq)));
        const curVal=(+sp.dataset.cntTarget)*uCnt;
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
          // По кривой (easePair) в .jsx играет ТОЛЬКО прозрачность слоя — остальные ключи
          // раскрытия линейные (setValueAtTime без ease): масштаб слоя 70→100, Gaussian
          // Blur от блюра плана к нулю и ход селектора Percent Offset −100→100 за F_DUR*SQ.
          const qL=ipvEase(uL);
          sp.style.opacity=qL.toFixed(3);
          const rBl=(rP.blur!=null)?rP.blur:0;
          const bl=((1-uL)*rBl).toFixed(1);
          sp.style.filter=(1-uL>0.01&&rBl>0)?('blur('+bl+'px)'):'';
          const rSc=(rP.scale!=null)?rP.scale:0.7;
          sp.style.transform='scale('+(rSc+(1-rSc)*uL).toFixed(3)+')';
          // Буквы открываются СЛЕВА НАПРАВО по времени плана — как Percent Offset
          // селектора в AE (форма «Ramp Up»): буква открывается на своём месте в слове,
          // а последняя — ровно к концу появления. Открытие — РОСТ МАСШТАБА буквы
          // (аниматор Scale 3D: 11 % → 100 %), а не прозрачность: Opacity в .jsx висит на
          // СЛОЕ слова (`op.setValueAtTime(t0,0); …(t0+F_DUR*SQ,100); easePair(op)`), и
          // у буквы ключей прозрачности нет ВООБЩЕ — она всегда непрозрачная.
          const chars=Math.max(1,curText.length);
          const cSc=(rP.char_scale!=null)?rP.char_scale:0.11;
          ipvRenderChars(sp,curText,(chEl,ci,n)=>{
            const qC=ipvRevealU(uL,ci,Math.max(1,n||chars));
            // Прозрачность буквы — инлайном и ВСЕГДА полная. Правило `.ipvintro span{opacity:0}`
            // (static/app.css) прячет КАЖДЫЙ span без класса `on`, а `on` стоит на СЛОВЕ:
            // с пустым инлайном (как было) буквы гасли на время раскрытия, и кадры 1.0 и
            // 3.0 с выходили ПУСТЫМИ, хотя в AE буквы уже растут масштабом.
            chEl.style.opacity='1';
            chEl.style.transform=(qC>=1)?'':('scale('+(cSc+(1-cSc)*qC).toFixed(3)+')');
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
        // Слово со счётчиком без своей анимации появляется за HL_DUR (ветка hasCnt в
        // introAnimFX), обычное — за F_DUR: обе длительности из .jsx, не свои числа.
        const dur=(isCount?ipvIntroHlDur():0.3)*sq;
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
  // База блока — от центра кадра. Масштаб общего нула (G) множит её ТОЛЬКО у группы,
  // привязанной к камере (поле cam из плана, считается из intro_cam/intro_cam2): у
  // откреплённой нул стоит в координатах кадра и масштаба родителя не наследует — там
  // база едет как есть, без G (числа уже посчитаны так же в plan_intro). Второго чтения
  // ключей стиля во фронте нет.
  const G=((pl&&pl.intro_scale!=null)?pl.intro_scale:100)/100;
  const gc=(g&&g.cam!==false)?G:1;
  const cc=ipvIntroChild(gc*(g?g.dx||0:0), (g?g.y||0:0)+gc*(g?g.dy||0:0), tm!=null?tm:ipvNow(), !!(g&&g.on2));
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
// Длину берём из contentDur (длина контента): хвостовой дисклеймер продлевает ползунок
// (IPV.dur), но блоков и линейки за концом ролика быть не должно.
let ITL={pps:0};
function itlFitPps(){const el=$('itl');const d=IPV.contentDur||IPV.dur;return (el&&d)?Math.max(1,el.clientWidth-2)/d:10;}
function itlFit(){ITL.pps=itlFitPps();itlDraw();const el=$('itl');if(el)el.scrollLeft=0;}
function itlZoom(f,px){const el=$('itl');if(!el||!(IPV.contentDur||IPV.dur))return;
  if(!ITL.pps)ITL.pps=itlFitPps();
  const cx=(px!=null)?px:el.clientWidth/2;
  const tm=(el.scrollLeft+cx)/ITL.pps;                       // время под курсором держим на месте
  ITL.pps=Math.max(itlFitPps(),Math.min(80,ITL.pps*f));
  itlDraw();el.scrollLeft=Math.max(0,tm*ITL.pps-cx);}
function itlDraw(){const el=$('itl'),inn=$('itlin');if(!el||!inn)return;
  const dur=IPV.contentDur||IPV.dur;
  if(!dur){inn.style.width='100%';$('itlblocks').innerHTML='';$('itlruler').innerHTML='';itlPh(0);return;}
  if(!ITL.pps)ITL.pps=itlFitPps();
  inn.style.width=Math.ceil(dur*ITL.pps)+'px';
  const steps=[1,2,5,10,15,30,60,120];const st=steps.find(s=>s*ITL.pps>=70)||120;
  let R='';for(let tm=0;tm<=dur;tm+=st)R+='<i style="left:'+(tm*ITL.pps)+'px">'+fmtT(tm)+'</i>';
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
    insSetSDCard(x,s,d);                       // и в карточку шага 2: она источник истины для ensureJobs
    blk.style.left=(s*ITL.pps)+'px';blk.style.width=Math.max(8,d*ITL.pps)+'px';
    blk.dataset.t=fmtIns(s)+' — '+fmtIns(s+d)+' ('+d+t('с')+') · '+insLbl(x);};
  const up=()=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);
    ipvAfterEdit();                            // ОДИН раз по отпусканию: re-render (renderIns) посреди драга уносил бы блок из-под пальца
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
  // Отмена/повтор правок вставок — Ctrl+Z, Ctrl+Shift+Z и Ctrl+Y, пока открыто окно
  // вставок или превью шага 3. В поле ввода их не перехватываем: там своя отмена текста.
  // «я»/«н» — те же клавиши в русской раскладке (как хоткеи редактора нарезки, 70-editor.js).
  if((e.ctrlKey||e.metaKey)&&!e.altKey&&!typing&&tg!=='select'){
    const k=(e.key||'').toLowerCase();
    if(k==='z'||k==='я'||k==='y'||k==='н'){
      e.preventDefault();
      if(k==='y'||k==='н'||e.shiftKey)insRedo();else insUndo();
      return;}
  }
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
  // Обратный пересчёт под формат: x/y вставки живут в БАЗОВЫХ единицах стиля
  // (1080×1920), а тянем мы в px кадра ролика. Кадр у форматов разной высоты,
  // поэтому ось делится на СВОЙ множитель: без этого перетащил на квадрате —
  // в вертикали уехало. Правило одно на фронт и сборку (core/style_geometry.py),
  // здесь — его обратная сторона.
  const ak=(typeof camFrameAxisK==='function')?camFrameAxisK():{x:1,y:1};
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
  // IPV.insShift — в px КАДРА (его прибавляет ipvInsPlace к координатам плана),
  // а st.x/st.y — базовые: переводим одно в другое этими двумя множителями.
  const move=ev=>{let dx=(ev.clientX-st.x0)/(k*z),dy=(ev.clientY-st.y0)/(k*z);
    [dx,dy]=axisLock(st,dx,dy,ev.shiftKey);
    IPV.insShift={i:i,dx:psh.dx+dx*ak.x,dy:psh.dy+dy*ak.y};
    ipvInsPlace(wr,x,ipvNow());};
  const up=ev=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);
    let dx=(ev.clientX-st.x0)/(k*z),dy=(ev.clientY-st.y0)/(k*z);
    [dx,dy]=axisLock(st,dx,dy,st.lock!==null);
    const nx=Math.round((st.x+dx)*10)/10,ny=Math.round((st.y+dy)*10)/10;
    if(onPlate){INS[real].kx=nx;INS[real].ky=ny;}else{INS[real].x=nx;INS[real].y=ny;}
    // сдвиг пишем и в карточку шага 2 — она источник x/y: драг правил только
    // INS, а ensureJobs при следующем открытии предпросмотра пересобирает список из карточек
    // и возвращал ноль. Карточку ищем по СТАБИЛЬНОМУ id вставки (insCardFor): у дублей одного
    // файла путь не различает, кто есть кто, и правка уезжала в первую карточку.
    // kx/ky уезжают туда же: у вставки на подложке карточка — источник сдвига ВСЕЙ карточки.
    const card=insCardFor(INS[real]);
    if(card){if(onPlate){card.kx=nx;card.ky=ny;}
              else{card.x=nx;card.y=ny;}}
    captureAE();ipvPlanSoon();
    if(x.card&&x.style==='cam1'&&!x.oncam2){
      // кам1 рисуется из ПЕЧЁНЫХ ключей anim.position (старых x/y) — до прихода нового плана
      // держим сдвиг поверх них, иначе она отпрыгнет назад на ~0.7с пока план пересчитается
      IPV.insShift={i:i,dx:psh.dx+dx*ak.x,dy:psh.dy+dy*ak.y};
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
  // gx/gy — базовые px стиля (intro_x/intro_y), а тянем в px кадра ролика: ось
  // делится на СВОЙ множитель формата, как у вставок. Иначе на квадрате сдвиг
  // уехал бы в вертикаль в 1.78 раза больше, чем просили.
  const ak=(typeof camFrameAxisK==='function')?camFrameAxisK():{x:1,y:1};
  const st={x0:e.clientX,y0:e.clientY,gx:INTRO[h].gx||0,gy:INTRO[h].gy||0,gs:INTRO[h].gs||100,lock:null};
  if(handle){e.preventDefault();e.stopPropagation();}
  const move=ev=>{let dx=(ev.clientX-st.x0)/(k*G)/ak.x,dy=(ev.clientY-st.y0)/(k*G)/ak.y;
    if(handle)pl.intro[gi].ds=Math.max(20,Math.min(300,st.gs+dx));
    else{[dx,dy]=axisLock(st,dx,dy,ev.shiftKey);pl.intro[gi].dx=st.gx+dx;pl.intro[gi].dy=st.gy+dy;}   // кэш плана — блок едет за пальцем
    ipvIntroPos(gi);};
  const up=ev=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);
    let dx=(ev.clientX-st.x0)/(k*G)/ak.x,dy=(ev.clientY-st.y0)/(k*G)/ak.y;
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

