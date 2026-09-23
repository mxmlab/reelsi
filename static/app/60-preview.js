// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Maxim Si
// плеер предпросмотра, громкость, панель слов, разметка интро
//
// Часть интерфейса Reelsi. Файлы static/app/ грузятся ПО ПОРЯДКУ ИМЁН обычными
// <script>-тегами (не модулями): один общий скоуп, как было в едином app.js.
// Порядок важен — объявления функций поднимаются в пределах своего файла.

// ================= preview player (ported) =================
let PV={vids:[],bufs:[],cams:null,segs:[],audio:[],words:[],dur:0,aidx:0,vidx:-1,primed:-1,curCi:-1,rollCi:-1,scrubbing:false,scrubT:0,playing:false,raf:0,xml:''};
// ===== двойной буфер камеры 1: склейка без замирания =====
// Раньше на КАЖДОМ стыке плеер делал av.currentTime=av.src прямо в момент склейки.
// Это честный seek-декодера (флаш буфера -> ближайший ключевой кадр -> декод вперёд):
// на 4K long-GOP 0.2-0.4 с замирания, причём вместе со звуком — звук идёт с этого же
// <video>. Видно было именно «в разрезе»: на смежных кусках seek не делается (порог
// 0.06) и там всё гладко.
// Лечим дублёром: второй <video> на том же файле заранее уводится на РАЗБЕГ перед
// нужным кадром, за PV_PREROLL до стыка пускается вживую (немой, невидимый), а на
// стыке элементы меняются местами — seek к этому моменту давно отыгран в фоне.
// Дублёр всегда лежит ВНЕ PV.vids, живой — всегда PV.vids[0]: весь остальной код
// (редактор, звук, camVisual, ED.cs) о подмене не знает.
const PV_PREROLL=0.35;   // сколько дублёр играет до стыка, прежде чем стать живым
const PV_SWAP_LO=-0.12;  // допуск позиции дублёра на стыке: не добежал (сек)
const PV_SWAP_HI=0.5;    // ...и перебежал; вне окна — откат на старый путь (seek на месте)
// ===== общая громкость видео-превью (монтаж / интро / раскладка камер) =====
// Одна настройка на все три плеера, живёт отдельным ключом localStorage (глобальная,
// как выбранный шаг). Ставим громкость ВСЕМ <video> плеера (слышен только не-
// заглушённый), чтобы при смене активной камеры уровень не прыгал. Новые <video>
// (см. openPreview/ipvRefresh/cpvOpen) берут MEDIA_VOL прямо при создании.
let MEDIA_VOL=1;
try{const _v=parseFloat(localStorage.getItem('reelsi_vol'));if(_v>=0&&_v<=1)MEDIA_VOL=_v;}catch(e){}
function applyMediaVol(){[PV,IPV,CPV].forEach(P=>{if(P&&P.vids)P.vids.forEach(v=>{if(v)v.volume=MEDIA_VOL;});
  if(P&&P.bufs)P.bufs.forEach(b=>{b.el.volume=MEDIA_VOL;});});
  // Музыка — тем же множителем, что голос. Иначе ползунок превью глушит ТОЛЬКО голос
  // (он идёт через <video>), музыка остаётся в полную, и баланс в превью врёт: юзер
  // компенсирует, ставит music_db −37, а в AE голос на полную — и музыки не слышно
  // (жалоба 2026-08-12). MEDIA_VOL — громкость прослушивания, она обязана менять
  // оба источника одинаково; в .jsx она не уезжает вообще.
  if(typeof MUSIC_EL!=='undefined'&&MUSIC_EL)MUSIC_EL.volume=MEDIA_VOL;
  if(typeof sfxSyncApply==='function')sfxSyncApply();}   // SFX-звуки тем же множителем
function setMediaVol(pct){MEDIA_VOL=Math.max(0,Math.min(1,(+pct||0)/100));
  try{localStorage.setItem('reelsi_vol',MEDIA_VOL);}catch(e){}
  applyMediaVol();syncVolUI();}
function syncVolUI(){const p=Math.round(MEDIA_VOL*100);
  document.querySelectorAll('input[data-vol]').forEach(s=>{if(+s.value!==p)s.value=p;});}
// ===== стилевые громкости (музыка/голос, dB) — как в рендере =====
// Уровни MUSIC_DB/VOICE_DB из стиля звучат в превью сразу, чтобы не собирать проект
// ради «не громко ли». Один AudioContext на всё. Источник создаётся ОДИН раз на элемент
// и только при его создании — и ОБЯЗАТЕЛЬНО у дублёров (bufMake): плеер двухбуферный,
// на стыке дублёр меняется местами с живым <video>, и не повешенный источник после
// первого же стыка пустит звук мимо регулятора.
let AUDIO=null,VG=null,MG=null;
function audioGraph(){if(AUDIO)return;
  try{AUDIO=new (window.AudioContext||window.webkitAudioContext)();
    VG=AUDIO.createGain();MG=AUDIO.createGain();
    VG.connect(AUDIO.destination);MG.connect(AUDIO.destination);}
  catch(e){AUDIO=null;VG=MG=null;}
  applyDbGains();}
function dbToGain(db){return Math.pow(10,(+db||0)/20);}
function applyDbGains(){if(!VG||!MG)return;const s=(typeof CURSTYLE!=='undefined'&&CURSTYLE)?CURSTYLE:{};
  VG.gain.value=dbToGain(s.voice_db!=null?s.voice_db:0);
  MG.gain.value=dbToGain(s.music_db!=null?s.music_db:-20);}
// Цензура: в рендере голос ныряет voice_db→−100 на окнах audio.censor из плана
// сцены. Окна считает scene_plan — здесь только «внутри окна или нет» и увод VG в ноль;
// вторую формулу не заводим. Вне окна возвращаем обычную громкость голоса из стиля.
function vgDuck(tm,plan){if(!VG)return;
  const cw=plan&&plan.audio&&plan.audio.censor;let mute=false;
  if(cw)for(const w of cw){if(tm>=w[0]&&tm<w[1]){mute=true;break;}}
  if(mute){VG.gain.value=0;return;}
  const s=(typeof CURSTYLE!=='undefined'&&CURSTYLE)?CURSTYLE:{};
  VG.gain.value=dbToGain(s.voice_db!=null?s.voice_db:0);}
function voiceWiring(v){if(!v||v.__wired)return;audioGraph();if(!VG)return;
  try{AUDIO.createMediaElementSource(v).connect(VG);v.__wired=true;}catch(e){}}
function pvFmt(s){return fmtT(s);}
// Заголовок шага 1 — имя ОТКРЫТОГО файла (как оно видно в списке клипов), а не название
// окна: у двух подряд открытых клипов заголовок был одинаковый и не говорил, что открыто.
// aria-label — то же имя (диалог называется тем, что в нём открыто); статический
// aria-label в разметке остаётся запасным — модалка всегда открывается отсюда.
function pvTitle(name){
  const ttl=$('mbPvTitle');if(!ttl)return;
  ttl.textContent=name||'';
  const dlg=ttl.closest('.modal');if(dlg&&name)dlg.setAttribute('aria-label',name);}
async function openEditClip(i){curEdit=i;const xml=CLIPS[i].xml;pvTitle(clipLabel(CLIPS[i]));
  openModal('mbPreview');await openPreview(xml);edOpen();}
let curEdit=-1;
// ===== превью-прокси камер =====
// Материал 4:2:2 10 бит (Sony/Canon) браузер НЕ берёт на аппаратный декодер:
// mediaCapabilities отвечает powerEfficient=false, и 4K жуётся софтом на проце —
// каждый seek на стыке сотни мс. Тот же материал в 720p 4:2:0 8 бит аппаратный.
// Прокси собирается ОТ ИСХОДНИКА, один раз на файл камеры, и правками нарезки не
// трогается (в отличие от черновика). Пока не готов — играем исходник, как раньше.
let PVPX={map:{},xml:'',poll:0,watch:[]};   // watch — стойки плееров, ждущих прокси (см. pvProxyWatch)
function pvSrc(path){return '/api/media?path='+encodeURIComponent(PVPX.map[path]||path);}
async function pvProxyLoad(xml,build){
  try{const d=await (await fetch('/api/preview_proxy',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({xml,build:!!build})})).json();
    if(d.error||!d.cams)return null;
    const m={};d.cams.forEach(c=>{if(c.ready&&c.proxy)m[c.path]=c.proxy;});
    return {map:m,building:!!d.building,total:d.cams.length,ready:Object.keys(m).length};
  }catch(e){return null;}}
// Карта копится, а не заменяется: PV/IPV/CPV открываются на разные клипы, а ключ —
// абсолютный путь исходника, так что чужие записи только помогают.
function pvProxyMerge(px){if(px)Object.assign(PVPX.map,px.map);return px;}
// Прогресс сборки — блоком ПОВЕРХ плеера. PXJOB на сервере один, поэтому
// блок рисует каждый плеер, который ждёт прокси (шаг 1 — монтаж, шаг 3 — вставки,
// раскладка камер): pvProxyWatch запоминает стойку, pvProxyPoll раздаёт ей свежие
// i/n/файл/процент. Пока сборка идёт — блок есть, кончилась — снимается.
function pvProxyBlock(stage,st){
  const pct=Math.max(0,Math.min(100,Math.round(+st.pct||0)));
  let el=stage.querySelector('.pvpx');
  if(!el){el=document.createElement('div');el.className='pvpx';
    el.innerHTML='<div class="pvpx_bar"><span class="pvpx_fill"></span></div>'+
      '<div class="pvpx_txt"></div><div class="pvpx_hint"></div>';
    stage.appendChild(el);}
  el.querySelector('.pvpx_fill').style.width=pct+'%';
  el.querySelector('.pvpx_txt').textContent=
    t('Готовлю прокси камеры {i}/{n}: {file} — {pct} %',
      {i:st.i||0,n:st.n||0,file:st.cur||'',pct:pct});
  el.querySelector('.pvpx_hint').textContent=t('один раз на файл, дальше из кэша');}
function pvProxyStages(st){const on=!!(st&&st.running);
  PVPX.watch=PVPX.watch.filter(id=>{
    const stage=$(id);if(!stage)return false;
    if(!on){const el=stage.querySelector('.pvpx');if(el)el.remove();return false;}
    pvProxyBlock(stage,st);return true;});}
function pvProxyWatch(stage){   // плеер ждёт прокси: следим за сборкой и показываем прогресс
  if(stage&&PVPX.watch.indexOf(stage)<0)PVPX.watch.push(stage);
  clearInterval(PVPX.poll);
  pvProxyPoll();                                 // статус сразу: иначе блок появится через 2 с
  PVPX.poll=setInterval(pvProxyPoll,2000);}
async function pvProxyPoll(){
  let s;try{s=await (await fetch('/api/preview_proxy_status')).json();}catch(e){return;}
  pvProxyStages(s);
  if(s.running)return;
  clearInterval(PVPX.poll);PVPX.poll=0;await pvProxyRefresh();}
function vLoaded(v){   // ждать метаданных после смены src (см. sparePrime/spareHandover)
  return new Promise(res=>{
    if(!v||v.readyState>=1)return res();
    const done=()=>{clearTimeout(T);v.removeEventListener('loadedmetadata',done);v.removeEventListener('error',done);res();};
    const T=setTimeout(done,3000);   // страховка: элемент мог быть выброшен сменой клипа
    v.addEventListener('loadedmetadata',done);v.addEventListener('error',done);});
}
function vSeeked(v){   // дождаться, когда элемент отыграл seek и декодировал кадр (переезд на паузе)
  return new Promise(res=>{
    if(!v||(!v.seeking&&v.readyState>=2))return res();
    const done=()=>{clearTimeout(T);v.removeEventListener('seeked',done);v.removeEventListener('canplay',done);v.removeEventListener('error',done);res();};
    const T=setTimeout(done,3000);   // страховка: прокси не открылся — просто не передаём эфир
    v.addEventListener('seeked',done);v.addEventListener('canplay',done);v.addEventListener('error',done);});
}
async function pvProxyRefresh(){   // прокси дособрались — обновить карту; живому <video> src не трогаем
  const px=await pvProxyLoad(PVPX.xml);if(!px)return;
  const was=JSON.stringify(PVPX.map);pvProxyMerge(px);
  if(JSON.stringify(PVPX.map)===was)return;
  // Переезд на прокси живому src не присваиваем: смена посреди игры сбрасывает элемент в
  // readyState 0 (чёрный кадр) и сдвигает время (баг). Стоящий плеер переезжает
  // дублёром сразу, играющий — на ближайшем стыке (sparePrime подтянет свежий src сам).
  for(const P of [PV,IPV,CPV])if(P&&P.vids&&P.vids.length&&!P.playing)await spareHandover(P);}
async function openPreview(xml){
  const stage=$('pvstage');
  const d=await (await fetch('/api/aicut_preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({xml})})).json();
  if(d.error){toast(errText(d));return;}
  clearInterval(PVPX.poll);PVPX.poll=0;PVPX.xml=xml;
  const px=await pvProxyLoad(xml,true);pvProxyMerge(px);
  // Правка нарезки режется по камере 1 — превью монтажа показывает только её: перебивки
  // вторых камер раскладываются позже и видны в предпросмотрах шага 2 (IPV/CPV), а тут
  // чужой исходник только сбивает (была жалоба «зачем в правках 2 камеры»).
  const cams=[d.cams[0]].filter(Boolean);PV.cams=cams;
  pvPause();PV.xml=xml;PV.segs=(d.segs||[]).map(s=>({...s,ci:0}));PV.audio=d.audio&&d.audio.length?d.audio:d.segs;PV.words=d.words;PV.aidx=0;PV.vidx=-1;
  PV.dur=d.dur||(PV.audio.length?PV.audio[PV.audio.length-1].te:0);
  [...stage.querySelectorAll('video')].forEach(v=>v.remove());
  PV.vids=cams.map((c,ix)=>{const v=document.createElement('video');
    v.src=pvSrc(c.path);v.preload='auto';v.muted=(ix!==0);v.playsInline=true;
    v.volume=MEDIA_VOL;v.style.zIndex=(ix===0)?'2':'1';stage.insertBefore(v,$('pvsub'));voiceWiring(v);return v;});
  PV.bufs=[];bufMake(PV,stage,$('pvsub'),0,0);
  PV.delta=camDeltas(PV);camBufs(PV,stage,$('pvsub'));   // тут камера одна, но контракт общий
  $('pvcam').textContent=cams.map((c,ix)=>(ix+1)+': '+(c.name||t('кам'))+(ix===0?t(' (звук)'):'')).join('  ·  ')+'  —  '+d.segs.length+t(' склеек · ')+Math.round(PV.dur)+t('с');
  if(px&&px.building)pvProxyWatch('pvstage');
  pvSeekTo(0);
}
function pvSegAt(list,tm){for(let i=0;i<list.length;i++){if(tm<list[i].te-1e-3)return i;}return Math.max(0,list.length-1);}
function pvUI(tm){const seek=$('pvseek');if(document.activeElement!==seek)seek.value=PV.dur?Math.round(tm/PV.dur*1000):0;
  $('pvtime').textContent=pvFmt(tm)+' / '+pvFmt(PV.dur);
  let cur='';for(const w of PV.words){if(tm>=w.s&&tm<w.e){cur=w.w;break;}}$('pvsub').textContent=cur;}
function pvApplyVisual(tm,play){camApply(PV,tm,play);}   // общая машина ракурсов, см. camApply
function pvSeekTo(tm){if(!PV.audio.length)return;PV.aidx=pvSegAt(PV.audio,tm);PV.vidx=-1;PV.primed=-1;
  const a=PV.audio[PV.aidx];try{PV.vids[0].currentTime=a.src+Math.max(0,tm-a.ts);}catch(e){}
  spareIdle(PV);camIdle(PV);pvApplyVisual(tm,false);pvUI(tm);   // разбег не готовим: на паузе\протяжке ползунка это лишний seek на каждый кадр
  if(ED.dur){ED.cs=a.src+Math.max(0,tm-a.ts);edDraw();edUI();}}
// --- дублёр камеры 1: общая машина всех плееров ------------------------------------
// Машина дублёра — ОБЩАЯ на все плееры (монтаж PV, редактор ED, вставки IPV, раскладка
// камер CPV): у всех цель одна и та же по смыслу — ИСХОДНОЕ время камеры 1. Поэтому она и
// хранится абсолютным исходным временем (b.at), а не индексом в списке сегментов; на этом
// же держится то, что редактор пользуется дублёром монтажного плеера, не заводя своего.
// P.bufs — дублёры, по одному на НЕПРЕРЫВНУЮ дорожку плеера. Слот 0 (ведущая камера, она
// же таймбаза) есть у всех. В раскладке камер добавляется второй — на звуковую камеру,
// когда звук слушают не с К1: её тоже гоняем через стык, только с постоянным оффсетом
// (CP.delta[k]) — поэтому у дублёра есть поле off.
function bufMake(P,stage,before,slot,off){
  const live=P.vids[slot];if(!live)return null;
  const el=document.createElement('video');
  // preload='metadata', а не 'auto': к дублёру обращаются ТОЛЬКО после явного seek на
  // разбег, качать \ демуксить начало файла второй раз — впустую удвоенный декод камеры
  el.src=live.src;el.preload='metadata';el.muted=true;el.playsInline=true;
  el.volume=MEDIA_VOL;el.style.zIndex='0';stage.insertBefore(el,before);voiceWiring(el);   // дублёр — под всеми камерами
  const b={el,slot,off:off||0,at:null,rolling:false};(P.bufs=P.bufs||[]).push(b);return b;}
function bufIdle(b){if(!b)return;b.at=null;b.rolling=false;
  b.el.pause();b.el.muted=true;b.el.playbackRate=1;b.el.style.zIndex='0';}
function bufArm(P,b,at){   // увести дублёра на разбег перед кадром at
  if(!b||P.scrubbing||(b.at!=null&&Math.abs(b.at-at)<1e-3))return;
  b.at=at;b.rolling=false;try{b.el.currentTime=Math.max(0,at-PV_PREROLL);}catch(e){}}
function bufRoll(b,left){   // left — сколько осталось до стыка, сек
  if(!b||b.at==null||left>Math.min(PV_PREROLL,b.at))return;
  if(b.el.seeking||b.el.readyState<3)return;   // не долезли — не беда, отработает старый путь
  // Дублёр обязан прийти на стык КАДР В КАДР. Пустить его «как есть» мало: тик, на котором
  // решаем пускать, сам приходит с опозданием (в фоновой вкладке rAF молчит и остаётся
  // страховочный интервал 120мс), и дублёр не добегал ~0.15с — это уже за окном допуска
  // PV_SWAP_LO, подмена срывалась, стык шёл через seek. Он немой и невидимый, поэтому
  // недобег гасим СКОРОСТЬЮ: сколько медиа осталось пройти на сколько времени осталось.
  b.el.playbackRate=Math.max(0.25,Math.min(2.5,(b.at-b.el.currentTime)/Math.max(0.05,left)));
  if(b.rolling)return;
  b.rolling=true;b.el.muted=true;b.el.play().catch(()=>{});}
function bufTake(P,b,at){   // подменить живой <video> своего слота дублёром
  if(!b||!b.rolling||b.at==null||Math.abs(b.at-at)>1e-3)return false;
  const d=b.el.currentTime-at;
  if(b.el.seeking||b.el.readyState<3||d<PV_SWAP_LO||d>PV_SWAP_HI){b.el.pause();b.rolling=false;return false;}
  return bufSwap(P,b);}
function bufSwap(P,b){   // обмен живой <video> ↔ дублёр: на стыке (bufTake) и на паузе (spareHandover)
  const old=P.vids[b.slot];P.vids[b.slot]=b.el;b.el=old;   // меняем местами
  old.pause();old.muted=true;old.style.zIndex='0';old.playbackRate=1;   // ушёл в дублёры — под камеры
  // вышел в эфир — скорость строго 1: разгон нужен был только чтобы попасть на стык
  P.vids[b.slot].playbackRate=1;
  P.vids[b.slot].volume=MEDIA_VOL;b.rolling=false;b.at=null;return true;}
function spareLead(P){return (P&&P.bufs&&P.bufs[0])||null;}   // дублёр ведущей камеры
// Обвязка под общий контракт плееров {audio:[{ts,te,src}], aidx}
function segGap(list,i){const a=list[i],b=list[i+1];   // сколько вырезано на стыке i->i+1, сек
  return (a&&b)?Math.abs(b.src-(a.src+(a.te-a.ts))):0;}
function spareIdle(P){(P.bufs||[]).forEach(bufIdle);}
function spareStop(P){(P.bufs||[]).forEach(b=>{b.el.pause();b.rolling=false;});}
function sparePrime(P){   // разбег к следующему стыку; свежий src дублёр подтягивает здесь
  const nx=P.audio[P.aidx+1];
  if(!nx||segGap(P.audio,P.aidx)<=0.06)return;   // смежные куски: подмена не нужна, живой доиграет
  // Переезд на прокси живому <video> src не меняет (сброс в readyState 0 = чёрный кадр, баг
  // BE). Дублёр под всеми камерами и нем — ему src меняем когда угодно: сравниваем с нужным
  // и при расхождении взводим заново только после загрузки метаданных. Не успел — bufRoll/
  // bufTake сами откажут по readyState<3, стык пройдёт старым путём, переезд повторится.
  (P.bufs||[]).forEach(b=>{
    const want=(P.cams&&P.cams[b.slot]&&P.cams[b.slot].path)?pvSrc(P.cams[b.slot].path):'';
    if(want&&b.el.src.split(location.origin).pop()!==want){
      b.el.src=want;b.at=null;b.rolling=false;   // смена src сбросила позицию — взводим заново
      vLoaded(b.el).then(()=>{if(P.audio[P.aidx+1]===nx)bufArm(P,b,nx.src+b.off);});
      return;
    }
    bufArm(P,b,nx.src+b.off);});}
function spareRollAt(P,tm){const a=P.audio[P.aidx];if(!a)return;
  (P.bufs||[]).forEach(b=>bufRoll(b,a.te-tm));}
function spareSwap(P){const nx=P.audio[P.aidx+1];if(!nx)return false;   // возвращаем судьбу ВЕДУЩЕЙ
  let lead=false;
  (P.bufs||[]).forEach(b=>{const ok=bufTake(P,b,nx.src+b.off);if(b.slot===0)lead=ok;});
  return lead;}
async function spareHandover(P){   // стоящий плеер: передача эфира дублёром, а не сменой src у живого
  if(!P||P.playing||P.scrubbing||!(P.vids||[]).length)return;
  const b=spareLead(P);if(!b||!(P.cams&&P.cams[b.slot]&&P.cams[b.slot].path))return;
  const live=P.vids[b.slot];
  const want=pvSrc(P.cams[b.slot].path);
  if(!want||live.src.split(location.origin).pop()===want)return;   // живой уже на свежем источнике
  if(b.el.src.split(location.origin).pop()!==want){
    b.el.src=want;b.at=null;b.rolling=false;   // смена src сбросила позицию — взводим заново
    await vLoaded(b.el);
    if(P.playing||P.scrubbing||!P.vids[b.slot])return;   // пока грузили, плеер тронули
  }
  try{b.el.currentTime=live.currentTime;}catch(e){return;}   // подводим к кадру, что на экране
  await vSeeked(b.el);
  if(P.playing||P.scrubbing||!P.vids[b.slot]||b.el.seeking||b.el.readyState<2)return;
  bufSwap(P,b);   // кадр к подъёму уже декодирован — чёрного нет
  camVisual(P,P.curCi>=0?P.curCi:0,false);   // обмен сбросил z-index живого в 0 — слой и звук по ракурсу
}
// --- переключение ракурса: ОБЩАЯ машина всех плееров -------------------------------
// Раньше это были три почти одинаковые копии (pvApplyVisual / ipvApplyVisual / cpvApply),
// и каждая правка моргания вносилась трижды — с неизбежным расхождением.
// Контракт плеера: P.vids — камеры (0 — ведущая, она же таймбаза), P.segs — EDL
// {ts,te,src,ci}, P.audioCi — с какой камеры слушаем (по умолчанию 0), P.delta[k] — оффсет
// камеры k от ведущей, P.curCi/P.vidx/P.defAt — состояние показа.
//
// Главное решение (2026-08-08, третий заход): вторичная камера — НЕ «спящий кадр, который
// будят на стыке», а ВТОРАЯ НЕПРЕРЫВНАЯ ДОРОЖКА. Снято одним дублём, синхрон один на весь
// клип, значит её исходное время = исходное время ведущей + ПОСТОЯННЫЙ оффсет (P.delta[k],
// тот же расчёт, что у звуковой камеры в раскладке). Она играет всегда, стыки склейки
// проходит своим дублёром — и в момент смены ракурса ей не нужно ни просыпаться, ни
// доезжать: переключение = смена z-index, кадр уже нужный.
//
// Так закрывается то, ради чего было два предыдущих захода. Держать её на паузе с сидкой на
// стык (заход 2) значило: не успел декодер — показываем ПРЕЖНЮЮ камеру лишние кадры, и это
// ровно то моргание, на которое жаловались. Держать её играющей, но править дрейф сидкой
// (заход 1) — сидка сама роняла кадр и давала тот же откат ракурса. Теперь: играет всегда +
// дрейф гасится СКОРОСТЬЮ (±6% незаметно) + стык проходится подменой дублёра.
//
// Правила, каждое снято с живого бага:
//  1) камера, которая УЖЕ на экране, с экрана не уходит: дрейф её не снимает;
//  2) прежнюю камеру после стыка не задерживаем — только пока входящая физически seeking,
//     и не дольше CAM_DEFER (это запасной путь, а не нормальная работа);
//  3) скорость трогаем только у НЕМЫХ камер: у ведущей и звуковой с неё идёт звук.
const CAM_SOFT=0.04;   // попали: скорость обратно в 1
const CAM_HARD=0.5;    // скоростью не догнать — только seek
const CAM_RATE=0.06;   // насколько ускоряем/замедляем догоняющую камеру
// Ракурс переключается ПОРЯДКОМ СЛОЁВ, а не прозрачностью. Слой <video> с opacity:0
// композитор вправе не рисовать вовсе, и на возврате в 1 первый кадр приходит с
// запозданием — на экране в этот момент остаётся то, что было под ним, то есть прежняя
// камера. Кадры видео тут не при чём, поэтому в логах плеера этого и не видно.
// Камеры кроют кадр целиком (inset:0 + object-fit:cover + чёрный фон), значит верхняя
// просто закрывает остальные: слои всегда непрозрачны и всегда отрисованы, меняется
// только кто сверху. Дублёры лежат ниже всех (z=0) — они играют с опережением.
function camVisual(P,ci,play){const ac=P.audioCi||0;
  P.vids.forEach((v,i)=>{v.style.opacity='1';v.style.zIndex=(i===ci)?'2':'1';v.muted=(i!==ac);
    if(play)v.play().catch(()=>{});});}   // играют ВСЕ камеры: см. «вторая непрерывная дорожка»
// Постоянный оффсет каждой камеры от ведущей — по первому её сегменту в EDL.
function camDeltas(P){const d=(P.vids||[]).map(()=>0);
  for(let k=1;k<(P.vids||[]).length;k++){const s=(P.segs||[]).find(g=>g.ci===k);if(!s)continue;
    const a=(P.audio||[])[pvSegAt(P.audio||[],s.ts)];if(!a)continue;
    d[k]=s.src-(a.src+(s.ts-a.ts));}
  return d;}
// Дублёр КАЖДОЙ используемой камере: на склейке она прыгает вместе с ведущей, и без дублёра
// это был бы seek — то самое замирание, только теперь ещё и на видимом ракурсе.
function camBufs(P,stage,before){
  for(let k=1;k<(P.vids||[]).length;k++){
    if(!(P.segs||[]).some(g=>g.ci===k))continue;
    bufMake(P,stage,before,k,(P.delta||[])[k]||0);}}
// время вторичной камеры: цель — исходное время ведущей + её оффсет.
function camTrack(P,v,want){
  const d=v.currentTime-want,ad=Math.abs(d);
  // Крупный разрыв (открылись, промотали, сорвалась подмена дублёра) скоростью не догнать —
  // seek, и ОДИН раз: пока декодер едет, повторный запрос каждый кадр сбрасывает готовый кадр.
  if(!P.playing||v.paused||ad>CAM_HARD){v.playbackRate=1;
    if(ad>0.02&&!v.seeking){try{v.currentTime=want;}catch(e){}}return;}
  v.playbackRate=(ad<=CAM_SOFT)?1:(d<0?1+CAM_RATE:1-CAM_RATE);}
function camApply(P,tm,play){
  if(!P.segs||!P.segs.length)return;
  let vi=pvSegAt(P.segs,tm);
  // ЧАСЫ ПЛЕЕРА ДРОЖАТ НА СТЫКЕ — вот откуда бралось моргание, пережившее все правки показа.
  // Время монтажа считается от currentTime ВЕДУЩЕЙ камеры, а на склейке её подменяет дублёр,
  // которому позволено стоять в окне PV_SWAP_LO..HI (до 0.12с НЕдобега). Сразу после подмены
  // tm считается уже от него — и на кадр-другой откатывается ЗА границу куска. pvSegAt честно
  // возвращает предыдущий кусок, а в нём прежняя камера: показ прыгал «новая → прежняя →
  // новая». Видно это было только счётчиком: на ОДНОЙ смене «кам» набирал 4 вместо 1.
  // Во время игры по кускам назад не ходим. Назад — это перемотка, а она всегда идёт через
  // seekTo, где P.vidx сбрасывается в -1 и запрет снимается.
  if(P.playing&&P.vidx>=0&&vi<P.vidx){vi=P.vidx;if(P.stats)P.stats.back++;}
  const s=P.segs[vi];if(!s)return;P.vidx=vi;
  const ac=P.audioCi||0,lead=P.vids[0];
  // ВСЕ немые камеры ведём каждый кадр, а не только ту, что сейчас в эфире: входящая должна
  // быть готова ДО стыка, иначе её нечем показать и приходится тянуть прежнюю (то самое
  // моргание). Звуковую (ac) не трогаем — её ведёт свой синхронизатор, ей нельзя менять
  // скорость: с неё идёт звук.
  if(lead)for(let k=1;k<P.vids.length;k++){const v=P.vids[k];
    if(v&&k!==ac&&!v.error)camTrack(P,v,lead.currentTime+((P.delta||[])[k]||0));}
  // Показываем РОВНО ТО, что просит EDL, и ни кадром иначе. Никаких «подождём готовности,
  // а пока подержим прежний ракурс»: ждать = показывать ДРУГУЮ камеру, и это ровно та
  // жалоба, из-за которой переписывалось всё остальное («мелькает прежняя камера»).
  // Цикл показа идёт 60 раз в секунду, поэтому «подержим всего пару кадров» — это и есть
  // видимая вспышка чужого ракурса. Худшее, что может случиться теперь: камера не успела
  // отыграть seek и пару кадров стоит на своём прежнем кадре — тот же ракурс, замерший на
  // мгновение. Это несравнимо мягче и, после перевода камер в непрерывные дорожки, редко.
  const ci=s.ci;
  if(P.stats&&P.curCi>=0&&ci!==P.curCi)P.stats.cam++;   // первый показ — не смена ракурса
  if(P.stats){const v=P.vids[ci];if(v&&v.seeking)P.stats.stale++;}
  P.curCi=ci;
  camVisual(P,ci,play);}
function camIdle(P){P.defAt=0;                         // перемотка/пауза: скорости в норму
  (P.vids||[]).forEach(v=>{if(v)v.playbackRate=1;});}
// ---------------------------------------------------------------------------
// Общий шаг всех плееров: время монтажа + продвижение блока на стыке. Гонка была одной и
// той же в трёх копиях (pvTick/ipvStep/cpvStep) — чиним одну функцию. Гонка: сидка
// av.currentTime=a.src асинхронна, и пока она едет, currentTime всё ещё показывает ПРЕЖНИЙ
// кусок — время из него улетало далеко за a.te, условие конца срабатывало снова и блоки
// проскакивали пачкой («проигрывается вырезанное, не переключается на следующий блок»).
// Поэтому пока element.seeking, время из currentTime не считаем — держим плейхед на начале
// куска, и блок не продвигаем.
function pvStep(P){
  const av=P.vids[0];if(!av||!P.audio.length)return null;
  let a=P.audio[P.aidx],tm,adv=false,swap=false,seeked=false;
  if(av.seeking){tm=a.ts;}
  else{
    tm=a.ts+(av.currentTime-a.src);
    if(tm>=a.te-0.03){
      if(P.aidx>=P.audio.length-1)return {tm:0,end:true};
      adv=true;
      const done=spareSwap(P);P.aidx++;a=P.audio[P.aidx];
      // дублёр уже стоит на нужном кадре и играет — seek не нужен
      if(done)swap=true;else{const live=P.vids[0];
        // смежные в исходнике сегменты (сохранённый ✂ без удаления) — без seek: микро-seek залипает
        if(Math.abs(live.currentTime-a.src)>0.06){seeked=true;try{live.currentTime=a.src;}catch(e){}}}
      tm=a.ts;sparePrime(P);
    }
  }
  return {tm,a,adv,swap,seeked};}
function pvTick(){if(!PV.playing||!PV.audio.length)return;
  const st=pvStep(PV);if(!st)return;
  if(st.end){pvPause();pvSeekTo(0);return;}
  pvApplyVisual(st.tm,true);
  spareRollAt(PV,st.tm);
  pvUI(st.tm);
  if(ED.dur){ED.cs=PV.vids[0].currentTime;edDraw();edUI();}  // плейхед редактора следует за монтажом (вид не прокручиваем — юзер листает сам)
  PV.raf=requestAnimationFrame(pvTick);}
function pvNow(){const a=PV.audio[PV.aidx];return a?a.ts+(PV.vids[0].currentTime-a.src):0;}
function pvPlay(){if(!PV.vids.length||!PV.audio.length)return;
  if(ED.play){ED.play=false;cancelAnimationFrame(ED.raf);const eb=$('edplay');if(eb)eb.innerHTML=ico('play');}  // редактор стоп: общий <video>
  PV.playing=true;$('pvplay').innerHTML=ico('pause');PV.vids[0].muted=false;PV.vids[0].play().catch(()=>{});
  sparePrime(PV);pvApplyVisual(pvNow(),true);PV.raf=requestAnimationFrame(pvTick);}
function pvPause(){PV.playing=false;const b=$('pvplay');if(b)b.innerHTML=ico('play');cancelAnimationFrame(PV.raf);
  PV.vids.forEach(v=>v.pause());spareStop(PV);camIdle(PV);}   // ни дублёр, ни разбег камеры не догорают на паузе
function pvToggle(){PV.playing?pvPause():pvPlay();}
// Разбег дублёра готовим ОДИН раз, когда протяжка улеглась. oninput у ползунка сыплется
// на каждый пиксель — seek дублёра на каждое событие оставлял его вечно «seeking», и на
// ближайшем стыке подмена срывалась в запасной путь. Симптом ровно такой: поводил
// ползунком — и стык снова замирает, хотя при обычном проигрывании всё гладко.
function pvScrub(v){const tm=v/1000*PV.dur;const was=PV.playing;
  PV.scrubbing=true;clearTimeout(PV.scrubT);
  pvPause();pvSeekTo(tm);if(was)pvPlay();
  PV.scrubT=setTimeout(()=>{PV.scrubbing=false;if(PV.playing)sparePrime(PV);},150);}

// Число ли слово — для кнопки счётчика «123» на чипе. Общая на панель слов шага AE
// и на разметку строк интро; второй копии правила в JS нет.
function isNumberWord(text){
  if(!text)return false;
  const s=(''+text).replace(/\s+/g,' ').trim();
  if(!s)return false;
  let dots=0,commas=0;
  for(let i=0;i<s.length;i++){if(s[i]==='.')dots++;else if(s[i]===',')commas++;}
  if(dots+commas>1)return false;
  const sep=(dots===1?'.':(commas===1?',':null));
  if(sep){
    const parts=s.split(sep);
    if(parts.length!==2)return false;
    const intClean=parts[0].replace(/ /g,'');
    const frac=parts[1];
    if(!intClean||!/^[0-9]+$/.test(intClean)||!frac||!/^[0-9]+$/.test(frac))return false;
    return true;
  }else{
    const intClean=s.replace(/ /g,'');
    if(!intClean||!/^[0-9]+$/.test(intClean))return false;
    return true;
  }
}
function wordBreakEl(cfg,prev,o){
  const b=document.createElement('span');
  b.className='brk';b.textContent='|';b.dataset.t=t('Стопка (клик — разрыв · Ctrl+клик — склейка)');
  b.dataset.a=cfg.words.indexOf(prev);b.dataset.b=cfg.words.indexOf(o);
  b.tabIndex=0;b.setAttribute('role','button');
  const toggleBreak=()=>{
    if(cfg.brk.has(prev.i)){
      cfg.brk.delete(prev.i);
    } else {
      cfg.brk.add(prev.i);
      cfg.jns.delete(prev.i);
    }
    if(cfg.syncBreak)cfg.syncBreak();
  };
  const toggleJoin=()=>{
    if(cfg.jns.has(prev.i)){
      cfg.jns.delete(prev.i);
    } else {
      cfg.jns.add(prev.i);
      cfg.brk.delete(prev.i);
    }
    if(cfg.syncBreak)cfg.syncBreak();
  };
  b.onclick=(e)=>{if(e&&(e.ctrlKey||e.metaKey)){e.preventDefault();toggleJoin();}else{toggleBreak();}};
  b.onkeydown=(e)=>{if(e.key!=='Enter')return;e.preventDefault();if(e.ctrlKey||e.metaKey){toggleJoin();}else{toggleBreak();}};
  return b;
}
function wordChipEl(cfg,o,k){
  const wi=cfg.words.indexOf(o);
  const c=document.createElement('span');c.className='chip';c.dataset.k=k;
  c.dataset.wi=wi;
  c.dataset.t=cfg.tip?cfg.tip(o):t('{s}с · двойной клик — в интро · Ctrl+клик — правка текста',{s:o.start});
  c.tabIndex=0;c.setAttribute('role','button');
  const txtSpan=document.createElement('span');txtSpan.textContent=o.w;c.appendChild(txtSpan);
  if(isNumberWord(o.w)){
    const cntBtn=document.createElement('span');
    cntBtn.className='chip-cnt';
    cntBtn.textContent='123';
    cntBtn.onclick=(e)=>{if(cfg.syncCount)cfg.syncCount(wi,e);};
    cntBtn.ondblclick=(e)=>{e.stopPropagation();};
    c.appendChild(cntBtn);
  }
  c.onclick=(e)=>{if(cfg.chipClick)cfg.chipClick(o,wi,c,e);};
  c.onkeydown=(e)=>{if(e.key!=='Enter')return;e.preventDefault();
    if(cfg.chipKeydown)cfg.chipKeydown(o,wi,c,e);};
  return c;
}
function wordsPaint(host,cfg){if(!host)return;
  const words=cfg.words,hl=cfg.hl,brk=cfg.brk,cnt=cfg.cnt,jns=cfg.jns;
  [...host.children].forEach(el=>{
    if(el.classList.contains('chip')){
      const o=words[+el.dataset.wi];
      if(o){
        el.classList.toggle('on',hl.has(o.i));
        const cntEl=el.querySelector('.chip-cnt');
        if(cntEl){
          const on=cnt&&cnt.has(o.i);
          cntEl.classList.toggle('on',on);
          cntEl.dataset.t=on?t('Счётчик включён (клик — выключить)'):t('Счётчик (клик — включить)');
        }
      }
      return;
    }
    if(!el.classList.contains('brk'))return;
    const a=words[+el.dataset.a],b=words[+el.dataset.b];
    const pair=!!(a&&b&&hl.has(a.i)&&hl.has(b.i));
    el.classList.toggle('hid',!pair);
    if(pair){
      const isBrk=brk&&brk.has(a.i);
      const isJns=jns&&jns.has(a.i);
      el.classList.toggle('on',isBrk);
      el.classList.toggle('jns',isJns);
      if(isJns){
        el.textContent='';
        el.style.color='';
        el.dataset.t=t('Склейка в строку (Ctrl+клик — снять склейку · клик — разрыв)');
      } else if(isBrk){
        el.textContent='|';
        el.style.color='';
        el.dataset.t=t('Разрыв стопки (клик — снять разрыв · Ctrl+клик — склейка)');
      } else {
        el.textContent='|';
        el.style.color='';
        el.dataset.t=t('Стопка (клик — разрыв · Ctrl+клик — склейка)');
      }
    } else {
      el.classList.remove('on','jns');
    }
  });}
function introWordEdit(el,word,save,cancel){
  if(!el||el.tagName==='INPUT')return;
  const inp=document.createElement('input');inp.type='text';inp.value=word;
  inp.className='iwordedit';inp.style.width=Math.max(60,word.length*10+22)+'px';
  let done=false;                                  // Esc/сохранение уже закрыли — blur не должен сохранять второй раз
  const commit=()=>{if(done)return;done=true;save(inp.value);};
  inp.onkeydown=(e)=>{e.stopPropagation();
    if(e.key==='Enter'){commit();}
    else if(e.key==='Escape'){done=true;cancel();}};
  inp.onblur=commit;                               // клик мимо = закрыть и сохранить
  inp.onclick=(e)=>e.stopPropagation();
  el.replaceWith(inp);inp.focus();inp.select();}
function aewEditIntroWord(el,wi){const o=WORDS[wi];if(!o)return;
  introWordEdit(el,o.w,s=>aewSaveWord(o,s),()=>aewRender());}
// Счётчик — на КАЖДОЕ слово-число строки: cnt_words хранит ПОЗИЦИИ слов внутри строки
// (0..count-1). Раньше это был один флаг строки на всё интро, и клик снимал счётчик со
// ВСЕХ остальных строк (правило «один счётчик на интро»); оно отменено — строки и кнопки
// независимы. p0 — позиция первого числа строки: без неё первую (легаси) разметку
// is_count нельзя превратить в список, и клик по второму числу потерял бы первое.
function introToggleCount(rows, i, p, p0){
  if(!rows||!rows[i])return;
  const r=rows[i];
  let list=Array.isArray(r.cnt_words)?r.cnt_words.slice():[];
  if(!Array.isArray(r.cnt_words)&&r.is_count&&p0!=null)list=[p0];
  const at=list.indexOf(p);
  if(at>=0)list.splice(at,1);else list.push(p);
  list.sort((a,b)=>a-b);
  r.cnt_words=list;                 // is_count всегда = «в списке есть позиции»
  r.is_count=list.length>0;
}
function introRowSetAnim(rows, i, val){
  if(!rows||!rows[i])return;
  rows[i].anim=val;
  if(val==='glitch'){
    let head=i;
    while(head>0&&!introIsHead(rows,head))head--;
    const end=introGroupEnd(rows,head);
    for(let k=head;k<end;k++){
      if(k!==i&&rows[k])rows[k].back=true;
    }
  }
}
function introRowHtml(cfg,r,i,idxs,gi){
  const A=cfg.arr,S=cfg.sync,W=cfg.words;
  const ws=idxs.map(x=>W[x].w),mid=(r.from!=null);
  const p0=idxs.findIndex(x=>isNumberWord(W[x].w));   // позиция первого числа строки: она же легаси-счётчик
  const sep=(i===0)?''
    :(mid?'<div class="xfade"><span class="bar"></span>'+ico('target')+t(' в середине')+'<span class="bar"></span></div>'
    :(r.break
      ?'<div class="xfade"><span class="bar"></span>'+ico('xfade')+t(' кросс-фейд ')+'<span class="rm" tabindex="0" role="button" aria-label="'+t('Убрать разрыв')+'" data-t="'+t('Убрать разрыв')+'" onclick="'+A+'['+i+'].break=false;'+S+'">'+ico('x')+'</span><span class="bar"></span></div>'
      :'<div class="xfade sep" tabindex="0" role="button" data-t="'+t('Разделить на два прекомпа')+'" onclick="'+A+'['+i+'].break=true;'+S+'"><span class="bar"></span>'+t('разделить')+'<span class="bar"></span></div>'));
  const fromBadge=mid
    ?'<span class="tag on" tabindex="0" role="button" style="cursor:pointer" aria-label="'+t('Убрать привязку')+'" data-t="'+t('Убрать привязку к слову')+'" onclick="'+A+'['+i+'].from=null;'+cfg.clearPick+';'+S+'">'+t('с «{w}»',{w:esc((W[r.from]||{}).w||('#'+r.from))})+ico('x')+'</span>'
    :'<button class="icon" data-t="'+t('Начать группу со слова из середины')+'" aria-label="'+t('Начать группу со слова из середины')+'" style="'+(cfg.pick===i?'color:var(--introhl,var(--subhl,var(--yel)))':'')+'" onclick="'+cfg.arm(i)+'">'+ico('target')+'</button>';
  const colVal = r.color || 'white';
  const animVal = r.anim || '';
  const fxVal = r.fx || '';
  const colorSelect = '<select style="min-width:0;width:76px;min-height:var(--h-sm);height:var(--h-sm);padding:2px 4px;font-size:12px;flex-shrink:0" aria-label="'+t('Цвет')+'" data-t="'+t('Цвет строки')+'" onchange="'+A+'['+i+'].color=this.value;if(this.value===\'custom\'&&!'+A+'['+i+'].fill)'+A+'['+i+'].fill=[1,1,1];this.blur();'+S+'">'
    +'<option value="white" '+(colVal==='white'?'selected':'')+'>'+t('белый')+'</option>'
    +'<option value="yellow" '+(colVal==='yellow'?'selected':'')+'>'+t('хайлайт')+'</option>'
    +'<option value="accent" '+(colVal==='accent'?'selected':'')+'>'+t('акцент')+'</option>'
    +'<option value="custom" '+(colVal==='custom'?'selected':'')+'>'+t('свой')+'</option>'
    +'</select>';
  const customColorInput = (colVal==='custom')
    ?'<input type="color" style="width:24px;height:24px;min-height:0;padding:0;border:1px solid var(--bd2);border-radius:var(--r-sm);cursor:pointer;flex-shrink:0" aria-label="'+t('Свой цвет')+'" data-t="'+t('Свой цвет строки')+'" value="'+(typeof rgb2hex==='function'?rgb2hex(r.fill||[1,1,1]):'#ffffff')+'" oninput="'+A+'['+i+'].fill=(typeof hex2rgb===\'function\'?hex2rgb(this.value):[1,1,1]);'+S+'">'
    :'';
  const animSelect = '<select style="min-width:0;width:84px;min-height:var(--h-sm);height:var(--h-sm);padding:2px 4px;font-size:12px;flex-shrink:0" aria-label="'+t('Появление')+'" data-t="'+t('Появление строки')+'" onchange="introRowSetAnim('+A+','+i+',this.value);this.blur();'+S+'">'
    +'<option value="" '+(animVal===''?'selected':'')+'>'+t('фейд')+'</option>'
    +'<option value="up" '+(animVal==='up'?'selected':'')+'>'+t('вверх')+'</option>'
    +'<option value="left" '+(animVal==='left'?'selected':'')+'>'+t('слева')+'</option>'
    +'<option value="right" '+(animVal==='right'?'selected':'')+'>'+t('справа')+'</option>'
    +'<option value="glitch" '+(animVal==='glitch'?'selected':'')+'>'+t('глитч')+'</option>'
    +'<option value="reveal" '+(animVal==='reveal'?'selected':'')+'>'+t('раскрытие')+'</option>'
    +'</select>';
  const fxSelect = '<select style="min-width:0;width:74px;min-height:var(--h-sm);height:var(--h-sm);padding:2px 4px;font-size:12px;flex-shrink:0" aria-label="'+t('Эффект')+'" data-t="'+t('Эффект строки')+'" onchange="'+A+'['+i+'].fx=this.value;this.blur();'+S+'">'
    +'<option value="" '+(fxVal===''?'selected':'')+'>'+t('нет')+'</option>'
    +'<option value="glow" '+(fxVal==='glow'?'selected':'')+'>'+t('свечение')+'</option>'
    +'</select>';
  const textColor = (colVal==='yellow'?'var(--introhl,var(--subhl,var(--yel)))':(colVal==='accent'?'var(--subhl3,#af1f1f)':(colVal==='custom'&&r.fill&&typeof rgb2hex==='function'?rgb2hex(r.fill):'var(--tx)')));
  return sep+'<div class="row introrow" data-ig="'+(gi-1)+'" style="align-items:center;margin-top:6px;flex-wrap:nowrap;gap:6px">'
    +introPlus(cfg.rows,i,cfg.add)
    +'<input type="number" min="0" step="1" style="width:48px;min-height:var(--h-sm);height:var(--h-sm);padding:2px 4px;text-align:center;flex-shrink:0" aria-label="'+t('Слов в строке')+'" value="'+r.count+'" oninput="'+A+'['+i+'].count=parseInt(this.value)||0;'+S+'">'
    +colorSelect
    +customColorInput
    +animSelect
    +fxSelect
    +'<label class="chk" style="margin:0;padding:0 4px;flex-shrink:0" data-t="'+t('Акцентный шрифт: другой шрифт и регистр этой строки (accent_font стиля; пусто = выключено)')+'"><input type="checkbox" aria-label="'+t('Акцентный шрифт')+'" '+(r.accent?'checked':'')+' onchange="'+A+'['+i+'].accent=this.checked;this.blur();'+S+'"></label>'
    +'<label class="chk" style="margin:0;padding:0 4px;flex-shrink:0" data-t="'+t('Задний план: строка уходит на задний план (шрифт back_font и регистр back_case из стиля)')+'"><input type="checkbox" aria-label="'+t('Задний план')+'" '+(r.back?'checked':'')+' onchange="'+A+'['+i+'].back=this.checked;this.blur();'+S+'"></label>'
    +'<label class="chk" style="margin:0;padding:0 4px;flex-shrink:0" data-t="'+t('Большое слева: строка встаёт слева крупно, остальные строки группы — стопкой справа (высота — по стопке)')+'"><input type="checkbox" aria-label="'+t('Большое слева')+'" '+(r.big?'checked':'')+' onchange="'+A+'['+i+'].big=this.checked;this.blur();'+S+'"></label>'
    +fromBadge
    // Слова строки — кликабельные: в общем списке их уже нет (introConsumed), и править
    // текст слова, ушедшего в интро, было негде вовсе. Клик = тот же /api/edit_word.
    +'<span class="grow" style="min-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:'+textColor+'">'
    +(ws.length?idxs.map((x,p)=>{
        const isNum=isNumberWord(W[x].w);
        const wHtml='<span class="iword" tabindex="0" role="button" data-t="'+t('Клик — изменить текст слова (Enter — сохранить, Esc — отмена)')+'"'
          +' onclick="'+cfg.edit+'(this,'+x+')"'
          +' onkeydown="if(event.key===\'Enter\'){event.preventDefault();'+cfg.edit+'(this,'+x+')}">'+esc(W[x].w)+'</span>';
        if(isNum){
          // Чип горит по СВОЕЙ позиции в cnt_words; без списка (старые задания) — по
          // легаси-флагу строки и попаданию в первое число.
          const on=Array.isArray(r.cnt_words)?r.cnt_words.includes(p):(!!r.is_count&&p===p0);
          const tip=on?t('Счётчик включён (клик — выключить)'):t('Счётчик (клик — включить)');
          return wHtml+'<span class="icnt '+(on?'on':'')+'" tabindex="0" role="button" data-t="'+tip+'"'
            +' onclick="event.stopPropagation();introToggleCount('+A+','+i+','+p+','+p0+');'+S+'">123</span>';
        }
        return wHtml;
      }).join(' ')
      :'—')
    +'</span>'
    +'<span class="x" tabindex="0" role="button" aria-label="'+t('Удалить строку')+'" data-t="'+t('Удалить строку интро')+'" style="flex-shrink:0" onclick="'+A+'.splice('+i+',1);'+S+'">'+ico('x')+'</span></div>';}

