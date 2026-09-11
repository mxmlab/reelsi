// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Maxim Si
// вкладка «Видео»: генерация и история задач
//
// Часть интерфейса Reelsi. Файлы static/app/ грузятся ПО ПОРЯДКУ ИМЁН обычными
// <script>-тегами (не модулями): один общий скоуп, как было в едином app.js.
// Порядок важен — объявления функций поднимаются в пределах своего файла.

// ================= генерация видео (отдельная вкладка) =================
// Референсы: [{url, role, caption}] — url это ГОТОВАЯ https-ссылка (OpenRouter качает
// файл по URL, локальные не принимает). kind считается из расширения ссылки.
let VREFS=[], VIDPOLL=false, VIDCTX=null;
// VIDRETRY — счётчик временных transport-сбоев опроса, VIDRT — таймер повтора
// (см. pollVideo: сбой сети не конец оплаченной задачи, опрос повторяется).
const VID_RETRY_MS=[2000,4000,8000,15000];
let VIDRETRY=0,VIDRT=null;
// Поля вкладки живут в общем состоянии (localStorage + зеркало на сервере): F5 или
// уход на другой шаг не должен стирать набранный запрос и подобранные референсы —
// их собирают минутами, а генерация к тому же перезагрузку переживает.
function vidStateObj(){const au=$('vid_audio');
  // разрешение НЕ сохраняем: оно общая настройка в ai_config, у вкладки своего нет
  return {prompt:val('vid_prompt'),dur:val('vid_dur'),
          aspect:val('vid_aspect'),seed:val('vid_seed'),audio:!!(au&&au.checked),refs:VREFS};}
let VIDSAVED=null;      // восстановленные значения ждут, пока селекты наполнятся моделью
function vidRestoreForm(){if(!VIDSAVED)return;
  const s=VIDSAVED,put=(id,v)=>{const el=$(id);if(!el||v==null||v==='')return;
    // список зависит от модели: чего в нём нет — не ставим, иначе селект съедет на первое
    if(el.tagName==='SELECT'&&![...el.options].some(x=>x.value===v))return;
    el.value=v;};
  put('vid_seed',s.seed);
  const au=$('vid_audio');if(au&&!au.disabled)au.checked=!!s.audio;
  VIDSAVED=null;   // осталось только то, что не зависит от списков модели
}
function openVideo(){
  if(STEP===3&&curAE>=0)captureAE();
  // .done со степпера НЕ снимаем: пройденные шаги остаются кликабельными (и по виду тоже).
  // Раньше на вкладке «Видео» все три чипа выглядели неактивными, хотя клик работал.
  [1,2,3].forEach(k=>{$('step'+k).classList.remove('on');
    const si=$('si'+k);si.classList.remove('active');si.classList.toggle('done',!!CLIPS.length);});
  $('pageVideo').classList.add('on');
  const nv=$('navvideo');if(nv)nv.classList.add('active');
  STEP=0;
  fillVideoControls();renderVidRefs();
  vidSyncCaps();                   // каталог провайдера — сам, в фоне (см. vidSyncCaps)
  vidHistLoad();                   // задачи сервера: что готово скачать и что оборвалось
  try{localStorage.setItem('reelsi_step','video');}catch(e){}
}
function vidRoleName(r){return r==='first_frame'?t('первый кадр'):r==='last_frame'?t('последний кадр'):t('референс');}
function vidUrlKind(u){u=(u||'').split('?')[0].split('#')[0].toLowerCase();
  return /\.(mp4|mov|m4v|webm|mkv|avi)$/.test(u)?'video':'image';}
function vidRefKind(r){return (r&&r.kind)?r.kind:vidUrlKind(r&&r.url);}
function vidUrlOk(u){return /^https:\/\/\S+/i.test((u||'').trim());}
function vidAddRef(){VREFS.push({url:'',role:'reference',caption:''});renderVidRefs();
  // фокус на новом поле ссылки
  setTimeout(()=>{const els=document.querySelectorAll('#vidrefs .vrurl');if(els.length)els[els.length-1].focus();},0);}
// Что лежит по ссылке, спрашиваем у сервера (ffprobe): расширения у ссылок часто нет,
// а фото и видео уходят в РАЗНЫЕ поля запроса; плюс нужна длина видео — Seedance берёт
// все видео-референсы суммарно не длиннее 15с и иначе отвечает 400 уже после отправки.
async function vidProbe(i){const r=VREFS[i];if(!r||!vidUrlOk(r.url))return;
  const url=r.url.trim();
  if(r.probed===url)return;
  r.probed=url;r.probing=true;renderVidRefs();
  let d;try{d=await (await fetch('/api/video_probe',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({url})})).json();}catch(e){d=null;}
  if(VREFS[i]!==r||r.url.trim()!==url)return;             // ссылку успели поменять
  r.probing=false;
  // у картинки ffprobe тоже показывает длительность (0.04с) — она бессмысленна и
  // только мусорила бы в бюджете видео-референсов
  if(d&&d.kind){r.kind=d.kind;r.duration=(d.kind==='video'&&d.duration)||0;
    r.w=d.width||0;r.h=d.height||0;r.fmt=d.fmt||'';}
  renderVidRefs();vidRefreshAutoShape();}
function renderVidRefs(){const host=$('vidrefs');if(!host)return;
  const c=vidCaps();
  vidRefSum(c);
  // Пустое состояние — одна строка, как у остальных списков: что тут бывает и откуда
  // берутся теги, объяснено в «!» этой карточки и поля запроса (справка — только в тултипах).
  if(!VREFS.length){host.innerHTML='<div class="empty">'+t('Референсов нет — «Добавить ссылку».')+'</div>';return;}
  let imgN=0,vidN=0,audN=0;
  const frames=(c&&c.frame_images)||['first_frame','last_frame'];
  host.innerHTML=VREFS.map((r,i)=>{
    const kind=vidRefKind(r);
    // @-тег по типу — как Seedance связывает файл с промптом (@video1 — движение/
    // камера, @image1 — внешность/кадр). Нумерация по типу, в порядке карточек.
    const tag=kind==='video'?('@video'+(++vidN)):kind==='audio'?('@audio'+(++audN))
      :('@image'+(++imgN));
    const num=tag+((r.role||'reference')!=='reference'?(' · '+vidRoleName(r.role)):'');
    const has=(r.url||'').trim();
    const badUrl=has&&!vidUrlOk(r.url);
    const prev=(!has||kind==='audio'||kind==='page')
      ?'<span class="vrph">'+(kind==='audio'?t('звук'):kind==='page'?t('не файл'):t('нет ссылки'))+'</span>'
      :(kind==='video'?'<video src="'+esc(r.url)+'" muted playsinline preload="metadata"></video>'
        :'<img src="'+esc(r.url)+'" alt="" onerror="this.style.display=\'none\'">');
    const opt=(v,lab)=>'<option value="'+v+'"'+((r.role||'reference')===v?' selected':'')+'>'+lab+'</option>';
    const capPh=kind==='video'?t('Что взять с {tag} — напр.: движение и ритм камеры, персонаж',{tag:tag})
                               :t('Что взять с {tag} — напр.: внешность/лицо, стиль',{tag:tag});
    // Кадром может быть только ФОТО (frame_images — это image_url): видео, поставленное
    // первым кадром, провайдер не принимал. Поэтому у видео роль одна — референс.
    const roles=(kind!=='image')?opt('reference',t('референс'))
      :(opt('reference',t('референс'))
        +(frames.indexOf('first_frame')>=0?opt('first_frame',t('первый кадр')):'')
        +(frames.indexOf('last_frame')>=0?opt('last_frame',t('последний кадр')):''));
    const meta=r.probing?'<span class="muted">'+t(' проверяю…')+'</span>'
      :(has&&(r.duration||r.w)?'<span class="muted"> '+(r.duration?(r.duration.toFixed(1)+t('с · ')):'')
        +(r.w?(r.w+'×'+r.h):'')+'</span>':'');
    return '<div class="vidref"><div class="vrprev">'+prev+'<span class="vrkind">'
      +(has?(kind==='video'?t('видео'):kind==='audio'?t('аудио'):kind==='page'?'—':t('фото')):'—')+'</span></div>'
      +'<div class="vrbody">'
      +'<div class="vrtop"><span class="vrnum" data-t="'+t('Тег для промпта: {kind}. Модель связывает файл по нему',{kind:kind})+'">'+esc(num)+'</span>'
      +meta
      +vidRefWarn(r,c)
      +(badUrl?' <span style="color:var(--yel)">'+t('нужна https:// ссылка')+'</span>':'')
      +'<span class="grow"></span>'
      +'<select onchange="VREFS['+i+'].role=this.value;renderVidRefs()">'+roles+'</select>'
      +'<button class="icon" aria-label="'+t('Убрать референс')+'" data-t="'+t('Убрать референс')+'" onclick="vidDelRef('+i+')">'+ico('x')+'</button></div>'
      +'<input class="vrurl" aria-label="'+t('Ссылка на референс')+'" placeholder="'+t('https://…  прямая ссылка на фото или видео')+'" '
      +'value="'+esc(r.url||'')+'" oninput="VREFS['+i+'].url=this.value" onchange="vidProbe('+i+')">'
      +'<input class="vrcap" placeholder="'+esc(capPh)+'" '
      +'value="'+esc(r.caption||'')+'" oninput="VREFS['+i+'].caption=this.value"></div></div>';
  }).join('');
}
// Сводка над списком: сколько чего и укладываются ли видео в бюджет модели. Именно
// на этом ловится 400 «video total duration ... 15.2 in r2v» — до отправки.
function vidRefSum(c){const el=$('vidrefsum');if(!el)return;
  const use=VREFS.filter(r=>(r.url||'').trim());
  if(!use.length){el.textContent='';return;}
  const vids=use.filter(r=>vidRefKind(r)==='video'&&(r.role||'reference')==='reference');
  const imgs=use.filter(r=>vidRefKind(r)==='image'&&(r.role||'reference')==='reference');
  if(!imgs.length&&!vids.length){el.textContent='';return;}   // годных ещё нет — молчим
  const total=vids.reduce((s,r)=>s+(r.duration||0),0);
  const lim=c&&c.ref_video_total_s;
  let txt=t('фото: {im} · видео: {vd}',{im:imgs.length,vd:vids.length});
  if(vids.length&&total)txt+=' ('+total.toFixed(1)+t('с')+(lim?(t(' из ')+lim+t('с')):'')+')';
  el.innerHTML=esc(txt);
  el.style.color=(lim&&total>lim+0.2)?'var(--yel)':'';}
function vidDelRef(i){VREFS.splice(i,1);renderVidRefs();vidRefreshAutoShape();}
async function vidGenerate(){
  if(!vidGenOn()){toast(t('⚠ Выбери профиль «Видео» (нужен OpenRouter-ключ и видео-модель)'));return;}
  const prompt=val('vid_prompt').trim();
  const refs=VREFS.filter(r=>(r.url||'').trim());
  // промпт обязателен у всех моделей — запрос из одних референсов провайдер отвергает
  if(!prompt){toast(t('Напиши, что снять — одних референсов провайдеру мало'));return;}
  const bad=refs.find(r=>!vidUrlOk(r.url));
  if(bad){toast(t('⚠ Референс должен быть https:// ссылкой: ')+(bad.url||'').slice(0,40));return;}
  // Модель берёт сервер из общего ai_config; разрешение — тоже общее (ai_config).
  const body={prompt,
    duration:val('vid_dur')||undefined,
    aspect_ratio:val('vid_aspect')||undefined,
    seed:val('vid_seed').trim()||undefined,audio:$('vid_audio').checked,
    // kind/duration уже узнаны ffprobe'ом при вставке ссылки — сервер по ним и
    // раскладывает референсы по полям запроса, и считает бюджет r2v
    refs:refs.map(r=>({url:r.url.trim(),role:r.role,caption:r.caption,
                       kind:vidRefKind(r),duration:r.duration||0,w:r.w||0,h:r.h||0}))};
  $('vidresult').style.display='none';
  logReset();
  progShow(t('Генерация видео'),t('отправляю запрос…'));
  const b=$('vidgo');if(b)b.disabled=true;
  try{await videoStart(body,{
    onResult:res=>vidShowResult(res),
    onError:d=>toast('⚠ '+errText(d)),
    onCancel:d=>toast(errText(d)||t('Генерация видео остановлена')),
  });}
  catch(e){vidBusy(false);hideProg();toast('⚠ '+(e.message||e));}
}
// Кнопка «Остановить» живёт ровно на время генерации: пока она идёт, «Сгенерировать»
// всё равно недоступна, а без пары к ней прервать облачный запрос было нечем.
function vidBusy(on){const g=$('vidgo'),s=$('vidstop');
  if(g)g.disabled=!!on;
  if(s)s.style.display=on?'':'none';}
let VIDCANCEL=false;
async function vidCancel(){
  const s=$('vidstop');if(s)s.disabled=true;
  progUpdate(null,t('останавливаю…'),t('Генерация видео'),t('видео считается в облаке'));
  try{const d=await (await fetch('/api/video_cancel',{method:'POST'})).json();
    if(!d.ok)throw new Error(errText(d));
    VIDCANCEL=true;                 // только подтверждённая отмена меняет исход done
    uiLog(t('генерация видео: остановлено пользователем'));}
  catch(e){VIDCANCEL=false;if(s)s.disabled=false;
    toast(t('Не отправил остановку — генерация продолжается'));
    uiLog(t('генерация видео: отмена не отправлена, продолжаю опрос'));}}
// Один старт и один poller для вкладки и карточек вставок. Контекст определяет,
// куда положить результат; второй poller мог бы забрать done у карточки и оставить
// её навсегда в `genBusy`.
function videoCall(ctx,name,...args){try{if(ctx&&ctx[name])ctx[name](...args);}catch(e){uiLog('video context: '+e);}}
async function videoStart(body,ctx){
  if(VIDPOLL)throw new Error(t('Генерация видео уже идёт'));
  let d;
  const headers={'Content-Type':'application/json'};
  if(ctx&&ctx.token)headers['X-Reelsi-Video-Context']=ctx.token;
  try{d=await (await fetch('/api/video_gen',{method:'POST',headers,
    body:JSON.stringify(body)})).json();}
  catch(e){throw new Error(t('Сервер не ответил'));}
  if(d.error){const e=new Error(errText(d));e.data=d;throw e;}
  uiLog(t('🎬 генерация видео: ')+(d.model||''));
  VIDCANCEL=false;VIDCTX=ctx||null;videoCall(VIDCTX,'onStart',d);vidBusy(true);
  VIDRETRY=0;clearTimeout(VIDRT);VIDPOLL=true;pollVideo();   // новый старт — счётчик transport-сбоев и повторы с нуля
  vidHistLoad();                  // задача уже в истории сервера — показать её строкой «идёт»
  return d;
}
function videoFinish(d,ctx){
  try{
    // Готовый файл главнее локального флага отмены: POST /cancel мог прийти к
    // серверу уже после done, и терять оплаченный ролик в таком окне нельзя.
    if(d.result){if(ctx&&ctx.onResult)videoCall(ctx,'onResult',d.result,d);else vidShowResult(d.result);
      progDone((ctx&&ctx.doneText)||t('Готово — видео ниже'));
      return;}
    // при отмене текст из потока НЕ глотаем: там сказано, приняли ли её у
    // провайдера — иначе задача досчитается и молча спишется
    if(VIDCANCEL){progDone(t('Остановлено'));
      $('progFill').className='progfill';   // остановлено ≠ сделано: зелёный только у «Готово» (как у нарезки)
      if(ctx&&ctx.onCancel)videoCall(ctx,'onCancel',d);else toast(errText(d)||t('Генерация видео остановлена'));
      return;}
    if(d.error){progDone('✗ '+errText(d));
      if(ctx&&ctx.onError)videoCall(ctx,'onError',d);else toast('⚠ '+errText(d));
      return;}
    const miss={error:t('Готово (файла нет — см. логи)')};progDone(miss.error);
    if(ctx&&ctx.onError)videoCall(ctx,'onError',miss);else toast('⚠ '+miss.error);
  }finally{videoCall(ctx,'onSettled',d);}
}
// Сеть не даёт права считать оплаченный VJOB отменённым. Временный сбой опроса —
// НЕ конец задачи (поймано 2026-08-24): один reject/не-2xx/битый JSON раньше гасил
// VIDPOLL, стирал VIDCTX, снимал busy и показывал ложное «не удалось получить статус»,
// а доехавший позже оплаченный done UI уже не подхватывал без F5. Теперь сохраняем
// poller, контекст, busy и связь карточки и повторяем опрос с ограниченным backoff.
function vidRetryWait(){
  // retry без terminal: прогресс честно пишет «связь потеряна», но toast/onSettled
  // не зовём — задача жива и заплачена, вернёмся к ней следующим опросом.
  progUpdate(null,null,t('Генерация видео'),t('связь со статусом потеряна — повторяю…'));
  const idx=Math.min(VIDRETRY,VID_RETRY_MS.length-1);
  VIDRETRY=Math.min(VIDRETRY+1,VID_RETRY_MS.length-1);
  clearTimeout(VIDRT);
  VIDRT=setTimeout(pollVideo,VID_RETRY_MS[idx]);
}
// Валидный {running:false,done:false} — состояние СЕРВЕРА потеряно (рестарт/
// перезапись VJOB), это не сетевой сбой, и backoff тут не лечит. Poller останавливаем,
// но связь карточки бережём (transport:true — insVideoSettle её не стирает): оплаченная
// задача не забывается, в истории на сервере она видна. Сетевой фразой не путаем.
function videoStateLost(ctx){
  const d={error:t('Сервер потерял состояние генерации — проверь историю задач'),transport:true,lost:true};
  VIDPOLL=false;VIDCTX=null;VIDCANCEL=false;vidBusy(false);videoFinish(d,ctx);
}
function videoContextForStatus(d){return (d&&typeof insVideoContextForJob==='function')
  ?insVideoContextForJob(d.key,d.context):null;}
async function pollVideo(){
  if(!VIDPOLL)return;
  let d=null;
  try{
    const r=await fetch('/api/video_status?since='+LOGSINCE);
    if(!r.ok){vidRetryWait();return;}   // не-2xx — тот же временный сбой, повторяем
    d=await r.json();                   // битый JSON падает в catch ниже
  }catch(e){vidRetryWait();return;}
  VIDRETRY=0;                            // валидный status — сбой транспорта кончился
  mergeLog(d);
  const last=[...LOGCACHE].map(fmtLog).reverse().find(l=>l.trim())||'';
  const el=d.elapsed?(' · '+d.elapsed+t('с')):'';
  progUpdate(null,last.trim().slice(0,80)+el,t('Генерация видео'),t('видео считается в облаке'));
  if(!d.running&&!d.done){videoStateLost(VIDCTX);return;}
  if(d.done){VIDPOLL=false;vidBusy(false);const s=$('vidstop');if(s)s.disabled=false;
    vidHistLoad();                 // чем бы ни кончилось — строка задачи должна обновиться
    const ctx=VIDCTX;VIDCTX=null;videoFinish(d,ctx);return;}
  setTimeout(pollVideo,2000);
}
function vidShowResult(res){
  const box=$('vidresult'),host=$('vidplayer');if(!box||!host)return;
  box.style.display='';
  host.innerHTML='<video controls autoplay playsinline style="width:100%;max-height:70vh;background:#000;border-radius:var(--r)" '
    +'src="'+esc(res.url)+'"></video>'
    +'<div class="row" style="margin-top:10px">'
    +'<a class="lnk" href="'+esc(res.url)+'&dl=1" download style="text-decoration:none;display:inline-flex;align-items:center;gap:6px">'+ico('dl')+t(' Скачать mp4')+'</a>'
    +'<span class="grow"></span>'
    +'<span class="muted mono" style="font-size:12px">'+esc(res.name||'')+'</span></div>';
  const c=$('vidcost');
  if(c)c.textContent=(typeof res.cost==='number')?('$'+res.cost.toFixed(3)):'';
  try{const v=host.querySelector('video');if(v&&typeof MEDIA_VOL==='number')v.volume=MEDIA_VOL;}catch(e){}
  box.scrollIntoView({behavior:'smooth',block:'nearest'});
}

// ---- задачи генерации: история сервера --------------------------------------
// Генерация идёт минутами в облаке, а страницу за это время закрывают и жмут F5.
// Список берём с сервера (/api/video_history): там и готовые ролики (лежат в
// _videogen/out — их ещё можно скачать), и то, что не закончилось: ошибки,
// отменённое и оборванное рестартом. В памяти страницы этого держать негде.
let VHIST=[];
function vidHistLoad(manual){
  const host=$('vidhist');if(!host)return;
  if(manual)host.innerHTML='<div class="empty">'+t('Читаю…')+'</div>';
  fetch('/api/video_history').then(r=>r.json()).then(d=>{
    if(d.error){host.innerHTML='<div class="empty">'+t('Список задач не прочитался: ')+esc(errText(d))+'</div>';return;}
    VHIST=d.items||[];renderVidHist(d.running_key||'');
  }).catch(()=>{host.innerHTML='<div class="empty">'+t('Сервер не ответил — список задач недоступен.')+'</div>';});
}
function vhFind(k){return VHIST.find(x=>x.key===k);}
function vhWhen(ts){if(!ts)return '';
  const d=new Date(ts*1000);
  return d.toLocaleString('ru-RU',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});}
function vhSize(b){return b?((b/1048576).toFixed(1)+t(' МБ')):'';}
// Статус задачи -> тег. Зелёный только у «готово» — это и есть «сделано»; всё
// остальное серым/красным, чтобы зелёный не превратился в декор.
function vhTag(it,running){
  if(running)return '<span class="tag on">'+t('идёт')+'</span>';
  const s=it.status||'';
  if(s==='done')return it.exists?('<span class="tag ok">'+t('готово')+ico('check')+'</span>')
                                :'<span class="tag">'+t('файла нет')+'</span>';
  if(s==='running')return '<span class="tag on">'+t('идёт')+'</span>';
  if(s==='lost')return '<span class="tag bad">'+t('прервано')+'</span>';
  if(s==='cancelled')return '<span class="tag">'+t('остановлено')+'</span>';
  return '<span class="tag bad">'+t('ошибка')+'</span>';
}
function renderVidHist(runKey){
  const host=$('vidhist');if(!host)return;
  const sum=$('vidhistsum');
  if(!VHIST.length){host.innerHTML='<div class="empty">'+t('Задач пока не было — сгенерируй ролик выше.')+'</div>';
    if(sum)sum.textContent='';return;}
  const ready=VHIST.filter(x=>x.status==='done'&&x.exists);
  const bad=VHIST.filter(x=>x.status==='error'||x.status==='lost');
  if(sum)sum.textContent=t('готово: ')+ready.length+(bad.length?(t(' · не доделано: ')+bad.length):'');
  host.innerHTML=VHIST.map(it=>{
    const run=(it.key===runKey);
    const meta=[it.model||'',it.opts&&it.opts.duration?(it.opts.duration+t('с')):'',
      it.opts&&it.opts.resolution||'',it.opts&&it.opts.aspect_ratio||'',
      (typeof it.cost==='number')?('$'+it.cost.toFixed(3)):'',vhSize(it.size)]
      .filter(Boolean).join(' · ');
    // вторая полка: запрос, а если задача не доехала — текст отказа (в нём бывает
    // прямая ссылка на уже оплаченный ролик у провайдера, терять её нельзя)
    // id задачи дописываем, только если его нет в самом тексте отказа (в «видео
    // СОЗДАНО, но не скачалось» он уже есть — выходило «задача X … задача X»)
    const err=it.error?errText(it):'';
    const note=(it.status==='done'||run)?(it.prompt||'')
      :(err+((it.task&&err.indexOf(it.task)<0)?('  ·  '+t('задача ')+it.task):''));
    const k=esc(it.key);
    const acts=[];
    if(it.exists){
      acts.push('<button class="sm" onclick="vidHistShow(\''+k+'\')" data-t="'+t('Показать в плеере выше')+'">'+ico('play')+t(' Показать')+'</button>');
      acts.push('<a class="lnk" href="'+esc(it.url)+'&dl=1" download style="text-decoration:none">'+ico('dl')+t(' Скачать')+'</a>');}
    if(it.prompt)acts.push('<button class="sm" onclick="vidHistReuse(\''+k+'\')" data-t="'+t('Подставить запрос, референсы и настройки этой задачи в поля выше')+'">'+ico('undo')+t(' Повторить')+'</button>');
    if(!run)acts.push('<button class="icon" aria-label="'+t('Убрать задачу и файл')+'" data-t="'+t('Убрать запись и удалить файл ролика')+'" onclick="vidHistDel(\''+k+'\')">'+ico('trash')+'</button>');
    return '<div class="vhrow">'
      +'<div class="vhtop">'+vhTag(it,run)
      +'<span class="vhname grow" title="'+esc(it.path||'')+'">'+esc(it.name||t('без файла'))+'</span>'
      +'<span class="muted mono" style="font-size:11.5px">'+esc(vhWhen(it.ts))+'</span>'
      +'<span class="vhacts">'+acts.join('')+'</span></div>'
      +(note?'<div class="vhtxt'+((it.status==='error'||it.status==='lost')?' bad':'')
        +'" title="'+esc(note)+'">'+esc(note)+'</div>':'')
      +(meta?'<div class="vhtxt">'+esc(meta)+'</div>':'')
      +'</div>';}).join('');
}
function vidHistShow(key){const it=vhFind(key);if(!it||!it.exists){toast(t('Файла уже нет на диске'));return;}
  vidShowResult({url:it.url,name:it.name,cost:it.cost});}
// «Повторить» — вернуть настройки задачи в поля. Ровно то, ради чего история и
// заводилась: после перезагрузки страницы удачный запрос не надо набирать заново.
async function vidHistReuse(key){const it=vhFind(key);if(!it)return;
  const tp=$('vid_prompt');if(tp)tp.value=it.prompt||'';
  VREFS=(it.refs||[]).map(r=>({url:r.url||'',role:r.role||'reference',caption:r.caption||'',
                               kind:r.kind||'',duration:r.duration||0,w:r.w||0,h:r.h||0}));
  const o=it.opts||{};
  const put=(id,v)=>{const el=$(id);if(!el||v==null||v==='')return;
    if(el.tagName==='SELECT'&&![...el.options].some(x=>x.value===String(v)))return;
    el.value=String(v);};
  put('vid_seed',o.seed);
  const au=$('vid_audio');if(au&&!au.disabled)au.checked=!!o.audio;
  // Разрешение — общая настройка в ai_config: «Повторить» ЖДЁТ смены модели, затем
  // выставляет разрешение ЧЕРЕЗ СЕРВЕРНЫЙ action и только если оно поддерживается.
  // Старая задача без opts.resolution или с несовместимым значением UI не ломает.
  const ms=$('vid_model');
  // модель меняем ТОЛЬКО если она в списке: иначе селект молча съедет на первую,
  // и «повторить» повторило бы чужой моделью
  if(ms&&it.model&&[...ms.options].some(x=>x.value===it.model)&&ms.value!==it.model){
    ms.value=it.model;
    await setVideoModel(it.model);
  }
  if(o.resolution&&vidSupportsRes(o.resolution))await setVideoResolution(o.resolution);
  vidRefreshAutoShape();
  renderVidRefs();saveState();
  toast(t('Настройки задачи подставлены'));
  $('vid_prompt').scrollIntoView({behavior:'smooth',block:'nearest'});}
async function vidHistDel(key){const it=vhFind(key);if(!it)return;
  if(!await askConfirm(it.exists?t('Убрать задачу и удалить файл «{n}»?',{n:it.name||''})
                       :t('Убрать задачу из списка?')))return;
  let d;try{d=await (await fetch('/api/video_history',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'delete',key})})).json();}
  catch(e){toast(t('Сервер не ответил'));return;}
  if(d.error){toast('⚠ '+errText(d));return;}
  vidHistLoad();}

// Каталог возможностей модели (бесплатный GET /videos/models). Показывает факты и
// подстраивает UI: параметры, разрешения/пропорции, принимает ли ВИДЕО-референс.
let VIDCAPS=null,VIDSYNC=false;
// Каталог моделей провайдера подтягивается САМ, в фоне (бесплатный GET, не генерация):
// пользователю не надо знать про списки допустимых длин и размеров — ему надо видеть
// в полях только то, что модель берёт. Не ответил — работаем на встроенных фактах.
async function vidSyncCaps(force){
  if(!vidGenOn()||(VIDSYNC&&!force))return;
  VIDSYNC=true;
  const box=$('vidcaps');if(box)box.textContent='';
  let d;try{d=await (await fetch('/api/video_models',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({model:vidModelId()})})).json();}
  catch(e){d=null;}
  try{
    if(!d||d.error){VIDCAPS=null;
      if(box)box.textContent=t('каталог провайдера недоступен — ограничения встроенные');
      return;}
    VIDCAPS=d;
    // каталог мог измениться: сервер переоценил и сбросил устаревшее разрешение —
    // применяем его итог, чтобы не осталось скрытого local state
    if(d.video_resolution!==undefined)AICFG.video_resolution=d.video_resolution;
    // в выпадашку попадает всё, что реально есть у провайдера (кроме главных восьми
    // там ещё Sora, Wan, Runway и прочие)
    if(d.list&&d.list.length&&AICFG){AICFG.video_models=d.list;fillVideoControls();}
    else vidApplyModel();
  }finally{VIDSYNC=false;}
}
function setSelOpts(id,first,arr){const s=$(id);if(!s)return;const cur=s.value;
  s.innerHTML='<option value="">'+esc(first)+'</option>'+arr.map(v=>'<option>'+esc(String(v))+'</option>').join('');
  if([...s.options].some(o=>o.value===cur))s.value=cur;}
// Предупреждение на карточке референса — под ВЫБРАННУЮ модель: чего она не примет.
function vidRefWarn(r,c){
  const y=s=>' <span style="color:var(--yel)">'+s+'</span>';
  const role=r.role||'reference',kind=vidRefKind(r);
  // ссылка на страницу вместо файла — самая частая ошибка: провайдер качает URL сам
  if(kind==='page')return y(t('это страница, а не файл — нужна прямая ссылка (…/фото.jpg)'));
  if(kind==='audio')return y(t('аудио этот API не передаёт — звук задай словами в промпте'));
  if(!c)return '';
  if(role==='first_frame'||role==='last_frame'){
    if(kind==='video')return y(t('кадром может быть только фото — поставь роль «референс»'));
    if(c.frame_images&&c.frame_images.length&&c.frame_images.indexOf(role)<0)
      return y(t('этот кадр модель не принимает'));
    return vidFmtWarn(r,c);}
  if(kind==='video'&&c.ref_videos===0)
    return y(t('видео-референс эта модель не берёт — только Seedance 2.0'));
  if(c.references===false)return y(t('референсы не заявлены — файл не уйдёт, только подпись'));
  return vidFmtWarn(r,c);}
// Формат картинки под модель: Veo не берёт .webp (проверено отказом) — говорим ДО
// отправки. Где данных меньше (Kling/Hailuo) — мягче: «может не принять».
function vidFmtWarn(r,c){
  const f=(r.fmt||'').toLowerCase(),ok=(c&&c.image_formats)||[];
  if(!f||!ok.length||ok.indexOf(f)>=0)return '';
  const list=ok.map(x=>'.'+x).join(t(' или '));
  return ' <span style="color:var(--yel)">'
    +(c.image_formats_strict?t('.{f} не принимается — нужен {list}',{f:f,list:list})
                            :t('.{f} может не принять — надёжнее {list}',{f:f,list:list}))+'</span>';}

