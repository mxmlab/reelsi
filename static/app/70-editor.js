// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Maxim Si
// редактор нарезки (таймлайн) и разметка шага 2
//
// Часть интерфейса Reelsi. Файлы static/app/ грузятся ПО ПОРЯДКУ ИМЁН обычными
// <script>-тегами (не модулями): один общий скоуп, как было в едином app.js.
// Порядок важен — объявления функций поднимаются в пределах своего файла.

// ================= editor (таймлайн: зум/пан, линейка, undo, возврат вырезанного) =================
// ЕДИНСТВЕННЫЙ плеер шага 1. Играет исходник камеры 1 и пропускает вырезанное, а
// обработанный голос (дорожка vt*) звучит в нём же: время звука — ED.cs (исходное время
// камеры 1 под плейхедом, оно же время запечённого трека). Блока «Монтаж» со своим
// плеером и ползунком больше нет: два плеера на одном <video> спорили за currentTime,
// и клик по таймлайну откатывался назад началом следующего куска монтажа.
let ED={xml:'',blocks:[],fps:60,cam:'',dur:0,peaks:[],pps:80,sel:-1,play:false,raw:false,raf:0,
  cs:0,drag:null,v0:0,v1:0,hist:[],cuts:[],br:[],brBand:0,
  // Поля плеера для дорожки голоса (60-preview.js:vt*): `cams` ставит openPreview —
  // дорожка и живой хост берут файл камеры 1 через vtCam1(P); `voicePanel` — id панели
  // «Голос» этого плеера, по ней vtFx берёт ЖИВЫЕ ручки на экране, а vtNote пишет статус.
  cams:null,voicePanel:'pvvoice',
  // Промах видео-дублёра на стыке: дорожку голоса держим закрытой, пока живое видео не
  // доедет seek'ом до места прыжка. `vtOpen` — идёт такое ожидание, `vtTimer` — его
  // страховка, `vtEl`/`vtOn` — на каком элементе и каким слушателем ждём (см. edJump).
  vtOpen:false,vtTimer:0,vtEl:null,vtOn:null};
const EDRULER=18;                                   // высота линейки, css px
// Мутационный выключатель к тесту `test_voice_spare.py`: с ним `edJump` снова зовёт
// `vtSpareTake` БЕЗУСЛОВНО — ровно то, что было до правки «голос следует за решением
// видео». Живёт в модуле, а не на плеере: плеер — боевое состояние, и лишнего поля в нём
// быть не должно. Выключен по умолчанию, в браузере никто его не ставит.
let EDMUTVOICE=0;
async function edOpen(){const xml=PV.xml||(curEdit>=0?CLIPS[curEdit].xml:'')||ED.xml;if(!xml)return;ED.xml=xml;
  const info=$('edtime');info.textContent=t('загрузка…');
  try{const d=await (await fetch('/api/editor_load',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({xml})})).json();
    if(d.error){toast(errText(d));return;}
    ED.blocks=(d.keep||[]).map(b=>({s0:b[0],s1:b[1]}));ED.fps=d.fps||60;ED.cam=d.cam;
    ED.orig=(d.keep||[]).map(b=>({s0:b[0],s1:b[1]}));   // раскладка ДО правки — для пересчёта таймингов вставок
    ED.sel=-1;ED.cs=ED.blocks.length?ED.blocks[0].s0:0;ED.hist=[];ED.cuts=[];
    const w=await (await fetch('/api/waveform?pps=80&path='+encodeURIComponent(ED.cam))).json();
    ED.peaks=w.peaks||[];ED.pps=w.pps||80;ED.dur=w.dur||(ED.blocks.length?ED.blocks[ED.blocks.length-1].s1:0);
    ED.v0=0;ED.v1=ED.dur||1;
    fetch('/api/omnicut_cuts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({xml})})
      .then(r=>r.json()).then(c=>{ED.cuts=c.cuts||[];}).catch(()=>{});
    // спорные вздохи/«кхе»: уверенные детектор вырезал сам, эти — на один клик
    fetch('/api/breaths',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({xml})})
      .then(r=>r.json()).then(b=>{ED.br=(b.marks||[]).filter(m=>!m['вырезано']);edDraw();}).catch(()=>{});
    edResize();edDraw();edUI();
  }catch(e){toast(t('Не открыл редактор нарезки — сервер не ответил. Проверь, что webui запущен'));uiLog(t('открытие редактора: ')+e);}}
function edResize(){const c=$('edtl');if(!c)return;const dpr=devicePixelRatio||1;
  c.width=c.clientWidth*dpr;c.height=c.clientHeight*dpr;}
function edTotal(){return ED.blocks.reduce((a,b)=>a+(b.s1-b.s0),0);}
function edBlockAt(s){for(let i=0;i<ED.blocks.length;i++){const b=ED.blocks[i];if(s>=b.s0&&s<=b.s1)return i;}return -1;}
function edCutTime(s){let a=0;for(const b of ED.blocks){if(s>=b.s1)a+=b.s1-b.s0;else{if(s>b.s0)a+=s-b.s0;break;}}return a;}
// координаты: видимое окно [v0,v1] сек -> пиксели канваса (device px)
function edS2X(s){const c=$('edtl');return (s-ED.v0)/(ED.v1-ED.v0)*c.width;}
function edX2S(x){const c=$('edtl');return ED.v0+x/c.width*(ED.v1-ED.v0);}
function edClampView(){const span=ED.v1-ED.v0;
  if(ED.v0<0){ED.v0=0;ED.v1=span;}if(ED.v1>ED.dur){ED.v1=ED.dur;ED.v0=Math.max(0,ED.dur-span);}}
function edZoom(f,atX){if(!ED.dur)return;const c=$('edtl');const ax=(atX!=null)?atX:c.width/2;const s=edX2S(ax);
  let span=(ED.v1-ED.v0)/f;span=Math.max(1.5,Math.min(ED.dur,span));
  ED.v0=s-(ax/c.width)*span;ED.v1=ED.v0+span;edClampView();edDraw();}
function edZoomFit(){ED.v0=0;ED.v1=ED.dur||1;edDraw();}
function edPush(){ED.hist.push(JSON.stringify(ED.blocks));if(ED.hist.length>60)ED.hist.shift();}
function edUndo(){if(!ED.hist.length){toast(t('Нечего отменять'));return;}
  ED.blocks=JSON.parse(ED.hist.pop());if(ED.sel>=ED.blocks.length)ED.sel=-1;edDraw();edUI();}
function edDraw(){const c=$('edtl');if(!c||!ED.dur)return;const g=c.getContext('2d');const W=c.width,H=c.height;
  const dpr=devicePixelRatio||1;const RH=Math.round(EDRULER*dpr);
  g.clearRect(0,0,W,H);g.fillStyle='#0d0d0d';g.fillRect(0,0,W,H);
  // волна ПО ВСЕМУ исходнику: оставленное зелёным, вырезанное серым (видно, что режем)
  const mid=RH+(H-RH)/2,amp=(H-RH)*0.44;let bi=0,runColor=null;
  g.lineWidth=1;g.beginPath();
  for(let x=0;x<W;x++){const s=edX2S(x);if(s<0||s>ED.dur)continue;
    while(bi<ED.blocks.length&&s>=ED.blocks[bi].s1)bi++;
    const inb=bi<ED.blocks.length&&s>=ED.blocks[bi].s0;
    const col=inb?((bi===ED.sel)?'#9fc0ff':'#6fce9e'):'#3f3f3f';
    if(col!==runColor){if(runColor)g.stroke();g.beginPath();g.strokeStyle=col;runColor=col;}
    const p=ED.peaks[Math.floor(s*ED.pps)]||0;const h=Math.max(dpr,p*amp*2);
    g.moveTo(x+.5,mid-h/2);g.lineTo(x+.5,mid+h/2);}
  if(runColor)g.stroke();
  // рамки блоков + ручки краёв
  ED.blocks.forEach((b,i)=>{const x0=edS2X(b.s0),x1=edS2X(b.s1);if(x1<0||x0>W)return;
    g.fillStyle=(i===ED.sel)?'rgba(110,168,255,.14)':'rgba(60,160,110,.08)';g.fillRect(x0,RH,x1-x0,H-RH);
    g.strokeStyle=(i===ED.sel)?'#6ea8ff':'#3ca06e';g.lineWidth=1;g.strokeRect(x0+.5,RH+.5,x1-x0-1,H-RH-1);
    g.fillStyle=(i===ED.sel)?'#6ea8ff':'#3ca06e';g.fillRect(x0,RH,2*dpr,H-RH);g.fillRect(x1-2*dpr,RH,2*dpr,H-RH);});
  // спорные вздохи/«кхе»: полоска под линейкой, клик по ней вырезает участок
  ED.brBand=Math.round(10*dpr);
  (ED.br||[]).forEach(m=>{if(edBlockAt((m.t0+m.t1)/2)<0)return;   // уже вырезано руками
    const x0=edS2X(m.t0),x1=edS2X(m.t1);if(x1<0||x0>W)return;
    g.fillStyle='rgba(224,138,60,.20)';g.fillRect(x0,RH,Math.max(2*dpr,x1-x0),H-RH);
    g.fillStyle='#e08a3c';g.fillRect(x0,RH,Math.max(2*dpr,x1-x0),ED.brBand);});
  // линейка
  g.fillStyle='#0a0a0a';g.fillRect(0,0,W,RH);
  g.strokeStyle='#212121';g.beginPath();g.moveTo(0,RH+.5);g.lineTo(W,RH+.5);g.stroke();
  const steps=[0.5,1,2,5,10,15,30,60,120,300];
  const st=steps.find(s2=>s2/(ED.v1-ED.v0)*W>=64*dpr)||300;
  g.fillStyle='#6f6f6f';g.font=(10*dpr)+'px ui-monospace,monospace';g.textBaseline='middle';
  for(let tm=Math.ceil(ED.v0/st)*st;tm<=ED.v1+1e-6;tm+=st){const x=edS2X(tm);
    g.strokeStyle='#2b2b2b';g.beginPath();g.moveTo(x+.5,0);g.lineTo(x+.5,RH);g.stroke();
    g.fillText(fmtT(tm),x+4*dpr,RH/2+dpr);}
  // плейхед (жёлтый, с флажком)
  const cx=edS2X(ED.cs);
  g.strokeStyle='#f5c518';g.lineWidth=Math.max(1,1.5*dpr);g.beginPath();g.moveTo(cx,0);g.lineTo(cx,H);g.stroke();
  g.fillStyle='#f5c518';g.beginPath();g.moveTo(cx-4*dpr,0);g.lineTo(cx+4*dpr,0);g.lineTo(cx,6*dpr);g.closePath();g.fill();}
function edUI(){const el=$('edtime');if(el)el.textContent=fmtIns(edCutTime(ED.cs))+' / '+fmtIns(edTotal());edWords();}
// Вырезанное место плейхеда: режим «слушать вырезанное» (галка ED.raw) отключён, а под
// плейхедом — щель между блоками. Спрашивают про ЭТОТ плеер — редактор; у шага 3 своего
// выреза нет, и его дорожка голоса глушиться из-за чужого плейхеда не должна.
function edRaw(P){return (typeof P==='undefined'||P===ED)&&!!ED.raw;}
function edInCut(P){return (typeof P==='undefined'||P===ED)&&!edRaw(P)&&ED.dur>0&&edBlockAt(ED.cs)<0;}
// Слово под плейхедом и строка субтитра кадра. Плеер один — редактор, поэтому и панель
// слов ведёт он (монтажного плеера, который вёл её раньше, больше нет). Считаем по
// ИСХОДНОЙ раскладке (ED.orig — то, что лежит в XML): PV.words сняты с неё, и
// несохранённая правка их не двигает.
function edWords(){
  const base=(ED.orig&&ED.orig.length)?ED.orig:ED.blocks;
  const mt=edCutOf(base,ED.cs);if(mt==null)return;            // курсор в вырезанном — подсветку не дёргаем
  const el=$('pvsub');
  if(el)el.textContent=pvWordAt(mt);}
// Перемотка — ОДНА дверь для всех: клик и драг по таймлайну, хоткеи, кнопка. Кто играет —
// тот и продолжает: состояние воспроизведения не трогаем, а плейхед и звук ставим на
// место клика сразу, без ожидания следующего кадра.
function edSeek(s){ED.cs=Math.max(0,Math.min(ED.dur,s));
  pvVideoTo(ED.cs);
  // Перемотка снимает и ожидание по промаху видео-дублёра: плейхед уже в другом месте, и
  // открыть гейт по чужому `seeked` значило бы вернуть голос на покинутый стык.
  if(ED.vtOpen)edVoiceSeekOff();
  // Разбег дорожки голоса снимается на перемотке там же, где его снимает pvVideoTo камере
  // (spareIdle): разбег готовился под конец ПРЕЖНЕГО блока, а плейхед уже в другом месте.
  // Прицел снимаем вместе с разбегом: vtSpareCtl взводит дублёра по `vspAt` каждый кадр,
  // и оставленный прицел вернул бы разогнанный дублёр на покинутый стык.
  if(typeof vtSpareIdle==='function'){vtSpareIdle(ED);vtSpareAt(ED,null);}
  if(typeof vtOf==='function')vtTick(ED,ED.cs);   // звук — на то же место и сразу, не ждём кадра
  edDraw();edUI();}
function edToggle(){ED.play?edPause():edPlay();}
function edPlay(){const v=PV.vids&&PV.vids[0];if(!v){toast(t('нет видео камеры 1'));return;}
  // Граф Web Audio будим ЗДЕСЬ и одной дверью на все плееры (audioWake): браузер держит
  // AudioContext в `suspended`, пока не было живого жеста, а звук камеры после
  // createMediaElementSource идёт ТОЛЬКО через граф — приостановленный даёт ровно
  // «видео играет, звука нет, ошибок нет». Зовём из обработчика нажатия, как и положено.
  if(typeof audioWake==='function')audioWake();
  // Строка «Firefox не читает звук этих камер» — одна на все плееры (pvAudioLimit):
  // без неё молчание исходника читается как поломка.
  if(typeof pvAudioLimit==='function')pvAudioLimit($('pvstage'),ED);
  // Голос клипа в прошлый раз не посчитался — «Играть» обязан попробовать снова: причина
  // (нет окружения RoFormer, занятый сервер) могла уйти, а vtPrep зовут только правки ручек.
  if(typeof vtOf==='function'&&vtOf(ED).failed){vtOf(ED).failed=false;vtPrep(ED);}
  if(!ED.raw&&edBlockAt(ED.cs)<0){const nb=ED.blocks.find(b=>b.s0>=ED.cs)||ED.blocks[0];if(!nb)return;ED.cs=nb.s0;}
  ED.play=true;$('edplay').innerHTML=ico('pause');
  // Пуск снимает ожидание по промаху: голос включается здесь же, и оставленный гейт
  // держал бы его немым до чужого события.
  if(ED.vtOpen)edVoiceSeekOff();
  spareIdle(PV);                                    // дублёр общий с показом кадра — начинаем с чистого листа
  pvVideoTo(ED.cs);
  v.muted=false;v.volume=MEDIA_VOL;v.style.opacity='1';v.style.zIndex='2';PV.vids.forEach((o,i)=>{if(i)o.style.zIndex='1';});
  v.play().catch(()=>{});
  vtLivePlay(ED);   // окно плагина открыто — команда «играть» уходит ему, с этого кадра
  ED.raf=requestAnimationFrame(edTick);}
function edPause(){const was=ED.play;ED.play=false;const b=$('edplay');if(b)b.innerHTML=ico('play');
  cancelAnimationFrame(ED.raf);if(PV.vids&&PV.vids[0])PV.vids[0].pause();spareStop(PV);camIdle(PV);
  if(was)vtPause(ED);}   // стояли и без нас — дорожку голоса дважды не дёргаем
// Стык блока в РЕДАКТОРЕ — тот же seek, что был в монтажном плеере, и болит он тут
// сильнее: по этому таймлайну и делают правки. Дублёр общий с монтажным плеером, цель —
// исходное время камеры 1, поэтому годится та же машина bufArm/bufRoll/bufTake. Отличие
// одно: живой <video> в редакторе красит не camVisual, а мы сами.
function edTake(at){if(!bufTake(PV,spareLead(PV),at))return false;
  const v=PV.vids[0];v.muted=false;v.volume=MEDIA_VOL;v.style.opacity='1';v.style.zIndex='2';return true;}
// Прыжок через вырезанное: картинка — дублёром или seek'ом, а голос следует ФАКТИЧЕСКОМУ
// решению картинки на ЭТОМ стыке, а не своему.
//
// Раньше дорожка голоса подменялась дублёром ВСЕГДА, а картинка при промахе видео-дублёра
// ещё ехала seek'ом. Голос оказывался на новом месте мгновенно, картинка — через задержку
// декодера: это и есть «звук спешит» (замер архитектора: 15 точек > 45 мс, p95 91 мс).
// Теперь голос идёт за видео: нет дублёра на камерах клипа — нет переезда картинки, значит
// нет и подмены голоса. Дублёр отказал или его нет вовсе — глушим и ждём `seeked` живого
// видео (страховка 300 мс), и только тогда включаем голос, выровняв его по картинке.
// Без этой ветки картинка вставала бы seek'ом, а «мягкий» промах (дублёр есть, но не
// долез) увёл бы оба потока на прыжок мимо `edTake` — и голос снова обогнал бы картинку.
function edJump(v,at){let out=v;
  const spare=spareLead(PV);
  if(!spare){
    // Дублёров камер у плеера нет вовсе: прыгать нечем, и голос не подменяем тоже.
    try{v.currentTime=at;}catch(e){}
    if(typeof vtSpareIdle==='function')vtSpareIdle(ED);
    if(typeof vtSpareAt==='function')vtSpareAt(ED,null);
    // Порядок важен: сначала голос в тишину, и только потом кадр дорожки. Наоборот — и
    // vtTick своим vtGate(P,true) вернул бы звук камеры, пока дорожка ещё звучит.
    edVoiceSeekWait(v,at);
    if(typeof vtOf==='function')vtTick(ED,at);
    return out;}
  const take=(typeof edTake==='function')&&edTake(at);
  if(take)out=PV.vids[0];          // дублёр успел — подмена элементом, звук туда же
  else try{v.currentTime=at;}catch(e){}   // не вышла — старый путь: seek на месте
  if(take||EDMUTVOICE){
    // Стык взят дублёром: дорожка проходит его своим и в ЭТОМ ЖЕ кадре. Пока живой
    // <audio> догонял бы перемоткой (а после неё он играет с задержкой 100–200 мс),
    // подменённый уже стоит на нужном кадре. Сначала кадр (vtTick получает прицел и
    // вживляет источник дублёра), потом подмена.
    if(typeof vtOf==='function')vtTick(ED,at);
    if(typeof vtOf==='function'&&typeof vtSpareTake==='function')vtSpareTake(ED,at);
    return out;}
  // Промах видео: дублёра голоса снимаем ДО кадра — vtSpareCtl взводит его по прицелу и
  // в следующем же тике подменил бы его сам, а голос на этом стыке подменять нечем.
  if(typeof vtSpareIdle==='function')vtSpareIdle(ED);
  if(typeof vtSpareAt==='function')vtSpareAt(ED,null);
  edVoiceSeekWait(v,at);
  if(typeof vtOf==='function')vtTick(ED,at);
  return out;}
// Промах видео-дублёра: голос молчит, пока картинка не встанет на место seek'ом.
//
// Глушим гейтом (vtGate) и паузой, а не одним muted: пока картинка едет, дорожка камеры 1
// обязана молчать вместе с голосом, иначе на стыке слышен сырой голос камеры. Позицию
// ставим сразу — это не перемотка в эфире (дорожка уже на паузе), а подготовка места.
//
// Ждём только РЕАЛЬНУЮ перемотку: `currentTime` присвоен, но декодер попал в буфер и
// `seeking` не поднялся — события `seeked` не будет вовсе, и ждать его значило бы держать
// голос немым всю страховку. Тогда закрываемся здесь же и в том же кадре.
function edVoiceSeekWait(v,at){
  edVoiceSeekOff();                            // прежнее ожидание (повторный прыжок) — прочь
  const live=(typeof vtOf==='function'&&vtOf(ED).el)||null;
  // Гейт ставим тому плееру, чей `vids` и есть картинка (в бою это vtMuteHost): у самого
  // редактора своих `vids` нет, и `vtGate(ED,…)` не заглушил бы НИЧЕГО — пока картинка
  // едет seek'ом, на стыке зазвучал бы сырой голос камеры. `typeof` — для стендов.
  const host=(typeof vtMuteHost==='function')?vtMuteHost(ED):ED;
  if(typeof vtGate==='function')vtGate(host,false);
  if(live){live.pause();
    const want=Math.max(0,at);
    if(Math.abs(live.currentTime-want)>0.005){try{live.currentTime=want;}catch(e){}}}
  ED.vtAt=at;                                  // куда целились: страховка, если позиция не встала
  const video=v||(PV.vids&&PV.vids[0])||null;
  ED.vtOpen=true;ED.vtEl=video;
  if(!video||!video.seeking){edVoiceSeekClose(at,video);return;}
  const onSeeked=()=>edVoiceSeekClose(at,video);
  // Ссылку на слушатель храним: без неё removeEventListener не найдёт его, и на каждом
  // промахе на видео оседал бы ещё один живой обработчик.
  ED.vtOn={fn:onSeeked};
  ED.vtTimer=setTimeout(onSeeked,300);   // страховка: событие могло потеряться
  // В node таймер держит процесс живым после прогона стенда; в браузере unref нет.
  if(ED.vtTimer&&typeof ED.vtTimer.unref==='function')ED.vtTimer.unref();
  video.addEventListener('seeked',onSeeked,{once:true});}
// Конец ожидания: голос обратно, и его позиция выровнена по ЖИВОМУ видео в этот момент —
// ровно этого не хватало, когда звук «спешил»: он стоял на месте прыжка, а картинка ещё
// подъезжала. Часы — currentTime живого видео (`at` — только запасной путь, если элемент
// уже сменился: в бою это ШАГ КАДРА при 60 к/с, на порядок меньше порога заметности).
function edVoiceSeekClose(at,video){
  if(!ED.vtOpen)return;
  edVoiceSeekOff();
  const st=(typeof vtOf==='function')?vtOf(ED):null,el=st&&st.el;
  if(!el)return;
  const want=(video&&isFinite(+video.currentTime))?(+video.currentTime):((at==null)?0:at);
  if(Math.abs(el.currentTime-Math.max(0,want))>0.005){try{el.currentTime=Math.max(0,want);}catch(e){}}
  if(typeof vtGate==='function')vtGate(ED,true);
  if(typeof vtPlaying==='function'&&!vtPlaying(ED))return;   // на паузе дорожка замирает вместе с картинкой
  el.play().catch(()=>{});}
// Снять ожидание не трогая дорожку: таймер, слушатель и отметки. Нужна там, где решение
// принято другое — новый прыжок, перемотка, пуск: открыть гейт по чужому `seeked` значило
// бы вернуть голос на покинутый стык.
function edVoiceSeekOff(){
  if(ED.vtTimer){clearTimeout(ED.vtTimer);ED.vtTimer=0;}
  const video=ED.vtEl;
  if(video&&ED.vtOn&&ED.vtOn.fn&&video.removeEventListener)
    video.removeEventListener('seeked',ED.vtOn.fn);
  ED.vtOn=null;ED.vtOpen=false;ED.vtEl=null;ED.vtAt=null;}
function edArm(){if(ED.raw||!ED.play)return;const i=edBlockAt(ED.cs);if(i<0)return;
  const b=ED.blocks[i],nb=ED.blocks[i+1];
  // Стыка впереди больше нет (последний блок или правка свела блоки вплотную) — дублёра
  // гасим. Раньше просто выходили, и разогнанный под исчезнувший стык дублёр доигрывал
  // фоном: лишний декод 4K рядом с живым — ровно тот ресурс, из-за которого стыки и дёргались.
  // Прицел голоса снимаем вместе с разбегом: vtSpareCtl взводит дублёра по `vspAt` каждый
  // кадр, и оставленный прицел вернул бы его на исчезнувший стык.
  if(!nb||nb.s0-b.s1<=0.06){bufIdle(spareLead(PV));vtSpareIdle(ED);vtSpareAt(ED,null);return;}
  // Сколько РЕАЛЬНОГО времени осталось до прыжка. Мера одна на оба дублёра: и видео, и
  // голос обязаны прийти на позицию ПОСЛЕ стыка ровно к прыжку — не раньше и не позже.
  const left=b.s1-ED.cs;
  // Прицел и разгон дорожки голоса — на ED, НЕ на PV: дорожка редактора живёт на ED
  // (vtOf(ED); edJump зовёт vtSpareTake(ED,at)), а на PV её дублёр не разгонялся вовсе —
  // замер архитектора: на КАЖДОМ из четырёх прыжков `paused:true, currentTime:0,
  // rolling:false`, удачных подмен 0 из 4. Видео-дублёр остаётся на PV: видео редактора —
  // это PV.vids, и edTake/edJump берут его там же.
  vtSpareAt(ED,nb.s0);   // прицел дорожки голоса: у редактора это блоки правки, а не EDL
  if(left>VT_ARM-PV_PREROLL)bufIdle(spareLead(PV));   // до стыка ещё далеко
  else bufArm(PV,spareLead(PV),nb.s0);            // блоки правки — цель пересчитываем каждый тик
  vtSpareArm(ED);
  // Левое время vtSpareRoll считает как «прицел − tm». Прицел голоса — НАЧАЛО следующего
  // блока (nb.s0), а прыжок редактор делает на КОНЦЕ текущего (b.s1): между ними вырезанный
  // зазор. Отсчёт от прицела даёт левое время с лишним зазором — дублёр пускается позже
  // нужного и на стыке отстаёт ровно на зазор, подмена срывается в запасной seek (это и
  // есть отставание голоса, от которого уходим: p95 48 мс, две точки > 125 мс). Отсчёт
  // ведём от реального времени до прыжка — `nb.s0-left` и есть нужное vtSpareRoll «tm».
  if(left<=VT_ARM-PV_PREROLL){bufRoll(spareLead(PV),left);vtSpareRoll(ED,nb.s0-left);}}
function edTick(){if(!ED.play)return;let v=PV.vids[0];ED.cs=v.currentTime;
  // seek ТОЛЬКО при реальном вырезанном зазоре (>60мс): микро-seek на смежном стыке (после ✂)
  // флашит декодер (readyState 4→1) и воспроизведение залипает на месте правки
  if(!ED.raw){const i=edBlockAt(ED.cs);
    if(i<0){const nb=ED.blocks.find(b=>b.s0>=ED.cs);
      if(!nb){edPause();ED.cs=ED.blocks.length?ED.blocks[0].s0:0;edDraw();edUI();return;}
      if(nb.s0>ED.cs+0.06){v=edJump(v,nb.s0);ED.cs=nb.s0;}}
    else{const b=ED.blocks[i];
      if(ED.cs>=b.s1-0.02){if(i>=ED.blocks.length-1){edPause();ED.cs=ED.blocks[0]?ED.blocks[0].s0:0;edDraw();edUI();return;}
        const nb=ED.blocks[i+1];
        if(nb.s0>ED.cs+0.06){v=edJump(v,nb.s0);ED.cs=nb.s0;}}}}
  else if(ED.cs>=ED.dur-0.05){edPause();}
  if(typeof vtOf==='function')vtTick(ED,ED.cs);   // дорожка обработанного голоса идёт за плейхедом
  if(typeof pvAudioLimit==='function')pvAudioLimit($('pvstage'),ED);   // строка про звук Firefox — по факту игры
  edArm();
  edDraw();edUI();ED.raf=requestAnimationFrame(edTick);}
// Что под курсором: БЛИЖАЙШИЙ край блока или плейхед. Раньше цикл брал первый край,
// попавший в допуск, а идёт он слева направо — и на общем зуме (163с на ~1300px) 8px
// допуска это ЦЕЛАЯ СЕКУНДА исходника. У любой убранной паузы (0.3-0.8с) оба края щели
// лежат ближе друг к другу: целишься в НАЧАЛО следующего блока — хватаешь КОНЕЦ
// предыдущего, тянешь вправо, и он съедает щель целиком (упор как раз в начало
// следующего). После такой «правки» плеер честно играет то, что считалось вырезанным,
// и к следующему блоку не переходит: переходить уже некуда (жалоба 2026-08-11).
const EDGRAB=8;   // радиус захвата края/плейхеда, css px
function edGrabAt(x){const tol=EDGRAB*(devicePixelRatio||1);let best=null;
  ED.blocks.forEach((b,i)=>{[b.s0,b.s1].forEach((s,edge)=>{const d=Math.abs(x-edS2X(s));
    if(d<tol&&(!best||d<best.d))best={i,edge,d};});});
  const dh=Math.abs(x-edS2X(ED.cs));   // плейхед нарисован поверх — вничью берём его
  if(dh<tol&&(!best||dh<=best.d))best={head:1,d:dh};
  return best;}
function edBreathAt(x,y){const dpr=devicePixelRatio||1;const RH=Math.round(EDRULER*dpr);
  if(y<RH||y>RH+(ED.brBand||0))return null;
  return (ED.br||[]).find(m=>x>=edS2X(m.t0)-2&&x<=edS2X(m.t1)+2&&edBlockAt((m.t0+m.t1)/2)>=0)||null;}
function edCutRange(t0,t1){       // вырезать участок: обрезать край блока или разрезать надвое
  edPush();const out=[];
  for(const b of ED.blocks){
    if(t1<=b.s0||t0>=b.s1){out.push(b);continue;}
    if(t0>b.s0+0.05)out.push({s0:b.s0,s1:t0});
    if(t1<b.s1-0.05)out.push({s0:t1,s1:b.s1});}
  ED.blocks=out;ED.sel=-1;edDraw();edUI();}
function edBind(){const c=$('edtl');if(!c||c._bound)return;c._bound=1;
  const px=e=>{const r=c.getBoundingClientRect();return (e.clientX-r.left)*(c.width/Math.max(1,r.width));};
  const py=e=>{const r=c.getBoundingClientRect();return (e.clientY-r.top)*(c.height/Math.max(1,r.height));};
  const dpr=()=>devicePixelRatio||1;
  c.addEventListener('mousedown',e=>{if(!ED.dur)return;const x=px(e),y=py(e);const s=edX2S(x);
    if(e.button===1){              // средняя кнопка: пан из любой точки таймлайна
      e.preventDefault();           // браузерный middle autoscroll не должен стартовать
      ED.drag={pan:1,x0:x,pv0:ED.v0,pv1:ED.v1,cursor:c.style.cursor};c.style.cursor='grabbing';return;}
    if(y<=EDRULER*dpr()){ED.drag={pan:1,x0:x,pv0:ED.v0,pv1:ED.v1,cursor:c.style.cursor};c.style.cursor='grabbing';return;}
    const br=edBreathAt(x,y);       // клик по оранжевой полоске = вырезать вздох
    if(br){edCutRange(br.t0,br.t1);uiLog(t('вздох вырезан: ')+fmtT(br.t0)+' ('+br['класс']+', '+br.p+')');return;}
    const g=edGrabAt(x);
    if(g&&g.head){ED.drag={head:1};return;}
    if(g){edPush();ED.drag={i:g.i,edge:g.edge};ED.sel=g.i;edDraw();return;}
    ED.sel=edBlockAt(s);edSeek(s);});
  window.addEventListener('mousemove',e=>{
    if(ED.drag){const x=px(e);
      if(ED.drag.pan){const ds=(ED.drag.x0-x)/c.width*(ED.drag.pv1-ED.drag.pv0);
        ED.v0=ED.drag.pv0+ds;ED.v1=ED.drag.pv1+ds;edClampView();edDraw();return;}
      if(ED.drag.head){edSeek(edX2S(x));return;}
      let s=Math.max(0,Math.min(ED.dur,edX2S(x)));
      const {i,edge}=ED.drag;const b=ED.blocks[i];const MIN=0.08;
      if(!b){ED.drag=null;return;}   // блок исчез под тягой (Ctrl+Z, вырезанный вздох) — не падаем на b.s0
      if(edge===0){const lo=i>0?ED.blocks[i-1].s1:0;b.s0=Math.max(lo,Math.min(b.s1-MIN,s));}
      else{const hi=i<ED.blocks.length-1?ED.blocks[i+1].s0:ED.dur;b.s1=Math.min(hi,Math.max(b.s0+MIN,s));}
      edDraw();edUI();return;}
    if(e.target!==c||!ED.dur)return;const x=px(e),y=py(e);const s=edX2S(x);
    let cur='text';
    const brh=edBreathAt(x,y);
    if(brh){c.style.cursor='pointer';const inf=$('edcut');
      if(inf)inf.textContent=t('похоже на «{k}» ({p}%) — клик, чтобы вырезать',{k:brh['класс'],p:Math.round(brh.p*100)});
      return;}
    if(y<=EDRULER*dpr())cur='grab';
    else{const gr=edGrabAt(x);if(gr)cur=gr.head?'ew-resize':'col-resize';}   // курсор показывает ровно то, что схватится
    c.style.cursor=cur;
    const info=$('edcut');if(info){
      if(edBlockAt(s)<0&&s>=0&&s<=ED.dur){const k=ED.cuts.find(k2=>s>=k2.t0&&s<=k2.t1);
        info.textContent=k?t('вырезано [{src}]: «{text}» — {reason}{rule}',{src:k.source||t('ИИ'),text:(k.text||'').slice(0,70),reason:(k.reason||'').slice(0,70),rule:k.rule?(' · '+k.rule):''})
                          :t('вырезанный кусок — двойной клик, чтобы вернуть');}
      else info.textContent='';}});
  window.addEventListener('mouseup',()=>{if(!ED.drag)return;const d=ED.drag;ED.drag=null;if(d.pan)c.style.cursor=d.cursor;});
  c.addEventListener('dblclick',e=>{if(!ED.dur)return;const s=edX2S(px(e));if(edBlockAt(s)>=0)return;
    let lo=0,hi=ED.dur;                              // вернуть вырезанную щель как блок
    for(const b of ED.blocks){if(b.s1<=s)lo=Math.max(lo,b.s1);if(b.s0>=s)hi=Math.min(hi,b.s0);}
    if(hi-lo<0.05)return;edPush();ED.blocks.push({s0:lo,s1:hi});ED.blocks.sort((a,b2)=>a.s0-b2.s0);
    ED.sel=ED.blocks.findIndex(b=>b.s0===lo);edDraw();edUI();});
  c.addEventListener('wheel',e=>{e.preventDefault();if(!ED.dur)return;
    if(e.shiftKey){const ds=(e.deltaY||e.deltaX)/Math.max(1,c.clientWidth)*(ED.v1-ED.v0);
      ED.v0+=ds;ED.v1+=ds;edClampView();edDraw();}
    else edZoom(e.deltaY<0?1.35:1/1.35,px(e));},{passive:false});
  c.addEventListener('auxclick',e=>{if(e.button===1)e.preventDefault();});   // middle autoscroll в Chrome/FF гаснет и по auxclick
}
// Пробел, нажатый НА КНОПКЕ (в т.ч. на чипе слова — он role=button), принадлежит ей:
// глобальный хендлер внизу файла кликает по такому элементу, и если модалка тем же
// событием дёрнет play/pause, выйдет два действия сразу — слово красится жёлтым И
// стартует плеер. Чекбокс сюда НЕ входит: на нём пробел намеренно отдан плееру.
function spaceOnControl(e){const el=e.target;
  return !!(el&&el.closest&&el.closest('button,[role=button],a[href],summary'));}
// Обратная сторона того же правила: кнопка, нажатая МЫШЬЮ, фокус себе не оставляет.
// Иначе после клика по «Копировать» в предпросмотре следующий пробел жал ЕЁ ЖЕ вместо
// play/pause — плеер молчал, а запрос копировался второй раз (2026-07-30).
// e.detail===0 = активация с клавиатуры (Enter/пробел): там фокус нужен, не трогаем.
// Фаза перехвата + setTimeout: снимаем фокус ПОСЛЕ обработчиков (они могут сами увести его,
// как правка чипа в инпут) и мимо возможного stopPropagation по дороге наверх.
document.addEventListener('click',e=>{
  if(!e.detail)return;
  const el=e.target&&e.target.closest&&e.target.closest('button,[role=button],summary');
  if(!el)return;
  setTimeout(()=>{if(document.activeElement===el)el.blur();},0);},true);
document.addEventListener('keydown',e=>{                    // хоткеи редактора (модалка открыта, не в поле ввода)
  if(!$('mbPreview').classList.contains('on'))return;
  if(e.key===' '&&spaceOnControl(e))return;
  if(e.key===' '&&e.target&&e.target.type==='checkbox'){e.preventDefault();e.target.blur();edToggle();return;}
  const tg=(e.target.tagName||'').toLowerCase();if(tg==='input'||tg==='textarea'||tg==='select')return;
  const k=e.key.toLowerCase();
  if(e.key===' '){e.preventDefault();edToggle();}
  else if(k==='c'||k==='с'||k==='s'||k==='ы'){edSplit();}
  else if(k==='d'||k==='в'||e.key==='Delete'||e.key==='Backspace'){edDelSel();}
  else if((e.ctrlKey||e.metaKey)&&k==='z'){e.preventDefault();edUndo();}
  else if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();
    const st=e.shiftKey?1:2/(ED.fps||60);edSeek(ED.cs+(e.key==='ArrowRight'?st:-st));}});
function edSplit(){const i=edBlockAt(ED.cs);if(i<0){toast(t('Поставь курсор на блок'));return;}const b=ED.blocks[i];
  if(ED.cs<=b.s0+0.05||ED.cs>=b.s1-0.05)return;edPush();
  ED.blocks.splice(i,1,{s0:b.s0,s1:ED.cs},{s0:ED.cs,s1:b.s1});ED.sel=i+1;edDraw();edUI();}
function edDelSel(){if(ED.sel<0){toast(t('Выбери блок (клик по нему)'));return;}edPush();
  ED.blocks.splice(ED.sel,1);ED.sel=-1;edDraw();edUI();}
// монтажное время -> время исходника (по старой раскладке) и обратно (по новой):
// так тайминги вставок переезжают на новую нарезку вместо того, чтобы молча съехать.
function edSrcOf(blocks,tm){let a=0;for(const b of blocks){const d=b.s1-b.s0;if(tm<=a+d)return b.s0+Math.max(0,tm-a);a+=d;}return null;}
function edCutOf(blocks,src){let a=0;for(const b of blocks){if(src<b.s0)return null;if(src<=b.s1)return a+(src-b.s0);a+=b.s1-b.s0;}return null;}
function edRemapInserts(c){if(!c||!(ED.orig||[]).length)return '';
  const remap=tm=>{const s=edSrcOf(ED.orig,tm);return s==null?null:edCutOf(ED.blocks,s);};
  let moved=0,lost=0;const fps=ED.fps||60;
  (c.inserts||[]).forEach(x=>{const nt=remap(x.start_sec||0);
    if(nt==null)lost++;else{if(Math.abs(nt-(x.start_sec||0))>0.02)moved++;x.start_sec=Math.round(nt*100)/100;}});
  if(c.job&&Array.isArray(c.job.ins))c.job.ins.forEach(x=>{const nt=remap((x.start_s||0)+(x.start_f||0)/fps);
    if(nt!=null){x.start_s=Math.floor(nt);x.start_f=Math.round((nt%1)*fps);}});
  return (moved||lost)?(t('Вставки: ')+(moved?moved+t(' сдвинуто под новый монтаж'):'')
    +(lost?((moved?', ':'')+lost+t(' попали в вырезанное — проверь вручную')):'')):'';}
async function edSave(){const btn=$('edsave');const info=$('edtime');
  const c=curEdit>=0?CLIPS[curEdit]:null;
  if(c&&c.status&&c.status.subs>0&&!await askConfirm(t('В XML уже есть субтитры — пересборка нарезки удалит их, разметку нужно будет повторить (тайминги вставок пересчитаю сам). Продолжить?')))return;
  btn.disabled=true;info.textContent=t('сохраняю…');
  try{const d=await (await fetch('/api/editor_save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({xml:ED.xml,keep:ED.blocks.map(b=>[b.s0,b.s1])})})).json();
    if(d.error){toast(errText(d));info.textContent='⚠';}
    else{info.textContent=t('сохранено ({n} блоков, {d}с)',{n:d.segs,d:d.dur});
      if(c){const msg=edRemapInserts(c);if(msg){toast(msg);uiLog(msg);}
        const nhl=clearHl(c);                        // индексы жёлтых больше не совпадают со словами
        if(nhl)uiLog(t('жёлтые сброшены ({n}) — после пересборки индексы указывали бы на другие слова',{n:nhl}));
        c.status={};c.edited=true;saveState();syncClipLists();renderClips1();}  // субтитры/жёлтые стёрты пересборкой — статус сброшен; клип помечен «правлено»
      ED.orig=ED.blocks.map(b=>({s0:b.s0,s1:b.s1}));        // новая база для следующей правки в этой же сессии
      ED.hist=[];                                           // сохранено — «несохранённых правок» больше нет
      await openPreview(ED.xml);edUI();}
  }catch(e){toast(t('НЕ сохранил правку нарезки — сервер не ответил. XML не тронут, нажми «Сохранить» ещё раз'));uiLog('editor_save: '+e);}btn.disabled=false;}

// ================= markup (step 2) =================
async function markupOne(i){if(uiBusyGuard())return;const c=CLIPS[i];progOpen({title:t('Разметка')});uiBusySet(true);
  try{const ok=await markupClip(c);renderClips2();saveState();
    if(ok)progDone(t('Готово: ')+c.name);
    else progDone(t('Остановлено'),true);}   // список не закрываем сами: у окна есть «Закрыть»
  finally{uiBusySet(false);}
}
// Конвейер разметки: правило зависимостей шагов от локальности моделей.
function markupPlan(phases){
  const p=phases||[];
  const hasSubs=p.includes('subs');
  const hasYellow=p.includes('yellow');
  const hasInserts=p.includes('inserts');
  const cfg=(typeof AICFG!=='undefined'&&AICFG)||{};
  const loc=cfg.step_local||{};
  const mod=cfg.step_profiles||{};
  const yellowLocal=Boolean(loc.yellow);
  const insertsLocal=Boolean(loc.inserts);
  const yellowModel=mod.yellow||'';
  const insertsModel=mod.inserts||'';
  const diffModels=(yellowModel!==insertsModel);
  // Локальные шаги ждут окончания всех субтитров (VRAM).
  // Если оба шага локальные и модели разные — вставки ждут окончания всех жёлтых.
  const yellowWaitAllSubs=hasSubs&&yellowLocal;
  const insertsWaitAllSubs=hasSubs&&insertsLocal;
  const insertsWaitAllYellow=hasYellow&&hasInserts&&yellowLocal&&insertsLocal&&diffModels;
  return {
    phases:p,
    hasSubs,
    hasYellow,
    hasInserts,
    yellowLocal,
    insertsLocal,
    yellowModel,
    insertsModel,
    diffModels,
    yellowWaitAllSubs,
    insertsWaitAllSubs,
    insertsWaitAllYellow,
  };
}
// «Разметить всё» — конвейер, локальные шаги — пофазно из-за VRAM.
// Облачные шаги стартуют сразу по готовности субтитров клипа.
// UICANCEL (кнопка «Остановить») рвёт цикл между шагами.
async function markupAll(){if(uiBusyGuard())return;const subeng=val('subengine')||'whisper';const list=selClips();if(!list.length){toast(t('Нет клипов'));return;}
  progOpen({title:t('Разметка')});uiBusySet(true);
  // «Разметить всё» молча пропускает уже готовое, как раньше: вопрос о
  // перезаписи — только у явного запуска ОДНОЙ фазы (кнопки «Субтитры/Жёлтые/Вставки»).
  try{await markupAllRun(subeng,list,['subs','yellow','inserts']);}finally{uiBusySet(false);}}
// Отдельная фаза разметки на выбранных клипах: те же галочки, что и в
// сборке (selClips: пусто у всех = все), прогресс делится на число выбранных фаз.
// Явный запуск фазы — ask=true: если фаза уже сделана, спросим о перезаписи.
async function markupPhase(phase){if(uiBusyGuard())return;const subeng=val('subengine')||'whisper';const list=selClips();if(!list.length){toast(t('Нет клипов'));return;}
  progOpen({title:t('Разметка')});uiBusySet(true);
  try{await markupAllRun(subeng,list,[phase],true);}finally{uiBusySet(false);}}
async function markupAllRun(subeng,list,phases,ask){
  // Одна фабрика на обе очереди разметки — ЛОКАЛЬНО: стенды вырезают из файла
  // только markupAllRun и падают на ReferenceError, будь она уровнем файла.
  // Методы объявлены свойствами (`push:function(item){…}`), а не сокращённой
  // записью: в сокращённой сторож tests/test_ui_js_calls.py видит вызовы
  // несуществующих `push`/`close`.
  const makeAsyncQueue=function(){
    const q=[];const waiters=[];let closed=false;
    return {
      push:function(item){if(closed)return;if(waiters.length)waiters.shift()(item);else q.push(item);},
      close:function(){closed=true;while(waiters.length)waiters.shift()(null);},
      next:function(){if(q.length)return Promise.resolve(q.shift());if(closed)return Promise.resolve(null);return new Promise(r=>waiters.push(r));}
    };
  };
  if(typeof AICFG==='undefined'||!AICFG){try{await loadAIProfiles();}catch(e){}}
  const batch='mk'+Date.now().toString(36);
  const N=list.length,fail=new Set();
  const failSubs=new Set();
  // Свой список роликов в окне прогресса: серверного задания у разметки нет, и до этого
  // в списке висели строки ПРОШЛОЙ нарезки вместо того, что размечается сейчас.
  localQStart(list.map(c=>c.name));
  const _poolRunner=typeof runPool==='function'?runPool:async(items,n,fn)=>{
    const isQ=typeof items.next==='function';
    const count=Math.max(1,Math.min(16,Math.floor(n)||1));
    let idx=0;const workers=[];
    for(let w=0;w<count;w++){
      workers.push((async()=>{
        while(true){
          if(typeof UICANCEL!=='undefined'&&UICANCEL)break;
          let item;
          if(isQ){item=await items.next();if(item==null)break;}
          else{if(idx>=items.length)break;item=items[idx++];}
          await fn(item,idx);
        }
      })());
    }
    await Promise.all(workers);
  };
  const yellowQueue=makeAsyncQueue();
  const insertsQueue=makeAsyncQueue();
  try{
    for(let i=0;i<N;i++){const c=list[i];       // свежие статусы (что пропускать)
      try{const st=await (await fetch('/api/xml_state',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({xml:c.xml})})).json();
        if(st.error)throw errText(st);c.status={subs:st.subs,colored:st.colored,ncams:st.ncams};}
      catch(e){uiLog('✗ '+c.name+': '+e);fail.add(c);localQSet(c.name,'error',''+e);}}
    const P=phases.length;
    const plan=(typeof markupPlan==='function'?markupPlan:(ph=>({
      hasSubs:(ph||[]).includes('subs'),hasYellow:(ph||[]).includes('yellow'),hasInserts:(ph||[]).includes('inserts'),
      yellowWaitAllSubs:(ph||[]).includes('subs')&&Boolean(typeof AICFG!=='undefined'&&AICFG&&AICFG.step_local&&AICFG.step_local.yellow),
      insertsWaitAllSubs:(ph||[]).includes('subs')&&Boolean(typeof AICFG!=='undefined'&&AICFG&&AICFG.step_local&&AICFG.step_local.inserts),
      insertsWaitAllYellow:(ph||[]).includes('yellow')&&(ph||[]).includes('inserts')&&Boolean(typeof AICFG!=='undefined'&&AICFG&&AICFG.step_local&&AICFG.step_local.yellow)&&Boolean(typeof AICFG!=='undefined'&&AICFG&&AICFG.step_local&&AICFG.step_local.inserts)&&(((typeof AICFG!=='undefined'&&AICFG&&AICFG.step_profiles&&AICFG.step_profiles.yellow)||'')!==((typeof AICFG!=='undefined'&&AICFG&&AICFG.step_profiles&&AICFG.step_profiles.inserts)||''))
    })))(phases);

    let donePairs=0;
    const totalPairs=N*P;
    const clipDonePhases={};
    const pushedYellow=new Set();
    const pushedInserts=new Set();

    const onSubsReady=(c)=>{
      if(!fail.has(c)&&!failSubs.has(c)&&c.status&&c.status.subs>0){
        if(plan.hasYellow&&!plan.yellowWaitAllSubs&&!pushedYellow.has(c.name)){
          pushedYellow.add(c.name);yellowQueue.push(c);
        }
        if(plan.hasInserts&&!plan.insertsWaitAllSubs&&!pushedInserts.has(c.name)){
          pushedInserts.add(c.name);insertsQueue.push(c);
        }
      }
    };

    const checkClipDone=(c,no)=>{
      if(!clipDonePhases[c.name])clipDonePhases[c.name]=new Set();
      clipDonePhases[c.name].add(no);
      if(phases.every((p,idx)=>clipDonePhases[c.name].has(idx+1))){
        if(!fail.has(c)&&(c.status&&c.status.subs>0)){
          localQSet(c.name,'done',qClipSum(c));
        }
      }
    };

    const phase=async(no,title,dep,has,call,pool,stepName)=>{
      const already=ask?list.filter(c=>!fail.has(c)&&has(c)):[];
      const force=ask&&already.length&&await askConfirm(t('Уже размечено у {n}: {names}.\nФаза «{title}» будет пересчитана заново. Продолжить?',{n:already.length,names:already.map(c=>c.name).join(', '),title:title}));
      if(stepName==='subs'){
        for(let i=0;i<N;i++){if(UICANCEL)return;const c=list[i];
          if(fail.has(c)){donePairs++;checkClipDone(c,no);continue;}
          if(dep(c)){localQSet(c.name,'wait',t('нет субтитров'));donePairs++;checkClipDone(c,no);continue;}
          if(!force&&has(c)){localQSet(c.name,no===P?'done':'wait',qClipSum(c));donePairs++;checkClipDone(c,no);onSubsReady(c);continue;}
          progQueue(t('Разметка {n}/{P} — {title}',{n:no,P:P,title:title}),i,N);
          progStep(title,(no-1)/P+i/N/P);
          uiLog('▸ '+c.name+' — '+title+'…');
          try{await call(c);if(!fail.has(c))localQSet(c.name,no===P?'done':'wait',qClipSum(c));onSubsReady(c);}
          catch(e){localQSet(c.name,'error',''+e);toast(title+' · '+c.name+': '+e);uiLog(t('  ОШИБКА: ')+e);fail.add(c);failSubs.add(c);}
          donePairs++;checkClipDone(c,no);
          renderClips2();saveState();
        }
      }else{
        const isYellow=(stepName==='yellow');
        const q=isYellow?yellowQueue:insertsQueue;
        const pushed=isYellow?pushedYellow:pushedInserts;
        const waitAllSubs=isYellow?plan.yellowWaitAllSubs:plan.insertsWaitAllSubs;
        if(waitAllSubs){
          await subsPromise;
          if(!isYellow&&plan.insertsWaitAllYellow){
            await yellowPromise;
          }
          for(const c of list){
            if(!failSubs.has(c)&&c.status&&c.status.subs>0&&!pushed.has(c.name)){
              pushed.add(c.name);q.push(c);
            }
          }
          q.close();
        }
        await _poolRunner(q,pool,async(c,idx)=>{
          if(typeof UICANCEL!=='undefined'&&UICANCEL)return;
          if(failSubs.has(c)){donePairs++;checkClipDone(c,no);return;}
          if(dep(c)){localQSet(c.name,'wait',t('нет субтитров'));donePairs++;checkClipDone(c,no);return;}
          if(!force&&has(c)){if(!fail.has(c))localQSet(c.name,no===P?'done':'wait',qClipSum(c));donePairs++;checkClipDone(c,no);return;}
          progQueue(t('Разметка {n}/{P} — {title}',{n:no,P:P,title:title}),donePairs,totalPairs);
          progStep(title,(no-1)/P+donePairs/totalPairs);
          uiLog('▸ '+c.name+' — '+title+'…');
          try{await call(c);if(!fail.has(c))localQSet(c.name,no===P?'done':'wait',qClipSum(c));}
          catch(e){localQSet(c.name,'error',''+e);toast(title+' · '+c.name+': '+e);uiLog(t('  ОШИБКА: ')+e);fail.add(c);}
          donePairs++;checkClipDone(c,no);
          renderClips2();saveState();
        });
      }
    };

    // Клипы с уже готовыми субтитрами сразу доступны облачным шагам
    for(const c of list){
      if(!fail.has(c)&&c.status&&c.status.subs>0){
        if(plan.hasYellow&&!plan.yellowWaitAllSubs&&!pushedYellow.has(c.name)){
          pushedYellow.add(c.name);yellowQueue.push(c);
        }
        if(plan.hasInserts&&!plan.insertsWaitAllSubs&&!pushedInserts.has(c.name)){
          pushedInserts.add(c.name);insertsQueue.push(c);
        }
      }
    }
    if(!plan.hasSubs){
      for(const c of list){
        if(plan.hasYellow&&!pushedYellow.has(c.name)){pushedYellow.add(c.name);yellowQueue.push(c);}
        if(plan.hasInserts&&!pushedInserts.has(c.name)){pushedInserts.add(c.name);insertsQueue.push(c);}
      }
      yellowQueue.close();
      insertsQueue.close();
    }

    const subLbl=typeof engLabel==='function'?engLabel(subeng):subeng;
    let subsPromise=Promise.resolve();
    let yellowPromise=Promise.resolve();
    let insertsPromise=Promise.resolve();

    if(phases.includes('subs')){
      subsPromise=phase(phases.indexOf('subs')+1,t('субтитры (')+subLbl+')',()=>false,c=>c.status.subs>0,async c=>{
        localQSet(c.name,'subs','');
        const d=await (await fetch('/api/gen_subs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({xml:c.xml,subengine:subeng})})).json();
        if(d.error)throw errText(d);c.status.subs=d.subs;uiLog(t('  субтитры: ')+d.subs+(typeof subSkipped==='function'?subSkipped(d):''));},1,'subs');
    }
    subsPromise.then(()=>{
      if(plan.hasYellow&&!plan.yellowWaitAllSubs)yellowQueue.close();
      if(plan.hasInserts&&!plan.insertsWaitAllSubs)insertsQueue.close();
    });

    if(phases.includes('yellow')){
      yellowPromise=phase(phases.indexOf('yellow')+1,t('жёлтые (ИИ)'),c=>!(c.status.subs>0),c=>c.status.colored>0,async c=>{
        localQSet(c.name,'yellow','');
        const d=await aiPost('/api/ai_yellow',{xml:c.xml,batch},t('жёлтые (ИИ)'));
        if(d.error)throw errText(d);c.status.colored=(d.colored||d.yellow||[]).length;uiLog(t('  жёлтых: ')+c.status.colored);
        if(typeof clearHl==='function')clearHl(c);if(curAE>=0&&CLIPS[curAE]===c)loadWordsFor(c.xml);},aiStepConc('yellow'),'yellow');
    }

    if(phases.includes('inserts')){
      insertsPromise=phase(phases.indexOf('inserts')+1,t('вставки (ИИ)'),c=>!(c.status.subs>0),c=>(c.inserts||[]).length>0,async c=>{
        localQSet(c.name,'inserts','');
        const d=await aiPost('/api/ai_inserts',{xml:c.xml,batch,rejected:c.ins_rejected||[],speaker:clipSpeaker(c)||undefined},t('вставки (ИИ)'));
        if(d.error)throw errText(d);c.inserts=(d.inserts||[]).map(x=>({...x,media:''}));c.insTarget=Math.max(d.insTarget||0,c.inserts.length);if(typeof insLog==='function')insLog(d);uiLog(t('  вставок: ')+c.inserts.length);
        if(!fail.has(c))localQSet(c.name,'files','');
        uiLog(t('  файлы:')+((typeof insAfterAI==='function'&&await insAfterAI(c))||' —'));},aiStepConc('inserts'),'inserts');
    }

    await Promise.all([subsPromise,yellowPromise,insertsPromise]);

    const ok=N-fail.size;
    renderClips2();saveState();
    if(typeof UICANCEL!=='undefined'&&UICANCEL)progDone(t('Остановлено — без ошибок: {n} из {m}',{n:ok,m:N}),true);
    else if(ok===N)progDone(t('Размечено клипов: ')+ok);
    else progDone(t('Размечено {n} из {m} — см. логи/сообщения',{n:ok,m:N}),true);
  }finally{localQEnd();}
}
// Итог строки списка по клипу: что у него уже посчитано. Собирается из состояния клипа,
// поэтому годится и для пропуска «уже есть» (итог сразу, без запуска фазы), и для конца прогона.
function qClipSum(c){const s=c.status||{};const p=[];
  if(s.subs>0)p.push(t('субтитры: ')+s.subs);
  if(s.colored>0)p.push(t('жёлтых: ')+s.colored);
  if((c.inserts||[]).length)p.push(t('вставок: ')+c.inserts.length);
  return p.join(' · ');}
// субтитры с нуля -> жёлтые -> вставки; статусы читаем через xml_state, вставки в clip.inserts
async function markupClip(c){const xml=c.xml;const subeng=val('subengine')||'whisper';
  // Одиночная разметка — список из одного клипа. Из пакета (markupAllRun) список уже
  // заведён, и свой начинать нельзя: он затёр бы строки остальных роликов прогона.
  const own=!LOCALQ;if(own)localQStart([c.name]);
  try{
    // Разметка одного клипа — очередь из одного: своего контекста ещё нет, ставим его сами.
    // Из пакета (markupAllRun) контекст уже выставлен снаружи — тогда его НЕ перетираем:
    // иначе на экране вместо «клип 3 из 8 · имя» появилось бы «клип 1 из 1».
    if(!PROGQ)progQueue(t('Разметка'),0,1);
    uiLog('▸ '+c.name+t(' — разметка'));
    const stop=()=>{if(UICANCEL){uiLog(t('  остановлено по кнопке'));return true;}return false;};
    if(stop())return false;
    let st={};try{st=await (await fetch('/api/xml_state',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({xml})})).json();}catch(e){toast(''+e);uiLog(t('  ошибка: ')+e);localQSet(c.name,'error',''+e);return false;}
    if(st.error){toast(errText(st));uiLog(t('  ошибка: ')+st.error);localQSet(c.name,'error',''+st.error);return false;}
    c.status={subs:st.subs,colored:st.colored,ncams:st.ncams};
    localQSet(c.name,'wait',qClipSum(c));       // что уже есть — видно до первого этапа
    // 1. субтитры
    const subLbl=engLabel(subeng);
    if(!(st.subs>0)){localQSet(c.name,'subs','');progStep(t('субтитры с нуля (')+subLbl+')…');uiLog(t('  субтитры с нуля (')+subLbl+')…');
      const d=await (await fetch('/api/gen_subs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({xml,subengine:subeng})})).json();
      if(d.error){toast(t('субтитры: ')+errText(d));uiLog(t('  субтитры: ОШИБКА — ')+d.error);localQSet(c.name,'error',''+d.error);return false;}
      c.status.subs=d.subs;uiLog(t('  субтитры: ')+d.subs+subSkipped(d));await sleep(700);}
    else uiLog(t('  субтитры уже есть ({n}) — пропуск',{n:st.subs}));
    if(stop())return false;

    // 2. жёлтые и вставки: параллельно по Promise.all или пофазно (если оба локальные и разные модели)
    const plan=(typeof markupPlan==='function'?markupPlan:(ph=>({insertsWaitAllYellow:false})))(['yellow','inserts']);
    let yellowOk=true;
    let insertsOk=true;

    const doYellow=async()=>{
      if(!(c.status.colored>0)){localQSet(c.name,'yellow','');progStep(t('жёлтые слова (ИИ)…'));uiLog(t('  жёлтые (ИИ)…'));
        try{
          const d=await aiPost('/api/ai_yellow',{xml},t('жёлтые (ИИ)'));
          if(d.error)throw errText(d);
          c.status.colored=(d.colored||d.yellow||[]).length;uiLog(t('  жёлтых: ')+c.status.colored);
          clearHl(c);if(curAE>=0&&CLIPS[curAE]===c)loadWordsFor(c.xml);await sleep(700);
        }catch(e){
          toast(t('жёлтые: ')+e);uiLog(t('  жёлтые: ОШИБКА — ')+e);localQSet(c.name,'error',''+e);yellowOk=false;
        }
      }else uiLog(t('  жёлтые уже есть ({n}) — пропуск',{n:c.status.colored}));
    };

    const doInserts=async()=>{
      if(!(c.inserts||[]).length){localQSet(c.name,'inserts','');progStep(t('вставки (ИИ)…'));uiLog(t('  вставки (ИИ)…'));
        try{
          const d=await aiPost('/api/ai_inserts',{xml,rejected:c.ins_rejected||[],speaker:clipSpeaker(c)||undefined},t('вставки (ИИ)'));
          if(d.error)throw errText(d);
          c.inserts=(d.inserts||[]).map(x=>({...x,media:''}));c.insTarget=Math.max(d.insTarget||0,c.inserts.length);insLog(d);uiLog(t('  вставок: ')+c.inserts.length);
          localQSet(c.name,'files','');
          uiLog(t('  файлы:')+(await insAfterAI(c)||' —'));
        }catch(e){
          toast(t('вставки: ')+e);uiLog(t('  вставки: ОШИБКА — ')+e);localQSet(c.name,'error',''+e);insertsOk=false;
        }
      }else uiLog(t('  вставки уже есть ({n}) — пропуск',{n:c.inserts.length}));
    };

    if(plan.insertsWaitAllYellow){
      await doYellow();
      if(stop())return false;
      await doInserts();
    }else{
      await Promise.all([doYellow(),doInserts()]);
    }
    if(!yellowOk||!insertsOk)return false;
    localQSet(c.name,'done',qClipSum(c));
    return true;
  }finally{if(own)localQEnd();}}
function sleep(ms){return new Promise(r=>setTimeout(r,ms));}
// Сколько роликов шаг гонит СРАЗУ (переопределение шага из настроек, 1..16). Уровень файла,
// а не локальная стрелка в markupAllRun: то же число нужно пакетному ИИ-интро (aiIntroAllRun),
// и вторая копия зажима разошлась бы с первой. AICFG может быть ещё не загружен — тогда 1.
function aiStepConc(s){const c=(typeof AICFG!=='undefined'&&AICFG)||{};return Math.max(1,Math.min(16,(c.step_concurrency||{})[s]||1));}
async function runPool(items,n,fn){
  if(!items)return;
  const isQ=typeof items.next==='function';
  if(!isQ&&!items.length)return;
  const count=Math.max(1,Math.min(isQ?16:items.length,Math.floor(n)||1));
  let idx=0;
  const workers=[];
  for(let w=0;w<count;w++){
    workers.push((async()=>{
      while(true){
        if(typeof UICANCEL!=='undefined'&&UICANCEL)break;
        let item,i;
        if(isQ){
          item=await items.next();
          if(item===null||item===undefined)break;
          i=idx++;
        }else{
          i=idx++;
          if(i>=items.length)break;
          item=items[i];
        }
        await fn(item,i);
      }
    })());
  }
  await Promise.all(workers);
}

// ── «Стоп» для одиночных ИИ-вызовов (ИИ интро, вставки): пока запрос в полёте, рядом
// с ИИ-кнопками появляется «⏹ Стоп», а сами ИИ-кнопки блокируются (случайный повторный
// клик ничего не запустит). Стоп = abort fetch + POST /api/ai_stop (флаг CANCEL в aicut
// + выгрузка модели — LM Studio бросает генерацию, VRAM освобождается).
let AIREQ=null;   // AbortController текущего одиночного ИИ-запроса (глобально один)
function aiBusy(stopId,on){const s=$(stopId);if(s){s.style.display=on?'':'none';s.disabled=false;}
  document.querySelectorAll('[data-aigrp="'+stopId+'"]').forEach(b=>b.disabled=on);}
// Живой хвост серверного лога, пока ИИ-запрос висит: ИИ-роуты синхронные (не джобы),
// поллинга у них не было — и минутный вызов выглядел как зависший UI. Тянем
// /api/status?since= раз в секунду и отдаём последнюю строку в setStatus.
// `tag` (необязателен) — имя XML клипа без расширения: в пачке сервер помечает свои
// строки префиксом «[имя] », и без фильтра хвост собирался из строк РАЗНЫХ роликов
// (они идут параллельно, а лог общий). Со своими строками префикс срезаем — он служебный.
// Возвращает функцию остановки.
function logTail(setStatus,tag){
  const t0=Date.now();let stop=false;
  // Хвост показываем ТОЛЬКО из строк, пришедших ПОСЛЕ старта этого вызова. Раньше брали
  // последнюю строку всего кэша — и пока новое действие молчало (модель думает первые
  // секунды), в подписи висел результат ПРЕДЫДУЩЕГО («✔ жёлтых: 12»), как будто он
  // относится к текущему шагу. Теперь до первой своей строки честно пишем «…».
  let from=LOGCACHE.length;
  (async function tick(){
    if(stop)return;
    try{const d=await (await fetch('/api/status?since='+LOGSINCE)).json();mergeLog(d);}catch(e){}
    if(stop)return;
    if(LOGCACHE.length<from)from=0;         // сервер начал лог заново (mergeLog->logReset)
    if(setStatus){const pre=tag?'['+tag+'] ':'';
      // Фильтруем ГОТОВЫЕ строки (fmtLog): структурная запись лога — объект {t,v},
      // и префикс лежит в его шаблоне, а не в начале String(l).
      let last=([...LOGCACHE.slice(from)].map(fmtLog).reverse()
        .find(l=>l.trim()&&(!tag||l.indexOf(pre)>=0))||'').trim();
      if(tag&&last.indexOf(pre)===0)last=last.slice(pre.length);
      setStatus(Math.round((Date.now()-t0)/1000)+t('с')+(last?' · '+last.slice(0,70):' · …'));}
    setTimeout(tick,1000);})();
  return()=>{stop=true;};}
// ИИ-вызов внутри пакетной разметки (там свой прогресс-оверлей, не aiFetch):
// обычный POST + хвост серверного лога в подпись прогресса и в строку своего клипа.
// Тег — стем XML (то же, что сервер ставит в префикс); строку списка держим ССЫЛКОЙ
// (progCurItem), а не поиском по имени: имя клипа и стем XML могут не совпадать.
//
// Строку лога в окно прогресса НЕ выводим: разбираем её в КОД события (одно место —
// progEventFromLog) и показываем короткий словарный статус. Сама строка остаётся в
// «Показать логи» — «думает: 560 симв. размышлений…» в строке ролика ничего не значило.
async function aiPost(url,body,label){
  const tag=(body&&body.batch&&body.xml)
    ?String(body.xml).replace(/^.*[\\\/]/,'').replace(/\.[^.]*$/,'') : null;
  const row=progCurItem();
  const untail=logTail(s=>{const ev=progEventFromLog(s);
    progUpdate(null,ev?PROGEV[ev]:label);
    if(tag&&row&&ev)progItem(row.name,ev);},tag);
  try{return await (await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body)})).json();}
  finally{untail();}}
async function aiFetch(url,body,stopId,statusId){
  if(AIREQ)throw new Error(t('ИИ уже работает — останови его или дождись'));
  AIREQ=new AbortController();aiBusy(stopId,true);
  const el=statusId?$(statusId):null;
  const untail=logTail(el?(s=>{el.className='muted';el.textContent='⏳ '+s;}):null);
  try{const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body),signal:AIREQ.signal});
    return await r.json();}
  finally{untail();AIREQ=null;aiBusy(stopId,false);}}
async function aiStop(stopId){const s=$(stopId);if(s)s.disabled=true;
  if(AIREQ)AIREQ.abort();
  uiLog(t('⏹ ИИ-вызов остановлен — выгружаю модель…'));
  try{await fetch('/api/ai_stop',{method:'POST'});}catch(e){}}
function aiAborted(e){return e&&(e.name==='AbortError'||e.code===20);}

