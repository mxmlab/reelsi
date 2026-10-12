// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Maxim Si
// ================= подкаст → рилсы (шаг 1) =================
// Блок «Подкаст → рилсы» на шаге 1: ссылка YouTube или файл → (проверка дорожек) →
// /api/podcast_create → /api/podcast_run → опрос /api/podcast_status. Сам разбор
// (скачивание, расшифровка, выбор кусков) делает сервер (api/podcast.py); здесь форма,
// строка статуса и список подкастов из /api/podcast_list.
let PODN=0,PODLOGN=0;      // интервал опроса и сколько строк лога уже показано
let PODTITLE='';           // название из последней проверки ссылки — уходит в create
let PODTRACKS=[];          // звуковые дорожки проверки PODPROBED
let PODDEFAULT='';         // дорожка, которую советует сервер (оригинал), из той же проверки
let PODPROBED='';          // ссылка, для которой проверка уже была (успех или ошибка); пусто — не было
let PODPROBEERR=false;     // проверка PODPROBED упала: в списке «не удалось загрузить — будет оригинал»
let PODLOADING='';         // ссылка, по которой проверка идёт сейчас (одна за раз на ссылку)
let PODLOADP=null;         // промис этой проверки: второй вызов для той же ссылки ждёт его
let PODREQ=0;              // номер запроса проверки: правка ссылки его поднимает, ответ старого номера отбрасывается
let POD_TRACK_SEL='';      // дорожка для сервера: format_id из проверки или выбора; '' — оригинал по умолчанию
let PODMENU=false;         // список дорожек открыт (своё меню, П20: нативный select такого не умеет)
let PODRUNNING=false;      // джоб идёт — по последнему ответу сервера
let PODDIR='';             // папка подкаста, который сейчас идёт (пусто — ничего не идёт)
let PODSTAGE='';           // строка стадии для карточки этого подкаста
// Папка подкаста, открытого последним: метка «cur» в списке, как LASTCLIP у клипов. Живёт в состоянии
// (podcastStateObj/podcastApplyState), ставит её openPodcastEditor (46-podcast-editor.js).
let LASTPOD='';
// Сравнение папок: путь приходит то из списка (как ввели корень), то из джоба (абсолютный)
function podNormDir(p){return String(p||'').replace(/\\/g,'/').replace(/\/+$/,'').toLowerCase();}
function podBusy(dir){return PODRUNNING&&!!PODDIR&&podNormDir(PODDIR)===podNormDir(dir);}

const PODSTATUS={new:'новый',downloading:'качается',downloaded:'скачан',transcribing:'расшифровка',
  transcribed:'расшифрован',choosing:'ИИ выбирает',ready:'куски готовы',converting:'перевод в рилсы',
  converted:'рилсы готовы',error:'ошибка'};
// Статусы, где конвейер не дошёл до «куски готовы» (и ошибка): их можно продолжить
const PODCONTINUE=['new','downloading','downloaded','transcribing','transcribed','choosing','error'];

function podcastSrc(){const r=document.querySelector('input[name=podcast_src]:checked');return r?r.value:'url';}

// Пустая папка — `<база>\podcasts`, как у гугл-диска (`<база>\gdrive_downloads`)
function podcastRootVal(){
  const v=val('podcast_root').trim();
  if(v)return v;
  return (val('base').trim().replace(/[\\\/]+$/,'')||'')+'\\podcasts';
}

// Строки формы по режиму источника. Сохранения нет: её делают вызывающие.
// Выбор дорожки живёт в строке ссылки (П17): виден всегда, в режиме «Файл» скрыт вместе с ней.
function podcastRowsUI(){
  const url=podcastSrc()==='url';
  $('podcast_url_row').style.display=url?'':'none';
  $('podcast_file_row').style.display=url?'none':'';
  podRenderTrackSel();
  segUI();
}
function podcastSrcUI(){podcastRowsUI();saveState();}

// Правка ссылки сбрасывает проверку: дорожки были для другой ссылки. Ответ на прежнюю ссылку,
// если он ещё в пути, отбросится: PODREQ уже другой.
function podcastUrlEdit(){
  PODREQ++;PODLOADING='';PODLOADP=null;
  PODPROBED='';PODPROBEERR=false;PODTRACKS=[];PODTITLE='';PODDEFAULT='';POD_TRACK_SEL='';
  podMenuClose(false);
  podRenderTrackSel();saveState();
}

// Язык по коду дорожки: «en-US» → английский (по основе кода). Нет в LANGNAME — название из
// Intl.DisplayNames на языке интерфейса (тот же LANG, что у t()); браузер без данных или код,
// который Intl не принял, — сам код основы: «xx-YY» → «xx», а не «xx-YY».
function podTrackName(lang){
  const base=String(lang||'').split('-')[0].toLowerCase();
  if(!base)return '—';
  if(LANGNAME[base])return LANGNAME[base];
  try{
    const name=new Intl.DisplayNames([LANG==='en'?'en':'ru'],{type:'language'}).of(base);
    if(name)return name.charAt(0).toLowerCase()+name.slice(1);   // с маленькой, как в LANGNAME
  }catch(e){}
  return base;
}
// Подпись дорожки: «русский — оригинал» / «английский — автодубляж»; без пометки — только язык
function podTrackLabel(tr){
  const lang=podTrackName(tr.lang);
  if(tr.original)return t('{lang} — оригинал',{lang});
  if(tr.dub)return t('{lang} — автодубляж',{lang});
  return lang;
}

// Ссылка YouTube в том же смысле, что у сервера (core/podcast.py, _YT_RE). Проверку по выбору
// запускаем только для неё: для остальных сервер всё равно ответит «не ссылка YouTube».
const POD_YT_RE=/^https?:\/\/(?:(?:www|m)\.)?youtube\.com\/(?:watch\?(?:[^#]*&)?v=|shorts\/|live\/)[A-Za-z0-9_-]+|^https?:\/\/youtu\.be\/[A-Za-z0-9_-]+/i;
function podIsYoutube(url){return POD_YT_RE.test(String(url||'').trim());}

// Нужна ли проверка при открытии выбора: ссылка YouTube, по ней проверки ещё не было и её сейчас
// не грузим. Чистая функция — её проверяет node-тест.
function podNeedProbe(url,probedUrl,loadingUrl){
  const u=String(url||'').trim();
  return podIsYoutube(u)&&u!==probedUrl&&u!==loadingUrl;
}

// Список дорожек — своё меню (П20), не нативный <select>: нативный раскрывается только в ответ на
// жест пользователя, а проверка YouTube идёт ~10 с. Кнопка #podcast_track показывает выбранную
// дорожку, #podcast_track_menu (role=listbox) открывается сразу; пока проверка идёт, в нём строка
// «загружаю дорожки…», а ответ перерисовывает ЭТО ЖЕ открытое меню.

// Опции меню по состоянию (чистая функция, тест — node). idle — одна «Оригинал (по умолчанию)»
// со значением ''. loading — строка «загружаю дорожки…», её не выбрать. error — «не удалось
// загрузить — будет оригинал» (значение ''). ready — дорожки: value — совет сервера (def), а без
// него первый original, а без него первая дорожка.
function podTrackOptions(s){
  const kind=(s&&s.kind)||'idle';
  if(kind==='loading')return {kind:kind,value:'',opts:[{value:'',text:t('загружаю дорожки…'),loading:true}]};
  if(kind==='error')return {kind:kind,value:'',opts:[{value:'',text:t('не удалось загрузить — будет оригинал')}]};
  const tracks=(kind==='ready'&&s.tracks)||[];
  if(!tracks.length)return {kind:'idle',value:'',opts:[{value:'',text:t('Оригинал (по умолчанию)')}]};
  const opts=tracks.map(tr=>({value:tr.format_id,text:podTrackLabel(tr),title:tr.label||tr.lang||''}));
  const hit=tracks.find(tr=>tr.format_id===s.def)||tracks.find(tr=>tr.original)||tracks[0];
  return {kind:'ready',value:hit.format_id,opts:opts};
}

// Состояние списка по глобальным: проверка этой ссылки идёт / проверена (или упала) / не было
function podTrackState(){
  const url=val('podcast_url').trim();
  if(url&&PODLOADING===url)return {kind:'loading'};
  if(url&&PODPROBED===url)return PODPROBEERR?{kind:'error'}:{kind:'ready',tracks:PODTRACKS,def:PODDEFAULT};
  return {kind:'idle'};
}

// Строка списка: role=option, выбранная — aria-selected (цвет как у .spkpick). Строка загрузки не выбирается.
function podMenuRow(o,selected){
  const row=document.createElement('div');
  row.setAttribute('role','option');
  row.setAttribute('aria-selected',selected?'true':'false');
  row.tabIndex=-1;
  row.dataset.v=o.value;
  row.textContent=o.text;
  if(o.title)row.title=o.title;
  if(o.loading){
    row.setAttribute('aria-disabled','true');
    const bar=document.createElement('div');bar.className='podmenu-bar';row.appendChild(bar);
  }else row.onclick=()=>podTrackPick(o.value);
  return row;
}

// Видимость списка и aria-expanded кнопки — в одном месте, чтобы они не расходились
function podMenuPaint(){
  const btn=$('podcast_track'),menu=$('podcast_track_menu');if(!btn||!menu)return;
  menu.className='podmenu dd-panel'+(PODMENU?' open':'');
  btn.setAttribute('aria-expanded',PODMENU?'true':'false');
}

// Фокус в список: на выбранную строку; пока строк нет (загрузка) — на сам список
function podFocusTrackRow(){
  const menu=$('podcast_track_menu');if(!menu)return;
  const rows=Array.from(menu.children).filter(r=>r.getAttribute('aria-disabled')!=='true');
  const row=rows.find(r=>r.getAttribute('aria-selected')==='true')||rows[0]||menu;
  row.focus();
}

// Подпись кнопки и строки списка — по состоянию. Открытый список перерисовывается тем же
// элементом: ответ проверки меняет строки, а не закрывает и открывает меню. Фокус, стоявший в
// списке, переносим на выбранную строку: иначе он пропал бы вместе со старыми строками.
function podRenderTrackSel(){
  const btn=$('podcast_track'),menu=$('podcast_track_menu');if(!btn||!menu)return;
  const r=podTrackOptions(podTrackState());
  const cur=r.opts.find(o=>o.value===POD_TRACK_SEL)||r.opts[0];
  $('podcast_track_txt').textContent=cur.text;
  const hadFocus=PODMENU&&(document.activeElement===menu||menu.contains(document.activeElement));
  if(hadFocus)menu.focus();          // фокус — на контейнере, пока строки меняются
  menu.innerHTML='';
  r.opts.forEach(o=>menu.appendChild(podMenuRow(o,!o.loading&&o.value===POD_TRACK_SEL)));
  podMenuPaint();
  if(hadFocus)podFocusTrackRow();
}

// Проверка ссылки (/api/podcast_probe): дорожки, название и совет сервера. Одна за раз на ссылку:
// если эта ссылка уже проверяется, отдаём её промис, а не шлём второй запрос. Ответ, пришедший
// после правки ссылки (PODREQ сменился), отбрасываем. Resolve: true — дорожки получены.
function podcastLoadTracks(url){
  url=String(url||'').trim();
  if(PODLOADING===url&&PODLOADP)return PODLOADP;
  const req=++PODREQ;
  PODLOADING=url;PODLOADP=null;
  podRenderTrackSel();
  uiLog(t('подкаст: проверяю ')+url);
  const p=(async()=>{
    let d=null,netErr=null;
    try{d=await (await fetch('/api/podcast_probe',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({url})})).json();}
    catch(e){netErr=e;}
    if(req!==PODREQ)return false;          // ссылку успели сменить: этот ответ уже не про неё
    PODLOADING='';PODLOADP=null;
    PODPROBED=url;
    if(netErr||(d&&d.error)){
      PODPROBEERR=true;PODTRACKS=[];PODTITLE='';PODDEFAULT='';POD_TRACK_SEL='';
      podRenderTrackSel();
      if(netErr){toast(t('Сервер не ответил: ')+netErr);uiLog('podcast: '+netErr);}
      else{toast('⚠ '+errText(d));uiLog(t('подкаст: ')+errText(d));}
      return false;
    }
    PODPROBEERR=false;
    PODTITLE=d.title||'';PODTRACKS=d.tracks||[];PODDEFAULT=d.default||'';
    POD_TRACK_SEL=podTrackOptions(podTrackState()).value;     // оригинал по совету сервера
    podRenderTrackSel();
    toast(t('звуковых дорожек: {n}',{n:PODTRACKS.length}));
    return true;
  })();
  PODLOADP=p;
  return p;
}

// Открыть список. Если проверка этой ссылки нужна — запускаем её: строка «загружаю дорожки…»
// уже в списке, а ответ перерисует его же. Проверку этой ссылки второй раз не шлём.
function podMenuOpen(){
  if(PODMENU)return;
  PODMENU=true;
  const url=val('podcast_url').trim();
  if(podNeedProbe(url,PODPROBED,PODLOADING))podcastLoadTracks(url);
  podRenderTrackSel();
  podFocusTrackRow();
}

// Закрыть список без изменения выбора. backToBtn — вернуть фокус на кнопку (Esc, выбор строки);
// при клике мимо и уходе фокуса наружу фокус уже у другого элемента, его не трогаем.
function podMenuClose(backToBtn){
  if(!PODMENU)return;
  PODMENU=false;
  if(backToBtn){const b=$('podcast_track');if(b)b.focus();}
  podMenuPaint();
}
// Меню дорожки — в общей механике закрытия (клик мимо, Esc, уход фокуса). Блок — #podcast_track_box.
document.addEventListener('DOMContentLoaded',()=>dropdownBind($('podcast_track_box'),
  {isOpen:()=>PODMENU,close:back=>podMenuClose(back)}));

// Клик по кнопке: открыт — закрываем, закрыт — открываем (сразу)
function podcastTrackOpen(){
  if(PODMENU)podMenuClose(true);
  else podMenuOpen();
}

// Выбрана строка: дорожка уходит на сервер, подпись кнопки обновляется, список закрывается
function podTrackPick(v){
  POD_TRACK_SEL=String(v||'');
  podMenuClose(true);
  podRenderTrackSel();
}

// Клавиши. На кнопке: стрелка вниз открывает список (Enter и пробел дают обычный click). В списке:
// стрелки ходят по строкам, Enter и пробел выбирают строку. Esc, клик мимо и уход фокуса закрывают
// список общей механикой (dropdownBind, 20-widgets.js) — здесь её не дублируем.
function podTrackKey(e){
  const k=e.key;
  if(!PODMENU){if(k==='ArrowDown'){e.preventDefault();podMenuOpen();}return;}
  const menu=$('podcast_track_menu');
  const rows=Array.from(menu.children).filter(r=>r.getAttribute('aria-disabled')!=='true');
  const i=rows.indexOf(document.activeElement);
  if(k==='ArrowDown'||k==='ArrowUp'){
    e.preventDefault();
    if(i<0){podFocusTrackRow();return;}
    rows[Math.min(rows.length-1,Math.max(0,i+(k==='ArrowDown'?1:-1)))].focus();
    return;
  }
  if((k==='Enter'||k===' ')&&i>=0){e.preventDefault();podTrackPick(rows[i].dataset.v);}
}


// Запуск конвейера по папке проекта: и «Разобрать», и «Продолжить» идут сюда
async function podcastStart(dir){
  try{
    const r=await (await fetch('/api/podcast_run',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({dir})})).json();
    if(r.error){toast('⚠ '+errText(r));uiLog(t('подкаст: ')+errText(r));return false;}
    PODLOGN=0;                  // лог джоба начался заново
    $('podcast_status').className='muted mono';
    $('podcast_status').textContent=t('запуск…');
    podcastPoll();
    return true;
  }catch(e){toast(t('Сервер не ответил: ')+e);uiLog('podcast: '+e);return false;}
}

async function podcastGo(){
  const root=podcastRootVal();
  const body={root,reels:val('podcast_reels')};
  if(podcastSrc()==='url'){
    const url=val('podcast_url').trim();
    if(!url){toast(t('Вставь ссылку YouTube'));return;}
    // Без проверки этой ссылки (или после её ошибки) сначала она: по умолчанию пойдёт оригинал,
    // а не то, что yt-dlp выберет сам. Проверка не вышла — её ошибку уже показали.
    if(PODPROBED!==url||PODPROBEERR){if(!await podcastLoadTracks(url))return;}
    body.url=url;body.title=PODTITLE;body.format_id=POD_TRACK_SEL;
  }else{
    const file=val('podcast_file').trim();
    if(!file){toast(t('Укажи файл подкаста'));return;}
    body.file=file;
  }
  const btn=$('podcast_go');if(btn)btn.disabled=true;
  let started=false;
  try{
    const c=await (await fetch('/api/podcast_create',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body)})).json();
    if(c.error){toast('⚠ '+errText(c));uiLog(t('подкаст: ')+errText(c));return;}
    started=await podcastStart(c.dir);
    podcastList();
  }catch(e){toast(t('Сервер не ответил: ')+e);uiLog('podcast: '+e);}
  finally{if(btn&&!started)btn.disabled=false;}   // при успехе кнопку вернёт опрос по концу джоба
}

async function podcastStop(){
  try{await fetch('/api/podcast_cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});}
  catch(e){toast(t('Сервер не ответил: ')+e);return;}
  $('podcast_status').textContent=t('останавливаю…');
}

// Строка статуса по стадии: «скачивание 12 % · 3.1 MiB/s · ETA 06:12» / «расшифровка» / …
function podLine(s){
  if(s.stage==='download'&&s.pct!=null){
    let x=t('скачивание {p} %',{p:Math.round(s.pct)});
    if(s.speed)x+=' · '+s.speed;
    if(s.eta&&s.eta!=='-')x+=' · ETA '+s.eta;
    return x;
  }
  if(s.stage==='convert'){
    // «Все рилсы»: идут подкасты по очереди — в строке и номер подкаста
    if(s.pn)return t('перевод в рилсы: подкаст {pi} из {pn}, кусок {i} из {n}',
      {pi:s.pi||0,pn:s.pn,i:s.i||0,n:s.n||0});
    return t('перевод в рилсы {i} из {n}',{i:s.i||0,n:s.n||0});
  }
  return t(s.cur||'работаю…');
}

// Рилсы легли в папку подкаста, а не в папку камеры 1 спикера (он не выбран или у него нет папки)
function podFallbackToast(r){
  if(r&&r.fallback)toast(t('спикер не выбран или у него нет папки камеры 1 — рилсы в папке подкаста'));
}

// «Перевести в рилсы» (П7): джоб на сервере, ход — тем же опросом, что и разбор. По концу
// готовые рилсы встают в очередь шага 1. ИИ-нарезку НЕ запускаем: её жмёт человек сам.
// Без папки — кнопка окна правки (onclick="podConvert()"): несохранённые куски сначала
// сохраняем, рилсы строятся по сохранённым; не вышло сохранить — старые куски не переводим.
// Папка рилсов — папка камеры 1 спикера из шага 1 (#speaker); без неё — папка подкаста.
async function podConvert(dir){
  if(dir===undefined){
    if(POD.dirty){await podSave();if(POD.dirty)return;}
    dir=POD.dir;
  }
  if(PODRUNNING){toast(t('Подкаст уже обрабатывается — дождись конца'));return;}
  let r;try{r=await (await fetch('/api/podcast_convert',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({dir:dir,ids:[],speaker:val('speaker')})})).json();}
  catch(e){toast(t('Сервер не ответил: ')+e);uiLog('podcast: '+e);return;}
  if(r.error){toast('⚠ '+errText(r));uiLog(t('подкаст: ')+errText(r));return;}
  podFallbackToast(r);
  PODLOGN=0;
  const st=$('podcast_status');if(st){st.className='muted mono';st.textContent=t('запуск…');}
  podcastPoll();
}

// «Все рилсы в очередь» (внизу списка): кусков без рилса всех подкастов папки — одним джобом;
// по концу все рилсы этих подкастов встают в очередь шага 1 (podConvertDone, тот же путь, что и «В рилсы»).
async function podConvertAll(){
  if(PODRUNNING){toast(t('Подкаст уже обрабатывается — дождись конца'));return;}
  let r;try{r=await (await fetch('/api/podcast_convert_all',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({root:podcastRootVal(),speaker:val('speaker')})})).json();}
  catch(e){toast(t('Сервер не ответил: ')+e);uiLog('podcast: '+e);return;}
  if(r.error){toast('⚠ '+errText(r));uiLog(t('подкаст: ')+errText(r));return;}
  podFallbackToast(r);
  PODLOGN=0;
  const st=$('podcast_status');if(st){st.className='muted mono';st.textContent=t('запуск…');}
  podcastPoll();
}

// Убрать подкаст в корзину (_reelsi_trash, как у клипов): насовсем не стираем. Рилсы в очереди остаются.
// Путь лежит внутри папки подкаста (с разделителем на границе, не просто общий префикс имени)
function podInside(path,folder){
  const p=podNormDir(path),f=podNormDir(folder);
  return !!p&&!!f&&p.startsWith(f+'/');
}
// Рилсы кусков, лежащие внутри папки подкаста: запасной путь без папки камеры 1 кладёт их туда
function podReelsInside(segs,folder){
  return (segs||[]).map(s=>(s&&s.reel)?String(s.reel):'').filter(r=>r&&podInside(r,folder));
}
// Очередь без записей, где хоть один путь внутри папки подкаста: эти рилсы уехали в корзину вместе с ним.
// Рилсы в папке камеры 1 спикера сюда не попадают — их папка не внутри папки подкаста.
function podQueueWithout(queue,folder){
  return (queue||[]).filter(e=>!(Array.isArray(e)?e:[e]).some(x=>podInside(x,folder)));
}
// Окно удаления подкаста: та же механика, что у клипов — «в корзину, можно вернуть».
// Галка «И исходник» — только если источник выбран с диска и лежит вне папки подкаста
// (скачанный source.* уезжает вместе с папкой). Рилсы внутри папки (запасной путь) уедут
// вместе с ним — число называем до вопроса, после удаления чистим очередь.
// Рилсы в папке камеры 1 спикера не трогаем.
async function podcastDelete(dir,title){
  let inside=[],src='';
  try{const g=await (await fetch('/api/podcast_get?dir='+encodeURIComponent(dir))).json();
    if(g&&g.ok&&g.project){
      inside=podReelsInside(g.project.segments,dir);
      const s=String(g.project.source||'').trim();
      if(s&&!podInside(s,dir))src=s;
    }}
  catch(e){}
  $('delPodTitle').textContent=t('Удалить подкаст «{n}»?',{n:title||'—'});
  $('delPodText').textContent=t('Уйдёт в корзину, можно вернуть.')
    +(inside.length
      ?' '+t('Рилсов в его папке: {k} — они тоже уйдут в корзину и уберутся из очереди.',{k:inside.length})
      :' '+t('Рилсы, уже стоящие в очереди, останутся.'));
  $('delPodSrcRow').style.display=src?'':'none';
  $('delPodSrc').checked=false;
  $('delPodSrcName').textContent=src?src.split(/[/\\]/).pop():'';
  $('delPodOk').onclick=()=>podcastDeleteGo(dir,!!src&&!!$('delPodSrc').checked);
  openModal('mbDelPodcast');
}

// Подтверждённое удаление подкаста: sources — унести и внешний исходник (если его не держит
// другой подкаст папки; отказ сервер отдаёт в skipped с причиной).
async function podcastDeleteGo(dir,sources){
  closeModal('mbDelPodcast');
  let r;try{r=await (await fetch('/api/podcast_delete',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({dir:dir,sources:sources})})).json();}
  catch(e){toast(t('Сервер не ответил: ')+e);uiLog('podcast: '+e);return;}
  if(r.error){toast('⚠ '+errText(r));uiLog(t('подкаст: ')+errText(r));return;}
  // корзина подкаста — в папке подкастов (рядом с его папкой): запоминаем, чтобы окно её видело
  if(typeof trashRemember==='function')trashRemember(String(dir).replace(/[\\/][^\\/]*[\\/]*$/,''));
  // QUEUE — очередь шага 1 (40-queue.js): чистим на месте, ссылки на массив остаются рабочими
  if(typeof QUEUE!=='undefined'&&Array.isArray(QUEUE)){
    const keep=podQueueWithout(QUEUE,dir);
    if(keep.length!==QUEUE.length){QUEUE.splice(0,QUEUE.length,...keep);renderQueue();saveState();}
  }
  const sk=r.skipped||[];
  sk.forEach(x=>uiLog(t('подкаст: исходник не унесён — ')+x.path+': '+x.why));
  toast(t('Подкаст убран в корзину')+(sk.length?t(' · исходник не унесён (см. журнал)'):''));
  podcastList();
}

// Конец перевода: рилсы из `reels` — в QUEUE без дублей. При отмене и ошибке уже готовые
// рилсы тоже встают: они на диске и в проекте.
function podConvertDone(s){
  const add=(s.reels||[]).filter(x=>x&&!QUEUE.some(p=>eqArr(p,[x])));
  if(!add.length)return;
  add.forEach(x=>QUEUE.push([x]));
  renderQueue();saveState();
  toast(t('В очередь добавлено рилсов: {n} — запусти ИИ-нарезку',{n:add.length}));
  const qt=$('qt');if(qt)qt.scrollIntoView({behavior:'smooth',block:'start'});
}

async function podcastTick(){
  let s;try{s=await (await fetch('/api/podcast_status?since='+PODLOGN)).json();}catch(e){return;}
  if((s.log_total||0)<PODLOGN)PODLOGN=0;                  // сервер начал лог заново
  (s.log||[]).forEach(l=>uiLog(t('подкаст: ')+fmtLog(l)));
  PODLOGN=s.log_total||PODLOGN;
  const wasRunning=PODRUNNING;
  PODRUNNING=!!s.running;
  PODDIR=PODRUNNING?(s.dir||''):'';
  const go=$('podcast_go'),stop=$('podcast_stop'),st=$('podcast_status');
  if(go)go.disabled=PODRUNNING;
  const all=$('podcast_all');if(all)all.disabled=PODRUNNING;
  if(stop)stop.style.display=PODRUNNING?'':'none';
  if(PODRUNNING){
    PODSTAGE=podLine(s);
    // карточка подкаста, который сейчас идёт, перерисовывается: у неё вместо «Продолжить» стадия
    if(!wasRunning)podcastList();
    else document.querySelectorAll('.podcard-stage').forEach(el=>{if(podBusy(el.dataset.dir))el.textContent=PODSTAGE;});
  }
  if(!st)return;
  if(PODRUNNING){st.className='muted mono';st.textContent=PODSTAGE;return;}
  clearInterval(PODN);PODN=0;
  // упавший конвейер не выдаём за успех; «Стоп» нажал человек — это не ошибка
  const conv=s.stage==='convert';          // перевод в рилсы (П7): свои слова, не «разбор»
  const bad=!!(s.done&&s.failed), stopped=!!(s.done&&s.cancelled);
  if(conv&&s.done)podConvertDone(s);
  if(stopped){st.className='muted mono';st.textContent=t(conv?'перевод остановлен':'разбор остановлен');}
  else if(bad){st.className='mono err';st.textContent=t(s.cur||(conv?'перевод не удался':'разбор не удался'));
    toast('⚠ '+t(conv?'Перевод в рилсы не удался — смотри лог':'Разбор подкаста не удался — смотри лог'));}
  else if(s.done){st.className='mono ok';st.textContent=t('готово');}
  else{st.className='muted mono';st.textContent='';}
  podcastList();
}
function podcastPoll(){clearInterval(PODN);podcastTick();PODN=setInterval(podcastTick,1000);}

// После F5 конвейер продолжает идти на сервере — страница должна к нему вернуться
async function podcastResume(){
  podcastList();
  let s;try{s=await (await fetch('/api/podcast_status?since=0')).json();}catch(e){return;}
  if(!s.running)return;
  PODLOGN=s.log_total||0;     // накопленный лог заново не вываливаем
  podcastPoll();
}
document.addEventListener('DOMContentLoaded',podcastResume);

async function podcastPickRoot(){await pickdir('podcast_root');podcastList();}

async function podcastList(){
  const box=$('podcast_list');if(!box)return;
  const root=podcastRootVal();
  const rootEl=$('podcast_root');if(rootEl)rootEl.placeholder=root;
  let d;try{d=await (await fetch('/api/podcast_list?root='+encodeURIComponent(root))).json();}
  catch(e){return;}
  box.innerHTML='';
  const items=d.items||[];
  items.forEach(it=>box.appendChild(podcastCard(it)));
  // «Все рилсы в очередь» — одна на блок, внизу списка; без подкастов её нет
  const allRow=$('podcast_all_row');if(allRow)allRow.style.display=items.length?'':'none';
  const all=$('podcast_all');if(all)all.disabled=PODRUNNING;
}


// Строка подкаста — вид строки клипа шага 1 (.clip): слева название, статус-теги, справа иконки
// (карандаш — правка кусков, «повтор» — продолжить недоделанный, мусорка — в корзину).
// Текст строится через textContent — название и ошибка приходят с диска.
function podcastCard(it){
  const st=it.status||'';
  const busy=podBusy(it.dir);
  const card=document.createElement('div');card.className='podcard';
  const row=document.createElement('div');row.className='clip podrow'+(it.dir===LASTPOD?' cur':'');
  const name=document.createElement('span');name.className='nm grow';name.setAttribute('data-noi18n','');
  name.title=it.dir||'';name.textContent=it.title||'—';
  row.appendChild(name);
  const tags=document.createElement('span');tags.className='st';
  const tag=document.createElement('span');
  tag.className='tag'+(st==='converted'?' ok':(st==='error'?' bad':''));
  tag.textContent=st==='converted'?t('рилсы готовы: {n}',{n:it.reels_ready||0}):t(PODSTATUS[st]||st);
  tags.appendChild(tag);
  const meta=document.createElement('span');meta.className='podcard-meta';
  meta.textContent=t('кусков {n} из {m}',{n:it.n_segments||0,m:it.reels_wanted||0});
  tags.appendChild(meta);
  // На какой дорожке скачано (П16): язык и оригинал/автодубляж; у файла с диска дорожки нет
  if(it.audio_track&&it.audio_track.lang){
    const tk=document.createElement('span');tk.className='podcard-meta';
    const kind=it.audio_track.original?t('оригинал'):t('автодубляж');
    tk.textContent=t('дорожка: {lang}, {kind}',{lang:podTrackName(it.audio_track.lang),kind});
    tags.appendChild(tk);}
  // Пока конвейер ЭТОГО подкаста идёт, на месте статуса — стадия
  if(busy){
    const sg=document.createElement('span');sg.className='muted mono podcard-stage';
    sg.dataset.dir=it.dir;sg.textContent=PODSTAGE||t('работаю…');tags.appendChild(sg);}
  row.appendChild(tags);
  const acts=document.createElement('span');acts.className='acts';
  if(st==='ready'||st==='converted')
    acts.appendChild(iconBtn('pencil',t('Править куски'),()=>openPodcastEditor(it.dir),{tip:true}));
  if(PODCONTINUE.includes(st)&&!busy){
    const b=iconBtn('refresh',t('Продолжить разбор'),()=>podcastStart(it.dir),{tip:true});
    b.disabled=PODRUNNING;acts.appendChild(b);}
  const del=iconBtn('trash',t('Убрать в корзину'),()=>podcastDelete(it.dir,it.title),{tip:true});
  del.disabled=busy;acts.appendChild(del);
  row.appendChild(acts);
  card.appendChild(row);
  // текст ошибки — отдельной строкой под строкой: длинная строка в теге ломала бы ряд
  if(st==='error'&&it.error){const e=document.createElement('div');e.className='err podcard-err';
    e.textContent=it.error;card.appendChild(e);}
  return card;
}

// Состояние формы — в stateObj (99-boot.js): источник, ссылка, файл, папка, рилсов
function podcastStateObj(){
  return {src:podcastSrc(),url:val('podcast_url'),file:val('podcast_file'),
    root:val('podcast_root'),reels:val('podcast_reels'),last:LASTPOD};
}
function podcastApplyState(p){
  if(!p||typeof p!=='object')return;
  if(p.src){const r=document.querySelector('input[name=podcast_src][value="'+(p.src==='file'?'file':'url')+'"]');
    if(r)r.checked=true;}
  if(p.url!=null)$('podcast_url').value=String(p.url);
  if(p.file!=null)$('podcast_file').value=String(p.file);
  if(p.root!=null)$('podcast_root').value=String(p.root);
  if(p.reels!=null)$('podcast_reels').value=String(p.reels);
  if(typeof p.last==='string')LASTPOD=p.last;
  podcastRowsUI();     // без сохранения: вызывается изнутри восстановления состояния
  podcastList();
}
