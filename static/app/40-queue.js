// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Maxim Si
// проект, камеры, очередь клипов, запуск нарезки и поллинг джоба
//
// Часть интерфейса Reelsi. Файлы static/app/ грузятся ПО ПОРЯДКУ ИМЁН обычными
// <script>-тегами (не модулями): один общий скоуп, как было в едином app.js.
// Порядок важен — объявления функций поднимаются в пределах своего файла.

// ================= project / cameras / queue =================
// CAMFROM[k] — ОТКУДА взялась папка камеры k: '' автоподбор, 'user' выбрана руками,
// 'spk' подставлена из профиля спикера. Раньше тут был булев флаг источника, и он путал два
// разных вопроса: «можно ли перетереть автоподбором» и «спрашивать ли перед заменой».
// Папка из профиля отвечала на них по-разному, а флаг был один — camDirApply гасил его
// в false, и ближайший loadCams молча возвращал автоподбор (см. там же).
let CAMS=null,QUEUE=[],CAMDIRS=[],CAMFILES=[],CAMFROM=[];
async function loadCams(){
  const base=val('base');
  const d=await (await fetch('/api/cams?base='+encodeURIComponent(base))).json();
  if(d.error){
    // Нет папок камер — на чистой установке не ошибка, а первый шаг: предложи их
    // создать. Голую ошибку не показываем: человек просто не знал, что нужно создать.
    if(d.err==='no_cam_folders'){
      $('caminfo').innerHTML='⚠ '+esc(errText(d))+' <button class="sm" onclick="makeCams()">'+t('Создать папки камер')+'</button>';
      return;
    }
    $('caminfo').textContent='⚠ '+errText(d);return;
  }
  CAMS=d;
  // Автоопределённые папки НЕ затирают выбранную (CAMFROM) — ни руками, ни из профиля
  // спикера; у таких лишь обновляем список файлов. Папку из профиля тут раньше затирало:
  // автоподбор для «камеры 1» отдаёт первой голую «камера1», и после F5 нарезка уходила
  // читать её вместо «камера1 Адилет» — с очередью, где лежат ИМЕНА файлов Адилета.
  const auto=d.dirs.map(x=>x.path),autoF=d.dirs.map(x=>x.files);
  for(let k=0;k<Math.max(auto.length,CAMDIRS.length);k++){
    if(CAMFROM[k]&&CAMDIRS[k]){
      try{const fd=await (await fetch('/api/files?dir='+encodeURIComponent(CAMDIRS[k]))).json();
        if(!fd.error)CAMFILES[k]=fd.files||[];}catch(e){}
    }else{CAMDIRS[k]=auto[k]||'';CAMFILES[k]=autoF[k]||[];}
  }
  if(!val('ai_outdir'))$('ai_outdir').value=d.outdir;
  if(!AEGLOBAL){AEGLOBAL=d.outdir;renderAeDirField();}
  if(!val('aemusicdir'))$('aemusicdir').value=(d.base||'')+'\\music';
  if(!val('gdrive_dest'))$('gdrive_dest').value=(d.base||'')+'\\gdrive_downloads';
  $('caminfo').textContent=t('Папки: ')+d.dirs.map(x=>x.name+' ('+x.files.length+')').join(', ');
  buildCamRows();
}
async function makeCams(){
  // Кнопка «Создать папки камер» на чистой установке: папки создаёт бэкенд по языку
  // интерфейса, после чего список перечитывается как при обычном /api/cams.
  const base=val('base');
  const d=await (await fetch('/api/cams_make',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({base,n:nCams(),lang:LANG})})).json();
  if(d.error){$('caminfo').textContent='⚠ '+errText(d);return;}
  await loadCams();
}
function fill(id,arr){const s=$(id);if(!s)return;s.innerHTML='';(arr||[]).forEach(f=>{const o=document.createElement('option');o.textContent=f;s.appendChild(o);});}
// Какие кнопки очереди живут при таком числе камер. Раньше это стояло в setMode, то есть
// только на СМЕНЕ радио: после F5 с сохранённым «1 камера» на странице оставались «Авто-пары»
// и оба подбора по звуку — а сводить им нечего.
function camModeUI(){const one=(nCams()===1);
  [['autopair',!one],['cammatch',!one],['cammatchall',!one],['autoqueue',one]]
    .forEach(pair=>{const b=$(pair[0]);if(b)b.style.display=pair[1]?'':'none';});}
function buildCamRows(){const n=nCams(),host=$('camrows');host.innerHTML='';
  for(let k=0;k<n;k++){const row=document.createElement('div');row.className='row';row.style.marginBottom='8px';
    row.innerHTML='<div class="grow"><label for="camdir'+k+'">'+t('Папка камеры {n}',{n:k+1})+'</label><input id="camdir'+k+'" style="width:100%"></div>'+
      '<button class="sm" onclick="pickCamDir('+k+')">'+t('Выбрать…')+'</button>'+
      '<div><label for="sel'+k+'">'+(n===1?t('Видео'):t('Камера {n}',{n:k+1}))+'</label><select id="sel'+k+'"></select></div>';
    host.appendChild(row);$('camdir'+k).value=CAMDIRS[k]||'';fill('sel'+k,CAMFILES[k]||[]);}
  camModeUI();renderQueue();}
async function pickCamDir(k){let d;try{d=await (await fetch('/api/pickdir')).json();}
  catch(e){toast(t('Не открылся выбор папки — сервер не ответил'));uiLog('pickdir(cam): '+e);return;}
  if(!d.path)return;
  CAMDIRS[k]=d.path;CAMFROM[k]='user';const fd=await (await fetch('/api/files?dir='+encodeURIComponent(d.path))).json();
  CAMFILES[k]=fd.files||[];$('camdir'+k).value=d.path;fill('sel'+k,CAMFILES[k]);saveState();
  if(typeof camDirCommit==='function')camDirCommit(k);}   // выбрал спикер — спросить «сохранить в профиль?»
let LASTCAMS=null;                       // чтобы вернуть радио, если юзер передумал
async function setMode(){
  // Смена числа камер пересобирает строки и обнуляет очередь. Раньше это происходило
  // молча: набрал 12 пар «Авто-парами», ткнул «3» посмотреть — очередь пуста, undo нет.
  if(QUEUE.length&&!await askConfirm(t('Сменить число камер? Очередь ({n} {pairs}) будет очищена.',{n:QUEUE.length,pairs:t(plur(QUEUE.length,'пара','пары','пар'))}))){
    if(LASTCAMS!=null){const r=document.querySelector('input[name=cams][value="'+LASTCAMS+'"]');
      if(r){r.checked=true;segUI();}}                 // segUI: вернуть подсветку пилюли
    return;}
  LASTCAMS=nCams();QUEUE=[];buildCamRows();   // buildCamRows → camModeUI: кнопки под число камер
  saveState();}
function curPick(){const n=nCams(),names=[];for(let k=0;k<n;k++){const s=$('sel'+k);if(!s||!s.value)return null;names.push(s.value);}return names;}
function eqArr(a,b){return a.length===b.length&&a.every((x,i)=>x===b[i]);}
function addPair(){const names=curPick();if(!names)return;if(QUEUE.some(p=>eqArr(p,names)))return;QUEUE.push(names);renderQueue();saveState();}
async function autoPair(){const n=nCams();if(n<2||!CAMFILES.length)return;const sets=CAMFILES.slice(0,n).map(f=>new Set(f||[]));
  const same=(CAMFILES[0]||[]).filter(fn=>sets.every(s=>s.has(fn)));
  (await newOnly(same)).forEach(fn=>{const names=Array(n).fill(fn);if(!QUEUE.some(p=>eqArr(p,names)))QUEUE.push(names);});
  renderQueue();saveState();}
// «Всё в очередь» — одна камера. Пары собирать не из чего (авто-пары и подбор по звуку
// работают от двух камер), и очередь набивалась селектом по одному файлу: выбрал → «В очередь»
// → снова выбрал, и так двадцать раз (просьба юзера). Берём из папки всё, чего в очереди ещё
// нет; галочка «только новые» сверху отсеивает то, на что в папке результата уже есть XML.
async function autoQueue(){
  const n=nCams();if(n!==1)return;
  const all=CAMFILES[0]||[];
  if(!all.length){toast(t('В папке камеры нет видео — проверь папку и «Пересканировать»'));return;}
  const have=new Set(QUEUE.map(p=>p[0]));
  const fresh=all.filter(fn=>!have.has(fn));
  if(!fresh.length){toast(t('Все файлы папки уже в очереди'));return;}
  const btn=$('autoqueue');if(btn)btn.disabled=true;
  try{
    const files=await newOnly(fresh);   // отсев ДО очереди: незачем предлагать давно нарезанное
    files.forEach(fn=>{if(!QUEUE.some(p=>eqArr(p,[fn])))QUEUE.push([fn]);});
    renderQueue();saveState();
    uiLog(t('в очередь: {n} из {m} файлов папки',{n:files.length,m:all.length}));
    if(files.length)toast(t('Добавлено в очередь: {n}',{n:files.length}));
  }catch(e){toast(t('Сервер не ответил: ')+e);uiLog('autoqueue: '+e);}
  finally{if(btn)btn.disabled=false;}}
// «Только новые»: выбрасываем дубли, на которые в папке результата уже есть XML
// (сверка — /api/newtakes: сайдкар прогона, пути внутри XML, имя файла). Папка
// камеры копится съёмками, а нарезать надо то, что приехало с последней; заодно
// подбор по звуку не гоняет корреляцию по давно готовому — это его основная цена.
// Сервер не ответил — берём ВСЁ: молча потерять дубль хуже, чем предложить лишний.
async function newOnly(files){const box=$('newonly');
  if(!box||!box.checked||!files.length)return files;
  const outdir=val('ai_outdir').trim();
  if(!outdir){toast(t('Не задана папка результата — беру все дубли'));return files;}
  try{const d=await (await fetch('/api/newtakes',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({outdir,files})})).json();
    if(d.error){uiLog(t('только новые: ')+errText(d));return files;}
    const done=(d.done||[]).length;
    if(done)uiLog(t('только новые: пропускаю уже нарезанных — {n}',{n:done}));
    if(!(d.new||[]).length)toast(t('Новых дублей нет — на все уже есть XML'));
    return d.new||[];
  }catch(e){uiLog(t('только новые: ')+e);return files;}}
// Автоподбор вторичных камер по звуку: видео камеры 1 выбрано (sel0 — id селекта
// камеры 0, строится как 'sel'+k), для каждой из камер 2..N ищем в её папке файл того
// же дубля — его звук коррелирует со звуком кам1 (имена файлов у камер могут не
// совпадать, тогда «Авто-пары по имени» не находит ничего). Нашлось всё — пара сразу
// уходит в очередь, как у авто-пар.
async function camMatch(){
  const n=nCams();if(n<2)return;
  const s0=$('sel'+0);if(!s0||!s0.value){toast(t('Сначала выбери видео камеры 1'));return;}
  const dirs=CAMDIRS.slice(1,n).filter(x=>(x||'').trim());
  if(dirs.length!==n-1){toast(t('Задай папки камер 2..{n}',{n:n}));return;}
  const btn=$('cammatch');if(btn)btn.disabled=true;
  uiLog(t('автоподбор по звуку: ')+s0.value+' …');
  try{
    // папку и имя шлём ПОРОЗНЬ, склеивает сервер (os.path.join) — как в /api/run.
    // Свой разделитель '\\' тут работал только под Windows.
    const d=await (await fetch('/api/cammatch',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({cam1dir:CAMDIRS[0]||'',cam1:s0.value,dirs:dirs})})).json();
    if(d.error){toast('⚠ '+errText(d));uiLog(t('автоподбор: ')+d.error);return;}
    const hits=[];let all=true;
    (d.matches||[]).forEach((m,k)=>{
      const s=$('sel'+(k+1));if(!s)return;
      if(m.name){s.value=m.name;hits.push(t('Камера {n}: {name} (увер. {c})',{n:k+2,name:m.name,c:m.score}));}
      else{all=false;hits.push(t('Камера {n}: похожего не нашлось',{n:k+2}));}});
    uiLog(t('автоподбор: ')+hits.join(' · '));
    if(all)addPair();
  }catch(e){toast(t('Сервер не ответил: ')+e);uiLog('cammatch: '+e);}
  finally{if(btn)btn.disabled=false;}
}
// «Подбор всех по звуку»: каждый файл камеры 1 прогоняется через тот же
// /api/cammatch, и если во ВСЕХ камерах 2..N нашёлся тот же дубль — пара уходит
// в очередь. Огибающие кэшируются на сервере (путь+mtime+размер), поэтому N
// запросов стоят не дороже одного полного скана.
async function camMatchAll(){
  const n=nCams();if(n<2)return;
  const dirs=CAMDIRS.slice(1,n).filter(x=>(x||'').trim());
  if(dirs.length!==n-1){toast(t('Задай папки камер 2..{n}',{n:n}));return;}
  const cam1files=CAMFILES[0]||[];if(!cam1files.length){toast(t('Сначала выбери видео камеры 1'));return;}
  const btn=$('cammatchall');if(btn)btn.disabled=true;
  let added=0;
  try{
    const files=await newOnly(cam1files);   // отсев ДО корреляции: она и есть вся цена этой кнопки
    if(!files.length)return;
    uiLog(t('автоподбор всех: ')+files.length+' …');
    for(const fn of files){
      const d=await (await fetch('/api/cammatch',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({cam1dir:CAMDIRS[0]||'',cam1:fn,dirs:dirs})})).json();
      if(d.error){uiLog(t('автоподбор: ')+fn+': '+d.error);continue;}
      const names=[fn];let all=true;
      (d.matches||[]).forEach(m=>{if(m.name)names.push(m.name);else all=false;});
      if(!all)continue;
      if(QUEUE.some(p=>eqArr(p,names)))continue;
      QUEUE.push(names);added++;
    }
    renderQueue();saveState();
    uiLog(t('добавлено {n} из {m}',{n:added,m:files.length}));
  }catch(e){toast(t('Сервер не ответил: ')+e);uiLog('cammatchall: '+e);}
  finally{if(btn)btn.disabled=false;}
}
// ================= гугл-диск =================
// Ссылка → rclone copy → файлы в папке загрузки → выбор файла камеры 1 → тот же
// /api/cammatch, что и «Подбор по звуку». Раскладку по камерам не изобретаем.
let GDN=0,GDLOGN=0;             // интервал поллинга прогресса и сколько строк лога уже показано
async function gdDownload(){
  const url=$('gdrive_url').value.trim();let dest=$('gdrive_dest').value.trim();
  if(!url){toast(t('Вставь ссылку гугл-диска'));return;}
  if(!dest)dest=(val('base').trim().replace(/[\\\/]+$/,'')||'')+'\\gdrive_downloads';
  const btn=$('gd_go');if(btn)btn.disabled=true;
  uiLog(t('гугл-диск: скачиваю ')+url);
  try{
    const d=await (await fetch('/api/gdrive_download',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({url,dest})})).json();
    if(d.error){toast('⚠ '+errText(d));uiLog(t('гугл-диск: ')+d.error);return;}
    $('gdrive_dest').value=dest;
    GDLOGN=0;                   // лог на сервере начался заново — считаем показанные строки с нуля
    $('gdrive_status').textContent=t('запуск rclone…');   // до первого опроса статус не должен быть пустым
    gdPoll();
  }catch(e){toast(t('Сервер не ответил: ')+e);uiLog('gdrive: '+e);}
  finally{if(btn)btn.disabled=false;}
}
// Что показать в строке статуса. Пока rclone не напечатал статистику (запуск,
// поиск файла на диске) — его же текст и время: молчащая строка читается как
// «зависло», а скачивание гигабайтов идёт минутами.
function gdLine(s){
  if(s.pct==null)return t(s.cur||'качаю…')+(s.elapsed?' · '+s.elapsed+t('с'):'');
  let x=t('скачано {p}% — {a} из {b}',{p:s.pct,a:s.bytes,b:s.total});
  if(s.n>1)x+=' · '+t('файл {n} из {m}',{n:s.i,m:s.n});
  if(s.speed)x+=' · '+s.speed;
  if(s.eta&&s.eta!=='-')x+=' · '+t('осталось {x}',{x:s.eta});
  if(s.file)x+=' · '+s.file;
  return x;
}
async function gdTick(){
  let s;try{s=await (await fetch('/api/gdrive_status?since='+GDLOGN)).json();}catch(e){return;}
  if((s.log_total||0)<GDLOGN)GDLOGN=0;                    // сервер начал лог заново
  (s.log||[]).forEach(l=>uiLog(t('гугл-диск: ')+fmtLog(l)));      // строки rclone — в общий лог страницы
  GDLOGN=s.log_total||GDLOGN;
  const st=$('gdrive_status');if(!st)return;
  if(s.running){st.textContent=gdLine(s);return;}
  clearInterval(GDN);GDN=0;
  // упавший rclone не выдаём за успех: раньше «скачивание завершено» писалось
  // на любом исходе, а следом список файлов затирал и это
  const bad=!!(s.done&&s.failed), stopped=!!(s.done&&s.cancelled);
  if(stopped){st.textContent=t('скачивание остановлено');}   // «Стоп» нажал человек — это не ошибка
  else if(bad){st.textContent='✗ '+t(s.cur||'скачивание не удалось');toast('⚠ '+t('Скачивание не удалось — смотри лог'));}
  else st.textContent=s.done?t('скачивание завершено'):'';
  gdFiles(bad||stopped);      // при отмене и ошибке список файлов статус не перебивает
}
function gdPoll(){clearInterval(GDN);gdTick();GDN=setInterval(gdTick,1000);}
// После F5 скачивание продолжает идти на сервере — страница должна к нему вернуться,
// иначе статус пустой, а файлы «сами появляются» в папке.
async function gdResume(){
  let s;try{s=await (await fetch('/api/gdrive_status?since=0')).json();}catch(e){return;}
  if(!s.running)return;
  GDLOGN=s.log_total||0;        // накопленный лог заново не вываливаем
  gdPoll();
}
async function gdFiles(quiet){
  const dest=$('gdrive_dest').value.trim();if(!dest)return;
  let fd;try{fd=await (await fetch('/api/files?dir='+encodeURIComponent(dest))).json();}catch(e){return;}
  const sel=$('gdrive_cam1');if(!sel)return;
  sel.innerHTML='';
  (fd.files||[]).forEach(f=>{const o=document.createElement('option');o.textContent=f;sel.appendChild(o);});
  if(!quiet&&fd.files&&fd.files.length)$('gdrive_status').textContent=t('скачано файлов: {n}',{n:fd.files.length});
}
document.addEventListener('DOMContentLoaded',gdResume);
async function gdSpread(){
  const n=nCams();if(n<2){toast(t('Раскладка нужна минимум двум камерам'));return;}
  const dest=$('gdrive_dest').value.trim();
  const s=$('gdrive_cam1');
  if(!dest){toast(t('Не задана папка загрузки'));return;}
  if(!s||!s.value){toast(t('Сначала выбери скачанный файл камеры 1'));return;}
  const btn=$('gd_spread');if(btn)btn.disabled=true;
  try{
    // Весь материал в одной папке — она и есть папки всех камер этой пары:
    // иначе cam1 не найдётся по пути папки камеры 1. Подбор по звуку — тот же
    // /api/cammatch, что и на «Подбор по звуку».
    const d=await (await fetch('/api/cammatch',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({cam1dir:dest,cam1:s.value,dirs:Array(n-1).fill(dest)})})).json();
    if(d.error){toast('⚠ '+errText(d));uiLog(t('раскладка: ')+d.error);return;}
    const files=(await (await fetch('/api/files?dir='+encodeURIComponent(dest))).json()).files||[];
    for(let k=0;k<n;k++){CAMDIRS[k]=dest;CAMFILES[k]=files;}
    buildCamRows();   // пересобрать селекты под папку загрузки, иначе в них старые списки
    const sel0=$('sel'+0);if(sel0)sel0.value=s.value;
    // одна папка вместо отдельных камер: один и тот же «лучший» файл не занимает
    // две камеры разом — нашедшиеся уникальные подставляем, остальные доукомплектуй
    const used=new Set([s.value]);let found=1;
    (d.matches||[]).forEach((m,k)=>{
      if(!m.name||used.has(m.name))return;
      used.add(m.name);found++;
      const sel=$('sel'+(k+1));if(sel)sel.value=m.name;
      uiLog(t('раскладка: Камера {n}: {name} (увер. {c})',{n:k+2,name:m.name,c:m.score}));
    });
    if(found===n)addPair();
    else uiLog(t('раскладка: нашлось {n} из {m} камер — доукомплектуй остальные и нажми «В очередь»',{n:found,m:n}));
    saveState();
    // Сборка превью-прокси к моменту просмотра: 4:2:2 10-бит жуется
    // софтом, и лучше начать заранее. Единственный клип — чей он, очевидно;
    // их несколько — прокси поднимутся сами при открытии предпросмотра.
    if(CLIPS.length===1)pvProxyLoad(CLIPS[0].xml,true);
  }catch(e){toast(t('Сервер не ответил: ')+e);uiLog('раскладка: '+e);}
  finally{if(btn)btn.disabled=false;}
}
async function clearQueue(){
  if(QUEUE.length>2&&!await askConfirm(t('Очистить очередь? В ней {n} {pairs}.',{n:QUEUE.length,pairs:t(plur(QUEUE.length,'пара','пары','пар'))})))return;
  QUEUE=[];renderQueue();saveState();}
function renderQueue(){const tb=document.querySelector('#qt tbody');if(!tb)return;
  // Пустая очередь — не пустая таблица с одной шапкой «# Камеры», а объяснение:
  // пары добавляются кнопками рядом. Как только пара появилась, таблица возвращается.
  const qe=$('qtempty'),qt=$('qt');
  if(!QUEUE.length){tb.innerHTML='';
    if(qt)qt.style.display='none';
    if(qe)qe.style.display='';
    return;}
  if(qt)qt.style.display='';
  if(qe)qe.style.display='none';
  tb.innerHTML='';
  QUEUE.forEach((p,i)=>{const tr=document.createElement('tr');
    // tabindex/role: без них пару из очереди нельзя было убрать без мыши
    tr.innerHTML='<td class="mono">'+(i+1)+'</td><td>'+esc(p.join('  +  '))+'</td>'
      +'<td class="x" tabindex="0" role="button" aria-label="'+t('Убрать пару из очереди')+'" data-t="'+t('Убрать пару из очереди')+'" '
      +'onclick="QUEUE.splice('+i+',1);renderQueue();saveState()">'+ico('x')+'</td>';tb.appendChild(tr);});}

// ================= CLIPS (unified list) =================
let CLIPS=[];   // {xml,name,status:{subs,colored},inserts:[],job:{...}}
// Отметка клипа галочкой — ОДНА на все три шага: галка на шаге 1 это та же
// c.sel, что на разметке и сборке. SEL_ANCHOR — индекс последней кликнутой галки, от него
// Shift считает диапазон; после удаления список перерисован и точки отсчёта больше нет —
// сбрасывается в -1 (см. delClips и removeSelClips).
let SEL_ANCHOR=-1;
// Shift отслеживаем сами: у события change модификаторов нет (это не MouseEvent), а
// синтетический клик, которым браузер помечает <label>, их теряет вовсе. Залипший Shift
// снимаем на blur — потерянный keyup иначе оставил бы диапазон включённым навсегда.
let SHIFT_HELD=false;
if(typeof document!=='undefined'){
  document.addEventListener('keydown',e=>{if(e.key==='Shift')SHIFT_HELD=true;});
  document.addEventListener('keyup',e=>{if(e.key==='Shift')SHIFT_HELD=false;});
}
if(typeof window!=='undefined'){
  window.addEventListener('blur',()=>{SHIFT_HELD=false;});
}
function clipByXml(xml){return CLIPS.find(c=>c.xml===xml);}
// Тег спикера ставится ЗДЕСЬ, в ЕДИНСТВЕННОЙ точке рождения клипа:
// после нарезки спикер точно тот, с которым резали; «Из папки результата» берёт
// папку из ai_outdir, а это папка спикера; «Добавить XML…» — догадка, тег поэтому
// исправим на шаге 3. «Не выбран» — запасной путь, работает как всегда.
function newClip(xml){const j=defJob();
  // Спикер уже выбран на шаге 1 — тег ставится здесь; его стиль и есть стиль клипа
  // (профиль — источник, пока клип не помечен «свой стиль»).
  const spk=val('speaker');
  if(spk){j.speaker=spk;
    const p=SPEAKERS[spk];
    if(p&&p.style&&STYLES[p.style]){j.styleKey=p.style;}}
  return {xml,name:(xml||'').replace(/^.*[\\\/]/,''),status:{},inserts:[],job:j,edited:false};}
function addExternal(){fetch('/api/pickfiles').then(r=>r.json()).then(d=>{
  (d.paths||[]).forEach(x=>{if(!clipByXml(x))CLIPS.push(newClip(x));});renderClips1();saveState();refreshStatuses();});}
async function scanOutdir(){const dir=val('ai_outdir').trim();
  if(!dir){toast(t('Не задана папка результата'));return;}
  try{const d=await (await fetch('/api/scanxml',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dir})})).json();
    if(d.error){toast(errText(d));return;}
    let added=0;(d.paths||[]).forEach(x=>{if(!clipByXml(x)){CLIPS.push(newClip(x));added++;}});
    toast(added?t('Добавлено из папки: {n}',{n:added}):t('Новых XML в папке нет'));
    renderClips1();saveState();refreshStatuses();
  }catch(e){toast(t('Не прочитал папку результата — сервер не ответил. Проверь, что webui запущен'));uiLog('scanxml: '+e);}}

// ---- run cut (ИИ или классика — общий поллинг) ----
let CUTLABEL=t('Нарезка');
function cutBusy(on){uiBusySet(on);}
// Единый флаг «длинная задача уже идёт». Оверлей прогресса кликам мешает, но у него есть
// кнопка «Свернуть» — после неё страница снова кликабельна, и второй клик по «Разметить
// всё» / «Собрать набор» запускал ВТОРОЙ цикл параллельно в те же XML (аудит, B4).
let UIBUSY=false;
function uiBusySet(on){UIBUSY=on;
  ['markupall','buildbtn','runai','runclassic','introall'].forEach(id=>{const b=$(id);if(b)b.disabled=on;});}
function uiBusyGuard(){if(!UIBUSY)return false;
  toast(t('Задача уже идёт — дождись конца или нажми «Остановить»'));progMaxi();return true;}
async function runAI(){
  if(uiBusyGuard())return;
  if(!QUEUE.length){toast(t('Очередь пуста'));return;}
  if(!val('ai_outdir').trim()){toast(t('Не задана папка результата'));return;}
  if(CAMDIRS.slice(0,nCams()).some(d=>!(d||'').trim())){toast(t('Не заданы папки камер'));return;}
  cutBusy(true);CUTLABEL=t('ИИ-нарезка');progShow(t('ИИ-нарезка'),t('готовлю…'));
  // selfcheck не шлём: в режиме GigaAM (дефолт) этот путь не исполняется вовсе,
  // сервер сам ставит False (см. run_omnicut_job). Убрано 2026-08-11.
  const body={outdir:val('ai_outdir'),pairs:QUEUE,camdirs:CAMDIRS.slice(0,nCams()),
    speaker:val('speaker'),
    review:$('chk_review')?$('chk_review').checked:false};
  let d;try{d=await (await fetch('/api/omnicut_run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();}
  catch(e){toast(t('Сервер не ответил: ')+e);cutBusy(false);hideProg();return;}
  if(d.error){toast(errText(d));cutBusy(false);hideProg();return;}
  logReset();pollAI();
}
async function runCustom(){
  if(uiBusyGuard())return;
  if(!QUEUE.length){toast(t('Очередь пуста'));return;}
  if(!val('ai_outdir').trim()){toast(t('Не задана папка результата'));return;}
  if(CAMDIRS.slice(0,nCams()).some(d=>!(d||'').trim())){toast(t('Не заданы папки камер'));return;}

  const pausesVal=(CUT_STAGES&&CUT_STAGES.pauses)||(CUT_DEFAULTS&&CUT_DEFAULTS.pauses)||'speech';
  const branch=(pausesVal==='loud')?'vad':'gigaam';

  if(branch==='vad'){
    if(!val('base').trim()){toast(t('Не задана папка проекта'));return;}
    cutBusy(true);CUTLABEL=t('Кастомная нарезка');progShow(t('Кастомная нарезка'),'Whisper + VAD…');
    const body={
      base:val('base'),
      outdir:val('ai_outdir'),
      pairs:QUEUE,
      camdirs:CAMDIRS.slice(0,nCams()),
      stages:CUT_STAGES,
      thresholds:CUT_THRESHOLDS
    };
    let d;try{d=await (await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();}
    catch(e){toast(t('Сервер не ответил: ')+e);cutBusy(false);hideProg();return;}
    if(d.error){toast(errText(d));cutBusy(false);hideProg();return;}
    logReset();pollAI();
  }else{
    cutBusy(true);CUTLABEL=t('Кастомная нарезка');progShow(t('Кастомная нарезка'),t('готовлю…'));
    const body={
      outdir:val('ai_outdir'),
      pairs:QUEUE,
      camdirs:CAMDIRS.slice(0,nCams()),
      speaker:val('speaker'),
      stages:CUT_STAGES,
      review:$('chk_review')?$('chk_review').checked:false
    };
    let d;try{d=await (await fetch('/api/omnicut_run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();}
    catch(e){toast(t('Сервер не ответил: ')+e);cutBusy(false);hideProg();return;}
    if(d.error){toast(errText(d));cutBusy(false);hideProg();return;}
    logReset();pollAI();
  }
}
const runClassic=runCustom;
// ---- единое ядро поллинга джоба: /api/status → лог → прогресс → onDone(d) ----
// eager: доля «внутри текущего клипа» для бара (0.15 нарезка, 0.3 сборка); null = без бара.
// Имя клипа приходит с сервера (progress.name) и кладётся в контекст очереди — тогда строка
// очереди у серверных задач та же, что у клиентских («клип i из N · имя» + этап).
function jobProg(d,eager,title){
  const p=(d.progress&&d.progress.n)?{i:+d.progress.i,n:+d.progress.n,name:d.progress.name||''}
        :(PROGMARK?{i:+PROGMARK[1],n:+PROGMARK[2],name:''}:null);        // фолбэк: [i/N] из лога
  if(!p)return{frac:null,sub:''};
  progQueue(title,p.i,p.n,p.name);
  if(eager==null)return{frac:null,sub:''};
  return{frac:(p.i-1)/p.n+eager/p.n,sub:''};}
// onTick(d) зовётся на КАЖДОМ опросе (не только в конце) — нарезка так отдаёт
// готовые клипы по одному, не дожидаясь всей очереди.
// Сбои fetch подряд. Ретраим как раньше, но обрыв связи не должен выглядеть
// работой: после трёх провалов подряд в оверлее видно «сервер не отвечает — жду…»
// (задание по UI-состояниям). Удачный ответ — счётчик в ноль, обычный текст сам
// возвращается следующим progUpdate.
let POLLFAIL=0;
async function pollJob(self,title,eager,onDone,onTick){
  // сеть/рестарт сервера не должны молча убивать поллинг (кнопки остались бы залоченными) — ретраим
  let d;try{d=await (await fetch('/api/status?since='+LOGSINCE)).json();}
  catch(e){POLLFAIL++;if(POLLFAIL>=3)progUpdate(null,t('сервер не отвечает — жду…'),title,undefined);
    setTimeout(self,1500);return;}
  POLLFAIL=0;
  mergeLog(d);
  queueRender(d);    // очередь этапов: единая дверь для нарезки/сборки/черновика
  if(onTick)onTick(d);
  const last=[...LOGCACHE].map(fmtLog).reverse().find(l=>l.trim());
  const pr=jobProg(d,eager,title);
  progUpdate(pr.frac,(last||'').trim().slice(0,80),title,pr.sub);
  if(d.done){onDone(d);return;}
  setTimeout(self,1000);}

// Клип попадает в список шага 1 СРАЗУ, как только он нарезан: сервер пополняет
// JOB["results"] после каждого файла, а ждать конца всей очереди, чтобы начать
// править, незачем (просьба юзера). Возвращает, сколько клипов добавилось.
function cutAdopt(d){
  const od=val('ai_outdir').replace(/[\\\/]+$/,'');
  if(!od)return 0;
  let n=0;
  (d.results||[]).forEach(x=>{const full=od+'\\'+x;
    if(clipByXml(full))return;
    CLIPS.push(newClip(full));n++;
    uiLog(t('нарезан: {x} — можно править, не дожидаясь остальных',{x:x}));});
  if(n){renderClips1();saveState();refreshStatuses();}
  return n;}

async function pollAI(){pollJob(pollAI,CUTLABEL,0.15,d=>{
    cutBusy(false);
    cutAdopt(d);
    const n=(d.results||[]).length,bad=d.failed||[];
    bad.forEach(f=>uiLog(t('✗ не собрался {name}: {reason}',{name:f.name,reason:f.reason||t('см. логи')})));
    let msg=UICANCEL?t('Остановлено — готово клипов: ')+n:(n?t('Готово: {n} клип(ов)',{n:n}):(bad.length?'':t('Готово (файлов нет — см. логи)')));
    if(bad.length)msg+=(msg?' · ':'')+t('⚠ не собрались ({n}): ',{n:bad.length})
      +bad.map(f=>f.name+' — '+(f.reason||'?').slice(0,90)).join(' · ');
    progDone(msg);
    if(bad.length)$('progFill').className='progfill';   // не красим в зелёный, если были падения
    renderClips1();saveState();refreshStatuses();},
  d=>{cutAdopt(d);progReadySet((d.results||[]).length);});}

// ---- render clip lists ----
function clipActs(i,step){const c=CLIPS[i];
  let a='<div class="acts">';
  if(step===1){
    // имя действия — в aria-label, справка — в data-t (единый #tipbox, работает и по фокусу);
    // нативный title тут показывал подсказку только мыши и через секунду
    a+='<button class="icon" aria-label="'+t('Правка / просмотр')+'" data-t="'+t('Правка / просмотр')+'" onclick="openEditClip('+i+')">'+ico('pencil')+'</button>';
    const nc=c.status&&c.status.ncams;   // одна камера — распределять нечего, кнопку прячем (пока ncams неизвестно — показываем, openCamsFor сам гардит)
    if(nc==null||nc>1)a+='<button class="icon" aria-label="'+t('Раскладка камер')+'" data-t="'+t('Раскладка камер (какая активна на каждом куске)')+'" onclick="openCamsFor('+i+')">'+ico('cam')+'</button>';
    // не /api/media: export_xml на лету проставляет настоящие таймкоды исходников —
    // без них DaVinci Resolve раскладывает нарезку со сдвигом в часы (Премьеру всё равно)
    a+='<a class="icon" aria-label="'+t('Скачать XML')+'" data-t="'+t('Скачать XML — открывается и в Premiere, и в DaVinci Resolve')+'" href="/api/export_xml?path='+encodeURIComponent(c.xml)+'" style="text-decoration:none;padding:5px 8px;color:var(--mut)">'+ico('dl')+'</a>';
    a+='<button class="icon" aria-label="'+t('Удалить клип')+'" data-t="'+t('Удалить клип: убрать из списка или стереть с диска со всеми сайдкарами')+'" onclick="delClip('+i+')">'+ico('x')+'</button>';
  }else if(step===2){
    a+='<button class="sm" onclick="markupOne('+i+')">'+ico('ai','gold')+' '+markupOneLabel(c)+'</button>';
    a+='<button class="icon" aria-label="'+t('Вставки')+'" data-t="'+t('Вставки: подбор ИИ, свой файл, база')+'" onclick="openInsertsFor('+i+')">'+ico('pencil')+'</button>';
    a+=dlIcon(i);
  }
  a+='</div>';return a;}
function clipReady(c){const s=c.status||{};return s.subs>0&&s.colored>0;}
function markupOneLabel(c){
  const s=c.status||{}, ins=c.inserts||[];
  const missing=ins.length>0&&ins.some(x=>!(x.media||'').trim());
  if(s.subs>0&&s.colored>0&&missing)return t('Добрать вставки');
  if(s.subs>0&&s.colored>0&&!missing)return t('Проверить');
  if(s.subs>0||s.colored>0||ins.length)return t('Продолжить');
  return t('Разметить');
}
// Формат скачивания таймлайна выбирается в ⚙ → «Инструменты» → «Экспорт» (#dlfmt) и хранится
// между загрузками; первое скачивание спрашивает его один раз (см. dlClip).
function dlFmtGet(){try{return localStorage.getItem('reelsi_dl_fmt')||'xml';}catch(e){return 'xml';}}
function dlFmtSet(v){try{localStorage.setItem('reelsi_dl_fmt',v);}catch(e){}
  document.querySelectorAll('#dlfmt').forEach(s=>{if(s.value!==v)s.value=v;});}
document.addEventListener('DOMContentLoaded',()=>{const f=dlFmtGet();
  document.querySelectorAll('#dlfmt').forEach(s=>{s.value=f;});});
// Кнопка-иконка «Скачать» у клипа: шаг 1 — только XML (там ни вставок, ни титров);
// шаг 2 — по выбранному формату (XML / .drp). .drp собирается на сервере
// из того же исходника + вставок (в XML их нет — живут в состоянии UI).
function dlIcon(i){const c=CLIPS[i];
  return '<a class="icon" tabindex="0" role="button" aria-label="'+t('Скачать')+'" data-t="'+t('Скачать таймлайн в ')+(dlFmtGet()==='drp'?'.drp (DaVinci Resolve)':'XML')+'" onclick="dlClip('+i+')" style="text-decoration:none;padding:5px 8px;color:var(--mut);cursor:pointer">'+ico('dl')+'</a>';}
// Формат выбирается в ⚙ → «Общее». Первое скачивание (формат ещё ни разу не выбирался —
// нет ключа reelsi_dl_fmt) спрашивает один раз в маленьком окне, дальше — как выбрано.
let DLFMT_WANT=-1;   // индекс клипа, с которого пришёл первый спрос
function dlPickFmt(v){closeModal('mbDlFmt');dlFmtSet(v);
  const i=DLFMT_WANT;DLFMT_WANT=-1;if(i>=0)dlClip(i);}
function dlClip(i){const c=CLIPS[i];if(!c)return;
  let asked=null;try{asked=localStorage.getItem('reelsi_dl_fmt');}catch(e){}
  if(asked===null){DLFMT_WANT=i;openModal('mbDlFmt');return;}   // не спрашивали — спросить, ответ запомнится
  if(dlFmtGet()==='drp'){
    const body={xml:c.xml,inserts:(c.inserts||[]).filter(x=>(x.media||'').trim())};
    fetch('/api/export_drp',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
      .then(async r=>{if(!r.ok){let e=r.status===404?t('не найден'):r.status===403?t('запрещено'):'',ed=null;try{const b=await r.json();ed=b;e=b.error||e}catch(_){}
          throw new Error((ed&&errText(ed))||e||t('сборка .drp не удалась ')+r.status);}
        const conv=await r.blob();const u=URL.createObjectURL(conv);
        const a=document.createElement('a');a.href=u;
        a.download=(c.name||'timeline').replace(/\.xml$/i,'')+'.drp';
        document.body.appendChild(a);a.click();a.remove();
        setTimeout(()=>URL.revokeObjectURL(u),4000);})
      .catch(e=>toast(t('Не скачалось .drp: ')+e.message));
  }else{
    location.href='/api/export_xml?path='+encodeURIComponent(c.xml);
  }}
function editedTag(c){return c.edited?'<span class="tag ok" data-t="'+t('Нарезка правлена вручную')+'">'+t('правлено')+'</span>':'';}
// Теги статуса. На шаге 2 (del=true) у КАЖДОГО непустого тега появляется крестик при
// наведении — снести именно эту часть разметки, не трогая остальные (см. clearPart).
function statusTags(c,del){
  const s=c.status||{};const ins=c.inserts||[];const chosen=ins.filter(x=>(x.media||'').trim()).length;
  const i=CLIPS.indexOf(c);
  const x=(part,hint)=>del?('<span class="tagx" data-t="'+esc(hint)+'" aria-label="'+esc(hint)+'" onclick="event.stopPropagation();clearPart('+i+',\''+part+'\')" tabindex="0" role="button">'+ico('x')+'</span>'):'';
  let out=editedTag(c);
  out+='<span class="tag'+(s.subs>0?' ok':'')+'">'+t('субтитры')+(s.subs>0?ico('check'):'')
    +(s.subs>0?x('subs',t('Убрать субтитры из XML (жёлтые уйдут вместе с ними — они живут цветом на словах)')):'')+'</span>';
  out+='<span class="tag'+(s.colored>0?' ok':'')+'">'+t('жёлтые')+(s.colored>0?(' '+s.colored):'')
    +(s.colored>0?x('yellow',t('Снять все жёлтые — субтитры останутся')):'')+'</span>';
  if(ins.length)out+='<span class="tag on">'+t('вставок {chosen} из {total}',{chosen:chosen,total:ins.length})
    +x('inserts',t('Удалить все вставки этого клипа (файлы на диске не трогаем)'))+'</span>';
  return out;}
// Точечная очистка с шага 2. Субтитры сносим пересборкой XML (как было до gen_subs),
// жёлтые — пустым set_yellow, вставки живут только в состоянии UI.
async function clearPart(i,part){const c=CLIPS[i];if(!c)return;
  const NAME={subs:t('субтитры (и жёлтые вместе с ними)'),yellow:t('жёлтые слова'),inserts:t('все вставки')};
  if(!await askConfirm(t('Удалить {what} у «{name}»?',{what:NAME[part],name:c.name})))return;
  try{
    if(part==='inserts'){
      c.inserts=[];c.insTarget=0;if(c.job)c.job.ins=[];
      if(curIns===i)renderInsHost();
      uiLog(t('очистка: вставки убраны ({name})',{name:c.name}));
    }else if(part==='yellow'){
      const d=await (await fetch('/api/set_yellow',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({xml:c.xml,indices:[]})})).json();
      if(d.error)throw errText(d);
      c.status=c.status||{};c.status.colored=0;clearHl(c);
      uiLog(t('очистка: жёлтые сняты ({name})',{name:c.name}));
    }else{
      const d=await (await fetch('/api/clear_subs',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({xml:c.xml})})).json();
      if(d.error)throw errText(d);
      c.status=c.status||{};c.status.subs=0;c.status.colored=0;clearHl(c);
      uiLog(t('очистка: субтитры и жёлтые убраны ({name}, {segs} кусков пересобрано)',{name:c.name,segs:d.segs}));
    }
    saveState();syncClipLists();
  }catch(e){toast('⚠ '+e);uiLog(t('очистка ({part}): ОШИБКА — ',{part:part})+e);}}
// Клик по галке: сам клип плюс (с Shift) ВЕСЬ диапазон от прошлой кликнутой галки до этой —
// состояние берётся у текущей. Три списка рисуют одну и ту же c.sel, поэтому после правки
// перерисовываем все три: иначе соседние галки в других списках разъедутся с состоянием.
// saveState/syncBuildBtn/markupSelCount остаются в onchange — ровно как было до диапазона.
// Списки перерисовываются целиком (innerHTML=''), поэтому галка, с которой человек работал
// с клавиатуры, к моменту возврата уже выброшена из документа — вместе с ней уходил и фокус:
// Tab/Space начинали с начала страницы, а Shift-выбор диапазона с клавиатуры рвался.
// Поэтому галку и её список запоминаем ДО перерисовки и возвращаем фокус на ту же галку
// (новый элемент) в том же списке. Список различаем по контейнеру строки клипа, клип — по
// data-ci: имя списка строкой сломалось бы при первом же переименовании id. Фокус мог стоять
// и не на галке (клик мышью по пустому месту) — тогда его не трогаем: перетаскивать фокус на
// чужую галку нельзя.
function pickClip(i,on,shift){
  if(!CLIPS[i])return;
  const el=document.activeElement,row=el&&el.dataset&&el.dataset.ci!==undefined
    ?(el.closest&&el.closest('[data-clips]')):null;
  const from=row&&document.contains(el)?{list:row.getAttribute('data-clips'),ci:el.dataset.ci}:null;
  CLIPS[i].sel=!!on;
  const a=SEL_ANCHOR;
  if(shift&&a>=0&&a<CLIPS.length&&a!==i){
    const lo=Math.min(a,i),hi=Math.max(a,i);
    for(let k=lo;k<=hi;k++)CLIPS[k].sel=!!on;
  }
  SEL_ANCHOR=i;
  renderClips1();renderClips2();renderClips3();
  // preventScroll — без него браузер дёрнул бы страницу к галке, которую человек и так видит
  const back=from&&document.querySelector('[data-clips="'+from.list
    +'"] input[type=checkbox][data-ci="'+from.ci+'"]');
  if(back&&back.focus)back.focus({preventScroll:true});}
// Кнопки-корзины «Удалить выбранные» в шапках трёх списков. Выбор общий, поэтому и кнопок
// три, но правило одно: пусто у всех ≠ все (в отличие от сборки) — неотмеченное не удаляем.
function syncDelSel(){const n=CLIPS.filter(c=>c.sel).length;
  document.querySelectorAll('[data-delsel]').forEach(b=>{b.disabled=!n;});}
// data-clips на контейнере — признак списка, в котором стоит галка: по нему pickClip после
// перерисовки находит тот же список (галки трёх шагов живут в разных контейнерах).
function renderClips1(){const h=$('clips1');if(!h)return;h.innerHTML='';
  if(!CLIPS.length){h.innerHTML='<div class="empty">'+t('Пока пусто. Запусти ИИ-нарезку или добавь XML.')+'</div>';syncDelSel();syncNav();return;}
  CLIPS.forEach((c,i)=>{const el=document.createElement('div');el.className='clip';
    el.innerHTML='<label class="pickbox" data-t="'+t('Выбор клипов — общий для всех шагов')+'" onclick="event.stopPropagation()">'
      +'<input type="checkbox" data-ci="'+i+'" '+(c.sel?'checked':'')+' onchange="CLIPS['+i+'].sel=this.checked;pickClip('+i+',this.checked,SHIFT_HELD);saveState();syncBuildBtn();markupSelCount()"></label>'
      +'<span class="idx">'+(i+1)+'</span><span class="nm grow" data-noi18n title="'+esc(c.xml)+'">'+esc(c.name)+'</span>'
      +'<span class="st">'+spkTagHTML(c)+(c.edited?'<span class="st">'+editedTag(c)+'</span>':'')+'</span>'+clipActs(i,1);
    h.appendChild(el);});
  syncDelSel();syncNav();}
function renderClips2(){const h=$('clips2');if(!h)return;h.innerHTML='';
  if(!CLIPS.length){h.innerHTML='<div class="empty">'+t('Клипов нет — вернись на ')+'<span class="lnk" tabindex="0" role="button" onclick="goStep(1)">'+t('шаг 1')+'</span>'+t(' и запусти нарезку или подхвати XML кнопкой «Из папки результата».')+'</div>';markupSelCount();syncDelSel();return;}
  // Блок клипа НЕ красим зелёным целиком (просьба юзера): и так видно по тегам, а
  // сплошная заливка глушила разницу между «готово» и «сейчас работаю».
  // Зелёные остаются только сами теги — мелкие маркеры «поработал».
  CLIPS.forEach((c,i)=>{const el=document.createElement('div');el.className='clip';
    el.innerHTML='<label class="pickbox" data-t="'+t('Фазы разметки у выбранных клипов (пусто у всех = все). Отмеченное здесь — то же, что и на шаге сборки.')+'" onclick="event.stopPropagation()">'
      +'<input type="checkbox" data-ci="'+i+'" '+(c.sel?'checked':'')+' onchange="CLIPS['+i+'].sel=this.checked;pickClip('+i+',this.checked,SHIFT_HELD);saveState();syncBuildBtn();markupSelCount()"></label>'
      +'<span class="idx">'+(i+1)+'</span><span class="nm" data-noi18n title="'+esc(c.xml)+'">'+esc(c.name)+'</span>'
      +'<span class="st grow">'+statusTags(c,true)+'</span>'+clipActs(i,2);
    h.appendChild(el);});
  markupSelCount();syncDelSel();}
// Счётчик «(N)» на кнопках фаз шага 2: сколько клипов уйдёт в фазу (не отмечено ни
// одного = все, как selClips). Обновляется при клике по галочке — как syncBuildBtn.
function markupSelCount(){const n=CLIPS.filter(c=>c.sel).length||CLIPS.length;
  document.querySelectorAll('[data-markupcnt]').forEach(e=>{e.textContent=n;});}
function renderClips3(){const h=$('clips3');if(!h)return;h.innerHTML='';
  if(!CLIPS.length){h.innerHTML='<div class="empty">'+t('Клипов нет — вернись на ')+'<span class="lnk" tabindex="0" role="button" onclick="goStep(1)">'+t('шаг 1')+'</span>'+t(' и запусти нарезку или подхвати XML кнопкой «Из папки результата».')+'</div>';syncBuildBtn();syncDelSel();return;}
  CLIPS.forEach((c,i)=>{const el=document.createElement('div');
    el.className='clip pick'+(aeDone(c)?' done':clipReady(c)?' ready':'')+(i===curAE?' cur':'');
    el.innerHTML='<label class="pickbox" data-t="'+t('В сборку набора (пусто у всех = собрать все)')+'" onclick="event.stopPropagation()">'
      +'<input type="checkbox" data-ci="'+i+'" '+(c.sel?'checked':'')+' onchange="CLIPS['+i+'].sel=this.checked;pickClip('+i+',this.checked,SHIFT_HELD);saveState();syncBuildBtn()"></label>'
      +'<span class="idx">'+(i+1)+'</span>'+spkSelHTML(i,c)
      +'<span class="nm grow" data-noi18n title="'+esc(c.xml)+'">'+esc(c.name)+'</span>'
      +'<span class="st">'+spkTagHTML(c,false)+statusTags(c)+(aeDone(c)?'<span class="tag ok">'+t('готов к AE')+ico('check')+'</span>':'')+'</span>'
      +'<span class="openhint hint">'+t('открыть →')+'</span>';
    el.tabIndex=0;el.setAttribute('role','button');   // строка кликабельна целиком — значит и фокусируема
    // Клик по строке = открыть предпросмотр ЭТОГО файла (интро/жёлтые/вставки/стиль в одном окне).
    // Раньше клик только выбирал клип, а предпросмотр надо было искать кнопкой ниже.
    el.onclick=()=>openAEFor(i);h.appendChild(el);});
  syncBuildBtn();syncDelSel();}
// Тег спикера на клипе: как он влияет на стиль/папки/пороги, видно по селектору.
// Клип ставится в очередь на шаге 1 при выбранном спикере — тег ставится там же.
function spkSelHTML(i,c){const k=(c.job||{}).speaker||'';
  let o='<button type="button" role="option" data-k="" aria-selected="'+(k===''?'true':'false')+'">'+t('не выбран')+'</button>';
  Object.keys(SPEAKERS).forEach(key=>{const s=SPEAKERS[key];
    o+='<button type="button" role="option" data-k="'+esc(key)+'" aria-selected="'+(key===k?'true':'false')+'">'+esc(s.label||key)+'</button>';});
  const label=k&&SPEAKERS[k]?(SPEAKERS[k].label||k):t('Спикер не выбран');
  // Свой список кнопок вместо нативного селекта: у него варианты открывались ВТОРЫМ
  // кликом — системным чёрным попапом, который стилями не красится. Теперь один клик по
  // summary раскрывает список, второй клик — это уже сам выбор.
  return '<details class="spkpick" onclick="event.stopPropagation()" onkeydown="event.stopPropagation()" aria-label="'+t('Спикер клипа')+'"><summary data-t="'+t('Спикер этого клипа: меняет его стиль, папку .jsx и пороги нарезки.')+'">'+esc(label)+'<span aria-hidden="true">⌄</span></summary><div class="opts" role="listbox" aria-label="'+t('Спикер клипа')+'" onclick="var b=event.target.closest(&quot;button&quot;);if(!b)return;setClipSpeaker('+i+',b.dataset.k);this.closest(&quot;details&quot;).open=false">'+o+'</div></details>';}
// withName=false — только метки (шаг 3: имя уже видно по селектору спикера, дублировать
// его тегом нельзя — юзер просил «не надо второй раз писать кто это»); на шагах 1-2
// селектора нет, там имя остаётся.
function spkTagHTML(c,withName){const k=(c.job||{}).speaker||'';
  const sp=SPEAKERS[k];
  let out='';
  if(withName!==false&&sp)out+='<span class="tag" data-t="'+esc(t('Спикер: {n}',{n:sp.label||k}))+'">'+esc(sp.label||k)+'</span>';
  return out;}
// Смена тега у клипа на шаге 3: переезжают стиль (кроме «свой стиль»), папки и пороги
// нового спикера. Тег без профиля или снятие тега — запасной путь «не выбран» не ломаем.
function setClipSpeaker(i,key){
  const c=CLIPS[i];if(!c)return;
  c.job=c.job||defJob();const j=c.job;
  j.speaker=key||'';
  const spk=key?SPEAKERS[key]:null;
  if(spk){
    // Смена спикера ставит ИМЯ его стиля, копий больше нет.
    if(spk.style&&STYLES[spk.style]){j.styleKey=spk.style;}
    // Папки нового спикера ставим сразу — юзер сам только что выбрал этого человека.
    const set=(id,v)=>{const el=$(id);if(el&&v&&!samePath(el.value,v))el.value=v;};
    set('ai_outdir',(spk.outdir||'').trim());
    if((spk.jsxdir||'').trim())AEGLOBAL=(spk.jsxdir||'').trim();
  }
  if(curAE===i)selectAE(i);           // панель принадлежит этому клипу — перечитать его стиль
  else{saveState();renderClips3();}
  renderAeDirField();syncBuildBtn();
  uiLog(t('спикер клипа «{name}»: {n}',{name:c.name,n:(spk?spk.label:t('не выбран'))}));}
// один клик по файлу: выбрать + сразу открыть предпросмотр
// AEXML — а не только индекс: curAE переживает F5, а панель после перезагрузки пустая,
// и «тот же клип» открывался бы с чужими (дефолтными) вставками, интро и стилем
function openAEFor(i){if(curAE!==i||AEXML!==CLIPS[i].xml)selectAE(i);openAEPreview();}
function selClips(){const s=CLIPS.filter(c=>c.sel);return s.length?s:CLIPS;}
function syncBuildBtn(){const n=CLIPS.filter(c=>c.sel).length;
  const b=$('buildbtn');if(b)b.textContent=n?t('Собрать выбранные ({n})',{n:n}):t('Собрать набор');
  const scope=$('buildscope');if(scope)scope.textContent=n?t('Выбрано {n} из {m}',{n:n,m:CLIPS.length}):t('Все {n} клипов',{n:CLIPS.length});
  const rms=$('rmSelClips');if(rms)rms.disabled=!n;   // метла «убрать отмеченные» — только когда есть что убирать
  // Клипы ДВУХ спикеров в одном наборе собираются только по одному — каждый в свою
  // папку: «Собрать набор» и «Один на всё» для них не существуют.
  const sel=selClips();const spks=new Set(sel.map(c=>(c.job||{}).speaker||'').filter(Boolean));
  const mixed=spks.size>1;
  if(b)b.disabled=!!mixed;
  const co=document.querySelector('input[name=multimode][value=combined]');
  if(co){if(mixed&&co.checked){document.querySelector('input[name=multimode][value=separate]').checked=true;segUI();}
    co.disabled=!!mixed;}
  introAllCount();}
// сколько файлов уйдёт в пакетное ИИ-интро — те же галочки, что и в сборке (пусто у всех = все)
function introAllCount(){const n=CLIPS.filter(c=>c.sel).length||CLIPS.length;
  document.querySelectorAll('[data-introcnt]').forEach(e=>{e.textContent=n;});}
function syncNav(){const b=$('toStep2');if(b)b.disabled=!CLIPS.length;}
// Индекс открытого клипа (curAE), превью нарезки (curEdit) и редактора вставок (curIns)
// живут отдельно от списка: без правки они после splice показывали на чужой клип,
// а с последнего — в пустоту (см. защиту в captureAE).
function _spliceClip(i){
  CLIPS.splice(i,1);
  if(curAE===i)curAE=-1;else if(curAE>i)curAE--;
  if(curEdit===i)curEdit=-1;else if(curEdit>i)curEdit--;
  if(curIns===i)curIns=-1;else if(curIns>i)curIns--;
}

// Что удаляем: индексы, зафиксированные на момент открытия окна (галку могли снять, пока
// окно открыто, — удаляем ровно то, что человек выбрал и увидел в окне).
let DEL_CLIP_IDXS=[];
function clipLabel(c){return (c&&(c.name||(c.xml?c.xml.split(/[/\\]/).pop():'')))||'';}
// Одиночный крестик шага 1 — ТОТ ЖЕ путь, что у «Удалить выбранные»: список из одного
// индекса. Второй копии логики нет.
function delClip(i){
  // curAE / curEdit / curIns правятся при удалении в _spliceClip (общий путь delClips)
  delClips([i]);
}
// Окно удаления обобщено на список: заголовок — сколько клипов, в теле — их
// имена. Дальше выбор один: убрать из списка (_spliceClip по убыванию индексов) или стереть
// с диска (сухой прогон, потом удаление по каждому XML — бэкенд удаляет по одному).
function delClips(idxs){
  const list=(idxs||[]).filter(i=>i>=0&&i<CLIPS.length);
  if(!list.length)return;
  DEL_CLIP_IDXS=list;
  const title=list.length===1?t('Удалить клип?')
    :t('Удалить {n} {clips}?',{n:list.length,clips:t(plur(list.length,'клип','клипа','клипов'))});
  $('delClipTitle').textContent=title;
  $('delClipName').textContent=list.map(i=>clipLabel(CLIPS[i])).join(', ');
  $('delClipInitial').style.display='flex';
  $('delClipConfirm').style.display='none';
  openModal('mbDelClip');
}

// «Удалить выбранные» в шапке любого из трёх списков. Пусто у всех НЕ значит «все»
// (это семантика selClips для сборки): без галочек удалять нечего — кнопка и так погашена.
function delSelClips(){
  const idx=[];CLIPS.forEach((c,i)=>{if(c.sel)idx.push(i);});
  if(!idx.length){toast(t('Ничего не отмечено — поставь галочки у клипов, которые убрать'));return;}
  delClips(idx);
}

function delClipListOnly(){
  const list=DEL_CLIP_IDXS.slice();DEL_CLIP_IDXS=[];
  if(!list.length)return;
  list.slice().sort((a,b)=>b-a).forEach(i=>_spliceClip(i));   // с конца: splice не съест соседей
  SEL_ANCHOR=-1;   // список перерисован — прошлая кликнутая галка больше ни на что не указывает
  closeModal('mbDelClip');
  renderClips1();renderClips2();renderClips3();syncNav();
  saveState();
}

// Сухой прогон по КАЖДОМУ выбранному клипу: сводный список файлов и суммарный размер.
// Ошибка одного клипа не мешает прочитать остальные (в execute он всё равно попробуется).
async function delClipDiskPrepare(){
  const list=DEL_CLIP_IDXS.slice();
  if(!list.length)return;
  $('delClipInitial').style.display='none';
  $('delClipConfirm').style.display='flex';
  $('delClipCams').textContent=t('Загрузка списка файлов…');
  $('delClipInfo').textContent='';
  $('delClipList').innerHTML='';
  $('delClipDiskConfirmBtn').disabled=true;
  const files=[],cams=[],bad=[];
  let bytes=0;
  for(const i of list){
    const c=CLIPS[i];if(!c)continue;
    const jsxdir=(effOutdir(c)||AEGLOBAL||'').trim();
    try{
      const res=await fetch('/api/clip_delete',{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({xml:c.xml,jsxdir:jsxdir,dry:true})
      });
      const d=await res.json();
      if(d.error){bad.push(clipLabel(c)+': '+errText(d));continue;}
      (d.cams||[]).forEach(n=>{if(cams.indexOf(n)<0)cams.push(n);});
      (d.files||[]).forEach(f=>files.push(f));
      bytes+=d.bytes||0;
    }catch(e){bad.push(clipLabel(c)+': '+e);}
  }
  $('delClipCams').textContent=cams.length
    ?t('Исходник (НЕ трогаем): ')+cams.join(', ')
    :t('Исходное видео: не трогаем (удаляются только файлы нарезки)');
  const n=files.length;
  const mb=(bytes/1048576).toFixed(2);
  $('delClipInfo').textContent=t('Будет удалено файлов: {n} ({size} МБ)',{n:n,size:mb})
    +(bad.length?t(' · не прочитано клипов: {n}',{n:bad.length}):'');
  if(n>0){
    $('delClipList').innerHTML=files.map(f=>{
      const name=f.path.split(/[/\\]/).pop();
      const sz=(f.size>1048576?(f.size/1048576).toFixed(2)+t(' МБ'):(f.size/1024).toFixed(1)+t(' КБ'));
      // имя файла приходит с диска (материал мог приехать с гугл-диска) — в разметку
      // только через esc(): кавычка или «<» в имени иначе станут тегом
      return '<div style="display:flex;justify-content:space-between;gap:8px"><span>'+esc(name)+'</span><span style="flex:none;color:var(--tx)">'+sz+'</span></div>';
    }).join('');
  }else{
    $('delClipList').innerHTML='<div class="empty">'+t('Файлы нарезки не найдены на диске')+'</div>';
  }
  if(bad.length)uiLog(t('удаление с диска: не прочитано — ')+bad.join('; '));
  $('delClipDiskConfirmBtn').disabled=false;
}

function delClipDiskBack(){
  $('delClipInitial').style.display='flex';
  $('delClipConfirm').style.display='none';
}

// Удаление по каждому клипу: ошибка одного не прерывает остальные, итог — тост и журнал
// (сколько убрано и кто именно не удалился). Из списка убираем всех, кого пытались удалить:
// файлы могли остаться на диске — их подхватит «Из папки результата».
async function delClipDiskExecute(){
  const list=DEL_CLIP_IDXS.slice();DEL_CLIP_IDXS=[];
  if(!list.length)return;
  $('delClipDiskConfirmBtn').disabled=true;
  const bad=[];let nfiles=0,bytes=0;
  for(const i of list){
    const c=CLIPS[i];if(!c)continue;
    const jsxdir=(effOutdir(c)||AEGLOBAL||'').trim();
    try{
      const res=await fetch('/api/clip_delete',{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({xml:c.xml,jsxdir:jsxdir,dry:false})
      });
      const d=await res.json();
      if(d.error){bad.push(clipLabel(c)+': '+errText(d));continue;}
      nfiles+=(d.files||[]).length;bytes+=d.bytes||0;
    }catch(e){bad.push(clipLabel(c)+': '+e);}
  }
  list.slice().sort((a,b)=>b-a).forEach(i=>_spliceClip(i));
  SEL_ANCHOR=-1;
  closeModal('mbDelClip');
  renderClips1();renderClips2();renderClips3();syncNav();
  saveState();
  const mb=(bytes/1048576).toFixed(2);
  toast(t('Удалено файлов: {n} ({size} МБ)',{n:nfiles,size:mb})
    +(bad.length?t(' · ошибок: {m}',{m:bad.length}):''));
  uiLog(t('удаление с диска: убрано файлов {n} ({size} МБ)',{n:nfiles,size:mb})
    +(bad.length?t(' · ошибки: ')+bad.join('; '):''));
}

// Метла в шапке списка клипов шага 1: убрать ВСЕ клипы из списка — ТОЛЬКО CLIPS/UI.
// Это не delClip (тот чистит диск через API удаления): дисковые XML и сайдкары
// остаются, список подхватывается заново кнопкой «Из папки результата». Поэтому и
// подтверждение, и справка говорят об этом явно — иначе выглядит как удаление файлов.
async function clearClipsList(){
  if(!CLIPS.length)return;
  if(!await askConfirm(t('Убрать все клипы из списка? Файлы нарезки на диске НЕ удаляются — список подхватится заново кнопкой «Из папки результата».')))return;
  while(CLIPS.length)_spliceClip(0);   // индексы открытых клипов правит общий безопасный путь
  SEL_ANCHOR=-1;                       // список перерисован — диапазон считать не от чего
  renderClips1();renderClips2();renderClips3();syncNav();
  saveState();
  uiLog(t('список клипов очищен — файлы на диске остались'));
}

// Метла в шапке «Файлы набора» шага 3: убрать из списка ТОЛЬКО отмеченные (c.sel)
// клипы. selClips() тут не годится — «пусто = все» это семантика СБОРКИ, а убрать
// всё за разговором «убрать отмеченное» нельзя. API удаления не зовём: дисковые
// файлы остаются, список подхватится «Из папки результата».
async function removeSelClips(){
  const idx=[];CLIPS.forEach((c,i)=>{if(c.sel)idx.push(i);});
  if(!idx.length){toast(t('Ничего не отмечено — поставь галочки у клипов, которые убрать'));return;}
  if(!await askConfirm(t('Убрать отмеченных клипов из списка: {n}? Файлы нарезки на диске НЕ удаляются.',{n:idx.length})))return;
  idx.sort((a,b)=>b-a).forEach(i=>_spliceClip(i));   // индексы в обратном порядке — splice не съест соседей
  SEL_ANCHOR=-1;                                     // список перерисован — диапазон считать не от чего
  // Убрали и сам открытый в панели клип: после _spliceClip curAE=-1, а данные панели
  // (AEXML/WORDS/INTRO) всё ещё про удалённый файл. При живых клипах панель
  // пересаживается на живой клип (как goStep(3)); при пустом списке selectAE звать
  // не на чем — явно прячем #aecfg и снимаем принадлежность панели (AEXML='').
  if(curAE<0&&CLIPS.length)selectAE(0);
  else if(curAE<0){$('aecfg').style.display='none';AEXML='';}
  renderClips1();renderClips2();renderClips3();syncNav();
  saveState();
  uiLog(t('из списка убрано отмеченных: {n} (файлы на диске остались)',{n:idx.length}));
}

let REFRESHING=false,REFRESHWANT=false;
async function refreshStatuses(){
  // Клипы теперь прилетают по одному прямо во время нарезки, и запрос статуса легко
  // попадает в уже идущий обход: молча выйти = оставить новый клип без тегов до конца
  // очереди. Поэтому не «пропускаем», а повторяем обход после текущего.
  if(REFRESHING){REFRESHWANT=true;return;}
  REFRESHING=true;
  try{
    for(const c of CLIPS){
      try{const d=await (await fetch('/api/xml_state',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({xml:c.xml})})).json();
        if(d.error)uiLog(t('статус не прочитан ({name}): ',{name:c.name})+d.error);   // молча оставить старые теги = соврать про разметку
        else{const st=c.status||{};st.subs=d.subs;st.colored=d.colored;st.ncams=d.ncams;c.status=st;}}
      catch(e){uiLog(t('статус не прочитан ({name}): ',{name:c.name})+e);}
    }
  }finally{REFRESHING=false;}
  renderClips1();renderClips2();renderClips3();saveState();   // ncams влияет и на кнопку раскладки камер на шаге 1; clips3 иначе не видит клипов, нарезанных во время очереди
  if(REFRESHWANT){REFRESHWANT=false;refreshStatuses();}
}

