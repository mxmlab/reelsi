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
  for(let k=0;k<n;k++){const row=document.createElement('div');row.className='camrow';
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
// Абсолютный путь: буква диска, UNC или корень. Рилс подкаста кладётся в очередь именно так
// (П7): такая запись не привязана к папкам камер и переживает смену их числа и папок.
function isAbsPath(p){return /^([A-Za-z]:[\\/]|\\\\|\/)/.test(String(p||''));}
// Рилс подкаста — пара из одних абсолютных путей (сейчас всегда одна запись).
function isReelPair(p){return p.length>0&&p.every(isAbsPath);}
// Очередь из одних рилсов папок камер не требует: без них пара — уже готовый путь.
function queueNeedsCamDirs(){return QUEUE.some(p=>!isReelPair(p));}
// Папки камер для запроса — ровно nCams() штук (пустые тоже). Короче списка — build_pairs
// на сервере решит, что пара длиннее папок, и откажет, даже если все записи абсолютные.
function camDirsArg(){const n=nCams();return Array.from({length:n},(_,k)=>CAMDIRS[k]||'');}
function queueBaseName(p){return String(p||'').split(/[\\\/]/).pop();}

async function setMode(){
  // Смена числа камер пересобирает строки и очищает пары очереди. Рилсы подкаста (абсолютные
  // пути) при этом остаются: от папок камер они не зависят. Подтверждение считает только
  // очищаемые пары — иначе оно пугало бы цифрой, в которую рилсы не входят.
  const keep=QUEUE.filter(isReelPair),drop=QUEUE.length-keep.length;
  if(drop&&!await askConfirm(t('Сменить число камер? Очередь ({n} {pairs}) будет очищена.',{n:drop,pairs:t(plur(drop,'пара','пары','пар'))}))){
    if(LASTCAMS!=null){const r=document.querySelector('input[name=cams][value="'+LASTCAMS+'"]');
      if(r){r.checked=true;segUI();}}                 // программная смена .checked change не шлёт — подсветку ставим сами
    return;}
  LASTCAMS=nCams();QUEUE=keep;buildCamRows();   // buildCamRows → camModeUI: кнопки под число камер
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
// Ячейка записи очереди: имя файла; рилс подкаста — имя с серой меткой, полный путь в title
function queueCellName(x){
  if(!isAbsPath(x))return esc(x);
  return '<span title="'+esc(x)+'">'+esc(queueBaseName(x))+'</span> <span class="muted">'+t('подкаст')+'</span>';}
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
    tr.innerHTML='<td class="mono">'+(i+1)+'</td><td>'+p.map(queueCellName).join('  +  ')+'</td>'
      +'<td class="x" tabindex="0" role="button" aria-label="'+t('Убрать пару из очереди')+'" data-t="'+t('Убрать пару из очереди')+'" '
      +'onclick="QUEUE.splice('+i+',1);renderQueue();saveState()">'+ico('x')+'</td>';tb.appendChild(tr);});}

// ================= CLIPS (unified list) =================
let CLIPS=[];   // {xml,name,status:{subs,colored},inserts:[],job:{...}}
// Отметка клипа галочкой — ОДНА на все три шага: галка на шаге 1 это та же
// c.sel, что на разметке и сборке. SEL_ANCHOR — индекс последней кликнутой галки, от него
// Shift считает диапазон; после удаления список перерисован и точки отсчёта больше нет —
// сбрасывается в -1 (см. delClipDiskExecute).
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
// Путь клипа к XML, как он есть НА ДИСКЕ. Сохранённая чужая склейка
// («/home/out\03.xml» — папка-posix плюс обратный слэш) на Linux это ОДНО имя
// файла с '\' внутри: такого файла нет, и по нему молча ломались превью, статусы,
// разметка и сборка. Признак — абсолютный posix-путь: у Windows-путей ('C:\…',
// '\\server\…') разделитель и так обратный, подмены там не будет.
function clipPathFix(p){p=String(p==null?'':p);return p.charAt(0)==='/'?p.replace(/\\/g,'/'):p;}
// Ключ сравнения путей клипов — ТОТ ЖЕ, что у normInsPath (85-inserts-view.js):
// разделители и регистр. Полное равенство строк плодило ВТОРОЙ клип на тот же файл
// при малейшей нестыковке разделителей (Windows 'C:\out\1.xml' против 'C:/out/1.xml'
// из другого источника), и ошибка не падала, а размножалась: у каждого клипа своя
// разметка, а собирался бы только один.
function clipKey(p){return normInsPath(clipPathFix(p));}
function clipByXml(xml){const k=clipKey(xml);return CLIPS.find(c=>clipKey(c.xml)===k);}
// Тег спикера ставится ЗДЕСЬ, в ЕДИНСТВЕННОЙ точке рождения клипа:
// после нарезки спикер точно тот, с которым резали; «Из папки результата» берёт
// папку из ai_outdir, а это папка спикера; «Добавить XML…» — догадка, тег поэтому
// исправим на шаге 3. «Не выбран» — запасной путь, работает как всегда.
function newClip(xml){const j=defJob();
  // Спикер уже выбран на шаге 1 — тег ставится здесь; его стиль и есть стиль клипа
  // (профиль — источник, пока клип не помечен «свой стиль»). Общий выбор: новая
  // нарезка/профиль, не клип — клип рождается ИЗ этой нарезки, чужого тега не бывает.
  const spk=val('speaker');
  if(spk){j.speaker=spk;
    const p=SPEAKERS[spk];
    if(p&&p.style&&STYLES[p.style]){j.styleKey=p.style;}}
  return {xml,name:(xml||'').replace(/^.*[\\\/]/,''),status:{},inserts:[],job:j,edited:false};}
// Добавить клипы по путям — ОДИН путь для «Добавить XML…» и для возврата клипа из корзины:
// без дублей по пути, перерисовка всех трёх списков, сохранение состояния, статусы.
function clipsAddPaths(paths){
  (paths||[]).forEach(x=>{if(!clipByXml(x))CLIPS.push(newClip(x));});
  renderClips1();renderClips2();renderClips3();saveState();refreshStatuses();}
function addExternal(){fetch('/api/pickfiles').then(r=>r.json()).then(d=>clipsAddPaths(d.paths));}
async function scanOutdir(){const dir=val('ai_outdir').trim();
  if(!dir){toast(t('Не задана папка результата'));return;}
  try{const d=await (await fetch('/api/scanxml',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dir})})).json();
    if(d.error){toast(errText(d));return;}
    const clipsSnap=d.clips||{},ins=d.inserts||{},spk=d.speakers||{},introMap=d.intro||{};
    let added=0;(d.paths||[]).forEach(x=>{if(clipByXml(x))return;
      let c;
      if(clipsSnap[x]){
        c=clipsSnap[x];
        c.xml=x;
        c.name=(x||'').replace(/^.*[\\\/]/,'');
        c.status={};
      }else{
        c=newClip(x);
        // Вставки и спикера возвращаем С ДИСКА: без этого повторный подхват уже
        // нарезанного давал пустые карточки и чужой стиль — готовая разметка терялась.
        const arr=ins[x];if(Array.isArray(arr)&&arr.length){c.inserts=arr;c.insTarget=arr.length;}
        const s=spk[x];if(s){c.job.speaker=s;const p=SPEAKERS[s];if(p&&p.style&&STYLES[p.style])c.job.styleKey=p.style;}
        if(introMap[x]&&typeof introRowsFromAI==='function'){
          c.job=c.job||defJob();
          c.job.introRows=introRowsFromAI(introMap[x]);
        }
      }
      CLIPS.push(c);added++;});
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
  if(queueNeedsCamDirs()&&CAMDIRS.slice(0,nCams()).some(d=>!(d||'').trim())){toast(t('Не заданы папки камер'));return;}
  cutBusy(true);CUTLABEL=t('ИИ-нарезка');progOpen({title:t('ИИ-нарезка')});
  // selfcheck не шлём: в режиме GigaAM (дефолт) этот путь не исполняется вовсе,
  // сервер сам ставит False (см. run_omnicut_job). Убрано 2026-08-11.
  // Общий выбор: новая нарезка/профиль, не клип — здесь собираются параметры НАРЕЗКИ.
  const body={outdir:val('ai_outdir'),pairs:QUEUE,camdirs:camDirsArg(),
    speaker:val('speaker'),
    review:$('chk_review')?$('chk_review').checked:false};
  let d;try{d=await (await fetch('/api/omnicut_run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();}
  catch(e){toast(t('Сервер не ответил: ')+e);cutBusy(false);hideProg();return;}
  if(d.error){toast(errText(d));cutBusy(false);hideProg();return;}
  cutRunStart();logReset();pollAI();
}
async function runCustom(){
  if(uiBusyGuard())return;
  if(!QUEUE.length){toast(t('Очередь пуста'));return;}
  if(!val('ai_outdir').trim()){toast(t('Не задана папка результата'));return;}
  if(queueNeedsCamDirs()&&CAMDIRS.slice(0,nCams()).some(d=>!(d||'').trim())){toast(t('Не заданы папки камер'));return;}

  const pausesVal=(CUT_STAGES&&CUT_STAGES.pauses)||(CUT_DEFAULTS&&CUT_DEFAULTS.pauses)||'speech';
  const branch=(pausesVal==='loud')?'vad':'gigaam';

  if(branch==='vad'){
    if(!val('base').trim()){toast(t('Не задана папка проекта'));return;}
    cutBusy(true);CUTLABEL=t('Кастомная нарезка');progOpen({title:t('Кастомная нарезка')});
    progUpdate(null,'Whisper + VAD');
    const body={
      base:val('base'),
      outdir:val('ai_outdir'),
      pairs:QUEUE,
      camdirs:camDirsArg(),
      stages:CUT_STAGES,
      thresholds:CUT_THRESHOLDS
    };
    let d;try{d=await (await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();}
    catch(e){toast(t('Сервер не ответил: ')+e);cutBusy(false);hideProg();return;}
    if(d.error){toast(errText(d));cutBusy(false);hideProg();return;}
    cutRunStart();logReset();pollAI();
  }else{
    cutBusy(true);CUTLABEL=t('Кастомная нарезка');progOpen({title:t('Кастомная нарезка')});
    // Общий выбор: новая нарезка/профиль, не клип — здесь собираются параметры НАРЕЗКИ.
    const body={
      outdir:val('ai_outdir'),
      pairs:QUEUE,
      camdirs:camDirsArg(),
      speaker:val('speaker'),
      stages:CUT_STAGES,
      review:$('chk_review')?$('chk_review').checked:false
    };
    let d;try{d=await (await fetch('/api/omnicut_run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();}
    catch(e){toast(t('Сервер не ответил: ')+e);cutBusy(false);hideProg();return;}
    if(d.error){toast(errText(d));cutBusy(false);hideProg();return;}
    cutRunStart();logReset();pollAI();
  }
}
const runClassic=runCustom;
// ---- единое ядро поллинга джоба: /api/status → лог → прогресс → onDone(d) ----
// eager: доля «внутри текущего клипа» для бара (0.15 нарезка, 0.3 сборка); null = без бара.
// В шапку идут только «сколько готово из скольких» и заголовок: имя файла и строка лога
// туда не попадают (они в строках роликов и в «Показать логи» — см. 55-progress.js).
// progress.i с сервера — номер НАЧАТОГО клипа (1-based), поэтому готовых на один меньше.
function jobProg(d,eager,title){
  const p=(d.progress&&d.progress.n)?{i:+d.progress.i,n:+d.progress.n}
        :(PROGMARK?{i:+PROGMARK[1],n:+PROGMARK[2]}:null);     // фолбэк: [i/N] из лога
  if(!p||!p.n)return{frac:null,sub:''};
  progQueue(title,Math.max(0,p.i-1),p.n);
  if(eager==null)return{frac:null,sub:''};
  return{frac:(p.i-1)/p.n+eager/p.n,sub:''};}
// onTick(d) зовётся на КАЖДОМ опросе (не только в конце) — нарезка так отдаёт
// готовые клипы по одному, не дожидаясь всей очереди.
// Сбои fetch подряд. Ретраим как раньше, но обрыв связи не должен выглядеть
// работой: после трёх провалов подряд в оверлее видно «сервер не отвечает — жду…»
// (задание по UI-состояниям). Удачный ответ — счётчик в ноль, обычный текст сам
// возвращается следующим progUpdate.
let POLLFAIL=0;
// Этап последней серверной строки лога — КОДОМ события, а не хвостом строки: разбор
// живёт в одном месте (progEventOf, 55-progress.js), а сама строка ждёт в «Показать
// логи». Ничего не разобралось — этап не показываем вовсе: сырое в шапку не идёт.
function jobStageText(){
  const last=[...LOGCACHE].reverse().find(l=>fmtLog(l).trim());
  return PROGEV[progEventOf(last)]||'';}
async function pollJob(self,title,eager,onDone,onTick){
  // сеть/рестарт сервера не должны молча убивать поллинг (кнопки остались бы залоченными) — ретраим
  let d;try{d=await (await fetch('/api/status?since='+LOGSINCE)).json();}
  catch(e){POLLFAIL++;if(POLLFAIL>=3)progUpdate(null,t('сервер не отвечает — жду…'),title,undefined);
    setTimeout(self,1500);return;}
  POLLFAIL=0;
  mergeLog(d);
  queueRender(d);    // очередь этапов: единая дверь для нарезки/сборки/черновика
  if(onTick)onTick(d);
  const pr=jobProg(d,eager,title);
  progUpdate(pr.frac,jobStageText(),title,pr.sub);
  if(d.done){onDone(d);return;}
  setTimeout(self,1000);}

// Порядок клипов прогона. Параллельные ролики отдают готовое в порядке ГОТОВНОСТИ, а в списке
// клипы должны стоять в порядке ОЧЕРЕДИ (просьба владельца, 2026-10-11, для любой нарезки).
// CUTRUN.base — длина списка ДО прогона: клипы с индексом >= base — клипы этого прогона и
// только они сортируются по номеру очереди; клипы до base не двигаются. База живёт в
// состоянии (stateObj/loadState), чтобы F5 посреди прогона не сбил порядок.
// Номер очереди берём из /api/status: result_order — параллельный results (api/jobs.py).
let CUTRUN=null;
function cutRunStart(){CUTRUN={base:CLIPS.length};saveState();}
// Вставка клипа прогона: перед первым клипом блока с БОЛЬШИМ номером очереди, иначе в конец
// списка (блок прогона — его хвост). Индексы открытых клипов сдвигаем, как _spliceClip сдвигает
// их при удалении: клип, на который они указывали, должен остаться тем же.
function cutInsertRunClip(c,qi,qiOf){
  const base=(typeof CUTRUN!=='undefined'&&CUTRUN)?CUTRUN.base:CLIPS.length;
  let pos=CLIPS.length;
  for(let i=base;i<CLIPS.length;i++){if(qiOf(CLIPS[i].xml)>qi){pos=i;break;}}
  CLIPS.splice(pos,0,c);
  if(typeof curAE!=='undefined'&&curAE>=pos)curAE++;
  if(typeof curEdit!=='undefined'&&curEdit>=pos)curEdit++;
  if(typeof curIns!=='undefined'&&curIns>=pos)curIns++;
  if(typeof SEL_ANCHOR!=='undefined'&&SEL_ANCHOR>=pos)SEL_ANCHOR++;
  // окно удаления держит свои индексы (DEL_CLIP_IDXS, открыто на q2 — после вставки q0 тот же клип)
  // и окно выбора формата скачивания (DLFMT_WANT) — тот же принцип, что у curAE
  if(typeof DEL_CLIP_IDXS!=='undefined')DEL_CLIP_IDXS=DEL_CLIP_IDXS.map(x=>x>=pos?x+1:x);
  if(typeof DLFMT_WANT!=='undefined'&&DLFMT_WANT>=pos)DLFMT_WANT++;
}
// Клип попадает в список шага 1 СРАЗУ, как только он нарезан: сервер пополняет
// JOB["results"] после каждого файла, а ждать конца всей очереди, чтобы начать
// править, незачем (просьба юзера). Возвращает, сколько клипов добавилось.
function cutAdopt(d){
  // Папку НЕ подставляем: /api/status отдаёт в results ПОЛНЫЕ пути (их пишет сам
  // сервер), а склейка в JS своим разделителем ломалась на Linux — «<папка>\имя.xml»
  // там одно имя файла, и «файл не найден» было верным ответом не про тот файл.
  // typeof: стенд вырезает эту функцию без CUTRUN, cutRunStart и cutInsertRunClip
  // (tests/test_ui_fixes_linux.py) — без них «ReferenceError» вместо проверки; без них клип встаёт в конец
  if(typeof CUTRUN!=='undefined'&&!CUTRUN&&typeof cutRunStart==='function')cutRunStart();   // F5 после прогона без записанной базы: база — текущая длина
  const res=d.results||[],ord=Array.isArray(d.result_order)?d.result_order:[];
  // номер очереди по пути: нет в ответе — Infinity, такой клип встаёт в конец блока (как раньше)
  const qiByKey=new Map();
  res.forEach((p,k)=>{if(typeof ord[k]==='number')qiByKey.set(clipKey(p),ord[k]);});
  const qiOf=xml=>{const v=qiByKey.get(clipKey(xml));return v==null?Infinity:v;};
  let n=0;
  res.forEach(full=>{
    if(clipByXml(full))return;
    const c=newClip(full),qi=qiOf(full);
    if(typeof cutInsertRunClip==='function')cutInsertRunClip(c,qi,qiOf);else CLIPS.push(c);
    n++;
    uiLog(t('нарезан: {x} — можно править, не дожидаясь остальных',
      {x:String(full||'').replace(/^.*[\\\/]/,'')}));});
  if(n){renderClips1();saveState();refreshStatuses();}
  return n;}

async function pollAI(){pollJob(pollAI,CUTLABEL,0.15,d=>{
    cutBusy(false);
    cutAdopt(d);
    CUTRUN=null;   // прогон кончился: следующий порядок задаёт следующий прогон
    const n=(d.results||[]).length,bad=d.failed||[];
    bad.forEach(f=>uiLog(t('✗ не собрался {name}: {reason}',{name:f.name,reason:f.reason||t('см. логи')})));
    let msg=UICANCEL?t('Остановлено — готово клипов: ')+n:(n?t('Готово: {n} клип(ов)',{n:n}):(bad.length?'':t('Готово (файлов нет — см. логи)')));
    if(bad.length)msg+=(msg?' · ':'')+t('⚠ не собрались ({n}): ',{n:bad.length})
      +bad.map(f=>f.name+' — '+(f.reason||'?').slice(0,90)).join(' · ');
    // Падения были — окно кончается НЕ зелёным: «поработал» и «упало» — разные статусы.
    progDone(msg,bad.length>0);
    renderClips1();saveState();refreshStatuses();},
  d=>{cutAdopt(d);progReadySet((d.results||[]).length);});}

// ---- render clip lists ----
// Скачивание таймлайна XML — POST-ом, а не переходом по ссылке: сервер на этом
// роуте ПИШЕТ (проставляет настоящие таймкоды в копию), и GET с побочным действием
// чужая страница дёргала тегом <img src>. Ответ — файл вложением, его и сохраняем.
function dlXml(c){
  fetch('/api/export_xml',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({path:c.xml})})
    .then(async r=>{
      if(!r.ok){let e=r.status===404?t('не найден'):r.status===403?t('запрещено'):'',ed=null;
        try{const b=await r.json();ed=b;e=b.error||e}catch(_){}
        throw new Error((ed&&errText(ed))||e||t('скачивание XML не удалось ')+r.status);}
      const blob=await r.blob();
      // голос не переключён в XML (сервер не смог записать) — файл уйдёт со старой дорожкой звука
      if(r.headers.get('X-Reelsi-Voice-Sync')==='failed')
        toast(t('XML скачан, но голос в нём не переключён — в файле прежняя дорожка звука'));
      const u=URL.createObjectURL(blob);
      const a=document.createElement('a');a.href=u;
      a.download=(c.name||'timeline').replace(/\.xml$/i,'')+'.xml';
      document.body.appendChild(a);a.click();a.remove();
      setTimeout(()=>URL.revokeObjectURL(u),4000);})
    .catch(e=>toast(t('Не скачалось XML: ')+e.message));
}
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
    a+='<button class="icon" aria-label="'+t('Скачать XML')+'" data-t="'+t('Скачать XML — открывается и в Premiere, и в DaVinci Resolve')+'" onclick="dlXml(CLIPS['+i+'])" style="text-decoration:none;padding:5px 8px;color:var(--mut)">'+ico('dl')+'</button>';
    // крестика нет: удаление — мусорка в шапке по галкам (#delSel1 → delSelClips), как на шагах 2 и 3
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
    dlXml(c);
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
  if(typeof markLastClip==='function')markLastClip(2,i);   // крестик на теге шага 2 — работа с этим клипом, но только если подтвердили
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
// Метка «над этим клипом работал» (класс .cur) — на каждом шаге своя. Сравниваем по XML:
// индекс при удалении и перестановке уезжает, путь к клипу остаётся. Пустая метка ничего не красит.
// typeof — стенды вырезают списки без 99-boot.js, где объявлен LASTCLIP (как AEMUSICDIR в stateObj).
function lastClipCls(step,c){if(typeof LASTCLIP==='undefined')return '';return LASTCLIP[step]&&LASTCLIP[step]===c.xml?' cur':'';}
// Запомнить клип, с которым работали на шаге step (шаги 1 и 2), и перерисовать его список.
// Шаг 3 метку ставит selectAE: там же, где открывается панель клипа.
function markLastClip(step,i){const c=CLIPS[i];if(!c||typeof LASTCLIP==='undefined')return;LASTCLIP[step]=c.xml;saveState();
  if(step===1)renderClips1();else if(step===2)renderClips2();}
function renderClips1(){const h=$('clips1');if(!h)return;h.innerHTML='';
  if(!CLIPS.length){h.innerHTML='<div class="empty">'+t('Пока пусто. Запусти ИИ-нарезку или добавь XML.')+'</div>';syncDelSel();syncNav();return;}
  CLIPS.forEach((c,i)=>{const el=document.createElement('div');el.className='clip'+lastClipCls(1,c);
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
  // Имя — гибкая колонка (grow), ярлыки — фиксированный блок правее: их начало одно у всех
  // клипов, а длинное имя обрезается многоточием, а не сталкивает ярлыки на вторую строку.
  CLIPS.forEach((c,i)=>{const el=document.createElement('div');el.className='clip'+lastClipCls(2,c);
    el.innerHTML='<label class="pickbox" data-t="'+t('Фазы разметки у выбранных клипов (пусто у всех = все). Отмеченное здесь — то же, что и на шаге сборки.')+'" onclick="event.stopPropagation()">'
      +'<input type="checkbox" data-ci="'+i+'" '+(c.sel?'checked':'')+' onchange="CLIPS['+i+'].sel=this.checked;pickClip('+i+',this.checked,SHIFT_HELD);saveState();syncBuildBtn();markupSelCount()"></label>'
      +'<span class="idx">'+(i+1)+'</span><span class="nm grow" data-noi18n title="'+esc(c.xml)+'">'+esc(c.name)+'</span>'
      +'<span class="st">'+statusTags(c,true)+'</span>'+clipActs(i,2);
    h.appendChild(el);});
  markupSelCount();syncDelSel();}
// Счётчик «(N)» на кнопках фаз шага 2: сколько клипов уйдёт в фазу (не отмечено ни
// одного = все, как selClips). Обновляется при клике по галочке — как syncBuildBtn.
function markupSelCount(){const n=CLIPS.filter(c=>c.sel).length||CLIPS.length;
  document.querySelectorAll('[data-markupcnt]').forEach(e=>{e.textContent=n;});}
function renderClips3(){const h=$('clips3');if(!h)return;h.innerHTML='';
  if(!CLIPS.length){h.innerHTML='<div class="empty">'+t('Клипов нет — вернись на ')+'<span class="lnk" tabindex="0" role="button" onclick="goStep(1)">'+t('шаг 1')+'</span>'+t(' и запусти нарезку или подхвати XML кнопкой «Из папки результата».')+'</div>';syncBuildBtn();syncDelSel();return;}
  CLIPS.forEach((c,i)=>{const el=document.createElement('div');
    el.className='clip pick'+(aeDone(c)?' done':clipReady(c)?' ready':'')+lastClipCls(3,c);
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
function spkSelHTML(i,c){const k=clipSpeaker(c);
  let o='<button type="button" role="option" data-k="" aria-selected="'+(k===''?'true':'false')+'">'+t('не выбран')+'</button>';
  Object.keys(SPEAKERS).forEach(key=>{const s=SPEAKERS[key];
    o+='<button type="button" role="option" data-k="'+esc(key)+'" aria-selected="'+(key===k?'true':'false')+'">'+esc(s.label||key)+'</button>';});
  const label=k&&SPEAKERS[k]?(SPEAKERS[k].label||k):t('Спикер не выбран');
  // Свой список кнопок вместо нативного селекта: у него варианты открывались ВТОРЫМ
  // кликом — системным чёрным попапом, который стилями не красится. Теперь один клик по
  // summary раскрывает список, второй клик — это уже сам выбор.
  return '<details class="spkpick" onclick="event.stopPropagation()" onkeydown="event.stopPropagation()" aria-label="'+t('Спикер клипа')+'"><summary data-t="'+t('Спикер этого клипа: меняет его стиль, папку .jsx и пороги нарезки.')+'">'+esc(label)+'<span aria-hidden="true">⌄</span></summary><div class="opts dd-panel" tabindex="-1" role="listbox" aria-label="'+t('Спикер клипа')+'" onclick="var b=event.target.closest(&quot;button&quot;);if(!b)return;setClipSpeaker('+i+',b.dataset.k);this.closest(&quot;details&quot;).open=false">'+o+'</div></details>';}
// Список спикера — общая механика выпадающих меню (20-widgets.js). Строки шага 1 перерисовываются,
// поэтому регистрируется не блок, а «открытый сейчас» .spkpick. Открытый — один: открытие второго
// закрывает первого (toggle приходит и с клавиатуры, и мимо мыши). Выбор спикера — setClipSpeaker.
const spkOpenBox=()=>document.querySelector('details.spkpick[open]');
document.addEventListener('DOMContentLoaded',()=>{
  dropdownBind(spkOpenBox,{isOpen:()=>!!spkOpenBox(),
    close:back=>{const d=spkOpenBox();if(!d)return;d.open=false;if(back)d.querySelector('summary').focus();}});
  document.addEventListener('toggle',e=>{const el=e.target;
    if(!el.open||!el.classList||!el.classList.contains('spkpick'))return;
    document.querySelectorAll('details.spkpick[open]').forEach(x=>{if(x!==el)x.open=false;});
  },true);
});
// withName=false — только метки (шаг 3: имя уже видно по селектору спикера, дублировать
// его тегом нельзя — юзер просил «не надо второй раз писать кто это»); на шагах 1-2
// селектора нет, там имя остаётся.
function spkTagHTML(c,withName){const k=clipSpeaker(c);
  const sp=SPEAKERS[k];
  let out='';
  if(withName!==false&&sp)out+='<span class="tag" data-t="'+esc(t('Спикер: {n}',{n:sp.label||k}))+'">'+esc(sp.label||k)+'</span>';
  return out;}
// Спикер КЛИПА — единственный источник для ОПЕРАЦИЙ над этим клипом, то есть для
// квоты ИИ-вставок, стоков, картинок и видео (тег job.speaker, ставит его newClip и
// селектор строки шага 3). Общий селектор шага 1 — это НОВАЯ нарезка, не клип:
// у клипа «Спикер не выбран» профиля нет и быть не должно, поэтому запасного пути
// на общий выбор здесь НЕТ — иначе клип спикера Б получал квоту спикера А (владелец
// выбрал наверху спикера А, работал с клипом спикера Б, а вставок выходило 7 вместо 13).
// ОДНА ДВЕРЬ: любое чтение спикера КЛИПА во фронте идёт сюда — папки .jsx и рендера,
// LUT, кадр камеры, голос, формат стока, стиль, подпись панели стиля. Прямое чтение
// `c.job.speaker` мимо двери — та же ошибка «взяли не того спикера», только тише.
function clipSpeaker(c){return (c&&c.job&&c.job.speaker)||'';}
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
  }
  if(curAE===i)selectAE(i);           // панель принадлежит этому клипу — перечитать его стиль
  else{saveState();renderClips3();}
  renderRenderDirField();syncBuildBtn();
  uiLog(t('спикер клипа «{name}»: {n}',{name:c.name,n:(spk?spk.label:t('не выбран'))}));}
// один клик по файлу: выбрать + сразу открыть предпросмотр
// AEXML — а не только индекс: curAE переживает F5, а панель после перезагрузки пустая,
// и «тот же клип» открывался бы с чужими (дефолтными) вставками, интро и стилем
function openAEFor(i){if(curAE!==i||AEXML!==CLIPS[i].xml)selectAE(i);openAEPreview();}
function selClips(){const s=CLIPS.filter(c=>c.sel);return s.length?s:CLIPS;}
function syncBuildBtn(){const n=CLIPS.filter(c=>c.sel).length;
  const b=$('buildbtn');if(b)b.textContent=n?t('Собрать выбранные ({n})',{n:n}):t('Собрать набор');
  const scope=$('buildscope');if(scope)scope.textContent=n?t('Выбрано {n} из {m}',{n:n,m:CLIPS.length}):t('Все {n} клипов',{n:CLIPS.length});
  // Клипы ДВУХ спикеров в одном наборе собираются только по одному — каждый в свою
  // папку: «Собрать набор» и «Один на всё» для них не существуют.
  const sel=selClips();const spks=new Set(sel.map(c=>clipSpeaker(c)).filter(Boolean));
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
  // Метка «работал» — по XML: убранный клип метку с собой уносит. Без сброса вернувшийся
  // с тем же путём клип (добавили из папки заново) снова стал бы помеченным.
  const gone=CLIPS[i]&&CLIPS[i].xml;
  CLIPS.splice(i,1);
  // typeof — стенды вырезают _spliceClip без 99-boot.js (там LASTCLIP)
  if(gone&&typeof LASTCLIP!=='undefined')[1,2,3].forEach(k=>{if(LASTCLIP[k]===gone)LASTCLIP[k]='';});
  if(curAE===i)curAE=-1;else if(curAE>i)curAE--;
  if(curEdit===i)curEdit=-1;else if(curEdit>i)curEdit--;
  if(curIns===i)curIns=-1;else if(curIns>i)curIns--;
  // база прогона (см. cutAdopt): убрали клип ДО неё — граница съезжает на единицу
  if(typeof CUTRUN!=='undefined'&&CUTRUN&&i<CUTRUN.base)CUTRUN.base--;
}

// Что удаляем: индексы, зафиксированные на момент открытия окна (галку могли снять, пока
// окно открыто, — удаляем ровно то, что человек выбрал и увидел в окне).
let DEL_CLIP_IDXS=[];
function clipLabel(c){return (c&&(c.name||(c.xml?c.xml.split(/[/\\]/).pop():'')))||'';}
// Одна механика на всех шагах: удалил — значит в корзину (файлы нарезки можно вернуть).
// Окно открывается сразу и сразу делает сухой прогон по каждому клипу: что уйдёт и сколько.
// Заголовок — сколько клипов, в теле — их имена. Галка «И исходники» по умолчанию снята.
async function delClips(idxs){
  const list=(idxs||[]).filter(i=>i>=0&&i<CLIPS.length);
  if(!list.length)return;
  DEL_CLIP_IDXS=list;
  const title=list.length===1?t('Удалить клип?')
    :t('Удалить {n} {clips}?',{n:list.length,clips:t(plur(list.length,'клип','клипа','клипов'))});
  $('delClipTitle').textContent=title;
  $('delClipName').textContent=list.map(i=>clipLabel(CLIPS[i])).join(', ');
  $('delClipSrc').checked=false;
  $('delClipSrcList').innerHTML='';
  $('delClipInfo').textContent=t('Загрузка списка файлов…');
  $('delClipDiskConfirmBtn').disabled=true;
  openModal('mbDelClip');
  await delClipDiskPrepare();
}

// «Удалить выбранные» в шапке любого из трёх списков. Пусто у всех НЕ значит «все»
// (это семантика selClips для сборки): без галочек удалять нечего — кнопка и так погашена.
function delSelClips(){
  const idx=[];CLIPS.forEach((c,i)=>{if(c.sel)idx.push(i);});
  if(!idx.length){toast(t('Ничего не отмечено — поставь галочки у клипов, которые убрать'));return;}
  delClips(idx);
}

// Один запрос к удалению по клипу: сухой прогон (dry) или настоящий (sources — уносить ли
// исходники). batch — все XML пачки: их исходники сервер не считает общими с пачкой.
async function delClipCall(c,dry,sources,batch){
  const res=await fetch('/api/clip_delete',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({xml:c.xml,jsxdir:(effOutdir(c)||'').trim(),dry:dry,sources:sources,batch:batch})
  });
  return res.json();
}

// Сухой прогон по КАЖДОМУ выбранному клипу: сколько файлов нарезки уйдёт и какие исходники
// есть на диске. Исходник, который нужен другому клипу папки, — серым с причиной и без галки.
// Ошибка одного клипа не мешает прочитать остальные (в execute он всё равно попробуется).
async function delClipDiskPrepare(){
  const list=DEL_CLIP_IDXS.slice();
  if(!list.length)return;
  const batch=list.map(i=>CLIPS[i]&&CLIPS[i].xml).filter(Boolean);
  let n=0,bytes=0;
  const bad=[],srcs=[],byKey={};
  for(const i of list){
    const c=CLIPS[i];if(!c)continue;
    try{
      const d=await delClipCall(c,true,false,batch);
      if(d.error){bad.push(clipLabel(c)+': '+errText(d));continue;}
      n+=(d.files||[]).length;bytes+=d.bytes||0;
      (d.sources||[]).forEach(s=>{
        const key=String(s.path).toLowerCase();
        if(!byKey[key]){byKey[key]={path:s.path,size:s.size||0,shared:[]};srcs.push(byKey[key]);}
        (s.shared_with||[]).forEach(x=>{if(byKey[key].shared.indexOf(x)<0)byKey[key].shared.push(x);});
      });
    }catch(e){bad.push(clipLabel(c)+': '+e);}
  }
  $('delClipInfo').textContent=t('Файлы нарезки: {n} ({size} МБ) — уйдут в корзину, можно вернуть',
      {n:n,size:(bytes/1048576).toFixed(2)})
    +(bad.length?t(' · не прочитано клипов: {n}',{n:bad.length}):'');
  if(bad.length)uiLog(t('удаление: не прочитано — ')+bad.join('; '));
  // Галка доступна, только если есть исходник, который никто больше не держит
  const free=srcs.filter(s=>!s.shared.length);
  $('delClipSrc').disabled=!free.length;
  $('delClipSrc').checked=false;
  $('delClipSrcList').innerHTML=srcs.length?srcs.map(s=>{
    const name=s.path.split(/[/\\]/).pop();
    const sz=(s.size>1048576?(s.size/1048576).toFixed(2)+t(' МБ'):(s.size/1024).toFixed(1)+t(' КБ'));
    // имя файла приходит с диска (материал мог приехать с гугл-диска) — в разметку
    // только через esc(): кавычка или «<» в имени иначе станут тегом
    const why=s.shared.length?'<div>'+esc(t('нужен ещё клипам: {n}, не удаляется',{n:s.shared.join(', ')}))+'</div>':'';
    return '<div style="color:'+(s.shared.length?'var(--mut)':'var(--tx)')+'">'
      +'<div style="display:flex;justify-content:space-between;gap:8px"><span>'+esc(name)+'</span><span style="flex:none">'+sz+'</span></div>'
      +why+'</div>';
  }).join(''):'<div class="empty">'+t('Исходное видео на диске не найдено')+'</div>';
  $('delClipDiskConfirmBtn').disabled=false;
}

// Удаление по каждому клипу: ошибка одного не прерывает остальные, итог — тост и журнал
// (сколько убрано и кто именно не удалился). Из списка убираем всех, кого пытались удалить:
// файлы могли остаться на диске — их подхватит «Из папки результата».
async function delClipDiskExecute(){
  const list=DEL_CLIP_IDXS.slice();DEL_CLIP_IDXS=[];
  if(!list.length)return;
  // исходники уносим, только если галку поставили сами и она доступна: общий исходник
  // (нужен другому клипу) не должен уехать в корзину вместе с клипом
  const srcOn=!!$('delClipSrc').checked&&!$('delClipSrc').disabled;
  const batch=list.map(i=>CLIPS[i]&&CLIPS[i].xml).filter(Boolean);
  $('delClipDiskConfirmBtn').disabled=true;
  const bad=[];let nclips=0,nfiles=0,bytes=0;
  for(const i of list){
    const c=CLIPS[i];if(!c)continue;
    try{
      const d=await delClipCall(c,false,srcOn,batch);
      if(d.error){bad.push(clipLabel(c)+': '+errText(d));continue;}
      nclips++;nfiles+=(d.files||[]).length;bytes+=d.bytes||0;
      trashRemember(trashDirOf(c.xml));   // корзина этого клипа — в папке его XML: запоминаем, чтобы видеть и стирать
    }catch(e){bad.push(clipLabel(c)+': '+e);}
  }
  // открытый в панели шага 3 клип уходит — панель принадлежит ему, а _spliceClip лишь сбрасывает curAE
  const openGone=curAE>=0&&list.includes(curAE);
  list.slice().sort((a,b)=>b-a).forEach(i=>_spliceClip(i));   // с конца: splice не съест соседей
  SEL_ANCHOR=-1;   // список перерисован — прошлая кликнутая галка больше ни на что не указывает
  // Панель не должна хранить данные удалённого клипа: при живых клипах садим на первый, иначе
  // прячем #aecfg и снимаем принадлежность (AEXML='') — как было в removeSelClips до переноса.
  if(openGone){
    if(CLIPS.length)selectAE(0);
    else{$('aecfg').style.display='none';AEXML='';}
  }
  closeModal('mbDelClip');
  renderClips1();renderClips2();renderClips3();syncNav();
  saveState();
  const mb=(bytes/1048576).toFixed(2);
  toast(t('В корзине: клипов {k}, файлов {n} ({size} МБ)',{k:nclips,n:nfiles,size:mb})
    +(bad.length?t(' · ошибок: {m}',{m:bad.length}):''));
  uiLog(t('удаление в корзину: клипов {k}, файлов {n} ({size} МБ)',{k:nclips,n:nfiles,size:mb})
    +(bad.length?t(' · ошибки: ')+bad.join('; '):''));
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

// Корзина: клипы папки результата и подкасты папки подкастов — одним списком. Пустая одна из
// папок не мешает показать другую; тост «не задана папка» — только если нет ни той, ни другой.
function trashRoots(){
  return {dir:val('ai_outdir').trim(),
    pod:typeof podcastRootVal==='function'?podcastRootVal():''};
}
// Папки, куда удаляли клипы и подкасты. Корзина клипа лежит в папке ЕГО XML (а не в папке
// результата: «Добавить XML…», рилсы подкастов), поэтому одной папки результата мало —
// помним все, куда удаляли. До 50 последних; сохраняется в состоянии (stateObj/applyState).
let TRASHROOTS=[];
// Папка файла: всё до последнего разделителя (C:/a/b.xml → C:/a).
function trashDirOf(p){return String(p||'').replace(/[\\/][^\\/]*[\\/]*$/,'');}
function trashRemember(dir){
  const d=String(dir||'').trim().replace(/[\\/]+$/,'');
  if(!d)return;
  const k=d.replace(/\\/g,'/').toLowerCase();
  TRASHROOTS=TRASHROOTS.filter(x=>x.replace(/\\/g,'/').toLowerCase()!==k);
  TRASHROOTS.push(d);
  if(TRASHROOTS.length>50)TRASHROOTS=TRASHROOTS.slice(-50);
}
// Запрос к корзине: папки — результат, все запомненные, папки XML всех клипов списка (без
// дублей; сервер всё равно сводит по realpath). Папки подкастов ещё может не быть — сервер
// её пропускает.
async function trashCall(url,body){
  const dirs=[],seen=new Set();
  const add=p=>{const d=String(p||'').trim().replace(/[\\/]+$/,'');if(!d)return;
    const k=d.replace(/\\/g,'/').toLowerCase();if(seen.has(k))return;seen.add(k);dirs.push(d);};
  add(body.dir);
  TRASHROOTS.forEach(add);
  CLIPS.forEach(c=>add(trashDirOf(c.xml)));
  return fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(Object.assign({},body,{dirs:dirs}))}).then(r=>r.json());
}
function trashNote(text){
  const n=document.createElement('div');n.className='empty';n.textContent=text;return n;
}
// Время удаления — местное. Сервер пишет deleted_at в UTC с зоной (…+00:00, см. core/clipstore.py):
// показать строку как есть значило бы выдать UTC за местное время (в 00:18 у владельца стояло «21:36»
// прошлого дня). Строка без зоны — уже местная: new Date читает её как местную и выводит как есть.
function trashWhen(s){
  if(!s)return '';
  const d=new Date(String(s));
  if(isNaN(d.getTime()))return String(s).replace('T',' ').substring(0,19);
  const p=n=>String(n).padStart(2,'0');
  return p(d.getDate())+'.'+p(d.getMonth()+1)+'.'+d.getFullYear()+' '+p(d.getHours())+':'+p(d.getMinutes());
}
// Строка записи: название, серая метка «подкаст», дата · файлов · МБ, исходники, «Вернуть».
// Текст — через textContent: названия и пути приходят с диска.
function trashRow(it){
  const row=document.createElement('div');
  row.style.cssText='display:flex;align-items:center;justify-content:space-between;gap:8px;padding:6px;border-bottom:1px solid var(--bd)';
  const info=document.createElement('div');
  info.style.cssText='display:flex;flex-direction:column;gap:2px;overflow:hidden';
  const name=document.createElement('span');
  name.style.cssText='font-weight:500;word-break:break-all';
  name.setAttribute('data-noi18n','');
  name.textContent=it.title||it.stem||it.id;
  if(it.kind==='podcast'){
    const tag=document.createElement('span');tag.className='muted';tag.style.fontSize='11px';
    tag.textContent=' '+t('подкаст');name.appendChild(tag);
  }
  const meta=document.createElement('span');meta.className='muted';meta.style.fontSize='11px';
  const mb=((it.bytes||0)/1048576).toFixed(2);
  const dt=trashWhen(it.deleted_at);
  meta.textContent=dt+' · '+t('{n} файлов',{n:it.files||0})+' · '+mb+t(' МБ')
    +(it.sources>0?' · '+t('+ исходники: {n}',{n:it.sources}):'');
  info.appendChild(name);info.appendChild(meta);
  const btn=document.createElement('button');btn.className='sm';btn.textContent=t('Вернуть');
  btn.onclick=()=>restoreTrashItem(it.root,it.id,it.kind);
  row.appendChild(info);row.appendChild(btn);
  return row;
}

async function openTrash(){
  const r=trashRoots();
  if(!r.dir&&!r.pod){toast(t('Не задана папка результата'));return;}
  const box=$('trashList');
  box.innerHTML='';box.appendChild(trashNote(t('Загрузка корзины…')));
  openModal('mbTrash');
  try{
    const d=await trashCall('/api/trash_list',{dir:r.dir,podcast_root:r.pod});
    box.innerHTML='';
    if(d.error){box.appendChild(trashNote(errText(d)));return;}
    const items=d.items||[];
    if(!items.length){box.appendChild(trashNote(t('Корзина пуста')));return;}
    items.forEach(it=>box.appendChild(trashRow(it)));
  }catch(e){
    box.innerHTML='';box.appendChild(trashNote(t('Ошибка загрузки корзины: ')+e));
  }
}

// Лежит ли хоть один из путей в папке результата (ai_outdir). Пути приходят с диска: слеши и регистр
// не сравниваем строго, иначе клип из папки результата не попал бы в scanOutdir.
function trashInOutdir(paths){
  const n=s=>String(s||'').replace(/\\/g,'/').replace(/\/+$/,'').toLowerCase();
  const out=n(val('ai_outdir').trim());
  return !!out&&(paths||[]).some(p=>n(String(p).replace(/[\\\/][^\\\/]*$/,''))===out);
}
// «Вернуть»: клип — на старые места; из папки результата — перечитываем её, из другой папки —
// добавляем в список по путям. Подкаст — папку на место и список подкастов перечитываем.
async function restoreTrashItem(root,id,kind){
  try{
    const d=await (await fetch('/api/trash_restore',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({root:root,id:id})})).json();
    if(d.error){toast(errText(d));return;}
    const n=(d.restored||[]).length;
    const sk=(d.skipped||[]).length;
    toast(t('Восстановлено файлов: {n}',{n})+(sk?t(' · пропущено: {m}',{m:sk}):''));
    if(sk)uiLog(t('корзина: не возвращено — ')+d.skipped.map(x=>x.path+': '+x.why).join('; '));
    closeModal('mbTrash');
    if(kind==='podcast'){podcastList();return;}
    // Клип из папки результата подхватывает scanOutdir: вставки и спикер там читаются с диска.
    // Клип из другой папки (рилсы подкастов в cut_out, «Добавить XML…») в ней не лежит: его
    // добавляем тем же путём, что и «Добавить XML…». Иначе он возвращён на диск, а в списке его нет.
    // Если в ответе нет .xml, место клипа неизвестно: перечитываем папку результата, как и раньше.
    const xmls=(d.restored||[]).filter(p=>/\.xml$/i.test(p));
    if(!xmls.length||trashInOutdir(xmls))await scanOutdir();
    clipsAddPaths(xmls);
  }catch(e){
    toast(t('Ошибка восстановления: ')+e);
  }
}

// Сводка корзины на ⚙ → «Нарезка»: сухой прогон по обеим папкам, без удаления.
async function trashCountLoad(){
  const sum=$('trashSum'),btn=$('trashEmptyBtn');
  if(!sum||!btn)return;
  const r=trashRoots();
  if(!r.dir&&!r.pod){sum.textContent=t('Папка результата не задана');btn.disabled=true;return;}
  try{
    const d=await trashCall('/api/trash_empty',{dir:r.dir,podcast_root:r.pod,dry:true});
    if(d.error){sum.textContent=errText(d);btn.disabled=true;return;}
    sum.textContent=t('В корзине: {n} записей, {size} ГБ',{n:d.entries||0,size:((d.bytes||0)/1073741824).toFixed(2)});
    btn.disabled=!d.entries;
  }catch(e){sum.textContent=t('Корзина не прочитана: ')+e;btn.disabled=true;}
}

// Очистка корзины насовсем — только отсюда, с подтверждением. Сначала сухой прогон: в вопросе
// должно стоять, сколько записей и байт уйдёт.
async function trashEmpty(){
  const r=trashRoots();
  let d;
  try{d=await trashCall('/api/trash_empty',{dir:r.dir,podcast_root:r.pod,dry:true});}
  catch(e){toast(t('Сервер не ответил: ')+e);uiLog(t('корзина: ')+e);return;}
  if(d.error){toast('⚠ '+errText(d));uiLog(t('корзина: ')+errText(d));return;}
  if(!d.entries){trashCountLoad();return;}
  const gb=((d.bytes||0)/1073741824).toFixed(2);
  if(!await askConfirm(t('Удалить насовсем {n} записей ({size} ГБ)? Вернуть будет нельзя.',{n:d.entries,size:gb})))return;
  let e;
  try{e=await trashCall('/api/trash_empty',{dir:r.dir,podcast_root:r.pod,dry:false});}
  catch(err){toast(t('Сервер не ответил: ')+err);uiLog(t('корзина: ')+err);trashCountLoad();return;}
  if(e.error){toast('⚠ '+errText(e));uiLog(t('корзина: ')+errText(e));trashCountLoad();return;}
  const bad=e.failed||[];
  const freed=((e.bytes||0)/1073741824).toFixed(2);
  toast(t('Освобождено {size} ГБ',{size:freed})+(bad.length?t(' · ошибок: {m}',{m:bad.length}):''));
  if(bad.length)uiLog(t('корзина: не удалено — ')+bad.map(x=>x.path+': '+x.why).join('; '));
  trashCountLoad();
}

