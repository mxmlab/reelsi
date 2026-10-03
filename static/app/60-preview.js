// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Maxim Si
// плеер предпросмотра, громкость, панель слов, разметка интро
//
// Часть интерфейса Reelsi. Файлы static/app/ грузятся ПО ПОРЯДКУ ИМЁН обычными
// <script>-тегами (не модулями): один общий скоуп, как было в едином app.js.
// Порядок важен — объявления функций поднимаются в пределах своего файла.

// ================= preview player (ported) =================
// Плеер шага 1 — РЕДАКТОР НАРЕЗКИ: играет его единственный <video> камеры 1
// (ED.play/ED.cs), отдельных монтажных кнопок нет вовсе. Здесь живёт общий объект
// кадра PV: камеры, дублёр, слова и EDL клипа. Своего состояния воспроизведения
// (PV.playing) у него больше нет — состояние ровно одно, редактора (ED.play).
// voicePanel — id панели «Голос» ЭТОГО плеера: по ней дорожка обработанного голоса
// (vt*, ниже) берёт живые ручки шумодава, а не профиль спикера. У плееров без панели
// (шаг 3) поля нет — им остаётся профиль.
let PV={vids:[],bufs:[],cams:null,segs:[],audio:[],words:[],dur:0,aidx:0,vidx:-1,primed:-1,curCi:-1,rollCi:-1,scrubbing:false,scrubT:0,raf:0,xml:'',voicePanel:'pvvoice'};
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
  if(P&&P.bufs)P.bufs.forEach(b=>{b.el.volume=MEDIA_VOL;});
  // Дорожка обработанного голоса — тем же множителем: ползунок громкости превью обязан
  // менять оба источника, иначе «стало» звучит громче «было» само по себе.
  if(P&&P.vt&&P.vt.el)P.vt.el.volume=MEDIA_VOL;});
  // Музыка — тем же множителем, что голос. Иначе ползунок превью глушит ТОЛЬКО голос
  // (он идёт через <video>), музыка остаётся в полную, и баланс в превью врёт: юзер
  // компенсирует, ставит music_db −37, а в AE голос на полную — и музыки не слышно
  // (жалоба 2026-08-12). MEDIA_VOL — громкость прослушивания, она обязана менять
  // оба источника одинаково; в .jsx она не уезжает вообще.
  if(typeof MUSIC_EL!=='undefined'&&MUSIC_EL)MUSIC_EL.volume=MEDIA_VOL;
  if(typeof sfxSyncApply==='function')sfxSyncApply();}   // SFX-звуки тем же множителем
function setMediaVol(pct){MEDIA_VOL=Math.max(0,Math.min(1,(+pct||0)/100));
  try{localStorage.setItem('reelsi_vol',MEDIA_VOL);}catch(e){}
  applyMediaVol();syncVolUI();
  // Живой хост играет своим процессом, мимо страницы: громкость прослушивания
  // доходит до него этой командой (одна формула — 95-styles.js:voiceFxLiveOutDb),
  // иначе ползунок строки плеера глушил бы всё, кроме голоса из плагинов.
  if(typeof voiceFxLiveGain==='function')voiceFxLiveGain();}
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
  const vdb=s.voice_db!=null?s.voice_db:0;
  VG.gain.value=dbToGain(vdb);
  MG.gain.value=dbToGain(s.music_db!=null?s.music_db:-20);
  // Живому хосту — итог этой громкости и громкости прослушивания (одна формула
  // живёт там же: 95-styles.js:voiceFxLiveOutDb).
  if(typeof voiceFxLiveGain==='function')voiceFxLiveGain();}
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
// Заголовок шага 1 — имя ОТКРЫТОГО файла (как оно видно в списке клипов), а не название
// окна: у двух подряд открытых клипов заголовок был одинаковый и не говорил, что открыто.
// aria-label — то же имя (диалог называется тем, что в нём открыто); статический
// aria-label в разметке остаётся запасным — модалка всегда открывается отсюда.
function pvTitle(name){
  const ttl=$('mbPvTitle');if(!ttl)return;
  ttl.textContent=name||'';
  const dlg=ttl.closest('.modal');if(dlg&&name)dlg.setAttribute('aria-label',name);}
async function openEditClip(i){curEdit=i;const xml=CLIPS[i].xml;pvTitle(clipLabel(CLIPS[i]));
  if(curAE!==i||AEXML!==CLIPS[i].xml)selectAE(i);
  openModal('mbPreview');await openPreview(xml);await edOpen();
  // Панель «Голос» — ПОСЛЕ редактора, и это порядок, а не вкус: спикера она берёт по
  // ED.xml, а дорожку голоса заказывает по камере ЭТОГО плеера (vtPrep(ED)). Позванная
  // раньше (в openPreview до edOpen), она видела прежний клип и рисовала «У клипа нет
  // спикера», хотя тег у клипа есть, — и голос не запускался вовсе.
  await pvVoicePanel();}
let curEdit=-1;
// ===== превью-прокси камер =====
// Материал 4:2:2 10 бит (Sony/Canon) браузер НЕ берёт на аппаратный декодер:
// mediaCapabilities отвечает powerEfficient=false, и 4K жуётся софтом на проце —
// каждый seek на стыке сотни мс. Тот же материал в 720p 4:2:0 8 бит аппаратный.
// Прокси собирается ОТ ИСХОДНИКА, один раз на файл камеры, и правками нарезки не
// трогается (в отличие от черновика). Пока не готов — играем исходник, как раньше.
let PVPX={map:{},xml:'',poll:0,watch:[],height:0};   // watch — стойки плееров, ждущих прокси (см. pvProxyWatch)
function pvSrc(path){return '/api/media?path='+encodeURIComponent(PVPX.map[path]||path);}
// `extra` — файлы, которым прокси нужен не из-за камер: слой перехода видеовставки
// (Quick 2.mov — ProRes 4K, браузер его не декодирует вовсе). Собираются тем же
// сборщиком и той же дверью, что прокси камер: второй такой двери не заводится.
// `allintra` — all-intra разновидность прокси (ключевой кадр каждый): ею пользовался
// рендер без AE, пока камеры игрались прокси. Теперь рендер берёт кадры камер из
// исходников (core/webrender.py), и её никто не просит — параметр оставлен, потому что
// дверь /api/preview_proxy одна на всех, и её разновидность называет вызывающий.
async function pvProxyLoad(xml,build,extra,allintra){
  // height — КОРОТКАЯ сторона прокси. У живого превью это 720 (мельче не нужно: кадр
  // показывается в стойке шириной в треть экрана), у рендера без AE — короткая сторона
  // кадра ролика (1080 при 1080x1920): снимок идёт в натуральном размере, и 720-прокси
  // в нём был бы мылом. Ставит его вызывающий (см. templates/render.html).
  const height=PVPX.height||720;
  try{const d=await (await fetch('/api/preview_proxy',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({xml,build:!!build,height,extra:extra||[],allintra:!!allintra})})).json();
    if(d.error||!d.cams)return null;
    const m={};d.cams.forEach(c=>{if(c.ready&&c.proxy)m[c.path]=c.proxy;});
    (d.extra||[]).forEach(c=>{if(c.ready&&c.proxy)m[c.path]=c.proxy;});
    return {map:m,building:!!d.building,total:d.cams.length,ready:Object.keys(m).length};
  }catch(e){return null;}}
// Карта копится, а не заменяется: PV/IPV/CPV открываются на разные клипы, а ключ —
// абсолютный путь исходника, так что чужие записи только помогают.
function pvProxyMerge(px){if(px)Object.assign(PVPX.map,px.map);return px;}
// Прогресс сборки — блоком ПОВЕРХ плеера. PXJOB на сервере один, поэтому
// блок рисует каждый плеер, который ждёт прокси (шаг 1 — монтаж, шаг 3 — вставки,
// раскладка камер): pvProxyWatch запоминает стойку, pvProxyPoll раздаёт ей свежие
// i/n/файл/процент. Пока сборка идёт — блок есть, кончилась — снимается.
//
// Блок — строка ОБЩЕГО контейнера прогресса стойки (`pvProgRow`): `vtVoiceLine` держит
// там же строку голоса. Идти разом могут оба, и тогда видно две строки друг над другом,
// а не два абсолютных блока у одного края, где второй накрывает первый.
function pvProxyBlock(stage,st){
  const pct=Math.max(0,Math.min(100,Math.round(+st.pct||0)));
  const el=pvProxyBox(stage);
  el.querySelector('.pvpx_fill').style.width=pct+'%';
  // Текст статуса берём из ОБЩЕГО словаря прогресса (55-progress.js): своей формулировки
  // у этого блока нет — иначе строка про прокси правку в одном месте не поймает.
  el.querySelector('.pvpx_txt').textContent=
    progStatusFor('proxy',{i:st.i||0,n:st.n||0,pct:pct});
  el.querySelector('.pvpx_hint').textContent=t('один раз на файл, дальше из кэша');}
// Строка прогресса в общем контейнере низа кадра (`.pvprog`). Контейнер один на стойку и
// живёт, только пока в нём есть строки: с двумя независимыми абсолютными блоками у
// `bottom:0` второй рисовался поверх первого. Порядок строк задаёт CSS (`order`), а не
// очерёдность создания: кто пришёл раньше, тому место не занимать. Поиск — по атрибутам,
// не по классам: так же ищет и мини-DOM стендов.
function pvProgRow(stage,kind,cls){
  let box=stage.querySelector('[data-pvprog]');
  if(!box){
    box=document.createElement('div');box.className='pvprog';
    box.setAttribute('data-pvprog','1');stage.appendChild(box);}
  let row=box.querySelector('[data-pvrow="'+kind+'"]');
  if(!row){
    row=document.createElement('div');row.className=cls;
    row.setAttribute('data-pvrow',kind);box.appendChild(row);}
  return row;}
// Снять строку; опустевший контейнер уходит следом — пустая подложка закрывала бы кадр.
function pvProgDrop(stage,kind){
  const box=stage&&stage.querySelector('[data-pvprog]');
  if(!box)return;
  const row=box.querySelector('[data-pvrow="'+kind+'"]');
  if(row)row.remove();
  if(!box.children.length)box.remove();}
// Разметка строки прокси — одно место: строка заводится по потребности и не
// пересоздаётся на каждый опрос (иначе полоса моргала бы).
function pvProxyBox(stage){
  const fresh=!stage.querySelector('[data-pvrow="proxy"]');
  const el=pvProgRow(stage,'proxy','pvpx');
  if(fresh)el.innerHTML='<div class="pvpx_bar"><span class="pvpx_fill"></span></div>'+
    '<div class="pvpx_line"><span class="pvpx_txt"></span><span class="pvpx_hint"></span></div>';
  return el;}
function pvProxyStages(st){const on=!!(st&&st.running);
  PVPX.watch=PVPX.watch.filter(id=>{
    const stage=$(id);if(!stage)return false;
    if(!on){pvProgDrop(stage,'proxy');return false;}
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
  // Слой перехода видеовставки живёт своим <video>: у него src ПЕРЕСТАВЛЯЕТСЯ (исходник
  // ProRes кадра не давал вовсе) — эта дверь есть только у превью вставок.
  if(typeof ipvTransRefresh==='function')ipvTransRefresh();
  // Переезд на прокси живому src не присваиваем: смена посреди игры сбрасывает элемент в
  // readyState 0 (чёрный кадр) и сдвигает время (баг). Стоящий плеер переезжает
  // дублёром сразу, играющий — на ближайшем стыке (sparePrime подтянет свежий src сам).
  for(const P of [PV,IPV,CPV])if(P&&P.vids&&P.vids.length&&!vtPlaying(P))await spareHandover(P);}
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
  // Камеры клипа — и плееру шага 1 (редактор ED): дорожка обработанного голоса и живой
  // хост берут файл камеры 1 через vtCam1(P), а у ED своего /api/aicut_preview нет.
  ED.cams=cams;
  edPause();   // открылся другой клип: прежнее воспроизведение редактора стоп — его <video> сейчас пересоздаётся
  PV.xml=xml;PV.segs=(d.segs||[]).map(s=>({...s,ci:0}));PV.audio=d.audio&&d.audio.length?d.audio:d.segs;PV.words=d.words;PV.aidx=0;PV.vidx=-1;
  PV.dur=d.dur||(PV.audio.length?PV.audio[PV.audio.length-1].te:0);
  [...stage.querySelectorAll('video')].forEach(v=>mediaFree(v));
  PV.vids=cams.map((c,ix)=>{const v=document.createElement('video');
    v.src=pvSrc(c.path);v.preload='auto';v.muted=(ix!==0);v.playsInline=true;
    v.volume=MEDIA_VOL;v.style.zIndex=(ix===0)?'2':'1';stage.insertBefore(v,$('pvsub'));voiceWiring(v);return v;});
  PV.bufs=[];bufMake(PV,stage,$('pvsub'),0,0);
  PV.delta=camDeltas(PV);camBufs(PV,stage,$('pvsub'));   // тут камера одна, но контракт общий
  const camEl=$('edcam');   // «камера — склеек — длина»: строка переехала в редактор (блок «Монтаж» убран)
  if(camEl)camEl.textContent=cams.map((c,ix)=>(ix+1)+': '+(c.name||t('кам'))+(ix===0?t(' (звук)'):'')).join('  ·  ')+'  —  '+d.segs.length+t(' склеек · ')+Math.round(PV.dur)+t('с');
  // Панель «Голос» здесь НЕ рисуется: её зовёт openEditClip ПОСЛЕ edOpen (порядок см. там).
  if(px&&px.building)pvProxyWatch('pvstage');
  pvVideoTo(0);
}
function pvSegAt(list,tm){for(let i=0;i<list.length;i++){if(tm<list[i].te-1e-3)return i;}return Math.max(0,list.length-1);}
// Слово под плейхедом: строка субтитра кадра. Ищется по МОНТАЖНОМУ времени (PV.words
// сняты с монтажа) — пересчёт из исходного времени редактора делает edWords (70-editor.js).
function pvWordAt(tm){for(const w of (PV.words||[])){if(tm>=w.s&&tm<w.e)return w.w;}return '';}
// Единственный плеер шага 1 — редактор: выставить его <video> на исходное время камеры 1
// и подвести звук. Дверей две, обе ведут сюда: edSeek (пауза, клик, хоткеи) и edTick (игра).
function pvVideoTo(src){const v=PV.vids&&PV.vids[0];if(!v)return;
  if(PV.audio&&PV.audio.length)PV.aidx=pvSegAt(PV.audio,Math.max(0,src));   // кусок EDL под этим местом
  PV.vidx=-1;   // перемотка: запрет «назад» в camApply снят
  try{v.currentTime=src;}catch(e){}
  spareIdle(PV);camIdle(PV);camApply(PV,src,false);}   // разбег не готовим: на паузе\протяжке это лишний seek на каждый кадр
// --- дублёр камеры 1: общая машина всех плееров ------------------------------------
// Машина дублёра — ОБЩАЯ на все плееры (объект кадра PV, редактор ED, вставки IPV, раскладка
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
  if(!P||vtPlaying(P)||P.scrubbing||!(P.vids||[]).length)return;
  const b=spareLead(P);if(!b||!(P.cams&&P.cams[b.slot]&&P.cams[b.slot].path))return;
  const live=P.vids[b.slot];
  const want=pvSrc(P.cams[b.slot].path);
  if(!want||live.src.split(location.origin).pop()===want)return;   // живой уже на свежем источнике
  if(b.el.src.split(location.origin).pop()!==want){
    b.el.src=want;b.at=null;b.rolling=false;   // смена src сбросила позицию — взводим заново
    await vLoaded(b.el);
    if(vtPlaying(P)||P.scrubbing||!P.vids[b.slot])return;   // пока грузили, плеер тронули
  }
  try{b.el.currentTime=live.currentTime;}catch(e){return;}   // подводим к кадру, что на экране
  await vSeeked(b.el);
  if(vtPlaying(P)||P.scrubbing||!P.vids[b.slot]||b.el.seeking||b.el.readyState<2)return;
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
  P.vids.forEach((v,i)=>{v.style.opacity='1';v.style.zIndex=(i===ci)?'2':'1';
    v.muted=(i!==ac)||!!P.voiceMute||!!P.liveMute;
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
  if(!vtPlaying(P)||v.paused||ad>CAM_HARD){v.playbackRate=1;
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
  // pvVideoTo, где P.vidx сбрасывается в -1 и запрет снимается.
  if(vtPlaying(P)&&P.vidx>=0&&vi<P.vidx){vi=P.vidx;if(P.stats)P.stats.back++;}
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
// Своих часов воспроизведения у плеера шага 1 больше нет: играет ЕДИНСТВЕННЫЙ плеер —
// редактор, и его кадр (edTick, 70-editor.js) ведёт и картинку, и стыки pvStep, и звук
// vtTick. Монтажные pvPlay/pvPause/pvTick/pvScrub вместе с блоком «Монтаж» удалены:
// два плеера на одном <video> спорили за currentTime, и клик по таймлайну откатывался
// назад началом следующего куска монтажа.

// ===== Обработанный голос клипа: одна дорожка на ВСЕ превью =====
// Включён ИИ-шумодав (или плагин цепочки) — панель «Голос» считает ОБРАБОТАННЫЙ голос
// ВСЕГО клипа (серверный роут /api/voicefx_bake, кеш по содержимому настроек и
// исходника), и превью играет его вместо звука камеры. Трека два не бывает: тот же
// `<стем>.voice.wav` уезжает в AE, DRP, Premiere XML и черновой рендер — «один механизм»
// и есть этот файл.
//
// Код дорожки — ОБЩИЙ: функции `vt*` принимают плеер первым аргументом, а состояние
// живёт на самом плеере (P.vt). Им пользуется единственный плеер шага 1 (редактор ED) и
// превью шага 3 (IPV), а копии этого блока в 85-inserts-view.js нет и быть не должно:
// разъехавшиеся копии — это ровно то, из-за чего звук одного превью перестаёт совпадать
// со звуком другого.
//
// ВИДЕО — ХОЗЯИН, ЗВУК — ПОДЧИНЁННЫЙ. Этот блок НЕ трогает <video> вообще: ни play,
// ни pause, ни currentTime. Раньше было наоборот — окно звука в 20 с запрашивалось
// заново у края окна, на каждый такой запрос сервер считал шумодав заново, и видео
// вставало и прыгало («играет, потом стоит, звук идёт дальше»). Теперь звук
// подводится к позиции видео (vtSrcAt — время монтажа) и играет/стоит вместе с ним:
// один seek <audio> на расхождение больше допуска, и ничего больше.
//
// Пока трек считается, играет ИСХОДНЫЙ звук камеры, а в общей форме прогресса видно
// «голос обрабатывается, k %» (проценты — из хода самого шумодава: RoFormer печатает
// `N/M`). Готов — подмена источника <audio> без остановки видео.
//
// ЗВУК ДРУГОЙ КАМЕРЫ дорожка не подменяет: обработан голос камеры 1, и когда слушают
// камеру 2 (раскладка камер, cpvAudio), звучит она сама. Так же это решал голосовой
// прокси: он подменял звук ТОЛЬКО у камеры 1, остальные камеры шли обычным прокси со
// своим звуком. Гейт — по активной камере ЗВУКА (P.audioCi), а не по номеру элемента.
const VT_DRIFT=0.15;  // расхождение звука с видео, с: больше — подводим <audio>
const VT_QUIET=400;   // затишье после правки ручки, мс: ползунок сыплется на каждый пиксель
const VT_POLL=1000;   // опрос хода запекания, мс: у RoFormer шаг — кусок клипа
// Файл камеры 1 открытого клипа: источник и для обработанного голоса, и для живого
// звука в окне плагина. Приезжает из /api/aicut_preview (P.cams) — второй копии
// «где взять камеру клипа» нет.
function vtCam1(P){return (P&&P.cams&&P.cams[0]&&P.cams[0].path)||'';}
// Состояние дорожки — на плеере: у шага 1 и шага 3 свои элементы, свои запросы и своя
// очередь, а код один. Поля: `timer` — затишье после правки ручки (pvVoiceTune),
// `poll` — опрос хода запекания. Таймеры РАЗНЫЕ нарочно: общий поле `timer` затирало бы
// то опрос хода, то отложенный пересчёт, и смена ручки терялась бы в середине счёта.
// `vtq` — «стою в очереди», `vtend` — строку хода снял законный конец.
function vtOf(P){
  // `dn` — настройки шумодава, под которые в <audio> стоит дорожка; `wantDn` — под
  // которые её сейчас просим. Плагины в них не входят нарочно: дорожка шумодава от
  // цепочки не зависит, и правка плагина её не пересчитывает и не переподключает.
  if(!P.vt)P.vt={on:false,el:null,path:'',seq:0,timer:0,poll:0,want:'',note:'',
    vtq:false,vtend:false,live:null,dn:'',wantDn:''};
  return P.vt;}
// Исходное время камеры 1 под бегунком: кусок монтажа (P.audio[P.aidx]) плюс
// смещение ВНУТРИ него. Именно так, а не «разница с началом клипа»: на стыке
// начинается ДРУГОЙ кусок, и своё исходное время считается от его начала. Одна
// формула на всё, что просит звук с сервера: обработанный голос и окно плагина.
//
// Плеер шага 1 (редактор) — исключение, и только он: его <video> играет ИСХОДНИК, и
// исходное время камеры 1 (ED.cs) уже лежит на самом плеере. Пересчитывать его из
// монтажного таймлайна значило бы завести вторую копию правила «где плейхед».
function vtSrcAt(P,tm){
  if(typeof ED!=='undefined'&&P===ED)return Math.max(0,+P.cs||0);
  const a=(P.audio&&P.audio[P.aidx])||null;if(!a)return null;
  return a.src+Math.max(0,Math.min(tm-a.ts,a.te-a.ts));}
// Какая камера звучит в плеере: у раскладки камер это P.audioCi, у остальных камера 1.
function vtAudioCam(P){return (P&&P.audioCi)||0;}
// Идёт ли воспроизведение ЭТОГО плеера. Поле своё у каждого (IPV.playing, ED.play), и
// второй копии «кто играет» из общей дорожки не заводится: у шага 1 играет редактор.
function vtPlaying(P){
  if(typeof ED!=='undefined'&&P===ED)return !!P.play;
  return !!P.playing;}
// Время монтажа плеера. У шага 3 своя дверь (ipvNow): в рендере без AE время —
// НОМЕР КАДРА, и общая формула «от currentTime ведущей» там соврала бы. У редактора
// время тоже своё (исходник, ED.cs) — им и меряем, а не currentTime <video>.
function vtNow(P){
  if(typeof IPV!=='undefined'&&P===IPV&&typeof ipvNow==='function')return ipvNow();
  if(typeof ED!=='undefined'&&P===ED)return Math.max(0,+P.cs||0);
  const a=(P.audio&&P.audio[P.aidx])||null;
  return (a&&P.vids&&P.vids.length)?a.ts+(P.vids[0].currentTime-a.src):0;}
// <audio> обработанного голоса: свой элемент, а не <video> — видео занято картинкой и
// своими стыками, а звуку нужен ровно запечённый трек. Через общий граф (voiceWiring):
// громкость голоса стиля и цензура действуют на него так же, как на звук камеры, иначе
// «стало» врало бы по уровню.
function vtEl(P){
  const st=vtOf(P);
  if(st.el)return st.el;
  const el=document.createElement('audio');el.preload='auto';
  voiceWiring(el);st.el=el;return el;}
// Пока играет обработанный голос, звук <video> глушим — иначе слышно два голоса
// разом. Флагом на плеере, а не одной установкой muted: <video> пересоздаётся и
// меняется местами с дублёром на стыке, и установка «один раз» до стыка не дожила бы
// (см. camVisual: он и есть одно место, где решается, кто звучит).
//
// Глушим, ПОКА ДОРОЖКА ПОДКЛЮЧЕНА, а не только пока обработанный звук реально играет.
// Было «live && el.readyState>=2», и на перемотке <audio> падал в readyState<2: гейт
// открывался, и в этот момент было слышно СЫРОЙ голос камеры — ровно то, на что
// жаловались («при перемотке ползунком звук пролагивает и включается старый»).
// Короткая тишина на догоне лучше старого голоса: камера молчит, пока трек наш.
//
// Дорожка камеры 1 звучит только тогда, когда звук идёт С камеры 1: слушают камеру 2 —
// её никто не глушит, а голос камеры 1 молчит (vtTick).
function vtGate(P,on){
  const ac=vtAudioCam(P),live=!!on&&ac===0&&!!vtOf(P).on,M=vtMuteHost(P);
  M.voiceMute=live;
  const v=M.vids&&M.vids[ac];if(v)v.muted=live;}
// Надпись про голос — в панель «Голос» плеера (она есть у превью нарезки). Плееру без
// панели (шаг 3) писать некуда: там голос не настраивают, а только слушают.
function vtNote(P,text){
  if(!P.voicePanel||typeof voiceFxStatus!=='function')return;
  voiceFxStatus($(P.voicePanel),text);}
// Имя клипа для строки прогресса: то же, что в заголовке превью (openEditClip).
function vtName(P){
  const c=(P.xml&&typeof clipByXml==='function')?clipByXml(P.xml):null;
  return (c&&typeof clipLabel==='function')?clipLabel(c):t('голос клипа');}
// Настройки голоса спикера клипа — из профиля (SPEAKERS). Ими же собирается проект,
// поэтому превью шага 3 — у него панели нет — просит трек ровно под них.
function vtProfileFx(P){
  const c=(P.xml&&typeof clipByXml==='function')?clipByXml(P.xml):null;
  const key=(c&&c.job&&c.job.speaker)||'';
  const prof=(key&&typeof SPEAKERS!=='undefined'&&SPEAKERS[key])||null;
  return (prof&&prof.voice_fx)||{};}
// Под какие настройки просим трек. Плеер со своей панелью «Голос» (шаг 1) спрашивает
// РУЧКИ на экране: до «Сохранить у спикера» источник правды для него — они. У остальных
// панели нет, и берётся профиль спикера, то есть то, по чему соберётся проект.
function vtFx(P){
  const host=(P.voicePanel&&typeof $==='function')?$(P.voicePanel):null;
  if(host&&host.dataset&&host.dataset.vfxmode==='panel'&&typeof voiceFxRead==='function')
    return voiceFxRead(host);
  return vtProfileFx(P);}
// Отцепить звук: трек больше не наш (другой клип, выключенная обработка).
function vtDetach(P){
  const st=vtOf(P);st.on=false;st.path='';st.dn='';
  const el=st.el;
  if(!el)return;
  el.pause();
  try{el.removeAttribute('src');el.load();}catch(e){}}   // load() сбрасывает прежний трек
// Выключили обработку (или открыли другой клип): звук обратно камере, трек — в никуда.
// Запечённый файл клипа при этом убирает СЕРВЕР (clear_final_voice): «выключил шумодав —
// звук исходный» обязано работать и для XML, DRP и чернового рендера, которые читают его.
function vtStop(P){
  const st=vtOf(P);st.seq++;
  clearTimeout(st.timer);st.timer=0;
  clearTimeout(st.poll);st.poll=0;
  st.vtq=false;st.vtend=true;st.want='';
  vtLivePause(P);
  // Живой хост плагинов живёт вместе с превью ЭТОГО плеера: другой клип или закрытие —
  // процесс гасится (по PID, на сервере), и после превью не остаётся ни одного.
  if(typeof voiceFxHostStop==='function')voiceFxHostStop();
  vtDetach(P);vtVoiceLine(P,'');vtNote(P,'');}
// Живое окно плагина: пока оно открыто, звук голоса идёт через него — вровень с
// картинкой (см. `vtLive*` ниже). Звук камеры 2 живёт как обычно: окно обрабатывает
// ТОЛЬКО голос камеры 1, и глушить чужую камеру нечем.
function vtLiveOn(P){
  return (typeof voiceFxLiveOn==='function')&&!!voiceFxLiveOn();}
// Глушение звука камеры плеера: решается В ОДНОМ месте и для дорожки, и для живого
// окна. Иначе два писателя (vtTick и vtLiveUpdate) затирали бы друг другу флаг, и
// звук камеры то замолкал бы поверх плагина, то звучал вместе с ним.
function vtSetMute(P,on){
  const M=vtMuteHost(P);
  M.voiceMute=!!on;
  const ac=vtAudioCam(P),v=M.vids&&M.vids[ac];
  if(v)v.muted=!!M.voiceMute||!!M.liveMute;
  return !!M.voiceMute;}
// Кадр плеера для ЖИВОГО окна: позиция — исходное время камеры 1 (та же формула,
// что у дорожки, vtSrcAt), пуск/пауза — состояние плеера, стык и перемотка —
// команда seek. Глушение своего голоса (дорожка vt и звук камеры 1) снимается и
// ставится ЗДЕСЬ: пока плагин звучит, свой голос молчит, иначе слышно два голоса.
// Окно закрылось — возвращаем звук дорожке (`voiceFxLiveOn` стал false).
const VT_LIVE_DRIFT=0.4;    // расхождение, после которого шлём перемотку, сек
const VT_LIVE_MS=250;       // как часто можно слать перемотку: ползунок сыплется
function vtLiveUpdate(P,tm){
  const st=vtOf(P),on=vtLiveOn(P),ac=vtAudioCam(P),M=vtMuteHost(P);
  if(!on){
    // Живого звука больше нет (окно закрыли): снимаем своё глушение и возвращаем
    // звук дорожке — иначе камера осталась бы немой до следующего кадра vtTick.
    if(st.live){st.live=null;}
    if(M.liveMute!==undefined){
      M.liveMute=undefined;M.voiceMute=false;
      const v=M.vids&&M.vids[ac];if(v)v.muted=false;
    }
    return false;}
  if(!st.live)st.live={sid:'',at:-1,sent:0,pending:0,rate:1};
  if(ac!==0){
    // Слушают камеру 2: хост обрабатывает голос камеры 1, и звучать ему нечем — камера 2
    // играет сама, и глушить её нельзя (это её звук, а не голос спикера).
    if(!st.live.paused)vtLiveCmd(P,'pause');
    if(st.el&&!st.el.paused)st.el.pause();
    M.liveMute=false;
    return false;}
  const at=vtSrcAt(P,tm);
  if(at==null)return false;
  const want=Math.max(0,at),live=vtPlaying(P)&&!P.scrubbing;
  // Звук камеры 1 глушим, только если плагин реально играет: на паузе слышно, как
  // ручка меняет звук ровно на этом месте, — это и есть «крутишь и слышишь».
  M.liveMute=live;
  M.voiceMute=false;
  const v=M.vids&&M.vids[vtAudioCam(P)];
  if(v)v.muted=!!M.liveMute;
  // Своя дорожка обработанного голоса молчит: звучит хост. Она остаётся ПОДКЛЮЧЁННОЙ
  // (`st.on`), а не отцепляется: хост гасят, когда плагины выключили, и тогда звук
  // должен вернуться ей без нового захода за дорожкой на сервер.
  if(st.el&&!st.el.paused){st.el.pause();}
  if(!live){vtLiveCmd(P,'pause');st.live.at=want;return true;}
  // Перемотку шлём не на каждый кадр: 60 команд в секунду в трубу — это и мусор в
  // логе, и дёрганый звук. Отдельно ловим СТЫК монтажа: там позиция прыгает сама,
  // а с ней и кусок исходника (иначе звук уехал бы на вырезанное место).
  // Сравниваем с тем, где звук окна ДОЛЖЕН быть сейчас (последняя команда + прошедшее
  // время), а не с местом последней команды: иначе при обычном проигрывании через
  // VT_LIVE_DRIFT секунд уходила бы перемотка, и так каждые 0,4 с — щелчок и
  // обрубленный хвост ревербератора (сброс плагинов на каждой перемотке).
  const far=st.live.at<0||Math.abs(want-vtLiveExpect(P,st))>vtLiveRate(P,st)*VT_LIVE_DRIFT;
  if(far)vtLiveCmd(P,'seek',want);
  else vtLiveCmd(P,'play',want);
  return true;}
// Где звук окна сейчас по нашим часам: место последней команды play/seek плюс время,
// прошедшее с неё (на паузе — стоит). Окно играет само, превью только задаёт место.
function vtLiveExpect(P,st){
  const l=st.live;if(!l||l.at<0)return -1;
  if(l.paused||!l.t0)return l.at;
  return l.at+(Date.now()-l.t0)/1000*vtLiveRate(P,st);}
// Множитель разбега: у плеера своя скорость (playbackRate), и допуск расхождения
// считается от неё — на ускоренном превью звук обязан бежать быстрее.
function vtLiveRate(P,st){
  const v=P.vids&&P.vids[0];
  return (v&&+v.playbackRate)||1;}
// Команда живому окну. Тихо: окно могло закрыться между кадром и командой — тогда
// живой звук просто кончился, и об этом скажет опрос панели (`voiceFxHostPoll`).
function vtLiveCmd(P,cmd,at){
  const st=vtOf(P),l=st.live;if(!l)return false;
  const sid=vtLiveSid(P);if(!sid)return false;
  if(cmd==='seek'||cmd==='play'){
    // Перемотка — не чаще порога: ползунок и подводка звука сыплются на каждый кадр, а
    // в трубу окна команда на кадр — это мусор. Стык монтажа и прыжок бегунка
    // приходят не чаще, чем этот порог, и потому не теряются. «Играть» и «пауза» —
    // смена состояния, её терять нельзя: она приходит один раз на нажатие.
    const now=Date.now();
    if(cmd==='play'&&!l.paused&&Math.abs(at-vtLiveExpect(P,st))<vtLiveRate(P,st)*VT_LIVE_DRIFT)
      return false;                       // окно уже играет это место — команда не нужна
    if(cmd==='seek')
      l.sent=now;                         // порог считается только по перемоткам
    l.at=at;l.t0=now;}
  if(cmd==='pause'&&l.paused)return false;   // пауза — одно и то же состояние
  l.paused=(cmd==='pause');
  fetch('/api/voicefx_live',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({sid:sid,cmd:cmd,at:(at==null?0:Math.max(0,at))})})
    .catch(e=>uiLog('voicefx_live: '+e));
  return true;}
// Номер сессии живого окна — со страницы (его положила панель, открывшая окно).
function vtLiveSid(P){
  const l=(typeof VOICEFXLIVE!=='undefined')&&VOICEFXLIVE;
  return (l&&l.running&&l.sid)||'';}
// Играть/стоять — командой живому окну: позиция берётся от текущего кадра.
function vtLivePlay(P){if(!vtLiveOn(P))return;vtLiveUpdate(P,vtNow(P));}
function vtLivePause(P){if(!vtLiveOn(P))return;vtLiveCmd(P,'pause');
  const M=vtMuteHost(P);M.liveMute=false;
  const v=M.vids&&M.vids[vtAudioCam(P)];if(v)v.muted=!!M.voiceMute;}
// Пауза: звук тоже стоит (иначе голос доигрывал бы поверх паузы). Звук камеры на паузе
// возвращаем: при следующем пуске его снова заглушит vtTick.
function vtPause(P){const st=vtOf(P);if(st.el)st.el.pause();vtLivePause(P);vtGate(P,false);}
// Стойка плеера: строка хода голоса рисуется там же, где прогресс сборки прокси —
// блоком поверх кадра. У шага 1 стойка одна: её и занимает редактор, у шага 3 —
// ipvstage. `typeof PV` — не перестраховка: дорожку зовут и стенды, и страница рендера,
// где плеера нарезки нет вовсе.
// Плеер шага 1 — тот, у кого панель «Голос» и живой хост плагинов: теперь это редактор
// (ED), а монтажный PV остался общим объектом кадра, которым редактор и играет. Имя
// vtIsPv оставлено прежним: под ним дорожка голоса живёт в стендах шага 1.
function vtIsPv(P){return (typeof PV!=='undefined'&&P===PV)||(typeof ED!=='undefined'&&P===ED);}
function vtIsEd(P){return typeof ED!=='undefined'&&P===ED;}   // редактор — единственный плеер шага 1
// Кому ставится флаг «звук камеры молчит». Флаг читает camVisual — тот, чей `vids` красит
// кадр, а у шага 1 кадр и ракурс ведёт общий объект PV (редактор ED ведёт только часы и
// дорожку голоса). Без этого camVisual(PV) снова включил бы звук камеры поверх
// обработанного голоса: слышно два голоса разом.
function vtMuteHost(P){return (vtIsEd(P)&&typeof PV!=='undefined'&&PV)?PV:P;}
function vtStage(P){
  if(typeof $!=='function')return null;
  if(vtIsPv(P)||vtIsEd(P))return $('pvstage');
  if(typeof CPV!=='undefined'&&P===CPV)return $('cpvstage');
  return $('ipvstage');}
// Голос клипа для превью: готов — играем, нет — просим посчитать и показываем ход.
// Настройки едут телом запроса (vtFx): у монтажа это ручки панели, у шага 3 — профиль.
// Кеш сервера считает трек по СОДЕРЖИМОМУ настроек, поэтому ответ несёт путь с новым
// ключом: смена ручки = новый URL = браузер берёт новый звук сразу, без переоткрытия.
async function vtPrep(P){
  const st=vtOf(P),xml=P.xml,src=vtCam1(P);
  const fx=vtFx(P);
  const seq=++st.seq;
  st.want=JSON.stringify(fx||{});          // под какие настройки просим трек
  const isFinal=!vtIsPv(P);
  // Плагины — вживую: живой хост подгоняется под цепочку панели (поднимается, если
  // нужен, перестраивается на лету, гасится, если плагинов не осталось). Шумодав он
  // НЕ пересчитывает: дорожка шумодава лежит в кеше отдельно от плагинов.
  if(!isFinal&&(vtIsPv(P)||vtIsEd(P))&&xml&&src&&typeof voiceFxHostSync==='function')voiceFxHostSync(fx,{player:P});
  // Настройки шумодава те же, и дорожка уже играет: правка касалась только плагинов
  // (или соседней ручки) — просить сервер и переподключать звук незачем. Так «добавил
  // плагин» не мигает «прошу голос клипа…» и не рвёт звук.
  const dn=JSON.stringify((fx&&fx.denoise)||{});
  if(xml&&src&&st.on&&st.path&&st.dn===dn&&!isFinal)return;
  st.wantDn=dn;
  if(xml&&src)vtVoiceLine(P,t('прошу голос клипа…'));
  vtDetach(P);vtGate(P,false);             // пока трек не готов — звук камеры, не тишина
  vtNote(P,'');
  if(!xml||!src){vtVoiceLine(P);return;}
  let d;
  try{
    const reqBody={xml:xml,src:src,fx:fx||{}};
    if(isFinal)reqBody.final=true;
    d=await (await fetch('/api/voicefx_bake',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(reqBody)})).json();}
  catch(e){if(seq===st.seq){vtNote(P,t('голос клипа не посчитан — сервер не ответил'));
      vtVoiceLine(P,t('голос клипа не посчитан — сервер не ответил'));uiLog('voicefx_bake: '+e);}return;}
  if(seq!==st.seq)return;                  // панель перерисовали — ответ не наш
  // Сбой запоминаем: причина (нет окружения RoFormer, сервер занят) могла уйти, а
  // сам vtPrep зовут только правки ручек — «Играть» обязан попробовать заново (edPlay).
  if(d.error){st.failed=true;vtNote(P,'⚠ '+errText(d));vtVoiceLine(P,'⚠ '+errText(d));
    uiLog('voicefx_bake: '+JSON.stringify(d).slice(0,200));return;}
  st.failed=false;
  // Запекание — на каждый клип своё, но СЧИТАЕТСЯ ПО ОДНОМУ: в работе может быть
  // голос другого клипа (открыли соседний, пока считался первый). Ждём свою очередь,
  // а не подхватываем чужой трек: ход ЭТОГО клипа отдаёт /api/voicefx_bake_status.
  vtVoiceShow(P,d,seq);
  if(d.ready||(d.done&&d.path)){
    if(isFinal&&typeof voiceFxHostStop==='function')voiceFxHostStop();
    vtVoiceTake(P,d.path,seq);return;}
  if(d.running||d.queued){
    if(isFinal&&xml&&src&&typeof voiceFxHostSync==='function')voiceFxHostSync(fx,{player:P});
    vtVoiceWatch(P,seq);return;}
  // Ни готового, ни счёта: обработка выключена. Строку «прошу голос клипа…» снимаем
  // сами — ответ этой двери хода не несёт, и она осталась бы висеть на кадре.
  vtVoiceLine(P,'');
  vtNote(P,t('обработка выключена — звук камеры как есть'));
  if(isFinal&&typeof voiceFxHostStop==='function')voiceFxHostStop();}
// Ход голоса — ОДНОЙ строкой там, где идут проценты сборки прокси: отдельного окна у
// голоса больше нет (`progOpen` для него не зовётся вовсе). Идут прокси и голос разом —
// две строки: своей формой голос закрывал бы ровно то, что настраивают в превью.
function vtVoiceShow(P,d,seq){
  const st=vtOf(P);
  if(seq!=null&&seq!==st.seq)return;
  const pct=Math.max(0,Math.min(100,Math.round(+d.pct||0)));
  // Проценты есть только тогда, когда их даёт сам шумодав (`N/M` печатает RoFormer).
  // У deep-filter хода работы нет вовсе — показывать «0 %» значило бы врать: полоса
  // идёт «работаю», а в строке написано «голос обрабатывается…» без числа.
  const known=(+d.n>0);
  // «Готово» ход отдаёт ДВУМЯ дверями, и обе надо понимать: заказ (`/api/voicefx_bake`,
  // `ready`) и ход (`/api/voicefx_bake_status`, у него `done` и путь). Признак один —
  // посчитанный трек с путём: по нему превью и играет, и строку снимает.
  if(d.ready||(d.done&&d.path)){st.vtq=false;st.vtend=true;
    vtVoiceLine(P,'');vtNote(P,t('обработанный голос клипа готов'));return;}
  if(d.error){st.vtq=false;st.vtend=true;st.failed=true;   // «Играть» попробует снова (edPlay)
    vtVoiceLine(P,'⚠ '+d.error);vtNote(P,'⚠ '+d.error);return;}
  if(d.queued&&!d.running){st.vtq=true;st.vtend=false;
    vtVoiceLine(P,t('голос клипа: в очереди'));
    vtNote(P,t('голос клипа: в очереди'));return;}
  if(!d.running&&!d.queued)return;
  st.vtq=false;st.vtend=false;
  let note;
  const isPv=vtIsPv(P)||vtIsEd(P);
  const isRaw = (typeof VOICEFXLIVE!=='undefined' && VOICEFXLIVE && VOICEFXLIVE.track_input==='raw') ||
                (isPv && typeof voiceFxHostOn==='function' && voiceFxHostOn() &&
                 typeof VOICEFXLIVE!=='undefined' && VOICEFXLIVE && VOICEFXLIVE.track_ready);
  if(isPv && isRaw){
    note=known?t('шумодав считается — плагины уже слышно, без очистки, {p} %',{p:pct})
              :t('шумодав считается — плагины уже слышно, без очистки…');
  }else{
    note=known?t('голос обрабатывается, {p} %',{p:pct}):t('голос обрабатывается…');
  }
  vtVoiceLine(P,note,known?pct:null,!!d.running);
  vtNote(P,note);}
// Строка хода голоса на кадре: текст, полоса и «Стоп» (у счёта — свой клип, чужой не
// трогаем). Строку снимает ЗАКОННЫЙ конец (`vtend`), поэтому опрос картинку не затирает.
// Ждём через опрос хода ЭТОГО клипа: у очереди процента нет вовсе — покажем «в очереди».
async function vtVoiceWatch(P,seq){
  const st=vtOf(P);
  if(seq!=null&&seq!==st.seq)return;
  if(st.vtend)return;                        // конец законный (готово / ошибка / «Стоп»)
  clearTimeout(st.poll);
  st.poll=setTimeout(()=>{st.poll=0;vtVoicePoll(P,seq);},VT_POLL);}
async function vtVoicePoll(P,seq){
  const st=vtOf(P);
  if(seq!==st.seq)return;
  let d;
  try{const u='/api/voicefx_bake_status?xml='+encodeURIComponent(P.xml||'');
    d=await (await fetch(u)).json();}
  catch(e){vtVoiceWatch(P,seq);return;}      // сервер не ответил — попробуем ещё
  if(seq!==st.seq)return;
  const was=st.vtend;
  vtVoiceShow(P,d,seq);
  if(d.ready||(d.done&&d.path)){vtVoiceTake(P,d.path,seq);return;}
  if(d.error||was)return;
  if(d.running||d.queued)vtVoiceWatch(P,seq);}
// «Стоп» у строки хода: сервер снимает клип из очереди или гасит текущий счёт по PID
// дочернего процесса. Своё состояние чистим сразу — кнопка не должна ждать ответа.
async function vtVoiceStop(P){
  const st=vtOf(P);st.seq++;st.vtq=false;st.vtend=true;st.want='';
  // Гасим ОБА таймера: и опрос хода, и отложенный пересчёт по правке ручки — иначе
  // «Стоп» тут же заказывал бы счёт заново.
  clearTimeout(st.poll);st.poll=0;
  clearTimeout(st.timer);st.timer=0;
  vtVoiceLine(P,'');vtNote(P,t('обработка остановлена'));
  try{await fetch('/api/voicefx_bake_cancel',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({xml:P.xml||''})});}
  catch(e){uiLog('voicefx_bake_cancel: '+e);}}
// Готовый трек: прежде чем играть — сверить, под ТЕ ЛИ настройки он посчитан. Пока шёл
// счёт, ручки могли поменять (движок, доля, плагины), и играть трек под прежние значит
// врать про то, что слышно — ровно исходная жалоба «сменил настройки, а разницы нет».
// Настройки те же — играем; другие — просим посчитать заново под текущие.
function vtVoiceTake(P,path,seq){
  const st=vtOf(P);
  if(st.want&&st.want!==JSON.stringify(vtFx(P)||{})){vtPrep(P);return;}
  if(path){
    if(!vtIsPv(P)&&typeof voiceFxHostStop==='function')voiceFxHostStop();
    vtVoiceUse(P,path,seq);
  }else vtVoiceLine(P,'');}
// Поставить готовый трек в <audio>. Видео не трогаем НИЧЕМ: подмена источника звука и
// подводка к текущей позиции — только у звукового элемента. `seq` сверяется ещё раз
// после загрузки метаданных: за это время мог приехать трек другого клипа.
function vtVoiceUse(P,path,seq){vtUse(P,path,seq);}
// Строка хода голоса в стойке плеера — отдельной строкой рядом с прогрессом прокси
// (обе — строки общего контейнера `.pvprog` поверх кадра, см. `pvProgRow`). Общей формы
// прогресса у голоса больше нет: окно, которое надо было бы свернуть или закрыть,
// закрывало бы ровно то, что настраивают, — а ход виден и без него.
//
// Идти разом могут оба, и тогда видно две строки друг над другом. Клики контейнер не
// перехватывает (драг вставок и интро под ним), кнопке «Стоп» они включены точечно в CSS.
// Пустой текст снимает строку, а с последней строкой уходит и контейнер; ссылка в
// `st.vtbox` остаётся у снятой строки (пустой, `display:none`) до следующего показа.
function vtVoiceLine(P,text,pct,running){
  const st=vtOf(P),stage=vtStage(P);
  if(!stage)return;
  if(!text){
    if(st.vtbox){st.vtbox.style.display='none';st.vtbox.innerHTML='';}
    pvProgDrop(stage,'voice');return;}
  const box=pvProgRow(stage,'voice','pvpxv');
  box.id=vtIsPv(P)?'pvvoiceline':'ipvvoiceline';st.vtbox=box;
  const known=(pct!=null&&pct>=0);
  const width=known?Math.max(0,Math.min(100,pct)):0;
  box.style.display='';
  box.innerHTML='<div class="pvpx_bar"><span class="pvpx_fill" style="width:'+width+'%"></span></div>'
    +'<div class="pvpx_line"><span class="pvpx_txt">'+esc(text)+'</span>'
    +(running?'<button class="sm pvpx_stop" data-vtstop="1" type="button">'+esc(t('Стоп'))+'</button>':'')
    +'</div>';
  const b=box.querySelector('[data-vtstop]');
  if(b)b.onclick=()=>vtVoiceStop(P);}
// Поставить готовый трек в <audio>. Видео не трогаем НИЧЕМ: подмена источника звука и
// подводка к текущей позиции — только у звукового элемента. `seq` сверяется ещё раз
// после загрузки метаданных: за это время мог приехать трек другого клипа.
function vtUse(P,path,seq){
  const st=vtOf(P);
  if(seq!=null&&seq!==st.seq)return;
  if(!path)return;
  const el=vtEl(P);
  st.on=true;st.dn=st.wantDn;
  // Надпись панели — под то, что слышно: иначе после счёта (или мгновенно из кеша)
  // висело «голос обрабатывается…», хотя обработанный голос уже играл.
  vtNote(P,t('обработанный голос клипа готов'));
  if(st.path===path){vtTick(P,vtNow(P));return;}
  st.path=path;
  el.volume=MEDIA_VOL;
  el.src='/api/media?path='+encodeURIComponent(path);
  const start=()=>{if(seq==null||seq===st.seq)vtTick(P,vtNow(P));};
  if(el.readyState>=1)start();else el.addEventListener('loadedmetadata',start,{once:true});}
// Кадр плеера: подвести звук к позиции ВИДЕО и играть вместе с ним. Позиция — исходное
// время камеры 1 под бегунком (vtSrcAt), оно же время запечённого трека: файл посчитан
// по звуку камеры 1 от её нуля, поэтому и смещения на стыках у них общие.
// Гейт открыт, ПОКА ТРЕК НАШ (`vtGate` смотрит на st.on): на перемотке и догрузке
// <audio> камера молчит — короткая тишина лучше старого голоса.
function vtTick(P,tm){
  const st=vtOf(P);
  // Окно плагина открыто: голос играет ОН (трек через цепочку в реальном времени),
  // и превью только задаёт ему позицию. Ветка одна на всё: своя дорожка в это время
  // молчит — иначе слышно два голоса разом.
  if(vtLiveOn(P)){vtLiveUpdate(P,tm);return;}
  // Хост ушёл (плагины выключили, превью закрыли): снять его глушение камеры, и звук
  // вернётся дорожке — иначе камера осталась бы немой до следующей правки.
  if(vtMuteHost(P).liveMute!==undefined||st.live)vtLiveUpdate(P,tm);
  if(!st.on||!st.el||!st.path){vtGate(P,false);return;}
  const el=st.el;
  if(vtAudioCam(P)!==0){                 // слушают другую камеру — она звучит сама
    if(!el.paused)el.pause();
    vtGate(P,false);return;}
  // Вырезанное место (только у единственного плеера шага 1 — редактора): картинка там
  // прыгает через вырез (edTick -> edJump), и звук обязан прыгнуть вместе с ней. Иначе
  // обработанный голос доигрывал бы удалённый кусок поверх прыжка картинки. Гейт при этом
  // НЕ открываем: звук камеры на вырезанном тоже должен молчать, а не зазвучать сырым.
  if(typeof edRaw==='function'&&!edRaw(P)&&typeof edInCut==='function'&&edInCut(P)){
    if(!el.paused)el.pause();
    vtGate(P,false);return;}
  vtGate(P,true);
  const at=vtSrcAt(P,tm);
  if(at==null||el.readyState<1)return;   // трек ещё не открылся — молчим, но не камеру
  const want=Math.max(0,at);
  if(!el.seeking&&Math.abs(el.currentTime-want)>VT_DRIFT){try{el.currentTime=want;}catch(e){}}
  const live=vtPlaying(P)&&!P.scrubbing;
  if(live){if(el.paused)el.play().catch(()=>{});}
  else if(!el.paused)el.pause();}
// Пересчёт после правки ручки панели: ~400 мс затишья. Ползунок сыплется на каждый
// пиксель, а голос клипа — это счёт шумодава на весь клип: запрос на каждое движение
// ставил бы их в очередь. Кеш по настройкам делает повторный вызов дешёвым: те же
// ручки — тот же файл.
// Тут же и АВТОСОХРАНЕНИЕ профиля спикера (`pvVoiceAuto`): ручку отпустили — она уже
// у спикера, а кнопки «Сохранить у спикера» больше нет. Две задержки у одного
// события, и обе нужны: сохранение — это файл профиля (быстро), пересчёт голоса —
// минуты счёта (дорого), поэтому оно и уезжает отдельным вызовом.
function pvVoiceTune(){
  if(typeof voiceFxStatus==='function')voiceFxStatus($('pvvoice'),'');
  if(typeof vtNote==='function'&&typeof ED!=='undefined')vtNote(ED,'');
  const st=vtOf(ED);
  clearTimeout(st.timer);
  st.timer=setTimeout(()=>{st.timer=0;vtPrep(ED);},VT_QUIET);
  if(typeof pvVoiceAuto==='function')pvVoiceAuto();}
// «Сохранить у спикера» позвало: профиль записан, и по нему печётся `<стем>.voice.wav`.
// Сборку превью-прокси при этом НЕ заказываем: прокси всегда со звуком камеры и от
// настроек голоса не зависит. Раньше заказывали — и каждая правка ручки тянула за собой
// пересборку файла камеры, а на экране висело «сборка прокси 1/1 · 0 %».
async function pvVoiceBake(){
  await vtPrep(ED);
  if(!vtOf(ED).on)vtNote(ED,t('обработка выключена — голос клипа не печётся'));}
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
  // «Добавить слово слева»: у акцента строка начинается со своего `from`, у обычной — с первого
  // слова диапазона (idxs). Слово 0 — слева брать нечего, кнопка гаснет (та же проверка, что
  // в introGrowLeft: кнопка и функция обязаны гаснуть на одном и том же слове).
  const firstW=(r.from!=null&&r.from>=0)?r.from:(idxs.length?idxs[0]:null);
  const canLeft=(firstW!=null&&firstW>0);
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
  const textColor = (colVal==='yellow'?'var(--introhl,var(--subhl,var(--yel)))':(colVal==='accent'?'var(--subhl3,#af1f1f)':(colVal==='custom'&&r.fill&&typeof rgb2hex==='function'?rgb2hex(r.fill):'var(--tx)')));
  return sep+'<div class="row introrow" data-ig="'+(gi-1)+'" style="align-items:center;margin-top:6px;flex-wrap:nowrap;gap:6px">'
    +introPlus(cfg.rows,i,cfg.add)
    +'<input type="number" min="0" step="1" style="width:48px;min-height:var(--h-sm);height:var(--h-sm);padding:2px 4px;text-align:center;flex-shrink:0" aria-label="'+t('Слов в строке')+'" value="'+r.count+'" oninput="'+A+'['+i+'].count=parseInt(this.value)||0;'+S+'">'
    +colorSelect
    +customColorInput
    +animSelect
    +'<label class="chk" style="margin:0;padding:0 4px;flex-shrink:0" data-t="'+t('Акцентный шрифт: другой шрифт и регистр этой строки (accent_font стиля; пусто = выключено)')+'"><input type="checkbox" aria-label="'+t('Акцентный шрифт')+'" '+(r.accent?'checked':'')+' onchange="'+A+'['+i+'].accent=this.checked;this.blur();'+S+'"></label>'
    +'<label class="chk" style="margin:0;padding:0 4px;flex-shrink:0" data-t="'+t('Задний план: строка уходит на задний план (шрифт back_font и регистр back_case из стиля)')+'"><input type="checkbox" aria-label="'+t('Задний план')+'" '+(r.back?'checked':'')+' onchange="'+A+'['+i+'].back=this.checked;this.blur();'+S+'"></label>'
    +'<label class="chk" style="margin:0;padding:0 4px;flex-shrink:0" data-t="'+t('Большое слева: строка встаёт слева крупно, остальные строки группы — стопкой справа (высота — по стопке)')+'"><input type="checkbox" aria-label="'+t('Большое слева')+'" '+(r.big?'checked':'')+' onchange="'+A+'['+i+'].big=this.checked;this.blur();'+S+'"></label>'
    +fromBadge
    // «Стрелка влево +» слева от слов: забрать предыдущее слово в эту строку. У первой строки
    // интро кнопка не исчезает, а гаснет (disabled) — вёрстка строк не скачет.
    +'<button class="icon" style="flex-shrink:0" aria-label="'+t('Добавить слово слева')+'" data-t="'+t('Добавить слово слева: забрать предыдущее слово в эту строку')+'"'+(canLeft?'':' disabled')+' onclick="'+cfg.grow+'('+i+')">'+ico('arrow_left_plus')+'</button>'
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

