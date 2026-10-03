// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Maxim Si
// ОДНА форма прогресса на весь интерфейс: шапка, полоса, строки роликов, кнопки.
//
// Часть интерфейса Reelsi. Файлы static/app/ грузятся ПО ПОРЯДКУ ИМЁН обычными
// <script>-тегами (не модулями): один общий скоуп, как было в едином app.js.
// Порядок важен — объявления функций поднимаются в пределах своего файла.
//
// Сюда приходят ВСЕ, кто показывает долгую работу: нарезка, сборка .jsx, рендер
// (AE и встроенный), разметка (субтитры/жёлтые/вставки), ИИ-интро, генерация видео,
// сборка прокси превью. Разметка окна — ТОЛЬКО здесь: классы qrow/qname/qdetail/
// qstage и поля шапки (progStage/progSub/progFill/progPct, чип progmini) больше ни
// в одном файле static/app/*.js не встречаются — за этим следит
// tests/test_progress_form.py (сообщение теста говорит, куда идти).
//
// Форма одна и выглядит так:
//
//   ЗАГОЛОВОК ЗАДАЧИ                       k из N · ~ETA
//   [полоса общего прогресса]
//   имя_ролика_1.xml                       человеческий статус
//   имя_ролика_2.xml                       готово
//   [Свернуть] [Показать логи] [Остановить]
//
// Правила формы (DESIGN.md + просьба владельца):
//   - в шапке НЕТ имён файлов и строк лога: только этап, «сколько готово из скольких»
//     и оценка «сколько ещё»;
//   - строка ролика — имя (обрезано многоточием, полное лежит в title) и КОРОТКИЙ
//     человеческий статус из словаря PROGEV;
//   - вторая строка строки ролика (.qdetail) — только человеческий текст: итог
//     разметки, короткая причина ошибки. Сырой хвост лога сюда не попадает никогда;
//   - сырой лог живёт ровно в одном месте — в окне «Показать логи»;
//   - зелёный #98ff38 — только у «готово» (это статус, а не украшение).
//
// Как добавить новый этап: одна строка в PROGEV (код события → человеческий статус)
// и, если сервер про этот этап пишет только строку лога, одно правило в PROGLOG.
// Больше нигде править не нужно: и шапка, и строки роликов собираются из этих таблиц.

// ================= словарь «событие → статус» =================
// Ключ — КОД события. Его отдаёт сервер (items[].stage у нарезки/сборки/рендера,
// stage_label у рендера) или достаёт разбор лога (PROGLOG). Значение — короткая
// человеческая подпись. Чего в словаре нет — в строку не выводится вовсе:
// неизвестное событие остаётся только в логе, на экран не просится.
const PROGEV={
  wait:t('в очереди'),
  probe:t('извлекаю звук'),
  cut:t('режу'),
  align:t('свожу дорожки'),
  subs:t('субтитры'),
  yellow:t('жёлтые (ИИ)'),
  inserts:t('вставки (ИИ)'),
  files:t('файлы вставок'),
  intro:t('интро (ИИ)'),
  jsx:t('собираю скрипт'),
  check:t('проверяю файлы'),
  aep:t('собираю проект в AE'),
  built:t('собран, ждёт рендера'),
  render:t('рендер'),
  proxy:t('собираю прокси'),
  // Этапы двери расчёта превью (api/previewcalc.py, кнопка «Рассчитать рото и трекинг»):
  // код этапа приходит в status, подпись живёт здесь — второй копии формулировок нет.
  plan:t('готовлю план'),
  head:t('считаю трекинг'),
  roto:t('считаю рото'),
  voice:t('обрабатываю голос'),
  video:t('генерирую видео'),
  setup:t('готовлю запрос'),
  think:t('думает'),
  answer:t('пишет ответ'),
  waiting:t('жду ответ модели'),
  retry:t('повторяю'),
  done:t('готово'),
  error:t('ошибка'),
  stopped:t('остановлено')};
// Какие события означают «ролик в работе прямо сейчас»: строка подсвечивается, и к
// ней прокручивается список. Всё остальное — либо ещё не начато, либо уже кончилось.
const PROG_BUSY={cut:1,probe:1,align:1,subs:1,yellow:1,inserts:1,files:1,intro:1,
  jsx:1,check:1,aep:1,render:1,proxy:1,voice:1,video:1,setup:1,think:1,answer:1,waiting:1,retry:1};

// ================= «строка лога → код события»: ОДНО место =================
// Часть роутов (ИИ-разметка, генерация видео) отдаёт прогресс только строкой лога:
// структурного кода события у неё нет. Разбираем такие строки ЗДЕСЬ и только здесь —
// в окне прогресса у них появляется короткий словарный статус, а сама строка ждёт
// в «Показать логи». Новое правило — одна строка; каждое закрыто тестом
// tests/test_progress_form.py.
//
// Порядок важен: выигрывает первое подошедшее правило. Строки, которой не нашлось
// правила, в окне прогресса НЕ показываем: неизвестный хвост — это лог.
const PROGLOG=[
  [/\[max_tokens\]|\[reasoning\]|\[cache\]/, 'setup'],
  [/·\s*думает:|^думает:/, 'think'],
  [/·\s*пишет ответ|^пишет ответ/, 'answer'],
  [/·\s*ждём первый чанк|^ждём первый чанк/, 'waiting'],
  [/повтор/, 'retry'],
  [/^\s*видео( готово)?[: ]/, 'video']];
// Признаки СЫРОЙ строки лога, которая не разобралась ни одним правилом: серверные
// строки начинаются со служебного маркера или несут «→»/ведущий отступ. Нужны, чтобы
// незнакомый хвост не просочился в шапку и в строку ролика (см. progHuman).
const PROGRAW=/^(?:\[|!|⚠|⏱|▸|✔|✂|·|\s\s)|→/;
// Код события по строке лога: '' — не разобрали (значит, и показывать нечего).
function progEventFromLog(line){
  if(line==null)return '';
  const s=String(line).replace(/\s+/g,' ').trim();
  if(!s)return '';
  for(const rule of PROGLOG)if(rule[0].test(s))return rule[1];
  return '';}
// Код события из записи лога: сервер может положить его СТРУКТУРНО (v.ev) — тогда
// разбор строки не нужен вовсе, и это самый надёжный путь.
function progEventOf(l){
  if(l&&typeof l==='object'&&l.v&&l.v.ev)return String(l.v.ev);
  return progEventFromLog(fmtLog(l));}
// Человеческий текст или сырой хвост лога? В шапку и в строки роликов сырое не
// попадает: у разобранного события есть словарный статус, а сама строка остаётся
// в «Показать логи». Фильтр ОДИН на всё окно — иначе достаточно одной двери,
// передавшей хвост, чтобы простыня вернулась на экран.
function progHuman(s){
  if(s==null)return '';
  const x=String(s).replace(/\s+/g,' ').trim();
  if(!x)return '';
  if(progEventFromLog(x))return '';       // разобралось как событие — это лог
  if(PROGRAW.test(x))return '';           // служебный маркер серверной строки
  return x.slice(0,120);}
// Короткая причина для второй строки: одна строка без переводов и без хвоста в полэкрана.
function progShort(s){
  const x=String(s==null?'':s).replace(/\s+/g,' ').trim();
  return x.length>90?x.slice(0,89)+'…':x;}

// ================= состояние окна =================
let PROGMIN=false;   // прогресс свёрнут в чип (оверлей скрыт, задача продолжается)
// Контекст очереди: заголовок операции и «сколько готово из скольких». Держится, пока
// идёт очередь, и НЕ затирается сообщениями об этапах — иначе на экране остаётся один
// этап без ответа на вопрос «сколько ещё осталось».
let PROGQ=null;      // {title, k, n}
// Строки окна: у серверного задания приходят из d.items (нарезка/сборка/рендер),
// у клиентского прогона (разметка, ИИ-интро) заводятся localQStart. Список ОДИН на
// оба случая — второй копии разметки и второго состояния у формы нет.
let PROGITEMS=[];
// Клиентский прогон: LOCALQ жив, пока идут POST-ы из браузера (разметка, ИИ-интро).
// Пока он жив, queueRender рисует его строки и серверные d.items игнорирует; localQEnd
// список НЕ стирает, а метит done — итог прогона остаётся на экране до нового задания.
let LOCALQ=null;     // {cur, done}
// «Остановить»: серверная задача гасится через /api/cancel, клиентские циклы смотрят флаг.
let UICANCEL=false;
// Состояние шапки: заголовок, момент старта (для оценки «сколько ещё»), серверная
// оценка времени (рендер считает её сам — она точнее нашей), признак «кончилось».
let PROG={title:'',t0:0,eta:null,done:false};

function progMini(){PROGMIN=true;$('prog').classList.remove('on');$('progmini').classList.add('on');}
function progMaxi(){PROGMIN=false;$('progmini').classList.remove('on');$('prog').classList.add('on');}
// Свернуть оверлей и уйти к готовым клипам: он перекрывает страницу, а нарезанное уже
// в списке шага 1 — правку можно начать, не дожидаясь очереди.
function progToReady(){progMini();goStep(1);
  const h=$('clips1');if(h)h.scrollIntoView({behavior:'smooth',block:'center'});}
function progReadySet(n){const b=$('progReady');if(!b)return;
  b.style.display=n?'':'none';b.textContent=t('Править готовые: ')+n;}
// Строка текущего этапа клиентского прогона (её ведёт хвост лога) — по ссылке, а не
// поиском по имени: имя клипа в списке и стем XML могут не совпадать.
function progCurItem(){return (LOCALQ&&LOCALQ.cur>=0)?PROGITEMS[LOCALQ.cur]:null;}

// ================= запись строк =================
// Завести/обновить строку ролика: имя + КОД события + понятные человеку подробности
// (extra.pct, extra.detail, extra.reason). Единственная дверь записи статуса строки.
function progItem(name,event,extra){
  const nm=String(name==null?'':name);
  let it=PROGITEMS.find(x=>x.name===nm);
  if(!it){it={name:nm,stage:'wait',detail:'',pct:null,path:'',reason:''};PROGITEMS.push(it);}
  // События нет в словаре — статус строки НЕ трогаем: показывать вместо него «в очереди»
  // значило бы врать, а показывать код события — тащить в окно служебное (оно для лога).
  if(PROGEV[event])it.stage=event;
  if(extra)for(const k in extra)if(extra[k]!==undefined)it[k]=extra[k];
  queueRender(null);
  return it;}
function localQStart(names){
  LOCALQ={cur:-1,done:false};
  PROGITEMS=(names||[]).map(n=>({name:String(n),stage:'wait',detail:'',pct:null,path:'',reason:''}));
  queueRender(null);}
function localQSet(name,stage,detail){
  if(!LOCALQ)return;
  const i=PROGITEMS.findIndex(it=>it.name===name);
  if(i<0)return;
  const it=PROGITEMS[i];
  it.detail=(detail==null?'':String(detail));
  LOCALQ.cur=i;
  progItem(name,stage,{detail:it.detail});}
function localQEnd(){if(LOCALQ)LOCALQ.done=true;}

// ================= шапка =================
// «k из N» — сколько роликов ГОТОВО из скольких в наборе. Прежнее «клип 1 из 4» при
// четырёх параллельных роликах отвечало не на тот вопрос: в работе были все четыре.
function progCountText(){
  const n=(PROGQ&&PROGQ.n)||PROGITEMS.length;
  if(!n)return '';
  const k=PROGITEMS.length?PROGITEMS.filter(it=>it.stage==='done').length:((PROGQ&&PROGQ.k)||0);
  return t('{k} из {n}',{k:k,n:n});}
// Оценка «сколько ещё»: серверная (рендер) точнее — берём её; иначе считаем по своей
// скорости. Молчим, пока готовых нет и пока не прошло 10 с: иначе цифра скачет.
function progEtaText(){
  const eta=PROG.eta;
  if(eta!=null&&eta>0)return '~'+fmtEta(eta);
  const n=(PROGQ&&PROGQ.n)||PROGITEMS.length;
  const k=PROGITEMS.filter(it=>it.stage==='done').length;
  if(!k||!n||k>=n)return '';
  const el=(Date.now()-(PROG.t0||Date.now()))/1000;
  if(el<10)return '';
  return '~'+fmtEta(el/k*(n-k));}
function progEtaSet(sec){PROG.eta=(sec!=null&&sec>0)?sec:null;}
// Строка очереди-шапки: «k из N · этап · ~оценка». Ни имён файлов, ни строк лога —
// всё сырое отсекает progHuman (см. там же).
function progHeadText(stage,sub){
  const parts=[progCountText(),progHuman(stage||sub||''),progEtaText()].filter(Boolean);
  return parts.join(' · ');}
function progHeadRender(){
  const line=progHeadText(PROG.stage,'');
  if(!PROGMIN&&$('progSub'))$('progSub').textContent=line;
  const pm=$('pmText');if(pm)pm.textContent=line||PROG.title;}
// Выставить/обновить контекст очереди: заголовок операции и сколько готово из скольких.
// Зовётся в начале длинной операции и на каждом её ролике — этапы меняет progStep.
function progQueue(title,k,n){
  PROGQ={title:title||'',k:k||0,n:n||0};
  if(PROGQ.title){PROG.title=PROGQ.title;if($('progStage'))$('progStage').textContent=PROGQ.title;}
  progHeadRender();}
// Сменить ТОЛЬКО этап (и, если задан, процент): строка очереди остаётся на экране —
// без неё этап не отвечает на вопрос «сколько ещё осталось».
function progStep(stage,frac){progUpdate(frac,stage);}
function progUpdate(frac,stage,title,sub){
  if(title){PROG.title=title;$('progStage').textContent=title;}
  PROG.stage=stage||sub||'';
  const line=progHeadText(PROG.stage,'');
  $('progSub').textContent=line;
  const f=$('progFill'),pf=$('pmFill');       // pf — зеркало в свёрнутый чип
  // frac===undefined — процент не передан (так зовёт progStep): шкалы не трогаем, иначе
  // смена этапа сбрасывала бы уже показанный процент в «бегающую» полоску.
  if(frac!==undefined){
    if(frac==null){f.className='progfill indet';$('progPct').textContent='';
      pf.className='fill indet';pf.style.width='';$('pmPct').textContent='';}
    else{const w=Math.max(3,Math.min(100,frac*100))+'%',p=Math.round(frac*100)+'%';
      f.className='progfill';f.style.width=w;$('progPct').textContent=p;
      pf.className='fill';pf.style.width=w;$('pmPct').textContent=p;}
  }
  $('pmText').textContent=line||PROG.title;}

// ================= открытие и закрытие окна =================
// progOpen({title, items}) — начать новое задание: заголовок, сброс полосы и кнопок,
// необязательный список строк сразу. Всё остальное окно (строки) ведёт progItem.
function progOpen(opts){
  opts=opts||{};
  // Новое задание — своя очередь: список клиентского прогона (разметка, ИИ-интро)
  // снимается здесь (а если задание серверное — ещё и в опросе статуса, по d.running),
  // и queueRender снова рисует серверные items.
  LOCALQ=null;PROGQ=null;
  PROG={title:opts.title||t('Работаю…'),t0:Date.now(),eta:null,done:false};
  PROGITEMS=(opts.items||[]).map(it=>Object.assign({name:'',stage:'wait',detail:'',pct:null,path:'',reason:''},it));
  $('progStage').textContent=PROG.title;$('progSub').textContent='';
  const f=$('progFill');f.className='progfill indet';f.style.width='';$('progPct').textContent='';
  $('progClose').style.display='none';
  UICANCEL=false;const ps=$('progStop');ps.style.display='';ps.disabled=false;
  PROGMIN=false;$('progmini').classList.remove('on');
  progReadySet(0);
  const pf=$('pmFill');pf.className='fill indet';pf.style.width='';$('pmPct').textContent='';
  $('pmText').textContent=PROG.title;
  queueRender(null);            // строки переданного списка (если он был)
  $('prog').classList.add('on');}
// Прежняя дверь «показать окно»: заголовок + первая подпись. Оставлена тонкой
// прослойкой — второго окна и второй разметки за ней нет.
function progShow(stage,sub){progOpen({title:stage||t('Работаю…')});if(sub)progUpdate(null,sub,stage);}
function progDone(msg,bad){
  PROGQ=null;PROG.done=true;
  const f=$('progFill');
  // «остановлено» и «с ошибками» — не «поработал»: зелёный остаётся только у честного «готово».
  f.className='progfill'+(bad?'':' done');f.style.width='100%';
  // Не зелёный исход — заголовок операции оставляем как есть (по нему видно, ЧТО не
  // доехало), а в подписи стоит причина. «Готово» пишем только у честного конца.
  $('progStage').textContent=bad?PROG.title:t('Готово');
  $('progSub').textContent=progHuman(msg||'');$('progPct').textContent='100%';
  $('progClose').style.display='';
  $('progStop').style.display='none';progReadySet(0);   // очередь кончилась — список и так на экране
  const pf=$('pmFill');pf.className='fill'+(bad?'':' done');pf.style.width='100%';$('pmPct').textContent='';
  $('pmText').textContent=t('Готово — открыть');}
function hideProg(){PROGQ=null;PROGMIN=false;$('prog').classList.remove('on');$('progmini').classList.remove('on');}
// «Остановить»: серверная задача (нарезка/сборка) гасится через /api/cancel (subprocess убивается
// сразу, внутрипроцессный шаг — после текущего клипа); клиентские циклы (разметка) смотрят UICANCEL.
async function cancelTask(){UICANCEL=true;const b=$('progStop');b.disabled=true;
  progUpdate(null,t('останавливаю…'));uiLog(t('⏹ остановка по кнопке'));
  // Генерация видео живёт в своём джобе (VJOB) — /api/cancel её не касается. А во время
  // генерации на экране висит именно этот оверлей, кнопка «Остановить» на странице под
  // ним: жали сюда, оно писало «останавливаю…» и спокойно досчитывало (за деньги).
  // Видео НЕ ставит UIBUSY: генерацию свернули, ушли на шаг 1 и запустили нарезку —
  // оверлей теперь у нарезки, а «Остановить» гасил видео (в фоне) и оставлял нарезку
  // без остановки. Здесь и сейчас оверлей принадлежит JOB-задаче (UIBUSY), а видео
  // останавливается своей кнопкой на вкладке «Видео» (аудит 2026-08-10, B1).
  if(VIDPOLL&&!UIBUSY){await vidCancel();b.disabled=false;return;}
  let fail=0;
  try{await fetch('/api/cancel',{method:'POST'});}catch(e){fail++;}
  // разметка идёт обычными POST-ами без JOB — текущую ИИ-генерацию рвёт только ai_stop
  try{await fetch('/api/ai_stop',{method:'POST'});}catch(e){fail++;}
  // Оба запроса упали — сервер не ответил, и без выхода из оверлея остаётся только F5
  // (кнопка «Остановить» disabled, «Закрыть» скрыта). Возвращаем кнопку и показываем
  // «Закрыть»: у юзера обязан быть выход из оверлея без перезагрузки (задание по UI-состояниям).
  if(fail===2){b.disabled=false;progUpdate(null,t('сервер не ответил — остановка не отправлена'));
    $('progClose').style.display='';toast(t('Сервер не ответил: ')+t('остановка не отправлена'));}}

// ================= строки роликов =================
// Вторая строка под именем — ТОЛЬКО понятное человеку: короткая причина ошибки или
// итог того, что уже посчитано. Сырой хвост лога сюда не проходит (progHuman).
function progNoteText(it){
  if(it.stage==='error'){const why=progShort(it.reason||it.detail);
    return why?t('ошибка: {why}',{why:why}):t('ошибка');}
  return progHuman(it.detail||'');}
// Короткий статус строки: словарная подпись события, у рендера — с процентом.
function progStatusText(it){
  const ev=PROGEV[it.stage]?it.stage:'wait';
  const label=PROGEV[ev];
  if(it.pct!=null&&it.pct>0&&it.pct<1&&(ev==='render'||ev==='video'||ev==='proxy'||ev==='voice'))
    return t('{label} {p}%',{label:label,p:Math.round(it.pct*100)});
  return label;}
// Строка ролика: имя (обрезано многоточием по CSS, полное — в title) + человеческий
// статус. Второй строкой — то, что есть сказать человеку.
function progRowHTML(it,i){
  const ev=PROGEV[it.stage]?it.stage:'wait';
  const st=PROGEV[ev];
  const cls=(ev==='done')?'qdone':(ev==='error'?'qerr':'');
  const name=String(it.name||'');
  const note=progNoteText(it);
  // У ошибки причина уже несёт слово «ошибка» — второй раз его в подсказку не пишем.
  const title=[name,(ev==='error'?[note||st]:[st,note])].flat().filter(Boolean).join(' — ');
  return '<div class="qrow'+(PROG_BUSY[ev]?' qcur':'')+'" id="qrow_'+i+'"'
    +(title?' title="'+esc(title)+'"':'')+'>'
    +'<span class="qname">'+esc(name)
    +(note?'<span class="qdetail">'+esc(note)+'</span>':'')+'</span>'
    +'<span class="qstage '+cls+'">'+esc(progStatusText(it))+'</span></div>';}
// Единственная дверь серверных строк: опрос статуса зовёт queueRender(d) и рисует
// d.items. Пока жив клиентский прогон (LOCALQ), серверные items не рисуются никогда —
// иначе в окне разметки висел список ПРОШЛОЙ нарезки.
function queueRender(d){
  // Новое СЕРВЕРНОЕ задание вытесняет закончившийся клиентский прогон: опрос статуса
  // присылает running=true — значит на сервере пошла нарезка/сборка/рендер.
  if(d&&d.running&&LOCALQ&&LOCALQ.done)LOCALQ=null;
  if(d&&d.items&&!LOCALQ)PROGITEMS=d.items;
  const items=PROGITEMS;
  const qw=$('qwrap'), ql=$('qlist');
  if(!items.length){
    if(qw)qw.style.display='none';
    if(ql)ql.innerHTML='';
    progHeadRender();
    return;
  }
  if(qw)qw.style.display='';
  const rows=[];let curIndex=-1;
  for(let i=0;i<items.length;i++){
    const it=items[i];
    if(curIndex<0&&PROG_BUSY[it.stage||'wait'])curIndex=i;
    rows.push(progRowHTML(it,i));
  }
  if(ql){
    ql.innerHTML=rows.join('');
    const doneN=items.filter(it=>it.stage==='done').length;
    const targetIdx=curIndex>=0?curIndex:(doneN<items.length?doneN:-1);
    if(targetIdx>=0){
      const el=$('qrow_'+targetIdx);
      if(el&&ql.scrollHeight>ql.clientHeight){
        // inline:'nearest' — не требует прокрутки вбок; scrollLeft=0 добивает случай,
        // когда список уже уехал по горизонтали (из-за этого начало имён и срезалось).
        el.scrollIntoView({block:'nearest',inline:'nearest'});
        ql.scrollLeft=0;
      }
    }
  }
  progHeadRender();}
function fmtEta(sec){
  sec=Math.max(0,Math.round(sec));
  const m=Math.floor(sec/60),s=sec%60;
  if(m>=60)return t('{h} ч {m} мин',{h:Math.floor(m/60),m:m%60});
  if(m>0)return t('{m} мин {s} с',{m:m,s:s});
  return t('{s} с',{s:s});
}
// Строка статуса для мест, у которых своя рамка (блок сборки прокси поверх плеера):
// текст берётся из ТОГО ЖЕ словаря, второй копии формулировок нет.
function progStatusFor(event,extra){
  const ev=PROGEV[event]?event:'wait';
  const ex=extra||{};
  if(ex.n)return t('{label} {i}/{n} · {p}%',
    {label:PROGEV[ev],i:ex.i||0,n:ex.n,p:Math.round(+ex.pct||0)});
  return progStatusText({stage:ev,pct:ex.pct});}
