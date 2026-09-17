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
    await loadStyleSchema();
    if(STSCHEMA)renderStylePanel();
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
  STYLE_EDITING=key;STYLE_EDIT_ORIG=JSON.parse(JSON.stringify(src));STYLE_TOUCHED=false;
  CURSTYLE=JSON.parse(JSON.stringify(src));
  ensureEditOption(key,src.label||key);
  $('style').value='__edit__';
  // Рото — теперь поле СТИЛЯ (задание EX2c), а не настройка клипа: шаблон его и приносит,
  // fillStyleFields раскладывает по панели вместе с остальными. Прежняя возня с
  // #roto/#rotobottom (запомнить у клипа и вернуть) канула вместе со старой разметкой.
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
    const descHtml = def.desc ? ` <span class="i layer-order-desc" data-t="${esc(t(def.desc))}">!</span>` : '';
    titleWrap.innerHTML = `<span>${esc(t(def.title))}</span>${descHtml}`;
    row.appendChild(titleWrap);

    const btns = document.createElement('div');
    btns.className = 'layer-order-btns';

    const upBtn = document.createElement('button');
    upBtn.type = 'button';
    upBtn.className = 'layer-order-btn icon';
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
    dnBtn.className = 'layer-order-btn icon';
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
// Рото — три обычных поля схемы (roto/roto_bottom/roto_cam1_only), поэтому значения
// берутся из панели общей дверью stReadView, а не из отдельных id старой разметки.
function rotoSync(){
  if(!CURSTYLE)CURSTYLE=JSON.parse(JSON.stringify((typeof STSCHEMA!=='undefined'&&STSCHEMA&&STSCHEMA.base)||STYLES.base||{}));
  if(typeof stReadView==='function'){
    const r=stReadView('roto');if(r!=null)CURSTYLE.roto=!!r;
    const b=stReadView('roto_bottom');if(b!=null)CURSTYLE.roto_bottom=b;
    const c=stReadView('roto_cam1_only');if(c!=null)CURSTYLE.roto_cam1_only=!!c;
  }
  captureAE();
}
// ---- живые подсказки стиля на превью ----
// «Низ маски %» (ротоскоп): пока поле крутится, на кадре предпросмотра снизу —
// полупрозрачная красная маска ровно на столько процентов высоты; перестал
// крутить или ушёл с поля — маска ушла. Иначе «35%» не говорит, где это по кадру.
let ROTOMASK_T=0;
function rotoMaskSync(pct){const v=(pct!==undefined)?pct:(typeof stReadView==='function'?stReadView('roto_bottom'):NaN);rotoMask(isNaN(v)||v==null?0:v);
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
function reflectStyle(){
  if(!CURSTYLE)return;
  // Поля рото панель выставляет сама (они в схеме и уже разложены fillStyleFields);
  // здесь остаётся только то, чего в схеме нет: режим интро (EXTERNAL) и превью.
  if(CURSTYLE.intro_mode){const el=$('intromode');if(el)el.value=CURSTYLE.intro_mode;}
  styleSubPos();
  applyStyleHlColor();
  if(typeof aewUpdateCaptionUI==='function')aewUpdateCaptionUI();
}
function styleFrameDim(){
  const pl=(typeof IPV!=='undefined'&&IPV.plan)||{};
  return {w:pl.w||1080,h:pl.h||1920};
}
function pxToPctX(px){return +(((px||0)/styleFrameDim().w)*100).toFixed(1);}
function pctToPxX(pct){return Math.round(((parseFloat(pct)||0)/100)*styleFrameDim().w);}
function pxToPctY(py){return +(((py||0)/styleFrameDim().h)*100).toFixed(1);}
function pctToPxY(pct){return Math.round(((parseFloat(pct)||0)/100)*styleFrameDim().h);}

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
  if(which==='music'){CURSTYLE.music_db=db;}
  else{CURSTYLE.voice_db=db;}
  if(typeof stRefresh==='function')stRefresh(which==='music'?'music_db':'voice_db');
  syncDbSliders();applyDbGains();
  captureAE();}
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
  if(typeof stRefresh==='function')stRefresh('cam1_zoom_cx');
  else updateStyleDiffDots();
  captureAE();ipvPlanSoon();
  zoomPickOff();}
document.addEventListener('pointerdown',e=>{if(ZOOM_PICK&&e.target&&e.target.closest('#ipvstage'))zoomPickClick(e);},true);
document.addEventListener('keydown',e=>{if(ZOOM_PICK&&e.key==='Escape')zoomPickOff();});
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
// SFX_PREFIX/SFX_ISVIDEO объявлены в 94-stylepanel.js и строятся ТАМ из схемы
// (initSfxMaps): два `let` с одним именем в общем скоупе файлов — это SyntaxError
// «Identifier 'SFX_PREFIX' has already been declared», и весь этот файл не исполнялся.
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
  if(p==="pop"&&typeof stRefresh==='function')stRefresh('pop_db');
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
