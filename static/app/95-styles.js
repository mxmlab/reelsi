// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Maxim Si
// пресеты стиля, профили спикеров, пикеры, музыка, движки ASR
//
// Часть интерфейса Reelsi. Файлы static/app/ грузятся ПО ПОРЯДКУ ИМЁН обычными
// <script>-тегами (не модулями): один общий скоуп, как было в едином app.js.
// Порядок важен — объявления функций поднимаются в пределах своего файла.

// ================= styles (ported) =================
let STYLES={},CURSTYLE=null,STYLESAVED='base',FONTS=[];
// Режим правки шаблона (задание AC2): STYLE_EDITING — имя открытого на карандаш шаблона,
// STYLE_EDIT_ORIG — его снимок до правок (для «несохранённые?»), STYLE_TOUCHED — флаг
// «поля трогали» для кастома. Селектор в этом режиме показывает имя + « — правится».
let STYLE_EDITING=null,STYLE_EDIT_ORIG=null,STYLE_TOUCHED=false;
// ---- спикеры: кто в кадре -> пороги нарезки + папка + стиль AE ----
// Пороги в gigaam_cut калибровались на спикере A; у другого спикера другая студия
// и другой голос, и те же цифры режут не там (замеры — в speakers.py). Селектор
// один раз ставит всё, что зависит от спикера, чтобы это не выбиралось руками
// каждый прогон (и не забывалось — из-за чего клипы уезжали в чужую папку).
let SPEAKERS={},SPKSAVED='',SPKDEF={},SPKLAB=[],SPKEDIT='';
// Глобальная папка для .jsx (клипы БЕЗ тега спикера) — отдельно от того, что поле
// показывает у клипа с тегом. Тег определяет папку клипа (профиль спикера), поле —
// вид на неё; глобальное значение хранится здесь, чтобы показ папки спикера не
// затирал папку для клипов без тега (задание N, «клип без спикера — как сегодня»).
let AEGLOBAL='';
// Папка вывода безголового рендера (задание BD, шаг 3). Та же механика, что у
// AEGLOBAL: у клипа со спикером — из его профиля (renderdir), у клипа без тега —
// глобальная; дефолт — папка exp рядом с репозиторием (подставляет сервер).
let AERENDER='';
async function loadSpeakers(){let d;
  try{d=await (await fetch('/api/speakers')).json();}
  catch(e){uiLog(t('loadSpeakers(запрос): ')+e);return;}
  if(!d.ok){uiLog(t('loadSpeakers(ответ): ')+(d.error||t('ответ без ok')));return;}
  SPEAKERS=d.speakers||{};SPKDEF=d.defaults||{};SPKLAB=d.labels||[];
  const sel=$('speaker');if(!sel)return;
  sel.innerHTML='<option value="">'+t('не выбран')+'</option>';
  Object.keys(SPEAKERS).forEach(k=>{const o=document.createElement('option');
    o.value=k;o.textContent=SPEAKERS[k].label||k;sel.appendChild(o);});
  sel.value=SPEAKERS[SPKSAVED]?SPKSAVED:'';
  applySpeakerDirs();
  spkEditUI();renderStyleInfo();cutSummary();jsxDirNote();}
// Папки выбранного спикера — НА ЗАГРУЗКЕ СТРАНИЦЫ, без вопросов.
// Раньше профиль попадал в поля только через onSpeakerChange, то есть в момент СМЕНЫ
// спикера в списке (или сохранения профиля). После F5 спикер восстанавливался, а поля
// оставались с тем, что лежало в сохранённом состоянии, — обычно с базовой папкой,
// подставленной автоматически при первом запуске. Человек правил «Папка для .jsx» в
// профиле, видел там нужный путь, а сборка уезжала в другую папку и молчала об этом
// (жалоба 2026-08-11). Профиль — источник правды; поле его показывает, а подпись под
// полем говорит, откуда значение. Разовая другая папка живёт до перезагрузки.
function applySpeakerDirs(){const p=SPEAKERS[val('speaker')];if(!p)return;
  const set=(id,v)=>{const el=$(id);if(el&&v&&!samePath(el.value,v))el.value=v;};
  set('ai_outdir',(p.outdir||'').trim());
  // Глобальная папка .jsx берёт дефолт из профиля текущего спикера; поле показывает
  // её (клип без тега) или папку тега (клип с тегом) — см. renderAeDirField.
  if((p.jsxdir||'').trim())AEGLOBAL=(p.jsxdir||'').trim();
  // Папка вывода рендера — так же из профиля (задание BD), см. renderRenderDirField.
  if((p.renderdir||'').trim())AERENDER=(p.renderdir||'').trim();
  renderAeDirField();
  // Папки камер — на загрузке страницы, без вопросов. Раньше они подставлялись
  // только через onSpeakerChange, после F5 оставался автоподбор, а очередь хранит
  // только имена — нарезка уходила читать чужую папку (жалоба 2026-08-20).
  for(let k=0;k<nCams();k++)camDirApply(k,'');}
// Карандаш правит ВЫБРАННОГО: без выбора править нечего, и кнопка гаснет, а не молчит.
function spkEditUI(){const b=$('spkedit');if(b)b.disabled=!SPEAKERS[val('speaker')];}
// Папка из профиля не перетирает молча то, что юзер вписал руками: пусто или папка
// другого спикера — ставим сразу, своё — спрашиваем. Правило одно на обе папки
// (результат нарезки и .jsx), поэтому и код один.
// C:/папка и C:\папка — ОДНА папка, но РАЗНЫЕ строки. Нативный диалог выбора отдаёт
// прямые слеши, профиль спикера хранит обратные, и сравнение «как есть» не совпадало
// НИКОГДА: подпись врала «задана вручную» на папке спикера, а spkDir переспрашивал про
// путь, который уже стоит в поле (замечено пользователем 2026-08-11: «выбираю виндой —
// C:/…, закрыл и открыл — C:\…»). Регистр тоже гасим: на Windows пути регистронезависимы.
function samePath(a,b){
  const n=s=>(s||'').trim().replace(/[\\/]+/g,'/').replace(/\/+$/,'').toLowerCase();
  return n(a)===n(b);}
async function spkDir(id,key,ask){const p=SPEAKERS[val('speaker')]||{};
  const want=(p[key]||'').trim();
  // Глобальная папка .jsx живёт в AEGLOBAL, а не в поле: у клипа с тегом поле
  // показывает папку тега, и сравнение «как есть» сравнило бы не то.
  const cur=(id==='aeoutdir')?AEGLOBAL:val(id).trim();
  if(!want||samePath(want,cur))return;
  // Спрашиваем ВСЕГДА при непустой разнице — и когда это папка ДРУГОГО спикера тоже:
  // папки называются AutoCut_out / MKAutoCut_out / NGAutoCut_out / VIKAutoCut_out и
  // частят у всех, раньше «своё» ловило чужое и вручную выставленное значение
  // исчезало без следа (жалоба 2026-08-11 «меняю папку, а файла там нет»).
  if(!cur||await askConfirm(ask+want)){
    if(id==='aeoutdir'){AEGLOBAL=want;renderAeDirField();}
    else{$(id).value=want;}}}
// Папка для .jsx КЛИПА: тег спикера определяет её у клипа (задание N), и каждый
// собирается в свою; без тега или у спикера без jsxdir — null (в сборку уйдёт
// глобальное поле). Профиль — источник, поле интерфейса его показывает.
function effOutdir(c){const j=c&&c.job,spk=(j&&j.speaker)?SPEAKERS[j.speaker]:null;
  return (spk&&(spk.jsxdir||'').trim())?spk.jsxdir.trim():null;}
function openClipSpeaker(){if(curAE<0||!CLIPS[curAE])return null;
  const k=(CLIPS[curAE].job||{}).speaker;return k?SPEAKERS[k]||null:null;}
function setAeDir(v){$('aeoutdir').value=v||'';$('aeoutdir3').value=v||'';jsxDirNote();saveState();}
// Поле показывает то, что сейчас действительно уходит в сборку: у клипа с тегом —
// папку его профиля (пусто = глобальная), у клипа без тега — глобальную. Вызывается,
// когда меняется тег открытого клипа, глобальный спикер или восстановленное состояние.
function renderAeDirField(){const sp=openClipSpeaker();
  if(sp)setAeDir((sp.jsxdir||'').trim()||AEGLOBAL);
  else{$('aeoutdir').value=AEGLOBAL;$('aeoutdir3').value=AEGLOBAL;jsxDirNote();saveState();}
  renderRenderDirField();}
// Папка вывода рендера: та же схема «профиль спикера / глобальная / дефолт exp».
// Дефолт приходит с сервера (/api/render_status.default_dir) — один источник:
// «exp рядом с репозиторием» не копируется в JS.
function renderRenderDirField(){const sp=openClipSpeaker();
  const v=sp?(sp.renderdir||'').trim():'';
  const cur=v||AERENDER;
  if($('aerenderdir'))$('aerenderdir').value=cur;
  if(cur)renderDirNote();}
// Одна папка для .jsx — ДВА поля (шаг 1 и дубль у кнопки сборки на шаге 3), а значение
// одно: правка любого поля видна в обоих. Отдельной переменной нет — источник один.
// Сохранение на каждый ввод — по заданию J, пункт 4: поля папок были голыми инпутами,
// и правка жила только до ближайшего тика flushSave (2.5с) — F5 или квота localStorage
// раньше тика возвращали старое сохранённое значение ровно с applyState при загрузке.
function aeDirSync(el){
  $('aeoutdir').value=el.value;$('aeoutdir3').value=el.value;
  // У клипа с тегом папка — производная от тега (профиль спикера), глобальную папку
  // при вводе не трогаем; «закрепить» правку — это запись в профиль (aeDirCommit).
  const sp=openClipSpeaker();
  if(!sp){AEGLOBAL=el.value;saveState();}
  jsxDirNote();}
// Правка папки на шаге 3 «закрепилась» (blur/выбор): у клипа с тегом это правка
// сохранённого профиля спикера — с подтверждением, она повлияет на все его будущие
// клипы. Отказ — поле возвращается к папке профиля. Без тега — глобальная папка.
async function aeDirCommit(el){const sp=openClipSpeaker();
  if(!sp){AEGLOBAL=el.value;saveState();jsxDirNote();return;}
  if(samePath(el.value,(sp.jsxdir||'').trim()))return;
  if(await askConfirm(t('Папка для .jsx этого клипа — из профиля спикера «{n}». Сохранить новую папку в его профиль? Это повлияет на все его будущие клипы.',{n:sp.label||''})))
    saveSpeakerJsxdir(sp,el.value);
  else renderAeDirField();}
async function saveSpeakerJsxdir(sp,dir){
  const data=JSON.parse(JSON.stringify(sp));data.jsxdir=dir.trim();
  let d;
  try{d=await (await fetch('/api/savespeaker',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:data.label,data})})).json();}
  catch(e){toast(t('Папка не сохранена — сервер не ответил'));uiLog(t('savespeaker(jsxdir): ')+e);renderAeDirField();return;}
  if(!d.ok){toast(errText(d)||t('Папка не сохранена'));renderAeDirField();return;}
  SPEAKERS[d.key]=data;
  setAeDir(data.jsxdir);
  uiLog(t('папка спикера «{n}» обновлена: ')+data.jsxdir);}
// Подпись «откуда папка»: у клипа с тегом — его профиль; без тега — профиль
// глобального спикера или ручная (задание J, пункт 2). Раньше человек видел путь
// и не знал, его это значение или подставленное.
function jsxDirNote(){const sp=openClipSpeaker();
  let note='';
  if(sp){
    note=t('папка спикера «{n}» — правка сохранится в его профиле',{n:sp.label||''});
  }else{
    const p=(SPEAKERS[val('speaker')]||{}).jsxdir||'';
    const v=AEGLOBAL;
    note=(p&&v&&samePath(p,v))?t('папка спикера «{n}»',{n:(SPEAKERS[val('speaker')]||{}).label||''}):(v?t('задана вручную'):'');
  }
  ['aeoutdirnote','aeoutdir3note'].forEach(id=>{const el=$(id);if(el)el.textContent=note;});}
// Папка вывода рендера — как jsxdir: правка поля синхронится в AERENDER (клип без
// тега) и «закрепляется» в профиль спикера по blur/выбору (renderDirCommit).
function renderDirSync(el){const sp=openClipSpeaker();
  if(!sp)AERENDER=el.value;
  renderDirNote();}
async function renderDirCommit(el){const sp=openClipSpeaker();
  if(!sp){AERENDER=el.value;renderDirNote();saveState();return;}
  if(samePath(el.value,(sp.renderdir||'').trim()))return;
  if(await askConfirm(t('Папка вывода рендера этого клипа — из профиля спикера «{n}». Сохранить новую папку в его профиль? Это повлияет на все его будущие клипы.',{n:sp.label||''})))
    saveSpeakerRenderdir(sp,el.value);
  else renderRenderDirField();}
async function saveSpeakerRenderdir(sp,dir){
  const data=JSON.parse(JSON.stringify(sp));data.renderdir=dir.trim();
  let d;
  try{d=await (await fetch('/api/savespeaker',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:data.label,data})})).json();}
  catch(e){toast(t('Папка не сохранена — сервер не ответил'));uiLog(t('savespeaker(renderdir): ')+e);renderRenderDirField();return;}
  if(!d.ok){toast(errText(d)||t('Папка не сохранена'));renderRenderDirField();return;}
  SPEAKERS[d.key]=data;
  if($('aerenderdir'))$('aerenderdir').value=data.renderdir||'';
  uiLog(t('папка спикера «{n}» обновлена: ')+data.renderdir);}
function renderDirNote(){const sp=openClipSpeaker();
  const el=$('aerenderdirnote');if(!el)return;
  let note='';
  if(sp)note=t('папка спикера «{n}» — правка сохранится в его профиле',{n:sp.label||''});
  else{const p=(SPEAKERS[val('speaker')]||{}).renderdir||'';
    const v=AERENDER;
    note=(p&&v&&samePath(p,v))?t('папка спикера «{n}»',{n:(SPEAKERS[val('speaker')]||{}).label||''}):(v?t('задана вручную'):'');}
  el.textContent=note;}
// Папки камер из профиля (задание U): camdirs[k] подставляется при выборе спикера.
// Механика — как spkDir: пусто или совпадает — молча; папку, поставленную руками
// (CAMFROM === 'user'), не перетираем без подтверждения. После подстановки перечитываем
// файлы камеры — иначе селекты останутся от старой папки.
async function camDirApply(k,ask){
  const p=SPEAKERS[val('speaker')]||{};
  const want=((p.camdirs||[])[k]||'').trim();
  if(!want||samePath(want,CAMDIRS[k]||''))return;
  if(ask&&CAMFROM[k]==='user'&&!await askConfirm(ask+want))return;
  CAMDIRS[k]=want;CAMFROM[k]='spk';
  const el=$('camdir'+k);if(el)el.value=want;
  reloadCamFiles(k);}
// Ручная смена папки камеры при выбранном спикере — правка его профиля: спрашиваем
// «сохранить?» (как aeDirCommit). Отказ — папка стоит на эту сессию, профиль не тронут.
async function camDirCommit(k){
  const p=SPEAKERS[val('speaker')];if(!p)return;
  const cur=(CAMDIRS[k]||'').trim(),want=((p.camdirs||[])[k]||'').trim();
  if(!cur||samePath(cur,want))return;
  if(await askConfirm(t('Папка камеры {n} этого спикера — из его профиля. Сохранить новую папку в профиль? Это повлияет на все его будущие клипы.',{n:k+1})))
    saveSpeakerCamdirs(k,cur);}
async function saveSpeakerCamdirs(k,dir){
  const sp=JSON.parse(JSON.stringify(SPEAKERS[val('speaker')]));
  const cds=(sp.camdirs||[]).slice();cds[k]=dir;sp.camdirs=cds;
  let d;
  try{d=await (await fetch('/api/savespeaker',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:sp.label,data:sp})})).json();}
  catch(e){toast(t('Папка камер не сохранена — сервер не ответил'));uiLog('savespeaker(camdirs): '+e);return;}
  if(!d.ok){toast(errText(d)||t('Папка камер не сохранена'));return;}
  SPEAKERS[d.key]=sp;
  uiLog(t('папка камеры {n} спикера «{s}» обновлена',{n:k+1,s:sp.label||''}));}
async function reloadCamFiles(k){
  try{const fd=await (await fetch('/api/files?dir='+encodeURIComponent(CAMDIRS[k]||''))).json();
    CAMFILES[k]=fd.files||[];fill('sel'+k,CAMFILES[k]);renderQueue();saveState();}catch(e){}}
// Поля папок камер в модалке спикера — по числу камер нарезки (nCams()).
function spkCamDirFill(cds){
  const host=$('spk_camdirs');if(!host)return;host.innerHTML='';
  for(let k=0;k<nCams();k++){
    const row=document.createElement('div');row.className='setrow';
    row.innerHTML='<label>'+t('Папка камеры {n}',{n:k+1})+'</label>'
      +'<input id="spk_camdir'+k+'" placeholder="'+t('пусто — автоподбор')+'">'
      +'<button class="sm" onclick="pickdir(\'spk_camdir'+k+'\')">'+t('Выбрать…')+'</button>';
    host.appendChild(row);$('spk_camdir'+k).value=(cds&&cds[k])||'';}}
// Смена спикера подставляет ЕГО папки и стиль.
async function onSpeakerChange(){const p=SPEAKERS[val('speaker')];
  if(p){
    await spkDir('ai_outdir','outdir',t('Папка результата задана вручную. Поставить папку спикера?\n'));
    // .jsx уезжают в проект AE того же человека — папка у них своя и такая же личная,
    // как папка нарезок. Раньше её меняли руками на шаге сборки и забывали.
    await spkDir('aeoutdir','jsxdir',t('Папка для .jsx задана вручную. Поставить папку спикера?\n'));
    // Папки камер — те же личные данные (задание U): свой материал, свои исходники.
    // Механика та же, что у spkDir, только массив: camdirs[k]. Пусто — автоподбор.
    for(let k=0;k<nCams();k++)await camDirApply(k,t('Папка камеры {n} задана вручную. Поставить папку спикера?\n',{n:k+1}));
    const st=$('style');
    // Стиль спикера — ЗНАЧЕНИЕ ПО УМОЛЧАНИЮ: ставим его здесь, а на шаге сборки юзер
    // меняет стиль как хочет, в профиль это не возвращается. Пропавший шаблон (стиль
    // переименовали или удалили) раньше молча игнорировался — и клип собирался чужим.
    // Клип с тегом живёт СВОИМ стилем (задание N): смена глобального спикера на шаге 1
    // не переписывает стиль открытого клипа — он принадлежит тегу, а не глобальному спикеру.
    const openTag=(curAE>=0&&CLIPS[curAE]&&(CLIPS[curAE].job||{}).speaker)||'';
    if(!openTag&&p.style&&st){
      if(STYLES[p.style]){if(st.value!==p.style){st.value=p.style;await onStyleChange();}}
      else{toast(t('Стиль «{n}» из профиля не найден — оставлен текущий',{n:p.style}));
        uiLog(t('спикер {n}: стиль {s} отсутствует в styles/',{n:p.label||'',s:p.style}));}}}
  renderStyleInfo();          // подпись «чей это стиль» — и когда стиль не менялся
  spkEditUI();cutSummary();saveState();renderAeDirField();}

// ---- редактор профиля спикера ----
// Профили заводились только руками, файлом в speakers/*.json: чтобы посадить нового
// человека, приходилось лезть в папку. Роуты save/delspeaker были готовы давно —
// не было окна.
function openSpeaker(key){
  SPKEDIT=SPEAKERS[key]?key:'';
  const p=SPKEDIT?SPEAKERS[SPKEDIT]:{};
  $('spkTitle').textContent=SPKEDIT?t('Спикер: ')+(p.label||SPKEDIT):t('Новый спикер');
  $('spk_label').value=SPKEDIT?(p.label||SPKEDIT):'';
  $('spk_outdir').value=p.outdir||'';
  $('spk_jsxdir').value=p.jsxdir||'';
  $('spk_renderdir').value=p.renderdir||'';
  spkCamDirFill(p.camdirs||[]);
  $('spk_hint').value=p.hint||'';
  $('spk_note').value=p.note||'';
  $('spk_bpcut').value=(p.breath_p_cut!=null?p.breath_p_cut:'');
  $('spk_bpmark').value=(p.breath_p_mark!=null?p.breath_p_mark:'');
  const ips=p.image_prompts||{};
  $('spk_extra_a').value=(ips.a&&ips.a.extra)||'';
  $('spk_pos_a').value=(ips.a&&ips.a.pos==='prefix')?'prefix':'suffix';
  $('spk_extra_b').value=(ips.b&&ips.b.extra)||'';
  $('spk_pos_b').value=(ips.b&&ips.b.pos==='prefix')?'prefix':'suffix';
  const vps=p.video_prompts||{};
  $('spk_video_extra_a').value=(vps.a&&vps.a.extra)||'';
  $('spk_video_pos_a').value=(vps.a&&vps.a.pos==='prefix')?'prefix':'suffix';
  $('spk_video_extra_b').value=(vps.b&&vps.b.extra)||'';
  $('spk_video_pos_b').value=(vps.b&&vps.b.pos==='prefix')?'prefix':'suffix';
  const ss=$('spk_style');ss.innerHTML='<option value="">'+t('не задан')+'</option>';
  Object.keys(STYLES).forEach(k=>{const o=document.createElement('option');
    o.value=k;o.textContent=t(STYLES[k].label||k);ss.appendChild(o);});
  ss.value=(p.style&&STYLES[p.style])?p.style:'';
  spkGrid(p.cut||{});
  $('spk_del').style.display=SPKEDIT?'':'none';
  openModal('mbSpeaker');}
// Поле пустое = порог общий. Поэтому в value кладём только то, что реально
// переопределено, а дефолт показываем placeholder'ом — иначе «профиль без правок»
// сохранился бы с шестнадцатью «своими» порогами, равными общим.
function spkGrid(cut){const host=$('spk_cut');if(!host)return;host.innerHTML='';
  SPKLAB.forEach(pair=>{const k=pair[0],lab=pair[1],d=SPKDEF[k];if(d===undefined)return;
    const cell=document.createElement('div');
    if(typeof d==='boolean')
      cell.innerHTML='<label class="chk"><input type="checkbox" data-cut="'+k+'"'
        +(cut[k]?' checked':'')+'> '+esc(t(lab))+'</label>';
    else
      cell.innerHTML='<label>'+esc(t(lab))+'</label><input type="number" step="0.01" data-cut="'+k+'"'
        +' value="'+(cut[k]!=null?esc(String(cut[k])):'')+'" placeholder="'+esc(String(d))+'">';
    host.appendChild(cell);});}
async function saveSpeaker(){
  const label=val('spk_label').trim();
  if(!label){toast(t('Дай спикеру имя'));$('spk_label').focus();return;}
  // Правим КОПИЮ профиля, а не собираем с нуля: в файле есть поля, которых в окне нет
  // (ref, breath_model) — пересборка их бы стёрла.
  const data=SPKEDIT?JSON.parse(JSON.stringify(SPEAKERS[SPKEDIT])):{};
  data.label=label;data.outdir=val('spk_outdir').trim();data.jsxdir=val('spk_jsxdir').trim();data.renderdir=val('spk_renderdir').trim();data.style=val('spk_style');
  data.hint=val('spk_hint');
  // Приписки к промптам генерации картинок (задание CQ)
  const exA=val('spk_extra_a').trim(),exB=val('spk_extra_b').trim();
  if(exA||exB){
    data.image_prompts={};
    if(exA)data.image_prompts.a={extra:exA,pos:val('spk_pos_a')==='prefix'?'prefix':'suffix'};
    if(exB)data.image_prompts.b={extra:exB,pos:val('spk_pos_b')==='prefix'?'prefix':'suffix'};
  }else delete data.image_prompts;
  // Видео хранит отдельные приписки: image_prompts нельзя переиспользовать, иначе
  // «3d icon» случайно уезжает в ролик. Пустые оба слота не записываем, чтобы старый
  // профиль оставался эквивалентен чистому query.
  const videoExA=val('spk_video_extra_a').trim(),videoExB=val('spk_video_extra_b').trim();
  if(videoExA||videoExB){
    data.video_prompts={};
    if(videoExA)data.video_prompts.a={extra:videoExA,pos:val('spk_video_pos_a')==='prefix'?'prefix':'suffix'};
    if(videoExB)data.video_prompts.b={extra:videoExB,pos:val('spk_video_pos_b')==='prefix'?'prefix':'suffix'};
  }else delete data.video_prompts;
  // Папки камер (задание U): пустое поле не пишется — «профиль без правок» не получает
  // мусорные camdirs, иначе у всех, кто не трогал, «свои» папки сломали бы автоподбор.
  const cds=[];let hasCam=false;
  for(let k=0;k<nCams();k++){const c=val('spk_camdir'+k).trim();cds.push(c);if(c)hasCam=true;}
  if(hasCam)data.camdirs=cds;else delete data.camdirs;
  const note=val('spk_note').trim();if(note)data.note=note;else delete data.note;
  [['breath_p_cut','spk_bpcut'],['breath_p_mark','spk_bpmark']].forEach(pair=>{
    const s=val(pair[1]).trim(),v=parseFloat(s);
    if(s===''||isNaN(v))delete data[pair[0]];else data[pair[0]]=v;});
  const cut={};let bad='';
  $('spk_cut').querySelectorAll('[data-cut]').forEach(el=>{const k=el.dataset.cut,d=SPKDEF[k];
    if(el.type==='checkbox'){if(el.checked!==d)cut[k]=el.checked;return;}
    const s=el.value.trim();if(s==='')return;
    const v=parseFloat(s);if(isNaN(v)){bad=bad||k;return;}
    if(v!==d)cut[k]=v;});
  if(bad){toast(t('Порог «{n}» — не число',{n:bad}));return;}
  data.cut=cut;
  let d;
  try{d=await (await fetch('/api/savespeaker',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:label,data:data})})).json();}
  catch(e){toast(t('Профиль не сохранён — сервер не ответил'));uiLog(t('savespeaker: ')+e);return;}
  if(!d.ok){toast(errText(d)||t('Профиль не сохранён'));uiLog(t('savespeaker: ')+JSON.stringify(d).slice(0,200));return;}
  // Переименование: файл кладётся под новым ключом, старый остался бы вторым профилем
  // в списке — с теми же настройками и прежним именем.
  if(SPKEDIT&&d.key!==SPKEDIT){
    // Клипы помнят спикера, которым резали (job.speaker) — без правки они остались бы
    // на старом, уже несуществующем имени, и следующая нарезка шла бы чужим профилем
    CLIPS.forEach(c=>{if(c.job&&c.job.speaker===SPKEDIT)c.job.speaker=d.key;});
    try{await fetch('/api/delspeaker',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:SPKEDIT})});}
    catch(e){uiLog(t('старый профиль ')+SPKEDIT+t(' не удалён: ')+e);}}
  SPKSAVED=d.key;await loadSpeakers();
  const sel=$('speaker');if(sel)sel.value=d.key;
  await onSpeakerChange();                // подставить папку и стиль сразу, а не со следующего выбора
  toast(t('Профиль «{n}» сохранён',{n:label}));uiLog(t('спикер сохранён: ')+d.path);
  closeModal('mbSpeaker');}
async function delSpeaker(){
  if(!SPKEDIT){closeModal('mbSpeaker');return;}
  const label=(SPEAKERS[SPKEDIT]||{}).label||SPKEDIT;
  if(!await askConfirm(t('Удалить профиль спикера «{n}»? (файл speakers/{f}.json)',{n:label,f:SPKEDIT})))return;
  let d;
  try{d=await (await fetch('/api/delspeaker',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:SPKEDIT})})).json();}
  catch(e){toast(t('Профиль не удалён — сервер не ответил'));uiLog(t('delspeaker: ')+e);return;}
  if(!d.ok){toast(errText(d)||t('Профиль не удалён'));return;}
  toast(t('Профиль «{n}» удалён',{n:label}));uiLog(t('спикер удалён: ')+SPKEDIT);
  SPKSAVED='';SPKEDIT='';await loadSpeakers();saveState();closeModal('mbSpeaker');}
function hex2rgb(h){h=(h||'').replace('#','');if(h.length!==6)return[1,0.9176,0];return[parseInt(h.slice(0,2),16)/255,parseInt(h.slice(2,4),16)/255,parseInt(h.slice(4,6),16)/255];}
function rgb2hex(a){const c=x=>('0'+Math.round(Math.max(0,Math.min(1,x||0))*255).toString(16)).slice(-2);a=a||[1,0.9176,0];return '#'+c(a[0])+c(a[1])+c(a[2]);}
function applyStyleHlColor(){
  const s=(typeof CURSTYLE!=='undefined'&&CURSTYLE)?CURSTYLE:{};
  const col=rgb2hex(s.hl_fill);
  let tx='#111111';
  const m=col.match(/^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i);
  if(m){
    const r=parseInt(m[1],16),g=parseInt(m[2],16),b=parseInt(m[3],16);
    const lum=0.299*r+0.587*g+0.114*b;
    if(lum<130)tx='#ffffff';
  }
  const introCol=s.intro_hl_fill?rgb2hex(s.intro_hl_fill):col;
  const accCol=s.hl_fill3?rgb2hex(s.hl_fill3):'#af1f1f';
  if(typeof document!=='undefined'&&document.documentElement){
    document.documentElement.style.setProperty('--subhl',col);
    document.documentElement.style.setProperty('--subhl-tx',tx);
    document.documentElement.style.setProperty('--introhl',introCol);
    document.documentElement.style.setProperty('--subhl3',accCol);
  }
}
// Три отказа — три разных сообщения. Раньше всё лежало в одном try, и любая ошибка
// РАЗБОРА (падение onStyleChange на битом состоянии) объявлялась «сервер не ответил»,
// а честная ошибка бэкенда молча уходила в никуда через if(!d.ok)return.
async function loadStyles(){let d;
  try{d=await (await fetch('/api/styles')).json();}
  catch(e){toast(t('Стили не загрузились — сервер не ответил'));uiLog(t('loadStyles(запрос): ')+e);return;}
  if(!d.ok){toast(t('Стили не загрузились: ')+(d.error||t('ответ без ok')));uiLog(t('loadStyles(ответ): ')+JSON.stringify(d).slice(0,300));return;}
  try{STYLES=d.styles||{};
    migrateClipStyles();
    const sel=$('style');const want=STYLESAVED||'base';sel.innerHTML='';
    Object.keys(STYLES).forEach(k=>{const o=document.createElement('option');o.value=k;o.textContent=t(STYLES[k].label||k);sel.appendChild(o);});
    if(want==='__custom__')ensureCustomOption();sel.value=(want==='__custom__')?'__custom__':(STYLES[want]?want:'base');onStyleChange();}
  catch(e){toast(t('Стили пришли, но не применились — смотри журнал'));uiLog(t('loadStyles(применение): ')+e);}}
// Миграция состояния: раньше задание клипа хранило РАЗВЁРНУТУЮ КОПИЮ стиля плюс свои
// поля рото. Теперь клип хранит только ИМЯ стиля, копия остаётся лишь у безымянного
// кастома. Стиль не узнали — клип честно становится кастомом со своей копией, молча
// терять его настройки нельзя.
function migrateClipStyles(){
  if(!STYLES||!Array.isArray(CLIPS))return;
  let n=0;
  CLIPS.forEach(c=>{const j=c&&c.job;if(!j)return;
    if(j.roto!==undefined||j.roto_bottom!==undefined){delete j.roto;delete j.roto_bottom;n++;}
    if(j.styleOwn!==undefined){delete j.styleOwn;n++;}
    if(!j.style)return;
    let k=j.styleKey;
    if(!k||k==='__edit__'||(k!=='__custom__'&&!STYLES[k]))k=styleKeyFor(j.style);
    if(k&&k!=='__custom__'&&STYLES[k]){j.styleKey=k;delete j.style;n++;}
    else j.styleKey='__custom__';});
  if(n)saveState();
  return n;}
// Какой пункт селектора соответствует стилю задания. Стиль в задании хранится РАЗВЁРНУТЫМ
// (j.style = весь объект), поэтому по одному ему шаблон не узнать — держим ещё и ключ (j.styleKey).
// Для старых заданий (и для «кастома», который на деле 1в1 совпал с шаблоном) сверяем содержимое:
// поля, которые правятся по клипу (рото) и подпись, в сравнении не участвуют.
// Рото стало свойством стиля, из сравнения «какой это шаблон» его исключать больше нельзя,
// иначе два стиля, отличающиеся только рото, считаются одинаковыми.
const STYLE_LOCAL=['label'];
function styleEq(a,b){if(!a||!b)return false;
  const keys=new Set([...Object.keys(a),...Object.keys(b)].filter(k=>STYLE_LOCAL.indexOf(k)<0));
  for(const k of keys){const x=a[k],y=b[k];
    if(x===y)continue;
    if(x==null&&y==null)continue;                       // нет ключа == null == дефолт
    if(JSON.stringify(x)!==JSON.stringify(y))return false;}
  return true;}
function styleKeyFor(st){if(!st)return null;
  const k=Object.keys(STYLES).find(k=>styleEq(st,STYLES[k]));return k||null;}
function ensureCustomOption(){const sel=$('style');if(!sel.querySelector('option[value="__custom__"]')){const oc=document.createElement('option');oc.value='__custom__';oc.textContent=t('кастом (свой)');sel.appendChild(oc);}}
function ensureEditOption(key,label){const sel=$('style');
  let o=sel.querySelector('option[value="__edit__"]');
  if(!o){o=document.createElement('option');o.value='__edit__';sel.appendChild(o);}
  o.textContent=t(label)+t(' — правится');}
function addCustomStyle(){const src=CURSTYLE||STYLES.base||{};CURSTYLE=JSON.parse(JSON.stringify(src));CURSTYLE.label='кастом';ensureCustomOption();$('style').value='__custom__';
  STYLE_EDITING=null;STYLE_EDIT_ORIG=null;STYLE_TOUCHED=false;
  if($('st_name'))$('st_name').value='';onStyleChange();}
const BUILTIN_STYLES={base:1,geologica:1};   // живут в styles.py, файла в styles/ у них нет
// Правка ШАБЛОНА. Раньше карандаш переключал селектор на «кастом (свой)» — и человеку
// казалось, что он заводит новый стиль, хотя сохранение перезаписывало тот же файл
// (задание AC2). Теперь правка выглядит правкой: селектор показывает имя шаблона с
// пометкой « — правится», кнопка «Сохранить» перезаписывает его. Встроенным base/geologica
// файла нет — только «Сохранить как…».
function editStyle(){
  const key=val('style');
  if(key==='__custom__'){if($('st_name'))$('st_name').focus();return;}      // уже кастом — не хватает имени
  if(STYLE_EDITING===key)return;
  const src=STYLES[key];if(!src){toast(t('Нечего править — стиль не выбран'));return;}
  // Рото — настройка КЛИПА, а не шаблона: запоминаем и возвращаем (иначе сбросится).
  const rb=val('rotobottom'),ro=$('roto').checked,r1=$('roto_cam1only').checked;
  STYLE_EDITING=key;STYLE_EDIT_ORIG=JSON.parse(JSON.stringify(src));STYLE_TOUCHED=false;
  CURSTYLE=JSON.parse(JSON.stringify(src));
  ensureEditOption(key,src.label||key);
  $('style').value='__edit__';
  $('roto').checked=ro;$('rotobottom').value=rb;$('roto_cam1only').checked=r1;rotoSync();
  if($('st_name'))$('st_name').value=BUILTIN_STYLES[key]?'':key;
  const el=$('st_saved');if(el){el.className='ok';el.textContent='';}
  fillStyleFields();
  renderStyleInfo();
  if(BUILTIN_STYLES[key])toast(t('«{n}» — встроенный шаблон: сохрани правки под своим именем',{n:t(src.label||key)}));
  else if($('stylehint'))$('stylehint').textContent=t('правится шаблон «{n}» — «Сохранить» перезапишет его',{n:t(src.label||key)});}
async function delStyle(){let name=$('style').value;
  // В режиме правки шаблона (задание AC2) в селекторе стоит служебный `__edit__`, а корзина
  // рядом с карандашом — про ТОТ шаблон, который сейчас правится. Без этой строки окно
  // спрашивало «удалить стиль „__edit__“?» и слало на сервер несуществующее имя.
  if(name==='__edit__')name=STYLE_EDITING||'';
  if(!name){toast(t('Нечего удалять — стиль не выбран'));return;}
  if(name==='__custom__'){toast(t('Это несохранённый кастом — просто выбери другой стиль'));return;}
  const label=(STYLES[name]&&STYLES[name].label)||name;
  if(!await askConfirm(t('Удалить шаблон стиля «{n}»? (файл styles/{f}.json)',{n:t(label),f:name})))return;
  const d=await (await fetch('/api/delstyle',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})})).json();
  if(d.error){toast(errText(d));return;}
  toast(t('Шаблон «{n}» удалён',{n:t(label)}));uiLog(t('стиль удалён: ')+name);
  STYLESAVED='base';await loadStyles();}
// Окна разделов панели стиля (задание AC2): не модалки — панель с полями раздела, в
// предпросмотре живёт в правой колонке рядом с кадром (панель целиком переезжает в
// вкладку «Стиль»), кадр не затемняется и продолжает играть. Одно окно за раз;
// закрытие по Esc и клику мимо (кнопка «x» ушла вместе с заголовком, задание CP1).
const STYLE_PARTS=['text','frame','inserts','layers','sound'];
const LAYER_DEFS = {
  subs: { title: 'Субтитры', desc: 'Строки субтитров и плашка' },
  intro: { title: 'Интро-текст', desc: 'Крупный акцентный текст' },
  photo: { title: 'Фотовставки', desc: 'Карточки фото и b-roll' },
  video: { title: 'Видеовставки', desc: 'Видео на весь экран и карточки' },
  roto: { title: 'Человек (ротоскоп)', desc: 'Вырезанный силуэт персонажа' }
};
const DEFAULT_LAYER_ORDER = ['subs', 'video', 'roto', 'photo', 'intro'];
let DRAG_LAYER_SRC = null;

function renderLayerOrderUI(){
  const box = $('st_layer_order_list');
  if(!box) return;
  const s = CURSTYLE || {};
  let list = Array.isArray(s.layer_order) ? [...s.layer_order] : [...DEFAULT_LAYER_ORDER];
  const validKeys = Object.keys(LAYER_DEFS);
  list = list.filter(k => validKeys.includes(k));
  validKeys.forEach(k => { if(!list.includes(k)) list.push(k); });
  if(!s.layer_order) s.layer_order = [...list];

  box.innerHTML = '';
  list.forEach((key, idx) => {
    const def = LAYER_DEFS[key] || { title: key, desc: '' };
    const row = document.createElement('div');
    row.className = 'layer-order-item';
    row.draggable = true;
    row.dataset.key = key;
    row.dataset.idx = idx;

    const grip = document.createElement('span');
    grip.className = 'layer-order-grip';
    grip.setAttribute('aria-hidden', 'true');
    grip.setAttribute('data-t', 'Перетащить строку для смены порядка');
    grip.innerHTML = ico('grip');
    row.appendChild(grip);

    const titleWrap = document.createElement('div');
    titleWrap.className = 'layer-order-title';
    titleWrap.innerHTML = `<span>${esc(t(def.title))}</span> <span class="layer-order-desc">${esc(t(def.desc))}</span>`;
    row.appendChild(titleWrap);

    const btns = document.createElement('div');
    btns.className = 'layer-order-btns';

    const upBtn = document.createElement('button');
    upBtn.type = 'button';
    upBtn.className = 'layer-order-btn';
    upBtn.innerHTML = ico('arrow_up');
    upBtn.setAttribute('aria-label', t('Поднять слой «{name}» выше', { name: t(def.title) }));
    upBtn.setAttribute('data-t', t('Поднять слой выше'));
    if(idx === 0) upBtn.disabled = true;
    upBtn.onclick = (e) => {
      e.stopPropagation();
      if(idx > 0){
        const next = [...list];
        const tmp = next[idx - 1];
        next[idx - 1] = next[idx];
        next[idx] = tmp;
        CURSTYLE.layer_order = next;
        stEdit();
        renderLayerOrderUI();
      }
    };
    btns.appendChild(upBtn);

    const dnBtn = document.createElement('button');
    dnBtn.type = 'button';
    dnBtn.className = 'layer-order-btn';
    dnBtn.innerHTML = ico('arrow_down');
    dnBtn.setAttribute('aria-label', t('Опустить слой «{name}» ниже', { name: t(def.title) }));
    dnBtn.setAttribute('data-t', t('Опустить слой ниже'));
    if(idx === list.length - 1) dnBtn.disabled = true;
    dnBtn.onclick = (e) => {
      e.stopPropagation();
      if(idx < list.length - 1){
        const next = [...list];
        const tmp = next[idx + 1];
        next[idx + 1] = next[idx];
        next[idx] = tmp;
        CURSTYLE.layer_order = next;
        stEdit();
        renderLayerOrderUI();
      }
    };
    btns.appendChild(dnBtn);
    row.appendChild(btns);

    row.addEventListener('dragstart', (e) => {
      DRAG_LAYER_SRC = idx;
      row.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
      try{ e.dataTransfer.setData('text/plain', key); }catch(_){}
    });
    row.addEventListener('dragend', () => {
      row.classList.remove('dragging');
      box.querySelectorAll('.layer-order-item').forEach(el => el.classList.remove('drag-over'));
      DRAG_LAYER_SRC = null;
    });
    row.addEventListener('dragover', (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      row.classList.add('drag-over');
    });
    row.addEventListener('dragleave', () => {
      row.classList.remove('drag-over');
    });
    row.addEventListener('drop', (e) => {
      e.preventDefault();
      row.classList.remove('drag-over');
      if(DRAG_LAYER_SRC != null && DRAG_LAYER_SRC !== idx){
        const next = [...list];
        const [moved] = next.splice(DRAG_LAYER_SRC, 1);
        next.splice(idx, 0, moved);
        CURSTYLE.layer_order = next;
        stEdit();
        renderLayerOrderUI();
      }
    });

    box.appendChild(row);
  });
}

function openStylePart(name){
  STYLE_PARTS.forEach(n=>{const el=$('stylepart_'+n);if(el)el.style.display=(n===name)?'':'none';});
  const r=document.querySelector('#styleparts input[name=stpart][value="'+name+'"]');
  if(r)r.checked=true;
  segUI();
  if(STYLE_PARTS.indexOf(name)>=0&&$('stylepart_'+name))$('stylepart_'+name).scrollIntoView({block:'nearest',behavior:'smooth'});}
function closeStylePart(){
  STYLE_PARTS.forEach(n=>{const el=$('stylepart_'+n);if(el)el.style.display='none';});
  document.querySelectorAll('#styleparts input[name=stpart]').forEach(r=>r.checked=false);
  segUI();}
function stylePartOpen(){return STYLE_PARTS.some(n=>{const el=$('stylepart_'+n);return el&&el.style.display!=='none';});}
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&stylePartOpen())closeStylePart();});
document.addEventListener('pointerdown',e=>{
  if(!stylePartOpen())return;
  const box=$('stylebox');
  if(box&&(box.contains(e.target)||(e.target.closest&&e.target.closest('#styleparts'))))return;
  closeStylePart();});
function updateHlBoldUI(){const on=$('st_hlbold').checked;const w=$('hlfontwrap');if(w)w.style.display=on?'':'none';}
// Есть ли на текущем стиле несохранённые правки: в режиме правки шаблона — расхождение
// CURSTYLE с его снимком до правок; в кастоме — флаг «поля трогали». Задание AC2.
function styleDirty(){
  if(STYLE_EDITING&&STYLE_EDIT_ORIG&&CURSTYLE)return !styleEq(CURSTYLE,STYLE_EDIT_ORIG);
  return !!STYLE_TOUCHED;}
function updateStyleSaveUI(){
  const row=$('styleSaveRow');if(!row)return;
  const k=val('style'), custom=k==='__custom__'||k==='__edit__'||!!STYLE_EDITING;
  row.style.display=(styleDirty()||custom)?'':'none';
}
async function onStyleChange(){const sel=$('style');const name=sel.value;const custom=$('stylecustom');
  if(name==='__edit__'){
    // режим правки шаблона: CURSTYLE уже скопирован editStyle — не трогаем, только поля
    if(!CURSTYLE)CURSTYLE=JSON.parse(JSON.stringify(STYLES[STYLE_EDITING]||STYLES.base||{}));
    if(custom)custom.style.display='';
    fillStyleFields();
  }
  else if(name==='__custom__'){
    if(!CURSTYLE)CURSTYLE=JSON.parse(JSON.stringify(STYLES.base||{}));
    if(custom)custom.style.display='';
    fillStyleFields();
  }
  else{
    // Уход со стиля с несохранёнными правками — спросить, а не потерять молча: правки
    // живут в CURSTYLE и будут перезаписаны новым шаблоном (задание AC2).
    if(styleDirty()&&!await askConfirm(t('На стиле есть несохранённые правки. Сменить стиль без сохранения?'))){
      sel.value=(STYLE_EDITING?'__edit__':'__custom__');return;
    }
    STYLE_EDITING=null;STYLE_EDIT_ORIG=JSON.parse(JSON.stringify(STYLES[name]||{}));STYLE_TOUCHED=false;
    CURSTYLE=JSON.parse(JSON.stringify(STYLES[name]||{}));
    if(custom)custom.style.display='';
    if($('st_name'))$('st_name').value='';
    if($('stylehint'))$('stylehint').textContent='';
    const el=$('st_saved');if(el){el.className='ok';el.textContent='';}
    fillStyleFields();
  }
  updateStyleDiffDots();
  renderStyleInfo();captureAE();}
function renderStyleInfo(){const el=$('styleinfo');if(!el)return;
  el.textContent=((CURSTYLE&&CURSTYLE.font)?(t('шрифт: ')+CURSTYLE.font+(CURSTYLE.hl_font?(t(' · выдел. ')+CURSTYLE.hl_font):'')):'')
    +styleSpeakerNote();}
// Стиль привязан к спикеру, но менять его тут можно свободно — поэтому пишем и чей он,
// и что стояло у спикера по умолчанию, если стиль от профиля увели.
function styleSpeakerNote(){const sp=(typeof SPEAKERS!=='undefined')?SPEAKERS[val('speaker')]:null;
  if(!sp||!sp.style)return '';
  return (val('style')===sp.style)
    ? t(' · стиль спикера «{n}»',{n:sp.label||''})
    : t(' · у спикера «{n}» по умолчанию: {s}',{n:sp.label||'',s:t((stylesMap()[sp.style]||{}).label||sp.style)});}
async function loadFonts(){try{const d=await (await fetch('/api/fonts')).json();FONTS=d.fonts||[];
  const dl=$('fontlist');if(dl)dl.innerHTML=FONTS.map(f=>'<option value="'+esc(f.ps)+'">'+esc(f.family)+'</option>').join('');updateHlFontList();}
  catch(e){uiLog(t('список шрифтов не загружен: ')+e);}}
function updateHlFontList(){const dl=$('hlfontlist');if(!dl||!FONTS.length)return;const base=(val('st_font')||'').trim();
  const fam=(FONTS.find(f=>f.ps===base)||{}).family;const list=fam?FONTS.filter(f=>f.family===fam):FONTS;
  dl.innerHTML=list.map(f=>'<option value="'+esc(f.ps)+'">'+esc(f.family)+'</option>').join('');}
function syncHex(){let v=val('st_hlhex').trim();if(v&&v[0]!=='#')v='#'+v;if(/^#[0-9a-fA-F]{6}$/.test(v)){v=v.toUpperCase();$('st_hlcolor').value=v;$('st_hlhex').value=v;stEdit();}}
function syncSubHex(){let v=val('st_subhex').trim();if(v&&v[0]!=='#')v='#'+v;if(/^#[0-9a-fA-F]{6}$/.test(v)){v=v.toUpperCase();$('st_subcolor').value=v;$('st_subhex').value=v;stEdit();}}
function syncSubBgHex(){let v=val('st_subbghex').trim();if(v&&v[0]!=='#')v='#'+v;if(/^#[0-9a-fA-F]{6}$/.test(v)){v=v.toUpperCase();$('st_subbgcolor').value=v;$('st_subbghex').value=v;stEdit();}}
function subBgUI(){const on=$('st_subbg')&&$('st_subbg').checked;const w=$('st_subbg_wrap');if(w)w.style.display=on?'':'none';}
function syncTopLineTrackHex(){let v=val('st_toplinetrackfillhex').trim();if(v&&v[0]!=='#')v='#'+v;if(/^#[0-9a-fA-F]{6}$/.test(v)){v=v.toUpperCase();$('st_toplinetrackfillcolor').value=v;$('st_toplinetrackfillhex').value=v;stEdit();}}
function syncTopLineFromHex(){let v=val('st_toplinefromhex').trim();if(v&&v[0]!=='#')v='#'+v;if(/^#[0-9a-fA-F]{6}$/.test(v)){v=v.toUpperCase();$('st_toplinefromcolor').value=v;$('st_toplinefromhex').value=v;stEdit();}}
function syncTopLineToHex(){let v=val('st_toplinetohex').trim();if(v&&v[0]!=='#')v='#'+v;if(/^#[0-9a-fA-F]{6}$/.test(v)){v=v.toUpperCase();$('st_toplinetocolor').value=v;$('st_toplinetohex').value=v;stEdit();}}
function topLineUI(){const on=$('st_topline')&&$('st_topline').checked;const w=$('st_topline_wrap');if(w)w.style.display=on?'':'none';}
function syncCaptionFillHex(){let v=val('st_captionfillhex').trim();if(v&&v[0]!=='#')v='#'+v;if(/^#[0-9a-fA-F]{6}$/.test(v)){v=v.toUpperCase();$('st_captionfillcolor').value=v;$('st_captionfillhex').value=v;stEdit();}}
function syncCaptionBgHex(){let v=val('st_captionbghex').trim();if(v&&v[0]!=='#')v='#'+v;if(/^#[0-9a-fA-F]{6}$/.test(v)){v=v.toUpperCase();$('st_captionbgcolor').value=v;$('st_captionbghex').value=v;stEdit();}}
function captionUI(){const on=$('st_caption')&&$('st_caption').checked;const w=$('st_caption_wrap');if(w)w.style.display=on?'':'none';captionBgUI();}
function captionBgUI(){const on=$('st_caption')&&$('st_caption').checked&&$('st_captionbg')&&$('st_captionbg').checked;const w=$('st_captionbg_wrap');if(w)w.style.display=on?'':'none';}
function syncHl3Hex(){let v=val('st_hl3hex').trim();if(v&&v[0]!=='#')v='#'+v;if(/^#[0-9a-fA-F]{6}$/.test(v)){v=v.toUpperCase();$('st_hl3color').value=v;$('st_hl3hex').value=v;stEdit();}}
// Тень ПРЕКОМПА интро (задание B): цвет тени камеры 1/камеры 2 — по образцу syncHl3Hex.
function syncIcShadow1Hex(){let v=val('st_icshadow1hex').trim();if(v&&v[0]!=='#')v='#'+v;if(/^#[0-9a-fA-F]{6}$/.test(v)){v=v.toUpperCase();$('st_icshadow1color').value=v;$('st_icshadow1hex').value=v;stEdit();}}
function syncIcShadow2Hex(){let v=val('st_icshadow2hex').trim();if(v&&v[0]!=='#')v='#'+v;if(/^#[0-9a-fA-F]{6}$/.test(v)){v=v.toUpperCase();$('st_icshadow2color').value=v;$('st_icshadow2hex').value=v;stEdit();}}
// intro_fill/intro_hl_fill — цвета с дефолтом None («как сегодня», задание FC): в
// отличие от обычных цветов источник истины для CURSTYLE не пикер (он не бывает
// пустым), а hex-поле — очистка hex-поля возвращает null, как пустой accent_font.
function syncIntroFillHex(){let v=val('st_introfillhex').trim();if(v&&v[0]!=='#'&&v!=='')v='#'+v;if(v===''){$('st_introfillhex').value='';stEdit();return;}if(/^#[0-9a-fA-F]{6}$/.test(v)){v=v.toUpperCase();$('st_introfillcolor').value=v;$('st_introfillhex').value=v;stEdit();}}
function syncIntroHlFillHex(){let v=val('st_introhlfillhex').trim();if(v&&v[0]!=='#'&&v!=='')v='#'+v;if(v===''){$('st_introhlfillhex').value='';stEdit();return;}if(/^#[0-9a-fA-F]{6}$/.test(v)){v=v.toUpperCase();$('st_introhlfillcolor').value=v;$('st_introhlfillhex').value=v;stEdit();}}
function introShadowUI(){const on=$('st_introshadow')&&$('st_introshadow').checked;const w=$('st_introshadow_wrap');if(w)w.style.display=on?'':'none';}
function rotoSync(){if(!CURSTYLE)CURSTYLE=JSON.parse(JSON.stringify(STYLES.base||{}));CURSTYLE.roto=$('roto').checked;CURSTYLE.roto_bottom=(parseFloat(val('rotobottom'))||0)/100;CURSTYLE.roto_cam1_only=$('roto_cam1only').checked;captureAE();rotoWrapUI();}
// Настройки рото («Устройство рото», «Низ маски», «Рото только на Камере 1») видны только
// при включённом «Авто-ротоскопе» (задание FG). Две двери: rotoSync — при клике по галке,
// fillStyleFields — при загрузке стиля (иначе после F5 обёртка не совпадёт с галкой).
function rotoWrapUI(){const w=$('rotowrap');if(!w)return;w.style.display=($('roto')&&$('roto').checked)?'':'none';}
// Сворачиваемые секции окна стиля (задание FG): состояние живёт в localStorage
// (ключ reelsi_stylesec_<имя>), как reelsi_ins_tab для вкладок окна вставок.
// Дефолт при первом открытии: «Субтитры» развёрнута, остальные свёрнуты.
const STYLESEC_DEFAULT={'stsec_subs':true};   // единственная развёрнутая по умолчанию
function styleSecKey(id){return 'reelsi_stylesec_'+id;}
function styleSecState(id){
  try{
    const v=localStorage.getItem(styleSecKey(id));
    if(v!==null)return v==='1';
  }catch(e){}
  return !!STYLESEC_DEFAULT[id];}
function styleSecApply(){
  document.querySelectorAll('#stylebody details.stsec').forEach(d=>{
    d.open=styleSecState(d.id);
    d.querySelector('summary').setAttribute('aria-expanded',d.open?'true':'false');
  });}
document.addEventListener('DOMContentLoaded',()=>{
  document.querySelectorAll('#stylebody details.stsec').forEach(d=>{
    d.addEventListener('toggle',()=>{
      try{localStorage.setItem(styleSecKey(d.id),d.open?'1':'0');}catch(e){}
      const s=d.querySelector('summary');if(s)s.setAttribute('aria-expanded',d.open?'true':'false');
    });
  });
  styleSecApply();});
// ---- живые подсказки стиля на превью ----
// «Низ маски %» (ротоскоп): пока поле крутится, на кадре предпросмотра снизу —
// полупрозрачная красная маска ровно на столько процентов высоты; перестал
// крутить или ушёл с поля — маска ушла. Иначе «35%» не говорит, где это по кадру.
let ROTOMASK_T=0;
function rotoMaskSync(){const v=parseFloat(val('rotobottom'));rotoMask(isNaN(v)?0:v);
  clearTimeout(ROTOMASK_T);ROTOMASK_T=setTimeout(rotoMaskHide,1500);}
function rotoMaskHide(){clearTimeout(ROTOMASK_T);rotoMask(-1);}
function rotoMask(pct){const show=pct>=0;
  document.querySelectorAll('.pvstage').forEach(st=>{let m=st.querySelector('.rotomask');
    if(show){if(!m){m=document.createElement('div');m.className='rotomask';
        m.innerHTML='<div class="rmband"></div>';st.appendChild(m);}
      m.querySelector('.rmband').style.height=Math.max(0,Math.min(100,pct))+'%';m.style.display='';}
    else if(m)m.style.display='none';});
  // маска рото живёт в координатах ИСХОДНИКА, а кадр двигает наезд Камеры 1 — рамке
  // подсказки нужен тот же transform, что и видео (иначе врёт тем сильнее, чем крупнее
  // зум). На паузе ipvZoom не тикает, поэтому зовём напрямую.
  if(show&&typeof ipvRotoMaskZoom==='function')ipvRotoMaskZoom();}
// позиция субтитров (sub_y, «% снизу»): строка в превью стоит ТАМ, где её поставит
// AE — отдельного маркера не нужно, сами слова и есть подсказка. Пока поле молчит
// (шаблон без правок), остаёмся на дефолте 40%, как у BASE.
function styleSubPos(){if(typeof CURSTYLE==='undefined'||!CURSTYLE)return;
  const pct=Math.round((1-(CURSTYLE.sub_y!=null?CURSTYLE.sub_y:0.5964))*100);
  // :not(.plan) — контейнер стопки по плану живёт во весь кадр (см. ipvSubs), и
  // инлайновый bottom обрезал бы ему высоту, а с ней и проценты рядов
  document.querySelectorAll('.pvsub:not(.plan)').forEach(el=>{el.style.bottom=pct+'%';});}
function reflectStyle(){if(!CURSTYLE)return;if(CURSTYLE.roto!=null)$('roto').checked=!!CURSTYLE.roto;if(CURSTYLE.roto_bottom!=null)$('rotobottom').value=Math.round(CURSTYLE.roto_bottom*100);
  $('roto_cam1only').checked=(CURSTYLE.roto_cam1_only!==false);
  if(CURSTYLE.intro_mode){const el=$('intromode');if(el)el.value=CURSTYLE.intro_mode;}
  styleSubPos();
  applyStyleHlColor();
  if(typeof aewUpdateCaptionUI==='function')aewUpdateCaptionUI();}
function styleFrameDim(){
  const pl=(typeof IPV!=='undefined'&&IPV.plan)||{};
  return {w:pl.w||1080,h:pl.h||1920};
}
function pxToPctX(px){return +(((px||0)/styleFrameDim().w)*100).toFixed(1);}
function pctToPxX(pct){return Math.round(((parseFloat(pct)||0)/100)*styleFrameDim().w);}
function pxToPctY(py){return +(((py||0)/styleFrameDim().h)*100).toFixed(1);}
function pctToPxY(pct){return Math.round(((parseFloat(pct)||0)/100)*styleFrameDim().h);}

function fillStyleFields(){const s=CURSTYLE||{};
  $('st_font').value=s.font||'';$('st_hlfont').value=s.hl_font||'';$('st_hlbold').checked=!!s.hl_bold;
  $('st_suby').value=Math.round((1-(s.sub_y!=null?s.sub_y:0.5964))*100);
  if($('st_subscale'))$('st_subscale').value=(s.sub_scale!=null?s.sub_scale:100);
  if($('st_subwords'))$('st_subwords').value=s.sub_words_per_row||1;
  if($('st_subrows'))$('st_subrows').value=s.sub_rows_max||1;
  $('st_introfont').value=s.intro_font||'';$('st_introhlfont').value=s.intro_hl_font||'';
  $('st_accentfont').value=s.accent_font||'';$('st_accentcase').value=s.accent_case||'title';
  $('st_backfont').value=s.back_font||'';$('st_backcase').value=s.back_case||'lower';
  if($('st_backstep'))$('st_backstep').value=(s.back_step!=null?s.back_step:0.45);
  if($('st_backscale'))$('st_backscale').value=(s.back_scale!=null?s.back_scale:0.69);
  if($('st_backgap'))$('st_backgap').value=(s.back_gap!=null?s.back_gap:4);
  if($('st_introanchor'))$('st_introanchor').value=s.intro_anchor||'center';
  if($('st_introanchor2'))$('st_introanchor2').value=s.intro_anchor2||'center';
  if($('st_introrotopos'))$('st_introrotopos').checked=!!s.intro_roto_by_pos;
  if($('st_introfade'))$('st_introfade').value=(s.intro_fade!=null?s.intro_fade:0.35);
  if($('st_introfxholdadd'))$('st_introfxholdadd').value=(s.intro_fx_hold_add!=null?s.intro_fx_hold_add:0.3);
  // затемнение под интро (задание IL): галка + непрозрачность слоя
  if($('st_introshade'))$('st_introshade').checked=!!s.intro_shade;
  if($('st_introshadeop'))$('st_introshadeop').value=(s.intro_shade_op!=null?s.intro_shade_op:100);
  $('st_hlcolor').value=rgb2hex(s.hl_fill);$('st_hlhex').value=rgb2hex(s.hl_fill).toUpperCase();
  $('st_subcolor').value=rgb2hex(s.sub_fill||[1,1,1]);$('st_subhex').value=rgb2hex(s.sub_fill||[1,1,1]).toUpperCase();
  $('st_subcase').value=s.sub_case||'upper';
  $('st_subbg').checked=!!s.sub_bg;
  $('st_subbgcolor').value=rgb2hex(s.sub_bg_fill||[1,1,1]);$('st_subbghex').value=rgb2hex(s.sub_bg_fill||[1,1,1]).toUpperCase();
  $('st_subbgop').value=(s.sub_bg_op!=null?s.sub_bg_op:72);
  $('st_subbgh').value=(s.sub_bg_h!=null?s.sub_bg_h:160);
  $('st_subbground').value=(s.sub_bg_round!=null?s.sub_bg_round:78);
  $('st_subbgpad').value=(s.sub_bg_pad!=null?s.sub_bg_pad:18);
  if($('st_subbgpadmin'))$('st_subbgpadmin').value=(s.sub_bg_padmin!=null?s.sub_bg_padmin:70);
  if($('st_subbganim'))$('st_subbganim').value=(s.sub_bg_anim!=null?s.sub_bg_anim:0.22);
  $('st_subbgdy').value=(s.sub_bg_dy!=null?s.sub_bg_dy:0);
  subBgUI();
  $('st_caption').checked=!!s.caption;
  $('st_captionfont').value=s.caption_font||'SFPro-Bold';
  $('st_captionsize').value=(s.caption_size!=null?s.caption_size:26);
  if($('st_captioncase'))$('st_captioncase').value=s.caption_case||'upper';
  $('st_captionfillcolor').value=rgb2hex(s.caption_fill||[1,1,1]);
  $('st_captionfillhex').value=rgb2hex(s.caption_fill||[1,1,1]).toUpperCase();
  $('st_captionx').value=(s.caption_x!=null?s.caption_x:55.5);
  $('st_captiony').value=(s.caption_y!=null?s.caption_y:228);
  $('st_captionbg').checked=(s.caption_bg!==false);
  $('st_captionbgcolor').value=rgb2hex(s.caption_bg_fill||[0.345,0.345,0.345]);
  $('st_captionbghex').value=rgb2hex(s.caption_bg_fill||[0.345,0.345,0.345]).toUpperCase();
  $('st_captionbgop').value=(s.caption_bg_op!=null?s.caption_bg_op:45);
  $('st_captionbground').value=(s.caption_bg_round!=null?s.caption_bg_round:68);
  if($('st_captionkx'))$('st_captionkx').value=(s.caption_kx!=null?s.caption_kx:1.718);
  if($('st_captionky'))$('st_captionky').value=(s.caption_ky!=null?s.caption_ky:2.484);
  captionUI();
  $('st_musicdb').value=(s.music_db!=null?s.music_db:-20);$('st_voicedb').value=(s.voice_db!=null?s.voice_db:0);
  syncDbSliders();
  $('st_introsfx').checked=(s.intro_riser!==false);
  if($('st_audiofades'))$('st_audiofades').checked=(s.audio_fades!==false);
  if($('st_riserfile'))$('st_riserfile').value=s.intro_riser_file||'';
  if($('st_poplead'))$('st_poplead').value=(s.pop_lead!=null?s.pop_lead:4);
  if($('st_popdb'))$('st_popdb').value=(s.pop_db!=null?s.pop_db:0);
  if($('st_glitchdb'))$('st_glitchdb').value=(s.glitch_db!=null?s.glitch_db:0);
  const dshow=(s.disclaimer!=='');  $('st_disc_show').checked=dshow;$('st_disc_text').value=(dshow&&s.disclaimer)?s.disclaimer:'';discUI();
  $('st_disc_end').checked=!!s.disclaimer_end;
  if($('st_discgap'))$('st_discgap').value=(s.disc_gap!=null?s.disc_gap:'');
  $('st_startblur').value=(s.start_blur!=null?s.start_blur:0);$('st_startblurdur').value=(s.start_blur_dur!=null?s.start_blur_dur:0.52);
  $('st_trans').value=s.transition||'';$('st_transsfx').value=s.transition_sfx||'';$('st_pop').value=s.pop||'';
  if($('st_glitch'))$('st_glitch').value=s.glitch||'';
  $('st_rotodev').value=s.roto_device||'';$('st_insstyle').value=s.insert_style||'auto';
  $('st_insfx').value=s.insert_fx||'card';
  if($('st_insanim'))$('st_insanim').value=s.insert_anim||'zoom';
  $('st_insc1on2x').value=pxToPctX(s.insert_c1on2_x!=null?s.insert_c1on2_x:0);$('st_insc1on2y').value=pxToPctY(s.insert_c1on2_y!=null?s.insert_c1on2_y:0);
  $('st_insc1x').value=pxToPctX(s.insert_c1_x!=null?s.insert_c1_x:0);$('st_insc1y').value=pxToPctY(s.insert_c1_y!=null?s.insert_c1_y:0);
  insC1On2UI();
  $('st_snapcut').checked=(s.insert_snap_cut!==false);
  $('st_subswap').checked=(s.insert_sub_swap!==false);
  renderLayerOrderUI();
  $('st_cam1zoom').value=s.cam1_zoom||'pulse';
  $('st_cam1zoomstart').checked=(s.cam1_zoom_start!==false);
  if($('st_cam1zoombig'))$('st_cam1zoombig').value=(s.cam1_zoom_big!=null?s.cam1_zoom_big:182);
  if($('st_cam1zoomlo'))$('st_cam1zoomlo').value=(s.cam1_zoom_lo!=null?s.cam1_zoom_lo:112);
  if($('st_cam1zoomhi'))$('st_cam1zoomhi').value=(s.cam1_zoom_hi!=null?s.cam1_zoom_hi:140);
  $('st_topline').checked=!!s.top_line;
  $('st_topliney').value=(s.top_line_y!=null?s.top_line_y:162);
  $('st_toplinew').value=(s.top_line_w!=null?s.top_line_w:969);
  $('st_toplineth').value=(s.top_line_th!=null?s.top_line_th:12.5);
  $('st_toplinetrackop').value=(s.top_line_track_op!=null?s.top_line_track_op:16);
  $('st_toplinetrackfillcolor').value=rgb2hex(s.top_line_track_fill||[1,1,1]);
  $('st_toplinetrackfillhex').value=rgb2hex(s.top_line_track_fill||[1,1,1]).toUpperCase();
  $('st_toplinefromcolor').value=rgb2hex(s.top_line_from||[0.984,1,0.541]);
  $('st_toplinefromhex').value=rgb2hex(s.top_line_from||[0.984,1,0.541]).toUpperCase();
  $('st_toplinetocolor').value=rgb2hex(s.top_line_to||[1,0.698,0.988]);
  $('st_toplinetohex').value=rgb2hex(s.top_line_to||[1,0.698,0.988]).toUpperCase();
  topLineUI();
  $('st_driftlo').value=(s.cam1_drift_lo!=null?s.cam1_drift_lo:100);$('st_drifthi').value=(s.cam1_drift_hi!=null?s.cam1_drift_hi:160);
  $('st_cam1fit').value=(s.cam1_fit!=null?s.cam1_fit:100);
  $('st_introscale').value=(s.intro_scale!=null?s.intro_scale:100);
  // ползунки «% кадра»: значение в стиле хранится в px, ползунок показывает долю кадра
  // (задание Q). H берём из плана сцены, вне превью — стандартный вертикальный кадр.
  const H=(typeof IPV!=='undefined'&&IPV.plan&&IPV.plan.h)||1920;
  $('st_introy').value=Math.round(((s.intro_y!=null?s.intro_y:0)/H)*100);
  $('st_introy2').value=Math.round(((s.intro_y2!=null?s.intro_y2:0)/H)*100);
  if($('st_introx'))$('st_introx').value=pxToPctX(s.intro_x!=null?s.intro_x:0);
  $('st_insc2y').value=Math.round(((s.insert_c2_y!=null?s.insert_c2_y:0.172))*1000)/10;
  if($('st_insc2x'))$('st_insc2x').value=Math.round(((s.insert_c2_x!=null?s.insert_c2_x:0.5))*1000)/10;
  // Класть сюда px НЕЛЬЗЯ: ползунок ограничен -50..100 (% кадра), и intro_y=799
  // прижимался к 100, а следующая же правка любого поля читала эти 100 обратно и
  // записывала в стиль 1920 px. Стиль портился от одного открытия окна.
  $('st_introy').value=Math.max(-50,Math.min(100,$('st_introy').value));
  $('st_introy2').value=Math.max(-50,Math.min(100,$('st_introy2').value));
  $('st_introglow').value=(s.intro_glow!=null?s.intro_glow:1);
  $('st_hl3color').value=rgb2hex(s.hl_fill3||[0.6863,0.1216,0.1216]);$('st_hl3hex').value=rgb2hex(s.hl_fill3||[0.6863,0.1216,0.1216]).toUpperCase();
  $('st_introfillcolor').value=rgb2hex(s.intro_fill||[1,1,1]);$('st_introfillhex').value=s.intro_fill?rgb2hex(s.intro_fill).toUpperCase():'';
  $('st_introhlfillcolor').value=rgb2hex(s.intro_hl_fill||s.hl_fill||[1,0.918,0]);$('st_introhlfillhex').value=s.intro_hl_fill?rgb2hex(s.intro_hl_fill).toUpperCase():'';
  $('st_introshadow').checked=!!s.intro_shadow;
  $('st_introshadowop').value=(s.intro_shadow_op!=null?s.intro_shadow_op:116);
  $('st_introshadowdir').value=(s.intro_shadow_dir!=null?s.intro_shadow_dir:16);
  $('st_introshadowdist').value=(s.intro_shadow_dist!=null?s.intro_shadow_dist:6.8);
  $('st_introshadowsoft').value=(s.intro_shadow_soft!=null?s.intro_shadow_soft:34);
  $('st_backshadowop').value=(s.back_shadow_op!=null?s.back_shadow_op:131);
  $('st_backshadowsoft').value=(s.back_shadow_soft!=null?s.back_shadow_soft:38);
  // Тень ПРЕКОМПА интро (задание B): своя у камеры 1 и камеры 2 — цвет + непрозрачность
  $('st_icshadow1color').value=rgb2hex(s.intro_comp_shadow_fill||[1,1,1]);$('st_icshadow1hex').value=rgb2hex(s.intro_comp_shadow_fill||[1,1,1]).toUpperCase();
  $('st_icshadow1op').value=(s.intro_comp_shadow_op!=null?s.intro_comp_shadow_op:68);
  $('st_icshadow2color').value=rgb2hex(s.intro_comp_shadow2_fill||[1,1,1]);$('st_icshadow2hex').value=rgb2hex(s.intro_comp_shadow2_fill||[1,1,1]).toUpperCase();
  $('st_icshadow2op').value=(s.intro_comp_shadow2_op!=null?s.intro_comp_shadow2_op:68);
  introShadowUI();
  const dw=$('driftwrap');if(dw)dw.style.display=((s.cam1_zoom||'pulse')==='drift')?'':'none';
  const zsw=$('cam1zoomstartwrap');if(zsw)zsw.style.display=((s.cam1_zoom||'pulse')==='none')?'none':'';
  // Первый наезд (cam1_zoom_big) используется только в pulse и drift; в jump его нет
  // (см. xml2ae/build.py) — показываем по тому же правилу, что у driftwrap.
  const zbw=$('zoombigwrap');if(zbw)zbw.style.display=((s.cam1_zoom||'pulse')==='jump'||(s.cam1_zoom||'pulse')==='none')?'none':'';
  // Возвраты (cam1_zoom_lo/hi) работают в pulse и jump; в drift свои границы дрейфа
  const zrw=$('zoomretwrap');if(zrw)zrw.style.display=((s.cam1_zoom||'pulse')==='drift'||(s.cam1_zoom||'pulse')==='none')?'none':'';
  updateHlFontList();updateHlBoldUI();reflectStyle();rotoWrapUI();styleSecApply();
  if(typeof syncSldnums==='function')syncSldnums();
  if(typeof syncSubTabUI==='function')syncSubTabUI();
  applyStyleHlColor();
  updateStyleDiffDots();}
function updateStyleDiffDots(){
  const s=CURSTYLE||{};
  let orig=STYLE_EDIT_ORIG;
  if(!orig){
    const k=STYLE_EDITING||val('style');
    orig=(k&&k!=='__custom__'&&k!=='__edit__'&&STYLES[k])?STYLES[k]:(STYLES.base||{});
  }
  const H=(typeof IPV!=='undefined'&&IPV.plan&&IPV.plan.h)||1920;

  // text tab diffs
  const d_font=(s.font||'SFPro-CondensedSemibold')!==(orig.font||'SFPro-CondensedSemibold');
  const d_hlcolor=rgb2hex(s.hl_fill||[1,0.918,0])!==rgb2hex(orig.hl_fill||[1,0.918,0]);
  const d_hlbold=(!!s.hl_bold)!==(!!orig.hl_bold);
  const d_subcolor=rgb2hex(s.sub_fill||[1,1,1])!==rgb2hex(orig.sub_fill||[1,1,1]);
  const d_subcase=(s.sub_case||'upper')!==(orig.sub_case||'upper');
  const d_suby=Math.round((1-(s.sub_y!=null?s.sub_y:0.5964))*100)!==Math.round((1-(orig.sub_y!=null?orig.sub_y:0.5964))*100);
  const d_subscale=(s.sub_scale!=null?s.sub_scale:100)!==(orig.sub_scale!=null?orig.sub_scale:100);
  const d_subwords=(s.sub_words_per_row||1)!==(orig.sub_words_per_row||1);
  const d_subrows=(s.sub_rows_max||1)!==(orig.sub_rows_max||1);
  const d_subbg=(!!s.sub_bg)!==(!!orig.sub_bg);
  const d_subbgcolor=rgb2hex(s.sub_bg_fill||[1,1,1])!==rgb2hex(orig.sub_bg_fill||[1,1,1]);
  const d_subbgop=(s.sub_bg_op!=null?s.sub_bg_op:72)!==(orig.sub_bg_op!=null?orig.sub_bg_op:72);
  const d_subbgh=(s.sub_bg_h!=null?s.sub_bg_h:160)!==(orig.sub_bg_h!=null?orig.sub_bg_h:160);
  const d_subbground=(s.sub_bg_round!=null?s.sub_bg_round:78)!==(orig.sub_bg_round!=null?orig.sub_bg_round:78);
  const d_subbgpad=(s.sub_bg_pad!=null?s.sub_bg_pad:18)!==(orig.sub_bg_pad!=null?orig.sub_bg_pad:18);
  const d_subbgdy=(s.sub_bg_dy!=null?s.sub_bg_dy:0)!==(orig.sub_bg_dy!=null?orig.sub_bg_dy:0);
  const d_caption=(!!s.caption)!==(!!orig.caption);
  const d_captionfont=(s.caption_font||'SFPro-Bold')!==(orig.caption_font||'SFPro-Bold');
  const d_captionsize=(s.caption_size!=null?s.caption_size:26)!==(orig.caption_size!=null?orig.caption_size:26);
  const d_captioncase=(s.caption_case||'upper')!==(orig.caption_case||'upper');
  const d_captionfill=rgb2hex(s.caption_fill||[1,1,1])!==rgb2hex(orig.caption_fill||[1,1,1]);
  const d_captionx=(s.caption_x!=null?s.caption_x:55.5)!==(orig.caption_x!=null?orig.caption_x:55.5);
  const d_captiony=(s.caption_y!=null?s.caption_y:228)!==(orig.caption_y!=null?orig.caption_y:228);
  const d_captionbg=(s.caption_bg!==false)!==(orig.caption_bg!==false);
  const d_captionbgcolor=rgb2hex(s.caption_bg_fill||[0.345,0.345,0.345])!==rgb2hex(orig.caption_bg_fill||[0.345,0.345,0.345]);
  const d_captionbgop=(s.caption_bg_op!=null?s.caption_bg_op:45)!==(orig.caption_bg_op!=null?orig.caption_bg_op:45);
  const d_captionbground=(s.caption_bg_round!=null?s.caption_bg_round:68)!==(orig.caption_bg_round!=null?orig.caption_bg_round:68);
  const d_captionkx=(s.caption_kx!=null?s.caption_kx:1.718)!==(orig.caption_kx!=null?orig.caption_kx:1.718);
  const d_captionky=(s.caption_ky!=null?s.caption_ky:2.484)!==(orig.caption_ky!=null?orig.caption_ky:2.484);
  const d_hlfont=(s.hl_font||'')!==(orig.hl_font||'');
  const d_introfont=(s.intro_font||'')!==(orig.intro_font||'');
  const d_introhlfont=(s.intro_hl_font||'')!==(orig.intro_hl_font||'');
  const d_accentfont=(s.accent_font||'')!==(orig.accent_font||'');
  const d_accentcase=(s.accent_case||'title')!==(orig.accent_case||'title');
  const d_backfont=(s.back_font||'')!==(orig.back_font||'');
  const d_backcase=(s.back_case||'lower')!==(orig.back_case||'lower');
  const d_backstep=(s.back_step!=null?s.back_step:0.45)!==(orig.back_step!=null?orig.back_step:0.45);
  const d_backscale=(s.back_scale!=null?s.back_scale:0.69)!==(orig.back_scale!=null?orig.back_scale:0.69);
  const d_backgap=(s.back_gap!=null?s.back_gap:4)!==(orig.back_gap!=null?orig.back_gap:4);
  const d_introanchor=(s.intro_anchor||'center')!==(orig.intro_anchor||'center');
  const d_introanchor2=(s.intro_anchor2||'center')!==(orig.intro_anchor2||'center');
  const d_introrotopos=(!!s.intro_roto_by_pos)!==(!!orig.intro_roto_by_pos);
  const d_introfade=(s.intro_fade!=null?s.intro_fade:0.35)!==(orig.intro_fade!=null?orig.intro_fade:0.35);
  const d_introfxholdadd=(s.intro_fx_hold_add!=null?s.intro_fx_hold_add:0.3)!==(orig.intro_fx_hold_add!=null?orig.intro_fx_hold_add:0.3);
  const d_introshade=(!!s.intro_shade)!==(!!orig.intro_shade);
  const d_introshadeop=(s.intro_shade_op!=null?s.intro_shade_op:100)!==(orig.intro_shade_op!=null?orig.intro_shade_op:100);
  const d_introglow=(s.intro_glow!=null?s.intro_glow:1)!==(orig.intro_glow!=null?orig.intro_glow:1);
  const d_hl3color=rgb2hex(s.hl_fill3||[0.6863,0.1216,0.1216])!==rgb2hex(orig.hl_fill3||[0.6863,0.1216,0.1216]);
  const d_introfill=!!s.intro_fill!==!!orig.intro_fill||(!!s.intro_fill&&rgb2hex(s.intro_fill)!==rgb2hex(orig.intro_fill));
  const d_introhlfill=!!s.intro_hl_fill!==!!orig.intro_hl_fill||(!!s.intro_hl_fill&&rgb2hex(s.intro_hl_fill)!==rgb2hex(orig.intro_hl_fill));
  const d_introshadow=(!!s.intro_shadow)!==(!!orig.intro_shadow);
  const d_introshadowop=(s.intro_shadow_op!=null?s.intro_shadow_op:116)!==(orig.intro_shadow_op!=null?orig.intro_shadow_op:116);
  const d_introshadowdir=(s.intro_shadow_dir!=null?s.intro_shadow_dir:16)!==(orig.intro_shadow_dir!=null?orig.intro_shadow_dir:16);
  const d_introshadowdist=(s.intro_shadow_dist!=null?s.intro_shadow_dist:6.8)!==(orig.intro_shadow_dist!=null?orig.intro_shadow_dist:6.8);
  const d_introshadowsoft=(s.intro_shadow_soft!=null?s.intro_shadow_soft:34)!==(orig.intro_shadow_soft!=null?orig.intro_shadow_soft:34);
  const d_backshadowop=(s.back_shadow_op!=null?s.back_shadow_op:131)!==(orig.back_shadow_op!=null?orig.back_shadow_op:131);
  const d_backshadowsoft=(s.back_shadow_soft!=null?s.back_shadow_soft:38)!==(orig.back_shadow_soft!=null?orig.back_shadow_soft:38);
  const d_icshadow1fill=rgb2hex(s.intro_comp_shadow_fill||[1,1,1])!==rgb2hex(orig.intro_comp_shadow_fill||[1,1,1]);
  const d_icshadow1op=(s.intro_comp_shadow_op!=null?s.intro_comp_shadow_op:68)!==(orig.intro_comp_shadow_op!=null?orig.intro_comp_shadow_op:68);
  const d_icshadow2fill=rgb2hex(s.intro_comp_shadow2_fill||[1,1,1])!==rgb2hex(orig.intro_comp_shadow2_fill||[1,1,1]);
  const d_icshadow2op=(s.intro_comp_shadow2_op!=null?s.intro_comp_shadow2_op:68)!==(orig.intro_comp_shadow2_op!=null?orig.intro_comp_shadow2_op:68);
  const d_introscale=(s.intro_scale!=null?s.intro_scale:100)!==(orig.intro_scale!=null?orig.intro_scale:100);
  const d_introy=Math.round(((s.intro_y!=null?s.intro_y:0)/H)*100)!==Math.round(((orig.intro_y!=null?orig.intro_y:0)/H)*100);
  const d_introy2=Math.round(((s.intro_y2!=null?s.intro_y2:0)/H)*100)!==Math.round(((orig.intro_y2!=null?orig.intro_y2:0)/H)*100);
  const d_introx=pxToPctX(s.intro_x!=null?s.intro_x:0)!==pxToPctX(orig.intro_x!=null?orig.intro_x:0);
  const d_subbgpadmin=(s.sub_bg_padmin!=null?s.sub_bg_padmin:70)!==(orig.sub_bg_padmin!=null?orig.sub_bg_padmin:70);
  const d_subbganim=(s.sub_bg_anim!=null?s.sub_bg_anim:0.22)!==(orig.sub_bg_anim!=null?orig.sub_bg_anim:0.22);
  const d_discshow=(s.disclaimer!=='')!==(orig.disclaimer!=='');
  const d_discend=(!!s.disclaimer_end)!==(!!orig.disclaimer_end);
  const d_disctext=((s.disclaimer!=='')?(s.disclaimer||''):'')!==((orig.disclaimer!=='')?(orig.disclaimer||''):'');
  const d_discgap=(s.disc_gap!=null?s.disc_gap:null)!==(orig.disc_gap!=null?orig.disc_gap:null);

  // frame tab diffs
  const d_cam1zoom=(s.cam1_zoom||'pulse')!==(orig.cam1_zoom||'pulse');
  const d_zoombig=(s.cam1_zoom_big!=null?s.cam1_zoom_big:182)!==(orig.cam1_zoom_big!=null?orig.cam1_zoom_big:182);
  const d_zoomret=(s.cam1_zoom_lo!=null?s.cam1_zoom_lo:112)!==(orig.cam1_zoom_lo!=null?orig.cam1_zoom_lo:112)
               ||(s.cam1_zoom_hi!=null?s.cam1_zoom_hi:140)!==(orig.cam1_zoom_hi!=null?orig.cam1_zoom_hi:140);
  const d_cam1zoomstart=(s.cam1_zoom_start!==false)!==(orig.cam1_zoom_start!==false);
  const d_topline=(!!s.top_line)!==(!!orig.top_line);
  const d_topliney=(s.top_line_y!=null?s.top_line_y:162)!==(orig.top_line_y!=null?orig.top_line_y:162);
  const d_toplinew=(s.top_line_w!=null?s.top_line_w:969)!==(orig.top_line_w!=null?orig.top_line_w:969);
  const d_toplineth=(s.top_line_th!=null?s.top_line_th:12.5)!==(orig.top_line_th!=null?orig.top_line_th:12.5);
  const d_toplinetrackop=(s.top_line_track_op!=null?s.top_line_track_op:16)!==(orig.top_line_track_op!=null?orig.top_line_track_op:16);
  const d_toplinetrackfill=rgb2hex(s.top_line_track_fill||[1,1,1])!==rgb2hex(orig.top_line_track_fill||[1,1,1]);
  const d_toplinefrom=rgb2hex(s.top_line_from||[0.984,1,0.541])!==rgb2hex(orig.top_line_from||[0.984,1,0.541]);
  const d_toplineto=rgb2hex(s.top_line_to||[1,0.698,0.988])!==rgb2hex(orig.top_line_to||[1,0.698,0.988]);
  const d_pickzoom=(s.cam1_zoom_cx!=null?s.cam1_zoom_cx:0.5)!==(orig.cam1_zoom_cx!=null?orig.cam1_zoom_cx:0.5)
                 ||(s.cam1_zoom_cy!=null?s.cam1_zoom_cy:0.5)!==(orig.cam1_zoom_cy!=null?orig.cam1_zoom_cy:0.5);
  const d_drift=(s.cam1_drift_lo!=null?s.cam1_drift_lo:100)!==(orig.cam1_drift_lo!=null?orig.cam1_drift_lo:100)
             ||(s.cam1_drift_hi!=null?s.cam1_drift_hi:160)!==(orig.cam1_drift_hi!=null?orig.cam1_drift_hi:160);
  const d_cam1fit=(s.cam1_fit!=null?s.cam1_fit:100)!==(orig.cam1_fit!=null?orig.cam1_fit:100);
  const d_startblur=(s.start_blur!=null?s.start_blur:0)!==(orig.start_blur!=null?orig.start_blur:0);
  const d_startblurdur=(s.start_blur_dur!=null?s.start_blur_dur:0.52)!==(orig.start_blur_dur!=null?orig.start_blur_dur:0.52);

  // inserts tab diffs
  const d_insstyle=(s.insert_style||'auto')!==(orig.insert_style||'auto');
  const d_insfx=(s.insert_fx||'card')!==(orig.insert_fx||'card');
  const d_insanim=(s.insert_anim||'zoom')!==(orig.insert_anim||'zoom');
  const d_insc1on2=pxToPctX(s.insert_c1on2_x!=null?s.insert_c1on2_x:0)!==pxToPctX(orig.insert_c1on2_x!=null?orig.insert_c1on2_x:0)
                ||pxToPctY(s.insert_c1on2_y!=null?s.insert_c1on2_y:0)!==pxToPctY(orig.insert_c1on2_y!=null?orig.insert_c1on2_y:0);
  const d_rotodev=(s.roto_device||'')!==(orig.roto_device||'');
  const d_snapcut=(s.insert_snap_cut!==false)!==(orig.insert_snap_cut!==false);
  const d_subswap=(s.insert_sub_swap!==false)!==(orig.insert_sub_swap!==false);
  const d_insc2y=Math.round((s.insert_c2_y!=null?s.insert_c2_y:0.172)*1000)/10!==Math.round((orig.insert_c2_y!=null?orig.insert_c2_y:0.172)*1000)/10;
  const d_insc2x=Math.round((s.insert_c2_x!=null?s.insert_c2_x:0.5)*1000)/10!==Math.round((orig.insert_c2_x!=null?orig.insert_c2_x:0.5)*1000)/10;
  const d_insc1=pxToPctX(s.insert_c1_x!=null?s.insert_c1_x:0)!==pxToPctX(orig.insert_c1_x!=null?orig.insert_c1_x:0)
             ||pxToPctY(s.insert_c1_y!=null?s.insert_c1_y:0)!==pxToPctY(orig.insert_c1_y!=null?orig.insert_c1_y:0);

  // layers tab diffs (задание FM)
  const d_layer_order=JSON.stringify(s.layer_order||DEFAULT_LAYER_ORDER)!==JSON.stringify(orig.layer_order||DEFAULT_LAYER_ORDER);

  // sound tab diffs
  const d_musicdb=(s.music_db!=null?s.music_db:-20)!==(orig.music_db!=null?orig.music_db:-20);
  const d_voicedb=(s.voice_db!=null?s.voice_db:0)!==(orig.voice_db!=null?orig.voice_db:0);
  const d_introsfx=(s.intro_riser!==false)!==(orig.intro_riser!==false);
  const d_audiofades=(s.audio_fades!==false)!==(orig.audio_fades!==false);
  const d_poplead=(s.pop_lead!=null?s.pop_lead:4)!==(orig.pop_lead!=null?orig.pop_lead:4);
  const d_popdb=(s.pop_db!=null?s.pop_db:0)!==(orig.pop_db!=null?orig.pop_db:0);
  const d_glitchdb=(s.glitch_db!=null?s.glitch_db:0)!==(orig.glitch_db!=null?orig.glitch_db:0);
  const d_riserfile=(s.intro_riser_file||'')!==(orig.intro_riser_file||'');
  const d_trans=(s.transition||'')!==(orig.transition||'');
  const d_transsfx=(s.transition_sfx||'')!==(orig.transition_sfx||'');
  const d_pop=(s.pop||'')!==(orig.pop||'');
  const d_glitch=(s.glitch||'')!==(orig.glitch||'');

  const setDot=(el,diff)=>{if(el)el.classList.toggle('st-changed',!!diff);};
  const setParentDot=(id,diff)=>{const el=$(id);if(el){const p=el.closest('.stylegrid > div')||el.closest('.stylemats > div')||el.closest('.stylepart > div')||el.parentElement;if(p)p.classList.toggle('st-changed',!!diff);}};

  setParentDot('st_font', d_font);
  setParentDot('st_hlcolor', d_hlcolor);
  setDot($('st_hlbold')&&$('st_hlbold').closest('label'), d_hlbold);
  setParentDot('st_subcolor', d_subcolor);
  setParentDot('st_subcase', d_subcase);
  setParentDot('st_suby', d_suby);
  setParentDot('st_subscale', d_subscale);
  setParentDot('st_subwords', d_subwords);
  setParentDot('st_subrows', d_subrows);
  setDot($('st_subbg')&&$('st_subbg').closest('label'), d_subbg);
  setParentDot('st_subbgcolor', d_subbgcolor);
  setParentDot('st_subbgop', d_subbgop);
  setParentDot('st_subbgh', d_subbgh);
  setParentDot('st_subbground', d_subbground);
  setParentDot('st_subbgpad', d_subbgpad);
  setParentDot('st_subbgdy', d_subbgdy);
  setDot($('st_caption')&&$('st_caption').closest('label'), d_caption);
  setParentDot('st_captionfont', d_captionfont);
  setParentDot('st_captionsize', d_captionsize);
  setParentDot('st_captioncase', d_captioncase);
  setParentDot('st_captionfillcolor', d_captionfill);
  setParentDot('st_captionx', d_captionx);
  setParentDot('st_captiony', d_captiony);
  setDot($('st_captionbg')&&$('st_captionbg').closest('label'), d_captionbg);
  setParentDot('st_captionbgcolor', d_captionbgcolor);
  setParentDot('st_captionbgop', d_captionbgop);
  setParentDot('st_captionbground', d_captionbground);
  setParentDot('st_captionkx', d_captionkx);
  setParentDot('st_captionky', d_captionky);
  setParentDot('st_hlfont', d_hlfont);
  setParentDot('st_introfont', d_introfont);
  setParentDot('st_introhlfont', d_introhlfont);
  setParentDot('st_accentfont', d_accentfont);
  setParentDot('st_accentcase', d_accentcase);
  setParentDot('st_backfont', d_backfont);
  setParentDot('st_backcase', d_backcase);
  setParentDot('st_backstep', d_backstep);
  setParentDot('st_backscale', d_backscale);
  setParentDot('st_backgap', d_backgap);
  setParentDot('st_introanchor', d_introanchor);
  setParentDot('st_introanchor2', d_introanchor2);
  setDot($('st_introrotopos')&&$('st_introrotopos').closest('label'), d_introrotopos);
  setParentDot('st_introfade', d_introfade);
  setParentDot('st_introfxholdadd', d_introfxholdadd);
  setDot($('st_introshade')&&$('st_introshade').closest('label'), d_introshade);
  setParentDot('st_introshadeop', d_introshadeop);
  setParentDot('st_introglow', d_introglow);
  setParentDot('st_hl3color', d_hl3color);
  setParentDot('st_introfillcolor', d_introfill);
  setParentDot('st_introhlfillcolor', d_introhlfill);
  setDot($('st_introshadow')&&$('st_introshadow').closest('label'), d_introshadow);
  setParentDot('st_introshadowop', d_introshadowop);
  setParentDot('st_introshadowdir', d_introshadowdir);
  setParentDot('st_introshadowdist', d_introshadowdist);
  setParentDot('st_introshadowsoft', d_introshadowsoft);
  setParentDot('st_backshadowop', d_backshadowop);
  setParentDot('st_backshadowsoft', d_backshadowsoft);
  setParentDot('st_icshadow1color', d_icshadow1fill);
  setParentDot('st_icshadow1op', d_icshadow1op);
  setParentDot('st_icshadow2color', d_icshadow2fill);
  setParentDot('st_icshadow2op', d_icshadow2op);
  setParentDot('st_introscale', d_introscale);
  setParentDot('st_introy', d_introy);
  setParentDot('st_introy2', d_introy2);
  setParentDot('st_introx', d_introx);
  setParentDot('st_subbgpadmin', d_subbgpadmin);
  setParentDot('st_subbganim', d_subbganim);
  setDot($('st_disc_show')&&$('st_disc_show').closest('label'), d_discshow);
  setDot($('st_disc_end')&&$('st_disc_end').closest('label'), d_discend);
  setParentDot('st_disc_text', d_disctext);
  setParentDot('st_discgap', d_discgap);

  setParentDot('st_cam1zoom', d_cam1zoom);
  setDot($('zoombigwrap'), d_zoombig);
  setDot($('zoomretwrap'), d_zoomret);
  setDot($('st_cam1zoomstart')&&$('st_cam1zoomstart').closest('label'), d_cam1zoomstart);
  setDot($('st_topline')&&$('st_topline').closest('label'), d_topline);
  setParentDot('st_topliney', d_topliney);
  setParentDot('st_toplinew', d_toplinew);
  setParentDot('st_toplineth', d_toplineth);
  setParentDot('st_toplinetrackop', d_toplinetrackop);
  setParentDot('st_toplinetrackfillcolor', d_toplinetrackfill);
  setParentDot('st_toplinefromcolor', d_toplinefrom);
  setParentDot('st_toplinetocolor', d_toplineto);
  setDot($('st_pickzoom'), d_pickzoom);
  setDot($('driftwrap'), d_drift);
  setParentDot('st_cam1fit', d_cam1fit);
  setParentDot('st_startblur', d_startblur);
  setParentDot('st_startblurdur', d_startblurdur);

  setParentDot('st_insstyle', d_insstyle);
  setParentDot('st_insfx', d_insfx);
  setParentDot('st_insanim', d_insanim);
  setDot($('insc1on2wrap'), d_insc1on2);
  setParentDot('st_rotodev', d_rotodev);
  setDot($('st_snapcut')&&$('st_snapcut').closest('label'), d_snapcut);
  setDot($('st_subswap')&&$('st_subswap').closest('label'), d_subswap);
  setParentDot('st_insc2y', d_insc2y);
  setParentDot('st_insc2x', d_insc2x);
  setParentDot('st_insc1y', d_insc1);

  setDot($('st_layer_order_list'), d_layer_order);

  setParentDot('st_musicdb', d_musicdb);
  setParentDot('st_voicedb', d_voicedb);
  setDot($('st_introsfx')&&$('st_introsfx').closest('label'), d_introsfx);
  setDot($('st_audiofades')&&$('st_audiofades').closest('label'), d_audiofades);
  setParentDot('st_poplead', d_poplead);
  setParentDot('st_popdb', d_popdb);
  setParentDot('st_glitchdb', d_glitchdb);
  setParentDot('st_riserfile', d_riserfile);
  setParentDot('st_trans', d_trans);
  setParentDot('st_transsfx', d_transsfx);
  setParentDot('st_pop', d_pop);
  setParentDot('st_glitch', d_glitch);

  // Tab segment dots (green dot on tab if any field inside is modified)
  const hasTextDiff=d_font||d_hlcolor||d_hlbold||d_subcolor||d_subcase||d_suby||d_subscale||d_subwords||d_subrows||d_hlfont||d_introfont||d_introhlfont||d_accentfont||d_accentcase||d_backfont||d_backcase||d_backstep||d_backscale||d_backgap||d_introanchor||d_introanchor2||d_introfade||d_introfxholdadd||d_introshade||d_introshadeop||d_introglow||d_hl3color||d_introfill||d_introhlfill||d_introshadow||d_introshadowop||d_introshadowdir||d_introshadowdist||d_introshadowsoft||d_backshadowop||d_backshadowsoft||d_icshadow1fill||d_icshadow1op||d_icshadow2fill||d_icshadow2op||d_introrotopos||d_introscale||d_introy||d_introy2||d_introx||d_subbgpadmin||d_subbganim||d_discshow||d_discend||d_disctext||d_subbg||d_subbgcolor||d_subbgop||d_subbgh||d_subbground||d_subbgpad||d_subbgdy||d_caption||d_captionfont||d_captionsize||d_captioncase||d_captionfill||d_captionx||d_captiony||d_captionbg||d_captionbgcolor||d_captionbgop||d_captionbground||d_captionkx||d_captionky;
  const hasFrameDiff=d_cam1zoom||d_zoombig||d_zoomret||d_cam1zoomstart||d_topline||d_topliney||d_toplinew||d_toplineth||d_toplinetrackop||d_toplinetrackfill||d_toplinefrom||d_toplineto||d_pickzoom||d_drift||d_cam1fit||d_startblur||d_startblurdur;
  const hasInsertsDiff=d_insstyle||d_insfx||d_insanim||d_insc1on2||d_rotodev||d_snapcut||d_insc2y||d_insc2x||d_insc1;
  const hasLayersDiff=d_layer_order;
  const hasSoundDiff=d_musicdb||d_voicedb||d_introsfx||d_audiofades||d_poplead||d_popdb||d_glitchdb||d_riserfile||d_trans||d_transsfx||d_pop||d_glitch;

  const tabRadioText=document.querySelector('#styleparts input[value="text"]');
  if(tabRadioText&&tabRadioText.parentElement)tabRadioText.parentElement.classList.toggle('st-changed',hasTextDiff);
  const tabRadioFrame=document.querySelector('#styleparts input[value="frame"]');
  if(tabRadioFrame&&tabRadioFrame.parentElement)tabRadioFrame.parentElement.classList.toggle('st-changed',hasFrameDiff);
  const tabRadioInserts=document.querySelector('#styleparts input[value="inserts"]');
  if(tabRadioInserts&&tabRadioInserts.parentElement)tabRadioInserts.parentElement.classList.toggle('st-changed',hasInsertsDiff);
  const tabRadioLayers=document.querySelector('#styleparts input[value="layers"]');
  if(tabRadioLayers&&tabRadioLayers.parentElement)tabRadioLayers.parentElement.classList.toggle('st-changed',hasLayersDiff);
  const tabRadioSound=document.querySelector('#styleparts input[value="sound"]');
  if(tabRadioSound&&tabRadioSound.parentElement)tabRadioSound.parentElement.classList.toggle('st-changed',hasSoundDiff);
  updateStyleSaveUI();
}
function stEdit(){CURSTYLE=CURSTYLE||{};STYLE_TOUCHED=true;const g=id=>val(id);
  CURSTYLE.font=g('st_font')||'SFPro-CondensedSemibold';CURSTYLE.hl_bold=$('st_hlbold').checked;
  CURSTYLE.hl_font=CURSTYLE.hl_bold?(g('st_hlfont')||null):null;CURSTYLE.intro_font=g('st_introfont')||null;CURSTYLE.intro_hl_font=g('st_introhlfont')||null;
  CURSTYLE.accent_font=g('st_accentfont')||null;CURSTYLE.accent_case=g('st_accentcase')||'title';
  CURSTYLE.back_font=g('st_backfont')||null;CURSTYLE.back_case=g('st_backcase')||'lower';
  let bstep=parseFloat(g('st_backstep'));CURSTYLE.back_step=isNaN(bstep)?0.45:bstep;
  let bscale=parseFloat(g('st_backscale'));CURSTYLE.back_scale=isNaN(bscale)?0.69:bscale;
  let bgap=parseFloat(g('st_backgap'));CURSTYLE.back_gap=isNaN(bgap)?4:bgap;
  CURSTYLE.intro_anchor=g('st_introanchor')||'center';
  CURSTYLE.intro_anchor2=g('st_introanchor2')||'center';
  CURSTYLE.intro_roto_by_pos=$('st_introrotopos')&&$('st_introrotopos').checked;
  let fade=parseFloat(g('st_introfade'));CURSTYLE.intro_fade=isNaN(fade)?0.35:fade;
  let fxholdadd=parseFloat(g('st_introfxholdadd'));CURSTYLE.intro_fx_hold_add=isNaN(fxholdadd)?0.3:fxholdadd;
  // затемнение под интро (задание IL): галка и непрозрачность слоя-фигуры
  CURSTYLE.intro_shade=$('st_introshade')&&$('st_introshade').checked;
  let ishop=parseFloat(g('st_introshadeop'));CURSTYLE.intro_shade_op=isNaN(ishop)?100:ishop;
  CURSTYLE.hl_fill=hex2rgb(g('st_hlcolor'));let mdb=parseFloat(g('st_musicdb'));CURSTYLE.music_db=isNaN(mdb)?-20:mdb;
  CURSTYLE.sub_fill=hex2rgb(g('st_subcolor'));CURSTYLE.sub_case=g('st_subcase')||'upper';
  let sy=parseFloat(g('st_suby'));CURSTYLE.sub_y=isNaN(sy)?0.5964:Math.min(0.98,Math.max(0.05,1-sy/100));
  let ssc=parseFloat(g('st_subscale'));CURSTYLE.sub_scale=isNaN(ssc)||ssc<=0?100:ssc;
  let sw=parseInt(g('st_subwords'));CURSTYLE.sub_words_per_row=isNaN(sw)||sw<1?1:sw;
  let sr=parseInt(g('st_subrows'));CURSTYLE.sub_rows_max=isNaN(sr)||sr<1?1:sr;
  CURSTYLE.sub_bg=$('st_subbg').checked;
  CURSTYLE.sub_bg_fill=hex2rgb(g('st_subbgcolor'));
  let sbgop=parseFloat(g('st_subbgop'));CURSTYLE.sub_bg_op=isNaN(sbgop)?72:sbgop;
  let sbgh=parseFloat(g('st_subbgh'));CURSTYLE.sub_bg_h=isNaN(sbgh)?160:sbgh;
  let sbgr=parseFloat(g('st_subbground'));CURSTYLE.sub_bg_round=isNaN(sbgr)?78:sbgr;
  let sbgpad=parseFloat(g('st_subbgpad'));CURSTYLE.sub_bg_pad=isNaN(sbgpad)?18:sbgpad;
  let sbgpadmin=parseFloat(g('st_subbgpadmin'));CURSTYLE.sub_bg_padmin=isNaN(sbgpadmin)?70:sbgpadmin;
  let sbganim=parseFloat(g('st_subbganim'));CURSTYLE.sub_bg_anim=isNaN(sbganim)?0.22:sbganim;
  let sbgdy=parseFloat(g('st_subbgdy'));CURSTYLE.sub_bg_dy=isNaN(sbgdy)?0:sbgdy;
  subBgUI();
  CURSTYLE.caption=$('st_caption')&&$('st_caption').checked;
  CURSTYLE.caption_font=g('st_captionfont')||'SFPro-Bold';
  let csz=parseFloat(g('st_captionsize'));CURSTYLE.caption_size=isNaN(csz)?26:csz;
  CURSTYLE.caption_case=g('st_captioncase')||'upper';
  CURSTYLE.caption_fill=hex2rgb(g('st_captionfillcolor'));
  let cx=parseFloat(g('st_captionx'));CURSTYLE.caption_x=isNaN(cx)?55.5:cx;
  let cy=parseFloat(g('st_captiony'));CURSTYLE.caption_y=isNaN(cy)?228:cy;
  CURSTYLE.caption_bg=$('st_captionbg')&&$('st_captionbg').checked;
  CURSTYLE.caption_bg_fill=hex2rgb(g('st_captionbgcolor'));
  let cbop=parseFloat(g('st_captionbgop'));CURSTYLE.caption_bg_op=isNaN(cbop)?45:cbop;
  let cbr=parseFloat(g('st_captionbground'));CURSTYLE.caption_bg_round=isNaN(cbr)?68:cbr;
  let ckx=parseFloat(g('st_captionkx'));CURSTYLE.caption_kx=isNaN(ckx)?1.718:ckx;
  let cky=parseFloat(g('st_captionky'));CURSTYLE.caption_ky=isNaN(cky)?2.484:cky;
  captionUI();
  if(typeof aewUpdateCaptionUI==='function')aewUpdateCaptionUI();
  let vdb=parseFloat(g('st_voicedb'));CURSTYLE.voice_db=isNaN(vdb)?0:vdb;CURSTYLE.intro_riser=$('st_introsfx').checked;
  CURSTYLE.audio_fades=$('st_audiofades')&&$('st_audiofades').checked;
  CURSTYLE.disclaimer=$('st_disc_show').checked?(g('st_disc_text').trim()||null):'';
  CURSTYLE.disclaimer_end=$('st_disc_end').checked;
  let dgap=parseFloat(g('st_discgap'));CURSTYLE.disc_gap=isNaN(dgap)?null:dgap;
  let sb=parseFloat(g('st_startblur'));CURSTYLE.start_blur=isNaN(sb)||sb<=0?0:sb;
  let sbd=parseFloat(g('st_startblurdur'));CURSTYLE.start_blur_dur=isNaN(sbd)||sbd<=0?0.52:sbd;
  CURSTYLE.transition=g('st_trans')||null;CURSTYLE.transition_sfx=g('st_transsfx')||null;CURSTYLE.pop=g('st_pop')||null;
  CURSTYLE.glitch=g('st_glitch')||null;
  CURSTYLE.intro_riser_file=g('st_riserfile')||null;
  let pld=parseInt(g('st_poplead'));CURSTYLE.pop_lead=isNaN(pld)||pld<=0?4:pld;
  let pdb=parseFloat(g('st_popdb'));CURSTYLE.pop_db=isNaN(pdb)?0:pdb;
  let gldb=parseFloat(g('st_glitchdb'));CURSTYLE.glitch_db=isNaN(gldb)?0:gldb;
  CURSTYLE.roto_device=g('st_rotodev')||null;CURSTYLE.insert_style=g('st_insstyle')||'auto';
  CURSTYLE.insert_fx=g('st_insfx')||'card';
  CURSTYLE.insert_anim=g('st_insanim')||'zoom';
  let ic2x=parseFloat(g('st_insc1on2x'));CURSTYLE.insert_c1on2_x=isNaN(ic2x)?0:pctToPxX(ic2x);
  let ic2y=parseFloat(g('st_insc1on2y'));CURSTYLE.insert_c1on2_y=isNaN(ic2y)?0:pctToPxY(ic2y);
  let ic1x=parseFloat(g('st_insc1x'));CURSTYLE.insert_c1_x=isNaN(ic1x)?0:pctToPxX(ic1x);
  let ic1y=parseFloat(g('st_insc1y'));CURSTYLE.insert_c1_y=isNaN(ic1y)?0:pctToPxY(ic1y);
  insC1On2UI();
  CURSTYLE.insert_snap_cut=$('st_snapcut').checked;
  CURSTYLE.insert_sub_swap=$('st_subswap').checked;
  CURSTYLE.layer_order=Array.isArray(CURSTYLE.layer_order)?[...CURSTYLE.layer_order]:[...DEFAULT_LAYER_ORDER];
  CURSTYLE.cam1_zoom=g('st_cam1zoom')||'pulse';
  let zb=parseFloat(g('st_cam1zoombig'));CURSTYLE.cam1_zoom_big=isNaN(zb)?182:zb;
  let zlo=parseFloat(g('st_cam1zoomlo'));CURSTYLE.cam1_zoom_lo=isNaN(zlo)?112:zlo;
  let zhi=parseFloat(g('st_cam1zoomhi'));CURSTYLE.cam1_zoom_hi=isNaN(zhi)?140:zhi;
  CURSTYLE.cam1_zoom_start=$('st_cam1zoomstart').checked;
  CURSTYLE.top_line=$('st_topline')&&$('st_topline').checked;
  let tly=parseFloat(g('st_topliney'));CURSTYLE.top_line_y=isNaN(tly)?162:tly;
  let tlw=parseFloat(g('st_toplinew'));CURSTYLE.top_line_w=isNaN(tlw)?969:tlw;
  let tlth=parseFloat(g('st_toplineth'));CURSTYLE.top_line_th=isNaN(tlth)?12.5:tlth;
  let tlop=parseFloat(g('st_toplinetrackop'));CURSTYLE.top_line_track_op=isNaN(tlop)?16:tlop;
  CURSTYLE.top_line_track_fill=hex2rgb(g('st_toplinetrackfillcolor'));
  CURSTYLE.top_line_from=hex2rgb(g('st_toplinefromcolor'));
  CURSTYLE.top_line_to=hex2rgb(g('st_toplinetocolor'));
  topLineUI();
  let dlo=parseFloat(g('st_driftlo'));CURSTYLE.cam1_drift_lo=isNaN(dlo)?100:dlo;
  let dhi=parseFloat(g('st_drifthi'));CURSTYLE.cam1_drift_hi=isNaN(dhi)?160:dhi;
  let cfit=parseFloat(g('st_cam1fit'));CURSTYLE.cam1_fit=(isNaN(cfit)||cfit<=0)?100:cfit;
  let isc=parseFloat(g('st_introscale'));CURSTYLE.intro_scale=(isNaN(isc)||isc<=0)?100:isc;
  // ползунки «% кадра» → px в стиле (задание Q): значение живёт в px, ползунок — доля кадра
  const H2=(typeof IPV!=='undefined'&&IPV.plan&&IPV.plan.h)||1920;
  let iy=parseFloat(g('st_introy'));CURSTYLE.intro_y=isNaN(iy)?0:Math.round(iy/100*H2);
  let iy2=parseFloat(g('st_introy2'));CURSTYLE.intro_y2=isNaN(iy2)?0:Math.round(iy2/100*H2);
  let ix=parseFloat(g('st_introx'));CURSTYLE.intro_x=isNaN(ix)?0:pctToPxX(ix);
  let ic2p=parseFloat(g('st_insc2y'));CURSTYLE.insert_c2_y=isNaN(ic2p)?0.172:ic2p/100;
  let ic2xp=parseFloat(g('st_insc2x'));CURSTYLE.insert_c2_x=isNaN(ic2xp)?0.5:ic2xp/100;
  let ig=parseFloat(g('st_introglow'));CURSTYLE.intro_glow=isNaN(ig)?1:ig;
  CURSTYLE.hl_fill3=hex2rgb(g('st_hl3color'));
  CURSTYLE.intro_fill=g('st_introfillhex')?hex2rgb(g('st_introfillhex')):null;
  CURSTYLE.intro_hl_fill=g('st_introhlfillhex')?hex2rgb(g('st_introhlfillhex')):null;
  CURSTYLE.intro_shadow=$('st_introshadow')&&$('st_introshadow').checked;introShadowUI();
  let isop=parseFloat(g('st_introshadowop'));CURSTYLE.intro_shadow_op=isNaN(isop)?116:isop;
  let isdir=parseFloat(g('st_introshadowdir'));CURSTYLE.intro_shadow_dir=isNaN(isdir)?16:isdir;
  let isdist=parseFloat(g('st_introshadowdist'));CURSTYLE.intro_shadow_dist=isNaN(isdist)?6.8:isdist;
  let issoft=parseFloat(g('st_introshadowsoft'));CURSTYLE.intro_shadow_soft=isNaN(issoft)?34:issoft;
  let bsop=parseFloat(g('st_backshadowop'));CURSTYLE.back_shadow_op=isNaN(bsop)?131:bsop;
  let bssoft=parseFloat(g('st_backshadowsoft'));CURSTYLE.back_shadow_soft=isNaN(bssoft)?38:bssoft;
  // Тень ПРЕКОМПА интро (задание B): цвет камеры 1/камеры 2 и непрозрачность 0..255.
  CURSTYLE.intro_comp_shadow_fill=hex2rgb(g('st_icshadow1color'));
  let ics1op=parseFloat(g('st_icshadow1op'));CURSTYLE.intro_comp_shadow_op=isNaN(ics1op)?68:ics1op;
  CURSTYLE.intro_comp_shadow2_fill=hex2rgb(g('st_icshadow2color'));
  let ics2op=parseFloat(g('st_icshadow2op'));CURSTYLE.intro_comp_shadow2_op=isNaN(ics2op)?68:ics2op;
  {const dw=$('driftwrap');if(dw)dw.style.display=(CURSTYLE.cam1_zoom==='drift')?'':'none';}
  {const zsw=$('cam1zoomstartwrap');if(zsw)zsw.style.display=(CURSTYLE.cam1_zoom==='none')?'none':'';}
  {const zbw=$('zoombigwrap');if(zbw)zbw.style.display=(CURSTYLE.cam1_zoom==='jump'||CURSTYLE.cam1_zoom==='none')?'none':'';}
  {const zrw=$('zoomretwrap');if(zrw)zrw.style.display=(CURSTYLE.cam1_zoom==='drift'||CURSTYLE.cam1_zoom==='none')?'none':'';}
  CURSTYLE.roto=$('roto').checked;CURSTYLE.roto_bottom=(parseFloat(val('rotobottom'))||0)/100;CURSTYLE.roto_cam1_only=$('roto_cam1only').checked;CURSTYLE.intro_mode=val('intromode');CURSTYLE.label=CURSTYLE.label||'кастом';styleSubPos();syncDbSliders();applyDbGains();
  if(typeof syncSubTabUI==='function')syncSubTabUI();
  applyStyleHlColor();
  updateStyleDiffDots();
  captureAE();
  // «% снизу» и строка в кадре — один источник (задание E): правка поля догоняет предпросмотр
  // через план (posy считает scene_plan из style.sub_y). ipvPlanFetch сам гасится вне AE-режима.
  ipvPlanSoon();updateStyleSaveUI();}
// Ползунки «Музыка, dB» / «Голос, dB» у плеера вставок — ЭТО ЖЕ поля стиля, что
// st_musicdb/st_voicedb: значение живёт в CURSTYLE, ползунок — ещё один способ его
// поменять (и наоборот), отдельной переменной у него нет. applyDbGains — чтобы
// сменивший уровень звучал сразу, без переоткрытия предпросмотра.
function syncDbSliders(){const s=CURSTYLE||{};
  const m=$('ipvmusicdb');if(m)m.value=Math.round((s.music_db!=null?s.music_db:-20)*2)/2;
  const v=$('ipvvoicedb');if(v)v.value=Math.round((s.voice_db!=null?s.voice_db:0)*2)/2;
  if(typeof syncSldnums==='function')syncSldnums();
  updateStyleDiffDots();}
function setStyleDb(which,v){if(!CURSTYLE)CURSTYLE=JSON.parse(JSON.stringify(STYLES.base||{}));
  const db=Math.max(-40,Math.min(6,Math.round((parseFloat(v)||0)*2)/2));
  if(which==='music'){CURSTYLE.music_db=db;$('st_musicdb').value=db;}
  else{CURSTYLE.voice_db=db;$('st_voicedb').value=db;}
  syncDbSliders();applyDbGains();
  updateStyleDiffDots();
  captureAE();}
function discUI(){const on=$('st_disc_show').checked;const w=$('st_disc_wrap');if(w)w.style.display=on?'':'none';}
// Точка наезда Камеры 1 прицелом (задание Q, часть 4): кнопка ставит курсор в crosshair
// над кадром предпросмотра, клик кладёт точку в cam1_zoom_cx/cy (доли кадра), на кадре
// остаётся маркер-перекрестие. Повторное нажатие кнопки и Esc — отмена. Маркер виден
// только когда точку ПРАВЯТ: в режиме прицела или на наведении/фокусе на кнопке. Раньше
// висел всегда — «зачем он на кадре» (жалоба 2026-08-13); потерять поставленную точку
// нельзя и так: она держит зум, и клик по кнопке снова её показывает.
let ZOOM_PICK=false;
let ZOOM_HOVER=false;
document.addEventListener('DOMContentLoaded',()=>{
  const b=$('st_pickzoom');if(!b)return;
  ['mouseenter','focusin'].forEach(ev=>b.addEventListener(ev,()=>{ZOOM_HOVER=true;zoomPickMark();}));
  ['mouseleave','focusout'].forEach(ev=>b.addEventListener(ev,()=>{ZOOM_HOVER=false;zoomPickMark();}));
});
function pickZoomPoint(){const st=$('ipvstage');
  if(ZOOM_PICK){zoomPickOff();return;}
  if(!st||!st.clientWidth){toast(t('Открой предпросмотр (шаг 3)'));return;}
  ZOOM_PICK=true;st.classList.add('zoompick');
  const b=$('st_pickzoom');if(b)b.textContent=t('Отменить точку');
  zoomPickMark();}
function zoomPickOff(){ZOOM_PICK=false;
  const st=$('ipvstage');if(st)st.classList.remove('zoompick');
  const b=$('st_pickzoom');if(b)b.textContent=t('Точка наезда…');
  zoomPickMark();}
function zoomPickMark(){const st=$('ipvstage');if(!st)return;
  let m=$('zoommark');
  if(!m){m=document.createElement('div');m.id='zoommark';m.className='zoommark';st.appendChild(m);}
  if(!ZOOM_PICK&&!ZOOM_HOVER){m.style.display='none';return;}   // вне правки точки маркер кадр не засоряет
  const cx=(CURSTYLE&&CURSTYLE.cam1_zoom_cx!=null)?CURSTYLE.cam1_zoom_cx:0.5;
  const cy=(CURSTYLE&&CURSTYLE.cam1_zoom_cy!=null)?CURSTYLE.cam1_zoom_cy:0.5;
  m.style.display='';m.style.left=(cx*100)+'%';m.style.top=(cy*100)+'%';}
function zoomPickClick(e){const st=$('ipvstage');if(!ZOOM_PICK||!st)return;
  const r=st.getBoundingClientRect();
  const cx=Math.max(0.02,Math.min(0.98,(e.clientX-r.left)/r.width));
  const cy=Math.max(0.02,Math.min(0.98,(e.clientY-r.top)/r.height));
  if(!CURSTYLE)CURSTYLE=JSON.parse(JSON.stringify(STYLES.base||{}));
  CURSTYLE.cam1_zoom_cx=cx;CURSTYLE.cam1_zoom_cy=cy;
  zoomPickMark();
  updateStyleDiffDots();
  captureAE();ipvPlanSoon();
  zoomPickOff();}
document.addEventListener('pointerdown',e=>{if(ZOOM_PICK&&e.target&&e.target.closest('#ipvstage'))zoomPickClick(e);},true);
document.addEventListener('keydown',e=>{if(ZOOM_PICK&&e.key==='Escape')zoomPickOff();});
// сдвиг «вылета на перебивке» имеет смысл только при принудительном стиле «Кам 1»:
// на «авто» вставка над перебивкой и так собирается в стиле Кам 2, вылета из-за спины там нет
function insC1On2UI(){const w=$('insc1on2wrap');if(w)w.style.display=(val('st_insstyle')==='cam1')?'':'none';}
async function pickInto(id){try{const d=await (await fetch('/api/pickone')).json();if(d.path){$(id).value=d.path;stEdit();
  if(SFX_PREFIX[id])openSfxEdit(id);}}   // заменил звук — сразу настрой (задание AA)
  catch(e){toast(t('Не открылся выбор файла — сервер не ответил'));uiLog(t('pickone: ')+e);}}
// Сохранение шаблона: две кнопки — «Сохранить» (перезаписать выбранный шаблон без карандаша,
// имя уже известно) и «Сохранить как…» (завести новый). Для встроенных base/geologica
// «Сохранить» требует сохранить под своим именем (задание CU).
async function saveStyle(){
  stEdit();
  let target=STYLE_EDITING;
  if(!target){
    const k=val('style');
    if(k&&k!=='__custom__'&&k!=='__edit__'&&!BUILTIN_STYLES[k]&&STYLES[k]){
      target=k;
    }
  }
  if(!target){
    const k=val('style');
    if(k&&BUILTIN_STYLES[k]){
      toast(t('«{n}» — встроенный шаблон: сохрани правки под своим именем',{n:t((STYLES[k]||{}).label||k)}));
      if($('st_name'))$('st_name').focus();
      return;
    }
    saveStyleAs();
    return;
  }
  if(BUILTIN_STYLES[target]){
    toast(t('«{n}» — встроенный шаблон: сохрани правки под своим именем',{n:t((STYLES[target]||{}).label||target)}));
    if($('st_name'))$('st_name').focus();
    return;
  }
  STYLE_EDITING=null;STYLE_EDIT_ORIG=null;STYLE_TOUCHED=false;
  await saveStyleTo(target);
  if($('st_name'))$('st_name').value='';
  if($('stylehint'))$('stylehint').textContent='';}
async function saveStyleAs(){
  stEdit();
  const name=val('st_name').trim();if(!name){toast(t('Дай имя шаблону'));$('st_name').focus();return;}
  STYLE_EDITING=null;STYLE_EDIT_ORIG=null;STYLE_TOUCHED=false;
  await saveStyleTo(name);
  if($('st_name'))$('st_name').value='';
  if($('stylehint'))$('stylehint').textContent='';}
async function saveStyleTo(name){
  CURSTYLE.label=name;
  const d=await (await fetch('/api/savestyle',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,data:CURSTYLE})})).json();
  const el=$('st_saved');
  if(d.error){el.className='err';el.textContent='⚠ '+errText(d);return;}
  STYLE_EDIT_ORIG=JSON.parse(JSON.stringify(CURSTYLE));
  STYLE_TOUCHED=false;
  STYLE_EDITING=null;
  updateStyleDiffDots();
  el.className='ok';el.textContent=t('сохранён');STYLESAVED=d.key;await loadStyles();}

// ================= misc pickers / music =================
async function pickdir(id){try{const d=await (await fetch('/api/pickdir')).json();if(d.path){$(id).value=d.path;
  if(id==='aeoutdir'||id==='aeoutdir3')aeDirCommit($(id));      // выбранное — закрепить (у тега: в профиль спикера)
  else if(id==='aerenderdir')renderDirCommit($(id));
  else saveState();}}
  catch(e){toast(t('Не открылся выбор папки — сервер не ответил'));uiLog(t('pickdir: ')+e);}}
function musicUI(){const m=val('musicmode');const ib=$('musicinbox');if(ib)ib.style.display=(m==='random')?'none':'';
  const pk=$('musicpick');if(pk)pk.style.display=(m==='file')?'':'none';const lb=$('musicinlbl');if(lb)lb.textContent=(m==='file')?t('Путь к файлу'):t('Ссылка YouTube');
  const inp=$('aemusic');if(inp)inp.placeholder=(m==='file')?'…\\music\\track.m4a':'https://youtube.com/...';}
async function pickMusic(){try{const d=await (await fetch('/api/pickaudio')).json();if(d.path){$('aemusic').value=d.path;captureAE();}}
  catch(e){toast(t('Не открылся выбор файла — сервер не ответил'));uiLog(t('pickaudio: ')+e);}}

// ================= ASR-движки (кто слушает звук) =================
// Список приходит с сервера (/api/asr_engines = asr_backends.engines()): встроенные
// Whisper/GigaAM + CTC-модели других языков из data/asr_engines.json. Один
// источник истины: добавил язык в JSON → он тут же в селекторе (F5, без рестарта).
let SUBWANT=null;     // выбранный «Движок субтитров» (шаг 2) из сохранённого состояния
                      // (селекты наполняются асинхронно, до этого хранить негде)
const LANGNAME={multi:t('любой язык'),ru:t('русский'),en:t('английский'),de:t('немецкий'),
                es:t('испанский'),fr:t('французский'),it:t('итальянский'),pl:t('польский'),
                pt:t('португальский'),tr:t('турецкий'),zh:t('китайский'),ar:t('арабский')};
function asrMigrate(v){  // старое состояние: голый размер Whisper / просто «whisper»
  if(!v||v==='whisper')return 'whisper:large-v3';
  return (['large-v3','medium','small'].indexOf(v)>=0)?('whisper:'+v):v;}
let ENGLBL={};        // id движка -> человеческое имя (для логов и прогресса)
function engLabel(id){return ENGLBL[id]||ENGLBL[asrMigrate(id)]||id;}
function fillEngineSel(sel,list,want,fallback){
  if(!sel)return;
  if(!list.length)list=[{id:fallback,label:fallback,lang:'multi'}];
  sel.innerHTML='';
  [...new Set(list.map(e=>e.lang))].forEach(l=>{     // группируем по языку
    const g=document.createElement('optgroup');g.label=LANGNAME[l]||l;
    list.filter(e=>e.lang===l).forEach(e=>{
      const o=document.createElement('option');o.value=e.id;o.textContent=t(e.label);g.appendChild(o);});
    sel.appendChild(g);});
  sel.value=want||fallback;
  if(!sel.value)sel.value=list[0].id;                // сохранённый движок исчез из JSON
  // селекты движков живут в ⚙, а выбранное показывает сводка на странице — обновляем её
  sel.onchange=()=>{saveState();cutSummary();markupSummary();};
}
let ASR_ENGINES=[];
async function loadASREngines(){
  let list=[];
  try{const d=await (await fetch('/api/asr_engines')).json();list=d.engines||[];}catch(e){}
  ASR_ENGINES=list;
  ENGLBL={};list.forEach(e=>{ENGLBL[e.id]=e.label;});
  fillEngineSel($('subengine'),list.filter(e=>e&&e.subs),SUBWANT,'whisper:large-v3');
  if(typeof fillCutAsr==='function')fillCutAsr();
  cutSummary();markupSummary();
}


// ---- редактор звука: волна, обрезка, точка удара (задание AA) ----
// Открывается карандашом у звука в панели стиля и после выбора файла кнопкой «Файл…».
// Три маркера тянутся мышью по волне (звук) или по кадру (видеопереход):
//   in  — обрезать слева (сек от начала файла),
//   out — обрезать справа (не задан = до конца файла; у «попа» тогда прежняя обрезка 0.1с),
//   at  — точка удара, которая должна попасть на событие (жёлтое слово / кат / старт).
// Дефолты in=0/out=пусто/at=0/db=0 = сегодняшнее поведение: .jsx не меняется (golden).
// Волна — с /api/waveform (кэш рядом с файлом, один pps на файл; зум отрисовкой).
let SFX=null,SFXAUD=null;
const SFX_PREFIX={st_pop:"pop",st_transsfx:"transition_sfx",st_riserfile:"intro_riser",st_trans:"transition",st_glitch:"glitch"};
const SFX_ISVIDEO={st_trans:true};
function openSfxEdit(field){
  const path=val(field).trim();if(!path){toast(t("Сначала выбери файл звука"));return;}
  const prefix=SFX_PREFIX[field]||field.replace(/^st_/,"");
  const s=CURSTYLE||{};
  const num=v=>{const f=parseFloat(v);return isNaN(f)?0:f;};
  SFX={field,prefix,path,kind:SFX_ISVIDEO[field]?"video":"audio",
    in:num(s[prefix+"_in"]),
    out:(s[prefix+"_out"]!=null&&s[prefix+"_out"]!=="")?num(s[prefix+"_out"]):null,
    at:num(s[prefix+"_at"]),db:num(s[prefix+"_db"]),peaks:[],pps:0,dur:0,active:null};
  $("sfxTitle").textContent=t("Настройка звука: ")+path.split(/[\\/]/).pop();
  $("sfx_db").value=SFX.db;
  $("sfxinfo").textContent="";
  openModal("mbSfx");
  sfxLoad();
}
function sfxPath(){return "/api/media?path="+encodeURIComponent(SFX.path);}
async function sfxLoad(){
  const v=$("sfxvid"),w=$("sfxwave");
  const isV=SFX.kind==="video";
  v.style.display=isV?"":"none";w.style.display=isV?"none":"block";
  try{
    let dur=0;
    if(isV){
      v.src=sfxPath();
      await new Promise(r=>{const d=()=>{v.removeEventListener("loadedmetadata",d);r();};
        v.addEventListener("loadedmetadata",d);v.addEventListener("error",d);});
      dur=v.duration||0;
    }else{
      const a=document.createElement("audio");
      a.preload="metadata";a.src=sfxPath();
      await new Promise(r=>{const d=()=>{a.removeEventListener("loadedmetadata",d);r();};
        a.addEventListener("loadedmetadata",d);a.addEventListener("error",d);});
      dur=a.duration||0;
    }
    SFX.dur=dur||0;
    if(!isV){
      SFX.pps=Math.max(80,Math.ceil(1500/Math.max(0.3,dur||1)));   // ~1500 точек на файл
      const d=await (await fetch("/api/waveform?pps="+SFX.pps+"&path="+encodeURIComponent(SFX.path))).json();
      SFX.pps=d.pps||SFX.pps;
      SFX.peaks=d.peaks||[];if(SFX.dur<=0&&d.dur)SFX.dur=d.dur;
    }
    sfxDraw();sfxMarkInfo();
  }catch(e){$("sfxinfo").textContent="⚠ "+e;}
}
function sfxS2X(s){const c=$("sfxwave");return (c.clientWidth||1)*s/Math.max(0.001,SFX.dur);}
function sfxX2S(x){return x/($("sfxwave").clientWidth||1)*SFX.dur;}
function sfxDraw(){
  const c=$("sfxwave");if(!c)return;const dpr=devicePixelRatio||1;
  c.width=Math.max(2,c.clientWidth*dpr);c.height=Math.max(2,c.clientHeight*dpr);
  const g=c.getContext("2d"),W=c.width,H=c.height,mid=H/2,amp=H*0.42;
  g.clearRect(0,0,W,H);g.fillStyle="#0d0d0d";g.fillRect(0,0,W,H);
  g.strokeStyle="#4a4a4a";g.lineWidth=1;g.beginPath();
  for(let x=0;x<W;x++){const s=sfxX2S(x/dpr);const p=SFX.peaks[Math.floor(s*SFX.pps)]||0;
    const h=Math.max(dpr,p*amp*2);g.moveTo(x+.5,mid-h/2);g.lineTo(x+.5,mid+h/2);}
  g.stroke();
  if(SFX.in>0||SFX.out!=null){g.fillStyle="rgba(0,0,0,.5)";
    if(SFX.in>0)g.fillRect(0,0,Math.max(0,sfxS2X(SFX.in)*dpr),H);
    if(SFX.out!=null)g.fillRect(Math.min(W,sfxS2X(SFX.out)*dpr),0,W,H);}
  const mk=(s,col,label)=>{if(s==null||s<0)return;const x=sfxS2X(s)*dpr;
    g.strokeStyle=col;g.lineWidth=Math.max(2,dpr*2);g.beginPath();g.moveTo(x+.5,0);g.lineTo(x+.5,H);g.stroke();
    g.fillStyle=col;g.font=Math.max(10,11*dpr)+"px sans-serif";g.fillText(label,x+3,12*dpr);};
  mk(SFX.in,"#6fce9e","в");
  if(SFX.out!=null)mk(SFX.out,"#e08a3c","к");
  mk(SFX.at,"#f5c518","!");
}
function sfxMarkInfo(){
  const o=SFX.in.toFixed(2)+t('с')+' → '+(SFX.out!=null?SFX.out.toFixed(2)+t('с'):t('конец'))
    +' · '+t('удар')+' '+SFX.at.toFixed(2)+t('с');
  $("sfxinfo").textContent=o;}
function sfxDragStart(x){
  const dpr=devicePixelRatio||1;
  const dist=(a,b)=>Math.abs(sfxS2X(a||0)*dpr-b);
  const cx=x*dpr;
  let best="in",bd=1e9;
  if(dist(SFX.in,cx)<bd){bd=dist(SFX.in,cx);best="in";}
  if(SFX.out!=null&&dist(SFX.out,cx)<bd){bd=dist(SFX.out,cx);best="out";}
  if(dist(SFX.at,cx)<bd){best="at";}
  SFX.active=best;}
function sfxDragMove(x){
  if(!SFX.active)return;
  let s=Math.max(0,Math.min(SFX.dur,sfxX2S(x)));
  if(SFX.active==="in"){if(SFX.out==null||s<SFX.out)SFX.in=s;}
  else if(SFX.active==="out"){if(s>SFX.in)SFX.out=s;else SFX.out=null;}
  else SFX.at=s;
  sfxDraw();sfxMarkInfo();}
function sfxDragEnd(){if(!SFX.active)return;SFX.active=null;sfxSave();}
function sfxSave(){
  const s=CURSTYLE||(CURSTYLE={});const p=SFX.prefix;
  if(SFX.in>1e-9)s[p+"_in"]=+SFX.in.toFixed(3);else delete s[p+"_in"];
  if(SFX.out!=null)s[p+"_out"]=+SFX.out.toFixed(3);else delete s[p+"_out"];
  if(SFX.at>1e-9)s[p+"_at"]=+SFX.at.toFixed(3);else delete s[p+"_at"];
  if(SFX.db)s[p+"_db"]=+SFX.db.toFixed(1);else delete s[p+"_db"];
  if(p==="pop"&&$('st_popdb')){$('st_popdb').value=(s.pop_db!=null?s.pop_db:0);if(typeof syncSldnums==='function')syncSldnums();}
  updateStyleDiffDots();
  captureAE();ipvPlanSoon();}
function sfxSaveDb(v){SFX.db=parseFloat(v)||0;sfxSave();}
function sfxReset(){
  SFX.in=0;SFX.out=null;SFX.at=0;SFX.db=0;$("sfx_db").value=0;
  sfxDraw();sfxMarkInfo();sfxSave();}
function sfxToggle(){
  if(SFX.kind==="video"){const v=$("sfxvid");
    if(v.paused){v.currentTime=Math.max(0,SFX.at||SFX.in||0);v.play().catch(()=>{});}
    else v.pause();return;}
  if(!SFXAUD)SFXAUD=new Audio();SFXAUD.src=sfxPath();
  if(SFXAUD.paused){SFXAUD.currentTime=SFX.in||0;
    SFXAUD.ontimeupdate=()=>{if(SFX.out!=null&&SFXAUD.currentTime>=SFX.out)SFXAUD.pause();};
    SFXAUD.play().catch(()=>{});}
  else SFXAUD.pause();}
document.addEventListener("DOMContentLoaded",()=>{
  const st=$("sfxstage");if(!st)return;
  st.addEventListener("pointerdown",e=>{
    const r=st.getBoundingClientRect();const x=e.clientX-r.left;
    const isV=SFX&&SFX.kind==="video";
    if(!isV){sfxDragStart(x);st.setPointerCapture(e.pointerId);}
    else if(SFX){const v=$("sfxvid");v.currentTime=Math.max(0,Math.min(SFX.dur||0,(x)/r.width*(SFX.dur||1)));}
    e.preventDefault();});
  st.addEventListener("pointermove",e=>{if(SFX&&SFX.active)sfxDragMove(e.clientX-st.getBoundingClientRect().left);});
  st.addEventListener("pointerup",()=>sfxDragEnd());
  st.addEventListener("pointercancel",()=>sfxDragEnd());});
