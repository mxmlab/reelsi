// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Maxim Si
// оверлей прогресса, лог, модалки, «!»-подсказки
//
// Часть интерфейса Reelsi. Файлы static/app/ грузятся ПО ПОРЯДКУ ИМЁН обычными
// <script>-тегами (не модулями): один общий скоуп, как было в едином app.js.
// Порядок важен — объявления функций поднимаются в пределах своего файла.

// ================= progress overlay =================
// Сама форма прогресса — в static/app/55-progress.js: одно окно, один словарь статусов
// (PROGEV), одна разметка строк и шапки, API progOpen/progItem/progDone. Здесь её нет и
// быть не должно — иначе вернётся вторая копия, которую потом правят в одном месте из
// двух. Отсюда наружу торчат только двери прогресса (progShow/progUpdate/progQueue/
// progStep/progDone/queueRender/localQ*), и живут они в том же модуле.

// ================= log =================
let LOGCACHE=[],CLIENTLOG=[];   // серверный лог (нарезка/сборка) + клиентские действия (разметка и т.п.)
let LOGSINCE=0,PROGMARK=null;   // абсолютный индекс лога (/api/status?since=) + последний маркер [i/N]
let LOGLAST='server';           // какой лог обновился последним ('server' | 'client')
let LOGTAB='server';            // текущая активная вкладка в окне логов

function logReset(){LOGCACHE=[];LOGSINCE=0;PROGMARK=null;}
function mergeLog(d){           // инкрементальный лог: сервер шлёт только новые строки
  if((d.log_total||0)<LOGSINCE)logReset();               // сервер начал лог заново (новый джоб)
  const incoming=d.log||[];
  if(incoming.length)LOGLAST='server';
  for(const l of incoming){LOGCACHE.push(l);const m=fmtLog(l).match(/\[(\d+)\/(\d+)\]/);if(m)PROGMARK=m;}
  LOGSINCE=d.log_total||LOGCACHE.length;
  if(LOGCACHE.length>4000)LOGCACHE.splice(0,LOGCACHE.length-4000);
  refreshLog();}
// слова, которые не влезли в шаблон субтитр-графики и потому пропали из XML (в норме — пусто)
function subSkipped(d){const s=(d&&d.skipped)||[];return s.length?t(' · ПРОПУЩЕНО ')+s.length+': '+s.join(', '):'';}
function uiLog(m){const now=new Date();const hh=x=>String(x).padStart(2,'0');
  CLIENTLOG.push('['+hh(now.getHours())+':'+hh(now.getMinutes())+':'+hh(now.getSeconds())+'] '+m);
  LOGLAST='client';
  if(CLIENTLOG.length>500)CLIENTLOG.shift();refreshLog();}
function _logScrolled(el){
  if(!el||el.clientHeight<=0)return;
  const isBottom=(el.scrollHeight-el.scrollTop-el.clientHeight)<=40;
  el.dataset.wasBottom=isBottom?'1':'0';
  el.dataset.savedScroll=String(el.scrollTop);}
function _logSetText(el,text){
  if(!el)return;
  const isHidden=(el.offsetParent===null&&el.style.display==='none');
  let wasBottom=true,oldScroll=0;
  if(!isHidden&&el.clientHeight>0){
    wasBottom=(el.scrollHeight-el.scrollTop-el.clientHeight)<=40;
    oldScroll=el.scrollTop;
  }else if(el.dataset.wasBottom==='0'){
    wasBottom=false;
    oldScroll=parseFloat(el.dataset.savedScroll||'0')||0;
  }
  el.textContent=text||'—';
  if(!isHidden&&el.clientHeight>0){
    if(wasBottom)el.scrollTop=el.scrollHeight;
    else el.scrollTop=oldScroll;
  }
  el.dataset.wasBottom=wasBottom?'1':'0';
  el.dataset.savedScroll=String(oldScroll);}
function refreshLog(){
  const ps=$('logpre_server'),pc=$('logpre_client');
  if(ps)_logSetText(ps,LOGCACHE.map(fmtLog).join('\n'));
  if(pc)_logSetText(pc,CLIENTLOG.map(fmtLog).join('\n'));}
function setLogTab(tab){
  LOGTAB=tab||'server';
  const ps=$('logpre_server'),pc=$('logpre_client');
  if(ps)ps.style.display=(LOGTAB==='server')?'':'none';
  if(pc)pc.style.display=(LOGTAB==='client')?'':'none';
  const cur=(LOGTAB==='server')?ps:pc;
  if(cur){
    if(cur.dataset.wasBottom!=='0')cur.scrollTop=cur.scrollHeight;
    else if(cur.dataset.savedScroll)cur.scrollTop=parseFloat(cur.dataset.savedScroll)||0;
  }
  const rad=document.querySelector('input[name="logtab"][value="'+LOGTAB+'"]');
  if(rad)rad.checked=true;
  segUI();}
async function openLog(){try{const d=await (await fetch('/api/status')).json();
  if(d.log){LOGCACHE=d.log;LOGSINCE=d.log_total||d.log.length;if(d.log.length)LOGLAST='server';}}catch(e){}
  refreshLog();openModal('mbLog');setLogTab(LOGLAST||'server');}

// ---- черновой mp4: запускается галкой при нарезке; здесь только поллинг чужого джоба
// (бут-поллинг подхватывает kind==='draft' после F5). Ручной draftRender удалён —
// пер-клип кнопки черновика нет с 2026-07-17.
async function pollDraft(){pollJob(pollDraft,t('Черновик mp4'),null,d=>{const res=d.results||[];
    if(res.length){progDone(t('Готово: ')+res.map(p=>p.replace(/^.*[\\\/]/,'')).join(' · '));toast(t('Черновик собран — лежит рядом с XML'));}
    else progDone(UICANCEL?t('Остановлено'):t('Ошибка — смотри Логи'),true);});}

// ---- очистка временных файлов (_tmp) в папке результата ----
// Прокси предпросмотра спрашиваем ОТДЕЛЬНО: это не мусор, а кэш по файлу камеры
// (десятки секунд пересборки на файл), и авто-очистка перед нарезкой их не трогает.
// Размеры тянем заранее — иначе кнопка предлагает удалить неизвестно что.
async function cleanTmp(){const od=val('ai_outdir').trim();if(!od){toast(t('Не задана папка результата'));return;}
  let info=null;try{const r=await (await fetch('/api/tmp_info?outdir='+encodeURIComponent(od))).json();
    if(!r.error)info=r;}catch(e){}
  const oth=info?(' — '+info.other_mb+t(' МБ')):'';
  if(!await askConfirm(t('Очистить временные файлы (_tmp)')+oth+t(' в:')+'\n'+od+'\n\n'+t('Черновики .draft.mp4 и кэш рото НЕ трогаются.')))return;
  let proxies=false;
  if(info&&info.proxy_mb>0)
    proxies=await askConfirm(t('Удалить и прокси предпросмотра ({n} МБ)?',{n:info.proxy_mb})+'\n\n'
      +t('Это кэш по файлу камеры, от монтажа он не зависит. Удалишь — при следующем открытии предпросмотра он пересоберётся, примерно полминуты на файл камеры.'));
  try{const d=await (await fetch('/api/clean_tmp',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({outdir:od,proxies})})).json();
    toast(d.error?errText(d):(t('Очищено: ')+(d.freed_mb||0)+t(' МБ')));}catch(e){toast(t('Сервер не ответил: ')+e);}}

// ================= modals =================
// Фокус: при открытии запоминаем, откуда пришли, уводим фокус ВНУТРЬ окна и держим его
// там (Tab по кругу). Раньше фокус оставался на body: с клавиатуры окно было недостижимо,
// а Tab уходил гулять по 24 контролам страницы ПОД бэкдропом.
let MODALBACK={};
function modalFocusables(m){return [...m.querySelectorAll(
  'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])')]
  .filter(e=>e.offsetParent!==null||e===document.activeElement);}
function openModal(id){const bd=$(id);MODALBACK[id]=document.activeElement;bd.classList.add('on');
  const m=bd.querySelector('.modal')||bd;
  tipArm(m);                                  // «!», нарисованные в модалке динамически
  // первым — не крестик закрытия («окно уже закрывают») и не «!»-подсказка: нужен
  // первый настоящий контрол.
  // Крестик метим АТРИБУТОМ data-close, а не текстом aria-label: aria-label входит в
  // I18N_ATTRS и переводится, так что на английском «Закрыть» превращалось в «Close»,
  // фильтр не срабатывал, и фокус вставал на крестик — первый же пробел (играть) закрывал
  // окно, теряя правку нарезки. Задеты были все модалки, на русском баг не виден (2026-08-22).
  const f=modalFocusables(m).filter(e=>!e.hasAttribute('data-close')
    &&!e.classList.contains('i'));
  (f[0]||m).focus&&(f[0]||m).focus({preventScroll:true});}
// Несохранённое не выбрасываем молча: правки нарезки живут в ED.blocks, а раскладка
// камер — в CAMED.assign, и до кнопки «Сохранить» ни то ни другое на диск не попадает.
// Escape, клик по фону и крестик закрывали окно сразу — десять минут ручной подчистки
// уходили в никуда (аудит 2026-07-23, B1/B2).
function modalDirty(id){
  if(id==='mbPreview')return ED.hist&&ED.hist.length>0;
  if(id==='mbCams')return !!(CAMED.saved&&CAMED.assign&&CAMED.saved.join()!==CAMED.assign.join());
  return false;}
function closeModal(id){
  if(modalDirty(id))askConfirm(id==='mbPreview'
      ?t('Правка нарезки не сохранена в XML. Закрыть и потерять её?')
      :t('Раскладка камер не сохранена. Закрыть и потерять её?'),{title:t('Несохранённые правки')}).then(ok=>{if(ok)_closeModal(id);});
  else _closeModal(id);}
// Одна модалка подтверждения на весь интерфейс: нативный confirm() не стилизован и
// блокирует поток, а при браузерной галке «блокировать диалоги» молча возвращает false —
// все кнопки с подтверждением переставали работать без единого сообщения. Открывается
// ЧЕРЕЗ openModal/closeModal, поэтому попадает в общий стек: Escape и клик по фону
// закрывают именно её. Возвращает Promise<boolean>: закрытие крестиком/фоном = false.
let CONFIRM_RESOLVE=null;
function askConfirm(text,opts){
  opts=opts||{};
  $('confirmTitle').textContent=opts.title||t('Подтверждение');
  $('confirmOk').textContent=opts.ok||t('Да');
  $('confirmCancel').textContent=opts.cancel||t('Отмена');
  $('confirmText').textContent=text;
  openModal('mbConfirm');
  return new Promise(res=>{
    CONFIRM_RESOLVE=res;
    // безопасный ответ по Enter — фокус при открытии стоит на кнопке отмены
    $('confirmCancel').focus({preventScroll:true});});}
// Крестик «Закрыть» у модалки подтверждения = ответ false (как Escape и клик по фону).
// Держим отдельно от confirmOk/confirmCancel, потому что те вешаются один раз на boot.
document.addEventListener('DOMContentLoaded',()=>{
  const ok=$('confirmOk'),cancel=$('confirmCancel');
  if(ok)ok.addEventListener('click',()=>{if(CONFIRM_RESOLVE){CONFIRM_RESOLVE(true);CONFIRM_RESOLVE=null;}closeModal('mbConfirm');});
  if(cancel)cancel.addEventListener('click',()=>{if(CONFIRM_RESOLVE){CONFIRM_RESOLVE(false);CONFIRM_RESOLVE=null;}closeModal('mbConfirm');});});
function _closeModal(id){
  // Модалка подтверждения закрыта любым путём (крестик, Escape, клик по фону,
  // программно) — это ответ false. Иначе CONFIRM_RESOLVE оставался висеть:
  // ждущий askConfirm промис не разрешался никогда, а следующий вопрос затирал
  // старую функцию (аудит 2026-08-25).
  if(id==='mbConfirm'&&CONFIRM_RESOLVE){CONFIRM_RESOLVE(false);CONFIRM_RESOLVE=null;}
  tipHide();
  $(id).classList.remove('on');if(id==='mbPreview'){edPause();   // плеер шага 1 один — редактор
    // Превью закрыли — живой хост плагинов гасим (процесс по PID, на сервере): по
    // закрытии превью не должно оставаться ни одного.
    if(typeof voiceFxHostStop==='function')voiceFxHostStop();}
  if(id==='mbInserts'){ipvPause();if(typeof ipvClose==='function')ipvClose();aeAfterPreview();if(typeof voiceFxHostStop==='function')voiceFxHostStop();}
  if(id==='mbCams'){cpvPause();if(typeof cpvReset==='function')cpvReset();if(typeof voiceFxHostStop==='function')voiceFxHostStop();}
  const back=MODALBACK[id];MODALBACK[id]=null;
  if(back&&back.isConnected&&back.focus)back.focus({preventScroll:true});}
// Tab не выпускает из верхней модалки
document.addEventListener('keydown',e=>{if(e.key!=='Tab')return;
  const top=MODAL_STACK.find(id=>$(id)&&$(id).classList.contains('on'));if(!top)return;
  const m=$(top).querySelector('.modal')||$(top);const f=modalFocusables(m);if(!f.length)return;
  const first=f[0],last=f[f.length-1];
  if(!m.contains(document.activeElement)){e.preventDefault();first.focus();return;}
  if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}
  else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}},true);
// ---- «!»-подсказки: одна плавающая карточка, position:fixed ----
// Считаем координаты сами: у края окна карточка прижимается правым краем, а не уезжает
// за экран, и её не режет скролл-контейнер (вкладка «Стиль» в предпросмотре).
function tipShow(el){const box=$('tipbox');if(!box)return;
  box.textContent=el.dataset.t||'';box.classList.add('on');
  box.style.left='0px';box.style.top='0px';                 // измеряем в свободной позиции
  const r=el.getBoundingClientRect(),b=box.getBoundingClientRect();
  const M=8;
  let x=Math.min(r.left,innerWidth-b.width-M);x=Math.max(M,x);
  let y=r.bottom+M;
  if(y+b.height>innerHeight-M)y=Math.max(M,r.top-b.height-M);   // не влезло вниз — показываем вверх
  box.style.left=Math.round(x)+'px';box.style.top=Math.round(y)+'px';}
// Справка на КОНТРОЛАХ (кнопки, поля, галки) всплывает с задержкой TIP_DELAY_MS, а не
// сразу: при движении мыши по панели мгновенная карточка под каждым элементом мешает —
// навёл мимоходом, получил подсказку. «!»-кружки (.i) — осознанный заход за справкой,
// им задержка не нужна (правило в DESIGN.md, правка юзера 2026-08-10).
const TIP_DELAY_MS=650;
let tipTimer=null,tipEl=null;
function tipHide(){const box=$('tipbox');if(box)box.classList.remove('on');
  clearTimeout(tipTimer);tipTimer=null;tipEl=null;}
function tipShowDelayed(el){
  clearTimeout(tipTimer);tipTimer=null;tipEl=null;
  if(el.classList.contains('i')){tipShow(el);return;}          // «!» — сразу
  tipEl=el;
  tipTimer=setTimeout(()=>{tipTimer=null;
    // за время ожидания могли уйти с элемента — показываем, только если мышь
    // (или фокус с клавиатуры) всё ещё на нём
    if(tipEl&&(tipEl.matches(':hover')||tipEl.contains(document.activeElement)))tipShow(tipEl);
    tipEl=null;},TIP_DELAY_MS);}
// «!» — не кнопка, фокус ему нужно выдать руками. Раньше это делалось ОДИН раз на boot,
// и подсказки, нарисованные позже (панель «Заменить камеру»), с клавиатуры не открывались.
// Зовём после каждой такой отрисовки и при открытии любой модалки.
function tipArm(root){(root||document).querySelectorAll('.i[data-t]:not([tabindex])')
  .forEach(e=>{e.tabIndex=0;});}
// [data-t], а не только «.i[data-t]»: справку теперь несёт сам контрол (кнопка/⚙/галка),
// а не отдельный кружок «!» рядом. #tipbox плавающий (fixed) — сам прижимается к краю
// и переворачивается вверх, так что работает и на кнопке у правого края.
document.addEventListener('mouseover',e=>{const el=e.target.closest&&e.target.closest('[data-t]');
  if(el)tipShowDelayed(el);else if(!e.target.closest||!e.target.closest('#tipbox'))tipHide();});
document.addEventListener('focusin',e=>{const el=e.target.closest&&e.target.closest('[data-t]');
  if(el)tipShowDelayed(el);else tipHide();});
window.addEventListener('scroll',tipHide,true);
window.addEventListener('resize',tipHide);
// Закрыли AE-предпросмотр: настройки блока стиля возвращаются на страницу, задание
// сохраняется, и клип, у которого есть интро, помечается «готов к AE» — карточка в списке
// заливается зелёным (просьба юзера: видно, над чем уже поработал, без чтения тегов).
function aeAfterPreview(){styleHome();
  if(IPVMODE!=='ae'||curAE<0||!CLIPS[curAE])return;
  captureAE();
  const j=CLIPS[curAE].job;if(j&&(j.introRows||[]).length)j.aeSeen=true;
  saveState();renderClips3();}
function aeDone(c){return !!(c&&c.job&&c.job.aeSeen&&(c.job.introRows||[]).length);}
// клик по фону закрывает ТОЛЬКО если и mousedown был на фоне — иначе драг из канваса
// (тянешь край блока, вывел мышь за модалку, отпустил) случайно закрывал окно
let BACKDOWN=null;
document.addEventListener('mousedown',e=>{BACKDOWN=e.target;},true);
function closeIfBack(e,id){if(e.target===$(id)&&BACKDOWN===$(id))closeModal(id);}
// Escape закрывает ТОЛЬКО верхнюю модалку. Раньше закрывались все открытые сразу, а
// mbInsLib в списке не было: стоя в «Базе вставок» (она открывается ПОВЕРХ «Вставок»)
// юзер жал Escape — база оставалась, а закрывалось окно под ней.
// Порядок = порядок вложенности: база поверх вставок, вставки поверх предпросмотра.
const MODAL_STACK=['mbConfirm','mbDelClip','mbInsLib','mbSpeaker','mbAISettings','mbCams','mbLog','mbInserts','mbPreview'];
document.addEventListener('keydown',e=>{if(e.key!=='Escape')return;
  const top=MODAL_STACK.find(id=>$(id)&&$(id).classList.contains('on'));
  if(top)closeModal(top);});

