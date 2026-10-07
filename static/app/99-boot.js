// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Maxim Si
// сохранение состояния и запуск интерфейса
//
// Часть интерфейса Reelsi. Файлы static/app/ грузятся ПО ПОРЯДКУ ИМЁН обычными
// <script>-тегами (не модулями): один общий скоуп, как было в едином app.js.
// Порядок важен — объявления функций поднимаются в пределах своего файла.

// ================= persistence =================
const LSKEY='reelsi_state';
// Прежнее общее значение «папка музыки» (aemusicdir). Поля в интерфейсе больше нет —
// музыка переехала в стиль, — но само значение остаётся запасной ступенью лестницы
// папок (musicPickDir, 90-ae.js): у владельца в нём рабочая папка с 21 треком, и
// потерять её нельзя. Живёт в памяти и в сохранённом состоянии, в разметку не
// возвращается: второго хранилища того же значения не заводим.
let AEMUSICDIR='';
let LSWARNED=false;             // не спамить тостом: квота localStorage лечится только вручную
let SRVST_T=null,SRVST_LAST='';  // дебаунс серверного зеркала + последняя отправленная строка
const SRVST_DELAY=1200;   // задержка отправки; ОБЯЗАНА быть меньше периода flushSave
                          // (2500 мс), иначе каждый тик отодвигает таймер и зеркало
                          // не пишется никогда — поймано 2026-09-08
const SRVST_MAX_WAIT=20000;  // потолок ожидания: если с последней УСПЕШНОЙ отправки
                             // прошло столько, шлём немедленно
let SRVST_OK_T=Date.now(); // с нулём потолок ожидания срабатывает сразу и первые правки уходят в обход дебаунса пачкой; отсчёт должен начинаться с загрузки страницы
let SRVST_ERR=false;       // «об этой серии провалов уже сообщили» — не спамить uiLog
// Ревизия серверного состояния. Сервер хранит её вместе с состоянием (верхний ключ
// _rev) и отдаёт при загрузке и при каждом успешном сохранении; каждое сохранение
// несёт base_rev — от какой ревизии правка росла. Разошлись — сервер НЕ пишет, значит
// писали из другой вкладки/браузера, и эта копия устарела.
// 0 — «ревизия пока неизвестна» (файла на сервере нет): тогда принимает любая.
let SRVST_REV=0;
// Сервер отверг запись как устаревшую. Сохранения на сервер из этой вкладки
// останавливаются: иначе следующий же тик flushSave затирал бы серверное.
let SRVST_STALE=false;
// Отправка строго по одной. Пока запрос в полёте, новое состояние не уходит, а ждёт
// своей очереди (хранится только последнее). Иначе два быстрых сохранения несут ОДИН
// base_rev, и второе получает stale_state: вкладка объявляет устаревшей саму себя
// (других вкладок нет), а последняя правка теряется. В бою это штатный случай — ответ
// сервера дольше паузы между сохранениями (дебаунс, flushSave, состояние большого
// размера, запись clip.json многих клипов).
let SRVST_SENDING=false;   // запрос в полёте
let SRVST_PENDING=null;    // состояние, ожидающее отправки после ответа (только последнее)
function stateObj(){return {
  // aemusicdir больше не поле интерфейса — музыка переехала в стиль (music_mode/
  // music_dir/music_src). Ключ остаётся в состоянии РОВНО ради переезда и как
  // запасная ступень лестницы папок: значение держит память (AEMUSICDIR), а не
  // разметка, — поля с этим id в index.html нет. typeof — потому что stateObj
  // вырезают и гоняют стенды интерфейса без файла запуска: незаданная переменная
  // там не ошибка, а честное «папки нет».
  base:val('base'),ai_outdir:val('ai_outdir'),aeoutdir:AEGLOBAL,aerender:AERENDER,
  aemusicdir:(typeof AEMUSICDIR!=='undefined'?AEMUSICDIR:''),
  // Движок рендера (какой из двух) — настройка рендера, как папка вывода: живёт в
  // состоянии, а не в разметке. Значение читает сам переключатель (rendEngine).
  rendengine:rendEngine(),
  cams:nCams(),CAMDIRS,CAMFILES,CAMFROM,QUEUE,newonly:$('newonly').checked,
  dedupe:(CUT_STAGES&&CUT_STAGES.dedupe!==undefined)?!!CUT_STAGES.dedupe:false,
  cut_stages:CUT_STAGES,
  cut_thresholds:CUT_THRESHOLDS,
  // genBusy — мгновенный флаг «идёт генерация», в состояние НЕ пишется: иначе
  // flushSave успевает сохранить залипшую «…», и она переживает F5 навсегда —
  // гвардия двойного клика глотает нажатия, а снять некому (поймано 2026-08-10).
  // _typeBusy (переискивание при смене типа вставки) — та же история: только в памяти.
  CLIPS:CLIPS.map(c=>{const cc={...c};cc.inserts=(c.inserts||[]).map(x=>{const xx={...x};delete xx.genBusy;delete xx._typeBusy;return xx;});return cc;}),
  // speaker — общий выбор: новая нарезка/профиль, не клип: он переживает F5 вместе
  // с остальными полями шага 1, а спикер КЛИПА живёт в его собственном job.speaker.
  curAE,styleSel:val('style'),CURSTYLE,STEP,subengine:val('subengine'),speaker:val('speaker'),
  VID:vidStateObj()};}
function saveState(){insHistTouch();   // единственная дверь истории правок вставок (см. 80-inserts.js)
  const s=JSON.stringify(stateObj());
  try{localStorage.setItem(LSKEY,s);}
  catch(e){if(!LSWARNED){LSWARNED=true;toast(t('⚠ localStorage переполнен — состояние хранится только на сервере (это ок)'));
    uiLog(t('⚠ saveState: ')+e);}}
  srvStateSave(s);}
function srvStateSave(s){ // зеркало на сервер: дебаунс SRVST_DELAY, не шлём неизменившееся
  if(SRVST_STALE)return;   // вкладка устарела — серверное состояние не трогаем вовсе
  if(s===SRVST_LAST)return;
  // потолок ожидания: если с последней УСПЕШНОЙ отправки прошло слишком много — сразу
  if(Date.now()-SRVST_OK_T>SRVST_MAX_WAIT){srvStatePost(s);return;}
  clearTimeout(SRVST_T);
  SRVST_T=setTimeout(()=>srvStatePost(s),SRVST_DELAY);}
// Постоянная плашка «эта вкладка устарела». Не тост: тост исчезает через пару секунд,
// а состояние вкладки остаётся расходящимся до перезагрузки — и оставленная без
// предупреждения вкладка выглядит рабочей. Показываем один раз (повторные отказы
// записи — та же причина, спамить нечем). Кнопка «Обновить» — F5: страница заново
// спросит сервер и возьмёт его состояние.
function srvStaleBanner(){
  // Отложенную отправку снимаем ЗДЕСЬ: srvStateSave взвёл её до отказа, и без
  // этого таймер доживал до срабатывания уже устаревшей вкладкой.
  clearTimeout(SRVST_T);SRVST_T=null;
  if(SRVST_STALE)return;
  SRVST_STALE=true;
  if(document.getElementById('srvstale'))return;
  const b=document.createElement('div');
  b.id='srvstale';b.setAttribute('role','alert');
  const txt=document.createElement('span');
  txt.textContent=t('Состояние изменено в другой вкладке или браузере — эта вкладка устарела');
  const btn=document.createElement('button');
  btn.className='sm';btn.textContent=t('Обновить');
  btn.onclick=()=>location.reload();
  b.appendChild(txt);b.appendChild(btn);document.body.appendChild(b);}
// ИНВАРИАНТ: вкладка, получившая отказ, больше НЕ пишет на сервер до перезагрузки.
// Гард стоит в САМОЙ двери отправки, а не только в srvStateSave: отложенный вызов,
// взведённый до отказа, приходит сюда уже в обход той проверки — и уходил на сервер,
// затирая чужую правку. Ревизия берётся ТОЛЬКО из успешной записи: приняв её из
// отказа, вкладка присваивала себе чужую ревизию, считала себя свежей и писала снова.
function srvStatePost(s){ // собственно отправка зеркала; звать из srvStateSave и beforeunload
  if(SRVST_STALE)return;   // вкладка устарела — серверное состояние не трогаем вовсе
  // Запрос уже в полёте: вторым уходить нельзя — он понесёт тот же base_rev и получит
  // отказ. Запоминаем только последнее состояние: промежуточные правки всё равно
  // старше того, что уже ушло.
  if(SRVST_SENDING){SRVST_PENDING=s;return;}
  SRVST_SENDING=true;
  fetch('/api/ui_state',{method:'POST',headers:{'Content-Type':'application/json'},
    body:'{"state":'+s+',"base_rev":'+SRVST_REV+'}'}).then(r=>{if(!r.ok)throw new Error('HTTP '+r.status);
    return r.json();}).then(d=>{
      SRVST_SENDING=false;   // ответ пришёл — дверь свободна, ожидающее решаем здесь же
      if(d.stale||d.err==='stale_state'){SRVST_PENDING=null;srvStaleBanner();return;}
      if(d.error)throw new Error(errText(d));
      if(typeof d.rev==='number')SRVST_REV=d.rev;   // только успешная запись даёт новую ревизию
      SRVST_LAST=s;SRVST_OK_T=Date.now();SRVST_ERR=false;
      const q=SRVST_PENDING;SRVST_PENDING=null;   // ожидающее уходит с УЖЕ свежей ревизией
      if(q!=null&&q!==s)srvStatePost(q);})
    .catch(e=>{SRVST_SENDING=false;SRVST_PENDING=null;
      if(!SRVST_ERR){SRVST_ERR=true;uiLog(t('⚠ ui_state: ')+e);}});}
function applyState(s){try{
  ['base','ai_outdir','aeoutdir'].forEach(k=>{const el=$(k);if(el&&s[k]!=null)el.value=s[k];});
  // Глобальная папка .jsx (клипы без тега спикера) — из сохранённого состояния;
  // поле показывает её или папку тега открытого клипа (см. renderAeDirField).
  if(s.aeoutdir!=null)AEGLOBAL=s.aeoutdir;
  if(s.aerender!=null)AERENDER=s.aerender;
  // Движок рендера — до первого сохранения состояния: rendEngineUI внутри ставит
  // выбранную радиокнопку и зовёт saveState (без него выбор так и остался бы в файле).
  if(s.rendengine!=null)rendEngineSet(s.rendengine);
  // s.aimodel (старый ключ) больше не читаем — выбор модели переехал в серверный ai_config.json
  if(s.cams){const rb=document.querySelector('input[name=cams][value="'+s.cams+'"]');if(rb)rb.checked=true;}
  if(Array.isArray(s.CAMDIRS))CAMDIRS=s.CAMDIRS;if(Array.isArray(s.CAMFILES))CAMFILES=s.CAMFILES;
  if(Array.isArray(s.CAMFROM))CAMFROM=s.CAMFROM;
  // s.CAMCUSTOM — ключ состояния, сохранённого ДО 2026-08-20 (булев «выбрана руками»):
  // читаем его, чтобы выбранная папка камеры пережила обновление и её не затёр автоподбор.
  else if(Array.isArray(s.CAMCUSTOM))CAMFROM=s.CAMCUSTOM.map(x=>x?'user':'');
  if(Array.isArray(s.QUEUE))QUEUE=s.QUEUE;
  if(s.newonly!=null)$('newonly').checked=!!s.newonly;   // «только новые» — рабочая настройка, а не разовая галка
  if(s.cut_stages&&typeof s.cut_stages==='object')CUT_STAGES=Object.assign({},s.cut_stages);
  if(s.dedupe!=null&&(CUT_STAGES.dedupe===undefined||!s.cut_stages))CUT_STAGES.dedupe=!!s.dedupe;   // «чистка дублей» — переживает F5, как соседние галки
  if(s.cut_thresholds&&typeof s.cut_thresholds==='object')CUT_THRESHOLDS=Object.assign({},s.cut_thresholds);
  if(Array.isArray(s.CLIPS)){CLIPS=s.CLIPS;
    // Старое состояние могло сохранить залипшую «…» (genBusy в JSON, поймано
    // 2026-08-10) — после восстановления кнопки генерации обязаны быть живыми.
    // И путь к XML чиним при загрузке: сохранённая чужая склейка («/home/out\1.xml»)
    // на Linux — имя файла с '\', которого нет (см. clipPathFix в 40-queue.js),
    // а список живёт в localStorage и такой путь переживает перезагрузку.
    (CLIPS||[]).forEach(c=>{c.xml=clipPathFix(c.xml);
      (c.inserts||[]).forEach(x=>{delete x.genBusy;});});}
  if(typeof s.curAE==='number')curAE=s.curAE;
  if(curAE>=CLIPS.length)curAE=-1;      // состояние могло сохраниться с индексом длиннее списка
  if(s.CURSTYLE)CURSTYLE=stMigrateIntroCam2(stMigrateCam2Zoom(stMigrateIntroPos2(s.CURSTYLE)));if(s.styleSel)STYLESAVED=s.styleSel;
  // ПЕРЕЕЗД МУЗЫКИ (2026, «музыка в стиле»): режим/ссылка/папка были общими полями
  // шага 3 и жили в состоянии как musicmode/aemusic/aemusicdir. Теперь это ключи СТИЛЯ
  // (music_mode/music_src/music_dir) — у каждого стиля своя музыка. Перекладываем
  // старые значения в живой стиль один раз, иначе у владельца с «файл + своя папка»
  // клипы молча перешли бы на случайный трек из папки по умолчанию. Гейт — отсутствие
  // ключа у стиля: стиль в состоянии сырой (его кладёт туда CURSTYLE, а не резолв),
  // у нового ключи уже есть, и второй раз подстановка не сработает.
  // Папку кладём ещё и в память (AEMUSICDIR): она — запасная ступень лестницы
  // musicPickDir перед <папкой проекта>\music, и стиль её не заменяет — у клипа
  // может быть свой (или вовсе пустой) стиль, а папка с треками у человека одна.
  if(s.aemusicdir!=null)AEMUSICDIR=String(s.aemusicdir);
  if(CURSTYLE){if(s.musicmode&&CURSTYLE.music_mode==null)CURSTYLE.music_mode=s.musicmode;
    if(s.aemusic&&CURSTYLE.music_src==null)CURSTYLE.music_src=s.aemusic;
    if(s.aemusicdir&&CURSTYLE.music_dir==null)CURSTYLE.music_dir=s.aemusicdir;}
  if(s.subengine!=null){SUBWANT=asrMigrate(s.subengine);const se=$('subengine');if(se&&se.options.length)se.value=SUBWANT;}
  // s.selfcheck_model и устаревшие ключи в старом состоянии просто игнорируем
  // Список спикеров грузится асинхронно — запоминаем выбор, ставит его loadSpeakers
  if(s.speaker!=null){SPKSAVED=s.speaker;const sp=$('speaker');if(sp&&sp.options.length>1)sp.value=s.speaker;spkEditUI();}
  // Вкладка «Видео»: запрос и референсы ставим сразу, остальное — когда fillVideoControls
  // наполнит списки под модель (они приходят с /api/ai_config, асинхронно)
  if(s.VID){VIDSAVED=s.VID;
    if(Array.isArray(s.VID.refs))VREFS=s.VID.refs;
    const tp=$('vid_prompt');if(tp&&s.VID.prompt!=null)tp.value=s.VID.prompt;}
  buildCamRows();renderQueue();renderAeDirField();renderCutStagesUI();cutSummary();
}catch(e){
  uiLog(t('⚠ состояние интерфейса не восстановлено: ')+e);
  console.error(e);
}}
// Состояние берём С СЕРВЕРА, а не из localStorage. Раньше было наоборот, и устаревшая
// копия браузера (вторая вкладка, старый профиль Chrome, другой браузер) перекрывала
// серверную, а через 2,5 с перезаписывала её — работа над клипами откатывалась.
// localStorage остаётся: запасным путём, когда сервер не ответил, и быстрым местом
// для тех же данных перед POST.
function storedState(s){try{localStorage.setItem(LSKEY,s);}catch(e){}}
function restoreState(){let s=null;   // локальная копия — только запасной путь
  try{s=JSON.parse(localStorage.getItem(LSKEY)||'null');}catch(e){}
  fetch('/api/ui_state').then(r=>r.json()).then(d=>{
    if(!d)throw new Error('пустой ответ');
    if(typeof d.rev==='number')SRVST_REV=d.rev;
    if(d.state){
      storedState(JSON.stringify(d.state));   // серверное — и в localStorage
      // SRVST_LAST — ПОСЛЕ applyState: он может дёрнуть saveState (rendEngineSet),
      // и та не должна ни слать зеркало заново, ни спотыкаться о «изменилось».
      applyState(d.state);SRVST_LAST=JSON.stringify(d.state);
      goStep(CLIPS.length?(d.state.STEP||1):1);
      if(CLIPS.length)refreshStatuses();
      toast(t('Состояние восстановлено с сервера'));
      return;
    }
    if(s){   // сервер пуст — из браузера; путь к серверу не закрыт, ревизия уже известна
      applyState(s);
      toast(t('Состояние взято из браузера — на сервере его нет'));
    }
  }).catch(e=>{   // сервер не ответил — работаем с тем, что есть в браузере
    if(s){applyState(s);
      toast(t('Сервер недоступен — состояние из браузера'));}
    uiLog(t('⚠ ui_state: ')+e);});}

// ================= boot =================
document.addEventListener('DOMContentLoaded',()=>{setTimeout(edBind,300);});
window.addEventListener('resize',()=>{if($('mbPreview').classList.contains('on')){edResize();edDraw();}});
$('base').value=__BASE__;
document.querySelectorAll('[data-ic]').forEach(el=>{el.outerHTML=ico(el.dataset.ic,el.dataset.cls||'');});  // статичные иконки
// доступность: «!»-тултипы открываются с клавиатуры, кликабельные div-ы работают по Enter/Space
tipArm();
document.addEventListener('keydown',e=>{
  if((e.key==='Enter'||e.key===' ')&&e.target&&e.target.matches
     &&e.target.matches('.stepitem,.lnk,.aisItem,[role=button]')){e.preventDefault();e.target.click();}});
// musicUI больше нет: поля музыки переехали из общих полей шага 3 в блок музыки клипа
// (musicClipUI в 95-styles.js), и он рисуется при открытии клипа, а не на загрузке.
segUI();syncVolUI();
restoreState();
LASTCAMS=nCams();      // база для отката радио, если юзер откажется чистить очередь
loadCutStages();       // загрузка ступеней нарезки с сервера
loadASREngines();      // после restoreState: он кладёт выбранный движок в ASRWANT
loadCams();
loadAIProfiles();
// спикеры — после стилей: выбор спикера подставляет стиль, а его надо знать
loadStyles().then(loadSpeakers);loadFonts();
// Схема стиля — на загрузке, а не только при открытии панели: из неё превью берёт
// таблицу «поле -> вид» и поле-представитель вида, которыми пересчитывает живой стиль
// под кадр формата (stScaleStyle/stScaleKind, 95-styles.js). Без неё драг в превью
// двигал бы стиль в базовых единицах, а кадр ролика у форматов разной высоты.
if(typeof loadStyleSchema==='function')loadStyleSchema();
edBind();
let bootStep=1,bootRaw='';try{bootRaw=localStorage.getItem('reelsi_step')||'';bootStep=parseInt(bootRaw)||1;}catch(e){}
if(bootRaw==='video')openVideo(); else goStep(CLIPS.length?bootStep:1);
// видео-джоб живёт своим потоком — подхватываем его отдельно от нарезки/сборки (F5 в процессе)
(async()=>{try{const d=await (await fetch('/api/video_status')).json();
  const ctx=videoContextForStatus(d);
  if(d.running){if(!ctx&&bootRaw!=='video')openVideo();
    logReset();progOpen({title:t('Генерация видео')});
    progUpdate(null,t('возобновляю после перезагрузки…'));
    vidBusy(true);            // F5 во время генерации — «Остановить» должна вернуться вместе с прогрессом
    VIDCANCEL=false;VIDCTX=ctx;VIDPOLL=true;VIDRETRY=0;pollVideo();}
  else if(d.done){if(ctx){VIDCTX=null;vidBusy(false);videoFinish(d,ctx);}
    else{insVideoForgetPending();if(d.result&&bootRaw==='video')vidShowResult(d.result);}}
  else{if(d.interrupted)jobInterrupted(d.interrupted);   // генерация оборвана перезапуском
    insVideoForgetPending();}
}catch(e){}})();
if(CLIPS.length)refreshStatuses();   // подтянуть ncams/статусы для восстановленных клипов (влияет на кнопку раскладки камер шага 1)
illHdrPoll();                        // если описание базы уже идёт (запущено до F5) — показать прогресс в шапке
// если на сервере уже крутится задача (F5 посреди сборки/нарезки) — подхватываем её индикацию
(async()=>{try{const d=await (await fetch('/api/status')).json();
  queueRender(d);   // очередь этапов: после F5 виден и итог уже закончившейся
  if(d.running){
    // kind/label приходят структурно из JOB (сниффинг лога — только фолбэк для старого сервера)
    const kind=d.kind||((d.log||[]).some(l=>fmtLog(l).indexOf('=== Сборка')===0)?'build':'cut');
    const label=d.label||(/Omni|27b/.test((d.log||[]).map(fmtLog).join('\n'))?t('ИИ-нарезка'):t('Нарезка'));
    progOpen({title:label});
    progUpdate(null,t('возобновляю после перезагрузки…'));
    if(kind==='build')pollBuild();
    else if(kind==='draft')pollDraft();
    else{CUTLABEL=label;cutBusy(true);pollAI();}
  }else if(d.interrupted)jobInterrupted(d.interrupted);   // задание оборвано перезапуском сервера
}catch(e){}})();
// рендер живёт в своём RJOB — после F5 подхватываем его отдельно
(async()=>{try{const d=await (await fetch('/api/render_status')).json();
  queueRender(d);   // очередь этапов: рендер — своя дверь, до проверки running
  // дефолт папки вывода рендера — один источник на сервере. Кладём в AERENDER,
  // а не только в поле: загрузка спикеров (applySpeakerDirs -> renderRenderDirField)
  // идёт позже и перезаписала бы значение, оставленное только в DOM.
  if(d.default_dir&&!AERENDER){AERENDER=d.default_dir;renderRenderDirField();}
  // Движок идущего рендера — от СЕРВЕРА (d.engine), а не от переключателя на странице:
  // встроенный рендер подписывался бы «Рендер AE» до первого ответа pollRender.
  if(d.running){RENDERENGINE=(d.engine==='builtin')?'builtin':'ae';logReset();
    progOpen({title:rendEngineLabel()});
    progUpdate(null,t('возобновляю после перезагрузки…'));uiBusySet(true);pollRender();}
  else if(d.interrupted)jobInterrupted(d.interrupted);   // рендер оборван перезапуском сервера
}catch(e){}})();
// bfcache возвращает страницу целиком — вместе с застрявшим в «…» genBusy старого
// сеанса: fetch, ушедший в зависший сервер, живёт ровно столько, сколько жила вкладка,
// а гвардия двойного клика глотает все нажатия. Ручная перезагрузка чистит сама,
// здесь подстраховываем переходы назад/вперёд — кнопки генерации не помнят чужой сеанс.
window.addEventListener('pageshow',e=>{if(!e.persisted)return;
  (CLIPS||[]).forEach(c=>(c.inserts||[]).forEach(x=>{x.genBusy=false;}));});
// сохранение состояния: захватываем DOM панели AE в джоб перед записью, чтобы правки не терялись
function flushSave(){if(STEP===3&&curAE>=0)captureAE();else saveState();}
setInterval(flushSave,2500);
window.addEventListener('beforeunload',()=>{flushSave();
  // sendBeacon гарантированно уходит при закрытии вкладки; обычный fetch не успевает.
  // Устаревшая вкладка не шлёт ничего: страховка «дописать при закрытии» затирала бы
  // серверное состояние ровно так же, как обычное сохранение.
  if(SRVST_STALE)return;
  const s=JSON.stringify(stateObj());
  // Запрос в полёте: SRVST_LAST — состояние ДО него, и по этой отметке маяк пропускался бы
  // ровно тогда, когда он нужнее всего (неподтверждённая правка уходит вместе с вкладкой).
  // Шлём текущее состояние с ТЕКУЩЕЙ ревизией: от непришедшего ответа она ещё не
  // обновилась; разойдётся — сервер честно откажет, а это не хуже прежнего поведения.
  if(!SRVST_SENDING&&s===SRVST_LAST)return;
  const body='{"state":'+s+',"base_rev":'+SRVST_REV+'}';
  if(navigator.sendBeacon){navigator.sendBeacon('/api/ui_state',
    new Blob([body],{type:'application/json'}));}
  else{fetch('/api/ui_state',{method:'POST',headers:{'Content-Type':'application/json'},
    body:body}).catch(()=>{});}});
document.addEventListener('visibilitychange',()=>{if(document.hidden)flushSave();});
