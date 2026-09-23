// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Maxim Si
// редактор раскладки камер и мини-плеер CPV
//
// Часть интерфейса Reelsi. Файлы static/app/ грузятся ПО ПОРЯДКУ ИМЁН обычными
// <script>-тегами (не модулями): один общий скоуп, как было в едином app.js.
// Порядок важен — объявления функций поднимаются в пределах своего файла.

// ================= camera layout editor (отдельное окно) =================
// НИ ОДНОГО зелёного: зелёный в этом UI значит «над этим я поработал» (правка юзера №1),
// и камера 3 в полосе раскладки читалась как «этот кусок уже готов».
const CAMCOL=['#6ea8ff','#e0a865','#c98ade','#5fc9d6'];
let CAMED={xml:'',i:-1,n:1,segs:[],assign:[],total:0,names:[]};
async function openCamsFor(i){const c=CLIPS[i];CAMED={xml:c.xml,i,n:1,segs:[],assign:[],total:0,names:[]};
  $('camsname').textContent=c.name;$('camsres').textContent='';$('camsstrip').innerHTML='';$('camslegend').innerHTML='';$('camsswap').innerHTML='';
  $('camslist').innerHTML='<div class="empty">'+t('Загрузка…')+'</div>';cpvReset();openModal('mbCams');
  try{const d=await (await fetch('/api/cams_load',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({xml:c.xml})})).json();
    if(d.error){toast(errText(d));closeModal('mbCams');return;}
    CAMED.n=d.n;CAMED.segs=d.segs||[];CAMED.assign=d.assign||[];CAMED.total=d.total||0;CAMED.names=d.names||[];
    CAMED.saved=(d.assign||[]).slice();   // база для гарда «закрываешь несохранённое»
    if(d.n<2){$('camslist').innerHTML='<div class="empty">'+t('У клипа одна камера — распределять нечего.')+'</div>';return;}
    renderCams();renderSwap();cpvOpen(c.xml);
  }catch(e){toast(t('Не загрузил раскладку камер — сервер не ответил. Проверь, что webui запущен'));uiLog(t('cams_load: ')+e);}}

// ---- мини-плеер раскладки камер (CPV): монтаж по видео, звук — выбранная камера (К1/К2/…) ----
// клонирован из IPV, но: свой audioCi (какую камеру слушать), звук выбранной камеры играет
// НЕПРЕРЫВНО (постоянный оффсет delta[k] от аудио кам1), даже когда её картинки на экране нет.
let CPV={vids:[],bufs:[],scrubbing:false,scrubT:0,
  segs:[],audio:[],words:[],dur:0,aidx:0,vidx:-1,primed:-1,curCi:-1,rollCi:-1,playing:false,raf:0,itv:0,xml:'',audioCi:0,delta:[]};
function cpvReset(){cpvPause();CPV={vids:[],bufs:[],scrubbing:false,scrubT:0,
  segs:[],audio:[],words:[],dur:0,aidx:0,vidx:-1,primed:-1,curCi:-1,rollCi:-1,playing:false,raf:0,itv:0,xml:'',audioCi:0,delta:[]};
  const st=$('cpvstage');if(st)[...st.querySelectorAll('video')].forEach(v=>v.remove());
  $('cpvsub').textContent='';$('cpvtime').textContent='0:00 / 0:00';$('cpvseek').value=0;$('cpvaudio').innerHTML='';}
async function cpvOpen(xml){
  let d;try{d=await (await fetch('/api/aicut_preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({xml})})).json();}
  catch(e){d={error:''+e};}
  if(d.error||!(d.cams||[]).length||!d.cams[0].path){uiLog(t('раскладка камер: предпросмотр — ')+(d.error||t('нет камер')));return;}
  CPV.xml=xml;CPV.segs=d.segs||[];CPV.audio=(d.audio&&d.audio.length?d.audio:d.segs)||[];
  CPV.words=d.words||[];CPV.dur=d.dur||(CPV.audio.length?CPV.audio[CPV.audio.length-1].te:0);CPV.audioCi=0;
  CPV.cams=d.cams;   // дублёру нужны пути камер, чтобы переезжать на прокси
  const stage=$('cpvstage');[...stage.querySelectorAll('video')].forEach(v=>v.remove());
  const px=await pvProxyLoad(xml,true);pvProxyMerge(px);   // прокси камер: без него 4:2:2 10 бит встаёт на каждом стыке
  CPV.vids=d.cams.map((c,ix)=>{const v=document.createElement('video');
    v.src=pvSrc(c.path);v.preload='auto';v.muted=(ix!==0);v.playsInline=true;
    v.volume=MEDIA_VOL;v.style.zIndex=(ix===0)?'2':'1';stage.insertBefore(v,$('cpvsub'));return v;});
  // Оффсеты и дублёры — общие (camDeltas/camBufs): раньше дублёр был только у звуковой камеры
  // и пересоздавался при смене К1/К2/К3, теперь он есть у КАЖДОЙ камеры с самого открытия —
  // все они непрерывные дорожки, и звуковая среди них ничем не выделена.
  CPV.bufs=[];bufMake(CPV,stage,$('cpvsub'),0,0);
  CPV.delta=camDeltas(CPV);camBufs(CPV,stage,$('cpvsub'));
  // кнопки выбора звука
  $('cpvaudio').innerHTML=d.cams.map((c,ix)=>'<label class="'+(ix===0?'on':'')+'"><input type="radio" name="cpvaud" '+(ix===0?'checked':'')+' onchange="cpvAudio('+ix+')"> '+t('К')+(ix+1)+'</label>').join('');
  cpvSeekTo(0);
  if(px&&px.building){PVPX.xml=xml;pvProxyWatch('cpvstage');}   // прокси готовятся — догнать их на переезде
}
// Смена «слушаем К1/К2/К3»: дублёр звуковой камере больше не нужен отдельно — он у неё уже
// есть (camBufs делает его каждой камере при открытии), поменять надо только кто звучит.
// Без своего дублёра её на каждой склейке дёргал cpvSyncAudioCam (допуск 0.25с), то есть
// звук вставал ровно там же, где раньше вставала картинка.
function cpvAudio(k){CPV.audioCi=k;
  document.querySelectorAll('#cpvaudio label').forEach((l,i)=>l.classList.toggle('on',i===k));
  if(!CPV.vids.length)return;
  if(CPV.vids[k])CPV.vids[k].playbackRate=1;   // звуковой камере скорость не правим: с неё идёт звук
  // мгновенно применить: перемотать выбранную камеру к текущему моменту и (пере)запустить
  const tm=cpvNow();cpvApply(tm,CPV.playing);if(CPV.playing){cpvSyncAudioCam();sparePrime(CPV);}}
function cpvSyncAudioCam(){const k=CPV.audioCi;if(k<=0||!CPV.vids[k])return;   // подвести звуковую камеру к моменту кам1
  const want=CPV.vids[0].currentTime+(CPV.delta[k]||0);
  if(Math.abs(CPV.vids[k].currentTime-want)>0.25){try{CPV.vids[k].currentTime=want;}catch(e){}}}
// Картинка ракурса — общая машина всех плееров (camApply в 60-preview.js). Звуковая камера
// (CPV.audioCi) — отдельная непрерывная дорожка: её время ведёт cpvSyncAudioCam, машина
// ракурсов только показывает её и не трогает ни seek, ни скорость (иначе поехал бы звук).
function cpvApply(tm,play){camApply(CPV,tm,play);if(play)cpvSyncAudioCam();}
function cpvSeekTo(tm){if(!CPV.vids.length||!CPV.audio.length)return;tm=Math.max(0,Math.min(tm,CPV.dur));
  CPV.aidx=pvSegAt(CPV.audio,tm);CPV.vidx=-1;CPV.primed=-1;const a=CPV.audio[CPV.aidx];
  try{CPV.vids[0].currentTime=a.src+Math.max(0,tm-a.ts);}catch(e){}
  spareIdle(CPV);camIdle(CPV);cpvApply(tm,false);cpvSyncAudioCam();cpvUI(tm);}
function cpvNow(){const a=CPV.audio[CPV.aidx];return (a&&CPV.vids.length)?a.ts+(CPV.vids[0].currentTime-a.src):0;}
// Дублёр подменяет ВЕДУЩУЮ камеру (слот 0) — она же таймбаза. Если звук слушают с другой
// камеры (К2/К3), её всё равно подтягивает cpvSyncAudioCam по допуску 0.25с, и вот там
// seek на стыке остаётся: под неё нужен второй дублёр. По умолчанию звук с К1 — этот
// случай подмена закрывает целиком.
function cpvStep(){if(!CPV.playing)return;
  const st=pvStep(CPV);if(!st)return;
  if(st.end){cpvPause();cpvSeekTo(0);return;}
  cpvApply(st.tm,true);spareRollAt(CPV,st.tm);
  cpvUI(st.tm);}
function cpvTick(){if(!CPV.playing)return;cpvStep();CPV.raf=requestAnimationFrame(cpvTick);}
function cpvPlay(){if(!CPV.vids.length)return;CPV.playing=true;$('cpvplay').innerHTML=ico('pause');
  CPV.vids[0].play().catch(()=>{});cpvApply(cpvNow(),true);sparePrime(CPV);
  CPV.raf=requestAnimationFrame(cpvTick);clearInterval(CPV.itv);CPV.itv=setInterval(cpvStep,120);}
function cpvPause(){if(!CPV||!CPV.vids)return;CPV.playing=false;const b=$('cpvplay');if(b)b.innerHTML=ico('play');
  cancelAnimationFrame(CPV.raf);clearInterval(CPV.itv);CPV.vids.forEach(v=>v.pause());spareStop(CPV);camIdle(CPV);}
function cpvToggle(){CPV.playing?cpvPause():cpvPlay();}
// разбег готовим один раз, когда протяжка улеглась (см. pvScrub — та же причина)
function cpvScrub(x){if(!CPV.vids.length)return;const tm=x/1000*CPV.dur;const was=CPV.playing;
  CPV.scrubbing=true;clearTimeout(CPV.scrubT);cpvPause();cpvSeekTo(tm);if(was)cpvPlay();
  CPV.scrubT=setTimeout(()=>{CPV.scrubbing=false;if(CPV.playing)sparePrime(CPV);},150);}
function cpvSeekAndPlay(tm){if(!CPV.vids.length)return;cpvSeekTo(tm);cpvPlay();}
function cpvUI(tm){const seek=$('cpvseek');if(seek&&document.activeElement!==seek)seek.value=CPV.dur?Math.round(tm/CPV.dur*1000):0;
  $('cpvtime').textContent=fmtT(tm)+' / '+fmtT(CPV.dur);
  let cur='';for(const w of CPV.words){if(tm>=w.s&&tm<w.e){cur=w.w;break;}}$('cpvsub').textContent=cur;}
// замена файла камеры (обычно 2-й): заново свести под кам1, сохранив слова/жёлтые/вставки/раскладку
function renderSwap(){const host=$('camsswap');if(!host)return;const N=CAMED.n;
  let rows='';for(let k=1;k<N;k++){
    rows+='<div class="row" style="align-items:center;gap:8px;margin-top:6px">'
      +'<span style="display:inline-flex;align-items:center;gap:6px;font-size:12px;min-width:120px"><span style="width:12px;height:12px;border-radius:var(--r-sm);background:'+CAMCOL[k%4]+'"></span>'+t('Камера ')+(k+1)+'</span>'
      +'<span class="mono muted grow" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0">'+esc(CAMED.names[k]||'—')+'</span>'
      +'<button class="sm" onclick="swapCam('+k+')">'+t('Заменить файл…')+'</button></div>';}
  // справка — в «!», не абзацем в карточке (правка юзера №2)
  host.innerHTML='<div class="pcgroup"><div class="pchdr">'+t('Заменить камеру ')
    +'<span class="i" data-t="'+t('Если вторая камера свелась не с тем файлом (не тот дубль или рассинхрон). Выберешь другой файл — сведу заново под Камеру 1; слова, жёлтые, вставки и раскладка останутся.')+'">!</span></div>'
    +rows+'<div id="swapres" class="hint" style="margin-top:8px"></div></div>';
  tipArm(host);}                       // «!» этой панели рисуется здесь — фокус ему выдаём сами
async function swapCam(k){const el=$('swapres');const c=CLIPS[CAMED.i];
  try{const d0=await (await fetch('/api/pickmedia')).json();
    if(!d0||!d0.path)return;
    el.className='hint';el.textContent=t('свожу камеру {n}… (извлекаю аудио, синхронизирую)',{n:k+1});
    const d=await (await fetch('/api/swap_cam',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({xml:CAMED.xml,cam:k,path:d0.path})})).json();
    if(d.error){el.className='err';el.textContent='⚠ '+errText(d);return;}
    let msg=t('Камера {n} = {name} · синхрон {o}с (увер. {c})',{n:k+1,name:d.name,o:d.offset,c:d.conf});
    if(d.subs)msg+=t(' · субтитры {s} и жёлтые {y} сохранены',{s:d.subs,y:d.yellow});
    el.className=d.low_conf?'err':'ok';el.textContent=(d.low_conf?t('⚠ низкая уверенность синхрона — проверь в предпросмотре! '):'')+msg;
    uiLog(t('замена камеры ')+(k+1)+t(' (')+(c?c.name:'')+t('): ')+d.name+t(', синхрон ')+d.offset+t('с увер.')+d.conf);
    if(d.low_conf)toast(t('Синхрон неуверенный — открой предпросмотр и проверь, что звук совпадает с картинкой'));
    // перезагрузить окно (имена/раскладка), статус клипа И мини-плеер (иначе звук/картинка
    // второй камеры остаются от ПРОШЛОГО файла — старые <video> держат старый src)
    const r=await (await fetch('/api/cams_load',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({xml:CAMED.xml})})).json();
    if(!r.error){CAMED.names=r.names||[];CAMED.assign=r.assign||[];CAMED.saved=(r.assign||[]).slice();renderCams();renderSwap();$('swapres').className=d.low_conf?'err':'ok';$('swapres').textContent=el.textContent;}
    cpvReset();cpvOpen(CAMED.xml);
  }catch(e){el.className='err';el.textContent='⚠ '+e;}}
function renderCams(){const N=CAMED.n;
  $('camslegend').innerHTML=Array.from({length:N},(_,k)=>'<span style="display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--mut)"><span style="width:12px;height:12px;border-radius:var(--r-sm);background:'+CAMCOL[k%4]+'"></span>'+t('Камера ')+(k+1)+(k===0?t(' · звук'):'')+'</span>').join('');
  const strip=$('camsstrip');strip.innerHTML='';
  CAMED.segs.forEach((s,i)=>{const w=CAMED.total?(s.dur/CAMED.total*100):0;const el=document.createElement('div');
    el.style.cssText='width:'+w+'%;background:'+CAMCOL[CAMED.assign[i]%4]+';min-width:1px;border-right:1px solid rgba(0,0,0,.3)';
    el.dataset.t=t('#{n} · Камера {k}',{n:i+1,k:CAMED.assign[i]+1});strip.appendChild(el);});
  const host=$('camslist');const sc=host.scrollTop;host.innerHTML='';  // не теряем прокрутку при переключении камеры
  CAMED.segs.forEach((s,i)=>{const row=document.createElement('div');row.className='clip';row.style.padding='8px 12px';row.style.cursor='pointer';
    row.dataset.t=t('Клик — перейти к этому куску в плеере');
    row.tabIndex=0;row.setAttribute('role','button');
    let btns='';for(let k=0;k<N;k++){const on=CAMED.assign[i]===k;
      btns+='<button class="sm" onclick="event.stopPropagation();camSet('+i+','+k+')" style="'+(on?('background:'+CAMCOL[k%4]+';color:#0b0b0b;border-color:'+CAMCOL[k%4]+';font-weight:700'):'')+'">'+t('К')+(k+1)+'</button>';}
    row.innerHTML='<span class="idx">'+(i+1)+'</span>'
      +'<span class="mono muted" style="min-width:118px">'+fmtIns(s.tl)+' → '+fmtIns(s.tl+s.dur)+'</span>'
      +'<span class="mono muted" style="min-width:48px">'+s.dur.toFixed(1)+t('с')+'</span>'
      +'<span class="grow"></span><span style="display:flex;gap:4px">'+btns+'</span>';
    row.onclick=()=>cpvSeekAndPlay(s.tl+0.01);host.appendChild(row);});
  host.scrollTop=sc;}
function camSet(i,k){CAMED.assign[i]=k;renderCams();$('camsres').textContent='';}
async function camsAuto(){
  // затирает ручную раскладку без undo — соседние деструктивные действия тоже спрашивают
  if(CAMED.saved&&CAMED.assign&&CAMED.saved.join()!==CAMED.assign.join()
     &&!await askConfirm(t('Вернуть автоматическую раскладку? Ручные изменения пропадут.')))return;
  try{const d=await (await fetch('/api/cams_load',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({xml:CAMED.xml,auto:true})})).json();
  if(d.error){toast(errText(d));return;}CAMED.assign=d.assign||[];renderCams();
  $('camsres').className='muted';$('camsres').textContent=t('авто-раскладка — не забудь «Сохранить»');}
  catch(e){toast(t('Авто-раскладка не пришла — сервер не ответил'));uiLog(t('cams_load(auto): ')+e);}}
// Раскладка камер НЕ трогает разметку: меняется только то, чья картинка видна на куске,
// а keep-интервалы, их длины и звук (всегда камера 1) те же — значит слова, их тайминги
// и индексы жёлтых остаются прежними. Сервер вычитывает субтитры/жёлтые до пересборки и
// вписывает обратно (см. api_cams_save), поэтому ни предупреждения, ни clearHl тут нет.
async function camsSave(){const c=CLIPS[CAMED.i];const el=$('camsres');
  el.className='muted';el.textContent=t('сохраняю…');
  try{const d=await (await fetch('/api/cams_save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({xml:CAMED.xml,assign:CAMED.assign})})).json();
    if(d.error){el.className='err';el.textContent='⚠ '+errText(d);return;}
    const kept=(d.subs?t(' · субтитры {s} и жёлтые {y} сохранены',{s:d.subs,y:d.yellow}):'');
    el.className='ok';el.textContent=t('сохранено ({n} кусков)',{n:d.segs})+kept;
    CAMED.saved=(CAMED.assign||[]).slice();
    uiLog(t('раскладка камер: ')+(c?c.name:'')+t(' — ')+d.segs+t(' сегм.')+kept);
    if(c){c.edited=true;saveState();renderClips2();renderClips1();}
    cpvReset();cpvOpen(CAMED.xml);   // пересобранный XML — новый EDL в мини-плеере
  }catch(e){el.className='err';el.textContent='⚠ '+e;}}

