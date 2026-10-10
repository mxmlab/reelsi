// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Maxim Si
// Кнопка «Рассчитать рото и трекинг» в окне превью AE: прогресс и «посчитано».
//
// Часть интерфейса Reelsi. Файлы static/app/ грузятся ПО ПОРЯДКУ ИМЁН обычными
// <script>-тегами (не модулями): один общий скоуп, как было в едином app.js.
//
// Зачем кнопка. Рото и слежение за головой раньше были видны только в финальном
// рендере: превью показывало полосу «здесь рото» на таймлайне и ничего в кадре.
// Теперь их считает дверь /api/preview_calc — ТЕМ ЖЕ кодом, что сборка
// (core/xml2ae/precompute.py), и кладёт в ТЕ ЖЕ кэши: посчитанное здесь сборка берёт
// готовым, а кэш сам протухает по исходнику — поэтому кнопки «пересчитать» нет.
//
// Прогресс идёт СТРОКОЙ В КАДРЕ (pvProgRow — тот же контейнер, что у прокси и голоса),
// а не отдельным окном: окно закрывало бы ровно то, что настраивают. Кнопка видна только
// на вкладке «Интро» и только когда рото или слежение включены в стиле клипа: иначе она
// предлагала бы работу, которой в сборке не будет.
let IPVCALC_UP=false;     // по кэшам всё посчитано (кнопка снята, пометка «посчитано»)
let IPVCALC_TO=0;         // таймер опроса, чтобы не завести второй
const IPVCALC_POLL=1500;  // мс: расчёт идёт минутами, чаще спрашивать нечего
let IPVCALC_SRV=null;     // ответ быстрой двери `wanted` для клипа {xml, wanted}; null — ещё не спрашивали
// Есть ли что считать в окне превью. Решает то же, что сервер по тому же телу: ответ
// быстрой двери `wanted` по этому клипу главнее (он знает число камер в XML и умолчания
// BASE, а в браузере их нет). Пока ответа нет — окно только открыли или стиль только что
// записан (ipvCalcStyleChanged сбрасывает ответ), — решаем по полям, что уходят в дверь:
// стиль CURSTYLE и roto, как в ipvPlanBody. Своей копии правил чтения стиля здесь нет.
function ipvCalcWanted(){
  if(IPVCALC_SRV&&IPVCALC_SRV.xml===IPV.xml)return IPVCALC_SRV.wanted;
  const s=(typeof CURSTYLE!=='undefined'&&CURSTYLE&&typeof CURSTYLE==='object')?CURSTYLE:{};
  const b=(typeof STSCHEMA!=='undefined'&&STSCHEMA&&STSCHEMA.base)?STSCHEMA.base:{};
  // roto — тот же запасной путь, что у поля `roto` в ipvPlanBody (нет в стиле — из схемы)
  const roto=(s.roto!=null)?!!s.roto:!!b.roto;
  return roto||!!s.cam1_head_follow||!!s.cam2_head_follow;
}
// Строка прогресса в кадре: текст — из общего словаря прогресса (55-progress.js), своей
// формулировки у этой двери нет. Пустой текст снимает строку.
function ipvCalcLine(text,pct,running){
  const stage=$('ipvstage');if(!stage)return;
  if(!text){pvProgDrop(stage,'roto');return;}
  const box=pvProgRow(stage,'roto','pvpxr');
  const w=Math.max(0,Math.min(100,+pct||0));
  box.innerHTML='<div class="pvpx_bar"><span class="pvpx_fill" style="width:'+w+'%"></span></div>'
    +'<div class="pvpx_line"><span class="pvpx_txt">'+esc(text)+'</span>'
    +(running?'<button class="sm pvpx_stop" type="button" onclick="ipvCalcStop()">'+esc(t('Стоп'))+'</button>':'')
    +'</div>';}
// Строка статуса по этапу двери: этап приходит кодом (plan/head/roto) — подписи берём из
// общего словаря статусов (PROGEV, 55-progress.js), второй копии формулировок нет.
function ipvCalcStatusText(st){
  return progStatusFor(st.stage||'roto',{i:st.i||0,n:st.n||0,pct:st.pct||0});}
// «Что уже посчитано» — по кэшам, без GPU: дверь БЕЗ build=true ничего не запускает.
// Одна дверь на открытие превью и на конец расчёта. План при этом НЕ трогаем: полный
// план приезжает из /api/scene, а здесь только готовые маски — слой рото рисуется по ним.
async function ipvCalcApply(){
  if(!aewOn())return null;
  const xml=IPV.xml;if(!xml)return null;
  // Тело — ТО ЖЕ, что у /api/scene (ipvPlanBody): разметка рото зависит от интро и
  // вставок задания, и от «{xml, style}» план вышел бы другим — маски искались бы не по
  // тем кускам, а полоса «здесь рото» разошлась бы с кадром.
  let d;try{d=await (await fetch('/api/preview_calc',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(ipvPlanBody())})).json();}
  catch(e){return null;}
  if(IPV.xml!==xml)return null;                 // модалку успели переоткрыть на другом клипе
  if(d.error)return null;
  // Решение сервера по этому клипу (см. ipvCalcWanted): кнопка берёт его, а не гадает по полям
  if('wanted' in d)IPVCALC_SRV={xml:xml,wanted:!!d.wanted};
  const roto=d.roto||[];
  const head=d.head||{};
  IPVCALC_UP=!!(roto.length||head.ready);
  // Разметку плана (`IPV.plan.roto` — полоса «здесь рото» на таймлайне) НЕ затираем
  // пустым кэшем: до расчёта полоса показывает, ГДЕ рото включён, и это её смысл.
  if(IPV.plan&&roto.length)IPV.plan.roto=roto;
  if(IPV.plan)IPV.plan.roto_ready=!!head.ready;
  IPV.roto=roto;
  IPV_ROTO=xml;
  IPV_ROTOACC=-1;IPV_ROTODRAWN=-1;
  ipvRoto(ipvNow());
  ipvCalcUI(true);                              // кнопка/пометка — по свежему кэшу
  return d;}
// Поставить расчёт: кнопка «Рассчитать рото и трекинг». Всё уже посчитано — гонять GPU
// незачем, обновляем план (слежение приезжает из него). Второй расчёт поверх идущего
// сервер не запускает — там тот же ответ `building=true`.
async function ipvCalcRun(){
  if(IPVCALC_UP){ipvPlanSoon();return;}
  const xml=IPV.xml;if(!xml)return;
  // Тело — как у /api/scene (ipvPlanBody), плюс признак «запускай расчёт»: своя сборка
  // полей здесь означала бы второй план, а с ним и маски не по тем кускам.
  const body=ipvPlanBody();body.build=true;
  let d;try{d=await (await fetch('/api/preview_calc',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)})).json();}
  catch(e){toast(String(e));return;}
  if(d.error){
    // Ошибка двери (в т.ч. «нечего считать»): кнопку снимаем, причину показываем тостом
    IPVCALC_SRV={xml:xml,wanted:false};ipvCalcUI(false);toast(errText(d));return;}
  if(!d.building){await ipvCalcDone();return;}
  ipvCalcUI(false);
  ipvCalcPoll();}
// Опрос прогресса: полоса на кадре, пока идёт. Кончилось — снять строку, запросить «что
// посчитано», перезапросить план (слежение приезжает из плана) и перерисовать кадр.
async function ipvCalcPoll(){
  clearTimeout(IPVCALC_TO);
  let s;try{s=await (await fetch('/api/preview_calc_status')).json();}catch(e){return;}
  if(s.running){
    ipvCalcUI(false);
    ipvCalcLine(ipvCalcStatusText(s),s.pct,true);
    IPVCALC_TO=setTimeout(ipvCalcPoll,IPVCALC_POLL);
    return;}
  await ipvCalcDone();}
async function ipvCalcDone(){
  clearTimeout(IPVCALC_TO);IPVCALC_TO=0;
  const st=$('ipvstage');if(st)pvProgDrop(st,'roto');
  await ipvCalcApply();          // кэш масок -> слой рото
  // План — через ipvRefresh (та же дверь, что у правок вставок): слежение и разметка
  // рото приезжают из /api/scene по свежим кэшам, своей копии «что играть» нет.
  ipvRefresh();
  if(typeof uiLog==='function')uiLog(t('рото и трекинг посчитаны — кэш масок на месте'));
  ipvCalcUI(true);}
function ipvCalcStop(){
  try{fetch('/api/preview_calc_cancel',{method:'POST'});}catch(e){}
  ipvCalcLine(t('останавливаю…'),0,false);
  if(typeof uiLog==='function')uiLog(t('рото и трекинг: остановка по кнопке'));
  // Опрос НЕ прекращаем: «Стоп» только ставит флаг — текущий кусок рото досчитывается,
  // и задание завершится само. Без опроса строка «останавливаю…» осталась бы на кадре.
  clearTimeout(IPVCALC_TO);IPVCALC_TO=setTimeout(ipvCalcPoll,IPVCALC_POLL);}
// Стиль клипа сменился. Два входа покрывают все пути: fillStyleFields (94-stylepanel.js) —
// панель заполнена стилем (выбор, кастом, правка шаблона, смена клипа), captureAE (90-ae.js) —
// стиль записан в задание (галки, панель, зум, сброс поля). Прежний ответ сервера уже не
// про этот стиль: сбрасываем его и пересчитываем кнопку по полям тела.
// Свежий ответ придёт с планом (ipvPlanFetch -> ipvCalcApply), пометку «посчитано» не
// трогаем — иначе она мигала бы на каждом шаге зума.
function ipvCalcStyleChanged(){IPVCALC_SRV=null;ipvCalcUI(IPVCALC_UP);}
// Показ/скрытие кнопки и пометки «посчитано». Pulse Green — ТОЛЬКО статус готовности
// (docs/DESIGN.md): пометка «посчитано» зелёная, кнопка — обычная.
function ipvCalcUI(ready){
  const btn=$('rotoCalcBtn');if(!btn)return;
  const on=aewOn()&&ipvCalcWanted();
  const done=!!ready&&!!IPVCALC_UP;
  btn.style.display=(on&&!done)?'':'none';
  const res=$('rotoCalcRes');
  if(res)res.style.display=(on&&done)?'':'none';}
