// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Maxim Si
// SVG-иконки, скраббер-число, переключение шагов
//
// Часть интерфейса Reelsi. Файлы static/app/ грузятся ПО ПОРЯДКУ ИМЁН обычными
// <script>-тегами (не модулями): один общий скоуп, как было в едином app.js.
// Порядок важен — объявления функций поднимаются в пределах своего файла.

// ================= SVG-иконки (контур 1.5px, по стилю: только chalk/gold, без эмодзи) =================
const IC={
 pencil:'<path d="M17 3a2.8 2.8 0 0 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/>',
 dl:'<path d="M12 3v12m0 0 4-4m-4 4-4-4M4 21h16"/>',
 x:'<path d="M18 6 6 18M6 6l12 12"/>',
 plus:'<path d="M12 5v14M5 12h14"/>',
 minus:'<path d="M5 12h14"/>',
 stop:'<rect x="6" y="6" width="12" height="12" rx="1.5"/>',
 fit:'<path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/>',
 play:'<path d="M7 4.5v15l12-7.5Z"/>',
 pause:'<path d="M8 4.5v15M16 4.5v15"/>',
 cut:'<circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><path d="M8.2 7.7 20 19M8.2 16.3 20 5"/>',
 trash:'<path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M10 11v6M14 11v6"/>',
 undo:'<path d="M9 14 4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>',
 save:'<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z"/><path d="M17 21v-8H7v8M7 3v5h8"/>',
 ai:'<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8Z"/><path d="M19 15l.8 2.4 2.4.8-2.4.8L19 21.5l-.8-2.5-2.4-.8 2.4-.8Z"/>',
 img:'<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-4.5-4.5L6 21"/>',
 film:'<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M8 3v18M16 3v18M3 8h5M3 16h5M16 8h5M16 16h5"/>',
 cam:'<rect x="2" y="6" width="14" height="12" rx="2"/><path d="m16 10 6-3.5v11L16 14z"/>',
 book:'<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V4a2 2 0 0 0-2-2H6.5A2.5 2.5 0 0 0 4 4.5v15A2.5 2.5 0 0 0 6.5 22H20v-5"/>',
 gear:'<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1.03 1.56V21a2 2 0 1 1-4 0v-.09a1.7 1.7 0 0 0-1.11-1.56 1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.7 1.7 0 0 0 .34-1.87 1.7 1.7 0 0 0-1.56-1.03H3a2 2 0 1 1 0-4h.09A1.7 1.7 0 0 0 4.65 8.9a1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06-.06a1.7 1.7 0 0 0 1.87.34H9a1.7 1.7 0 0 0 1.03-1.56V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1.03 1.56 1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.87V9c.21.63.79 1.05 1.56 1.03H21a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.51.97Z"/>',
 broom:'<path d="M13 3l-2 8m-5.5 3.5c3 1.5 7.5 1.5 10 0M5 14l-1.5 7h17L19 14a17 17 0 0 1-14 0Z"/>',
 check:'<path d="M4 12.5 9 17.5 20 6.5"/>',
 target:'<circle cx="12" cy="12" r="5"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4"/>',
 xfade:'<path d="M7 4v16M7 4 4 7m3-3 3 3M17 20V4m0 16 3-3m-3 3-3-3"/>',
 sound:'<path d="M11 5 6 9H2v6h4l5 4V5Z"/><path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13"/>',
 music:'<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
 refresh:'<path d="M20.5 12a8.5 8.5 0 1 1-2.5-6"/><path d="M21 3v6h-6"/>',
 arrow_up:'<path d="m18 15-6-6-6 6"/>',
 arrow_down:'<path d="m6 9 6 6 6-6"/>',
 grip:'<circle cx="9" cy="6" r="1.2" fill="currentColor"/><circle cx="9" cy="12" r="1.2" fill="currentColor"/><circle cx="9" cy="18" r="1.2" fill="currentColor"/><circle cx="15" cy="6" r="1.2" fill="currentColor"/><circle cx="15" cy="12" r="1.2" fill="currentColor"/><circle cx="15" cy="18" r="1.2" fill="currentColor"/>',
};
function ico(n,cls){return '<svg class="ic'+(cls?' '+cls:'')+'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'+(IC[n]||'')+'</svg>';}

// ================= скраббер-число (как в After Effects) =================
// Разметка: <span class="scrub" data-o="ВЫРАЖЕНИЕ-ОБЪЕКТ" data-k="ключ" data-step="1" data-cb="колбэк()">0</span>
// Тянешь мышкой — значение бежит (Shift — в 5 раз быстрее, Ctrl — в 5 раз точнее),
// двойной клик — ввод с клавиатуры. Объект берём выражением, как в остальных inline-обработчиках.
let SCRUB=null;
function scrubObj(el){try{return new Function('return ('+(el.dataset.o||'null')+')')();}catch(e){return null;}}
function scrubCb(el){if(el.dataset.cb)try{new Function(el.dataset.cb)();}catch(e){}}
function scrubSet(el,v){const o=scrubObj(el);if(!o)return;
  const st=parseFloat(el.dataset.step)||1;
  v=Math.round(v/st)*st;
  const mn=el.dataset.min!==undefined?parseFloat(el.dataset.min):-1e9;
  const mx=el.dataset.max!==undefined?parseFloat(el.dataset.max):1e9;
  v=Math.max(mn,Math.min(mx,v));
  o[el.dataset.k]=v;el.textContent=(Math.round(v*100)/100);scrubCb(el);}
function scrubDown(ev,el){
  if(el.querySelector('input'))return;                 // уже в режиме ввода — не перехватываем
  ev.preventDefault();
  const o=scrubObj(el);if(!o)return;
  SCRUB={el,x0:ev.clientX,v0:parseFloat(o[el.dataset.k])||0,moved:false};
  document.body.style.cursor='ew-resize';
  window.addEventListener('mousemove',scrubMove);window.addEventListener('mouseup',scrubUp);}
function scrubMove(ev){if(!SCRUB)return;
  const dx=ev.clientX-SCRUB.x0;
  if(Math.abs(dx)>2)SCRUB.moved=true;
  const k=ev.shiftKey?5:(ev.ctrlKey?0.2:1);
  scrubSet(SCRUB.el,SCRUB.v0+dx*k*(parseFloat(SCRUB.el.dataset.step)||1));}
function scrubUp(){if(!SCRUB)return;
  document.body.style.cursor='';
  window.removeEventListener('mousemove',scrubMove);window.removeEventListener('mouseup',scrubUp);
  const el=SCRUB.el,moved=SCRUB.moved;SCRUB=null;
  if(moved&&el.dataset.save)try{new Function(el.dataset.save)();}catch(e){}}   // в localStorage — один раз по отпусканию, а не на каждый пиксель
function scrubEdit(el){const o=scrubObj(el);if(!o)return;
  const cur=parseFloat(o[el.dataset.k])||0;
  el.innerHTML='<input type="number" step="'+(el.dataset.step||1)+'" value="'+cur+'">';
  const inp=el.querySelector('input');inp.focus();inp.select();
  // Применяем ЗДЕСЬ, а не по blur: blur не приходит, если окно потеряло фокус
  // (переключился на AE — значение молча терялось). Флаг — чтобы Enter и следом
  // blur не применили правку дважды.
  let closed=false;
  const done=ok=>{if(closed)return;closed=true;
    const v=parseFloat(inp.value);el.innerHTML='';scrubSet(el,(ok&&!isNaN(v))?v:cur);
    if(el.dataset.save)try{new Function(el.dataset.save)();}catch(e){}};
  inp.addEventListener('blur',()=>done(true));
  inp.addEventListener('keydown',e=>{
    if(e.key==='Enter'){e.preventDefault();done(true);}
    if(e.key==='Escape'){e.preventDefault();done(false);}});}
// одно скраббер-число (из него собраны X/Y, маска и «файл с»)
function scrubSpan(objExpr,k,v,o){o=o||{};
  return '<span class="scrub" data-o="'+objExpr+'" data-k="'+k+'" data-step="'+(o.step||1)+'"'
    +(o.min!=null?' data-min="'+o.min+'"':'')+(o.max!=null?' data-max="'+o.max+'"':'')
    +(o.cb?' data-cb="'+esc(o.cb)+'"':'')+(o.save?' data-save="'+esc(o.save)+'"':'')
    +' onmousedown="scrubDown(event,this)" ondblclick="scrubEdit(this)">'+(Math.round((v||0)*100)/100)+'</span>';}
// готовая пара X/Y для карточки вставки
function scrubXY(objExpr,x,y,cb,save){const o={cb,save};
  return '<span class="xy" data-t="'+t('Точка покоя вставки, px от центра (у видео — панорама кадра; тяни мышкой)')+'">'
    +'<b>X</b>'+scrubSpan(objExpr,'x',x,o)+'<b>Y</b>'+scrubSpan(objExpr,'y',y,o)+'</span>';}
// форма маски вставки: ширина/высота карточки в % от авторасчёта (100 = как считает
// xml2ae сам). У фото авторасчёт режет всё не-ультравайдное в квадрат, а предмет в квадрат
// лезет не всегда; у видео 100 = весь кадр исходника. Маска РЕЖЕТ, а не масштабирует —
// крупность предмета меняется скраббером «масштаб» рядом.
function scrubMask(objExpr,mw,mh,cb,save){const o={cb,save,min:20,max:300};
  return '<span class="xy" data-t="'+t('Маска: ширина и высота карточки, % от авто — режет кадр, крупность не меняет (тяни мышкой, двойной клик — ввод)')+'">'
    +'<b>'+t('Ш')+'</b>'+scrubSpan(objExpr,'mw',mw==null?100:mw,o)+'<b>'+t('В')+'</b>'+scrubSpan(objExpr,'mh',mh==null?100:mh,o)+'</span>';}
// масштаб вставки, % от АВТО (100 = как считает xml2ae сам). У фото множит посчитанную под
// картинку карточку (scale там пересчитывается по пропорциям, абсолютное число тут держать
// нельзя — затрёт), у видео — заполнение кадра: 100 = во весь экран, меньше = карточка.
// От него же считается анимация кам2 (наезд идёт ОТ осевшего масштаба, а не от константы).
function scrubScale(objExpr,sc,cb,save){
  return '<span class="xy" data-t="'+t('Масштаб вставки, % от авто: у фото — размер карточки, у видео 100 = во весь кадр (тяни мышкой, двойной клик — ввод)')+'">'
    +'<b>'+t('масштаб')+'</b>'+scrubSpan(objExpr,'sc',sc==null?100:sc,{cb,save,min:10,max:400})+'<b>%</b></span>';}
// видеовставка: с какой секунды ФАЙЛА играть кусок (нужное часто не в начале ролика)
function scrubSin(objExpr,sin,cb,save){
  return '<span class="xy" data-t="'+t('С какой секунды исходного файла играть кусок (тяни мышкой, двойной клик — ввод)')+'">'
    +'<b>'+t('файл с')+'</b>'+scrubSpan(objExpr,'sin',sin,{cb,save,step:0.1,min:0})+'<b>'+t('с')+'</b></span>';}
// скраббер тянет ЗНАЧЕНИЕ ОБЪЕКТА, а у старых вставок этих ключей нет — с undefined
// он стартовал бы с нуля (или с NaN). Проставляем дефолты перед отрисовкой карточки.
function insScrubInit(x){if(x.mw==null)x.mw=100;if(x.mh==null)x.mh=100;if(x.sc==null)x.sc=100;
  if(x.sin==null)x.sin=0;if(x.x==null)x.x=0;if(x.y==null)x.y=0;return x;}

// ================= steps =================
let STEP=1;
function goStep(n){
  if(n===2 && !CLIPS.length){toast(t('Сначала сделай нарезку или добавь XML'));return;}
  if(STEP===3&&n!==3&&curAE>=0)captureAE();   // уход с шага AE — не потерять интро/жёлтые/вставки в джобе
  const pv=$('pageVideo');if(pv)pv.classList.remove('on');   // уходим с вкладки «Видео» (если были на ней)
  const nv=$('navvideo');if(nv)nv.classList.remove('active');
  STEP=n;
  [1,2,3].forEach(k=>{$('step'+k).classList.toggle('on',k===n);
    const si=$('si'+k); si.classList.toggle('active',k===n); si.classList.toggle('done',k!==n && (k<n||CLIPS.length));});
  if(n===1)renderClips1();
  if(n===2){renderClips2(); refreshStatuses(); fillAIProfileSelects();}   // пере-заполнить селекты моделей/ума разметки
  if(n===3){ensureJobs(); if(curAE>=CLIPS.length)curAE=-1;
    // выбранный клип надо ЗАГРУЗИТЬ в панель (после F5 curAE восстановлен, а DOM пустой);
    // curAE=-1 перед selectAE — чтобы captureAE не записал в джоб дефолтный DOM
    const w=curAE>=0?curAE:0; curAE=-1;
    if(CLIPS.length)selectAE(w); else renderClips3();}
  try{localStorage.setItem('reelsi_step',n);}catch(e){}
  saveState();
}
function toast(m,ms){const box=document.createElement('div');box.className='toast';
  // role=status + aria-live: тост обязан озвучиваться скринридером, а не пропадать молча
  box.setAttribute('role','status');box.setAttribute('aria-live','polite');box.textContent=m;
  document.body.appendChild(box);
  // ошибкам — больше времени на прочтение
  setTimeout(()=>box.remove(),ms||(/^[⚠✗]/.test(String(m))?6000:2600));}

// ================= компонент «слайдер + число» (.sldnum, задание CP2) =================
// Синхронизация input[type=range] и input[type=number] в обе стороны:
// sldSync кладёт значение ползунка в число ДО вызова stEdit.
function sldSync(el){
  if(!el)return;
  const wrap=el.closest&&el.closest('.sldnum');
  if(!wrap)return;
  const rng=wrap.querySelector('input[type=range]');
  const num=wrap.querySelector('input[type=number]');
  if(el===rng&&num){num.value=rng.value;}
  else if(el===num&&rng){rng.value=num.value;}
}
function syncSldnums(root){
  const el=root||document;
  const nodes=el.classList&&el.classList.contains('sldnum')?[el]:
    (el.closest&&el.closest('.sldnum')?[el.closest('.sldnum')]:
     el.querySelectorAll?el.querySelectorAll('.sldnum'):[]);
  nodes.forEach(wrap=>{
    const rng=wrap.querySelector('input[type=range]');
    const num=wrap.querySelector('input[type=number]');
    if(!rng||!num)return;
    if(num.value!=='')rng.value=num.value;
    else if(rng.value!=='')num.value=rng.value;
  });
}



