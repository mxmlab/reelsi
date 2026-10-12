// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Maxim Si
// ================= подкаст → рилсы: окно правки кусков (П5) =================
// Окно 16:9 из карточки подкаста («Править» на шаге 1): видео (прокси 720p, пока его нет —
// исходник), список кусков и таймлайн «Вся запись». Слова расшифровки — строкой над волной
// того же таймлайна: одна полоса, один зум, одно окно (v0/v1).
// Разметка окна — mbPod в templates/index.html. Чистые функции (без DOM) — podParseTime,
// podFmtTime, podClampSeg, podSnap, podSplitAt, podGapAt, podWordBubbles — их проверяет
// tests/test_podcast_editor_js.py через node.
// Имя локальной переменной `t` здесь не брать: оно закрывает функцию перевода t().

const POD_MIN_LEN=5;        // минимальная длина куска, с (и от каждого края при разрезе)
const POD_SNAP_DIST=1;      // прилипание границы к слову — не дальше, с
const POD_NEW_LEN=60;       // «+ Кусок»: длина нового куска, с
const POD_GAP_MAX=60;       // двойной клик по пустому месту: новый кусок не длиннее, с (вокруг точки)
const POD_BUBBLE_PAD=6;     // поле бабла слова по бокам, css px (текст 11 px, как фишка слова на шаге 1)
const POD_BUBBLE_GAP=4;     // зазор между баблами слов, css px: следующий не ближе, чем на столько к правому краю предыдущего
// Цвета, шаг подписей и подписи линейки — общие для полос времени: TL_COL, tlTickStep,
// tlRulerLabel в 00-core.js (см. там). Здесь — только геометрия окна подкаста.
const POD_TL_RULER=18;      // высота линейки, css px (как EDRULER у шага 1 и у превью вставок)
const POD_TL_RIBBON=22;     // высота ленты слов над волной, css px (общая полоса, баблы внутри)
const POD_TL_RIBBON_GAP=4;  // зазор ленты от линейки и от волны, css px
const POD_TL_RIBBON_X=2;    // отступ ленты от краёв холста по горизонтали, css px
const POD_TL_HANDLE=5;      // ловит край блока, css px
const POD_TL_PH=6;          // ловит плейхед на «Вся запись»: ±6 css px от линии
const POD_VIEW_MIN=5;       // «Вся запись»: окно не уже, с
const POD_ZOOM_STEP=TL_ZOOM_STEP;   // один щелчок колеса / кнопки «+» над «Вся запись» — общий шаг (00-core.js)
const POD_PEAKS_PPS=50;     // пиков волны в секунду (сетка /api/waveform)
const POD_WAV='audio16k.wav';   // звук для расшифровки лежит в папке подкаста
const POD_SAVE_MS=600;          // автосохранение: через сколько мс после последней правки уходит запрос
const POD_HIST_MAX=100;         // глубина истории правок кусков — как INS_HIST_MAX у вставок (80-inserts.js)

// --- чистые функции ---------------------------------------------------------------

// «1:02:03.5», «02:03», «75», «1:02:03» → секунды; мусор → null.
// Все части, кроме последней, — целые; последняя может быть дробной.
function podParseTime(str){
  const s=String(str==null?'':str).trim().replace(',','.');
  if(!s)return null;
  const parts=s.split(':');
  if(parts.length>3)return null;
  let total=0;
  for(let i=0;i<parts.length;i++){
    const p=parts[i];
    const last=i===parts.length-1;
    if(last?!/^\d+(\.\d+)?$/.test(p):!/^\d+$/.test(p))return null;
    total=total*60+parseFloat(p);
  }
  return total;
}

// Секунды → «h:mm:ss.s» (от часа) или «mm:ss.s». Десятые — округлением.
function podFmtTime(sec){
  let x=Math.max(0,Math.round((Number(sec)||0)*10));
  const tenth=x%10;
  x=(x-tenth)/10;
  const s=x%60, m=Math.floor(x/60)%60, h=Math.floor(x/3600);
  const ss=String(s).padStart(2,'0')+'.'+tenth;
  return h>0?h+':'+String(m).padStart(2,'0')+':'+ss:String(m).padStart(2,'0')+':'+ss;
}

// Границы, в которых кусок i может стоять: соседей не трогаем, за края записи не выходим.
// Левый сосед — тот, что кончается не позже начала куска; правый — начинается не раньше конца.
function podBounds(segs,i,dur){
  const me=segs[i];
  let lo=0, hi=dur>0?dur:Infinity;
  segs.forEach((o,j)=>{
    if(j===i||!o)return;
    if(Number(o.end)<=Number(me.start)+1e-9)lo=Math.max(lo,Number(o.end));
    else if(Number(o.start)>=Number(me.end)-1e-9)hi=Math.min(hi,Number(o.start));
  });
  return {lo:lo,hi:hi};
}

// Новые границы куска i (start, end) с учётом соседей, краёв записи и минимальной длины.
// Сдвинулся один край (тот, что отличается от segs[i]): при длине < minLen он упирается в
// minLen от другого края — кусок не сжимается. Если и так места нет — откат к прежнему куску.
// ПОЧЕМУ так: наезд на соседа режем по его краю, а не отменяем всё движение целиком.
function podClampSeg(segs,i,start,end,dur,minLen){
  const ml=minLen==null?POD_MIN_LEN:minLen;
  const me=segs[i];
  if(!me)return {start:0,end:0};
  let s=Number(start), e=Number(end);
  if(!isFinite(s)||!isFinite(e))return {start:Number(me.start),end:Number(me.end)};
  const b=podBounds(segs,i,dur);
  if(s!==Number(me.start))s=Math.min(s,e-ml);
  else e=Math.max(e,s+ml);
  s=Math.max(s,b.lo);
  e=Math.min(e,b.hi);
  if(e-s<ml-1e-9)return {start:Number(me.start),end:Number(me.end)};
  return {start:s,end:e};
}

// Прилипание к словам: start → начало ближайшего слова, end → конец. Дальше maxDist от всех
// слов — не прилипает, возвращается t как есть.
function podSnap(tm,words,kind,maxDist){
  const md=maxDist==null?POD_SNAP_DIST:maxDist;
  let best=null, bd=Infinity;
  (words||[]).forEach(w=>{
    const v=Number(kind==='end'?w.end:w.start);
    if(!isFinite(v))return;
    const d=Math.abs(v-tm);
    if(d<bd){bd=d;best=v;}
  });
  return best!==null&&bd<=md?best:tm;
}

// Разрез куска под плейхедом t (C). Точка прилипает к концу ближайшего слова в пределах 1 с:
// слово целиком остаётся слева. Оба куска — не короче POD_MIN_LEN. Вернёт {i, a, b}: индекс
// разрезаемого куска и границы левого (a) и правого (b) кусков. Нет куска под t или хоть один
// кусок вышел бы короче минимума — null.
// ПОЧЕМУ проверка после прилипания: слово, притянутое к краю, может съесть минимум.
function podSplitAt(segs,at,words){
  const tm=Number(at);
  const i=(segs||[]).findIndex(s=>Number(s.start)<tm&&tm<Number(s.end));
  if(i<0)return null;
  const me=segs[i];
  const c=podSnap(tm,words,'end');
  const a={start:Number(me.start),end:c}, b={start:c,end:Number(me.end)};
  if(a.end-a.start<POD_MIN_LEN-1e-9||b.end-b.start<POD_MIN_LEN-1e-9)return null;
  return {i:i,a:a,b:b};
}

// Место под новый кусок от двойного клика в t: от ближайшего конца соседа слева до ближайшего
// начала соседа справа, но не дальше POD_GAP_MAX/2 от точки клика — «вернуть вырезанное» и
// обрезать как угодно. Клик внутри куска — null; место короче POD_MIN_LEN — null.
function podGapAt(segs,at,dur){
  const tm=Number(at);
  let lo=0, hi=dur>0?dur:Infinity;
  for(const s of segs||[]){
    const a=Number(s.start), b=Number(s.end);
    if(a<tm&&tm<b)return null;
    if(b<=tm)lo=Math.max(lo,b);
    else if(a>=tm)hi=Math.min(hi,a);
  }
  const start=Math.max(lo,tm-POD_GAP_MAX/2), end=Math.min(hi,tm+POD_GAP_MAX/2);
  if(end-start<POD_MIN_LEN-1e-9)return null;
  return {start:start,end:end};
}

// Раскладка баблов слов на «Вся запись» (всё в css px, без DOM). Бабл встаёт в начало своего
// слова: x = начало слова; ширина = время слова − POD_BUBBLE_GAP, если времени хватает на текст
// с зазором, иначе текст + 2·поле (зазор до следующего тогда не выдерживается — слово пропустится). Жадно слева
// направо: слово рисуется, если его x не ближе POD_BUBBLE_GAP к правому краю последнего
// нарисованного бабла, и если бабл не вылезает за края ленты (0..widthPx). Иначе слово
// пропускается — соседи не сдвигаются, наезжающих баблов не бывает.
// measure(text) → ширина текста, css px. Возвращает [{x,w,text,start,end}] по возрастанию x.
function podWordBubbles(words,v0,v1,widthPx,measure){
  const span=Number(v1)-Number(v0);
  if(!(span>0)||!(widthPx>0))return [];
  const vis=(words||[]).filter(wd=>{
    const s=Number(wd.start), e=Number(wd.end);
    return Number.isFinite(s)&&Number.isFinite(e)&&e>s&&e>v0&&s<v1;
  }).sort((a,b)=>Number(a.start)-Number(b.start));
  const xOf=tm=>(Number(tm)-v0)/span*widthPx;
  const out=[];
  let last=-Infinity;                       // правый край последнего нарисованного бабла
  for(const wd of vis){
    const text=String(wd.w||'');
    const x0=xOf(wd.start), win=xOf(wd.end)-x0;
    // Растянутый бабл (речь без пауз) занимает время слова минус зазор: иначе он доходит до начала
    // следующего слова, и тот выпадает из-за зазора. Если времени впритык — бабл по тексту.
    const tw=Number(measure(text))+2*POD_BUBBLE_PAD;
    const w=win>=tw+POD_BUBBLE_GAP ? win-POD_BUBBLE_GAP : tw;
    if(x0<0||x0+w>widthPx+1e-9)continue;                       // за край ленты
    if(x0<last+POD_BUBBLE_GAP-1e-9)continue;                   // наезд на последний нарисованный
    out.push({x:x0,w:w,text:text,start:Number(wd.start),end:Number(wd.end)});
    last=x0+w;
  }
  return out;
}

// Цвет из CSS-переменной (правила стиля — в app.css :root); нет переменной — запасной.
function podCssVar(name,fb){
  try{
    const v=getComputedStyle(document.documentElement).getPropertyValue(name);
    return String(v||'').trim()||fb;
  }catch(e){return fb;}
}

// Прямоугольник со скруглением (радиус r) — путь вручную: roundRect есть не везде.
function podRoundRect(g,x,y,w,h,r){
  r=Math.min(r,w/2,h/2);
  g.beginPath();
  g.moveTo(x+r,y);g.arcTo(x+w,y,x+w,y+h,r);g.arcTo(x+w,y+h,x,y+h,r);
  g.arcTo(x,y+h,x,y,r);g.arcTo(x,y,x+w,y,r);g.closePath();
}

// --- «Вся запись»: окно [v0,v1] в секундах; w — ширина в любых единицах (css или device px) ---

// Секунда → координата в окне шириной w
function podS2X(s,v0,v1,w){return (s-v0)/(v1-v0)*w;}
// Координата → секунда (обратное к podS2X)
function podX2S(x,v0,v1,w){return v0+x/w*(v1-v0);}

// Окно в пределах записи: длина от POD_VIEW_MIN (или всей записи, если она короче) до dur,
// сдвиг — чтобы не выйти за 0 и за dur. Запись без длины (dur 0): окно как есть.
function podClampView(v0,v1,dur){return tlClampView(v0,v1,dur,POD_VIEW_MIN);}

// Масштаб окна: f>1 — ближе, f<1 — дальше. atF — доля ширины окна, где курсор (null — центр).
// ПОЧЕМУ так: точка под курсором остаётся на месте, иначе колесо уводит запись из-под пальца.
function podZoomSpan(v0,v1,f,atF,dur){return tlZoomSpan(v0,v1,f,atF,dur,POD_VIEW_MIN);}

// Пан окна средней кнопкой: сдвиг на dxPx пикселей ширины widthPx, т.е. на dx·(v1−v0)/width
// секунд (dx > 0 — окно вправо, к концу записи). Упор в начало и конец записи, длина окна
// та же. Вызывающий передаёт x0 − clientX: тянут курсор вправо — окно уходит назад во времени.
function podPanBy(v0,v1,dxPx,widthPx,dur){
  const w=Number(widthPx)>0?Number(widthPx):1;
  const ds=(Number(dxPx)||0)*(Number(v1)-Number(v0))/w;
  const r=podClampView(Number(v0)+ds,Number(v1)+ds,dur);
  return [r.v0,r.v1];
}

// Выбор кусков. picked — массив id выбранных; segs — куски (нужны start для диапазона);
// mode: 'one' — только id; 'toggle' — добавить или убрать; 'range' — от active до id по порядку start.
// ПОЧЕМУ сортировка внутри: диапазон — по времени, а не по порядку в массиве (он бывает любым).
function podPick(picked,segs,id,mode,active){
  const cur=Array.isArray(picked)?picked.slice():[];
  if(mode==='toggle'){
    const k=cur.indexOf(id);
    if(k>=0)cur.splice(k,1);else cur.push(id);
    return cur;
  }
  if(mode==='range'){
    const arr=(segs||[]).slice().sort((a,b)=>Number(a.start)-Number(b.start));
    const a=arr.findIndex(s=>s.id===active), b=arr.findIndex(s=>s.id===id);
    if(a<0||b<0)return [id];
    const lo=Math.min(a,b), hi=Math.max(a,b);
    return arr.slice(lo,hi+1).map(s=>s.id);
  }
  return [id];
}

// --- состояние окна ---------------------------------------------------------------

// raf — кадр слежения окна за плейхедом во время игры.
// v0/v1 — видимое окно «Вся запись», сек (слова ездят с ним); picked — выбранные куски (id); peaks — пики волны.
let POD={dir:'',title:'',open:false,dur:0,words:[],segs:[],sel:'',dirty:false,
  stopAt:null,pollN:0,drag:null,hooked:false,raf:0,
  v0:0,v1:1,picked:[],peaks:[],peaksPps:POD_PEAKS_PPS,
  saveT:0,saveChain:Promise.resolve(true)};  // таймер автосохранения и цепочка запросов (по одному за раз)
// История правок кусков: снимки podSegsState() в общей механике histStack (00-core.js)
const POD_HIST=histStack({max:POD_HIST_MAX,snapshot:()=>podSegsState(),apply:s=>{
  POD.segs=JSON.parse(s).map(x=>Object.assign({},x));podSortSegs();
  if(podSegIdx(POD.sel)<0)POD.sel=POD.segs.length?POD.segs[0].id:'';
  POD.dirty=true;                  // откат — тоже правка: уйдёт на сервер таймером
  podScheduleSave();podRefresh();},onUi:podUndoUI});

function podId(){
  let id;
  do{id=Math.floor(Math.random()*0x1000000).toString(16).padStart(6,'0');}
  while(POD.segs.some(s=>s.id===id));
  return id;
}
function podSegIdx(id){return POD.segs.findIndex(s=>s.id===id);}
function podSortSegs(){POD.segs.sort((a,b)=>Number(a.start)-Number(b.start));}
function podVideo(){return $('pod_video');}
function podNow(){const v=podVideo();return v?(Number(v.currentTime)||0):0;}
function podSpan(){return POD.dur>0?POD.dur:1;}

// Перерисовка всего, что зависит от кусков. Вызывается после любой правки.
// Выбор чистим первым: удалённый или перезаписанный сервером кусок из выбора уходит.
function podRefresh(){
  POD.picked=POD.picked.filter(id=>podSegIdx(id)>=0);
  podRenderSegs();podRenderTl();podRenderTime();podRenderPick();
}

// Панель кусков: число выбранных — на кнопках «в рилсы» и «удалить» (полный текст — в aria-label).
// Без выбора «в рилсы» берёт все куски — в заголовке окна видно, сколько уйдёт.
function podRenderPick(){
  const n=POD.picked.length, m=POD.segs.length;
  const cn=$('pod_conv_n');if(cn)cn.textContent=String(n);
  const dn=$('pod_del_n');if(dn)dn.textContent=String(n);
  const dp=$('pod_del_picked');if(dp)dp.disabled=!n;
  const cp=$('pod_conv_picked');if(cp)cp.disabled=!n;
  const lb=$('pod_conv_lbl');if(lb)lb.textContent=t('Перевести в рилсы')+' ('+(n||m)+')';
  podUndoUI();
}

// --- список кусков ----------------------------------------------------------------


// Поле времени: ввод понимается в любом из форматов podParseTime; сдвиг границ — через podClampSeg
function podTimeInput(sg,kind){
  const inp=document.createElement('input');
  inp.type='text';inp.className='pod-time mono';
  inp.value=podFmtTime(sg[kind]);
  inp.setAttribute('aria-label',kind==='start'?t('Начало куска'):t('Конец куска'));
  inp.onchange=()=>{
    const v=podParseTime(inp.value);
    const i=podSegIdx(sg.id);
    if(v===null||i<0){toast(t('Не понял время: пиши мм:сс.с, ч:мм:сс или секунды'));inp.value=podFmtTime(sg[kind]);return;}
    const r=podClampSeg(POD.segs,i,kind==='start'?v:sg.start,kind==='end'?v:sg.end,POD.dur);
    if(Math.abs(r.start-(kind==='start'?v:r.start))>1e-6||Math.abs(r.end-(kind==='end'?v:r.end))>1e-6)
      toast(t('Границу подвинул сосед или минимум {n} с',{n:POD_MIN_LEN}));
    sg.start=r.start;sg.end=r.end;podEdited();podRefresh();
  };
  inp.onclick=(e)=>e.stopPropagation();
  return inp;
}

function podRenderSegs(){
  const box=$('pod_segs');if(!box)return;
  box.innerHTML='';
  if(!POD.segs.length){
    const e=document.createElement('div');e.className='muted';
    e.textContent=t('Кусков нет — поставь «+ Кусок» от плейхеда');
    box.appendChild(e);return;
  }
  POD.segs.forEach((sg,i)=>{
    const on=POD.picked.includes(sg.id), act=sg.id===POD.sel;
    const row=document.createElement('div');
    row.className='podseg edcard'+((on||act)?' sel':'');
    row.onclick=(ev)=>podPickApply(sg.id,podModeOf(ev),POD.sel);
    // галочка — тот же выбор, что Ctrl+клик по строке
    const cb=document.createElement('input');cb.type='checkbox';cb.className='podseg-cb';
    cb.checked=on;cb.setAttribute('aria-label',t('Выбрать кусок'));
    cb.onclick=(e)=>{e.stopPropagation();podPickApply(sg.id,'toggle',POD.sel);};
    const n=document.createElement('span');n.className='podseg-n mono muted';n.textContent=String(i+1);
    const title=document.createElement('input');
    title.type='text';title.className='podseg-title';title.value=sg.title||'';
    title.title=title.value;// полное название в подсказке: поле узкое, при обрезке видно целиком
    title.placeholder=t('Название куска');title.setAttribute('aria-label',t('Название куска'));
    title.onclick=(e)=>e.stopPropagation();
    title.oninput=()=>{title.title=title.value;};
    title.onchange=()=>{sg.title=title.value.slice(0,160);podEdited();};
    const dur=document.createElement('span');dur.className='podseg-dur mono muted';
    dur.textContent=podFmtTime(sg.end-sg.start);
    // одна строка: ☐ № название начало конец длительность ▶ 🗑 (без переноса на вторую строку)
    row.append(cb,n,title,podTimeInput(sg,'start'),podTimeInput(sg,'end'),dur,
      iconBtn('play',t('Играть кусок'),()=>podPlaySeg(sg.id),{stop:true,title:true}),
      iconBtn('trash',t('Удалить кусок'),()=>podDelete(sg.id),{stop:true,title:true}));
    box.appendChild(row);
  });
}

// Клик по строке/блоку: mode — из клавиш (Shift — диапазон, Ctrl/⌘ — добавить или убрать)
function podModeOf(e){return e.shiftKey?'range':((e.ctrlKey||e.metaKey)?'toggle':'one');}

// Выбор кусков. Активный (POD.sel) — то, что правят I/O и крупная полоса слов. Диапазон
// от активного его не двигает: иначе Shift+клик съедал бы якорь.
function podPickApply(id,mode,active){
  POD.picked=podPick(POD.picked,POD.segs,id,mode,active);
  if(mode!=='range')POD.sel=id;
  podRefresh();
}

// Без подтверждения: удаление откатывается ↶ (Ctrl+Z), как у вставок
function podDeletePicked(){
  const ids=POD.picked.filter(id=>podSegIdx(id)>=0);
  if(!ids.length)return;
  POD.segs=POD.segs.filter(s=>ids.indexOf(s.id)<0);
  POD.picked=[];
  if(podSegIdx(POD.sel)<0)POD.sel=POD.segs.length?POD.segs[0].id:'';
  podEdited();
  podRefresh();
}

// --- таймлайн всей записи (canvas) ------------------------------------------------

function podTlCanvas(){return $('pod_tl_cv');}

// Рисование «Вся запись» — как edDraw у шага 1: волна, рамки, линейка, плейхед.
// Пики — максимум по пикселю (на отдалении один пиксель = десятки секунд, точечная выборка
// пропускала бы громкие места).
function podRenderTl(){
  const cv=podTlCanvas();if(!cv)return;
  const C=TL_COL, dpr=devicePixelRatio||1;
  const W=Math.max(1,Math.round(cv.clientWidth*dpr)), H=Math.max(1,Math.round(cv.clientHeight*dpr));
  if(cv.width!==W)cv.width=W;
  if(cv.height!==H)cv.height=H;
  const g=cv.getContext('2d');
  const RH=Math.round(POD_TL_RULER*dpr);
  const RT=RH+Math.round(POD_TL_RIBBON_GAP*dpr), RBH=Math.round(POD_TL_RIBBON*dpr);   // лента слов над волной
  const WT=RT+RBH+Math.round(POD_TL_RIBBON_GAP*dpr);                                    // волна — ниже ленты, остаток высоты
  const v0=POD.v0, v1=POD.v1, segs=POD.segs;
  g.fillStyle=C.bg;g.fillRect(0,0,W,H);
  if(!(v1>v0))return;
  // волна: внутри куска — зелёным, у выбранных — синим, вне кусков — серым
  const pk=POD.peaks||[], pps=POD.peaksPps||POD_PEAKS_PPS;
  // общая волна (tlPaintWave, 00-core.js): максимум по пикселю, цвета — по кускам
  let bi=0;
  tlPaintWave(g,{W:W,H:H,top:WT,dpr:dpr,v0:v0,v1:v1,dur:POD.dur,peaks:pk,pps:pps,
    colorAt:s=>{
      while(bi<segs.length&&s>=segs[bi].end)bi++;
      const inb=bi<segs.length&&s>=segs[bi].start;
      return inb?(POD.picked.includes(segs[bi].id)?C.waveSel:C.wave):C.waveOff;}});
  // рамки кусков: выбранные — синим, активный без выбора — светлой рамкой
  segs.forEach((sg,i)=>{
    const x0=podS2X(sg.start,v0,v1,W), x1=podS2X(sg.end,v0,v1,W);
    if(x1<0||x0>W)return;
    const on=POD.picked.includes(sg.id), edge=on?C.selEdge:C.blkEdge;
    tlDrawSpan(g,x0,x1,WT,H,dpr,{on:on,edge:edge,active:sg.id===POD.sel});
    if(x1-x0>14*dpr){g.font=(10*dpr)+'px ui-monospace,monospace';g.textBaseline='top';
      g.fillStyle=edge;g.fillText(String(i+1),x0+4*dpr,H-14*dpr);}
  });
  // лента слов — общая полоса над волной (#101010, рамка --bd2, скругление --r-sm), слова — баблы
  // как фишки шага 1 внутри неё. Бабл встаёт в своё время того же окна, что и волна; слова, которые
  // не влезают без наезда, не рисуются (podWordBubbles). Слово под плейхедом — с синей рамкой.
  const cs=podCssVar('--bd2','#2e2e2e'), tx=podCssVar('--tx','#f3f3f3');
  const rr=4*dpr, LX=Math.round(POD_TL_RIBBON_X*dpr);
  g.fillStyle=C.ribbon;podRoundRect(g,LX,RT,W-2*LX,RBH,rr);g.fill();
  g.strokeStyle=cs;g.lineWidth=dpr;podRoundRect(g,LX+dpr/2,RT+dpr/2,W-2*LX-dpr,RBH-dpr,rr);g.stroke();
  const fontPx=(11*dpr)+'px system-ui,sans-serif';
  const measure=txt=>{g.font=fontPx;return g.measureText(txt).width/dpr;};
  const now=podNow(), BY=RT+3*dpr, BH=RBH-6*dpr;   // бабл — 16 css px внутри ленты
  podWordBubbles(POD.words||[],v0,v1,W/dpr,measure).forEach(b=>{
    const x=b.x*dpr, bw=b.w*dpr;
    if(x+bw<0||x>W)return;
    const cur=now>=b.start&&now<b.end;
    g.fillStyle=C.bubble;podRoundRect(g,x,BY,bw,BH,rr);g.fill();
    g.strokeStyle=cur?C.ph:cs;g.lineWidth=dpr;
    podRoundRect(g,x+dpr/2,BY+dpr/2,bw-dpr,BH-dpr,rr);g.stroke();
    g.font=fontPx;g.fillStyle=tx;g.textBaseline='middle';
    g.fillText(b.text,x+POD_BUBBLE_PAD*dpr,BY+BH/2);
  });
  // линейка — общая tlPaintRuler (00-core.js)
  tlPaintRuler(g,{W:W,RH:RH,dpr:dpr,v0:v0,v1:v1,total:podSpan()});
  // плейхед — последним, поверх блоков: синий с тёмной обводкой (tlDrawPlayhead, 00-core.js)
  const ph=podS2X(podNow(),v0,v1,W);
  if(ph>=-1&&ph<=W+1)tlDrawPlayhead(g,ph,H,dpr);
}

// Пики волны всей записи (раз на открытие). Файла нет или ошибка — без волны, молча.
async function podLoadPeaks(dir){
  const p=String(dir||'').replace(/[\\\/]+$/,'')+'/'+POD_WAV;
  let r;
  try{r=await (await fetch('/api/waveform?pps='+POD_PEAKS_PPS+'&path='+encodeURIComponent(p))).json();}
  catch(e){return;}
  if(!POD.open||POD.dir!==dir||!r||!r.ok||!Array.isArray(r.peaks))return;
  POD.peaks=r.peaks;POD.peaksPps=Number(r.pps)||POD_PEAKS_PPS;
  podRenderTl();
}

// Окно «Вся запись»: зум, прокрутка, «вся запись»
function podZoomFit(){POD.v0=0;POD.v1=podSpan();podRenderTl();}
function podPan(ds){const r=podClampView(POD.v0+ds,POD.v1+ds,POD.dur);POD.v0=r.v0;POD.v1=r.v1;}
// Масштаб к точке atX (css px от левого края таймлайна): время под точкой не уезжает
function podZoom(f,atX){
  const cv=podTlCanvas();if(!cv||!POD.dur)return;
  const w=cv.getBoundingClientRect().width||1;
  const r=podZoomSpan(POD.v0,POD.v1,f,atX==null?null:atX/w,POD.dur);
  POD.v0=r.v0;POD.v1=r.v1;podRenderTl();
}
// Колесо над «Вся запись»: зум к курсору, Shift или горизонтальное колесо — прокрутка окна (tlWheelBind)
function podWheelBind(tl){
  tlWheelBind(tl,{
    pan:dx=>{if(!POD.dur)return;const cv=podTlCanvas();if(!cv)return;
      const w=cv.getBoundingClientRect().width||1;
      podPan(dx/w*(POD.v1-POD.v0));podRenderTl();},
    zoom:(f,x)=>{if(!POD.dur)return;podZoom(f,x);}});
}
// Явный переход к куску («Играть кусок») показывает его: окно встаёт на t, если t вне окна (tlReveal).
// Игра, перемотка и протяжка по самому «Вся запись» окно не двигают — решение владельца 2026-10-12.
// Протяжка окна (средняя кнопка, линейка) идёт своим путём — её не перебиваем.
function podRevealAt(tm){
  if(tlPanning($('pod_tl')))return;
  const r=tlReveal(POD.v0,POD.v1,tm,POD.dur,POD_VIEW_MIN);
  POD.v0=r[0];POD.v1=r[1];
}

// Попадание в блок «Вся запись»: край (l/r) или середина (m); css px, рамка HANDLE
function podTlHit(clientX){
  const cv=podTlCanvas();if(!cv)return null;
  const r=cv.getBoundingClientRect(), w=r.width||1, x=clientX-r.left;
  const c=tlHitOrder(podTlSegCands(x,w),{edge:POD_TL_HANDLE});
  return c?{id:c.id,kind:c.side}:null;
}
// Кандидаты по горизонтали: края (l/r) и середина (m) каждого куска. Порядок выбора — tlHitOrder (00-core.js).
function podTlSegCands(x,w){
  const out=[];
  for(const sg of POD.segs){
    const x0=podS2X(sg.start,POD.v0,POD.v1,w), x1=podS2X(sg.end,POD.v0,POD.v1,w);
    out.push({kind:'edge',dist:Math.abs(x-x0),id:sg.id,side:'l'});
    out.push({kind:'edge',dist:Math.abs(x-x1),id:sg.id,side:'r'});
    if(x>=x0&&x<=x1)out.push({kind:'mid',dist:0,id:sg.id,side:'m'});
  }
  return out;
}

function podRenderTime(){
  const tm=$('pod_time');if(!tm)return;
  tm.textContent=podFmtTime(podNow())+' / '+podFmtTime(POD.dur||podNow());
}

// Экранная точка → время на «Вся запись» (css px от левого края холста).
function podT(clientX){
  const r=podTlCanvas().getBoundingClientRect();
  const x=Math.min(r.width,Math.max(0,clientX-r.left));
  return podX2S(x,POD.v0,POD.v1,r.width||1);
}

// Что под курсором на «Вся запись»: край или середина куска ({id,kind}), плейхед ('ph'),
// линейка ('ruler') или пусто (null). Порядок — общий с редактором шага 1 (tlHitOrder, 00-core.js):
// край > плейхед > линейка > середина. Плейхед важнее середины: иначе его не схватить там, где он
// стоит внутри куска (а он почти всегда там стоит при игре).
function podTlWhere(clientX,clientY){
  const cv=podTlCanvas();if(!cv)return null;
  const r=cv.getBoundingClientRect(), w=r.width||1, x=clientX-r.left, y=clientY-r.top;
  const cands=podTlSegCands(x,w);
  cands.push({kind:'head',dist:Math.abs(x-podS2X(podNow(),POD.v0,POD.v1,w))});
  if(y<=POD_TL_RULER)cands.push({kind:'ruler',dist:0});
  const c=tlHitOrder(cands,{edge:POD_TL_HANDLE,head:POD_TL_PH});
  if(!c)return null;
  if(c.kind==='head')return {id:null,kind:'ph'};
  if(c.kind==='ruler')return {id:null,kind:'ruler'};
  return {id:c.id,kind:c.side};
}

// Нажатие на «Вся запись». Край блока — тянуть границу, середина — сдвиг (как раньше).
// Плейхед и пустое место — перемотка (линейка — протяжка вида, см. podMidDown): currentTime идёт за курсором, пока кнопка
// зажата, без ожидания отпускания. Игра при этом НЕ останавливается: тяга только переставляет
// позицию (как перемотка в редакторе шага 1). Пустое место окно не двигает: окно — средняя кнопка.
function podDownTl(e){
  const wh=podTlWhere(e.clientX,e.clientY);
  // Линейка — протяжка вида, а не перемотка (как у редактора шага 1): окно едет за курсором
  if(wh&&wh.kind==='ruler'){podMid(e);e.preventDefault();return;}
  if(!wh||wh.kind==='ph'){
    POD.drag={id:null,kind:'seek',t0:podT(e.clientX),moved:false,orig:null};
    podSeek(POD.drag.t0);
  }else{
    const i=podSegIdx(wh.id);if(i<0)return;
    const sg=POD.segs[i];
    POD.drag={id:wh.id,kind:wh.kind,t0:podT(e.clientX),moved:false,mode:podModeOf(e),prevSel:POD.sel,
      orig:{start:sg.start,end:sg.end}};
  }
  document.addEventListener('pointermove',podMove);
  document.addEventListener('pointerup',podUp);
  e.preventDefault();
}

// Средняя кнопка и линейка: протяжка окна — общая tlMidPan (00-core.js). Окно едет за курсором
// ровно на пройденное расстояние; курсор захвачен, поэтому тяга не теряет окно за краем холста.
// Линейка и средняя кнопка. Окно на всю запись тоже идёт сюда: протяжка там безвредна (окно не
// сдвигается), а клик по линейке должен ставить плейхед и при полном окне.
function podMid(e){
  if(!POD.dur)return;
  const el=$('pod_tl');if(!el)return;
  tlMidPan(el,e,{snap:()=>({v0:POD.v0,v1:POD.v1}),apply:(dx,s)=>{
    const cv=podTlCanvas();if(!cv)return;
    const w=cv.getBoundingClientRect().width||1;
    const r=podPanBy(s.v0,s.v1,-dx,w,POD.dur);     // podPanBy: dx > 0 — окно вправо, тянем вправо — окно назад
    POD.v0=r[0];POD.v1=r[1];podRenderTl();},
    click:cx=>podSeek(podT(cx))});                 // клик по линейке — плейхед в точку клика, окно стоит
}
// Курсор над плейхедом — ew-resize: за него тянут (см. podDownTl)
function podTlHover(e){
  if(POD.drag||tlPanning($('pod_tl')))return;
  const tl=$('pod_tl');if(!tl)return;
  const wh=podTlWhere(e.clientX,e.clientY);
  tl.style.cursor=(wh&&wh.kind==='ph')?'ew-resize':(wh&&wh.kind==='ruler')?'grab':'';
}

// Двойной клик по пустому месту «Вся запись» — новый кусок там (так «возвращается вырезанное»).
// На куске и на линейке — ничего: там свои действия. Плейхед сюда не считаем: первый клик
// уже перевёл его под курсор, и двойной клик не должен из-за этого пропадать.
function podDblTl(e){
  if(!POD.dur)return;
  const cv=podTlCanvas();if(!cv)return;
  if(podTlHit(e.clientX))return;
  if(e.clientY-cv.getBoundingClientRect().top<=POD_TL_RULER)return;
  const g=podGapAt(POD.segs,podT(e.clientX),POD.dur);
  if(!g){toast(t('Свободно меньше {n} с — некуда ставить кусок',{n:POD_MIN_LEN}));return;}
  const sg={id:podId(),start:g.start,end:g.end,title:'',why:'',reel:''};
  POD.segs.push(sg);podSortSegs();
  POD.sel=sg.id;POD.picked=[sg.id];
  podEdited();podRefresh();
}

// Копия массива, где кусок i стоит на исходных границах: от них считаются соседи и минимум
function podArrOrig(i,orig){
  const arr=POD.segs.slice();
  arr[i]=Object.assign({},POD.segs[i],{start:orig.start,end:orig.end});
  return arr;
}

function podMove(e){
  const d=POD.drag;if(!d)return;
  if(d.kind==='seek'){
    // Перемотка тягой по «Вся запись»: плейхед — под курсором, без ожидания отпускания
    d.moved=true;
    podSeek(podT(e.clientX));
    return;
  }
  const i=podSegIdx(d.id);if(i<0)return;
  const tm=podT(e.clientX);
  const o=d.orig, len=o.end-o.start;
  const arr=podArrOrig(i,o);
  let r;
  if(d.kind==='m'){
    const b=podBounds(arr,i,POD.dur);
    let ns=o.start+(tm-d.t0);
    ns=Math.max(b.lo,Math.min(ns,b.hi-len));
    r={start:ns,end:ns+len};
  }else if(d.kind==='l'){
    r=podClampSeg(arr,i,tm,o.end,POD.dur);
  }else{
    r=podClampSeg(arr,i,o.start,tm,POD.dur);
  }
  const sg=POD.segs[i];
  if(Math.abs(sg.start-r.start)>1e-9||Math.abs(sg.end-r.end)>1e-9){
    sg.start=r.start;sg.end=r.end;d.moved=true;
    podRefresh();
  }
}

function podUp(){
  document.removeEventListener('pointermove',podMove);
  document.removeEventListener('pointerup',podUp);
  const d=POD.drag;POD.drag=null;
  if(!d)return;
  if(d.kind==='seek'){
    // игра не прерывалась: тяга только переставила позицию
    podSyncPlayBtn();podRefresh();
    return;
  }
  const i=podSegIdx(d.id);
  if(i>=0&&d.moved){
    // Прилипание: граница при отпускании уходит к краю ближайшего слова в пределах 1 с
    // (как было у полосы слов). Середина куска — сдвиг, её не прилипаем.
    if(d.kind!=='m'){
      const sg=POD.segs[i];
      const arr=podArrOrig(i,d.orig);
      let s=sg.start, en=sg.end;
      if(d.kind==='l')s=podSnap(s,POD.words,'start');
      else en=podSnap(en,POD.words,'end');
      const r=podClampSeg(arr,i,s,en,POD.dur);
      sg.start=r.start;sg.end=r.end;
    }
    podEdited();
    // тянул блок — он и активный; без выбора он же и выбранный
    POD.sel=d.id;
    if(!POD.picked.includes(d.id))POD.picked=[d.id];
  }else if(!d.moved){
    if(i>=0)podPickApply(d.id,d.mode,d.prevSel);   // клик по блоку — выбор по клавишам и перемотка
    podSeek(d.t0);                                  // клик по пустому месту — перемотка
  }
  podRefresh();
}

// --- плеер ------------------------------------------------------------------------

function podSyncPlayBtn(){
  const b=$('pod_play'),v=podVideo();
  if(b&&v)b.innerHTML=ico(v.paused?'play':'pause');
}

function podPlayPause(){
  const v=podVideo();if(!v)return;
  POD.stopAt=null;
  if(v.paused)v.play().catch(()=>{});else v.pause();
  podSyncPlayBtn();
}

function podSeek(tm){
  const v=podVideo();if(!v)return;
  POD.stopAt=null;
  const max=POD.dur>0?POD.dur:tm;
  v.currentTime=Math.max(0,Math.min(tm,max));
  podRenderTime();podRenderTl();
}

// «Играть кусок»: на начало, играть, остановиться на конце
function podPlaySeg(id){
  const i=podSegIdx(id);if(i<0)return;
  const sg=POD.segs[i];const v=podVideo();if(!v)return;
  POD.sel=id;
  v.currentTime=sg.start;
  POD.stopAt=sg.end;
  podRevealAt(sg.start);   // явный переход из списка: кусок виден, даже если окно ушло
  v.play().catch(()=>{});
  podSyncPlayBtn();podRenderSegs();podRenderTl();
}

function podOnTime(){
  const v=podVideo();if(!v)return;
  if(POD.stopAt!=null&&v.currentTime>=POD.stopAt){v.pause();POD.stopAt=null;}
  podRenderTime();podRenderTl();
}

// Во время игры плейхед перерисовывается по кадрам: timeupdate приходит ~4 раза в секунду,
// на 60 px/s это прыжки по 15 px — видно глазом. Окно при этом не едет (решение 2026-10-12);
// кадр только рисует плейхед на месте. Кадр останавливается сам при паузе.
function podFollow(){
  POD.raf=0;
  const v=podVideo();
  if(!POD.open||!v||v.paused)return;
  podRenderTl();
  POD.raf=requestAnimationFrame(podFollow);
}
function podFollowStart(){if(!POD.raf)POD.raf=requestAnimationFrame(podFollow);}

// Колесо над крупным таймлайном — масштаб окна, в пределах [MIN, MAX] полуокна.
// Вверх — ближе (окно уже), вниз — дальше. Страницу при этом не прокручиваем.
// Один раз на жизнь страницы: обработчики видео и холста «Вся запись»
function podHook(){
  if(POD.hooked)return;
  POD.hooked=true;
  const tl=$('pod_tl');
  // Средняя кнопка (колесо) — пан, левая — перемотка/тяга. Браузерный middle autoscroll
  // включается на mousedown и даёт о себе знать на auxclick — гасим оба (как в 70-editor.js).
  // Указатель ловим в pointerdown (podMid → tlMidPan), движение и отпускание — на самом элементе.
  if(tl){
    tl.addEventListener('pointerdown',e=>{if(e.button===1)podMid(e);else if(e.button===0)podDownTl(e);});
    tlMidGuard(tl);   // средняя кнопка: без автоскролла браузера
    tl.addEventListener('pointermove',podTlHover);   // протяжку ведёт tlMidPan на самом элементе
    podWheelBind(tl);
    tl.addEventListener('dblclick',podDblTl);
  }
  // холст перерисовывается по ширине окна (devicePixelRatio тоже меняется при переносе на другой монитор)
  window.addEventListener('resize',()=>{if(POD.open)podRenderTl();});
  // «Вся запись» растягивается по высоте окна (flex): холст перерисовывается по размеру своей рамки,
  // а не только при resize окна — иначе после растяжки остаётся старая высота холста
  if(tl&&window.ResizeObserver){new ResizeObserver(()=>{if(POD.open)podRenderTl();}).observe(tl);}
  const v=podVideo();if(!v)return;
  v.addEventListener('timeupdate',podOnTime);
  v.addEventListener('loadedmetadata',()=>{
    if(!POD.dur&&isFinite(v.duration)&&v.duration>0){
      POD.dur=v.duration;
      // длина пришла позже: окно «вся запись» пересчитываем, иначе осталось бы на 1 с
      POD.v0=0;POD.v1=podSpan();
    }
    podRefresh();
  });
  v.addEventListener('play',podFollowStart);
  ['play','pause','ended'].forEach(ev=>v.addEventListener(ev,podSyncPlayBtn));
}

// Тихая смена источника на прокси: тот же момент, та же пауза/игра
function podSwitchSrc(path){
  const v=podVideo();if(!v)return;
  const was=v.currentTime||0, playing=!v.paused;
  const onMeta=()=>{
    v.removeEventListener('loadedmetadata',onMeta);
    try{v.currentTime=Math.min(was,v.duration||was);}catch(e){}
    if(playing)v.play().catch(()=>{});
  };
  v.addEventListener('loadedmetadata',onMeta);
  v.src='/api/media?path='+encodeURIComponent(path);

}

// --- прокси: опрос раз в секунду, пока окно открыто и прокси не готов -------------

function podProxyPoll(){clearInterval(POD.pollN);POD.pollN=setInterval(podProxyTick,1000);}
function podProxyStop(){clearInterval(POD.pollN);POD.pollN=0;}

async function podProxyTick(){
  if(!POD.open){podProxyStop();return;}
  const dir=POD.dir;
  let s;try{s=await (await fetch('/api/podcast_proxy_status?dir='+encodeURIComponent(dir))).json();}
  catch(e){return;}
  if(!POD.open||POD.dir!==dir||!s.ok)return;
  // Строка прогресса — та же, что у превью шага 1 (pvProxyBlock в 60-preview.js): своей
  // разметки здесь нет, одна дверь на обе стойки.
  const stage=$('pod_stage');
  if(s.ready){podProxyStop();pvProgDrop(stage,'proxy');await podProxyUse(dir);return;}
  if(s.running){if(stage)pvProxyBlock(stage,{pct:s.pct==null?0:s.pct,i:1,n:1});return;}
  // сборка кончилась без готового прокси: окно играет исходник, строку снимаем, причину —
  // тостом целиком (в превью шага 1 ошибка сборки строкой не показывается)
  podProxyStop();
  pvProgDrop(stage,'proxy');
  if(s.error)toast('⚠ '+errText(s));
}

async function podProxyUse(dir){
  let r;
  try{r=await (await fetch('/api/podcast_proxy',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({dir:dir})})).json();}catch(e){return;}
  if(!POD.open||POD.dir!==dir)return;
  if(r.ok&&r.ready&&r.path){podSwitchSrc(r.path);pvProgDrop($('pod_stage'),'proxy');}
}

// --- открытие, закрытие, сохранение -----------------------------------------------

async function openPodcastEditor(dir){
  let d;
  try{d=await (await fetch('/api/podcast_get?dir='+encodeURIComponent(dir))).json();}
  catch(e){toast(t('Сервер не ответил: ')+e);return;}
  if(d.error){toast('⚠ '+errText(d));return;}
  // метка «последним открывал» в списке подкастов — как у клипов (markLastClip). typeof — LASTPOD живёт
  // в 45-podcast.js, стенды вырезают функции поодиночке
  if(typeof LASTPOD!=='undefined'){LASTPOD=dir;saveState();}
  if(typeof podcastList==='function')podcastList();
  const proj=d.project||{};
  POD.dir=dir;POD.title=proj.title||'';POD.open=true;POD.dirty=false;POD.drag=null;POD.stopAt=null;
  POD.words=d.words||[];
  POD.segs=(proj.segments||[]).map(s=>Object.assign({},s));
  podSortSegs();
  POD.sel=POD.segs.length?POD.segs[0].id:'';
  POD_HIST.reset();
  POD.dur=Number(proj.duration)||0;
  if(!POD.dur){
    POD.dur=POD.words.reduce((m,w)=>Math.max(m,Number(w.end)||0),0);
    POD.dur=POD.segs.reduce((m,s)=>Math.max(m,Number(s.end)||0),POD.dur);
  }
  POD.picked=[];POD.peaks=[];POD.peaksPps=POD_PEAKS_PPS;
  POD.v0=0;POD.v1=podSpan();
  podHook();
  const ttl=$('pod_title');if(ttl)ttl.textContent=POD.title||'—';
  pvProgDrop($('pod_stage'),'proxy');   // строка от прошлого открытия не должна висеть
  const v=podVideo();
  if(v){v.pause();v.src='/api/media?path='+encodeURIComponent(proj.source||'');}
  openModal('mbPod');
  podSyncPlayBtn();
  podRefresh();
  podProxyStart(dir);
  podLoadPeaks(dir);
}

// Прокси для браузера: готов — играем его, нет — собирается (опрос), иначе играем исходник
async function podProxyStart(dir){
  let r;
  try{r=await (await fetch('/api/podcast_proxy',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({dir:dir})})).json();}
  catch(e){return;}
  if(!POD.open||POD.dir!==dir)return;
  if(r.error){toast('⚠ '+errText(r));return;}
  if(r.ready&&r.path){podSwitchSrc(r.path);return;}
  if(r.building){const stage=$('pod_stage');if(stage)pvProxyBlock(stage,{pct:0,i:1,n:1});podProxyPoll();}
}

function podBackClose(e){if(e.target===$('mbPod')&&BACKDOWN===$('mbPod'))podClose();}

// Закрытие: правки уже уходят на сервер сами. Отложенное сохранение — сейчас, окно закрываем
// после ответа. Сервер не принял — окно остаётся открытым: иначе правки пропали бы (тост с
// причиной уже показан; ↶ откатывает к принятому состоянию, и следующая правка сохранит).
function podClose(){
  if(!POD.dirty){podDoClose();return;}
  const dir=POD.dir;
  podFlush().then(ok=>{if(ok&&POD.dir===dir)podDoClose();});
}

function podDoClose(){
  POD.open=false;POD.stopAt=null;POD.drag=null;
  podProxyStop();
  pvProgDrop($('pod_stage'),'proxy');   // окно закрыто — строка сборки уходит с ним
  const v=podVideo();if(v)v.pause();
  closeModal('mbPod');
}

// --- история правок кусков (как у вставок: ↶ ↷, Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y) ---------

// Состояние кусков: только поля, которые уходят на сервер. Одна строка — сравнение дешёвое.
function podSegsState(){
  return JSON.stringify(POD.segs.map(s=>({id:s.id,start:s.start,end:s.end,
    title:s.title||'',why:s.why||'',reel:s.reel||''})));
}

// Шаг истории пишет podEdited — по итогу правки или жеста, а не podRefresh: тяга границы
// идёт сотнями движений, а ↶ должна откатывать её целиком, одним шагом.
function podUndoUI(){
  const b=$('podUndoBtn');if(b)b.disabled=!POD_HIST.canUndo();
  const rb=$('podRedoBtn');if(rb)rb.disabled=!POD_HIST.canRedo();
}

function podUndo(){POD_HIST.undo();}
function podRedo(){POD_HIST.redo();}

// Любая правка кусков: шаг истории (если что-то изменилось) и отложенное сохранение
function podEdited(){
  POD.dirty=true;
  POD_HIST.touch();
  podScheduleSave();
}

// --- автосохранение: через POD_SAVE_MS после последней правки, как у вставок -------------

function podScheduleSave(){
  clearTimeout(POD.saveT);
  POD.saveT=setTimeout(()=>{POD.saveT=0;podSave().catch(()=>{});},POD_SAVE_MS);
}

// Немедленно: всё, что ещё ждёт таймера или ушло с ошибкой. true — всё на диске.
async function podFlush(){
  clearTimeout(POD.saveT);POD.saveT=0;
  while(POD.dirty){if(!(await podSave()))return false;}
  return true;
}

// Запросы — по одному: правка во время запроса не теряется, она уйдёт следующим
async function podSave(){
  const run=POD.saveChain.then(()=>podSaveOnce());
  POD.saveChain=run.catch(()=>false);
  return run;
}

// Один запрос с текущими кусками. Ответ сервера заменяет список в окне, только если за время
// запроса ничего не меняли: иначе правка, сделанная поверх, пропала бы.
async function podSaveOnce(){
  if(!POD.dirty)return true;
  const dir=POD.dir;
  const sent=podSegsState();
  const body={dir:dir,segments:POD.segs.map(s=>({id:s.id,start:s.start,end:s.end,
    title:s.title||'',why:s.why||'',reel:s.reel||''}))};
  let d;
  try{
    d=await (await fetch('/api/podcast_save',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body)})).json();
  }catch(e){toast(t('Сервер не ответил: ')+e);return false;}
  // ошибка сервера — текстом целиком; куски и правки в окне остаются, повтор — со следующей правкой
  if(d.error){toast('⚠ '+errText(d));return false;}
  if(podSegsState()!==sent)return true;   // правили во время запроса: таймер уже взвёлся и дошлёт
  POD.dirty=false;
  if(POD.open&&POD.dir===dir){
    POD.segs=(d.project.segments||[]).map(s=>Object.assign({},s));
    podSortSegs();
    if(podSegIdx(POD.sel)<0)POD.sel=POD.segs.length?POD.segs[0].id:'';
    POD_HIST.rebase();                    // нормализация сервера — не шаг истории
    podRefresh();
  }
  podcastList();   // карточка на шаге 1 — с новым числом кусков
  return true;
}

// «Перевести в рилсы»: выбранные куски, а без выбора — все. Несохранённое сохраняем первым
// (как podConvert в 45-podcast.js): рилсы строятся по сохранённым кускам. Сам джоб и опрос
// — те же, что у podConvert; ids уходят в /api/podcast_convert (П7).
async function podConvertPicked(){
  if(!(await podFlush()))return;
  if(PODRUNNING){toast(t('Подкаст уже обрабатывается — дождись конца'));return;}
  const ids=POD.picked.filter(id=>podSegIdx(id)>=0);
  // speaker — как у podConvert: без него рилсы уходят в запасную папку подкаста, а не в папку
  // камеры 1 спикера (владелец, 2026-10-11). Подсказку про запасную папку — той же podFallbackToast.
  let r;
  try{r=await (await fetch('/api/podcast_convert',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({dir:POD.dir,ids:ids,speaker:val('speaker')})})).json();}
  catch(e){toast(t('Сервер не ответил: ')+e);uiLog('podcast: '+e);return;}
  if(r.error){toast('⚠ '+errText(r));uiLog(t('подкаст: ')+errText(r));return;}
  podFallbackToast(r);
  PODLOGN=0;
  const st=$('podcast_status');if(st){st.className='muted mono';st.textContent=t('запуск…');}
  podcastPoll();
}

// Новый кусок: 60 с от плейхеда, урезанный до свободного места до следующего куска
function podAdd(){
  const v=podNow();
  if(POD.segs.some(s=>s.start<=v&&v<s.end)){
    toast(t('Здесь уже есть кусок — встань на свободное место'));return;
  }
  let freeEnd=POD.dur>0?POD.dur:Infinity;
  POD.segs.forEach(s=>{if(s.start>=v&&s.start<freeEnd)freeEnd=s.start;});
  if(freeEnd-v<POD_MIN_LEN){
    toast(t('Свободно меньше {n} с — некуда ставить кусок',{n:POD_MIN_LEN}));return;
  }
  const sg={id:podId(),start:v,end:Math.min(v+POD_NEW_LEN,freeEnd),title:'',why:'',reel:''};
  POD.segs.push(sg);
  podSortSegs();
  POD.sel=sg.id;
  podEdited();podRefresh();
}

// Без подтверждения — как у вставок: откат ↶ (Ctrl+Z)
function podDelete(id){
  const i=podSegIdx(id);if(i<0)return;
  POD.segs.splice(i,1);
  if(POD.sel===id)POD.sel=POD.segs.length?POD.segs[0].id:'';
  podEdited();
  podRefresh();
}

// C — разрезать кусок под плейхедом на два (прилипание к слову, оба — не короче минимума).
// Правая часть становится активной и единственной выбранной: как «Резать» в редакторе шага 1.
function podCut(){
  if(!POD.segs.length){toast(t('Кусков нет — поставь «+ Кусок» от плейхеда'));return;}
  const r=podSplitAt(POD.segs,podNow(),POD.words);
  if(!r){toast(t('Плейхед — внутрь куска; от каждого края — минимум {n} с',{n:POD_MIN_LEN}));return;}
  const old=POD.segs[r.i];
  const right={id:podId(),start:r.b.start,end:r.b.end,title:'',why:'',reel:''};
  POD.segs.splice(r.i,1,Object.assign({},old,{start:r.a.start,end:r.a.end}),right);
  POD.sel=right.id;POD.picked=[right.id];
  podEdited();podRefresh();
}

// D / Delete — удалить выбранные куски; без выбора — активный. Откат — ↶ (Ctrl+Z), без подтверждения.
function podDelKey(){
  if(POD.picked.some(id=>podSegIdx(id)>=0))podDeletePicked();
  else if(podSegIdx(POD.sel)>=0)podDelete(POD.sel);
  else toast(t('Сначала выбери кусок'));
}

// I/O: начало или конец выбранного куска = плейхед; с прилипанием и теми же проверками
function podSetEdge(kind){
  const i=podSegIdx(POD.sel);
  if(i<0){toast(t('Сначала выбери кусок'));return;}
  const sg=POD.segs[i];
  const orig={start:sg.start,end:sg.end};
  const tm=podNow();
  let s=sg.start, en=sg.end;
  if(kind==='start')s=podSnap(tm,POD.words,'start');
  else en=podSnap(tm,POD.words,'end');
  const r=podClampSeg(POD.segs,i,s,en,POD.dur);
  if(Math.abs(r.start-orig.start)<1e-9&&Math.abs(r.end-orig.end)<1e-9){
    toast(t('Граница не сдвинулась: мешает сосед или минимум {n} с',{n:POD_MIN_LEN}));return;
  }
  sg.start=r.start;sg.end=r.end;
  podEdited();podRefresh();
}

// Клавиши окна — только когда оно открыто и фокус не в поле ввода
function podKey(e){
  if(!POD.open)return;
  const mb=$('mbPod');if(!mb||!mb.classList.contains('on'))return;
  const cf=$('mbConfirm');if(cf&&cf.classList.contains('on'))return;
  if(e.key==='Escape'){e.preventDefault();podClose();return;}
  const tg=e.target;
  const tag=tg&&tg.tagName;
  if(tag==='INPUT'||tag==='TEXTAREA'||tag==='SELECT'||(tg&&tg.isContentEditable))return;
  // Ctrl/⌘+A — выбрать все куски (в полях ввода это остаётся выделением текста — выше)
  if((e.ctrlKey||e.metaKey)&&!e.altKey&&(e.key==='a'||e.key==='A')){
    e.preventDefault();POD.picked=POD.segs.map(s=>s.id);podRefresh();return;
  }
  // Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y — правки кусков; «я»/«н» — та же клавиша в русской раскладке (histKey)
  const hk=histKey(e);
  if(hk){e.preventDefault();hk==='redo'?podRedo():podUndo();return;}
  if(e.ctrlKey||e.metaKey||e.altKey)return;
  if(e.key===' '){
    e.preventDefault();
    // кнопка под фокусом иначе сработала бы ещё и по пробелу
    if(tag==='BUTTON'&&tg.blur)tg.blur();
    podPlayPause();return;
  }
  if(e.key==='i'||e.key==='I'){e.preventDefault();podSetEdge('start');return;}
  if(e.key==='o'||e.key==='O'){e.preventDefault();podSetEdge('end');return;}
  // C — резать (как «Резать» в редакторе шага 1); D или Delete — удалить. «с»/«в» — те же клавиши в русской раскладке
  const ek=tlEditKey(e);   // C/S — резать, D/Delete/Backspace — удалить (00-core.js)
  if(ek==='split'){e.preventDefault();podCut();return;}
  if(ek==='delete'){e.preventDefault();podDelKey();}
}
document.addEventListener('keydown',podKey);
