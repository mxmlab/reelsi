// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Maxim Si
// окно вставок и база вставок: скан, автоподбор, импорт
//
// Часть интерфейса Reelsi. Файлы static/app/ грузятся ПО ПОРЯДКУ ИМЁН обычными
// <script>-тегами (не модулями): один общий скоуп, как было в едином app.js.
// Порядок важен — объявления функций поднимаются в пределах своего файла.

// ================= inserts modal =================
// заметки бэкенда о вставках (снап тайминга к фразе, сдвиги запретных зон) — в UI-лог
function insLog(d){(d&&d.log||[]).forEach(l=>{const s=fmtLog(l);if(!/^анализ модели/.test(s))uiLog('  '+s);});}
let curIns=-1;
const INS_PARTS=['inserts','subs'];
let INS_ACTIVE_PART='inserts';
function openInsPart(name){
  INS_ACTIVE_PART=name;
  try{localStorage.setItem('reelsi_ins_tab',name);}catch(e){}
  INS_PARTS.forEach(n=>{
    const el=$('inspart_'+n);if(el)el.style.display=(n===name)?'':'none';
  });
  const r=document.querySelector('#insparts input[name=inspart][value="'+name+'"]');
  if(r)r.checked=true;
  segUI();
  if(name==='subs')renderSubTab();
}
function syncSubTabUI(){
  const s=CURSTYLE||{};
  const sw=s.sub_words_per_row||1;
  const sr=s.sub_rows_max||1;
  if($('insp_subwords'))$('insp_subwords').value=sw;
  if($('insp_subrows'))$('insp_subrows').value=sr;
}
let _inspSubPatchTimer = null;
function patchSubStyleSoon(sw, sr){
  if(_inspSubPatchTimer)clearTimeout(_inspSubPatchTimer);
  _inspSubPatchTimer=setTimeout(async ()=>{
    _inspSubPatchTimer=null;
    const c=(curAE>=0&&CLIPS[curAE])?CLIPS[curAE]:null;
    const j=c?c.job:null;
    if(!j||!j.speaker){
      uiLog(t('настройки сабов не сохранены в файл: у клипа нет тега спикера'));
      return;
    }
    const spk=(typeof SPEAKERS!=='undefined')?SPEAKERS[j.speaker]:null;
    if(!spk||!spk.style){
      uiLog(t('настройки сабов не сохранены в файл: у спикера не задан стиль'));
      return;
    }
    const styleKey=spk.style;
    if(typeof BUILTIN_STYLES!=='undefined'&&BUILTIN_STYLES[styleKey]){
      const sLabel=(typeof STYLES!=='undefined'&&STYLES[styleKey]&&STYLES[styleKey].label)||styleKey;
      uiLog(t('настройки сабов не сохранены в файл: стиль «{s}» встроенный',{s:t(sLabel)}));
      return;
    }
    const patch={sub_words_per_row:sw,sub_rows_max:sr};
    try{
      const res=await fetch('/api/style_patch',{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({name:styleKey,patch})
      });
      const d=await res.json();
      if(d.error){
        uiLog(t('ошибка сохранения стиля «{n}»: {e}',{n:styleKey,e:(typeof errText==='function'?errText(d):(d.error||''))}));
      }else{
        if(typeof STYLES!=='undefined'&&STYLES[styleKey]){
          Object.assign(STYLES[styleKey],patch);
        }
        if(typeof STYLE_EDIT_ORIG!=='undefined'&&STYLE_EDIT_ORIG&&STYLE_EDITING===styleKey){
          Object.assign(STYLE_EDIT_ORIG,patch);
        }
      }
    }catch(e){
      uiLog(t('ошибка сети при сохранении стиля «{n}»: {e}',{n:styleKey,e:String(e)}));
    }
  },400);
}
function inspSubEdit(){
  if(!CURSTYLE)CURSTYLE=JSON.parse(JSON.stringify(STYLES.base||{}));
  let sw=parseInt(val('insp_subwords'));
  CURSTYLE.sub_words_per_row=isNaN(sw)||sw<1?1:Math.min(6,sw);
  let sr=parseInt(val('insp_subrows'));
  CURSTYLE.sub_rows_max=isNaN(sr)||sr<1?1:sr;
  if(typeof stRefresh==='function'){
    stRefresh('sub_words_per_row');
    stRefresh('sub_rows_max');
  }
  syncSubTabUI();
  captureAE();
  ipvPlanSoon();
  patchSubStyleSoon(CURSTYLE.sub_words_per_row,CURSTYLE.sub_rows_max);
}
function renderSubTab(){
  syncSubTabUI();
  renderSubRowsList();
}
function renderSubRowsList(){
  const host=$('subrowslist');if(!host)return;
  const subs=(IPV.plan&&IPV.plan.subs)||[];
  if(!subs.length){
    host.innerHTML='<span class="hint">'+t('Субтитры не загружены или ещё рассчитываются.')+'</span>';
    return;
  }
  host.innerHTML='';
  subs.forEach((sub,idx)=>{
    const el=document.createElement('div');
    el.className='subrow-item'+(sub.row?' subrow-r1':'');
    el.dataset.idx=idx;
    el.dataset.s=sub.s;
    el.dataset.e=sub.e;
    el.tabIndex=0;
    el.setAttribute('role','button');
    el.dataset.t=t('Клик — перейти к этой строке');
    el.innerHTML='<span class="subrow-time mono muted">'+fmtT(sub.s)+' – '+fmtT(sub.e)+'</span><span class="subrow-text">'+esc(sub.w)+'</span>';
    el.onclick=()=>ipvSeekTo(sub.s);
    el.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();ipvSeekTo(sub.s);}};
    host.appendChild(el);
  });
  subrowHighlight(ipvNow());
}
function subrowHighlight(tm){
  const host=$('subrowslist');if(!host)return;
  const subs=(IPV.plan&&IPV.plan.subs)||[];
  const items=host.querySelectorAll('.subrow-item');
  items.forEach((el,idx)=>{
    const sub=subs[idx];
    if(!sub)return;
    const on=(tm>=sub.s&&tm<=(sub.gend!=null?sub.gend:sub.e));
    el.classList.toggle('active',on);
  });
}
function openInsertsFor(i){if(i==null||i<0||!CLIPS[i]){toast(t('Выбери клип'));return;}
  curIns=i;IPVMODE='clips';
  INS_UNDO=null;insUndoUI();   // undo живёт в рамках сеанса окна — чужой клип/переоткрытие сбрасывает снимок
  if(curAE!==i||AEXML!==CLIPS[i].xml)selectAE(i);
  $('mbInserts').querySelector('.modal').classList.remove('aemode');
  $('mbInsTitle').textContent=t('Вставки');
  $('insres').textContent='';$('insname').textContent=CLIPS[i].name||'';
  renderInsHost();
  let savedTab='inserts';
  try{savedTab=localStorage.getItem('reelsi_ins_tab')||'inserts';}catch(e){}
  openInsPart(savedTab);
  openModal('mbInserts');ipvOpen(CLIPS[i].xml);}
// предпросмотр AE-вкладки: та же модалка, но вставки = INS (шаг 3) + оверлей интро;
// правая колонка карточек прячется (AE-вставки редактируются на странице, блоками на полосе — тоже)
function openAEPreview(){if(curAE<0){toast(t('Выбери клип'));return;}captureAE();
  ensureJobs();                                   // подтянуть вставки, добавленные на шаге 2 ПОСЛЕ выбора клипа
  INS=(CLIPS[curAE].job.ins||[]).map(x=>({...x}));renderIns();
  IPVMODE='ae';curIns=-1;INS_UNDO=null;insUndoUI();
  $('mbInserts').querySelector('.modal').classList.add('aemode');
  $('mbInsTitle').textContent=t('Предпросмотр AE — вставки · интро · слова');
  $('insres').textContent='';$('insname').textContent=CLIPS[curAE].name||'';
  openModal('mbInserts');ipvOpen(CLIPS[curAE].xml);
  if(AEWMODE==='style')styleToModal();                 // вкладка осталась с прошлого раза — вернуть блок в модалку
  introAllCount();aewLoadCaption(CLIPS[curAE].xml);aewRender();}

// ===== панель слов в AE-превью: ТЕ ЖЕ данные, что панель шага AE (WORDS/HL/BRK/INTRO) =====
// всё, что меняется здесь, сразу видно в задании сборки (captureAE) и на таймлайне интро.
// Вкладок «Жёлтые / Интро / Правка слов» больше нет (2026-07-30): переключаться между ними
// приходилось на каждое слово, а работа идёт по одному и тому же списку. Теперь один режим
// 'words' и три жеста по чипу: клик — жёлтое, двойной — в интро, Ctrl+клик — правка текста.
let AEWMODE='words';
function aewOn(){return $('mbInserts').classList.contains('on')&&IPVMODE==='ae';}
function aewSetMode(m){AEWMODE=m;
  document.querySelectorAll('#aewmode label').forEach(l=>l.classList.toggle('on',l.querySelector('input').checked));
  if(m==='style'){styleToModal();const p=$('stpanel');if(p&&!p.children.length&&typeof renderStylePanel==='function')renderStylePanel();}else styleHome();
  aewRender();}
// Блок стиля НЕ дублируется в модалке — сам узел #stylebox переезжает туда и обратно.
// Копия ломала бы всё: id перестали бы быть уникальными, onStyleChange/stEdit писали бы
// в невидимую копию, а captureAE читал бы старую.
function styleToModal(){const box=$('stylebox'),host=$('aewstyle');if(!box||!host)return;
  if(box.parentNode!==host)host.appendChild(box);
  host.style.display='';const aw=$('styleaway');if(aw)aw.style.display='';}
function styleHome(){const box=$('stylebox'),slot=$('styleslot');if(!box||!slot)return;
  if(box.parentNode!==slot)slot.insertBefore(box,slot.firstChild);
  const host=$('aewstyle');if(host)host.style.display='none';
  const aw=$('styleaway');if(aw)aw.style.display='none';
  rotoMaskHide();}   // маска рото не должна висеть после ухода со вкладки «Стиль»
function aewSync(){renderIntro();captureAE();aewRender();}   // изменение → джоб + таймлайн + панель
// ---- подпись о ролике в панели AE ----
function aewUpdateCaptionUI(){
  const box=$('aewcaption');if(!box)return;
  const on=!!(AEWMODE==='words'&&IPV.plan&&IPV.plan.caption);
  box.style.display=on?'':'none';
}
async function aewLoadCaption(xml){
  const box=$('aewcaption');if(!box)return;
  const inp=$('aewcaption_text');if(inp)inp.value='';
  const res=$('aewcaptionres');if(res)res.textContent='';
  aewUpdateCaptionUI();
  if(!xml)return;
  try{
    const d=await (await fetch('/api/caption',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({xml})})).json();
    if(d.ok&&inp)inp.value=d.text||'';
    aewUpdateCaptionUI();
  }catch(e){}
}
async function aewSaveCaption(){
  const xml=IPV.xml;if(!xml)return;
  const inp=$('aewcaption_text');const text=inp?(inp.value||'').trim():'';
  const btn=$('aewcaptionsave');if(btn)btn.disabled=true;
  const res=$('aewcaptionres');
  if(res){res.className='muted';res.textContent=t('сохраняю…');}
  try{
    const d=await (await fetch('/api/caption',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({xml,text})})).json();
    if(d.error){if(res){res.className='err';res.textContent='⚠ '+errText(d);}return;}
    if(res){res.className='ok';res.textContent=t('сохранено');}
    if(typeof ipvPlanSoon==='function')ipvPlanSoon();
  }catch(e){
    if(res){res.className='err';res.textContent='⚠ '+e;}
  }finally{
    if(btn)btn.disabled=false;
  }
}
function aewRender(){if(!aewOn())return;if(typeof applyStyleHlColor==='function')applyStyleHlColor();const host=$('aewwords');if(!host)return;host.innerHTML='';
  const words=(AEWMODE==='words');
  host.style.display=words?'':'none';
  $('aewintro').style.display=words?'':'none';
  {const bar=$('aewintrobar');if(bar)bar.style.display=words?'':'none';}
  aewUpdateCaptionUI();
  if(!words)return;
  if(!WORDS.length){host.innerHTML='<span class="hint">'+t('Слова не загружены — в XML нет субтитров или ещё грузятся.')+'</span>';
    aewRenderIntro();return;}
  // слова, уже ушедшие в интро, живут в списке групп выше — в общем списке их не дублируем
  const cons=introConsumed();
  const shown=WORDS.filter(o=>!cons.has(o.i));
  const cfg={
    words:WORDS,
    hl:HL,
    brk:BRK,
    cnt:CNT,
    jns:JNS,
    syncBreak:()=>aewSyncLight(),
    syncCount:(wi,ev)=>aewToggleCount(wi,ev),
    tip:(o)=>t('{s}с · двойной клик — в интро · Ctrl+клик — правка текста',{s:o.start}),
    chipClick:(o,wi,c,e)=>aewChip(o,wi,c,e),
    chipKeydown:(o,wi,c,e)=>{
      if(e.ctrlKey||e.metaKey)aewEditChip(o,wi,c);
      else if(e.shiftKey)aewToggleAccent(wi);
      else aewChip(o,wi,c,null);
    }
  };
  shown.forEach((o,k)=>{const prev=shown[k-1];
    if(prev)host.appendChild(wordBreakEl(cfg,prev,o));
    host.appendChild(wordChipEl(cfg,o,k));});
  // двойной клик = слово в интро. Делегируем на host: чип может быть заменён инпутом правки.
  host.ondblclick=(e)=>{const ch=e.target.closest&&e.target.closest('.chip');if(!ch)return;
    if(e.ctrlKey||e.metaKey)return;                   // Ctrl — правка, её сделал уже первый клик
    const wi=+ch.dataset.wi;const o=WORDS[wi];if(!o)return;
    e.preventDefault();aewUndoClicks(o);aewToggleAccent(wi);};
  aewPaint();aewRenderIntro();}
function aewToggleCount(wi,ev){
  if(ev){ev.stopPropagation();ev.preventDefault();}
  const o=WORDS[wi];if(!o)return;
  if(CNT.has(o.i))CNT.delete(o.i);
  else CNT.add(o.i);
  aewSyncLight();
}
// Жёлтое/разрыв — это ТОЛЬКО классы на уже existing узлах. Пересобирать список нельзя:
// Chrome выдаёт dblclick, лишь когда оба клика пришлись на ОДИН И ТОТ ЖЕ узел, а полная
// перерисовка на первом клике заменяет чип — и «двойной клик = в интро» не срабатывал
// вообще (2026-07-30, пойман настоящими кликами: dblclick не приходил ни разу).
function aewPaint(){const host=$('aewwords');wordsPaint(host,{words:WORDS,hl:HL,brk:BRK,cnt:CNT,jns:JNS});}
function aewSyncLight(){renderIntro();captureAE();aewPaint();}   // жёлтое/разрыв: джоб + таймлайн, список НЕ трогаем
// Жёлтый ставится СРАЗУ по первому клику (это самое частое действие, откладывать его на 220мс
// ради двойного — заметная задержка). Пара кликов двойного щёлкает жёлтым туда-обратно, и перед
// уходом слова в интро состояние возвращается ровно к тому, что было до первого клика.
let AEWDBL=null,AEWDBLT=null;
function aewMark(o){
  if(!AEWDBL||AEWDBL.i!==o.i)AEWDBL={i:o.i,hl:HL.has(o.i),brk:BRK.has(o.i),jns:JNS.has(o.i)};
  if(AEWDBLT)clearTimeout(AEWDBLT);
  AEWDBLT=setTimeout(()=>{AEWDBL=null;AEWDBLT=null;},450);}
function aewUndoClicks(o){
  if(!AEWDBL||AEWDBL.i!==o.i)return;
  AEWDBL.hl?HL.add(o.i):HL.delete(o.i);
  AEWDBL.brk?BRK.add(o.i):BRK.delete(o.i);
  AEWDBL.jns?JNS.add(o.i):JNS.delete(o.i);
  AEWDBL=null;if(AEWDBLT){clearTimeout(AEWDBLT);AEWDBLT=null;}}
function aewChip(o,wi,el,ev){
  if(ev&&(ev.ctrlKey||ev.metaKey)){ev.preventDefault();aewEditChip(o,wi,el);return;}
  // прицел «начать группу со слова» из строки интро — он взведён явно, жёлтый тут ни при чём
  if(INTRO_PICK>=0&&INTRO[INTRO_PICK]){INTRO[INTRO_PICK].from=wi;INTRO_PICK=-1;aewSync();return;}
  aewMark(o);
  if(HL.has(o.i)){HL.delete(o.i);BRK.delete(o.i);JNS.delete(o.i);}else HL.add(o.i);   // hl: как на вкладке AE
  aewSyncLight();}
// Двойной клик по слову ставит/снимает жёлтый акцент интро прямо с него — не надо ни выбирать
// слово, ни переключать вкладку. «Жёлтый» здесь — это color самой строки-акцента (не HL-набор субтитров):
// слово, ушедшее в интро, из жёлтых субтитров всё равно вычищается (hlDropIntro).
function aewToggleAccent(wi){
  const at=INTRO.findIndex(r=>r.from===wi&&(r.count|0)===1&&r.break);
  if(at>=0)INTRO.splice(at,1);                                    // повтор → снять акцент
  // Белый, а не жёлтый: двойной клик ставил жёлтый ВСЕГДА, и вся ручная добивка акцентов
  // выходила жёлтой (в наборе C1387-C1395 — 75% против 42% у ИИ). Жёлтый теперь ставится
  // осознанно — галкой «жёлтый» в строке группы.
  else INTRO.push({count:1,color:'white',break:true,from:wi});    // акцент с середины ролика
  INTRO_PICK=-1;AEW_TOEND=true;aewSync();}
function aewEditChip(o,wi,el){if(el.tagName==='INPUT')return;
  if(!el.isConnected)el=$('aewwords').querySelector('.chip[data-wi="'+wi+'"]');   // список успели перерисовать
  if(!el)return;
  const inp=document.createElement('input');inp.type='text';inp.value=o.w;
  inp.style.cssText='width:'+Math.max(60,o.w.length*11+20)+'px;font-size:13px';
  let done=false;                                  // Esc/сохранение уже закрыли — blur не должен сохранять второй раз
  const commit=()=>{if(done)return;done=true;aewSaveWord(o,inp.value);};
  inp.onkeydown=(e)=>{e.stopPropagation();
    if(e.key==='Enter'){commit();}
    else if(e.key==='Escape'){done=true;aewRender();}};
  inp.onblur=commit;                               // клик мимо = закрыть и сохранить
  inp.onclick=(e)=>e.stopPropagation();
  el.replaceWith(inp);inp.focus();inp.select();}
function aewAddLine(){INTRO.push({count:1,color:'white'});AEW_TOEND=true;aewSync();}
let AEW_TOEND=false;   // к низу списка крутим только когда строку реально добавили в конец
function aewRenderIntro(){const host=$('aewintro');if(!host)return;
  const old=$('aewintrolist'),keep=old?old.scrollTop:0;   // не дёргать список при правке в середине
  // кнопки СВЕРХУ (вне прокрутки), группы — компактным скролл-списком: не крутить весь экран
  host.innerHTML='<div class="row" style="gap:8px;margin-bottom:6px">'
    +(INTRO.length?'':'<button class="sm" onclick="aewAddLine()">'+ico('plus')+t(' первая строка')+'</button>')
    +'<span class="muted grow" style="font-size:11px;align-self:center" id="aewintrocnt"></span></div>'
    +'<div id="aewintrolist" class="aewintrolist"></div>';
  const list=$('aewintrolist');let total=0,gi=0;
  const cfg={arr:'INTRO',rows:INTRO,sync:'aewSync()',words:WORDS,
    pick:INTRO_PICK,clearPick:'INTRO_PICK=-1',add:'aewAddLineIn',
    arm:i=>'INTRO_PICK=(INTRO_PICK==='+i+')?-1:'+i+';aewRender()',
    edit:'aewEditIntroWord'};
  introWalk((r,i,idxs)=>{total+=idxs.length;if(introIsHead(INTRO,i))gi++;
    list.insertAdjacentHTML('beforeend',introRowHtml(cfg,r,i,idxs,gi));});
  $('aewintrocnt').textContent=INTRO.length?t('{n} групп · {m} слов',{n:INTRO.length,m:total}):t('групп нет — добавь строку или акцент');
  if(AEW_TOEND){list.scrollTop=list.scrollHeight;AEW_TOEND=false;}   // новая группа в конце — показать её
  else list.scrollTop=keep;
  INTROPLAY=-2;if(IPV.vids.length)ipvIntro(ipvNow());}   // вернуть подсветку текущей группы
function aewHighlight(tm){if(!aewOn()||!WORDS.length)return;   // подсветка текущего слова по плейхеду
  let k=-1;for(let i=0;i<WORDS.length;i++){const s=WORDS[i].start,ns=(i+1<WORDS.length?WORDS[i+1].start:1e9);
    if(tm>=s&&tm<ns){k=i;break;}}
  const chips=$('aewwords').querySelectorAll('.chip');
  chips.forEach(ch=>ch.classList.toggle('cur',+ch.dataset.wi===k));}
function syncClipLists(){renderClips2();renderClips3();}   // теги «вставок X из Y» в списках — сразу, без F5
// Клип берём в переменную ОДИН РАЗ, как в aiInsertsMore: curIns меняется по клику в
// списке, а ИИ думает секунды — читая CLIPS[curIns] после await, ответ про один клип
// записывался в другой, открытый к тому моменту.
async function aiInsertsRun(){if(curIns<0)return;const c=CLIPS[curIns];const xml=c.xml;const el=$('insres');
  el.className='muted';el.textContent=t('подбираю…');uiLog(t('вставки (ИИ) для ')+c.name+t('…'));
  try{const d=await aiFetch('/api/ai_inserts',{xml,rejected:c.ins_rejected||[]},'insStop','insres');
    if(d.error){el.className='err';el.textContent='⚠ '+errText(d);uiLog(t('  ОШИБКА: ')+d.error);return;}
    c.inserts=(d.inserts||[]).map(x=>({...x,media:''}));insLog(d);
    c.insTarget=Math.max(d.insTarget||0,c.inserts.length);   // цель «добрать» приезжает с бэкенда (от длины ролика), а не зашита в JS
    const nv=c.inserts.filter(x=>x.type==='video').length;
    const tail=await insAfterAI(c);   // файлы из базы + (по галке) генерация остатка
    el.className='ok';el.textContent=c.inserts.length+t(' ({p} фото + {v} видео)',{p:c.inserts.length-nv,v:nv})+tail;
    uiLog(t('  вставок: ')+c.inserts.length);
    renderInsHost();syncClipLists();saveState();
  }catch(e){if(aiAborted(e)){el.className='muted';el.textContent=t('⏹ остановлено');return;}
    el.className='err';el.textContent='⚠ '+e;}}
// добрать недостающие: сгенерить (target - текущих) НОВЫХ, не повторяя оставленные (тайминги/темы)
async function aiInsertsMore(){if(curIns<0)return;const c=CLIPS[curIns];const cur=c.inserts||[];
  // цель — с бэкенда (от длины ролика); 13 — фолбэк для клипов, разметанных до BX,
  // когда insTarget ещё не приезжал (иначе у длинного ролика набор «полон» на 13 из 22)
  const target=Math.max(c.insTarget||0,13);const need=Math.max(0,target-cur.length);
  const el=$('insres');
  if(need<=0){el.className='muted';el.textContent=t('ничего добирать — удали лишние карточки сначала');return;}
  el.className='muted';el.textContent=t('добираю {n}…',{n:need});uiLog(t('добор вставок ({n}) для {m}…',{n:need,m:c.name}));
  const avoid=cur.map(x=>({start_sec:x.start_sec,type:x.type,query:x.query||''}));
  try{const d=await aiFetch('/api/ai_inserts',{xml:c.xml,count:need,avoid,rejected:c.ins_rejected||[]},'insStop','insres');
    if(d.error){el.className='err';el.textContent='⚠ '+errText(d);uiLog(t('  ОШИБКА: ')+d.error);return;}
    insLog(d);
    const fresh=(d.inserts||[]).map(x=>({...x,media:''}))
      .filter(n=>!cur.some(o=>Math.abs((o.start_sec||0)-(n.start_sec||0))<1.0)).slice(0,need);
    c.inserts=cur.concat(fresh).sort((a,b)=>(a.start_sec||0)-(b.start_sec||0));
    const nv=c.inserts.filter(x=>x.type==='video').length;
    const tail=await insAfterAI(c);              // файлы из базы + (по галке) генерация остатка
    el.className='ok';el.textContent=t('добрано {f} · всего {t} ',{f:fresh.length,t:c.inserts.length})+t('({p} фото + {v} видео)',{p:c.inserts.length-nv,v:nv})+tail;
    uiLog(t('  добрано вставок: ')+fresh.length);
    renderInsHost();syncClipLists();saveState();
  }catch(e){if(aiAborted(e)){el.className='muted';el.textContent=t('⏹ остановлено');return;}
    el.className='err';el.textContent='⚠ '+e;}}
function insAdd(type){if(curIns<0)return;const c=CLIPS[curIns];if(!c.inserts)c.inserts=[];
  const vid=type==='video';
  // at, а не t: имя t занято функцией перевода, а тост ниже её зовёт (см. ipvUI/ipvOverlay)
  const at=Math.round(Math.max(0,ipvNow())*10)/10;   // на месте плейхеда предпросмотра
  c.inserts.push({type:vid?'video':'photo',start_sec:at,duration_sec:vid?3:2,query:'',prompt:'',mosaic:false,plate:false,media:''});
  c.inserts.sort((a,b)=>(a.start_sec||0)-(b.start_sec||0));
  renderInsHost();syncClipLists();saveState();
  if(IPV.vids.length&&!IPV.playing)ipvSeekTo(at+0.01);   // показать заглушку сразу (без сдвига t>=start даёт флоат-промах)
  toast((vid?t('Видео'):t('Фото'))+t('-вставка на ')+fmtIns(at)+t(' — выбери файл'));}
function insToggleType(i){const x=CLIPS[curIns].inserts[i];x.type=(x.type==='video')?'photo':'video';
  renderInsHost();syncClipLists();saveState();}
// удаление карточки ЦЕЛИКОМ: ИИ-предложение (есть query) запоминаем в ins_rejected —
// повторная разметка/«заново» передаст это модели («юзер удалил — не предлагай похожее»).
// В базу вставок при этом НЕ лезем: «эта вставка тут не нужна» — не то же самое, что
// «эта картинка не подходит под запрос» (см. insClearMedia).
// Перед мутацией делаем снимок для одноуровневого undo (insUndo): копию вставки, её
// индекс и ТОЧНОЕ ins_rejected ДО того, как сюда допишется брак. Undo вернёт как было.
function insDel(i){const c=CLIPS[curIns];if(!c||!c.inserts)return;const x=c.inserts[i];
  INS_UNDO={xml:c.xml,idx:i,item:JSON.parse(JSON.stringify(x)),
    rejected:c.ins_rejected===undefined?null:JSON.parse(JSON.stringify(c.ins_rejected))};
  if(x&&(x.query||'').trim())
    c.ins_rejected=((c.ins_rejected||[]).concat([{type:x.type,start_sec:x.start_sec,query:x.query||''}])).slice(-30);
  c.inserts.splice(i,1);renderInsHost();syncClipLists();saveState();insUndoUI();}
let INS_UNDO=null;   // снимок последнего insDel: {xml, idx, item, rejected} — между F5 не хранится
function insUndoUI(){const b=$('insUndoBtn');if(b)b.disabled=!INS_UNDO;}
function insUndo(){
  const u=INS_UNDO;if(!u)return;
  // Клип ищем ПО XML, а не по индексу: за время от удаления до отката список мог
  // перестроиться (клипы удаляются, curIns меняется) — индекс бы указывал на чужого.
  const c=CLIPS.find(v=>v&&v.xml===u.xml);
  if(!c){INS_UNDO=null;insUndoUI();return;}
  if(!Array.isArray(c.inserts))c.inserts=[];
  c.inserts.splice(Math.min(u.idx,c.inserts.length),0,u.item);
  if(u.rejected===null)delete c.ins_rejected; else c.ins_rejected=u.rejected;
  if(curIns>=0&&CLIPS[curIns]===c)renderInsHost();
  syncClipLists();saveState();INS_UNDO=null;insUndoUI();
  uiLog(t('вставка возвращена: {i} ({n})',{i:u.idx+1,n:c.name}));}
// Две кнопки генерации — по одной на личную приписку из профиля спикера («1» и «2»).
// У фото и видео конфиги разные, но смысл одинаков: цифра — не декор, а выбор слота;
// сама приписка видна в тултипе и финальный промпт собирает сервер.
function insGenBtns(i,x){
  const c=(curIns>=0&&CLIPS[curIns])?CLIPS[curIns]:null;
  const spkKey=(c&&c.job&&c.job.speaker)||(val('speaker')||'').trim()||'';
  const video=x.type==='video',ips=video?videoPrompts(spkKey):imgPrompts(spkKey);
  // у вставки с галкой «на подложке» кнопки 1/2 шлют слоты pa/pb: в тултипе
  // должна быть видна ТА приписка, которая реально уйдёт в генерацию
  const keys=(!video&&x.plate)?['pa','pb']:['a','b'];
  const action=video?'insGenVideo':'insGenOne',kind=video?t('видео'):t('картинку');
  return keys.map((s,n)=>{
    const ex=(ips[s].extra||'').trim();
    return '<button class="sm" onclick="'+action+'('+i+',\''+s+'\')"'
      +' aria-label="'+t('Сгенерить {kind} промптом {n}',{kind:kind,n:n+1})+'"'
      +' data-t="'+t('Сгенерить {kind} по запросу промптом {n}.',{kind:kind,n:n+1})
      +t(' Приписка: ')+(ex?esc(ex):t('нет — генерим предмет как есть'))+'"'
      +(x.genBusy?' disabled':'')+'>'
      +(x.genBusy?'…':(ico('ai','gold')+'<span class="mono">'+(n+1)+'</span>'))+'</button>';}).join('');}
function renderInsHost(){const host=$('insHost');if(!host)return;host.innerHTML='';
  const gb=$('insGenBtn');if(gb)gb.style.display=imgGenOn()?'':'none';
  if(curIns<0)return;
  const arr=CLIPS[curIns].inserts||[];
  if(!arr.length){host.innerHTML='<div class="empty">'+t('Вставок нет — нажми «Подобрать заново (все)» или добавь файл вручную.')+'</div>';ipvMarks();ipvRefresh();return;}
  arr.forEach((x,i)=>{const vid=x.type==='video';const card=document.createElement('div');
    const fname=(x.media||'').replace(/^.*[\\\/]/,'');
    insScrubInit(x);                                  // скрабберы тянут значение объекта
    // зелёная рамка — только у файла, выбранного ОСОЗНАННО: автоподбор из базы и
    // генерация её не получают (у них свои теги «авто»/«ген»), иначе «зелёное = проверено»
    // теряет смысл — весь список загорался сам собой сразу после разметки
    const picked=fname&&!x.libAuto&&!x.genAuto;
    card.className='inscard '+(vid?'tvideo':'tphoto')+(picked?' chosen':'');
    card.innerHTML='<div class="top">'
      +'<span class="badge '+(vid?'video':'photo')+'" style="cursor:pointer" tabindex="0" role="button" aria-label="'+t('Переключить фото/видео')+'" data-t="'+t('Клик — переключить фото/видео')+'" onclick="insToggleType('+i+')">'+ico(vid?'film':'img','gold')+(vid?t('ВИДЕО'):t('ФОТО'))+'</span>'
      +'<span class="mono muted" style="min-width:70px">'+fmtIns(x.start_sec)+' ('+(Math.round((x.duration_sec||2)*10)/10)+t('с')+')</span>'
      +scrubXY('CLIPS[curIns].inserts['+i+']',x.x,x.y,'ipvRefresh()','saveState()')
      +scrubScale('CLIPS[curIns].inserts['+i+']',x.sc,'ipvRefresh()','saveState()')                // крупность (и от неё — анимация)
      +(vid?scrubSin('CLIPS[curIns].inserts['+i+']',x.sin,'ipvRefresh()','saveState()')            // у видео — откуда играть файл
           :((CURSTYLE&&(CURSTYLE.insert_fx||'card')==='card')                                                // у фото — форма маски, но только при «card» (дефолт как в сборке)
             ?scrubMask('CLIPS[curIns].inserts['+i+']',x.mw,x.mh,'ipvRefresh()','saveState()'):''))      +'<label class="chk" style="display:flex;gap:6px;align-items:center;margin:0"><input type="checkbox" '+(x.mosaic?'checked':'')+' onchange="CLIPS[curIns].inserts['+i+'].mosaic=this.checked;this.blur();saveState();ipvRefresh()"> mosaic</label>'
      // галка «на подложке» — рядом с mosaic, тот же путь сохранения: вставка
      // едет на картинке-подложке из стиля, фон с фото снимается, промпт — свой (pa/pb)
      +'<label class="chk" style="display:flex;gap:6px;align-items:center;margin:0" data-t="'+t('вставка встаёт на картинку-подложку из стиля («Подложка (файл)»), фон с фото снимается, а промпт генерации берётся из «Подложка: приписка к промпту 1/2» профиля спикера')+'"><input type="checkbox" '+(x.plate?'checked':'')+' onchange="CLIPS[curIns].inserts['+i+'].plate=this.checked;this.blur();saveState();ipvRefresh()"> '+t('на подложке')+'</label>'
      +'<span class="grow"></span><span class="del" tabindex="0" role="button" aria-label="'+t('Удалить вставку')+'" data-t="'+t('Удалить вставку целиком')+'" onclick="insDel('+i+')">'+ico('x')+'</span></div>'
      +'<div style="display:flex;gap:8px;align-items:center"><input class="qedit" data-noi18n value="'+esc(x.query||'')+'" placeholder="'+t('что искать в базе / генерить — по-английски, 2–4 слова')+'"'
      +' aria-label="'+t('Описание вставки')+'" data-t="'+t('Описание для подбора и генерации')+'"'
      +' onchange="insQuery('+i+',this.value)" onkeydown="if(event.key===\'Enter\')this.blur()"></div>'
      +(x.prompt?'<div class="muted" data-noi18n style="margin-top:4px;font-size:12px">'+esc(x.prompt)+'</div>':'')
      +'<div style="margin-top:6px;display:flex;gap:8px;align-items:center"><button class="sm" onclick="insPick('+i+')">'+t('Выбрать файл…')+'</button>'
      +'<button class="sm" onclick="insLibFor('+i+')" aria-label="'+t('Варианты из базы вставок')+'" data-t="'+t('Варианты из базы вставок')+'">'+ico('book')+'</button>'
      +((vid?vidGenOn():imgGenOn())?insGenBtns(i,x):'')
      +(!vid&&fname?'<button class="sm" onclick="insRembg('+i+')" data-t="'+t('Убрать фон у этого файла (как Remove Background в фотошопе) — рядом ляжет прозрачный -nobg.png')+'"'+(x.genBusy?' disabled':'')+'>'+t('Фон долой')+'</button>':'')
      +'<button class="sm" onclick="insCopy('+i+')" data-t="'+t('Скопировать поисковый запрос')+'">'+t('Копировать')+'</button>'
      +'<span class="'+(fname?'ok':'muted')+'" style="font-size:12px"'
        +(!fname&&x.noAuto?' data-t="'+t('Файл убрали крестиком — автоподбор и автогенерация сюда больше не лезут. Поставь файл сам: варианты из базы, «Выбрать файл…» или генерация.')+'"':'')
        +'>'+(fname?(ico('check')+' <span data-noi18n>'+esc(fname)+'</span>'):t('файл не выбран'))+'</span>'
      +(fname?'<span class="del" tabindex="0" role="button" aria-label="'+t('Убрать файл')+'" onclick="insClearMedia('+i+')" data-t="'+t('Убрать файл (вставка останется)')+'">'+ico('x')+'</span>':'')
      +(x.libAuto?'<span class="tag on" data-t="'+t('Файл подобран из базы автоматически — проверь, замени или выбери файл вручную')+'">'+ico('book')+t('авто')+'</span>':'')
      +(x.genAuto?'<span class="tag on" data-t="'+t(vid?'Ролик сгенерирован ИИ и будет перенесён в базу при сборке':'Картинка сгенерирована ИИ (Nano Banana) и добавлена в базу вставок')+'">'+ico('ai')+t('ген')+'</span>':'')+'</div>'
      +(x.libShown?insLibRow(x,i):'');
    host.appendChild(card);});
  ipvMarks();ipvRefresh();}
function insLibRow(x,i){const opts=x.libOpts||[];
  return '<div class="librow">'+opts.map((o,j)=>{
    const img=isPhotoPath(o.path);
    const cur=(x.media===o.path);
    return '<div class="libopt'+(cur?' cur':'')+'" tabindex="0" role="button" onclick="insLibPick('+i+','+j+')" title="'+esc(o.path)+' · score '+o.score+'">'
      +(img?('<img loading="lazy" src="/api/media?path='+encodeURIComponent(o.path)+'">')
           :('<span class="vph">'+ico('film','gold')+'</span>'))
      +'<span class="nm" data-noi18n>'+esc(o.name)+'</span><span class="sc">'+Math.round(o.score*100)+'%'+(o.used?' · '+o.used+'×':'')+'</span></div>';
  }).join('')+'</div>';}
function insCopy(i){const q=CLIPS[curIns].inserts[i].query||'';navigator.clipboard.writeText(q).then(()=>{$('insres').className='ok';$('insres').textContent=t('скопировано');});}
// тип вставки ВСЕГДА по расширению файла: он решает в AE всё (фото = стоп-кадр с
// наездом, видео = футаж с переходом и whoosh), а файл может приехать любой — руками
// через «Выбрать файл…» или из вкладки AE. Список расширений — как IMG_EXT в insertlib.py.
function insKind(p){p=(p||'').trim();if(!p)return '';
  return isPhotoPath(p)?'photo':'video';}
// какой тип у базы ЗАПРАШИВАТЬ в АВТОподборе: пожелание, а не требование (в match_many
// это +0.05 к score) — заметно более подходящее видео пусть едет вместо фото. Тип может
// отсутствовать в старых сохранённых состояниях — тогда считаем фото (как в insGenBatch).
// Ручная выдача 📚 тип НЕ передаёт: там юзер смотрит глазами и решает сам.
function insWant(x){return (x&&x.type==='video')?'video':'photo';}
function insSetMedia(x,p){x.media=p||'';const k=insKind(p);if(k)x.type=k;
  if(p)x.noAuto=false;}          // файл снова есть — запрет на автоподбор больше не нужен
// Ссылка VJOB -> вставка не может быть индексом: карточки сортируются, редактор
// переключает клипы, а F5 восстанавливает их заново. Opaque-token хранится в самой
// вставке и уходит серверу только заголовком, чтобы status вернул его вместе с key.
let INSVIDSEQ=0;
function insVideoTarget(c,x){if(!x.video_target){const rnd=(window.crypto&&crypto.randomUUID)
  ?crypto.randomUUID():((Date.now().toString(36))+'_'+(++INSVIDSEQ));
  x.video_target='iv_'+rnd;}
  return {xml:c.xml||'',token:x.video_target};}
function insVideoFindTarget(target){if(!target||!target.token)return null;
  const c=CLIPS.find(v=>v&&v.xml===target.xml);
  const x=c&&(c.inserts||[]).find(v=>v&&v.video_target===target.token);
  return x?{c,x}:null;}
function insVideoClearLink(x){delete x.video_job;delete x.video_slot;delete x.video_target;}
function insVideoJobMatches(x,key){return !!(x&&x.video_job&&(x.video_job==='pending'||(key&&x.video_job===key)));}
function insVideoBindJob(target,key){const found=insVideoFindTarget(target);if(!found)return;
  found.x.video_job=key||'pending';found.x.genBusy=true;saveState();renderInsHost();}
function insVideoApplyResult(target,res,slot){const found=insVideoFindTarget(target);if(!found)return;
  const x=found.x;insSetMedia(x,res.path);x.genAuto=true;x.libAuto=false;x.libOpts=null;x.noAuto=false;
  uiLog(t('✨ сгенерено (промпт {p})',{p:(slot==='b'?'2':'1')})+': '+res.path.replace(/^.*[\\\/]/,''));}
function insVideoSettle(target,d){const found=insVideoFindTarget(target);if(!found)return;
  const x=found.x;x.genBusy=false;
  // При локальном обрыве status ключ оставляем: следующий F5 снова спросит VJOB и
  // доставит уже готовый оплаченный файл, вместо второго платного запуска.
  if(!(d&&d.transport))insVideoClearLink(x);
  saveState();syncClipLists();renderInsHost();}
function insVideoContext(target,slot){return {type:'insert',token:target.token,
  doneText:t('Готово — видео добавлено во вставку'),
  onStart:d=>insVideoBindJob(target,d.key),
  onResult:res=>insVideoApplyResult(target,res,slot),
  onError:d=>toast('⚠ '+errText(d)),
  onCancel:d=>toast(errText(d)||t('Генерация видео остановлена')),
  onSettled:d=>insVideoSettle(target,d),
};}
// Восстановление F5: status возвращает и key, и server-side token. Сначала ищем
// токен (он переживает даже reload между POST и его ответом), key — compatibility
// fallback для ещё незавершённого состояния. После settle связь очищается, поэтому
// старый terminal status не может повторно положить результат. Никаких curIns/индексов здесь нет.
function insVideoContextForJob(key,token){let found=null;
  if(token)for(const c of CLIPS){if(!c)continue;const x=(c.inserts||[]).find(v=>v&&v.video_target===token&&insVideoJobMatches(v,key));
    if(x){found={c,x};break;}}
  if(!found&&key)for(const c of CLIPS){if(!c)continue;const x=(c.inserts||[]).find(v=>v&&v.video_job===key);
    if(x){found={c,x};break;}}
  if(!found)return null;
  const target=insVideoTarget(found.c,found.x);found.x.video_job=key||found.x.video_job||'pending';
  found.x.genBusy=true;saveState();renderInsHost();
  return insVideoContext(target,found.x.video_slot||'a');}
function insVideoForgetPending(){let changed=false;
  for(const c of CLIPS)for(const x of (c.inserts||[]))if(x&&x.video_job==='pending'){
    insVideoClearLink(x);x.genBusy=false;changed=true;}
  if(changed){saveState();renderInsHost();}}
// Видео-карточка использует тот же VJOB и pollVideo, что отдельная вкладка. Нельзя
// заводить свой poller: один из них увидит done первым, второй не снимет genBusy.
async function insGenVideo(i,slot){if(curIns<0)return;const c=CLIPS[curIns],x=c&&c.inserts[i];if(!x)return;
  if(!(x.query||'').trim()){toast(t('У вставки нет запроса — нечего генерить'));return;}
  if(x.genBusy)return;
  if(x.video_job){toast(t('Статус прошлого видео-запуска ещё не получен'));return;}
  const actualSlot=(slot==='b'?'b':'a'),target=insVideoTarget(c,x);
  // Mark before fetch: F5 между отправкой POST и ответом всё равно знает карточку.
  x.video_job='pending';x.video_slot=actualSlot;x.genBusy=true;saveState();renderInsHost();
  const speaker=(c&&c.job&&c.job.speaker)||(val('speaker')||'').trim()||undefined;
  logReset();progShow(t('Генерация видео'),t('отправляю запрос…'));
  try{await videoStart({query:x.query,slot:(slot==='b'?'b':'a'),speaker:speaker,
    insert_duration:x.duration_sec,xml:c.xml},insVideoContext(target,actualSlot));}
  catch(e){const found=insVideoFindTarget(target);if(found){found.x.genBusy=false;
      // JSON error means VJOB did not start. A transport error is ambiguous, so the
      // token stays persisted and boot can reattach rather than risk charging twice.
      if(e.data)insVideoClearLink(found.x);
      saveState();syncClipLists();renderInsHost();}
    hideProg();toast('⚠ '+(e.message||e));}
}
// Форма маски, с которой файл в прошлый раз ушёл в проект (её отдаёт база вместе с путём).
// Без этого каждая картинка из базы приезжала с кропом 100/100, и одну и ту же вставку
// приходилось подгонять скрабберами заново в каждом ролике. Только фото: у видео маски нет.
function insApplyCrop(x,o){if(!o||insKind(o.path)!=='photo')return;
  if(o.mw)x.mw=o.mw;
  if(o.mh)x.mh=o.mh;}
async function insPick(i){try{const d=await (await fetch('/api/pickmedia')).json();if(d.path){const x=CLIPS[curIns].inserts[i];insSetMedia(x,d.path);x.libAuto=false;x.libOpts=null;renderInsHost();syncClipLists();saveState();}}
  catch(e){toast(t('Не открылся выбор файла — сервер не ответил'));uiLog(t('pickmedia: ')+e);}}

// ---- база вставок: скан прошлых проектов + автоподбор файла под ИИ-запрос ----
function illCfg(){try{return JSON.parse(localStorage.getItem('reelsi_inslib')||'{}');}catch(e){return {};}}
function illSaveCfg(){const c=illCfg();c.dirs=val('illdirs');c.auto=$('illauto').checked;
  c.gen=$('illgen')?$('illgen').checked:false;    // генерить недостающие прямо при разметке (платно)
  localStorage.setItem('reelsi_inslib',JSON.stringify(c));}
async function openInsLib(){openModal('mbInsLib');const c=illCfg();
  if(c.dirs!=null)$('illdirs').value=c.dirs;if(c.auto!=null)$('illauto').checked=!!c.auto;
  if($('illgen')){$('illgen').checked=!!c.gen;$('illgenRow').style.display=imgGenOn()?'flex':'none';}
  if(c.src)$('illsrc').value=c.src;if(c.dest)$('illdest').value=c.dest;if(c.since)$('illsince').value=c.since;
  illInfo();illList();illPollDescribe(true);}
async function illInfo(){try{const d=await (await fetch('/api/insertlib_info')).json();
  $('illinfo').textContent=d.built?(t('В базе {n} файлов · эмбеддер: {e} · папки: {p}',{n:d.count,e:(d.emb_model||t('нет (матч по именам)')),p:(d.dirs||[]).join(' · ')})):t('База ещё не построена — укажи папки и нажми «Сканировать».');
  if(d.built&&!val('illdirs').trim())$('illdirs').value=(d.dirs||[]).join('\n');}
  catch(e){uiLog(t('база вставок: статус не прочитан — ')+e);}}
async function pickdirInto(id){try{const d=await (await fetch('/api/pickdir')).json();
  if(d.path){const el=$(id);el.value=(el.value.trim()?el.value.trim()+'\n':'')+d.path;illSaveCfg();}}
  catch(e){toast(t('Не открылся выбор папки — сервер не ответил'));uiLog(t('pickdir: ')+e);}}
async function illScan(){const dirs=val('illdirs').split('\n').map(s=>s.trim()).filter(Boolean);
  if(!dirs.length){toast(t('Укажи папки'));return;}
  illSaveCfg();const btn=$('illscan');btn.disabled=true;$('illres').className='muted';$('illres').textContent=t('сканирую…');$('illlog').textContent='';
  try{const d=await (await fetch('/api/insertlib_scan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dirs})})).json();
    $('illlog').textContent=(d.log||[]).map(fmtLog).join('\n');
    if(d.error){$('illres').className='err';$('illres').textContent='⚠ '+errText(d);return;}
    $('illres').className='ok';$('illres').textContent=t('{n} файлов',{n:d.count})+(d.emb?'':t(' (без эмбеддера)'));
    uiLog(t('база вставок: ')+d.count+t(' файлов')+(d.emb?t(' + эмбеддинги'):''));illInfo();
  }catch(e){$('illres').className='err';$('illres').textContent='⚠ '+e;}
  finally{btn.disabled=false;}}
// автоподбор: для вставок с query и без файла спросить базу, opts[0].auto -> проставить media
// подбор из базы для СПИСКА вставок (один batch-запрос): всем заполняем libOpts,
// а файл ставим только тем, у кого его нет — выбранное руками не затираем.
async function insLibFill(arr,speaker){
  arr=(arr||[]).filter(x=>(x.query||'').trim());
  if(!arr.length)return 0;
  let d;try{d=await (await fetch('/api/insertlib_match',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({queries:arr.map(x=>({q:x.query,type:insWant(x)})),k:5,speaker:speaker||undefined})})).json();}catch(e){return 0;}
  if(d.error||!Array.isArray(d.results))return 0;
  let n=0;
  // noAuto (юзер снял картинку крестиком) — варианты для 📚 всё равно заполняем,
  // но САМИ файл не ставим: раз убрал, значит не надо, и вернуть его должен он сам.
  arr.forEach((x,j)=>{const opts=d.results[j]||[];x.libOpts=opts;
    if(!x.media&&!x.noAuto&&opts.length&&opts[0].auto){
      insSetMedia(x,opts[0].path);insApplyCrop(x,opts[0]);x.libAuto=true;n++;}});
  return n;}
async function insLibAuto(c){
  const spkKey=(c&&c.job&&c.job.speaker)||(val('speaker')||'').trim()||undefined;
  const n=await insLibFill((c.inserts||[]).filter(x=>!x.media),spkKey);
  if(n)uiLog(t('база вставок: автоподобрано {n} файлов',{n:n}));
  return n;}
// правка описания прямо в карточке: это же описание идёт и в подбор из базы, и в
// генерацию. После правки сразу перематчиваем из базы — но НЕ трогаем файл, выбранный
// руками или сгенерённый (за него плачено): его сперва убери крестиком.
async function insQuery(i,v){if(curIns<0)return;const x=CLIPS[curIns].inserts[i];if(!x)return;
  const q=(v||'').trim();
  if(q===(x.query||''))return;
  x.query=q;x.libOpts=null;x.libShown=false;         // старые варианты — не про этот запрос
  const auto=illCfg().auto!==false;
  const spkKey=(CLIPS[curIns]&&CLIPS[curIns].job&&CLIPS[curIns].job.speaker)||(val('speaker')||'').trim()||undefined;
  if(auto&&x.libAuto){x.media='';x.libAuto=false;}   // старый автофайл был под старое описание
  if(q&&!x.media&&auto)await insLibFill([x],spkKey); // ...и сразу ищем под новое
  renderInsHost();syncClipLists();saveState();
  uiLog(t('описание вставки: «{q}»',{q:q})+(x.media?t(' → {f}',{f:x.media.replace(/^.*[\\\/]/,'')}):''));}
// «этот файл не под этот запрос»: автоподбор его больше не предложит (руками через 📚 —
// пожалуйста). Бракуем пару файл+запрос, а не файл целиком: под другую тему он ещё сгодится.
async function insRejectMedia(media,query){
  if(!media||!(query||'').trim())return false;
  try{const d=await (await fetch('/api/insertlib_reject',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({path:media,query})})).json();
    if(d.ok)uiLog(t('в базе забраковано под «{q}»: {f}',{q:query,f:media.replace(/^.*[\\\/]/,'')}));
    return !!d.ok;}
  catch(e){return false;}}
// крестик у имени файла: убрать ТОЛЬКО картинку, вставка (тайминг/описание) остаётся.
// Ставим noAuto: следующий проход автоподбора/автогенерации эту вставку не трогает.
// Иначе получалось издевательство — снял картинку, добрал вставки ИИ (или поправил
// описание), и ровно она же приехала обратно: в базе мы её НЕ бракуем, значит она снова
// первая по score. Руками (📚 / «Выбрать файл…» / ✨) поставить можно всегда — там флаг
// снимается, это осознанный выбор юзера.
// В базе НИЧЕГО не бракуем (2026-07-22): раньше снятие автоподбора само писало файлу
// «не подходит под этот запрос», и юзер, который просто чистил вставку (или сносил её
// целиком), молча портил базу — брак всплывал потом, в других роликах. Пометка «не та
// картинка» осталась только там, где это сказано явно: ✨ перегенерация поверх (insGenOne).
async function insClearMedia(i){if(curIns<0)return;const x=CLIPS[curIns].inserts[i];
  if(!x||!x.media)return;
  uiLog(t('убрана картинка: {f} (в базе не бракуем, автоподбор сюда больше не лезет)',{f:x.media.replace(/^.*[\\\/]/,'')}));
  x.media='';x.libAuto=false;x.genAuto=false;x.libOpts=null;x.noAuto=true;
  renderInsHost();syncClipLists();saveState();}
// кнопка 📚 на карточке: показать топ-варианты из базы (клик по варианту = выбрать)
async function insLibFor(i){const x=CLIPS[curIns].inserts[i];if(!x)return;
  if(x.libShown){x.libShown=false;renderInsHost();return;}
  if(!x.libOpts){const q=(x.query||'').trim()||((x.media||'').replace(/^.*[\\\/]/,''));
    if(!q){toast(t('У вставки нет запроса — нечем искать'));return;}
    const spkKey=(CLIPS[curIns]&&CLIPS[curIns].job&&CLIPS[curIns].job.speaker)||(val('speaker')||'').trim()||undefined;
    try{const d=await (await fetch('/api/insertlib_match',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({queries:[{q}],k:8,speaker:spkKey||undefined})})).json();      // без типа: показываем всё, что похоже
      x.libOpts=(d.results&&d.results[0])||[];}catch(e){x.libOpts=[];}}
  if(!x.libOpts.length){toast(t('База пуста или нет совпадений — открой «База» и просканируй папки'));return;}
  x.libShown=true;renderInsHost();}
function insLibPick(i,j){const x=CLIPS[curIns].inserts[i];const o=(x.libOpts||[])[j];if(!o)return;
  insSetMedia(x,o.path);insApplyCrop(x,o);x.libAuto=false;x.libShown=false;
  renderInsHost();syncClipLists();saveState();}
// импорт новых файлов в свою папку базы
async function illImport(){const src=val('illsrc').trim(),dest=val('illdest').trim(),since=val('illsince');
  if(!src||!dest){toast(t('Укажи источник и папку базы'));return;}
  const c=illCfg();c.src=src;c.dest=dest;c.since=since;localStorage.setItem('reelsi_inslib',JSON.stringify(c));
  if(!await askConfirm(t('Перенести (не скопировать) вставки из\n{src}\nс {since} в\n{dest}\n\nСтарые XML-проекты, ссылающиеся на эти файлы, потеряют пути (лог переноса сохранится).',{src:src,dest:dest,since:since})))return;
  $('illres').className='muted';$('illres').textContent=t('переношу…');
  try{const d=await (await fetch('/api/insertlib_import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dirs:[src],dest,since})})).json();
    $('illlog').textContent=(d.log||[]).map(fmtLog).join('\n');
    if(d.error){$('illres').className='err';$('illres').textContent='⚠ '+errText(d);return;}
    $('illres').className='ok';$('illres').textContent=t('перенесено {n}',{n:d.count});
    uiLog(t('база вставок: импорт {n} файлов в {d}',{n:d.count,d:dest}));
    // добавить папку базы в скан-лист и пересканировать
    const dirs=val('illdirs');if(!dirs.includes(dest))$('illdirs').value=(dirs.trim()?dirs.trim()+'\n':'')+dest;
    illSaveCfg();await illScan();illList();
  }catch(e){$('illres').className='err';$('illres').textContent='⚠ '+e;}}
// vision-описания (фоновый джоб + поллинг)
let ILLPOLL=0;
async function illDescribe(){
  try{const d=await (await fetch('/api/insertlib_describe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({only_missing:true})})).json();
    if(d.error){$('illres').className='err';$('illres').textContent='⚠ '+errText(d);return;}
    $('illres').className='muted';$('illres').textContent=t('описываю…');$('illdesc').disabled=true;
    illPollDescribe();
  }catch(e){$('illres').className='err';$('illres').textContent='⚠ '+e;}}
// Хвост лога, а не весь: описание полутора тысяч файлов — это столько же строк, и
// опрос раз в 1.5с тянул их целиком каждый раз. Сервер умеет ?since — пользуемся,
// как основной лог. Показываем всё равно последние 25.
let ILLLOG=[],ILLSINCE=0;
async function illStatus(){
  const d=await (await fetch('/api/insertlib_describe_status?since='+ILLSINCE)).json();
  if(!d.log_total||d.log_total<ILLSINCE)ILLLOG=[],ILLSINCE=0;   // джоб перезапустили — лог начался заново
  if((d.log||[]).length){ILLLOG=ILLLOG.concat(d.log).slice(-25);ILLSINCE=d.log_total;}
  return d;}
function illLogText(){return ILLLOG.map(fmtLog).join('\n');}
async function illPollDescribe(passive){clearTimeout(ILLPOLL);
  let d;try{d=await illStatus();}catch(e){return;}
  if(!$('mbInsLib').classList.contains('on'))return;   // модалку закрыли — не дёргаем DOM
  if(d.running){
    $('illdesc').disabled=true;
    $('illres').className='muted';$('illres').textContent=t('описано {a} / {b}',{a:d.done,b:(d.total||'…')});
    $('illlog').textContent=illLogText();const l=$('illlog');l.scrollTop=l.scrollHeight;
    ILLPOLL=setTimeout(illPollDescribe,1500);return;}
  $('illdesc').disabled=false;
  if(!passive){ // джоб только что закончился
    $('illres').className=d.error?'err':'ok';
    $('illres').textContent=d.error?('⚠ '+errText(d)):t('описано {n}',{n:d.done});
    $('illlog').textContent=illLogText();
    illInfo();illList();}}
// «📚 Обновить базу» в шапке: пересканировать папки индекса + описать новые файлы через ИИ.
// Прогресс — в самой кнопке; работает с любого шага, ничего открывать не надо.
let ILLHDRPOLL=0;
async function illRefresh(){
  const st=await illStatus().catch(()=>({}));
  if(st.running){toast(t('База уже обновляется — {a} / {b}',{a:st.done,b:(st.total||'…')}));illHdrPoll();return;}
  let dirs=[];try{const inf=await (await fetch('/api/insertlib_info')).json();dirs=inf.dirs||[];}catch(e){}
  if(!dirs.length)dirs=(illCfg().dirs||'').split('\n').map(s=>s.trim()).filter(Boolean);
  if(!dirs.length){toast(t('База не настроена — открой «База» в окне вставок и укажи папки'));return;}
  $('illhdrtxt').textContent=t(' скан…');uiLog(t('база вставок: пересканирую {n} папок…',{n:dirs.length}));
  try{const d=await (await fetch('/api/insertlib_scan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dirs})})).json();
    if(d.error){toast('⚠ '+errText(d));$('illhdrtxt').textContent='';return;}
    uiLog(t('база вставок: в индексе {n} файлов, запускаю описание новых…',{n:d.count}));
    const r=await (await fetch('/api/insertlib_describe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({only_missing:true})})).json();
    if(r.error){toast('⚠ '+errText(r));$('illhdrtxt').textContent='';return;}
    illHdrPoll();
  }catch(e){toast('⚠ '+e);$('illhdrtxt').textContent='';}}
async function illHdrPoll(){clearTimeout(ILLHDRPOLL);
  let d;try{d=await illStatus();}catch(e){$('illhdrtxt').textContent='';return;}
  if(d.running){$('illhdrtxt').textContent=' '+d.done+'/'+(d.total||'…');ILLHDRPOLL=setTimeout(illHdrPoll,2500);return;}
  $('illhdrtxt').textContent='';
  if(d.total||d.error){toast(d.error?(t('База: ⚠ ')+errText(d)):t('База обновлена: описано {n}',{n:d.done}));
    uiLog(t('база вставок: ')+(d.error?(t('ошибка ')+d.error):(t('описано ')+d.done)));}}
// список файлов с редактируемыми описаниями
let ILLDEB=0;
function illListDeb(){clearTimeout(ILLDEB);ILLDEB=setTimeout(illList,300);}
async function illList(){
  let d;try{d=await (await fetch('/api/insertlib_items',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({q:val('illq'),limit:60})})).json();}
  catch(e){$('illlist').innerHTML='<div class="empty">'+t('Список не загрузился — сервер не ответил')+'</div>';uiLog(t('insertlib_items: ')+e);return;}
  if(d.error)return;
  $('illcount').textContent=d.total?t('показано {a} из {b}',{a:Math.min(60,d.total),b:d.total}):t('пусто');
  const host=$('illlist');host.innerHTML='';
  if(!(d.items||[]).length){host.innerHTML='<div class="empty">'+(val('illq').trim()
    ?t('Ничего не нашлось по запросу')
    :t('База пуста — укажи папки выше и нажми «Сканировать»'))+'</div>';return;}
  (d.items||[]).forEach(it=>{const img=isPhotoPath(it.path);
    const row=document.createElement('div');row.className='illrow';
    row.innerHTML=(img?('<img loading="lazy" src="/api/media?path='+encodeURIComponent(it.path)+'">')
        :('<span class="vph">'+ico('film','gold')+'</span>'))
      +'<div class="meta"><div class="nm" title="'+esc(it.path)+'">'+esc(it.name)+(it.used?(' <span class="muted">· '+it.used+'×</span>'):'')+'</div>'
      +'<input class="desc" value="'+esc(it.desc)+'" aria-label="'+t('Описание файла')+'" data-t="'+t('Описание для поиска — правь свободно; Enter/клик мимо = сохранить')+'"></div>'
      +'<span class="tag'+(it.desc_src==='ai'?' on':'')+'">'+(it.desc_src==='ai'?t('ИИ'):it.desc_src==='user'?t('моё'):t('имя'))+'</span>';
    const inp=row.querySelector('.desc');
    inp.onkeydown=(e)=>{if(e.key==='Enter')inp.blur();};
    inp.onchange=async()=>{const r=await (await fetch('/api/insertlib_desc',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:it.path,desc:inp.value})})).json();
      if(r.error){toast('⚠ '+errText(r));return;}
      row.querySelector('.tag').textContent=t('моё');row.querySelector('.tag').classList.add('on');};
    host.appendChild(row);});}

