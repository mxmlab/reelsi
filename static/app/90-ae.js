// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Maxim Si
// шаг After Effects: слова, интро, ручные вставки
//
// Часть интерфейса Reelsi. Файлы static/app/ грузятся ПО ПОРЯДКУ ИМЁН обычными
// <script>-тегами (не модулями): один общий скоуп, как было в едином app.js.
// Порядок важен — объявления функций поднимаются в пределах своего файла.

// ================= AE step (per-clip) =================
let curAE=-1;
// Чей клип СЕЙЧАС загружен в панель AE (селектом clips3 → selectAE). Пустая строка =
// панель с дефолтами, ничьи данные. Без этого признака captureAE стирал разметку — см. там.
let AEXML='';
function defJob(){return {highlights:[],hl_breaks:[],hl_count:[],hl_joins:[],hlxml:'',introRows:[],intromode:'word',
  music:'',music_random:true,exposure:0,style:null,styleKey:null,censor:true,ins:[]};}
// normInsPath объявлена в 85-inserts-view.js (грузится раньше) — один общий источник для
// ensureJobs, applyInsMoved и драга в предпросмотре: «та же вставка» ищется одинаково.
function ensureJobs(){CLIPS.forEach(c=>{if(!c.job)c.job=defJob();
  // AE-список вставок ПОЛНОСТЬЮ пересобирается из разметки шага 2 — она источник истины.
  // Раньше он патчился по месту (удаляли только помеченные src2, добавляли по имени файла),
  // и накапливал мусор: заменил картинку на шаге 2 — старая оставалась висеть; удалил вставку
  // и создал новую с тем же файлом — новая не добавлялась, старая жила на старом тайминге.
  // Тайминги ВСЕГДА с шага 2; ручные AE-подстройки (стиль/scale/noexit) переносим по файлу.
  // Всё, что правится ПРЯМО В КАРТОЧКЕ шага 2 (мозаика, точка покоя x/y, масштаб sc, форма
  // маски mw/mh, старт куска в файле sin), едет из неё же — иначе правка по предпросмотру
  // никуда не доезжала и молча терялась.
  const old=Array.isArray(c.job.ins)?c.job.ins:[];
  const tw={};old.forEach(x=>{const k=normInsPath(x.media);if(k&&!tw[k])tw[k]={style:x.style,scale:x.scale,sin:x.sin,noexit:x.noexit,x:x.x,y:x.y};});
  // Вставки, добавленные РУКАМИ на шаге 3 (кнопка «＋ вставка»), в c.inserts не попадают —
  // пересборка списка их молча убивала на каждом заходе в шаг. Признак ручной = нет src2.
  const manual=old.filter(x=>!x.src2&&(x.media||'').trim());
  // Карточки шага 2 с query, но БЕЗ выбранного файла («Добрать…»/«Подобрать заново», когда
  // автоподбор не нашёл файл) раньше молча выпадали из AE-списка на каждом заходе в шаг 3:
  // запрос в c.inserts есть, а «Вставки» на шаге 3 пустые — «ИИ вставки удалились».
  // Теперь такие карточки едут как РУЧНЫЕ вставки (src2=1) с query: на шаге 3 видно, что
  // вставка на месте («файл не выбран»), файл можно добрать кнопкой «Выбрать…», а idx —
  // обратная ссылка на карточку шага 2, чтобы выбранный файл пережил пересборку.
  const src2Cards=[];
  (c.inserts||[]).forEach((x,i)=>{if((x.query||'').trim()&&!(x.media||'').trim())src2Cards.push(
    {media:'',src2:1,idx:i,start_s:(+x.start_sec||0),start_f:0,
      dur_s:(+x.duration_sec||2),dur_f:0,type:'photo',query:(x.query||'').trim()});});
  // секунды как есть (start_s дробный, start_f=0): сервер понимает float,
  // а пересчёт в кадры с хардкодом fps=60 врал на не-60fps XML
  c.job.ins=(c.inserts||[]).filter(x=>(x.media||'').trim()).map(x=>{const was=tw[normInsPath(x.media)]||{};
    return cardToIns(x,was);});
  c.job.ins=c.job.ins.concat(src2Cards).concat(manual);
  c.job.ins.sort((a,b)=>((a.start_s||0)+(a.start_f||0)/60)-((b.start_s||0)+(b.start_f||0)/60));});}
// Пересборка XML меняет состав и порядок слов, а жёлтые хранятся ИНДЕКСАМИ — после
// пересборки они указывают на чужие слова, и в .jsx подсвечивается не то. Чистим их
// вместе со статусом (в UI при этом пропадает тег «жёлтые N» — это честно).
function clearHl(c){if(!c||!c.job)return;
  const had=(c.job.highlights||[]).length;
  c.job.highlights=[];c.job.hl_breaks=[];c.job.hl_count=[];c.job.hl_joins=[];c.job.hlxml='';
  if(curAE>=0&&CLIPS[curAE]===c){HL=new Set();BRK=new Set();CNT=new Set();JNS=new Set();HLXML='';}
  return had;}
function selectAE(i){if(curAE>=0&&curAE!==i)captureAE();curAE=i;const c=CLIPS[i];const j=c.job||defJob();c.job=j;
  AEXML=c.xml;                                          // с этого момента панель принадлежит клипу
  // Карточка «Настройка» (#aecfg) на странице больше не показывается — стиль/интро/жёлтые/
  // вставки правятся в предпросмотре (клик по клипу → openAEFor → openAEPreview). DOM оставлен
  // скрытым: #stylebox всё так же переезжает во вкладку «Стиль» модалки и обратно (styleToModal/Home).
  $('aecfg').style.display='none';$('aename').textContent=c.name;
  HL=new Set(j.highlights||[]);BRK=new Set(j.hl_breaks||[]);CNT=new Set(j.hl_count||[]);JNS=new Set(j.hl_joins||[]);HLXML=j.hlxml||'';
  INS=(j.ins||[]).map(x=>({...x}));renderIns();
  // gx/gy/gs — геометрия группы: живёт на головной строке, таскаем со строкой
  INTRO=(j.introRows||[]).map(r=>({count:r.count,color:r.color||'white',fill:r.fill||null,anim:(r.anim==='count'?'':(r.anim||'')),fx:r.fx||'',dec:parseInt(r.dec)||0,is_count:!!(r.is_count||r.anim==='count'),cnt_words:(Array.isArray(r.cnt_words)?r.cnt_words.slice():null),break:!!r.break,from:(r.from!=null?r.from:null),gx:r.gx||0,gy:r.gy||0,gs:r.gs||100,accent:!!r.accent,back:!!r.back}));INTRO_PICK=-1;
  $('aeexposure').value=j.exposure||0;$('censor').checked=j.censor!==false;
  const mm=$('musicmode');if(mm)mm.value=j.music_random?'random':(j.music?(/^https?:/i.test(j.music)?'url':'file'):'random');
  $('aemusic').value=j.music||'';musicUI();
  // стиль: сперва по ключу задания, иначе — узнаём шаблон по содержимому (старые задания,
  // и «кастом», совпадающий с шаблоном 1в1: незачем открывать простыню настроек)
  let sk=(j.styleKey&&j.styleKey!=='__custom__'&&STYLES[j.styleKey])?j.styleKey:styleKeyFor(j.style);
  if(sk){$('style').value=sk;onStyleChange();}
  else if(j.style){CURSTYLE=j.style;ensureCustomOption();$('style').value='__custom__';onStyleChange();}
  else{const cur=$('style').value;                      // клип ещё не трогали — оставляем выбранный стиль
    if(cur==='__custom__'||!STYLES[cur])$('style').value=STYLES[STYLESAVED]?STYLESAVED:'base';
    onStyleChange();}
  const im=$('intromode');if(im)im.value=j.intromode||'word';   // после onStyleChange: reflectStyle ставит режим из стиля
  rotoSync();renderClips3();renderAeDirField();loadWordsFor(c.xml);saveState();
  if($('st_name'))$('st_name').value='';   // вышли из редактора шаблона — см. captureAE (templateEdit)
}
// curAE переживает клип (список стал короче после удаления/пересборки набора, а индекс
// приехал из сохранённого состояния). Без этой проверки запись джоба падала TypeError'ом
// внутри чужого try — loadStyles ловил его и врал «Стили не загрузились — сервер не ответил».
function captureAE(){if(curAE<0)return;const c=CLIPS[curAE];if(!c){curAE=-1;return;}
  // Панель AE (INS/HL/BRK/INTRO + поля стиля) принадлежит тому клипу, который в неё
  // ЗАГРУЗИЛИ через selectAE. Пока этого не было, в ней дефолты: вставок нет, интро нет,
  // жёлтых нет — и запись такой панели в задание СТИРАЛА разметку клипа. Ловилось так:
  // curAE переживает F5, а onStyleChange зовёт captureAE и на загрузке стилей при старте,
  // и на выборе спикера на шаге 1 — то есть клип, открытый в прошлый раз на шаге AE, терял
  // ИИ-вставки, жёлтые и интро молча, при простом обновлении страницы (2026-08-08).
  // Не наша панель — не наши данные: только сохраняем состояние, задание не трогаем.
  if(AEXML!==c.xml){saveState();return;}
  const j=c.job||defJob();
  const ir=introResolve();
  j.highlights=(HLXML===c.xml)?[...HL]:[];j.hl_breaks=(HLXML===c.xml)?[...BRK]:[];j.hl_count=(HLXML===c.xml)?[...CNT]:[];j.hl_joins=(HLXML===c.xml)?[...JNS]:[];j.hlxml=HLXML;
  j.introRows=INTRO.map(r=>({count:r.count,color:r.color||'white',fill:r.fill||null,anim:r.anim||'',fx:r.fx||'',dec:parseInt(r.dec)||0,is_count:!!r.is_count,cnt_words:(Array.isArray(r.cnt_words)?r.cnt_words.slice():null),break:!!r.break,from:(r.from!=null?r.from:null),gx:r.gx||0,gy:r.gy||0,gs:r.gs||100,accent:!!r.accent,back:!!r.back}));j.intromode=val('intromode');
  j.exposure=parseFloat(val('aeexposure'))||0;j.censor=$('censor').checked;
  j.music_random=(val('musicmode')==='random');j.music=j.music_random?'':val('aemusic').trim();
  // Копия стиля живёт в задании ТОЛЬКО у безымянного кастома: у именованного стиля
  // источник — файл стиля, его читает сборка (styles.resolve по имени). Копия в задании
  // и была причиной рассинхрона: правка стиля до клипа не доезжала.
  const sk=(val('style')==='__edit__'?(STYLE_EDITING||null):(val('style')||null));
  j.styleKey=sk;
  if(sk&&sk!=='__custom__'&&STYLES[sk])delete j.style;else j.style=CURSTYLE;
  c.job=j;
  j.ins=INS.map(x=>({...x}));
  saveState();}
// число камер — ПО КЛИПУ (из xml_state/parse_full), радио шага 1 — только фолбэк:
// в одном наборе спокойно живут 1- и 2-камерные файлы.
function clipNcams(c){return (c&&c.status&&c.status.ncams)||nCams();}
// В сборку уходит ИМЯ стиля, а не копия; стиль резолвит бэкенд (_norm_build_jobs через
// styles.resolve); копия остаётся только у безымянного кастома; раньше слали копию и
// правка стиля до клипа не доезжала.
function styleForJob(j){
  const k=j&&j.styleKey;
  if(k&&k!=='__custom__'&&k!=='__edit__'&&typeof STYLES!=='undefined'&&STYLES[k])return k;
  return (j&&j.style)||null;}
function jobForBuild(c){const j=c.job||defJob();
  return {xml:c.xml,music:j.music_random?'':(j.music||''),music_random:!!j.music_random,music_dir:val('aemusicdir').trim(),
    // Папка для .jsx — из тега спикера (задание N); пусто = глобальное поле на шаге 3.
    outdir:effOutdir(c)||'',
    highlights:j.highlights||[],hl_breaks:j.hl_breaks||[],hl_count:j.hl_count||[],hl_joins:j.hl_joins||[],inserts:(j.ins||[]).filter(r=>(r.media||'').trim()),
    intro:[],intro_remove:[],intro_splits:[],introRows:j.introRows||[],intro_mode:j.intromode||'word',
    cams:clipNcams(c),exposure:j.exposure||0,style:styleForJob(j),censor:j.censor!==false};}
// intro для сборки надо резолвить по словам ЭТОГО файла — делаем на бэке? нет: резолвим из introRows+загруженных слов текущего.
// поэтому перед сборкой текущего используем introResolve(); для набора — по сохранённым introRows и словам каждого (грузим).

// запуск фоновой сборки: лог в /api/status (модалка «Логи»), прогресс-оверлей, переживает F5
// файлы вставок, ушедших в проект, бэкенд переносит из «Скаченного» в базу — старые
// пути в нашем состоянии надо починить, иначе следующая сборка не найдёт файл.
function applyInsMoved(map){
  const keys=Object.keys(map||{});if(!keys.length)return 0;
  const idx={};keys.forEach(k=>idx[normInsPath(k)]=map[k]);
  const fix=arr=>(arr||[]).forEach(x=>{const n=idx[normInsPath(x.media)];if(n)x.media=n;});
  CLIPS.forEach(c=>{fix(c.inserts);fix(c.job&&c.job.ins);});
  fix(INS);
  uiLog(t('прибрано в базу вставок: {n} файлов (переехали из «Скаченного»)',{n:keys.length}));
  saveState();renderIns();renderInsHost();
  return keys.length;}
async function startBuild(jobs,mode,outdir){const el=$('aeres');el.className='muted';el.textContent='';
  const d=await (await fetch('/api/build_run',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({jobs,mode,outdir:outdir!==undefined?outdir:AEGLOBAL,dest:(val('illdest')||'').trim()})})).json();
  if(d.error){el.className='err';el.textContent='⚠ '+errText(d);return;}
  uiBusySet(true);progShow(t('Сборка .jsx'),t('готовлю…'));logReset();pollBuild();}
async function pollBuild(){pollJob(pollBuild,t('Сборка .jsx'),0.3,d=>{const res=d.results||[];const el=$('aeres');
    uiBusySet(false);
    applyInsMoved(d.insmoved);   // вставки переехали в базу — чиним пути у себя
    if(res.length){el.className='ok';el.textContent=res.map(p=>p.replace(/^.*[\\\/]/,'')).join('  ·  ');
      progDone((UICANCEL?t('Остановлено — собрано '):t('Готово: '))+res.length+t(' .jsx'));}
    else if(UICANCEL){el.className='muted';el.textContent=t('Остановлено');progDone(t('Остановлено'));}
    else{el.className='err';el.textContent=t('⚠ Ничего не собралось — смотри Логи');
      progDone(t('Ошибка — смотри Логи'));$('progFill').className='progfill';}});}

async function tojsx(){if(uiBusyGuard())return;if(curAE<0){toast(t('Выбери клип'));return;}captureAE();const c=CLIPS[curAE];const xml=c.xml;
  const ir=introResolve();const j=c.job;
  const body={xml,music:j.music_random?'':(j.music||''),music_random:!!j.music_random,music_dir:val('aemusicdir').trim(),
    outdir:effOutdir(c)||'',          // папка клипа — из тега спикера (задание N)
    highlights:(HLXML===xml)?[...HL]:[],hl_breaks:(HLXML===xml)?[...BRK]:[],hl_count:(HLXML===xml)?[...CNT]:[],hl_joins:(HLXML===xml)?[...JNS]:[],inserts:INS.filter(r=>(r.media||'').trim()),
    intro:ir.lines,intro_remove:ir.remove,intro_splits:ir.splits,intro_mode:val('intromode'),
    censor:$('censor').checked,cams:clipNcams(c),exposure:parseFloat(val('aeexposure'))||0,
    style:styleForJob({styleKey:(val('style')==='__edit__'?STYLE_EDITING:val('style')),style:CURSTYLE})};
  await startBuild([body],'separate');}

// ================= безголовый рендер в AE (задание BD, шаг 3) =================
// Кнопка «Собрать и отрендерить»: тот же джоб, что у сборки текущего, но бэкенд
// строит БЕЗГОЛОВЫЙ .jsx (очередь+save+quit), гоняет verify_jsx ДО After Effects
// и рендерит aerender'ом. Свой RJOB на бэкенде, прогресс — честный процент
// (кадр / кадры из плана).
let RENDERPOLL=0;
let RENDERSEEN=0;   // сколько строк рендер-лога уже в общем кэше (RJOB отдаёт только хвост)
// «Собрать и отрендерить» идёт по отмеченным галочками, а НЕ по открытому клипу —
// кнопка стояла вплотную к «Только текущий» и читалась как «текущий», хотя рендерила
// CLIPS[curAE] (задание BI, прогон 2026-08-14: галочка на 01, отрендерился 04).
// Набор собирается той же функцией, что и «Собрать набор» — вторая копия тут
// гарантированно даст «собралось одно, отрендерилось другое».
async function startRender(){if(uiBusyGuard())return;const jobs=await collectJobs();if(jobs===null)return;
  // Рендер нескольких клипов всегда собирает один общий .jsx (_run_render_combined),
  // радио multimode на рендер не влияет — оно только для ручной сборки «Собрать набор»
  // (решение пользователя 2026-09-11).
  const body={jobs,outdir:buildOutdir(),render_dir:val('aerenderdir').trim()||AERENDER};
  const el=$('aeresrend');if(el){el.className='muted';el.textContent=t('рендер…');}
  RENDERSEEN=0;
  let d;try{d=await (await fetch('/api/render_run',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)})).json();}
  catch(e){if(el){el.className='err';el.textContent='⚠ '+e;}toast(t('Сервер не ответил: ')+e);return;}
  if(d.error){if(el){el.className='err';el.textContent='⚠ '+errText(d);}toast(errText(d)||d.error);return;}
  uiBusySet(true);progShow(t('Рендер AE'),t('готовлю…'));logReset();pollRender();}
// Сбои fetch подряд (как в pollJob): обрыв связи не должен выглядеть рендером.
// После трёх провалов — в оверлее видно «сервер не отвечает — жду…», задача жива
// и ретраится как раньше (задание по UI-состояниям).
let RENDERPOLLFAIL=0;
async function pollRender(){
  let d;try{d=await (await fetch('/api/render_status')).json();}
  catch(e){RENDERPOLLFAIL++;if(RENDERPOLLFAIL>=3)progUpdate(null,t('сервер не отвечает — жду…'),t('Рендер AE'),undefined);
    setTimeout(pollRender,1500);return;}
  RENDERPOLLFAIL=0;
  // рендер пишет в СВОЙ лог (RJOB); RJOB отдаёт только хвост, поэтому добавляем
  // НОВЫЕ строки по счётчику, а не весь массив (mergeLog с log_total тут не годится:
  // total = длина хвоста не растёт монотонно, и строки дублировались бы каждый тик).
  if(d.log&&d.log.length>RENDERSEEN){
    const incoming=d.log.slice(RENDERSEEN);
    LOGCACHE.push(...incoming);RENDERSEEN=d.log.length;
    if(incoming.length)LOGLAST='server';
    for(const l of incoming){const s=fmtLog(l);const m=s.match(/\[(\d+)\/(\d+)\]/);if(m)PROGMARK=m;}
    if(LOGCACHE.length>4000)LOGCACHE.splice(0,LOGCACHE.length-4000);refreshLog();}
  queueRender(d);   // очередь этапов — общая дверь рендера (задание FA)
  const el=$('aeresrend');
  // версия AE и папка вывода — «на виду», а не в глубине лога (задание BD)
  const sub=[d.ae||'',d.out_dir||''].filter(Boolean).join('  ·  ');
  const cur=(d.cur||'').replace(/^.*[\\\/]/,'');
  // Общий процент рендера по набору (задание FA, FO): честный процент с сервера d.pct
  // (монотонная шкала 0..15% сборка таймлайнов, 15..35% сборка проекта, 35..100% рендер).
  const items=d.items||[];const total=(d.stage_total!=null&&d.stage_total>0)?d.stage_total:items.length;
  let doneN=0,curn=null;
  for(const it of items){if(it.stage==='done')doneN++;else if(it.stage==='render'||it.stage==='jsx'||it.stage==='aep'){if(!curn)curn=it;}}
  const share=(curn&&curn.pct!=null)?curn.pct:0;
  const frac=(d.pct!=null)?Math.max(0,Math.min(1,d.pct)):(total?Math.min(1,(doneN+share)/total):null);
  const pos=(d.stage_done!=null)?d.stage_done:(doneN+(curn?1:0));
  // «3/12 · <имя> · сборка таймлайнов 5%» — подпись оверлея
  let qhead=total?pos+'/'+total:'';
  if(total){
    const nm=cur||(curn&&curn.name)||'';
    if(nm)qhead+=' · '+nm;
    const lbl=d.stage_label||(curn?'рендер':'');
    if(lbl){
      const p=Math.round((frac||0)*100);
      qhead+=' · '+t(lbl)+' '+p+'%';
    }
  }
  // ETA (задания FK, FQ): оценка времени до конца текущей фазы и всей работы.
  // Прочерк, пока замеров меньше 15 с и нет статистики (бэкенд отдаёт eta=null).
  const etaPhase=(d.eta_phase!=null&&d.eta_phase>0)?d.eta_phase:((d.eta!=null&&d.eta>0)?d.eta:null);
  const etaTot=(d.eta_total!=null&&d.eta_total>0)?d.eta_total:etaPhase;
  if(etaTot!=null&&etaTot>0){
    const isPrelim=!!d.eta_preliminary;
    const pfx=isPrelim?'~':'';
    const lbl=d.stage_label||'рендер';
    if(etaPhase!=null&&etaTot!=null&&Math.abs(etaTot-etaPhase)>5){
      qhead+=' · '+t('фаза {phase} · всего {total}',{phase:pfx+fmtEta(etaPhase),total:pfx+fmtEta(etaTot)});
    }else{
      qhead+=' · '+t('до конца {lbl}: {eta}',{lbl:t(lbl),eta:pfx+fmtEta(etaTot)});
    }
  }
  // Подпись собираем одной строкой ДО вызова: progUpdate сам ставит разделитель между
  // ЧЕТВЁРТЫМ и ВТОРЫМ аргументом, и при пустой sub оставлял бы висящий хвост ' · '.
  // Склеиваем только непустые куски и передаём их вторым аргументом (stage), а четвёртый
  // (sub) — null, чтобы хвост не возникал.
  const subsig=[qhead,sub].filter(Boolean).join(' · ');
  progUpdate(frac,subsig,t('Рендер AE'),null);
  if(d.running){setTimeout(pollRender,1000);return;}
  uiBusySet(false);
  const bad=d.failed||[];
  const res=d.result||[];   // набор рендерит много файлов — RJOB копит список (задание BI)
  if(res.length){if(el){el.className='ok';el.textContent=res.map(p=>p.replace(/^.*[\\\/]/,'')).join('  ·  ');}
    progDone((UICANCEL?t('Остановлено — готово: '):t('Готово: '))+res.length+' '+t(plur(res.length,'файл','файла','файлов')));}
  else if(bad.length){if(el){el.className='err';el.textContent=t('⚠ {n} — смотри Логи',{n:bad.length});}
    progDone(UICANCEL?t('Остановлено'):(t('Ошибка — смотри Логи')+' ('+bad[0].reason+')'));$('progFill').className='progfill';}
  else{progDone(UICANCEL?t('Остановлено'):t('Ошибка — смотри Логи'));$('progFill').className='progfill';}}

// Общий сбор набора для «Собрать набор» и «Собрать и отрендерить»: отмечены
// галочками — только они, пусто = все; проверка разметки с тем же вопросом;
// интро резолвится по словам КАЖДОГО файла. Одна копия на обе кнопки — разъехавшиеся
// копии дали бы ровно «собралось одно, отрендерилось другое» (задание BI).
async function collectJobs(){if(curAE>=0)captureAE();if(!CLIPS.length){toast(t('Нет клипов'));return null;}
  // Клип без субтитров/жёлтых уходил в сборку молча: разметка упала на одном из восьми,
  // юзер не заметил в потоке — и узнал уже в After Effects. Зелёная рамка .clip.ready
  // это показывала, но кнопка её не читала.
  const bad=selClips().filter(c=>!clipReady(c));
  if(bad.length&&!await askConfirm(t('Без разметки {n} {p}: {names}.\nСобрать всё равно?',{n:bad.length,p:t(plur(bad.length,'клип','клипа','клипов')),names:bad.map(c=>c.name).join(', ')})))return null;
  const jobs=[];
  for(const c of selClips()){const j=c.job||defJob();   // отмечены галочками — только они; пусто = все
    let ir={lines:[],remove:[],splits:[]};
    if((j.introRows||[]).length){try{const wd=await (await fetch('/api/words',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({xml:c.xml})})).json();
      const words=wd.words||[];ir=resolveIntroFor(j.introRows,words);}catch(e){}}
    const jb=jobForBuild(c);jb.intro=ir.lines;jb.intro_remove=ir.remove;jb.intro_splits=ir.splits;jobs.push(jb);}
  return jobs;}
// outdir набора — по набору целиком: один спикер = его папка, иначе общая (AEGLOBAL).
function buildOutdir(){const sel=selClips();const spks=new Set(sel.map(c=>(c.job||{}).speaker||'').filter(Boolean));
  return (spks.size===1)?effOutdir(sel[0])||AEGLOBAL:AEGLOBAL;}
async function buildMulti(){if(uiBusyGuard())return;const jobs=await collectJobs();if(jobs===null)return;
  const mode=document.querySelector('input[name=multimode]:checked').value;
  await startBuild(jobs,mode,buildOutdir());}
function resolveIntroFor(rows,words){let off=0;const lines=[],remove=[],splits=[];
  rows=introSortRows(rows,words)||rows;   // группы — по таймингу, как в панели
  rows.forEach((r,i)=>{if(r.from!=null&&r.from>=0&&r.from<words.length)off=r.from;
    const cnt=Math.max(0,r.count|0),ws=[],ts=[];
    for(let k=0;k<cnt&&off<words.length;k++){ws.push(words[off].w);ts.push(words[off].start);remove.push(words[off].i);off++;}
    if(ws.length){
      if(i>0&&(r.break||r.from!=null))splits.push(lines.length);
      // Смещение ГРУППЫ живёт на головной строке (первая в списке ИЛИ break/from).
      // При разрезании/слиянии групп оно остаётся у той строки, которая стала головной:
      // у новой группы, начатой строкой без gx/gy, смещение 0/0. Поэтому несём gx/gy
       // только на голове — середина группу не двигает, и её поля не должны лезть в данные.
       const head=(i===0||r.break||r.from!=null);
       let decVal=parseInt(r.dec)||0;
       for(let wi=0;wi<ws.length;wi++){
         const s=(''+ws[wi]).trim();
         const m=s.match(/^[0-9\s]*[.,]([0-9]+)$/);
         if(m){decVal=m[1].length;break;}
         if(/^[0-9\s]+$/.test(s)){decVal=0;break;}
       }
       lines.push({text:ws.join(' '),color:r.color||'white',fill:r.fill||null,anim:r.anim||'',fx:r.fx||'',dec:decVal,is_count:!!(r.is_count||r.anim==='count'),cnt_words:(Array.isArray(r.cnt_words)?r.cnt_words.slice():null),times:ts,words:ws,
         gx:(head?(r.gx||0):0),gy:(head?(r.gy||0):0),gs:(head?(r.gs||100):100),accent:!!r.accent,back:!!r.back});}});
  return {lines,remove,splits};}

// ================= words / intro (ported) =================
let HL=new Set(),BRK=new Set(),CNT=new Set(),JNS=new Set(),WORDS=[],HLXML='',WORDSREQ=0;
async function loadWordsFor(xml){const info=$('wordsinfo');
  const req=++WORDSREQ;
  // мгновенно чистим панель: пока грузятся слова НОВОГО клипа, не показывать слова/тайминги старого
  WORDS=[];renderIntro();info.textContent=t('Читаю слова…');
  let d;try{d=await (await fetch('/api/words',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({xml})})).json();}
  catch(e){if(req===WORDSREQ)info.textContent='⚠ '+e;return;}
  if(req!==WORDSREQ)return;   // за время запроса выбрали другой клип — этот ответ уже не наш
  if(d.error){info.textContent='⚠ '+errText(d);return;}
  WORDS=d.words;if(HLXML!==xml){HL=new Set();BRK=new Set();CNT=new Set();JNS=new Set();HLXML=xml;
    if(Array.isArray(d.yellow)&&d.yellow.length){HL=new Set(d.yellow);if(Array.isArray(d.breaks))BRK=new Set(d.breaks);}}
  wordsInfo();renderIntro();aewRender();}
// ---- группы интро: главная строка + продолжение ----
// Прекомп = «главная» строка (первая в списке, либо с break, либо якорь-акцент с `from`) плюс идущие
// за ней строки без break/from. Главная задаёт тайминг группы — на ней «+» добавляет строку в конец
// ЭТОЙ группы (стрелки ↑/↓ убраны: «вниз» добавляла, а не убирала — читалось наоборот).
function introIsHead(rows,i){return i===0||!!rows[i].break||rows[i].from!=null;}
function introGroupEnd(rows,i){let k=i+1;while(k<rows.length&&!introIsHead(rows,k))k++;return k;}
// кнопка «+» у главной строки; у остальных строк колонка пустая (чтобы не съезжала вёрстка)
function introPlus(rows,i,call){return '<span class="introar">'
  +(introIsHead(rows,i)?'<button class="icon plus" aria-label="'+t('Добавить строку в прекомп')+'" data-t="'+t('Добавить строку в этот прекомп')+'" onclick="'+call+'('+i+')">'+ico('plus')+'</button>':'')
  +'</span>';}
function introAddInGroup(rows,i){const at=introGroupEnd(rows,i);
  rows.splice(at,0,{count:1,color:(rows[i]||{}).color||'white'});return at;}
function aewAddLineIn(i){const at=introAddInGroup(INTRO,i);if(INTRO_PICK>=at)INTRO_PICK++;aewSync();}
function pvwAddLineIn(i){const at=introAddInGroup(PVW.intro,i);if(PVW.pick>=at)PVW.pick++;pvwCommitIntro();pvwRender();}
// ---- тайминг строк интро (как на карточках вставок: ▶ 1:02.4 (1.8с)) ----
// ts = моменты слов строки. Пусто (count=0 / слова не загружены) — прочерк вместо кнопки.
function introTimeBadge(ts){
  if(!ts||!ts.length)return '<span class="mono muted introtb">—</span>';
  const t0=ts[0],d=Math.round((ts[ts.length-1]-t0)*10)/10;
  return '<span class="mono muted introtb" data-t="'+t('Первое слово строки')+(d?(t(' · строка договаривается за ')+d+t('с')):'')+'">'
    +fmtIns(t0)+(d?(t(' ({n}с)',{n:d})):'')+'</span>';}
// ---- «сейчас играет вот эта группа» ----
// Как карточка вставки .playing в предпросмотре: по плейхеду подсвечиваем строки того прекомпа,
// который в этот момент висит за спиной. gi — индекс группы (как в introGroupWindows), -1 = нет.
let INTROPLAY=-2;
function introMarkPlaying(gi){
  if(gi===INTROPLAY)return;INTROPLAY=gi;
  ['aewintrolist','pvwintro'].forEach(id=>{const host=$(id);if(!host)return;
    host.querySelectorAll('[data-ig]').forEach(e=>e.classList.toggle('playing',+e.dataset.ig===gi));});
  if(gi<0)return;
  const l=$('aewintrolist');   // компактный скролл-список — доводим текущую группу до глаз
  if(l){const e=l.querySelector('.pchdr[data-ig="'+gi+'"],[data-ig="'+gi+'"]');
    if(e)e.scrollIntoView({block:'nearest',behavior:'smooth'});}}
// ---- порядок интро по таймингу ----
// Строка с `from` = якорь (группа с середины ролика); идущие за ней строки без `from` берут слова
// подряд, т.е. это одна цепочка. Сортируем ЦЕПОЧКАМИ по слову-якорю: акцент, поставленный последним,
// встаёт туда, где он реально звучит. Возвращает новый массив или null, если порядок уже верный.
function introSortRows(rows,words){
  if(!rows||rows.length<2||!words||!words.length)return null;
  const runs=[];
  rows.forEach(r=>{const at=(r.from!=null&&r.from>=0&&r.from<words.length)?r.from:-1;
    if(!runs.length||at>=0)runs.push({at:(at>=0?at:0),n:runs.length,rows:[]});
    runs[runs.length-1].rows.push(r);});
  const out=[];runs.slice().sort((a,b)=>(a.at-b.at)||(a.n-b.n)).forEach(g=>g.rows.forEach(r=>out.push(r)));
  for(let k=0;k<out.length;k++)if(out[k]!==rows[k])return out;
  return null;}
function introReorder(){const s=introSortRows(INTRO,WORDS);if(!s)return false;
  const pick=(INTRO_PICK>=0?INTRO[INTRO_PICK]:null);
  INTRO.length=0;s.forEach(r=>INTRO.push(r));
  INTRO_PICK=pick?INTRO.indexOf(pick):-1;return true;}
// обход интро-строк: row.from (индекс слова) = группа начинается С СЕРЕДИНЫ ролика, не подряд
function introWalk(fn){introReorder();let off=0;INTRO.forEach((r,i)=>{
  if(r.from!=null&&r.from>=0&&r.from<WORDS.length)off=r.from;
  const c=Math.max(0,r.count|0),idxs=[];
  for(let k=0;k<c&&off<WORDS.length;k++){idxs.push(off);off++;}
  fn(r,i,idxs);});}
function introConsumed(){const s=new Set();introWalk((r,i,idxs)=>{idxs.forEach(x=>s.add(WORDS[x].i));});return s;}
// слово, ушедшее в интро, жёлтым быть не может — снимаем выделение при каждом пересчёте
function hlDropIntro(){const cons=introConsumed();cons.forEach(i=>{HL.delete(i);BRK.delete(i);});return cons;}
function wordsInfo(){const cons=hlDropIntro().size;const el=$('wordsinfo');if(!el)return;
  el.textContent=t('{n} слов · выделено {h}',{n:WORDS.length-cons,h:HL.size})+(cons?(t(' · в интро ')+cons):'');}
let INTRO=[];
// акцентов СТОЛЬКО, сколько голов (break), а не строк: длинный акцент приезжает стопкой
// из 2-3 строк в одном прекомпе, и «21 акцент» вместо 9 в логе только путал.
function midCount(d){return ((d||{}).mid_groups||[]).filter(g=>g.break!==false).length;}
// mid_groups приходят уже с переносом по словам: голова несёт break+from, продолжения
// строк — break:false, from:null (добирают слова подряд, встают в тот же прекомп).
function introRowsFromAI(d){
  return (d.intro_rows||[]).map(r=>({count:r.count,color:r.color,break:!!r.break,
      back:!!r.back,anim:r.anim||'',fx:r.fx||''}))
    .concat((d.mid_groups||[]).map(g=>({count:g.count,color:g.color,
      break:(g.break!==false),from:(g.from!=null?g.from:null),
      back:!!g.back,anim:g.anim||'',fx:g.fx||''})));}
async function aiIntroRun(){if(uiBusyGuard())return;   // идёт пакетный прогон — второй вызов ИИ параллельно не пускаем
  if(curAE<0){toast(t('Выбери клип'));return;}const c=CLIPS[curAE];const el=$('introres');
  el.className='muted';el.textContent=t('ИИ размечает…');uiLog(t('интро (ИИ) для ')+c.name+t('…'));
  // шлём вставки клипа — акценты за спиной встанут туда, где вставок нет
  try{const d=await aiFetch('/api/ai_intro',{xml:c.xml,
        inserts:(c.inserts||[]).map(x=>({start_sec:x.start_sec,duration_sec:x.duration_sec}))},'introStop','introres');
    if(d.error){el.className='err';el.textContent='⚠ '+errText(d);uiLog(t('  ОШИБКА: ')+d.error);return;}
    insLog(d);
    INTRO=introRowsFromAI(d);
    INTRO_PICK=-1;renderIntro();captureAE();aewRender();   // панель интро живёт в предпросмотре — её и обновляем
    el.className='ok';el.textContent=t('{n} строк + {m} акцентов',{n:(d.intro_rows||[]).length,m:midCount(d)});
    uiLog(t('  интро: ')+(d.intro_rows||[]).length+t(' строк, акцентов в середине: ')+midCount(d));
  }catch(e){if(aiAborted(e)){el.className='muted';el.textContent=t('⏹ остановлено');return;}
    el.className='err';el.textContent='⚠ '+e;}}
// ИИ-интро сразу на ВСЕ отмеченные файлы. Раньше это делалось по одному: открыть предпросмотр
// клипа → «ИИ интро» → закрыть, и так восемь раз на набор. Отмечает те же галочки, что и сборка
// (selClips: пусто у всех = все), идёт по общему пакетному шаблону (оверлей + «Остановить»).
async function aiIntroAll(){if(uiBusyGuard())return;
  if(curAE>=0)captureAE();                      // не потерять правки открытого клипа
  const list=selClips();if(!list.length){toast(t('Нет клипов'));return;}
  const have=list.filter(c=>((c.job||{}).introRows||[]).length);
  if(have.length&&!await askConfirm(t('Интро уже размечено у {n} {p}: {names}.\nИИ заменит эту разметку. Продолжить?',{n:have.length,p:t(plur(have.length,'файла','файлов','файлов')),names:have.map(c=>c.name).join(', ')})))return;
  progShow(t('ИИ интро'),'—');uiBusySet(true);
  try{await aiIntroAllRun(list);}finally{uiBusySet(false);}}
async function aiIntroAllRun(list){
  const N=list.length;let ok=0,fail=0,skip=0;
  uiLog(t('интро (ИИ) — файлов: ')+N);
  for(let i=0;i<N;i++){if(UICANCEL)break;const c=list[i];
    progUpdate(i/N,c.name,t('ИИ интро'),t('файл {n} из {m}',{n:i+1,m:N}));
    uiLog('▸ '+c.name+t(' — интро (ИИ)…'));
    // без субтитров интро собирать не из чего — на бэкенде это ошибка, но пропуск честнее
    if(!((c.status||{}).subs>0)){skip++;uiLog(t('  пропуск — нет субтитров'));continue;}
    try{const d=await aiPost('/api/ai_intro',{xml:c.xml,
        inserts:(c.inserts||[]).map(x=>({start_sec:x.start_sec,duration_sec:x.duration_sec}))},t('интро (ИИ)'));
      if(d.error)throw errText(d);
      insLog(d);
      c.job=c.job||defJob();c.job.introRows=introRowsFromAI(d);ok++;
      uiLog(t('  интро: ')+(d.intro_rows||[]).length+t(' строк, акцентов в середине: ')+midCount(d));
      // открытый в предпросмотре клип обновляем и в панели, иначе на экране осталась бы старая разметка
      if(curAE>=0&&c===CLIPS[curAE]){INTRO=c.job.introRows.map(r=>({count:r.count,color:r.color||'white',fill:r.fill||null,anim:r.anim||'',fx:r.fx||'',dec:parseInt(r.dec)||0,is_count:!!r.is_count,cnt_words:(Array.isArray(r.cnt_words)?r.cnt_words.slice():null),break:!!r.break,from:(r.from!=null?r.from:null),gx:r.gx||0,gy:r.gy||0,gs:r.gs||100,accent:!!r.accent,back:!!r.back}));INTRO_PICK=-1;
        renderIntro();captureAE();aewRender();}
    }catch(e){fail++;toast(t('интро · ')+c.name+t(': ')+e);uiLog(t('  ОШИБКА: ')+e);}
    saveState();await sleep(300);}
  syncClipLists();
  const tail=(fail?(t(' · ошибок ')+fail):'')+(skip?(t(' · без субтитров ')+skip):'');
  if(UICANCEL)progDone(t('Остановлено — интро размечено {a} из {b}',{a:ok,b:N})+tail);
  else if(!fail&&!skip)progDone(t('Интро размечено: {a} из {b}',{a:ok,b:N}));
  else{progDone(t('Интро {a} из {b}',{a:ok,b:N})+tail+t(' — см. логи'));$('progFill').className='progfill';}}
// Тонкая обёртка над resolveIntroFor (задание BG, 2026-08-14): своя копия обхода строк
// здесь разошлась с близнецом и теряла gs группы — превью после рефетча плана возвращало
// блок к 100%, а в сборку текущего клипа (tojsx/startRender) масштаб группы не уезжал.
// introReorder() оставлен: панель интро сортирует INTRO по таймингу, и порядок строк
// менять по-прежнему надо (resolveIntroFor сортирует только свою копию).
function introResolve(){introReorder();return resolveIntroFor(INTRO,WORDS);}
let INTRO_PICK=-1;   // индекс строки, ждущей клика по слову («начать группу с этого слова»)
// Строки интро правятся только в предпросмотре (aewRenderIntro) — на вкладке AE их панели больше нет.
// Здесь остаётся то, что нужно всем: пересортировать группы по таймингу и обновить оверлей интро.
function renderIntro(){introReorder();wordsInfo();
  INTROPLAY=-2;                                        // строки перерисованы — подсветку навесить заново
  if(typeof aewOn==='function'&&aewOn())ipvIntroRefresh();}   // открыт предпросмотр → таймлайн интро сразу

// ================= INS (AE manual inserts, collapsed) =================
let INS=[];
function addIns(){INS.push({type:'photo',style:'cam2',media:'',start_s:0,start_f:0,dur_s:2,dur_f:0,scale:44,mosaic:false,x:0,y:0,sc:100,mw:100,mh:100,sin:0});renderIns();captureAE();}
function seg2(cur,a,al,b,bl,cbp,name){function opt(v,lab){return '<label class="'+(cur===v?'on':'')+'"><input type="radio" name="'+name+'" '+(cur===v?'checked':'')+' onchange="'+cbp+"'"+v+"')\"> "+lab+'</label>';}
  return '<div class="seg">'+opt(a,al)+opt(b,bl)+'</div>';}
function insType(i,v){INS[i].type=v;renderIns();captureAE();}
function insStyle(i,v){INS[i].style=v;renderIns();captureAE();}
function tcField(i,k,lbl){return '<div class="fld"><label>'+lbl+'</label><span class="tc">'
  +'<input type="number" min="0" step="1" value="'+INS[i][k+'_s']+'" oninput="INS['+i+'].'+k+'_s=parseInt(this.value)||0;captureAE()"><span class="u">'+t('с')+'</span>'
  +'<input type="number" min="0" step="1" value="'+INS[i][k+'_f']+'" oninput="INS['+i+'].'+k+'_f=parseInt(this.value)||0;captureAE()"><span class="u">'+t('к')+'</span></span></div>';}
function renderIns(){const host=$('inslist');if(!host)return;host.innerHTML='';
  INS.forEach((r,i)=>{const card=document.createElement('div');const photo=r.type==='photo';const fname=(r.media||'').replace(/^.*[\\\/]/,'');
    insScrubInit(r);                                  // скрабберы тянут значение объекта
    card.className='inscard '+(photo?'tphoto':'tvideo')+(fname?' chosen':'');
    let fields='';
    if(photo){fields+='<div class="fld"><label>'+t('Стиль')+'</label>'+seg2(r.style,'cam2',t('Кам2'),'cam1',t('Кам1·рото'),'insStyle('+i+',','insStyle'+i)+'</div>';
      fields+='<div class="fld"><label>'+t('Эффект')+'</label><label class="chk" style="display:flex;align-items:center;gap:6px;margin:0;height:34px"><input type="checkbox" '+(r.mosaic?'checked':'')+' onchange="INS['+i+'].mosaic=this.checked;this.blur();captureAE()">'+t(' мозаика')+'</label></div>';
      fields+='<div class="fld"><label>'+t('Маска %')+'</label><div style="height:34px;display:flex;align-items:center">'
        +scrubMask('INS['+i+']',r.mw,r.mh,'ipvRefresh()','captureAE()')+'</div></div>';}
    else{fields+='<div class="fld"><label>'+t('Файл с')+'</label><div style="height:34px;display:flex;align-items:center">'
        +scrubSin('INS['+i+']',r.sin,'ipvRefresh()','captureAE()')+'</div></div>';}
    // Масштаб — множитель к авто, а не абсолютный scale: у фото xml2ae пересчитывает scale
    // по пропорциям картинки на каждой сборке, и абсолютное число тут просто затиралось.
    fields+='<div class="fld"><label>'+t('Масштаб %')+'</label><div style="height:34px;display:flex;align-items:center">'
      +scrubScale('INS['+i+']',r.sc,'ipvRefresh()','captureAE()')+'</div></div>';
    fields+=tcField(i,'start',t('Старт'))+tcField(i,'dur',t('Длит.'));
    fields+='<div class="fld"><label>'+t('Положение')+'</label><div style="height:34px;display:flex;align-items:center">'
      +scrubXY('INS['+i+']',r.x,r.y,'ipvRefresh()','captureAE()')+'</div></div>';   // тянешь — видно в кадре, отпустил — записалось
    card.innerHTML='<div class="top"><span class="badge '+r.type+'">'+ico(photo?'img':'film','gold')+(photo?t('ФОТО'):t('ВИДЕО'))+'</span>'+seg2(r.type,'photo',t('фото'),'video',t('видео'),'insType('+i+',','insType'+i)
      +'<span class="grow"></span><span class="del" tabindex="0" role="button" aria-label="'+t('Удалить вставку')+'" data-t="'+t('Удалить вставку')+'" onclick="INS.splice('+i+',1);renderIns();captureAE()">'+ico('x')+'</span></div>'
      +'<div class="media"><span class="name '+(fname?'':'empty')+'" title="'+esc(r.media||r.query)+'">'+(fname?'<span data-noi18n>'+esc(fname)+'</span>':(r.query?'<span data-noi18n>'+esc(r.query)+'</span>':t('файл не выбран')))+'</span><button class="sm" onclick="pickIns('+i+')">'+t('Выбрать…')+'</button></div>'
      +'<div class="rowline">'+fields+'</div>';
    host.appendChild(card);});
  const info=$('insinfo');if(info){const nv=INS.filter(r=>r.type==='video').length;info.textContent=INS.length?(INS.length+t(' ({p} фото + {v} видео)',{p:INS.length-nv,v:nv})):t('нет');}}
async function pickIns(i){try{const d=await (await fetch('/api/pickmedia')).json();
  if(d.path){const r=INS[i];insSetMedia(r,d.path);
    // src2-карточка шага 2 без файла: выбранный файл пишем и в неё, иначе пересборка
    // (ensureJobs) вернёт заглушку без файла и выбор молча потеряется
    if(r.src2&&r.idx!=null&&curAE>=0&&CLIPS[curAE]){const cd=CLIPS[curAE].inserts[r.idx];
      if(cd){cd.media=d.path;const k=insKind(d.path);if(k)cd.type=k;saveState();}}
    renderIns();captureAE();}}
  catch(e){toast(t('Не открылся выбор файла — сервер не ответил'));uiLog('pickmedia: '+e);}}

// Правка / удаление слова в AE-панели (переопределяет aewSaveWord из 80-inserts.js):
// пустое поле удаляет слово через /api/delete_word, сдвигает наборы и вызывает captureAE.
async function aewDeleteWord(o){
  const xml=CLIPS[curAE]?CLIPS[curAE].xml:HLXML;
  if($('aewres')){$('aewres').className='muted';$('aewres').textContent=t('удаляю…');}
  try{
    const d=await (await fetch('/api/delete_word',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({xml,index:o.i})})).json();
    if(d.error){
      if($('aewres')){$('aewres').className='err';$('aewres').textContent='⚠ '+errText(d);}
      aewRender();return;
    }
    const delIdx=d.index;
    shiftIndices(HL,delIdx);
    shiftIndices(BRK,delIdx);
    shiftIndices(CNT,delIdx);
    shiftIndices(JNS,delIdx);
    shiftIntroRows(INTRO,delIdx);
    const wwi=WORDS.findIndex(w=>w.i===delIdx);
    if(wwi>=0)WORDS.splice(wwi,1);
    WORDS.forEach(w=>{if(w.i>delIdx)w.i--;});
    if(typeof IPV!=='undefined'&&IPV.words){
      const iwi=IPV.words.findIndex(w=>Math.abs(w.s-o.start)<0.05);
      if(iwi>=0)IPV.words.splice(iwi,1);
    }
    // Сдвиг в предпросмотре PVW если тот же клип
    if(typeof PVW!=='undefined'&&PVW.xml===xml){
      shiftIndices(PVW.hl,delIdx);
      shiftIndices(PVW.brk,delIdx);
      shiftIndices(PVW.cnt,delIdx);
      shiftIndices(PVW.jns,delIdx);
      shiftIntroRows(PVW.intro,delIdx);
      const pwi=PVW.words.findIndex(w=>w.i===delIdx);
      if(pwi>=0)PVW.words.splice(pwi,1);
      PVW.words.forEach(w=>{if(w.i>delIdx)w.i--;});
      if(typeof pvwCommitIntro==='function')pvwCommitIntro();
      if(typeof pvwRender==='function')pvwRender();
    }
    captureAE();
    if($('aewres')){$('aewres').className='ok';$('aewres').textContent=t('слово удалено');}
    renderIntro();aewRender();
    uiLog(t('удаление слова #{idx}: «{w}» (AE-превью)',{idx:delIdx,w:d.word||o.w}));
  }catch(e){
    if($('aewres')){$('aewres').className='err';$('aewres').textContent='⚠ '+e;}
    aewRender();
  }
}

async function aewSaveWord(o,text){
  text=(text||'').trim();
  if(text===o.w){aewRender();return;}
  if(!text){await aewDeleteWord(o);return;}
  const xml=CLIPS[curAE]?CLIPS[curAE].xml:HLXML;
  if($('aewres')){$('aewres').className='muted';$('aewres').textContent=t('сохраняю…');}
  try{
    const d=await (await fetch('/api/edit_word',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({xml,index:o.i,text,was:o.w})})).json();
    if(d.error){if($('aewres')){$('aewres').className='err';$('aewres').textContent='⚠ '+errText(d);}aewRender();return;}
    const was=o.w;
    o.w=d.word;
    if(typeof IPV!=='undefined'&&IPV.words){
      const iw=IPV.words.find(w=>Math.abs(w.s-o.start)<0.05);if(iw)iw.w=d.word;
    }
    if($('aewres')){$('aewres').className='ok';$('aewres').textContent=t('слово изменено');}
    renderIntro();aewRender();uiLog(t('правка слова (AE-превью): «{t}»',{t:text}));
    if(d.learned)uiLog(t('словарь терминов: запомнил «{w}» → «{l}»',{w:was,l:d.learned}));
  }catch(e){if($('aewres')){$('aewres').className='err';$('aewres').textContent='⚠ '+e;}aewRender();}
}


